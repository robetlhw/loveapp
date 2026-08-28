import re
import unicodedata
from dataclasses import dataclass

from loveapp.application.date_planning.requirements import (
    DateRequirementBinding,
    primary_desired_stops,
    resolve_requirement_bindings_for_plan_item,
)
from loveapp.domain.date_operations import (
    DateOperationType,
    DatePlanOperation,
    DateRequirementMatch,
    DateStopConstraints,
    DateStopRequirement,
    DesiredDateStop,
    MealType,
    StopKind,
    StopReference,
    TemporalAnchor,
    TimeWindow,
    desired_stops_from_legacy_slots,
)
from loveapp.domain.date_plan import DatePlan, DatePlanItem
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.enums import PlaceCategory


@dataclass(frozen=True)
class LegacyDateRequirementSlots:
    dining_keywords: list[str]
    meal_keywords: dict[str, list[str]]
    activity_keywords: list[str]
    schedule_hints: list[str]


@dataclass(frozen=True)
class DateRequirementProjectionResult:
    requirements: list[DateStopRequirement]
    rejected_batch_requirements: list[DateStopRequirement]


_DATE_OPERATION_ORDER = {
    DateOperationType.UPDATE_CONSTRAINT: 0,
    DateOperationType.UPDATE_REQUIREMENT: 0,
    DateOperationType.REMOVE_STOP: 1,
    DateOperationType.REPLACE_STOP: 2,
    DateOperationType.ADD_STOP: 3,
    DateOperationType.MOVE_STOP: 4,
    DateOperationType.REPLAN: 5,
}


def order_date_plan_operations(
    operations: list[DatePlanOperation] | tuple[DatePlanOperation, ...],
) -> list[DatePlanOperation]:
    return [
        operation
        for _, operation in sorted(
            enumerate(operations),
            key=lambda item: (
                _DATE_OPERATION_ORDER[item[1].type],
                0
                if item[1].type == DateOperationType.ADD_STOP
                and item[1].alternative_group is not None
                else 1,
                item[0],
            ),
        )
    ]


class DateRequirementProjector:
    """Project verified operations onto canonical date-stop requirements."""

    def apply_operations(
        self,
        desired_stops: list[DesiredDateStop],
        operations: list[DatePlanOperation] | tuple[DatePlanOperation, ...],
        *,
        current_plan: DatePlan | None = None,
    ) -> list[DesiredDateStop]:
        projected = list(desired_stops)
        for operation in order_date_plan_operations(operations):
            if operation.type == DateOperationType.ADD_STOP and operation.payload is not None:
                projected = _add_or_merge(projected, operation.payload)
            elif operation.type == DateOperationType.REMOVE_STOP and operation.target is not None:
                index = _target_index(projected, operation.target, current_plan)
                if index is not None:
                    projected.pop(index)
            elif operation.type == DateOperationType.REPLACE_STOP and operation.payload is not None:
                index = _target_index(projected, operation.target, current_plan)
                target_item = _target_item(operation.target, current_plan)
                inherited = (
                    inherit_desired_stop_role(operation.payload, projected[index])
                    if index is not None
                    else inherit_desired_stop_role(operation.payload, target_item)
                    if target_item is not None
                    else operation.payload
                )
                if (
                    inherited.generic_replacement
                    and inherited.keyword is None
                    and inherited.place_name is None
                    and inherited.meal_type is None
                ):
                    if index is not None:
                        projected.pop(index)
                    continue
                if index is None:
                    projected = _add_or_merge(projected, inherited)
                else:
                    projected[index] = inherited
            elif operation.type == DateOperationType.MOVE_STOP and operation.payload is not None:
                index = _target_index(projected, operation.target, current_plan)
                if index is not None:
                    projected[index] = _apply_placement(projected[index], operation.payload)
                else:
                    target_item = _target_item(operation.target, current_plan)
                    if target_item is not None:
                        projected = _add_or_merge(
                            projected,
                            _apply_placement(
                                _desired_stop_for_item(target_item),
                                operation.payload,
                            ),
                        )
        return _dedupe_stops(projected)

    def apply_requirement_operations(
        self,
        requirements: list[DateStopRequirement],
        operations: list[DatePlanOperation] | tuple[DatePlanOperation, ...],
        *,
        current_plan: DatePlan | None = None,
        source_text: str | None = None,
        requirement_matches: list[DateRequirementMatch] | None = None,
    ) -> list[DateStopRequirement]:
        return self.project_requirement_operations(
            requirements,
            operations,
            current_plan=current_plan,
            source_text=source_text,
            requirement_matches=requirement_matches,
        ).requirements

    def project_requirement_operations(
        self,
        requirements: list[DateStopRequirement],
        operations: list[DatePlanOperation] | tuple[DatePlanOperation, ...],
        *,
        current_plan: DatePlan | None = None,
        source_text: str | None = None,
        requirement_matches: list[DateRequirementMatch] | None = None,
    ) -> DateRequirementProjectionResult:
        """Project the batch and its addition-only failure fallback together."""

        projected = list(requirements)
        rejected_batch_requirements = list(requirements)
        existing_requirement_ids = {requirement.id for requirement in requirements}
        ordered = order_date_plan_operations(operations)
        grouped_adds = _alternative_add_groups(ordered, source_text)
        grouped_by_operation = {
            id(operation): (index, group)
            for index, group in enumerate(grouped_adds)
            for operation in group
        }
        applied_groups: set[int] = set()

        for operation in ordered:
            grouped = grouped_by_operation.get(id(operation))
            if grouped is not None:
                group_index, group = grouped
                if group_index not in applied_groups:
                    alternatives = [
                        member.payload
                        for member in group
                        if member.payload is not None
                    ]
                    incoming = DateStopRequirement(
                        alternatives=alternatives,
                        min_satisfied=1,
                        max_satisfied=1,
                        source_span=_shared_source_span(group, source_text),
                    )
                    projected = _add_or_merge_requirement(
                        projected,
                        incoming,
                    )
                    rejected_batch_requirements = _add_or_merge_requirement(
                        rejected_batch_requirements,
                        incoming,
                    )
                    applied_groups.add(group_index)
                continue
            if operation.type == DateOperationType.UPDATE_REQUIREMENT:
                projected = _apply_requirement_update(projected, operation)
                continue
            if operation.type == DateOperationType.ADD_STOP and operation.payload is not None:
                incoming = _single_requirement(operation.payload, operation.source_span)
                projected = _add_or_merge_requirement(
                    projected,
                    incoming,
                )
                rejected_batch_requirements = _add_or_merge_requirement(
                    rejected_batch_requirements,
                    incoming,
                )
            elif operation.type == DateOperationType.REMOVE_STOP and operation.target is not None:
                bindings = _target_requirement_bindings(
                    projected,
                    operation.target,
                    current_plan,
                    requirement_matches or [],
                )
                if len(bindings) == 1:
                    projected = _remove_bound_requirements(projected, bindings)
            elif operation.type == DateOperationType.REPLACE_STOP and operation.payload is not None:
                bindings = _target_requirement_bindings(
                    projected,
                    operation.target,
                    current_plan,
                    requirement_matches or [],
                )
                if len(bindings) > 1:
                    continue
                binding = bindings[0] if bindings else None
                index = (
                    next(
                        (
                            offset
                            for offset, requirement in enumerate(projected)
                            if requirement.id == binding.requirement_id
                        ),
                        None,
                    )
                    if binding is not None
                    else None
                )
                target_item = _target_item(operation.target, current_plan)
                previous = (
                    projected[index].alternatives[binding.alternative_index]
                    if index is not None and binding is not None
                    else target_item
                )
                inherited = (
                    inherit_desired_stop_role(operation.payload, previous)
                    if previous is not None
                    else operation.payload
                )
                if (
                    inherited.generic_replacement
                    and inherited.keyword is None
                    and inherited.place_name is None
                    and inherited.meal_type is None
                ):
                    if binding is not None:
                        projected = _remove_bound_requirements(projected, [binding])
                    continue
                if index is None or binding is None:
                    projected = _add_or_merge_requirement(
                        projected,
                        _single_requirement(inherited, operation.source_span),
                    )
                else:
                    current = projected[index]
                    alternatives = list(current.alternatives)
                    alternatives[binding.alternative_index] = inherited
                    projected[index] = current.model_copy(
                        update={
                            "alternatives": alternatives,
                            "source_span": operation.source_span or current.source_span,
                        }
                    )
            elif operation.type == DateOperationType.MOVE_STOP and operation.payload is not None:
                projected = _apply_requirement_move(
                    projected,
                    operation,
                    current_plan,
                    requirement_matches or [],
                )
                rejected_batch_requirements = _apply_requirement_move(
                    rejected_batch_requirements,
                    operation,
                    None,
                    [],
                    allowed_requirement_ids={
                        requirement.id
                        for requirement in rejected_batch_requirements
                        if requirement.id not in existing_requirement_ids
                    },
                )
        return DateRequirementProjectionResult(
            requirements=_dedupe_requirements(projected),
            rejected_batch_requirements=_dedupe_requirements(
                rejected_batch_requirements
            ),
        )


def desired_stops_for_state(state: DatePlanningTaskState) -> list[DesiredDateStop]:
    return primary_desired_stops(requirements_for_state(state))


def requirements_for_state(state: DatePlanningTaskState) -> list[DateStopRequirement]:
    return list(state.requirements)


def desired_stops_from_plan(plan: DatePlan | None) -> list[DesiredDateStop]:
    if plan is None:
        return []
    result: list[DesiredDateStop] = []
    for item in sorted(plan.items, key=lambda value: (value.day_index, value.order)):
        keyword = item.slot_keyword or next(iter(item.place.search_keywords), None)
        kind = _kind_for_item(item)
        meal_type = (
            MealType(item.meal_type) if item.meal_type in MealType._value2member_map_ else None
        )
        time_window = TimeWindow(label=item.time_label) if item.time_label else None
        after = (
            TemporalAnchor.DINNER
            if item.time_label in {"晚饭后", "晚餐后"}
            else StopReference(keyword=item.after_item)
            if item.after_item
            else None
        )
        if keyword is None and meal_type is None:
            continue
        result.append(
            DesiredDateStop(
                kind=kind,
                keyword=keyword,
                place_name=item.place.name if keyword is None else None,
                meal_type=meal_type,
                target_day=item.day_index if plan.day_count > 1 else None,
                time_window=time_window,
                after=after,
            )
        )
    return _dedupe_stops(result)


def desired_stops_from_legacy(
    *,
    dining_keywords: list[str],
    meal_keywords: dict[str, list[str]],
    activity_keywords: list[str],
    schedule_hints: list[str],
    target_day: int | None = None,
) -> list[DesiredDateStop]:
    return _dedupe_stops(
        desired_stops_from_legacy_slots(
            dining_keywords=dining_keywords,
            meal_keywords=meal_keywords,
            activity_keywords=activity_keywords,
            schedule_hints=schedule_hints,
            target_day=target_day,
        )
    )


def derive_legacy_slots(desired_stops: list[DesiredDateStop]) -> LegacyDateRequirementSlots:
    dining: list[str] = []
    activities: list[str] = []
    meals: dict[str, list[str]] = {}
    hints: list[str] = []
    for stop in desired_stops:
        value = stop.keyword or stop.place_name
        if value is not None:
            target = dining if stop.kind in {StopKind.DINING, StopKind.CAFE} else activities
            if value not in target:
                target.append(value)
            if stop.meal_type is not None:
                meals.setdefault(stop.meal_type.value, [])
                if value not in meals[stop.meal_type.value]:
                    meals[stop.meal_type.value].append(value)
        for hint in _placement_hints(stop):
            if hint not in hints:
                hints.append(hint)
    return LegacyDateRequirementSlots(
        dining_keywords=dining[:8],
        meal_keywords=meals,
        activity_keywords=activities[:8],
        schedule_hints=hints[:8],
    )


def project_requirements_to_state(
    state: DatePlanningTaskState,
    requirements: list[DateStopRequirement] | list[DesiredDateStop],
) -> DatePlanningTaskState:
    canonical = _normalize_requirements(requirements)
    desired_stops = primary_desired_stops(canonical)
    legacy = derive_legacy_slots(desired_stops)
    return state.model_copy(
        update={
            "requirements": canonical,
            "requirement_schema_version": 1,
            "desired_stops": desired_stops,
            "dining_keywords": legacy.dining_keywords,
            "meal_keywords": legacy.meal_keywords,
            "activity_keywords": legacy.activity_keywords,
            "schedule_hints": legacy.schedule_hints,
        }
    )


def inherit_desired_stop_role(
    replacement: DesiredDateStop,
    previous: DesiredDateStop | DatePlanItem,
) -> DesiredDateStop:
    if isinstance(previous, DatePlanItem):
        previous = _desired_stop_for_item(previous)
    same_role_family = replacement.kind == previous.kind or {
        replacement.kind,
        previous.kind,
    } <= {StopKind.DINING, StopKind.CAFE}
    return replacement.model_copy(
        update={
            "meal_type": (
                replacement.meal_type
                if replacement.meal_type is not None or not same_role_family
                else previous.meal_type
            ),
            "target_day": replacement.target_day or previous.target_day,
            "time_window": replacement.time_window or previous.time_window,
            "after": replacement.after or previous.after,
            "before": replacement.before or previous.before,
            "constraints": _merge_stop_constraints(
                replacement.constraints,
                previous.constraints,
            ),
        }
    )


def _apply_placement(
    current: DesiredDateStop,
    placement: DesiredDateStop,
) -> DesiredDateStop:
    return current.model_copy(
        update={
            "meal_type": placement.meal_type or current.meal_type,
            "target_day": placement.target_day or current.target_day,
            "time_window": placement.time_window or current.time_window,
            "after": placement.after or current.after,
            "before": placement.before or current.before,
            "constraints": _merge_stop_constraints(
                placement.constraints,
                current.constraints,
            ),
        }
    )


def _merge_stop_constraints(
    incoming: DateStopConstraints | None,
    current: DateStopConstraints | None,
) -> DateStopConstraints | None:
    if incoming is None:
        return current
    if current is None:
        return incoming
    return incoming.model_copy(
        update={
            field: (
                getattr(incoming, field)
                if getattr(incoming, field) is not None
                else getattr(current, field)
            )
            for field in (
                "max_cost_per_person",
                "min_rating",
                "preferred_area",
                "max_distance_meters",
            )
        }
    )


def _add_or_merge(
    current: list[DesiredDateStop],
    incoming: DesiredDateStop,
) -> list[DesiredDateStop]:
    index = next(
        (index for index, stop in enumerate(current) if _same_stop_identity(stop, incoming)),
        None,
    )
    if index is None:
        return [*current, incoming]
    merged = list(current)
    merged[index] = _apply_placement(current[index], incoming)
    return merged


def _target_index(
    desired_stops: list[DesiredDateStop],
    reference: StopReference | None,
    current_plan: DatePlan | None,
) -> int | None:
    if reference is None:
        return None
    if reference.ordinal is not None and reference.ordinal <= len(desired_stops):
        return reference.ordinal - 1
    reference_values = [
        _normalize(value)
        for value in (reference.keyword, reference.place_name)
        if value is not None
    ]
    if reference.place_id is not None and current_plan is not None:
        item = next(
            (value for value in current_plan.items if value.place.id == reference.place_id),
            None,
        )
        if item is not None:
            reference_values.extend(
                _normalize(value)
                for value in (item.slot_keyword, item.place.name)
                if value is not None
            )
    matches = [
        index
        for index, stop in enumerate(desired_stops)
        if _stop_matches_reference(stop, reference, reference_values)
    ]
    return matches[0] if len(matches) == 1 else None


def _stop_matches_reference(
    stop: DesiredDateStop,
    reference: StopReference,
    reference_values: list[str],
) -> bool:
    if reference.meal_type is not None and stop.meal_type == reference.meal_type:
        return True
    values = [_normalize(value) for value in (stop.keyword, stop.place_name) if value]
    return any(
        candidate in expected or expected in candidate
        for candidate in values
        for expected in reference_values
    )


def _target_item(
    reference: StopReference | None,
    current_plan: DatePlan | None,
) -> DatePlanItem | None:
    if reference is None or current_plan is None:
        return None
    ordered = sorted(current_plan.items, key=lambda item: (item.day_index, item.order))
    if reference.ordinal is not None:
        return ordered[reference.ordinal - 1] if reference.ordinal <= len(ordered) else None
    matches = [
        item
        for item in ordered
        if (reference.place_id is not None and item.place.id == reference.place_id)
        or (
            reference.meal_type is not None
            and (
                item.meal_type == reference.meal_type.value
                or (
                    item.meal_type is None
                    and item.place.category
                    in {PlaceCategory.RESTAURANT, PlaceCategory.CAFE}
                )
            )
        )
        or any(
            _normalize(value) in _normalize(candidate) or _normalize(candidate) in _normalize(value)
            for value in (reference.keyword, reference.place_name)
            if value is not None
            for candidate in (item.slot_keyword, item.place.name)
            if candidate is not None
        )
    ]
    return matches[0] if len(matches) == 1 else None


def _same_stop_identity(first: DesiredDateStop, second: DesiredDateStop) -> bool:
    if first.kind != second.kind and not (
        {first.kind, second.kind} <= {StopKind.DINING, StopKind.CAFE}
    ):
        return False
    first_value = _normalize(first.keyword or first.place_name or "")
    second_value = _normalize(second.keyword or second.place_name or "")
    return bool(
        first_value
        and second_value
        and first_value == second_value
        and first.meal_type == second.meal_type
        and first.target_day == second.target_day
    )


def _dedupe_stops(stops: list[DesiredDateStop]) -> list[DesiredDateStop]:
    result: list[DesiredDateStop] = []
    for stop in stops:
        result = _add_or_merge(result, stop)
    return result


def _normalize_requirements(
    values: list[DateStopRequirement] | list[DesiredDateStop],
) -> list[DateStopRequirement]:
    if not values:
        return []
    requirements = (
        list(values)
        if isinstance(values[0], DateStopRequirement)
        else [_single_requirement(stop) for stop in values if isinstance(stop, DesiredDateStop)]
    )
    return _dedupe_requirements(requirements)


def _single_requirement(
    stop: DesiredDateStop,
    source_span: str | None = None,
) -> DateStopRequirement:
    return DateStopRequirement(
        alternatives=[stop],
        source_span=source_span,
    )


def _dedupe_requirements(
    requirements: list[DateStopRequirement],
) -> list[DateStopRequirement]:
    result: list[DateStopRequirement] = []
    for incoming in requirements:
        index = next(
            (
                index
                for index, current in enumerate(result)
                if _same_requirement_identity(current, incoming)
            ),
            None,
        )
        if index is None:
            result.append(incoming)
            continue
        current = result[index]
        alternatives = list(current.alternatives)
        for alternative in incoming.alternatives:
            match = next(
                (
                    offset
                    for offset, existing in enumerate(alternatives)
                    if _same_stop_identity(existing, alternative)
                ),
                None,
            )
            if match is None:
                alternatives.append(alternative)
            else:
                alternatives[match] = _apply_placement(alternatives[match], alternative)
        result[index] = current.model_copy(
            update={
                "alternatives": alternatives,
                "source_span": incoming.source_span or current.source_span,
            }
        )
    return result


def _same_requirement_identity(
    first: DateStopRequirement,
    second: DateStopRequirement,
) -> bool:
    if first.id == second.id:
        return True
    if len(first.alternatives) == len(second.alternatives) == 1:
        return _same_stop_identity(first.alternatives[0], second.alternatives[0])
    return (
        first.min_satisfied == second.min_satisfied
        and first.max_satisfied == second.max_satisfied
        and {_stop_identity_key(stop) for stop in first.alternatives}
        == {_stop_identity_key(stop) for stop in second.alternatives}
    )


def _stop_identity_key(stop: DesiredDateStop) -> tuple[object, ...]:
    return (
        stop.kind,
        _normalize(stop.keyword or stop.place_name or ""),
        stop.meal_type,
        stop.target_day,
    )


def _add_or_merge_requirement(
    current: list[DateStopRequirement],
    incoming: DateStopRequirement,
) -> list[DateStopRequirement]:
    return _dedupe_requirements([*current, incoming])


def _apply_requirement_update(
    requirements: list[DateStopRequirement],
    operation: DatePlanOperation,
) -> list[DateStopRequirement]:
    update = operation.requirement_update
    if update is None:
        return requirements
    target_ids: list[str] = []
    for reference in update.targets:
        requirement_id = reference.requirement_id
        if requirement_id is None and reference.stop_reference is not None:
            candidates = _requirement_indexes_for_reference(
                requirements,
                reference.stop_reference,
            )
            if len(candidates) != 1:
                return requirements
            requirement_id = requirements[candidates[0]].id
        if requirement_id is None or not any(
            requirement.id == requirement_id for requirement in requirements
        ):
            return requirements
        target_ids.append(requirement_id)
    target_ids = list(dict.fromkeys(target_ids))
    if len(target_ids) != len(update.targets):
        return requirements
    targets = [
        requirement for requirement in requirements if requirement.id in target_ids
    ]
    if len(targets) != len(target_ids) or any(
        len(requirement.alternatives) != 1 for requirement in targets
    ):
        return requirements
    alternatives = [requirement.alternatives[0] for requirement in targets]
    try:
        grouped = DateStopRequirement(
            alternatives=alternatives,
            min_satisfied=update.min_satisfied,
            max_satisfied=update.max_satisfied,
            source_span=operation.source_span,
        )
    except ValueError:
        return requirements
    remaining = [
        requirement for requirement in requirements if requirement.id not in target_ids
    ]
    return _add_or_merge_requirement(remaining, grouped)


def _apply_requirement_move(
    requirements: list[DateStopRequirement],
    operation: DatePlanOperation,
    current_plan: DatePlan | None,
    requirement_matches: list[DateRequirementMatch],
    *,
    allowed_requirement_ids: set[str] | None = None,
) -> list[DateStopRequirement]:
    if operation.payload is None:
        return requirements
    bindings = _target_requirement_bindings(
        requirements,
        operation.target,
        current_plan,
        requirement_matches,
    )
    if len(bindings) == 1:
        binding = bindings[0]
        if (
            allowed_requirement_ids is not None
            and binding.requirement_id not in allowed_requirement_ids
        ):
            return requirements
        index = next(
            (
                offset
                for offset, requirement in enumerate(requirements)
                if requirement.id == binding.requirement_id
            ),
            None,
        )
        if index is None:
            return requirements
        current = requirements[index]
        alternatives = list(current.alternatives)
        alternatives[binding.alternative_index] = _apply_placement(
            alternatives[binding.alternative_index],
            operation.payload,
        )
        projected = list(requirements)
        projected[index] = current.model_copy(
            update={
                "alternatives": alternatives,
                "source_span": operation.source_span or current.source_span,
            }
        )
        return projected
    if not bindings and allowed_requirement_ids is None:
        target_item = _target_item(operation.target, current_plan)
        if target_item is not None:
            return _add_or_merge_requirement(
                requirements,
                _single_requirement(
                    _apply_placement(
                        _desired_stop_for_item(target_item),
                        operation.payload,
                    ),
                    operation.source_span,
                ),
            )
    return requirements


def _target_requirement_bindings(
    requirements: list[DateStopRequirement],
    reference: StopReference | None,
    current_plan: DatePlan | None,
    requirement_matches: list[DateRequirementMatch],
) -> list[DateRequirementBinding]:
    if reference is None:
        return []
    target_item = _target_item(reference, current_plan)
    if target_item is not None:
        return resolve_requirement_bindings_for_plan_item(
            place_id=target_item.place.id,
            requirements=requirements,
            plan=current_plan,
            matches=requirement_matches,
        )
    if (
        current_plan is None
        and reference.ordinal is not None
        and reference.ordinal <= len(requirements)
    ):
        requirement = requirements[reference.ordinal - 1]
        if len(requirement.alternatives) == 1:
            return [
                DateRequirementBinding(
                    requirement_id=requirement.id,
                    alternative_index=0,
                    place_id="unbound",
                    source="ordinal_fallback",
                )
            ]
    bindings: list[DateRequirementBinding] = []
    for requirement_index in _requirement_indexes_for_reference(requirements, reference):
        requirement = requirements[requirement_index]
        reference_values = [
            _normalize(value)
            for value in (reference.keyword, reference.place_name)
            if value is not None
        ]
        for alternative_index, alternative in enumerate(requirement.alternatives):
            if _stop_matches_reference(alternative, reference, reference_values):
                bindings.append(
                    DateRequirementBinding(
                        requirement_id=requirement.id,
                        alternative_index=alternative_index,
                        place_id=reference.place_id or "unbound",
                        source="keyword_fallback",
                    )
                )
    return bindings


def _remove_bound_requirements(
    requirements: list[DateStopRequirement],
    bindings: list[DateRequirementBinding],
) -> list[DateStopRequirement]:
    removals: dict[str, set[int]] = {}
    for binding in bindings:
        removals.setdefault(binding.requirement_id, set()).add(binding.alternative_index)
    result: list[DateStopRequirement] = []
    for requirement in requirements:
        indexes = removals.get(requirement.id)
        if not indexes:
            result.append(requirement)
            continue
        alternatives = [
            alternative
            for index, alternative in enumerate(requirement.alternatives)
            if index not in indexes
        ]
        if not alternatives:
            continue
        result.append(
            requirement.model_copy(
                update={
                    "alternatives": alternatives,
                    "min_satisfied": min(
                        requirement.min_satisfied,
                        len(alternatives),
                    ),
                    "max_satisfied": (
                        min(requirement.max_satisfied, len(alternatives))
                        if requirement.max_satisfied is not None
                        else None
                    ),
                }
            )
        )
    return result


def _requirement_indexes_for_reference(
    requirements: list[DateStopRequirement],
    reference: StopReference,
) -> list[int]:
    reference_values = [
        _normalize(value)
        for value in (reference.keyword, reference.place_name)
        if value is not None
    ]
    return [
        index
        for index, requirement in enumerate(requirements)
        if any(
            _stop_matches_reference(alternative, reference, reference_values)
            for alternative in requirement.alternatives
        )
    ]


def _target_requirement_index(
    requirements: list[DateStopRequirement],
    reference: StopReference | None,
    current_plan: DatePlan | None,
) -> int | None:
    if reference is None:
        return None
    ordered = (
        sorted(current_plan.items, key=lambda item: (item.day_index, item.order))
        if current_plan is not None
        else []
    )
    if reference.ordinal is not None and ordered:
        target = ordered[reference.ordinal - 1] if reference.ordinal <= len(ordered) else None
        if target is None:
            return None
        reference_values = [
            _normalize(value)
            for value in (target.slot_keyword, target.place.name)
            if value is not None
        ]
    elif reference.ordinal is not None and reference.ordinal <= len(requirements):
        return reference.ordinal - 1
    else:
        reference_values = [
            _normalize(value)
            for value in (reference.keyword, reference.place_name)
            if value is not None
        ]
        if reference.place_id is not None and current_plan is not None:
            target = next(
                (item for item in current_plan.items if item.place.id == reference.place_id),
                None,
            )
            if target is not None:
                reference_values.extend(
                    _normalize(value)
                    for value in (target.slot_keyword, target.place.name)
                    if value is not None
                )
    matches = [
        index
        for index, requirement in enumerate(requirements)
        if any(
            _stop_matches_reference(alternative, reference, reference_values)
            for alternative in requirement.alternatives
        )
    ]
    return matches[0] if len(matches) == 1 else None


def _alternative_add_groups(
    operations: list[DatePlanOperation] | tuple[DatePlanOperation, ...],
    source_text: str | None,
) -> list[list[DatePlanOperation]]:
    additions = [
        operation
        for operation in operations
        if operation.type == DateOperationType.ADD_STOP and operation.payload is not None
    ]
    if len(additions) < 2:
        return []
    explicit_groups: dict[str, list[DatePlanOperation]] = {}
    for operation in additions:
        if operation.alternative_group is not None:
            explicit_groups.setdefault(operation.alternative_group, []).append(operation)
    result = [group for group in explicit_groups.values() if len(group) > 1]
    explicitly_grouped = {id(operation) for group in result for operation in group}
    implicit = [operation for operation in additions if id(operation) not in explicitly_grouped]
    source_spans = list(
        dict.fromkeys(operation.source_span for operation in implicit if operation.source_span)
    )
    evidence = source_spans[0] if len(source_spans) == 1 else ""
    if not evidence and not source_spans:
        evidence = source_text or ""
    if _ALTERNATIVE_CUE.search(evidence) is None:
        return result
    groups: dict[tuple[object, ...], list[DatePlanOperation]] = {}
    for operation in implicit:
        payload = operation.payload
        if payload is None:
            continue
        signature = (
            payload.kind,
            payload.meal_type,
            payload.target_day,
            payload.time_window.model_dump_json() if payload.time_window else None,
            str(payload.after),
            str(payload.before),
        )
        groups.setdefault(signature, []).append(operation)
    result.extend(group for group in groups.values() if len(group) > 1)
    return result


def _shared_source_span(
    operations: list[DatePlanOperation],
    source_text: str | None,
) -> str | None:
    spans = list(
        dict.fromkeys(
            operation.source_span for operation in operations if operation.source_span
        )
    )
    if len(spans) == 1:
        return spans[0]
    return source_text


def _desired_stop_for_item(item: DatePlanItem) -> DesiredDateStop:
    keyword = item.slot_keyword or next(iter(item.place.search_keywords), None)
    meal_type = MealType(item.meal_type) if item.meal_type in MealType._value2member_map_ else None
    return DesiredDateStop(
        kind=_kind_for_item(item),
        keyword=keyword,
        place_name=item.place.name if keyword is None else None,
        meal_type=meal_type,
        target_day=item.day_index,
        time_window=TimeWindow(label=item.time_label) if item.time_label else None,
        after=(TemporalAnchor.DINNER if item.time_label in {"晚饭后", "晚餐后"} else None),
    )


def _kind_for_item(item: DatePlanItem) -> StopKind:
    return {
        PlaceCategory.RESTAURANT: StopKind.DINING,
        PlaceCategory.CAFE: StopKind.CAFE,
        PlaceCategory.ATTRACTION: StopKind.ACTIVITY,
        PlaceCategory.ENTERTAINMENT: StopKind.ACTIVITY,
    }[item.place.category]


def _placement_hints(stop: DesiredDateStop) -> list[str]:
    hints: list[str] = []
    if stop.time_window is not None and stop.time_window.label is not None:
        hints.append(stop.time_window.label)
    if isinstance(stop.after, StopReference):
        value = stop.after.keyword or stop.after.place_name
        if value:
            hints.append(f"{value}后")
    elif stop.after in {TemporalAnchor.DINNER, TemporalAnchor.AFTER_DINNER}:
        hints.append("晚饭后")
    elif stop.after == TemporalAnchor.LUNCH:
        hints.append("午饭后")
    elif stop.after == TemporalAnchor.BREAKFAST:
        hints.append("早餐后")
    if isinstance(stop.before, StopReference):
        value = stop.before.keyword or stop.before.place_name
        if value:
            hints.append(f"{value}前")
    return list(dict.fromkeys(hints))


_ALTERNATIVE_CUE = re.compile(r"(?:或者|或是|还是|也行|也可以|均可|任选|二选一)")


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold())

import re
import unicodedata
from dataclasses import dataclass

from loveapp.domain.date_operations import (
    DateOperationType,
    DatePlanOperation,
    DesiredDateStop,
    MealType,
    StopKind,
    StopReference,
    TemporalAnchor,
    TimeWindow,
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
        for operation in operations:
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


def desired_stops_for_state(state: DatePlanningTaskState) -> list[DesiredDateStop]:
    if state.desired_stops:
        return list(state.desired_stops)
    return desired_stops_from_legacy(
        dining_keywords=state.dining_keywords,
        meal_keywords=state.meal_keywords,
        activity_keywords=state.activity_keywords,
        schedule_hints=state.schedule_hints,
        target_day=state.target_day,
    )


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
    meal_by_keyword = {
        keyword: MealType(meal_type)
        for meal_type, keywords in meal_keywords.items()
        if meal_type in MealType._value2member_map_
        for keyword in keywords
    }
    result = [
        DesiredDateStop(
            kind=StopKind.CAFE if "咖啡" in keyword else StopKind.DINING,
            keyword=keyword,
            meal_type=meal_by_keyword.get(keyword),
            target_day=target_day,
        )
        for keyword in dining_keywords
    ]
    has_unique_after_dinner_target = len(activity_keywords) == 1 and (
        "晚饭后" in schedule_hints or "晚餐后" in schedule_hints
    )
    result.extend(
        DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword=keyword,
            target_day=target_day,
            time_window=(
                TimeWindow(label="晚饭后")
                if has_unique_after_dinner_target
                else None
            ),
            after=(
                TemporalAnchor.DINNER
                if has_unique_after_dinner_target
                else None
            ),
        )
        for keyword in activity_keywords
    )
    return _dedupe_stops(result)


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
    desired_stops: list[DesiredDateStop],
) -> DatePlanningTaskState:
    canonical = _dedupe_stops(desired_stops)
    legacy = derive_legacy_slots(canonical)
    return state.model_copy(
        update={
            "desired_stops": canonical,
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
        or (reference.meal_type is not None and item.meal_type == reference.meal_type.value)
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
    return bool(first_value and second_value and first_value == second_value)


def _dedupe_stops(stops: list[DesiredDateStop]) -> list[DesiredDateStop]:
    result: list[DesiredDateStop] = []
    for stop in stops:
        result = _add_or_merge(result, stop)
    return result


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


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold())

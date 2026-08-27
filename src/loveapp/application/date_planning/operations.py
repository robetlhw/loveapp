from dataclasses import dataclass
from typing import Protocol

from loveapp.application.date_planning.state_projection import inherit_desired_stop_role
from loveapp.application.date_planning.structured_stops import (
    has_placement_requirement,
    item_matches_reference,
    match_desired_stop,
)
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.date_operations import (
    DateOperationType,
    DatePlanOperation,
    DesiredDateStop,
    MealType,
    StopKind,
    StopReference,
    TemporalAnchor,
)
from loveapp.domain.date_plan import DatePlan, DatePlanItem, DatePlanRequest
from loveapp.domain.enums import DatePlanMutation, PlaceCategory


class DateOperationPlanner(Protocol):
    async def plan(
        self,
        request: DatePlanRequest,
        *,
        trace: ExecutionTrace | None = None,
        existing_plan: DatePlan | None = None,
        mutation: DatePlanMutation = DatePlanMutation.NONE,
        focus_activity_keywords: list[str] | None = None,
        focus_dining_keywords: list[str] | None = None,
    ) -> DatePlan: ...

    async def rebuild_plan(
        self,
        existing_plan: DatePlan,
        request: DatePlanRequest,
        items: list[DatePlanItem],
        *,
        summary: str,
        trace: ExecutionTrace | None = None,
    ) -> DatePlan: ...


@dataclass(frozen=True)
class RejectedDatePlanOperation:
    operation: DatePlanOperation
    reason: str


@dataclass(frozen=True)
class DatePlanOperationExecution:
    plan: DatePlan
    applied: tuple[DatePlanOperation, ...]
    rejected: tuple[RejectedDatePlanOperation, ...]
    effective_mutation: DatePlanMutation


class DateOperationExecutor:
    """Apply verified operations without changing the committed task snapshot."""

    def __init__(self, planner: DateOperationPlanner) -> None:
        self._planner = planner

    async def apply(
        self,
        existing_plan: DatePlan | None,
        operations: list[DatePlanOperation],
        request: DatePlanRequest,
        *,
        trace: ExecutionTrace | None = None,
        required_mutation: DatePlanMutation = DatePlanMutation.NONE,
    ) -> DatePlanOperationExecution:
        ordered = _ordered_operations(operations)
        if existing_plan is None:
            return await self._create_initial_plan(
                ordered,
                request,
                trace=trace,
                required_mutation=required_mutation,
            )

        plan = existing_plan
        applied: list[DatePlanOperation] = []
        rejected: list[RejectedDatePlanOperation] = []
        effective_mutation = DatePlanMutation.NONE

        constraint_operations = [
            operation
            for operation in ordered
            if operation.type == DateOperationType.UPDATE_CONSTRAINT
        ]
        if constraint_operations:
            plan = await self._planner.plan(
                _request_for_existing_plan_constraints(request, plan),
                trace=trace,
                existing_plan=plan,
                mutation=DatePlanMutation.UPDATE_CONSTRAINT,
            )
            applied.extend(constraint_operations)
            effective_mutation = DatePlanMutation.UPDATE_CONSTRAINT

        for operation in ordered:
            if operation.type == DateOperationType.UPDATE_CONSTRAINT:
                continue
            if operation.type == DateOperationType.REPLAN:
                plan = await self._planner.plan(
                    _request_with_add_operations(request, ordered),
                    trace=trace,
                    existing_plan=plan,
                    mutation=DatePlanMutation.REPLAN,
                )
                applied.append(operation)
                effective_mutation = DatePlanMutation.REPLAN
                continue
            if operation.type == DateOperationType.REMOVE_STOP:
                candidate, reason = await self._remove(plan, operation, request, trace=trace)
                mutation = DatePlanMutation.REMOVE
            elif operation.type == DateOperationType.REPLACE_STOP:
                candidate, reason = await self._replace(plan, operation, request, trace=trace)
                mutation = DatePlanMutation.REPLACE
            elif operation.type == DateOperationType.ADD_STOP:
                candidate, reason = await self._add(plan, operation, request, trace=trace)
                mutation = DatePlanMutation.ADD
            elif operation.type == DateOperationType.MOVE_STOP:
                candidate, reason = await self._move(plan, operation, request, trace=trace)
                mutation = DatePlanMutation.REORDER
            else:  # pragma: no cover - enum exhaustiveness guard
                candidate, reason = plan, "unsupported_operation"
                mutation = effective_mutation
            if reason is not None:
                rejected.append(RejectedDatePlanOperation(operation, reason))
                continue
            plan = candidate
            applied.append(operation)
            effective_mutation = mutation

        if (
            required_mutation == DatePlanMutation.REPLAN
            and effective_mutation != DatePlanMutation.REPLAN
        ):
            plan = await self._planner.plan(
                _request_with_add_operations(request, ordered),
                trace=trace,
                existing_plan=plan,
                mutation=DatePlanMutation.REPLAN,
            )
            effective_mutation = DatePlanMutation.REPLAN

        return DatePlanOperationExecution(
            plan=plan,
            applied=tuple(applied),
            rejected=tuple(rejected),
            effective_mutation=effective_mutation,
        )

    async def _create_initial_plan(
        self,
        operations: list[DatePlanOperation],
        request: DatePlanRequest,
        *,
        trace: ExecutionTrace | None,
        required_mutation: DatePlanMutation,
    ) -> DatePlanOperationExecution:
        rejected = [
            RejectedDatePlanOperation(operation, "operation_requires_existing_plan")
            for operation in operations
            if operation.type
            in {
                DateOperationType.REMOVE_STOP,
                DateOperationType.REPLACE_STOP,
                DateOperationType.MOVE_STOP,
            }
        ]
        eligible = [
            operation
            for operation in operations
            if operation.type
            in {
                DateOperationType.UPDATE_CONSTRAINT,
                DateOperationType.ADD_STOP,
                DateOperationType.REPLAN,
            }
        ]
        replan = required_mutation == DatePlanMutation.REPLAN or any(
            operation.type == DateOperationType.REPLAN for operation in eligible
        )
        plan = await self._planner.plan(
            _request_with_add_operations(request, eligible),
            trace=trace,
            mutation=DatePlanMutation.REPLAN if replan else DatePlanMutation.NONE,
        )
        applied: list[DatePlanOperation] = []
        for operation in eligible:
            if operation.type != DateOperationType.ADD_STOP or operation.payload is None:
                applied.append(operation)
                continue
            matches = list(match_desired_stop(plan, operation.payload))
            if len(matches) != 1:
                rejected.append(RejectedDatePlanOperation(operation, "stop_not_added"))
                continue
            if has_placement_requirement(operation.payload) and not matches[0].placement_satisfied:
                move = DatePlanOperation(
                    type=DateOperationType.MOVE_STOP,
                    target=StopReference(place_id=matches[0].item.place.id),
                    payload=operation.payload,
                )
                plan, reason = await self._move(plan, move, request, trace=trace)
                if reason is not None:
                    rejected.append(RejectedDatePlanOperation(operation, reason))
                    continue
            applied.append(operation)
        return DatePlanOperationExecution(
            plan=plan,
            applied=tuple(applied),
            rejected=tuple(rejected),
            effective_mutation=DatePlanMutation.REPLAN if replan else _effective_mutation(applied),
        )

    async def _remove(
        self,
        plan: DatePlan,
        operation: DatePlanOperation,
        request: DatePlanRequest,
        *,
        trace: ExecutionTrace | None,
    ) -> tuple[DatePlan, str | None]:
        target, reason = _unique_target(plan, operation.target)
        if target is None:
            return plan, reason
        if len(plan.items) <= 1:
            return plan, "operation_would_empty_plan"
        items = [item for item in plan.items if item.place.id != target.place.id]
        rebuilt = await self._planner.rebuild_plan(
            plan,
            request,
            _renumber_items(items),
            summary=f"已删除{target.place.name}并保留其他行程节点。",
            trace=trace,
        )
        return rebuilt, None

    async def _replace(
        self,
        plan: DatePlan,
        operation: DatePlanOperation,
        request: DatePlanRequest,
        *,
        trace: ExecutionTrace | None,
    ) -> tuple[DatePlan, str | None]:
        target, reason = _unique_target(plan, operation.target)
        if target is None:
            return plan, reason
        desired = operation.payload
        if desired is None:  # model validation protects this boundary
            return plan, "replacement_payload_missing"
        desired = inherit_desired_stop_role(desired, target)
        specific_request, activity_focus, dining_focus = _request_for_stop(
            request,
            desired,
            replace_place_name=target.place.name,
        )
        candidate = await self._planner.plan(
            specific_request,
            trace=trace,
            existing_plan=plan,
            mutation=DatePlanMutation.REPLACE,
            focus_activity_keywords=activity_focus,
            focus_dining_keywords=dining_focus,
        )
        candidate_ids = {item.place.id for item in candidate.items}
        if target.place.id in candidate_ids:
            return plan, "replacement_not_applied"
        return await self._apply_desired_placement(
            original=plan,
            candidate=candidate,
            desired=desired,
            request=specific_request,
            trace=trace,
        )

    async def _add(
        self,
        plan: DatePlan,
        operation: DatePlanOperation,
        request: DatePlanRequest,
        *,
        trace: ExecutionTrace | None,
    ) -> tuple[DatePlan, str | None]:
        desired = operation.payload
        if desired is None:  # model validation protects this boundary
            return plan, "add_payload_missing"
        specific_request, activity_focus, dining_focus = _request_for_stop(request, desired)
        candidate = await self._planner.plan(
            specific_request,
            trace=trace,
            existing_plan=plan,
            mutation=DatePlanMutation.ADD,
            focus_activity_keywords=activity_focus,
            focus_dining_keywords=dining_focus,
        )
        original_ids = {item.place.id for item in plan.items}
        if not any(item.place.id not in original_ids for item in candidate.items):
            return plan, "stop_not_added"
        return await self._apply_desired_placement(
            original=plan,
            candidate=candidate,
            desired=desired,
            request=specific_request,
            trace=trace,
        )

    async def _apply_desired_placement(
        self,
        *,
        original: DatePlan,
        candidate: DatePlan,
        desired: DesiredDateStop,
        request: DatePlanRequest,
        trace: ExecutionTrace | None,
    ) -> tuple[DatePlan, str | None]:
        if not has_placement_requirement(desired):
            return candidate, None
        original_ids = {item.place.id for item in original.items}
        matches = [
            match
            for match in match_desired_stop(candidate, desired)
            if match.item.place.id not in original_ids
        ]
        if len(matches) != 1:
            matches = list(match_desired_stop(candidate, desired))
        if len(matches) != 1:
            return original, "placement_target_not_unique"
        if matches[0].placement_satisfied and _placement_role_is_materialized(
            matches[0].item,
            desired,
        ):
            return candidate, None
        move = DatePlanOperation(
            type=DateOperationType.MOVE_STOP,
            target=StopReference(place_id=matches[0].item.place.id),
            payload=desired,
            confidence=1,
        )
        moved, reason = await self._move(candidate, move, request, trace=trace)
        return (moved, None) if reason is None else (original, reason)

    async def _move(
        self,
        plan: DatePlan,
        operation: DatePlanOperation,
        request: DatePlanRequest,
        *,
        trace: ExecutionTrace | None,
    ) -> tuple[DatePlan, str | None]:
        target, reason = _unique_target(plan, operation.target)
        if target is None:
            return plan, reason
        desired = operation.payload
        if desired is None:  # model validation protects this boundary
            return plan, "move_payload_missing"
        ordered, reason = _move_items(plan, target, desired)
        if reason is not None:
            return plan, reason
        rebuilt = await self._planner.rebuild_plan(
            plan,
            request,
            ordered,
            summary=f"已按新的时段要求调整{target.place.name}的位置。",
            trace=trace,
        )
        return rebuilt, None


def _ordered_operations(operations: list[DatePlanOperation]) -> list[DatePlanOperation]:
    order = {
        DateOperationType.UPDATE_CONSTRAINT: 0,
        DateOperationType.REMOVE_STOP: 1,
        DateOperationType.REPLACE_STOP: 2,
        DateOperationType.ADD_STOP: 3,
        DateOperationType.MOVE_STOP: 4,
        DateOperationType.REPLAN: 5,
    }
    return [
        operation
        for _, operation in sorted(
            enumerate(operations),
            key=lambda item: (order[item[1].type], item[0]),
        )
    ]


def _request_with_add_operations(
    request: DatePlanRequest,
    operations: list[DatePlanOperation],
) -> DatePlanRequest:
    result = request
    for operation in operations:
        if operation.type == DateOperationType.ADD_STOP and operation.payload is not None:
            result, _, _ = _request_for_stop(result, operation.payload)
    return result


def _request_for_existing_plan_constraints(
    request: DatePlanRequest,
    plan: DatePlan,
) -> DatePlanRequest:
    dining_keywords: list[str] = []
    activity_keywords: list[str] = []
    meal_keywords: dict[str, list[str]] = {}
    schedule_hints: list[str] = []
    for item in plan.items:
        keyword = item.slot_keyword or next(iter(item.place.search_keywords), None)
        if keyword is not None:
            target = (
                dining_keywords
                if item.place.category in {PlaceCategory.RESTAURANT, PlaceCategory.CAFE}
                else activity_keywords
            )
            if keyword not in target:
                target.append(keyword)
            if item.meal_type is not None:
                meal_keywords.setdefault(item.meal_type, []).append(keyword)
        if item.time_label is not None and item.time_label not in schedule_hints:
            schedule_hints.append(item.time_label)
    return request.model_copy(
        update={
            "dining_keywords": dining_keywords,
            "activity_keywords": activity_keywords,
            "meal_keywords": meal_keywords,
            "schedule_hints": schedule_hints[:8],
            "replace_place_names": [],
            "excluded_keywords": [],
        }
    )


def _request_for_stop(
    request: DatePlanRequest,
    desired: DesiredDateStop,
    *,
    replace_place_name: str | None = None,
) -> tuple[DatePlanRequest, list[str], list[str]]:
    value = desired.keyword or desired.place_name
    activity_focus: list[str] = []
    dining_focus: list[str] = []
    updates: dict[str, object] = {}
    if value is not None:
        if desired.kind in {StopKind.DINING, StopKind.CAFE}:
            dining_focus = [value]
            updates["dining_keywords"] = list(dict.fromkeys([*request.dining_keywords, value]))
        else:
            activity_focus = [value]
            updates["activity_keywords"] = list(dict.fromkeys([*request.activity_keywords, value]))
    if desired.meal_type is not None and value is not None:
        meal_keywords = {
            meal: [keyword for keyword in keywords if keyword != value]
            for meal, keywords in request.meal_keywords.items()
        }
        meal_keywords = {meal: keywords for meal, keywords in meal_keywords.items() if keywords}
        meal_keywords[desired.meal_type.value] = list(
            dict.fromkeys([*meal_keywords.get(desired.meal_type.value, []), value])
        )
        updates["meal_keywords"] = meal_keywords
    hints = _placement_hints(desired)
    if hints:
        updates["schedule_hints"] = list(dict.fromkeys([*request.schedule_hints, *hints]))[:8]
    if desired.target_day is not None:
        updates["target_day"] = desired.target_day
    if replace_place_name is not None:
        updates["replace_place_names"] = [replace_place_name]
    return request.model_copy(update=updates), activity_focus, dining_focus


def _placement_hints(desired: DesiredDateStop) -> list[str]:
    hints = [desired.time_window.label] if desired.time_window and desired.time_window.label else []
    for anchor, suffix in ((desired.after, "后"), (desired.before, "前")):
        if isinstance(anchor, StopReference):
            value = anchor.keyword or anchor.place_name
            if value is not None:
                hints.append(f"{value}{suffix}")
        elif anchor is not None:
            label = {
                TemporalAnchor.BREAKFAST: "早餐",
                TemporalAnchor.LUNCH: "午饭",
                TemporalAnchor.DINNER: "晚饭",
                TemporalAnchor.AFTER_DINNER: "晚饭",
                TemporalAnchor.AFTERNOON: "下午",
                TemporalAnchor.EVENING: "晚上",
            }.get(anchor)
            if label is not None:
                hints.append(f"{label}{suffix}")
    return list(dict.fromkeys(hints))


def _unique_target(
    plan: DatePlan,
    reference: StopReference | None,
) -> tuple[DatePlanItem | None, str | None]:
    if reference is None:
        return None, "operation_target_missing"
    ordered = sorted(plan.items, key=lambda item: (item.day_index, item.order))
    if reference.ordinal is not None:
        ordinal_match = (
            [ordered[reference.ordinal - 1]] if reference.ordinal <= len(ordered) else []
        )
        has_other_identity = any(
            value is not None
            for value in (
                reference.place_id,
                reference.place_name,
                reference.keyword,
                reference.meal_type,
            )
        )
        matches = (
            [item for item in ordinal_match if item_matches_reference(item, reference)]
            if has_other_identity
            else ordinal_match
        )
    else:
        matches = [item for item in ordered if item_matches_reference(item, reference)]
    if not matches:
        return None, "operation_target_not_found"
    if len(matches) != 1:
        return None, "operation_target_ambiguous"
    return matches[0], None


def _move_items(
    plan: DatePlan,
    target: DatePlanItem,
    desired: DesiredDateStop,
) -> tuple[list[DatePlanItem], str | None]:
    target_day = desired.target_day or target.day_index
    moved = target.model_copy(
        update={
            "day_index": target_day,
            "meal_type": (
                desired.meal_type.value if desired.meal_type is not None else target.meal_type
            ),
            "time_label": _desired_time_label(desired) or target.time_label,
            "after_item": None,
            "slot_keyword": desired.keyword or target.slot_keyword,
            "route_from_previous": None,
        }
    )
    remaining = [item for item in plan.items if item.place.id != target.place.id]
    day_items = sorted(
        [item for item in remaining if item.day_index == target_day],
        key=lambda item: item.order,
    )

    if desired.after is not None:
        anchor, reason = _unique_anchor(day_items, desired.after)
        if reason is None and anchor is not None:
            index = day_items.index(anchor) + 1
            moved = moved.model_copy(
                update={"after_item": anchor.slot_keyword or anchor.place.name}
            )
        elif isinstance(desired.after, StopReference):
            return list(plan.items), reason
        else:
            index = _ranked_insert_index(day_items, desired)
    elif desired.before is not None:
        anchor, reason = _unique_anchor(day_items, desired.before)
        if reason is None and anchor is not None:
            index = day_items.index(anchor)
        elif isinstance(desired.before, StopReference):
            return list(plan.items), reason
        else:
            index = _ranked_insert_index(day_items, desired)
    else:
        index = _ranked_insert_index(day_items, desired)
    day_items.insert(index, moved)
    day_items = [
        item.model_copy(update={"order": order}) for order, item in enumerate(day_items, start=1)
    ]

    by_day: dict[int, list[DatePlanItem]] = {}
    for item in remaining:
        if item.day_index != target_day:
            by_day.setdefault(item.day_index, []).append(item)
    by_day[target_day] = day_items
    ordered: list[DatePlanItem] = []
    for day_index in sorted(by_day):
        ordered.extend(sorted(by_day[day_index], key=lambda item: item.order))
    return _renumber_items(ordered), None


def _unique_anchor(
    items: list[DatePlanItem],
    anchor: TemporalAnchor | StopReference,
) -> tuple[DatePlanItem | None, str | None]:
    if isinstance(anchor, StopReference):
        matches = [item for item in items if item_matches_reference(item, anchor)]
    else:
        meal_type = {
            TemporalAnchor.BREAKFAST: MealType.BREAKFAST,
            TemporalAnchor.LUNCH: MealType.LUNCH,
            TemporalAnchor.DINNER: MealType.DINNER,
            TemporalAnchor.AFTER_DINNER: MealType.DINNER,
        }.get(anchor)
        matches = (
            [item for item in items if item.meal_type == meal_type.value]
            if meal_type is not None
            else []
        )
    if not matches:
        return None, "placement_anchor_not_found"
    if len(matches) != 1:
        return None, "placement_anchor_ambiguous"
    return matches[0], None


def _ranked_insert_index(
    items: list[DatePlanItem],
    desired: DesiredDateStop,
) -> int:
    desired_rank = _desired_rank(desired)
    return next(
        (index for index, item in enumerate(items) if _item_rank(item) > desired_rank),
        len(items),
    )


def _desired_rank(desired: DesiredDateStop) -> int:
    if desired.meal_type is not None:
        return {
            MealType.BREAKFAST: 10,
            MealType.LUNCH: 20,
            MealType.DINNER: 50,
        }[desired.meal_type]
    label = desired.time_window.label if desired.time_window else None
    if label and "上午" in label:
        return 15
    if label and "下午" in label:
        return 30
    if label and ("晚饭后" in label or "晚餐后" in label or "晚上" in label):
        return 60
    if isinstance(desired.after, TemporalAnchor) and desired.after in {
        TemporalAnchor.DINNER,
        TemporalAnchor.AFTER_DINNER,
    }:
        return 60
    return 30


def _item_rank(item: DatePlanItem) -> int:
    if item.meal_type == MealType.BREAKFAST.value:
        return 10
    if item.meal_type == MealType.LUNCH.value:
        return 20
    if item.meal_type == MealType.DINNER.value:
        return 50
    if item.time_label and "上午" in item.time_label:
        return 15
    if item.time_label and "下午" in item.time_label:
        return 30
    if item.time_label and ("晚饭后" in item.time_label or "晚上" in item.time_label):
        return 60
    return 30


def _desired_time_label(desired: DesiredDateStop) -> str | None:
    if desired.time_window is not None and desired.time_window.label is not None:
        return desired.time_window.label
    if desired.meal_type is not None:
        return {
            MealType.BREAKFAST: "早餐",
            MealType.LUNCH: "午餐",
            MealType.DINNER: "晚餐",
        }[desired.meal_type]
    if isinstance(desired.after, TemporalAnchor) and desired.after in {
        TemporalAnchor.DINNER,
        TemporalAnchor.AFTER_DINNER,
    }:
        return "晚饭后"
    return None


def _placement_role_is_materialized(
    item: DatePlanItem,
    desired: DesiredDateStop,
) -> bool:
    if (
        desired.time_window is not None
        and desired.time_window.label is not None
        and item.time_label != desired.time_window.label
    ):
        return False
    if isinstance(desired.after, StopReference):
        expected = desired.after.keyword or desired.after.place_name
        actual = item.after_item
        if expected is None or actual is None:
            return False
        normalized_expected = "".join(expected.casefold().split())
        normalized_actual = "".join(actual.casefold().split())
        if (
            normalized_expected not in normalized_actual
            and normalized_actual not in normalized_expected
        ):
            return False
    elif desired.after in {TemporalAnchor.DINNER, TemporalAnchor.AFTER_DINNER}:
        if item.time_label not in {"晚饭后", "晚餐后"}:
            return False
    return True


def _renumber_items(items: list[DatePlanItem]) -> list[DatePlanItem]:
    day_orders: dict[int, int] = {}
    result: list[DatePlanItem] = []
    for item in items:
        day_orders[item.day_index] = day_orders.get(item.day_index, 0) + 1
        result.append(
            item.model_copy(
                update={
                    "order": day_orders[item.day_index],
                    "route_from_previous": None,
                }
            )
        )
    return result


def _effective_mutation(operations: list[DatePlanOperation]) -> DatePlanMutation:
    mutation_by_type = {
        DateOperationType.UPDATE_CONSTRAINT: DatePlanMutation.UPDATE_CONSTRAINT,
        DateOperationType.ADD_STOP: DatePlanMutation.ADD,
        DateOperationType.REPLAN: DatePlanMutation.REPLAN,
    }
    return next(
        (
            mutation_by_type[operation.type]
            for operation in reversed(operations)
            if operation.type in mutation_by_type
        ),
        DatePlanMutation.NONE,
    )

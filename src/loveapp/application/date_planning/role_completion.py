from dataclasses import dataclass

from loveapp.application.date_planning.structured_stops import match_desired_stop
from loveapp.domain.date_operations import DesiredDateStop, MealType, TemporalAnchor
from loveapp.domain.date_plan import DatePlan, DatePlanItem
from loveapp.domain.enums import PlaceCategory


@dataclass(frozen=True)
class DateRoleCompletionResult:
    plan: DatePlan
    inferred_dinner_item_ids: tuple[str, ...] = ()
    unresolved_roles: tuple[str, ...] = ()
    reordered: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.inferred_dinner_item_ids) or self.reordered


def complete_date_plan_roles(
    plan: DatePlan,
    desired_stops: list[DesiredDateStop],
) -> DateRoleCompletionResult:
    """Complete only roles implied by a unique structural dependency."""

    after_dinner = [stop for stop in desired_stops if _requires_dinner_anchor(stop)]
    if not after_dinner or not plan.items:
        return DateRoleCompletionResult(plan=plan)

    items = list(plan.items)
    dependent_ids: set[str] = set()
    required_days: set[int] = set()
    for stop in after_dinner:
        matches = [match.item for match in match_desired_stop(plan, stop)]
        dependent_ids.update(item.place.id for item in matches)
        if stop.target_day is not None:
            required_days.add(stop.target_day)
        elif len(matches) == 1:
            required_days.add(matches[0].day_index)
        elif plan.day_count == 1:
            required_days.add(1)

    inferred: list[str] = []
    unresolved: list[str] = []
    for day_index in sorted(required_days):
        day_items = [item for item in items if item.day_index == day_index]
        if any(item.meal_type == MealType.DINNER.value for item in day_items):
            continue
        candidates = [
            item
            for item in day_items
            if item.meal_type is None
            and item.place.category in {PlaceCategory.RESTAURANT, PlaceCategory.CAFE}
        ]
        if len(candidates) != 1:
            unresolved.append(
                f"day_{day_index}:dinner_anchor_{'missing' if not candidates else 'ambiguous'}"
            )
            continue
        candidate = candidates[0]
        inferred.append(candidate.place.id)
        items = [
            item.model_copy(update={"meal_type": MealType.DINNER.value, "time_label": "晚餐"})
            if item.place.id == candidate.place.id
            else item
            for item in items
        ]

    ordered = _order_by_temporal_roles(items, dependent_ids)
    reordered = [item.place.id for item in ordered] != [item.place.id for item in items]
    return DateRoleCompletionResult(
        plan=plan.model_copy(update={"items": ordered}),
        inferred_dinner_item_ids=tuple(inferred),
        unresolved_roles=tuple(unresolved),
        reordered=reordered,
    )


def _requires_dinner_anchor(stop: DesiredDateStop) -> bool:
    if isinstance(stop.after, TemporalAnchor) and stop.after in {
        TemporalAnchor.DINNER,
        TemporalAnchor.AFTER_DINNER,
    }:
        return True
    label = stop.time_window.label if stop.time_window is not None else None
    return label is not None and ("晚饭后" in label or "晚餐后" in label)


def _order_by_temporal_roles(
    items: list[DatePlanItem],
    dependent_ids: set[str],
) -> list[DatePlanItem]:
    result: list[DatePlanItem] = []
    for day_index in sorted({item.day_index for item in items}):
        day_items = [item for item in items if item.day_index == day_index]
        day_items.sort(
            key=lambda item: (
                _temporal_rank(item, item.place.id in dependent_ids),
                item.order,
            )
        )
        result.extend(
            item.model_copy(update={"order": order, "route_from_previous": None})
            for order, item in enumerate(day_items, start=1)
        )
    return result


def _temporal_rank(item: DatePlanItem, is_after_dinner: bool) -> int:
    if item.meal_type == MealType.BREAKFAST.value:
        return 10
    if item.meal_type == MealType.LUNCH.value:
        return 20
    if item.time_label and "上午" in item.time_label:
        return 15
    if item.time_label and "下午" in item.time_label:
        return 30
    if is_after_dinner or (
        item.time_label is not None
        and ("晚饭后" in item.time_label or "晚餐后" in item.time_label)
    ):
        return 60
    if item.meal_type == MealType.DINNER.value:
        return 50
    if item.place.category in {PlaceCategory.RESTAURANT, PlaceCategory.CAFE}:
        return 40
    return 30

from dataclasses import dataclass

from loveapp.application.date_planning.structured_stops import match_desired_stop
from loveapp.domain.date_operations import DesiredDateStop, MealType, StopKind, TemporalAnchor
from loveapp.domain.date_plan import DatePlan, DatePlanItem
from loveapp.domain.enums import PlaceCategory


@dataclass(frozen=True)
class DateRoleCompletionResult:
    plan: DatePlan
    inferred_lunch_item_ids: tuple[str, ...] = ()
    inferred_dinner_item_ids: tuple[str, ...] = ()
    unresolved_roles: tuple[str, ...] = ()
    reordered: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.inferred_lunch_item_ids or self.inferred_dinner_item_ids) or self.reordered


def complete_date_plan_roles(
    plan: DatePlan,
    desired_stops: list[DesiredDateStop],
) -> DateRoleCompletionResult:
    """Complete only roles implied by a unique structural dependency."""

    anchored = [
        (stop, anchor)
        for stop in desired_stops
        if (anchor := _required_meal_anchor(stop)) is not None
    ]
    if not anchored or not plan.items:
        return DateRoleCompletionResult(plan=plan)

    items = list(plan.items)
    dependent_anchors: dict[tuple[int, str], MealType] = {}
    requirement_owned_items: set[tuple[int, str]] = set()
    for stop in desired_stops:
        if stop.kind not in {StopKind.DINING, StopKind.CAFE}:
            continue
        requirement_owned_items.update(
            (match.item.day_index, match.item.place.id)
            for match in match_desired_stop(plan, stop)
        )
    required_days: dict[MealType, set[int]] = {}
    for stop, anchor in anchored:
        matches = [match.item for match in match_desired_stop(plan, stop)]
        dependent_anchors.update(
            {(item.day_index, item.place.id): anchor for item in matches}
        )
        if stop.target_day is not None:
            required_days.setdefault(anchor, set()).add(stop.target_day)
        elif len(matches) == 1:
            required_days.setdefault(anchor, set()).add(matches[0].day_index)
        elif plan.day_count == 1:
            required_days.setdefault(anchor, set()).add(1)

    inferred: dict[MealType, list[str]] = {MealType.LUNCH: [], MealType.DINNER: []}
    unresolved: list[str] = []
    all_days = sorted({day for days in required_days.values() for day in days})
    anchor_order = (MealType.LUNCH, MealType.DINNER)
    for day_index in all_days:
        day_items = [item for item in items if item.day_index == day_index]
        missing_anchors = [
            anchor
            for anchor in anchor_order
            if day_index in required_days.get(anchor, set())
            and not any(item.meal_type == anchor.value for item in day_items)
        ]
        all_candidates = sorted(
            (
                item
                for item in day_items
                if item.meal_type is None
                and item.place.category in {PlaceCategory.RESTAURANT, PlaceCategory.CAFE}
            ),
            key=lambda item: item.order,
        )
        owned_candidates = [
            item
            for item in all_candidates
            if (item.day_index, item.place.id) in requirement_owned_items
        ]
        candidates = owned_candidates or [
            item
            for item in all_candidates
            if item.place.category == PlaceCategory.RESTAURANT
        ]
        if missing_anchors and len(candidates) == len(missing_anchors):
            assignments = zip(missing_anchors, candidates, strict=True)
            for anchor, candidate in assignments:
                inferred[anchor].append(candidate.place.id)
                label = "午餐" if anchor == MealType.LUNCH else "晚餐"
                items = [
                    item.model_copy(update={"meal_type": anchor.value, "time_label": label})
                    if item.day_index == day_index and item.place.id == candidate.place.id
                    else item
                    for item in items
                ]
            continue
        for anchor in missing_anchors:
            unresolved.append(
                f"day_{day_index}:{anchor.value}_anchor_"
                f"{'missing' if not candidates else 'ambiguous'}"
            )

    ordered = _order_by_temporal_roles(items, dependent_anchors)
    reordered = [item.place.id for item in ordered] != [item.place.id for item in items]
    return DateRoleCompletionResult(
        plan=plan.model_copy(update={"items": ordered}),
        inferred_lunch_item_ids=tuple(inferred[MealType.LUNCH]),
        inferred_dinner_item_ids=tuple(inferred[MealType.DINNER]),
        unresolved_roles=tuple(unresolved),
        reordered=reordered,
    )


def _required_meal_anchor(stop: DesiredDateStop) -> MealType | None:
    if stop.after == TemporalAnchor.LUNCH:
        return MealType.LUNCH
    if isinstance(stop.after, TemporalAnchor) and stop.after in {
        TemporalAnchor.DINNER,
        TemporalAnchor.AFTER_DINNER,
    }:
        return MealType.DINNER
    label = stop.time_window.label if stop.time_window is not None else None
    if label is not None and ("午饭后" in label or "午餐后" in label):
        return MealType.LUNCH
    if label is not None and ("晚饭后" in label or "晚餐后" in label):
        return MealType.DINNER
    return None


def _order_by_temporal_roles(
    items: list[DatePlanItem],
    dependent_anchors: dict[tuple[int, str], MealType],
) -> list[DatePlanItem]:
    result: list[DatePlanItem] = []
    for day_index in sorted({item.day_index for item in items}):
        day_items = [item for item in items if item.day_index == day_index]
        day_items.sort(
            key=lambda item: (
                _temporal_rank(
                    item,
                    dependent_anchors.get((item.day_index, item.place.id)),
                ),
                item.order,
            )
        )
        result.extend(
            item.model_copy(update={"order": order, "route_from_previous": None})
            for order, item in enumerate(day_items, start=1)
        )
    return result


def _temporal_rank(item: DatePlanItem, after_anchor: MealType | None) -> int:
    if item.meal_type == MealType.BREAKFAST.value:
        return 10
    if item.meal_type == MealType.LUNCH.value:
        return 20
    if item.time_label and "上午" in item.time_label:
        return 15
    if item.time_label and "下午" in item.time_label:
        return 30
    if after_anchor == MealType.LUNCH:
        return 30
    if after_anchor == MealType.DINNER or (
        item.time_label is not None
        and ("晚饭后" in item.time_label or "晚餐后" in item.time_label)
    ):
        return 60
    if item.meal_type == MealType.DINNER.value:
        return 50
    if item.place.category in {PlaceCategory.RESTAURANT, PlaceCategory.CAFE}:
        return 40
    return 30

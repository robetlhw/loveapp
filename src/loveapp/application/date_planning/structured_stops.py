import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from loveapp.domain.date_operations import (
    DesiredDateStop,
    MealType,
    StopKind,
    StopReference,
    TemporalAnchor,
    TimeWindow,
)
from loveapp.domain.date_plan import DatePlan, DatePlanItem
from loveapp.domain.enums import PlaceCategory


@dataclass(frozen=True)
class DateStopMatch:
    item: DatePlanItem
    placement_satisfied: bool


def match_desired_stop(
    plan: DatePlan | None,
    desired: DesiredDateStop,
    *,
    keyword_aliases: Mapping[str, Sequence[str]] | None = None,
) -> tuple[DateStopMatch, ...]:
    """Match stop identity first, then evaluate the requested placement."""

    if plan is None:
        return ()
    aliases = keyword_aliases or {}
    return tuple(
        DateStopMatch(
            item=item,
            placement_satisfied=requirement_satisfied(
                item,
                desired,
                plan,
                keyword_aliases=aliases,
            ),
        )
        for item in plan.items
        if _identity_matches(item, desired, keyword_aliases=aliases)
    )


def requirement_satisfied(
    item: DatePlanItem,
    desired: DesiredDateStop,
    plan: DatePlan,
    *,
    keyword_aliases: Mapping[str, Sequence[str]] | None = None,
) -> bool:
    aliases = keyword_aliases or {}
    if desired.target_day is not None and item.day_index != desired.target_day:
        return False
    if desired.meal_type is not None and item.meal_type != desired.meal_type.value:
        return False
    if desired.time_window is not None and not _time_window_satisfied(
        item,
        desired.time_window,
        plan,
    ):
        return False
    if desired.after is not None:
        anchor_order = _anchor_order(
            plan,
            item.day_index,
            desired.after,
            keyword_aliases=aliases,
        )
        if anchor_order is None or item.order <= anchor_order:
            return False
    if desired.before is not None:
        anchor_order = _anchor_order(
            plan,
            item.day_index,
            desired.before,
            keyword_aliases=aliases,
        )
        if anchor_order is None or item.order >= anchor_order:
            return False
    return True


def has_placement_requirement(stop: DesiredDateStop) -> bool:
    return any(
        value is not None
        for value in (stop.meal_type, stop.target_day, stop.time_window, stop.after, stop.before)
    )


def item_matches_reference(
    item: DatePlanItem,
    reference: StopReference,
    *,
    keyword_aliases: Mapping[str, Sequence[str]] | None = None,
) -> bool:
    if reference.place_id is not None and item.place.id == reference.place_id:
        return True
    if reference.meal_type is not None and item.meal_type == reference.meal_type.value:
        return True
    value = reference.keyword or reference.place_name
    return value is not None and item_matches_keyword(
        item,
        value,
        keyword_aliases=keyword_aliases,
    )


def item_matches_keyword(
    item: DatePlanItem,
    keyword: str,
    *,
    keyword_aliases: Mapping[str, Sequence[str]] | None = None,
) -> bool:
    aliases = (keyword_aliases or {}).get(keyword, (keyword,))
    haystack = _normalize(
        " ".join(
            [
                item.slot_keyword or "",
                item.place.name,
                item.place.type_name or "",
                *item.place.tags,
                *item.place.search_keywords,
            ]
        )
    )
    return any(_normalize(alias) in haystack for alias in aliases)


def _identity_matches(
    item: DatePlanItem,
    desired: DesiredDateStop,
    *,
    keyword_aliases: Mapping[str, Sequence[str]],
) -> bool:
    if desired.target_day is not None and item.day_index != desired.target_day:
        return False
    actual_kind = _stop_kind(item)
    if desired.kind != StopKind.OTHER and actual_kind != desired.kind:
        return False
    identity_values = [
        value for value in (desired.keyword, desired.place_name) if value is not None
    ]
    if identity_values:
        return any(
            item_matches_keyword(item, value, keyword_aliases=keyword_aliases)
            for value in identity_values
        )
    return desired.meal_type is not None and item.meal_type == desired.meal_type.value


def _stop_kind(item: DatePlanItem) -> StopKind:
    return {
        PlaceCategory.RESTAURANT: StopKind.DINING,
        PlaceCategory.CAFE: StopKind.CAFE,
        PlaceCategory.ATTRACTION: StopKind.ACTIVITY,
        PlaceCategory.ENTERTAINMENT: StopKind.ACTIVITY,
    }[item.place.category]


def _time_window_satisfied(
    item: DatePlanItem,
    window: TimeWindow,
    plan: DatePlan,
) -> bool:
    if window.start is not None or window.end is not None:
        expected = window.label or (
            window.start.strftime("%H:%M") if window.start is not None else None
        )
        return expected is not None and _normalize(expected) == _normalize(item.time_label or "")
    label = _normalize(window.label or "")
    if label in {_normalize("晚饭后"), _normalize("晚餐后")}:
        dinner_order = _meal_anchor_order(plan, item.day_index, MealType.DINNER)
        return dinner_order is not None and item.order > dinner_order
    expected_meal = {
        _normalize("早餐"): MealType.BREAKFAST,
        _normalize("早饭"): MealType.BREAKFAST,
        _normalize("午餐"): MealType.LUNCH,
        _normalize("午饭"): MealType.LUNCH,
        _normalize("晚餐"): MealType.DINNER,
        _normalize("晚饭"): MealType.DINNER,
    }.get(label)
    if expected_meal is not None:
        return item.meal_type == expected_meal.value
    return bool(label) and label in _normalize(item.time_label or "")


def _anchor_order(
    plan: DatePlan,
    day_index: int,
    anchor: TemporalAnchor | StopReference,
    *,
    keyword_aliases: Mapping[str, Sequence[str]],
) -> int | None:
    if isinstance(anchor, StopReference):
        matches = [
            item
            for item in plan.items
            if item.day_index == day_index
            and item_matches_reference(
                item,
                anchor,
                keyword_aliases=keyword_aliases,
            )
        ]
        return matches[0].order if len(matches) == 1 else None
    meal_type = {
        TemporalAnchor.BREAKFAST: MealType.BREAKFAST,
        TemporalAnchor.LUNCH: MealType.LUNCH,
        TemporalAnchor.DINNER: MealType.DINNER,
        TemporalAnchor.AFTER_DINNER: MealType.DINNER,
    }.get(anchor)
    return _meal_anchor_order(plan, day_index, meal_type) if meal_type is not None else None


def _meal_anchor_order(
    plan: DatePlan,
    day_index: int,
    meal_type: MealType,
) -> int | None:
    matches = [
        item
        for item in plan.items
        if item.day_index == day_index and item.meal_type == meal_type.value
    ]
    return matches[0].order if len(matches) == 1 else None


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold())

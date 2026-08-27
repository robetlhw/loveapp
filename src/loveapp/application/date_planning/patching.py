from datetime import timedelta
from typing import Any

from loveapp.domain.date_patch import DatePlanPatch
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.enums import DatePlanMode, DatePlanMutation, PlaceCategory

_SCALAR_FIELDS = (
    "city",
    "area",
    "plan_mode",
    "date",
    "end_date",
    "day_count",
    "nights",
    "target_day",
    "start_time",
    "budget",
    "budget_scope",
    "transport_mode",
)
_LIST_FIELDS = (
    "preferences",
    "dining_keywords",
    "activity_keywords",
    "schedule_hints",
    "excluded_keywords",
    "notes",
    "constraints",
    "lodging_notes",
)


class DatePlanPatchApplier:
    """Apply a current-turn patch deterministically to committed state."""

    def apply(
        self,
        current: DatePlanningTaskState,
        patch: DatePlanPatch,
        *,
        mutation: DatePlanMutation = DatePlanMutation.NONE,
    ) -> DatePlanningTaskState:
        updates: dict[str, Any] = {}
        trip_window_changed = any(
            value is not None and value != getattr(current, field)
            for field, value in (
                ("date", patch.date),
                ("end_date", patch.end_date),
                ("day_count", patch.day_count),
            )
        )
        for field in _SCALAR_FIELDS:
            value = getattr(patch, field)
            if value is not None:
                updates[field] = value
        if patch.target_day is None and trip_window_changed:
            updates["target_day"] = None

        target_is_dining = _replacement_target_is_dining(current, patch.replace_place_names)
        replace_dining = (
            mutation in {DatePlanMutation.REPLACE, DatePlanMutation.REPLAN}
            and bool(patch.dining_keywords)
            and target_is_dining is not False
        )
        replace_activity = (
            mutation in {DatePlanMutation.REPLACE, DatePlanMutation.REPLAN}
            and bool(patch.activity_keywords)
            and target_is_dining is not True
        )
        for field in _LIST_FIELDS:
            values = list(getattr(patch, field))
            if values:
                replace = (field == "dining_keywords" and replace_dining) or (
                    field == "activity_keywords" and replace_activity
                )
                updates[field] = (
                    _unique(values)
                    if replace
                    else _unique([*getattr(current, field), *values])
                )
        if patch.meal_keywords:
            updates["meal_keywords"] = _merge_meal_keywords(
                current.meal_keywords,
                patch.meal_keywords,
                replace=replace_dining,
            )

        candidate = current.model_copy(update=updates)
        candidate = _normalize_temporal_state(candidate)
        date_changed = (
            candidate.city != current.city
            or candidate.date != current.date
            or candidate.end_date != current.end_date
            or candidate.day_count != current.day_count
            or candidate.start_time != current.start_time
        )
        return candidate.model_copy(
            update={
                "weather": None if date_changed else current.weather,
                "weather_forecasts": [] if date_changed else current.weather_forecasts,
            }
        )


def _normalize_temporal_state(state: DatePlanningTaskState) -> DatePlanningTaskState:
    date = state.date
    end_date = state.end_date
    day_count = state.day_count
    if date is not None and day_count is not None and day_count > 1:
        end_date = date + timedelta(days=day_count - 1)
    elif date is not None and day_count == 1:
        end_date = date
    elif date is not None and end_date is not None:
        if end_date < date:
            end_date = None
        else:
            day_count = (end_date - date).days + 1
    if day_count is not None:
        plan_mode = DatePlanMode.MULTI_DAY if day_count > 1 else DatePlanMode.SINGLE_DAY
        nights = state.nights if state.nights is not None else max(day_count - 1, 0)
        nights = max(nights, day_count - 1) if day_count > 1 else 0
        nights = min(nights, 4)
    else:
        plan_mode = state.plan_mode
        nights = state.nights
    return state.model_copy(
        update={
            "end_date": end_date,
            "day_count": day_count,
            "nights": nights,
            "plan_mode": plan_mode,
        }
    )


def _merge_meal_keywords(
    current: dict[str, list[str]],
    incoming: dict[str, list[str]],
    *,
    replace: bool,
) -> dict[str, list[str]]:
    if replace:
        return {key: _unique(values) for key, values in incoming.items()}
    merged = {key: _unique(values) for key, values in current.items()}
    reassigned = {value for values in incoming.values() for value in values}
    if reassigned:
        merged = {
            key: [value for value in values if value not in reassigned]
            for key, values in merged.items()
        }
    for key, values in incoming.items():
        merged[key] = _unique([*merged.get(key, []), *values])
    return {key: values for key, values in merged.items() if values}


def _replacement_target_is_dining(
    current: DatePlanningTaskState,
    place_names: list[str],
) -> bool | None:
    if current.current_plan is None or not place_names:
        return None
    targets = ["".join(name.casefold().split()) for name in place_names]
    for item in current.current_plan.items:
        place_name = "".join(item.place.name.casefold().split())
        if any(target in place_name or place_name in target for target in targets):
            return item.place.category in {PlaceCategory.RESTAURANT, PlaceCategory.CAFE}
    return None


def _unique(values: list[Any]) -> list[Any]:
    return list(dict.fromkeys(values))

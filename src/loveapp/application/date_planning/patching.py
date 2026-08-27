from datetime import timedelta
from typing import Any

from loveapp.domain.date_patch import DatePlanPatch
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.enums import DatePlanMode


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
    "replace_place_names",
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
    ) -> DatePlanningTaskState:
        updates: dict[str, Any] = {}
        for field in _SCALAR_FIELDS:
            value = getattr(patch, field)
            if value is not None:
                updates[field] = value
        for field in _LIST_FIELDS:
            values = list(getattr(patch, field))
            if values:
                updates[field] = _unique([*getattr(current, field), *values])
        if patch.meal_keywords:
            updates["meal_keywords"] = _merge_meal_keywords(
                current.meal_keywords,
                patch.meal_keywords,
            )

        candidate = current.model_copy(update=updates)
        return _normalize_temporal_state(candidate)


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
        nights = min(state.nights if state.nights is not None else max(day_count - 1, 0), 4)
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
) -> dict[str, list[str]]:
    merged = {key: _unique(values) for key, values in current.items()}
    for key, values in incoming.items():
        merged[key] = _unique([*merged.get(key, []), *values])
    return {key: values for key, values in merged.items() if values}


def _unique(values: list[Any]) -> list[Any]:
    return list(dict.fromkeys(values))

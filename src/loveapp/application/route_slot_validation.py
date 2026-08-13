import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any

from loveapp.domain.date_plan import MAX_TRIP_DAYS
from loveapp.domain.enums import BudgetScope, DatePlanMode, TransportMode
from loveapp.domain.memory import MessageRole
from loveapp.domain.routing import DatePlanSlots, RouteInput


@dataclass(frozen=True)
class SlotValidationResult:
    validated_slots: DatePlanSlots
    accepted_fields: dict[str, str]
    rejected_fields: dict[str, str]
    field_sources: dict[str, str]
    warnings: list[str]


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
_TEXT_ALIASES: dict[str, tuple[str, ...]] = {
    "日料": ("日料", "日本料理"),
    "日本料理": ("日本料理", "日料"),
    "西餐": ("西餐", "西式料理"),
    "电影院": ("电影院", "影院", "电影"),
    "影院": ("影院", "电影院", "电影"),
    "地铁": ("地铁", "轨道交通"),
    "公交": ("公交", "公共交通"),
}
_TRANSPORT_MARKERS: dict[TransportMode, tuple[str, ...]] = {
    TransportMode.WALKING: ("步行", "走路"),
    TransportMode.TRANSIT: ("地铁", "公交", "公共交通", "轨道交通"),
    TransportMode.DRIVING: ("开车", "驾车", "自驾"),
    TransportMode.CYCLING: ("骑行", "骑车", "自行车"),
}


def validate_route_slots(
    route_input: RouteInput,
    rule_slots: DatePlanSlots,
    llm_slots: DatePlanSlots,
    task_slots: DatePlanSlots | None = None,
) -> SlotValidationResult:
    """Validate each LLM slot independently against deterministic evidence."""

    task_slots = task_slots or DatePlanSlots()
    latest = _normalize_text(route_input.latest_query)
    historical_user_texts = [
        _normalize_text(message.content)
        for message in route_input.recent_messages
        if message.role == MessageRole.USER
    ]
    source_texts = [latest]
    if route_input.date_task_state is not None and route_input.date_task_state.is_resumable:
        source_texts.extend(historical_user_texts[-6:])

    accepted: dict[str, str] = {}
    rejected: dict[str, str] = {}
    sources: dict[str, str] = {}
    updates: dict[str, Any] = {}

    for field in _SCALAR_FIELDS:
        value = getattr(llm_slots, field)
        if not _has_value(value):
            continue
        rule_value = getattr(rule_slots, field)
        task_value = getattr(task_slots, field)
        if _values_equal(value, rule_value):
            updates[field] = value
            accepted[field] = _display(value)
            sources[field] = "rule"
            continue
        if _values_equal(value, task_value):
            updates[field] = value
            accepted[field] = _display(value)
            sources[field] = "task_state"
            continue
        if _scalar_has_evidence(field, value, source_texts, latest):
            updates[field] = value
            accepted[field] = _display(value)
            sources[field] = "llm_verified"
        else:
            rejected[field] = "no_source_evidence"

    for field in _LIST_FIELDS:
        values = list(getattr(llm_slots, field))
        if not values:
            continue
        rule_values = list(getattr(rule_slots, field))
        task_values = list(getattr(task_slots, field))
        valid: list[str] = []
        invalid: list[str] = []
        value_sources: list[str] = []
        for value in values:
            if value in rule_values:
                valid.append(value)
                value_sources.append("rule")
            elif field != "replace_place_names" and value in task_values:
                valid.append(value)
                value_sources.append("task_state")
            elif (
                _text_value_supported_exact(value, latest)
                if field == "replace_place_names"
                else _text_value_supported(value, source_texts)
            ):
                valid.append(value)
                value_sources.append("llm_verified")
            else:
                invalid.append(value)
        if valid:
            updates[field] = _unique(valid)
            accepted[field] = _display(valid)
            sources[field] = _combine_sources(value_sources)
        if invalid:
            rejected[field] = f"unsupported_values:{','.join(invalid)}"

    meal_keywords: dict[str, list[str]] = {}
    invalid_meals: list[str] = []
    meal_sources: list[str] = []
    for meal_type, values in llm_slots.meal_keywords.items():
        for value in values:
            if value in rule_slots.meal_keywords.get(meal_type, []):
                meal_keywords.setdefault(meal_type, []).append(value)
                meal_sources.append("rule")
            elif value in task_slots.meal_keywords.get(meal_type, []):
                meal_keywords.setdefault(meal_type, []).append(value)
                meal_sources.append("task_state")
            elif _text_value_supported(value, source_texts):
                meal_keywords.setdefault(meal_type, []).append(value)
                meal_sources.append("llm_verified")
            else:
                invalid_meals.append(f"{meal_type}:{value}")
    if meal_keywords:
        updates["meal_keywords"] = {
            meal_type: _unique(values) for meal_type, values in meal_keywords.items()
        }
        accepted["meal_keywords"] = _display(meal_keywords)
        sources["meal_keywords"] = _combine_sources(meal_sources)
    if invalid_meals:
        rejected["meal_keywords"] = f"unsupported_values:{','.join(invalid_meals)}"

    validated = DatePlanSlots(**updates)
    return SlotValidationResult(
        validated_slots=validated,
        accepted_fields=accepted,
        rejected_fields=rejected,
        field_sources=sources,
        warnings=[
            f"dropped {field}: {reason}" for field, reason in rejected.items()
        ],
    )


def merge_route_slot_sources(
    rule_slots: DatePlanSlots,
    validated_llm_slots: DatePlanSlots,
    task_slots: DatePlanSlots | None = None,
) -> tuple[DatePlanSlots, dict[str, str], dict[str, str]]:
    """Merge rule, verified LLM, and task values in explicit priority order."""

    task_slots = task_slots or DatePlanSlots()
    updates: dict[str, Any] = {}
    accepted: dict[str, str] = {}
    sources: dict[str, str] = {}

    for field in _SCALAR_FIELDS:
        rule_value = getattr(rule_slots, field)
        llm_value = getattr(validated_llm_slots, field)
        task_value = getattr(task_slots, field)
        if _has_value(rule_value):
            value, source = rule_value, "rule"
        elif _has_value(llm_value):
            value = llm_value
            source = "task_state" if _values_equal(llm_value, task_value) else "llm_verified"
        elif _has_value(task_value):
            value, source = task_value, "task_state"
        else:
            continue
        updates[field] = value
        accepted[field] = _display(value)
        sources[field] = source

    for field in _LIST_FIELDS:
        rule_values = list(getattr(rule_slots, field))
        llm_values = list(getattr(validated_llm_slots, field))
        task_values = list(getattr(task_slots, field))
        values = _unique([*rule_values, *llm_values, *task_values])
        if not values:
            continue
        updates[field] = values
        accepted[field] = _display(values)
        field_sources: list[str] = []
        if rule_values:
            field_sources.append("rule")
        if any(value not in rule_values and value not in task_values for value in llm_values):
            field_sources.append("llm_verified")
        if task_values or (llm_values and all(value in task_values for value in llm_values)):
            field_sources.append("task_state")
        sources[field] = _combine_sources(field_sources)

    merged_meals: dict[str, list[str]] = {}
    for slots in (task_slots, validated_llm_slots, rule_slots):
        for meal_type, values in slots.meal_keywords.items():
            merged_meals[meal_type] = _unique([*merged_meals.get(meal_type, []), *values])
    if merged_meals:
        updates["meal_keywords"] = merged_meals
        accepted["meal_keywords"] = _display(merged_meals)
        meal_sources: list[str] = []
        if rule_slots.meal_keywords:
            meal_sources.append("rule")
        llm_meals = {
            (meal_type, value)
            for meal_type, values in validated_llm_slots.meal_keywords.items()
            for value in values
        }
        task_meals = {
            (meal_type, value)
            for meal_type, values in task_slots.meal_keywords.items()
            for value in values
        }
        rule_meals = {
            (meal_type, value)
            for meal_type, values in rule_slots.meal_keywords.items()
            for value in values
        }
        if llm_meals - rule_meals - task_meals:
            meal_sources.append("llm_verified")
        if task_meals or (llm_meals and llm_meals <= task_meals):
            meal_sources.append("task_state")
        sources["meal_keywords"] = _combine_sources(meal_sources)

    merged = DatePlanSlots(**updates)
    merged, accepted, sources = _normalize_temporal_range(merged, accepted, sources)
    return merged, accepted, sources


def _scalar_has_evidence(
    field: str,
    value: Any,
    source_texts: list[str],
    latest: str,
) -> bool:
    if field in {"city", "area"}:
        return any(_location_value_supported(str(value), text) for text in source_texts)
    if field == "budget":
        return any(_budget_value_supported(value, text) for text in source_texts)
    if field == "budget_scope":
        if value == BudgetScope.PER_DAY:
            return any(marker in text for text in source_texts for marker in ("每天", "每日"))
        return any(marker in text for text in source_texts for marker in ("总预算", "总共", "一共"))
    if field == "transport_mode" and isinstance(value, TransportMode):
        return any(marker in text for text in source_texts for marker in _TRANSPORT_MARKERS[value])
    if field == "plan_mode":
        if value == DatePlanMode.MULTI_DAY:
            return bool(re.search(r"\d+\s*天|[两二三四五六七]\s*天|到周", latest))
        return any(marker in latest for marker in ("一天", "当天", "单日"))
    # Temporal and count fields are accepted only when the deterministic
    # parser or the active task state produced the same normalized value.
    if field in {
        "date",
        "end_date",
        "day_count",
        "nights",
        "target_day",
        "start_time",
    }:
        return False
    return False


def _text_value_supported(value: str, source_texts: list[str]) -> bool:
    normalized = _normalize_text(value)
    aliases = _TEXT_ALIASES.get(normalized, (normalized,))
    return any(alias and alias in text for text in source_texts for alias in aliases)


def _text_value_supported_exact(value: str, source_text: str) -> bool:
    normalized = _normalize_text(value)
    return bool(normalized) and normalized in source_text


def _budget_value_supported(value: Any, text: str) -> bool:
    amount = re.escape(str(value))
    bounded_amount = rf"(?<!\d){amount}(?!\d)"
    clauses = re.split(r"[，,。！？!?；;、\n]", text)
    return any(
        re.search(
            rf"(?:预算|总共|一共|每天|每日|每人|人均|花费|控制在)"
            rf"[^，,。！？!?；;、\n]{{0,10}}{bounded_amount}"
            rf"|{bounded_amount}(?:元|块|人民币)",
            clause,
        )
        is not None
        for clause in clauses
    )


def _location_value_supported(value: str, text: str) -> bool:
    candidate = _normalize_text(value)
    if len(candidate) < 2:
        return False
    for match in re.finditer(re.escape(candidate), text):
        start, end = match.span()
        before = text[start - 1] if start else ""
        after = text[end] if end < len(text) else ""
        before_is_cjk = _is_cjk(before)
        after_is_cjk = _is_cjk(after)
        if not before_is_cjk and not after_is_cjk:
            return True
        if not before_is_cjk and start == 0:
            return True
        prefix = text[max(0, start - 4) : start]
        if any(
            prefix.endswith(marker)
            for marker in (
                "在",
                "去",
                "到",
                "是",
                "为",
                "于",
                "住在",
                "前往",
                "城市",
                "地点",
                "区域",
            )
        ):
            return True
        if not before_is_cjk and after in {"市", "区", "县", "镇", "省"}:
            return True
    return False


def _is_cjk(value: str) -> bool:
    return bool(value) and "\u4e00" <= value <= "\u9fff"


def _normalize_temporal_range(
    slots: DatePlanSlots,
    accepted: dict[str, str],
    sources: dict[str, str],
) -> tuple[DatePlanSlots, dict[str, str], dict[str, str]]:
    updates: dict[str, Any] = {}
    start_date = slots.date
    end_date = slots.end_date
    day_count = slots.day_count
    target_day = slots.target_day

    if day_count is None and slots.nights is not None and slots.nights > 0:
        day_count = min(slots.nights + 1, MAX_TRIP_DAYS)
        updates["day_count"] = day_count
        _record_derived_field("day_count", day_count, accepted, sources, "nights")

    if target_day is not None and (day_count is None or target_day > day_count):
        day_count = min(target_day, MAX_TRIP_DAYS)
        updates["day_count"] = day_count
        _record_derived_field("day_count", day_count, accepted, sources, "target_day")

    if start_date is not None and end_date is not None:
        range_days = (end_date - start_date).days + 1
        if range_days < 1:
            if day_count is not None and day_count > 1:
                end_date = start_date + timedelta(days=day_count - 1)
                updates["end_date"] = end_date
                _record_derived_field("end_date", end_date, accepted, sources, "date", "day_count")
            else:
                end_date = None
                updates["end_date"] = None
                accepted.pop("end_date", None)
                sources.pop("end_date", None)
        elif day_count is None or _source_priority(sources.get("end_date")) > _source_priority(
            sources.get("day_count")
        ):
            day_count = min(range_days, MAX_TRIP_DAYS)
            end_date = start_date + timedelta(days=day_count - 1)
            updates.update({"day_count": day_count, "end_date": end_date})
            _record_derived_field("day_count", day_count, accepted, sources, "date", "end_date")
            _record_derived_field("end_date", end_date, accepted, sources, "date", "end_date")
        else:
            end_date = start_date + timedelta(days=day_count - 1)
            updates["end_date"] = end_date
            _record_derived_field("end_date", end_date, accepted, sources, "date", "day_count")
    elif start_date is not None and day_count is not None and day_count > 1:
        end_date = start_date + timedelta(days=day_count - 1)
        updates["end_date"] = end_date
        _record_derived_field("end_date", end_date, accepted, sources, "date", "day_count")

    max_consistent_nights = max(day_count - 1, 0) if day_count is not None else None
    if max_consistent_nights is not None and (
        slots.nights is None or slots.nights > max_consistent_nights
    ):
        updates["nights"] = max_consistent_nights
        _record_derived_field(
            "nights",
            max_consistent_nights,
            accepted,
            sources,
            "day_count",
        )

    effective_end = updates.get("end_date", end_date)
    effective_days = updates.get("day_count", day_count)
    if (effective_days or 0) > 1 or effective_end is not None or (target_day or 0) > 1:
        if slots.plan_mode != DatePlanMode.MULTI_DAY:
            updates["plan_mode"] = DatePlanMode.MULTI_DAY
            _record_derived_field(
                "plan_mode",
                DatePlanMode.MULTI_DAY,
                accepted,
                sources,
                "day_count",
                "end_date",
                "target_day",
            )
    elif slots.plan_mode is None and start_date is not None:
        updates["plan_mode"] = DatePlanMode.SINGLE_DAY
        _record_derived_field("plan_mode", DatePlanMode.SINGLE_DAY, accepted, sources, "date")

    return slots.model_copy(update=updates), accepted, sources


def _record_derived_field(
    field: str,
    value: Any,
    accepted: dict[str, str],
    sources: dict[str, str],
    *dependencies: str,
) -> None:
    accepted[field] = _display(value)
    dependency_sources = [sources[name] for name in dependencies if name in sources]
    sources[field] = (
        "derived_from:" + "+".join(dict.fromkeys(dependency_sources))
        if dependency_sources
        else "derived"
    )


def _source_priority(source: str | None) -> int:
    if source is None:
        return 0
    if "rule" in source:
        return 3
    if "llm_verified" in source:
        return 2
    if "task_state" in source:
        return 1
    return 0


def _combine_sources(values: list[str]) -> str:
    unique = list(dict.fromkeys(values))
    if not unique:
        return "unknown"
    return unique[0] if len(unique) == 1 else "mixed:" + "+".join(unique)


def _normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().replace(" ", "")


def _values_equal(first: Any, second: Any) -> bool:
    return _has_value(first) and first == second


def _has_value(value: Any) -> bool:
    return value is not None and value != [] and value != {}


def _display(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return ";".join(f"{key}={','.join(map(str, values))}" for key, values in value.items())
    if isinstance(value, list):
        return ",".join(map(str, value))
    return str(value)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))

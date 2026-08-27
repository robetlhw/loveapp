import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from loveapp.application.date_planning.location import resolve_date_location
from loveapp.domain.date_patch import DatePlanPatch, SlotSource
from loveapp.domain.date_plan import MAX_TRIP_DAYS
from loveapp.domain.enums import BudgetScope, DatePlanMode, TaskType, TransportMode
from loveapp.domain.runtime_context import RuntimeContext


class BudgetUpdateKind(StrEnum):
    SET = "set"
    UPDATE = "update"
    INCREASE = "increase"
    DECREASE = "decrease"
    UPPER_BOUND = "upper_bound"


@dataclass(frozen=True)
class DateFactParseResult:
    patch: DatePlanPatch
    budget_update_kind: BudgetUpdateKind | None = None
    matched_spans: tuple[str, ...] = ()


@dataclass(frozen=True)
class _BudgetFact:
    value: int
    scope: BudgetScope
    update_kind: BudgetUpdateKind
    span: str


class DateFactParser:
    """Extract deterministic date-planning facts from the current text."""

    def parse(
        self,
        text: str,
        runtime_context: RuntimeContext | None = None,
    ) -> DatePlanPatch:
        return self.parse_detailed(text, runtime_context).patch

    def parse_detailed(
        self,
        text: str,
        runtime_context: RuntimeContext | None = None,
    ) -> DateFactParseResult:
        normalized = _normalize(text)
        budget_fact = _extract_budget(normalized, runtime_context)
        location = resolve_date_location(normalized)
        start_date, end_date, day_count, nights, plan_mode = _extract_date_window(normalized)
        target_day = _extract_target_day(normalized)
        if target_day is not None:
            plan_mode = DatePlanMode.MULTI_DAY if target_day > 1 else plan_mode
        start_time = _extract_start_time(normalized, start_date)
        transport_mode = next(
            (
                mode
                for mode, markers in _TRANSPORT_MARKERS.items()
                if any(marker in normalized for marker in markers)
            ),
            None,
        )

        values = {
            "city": location.city,
            "area": location.area,
            "plan_mode": plan_mode,
            "date": start_date,
            "end_date": end_date,
            "day_count": day_count,
            "nights": nights,
            "target_day": target_day,
            "start_time": start_time,
            "budget": budget_fact.value if budget_fact else None,
            "budget_scope": budget_fact.scope if budget_fact else None,
            "transport_mode": transport_mode,
        }
        source_by_field = {
            field: SlotSource.RULE
            for field, value in values.items()
            if value is not None
        }
        spans = (budget_fact.span,) if budget_fact else ()
        return DateFactParseResult(
            patch=DatePlanPatch(**values, source_by_field=source_by_field),
            budget_update_kind=(budget_fact.update_kind if budget_fact else None),
            matched_spans=spans,
        )


_TRANSPORT_MARKERS = {
    TransportMode.WALKING: ("步行", "走路"),
    TransportMode.TRANSIT: ("地铁", "公交", "公共交通", "轨道交通"),
    TransportMode.DRIVING: ("开车", "驾车", "自驾"),
    TransportMode.CYCLING: ("骑行", "骑车", "自行车"),
}

_DAILY_BUDGET_PATTERNS = (
    re.compile(
        r"(?:每天|每日|一天)\s*(?:的)?\s*(?:预算)?\s*"
        r"(?:还是|仍然是|依然是|仍是|是|为|就(?:定)?|改(?:为|成|到)|调整到|"
        r"控制在|最多(?:是)?|不超过|:)?\s*"
        r"(?P<value>\d{2,6})\s*(?:元|块)?"
    ),
    re.compile(
        r"预算\s*(?:是|为)?\s*(?:每天|每日|一天)\s*"
        r"(?P<value>\d{2,6})\s*(?:元|块)?"
    ),
)
_BUDGET_PATTERNS: tuple[tuple[BudgetUpdateKind, re.Pattern[str]], ...] = (
    (
        BudgetUpdateKind.INCREASE,
        re.compile(
            r"(?:总预算|预算)\s*(?:提高|增加|上调)\s*(?:到|至|为)?\s*"
            r"(?P<value>\d{2,6})\s*(?:元|块)?"
        ),
    ),
    (
        BudgetUpdateKind.DECREASE,
        re.compile(
            r"(?:总预算|预算)\s*(?:降低|下降|下调|降)\s*(?:到|至|为)?\s*"
            r"(?P<value>\d{2,6})\s*(?:元|块)?"
        ),
    ),
    (
        BudgetUpdateKind.UPPER_BOUND,
        re.compile(
            r"(?:总预算|预算)\s*(?:控制在|最多(?:是)?|上限(?:是|为)?|不超过)\s*"
            r"(?P<value>\d{2,6})\s*(?:元|块)?"
        ),
    ),
    (
        BudgetUpdateKind.UPDATE,
        re.compile(
            r"(?:总预算|预算)\s*(?:改(?:为|成|到)|调整(?:到|为)|设(?:为|成)|定为)\s*"
            r"(?P<value>\d{2,6})\s*(?:元|块)?"
        ),
    ),
    (
        BudgetUpdateKind.SET,
        re.compile(
            r"(?:总预算|预算|总共|总价)\s*"
            r"(?:还是|仍然是|依然是|仍是|是|为|就(?:定)?|定在|:)?\s*"
            r"(?P<value>\d{2,6})\s*(?:元|块)?(?:以内|左右)?(?:吧)?"
        ),
    ),
)


def _extract_budget(
    text: str,
    runtime_context: RuntimeContext | None,
) -> _BudgetFact | None:
    for pattern in _DAILY_BUDGET_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            return _BudgetFact(
                value=int(match.group("value")),
                scope=BudgetScope.PER_DAY,
                update_kind=_budget_update_kind(match.group(0)),
                span=match.group(0),
            )
    for update_kind, pattern in _BUDGET_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            return _BudgetFact(
                value=int(match.group("value")),
                scope=BudgetScope.TOTAL,
                update_kind=update_kind,
                span=match.group(0),
            )

    # Preserve the established shorthand "300 元" while a date task is active,
    # and for a query that otherwise clearly describes a date plan.
    bare = re.search(r"(?<!\d)(?P<value>\d{2,6})\s*(?:元|块)(?:以内|左右)?", text)
    has_date_context = bool(
        runtime_context is not None
        and runtime_context.active_task == TaskType.DATE_PLANNING
    ) or any(marker in text for marker in ("约会", "行程", "餐厅", "活动", "旅行", "旅游"))
    if bare is not None and has_date_context:
        return _BudgetFact(
            value=int(bare.group("value")),
            scope=BudgetScope.TOTAL,
            update_kind=BudgetUpdateKind.SET,
            span=bare.group(0),
        )
    return None


def _budget_update_kind(span: str) -> BudgetUpdateKind:
    if re.search(r"提高|增加|上调", span):
        return BudgetUpdateKind.INCREASE
    if re.search(r"降低|下降|下调|降到", span):
        return BudgetUpdateKind.DECREASE
    if re.search(r"控制在|最多|上限|不超过", span):
        return BudgetUpdateKind.UPPER_BOUND
    if re.search(r"改|调整|设为|设成|定为", span):
        return BudgetUpdateKind.UPDATE
    return BudgetUpdateKind.SET


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()


def extract_requested_day_count(text: str) -> int | None:
    match = re.search(
        r"([0-9]{1,2}|[一二三四五六七八九十两]{1,3})\s*"
        r"(?:天(?!后)|日(?=游|行程|旅行|旅游))",
        text,
    )
    return _parse_small_number(match.group(1)) if match else None


def _extract_date_window(
    text: str,
) -> tuple[date | None, date | None, int | None, int | None, DatePlanMode | None]:
    start_date, end_date = _extract_explicit_date_range(text)
    duration_text = re.sub(
        r"第\s*(?:[0-9]{1,2}|[一二三四五六七八九十两]{1,3})\s*天",
        "",
        text,
    )
    requested_days = extract_requested_day_count(duration_text)
    requested_nights = _extract_requested_nights(text)

    if start_date is None:
        start_date = _extract_date(text)

    if start_date is not None and end_date is not None:
        day_count = min((end_date - start_date).days + 1, MAX_TRIP_DAYS)
        end_date = start_date + timedelta(days=day_count - 1)
    elif requested_days is not None:
        day_count = min(requested_days, MAX_TRIP_DAYS)
        if start_date is not None and day_count > 1:
            end_date = start_date + timedelta(days=day_count - 1)
    elif any(marker in text for marker in ("多日", "多天", "几天", "几日")):
        day_count = None
    elif start_date is not None:
        day_count = 1
    else:
        day_count = None

    if day_count is not None:
        nights = min(
            requested_nights if requested_nights is not None else max(day_count - 1, 0),
            MAX_TRIP_DAYS - 1,
        )
    else:
        nights = requested_nights
    multi_day = (
        (day_count or 0) > 1
        or end_date is not None
        or any(marker in text for marker in ("多日", "多天", "几天", "几日"))
    )
    plan_mode = (
        DatePlanMode.MULTI_DAY
        if multi_day
        else DatePlanMode.SINGLE_DAY
        if start_date is not None or requested_days == 1
        else None
    )
    return start_date, end_date, day_count, nights, plan_mode


def _extract_explicit_date_range(text: str) -> tuple[date | None, date | None]:
    today = date.today()
    relative = re.search(
        r"(今天|明天|后天|大后天)\s*(?:到|至|[-~～—])\s*(今天|明天|后天|大后天)",
        text,
    )
    if relative:
        offsets = {"今天": 0, "明天": 1, "后天": 2, "大后天": 3}
        start = today + timedelta(days=offsets[relative.group(1)])
        end = today + timedelta(days=offsets[relative.group(2)])
        return (start, end) if end >= start else (None, None)

    weekday = re.search(
        r"(?P<prefix>下周|下星期)?(?:周|星期)(?P<start>[一二三四五六日天])\s*"
        r"(?:到|至|[-~～—])\s*"
        r"(?:(?P<end_prefix>下周|下星期)?(?:周|星期))?"
        r"(?P<end>[一二三四五六日天])",
        text,
    )
    if weekday:
        values = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
        start_weekday = values[weekday.group("start")]
        end_weekday = values[weekday.group("end")]
        start = _next_weekday(
            today,
            start_weekday,
            force_next_week=weekday.group("prefix") is not None,
        )
        return start, start + timedelta(days=(end_weekday - start_weekday) % 7)

    full_dates = [
        _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        for match in re.finditer(r"(?<!\d)(20\d{2})[-年](\d{1,2})[-月](\d{1,2})日?(?!\d)", text)
    ]
    valid_full_dates = [value for value in full_dates if value is not None]
    if len(valid_full_dates) >= 2:
        start, end = valid_full_dates[:2]
        return (start, end) if end >= start else (None, None)

    shorthand = re.search(
        r"(\d{1,2})月(\d{1,2})日?\s*(?:到|至|[-~～—])\s*(\d{1,2})日",
        text,
    )
    if shorthand:
        start = _safe_date(today.year, int(shorthand.group(1)), int(shorthand.group(2)))
        end = _safe_date(today.year, int(shorthand.group(1)), int(shorthand.group(3)))
        if start is not None and end is not None and end >= start:
            return start, end

    month_dates = [
        _safe_date(today.year, int(match.group(1)), int(match.group(2)))
        for match in re.finditer(r"(?<!\d)(\d{1,2})月(\d{1,2})日?", text)
    ]
    valid_month_dates = [value for value in month_dates if value is not None]
    if len(valid_month_dates) >= 2:
        start, end = valid_month_dates[:2]
        if end < start:
            end = _safe_date(start.year + 1, end.month, end.day) or end
        return start, end
    return None, None


def _next_weekday(today: date, weekday: int, *, force_next_week: bool) -> date:
    if force_next_week:
        return today + timedelta(days=7 - today.weekday() + weekday)
    delta = (weekday - today.weekday()) % 7
    return today + timedelta(days=delta or 7)


def _extract_requested_nights(text: str) -> int | None:
    match = re.search(r"([0-9]{1,2}|[一二三四五六七八九十两]{1,3})\s*(?:夜|晚)", text)
    return _parse_small_number(match.group(1)) if match else None


def _parse_small_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    normalized = value.replace("两", "二")
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if normalized == "十":
        return 10
    if "十" in normalized:
        tens, ones = normalized.split("十", maxsplit=1)
        return (digits.get(tens, 1) * 10) + digits.get(ones, 0)
    return digits.get(normalized)


def _extract_target_day(text: str) -> int | None:
    match = re.search(r"第\s*([0-9]{1,2}|[一二三四五六七八九十两]{1,3})\s*天", text)
    if match:
        value = _parse_small_number(match.group(1))
        return min(value, MAX_TRIP_DAYS) if value else None
    if "首日" in text or "第一日" in text:
        return 1
    return None


def _extract_date(text: str) -> date | None:
    iso = re.search(r"\b(20\d{2})[-年](\d{1,2})[-月](\d{1,2})日?\b", text)
    if iso:
        return _safe_date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    month_day = re.search(r"(\d{1,2})月(\d{1,2})日?", text)
    if month_day:
        today = date.today()
        return _safe_date(today.year, int(month_day.group(1)), int(month_day.group(2)))
    today = date.today()
    if "大后天" in text:
        return today + timedelta(days=3)
    if "后天" in text:
        return today + timedelta(days=2)
    if "明天" in text:
        return today + timedelta(days=1)
    if "今天" in text:
        return today
    if "下周" in text:
        weekday = _weekday_in_text(text)
        return today + timedelta(days=7 - today.weekday() + (weekday if weekday is not None else 5))
    if "周末" in text:
        return today + timedelta(days=(5 - today.weekday()) % 7)
    weekday = _weekday_in_text(text)
    if weekday is not None:
        delta = (weekday - today.weekday()) % 7
        return today + timedelta(days=delta or 7)
    return None


def _weekday_in_text(text: str) -> int | None:
    match = re.search(r"(?:周|星期)([一二三四五六日天])", text)
    if not match:
        return None
    return {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}[match.group(1)]


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _extract_start_time(text: str, planned_date: date | None) -> datetime | None:
    if planned_date is None:
        return None
    match = re.search(r"(上午|下午|晚上|中午)?\s*(\d{1,2})(?:点|时|:)(\d{1,2})?", text)
    if not match:
        return None
    period, hour_text, minute_text = match.groups()
    hour = int(hour_text)
    minute = int(minute_text or 0)
    if period in {"下午", "晚上"} and hour < 12:
        hour += 12
    if period == "中午" and hour < 11:
        hour += 12
    if hour > 23 or minute > 59:
        return None
    return datetime(planned_date.year, planned_date.month, planned_date.day, hour, minute)

import re
import unicodedata
from dataclasses import dataclass

from loveapp.domain.date_operations import (
    DateOperationType,
    DatePlanOperation,
    DateReplacementPreference,
    DateSemanticParseResult,
    DesiredDateStop,
    MealType,
    StopKind,
    StopReference,
)
from loveapp.domain.date_plan import DatePlan, DatePlanItem
from loveapp.domain.enums import PlaceCategory


@dataclass(frozen=True)
class DateModificationDetection:
    reasons: tuple[str, ...]
    has_plan_reference: bool
    requests_replacement: bool

    @property
    def is_candidate(self) -> bool:
        return bool(self.reasons)


def detect_date_modification(
    text: str,
    current_plan: DatePlan | None,
) -> DateModificationDetection:
    """Detect modification semantics without authorizing a plan mutation."""

    normalized = _normalize(text)
    reference = _reference_candidates(normalized, current_plan)
    has_plan_reference = reference.recognized
    explicit_replacement = _GENERIC_REPLACEMENT_CUE.search(normalized) is not None
    dissatisfaction = _DISSATISFACTION_CUE.search(normalized) is not None
    alternative_request = _ALTERNATIVE_REQUEST_CUE.search(normalized) is not None
    requests_replacement = explicit_replacement or (dissatisfaction and alternative_request)

    reasons: list[str] = []
    if has_plan_reference:
        reasons.append("current_plan_reference")
    if explicit_replacement:
        reasons.append("generic_replacement")
    if dissatisfaction:
        reasons.append("plan_item_dissatisfaction")
    if alternative_request:
        reasons.append("alternative_request")
    if not (explicit_replacement or dissatisfaction) or (
        not has_plan_reference and not explicit_replacement
    ):
        reasons = []
    return DateModificationDetection(
        reasons=tuple(dict.fromkeys(reasons)),
        has_plan_reference=has_plan_reference,
        requests_replacement=requests_replacement,
    )


def looks_like_date_modification_semantics(text: str) -> bool:
    normalized = _normalize(text)
    return _GENERIC_REPLACEMENT_CUE.search(normalized) is not None or (
        _DISSATISFACTION_CUE.search(normalized) is not None
        and _ALTERNATIVE_REQUEST_CUE.search(normalized) is not None
    )


def interpret_date_modification(
    text: str,
    current_plan: DatePlan | None,
) -> DateSemanticParseResult:
    """Interpret only high-confidence, plan-aware generic replacements."""

    detection = detect_date_modification(text, current_plan)
    if not detection.is_candidate or current_plan is None:
        return DateSemanticParseResult()
    if not detection.requests_replacement:
        return DateSemanticParseResult()

    candidates = _reference_candidates(_normalize(text), current_plan)
    if candidates.recognized and len(candidates.items) != 1:
        unresolved = [item.place.name for item in candidates.items]
        if not unresolved:
            unresolved = ["当前行程中的目标节点"]
        return DateSemanticParseResult(unresolved_references=unresolved)
    if len(candidates.items) != 1:
        return DateSemanticParseResult()

    item = candidates.items[0]
    preferences = (
        [DateReplacementPreference.NEARBY]
        if _NEARBY_PREFERENCE_CUE.search(_normalize(text)) is not None
        else []
    )
    return DateSemanticParseResult(
        operations=[
            DatePlanOperation(
                type=DateOperationType.REPLACE_STOP,
                target=_reference_for_item(item, candidates.ordinal),
                payload=DesiredDateStop(
                    kind=_kind_for_item(item),
                    generic_replacement=True,
                    replacement_preferences=preferences,
                    target_day=item.day_index if current_plan.day_count > 1 else None,
                ),
                source_span=text,
                confidence=1,
            )
        ]
    )


@dataclass(frozen=True)
class _ReferenceCandidates:
    items: tuple[DatePlanItem, ...]
    recognized: bool
    ordinal: int | None = None


def _reference_candidates(
    text: str,
    current_plan: DatePlan | None,
) -> _ReferenceCandidates:
    if current_plan is None or not current_plan.items:
        return _ReferenceCandidates((), False)
    ordered = sorted(current_plan.items, key=lambda item: (item.day_index, item.order))

    ordinal_match = _ORDINAL_REFERENCE.search(text)
    if ordinal_match is not None:
        ordinal = _ordinal_value(ordinal_match.group("ordinal"))
        matches = (ordered[ordinal - 1],) if ordinal is not None and ordinal <= len(ordered) else ()
        return _ReferenceCandidates(matches, True, ordinal)

    name_matches = tuple(
        item
        for item in ordered
        if len(_normalize(item.place.name)) >= 2 and _normalize(item.place.name) in text
    )
    if name_matches:
        return _ReferenceCandidates(name_matches, True)

    meal_type = _referenced_meal_type(text)
    if meal_type is not None:
        return _ReferenceCandidates(
            tuple(item for item in ordered if item.meal_type == meal_type.value),
            True,
        )

    category = _referenced_category(text)
    if category is not None:
        return _ReferenceCandidates(
            tuple(item for item in ordered if _category_matches(item, category)),
            True,
        )

    keyword_matches = tuple(
        item
        for item in ordered
        if item.slot_keyword is not None
        and len(_normalize(item.slot_keyword)) >= 2
        and _normalize(item.slot_keyword) in text
    )
    if keyword_matches:
        return _ReferenceCandidates(keyword_matches, True)

    if _GENERIC_PLAN_REFERENCE.search(text) is not None:
        return _ReferenceCandidates(tuple(ordered), True)
    return _ReferenceCandidates((), False)


def _reference_for_item(item: DatePlanItem, ordinal: int | None) -> StopReference:
    if ordinal is not None:
        return StopReference(ordinal=ordinal)
    return StopReference(
        place_id=item.place.id,
        place_name=item.place.name,
        keyword=item.slot_keyword,
        meal_type=(
            MealType(item.meal_type) if item.meal_type in MealType._value2member_map_ else None
        ),
    )


def _kind_for_item(item: DatePlanItem) -> StopKind:
    return {
        PlaceCategory.RESTAURANT: StopKind.DINING,
        PlaceCategory.CAFE: StopKind.CAFE,
        PlaceCategory.ATTRACTION: StopKind.ACTIVITY,
        PlaceCategory.ENTERTAINMENT: StopKind.ACTIVITY,
    }[item.place.category]


def _referenced_meal_type(text: str) -> MealType | None:
    for meal_type, pattern in _MEAL_REFERENCES:
        if pattern.search(text) is not None:
            return meal_type
    return None


def _referenced_category(text: str) -> str | None:
    for category, pattern in _CATEGORY_REFERENCES:
        if pattern.search(text) is not None:
            return category
    return None


def _category_matches(item: DatePlanItem, category: str) -> bool:
    if category == "dining":
        return item.place.category in {PlaceCategory.RESTAURANT, PlaceCategory.CAFE}
    if category == "activity":
        return item.place.category in {PlaceCategory.ATTRACTION, PlaceCategory.ENTERTAINMENT}
    return True


def _ordinal_value(raw: str) -> int | None:
    if raw.isdigit():
        return int(raw)
    return {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}.get(raw)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold())


_ORDINAL_REFERENCE = re.compile(
    r"第(?P<ordinal>[一二三四五1-5])个(?:地方|地点|景点|活动|餐厅|饭店|节点)?"
)
_MEAL_REFERENCES = (
    (MealType.BREAKFAST, re.compile(r"(?:那个|这个|原来的)?(?:早餐|早饭)(?:餐厅|饭店)?")),
    (MealType.LUNCH, re.compile(r"(?:那个|这个|原来的)?(?:午餐|午饭)(?:餐厅|饭店)?")),
    (MealType.DINNER, re.compile(r"(?:那个|这个|原来的)?(?:晚餐|晚饭)(?:餐厅|饭店)?")),
)
_CATEGORY_REFERENCES = (
    ("dining", re.compile(r"(?:那个|这个|该|原来的)?(?:餐厅|饭店|餐馆|用餐地点)")),
    ("activity", re.compile(r"(?:那个|这个|该|原来的)?(?:景点|活动)")),
    ("place", re.compile(r"(?:那个|这个|该|原来的)(?:地方|地点|节点)")),
)
_GENERIC_PLAN_REFERENCE = re.compile(r"那个|这个|该节点|原来的|之前的")
_GENERIC_REPLACEMENT_CUE = re.compile(
    r"替换|更换|换掉|换一下|换一换|换成|换为|换一个|换一家|换个|"
    r"改一个|改一家|改一下|调整一下"
)
_DISSATISFACTION_CUE = re.compile(
    r"不想去|不喜欢|不太喜欢|太远|有点远|太贵|有点贵|不合适|不太合适|太晚|有点晚"
)
_ALTERNATIVE_REQUEST_CUE = re.compile(
    r"有没有.{0,8}(?:一点|一些|其他|别的)|换|替换|更换|改(?:一个|一家|成|为)"
)
_NEARBY_PREFERENCE_CUE = re.compile(r"近一点|近一些|附近|别太远|不要太远")

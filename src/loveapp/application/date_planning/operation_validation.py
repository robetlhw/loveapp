import re
import unicodedata
from dataclasses import dataclass

from loveapp.domain.date_operations import DateOperationType, DatePlanOperation, StopReference
from loveapp.domain.date_patch import DatePlanPatch
from loveapp.domain.enums import PlaceCategory
from loveapp.domain.runtime_context import RuntimeContext


@dataclass(frozen=True)
class RejectedDateOperation:
    operation: DatePlanOperation
    reason: str


@dataclass(frozen=True)
class DateOperationVerification:
    accepted: tuple[DatePlanOperation, ...]
    rejected: tuple[RejectedDateOperation, ...]


class DateOperationVerifier:
    """Authorize typed operations using current-turn evidence and trusted state."""

    def verify(
        self,
        operations: list[DatePlanOperation],
        text: str,
        runtime_context: RuntimeContext | None,
        current_turn_patch: DatePlanPatch,
    ) -> DateOperationVerification:
        accepted: list[DatePlanOperation] = []
        rejected: list[RejectedDateOperation] = []
        for operation in operations:
            reason = self._rejection_reason(
                operation,
                text,
                runtime_context,
                current_turn_patch,
            )
            if reason is None:
                accepted.append(operation)
            else:
                rejected.append(RejectedDateOperation(operation, reason))
        return DateOperationVerification(tuple(accepted), tuple(rejected))

    def _rejection_reason(
        self,
        operation: DatePlanOperation,
        text: str,
        runtime_context: RuntimeContext | None,
        patch: DatePlanPatch,
    ) -> str | None:
        normalized = _normalize(text)
        if (
            operation.source_span is not None
            and _normalize_source_evidence(operation.source_span)
            not in _normalize_source_evidence(text)
        ):
            return "source_span_not_in_current_turn"
        if operation.type == DateOperationType.UPDATE_CONSTRAINT:
            return _constraint_rejection(operation, patch)
        if operation.type == DateOperationType.REPLAN:
            return None if _REPLAN_CUE.search(normalized) else "replan_without_explicit_cue"
        if operation.type == DateOperationType.REMOVE_STOP and not _REMOVE_CUE.search(normalized):
            return "remove_without_explicit_cue"
        if operation.type == DateOperationType.REPLACE_STOP and not _REPLACE_CUE.search(normalized):
            return "replace_without_explicit_cue"
        if operation.type == DateOperationType.MOVE_STOP and not _MOVE_CUE.search(normalized):
            return "move_without_temporal_cue"
        if operation.target is not None and not _reference_has_evidence(
            operation.target,
            normalized,
            runtime_context,
        ):
            return "target_without_source_or_unique_context_evidence"
        if operation.payload is not None:
            payload_value = operation.payload.keyword or operation.payload.place_name
            if payload_value is not None and not _text_supports_value(payload_value, normalized):
                return "payload_without_current_turn_evidence"
        return None


def _constraint_rejection(
    operation: DatePlanOperation,
    patch: DatePlanPatch,
) -> str | None:
    field = operation.constraint_field
    if field is None:
        return "constraint_field_missing"
    patch_value = getattr(patch, field.value, None)
    if patch_value is None:
        return "constraint_not_in_deterministic_patch"
    if _comparable_value(patch_value) != _comparable_value(operation.constraint_value):
        return "constraint_conflicts_with_deterministic_patch"
    return None


def _reference_has_evidence(
    reference: StopReference,
    text: str,
    runtime_context: RuntimeContext | None,
) -> bool:
    reference_scope = _reference_scope(text)
    for value in (reference.place_name, reference.keyword):
        if value is not None and _text_supports_value(value, reference_scope):
            return True
    if reference.meal_type is not None and _MEAL_TEXT[reference.meal_type.value].search(
        reference_scope
    ):
        return True
    if reference.ordinal is not None and re.search(
        rf"第\s*(?:{reference.ordinal}|{_CHINESE_ORDINALS.get(reference.ordinal, '')})\s*个",
        reference_scope,
    ):
        return True
    plan = (
        runtime_context.active_date_plan.current_plan
        if runtime_context is not None and runtime_context.active_date_plan is not None
        else None
    )
    if plan is None:
        return False
    matches = [
        item
        for item in plan.items
        if (
            (reference.place_id is not None and item.place.id == reference.place_id)
            or (
                reference.place_name is not None
                and _text_supports_value(reference.place_name, _normalize(item.place.name))
            )
            or (
                reference.keyword is not None
                and _item_supports_keyword(item, reference.keyword)
            )
            or (
                reference.meal_type is not None
                and item.meal_type == reference.meal_type.value
            )
        )
    ]
    category_matches = _generic_category_matches(reference_scope, plan.items)
    if len(category_matches) == 1 and category_matches[0] in matches:
        return True
    has_contextual_reference = bool(
        re.search(
            r"那个|这个|该|原来的|原先的|之前的|第\s*[一二三四五六七八九十\d]+\s*个",
            reference_scope,
        )
    )
    return len(matches) == 1 and has_contextual_reference


def _reference_scope(text: str) -> str:
    replacement_cues = list(_REPLACE_CUE.finditer(text))
    return text[: replacement_cues[-1].start()] if replacement_cues else text


def _generic_category_matches(text: str, items: list) -> list:
    categories: set[PlaceCategory] = set()
    if re.search(r"餐厅|饭店|用餐", text):
        categories.add(PlaceCategory.RESTAURANT)
    if re.search(r"咖啡馆|咖啡店", text):
        categories.add(PlaceCategory.CAFE)
    if "景点" in text:
        categories.add(PlaceCategory.ATTRACTION)
    if "活动" in text:
        categories.update({PlaceCategory.ATTRACTION, PlaceCategory.ENTERTAINMENT})
    return [item for item in items if item.place.category in categories]


def _item_supports_keyword(item, keyword: str) -> bool:
    aliases = _VALUE_ALIASES.get(keyword, (keyword,))
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


def _text_supports_value(value: str, text: str) -> bool:
    aliases = _VALUE_ALIASES.get(value, (value,))
    return any(_normalize(alias) in text for alias in aliases)


def _comparable_value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold())


def _normalize_source_evidence(value: str) -> str:
    return re.sub(r"[，,。；;！？!?]+", "", _normalize(value))


_REPLAN_CUE = re.compile(r"重新.{0,4}(?:规划|安排)|换一套|全部重排|从头安排|重新来")
_REMOVE_CUE = re.compile(r"删除|删掉|去掉|移除|取消|不去|不要")
_REPLACE_CUE = re.compile(
    r"替换|更换|换掉|换一下|换一换|换成|换为|换一个|换一家|换个|改成|改为|"
    r"有没有.{0,8}(?:近一点|近一些|附近|其他|别的)"
)
_MOVE_CUE = re.compile(
    r"之前|之后|前|后|上午|中午|下午|晚上|早餐|午饭|午餐|晚饭|晚餐|调整顺序|放到"
)
_CHINESE_ORDINALS = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五"}
_MEAL_TEXT = {
    "breakfast": re.compile(r"早餐|早饭|早上"),
    "lunch": re.compile(r"午餐|午饭|中饭|中午"),
    "dinner": re.compile(r"晚餐|晚饭|晚上"),
}
_VALUE_ALIASES = {
    "电影": ("电影", "电影院", "影院"),
    "电影院": ("电影院", "电影", "影院"),
    "展览": ("展览", "展馆", "美术馆"),
    "日料": ("日料", "日本料理"),
    "西餐": ("西餐", "西式料理"),
}

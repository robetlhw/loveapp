import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from loveapp.domain.contextual_memory import is_contextual_update_target_compatible
from loveapp.domain.memory import (
    ContextualMemoryUpdate,
    ContextualUpdateType,
    MemoryItem,
    MessageRole,
    StoredMessage,
    utc_now,
)


@dataclass(frozen=True)
class ContextualMemoryUpdateResolution:
    target: MemoryItem | None = None
    update_type: ContextualUpdateType | None = None
    evidence_span: str | None = None
    temporal_expression: str | None = None
    duration_value: int | None = None
    duration_unit: str | None = None
    reason: str = "no_contextual_qualifier"
    candidate_ids: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return (
            self.target is not None
            and self.update_type is not None
            and self.evidence_span is not None
            and self.temporal_expression is not None
        )

    def to_update(self, *, reference_time=None) -> ContextualMemoryUpdate:
        if not self.resolved or self.target is None or self.update_type is None:
            raise ValueError("cannot build a contextual update from an unresolved resolution")
        return ContextualMemoryUpdate(
            target_memory_id=self.target.id,
            update_type=self.update_type,
            evidence_span=self.evidence_span or "",
            temporal_expression=self.temporal_expression or "",
            reference_time=reference_time or utc_now(),
            target_canonical_predicate=self.target.canonical_predicate or "",
            duration_value=self.duration_value,
            duration_unit=self.duration_unit,
            reason=self.reason,
        )


_DURATION_PATTERN = re.compile(
    r"(?P<prefix>持续(?:了)?|已经|都|足足|整整)"
    r"(?P<value>\d+|[一二三四五六七八九十两]+)(?:个)?"
    r"(?P<unit>天|周|星期|个月|月|年)(?:了)?"
)
_PERSISTENCE_PATTERN = re.compile(
    r"还是这样|一直(?:没|没有)?恢复|依然(?:这样|没有恢复)|一直如此|没什么变化"
)
_CONTACT_ANTECEDENT_PATTERN = re.compile(
    r"(?:回(?:复|我|消息)?|联系|聊天|交流|沟通|互动|理我|搭理).{0,16}"
    r"(?:慢|少|很少|不怎么|不太|几乎不|降低|下降|变少|越来越少)"
    r"|(?:很少|不怎么|不太|几乎不).{0,8}"
    r"(?:回(?:复|我|消息)?|联系|聊天|交流|沟通|互动|理我|搭理)"
)


def may_contain_contextual_memory_update(text: str) -> bool:
    normalized = _normalize(text)
    return bool(_DURATION_PATTERN.search(normalized) or _PERSISTENCE_PATTERN.search(normalized))


def resolve_contextual_memory_update(
    text: str,
    conversation_history: Iterable[StoredMessage],
    existing_memories: Iterable[MemoryItem],
) -> ContextualMemoryUpdateResolution:
    """Resolve a narrow temporal continuation against recent user evidence.

    A qualifier by itself never selects an arbitrary memory.  The antecedent
    must be a recent user-authored contact pattern and exactly one active,
    compatible memory must remain after local filtering.
    """

    normalized = _normalize(text)
    duration = _DURATION_PATTERN.search(normalized)
    persistence = _PERSISTENCE_PATTERN.search(normalized)
    if duration is None and persistence is None:
        return ContextualMemoryUpdateResolution()

    user_history = [
        message
        for message in conversation_history
        if message.role == MessageRole.USER and message.content.strip()
    ]
    antecedent_ids = {
        message.id
        for message in user_history
        if _CONTACT_ANTECEDENT_PATTERN.search(_normalize(message.content))
    }
    if not antecedent_ids:
        return ContextualMemoryUpdateResolution(reason="no_user_contact_antecedent")

    candidates = [
        item
        for item in existing_memories
        if is_contextual_update_target_compatible(item)
        and (
            item.source_message_id in antecedent_ids
            or _CONTACT_ANTECEDENT_PATTERN.search(_normalize(item.summary)) is not None
        )
    ]
    candidates.sort(
        key=lambda item: (
            item.source_message_id in antecedent_ids,
            item.updated_at,
        ),
        reverse=True,
    )
    candidate_ids = tuple(item.id for item in candidates[:8])
    if len(candidates) != 1:
        return ContextualMemoryUpdateResolution(
            reason="ambiguous_target" if candidates else "no_compatible_active_memory",
            candidate_ids=candidate_ids,
        )

    target = candidates[0]
    if duration is not None:
        value = _parse_chinese_number(duration.group("value"))
        if value is None:
            return ContextualMemoryUpdateResolution(
                reason="unsupported_duration_value",
                candidate_ids=candidate_ids,
            )
        unit = _normalize_duration_unit(duration.group("unit"))
        return ContextualMemoryUpdateResolution(
            target=target,
            update_type=ContextualUpdateType.DURATION,
            evidence_span=duration.group(0),
            temporal_expression=duration.group(0),
            duration_value=value,
            duration_unit=unit,
            reason="explicit_duration_for_recent_contact_pattern",
            candidate_ids=candidate_ids,
        )

    assert persistence is not None
    return ContextualMemoryUpdateResolution(
        target=target,
        update_type=ContextualUpdateType.PERSISTENCE,
        evidence_span=persistence.group(0),
        temporal_expression=persistence.group(0),
        reason="persistence_for_recent_contact_pattern",
        candidate_ids=candidate_ids,
    )


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()


def _normalize_duration_unit(value: str) -> str:
    return {"星期": "week", "周": "week", "个月": "month", "月": "month", "天": "day", "年": "year"}[value]


def _parse_chinese_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value in digits:
        return digits[value]
    if value == "十":
        return 10
    if "十" in value:
        before, _, after = value.partition("十")
        tens = digits.get(before, 1 if not before else 0)
        ones = digits.get(after, 0) if after else 0
        parsed = tens * 10 + ones
        return parsed or None
    return None

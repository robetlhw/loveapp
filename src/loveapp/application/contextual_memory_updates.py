import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from loveapp.domain.contextual_memory import is_contextual_update_target_compatible
from loveapp.domain.memory import (
    ContextualMemoryUpdate,
    ContextualUpdateType,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
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
    semantic_candidate_ids: tuple[str, ...] = ()
    compatible_candidate_ids: tuple[str, ...] = ()
    rejected_candidates: tuple[tuple[str, str], ...] = ()
    plural_reference: bool = False

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
_INTERACTION_DECLINE_MARKER = (
    r"(?:慢|少|很少|不怎么|不太|几乎不|降低|下降|减少|变少|越来越少)"
)
_WEAK_DECLINE_PREFIX = r"(?:很少|不怎么|不太|几乎不)"
_RESPONSE_TARGET = r"(?:回复|回(?:我|消息|信息|得)|回应|理我|搭理)"
_CONTACT_FREQUENCY_TARGET = r"(?:联系|聊天|交流|沟通|互动|见面|碰面)"
_RESPONSE_ANTECEDENT_PATTERN = re.compile(
    rf"{_RESPONSE_TARGET}.{{0,16}}{_INTERACTION_DECLINE_MARKER}"
    rf"|{_WEAK_DECLINE_PREFIX}.{{0,8}}{_RESPONSE_TARGET}"
)
_CONTACT_FREQUENCY_ANTECEDENT_PATTERN = re.compile(
    rf"{_CONTACT_FREQUENCY_TARGET}(?:次数|频率)?.{{0,16}}"
    rf"{_INTERACTION_DECLINE_MARKER}"
    rf"|{_WEAK_DECLINE_PREFIX}.{{0,8}}{_CONTACT_FREQUENCY_TARGET}"
)
_PLURAL_REFERENCE_PATTERN = re.compile(
    r"(?:这(?:两|几|多)种(?:情况|问题|变化|表现)|"
    r"这些(?:情况|问题|变化|表现)|(?:两|几|多)个(?:情况|问题|表现))"
)
_NON_PARTNER_REFERENCE_PATTERN = re.compile(
    r"(?:我|自己).{0,10}(?:工作|学习|身体|生活|项目|状态)"
)
_SEMANTIC_ANTECEDENT_KINDS = {
    MemoryKind.INTERACTION_PATTERN,
    MemoryKind.INTERACTION_EVENT,
    MemoryKind.RELATIONSHIP_STATE,
}
_CONTACT_FREQUENCY_PREDICATES = {
    "interaction.contact_frequency",
    "contact.status",
}


def may_contain_contextual_memory_update(text: str) -> bool:
    normalized = _normalize(text)
    return bool(_DURATION_PATTERN.search(normalized) or _PERSISTENCE_PATTERN.search(normalized))


def resolve_contextual_memory_update(
    text: str,
    conversation_history: Iterable[StoredMessage],
    existing_memories: Iterable[MemoryItem],
) -> ContextualMemoryUpdateResolution:
    """Resolve a narrow temporal continuation against recent user evidence.

    A qualifier by itself never selects an arbitrary memory.  Semantic
    antecedents from the latest matching user turn are counted before mutation
    compatibility is checked, so filtering cannot manufacture a false unique
    target.
    """

    normalized = _normalize(text)
    duration = _DURATION_PATTERN.search(normalized)
    persistence = _PERSISTENCE_PATTERN.search(normalized)
    if duration is None and persistence is None:
        return ContextualMemoryUpdateResolution()

    update_type = (
        ContextualUpdateType.DURATION
        if duration is not None
        else ContextualUpdateType.PERSISTENCE
    )
    qualifier_match = duration or persistence
    assert qualifier_match is not None
    evidence_span = qualifier_match.group(0)
    duration_value: int | None = None
    duration_unit: str | None = None
    if duration is not None:
        duration_value = _parse_chinese_number(duration.group("value"))
        if duration_value is not None:
            duration_unit = _normalize_duration_unit(duration.group("unit"))
    plural_reference = _PLURAL_REFERENCE_PATTERN.search(normalized) is not None

    def unresolved(
        reason: str,
        *,
        semantic_ids: tuple[str, ...] = (),
        compatible_ids: tuple[str, ...] = (),
        rejected: tuple[tuple[str, str], ...] = (),
    ) -> ContextualMemoryUpdateResolution:
        return ContextualMemoryUpdateResolution(
            update_type=update_type,
            evidence_span=evidence_span,
            temporal_expression=evidence_span,
            duration_value=duration_value,
            duration_unit=duration_unit,
            reason=reason,
            candidate_ids=compatible_ids,
            semantic_candidate_ids=semantic_ids,
            compatible_candidate_ids=compatible_ids,
            rejected_candidates=rejected,
            plural_reference=plural_reference,
        )

    user_history = [
        message
        for message in conversation_history
        if message.role == MessageRole.USER and message.content.strip()
    ]
    antecedent = None
    antecedent_predicates: frozenset[str] = frozenset()
    for message in reversed(user_history):
        predicates = _antecedent_predicates(_normalize(message.content))
        if predicates:
            antecedent = message
            antecedent_predicates = predicates
            break
    if antecedent is None:
        return unresolved("no_user_contact_antecedent")
    antecedent_ids = {antecedent.id}

    # Keep semantic antecedents separate from mutation eligibility.  Filtering
    # first can turn an ambiguous reference into a false unique target.
    semantic_candidates = []
    for item in existing_memories:
        if item.status not in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}:
            continue
        source_match = item.source_message_id in antecedent_ids
        predicate_match = (
            item.kind in _SEMANTIC_ANTECEDENT_KINDS
            and (
                (item.canonical_predicate or "").startswith(
                    ("interaction.", "contact.", "relationship.")
                )
                or item.kind == MemoryKind.INTERACTION_PATTERN
            )
        )
        deduped_semantic_match = _matches_recent_antecedent_semantics(
            item,
            antecedent_predicates,
            antecedent.created_at,
        )
        if (source_match and predicate_match) or deduped_semantic_match:
            semantic_candidates.append(item)
    semantic_candidates.sort(
        key=lambda item: (
            item.source_message_id in antecedent_ids,
            item.updated_at,
        ),
        reverse=True,
    )
    semantic_ids = tuple(item.id for item in semantic_candidates[:8])
    if not semantic_candidates:
        return unresolved("no_semantic_antecedent")

    def compatibility_diagnostics() -> tuple[
        tuple[str, ...], tuple[tuple[str, str], ...]
    ]:
        compatible_ids = tuple(
            item.id
            for item in semantic_candidates[:8]
            if is_contextual_update_target_compatible(item)
        )
        rejected = tuple(
            (item.id, "mutation_incompatible")
            for item in semantic_candidates[:8]
            if not is_contextual_update_target_compatible(item)
        )
        return compatible_ids, rejected

    if _NON_PARTNER_REFERENCE_PATTERN.search(normalized):
        compatible_ids, rejected = compatibility_diagnostics()
        return unresolved(
            "explicit_subject_mismatch",
            semantic_ids=semantic_ids,
            compatible_ids=compatible_ids,
            rejected=tuple(
                (item.id, "explicit_subject_mismatch")
                for item in semantic_candidates[:8]
            ),
        )

    if plural_reference and len(semantic_candidates) > 1:
        compatible_ids, rejected = compatibility_diagnostics()
        return unresolved(
            "unsupported_multi_target",
            semantic_ids=semantic_ids,
            compatible_ids=compatible_ids,
            rejected=rejected,
        )
    if len(semantic_candidates) > 1:
        compatible_ids, rejected = compatibility_diagnostics()
        return unresolved(
            "ambiguous_semantic_antecedent",
            semantic_ids=semantic_ids,
            compatible_ids=compatible_ids,
            rejected=rejected,
        )

    semantic_target = semantic_candidates[0]
    if not is_contextual_update_target_compatible(semantic_target):
        return unresolved(
            "no_compatible_active_memory",
            semantic_ids=semantic_ids,
            rejected=((semantic_target.id, "mutation_incompatible"),),
        )

    target = semantic_target
    if duration is not None:
        if duration_value is None or duration_unit is None:
            return unresolved(
                "unsupported_duration_value",
                semantic_ids=semantic_ids,
                compatible_ids=(target.id,),
            )
        return ContextualMemoryUpdateResolution(
            target=target,
            update_type=ContextualUpdateType.DURATION,
            evidence_span=evidence_span,
            temporal_expression=evidence_span,
            duration_value=duration_value,
            duration_unit=duration_unit,
            reason="explicit_duration_for_recent_contact_pattern",
            candidate_ids=(target.id,),
            semantic_candidate_ids=semantic_ids,
            compatible_candidate_ids=(target.id,),
            plural_reference=plural_reference,
        )

    assert persistence is not None
    return ContextualMemoryUpdateResolution(
        target=target,
        update_type=ContextualUpdateType.PERSISTENCE,
        evidence_span=evidence_span,
        temporal_expression=evidence_span,
        reason="persistence_for_recent_contact_pattern",
        candidate_ids=(target.id,),
        semantic_candidate_ids=semantic_ids,
        compatible_candidate_ids=(target.id,),
        plural_reference=plural_reference,
    )


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()


def _antecedent_predicates(text: str) -> frozenset[str]:
    predicates: set[str] = set()
    if _RESPONSE_ANTECEDENT_PATTERN.search(text):
        predicates.add("interaction.response_engagement")
    if _CONTACT_FREQUENCY_ANTECEDENT_PATTERN.search(text):
        predicates.update(_CONTACT_FREQUENCY_PREDICATES)
    return frozenset(predicates)


def _matches_recent_antecedent_semantics(
    item: MemoryItem,
    antecedent_predicates: frozenset[str],
    antecedent_created_at: datetime,
) -> bool:
    item_predicate = item.canonical_predicate or item.state_dimension
    if item_predicate not in antecedent_predicates:
        return False
    last_seen_at = item.last_seen_at or item.updated_at
    if last_seen_at < antecedent_created_at:
        return False
    evidence = _normalize(" ".join([item.summary, *item.evidence_spans]))
    if item_predicate == "interaction.response_engagement":
        return _RESPONSE_ANTECEDENT_PATTERN.search(evidence) is not None
    return _CONTACT_FREQUENCY_ANTECEDENT_PATTERN.search(evidence) is not None


def _normalize_duration_unit(value: str) -> str:
    units = {
        "星期": "week",
        "周": "week",
        "个月": "month",
        "月": "month",
        "天": "day",
        "年": "year",
    }
    return units[value]


def _parse_chinese_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    digits = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
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

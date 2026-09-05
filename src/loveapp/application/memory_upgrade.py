"""Conservative policy for escalating memory extraction to a strong model."""

import re
from dataclasses import dataclass

from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    EvidenceExplicitness,
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    PredicateType,
    StoredMessage,
    normalize_candidate_predicate,
)
from loveapp.domain.memory_dimensions import (
    covered_claim_dimensions,
    detect_evidence_dimensions,
    is_relationship_interaction_subject,
)
from loveapp.domain.memory_lifecycle import (
    governed_state_identity,
    governed_state_value,
)

from .memory_repair import MemoryResponseError


@dataclass(frozen=True)
class MemoryUpgradeDecision:
    should_upgrade: bool
    reason: str | None = None
    importance: int = 1
    signals: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ExistingConflict:
    claim: AtomicClaim
    governed: bool
    local_subject_match: bool


_LOCAL_GOVERNED_CONFLICT_MIN_CONFIDENCE = 0.9
_GENERIC_STATE_EVIDENCE = frozenset(
    {
        "现在",
        "目前",
        "最近",
        "这次",
        "这样",
        "这种情况",
        "已经",
        "还是",
        "仍然",
        "又",
        "再次",
        "我",
        "我们",
        "她",
        "他",
        "对方",
        "双方",
    }
)


# These are durable relationship facts where a wrong extraction is more costly
# than one extra strong-model request. Generic advice questions are deliberately
# absent: the gate already handles them and they do not need semantic recovery.
_HIGH_VALUE_PATTERNS: tuple[tuple[str, str, int], ...] = (
    (r"记住|记录一下|别忘了|不要忘记", "explicit_remember", 5),
    (r"之前.*(?:错|误会)|不是.{0,12}而是|纠正|改成|现在.*才知道", "explicit_correction", 5),
    (r"边界|明确拒绝|不愿意|不想见|不接受|不要再联系|不舒服", "boundary_or_rejection", 5),
    (r"吵架|冲突|争执|伤害", "conflict_event", 4),
    (r"和好了|复合|分手|确认关系|冷战|关系结束", "relationship_state", 5),
    (r"照你说的|按你说的|这个办法.*有效|确实有效|结果.*开心|后来.*和好", "advice_outcome", 5),
    (r"过敏|忌口|不吃|不能吃|预算|消费观|经济实惠", "restriction_or_budget", 4),
    (r"听说.{0,40}(聊天|联系|追求|喜欢)", "hearsay_or_competitor", 4),
    (r"(?:感觉|觉得|认为).{0,30}(追求|喜欢|对方|男生|女生)", "relationship_belief", 4),
    (r"比我(?:优秀|好|强)|配不上|不如", "self_comparison", 4),
    (
        r"连续|每天|每周|每月|最近(?:\d+|[一二三四五六七八九十两]+)(?:天|周|个月)"
        r"|过去(?:\d+|[一二三四五六七八九十两]+)(?:天|周|个月)|这段时间|一直",
        "temporal_pattern",
        4,
    ),
)


# A coverage gap is diagnostic by default.  Only these semantic signals make
# recovering a missing claim worth a second model call.  Generic temporal
# repetition remains important for empty/low-confidence extraction, but it is
# too common to justify escalation whenever another dimension was omitted.
_COVERAGE_RECOVERY_SIGNALS = frozenset(
    {
        "explicit_remember",
        "explicit_correction",
        "boundary_or_rejection",
        "conflict_event",
        "relationship_state",
        "advice_outcome",
        "restriction_or_budget",
        "hearsay_or_competitor",
        "relationship_belief",
        "self_comparison",
    }
)


def assess_memory_upgrade(
    text: str,
    *,
    existing_memories: list[MemoryItem],
    conversation_history: list[StoredMessage],
    extraction: AtomicExtraction | None = None,
    failure: MemoryResponseError | None = None,
    partial: bool = False,
    min_importance: int = 4,
) -> MemoryUpgradeDecision:
    """Return whether a second model call is justified.

    A syntax error by itself never upgrades. A strong model is considered only
    when the user text carries a durable, high-impact signal and the Flash
    result is absent, semantically invalid, uncertain, or potentially
    contradictory with existing context.
    """

    signals: list[str] = []
    importance = 1
    for pattern, signal, score in _HIGH_VALUE_PATTERNS:
        if re.search(pattern, text):
            signals.append(signal)
            importance = max(importance, score)

    source_dimensions = detect_evidence_dimensions(text)
    if failure is not None and failure.category == "atomicity_validation" and len(
        source_dimensions
    ) > 1:
        signals.append("multi_dimension_atomicity_failure")

    ambiguous_reference = _has_ambiguous_reference(text, conversation_history)
    if ambiguous_reference:
        signals.append("ambiguous_reference")
        importance = max(importance, 4)

    explicit_correction = "explicit_correction" in signals
    existing_conflict = explicit_correction and bool(existing_memories)
    governed_conflict_local_resolution = False
    coverage_gap = False
    if extraction is not None and _has_claim_coverage_gap(text, extraction):
        coverage_gap = True
        signals.append("claim_coverage_gap")
    if partial:
        signals.append("partial_claim_validation")
    if extraction is not None:
        conflicts = _existing_conflicts(extraction, existing_memories)
        if conflicts:
            existing_conflict = True
            importance = max(importance, 4)
            governed_conflict_local_resolution = (
                _can_resolve_governed_conflicts_locally(
                    conflicts,
                    source_text=text,
                    partial=partial,
                    ambiguous_reference=ambiguous_reference,
                )
            )
    if existing_conflict:
        signals.append("existing_memory_conflict")
    if governed_conflict_local_resolution:
        signals.append("governed_conflict_local_resolution")

    conflict_requires_upgrade = (
        existing_conflict and not governed_conflict_local_resolution
    )

    if failure is not None:
        if importance < min_importance and not conflict_requires_upgrade:
            return MemoryUpgradeDecision(
                should_upgrade=False,
                reason=None,
                importance=importance,
                signals=tuple(dict.fromkeys(signals)),
            )
        # Syntax, root-shape, and schema failures are format problems. They
        # are intentionally discarded rather than sent to another model.
        # Transport failures are also not semantic fallback candidates.
        if failure.category in {
            "transport",
            "timeout",
            "empty_response",
            "json_syntax",
            "root_shape",
            "schema_validation",
            "semantic_gate_contract",
            "unsupported_enum",
            "missing_temporal_anchor",
        }:
            return MemoryUpgradeDecision(
                should_upgrade=False,
                reason=None,
                importance=importance,
                signals=tuple(dict.fromkeys(signals)),
            )
        reason = (
            "existing_memory_conflict"
            if conflict_requires_upgrade
            else "semantic_uncertainty"
        )
        return MemoryUpgradeDecision(
            should_upgrade=True,
            reason=reason,
            importance=importance,
            signals=tuple(dict.fromkeys(signals)),
        )

    if extraction is None or (
        importance < min_importance and not conflict_requires_upgrade
    ):
        return MemoryUpgradeDecision(
            should_upgrade=False,
            importance=importance,
            signals=tuple(dict.fromkeys(signals)),
        )

    empty_important = not extraction.claims and importance >= min_importance
    low_confidence = any(claim.confidence < 0.55 for claim in extraction.claims)
    model_inferred = any(
        claim.perspective.value == "model_inferred" for claim in extraction.claims
    )
    uncertain_extraction = (
        not extraction.claims or partial or low_confidence or model_inferred
    )
    recoverable_coverage_gap = coverage_gap and any(
        signal in _COVERAGE_RECOVERY_SIGNALS for signal in signals
    )
    if conflict_requires_upgrade:
        reason = "existing_memory_conflict"
    elif "ambiguous_reference" in signals and uncertain_extraction:
        reason = "ambiguous_reference"
    elif recoverable_coverage_gap:
        reason = "claim_coverage_gap"
    elif empty_important:
        reason = "important_empty_extraction"
    elif low_confidence or model_inferred:
        reason = "low_confidence_important"
    else:
        return MemoryUpgradeDecision(
            should_upgrade=False,
            importance=importance,
            signals=tuple(dict.fromkeys(signals)),
        )

    return MemoryUpgradeDecision(
        should_upgrade=True,
        reason=reason,
        importance=importance,
        signals=tuple(dict.fromkeys(signals)),
    )


def _has_ambiguous_reference(
    text: str,
    conversation_history: list[StoredMessage],
) -> bool:
    if not conversation_history:
        return False
    if not re.search(r"对方|这个|那个|前者|后者", text):
        return False
    user_history = [
        message.content
        for message in conversation_history
        if message.role.value == "user"
    ]
    if len(user_history) < 2:
        return False
    # Temporal connectors such as "后来" and "之前" are not references.
    # Demonstratives only justify escalation when recent user turns contain
    # genuinely different person categories that could be their antecedent.
    entity_markers: set[str] = set()
    for item in user_history:
        if "她" in item or "女生" in item or "女孩" in item:
            entity_markers.add("female")
        if "他" in item or "男生" in item or "男孩" in item:
            entity_markers.add("male")
    return len(entity_markers) >= 2


def _has_claim_coverage_gap(
    text: str,
    extraction: AtomicExtraction,
) -> bool:
    source_dimensions = detect_evidence_dimensions(text)
    covered_dimensions: set[str] = set()
    for claim in extraction.claims:
        covered_dimensions.update(
            covered_claim_dimensions(
                kind=claim.kind.value,
                predicate=claim.predicate,
                payload=claim.payload,
                evidence_text=" ".join(claim.evidence_spans),
            )
        )
    if source_dimensions - covered_dimensions:
        return True

    coverage_rules = (
        (r"听说.{0,40}(聊天|联系)", ("聊天",)),
        (r"(?:感觉|觉得|认为).{0,30}(追求|喜欢)", ("追求",)),
        (r"比我(?:优秀|好|强)|配不上|不如", ("优秀", "比我")),
    )
    claim_texts = [
        " ".join(
            [
                claim.summary,
                claim.predicate,
                *claim.evidence_spans,
                *[str(value) for value in claim.payload.values()],
            ]
        )
        for claim in extraction.claims
    ]
    matched_claim_indexes: list[int] = []
    for pattern, required_terms in coverage_rules:
        if not re.search(pattern, text):
            continue
        candidates = [
            index
            for index, claim_text in enumerate(claim_texts)
            if all(term in claim_text for term in required_terms)
        ]
        if not candidates:
            return True
        matched_claim_indexes.append(candidates[0])
    return len(matched_claim_indexes) != len(set(matched_claim_indexes))


def _existing_conflicts(
    extraction: AtomicExtraction,
    existing_memories: list[MemoryItem],
) -> tuple[_ExistingConflict, ...]:
    conflicts: list[_ExistingConflict] = []
    normalized_existing = [
        (item, normalize_candidate_predicate(item)) for item in existing_memories
    ]
    for claim in extraction.claims:
        normalized_claim = normalize_candidate_predicate(claim.to_candidate())
        for item, normalized_item in normalized_existing:
            local_subject_match = claim.subject.casefold() == item.subject.casefold()
            interaction_alias_match = (
                claim.kind == MemoryKind.INTERACTION_PATTERN
                and item.kind == claim.kind
                and is_relationship_interaction_subject(claim.subject)
                and is_relationship_interaction_subject(item.subject)
            )
            if not local_subject_match and not interaction_alias_match:
                continue
            if _is_governed_state_conflict(normalized_claim, normalized_item):
                conflicts.append(
                    _ExistingConflict(
                        claim=claim,
                        governed=True,
                        local_subject_match=local_subject_match,
                    )
                )
                continue
            if _is_legacy_conflict(claim, item):
                conflicts.append(
                    _ExistingConflict(
                        claim=claim,
                        governed=False,
                        local_subject_match=local_subject_match,
                    )
                )
    return tuple(conflicts)


def _is_governed_state_conflict(
    claim: MemoryCandidate,
    existing: MemoryCandidate,
) -> bool:
    claim_identity = governed_state_identity(claim)
    existing_identity = governed_state_identity(existing)
    if claim_identity is None or claim_identity != existing_identity:
        return False
    claim_value = governed_state_value(claim)
    existing_value = governed_state_value(existing)
    return (
        claim_value is not None
        and existing_value is not None
        and claim_value != existing_value
    )


def _is_legacy_conflict(claim: AtomicClaim, item: MemoryItem) -> bool:
    existing_predicate = item.payload.get("predicate")
    if not isinstance(existing_predicate, str) or existing_predicate != claim.predicate:
        return False
    if claim.kind == MemoryKind.INTERACTION_PATTERN and item.kind == claim.kind:
        claim_metric = claim.payload.get("metric")
        item_metric = item.payload.get("metric")
        if claim_metric != item_metric:
            return False
        claim_state = _pattern_state(claim.payload)
        item_state = _pattern_state(item.payload)
        return bool(claim_state and item_state and claim_state != item_state)
    if claim.kind == MemoryKind.STABLE_FACT and item.kind == claim.kind:
        claim_object = claim.object or claim.payload.get("object")
        item_object = item.payload.get("object")
        return bool(claim_object and item_object and claim_object != item_object)
    if claim.kind == MemoryKind.PREFERENCE and item.kind == claim.kind:
        claim_preference = claim.payload.get("preference")
        item_preference = item.payload.get("preference")
        claim_type = claim.payload.get("preference_type")
        item_type = item.payload.get("preference_type")
        return bool(
            claim_preference == item_preference
            and claim_type
            and item_type
            and claim_type != item_type
        )
    return False


def _can_resolve_governed_conflicts_locally(
    conflicts: tuple[_ExistingConflict, ...],
    *,
    source_text: str,
    partial: bool,
    ambiguous_reference: bool,
) -> bool:
    if not conflicts or partial or ambiguous_reference:
        return False
    return all(
        conflict.governed
        and conflict.local_subject_match
        and _is_high_integrity_governed_claim(conflict.claim, source_text)
        for conflict in conflicts
    )


def _is_high_integrity_governed_claim(claim: AtomicClaim, source_text: str) -> bool:
    if claim.predicate_type != PredicateType.CANONICAL:
        return False
    if claim.explicitness != EvidenceExplicitness.EXPLICIT:
        return False
    if claim.confidence < _LOCAL_GOVERNED_CONFLICT_MIN_CONFIDENCE:
        return False
    if claim.perspective != MemoryPerspective.USER_REPORTED or claim.requires_inference:
        return False
    return all(
        _is_specific_exact_evidence(span, source_text)
        for span in claim.evidence_spans
    )


def _is_specific_exact_evidence(span: str, source_text: str) -> bool:
    if not span.strip() or span not in source_text:
        return False
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "", span).casefold()
    return len(normalized) >= 2 and normalized not in _GENERIC_STATE_EVIDENCE


def _pattern_state(payload: dict[str, object]) -> str:
    for key in ("current", "direction", "frequency"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.casefold().strip()
    return ""

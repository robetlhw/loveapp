import re
from dataclasses import dataclass, replace
from datetime import datetime

from loveapp.domain.memory import (
    AdmissionDecision,
    EvidenceExplicitness,
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    PredicateType,
)
from loveapp.domain.memory_lifecycle import governed_state_identity, governed_state_value
from loveapp.domain.memory_predicates import is_high_risk_predicate


@dataclass(frozen=True)
class MemoryAdmissionPolicy:
    direct_confirm_threshold: float
    strong_review_threshold: float
    allow_proposed: bool
    require_explicit_evidence: bool = False
    require_multi_evidence: bool = False
    default_ttl_days: int | None = None
    high_risk: bool = False


@dataclass(frozen=True)
class AdmissionAssessment:
    decision: AdmissionDecision
    score: float
    score_breakdown: dict[str, float | bool | str]
    reason: str


@dataclass(frozen=True)
class GovernedTransitionEligibility:
    eligible: bool
    reason: str
    target_memory_id: str | None = None
    state_dimension: str | None = None


_GOVERNED_TRANSITION_MIN_CONFIDENCE = 0.9
_CURRENT_TRANSITION_EVIDENCE_PATTERN = re.compile(
    r"(?:现在|当前|如今|最近|近来|这(?:几|些|段)(?:天|周|星期|个月|月|年|时间)|"
    r"currently|now|recently|over\s+the\s+past)",
    re.IGNORECASE,
)
_HISTORICAL_TRANSITION_EVIDENCE_PATTERN = re.compile(
    r"(?:曾经|以前|之前|去年|前年|当时|过去|上(?:个|一)(?:月|周|星期|年)|"
    r"previously|formerly|last\s+(?:month|week|year)|in\s+the\s+past)",
    re.IGNORECASE,
)


DEFAULT_ADMISSION_POLICIES: dict[MemoryKind, MemoryAdmissionPolicy] = {
    MemoryKind.PREFERENCE: MemoryAdmissionPolicy(0.75, 0.55, True),
    MemoryKind.STABLE_FACT: MemoryAdmissionPolicy(
        0.85,
        0.65,
        True,
        require_explicit_evidence=True,
    ),
    MemoryKind.INTERACTION_EVENT: MemoryAdmissionPolicy(0.80, 0.60, True),
    MemoryKind.INTERACTION_PATTERN: MemoryAdmissionPolicy(
        0.92,
        0.70,
        True,
        require_multi_evidence=True,
    ),
    MemoryKind.PLANNED_EVENT: MemoryAdmissionPolicy(0.85, 0.65, True),
    MemoryKind.ACTION_INTENT: MemoryAdmissionPolicy(0.75, 0.55, True, default_ttl_days=14),
    MemoryKind.ADVICE_OUTCOME: MemoryAdmissionPolicy(0.85, 0.65, True),
    MemoryKind.RELATIONSHIP_STATE: MemoryAdmissionPolicy(
        0.95,
        0.70,
        True,
        require_explicit_evidence=True,
        high_risk=True,
    ),
}


def build_admission_policies(
    overrides: dict[str, dict[str, object]] | None = None,
) -> dict[MemoryKind, MemoryAdmissionPolicy]:
    policies = dict(DEFAULT_ADMISSION_POLICIES)
    for raw_kind, values in (overrides or {}).items():
        try:
            kind = MemoryKind(raw_kind)
        except ValueError as exc:
            raise ValueError(f"unknown memory admission policy kind: {raw_kind}") from exc
        allowed = {
            key: value
            for key, value in values.items()
            if key in MemoryAdmissionPolicy.__dataclass_fields__
        }
        policies[kind] = replace(policies[kind], **allowed)
    return policies

_EXPLICITNESS_ADJUSTMENTS = {
    EvidenceExplicitness.EXPLICIT: 0.0,
    EvidenceExplicitness.STRONGLY_IMPLIED: -0.08,
    EvidenceExplicitness.WEAKLY_INFERRED: -0.22,
    EvidenceExplicitness.SPECULATIVE: -0.45,
}

_KNOWN_SUBJECTS = {
    "user",
    "partner",
    "relationship",
    "对方",
    "伴侣",
    "她",
    "他",
    "用户",
}

_FREQUENCY_PATTERN = re.compile(
    r"经常|总是|每次|一直|反复|每天|每周|通常|频繁|很少|偶尔|"
    r"always|often|usually|every\s+(?:day|week|time)|repeatedly",
    re.IGNORECASE,
)


def assess_memory_admission(
    candidate: MemoryCandidate,
    source_text: str,
    *,
    conflict: bool = False,
    corroborating_evidence_count: int = 0,
    policies: dict[MemoryKind, MemoryAdmissionPolicy] | None = None,
    governed_transition_eligibility: GovernedTransitionEligibility | None = None,
) -> AdmissionAssessment:
    policy = (policies or DEFAULT_ADMISSION_POLICIES)[candidate.kind]
    evidence_valid = bool(candidate.evidence_spans) and all(
        evidence and evidence in source_text for evidence in candidate.evidence_spans
    )
    explicitness_adjustment = _EXPLICITNESS_ADJUSTMENTS[candidate.explicitness]
    inference_adjustment = -0.18 if candidate.requires_inference else 0.0
    evidence_adjustment = 0.0 if evidence_valid else -0.5
    subject_adjustment = 0.0 if candidate.subject.casefold() in _KNOWN_SUBJECTS else -0.05
    perspective_adjustment = (
        -0.15
        if candidate.perspective
        in {MemoryPerspective.USER_BELIEF, MemoryPerspective.MODEL_INFERRED}
        else 0.0
    )
    conflict_adjustment = -0.05 if conflict else 0.0
    pattern_has_frequency = interaction_pattern_has_frequency(candidate)
    pattern_has_multiple = interaction_pattern_has_multiple_evidence(
        candidate,
        corroborating_evidence_count=corroborating_evidence_count,
    )
    pattern_adjustment = 0.0
    if candidate.kind == MemoryKind.INTERACTION_PATTERN:
        if pattern_has_frequency:
            pattern_adjustment += 0.04
        elif pattern_has_multiple:
            pattern_adjustment += 0.02
        else:
            pattern_adjustment -= 0.20

    temporal_valid = _temporal_shape_is_valid(candidate)
    temporal_adjustment = 0.0 if temporal_valid else -0.20
    score = _clamp(
        candidate.confidence
        + explicitness_adjustment
        + inference_adjustment
        + evidence_adjustment
        + subject_adjustment
        + perspective_adjustment
        + conflict_adjustment
        + pattern_adjustment
        + temporal_adjustment
    )
    breakdown: dict[str, float | bool | str] = {
        "model_confidence": round(candidate.confidence, 4),
        "explicitness": candidate.explicitness.value,
        "explicitness_adjustment": explicitness_adjustment,
        "requires_inference": candidate.requires_inference,
        "inference_adjustment": inference_adjustment,
        "evidence_is_source_substring": evidence_valid,
        "evidence_adjustment": evidence_adjustment,
        "subject_resolved": candidate.subject.casefold() in _KNOWN_SUBJECTS,
        "subject_adjustment": subject_adjustment,
        "perspective_adjustment": perspective_adjustment,
        "conflict": conflict,
        "conflict_adjustment": conflict_adjustment,
        "temporal_shape_valid": temporal_valid,
        "temporal_adjustment": temporal_adjustment,
        "pattern_has_frequency": pattern_has_frequency,
        "pattern_has_multiple_evidence": pattern_has_multiple,
        "pattern_adjustment": pattern_adjustment,
        "governed_transition_candidate": bool(
            governed_transition_eligibility is not None
            and governed_transition_eligibility.eligible
        ),
        "governed_transition_reason": (
            governed_transition_eligibility.reason
            if governed_transition_eligibility is not None
            else "not_evaluated"
        ),
        "score": round(score, 4),
    }
    if (
        governed_transition_eligibility is not None
        and governed_transition_eligibility.target_memory_id is not None
    ):
        breakdown["governed_transition_target_memory_id"] = (
            governed_transition_eligibility.target_memory_id
        )

    if not evidence_valid:
        return AdmissionAssessment(
            AdmissionDecision.REJECT,
            score,
            breakdown,
            "evidence_not_in_source",
        )
    if not temporal_valid:
        decision = AdmissionDecision.PROPOSE if policy.allow_proposed else AdmissionDecision.REJECT
        return AdmissionAssessment(decision, score, breakdown, "invalid_temporal_shape")
    if (
        candidate.kind == MemoryKind.RELATIONSHIP_STATE
        and candidate.explicitness == EvidenceExplicitness.SPECULATIVE
    ):
        return AdmissionAssessment(
            AdmissionDecision.REJECT,
            score,
            breakdown,
            "speculative_relationship_state",
        )

    high_risk = policy.high_risk or is_high_risk_predicate(candidate.canonical_predicate)
    explicit_requirement_met = (
        not policy.require_explicit_evidence
        or candidate.explicitness == EvidenceExplicitness.EXPLICIT
    )
    multi_evidence_requirement_met = (
        not policy.require_multi_evidence or pattern_has_frequency or pattern_has_multiple
    )
    if (
        governed_transition_eligibility is not None
        and governed_transition_eligibility.eligible
    ):
        return AdmissionAssessment(
            AdmissionDecision.CONFIRM,
            score,
            breakdown,
            "confirmed_governed_transition",
        )
    direct_requirements_met = (
        explicit_requirement_met
        and multi_evidence_requirement_met
        and not candidate.requires_inference
        and candidate.perspective == MemoryPerspective.USER_REPORTED
        and candidate.predicate_type != PredicateType.CUSTOM
        and not conflict
    )
    if score >= policy.direct_confirm_threshold and direct_requirements_met:
        return AdmissionAssessment(
            AdmissionDecision.CONFIRM,
            score,
            breakdown,
            "direct_threshold_met",
        )
    if score >= policy.strong_review_threshold and (
        high_risk
        or conflict
        or candidate.requires_inference
        or not explicit_requirement_met
        or not multi_evidence_requirement_met
        or candidate.predicate_type == PredicateType.CUSTOM
    ):
        return AdmissionAssessment(
            AdmissionDecision.STRONG_REVIEW,
            score,
            breakdown,
            "high_risk_or_ambiguous",
        )
    proposed_floor = max(policy.strong_review_threshold - 0.2, 0.35)
    if candidate.perspective == MemoryPerspective.USER_BELIEF:
        proposed_floor = min(proposed_floor, 0.15)
    source_type = str(candidate.payload.get("source_type") or "").casefold()
    if source_type in {"hearsay", "third_party_report"}:
        proposed_floor = min(proposed_floor, 0.35)
    if policy.allow_proposed and score >= proposed_floor:
        return AdmissionAssessment(
            AdmissionDecision.PROPOSE,
            score,
            breakdown,
            "valuable_but_unconfirmed",
        )
    return AdmissionAssessment(
        AdmissionDecision.REJECT,
        score,
        breakdown,
        "below_admission_threshold",
    )


def assess_governed_transition_eligibility(
    candidate: MemoryCandidate,
    source_text: str,
    active_memories: list[MemoryItem],
    *,
    min_confidence: float = _GOVERNED_TRANSITION_MIN_CONFIDENCE,
) -> GovernedTransitionEligibility:
    """Authorize only an explicit, unique, forward canonical state transition."""

    if candidate.predicate_type != PredicateType.CANONICAL:
        return GovernedTransitionEligibility(False, "noncanonical_candidate")
    identity = governed_state_identity(candidate)
    value = governed_state_value(candidate)
    if identity is None or value is None:
        return GovernedTransitionEligibility(False, "ungoverned_or_invalid_state")

    targets = [
        item
        for item in active_memories
        if item.status in {MemoryStatus.CONFIRMED, MemoryStatus.PROPOSED}
        and item.subject.casefold() == candidate.subject.casefold()
        and governed_state_identity(item) == identity
        and governed_state_value(item) not in {None, value}
    ]
    if not targets:
        return GovernedTransitionEligibility(
            False,
            "no_unique_governed_target",
            state_dimension=identity[1],
        )
    if len(targets) != 1:
        return GovernedTransitionEligibility(
            False,
            "ambiguous_governed_targets",
            state_dimension=identity[1],
        )

    target = targets[0]
    common = {
        "target_memory_id": target.id,
        "state_dimension": identity[1],
    }
    if candidate.explicitness != EvidenceExplicitness.EXPLICIT:
        return GovernedTransitionEligibility(False, "nonexplicit_evidence", **common)
    if candidate.perspective != MemoryPerspective.USER_REPORTED:
        return GovernedTransitionEligibility(False, "nonreported_perspective", **common)
    if candidate.requires_inference:
        return GovernedTransitionEligibility(False, "requires_inference", **common)
    if candidate.confidence < min_confidence:
        return GovernedTransitionEligibility(False, "below_transition_confidence", **common)
    if not candidate.evidence_spans or any(
        not span or span not in source_text for span in candidate.evidence_spans
    ):
        return GovernedTransitionEligibility(False, "evidence_not_in_source", **common)
    if candidate.period_start and candidate.period_end:
        start, end = _align_datetimes(candidate.period_start, candidate.period_end)
        if start > end:
            return GovernedTransitionEligibility(False, "invalid_temporal_shape", **common)
    temporal_failure = _transition_temporal_failure(candidate, target)
    if temporal_failure is not None:
        return GovernedTransitionEligibility(False, temporal_failure, **common)
    return GovernedTransitionEligibility(
        True,
        "eligible_governed_transition",
        **common,
    )


def interaction_pattern_has_frequency(candidate: MemoryCandidate) -> bool:
    return bool(_FREQUENCY_PATTERN.search(" ".join(candidate.evidence_spans)))


def interaction_pattern_has_multiple_evidence(
    candidate: MemoryCandidate,
    *,
    corroborating_evidence_count: int = 0,
) -> bool:
    return len(candidate.evidence_spans) + corroborating_evidence_count >= 2


def _temporal_shape_is_valid(candidate: MemoryCandidate) -> bool:
    if candidate.kind == MemoryKind.PLANNED_EVENT:
        return any(
            (
                candidate.occurred_at,
                candidate.period_start,
                candidate.period_end,
                candidate.payload.get("temporal_expression"),
            )
        )
    if candidate.kind == MemoryKind.INTERACTION_EVENT:
        return candidate.period_start is None or candidate.period_end is None or (
            candidate.period_start <= candidate.period_end
        )
    return True


def _transition_temporal_failure(
    candidate: MemoryCandidate,
    target: MemoryItem,
) -> str | None:
    evidence = " ".join(candidate.evidence_spans)
    if _HISTORICAL_TRANSITION_EVIDENCE_PATTERN.search(evidence):
        return "historical_transition"
    incoming_time = candidate.period_end or candidate.occurred_at or candidate.period_start
    target_time = target.period_end or target.occurred_at or target.period_start
    if incoming_time is None:
        return "missing_transition_temporal_evidence"
    if target_time is None:
        return (
            None
            if _CURRENT_TRANSITION_EVIDENCE_PATTERN.search(evidence)
            else "missing_transition_temporal_evidence"
        )
    incoming_time, target_time = _align_datetimes(incoming_time, target_time)
    return None if incoming_time >= target_time else "historical_transition"


def _align_datetimes(left: datetime, right: datetime) -> tuple[datetime, datetime]:
    if left.tzinfo is None and right.tzinfo is not None:
        left = left.replace(tzinfo=right.tzinfo)
    elif left.tzinfo is not None and right.tzinfo is None:
        right = right.replace(tzinfo=left.tzinfo)
    return left, right


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)

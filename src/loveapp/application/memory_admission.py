import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

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
from loveapp.domain.memory_dimensions import (
    INTERACTION_PATTERN_DIMENSIONS,
    dimension_for_predicate,
    infer_initiation_balance,
    interaction_pattern_state,
    normalize_interaction_metric,
    normalize_interaction_source,
)
from loveapp.domain.memory_epistemics import is_epistemically_confirmed
from loveapp.domain.memory_lifecycle import (
    governed_state_identity,
    governed_state_value,
    memory_concept,
)
from loveapp.domain.memory_normalization import (
    interaction_pattern_has_recurrence_signal,
    interaction_pattern_has_time_window,
)
from loveapp.domain.memory_predicates import is_high_risk_predicate
from loveapp.domain.memory_type_compatibility import assess_memory_type_compatibility


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


@dataclass(frozen=True)
class PatternEvidenceLinkAssessment:
    required: bool
    valid: bool
    linked_event_count: int = 0
    reason: str = "not_required"
    linked_event_ids: tuple[str, ...] = ()
    rejected_evidence_ids: tuple[str, ...] = ()


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
    r"经常|总是|每次|一直|反复|每天|每周|通常|频繁|很少|偶尔|多次|持续|"
    r"基本都|几乎都|大多数时候|越来越|"
    r"always|often|usually|every\s+(?:day|week|time)|repeatedly|over\s+time",
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
    pattern_evidence_links: PatternEvidenceLinkAssessment | None = None,
) -> AdmissionAssessment:
    policy = (policies or DEFAULT_ADMISSION_POLICIES)[candidate.kind]
    evidence_valid = bool(candidate.evidence_spans) and all(
        evidence and evidence in source_text for evidence in candidate.evidence_spans
    )
    explicitness_adjustment = _EXPLICITNESS_ADJUSTMENTS[candidate.explicitness]
    inference_adjustment = -0.18 if candidate.requires_inference else 0.0
    evidence_adjustment = 0.0 if evidence_valid else -0.5
    subject_adjustment = 0.0 if candidate.subject.casefold() in _KNOWN_SUBJECTS else -0.05
    perspective_adjustment = -0.15 if not is_epistemically_confirmed(candidate) else 0.0
    conflict_adjustment = -0.05 if conflict else 0.0
    pattern_has_frequency = interaction_pattern_has_frequency(candidate)
    pattern_has_multiple = interaction_pattern_has_multiple_evidence(
        candidate,
        corroborating_evidence_count=corroborating_evidence_count,
    )
    pattern_has_recurrence = interaction_pattern_has_recurrence_signal(candidate)
    pattern_has_window = interaction_pattern_has_time_window(candidate)
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
        "epistemic_status": candidate.epistemic_status.value,
        "conflict": conflict,
        "conflict_adjustment": conflict_adjustment,
        "temporal_shape_valid": temporal_valid,
        "temporal_adjustment": temporal_adjustment,
        "pattern_has_frequency": pattern_has_frequency,
        "pattern_has_multiple_evidence": pattern_has_multiple,
        "pattern_has_recurrence": pattern_has_recurrence,
        "pattern_has_time_window": pattern_has_window,
        "pattern_adjustment": pattern_adjustment,
        "pattern_evidence_link_required": bool(
            pattern_evidence_links is not None and pattern_evidence_links.required
        ),
        "pattern_evidence_link_valid": bool(
            pattern_evidence_links is None or pattern_evidence_links.valid
        ),
        "pattern_linked_event_count": (
            pattern_evidence_links.linked_event_count
            if pattern_evidence_links is not None
            else 0
        ),
        "pattern_evidence_link_reason": (
            pattern_evidence_links.reason
            if pattern_evidence_links is not None
            else "not_evaluated"
        ),
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
    if pattern_evidence_links is not None and not pattern_evidence_links.valid:
        return AdmissionAssessment(
            AdmissionDecision.REJECT,
            score,
            breakdown,
            pattern_evidence_links.reason,
        )
    # A model-inferred Pattern is only safe when its recurrence/window
    # provenance is explicit.  User-reported Pattern-shaped claims retain the
    # existing admission/review path (and clear one-off behaviours are
    # demoted to Events by normalization).  Likewise, an explicitly governed
    # state transition is a replacement of an already-qualified state rather
    # than a new inferred Pattern, so its target's lifecycle evidence is the
    # authority for this boundary.
    pattern_boundary_invalid = False
    if candidate.kind == MemoryKind.INTERACTION_PATTERN:
        governed_exception = (
            governed_transition_eligibility is not None
            and governed_transition_eligibility.eligible
        ) or candidate.payload.get("contextual_update_type") == "correction"
        lifecycle_surface = memory_concept(candidate) in {
            "active_conflict",
            "contact_unavailable",
            "contact_restored",
            "contact_reduced",
            "response_unresponsive",
            "response_restored",
            "relationship_repaired",
            "relationship_started",
        }
        # A recurring claim with no declared window (or a low-confidence
        # windowed claim) is not safe to admit as a durable Pattern.  Keep the
        # legacy strong-review path for an otherwise unqualified user report
        # such as a free-form one-off sentence; normalization repairs clear
        # bounded actions to Events, while genuinely ambiguous reports remain
        # reviewable rather than silently confirmed.
        pattern_boundary_invalid = (
            not governed_exception
            and (
                (
                    candidate.confidence < 0.75
                    and str(candidate.payload.get("source_type") or "").casefold()
                    not in {"hearsay", "third_party_report"}
                )
                or (
                    pattern_has_recurrence
                    and not pattern_has_window
                    and not lifecycle_surface
                )
                or (
                    candidate.perspective == MemoryPerspective.MODEL_INFERRED
                    and (not pattern_has_recurrence or not pattern_has_window)
                )
            )
        )
    if pattern_boundary_invalid:
        return AdmissionAssessment(
            AdmissionDecision.REJECT,
            score,
            breakdown,
            "interaction_pattern_boundary_invalid",
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
        and is_epistemically_confirmed(candidate)
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
    if not is_epistemically_confirmed(candidate):
        return GovernedTransitionEligibility(False, "nonreported_perspective")

    targets = [
        item
        for item in active_memories
        if item.status in {MemoryStatus.CONFIRMED, MemoryStatus.PROPOSED}
        and item.subject.casefold() == candidate.subject.casefold()
        and governed_state_identity(item) == identity
        and governed_state_value(item) not in {None, value}
        and assess_memory_type_compatibility(
            candidate,
            item,
        ).lifecycle_replace_allowed
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


def assess_pattern_evidence_links(
    candidate: MemoryCandidate,
    active_memories: list[MemoryItem],
) -> PatternEvidenceLinkAssessment:
    """Validate stored Event references for a model-inferred Pattern.

    Shape validation alone cannot establish that an ID points to an Event in
    the current user/relationship scope.  ``active_memories`` is already
    scoped and lifecycle-filtered by ``MemoryService``, so this boundary can
    authorize evidence without adding a Store API or trusting model payload.
    """

    if candidate.kind != MemoryKind.INTERACTION_PATTERN:
        return PatternEvidenceLinkAssessment(required=False, valid=True)
    source = candidate.payload.get("source")
    normalized_source = normalize_interaction_source(source)
    if (
        normalized_source not in {"model_inferred", "derived_from_events"}
        and candidate.perspective != MemoryPerspective.MODEL_INFERRED
    ):
        return PatternEvidenceLinkAssessment(required=False, valid=True)

    raw_ids = candidate.payload.get("evidence_ids")
    if (
        not isinstance(raw_ids, list)
        or not raw_ids
        or any(not isinstance(value, str) or not value.strip() for value in raw_ids)
    ):
        return PatternEvidenceLinkAssessment(
            required=True,
            valid=False,
            reason="model_inferred_pattern_missing_event_evidence",
        )
    evidence_ids = tuple(dict.fromkeys(str(value).strip() for value in raw_ids))
    active_events = {
        item.id: item
        for item in active_memories
        if item.kind == MemoryKind.INTERACTION_EVENT
        and item.status in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
    }
    missing = tuple(memory_id for memory_id in evidence_ids if memory_id not in active_events)
    compatible = tuple(
        memory_id
        for memory_id in evidence_ids
        if memory_id in active_events
        and _pattern_event_evidence_compatible(candidate, active_events[memory_id])
    )
    incompatible = tuple(
        memory_id
        for memory_id in evidence_ids
        if memory_id in active_events and memory_id not in compatible
    )
    if missing:
        return PatternEvidenceLinkAssessment(
            required=True,
            valid=False,
            linked_event_count=len(compatible),
            reason="model_inferred_pattern_invalid_event_evidence",
            linked_event_ids=compatible,
            rejected_evidence_ids=missing,
        )
    if incompatible:
        return PatternEvidenceLinkAssessment(
            required=True,
            valid=False,
            linked_event_count=len(compatible),
            reason="model_inferred_pattern_incompatible_event_evidence",
            linked_event_ids=compatible,
            rejected_evidence_ids=incompatible,
        )
    if len(compatible) < 2:
        return PatternEvidenceLinkAssessment(
            required=True,
            valid=False,
            linked_event_count=len(compatible),
            reason="model_inferred_pattern_insufficient_event_evidence",
            linked_event_ids=compatible,
        )
    return PatternEvidenceLinkAssessment(
        required=True,
        valid=True,
        linked_event_count=len(compatible),
        reason="model_inferred_pattern_event_evidence_valid",
        linked_event_ids=compatible,
    )


def _pattern_event_evidence_compatible(
    pattern: MemoryCandidate,
    event: MemoryItem,
) -> bool:
    if not is_epistemically_confirmed(event):
        return False
    evidence_subject = pattern.payload.get("evidence_subject")
    if (
        isinstance(evidence_subject, str)
        and evidence_subject.strip()
        and evidence_subject.casefold() != event.subject.casefold()
    ):
        return False
    expected_participants = _participants_key(pattern.payload.get("participants"))
    if expected_participants and expected_participants != _participants_key(
        event.payload.get("participants")
    ):
        return False
    if not _event_is_inside_pattern_window(pattern, event):
        return False

    metric = normalize_interaction_metric(pattern.payload.get("metric"))
    event_metric = _event_pattern_metric(event)
    if metric not in INTERACTION_PATTERN_DIMENSIONS or event_metric != metric:
        return False
    if metric == "initiation_balance":
        expected_state = interaction_pattern_state(metric, pattern.payload)
        event_direction = _event_initiation_direction(event)
        if expected_state in {"partner_to_user", "user_to_partner"}:
            return event_direction == expected_state
    return True


def _participants_key(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        sorted(
            str(item).casefold().strip()
            for item in value
            if isinstance(item, str) and item.strip()
        )
    )


def _event_is_inside_pattern_window(
    pattern: MemoryCandidate,
    event: MemoryItem,
) -> bool:
    window = pattern.payload.get("time_window")
    start = pattern.period_start
    end = pattern.period_end
    if isinstance(window, dict):
        start = start or _parse_window_boundary(window.get("start"), is_end=False)
        end = end or _parse_window_boundary(window.get("end"), is_end=True)
    if start is None and end is None:
        return False
    event_time = event.occurred_at or event.period_end or event.period_start
    if event_time is None:
        return False
    if start is not None:
        event_time, aligned_start = _align_datetimes(event_time, start)
        if event_time < aligned_start:
            return False
    if end is not None:
        event_time, aligned_end = _align_datetimes(event_time, end)
        if event_time > aligned_end:
            return False
    return True


def _parse_window_boundary(value: object, *, is_end: bool) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if is_end and len(value.strip()) == 10:
        parsed += timedelta(days=1) - timedelta(microseconds=1)
    return parsed


def _event_pattern_metric(event: MemoryItem) -> str | None:
    values = (
        event.canonical_predicate,
        event.custom_predicate,
        event.raw_predicate,
        event.payload.get("predicate"),
        event.payload.get("action"),
        event.payload.get("activity_type"),
    )
    for value in values:
        dimension = dimension_for_predicate(value)
        if dimension in INTERACTION_PATTERN_DIMENSIONS:
            return dimension
    texts = [
        str(value).casefold().replace("-", "_")
        for value in (*values, event.summary, event.original_text)
        if isinstance(value, str) and value.strip()
    ]
    if any(
        re.search(
            r"(?:initiat|started|contacted_user|messaged_user|"
            r"user_(?:contacted|messaged|invited|shared)_partner|"
            r"partner_(?:contacted|messaged|invited|shared|disclosed)|"
            r"主动.{0,10}(?:联系|找|发消息|聊天|约|邀请|分享|告诉))",
            text,
        )
        for text in texts
    ):
        return "initiation_balance"
    if any(
        re.search(r"(?:argu|quarrel|fight|conflict|吵架|争吵|争执|矛盾|冲突|冷战)", text)
        for text in texts
    ):
        return "conflict_frequency"
    if any(
        re.search(
            r"(?:disclos|opened_up|shared_(?:work|feeling|personal)|分享|告诉)",
            text,
        )
        for text in texts
    ):
        return "emotional_disclosure"
    if any(re.search(r"(?:repl(?:y|ied)|respond|回复|回应)", text) for text in texts):
        return "response_engagement"
    if any(
        re.search(r"(?:contact|messag|chat|call|meet|联系|消息|聊天|电话|见面)", text)
        for text in texts
    ):
        return "contact_frequency"
    return None


def _event_initiation_direction(event: MemoryItem) -> str | None:
    values = (
        event.payload.get("action"),
        event.payload.get("activity_type"),
        event.payload.get("predicate"),
        event.raw_predicate,
        event.custom_predicate,
        event.summary,
        event.original_text,
    )
    texts = [
        value.casefold().replace("-", "_")
        for value in values
        if isinstance(value, str) and value.strip()
    ]
    if any(
        re.search(
            r"(?:partner|she|he).{0,20}"
            r"(?:initiat|started|contact|messag|invit|shar|disclos)",
            text,
        )
        for text in texts
    ):
        return "partner_to_user"
    if any(
        re.search(
            r"(?:user|\bi\b).{0,20}"
            r"(?:initiat|started|contact|messag|invit|shar|disclos)",
            text,
        )
        for text in texts
    ):
        return "user_to_partner"
    return infer_initiation_balance(" ".join(event.evidence_spans))


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

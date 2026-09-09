from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

from loveapp.application.memory_admission import (
    assess_memory_admission,
    assess_pattern_evidence_links,
)
from loveapp.application.memory_relations import resolve_claim_relation
from loveapp.domain.memory import (
    AdmissionDecision,
    ClaimRelation,
    EpistemicStatus,
    EvidenceExplicitness,
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    PatternLifecycleState,
    PredicateType,
    TimeKind,
)
from loveapp.domain.memory_dimensions import interaction_pattern_state, normalize_interaction_metric
from loveapp.domain.memory_epistemics import is_epistemically_confirmed
from loveapp.domain.memory_normalization import (
    NormalizationContractError,
    normalize_memory_candidate_contract,
)
from loveapp.domain.memory_write import MemoryAuditDraft, MemoryWriteOperation

_CONSOLIDATION_WINDOW = timedelta(days=30)
_MIN_DISTINCT_EVENTS = 3
_MIN_CONFIRMED_EVENT_CONFIDENCE = 0.75
_MIN_PROPOSED_EVENT_CONFIDENCE = 0.85
_MAX_PATTERN_EVIDENCE_IDS = 20

_NEGATED_CONSOLIDATION_PATTERN = re.compile(
    r"(?:没有|没|未|并未|从未|不曾|不再|不(?:怎么|太|会)?).{0,12}"
    r"(?:主动|找我|找她|找他|联系|发消息|聊天|分享|告诉)|"
    r"\b(?:did\s+not|didn't|never|no\s+longer|hasn't|hadn't).{0,32}"
    r"(?:initiat|contact|messag|chat|shar|disclos|tell)\w*\b",
    re.IGNORECASE,
)
_NEGATIVE_POLARITY_VALUES = frozenset(
    {"false", "negative", "negated", "denied", "no"}
)


@dataclass(frozen=True)
class _ConsolidationRule:
    name: str
    category: str
    pattern: re.Pattern[str]
    canonical_predicate: str
    metric: str
    current: str
    summary: str
    negation_mode: str = "forbidden"

    @property
    def cluster_key(self) -> tuple[str, str]:
        return self.metric, self.current


_CONSOLIDATION_RULES = (
    _ConsolidationRule(
        name="communication_frequency_reduced",
        category="communication_frequency",
        pattern=re.compile(
            r"(?:partner_(?:did_not|stopped|refused)_(?:contact|message|chat|call|meet)|"
            r"(?:她|他|对方).{0,10}(?:没有|没|不再|拒绝|停止).{0,8}"
            r"(?:联系|发消息|聊天|通话|见面|互动)|"
            r"(?:没有|没|不再).{0,8}(?:联系|聊天|交流|见面)|"
            r"\b(?:did\s+not|didn't|stopped|refused|no\s+longer).{0,24}"
            r"(?:contact|messag|chat|call|meet|interact)\w*\b)",
            re.IGNORECASE,
        ),
        canonical_predicate="interaction.contact_frequency",
        metric="contact_frequency",
        current="low",
        summary="双方多次没有发生预期的联系或互动。",
        negation_mode="any",
    ),
    _ConsolidationRule(
        name="conflict_repair_event",
        category="conflict_trend",
        pattern=re.compile(
            r"(?:conflict_(?:resolved|repaired)|relationship_repaired|made_up|"
            r"和好|说开|矛盾.{0,6}解决|冲突.{0,6}解决|不再吵架|没有再吵)|"
            r"\b(?:reconcil|resolved|made\s+up|stopped\s+arguing)\w*\b",
            re.IGNORECASE,
        ),
        canonical_predicate="interaction.conflict_frequency",
        metric="conflict_frequency",
        current="low",
        summary="双方近期较少发生冲突。",
        negation_mode="any",
    ),
    _ConsolidationRule(
        name="conflict_occurrence",
        category="conflict_trend",
        pattern=re.compile(
            r"(?:conflict_event|argument|quarrel|fight|吵架|争吵|争执|矛盾|冲突|冷战)"
            r"|\b(?:argu|quarrel|fight|conflict)\w*\b",
            re.IGNORECASE,
        ),
        canonical_predicate="interaction.conflict_frequency",
        metric="conflict_frequency",
        current="high",
        summary="双方近期多次发生冲突。",
    ),
    _ConsolidationRule(
        name="partner_initiated_contact",
        category="interaction_initiative",
        pattern=re.compile(
            r"(?:partner_(?:initiated|started)_(?:chat|contact)|"
            r"partner_(?:contacted|messaged|invited|shared|disclosed)_user|"
            r"partner_(?:shared|disclosed)_(?:work|feelings|personal|update)|"
            r"(?:她|他|对方).{0,8}主动.{0,8}"
            r"(?:找我|联系我|发消息|聊天|约我|邀请我|分享|告诉我)|"
            r"partner.{0,12}(?:initiated|started|contacted|messaged|invited|shared))",
            re.IGNORECASE,
        ),
        canonical_predicate="interaction.initiation_balance",
        metric="initiation_balance",
        current="partner_to_user",
        summary="对方多次主动发起联系。",
    ),
    _ConsolidationRule(
        name="user_initiated_contact",
        category="interaction_initiative",
        pattern=re.compile(
            r"(?:user_(?:initiated|started)_(?:chat|contact)|"
            r"user_contacted_partner|user_messaged_partner|"
            r"我.{0,8}主动.{0,8}(?:找她|找他|联系|发消息|聊天)|"
            r"user.{0,12}(?:initiated|started|contacted|messaged))",
            re.IGNORECASE,
        ),
        canonical_predicate="interaction.initiation_balance",
        metric="initiation_balance",
        current="user_to_partner",
        summary="用户多次主动发起联系。",
    ),
    _ConsolidationRule(
        name="communication_frequency_observed",
        category="communication_frequency",
        pattern=re.compile(
            r"(?:shared_(?:chat|call|meeting)|mutual_(?:contact|chat)|"
            r"双方.{0,8}(?:联系|聊天|通话|见面|互动)|我们.{0,8}(?:聊|通话|见面)|"
            r"\b(?:we|both).{0,16}(?:chatted|called|met|talked|interacted)\b)",
            re.IGNORECASE,
        ),
        canonical_predicate="interaction.contact_frequency",
        metric="contact_frequency",
        current="high",
        summary="双方近期多次发生联系或互动。",
    ),
)


@dataclass(frozen=True)
class GovernedPatternConsolidation:
    operation: MemoryWriteOperation | None = None
    audit: MemoryAuditDraft | None = None


@dataclass(frozen=True)
class GovernedPatternEvolution:
    handled: bool = False
    status: str = "not_applicable"
    operations: tuple[MemoryWriteOperation, ...] = ()
    audits: tuple[MemoryAuditDraft, ...] = ()
    pattern_memory_ids: tuple[str, ...] = ()
    positive_evidence_ids: tuple[str, ...] = ()
    negative_evidence_ids: tuple[str, ...] = ()


def govern_interaction_pattern_consolidation(
    trigger: MemoryItem,
    active_memories: list[MemoryItem],
    *,
    source_text: str,
    reference_time: datetime,
) -> GovernedPatternConsolidation:
    """Run an inferred Pattern through existing normalization and admission."""

    proposed = detect_interaction_pattern_consolidation(
        trigger,
        active_memories,
        reference_time=reference_time,
    )
    if proposed is None:
        return GovernedPatternConsolidation()
    try:
        candidate = normalize_memory_candidate_contract(
            proposed,
            reference_time,
            allow_legacy_open_world=True,
        )
    except NormalizationContractError as exc:
        return GovernedPatternConsolidation(
            audit=MemoryAuditDraft(
                relation=ClaimRelation.UNCERTAIN,
                decision=AdmissionDecision.REJECT,
                rule_name="event_pattern_consolidation_rejected",
                score_breakdown={
                    "normalization_contract_error": exc.code,
                    "normalization_contract_detail": exc.detail,
                },
                raw_predicate=proposed.raw_predicate,
                canonical_predicate=proposed.canonical_predicate,
                evidence=proposed.evidence_spans,
                reason=str(exc),
            )
        )
    links = assess_pattern_evidence_links(candidate, active_memories)
    assessment = assess_memory_admission(
        candidate,
        source_text,
        corroborating_evidence_count=links.linked_event_count,
        pattern_evidence_links=links,
    )
    if assessment.decision == AdmissionDecision.REJECT:
        return GovernedPatternConsolidation(
            audit=MemoryAuditDraft(
                relation=ClaimRelation.UNCERTAIN,
                decision=AdmissionDecision.REJECT,
                rule_name="event_pattern_consolidation_rejected",
                admission_score=assessment.score,
                score_breakdown=assessment.score_breakdown,
                raw_predicate=candidate.raw_predicate,
                canonical_predicate=candidate.canonical_predicate,
                evidence=candidate.evidence_spans,
                reason=assessment.reason,
            )
        )
    resolution = resolve_claim_relation(
        candidate,
        active_memories,
        incoming_status=MemoryStatus.PROPOSED,
    )
    governed = candidate.model_copy(
        update={
            "admission_score": assessment.score,
            "admission_decision": assessment.decision,
            "claim_relation": resolution.relation,
            "lifecycle_review_required": (
                candidate.lifecycle_review_required
                or resolution.relation.value in {"contradiction", "uncertain"}
            ),
        }
    )
    return GovernedPatternConsolidation(
        operation=MemoryWriteOperation(
            candidate=governed,
            status=MemoryStatus.PROPOSED,
            relation=resolution.relation,
            target_memory_ids=list(resolution.target_memory_ids),
            rule_name="event_pattern_consolidation",
            reason="Repeated compatible interaction Events support a bounded inferred Pattern.",
            score_breakdown={
                **assessment.score_breakdown,
                "evidence_event_ids": list(links.linked_event_ids),
                "relation_rule": resolution.rule_name,
            },
        )
    )


def govern_interaction_pattern_evolution(
    trigger: MemoryItem,
    active_memories: list[MemoryItem],
    *,
    source_text: str,
    reference_time: datetime,
) -> GovernedPatternEvolution:
    """Apply one Event as positive or negative evidence to one inferred Pattern.

    The resolver is deliberately single-target and model-inferred-only.  If
    more than one active Pattern could receive the Event, evolution fails
    closed instead of letting filtering manufacture a unique target.
    """

    if trigger.kind != MemoryKind.INTERACTION_EVENT or not _eligible_event(trigger):
        return GovernedPatternEvolution()
    rule = _matching_rule(trigger)
    if rule is None:
        return GovernedPatternEvolution()
    candidates = [
        item
        for item in active_memories
        if item.kind == MemoryKind.INTERACTION_PATTERN
        and item.status in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
        and item.perspective == MemoryPerspective.MODEL_INFERRED
        and item.pattern_state != PatternLifecycleState.SUPERSEDED
        and normalize_interaction_metric(item.payload.get("metric")) == rule.metric
        and _participants_are_compatible(item, trigger)
    ]
    if len(candidates) > 1:
        target_ids = tuple(item.id for item in candidates)
        return GovernedPatternEvolution(
            handled=True,
            status="ambiguous_pattern_target",
            audits=(
                MemoryAuditDraft(
                    relation=ClaimRelation.UNCERTAIN,
                    decision=AdmissionDecision.PROPOSE,
                    target_memory_ids=list(target_ids),
                    rule_name="interaction_pattern_evolution_ambiguous",
                    evidence=list(trigger.evidence_spans),
                    reason=(
                        "Multiple active inferred Patterns share the Event metric; "
                        "Pattern evolution failed closed."
                    ),
                ),
            ),
            pattern_memory_ids=target_ids,
        )
    if not candidates:
        return GovernedPatternEvolution()

    pattern = candidates[0]
    current = interaction_pattern_state(rule.metric, pattern.payload)
    if current == rule.current:
        update = _pattern_evidence_update(
            pattern,
            trigger,
            positive=True,
            reference_time=reference_time,
        )
        return GovernedPatternEvolution(
            handled=True,
            status="positive_evidence",
            operations=(
                MemoryWriteOperation(
                    candidate=update,
                    status=pattern.status,
                    relation=ClaimRelation.SAME,
                    target_memory_ids=[pattern.id],
                    rule_name="interaction_pattern_positive_evidence",
                    reason="A compatible Event adds positive evidence to the active Pattern.",
                    score_breakdown={
                        "pattern_memory_id": pattern.id,
                        "evidence_event_id": trigger.id,
                        "pattern_state": PatternLifecycleState.ACTIVE.value,
                    },
                ),
            ),
            pattern_memory_ids=(pattern.id,),
            positive_evidence_ids=tuple(update.positive_evidence_ids),
            negative_evidence_ids=tuple(update.negative_evidence_ids),
        )
    if not _pattern_states_are_opposed(rule.metric, current, rule.current):
        return GovernedPatternEvolution(
            handled=True,
            status="incompatible_pattern_state",
            pattern_memory_ids=(pattern.id,),
        )

    weakening = _pattern_evidence_update(
        pattern,
        trigger,
        positive=False,
        reference_time=reference_time,
    )
    operations = [
        MemoryWriteOperation(
            candidate=weakening,
            status=pattern.status,
            relation=ClaimRelation.SAME,
            target_memory_ids=[pattern.id],
            rule_name="interaction_pattern_negative_evidence",
            reason="An opposite Event weakens the active Pattern without immediately replacing it.",
            score_breakdown={
                "pattern_memory_id": pattern.id,
                "evidence_event_id": trigger.id,
                "pattern_state": PatternLifecycleState.WEAKENING.value,
            },
        )
    ]
    reversal_evidence = _reversal_evidence(
        weakening,
        trigger,
        active_memories,
        rule=rule,
        reference_time=reference_time,
    )
    status = "negative_evidence"
    audits: list[MemoryAuditDraft] = []
    pattern_ids = [pattern.id]
    if len(reversal_evidence) >= 2:
        reversal = _build_pattern_candidate(
            rule,
            reversal_evidence,
            trigger=trigger,
            reference_time=reference_time,
        )
        governed, rejection = _govern_reversal_candidate(
            reversal,
            active_memories,
            source_text=source_text,
            target=pattern,
            reference_time=reference_time,
        )
        if governed is not None:
            operations.append(governed)
            status = (
                "superseded_by_reversal"
                if governed.relation == ClaimRelation.UPDATE
                else "reversal_proposed_confirmed_protected"
            )
        elif rejection is not None:
            audits.append(rejection)
            status = "reversal_rejected"

    return GovernedPatternEvolution(
        handled=True,
        status=status,
        operations=tuple(operations),
        audits=tuple(audits),
        pattern_memory_ids=tuple(pattern_ids),
        positive_evidence_ids=tuple(weakening.positive_evidence_ids),
        negative_evidence_ids=tuple(weakening.negative_evidence_ids),
    )


def _pattern_evidence_update(
    pattern: MemoryItem,
    trigger: MemoryItem,
    *,
    positive: bool,
    reference_time: datetime,
) -> MemoryCandidate:
    data = {field: getattr(pattern, field) for field in MemoryCandidate.model_fields}
    payload = dict(pattern.payload)
    positive_ids = list(pattern.positive_evidence_ids or payload.get("evidence_ids") or [])
    negative_ids = list(pattern.negative_evidence_ids)
    if positive:
        positive_ids = _append_evidence_id(positive_ids, trigger.id)
        state = PatternLifecycleState.ACTIVE
        confidence = min(pattern.confidence + 0.04, 0.98)
        payload["last_positive_evidence_id"] = trigger.id
        payload["pattern_evolution_rule"] = "positive_evidence"
    else:
        negative_ids = _append_evidence_id(negative_ids, trigger.id)
        state = PatternLifecycleState.WEAKENING
        confidence = max(pattern.confidence - 0.12, 0.25)
        payload["last_negative_evidence_id"] = trigger.id
        payload["pattern_evolution_rule"] = "negative_evidence"
    payload.update(
        {
            "evidence_ids": positive_ids,
            "positive_evidence_ids": positive_ids,
            "negative_evidence_ids": negative_ids,
            "state": state.value,
            "pattern_evolution_update": True,
            "time_window": _expanded_pattern_window(pattern, trigger, reference_time),
        }
    )
    evidence_span = next(
        (span for span in trigger.evidence_spans if span and span in trigger.original_text),
        trigger.original_text,
    )
    data.update(
        {
            "original_text": trigger.original_text,
            "evidence_spans": [evidence_span],
            "period_start": _window_start(pattern, trigger, reference_time),
            "period_end": _window_end(pattern, trigger, reference_time),
            "confidence": round(confidence, 4),
            "payload": payload,
            "claim_relation": ClaimRelation.SAME,
            "pattern_state": state,
            "positive_evidence_ids": positive_ids,
            "negative_evidence_ids": negative_ids,
        }
    )
    return MemoryCandidate.model_validate(data)


def _govern_reversal_candidate(
    candidate: MemoryCandidate,
    active_memories: list[MemoryItem],
    *,
    source_text: str,
    target: MemoryItem,
    reference_time: datetime,
) -> tuple[MemoryWriteOperation | None, MemoryAuditDraft | None]:
    try:
        normalized = normalize_memory_candidate_contract(
            candidate,
            reference_time,
            allow_legacy_open_world=True,
        )
    except NormalizationContractError as exc:
        return None, MemoryAuditDraft(
            relation=ClaimRelation.UNCERTAIN,
            decision=AdmissionDecision.REJECT,
            target_memory_ids=[target.id],
            rule_name="interaction_pattern_reversal_rejected",
            raw_predicate=candidate.raw_predicate,
            canonical_predicate=candidate.canonical_predicate,
            evidence=list(candidate.evidence_spans),
            score_breakdown={"normalization_contract_error": exc.code},
            reason=str(exc),
        )
    links = assess_pattern_evidence_links(normalized, active_memories)
    assessment = assess_memory_admission(
        normalized,
        source_text,
        corroborating_evidence_count=links.linked_event_count,
        pattern_evidence_links=links,
    )
    if assessment.decision == AdmissionDecision.REJECT:
        return None, MemoryAuditDraft(
            relation=ClaimRelation.UNCERTAIN,
            decision=AdmissionDecision.REJECT,
            target_memory_ids=[target.id],
            rule_name="interaction_pattern_reversal_rejected",
            admission_score=assessment.score,
            score_breakdown=assessment.score_breakdown,
            raw_predicate=normalized.raw_predicate,
            canonical_predicate=normalized.canonical_predicate,
            evidence=list(normalized.evidence_spans),
            reason=assessment.reason,
        )
    relation = (
        ClaimRelation.UPDATE
        if target.status == MemoryStatus.PROPOSED
        else ClaimRelation.CONTRADICTION
    )
    target_ids = [target.id] if relation == ClaimRelation.UPDATE else []
    governed = normalized.model_copy(
        update={
            "admission_score": assessment.score,
            "admission_decision": assessment.decision,
            "claim_relation": relation,
            "lifecycle_review_required": relation != ClaimRelation.UPDATE,
        }
    )
    return MemoryWriteOperation(
        candidate=governed,
        status=MemoryStatus.PROPOSED,
        relation=relation,
        target_memory_ids=target_ids,
        rule_name="interaction_pattern_reversal",
        reason=(
            "Repeated opposite Events establish a new Pattern and supersede the old inferred "
            "Pattern."
            if relation == ClaimRelation.UPDATE
            else "A proposed inferred reversal cannot supersede a confirmed Pattern."
        ),
        score_breakdown={
            **assessment.score_breakdown,
            "evidence_event_ids": list(links.linked_event_ids),
            "protected_confirmed_target": relation != ClaimRelation.UPDATE,
        },
    ), None
def detect_interaction_pattern_consolidation(
    trigger: MemoryItem,
    active_memories: list[MemoryItem],
    *,
    reference_time: datetime,
) -> MemoryCandidate | None:
    """Build one conservative, evidence-linked Pattern from repeated Events."""

    if trigger.kind != MemoryKind.INTERACTION_EVENT:
        return None
    rule = _matching_rule(trigger)
    trigger_time = _event_time(trigger)
    if rule is None or trigger_time is None or not _eligible_event(trigger):
        return None

    lower_bound = reference_time - _CONSOLIDATION_WINDOW
    aligned_trigger_time = _align_time(trigger_time, reference_time)
    if not lower_bound <= aligned_trigger_time <= reference_time:
        return None
    cluster = [
        item
        for item in active_memories
        if item.kind == MemoryKind.INTERACTION_EVENT
        and item.subject.casefold() == trigger.subject.casefold()
        and _participants_key(item) == _participants_key(trigger)
        and (item_rule := _matching_rule(item)) is not None
        and item_rule.cluster_key == rule.cluster_key
        and _eligible_event(item)
        and (timestamp := _event_time(item)) is not None
        and lower_bound <= _align_time(timestamp, reference_time) <= reference_time
    ]
    if not any(item.id == trigger.id for item in cluster):
        return None

    trigger_occurrence = _event_occurrence_identity(trigger, reference_time)
    if trigger_occurrence is None:
        return None
    if any(
        item.id != trigger.id
        and _event_occurrence_identity(item, reference_time) == trigger_occurrence
        for item in cluster
    ):
        # A new source message describing an already represented occurrence is
        # corroboration of that Event, not evidence of another recurrence.
        return None

    by_occurrence: dict[str, MemoryItem] = {}
    for item in sorted(cluster, key=lambda value: _event_time(value) or reference_time):
        occurrence_id = _event_occurrence_identity(item, reference_time)
        if occurrence_id is None:
            continue
        by_occurrence[occurrence_id] = item
    evidence = list(by_occurrence.values())
    if len(evidence) < _MIN_DISTINCT_EVENTS:
        return None

    evidence.sort(key=lambda value: _event_time(value) or reference_time)
    if len(evidence) > _MAX_PATTERN_EVIDENCE_IDS:
        retained = evidence[-_MAX_PATTERN_EVIDENCE_IDS:]
        if all(item.id != trigger.id for item in retained):
            retained = [trigger, *evidence[-(_MAX_PATTERN_EVIDENCE_IDS - 1) :]]
            retained.sort(key=lambda value: _event_time(value) or reference_time)
        evidence = retained
    if not any(item.id == trigger.id for item in evidence):
        return None
    start = _event_time(evidence[0])
    end = _event_time(evidence[-1])
    if start is None or end is None:
        return None
    aligned_start = _align_time(start, reference_time)
    aligned_end = _align_time(end, reference_time)
    if aligned_start >= aligned_end:
        return None
    return _build_pattern_candidate(
        rule,
        evidence,
        trigger=trigger,
        reference_time=reference_time,
    )


def _build_pattern_candidate(
    rule: _ConsolidationRule,
    evidence: list[MemoryItem],
    *,
    trigger: MemoryItem,
    reference_time: datetime,
) -> MemoryCandidate:
    evidence = sorted(evidence, key=lambda value: _event_time(value) or reference_time)
    start = _event_time(evidence[0])
    end = _event_time(evidence[-1])
    if start is None or end is None:
        raise ValueError("pattern evidence must contain bounded event times")
    confidence = round(min(0.98, sum(item.confidence for item in evidence) / len(evidence)), 4)
    evidence_span = next(
        (span for span in trigger.evidence_spans if span and span in trigger.original_text),
        trigger.original_text,
    )
    payload = {
        "predicate": rule.canonical_predicate,
        "metric": rule.metric,
        "current": rule.current,
        "source": MemoryPerspective.MODEL_INFERRED.value,
        "evidence_ids": [item.id for item in evidence],
        "positive_evidence_ids": [item.id for item in evidence],
        "negative_evidence_ids": [],
        "state": PatternLifecycleState.ACTIVE.value,
        "time_window": {
            "start": _align_time(start, reference_time).isoformat(),
            "end": _align_time(end, reference_time).isoformat(),
        },
        "consolidation_rule": rule.name,
        "consolidation_category": rule.category,
        "evidence_subject": trigger.subject,
        "participants": list(_participants_key(trigger)),
    }
    return MemoryCandidate(
        kind=MemoryKind.INTERACTION_PATTERN,
        subject=trigger.subject,
        summary=rule.summary,
        original_text=trigger.original_text,
        evidence_spans=[evidence_span],
        time_kind=TimeKind.INTERVAL,
        period_start=_align_time(start, reference_time),
        period_end=_align_time(end, reference_time),
        importance=max(item.importance for item in evidence),
        perspective=MemoryPerspective.MODEL_INFERRED,
        epistemic_status=EpistemicStatus.HYPOTHESIS,
        confidence=confidence,
        payload=payload,
        raw_predicate=rule.canonical_predicate,
        predicate_type=PredicateType.CANONICAL,
        canonical_predicate=rule.canonical_predicate,
        state_dimension=rule.canonical_predicate,
        state_value=rule.current,
        explicitness=EvidenceExplicitness.STRONGLY_IMPLIED,
        requires_inference=True,
        pattern_state=PatternLifecycleState.ACTIVE,
        positive_evidence_ids=[item.id for item in evidence],
    )


def _reversal_evidence(
    weakening: MemoryCandidate,
    trigger: MemoryItem,
    active_memories: list[MemoryItem],
    *,
    rule: _ConsolidationRule,
    reference_time: datetime,
) -> list[MemoryItem]:
    negative_ids = set(weakening.negative_evidence_ids)
    eligible = [
        item
        for item in active_memories
        if item.id in negative_ids
        and item.kind == MemoryKind.INTERACTION_EVENT
        and item.subject.casefold() == trigger.subject.casefold()
        and _participants_key(item) == _participants_key(trigger)
        and (item_rule := _matching_rule(item)) is not None
        and item_rule.cluster_key == rule.cluster_key
        and _eligible_event(item)
        and _event_time(item) is not None
    ]
    by_occurrence: dict[str, MemoryItem] = {}
    for item in sorted(eligible, key=lambda value: _event_time(value) or reference_time):
        occurrence_id = _event_occurrence_identity(item, reference_time)
        if occurrence_id is not None:
            by_occurrence[occurrence_id] = item
    evidence = list(by_occurrence.values())
    if len(evidence) < 2:
        return []
    start = _event_time(evidence[0])
    end = _event_time(evidence[-1])
    if start is None or end is None:
        return []
    if _align_time(start, reference_time) >= _align_time(end, reference_time):
        return []
    return evidence[-_MAX_PATTERN_EVIDENCE_IDS:]


def _participants_are_compatible(pattern: MemoryCandidate, event: MemoryCandidate) -> bool:
    expected = _participants_key(pattern)
    actual = _participants_key(event)
    return not expected or expected == actual


def _pattern_states_are_opposed(
    metric: str,
    current: str | None,
    incoming: str,
) -> bool:
    if current is None or current == incoming:
        return False
    if metric == "initiation_balance":
        return {current, incoming} == {"partner_to_user", "user_to_partner"}
    positive = {"high", "increasing", "frequent", "normal", "restored", "responsive"}
    negative = {"low", "decreasing", "rare", "reduced", "unavailable", "unresponsive"}
    return (current in positive and incoming in negative) or (
        current in negative and incoming in positive
    )


def _append_evidence_id(values: list[str], memory_id: str) -> list[str]:
    return list(dict.fromkeys([*values, memory_id]))[-_MAX_PATTERN_EVIDENCE_IDS:]


def _expanded_pattern_window(
    pattern: MemoryCandidate,
    trigger: MemoryCandidate,
    reference_time: datetime,
) -> dict[str, str]:
    start = _window_start(pattern, trigger, reference_time)
    end = _window_end(pattern, trigger, reference_time)
    return {"start": start.isoformat(), "end": end.isoformat()}


def _window_start(
    pattern: MemoryCandidate,
    trigger: MemoryCandidate,
    reference_time: datetime,
) -> datetime:
    values = [
        value
        for value in (
            pattern.period_start,
            pattern.occurred_at,
            _event_time(trigger),
        )
        if value is not None
    ]
    if not values:
        return reference_time
    return min(_align_time(value, reference_time) for value in values)


def _window_end(
    pattern: MemoryCandidate,
    trigger: MemoryCandidate,
    reference_time: datetime,
) -> datetime:
    values = [
        value
        for value in (
            pattern.period_end,
            pattern.occurred_at,
            _event_time(trigger),
        )
        if value is not None
    ]
    if not values:
        return reference_time
    return max(_align_time(value, reference_time) for value in values)


def _matching_rule(memory: MemoryCandidate) -> _ConsolidationRule | None:
    negated = _is_negated_event(memory)
    payload = memory.payload
    structured_values = [
        payload.get("action"),
        payload.get("activity_type"),
        payload.get("predicate"),
        memory.raw_predicate,
        memory.custom_predicate,
    ]
    structured_texts = [
        _normalize_identifier(value)
        for value in structured_values
        if isinstance(value, str) and value.strip()
    ]
    matches = [
        rule
        for rule in _CONSOLIDATION_RULES
        if any(rule.pattern.search(value) for value in structured_texts)
        and (rule.negation_mode != "forbidden" or not negated)
        and (rule.negation_mode != "required" or negated)
    ]
    if matches:
        return matches[0]

    evidence_texts = [
        _normalize_identifier(value)
        for value in (memory.summary, memory.original_text)
        if isinstance(value, str) and value.strip()
    ]
    matches = [
        rule
        for rule in _CONSOLIDATION_RULES
        if any(rule.pattern.search(value) for value in evidence_texts)
        and (rule.negation_mode != "forbidden" or not negated)
        and (rule.negation_mode != "required" or negated)
    ]
    return matches[0] if matches else None


def _eligible_event(item: MemoryItem) -> bool:
    if not is_epistemically_confirmed(item):
        return False
    if item.requires_inference or item.explicitness == EvidenceExplicitness.SPECULATIVE:
        return False
    if item.status == MemoryStatus.CONFIRMED:
        return item.confidence >= _MIN_CONFIRMED_EVENT_CONFIDENCE
    return (
        item.status == MemoryStatus.PROPOSED
        and item.confidence >= _MIN_PROPOSED_EVENT_CONFIDENCE
        and item.explicitness == EvidenceExplicitness.EXPLICIT
    )


def _event_time(memory: MemoryCandidate) -> datetime | None:
    return memory.occurred_at or memory.period_end or memory.period_start


def _event_occurrence_identity(
    memory: MemoryCandidate,
    reference_time: datetime,
) -> str | None:
    event_id = memory.payload.get("event_id")
    if isinstance(event_id, str) and event_id.strip():
        return f"event:{_normalize_identifier(event_id)}"
    if memory.occurred_at is not None:
        return "point:" + _occurrence_time_bucket(
            memory.occurred_at,
            reference_time,
            precision=memory.temporal_precision.value,
        )
    if memory.period_start is None and memory.period_end is None:
        return None
    start = (
        _occurrence_time_bucket(
            memory.period_start,
            reference_time,
            precision=memory.temporal_precision.value,
        )
        if memory.period_start is not None
        else ""
    )
    end = (
        _occurrence_time_bucket(
            memory.period_end,
            reference_time,
            precision=memory.temporal_precision.value,
        )
        if memory.period_end is not None
        else ""
    )
    return f"interval:{start}/{end}"


def _occurrence_time_bucket(
    value: datetime,
    reference_time: datetime,
    *,
    precision: str,
) -> str:
    aligned = _align_time(value, reference_time)
    if precision == "exact":
        return aligned.isoformat()
    if precision == "month":
        return aligned.strftime("%Y-%m")
    if precision == "week":
        return (aligned.date() - timedelta(days=aligned.weekday())).isoformat()
    # Day/approximate/unknown timestamps cannot safely distinguish multiple
    # occurrences inside the same calendar day without a stable event_id.
    return aligned.date().isoformat()


def _is_negated_event(memory: MemoryCandidate) -> bool:
    if memory.payload.get("negated") is True or memory.payload.get("is_negated") is True:
        return True
    polarity = memory.payload.get("polarity")
    if (
        isinstance(polarity, str)
        and _normalize_identifier(polarity) in _NEGATIVE_POLARITY_VALUES
    ):
        return True
    evidence = " ".join(
        value.replace("_", " ")
        for value in (
            memory.payload.get("action"),
            memory.payload.get("activity_type"),
            memory.payload.get("predicate"),
            memory.raw_predicate,
            memory.custom_predicate,
            memory.summary,
            memory.original_text,
            *memory.evidence_spans,
        )
        if isinstance(value, str) and value.strip()
    )
    return bool(_NEGATED_CONSOLIDATION_PATTERN.search(evidence))


def _participants_key(memory: MemoryCandidate) -> tuple[str, ...]:
    participants = memory.payload.get("participants")
    if not isinstance(participants, (list, tuple)):
        return ()
    return tuple(
        sorted(
            _normalize_identifier(item)
            for item in participants
            if isinstance(item, str) and item.strip()
        )
    )


def _normalize_identifier(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"[\s-]+", "_", normalized)


def _align_time(value: datetime, reference: datetime) -> datetime:
    if value.tzinfo is None and reference.tzinfo is not None:
        return value.replace(tzinfo=reference.tzinfo)
    if value.tzinfo is not None and reference.tzinfo is None:
        return value.replace(tzinfo=None)
    return value

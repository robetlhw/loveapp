"""Read-only long-tail semantic relation proposal and validation."""

from __future__ import annotations

import json
from datetime import datetime

from loveapp.domain.memory import (
    AdmissionDecision,
    ClaimRelation,
    EvidenceExplicitness,
    MemoryCandidate,
    MemoryItem,
    MemoryPerspective,
    MemoryStatus,
    PredicateType,
    utc_now,
)
from loveapp.domain.memory_lifecycle import MemoryRole, memory_role
from loveapp.domain.memory_semantic_relation import (
    LongTailCandidateMatch,
    LongTailRelationShadowResult,
    LongTailRelationValidation,
    SemanticRelationProposal,
)
from loveapp.ports.memory import SemanticRelationJudge
from loveapp.ports.observability import TraceRecorder

from .memory_retrieval import HybridMemoryRetriever, MemoryRetrievalMode, RetrievedMemory

_ACTIVE_STATUSES = {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
_TARGETED_RELATIONS = {
    ClaimRelation.SAME,
    ClaimRelation.UPDATE,
    ClaimRelation.CONTRADICTION,
    ClaimRelation.COMPLEMENTARY,
}
_DESTRUCTIVE_ROLES = {
    MemoryRole.CURRENT_STATE,
    MemoryRole.INTERACTION_PATTERN,
    MemoryRole.STABLE_PROFILE,
}
_SUBJECT_ALIASES = {
    "partner": "partner",
    "relationship_partner": "partner",
    "she": "partner",
    "he": "partner",
    "ta": "partner",
    "她": "partner",
    "他": "partner",
    "对方": "partner",
    "伴侣": "partner",
    "relationship": "relationship",
    "couple": "relationship",
    "we": "relationship",
    "我们": "relationship",
    "双方": "relationship",
    "user": "user",
    "我": "user",
}


class LongTailRelationCandidateRetriever:
    """Retrieve a small custom-memory set without semantic deduplication."""

    def __init__(
        self,
        retriever: HybridMemoryRetriever,
        *,
        limit: int = 5,
    ) -> None:
        if limit < 1 or limit > 10:
            raise ValueError("long-tail candidate limit must be between 1 and 10")
        self._retriever = retriever
        self._limit = limit

    async def retrieve(
        self,
        incoming: MemoryCandidate,
        memories: list[MemoryItem],
        *,
        user_id: str,
        relationship_id: str,
        reference_time: datetime,
    ) -> list[RetrievedMemory]:
        eligible = [
            item
            for item in memories
            if item.user_id == user_id
            and item.relationship_id == relationship_id
            and item.status in _ACTIVE_STATUSES
            and item.predicate_type == PredicateType.CUSTOM
            and not _is_expired(item, reference_time)
        ]
        if not eligible:
            return []
        broad = await self._retriever.retrieve(
            eligible,
            query=_relation_query(incoming),
            limit=max(self._limit * 4, 20),
            reference_time=reference_time,
            mode=MemoryRetrievalMode.CURRENT,
            preserve_candidates=True,
            require_relevance=False,
        )
        broad.sort(key=lambda result: _relation_rank(incoming, result))
        return broad[: self._limit]


class LongTailSemanticRelationValidator:
    """Validate a semantic proposal without producing a write operation."""

    def __init__(
        self,
        *,
        proposal_confidence_threshold: float = 0.9,
        incoming_confidence_threshold: float = 0.75,
        admission_score_threshold: float = 0.65,
    ) -> None:
        self._proposal_confidence_threshold = proposal_confidence_threshold
        self._incoming_confidence_threshold = incoming_confidence_threshold
        self._admission_score_threshold = admission_score_threshold

    def validate(
        self,
        proposal: SemanticRelationProposal,
        *,
        incoming: MemoryCandidate,
        retrieved: list[MemoryItem],
        user_id: str,
        relationship_id: str,
        incoming_status: MemoryStatus,
        incoming_source_message_id: str | None,
        reference_time: datetime,
    ) -> LongTailRelationValidation:
        by_id = {item.id: item for item in retrieved}
        target_ids = proposal.target_memory_ids
        targets = [by_id[memory_id] for memory_id in target_ids if memory_id in by_id]
        relation_requires_target = proposal.relation in _TARGETED_RELATIONS
        unique_target_ids = len(set(target_ids)) == len(target_ids)
        exact_target_count = (
            len(target_ids) == 1 if relation_requires_target else len(target_ids) == 0
        )
        all_targets_found = len(targets) == len(target_ids) and (
            bool(targets) or not relation_requires_target
        )

        def targets_satisfy(check) -> bool:
            if not all_targets_found:
                return False
            if not targets:
                return not relation_requires_target
            return all(check(item) for item in targets)

        checks = {
            "incoming_custom": incoming.predicate_type == PredicateType.CUSTOM,
            "target_count_within_bounds": exact_target_count,
            "unique_target": unique_target_ids,
            "target_exists_in_retrieved_set": all_targets_found,
            "same_scope": targets_satisfy(
                lambda item: (
                    item.user_id == user_id
                    and item.relationship_id == relationship_id
                )
            ),
            "target_active": targets_satisfy(
                lambda item: item.status in _ACTIVE_STATUSES
            ),
            "target_not_expired": targets_satisfy(
                lambda item: not _is_expired(item, reference_time)
            ),
            "subject_compatible": targets_satisfy(
                lambda item: (
                    _subject_key(item.subject) == _subject_key(incoming.subject)
                )
            ),
            "kind_compatible": targets_satisfy(
                lambda item: item.kind == incoming.kind
            ),
            "same_semantic_dimension": proposal.same_semantic_dimension,
            "proposal_confidence_sufficient": (
                proposal.confidence >= self._proposal_confidence_threshold
            ),
            "incoming_confidence_sufficient": (
                incoming.confidence >= self._incoming_confidence_threshold
            ),
            "incoming_evidence_sufficient": (
                incoming.explicitness == EvidenceExplicitness.EXPLICIT
                and bool(incoming.evidence_spans)
                and not incoming.requires_inference
                and incoming.perspective == MemoryPerspective.USER_REPORTED
            ),
            "admission_sufficient": (
                incoming_status == MemoryStatus.CONFIRMED
                and incoming.admission_decision == AdmissionDecision.CONFIRM
                and incoming.admission_score is not None
                and incoming.admission_score >= self._admission_score_threshold
            ),
            "perspective_protection": targets_satisfy(
                lambda item: (
                    _perspective_rank(incoming.perspective)
                    >= _perspective_rank(item.perspective)
                )
            ),
            "temporal_evidence_available": targets_satisfy(
                lambda item: _has_temporal_evidence(incoming, item)
            ),
            "temporal_order_plausible": targets_satisfy(
                lambda item: _temporal_order_plausible(incoming, item)
            ),
            "event_pattern_state_protection": targets_satisfy(
                lambda item: memory_role(incoming) == memory_role(item)
            ),
            "destructive_role_eligible": (
                memory_role(incoming) in _DESTRUCTIVE_ROLES
                and targets_satisfy(
                    lambda item: memory_role(item) in _DESTRUCTIVE_ROLES
                )
            ),
            "confirmed_protection": targets_satisfy(
                lambda item: (
                    item.status != MemoryStatus.CONFIRMED
                    or incoming_status == MemoryStatus.CONFIRMED
                )
            ),
            "source_message_is_distinct": targets_satisfy(
                lambda item: (
                    incoming_source_message_id is None
                    or item.source_message_id is None
                    or item.source_message_id != incoming_source_message_id
                )
            ),
        }
        base_checks = {
            "incoming_custom",
            "target_count_within_bounds",
            "unique_target",
            "target_exists_in_retrieved_set",
            "same_scope",
            "target_active",
            "target_not_expired",
            "source_message_is_distinct",
        }
        destructive_checks = base_checks | {
            "subject_compatible",
            "kind_compatible",
            "same_semantic_dimension",
            "proposal_confidence_sufficient",
            "incoming_confidence_sufficient",
            "incoming_evidence_sufficient",
            "admission_sufficient",
            "perspective_protection",
            "temporal_evidence_available",
            "temporal_order_plausible",
            "event_pattern_state_protection",
            "destructive_role_eligible",
            "confirmed_protection",
        }
        targeted_checks = base_checks | {"subject_compatible"}
        same_checks = targeted_checks | {
            "kind_compatible",
            "same_semantic_dimension",
            "event_pattern_state_protection",
        }
        if proposal.relation == ClaimRelation.UPDATE:
            required = destructive_checks
        elif proposal.relation == ClaimRelation.SAME:
            required = same_checks
        elif relation_requires_target:
            required = targeted_checks
        else:
            required = base_checks
        failed = [name for name in sorted(required) if not checks[name]]
        validator_pass = not failed
        would_update = validator_pass and proposal.relation == ClaimRelation.UPDATE
        reasons = [f"failed:{name}" for name in failed]
        if would_update:
            reasons.append("validated_shadow_update")
        elif validator_pass:
            reasons.append("non_destructive_relation")
        else:
            reasons.append("fail_closed")
        return LongTailRelationValidation(
            validator_pass=validator_pass,
            validated_relation=(
                proposal.relation if validator_pass else ClaimRelation.UNCERTAIN
            ),
            validator_reasons=reasons,
            checks=checks,
            would_update=would_update,
            would_supersede_memory_ids=target_ids if would_update else [],
        )


class LongTailRelationShadowEvaluator:
    """Run Phase 2A/2B while keeping the production write path unchanged."""

    def __init__(
        self,
        judge: SemanticRelationJudge,
        *,
        retriever: HybridMemoryRetriever | None = None,
        validator: LongTailSemanticRelationValidator | None = None,
        candidate_limit: int = 5,
    ) -> None:
        self._judge = judge
        self._candidate_retriever = LongTailRelationCandidateRetriever(
            retriever or HybridMemoryRetriever(),
            limit=candidate_limit,
        )
        self._validator = validator or LongTailSemanticRelationValidator()

    async def evaluate(
        self,
        *,
        incoming: MemoryCandidate,
        existing_memories: list[MemoryItem],
        user_id: str,
        relationship_id: str,
        incoming_status: MemoryStatus,
        incoming_source_message_id: str | None = None,
        reference_time: datetime | None = None,
        trace: TraceRecorder | None = None,
        candidate_index: int | None = None,
    ) -> LongTailRelationShadowResult:
        now = reference_time or utc_now()
        retrieved = await self._candidate_retriever.retrieve(
            incoming,
            existing_memories,
            user_id=user_id,
            relationship_id=relationship_id,
            reference_time=now,
        )
        _record_retrieval_trace(trace, incoming, retrieved, candidate_index)
        if retrieved:
            try:
                proposal = await self._judge.propose_relation(
                    incoming=incoming,
                    candidates=[result.item for result in retrieved],
                    trace=trace,
                )
                judge_status = "completed"
                judge_error_type = None
            except Exception as exc:
                proposal = SemanticRelationProposal(
                    relation=ClaimRelation.UNCERTAIN,
                    target_memory_ids=[],
                    same_semantic_dimension=False,
                    confidence=0,
                    reason="Semantic relation judge failed closed.",
                )
                judge_status = "failed"
                judge_error_type = type(exc).__name__
        else:
            proposal = SemanticRelationProposal(
                relation=ClaimRelation.UNCERTAIN,
                target_memory_ids=[],
                same_semantic_dimension=False,
                confidence=1,
                reason="No eligible custom relation candidates were retrieved.",
            )
            judge_status = "not_called"
            judge_error_type = None
        _record_proposal_trace(
            trace,
            proposal,
            candidate_index,
            judge_status=judge_status,
            judge_error_type=judge_error_type,
        )
        validation = self._validator.validate(
            proposal,
            incoming=incoming,
            retrieved=[result.item for result in retrieved],
            user_id=user_id,
            relationship_id=relationship_id,
            incoming_status=incoming_status,
            incoming_source_message_id=incoming_source_message_id,
            reference_time=now,
        )
        _record_validator_trace(trace, validation, candidate_index)
        return LongTailRelationShadowResult(
            judge_status=judge_status,
            judge_error_type=judge_error_type,
            incoming_summary=incoming.summary,
            retrieved_candidates=[
                LongTailCandidateMatch(
                    memory_id=result.item.id,
                    kind=result.item.kind,
                    subject=result.item.subject,
                    summary=result.item.summary,
                    status=result.item.status,
                    score=result.score.as_dict(),
                )
                for result in retrieved
            ],
            proposal=proposal,
            validation=validation,
        )


def _relation_query(incoming: MemoryCandidate) -> str:
    payload_object = incoming.payload.get("object")
    parts = [
        incoming.summary,
        *incoming.evidence_spans,
        incoming.custom_predicate or "",
        str(payload_object) if payload_object is not None else "",
    ]
    return " ".join(dict.fromkeys(part.strip() for part in parts if part.strip()))


def _relation_rank(
    incoming: MemoryCandidate,
    result: RetrievedMemory,
) -> tuple[float, float, float, str]:
    item = result.item
    compatibility_bonus = 0.0
    if _subject_key(item.subject) == _subject_key(incoming.subject):
        compatibility_bonus += 0.12
    if item.kind == incoming.kind:
        compatibility_bonus += 0.08
    if memory_role(item) == memory_role(incoming):
        compatibility_bonus += 0.05
    return (
        -(result.score.total + compatibility_bonus),
        -result.score.semantic_similarity,
        -result.score.predicate_match,
        item.id,
    )


def _subject_key(value: str) -> str:
    normalized = value.casefold().strip()
    return _SUBJECT_ALIASES.get(normalized, normalized)


def _perspective_rank(value: MemoryPerspective) -> int:
    return {
        MemoryPerspective.MODEL_INFERRED: 0,
        MemoryPerspective.USER_BELIEF: 1,
        MemoryPerspective.USER_REPORTED: 2,
    }[value]


def _temporal_order_plausible(
    incoming: MemoryCandidate,
    target: MemoryItem,
) -> bool:
    incoming_time = incoming.period_end or incoming.occurred_at or incoming.period_start
    target_time = target.period_end or target.occurred_at or target.period_start
    if incoming_time is None or target_time is None:
        return True
    incoming_time, target_time = _align_datetimes(incoming_time, target_time)
    return incoming_time >= target_time


def _has_temporal_evidence(
    incoming: MemoryCandidate,
    target: MemoryItem,
) -> bool:
    incoming_time = incoming.period_end or incoming.occurred_at or incoming.period_start
    target_time = target.period_end or target.occurred_at or target.period_start
    return incoming_time is not None and target_time is not None


def _align_datetimes(left: datetime, right: datetime) -> tuple[datetime, datetime]:
    if left.tzinfo is None and right.tzinfo is not None:
        left = left.replace(tzinfo=right.tzinfo)
    elif left.tzinfo is not None and right.tzinfo is None:
        right = right.replace(tzinfo=left.tzinfo)
    return left, right


def _is_expired(item: MemoryItem, reference_time: datetime) -> bool:
    if item.expires_at is None:
        return False
    expires_at, now = _align_datetimes(item.expires_at, reference_time)
    return expires_at <= now


def _record_retrieval_trace(
    trace: TraceRecorder | None,
    incoming: MemoryCandidate,
    retrieved: list[RetrievedMemory],
    candidate_index: int | None,
) -> None:
    if trace is None:
        return
    with trace.measure("memory_long_tail_candidate_retrieval") as details:
        details.update(
            {
                "candidate_index": candidate_index,
                "incoming_summary": incoming.summary,
                "candidate_count": len(retrieved),
                "retrieved_candidates_json": json.dumps(
                    [
                        {
                            "memory_id": result.item.id,
                            "kind": result.item.kind.value,
                            "subject": result.item.subject,
                            "summary": result.item.summary,
                            "status": result.item.status.value,
                            "score": result.score.as_dict(),
                        }
                        for result in retrieved
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "preserved_semantic_candidates": True,
                "resolution_status": (
                    "retrieval_no_candidate" if not retrieved else "retrieval_candidates_found"
                ),
            }
        )


def _record_proposal_trace(
    trace: TraceRecorder | None,
    proposal: SemanticRelationProposal,
    candidate_index: int | None,
    *,
    judge_status: str,
    judge_error_type: str | None,
) -> None:
    if trace is None:
        return
    with trace.measure("memory_semantic_relation_proposal") as details:
        details.update(
            {
                "candidate_index": candidate_index,
                "judge_status": judge_status,
                "judge_error_type": judge_error_type,
                "relation": proposal.relation.value,
                "target_memory_ids_json": json.dumps(proposal.target_memory_ids),
                "same_semantic_dimension": proposal.same_semantic_dimension,
                "confidence": proposal.confidence,
                "reason": proposal.reason,
                "judge_model": proposal.judge_model,
                "prompt_tokens": proposal.prompt_tokens,
                "completion_tokens": proposal.completion_tokens,
                "total_tokens": proposal.total_tokens,
                "judge_latency_ms": proposal.latency_ms,
                "resolution_status": _proposal_resolution_status(
                    judge_status=judge_status,
                    proposal=proposal,
                    candidate_count=None,
                ),
            }
        )


def _record_validator_trace(
    trace: TraceRecorder | None,
    validation: LongTailRelationValidation,
    candidate_index: int | None,
) -> None:
    if trace is None:
        return
    with trace.measure("memory_long_tail_validator") as details:
        details.update(
            {
                "candidate_index": candidate_index,
                "validator_pass": validation.validator_pass,
                "validated_relation": validation.validated_relation.value,
                "validator_reasons_json": json.dumps(validation.validator_reasons),
                "checks_json": json.dumps(validation.checks, separators=(",", ":")),
                "would_update": validation.would_update,
                "would_supersede_memory_ids_json": json.dumps(
                    validation.would_supersede_memory_ids
                ),
                "store_mutation_permitted": False,
                "resolution_status": (
                    "validator_denied"
                    if not validation.validator_pass
                    else "validator_allowed_shadow"
                ),
            }
        )


def _proposal_resolution_status(
    *,
    judge_status: str,
    proposal: SemanticRelationProposal,
    candidate_count: int | None,
) -> str:
    if judge_status == "failed":
        return "deterministic_fallback"
    if judge_status == "not_called":
        return "resolver_not_called"
    if candidate_count == 0:
        return "retrieval_no_candidate"
    if proposal.relation == ClaimRelation.UNCERTAIN:
        return "semantic_uncertain"
    if proposal.relation == ClaimRelation.UPDATE:
        return "semantic_update_proposed"
    return "semantic_relation_proposed"


__all__ = [
    "LongTailRelationCandidateRetriever",
    "LongTailRelationShadowEvaluator",
    "LongTailSemanticRelationValidator",
]

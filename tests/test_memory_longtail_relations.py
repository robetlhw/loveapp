import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from loveapp.adapters.memory.in_memory import InMemoryMemoryStore
from loveapp.application.memory import MemoryService
from loveapp.application.memory_retrieval import HybridMemoryRetriever
from loveapp.application.memory_semantic_relations import (
    LongTailRelationCandidateRetriever,
    LongTailRelationShadowEvaluator,
    LongTailSemanticRelationValidator,
)
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.memory import (
    AdmissionDecision,
    AtomicClaim,
    AtomicExtraction,
    ClaimRelation,
    EvidenceExplicitness,
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    PredicateType,
    TimeKind,
    memory_dedupe_key,
)
from loveapp.domain.memory_lifecycle import semantic_context_key
from loveapp.domain.memory_semantic_relation import SemanticRelationProposal
from loveapp.domain.memory_verification import ClaimVerification

NOW = datetime(2026, 8, 29, 12, tzinfo=UTC)
USER_ID = "longtail-user"
RELATIONSHIP_ID = "partner"


class StaticJudge:
    def __init__(self, proposal: SemanticRelationProposal) -> None:
        self.proposal = proposal
        self.candidate_ids: list[str] = []

    async def propose_relation(
        self,
        *,
        incoming: MemoryCandidate,
        candidates: list[MemoryItem],
        trace=None,
    ) -> SemanticRelationProposal:
        del incoming, trace
        self.candidate_ids = [item.id for item in candidates]
        return self.proposal.model_copy(deep=True)


class RaisingJudge:
    async def propose_relation(self, **kwargs: object) -> SemanticRelationProposal:
        del kwargs
        raise RuntimeError("judge unavailable")


class RelationEmbeddingProvider:
    """Keep the friend-activity dimension distinct from realistic distractors."""

    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0] if "朋友聚会" in text else [0.0, 1.0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "朋友聚会" in text else [0.0, 1.0] for text in texts]


class StaticExtractor:
    def __init__(self, claims: list[AtomicClaim]) -> None:
        self._extraction = AtomicExtraction(claims=claims)

    async def extract(self, text: str, **kwargs: object) -> AtomicExtraction:
        del text, kwargs
        return self._extraction.model_copy(deep=True)


class LegacyUpdateVerifier:
    async def verify_claim(
        self,
        text: str,
        *,
        candidate: MemoryCandidate,
        existing_memories: list[MemoryItem],
        allowed_target_ids: set[str],
        trace=None,
    ) -> ClaimVerification:
        del text, candidate, existing_memories, trace
        return ClaimVerification(
            claim_supported=True,
            relation=ClaimRelation.UPDATE,
            target_memory_ids=[next(iter(allowed_target_ids))],
            reason="Legacy verifier proposed a destructive custom update.",
            evidence_sufficient=True,
            verifier_model="legacy-strong-verifier",
        )


def _candidate(
    text: str = "她最近很少再邀请我参加朋友聚会。",
    *,
    kind: MemoryKind = MemoryKind.INTERACTION_PATTERN,
    subject: str = "partner",
    predicate: str = "rarely_invites_user_to_friend_activities",
    confidence: float = 0.96,
    perspective: MemoryPerspective = MemoryPerspective.USER_REPORTED,
    occurred_at: datetime | None = None,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
) -> MemoryCandidate:
    return MemoryCandidate(
        kind=kind,
        subject=subject,
        summary=text,
        original_text=text,
        evidence_spans=[text],
        time_kind=(
            TimeKind.POINT if kind == MemoryKind.INTERACTION_EVENT else TimeKind.INTERVAL
        ),
        occurred_at=occurred_at,
        period_start=period_start,
        period_end=period_end,
        confidence=confidence,
        perspective=perspective,
        explicitness=EvidenceExplicitness.EXPLICIT,
        payload={
            "predicate": predicate,
            "object": "participation_restricted",
            "metric": "social_integration",
            "frequency": "rare",
        },
        raw_predicate=predicate,
        predicate_type=PredicateType.CUSTOM,
        custom_predicate=predicate,
        admission_score=0.95,
        admission_decision=AdmissionDecision.CONFIRM,
    )


def _item(
    memory_id: str,
    *,
    candidate: MemoryCandidate | None = None,
    status: MemoryStatus = MemoryStatus.CONFIRMED,
    source_message_id: str | None = None,
) -> MemoryItem:
    value = candidate or _candidate(
        "她最近经常邀请我参加朋友聚会。",
        predicate="invites_user_to_friend_activities",
        period_start=NOW - timedelta(days=60),
        period_end=NOW - timedelta(days=30),
    )
    return MemoryItem(
        id=memory_id,
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        status=status,
        source_message_id=source_message_id or f"source-{memory_id}",
        created_at=NOW - timedelta(days=30),
        updated_at=NOW - timedelta(days=30),
        dedupe_key=memory_dedupe_key(value),
        **value.model_dump(),
    )


def _update_proposal(
    *target_ids: str,
    confidence: float = 0.97,
) -> SemanticRelationProposal:
    return SemanticRelationProposal(
        relation=ClaimRelation.UPDATE,
        target_memory_ids=list(target_ids),
        same_semantic_dimension=True,
        confidence=confidence,
        reason="The newer sustained pattern replaces the older pattern.",
        judge_model="scripted-semantic-judge",
    )


def _validate(
    proposal: SemanticRelationProposal,
    *,
    incoming: MemoryCandidate | None = None,
    retrieved: list[MemoryItem] | None = None,
    incoming_status: MemoryStatus = MemoryStatus.CONFIRMED,
    incoming_source_message_id: str = "incoming-source",
):
    return LongTailSemanticRelationValidator().validate(
        proposal,
        incoming=incoming or _candidate(period_end=NOW),
        retrieved=retrieved or [_item("target")],
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        incoming_status=incoming_status,
        incoming_source_message_id=incoming_source_message_id,
        reference_time=NOW,
    )


def _claim(candidate: MemoryCandidate, claim_id: str = "incoming-longtail") -> AtomicClaim:
    return AtomicClaim(
        claim_id=claim_id,
        kind=candidate.kind,
        subject=candidate.subject,
        predicate=candidate.raw_predicate or candidate.custom_predicate or "custom_fact",
        summary=candidate.summary,
        evidence_spans=candidate.evidence_spans,
        time_kind=candidate.time_kind,
        occurred_at=candidate.occurred_at,
        period_start=candidate.period_start,
        period_end=candidate.period_end,
        confidence=candidate.confidence,
        perspective=candidate.perspective,
        explicitness=candidate.explicitness,
        payload=candidate.payload,
        raw_predicate=candidate.raw_predicate,
        predicate_type=candidate.predicate_type,
        custom_predicate=candidate.custom_predicate,
    )


@pytest.mark.asyncio
async def test_relation_retrieval_preserves_same_semantic_context_key() -> None:
    first = _item("first")
    second = _item("second")
    assert semantic_context_key(first) == semantic_context_key(second)
    retriever = LongTailRelationCandidateRetriever(HybridMemoryRetriever(), limit=5)

    retrieved = await retriever.retrieve(
        _candidate(),
        [first, second],
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        reference_time=NOW,
    )

    assert {result.item.id for result in retrieved} == {"first", "second"}


@pytest.mark.asyncio
async def test_relation_retrieval_caps_top_k_at_five() -> None:
    memories = [_item(f"target-{index}") for index in range(8)]
    retriever = LongTailRelationCandidateRetriever(HybridMemoryRetriever(), limit=5)

    retrieved = await retriever.retrieve(
        _candidate(),
        memories,
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        reference_time=NOW,
    )

    assert len(retrieved) == 5
    assert len({result.item.id for result in retrieved}) == 5


@pytest.mark.asyncio
async def test_relation_retrieval_keeps_target_among_realistic_distractors() -> None:
    target = _item(
        "friend-activity-target",
        candidate=_candidate(
            "前两个月她经常邀请我参加朋友聚会。",
            predicate="invites_user_to_friend_activities",
            period_start=NOW - timedelta(days=60),
            period_end=NOW - timedelta(days=30),
        ),
    )
    distractor_summaries = [
        "她最近回复消息越来越慢。",
        "我们最近经常因为钱的问题吵架。",
        "她最近不太愿意见我的父母。",
        "她最近喜欢一个人去跑步。",
        "她最近工作压力比较大。",
        "她最近在准备下个月的旅行。",
        "她最近常常给朋友送生日礼物。",
        "她最近更喜欢在家看电影。",
    ]
    distractors = [
        _item(
            f"distractor-{index}",
            candidate=_candidate(
                summary,
                predicate=f"distractor_pattern_{index}",
                period_start=NOW - timedelta(days=20),
                period_end=NOW,
            ),
        )
        for index, summary in enumerate(distractor_summaries)
    ]
    retriever = LongTailRelationCandidateRetriever(
        HybridMemoryRetriever(RelationEmbeddingProvider()),
        limit=5,
    )

    retrieved = await retriever.retrieve(
        _candidate(
            "最近她很少再邀请我参加朋友聚会。",
            predicate="stopped_inviting_user_to_friend_activities",
            period_start=NOW - timedelta(days=30),
            period_end=NOW,
        ),
        [*distractors, target],
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        reference_time=NOW,
    )

    assert len(distractors) > 5
    assert len(retrieved) == 5
    assert target.id in {result.item.id for result in retrieved}


@pytest.mark.asyncio
async def test_valid_update_is_authorized_only_in_shadow() -> None:
    target = _item("target")
    judge = StaticJudge(_update_proposal(target.id))
    evaluator = LongTailRelationShadowEvaluator(judge)

    result = await evaluator.evaluate(
        incoming=_candidate(period_end=NOW),
        existing_memories=[target],
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        incoming_status=MemoryStatus.CONFIRMED,
        incoming_source_message_id="incoming-source",
        reference_time=NOW,
    )

    assert result.mode == "shadow"
    assert result.store_mutation_permitted is False
    assert result.validation.validator_pass is True
    assert result.validation.validated_relation == ClaimRelation.UPDATE
    assert result.validation.would_update is True
    assert result.validation.would_supersede_memory_ids == [target.id]


@pytest.mark.parametrize(
    ("case", "failed_check"),
    [
        ("missing_target", "target_exists_in_retrieved_set"),
        ("multiple_targets", "target_count_within_bounds"),
        ("subject_mismatch", "subject_compatible"),
        ("kind_mismatch", "kind_compatible"),
        ("event_vs_pattern", "event_pattern_state_protection"),
        ("event_to_event", "destructive_role_eligible"),
        ("weak_perspective", "perspective_protection"),
        ("requires_inference", "incoming_evidence_sufficient"),
        ("missing_admission_score", "admission_sufficient"),
        ("low_proposal_confidence", "proposal_confidence_sufficient"),
        ("low_incoming_confidence", "incoming_confidence_sufficient"),
        ("missing_temporal_evidence", "temporal_evidence_available"),
        ("temporal_inversion", "temporal_order_plausible"),
        ("proposed_over_confirmed", "confirmed_protection"),
        ("source_replay", "source_message_is_distinct"),
    ],
)
def test_destructive_update_guards_fail_closed(case: str, failed_check: str) -> None:
    target = _item("target")
    incoming = _candidate(period_end=NOW)
    proposal = _update_proposal(target.id)
    retrieved = [target]
    incoming_status = MemoryStatus.CONFIRMED
    source_message_id = "incoming-source"

    if case == "missing_target":
        proposal = _update_proposal("missing")
    elif case == "multiple_targets":
        second = _item("second-target")
        retrieved.append(second)
        proposal = _update_proposal(target.id, second.id)
    elif case == "subject_mismatch":
        target = target.model_copy(update={"subject": "user"})
        retrieved = [target]
    elif case == "kind_mismatch":
        target = target.model_copy(update={"kind": MemoryKind.STABLE_FACT})
        retrieved = [target]
    elif case == "event_vs_pattern":
        target = target.model_copy(update={"kind": MemoryKind.INTERACTION_EVENT})
        retrieved = [target]
    elif case == "event_to_event":
        incoming = _candidate(
            "昨天她没有邀请我参加一次聚会。",
            kind=MemoryKind.INTERACTION_EVENT,
            occurred_at=NOW,
        )
        target = _item(
            "target",
            candidate=_candidate(
                "上周她邀请我参加了一次聚会。",
                kind=MemoryKind.INTERACTION_EVENT,
                occurred_at=NOW - timedelta(days=7),
            ),
        )
        retrieved = [target]
    elif case == "weak_perspective":
        incoming = incoming.model_copy(
            update={"perspective": MemoryPerspective.USER_BELIEF}
        )
    elif case == "requires_inference":
        incoming = incoming.model_copy(update={"requires_inference": True})
    elif case == "missing_admission_score":
        incoming = incoming.model_copy(update={"admission_score": None})
    elif case == "low_proposal_confidence":
        proposal = _update_proposal(target.id, confidence=0.89)
    elif case == "low_incoming_confidence":
        incoming = incoming.model_copy(update={"confidence": 0.74})
    elif case == "missing_temporal_evidence":
        incoming = incoming.model_copy(
            update={"occurred_at": None, "period_start": None, "period_end": None}
        )
    elif case == "temporal_inversion":
        incoming = incoming.model_copy(
            update={
                "period_start": NOW - timedelta(days=90),
                "period_end": NOW - timedelta(days=70),
            }
        )
    elif case == "proposed_over_confirmed":
        incoming_status = MemoryStatus.PROPOSED
    elif case == "source_replay":
        source_message_id = target.source_message_id or ""

    result = _validate(
        proposal,
        incoming=incoming,
        retrieved=retrieved,
        incoming_status=incoming_status,
        incoming_source_message_id=source_message_id,
    )

    assert result.checks[failed_check] is False
    assert result.validator_pass is False
    assert result.validated_relation == ClaimRelation.UNCERTAIN
    assert result.would_update is False
    assert result.would_supersede_memory_ids == []
    assert "fail_closed" in result.validator_reasons


@pytest.mark.parametrize(
    "relation",
    [
        ClaimRelation.SAME,
        ClaimRelation.CONTRADICTION,
        ClaimRelation.COMPLEMENTARY,
        ClaimRelation.UNRELATED,
        ClaimRelation.UNCERTAIN,
    ],
)
def test_non_destructive_relations_never_produce_would_update(
    relation: ClaimRelation,
) -> None:
    target_ids = (
        []
        if relation in {ClaimRelation.UNRELATED, ClaimRelation.UNCERTAIN}
        else ["target"]
    )
    proposal = SemanticRelationProposal(
        relation=relation,
        target_memory_ids=target_ids,
        same_semantic_dimension=bool(target_ids),
        confidence=0.99,
        reason="A non-destructive semantic relation was proposed.",
    )

    result = _validate(proposal)

    assert result.validator_pass is True
    assert result.validated_relation == relation
    assert result.would_update is False
    assert result.would_supersede_memory_ids == []


@pytest.mark.parametrize(
    "relation",
    [ClaimRelation.UNRELATED, ClaimRelation.UNCERTAIN],
)
def test_untargeted_relations_reject_target_ids(relation: ClaimRelation) -> None:
    proposal = SemanticRelationProposal(
        relation=relation,
        target_memory_ids=["target"],
        same_semantic_dimension=False,
        confidence=0.99,
        reason="Untargeted relations must not retain an arbitrary target.",
    )

    result = _validate(proposal)

    assert result.checks["target_count_within_bounds"] is False
    assert result.validator_pass is False
    assert result.would_update is False


def test_missing_target_checks_are_not_reported_as_vacuously_true() -> None:
    result = _validate(_update_proposal("missing"))

    assert result.checks["target_exists_in_retrieved_set"] is False
    assert result.checks["same_scope"] is False
    assert result.checks["target_active"] is False
    assert result.checks["target_not_expired"] is False


@pytest.mark.asyncio
async def test_judge_exception_fails_closed() -> None:
    evaluator = LongTailRelationShadowEvaluator(RaisingJudge())

    result = await evaluator.evaluate(
        incoming=_candidate(period_end=NOW),
        existing_memories=[_item("target")],
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        incoming_status=MemoryStatus.CONFIRMED,
        reference_time=NOW,
    )

    assert result.proposal.relation == ClaimRelation.UNCERTAIN
    assert result.proposal.target_memory_ids == []
    assert result.proposal.confidence == 0
    assert "judge failed closed" in result.proposal.reason
    assert result.validation.would_update is False
    assert result.validation.would_supersede_memory_ids == []


@pytest.mark.asyncio
async def test_shadow_evaluation_does_not_mutate_memory_item_inputs() -> None:
    target = _item("target")
    incoming = _candidate(period_end=NOW)
    before_target = target.model_dump(mode="json")
    before_incoming = incoming.model_dump(mode="json")
    evaluator = LongTailRelationShadowEvaluator(
        StaticJudge(_update_proposal(target.id))
    )

    result = await evaluator.evaluate(
        incoming=incoming,
        existing_memories=[target],
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        incoming_status=MemoryStatus.CONFIRMED,
        incoming_source_message_id="incoming-source",
        reference_time=NOW,
    )

    assert result.validation.would_update is True
    assert target.model_dump(mode="json") == before_target
    assert incoming.model_dump(mode="json") == before_incoming


@pytest.mark.asyncio
async def test_memory_service_shadow_update_does_not_change_uncertain_add_path() -> None:
    old_candidate = _candidate(
        "她最近经常邀请我参加朋友聚会。",
        predicate="invites_user_to_friend_activities",
        period_start=NOW - timedelta(days=60),
        period_end=NOW - timedelta(days=30),
    )
    incoming = _candidate(
        "但最近她很少再邀请我参加朋友聚会了。",
        predicate="stopped_inviting_user_to_friend_activities",
        period_start=NOW - timedelta(days=30),
        period_end=NOW,
    )
    store = InMemoryMemoryStore(clock=lambda: NOW)
    old = await store.save_memory(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        candidate=old_candidate,
        source_message_id="old-source",
        status=MemoryStatus.CONFIRMED,
    )
    judge = StaticJudge(_update_proposal(old.item.id))
    service = MemoryService(
        store,
        StaticExtractor([_claim(incoming)]),
        clock=lambda: NOW,
        long_tail_relation_evaluator=LongTailRelationShadowEvaluator(judge),
    )
    trace = ExecutionTrace()

    result = await service.remember_text(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        conversation_id="longtail-shadow",
        text=incoming.original_text,
        status=MemoryStatus.CONFIRMED,
        trace=trace,
    )
    assert await service.wait_for_long_tail_shadow(timeout_seconds=1) == 0

    old_after = await store.get_memory(old.item.id, USER_ID)
    memories = await store.list_memories(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        read_only=True,
    )
    audits = await store.list_transition_audits(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        source_message_id=result.message.id,
    )
    records = trace.snapshot()
    proposal_trace = next(
        record for record in records if record.name == "memory_semantic_relation_proposal"
    )
    validator_trace = next(
        record for record in records if record.name == "memory_long_tail_validator"
    )
    governance_trace = next(
        record for record in records if record.name == "memory_candidate_governance"
    )

    assert proposal_trace.details["relation"] == ClaimRelation.UPDATE.value
    assert validator_trace.details["would_update"] is True
    assert validator_trace.details["store_mutation_permitted"] is False
    assert governance_trace.details["claim_relation"] == ClaimRelation.UNCERTAIN.value
    assert governance_trace.details["planned_action"] == "add"
    assert old_after is not None and old_after.status == MemoryStatus.CONFIRMED
    assert len(result.saved) == 1
    assert result.saved[0].item.id != old.item.id
    assert result.saved[0].item.claim_relation == ClaimRelation.UNCERTAIN
    assert result.saved[0].item.supersedes_id is None
    assert len(memories) == 2
    assert audits[0].relation == ClaimRelation.UNCERTAIN


@pytest.mark.asyncio
async def test_slow_shadow_judge_cannot_delay_or_cancel_official_memory_write() -> None:
    target_candidate = _candidate(
        "她最近经常邀请我参加朋友聚会。",
        predicate="invites_user_to_friend_activities",
        period_start=NOW - timedelta(days=60),
        period_end=NOW - timedelta(days=30),
    )
    incoming = _candidate(period_end=NOW)

    store = InMemoryMemoryStore(clock=lambda: NOW)
    saved_target = await store.save_memory(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        candidate=target_candidate,
        source_message_id="slow-shadow-target-source",
        status=MemoryStatus.CONFIRMED,
    )
    target = saved_target.item

    class BlockingJudge:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def propose_relation(self, **kwargs) -> SemanticRelationProposal:
            del kwargs
            self.started.set()
            await self.release.wait()
            return _update_proposal(target.id)

    judge = BlockingJudge()
    service = MemoryService(
        store,
        StaticExtractor([_claim(incoming)]),
        clock=lambda: NOW,
        long_tail_relation_evaluator=LongTailRelationShadowEvaluator(judge),
    )
    try:
        result = await asyncio.wait_for(
            service.remember_text(
                user_id=USER_ID,
                relationship_id=RELATIONSHIP_ID,
                conversation_id="slow-longtail-shadow",
                text=incoming.original_text,
                status=MemoryStatus.CONFIRMED,
            ),
            timeout=1,
        )
        await asyncio.wait_for(judge.started.wait(), timeout=1)

        assert len(result.saved) == 1
        assert result.saved[0].item.claim_relation == ClaimRelation.UNCERTAIN
        assert await service.wait_for_scope(
            user_id=USER_ID,
            relationship_id=RELATIONSHIP_ID,
            timeout_seconds=0,
        ) == 0
        assert await service.wait_for_long_tail_shadow(timeout_seconds=0) == 1
    finally:
        judge.release.set()
        await service.wait_for_long_tail_shadow(timeout_seconds=1)
        await service.aclose()


@pytest.mark.asyncio
async def test_shadow_mode_does_not_suppress_legacy_verifier_relation() -> None:
    old_candidate = _candidate(
        "她以前经常邀请我参加朋友聚会。",
        predicate="invites_user_to_friend_activities",
        period_start=NOW - timedelta(days=60),
        period_end=NOW - timedelta(days=30),
    )
    incoming = _candidate(
        "最近她很少再邀请我参加朋友聚会。",
        predicate="rarely_invites_user_to_friend_activities",
        period_start=NOW - timedelta(days=30),
        period_end=NOW,
    )
    store = InMemoryMemoryStore(clock=lambda: NOW)
    old = await store.save_memory(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        candidate=old_candidate,
        source_message_id="old-source",
        status=MemoryStatus.PROPOSED,
    )
    service = MemoryService(
        store,
        StaticExtractor([_claim(incoming)]),
        verifier=LegacyUpdateVerifier(),
        clock=lambda: NOW,
        long_tail_relation_evaluator=LongTailRelationShadowEvaluator(
            StaticJudge(_update_proposal(old.item.id))
        ),
    )

    result = await service.remember_text(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        conversation_id="longtail-legacy-verifier",
        text=incoming.original_text,
    )

    old_after = await store.get_memory(old.item.id, USER_ID)
    assert old_after is not None and old_after.status == MemoryStatus.SUPERSEDED
    assert len(result.saved) == 1
    assert result.saved[0].item.claim_relation == ClaimRelation.UPDATE
    assert result.saved[0].item.supersedes_id == old.item.id

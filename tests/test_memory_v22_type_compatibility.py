from datetime import UTC, datetime, timedelta

from loveapp.adapters.memory.in_memory import InMemoryMemoryStore
from loveapp.application.memory import MemoryService
from loveapp.application.memory_relations import resolve_claim_relation
from loveapp.application.memory_semantic_relations import (
    LongTailSemanticRelationValidator,
)
from loveapp.domain.memory import (
    AdmissionDecision,
    AtomicClaim,
    AtomicExtraction,
    ClaimRelation,
    EpistemicStatus,
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
from loveapp.domain.memory_lifecycle import (
    plan_memory_transitions,
    semantic_context_key,
)
from loveapp.domain.memory_semantic_relation import SemanticRelationProposal
from loveapp.domain.memory_verification import ClaimVerification

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)
USER_ID = "v22-user"
RELATIONSHIP_ID = "v22-relationship"


class StaticExtractor:
    def __init__(self, claims: list[AtomicClaim]) -> None:
        self._extraction = AtomicExtraction(claims=claims)

    async def extract(self, text: str, **kwargs: object) -> AtomicExtraction:
        del text, kwargs
        return self._extraction.model_copy(deep=True)


class CapturingUpdateVerifier:
    def __init__(self, target_memory_id: str | None) -> None:
        self._target_memory_id = target_memory_id
        self.allowed_target_ids: set[str] | None = None

    async def verify_claim(
        self,
        text: str,
        *,
        candidate: MemoryCandidate,
        existing_memories: list[MemoryItem],
        allowed_target_ids: set[str],
        trace: object = None,
    ) -> ClaimVerification:
        del text, candidate, existing_memories, trace
        self.allowed_target_ids = set(allowed_target_ids)
        targets = [self._target_memory_id] if self._target_memory_id is not None else []
        return ClaimVerification(
            claim_supported=True,
            evidence_sufficient=True,
            relation=(
                ClaimRelation.UPDATE
                if self._target_memory_id is not None
                else ClaimRelation.UNRELATED
            ),
            target_memory_ids=targets,
            reason="Crafted verifier proposal for the V2.2 type boundary.",
            verifier_model="v22-test-verifier",
        )


def _candidate(
    *,
    kind: MemoryKind = MemoryKind.STABLE_FACT,
    text: str = "She dislikes crowded places.",
    predicate: str = "partner_dislikes_crowds",
    perspective: MemoryPerspective = MemoryPerspective.USER_REPORTED,
    epistemic_status: EpistemicStatus = EpistemicStatus.CONFIRMED,
    occurred_at: datetime | None = None,
) -> MemoryCandidate:
    return MemoryCandidate(
        kind=kind,
        subject="partner",
        summary=text,
        original_text=text,
        evidence_spans=[text],
        time_kind=(
            TimeKind.POINT if kind == MemoryKind.INTERACTION_EVENT else TimeKind.TIMELESS
        ),
        occurred_at=occurred_at,
        perspective=perspective,
        epistemic_status=epistemic_status,
        confidence=0.95,
        explicitness=EvidenceExplicitness.EXPLICIT,
        raw_predicate=predicate,
        predicate_type=PredicateType.CUSTOM,
        custom_predicate=predicate,
        payload={"predicate": predicate, "object": "crowded_places"},
        admission_score=0.95,
        admission_decision=AdmissionDecision.CONFIRM,
    )


def _item(memory_id: str, candidate: MemoryCandidate) -> MemoryItem:
    return MemoryItem(
        **candidate.model_dump(),
        id=memory_id,
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        status=MemoryStatus.CONFIRMED,
        source_message_id=f"source-{memory_id}",
        created_at=NOW - timedelta(days=1),
        updated_at=NOW - timedelta(days=1),
        dedupe_key=memory_dedupe_key(candidate),
    )


def _claim(candidate: MemoryCandidate, claim_id: str) -> AtomicClaim:
    data = candidate.model_dump(
        exclude={
            "admission_decision",
            "admission_score",
            "claim_relation",
            "lifecycle_review_required",
            "original_text",
        }
    )
    data.update(
        {
            "claim_id": claim_id,
            "predicate": str(
                candidate.raw_predicate
                or candidate.payload.get("predicate")
                or "v22_test_predicate"
            ),
            "object": None,
        }
    )
    return AtomicClaim.model_validate(data)


def _state_candidate(
    value: str,
    *,
    text: str,
    perspective: MemoryPerspective = MemoryPerspective.USER_REPORTED,
    epistemic_status: EpistemicStatus = EpistemicStatus.CONFIRMED,
) -> MemoryCandidate:
    predicate = "relationship.conflict_status"
    return MemoryCandidate(
        kind=MemoryKind.RELATIONSHIP_STATE,
        subject="relationship",
        summary=text,
        original_text=text,
        evidence_spans=[text],
        time_kind=TimeKind.TIMELESS,
        perspective=perspective,
        epistemic_status=epistemic_status,
        confidence=0.95,
        explicitness=EvidenceExplicitness.EXPLICIT,
        raw_predicate=predicate,
        predicate_type=PredicateType.CANONICAL,
        canonical_predicate=predicate,
        state_dimension=predicate,
        state_value=value,
        payload={
            "predicate": predicate,
            "state_dimension": predicate,
            "state_value": value,
        },
    )


def _validate(
    relation: ClaimRelation,
    incoming: MemoryCandidate,
    target: MemoryItem,
):
    return LongTailSemanticRelationValidator().validate(
        SemanticRelationProposal(
            relation=relation,
            target_memory_ids=[target.id],
            same_semantic_dimension=True,
            confidence=0.99,
            reason="Crafted V2.2 type-boundary proposal.",
        ),
        incoming=incoming,
        retrieved=[target],
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        incoming_status=MemoryStatus.CONFIRMED,
        incoming_source_message_id="new-source",
        reference_time=NOW,
    )


def test_exact_belief_and_fact_have_distinct_identity_and_no_direct_relation() -> None:
    fact = _candidate()
    belief = _candidate(
        perspective=MemoryPerspective.USER_BELIEF,
        epistemic_status=EpistemicStatus.UNCERTAIN,
    )

    resolution = resolve_claim_relation(
        belief,
        [_item("fact", fact)],
        incoming_status=MemoryStatus.CONFIRMED,
    )

    assert memory_dedupe_key(belief) != memory_dedupe_key(fact)
    assert resolution.relation not in {ClaimRelation.SAME, ClaimRelation.UPDATE}


def test_fact_and_belief_cannot_update_each_other_in_either_direction() -> None:
    fact = _candidate(text="She dislikes crowds.")
    belief = _candidate(
        text="I think she dislikes crowds.",
        perspective=MemoryPerspective.USER_BELIEF,
        epistemic_status=EpistemicStatus.UNCERTAIN,
    )

    belief_to_fact = resolve_claim_relation(
        belief,
        [_item("fact", fact)],
        incoming_status=MemoryStatus.CONFIRMED,
    )
    fact_to_belief = resolve_claim_relation(
        fact,
        [_item("belief", belief)],
        incoming_status=MemoryStatus.CONFIRMED,
    )

    assert belief_to_fact.relation != ClaimRelation.UPDATE
    assert fact_to_belief.relation != ClaimRelation.UPDATE
    assert not _validate(ClaimRelation.UPDATE, belief, _item("fact-v", fact)).validator_pass
    assert not _validate(
        ClaimRelation.UPDATE,
        fact,
        _item("belief-v", belief),
    ).validator_pass


def test_same_belief_replay_remains_idempotent() -> None:
    belief = _candidate(
        text="I suspect she dislikes crowds.",
        perspective=MemoryPerspective.USER_BELIEF,
        epistemic_status=EpistemicStatus.HYPOTHESIS,
    )

    resolution = resolve_claim_relation(
        belief,
        [_item("belief", belief)],
        incoming_status=MemoryStatus.CONFIRMED,
    )

    assert resolution.relation == ClaimRelation.SAME
    assert resolution.target_memory_ids == ("belief",)


def test_event_event_allows_same_and_complementary_but_not_update() -> None:
    first = _candidate(
        kind=MemoryKind.INTERACTION_EVENT,
        text="Yesterday she initiated a chat.",
        predicate="partner_initiated_chat",
        occurred_at=NOW - timedelta(days=1),
    )
    repeated = first.model_copy(deep=True)
    later = _candidate(
        kind=MemoryKind.INTERACTION_EVENT,
        text="Today she initiated another chat.",
        predicate="partner_initiated_chat",
        occurred_at=NOW,
    )
    target = _item("event", first)

    same = resolve_claim_relation(
        repeated,
        [target],
        incoming_status=MemoryStatus.CONFIRMED,
    )
    complementary = resolve_claim_relation(
        later,
        [target],
        incoming_status=MemoryStatus.CONFIRMED,
    )

    assert same.relation == ClaimRelation.SAME
    assert complementary.relation == ClaimRelation.COMPLEMENTARY
    update_validation = _validate(ClaimRelation.UPDATE, later, target)
    assert not update_validation.validator_pass
    assert not update_validation.checks["relation_type_allowed"]


def test_event_pattern_is_evidence_only_and_direct_proposal_is_denied() -> None:
    event = _candidate(
        kind=MemoryKind.INTERACTION_EVENT,
        text="Yesterday she initiated a chat.",
        predicate="partner_initiated_chat",
        occurred_at=NOW,
    )
    pattern = _candidate(
        kind=MemoryKind.INTERACTION_PATTERN,
        text="She often initiates chats.",
        predicate="partner_initiated_chat",
    )
    target = _item("pattern", pattern)

    resolution = resolve_claim_relation(
        event,
        [target],
        incoming_status=MemoryStatus.CONFIRMED,
    )
    validation = _validate(ClaimRelation.COMPLEMENTARY, event, target)

    assert resolution.relation == ClaimRelation.UNRELATED
    assert resolution.target_memory_ids == ()
    assert resolution.rule_name == "event_pattern_evidence_only"
    assert not validation.validator_pass
    assert not validation.checks["type_compatible"]


def test_preference_event_defaults_to_unrelated_and_direct_proposal_is_denied() -> None:
    preference = _candidate(
        kind=MemoryKind.PREFERENCE,
        text="I prefer quiet conversations.",
        predicate="prefers_quiet_conversations",
    )
    event = _candidate(
        kind=MemoryKind.INTERACTION_EVENT,
        text="Yesterday she spoke quietly.",
        predicate="partner_spoke_quietly",
        occurred_at=NOW,
    )
    target = _item("event", event)

    resolution = resolve_claim_relation(
        preference,
        [target],
        incoming_status=MemoryStatus.CONFIRMED,
    )
    validation = _validate(ClaimRelation.COMPLEMENTARY, preference, target)

    assert resolution.relation == ClaimRelation.UNRELATED
    assert resolution.target_memory_ids == ()
    assert resolution.rule_name == "memory_type_boundary"
    assert not validation.validator_pass
    assert not validation.checks["type_compatible"]


def test_belief_state_cannot_supersede_fact_state_through_lifecycle() -> None:
    old_fact = _candidate(
        kind=MemoryKind.RELATIONSHIP_STATE,
        text="We are in an active conflict.",
        predicate="relationship.conflict_status",
    ).model_copy(
        update={
            "subject": "relationship",
            "predicate_type": PredicateType.CANONICAL,
            "canonical_predicate": "relationship.conflict_status",
            "custom_predicate": None,
            "state_dimension": "relationship.conflict_status",
            "state_value": "active",
            "payload": {
                "predicate": "relationship.conflict_status",
                "state_dimension": "relationship.conflict_status",
                "state_value": "active",
            },
        }
    )
    belief_trigger = old_fact.model_copy(
        update={
            "summary": "I think the conflict is resolved.",
            "original_text": "I think the conflict is resolved.",
            "evidence_spans": ["I think the conflict is resolved."],
            "perspective": MemoryPerspective.USER_BELIEF,
            "epistemic_status": EpistemicStatus.UNCERTAIN,
            "state_value": "resolved",
            "payload": {
                "predicate": "relationship.conflict_status",
                "state_dimension": "relationship.conflict_status",
                "state_value": "resolved",
            },
        }
    )

    plans = plan_memory_transitions(
        [belief_trigger],
        [_item("active-fact", old_fact)],
        trigger_statuses=[MemoryStatus.CONFIRMED],
    )

    assert plans == []


async def test_store_keeps_exact_belief_and_fact_as_distinct_rows() -> None:
    store = InMemoryMemoryStore()
    fact = _candidate()
    belief = _candidate(
        perspective=MemoryPerspective.USER_BELIEF,
        epistemic_status=EpistemicStatus.UNCERTAIN,
    )

    fact_saved = await store.save_memory(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        candidate=fact,
        status=MemoryStatus.CONFIRMED,
    )
    belief_saved = await store.save_memory(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        candidate=belief,
        status=MemoryStatus.CONFIRMED,
    )
    memories = await store.list_memories(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
    )

    assert fact_saved.created
    assert belief_saved.created
    assert fact_saved.item.id != belief_saved.item.id
    assert len(memories) == 2
    assert semantic_context_key(fact_saved.item) != semantic_context_key(
        belief_saved.item
    )


async def test_strong_verifier_cannot_update_fact_with_belief() -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    fact = _candidate()
    saved_fact = await store.save_memory(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        candidate=fact,
        status=MemoryStatus.PROPOSED,
    )
    belief = _candidate(
        text="I think she dislikes crowded places.",
        perspective=MemoryPerspective.USER_BELIEF,
        epistemic_status=EpistemicStatus.UNCERTAIN,
    )
    verifier = CapturingUpdateVerifier(saved_fact.item.id)
    service = MemoryService(
        store,
        StaticExtractor([_claim(belief, "belief-correction")]),
        verifier=verifier,
        clock=lambda: NOW,
    )

    result = await service.remember_text(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        text="\u8bb0\u4e00\u4e0b\uff1aI think she dislikes crowded places.",
    )

    fact_after = await store.get_memory(saved_fact.item.id, USER_ID)
    audits = await store.list_transition_audits(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        source_message_id=result.message.id,
    )
    assert verifier.allowed_target_ids == {saved_fact.item.id}
    assert fact_after is not None and fact_after.status == MemoryStatus.PROPOSED
    assert len(result.saved) == 1
    assert result.saved[0].item.supersedes_id is None
    assert result.saved[0].item.claim_relation != ClaimRelation.UPDATE
    assert audits[0].rule_name == "strong_verifier_fallback"
    assert "forbidden by the memory type boundary" in str(
        audits[0].score_breakdown["strong_verifier_error"]
    )


async def test_strong_verifier_does_not_receive_event_pattern_evidence_target() -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    pattern = _candidate(
        kind=MemoryKind.INTERACTION_PATTERN,
        text="She often initiates chats.",
        predicate="partner_initiated_chat",
    )
    saved_pattern = await store.save_memory(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        candidate=pattern,
        status=MemoryStatus.CONFIRMED,
    )
    event = _candidate(
        kind=MemoryKind.INTERACTION_EVENT,
        text="Yesterday she initiated a chat.",
        predicate="partner_initiated_chat",
        occurred_at=NOW - timedelta(days=1),
    )
    verifier = CapturingUpdateVerifier(None)
    service = MemoryService(
        store,
        StaticExtractor([_claim(event, "single-chat-event")]),
        verifier=verifier,
        clock=lambda: NOW,
    )

    await service.remember_text(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        text="\u8bb0\u4e00\u4e0b\uff1aYesterday she initiated a chat.",
    )

    assert saved_pattern.item.id not in (verifier.allowed_target_ids or set())


async def test_in_batch_belief_state_cannot_supersede_factual_state() -> None:
    fact = _state_candidate(
        "active",
        text="We are currently in an active conflict.",
    )
    belief = _state_candidate(
        "resolved",
        text="I think the conflict may already be resolved.",
        perspective=MemoryPerspective.USER_BELIEF,
        epistemic_status=EpistemicStatus.UNCERTAIN,
    )
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        StaticExtractor(
            [
                _claim(fact, "reported-active-conflict"),
                _claim(belief, "belief-resolved-conflict"),
            ]
        ),
        clock=lambda: NOW,
    )

    await service.remember_text(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        text=(
            "\u8bb0\u4e00\u4e0b\uff1aWe are currently in an active conflict. "
            "I think the conflict may already be resolved."
        ),
        status=MemoryStatus.CONFIRMED,
    )

    memories = await store.list_memories(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
    )
    fact_item = next(
        item for item in memories if item.epistemic_status == EpistemicStatus.CONFIRMED
    )
    belief_item = next(
        item for item in memories if item.perspective == MemoryPerspective.USER_BELIEF
    )
    assert fact_item.status == MemoryStatus.CONFIRMED
    assert belief_item.status == MemoryStatus.CONFIRMED
    assert belief_item.epistemic_status != EpistemicStatus.CONFIRMED
    assert belief_item.claim_relation != ClaimRelation.UPDATE
    assert belief_item.supersedes_id is None

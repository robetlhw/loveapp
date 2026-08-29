import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import loveapp.adapters.memory.sqlite as sqlite_memory
from loveapp.adapters.memory.in_memory import InMemoryMemoryStore
from loveapp.adapters.memory.sqlite import SQLiteMemoryStore
from loveapp.application.memory import MemoryService
from loveapp.application.memory_admission import (
    assess_memory_admission,
    build_admission_policies,
)
from loveapp.application.memory_relations import resolve_claim_relation
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.advice import RelationshipContext
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
    MessageRole,
    PredicateType,
    TemporalPrecision,
    TimeKind,
    memory_dedupe_key,
    normalize_candidate_predicate,
)
from loveapp.domain.memory_context import attach_memories
from loveapp.domain.memory_predicates import normalize_predicate
from loveapp.domain.memory_verification import ClaimVerification
from loveapp.domain.memory_write import (
    MemoryStatusUpdate,
    MemoryWriteBatch,
    MemoryWriteOperation,
    RelationshipPlanStatusUpdate,
)
from loveapp.domain.relationship_plan import PlanStatus, RelationshipPlan

NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)
USER_ID = "memory-v2-user"
RELATIONSHIP_ID = "primary"


class StaticExtractor:
    def __init__(self, claims: list[AtomicClaim]) -> None:
        self._extraction = AtomicExtraction(claims=claims)

    async def extract(self, text: str, **kwargs: object) -> AtomicExtraction:
        del text, kwargs
        return self._extraction.model_copy(deep=True)


class CapturingInMemoryMemoryStore(InMemoryMemoryStore):
    def __init__(self) -> None:
        super().__init__(clock=lambda: NOW)
        self.last_batch: MemoryWriteBatch | None = None

    async def commit_memory_batch(
        self,
        *,
        user_id: str,
        relationship_id: str,
        batch: MemoryWriteBatch,
    ):
        self.last_batch = batch.model_copy(deep=True)
        return await super().commit_memory_batch(
            user_id=user_id,
            relationship_id=relationship_id,
            batch=batch,
        )


class OutOfScopeTargetVerifier:
    def __init__(self, target_memory_id: str) -> None:
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
        return ClaimVerification(
            claim_supported=True,
            relation=ClaimRelation.UPDATE,
            canonical_predicate="contact.status",
            state_dimension="relationship.contact_status",
            state_value="restored",
            target_memory_ids=[self._target_memory_id],
            reason="Attempt to update a memory outside the supplied scope.",
            evidence_sufficient=True,
            verifier_model="malicious-test-verifier",
        )


class SupportingPatternVerifier:
    async def verify_claim(
        self,
        text: str,
        *,
        candidate: MemoryCandidate,
        existing_memories: list[MemoryItem],
        allowed_target_ids: set[str],
        trace: object = None,
    ) -> ClaimVerification:
        del text, candidate, existing_memories, allowed_target_ids, trace
        return ClaimVerification(
            claim_supported=True,
            relation=ClaimRelation.UNRELATED,
            canonical_predicate="interaction.contact_frequency",
            state_dimension="interaction.contact_frequency",
            state_value="low",
            reason="The single event is supported, but it is not a repeated pattern.",
            evidence_sufficient=True,
            verifier_model="supporting-pattern-verifier",
        )


def _candidate(
    *,
    kind: MemoryKind,
    text: str,
    subject: str = "user",
    raw_predicate: str | None = None,
    canonical_predicate: str | None = None,
    payload: dict[str, object] | None = None,
    evidence_spans: list[str] | None = None,
    explicitness: EvidenceExplicitness = EvidenceExplicitness.EXPLICIT,
    confidence: float = 1.0,
) -> MemoryCandidate:
    candidate_payload = dict(payload or {})
    predicate = raw_predicate or canonical_predicate
    if predicate is not None:
        candidate_payload.setdefault("predicate", predicate)
    return normalize_candidate_predicate(
        MemoryCandidate(
            kind=kind,
            subject=subject,
            summary=text,
            original_text=text,
            evidence_spans=evidence_spans or [text],
            time_kind=TimeKind.TIMELESS,
            confidence=confidence,
            payload=candidate_payload,
            raw_predicate=raw_predicate or canonical_predicate,
            predicate_type=(
                PredicateType.CANONICAL
                if canonical_predicate is not None
                else PredicateType.CUSTOM
            ),
            canonical_predicate=canonical_predicate,
            explicitness=explicitness,
        )
    )


def _preference(
    value: str,
    *,
    text: str | None = None,
    preference_type: str = "like",
    canonical_predicate: str = "preference.food.cuisine",
    evidence_spans: list[str] | None = None,
    confidence: float = 1.0,
) -> MemoryCandidate:
    source = text or f"I prefer {value}"
    return _candidate(
        kind=MemoryKind.PREFERENCE,
        text=source,
        canonical_predicate=canonical_predicate,
        payload={
            "preference": value,
            "preference_type": preference_type,
        },
        evidence_spans=evidence_spans,
        confidence=confidence,
    )


def _relationship_state(value: str, *, text: str | None = None) -> MemoryCandidate:
    return _candidate(
        kind=MemoryKind.RELATIONSHIP_STATE,
        text=text or f"Contact status is now {value}",
        subject="relationship",
        canonical_predicate="contact.status",
        payload={"state_value": value},
    )


def _item(
    candidate: MemoryCandidate,
    *,
    memory_id: str,
    status: MemoryStatus,
) -> MemoryItem:
    normalized = normalize_candidate_predicate(candidate)
    return MemoryItem(
        id=memory_id,
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        status=status,
        source_message_id=f"source-{memory_id}",
        created_at=NOW,
        updated_at=NOW,
        last_seen_at=NOW,
        dedupe_key=memory_dedupe_key(normalized),
        **normalized.model_dump(),
    )


def _operation(
    candidate: MemoryCandidate,
    *,
    relation: ClaimRelation = ClaimRelation.UNRELATED,
    target_memory_ids: list[str] | None = None,
) -> MemoryWriteOperation:
    governed = candidate.model_copy(
        update={
            "admission_score": 0.96,
            "admission_decision": AdmissionDecision.CONFIRM,
            "claim_relation": relation,
        }
    )
    return MemoryWriteOperation(
        candidate=governed,
        status=MemoryStatus.CONFIRMED,
        relation=relation,
        target_memory_ids=target_memory_ids or [],
        rule_name=f"test_{relation.value}",
        reason="V2 governance test",
        score_breakdown={"model_confidence": governed.confidence},
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
                or "test_predicate"
            ),
            "object": None,
        }
    )
    return AtomicClaim.model_validate(data)


def test_predicate_normalizer_preserves_canonical_and_maps_exact_alias() -> None:
    canonical = normalize_predicate(
        kind=MemoryKind.RELATIONSHIP_STATE,
        raw_predicate="contact.status",
        canonical_predicate="contact.status",
        predicate_type=PredicateType.CANONICAL,
        payload={"state_value": "restored"},
    )
    alias = normalize_predicate(
        kind=MemoryKind.RELATIONSHIP_STATE,
        raw_predicate="started_talking_again",
    )

    assert canonical.predicate_type == PredicateType.CANONICAL
    assert canonical.canonical_predicate == "contact.status"
    assert canonical.state_dimension == "relationship.contact_status"
    assert canonical.state_value == "restored"
    assert canonical.alias_hit is False
    assert alias.predicate_type == PredicateType.CANONICAL
    assert alias.canonical_predicate == "contact.status"
    assert alias.state_value == "restored"
    assert alias.alias_hit is True


def test_predicate_normalizer_recognizes_raw_canonical_without_duplicate_field() -> None:
    normalized = normalize_predicate(
        kind=MemoryKind.RELATIONSHIP_STATE,
        raw_predicate="contact.status",
        payload={"state_value": "restored"},
    )

    assert normalized.predicate_type == PredicateType.CANONICAL
    assert normalized.canonical_predicate == "contact.status"
    assert normalized.state_dimension == "relationship.contact_status"
    assert normalized.state_value == "restored"
    assert normalized.alias_hit is False


@pytest.mark.parametrize(
    ("raw_predicate", "canonical_predicate", "state_value"),
    [
        ("resumed_communication", "contact.status", "restored"),
        ("confessed_to_partner", "confession.status", "executed"),
        (
            "interaction.reconciliation",
            "relationship.repair_status",
            "completed",
        ),
        (
            "resumed_chatting",
            "interaction.response_engagement",
            "responsive",
        ),
    ],
)
def test_predicate_normalizer_maps_recorded_live_aliases(
    raw_predicate: str,
    canonical_predicate: str,
    state_value: str,
) -> None:
    normalized = normalize_predicate(
        kind=MemoryKind.INTERACTION_EVENT,
        raw_predicate=raw_predicate,
    )

    assert normalized.canonical_predicate == canonical_predicate
    assert normalized.state_value == state_value
    assert normalized.alias_hit is True


@pytest.mark.parametrize(
    "raw_predicate",
    [
        "collects_ticket_stubs",
        "she_probably_stopped_responding",
        "她大概不想理我了",
    ],
)
def test_unknown_or_ambiguous_predicate_uses_custom_fallback(
    raw_predicate: str,
) -> None:
    normalized = normalize_predicate(
        kind=MemoryKind.RELATIONSHIP_STATE,
        raw_predicate=raw_predicate,
    )

    assert normalized.predicate_type == PredicateType.CUSTOM
    assert normalized.canonical_predicate is None
    assert normalized.custom_predicate is not None
    assert normalized.state_dimension is None
    assert normalized.state_value is None
    assert normalized.alias_hit is False


def test_admission_confirms_explicit_preference_and_honors_policy_override() -> None:
    candidate = _preference("quiet cafes", confidence=0.90)
    default_assessment = assess_memory_admission(candidate, candidate.original_text)
    stricter_policies = build_admission_policies(
        {"preference": {"direct_confirm_threshold": 0.95}}
    )
    overridden_assessment = assess_memory_admission(
        candidate,
        candidate.original_text,
        policies=stricter_policies,
    )

    assert default_assessment.decision == AdmissionDecision.CONFIRM
    assert default_assessment.reason == "direct_threshold_met"
    assert overridden_assessment.decision == AdmissionDecision.PROPOSE
    assert stricter_policies[MemoryKind.PREFERENCE].direct_confirm_threshold == 0.95


def test_admission_rejects_speculative_relationship_state() -> None:
    text = "Maybe we have already broken up"
    candidate = _candidate(
        kind=MemoryKind.RELATIONSHIP_STATE,
        text=text,
        subject="relationship",
        canonical_predicate="relationship.stage",
        payload={"state_value": "separated"},
        explicitness=EvidenceExplicitness.SPECULATIVE,
    )

    assessment = assess_memory_admission(candidate, text)

    assert assessment.decision == AdmissionDecision.REJECT
    assert assessment.reason == "speculative_relationship_state"


def test_single_evidence_interaction_pattern_cannot_be_confirmed() -> None:
    text = "We spoke once after class"
    candidate = _candidate(
        kind=MemoryKind.INTERACTION_PATTERN,
        text=text,
        subject="relationship",
        raw_predicate="interaction_frequency",
        payload={"metric": "interaction_frequency", "current": "low"},
    )

    assessment = assess_memory_admission(candidate, text)

    assert assessment.decision == AdmissionDecision.STRONG_REVIEW
    assert assessment.decision != AdmissionDecision.CONFIRM
    assert assessment.score_breakdown["pattern_has_frequency"] is False
    assert assessment.score_breakdown["pattern_has_multiple_evidence"] is False


def test_evidence_that_is_not_a_source_substring_is_rejected() -> None:
    candidate = _preference(
        "quiet cafes",
        text="I want somewhere for a date",
        evidence_spans=["I explicitly prefer quiet cafes"],
    )

    assessment = assess_memory_admission(candidate, candidate.original_text)

    assert assessment.decision == AdmissionDecision.REJECT
    assert assessment.reason == "evidence_not_in_source"
    assert assessment.score_breakdown["evidence_is_source_substring"] is False
    assert assessment.score_breakdown["evidence_adjustment"] == -0.5


def test_claim_relation_resolver_covers_all_six_relations() -> None:
    cuisine = _item(
        _preference("日料"),
        memory_id="cuisine",
        status=MemoryStatus.CONFIRMED,
    )
    old_state = _item(
        _relationship_state("reduced"),
        memory_id="old-contact-state",
        status=MemoryStatus.CONFIRMED,
    )

    resolutions = {
        "same": resolve_claim_relation(
            _preference("日本料理"),
            [cuisine],
            incoming_status=MemoryStatus.CONFIRMED,
        ),
        "complementary": resolve_claim_relation(
            _preference("寿司"),
            [cuisine],
            incoming_status=MemoryStatus.CONFIRMED,
        ),
        "update": resolve_claim_relation(
            _relationship_state("restored"),
            [old_state],
            incoming_status=MemoryStatus.CONFIRMED,
        ),
        "contradiction": resolve_claim_relation(
            _relationship_state("restored"),
            [old_state],
            incoming_status=MemoryStatus.PROPOSED,
        ),
        "unrelated": resolve_claim_relation(
            _candidate(
                kind=MemoryKind.STABLE_FACT,
                text="I have romantic feelings for her",
                canonical_predicate="relationship.romantic_interest",
            ),
            [old_state],
            incoming_status=MemoryStatus.CONFIRMED,
        ),
        "uncertain": resolve_claim_relation(
            _candidate(
                kind=MemoryKind.STABLE_FACT,
                text="Our shared playlist has a special meaning",
                raw_predicate="shared_playlist_symbolism",
            ),
            [old_state],
            incoming_status=MemoryStatus.PROPOSED,
        ),
    }

    assert {result.relation for result in resolutions.values()} == set(ClaimRelation)
    assert resolutions["same"].target_memory_ids == (cuisine.id,)
    assert resolutions["complementary"].target_memory_ids == (cuisine.id,)
    assert resolutions["update"].target_memory_ids == (old_state.id,)
    assert resolutions["contradiction"].target_memory_ids == (old_state.id,)
    assert resolutions["contradiction"].relation == ClaimRelation.CONTRADICTION
    assert resolutions["contradiction"].relation != ClaimRelation.UPDATE


async def test_in_memory_batch_retry_merges_evidence_and_writes_audit() -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    first_candidate = _preference("日料", text="She likes Japanese food")
    first_batch = MemoryWriteBatch(
        source_message_id="message-1",
        operations=[_operation(first_candidate)],
    )

    first = await store.commit_memory_batch(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        batch=first_batch,
    )
    retry = await store.commit_memory_batch(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        batch=first_batch,
    )

    assert first.saved[0].created is True
    assert retry.saved[0].created is False
    assert retry.saved[0].item.id == first.saved[0].item.id
    assert len(first.audits) == len(retry.audits) == 1

    second_evidence = "She says Japanese cuisine is her favorite"
    same_claim_batch = MemoryWriteBatch(
        source_message_id="message-2",
        operations=[
            _operation(
                _preference("日本料理", text=second_evidence),
                relation=ClaimRelation.SAME,
                target_memory_ids=[first.saved[0].item.id],
            )
        ],
    )
    merged = await store.commit_memory_batch(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        batch=same_claim_batch,
    )

    active = await store.list_memories(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
    )
    message_2_audits = await store.list_transition_audits(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        source_message_id="message-2",
    )
    assert merged.saved[0].created is False
    assert merged.saved[0].item.id == first.saved[0].item.id
    assert len(active) == 1
    assert active[0].evidence_spans == [
        "She likes Japanese food",
        second_evidence,
    ]
    assert len(message_2_audits) == 1
    assert message_2_audits[0].incoming_memory_id == active[0].id
    assert message_2_audits[0].target_memory_ids == [active[0].id]
    assert message_2_audits[0].relation == ClaimRelation.SAME
    assert message_2_audits[0].decision == AdmissionDecision.CONFIRM
    assert message_2_audits[0].canonical_predicate == "preference.food.cuisine"
    assert message_2_audits[0].evidence == [second_evidence]


async def test_same_preference_aliases_merge_across_raw_preference_types() -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    first_candidate = _preference(
        "日本料理",
        text="She likes Japanese food",
        preference_type="food",
    )
    second_candidate = _preference(
        "日料",
        text="Japanese cuisine is her favorite",
        preference_type="cuisine",
    )
    assert memory_dedupe_key(first_candidate) == memory_dedupe_key(second_candidate)

    first = await store.commit_memory_batch(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        batch=MemoryWriteBatch(operations=[_operation(first_candidate)]),
    )
    resolution = resolve_claim_relation(
        second_candidate,
        [first.saved[0].item],
        incoming_status=MemoryStatus.CONFIRMED,
    )
    second = await store.commit_memory_batch(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        batch=MemoryWriteBatch(
            operations=[
                _operation(
                    second_candidate,
                    relation=resolution.relation,
                    target_memory_ids=list(resolution.target_memory_ids),
                )
            ]
        ),
    )

    active = await store.list_memories(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        status=MemoryStatus.CONFIRMED,
    )
    assert resolution.relation == ClaimRelation.SAME
    assert second.saved[0].created is False
    assert len(active) == 1
    assert active[0].evidence_spans == [
        "She likes Japanese food",
        "Japanese cuisine is her favorite",
    ]


async def test_in_memory_batch_rolls_back_memory_status_plan_and_audit() -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    seed = await store.commit_memory_batch(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        batch=MemoryWriteBatch(
            source_message_id="seed-message",
            operations=[_operation(_preference("日料"))],
        ),
    )
    original = seed.saved[0].item
    await store.save_relationship_plan(
        RelationshipPlan(
            plan_id="active-plan",
            user_id=USER_ID,
            relationship_id=RELATIONSHIP_ID,
            activity_type="museum",
            status=PlanStatus.PROPOSED,
        )
    )
    await store.save_relationship_plan(
        RelationshipPlan(
            plan_id="terminal-plan",
            user_id=USER_ID,
            relationship_id=RELATIONSHIP_ID,
            activity_type="concert",
            status=PlanStatus.CANCELLED,
        )
    )
    audits_before = await store.list_transition_audits(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
    )
    failing_batch = MemoryWriteBatch(
        source_message_id="failing-message",
        operations=[_operation(_preference("川菜"))],
        status_updates=[
            MemoryStatusUpdate(
                memory_id=original.id,
                status=MemoryStatus.SUPERSEDED,
                rule_name="test_status_update",
                reason="Must be rolled back",
            )
        ],
        plan_updates=[
            RelationshipPlanStatusUpdate(
                plan_id="active-plan",
                status=PlanStatus.CONFIRMED,
            ),
            RelationshipPlanStatusUpdate(
                plan_id="terminal-plan",
                status=PlanStatus.CONFIRMED,
            ),
        ],
    )

    with pytest.raises(ValueError, match="invalid relationship plan transition"):
        await store.commit_memory_batch(
            user_id=USER_ID,
            relationship_id=RELATIONSHIP_ID,
            batch=failing_batch,
        )

    memories_after = await store.list_memories(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
    )
    active_plan = await store.get_relationship_plan("active-plan", USER_ID)
    terminal_plan = await store.get_relationship_plan("terminal-plan", USER_ID)
    audits_after = await store.list_transition_audits(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
    )
    assert [(item.id, item.status) for item in memories_after] == [
        (original.id, MemoryStatus.CONFIRMED)
    ]
    assert active_plan is not None and active_plan.status == PlanStatus.PROPOSED
    assert terminal_plan is not None and terminal_plan.status == PlanStatus.CANCELLED
    assert [audit.id for audit in audits_after] == [audit.id for audit in audits_before]


def test_context_partitions_confirmed_uncertain_and_conflicted_memories() -> None:
    confirmed_current = _item(
        _relationship_state("restored"),
        memory_id="confirmed-current",
        status=MemoryStatus.CONFIRMED,
    )
    confirmed_fact = _item(
        _candidate(
            kind=MemoryKind.STABLE_FACT,
            text="I work near the university",
            canonical_predicate="relationship.romantic_interest",
        ),
        memory_id="confirmed-fact",
        status=MemoryStatus.CONFIRMED,
    )
    uncertain = _item(
        _candidate(
            kind=MemoryKind.INTERACTION_EVENT,
            text="We spoke briefly after class",
            canonical_predicate="relationship.romantic_interest",
        ),
        memory_id="uncertain-event",
        status=MemoryStatus.PROPOSED,
    )
    conflicted = _item(
        _preference("川菜").model_copy(
            update={"claim_relation": ClaimRelation.CONTRADICTION}
        ),
        memory_id="conflicted-preference",
        status=MemoryStatus.PROPOSED,
    )

    context = attach_memories(
        RelationshipContext(
            user_id=USER_ID,
            relationship_id=RELATIONSHIP_ID,
        ),
        [
            confirmed_current,
            confirmed_fact,
            uncertain,
            conflicted,
        ],
        reference_time=NOW,
    )

    assert {item.id for item in context.confirmed_current_state} == {
        confirmed_current.id
    }
    assert {item.id for item in context.confirmed_long_term} == {confirmed_fact.id}
    assert {item.id for item in context.uncertain_items} == {
        uncertain.id,
        conflicted.id,
    }
    assert {item.id for item in context.conflicted_items} == {conflicted.id}


def test_context_includes_confirmed_preference_in_long_term_partition() -> None:
    confirmed_preference = _item(
        _preference("日料"),
        memory_id="confirmed-preference",
        status=MemoryStatus.CONFIRMED,
    )

    context = attach_memories(
        RelationshipContext(
            user_id=USER_ID,
            relationship_id=RELATIONSHIP_ID,
        ),
        [confirmed_preference],
        reference_time=NOW,
    )

    assert [item.id for item in context.confirmed_long_term] == [
        confirmed_preference.id
    ]


async def test_sqlite_batch_commits_update_chain_and_transition_audit(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory-v2-update.db", clock=lambda: NOW)
    source_message = await store.add_message(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        role=MessageRole.USER,
        content="Contact has been restored.",
    )
    previous = await store.save_memory(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        candidate=_relationship_state("reduced"),
        status=MemoryStatus.CONFIRMED,
    )
    batch = MemoryWriteBatch(
        source_message_id=source_message.id,
        operations=[
            _operation(
                _relationship_state("restored"),
                relation=ClaimRelation.UPDATE,
                target_memory_ids=[previous.item.id],
            )
        ],
    )

    committed = await store.commit_memory_batch(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        batch=batch,
    )

    updated_previous = await store.get_memory(previous.item.id, USER_ID)
    active_confirmed = await store.list_memories(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        status=MemoryStatus.CONFIRMED,
    )
    audits = await store.list_transition_audits(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        source_message_id=source_message.id,
    )
    assert updated_previous is not None
    assert updated_previous.status == MemoryStatus.SUPERSEDED
    assert len(active_confirmed) == 1
    assert active_confirmed[0].state_value == "restored"
    assert committed.saved[0].item.supersedes_id == previous.item.id
    assert len(audits) == 1
    assert audits[0].incoming_memory_id == committed.saved[0].item.id
    assert audits[0].target_memory_ids == [previous.item.id]
    assert audits[0].relation == ClaimRelation.UPDATE
    assert audits[0].decision == AdmissionDecision.CONFIRM
    await store.aclose()


async def test_sqlite_batch_failure_rolls_back_memory_status_plan_and_audit(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory-v2-rollback.db", clock=lambda: NOW)
    seed_message = await store.add_message(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        role=MessageRole.USER,
        content="She likes Japanese food.",
    )
    seed = await store.commit_memory_batch(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        batch=MemoryWriteBatch(
            source_message_id=seed_message.id,
            operations=[_operation(_preference("日料"))],
        ),
    )
    original = seed.saved[0].item
    for plan_id, activity_type, status in (
        ("sqlite-active-plan", "museum", PlanStatus.PROPOSED),
        ("sqlite-terminal-plan", "concert", PlanStatus.CANCELLED),
    ):
        await store.save_relationship_plan(
            RelationshipPlan(
                plan_id=plan_id,
                user_id=USER_ID,
                relationship_id=RELATIONSHIP_ID,
                activity_type=activity_type,
                status=status,
            )
        )
    audits_before = await store.list_transition_audits(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
    )
    failing_message = await store.add_message(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        role=MessageRole.USER,
        content="This write must roll back.",
    )
    failing_batch = MemoryWriteBatch(
        source_message_id=failing_message.id,
        operations=[_operation(_preference("川菜"))],
        status_updates=[
            MemoryStatusUpdate(
                memory_id=original.id,
                status=MemoryStatus.SUPERSEDED,
                rule_name="test_sqlite_status_update",
                reason="Must be rolled back",
            )
        ],
        plan_updates=[
            RelationshipPlanStatusUpdate(
                plan_id="sqlite-active-plan",
                status=PlanStatus.CONFIRMED,
            ),
            RelationshipPlanStatusUpdate(
                plan_id="sqlite-terminal-plan",
                status=PlanStatus.CONFIRMED,
            ),
        ],
    )

    with pytest.raises(ValueError, match="invalid relationship plan transition"):
        await store.commit_memory_batch(
            user_id=USER_ID,
            relationship_id=RELATIONSHIP_ID,
            batch=failing_batch,
        )

    memories_after = await store.list_memories(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
    )
    active_plan = await store.get_relationship_plan("sqlite-active-plan", USER_ID)
    terminal_plan = await store.get_relationship_plan("sqlite-terminal-plan", USER_ID)
    audits_after = await store.list_transition_audits(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
    )
    assert [(item.id, item.status) for item in memories_after] == [
        (original.id, MemoryStatus.CONFIRMED)
    ]
    assert active_plan is not None and active_plan.status == PlanStatus.PROPOSED
    assert terminal_plan is not None and terminal_plan.status == PlanStatus.CANCELLED
    assert [audit.id for audit in audits_after] == [audit.id for audit in audits_before]
    assert all(audit.source_message_id != failing_message.id for audit in audits_after)
    await store.aclose()


async def test_service_uses_in_batch_operation_targets_for_state_updates() -> None:
    source_text = "记一下：contact was unavailable; contact is restored."
    unavailable = _relationship_state(
        "unavailable",
        text="contact was unavailable",
    )
    restored = _relationship_state(
        "restored",
        text="contact is restored",
    )
    store = CapturingInMemoryMemoryStore()
    service = MemoryService(
        store,
        StaticExtractor(
            [
                _claim(unavailable, "unavailable-state"),
                _claim(restored, "restored-state"),
            ]
        ),
        clock=lambda: NOW,
    )

    result = await service.remember_text(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        text=source_text,
    )

    all_memories = await store.list_memories(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
    )
    by_value = {item.state_value: item for item in all_memories}
    confirmed = [item for item in all_memories if item.status == MemoryStatus.CONFIRMED]
    audits = await store.list_transition_audits(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        source_message_id=result.message.id,
    )
    audit_by_incoming_id = {audit.incoming_memory_id: audit for audit in audits}

    assert store.last_batch is not None
    assert len(store.last_batch.operations) == 2
    assert store.last_batch.operations[1].target_operation_indexes == [0]
    assert len(confirmed) == 1
    assert confirmed[0].state_value == "restored"
    assert by_value["unavailable"].status == MemoryStatus.SUPERSEDED
    assert by_value["restored"].supersedes_id == by_value["unavailable"].id
    restored_audit = audit_by_incoming_id[by_value["restored"].id]
    assert restored_audit.relation == ClaimRelation.UPDATE
    assert restored_audit.target_memory_ids == [by_value["unavailable"].id]
    assert "same_state_dimension_in_batch" in restored_audit.rule_name


async def test_service_trace_exposes_complete_candidate_governance() -> None:
    source_text = "\u8bb0\u4e00\u4e0b: we started talking again."
    store = InMemoryMemoryStore(clock=lambda: NOW)
    previous = await store.save_memory(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        candidate=_relationship_state("unavailable"),
        status=MemoryStatus.CONFIRMED,
    )
    claim = AtomicClaim(
        claim_id="contact-restored",
        kind=MemoryKind.RELATIONSHIP_STATE,
        subject="relationship",
        predicate="started_talking_again",
        summary="Contact is restored.",
        evidence_spans=[source_text],
        time_kind=TimeKind.TIMELESS,
        confidence=1.0,
        explicitness=EvidenceExplicitness.EXPLICIT,
        payload={"state_value": "restored"},
    )
    service = MemoryService(
        store,
        StaticExtractor([claim]),
        clock=lambda: NOW,
    )
    trace = ExecutionTrace()

    await service.remember_text(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        text=source_text,
        status=MemoryStatus.CONFIRMED,
        trace=trace,
    )

    record = next(
        item for item in trace.snapshot() if item.name == "memory_candidate_governance"
    )
    details = record.details
    assert details["subject"] == "relationship"
    assert details["confidence"] == pytest.approx(1.0)
    assert details["perspective"] == "user_reported"
    assert details["importance"] == 3
    assert json.loads(str(details["evidence_spans_json"])) == [source_text]
    assert details["raw_predicate"] == "started_talking_again"
    assert details["canonical_predicate"] == "contact.status"
    assert details["alias_hit"] is True
    assert details["state_dimension"] == "relationship.contact_status"
    assert details["state_value"] == "restored"
    assert details["extractor_model"] is None
    assert details["verifier_model"] is None
    assert details["prompt_version"] is None
    assert details["lifecycle_review_required"] is False
    assert details["time_kind"] == TimeKind.TIMELESS.value
    assert details["occurred_at"] is None
    assert details["period_start"] is None
    assert details["period_end"] is None
    assert details["temporal_precision"] == TemporalPrecision.UNKNOWN.value
    payload = json.loads(str(details["payload_json"]))
    assert payload["state_value"] == "restored"
    assert payload["predicate"] == "started_talking_again"
    assert details["admission_decision"] == AdmissionDecision.CONFIRM.value
    assert details["admission_score"] == pytest.approx(0.95)
    breakdown = json.loads(str(details["score_breakdown_json"]))
    assert breakdown["conflict"] is True
    assert json.loads(str(details["compared_memory_ids_json"])) == [previous.item.id]
    assert details["claim_relation"] == ClaimRelation.UPDATE.value
    assert json.loads(str(details["relation_target_memory_ids_json"])) == [
        previous.item.id
    ]
    assert details["planned_action"] == "replace"
    assert json.loads(str(details["planned_target_memory_ids_json"])) == [
        previous.item.id
    ]


async def test_weak_interaction_decline_extracts_reduced_without_confirming_mood() -> None:
    source_text = "她最近不怎么理我，也可能是她心情不好"
    contact_evidence = "她最近不怎么理我"
    matched_span = "最近不怎么理我"
    mood_evidence = "可能是她心情不好"
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        StaticExtractor(
            [
                AtomicClaim(
                    claim_id="contact-reduced",
                    kind=MemoryKind.RELATIONSHIP_STATE,
                    subject="relationship",
                    predicate="contact_frequency_declined",
                    summary="Contact has become less frequent.",
                    evidence_spans=[contact_evidence],
                    time_kind=TimeKind.TIMELESS,
                    confidence=0.98,
                    explicitness=EvidenceExplicitness.EXPLICIT,
                    payload={
                        "state_dimension": "relationship.contact_status",
                        "state_value": "reduced",
                    },
                ),
                AtomicClaim(
                    claim_id="partner-mood-inference",
                    kind=MemoryKind.STABLE_FACT,
                    subject="partner",
                    predicate="partner_mood_bad",
                    summary="Partner may be in a bad mood.",
                    evidence_spans=[mood_evidence],
                    time_kind=TimeKind.TIMELESS,
                    confidence=0.95,
                    perspective=MemoryPerspective.MODEL_INFERRED,
                    explicitness=EvidenceExplicitness.WEAKLY_INFERRED,
                    requires_inference=True,
                ),
            ]
        ),
        clock=lambda: NOW,
    )
    trace = ExecutionTrace()

    result = await service.remember_text(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        text=source_text,
        trace=trace,
    )

    assert result.gate_decision is not None
    assert result.gate_decision.should_extract is True
    assert result.gate_decision.matched_rule == "temporal_interaction_decline"
    assert result.gate_decision.matched_span == matched_span
    gate_trace = next(item for item in trace.snapshot() if item.name == "memory_gate")
    assert gate_trace.details["gate_reason"] == "durable_signal"
    assert gate_trace.details["matched_rule"] == "temporal_interaction_decline"
    assert gate_trace.details["matched_span"] == matched_span

    saved = [save.item for save in result.saved]
    reduced = [
        item
        for item in saved
        if item.canonical_predicate == "contact.status"
        and item.state_value == "reduced"
    ]
    assert len(reduced) == 1
    assert reduced[0].status == MemoryStatus.CONFIRMED
    mood_memories = [item for item in saved if item.raw_predicate == "partner_mood_bad"]
    assert all(item.status != MemoryStatus.CONFIRMED for item in mood_memories)
    assert result.rejected_by_policy == 1


async def test_service_rejects_strong_verifier_target_outside_allowed_scope() -> None:
    source_text = "记一下：our shared playlist symbolizes mutual understanding."
    store = InMemoryMemoryStore(clock=lambda: NOW)
    existing = await store.save_memory(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        candidate=_relationship_state("reduced"),
        status=MemoryStatus.CONFIRMED,
    )
    candidate = _candidate(
        kind=MemoryKind.STABLE_FACT,
        text="our shared playlist symbolizes mutual understanding",
        subject="relationship",
        raw_predicate="shared_playlist_symbolism",
        confidence=0.95,
    )
    verifier = OutOfScopeTargetVerifier("foreign-memory-id")
    service = MemoryService(
        store,
        StaticExtractor([_claim(candidate, "custom-claim")]),
        verifier=verifier,
        clock=lambda: NOW,
    )

    result = await service.remember_text(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        text=source_text,
    )

    existing_after = await store.get_memory(existing.item.id, USER_ID)
    audits = await store.list_transition_audits(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        source_message_id=result.message.id,
    )
    assert verifier.allowed_target_ids == {existing.item.id}
    assert existing_after is not None and existing_after.status == MemoryStatus.CONFIRMED
    assert len(result.saved) == 1
    assert result.saved[0].item.status == MemoryStatus.PROPOSED
    assert result.saved[0].item.predicate_type == PredicateType.CUSTOM
    assert result.saved[0].item.lifecycle_review_required is True
    assert len(audits) == 1
    assert audits[0].relation == ClaimRelation.UNCERTAIN
    assert audits[0].target_memory_ids == []
    assert audits[0].rule_name == "strong_verifier_fallback"
    assert "claim verifier returned a target outside" in str(
        audits[0].score_breakdown["strong_verifier_error"]
    )


async def test_service_cannot_replace_non_source_evidence_to_bypass_admission() -> None:
    source_text = "记一下：Japanese food came up in conversation."
    hallucinated_evidence = "She explicitly said Japanese food is her favorite"
    candidate = _preference(
        "日料",
        text=hallucinated_evidence,
        evidence_spans=[hallucinated_evidence],
    )
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        StaticExtractor([_claim(candidate, "invalid-evidence")]),
        clock=lambda: NOW,
    )

    result = await service.remember_text(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        text=source_text,
        status=MemoryStatus.CONFIRMED,
    )

    memories = await store.list_memories(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
    )
    audits = await store.list_transition_audits(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        source_message_id=result.message.id,
    )
    assert result.saved == []
    assert result.rejected_by_policy == 1
    assert memories == []
    assert len(audits) == 1
    assert audits[0].decision == AdmissionDecision.REJECT
    assert audits[0].reason == "evidence_not_in_source"
    assert audits[0].evidence == [hallucinated_evidence]
    assert audits[0].score_breakdown["evidence_is_source_substring"] is False


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_ttl_expiration_writes_lifecycle_audit(
    backend: str,
    tmp_path: Path,
) -> None:
    clock = [NOW]
    store = (
        InMemoryMemoryStore(clock=lambda: clock[0])
        if backend == "memory"
        else SQLiteMemoryStore(tmp_path / "ttl-audit.db", clock=lambda: clock[0])
    )
    candidate = _candidate(
        kind=MemoryKind.STABLE_FACT,
        text="I work near the university",
        canonical_predicate="relationship.romantic_interest",
    ).model_copy(update={"expires_at": NOW + timedelta(hours=1)})
    saved = await store.save_memory(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        candidate=candidate,
        status=MemoryStatus.CONFIRMED,
    )

    clock[0] = NOW + timedelta(hours=2)
    await store.list_memories(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
    )
    expired = await store.get_memory(saved.item.id, USER_ID)
    audits = await store.list_transition_audits(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
    )

    assert expired is not None and expired.status == MemoryStatus.EXPIRED
    ttl_audit = next(audit for audit in audits if audit.rule_name == "ttl_expired")
    assert ttl_audit.target_memory_ids == [saved.item.id]
    assert ttl_audit.score_breakdown["target_memory_status"] == "expired"
    await store.aclose()


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_direct_status_transition_writes_lifecycle_audit(
    backend: str,
    tmp_path: Path,
) -> None:
    store = (
        InMemoryMemoryStore(clock=lambda: NOW)
        if backend == "memory"
        else SQLiteMemoryStore(tmp_path / "status-audit.db", clock=lambda: NOW)
    )
    saved = await store.save_memory(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        candidate=_preference("日料"),
        status=MemoryStatus.CONFIRMED,
    )

    await store.set_memory_status(saved.item.id, USER_ID, MemoryStatus.REJECTED)
    audits = await store.list_transition_audits(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
    )

    status_audit = next(
        audit for audit in audits if audit.rule_name == "set_memory_status"
    )
    assert status_audit.target_memory_ids == [saved.item.id]
    assert status_audit.score_breakdown["target_memory_status"] == "rejected"
    await store.aclose()


async def test_planned_event_retry_uses_stable_identity_for_same_message() -> None:
    source_text = "记一下：我们周六上午一起去爬山"
    planned = _candidate(
        kind=MemoryKind.PLANNED_EVENT,
        text="我们周六上午一起去爬山",
        subject="relationship",
        raw_predicate="plan_hiking",
        payload={
            "activity_type": "爬山",
            "participants": ["user", "partner"],
            "event_status": "planned",
        },
    ).model_copy(update={"period_start": NOW + timedelta(days=3)})
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        StaticExtractor([_claim(planned, "hiking-plan")]),
        clock=lambda: NOW,
    )
    message = await service.record_message(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        role=MessageRole.USER,
        content=source_text,
    )

    first = await service.remember_recorded_message(message=message, text=source_text)
    second = await service.remember_recorded_message(message=message, text=source_text)
    memories = await store.list_memories(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        status=MemoryStatus.PROPOSED,
    )
    plans = await store.list_relationship_plans(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
    )

    assert first.saved[0].created is True
    assert second.saved[0].created is False
    assert first.saved[0].item.id == second.saved[0].item.id
    assert len(memories) == 1
    assert len(plans) == 1
    assert memories[0].payload["plan_id"] == plans[0].plan_id
    assert memories[0].payload["plan_id_generated"] is True


def test_state_shaped_interaction_events_remain_independent_history() -> None:
    first_candidate = _candidate(
        kind=MemoryKind.INTERACTION_EVENT,
        text="I confessed last week",
        subject="relationship",
        raw_predicate="confessed",
    ).model_copy(update={"occurred_at": NOW - timedelta(days=7)})
    second_candidate = _candidate(
        kind=MemoryKind.INTERACTION_EVENT,
        text="I clarified my confession today",
        subject="relationship",
        raw_predicate="confessed",
    ).model_copy(update={"occurred_at": NOW})
    first_item = _item(
        first_candidate,
        memory_id="first-confession-event",
        status=MemoryStatus.CONFIRMED,
    )

    resolution = resolve_claim_relation(
        second_candidate,
        [first_item],
        incoming_status=MemoryStatus.CONFIRMED,
    )
    context = attach_memories(
        RelationshipContext(user_id=USER_ID, relationship_id=RELATIONSHIP_ID),
        [
            first_item,
            _item(
                second_candidate,
                memory_id="second-confession-event",
                status=MemoryStatus.CONFIRMED,
            ),
        ],
        reference_time=NOW,
    )

    assert first_candidate.state_dimension == "relationship.confession_status"
    assert memory_dedupe_key(first_candidate) != memory_dedupe_key(second_candidate)
    assert resolution.relation == ClaimRelation.UNRELATED
    assert {item.id for item in context.recent_events} == {
        "first-confession-event",
        "second-confession-event",
    }


async def test_strong_verifier_whitelist_matches_only_supplied_context_memories() -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    seeded: list[MemoryItem] = []
    for index in range(9):
        saved = await store.save_memory(
            user_id=USER_ID,
            relationship_id=RELATIONSHIP_ID,
            candidate=_candidate(
                kind=MemoryKind.STABLE_FACT,
                text=f"stable fact {index}",
                raw_predicate=f"stable_fact_{index}",
            ).model_copy(update={"importance": 1 if index == 0 else 5}),
            status=MemoryStatus.CONFIRMED,
        )
        seeded.append(saved.item)
    omitted_target = seeded[0].id
    verifier = OutOfScopeTargetVerifier(omitted_target)
    candidate = _candidate(
        kind=MemoryKind.STABLE_FACT,
        text="our shared playlist symbolizes mutual understanding",
        subject="relationship",
        raw_predicate="shared_playlist_symbolism",
    )
    service = MemoryService(
        store,
        StaticExtractor([_claim(candidate, "playlist-symbolism")]),
        verifier=verifier,
        clock=lambda: NOW,
    )

    result = await service.remember_text(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        text="记一下：our shared playlist symbolizes mutual understanding",
    )
    audits = await store.list_transition_audits(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        source_message_id=result.message.id,
    )

    assert verifier.allowed_target_ids is not None
    assert omitted_target not in verifier.allowed_target_ids
    assert verifier.allowed_target_ids < {item.id for item in seeded}
    assert result.saved[0].item.status == MemoryStatus.PROPOSED
    assert audits[0].rule_name == "strong_verifier_fallback"


async def test_strong_verifier_cannot_confirm_single_event_as_pattern() -> None:
    source_text = "记一下：we spoke once after class"
    candidate = _candidate(
        kind=MemoryKind.INTERACTION_PATTERN,
        text="we spoke once after class",
        subject="relationship",
        raw_predicate="interaction_frequency",
        payload={"metric": "interaction_frequency", "current": "low"},
    )
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        StaticExtractor([_claim(candidate, "single-event-pattern")]),
        verifier=SupportingPatternVerifier(),
        clock=lambda: NOW,
    )

    result = await service.remember_text(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        text=source_text,
    )

    assert len(result.saved) == 1
    assert result.saved[0].item.status == MemoryStatus.PROPOSED
    assert result.saved[0].item.admission_decision == AdmissionDecision.STRONG_REVIEW


async def test_admission_policy_default_ttl_override_is_applied() -> None:
    source_text = "记一下：I prefer quiet cafes"
    candidate = _preference("quiet cafes", text="I prefer quiet cafes")
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        StaticExtractor([_claim(candidate, "quiet-cafe-preference")]),
        admission_policy_overrides={"preference": {"default_ttl_days": 2}},
        clock=lambda: NOW,
    )

    result = await service.remember_text(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        text=source_text,
    )

    assert result.saved[0].item.expires_at == NOW + timedelta(days=2)


async def test_sqlite_schema_migration_rolls_back_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "migration-rollback.db"
    initialized = SQLiteMemoryStore(database_path, clock=lambda: NOW)
    await initialized.initialize()
    await initialized.aclose()
    with sqlite3.connect(database_path) as connection:
        connection.execute("ALTER TABLE memory_items DROP COLUMN verifier_model")
        connection.execute("PRAGMA user_version = 5")

    migrate = sqlite_memory._migrate_schema

    async def fail_after_migration(connection) -> None:
        await migrate(connection)
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(sqlite_memory, "_migrate_schema", fail_after_migration)
    failing_store = SQLiteMemoryStore(database_path, clock=lambda: NOW)

    with pytest.raises(RuntimeError, match="injected migration failure"):
        await failing_store.initialize()

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(memory_items)")
        }
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert "verifier_model" not in columns
    assert version == 5


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_batch_plan_transition_writes_exactly_one_audit(
    backend: str,
    tmp_path: Path,
) -> None:
    store = (
        InMemoryMemoryStore(clock=lambda: NOW)
        if backend == "memory"
        else SQLiteMemoryStore(tmp_path / "plan-audit.db", clock=lambda: NOW)
    )
    await store.save_relationship_plan(
        RelationshipPlan(
            plan_id="single-audit-plan",
            user_id=USER_ID,
            relationship_id=RELATIONSHIP_ID,
            activity_type="museum",
            status=PlanStatus.PROPOSED,
        )
    )

    result = await store.commit_memory_batch(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        batch=MemoryWriteBatch(
            plan_updates=[
                RelationshipPlanStatusUpdate(
                    plan_id="single-audit-plan",
                    status=PlanStatus.CONFIRMED,
                )
            ]
        ),
    )
    audits = await store.list_transition_audits(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
    )

    plan_audits = [
        audit for audit in audits if audit.canonical_predicate == "plan.status"
    ]
    assert len(result.audits) == 1
    assert len(plan_audits) == 1
    assert plan_audits[0].rule_name == "relationship_plan_confirmed"
    assert plan_audits[0].score_breakdown == {
        "plan_id": "single-audit-plan",
        "target_plan_status": "confirmed",
    }
    await store.aclose()

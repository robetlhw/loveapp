"""Regression coverage for the v3.2 mutation/link/retrieval boundary.

These tests intentionally use deterministic candidates and stores.  They do
not exercise an LLM and do not change any evaluation Gold data.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore, SQLiteMemoryStore
from loveapp.application.memory import MemoryService, _event_can_support_state
from loveapp.domain.advice import RelationshipContext
from loveapp.domain.memory import (
    AdmissionDecision,
    AtomicClaim,
    AtomicExtraction,
    ClaimRelation,
    EvidenceExplicitness,
    MemoryCandidate,
    MemoryKind,
    MemoryStatus,
    MessageRole,
    MutationAction,
    TimeKind,
)
from loveapp.domain.memory_write import MemoryWriteBatch, MemoryWriteOperation

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)


class _StaticExtractor:
    def __init__(self, claims: list[AtomicClaim]) -> None:
        self._extraction = AtomicExtraction(claims=claims)

    async def extract(self, text: str, **kwargs: object) -> AtomicExtraction:
        del text, kwargs
        return self._extraction.model_copy(deep=True)


def _event(text: str = "昨天因为迟到吵架了") -> AtomicClaim:
    return AtomicClaim(
        claim_id="event-1",
        kind=MemoryKind.INTERACTION_EVENT,
        subject="relationship",
        predicate="conflict_event",
        summary=text,
        evidence_spans=[text],
        time_kind=TimeKind.POINT,
        occurred_at=NOW,
        payload={
            "event_type": "conflict",
            "action": "argue",
            "cause": {"category": "lateness"},
            "participants": ["user", "partner"],
        },
        confidence=1.0,
        explicitness=EvidenceExplicitness.EXPLICIT,
    )


def _active_state(text: str = "现在还在冷战") -> AtomicClaim:
    return AtomicClaim(
        claim_id="state-1",
        kind=MemoryKind.RELATIONSHIP_STATE,
        subject="relationship",
        predicate="relationship.conflict_status",
        summary=text,
        evidence_spans=[text],
        time_kind=TimeKind.TIMELESS,
        canonical_predicate="relationship.conflict_status",
        state_dimension="relationship.conflict_status",
        state_value="active",
        payload={
            "state_dimension": "relationship.conflict_status",
            "state_value": "active",
        },
        confidence=1.0,
        explicitness=EvidenceExplicitness.EXPLICIT,
    )


def _operation(candidate: MemoryCandidate, *, action: MutationAction) -> MemoryWriteOperation:
    candidate = candidate.model_copy(
        update={
            "admission_score": 0.98,
            "admission_decision": AdmissionDecision.CONFIRM,
            "claim_relation": ClaimRelation.UNRELATED,
        }
    )
    return MemoryWriteOperation(
        candidate=candidate,
        status=MemoryStatus.CONFIRMED,
        relation=ClaimRelation.UNRELATED,
        mutation_action=action,
        rule_name="test_create",
        reason="v3.2 contract test",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_event_state_link_is_resolved_inside_one_atomic_batch(
    backend: str,
    tmp_path: Path,
) -> None:
    store = (
        SQLiteMemoryStore(tmp_path / "event-state.db", clock=lambda: NOW)
        if backend == "sqlite"
        else InMemoryMemoryStore(clock=lambda: NOW)
    )
    await store.save_relationship_context(RelationshipContext(user_id="u", relationship_id="r"))
    await store.add_message(
        user_id="u",
        relationship_id="r",
        role=MessageRole.USER,
        content="event-state batch",
        message_id="message-1",
    )
    event = _event().to_candidate()
    state = _active_state().to_candidate()
    committed = await store.commit_memory_batch(
        user_id="u",
        relationship_id="r",
        batch=MemoryWriteBatch(
            source_message_id="message-1",
            operations=[
                _operation(event, action=MutationAction.CREATE),
                MemoryWriteOperation(
                    candidate=state,
                    status=MemoryStatus.CONFIRMED,
                    relation=ClaimRelation.UNRELATED,
                    mutation_action=MutationAction.LINK,
                    source_event_operation_indexes=[0],
                    rule_name="event_state_link",
                    reason="state is supported by the Event in this batch",
                ),
            ],
        ),
    )

    event_item, state_item = [result.item for result in committed.saved]
    assert state_item.source_event_ids == [event_item.id]
    assert state_item.payload["source_event_ids"] == [event_item.id]
    loaded = await store.get_memory(state_item.id, "u")
    assert loaded is not None and loaded.source_event_ids == [event_item.id]
    audits = await store.list_transition_audits(
        user_id="u",
        relationship_id="r",
        source_message_id="message-1",
    )
    state_audit = next(audit for audit in audits if audit.incoming_memory_id == state_item.id)
    assert state_audit.mutation_action == MutationAction.LINK
    if backend == "sqlite":
        await store.aclose()


@pytest.mark.asyncio
async def test_event_state_link_rolls_back_with_the_batch() -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    await store.save_relationship_context(RelationshipContext(user_id="u", relationship_id="r"))
    event = _event().to_candidate()
    state = _active_state().to_candidate()
    with pytest.raises(ValueError, match="outside the current relationship scope"):
        await store.commit_memory_batch(
            user_id="u",
            relationship_id="r",
            batch=MemoryWriteBatch(
                source_message_id="message-1",
                operations=[
                    _operation(event, action=MutationAction.CREATE),
                    MemoryWriteOperation(
                        candidate=state,
                        status=MemoryStatus.CONFIRMED,
                        relation=ClaimRelation.UNRELATED,
                        mutation_action=MutationAction.LINK,
                        source_event_operation_indexes=[0],
                        rule_name="event_state_link",
                        reason="test rollback",
                    ),
                ],
                status_updates=[
                    {
                        "memory_id": "missing",
                        "status": MemoryStatus.SUPERSEDED,
                        "rule_name": "force_failure",
                        "reason": "rollback",
                    }
                ],
            ),
        )
    assert await store.list_memories(user_id="u", relationship_id="r") == []


@pytest.mark.asyncio
async def test_service_links_event_and_state_candidates_before_commit() -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        _StaticExtractor([_event(), _active_state()]),
        clock=lambda: NOW,
    )
    source_text = (
        "Please remember: yesterday we argued because I was late, "
        "and we are still in a cold war."
    )
    extractor = _StaticExtractor([_event(source_text), _active_state(source_text)])
    service = MemoryService(store, extractor, clock=lambda: NOW)
    result = await service.remember_text(
        user_id="u",
        relationship_id="r",
        text=source_text,
        status=MemoryStatus.CONFIRMED,
    )
    assert len(result.saved) == 2
    event_item = next(
        item.item
        for item in result.saved
        if item.item.kind == MemoryKind.INTERACTION_EVENT
    )
    state_item = next(
        item.item
        for item in result.saved
        if item.item.kind == MemoryKind.RELATIONSHIP_STATE
    )
    assert state_item.source_event_ids == [event_item.id]
    audits = await store.list_transition_audits(
        user_id="u",
        relationship_id="r",
        source_message_id=result.message.id,
    )
    state_audit = next(audit for audit in audits if audit.incoming_memory_id == state_item.id)
    assert state_audit.mutation_action == MutationAction.LINK


def test_mutation_action_is_separate_from_claim_relation() -> None:
    candidate = _active_state().to_candidate()
    operation = _operation(candidate, action=MutationAction.SUPERSEDE)
    assert operation.relation == ClaimRelation.UNRELATED
    assert operation.mutation_action == MutationAction.SUPERSEDE


def test_unrelated_event_is_not_semantically_compatible_with_contact_state() -> None:
    unrelated = _event("我今天买了一杯咖啡").model_copy(
        update={"payload": {"event_type": "unknown_activity"}}
    ).to_candidate()
    contact_state = _active_state("她最近回复越来越慢").to_candidate().model_copy(
        update={
            "canonical_predicate": "interaction.response_engagement",
            "state_dimension": "interaction.response_engagement",
            "state_value": "slow",
        }
    )
    assert _event_can_support_state(unrelated, contact_state) is False

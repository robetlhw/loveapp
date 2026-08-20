from datetime import UTC, datetime
from pathlib import Path

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore, SQLiteMemoryStore
from loveapp.application.memory import MemoryService
from loveapp.application.memory_gate import MemoryGate
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    ClaimRelation,
    EvidenceExplicitness,
    MemoryKind,
    MemoryStatus,
    MessageRole,
)

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
SCOPE = {
    "user_id": "recurrence-user",
    "relationship_id": "recurrence-relationship",
    "conversation_id": "recurrence-conversation",
}

CONTACT_LOW = "\u5979\u6700\u8fd1\u8054\u7cfb\u8d8a\u6765\u8d8a\u5c11\u3002"
CONTACT_NORMAL = "\u6700\u8fd1\u5df2\u7ecf\u6062\u590d\u6b63\u5e38\u4e86\u3002"
CONTACT_LOW_AGAIN = (
    "\u5979\u6700\u8fd1\u53c8\u5f00\u59cb\u8054\u7cfb\u5f97\u5f88\u5c11\u3002"
)
CONFLICT_ACTIVE = "\u6211\u4eec\u73b0\u5728\u8fd8\u5728\u51b7\u6218\u3002"
CONFLICT_RESOLVED = (
    "\u6628\u5929\u5df2\u7ecf\u8bf4\u5f00\u4e86\uff0c\u73b0\u5728\u548c\u597d\u4e86\u3002"
)
CONFLICT_ACTIVE_AGAIN = (
    "\u4f46\u8fd9\u51e0\u5929\u53c8\u5f00\u59cb\u56e0\u4e3a\u94b1\u5435\u67b6\u3002"
)


class SequenceExtractor:
    def __init__(self, *extractions: AtomicExtraction) -> None:
        self._extractions = iter(extractions)

    async def extract(self, text: str, **kwargs) -> AtomicExtraction:
        del text, kwargs
        return next(self._extractions)


def _contact_claim(value: str, evidence: str) -> AtomicClaim:
    return AtomicClaim(
        claim_id=f"contact-{value}",
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="relationship",
        predicate="contact_frequency_changed",
        summary=f"Current contact frequency is {value}",
        evidence_spans=[evidence],
        confidence=0.95,
        explicitness=EvidenceExplicitness.EXPLICIT,
        payload={"metric": "contact_frequency", "current": value},
    )


def _conflict_claim(value: str, evidence: str) -> AtomicClaim:
    return AtomicClaim(
        claim_id=f"conflict-{value}",
        kind=MemoryKind.RELATIONSHIP_STATE,
        subject="relationship",
        predicate="conflict_status",
        summary=f"Current conflict state is {value}",
        evidence_spans=[evidence],
        confidence=0.95,
        explicitness=EvidenceExplicitness.EXPLICIT,
        payload={"state_dimension": "conflict_status", "state_value": value},
    )


def _extraction(claim: AtomicClaim) -> AtomicExtraction:
    return AtomicExtraction(claims=[claim])


def _store(backend: str, tmp_path: Path):
    if backend == "sqlite":
        return SQLiteMemoryStore(tmp_path / "recurrence.db", clock=lambda: NOW)
    return InMemoryMemoryStore(clock=lambda: NOW)


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_contact_low_normal_low_again_creates_a_new_current_row(
    backend: str,
    tmp_path: Path,
) -> None:
    store = _store(backend, tmp_path)
    service = MemoryService(
        store,
        SequenceExtractor(
            _extraction(_contact_claim("low", CONTACT_LOW)),
            _extraction(_contact_claim("normal", CONTACT_NORMAL)),
            _extraction(_contact_claim("low", CONTACT_LOW_AGAIN)),
        ),
        clock=lambda: NOW,
    )

    first = await service.remember_text(
        text=CONTACT_LOW,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    restored = await service.remember_text(
        text=CONTACT_NORMAL,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    recurrent = await service.remember_text(
        text=CONTACT_LOW_AGAIN,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )

    first_low = await store.get_memory(first.saved[0].item.id, SCOPE["user_id"])
    normal = await store.get_memory(restored.saved[0].item.id, SCOPE["user_id"])
    new_low = recurrent.saved[0].item
    context = await service.get_context(
        SCOPE["user_id"],
        SCOPE["relationship_id"],
        query="contact frequency",
    )
    current_contact_ids = {
        item.id
        for item in context.remembered_items
        if item.state_dimension == "interaction.contact_frequency"
    }

    assert first_low is not None and first_low.status == MemoryStatus.SUPERSEDED
    assert normal is not None and normal.status == MemoryStatus.SUPERSEDED
    assert new_low.status == MemoryStatus.CONFIRMED
    assert new_low.state_value == "low"
    assert new_low.id != first_low.id
    assert new_low.source_message_id != first_low.source_message_id
    assert new_low.source_message_id == recurrent.message.id
    assert new_low.supersedes_id == normal.id
    assert new_low.claim_relation == ClaimRelation.UPDATE
    assert current_contact_ids == {new_low.id}


async def test_active_resolved_active_conflict_again_preserves_history() -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(
            _extraction(_conflict_claim("active", CONFLICT_ACTIVE)),
            _extraction(_conflict_claim("resolved", CONFLICT_RESOLVED)),
            _extraction(_conflict_claim("active", CONFLICT_ACTIVE_AGAIN)),
        ),
        clock=lambda: NOW,
    )

    first = await service.remember_text(
        text=CONFLICT_ACTIVE,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    resolved = await service.remember_text(
        text=CONFLICT_RESOLVED,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    recurrent = await service.remember_text(
        text=CONFLICT_ACTIVE_AGAIN,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )

    first_active = await store.get_memory(first.saved[0].item.id, SCOPE["user_id"])
    resolved_after = await store.get_memory(
        resolved.saved[0].item.id,
        SCOPE["user_id"],
    )
    new_active = recurrent.saved[0].item
    context = await service.get_context(
        SCOPE["user_id"],
        SCOPE["relationship_id"],
        query="conflict",
    )
    current_conflict_ids = {
        item.id
        for item in context.remembered_items
        if item.state_dimension == "relationship.conflict_status"
    }

    assert first_active is not None
    assert first_active.status == MemoryStatus.SUPERSEDED
    assert resolved_after is not None
    assert resolved_after.status == MemoryStatus.SUPERSEDED
    assert new_active.status == MemoryStatus.CONFIRMED
    assert new_active.state_value == "active"
    assert new_active.id != first_active.id
    assert new_active.source_message_id != first_active.source_message_id
    assert new_active.supersedes_id == resolved_after.id
    assert current_conflict_ids == {new_active.id}


@pytest.mark.parametrize(
    "text",
    [
        "\u6211\u4eec\u53c8\u5f00\u59cb\u51b7\u6218\u4e86\u3002",
        "\u6211\u4eec\u6700\u8fd1\u53c8\u5f00\u59cb\u51b7\u6218\u4e86\u3002",
        "\u6211\u4eec\u518d\u6b21\u56e0\u4e3a\u94b1\u5435\u67b6\u3002",
    ],
)
def test_recurrence_cues_enter_the_existing_gate(text: str) -> None:
    decision = MemoryGate().evaluate(text)

    assert decision.should_extract is True
    assert "contextual_recurrence" in decision.signals


async def test_repeated_current_state_merges_instead_of_creating_duplicates() -> None:
    repeated = "\u5979\u6700\u8fd1\u8fd8\u662f\u8054\u7cfb\u5f97\u5f88\u5c11\u3002"
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(
            _extraction(_contact_claim("low", CONTACT_LOW)),
            _extraction(_contact_claim("low", repeated)),
        ),
        clock=lambda: NOW,
    )

    first = await service.remember_text(
        text=CONTACT_LOW,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    second = await service.remember_text(
        text=repeated,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    memories = await store.list_memories(
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
    )

    assert second.saved[0].created is False
    assert second.saved[0].item.id == first.saved[0].item.id
    assert second.saved[0].item.claim_relation == ClaimRelation.SAME
    assert len(memories) == 1


async def test_replaying_the_same_source_message_is_idempotent() -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(
            _extraction(_contact_claim("low", CONTACT_LOW)),
            _extraction(_contact_claim("normal", CONTACT_NORMAL)),
            _extraction(_contact_claim("low", CONTACT_LOW_AGAIN)),
            _extraction(_contact_claim("low", CONTACT_LOW_AGAIN)),
        ),
        clock=lambda: NOW,
    )
    first = await service.remember_text(
        text=CONTACT_LOW,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    await service.remember_text(
        text=CONTACT_NORMAL,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    recurrent_source = await service.record_message(
        role=MessageRole.USER,
        content=CONTACT_LOW_AGAIN,
        **SCOPE,
    )

    recurrent = await service.remember_recorded_message(
        message=recurrent_source,
        text=recurrent_source.content,
        status=MemoryStatus.CONFIRMED,
    )
    replay = await service.remember_recorded_message(
        message=recurrent_source,
        text=recurrent_source.content,
        status=MemoryStatus.CONFIRMED,
    )
    memories = await store.list_memories(
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
    )
    first_after = await store.get_memory(first.saved[0].item.id, SCOPE["user_id"])

    assert replay.saved[0].created is False
    assert replay.saved[0].item.id == recurrent.saved[0].item.id
    assert replay.saved[0].item.source_message_id == recurrent_source.id
    assert first_after is not None and first_after.status == MemoryStatus.SUPERSEDED
    assert len(memories) == 3


async def test_proposed_recurrence_does_not_close_confirmed_current_state() -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(
            _extraction(_contact_claim("low", CONTACT_LOW)),
            _extraction(_contact_claim("normal", CONTACT_NORMAL)),
            _extraction(_contact_claim("low", CONTACT_LOW_AGAIN)),
        ),
        clock=lambda: NOW,
    )

    first = await service.remember_text(
        text=CONTACT_LOW,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    restored = await service.remember_text(
        text=CONTACT_NORMAL,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    recurrent = await service.remember_text(
        text=CONTACT_LOW_AGAIN,
        status=MemoryStatus.PROPOSED,
        **SCOPE,
    )

    first_low = await store.get_memory(first.saved[0].item.id, SCOPE["user_id"])
    current_normal = await store.get_memory(
        restored.saved[0].item.id,
        SCOPE["user_id"],
    )
    proposed_low = recurrent.saved[0].item

    assert first_low is not None and first_low.status == MemoryStatus.SUPERSEDED
    assert current_normal is not None
    assert current_normal.status == MemoryStatus.CONFIRMED
    assert proposed_low.status == MemoryStatus.PROPOSED
    assert proposed_low.claim_relation == ClaimRelation.CONTRADICTION
    assert proposed_low.supersedes_id is None

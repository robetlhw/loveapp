from datetime import UTC, datetime
from pathlib import Path

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore, SQLiteMemoryStore
from loveapp.domain.advice import RelationshipContext
from loveapp.domain.memory import (
    MemoryCandidate,
    MemoryKind,
    MemoryStatus,
    MessageRole,
    TimeKind,
)
from loveapp.domain.memory_event_enrichment import (
    EventEnrichmentField,
    GenericEventEnrichment,
)
from loveapp.domain.memory_write import MemoryWriteBatch


def _event(source_id: str) -> MemoryCandidate:
    return MemoryCandidate(
        kind=MemoryKind.INTERACTION_EVENT,
        subject="relationship",
        summary="双方发生了一次冲突",
        original_text="我们吵架了",
        evidence_spans=["吵架了"],
        time_kind=TimeKind.POINT,
        occurred_at=datetime(2026, 9, 10, tzinfo=UTC),
        payload={"event_type": "conflict", "action": "吵架"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_generic_event_enrichment_is_typed_non_destructive_and_idempotent(
    backend: str,
    tmp_path: Path,
) -> None:
    store = (
        SQLiteMemoryStore(tmp_path / "generic-enrichment.db")
        if backend == "sqlite"
        else InMemoryMemoryStore()
    )
    await store.save_relationship_context(RelationshipContext(user_id="u", relationship_id="r"))
    source = await store.add_message(
        user_id="u",
        relationship_id="r",
        role=MessageRole.USER,
        content="我们吵架了",
        message_id="event-source",
    )
    target = await store.save_memory(
        user_id="u",
        relationship_id="r",
        candidate=_event(source.id),
        source_message_id=source.id,
        status=MemoryStatus.CONFIRMED,
    )
    enrichment = GenericEventEnrichment(
        target_memory_id=target.item.id,
        antecedent_message_id=source.id,
        evidence_span="因为我迟到了",
        field=EventEnrichmentField.CAUSE,
        value={"category": "lateness"},
        reason="source-linked event completion",
    )
    cause_source = await store.add_message(
        user_id="u",
        relationship_id="r",
        role=MessageRole.USER,
        content="因为我迟到了",
        message_id="cause-source",
    )
    batch = MemoryWriteBatch(
        source_message_id=cause_source.id,
        event_enrichments=[enrichment],
    )
    first = await store.commit_memory_batch(user_id="u", relationship_id="r", batch=batch)
    second = await store.commit_memory_batch(user_id="u", relationship_id="r", batch=batch)
    loaded = await store.get_memory(target.item.id, "u")
    assert loaded is not None
    assert loaded.payload["cause"] == {"category": "lateness"}
    assert any(audit.rule_name == "enrich_event_cause" for audit in first.audits)
    assert first.updated_memory_ids == [target.item.id]
    assert second.updated_memory_ids == [target.item.id]
    assert len(loaded.payload["enrichment_history"]) == 1
    close = getattr(store, "aclose", None)
    if callable(close):
        await close()


def test_generic_enrichment_rejects_arbitrary_field() -> None:
    with pytest.raises(ValueError):
        GenericEventEnrichment(
            target_memory_id="m",
            antecedent_message_id="s",
            evidence_span="证据",
            field="not_a_real_field",
            value="x",
            reason="test",
        )

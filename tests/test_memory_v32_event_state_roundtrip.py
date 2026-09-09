from datetime import UTC, datetime
from pathlib import Path

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore, SQLiteMemoryStore
from loveapp.domain.advice import RelationshipContext
from loveapp.domain.memory import (
    MemoryCandidate,
    MemoryKind,
    MemoryStatus,
    RelationshipImpact,
    TimeKind,
)
from loveapp.domain.memory_dimensions import normalize_interaction_event_payload


def _event() -> MemoryCandidate:
    payload = normalize_interaction_event_payload(
        {
            "event_type": "date",
            "action": "正式约会",
            "event_markers": ["first_occurrence"],
            "source_event_ids": ["event-support-1"],
        },
        evidence_text="昨天我们第一次正式约会",
    )
    return MemoryCandidate(
        kind=MemoryKind.INTERACTION_EVENT,
        subject="relationship",
        summary="双方第一次正式约会",
        original_text="昨天我们第一次正式约会",
        evidence_spans=["第一次正式约会"],
        time_kind=TimeKind.POINT,
        occurred_at=datetime(2026, 9, 9, tzinfo=UTC),
        relationship_impact=RelationshipImpact.IMPROVING,
        source_event_ids=["event-support-1"],
        event_markers=["first_occurrence"],
        payload=payload,
    )


def _state() -> MemoryCandidate:
    return MemoryCandidate(
        kind=MemoryKind.RELATIONSHIP_STATE,
        subject="relationship",
        summary="当前仍在冷战",
        original_text="我们现在还在冷战",
        evidence_spans=["现在还在冷战"],
        state_dimension="relationship.conflict_status",
        state_value="active",
        source_event_ids=["event-support-1"],
        supporting_event_ids=["event-support-1", "event-support-2"],
        payload={
            "state_dimension": "relationship.conflict_status",
            "state_value": "active",
            "source_event_ids": ["event-support-1"],
            "supporting_event_ids": ["event-support-1", "event-support-2"],
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
async def test_event_and_state_link_fields_round_trip(store_kind: str, tmp_path: Path) -> None:
    if store_kind == "sqlite":
        store = SQLiteMemoryStore(tmp_path / "links.db")
    else:
        store = InMemoryMemoryStore()
    await store.save_relationship_context(
        RelationshipContext(user_id="u-v32", relationship_id="r-v32")
    )
    event = await store.save_memory(
        user_id="u-v32",
        relationship_id="r-v32",
        candidate=_event(),
        source_message_id=None,
        status=MemoryStatus.CONFIRMED,
    )
    state = await store.save_memory(
        user_id="u-v32",
        relationship_id="r-v32",
        candidate=_state(),
        status=MemoryStatus.CONFIRMED,
    )

    if store_kind == "sqlite":
        await store.aclose()
        store = SQLiteMemoryStore(tmp_path / "links.db")
    rows = await store.list_memories(user_id="u-v32", relationship_id="r-v32")
    by_id = {item.id: item for item in rows}
    assert by_id[event.item.id].source_event_ids == ["event-support-1"]
    assert by_id[event.item.id].event_markers == ["first_occurrence"]
    assert by_id[state.item.id].source_event_ids == ["event-support-1"]
    assert by_id[state.item.id].supporting_event_ids == [
        "event-support-1",
        "event-support-2",
    ]
    assert by_id[state.item.id].payload["source_event_ids"] == ["event-support-1"]
    assert by_id[state.item.id].payload["supporting_event_ids"] == [
        "event-support-1",
        "event-support-2",
    ]
    close = getattr(store, "aclose", None)
    if callable(close):
        await close()

from datetime import UTC, datetime

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.application.memory import MemoryService, NoOpMemoryExtractor
from loveapp.domain.memory import MemoryCandidate, MemoryKind, MemoryStatus, TimeKind

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


def _state(summary: str, value: str) -> MemoryCandidate:
    predicate = "relationship.conflict_status"
    return MemoryCandidate(
        kind=MemoryKind.RELATIONSHIP_STATE,
        subject="relationship",
        summary=summary,
        original_text=summary,
        evidence_spans=[summary],
        time_kind=TimeKind.TIMELESS,
        importance=3,
        confidence=0.95,
        canonical_predicate=predicate,
        raw_predicate=predicate,
        state_dimension=predicate,
        state_value=value,
        payload={
            "predicate": predicate,
            "state_dimension": predicate,
            "state_value": value,
        },
    )


@pytest.mark.asyncio
async def test_history_mode_retrieves_superseded_without_leaking_into_current_mode() -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(store, NoOpMemoryExtractor(), clock=lambda: NOW)
    old = await store.save_memory(
        user_id="history-user",
        relationship_id="relationship",
        candidate=_state("以前我们有过冷战", "active"),
        status=MemoryStatus.CONFIRMED,
    )
    await store.set_memory_status(old.item.id, "history-user", MemoryStatus.SUPERSEDED)
    current = await store.save_memory(
        user_id="history-user",
        relationship_id="relationship",
        candidate=_state("后来冲突已经解决", "resolved"),
        status=MemoryStatus.CONFIRMED,
    )
    rejected = await store.save_memory(
        user_id="history-user",
        relationship_id="relationship",
        candidate=_state("未经采纳的冷战说法", "active"),
        status=MemoryStatus.REJECTED,
    )
    expired = await store.save_memory(
        user_id="history-user",
        relationship_id="relationship",
        candidate=_state("已过期的冷战说法", "active"),
        status=MemoryStatus.EXPIRED,
    )

    current_results = await service.retrieve_memories(
        "history-user",
        "relationship",
        query="现在关系怎么样？",
    )
    history_results = await service.retrieve_memories(
        "history-user",
        "relationship",
        query="以前有没有冷战？",
    )

    assert old.item.id not in {result.item.id for result in current_results}
    assert current.item.id in {result.item.id for result in current_results}
    assert old.item.id in {result.item.id for result in history_results}
    assert current.item.id in {result.item.id for result in history_results}
    assert rejected.item.id not in {result.item.id for result in history_results}
    assert expired.item.id not in {result.item.id for result in history_results}
    assert history_results[0].item.id == old.item.id

    forced_current = await service.retrieve_memories(
        "history-user",
        "relationship",
        query="以前有没有冷战？",
        mode="current",
    )
    assert old.item.id not in {result.item.id for result in forced_current}


@pytest.mark.asyncio
async def test_history_context_does_not_project_superseded_state_as_current_fact() -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(store, NoOpMemoryExtractor(), clock=lambda: NOW)
    old = await store.save_memory(
        user_id="history-context-user",
        relationship_id="relationship",
        candidate=_state("以前我们有过冷战", "active"),
        status=MemoryStatus.CONFIRMED,
    )
    await store.set_memory_status(
        old.item.id,
        "history-context-user",
        MemoryStatus.SUPERSEDED,
    )
    await store.save_memory(
        user_id="history-context-user",
        relationship_id="relationship",
        candidate=_state("后来冲突已经解决", "resolved"),
        status=MemoryStatus.CONFIRMED,
    )

    context = await service.get_context(
        "history-context-user",
        "relationship",
        query="以前有没有冷战？",
    )

    assert old.item.id in {item.id for item in context.remembered_items}
    assert old.item.id not in {item.id for item in context.current_state}
    assert old.item.id not in {item.id for item in context.confirmed_current_state}

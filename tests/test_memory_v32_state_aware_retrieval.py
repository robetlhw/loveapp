"""Regression coverage for bounded State-aware linked Event retrieval."""

from datetime import UTC, datetime

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.application.memory import MemoryService, NoOpMemoryExtractor
from loveapp.application.memory_retrieval import (
    MemoryRetrievalScore,
    RetrievedMemory,
    expand_linked_memories,
)
from loveapp.domain.advice import RelationshipContext
from loveapp.domain.memory import MemoryCandidate, MemoryKind, MemoryStatus, TimeKind

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)


def _candidate(
    *,
    kind: MemoryKind,
    summary: str,
    canonical: str,
    state_value: str | None = None,
    payload: dict[str, object] | None = None,
) -> MemoryCandidate:
    values = dict(payload or {})
    if state_value is not None:
        values.update({"state_dimension": canonical, "state_value": state_value})
    return MemoryCandidate(
        kind=kind,
        subject="relationship",
        summary=summary,
        original_text=summary,
        evidence_spans=[summary],
        time_kind=TimeKind.POINT if kind == MemoryKind.INTERACTION_EVENT else TimeKind.TIMELESS,
        occurred_at=NOW if kind == MemoryKind.INTERACTION_EVENT else None,
        canonical_predicate=canonical,
        raw_predicate=canonical,
        state_dimension=canonical if state_value is not None else None,
        state_value=state_value,
        confidence=0.95,
        payload=values,
    )


async def _service(*, context_limit: int = 20, token_budget: int = 4096):
    store = InMemoryMemoryStore(clock=lambda: NOW)
    await store.save_relationship_context(RelationshipContext(user_id="u", relationship_id="r"))
    service = MemoryService(
        store,
        NoOpMemoryExtractor(),
        clock=lambda: NOW,
        context_limit=context_limit,
        context_token_budget=token_budget,
    )
    return store, service


@pytest.mark.asyncio
async def test_advice_query_expands_only_active_linked_event() -> None:
    store, service = await _service()
    event = await store.save_memory(
        user_id="u",
        relationship_id="r",
        candidate=_candidate(
            kind=MemoryKind.INTERACTION_EVENT,
            summary="昨天因为迟到吵架了",
            canonical="conflict_event",
            payload={"event_type": "conflict", "cause": "迟到"},
        ),
        status=MemoryStatus.CONFIRMED,
    )
    unrelated_event = await store.save_memory(
        user_id="u",
        relationship_id="r",
        candidate=_candidate(
            kind=MemoryKind.INTERACTION_EVENT,
            summary="上周一起看了电影",
            canonical="shared_activity_event",
            payload={"event_type": "shared_activity"},
        ),
        status=MemoryStatus.CONFIRMED,
    )
    state = _candidate(
        kind=MemoryKind.RELATIONSHIP_STATE,
        summary="我们现在还在冷战",
        canonical="relationship.conflict_status",
        state_value="active",
        payload={"memory_role": "current_state"},
    ).model_copy(update={"source_event_ids": [event.item.id]})
    await store.save_memory(
        user_id="u", relationship_id="r", candidate=state, status=MemoryStatus.CONFIRMED
    )

    results = await service.retrieve_memories("u", "r", query="那我现在应该怎么办？", limit=4)

    ids = {result.item.id for result in results}
    assert event.item.id in ids
    assert unrelated_event.item.id not in ids
    assert any(result.item.kind == MemoryKind.RELATIONSHIP_STATE for result in results)

    context = await service.get_context("u", "r", query="我现在应该怎么做？")
    context_ids = {item.id for item in context.remembered_items}
    assert event.item.id in context_ids
    assert unrelated_event.item.id not in context_ids
    assert any(item.id == event.item.id for item in context.recent_events)


@pytest.mark.asyncio
async def test_non_advice_query_does_not_dump_linked_event() -> None:
    store, _service_instance = await _service()
    event = await store.save_memory(
        user_id="u",
        relationship_id="r",
        candidate=_candidate(
            kind=MemoryKind.INTERACTION_EVENT,
            summary="昨天因为迟到吵架了",
            canonical="conflict_event",
            payload={"event_type": "conflict"},
        ),
        status=MemoryStatus.CONFIRMED,
    )
    state = _candidate(
        kind=MemoryKind.RELATIONSHIP_STATE,
        summary="我们现在还在冷战",
        canonical="relationship.conflict_status",
        state_value="active",
    ).model_copy(update={"source_event_ids": [event.item.id]})
    state_result = await store.save_memory(
        user_id="u", relationship_id="r", candidate=state, status=MemoryStatus.CONFIRMED
    )

    state_item = state_result.item
    retrieved_state = RetrievedMemory(
        item=state_item,
        score=MemoryRetrievalScore(0, 0, 1, 1, 1, 1, 1),
        retrieval_text=state_item.summary,
    )
    results = expand_linked_memories(
        [retrieved_state],
        [state_item, event.item],
        query="当前关系状态",
        reference_time=NOW,
    )

    assert event.item.id not in {result.item.id for result in results}


@pytest.mark.asyncio
async def test_linked_retrieval_respects_result_limit_and_token_budget() -> None:
    store, service = await _service(context_limit=2, token_budget=80)
    event = await store.save_memory(
        user_id="u",
        relationship_id="r",
        candidate=_candidate(
            kind=MemoryKind.INTERACTION_EVENT,
            summary="昨天因为迟到吵架了",
            canonical="conflict_event",
            payload={"event_type": "conflict"},
        ),
        status=MemoryStatus.CONFIRMED,
    )
    state = _candidate(
        kind=MemoryKind.RELATIONSHIP_STATE,
        summary="我们现在还在冷战",
        canonical="relationship.conflict_status",
        state_value="active",
    ).model_copy(update={"source_event_ids": [event.item.id]})
    await store.save_memory(
        user_id="u", relationship_id="r", candidate=state, status=MemoryStatus.CONFIRMED
    )

    results = await service.retrieve_memories("u", "r", query="我现在应该怎么做？", limit=2)

    assert len(results) <= 2
    assert sum(max(1, (len(result.retrieval_text) + 2) // 3) for result in results) <= 80


@pytest.mark.asyncio
async def test_superseded_linked_event_is_not_expanded() -> None:
    store, service = await _service()
    event = await store.save_memory(
        user_id="u",
        relationship_id="r",
        candidate=_candidate(
            kind=MemoryKind.INTERACTION_EVENT,
            summary="历史冲突事件",
            canonical="conflict_event",
            payload={"event_type": "conflict"},
        ),
        status=MemoryStatus.SUPERSEDED,
    )
    state = _candidate(
        kind=MemoryKind.RELATIONSHIP_STATE,
        summary="现在冲突已经解决",
        canonical="relationship.conflict_status",
        state_value="resolved",
    ).model_copy(update={"source_event_ids": [event.item.id]})
    await store.save_memory(
        user_id="u", relationship_id="r", candidate=state, status=MemoryStatus.CONFIRMED
    )

    results = await service.retrieve_memories("u", "r", query="我现在应该怎么办？", limit=4)

    assert event.item.id not in {result.item.id for result in results}

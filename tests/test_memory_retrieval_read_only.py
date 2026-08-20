from datetime import UTC, datetime, timedelta

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore, SQLiteMemoryStore
from loveapp.application.memory import MemoryService, NoOpMemoryExtractor
from loveapp.application.memory_retrieval import HybridMemoryRetriever
from loveapp.domain.memory import MemoryCandidate, MemoryKind, MemoryStatus, TimeKind

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


def _candidate(
    summary: str,
    *,
    predicate: str = "relationship.conflict_status",
    state_value: str | None = None,
    occurred_at: datetime | None = None,
) -> MemoryCandidate:
    payload: dict[str, object] = {"predicate": predicate}
    if state_value is not None:
        payload.update({"state_dimension": predicate, "state_value": state_value})
    return MemoryCandidate(
        kind=(
            MemoryKind.RELATIONSHIP_STATE
            if state_value is not None
            else MemoryKind.INTERACTION_EVENT
        ),
        subject="relationship",
        summary=summary,
        original_text=summary,
        evidence_spans=[summary],
        time_kind=TimeKind.POINT if occurred_at is not None else TimeKind.TIMELESS,
        occurred_at=occurred_at,
        importance=3,
        confidence=0.95,
        canonical_predicate=predicate,
        raw_predicate=predicate,
        state_dimension=predicate if state_value is not None else None,
        state_value=state_value,
        payload=payload,
    )


async def _service() -> tuple[InMemoryMemoryStore, MemoryService]:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(store, NoOpMemoryExtractor(), clock=lambda: NOW)
    return store, service


@pytest.mark.asyncio
async def test_retrieve_memories_is_read_only() -> None:
    store, service = await _service()
    first = await store.save_memory(
        user_id="read-only-user",
        relationship_id="relationship",
        candidate=_candidate("旧冲突", state_value="active"),
        status=MemoryStatus.CONFIRMED,
    )
    second = await store.save_memory(
        user_id="read-only-user",
        relationship_id="relationship",
        candidate=_candidate("当前冲突已解决", state_value="resolved"),
        status=MemoryStatus.CONFIRMED,
    )
    before = await store.list_memories(
        user_id="read-only-user",
        relationship_id="relationship",
        read_only=True,
    )

    await service.retrieve_memories(
        "read-only-user",
        "relationship",
        query="现在关系怎么样？",
    )

    after = await store.list_memories(
        user_id="read-only-user",
        relationship_id="relationship",
        read_only=True,
    )
    assert len(after) == len(before) == 2
    assert {item.id: (item.status, item.updated_at) for item in after} == {
        first.item.id: (MemoryStatus.CONFIRMED, first.item.updated_at),
        second.item.id: (MemoryStatus.CONFIRMED, second.item.updated_at),
    }


@pytest.mark.asyncio
async def test_get_context_is_read_only_for_duplicate_active_states() -> None:
    store, service = await _service()
    await store.save_memory(
        user_id="context-user",
        relationship_id="relationship",
        candidate=_candidate(
            "联系变少",
            predicate="interaction.contact_frequency",
            state_value="low",
        ),
        status=MemoryStatus.CONFIRMED,
    )
    await store.save_memory(
        user_id="context-user",
        relationship_id="relationship",
        candidate=_candidate(
            "联系恢复正常",
            predicate="interaction.contact_frequency",
            state_value="normal",
        ),
        status=MemoryStatus.CONFIRMED,
    )
    before = await store.list_memories(
        user_id="context-user",
        relationship_id="relationship",
        read_only=True,
    )

    context = await service.get_context(
        "context-user",
        "relationship",
        query="联系怎么样？",
    )

    after = await store.list_memories(
        user_id="context-user",
        relationship_id="relationship",
        read_only=True,
    )
    assert context.remembered_items
    assert {item.id: item.status for item in after} == {
        item.id: item.status for item in before
    }


@pytest.mark.asyncio
async def test_huge_first_memory_never_exceeds_token_budget() -> None:
    store, _ = await _service()
    huge_text = "最近发生了关系事件 " + ("很长的证据 " * 30)
    saved = await store.save_memory(
        user_id="budget-user",
        relationship_id="relationship",
        candidate=_candidate(huge_text, predicate="event.huge"),
        status=MemoryStatus.CONFIRMED,
    )
    retriever = HybridMemoryRetriever(token_budget=10)

    results = await retriever.retrieve(
        [saved.item],
        query="最近发生什么？",
        reference_time=NOW,
    )

    assert results == []


@pytest.mark.asyncio
async def test_small_memories_fit_and_huge_memory_is_skipped() -> None:
    store, _ = await _service()
    small = []
    for index in range(2):
        result = await store.save_memory(
            user_id="budget-user",
            relationship_id="relationship",
            candidate=_candidate(
                f"最近发生了小事件 {index}",
                predicate=f"event.small_{index}",
                occurred_at=NOW - timedelta(minutes=index),
            ),
            status=MemoryStatus.CONFIRMED,
        )
        small.append(result.item)
    huge_text = "最近发生了关系事件 " + ("很长的证据 " * 30)
    huge = await store.save_memory(
        user_id="budget-user",
        relationship_id="relationship",
        candidate=_candidate(huge_text, predicate="event.huge"),
        status=MemoryStatus.CONFIRMED,
    )
    retriever = HybridMemoryRetriever(token_budget=80)

    results = await retriever.retrieve(
        [*small, huge.item],
        query="最近发生什么？",
        reference_time=NOW,
    )
    used_tokens = sum(max(1, (len(item.retrieval_text) + 2) // 3) for item in results)

    assert used_tokens <= 80
    assert {item.id for item in small} <= {result.item.id for result in results}
    assert huge.item.id not in {result.item.id for result in results}


@pytest.mark.asyncio
async def test_equal_score_ranking_is_stable() -> None:
    store, _ = await _service()
    memories = []
    for predicate in ("event.alpha", "event.beta", "event.gamma"):
        result = await store.save_memory(
            user_id="ranking-user",
            relationship_id="relationship",
            candidate=_candidate(
                "同一时间发生的关系事件",
                predicate=predicate,
                occurred_at=NOW,
            ),
            status=MemoryStatus.CONFIRMED,
        )
        memories.append(result.item)
    retriever = HybridMemoryRetriever()

    first = await retriever.retrieve(memories, query="同一时间发生")
    second = await retriever.retrieve(memories, query="同一时间发生")

    assert [result.item.id for result in first] == [result.item.id for result in second]
    assert [result.item.payload["predicate"] for result in first] == [
        "event.alpha",
        "event.beta",
        "event.gamma",
    ]


@pytest.mark.asyncio
async def test_equal_score_ranking_matches_in_memory_and_sqlite(tmp_path) -> None:
    memory_store = InMemoryMemoryStore(clock=lambda: NOW)
    sqlite_store = SQLiteMemoryStore(tmp_path / "ranking.db", clock=lambda: NOW)
    services = (
        MemoryService(memory_store, NoOpMemoryExtractor(), clock=lambda: NOW),
        MemoryService(sqlite_store, NoOpMemoryExtractor(), clock=lambda: NOW),
    )
    try:
        for store in (memory_store, sqlite_store):
            for predicate in ("event.alpha", "event.beta", "event.gamma"):
                await store.save_memory(
                    user_id="ranking-user",
                    relationship_id="relationship",
                    candidate=_candidate(
                        "同一时间发生的关系事件",
                        predicate=predicate,
                        occurred_at=NOW,
                    ),
                    status=MemoryStatus.CONFIRMED,
                )
        rankings = []
        for service in services:
            results = await service.retrieve_memories(
                "ranking-user",
                "relationship",
                query="同一时间发生",
            )
            rankings.append([result.item.payload["predicate"] for result in results])
    finally:
        await sqlite_store.aclose()

    assert rankings == [
        ["event.alpha", "event.beta", "event.gamma"],
        ["event.alpha", "event.beta", "event.gamma"],
    ]


@pytest.mark.asyncio
async def test_sqlite_retrieval_filters_expired_memory_without_writing_status_or_audit(
    tmp_path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "read-only.db", clock=lambda: NOW)
    service = MemoryService(store, NoOpMemoryExtractor(), clock=lambda: NOW)
    candidate = _candidate("已过期的关系事件", predicate="event.expired")
    candidate = candidate.model_copy(update={"expires_at": NOW - timedelta(seconds=1)})
    saved = await store.save_memory(
        user_id="sqlite-read-user",
        relationship_id="relationship",
        candidate=candidate,
        status=MemoryStatus.CONFIRMED,
    )
    before_audits = await store.list_transition_audits(
        user_id="sqlite-read-user",
        relationship_id="relationship",
    )

    try:
        results = await service.retrieve_memories(
            "sqlite-read-user",
            "relationship",
            query="以前发生了什么？",
        )
        context = await service.get_context(
            "sqlite-read-user",
            "relationship",
            query="以前发生了什么？",
        )
        after = await store.get_memory(saved.item.id, "sqlite-read-user")
        after_audits = await store.list_transition_audits(
            user_id="sqlite-read-user",
            relationship_id="relationship",
        )
    finally:
        await store.aclose()

    assert results == []
    assert context.remembered_items == []
    assert after is not None and after.status == MemoryStatus.CONFIRMED
    assert after.updated_at == saved.item.updated_at
    assert after.last_used_at is None
    assert after_audits == before_audits


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["in_memory", "sqlite"])
async def test_get_context_does_not_create_missing_relationship_scope(
    backend: str,
    tmp_path,
) -> None:
    store = (
        InMemoryMemoryStore(clock=lambda: NOW)
        if backend == "in_memory"
        else SQLiteMemoryStore(tmp_path / "missing-context.db", clock=lambda: NOW)
    )
    service = MemoryService(store, NoOpMemoryExtractor(), clock=lambda: NOW)

    try:
        context = await service.get_context(
            "missing-user",
            "missing-relationship",
            query="我们现在关系怎么样？",
        )
        persisted = await store.get_relationship_context(
            "missing-user",
            "missing-relationship",
            read_only=True,
        )
    finally:
        await store.aclose()

    assert context.user_id == "missing-user"
    assert context.relationship_id == "missing-relationship"
    assert persisted is None

from datetime import UTC, datetime, timedelta

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore, SQLiteMemoryStore
from loveapp.application.memory import MemoryService, NoOpMemoryExtractor
from loveapp.application.memory_retrieval import HybridMemoryRetriever
from loveapp.domain.memory import MemoryCandidate, MemoryKind, MemoryStatus, TimeKind

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
SCOPE = {
    "user_id": "retrieval-user",
    "relationship_id": "retrieval-relationship",
    "conversation_id": "retrieval-conversation",
}


class FakeEmbeddingProvider:
    model_name = "fake-memory-embedding"
    is_ready = True

    def start_warmup(self):
        raise AssertionError("warmup is not needed for the fake provider")

    async def warmup(self) -> None:
        return None

    async def dimension(self) -> int:
        return 2

    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0] if "contact" in text else [0.0, 1.0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "contact" in text else [0.0, 1.0] for text in texts]

    async def aclose(self) -> None:
        return None


async def _service(*, embedding_provider=None, context_limit: int = 20):
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        NoOpMemoryExtractor(),
        clock=lambda: NOW,
        context_limit=context_limit,
        embedding_provider=embedding_provider,
    )
    await service.ensure_context(SCOPE["user_id"], SCOPE["relationship_id"])
    return store, service


async def _save(
    store: InMemoryMemoryStore,
    *,
    kind: MemoryKind,
    summary: str,
    canonical: str | None = None,
    subject: str = "relationship",
    status: MemoryStatus = MemoryStatus.CONFIRMED,
    state_value: str | None = None,
    occurred_at: datetime | None = None,
    importance: int = 3,
    payload: dict[str, object] | None = None,
    source_message_id: str | None = None,
    expires_at: datetime | None = None,
):
    data = dict(payload or {})
    if canonical is not None:
        data.setdefault("predicate", canonical)
    if state_value is not None and canonical is not None:
        data.update({"state_dimension": canonical, "state_value": state_value})
    candidate = MemoryCandidate(
        kind=kind,
        subject=subject,
        summary=summary,
        original_text=summary,
        evidence_spans=[summary],
        time_kind=TimeKind.POINT if occurred_at is not None else TimeKind.TIMELESS,
        occurred_at=occurred_at,
        expires_at=expires_at,
        importance=importance,
        confidence=0.95,
        canonical_predicate=canonical,
        raw_predicate=canonical,
        state_dimension=canonical if state_value is not None else None,
        state_value=state_value,
        payload=data,
    )
    return await store.save_memory(
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
        candidate=candidate,
        source_message_id=source_message_id,
        status=status,
    )


@pytest.mark.asyncio
async def test_current_state_retrieval_excludes_superseded_state() -> None:
    store, service = await _service()
    old = await _save(
        store,
        kind=MemoryKind.RELATIONSHIP_STATE,
        summary="我们现在还在冷战",
        canonical="relationship.conflict_status",
        state_value="active",
        status=MemoryStatus.SUPERSEDED,
    )
    current = await _save(
        store,
        kind=MemoryKind.RELATIONSHIP_STATE,
        summary="我们的冲突已经解决，现在关系正常",
        canonical="relationship.conflict_status",
        state_value="resolved",
    )

    context = await service.get_context(
        SCOPE["user_id"],
        SCOPE["relationship_id"],
        query="我们现在关系怎么样",
    )

    ids = {item.id for item in context.remembered_items}
    assert old.item.id not in ids
    assert current.item.id in ids
    assert [item.id for item in context.confirmed_current_state] == [current.item.id]


@pytest.mark.asyncio
async def test_preference_query_prioritizes_confirmed_preference_over_uncertain_value() -> None:
    store, service = await _service()
    confirmed = await _save(
        store,
        kind=MemoryKind.PREFERENCE,
        subject="partner",
        summary="她喜欢吃日料",
        canonical="preference.food.cuisine",
        payload={"preference": "日料", "preference_type": "cuisine"},
    )
    proposed = await _save(
        store,
        kind=MemoryKind.PREFERENCE,
        subject="partner",
        summary="她可能不喜欢日料",
        canonical="preference.food.cuisine",
        status=MemoryStatus.PROPOSED,
        payload={"preference": "日料", "preference_type": "dislike"},
    )

    context = await service.get_context(
        SCOPE["user_id"],
        SCOPE["relationship_id"],
        query="她喜欢吃什么",
    )

    assert confirmed.item.id in {item.id for item in context.remembered_items}
    assert context.partner_preferences == ["日料"]
    assert proposed.item.id in {item.id for item in context.uncertain_items}


@pytest.mark.asyncio
async def test_recent_event_outranks_old_event_for_recent_query() -> None:
    store, service = await _service()
    old = await _save(
        store,
        kind=MemoryKind.INTERACTION_EVENT,
        summary="三个月前我们一起去旅行",
        canonical="travel_event",
        occurred_at=NOW - timedelta(days=90),
        importance=2,
        payload={"event_id": "trip-old"},
    )
    recent = await _save(
        store,
        kind=MemoryKind.INTERACTION_EVENT,
        summary="昨天我们因为钱的问题吵架",
        canonical="argument_event",
        occurred_at=NOW - timedelta(days=1),
        importance=3,
        payload={"event_id": "argument-recent"},
    )

    retrieved = await service.retrieve_memories(
        SCOPE["user_id"],
        SCOPE["relationship_id"],
        query="最近发生什么",
    )

    assert retrieved[0].item.id == recent.item.id
    assert old.item.id in {result.item.id for result in retrieved}


@pytest.mark.asyncio
async def test_semantic_fallback_matches_contact_predicate_without_exact_words() -> None:
    store, service = await _service()
    contact = await _save(
        store,
        kind=MemoryKind.INTERACTION_PATTERN,
        summary="她最近回复消息越来越少",
        canonical="interaction.contact_frequency",
        state_value="low",
        payload={"metric": "contact_frequency", "current": "low"},
    )

    retrieved = await service.retrieve_memories(
        SCOPE["user_id"],
        SCOPE["relationship_id"],
        query="她是不是没有以前主动了",
    )

    assert retrieved[0].item.id == contact.item.id
    assert retrieved[0].score.predicate_match > 0


@pytest.mark.asyncio
async def test_predicate_match_beats_unrelated_preference_noise() -> None:
    store, service = await _service()
    preference = await _save(
        store,
        kind=MemoryKind.PREFERENCE,
        subject="partner",
        summary="她喜欢吃日料",
        canonical="preference.food.cuisine",
        payload={"preference": "日料", "preference_type": "cuisine"},
    )
    contact = await _save(
        store,
        kind=MemoryKind.INTERACTION_PATTERN,
        summary="她最近回复消息越来越少，互动变冷淡",
        canonical="interaction.contact_frequency",
        state_value="low",
        payload={"metric": "contact_frequency", "current": "low"},
    )

    retrieved = await service.retrieve_memories(
        SCOPE["user_id"],
        SCOPE["relationship_id"],
        query="她最近为什么冷淡",
    )

    assert retrieved[0].item.id == contact.item.id
    assert preference.item.id not in {result.item.id for result in retrieved[:1]}


@pytest.mark.asyncio
async def test_expired_and_superseded_memories_never_enter_default_context() -> None:
    store, service = await _service()
    superseded = await _save(
        store,
        kind=MemoryKind.RELATIONSHIP_STATE,
        summary="旧的冲突状态",
        canonical="relationship.conflict_status",
        state_value="active",
        status=MemoryStatus.SUPERSEDED,
    )
    expired = await _save(
        store,
        kind=MemoryKind.STABLE_FACT,
        summary="过期的画像信息",
        canonical="profile.fact",
        expires_at=NOW - timedelta(minutes=1),
    )

    context = await service.get_context(
        SCOPE["user_id"],
        SCOPE["relationship_id"],
        query="关系和画像",
    )

    ids = {item.id for item in context.remembered_items}
    assert superseded.item.id not in ids
    assert expired.item.id not in ids


@pytest.mark.asyncio
async def test_multi_topic_query_assembles_preference_and_current_state() -> None:
    store, service = await _service()
    preference = await _save(
        store,
        kind=MemoryKind.PREFERENCE,
        subject="partner",
        summary="她喜欢安静的餐厅",
        canonical="preference.environment.noise",
        payload={"preference": "安静", "preference_type": "noise"},
    )
    state = await _save(
        store,
        kind=MemoryKind.RELATIONSHIP_STATE,
        summary="她最近因为工作压力不开心",
        canonical="relationship.conflict_status",
        state_value="active",
    )

    context = await service.get_context(
        SCOPE["user_id"],
        SCOPE["relationship_id"],
        query="她喜欢什么，以及最近为什么不开心",
    )

    ids = {item.id for item in context.remembered_items}
    assert {preference.item.id, state.item.id} <= ids


@pytest.mark.asyncio
async def test_unrelated_query_returns_no_personal_memory() -> None:
    store, service = await _service()
    await _save(
        store,
        kind=MemoryKind.PREFERENCE,
        subject="partner",
        summary="她喜欢吃日料",
        canonical="preference.food.cuisine",
        payload={"preference": "日料", "preference_type": "cuisine"},
    )
    await _save(
        store,
        kind=MemoryKind.INTERACTION_EVENT,
        summary="昨天我们一起看了电影",
        canonical="movie_event",
        occurred_at=NOW - timedelta(days=1),
        payload={"event_id": "movie-weather-negative"},
    )

    context = await service.get_context(
        SCOPE["user_id"],
        SCOPE["relationship_id"],
        query="今天上海天气怎么样",
    )

    assert context.remembered_items == []


@pytest.mark.asyncio
async def test_retrieval_limit_and_token_budget_are_bounded() -> None:
    store, service = await _service(context_limit=4)
    for index in range(20):
        await _save(
            store,
            kind=MemoryKind.INTERACTION_EVENT,
            summary=f"最近发生了第{index}个关系事件，内容很长用于预算测试",
            canonical=f"event_{index}",
            occurred_at=NOW - timedelta(days=index),
            payload={"event_id": f"event-{index}"},
        )

    retrieved = await service.retrieve_memories(
        SCOPE["user_id"],
        SCOPE["relationship_id"],
        query="最近发生什么",
        limit=4,
    )

    assert len(retrieved) <= 4
    assert len({result.item.id for result in retrieved}) == len(retrieved)


@pytest.mark.asyncio
async def test_token_budget_is_enforced_by_the_retriever() -> None:
    store, _ = await _service()
    for index in range(8):
        await _save(
            store,
            kind=MemoryKind.INTERACTION_EVENT,
            summary=f"事件 {index} 最近发生，包含一段用于预算计算的较长证据文本",
            canonical=f"budget_event_{index}",
            occurred_at=NOW - timedelta(days=index),
            payload={"event_id": f"budget-{index}"},
        )
    memories = await store.list_memories(
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
    )
    retriever = HybridMemoryRetriever(token_budget=70)

    retrieved = await retriever.retrieve(
        memories,
        query="最近发生什么",
        limit=8,
        reference_time=NOW,
    )

    estimated_tokens = sum(max(1, (len(item.retrieval_text) + 2) // 3) for item in retrieved)
    assert estimated_tokens <= 70


@pytest.mark.asyncio
async def test_optional_embedding_provider_contributes_semantic_score() -> None:
    store, service = await _service(embedding_provider=FakeEmbeddingProvider())
    contact = await _save(
        store,
        kind=MemoryKind.INTERACTION_PATTERN,
        summary="contact pattern",
        canonical="interaction.contact_frequency",
        state_value="low",
        payload={"metric": "contact_frequency", "current": "low"},
    )
    preference = await _save(
        store,
        kind=MemoryKind.PREFERENCE,
        subject="partner",
        summary="favorite food",
        canonical="preference.food.cuisine",
        payload={"preference": "日料", "preference_type": "cuisine"},
    )

    retrieved = await service.retrieve_memories(
        SCOPE["user_id"],
        SCOPE["relationship_id"],
        query="contact",
    )

    assert retrieved[0].item.id == contact.item.id
    assert retrieved[0].score.semantic_similarity == pytest.approx(1.0)
    assert preference.item.id not in {result.item.id for result in retrieved[:1]}


@pytest.mark.asyncio
async def test_sqlite_and_in_memory_read_paths_have_the_same_current_state_filter(
    tmp_path,
) -> None:
    memory_store, memory_service = await _service()
    sqlite_store = SQLiteMemoryStore(tmp_path / "retrieval.db", clock=lambda: NOW)
    sqlite_service = MemoryService(
        sqlite_store,
        NoOpMemoryExtractor(),
        clock=lambda: NOW,
    )
    for store in (memory_store, sqlite_store):
        await _save(
            store,
            kind=MemoryKind.RELATIONSHIP_STATE,
            summary="旧的冲突状态",
            canonical="relationship.conflict_status",
            state_value="active",
            status=MemoryStatus.SUPERSEDED,
        )
        await _save(
            store,
            kind=MemoryKind.RELATIONSHIP_STATE,
            summary="冲突已经解决",
            canonical="relationship.conflict_status",
            state_value="resolved",
        )

    try:
        memory_context = await memory_service.get_context(
            SCOPE["user_id"],
            SCOPE["relationship_id"],
            query="我们现在关系怎么样",
        )
        sqlite_context = await sqlite_service.get_context(
            SCOPE["user_id"],
            SCOPE["relationship_id"],
            query="我们现在关系怎么样",
        )
    finally:
        await sqlite_store.aclose()

    assert [item.state_value for item in memory_context.remembered_items] == ["resolved"]
    assert [item.state_value for item in sqlite_context.remembered_items] == ["resolved"]


@pytest.mark.asyncio
async def test_retriever_excludes_proposed_from_confirmed_fact_projection() -> None:
    store, service = await _service()
    confirmed = await _save(
        store,
        kind=MemoryKind.PREFERENCE,
        subject="partner",
        summary="她喜欢日料",
        canonical="preference.food.cuisine",
        payload={"preference": "日料", "preference_type": "cuisine"},
    )
    proposed = await _save(
        store,
        kind=MemoryKind.PREFERENCE,
        subject="partner",
        summary="她可能讨厌日料",
        canonical="preference.food.cuisine",
        status=MemoryStatus.PROPOSED,
        payload={"preference": "日料", "preference_type": "dislike"},
    )

    context = await service.get_context(
        SCOPE["user_id"],
        SCOPE["relationship_id"],
        query="她喜欢什么",
    )

    assert confirmed.item.id in {item.id for item in context.confirmed_long_term}
    assert proposed.item.id not in {item.id for item in context.confirmed_long_term}

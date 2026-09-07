import asyncio

from qdrant_client import AsyncQdrantClient

from loveapp.adapters.knowledge.qdrant import QdrantKnowledgeStore
from loveapp.bootstrap import load_seed_documents
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.enums import AdviceGoal, AdviceScenario, RelationshipStage
from loveapp.domain.knowledge import KnowledgeDocument, KnowledgeFilters


class FakeEmbeddingProvider:
    model_name = "fake"

    def __init__(self) -> None:
        self.query_calls = 0

    @property
    def is_ready(self) -> bool:
        return True

    def start_warmup(self):
        return asyncio.create_task(self.warmup())

    async def warmup(self) -> None:
        return None

    async def dimension(self) -> int:
        return 3

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return self._embed(text)

    async def aclose(self) -> None:
        return None

    @staticmethod
    def _embed(text: str) -> list[float]:
        return [
            float("吵架" in text or "冲突" in text),
            float("聊天" in text or "回复" in text),
            float("约会" in text or "邀约" in text),
        ]


async def test_qdrant_indexes_and_filters_documents() -> None:
    client = AsyncQdrantClient(location=":memory:")
    store = QdrantKnowledgeStore(client, "test_knowledge", FakeEmbeddingProvider())
    documents = load_seed_documents()

    indexed = await store.index_documents(documents, recreate=True)
    matches = await store.search(
        "和对象吵架了怎么办",
        filters=KnowledgeFilters(scenario=AdviceScenario.CONFLICT, hard=True),
        limit=3,
    )

    assert indexed == len(documents)
    assert await store.count() == len(documents)
    assert matches
    assert all(match.document.scenario == AdviceScenario.CONFLICT for match in matches)
    await store.aclose()


async def test_qdrant_accepts_multiple_scenario_filters() -> None:
    client = AsyncQdrantClient(location=":memory:")
    store = QdrantKnowledgeStore(client, "multi_scenario", FakeEmbeddingProvider())
    await store.index_documents(load_seed_documents(), recreate=True)

    matches = await store.search(
        "聊天回复",
        filters=KnowledgeFilters(
            scenarios=[AdviceScenario.PURSUIT, AdviceScenario.CHAT_ANALYSIS],
            hard=True,
        ),
        limit=6,
    )

    assert matches
    assert {match.document.scenario for match in matches} <= {
        AdviceScenario.PURSUIT,
        AdviceScenario.CHAT_ANALYSIS,
    }
    await store.aclose()


async def test_soft_scenario_preference_does_not_exclude_semantic_match() -> None:
    client = AsyncQdrantClient(location=":memory:")
    embedding = FakeEmbeddingProvider()
    store = QdrantKnowledgeStore(client, "soft_preferences", embedding)
    await store.index_documents(load_seed_documents(), recreate=True)

    matches = await store.search(
        "和对象吵架了怎么办",
        filters=KnowledgeFilters(scenario=AdviceScenario.PURSUIT),
        limit=3,
    )

    assert matches[0].document.scenario == AdviceScenario.CONFLICT
    assert embedding.query_calls == 1
    assert matches[0].base_score is not None
    await store.aclose()


async def test_candidate_limit_is_configurable_and_recorded_in_trace() -> None:
    client = AsyncQdrantClient(location=":memory:")
    store = QdrantKnowledgeStore(
        client,
        "candidate_limit",
        FakeEmbeddingProvider(),
        candidate_limit=30,
    )
    await store.index_documents(load_seed_documents(), recreate=True)
    trace = ExecutionTrace()

    await store.search(
        "聊天回复",
        filters=KnowledgeFilters(scenario=AdviceScenario.CHAT_ANALYSIS, hard=True),
        limit=3,
        trace=trace,
    )

    vector_search = next(
        record for record in trace.snapshot() if record.name == "rag_vector_search"
    )
    assert vector_search.details["candidate_limit"] == 30
    assert vector_search.details["hard_filter"] is True
    await store.aclose()


async def test_detailed_search_keeps_nearest_candidates_below_threshold() -> None:
    client = AsyncQdrantClient(location=":memory:")
    store = QdrantKnowledgeStore(
        client,
        "threshold_diagnostics",
        FakeEmbeddingProvider(),
        min_score=1.1,
    )
    await store.index_documents(load_seed_documents(), recreate=True)

    result = await store.search_detailed("聊天回复", limit=3)

    assert result.returned == []
    assert result.candidates == []
    assert result.reranked_candidates == []
    assert result.nearest_candidates
    await store.aclose()


async def test_configured_hard_filter_is_applied_without_mutating_call_filters() -> None:
    client = AsyncQdrantClient(location=":memory:")
    store = QdrantKnowledgeStore(
        client,
        "configured_hard_filter",
        FakeEmbeddingProvider(),
        hard_filter=True,
    )
    await store.index_documents(load_seed_documents(), recreate=True)

    result = await store.search_detailed(
        "和对象吵架了怎么办",
        filters=KnowledgeFilters(scenario=AdviceScenario.CONFLICT),
        limit=3,
    )

    assert result.returned
    assert all(match.document.scenario == AdviceScenario.CONFLICT for match in result.returned)
    await store.aclose()


async def test_in_memory_hard_filter_matches_qdrant_empty_metadata_semantics() -> None:
    from loveapp.adapters.knowledge.in_memory import InMemoryKnowledgeRetriever

    document = KnowledgeDocument(
        id="empty-metadata",
        title="Empty metadata",
        scenario=AdviceScenario.CONFLICT,
        question="冲突问题",
        answer="答案",
    )
    retriever = InMemoryKnowledgeRetriever([document])

    result = await retriever.search_detailed(
        "冲突问题",
        filters=KnowledgeFilters(
            scenario=AdviceScenario.CONFLICT,
            goals=[AdviceGoal.REPAIR],
            relationship_stage=RelationshipStage.DATING,
            hard=True,
        ),
        limit=5,
    )

    assert result.returned == []

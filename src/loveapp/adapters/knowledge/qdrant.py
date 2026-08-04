import hashlib
import uuid
from collections.abc import Iterable
from contextlib import nullcontext

from qdrant_client import AsyncQdrantClient, models

from loveapp.domain.enums import RelationshipStage
from loveapp.domain.knowledge import KnowledgeDocument, KnowledgeFilters, RetrievedDocument
from loveapp.ports.embeddings import EmbeddingProvider
from loveapp.ports.observability import TraceRecorder

from .scoring import soft_rerank

_POINT_NAMESPACE = uuid.UUID("32b0377e-0d66-4784-9035-98790f5e1c87")


class QdrantKnowledgeStore:
    def __init__(
        self,
        client: AsyncQdrantClient,
        collection_name: str,
        embedding_provider: EmbeddingProvider,
        min_score: float | None = None,
    ) -> None:
        self._client = client
        self._collection_name = collection_name
        self._embedding_provider = embedding_provider
        self._min_score = min_score

    def start_warmup(self):
        return self._embedding_provider.start_warmup()

    async def warmup(self) -> None:
        await self._embedding_provider.warmup()

    async def ensure_collection(self, recreate: bool = False) -> None:
        exists = await self._client.collection_exists(self._collection_name)
        if recreate and exists:
            await self._client.delete_collection(self._collection_name)
            exists = False

        vector_size = await self._embedding_provider.dimension()
        if not exists:
            await self._client.create_collection(
                collection_name=self._collection_name,
                vectors_config=models.VectorParams(
                    size=vector_size,
                    distance=models.Distance.COSINE,
                ),
            )
            return

        collection = await self._client.get_collection(self._collection_name)
        vectors = collection.config.params.vectors
        existing_size = vectors.size if isinstance(vectors, models.VectorParams) else None
        if existing_size is not None and existing_size != vector_size:
            raise ValueError(
                "Qdrant collection 向量维度与当前 Embedding 模型不一致；"
                "请使用 --recreate 重新入库。"
            )

    async def index_documents(
        self,
        documents: list[KnowledgeDocument],
        recreate: bool = False,
    ) -> int:
        await self.ensure_collection(recreate=recreate)
        vectors = await self._embedding_provider.embed_documents(
            [document.retrieval_text for document in documents]
        )
        points = [
            models.PointStruct(
                id=_point_id(document.id),
                vector=vector,
                payload=_document_payload(document),
            )
            for document, vector in zip(documents, vectors, strict=True)
        ]
        for batch in _batched(points, 64):
            await self._client.upsert(
                collection_name=self._collection_name,
                points=batch,
                wait=True,
            )
        return len(points)

    async def search(
        self,
        query: str,
        filters: KnowledgeFilters | None = None,
        limit: int = 5,
        trace: TraceRecorder | None = None,
    ) -> list[RetrievedDocument]:
        was_ready = self._embedding_provider.is_ready
        measure = trace.measure("embedding_warmup_wait") if trace else nullcontext({})
        with measure as details:
            details["already_ready"] = was_ready
            details["model"] = self._embedding_provider.model_name
            await self._embedding_provider.warmup()

        measure = trace.measure("rag_query_embedding") if trace else nullcontext({})
        with measure as details:
            details["model"] = self._embedding_provider.model_name
            query_vector = await self._embedding_provider.embed_query(query)

        candidate_limit = limit if filters and filters.hard else max(limit, 15)
        measure = trace.measure("rag_vector_search") if trace else nullcontext({})
        with measure as details:
            details["candidate_limit"] = candidate_limit
            details["hard_filter"] = bool(filters and filters.hard)
            response = await self._client.query_points(
                collection_name=self._collection_name,
                query=query_vector,
                query_filter=_build_filter(filters),
                limit=candidate_limit,
                score_threshold=self._min_score,
                with_payload=True,
            )
            details["candidate_count"] = len(response.points)
        matches = [
            RetrievedDocument(
                document=KnowledgeDocument.model_validate(point.payload or {}),
                score=max(float(point.score), 0.0),
                base_score=max(float(point.score), 0.0),
            )
            for point in response.points
        ]
        measure = trace.measure("rag_soft_rerank") if trace else nullcontext({})
        with measure as details:
            reranked = soft_rerank(query, matches, filters)[:limit]
            details["returned_count"] = len(reranked)
            return reranked

    async def count(self) -> int:
        result = await self._client.count(
            collection_name=self._collection_name,
            exact=True,
        )
        return result.count

    async def aclose(self) -> None:
        await self._embedding_provider.aclose()
        await self._client.close()


def _build_filter(filters: KnowledgeFilters | None) -> models.Filter | None:
    if filters is None or not filters.hard:
        return None
    conditions: list[models.Condition] = []
    scenarios = list(
        dict.fromkeys([*filters.scenarios, *([filters.scenario] if filters.scenario else [])])
    )
    if scenarios:
        conditions.append(
            models.FieldCondition(
                key="scenario",
                match=_match_values([scenario.value for scenario in scenarios]),
            )
        )
    goals = list(dict.fromkeys([*filters.goals, *([filters.goal] if filters.goal else [])]))
    if goals:
        conditions.append(
            models.FieldCondition(
                key="goals",
                match=_match_values([goal.value for goal in goals]),
            )
        )
    if filters.relationship_stage not in (None, RelationshipStage.UNKNOWN):
        conditions.append(
            models.FieldCondition(
                key="relationship_stages",
                match=models.MatchValue(value=filters.relationship_stage.value),
            )
        )
    return models.Filter(must=conditions) if conditions else None


def _match_values(values: list[str]) -> models.Match:
    if len(values) == 1:
        return models.MatchValue(value=values[0])
    return models.MatchAny(any=values)


def _document_payload(document: KnowledgeDocument) -> dict:
    payload = document.model_dump(
        mode="json",
        exclude_none=True,
        exclude_computed_fields=True,
    )
    payload["content_hash"] = hashlib.sha256(document.retrieval_text.encode("utf-8")).hexdigest()
    return payload


def _point_id(document_id: str) -> str:
    return str(uuid.uuid5(_POINT_NAMESPACE, document_id))


def _batched[T](values: list[T], size: int) -> Iterable[list[T]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]

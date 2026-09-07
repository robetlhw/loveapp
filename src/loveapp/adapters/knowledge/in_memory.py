from contextlib import nullcontext

from loveapp.domain.enums import RelationshipStage
from loveapp.domain.knowledge import (
    KnowledgeDocument,
    KnowledgeFilters,
    KnowledgeSearchResult,
    RetrievalTextMode,
    RetrievedDocument,
)
from loveapp.ports.observability import TraceRecorder

from .scoring import RerankConfig, soft_rerank, text_terms


class InMemoryKnowledgeRetriever:
    """A deterministic development adapter; production RAG will implement the same port."""

    def __init__(
        self,
        documents: list[KnowledgeDocument],
        *,
        rerank_config: RerankConfig | None = None,
        retrieval_text_mode: RetrievalTextMode = RetrievalTextMode.FULL,
        hard_filter: bool = False,
    ) -> None:
        self._documents = list(documents)
        self._rerank_config = rerank_config or RerankConfig()
        self._retrieval_text_mode = retrieval_text_mode
        self._hard_filter = hard_filter

    async def search(
        self,
        query: str,
        filters: KnowledgeFilters | None = None,
        limit: int = 5,
        trace: TraceRecorder | None = None,
    ) -> list[RetrievedDocument]:
        result = await self.search_detailed(
            query,
            filters=filters,
            limit=limit,
            trace=trace,
        )
        return result.returned

    async def search_detailed(
        self,
        query: str,
        filters: KnowledgeFilters | None = None,
        limit: int = 5,
        trace: TraceRecorder | None = None,
    ) -> KnowledgeSearchResult:
        effective_filters = filters
        if self._hard_filter and filters is not None and not filters.hard:
            effective_filters = filters.model_copy(update={"hard": True})
        matches: list[RetrievedDocument] = []
        query_terms = text_terms(query)

        measure = trace.measure("rag_candidate_scoring") if trace else nullcontext({})
        with measure:
            for document in self._documents:
                if effective_filters and effective_filters.hard and not _matches_filters(
                    document, effective_filters
                ):
                    continue

                document_terms = text_terms(
                    document.render_retrieval_text(self._retrieval_text_mode)
                )
                overlap = query_terms & document_terms
                score = len(overlap) / max(len(query_terms), 1)

                if score > 0:
                    matches.append(
                        RetrievedDocument(
                            document=document,
                            score=round(score, 6),
                            base_score=round(score, 6),
                        )
                    )

        measure = trace.measure("rag_soft_rerank") if trace else nullcontext({})
        with measure:
            reranked = soft_rerank(query, matches, effective_filters, self._rerank_config)
        return KnowledgeSearchResult(
            returned=reranked[:limit],
            nearest_candidates=matches,
            candidates=matches,
            reranked_candidates=reranked,
        )


def _matches_filters(
    document: KnowledgeDocument,
    filters: KnowledgeFilters | None,
) -> bool:
    if filters is None:
        return True
    scenarios = list(
        dict.fromkeys([*filters.scenarios, *([filters.scenario] if filters.scenario else [])])
    )
    if scenarios and document.scenario not in scenarios:
        return False
    goals = list(dict.fromkeys([*filters.goals, *([filters.goal] if filters.goal else [])]))
    if goals and not any(goal in document.goals for goal in goals):
        return False
    return not (
        filters.relationship_stage not in (None, RelationshipStage.UNKNOWN)
        and filters.relationship_stage not in document.relationship_stages
    )

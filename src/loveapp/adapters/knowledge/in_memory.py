from contextlib import nullcontext

from loveapp.domain.enums import RelationshipStage
from loveapp.domain.knowledge import KnowledgeDocument, KnowledgeFilters, RetrievedDocument
from loveapp.ports.observability import TraceRecorder

from .scoring import soft_rerank, text_terms


class InMemoryKnowledgeRetriever:
    """A deterministic development adapter; production RAG will implement the same port."""

    def __init__(self, documents: list[KnowledgeDocument]) -> None:
        self._documents = list(documents)

    async def search(
        self,
        query: str,
        filters: KnowledgeFilters | None = None,
        limit: int = 5,
        trace: TraceRecorder | None = None,
    ) -> list[RetrievedDocument]:
        matches: list[RetrievedDocument] = []
        query_terms = text_terms(query)

        measure = trace.measure("rag_candidate_scoring") if trace else nullcontext({})
        with measure:
            for document in self._documents:
                if filters and filters.hard and not _matches_filters(document, filters):
                    continue

                document_terms = text_terms(document.retrieval_text)
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
            return soft_rerank(query, matches, filters)[:limit]


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
    if goals and document.goals and not any(goal in document.goals for goal in goals):
        return False
    return not (
        filters.relationship_stage not in (None, RelationshipStage.UNKNOWN)
        and document.relationship_stages
        and filters.relationship_stage not in document.relationship_stages
    )

from typing import Protocol

from loveapp.domain.knowledge import KnowledgeFilters, RetrievedDocument
from loveapp.ports.observability import TraceRecorder


class KnowledgeRetriever(Protocol):
    async def search(
        self,
        query: str,
        filters: KnowledgeFilters | None = None,
        limit: int = 5,
        trace: TraceRecorder | None = None,
    ) -> list[RetrievedDocument]: ...

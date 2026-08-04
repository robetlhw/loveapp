from collections.abc import Callable
from typing import Protocol

from loveapp.domain.advice import (
    AdviceRequest,
    AdviceResponse,
    AdviceStreamEvent,
    RelationshipContext,
)
from loveapp.domain.enums import AdviceScenario
from loveapp.domain.knowledge import RetrievedDocument
from loveapp.domain.memory import StoredMessage
from loveapp.domain.policy import ResolvedScenarioPolicy

AdviceStreamCallback = Callable[[AdviceStreamEvent], None]


class AdviceComposer(Protocol):
    async def compose(
        self,
        request: AdviceRequest,
        scenario: AdviceScenario,
        context: RelationshipContext,
        documents: list[RetrievedDocument],
        conversation_history: list[StoredMessage],
        policy: ResolvedScenarioPolicy,
        stream_callback: AdviceStreamCallback | None = None,
    ) -> AdviceResponse: ...

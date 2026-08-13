from typing import Protocol

from loveapp.domain.conversation import ConversationFlowState


class ConversationFlowStateStore(Protocol):
    async def get(
        self,
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str,
    ) -> ConversationFlowState | None: ...

    async def save(self, state: ConversationFlowState) -> ConversationFlowState: ...

    async def delete(
        self,
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str,
    ) -> bool: ...

    async def aclose(self) -> None: ...

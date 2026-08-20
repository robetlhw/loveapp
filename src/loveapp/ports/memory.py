from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from loveapp.domain.advice import RelationshipContext
from loveapp.domain.memory import (
    AtomicExtraction,
    MemoryCandidate,
    MemoryExtractionAttempt,
    MemoryExtractionRun,
    MemoryItem,
    MemoryKind,
    MemorySaveResult,
    MemoryStatus,
    MessageRole,
    StoredMessage,
)
from loveapp.domain.memory_verification import ClaimVerification
from loveapp.domain.memory_write import (
    MemoryTransitionAudit,
    MemoryWriteBatch,
    MemoryWriteBatchResult,
)
from loveapp.domain.relationship_plan import PlanStatus, RelationshipPlan
from loveapp.ports.observability import TraceRecorder

MemoryAttemptCallback = Callable[[MemoryExtractionAttempt], None]


class MemoryStore(Protocol):
    async def add_message(
        self,
        *,
        user_id: str,
        relationship_id: str,
        role: MessageRole,
        content: str,
        conversation_id: str | None = None,
    ) -> StoredMessage: ...

    async def list_messages(
        self,
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str | None = None,
        limit: int = 20,
    ) -> list[StoredMessage]: ...

    async def save_memory(
        self,
        *,
        user_id: str,
        relationship_id: str,
        candidate: MemoryCandidate,
        source_message_id: str | None = None,
        status: MemoryStatus = MemoryStatus.PROPOSED,
    ) -> MemorySaveResult: ...

    async def save_memories(
        self,
        *,
        user_id: str,
        relationship_id: str,
        candidates: list[MemoryCandidate],
        source_message_id: str | None = None,
        status: MemoryStatus = MemoryStatus.PROPOSED,
    ) -> list[MemorySaveResult]: ...

    async def commit_memory_batch(
        self,
        *,
        user_id: str,
        relationship_id: str,
        batch: MemoryWriteBatch,
    ) -> MemoryWriteBatchResult: ...

    async def get_memory(self, memory_id: str, user_id: str) -> MemoryItem | None: ...

    async def list_memories(
        self,
        *,
        user_id: str,
        relationship_id: str | None = None,
        kind: MemoryKind | None = None,
        status: MemoryStatus | None = None,
        limit: int = 100,
        read_only: bool = False,
    ) -> list[MemoryItem]: ...

    async def set_memory_status(
        self,
        memory_id: str,
        user_id: str,
        status: MemoryStatus,
    ) -> MemoryItem | None: ...

    async def delete_memory(self, memory_id: str, user_id: str) -> bool: ...

    async def clear_memories(self, user_id: str, relationship_id: str | None = None) -> int: ...

    async def get_relationship_context(
        self,
        user_id: str,
        relationship_id: str,
        limit: int = 20,
        read_only: bool = False,
    ) -> RelationshipContext | None: ...

    async def save_relationship_context(self, context: RelationshipContext) -> None: ...

    async def save_relationship_plan(self, plan: RelationshipPlan) -> RelationshipPlan: ...

    async def get_relationship_plan(
        self,
        plan_id: str,
        user_id: str,
    ) -> RelationshipPlan | None: ...

    async def list_relationship_plans(
        self,
        *,
        user_id: str,
        relationship_id: str,
        status: PlanStatus | None = None,
        limit: int = 100,
        read_only: bool = False,
    ) -> list[RelationshipPlan]: ...

    async def set_relationship_plan_status(
        self,
        *,
        plan_id: str,
        user_id: str,
        status: PlanStatus,
        transitioned_at: datetime | None = None,
        source_event_memory_id: str | None = None,
    ) -> RelationshipPlan | None: ...

    async def sync_relationship_plans(
        self,
        *,
        user_id: str,
        relationship_id: str,
    ) -> list[RelationshipPlan]: ...

    async def save_extraction_run(self, run: MemoryExtractionRun) -> None: ...

    async def list_extraction_runs(
        self,
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str | None = None,
        limit: int = 20,
    ) -> list[MemoryExtractionRun]: ...

    async def list_transition_audits(
        self,
        *,
        user_id: str,
        relationship_id: str,
        source_message_id: str | None = None,
        limit: int = 100,
    ) -> list[MemoryTransitionAudit]: ...

    async def aclose(self) -> None: ...


class MemoryExtractor(Protocol):
    async def extract(
        self,
        text: str,
        *,
        reference_time: datetime,
        existing_memories: list[MemoryItem],
        conversation_history: list[StoredMessage],
        trace: TraceRecorder | None = None,
        attempt_callback: MemoryAttemptCallback | None = None,
    ) -> AtomicExtraction: ...


class StrongClaimVerifier(Protocol):
    async def verify_claim(
        self,
        text: str,
        *,
        candidate: MemoryCandidate,
        existing_memories: list[MemoryItem],
        allowed_target_ids: set[str],
        trace: TraceRecorder | None = None,
    ) -> ClaimVerification: ...

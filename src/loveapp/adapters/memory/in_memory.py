from datetime import datetime
from uuid import uuid4

from loveapp.domain.advice import RelationshipContext
from loveapp.domain.memory import (
    MemoryCandidate,
    MemoryExtractionRun,
    MemoryItem,
    MemoryKind,
    MemorySaveResult,
    MemoryStatus,
    MessageRole,
    StoredMessage,
    memory_dedupe_key,
    utc_now,
)
from loveapp.domain.memory_context import attach_memories, select_context_memories
from loveapp.domain.relationship_plan import (
    ACTIVE_PLAN_STATUSES,
    PlanStatus,
    RelationshipPlan,
    can_transition_plan_status,
    memory_with_plan,
    relationship_plan_from_memory,
)


class InMemoryMemoryStore:
    def __init__(self) -> None:
        self._contexts: dict[tuple[str, str], RelationshipContext] = {}
        self._memories: dict[str, MemoryItem] = {}
        self._messages: dict[str, StoredMessage] = {}
        self._extraction_runs: dict[str, MemoryExtractionRun] = {}
        self._relationship_plans: dict[str, RelationshipPlan] = {}

    async def add_message(
        self,
        *,
        user_id: str,
        relationship_id: str,
        role: MessageRole,
        content: str,
        conversation_id: str | None = None,
    ) -> StoredMessage:
        message = StoredMessage(
            id=str(uuid4()),
            conversation_id=conversation_id or str(uuid4()),
            user_id=user_id,
            relationship_id=relationship_id,
            role=role,
            content=content,
        )
        self._messages[message.id] = message
        return message.model_copy(deep=True)

    async def list_messages(
        self,
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str | None = None,
        limit: int = 20,
    ) -> list[StoredMessage]:
        messages = [
            message
            for message in self._messages.values()
            if message.user_id == user_id
            and message.relationship_id == relationship_id
            and (conversation_id is None or message.conversation_id == conversation_id)
        ]
        messages.sort(key=lambda message: message.created_at)
        return [message.model_copy(deep=True) for message in messages[-limit:]]

    async def save_memory(
        self,
        *,
        user_id: str,
        relationship_id: str,
        candidate: MemoryCandidate,
        source_message_id: str | None = None,
        status: MemoryStatus = MemoryStatus.PROPOSED,
    ) -> MemorySaveResult:
        now = utc_now()
        self._expire_due_memories(now)
        key = memory_dedupe_key(candidate)
        duplicates = [
            item
            for item in self._memories.values()
            if item.user_id == user_id
            and item.relationship_id == relationship_id
            and item.status in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
            and memory_dedupe_key(item) == key
        ]
        if duplicates:
            item = max(duplicates, key=_memory_keeper_rank)
            for duplicate in duplicates:
                if duplicate.id != item.id:
                    duplicate.status = MemoryStatus.SUPERSEDED
                    duplicate.updated_at = now
            if status == MemoryStatus.CONFIRMED:
                item.status = status
            item.updated_at = now
            item.confidence = max(item.confidence, candidate.confidence)
            item.importance = max(item.importance, candidate.importance)
            item.dedupe_key = key
            self._sync_plan_for_memory(item)
            return MemorySaveResult(item=item.model_copy(deep=True), created=False)

        if candidate.supersedes_id:
            previous = self._memories.get(candidate.supersedes_id)
            if not previous or (previous.user_id, previous.relationship_id) != (
                user_id,
                relationship_id,
            ):
                raise ValueError("The superseded memory is outside the current relationship scope.")
            previous.status = MemoryStatus.SUPERSEDED
            previous.updated_at = utc_now()
            self._deactivate_plan_for_memory(
                previous.id,
                PlanStatus.CANCELLED,
                previous.updated_at,
            )

        item = MemoryItem(
            id=str(uuid4()),
            user_id=user_id,
            relationship_id=relationship_id,
            source_message_id=source_message_id,
            status=status,
            dedupe_key=key,
            created_at=now,
            updated_at=now,
            **candidate.model_dump(),
        )
        self._memories[item.id] = item
        self._sync_plan_for_memory(item)
        return MemorySaveResult(item=item.model_copy(deep=True), created=True)

    async def save_memories(
        self,
        *,
        user_id: str,
        relationship_id: str,
        candidates: list[MemoryCandidate],
        source_message_id: str | None = None,
        status: MemoryStatus = MemoryStatus.PROPOSED,
    ) -> list[MemorySaveResult]:
        snapshot = {
            memory_id: item.model_copy(deep=True) for memory_id, item in self._memories.items()
        }
        plan_snapshot = {
            plan_id: item.model_copy(deep=True)
            for plan_id, item in self._relationship_plans.items()
        }
        try:
            return [
                await self.save_memory(
                    user_id=user_id,
                    relationship_id=relationship_id,
                    candidate=candidate,
                    source_message_id=source_message_id,
                    status=status,
                )
                for candidate in candidates
            ]
        except Exception:
            self._memories = snapshot
            self._relationship_plans = plan_snapshot
            raise

    async def get_memory(self, memory_id: str, user_id: str) -> MemoryItem | None:
        item = self._memories.get(memory_id)
        if not item or item.user_id != user_id:
            return None
        return item.model_copy(deep=True)

    async def list_memories(
        self,
        *,
        user_id: str,
        relationship_id: str | None = None,
        kind: MemoryKind | None = None,
        status: MemoryStatus | None = None,
        limit: int = 100,
    ) -> list[MemoryItem]:
        self._expire_due_memories()
        items = [
            item
            for item in self._memories.values()
            if item.user_id == user_id
            and (relationship_id is None or item.relationship_id == relationship_id)
            and (kind is None or item.kind == kind)
            and (status is None or item.status == status)
        ]
        items.sort(key=lambda item: (item.importance, item.updated_at), reverse=True)
        return [item.model_copy(deep=True) for item in items[:limit]]

    async def set_memory_status(
        self,
        memory_id: str,
        user_id: str,
        status: MemoryStatus,
    ) -> MemoryItem | None:
        item = self._memories.get(memory_id)
        if not item or item.user_id != user_id:
            return None
        item.status = status
        item.updated_at = utc_now()
        if item.kind == MemoryKind.PLANNED_EVENT:
            if status == MemoryStatus.EXPIRED:
                self._deactivate_plan_for_memory(
                    item.id,
                    PlanStatus.EXPIRED,
                    item.updated_at,
                )
            elif status in {MemoryStatus.REJECTED, MemoryStatus.SUPERSEDED}:
                self._deactivate_plan_for_memory(
                    item.id,
                    PlanStatus.CANCELLED,
                    item.updated_at,
                )
        return item.model_copy(deep=True)

    async def delete_memory(self, memory_id: str, user_id: str) -> bool:
        item = self._memories.get(memory_id)
        if not item or item.user_id != user_id:
            return False
        source_message_id = item.source_message_id
        linked_plan_ids = [
            plan.plan_id
            for plan in self._relationship_plans.values()
            if plan.source_memory_id == memory_id
        ]
        for plan_id in linked_plan_ids:
            del self._relationship_plans[plan_id]
        del self._memories[memory_id]
        if source_message_id and not any(
            memory.source_message_id == source_message_id for memory in self._memories.values()
        ):
            self._messages.pop(source_message_id, None)
        return True

    async def clear_memories(self, user_id: str, relationship_id: str | None = None) -> int:
        ids = [
            item.id
            for item in self._memories.values()
            if item.user_id == user_id
            and (relationship_id is None or item.relationship_id == relationship_id)
        ]
        for memory_id in ids:
            del self._memories[memory_id]
        plan_ids = [
            plan.plan_id
            for plan in self._relationship_plans.values()
            if plan.user_id == user_id
            and (relationship_id is None or plan.relationship_id == relationship_id)
        ]
        for plan_id in plan_ids:
            del self._relationship_plans[plan_id]
        message_ids = [
            message.id
            for message in self._messages.values()
            if message.user_id == user_id
            and (relationship_id is None or message.relationship_id == relationship_id)
        ]
        for message_id in message_ids:
            del self._messages[message_id]
        run_ids = [
            run.id
            for run in self._extraction_runs.values()
            if run.user_id == user_id
            and (relationship_id is None or run.relationship_id == relationship_id)
        ]
        for run_id in run_ids:
            del self._extraction_runs[run_id]
        return len(ids)

    async def get_relationship_context(
        self,
        user_id: str,
        relationship_id: str,
        limit: int = 20,
    ) -> RelationshipContext | None:
        context = self._contexts.get((user_id, relationship_id))
        if context is None:
            return None
        plans = await self.sync_relationship_plans(
            user_id=user_id,
            relationship_id=relationship_id,
        )
        active_plans = [plan for plan in plans if plan.status in ACTIVE_PLAN_STATUSES]
        active_plan_memory_ids = {
            plan.source_memory_id for plan in active_plans if plan.source_memory_id is not None
        }
        memories = await self.list_memories(
            user_id=user_id,
            relationship_id=relationship_id,
            limit=limit,
        )
        now = utc_now()
        active = [
            item
            for item in memories
            if item.status in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
            and (item.expires_at is None or item.expires_at > now)
            and (
                item.kind != MemoryKind.PLANNED_EVENT
                or item.id in active_plan_memory_ids
            )
        ]
        selected = select_context_memories(active, limit=limit, reference_time=now)
        return attach_memories(
            context,
            selected,
            active_plans=active_plans,
            reference_time=now,
        )

    def _expire_due_memories(self, now: datetime | None = None) -> None:
        current = now or utc_now()
        for item in self._memories.values():
            if (
                item.status in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
                and item.expires_at is not None
                and item.expires_at <= current
            ):
                item.status = MemoryStatus.EXPIRED
                item.updated_at = current
                for plan in self._relationship_plans.values():
                    if plan.source_memory_id == item.id and plan.status in ACTIVE_PLAN_STATUSES:
                        plan.status = PlanStatus.EXPIRED
                        plan.updated_at = current

    def _expire_due_plans(self, now: datetime | None = None) -> None:
        current = now or utc_now()
        for plan in self._relationship_plans.values():
            if (
                plan.status in ACTIVE_PLAN_STATUSES
                and plan.expires_at is not None
                and plan.expires_at <= current
            ):
                plan.status = PlanStatus.EXPIRED
                plan.updated_at = current
                source = self._memories.get(plan.source_memory_id or "")
                if source is not None and source.status in {
                    MemoryStatus.PROPOSED,
                    MemoryStatus.CONFIRMED,
                }:
                    source.status = MemoryStatus.EXPIRED
                    source.updated_at = current

    async def save_relationship_context(self, context: RelationshipContext) -> None:
        key = (context.user_id, context.relationship_id)
        self._contexts[key] = context.model_copy(deep=True)

    async def save_relationship_plan(self, plan: RelationshipPlan) -> RelationshipPlan:
        existing = self._relationship_plans.get(plan.plan_id)
        if existing is not None and (existing.user_id, existing.relationship_id) != (
            plan.user_id,
            plan.relationship_id,
        ):
            raise ValueError("plan_id belongs to a different relationship scope")
        self._relationship_plans[plan.plan_id] = plan.model_copy(deep=True)
        return plan.model_copy(deep=True)

    async def get_relationship_plan(
        self,
        plan_id: str,
        user_id: str,
    ) -> RelationshipPlan | None:
        plan = self._relationship_plans.get(plan_id)
        if plan is None or plan.user_id != user_id:
            return None
        return plan.model_copy(deep=True)

    async def list_relationship_plans(
        self,
        *,
        user_id: str,
        relationship_id: str,
        status: PlanStatus | None = None,
        limit: int = 100,
    ) -> list[RelationshipPlan]:
        await self.sync_relationship_plans(
            user_id=user_id,
            relationship_id=relationship_id,
        )
        plans = [
            plan
            for plan in self._relationship_plans.values()
            if plan.user_id == user_id
            and plan.relationship_id == relationship_id
            and (status is None or plan.status == status)
        ]
        plans.sort(key=lambda plan: plan.updated_at, reverse=True)
        return [plan.model_copy(deep=True) for plan in plans[:limit]]

    async def set_relationship_plan_status(
        self,
        *,
        plan_id: str,
        user_id: str,
        status: PlanStatus,
        transitioned_at: datetime | None = None,
        source_event_memory_id: str | None = None,
    ) -> RelationshipPlan | None:
        plan = self._relationship_plans.get(plan_id)
        if plan is None or plan.user_id != user_id:
            return None
        if not can_transition_plan_status(plan.status, status):
            raise ValueError(f"invalid relationship plan transition: {plan.status} -> {status}")
        now = transitioned_at or utc_now()
        plan.status = status
        plan.updated_at = now
        if status == PlanStatus.COMPLETED:
            plan.completed_at = now
        elif status == PlanStatus.CANCELLED:
            plan.cancelled_at = now
        if source_event_memory_id is not None:
            plan.payload["terminal_event_memory_id"] = source_event_memory_id
        source = self._memories.get(plan.source_memory_id or "")
        if source is not None:
            source.payload["plan_status"] = status.value
            if status in {PlanStatus.COMPLETED, PlanStatus.CANCELLED}:
                source.status = MemoryStatus.SUPERSEDED
            elif status == PlanStatus.EXPIRED:
                source.status = MemoryStatus.EXPIRED
            source.updated_at = now
        return plan.model_copy(deep=True)

    async def sync_relationship_plans(
        self,
        *,
        user_id: str,
        relationship_id: str,
    ) -> list[RelationshipPlan]:
        self._expire_due_memories()
        self._expire_due_plans()
        existing_source_ids = {
            plan.source_memory_id
            for plan in self._relationship_plans.values()
            if plan.user_id == user_id and plan.relationship_id == relationship_id
        }
        planned_memories = [
            memory
            for memory in self._memories.values()
            if memory.user_id == user_id
            and memory.relationship_id == relationship_id
            and memory.kind == MemoryKind.PLANNED_EVENT
            and memory.status in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
        ]
        for memory in planned_memories:
            if memory.id not in existing_source_ids:
                self._sync_plan_for_memory(memory)
        return [
            plan.model_copy(deep=True)
            for plan in self._relationship_plans.values()
            if plan.user_id == user_id and plan.relationship_id == relationship_id
        ]

    def _sync_plan_for_memory(self, memory: MemoryItem) -> RelationshipPlan | None:
        if memory.kind != MemoryKind.PLANNED_EVENT:
            return None
        existing = next(
            (
                plan
                for plan in self._relationship_plans.values()
                if plan.source_memory_id == memory.id
            ),
            None,
        )
        if existing is None:
            existing = relationship_plan_from_memory(memory)
            collision = self._relationship_plans.get(existing.plan_id)
            if collision is not None and collision.source_memory_id != memory.id:
                payload = dict(memory.payload)
                payload.pop("plan_id", None)
                memory.payload = payload
                existing = relationship_plan_from_memory(memory)
            self._relationship_plans[existing.plan_id] = existing
        enriched = memory_with_plan(memory, existing)
        memory.payload = enriched.payload
        return existing.model_copy(deep=True)

    def _set_plan_status_for_memory(
        self,
        memory_id: str,
        status: PlanStatus,
        transitioned_at: datetime,
    ) -> None:
        plan = next(
            (
                item
                for item in self._relationship_plans.values()
                if item.source_memory_id == memory_id
            ),
            None,
        )
        if plan is None or plan.status not in ACTIVE_PLAN_STATUSES:
            return
        plan.status = status
        plan.updated_at = transitioned_at
        if status == PlanStatus.CANCELLED:
            plan.cancelled_at = transitioned_at

    def _deactivate_plan_for_memory(
        self,
        memory_id: str,
        status: PlanStatus,
        transitioned_at: datetime,
    ) -> None:
        self._set_plan_status_for_memory(memory_id, status, transitioned_at)

    async def save_extraction_run(self, run: MemoryExtractionRun) -> None:
        self._extraction_runs[run.id] = run.model_copy(deep=True)

    async def list_extraction_runs(
        self,
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str | None = None,
        limit: int = 20,
    ) -> list[MemoryExtractionRun]:
        runs = [
            run
            for run in self._extraction_runs.values()
            if run.user_id == user_id
            and run.relationship_id == relationship_id
            and (conversation_id is None or run.conversation_id == conversation_id)
        ]
        runs.sort(key=lambda run: run.updated_at, reverse=True)
        return [run.model_copy(deep=True) for run in runs[:limit]]

    async def aclose(self) -> None:
        return None


def _memory_keeper_rank(item: MemoryItem) -> tuple[int, int, float, object]:
    return (
        int(item.status == MemoryStatus.CONFIRMED),
        item.importance,
        item.confidence,
        item.updated_at,
    )

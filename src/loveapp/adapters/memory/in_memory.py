from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from loveapp.domain.advice import RelationshipContext
from loveapp.domain.memory import (
    AdmissionDecision,
    ClaimRelation,
    MemoryCandidate,
    MemoryExtractionRun,
    MemoryItem,
    MemoryKind,
    MemorySaveResult,
    MemoryStatus,
    MessageRole,
    StoredMessage,
    memory_dedupe_key,
    normalize_candidate_predicate,
    utc_now,
)
from loveapp.domain.memory_context import attach_memories, select_context_memories
from loveapp.domain.memory_write import (
    MemoryTransitionAudit,
    MemoryWriteBatch,
    MemoryWriteBatchResult,
    resolve_operation_target_ids,
)
from loveapp.domain.relationship_plan import (
    ACTIVE_PLAN_STATUSES,
    PlanStatus,
    RelationshipPlan,
    can_transition_plan_status,
    memory_with_plan,
    relationship_plan_from_memory,
)


class InMemoryMemoryStore:
    def __init__(self, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._clock = clock
        self._contexts: dict[tuple[str, str], RelationshipContext] = {}
        self._memories: dict[str, MemoryItem] = {}
        self._messages: dict[str, StoredMessage] = {}
        self._extraction_runs: dict[str, MemoryExtractionRun] = {}
        self._transition_audits: dict[str, MemoryTransitionAudit] = {}
        self._relationship_plans: dict[str, RelationshipPlan] = {}

    def set_clock(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock

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
            created_at=self._clock(),
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
        now = self._clock()
        self._expire_due_memories(now)
        candidate = normalize_candidate_predicate(candidate)
        key = memory_dedupe_key(candidate)
        if source_message_id is not None:
            idempotent = next(
                (
                    item
                    for item in self._memories.values()
                    if item.source_message_id == source_message_id
                    and item.dedupe_key == key
                ),
                None,
            )
            if idempotent is not None:
                return MemorySaveResult(item=idempotent.model_copy(deep=True), created=False)
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
            item.last_seen_at = now
            item.confidence = max(item.confidence, candidate.confidence)
            item.importance = max(item.importance, candidate.importance)
            item.evidence_spans = list(
                dict.fromkeys([*item.evidence_spans, *candidate.evidence_spans])
            )[:8]
            if _explicitness_rank(candidate.explicitness) > _explicitness_rank(
                item.explicitness
            ):
                item.explicitness = candidate.explicitness
            if item.canonical_predicate is None and candidate.canonical_predicate is not None:
                item.predicate_type = candidate.predicate_type
                item.canonical_predicate = candidate.canonical_predicate
                item.custom_predicate = None
                item.state_dimension = candidate.state_dimension
                item.state_value = candidate.state_value
            if candidate.admission_score is not None:
                item.admission_score = max(
                    item.admission_score or 0,
                    candidate.admission_score,
                )
                item.admission_decision = candidate.admission_decision
            if candidate.claim_relation is not None:
                item.claim_relation = candidate.claim_relation
            item.lifecycle_review_required = (
                item.lifecycle_review_required or candidate.lifecycle_review_required
            )
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
            previous.updated_at = self._clock()
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
            last_seen_at=now,
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

    async def commit_memory_batch(
        self,
        *,
        user_id: str,
        relationship_id: str,
        batch: MemoryWriteBatch,
    ) -> MemoryWriteBatchResult:
        memory_snapshot = {
            memory_id: item.model_copy(deep=True) for memory_id, item in self._memories.items()
        }
        plan_snapshot = {
            plan_id: item.model_copy(deep=True)
            for plan_id, item in self._relationship_plans.items()
        }
        audit_snapshot = dict(self._transition_audits)
        try:
            saved: list[MemorySaveResult] = []
            audits: list[MemoryTransitionAudit] = []
            for operation in batch.operations:
                candidate = operation.candidate.model_copy(update={"supersedes_id": None})
                result = await self.save_memory(
                    user_id=user_id,
                    relationship_id=relationship_id,
                    candidate=candidate,
                    source_message_id=batch.source_message_id,
                    status=operation.status,
                )
                saved.append(result)

            for update in batch.plan_updates:
                source_event_memory_id = (
                    saved[update.candidate_index].item.id
                    if update.candidate_index is not None
                    and update.candidate_index < len(saved)
                    else None
                )
                await self.set_relationship_plan_status(
                    plan_id=update.plan_id,
                    user_id=user_id,
                    status=update.status,
                    transitioned_at=update.transitioned_at,
                    source_event_memory_id=source_event_memory_id,
                    record_audit=False,
                )

            saved_memory_ids = [result.item.id for result in saved]
            resolved_targets: list[list[str]] = []
            for index, operation in enumerate(batch.operations):
                result = saved[index]
                operation_targets = resolve_operation_target_ids(
                    operation,
                    saved_memory_ids,
                    operation_index=index,
                )
                resolved_targets.append(operation_targets)
                transition_targets = (
                    operation_targets
                    if operation.relation == ClaimRelation.UPDATE
                    else []
                )
                for memory_id in transition_targets:
                    if memory_id == result.item.id:
                        continue
                    target = self._memories.get(memory_id)
                    if (
                        operation.status == MemoryStatus.PROPOSED
                        and target is not None
                        and target.status == MemoryStatus.CONFIRMED
                    ):
                        raise ValueError(
                            "a proposed memory cannot supersede a confirmed memory"
                        )
                    self._set_scoped_memory_status(
                        memory_id,
                        user_id,
                        relationship_id,
                        operation.target_status,
                    )
                if transition_targets:
                    stored = self._memories[result.item.id]
                    stored.supersedes_id = transition_targets[0]
                    result.item.supersedes_id = transition_targets[0]

            for update in batch.status_updates:
                self._set_scoped_memory_status(
                    update.memory_id,
                    user_id,
                    relationship_id,
                    update.status,
                )

            for index, operation in enumerate(batch.operations):
                candidate = operation.candidate
                audit = MemoryTransitionAudit(
                    user_id=user_id,
                    relationship_id=relationship_id,
                    source_message_id=batch.source_message_id,
                    incoming_memory_id=saved[index].item.id,
                    target_memory_ids=resolved_targets[index],
                    relation=operation.relation,
                    decision=(
                        candidate.admission_decision or AdmissionDecision.PROPOSE
                    ),
                    rule_name=operation.rule_name,
                    admission_score=candidate.admission_score,
                    score_breakdown=operation.score_breakdown,
                    raw_predicate=candidate.raw_predicate,
                    canonical_predicate=candidate.canonical_predicate,
                    extractor_model=candidate.extractor_model,
                    verifier_model=candidate.verifier_model,
                    prompt_version=candidate.prompt_version,
                    evidence=list(candidate.evidence_spans),
                    reason=operation.reason,
                    created_at=self._clock(),
                )
                self._transition_audits[audit.id] = audit
                audits.append(audit.model_copy(deep=True))
            for draft in batch.audit_only:
                audit = MemoryTransitionAudit(
                    user_id=user_id,
                    relationship_id=relationship_id,
                    source_message_id=batch.source_message_id,
                    incoming_memory_id=None,
                    target_memory_ids=draft.target_memory_ids,
                    relation=draft.relation,
                    decision=draft.decision,
                    rule_name=draft.rule_name,
                    admission_score=draft.admission_score,
                    score_breakdown=draft.score_breakdown,
                    raw_predicate=draft.raw_predicate,
                    canonical_predicate=draft.canonical_predicate,
                    extractor_model=draft.extractor_model,
                    verifier_model=draft.verifier_model,
                    prompt_version=draft.prompt_version,
                    evidence=draft.evidence,
                    reason=draft.reason,
                    created_at=self._clock(),
                )
                self._transition_audits[audit.id] = audit
                audits.append(audit.model_copy(deep=True))
            for update in batch.status_updates:
                item = self._memories[update.memory_id]
                audits.append(
                    self._record_memory_lifecycle_audit(
                        item,
                        rule_name=update.rule_name,
                        reason=update.reason,
                        target_status=update.status,
                    )
                )
            for update in batch.plan_updates:
                plan = self._relationship_plans[update.plan_id]
                incoming_memory_id = (
                    saved[update.candidate_index].item.id
                    if update.candidate_index is not None
                    and update.candidate_index < len(saved)
                    else None
                )
                audits.append(
                    self._record_plan_lifecycle_audit(
                        plan,
                        incoming_memory_id=incoming_memory_id,
                        rule_name=f"relationship_plan_{update.status.value}",
                        reason=(
                            "The relationship plan changed status in the atomic memory batch."
                        ),
                    )
                )
            committed_audits = [
                audit.model_copy(deep=True)
                for audit_id, audit in self._transition_audits.items()
                if audit_id not in audit_snapshot
            ]
            return MemoryWriteBatchResult(saved=saved, audits=committed_audits)
        except Exception:
            self._memories = memory_snapshot
            self._relationship_plans = plan_snapshot
            self._transition_audits = audit_snapshot
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
        previous_status = item.status
        self._set_scoped_memory_status(
            memory_id,
            user_id,
            item.relationship_id,
            status,
        )
        if previous_status != status:
            self._record_memory_lifecycle_audit(
                item,
                rule_name="set_memory_status",
                reason="The memory status was changed through the store lifecycle API.",
                target_status=status,
            )
        return item.model_copy(deep=True)

    def _set_scoped_memory_status(
        self,
        memory_id: str,
        user_id: str,
        relationship_id: str,
        status: MemoryStatus,
    ) -> None:
        item = self._memories.get(memory_id)
        if item is None or (item.user_id, item.relationship_id) != (
            user_id,
            relationship_id,
        ):
            raise ValueError("memory transition target is outside the current relationship scope")
        item.status = status
        item.updated_at = self._clock()
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
        now = self._clock()
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
        current = now or self._clock()
        for item in self._memories.values():
            if (
                item.status in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
                and item.expires_at is not None
                and item.expires_at <= current
            ):
                item.status = MemoryStatus.EXPIRED
                item.updated_at = current
                self._record_memory_lifecycle_audit(
                    item,
                    rule_name="ttl_expired",
                    reason="The memory reached its configured expiration time.",
                    target_status=MemoryStatus.EXPIRED,
                    created_at=current,
                )
                for plan in self._relationship_plans.values():
                    if plan.source_memory_id == item.id and plan.status in ACTIVE_PLAN_STATUSES:
                        plan.status = PlanStatus.EXPIRED
                        plan.updated_at = current
                        self._record_plan_lifecycle_audit(
                            plan,
                            rule_name="plan_source_memory_expired",
                            reason="The source planned-event memory expired.",
                            created_at=current,
                        )

    def _expire_due_plans(self, now: datetime | None = None) -> None:
        current = now or self._clock()
        for plan in self._relationship_plans.values():
            if (
                plan.status in ACTIVE_PLAN_STATUSES
                and plan.expires_at is not None
                and plan.expires_at <= current
            ):
                plan.status = PlanStatus.EXPIRED
                plan.updated_at = current
                self._record_plan_lifecycle_audit(
                    plan,
                    rule_name="plan_ttl_expired",
                    reason="The relationship plan reached its configured expiration time.",
                    created_at=current,
                )
                source = self._memories.get(plan.source_memory_id or "")
                if source is not None and source.status in {
                    MemoryStatus.PROPOSED,
                    MemoryStatus.CONFIRMED,
                }:
                    source.status = MemoryStatus.EXPIRED
                    source.updated_at = current
                    self._record_memory_lifecycle_audit(
                        source,
                        rule_name="plan_ttl_expired",
                        reason="The linked relationship plan expired.",
                        target_status=MemoryStatus.EXPIRED,
                        created_at=current,
                    )

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
        record_audit: bool = True,
    ) -> RelationshipPlan | None:
        plan = self._relationship_plans.get(plan_id)
        if plan is None or plan.user_id != user_id:
            return None
        if not can_transition_plan_status(plan.status, status):
            raise ValueError(f"invalid relationship plan transition: {plan.status} -> {status}")
        now = transitioned_at or self._clock()
        previous_status = plan.status
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
        if previous_status != status and record_audit:
            self._record_plan_lifecycle_audit(
                plan,
                incoming_memory_id=source_event_memory_id,
                rule_name=f"set_relationship_plan_status:{status.value}",
                reason="The plan status was changed through the store lifecycle API.",
                created_at=now,
            )
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
        self._record_plan_lifecycle_audit(
            plan,
            rule_name="plan_source_memory_status_changed",
            reason="The source planned-event memory left its active lifecycle state.",
            created_at=transitioned_at,
        )

    def _deactivate_plan_for_memory(
        self,
        memory_id: str,
        status: PlanStatus,
        transitioned_at: datetime,
    ) -> None:
        self._set_plan_status_for_memory(memory_id, status, transitioned_at)

    def _record_memory_lifecycle_audit(
        self,
        item: MemoryItem,
        *,
        rule_name: str,
        reason: str,
        target_status: MemoryStatus,
        created_at: datetime | None = None,
    ) -> MemoryTransitionAudit:
        audit = MemoryTransitionAudit(
            user_id=item.user_id,
            relationship_id=item.relationship_id,
            source_message_id=item.source_message_id,
            target_memory_ids=[item.id],
            relation=ClaimRelation.UPDATE,
            decision=item.admission_decision or AdmissionDecision.PROPOSE,
            rule_name=rule_name,
            admission_score=item.admission_score,
            score_breakdown={"target_memory_status": target_status.value},
            raw_predicate=item.raw_predicate,
            canonical_predicate=item.canonical_predicate,
            extractor_model=item.extractor_model,
            verifier_model=item.verifier_model,
            prompt_version=item.prompt_version,
            evidence=item.evidence_spans,
            reason=reason,
            created_at=created_at or self._clock(),
        )
        self._transition_audits[audit.id] = audit
        return audit.model_copy(deep=True)

    def _record_plan_lifecycle_audit(
        self,
        plan: RelationshipPlan,
        *,
        rule_name: str,
        reason: str,
        incoming_memory_id: str | None = None,
        created_at: datetime | None = None,
    ) -> MemoryTransitionAudit:
        source = self._memories.get(plan.source_memory_id or "")
        audit = MemoryTransitionAudit(
            user_id=plan.user_id,
            relationship_id=plan.relationship_id,
            source_message_id=plan.source_message_id,
            incoming_memory_id=incoming_memory_id,
            target_memory_ids=[source.id] if source is not None else [],
            relation=ClaimRelation.UPDATE,
            decision=(
                source.admission_decision
                if source is not None and source.admission_decision is not None
                else AdmissionDecision.PROPOSE
            ),
            rule_name=rule_name,
            admission_score=source.admission_score if source is not None else None,
            score_breakdown={
                "plan_id": plan.plan_id,
                "target_plan_status": plan.status.value,
            },
            raw_predicate="plan.status",
            canonical_predicate="plan.status",
            extractor_model=source.extractor_model if source is not None else None,
            verifier_model=source.verifier_model if source is not None else None,
            prompt_version=source.prompt_version if source is not None else None,
            evidence=source.evidence_spans if source is not None else [],
            reason=reason,
            created_at=created_at or self._clock(),
        )
        self._transition_audits[audit.id] = audit
        return audit.model_copy(deep=True)

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

    async def list_transition_audits(
        self,
        *,
        user_id: str,
        relationship_id: str,
        source_message_id: str | None = None,
        limit: int = 100,
    ) -> list[MemoryTransitionAudit]:
        audits = [
            audit
            for audit in self._transition_audits.values()
            if audit.user_id == user_id
            and audit.relationship_id == relationship_id
            and (
                source_message_id is None
                or audit.source_message_id == source_message_id
            )
        ]
        audits.sort(key=lambda audit: audit.created_at, reverse=True)
        return [audit.model_copy(deep=True) for audit in audits[:limit]]

    async def aclose(self) -> None:
        return None


def _memory_keeper_rank(item: MemoryItem) -> tuple[int, int, float, object]:
    return (
        int(item.status == MemoryStatus.CONFIRMED),
        item.importance,
        item.confidence,
        item.updated_at,
    )


def _explicitness_rank(value) -> int:
    return {
        "speculative": 0,
        "weakly_inferred": 1,
        "strongly_implied": 2,
        "explicit": 3,
    }.get(str(value), 0)

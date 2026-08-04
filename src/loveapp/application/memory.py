import asyncio
import inspect
from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from loveapp.domain.advice import RelationshipContext
from loveapp.domain.enums import RelationshipStage
from loveapp.domain.memory import (
    AtomicExtraction,
    DiscardedSpan,
    MemoryCandidate,
    MemoryCompactionGroup,
    MemoryCompactionResult,
    MemoryExtractionAttempt,
    MemoryExtractionRun,
    MemoryExtractionStatus,
    MemoryGateDecision,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    MemoryValence,
    MessageRole,
    RelationshipImpact,
    RememberResult,
    StoredMessage,
    TimeKind,
    memory_dedupe_key,
    utc_now,
)
from loveapp.domain.memory_context import (
    attach_memories,
    select_context_memories,
)
from loveapp.domain.memory_lifecycle import (
    legacy_transition_target_ids,
    normalize_memory_candidate,
    plan_memory_transitions,
    semantic_duplicate_ids,
)
from loveapp.domain.relationship_evidence import (
    project_standardized_relationship_evidence,
    standardize_relationship_evidence,
)
from loveapp.domain.relationship_plan import (
    ACTIVE_PLAN_STATUSES,
    PlanStatus,
    RelationshipPlan,
    add_plan_identity,
    has_retrospective_event_semantics,
    match_plan_transitions,
    memory_references_plan,
    memory_with_plan,
    suppressed_plan_ids_for_text,
)
from loveapp.ports.memory import MemoryExtractor, MemoryStore
from loveapp.ports.observability import TraceRecorder

from .memory_gate import MemoryGate
from .relationship_events import (
    build_contextual_relationship_candidate,
    build_pending_confession_candidate,
    may_contain_contextual_relationship_event,
    resolve_contextual_relationship_event,
)


class MemoryService:
    def __init__(
        self,
        store: MemoryStore,
        extractor: MemoryExtractor,
        *,
        min_confidence: float = 0.65,
        tentative_min_confidence: float = 0.5,
        belief_min_confidence: float = 0.4,
        context_limit: int = 20,
        history_limit: int = 12,
        context_wait_seconds: float = 2,
        gate: MemoryGate | None = None,
        shutdown_grace_seconds: float = 10,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.store = store
        self._extractor = extractor
        self._min_confidence = min_confidence
        self._tentative_min_confidence = tentative_min_confidence
        self._belief_min_confidence = belief_min_confidence
        self._context_limit = context_limit
        self._history_limit = history_limit
        self._context_wait_seconds = max(context_wait_seconds, 0)
        self._gate = gate or MemoryGate()
        self._shutdown_grace_seconds = shutdown_grace_seconds
        self._clock = clock
        self._background_tasks: set[asyncio.Task] = set()
        self._background_task_scopes: dict[asyncio.Task, tuple[str, str]] = {}

    async def remember_text(
        self,
        *,
        user_id: str,
        relationship_id: str,
        text: str,
        conversation_id: str | None = None,
        relationship_stage: RelationshipStage = RelationshipStage.UNKNOWN,
        status: MemoryStatus = MemoryStatus.PROPOSED,
        raise_on_extraction_error: bool = False,
        trace: TraceRecorder | None = None,
    ) -> RememberResult:
        conversation_history = (
            await self.get_conversation_history(
                user_id,
                relationship_id,
                conversation_id,
            )
            if conversation_id
            else []
        )
        message = await self.record_message(
            user_id=user_id,
            relationship_id=relationship_id,
            role=MessageRole.USER,
            content=text,
            conversation_id=conversation_id,
        )
        return await self.remember_recorded_message(
            message=message,
            text=text,
            status=status,
            raise_on_extraction_error=raise_on_extraction_error,
            conversation_history=conversation_history,
            trace=trace,
        )

    async def remember_recorded_message(
        self,
        *,
        message: StoredMessage,
        text: str,
        status: MemoryStatus = MemoryStatus.PROPOSED,
        raise_on_extraction_error: bool = False,
        conversation_history: list[StoredMessage] | None = None,
        trace: TraceRecorder | None = None,
    ) -> RememberResult:
        retrospective_probe = has_retrospective_event_semantics(text)
        contextual_probe = may_contain_contextual_relationship_event(text)
        preloaded_memories: list[MemoryItem] | None = None
        if conversation_history is None and (contextual_probe or retrospective_probe):
            conversation_history = await self.get_conversation_history(
                message.user_id,
                message.relationship_id,
                message.conversation_id,
                exclude_message_id=message.id,
            )
        if contextual_probe or retrospective_probe:
            preloaded_memories = await self.store.list_memories(
                user_id=message.user_id,
                relationship_id=message.relationship_id,
                limit=200,
            )
        gate_decision = _evaluate_gate(
            self._gate,
            text,
            conversation_history=conversation_history or [],
            existing_memories=preloaded_memories or [],
        )
        now = self._clock()
        extraction_run = MemoryExtractionRun(
            id=str(uuid4()),
            user_id=message.user_id,
            relationship_id=message.relationship_id,
            conversation_id=message.conversation_id,
            source_message_id=message.id,
            status=(
                MemoryExtractionStatus.RUNNING
                if gate_decision.should_extract
                else MemoryExtractionStatus.SKIPPED
            ),
            gate_decision=gate_decision,
            created_at=now,
            updated_at=now,
            completed_at=None if gate_decision.should_extract else now,
        )
        await self.store.save_extraction_run(extraction_run)
        if not gate_decision.should_extract:
            return RememberResult(
                message=message,
                gate_decision=gate_decision,
                extraction_run_id=extraction_run.id,
            )
        if conversation_history is None:
            conversation_history = await self.get_conversation_history(
                message.user_id,
                message.relationship_id,
                message.conversation_id,
                exclude_message_id=message.id,
            )
        if preloaded_memories is None:
            existing = await self.store.list_memories(
                user_id=message.user_id,
                relationship_id=message.relationship_id,
                limit=200,
            )
        else:
            existing = preloaded_memories
        relationship_plans = await self.store.sync_relationship_plans(
            user_id=message.user_id,
            relationship_id=message.relationship_id,
        )
        relationship_plans = await self.reconcile_relationship_plans(
            user_id=message.user_id,
            relationship_id=message.relationship_id,
            memories=existing,
            plans=relationship_plans,
        )
        existing = _filter_memories_for_plan_status(existing, relationship_plans)
        existing = _attach_plan_metadata(existing, relationship_plans)
        active = [
            item
            for item in existing
            if item.status in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
        ]
        reconciled_ids = await self._reconcile_active_lifecycle(
            message.user_id,
            active,
        )
        if reconciled_ids:
            active = [item for item in active if item.id not in reconciled_ids]
        contextual_event = resolve_contextual_relationship_event(
            text,
            conversation_history,
            active,
        )
        contextual_candidates = (
            [
                build_contextual_relationship_candidate(
                    contextual_event,
                    reference_time=self._clock(),
                )
            ]
            if contextual_event is not None
            else []
        )
        pending_candidates = []
        pending_confession = build_pending_confession_candidate(
            text,
            reference_time=self._clock(),
        )
        if pending_confession is not None:
            pending_candidates.append(pending_confession)
        deterministic_candidates = [*contextual_candidates, *pending_candidates]
        deterministic_saved = []
        if deterministic_candidates:
            try:
                deterministic_saved = await self.store.save_memories(
                    user_id=message.user_id,
                    relationship_id=message.relationship_id,
                    candidates=deterministic_candidates,
                    source_message_id=message.id,
                    status=status,
                )
                await self._project_relationship_stage(
                    message.user_id,
                    message.relationship_id,
                    [
                        saved.item
                        for saved in deterministic_saved
                        if _candidate_predicate(saved.item)
                        in _RELATIONSHIP_STAGE_EVENT_PREDICATES
                    ],
                )
                await self.store.save_extraction_run(
                    extraction_run.model_copy(
                        update={
                            "saved_memory_ids": [saved.item.id for saved in deterministic_saved],
                            "updated_at": self._clock(),
                        }
                    )
                )
            except Exception as exc:
                await self._finish_extraction_run(
                    extraction_run,
                    MemoryExtractionStatus.FAILED,
                    attempts=[],
                    saved_memory_ids=[saved.item.id for saved in deterministic_saved],
                    error=f"deterministic memory persistence failed: {exc}",
                )
                raise
        if deterministic_saved:
            active_ids = {item.id for item in active}
            for saved in deterministic_saved:
                if (
                    saved.item.status in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
                    and saved.item.id not in active_ids
                ):
                    active.append(saved.item)
                    active_ids.add(saved.item.id)
        attempts: list[MemoryExtractionAttempt] = []
        try:
            extraction_kwargs = {
                "reference_time": self._clock(),
                "existing_memories": select_context_memories(
                    active,
                    query=text,
                    limit=20,
                    reference_time=now,
                ),
                "conversation_history": conversation_history,
                "trace": trace,
            }
            # Keep older third-party extractors usable while the telemetry
            # callback is adopted by the extractor port.
            if _supports_keyword(self._extractor.extract, "attempt_callback"):
                extraction_kwargs["attempt_callback"] = attempts.append
            extraction = await self._extractor.extract(text, **extraction_kwargs)
        except asyncio.CancelledError:
            await self._finish_extraction_run(
                extraction_run,
                MemoryExtractionStatus.CANCELLED,
                attempts=attempts,
                saved_memory_ids=[saved.item.id for saved in deterministic_saved],
                error="memory extraction task was cancelled",
            )
            raise
        except Exception as exc:
            if deterministic_candidates:
                result = RememberResult(
                    message=message,
                    saved=list(deterministic_saved),
                    extraction_error=str(exc),
                    gate_decision=gate_decision,
                    extraction_run_id=extraction_run.id,
                )
                await self._finish_extraction_run(
                    extraction_run,
                    MemoryExtractionStatus.FAILED,
                    attempts=attempts,
                    saved_memory_ids=[saved.item.id for saved in result.saved],
                    error=str(exc),
                )
                if raise_on_extraction_error:
                    raise
                return result
            await self._finish_extraction_run(
                extraction_run,
                MemoryExtractionStatus.FAILED,
                attempts=attempts,
                error=str(exc),
            )
            if raise_on_extraction_error:
                raise
            return RememberResult(
                message=message,
                extraction_error=str(exc),
                gate_decision=gate_decision,
                extraction_run_id=extraction_run.id,
            )

        result = RememberResult(
            message=message,
            saved=list(deterministic_saved),
            discarded_spans=extraction.discarded_spans,
            gate_decision=gate_decision,
            extraction_run_id=extraction_run.id,
        )
        active_ids = {item.id for item in active}
        prepared: list[MemoryCandidate] = []
        extracted_candidates = [claim.to_candidate() for claim in extraction.claims]
        if deterministic_candidates:
            deterministic_predicates = {
                _candidate_predicate(candidate) for candidate in contextual_candidates
            }
            deterministic_predicates.update(
                _candidate_predicate(candidate) for candidate in pending_candidates
            )
            extracted_candidates = [
                candidate
                for candidate in extracted_candidates
                if _candidate_predicate(candidate) not in deterministic_predicates
            ]
        candidates = extracted_candidates
        for candidate in atomize_candidates(candidates):
            candidate = add_plan_identity(normalize_memory_candidate(candidate, now))
            if candidate.confidence < self._confidence_floor(candidate):
                result.skipped_low_confidence += 1
                continue
            updates: dict = {}
            if any(evidence not in text for evidence in candidate.evidence_spans):
                updates["original_text"] = text
                updates["evidence_spans"] = [text]
            if candidate.supersedes_id and candidate.supersedes_id not in active_ids:
                updates["supersedes_id"] = None
            if updates:
                candidate = candidate.model_copy(update=updates)
            prepared.append(candidate)
        plan_transitions = match_plan_transitions(prepared, relationship_plans)
        plans_by_id = {plan.plan_id: plan for plan in relationship_plans}
        for transition in plan_transitions:
            candidate = prepared[transition.candidate_index]
            payload = dict(candidate.payload)
            payload.setdefault("related_plan_id", transition.plan_id)
            if transition.target_status == PlanStatus.COMPLETED:
                payload.setdefault("completes_plan_id", transition.plan_id)
            matched_plan = plans_by_id.get(transition.plan_id)
            updates = {"payload": payload}
            if (
                matched_plan is not None
                and candidate.supersedes_id == matched_plan.source_memory_id
            ):
                updates["supersedes_id"] = None
            prepared[transition.candidate_index] = candidate.model_copy(update=updates)
        transition_plans = plan_memory_transitions(prepared, active)
        transition_updates = {
            memory_id: plan.target_status
            for plan in transition_plans
            for memory_id in plan.target_ids
        }
        for plan in transition_plans:
            candidate = prepared[plan.trigger_index]
            if candidate.supersedes_id is None and plan.target_ids:
                prepared[plan.trigger_index] = candidate.model_copy(
                    update={"supersedes_id": plan.target_ids[0]}
                )
        try:
            prepared_saved = []
            if prepared:
                prepared_saved = await self.store.save_memories(
                    user_id=message.user_id,
                    relationship_id=message.relationship_id,
                    candidates=prepared,
                    source_message_id=message.id,
                    status=status,
                )
                result.saved.extend(prepared_saved)
                await self._project_relationship_stage(
                    message.user_id,
                    message.relationship_id,
                    [saved.item for saved in result.saved],
                )
            for transition in plan_transitions:
                source_event_memory_id = (
                    prepared_saved[transition.candidate_index].item.id
                    if transition.candidate_index < len(prepared_saved)
                    else None
                )
                transitioned = await self.store.set_relationship_plan_status(
                    plan_id=transition.plan_id,
                    user_id=message.user_id,
                    status=transition.target_status,
                    transitioned_at=(
                        prepared[transition.candidate_index].occurred_at or self._clock()
                    ),
                    source_event_memory_id=source_event_memory_id,
                )
                if transitioned is not None and transition.target_status in {
                    PlanStatus.COMPLETED,
                    PlanStatus.CANCELLED,
                    PlanStatus.EXPIRED,
                }:
                    await self._close_linked_action_intents(
                        message.user_id,
                        transitioned,
                        active,
                    )
            for memory_id, target_status in transition_updates.items():
                await self.store.set_memory_status(
                    memory_id,
                    message.user_id,
                    target_status,
                )
            await self.store.sync_relationship_plans(
                user_id=message.user_id,
                relationship_id=message.relationship_id,
            )
        except Exception as exc:
            await self._finish_extraction_run(
                extraction_run,
                MemoryExtractionStatus.FAILED,
                attempts=attempts,
                discarded_spans=extraction.discarded_spans,
                error=f"memory persistence failed: {exc}",
            )
            raise
        await self._finish_extraction_run(
            extraction_run,
            MemoryExtractionStatus.COMPLETED,
            attempts=attempts,
            saved_memory_ids=[saved.item.id for saved in result.saved],
            discarded_spans=extraction.discarded_spans,
        )
        return result

    def start_background_extraction(
        self,
        *,
        message: StoredMessage,
        text: str,
        status: MemoryStatus = MemoryStatus.PROPOSED,
        conversation_history: list[StoredMessage] | None = None,
        trace: TraceRecorder | None = None,
        name: str = "loveapp-memory-extraction",
    ) -> asyncio.Task[RememberResult]:
        """Start the shared memory sidecar without blocking the main task.

        All user-facing workflows use this entry point so routing a turn to a
        different business agent does not change which memory system sees it.
        """

        async def extract() -> RememberResult:
            if trace is None:
                return await self.remember_recorded_message(
                    message=message,
                    text=text,
                    status=status,
                    conversation_history=conversation_history,
                )

            with trace.measure("memory_extraction") as details:
                result = await self.remember_recorded_message(
                    message=message,
                    text=text,
                    status=status,
                    conversation_history=conversation_history,
                    trace=trace,
                )
                if result.gate_decision is not None:
                    details["gate_reason"] = result.gate_decision.reason.value
                    details["gate_should_extract"] = result.gate_decision.should_extract
                details["saved_count"] = len(result.saved)
                return result

        create_task = getattr(trace, "create_task", None) if trace is not None else None
        if callable(create_task):
            task = create_task(extract(), name=name)
        else:
            task = asyncio.create_task(extract(), name=name)
        self.track_background_task(
            task,
            scope=(message.user_id, message.relationship_id),
        )
        return task

    def _confidence_floor(self, candidate: MemoryCandidate) -> float:
        source_type = candidate.payload.get("source_type")
        is_hearsay = isinstance(source_type, str) and source_type.casefold() in {
            "hearsay",
            "third_party_report",
        }
        if candidate.perspective == MemoryPerspective.USER_BELIEF:
            return self._belief_min_confidence
        if is_hearsay:
            return self._tentative_min_confidence
        return self._min_confidence

    async def _finish_extraction_run(
        self,
        run: MemoryExtractionRun,
        status: MemoryExtractionStatus,
        *,
        attempts: list[MemoryExtractionAttempt],
        saved_memory_ids: list[str] | None = None,
        discarded_spans: list[DiscardedSpan] | None = None,
        error: str | None = None,
    ) -> None:
        now = self._clock()
        await self.store.save_extraction_run(
            run.model_copy(
                update={
                    "status": status,
                    "attempts": list(attempts),
                    "saved_memory_ids": saved_memory_ids or [],
                    "discarded_spans": (
                        list(discarded_spans)
                        if discarded_spans is not None
                        else run.discarded_spans
                    ),
                    "error": error[:1000] if error else None,
                    "updated_at": now,
                    "completed_at": now,
                }
            )
        )

    async def wait_for_scope(
        self,
        *,
        user_id: str,
        relationship_id: str,
        timeout_seconds: float | None = None,
    ) -> int:
        """Wait briefly for earlier memory writes in the same relationship."""

        tasks = [
            task
            for task, scope in self._background_task_scopes.items()
            if scope == (user_id, relationship_id) and not task.done()
        ]
        if not tasks:
            return 0
        timeout = self._context_wait_seconds if timeout_seconds is None else max(timeout_seconds, 0)
        if timeout:
            await asyncio.wait(tasks, timeout=timeout)
        return sum(not task.done() for task in tasks)

    def track_background_task(
        self,
        task: asyncio.Task,
        *,
        scope: tuple[str, str] | None = None,
    ) -> None:
        self._background_tasks.add(task)
        if scope is not None:
            self._background_task_scopes[task] = scope
        task.add_done_callback(self._forget_background_task)
        task.add_done_callback(_consume_background_exception)

    def _forget_background_task(self, task: asyncio.Task) -> None:
        self._background_tasks.discard(task)
        self._background_task_scopes.pop(task, None)

    async def aclose(self) -> None:
        tasks = [task for task in self._background_tasks if not task.done()]
        if not tasks:
            return
        _done, pending = await asyncio.wait(tasks, timeout=self._shutdown_grace_seconds)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def compact_memories(
        self,
        *,
        user_id: str,
        relationship_id: str,
        apply: bool = False,
    ) -> MemoryCompactionResult:
        items = await self.store.list_memories(
            user_id=user_id,
            relationship_id=relationship_id,
            limit=1000,
        )
        active = [
            item
            for item in items
            if item.status in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
        ]
        grouped: dict[str, list[MemoryItem]] = {}
        for item in active:
            grouped.setdefault(memory_dedupe_key(item), []).append(item)

        groups: list[MemoryCompactionGroup] = []
        applied_count = 0
        for identity_key, duplicates in grouped.items():
            if len(duplicates) < 2:
                continue
            keeper = max(duplicates, key=_memory_keeper_rank)
            redundant = [item for item in duplicates if item.id != keeper.id]
            groups.append(
                MemoryCompactionGroup(
                    identity_key=identity_key,
                    keeper_id=keeper.id,
                    duplicate_ids=[item.id for item in redundant],
                    summaries=list(dict.fromkeys(item.summary for item in duplicates)),
                )
            )
            if apply:
                for item in redundant:
                    await self.store.set_memory_status(
                        item.id,
                        user_id,
                        MemoryStatus.SUPERSEDED,
                    )
                    applied_count += 1
        return MemoryCompactionResult(groups=groups, applied_count=applied_count)

    async def record_message(
        self,
        *,
        user_id: str,
        relationship_id: str,
        role: MessageRole,
        content: str,
        conversation_id: str | None = None,
        relationship_stage: RelationshipStage = RelationshipStage.UNKNOWN,
    ) -> StoredMessage:
        await self.ensure_context(user_id, relationship_id, relationship_stage)
        return await self.store.add_message(
            user_id=user_id,
            relationship_id=relationship_id,
            role=role,
            content=content,
            conversation_id=conversation_id,
        )

    async def get_conversation_history(
        self,
        user_id: str,
        relationship_id: str,
        conversation_id: str,
        exclude_message_id: str | None = None,
    ) -> list[StoredMessage]:
        messages = await self.store.list_messages(
            user_id=user_id,
            relationship_id=relationship_id,
            conversation_id=conversation_id,
            limit=self._history_limit + int(exclude_message_id is not None),
        )
        if exclude_message_id is not None:
            messages = [message for message in messages if message.id != exclude_message_id]
        return messages[-self._history_limit :]

    async def remember_date_preferences(
        self,
        *,
        user_id: str,
        relationship_id: str,
        preferences: list[str],
        relationship_stage: RelationshipStage = RelationshipStage.UNKNOWN,
    ) -> RememberResult | None:
        cleaned = list(dict.fromkeys(value.strip() for value in preferences if value.strip()))
        if not cleaned:
            return None
        await self.ensure_context(user_id, relationship_id, relationship_stage)
        text = f"约会安排偏好：{'、'.join(cleaned)}"
        message = await self.store.add_message(
            user_id=user_id,
            relationship_id=relationship_id,
            role=MessageRole.USER,
            content=text,
        )
        result = RememberResult(message=message)
        candidates: list[MemoryCandidate] = []
        for preference in cleaned:
            candidates.append(
                MemoryCandidate(
                    kind=MemoryKind.PREFERENCE,
                    subject="relationship",
                    summary=f"约会安排偏好：{preference}",
                    original_text=text,
                    evidence_spans=[text],
                    time_kind=TimeKind.TIMELESS,
                    valence=MemoryValence.POSITIVE,
                    relationship_impact=RelationshipImpact.UNCLEAR,
                    importance=3,
                    perspective=MemoryPerspective.USER_REPORTED,
                    confidence=1,
                    payload={"preference": preference, "preference_type": "date"},
                )
            )
        result.saved = await self.store.save_memories(
            user_id=user_id,
            relationship_id=relationship_id,
            candidates=candidates,
            source_message_id=message.id,
            status=MemoryStatus.CONFIRMED,
        )
        return result

    async def ensure_context(
        self,
        user_id: str,
        relationship_id: str,
        relationship_stage: RelationshipStage = RelationshipStage.UNKNOWN,
    ) -> RelationshipContext:
        context = await self.store.get_relationship_context(
            user_id,
            relationship_id,
            self._context_limit,
        )
        if context is None:
            context = RelationshipContext(
                user_id=user_id,
                relationship_id=relationship_id,
                relationship_stage=relationship_stage,
            )
            await self.store.save_relationship_context(context)
            return context
        if relationship_stage != RelationshipStage.UNKNOWN and (
            context.relationship_stage != relationship_stage
        ):
            context.relationship_stage = relationship_stage
            await self.store.save_relationship_context(context)
        return context

    async def _project_relationship_stage(
        self,
        user_id: str,
        relationship_id: str,
        memories: list[MemoryItem],
    ) -> RelationshipStage | None:
        target_stage = _relationship_stage_from_memories(memories)
        if target_stage is None:
            return None
        context = await self.ensure_context(user_id, relationship_id)
        if context.relationship_stage in {
            RelationshipStage.DATING,
            RelationshipStage.STABLE_RELATIONSHIP,
            RelationshipStage.LONG_DISTANCE,
            RelationshipStage.BREAKUP,
        }:
            return None
        if context.relationship_stage == target_stage:
            return None
        updated = context.model_copy(update={"relationship_stage": target_stage})
        await self.store.save_relationship_context(updated)
        return target_stage

    async def _reconcile_active_lifecycle(
        self,
        user_id: str,
        active_memories: list[MemoryItem],
    ) -> set[str]:
        stale_ids = legacy_transition_target_ids(active_memories)
        duplicate_ids = semantic_duplicate_ids(active_memories)
        reconciled_ids = stale_ids | duplicate_ids
        for memory_id in reconciled_ids:
            await self.store.set_memory_status(
                memory_id,
                user_id,
                MemoryStatus.SUPERSEDED,
            )
        return reconciled_ids

    async def _close_linked_action_intents(
        self,
        user_id: str,
        plan: RelationshipPlan,
        active_memories: list[MemoryItem],
    ) -> None:
        for memory in active_memories:
            if (
                memory.kind == MemoryKind.ACTION_INTENT
                and memory_references_plan(memory, plan)
            ):
                await self.store.set_memory_status(
                    memory.id,
                    user_id,
                    MemoryStatus.SUPERSEDED,
                )

    async def reconcile_relationship_plans(
        self,
        *,
        user_id: str,
        relationship_id: str,
        memories: list[MemoryItem] | None = None,
        plans: list[RelationshipPlan] | None = None,
    ) -> list[RelationshipPlan]:
        if memories is None:
            memories = await self.store.list_memories(
                user_id=user_id,
                relationship_id=relationship_id,
                limit=1000,
            )
        eligible_memories = [
            memory
            for memory in memories
            if memory.status in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
        ]
        if plans is None:
            plans = await self.store.sync_relationship_plans(
                user_id=user_id,
                relationship_id=relationship_id,
            )
        transitions = match_plan_transitions(
            eligible_memories,
            plans,
            infer_legacy_completion=True,
        )
        if not transitions:
            return plans
        for transition in transitions:
            event = eligible_memories[transition.candidate_index]
            transitioned = await self.store.set_relationship_plan_status(
                plan_id=transition.plan_id,
                user_id=user_id,
                status=transition.target_status,
                transitioned_at=(
                    event.occurred_at or event.period_end or event.updated_at
                ),
                source_event_memory_id=event.id,
            )
            if transitioned is not None:
                await self._close_linked_action_intents(
                    user_id,
                    transitioned,
                    eligible_memories,
                )
        return await self.store.sync_relationship_plans(
            user_id=user_id,
            relationship_id=relationship_id,
        )

    async def get_context(
        self,
        user_id: str,
        relationship_id: str,
        relationship_stage: RelationshipStage = RelationshipStage.UNKNOWN,
        *,
        query: str | None = None,
    ) -> RelationshipContext:
        context = await self.ensure_context(user_id, relationship_id, relationship_stage)
        memories = await self.store.list_memories(
            user_id=user_id,
            relationship_id=relationship_id,
            limit=200,
        )
        active = [
            item
            for item in memories
            if item.status in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
        ]
        reconciled_ids = await self._reconcile_active_lifecycle(user_id, active)
        if reconciled_ids:
            active = [item for item in active if item.id not in reconciled_ids]
        plans = await self.store.sync_relationship_plans(
            user_id=user_id,
            relationship_id=relationship_id,
        )
        plans = await self.reconcile_relationship_plans(
            user_id=user_id,
            relationship_id=relationship_id,
            memories=memories,
            plans=plans,
        )
        active = _filter_memories_for_plan_status(active, plans)
        context_time = self._clock()
        standardized_evidence = standardize_relationship_evidence(
            active,
            reference_time=context_time,
        )
        relationship_evidence = project_standardized_relationship_evidence(
            standardized_evidence,
            reference_time=context_time,
        )
        active_plans = [plan for plan in plans if plan.status in ACTIVE_PLAN_STATUSES]
        suppressed_plan_ids = (
            suppressed_plan_ids_for_text(query, active_plans) if query else set()
        )
        visible_plans = [
            plan for plan in active_plans if plan.plan_id not in suppressed_plan_ids
        ]
        visible_plan_memory_ids = {
            plan.source_memory_id
            for plan in visible_plans
            if plan.source_memory_id is not None
        }
        suppressed_plans = [
            plan for plan in active_plans if plan.plan_id in suppressed_plan_ids
        ]
        active = [
            item
            for item in active
            if (
                item.kind != MemoryKind.PLANNED_EVENT
                or item.id in visible_plan_memory_ids
            )
            and not (
                item.kind == MemoryKind.ACTION_INTENT
                and any(memory_references_plan(item, plan) for plan in suppressed_plans)
            )
        ]
        selected = select_context_memories(
            active,
            query=query,
            limit=self._context_limit,
            reference_time=context_time,
        )
        base = RelationshipContext(
            user_id=user_id,
            relationship_id=relationship_id,
            relationship_stage=context.relationship_stage,
        )
        return attach_memories(
            base,
            selected,
            active_plans=visible_plans,
            relationship_evidence=relationship_evidence,
            reference_time=context_time,
        )


class NoOpMemoryExtractor:
    async def extract(
        self,
        text: str,
        *,
        reference_time: datetime,
        existing_memories: list[MemoryItem],
        conversation_history: list[StoredMessage],
        trace: TraceRecorder | None = None,
        attempt_callback=None,
    ) -> AtomicExtraction:
        del text, reference_time, existing_memories, conversation_history, trace, attempt_callback
        return AtomicExtraction()


def atomize_candidates(candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
    atomic: list[MemoryCandidate] = []
    for candidate in candidates:
        preferences = candidate.payload.get("preference")
        if candidate.kind != MemoryKind.PREFERENCE or not isinstance(preferences, list):
            atomic.append(candidate)
            continue

        cleaned = [str(value).strip() for value in preferences if str(value).strip()]
        for index, preference in enumerate(cleaned):
            payload = {
                key: _select_parallel_value(value, index, len(cleaned))
                for key, value in candidate.payload.items()
            }
            payload["preference"] = preference
            preference_type = str(payload.get("preference_type") or "").casefold()
            atomic.append(
                candidate.model_copy(
                    update={
                        "summary": _atomic_preference_summary(
                            candidate.subject,
                            preference,
                            preference_type,
                        ),
                        "payload": payload,
                        "supersedes_id": candidate.supersedes_id if index == 0 else None,
                    }
                )
            )
    return atomic


def _select_parallel_value(value: object, index: int, expected_length: int) -> object:
    if isinstance(value, list) and len(value) == expected_length:
        return value[index]
    return value


def _atomic_preference_summary(subject: str, preference: str, preference_type: str) -> str:
    label = {
        "user": "用户",
        "partner": "对方",
        "relationship": "双方",
    }.get(subject.casefold(), subject)
    if preference.startswith(("喜欢", "不喜欢", "偏好", "避免", "不吃", "不能")):
        return f"{label}{preference}"
    if preference_type in {"dislike", "avoid"}:
        return f"{label}不喜欢{preference}"
    if preference_type in {"restriction", "allergy"}:
        return f"{label}需要避开{preference}"
    if preference_type == "date":
        return f"约会安排偏好：{preference}"
    return f"{label}喜欢{preference}"


def _memory_keeper_rank(item: MemoryItem) -> tuple[int, int, float, datetime]:
    return (
        int(item.status == MemoryStatus.CONFIRMED),
        item.importance,
        item.confidence,
        item.updated_at,
    )


def _supports_keyword(callable_object: object, keyword: str) -> bool:
    try:
        parameters = inspect.signature(callable_object).parameters
    except (TypeError, ValueError):
        # Callable objects implemented in extensions may not expose a
        # signature; the current protocol supports the keyword.
        return True
    return keyword in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )


def _evaluate_gate(
    gate: MemoryGate,
    text: str,
    *,
    conversation_history: list[StoredMessage],
    existing_memories: list[MemoryItem],
) -> MemoryGateDecision:
    evaluate = gate.evaluate
    if _supports_keyword(evaluate, "conversation_history"):
        return evaluate(
            text,
            conversation_history=conversation_history,
            existing_memories=existing_memories,
        )
    return evaluate(text)


_RELATIONSHIP_STAGE_EVENT_PREDICATES = {
    "confession_succeeded",
    "confession_accepted",
    "relationship_started",
    "relationship_confirmed",
}


def _candidate_predicate(candidate: MemoryCandidate) -> str:
    predicate = candidate.payload.get("predicate")
    return predicate.strip().casefold() if isinstance(predicate, str) else ""


def _attach_plan_metadata(
    memories: list[MemoryItem],
    plans: list[RelationshipPlan],
) -> list[MemoryItem]:
    plans_by_source = {
        plan.source_memory_id: plan
        for plan in plans
        if plan.source_memory_id is not None
    }
    return [
        memory_with_plan(memory, plan)
        if (plan := plans_by_source.get(memory.id)) is not None
        else memory
        for memory in memories
    ]


def _filter_memories_for_plan_status(
    memories: list[MemoryItem],
    plans: list[RelationshipPlan],
) -> list[MemoryItem]:
    active_plan_memory_ids = {
        plan.source_memory_id
        for plan in plans
        if plan.status in ACTIVE_PLAN_STATUSES and plan.source_memory_id is not None
    }
    terminal_plans = [plan for plan in plans if plan.status not in ACTIVE_PLAN_STATUSES]
    return [
        memory
        for memory in memories
        if (
            memory.kind != MemoryKind.PLANNED_EVENT
            or memory.id in active_plan_memory_ids
        )
        and not (
            memory.kind == MemoryKind.ACTION_INTENT
            and any(memory_references_plan(memory, plan) for plan in terminal_plans)
        )
    ]


def _relationship_stage_from_memories(
    memories: list[MemoryItem],
) -> RelationshipStage | None:
    if any(
        _candidate_predicate(memory) in _RELATIONSHIP_STAGE_EVENT_PREDICATES
        and memory.confidence >= 0.8
        and memory.importance >= 4
        for memory in memories
    ):
        return RelationshipStage.DATING
    return None


def _consume_background_exception(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    try:
        task.exception()
    except Exception:
        return

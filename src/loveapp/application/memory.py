import asyncio
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from loveapp.domain.advice import RelationshipContext
from loveapp.domain.enums import RelationshipStage
from loveapp.domain.memory import (
    AdmissionDecision,
    AtomicExtraction,
    ClaimRelation,
    DiscardedSpan,
    EvidenceExplicitness,
    MemoryCandidate,
    MemoryCompactionGroup,
    MemoryCompactionResult,
    MemoryExtractionAttempt,
    MemoryExtractionRun,
    MemoryExtractionStatus,
    MemoryGateDecision,
    MemoryGateReason,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    MemoryValence,
    MessageRole,
    PredicateType,
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
    governed_state_identity,
    governed_state_value,
    legacy_transition_target_ids,
    memory_concept,
    normalize_memory_candidate,
    plan_memory_transitions,
    semantic_duplicate_ids,
)
from loveapp.domain.memory_predicates import CANONICAL_PREDICATES, normalize_predicate
from loveapp.domain.memory_verification import ClaimVerification
from loveapp.domain.memory_write import (
    MemoryAuditDraft,
    MemoryStatusUpdate,
    MemoryWriteBatch,
    MemoryWriteOperation,
    RelationshipPlanStatusUpdate,
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
from loveapp.ports.memory import MemoryExtractor, MemoryStore, StrongClaimVerifier
from loveapp.ports.observability import TraceRecorder

from .contextual_memory_updates import (
    may_contain_contextual_memory_update,
    resolve_contextual_memory_update,
)
from .memory_admission import (
    assess_memory_admission,
    build_admission_policies,
    interaction_pattern_has_frequency,
    interaction_pattern_has_multiple_evidence,
)
from .memory_gate import MemoryGate
from .memory_relations import (
    ClaimRelationResolution,
    has_local_conflict,
    resolve_claim_relation,
)
from .relationship_events import (
    build_contextual_relationship_candidate,
    build_pending_confession_candidate,
    may_contain_contextual_relationship_event,
    resolve_contextual_relationship_event,
)


@dataclass(frozen=True)
class _CandidateObservation:
    candidate_index: int
    alias_hit: bool
    admission_reason: str
    score_breakdown: dict[str, object]
    compared_memory_ids: tuple[str, ...]
    strong_called: bool
    strong_compared_memory_ids: tuple[str, ...]
    relation_target_memory_ids: tuple[str, ...]


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
        admission_policy_overrides: dict[str, dict[str, object]] | None = None,
        verifier: StrongClaimVerifier | None = None,
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
        self._admission_policies = build_admission_policies(admission_policy_overrides)
        self._verifier = verifier
        set_store_clock = getattr(store, "set_clock", None)
        if callable(set_store_clock):
            set_store_clock(clock)
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
        contextual_update_probe = may_contain_contextual_memory_update(text)
        preloaded_memories: list[MemoryItem] | None = None
        history_loaded_for_gate = conversation_history is not None
        if conversation_history is None and (
            contextual_probe or retrospective_probe or contextual_update_probe
        ):
            conversation_history = await self.get_conversation_history(
                message.user_id,
                message.relationship_id,
                message.conversation_id,
                exclude_message_id=message.id,
            )
            history_loaded_for_gate = True
        if contextual_probe or retrospective_probe or contextual_update_probe:
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
        gate_decision = gate_decision.model_copy(
            update={
                "contextual_probe": (
                    gate_decision.contextual_probe or contextual_update_probe
                ),
                "history_loaded_for_gate": history_loaded_for_gate,
            }
        )
        _record_gate_trace(trace, gate_decision)
        if contextual_update_probe and not gate_decision.should_extract:
            unresolved_contextual_update = resolve_contextual_memory_update(
                text,
                conversation_history or [],
                preloaded_memories or [],
            )
            if unresolved_contextual_update.update_type is not None:
                _record_contextual_update_trace(trace, unresolved_contextual_update)
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
        contextual_update = resolve_contextual_memory_update(
            text,
            conversation_history,
            active,
        )
        if contextual_update.update_type is not None:
            _record_contextual_update_trace(trace, contextual_update)
        attempts: list[MemoryExtractionAttempt] = []
        extraction_failure: Exception | None = None
        if (
            contextual_update.resolved
            and gate_decision.reason == MemoryGateReason.CONTEXTUAL_UPDATE
        ):
            # The current turn only qualifies a known fact.  Invoking the
            # extractor here would invite it to turn the attached consultation
            # into a partner-state claim.
            extraction = AtomicExtraction()
        else:
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
                    error="memory extraction task was cancelled",
                )
                raise
            except Exception as exc:
                if not deterministic_candidates:
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
                extraction_failure = exc
                extraction = AtomicExtraction()

        result = RememberResult(
            message=message,
            extraction_error=(
                str(extraction_failure) if extraction_failure is not None else None
            ),
            discarded_spans=extraction.discarded_spans,
            gate_decision=gate_decision,
            extraction_run_id=extraction_run.id,
        )
        active_ids = {item.id for item in active}
        prepared: list[MemoryCandidate] = []
        prepared_statuses: list[MemoryStatus] = []
        relation_resolutions = []
        admission_breakdowns: list[dict[str, object]] = []
        candidate_observations: list[_CandidateObservation] = []
        audit_only: list[MemoryAuditDraft] = []
        extracted_candidates = [claim.to_candidate() for claim in extraction.claims]
        if deterministic_candidates:
            remaining_extracted = list(extracted_candidates)
            merged_deterministic: list[MemoryCandidate] = []
            for deterministic in deterministic_candidates:
                key = _candidate_governance_key(deterministic)
                match_index = next(
                    (
                        index
                        for index, candidate in enumerate(remaining_extracted)
                        if _candidate_governance_key(candidate) == key
                    ),
                    None,
                )
                if match_index is None:
                    merged_deterministic.append(deterministic)
                    continue
                extracted = remaining_extracted.pop(match_index)
                payload = dict(extracted.payload)
                payload.update(deterministic.payload)
                merged_deterministic.append(
                    deterministic.model_copy(
                        update={
                            "payload": payload,
                            "occurred_at": deterministic.occurred_at or extracted.occurred_at,
                            "period_start": deterministic.period_start or extracted.period_start,
                            "period_end": deterministic.period_end or extracted.period_end,
                        }
                    )
                )
            candidates = [*merged_deterministic, *remaining_extracted]
        else:
            candidates = extracted_candidates
        for candidate_index, candidate in enumerate(atomize_candidates(candidates)):
            predicate_normalization = normalize_predicate(
                kind=candidate.kind,
                raw_predicate=candidate.raw_predicate or candidate.payload.get("predicate"),
                canonical_predicate=candidate.canonical_predicate,
                custom_predicate=candidate.custom_predicate,
                predicate_type=candidate.predicate_type,
                payload=candidate.payload,
            )
            had_explicit_expiration = candidate.expires_at is not None
            candidate = add_plan_identity(
                normalize_memory_candidate(candidate, now),
                identity_scope=message.id,
            )
            admission_policy = self._admission_policies[candidate.kind]
            if (
                not had_explicit_expiration
                and admission_policy.default_ttl_days is not None
            ):
                candidate = candidate.model_copy(
                    update={
                        "expires_at": now
                        + timedelta(days=admission_policy.default_ttl_days)
                    }
                )
            if candidate.confidence < self._confidence_floor(candidate):
                result.skipped_low_confidence += 1
                _record_candidate_observation(
                    trace,
                    candidate_index=candidate_index,
                    candidate=candidate,
                    alias_hit=predicate_normalization.alias_hit,
                    admission_reason="below_service_confidence_floor",
                    score_breakdown={},
                    compared_memory_ids=[item.id for item in active],
                    strong_called=False,
                    strong_compared_memory_ids=[],
                    relation=ClaimRelation.UNRELATED,
                    relation_rule="not_evaluated",
                    relation_reason="Candidate did not reach typed admission.",
                    relation_target_memory_ids=[],
                    planned_action="skip_low_confidence",
                    planned_target_memory_ids=[],
                    target_operation_indexes=[],
                )
                continue
            updates: dict = {}
            if candidate.original_text != text:
                updates["original_text"] = text
            if candidate.supersedes_id:
                # Extractors may describe an intended replacement, but target
                # IDs are selected only by the local relation/lifecycle planner.
                updates["supersedes_id"] = None
            if updates:
                candidate = candidate.model_copy(update=updates)
            conflict = has_local_conflict(candidate, active)
            assessment = assess_memory_admission(
                candidate,
                text,
                conflict=conflict,
                policies=self._admission_policies,
            )
            decision = (
                AdmissionDecision.CONFIRM
                if status == MemoryStatus.CONFIRMED
                and assessment.decision != AdmissionDecision.REJECT
                else assessment.decision
            )
            incoming_status = (
                MemoryStatus.CONFIRMED
                if decision == AdmissionDecision.CONFIRM
                else MemoryStatus.PROPOSED
            )
            verification = None
            verification_error: str | None = None
            strong_called = False
            strong_compared_memory_ids: list[str] = []
            if decision == AdmissionDecision.STRONG_REVIEW and self._verifier is not None:
                strong_called = True
                try:
                    verification_memories = select_context_memories(
                        active,
                        query=text,
                        limit=8,
                        reference_time=now,
                    )
                    strong_compared_memory_ids = [
                        item.id for item in verification_memories
                    ]
                    verifier_allowed_ids = {
                        item.id for item in verification_memories
                    }
                    verification = await self._verifier.verify_claim(
                        text,
                        candidate=candidate,
                        existing_memories=verification_memories,
                        allowed_target_ids=verifier_allowed_ids,
                        trace=trace,
                    )
                    _validate_claim_verification(verification, verifier_allowed_ids)
                except Exception as exc:
                    verification_error = f"{type(exc).__name__}: {exc}"[:500]
                    verification = None
                if verification is not None:
                    payload = dict(candidate.payload)
                    canonical = verification.canonical_predicate
                    if canonical in CANONICAL_PREDICATES:
                        if verification.state_dimension is not None:
                            payload["state_dimension"] = verification.state_dimension
                        if verification.state_value is not None:
                            payload["state_value"] = verification.state_value
                        candidate = normalize_memory_candidate(
                            candidate.model_copy(
                                update={
                                    "payload": payload,
                                    "predicate_type": PredicateType.CANONICAL,
                                    "canonical_predicate": canonical,
                                    "custom_predicate": None,
                                    "state_dimension": verification.state_dimension,
                                    "state_value": verification.state_value,
                                    "verifier_model": verification.verifier_model,
                                }
                            ),
                            now,
                        )
                    elif verification.verifier_model:
                        candidate = candidate.model_copy(
                            update={"verifier_model": verification.verifier_model}
                        )
                    if (
                        verification.claim_supported
                        and verification.evidence_sufficient
                        and _verification_can_confirm(candidate)
                    ):
                        decision = AdmissionDecision.CONFIRM
                        incoming_status = MemoryStatus.CONFIRMED
                    elif (
                        candidate.kind == MemoryKind.RELATIONSHIP_STATE
                        and not verification.claim_supported
                    ):
                        decision = AdmissionDecision.REJECT
            resolution = resolve_claim_relation(
                candidate,
                active,
                incoming_status=incoming_status,
            )
            if verification_error is not None:
                resolution = ClaimRelationResolution(
                    relation=resolution.relation,
                    target_memory_ids=resolution.target_memory_ids,
                    rule_name="strong_verifier_fallback",
                    reason=f"Strong verifier failed validation: {verification_error}",
                )
            if (
                verification is not None
                and verification.claim_supported
                and verification.evidence_sufficient
                and verification.relation != ClaimRelation.UNCERTAIN
            ):
                verified_targets = tuple(
                    memory_id
                    for memory_id in verification.target_memory_ids
                    if memory_id in active_ids
                )
                verified_relation = verification.relation
                if (
                    incoming_status == MemoryStatus.PROPOSED
                    and verified_relation == ClaimRelation.UPDATE
                    and any(
                        active_by_id.status == MemoryStatus.CONFIRMED
                        for active_by_id in active
                        if active_by_id.id in verified_targets
                    )
                ):
                    verified_relation = ClaimRelation.CONTRADICTION
                resolution = ClaimRelationResolution(
                    relation=verified_relation,
                    target_memory_ids=verified_targets,
                    rule_name="strong_claim_verifier",
                    reason=verification.reason,
                )
            if decision == AdmissionDecision.REJECT:
                result.rejected_by_policy += 1
                rejection_breakdown = {
                    **assessment.score_breakdown,
                    **(
                        {"strong_verifier_error": verification_error}
                        if verification_error is not None
                        else {}
                    ),
                }
                audit_only.append(
                    MemoryAuditDraft(
                        candidate_index=candidate_index,
                        relation=resolution.relation,
                        decision=decision,
                        target_memory_ids=list(resolution.target_memory_ids),
                        rule_name="admission_policy",
                        admission_score=assessment.score,
                        score_breakdown=rejection_breakdown,
                        raw_predicate=candidate.raw_predicate,
                        canonical_predicate=candidate.canonical_predicate,
                        extractor_model=candidate.extractor_model,
                        verifier_model=candidate.verifier_model,
                        prompt_version=candidate.prompt_version,
                        evidence=candidate.evidence_spans,
                        reason=assessment.reason,
                    )
                )
                _record_candidate_observation(
                    trace,
                    candidate_index=candidate_index,
                    candidate=candidate.model_copy(
                        update={
                            "admission_score": assessment.score,
                            "admission_decision": decision,
                            "claim_relation": resolution.relation,
                        }
                    ),
                    alias_hit=predicate_normalization.alias_hit,
                    admission_reason=assessment.reason,
                    score_breakdown=rejection_breakdown,
                    compared_memory_ids=[item.id for item in active],
                    strong_called=strong_called,
                    strong_compared_memory_ids=strong_compared_memory_ids,
                    relation=resolution.relation,
                    relation_rule=resolution.rule_name,
                    relation_reason=resolution.reason,
                    relation_target_memory_ids=list(resolution.target_memory_ids),
                    planned_action="reject",
                    planned_target_memory_ids=[],
                    target_operation_indexes=[],
                )
                continue
            candidate = candidate.model_copy(
                update={
                    "admission_score": assessment.score,
                    "admission_decision": decision,
                    "claim_relation": resolution.relation,
                    "lifecycle_review_required": (
                        candidate.lifecycle_review_required
                        or decision == AdmissionDecision.STRONG_REVIEW
                        or resolution.relation
                        in {ClaimRelation.CONTRADICTION, ClaimRelation.UNCERTAIN}
                    ),
                }
            )
            prepared.append(candidate)
            prepared_statuses.append(incoming_status)
            relation_resolutions.append(resolution)
            admission_breakdown = dict(assessment.score_breakdown)
            if verification_error is not None:
                admission_breakdown["strong_verifier_error"] = verification_error
            admission_breakdowns.append(admission_breakdown)
            candidate_observations.append(
                _CandidateObservation(
                    candidate_index=candidate_index,
                    alias_hit=predicate_normalization.alias_hit,
                    admission_reason=assessment.reason,
                    score_breakdown=admission_breakdown,
                    compared_memory_ids=tuple(item.id for item in active),
                    strong_called=strong_called,
                    strong_compared_memory_ids=tuple(strong_compared_memory_ids),
                    relation_target_memory_ids=tuple(resolution.target_memory_ids),
                )
            )
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
        transition_plans = plan_memory_transitions(
            prepared,
            active,
            trigger_statuses=prepared_statuses,
        )
        lifecycle_by_candidate: dict[int, list] = {}
        for plan in transition_plans:
            lifecycle_by_candidate.setdefault(plan.trigger_index, []).append(plan)
        in_batch_state_targets = _plan_in_batch_state_transitions(
            prepared,
            prepared_statuses,
        )
        active_by_id = {item.id: item for item in active}
        operations: list[MemoryWriteOperation] = []
        for index, candidate in enumerate(prepared):
            resolution = relation_resolutions[index]
            relation = resolution.relation
            target_ids = list(resolution.target_memory_ids)
            rule_name = resolution.rule_name
            reason = resolution.reason
            lifecycle_rule_names: list[str] = []
            lifecycle_targets: list[str] = []
            for lifecycle in lifecycle_by_candidate.get(index, []):
                eligible_targets = [
                    memory_id
                    for memory_id in lifecycle.target_ids
                    if (
                        prepared_statuses[index] == MemoryStatus.CONFIRMED
                        or active_by_id[memory_id].status == MemoryStatus.PROPOSED
                    )
                ]
                if eligible_targets:
                    lifecycle_targets.extend(eligible_targets)
                    lifecycle_rule_names.append(lifecycle.rule_name)
            target_operation_indexes = in_batch_state_targets.get(index, [])
            if lifecycle_targets or target_operation_indexes:
                relation = ClaimRelation.UPDATE
                target_ids = list(dict.fromkeys([*target_ids, *lifecycle_targets]))
                rule_names = [*lifecycle_rule_names]
                if target_operation_indexes:
                    rule_names.append("same_state_dimension_in_batch")
                rule_name = "+".join(dict.fromkeys(rule_names))
                reason = "A deterministic lifecycle transition closes older working state."
            elif _has_in_batch_state_conflict(index, prepared, prepared_statuses):
                relation = ClaimRelation.CONTRADICTION
                target_ids = []
                target_operation_indexes = []
                rule_name = "proposed_state_conflict_in_batch"
                reason = "An unconfirmed in-batch state cannot replace a confirmed value."
            else:
                target_operation_indexes = []
            if relation != ClaimRelation.UPDATE:
                target_ids = []
            updated_candidate = candidate.model_copy(
                update={
                    "claim_relation": relation,
                    "lifecycle_review_required": (
                        candidate.lifecycle_review_required
                        or relation in {ClaimRelation.CONTRADICTION, ClaimRelation.UNCERTAIN}
                    ),
                }
            )
            prepared[index] = updated_candidate
            operations.append(
                MemoryWriteOperation(
                    candidate=updated_candidate,
                    status=prepared_statuses[index],
                    relation=relation,
                    target_memory_ids=target_ids,
                    target_operation_indexes=target_operation_indexes,
                    rule_name=rule_name,
                    reason=reason,
                    score_breakdown=admission_breakdowns[index],
                )
            )
            observation = candidate_observations[index]
            _record_candidate_observation(
                trace,
                candidate_index=observation.candidate_index,
                candidate=updated_candidate,
                alias_hit=observation.alias_hit,
                admission_reason=observation.admission_reason,
                score_breakdown=observation.score_breakdown,
                compared_memory_ids=list(observation.compared_memory_ids),
                strong_called=observation.strong_called,
                strong_compared_memory_ids=list(
                    observation.strong_compared_memory_ids
                ),
                relation=relation,
                relation_rule=rule_name,
                relation_reason=reason,
                relation_target_memory_ids=list(
                    observation.relation_target_memory_ids
                ),
                planned_action=_planned_memory_action(relation),
                planned_target_memory_ids=target_ids,
                target_operation_indexes=target_operation_indexes,
            )
        status_updates: list[MemoryStatusUpdate] = []
        plan_updates: list[RelationshipPlanStatusUpdate] = []
        contextual_updates = (
            [contextual_update.to_update(reference_time=now)]
            if contextual_update.resolved
            else []
        )
        for transition in plan_transitions:
            candidate = prepared[transition.candidate_index]
            plan_updates.append(
                RelationshipPlanStatusUpdate(
                    plan_id=transition.plan_id,
                    status=transition.target_status,
                    candidate_index=transition.candidate_index,
                    transitioned_at=candidate.occurred_at or self._clock(),
                )
            )
            if transition.target_status in {
                PlanStatus.COMPLETED,
                PlanStatus.CANCELLED,
                PlanStatus.EXPIRED,
            }:
                matched_plan = plans_by_id.get(transition.plan_id)
                if matched_plan is not None:
                    for memory in active:
                        if (
                            memory.kind == MemoryKind.ACTION_INTENT
                            and memory_references_plan(memory, matched_plan)
                        ):
                            status_updates.append(
                                MemoryStatusUpdate(
                                    memory_id=memory.id,
                                    status=MemoryStatus.SUPERSEDED,
                                    rule_name="close_linked_action_intent",
                                    reason="The linked relationship plan reached a terminal state.",
                                )
                            )
        try:
            prepared_saved = []
            if operations or audit_only or contextual_updates:
                committed = await self.store.commit_memory_batch(
                    user_id=message.user_id,
                    relationship_id=message.relationship_id,
                    batch=MemoryWriteBatch(
                        source_message_id=message.id,
                        operations=operations,
                        contextual_updates=contextual_updates,
                        status_updates=status_updates,
                        plan_updates=plan_updates,
                        audit_only=audit_only,
                    ),
                )
                prepared_saved = committed.saved
                result.saved.extend(prepared_saved)
                result.contextual_updated_memory_ids.extend(committed.updated_memory_ids)
                await self._project_relationship_stage(
                    message.user_id,
                    message.relationship_id,
                    [saved.item for saved in result.saved],
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
            (
                MemoryExtractionStatus.FAILED
                if extraction_failure is not None
                else MemoryExtractionStatus.COMPLETED
            ),
            attempts=attempts,
            saved_memory_ids=[saved.item.id for saved in result.saved],
            discarded_spans=extraction.discarded_spans,
            error=str(extraction_failure) if extraction_failure is not None else None,
        )
        if extraction_failure is not None and raise_on_extraction_error:
            raise extraction_failure
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
                    details["matched_rule"] = result.gate_decision.matched_rule
                    details["matched_span"] = result.gate_decision.matched_span
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


def _planned_memory_action(relation: ClaimRelation) -> str:
    if relation == ClaimRelation.SAME:
        return "merge"
    if relation == ClaimRelation.UPDATE:
        return "replace"
    return "add"


def _record_candidate_observation(
    trace: TraceRecorder | None,
    *,
    candidate_index: int,
    candidate: MemoryCandidate,
    alias_hit: bool,
    admission_reason: str,
    score_breakdown: dict[str, object],
    compared_memory_ids: list[str],
    strong_called: bool,
    strong_compared_memory_ids: list[str],
    relation: ClaimRelation,
    relation_rule: str,
    relation_reason: str,
    relation_target_memory_ids: list[str],
    planned_action: str,
    planned_target_memory_ids: list[str],
    target_operation_indexes: list[int],
) -> None:
    if trace is None:
        return
    action_targets = (
        relation_target_memory_ids if planned_action == "merge" else planned_target_memory_ids
    )
    actions: list[dict[str, object]] = [
        {
            "action": planned_action,
            "target_memory_ids": action_targets,
            "target_operation_indexes": target_operation_indexes,
        }
    ]
    if candidate.expires_at is not None and planned_action not in {
        "reject",
        "skip_low_confidence",
    }:
        actions.append(
            {
                "action": "schedule_expiration",
                "expires_at": candidate.expires_at.isoformat(),
            }
        )
    planned_status = None
    if candidate.admission_decision is not None:
        planned_status = (
            MemoryStatus.CONFIRMED.value
            if candidate.admission_decision == AdmissionDecision.CONFIRM
            else MemoryStatus.PROPOSED.value
        )
    family = candidate.payload.get("canonical_concept")
    with trace.measure("memory_candidate_governance") as details:
        details.update(
            {
                "candidate_index": candidate_index,
                "memory_kind": candidate.kind.value,
                "summary": candidate.summary,
                "raw_predicate": candidate.raw_predicate,
                "predicate_type": candidate.predicate_type.value,
                "canonical_predicate": candidate.canonical_predicate,
                "custom_predicate": candidate.custom_predicate,
                "alias_hit": alias_hit,
                "predicate_family": family if isinstance(family, str) else None,
                "state_dimension": candidate.state_dimension,
                "state_value": candidate.state_value,
                "admission_decision": (
                    candidate.admission_decision.value
                    if candidate.admission_decision is not None
                    else None
                ),
                "admission_score": candidate.admission_score,
                "admission_reason": admission_reason,
                "score_breakdown_json": json.dumps(
                    score_breakdown,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ),
                "compared_memory_ids_json": json.dumps(compared_memory_ids),
                "strong_called": strong_called,
                "strong_compared_memory_ids_json": json.dumps(
                    strong_compared_memory_ids
                ),
                "claim_relation": relation.value,
                "relation_rule": relation_rule,
                "relation_reason": relation_reason,
                "relation_target_memory_ids_json": json.dumps(
                    relation_target_memory_ids
                ),
                "planned_action": planned_action,
                "planned_status": planned_status,
                "planned_target_memory_ids_json": json.dumps(
                    planned_target_memory_ids
                ),
                "target_operation_indexes_json": json.dumps(
                    target_operation_indexes
                ),
                "planned_actions_json": json.dumps(
                    actions,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "expires_at": (
                    candidate.expires_at.isoformat()
                    if candidate.expires_at is not None
                    else None
                ),
            }
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


def _record_gate_trace(
    trace: TraceRecorder | None,
    decision: MemoryGateDecision,
) -> None:
    if trace is None:
        return
    with trace.measure("memory_gate") as details:
        details.update(
            {
                "gate_reason": decision.reason.value,
                "gate_should_extract": decision.should_extract,
                "matched_rule": decision.matched_rule,
                "matched_span": decision.matched_span,
                "contextual_probe": decision.contextual_probe,
                "history_loaded_for_gate": decision.history_loaded_for_gate,
                "antecedent_candidate_ids_json": json.dumps(
                    decision.antecedent_candidate_ids
                ),
                "selected_target_memory_id": decision.selected_target_memory_id,
                "target_guard_result": decision.target_guard_result,
                "contextual_update_type": decision.contextual_update_type,
            }
        )


def _record_contextual_update_trace(
    trace: TraceRecorder | None,
    resolution,
) -> None:
    if trace is None:
        return
    with trace.measure("memory_contextual_update") as details:
        details.update(
            {
                "contextual_update_probe": True,
                "antecedent_candidate_ids_json": json.dumps(
                    list(resolution.candidate_ids)
                ),
                "semantic_candidate_ids_json": json.dumps(
                    list(resolution.semantic_candidate_ids)
                ),
                "compatible_candidate_ids_json": json.dumps(
                    list(resolution.compatible_candidate_ids)
                ),
                "rejected_candidates_json": json.dumps(
                    [
                        {"memory_id": memory_id, "reason": reason}
                        for memory_id, reason in resolution.rejected_candidates
                    ]
                ),
                "plural_reference": resolution.plural_reference,
                "selected_target_memory_id": (
                    resolution.target.id if resolution.target is not None else None
                ),
                "target_guard_result": (
                    "compatible_active_target"
                    if resolution.resolved
                    else resolution.reason
                ),
                "contextual_update_type": (
                    resolution.update_type.value
                    if resolution.update_type is not None
                    else None
                ),
                "evidence_span": resolution.evidence_span,
                "reason": resolution.reason,
            }
        )


_RELATIONSHIP_STAGE_EVENT_PREDICATES = {
    "confession_succeeded",
    "confession_accepted",
    "relationship_started",
    "relationship_confirmed",
}


def _candidate_predicate(candidate: MemoryCandidate) -> str:
    predicate = candidate.payload.get("predicate")
    return predicate.strip().casefold() if isinstance(predicate, str) else ""


def _candidate_governance_key(candidate: MemoryCandidate) -> str:
    return memory_concept(candidate) or _candidate_predicate(candidate)


def _plan_in_batch_state_transitions(
    candidates: list[MemoryCandidate],
    statuses: list[MemoryStatus],
) -> dict[int, list[int]]:
    targets: dict[int, list[int]] = {}
    for index, candidate in enumerate(candidates):
        identity = governed_state_identity(candidate)
        value = governed_state_value(candidate)
        if identity is None or value is None:
            continue
        eligible: list[int] = []
        for previous_index, previous in enumerate(candidates[:index]):
            if (
                previous.subject.casefold() != candidate.subject.casefold()
                or governed_state_identity(previous) != identity
                or governed_state_value(previous) in {None, value}
            ):
                continue
            if (
                statuses[index] == MemoryStatus.CONFIRMED
                or statuses[previous_index] == MemoryStatus.PROPOSED
            ):
                eligible.append(previous_index)
        if eligible:
            targets[index] = eligible
    return targets


def _has_in_batch_state_conflict(
    index: int,
    candidates: list[MemoryCandidate],
    statuses: list[MemoryStatus],
) -> bool:
    candidate = candidates[index]
    identity = governed_state_identity(candidate)
    value = governed_state_value(candidate)
    if statuses[index] != MemoryStatus.PROPOSED or identity is None or value is None:
        return False
    return any(
        statuses[previous_index] == MemoryStatus.CONFIRMED
        and previous.subject.casefold() == candidate.subject.casefold()
        and governed_state_identity(previous) == identity
        and governed_state_value(previous) not in {None, value}
        for previous_index, previous in enumerate(candidates[:index])
    )


def _verification_can_confirm(candidate: MemoryCandidate) -> bool:
    if candidate.predicate_type == PredicateType.CUSTOM:
        return False
    if candidate.explicitness == EvidenceExplicitness.SPECULATIVE:
        return False
    if candidate.perspective != MemoryPerspective.USER_REPORTED:
        return False
    if candidate.kind in {MemoryKind.RELATIONSHIP_STATE, MemoryKind.STABLE_FACT}:
        return candidate.explicitness == EvidenceExplicitness.EXPLICIT
    if candidate.kind == MemoryKind.INTERACTION_PATTERN:
        return (
            candidate.explicitness == EvidenceExplicitness.EXPLICIT
            and (
                interaction_pattern_has_frequency(candidate)
                or interaction_pattern_has_multiple_evidence(candidate)
            )
        )
    return candidate.explicitness in {
        EvidenceExplicitness.EXPLICIT,
        EvidenceExplicitness.STRONGLY_IMPLIED,
    }


def _validate_claim_verification(
    verification: ClaimVerification,
    allowed_target_ids: set[str],
) -> None:
    invalid_targets = set(verification.target_memory_ids) - allowed_target_ids
    if invalid_targets:
        raise ValueError("claim verifier returned a target outside the candidate set")
    canonical = verification.canonical_predicate
    if canonical is None:
        return
    spec = CANONICAL_PREDICATES.get(canonical)
    if spec is None:
        raise ValueError("claim verifier returned an unregistered canonical predicate")
    if (
        verification.state_dimension is not None
        and verification.state_dimension != spec.state_dimension
    ):
        raise ValueError("claim verifier returned an incompatible state dimension")
    if spec.allowed_values and verification.state_value not in spec.allowed_values:
        raise ValueError("claim verifier returned an unsupported state value")


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

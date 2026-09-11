"""Optional two-stage memory extraction.

Stage 1 is deliberately recall-oriented and only emits proposition hints.
Stage 2 converts those hints into the existing :class:`AtomicExtraction`
contract.  Neither stage is allowed to authorize a database mutation; target
resolution and lifecycle governance remain downstream Python code.
"""

from __future__ import annotations

import inspect
import json
import re
from contextlib import nullcontext
from datetime import datetime
from time import perf_counter
from typing import Any

from openai import AsyncOpenAI
from pydantic import SecretStr, ValidationError

from loveapp.application.memory_repair import (
    MemoryResponseError,
    parse_memory_response,
)
from loveapp.application.memory_retrieval import HybridMemoryRetriever
from loveapp.domain.memory import (
    AtomicExtraction,
    CoarseExtraction,
    CoarseProposition,
    DiscardReason,
    EpistemicStatus,
    ExtractionEpistemicStatus,
    MemoryAttemptStatus,
    MemoryExtractionAttempt,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemorySemanticGateReason,
    OperationHint,
    PropositionOrigin,
    RelationHint,
    SemanticRole,
    StoredMessage,
    canonical_semantic_role,
)
from loveapp.domain.memory_dimensions import (
    INTERACTION_PATTERN_DIMENSIONS,
    detect_evidence_dimensions,
    normalize_interaction_metric,
    normalize_state_dimension,
    normalize_state_value,
)
from loveapp.domain.memory_semantic_units import (
    EnrichmentDraft,
    NewMemoryDraft,
    RefinementDraft,
    SemanticAtomicExtraction,
    SemanticUnitProvenance,
)
from loveapp.domain.runtime_context import ConversationContext, PendingMemoryContext
from loveapp.ports.memory import MemoryAttemptCallback, MemoryExtractor
from loveapp.ports.observability import TraceRecorder

from .openai_compatible import (
    _build_prompt,
    _capture_usage,
    _flush_attempts,
    _safe_model_response_snapshot,
)
from .stage2_routing import (
    CANONICAL_SEMANTIC_ROLES,
    SemanticRoleRouter,
    Stage2ExtractorRoute,
    Stage2PromptRoute,
    is_new_event_occurrence,
    route_instruction,
)

_TWO_STAGE_PROMPT_VERSION = "memory-semantic-ontology-v1.4"


class TwoStageMemoryExtractor:
    """Run one coarse and one batched detailed extraction call.

    ``fallback`` is intentionally a complete existing extractor (normally the
    Tiered Flash/Strong extractor).  It is used only when a two-stage call is
    unavailable or cannot be converted safely to the shared contract.
    """

    requires_semantic_gate_contract = True

    def __init__(
        self,
        *,
        api_key: SecretStr,
        base_url: str,
        model: str,
        timeout_seconds: float = 60,
        max_retries: int = 0,
        max_tokens: int = 4096,
        tier: str = "flash",
        thinking: str | None = None,
        fallback: MemoryExtractor | None = None,
        client: Any | None = None,
        candidate_retriever: HybridMemoryRetriever | None = None,
    ) -> None:
        self._model = model
        self._tier = tier
        self._max_tokens = max_tokens
        self._sdk_max_retries = max_retries
        self._thinking = thinking
        self._fallback = fallback
        self._role_router = SemanticRoleRouter()
        self._candidate_retriever = candidate_retriever or HybridMemoryRetriever()
        # The diagnostic snapshot is intentionally kept on the opt-in
        # extractor rather than persisted in the Memory domain.  Evaluation
        # harnesses can inspect it after each call without changing the Store
        # contract or leaking two-stage internals into production writes.
        self._last_diagnostic: dict[str, Any] = {}
        self._client = client or AsyncOpenAI(
            api_key=api_key.get_secret_value(),
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    @property
    def can_verify(self) -> bool:
        return bool(getattr(self._fallback, "can_verify", False))

    @property
    def verifier_model(self) -> str | None:
        return getattr(self._fallback, "verifier_model", None)

    @property
    def last_diagnostic(self) -> dict[str, Any]:
        """Return the most recent stage-level diagnostic snapshot."""

        return self._last_diagnostic

    async def verify_claim(self, *args: Any, **kwargs: Any) -> Any:
        verifier = getattr(self._fallback, "verify_claim", None)
        if verifier is None:
            raise RuntimeError("strong claim verifier is not configured")
        return await verifier(*args, **kwargs)

    async def extract(
        self,
        text: str,
        *,
        reference_time: datetime,
        existing_memories: list[MemoryItem],
        conversation_history: list[StoredMessage],
        pending_memory_context: PendingMemoryContext | None = None,
        conversation_context: ConversationContext | None = None,
        trace: TraceRecorder | None = None,
        attempt_callback: MemoryAttemptCallback | None = None,
    ) -> AtomicExtraction:
        conversation_context = conversation_context or ConversationContext.from_turn(
            text,
            conversation_history=conversation_history,
            pending_memory_context=pending_memory_context,
            relevant_memories=existing_memories,
        )
        attempts: list[MemoryExtractionAttempt] = []
        started = perf_counter()
        current_stage = "coarse"
        diagnostic: dict[str, Any] = {
            "stage1": {
                "called": False,
                "model": self._model,
                "raw_output": None,
                "parse_success": False,
                "validation_success": False,
                "propositions": [],
            },
            "stage2": {
                "called": False,
                "model": self._model,
                "raw_output": None,
                "parse_success": False,
                "validation_success": False,
                "claims": [],
                "semantic_units": [],
                "routes": [],
            },
            "retrieval": {
                "called": False,
                "failed": False,
                "limit": 5,
                "candidate_ids": [],
                "candidates": [],
            },
            "fallback": {
                "triggered": False,
                "stage": None,
                "reason_code": None,
                "exception_type": None,
                "exception_message": None,
            },
            "final_extractor_used": None,
            "final_claims": [],
        }
        self._last_diagnostic = diagnostic
        try:
            coarse = await self._extract_coarse(
                text,
                reference_time=reference_time,
                existing_memories=existing_memories,
                conversation_history=conversation_history,
                pending_memory_context=pending_memory_context,
                conversation_context=conversation_context,
                trace=trace,
                attempts=attempts,
            )
            if not coarse.should_extract:
                extraction = AtomicExtraction(
                    should_extract=False,
                    gate_reason=_negative_gate_reason(coarse.gate_reason),
                )
            else:
                retrieved_memories = await self._retrieve_candidates(
                    text,
                    existing_memories,
                    reference_time=reference_time,
                )
                stage2_context = ConversationContext.from_turn(
                    text,
                    conversation_history=conversation_history,
                    pending_memory_context=pending_memory_context,
                    pending_questions=(
                        list(conversation_context.pending_questions)
                        if conversation_context is not None
                        else None
                    ),
                    active_topic=(
                        conversation_context.active_topic
                        if conversation_context is not None
                        else None
                    ),
                    relevant_memories=retrieved_memories,
                )
                current_stage = "detailed"
                extraction = await self._extract_detailed(
                    text,
                    reference_time=reference_time,
                    existing_memories=retrieved_memories,
                    conversation_history=conversation_history,
                    pending_memory_context=pending_memory_context,
                    conversation_context=stage2_context,
                    coarse=coarse,
                    trace=trace,
                    attempts=attempts,
                )
            _flush_attempts(attempts, attempt_callback)
            diagnostic["final_extractor_used"] = "two_stage_native"
            diagnostic["final_claims"] = [
                claim.model_dump(mode="json") for claim in extraction.claims
            ]
            diagnostic["final_semantic_units"] = [
                unit.model_dump(mode="json") for unit in getattr(extraction, "semantic_units", [])
            ]
            return extraction
        except Exception as exc:
            _flush_attempts(attempts, attempt_callback)
            failed_stage = next(
                (
                    attempt.stage
                    for attempt in reversed(attempts)
                    if attempt.status == MemoryAttemptStatus.FAILED
                ),
                None,
            )
            fallback_stage = str(failed_stage or current_stage or "unknown").casefold()
            diagnostic["fallback"] = {
                "triggered": True,
                "stage": fallback_stage,
                "reason_code": _fallback_reason_code(exc, stage=fallback_stage),
                "exception_type": type(exc).__name__,
                "exception_message": str(exc)[:1000],
            }
            fallback_attempts: list[MemoryExtractionAttempt] = []
            fallback, fallback_error = await self._fallback_extract(
                text,
                reference_time=reference_time,
                existing_memories=existing_memories,
                conversation_history=conversation_history,
                pending_memory_context=pending_memory_context,
                conversation_context=conversation_context,
                trace=trace,
                attempt_callback=fallback_attempts.append,
            )
            for attempt in fallback_attempts:
                if attempt_callback is not None:
                    attempt_callback(
                        attempt.model_copy(
                            update={
                                "extraction_strategy": "single_stage",
                                "stage": "fallback",
                                "fallback_used": True,
                            }
                        )
                    )
            if fallback_error is not None:
                diagnostic["fallback"]["exception_type"] = type(fallback_error).__name__
                diagnostic["fallback"]["exception_message"] = str(fallback_error)[:1000]
            if fallback is not None:
                diagnostic["final_extractor_used"] = "single_stage_fallback"
                diagnostic["final_claims"] = [
                    claim.model_dump(mode="json") for claim in fallback.claims
                ]
                return fallback
            if trace is not None:
                with trace.measure("memory_two_stage_failure") as details:
                    details["error"] = str(exc)[:1000]
                    details["elapsed_ms"] = (perf_counter() - started) * 1000
            return AtomicExtraction()

    async def _retrieve_candidates(
        self,
        text: str,
        existing_memories: list[MemoryItem],
        *,
        reference_time: datetime,
    ) -> list[MemoryItem]:
        """Provide a bounded, read-only candidate set to Stage 2.

        Retrieval is deliberately advisory.  The returned rows are only
        serialized into the Stage 2 context; target selection remains a
        downstream resolver responsibility.
        """

        try:
            retrieved = await self._candidate_retriever.retrieve(
                existing_memories,
                query=text,
                limit=5,
                reference_time=reference_time,
                preserve_candidates=True,
            )
        except Exception as exc:  # pragma: no cover - defensive provider boundary
            self._last_diagnostic["retrieval"] = {
                "called": True,
                "failed": True,
                "error": str(exc)[:500],
                "candidate_ids": [],
                "limit": 5,
            }
            return []
        self._last_diagnostic["retrieval"] = {
            "called": True,
            "failed": False,
            "limit": 5,
            "candidate_ids": [result.item.id for result in retrieved],
            "candidates": [
                {
                    "memory_id": result.item.id,
                    "rank": index,
                    "scores": result.score.as_dict(),
                }
                for index, result in enumerate(retrieved, start=1)
            ],
        }
        return [result.item for result in retrieved]

    async def _extract_coarse(
        self,
        text: str,
        *,
        reference_time: datetime,
        existing_memories: list[MemoryItem],
        conversation_history: list[StoredMessage],
        pending_memory_context: PendingMemoryContext | None,
        conversation_context: ConversationContext | None,
        trace: TraceRecorder | None,
        attempts: list[MemoryExtractionAttempt],
    ) -> CoarseExtraction:
        prompt = _build_coarse_prompt(
            text,
            reference_time=reference_time,
            existing_memories=existing_memories,
            conversation_history=conversation_history,
            pending_memory_context=pending_memory_context,
            conversation_context=conversation_context,
        )
        details: dict[str, Any] = {
            "model": self._model,
            "tier": self._tier,
            "stage": "coarse",
            "prompt_version": _TWO_STAGE_PROMPT_VERSION,
        }
        started = perf_counter()
        self._last_diagnostic["stage1"]["called"] = True
        try:
            content, usage = await self._call(prompt, _COARSE_SYSTEM_PROMPT, trace, details)
            self._last_diagnostic["stage1"]["raw_output"] = _safe_model_response_snapshot(content)
            _capture_usage(details, usage)
            self._last_diagnostic["stage1"].update(
                {
                    key: details[key]
                    for key in (
                        "prompt_tokens",
                        "completion_tokens",
                        "reasoning_tokens",
                        "total_tokens",
                        "finish_reason",
                    )
                    if key in details
                }
            )
            raw_payload = _parse_json_object(content)
            self._last_diagnostic["stage1"]["parse_success"] = True
            coarse_payload, repair_steps = _coerce_coarse_payload(raw_payload)
            coarse = CoarseExtraction.model_validate(coarse_payload)
            _validate_coarse_context(coarse, text, conversation_context)
            if coarse.should_extract and not coarse.propositions:
                raise MemoryResponseError(
                    "two-stage coarse output contains no propositions",
                    category="empty_propositions",
                )
            if repair_steps:
                details["repair_steps"] = "; ".join(repair_steps)
            self._last_diagnostic["stage1"]["validation_success"] = True
            self._last_diagnostic["stage1"]["propositions"] = [
                proposition.model_dump(mode="json") for proposition in coarse.propositions
            ]
            self._last_diagnostic["stage1"]["answered_pending_questions"] = (
                coarse.answered_pending_questions
            )
            details["proposition_count"] = len(coarse.propositions)
            details["should_extract"] = coarse.should_extract
            attempts.append(_build_attempt(details, started, status=MemoryAttemptStatus.COMPLETED))
            return coarse
        except Exception as exc:
            if "content" in locals():
                self._last_diagnostic["stage1"]["raw_output"] = _safe_model_response_snapshot(
                    content
                )
            details["failure_category"] = _failure_category(exc)
            details["error"] = str(exc)[:1000]
            attempts.append(
                _build_attempt(
                    details,
                    started,
                    status=MemoryAttemptStatus.FAILED,
                    error=str(exc),
                )
            )
            raise

    async def _extract_detailed(
        self,
        text: str,
        *,
        reference_time: datetime,
        existing_memories: list[MemoryItem],
        conversation_history: list[StoredMessage],
        pending_memory_context: PendingMemoryContext | None,
        conversation_context: ConversationContext | None,
        coarse: CoarseExtraction,
        trace: TraceRecorder | None,
        attempts: list[MemoryExtractionAttempt],
    ) -> AtomicExtraction:
        routes = self._role_router.route_many(coarse.propositions)
        route_payload = [
            _route_prompt_payload(route, proposition)
            for route, proposition in zip(routes, coarse.propositions, strict=False)
        ]
        self._last_diagnostic["stage2"]["routes"] = route_payload
        if routes and all(
            route.selected_route == Stage2ExtractorRoute.UNCERTAIN for route in routes
        ):
            self._last_diagnostic["stage2"]["abstentions"] = [
                {"proposition_id": route.proposition_id, "reason": _routing_failure(route)}
                for route in routes
            ]
            return SemanticAtomicExtraction(
                should_extract=coarse.should_extract, gate_reason=coarse.gate_reason
            )
        prompt = _build_detailed_prompt(
            text,
            reference_time=reference_time,
            existing_memories=existing_memories,
            conversation_history=conversation_history,
            pending_memory_context=pending_memory_context,
            conversation_context=conversation_context,
            coarse=coarse,
        )
        details: dict[str, Any] = {
            "model": self._model,
            "tier": self._tier,
            "stage": "detailed",
            "prompt_version": _TWO_STAGE_PROMPT_VERSION,
            "route_count": len(routes),
            "routes": [route.model_dump() for route in routes],
        }
        started = perf_counter()
        self._last_diagnostic["stage2"]["called"] = True
        try:
            content, usage = await self._call(
                prompt,
                _build_detailed_system_prompt(routes),
                trace,
                details,
            )
            self._last_diagnostic["stage2"]["raw_output"] = _safe_model_response_snapshot(content)
            _capture_usage(details, usage)
            self._last_diagnostic["stage2"].update(
                {
                    key: details[key]
                    for key in (
                        "prompt_tokens",
                        "completion_tokens",
                        "reasoning_tokens",
                        "total_tokens",
                        "finish_reason",
                    )
                    if key in details
                }
            )
            raw_model_payload = _parse_json_object(content)
            self._last_diagnostic["stage2"]["parse_success"] = True
            _reject_unsafe_detailed_keys(raw_model_payload)
            raw = _coerce_detailed_payload(raw_model_payload)
            raw = _filter_unroutable_units(raw, coarse, self._last_diagnostic["stage2"])
            extraction, repair_steps = _parse_detailed_extraction(
                raw,
                source_text=text,
                coarse=coarse,
            )
            _validate_stage2_role_contract(
                extraction,
                source_text=text,
                coarse=coarse,
            )
            if (
                coarse.should_extract
                and not extraction.semantic_units
                and not self._last_diagnostic["stage2"].get("abstentions")
            ):
                raise MemoryResponseError(
                    "two-stage detailed output contains no valid semantic units",
                    category="empty_claims",
                )
            self._last_diagnostic["stage2"]["validation_success"] = True
            self._last_diagnostic["stage2"]["claims"] = [
                claim.model_dump(mode="json") for claim in extraction.claims
            ]
            self._last_diagnostic["stage2"]["semantic_units"] = [
                unit.model_dump(mode="json") for unit in extraction.semantic_units
            ]
            details["claim_count"] = len(extraction.claims)
            details["semantic_unit_count"] = len(extraction.semantic_units)
            details["repair_steps"] = repair_steps or None
            attempts.append(_build_attempt(details, started, status=MemoryAttemptStatus.COMPLETED))
            return extraction
        except Exception as exc:
            if "content" in locals():
                self._last_diagnostic["stage2"]["raw_output"] = _safe_model_response_snapshot(
                    content
                )
            details["failure_category"] = _failure_category(exc)
            details["error"] = str(exc)[:1000]
            details["raw_model_response"] = _safe_model_response_snapshot(locals().get("content"))
            attempts.append(
                _build_attempt(
                    details,
                    started,
                    status=MemoryAttemptStatus.FAILED,
                    error=str(exc),
                )
            )
            raise

    async def _call(
        self,
        prompt: str,
        system_prompt: str,
        trace: TraceRecorder | None,
        details: dict[str, Any],
    ) -> tuple[str | None, Any]:
        measure = (
            trace.measure(f"memory_two_stage_{details['stage']}") if trace else nullcontext(details)
        )
        with measure as measured:
            measured.update(details)
            kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": self._max_tokens,
            }
            if self._thinking is not None:
                kwargs["extra_body"] = {"thinking": {"type": self._thinking}}
            completion = await self._client.chat.completions.create(**kwargs)
            choice = completion.choices[0]
            measured["finish_reason"] = getattr(choice, "finish_reason", None)
            return getattr(choice.message, "content", None), getattr(completion, "usage", None)

    async def _fallback_extract(
        self, text: str, **kwargs: Any
    ) -> tuple[AtomicExtraction | None, Exception | None]:
        if self._fallback is None:
            return None, None
        try:
            extract = self._fallback.extract
            if "conversation_context" in kwargs:
                try:
                    parameters = inspect.signature(extract).parameters
                except (TypeError, ValueError):
                    parameters = {}
                if (
                    parameters
                    and "conversation_context" not in parameters
                    and not any(
                        parameter.kind == inspect.Parameter.VAR_KEYWORD
                        for parameter in parameters.values()
                    )
                ):
                    kwargs.pop("conversation_context", None)
            return await extract(text, **kwargs), None
        except Exception as exc:
            return AtomicExtraction(), exc

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            await close()
        close_fallback = getattr(self._fallback, "aclose", None)
        if close_fallback is not None:
            await close_fallback()


def _build_coarse_prompt(
    text: str,
    *,
    reference_time: datetime,
    existing_memories: list[MemoryItem],
    conversation_history: list[StoredMessage],
    pending_memory_context: PendingMemoryContext | None,
    conversation_context: ConversationContext | None = None,
) -> str:
    context = json.loads(
        _build_prompt(
            text,
            reference_time,
            existing_memories,
            conversation_history,
            pending_memory_context,
            conversation_context=conversation_context,
        )
    )
    # Stage 1 sees semantic context, but not database identifiers.
    for item in context.get("existing_active_memories", []):
        item.pop("id", None)
    _align_pending_question_payload(context)
    context["stage"] = "coarse_proposition_extraction"
    context["contract"] = {
        "stage1_responsibilities": [
            "proposition_decomposition",
            "semantic_role_routing",
            "candidate_memory_kind_hint",
            "proposition_origin_classification",
            "relation_hint",
            "occurrence_grouping",
            "operation_hint",
            "answered_question_alignment",
            "attributes_hint",
        ],
        "stage1_must_not_decide": [
            "CREATE",
            "ENRICH",
            "UPDATE",
            "target_memory_id",
            "mutation_action",
        ],
        "candidate_kinds_are_hints": True,
        "semantic_units": {
            "description": "bounded semantic handoff; normalized to propositions downstream",
            "fields": [
                "proposition_id",
                "evidence_span",
                "semantic_role",
                "proposition_origin",
                "epistemic_status",
                "perspective",
                "occurrence_group",
                "same_occurrence_group",
                "relation_hint",
                "operation_hint",
                "candidate_kind",
                "candidate_kinds",
                "attributes_hint",
                "temporal_hint",
                "target_field_hint",
                "answered_questions",
                "answered_pending_questions",
                "confidence",
            ],
        },
        "proposition_origins": [
            "answer_to_question",
            "spontaneous_disclosure",
            "follow_up_detail",
            "new_occurrence",
            "uncertain",
        ],
        "context_is_read_only": True,
        "answered_questions": "open question IDs only; never memory IDs",
        "answered_pending_questions": "legacy alias for answered_questions",
        "relation_hints": [hint.value for hint in RelationHint],
        "operation_hints": [hint.value for hint in OperationHint],
        "semantic_roles": [role.value for role in CANONICAL_SEMANTIC_ROLES],
        "epistemic_statuses": [
            "observed",
            "reported",
            "believed",
            "inferred",
            "uncertain",
        ],
        "semantic_role_candidates_allowed": True,
        "forbidden_outputs": [
            "canonical_predicate",
            "target_memory_id",
            "target_memory_ids",
            "mutation_action",
            "db_patch",
        ],
    }
    return json.dumps(context, ensure_ascii=False)


def _build_detailed_prompt(
    text: str,
    *,
    reference_time: datetime,
    existing_memories: list[MemoryItem],
    conversation_history: list[StoredMessage],
    pending_memory_context: PendingMemoryContext | None,
    conversation_context: ConversationContext | None = None,
    coarse: CoarseExtraction,
) -> str:
    context = json.loads(
        _build_prompt(
            text,
            reference_time,
            existing_memories,
            conversation_history,
            pending_memory_context,
            conversation_context=conversation_context,
        )
    )
    for item in context.get("existing_active_memories", []):
        item.pop("id", None)
    _align_pending_question_payload(context)
    context.update(
        {
            "stage": "batched_kind_aware_detailed_extraction",
            "coarse_extraction": coarse.model_dump(mode="json"),
            "stage2_route_plan": _build_route_plan(coarse),
            "contract": {
                "output": "SemanticAtomicExtraction",
                "one_response_for_all_propositions": True,
                "stage2_routes": [route.value for route in Stage2ExtractorRoute],
                "semantic_unit_types": [
                    "new_memory",
                    "enrichment",
                    "refinement",
                ],
                "context_fields": [
                    "conversation_context",
                    "pending_questions",
                    "active_topic",
                    "relevant_memories",
                    "proposition_origin",
                    "epistemic_status",
                    "perspective",
                    "occurrence_group",
                    "same_occurrence_group",
                    "relation_hint",
                    "operation_hint",
                    "answered_questions",
                    "answered_pending_questions",
                ],
                "context_is_read_only": True,
                "forbidden_outputs": [
                    "target_memory_id",
                    "target_memory_ids",
                    "mutation_action",
                    "supersedes_id",
                    "db_patch",
                ],
            },
        }
    )
    return json.dumps(context, ensure_ascii=False)


def _route_prompt_payload(
    route: Stage2PromptRoute,
    proposition: Any,
) -> dict[str, Any]:
    """Serialize one role route without exposing storage authority."""

    return {
        **route.model_dump(),
        "evidence_span": proposition.evidence_span,
        "candidate_kinds": [
            kind.value if isinstance(kind, MemoryKind) else str(kind)
            for kind in proposition.candidate_kinds
        ],
        "subject_hint": proposition.subject_hint,
        "temporal_hint": proposition.temporal_hint,
        "target_field_hint": proposition.target_field_hint,
        "proposition_origin": getattr(
            proposition.proposition_origin, "value", proposition.proposition_origin
        ),
        "epistemic_status": getattr(
            proposition.epistemic_status, "value", proposition.epistemic_status
        ),
        "perspective": getattr(proposition, "perspective", None),
        "occurrence_group": getattr(
            proposition, "occurrence_group", getattr(proposition, "same_occurrence_group", None)
        ),
        "relation_hint": getattr(proposition.relation_hint, "value", proposition.relation_hint),
        "operation_hint": getattr(
            proposition.operation_hint, "value", proposition.operation_hint
        ),
        "answered_questions": list(
            getattr(proposition, "answered_questions", proposition.answered_pending_questions)
        ),
        "attributes_hint": list(getattr(proposition, "attributes_hint", [])),
        "instruction": route_instruction(route.selected_route),
    }


def _build_route_plan(coarse: CoarseExtraction) -> list[dict[str, Any]]:
    router = SemanticRoleRouter()
    routes = router.route_many(coarse.propositions)
    return [
        _route_prompt_payload(route, proposition)
        for route, proposition in zip(routes, coarse.propositions, strict=False)
    ]


def _build_detailed_system_prompt(
    routes: list[Stage2PromptRoute],
) -> str:
    """Add only the active role instructions to the shared Stage 2 contract.

    The network shape intentionally remains one batched detailed call.  The
    route block makes the reasoning task explicit without introducing a
    second extractor pipeline or granting write authority to the model.
    """

    active = list(dict.fromkeys(route.selected_route for route in routes))
    if not active:
        return _DETAILED_SYSTEM_PROMPT
    instructions = "\n".join(f"- {route.value}: {route_instruction(route)}" for route in active)
    return (
        f"{_DETAILED_SYSTEM_PROMPT}\n\n"
        "本次批次启用的 Stage2 semantic routes（仅用于选择语义草稿）：\n"
        f"{instructions}"
    )


def _proposition_for_unit(
    unit: NewMemoryDraft | EnrichmentDraft | RefinementDraft,
    coarse: CoarseExtraction,
) -> Any | None:
    """Match a detailed unit to its Stage 1 proposition by id or evidence."""

    unit_id = getattr(unit, "unit_id", None) or getattr(unit, "claim_id", None)
    propositions = list(coarse.propositions)
    if unit_id:
        exact = next(
            (item for item in propositions if item.proposition_id == str(unit_id)),
            None,
        )
        if exact is not None:
            return exact
    if isinstance(unit, NewMemoryDraft):
        evidence = list(unit.evidence_spans)
    else:
        evidence = [unit.evidence_span]
    matches = [
        proposition
        for proposition in propositions
        if any(
            span and (span in proposition.evidence_span or proposition.evidence_span in span)
            for span in evidence
        )
    ]
    return matches[0] if len(matches) == 1 else None


def _align_pending_question_payload(context: dict[str, Any]) -> None:
    for question in context.get("conversation_context", {}).get("pending_questions", []):
        if "id" in question:
            question["question_id"] = question.pop("id")


def _validate_coarse_context(
    coarse: CoarseExtraction,
    text: str,
    context: ConversationContext | None,
) -> None:
    ids = [item.proposition_id for item in coarse.propositions]
    if len(set(ids)) != len(ids) or any(
        item.evidence_span not in text for item in coarse.propositions
    ):
        raise MemoryResponseError(
            "Stage 1 requires unique proposition IDs and current-user evidence",
            category="schema_validation",
        )
    allowed = {
        question.id
        for question in (context.pending_questions if context else [])
        if question.status == "open"
    }
    answered = list(
        dict.fromkeys(
            [
                *coarse.answered_questions,
                *(qid for item in coarse.propositions for qid in item.answered_questions),
            ]
        )
    )
    if not set(answered) <= allowed:
        raise MemoryResponseError(
            "answered_pending_questions must reference open conversation questions",
            category="schema_validation",
        )
    coarse.answered_questions = answered
    coarse.answered_pending_questions = answered


def _routing_failure(route: Stage2PromptRoute) -> str:
    return "ROUTING_AMBIGUITY" if len(route.candidate_routes) > 1 else "ROUTING_ABSTENTION"


def _matched_propositions(
    unit: dict[str, Any],
    coarse: CoarseExtraction,
) -> list[CoarseProposition]:
    index = {item.proposition_id: item for item in coarse.propositions}
    evidence = unit.get("evidence_spans") or [unit.get("evidence_span")]
    if isinstance(evidence, str):
        evidence = [evidence]
    evidence_matches = [
        item
        for item in coarse.propositions
        if any(
            isinstance(span, str)
            and span
            and (span in item.evidence_span or item.evidence_span in span)
            for span in evidence
        )
    ]
    explicit = unit.get("proposition_ids")
    if explicit is not None:
        if (
            not isinstance(explicit, list)
            or not explicit
            or len(explicit) > 12
            or any(not isinstance(pid, str) or pid not in index for pid in explicit)
        ):
            raise MemoryResponseError(
                "invalid Stage 1 proposition references", category="schema_validation"
            )
        matches = [index[pid] for pid in dict.fromkeys(explicit)]
    else:
        pid = unit.get("proposition_id") or unit.get("claim_id") or unit.get("unit_id")
        matches = [index[pid]] if isinstance(pid, str) and pid in index else evidence_matches
    if any(item not in evidence_matches for item in matches):
        raise MemoryResponseError(
            "proposition reference does not match the unit's evidence",
            category="routing_abstention",
        )
    # A supplied ID must not hide another proposition present in the same
    # evidence, especially a weaker epistemic claim. Preserve all candidates.
    matches = evidence_matches
    if len(matches) > 1:
        groups = {item.occurrence_group for item in matches}
        if not all(groups) or len(groups) != 1:
            raise MemoryResponseError(
                "multiple propositions require one explicit same_occurrence_group",
                category="routing_ambiguity",
            )
        if (
            unit.get("semantic_type", "new_memory") != "new_memory"
            or unit.get("kind") != MemoryKind.INTERACTION_EVENT
            or any(
                item.candidate_kinds != [MemoryKind.INTERACTION_EVENT]
                or canonical_semantic_role(item.semantic_role) != SemanticRole.NEW_PROPOSITION
                for item in matches
            )
        ):
            raise MemoryResponseError(
                "occurrence grouping can only combine new event propositions",
                category="routing_abstention",
            )
    group = unit.get("occurrence_group", unit.get("same_occurrence_group"))
    if group and (not matches or any(item.occurrence_group != group for item in matches)):
        raise MemoryResponseError(
            "occurrence group differs from Stage 1", category="routing_abstention"
        )
    return matches


def _filter_unroutable_units(
    raw: dict[str, Any],
    coarse: CoarseExtraction,
    diagnostic: dict[str, Any],
) -> dict[str, Any]:
    router = SemanticRoleRouter()
    routes = {item.proposition_id: router.route(item) for item in coarse.propositions}
    key = "semantic_units" if "semantic_units" in raw else "claims"
    kept = []
    abstentions = [
        {"proposition_id": pid, "reason": _routing_failure(route)}
        for pid, route in routes.items()
        if route.selected_route == Stage2ExtractorRoute.UNCERTAIN
    ]
    for unit in raw.get(key, []):
        try:
            matches = _matched_propositions(unit, coarse)
        except MemoryResponseError as exc:
            if exc.category not in {"routing_ambiguity", "routing_abstention"}:
                raise
            abstentions.append(
                {
                    "unit_id": unit.get("unit_id") or unit.get("claim_id"),
                    "reason": exc.category.upper(),
                    "detail": str(exc),
                }
            )
            continue
        if not matches:
            abstentions.append({"proposition_id": None, "reason": "ROUTING_ABSTENTION"})
            continue
        if any(
            routes[item.proposition_id].selected_route == Stage2ExtractorRoute.UNCERTAIN
            for item in matches
        ):
            continue
        kept.append(unit)
    if abstentions:
        diagnostic["abstentions"] = abstentions
    return {**raw, key: kept}


def _inherit_proposition_semantics(unit: dict[str, Any], coarse: CoarseExtraction) -> None:
    matches = _matched_propositions(unit, coarse)
    # Provider provenance is not authoritative. Rebuild it from validated Stage 1.
    unit.pop("provenance", None)
    unit.pop("same_occurrence_group", None)
    unit.pop("occurrence_group", None)
    unit.pop("relation_hint", None)
    unit.pop("operation_hint", None)
    unit.pop("answered_questions", None)
    unit.pop("answered_pending_questions", None)
    unit.pop("proposition_ids", None)
    unit.pop("proposition_id", None)
    if not matches:
        return
    epistemics = [
        item.epistemic_status for item in matches if "epistemic_status" in item.model_fields_set
    ]
    detailed_epistemic = str(unit.get("epistemic_status") or "").casefold()
    if detailed_epistemic in {item.value for item in ExtractionEpistemicStatus}:
        epistemics.append(ExtractionEpistemicStatus(detailed_epistemic))
    # The weakest evidence mode must survive a grouped event and cannot be
    # upgraded by a more confident detailed response.
    priority = ["inferred", "believed", "uncertain", "reported", "observed"]
    epistemic = (
        min(epistemics, key=lambda value: priority.index(value.value)) if epistemics else None
    )
    roles = list(dict.fromkeys(canonical_semantic_role(item.semantic_role) for item in matches))
    provenance = SemanticUnitProvenance(
        proposition_ids=[item.proposition_id for item in matches],
        semantic_roles=roles,
        epistemic_status=epistemic,
        relation_hint=matches[0].relation_hint,
        operation_hint=matches[0].operation_hint,
        occurrence_group=matches[0].occurrence_group,
        answered_questions=list(
            dict.fromkeys(qid for item in matches for qid in item.answered_questions)
        ),
    )
    is_new = unit.get("semantic_type", "new_memory") == "new_memory"
    if is_new:
        unit.setdefault("payload", {})["extraction_provenance"] = provenance.model_dump(mode="json")
    else:
        unit["provenance"] = provenance.model_dump(mode="json")
    if unit.get("semantic_type") == "refinement":
        return  # Trace-only refinement has no admission/persistence authority.
    perspectives = {_storage_perspective(item.perspective) for item in matches}
    perspectives.add(_storage_perspective(unit.get("perspective")))
    if epistemic == ExtractionEpistemicStatus.INFERRED or "model_inferred" in perspectives:
        unit["perspective"] = "model_inferred"
        unit["epistemic_status"] = "hypothesis"
    elif epistemic == ExtractionEpistemicStatus.BELIEVED or "user_belief" in perspectives:
        unit["perspective"] = "user_belief"
        if unit.get("epistemic_status") not in {"prediction", "hypothesis"}:
            unit["epistemic_status"] = "uncertain"
    else:
        if unit.get("perspective") is not None:
            unit["perspective"] = _storage_perspective(unit["perspective"])
        if epistemic == ExtractionEpistemicStatus.UNCERTAIN:
            unit["epistemic_status"] = "uncertain"
        elif epistemic is not None:
            unit.setdefault("epistemic_status", _storage_epistemic_status(epistemic))
    if unit.get("epistemic_status") is not None:
        unit["epistemic_status"] = _storage_epistemic_status(unit["epistemic_status"])


def _storage_perspective(value: object) -> str | None:
    normalized = str(value or "").strip().casefold()
    return {
        "user": "user_reported",
        "reported": "user_reported",
        "observed": "user_reported",
        "belief": "user_belief",
        "believed": "user_belief",
        "model": "model_inferred",
        "inferred": "model_inferred",
    }.get(normalized, normalized or None)


def _validate_stage2_role_contract(
    extraction: SemanticAtomicExtraction,
    *,
    source_text: str,
    coarse: CoarseExtraction,
) -> None:
    """Apply bounded, safety-critical role checks after model parsing.

    Most role guidance remains advisory so a provider can recover from a
    coarse hint.  The new-event/enrichment boundary is different: accepting a
    clearly new occurrence as an enrichment could authorize a destructive
    downstream write, so that direction fails closed.
    """

    router = SemanticRoleRouter()
    for unit in extraction.semantic_units:
        proposition = _proposition_for_unit(unit, coarse)
        route = router.route(proposition) if proposition is not None else None
        selected = route.selected_route if route is not None else None
        evidence = (
            " ".join(unit.evidence_spans)
            if isinstance(unit, NewMemoryDraft)
            else getattr(unit, "evidence_span", "")
        )
        if isinstance(unit, EnrichmentDraft):
            # Use the proposition span first.  Looking at the entire compound
            # message would incorrectly reject a legitimate old-event
            # enrichment that happens to share a turn with a new proposition.
            occurrence_text = proposition.evidence_span if proposition is not None else evidence
            if is_new_event_occurrence(occurrence_text, evidence):
                raise MemoryResponseError(
                    "new bounded Event occurrence cannot be an enrichment",
                    category="semantic_role_mismatch",
                )
            if selected in {
                Stage2ExtractorRoute.STATE,
                Stage2ExtractorRoute.PATTERN,
                Stage2ExtractorRoute.BELIEF,
                Stage2ExtractorRoute.REFINEMENT,
            }:
                raise MemoryResponseError(
                    "detailed semantic unit does not match its Stage 1 role",
                    category="semantic_role_mismatch",
                )
            if (
                selected == Stage2ExtractorRoute.EVENT_ENRICHMENT
                and route is not None
                and unit.target_kind != MemoryKind.INTERACTION_EVENT
            ):
                raise MemoryResponseError(
                    "event enrichment route must target an interaction_event hint",
                    category="semantic_role_mismatch",
                )
        if isinstance(unit, RefinementDraft) and selected not in {
            Stage2ExtractorRoute.REFINEMENT,
            Stage2ExtractorRoute.UNCERTAIN,
            None,
        }:
            raise MemoryResponseError(
                "detailed refinement unit does not match its Stage 1 role",
                category="semantic_role_mismatch",
            )
        if isinstance(unit, NewMemoryDraft) and proposition is not None:
            # ``state_assertion`` is the legacy Stage-1 spelling.  It routes
            # to the State prompt for compatibility, but older providers used
            # it as a broad hint for ordinary stable facts (for example a
            # residence claim).  Keep that bounded legacy behavior parseable;
            # the stricter relationship_state requirement applies to the new
            # explicit ``state_update`` role.
            legacy_state_hint = SemanticRole.STATE_ASSERTION in {
                role
                for role in (
                    getattr(proposition, "semantic_role", None),
                    *(getattr(proposition, "semantic_role_candidates", []) or []),
                )
                if isinstance(role, SemanticRole)
            }
            if (
                selected == Stage2ExtractorRoute.STATE
                and unit.kind != MemoryKind.RELATIONSHIP_STATE
                and not legacy_state_hint
            ):
                raise MemoryResponseError(
                    "state route must emit a relationship_state draft",
                    category="semantic_role_mismatch",
                )
            if (
                selected == Stage2ExtractorRoute.PATTERN
                and unit.kind != MemoryKind.INTERACTION_PATTERN
            ):
                raise MemoryResponseError(
                    "pattern route must emit an interaction_pattern draft",
                    category="semantic_role_mismatch",
                )
            if selected == Stage2ExtractorRoute.PATTERN:
                metric = normalize_interaction_metric(unit.payload.get("metric"))
                if metric not in INTERACTION_PATTERN_DIMENSIONS:
                    raise MemoryResponseError(
                        "pattern route requires a registered metric",
                        category="semantic_validation",
                    )
            if (
                selected == Stage2ExtractorRoute.BELIEF
                and unit.perspective != MemoryPerspective.USER_BELIEF
            ):
                raise MemoryResponseError(
                    "belief route must retain user_belief perspective",
                    category="semantic_role_mismatch",
                )
            if selected == Stage2ExtractorRoute.EVENT_ENRICHMENT:
                raise MemoryResponseError(
                    "event enrichment route must emit an enrichment draft",
                    category="semantic_role_mismatch",
                )


_COARSE_SYSTEM_PROMPT = """
你是 LoveApp Memory V1.4 的 Stage 1 语义分解器。只输出一个 JSON 对象：
{"should_extract": true, "gate_reason": "STABLE_FACT", "propositions": [], "discarded_spans": []}
gate_reason 必须严格使用以下大写枚举之一：STABLE_FACT、PREFERENCE、INTERACTION_PATTERN、
RELATIONSHIP_STATE、RELATIONSHIP_CHANGE、PARTIAL_CHANGE、USER_BELIEF、PLANNED_EVENT、
ACTION_INTENT、ADVICE_OUTCOME、COMPOUND_MEMORY、CONTEXT_DEPENDENT_REPLY、TRANSIENT、
SMALL_TALK、NO_MEMORY；不要填写解释句。
每个 proposition 必须严格包含 proposition_id、evidence_span、candidate_kinds、semantic_role；
candidate_kinds 只能使用 stable_fact、preference、interaction_event、interaction_pattern、
advice_outcome、planned_event、action_intent、relationship_state；允许多个候选。
semantic_role 只能是 new_proposition、attribute_completion、refinement、correction、uncertain。
role 只表示操作；candidate_kind(s) 表示内容类型；epistemic_status 表示证据性质，三者正交。
epistemic_status 使用 observed、reported、believed、inferred、uncertain；perspective 可用 USER，
表示信息报告者，不替代 subject_hint。用户猜测必须标 believed，系统推断标 inferred。
“我俩正在吵架”是 new_proposition + interaction_event；当前冲突由后续 State Projection 消费事件。
“最近都是我主动联系她”是 new_proposition + interaction_pattern。
“我感觉她可能不喜欢我”是 new_proposition + relationship_state + believed，不是客观事实。
correction 表示明确纠正此前说法；普通随时间变化仍是 new_proposition。refinement 仅表示更具体。
可选填写
semantic_role_candidates/semantic_roles 保留多个语义假设；Stage 1 绝不输出 CREATE、ENRICH、UPDATE、
target_memory_id 或任何数据库写入指令。
先判断当前文本是否描述新的、有边界的 occurrence；“今天/又/再次/这次”出现时仍需结合
完整事件语义判断，新的 occurrence 必须是 new_proposition。只有对已有事件属性的省略式补充才是
attribute_completion。看到“因为”不能直接判为 completion。
必须结合 recent_conversation：Assistant 刚问“为什么吵架/当时什么反应”，用户回答原因或情绪，
即使句子很短也应 should_extract=true，并判 attribute_completion。已有“昨天我们吵架了”时，
“因为我迟到，她特别生气，现在已经不理我了”至少拆成旧 Event 的 cause、emotion completion，
以及独立 relationship_state。已有“我住上海”时，“具体在浦东”是 refinement，不是 Event completion。
严禁输出 canonical_predicate、target_memory_id、mutation_action、数据库 patch 或任何写入指令。
可用 temporal_hint 对象/文本、target_field_hint 列表、attributes_hint。
answered_pending_questions 只填写 context 中 open question_id，可在根或对应命题内返回。
同一事件下多个命题使用相同 same_occurrence_group；不同日期的 occurrence 不可分在同组。
例如“昨天我压力很大，她陪我聊了两个小时”可拆成同一 group 的背景和行为供 Stage2 组合。
""".strip()


_DETAILED_SYSTEM_PROMPT = """
你是 LoveApp Memory V1.4 的 Stage 2 结构化提取器。只输出一个 JSON 对象：
{"semantic_units": [], "discarded_spans": []}
一次响应处理全部 Stage 1 propositions。semantic_units 只能包含以下三种对象：
1) new_memory：必须包含 semantic_type、claim_id、kind、subject、raw_predicate、predicate、
summary、evidence_spans、semantic_payload；表示新的独立命题。
2) enrichment：必须包含 semantic_type、unit_id、target_kind=interaction_event、
target_semantic_hint、attribute_namespace、attribute_name、value、evidence_span、confidence；
只表示 conflict/date/shared_activity 旧事件的属性补充。
3) refinement：必须包含 semantic_type、unit_id、target_kind、target_semantic_hint、
raw_predicate、value、evidence_span、confidence；只表示精度提升，本轮不会授权写入。
kind 只能使用 stable_fact、preference、interaction_event、interaction_pattern、advice_outcome、
planned_event、action_intent、relationship_state。subject 只能使用 user、partner、relationship、
other_person。summary 必须使用简体中文。evidence_spans/evidence_span 必须逐字来自当前
user_message。new_memory 的 semantic_payload 必须保留事件 value/object/attributes。
Event canonical attribute 仅有 cause、severity、emotion、resolution、outcome、location、
activity_type；无法映射且明确属于旧事件的 weather 才可使用 custom namespace。
新的 bounded occurrence 必须输出 new_memory，绝不能输出 enrichment。
使用 stage2_route_plan 的 role × kind × epistemic 组合，而非仅按 role 路由。
new_proposition 与 correction 输出 new_memory，后者保留更正后的新值，交给现有 correction 治理；
interaction_event 的 attribute_completion 输出 enrichment；非事件 refinement 输出 refinement。
每个 claim_id/unit_id 对齐 Stage1 proposition_id。只有相同非空 same_occurrence_group 的
事件命题才可合成一个 new_memory，并输出完整 proposition_ids 列表；不同 occurrence 不可合并。
同一 group 中的事件背景、动作和结果应组合为同一个事件草稿，保留全部证据和属性；
不要把同一 occurrence 的背景另存为一个独立事件。组合不产生任何数据库 target。
保留 epistemic_status 和 perspective，不得把 believed/inferred 升级成 confirmed 客观事实。
stable_fact 表示可核实事实，preference 表示持有者偏好；沿用已注册 preference
domain/dimension/value。
Event 保留 event_type，Pattern 保留 metric 和 source；user_reported 与 derived_from_events 不混用。
一个句子可同时输出多个单位，例如旧 conflict 的 cause、emotion completion 与新的 contact state
必须分开。已有 conflict 后的“因为我迟到了”输出 enrichment(cause)，Assistant 追问反应后的
“特别生气”输出 enrichment(emotion)；“今天我们又吵了一架”始终输出 new_memory。
Extraction 不负责确认数据库里是否存在 target：即使 existing_active_memories 为空，只要当前回答在
conversation 语义上明确补充旧 Event，也必须输出 enrichment draft，后续 resolver 会做 0-target
fail-closed。target_semantic_hint 必须是 JSON 对象，例如 {"event_type":"conflict"}，不能是字符串。
“现在已经不理我了”作为 relationship_state 时保留
semantic_payload.state_dimension=contact_availability 和 state_value=unavailable。
你只提供语义事实，不决定关系目标、生命周期或数据库操作。
严禁输出 target_memory_id、target_memory_ids、mutation_action、supersedes_id 或任意 DB patch；
canonical predicate 由后续 deterministic normalizer 治理，不能伪造未注册 canonical。
Stage 2 必须先遵循 user prompt 中的 stage2_route_plan，再选择 semantic unit。
NewMemoryExtractor 只产出 new_memory；EventEnrichmentExtractor 只产出 enrichment；StateExtractor
产出 relationship_state；PatternExtractor 产出带 metric 的 interaction_pattern；RefinementExtractor
只产出 refinement；BeliefExtractor 使用 user_belief/uncertain。候选 route 冲突时宁可不产出单位。
新时间标记 + 新 occurrence + event predicate 始终遵循 New Event Dominance，禁止 enrichment；
已有 active conflict 语境中的“说开/和解/解决”优先输出
enrichment(attribute_name=resolution,value=reconciled)，不要凭空创建 reconciliation event。
所有 route 都不得输出 target_memory_id、mutation_action 或数据库 patch。
""".strip()

# Keep the long-standing Chinese contract above intact while making the new
# context-aware hand-off explicit for providers that key on English field
# names.  This is guidance only: no line grants target or write authority.
_COARSE_SYSTEM_PROMPT += """

Context-aware Stage 1 contract:
- Read conversation_context as read-only context (previous messages, pending_questions,
  active_topic, and relevant_memories) to understand why the user says this now.
- Decompose the message into semantic_units/propositions; do not split solely on punctuation.
- Classify proposition_origin as one of answer_to_question, spontaneous_disclosure,
  follow_up_detail, new_occurrence, or uncertain.
- Add bounded relation_hint (none, same_event, cause, context, support, contrast, correction),
  occurrence_group for propositions from one real-world event, and operation_hint
  (new_like, enrich_like, refine_like, unknown). These are semantic hand-off hints only.
- Use answered_questions for open question IDs answered by this turn; it never identifies a
  memory row or a resolver target.
- Include bounded attributes_hint when useful. Never emit target IDs, CREATE/ENRICH/UPDATE,
  mutation actions, or database patches.
- If a short phrase answers a pending question, mark answer_to_question; without supporting
  context, remain uncertain rather than inventing an enrichment target.
""".strip()

_DETAILED_SYSTEM_PROMPT += """

Context-aware Stage 2 contract:
- Use conversation_context, pending_questions, active_topic, relevant_memories, and the
  proposition_origin supplied by Stage 1 as read-only semantic context.
- Treat occurrence_group, relation_hint, operation_hint, and answered_questions as validated
  Stage 1 metadata. Do not invent or replace them with target or mutation instructions.
- Preserve multiple semantic units from one turn. Context can explain an enrichment, but
  resolver code—not the model—selects any target and authorizes mutation.
- A new occurrence with its own time/event evidence is new_memory, not enrichment. A phrase
  answering a pending question may be an enrichment candidate only when the context supports it.
- Never emit target_memory_id(s), mutation_action, supersedes_id, or db_patch.
""".strip()


_COARSE_KIND_ALIASES: dict[str, MemoryKind] = {
    "fact": MemoryKind.STABLE_FACT,
    "personal_fact": MemoryKind.STABLE_FACT,
    "residence": MemoryKind.STABLE_FACT,
    "location": MemoryKind.STABLE_FACT,
    "residence_location": MemoryKind.STABLE_FACT,
    "location_detail": MemoryKind.STABLE_FACT,
    "life_event": MemoryKind.STABLE_FACT,
    "preference": MemoryKind.PREFERENCE,
    "personal_preference": MemoryKind.PREFERENCE,
    "dislike": MemoryKind.PREFERENCE,
    "event": MemoryKind.INTERACTION_EVENT,
    "relationship_event": MemoryKind.INTERACTION_EVENT,
    "conflict_event": MemoryKind.INTERACTION_EVENT,
    "conflict": MemoryKind.INTERACTION_EVENT,
    "causal_relation": MemoryKind.INTERACTION_EVENT,
    "context": MemoryKind.INTERACTION_EVENT,
    "reaction": MemoryKind.INTERACTION_EVENT,
    "emotion": MemoryKind.INTERACTION_EVENT,
    "emotion_state": MemoryKind.RELATIONSHIP_STATE,
    "user_state": MemoryKind.RELATIONSHIP_STATE,
    "relationship_state": MemoryKind.RELATIONSHIP_STATE,
    "relationship_status": MemoryKind.RELATIONSHIP_STATE,
    "current_state": MemoryKind.RELATIONSHIP_STATE,
    "ongoing_state": MemoryKind.RELATIONSHIP_STATE,
    "current_situation": MemoryKind.RELATIONSHIP_STATE,
    "behavior": MemoryKind.RELATIONSHIP_STATE,
    "third_party_behavior": MemoryKind.RELATIONSHIP_STATE,
    "observation": MemoryKind.INTERACTION_PATTERN,
    "relationship_pattern": MemoryKind.INTERACTION_PATTERN,
    "interaction_behavior": MemoryKind.INTERACTION_PATTERN,
    "temporal_state": MemoryKind.INTERACTION_PATTERN,
    "contact_frequency_change": MemoryKind.INTERACTION_PATTERN,
    "no_response_status": MemoryKind.RELATIONSHIP_STATE,
    "recurring_pattern": MemoryKind.INTERACTION_PATTERN,
    "interaction_pattern": MemoryKind.INTERACTION_PATTERN,
    "user_belief": MemoryKind.RELATIONSHIP_STATE,
    "inference": MemoryKind.RELATIONSHIP_STATE,
    "concern": MemoryKind.RELATIONSHIP_STATE,
}

_COARSE_GATE_ALIASES: dict[str, MemorySemanticGateReason] = {
    "FACT": MemorySemanticGateReason.STABLE_FACT,
    "PREFERENCE": MemorySemanticGateReason.PREFERENCE,
    "PATTERN": MemorySemanticGateReason.INTERACTION_PATTERN,
    "EVENT": MemorySemanticGateReason.COMPOUND_MEMORY,
    "STATE": MemorySemanticGateReason.RELATIONSHIP_STATE,
    "RELATIONSHIP": MemorySemanticGateReason.RELATIONSHIP_STATE,
    "BELIEF": MemorySemanticGateReason.USER_BELIEF,
    "NO_MEMORY": MemorySemanticGateReason.NO_MEMORY,
    "NO_DURABLE_SIGNAL": MemorySemanticGateReason.NO_MEMORY,
}


def _coerce_coarse_payload(value: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Repair bounded, non-authoritative Stage1 vocabulary drift.

    Flash models occasionally return descriptive gate text and proposition
    aliases (``text``, ``source_span`` or domain labels such as ``event``).
    These fields are only semantic hints, so normalizing them at this adapter
    boundary is safe; unknown values remain invalid and fail closed.
    """

    payload = dict(value)
    repairs: list[str] = []
    should_extract = bool(payload.get("should_extract"))
    raw_props = payload.get("propositions")
    # The context-aware Stage 1 contract calls these semantic_units.  Keep
    # the persisted/downstream spelling as propositions while accepting the
    # additive hand-off shape at this adapter boundary.
    if not isinstance(raw_props, list) and isinstance(payload.get("semantic_units"), list):
        raw_props = payload.pop("semantic_units")
        repairs.append("semantic_units_alias")
    if not isinstance(raw_props, list):
        raw_props = []
        payload["propositions"] = raw_props
        repairs.append("propositions_defaulted")

    normalized_props: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_props, 1):
        if not isinstance(raw, dict):
            raise MemoryResponseError(
                f"two-stage coarse proposition {index} is not an object",
                category="schema_validation",
            )
        item = dict(raw)
        if "proposition_id" not in item and item.get("id") is not None:
            item["proposition_id"] = str(item["id"])
            repairs.append("proposition_id_alias")
        item.pop("id", None)
        if "proposition_id" not in item:
            item["proposition_id"] = f"p{index}"
            repairs.append("proposition_id_alias")
        if "evidence_span" not in item:
            evidence = item.get("source_span") or item.get("text")
            if isinstance(evidence, str) and evidence.strip():
                item["evidence_span"] = evidence.strip()
                repairs.append("evidence_span_alias")
        # Providers use both candidate_kinds and the Stage-1 contract's
        # singular candidate_memory_kind spelling. Normalize only the
        # bounded hint; it never authorizes a write.
        if "candidate_kinds" not in item:
            kind_hint = item.pop("candidate_kind", None)
            if kind_hint is None:
                kind_hint = item.pop("candidate_memory_kind", None)
            if kind_hint is not None:
                item["candidate_kinds"] = kind_hint if isinstance(kind_hint, list) else [kind_hint]
                repairs.append("candidate_memory_kind_alias")
        else:
            item.pop("candidate_memory_kind", None)
        if "candidate_kinds" in item and isinstance(item["candidate_kinds"], str):
            item["candidate_kinds"] = [item["candidate_kinds"]]
            repairs.append("candidate_kinds_scalar")
        if "candidate_kinds" in item and isinstance(item["candidate_kinds"], list):
            kinds: list[str] = []
            for raw_kind in item["candidate_kinds"]:
                normalized = _normalize_coarse_kind(raw_kind)
                if normalized is None:
                    raise MemoryResponseError(
                        f"unsupported two-stage candidate kind: {raw_kind}",
                        category="unsupported_enum",
                    )
                if normalized.value not in kinds:
                    kinds.append(normalized.value)
            if kinds:
                item["candidate_kinds"] = kinds
                valid_kind_values = {member.value for member in MemoryKind}
                raw_kind_values = raw.get("candidate_kinds", raw.get("candidate_memory_kind"))
                if isinstance(raw_kind_values, str):
                    raw_kind_values = [raw_kind_values]
                if isinstance(raw_kind_values, list) and any(
                    str(kind) not in valid_kind_values for kind in raw_kind_values
                ):
                    repairs.append("candidate_kind_alias")
        raw_role_candidates = None
        for alias in ("semantic_role_candidates", "semantic_roles", "role_candidates"):
            if alias in item:
                raw_role_candidates = item.pop(alias)
                if alias != "semantic_role_candidates":
                    repairs.append("semantic_role_candidates_alias")
                break
        if raw_role_candidates is not None:
            if not isinstance(raw_role_candidates, list):
                raise MemoryResponseError(
                    "two-stage semantic role candidates must be a list",
                    category="schema_validation",
                )
            normalized_roles: list[str] = []
            for raw_role in raw_role_candidates:
                normalized_role = _normalize_semantic_role(raw_role)
                if normalized_role is None:
                    raise MemoryResponseError(
                        f"unsupported two-stage semantic role: {raw_role}",
                        category="unsupported_enum",
                    )
                if normalized_role.value not in normalized_roles:
                    normalized_roles.append(normalized_role.value)
            item["semantic_role_candidates"] = normalized_roles
            if normalized_roles and "semantic_role" not in item:
                item["semantic_role"] = normalized_roles[0]
            repairs.append("semantic_role_candidates_normalized")
        if "semantic_role" not in item and item.get("role") is not None:
            item["semantic_role"] = item.pop("role")
            repairs.append("semantic_role_alias")
        else:
            item.pop("role", None)
        role = _normalize_semantic_role(item.get("semantic_role"))
        if role is not None:
            if item.get("semantic_role") != role.value:
                repairs.append("semantic_role_alias")
            item["semantic_role"] = role.value
        # Canonical semantic hand-off hints.  These remain bounded metadata;
        # they never authorize a resolver target or a storage mutation.
        if "occurrence_group" not in item and "same_occurrence_group" in item:
            item["occurrence_group"] = item["same_occurrence_group"]
            repairs.append("occurrence_group_alias")
        if "same_occurrence_group" not in item and "occurrence_group" in item:
            item["same_occurrence_group"] = item["occurrence_group"]
        if "answered_questions" not in item and "answered_pending_questions" in item:
            item["answered_questions"] = item["answered_pending_questions"]
            repairs.append("answered_questions_alias")
        if "answered_pending_questions" not in item and "answered_questions" in item:
            item["answered_pending_questions"] = item["answered_questions"]
        relation = _normalize_relation_hint(item.get("relation_hint"))
        if relation is None:
            relation = RelationHint.NONE
        item["relation_hint"] = relation.value
        operation = _normalize_operation_hint(item.get("operation_hint"))
        if operation is None:
            operation = _operation_hint_from_role(role)
        item["operation_hint"] = operation.value
        if not item.get("candidate_kinds"):
            role_value = str(item.get("semantic_role") or "").casefold()
            item["candidate_kinds"] = [
                MemoryKind.INTERACTION_EVENT.value
                if role_value
                in {
                    SemanticRole.ATTRIBUTE_COMPLETION.value,
                    SemanticRole.CONTEXTUAL_COMPLETION.value,
                    SemanticRole.NEW_PROPOSITION.value,
                }
                else MemoryKind.INTERACTION_PATTERN.value
                if role_value == SemanticRole.PATTERN_EXTRACTION.value
                else MemoryKind.RELATIONSHIP_STATE.value
                if role_value
                in {
                    SemanticRole.STATE_UPDATE.value,
                    SemanticRole.BELIEF_EXTRACTION.value,
                }
                else MemoryKind.STABLE_FACT.value
            ]
            repairs.append("candidate_kinds_defaulted_from_role")
        raw_epistemic = item.get("epistemic_status")
        if raw_epistemic is not None:
            normalized_epistemic = _normalize_extraction_epistemic_status(raw_epistemic)
            if normalized_epistemic is None:
                raise MemoryResponseError(
                    "unsupported extraction epistemic status", category="unsupported_enum"
                )
            item["epistemic_status"] = normalized_epistemic
        origin = _normalize_proposition_origin(item.get("proposition_origin", item.get("origin")))
        if origin is None:
            if item.get("proposition_origin") not in (None, ""):
                repairs.append("proposition_origin_defaulted")
            origin = PropositionOrigin.UNCERTAIN
        item["proposition_origin"] = origin.value
        item.pop("origin", None)
        attributes = item.get("attributes_hint", item.get("attribute_hints"))
        if attributes is None:
            attributes = item.get("attributes")
        if attributes is not None:
            if isinstance(attributes, str):
                attributes = [attributes]
            if isinstance(attributes, list):
                item["attributes_hint"] = [
                    str(attribute).strip()[:80]
                    for attribute in attributes
                    if str(attribute).strip()
                ][:12]
            else:
                item.pop("attributes_hint", None)
            item.pop("attribute_hints", None)
            item.pop("attributes", None)
        for alias in ("source_span", "text"):
            item.pop(alias, None)
        for forbidden in (
            "entities",
            "participants",
            "temporal_anchor",
            "time_anchor",
            "time_expression",
            "resolved_time",
            "temporal_scope",
            "related_to_recent",
            "notes",
        ):
            item.pop(forbidden, None)
        normalized_props.append(item)
    payload["propositions"] = normalized_props

    discarded = payload.get("discarded_spans", [])
    if isinstance(discarded, list):
        normalized_discarded: list[dict[str, str]] = []
        for item in discarded:
            if isinstance(item, str) and item.strip():
                normalized_discarded.append(
                    {"text": item.strip(), "reason": DiscardReason.NO_DURABLE_MEMORY.value}
                )
                repairs.append("discarded_span_alias")
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                normalized_discarded.append(
                    {
                        "text": item["text"],
                        "reason": item.get("reason", DiscardReason.NO_DURABLE_MEMORY.value),
                    }
                )
        payload["discarded_spans"] = normalized_discarded

    raw_gate = payload.get("gate_reason")
    gate = _normalize_gate_reason(raw_gate, should_extract=should_extract, props=normalized_props)
    if gate.value != raw_gate:
        repairs.append("gate_reason_alias")
    payload["gate_reason"] = gate.value
    return payload, list(dict.fromkeys(repairs))


def _coerce_detailed_payload(value: dict[str, Any]) -> dict[str, Any]:
    """Flatten bounded Stage2 transport variants into AtomicExtraction JSON.

    The model sometimes wraps claims in ``extractions`` or
    ``atomic_extractions`` and uses ``claim_type``/``evidence_span`` aliases.
    Flattening those containers does not decide a relation or a write; it only
    restores the already requested AtomicExtraction boundary. Unknown claim
    semantics remain subject to the existing raw parser and validator.
    """

    root = dict(value)
    semantic_units = root.get("semantic_units")
    if semantic_units is not None:
        if not isinstance(semantic_units, list) or any(
            not isinstance(item, dict) for item in semantic_units
        ):
            raise MemoryResponseError(
                "two-stage detailed semantic_units must be objects",
                category="schema_validation",
            )
        if root.get("claims"):
            raise MemoryResponseError(
                "two-stage detailed output cannot mix claims and semantic_units",
                category="schema_validation",
            )
        discarded = _normalize_detailed_discarded(root.get("discarded_spans", []))
        return {
            "semantic_units": [
                _normalize_detailed_semantic_unit(item, index=index)
                for index, item in enumerate(semantic_units, 1)
            ],
            "discarded_spans": discarded,
        }

    claims: list[dict[str, Any]] = []
    direct_claims = root.get("claims")
    if isinstance(direct_claims, list):
        if any(not isinstance(item, dict) for item in direct_claims):
            raise MemoryResponseError(
                "two-stage detailed claims must be objects",
                category="schema_validation",
            )
        claims.extend(direct_claims)
    for container_key in ("extractions", "atomic_extractions", "propositions"):
        containers = root.get(container_key)
        if not isinstance(containers, list):
            continue
        for container in containers:
            if not isinstance(container, dict):
                continue
            nested = container.get("claims")
            if isinstance(nested, list):
                for nested_claim in nested:
                    if not isinstance(nested_claim, dict):
                        raise MemoryResponseError(
                            "two-stage nested claims must be objects",
                            category="schema_validation",
                        )
                    item = dict(nested_claim)
                    _inherit_claim_hint(item, container)
                    claims.append(item)
    normalized_claims = [
        _normalize_detailed_claim(item, index=index) for index, item in enumerate(claims, 1)
    ]
    return {
        "claims": normalized_claims,
        "discarded_spans": _normalize_detailed_discarded(root.get("discarded_spans", [])),
    }


def _parse_detailed_extraction(
    raw: dict[str, Any],
    *,
    source_text: str,
    coarse: CoarseExtraction,
) -> tuple[SemanticAtomicExtraction, str]:
    semantic_payloads = raw.get("semantic_units")
    if not isinstance(semantic_payloads, list):
        for claim in raw.get("claims", []):
            _inherit_proposition_semantics(claim, coarse)
        parsed = parse_memory_response(
            json.dumps(raw, ensure_ascii=False),
            source_text=source_text,
            validation_mode="raw",
        )
        extraction = SemanticAtomicExtraction.model_validate(
            {
                **parsed.extraction.model_dump(mode="python"),
                "should_extract": coarse.should_extract,
                "gate_reason": coarse.gate_reason,
            }
        )
        return extraction, parsed.repair_steps

    for unit in semantic_payloads:
        _inherit_proposition_semantics(unit, coarse)
    raw_new_claims = [
        {key: value for key, value in payload.items() if key != "semantic_type"}
        for payload in semantic_payloads
        if payload.get("semantic_type") == "new_memory"
    ]
    parsed = parse_memory_response(
        json.dumps(
            {
                "claims": raw_new_claims,
                "discarded_spans": raw.get("discarded_spans", []),
            },
            ensure_ascii=False,
        ),
        source_text=source_text,
        validation_mode="raw",
    )
    if len(parsed.extraction.claims) != len(raw_new_claims):
        raise MemoryResponseError(
            "two-stage semantic new-memory draft failed atomic validation",
            category="atomicity_validation",
            repair_steps=parsed.repair_steps,
        )

    claims = iter(parsed.extraction.claims)
    units: list[NewMemoryDraft | EnrichmentDraft | RefinementDraft] = []
    for payload in semantic_payloads:
        semantic_type = payload.get("semantic_type")
        if semantic_type == "new_memory":
            units.append(NewMemoryDraft.from_atomic_claim(next(claims)))
            continue
        unit = (
            EnrichmentDraft.model_validate(payload)
            if semantic_type == "enrichment"
            else RefinementDraft.model_validate(payload)
        )
        if unit.evidence_span not in source_text:
            raise MemoryResponseError(
                "two-stage semantic unit evidence is not in the user message",
                category="semantic_validation",
            )
        units.append(unit)
    extraction = SemanticAtomicExtraction(
        should_extract=coarse.should_extract,
        gate_reason=coarse.gate_reason,
        claims=parsed.extraction.claims,
        semantic_units=units,
        discarded_spans=parsed.extraction.discarded_spans,
    )
    return extraction, parsed.repair_steps


def _normalize_detailed_discarded(value: object) -> list[object]:
    if not isinstance(value, list):
        return []
    return [
        {"text": item, "reason": DiscardReason.NO_DURABLE_MEMORY.value}
        if isinstance(item, str)
        else item
        for item in value
    ]


def _normalize_detailed_semantic_unit(
    value: dict[str, Any],
    *,
    index: int,
) -> dict[str, Any]:
    unit = dict(value)
    semantic_type = str(unit.get("semantic_type") or unit.get("type") or "").strip().casefold()
    semantic_type = {
        "new": "new_memory",
        "new_memory_draft": "new_memory",
        "create": "new_memory",
        "attribute_completion": "enrichment",
        "enrichment_draft": "enrichment",
        "refinement_draft": "refinement",
    }.get(semantic_type, semantic_type)
    unit.pop("type", None)
    if semantic_type == "new_memory":
        unit.pop("semantic_type", None)
        if "payload" not in unit and "semantic_payload" in unit:
            unit["payload"] = unit.pop("semantic_payload")
        return {
            "semantic_type": "new_memory",
            **_normalize_detailed_claim(unit, index=index),
        }
    if semantic_type not in {"enrichment", "refinement"}:
        raise MemoryResponseError(
            f"unsupported two-stage semantic type: {semantic_type}",
            category="unsupported_enum",
        )

    unit["semantic_type"] = semantic_type
    unit["unit_id"] = str(
        unit.get("unit_id")
        or unit.pop("draft_id", None)
        or unit.pop("proposition_id", None)
        or f"u{index}"
    )
    raw_kind = unit.get("target_kind")
    kind = _normalize_coarse_kind(raw_kind)
    if kind is None:
        raise MemoryResponseError(
            f"unsupported two-stage semantic target kind: {raw_kind}",
            category="unsupported_enum",
        )
    unit["target_kind"] = kind.value
    hint = unit.get("target_semantic_hint")
    if hint is None:
        hint = {}
    if isinstance(hint, str) and hint.strip():
        # This remains an untrusted descriptive hint. Wrapping provider
        # vocabulary drift cannot create a target id or authorize a write.
        hint = {"description": hint.strip()}
    elif not isinstance(hint, dict):
        raise MemoryResponseError(
            "two-stage target_semantic_hint must be an object or text",
            category="schema_validation",
        )
    event_type_hint = unit.pop("event_type_hint", None)
    if isinstance(event_type_hint, str) and event_type_hint.strip():
        hint.setdefault("event_type", event_type_hint.strip())
    unit["target_semantic_hint"] = hint
    evidence = unit.get("evidence_span")
    if not isinstance(evidence, str):
        evidence_spans = unit.pop("evidence_spans", None)
        if isinstance(evidence_spans, list) and len(evidence_spans) == 1:
            evidence = evidence_spans[0]
    unit["evidence_span"] = evidence
    if "subject_hint" not in unit and "subject" in unit:
        unit["subject_hint"] = unit.pop("subject")
    if semantic_type == "enrichment":
        if "attribute_name" not in unit and "field" in unit:
            unit["attribute_name"] = unit.pop("field")
        if "attribute_namespace" not in unit:
            unit["attribute_namespace"] = (
                "canonical"
                if unit.get("attribute_name")
                in {
                    "cause",
                    "severity",
                    "emotion",
                    "resolution",
                    "outcome",
                    "location",
                    "activity_type",
                }
                else "custom"
            )
    return unit


def _inherit_claim_hint(claim: dict[str, Any], container: dict[str, Any]) -> None:
    for field in (
        "proposition_id",
        "kind",
        "candidate_kind",
        "candidate_kinds",
        "semantic_role",
        "epistemic_status",
        "perspective",
        "relation_hint",
        "operation_hint",
        "occurrence_group",
        "same_occurrence_group",
        "answered_questions",
        "answered_pending_questions",
    ):
        if field not in claim and field in container:
            claim[field] = container[field]


def _normalize_detailed_claim(item: dict[str, Any], *, index: int) -> dict[str, Any]:
    claim = dict(item)
    claim_id = claim.get("claim_id") or claim.get("id") or claim.get("proposition_id")
    claim["claim_id"] = str(claim_id or f"c{index}")
    raw_kind = claim.get("kind") or claim.get("claim_type") or claim.get("candidate_kind")
    if raw_kind is None:
        candidates = claim.get("candidate_kinds")
        if isinstance(candidates, list) and candidates:
            raw_kind = candidates[0]
    kind = _normalize_coarse_kind(raw_kind)
    if kind is None:
        raise MemoryResponseError(
            f"unsupported two-stage detailed kind: {raw_kind}",
            category="unsupported_enum",
        )
    claim["kind"] = kind.value
    claim.pop("claim_type", None)
    claim.pop("candidate_kinds", None)
    claim.pop("candidate_kind", None)
    subject = str(claim.get("subject") or "").strip()
    if not subject:
        raise MemoryResponseError(
            "two-stage detailed claim is missing subject",
            category="schema_validation",
        )
    claim["subject"] = {
        "user_and_partner": "relationship",
        "user_and_她": "relationship",
        "双方": "relationship",
        "我们": "relationship",
        "我": "user",
        "用户": "user",
        "她": "partner",
        "对方": "partner",
    }.get(subject, subject)
    evidence = claim.get("evidence_spans") or claim.get("evidence_span")
    if isinstance(evidence, str):
        evidence = [evidence]
    if not isinstance(evidence, list):
        evidence = []
    evidence = [str(value) for value in evidence if str(value).strip()]
    if not evidence:
        raise MemoryResponseError(
            "two-stage detailed claim is missing evidence",
            category="schema_validation",
        )
    claim["evidence_spans"] = evidence[:8]
    claim.pop("evidence_span", None)
    claim.setdefault("summary", evidence[0])
    if not isinstance(claim.get("predicate"), str) or not claim["predicate"].strip():
        raise MemoryResponseError(
            "two-stage detailed claim is missing predicate",
            category="schema_validation",
        )
    payload = dict(claim.get("payload") or {})
    for source in ("attributes", "qualifiers"):
        value = claim.pop(source, None)
        if isinstance(value, dict):
            payload.update(value)
    for source in ("polarity", "modality", "atomic", "atomicity_notes"):
        value = claim.pop(source, None)
        if value is not None:
            payload[source] = value
    _normalize_role_sensitive_payload(
        claim,
        payload,
        evidence_spans=evidence,
    )
    claim["payload"] = payload
    return claim


def _normalize_role_sensitive_payload(
    claim: dict[str, Any],
    payload: dict[str, Any],
    *,
    evidence_spans: list[str],
) -> None:
    """Apply only registered metric/state aliases at the Stage 2 boundary.

    This is intentionally narrower than the full production normalizer.  It
    repairs provider vocabulary drift while refusing to invent an unregistered
    dimension or value.
    """

    kind = _normalize_coarse_kind(claim.get("kind"))
    evidence_text = " ".join(evidence_spans)
    if kind == MemoryKind.INTERACTION_PATTERN:
        raw_metric = payload.get("metric") or payload.get("metric_hint")
        metric = normalize_interaction_metric(raw_metric)
        if metric not in INTERACTION_PATTERN_DIMENSIONS:
            predicate_metric = normalize_interaction_metric(
                claim.get("predicate") or claim.get("raw_predicate")
            )
            metric = (
                predicate_metric if predicate_metric in INTERACTION_PATTERN_DIMENSIONS else None
            )
        if metric is None:
            detected = detect_evidence_dimensions(evidence_text) & INTERACTION_PATTERN_DIMENSIONS
            if len(detected) == 1:
                metric = next(iter(detected))
        if metric is not None:
            payload["metric"] = metric
        # metric_hint is transport vocabulary, not a persisted semantic field.
        payload.pop("metric_hint", None)
    elif kind == MemoryKind.RELATIONSHIP_STATE:
        dimension = normalize_state_dimension(
            payload.get("state_dimension") or claim.get("state_dimension")
        )
        value = normalize_state_value(
            dimension,
            payload.get("state_value") or claim.get("state_value"),
        )
        if dimension is not None:
            payload["state_dimension"] = dimension
            claim["state_dimension"] = dimension
        if value is not None:
            payload["state_value"] = value
            claim["state_value"] = value


def _normalize_coarse_kind(value: object) -> MemoryKind | None:
    if isinstance(value, MemoryKind):
        return value
    normalized = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    try:
        return MemoryKind(normalized)
    except ValueError:
        return _COARSE_KIND_ALIASES.get(normalized)


def _normalize_semantic_role(value: object) -> SemanticRole | None:
    normalized = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    try:
        return SemanticRole(normalized)
    except ValueError:
        aliases = {
            # Keep the legacy ``state`` spelling parse-compatible; the router
            # maps both legacy STATE_ASSERTION and new STATE_UPDATE to State.
            "state": SemanticRole.STATE_ASSERTION,
            "state_update": SemanticRole.STATE_UPDATE,
            "context": SemanticRole.CONTEXTUAL_COMPLETION,
            "correction": SemanticRole.CORRECTION,
            "enrichment": SemanticRole.ATTRIBUTE_COMPLETION,
            "attribute": SemanticRole.ATTRIBUTE_COMPLETION,
            "update": SemanticRole.STATE_UPDATE,
            "pattern": SemanticRole.PATTERN_EXTRACTION,
            "belief": SemanticRole.BELIEF_EXTRACTION,
            "standalone": SemanticRole.NEW_PROPOSITION,
        }
        return aliases.get(normalized)


def _normalize_extraction_epistemic_status(value: object) -> str | None:
    try:
        return ExtractionEpistemicStatus(value).value
    except ValueError:
        return None


def _storage_epistemic_status(value: object) -> str:
    """Map Stage 1 evidence semantics to the frozen storage epistemic enum.

    The extraction layer distinguishes observed/reported/believed/inferred;
    the persisted contract distinguishes confirmed/uncertain/hypothesis/
    prediction.  This compatibility mapping is deliberately one-way and does
    not add a new database field or grant lifecycle authority to the model.
    """

    normalized = str(value or "").strip().casefold().replace("-", "_")
    if normalized in {item.value for item in EpistemicStatus}:
        return normalized
    mapping = {
        ExtractionEpistemicStatus.OBSERVED.value: EpistemicStatus.CONFIRMED.value,
        ExtractionEpistemicStatus.REPORTED.value: EpistemicStatus.CONFIRMED.value,
        ExtractionEpistemicStatus.BELIEVED.value: EpistemicStatus.UNCERTAIN.value,
        ExtractionEpistemicStatus.INFERRED.value: EpistemicStatus.HYPOTHESIS.value,
        ExtractionEpistemicStatus.UNCERTAIN.value: EpistemicStatus.UNCERTAIN.value,
    }
    return mapping.get(normalized, EpistemicStatus.UNCERTAIN.value)


def _normalize_proposition_origin(value: object) -> PropositionOrigin | None:
    if isinstance(value, PropositionOrigin):
        return value
    normalized = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "answer": PropositionOrigin.ANSWER_TO_QUESTION,
        "answer_to_question": PropositionOrigin.ANSWER_TO_QUESTION,
        "question_answer": PropositionOrigin.ANSWER_TO_QUESTION,
        "spontaneous": PropositionOrigin.SPONTANEOUS_DISCLOSURE,
        "spontaneous_disclosure": PropositionOrigin.SPONTANEOUS_DISCLOSURE,
        "follow_up": PropositionOrigin.FOLLOW_UP_DETAIL,
        "follow_up_detail": PropositionOrigin.FOLLOW_UP_DETAIL,
        "detail": PropositionOrigin.FOLLOW_UP_DETAIL,
        "new": PropositionOrigin.NEW_OCCURRENCE,
        "new_occurrence": PropositionOrigin.NEW_OCCURRENCE,
        "occurrence": PropositionOrigin.NEW_OCCURRENCE,
        "uncertain": PropositionOrigin.UNCERTAIN,
        "unknown": PropositionOrigin.UNCERTAIN,
    }
    return aliases.get(normalized)


def _normalize_relation_hint(value: object) -> RelationHint | None:
    if isinstance(value, RelationHint):
        return value
    normalized = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "same": RelationHint.SAME_EVENT,
        "same_event": RelationHint.SAME_EVENT,
        "cause": RelationHint.CAUSE,
        "causal": RelationHint.CAUSE,
        "context": RelationHint.CONTEXT,
        "support": RelationHint.SUPPORT,
        "contrast": RelationHint.CONTRAST,
        "correction": RelationHint.CORRECTION,
        "none": RelationHint.NONE,
        "": RelationHint.NONE,
    }
    return aliases.get(normalized)


def _normalize_operation_hint(value: object) -> OperationHint | None:
    if isinstance(value, OperationHint):
        return value
    normalized = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "new": OperationHint.NEW_LIKE,
        "new_like": OperationHint.NEW_LIKE,
        "enrich": OperationHint.ENRICH_LIKE,
        "enrichment": OperationHint.ENRICH_LIKE,
        "enrich_like": OperationHint.ENRICH_LIKE,
        "refine": OperationHint.REFINE_LIKE,
        "refinement": OperationHint.REFINE_LIKE,
        "refine_like": OperationHint.REFINE_LIKE,
        "unknown": OperationHint.UNKNOWN,
        "": OperationHint.UNKNOWN,
    }
    return aliases.get(normalized)


def _operation_hint_from_role(role: SemanticRole | None) -> OperationHint:
    canonical = canonical_semantic_role(role) if role is not None else None
    if canonical == SemanticRole.ATTRIBUTE_COMPLETION:
        return OperationHint.ENRICH_LIKE
    if canonical == SemanticRole.REFINEMENT:
        return OperationHint.REFINE_LIKE
    if canonical in {SemanticRole.NEW_PROPOSITION, SemanticRole.CORRECTION}:
        return OperationHint.NEW_LIKE
    return OperationHint.UNKNOWN


def _normalize_gate_reason(
    value: object,
    *,
    should_extract: bool,
    props: list[dict[str, Any]],
) -> MemorySemanticGateReason:
    normalized = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    try:
        return MemorySemanticGateReason(normalized)
    except ValueError:
        if not should_extract:
            return MemorySemanticGateReason.NO_MEMORY
        kinds = {str(kind) for item in props for kind in item.get("candidate_kinds", [])}
        if len(kinds) > 1 or len(props) > 1:
            return MemorySemanticGateReason.COMPOUND_MEMORY
        if "preference" in kinds:
            return MemorySemanticGateReason.PREFERENCE
        if "interaction_pattern" in kinds:
            return MemorySemanticGateReason.INTERACTION_PATTERN
        if "relationship_state" in kinds:
            return MemorySemanticGateReason.RELATIONSHIP_STATE
        if "interaction_event" in kinds:
            return MemorySemanticGateReason.COMPOUND_MEMORY
        return _COARSE_GATE_ALIASES.get(normalized, MemorySemanticGateReason.COMPOUND_MEMORY)


def _negative_gate_reason(
    reason: MemorySemanticGateReason | None,
) -> MemorySemanticGateReason:
    if reason in {
        MemorySemanticGateReason.TRANSIENT,
        MemorySemanticGateReason.SMALL_TALK,
        MemorySemanticGateReason.NO_MEMORY,
    }:
        return reason
    return MemorySemanticGateReason.NO_MEMORY


def _parse_json_object(content: str | None) -> dict[str, Any]:
    if not content or not content.strip():
        raise MemoryResponseError(
            "two-stage model returned an empty response",
            category="empty_response",
        )
    raw = content.strip().lstrip("\ufeff")
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise MemoryResponseError(
            "two-stage model response is not a JSON object",
            category="root_shape",
        )
    try:
        value = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise MemoryResponseError(str(exc), category="json_syntax") from exc
    if not isinstance(value, dict):
        raise MemoryResponseError("two-stage JSON root must be an object", category="root_shape")
    return value


def _reject_unsafe_detailed_keys(value: object) -> None:
    forbidden = {
        "target_memory_id",
        "target_memory_ids",
        "mutation_action",
        "supersedes_id",
        "db_patch",
    }

    def walk(item: object) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if str(key).casefold() in forbidden:
                    raise ValueError(f"two-stage output contains forbidden field: {key}")
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)


def _failure_category(exc: Exception) -> str:
    if isinstance(exc, MemoryResponseError):
        return exc.category
    if isinstance(exc, ValidationError):
        return "schema_validation"
    return "transport_or_runtime"


def _fallback_reason_code(exc: Exception, *, stage: str) -> str:
    """Map a native-stage exception to the bounded diagnostic taxonomy."""

    normalized_stage = stage.casefold()
    if normalized_stage not in {"coarse", "detailed"}:
        return "UNKNOWN_ERROR"
    prefix = "STAGE1" if normalized_stage == "coarse" else "STAGE2"
    if isinstance(exc, ValidationError):
        return f"{prefix}_SCHEMA_ERROR"
    if isinstance(exc, MemoryResponseError):
        category = (exc.category or "").casefold()
        if category in {"empty_response", "root_shape", "json_syntax"}:
            return f"{prefix}_PARSE_ERROR"
        if category == "empty_propositions" and normalized_stage == "coarse":
            return "STAGE1_EMPTY_PROPOSITIONS"
        if category == "empty_claims" and normalized_stage == "detailed":
            return "STAGE2_EMPTY_CLAIMS"
        if category in {"schema_validation", "unsupported_enum", "semantic_gate_contract"}:
            return f"{prefix}_SCHEMA_ERROR"
        if category == "semantic_role_mismatch":
            return f"{prefix}_ROLE_MISMATCH"
        if category in {"atomicity_validation", "semantic_validation"}:
            return "ATOMIC_EXTRACTION_VALIDATION_ERROR"
    if normalized_stage == "detailed" and isinstance(exc, ValueError):
        if "forbidden field" in str(exc).casefold():
            return "UNSUPPORTED_OUTPUT"
        return "STAGE2_SCHEMA_ERROR"
    return f"{prefix}_CALL_ERROR"


def _build_attempt(
    details: dict[str, Any],
    started: float,
    *,
    status: MemoryAttemptStatus,
    error: str | None = None,
) -> MemoryExtractionAttempt:
    return MemoryExtractionAttempt(
        attempt=1 if details.get("stage") == "coarse" else 2,
        status=status,
        duration_ms=max(0, (perf_counter() - started) * 1000),
        model=str(details.get("model")) if details.get("model") else None,
        tier=str(details.get("tier")) if details.get("tier") else None,
        prompt_tokens=details.get("prompt_tokens"),
        completion_tokens=details.get("completion_tokens"),
        reasoning_tokens=details.get("reasoning_tokens"),
        total_tokens=details.get("total_tokens"),
        claim_count=details.get("claim_count"),
        extraction_status=details.get("failure_category"),
        failure_category=details.get("failure_category"),
        raw_model_response=details.get("raw_model_response"),
        extraction_strategy="two_stage",
        stage=str(details.get("stage")) if details.get("stage") else None,
        fallback_used=False,
        error=error,
    )


# Public short name used by the v3.2 design document.  Keep the explicit
# ``Memory`` spelling as the canonical implementation name for consistency
# with the existing extractor classes.
TwoStageExtractor = TwoStageMemoryExtractor

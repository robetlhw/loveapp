import json
import re
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime
from time import perf_counter
from typing import Any, Literal

from openai import AsyncOpenAI
from pydantic import SecretStr, ValidationError

from loveapp.application.memory_repair import (
    MemoryResponseError,
    ParsedMemoryResponse,
    parse_memory_response,
    validate_memory_extraction,
)
from loveapp.application.memory_upgrade import MemoryUpgradeDecision, assess_memory_upgrade
from loveapp.domain.memory import (
    AtomicExtraction,
    MemoryAttemptStatus,
    MemoryCandidate,
    MemoryExtractionAttempt,
    MemoryItem,
    StoredMessage,
)
from loveapp.domain.memory_predicates import CANONICAL_PREDICATES
from loveapp.domain.memory_verification import ClaimVerification
from loveapp.domain.runtime_context import PendingMemoryContext
from loveapp.ports.memory import MemoryAttemptCallback
from loveapp.ports.observability import TraceRecorder

_MEMORY_PROMPT_VERSION = "memory-v2.6"


class OpenAICompatibleMemoryExtractor:
    requires_semantic_gate_contract = True

    def __init__(
        self,
        *,
        api_key: SecretStr,
        base_url: str,
        model: str,
        timeout_seconds: float = 60,
        max_retries: int = 2,
        max_tokens: int = 4096,
        tier: str = "flash",
        thinking: Literal["enabled", "disabled"] | None = None,
        validation_mode: Literal["legacy", "raw"] = "legacy",
    ) -> None:
        if validation_mode not in {"legacy", "raw"}:
            raise ValueError(f"unsupported validation_mode: {validation_mode}")
        self._model = model
        self._max_tokens = max_tokens
        self._sdk_max_retries = max_retries
        self._tier = tier
        self._thinking = thinking
        self._validation_mode = validation_mode
        self._client = AsyncOpenAI(
            api_key=api_key.get_secret_value(),
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    async def extract(
        self,
        text: str,
        *,
        reference_time: datetime,
        existing_memories: list[MemoryItem],
        conversation_history: list[StoredMessage],
        pending_memory_context: PendingMemoryContext | None = None,
        trace: TraceRecorder | None = None,
        attempt_callback: MemoryAttemptCallback | None = None,
    ) -> AtomicExtraction:
        parsed = await self._extract_once(
            text,
            reference_time=reference_time,
            existing_memories=existing_memories,
            conversation_history=conversation_history,
            pending_memory_context=pending_memory_context,
            trace=trace,
            attempt_callback=attempt_callback,
            attempt_number=1,
            trace_name=(
                "memory_model_attempt_1"
                if self._tier == "flash"
                else f"memory_model_{self._tier}_attempt_1"
            ),
        )
        return parsed.extraction

    async def _extract_once(
        self,
        text: str,
        *,
        reference_time: datetime,
        existing_memories: list[MemoryItem],
        conversation_history: list[StoredMessage],
        pending_memory_context: PendingMemoryContext | None = None,
        trace: TraceRecorder | None,
        attempt_callback: MemoryAttemptCallback | None,
        attempt_number: int,
        trace_name: str,
    ) -> ParsedMemoryResponse:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _build_prompt(
                    text,
                    reference_time,
                    existing_memories,
                    conversation_history,
                    pending_memory_context,
                ),
            },
        ]
        started = perf_counter()
        details: dict[str, Any] = {}
        try:
            measure = trace.measure(trace_name) if trace else nullcontext({})
            with measure as details:
                details["model"] = self._model
                details["tier"] = self._tier
                details["application_attempt"] = attempt_number
                details["sdk_max_retries"] = self._sdk_max_retries
                details["max_tokens"] = self._max_tokens
                request_kwargs: dict[str, Any] = {
                    "model": self._model,
                    "messages": messages,
                    "response_format": {"type": "json_object"},
                    "temperature": 0,
                    "max_tokens": self._max_tokens,
                }
                if self._thinking is not None:
                    # DeepSeek exposes this OpenAI-compatible extension via
                    # the request body rather than the OpenAI SDK signature.
                    request_kwargs["extra_body"] = {"thinking": {"type": self._thinking}}
                    details["thinking"] = self._thinking
                completion = await self._client.chat.completions.create(
                    **request_kwargs,
                )
                _capture_usage(details, getattr(completion, "usage", None))
                choice = completion.choices[0]
                details["finish_reason"] = getattr(choice, "finish_reason", None)
                content = choice.message.content
                try:
                    parsed = parse_memory_response(
                        content,
                        source_text=text,
                        validation_mode=self._validation_mode,
                    )
                except MemoryResponseError as exc:
                    # The trace context stores its details when it exits, so
                    # record the parse category before re-raising.
                    details["failure_category"] = exc.category
                    details.update(exc.details)
                    if exc.repair_status != "none":
                        details.setdefault("repair_status", exc.repair_status)
                    if exc.repair_steps:
                        details.setdefault("repair_steps", exc.repair_steps)
                    details.setdefault(
                        "raw_model_response",
                        _safe_model_response_snapshot(content),
                    )
                    details.setdefault("validation_error", str(exc)[:1000])
                    details.setdefault(
                        "repair_attempt",
                        _repair_attempt_from_steps(exc.repair_steps),
                    )
                    details.setdefault("repair_result", "unresolved")
                    raise
                parsed = replace(
                    parsed,
                    extraction=parsed.extraction.model_copy(
                        update={
                            "claims": [
                                claim.model_copy(
                                    update={
                                        "raw_predicate": (claim.raw_predicate or claim.predicate),
                                        "prompt_version": _MEMORY_PROMPT_VERSION,
                                        "extractor_model": self._model,
                                    }
                                )
                                for claim in parsed.extraction.claims
                            ]
                        }
                    ),
                )
                details["repair_status"] = parsed.repair_status
                details["validation_mode"] = parsed.validation_mode
                details["validation_boundary"] = (
                    "raw_generic" if parsed.validation_mode == "raw" else "legacy_combined"
                )
                details["extraction_status"] = parsed.extraction_status
                details["semantic_gate_should_extract"] = parsed.extraction.should_extract
                details["semantic_gate_reason"] = (
                    parsed.extraction.gate_reason.value
                    if parsed.extraction.gate_reason is not None
                    else None
                )
                details["semantic_gate_contract"] = (
                    "present" if parsed.extraction.should_extract is not None else "legacy_missing"
                )
                details["claim_count"] = len(parsed.extraction.claims)
                details["original_claim_count"] = parsed.original_claim_count
                details["repaired_claim_count"] = parsed.repaired_claim_count
                details["discarded_claim_count"] = parsed.discarded_claim_count
                details["discarded_span_count"] = len(parsed.extraction.discarded_spans)
                details["claim_confidences"] = ",".join(
                    f"{claim.claim_id}:{claim.confidence:.2f}" for claim in parsed.extraction.claims
                )
                details["claims_json"] = json.dumps(
                    [claim.model_dump(mode="json") for claim in parsed.extraction.claims],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                details["claim_predicates_json"] = json.dumps(
                    [
                        {
                            "claim_id": claim.claim_id,
                            "raw_predicate": claim.raw_predicate or claim.predicate,
                        }
                        for claim in parsed.extraction.claims
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                if parsed.repair_steps:
                    details["repair_steps"] = parsed.repair_steps
                    repair_attempt = _repair_attempt_from_steps(parsed.repair_steps)
                    if repair_attempt != "none":
                        details["repair_attempt"] = repair_attempt
                        details["repair_result"] = _relationship_stage_repair_result(
                            parsed.extraction
                        )
                if parsed.invalid_claim_count:
                    details["invalid_claim_count"] = parsed.invalid_claim_count
                    details["invalid_claim_reasons"] = " | ".join(parsed.invalid_claim_reasons)
                    details["discard_reason"] = "partial_claims_discarded"
        except MemoryResponseError as exc:
            details["failure_category"] = exc.category
            details["retry_reason"] = "local_parse_or_validation"
            attempt = _build_attempt(
                details,
                attempt_number,
                MemoryAttemptStatus.FAILED,
                (perf_counter() - started) * 1000,
                error=str(exc),
            )
            exc.attempt = attempt
            exc.details = details
            _safe_notify(attempt_callback, attempt)
            raise
        except Exception as exc:
            details["failure_category"] = "transport"
            attempt = _build_attempt(
                details,
                attempt_number,
                MemoryAttemptStatus.FAILED,
                (perf_counter() - started) * 1000,
                error=str(exc),
            )
            _safe_notify(attempt_callback, attempt)
            raise
        else:
            attempt = _build_attempt(
                details,
                attempt_number,
                MemoryAttemptStatus.COMPLETED,
                (perf_counter() - started) * 1000,
            )
            _safe_notify(attempt_callback, attempt)
            return parsed

    async def aclose(self) -> None:
        await self._client.close()

    async def verify_claim(
        self,
        text: str,
        *,
        candidate: MemoryCandidate,
        existing_memories: list[MemoryItem],
        allowed_target_ids: set[str],
        trace: TraceRecorder | None = None,
    ) -> ClaimVerification:
        messages = [
            {"role": "system", "content": _VERIFIER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _build_verifier_prompt(text, candidate, existing_memories),
            },
        ]
        measure = trace.measure("memory_claim_verifier") if trace else nullcontext({})
        with measure as details:
            details["model"] = self._model
            details["tier"] = self._tier
            completion = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=min(self._max_tokens, 1200),
                **(
                    {"extra_body": {"thinking": {"type": self._thinking}}}
                    if self._thinking is not None
                    else {}
                ),
            )
            _capture_usage(details, getattr(completion, "usage", None))
            verification = _parse_claim_verification(
                completion.choices[0].message.content
            ).model_copy(update={"verifier_model": self._model})
            invalid_targets = set(verification.target_memory_ids) - allowed_target_ids
            if invalid_targets:
                raise ValueError("claim verifier returned a target outside the candidate set")
            if (
                verification.canonical_predicate is not None
                and verification.canonical_predicate not in CANONICAL_PREDICATES
            ):
                raise ValueError("claim verifier returned an unregistered canonical predicate")
            details["relation"] = verification.relation.value
            details["claim_supported"] = verification.claim_supported
            details["evidence_sufficient"] = verification.evidence_sufficient
            return verification


class TieredMemoryExtractor:
    """Run Flash first and use a strong model only for important uncertainty."""

    requires_semantic_gate_contract = True

    def __init__(
        self,
        flash: OpenAICompatibleMemoryExtractor,
        strong: OpenAICompatibleMemoryExtractor | None = None,
        *,
        upgrade_min_importance: int = 4,
    ) -> None:
        self._flash = flash
        self._strong = strong
        self._upgrade_min_importance = upgrade_min_importance

    @property
    def can_verify(self) -> bool:
        return self._strong is not None

    @property
    def verifier_model(self) -> str | None:
        return self._strong._model if self._strong is not None else None

    async def verify_claim(
        self,
        text: str,
        *,
        candidate: MemoryCandidate,
        existing_memories: list[MemoryItem],
        allowed_target_ids: set[str],
        trace: TraceRecorder | None = None,
    ) -> ClaimVerification:
        if self._strong is None:
            raise RuntimeError("strong claim verifier is not configured")
        return await self._strong.verify_claim(
            text,
            candidate=candidate,
            existing_memories=existing_memories,
            allowed_target_ids=allowed_target_ids,
            trace=trace,
        )

    async def extract(
        self,
        text: str,
        *,
        reference_time: datetime,
        existing_memories: list[MemoryItem],
        conversation_history: list[StoredMessage],
        pending_memory_context: PendingMemoryContext | None = None,
        trace: TraceRecorder | None = None,
        attempt_callback: MemoryAttemptCallback | None = None,
    ) -> AtomicExtraction:
        flash_attempts: list[MemoryExtractionAttempt] = []
        try:
            flash_parsed = await self._flash._extract_once(
                text,
                reference_time=reference_time,
                existing_memories=existing_memories,
                conversation_history=conversation_history,
                pending_memory_context=pending_memory_context,
                trace=trace,
                attempt_callback=flash_attempts.append,
                attempt_number=1,
                trace_name="memory_model_attempt_1",
            )
        except MemoryResponseError as failure:
            decision = assess_memory_upgrade(
                text,
                existing_memories=existing_memories,
                conversation_history=conversation_history,
                failure=failure,
                min_importance=self._upgrade_min_importance,
            )
            self._record_decision(trace, decision, failure=failure)
            if not decision.should_upgrade or self._strong is None:
                self._annotate_attempt(
                    flash_attempts,
                    upgrade_reason=decision.reason,
                    discard_reason=(
                        _discard_reason_for_failure(failure)
                        if not decision.should_upgrade
                        else "strong_model_unavailable"
                    ),
                )
                _flush_attempts(flash_attempts, attempt_callback)
                return AtomicExtraction()
            self._annotate_attempt(flash_attempts, upgrade_reason=decision.reason)
            _flush_attempts(flash_attempts, attempt_callback)
            return await self._run_strong(
                text,
                reference_time=reference_time,
                existing_memories=existing_memories,
                conversation_history=conversation_history,
                pending_memory_context=pending_memory_context,
                trace=trace,
                attempt_callback=attempt_callback,
                fallback_extraction=None,
                decision=decision,
            )
        except Exception:
            _flush_attempts(flash_attempts, attempt_callback)
            raise

        flash_extraction = flash_parsed.extraction
        if flash_extraction.should_extract is False:
            decision = MemoryUpgradeDecision(
                should_upgrade=False,
                reason="semantic_gate_rejected",
                signals=("semantic_gate_false",),
            )
            self._record_decision(trace, decision)
            self._annotate_attempt(
                flash_attempts,
                upgrade_reason=decision.reason,
            )
            _flush_attempts(flash_attempts, attempt_callback)
            return flash_extraction
        decision = assess_memory_upgrade(
            text,
            existing_memories=existing_memories,
            conversation_history=conversation_history,
            extraction=flash_extraction,
            partial=_is_partial_extraction(flash_parsed),
            min_importance=self._upgrade_min_importance,
        )
        self._record_decision(trace, decision)
        if not decision.should_upgrade or self._strong is None:
            self._annotate_attempt(
                flash_attempts,
                upgrade_reason=decision.reason,
                discard_reason="strong_model_unavailable" if decision.reason else None,
            )
            _flush_attempts(flash_attempts, attempt_callback)
            return flash_extraction

        self._annotate_attempt(flash_attempts, upgrade_reason=decision.reason)
        _flush_attempts(flash_attempts, attempt_callback)
        return await self._run_strong(
            text,
            reference_time=reference_time,
            existing_memories=existing_memories,
            conversation_history=conversation_history,
            pending_memory_context=pending_memory_context,
            trace=trace,
            attempt_callback=attempt_callback,
            fallback_extraction=flash_extraction,
            decision=decision,
        )

    async def _run_strong(
        self,
        text: str,
        *,
        reference_time: datetime,
        existing_memories: list[MemoryItem],
        conversation_history: list[StoredMessage],
        pending_memory_context: PendingMemoryContext | None,
        trace: TraceRecorder | None,
        attempt_callback: MemoryAttemptCallback | None,
        fallback_extraction: AtomicExtraction | None,
        decision: MemoryUpgradeDecision,
    ) -> AtomicExtraction:
        strong_attempts: list[MemoryExtractionAttempt] = []
        try:
            parsed = await self._strong._extract_once(
                text,
                reference_time=reference_time,
                existing_memories=existing_memories,
                conversation_history=conversation_history,
                pending_memory_context=pending_memory_context,
                trace=trace,
                attempt_callback=strong_attempts.append,
                attempt_number=2,
                trace_name="memory_model_strong_attempt_2",
            )
        except MemoryResponseError:
            self._annotate_attempt(
                strong_attempts,
                upgrade_reason=decision.reason,
                discard_reason="strong_output_invalid",
            )
            _flush_attempts(strong_attempts, attempt_callback)
            return fallback_extraction or AtomicExtraction()
        except Exception:
            _flush_attempts(strong_attempts, attempt_callback)
            if fallback_extraction is not None:
                return fallback_extraction
            raise
        self._annotate_attempt(strong_attempts, upgrade_reason=decision.reason)
        if parsed.extraction.should_extract is False:
            self._annotate_attempt(
                strong_attempts,
                discard_reason="strong_semantic_gate_rejected",
            )
            _flush_attempts(strong_attempts, attempt_callback)
            return fallback_extraction or parsed.extraction
        if (
            fallback_extraction is not None
            and fallback_extraction.claims
            and not parsed.extraction.claims
        ):
            # A structurally valid empty Strong response must not erase a
            # usable Flash extraction.  Keep the fast-path claims and expose
            # the disagreement in attempt telemetry for later review.
            self._annotate_attempt(
                strong_attempts,
                discard_reason="strong_empty_fallback_to_flash",
            )
            _flush_attempts(strong_attempts, attempt_callback)
            return fallback_extraction
        _flush_attempts(strong_attempts, attempt_callback)
        if fallback_extraction is not None:
            return parsed.extraction.model_copy(
                update={
                    "should_extract": fallback_extraction.should_extract,
                    "gate_reason": fallback_extraction.gate_reason,
                }
            )
        return parsed.extraction

    @staticmethod
    def _annotate_attempt(
        attempts: list[MemoryExtractionAttempt],
        *,
        upgrade_reason: str | None = None,
        discard_reason: str | None = None,
    ) -> None:
        if not attempts:
            return
        updates: dict[str, str] = {}
        if upgrade_reason is not None:
            updates["upgrade_reason"] = upgrade_reason
        if discard_reason is not None:
            updates["discard_reason"] = discard_reason
        if not updates:
            return
        attempts[-1] = attempts[-1].model_copy(update=updates)

    @staticmethod
    def _record_decision(
        trace: TraceRecorder | None,
        decision: MemoryUpgradeDecision,
        *,
        failure: MemoryResponseError | None = None,
    ) -> None:
        if trace is None:
            return
        with trace.measure("memory_extraction_upgrade_gate") as details:
            details["should_upgrade"] = decision.should_upgrade
            details["importance"] = decision.importance
            details["signals"] = ",".join(decision.signals) or None
            details["upgrade_reason"] = decision.reason
            details["failure_category"] = failure.category if failure else None
            details["discard_reason"] = (
                _discard_reason_for_failure(failure)
                if failure is not None and not decision.should_upgrade
                else None
            )

    async def aclose(self) -> None:
        await self._flash.aclose()
        if self._strong is not None and self._strong is not self._flash:
            await self._strong.aclose()


def _build_prompt(
    text: str,
    reference_time: datetime,
    existing_memories: list[MemoryItem],
    conversation_history: list[StoredMessage],
    pending_memory_context: PendingMemoryContext | None = None,
) -> str:
    payload = {
        "reference_time": reference_time.isoformat(),
        "user_message": text,
        "recent_conversation": [
            {"role": message.role.value, "content": message.content}
            for message in conversation_history[-6:]
        ],
        "runtime_context": {
            "l0_route": (
                "CONTEXT_PASS" if pending_memory_context is not None else None
            ),
            "pending_memory_context": (
                pending_memory_context.model_dump(mode="json")
                if pending_memory_context is not None
                else None
            ),
        },
        "existing_active_memories": [
            {
                "id": item.id,
                "kind": item.kind.value,
                "subject": item.subject,
                "summary": item.summary,
                "time_kind": item.time_kind.value,
                "occurred_at": item.occurred_at.isoformat() if item.occurred_at else None,
                "period_start": item.period_start.isoformat() if item.period_start else None,
                "period_end": item.period_end.isoformat() if item.period_end else None,
                "expires_at": item.expires_at.isoformat() if item.expires_at else None,
                "perspective": item.perspective.value,
                "status": item.status.value,
                "predicate_type": item.predicate_type.value,
                "canonical_predicate": item.canonical_predicate,
                "custom_predicate": item.custom_predicate,
                "state_dimension": item.state_dimension,
                "state_value": item.state_value,
                "admission_decision": (
                    item.admission_decision.value if item.admission_decision else None
                ),
                "payload": item.payload,
            }
            for item in existing_memories[:20]
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _build_verifier_prompt(
    text: str,
    candidate: MemoryCandidate,
    existing_memories: list[MemoryItem],
) -> str:
    payload = {
        "user_message": text,
        "candidate_claim": candidate.model_dump(mode="json"),
        "existing_memory_candidates": [
            {
                "id": item.id,
                "kind": item.kind.value,
                "subject": item.subject,
                "summary": item.summary,
                "status": item.status.value,
                "canonical_predicate": item.canonical_predicate,
                "custom_predicate": item.custom_predicate,
                "state_dimension": item.state_dimension,
                "state_value": item.state_value,
                "evidence_spans": item.evidence_spans,
                "payload": item.payload,
            }
            for item in existing_memories[:8]
        ],
        "canonical_predicates": list(CANONICAL_PREDICATES),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _parse_claim_verification(content: str | None) -> ClaimVerification:
    if not content or not content.strip():
        raise ValueError("claim verifier returned an empty response")
    raw = content.strip().lstrip("\ufeff")
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("claim verifier response is not a JSON object")
    return ClaimVerification.model_validate(json.loads(raw[start : end + 1]))


def _parse_response(content: str | None) -> AtomicExtraction:
    return parse_memory_response(content).extraction


def _validate_extraction(extraction: AtomicExtraction, source_text: str) -> None:
    validate_memory_extraction(extraction, source_text)


def _validation_detail(exc: ValidationError | json.JSONDecodeError) -> str:
    if isinstance(exc, ValidationError):
        details: list[str] = []
        for error in exc.errors(include_url=False)[:5]:
            location = ".".join(str(part) for part in error["loc"])
            details.append(f"{location or 'root'} - {error['msg']}")
        return "; ".join(details)
    return f"第 {exc.lineno} 行第 {exc.colno} 列 - {exc.msg}"


def _capture_usage(details: dict, usage) -> None:
    if usage is None:
        return
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, field, None)
        if value is not None:
            details[field] = int(value)
    completion_details = getattr(usage, "completion_tokens_details", None)
    reasoning_tokens = getattr(completion_details, "reasoning_tokens", None)
    if reasoning_tokens is not None:
        details["reasoning_tokens"] = int(reasoning_tokens)


def _build_attempt(
    details: dict[str, Any],
    attempt: int,
    status: MemoryAttemptStatus,
    duration_ms: float,
    *,
    error: str | None = None,
) -> MemoryExtractionAttempt:
    return MemoryExtractionAttempt(
        attempt=attempt,
        status=status,
        duration_ms=max(0, duration_ms),
        model=str(details.get("model")) if details.get("model") else None,
        tier=str(details.get("tier")) if details.get("tier") else None,
        prompt_tokens=details.get("prompt_tokens"),
        completion_tokens=details.get("completion_tokens"),
        reasoning_tokens=details.get("reasoning_tokens"),
        total_tokens=details.get("total_tokens"),
        claim_count=details.get("claim_count"),
        original_claim_count=details.get("original_claim_count"),
        repaired_claim_count=details.get("repaired_claim_count"),
        discarded_claim_count=details.get("discarded_claim_count"),
        discarded_span_count=details.get("discarded_span_count"),
        claim_confidences=details.get("claim_confidences"),
        invalid_claim_count=details.get("invalid_claim_count"),
        invalid_claim_reasons=(
            str(details.get("invalid_claim_reasons"))[:1000]
            if details.get("invalid_claim_reasons")
            else None
        ),
        extraction_status=(
            str(details.get("extraction_status"))
            if details.get("extraction_status")
            else None
        ),
        failure_category=(
            str(details.get("failure_category")) if details.get("failure_category") else None
        ),
        repair_status=(str(details.get("repair_status")) if details.get("repair_status") else None),
        repair_steps=(
            str(details.get("repair_steps"))[:1000] if details.get("repair_steps") else None
        ),
        raw_model_response=(
            str(details.get("raw_model_response"))[:2000]
            if details.get("raw_model_response")
            else None
        ),
        invalid_claim_snapshot=(
            str(details.get("invalid_claim_snapshot"))[:2000]
            if details.get("invalid_claim_snapshot")
            else None
        ),
        validation_error=(
            str(details.get("validation_error"))[:1000] if details.get("validation_error") else None
        ),
        repair_attempt=(
            str(details.get("repair_attempt"))[:160] if details.get("repair_attempt") else None
        ),
        repair_result=(
            str(details.get("repair_result"))[:500] if details.get("repair_result") else None
        ),
        upgrade_reason=(
            str(details.get("upgrade_reason")) if details.get("upgrade_reason") else None
        ),
        discard_reason=(
            str(details.get("discard_reason")) if details.get("discard_reason") else None
        ),
        retry_reason=(str(details.get("retry_reason")) if details.get("retry_reason") else None),
        error=error[:500] if error else None,
    )


def _repair_attempt_from_steps(steps: str) -> str:
    values = {value.strip() for value in steps.split(",") if value.strip()}
    if "relationship_stage_shape_repair" in values:
        return "relationship_stage_bounded_repair"
    if "relationship_stage_semantic_normalization" in values:
        return "relationship_stage_semantic_normalization"
    if "relationship_stage_fail_closed" in values:
        return "relationship_stage_semantic_guard"
    return "none"


def _is_partial_extraction(parsed: ParsedMemoryResponse) -> bool:
    repair_steps = {value.strip() for value in parsed.repair_steps.split(",") if value.strip()}
    return bool(
        parsed.invalid_claim_count
        or parsed.repaired_claim_count
        or "partial_discarded_spans" in repair_steps
    )


def _relationship_stage_repair_result(extraction: AtomicExtraction) -> str:
    values = [
        claim.payload.get("state_value")
        for claim in extraction.claims
        if claim.canonical_predicate == "relationship.stage"
    ]
    normalized = [str(value) for value in values if value]
    return f"relationship.stage={','.join(normalized)}" if normalized else "unresolved"


def _safe_model_response_snapshot(content: object, *, limit: int = 2000) -> str:
    snapshot = str(content or "")
    try:
        parsed = json.loads(snapshot)
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = None
    if parsed is not None:
        snapshot = json.dumps(
            _redact_snapshot_value(parsed),
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    sensitive_field = re.compile(
        r'(?i)(["\']?(?:api[ _-]?key|authorization|password|secret|token|'
        r"access[ _-]?token|refresh[ _-]?token|client[ _-]?secret|private[ _-]?key)"
        r'["\']?\s*:\s*)'
        r'(?:(?:"(?:\\.|[^"\\])*")|(?:\'(?:\\.|[^\'\\])*\')|[^\r\n,}\]]+)'
    )
    snapshot = sensitive_field.sub(r'\1"[REDACTED]"', snapshot)
    return snapshot[:limit]


def _redact_snapshot_value(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]" if _is_sensitive_snapshot_key(key) else _redact_snapshot_value(child)
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_snapshot_value(child) for child in value[:5]]
    return value


def _is_sensitive_snapshot_key(value: object) -> bool:
    normalized = re.sub(r"[-_\s]+", "", str(value).casefold())
    return normalized in {
        "apikey",
        "authorization",
        "password",
        "secret",
        "token",
        "accesstoken",
        "refreshtoken",
        "clientsecret",
        "privatekey",
    } or normalized.endswith(("apikey", "password", "secret", "token", "privatekey"))


def _discard_reason_for_failure(failure: MemoryResponseError) -> str:
    return {
        "json_syntax": "ordinary_format_error",
        "empty_response": "empty_model_response",
        "root_shape": "root_shape_invalid",
        "unsupported_enum": "unsupported_enum",
        "schema_validation": "schema_validation_failed",
        "semantic_gate_contract": "semantic_gate_contract_violation",
        "semantic_validation": "semantic_validation_failed",
        "missing_temporal_anchor": "missing_temporal_anchor",
        "atomicity_validation": "atomicity_validation_failed",
    }.get(failure.category, "memory_response_invalid")


def _safe_notify(
    callback: MemoryAttemptCallback | None,
    attempt: MemoryExtractionAttempt,
) -> None:
    if callback is None:
        return
    try:
        callback(attempt)
    except Exception:
        # Telemetry must not turn a usable extraction into a failed turn.
        return


def _flush_attempts(
    attempts: list[MemoryExtractionAttempt],
    callback: MemoryAttemptCallback | None,
) -> None:
    for attempt in attempts:
        _safe_notify(callback, attempt)


_VERIFIER_SYSTEM_PROMPT = """
你是 LoveApp 的高风险记忆声明验证器。只输出一个 JSON 对象，字段固定为：
claim_supported、relation、canonical_predicate、state_dimension、state_value、
target_memory_ids、reason、evidence_sufficient。

relation 只能是 same、complementary、update、contradiction、unrelated、uncertain。
只判断 user_message 是否支持 candidate_claim，不得补充原文没有的事实。target_memory_ids 只能从
existing_memory_candidates 中选择；不确定时返回空数组。canonical_predicate 只能从传入的
canonical_predicates 选择，无法可靠映射时为 null。证据不足时 claim_supported 或
evidence_sufficient 必须为 false，不能把推测验证成事实。你只提供判断信号，不执行数据库操作。
""".strip()


_SYSTEM_PROMPT = """
你是 LoveApp 的关系记忆原子声明抽取器，只输出一个合法 JSON 对象：
{"should_extract": true, "gate_reason": "STABLE_FACT", "claims": [...],
 "discarded_spans": [...]}。

你必须在这同一次响应中同时完成语义 Gate 判断和原子声明抽取，禁止省略 should_extract 或
gate_reason，也不要输出额外解释。should_extract 只能是 JSON 布尔值。gate_reason 只能是：
STABLE_FACT、PREFERENCE、INTERACTION_PATTERN、RELATIONSHIP_STATE、RELATIONSHIP_CHANGE、
PARTIAL_CHANGE、USER_BELIEF、PLANNED_EVENT、ACTION_INTENT、ADVICE_OUTCOME、
COMPOUND_MEMORY、CONTEXT_DEPENDENT_REPLY、TRANSIENT、SMALL_TALK、NO_MEMORY。

语义 Gate 规则：
- 未来咨询仍可能有用的稳定事实、偏好、持续互动模式、关系状态或变化、用户持续信念、明确计划、
  行动承诺、建议结果和上下文相关回答，should_extract=true，并选择对应的正向 gate_reason。
- 一次性瞬时情绪或无持续价值的单次反应用 TRANSIENT；寒暄用 SMALL_TALK；普通咨询问题、拒绝
  提供信息、纯操作内容或没有可保存信息时用 NO_MEMORY。以上三类必须 should_extract=false。
- 语义 Gate 和 claim 可构造性分开判断。若 user_message 是对 recent_conversation 中最近一条
  Assistant 关系记忆追问的具体回答（时长、原因、主体、是否、频率或其他明确值），必须
  should_extract=true 且 gate_reason=CONTEXT_DEPENDENT_REPLY。即使当前不能安全构造完整
  claim，也应返回 claims=[]，不得因短答本身不完整而改判 NO_MEMORY。只有明确不知道、
  拒绝回答或切换到无可记忆信息的话题时才 should_extract=false。
- 当 runtime_context.l0_route=CONTEXT_PASS 且包含 pending_memory_context 时，优先将
  previous_assistant_question 与 user_message 联合解释。极短、省略或代词回答不能仅因脱离问题
  后不完整而拒绝。Q+A 形成具体关系事实时返回 true；“我不知道”等无信息回答、拒答或无记忆的
  话题中断返回 false。若用户切换话题但独立提供新的 durable fact，抽取该独立事实，不要填入
  pending expected_slot。
- CONTEXT_PASS 语义示例（仅示范 Gate，不要求强行构造 claim）：
  Q“你们为什么吵架？”+A“消费观。” => true/CONTEXT_DEPENDENT_REPLY；
  Q“是谁先提的分手？”+A“她。” => true/CONTEXT_DEPENDENT_REPLY；
  Q“你们为什么吵架？”+A“我也不知道。” => false/NO_MEMORY；
  Q“你们为什么吵架？”+A“对了，她特别喜欢寿司。” => true/PREFERENCE，且寿司不是冲突原因。
- pending_memory_context 处于 active 且当前回答直接填充 expected_slot 时，必须将结构化的
  previous_assistant_question、expected_slot、topic 与当前回答共同解释为一个 proposition；不要把
  问答文本拼成新的 user_message。短回答本身可以成为完整记忆，claim 的 evidence_spans 仍只能逐字
  摘自当前 user_message，requires_inference=true。例：actor 问答“这次是谁先提的分手？”+“她。”
  表示 partner 是分手发起者；cause 问答“你们主要因为什么吵起来？”+“消费观。”表示消费观是冲突
  原因；interaction_state 问答“她后来主动道歉了吗？”+“没有。”表示 partner 没有主动道歉。
  当问题描述一次有边界的过去行为时，actor/cause/是否答案应补全为 interaction_event，不得为发起者、
  原因等开放属性伪造 relationship_state 或未注册 state_dimension；只有明确的当前状态才使用
  relationship_state。
  不得超出问题与回答发明事实。“不知道/不确定/不想说”等未知或拒答不构造 claim；发生话题切换时
  只抽取新话题中独立陈述的 durable fact，不得用它填充原 expected_slot。
- USER_BELIEF 只有在该判断持续、反复出现、涉及关系层面的长期判断，或对未来建议和决策仍有
  持续价值时才应保存。仅由一个刚发生的单次事件触发、用户明确描述为瞬间反应、且没有持续或
  重复证据的主观解释属于 TRANSIENT；不得仅因 perspective 是 user_belief，或出现“感觉/觉得/
  怀疑/是不是”，就判为 USER_BELIEF。若同一句同时包含“今天/突然”和过去数周或数月的持续
  belief，以持续时间语义为准，不得按瞬时猜测丢弃。
- 上述持续性门槛只用于区分主观 USER_BELIEF 与 TRANSIENT_BELIEF，不适用于具体事实、偏好、
  互动模式、关系变化或 CONTEXT_PASS 的明确 slot answer；这些类型继续按各自 Gate policy 判断。
- USER_BELIEF / TRANSIENT 边界示例（仅示范 Gate）：
  “她刚刚回消息有点冷，我突然觉得她是不是生气了。” => false/TRANSIENT；
  “她今天没主动找我，我一下觉得她是不是烦我了。” => false/TRANSIENT；
  “最近一个多月我一直觉得她在刻意回避和我聊未来。” => true/USER_BELIEF；
  “我这两三个月总担心她可能会离开这段关系。” => true/USER_BELIEF。
- PLANNED_EVENT 必须至少有明确的承诺、协调结果或可执行安排。仅有可能性、随口提及、尚未
  讨论或纯设想时 should_extract=false 且 gate_reason=NO_MEMORY。
- 若用户明确将一次过去事件限定为偶发、已有一次性解释、之前没有同类模式且无当前持续影响，
  该事件整体用 TRANSIENT，不要拆成 STABLE_FACT 或 interaction pattern。
- should_extract=false 时 claims 必须为空。should_extract=true 但无法安全形成合法 claim 时允许
  claims 暂时为空；不得为了填充 claims 发明事实。
- recent_conversation 只用于理解省略、指代和 Assistant 紧邻追问。Assistant 的问题不是用户事实；
  用户明确不知道、不愿回答或已切换到无记忆话题时仍应 should_extract=false。
- 只根据输入中的 user_message、recent_conversation、reference_time 和
  existing_active_memories 判断。
  不得猜测任何外部标签、标准答案、评测理由或 extraction hint。

只记录未来咨询或约会规划中仍可能有用的用户信息。没有值得记录的内容时 claims 返回空数组。
允许的 kind 只有：stable_fact、preference、interaction_event、
   interaction_pattern、advice_outcome、planned_event、action_intent、relationship_state。

每条 claim 必须额外提供 Predicate 治理字段：
- predicate_type 只能是 canonical 或 custom。
- 核心状态优先使用 canonical_predicate，且只能从以下受控词表选择：
  contact.status、relationship.stage、relationship.repair_status、confession.status、
  relationship.familiarity、relationship.contact_opportunity、relationship.conflict_status、
  relationship.interaction_reciprocity、partner.relationship_status、
  interaction.contact_frequency、interaction.topic_scope、interaction.channel、
  interaction.initiation_balance、interaction.response_engagement、
  interaction.emotional_disclosure、preference.general、preference.food.cuisine、
  preference.food.spiciness、preference.environment.noise、preference.activity.type、
  preference.budget.range。
- 无法可靠映射时必须使用 predicate_type=custom、custom_predicate=<英文 snake_case>，
  canonical_predicate 必须为 null；不得伪造新的 canonical 值。
- raw_predicate 保留你最初识别的英文谓词；predicate 继续输出该原始谓词以兼容旧结构。
- explicitness 只能是 explicit、strongly_implied、weakly_inferred、speculative。
- requires_inference 表示该声明是否需要跨句、指代或因果推断。
- 状态型记忆将 state_dimension/state_value 直接放在 claim 中；payload 中也保留同名字段。
- 不得决定数据库操作，不得输出或猜测 supersedes_id；Python 生命周期策略会选择目标。

核心规则：
1. interaction_event 只表示已经发生的一次有边界的具体互动，不等于低重要性或短期保留。
   interaction_pattern 是重复出现或对一段时间的汇总。最近三天每天发生也可以是 pattern。
   一次 event 不能推出 pattern；event 与 pattern 可以同时存在且互不覆盖。
2. planned_event 表示未来已经明确提到的活动、安排或可能发生的事件，例如“下周有小组讨论”、
   “后天要参加一个活动”、“周末准备和她见面”。它不是已经发生的 interaction_event。
   planned_event 必须填写未来时间字段或 payload.temporal_expression，并在 payload 中标记
   event_status=planned 或 tentative；如果能确定有效期，应填写 expires_at。
   payload.activity_type 使用简短、可复用的活动名称，payload.participants 使用 user、partner 等
   参与者数组。已有计划的 payload.plan_id 是计划生命周期标识，后续事件不得另造 ID。
   action_intent 表示用户已经决定或明确准备执行、但尚未给出日期的具体下一步，例如
   “我决定请她吃顿饭”“之后再认真聊消费观”。它必须是具体行动，payload.event_status 使用
   intended 或 tentative；“以后也许见面”这类泛泛愿望仍应丢弃。
   relationship_state 表示当前有效、以后可能变化的关系状态。payload 必须且只能描述一个
   state_dimension/state_value。支持的维度和值为：
   - relationship_familiarity: unfamiliar、low、moderate、high
   - contact_opportunity: low、moderate、high
   - contact_availability: unavailable、limited、available
   - conflict_status: active、cooling、repairing、resolved
   - interaction_reciprocity: low、mixed、high
   - partner_relationship_status: unknown、single、partnered、married
   “不太熟/刚认识”通常为 low，“熟了一些/比较熟/逐渐熟络”通常为 moderate，
   “已经很熟/彼此非常熟悉”通常为 high；只根据用户明确措辞判断。
   用户明确说“不知道/不确定对方是否单身”时，保存为 partner_relationship_status=unknown，
   subject=relationship、perspective=user_reported；只有用户提供对方明确表达或可靠事实时才能
   改成 single、partnered 或 married。接受邀约、回复积极、主动感谢等互动证据不能更新这个
   独立状态。
3. valence 只是 positive、negative、mixed、neutral、unknown 属性，不是记忆类型。
4. 用户说“我觉得她故意躲我”只能记为 user_belief，不能改写成对方确实在躲避。
5. evidence_spans 中每一项必须逐字摘自 user_message。summary 是规范化概括，不能替代原文证据，
   也不能补充用户没说过的动机或事实。
6. 普通知识问题、寒暄、仅在当下有用的操作指令、模型自己的建议都不记录。
   用户的咨询问题和目标要放入 discarded_spans，并标注 consultation_question 或 consultation_goal。
7. “准备、考虑、打算、下周想……”如果包含具体未来时间和事件，应记录为 planned_event；
   没有日期但已明确决定执行具体动作时记录为 action_intent。只有既没有明确时间、也没有形成
   具体行动承诺的泛泛愿望才放入 discarded_spans，标注 ephemeral。
8. 用户汇报采纳建议后的现实结果时使用 advice_outcome；例如调整做法后对方开心、双方和好、
   用户明确说某个办法有效，都应记录结果，并与其中的新约定或边界拆成独立 claims。
   用户回顾“上次、之前、那天、后来、回来后、结束后、做完/看完/吃完”等已经发生的活动时，
   抽取 interaction_event，并在 payload 中填写 event_status=completed、activity_type 和
   participants。
   若它与 existing_active_memories 中带 plan_id 的计划是同一活动，必须填写
   payload.related_plan_id；完成事件还要填写 payload.completes_plan_id。先比较计划 ID，再综合活动、
   参与者和时间，不得只因两个事件都发生在关系中就关联。确认邀约只表示 confirmed，不能写成
   completed；取消计划使用 event_status=cancelled 和 related_plan_id。
9. 时间字段使用 reference_time 解析相对时间，并输出带时区的 ISO 8601。planned_event 的未来时间
   优先放入 period_start/period_end；无法精确换算时保留 payload.temporal_expression。
   无法确定时留 null，
   temporal_precision 使用 exact、day、week、month、approximate、unknown。
10. time_kind 只能是 point、interval、timeless、unknown。稳定事实和偏好通常为 timeless。
10. subject 回答“这个 proposition 描述哪个实体或哪段关系”；perspective 回答“该 proposition 从谁的
   认知立场得知”；payload.actor 只记录事件或行为的执行者。三者相互独立。subject 只使用 user、
   partner、relationship，不要把第三方、belief holder 或 payload.actor 复制到 subject。不要仅因句子
   以“我觉得/我担心/我认为”开头就选择 subject=user。
   例如：“我觉得她可能在躲我”使用 subject=partner、perspective=user_belief；“我担心我们可能
   越来越疏远”使用 subject=relationship、perspective=user_belief；“我不喜欢酒吧”使用
   subject=user、perspective=user_reported；“她明确说自己不想结婚”使用 subject=partner、
   perspective=user_reported；“我们昨天因为消费观吵了一架”使用 subject=relationship、
   perspective=user_reported。当 proposition 明确回答“谁执行了这个动作”（如谁先提出分手）时，
   执行动作的 user 或 partner 是 subject；当 proposition 描述双方共同经历的争吵、联系、关系状态或
   interaction metric，且没有以单个 actor 为语义焦点时，subject=relationship。Advice 对关系产生的
   outcome 也使用 relationship，即使证据表现为 partner 的反应。predicate 使用稳定的英文 snake_case
   动词或状态，object 是可选的规范化宾语。
   subject hard contrasts：
   先按 proposition 的语义焦点判定：单个 actor 的个人行为/事件 => 该 user 或 partner；partner 的个人
   状态或意图（即使出现在关系语境、由用户转述或属于 user_belief）=> partner；只有双方共享的当前
   关系状态、共同事件或 interaction metric => relationship。USER_BELIEF 的 holder 只决定
   perspective，
   proposition target 才决定 subject。
   “是我先提出暂停联系” => user；“我们已经暂停联系两周” => relationship；
   “她先提了分手” => partner；“我们已经正式分手” => relationship；
   “过去三个月每次吵完都是我先道歉” => user；“她的消息持续变短”作为双方通信 metric => relationship；
   “她明确说自己是单身” => partner；“我们现在处于冷战” => relationship；
   “我觉得她不想结婚” => partner + user_belief；
   “我担心我们越来越疏远” => relationship + user_belief；
   “按建议讲清预算后她反馈更舒服”作为 advice outcome => relationship，partner 反应放 payload.actor。
11. perspective 只能是 user_reported、user_belief、model_inferred。
    除非非常必要，不使用 model_inferred。
    kind 与 perspective 是两个独立字段，绝不能把 user_belief、hearsay 或 subjective 当成 kind。
    例如“听说她最近经常和一个男生聊天”应使用 interaction_pattern + user_reported，
    并在 payload 中标记 source_type=hearsay、降低 confidence；“感觉那个男生也在追她”
    应使用 stable_fact 或 interaction_pattern + user_belief，不能写成对方确实在追求她。
    “我觉得他比我优秀”应记录为 user_belief，而不是客观的 partner 属性。
12. relationship_impact 只能是 improving、damaging、unchanged、unclear。
13. confidence 是 0 到 1，importance 和 intensity 是 1 到 5；无法判断 intensity 时为 null。
14. payload 放可复用的规范值，例如偏好可写 {"preference": "安静", "preference_type": "like"}。
    preference 和 preference_type 必须是单个字符串，禁止使用数组合并多个偏好。
15. 原子性是硬约束：一条 claim 只能表达一个能够被独立确认、更新或删除的信息。
    如果两个 proposition 未来可以被独立确认、更新、否定、supersede 或删除，就必须拆成两条
    claims。不要仅因多个维度共享 engagement、social inclusion 或 relationship progress 等宽泛主题
    就合并。回复速度、消息长度、主动发起、话题范围、互动渠道、社交邀请、介绍朋友均是可独立
    更新的维度；同时出现时分别抽取。
    例如“她会邀请我参加朋友聚会，也会主动把我介绍给她的朋友”必须拆成 social invitation 与
    friend introduction 两条；“我按建议先倾听，她愿意继续沟通，我们把矛盾说开了”应将建议效果
    advice_outcome 与当前冲突已解决的 relationship_state 分开。相反，同一 metric 的 baseline 与
    current（如隔天回复变为每天回复）仍是一条变化 claim，不要过度拆分。
    “回复越来越慢、消息越来越短、也不再主动开启话题”必须分别抽取 reply speed、message length、
    topic initiation 三条 claims，不得合成笼统的 response engagement。
    一条输入含多个事实、偏好、事件或模式时，必须输出多个 claim 对象。
    例如“我不喜欢酒吧，她喜欢话剧”必须拆成 user 的 dislike 和 partner 的 like 两条 preference。
    一个 interaction_pattern 可以用多个 evidence_spans 表达同一指标从 baseline 到 current 的变化，
    但不得把“用户喜欢对方”和“互动改善”等不同谓词合成一条。
    evidence_spans 必须选择支持该声明的最小原文片段，不能用整段原文掩盖多个独立谓词。
    熟悉度、接触机会、联系频率、话题范围、互动渠道和主动性是不同的可更新维度，同时陈述多个
    独立状态或指标时必须拆成不同 claims。interaction_pattern 的 metric 只能是一个字符串；
    不得用 context、summary 或长 evidence 把第二个独立指标藏进同一 claim。渠道、共同场景或
    社会关系可以作为一个主事实的必要限定，例如“线上联系频率提高”的主 metric 是
    contact_frequency、channel 是 online；限定信息不改变该 claim 只有一个可更新主维度。
16. supersedes_id 始终为 null 或省略。一次开心互动不能自动替代“过去一个月互动减少”这样的
    趋势；是否更新旧状态完全由 Python 生命周期策略决定。
17. recent_conversation 只用于理解指代和上下文，不得把 Assistant 的话当成用户确认的事实。
18. discarded_spans 每项包含 text 和 reason；reason 只能是 consultation_question、
    consultation_goal、ephemeral、no_durable_memory。text 必须逐字来自 user_message。
    discarded_spans 不得与任何 claim 的 evidence_spans 重叠；一个片段只要支持已保存声明，
    就不能同时标记为未写入。当前熟悉度和接触机会不是 ephemeral。
19. 每条 claim 必须包含 claim_id、kind、subject、predicate、predicate_type、summary、
    evidence_spans、explicitness、requires_inference。
    object、时间、情绪、影响、置信度、payload 和 supersedes_id 等字段仅在有信息时提供，
    未提供时由结构模型使用默认值。时间字段必须直接放在 claim 中，禁止创建 temporal 等嵌套对象。
    输出紧凑 JSON，不要缩进或重复解释字段含义。
20. summary 必须使用简体中文，predicate、metric 等规范键使用英文 snake_case。
21. interaction_pattern 的 payload 必须包含 metric。描述变化时还应包含 direction、baseline、
    current，描述重复行为时应包含 frequency。不得从单次 event 推导 pattern。
    “通常由谁主动联系/开启话题”的主 metric 统一使用 initiation_balance；渠道放入 channel，
    不要另造 contact_initiative、initiation_frequency 等 metric。
22. 拆分示例：“我喜欢她，最近一直主动找她聊天；起初她回应少，最近偶尔聊二十分钟。”
    必须得到三条：用户喜欢对方的 stable_fact、用户持续主动发起聊天的 interaction_pattern、
    双方聊天参与度从低到提高的 interaction_pattern。后两条是不同 metric，不得合并。
    “这是不是有进展、我该怎么办”属于 consultation_question 或 consultation_goal，不写入 claim。
23. “她平时很勤俭节约，买东西偏向经济实惠”应记录为 partner 的 preference 或稳定习惯；
    “可能是消费观不同”只能记录为 user_belief，不能当成已证实的关系原因。
24. “我考虑她的消费观选择平价餐厅，她很开心，我们和好了”应至少拆出 advice_outcome
    和已发生的 interaction_event；不要把“选择餐厅、对方开心、和好”合并为一条模糊记忆。
25. “听说她最近和一个男生经常一起聊天，感觉那个男孩子也在追求她，他比我优秀，
    你觉得我希望大吗”至少拆出三条原子信息：听闻的互动模式、用户对对方被追求的 user_belief、
    用户觉得自己不如对方的 user_belief；最后的希望问题只放入 discarded_spans。
26. “我俩最近被分到了同一个课程作业小组，下周有机会一起小组讨论，我想把握这次机会”
    至少拆出共享课程上下文的事实和一个 planned_event；“你有啥好方法吗”只放入 discarded_spans。
27. “我决定先请她吃顿饭，然后再认真聊一下消费观”没有日历时间，但包含两个明确动作，
    应拆成两个 action_intent；不得输出缺少时间的 planned_event，也不得把两步合为一条。
28. “刚认识不久但每天都能见面”必须拆成 relationship_familiarity=low 和
    contact_opportunity=high；熟悉程度低不等于接触机会少。
29. “认识很久但平时很少碰面，聊天基本只谈工作”至少拆成 contact_opportunity=low 和
    topic_scope=shared_work；“认识很久”本身不等于熟悉度高，除非用户明确说彼此很熟。
30. “已经很熟了，但主要在群里聊，偶尔才私聊”必须拆成 relationship_familiarity=high、
    interaction_channel 的当前模式以及私聊频率；不得合并成笼统的“关系较好”。
31. “我喜欢同班女生，我们在同一课程小组”至少拆成喜欢对方、同班关系和共享小组背景，
    不得把多个可独立更新的事实放进一个 stable_fact。
32. 对关系有意义的 interaction_event、interaction_pattern、advice_outcome 和 relationship_state
    应在 payload.relationship_evidence 中输出标准证据数组。每项只能包含：
    - dimension：familiarity、trust、investment、conflict、boundary 之一；
    - direction：support 或 oppose；
    - strength：0.05 到 1，表示该事实对该维度的支持或削弱力度；
    - confidence：0 到 1，表示这条证据解释本身的确定度，不得高于 claim.confidence；
    - rationale：简短英文 snake_case 理由。
    strength 与 confidence 含义不同，不得互相替代。同一原子事件可以产生多个维度的证据，
    但不能直接输出“双方关系很好”“已经恋爱”等关系阶段结论。
    例如，对方自愿到用户家吃饭并给出积极反馈，可产生 familiarity/support、trust/support，
    以及较弱的 investment/support；一次消费观争吵通常只产生 conflict/support，除非原文明示
    信任受损或边界被侵犯，否则不能自动削弱 trust 或增加 familiarity。participants 同时包含
    user 和 partner 本身不构成熟悉、信任或投入证据。planned_event 和 action_intent 尚未发生，
    不得用来投影当前关系证据。重复次数只能影响证据强度，不能直接写成关系状态。
33. payload.relationship_evidence 示例：
    [{"dimension":"familiarity","direction":"support","strength":0.65,
      "confidence":0.9,"rationale":"private_shared_interaction"},
     {"dimension":"trust","direction":"support","strength":0.8,
      "confidence":0.85,"rationale":"private_access_accepted"}]。
    无直接关系证据时省略该字段，不得为了填满结构而猜测。
34. relationship.stage 只表示用户明确陈述的当前正式关系阶段：“普通朋友/尚未正式交往”为
    acquaintance；“确认恋爱关系/正式在一起/我们在一起了/开始交往/成为情侣”为 dating；只有
    明确长期承诺、长期共同规划或共同生活安排才是 committed。否定、假设、希望、可能性或笼统的
    “关系更稳定”不能推出 dating/committed。“和好/矛盾解决”应使用 conflict_status=resolved，
    不能仅凭和好写 relationship.stage=reconciled；愿意见朋友、参加社交活动、见父母等开放世界事实
    使用 custom predicate，不能推成 relationship.stage 或 relationship_familiarity。若表白被接受且
    原文明说“我们在一起了”，应另存当前 relationship.stage=dating，不能只保存表白事件。
35. “没有回消息/不回应”属于 interaction.response_engagement，不是 contact_frequency；“终于回复、
    恢复正常聊天”应输出 response_engagement=responsive 或 contact.status=restored。contact_frequency
    不承载单次回复恢复事件，也不要用 resumed、available 等游离值代替现有 responsive/restored。
""".strip()

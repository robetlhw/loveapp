from __future__ import annotations

import json
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime
from time import perf_counter
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from loveapp.adapters.memory.openai_compatible import (
    _MEMORY_PROMPT_VERSION,
    _SYSTEM_PROMPT,
    _build_prompt,
)
from loveapp.application.memory_repair import MemoryResponseError, parse_memory_response
from loveapp.domain.memory import (
    AtomicExtraction,
    MemoryExtractionAttempt,
    MemoryItem,
    StoredMessage,
)
from loveapp.domain.runtime_context import PendingMemoryContext
from loveapp.ports.observability import TraceRecorder


class FlashDiagnosticResult(BaseModel):
    """Raw and post-repair views derived from one Flash completion."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model: str
    prompt_version: str = _MEMORY_PROMPT_VERSION
    raw_response: str | None = None
    raw_json_valid: bool = False
    raw_claims: list[dict[str, Any]] = Field(default_factory=list)
    raw_claim_count: int = 0
    raw_error: str | None = None
    finish_reason: str | None = None
    post_repair_extraction: AtomicExtraction | None = None
    repair_status: str = "none"
    repair_steps: str = ""
    invalid_claim_count: int = 0
    repaired_claim_count: int = 0
    discarded_claim_count: int = 0
    post_repair_error_category: str | None = None
    post_repair_error: str | None = None
    latency_ms: float = Field(ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


async def run_flash_raw_diagnostic(
    extractor: Any,
    text: str,
    *,
    reference_time: datetime,
    existing_memories: list[MemoryItem],
    conversation_history: list[StoredMessage],
    pending_memory_context: PendingMemoryContext | None,
    trace: TraceRecorder | None = None,
) -> FlashDiagnosticResult:
    """Call production-configured Flash once, then parse that exact response locally."""

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
    request: dict[str, Any] = {
        "model": extractor._model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": extractor._max_tokens,
    }
    if extractor._thinking is not None:
        request["extra_body"] = {"thinking": {"type": extractor._thinking}}
    started = perf_counter()
    measure = (
        trace.measure("memory_extraction_v1_flash_raw")
        if trace is not None
        else nullcontext({})
    )
    with measure as details:
        details["model"] = extractor._model
        details["tier"] = "flash"
        completion = await extractor._client.chat.completions.create(**request)
        choice = completion.choices[0]
        content = choice.message.content
        usage = getattr(completion, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
        details["prompt_tokens"] = prompt_tokens
        details["completion_tokens"] = completion_tokens
        details["total_tokens"] = total_tokens
        details["finish_reason"] = getattr(choice, "finish_reason", None)

        raw_json_valid, raw_claims, raw_error = _read_raw_claims(content)
        extraction: AtomicExtraction | None = None
        repair_status = "none"
        repair_steps = ""
        invalid_claim_count = repaired_claim_count = discarded_claim_count = 0
        error_category = error = None
        try:
            # Extraction diagnostics must observe the same Raw Claim boundary
            # as production; semantic normalization is evaluated downstream.
            parsed = parse_memory_response(
                content,
                source_text=text,
                validation_mode="raw",
            )
            parsed = replace(
                parsed,
                extraction=parsed.extraction.model_copy(
                    update={
                        "claims": [
                            claim.model_copy(
                                update={
                                    "raw_predicate": claim.raw_predicate or claim.predicate,
                                    "prompt_version": _MEMORY_PROMPT_VERSION,
                                    "extractor_model": extractor._model,
                                }
                            )
                            for claim in parsed.extraction.claims
                        ]
                    }
                ),
            )
            extraction = parsed.extraction
            repair_status = parsed.repair_status
            repair_steps = parsed.repair_steps
            invalid_claim_count = parsed.invalid_claim_count
            repaired_claim_count = parsed.repaired_claim_count
            discarded_claim_count = parsed.discarded_claim_count
        except MemoryResponseError as exc:
            error_category = exc.category
            error = str(exc)[:1000]
            repair_status = exc.repair_status
            repair_steps = exc.repair_steps
        latency_ms = (perf_counter() - started) * 1000
        details.update(
            {
                "latency_ms": latency_ms,
                "raw_claim_count": len(raw_claims),
                "claim_count": len(extraction.claims) if extraction is not None else 0,
                "repair_status": repair_status,
                "repair_steps": repair_steps,
                "invalid_claim_count": invalid_claim_count,
                "repaired_claim_count": repaired_claim_count,
                "discarded_claim_count": discarded_claim_count,
                "failure_category": error_category,
                "upgrade_reason": None,
            }
        )

    return FlashDiagnosticResult(
        model=extractor._model,
        raw_response=content,
        raw_json_valid=raw_json_valid,
        raw_claims=raw_claims,
        raw_claim_count=len(raw_claims),
        raw_error=raw_error,
        finish_reason=getattr(choice, "finish_reason", None),
        post_repair_extraction=extraction,
        repair_status=repair_status,
        repair_steps=repair_steps,
        invalid_claim_count=invalid_claim_count,
        repaired_claim_count=repaired_claim_count,
        discarded_claim_count=discarded_claim_count,
        post_repair_error_category=error_category,
        post_repair_error=error,
        latency_ms=latency_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


async def run_production_cascade_from_flash_result(
    cascade: Any,
    diagnostic: FlashDiagnosticResult,
    text: str,
    *,
    reference_time: datetime,
    existing_memories: list[MemoryItem],
    conversation_history: list[StoredMessage],
    pending_memory_context: PendingMemoryContext | None,
    trace: TraceRecorder | None = None,
) -> tuple[AtomicExtraction, list[MemoryExtractionAttempt]]:
    """Run the real Tiered extractor while replaying Layer A's exact Flash sample."""

    attempts: list[MemoryExtractionAttempt] = []
    flash = cascade._flash
    original_client = flash._client
    flash._client = _ReplayClient(diagnostic)
    try:
        extraction = await cascade.extract(
            text,
            reference_time=reference_time,
            existing_memories=existing_memories,
            conversation_history=conversation_history,
            pending_memory_context=pending_memory_context,
            trace=trace,
            attempt_callback=attempts.append,
        )
    finally:
        flash._client = original_client
    attempts = [
        attempt.model_copy(update={"duration_ms": diagnostic.latency_ms})
        if attempt.tier == "flash"
        else attempt
        for attempt in attempts
    ]
    return extraction, attempts


def _read_raw_claims(content: str | None) -> tuple[bool, list[dict[str, Any]], str | None]:
    if not content or not content.strip():
        return False, [], "empty_response"
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        return False, [], f"{type(exc).__name__}:{exc}"
    if not isinstance(payload, dict):
        return False, [], "root_not_object"
    claims = payload.get("claims", [])
    if not isinstance(claims, list):
        return False, [], "claims_not_array"
    return True, [claim for claim in claims if isinstance(claim, dict)], None


class _ReplayCompletions:
    def __init__(self, diagnostic: FlashDiagnosticResult) -> None:
        self._diagnostic = diagnostic

    async def create(self, **_: Any) -> Any:
        usage = SimpleNamespace(
            prompt_tokens=self._diagnostic.prompt_tokens,
            completion_tokens=self._diagnostic.completion_tokens,
            total_tokens=self._diagnostic.total_tokens,
        )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason=self._diagnostic.finish_reason,
                    message=SimpleNamespace(content=self._diagnostic.raw_response),
                )
            ],
            usage=usage,
        )


class _ReplayClient:
    def __init__(self, diagnostic: FlashDiagnosticResult) -> None:
        self.chat = SimpleNamespace(completions=_ReplayCompletions(diagnostic))


__all__ = [
    "FlashDiagnosticResult",
    "run_flash_raw_diagnostic",
    "run_production_cascade_from_flash_result",
]

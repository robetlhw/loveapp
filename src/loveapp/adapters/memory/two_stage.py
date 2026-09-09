"""Optional two-stage memory extraction.

Stage 1 is deliberately recall-oriented and only emits proposition hints.
Stage 2 converts those hints into the existing :class:`AtomicExtraction`
contract.  Neither stage is allowed to authorize a database mutation; target
resolution and lifecycle governance remain downstream Python code.
"""

from __future__ import annotations

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
from loveapp.domain.memory import (
    AtomicExtraction,
    CoarseExtraction,
    MemoryAttemptStatus,
    MemoryExtractionAttempt,
    MemoryItem,
    MemorySemanticGateReason,
    StoredMessage,
)
from loveapp.domain.runtime_context import PendingMemoryContext
from loveapp.ports.memory import MemoryAttemptCallback, MemoryExtractor
from loveapp.ports.observability import TraceRecorder

from .openai_compatible import (
    _build_prompt,
    _capture_usage,
    _flush_attempts,
    _safe_model_response_snapshot,
)

_TWO_STAGE_PROMPT_VERSION = "memory-v3.2-two-stage"


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
    ) -> None:
        self._model = model
        self._tier = tier
        self._max_tokens = max_tokens
        self._sdk_max_retries = max_retries
        self._thinking = thinking
        self._fallback = fallback
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
        trace: TraceRecorder | None = None,
        attempt_callback: MemoryAttemptCallback | None = None,
    ) -> AtomicExtraction:
        attempts: list[MemoryExtractionAttempt] = []
        started = perf_counter()
        try:
            coarse = await self._extract_coarse(
                text,
                reference_time=reference_time,
                existing_memories=existing_memories,
                conversation_history=conversation_history,
                pending_memory_context=pending_memory_context,
                trace=trace,
                attempts=attempts,
            )
            if not coarse.should_extract:
                extraction = AtomicExtraction(
                    should_extract=False,
                    gate_reason=coarse.gate_reason or MemorySemanticGateReason.NO_MEMORY,
                )
            else:
                extraction = await self._extract_detailed(
                    text,
                    reference_time=reference_time,
                    existing_memories=existing_memories,
                    conversation_history=conversation_history,
                    pending_memory_context=pending_memory_context,
                    coarse=coarse,
                    trace=trace,
                    attempts=attempts,
                )
            _flush_attempts(attempts, attempt_callback)
            return extraction
        except Exception as exc:
            _flush_attempts(attempts, attempt_callback)
            fallback_attempts: list[MemoryExtractionAttempt] = []
            fallback = await self._fallback_extract(
                text,
                reference_time=reference_time,
                existing_memories=existing_memories,
                conversation_history=conversation_history,
                pending_memory_context=pending_memory_context,
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
            if fallback is not None:
                return fallback
            if trace is not None:
                with trace.measure("memory_two_stage_failure") as details:
                    details["error"] = str(exc)[:1000]
                    details["elapsed_ms"] = (perf_counter() - started) * 1000
            return AtomicExtraction()

    async def _extract_coarse(
        self,
        text: str,
        *,
        reference_time: datetime,
        existing_memories: list[MemoryItem],
        conversation_history: list[StoredMessage],
        pending_memory_context: PendingMemoryContext | None,
        trace: TraceRecorder | None,
        attempts: list[MemoryExtractionAttempt],
    ) -> CoarseExtraction:
        prompt = _build_coarse_prompt(
            text,
            reference_time=reference_time,
            existing_memories=existing_memories,
            conversation_history=conversation_history,
            pending_memory_context=pending_memory_context,
        )
        details: dict[str, Any] = {
            "model": self._model,
            "tier": self._tier,
            "stage": "coarse",
            "prompt_version": _TWO_STAGE_PROMPT_VERSION,
        }
        started = perf_counter()
        try:
            content, usage = await self._call(prompt, _COARSE_SYSTEM_PROMPT, trace, details)
            _capture_usage(details, usage)
            coarse = CoarseExtraction.model_validate(_parse_json_object(content))
            details["proposition_count"] = len(coarse.propositions)
            details["should_extract"] = coarse.should_extract
            attempts.append(_build_attempt(details, started, status=MemoryAttemptStatus.COMPLETED))
            return coarse
        except Exception as exc:
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
        coarse: CoarseExtraction,
        trace: TraceRecorder | None,
        attempts: list[MemoryExtractionAttempt],
    ) -> AtomicExtraction:
        prompt = _build_detailed_prompt(
            text,
            reference_time=reference_time,
            existing_memories=existing_memories,
            conversation_history=conversation_history,
            pending_memory_context=pending_memory_context,
            coarse=coarse,
        )
        details: dict[str, Any] = {
            "model": self._model,
            "tier": self._tier,
            "stage": "detailed",
            "prompt_version": _TWO_STAGE_PROMPT_VERSION,
        }
        started = perf_counter()
        try:
            content, usage = await self._call(prompt, _DETAILED_SYSTEM_PROMPT, trace, details)
            _capture_usage(details, usage)
            raw = _parse_json_object(content)
            _reject_unsafe_detailed_keys(raw)
            parsed = parse_memory_response(
                json.dumps(raw, ensure_ascii=False),
                source_text=text,
                validation_mode="raw",
            )
            extraction = parsed.extraction.model_copy(
                update={
                    "should_extract": coarse.should_extract,
                    "gate_reason": coarse.gate_reason,
                }
            )
            details["claim_count"] = len(extraction.claims)
            details["repair_steps"] = parsed.repair_steps or None
            attempts.append(_build_attempt(details, started, status=MemoryAttemptStatus.COMPLETED))
            return extraction
        except Exception as exc:
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
            trace.measure(f"memory_two_stage_{details['stage']}")
            if trace
            else nullcontext(details)
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

    async def _fallback_extract(self, text: str, **kwargs: Any) -> AtomicExtraction | None:
        if self._fallback is None:
            return None
        try:
            return await self._fallback.extract(text, **kwargs)
        except Exception:
            return AtomicExtraction()

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
) -> str:
    context = json.loads(
        _build_prompt(
            text,
            reference_time,
            existing_memories,
            conversation_history,
            pending_memory_context,
        )
    )
    # Stage 1 sees semantic context, but not database identifiers.
    for item in context.get("existing_active_memories", []):
        item.pop("id", None)
    context["stage"] = "coarse_proposition_extraction"
    context["contract"] = {
        "candidate_kinds_are_hints": True,
        "semantic_roles": [
            "standalone_proposition",
            "attribute_update",
            "refinement_candidate",
            "state_assertion",
            "contextual_completion",
        ],
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
    coarse: CoarseExtraction,
) -> str:
    context = json.loads(
        _build_prompt(
            text,
            reference_time,
            existing_memories,
            conversation_history,
            pending_memory_context,
        )
    )
    for item in context.get("existing_active_memories", []):
        item.pop("id", None)
    context.update(
        {
            "stage": "batched_kind_aware_detailed_extraction",
            "coarse_extraction": coarse.model_dump(mode="json"),
            "contract": {
                "output": "AtomicExtraction",
                "one_response_for_all_propositions": True,
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


_COARSE_SYSTEM_PROMPT = """
你是 LoveApp Memory v3.2 的 Stage 1 语义拆分器。只输出一个 JSON 对象：
{"should_extract": true, "gate_reason": "...", "propositions": [], "discarded_spans": []}
把一条用户消息拆成可独立治理的 proposition；candidate_kinds 只能是候选提示，允许多个候选。
semantic_role 只能是 standalone_proposition、attribute_update、refinement_candidate、
state_assertion、contextual_completion。
严禁输出 canonical_predicate、target_memory_id、mutation_action、数据库 patch 或任何写入指令。
""".strip()


_DETAILED_SYSTEM_PROMPT = """
你是 LoveApp Memory v3.2 的 Stage 2 结构化提取器。只输出现有 AtomicExtraction JSON 合约。
一次响应处理全部 Stage 1 propositions，claims 必须保持原子性并保留 evidence_spans。
你只提供语义事实，不决定关系目标、生命周期或数据库操作。
严禁输出 target_memory_id、target_memory_ids、mutation_action、supersedes_id 或任意 DB patch；
canonical predicate 由后续 deterministic normalizer 治理，不能伪造未注册 canonical。
""".strip()


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

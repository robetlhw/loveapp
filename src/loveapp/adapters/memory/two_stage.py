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
    DiscardReason,
    MemoryAttemptStatus,
    MemoryExtractionAttempt,
    MemoryItem,
    MemoryKind,
    MemorySemanticGateReason,
    SemanticRole,
    StoredMessage,
)
from loveapp.domain.memory_semantic_units import (
    EnrichmentDraft,
    NewMemoryDraft,
    RefinementDraft,
    SemanticAtomicExtraction,
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

_TWO_STAGE_PROMPT_VERSION = "memory-event-enrichment-mvp-v1"


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
        trace: TraceRecorder | None = None,
        attempt_callback: MemoryAttemptCallback | None = None,
    ) -> AtomicExtraction:
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
                trace=trace,
                attempts=attempts,
            )
            if not coarse.should_extract:
                extraction = AtomicExtraction(
                    should_extract=False,
                    gate_reason=_negative_gate_reason(coarse.gate_reason),
                )
            else:
                current_stage = "detailed"
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
            diagnostic["final_extractor_used"] = "two_stage_native"
            diagnostic["final_claims"] = [
                claim.model_dump(mode="json") for claim in extraction.claims
            ]
            diagnostic["final_semantic_units"] = [
                unit.model_dump(mode="json")
                for unit in getattr(extraction, "semantic_units", [])
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
        self._last_diagnostic["stage2"]["called"] = True
        try:
            content, usage = await self._call(prompt, _DETAILED_SYSTEM_PROMPT, trace, details)
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
            extraction, repair_steps = _parse_detailed_extraction(
                raw,
                source_text=text,
                coarse=coarse,
            )
            if coarse.should_extract and not extraction.semantic_units:
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
            return await self._fallback.extract(text, **kwargs), None
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
            "new_proposition",
            "attribute_completion",
            "refinement",
            "contextual_completion",
            "uncertain",
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
                "output": "SemanticAtomicExtraction",
                "one_response_for_all_propositions": True,
                "semantic_unit_types": [
                    "new_memory",
                    "enrichment",
                    "refinement",
                ],
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
你是 LoveApp Memory Event Enrichment MVP 的 Stage 1 语义拆分器。只输出一个 JSON 对象：
{"should_extract": true, "gate_reason": "STABLE_FACT", "propositions": [], "discarded_spans": []}
gate_reason 必须严格使用以下大写枚举之一：STABLE_FACT、PREFERENCE、INTERACTION_PATTERN、
RELATIONSHIP_STATE、RELATIONSHIP_CHANGE、PARTIAL_CHANGE、USER_BELIEF、PLANNED_EVENT、
ACTION_INTENT、ADVICE_OUTCOME、COMPOUND_MEMORY、CONTEXT_DEPENDENT_REPLY、TRANSIENT、
SMALL_TALK、NO_MEMORY；不要填写解释句。
每个 proposition 必须严格包含 proposition_id、evidence_span、candidate_kinds、semantic_role；
candidate_kinds 只能使用 stable_fact、preference、interaction_event、interaction_pattern、
advice_outcome、planned_event、action_intent、relationship_state；允许多个候选。
semantic_role 只能是 new_proposition、attribute_completion、refinement、
contextual_completion、uncertain。
先判断当前文本是否描述新的、有边界的 occurrence；“今天/又/再次/这次”出现时仍需结合
完整事件语义判断，新的 occurrence 必须是 new_proposition。只有对已有事件属性的省略式补充才是
attribute_completion/contextual_completion。看到“因为”不能直接判为 completion。
必须结合 recent_conversation：Assistant 刚问“为什么吵架/当时什么反应”，用户回答原因或情绪，
即使句子很短也应 should_extract=true，并判 contextual_completion。已有“昨天我们吵架了”时，
“因为我迟到，她特别生气，现在已经不理我了”至少拆成旧 Event 的 cause、emotion completion，
以及独立 relationship_state。已有“我住上海”时，“具体在浦东”是 refinement，不是 Event completion。
严禁输出 canonical_predicate、target_memory_id、mutation_action、数据库 patch 或任何写入指令。
""".strip()


_DETAILED_SYSTEM_PROMPT = """
你是 LoveApp Memory Event Enrichment MVP 的 Stage 2 结构化提取器。只输出一个 JSON 对象：
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
严格按 Stage 1 role 映射：new_proposition 输出 new_memory；interaction_event 的
attribute_completion/contextual_completion 输出 enrichment；refinement 输出 refinement。
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
        if "proposition_id" not in item:
            item["proposition_id"] = f"p{index}"
            repairs.append("proposition_id_alias")
        if "evidence_span" not in item:
            evidence = item.get("source_span") or item.get("text")
            if isinstance(evidence, str) and evidence.strip():
                item["evidence_span"] = evidence.strip()
                repairs.append("evidence_span_alias")
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
                if any(str(kind) not in valid_kind_values for kind in raw["candidate_kinds"]):
                    repairs.append("candidate_kind_alias")
        role = _normalize_semantic_role(item.get("semantic_role"))
        if role is not None:
            if item.get("semantic_role") != role.value:
                repairs.append("semantic_role_alias")
            item["semantic_role"] = role.value
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
        "discarded_spans": _normalize_detailed_discarded(
            root.get("discarded_spans", [])
        ),
    }


def _parse_detailed_extraction(
    raw: dict[str, Any],
    *,
    source_text: str,
    coarse: CoarseExtraction,
) -> tuple[SemanticAtomicExtraction, str]:
    semantic_payloads = raw.get("semantic_units")
    if not isinstance(semantic_payloads, list):
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
    semantic_type = str(
        unit.get("semantic_type") or unit.get("type") or ""
    ).strip().casefold()
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
                if unit.get("attribute_name") in {
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
    for field in ("proposition_id", "kind", "candidate_kinds", "semantic_role"):
        if field not in claim and field in container:
            claim[field] = container[field]


def _normalize_detailed_claim(item: dict[str, Any], *, index: int) -> dict[str, Any]:
    claim = dict(item)
    claim_id = claim.get("claim_id") or claim.get("id") or claim.get("proposition_id")
    claim["claim_id"] = str(claim_id or f"c{index}")
    raw_kind = claim.get("kind") or claim.get("claim_type")
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
    claim["payload"] = payload
    return claim


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
            "state": SemanticRole.STATE_ASSERTION,
            "state_update": SemanticRole.ATTRIBUTE_UPDATE,
            "context": SemanticRole.CONTEXTUAL_COMPLETION,
            "enrichment": SemanticRole.ATTRIBUTE_UPDATE,
            "update": SemanticRole.ATTRIBUTE_UPDATE,
            "refinement": SemanticRole.REFINEMENT_CANDIDATE,
        }
        return aliases.get(normalized)


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

"""OpenAI-compatible semantic judge for long-tail Memory relations."""

from __future__ import annotations

import json
from contextlib import nullcontext
from time import perf_counter
from typing import Any, Literal

from openai import AsyncOpenAI
from pydantic import SecretStr, ValidationError

from loveapp.domain.memory import MemoryCandidate, MemoryItem
from loveapp.domain.memory_semantic_relation import SemanticRelationProposal
from loveapp.ports.observability import TraceRecorder


class OpenAICompatibleSemanticRelationJudge:
    """Propose relations only; callers must validate and must not commit them."""

    def __init__(
        self,
        *,
        api_key: SecretStr,
        base_url: str,
        model: str,
        timeout_seconds: float = 30,
        max_retries: int = 0,
        max_tokens: int = 1000,
        thinking: Literal["enabled", "disabled"] | None = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._thinking = thinking
        self._client = AsyncOpenAI(
            api_key=api_key.get_secret_value(),
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    async def propose_relation(
        self,
        *,
        incoming: MemoryCandidate,
        candidates: list[MemoryItem],
        trace: TraceRecorder | None = None,
    ) -> SemanticRelationProposal:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "incoming_memory": _incoming_payload(incoming),
                        "candidate_memories": [
                            _candidate_payload(candidate) for candidate in candidates
                        ],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ]
        started = perf_counter()
        measure = (
            trace.measure("memory_semantic_relation_model")
            if trace is not None
            else nullcontext({})
        )
        with measure as details:
            details["model"] = self._model
            details["candidate_count"] = len(candidates)
            request: dict[str, Any] = {
                "model": self._model,
                "messages": messages,
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": self._max_tokens,
            }
            if self._thinking is not None:
                request["extra_body"] = {"thinking": {"type": self._thinking}}
                details["thinking"] = self._thinking
            completion = await self._client.chat.completions.create(**request)
            usage = getattr(completion, "usage", None)
            prompt_tokens = _usage_value(usage, "prompt_tokens")
            completion_tokens = _usage_value(usage, "completion_tokens")
            total_tokens = _usage_value(usage, "total_tokens")
            if prompt_tokens is not None:
                details["prompt_tokens"] = prompt_tokens
            if completion_tokens is not None:
                details["completion_tokens"] = completion_tokens
            if total_tokens is not None:
                details["total_tokens"] = total_tokens
            content = completion.choices[0].message.content
            proposal = _parse_proposal(content).model_copy(
                update={
                    "judge_model": self._model,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "latency_ms": (perf_counter() - started) * 1000,
                }
            )
            details["relation"] = proposal.relation.value
            details["confidence"] = proposal.confidence
            details["same_semantic_dimension"] = proposal.same_semantic_dimension
            details["target_count"] = len(proposal.target_memory_ids)
            return proposal

    async def aclose(self) -> None:
        await self._client.close()


def _incoming_payload(candidate: MemoryCandidate) -> dict[str, object]:
    return {
        "kind": candidate.kind.value,
        "subject": candidate.subject,
        "summary": candidate.summary,
        "original_text": candidate.original_text,
        "evidence_spans": candidate.evidence_spans,
        "custom_predicate": candidate.custom_predicate,
        "perspective": candidate.perspective.value,
        "explicitness": candidate.explicitness.value,
        "confidence": candidate.confidence,
        "time_kind": candidate.time_kind.value,
        "occurred_at": _iso(candidate.occurred_at),
        "period_start": _iso(candidate.period_start),
        "period_end": _iso(candidate.period_end),
    }


def _candidate_payload(item: MemoryItem) -> dict[str, object]:
    return {
        "id": item.id,
        "kind": item.kind.value,
        "subject": item.subject,
        "summary": item.summary,
        "original_text": item.original_text,
        "evidence_spans": item.evidence_spans,
        "custom_predicate": item.custom_predicate,
        "status": item.status.value,
        "perspective": item.perspective.value,
        "explicitness": item.explicitness.value,
        "confidence": item.confidence,
        "time_kind": item.time_kind.value,
        "occurred_at": _iso(item.occurred_at),
        "period_start": _iso(item.period_start),
        "period_end": _iso(item.period_end),
    }


def _parse_proposal(content: str | None) -> SemanticRelationProposal:
    if not content or not content.strip():
        raise ValueError("semantic relation judge returned an empty response")
    cleaned = content.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        raise ValueError(
            "semantic relation judge returned invalid structured output"
        ) from None
    if not isinstance(payload, dict):
        raise ValueError(
            "semantic relation judge returned invalid structured output"
        ) from None
    relation = payload.get("relation")
    if isinstance(relation, str):
        payload["relation"] = relation.casefold().strip()
    try:
        return SemanticRelationProposal.model_validate(payload)
    except ValidationError:
        # ValidationError includes rejected input values; keep raw model output
        # out of traces and fail-closed diagnostics.
        raise ValueError(
            "semantic relation judge returned invalid structured output"
        ) from None


def _usage_value(usage: object, field: str) -> int | None:
    value = getattr(usage, field, None) if usage is not None else None
    return value if isinstance(value, int) and value >= 0 else None


def _iso(value: object) -> str | None:
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else None


_SYSTEM_PROMPT = """
You judge the semantic relation between one incoming open-world relationship memory and a
small retrieved candidate set. Return one strict JSON object with exactly these fields:
relation, target_memory_ids, same_semantic_dimension, confidence, reason.

relation must be one of: same, update, contradiction, complementary, unrelated, uncertain.
Target IDs must come only from candidate_memories. Select at most one target. Use uncertain
when no single target is safe.

Definitions:
- same: the same durable fact or sustained pattern restated without material change.
- update: the same independently changeable subject and semantic dimension has a newer,
  explicit sustained state/pattern that materially replaces the old one.
- contradiction: meanings conflict, but replacement authority or temporal evidence is weak.
- complementary: related facts in independently changeable dimensions can coexist.
- unrelated: no meaningful lifecycle relation.
- uncertain: evidence or target identity is insufficient.

Safety rules:
- Similar wording alone never proves update.
- A single event does not replace a sustained pattern or state.
- A pattern does not replace an event or a different state dimension.
- Social-circle integration and family integration are distinct dimensions.
- A belief or inference does not replace a stronger reported fact.
- Historical facts do not replace newer current facts.
- If multiple candidates are plausible, return uncertain with no target.
- False update is much more harmful than false add.

This is a semantic proposal only. Never describe or request a database mutation.
""".strip()


__all__ = ["OpenAICompatibleSemanticRelationJudge"]

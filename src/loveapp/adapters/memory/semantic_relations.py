"""OpenAI-compatible semantic judge for long-tail Memory relations."""

from __future__ import annotations

import json
import re
from contextlib import nullcontext
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal

from openai import AsyncOpenAI
from pydantic import SecretStr, ValidationError

from loveapp.domain.memory import ClaimRelation, MemoryCandidate, MemoryItem
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
        max_target_count: int = 1,
    ) -> None:
        if not 1 <= max_target_count <= 5:
            raise ValueError("max_target_count must be between 1 and 5")
        self._model = model
        self._max_tokens = max_tokens
        self._thinking = thinking
        self._max_target_count = max_target_count
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
        base_messages = [
            {
                "role": "system",
                "content": _system_prompt(max_target_count=self._max_target_count),
            },
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
            details["max_target_count"] = self._max_target_count
            details["attempt_count"] = 0
            details["retry_count"] = 0
            details["local_repair_applied"] = False
            messages = base_messages
            usage_totals: dict[str, int | None] = {
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
            }
            parsed: _ParsedProposal | None = None

            for attempt in (1, 2):
                details["attempt_count"] = attempt
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
                try:
                    completion = await self._client.chat.completions.create(**request)
                except Exception:
                    details[f"attempt_{attempt}_status"] = "transport_failed"
                    details["parse_status"] = "failed"
                    raise

                usage = getattr(completion, "usage", None)
                _accumulate_usage(usage_totals, usage, details, attempt)
                content = completion.choices[0].message.content
                try:
                    parsed = _parse_proposal_result(content)
                except ValueError:
                    details[f"attempt_{attempt}_status"] = "parse_failed"
                    if attempt == 1:
                        details["retry_count"] = 1
                        details["retry_reason"] = "structured_output_parse_failure"
                        messages = _json_retry_messages(base_messages, content)
                        continue
                    details["parse_status"] = "failed"
                    raise

                repaired = bool(parsed.repair_steps)
                details[f"attempt_{attempt}_status"] = "repaired" if repaired else "parsed"
                details["local_repair_applied"] = repaired
                if repaired:
                    details["local_repair_steps"] = ",".join(parsed.repair_steps)
                details["parse_status"] = "completed"
                policy_diagnostic = _target_policy_diagnostics(
                    parsed.proposal,
                    candidates=candidates,
                    max_target_count=self._max_target_count,
                )
                details["target_policy_reasons"] = ",".join(policy_diagnostic["reasons"])
                details["target_policy_rejected_ids"] = ",".join(
                    policy_diagnostic["rejected_ids"]
                )
                details["raw_target_count"] = len(parsed.proposal.target_memory_ids)
                details["raw_target_ids"] = ",".join(parsed.proposal.target_memory_ids[:5])
                proposal = _validate_target_policy(
                    parsed.proposal,
                    candidates=candidates,
                    max_target_count=self._max_target_count,
                )
                details["target_policy_status"] = (
                    "accepted" if policy_diagnostic["valid"] else "fail_closed"
                )
                break

            if parsed is None:  # pragma: no cover - loop exits or raises above
                raise ValueError("semantic relation judge returned invalid structured output")

            prompt_tokens = usage_totals["prompt_tokens"]
            completion_tokens = usage_totals["completion_tokens"]
            total_tokens = usage_totals["total_tokens"]
            proposal = proposal.model_copy(
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


@dataclass(frozen=True, slots=True)
class _ParsedProposal:
    proposal: SemanticRelationProposal
    repair_steps: tuple[str, ...]


def _parse_proposal_result(content: str | None) -> _ParsedProposal:
    if not content or not content.strip():
        raise ValueError("semantic relation judge returned an empty response")
    cleaned = content.strip()
    repair_steps: list[str] = []
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        fenced = _strip_json_fence(cleaned)
        if fenced is not None:
            try:
                payload = json.loads(fenced)
            except json.JSONDecodeError:
                payload = _extract_single_json_object(cleaned)
                repair_steps.append("embedded_json")
            else:
                repair_steps.append("json_fence")
        else:
            payload = _extract_single_json_object(cleaned)
            repair_steps.append("embedded_json")
    if not isinstance(payload, dict):
        raise ValueError("semantic relation judge returned invalid structured output") from None
    payload = dict(payload)
    relation = payload.get("relation")
    if isinstance(relation, str):
        normalized_relation = relation.casefold().strip()
        if normalized_relation != relation:
            repair_steps.append("relation_casefold")
        payload["relation"] = normalized_relation
    confidence = payload.get("confidence")
    if isinstance(confidence, str) and _NUMBER_PATTERN.fullmatch(confidence.strip()):
        payload["confidence"] = float(confidence)
        repair_steps.append("confidence_numeric_string")
    target_ids = payload.get("target_memory_ids")
    if isinstance(target_ids, str):
        payload["target_memory_ids"] = [target_ids]
        repair_steps.append("target_id_scalar")
    reason = payload.get("reason")
    if isinstance(reason, str) and len(reason) > _MAX_REASON_LENGTH:
        payload["reason"] = reason[:_MAX_REASON_LENGTH]
        repair_steps.append("reason_truncated")
    try:
        proposal = SemanticRelationProposal.model_validate(payload)
    except ValidationError:
        # ValidationError includes rejected input values; keep raw model output
        # out of traces and fail-closed diagnostics.
        raise ValueError("semantic relation judge returned invalid structured output") from None
    return _ParsedProposal(proposal=proposal, repair_steps=tuple(repair_steps))


def _strip_json_fence(content: str) -> str | None:
    match = _JSON_FENCE_PATTERN.fullmatch(content)
    return match.group("body").strip() if match is not None else None


def _extract_single_json_object(content: str) -> dict[str, object]:
    decoder = json.JSONDecoder()
    candidates: list[dict[str, object]] = []
    offset = 0
    while offset < len(content):
        start = content.find("{", offset)
        if start < 0:
            break
        try:
            value, length = decoder.raw_decode(content[start:])
        except json.JSONDecodeError:
            offset = start + 1
            continue
        if isinstance(value, dict):
            candidates.append(value)
        offset = start + max(length, 1)
    if len(candidates) != 1:
        raise ValueError("semantic relation judge returned invalid structured output") from None
    return candidates[0]


def _json_retry_messages(
    base_messages: list[dict[str, str]],
    content: str | None,
) -> list[dict[str, str]]:
    return [
        *base_messages,
        {"role": "assistant", "content": content or ""},
        {"role": "user", "content": _JSON_ONLY_RETRY_PROMPT},
    ]


def _accumulate_usage(
    totals: dict[str, int | None],
    usage: object,
    details: dict[str, Any],
    attempt: int,
) -> None:
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = _usage_value(usage, field)
        if value is None:
            continue
        details[f"attempt_{attempt}_{field}"] = value
        totals[field] = (totals[field] or 0) + value
        details[field] = totals[field]


def _usage_value(usage: object, field: str) -> int | None:
    value = getattr(usage, field, None) if usage is not None else None
    return value if isinstance(value, int) and value >= 0 else None


def _iso(value: object) -> str | None:
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else None


_NUMBER_PATTERN = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_MAX_REASON_LENGTH = 500
_JSON_FENCE_PATTERN = re.compile(
    r"```(?:json)?\s*(?P<body>.*?)\s*```",
    flags=re.IGNORECASE | re.DOTALL,
)
_JSON_ONLY_RETRY_PROMPT = """
Your previous response could not be parsed as the required schema. Return only one JSON
object with exactly these fields: relation, target_memory_ids, same_semantic_dimension,
confidence, reason. Keep reason at or below 500 characters. Do not include markdown
fences or explanatory text.
""".strip()


_SYSTEM_PROMPT = """
You judge the semantic relation between one incoming open-world relationship memory and a
small retrieved candidate set. Return one strict JSON object with exactly these fields:
relation, target_memory_ids, same_semantic_dimension, confidence, reason.

relation must be one of: same, update, contradiction, complementary, unrelated, uncertain.
Target IDs must come only from candidate_memories. A target is a memory that directly
expresses the same fact, independently changeable state/dimension, or directly related
event being judged. Related-by-topic, background context, or merely sharing a person is
not enough: related does not mean target. {target_instruction} Keep reason concise and at
or below 500 characters.

Definitions:
- same: the same durable fact or sustained pattern restated without material change.
- update: the same independently changeable subject and semantic dimension has a newer,
  explicit sustained state/pattern that materially replaces the old one.
- contradiction: the incoming and candidate facts cannot both be true for the same
  subject and semantic dimension. This is a factual relation only; do not downgrade it
  to uncertain because replacement authority or write permission is unclear.
- complementary: related facts in independently changeable dimensions can coexist.
- unrelated: no meaningful lifecycle relation.
- uncertain: the evidence is insufficient to determine whether the claims are the same,
  changing, conflicting, complementary, or unrelated, or no single direct target can be
  identified from a semantic identity/evidence match.

Safety rules:
- Similar wording alone never proves update.
- A single event does not replace a sustained pattern or state.
- A pattern does not replace an event or a different state dimension.
- Social-circle integration and family integration are distinct dimensions.
- A belief or inference does not replace a stronger reported fact.
- Historical facts do not replace newer current facts.
- {ambiguity_instruction}
- For same, update, complementary, or contradiction, return only the minimal direct
  target set. Do not include every related candidate. For unrelated or uncertain, return
  an empty target_memory_ids array.
- Do not use write-risk considerations to change the factual relation label; write
  authorization belongs to the downstream validator.

This is a semantic proposal only. Never describe or request a database mutation.
""".strip()


def _system_prompt(*, max_target_count: int) -> str:
    if max_target_count == 1:
        target_instruction = (
            "Select at most one target. Use uncertain when no single direct semantic target "
            "can be identified from the evidence."
        )
        ambiguity_instruction = (
            "If multiple candidates are plausible, return uncertain with no target."
        )
    else:
        target_instruction = (
            f"Select at most {max_target_count} targets only when the incoming claim "
            "explicitly contains multiple claims or one event that directly maps to each "
            "target. Preserve the minimal independently supported target set; otherwise "
            "use uncertain with no target."
        )
        ambiguity_instruction = (
            "Multiple targets are valid only for an explicit multi-claim incoming memory; "
            "semantic ambiguity without explicit plural support remains uncertain."
        )
    return _SYSTEM_PROMPT.format(
        target_instruction=target_instruction,
        ambiguity_instruction=ambiguity_instruction,
    )


def _validate_target_policy(
    proposal: SemanticRelationProposal,
    *,
    candidates: list[MemoryItem],
    max_target_count: int,
) -> SemanticRelationProposal:
    """Apply only structural target-policy guards before downstream validation.

    The model still owns semantic relation classification.  These guards prevent a
    malformed or over-broad proposal from being mistaken for an authorized target set:
    target IDs must come from the supplied candidates, be unique, and obey relation
    cardinality.  A violation fails closed as ``uncertain`` with no targets.
    """

    diagnostic = _target_policy_diagnostics(
        proposal,
        candidates=candidates,
        max_target_count=max_target_count,
    )
    if diagnostic["valid"]:
        return proposal
    return proposal.model_copy(
        update={
            "relation": ClaimRelation.UNCERTAIN,
            "target_memory_ids": [],
            "same_semantic_dimension": False,
            "confidence": 0.0,
            "reason": "Target policy violation; proposal failed closed.",
        }
    )


def _target_policy_diagnostics(
    proposal: SemanticRelationProposal,
    *,
    candidates: list[MemoryItem],
    max_target_count: int,
) -> dict[str, Any]:
    """Explain structural target-policy decisions without semantic re-judging."""

    candidate_ids = {candidate.id for candidate in candidates}
    target_ids = list(proposal.target_memory_ids)
    targeted = proposal.relation in {
        # Keep this local to avoid coupling the adapter to application policy constants.
        ClaimRelation.SAME,
        ClaimRelation.UPDATE,
        ClaimRelation.CONTRADICTION,
        ClaimRelation.COMPLEMENTARY,
    }
    reasons: list[str] = []
    if len(target_ids) > max_target_count:
        reasons.append("target_count_exceeds_max")
    if len(target_ids) != len(set(target_ids)):
        reasons.append("duplicate_target_ids")
    rejected_ids = sorted({target_id for target_id in target_ids if target_id not in candidate_ids})
    if rejected_ids:
        reasons.append("unknown_target_id")
    if targeted and not target_ids:
        reasons.append("target_required_for_relation")
    if not targeted and target_ids:
        reasons.append("target_forbidden_for_relation")
    return {
        "valid": not reasons,
        "reasons": reasons,
        "rejected_ids": rejected_ids,
    }


__all__ = ["OpenAICompatibleSemanticRelationJudge"]

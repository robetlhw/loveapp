from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import nullcontext
from time import perf_counter
from typing import Any, Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from loveapp.ports.observability import TraceRecorder


class ExtractionAlignmentPair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_index: int = Field(ge=0)
    actual_index: int = Field(ge=0)
    proposition_equivalent: bool
    semantic_match: bool
    evidence_support: Literal["PASS", "FAIL", "UNCERTAIN"]
    reason: str = Field(max_length=300)


class ExtractionAlignmentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matches: list[ExtractionAlignmentPair] = Field(default_factory=list)
    unmatched_expected: list[int] = Field(default_factory=list)
    unmatched_actual: list[int] = Field(default_factory=list)
    over_merge_actual_indices: list[int] = Field(default_factory=list)
    over_split_expected_indices: list[int] = Field(default_factory=list)
    uncertain: bool = False
    reason: str = Field(default="", max_length=500)


class OpenAICompatibleExtractionAlignmentJudge:
    """Evaluation-only case-level semantic claim alignment judge."""

    def __init__(
        self,
        *,
        api_key: SecretStr,
        base_url: str,
        model: str,
        timeout_seconds: float = 60,
        max_retries: int = 0,
        max_tokens: int = 1800,
        thinking: Literal["enabled", "disabled"] | None = "disabled",
    ) -> None:
        self.model = model
        self._max_tokens = max_tokens
        self._thinking = thinking
        self.call_count = 0
        self.failure_count = 0
        self.latencies_ms: list[float] = []
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.failure_diagnostics: list[dict[str, str | None]] = []
        self._client = AsyncOpenAI(
            api_key=api_key.get_secret_value(),
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    async def align(
        self,
        *,
        user_message: str,
        pending_memory_context: Mapping[str, Any] | None,
        expected_claims: list[Mapping[str, Any]],
        actual_claims: list[Mapping[str, Any]],
        trace: TraceRecorder | None = None,
    ) -> ExtractionAlignmentResult:
        if not expected_claims and not actual_claims:
            return ExtractionAlignmentResult()
        payload = {
            "user_message": user_message,
            "pending_memory_context": pending_memory_context,
            "expected_claims": expected_claims,
            "actual_claims": actual_claims,
        }
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _ALIGNMENT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": self._max_tokens,
        }
        if self._thinking is not None:
            request["extra_body"] = {"thinking": {"type": self._thinking}}
        measure = (
            trace.measure("memory_extraction_semantic_alignment")
            if trace is not None
            else nullcontext({})
        )
        self.call_count += 1
        started = perf_counter()
        content: str | None = None
        try:
            with measure as details:
                details["model"] = self.model
                details["tier"] = "semantic_matcher"
                details["expected_claim_count"] = len(expected_claims)
                details["actual_claim_count"] = len(actual_claims)
                completion = await self._client.chat.completions.create(**request)
                usage = getattr(completion, "usage", None)
                if usage is not None:
                    details["prompt_tokens"] = int(getattr(usage, "prompt_tokens", 0) or 0)
                    details["completion_tokens"] = int(
                        getattr(usage, "completion_tokens", 0) or 0
                    )
                    details["total_tokens"] = int(getattr(usage, "total_tokens", 0) or 0)
                    self.prompt_tokens += details["prompt_tokens"]
                    self.completion_tokens += details["completion_tokens"]
                    self.total_tokens += details["total_tokens"]
                content = completion.choices[0].message.content
                result = _parse_alignment_result(
                    content,
                    expected_count=len(expected_claims),
                    actual_count=len(actual_claims),
                )
                details["match_count"] = sum(pair.semantic_match for pair in result.matches)
                details["claim_count"] = len(actual_claims)
                details["uncertain"] = result.uncertain
                details["latency_ms"] = (perf_counter() - started) * 1000
                return result
        except Exception as exc:
            self.failure_count += 1
            self.failure_diagnostics.append(
                {
                    "error": f"{type(exc).__name__}: {exc}"[:1000],
                    "response": content[:2000] if content else None,
                }
            )
            return ExtractionAlignmentResult(
                unmatched_expected=list(range(len(expected_claims))),
                unmatched_actual=list(range(len(actual_claims))),
                uncertain=True,
                reason=f"semantic_alignment_failed:{type(exc).__name__}",
            )
        finally:
            self.latencies_ms.append((perf_counter() - started) * 1000)

    async def aclose(self) -> None:
        await self._client.close()


def _parse_alignment_result(
    content: str | None,
    *,
    expected_count: int,
    actual_count: int,
) -> ExtractionAlignmentResult:
    if not content or not content.strip():
        raise ValueError("semantic alignment judge returned an empty response")
    raw = content.strip().lstrip("\ufeff")
    if raw.startswith("```"):
        lines = raw.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        raw = "\n".join(lines).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("semantic alignment response is not a JSON object")
    result = ExtractionAlignmentResult.model_validate(json.loads(raw[start : end + 1]))
    expected_indices: set[int] = set()
    actual_indices: set[int] = set()
    repaired_matches: list[ExtractionAlignmentPair] = []
    for index in result.unmatched_expected:
        if index < 0 or index >= expected_count:
            raise ValueError("unmatched expected index is out of range")
    for index in result.unmatched_actual:
        if index < 0 or index >= actual_count:
            raise ValueError("unmatched actual index is out of range")
    for index in result.over_merge_actual_indices:
        if index < 0 or index >= actual_count:
            raise ValueError("over-merge actual index is out of range")
    for index in result.over_split_expected_indices:
        if index < 0 or index >= expected_count:
            raise ValueError("over-split expected index is out of range")
    equivalent_expected_by_actual: dict[int, set[int]] = {}
    for pair in result.matches:
        if not pair.proposition_equivalent:
            continue
        equivalent_expected_by_actual.setdefault(pair.actual_index, set()).add(
            pair.expected_index
        )
    inferred_over_merge_indices = {
        actual_index
        for actual_index, paired_expected in equivalent_expected_by_actual.items()
        if len(paired_expected) > 1
    }
    over_merge_indices = set(result.over_merge_actual_indices) | inferred_over_merge_indices
    for pair in result.matches:
        if (
            pair.expected_index < 0
            or pair.expected_index >= expected_count
            or pair.actual_index < 0
            or pair.actual_index >= actual_count
        ):
            raise ValueError("semantic alignment returned an out-of-range index")
        if not pair.proposition_equivalent:
            continue
        if pair.expected_index in expected_indices:
            if pair.expected_index not in result.over_split_expected_indices:
                raise ValueError("semantic alignment must be one-to-one")
            continue
        if pair.actual_index in actual_indices:
            if pair.actual_index not in over_merge_indices:
                raise ValueError("semantic alignment must be one-to-one")
            continue
        expected_indices.add(pair.expected_index)
        actual_indices.add(pair.actual_index)
        repaired_matches.append(pair)
    return result.model_copy(
        update={
            "matches": repaired_matches,
            "unmatched_expected": sorted(set(range(expected_count)) - expected_indices),
            "unmatched_actual": sorted(set(range(actual_count)) - actual_indices),
            "over_merge_actual_indices": sorted(over_merge_indices),
        }
    )


_ALIGNMENT_SYSTEM_PROMPT = """
You evaluate extraction only. Return exactly one JSON object matching this shape:
{
  "matches": [{
    "expected_index": 0,
    "actual_index": 0,
    "proposition_equivalent": true,
    "semantic_match": true,
    "evidence_support": "PASS",
    "reason": "brief reason"
  }],
  "unmatched_expected": [],
  "unmatched_actual": [],
  "over_merge_actual_indices": [],
  "over_split_expected_indices": [],
  "uncertain": false,
  "reason": "brief case-level reason"
}

Align the whole case once. A claim may appear in at most one pair, except that an actual claim may
appear in multiple pairs when it demonstrably merges independently updateable expected
propositions; list that actual index in over_merge_actual_indices. Pair claims when they address
the same proposition even if kind, subject, or perspective is wrong. proposition_equivalent
concerns meaning, not predicate spelling. Set semantic_match=true only when the proposition and
subject are compatible and the actual claim does not turn a user belief or hearsay into a reported
fact. A kind mismatch alone does not make semantic_match false; it is scored separately. Never
compare canonical_predicate,
state_dimension, state_value, relation, admission, lifecycle, or supersedes fields.

Mark OVER_MERGE when one actual claim collapses independently updateable expected propositions.
Mark OVER_SPLIT when several actual claims redundantly split one expected proposition. An abstract
claim does not automatically cover several expected claims. evidence_support is PASS only when the
actual evidence spans genuinely support that paired proposition in the user message or structured
pending context. Use UNCERTAIN instead of guessing.
""".strip()


__all__ = [
    "ExtractionAlignmentPair",
    "ExtractionAlignmentResult",
    "OpenAICompatibleExtractionAlignmentJudge",
]

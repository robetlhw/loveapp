"""OpenAI-compatible semantic judge for long-tail Memory relations."""

from __future__ import annotations

import json
import re
from contextlib import nullcontext
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

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
            proposal: SemanticRelationProposal | None = None

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
                    parsed.output,
                    candidates=candidates,
                    max_target_count=self._max_target_count,
                )
                details["target_policy_reasons"] = ",".join(policy_diagnostic["reasons"])
                details["target_policy_rejected_ids"] = ",".join(
                    policy_diagnostic["rejected_ids"]
                )
                details["raw_target_count"] = len(parsed.output.target_memory_ids)
                details["raw_target_ids"] = ",".join(parsed.output.target_memory_ids[:5])
                details["candidate_relation_count"] = len(
                    parsed.output.candidate_relations
                )
                details["candidate_relations_json"] = json.dumps(
                    [
                        {
                            "memory_id": item.memory_id,
                            "relation": item.relation.value,
                            "is_direct_target": item.is_direct_target,
                            "confidence": item.confidence,
                        }
                        for item in parsed.output.candidate_relations
                    ],
                    separators=(",", ":"),
                )
                details["reported_overall_relation"] = (
                    parsed.output.overall_relation.value
                )
                proposal = _validate_target_policy(
                    parsed.output,
                    candidates=candidates,
                    max_target_count=self._max_target_count,
                )
                details["derived_overall_relation"] = proposal.relation.value
                details["target_policy_status"] = (
                    "accepted" if policy_diagnostic["valid"] else "fail_closed"
                )
                break

            if parsed is None or proposal is None:  # pragma: no cover - loop exits or raises
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
    output: _CandidateWiseRelationOutput
    repair_steps: tuple[str, ...]


class _CandidateRelationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(min_length=1, max_length=200)
    relation: ClaimRelation
    is_direct_target: bool = Field(strict=True)
    confidence: float = Field(ge=0, le=1)


class _CandidateWiseRelationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_relations: list[_CandidateRelationOutput] = Field(max_length=5)
    target_memory_ids: list[str] = Field(max_length=5)
    overall_relation: ClaimRelation
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)


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
    overall_relation = payload.get("overall_relation")
    if isinstance(overall_relation, str):
        normalized_relation = overall_relation.casefold().strip()
        if normalized_relation != overall_relation:
            repair_steps.append("overall_relation_casefold")
        payload["overall_relation"] = normalized_relation
    candidate_relations = payload.get("candidate_relations")
    candidate_relation_repaired = False
    candidate_confidence_repaired = False
    if isinstance(candidate_relations, list):
        normalized_candidates: list[object] = []
        for candidate_relation in candidate_relations:
            if not isinstance(candidate_relation, dict):
                normalized_candidates.append(candidate_relation)
                continue
            normalized_candidate = dict(candidate_relation)
            relation = normalized_candidate.get("relation")
            if isinstance(relation, str):
                normalized_relation = relation.casefold().strip()
                if normalized_relation != relation:
                    candidate_relation_repaired = True
                normalized_candidate["relation"] = normalized_relation
            candidate_confidence = normalized_candidate.get("confidence")
            if isinstance(candidate_confidence, str) and _NUMBER_PATTERN.fullmatch(
                candidate_confidence.strip()
            ):
                normalized_candidate["confidence"] = float(candidate_confidence)
                candidate_confidence_repaired = True
            normalized_candidates.append(normalized_candidate)
        payload["candidate_relations"] = normalized_candidates
    if candidate_relation_repaired:
        repair_steps.append("candidate_relation_casefold")
    if candidate_confidence_repaired:
        repair_steps.append("candidate_confidence_numeric_string")
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
        output = _CandidateWiseRelationOutput.model_validate(payload)
    except ValidationError:
        # ValidationError includes rejected input values; keep raw model output
        # out of traces and fail-closed diagnostics.
        raise ValueError("semantic relation judge returned invalid structured output") from None
    return _ParsedProposal(output=output, repair_steps=tuple(repair_steps))


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
object with exactly these fields: candidate_relations, target_memory_ids,
overall_relation, confidence, reason. candidate_relations must contain exactly one entry
for every supplied candidate, and every entry must contain exactly memory_id, relation,
is_direct_target, and confidence. Keep reason at or below 500 characters. Do not include
markdown fences or explanatory text.
""".strip()


_SYSTEM_PROMPT = """
You judge the semantic relation between one incoming open-world relationship memory and a
small retrieved candidate set. Classify incoming_memory against every candidate separately,
then construct the minimal target set. Do this in one response and one model call.

Return one strict JSON object with exactly these fields:
- candidate_relations: exactly one object for every candidate_memory, in input order. Each
  object has exactly memory_id, relation, is_direct_target, confidence.
- is_direct_target: true only when the incoming claim directly repeats, modifies, adds to,
  or negates that candidate claim. Semantic or contextual relevance alone is false.
- target_memory_ids: the minimal set consisting exactly of candidates marked
  is_direct_target=true.
- overall_relation: your summary label. Code derives the authoritative overall relation
  from the selected candidate relations, so this field never selects targets.
- confidence: confidence in the overall semantic assessment.
- reason: a concise reason of at most 500 characters.

Every relation must be one of: same, update, contradiction, complementary, unrelated,
uncertain. Every memory_id and target ID must come from candidate_memories.
{target_instruction}

Definitions:
- same: the same durable fact, state, pattern, or event identity restated without material
  change. New duration, timing, frequency, scope, cause, or other material qualifiers are
  claim-level additions, not mere restatement. The same event type at another time is not
  the same event.
- update: the same independently changeable subject and semantic dimension has a newer,
  explicit sustained state/pattern that materially replaces the old one.
- contradiction: the incoming and candidate facts cannot both be true for the same
  subject and semantic dimension. This is a factual relation only; do not downgrade it
  to uncertain because replacement authority or write permission is unclear.
- complementary: the incoming claim directly adds claim-level semantic content that can
  coexist with and complete or qualify the candidate. A new instance of the same event
  type is complementary, not same. Topic or contextual relevance alone is not
  complementary.
- unrelated: no direct SAME, UPDATE, CONTRADICTION, or COMPLEMENTARY relation. Claims may
  still share a person, topic, event, or contextual theme.
- uncertain: the evidence is insufficient to determine whether the claims are the same,
  changing, conflicting, complementary, or unrelated, or no single direct target can be
  identified from a semantic identity/evidence match.

Semantic classification rules:
- Similar wording alone never proves update.
- Two event instances at different times are not SAME even when their event type matches;
  classify a distinct compatible event as COMPLEMENTARY.
- A sustained pattern and one event instance are never SAME.
- A single event does not replace a sustained pattern or state.
- A pattern does not replace an event or a different state dimension.
- Social-circle integration and family integration are distinct dimensions.
- Historical facts do not replace newer current facts.
- {ambiguity_instruction}
- Related != Target. Semantic similarity != Target. Same topic != Target. Same subject !=
  Target. Same domain != Target. Possible explanation != Target. Contextually useful for
  advice != Target.
- COMPLEMENTARY does not automatically make a candidate a direct target. It is valid to
  classify a candidate as complementary while setting is_direct_target=false.
- A COMPLEMENTARY candidate is a direct target when the incoming claim itself adds a
  specific detail to that candidate, reports a clearly linked new instance of its event
  series, or gives a concrete event instance of its sustained pattern. In those cases set
  is_direct_target=true. Do not confuse this with merely useful background.
- This pattern/event rule is directional: an incoming concrete event may instantiate an
  old sustained pattern, but an incoming newly asserted pattern does not directly target
  one isolated historical event merely because that event is an example.
- A candidate may enter target_memory_ids only when is_direct_target=true. Unrelated and
  uncertain candidates must always have is_direct_target=false.
- If one candidate completely captures the direct semantic relationship, do not include
  additional candidates merely because they share the same topic, person, event, or
  contextual theme.
- Prefer the narrow claim that the incoming directly extends. When an exact prior event or
  state captures the relation, broader patterns, possible explanations, and other nearby
  events remain non-direct. Use the broader pattern only when it is itself the claim being
  instantiated and no narrower candidate captures that relation.
- Multi-target is valid only when the incoming claim itself explicitly and independently
  acts on every selected semantic target. Never use multi-target to express ambiguity.
- For an explicit multi-clause incoming claim, evaluate each clause independently. If one
  clause changes daily chat and another changes video contact, both matching old claims are
  direct targets. Do not omit the second target merely because its candidate relation is
  complementary rather than update.
- A different condition or time window does not by itself make a candidate non-direct when
  an explicit clause changes the same independently measurable behavior, such as video
  contact or proactive sharing. Judge the changed metric, not only matching qualifiers.
- Multiple paraphrases of one proposition do not create multiple independent targets.
  Mark only the candidate that best matches the incoming claim's evidence scope as direct;
  keep redundant nearby paraphrases non-direct.
- Semantic relation != write authority. A weak belief can semantically contradict a strong
  fact even when it cannot replace that fact. Proposed status, low confidence, user belief,
  and weak evidence affect downstream write permission, not the factual relation label.
- The downstream Validator, not this Judge, decides whether update, merge, or supersession
  is authorized.
- When an incoming claim is explicitly weak, subjective, and spans several inferred
  dimensions without directly denying an observed candidate fact, classify that candidate
  as uncertain with is_direct_target=false rather than forcing contradiction.
- When a candidate clearly describes an earlier baseline and the incoming claim explicitly
  describes a newer sustained state in the same dimension, classify it as update, not
  contradiction. Contradiction is for incompatible claims about the same state/time without
  a supported transition.

Direct COMPLEMENTARY examples:
1. Old: "When stressed, she likes walking alone." Incoming: "She usually walks by the river
   near work for about forty minutes." This adds a specific detail to the old habit:
   complementary, is_direct_target=true.
2. Old: "Last week she commented on my post once." Incoming: "Yesterday she commented on
   another post again." This is a linked new event instance: complementary,
   is_direct_target=true.
3. Old pattern: "When busy, her replies become short." Incoming event: "Yesterday she was
   exhausted and sent only a few short replies." This event instantiates the pattern:
   complementary, is_direct_target=true; never same.
4. Old: "Quiet hotel rooms matter to her." Incoming: "She would stay farther from sights
   to get a quieter room." This adds a compatible trade-off/detail, so it is complementary,
   not update, and is_direct_target=true.
5. Old: "When upset, she processes things alone for a while." Incoming: "She usually takes
   one night and speaks the next day." The concrete duration and sequence are new material
   qualifiers: complementary, not same, and is_direct_target=true.

Non-direct COMPLEMENTARY example:
- Incoming: "She rarely initiates chats lately." Candidate: "When work is busy, her replies
  become short." The candidate may be useful context, but it does not complete or qualify
  the incoming initiation-frequency claim: is_direct_target=false.

UNRELATED boundary examples:
- Incoming preference: "She likes history podcasts before sleep." Candidate preference:
  "She often listens to interview and culture podcasts." These are different coexisting
  content preferences under one medium; the incoming claim does not qualify the candidate,
  so relation is unrelated and is_direct_target=false.
- Incoming pattern: "We recently started cooking together every week." Candidate event:
  "We cooked dinner together once last week." One past event neither defines nor receives
  the newly asserted recurring pattern, so relation is unrelated and
  is_direct_target=false.

Weak multi-dimensional inference example:
- Incoming infers that she may not want to meet or initiate chats but explicitly says there
  is insufficient evidence. A candidate reports observed chat initiations or one recent
  conversation. Internal willingness and observed behavior are different dimensions, so
  they can coexist; use uncertain with is_direct_target=false. Do not turn a possible desire
  or motive into a denial of observed behavior unless the incoming explicitly asserts that
  the behavior itself stopped or changed.

Contradiction examples (classify the old candidate as contradiction and target it):
1. Old: "She explicitly said she is currently single." Incoming: "I suspect she may
   actually have another partner, but I have no evidence." The new claim is weak, yet the
   claims conflict; relation is contradiction. Validator decides write authority.
2. Old: "She has always preferred quiet small restaurants." Incoming: "I feel she may
   actually prefer very noisy places." Relation is contradiction, not uncertain.
3. Old: "We confirmed that we will hike after returning home." Incoming: "I think she may
   no longer want to go, but she has not explicitly cancelled." Relation is contradiction;
   lack of cancellation affects mutation permission only.

Output example:
{{"candidate_relations":[{{"memory_id":"M1","relation":"update",
"is_direct_target":true,"confidence":0.93}},
{{"memory_id":"M2","relation":"complementary","is_direct_target":false,
"confidence":0.90}}],
"target_memory_ids":["M1"],"overall_relation":"update","confidence":0.93,
"reason":"M1 is the only direct same-dimension change; M2 is related background."}}

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
    output: _CandidateWiseRelationOutput,
    *,
    candidates: list[MemoryItem],
    max_target_count: int,
) -> SemanticRelationProposal:
    """Apply only structural target-policy guards before downstream validation.

    The model still owns semantic relation classification.  These guards prevent a
    malformed or over-broad proposal from being mistaken for an authorized target set:
    target IDs must come from the supplied candidates, be unique, match the explicit
    direct-target flags, and obey cardinality.  A violation fails closed as ``uncertain``
    with no targets.
    """

    diagnostic = _target_policy_diagnostics(
        output,
        candidates=candidates,
        max_target_count=max_target_count,
    )
    if not diagnostic["valid"]:
        return SemanticRelationProposal(
            relation=ClaimRelation.UNCERTAIN,
            target_memory_ids=[],
            same_semantic_dimension=False,
            confidence=0.0,
            reason="Target policy violation; proposal failed closed.",
        )

    relations_by_id = {
        item.memory_id: item for item in output.candidate_relations
    }
    selected = [relations_by_id[target_id] for target_id in output.target_memory_ids]
    if selected:
        relation = _derive_overall_relation([item.relation for item in selected])
        confidence = min(item.confidence for item in selected)
    else:
        relation = (
            ClaimRelation.UNCERTAIN
            if any(
                item.relation == ClaimRelation.UNCERTAIN
                for item in output.candidate_relations
            )
            else ClaimRelation.UNRELATED
        )
        confidence = output.confidence
    return SemanticRelationProposal(
        relation=relation,
        target_memory_ids=list(output.target_memory_ids),
        same_semantic_dimension=any(
            item.relation
            in {ClaimRelation.SAME, ClaimRelation.UPDATE, ClaimRelation.CONTRADICTION}
            for item in selected
        ),
        confidence=confidence,
        reason=output.reason,
    )


def _target_policy_diagnostics(
    output: _CandidateWiseRelationOutput,
    *,
    candidates: list[MemoryItem],
    max_target_count: int,
) -> dict[str, Any]:
    """Explain structural target-policy decisions without semantic re-judging."""

    candidate_ids = {candidate.id for candidate in candidates}
    target_ids = list(output.target_memory_ids)
    non_targetable_relations = {ClaimRelation.UNRELATED, ClaimRelation.UNCERTAIN}
    reasons: list[str] = []
    relation_ids = [item.memory_id for item in output.candidate_relations]
    if len(relation_ids) != len(set(relation_ids)):
        reasons.append("duplicate_candidate_relation_ids")
    unknown_relation_ids = sorted(
        {memory_id for memory_id in relation_ids if memory_id not in candidate_ids}
    )
    if unknown_relation_ids:
        reasons.append("unknown_candidate_relation_id")
    missing_relation_ids = sorted(candidate_ids.difference(relation_ids))
    if missing_relation_ids:
        reasons.append("missing_candidate_relation_id")
    if len(target_ids) > max_target_count:
        reasons.append("target_count_exceeds_max")
    if len(target_ids) != len(set(target_ids)):
        reasons.append("duplicate_target_ids")
    unknown_target_ids = sorted(
        {target_id for target_id in target_ids if target_id not in candidate_ids}
    )
    if unknown_target_ids:
        reasons.append("unknown_target_id")
    relations_by_id = {item.memory_id: item for item in output.candidate_relations}
    direct_target_ids = [
        item.memory_id for item in output.candidate_relations if item.is_direct_target
    ]
    if len(direct_target_ids) > max_target_count:
        reasons.append("direct_target_count_exceeds_max")
    if any(
        target_id in relations_by_id
        and not relations_by_id[target_id].is_direct_target
        for target_id in target_ids
    ):
        reasons.append("target_not_marked_direct")
    if any(
        item.is_direct_target and item.relation in non_targetable_relations
        for item in output.candidate_relations
    ):
        reasons.append("non_targetable_relation_marked_direct")
    if not unknown_target_ids and set(target_ids) != set(direct_target_ids):
        reasons.append("target_set_mismatch_direct_targets")
    return {
        "valid": not reasons,
        "reasons": reasons,
        "rejected_ids": sorted(
            set(unknown_relation_ids).union(unknown_target_ids)
        ),
    }


def _derive_overall_relation(relations: list[ClaimRelation]) -> ClaimRelation:
    """Derive the legacy single relation without letting it select targets."""

    if len(relations) > 1 and all(
        relation in {ClaimRelation.SAME, ClaimRelation.COMPLEMENTARY}
        for relation in relations
    ):
        return ClaimRelation.COMPLEMENTARY
    precedence = (
        ClaimRelation.CONTRADICTION,
        ClaimRelation.UPDATE,
        ClaimRelation.SAME,
        ClaimRelation.COMPLEMENTARY,
    )
    return next(relation for relation in precedence if relation in relations)


__all__ = ["OpenAICompatibleSemanticRelationJudge"]

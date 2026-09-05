import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from loveapp.adapters.memory.semantic_relations import (
    OpenAICompatibleSemanticRelationJudge,
)
from loveapp.application.memory_semantic_relations import (
    LongTailRelationShadowEvaluator,
)
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.memory import (
    AdmissionDecision,
    ClaimRelation,
    EvidenceExplicitness,
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    PredicateType,
    TimeKind,
)

NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)
API_SECRET = "semantic-adapter-secret-must-not-leak"
RAW_SECRET = "raw-model-secret-must-not-leak"


class _FakeCompletions:
    def __init__(self, contents: list[str]) -> None:
        self._contents = contents
        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.requests.append(kwargs)
        content = self._contents[min(len(self.requests) - 1, len(self._contents) - 1)]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(
                prompt_tokens=23,
                completion_tokens=11,
                total_tokens=34,
            ),
        )


async def _async_noop() -> None:
    return None


def _judge(
    content: str,
    *retry_contents: str,
    max_target_count: int = 1,
) -> tuple[OpenAICompatibleSemanticRelationJudge, _FakeCompletions]:
    completions = _FakeCompletions([content, *retry_contents])
    judge = OpenAICompatibleSemanticRelationJudge(
        api_key=SecretStr(API_SECRET),
        base_url="https://example.invalid",
        model="semantic-flash",
        max_retries=0,
        thinking="disabled",
        max_target_count=max_target_count,
    )
    judge._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        close=_async_noop,
    )
    return judge, completions


@pytest.mark.asyncio
async def test_semantic_relation_adapter_can_request_bounded_target_sets() -> None:
    payload = {
        "relation": "complementary",
        "target_memory_ids": ["first", "second"],
        "same_semantic_dimension": False,
        "confidence": 0.94,
        "reason": "The incoming claim explicitly contains two related details.",
    }
    judge, completions = _judge(json.dumps(payload), max_target_count=5)

    try:
        proposal = await judge.propose_relation(
            incoming=_incoming(),
            candidates=[
                _target().model_copy(update={"id": "first"}),
                _target().model_copy(update={"id": "second"}),
            ],
        )
    finally:
        await judge.aclose()

    assert proposal.relation == ClaimRelation.COMPLEMENTARY
    assert proposal.target_memory_ids == ["first", "second"]
    messages = completions.requests[0]["messages"]
    assert isinstance(messages, list)
    assert "Select at most 5 targets" in messages[0]["content"]
    assert "explicit multi-claim" in messages[0]["content"]


@pytest.mark.asyncio
async def test_semantic_relation_adapter_fails_closed_for_unknown_target() -> None:
    payload = {
        "relation": "update",
        "target_memory_ids": ["not-a-candidate"],
        "same_semantic_dimension": True,
        "confidence": 0.99,
        "reason": "The incoming claim updates a candidate.",
    }
    judge, _ = _judge(json.dumps(payload))
    trace = ExecutionTrace()
    try:
        proposal = await judge.propose_relation(
            incoming=_incoming(),
            candidates=[_target()],
            trace=trace,
        )
    finally:
        await judge.aclose()
    assert proposal.relation == ClaimRelation.UNCERTAIN
    assert proposal.target_memory_ids == []
    assert proposal.confidence == 0
    details = _model_trace_details(trace)
    assert details["target_policy_status"] == "fail_closed"
    assert details["target_policy_reasons"] == "unknown_target_id"
    assert details["target_policy_rejected_ids"] == "not-a-candidate"


def test_semantic_relation_adapter_rejects_unbounded_target_configuration() -> None:
    with pytest.raises(ValueError, match="max_target_count"):
        OpenAICompatibleSemanticRelationJudge(
            api_key=SecretStr(API_SECRET),
            base_url="https://example.invalid",
            model="semantic-flash",
            max_target_count=6,
        )


def _model_trace_details(trace: ExecutionTrace) -> dict[str, object]:
    records = [
        record for record in trace.snapshot() if record.name == "memory_semantic_relation_model"
    ]
    assert len(records) == 1
    return records[0].details


def _incoming() -> MemoryCandidate:
    text = "最近一个月她几乎不再邀请我参加朋友聚会。"
    return MemoryCandidate(
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="partner",
        summary=text,
        original_text=text,
        evidence_spans=[text],
        time_kind=TimeKind.INTERVAL,
        period_start=NOW - timedelta(days=30),
        period_end=NOW,
        perspective=MemoryPerspective.USER_REPORTED,
        confidence=0.96,
        payload={"object": "social_integration_restricted"},
        raw_predicate="rarely_invites_user_to_friend_activities",
        predicate_type=PredicateType.CUSTOM,
        custom_predicate="rarely_invites_user_to_friend_activities",
        explicitness=EvidenceExplicitness.EXPLICIT,
        admission_score=0.95,
        admission_decision=AdmissionDecision.CONFIRM,
    )


def _target() -> MemoryItem:
    text = "前两个月她经常邀请我参加朋友聚会。"
    candidate = MemoryCandidate(
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="partner",
        summary=text,
        original_text=text,
        evidence_spans=[text],
        time_kind=TimeKind.INTERVAL,
        period_start=NOW - timedelta(days=90),
        period_end=NOW - timedelta(days=31),
        perspective=MemoryPerspective.USER_REPORTED,
        confidence=0.97,
        payload={"object": "social_integration_included"},
        raw_predicate="invites_user_to_friend_activities",
        predicate_type=PredicateType.CUSTOM,
        custom_predicate="invites_user_to_friend_activities",
        explicitness=EvidenceExplicitness.EXPLICIT,
        admission_score=0.96,
        admission_decision=AdmissionDecision.CONFIRM,
    )
    return MemoryItem(
        **candidate.model_dump(),
        id="social-pattern-old",
        user_id="semantic-user",
        relationship_id="partner",
        status=MemoryStatus.CONFIRMED,
        source_message_id="old-source",
        created_at=NOW - timedelta(days=31),
        updated_at=NOW - timedelta(days=31),
        dedupe_key="fixture:social-pattern-old",
    )


@pytest.mark.asyncio
async def test_semantic_relation_adapter_parses_strict_first_response() -> None:
    payload = {
        "relation": "update",
        "target_memory_ids": ["social-pattern-old"],
        "same_semantic_dimension": True,
        "confidence": 0.93,
        "reason": "The sustained social-integration pattern changed.",
    }
    judge, completions = _judge(json.dumps(payload))
    trace = ExecutionTrace()

    try:
        proposal = await judge.propose_relation(
            incoming=_incoming(),
            candidates=[_target()],
            trace=trace,
        )
    finally:
        await judge.aclose()

    assert proposal.relation == ClaimRelation.UPDATE
    assert len(completions.requests) == 1
    details = _model_trace_details(trace)
    assert details["attempt_count"] == 1
    assert details["retry_count"] == 0
    assert details["attempt_1_status"] == "parsed"
    assert details["local_repair_applied"] is False
    assert details["parse_status"] == "completed"


@pytest.mark.asyncio
async def test_semantic_relation_adapter_repairs_bounded_structured_output() -> None:
    payload = {
        "relation": " UPDATE ",
        "target_memory_ids": "social-pattern-old",
        "same_semantic_dimension": True,
        "confidence": "0.93",
        "reason": "The sustained social-integration pattern changed.",
    }
    content = f"Proposed JSON:\n```json\n{json.dumps(payload)}\n```\nEnd."
    judge, completions = _judge(content)
    trace = ExecutionTrace()

    try:
        proposal = await judge.propose_relation(
            incoming=_incoming(),
            candidates=[_target()],
            trace=trace,
        )
    finally:
        await judge.aclose()

    assert proposal.relation == ClaimRelation.UPDATE
    assert proposal.target_memory_ids == ["social-pattern-old"]
    assert proposal.same_semantic_dimension is True
    assert proposal.confidence == 0.93
    assert proposal.judge_model == "semantic-flash"
    assert proposal.prompt_tokens == 23
    assert proposal.completion_tokens == 11
    assert proposal.total_tokens == 34
    assert proposal.latency_ms is not None and proposal.latency_ms >= 0

    request = completions.requests[0]
    assert request["response_format"] == {"type": "json_object"}
    assert request["temperature"] == 0
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    messages = request["messages"]
    assert isinstance(messages, list)
    user_payload = json.loads(messages[1]["content"])
    assert user_payload["incoming_memory"]["custom_predicate"] == (
        "rarely_invites_user_to_friend_activities"
    )
    assert user_payload["candidate_memories"][0]["id"] == "social-pattern-old"

    observable = json.dumps(
        {
            "request": request,
            "trace": [record.model_dump(mode="json") for record in trace.snapshot()],
        },
        ensure_ascii=False,
        default=str,
    )
    assert API_SECRET not in observable
    assert content not in observable

    assert len(completions.requests) == 1
    details = _model_trace_details(trace)
    assert details["attempt_count"] == 1
    assert details["retry_count"] == 0
    assert details["attempt_1_status"] == "repaired"
    assert details["local_repair_applied"] is True
    assert details["local_repair_steps"] == (
        "embedded_json,relation_casefold,confidence_numeric_string,target_id_scalar"
    )
    assert details["parse_status"] == "completed"


@pytest.mark.asyncio
async def test_semantic_relation_adapter_truncates_overlong_reason_without_retry() -> None:
    payload = {
        "relation": "contradiction",
        "target_memory_ids": ["social-pattern-old"],
        "same_semantic_dimension": True,
        "confidence": 0.7,
        "reason": "x" * 600,
    }
    judge, completions = _judge(json.dumps(payload))
    trace = ExecutionTrace()

    try:
        proposal = await judge.propose_relation(
            incoming=_incoming(),
            candidates=[_target()],
            trace=trace,
        )
    finally:
        await judge.aclose()

    assert proposal.relation == ClaimRelation.CONTRADICTION
    assert proposal.target_memory_ids == ["social-pattern-old"]
    assert proposal.reason == "x" * 500
    assert len(completions.requests) == 1
    details = _model_trace_details(trace)
    assert details["attempt_1_status"] == "repaired"
    assert details["retry_count"] == 0
    assert details["local_repair_steps"] == "reason_truncated"
    assert details["parse_status"] == "completed"


@pytest.mark.asyncio
async def test_semantic_relation_adapter_retries_invalid_json_once() -> None:
    retry_payload = {
        "relation": "uncertain",
        "target_memory_ids": [],
        "same_semantic_dimension": False,
        "confidence": 0.72,
        "reason": "No unique target is safe.",
    }
    judge, completions = _judge(
        "This is not structured output.",
        json.dumps(retry_payload),
    )
    trace = ExecutionTrace()

    try:
        proposal = await judge.propose_relation(
            incoming=_incoming(),
            candidates=[_target()],
            trace=trace,
        )
    finally:
        await judge.aclose()

    assert proposal.relation == ClaimRelation.UNCERTAIN
    assert proposal.prompt_tokens == 46
    assert proposal.completion_tokens == 22
    assert proposal.total_tokens == 68
    assert len(completions.requests) == 2
    retry_messages = completions.requests[1]["messages"]
    assert isinstance(retry_messages, list)
    assert retry_messages[-1]["role"] == "user"
    assert "Return only one JSON" in retry_messages[-1]["content"]
    details = _model_trace_details(trace)
    assert details["attempt_count"] == 2
    assert details["retry_count"] == 1
    assert details["retry_reason"] == "structured_output_parse_failure"
    assert details["attempt_1_status"] == "parse_failed"
    assert details["attempt_2_status"] == "parsed"
    assert details["parse_status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        f'{{"relation":"update","reason":"{RAW_SECRET}"',
        f"The relation is update because {RAW_SECRET}.",
        (
            json.dumps(
                {
                    "relation": "same",
                    "target_memory_ids": ["social-pattern-old"],
                    "same_semantic_dimension": True,
                    "confidence": 0.9,
                    "reason": "First object.",
                }
            )
            + json.dumps(
                {
                    "relation": "update",
                    "target_memory_ids": ["social-pattern-old"],
                    "same_semantic_dimension": True,
                    "confidence": 0.9,
                    "reason": "Second object.",
                }
            )
        ),
        json.dumps(
            {
                "relation": RAW_SECRET,
                "target_memory_ids": ["social-pattern-old"],
                "same_semantic_dimension": True,
                "confidence": 0.95,
                "reason": "Invalid relation must fail closed.",
            }
        ),
        json.dumps(
            {
                "relation": "update",
                "target_memory_ids": ["social-pattern-old"],
                "same_semantic_dimension": True,
                "confidence": 1.5,
                "reason": RAW_SECRET,
            }
        ),
    ],
    ids=[
        "malformed-json",
        "free-text-relation",
        "multiple-json-objects",
        "invalid-relation",
        "invalid-confidence",
    ],
)
async def test_semantic_relation_adapter_rejects_invalid_output_without_raw_leakage(
    content: str,
) -> None:
    judge, completions = _judge(content)
    trace = ExecutionTrace()

    try:
        with pytest.raises(
            ValueError,
            match=r"^semantic relation judge returned invalid structured output$",
        ) as exc_info:
            await judge.propose_relation(
                incoming=_incoming(),
                candidates=[_target()],
                trace=trace,
            )
    finally:
        await judge.aclose()

    assert RAW_SECRET not in str(exc_info.value)
    failed = trace.failed_step
    assert failed is not None
    assert failed.name == "memory_semantic_relation_model"
    assert failed.error == "semantic relation judge returned invalid structured output"
    trace_payload = json.dumps(failed.model_dump(mode="json"), ensure_ascii=False)
    assert RAW_SECRET not in trace_payload
    assert API_SECRET not in trace_payload
    assert len(completions.requests) == 2
    assert failed.details["attempt_count"] == 2
    assert failed.details["retry_count"] == 1
    assert failed.details["attempt_1_status"] == "parse_failed"
    assert failed.details["attempt_2_status"] == "parse_failed"
    assert failed.details["parse_status"] == "failed"


@pytest.mark.asyncio
async def test_invalid_adapter_output_becomes_uncertain_in_shadow_mode() -> None:
    content = json.dumps(
        {
            "relation": RAW_SECRET,
            "target_memory_ids": ["social-pattern-old"],
            "same_semantic_dimension": True,
            "confidence": 0.99,
            "reason": "An invalid proposal must not authorize a mutation.",
        }
    )
    judge, completions = _judge(content)
    trace = ExecutionTrace()

    try:
        result = await LongTailRelationShadowEvaluator(judge).evaluate(
            incoming=_incoming(),
            existing_memories=[_target()],
            user_id="semantic-user",
            relationship_id="partner",
            incoming_status=MemoryStatus.CONFIRMED,
            incoming_source_message_id="incoming-source",
            reference_time=NOW,
            trace=trace,
        )
    finally:
        await judge.aclose()

    assert len(completions.requests) == 2
    assert [item.memory_id for item in result.retrieved_candidates] == ["social-pattern-old"]
    assert result.proposal.relation == ClaimRelation.UNCERTAIN
    assert result.proposal.target_memory_ids == []
    assert result.validation.validated_relation == ClaimRelation.UNCERTAIN
    assert result.validation.would_update is False
    assert result.validation.would_supersede_memory_ids == []
    assert result.store_mutation_permitted is False

    observable = json.dumps(
        {
            "result": result.model_dump(mode="json"),
            "trace": [record.model_dump(mode="json") for record in trace.snapshot()],
        },
        ensure_ascii=False,
    )
    assert RAW_SECRET not in observable
    assert API_SECRET not in observable

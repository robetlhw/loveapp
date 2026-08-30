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
    def __init__(self, content: str) -> None:
        self._content = content
        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.requests.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=self._content))
            ],
            usage=SimpleNamespace(
                prompt_tokens=23,
                completion_tokens=11,
                total_tokens=34,
            ),
        )


async def _async_noop() -> None:
    return None


def _judge(content: str) -> tuple[OpenAICompatibleSemanticRelationJudge, _FakeCompletions]:
    completions = _FakeCompletions(content)
    judge = OpenAICompatibleSemanticRelationJudge(
        api_key=SecretStr(API_SECRET),
        base_url="https://example.invalid",
        model="semantic-flash",
        max_retries=0,
        thinking="disabled",
    )
    judge._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        close=_async_noop,
    )
    return judge, completions


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
async def test_semantic_relation_adapter_parses_structured_proposal() -> None:
    payload = {
        "relation": " UPDATE ",
        "target_memory_ids": ["social-pattern-old"],
        "same_semantic_dimension": True,
        "confidence": 0.93,
        "reason": "The sustained social-integration pattern changed.",
    }
    content = f"```json\n{json.dumps(payload)}\n```"
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        f'{{"relation":"update","reason":"{RAW_SECRET}"',
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
    ids=["malformed-json", "invalid-relation", "invalid-confidence"],
)
async def test_semantic_relation_adapter_rejects_invalid_output_without_raw_leakage(
    content: str,
) -> None:
    judge, _completions = _judge(content)
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

    assert len(completions.requests) == 1
    assert [item.memory_id for item in result.retrieved_candidates] == [
        "social-pattern-old"
    ]
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

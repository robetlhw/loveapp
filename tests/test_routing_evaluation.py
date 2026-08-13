import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from loveapp.adapters.routing import OpenAICompatibleRouteCorrector
from loveapp.application.routing import route_by_rules
from loveapp.core.config import Settings
from loveapp.domain.enums import TaskType
from loveapp.domain.routing import RouteCorrection, RouteInput
from loveapp.evaluation.routing import (
    _average_tokens_per_llm_call,
    _evaluate_routing_conversations,
    _llm_call_count,
    evaluate_live_routing_conversations,
    evaluate_routing_conversations,
)


async def test_routing_v2_regression_set_is_multiturn_and_preserves_historical_expectations(
) -> None:
    dataset = Path(__file__).parents[1] / "evals" / "routing" / "cases_v2.jsonl"

    report = await evaluate_routing_conversations(dataset)

    assert report["case_count"] == 13
    assert report["turn_count"] == 36
    assert report["multi_turn_case_count"] == 13
    assert report["context_turn_count"] >= 20
    # v2 is an immutable historical fixture.  The post-remediation safety
    # policy intentionally keeps a sensitive context after a HIGH turn, while
    # v2 recorded that final turn as normal.  Keep the mismatch visible rather
    # than rewriting the old expected value; v4 carries the current contract.
    assert report["pass_rate"] == pytest.approx(0.9722)
    assert report["conversation_pass_rate"] == pytest.approx(0.9231)
    assert report["high_risk_recall"] == 1.0
    assert report["never_policy_violations"] == 0
    assert report["required_policy_misses"] == 0
    assert report["llm_call_rate"] <= 0.2


async def test_routing_v3_reported_action_regression_set_passes() -> None:
    dataset = Path(__file__).parents[1] / "evals" / "routing" / "cases_v3.jsonl"

    report = await evaluate_routing_conversations(dataset)

    assert report["case_count"] == 3
    assert report["turn_count"] == 6
    assert report["multi_turn_case_count"] == 3
    assert report["pass_rate"] == 1.0
    assert report["conversation_pass_rate"] == 1.0
    assert report["never_policy_violations"] == 0


async def test_routing_v4_policy_report_has_118_turns_and_policy_metrics() -> None:
    dataset = Path(__file__).parents[1] / "evals" / "routing" / "cases_v4.jsonl"

    report = await evaluate_routing_conversations(dataset)

    assert report["evaluation_mode"] == "policy"
    assert report["corrector_kind"] == "recording"
    assert report["case_count"] == 47
    assert report["turn_count"] == 118
    assert len(report["dataset_sha256"]) == 64
    assert set(report["task_metrics"]) == {task.value for task in TaskType}
    assert report["out_of_scope_positive_turn_count"] >= 10
    assert report["clarification_annotation_turn_count"] >= 100
    assert report["goal_annotation_turn_count"] >= 6
    assert report["pending_continuation_count"] >= 1
    assert report["pending_continuation_success_rate"] == 1.0
    assert report["clarification_exhausted_annotation_turn_count"] >= 1
    assert report["clarification_exhausted_accuracy"] == 1.0
    assert report["slot_annotation_turn_count"] >= 6
    assert report["slot_hallucination_rate"] == 0
    assert report["slot_hallucination_attempt_rate"] > 0
    assert report["slot_unsupported_attempt_field_count"] == 3
    assert report["slot_validator_block_rate"] == 1.0
    assert report["p50_policy_latency_ms"] >= 0
    assert report["p95_policy_latency_ms"] >= report["p50_policy_latency_ms"]
    assert report["p50_live_router_latency_ms"] == 0
    assert report["p95_live_router_latency_ms"] == 0
    assert isinstance(report["acceptance_passed"], bool)
    compound = next(
        case for case in report["cases"] if case["id"] == "rt_v4_c29_compound_pending"
    )
    continuation = compound["turns"][1]
    assert continuation["pending_continuation"] is True
    assert continuation["forced_task"] == "date_planning"
    assert continuation["flow_before"]["pending_task"] == "date_planning"
    assert continuation["flow_after"]["pending_task"] is None
    pending_cancel = next(
        case
        for case in report["cases"]
        if case["id"] == "rt_v4_c47_pending_cancel_before_continuation"
    )
    cancelled_turn = pending_cancel["turns"][1]
    assert cancelled_turn["actual"]["pending_task_cancelled"] is True
    assert cancelled_turn["flow_before"]["pending_task"] == "date_planning"
    assert cancelled_turn["flow_after"]["pending_task"] is None
    clarification = next(
        case for case in report["cases"] if case["id"] == "rt_v4_c30_clarification"
    )
    assert clarification["turns"][1]["actual"]["clarification_exhausted"] is True
    assert clarification["turns"][1]["actual"]["route"] == "clarify_intent"
    deescalated = next(
        case
        for case in report["cases"]
        if case["id"] == "rt_v4_c33_high_risk_deescalation"
    )
    assert deescalated["turns"][1]["actual"]["route"] == "sensitive_risk_response"
    guard_cases = {
        case["id"]: case
        for case in report["cases"]
        if case["id"]
        in {
            "rt_v4_c45_guard_rejects_date_override",
            "rt_v4_c46_guard_rejects_secondary_date",
        }
    }
    assert set(guard_cases) == {
        "rt_v4_c45_guard_rejects_date_override",
        "rt_v4_c46_guard_rejects_secondary_date",
    }
    assert all(
        case["turns"][0]["actual"]["task_guard_applied"] is True
        for case in guard_cases.values()
    )


async def test_live_routing_eval_requires_explicit_environment_guard() -> None:
    dataset = Path(__file__).parents[1] / "evals" / "routing" / "cases_v4.jsonl"
    settings = Settings(_env_file=None, router_live_eval_enabled=False)

    with pytest.raises(RuntimeError, match="LOVEAPP_ROUTER_LIVE_EVAL_ENABLED=true"):
        await evaluate_live_routing_conversations(dataset, settings)


async def test_risk_accuracy_requires_an_exact_risk_level_match(tmp_path: Path) -> None:
    dataset = tmp_path / "risk.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "id": "risk-exact-match",
                "turns": [
                    {
                        "turn_id": "t1",
                        "query": "怎样避免伤害自己？",
                        "expected": {
                            "risk_level": "normal",
                            "llm_policy": "optional",
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = await evaluate_routing_conversations(dataset)

    assert report["cases"][0]["turns"][0]["actual"]["risk_level"] == "sensitive"
    assert report["risk_accuracy"] == 0


async def test_routing_dataset_validation_rejects_unknown_policy(tmp_path: Path) -> None:
    dataset = tmp_path / "invalid.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "id": "invalid-policy",
                "turns": [
                    {
                        "turn_id": "t1",
                        "query": "你好",
                        "expected": {"llm_policy": "sometimes"},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="未知 llm_policy"):
        await evaluate_routing_conversations(dataset)


async def test_route_corrector_accumulates_usage_across_structure_repair_attempts() -> None:
    corrector = OpenAICompatibleRouteCorrector(
        api_key=SecretStr("test-key"),
        base_url="https://example.invalid",
        model="test-router",
        max_retries=0,
    )
    completions = _FakeRouteCompletions(
        responses=[
            "not-json",
            '{"task_type":"relationship_advice","task_confidence":0.9}',
        ],
        usages=[(11, 3), (17, 5)],
    )
    corrector._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        close=_async_noop,
    )
    route_input = RouteInput(
        latest_query="\u6211\u559c\u6b22\u5979\uff0c\u6709\u4ec0\u4e48\u5efa\u8bae\u5417\uff1f"
    )

    correction = await corrector.correct(route_input, route_by_rules(route_input))

    assert correction.task_type == TaskType.RELATIONSHIP_ADVICE
    assert completions.calls == 2
    assert corrector.last_telemetry["attempt_count"] == 2
    assert corrector.last_telemetry["input_tokens"] == 28
    assert corrector.last_telemetry["output_tokens"] == 8
    await corrector.aclose()


def test_router_token_averages_use_underlying_llm_calls() -> None:
    telemetry = {"attempt_count": 2}

    llm_call_count = _llm_call_count(1, telemetry)

    assert llm_call_count == 2
    assert _average_tokens_per_llm_call(28, llm_call_count) == 14
    assert _average_tokens_per_llm_call(8, llm_call_count) == 4
    assert _average_tokens_per_llm_call(28, 0) == 0


async def test_live_report_averages_tokens_per_underlying_llm_request(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "token_average.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "id": "token-average",
                "turns": [
                    {
                        "query": (
                            "\u6211\u559c\u6b22\u5979\uff0c\u6709\u4ec0\u4e48\u5efa\u8bae\u5417\uff1f"
                        ),
                        "expected": {
                            "task_type": "relationship_advice",
                            "llm_policy": "required",
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    corrector = _RetryTelemetryCorrector()

    report = await _evaluate_routing_conversations(
        dataset,
        corrector=corrector,
        evaluation_mode="live",
        input_cost_per_million=0,
        output_cost_per_million=0,
        confidence_threshold=0.72,
        ambiguity_margin=0.16,
        clarification_threshold=0.68,
        safety_context_turns=4,
        prompt_version="routing-v3.0",
    )

    assert corrector.calls == 1
    assert report["router_correction_call_count"] == 1
    assert report["llm_call_count"] == 2
    assert report["average_input_tokens"] == 14
    assert report["average_output_tokens"] == 4
    assert report["average_input_tokens_per_turn"] == 28
    assert report["average_output_tokens_per_turn"] == 8


class _FakeRouteCompletions:
    def __init__(self, *, responses: list[str], usages: list[tuple[int, int]]) -> None:
        self.responses = responses
        self.usages = usages
        self.calls = 0

    async def create(self, **_kwargs: object) -> SimpleNamespace:
        index = self.calls
        self.calls += 1
        input_tokens, output_tokens = self.usages[index]
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.responses[index]),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
            ),
        )


class _RetryTelemetryCorrector:
    def __init__(self) -> None:
        self.calls = 0
        self.last_telemetry: dict[str, int | float | str] = {}

    async def correct(
        self,
        _route_input: RouteInput,
        _rule_result: object,
    ) -> RouteCorrection:
        self.calls += 1
        self.last_telemetry = {
            "model": "test-router",
            "input_tokens": 28,
            "output_tokens": 8,
            "duration_ms": 1.0,
            "attempt_count": 2,
        }
        return RouteCorrection(
            task_type=TaskType.RELATIONSHIP_ADVICE,
            task_confidence=0.9,
        )

    async def aclose(self) -> None:
        return None


async def _async_noop() -> None:
    return None

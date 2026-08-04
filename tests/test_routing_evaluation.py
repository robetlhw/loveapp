from pathlib import Path

from loveapp.evaluation.routing import evaluate_routing_conversations


async def test_routing_v2_regression_set_is_multiturn_and_passes() -> None:
    dataset = Path(__file__).parents[1] / "evals" / "routing" / "cases_v2.jsonl"

    report = await evaluate_routing_conversations(dataset)

    assert report["case_count"] == 13
    assert report["turn_count"] == 36
    assert report["multi_turn_case_count"] == 13
    assert report["context_turn_count"] >= 20
    assert report["pass_rate"] == 1.0
    assert report["conversation_pass_rate"] == 1.0
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

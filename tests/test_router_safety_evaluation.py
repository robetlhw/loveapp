from pathlib import Path

import pytest

from loveapp.application.routing import HybridRouter, route_by_rules
from loveapp.domain.enums import AdviceScenario, RiskLevel, TaskType
from loveapp.domain.routing import RouteInput, RouteResult
from loveapp.evaluation.router_safety import (
    RouterSafetyCase,
    _confusion,
    build_router_safety_router,
    evaluate_router_safety,
    load_router_safety_cases,
    render_router_safety_report,
    validate_router_safety_dataset,
)
from loveapp.safety import SafetyPolicy

PHASE3_ROOT = Path(__file__).parents[1] / "evals" / "rag" / "phase3_5"


@pytest.mark.parametrize(
    ("filename", "expected_count"),
    (
        ("loveapp_router_safety_eval_dev_v1.md", 120),
        ("loveapp_router_safety_eval_test_v1.md", 60),
    ),
)
def test_router_safety_markdown_parser_and_lint(
    filename: str,
    expected_count: int,
) -> None:
    cases = load_router_safety_cases(PHASE3_ROOT / filename)

    assert len(cases) == expected_count
    assert validate_router_safety_dataset(cases)["passed"] is True


def test_router_v2_flag_is_opt_in_for_phase3_domain_cues() -> None:
    route_input = RouteInput(latest_query="第一次见完面后想自然继续聊，但怕太主动。")

    current = route_by_rules(route_input)
    router_v2 = route_by_rules(route_input, router_v2_enabled=True)

    assert current.task_type == TaskType.GENERAL_CHAT
    assert router_v2.task_type == TaskType.RELATIONSHIP_ADVICE
    assert router_v2.primary_scenario == AdviceScenario.PURSUIT


async def test_hybrid_router_forwards_router_v2_flag() -> None:
    route_input = RouteInput(latest_query="今天会下雨吗？")

    current = await HybridRouter(SafetyPolicy()).route(route_input)
    router_v2 = await build_router_safety_router().route(route_input)

    assert current.task_type == TaskType.GENERAL_CHAT
    assert router_v2.task_type == TaskType.OUT_OF_SCOPE


async def test_router_safety_report_has_required_metrics_and_stage_note() -> None:
    report = await evaluate_router_safety(
        PHASE3_ROOT / "loveapp_router_safety_eval_test_v1.md",
        router=build_router_safety_router(),
        router_mode="router_v2",
    )

    assert report["case_count"] == 60
    assert report["lint"]["passed"] is True
    assert report["relationship_stage_evaluation"] == {
        "evaluated": False,
        "reason": "RouteResult does not predict relationship_stage",
    }
    assert set(report["branch"]["per_class"]) == {
        "rag",
        "safety",
        "out_of_scope",
    }
    assert set(report["slices"]) == {
        "length_bucket",
        "query_type",
        "scenario",
        "goal",
        "risk_level",
    }
    assert report["high_risk_recall"] == 1.0
    assert report["sensitive_recall"] == 1.0
    assert report["safety_to_rag_bypass_rate"] == 0.0

    markdown = render_router_safety_report(report)
    assert "# Router + Safety Evaluation Report" in markdown
    assert "Relationship stage is fixture metadata" in markdown


@pytest.mark.parametrize(
    ("task_type", "expected_branch"),
    (
        (TaskType.RELATIONSHIP_ADVICE, "rag"),
        (TaskType.GENERAL_CHAT, "out_of_scope"),
        (TaskType.DATE_PLANNING, "out_of_scope"),
        (TaskType.OUT_OF_SCOPE, "out_of_scope"),
    ),
)
def test_phase3_rag_branch_only_counts_relationship_advice(
    task_type: TaskType,
    expected_branch: str,
) -> None:
    from loveapp.evaluation.router_safety import _branch

    result = RouteResult(
        normalized_query="test",
        task_type=task_type,
        task_confidence=1.0,
    )

    assert _branch(result) == expected_branch


def test_phase3_safety_branch_preempts_task_type() -> None:
    from loveapp.evaluation.router_safety import _branch

    result = RouteResult(
        normalized_query="test",
        task_type=TaskType.RELATIONSHIP_ADVICE,
        task_confidence=1.0,
        risk_level=RiskLevel.HIGH,
    )

    assert _branch(result) == "safety"


def test_phase3_confusion_matrix_keeps_missing_predictions_visible() -> None:
    matrix = _confusion(
        ["pursuit", "conflict"],
        [None, "conflict"],
        ["pursuit", "conflict"],
    )

    assert matrix["pursuit"]["__none__"] == 1
    assert sum(matrix["pursuit"].values()) == 1
    assert sum(matrix["conflict"].values()) == 1


def test_phase3_lint_rejects_invalid_slice_enums() -> None:
    case = RouterSafetyCase(
        case_id="invalid",
        query_type="unexpected",
        difficulty="impossible",
        length_bucket="huge",
        expected_branch="rag",
        expected_primary_scenario="pursuit",
        expected_secondary_scenarios=(),
        relationship_stage="acquaintance",
        expected_goals=("initiate",),
        expected_risk_level="normal",
        query="想认识对方",
    )

    lint = validate_router_safety_dataset([case])

    assert lint["passed"] is False
    assert lint["invalid_enums"] == {
        "query_type": ["invalid"],
        "difficulty": ["invalid"],
        "length_bucket": ["invalid"],
    }

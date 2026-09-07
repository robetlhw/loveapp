import json
from pathlib import Path

import pytest

from loveapp.domain.enums import AdviceGoal, AdviceScenario, TaskType
from loveapp.domain.routing import RouteResult
from loveapp.evaluation.router_phase32 import (
    PHASE321_OUTPUT_FILENAMES,
    _build_report,
    _evaluate_cases,
    _goal_metrics_from_pairs,
    _scenario_disagreement_metrics,
    add_conditional_diagnostics,
    apply_phase321_goal_policy,
    build_phase321_conditional_ablation,
    calculate_phase321_repeatability_metrics,
    evaluate_phase321_goal_policy_ablation,
    phase321_call_budget,
    select_phase321_goals,
    write_phase321_reports,
)
from loveapp.evaluation.router_safety import RouterSafetyCase


def _row(
    case_id: str,
    *,
    expected_scenario: str = "conflict",
    expected_goals: tuple[str, ...] = ("repair",),
    actual_scenario: str = "conflict",
    actual_goals: tuple[str, ...] = ("repair",),
    called: bool = False,
    primary_goal: str | None = None,
    goal_scores: dict[str, float] | None = None,
) -> dict:
    primary = primary_goal or (actual_goals[0] if actual_goals else None)
    return {
        "id": case_id,
        "query": f"query-{case_id}",
        "expected": {
            "branch": "rag",
            "primary_scenario": expected_scenario,
            "goals": list(expected_goals),
            "risk_level": "normal",
        },
        "actual": {
            "branch": "rag",
            "primary_scenario": actual_scenario,
            "secondary_scenarios": [],
            "goals": list(actual_goals),
            "primary_goal": primary,
            "llm_goal_scores": dict(goal_scores or {}),
        },
        "trace": {
            "llm_called": called,
            "llm_route_decision": {
                "primary_goal": primary,
                "goals": list(actual_goals),
                "goal_scores": dict(goal_scores or {}),
            }
            if called
            else None,
        },
        "errors": [],
    }


def test_phase321_goal_metrics_separate_label_and_case_counts() -> None:
    metrics = _goal_metrics_from_pairs(
        [
            ({"repair"}, {"repair", "communicate"}),
            ({"understand", "progress"}, {"understand"}),
        ]
    )

    assert metrics["micro_precision"] == 0.6667
    assert metrics["micro_recall"] == 0.6667
    assert metrics["exact_set_accuracy"] == 0.0
    assert metrics["average_predicted_label_count"] == 1.5
    assert metrics["average_gold_label_count"] == 1.5
    assert metrics["overprediction_case_count"] == 1
    assert metrics["overprediction_label_count"] == 1
    assert metrics["missing_case_count"] == 1
    assert metrics["missing_label_count"] == 1
    assert metrics["gold_coverage"] == 0.6667


def test_phase321_scenario_disagreement_distinguishes_top2_recovery() -> None:
    cases = [
        RouterSafetyCase(
            case_id=case_id,
            query_type="synthetic",
            difficulty="hard",
            length_bucket="short",
            expected_branch="rag",
            expected_primary_scenario="conflict",
            expected_secondary_scenarios=(),
            relationship_stage=None,
            expected_goals=("repair",),
            expected_risk_level="normal",
            query=case_id,
        )
        for case_id in ("recovered", "missed")
    ]
    rows = [
        {
            "actual": {
                "primary_scenario": "boundary",
                "secondary_scenarios": ["conflict"],
            }
        },
        {
            "actual": {
                "primary_scenario": "boundary",
                "secondary_scenarios": ["relationship_maintenance"],
            }
        },
    ]

    metrics = _scenario_disagreement_metrics(cases, rows)

    assert metrics["primary_disagreement_count"] == 2
    assert metrics["top2_disagreement_count"] == 1
    assert metrics["top2_recovery_count"] == 1
    assert metrics["top2_recovery_rate"] == 0.5


def test_phase321_goal_policy_keeps_primary_and_thresholds_secondaries() -> None:
    row = _row(
        "goal-policy",
        actual_goals=("repair", "communicate", "understand"),
        called=True,
        primary_goal="repair",
        goal_scores={"repair": 0.2, "communicate": 0.49, "understand": 0.1},
    )

    assert select_phase321_goals(
        row, secondary_threshold=0.5, max_goals=3
    ) == ["repair"]
    assert select_phase321_goals(
        row, secondary_threshold=0.4, max_goals=2
    ) == ["repair", "communicate"]

    no_llm_row = _row(
        "rule-only",
        actual_goals=("repair", "communicate", "understand"),
        called=False,
    )
    assert select_phase321_goals(
        no_llm_row, secondary_threshold=0.9, max_goals=1
    ) == ["repair", "communicate", "understand"]


def test_phase321_goal_policy_ablation_selects_precision_gain_without_recall_loss() -> None:
    source = {
        "evaluation": "phase3.2_live_llm_semantic_router",
        "dataset": "dev.md",
        "live_llm": True,
        "cases": [
            _row(
                "extra",
                expected_goals=("repair",),
                actual_goals=("repair", "communicate"),
                called=True,
                goal_scores={"repair": 0.9, "communicate": 0.3},
            ),
            _row(
                "multi",
                expected_goals=("understand", "progress"),
                actual_goals=("understand", "progress"),
                called=True,
                goal_scores={"understand": 0.9, "progress": 0.6},
            ),
        ],
    }

    report = evaluate_phase321_goal_policy_ablation(
        source,
        secondary_thresholds=(0.2, 0.4),
        max_goals_values=(2,),
    )

    assert report["evaluation"] == "phase3.2.1_goal_policy_ablation"
    assert len(report["candidates"]) == 2
    assert report["selected_policy"] == {
        "secondary_threshold": 0.4,
        "max_goals": 2,
        "primary_goal_always_retained": True,
        "recall_guard_passed": True,
    }
    assert report["selected_metrics"]["micro_precision"] == 1.0
    assert report["selected_metrics"]["micro_recall"] == 1.0
    assert report["selected_metrics"]["overprediction_label_count"] == 0


def test_phase321_goal_policy_replays_same_live_decisions_without_new_calls() -> None:
    case = RouterSafetyCase(
        case_id="goal-replay",
        query_type="synthetic",
        difficulty="easy",
        length_bucket="short",
        expected_branch="rag",
        expected_primary_scenario="conflict",
        expected_secondary_scenarios=(),
        relationship_stage=None,
        expected_goals=("repair",),
        expected_risk_level="normal",
        query="goal replay",
    )
    row = _row(
        "goal-replay",
        actual_goals=("repair", "communicate"),
        called=True,
        primary_goal="repair",
        goal_scores={"repair": 0.9, "communicate": 0.3},
    )
    row["errors"] = ["goal_overprediction", "goal_wrong_default_communicate"]
    source = {
        "generated_at": "before",
        "cases": [row],
        "llm_called_count": 1,
        "goal_policy": {"secondary_threshold": 0.0, "max_goals": 3},
        "metadata": {"goal_secondary_threshold": 0.0, "goal_max_count": 3},
    }

    replayed = apply_phase321_goal_policy(
        source,
        [case],
        secondary_threshold=0.4,
        max_goals=2,
    )

    assert source["cases"][0]["actual"]["goals"] == ["repair", "communicate"]
    assert replayed["cases"][0]["actual"]["goals"] == ["repair"]
    assert replayed["cases"][0]["actual"]["final_goal_weights"] == {"repair": 1.0}
    assert replayed["cases"][0]["errors"] == []
    assert replayed["goal_micro_f1"] == 1.0
    assert replayed["llm_called_count"] == 1
    assert replayed["goal_policy"]["application_mode"] == (
        "offline_replay_of_same_live_decisions"
    )


def test_phase321_call_budget_avoids_goal_policy_rerun() -> None:
    budget = phase321_call_budget(
        dev_case_count=120,
        challenge_case_count=120,
    )

    assert budget["goal_policy_additional_live_calls"] == 0
    assert budget["live_case_evaluations"] == 808
    assert budget["llm_case_call_upper_bound"] == 808
    assert budget["llm_call_upper_bound"] == 808
    assert budget["provider_attempt_upper_bound"] == 808
    assert budget["total_route_case_evaluations"] == 1048

    with_retries = phase321_call_budget(
        dev_case_count=120,
        challenge_case_count=120,
        max_retries=2,
    )
    assert with_retries["llm_case_call_upper_bound"] == 808
    assert with_retries["provider_attempt_upper_bound"] == 2424
    assert with_retries["llm_call_upper_bound"] == 2424


def test_conditional_diagnostics_distinguish_dimension_waste_from_case_waste() -> None:
    rule = {
        "cases": [
            _row("miss", actual_scenario="relationship_maintenance"),
            _row(
                "rescued",
                expected_goals=("repair",),
                actual_scenario="relationship_maintenance",
                actual_goals=("communicate",),
            ),
            _row("unnecessary"),
        ]
    }
    always = {
        "cases": [
            _row("miss", called=True),
            _row("rescued", called=True),
            _row("unnecessary", called=True),
        ]
    }
    conditional = {
        "comparison": {},
        "cases": [
            _row("miss", actual_scenario="relationship_maintenance"),
            _row("rescued", called=True),
            _row("unnecessary", called=True),
        ],
    }

    diagnostics = add_conditional_diagnostics(
        {"rule": rule, "always": always, "conditional": conditional}
    )

    assert diagnostics["conditional_trigger_miss_dimension_count"] == 1
    assert diagnostics["conditional_trigger_miss_case_count"] == 1
    assert diagnostics["conditional_unnecessary_call_dimension_count"] == 4
    assert diagnostics["conditional_unnecessary_call_case_count"] == 1
    assert diagnostics["conditional_unnecessary_call_case_ids"] == ["unnecessary"]


def test_phase321_conditional_ablation_prefers_accepted_accuracy_first_variant() -> None:
    always = {
        "scenario_macro_f1": 0.85,
        "goal_micro_f1": 0.80,
        "llm_call_rate": 1.0,
    }
    variants = {
        "C1": {
            "live_llm": True,
            "rag_recall": 1.0,
            "scenario_macro_f1": 0.82,
            "goal_micro_f1": 0.77,
            "goal_macro_f1": 0.75,
            "llm_call_rate": 0.70,
        },
        "C2": {
            "live_llm": True,
            "rag_recall": 1.0,
            "scenario_macro_f1": 0.84,
            "goal_micro_f1": 0.79,
            "goal_macro_f1": 0.77,
            "llm_call_rate": 0.82,
        },
    }

    report = build_phase321_conditional_ablation(
        variants, always_report=always, dataset="challenge_dev"
    )

    assert report["selected_variant"] == "C2"
    assert report["selection_status"] == "selected_from_accepted_variants"
    assert report["metric_semantics"]["conditional_unnecessary_call_case_count"].startswith(
        "unique"
    )


def test_phase321_repeatability_reports_top2_and_exact_goal_sets() -> None:
    runs = [
        {
            "run": 1,
            "rows": [
                {
                    "id": "one",
                    "actual": {
                        "branch": "rag",
                        "primary_scenario": "conflict",
                        "secondary_scenarios": ["boundary"],
                        "goals": ["repair", "communicate"],
                    },
                },
                {
                    "id": "two",
                    "actual": {
                        "branch": "rag",
                        "primary_scenario": "breakup",
                        "secondary_scenarios": ["boundary"],
                        "goals": ["end_relationship"],
                    },
                },
            ],
        },
        {
            "run": 2,
            "rows": [
                {
                    "id": "one",
                    "actual": {
                        "branch": "rag",
                        "primary_scenario": "conflict",
                        "secondary_scenarios": ["relationship_maintenance"],
                        "goals": ["communicate", "repair"],
                    },
                },
                {
                    "id": "two",
                    "actual": {
                        "branch": "rag",
                        "primary_scenario": "breakup",
                        "secondary_scenarios": ["boundary"],
                        "goals": ["end_relationship", "set_boundary"],
                    },
                },
            ],
        },
    ]

    metrics = calculate_phase321_repeatability_metrics(runs, ["one", "two"])

    assert metrics["branch_agreement_rate"] == 1.0
    assert metrics["primary_scenario_agreement_rate"] == 1.0
    assert metrics["scenario_top2_set_agreement_rate"] == 0.5
    assert metrics["goal_jaccard_agreement"] == 0.75
    assert metrics["exact_goal_set_agreement_rate"] == 0.5


def test_phase321_writer_uses_frozen_paths_without_placeholder_reports(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match=r"missing Phase 3\.2\.1 reports"):
        write_phase321_reports({"goal_policy_ablation": {"ok": True}}, tmp_path)

    written = write_phase321_reports(
        {"goal_policy_ablation": {"ok": True}},
        tmp_path,
        require_complete=False,
    )

    path = written["goal_policy_ablation"]
    assert path.name == PHASE321_OUTPUT_FILENAMES["goal_policy_ablation"]
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_phase321_formal_writer_rejects_complete_placeholder_bundle(
    tmp_path: Path,
) -> None:
    output = tmp_path / "formal"

    with pytest.raises(ValueError, match="not a real Live report"):
        write_phase321_reports(
            {name: {} for name in PHASE321_OUTPUT_FILENAMES},
            output,
            require_complete=True,
        )

    assert not output.exists()


@pytest.mark.asyncio
async def test_phase321_evaluator_records_weight_and_sanitization_trace() -> None:
    result = RouteResult(
        normalized_query="synthetic relationship query",
        task_type=TaskType.RELATIONSHIP_ADVICE,
        task_confidence=0.9,
        primary_scenario=AdviceScenario.CONFLICT,
        primary_goal=AdviceGoal.REPAIR,
        scenario_scores={AdviceScenario.CONFLICT: 1.0},
        goal_scores={AdviceGoal.REPAIR: 1.0},
        final_scenario_weights={AdviceScenario.CONFLICT: 1.0},
        final_goal_weights={AdviceGoal.REPAIR: 1.0},
        semantic_bypass_reason="deterministic_system_command",
        router_llm_raw_decision={"goals": ["repair", "repair"]},
        router_llm_sanitized_decision={"goals": ["repair"]},
        router_semantic_sanitization_count=1,
        router_semantic_sanitization_reasons=["duplicate_label_removed"],
    )

    class Router:
        async def route(self, route_input):
            return result

    case = RouterSafetyCase(
        case_id="synthetic",
        query_type="synthetic",
        difficulty="easy",
        length_bucket="short",
        expected_branch="rag",
        expected_primary_scenario="conflict",
        expected_secondary_scenarios=(),
        relationship_stage=None,
        expected_goals=("repair",),
        expected_risk_level="normal",
        query="synthetic relationship query",
    )

    rows, *_ = await _evaluate_cases(Router(), [case], metadata={})

    assert rows[0]["actual"]["final_scenario_weights"] == {"conflict": 1.0}
    assert rows[0]["actual"]["final_goal_weights"] == {"repair": 1.0}
    assert rows[0]["trace"]["semantic_bypass_reason"] == "deterministic_system_command"
    assert rows[0]["trace"]["llm_raw_decision"] == {"goals": ["repair", "repair"]}
    assert rows[0]["trace"]["llm_sanitized_decision"] == {"goals": ["repair"]}
    assert rows[0]["trace"]["semantic_sanitization_reasons"] == [
        "duplicate_label_removed"
    ]


def test_phase321_report_aggregates_boundary_diagnostics(tmp_path: Path) -> None:
    cases = [
        RouterSafetyCase(
            case_id=case_id,
            query_type="synthetic",
            difficulty="easy",
            length_bucket="short",
            expected_branch="rag",
            expected_primary_scenario="conflict",
            expected_secondary_scenarios=(),
            relationship_stage=None,
            expected_goals=("repair",),
            expected_risk_level="normal",
            query=case_id,
        )
        for case_id in ("sanitized", "unexpected-bypass")
    ]
    sanitized = _row(
        "sanitized",
        actual_scenario="boundary",
        called=True,
    )
    sanitized["actual"].update(
        {"risk_level": "normal", "secondary_scenarios": ["conflict"]}
    )
    sanitized["trace"].update(
        {
            "llm_call_count": 1,
            "llm_attempt_count": 1,
            "semantic_sanitization_count": 2,
            "semantic_sanitization_reasons": [
                "duplicate_label_removed",
                "goal_order_normalized",
            ],
        }
    )
    bypass = _row("unexpected-bypass")
    bypass["actual"]["risk_level"] = "normal"
    bypass["trace"].update(
        {
            "llm_call_count": 0,
            "llm_attempt_count": 0,
            "semantic_sanitization_count": 0,
            "semantic_sanitization_reasons": [],
        }
    )

    report = _build_report(
        tmp_path / "synthetic-dev.md",
        cases,
        [sanitized, bypass],
        latencies=(1.0, 2.0),
        llm_latencies=(1.0,),
        input_tokens=0,
        output_tokens=0,
        metadata={"provider": "synthetic-live", "live_llm": True},
        arm="always",
        arm_alias=None,
        input_cost_per_million=None,
        output_cost_per_million=None,
    )

    assert report["semantic_sanitization_count"] == 2
    assert report["semantic_sanitization_case_count"] == 1
    assert report["semantic_sanitization_rate"] == 1.0
    assert report["semantic_sanitization_reasons"] == {
        "duplicate_label_removed": 1,
        "goal_order_normalized": 1,
    }
    assert report["always_llm_bypass_count"] == 1
    assert report["always_unexpected_bypass_count"] == 1
    assert report["scenario_primary_disagreement_count"] == 1
    assert report["scenario_top2_disagreement_count"] == 0
    assert report["scenario_top2_recovery_count"] == 1

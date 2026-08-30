from pathlib import Path

import pytest

from loveapp.evaluation.dateplan import evaluate_dateplan, render_dateplan_report

DATASET = Path(__file__).parents[1] / "evals" / "dateplan" / "dateplan_cases_v1.jsonl"


@pytest.mark.asyncio
async def test_dateplan_evaluation_records_state_patch_validation_and_completion() -> None:
    report = await evaluate_dateplan(DATASET)

    assert report["scenario_count"] == 10
    assert report["turn_count"] == 16
    assert report["patch_accuracy"] == pytest.approx(1.0)
    assert report["state_preservation_accuracy"] == pytest.approx(1.0)
    assert report["validation_accuracy"] == pytest.approx(1.0)
    assert next(case for case in report["cases"] if case["id"] == "DP-001")["passed"] is True
    # A past date is intentionally kept as an immutable baseline expectation;
    # the failure documents that the current workflow has no reference-clock
    # expiry validation rather than changing the expectation to fit behavior.
    expired = next(case for case in report["cases"] if case["id"] == "DP-008")
    assert expired["passed"] is False
    assert expired["failures"][0]["attribution"] == "Workflow state transition"


@pytest.mark.asyncio
async def test_dateplan_evaluation_filters_cases_and_keeps_full_trace() -> None:
    report = await evaluate_dateplan(DATASET, case_id="DP-003")

    assert report["scenario_count"] == 1
    assert report["cases"][0]["id"] == "DP-003"
    turn = report["cases"][0]["turns"][1]
    assert turn["db_before"]["budget"] == 300
    assert turn["db_after"]["budget"] == 500
    assert turn["actual"]["trusted_context_patch_isolated"] is True
    assert any(record["name"] == "date_patch_apply" for record in turn["trace"])
    assert any(record["name"] == "date_plan_validation" for record in turn["trace"])


def test_dateplan_report_has_failure_attribution() -> None:
    report = {
        "scenario_count": 1,
        "turn_count": 1,
        "scenario_pass_rate": 0.0,
        "patch_accuracy": 0.0,
        "state_preservation_accuracy": 1.0,
        "validation_accuracy": 1.0,
        "final_plan_completion_rate": 0.0,
        "cases": [
            {
                "id": "DP-X",
                "category": "test",
                "passed": False,
                "failures": [
                    {
                        "assertion": "patch_fields",
                        "expected": {"budget": 300},
                        "actual": {"budget": 500},
                        "attribution": "Patch extraction",
                    }
                ],
            }
        ],
    }

    rendered = render_dateplan_report(report)
    assert "DP-X" in rendered
    assert "Patch extraction" in rendered

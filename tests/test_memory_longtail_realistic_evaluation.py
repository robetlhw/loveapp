from pathlib import Path

import pytest

from loveapp.evaluation.memory_longtail_realistic import (
    evaluate_memory_longtail_realistic,
    render_longtail_realistic_report,
)

DATASET = Path(__file__).parents[1] / "evals" / "memory" / "longtail_realistic_v1.jsonl"


@pytest.mark.asyncio
async def test_realistic_longtail_dataset_is_multiturn_and_shadow_only() -> None:
    report = await evaluate_memory_longtail_realistic(DATASET)

    assert report["scenario_count"] == 26
    assert report["turn_count"] == 50
    assert report["store_mutation_permitted"] is False
    assert report["metrics"]["false_destructive_update_count"] == 0
    assert report["metrics"]["confirmed_overwrite_violation_count"] == 0
    assert report["metrics"]["retrieval_recall_at_5"] >= 0.9
    assert report["metrics"]["gate_recall"] >= 0.75
    assert report["metrics"]["gate_expected_negative_count"] == 3
    assert report["metrics"]["gate_true_negative_count"] == 3
    assert report["metrics"]["gate_false_positive_count"] == 0
    assert report["metrics"]["gate_precision"] == 1.0
    assert all(
        "error_attribution" in claim
        for case in report["cases"]
        for turn in case["turns"]
        for claim in turn["claim_results"]
    )


@pytest.mark.asyncio
async def test_realistic_longtail_filters_case_and_category() -> None:
    by_case = await evaluate_memory_longtail_realistic(DATASET, case_id="LT-R-001")
    assert by_case["scenario_count"] == 1
    assert by_case["case_filter"] == "LT-R-001"

    by_category = await evaluate_memory_longtail_realistic(
        DATASET,
        category="ambiguous_target",
    )
    assert by_category["scenario_count"] == 1
    assert by_category["cases"][0]["id"] == "LT-B-001"


@pytest.mark.asyncio
async def test_realistic_longtail_repeat_preserves_separate_runs() -> None:
    report = await evaluate_memory_longtail_realistic(DATASET, case_id="LT-S-001", repeat=2)

    assert report["repeat"] == 2
    assert report["evaluated_row_count"] == 2
    assert len(report["runs"]) == 2
    assert (
        report["runs"][0]["cases"][0]["final_virtual_memory_ids"]
        == report["runs"][1]["cases"][0]["final_virtual_memory_ids"]
    )


def test_realistic_longtail_report_is_reviewable() -> None:
    rendered = render_longtail_realistic_report(
        {
            "dataset": "fixture.jsonl",
            "dataset_sha256": "abc",
            "scenario_count": 1,
            "turn_count": 2,
            "evaluation_mode": "shadow_fixture",
            "store_mutation_permitted": False,
            "metrics": {
                "gate_recall": 1.0,
                "retrieval_recall_at_5": 1.0,
                "relation_accuracy": 1.0,
                "false_destructive_update_count": 0,
                "error_attribution": {},
            },
            "cases": [],
        }
    )

    assert "Memory Long-tail Realistic Evaluation" in rendered
    assert "retrieval_recall_at_5" in rendered

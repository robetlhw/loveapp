from pathlib import Path

import pytest

from loveapp.evaluation.memory_longtail_realistic import (
    _load_cases,
    evaluate_memory_longtail_realistic,
    render_longtail_realistic_report,
)

DATASET = Path(__file__).parents[1] / "evals" / "memory" / "longtail_realistic_v1.jsonl"


def test_realistic_longtail_custom_semantic_reviews_are_explicit_and_scoped() -> None:
    cases = {case["id"]: case for case in _load_cases(DATASET.read_bytes())}

    def claim(case_id: str, turn_id: str, claim_id: str) -> dict[str, object]:
        case = cases[case_id]
        turn = next(item for item in case["turns"] if item["turn_id"] == turn_id)
        return next(item for item in turn["claims"] if item["id"] == claim_id)

    reviewed = {
        ("LT-R-003", "t1", "friends-old"): "social_circle_integration",
        ("LT-R-003", "t2", "friends-new"): "social_circle_integration",
        ("LT-S-002", "t1", "same-friends-old"): "social_circle_integration",
        ("LT-S-002", "t2", "same-friends-new"): "social_circle_integration",
        ("LT-E-001", "t2", "event-pattern-new"): "contact_absence",
        ("LT-E-002", "t1", "event-invite-old"): "social_circle_integration",
        ("LT-E-002", "t2", "event-invite-new"): "excluded_from_gathering",
        ("LT-P-001", "t1", "belief-old"): "social_circle_integration",
        ("LT-A-002", "t2", "partial-intro"): "social_circle_integration",
        ("LT-B-001", "t1", "ambiguous-invite"): "social_circle_integration",
        ("LT-B-001", "t1", "ambiguous-intro"): "social_circle_integration",
        ("LT-B-001", "t2", "ambiguous-new"): "social_circle_integration",
        ("LT-H-002", "t1", "topic-conflict"): "conflict_frequency",
    }
    for key, alias in reviewed.items():
        representations = claim(*key)["acceptable_representations"]
        matching = [
            representation
            for representation in representations
            if alias in representation.get("custom_predicates", [])
        ]
        assert len(matching) == 1
        reviewed_alias = matching[0]
        assert reviewed_alias.get("payload_constraints") or reviewed_alias.get(
            "evidence_contains_any"
        )

    excluded = (
        ("LT-P-001", "t2", "belief-new"),
        ("LT-C-001", "t1", "comp-friend"),
        ("LT-C-001", "t2", "comp-parent"),
        ("LT-A-002", "t1", "partial-invite-intro"),
        ("LT-U-001", "t2", "unrelated-social"),
        ("LT-U-002", "t2", "unrelated-restaurant"),
    )
    for key in excluded:
        assert "acceptable_representations" not in claim(*key)


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

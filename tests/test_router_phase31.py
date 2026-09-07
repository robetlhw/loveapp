from pathlib import Path

from loveapp.evaluation.router_phase31 import (
    CHALLENGE_SLICES,
    evaluate_phase31_dataset,
    load_router_challenge_cases,
    validate_router_challenge_dataset,
)

ROOT = Path(__file__).parents[1]
CHALLENGE_PATH = ROOT / "evals" / "rag" / "phase3_1" / "loveapp_router_challenge_dev_v1.md"
REFERENCE_PATHS = (
    ROOT / "evals" / "rag" / "phase3_5" / "loveapp_router_safety_eval_dev_v1.md",
    ROOT / "evals" / "rag" / "phase3_5" / "loveapp_router_safety_eval_test_v1.md",
)


def test_phase31_challenge_fixture_has_expected_shape_and_lint() -> None:
    cases = load_router_challenge_cases(CHALLENGE_PATH)
    lint = validate_router_challenge_dataset(cases, reference_paths=REFERENCE_PATHS)

    assert len(cases) == 120
    assert [case.case_id for case in cases] == [
        f"phase31_challenge_{index:03d}" for index in range(1, 121)
    ]
    assert lint["passed"] is True
    assert lint["errors"] == []
    assert lint["cross_file_overlap_count"] == 0
    assert lint["id_overlap_count"] == 0
    assert lint["slice_counts"] == {
        "short_colloquial": 60,
        "scenario_hard_confusion": 40,
        "goal_multilabel": 40,
    }


def test_phase31_compatibility_validator_uses_explicit_slice_membership() -> None:
    from loveapp.evaluation.router_phase31 import validate_phase31_challenge_dataset

    lint = validate_phase31_challenge_dataset(
        CHALLENGE_PATH,
        old_dev=REFERENCE_PATHS[0],
        old_test=REFERENCE_PATHS[1],
    )

    assert lint["short_colloquial_count"] == 60
    assert lint["hard_confusion_count"] == 40
    assert lint["multi_label_goal_count"] == 40
    assert lint["passed"] is True


def test_phase31_slice_contracts_are_explicit() -> None:
    cases = load_router_challenge_cases(CHALLENGE_PATH)

    assert set(CHALLENGE_SLICES) == {
        "short_colloquial",
        "scenario_hard_confusion",
        "goal_multilabel",
    }
    short_cases = [case for case in cases if "short_colloquial" in case.challenge_slices]
    hard_cases = [case for case in cases if "scenario_hard_confusion" in case.challenge_slices]
    multilabel_cases = [case for case in cases if "goal_multilabel" in case.challenge_slices]

    assert len(short_cases) == 60
    assert all(case.query_type == "colloquial" for case in short_cases)
    assert all(case.length_bucket == "short" for case in short_cases)
    assert len(hard_cases) == 40
    assert all(case.difficulty == "hard" for case in hard_cases)
    assert len(multilabel_cases) == 40
    assert all(len(case.expected_goals) >= 2 for case in multilabel_cases)


def test_phase31_hard_confusion_pairs_and_ood_cases_are_annotated() -> None:
    cases = load_router_challenge_cases(CHALLENGE_PATH)
    by_id = {case.case_id: case for case in cases}

    assert all(
        by_id[f"phase31_challenge_{index:03d}"].expected_secondary_scenarios
        for index in range(61, 101)
    )
    assert all(
        by_id[f"phase31_challenge_{index:03d}"].expected_branch == "out_of_scope"
        and by_id[f"phase31_challenge_{index:03d}"].expected_primary_scenario is None
        for index in range(55, 61)
    )
    assert all(
        by_id[f"phase31_challenge_{index:03d}"].expected_branch == "rag"
        for index in range(1, 55)
    )


def test_phase31_case_can_be_adapted_to_common_router_safety_case() -> None:
    case = load_router_challenge_cases(CHALLENGE_PATH)[80]
    adapted = case.as_router_safety_case()

    assert adapted.case_id == case.case_id
    assert adapted.query == case.query
    assert adapted.expected_primary_scenario == "pursuit"
    assert adapted.expected_secondary_scenarios == ("chat_analysis",)
    assert adapted.expected_goals == ("understand", "progress")


async def test_phase31_evaluator_preserves_canonical_challenge_slice_denominators() -> None:
    report = await evaluate_phase31_dataset(CHALLENGE_PATH, arm="rule")

    assert report["slices"]["scenario_hard_confusion"]["count"] == 40
    assert report["slices"]["goal_multilabel"]["count"] == 40
    # Historical aliases remain available for existing report consumers.
    assert report["slices"]["hard_confusion"]["count"] == 40
    assert report["slices"]["multi_label_goals"]["count"] == 40
    assert report["safety"]["safety_support"] == 0
    assert report["safety"]["high_risk_recall_defined"] is False

from __future__ import annotations

from loveapp.evaluation.rag_v2_metadata import (
    MetadataFilterExperimentConfig,
    compare_metadata_filter_reports,
    render_metadata_filter_comparison,
)


def _row(
    *,
    candidate_ids: list[str],
    returned_ids: list[str],
    predicted_scenario: str = "relationship_maintenance",
    predicted_goals: list[str] | None = None,
    branch: str = "rag",
    expected_branch: str = "rag",
) -> dict:
    return {
        "id": "rag_v2_dev_001",
        "query": "她突然不回我消息怎么办？",
        "expected_branch": expected_branch,
        "predicted_branch": branch,
        "scenario": "breakup",
        "predicted_scenario": predicted_scenario,
        "goals": ["repair"],
        "predicted_goals": predicted_goals or ["repair"],
        "relevant_ids": ["kb_gold"],
        "nearest_candidate_ids": candidate_ids,
        "candidate_ids": candidate_ids,
        "returned_ids": returned_ids,
        "diagnostics_available": True,
        "answered_case": expected_branch == "rag",
        "in_domain_no_answer": False,
        "retrieval_called": True,
        "covered": bool(returned_ids),
        "branch_correct": branch == expected_branch,
        "hit_at_3": bool(set(returned_ids[:3]) & {"kb_gold"}),
        "mrr": 1.0 if returned_ids and returned_ids[0] == "kb_gold" else 0.0,
        "candidate_recall": 1.0 if "kb_gold" in candidate_ids else 0.0,
        "coverage": 1.0 if returned_ids else 0.0,
    }


def test_frozen_config_only_changes_hard_filter() -> None:
    hard = MetadataFilterExperimentConfig.frozen(hard_filter=True).model_dump(mode="json")
    soft = MetadataFilterExperimentConfig.frozen(hard_filter=False).model_dump(mode="json")

    assert hard["hard_filter"] is True
    assert soft["hard_filter"] is False
    hard.pop("hard_filter")
    soft.pop("hard_filter")
    assert hard == soft


def test_amplification_marks_candidate_and_topk_recovery() -> None:
    hard_row = _row(candidate_ids=["kb_other"], returned_ids=["kb_other"])
    hard_row["trace"] = [
        {"name": "rag_vector_search", "details": {"hard_filter": True}}
    ]
    hard = {"mode": "e2e", "cases": [hard_row]}
    soft = {"mode": "e2e", "cases": [_row(candidate_ids=["kb_gold"], returned_ids=["kb_gold"])]}

    report = compare_metadata_filter_reports(hard, soft)

    amplification = report["hard_filter_amplification"]
    assert amplification["hard_filter_amplification_count"] == 1
    assert amplification["hard_filter_candidate_recovery"] == 1
    assert amplification["hard_filter_top3_recovery"] == 1
    assert report["router_correctness_slices"]["metadata_incorrect"]["case_count"] == 1
    assert report["error_attribution"]["hard"]["hard_filter_amplification"] == 1


def test_correct_router_slice_is_separate_from_metadata_error_slice() -> None:
    correct = _row(
        candidate_ids=["kb_gold"],
        returned_ids=["kb_gold"],
        predicted_scenario="breakup",
    )
    incorrect = _row(candidate_ids=["kb_other"], returned_ids=["kb_other"])
    incorrect["trace"] = [
        {"name": "rag_vector_search", "details": {"hard_filter": True}}
    ]
    hard = {"mode": "e2e", "cases": [correct, {**incorrect, "id": "rag_v2_dev_002"}]}
    soft = {
        "mode": "e2e",
        "cases": [
            correct,
            {
                **incorrect,
                "id": "rag_v2_dev_002",
                "candidate_ids": ["kb_gold"],
                "nearest_candidate_ids": ["kb_gold"],
                "returned_ids": ["kb_gold"],
                "hit_at_3": True,
                "mrr": 1.0,
                "candidate_recall": 1.0,
            },
        ],
    }

    report = compare_metadata_filter_reports(hard, soft)

    assert report["router_correctness_slices"]["metadata_correct"]["case_count"] == 1
    assert report["router_correctness_slices"]["metadata_incorrect"]["case_count"] == 1
    assert report["router_correctness_slices"]["metadata_incorrect"]["soft"]["hit_at_3"] == 1.0


def test_renderer_includes_required_sections_and_delta_definition() -> None:
    report = compare_metadata_filter_reports(
        {"mode": "e2e", "cases": []},
        {"mode": "e2e", "cases": []},
        dataset="dev",
    )

    markdown = render_metadata_filter_comparison(report)
    assert "## Overall" in markdown
    assert "## Router correctness slices" in markdown
    assert "## Error attribution" in markdown
    assert "## Case studies" in markdown
    assert "soft_minus_hard" in markdown
    assert "## Relationship-stage evaluation boundary" in markdown


def test_in_domain_no_answer_failure_is_not_dataset_issue() -> None:
    hard_row = _row(candidate_ids=[], returned_ids=[])
    hard_row.update(
        {
            "id": "rag_v2_dev_002",
            "relevant_ids": [],
            "goals": [],
            "in_domain_no_answer": True,
            "abstained": False,
            "answered_case": False,
        }
    )
    soft_row = {**hard_row, "abstained": False}
    report = compare_metadata_filter_reports(
        {"mode": "e2e", "cases": [hard_row]},
        {"mode": "e2e", "cases": [soft_row]},
    )
    assert report["error_attribution"]["paired"].get("no_answer_error") == 1
    assert report["error_attribution"]["paired"].get("gold_or_dataset_issue", 0) == 0


def test_trace_evidence_is_exposed_for_amplification_cases() -> None:
    hard = _row(candidate_ids=["kb_other"], returned_ids=["kb_other"])
    hard["nearest_candidate_ids"] = ["kb_other"]
    hard["trace"] = [
        {"name": "rag_vector_search", "details": {"hard_filter": True}},
    ]
    soft = _row(candidate_ids=["kb_gold"], returned_ids=["kb_gold"])
    soft["nearest_candidate_ids"] = ["kb_gold"]
    soft["trace"] = [
        {"name": "rag_vector_search", "details": {"hard_filter": False}},
    ]
    report = compare_metadata_filter_reports(
        {"mode": "e2e", "cases": [hard]},
        {"mode": "e2e", "cases": [soft]},
    )
    evidence = report["hard_filter_trace_evidence"]
    assert evidence["case_count"] == 1
    case = evidence["cases"][0]
    assert case["hard_nearest_contains_gold"] is False
    assert case["soft_nearest_contains_gold"] is True
    assert case["hard_filter_applied_before_dense_candidate"] is True


def test_trace_can_confirm_hard_filter_was_applied_before_dense_search() -> None:
    hard = {
        "mode": "e2e",
        "cases": [
            {
                **_row(candidate_ids=["kb_other"], returned_ids=["kb_other"]),
                "trace": [
                    {
                        "name": "rag_vector_search",
                        "details": {"hard_filter": True},
                    }
                ],
            }
        ],
    }
    soft = {
        "mode": "e2e",
        "cases": [_row(candidate_ids=["kb_gold"], returned_ids=["kb_gold"])],
    }

    report = compare_metadata_filter_reports(hard, soft)

    pair = report["paired_cases"][0]
    assert pair["hard_filter_applied_before_dense_candidate"] is True
    assert report["hard_filter_amplification"]["hard_filter_amplification_count"] == 1

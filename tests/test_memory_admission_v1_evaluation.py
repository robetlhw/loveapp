import asyncio
from pathlib import Path

from loveapp.evaluation.memory_admission_v1 import (
    evaluate_memory_admission_integration,
    evaluate_memory_admission_v1,
    load_memory_admission_v1_cases,
    render_memory_admission_policy_review,
    render_memory_admission_strong_review_audit,
    render_memory_admission_v1_report,
)

DATASET = Path("evals/memory/admission_v1.jsonl")


def test_admission_v1_dataset_has_64_strict_and_8_policy_review_cases() -> None:
    cases = load_memory_admission_v1_cases(DATASET)

    assert len(cases) == 72
    assert [case.case_id for case in cases] == [f"ADM-{index:03d}" for index in range(1, 73)]
    assert sum(case.contract_status == "EXACT" for case in cases) == 64
    assert sum(case.contract_status == "POLICY_REVIEW" for case in cases) == 8


def test_admission_v1_strict_baseline_matches_current_production_contract() -> None:
    report = evaluate_memory_admission_v1(DATASET, fail_on_error=True)
    metrics = report["metrics"]

    assert report["strict_case_count"] == 64
    assert report["strict_passed_case_count"] == 64
    assert metrics["decision_accuracy"] == 1.0
    assert metrics["reason_accuracy"] == 1.0
    assert metrics["score_mae"] == 0.0
    assert metrics["score_max_abs_error"] == 0.0
    assert report["policy_snapshot_drift"] is False
    assert report["production_path_audit"]["high_risk_policy_review_cases"] == ["ADM-065"]
    assert report["status"] == "BASELINE_PASS_POLICY_REVIEW_PENDING"
    assert metrics["safety"]["invalid_evidence_reject_recall"] == 1.0
    assert metrics["safety"]["speculative_relationship_state_reject_recall"] == 1.0
    assert metrics["safety"]["dangerous_direct_confirm_violation_count"] == 0


def test_admission_v1_reports_all_decision_kind_and_slice_metrics() -> None:
    report = evaluate_memory_admission_v1(DATASET)
    metrics = report["metrics"]

    assert set(metrics["per_decision"]) == {"confirm", "propose", "strong_review", "reject"}
    assert set(metrics["per_kind"]) == {
        "stable_fact",
        "preference",
        "interaction_event",
        "interaction_pattern",
        "advice_outcome",
        "planned_event",
        "action_intent",
        "relationship_state",
    }
    assert metrics["pattern"]["frequency_detection_accuracy"] == 1.0
    assert metrics["pattern"]["multi_evidence_detection_accuracy"] == 1.0
    assert metrics["pattern"]["corroboration_handling_accuracy"] == 1.0
    assert metrics["temporal"]["invalid_temporal_reason_accuracy"] == 1.0


def test_admission_v1_policy_review_is_observe_only() -> None:
    report = evaluate_memory_admission_v1(DATASET)
    review = report["policy_review"]

    assert len(review) == 8
    assert {row["case_id"] for row in review} == {f"ADM-{index:03d}" for index in range(65, 73)}
    assert all("policy_classification" in row for row in review)
    assert all("recommended_policy" in row for row in review)
    assert report["contract"]["policy_review_cases_scored"] is False
    assert all("current_breakdown" in row for row in review)
    assert all("current_code_path" in row for row in review)
    assert all("review_category" in row for row in review)


def test_admission_v1_report_renderers_include_diagnostics() -> None:
    report = evaluate_memory_admission_v1(DATASET)

    rendered = render_memory_admission_v1_report(report)
    policy = render_memory_admission_policy_review(report)
    strong = render_memory_admission_strong_review_audit(report)

    assert "Strict Metrics (64 EXACT cases)" in rendered
    assert "Decision Precision / Recall" in rendered
    assert "invalid_evidence_reject_recall" in rendered
    assert "ADM-065" in policy
    assert "observe-only" in policy
    assert "Current score breakdown" in policy
    assert "Current code path" in policy
    assert "StrongClaimVerifier" in strong


def test_admission_v1_integration_diagnostic_uses_isolated_store_and_12_cases() -> None:
    report = asyncio.run(evaluate_memory_admission_integration(DATASET))

    assert report["case_count"] == 12
    assert report["passed_case_count"] == 12
    assert report["production_store_mutation_permitted"] is False
    assert report["isolated_in_memory_store_mutation"] is True
    assert report["model_calls_permitted"] is False
    assert report["strong_called_count"] == 3
    assert report["ttl_checked_count"] == 1
    assert report["ttl_passed_count"] == 1
    assert report["ttl_diagnostic"]["case_id"] == "ADM-071"
    assert report["ttl_diagnostic"]["passed"] is True
    assert all(row["passed"] for row in report["rows"])
    assert {
        row["expected_decision"]: (row["planned_action"], row["planned_status"])
        for row in report["rows"]
    } == {
        "confirm": ("add", "confirmed"),
        "propose": ("add", "proposed"),
        "strong_review": ("add", "proposed"),
        "reject": ("reject", None),
    }
    assert all(row["transition_audit_written"] for row in report["rows"])
    assert report["ttl_diagnostic"]["case_id"] == "ADM-071"
    assert report["ttl_diagnostic"]["ttl_days"] == 14
    assert report["ttl_diagnostic"]["passed"] is True
    assert (
        report["ttl_diagnostic"]["expected_expires_at"]
        == report["ttl_diagnostic"]["actual_expires_at"]
    )


def test_admission_v1_loader_rejects_incomplete_dataset(tmp_path: Path) -> None:
    rows = DATASET.read_text(encoding="utf-8").splitlines()
    path = tmp_path / "incomplete.jsonl"
    path.write_text("\n".join(rows[:1]) + "\n", encoding="utf-8")

    try:
        load_memory_admission_v1_cases(path)
    except ValueError as exc:
        assert "ids differ" in str(exc)
    else:
        raise AssertionError("incomplete Admission dataset unexpectedly loaded")

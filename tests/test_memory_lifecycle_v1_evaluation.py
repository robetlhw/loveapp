import asyncio
import json
from pathlib import Path

import pytest

from loveapp.evaluation.memory_lifecycle import (
    REPORT_VERSION,
    evaluate_memory_lifecycle,
    evaluate_memory_lifecycle_integration,
    evaluate_memory_lifecycle_v1,
    load_memory_lifecycle_v1_cases,
    render_memory_lifecycle_integration_diagnostic,
    render_memory_lifecycle_policy_review,
    render_memory_lifecycle_v1_report,
)

DATASET = Path("evals/memory/lifecycle_v1.jsonl")


def test_lifecycle_v1_dataset_is_complete_and_versioned() -> None:
    cases = load_memory_lifecycle_v1_cases(DATASET)

    assert len(cases) == 72
    assert [case["case_id"] for case in cases] == [
        f"LIFE-{index:03d}" for index in range(1, 73)
    ]
    assert sum(case["contract_status"] == "EXACT" for case in cases) == 64
    assert sum(case["contract_status"] == "POLICY_REVIEW" for case in cases) == 8
    assert sum(case["operation"] == "plan_transitions" for case in cases[:64]) == 40
    assert sum(case["operation"] == "semantic_duplicates" for case in cases[:64]) == 16
    assert sum(case["operation"] == "legacy_transition_targets" for case in cases[:64]) == 8


def test_lifecycle_v1_baseline_is_frozen_after_remediation() -> None:
    report = evaluate_memory_lifecycle_v1(DATASET, fail_on_error=True)
    metrics = report["metrics"]

    assert report["version"] == REPORT_VERSION
    assert report["case_count"] == 72
    assert report["strict_case_count"] == 64
    assert report["strict_passed_case_count"] == 64
    assert report["policy_review_case_count"] == 8
    assert report["status"] == "ENGINEERING_FROZEN_WITH_KNOWN_POLICY_DEBT"
    assert metrics["overall_strict_case_accuracy"] == 1.0
    assert metrics["plan_exact_match_accuracy"] == 1.0
    assert metrics["target_micro_precision"] == pytest.approx(1.0, abs=0.0001)
    assert metrics["target_micro_recall"] == pytest.approx(1.0, abs=0.0001)
    assert metrics["target_micro_f1"] == pytest.approx(1.0, abs=0.0001)
    assert metrics["all_operation_target_micro_recall"] == pytest.approx(
        1.0, abs=0.0001
    )
    assert metrics["duplicate_set_accuracy"] == 1.0
    assert metrics["legacy_target_set_accuracy"] == 1.0
    assert metrics["safety"]["proposed_closes_confirmed_violation_rate"] == 0.0
    assert metrics["safety"]["rejected_trigger_transition_rate"] == 0.0
    assert metrics["safety"]["keeper_accuracy"] == 1.0
    assert metrics["safety"]["ordinary_event_false_collapse_rate"] == 0.0
    assert metrics["error_taxonomy"] == {}


def test_lifecycle_v1_remediation_has_no_strict_failures_and_inputs_are_read_only() -> None:
    report = evaluate_memory_lifecycle_v1(DATASET)
    failures = {
        row["case_id"]: row
        for row in report["cases"]
        if row["contract_status"] == "EXACT" and not row["passed"]
    }

    assert failures == {}
    assert all(row["input_mutated"] is False for row in report["cases"])


def test_lifecycle_v1_remediation_preserves_concepts_precedence_and_legacy_ordering() -> None:
    report = evaluate_memory_lifecycle_v1(DATASET)
    rows = {row["case_id"]: row for row in report["cases"]}

    assert rows["LIFE-005"]["trace"]["trigger_concepts"] == [
        "response_restored"
    ]
    assert rows["LIFE-019"]["actual_rule_names"] == [
        "restore_contact_frequency"
    ]
    assert rows["LIFE-014"]["actual_rule_names"] == [
        "replace_state:contact_availability"
    ]
    assert rows["LIFE-062"]["actual_target_ids"] == []


def test_lifecycle_v1_supports_all_filters_and_async_compatibility() -> None:
    report = evaluate_memory_lifecycle_v1(DATASET, case_id="LIFE-023")
    assert report["case_count"] == 1
    assert report["strict_passed_case_count"] == 1
    assert report["cases"][0]["actual_output"] == []

    duplicates = evaluate_memory_lifecycle_v1(
        DATASET,
        operation="semantic_duplicates",
        contract_status="EXACT",
    )
    assert duplicates["case_count"] == 16
    assert duplicates["strict_passed_case_count"] == 16

    review = evaluate_memory_lifecycle_v1(DATASET, contract_status="POLICY_REVIEW")
    assert review["case_count"] == 8
    assert review["status"] == "POLICY_REVIEW_ONLY"

    compatible = asyncio.run(evaluate_memory_lifecycle(DATASET, case_id="LIFE-001"))
    assert compatible["strict_passed_case_count"] == 1

    with pytest.raises(ValueError, match="no Lifecycle V1 cases"):
        evaluate_memory_lifecycle_v1(DATASET, case_id="LIFE-999")


def test_lifecycle_v1_reports_include_drift_and_policy_diagnostics() -> None:
    report = evaluate_memory_lifecycle_v1(DATASET)
    rendered = render_memory_lifecycle_v1_report(report)
    policy = render_memory_lifecycle_policy_review(report)

    assert "Strict Metrics" in rendered
    assert "Safety / Governance" in rendered
    assert "Failed Strict Cases" in rendered
    assert "| none |" in rendered
    assert "LIFE-065" in policy
    assert "observe-only" in policy
    assert "CALLER_ACTIVE_SET_VIOLATION" in policy


def test_lifecycle_v1_isolated_integration_exercises_store_status_and_audit() -> None:
    report = asyncio.run(evaluate_memory_lifecycle_integration(DATASET))

    assert report["case_count"] == 19
    # The integration sample covers explicit transitions, generic replacements,
    # authority protection, rejected input, duplicate reconciliation, and
    # legacy ordering including the repaired no-op case LIFE-062.
    assert report["planned_contract_match_count"] == 19
    assert report["expected_store_outcome_pass_count"] == 19
    assert report["planned_write_application_pass_count"] == 19
    assert report["store_application_pass_count"] == 19
    assert report["actual_status_transition_count"] == 16
    assert report["passed_case_count"] == 19
    assert {
        row["case_id"] for row in report["rows"]
    } == {
        "LIFE-001",
        "LIFE-005",
        "LIFE-018",
        "LIFE-019",
        "LIFE-009",
        "LIFE-010",
        "LIFE-012",
        "LIFE-013",
        "LIFE-014",
        "LIFE-017",
        "LIFE-023",
        "LIFE-026",
        "LIFE-028",
        "LIFE-041",
        "LIFE-045",
        "LIFE-050",
        "LIFE-057",
        "LIFE-058",
        "LIFE-062",
    }
    assert report["production_store_mutation_permitted"] is False
    assert report["isolated_in_memory_store_mutation"] is True
    assert report["model_calls_permitted"] is False
    conflict = next(row for row in report["rows"] if row["case_id"] == "LIFE-009")
    assert conflict["planned_rule_names"] == ["resolve_active_conflict"]
    assert set(conflict["planned_actual_target_ids"]) <= set(conflict["actual_closed_ids"])
    proposed = next(row for row in report["rows"] if row["case_id"] == "LIFE-023")
    assert proposed["planned_fixture_target_ids"] == []
    assert proposed["untouched_memory_ids"]
    assert all(row["commit_error"] is None for row in report["rows"])
    assert all(row["store_application_exercised"] for row in report["rows"])
    assert (
        "Expected Store outcomes passed"
        in render_memory_lifecycle_integration_diagnostic(report)
    )
    assert "Isolated write batches applied" in render_memory_lifecycle_integration_diagnostic(
        report
    )


def test_lifecycle_v1_loader_rejects_incomplete_dataset(tmp_path: Path) -> None:
    first = DATASET.read_text(encoding="utf-8").splitlines()[0]
    path = tmp_path / "incomplete.jsonl"
    path.write_text(first + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="case ids differ"):
        load_memory_lifecycle_v1_cases(path)


def test_lifecycle_v1_report_is_json_serializable() -> None:
    report = evaluate_memory_lifecycle_v1(DATASET)
    encoded = json.dumps(report, ensure_ascii=False)
    assert json.loads(encoded)["version"] == REPORT_VERSION

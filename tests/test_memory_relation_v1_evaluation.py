import asyncio
import json
from pathlib import Path

import pytest

from loveapp.evaluation.memory_relation_v1 import (
    REPORT_VERSION,
    evaluate_memory_relation_integration,
    evaluate_memory_relation_v1,
    load_memory_relation_v1_cases,
    render_memory_relation_integration_diagnostic,
    render_memory_relation_policy_review,
    render_memory_relation_v1_report,
)

DATASET = Path("evals/memory/relation_v1.jsonl")


def test_relation_v1_dataset_is_complete_and_source_versioned() -> None:
    cases = load_memory_relation_v1_cases(DATASET)

    assert len(cases) == 72
    assert [case["case_id"] for case in cases] == [
        f"REL-{index:03d}" for index in range(1, 73)
    ]
    assert sum(case["contract_status"] == "EXACT" for case in cases) == 64
    assert sum(case["contract_status"] == "POLICY_REVIEW" for case in cases) == 8
    assert {case["expected"]["relation"] for case in cases[:64]} == {
        "same",
        "complementary",
        "update",
        "contradiction",
        "unrelated",
        "uncertain",
    }


def test_relation_v1_strict_baseline_matches_remediated_production_contract() -> None:
    report = evaluate_memory_relation_v1(DATASET, fail_on_error=True)
    metrics = report["metrics"]

    assert report["version"] == REPORT_VERSION
    assert report["case_count"] == 72
    assert report["strict_case_count"] == 64
    assert report["strict_passed_case_count"] == 64
    assert report["policy_review_case_count"] == 8
    assert report["status"] == "BASELINE_PASS_POLICY_REVIEW_PENDING"
    assert metrics["relation_accuracy"] == 1.0
    assert metrics["rule_name_accuracy"] == 1.0
    assert metrics["reason_accuracy"] == 1.0
    assert metrics["target_exact_match_accuracy"] == 1.0
    assert metrics["target_set_accuracy"] == 1.0
    assert metrics["target_micro_precision"] == 1.0
    assert metrics["target_micro_recall"] == 1.0
    assert metrics["safety"]["same_keeper_accuracy"] == 1.0
    assert metrics["safety"]["proposed_overwrites_confirmed_violation_rate"] == 0.0
    assert metrics["safety"]["cross_subject_false_link_rate"] == 0.0
    assert metrics["safety"]["unrelated_false_link_rate"] == 0.0
    assert metrics["error_taxonomy"] == {}


def test_relation_v1_remediated_cases_use_governed_relation_paths() -> None:
    report = evaluate_memory_relation_v1(DATASET)
    cases = {
        row["case_id"]: row
        for row in report["cases"]
        if row["case_id"] in {"REL-016", "REL-017", "REL-028", "REL-029", "REL-051"}
    }

    assert set(cases) == {"REL-016", "REL-017", "REL-028", "REL-029", "REL-051"}
    assert all(row["passed"] for row in cases.values())
    assert cases["REL-016"]["actual_relation"] == "update"
    assert cases["REL-017"]["actual_relation"] == "update"
    assert cases["REL-028"]["actual_relation"] == "contradiction"
    assert cases["REL-029"]["actual_relation"] == "contradiction"
    assert cases["REL-051"]["actual_relation"] == "uncertain"
    assert all(
        row["input_mutated"] is False
        for row in report["cases"]
    )


def test_relation_v1_supports_case_slice_and_contract_filters() -> None:
    report = evaluate_memory_relation_v1(DATASET, case_id="REL-011")
    assert report["case_count"] == 1
    assert report["strict_case_count"] == 1
    assert report["strict_passed_case_count"] == 1
    assert report["cases"][0]["actual_relation"] == "update"

    policy = evaluate_memory_relation_v1(DATASET, contract_status="POLICY_REVIEW")
    assert policy["case_count"] == 8
    assert policy["strict_case_count"] == 0
    assert policy["policy_review_case_count"] == 8

    with pytest.raises(ValueError, match="no Relation V1 cases"):
        evaluate_memory_relation_v1(DATASET, case_id="REL-999")


def test_relation_v1_report_renderers_include_diagnostics() -> None:
    report = evaluate_memory_relation_v1(DATASET)
    rendered = render_memory_relation_v1_report(report)
    policy = render_memory_relation_policy_review(report)

    assert "Strict Metrics" in rendered
    assert "Relation Precision / Recall" in rendered
    assert "target_micro_f1" in rendered
    assert "BASELINE_PASS_POLICY_REVIEW_PENDING" in rendered
    assert "| none | - | - | - | - |" in rendered
    assert "REL-065" not in rendered.split("## Failed Strict Cases", 1)[0]
    assert "REL-065" in policy
    assert "observe-only" in policy
    assert "current_code_path" not in policy


def test_relation_v1_isolated_integration_exercises_store_contract() -> None:
    report = asyncio.run(evaluate_memory_relation_integration(DATASET))

    assert report["case_count"] == 14
    assert report["passed_relation_count"] == 14
    assert report["production_store_mutation_permitted"] is False
    assert report["isolated_in_memory_store_mutation"] is True
    assert report["model_calls_permitted"] is False
    assert report["store_write_attempt_count"] == 14
    assert report["transition_audit_count"] == 14
    by_id = {row["case_id"]: row for row in report["rows"]}
    assert by_id["REL-028"]["claim_relation"] == "contradiction"
    assert by_id["REL-051"]["claim_relation"] == "uncertain"
    assert all(row["store_mutation_permitted"] is False for row in report["rows"])
    assert all(row["transition_audits"] for row in report["rows"])
    assert all(
        audit["relation"] == row["claim_relation"]
        for row in report["rows"]
        for audit in row["transition_audits"]
        if audit["incoming_memory_id"] is not None
    )
    rendered = render_memory_relation_integration_diagnostic(report)
    assert "Store writes attempted" in rendered
    assert "Final statuses" in rendered


def test_relation_v1_loader_rejects_missing_case_ids(tmp_path: Path) -> None:
    rows = DATASET.read_text(encoding="utf-8").splitlines()
    path = tmp_path / "incomplete.jsonl"
    path.write_text("\n".join(rows[:1]) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="case ids differ"):
        load_memory_relation_v1_cases(path)


def test_relation_v1_report_json_is_serializable() -> None:
    report = evaluate_memory_relation_v1(DATASET)
    encoded = json.dumps(report, ensure_ascii=False)
    assert json.loads(encoded)["version"] == REPORT_VERSION

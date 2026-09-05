import asyncio
import json
from collections import Counter
from pathlib import Path

import pytest
from typer.testing import CliRunner

import loveapp.cli as cli
from loveapp.evaluation.memory_longtail_write_v1 import (
    EXPECTED_CASE_COUNT,
    EXPECTED_POLICY_REVIEW_CASE_COUNT,
    EXPECTED_STRICT_CASE_COUNT,
    REPORT_VERSION,
    evaluate_memory_longtail_write_integration,
    evaluate_memory_longtail_write_v1,
    load_memory_longtail_write_v1_cases,
    render_memory_longtail_write_integration_diagnostic,
    render_memory_longtail_write_policy_review,
    render_memory_longtail_write_v1_report,
)

ROOT = Path(__file__).parents[1]
DATASET = ROOT / "evals/memory/longtail_write_v1.jsonl"


def test_longtail_write_v1_dataset_is_complete_and_immutable_shape() -> None:
    cases = load_memory_longtail_write_v1_cases(DATASET)

    assert len(cases) == EXPECTED_CASE_COUNT == 112
    assert [case["case_id"] for case in cases] == [
        f"LTW-{index:03d}" for index in range(1, EXPECTED_CASE_COUNT + 1)
    ]
    assert sum(case["contract_status"] == "EXACT" for case in cases) == (
        EXPECTED_STRICT_CASE_COUNT
    )
    assert sum(case["contract_status"] == "POLICY_REVIEW" for case in cases) == (
        EXPECTED_POLICY_REVIEW_CASE_COUNT
    )
    assert Counter(
        case["expected"]["relation"]
        for case in cases
        if case["contract_status"] == "EXACT"
    ) == Counter(
        {
            "same": 8,
            "complementary": 34,
            "update": 12,
            "contradiction": 17,
            "unrelated": 16,
            "uncertain": 9,
        }
    )


def test_longtail_write_v1_baseline_uses_production_resolver_read_only() -> None:
    report = evaluate_memory_longtail_write_v1(DATASET, fail_on_error=True)

    assert report["version"] == REPORT_VERSION
    assert report["case_count"] == 112
    assert report["strict_case_count"] == 96
    # The current deterministic resolver intentionally does not claim semantic
    # understanding for the fixture's long-tail custom predicates.  The
    # resulting drift is the baseline being measured, not a test expectation
    # to hide by injecting a second relation algorithm.
    assert report["strict_passed_case_count"] == 8
    assert report["status"] == "BASELINE_DRIFT_REQUIRES_REVIEW"
    assert report["production_store_mutation_permitted"] is False
    assert report["model_calls_permitted"] is False
    assert report["relation_authority"].endswith("resolve_claim_relation")
    assert report["metrics"]["per_relation"]["same"]["recall"] == 1.0
    assert report["metrics"]["target_exact_match_accuracy"] == pytest.approx(
        report["metrics"]["target_set_accuracy"]
    )
    assert report["metrics"]["safety"]["proposed_overwrites_confirmed_count"] == 0
    assert report["metrics"]["safety"]["historical_event_preservation_rate"] == 1.0
    for key in (
        "new_row_decision_accuracy",
        "final_status_accuracy",
        "supersede_exact_match_accuracy",
        "preserve_exact_match_accuracy",
    ):
        assert key in report["metrics"]
    assert all(
        row["checks"]["input_unchanged"]
        for row in report["cases"]
        if "input_unchanged" in row["checks"]
    )


def test_longtail_write_v1_supports_filters_and_rejects_empty_selection() -> None:
    one = evaluate_memory_longtail_write_v1(DATASET, case_id="LTW-017")
    assert one["case_count"] == 1
    assert one["cases"][0]["case_id"] == "LTW-017"

    updates = evaluate_memory_longtail_write_v1(DATASET, relation="update")
    assert updates["strict_case_count"] == 12
    assert updates["policy_review_case_count"] == 0

    live = evaluate_memory_longtail_write_v1(DATASET, live_subset=True)
    assert live["case_count"] == 24
    assert all(row["case_id"] for row in live["cases"])

    with pytest.raises(ValueError, match="no Long-tail Write V1 cases"):
        evaluate_memory_longtail_write_v1(DATASET, case_id="LTW-999")


def test_longtail_write_v1_report_renderers_include_diagnostics() -> None:
    report = evaluate_memory_longtail_write_v1(DATASET)

    rendered = render_memory_longtail_write_v1_report(report)
    policy = render_memory_longtail_write_policy_review(report)
    assert "Strict Metrics" in rendered
    assert "Relation Precision / Recall / F1" in rendered
    assert "target_micro_f1" in rendered
    assert "BASELINE_DRIFT_REQUIRES_REVIEW" in rendered
    assert "LTW-097" not in rendered.split("## Failed Strict Cases", 1)[0]
    assert "LTW-097" in policy
    assert "observe-only" in policy


def test_longtail_write_v1_baseline_is_json_serializable() -> None:
    report = evaluate_memory_longtail_write_v1(DATASET)
    encoded = json.dumps(report, ensure_ascii=False)
    assert json.loads(encoded)["version"] == REPORT_VERSION


def test_longtail_write_v1_cli_help_and_filtered_report(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.app, ["eval", "memory-longtail-write-v1", "--help"])
    assert result.exit_code == 0, result.output
    assert "--dataset" in result.stdout
    assert "--integration" in result.stdout
    output = tmp_path / "filtered.json"
    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "memory-longtail-write-v1",
            "--dataset",
            str(DATASET),
            "--case",
            "LTW-001",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text(encoding="utf-8"))["case_count"] == 1
    assert output.with_suffix(".md").exists()


def test_longtail_write_v1_isolated_integration_has_balanced_36_case_sample() -> None:
    report = asyncio.run(evaluate_memory_longtail_write_integration(DATASET))

    assert report["case_count"] == 36
    assert len(report["selected_case_ids"]) == 36
    assert len(set(report["selected_case_ids"])) == 36
    cases = {case["case_id"]: case for case in load_memory_longtail_write_v1_cases(DATASET)}
    selected_cases = [cases[case_id] for case_id in report["selected_case_ids"]]
    assert Counter(case["expected"]["relation"] for case in selected_cases) == Counter(
        {
            "same": 4,
            "complementary": 12,
            "update": 6,
            "contradiction": 4,
            "unrelated": 4,
            "uncertain": 6,
        }
    )
    assert sum(case["slice"] == "temporal_event_identity" for case in selected_cases) == 4
    assert sum(case["slice"] == "custom_canonical_coexistence" for case in selected_cases) == 4
    assert report["production_store_mutation_permitted"] is False
    assert report["isolated_in_memory_store_mutation"] is True
    assert report["model_calls_permitted"] is False
    assert report["store_write_attempt_count"] == 36
    assert report["transition_audit_count"] >= 36
    assert all(row["store_mutation_permitted"] is False for row in report["rows"])
    assert all(row["isolated_store_mutation"] is True for row in report["rows"])
    assert all(row["before_rows"] and row["after_rows"] for row in report["rows"])
    assert all(row["transition_audits"] for row in report["rows"])
    assert all("status_updates" in row and "store_outcome" in row for row in report["rows"])

    by_id = {row["case_id"]: row for row in report["rows"]}
    assert by_id["LTW-001"]["actual_target_memory_ids"] == ["S1"]
    assert by_id["LTW-001"]["actual_new_row"] is False
    assert by_id["LTW-084"]["actual_relation"] == "uncertain"
    # The fixture intentionally exposes the current custom-uncertain target
    # over-selection; it must be visible, not silently rewritten to the Gold.
    assert set(by_id["LTW-084"]["actual_target_memory_ids"]) == {"M41", "M42"}

    rendered = render_memory_longtail_write_integration_diagnostic(report)
    assert "Store write attempts" in rendered
    assert "Transition audits" in rendered
    assert "Status changes" in rendered

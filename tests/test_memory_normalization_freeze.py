import asyncio
import json
from pathlib import Path

from loveapp.evaluation.memory_normalization_freeze import (
    evaluate_memory_normalization_production_smoke,
    load_production_smoke_cases,
    render_production_smoke_report,
)

DATASET = Path("evals/memory/normalization_production_smoke_v1.jsonl")


def test_production_smoke_dataset_contains_required_pressure_cases() -> None:
    cases = load_production_smoke_cases(DATASET)
    assert {case.source_case_id for case in cases} >= {
        "SUBJ-003",
        "SUBJ-013",
        "SUBJ-021",
        "SUBJ-022",
    }


def test_production_smoke_uses_real_parser_normalizer_and_admission_boundary() -> None:
    report = asyncio.run(evaluate_memory_normalization_production_smoke(DATASET))

    assert report["model_calls_permitted"] is False
    assert report["store_mutation_permitted"] is False
    assert report["isolated_store_mutation_permitted"] is True
    assert report["passed_case_count"] == report["case_count"]
    assert report["metrics"]["generic_validation_acceptance_rate"] == 1.0
    assert report["metrics"]["normalization_success_rate"] == 1.0
    assert report["metrics"]["admission_reached_rate"] == 1.0
    assert report["metrics"]["store_write_attempt_rate"] == 1.0

    rows = {row["source_case_id"]: row for row in report["cases"]}
    for source_case_id in ("SUBJ-003", "SUBJ-013", "SUBJ-021", "SUBJ-022"):
        row = rows[source_case_id]
        assert row["raw_claim_present"] is True
        assert row["generic_validation_result"]["status"] == "accept"
        assert row["admission_reached"] is True
        assert row["store_write_attempted"] is True
        assert row["drop_stage"] is None

    assert rows["SUBJ-003"]["normalizer_output"][0]["predicate_type"] == "custom"
    assert rows["SUBJ-013"]["normalizer_output"][0]["predicate_type"] == "custom"
    assert len(rows["SUBJ-021"]["normalizer_output"]) == 2
    assert rows["SUBJ-021"]["normalizer_output"][0]["custom_predicate"] == "boundary_agreed"
    assert rows["SUBJ-022"]["normalizer_output"][0]["custom_predicate"] == "contact.status"


def test_production_smoke_report_renders_stage_and_retention_details() -> None:
    report = asyncio.run(
        evaluate_memory_normalization_production_smoke(DATASET, case_id="SMOKE-SUBJ-003")
    )
    markdown = render_production_smoke_report(report)
    assert "# Memory Normalization Production-Path Smoke" in markdown
    assert "Raw" in markdown
    assert "Admission" in markdown
    assert "SUBJ-003" in markdown
    assert "Final retention" in markdown


def test_production_smoke_artifact_is_json_serializable() -> None:
    report = asyncio.run(
        evaluate_memory_normalization_production_smoke(DATASET, case_id="SMOKE-SUBJ-022")
    )
    json.dumps(report, ensure_ascii=False)

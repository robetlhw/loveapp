import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from loveapp.cli import app
from loveapp.evaluation.memory_foundation import (
    TextKeyedScriptedExtractor,
    evaluate_memory_foundation,
)

DATASET = Path("evals/memory/cases_v1.jsonl")


def _cases() -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_foundation_fixture_has_stable_ids_and_invariants() -> None:
    cases = _cases()

    assert [case["id"] for case in cases] == [f"MEM-{number:03d}" for number in range(1, 19)]
    assert all(case["turns"] for case in cases)
    assert all("expected_final" in case for case in cases)
    assert all("gate_should_extract" in turn["expect"] for case in cases for turn in case["turns"])


async def test_text_keyed_extractor_is_not_shifted_by_a_skipped_turn() -> None:
    turns = _cases()[15]["turns"] + _cases()[0]["turns"][:1]
    extractor = TextKeyedScriptedExtractor(turns)

    extracted = await extractor.extract(turns[1]["input"])

    assert [claim.claim_id for claim in extracted.claims] == ["mem001-active"]
    assert extractor.call_count == 1


async def test_foundation_evaluator_runs_all_cases_through_memory_service() -> None:
    report = await evaluate_memory_foundation(DATASET)

    assert report["case_count"] == 18
    assert report["passed_case_count"] == 18
    assert report["failed_case_count"] == 0
    assert report["total_turns"] == 34
    assert report["metrics"]["gate_expected_positive_accuracy"] == 1.0
    assert report["metrics"]["gate_expected_negative_accuracy"] == 1.0
    assert report["metrics"]["canonical_match_rate"] == 1.0
    assert report["metrics"]["relation_expected_turns"] == 8
    assert report["metrics"]["relation_accuracy"] == 1.0
    assert report["metrics"]["lifecycle_success_rate"] == 1.0
    assert report["metrics"]["stale_active_memory_count"] == 0
    assert report["metrics"]["duplicate_active_memory_count"] == 0
    assert report["metrics"]["confirmed_overwrite_violation_count"] == 0
    assert report["metrics"]["long_tail_gate_recall"] == 1.0
    assert report["metrics"]["custom_uncertain_count"] >= 0
    assert report["metrics"]["strong_model_failure_count"] == 0
    assert all(row["turns"] for row in report["cases"])
    assert all(
        turn["extraction_status"] in {"completed", "skipped"}
        for row in report["cases"]
        for turn in row["turns"]
    )
    history_case = next(row for row in report["cases"] if row["id"] == "MEM-018")
    assert "historical_conflict_active" in history_case["final_history_context_refs"]
    stage_case = next(row for row in report["cases"] if row["id"] == "MEM-004")
    assert stage_case["final_relationship_stage"] == "dating"


async def test_foundation_evaluator_requires_explicit_verifier_fixtures(
    tmp_path: Path,
) -> None:
    case = _cases()[0]
    dataset = tmp_path / "case.jsonl"
    dataset.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    dataset.with_name("case_verifications.json").write_text("{}", encoding="utf-8")

    report = await evaluate_memory_foundation(dataset)

    assert report["passed_case_count"] == 0
    assert report["failed_case_count"] == 1
    assert report["cases"][0]["failures"]


async def test_foundation_evaluator_filters_one_isolated_case() -> None:
    report = await evaluate_memory_foundation(DATASET, case_id="MEM-016")

    assert report["case_count"] == 1
    assert report["passed_case_count"] == 1
    assert report["cases"][0]["id"] == "MEM-016"
    assert report["cases"][0]["final_memories"] == []

    with pytest.raises(ValueError, match="unknown memory foundation case"):
        await evaluate_memory_foundation(DATASET, case_id="MEM-999")


def test_memory_foundation_cli_supports_case_filter(tmp_path: Path) -> None:
    output = tmp_path / "foundation.json"

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "memory-foundation",
            "--dataset",
            str(DATASET),
            "--output",
            str(output),
            "--case",
            "MEM-016",
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["case_filter"] == "MEM-016"
    assert report["passed_case_count"] == 1

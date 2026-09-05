import json
from collections import Counter
from pathlib import Path

import pytest

from loveapp.domain.memory import MemoryKind, PredicateType
from loveapp.evaluation.memory_normalization_v1 import (
    CONTRACT_VERIFY_CASE_IDS,
    EXPECTED_SLICE_COUNTS,
    REQUIRED_ERROR_TAXONOMY,
    evaluate_memory_normalization_v1,
    load_memory_normalization_v1_cases,
    render_memory_normalization_v1_report,
)

DATASET = Path("evals/memory/normalization_v1.jsonl")


def test_normalization_v1_dataset_matches_external_contract() -> None:
    cases = load_memory_normalization_v1_cases(DATASET)

    assert len(cases) == 56
    assert {case.case_id for case in cases} == {
        f"NORM-{index:03d}" for index in range(1, 57)
    }
    assert Counter(case.slice for case in cases) == EXPECTED_SLICE_COUNTS
    assert {
        case.case_id for case in cases if case.contract_status == "CONTRACT_VERIFY"
    } == CONTRACT_VERIFY_CASE_IDS
    assert all(case.contract_resolution is not None for case in cases)


def test_normalization_v1_baseline_preserves_gold_and_scores_resolved_contracts() -> None:
    report = evaluate_memory_normalization_v1(DATASET)
    by_id = {row["case_id"]: row for row in report["cases"]}

    assert report["case_count"] == 56
    assert report["model_calls_permitted"] is False
    assert report["store_mutation_permitted"] is False
    assert report["metrics"]["custom_preservation_accuracy"] == 1.0
    assert report["metrics"]["unsafe_canonicalization_rate"] == 0.0
    assert report["metrics"]["idempotency_accuracy"] == 1.0

    assert by_id["NORM-006"]["expected"]["canonical_predicate"] == (
        "preference.budget.range"
    )
    assert by_id["NORM-029"]["expected"]["canonical_predicate"] == (
        "preference.food.cuisine"
    )
    assert by_id["NORM-052"]["expected"]["contract_outcome"] == "reject"
    assert by_id["NORM-052"]["scoring_scope"]["contract_outcome"] is True
    assert by_id["NORM-053"]["scoring_scope"]["canonical_mapping"] is True
    assert by_id["NORM-053"]["scoring_scope"]["contract_outcome"] is True
    assert report["metrics"]["details"]["canonical_mapping_accuracy"][
        "denominator"
    ] == 13
    assert set(REQUIRED_ERROR_TAXONOMY) <= set(report["error_taxonomy"])


def test_normalization_v1_exact_mismatch_is_attributed_to_spec_conflict() -> None:
    report = evaluate_memory_normalization_v1(
        DATASET,
        case_id="NORM-001",
        require_complete=False,
        normalizer=lambda candidate, _reference_time: candidate,
    )

    row = report["cases"][0]
    assert "MISSED_CANONICAL_MAPPING" in row["errors"]
    assert "IMPLEMENTATION_SPEC_CONFLICT" in row["errors"]


def test_normalization_v1_scores_norm_053_wrong_canonical_target() -> None:
    def wrong_target(candidate, _reference_time):
        return candidate.model_copy(
            update={
                "predicate_type": PredicateType.CANONICAL,
                "canonical_predicate": "interaction.response_engagement",
                "custom_predicate": None,
            }
        )

    report = evaluate_memory_normalization_v1(
        DATASET,
        case_id="NORM-053",
        require_complete=False,
        normalizer=wrong_target,
    )

    row = report["cases"][0]
    assert row["checks"]["canonical_mapping"] is False
    assert "WRONG_CANONICAL_MAPPING" in row["errors"]


def test_normalization_v1_counts_state_coercion_as_unsafe() -> None:
    def unsafe_state(candidate, _reference_time):
        return candidate.model_copy(
            update={
                "kind": MemoryKind.RELATIONSHIP_STATE,
                "predicate_type": PredicateType.CUSTOM,
                "canonical_predicate": None,
                "custom_predicate": "ambiguous_relationship_warmth",
                "state_dimension": "conflict_status",
                "state_value": "active",
                "payload": {
                    "state_dimension": "conflict_status",
                    "state_value": "active",
                },
            }
        )

    report = evaluate_memory_normalization_v1(
        DATASET,
        case_id="NORM-039",
        require_complete=False,
        normalizer=unsafe_state,
    )

    row = report["cases"][0]
    assert row["actual_normalization_mode"] == "state"
    assert row["checks"]["unsafe_canonicalization"] is True
    assert report["metrics"]["unsafe_canonicalization_rate"] == 1.0


def test_normalization_v1_records_aligned_state_representations_and_is_idempotent() -> None:
    report = evaluate_memory_normalization_v1(DATASET)
    by_id = {row["case_id"]: row for row in report["cases"]}

    state = by_id["NORM-049"]
    assert state["top_level_state"] == state["payload_state"]
    assert state["idempotent"] is True
    assert state["idempotency_diff_paths"] == []

    custom = by_id["NORM-051"]
    assert custom["normalizer_mode"] == "preserve"
    assert custom["idempotent"] is True


def test_normalization_v1_filters_do_not_require_full_dataset_result() -> None:
    report = evaluate_memory_normalization_v1(
        DATASET,
        case_id="NORM-029",
        require_complete=False,
    )

    assert report["case_count"] == 1
    assert report["cases"][0]["case_id"] == "NORM-029"


def test_normalization_v1_loader_rejects_unknown_input_fields(tmp_path: Path) -> None:
    source = DATASET.read_text(encoding="utf-8").splitlines()[0]
    malformed = source.replace('"input_claim":{', '"input_claim":{"typo":true,', 1)
    path = tmp_path / "malformed.jsonl"
    path.write_text(malformed + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported fields"):
        load_memory_normalization_v1_cases(path, require_complete=False)


def test_normalization_v1_loader_requires_resolution_in_fixed_gold(
    tmp_path: Path,
) -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8").splitlines()[0])
    payload.pop("contract_resolution")
    path = tmp_path / "missing-resolution.jsonl"
    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="contract_resolution"):
        load_memory_normalization_v1_cases(path, require_complete=False)


def test_normalization_v1_report_renders_metrics_and_next_phase() -> None:
    report = evaluate_memory_normalization_v1(
        DATASET,
        slice_name="ambiguous",
        require_complete=False,
    )
    markdown = render_memory_normalization_v1_report(report)

    assert "# Memory Normalization V1 Evaluation Report" in markdown
    assert "unsafe_canonicalization_rate" in markdown
    assert "Normalization V1 Failure Review + Minimal Remediation" in markdown
    assert "Current Canonical Registry (23)" in markdown
    assert "Invalid state dimension/value inputs accepted" in markdown
    for answer in range(1, 18):
        assert f"{answer}." in markdown

from pathlib import Path

from loveapp.evaluation.memory_normalization_boundary import (
    evaluate_memory_normalization_boundary,
    load_memory_normalization_boundary_cases,
    render_memory_normalization_boundary_report,
)

DATASET = Path("evals/memory/normalization_boundary_v1.jsonl")


def test_boundary_dataset_has_twenty_cases_and_required_acceptance_examples() -> None:
    cases = load_memory_normalization_boundary_cases(DATASET)

    assert len(cases) == 20
    assert {case.case_id for case in cases} == {
        f"BND-{index:03d}" for index in range(1, 21)
    }
    by_id = {case.case_id: case for case in cases}
    assert by_id["BND-001"].expected.generic_validation == "accept"
    assert by_id["BND-001"].expected.normalization == "canonical"
    assert by_id["BND-003"].expected.state_dimension == "conflict_status"
    assert by_id["BND-005"].expected.normalization == "custom"
    assert by_id["BND-014"].expected.generic_validation == "reject"
    assert by_id["BND-017"].expected.contract_outcome == "reject"


def test_boundary_evaluator_exposes_each_validation_layer() -> None:
    report = evaluate_memory_normalization_boundary(DATASET)
    row = {item["case_id"]: item for item in report["cases"]}["BND-001"]

    assert report["model_calls_permitted"] is False
    assert report["store_mutation_permitted"] is False
    assert row["generic_validation"]["status"] == "accept"
    assert row["normalizer_input"] is not None
    assert row["normalization"]["status"] in {"accept", "reject", "error"}
    assert row["canonical_validation"]["status"] in {"accept", "reject", "not_run"}
    assert "production_claim_retained" in row
    assert "where_dropped" in row
    assert "why_dropped" in row


def test_boundary_metrics_are_separate_from_v1_normalization_metrics() -> None:
    report = evaluate_memory_normalization_boundary(DATASET)
    metrics = report["metrics"]

    assert "generic_validation_acceptance_rate" in metrics
    assert "false_pre_normalization_rejection_rate" in metrics
    assert "normalizer_recovery_accuracy" in metrics
    assert "validation_boundary_rejection_count" in metrics
    assert metrics["details"]["generic_validation_acceptance_rate"]["denominator"] == 16
    assert metrics["details"]["false_pre_normalization_rejection_rate"]["denominator"] == 16


def test_required_boundary_cases_express_the_target_contract() -> None:
    report = evaluate_memory_normalization_boundary(DATASET)
    rows = {row["case_id"]: row for row in report["cases"]}

    metric = rows["BND-001"]
    assert metric["generic_validation"]["status"] == "accept"
    assert metric["final_claim"]["payload"]["metric"] == "initiation_balance"
    assert metric["final_claim"]["canonical_predicate"] == (
        "interaction.initiation_balance"
    )
    assert metric["canonical_validation"]["status"] == "accept"

    state = rows["BND-003"]
    assert state["generic_validation"]["status"] == "accept"
    assert state["final_claim"]["state_dimension"] == "conflict_status"
    assert state["final_claim"]["state_value"] == "active"

    open_state = rows["BND-005"]
    assert open_state["generic_validation"]["status"] == "accept"
    if open_state["final_claim"] is not None:
        assert open_state["final_claim"]["predicate_type"] == "custom"

    invalid_subject = rows["BND-014"]
    assert invalid_subject["expected"]["generic_validation"] == "reject"

    conflict = rows["BND-017"]
    assert conflict["generic_validation"]["status"] == "accept"
    assert conflict["normalization"]["status"] == "reject"
    assert "CANONICAL_CUSTOM_CONFLICT" in conflict["normalization"]["diagnostics"]


def test_boundary_report_renders_stage_and_taxonomy_sections() -> None:
    report = evaluate_memory_normalization_boundary(DATASET)
    markdown = render_memory_normalization_boundary_report(report)

    assert "# Memory Validation Boundary Migration Report" in markdown
    assert "generic_validation_acceptance_rate" in markdown
    assert "Canonical" in markdown
    assert "Error Taxonomy" in markdown

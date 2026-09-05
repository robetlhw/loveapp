import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from loveapp.application.memory_repair import (
    parse_memory_response,
    validate_memory_claim_generic,
    validate_normalized_memory_claim,
)
from loveapp.domain.memory import AtomicClaim, PredicateType
from loveapp.domain.memory_normalization import normalize_memory_candidate_contract
from loveapp.evaluation.memory_normalization_v1 import (
    load_memory_normalization_v1_cases,
)
from loveapp.evaluation.memory_normalization_v1_1 import (
    evaluate_memory_normalization_v1_1,
    load_memory_normalization_v1_1_cases,
    render_memory_normalization_v1_1_report,
)

V1_DATASET = Path("evals/memory/normalization_v1.jsonl")
V1_1_DATASET = Path("evals/memory/normalization_v1_1.jsonl")


def test_v1_1_migration_preserves_semantic_gold_and_splits_n1_n2() -> None:
    old = {case.case_id: case for case in load_memory_normalization_v1_cases(V1_DATASET)}
    new = {
        case.case_id: case for case in load_memory_normalization_v1_1_cases(V1_1_DATASET)
    }

    assert set(new) == set(old)
    assert sum(case.evaluation_layer == "N1" for case in new.values()) == 46
    assert sum(case.evaluation_layer == "N2" for case in new.values()) == 10
    for case_id, old_case in old.items():
        assert new[case_id].expected == old_case.expected
        assert new[case_id].scoring_scope == old_case.scoring_scope


def test_v1_1_meets_contract_and_safety_targets() -> None:
    report = evaluate_memory_normalization_v1_1(V1_1_DATASET, fail_on_error=True)
    metrics = report["metrics"]

    assert report["passed_case_count"] == 56
    assert metrics["semantic_hint_resolution_accuracy"] == 1.0
    assert metrics["canonical_mapping_accuracy"] == 1.0
    assert metrics["state_dimension_accuracy"] == 1.0
    assert metrics["state_value_accuracy"] == 1.0
    assert metrics["custom_preservation_accuracy"] == 1.0
    assert metrics["unsafe_canonicalization_rate"] == 0.0
    assert metrics["schema_validity"] == 1.0
    assert metrics["idempotency_accuracy"] == 1.0
    assert metrics["conflict_outcome_accuracy"] == 1.0
    assert report["normalization_status"] == "FREEZE_CANDIDATE"


def test_typed_preference_hints_fix_budget_and_cuisine_without_alias_sprawl() -> None:
    report = evaluate_memory_normalization_v1_1(V1_1_DATASET)
    rows = {row["case_id"]: row for row in report["cases"]}

    assert rows["NORM-006"]["predicate_representation"]["canonical_predicate"] == (
        "preference.budget.range"
    )
    assert rows["NORM-006"]["normalizer_output"]["payload"]["budget_max_cny"] == 300
    assert rows["NORM-029"]["predicate_representation"]["canonical_predicate"] == (
        "preference.food.cuisine"
    )


def test_conflicting_declaration_rejects_but_equivalent_duplicate_reconciles() -> None:
    report = evaluate_memory_normalization_v1_1(V1_1_DATASET)
    rows = {row["case_id"]: row for row in report["cases"]}

    assert rows["NORM-052"]["contract_outcome"] == "reject"
    assert rows["NORM-052"]["contract_rejection"]["code"] == (
        "CANONICAL_CUSTOM_CONFLICT"
    )
    assert rows["NORM-053"]["contract_outcome"] == "accept"
    assert rows["NORM-053"]["predicate_representation"] == {
        "predicate_type": "canonical",
        "raw_predicate": "initiation_balance",
        "canonical_predicate": "interaction.initiation_balance",
        "custom_predicate": None,
    }


def test_generic_and_normalized_validation_are_distinct_boundaries() -> None:
    claim = AtomicClaim(
        claim_id="raw-interaction",
        kind="interaction_pattern",
        subject="relationship",
        predicate="model_surface_form",
        summary="双方互动模式发生了变化",
        evidence_spans=["双方互动模式发生了变化"],
        payload={},
    )

    validate_memory_claim_generic(claim, claim.evidence_spans[0], set())
    with pytest.raises(ValueError, match=r"payload\.metric"):
        validate_normalized_memory_claim(claim)


def test_parse_promotes_non_authoritative_hints_before_canonical_validation() -> None:
    source = "双方当前仍在冷战"
    response = {
        "claims": [
            {
                "claim_id": "hinted-state",
                "kind": "relationship_state",
                "subject": "relationship",
                "predicate": "model_surface_state",
                "summary": "双方当前仍处于冲突状态",
                "evidence_spans": [source],
                "payload": {
                    "state_dimension_hint": "relationship_conflict_status",
                    "state_value_hint": "unresolved",
                },
            }
        ],
        "discarded_spans": [],
    }

    parsed = parse_memory_response(
        json.dumps(response, ensure_ascii=False),
        source_text=source,
    )
    claim = parsed.extraction.claims[0]
    normalized = normalize_memory_candidate_contract(
        claim.to_candidate(),
        datetime(2026, 9, 2, 10, tzinfo=UTC),
    )

    assert normalized.predicate_type == PredicateType.CANONICAL
    assert normalized.canonical_predicate == "relationship.conflict_status"
    assert claim.payload["state_dimension"] == "conflict_status"
    assert claim.payload["state_value"] == "active"
    assert normalized.state_dimension == "conflict_status"
    assert normalized.state_value == "active"
    assert "state_dimension_hint" in parsed.repair_steps
    assert "state_value_hint" in parsed.repair_steps


def test_v1_1_report_records_contract_decisions() -> None:
    report = evaluate_memory_normalization_v1_1(V1_1_DATASET, layer="N2")
    markdown = render_memory_normalization_v1_1_report(report)

    assert "Option C" in markdown
    assert "NORM-052" in markdown
    assert "Normalization V1.1" in markdown


def test_v1_1_report_renders_conflict_outcome_detail_from_contract_metric() -> None:
    report = evaluate_memory_normalization_v1_1(V1_1_DATASET)
    markdown = render_memory_normalization_v1_1_report(report)

    assert "| conflict_outcome_accuracy | 1.0000 | 5 | 5 | True |" in markdown

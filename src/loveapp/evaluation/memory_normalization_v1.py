from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from loveapp.application.memory_repair import (
    MemoryResponseError,
    parse_memory_response,
    validate_memory_claim_generic,
)
from loveapp.domain.memory import AtomicClaim, MemoryCandidate, MemoryKind, PredicateType
from loveapp.domain.memory_dimensions import (
    RELATIONSHIP_STATE_POLICIES,
    normalize_state_dimension,
    normalize_state_value,
)
from loveapp.domain.memory_lifecycle import normalize_memory_candidate
from loveapp.domain.memory_normalization import NormalizationContractError
from loveapp.domain.memory_predicates import CANONICAL_PREDICATES

NormalizationMode = Literal["canonical", "state", "custom", "reject", "preserve"]
ContractOutcome = Literal["accept", "reject", "defer"]
ContractStatus = Literal["EXACT", "CONTRACT_VERIFY"]
ContractResolution = Literal[
    "exact",
    "repo_identifier",
    "custom_fallback",
    "reject",
    "implementation_spec_conflict",
    "ambiguous_gold_policy",
]
NormalizationSlice = Literal[
    "canonical_direct",
    "state_mapping",
    "alias_variation",
    "custom_preservation",
    "ambiguous",
    "idempotency",
    "conflict_shape",
]

EXPECTED_SLICE_COUNTS: dict[str, int] = {
    "canonical_direct": 10,
    "state_mapping": 12,
    "alias_variation": 8,
    "custom_preservation": 8,
    "ambiguous": 8,
    "idempotency": 5,
    "conflict_shape": 5,
}
CONTRACT_VERIFY_CASE_IDS = {
    "NORM-005",
    "NORM-006",
    "NORM-007",
    "NORM-008",
    "NORM-009",
    "NORM-010",
    "NORM-029",
    "NORM-052",
    "NORM-053",
}
CONTRACT_RESOLUTIONS: dict[str, ContractResolution] = {
    "NORM-005": "custom_fallback",
    "NORM-006": "repo_identifier",
    "NORM-007": "custom_fallback",
    "NORM-008": "custom_fallback",
    "NORM-009": "custom_fallback",
    "NORM-010": "custom_fallback",
    "NORM-029": "repo_identifier",
    "NORM-052": "reject",
    "NORM-053": "repo_identifier",
}
EXPECTED_CASE_IDS = {f"NORM-{index:03d}" for index in range(1, 57)}
REQUIRED_ERROR_TAXONOMY = (
    "WRONG_CANONICAL_MAPPING",
    "MISSED_CANONICAL_MAPPING",
    "UNSAFE_CANONICALIZATION",
    "WRONG_STATE_DIMENSION",
    "WRONG_STATE_VALUE",
    "CUSTOM_NOT_PRESERVED",
    "CANONICAL_CUSTOM_CONFLICT",
    "STATE_VALUE_INVALID",
    "UNKNOWN_STATE_DIMENSION",
    "NON_IDEMPOTENT",
    "SCHEMA_INVALID",
    "AMBIGUOUS_GOLD_POLICY",
    "IMPLEMENTATION_SPEC_CONFLICT",
)

Normalizer = Callable[[MemoryCandidate, datetime], MemoryCandidate]


class NormalizationScoringScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_mapping: bool = False
    state_dimension: bool = False
    state_value: bool = False
    custom_preservation: bool = False
    unsafe_canonicalization: bool = False
    schema_validity: bool = True
    idempotency: bool = False
    contract_outcome: bool = False


class NormalizationExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    normalization_mode: NormalizationMode
    canonical_predicate: str | None = Field(default=None, max_length=120)
    custom_predicate: str | None = Field(default=None, max_length=120)
    state_dimension: str | None = Field(default=None, max_length=120)
    state_value: str | None = Field(default=None, max_length=120)
    must_not_force_canonical: bool = False
    payload_equals: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices(
            "payload_equals",
            "expected_payload_constraints",
        ),
    )
    payload_absent: list[str] = Field(default_factory=list)
    contract_outcome: ContractOutcome = "accept"


class NormalizationV1Case(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^NORM-\d{3}$")
    schema_version: str = "normalization-v1"
    evaluation_layer: Literal["N1", "N2"] | None = None
    slice: NormalizationSlice
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    reference_time: datetime = datetime.fromisoformat("2026-09-02T10:00:00+08:00")
    input_claim: dict[str, Any]
    expected: NormalizationExpectation
    scoring_scope: NormalizationScoringScope
    contract_status: ContractStatus
    contract_resolution: ContractResolution
    notes: str = ""

    @model_validator(mode="after")
    def validate_scoring_contract(self) -> NormalizationV1Case:
        scope = self.scoring_scope
        expected = self.expected
        if expected.normalization_mode == "reject" and (
            expected.contract_outcome != "reject" or not scope.contract_outcome
        ):
            raise ValueError(
                "reject Gold must explicitly score contract_outcome=reject"
            )
        if scope.canonical_mapping and expected.canonical_predicate is None:
            raise ValueError("canonical_mapping requires expected canonical_predicate")
        if scope.state_dimension and expected.state_dimension is None:
            raise ValueError("state_dimension scoring requires an expected value")
        if scope.state_value and expected.state_value is None:
            raise ValueError("state_value scoring requires an expected value")
        if scope.custom_preservation and expected.custom_predicate is None:
            raise ValueError("custom_preservation requires expected custom_predicate")
        if scope.unsafe_canonicalization and not expected.must_not_force_canonical:
            raise ValueError(
                "unsafe_canonicalization scoring requires must_not_force_canonical=true"
            )
        if self.contract_status == "EXACT" and self.contract_resolution != "exact":
            raise ValueError("EXACT cases must use contract_resolution=exact")
        if self.contract_status == "CONTRACT_VERIFY":
            expected_resolution = CONTRACT_RESOLUTIONS.get(self.case_id)
            if expected_resolution is None:
                raise ValueError("CONTRACT_VERIFY case has no reviewed resolution")
            if self.contract_resolution != expected_resolution:
                raise ValueError(
                    "CONTRACT_VERIFY resolution differs from the reviewed contract: "
                    f"expected {expected_resolution}, got {self.contract_resolution}"
                )
        if set(expected.payload_equals) & set(expected.payload_absent):
            raise ValueError("payload fields cannot be both required and absent")
        _validate_raw_claim_keys(self.input_claim)
        _hydrate_raw_claim(self)
        return self


def load_memory_normalization_v1_cases(
    path: Path,
    *,
    require_complete: bool = True,
) -> list[NormalizationV1Case]:
    cases: list[NormalizationV1Case] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = NormalizationV1Case.model_validate_json(line)
        except Exception as exc:
            raise ValueError(
                f"invalid Normalization V1 case on line {line_number}: {exc}"
            ) from exc
        if case.case_id in seen:
            raise ValueError(f"duplicate Normalization V1 case_id: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError("Normalization V1 dataset is empty")
    if require_complete:
        _validate_complete_dataset(cases)
    return cases


def evaluate_memory_normalization_v1(
    dataset_path: Path,
    *,
    case_id: str | None = None,
    slice_name: str | None = None,
    fail_on_error: bool = False,
    require_complete: bool = True,
    normalizer: Normalizer = normalize_memory_candidate,
) -> dict[str, Any]:
    raw = dataset_path.read_bytes()
    all_cases = load_memory_normalization_v1_cases(
        dataset_path,
        require_complete=require_complete,
    )
    cases = [
        case
        for case in all_cases
        if (case_id is None or case.case_id == case_id)
        and (slice_name is None or case.slice == slice_name)
    ]
    if not cases:
        raise ValueError(
            "no Normalization V1 cases matched: "
            f"case_id={case_id!r}, slice={slice_name!r}"
        )

    rows: list[dict[str, Any]] = []
    for case in cases:
        try:
            rows.append(_evaluate_case(case, normalizer=normalizer))
        except Exception as exc:
            if fail_on_error:
                raise
            rows.append(_failed_case(case, exc))

    metrics = _aggregate_metrics(rows)
    slices = {
        name: _aggregate_metrics([row for row in rows if row["slice"] == name])
        for name in EXPECTED_SLICE_COUNTS
        if any(row["slice"] == name for row in rows)
    }
    observed_error_taxonomy = Counter(
        error for row in rows for error in row.get("errors", [])
    )
    error_taxonomy = {
        name: observed_error_taxonomy.get(name, 0)
        for name in sorted(set(REQUIRED_ERROR_TAXONOMY) | set(observed_error_taxonomy))
    }
    diagnostic_taxonomy = Counter(
        diagnostic for row in rows for diagnostic in row.get("diagnostics", [])
    )
    targets = {
        "canonical_mapping_accuracy": ">=0.90",
        "state_dimension_accuracy": ">=0.90",
        "state_value_accuracy": ">=0.90",
        "custom_preservation_accuracy": ">=0.95",
        "unsafe_canonicalization_rate": "<=0.05",
        "schema_validity": "1.00",
        "idempotency_accuracy": ">=0.98",
        "canonical_coverage": "observe",
    }
    target_pass = {
        "canonical_mapping_accuracy": _at_least(
            metrics["canonical_mapping_accuracy"], 0.90
        ),
        "state_dimension_accuracy": _at_least(
            metrics["state_dimension_accuracy"], 0.90
        ),
        "state_value_accuracy": _at_least(metrics["state_value_accuracy"], 0.90),
        "custom_preservation_accuracy": _at_least(
            metrics["custom_preservation_accuracy"], 0.95
        ),
        "unsafe_canonicalization_rate": _at_most(
            metrics["unsafe_canonicalization_rate"], 0.05
        ),
        "schema_validity": _at_least(metrics["schema_validity"], 1.0),
        "idempotency_accuracy": _at_least(
            metrics["idempotency_accuracy"], 0.98
        ),
        "canonical_coverage": None,
    }
    return {
        "evaluation": "memory_normalization_v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "dataset": str(dataset_path),
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "source_of_truth": "local_fixed_raw_claim_jsonl",
        "case_filter": case_id,
        "slice_filter": slice_name,
        "case_count": len(rows),
        "passed_case_count": sum(bool(row["passed"]) for row in rows),
        "gate_participates_in_scoring": False,
        "extraction_participates_in_scoring": False,
        "model_calls_permitted": False,
        "store_mutation_permitted": False,
        "normalization_path": (
            "fixed raw dict -> structural hydrate -> AtomicClaim.to_candidate -> "
            "normalize_memory_candidate"
        ),
        "contract_diagnostic_path": (
            "the same hydrated raw claim -> validate_memory_claim and "
            "parse_memory_response; diagnostics never replace main normalization output"
        ),
        "metrics": metrics,
        "targets": targets,
        "target_pass": target_pass,
        "slices": slices,
        "error_taxonomy": error_taxonomy,
        "diagnostic_taxonomy": dict(sorted(diagnostic_taxonomy.items())),
        "cases": rows,
        "top_bottlenecks": _top_bottlenecks(metrics),
        "next_phase": "Normalization V1 Failure Review + Minimal Remediation",
    }


def render_memory_normalization_v1_report(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    targets = report["targets"]
    target_pass = report["target_pass"]
    metric_names = (
        "canonical_mapping_accuracy",
        "state_dimension_accuracy",
        "state_value_accuracy",
        "custom_preservation_accuracy",
        "unsafe_canonicalization_rate",
        "schema_validity",
        "idempotency_accuracy",
        "canonical_coverage",
    )
    lines = [
        "# Memory Normalization V1 Evaluation Report",
        "",
        f"Generated: `{report['generated_at']}`  ",
        f"Dataset: `{report['dataset']}`  ",
        f"Dataset SHA256: `{report['dataset_sha256']}`  ",
        "Extraction participates in scoring: `False`  ",
        "Store mutation permitted: `False`",
        "",
        "## Metrics",
        "",
        "| Metric | Result | Numerator | Denominator | Target | Pass |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for name in metric_names:
        detail = metrics["details"][name]
        value = metrics[name]
        rendered_value = "n/a" if value is None else f"{value:.4f}"
        passed = target_pass[name]
        rendered_pass = "observe" if passed is None else str(bool(passed))
        lines.append(
            f"| {name} | {rendered_value} | {detail['numerator']} | "
            f"{detail['denominator']} | {targets[name]} | {rendered_pass} |"
        )
    lines.extend(
        [
            "",
            "## Slice Metrics",
            "",
            "| Slice | Cases | Canonical | State Dimension | State Value | "
            "Custom | Unsafe Rate | Schema | Idempotency |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for name, values in report["slices"].items():
        lines.append(
            f"| {name} | {values['case_count']} | "
            f"{_format_metric(values['canonical_mapping_accuracy'])} | "
            f"{_format_metric(values['state_dimension_accuracy'])} | "
            f"{_format_metric(values['state_value_accuracy'])} | "
            f"{_format_metric(values['custom_preservation_accuracy'])} | "
            f"{_format_metric(values['unsafe_canonicalization_rate'])} | "
            f"{_format_metric(values['schema_validity'])} | "
            f"{_format_metric(values['idempotency_accuracy'])} |"
        )
    lines.extend(
        [
            "",
            "## Error Taxonomy",
            "",
            "| Error | Cases |",
            "|---|---:|",
            *[
                f"| {name} | {count} |"
                for name, count in report["error_taxonomy"].items()
            ],
            "",
            "## Contract Diagnostics",
            "",
            "| Diagnostic | Cases |",
            "|---|---:|",
            *[
                f"| {name} | {count} |"
                for name, count in report["diagnostic_taxonomy"].items()
            ],
            "",
            "## Failed Cases",
            "",
            "| Case | Slice | Actual Mode | Errors |",
            "|---|---|---|---|",
        ]
    )
    failures = [row for row in report["cases"] if not row["passed"]]
    if failures:
        for row in failures:
            lines.append(
                f"| {row['case_id']} | {row['slice']} | "
                f"{row.get('actual_normalization_mode')} | "
                f"{', '.join(row['errors'])} |"
            )
    else:
        lines.append("| none | - | - | - |")
    lines.extend(["", "## Top Bottlenecks", ""])
    lines.extend(
        f"{index}. {value}"
        for index, value in enumerate(report["top_bottlenecks"], 1)
    )
    by_error = {
        name: [row["case_id"] for row in report["cases"] if name in row["errors"]]
        for name in report["error_taxonomy"]
    }
    alias_misses = [
        row["case_id"]
        for row in report["cases"]
        if row["slice"] == "alias_variation"
        and "MISSED_CANONICAL_MAPPING" in row["errors"]
    ]
    unsafe_ambiguous = [
        row["case_id"]
        for row in report["cases"]
        if row["slice"] == "ambiguous"
        and "UNSAFE_CANONICALIZATION" in row["errors"]
    ]
    accepted_representation_conflicts = [
        row["case_id"]
        for row in report["cases"]
        if "CANONICAL_CUSTOM_CONFLICT" in row.get("diagnostics", [])
        and row["contract_outcome"] == "accept"
    ]
    incorrectly_accepted_representation_conflicts = [
        row["case_id"]
        for row in report["cases"]
        if row["case_id"] in accepted_representation_conflicts
        and row["expected"]["contract_outcome"] == "reject"
    ]
    safely_reconciled_representation_conflicts = [
        row["case_id"]
        for row in report["cases"]
        if row["case_id"] in accepted_representation_conflicts
        and row["expected"]["contract_outcome"] == "accept"
    ]
    accepted_invalid_states = [
        row["case_id"]
        for row in report["cases"]
        if row["slice"] == "conflict_shape"
        and {
            "STATE_VALUE_INVALID",
            "UNKNOWN_STATE_DIMENSION",
        }.intersection(row.get("diagnostics", []))
        and row["contract_outcome"] == "accept"
    ]
    dual_representation_outputs = [
        row["case_id"]
        for row in report["cases"]
        if row.get("predicate_representation")
        and row["predicate_representation"]["canonical_predicate"] is not None
        and row["predicate_representation"]["custom_predicate"] is not None
    ]
    canonical_failures = Counter(
        row["expected"]["canonical_predicate"]
        for row in report["cases"]
        if row["checks"]["canonical_mapping"] is False
    )
    canonical_failure_summary = ", ".join(
        f"{name} ({count})"
        for name, count in canonical_failures.most_common()
        if name is not None
    )
    canonical_registry = ", ".join(f"`{name}`" for name in CANONICAL_PREDICATES)
    state_registry = "; ".join(
        f"`{policy.dimension}`=[{', '.join(sorted(policy.allowed_values))}]"
        for policy in RELATIONSHIP_STATE_POLICIES
    )
    contract_resolutions = "; ".join(
        f"{row['case_id']}={row['contract_resolution']}"
        for row in report["cases"]
        if row["contract_status"] == "CONTRACT_VERIFY"
    )
    lines.extend(
        [
            "",
            "## Required Answers",
            "",
            "1. Current Canonical Registry (23): " + canonical_registry + ".",
            "2. Current lifecycle state dimensions and values: " + state_registry + ".",
            "3. `CONTRACT_VERIFY` resolutions: "
            + contract_resolutions
            + ". Full reasons are fixed in `MEMORY_NORMALIZATION_CONTRACT_RESOLUTION.md`.",
            "4. Canonical Mapping Accuracy: `"
            f"{_format_metric(metrics['canonical_mapping_accuracy'])}`.",
            "5. Most error-prone canonical targets: "
            + (canonical_failure_summary or "none")
            + ". Failed case IDs: `"
            + ", ".join(
                by_error.get("MISSED_CANONICAL_MAPPING", [])
                + by_error.get("WRONG_CANONICAL_MAPPING", [])
            )
            + "`.",
            "6. State Dimension Accuracy: "
            f"`{_format_metric(metrics['state_dimension_accuracy'])}`.",
            "7. State Value Accuracy: "
            f"`{_format_metric(metrics['state_value_accuracy'])}`.",
            "8. Custom Preservation Accuracy: "
            f"`{_format_metric(metrics['custom_preservation_accuracy'])}`.",
            "9. Unsafe Canonicalization Rate: "
            f"`{_format_metric(metrics['unsafe_canonicalization_rate'])}`; ambiguous cases forced "
            f"canonical: `{unsafe_ambiguous}`.",
            f"10. Ambiguous cases incorrectly forced canonical: `{unsafe_ambiguous}`.",
            f"11. Alias Variation misses: `{alias_misses}`.",
            "12. Idempotency Accuracy: "
            f"`{_format_metric(metrics['idempotency_accuracy'])}`.",
            "13. Normalized outputs retaining canonical and custom simultaneously: "
            f"`{dual_representation_outputs}`. Incorrectly accepted raw conflicts: "
            f"`{incorrectly_accepted_representation_conflicts}`; safely reconciled equivalent "
            f"declarations: `{safely_reconciled_representation_conflicts}`.",
            "14. Invalid state dimension/value inputs accepted: "
            f"`{accepted_invalid_states}`; NORM-054 through NORM-056 were rejected by ingress.",
            "15. `IMPLEMENTATION_SPEC_CONFLICT` count: `"
            f"{report['error_taxonomy'].get('IMPLEMENTATION_SPEC_CONFLICT', 0)}`.",
            "16. Top three bottlenecks: "
            + "; ".join(report["top_bottlenecks"][:3])
            + ".",
            "17. Next remediation should be limited to two points: bounded canonical/alias "
            "coverage, and one authoritative lifecycle-state representation plus ingress conflict "
            "handling. No ontology expansion is recommended from this baseline alone.",
        ]
    )
    lines.extend(
        [
            "",
            f"NEXT_PHASE = `{report['next_phase']}`",
            "",
            "This is an observational baseline. It does not modify the production normalizer.",
        ]
    )
    return "\n".join(lines) + "\n"


def _evaluate_case(
    case: NormalizationV1Case,
    *,
    normalizer: Normalizer,
    enforce_contract: bool = False,
) -> dict[str, Any]:
    raw_before = deepcopy(case.input_claim)
    hydrated_payload, claim = _hydrate_raw_claim(case)
    source_text = " ".join(claim.evidence_spans)
    diagnostics = _raw_shape_diagnostics(hydrated_payload)
    validation = _validation_diagnostic(claim, source_text)
    diagnostics.extend(validation["diagnostics"])
    repair = _repair_diagnostic(hydrated_payload, source_text)
    diagnostics.extend(repair["diagnostics"])

    raw_candidate = claim.to_candidate().model_copy(deep=True)
    try:
        first = normalizer(raw_candidate, case.reference_time)
    except NormalizationContractError as exc:
        if not enforce_contract:
            raise
        return _contract_rejected_case(
            case,
            raw_before=raw_before,
            hydrated_payload=hydrated_payload,
            candidate=raw_candidate,
            validation=validation,
            repair=repair,
            diagnostics=diagnostics,
            error=exc,
        )
    if not isinstance(first, MemoryCandidate):
        raise TypeError("normalizer must return MemoryCandidate")
    second = normalizer(first.model_copy(deep=True), case.reference_time)
    if not isinstance(second, MemoryCandidate):
        raise TypeError("second normalizer pass must return MemoryCandidate")

    first_dump = first.model_dump(mode="json")
    second_dump = second.model_dump(mode="json")
    idempotent = first_dump == second_dump
    output_schema = _output_schema_diagnostic(first)
    diagnostics.extend(output_schema["diagnostics"])
    normalizer_mode = _normalizer_mode(case.input_claim, first)
    repaired_claims = repair.get("post_repair_claims") or []
    contract_outcome = "accept" if repaired_claims else "reject"
    actual_mode = (
        "reject"
        if case.scoring_scope.contract_outcome and contract_outcome == "reject"
        else normalizer_mode
    )
    checks = _case_checks(
        case,
        candidate=first,
        actual_mode=actual_mode,
        contract_outcome=contract_outcome,
        output_schema_valid=output_schema["valid"],
        idempotent=idempotent,
    )
    errors = _case_errors(
        case,
        candidate=first,
        actual_mode=actual_mode,
        contract_outcome=contract_outcome,
        output_schema=output_schema,
        idempotent=idempotent,
        checks=checks,
    )
    if (
        case.expected.contract_outcome == "reject"
        and contract_outcome == "accept"
    ):
        for diagnostic in (
            "CANONICAL_CUSTOM_CONFLICT",
            "STATE_VALUE_INVALID",
            "UNKNOWN_STATE_DIMENSION",
        ):
            if diagnostic in diagnostics:
                errors.append(diagnostic)
    implementation_spec_conflict = bool(
        case.contract_resolution == "implementation_spec_conflict"
        or (case.contract_status == "EXACT" and errors)
        or (
            case.scoring_scope.contract_outcome
            and checks["contract_outcome"] is False
        )
    )
    if implementation_spec_conflict:
        errors.append("IMPLEMENTATION_SPEC_CONFLICT")
    elif case.contract_resolution == "ambiguous_gold_policy":
        errors.append("AMBIGUOUS_GOLD_POLICY")
    errors = list(dict.fromkeys(errors))
    if raw_before != case.input_claim:
        errors.append("INPUT_MUTATED")

    return {
        "case_id": case.case_id,
        "slice": case.slice,
        "difficulty": case.difficulty,
        "contract_status": case.contract_status,
        "contract_resolution": case.contract_resolution,
        "expected": case.expected.model_dump(mode="json"),
        "scoring_scope": case.scoring_scope.model_dump(mode="json"),
        "input_claim": case.input_claim,
        "hydrated_claim": hydrated_payload,
        "validation_diagnostic": validation,
        "repair_diagnostic": repair,
        "normalizer_output": first_dump,
        "second_pass_output": second_dump,
        "top_level_state": {
            "state_dimension": first.state_dimension,
            "state_value": first.state_value,
        },
        "payload_state": {
            "state_dimension": first.payload.get("state_dimension"),
            "state_value": first.payload.get("state_value"),
        },
        "predicate_representation": {
            "predicate_type": first.predicate_type.value,
            "raw_predicate": first.raw_predicate,
            "canonical_predicate": first.canonical_predicate,
            "custom_predicate": first.custom_predicate,
        },
        "normalizer_mode": normalizer_mode,
        "contract_outcome": contract_outcome,
        "actual_normalization_mode": actual_mode,
        "idempotent": idempotent,
        "idempotency_diff_paths": (
            [] if idempotent else _diff_paths(first_dump, second_dump)
        ),
        "output_schema": output_schema,
        "checks": checks,
        "diagnostics": sorted(set(diagnostics)),
        "errors": errors,
        "primary_error": errors[0] if errors else None,
        "passed": not errors,
    }


def _contract_rejected_case(
    case: NormalizationV1Case,
    *,
    raw_before: dict[str, Any],
    hydrated_payload: dict[str, Any],
    candidate: MemoryCandidate,
    validation: dict[str, Any],
    repair: dict[str, Any],
    diagnostics: list[str],
    error: NormalizationContractError,
) -> dict[str, Any]:
    contract_outcome = "reject"
    output_schema = {
        "valid": False,
        "errors": [str(error)],
        "diagnostics": [error.code],
    }
    diagnostics = [*diagnostics, error.code]
    checks = _case_checks(
        case,
        candidate=candidate,
        actual_mode="reject",
        contract_outcome=contract_outcome,
        output_schema_valid=False,
        idempotent=False,
    )
    errors = _case_errors(
        case,
        candidate=candidate,
        actual_mode="reject",
        contract_outcome=contract_outcome,
        output_schema=output_schema,
        idempotent=False,
        checks=checks,
    )
    if case.contract_status == "EXACT" and errors:
        errors.append("IMPLEMENTATION_SPEC_CONFLICT")
    if raw_before != case.input_claim:
        errors.append("INPUT_MUTATED")
    errors = list(dict.fromkeys(errors))
    return {
        "case_id": case.case_id,
        "slice": case.slice,
        "difficulty": case.difficulty,
        "contract_status": case.contract_status,
        "contract_resolution": case.contract_resolution,
        "expected": case.expected.model_dump(mode="json"),
        "scoring_scope": case.scoring_scope.model_dump(mode="json"),
        "input_claim": case.input_claim,
        "hydrated_claim": hydrated_payload,
        "validation_diagnostic": validation,
        "repair_diagnostic": repair,
        "normalizer_output": None,
        "second_pass_output": None,
        "top_level_state": None,
        "payload_state": None,
        "predicate_representation": {
            "predicate_type": candidate.predicate_type.value,
            "raw_predicate": candidate.raw_predicate,
            "canonical_predicate": candidate.canonical_predicate,
            "custom_predicate": candidate.custom_predicate,
        },
        "normalizer_mode": "reject",
        "contract_outcome": contract_outcome,
        "actual_normalization_mode": "reject",
        "idempotent": False,
        "idempotency_diff_paths": [],
        "output_schema": output_schema,
        "checks": checks,
        "diagnostics": sorted(set(diagnostics)),
        "errors": errors,
        "primary_error": errors[0] if errors else None,
        "contract_rejection": {"code": error.code, "detail": error.detail},
        "passed": not errors,
    }


def _hydrate_raw_claim(
    case: NormalizationV1Case,
) -> tuple[dict[str, Any], AtomicClaim]:
    payload = deepcopy(case.input_claim)
    payload.setdefault("claim_id", case.case_id.casefold())
    if not payload.get("predicate"):
        payload["predicate"] = (
            payload.get("raw_predicate")
            or payload.get("canonical_predicate")
            or payload.get("custom_predicate")
            or ("has_state" if payload.get("state_dimension") else "unknown")
        )
    payload.setdefault("evidence_spans", [payload["summary"]])
    claim_payload = deepcopy(payload.get("payload") or {})
    for field in ("state_dimension", "state_value"):
        if payload.get(field) is not None:
            claim_payload.setdefault(field, payload[field])
    payload["payload"] = claim_payload
    return payload, AtomicClaim.model_validate(payload)


def _validate_raw_claim_keys(value: dict[str, Any]) -> None:
    unknown = set(value) - set(AtomicClaim.model_fields)
    if unknown:
        raise ValueError(
            "input_claim contains unsupported fields: "
            + ", ".join(sorted(str(field) for field in unknown))
        )
    required = {"kind", "subject", "summary"}
    missing = sorted(field for field in required if field not in value)
    if missing:
        raise ValueError("input_claim missing required fields: " + ", ".join(missing))


def _validation_diagnostic(claim: AtomicClaim, source_text: str) -> dict[str, Any]:
    try:
        validate_memory_claim_generic(claim.model_copy(deep=True), source_text, set())
    except Exception as exc:
        return {
            "status": "reject",
            "error_type": type(exc).__name__,
            "error": str(exc)[:1000],
            "diagnostics": _error_diagnostics(exc),
        }
    return {
        "status": "accept",
        "error_type": None,
        "error": None,
        "diagnostics": [],
    }


def _repair_diagnostic(
    hydrated_payload: dict[str, Any],
    source_text: str,
) -> dict[str, Any]:
    response = {
        "should_extract": True,
        "gate_reason": "STABLE_FACT",
        "claims": [hydrated_payload],
        "discarded_spans": [],
    }
    try:
        parsed = parse_memory_response(
            json.dumps(response, ensure_ascii=False, default=str),
            source_text=source_text,
        )
    except MemoryResponseError as exc:
        return {
            "status": "error",
            "error_category": exc.category,
            "error": str(exc)[:1000],
            "repair_status": exc.repair_status,
            "repair_steps": exc.repair_steps,
            "extraction_status": None,
            "invalid_claim_count": int(exc.details.get("invalid_claim_count") or 0),
            "invalid_claim_reasons": exc.details.get("invalid_claim_reasons"),
            "diagnostics": ["REPAIR_CONTRACT_ERROR"],
        }
    diagnostics = []
    if parsed.extraction_status == "claim_schema_invalid":
        diagnostics.append("REPAIR_REJECTED_RAW_CLAIM")
    if parsed.repair_steps:
        diagnostics.append("REPAIR_CHANGED_RAW_CLAIM")
    return {
        "status": "completed",
        "error_category": None,
        "error": None,
        "repair_status": parsed.repair_status,
        "repair_steps": parsed.repair_steps,
        "extraction_status": parsed.extraction_status,
        "invalid_claim_count": parsed.invalid_claim_count,
        "invalid_claim_reasons": list(parsed.invalid_claim_reasons),
        "post_repair_claims": [
            claim.model_dump(mode="json") for claim in parsed.extraction.claims
        ],
        "diagnostics": diagnostics,
    }


def _raw_shape_diagnostics(raw: dict[str, Any]) -> list[str]:
    diagnostics: list[str] = []
    if raw.get("canonical_predicate") and raw.get("custom_predicate"):
        diagnostics.append("CANONICAL_CUSTOM_CONFLICT")
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    dimension = raw.get("state_dimension") or payload.get("state_dimension")
    value = raw.get("state_value") or payload.get("state_value")
    if dimension is not None:
        canonical_dimension = normalize_state_dimension(dimension)
        if canonical_dimension is None:
            canonical_spec = next(
                (
                    spec
                    for spec in CANONICAL_PREDICATES.values()
                    if spec.state_dimension == dimension
                ),
                None,
            )
            if canonical_spec is None:
                diagnostics.append("UNKNOWN_STATE_DIMENSION")
            elif (
                value is not None
                and canonical_spec.allowed_values
                and str(value) not in canonical_spec.allowed_values
            ):
                diagnostics.append("STATE_VALUE_INVALID")
        elif value is not None and normalize_state_value(canonical_dimension, value) is None:
            diagnostics.append("STATE_VALUE_INVALID")
    return diagnostics


def _output_schema_diagnostic(candidate: MemoryCandidate) -> dict[str, Any]:
    diagnostics: list[str] = []
    errors: list[str] = []
    try:
        MemoryCandidate.model_validate(candidate.model_dump(mode="python"))
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}"[:1000])
        diagnostics.append("SCHEMA_INVALID")

    if candidate.canonical_predicate and candidate.custom_predicate:
        diagnostics.append("CANONICAL_CUSTOM_CONFLICT")
        errors.append("canonical_predicate and custom_predicate are both populated")
    if candidate.predicate_type == PredicateType.CANONICAL:
        if candidate.canonical_predicate not in CANONICAL_PREDICATES:
            diagnostics.append("SCHEMA_INVALID")
            errors.append("canonical predicate is not registered")
        if candidate.custom_predicate is not None:
            diagnostics.append("CANONICAL_CUSTOM_CONFLICT")
            errors.append("canonical output retains custom_predicate")
    elif not candidate.custom_predicate or candidate.canonical_predicate is not None:
        diagnostics.append("SCHEMA_INVALID")
        errors.append("custom output has an invalid predicate representation")

    if candidate.kind == MemoryKind.RELATIONSHIP_STATE and not _valid_state_output(
        candidate
    ):
        diagnostics.extend(_state_output_diagnostics(candidate))
        errors.append("relationship state output is not registered")
    return {
        "valid": not errors,
        "errors": errors,
        "diagnostics": sorted(set(diagnostics)),
    }


def _valid_state_output(candidate: MemoryCandidate) -> bool:
    payload_dimension = normalize_state_dimension(candidate.payload.get("state_dimension"))
    payload_value = normalize_state_value(
        payload_dimension,
        candidate.payload.get("state_value"),
    )
    if payload_dimension is not None and payload_value is not None:
        return True
    spec = CANONICAL_PREDICATES.get(candidate.canonical_predicate or "")
    if spec is None or spec.state_dimension is None or candidate.state_value is None:
        return False
    return not spec.allowed_values or candidate.state_value in spec.allowed_values


def _state_output_diagnostics(candidate: MemoryCandidate) -> list[str]:
    dimension = candidate.state_dimension or candidate.payload.get("state_dimension")
    value = candidate.state_value or candidate.payload.get("state_value")
    if dimension is None or (
        normalize_state_dimension(dimension) is None
        and not any(
            spec.state_dimension == dimension for spec in CANONICAL_PREDICATES.values()
        )
    ):
        return ["UNKNOWN_STATE_DIMENSION"]
    if value is None:
        return ["STATE_VALUE_INVALID"]
    return ["STATE_VALUE_INVALID"]


def _normalizer_mode(raw: dict[str, Any], candidate: MemoryCandidate) -> str:
    if _representation_preserved(raw, candidate):
        return "preserve"
    if candidate.kind == MemoryKind.RELATIONSHIP_STATE and (
        candidate.state_dimension is not None
        or candidate.payload.get("state_dimension") is not None
    ):
        return "state"
    if candidate.predicate_type == PredicateType.CANONICAL:
        return "canonical"
    if candidate.predicate_type == PredicateType.CUSTOM:
        return "custom"
    return "reject"


def _representation_preserved(raw: dict[str, Any], candidate: MemoryCandidate) -> bool:
    declared_canonical = raw.get("canonical_predicate")
    if declared_canonical is not None:
        return (
            raw.get("custom_predicate") is None
            and raw.get("predicate_type") in {None, PredicateType.CANONICAL.value}
            and candidate.predicate_type == PredicateType.CANONICAL
            and candidate.canonical_predicate == declared_canonical
            and candidate.custom_predicate is None
        )
    declared_custom = raw.get("custom_predicate")
    if declared_custom is not None and raw.get("canonical_predicate") is None:
        return (
            candidate.predicate_type == PredicateType.CUSTOM
            and candidate.custom_predicate == declared_custom
            and candidate.canonical_predicate is None
        )
    declared_dimension = raw.get("state_dimension")
    declared_value = raw.get("state_value")
    return (
        declared_dimension is not None
        and declared_value is not None
        and candidate.state_dimension == declared_dimension
        and candidate.state_value == declared_value
    )


def _case_checks(
    case: NormalizationV1Case,
    *,
    candidate: MemoryCandidate,
    actual_mode: str,
    contract_outcome: str,
    output_schema_valid: bool,
    idempotent: bool,
) -> dict[str, bool | None]:
    expected = case.expected
    scope = case.scoring_scope
    payload_pass = all(
        candidate.payload.get(key) == value
        for key, value in expected.payload_equals.items()
    ) and all(key not in candidate.payload for key in expected.payload_absent)
    schema_pass = output_schema_valid and payload_pass
    if scope.contract_outcome and expected.contract_outcome in {"reject", "defer"}:
        schema_pass = contract_outcome == expected.contract_outcome
    return {
        "normalization_mode": actual_mode == expected.normalization_mode,
        "canonical_mapping": (
            candidate.canonical_predicate == expected.canonical_predicate
            if scope.canonical_mapping
            else None
        ),
        "state_dimension": (
            candidate.state_dimension == expected.state_dimension
            if scope.state_dimension
            else None
        ),
        "state_value": (
            candidate.state_value == expected.state_value
            if scope.state_value
            else None
        ),
        "custom_preservation": (
            candidate.predicate_type == PredicateType.CUSTOM
            and candidate.canonical_predicate is None
            and candidate.custom_predicate == expected.custom_predicate
            if scope.custom_preservation
            else None
        ),
        "unsafe_canonicalization": (
            candidate.predicate_type == PredicateType.CANONICAL
            or candidate.canonical_predicate is not None
            or actual_mode == "state"
            if scope.unsafe_canonicalization
            else None
        ),
        "schema_validity": schema_pass if scope.schema_validity else None,
        "idempotency": idempotent if scope.idempotency else None,
        "contract_outcome": (
            contract_outcome == expected.contract_outcome
            if scope.contract_outcome
            else None
        ),
        "payload_constraints": payload_pass,
    }


def _case_errors(
    case: NormalizationV1Case,
    *,
    candidate: MemoryCandidate,
    actual_mode: str,
    contract_outcome: str,
    output_schema: dict[str, Any],
    idempotent: bool,
    checks: dict[str, bool | None],
) -> list[str]:
    errors: list[str] = []
    expected = case.expected
    scope = case.scoring_scope
    if checks["normalization_mode"] is False:
        errors.append("WRONG_NORMALIZATION_MODE")
    if scope.contract_outcome and checks["contract_outcome"] is False:
        errors.append(
            "UNEXPECTED_ACCEPT" if contract_outcome == "accept" else "UNEXPECTED_REJECT"
        )
    if scope.canonical_mapping and checks["canonical_mapping"] is False:
        errors.append(
            "MISSED_CANONICAL_MAPPING"
            if candidate.canonical_predicate is None
            else "WRONG_CANONICAL_MAPPING"
        )
    if scope.state_dimension and checks["state_dimension"] is False:
        errors.append("WRONG_STATE_DIMENSION")
    if scope.state_value and checks["state_value"] is False:
        errors.append("WRONG_STATE_VALUE")
    if scope.custom_preservation and checks["custom_preservation"] is False:
        errors.append("CUSTOM_NOT_PRESERVED")
    if scope.unsafe_canonicalization and checks["unsafe_canonicalization"] is True:
        errors.append("UNSAFE_CANONICALIZATION")
    if scope.schema_validity and checks["schema_validity"] is False:
        errors.append("SCHEMA_INVALID")
    if scope.idempotency and not idempotent:
        errors.append("NON_IDEMPOTENT")
    contract_rejected_as_expected = bool(
        scope.contract_outcome
        and expected.contract_outcome == "reject"
        and checks["contract_outcome"] is True
    )
    if not contract_rejected_as_expected:
        if "CANONICAL_CUSTOM_CONFLICT" in output_schema["diagnostics"]:
            errors.append("CANONICAL_CUSTOM_CONFLICT")
        if "UNKNOWN_STATE_DIMENSION" in output_schema["diagnostics"]:
            errors.append("UNKNOWN_STATE_DIMENSION")
        if "STATE_VALUE_INVALID" in output_schema["diagnostics"]:
            errors.append("STATE_VALUE_INVALID")
    if checks["payload_constraints"] is False:
        errors.append("SCHEMA_INVALID")
    if expected.must_not_force_canonical and actual_mode == "state":
        errors.append("UNSAFE_CANONICALIZATION")
    return list(dict.fromkeys(errors))


def _aggregate_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    output_names = {
        "canonical_mapping": "canonical_mapping_accuracy",
        "state_dimension": "state_dimension_accuracy",
        "state_value": "state_value_accuracy",
        "custom_preservation": "custom_preservation_accuracy",
        "schema_validity": "schema_validity",
        "idempotency": "idempotency_accuracy",
        "contract_outcome": "contract_outcome_accuracy",
    }
    details: dict[str, dict[str, int]] = {}
    for name, output_name in output_names.items():
        values = [row["checks"][name] for row in rows if row["checks"][name] is not None]
        details[output_name] = {
            "numerator": sum(value is True for value in values),
            "denominator": len(values),
        }
    unsafe_values = [
        row["checks"]["unsafe_canonicalization"]
        for row in rows
        if row["checks"]["unsafe_canonicalization"] is not None
    ]
    details["unsafe_canonicalization_rate"] = {
        "numerator": sum(value is True for value in unsafe_values),
        "denominator": len(unsafe_values),
    }
    coverage_rows = [
        row
        for row in rows
        if row.get("normalizer_output") is not None
        and not (
            row["scoring_scope"]["contract_outcome"]
            and row["expected"]["contract_outcome"] in {"reject", "defer"}
        )
    ]
    details["canonical_coverage"] = {
        "numerator": sum(
            row["predicate_representation"]["canonical_predicate"] is not None
            for row in coverage_rows
        ),
        "denominator": len(coverage_rows),
    }
    result: dict[str, Any] = {
        "case_count": len(rows),
        "passed_case_count": sum(bool(row["passed"]) for row in rows),
    }
    for output_name in output_names.values():
        result[output_name] = _ratio_detail(details[output_name])
    result["unsafe_canonicalization_rate"] = _ratio_detail(
        details["unsafe_canonicalization_rate"]
    )
    result["canonical_coverage"] = _ratio_detail(details["canonical_coverage"])
    result["details"] = details
    result["validation_boundary_rejection_count"] = sum(
        row.get("validation_diagnostic", {}).get("status") == "reject" for row in rows
    )
    return result


def _failed_case(case: NormalizationV1Case, exc: Exception) -> dict[str, Any]:
    checks = {
        "normalization_mode": False,
        "canonical_mapping": (
            False if case.scoring_scope.canonical_mapping else None
        ),
        "state_dimension": False if case.scoring_scope.state_dimension else None,
        "state_value": False if case.scoring_scope.state_value else None,
        "custom_preservation": (
            False if case.scoring_scope.custom_preservation else None
        ),
        "unsafe_canonicalization": (
            False if case.scoring_scope.unsafe_canonicalization else None
        ),
        "schema_validity": False if case.scoring_scope.schema_validity else None,
        "idempotency": False if case.scoring_scope.idempotency else None,
        "contract_outcome": False if case.scoring_scope.contract_outcome else None,
        "payload_constraints": False,
    }
    return {
        "case_id": case.case_id,
        "slice": case.slice,
        "difficulty": case.difficulty,
        "contract_status": case.contract_status,
        "contract_resolution": case.contract_resolution,
        "expected": case.expected.model_dump(mode="json"),
        "scoring_scope": case.scoring_scope.model_dump(mode="json"),
        "input_claim": case.input_claim,
        "hydrated_claim": None,
        "validation_diagnostic": None,
        "repair_diagnostic": None,
        "normalizer_output": None,
        "second_pass_output": None,
        "top_level_state": None,
        "payload_state": None,
        "predicate_representation": {
            "predicate_type": None,
            "raw_predicate": None,
            "canonical_predicate": None,
            "custom_predicate": None,
        },
        "normalizer_mode": "reject",
        "contract_outcome": "reject",
        "actual_normalization_mode": "reject",
        "idempotent": False,
        "idempotency_diff_paths": [],
        "output_schema": {"valid": False, "errors": [str(exc)], "diagnostics": []},
        "checks": checks,
        "diagnostics": [],
        "errors": ["NORMALIZATION_EXCEPTION", "SCHEMA_INVALID"],
        "primary_error": "NORMALIZATION_EXCEPTION",
        "execution_error": f"{type(exc).__name__}: {exc}"[:1000],
        "passed": False,
    }


def _validate_complete_dataset(cases: list[NormalizationV1Case]) -> None:
    ids = {case.case_id for case in cases}
    if ids != EXPECTED_CASE_IDS:
        missing = sorted(EXPECTED_CASE_IDS - ids)
        unexpected = sorted(ids - EXPECTED_CASE_IDS)
        raise ValueError(
            "Normalization V1 dataset must contain exactly NORM-001..NORM-056; "
            f"missing={missing}, unexpected={unexpected}"
        )
    counts = Counter(case.slice for case in cases)
    if dict(counts) != EXPECTED_SLICE_COUNTS:
        raise ValueError(
            "Normalization V1 slice counts do not match the external specification: "
            f"expected={EXPECTED_SLICE_COUNTS}, actual={dict(counts)}"
        )
    actual_verify = {
        case.case_id for case in cases if case.contract_status == "CONTRACT_VERIFY"
    }
    if actual_verify != CONTRACT_VERIFY_CASE_IDS:
        raise ValueError(
            "Normalization V1 CONTRACT_VERIFY cases do not match the external spec: "
            f"expected={sorted(CONTRACT_VERIFY_CASE_IDS)}, "
            f"actual={sorted(actual_verify)}"
        )


def _error_diagnostics(exc: Exception) -> list[str]:
    text = str(exc)
    values = ["VALIDATION_BOUNDARY_REJECTED"]
    if "同时提供 canonical 和 custom" in text:
        values.append("CANONICAL_CUSTOM_CONFLICT")
    if "state_dimension/state_value" in text:
        values.append("STATE_SHAPE_REJECTED")
    return values


def _diff_paths(left: Any, right: Any, prefix: str = "") -> list[str]:
    if type(left) is not type(right):
        return [prefix or "$"]
    if isinstance(left, dict):
        paths: list[str] = []
        for key in sorted(set(left) | set(right)):
            child = f"{prefix}.{key}" if prefix else str(key)
            if key not in left or key not in right:
                paths.append(child)
            else:
                paths.extend(_diff_paths(left[key], right[key], child))
        return paths[:50]
    if isinstance(left, list):
        if len(left) != len(right):
            return [prefix or "$"]
        paths = []
        for index, (left_value, right_value) in enumerate(zip(left, right, strict=True)):
            paths.extend(
                _diff_paths(left_value, right_value, f"{prefix}[{index}]")
            )
        return paths[:50]
    return [] if left == right else [prefix or "$"]


def _ratio_detail(detail: dict[str, int]) -> float | None:
    denominator = detail["denominator"]
    if denominator == 0:
        return None
    return round(detail["numerator"] / denominator, 4)


def _at_least(value: float | None, threshold: float) -> bool:
    return value is not None and value >= threshold


def _at_most(value: float | None, threshold: float) -> bool:
    return value is not None and value <= threshold


def _format_metric(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def _top_bottlenecks(metrics: dict[str, Any]) -> list[str]:
    candidates: list[tuple[float, str]] = []
    for name, target in (
        ("canonical_mapping_accuracy", 0.90),
        ("state_dimension_accuracy", 0.90),
        ("state_value_accuracy", 0.90),
        ("custom_preservation_accuracy", 0.95),
        ("schema_validity", 1.0),
        ("idempotency_accuracy", 0.98),
    ):
        value = metrics[name]
        if value is not None:
            candidates.append((max(0.0, target - value), name))
    unsafe = metrics["unsafe_canonicalization_rate"]
    if unsafe is not None:
        candidates.append((max(0.0, unsafe - 0.05), "unsafe_canonicalization_rate"))
    ranked = sorted(candidates, key=lambda item: (-item[0], item[1]))
    return [f"{name} (target gap={gap:.4f})" for gap, name in ranked[:3]]

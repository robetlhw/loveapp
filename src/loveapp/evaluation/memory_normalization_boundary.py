"""Evaluation harness for the Raw-claim/Normalized-claim validation boundary.

The boundary evaluation is intentionally offline and deterministic.  It feeds
fixed Raw claims through the same generic validator, contract normalizer, and
post-normalization validator used by Memory ingress, while recording the
stage at which a claim is rejected.  It does not write to a Memory Store.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from loveapp.application.memory_repair import (
    validate_memory_claim_generic,
)
from loveapp.domain.memory import AtomicClaim, MemoryCandidate, PredicateType
from loveapp.domain.memory_normalization import (
    NormalizationContractError,
    normalize_memory_candidate_contract,
    validate_normalized_memory_candidate,
)

BoundaryOutcome = Literal["accept", "reject"]
NormalizationMode = Literal["canonical", "state", "custom", "reject"]


class BoundaryExpected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generic_validation: BoundaryOutcome
    normalization: NormalizationMode
    contract_outcome: BoundaryOutcome
    canonical_predicate: str | None = None
    custom_predicate: str | None = None
    state_dimension: str | None = None
    state_value: str | None = None
    semantic_valid: bool = True
    hint_only: bool = False


class BoundaryCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^BND-\d{3}$")
    category: str = Field(min_length=1, max_length=80)
    description: str = ""
    input_claim: dict[str, Any]
    source_text: str | None = None
    expected: BoundaryExpected


def load_memory_normalization_boundary_cases(
    path: Path,
    *,
    require_complete: bool = True,
) -> list[BoundaryCase]:
    cases: list[BoundaryCase] = []
    seen: set[str] = set()
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(),
        1,
    ):
        if not line.strip():
            continue
        try:
            case = BoundaryCase.model_validate_json(line)
        except Exception as exc:
            raise ValueError(
                f"invalid normalization boundary case on line {line_number}: {exc}"
            ) from exc
        if case.case_id in seen:
            raise ValueError(f"duplicate normalization boundary case_id: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError("normalization boundary dataset is empty")
    if require_complete:
        expected = {f"BND-{index:03d}" for index in range(1, 21)}
        if seen != expected:
            raise ValueError(
                "normalization boundary dataset must contain BND-001..BND-020; "
                f"missing={sorted(expected - seen)}, unexpected={sorted(seen - expected)}"
            )
    return cases


def evaluate_memory_normalization_boundary(
    dataset_path: Path,
    *,
    case_id: str | None = None,
    fail_on_error: bool = False,
    require_complete: bool = True,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    """Run the boundary set against production validation/normalization.

    A strict post-normalization contract is used intentionally.  Legacy
    open-world compatibility is a production ingress concern and would hide a
    canonical validation failure in this contract audit.
    """

    raw_bytes = dataset_path.read_bytes()
    all_cases = load_memory_normalization_boundary_cases(
        dataset_path,
        require_complete=require_complete,
    )
    cases = [case for case in all_cases if case_id is None or case.case_id == case_id]
    if not cases:
        raise ValueError(f"no boundary cases matched case_id={case_id!r}")
    now = reference_time or datetime.fromisoformat("2026-09-02T10:00:00+08:00")
    rows: list[dict[str, Any]] = []
    for case in cases:
        try:
            rows.append(_evaluate_case(case, reference_time=now))
        except Exception as exc:
            if fail_on_error:
                raise
            rows.append(_failed_case(case, exc))

    metrics = _aggregate_metrics(rows)
    return {
        "evaluation": "memory_normalization_boundary_v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "dataset": str(dataset_path),
        "dataset_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "case_filter": case_id,
        "case_count": len(rows),
        "passed_case_count": sum(bool(row["passed"]) for row in rows),
        "model_calls_permitted": False,
        "store_mutation_permitted": False,
        "normalization_path": (
            "Raw claim -> Generic Validator -> deterministic contract normalizer -> "
            "Canonical/Normalized Validator"
        ),
        "metrics": metrics,
        "error_taxonomy": dict(
            sorted(Counter(error for row in rows for error in row["errors"]).items())
        ),
        "cases": rows,
        "boundary_status": (
            "PASS"
            if all(bool(row["passed"]) for row in rows)
            else "NEEDS_REVIEW"
        ),
    }


def evaluate_memory_normalization_v1_2(
    boundary_dataset: Path,
    *,
    normalization_v1_1_report: Path | None = None,
    case_id: str | None = None,
    fail_on_error: bool = False,
) -> dict[str, Any]:
    """Combine the boundary result with the previous V1.1 baseline metrics."""

    boundary = evaluate_memory_normalization_boundary(
        boundary_dataset,
        case_id=case_id,
        fail_on_error=fail_on_error,
        require_complete=case_id is None,
    )
    before: dict[str, Any] = {}
    if normalization_v1_1_report and normalization_v1_1_report.exists():
        payload = json.loads(normalization_v1_1_report.read_text(encoding="utf-8"))
        before = payload.get("metrics", {})
    return {
        "evaluation": "memory_normalization_v1_2",
        "generated_at": datetime.now().astimezone().isoformat(),
        "boundary": boundary,
        "before_v1_1": before,
        "after_boundary": boundary["metrics"],
        "normalization_status": (
            "FREEZE_CANDIDATE"
            if _boundary_targets_pass(boundary["metrics"])
            else "NOT_FROZEN"
        ),
    }


def render_memory_normalization_boundary_report(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# Memory Validation Boundary Migration Report",
        "",
        f"Generated: `{report['generated_at']}`  ",
        f"Dataset: `{report['dataset']}`  ",
        f"Dataset SHA256: `{report['dataset_sha256']}`  ",
        "Generic Validator and Canonical Validator are scored as separate stages.",
        "Store mutation permitted: `False`",
        "",
        "## Boundary Metrics",
        "",
        "| Metric | Result | Numerator | Denominator | Target |",
        "|---|---:|---:|---:|---:|",
    ]
    targets = {
        "generic_validation_acceptance_rate": ">=0.95",
        "false_pre_normalization_rejection_rate": "<=0.05",
        "normalizer_recovery_accuracy": ">=0.95",
        "validation_boundary_rejection_count": "generic-invalid only",
    }
    for name in (
        "generic_validation_acceptance_rate",
        "false_pre_normalization_rejection_rate",
        "normalizer_recovery_accuracy",
        "validation_boundary_rejection_count",
    ):
        detail = metrics["details"][name]
        value = metrics[name]
        rendered = str(value) if isinstance(value, int) else _format_metric(value)
        lines.append(
            f"| {name} | {rendered} | {detail['numerator']} | "
            f"{detail['denominator']} | {targets[name]} |"
        )
    lines.extend(
        [
            "",
            "## Layer Results",
            "",
            "| Case | Generic | Normalization | Canonical | Retained | Drop stage | Errors |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for row in report["cases"]:
        lines.append(
            f"| {row['case_id']} | {row['generic_validation']['status']} | "
            f"{row['normalization']['status']} | {row['canonical_validation']['status']} | "
            f"{row['production_claim_retained']} | {row['where_dropped'] or '-'} | "
            f"{', '.join(row['errors']) or '-'} |"
        )
    lines.extend(
        [
            "",
            "## Error Taxonomy",
            "",
            "| Error | Cases |",
            "|---|---:|",
        ]
    )
    for name, count in report["error_taxonomy"].items():
        lines.append(f"| {name} | {count} |")
    lines.extend(
        [
            "",
            "## Diagnostic Taxonomy",
            "",
            "| Diagnostic | Cases |",
            "|---|---:|",
        ]
    )
    for name, count in report["metrics"].get("diagnostic_taxonomy", {}).items():
        lines.append(f"| {name} | {count} |")
    lines.extend(
        [
            "",
            f"Boundary status: `{report['boundary_status']}`",
            "",
            "Raw semantic validity is evaluated independently from canonical validity.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_memory_normalization_v1_2_report(report: dict[str, Any]) -> str:
    boundary = report["boundary"]
    before = report.get("before_v1_1") or {}
    after = report["after_boundary"]
    lines = [
        "# Memory Normalization V1.2 Boundary Report",
        "",
        f"Generated: `{report['generated_at']}`  ",
        f"Normalization V1.2 status: `{report['normalization_status']}`",
        "",
        "## Before / After",
        "",
        "| Metric | Before V1.1 | Boundary V1.2 |",
        "|---|---:|---:|",
    ]
    before_metrics = {
        "Validation Boundary Reject Count": before.get(
            "validation_boundary_rejection_count", "n/a"
        ),
        "Generic Validation Acceptance": "n/a",
        "False Pre-Normalization Reject Rate": "n/a",
        "Canonical Mapping Accuracy": before.get("canonical_mapping_accuracy", "n/a"),
        "State Dimension Accuracy": before.get("state_dimension_accuracy", "n/a"),
        "State Value Accuracy": before.get("state_value_accuracy", "n/a"),
        "Custom Preservation": before.get("custom_preservation_accuracy", "n/a"),
        "Unsafe Canonicalization": before.get("unsafe_canonicalization_rate", "n/a"),
        "Idempotency": before.get("idempotency_accuracy", "n/a"),
    }
    after_metrics = {
        "Validation Boundary Reject Count": after["validation_boundary_rejection_count"],
        "Generic Validation Acceptance": after["generic_validation_acceptance_rate"],
        "False Pre-Normalization Reject Rate": after[
            "false_pre_normalization_rejection_rate"
        ],
        "Canonical Mapping Accuracy": "see V1.1 baseline",
        "State Dimension Accuracy": "see V1.1 baseline",
        "State Value Accuracy": "see V1.1 baseline",
        "Custom Preservation": "see V1.1 baseline",
        "Unsafe Canonicalization": "see V1.1 baseline",
        "Idempotency": "see V1.1 baseline",
    }
    for name, value in before_metrics.items():
        lines.append(f"| {name} | {_display(value)} | {_display(after_metrics[name])} |")
    lines.extend(
        [
            "",
            "## Boundary Result",
            "",
            f"- Cases: `{boundary['case_count']}`  ",
            f"- Passed: `{boundary['passed_case_count']}`  ",
            f"- Generic acceptance: `{after['generic_validation_acceptance_rate']}`  ",
            f"- False pre-normalization rejection: `"
            f"{after['false_pre_normalization_rejection_rate']}`  ",
            f"- Normalizer recovery: `{after['normalizer_recovery_accuracy']}`",
            "",
            "Production extraction is wired to the Raw/Generic validation boundary before "
            "deterministic normalization. This migration does not alter the Extraction "
            "Prompt, ontology, Relation, Lifecycle, or Store contracts.",
        ]
    )
    return "\n".join(lines) + "\n"


def _evaluate_case(
    case: BoundaryCase,
    *,
    reference_time: datetime,
) -> dict[str, Any]:
    raw_claim = deepcopy(case.input_claim)
    hydrated = _hydrate_claim(raw_claim)
    source_text = case.source_text or " ".join(hydrated.get("evidence_spans", []))
    generic = _run_generic_validator(hydrated, source_text)
    normalization: dict[str, Any] = {
        "status": "not_run",
        "output": None,
        "error": None,
        "diagnostics": [],
    }
    canonical: dict[str, Any] = {
        "status": "not_run",
        "error": None,
        "diagnostics": [],
    }
    final: MemoryCandidate | None = None
    if generic["status"] == "accept":
        claim = generic["claim"]
        candidate = claim.to_candidate()
        normalization["input"] = candidate.model_dump(mode="json")
        try:
            final = normalize_memory_candidate_contract(
                candidate,
                reference_time,
                allow_legacy_open_world=False,
            )
        except NormalizationContractError as exc:
            normalization.update(
                status="reject",
                error=str(exc),
                diagnostics=[exc.code],
            )
        except Exception as exc:
            normalization.update(
                status="error",
                error=f"{type(exc).__name__}: {exc}",
                diagnostics=[_normalization_error_code(exc)],
            )
        else:
            normalization.update(
                status="accept",
                output=final.model_dump(mode="json"),
            )
            try:
                validate_normalized_memory_candidate(
                    final,
                    allow_legacy_open_world=False,
                )
            except Exception as exc:
                canonical.update(
                    status="reject",
                    error=f"{type(exc).__name__}: {exc}",
                    diagnostics=[_canonical_error_code(exc)],
                )
            else:
                canonical["status"] = "accept"

    expected = case.expected
    errors: list[str] = []
    if generic["status"] != expected.generic_validation:
        errors.append(
            "GENERIC_SCHEMA_INVALID"
            if expected.generic_validation == "reject"
            else "FALSE_PRE_NORMALIZATION_REJECT"
        )
    actual_mode = _normalization_mode(final) if final is not None else "reject"
    if actual_mode != expected.normalization:
        errors.append("WRONG_NORMALIZATION_MODE")
    if expected.canonical_predicate is not None and (
        final is None or final.canonical_predicate != expected.canonical_predicate
    ):
        errors.append("WRONG_CANONICAL_MAPPING")
    if expected.custom_predicate is not None and (
        final is None or final.custom_predicate != expected.custom_predicate
    ):
        errors.append("CUSTOM_NOT_PRESERVED")
    if expected.state_dimension is not None and (
        final is None or final.state_dimension != expected.state_dimension
    ):
        errors.append("WRONG_STATE_DIMENSION")
    if expected.state_value is not None and (
        final is None or final.state_value != expected.state_value
    ):
        errors.append("WRONG_STATE_VALUE")
    actual_contract = "accept" if canonical["status"] == "accept" else "reject"
    if actual_contract != expected.contract_outcome:
        errors.append(
            "UNEXPECTED_ACCEPT" if actual_contract == "accept" else "UNEXPECTED_REJECT"
        )
    if expected.semantic_valid and generic["status"] == "reject":
        errors.append("FALSE_PRE_NORMALIZATION_REJECT")
    if not expected.semantic_valid and generic["status"] == "accept":
        errors.append("GENERIC_SCHEMA_INVALID")
    recovery = bool(
        generic["status"] == "accept"
        and canonical["status"] == "accept"
        and actual_mode == expected.normalization
    )
    if expected.hint_only and not recovery:
        errors.append("NORMALIZATION_UNRESOLVED")
    errors = list(dict.fromkeys(errors))
    where_dropped = _first_failure_stage(generic, normalization, canonical)
    return {
        "case_id": case.case_id,
        "category": case.category,
        "description": case.description,
        "input_claim": raw_claim,
        "expected": expected.model_dump(mode="json"),
        "generic_validation": _public_stage(generic),
        "normalizer_input": normalization.get("input"),
        "normalization": _public_stage(normalization),
        "canonical_validation": _public_stage(canonical),
        "final_claim": final.model_dump(mode="json") if final is not None else None,
        "diagnostics": sorted(
            {
                *generic.get("diagnostics", []),
                *normalization.get("diagnostics", []),
                *canonical.get("diagnostics", []),
            }
        ),
        "production_claim_retained": bool(
            generic["status"] == "accept" and canonical["status"] == "accept"
        ),
        "where_dropped": where_dropped,
        "why_dropped": _failure_reason(generic, normalization, canonical),
        "recovery": recovery,
        "errors": errors,
        "passed": not errors,
    }


def _run_generic_validator(raw: dict[str, Any], source_text: str) -> dict[str, Any]:
    try:
        _validate_raw_shape(raw)
        claim = AtomicClaim.model_validate(raw)
        validate_memory_claim_generic(claim, source_text, set())
    except Exception as exc:
        return {
            "status": "reject",
            "claim": None,
            "error": f"{type(exc).__name__}: {exc}",
            "diagnostics": [_generic_error_code(exc)],
        }
    return {
        "status": "accept",
        "claim": claim,
        "error": None,
        "diagnostics": [],
    }


def _hydrate_claim(raw: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(raw)
    payload.setdefault("claim_id", "boundary-claim")
    payload.setdefault(
        "predicate",
        payload.get("raw_predicate")
        or payload.get("canonical_predicate")
        or payload.get("custom_predicate")
        or ("has_state" if payload.get("state_dimension") else "unknown"),
    )
    if "evidence_spans" not in payload:
        payload["evidence_spans"] = [payload.get("summary", "")]
    claim_payload = deepcopy(payload.get("payload") or {})
    for field in ("state_dimension", "state_value"):
        if payload.get(field) is not None:
            claim_payload.setdefault(field, payload[field])
    payload["payload"] = claim_payload
    return payload


def _validate_raw_shape(raw: dict[str, Any]) -> None:
    allowed = set(AtomicClaim.model_fields)
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError("unsupported raw claim fields: " + ", ".join(sorted(unknown)))
    for field in ("kind", "subject", "summary", "evidence_spans"):
        if field not in raw or raw[field] in (None, "", []):
            raise ValueError(f"missing generic field: {field}")


def _normalization_mode(candidate: MemoryCandidate | None) -> str:
    if candidate is None:
        return "reject"
    if candidate.kind.value == "relationship_state" and (
        candidate.state_dimension is not None
        or candidate.payload.get("state_dimension") is not None
    ):
        return "state"
    if candidate.predicate_type == PredicateType.CANONICAL:
        return "canonical"
    if candidate.predicate_type == PredicateType.CUSTOM:
        return "custom"
    return "reject"


def _first_failure_stage(
    generic: dict[str, Any],
    normalization: dict[str, Any],
    canonical: dict[str, Any],
) -> str | None:
    if generic["status"] != "accept":
        return "generic_validation"
    if normalization["status"] not in {"accept", "not_run"}:
        return "normalization"
    if canonical["status"] != "accept":
        return "canonical_validation"
    return None


def _failure_reason(
    generic: dict[str, Any],
    normalization: dict[str, Any],
    canonical: dict[str, Any],
) -> str | None:
    for stage in (generic, normalization, canonical):
        if stage.get("status") not in {"accept", "not_run"}:
            return stage.get("error")
    return None


def _public_stage(stage: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in stage.items()
        if key != "claim" and key != "input"
    }


def _aggregate_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    semantic = [row for row in rows if _expected(row, "semantic_valid")]
    generic_accept = [row for row in semantic if row["generic_validation"]["status"] == "accept"]
    generic_denominator = len(semantic)
    generic_numerator = len(generic_accept)
    false_pre = generic_denominator - generic_numerator
    hint_rows = [row for row in rows if _expected(row, "hint_only")]
    recovered = sum(bool(row["recovery"]) for row in hint_rows)
    details: dict[str, dict[str, int]] = {
        "generic_validation_acceptance_rate": {
            "numerator": generic_numerator,
            "denominator": generic_denominator,
        },
        "false_pre_normalization_rejection_rate": {
            "numerator": false_pre,
            "denominator": generic_denominator,
        },
        "normalizer_recovery_accuracy": {
            "numerator": recovered,
            "denominator": len(hint_rows),
        },
        "validation_boundary_rejection_count": {
            "numerator": sum(
                row["generic_validation"]["status"] == "reject" for row in rows
            ),
            "denominator": len(rows),
        },
    }
    diagnostic_taxonomy = Counter(
        diagnostic
        for row in rows
        for diagnostic in row.get("diagnostics", [])
    )
    return {
        "case_count": len(rows),
        "passed_case_count": sum(bool(row["passed"]) for row in rows),
        "generic_validation_acceptance_rate": _ratio(details["generic_validation_acceptance_rate"]),
        "false_pre_normalization_rejection_rate": _ratio(
            details["false_pre_normalization_rejection_rate"]
        ),
        "normalizer_recovery_accuracy": _ratio(details["normalizer_recovery_accuracy"]),
        "validation_boundary_rejection_count": details[
            "validation_boundary_rejection_count"
        ]["numerator"],
        "details": details,
        "diagnostic_taxonomy": dict(sorted(diagnostic_taxonomy.items())),
    }


def _expected(row: dict[str, Any], key: str) -> bool:
    # Expected fields are copied into each row only when scoring; keeping this
    # helper tolerant makes failed infrastructure rows aggregate cleanly.
    return bool(row.get("expected", {}).get(key, False))


def _failed_case(case: BoundaryCase, exc: Exception) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "category": case.category,
        "description": case.description,
        "input_claim": case.input_claim,
        "expected": case.expected.model_dump(mode="json"),
        "generic_validation": {"status": "error", "error": str(exc), "diagnostics": []},
        "normalizer_input": None,
        "normalization": {"status": "error", "error": str(exc), "diagnostics": []},
        "canonical_validation": {"status": "not_run", "error": None, "diagnostics": []},
        "final_claim": None,
        "diagnostics": ["HARNESS_EXCEPTION"],
        "production_claim_retained": False,
        "where_dropped": "harness",
        "why_dropped": f"{type(exc).__name__}: {exc}",
        "recovery": False,
        "errors": ["HARNESS_EXCEPTION"],
        "passed": False,
    }


def _generic_error_code(exc: Exception) -> str:
    if isinstance(exc, (TypeError, ValueError)):
        text = str(exc).casefold()
        if "evidence" in text:
            return "GENERIC_EVIDENCE_INVALID"
        if "subject" in text:
            return "GENERIC_ENUM_INVALID"
        if "perspective" in text or "kind" in text:
            return "GENERIC_ENUM_INVALID"
        return "GENERIC_SCHEMA_INVALID"
    return "GENERIC_SCHEMA_INVALID"


def _normalization_error_code(exc: Exception) -> str:
    if isinstance(exc, NormalizationContractError):
        return exc.code
    return "NORMALIZATION_UNRESOLVED"


def _canonical_error_code(exc: Exception) -> str:
    text = str(exc).casefold()
    if "canonical" in text and "custom" in text:
        return "CANONICAL_CUSTOM_CONFLICT"
    if "state" in text:
        return "STATE_VALUE_INVALID"
    if "canonical" in text:
        return "CANONICAL_UNREGISTERED"
    return "SCHEMA_INVALID"


def _boundary_targets_pass(metrics: dict[str, Any]) -> bool:
    generic_acceptance = metrics["generic_validation_acceptance_rate"]
    false_rejection = metrics["false_pre_normalization_rejection_rate"]
    recovery = metrics["normalizer_recovery_accuracy"]
    return (
        generic_acceptance is not None
        and generic_acceptance >= 0.95
        and false_rejection is not None
        and false_rejection <= 0.05
        and recovery is not None
        and recovery >= 0.95
    )


def _ratio(detail: dict[str, int]) -> float | None:
    if detail["denominator"] == 0:
        return None
    return round(detail["numerator"] / detail["denominator"], 4)


def _format_metric(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def _display(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)

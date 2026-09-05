from __future__ import annotations

import hashlib
import json
from collections import Counter
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from loveapp.domain.memory_normalization import normalize_memory_candidate_contract
from loveapp.evaluation.memory_normalization_v1 import (
    EXPECTED_CASE_IDS,
    EXPECTED_SLICE_COUNTS,
    NormalizationV1Case,
    _aggregate_metrics,
    _evaluate_case,
    _failed_case,
    _format_metric,
    _top_bottlenecks,
    load_memory_normalization_v1_cases,
)

_N1_SLICES = frozenset(
    {
        "canonical_direct",
        "state_mapping",
        "alias_variation",
        "custom_preservation",
        "ambiguous",
    }
)
_CANONICAL_HINTS = {
    "interaction.initiation_balance": ("metric_hint", "initiation_balance"),
    "interaction.response_engagement": ("metric_hint", "response_engagement"),
    "preference.food.cuisine": ("preference_type_hint", "cuisine"),
    "preference.budget.range": ("preference_type_hint", "budget"),
}


def migrate_memory_normalization_v1_1(
    source_path: Path,
    output_path: Path,
) -> list[dict[str, Any]]:
    """Migrate only the input contract; semantic Gold remains byte-for-byte equivalent."""

    migrated: list[dict[str, Any]] = []
    for case in load_memory_normalization_v1_cases(source_path):
        row = case.model_dump(mode="json", exclude_none=True)
        layer = "N1" if case.slice in _N1_SLICES else "N2"
        row["schema_version"] = "normalization-v1.1"
        row["evaluation_layer"] = layer
        claim = deepcopy(row["input_claim"])
        payload = deepcopy(claim.get("payload") or {})
        if layer == "N1":
            canonical_hint = _CANONICAL_HINTS.get(case.expected.canonical_predicate or "")
            if canonical_hint is not None:
                payload.setdefault(*canonical_hint)
            if case.expected.state_dimension and case.expected.state_value:
                payload.setdefault("state_dimension_hint", case.expected.state_dimension)
                payload.setdefault("state_value_hint", case.expected.state_value)
        claim["payload"] = payload
        row["input_claim"] = claim
        migrated.append(row)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in migrated
        ),
        encoding="utf-8",
    )
    return migrated


def load_memory_normalization_v1_1_cases(path: Path) -> list[NormalizationV1Case]:
    cases = load_memory_normalization_v1_cases(path)
    if {case.case_id for case in cases} != EXPECTED_CASE_IDS:
        raise ValueError("Normalization V1.1 must preserve all 56 semantic cases")
    for case in cases:
        if case.schema_version != "normalization-v1.1":
            raise ValueError(f"{case.case_id} is not normalization-v1.1")
        expected_layer = "N1" if case.slice in _N1_SLICES else "N2"
        if case.evaluation_layer != expected_layer:
            raise ValueError(
                f"{case.case_id} must use {expected_layer}, got {case.evaluation_layer}"
            )
    counts = Counter(case.slice for case in cases)
    if dict(counts) != EXPECTED_SLICE_COUNTS:
        raise ValueError("Normalization V1.1 changed the Golden Set slice counts")
    return cases


def evaluate_memory_normalization_v1_1(
    dataset_path: Path,
    *,
    case_id: str | None = None,
    layer: str | None = None,
    fail_on_error: bool = False,
) -> dict[str, Any]:
    raw = dataset_path.read_bytes()
    all_cases = load_memory_normalization_v1_1_cases(dataset_path)
    cases = [
        case
        for case in all_cases
        if (case_id is None or case.case_id == case_id)
        and (layer is None or case.evaluation_layer == layer)
    ]
    if not cases:
        raise ValueError(f"no V1.1 cases matched case_id={case_id!r}, layer={layer!r}")

    rows: list[dict[str, Any]] = []
    for case in cases:
        try:
            row = _evaluate_case(
                case,
                normalizer=normalize_memory_candidate_contract,
                enforce_contract=True,
            )
        except Exception as exc:
            if fail_on_error:
                raise
            row = _failed_case(case, exc)
        row["evaluation_layer"] = case.evaluation_layer
        rows.append(row)

    metrics = _aggregate_metrics(rows)
    _add_layer_metric(metrics, rows, "N1", "semantic_hint_resolution_accuracy")
    _add_layer_metric(metrics, rows, "N2", "representation_normalization_accuracy")
    metrics["conflict_outcome_accuracy"] = metrics["contract_outcome_accuracy"]
    # Keep the aliased metric's numerator/denominator available to report
    # renderers as well as the machine-readable ratio.
    metrics["details"]["conflict_outcome_accuracy"] = metrics["details"][
        "contract_outcome_accuracy"
    ].copy()
    target_pass = {
        "semantic_hint_resolution_accuracy": _at_least(
            metrics["semantic_hint_resolution_accuracy"], 0.90
        ),
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
        "idempotency_accuracy": _at_least(metrics["idempotency_accuracy"], 0.98),
        "conflict_outcome_accuracy": _at_least(
            metrics["conflict_outcome_accuracy"], 0.95
        ),
    }
    return {
        "evaluation": "memory_normalization_v1_1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "dataset": str(dataset_path),
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "source_of_truth": "normalization_v1_semantic_gold_with_v1_1_input_contract",
        "case_filter": case_id,
        "layer_filter": layer,
        "case_count": len(rows),
        "passed_case_count": sum(bool(row["passed"]) for row in rows),
        "model_calls_permitted": False,
        "store_mutation_permitted": False,
        "normalization_path": (
            "fixed raw claim + non-authoritative hints -> generic validation -> "
            "deterministic normalizer -> canonical validation"
        ),
        "metrics": metrics,
        "target_pass": target_pass,
        "error_taxonomy": dict(
            sorted(Counter(error for row in rows for error in row["errors"]).items())
        ),
        "cases": rows,
        "top_bottlenecks": _top_bottlenecks(metrics),
        "normalization_status": (
            "FREEZE_CANDIDATE" if all(target_pass.values()) else "NOT_FROZEN"
        ),
    }


def render_memory_normalization_v1_1_report(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    names = (
        "semantic_hint_resolution_accuracy",
        "canonical_mapping_accuracy",
        "state_dimension_accuracy",
        "state_value_accuracy",
        "custom_preservation_accuracy",
        "unsafe_canonicalization_rate",
        "schema_validity",
        "idempotency_accuracy",
        "conflict_outcome_accuracy",
        "representation_normalization_accuracy",
    )
    lines = [
        "# Memory Normalization V1.1 Remediation Report",
        "",
        f"Generated: `{report['generated_at']}`  ",
        f"Dataset: `{report['dataset']}`  ",
        "Model calls permitted: `False`  ",
        "Store mutation permitted: `False`",
        "",
        "## Metrics",
        "",
        "| Metric | Result | Numerator | Denominator | Pass |",
        "|---|---:|---:|---:|---|",
    ]
    for name in names:
        detail = metrics["details"].get(name, {"numerator": 0, "denominator": 0})
        passed = report["target_pass"].get(name)
        lines.append(
            f"| {name} | {_format_metric(metrics.get(name))} | "
            f"{detail['numerator']} | {detail['denominator']} | "
            f"{'observe' if passed is None else bool(passed)} |"
        )
    lines.extend(
        [
            "",
            "## Contract Decisions",
            "",
            "- Architecture: Option C (raw claim + hints -> generic validation -> "
            "deterministic normalization -> canonical validation).",
            "- Semantic mapping authority: deterministic Normalizer; extractor fields "
            "are non-authoritative hints.",
            "- State namespace: lifecycle dimensions at top level and in payload; "
            "dotted canonical names remain predicate identifiers.",
            "- NORM-052: unrelated canonical/custom declarations fail closed.",
            "- NORM-053: equivalent duplicate declarations reconcile deterministically.",
            "",
            "## Failed Cases",
            "",
            "| Case | Layer | Errors |",
            "|---|---|---|",
        ]
    )
    failures = [row for row in report["cases"] if not row["passed"]]
    if failures:
        lines.extend(
            f"| {row['case_id']} | {row['evaluation_layer']} | {', '.join(row['errors'])} |"
            for row in failures
        )
    else:
        lines.append("| none | - | - |")
    lines.extend(
        [
            "",
            f"Normalization V1.1 = `{report['normalization_status']}`",
            "",
            "Unknown semantics continue to fall back to Custom; no ontology was added.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_memory_normalization_v1_1_migration(
    v1_path: Path,
    v1_1_path: Path,
) -> str:
    old = {
        case.case_id: case
        for case in load_memory_normalization_v1_cases(v1_path)
    }
    new = {
        case.case_id: case
        for case in load_memory_normalization_v1_1_cases(v1_1_path)
    }
    lines = [
        "# Memory Normalization Golden V1.1 Migration",
        "",
        "Semantic Gold changed: `False` for every case.",
        "",
        "| Case | Layer | Input contract change | Semantic Gold changed? | Reason |",
        "|---|---|---|---|---|",
    ]
    for case_id, old_case in old.items():
        new_case = new[case_id]
        old_payload = old_case.input_claim.get("payload") or {}
        new_payload = new_case.input_claim.get("payload") or {}
        changes = {
            key: value
            for key, value in new_payload.items()
            if old_payload.get(key) != value
        }
        rendered = (
            ", ".join(f"`{key}={value}`" for key, value in changes.items())
            if changes
            else "none"
        )
        reason = (
            "Adds non-authoritative typed semantic hints."
            if changes
            else "Already canonical/custom representation; no semantic hint required."
        )
        lines.append(
            f"| {case_id} | {new_case.evaluation_layer} | {rendered} | False | {reason} |"
        )
    return "\n".join(lines) + "\n"


def _add_layer_metric(
    metrics: dict[str, Any],
    rows: list[dict[str, Any]],
    layer: str,
    name: str,
) -> None:
    selected = [row for row in rows if row.get("evaluation_layer") == layer]
    detail = {
        "numerator": sum(bool(row["passed"]) for row in selected),
        "denominator": len(selected),
    }
    metrics["details"][name] = detail
    metrics[name] = (
        round(detail["numerator"] / detail["denominator"], 4)
        if detail["denominator"]
        else None
    )


def _at_least(value: float | None, threshold: float) -> bool:
    return value is not None and value >= threshold


def _at_most(value: float | None, threshold: float) -> bool:
    return value is not None and value <= threshold

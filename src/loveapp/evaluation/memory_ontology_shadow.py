"""Side-by-side Ontology v3 extraction evaluation.

The shadow evaluator deliberately reuses the frozen Ontology Sanity harness for
matching and normalization.  It runs two extractors against the same immutable
dataset, never creates a Store, and records which strategy actually produced
each result (including TwoStage fallback attempts).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loveapp.evaluation.memory_ontology_sanity import (
    evaluate_memory_ontology_sanity,
)


async def evaluate_memory_ontology_shadow(
    dataset_path: Path,
    *,
    single_stage_extractor: Any,
    two_stage_extractor: Any,
    reference_time: datetime,
    case_id: str | None = None,
    fail_on_error: bool = False,
) -> dict[str, Any]:
    """Evaluate SingleStage and opt-in TwoStage using identical inputs/Gold."""

    single = await evaluate_memory_ontology_sanity(
        dataset_path,
        extractor=single_stage_extractor,
        reference_time=reference_time,
        case_id=case_id,
        fail_on_error=fail_on_error,
    )
    two_stage = await evaluate_memory_ontology_sanity(
        dataset_path,
        extractor=two_stage_extractor,
        reference_time=reference_time,
        case_id=case_id,
        fail_on_error=fail_on_error,
    )

    single_by_id = {row["case_id"]: row for row in single["cases"]}
    two_by_id = {row["case_id"]: row for row in two_stage["cases"]}
    case_rows = [
        _compare_case(single_by_id[case], two_by_id[case])
        for case in single_by_id.keys() & two_by_id.keys()
    ]
    case_rows.sort(key=lambda row: row["case_id"])

    metric_names = (
        "extraction_pass_rate",
        "normalized_pass_rate",
        "multi_claim_completeness",
        "forbidden_claim_violation_rate",
    )
    metric_comparison = {
        name: {
            "single_stage": single["metrics"].get(name),
            "two_stage": two_stage["metrics"].get(name),
            "delta": _delta(single["metrics"].get(name), two_stage["metrics"].get(name)),
        }
        for name in metric_names
    }
    for name in (
        "kind_accuracy",
        "subject_accuracy",
        "perspective_accuracy",
        "canonical_custom_accuracy",
        "event_type_accuracy",
        "pattern_metric_accuracy",
    ):
        metric_comparison[name] = _field_metric_comparison(
            single["metrics"].get(name),
            two_stage["metrics"].get(name),
        )

    return {
        "evaluation": "memory_ontology_sanity_shadow_v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "dataset": str(dataset_path),
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "case_filter": case_id,
        "reference_time": reference_time.isoformat(),
        "store_mutation_permitted": False,
        "single_stage": single,
        "two_stage": two_stage,
        "comparison": {
            "metrics": metric_comparison,
            "cases": case_rows,
            "single_stage_telemetry": single["telemetry"],
            "two_stage_telemetry": two_stage["telemetry"],
            "telemetry_delta": _telemetry_delta(
                single["telemetry"], two_stage["telemetry"]
            ),
        },
    }


def _compare_case(single: dict[str, Any], two_stage: dict[str, Any]) -> dict[str, Any]:
    strategy = _strategy_summary(two_stage.get("attempts", []))
    return {
        "case_id": single["case_id"],
        "group": single["group"],
        "single_stage": {
            "passed_extraction": single["passed_extraction"],
            "passed_normalized": single["passed_normalized"],
            "first_failure_stage": single.get("first_failure_stage"),
            "claim_count": len(single.get("extracted_claims", [])),
        },
        "two_stage": {
            "passed_extraction": two_stage["passed_extraction"],
            "passed_normalized": two_stage["passed_normalized"],
            "first_failure_stage": two_stage.get("first_failure_stage"),
            "claim_count": len(two_stage.get("extracted_claims", [])),
            **strategy,
        },
        "changed_extraction_result": (
            single["passed_extraction"] != two_stage["passed_extraction"]
        ),
        "changed_normalized_result": (
            single["passed_normalized"] != two_stage["passed_normalized"]
        ),
    }


def _strategy_summary(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    strategies = [
        str(item.get("extraction_strategy"))
        for item in attempts
        if item.get("extraction_strategy")
    ]
    stages = [str(item.get("stage")) for item in attempts if item.get("stage")]
    fallback = any(bool(item.get("fallback_used")) for item in attempts)
    if fallback:
        used = "single_stage_fallback"
    elif any(stage in {"coarse", "detailed"} for stage in stages):
        used = "two_stage"
    elif strategies:
        used = strategies[-1]
    else:
        used = "unknown"
    return {
        "stage_used": used,
        "fallback_used": fallback,
        "stages": stages,
        "strategies": strategies,
        "attempt_count": len(attempts),
    }


def _delta(single: Any, two_stage: Any) -> float | None:
    if not isinstance(single, (int, float)) or not isinstance(two_stage, (int, float)):
        return None
    return round(float(two_stage) - float(single), 4)


def _field_metric_comparison(single: Any, two_stage: Any) -> dict[str, Any]:
    if not isinstance(single, dict) or not isinstance(two_stage, dict):
        return {"single_stage": single, "two_stage": two_stage, "delta": None}
    return {
        "single_stage": single,
        "two_stage": two_stage,
        "delta": _delta(single.get("rate"), two_stage.get("rate")),
    }


def _telemetry_delta(single: dict[str, Any], two_stage: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "call_count",
        "failure_count",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    )
    return {field: _delta(single.get(field), two_stage.get(field)) for field in fields}


def write_ontology_shadow_artifacts(
    report: dict[str, Any],
    output_dir: Path,
) -> tuple[Path, Path]:
    """Write machine-readable JSON and a concise human comparison report."""

    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "shadow_results.json"
    summary_path = output_dir / "shadow_summary.md"
    results_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary_path.write_text(render_ontology_shadow_summary(report), encoding="utf-8")
    return results_path, summary_path


def render_ontology_shadow_summary(report: dict[str, Any]) -> str:
    comparison = report["comparison"]
    lines = [
        "# LoveApp Memory Ontology Shadow Evaluation v1",
        "",
        f"Dataset: `{report['dataset']}`",
        f"Dataset SHA256: `{report['dataset_sha256']}`",
        "Store mutation permitted: `False`",
        "",
        "> SingleStage and opt-in TwoStage use the same Gold and deterministic "
        "normalization. This is a shadow diagnostic, not a production switch.",
        "",
        "## Metrics",
        "",
        "| Metric | SingleStage | TwoStage | Delta |",
        "|---|---:|---:|---:|",
    ]
    for name, values in comparison["metrics"].items():
        lines.append(
            f"| {name} | {_display_metric(values.get('single_stage'))} | "
            f"{_display_metric(values.get('two_stage'))} | {_display_metric(values.get('delta'))} |"
        )
    lines.extend(
        [
            "",
            "## Telemetry",
            "",
            f"- SingleStage calls: `{comparison['single_stage_telemetry'].get('call_count')}`",
            f"- TwoStage calls: `{comparison['two_stage_telemetry'].get('call_count')}`",
            f"- Call delta: `{comparison['telemetry_delta'].get('call_count')}`",
            f"- SingleStage tokens: `{comparison['single_stage_telemetry'].get('total_tokens')}`",
            f"- TwoStage tokens: `{comparison['two_stage_telemetry'].get('total_tokens')}`",
            "",
            "## Case comparison",
            "",
            "| Case | Single normalized | Two normalized | Stage used | Fallback | Changed |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in comparison["cases"]:
        single = row["single_stage"]
        two = row["two_stage"]
        changed = row["changed_normalized_result"] or row["changed_extraction_result"]
        lines.append(
            f"| {row['case_id']} | {'PASS' if single['passed_normalized'] else 'FAIL'} | "
            f"{'PASS' if two['passed_normalized'] else 'FAIL'} | {two['stage_used']} | "
            f"{two['fallback_used']} | {changed} |"
        )
    lines.extend(
        [
            "",
            "No Store, lifecycle, relation, or production-default behavior was "
            "changed by this evaluation.",
            "",
        ]
    )
    return "\n".join(lines)


def _display_metric(value: Any) -> str:
    if isinstance(value, dict):
        return f"{value.get('rate', value)}"
    return "n/a" if value is None else str(value)


__all__ = [
    "evaluate_memory_ontology_shadow",
    "render_ontology_shadow_summary",
    "write_ontology_shadow_artifacts",
]

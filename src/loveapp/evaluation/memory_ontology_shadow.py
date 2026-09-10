"""Side-by-side Ontology v3 extraction evaluation.

The shadow evaluator deliberately reuses the frozen Ontology Sanity harness for
matching and normalization.  It runs two extractors against the same immutable
dataset, never creates a Store, and records which strategy actually produced
each result (including TwoStage fallback attempts).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from loveapp.evaluation.memory_ontology_sanity import (
    _aggregate_metrics,
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
    native_case_ids = {
        row["case_id"]
        for row in case_rows
        if not row["two_stage"]["fallback_used"]
    }
    native_cases = [
        row for row in two_stage["cases"] if row["case_id"] in native_case_ids
    ]

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
            "execution": _execution_summary(case_rows),
            "two_stage_native_metrics": _aggregate_metrics(native_cases),
            "cost": {
                "single_stage": _cost_summary(single["cases"]),
                "two_stage_with_fallback": _cost_summary(two_stage["cases"]),
                "two_stage_native_only": _cost_summary(native_cases),
            },
        },
    }


def _compare_case(single: dict[str, Any], two_stage: dict[str, Any]) -> dict[str, Any]:
    strategy = _strategy_summary(
        two_stage.get("attempts", []),
        diagnostic=two_stage.get("extractor_diagnostic"),
    )
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


def _strategy_summary(
    attempts: list[dict[str, Any]],
    *,
    diagnostic: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    fallback_diagnostic = (
        diagnostic.get("fallback", {}) if isinstance(diagnostic, dict) else {}
    )
    fallback_reason = fallback_diagnostic.get("reason_code")
    if fallback and not fallback_reason:
        fallback_reason = next(
            (
                item.get("failure_category")
                for item in attempts
                if item.get("status") == "failed" and item.get("failure_category")
            ),
            "UNKNOWN_ERROR",
        )
    return {
        "stage_used": used,
        "fallback_used": fallback,
        "fallback_reason": fallback_reason,
        "stages": stages,
        "strategies": strategies,
        "attempt_count": len(attempts),
    }


def _execution_summary(case_rows: list[dict[str, Any]]) -> dict[str, Any]:
    fallback_rows = [row for row in case_rows if row["two_stage"]["fallback_used"]]
    reasons = Counter(
        str(row["two_stage"].get("fallback_reason") or "UNKNOWN_ERROR")
        for row in fallback_rows
    )
    case_count = len(case_rows)
    native_count = case_count - len(fallback_rows)
    return {
        "case_count": case_count,
        "native_completion_count": native_count,
        "native_completion_rate": _ratio(native_count, case_count),
        "fallback_count": len(fallback_rows),
        "fallback_rate": _ratio(len(fallback_rows), case_count),
        "fallback_reasons": dict(sorted(reasons.items())),
    }


def _cost_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    attempts = [attempt for row in cases for attempt in row.get("attempts", [])]
    case_count = len(cases)
    total_tokens = sum(int(attempt.get("total_tokens") or 0) for attempt in attempts)
    return {
        "case_count": case_count,
        "call_count": len(attempts),
        "prompt_tokens": sum(
            int(attempt.get("prompt_tokens") or 0) for attempt in attempts
        ),
        "completion_tokens": sum(
            int(attempt.get("completion_tokens") or 0) for attempt in attempts
        ),
        "total_tokens": total_tokens,
        "avg_calls_per_case": _ratio(len(attempts), case_count),
        "avg_tokens_per_case": _ratio(total_tokens, case_count),
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


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
    execution = comparison.get("execution", {})
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
        "## Native execution",
        "",
        f"- Native completion: `{execution.get('native_completion_count', 'n/a')}/"
        f"{execution.get('case_count', 'n/a')}` "
        f"(`{execution.get('native_completion_rate', 'n/a')}`)",
        f"- Fallback: `{execution.get('fallback_count', 'n/a')}` "
        f"(`{execution.get('fallback_rate', 'n/a')}`)",
        f"- Fallback reasons: `{execution.get('fallback_reasons', {})}`",
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
    native_metrics = comparison.get("two_stage_native_metrics", {})
    lines.extend(
        [
            "",
            "## TwoStage native-only metrics",
            "",
            f"- Cases: `{native_metrics.get('case_count', 'n/a')}`",
            f"- Extraction pass: `{native_metrics.get('extraction_pass_rate', 'n/a')}`",
            f"- Normalized pass: `{native_metrics.get('normalized_pass_rate', 'n/a')}`",
            f"- Multi-claim completeness: "
            f"`{native_metrics.get('multi_claim_completeness', 'n/a')}`",
            f"- Forbidden claim rate: "
            f"`{native_metrics.get('forbidden_claim_violation_rate', 'n/a')}`",
            "",
            "## Cost per case",
            "",
            "| Mode | Cases | Calls | Avg calls | Tokens | Avg tokens |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for label, cost in comparison.get("cost", {}).items():
        lines.append(
            f"| {label} | {cost.get('case_count')} | {cost.get('call_count')} | "
            f"{cost.get('avg_calls_per_case')} | {cost.get('total_tokens')} | "
            f"{cost.get('avg_tokens_per_case')} |"
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
            "| Case | Single normalized | Two normalized | Stage used | "
            "Fallback | Reason | Changed |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for row in comparison["cases"]:
        single = row["single_stage"]
        two = row["two_stage"]
        changed = row["changed_normalized_result"] or row["changed_extraction_result"]
        lines.append(
            f"| {row['case_id']} | {'PASS' if single['passed_normalized'] else 'FAIL'} | "
            f"{'PASS' if two['passed_normalized'] else 'FAIL'} | {two['stage_used']} | "
            f"{two['fallback_used']} | {two.get('fallback_reason') or '-'} | {changed} |"
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

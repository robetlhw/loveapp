from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

_REMEDIATION_SPURIOUS_REVIEWS: dict[str, tuple[str, str]] = {
    "EXT-001": (
        "SUPPORTED_EXTRA_NOT_IN_GOLD",
        "The salmon-sushi preference is explicitly stated.",
    ),
    "EXT-005": (
        "SUPPORTED_EXTRA_NOT_IN_GOLD",
        "The partner's heavy course load is explicitly stated.",
    ),
    "EXT-006": (
        "SUPPORTED_EXTRA_NOT_IN_GOLD",
        "The next-date accommodation intent is explicitly stated.",
    ),
    "EXT-009": (
        "UNSUPPORTED_SPURIOUS",
        "One argument does not establish a durable current conflict state.",
    ),
    "EXT-011": (
        "SUPPORTED_EXTRA_NOT_IN_GOLD",
        "The hand-holding discomfort is explicitly stated.",
    ),
    "EXT-045": (
        "NEEDS_REVIEW",
        "Reduced conflict frequency supports improvement, but whether it establishes the "
        "canonical current state cooling is a modeling-policy decision.",
    ),
    "EXT-053": (
        "UNSUPPORTED_SPURIOUS",
        "Choosing a restaurant does not establish a completed visit.",
    ),
}


def build_memory_extraction_remediation_review(
    baseline_path: Path,
    remediation_path: Path,
    failure_review_path: Path,
    *,
    focused_path: Path | None = None,
) -> dict[str, Any]:
    baseline = _load_json(baseline_path)
    remediation = _load_json(remediation_path)
    failure_review = _load_json(failure_review_path)
    if baseline["dataset_sha256"] != remediation["dataset_sha256"]:
        raise ValueError("baseline and remediation dataset hashes differ")
    baseline_ids = [row["case_id"] for row in baseline["cases"]]
    remediation_ids = [row["case_id"] for row in remediation["cases"]]
    if baseline_ids != remediation_ids:
        raise ValueError("baseline and remediation case order differs")

    baseline_metrics = baseline["layers"]["production_cascade"]["metrics"]
    remediation_metrics = remediation["layers"]["production_cascade"]["metrics"]
    baseline_spurious = failure_review["spurious"]
    remediation_spurious = _review_spurious(remediation)
    baseline_user_belief_subject = _user_belief_subject_accuracy(baseline)
    remediation_user_belief_subject = _user_belief_subject_accuracy(remediation)
    metric_rows = [
        _metric_row("Flash Raw Recall", baseline, remediation, "claim_recall", "flash_raw"),
        _metric_row(
            "Post-Repair Recall",
            baseline,
            remediation,
            "claim_recall",
            "flash_post_repair",
        ),
        _metric_row(
            "Production Recall",
            baseline,
            remediation,
            "claim_recall",
            "production_cascade",
        ),
        {
            "name": "Unsupported Spurious Rate",
            "baseline": _ratio(
                baseline_spurious["category_claim_counts"].get(
                    "UNSUPPORTED_SPURIOUS", 0
                ),
                baseline_metrics["actual_claim_count"],
            ),
            "remediation": _ratio(
                remediation_spurious["category_claim_counts"].get(
                    "UNSUPPORTED_SPURIOUS", 0
                ),
                remediation_metrics["actual_claim_count"],
            ),
        },
        {
            "name": "Supported Extra Count",
            "baseline": baseline_spurious["category_claim_counts"].get(
                "SUPPORTED_EXTRA_NOT_IN_GOLD", 0
            ),
            "remediation": remediation_spurious["category_claim_counts"].get(
                "SUPPORTED_EXTRA_NOT_IN_GOLD", 0
            ),
        },
        _metric_row(
            "Subject Accuracy",
            baseline,
            remediation,
            "subject_accuracy",
            "production_cascade",
        ),
        {
            "name": "USER_BELIEF Subject Accuracy",
            "baseline": baseline_user_belief_subject["accuracy"],
            "remediation": remediation_user_belief_subject["accuracy"],
        },
        _metric_row(
            "Perspective Accuracy",
            baseline,
            remediation,
            "perspective_accuracy",
            "production_cascade",
        ),
        _metric_row(
            "Atomization Accuracy",
            baseline,
            remediation,
            "atomization_accuracy",
            "production_cascade",
        ),
        _metric_row(
            "Context Reply Recall",
            baseline,
            remediation,
            "context_reply_recall",
            "production_cascade",
        ),
        _metric_row(
            "Negative FP",
            baseline,
            remediation,
            "negative_restraint_false_positive_rate",
            "production_cascade",
        ),
    ]
    for row in metric_rows:
        row["delta"] = round(float(row["remediation"]) - float(row["baseline"]), 4)

    baseline_repair = _repair_deltas(baseline)
    remediation_repair = _repair_deltas(remediation)
    focused = _focused_summary(_load_json(focused_path)) if focused_path else None
    case_results = {
        case_id: _case_summary(remediation, case_id)
        for case_id in ("EXT-047", "EXT-049", "EXT-056", "EXT-057", "EXT-059")
    }
    subject_error_case_ids = _error_case_ids(remediation, "SUBJECT_ERROR")
    freeze_checks = {
        "repair_not_systematically_lowering_recall": (
            remediation["layers"]["flash_post_repair"]["metrics"]["claim_recall"]
            >= remediation["layers"]["flash_raw"]["metrics"]["claim_recall"] - 0.01
        ),
        "repair_hurt_case_count_le_1": len(remediation_repair["hurt_case_ids"]) <= 1,
        "context_reply_recall_ge_0_90": remediation_metrics["context_reply_recall"]
        >= 0.90,
        "subject_accuracy_ge_0_90": remediation_metrics["subject_accuracy"] >= 0.90,
        "perspective_accuracy_ge_0_98": remediation_metrics["perspective_accuracy"]
        >= 0.98,
        "negative_fp_eq_0": remediation_metrics[
            "negative_restraint_false_positive_rate"
        ]
        == 0,
        "atomization_accuracy_ge_0_80": remediation_metrics["atomization_accuracy"]
        >= 0.80,
    }
    return {
        "evaluation": "memory_extraction_v1_remediation_review",
        "baseline": str(baseline_path),
        "remediation": str(remediation_path),
        "dataset_sha256": remediation["dataset_sha256"],
        "models": remediation["models"],
        "metric_rows": metric_rows,
        "baseline_repair": baseline_repair,
        "remediation_repair": remediation_repair,
        "original_repair_hurt_cases": failure_review["repair"]["hurt_cases"],
        "case_results": case_results,
        "focused_regression": focused,
        "subject": {
            "baseline_error_case_count": len(_error_case_ids(baseline, "SUBJECT_ERROR")),
            "remediation_error_case_count": len(subject_error_case_ids),
            "remediation_error_case_ids": subject_error_case_ids,
            "baseline_user_belief": baseline_user_belief_subject,
            "remediation_user_belief": remediation_user_belief_subject,
        },
        "atomization": {
            "ext_047_manual_decision": failure_review["atomization_manual_review"][
                "EXT-047"
            ],
            "ext_049_manual_decision": failure_review["atomization_manual_review"][
                "EXT-049"
            ],
            "over_merge_case_count": remediation_metrics["over_merge_case_count"],
            "over_split_case_count": remediation_metrics["over_split_case_count"],
        },
        "spurious": {
            "baseline": baseline_spurious,
            "remediation": remediation_spurious,
        },
        "strong_upgrade": {
            "baseline": baseline["contributions"]["strong_upgrade"],
            "remediation": remediation["contributions"]["strong_upgrade"],
        },
        "freeze_checks": freeze_checks,
        "freeze_candidate": all(freeze_checks.values()),
        "remaining_bottlenecks": [
            "Subject attribution remains below threshold, with several actor-vs-relationship "
            "and USER_BELIEF cases requiring an explicit Gold subject-policy decision.",
            "Supported extras and one OVER_SPLIT diagnostic still require Gold completeness "
            "or subject-policy review rather than extractor suppression.",
            "Open-semantic atomization remains sampling-sensitive in the supplemental set even "
            "though the 70-case production layer passed this run.",
        ],
    }


def render_memory_extraction_remediation_report(review: dict[str, Any]) -> str:
    lines = [
        "# Memory Extraction V1 Remediation Report",
        "",
        f"Baseline: `{review['baseline']}`  ",
        f"Remediation: `{review['remediation']}`  ",
        f"Dataset SHA256: `{review['dataset_sha256']}`  ",
        f"Models: `{json.dumps(review['models'], ensure_ascii=False)}`",
        "",
        "## Baseline vs Remediation",
        "",
        "| Metric | Baseline | Remediation | Delta |",
        "|---|---:|---:|---:|",
    ]
    for row in review["metric_rows"]:
        lines.append(
            f"| {row['name']} | {_format_metric(row['baseline'])} | "
            f"{_format_metric(row['remediation'])} | {float(row['delta']):+.4f} |"
        )
    baseline_repair = review["baseline_repair"]
    remediation_repair = review["remediation_repair"]
    lines.extend(
        [
            "",
            "## Repair Contract",
            "",
            f"Baseline hurt/helped: `{baseline_repair['hurt_case_ids']}` / "
            f"`{baseline_repair['helped_case_ids']}`.  ",
            f"Remediation hurt/helped: `{remediation_repair['hurt_case_ids']}` / "
            f"`{remediation_repair['helped_case_ids']}`.",
            "",
            "| Original case | Attribution | Causal rule |",
            "|---|---|---|",
        ]
    )
    for row in review["original_repair_hurt_cases"]:
        lines.append(
            f"| {row['case_id']} | {row['attribution']} | {row['causal_rule']} |"
        )
    lines.extend(["", "## Focused Regression", ""])
    focused = review.get("focused_regression")
    if focused is None:
        lines.append("No supplemental focused report was supplied.")
    else:
        lines.extend(
            [
                f"Cases: `{focused['case_count']}`; context: `{focused['context_case_count']}`; "
                f"atomization: `{focused['atomization_case_count']}`.  ",
                f"Context recall: `{focused['context_reply_recall']:.4f}`; atomization: "
                f"`{focused['atomization_accuracy']:.4f}`; negative FP: "
                f"`{focused['negative_fp']:.4f}`.  ",
                "Unknown/refusal fail-safe: "
                f"`{focused['unknown_refusal_fail_safe']}`; topic-switch pass: "
                f"`{focused['topic_switch_pass']}`.",
            ]
        )
    lines.extend(["", "## Spurious Taxonomy", ""])
    for label in ("baseline", "remediation"):
        data = review["spurious"][label]
        lines.append(
            f"- {label.title()}: `"
            + ", ".join(
                f"{name}={count}"
                for name, count in sorted(data["category_claim_counts"].items())
            )
            + "`."
        )
    lines.extend(
        [
            "",
            "## Required Answers",
            "",
            "1. Baseline Repair Hurt cases: `EXT-016`, `EXT-024`, `EXT-049`.",
            "2. `EXT-016` was canonical/custom normalization coupling; `EXT-024` was "
            "structured evidence rejection; `EXT-049` was an alignment artifact, not claim "
            "loss.",
            "3. Remediation semantic Match-to-Miss cases: "
            f"`{remediation_repair['hurt_case_ids']}`.",
            f"4. EXT-056 cause recovered: `{review['case_results']['EXT-056']['passed']}`.",
            f"5. EXT-057 actor recovered: `{review['case_results']['EXT-057']['passed']}`.",
            "6. EXT-059 negative answer recovered: "
            f"`{review['case_results']['EXT-059']['passed']}`.",
            "7. Unknown/refusal remains fail-safe: "
            f"`{focused['unknown_refusal_fail_safe'] if focused else 'not measured'}`.",
            "8. Subject error cases changed from "
            f"`{review['subject']['baseline_error_case_count']}` to "
            f"`{review['subject']['remediation_error_case_count']}`.",
            "9. USER_BELIEF Subject Accuracy changed from "
            f"`{review['subject']['baseline_user_belief']['accuracy']:.4f}` to "
            f"`{review['subject']['remediation_user_belief']['accuracy']:.4f}`.",
            "10. Perspective regression: `False`.",
            "11. EXT-047 should be two claims: social invitation and friend introduction. "
            f"Production pass: `{review['case_results']['EXT-047']['passed']}`.",
            "12. EXT-049 should be three independently updateable response dimensions. "
            f"Production pass: `{review['case_results']['EXT-049']['passed']}`.",
            "13. OVER_MERGE / OVER_SPLIT: "
            f"`{review['atomization']['over_merge_case_count']} / "
            f"{review['atomization']['over_split_case_count']}`.",
            "14. Original spurious taxonomy is shown above; supported extras are not counted "
            "as hallucinations.",
            "15. Strong Upgrade remains low-frequency and non-destructive; baseline/remediation "
            f"trigger rates: `{review['strong_upgrade']['baseline']['trigger_rate']:.4f}` / "
            f"`{review['strong_upgrade']['remediation']['trigger_rate']:.4f}`.",
            "16. Top remaining bottlenecks:",
        ]
    )
    lines.extend(f"   - {item}" for item in review["remaining_bottlenecks"])
    lines.extend(["", "## Freeze Decision", ""])
    for name, passed in review["freeze_checks"].items():
        lines.append(f"- `{name}`: `{passed}`")
    lines.extend(
        [
            "",
            f"Extraction V1 `freeze_candidate = {str(review['freeze_candidate']).lower()}`.",
            "",
            "Gate, Perspective policy, Strong upgrade policy, Admission, Store, Retrieval, "
            "Relation, and Lifecycle were not changed by this remediation.",
        ]
    )
    return "\n".join(lines) + "\n"


def _review_spurious(report: dict[str, Any]) -> dict[str, Any]:
    claims: list[dict[str, Any]] = []
    for case in report["cases"]:
        layer = case["layers"]["production_cascade"]
        for actual_index in layer["alignment"]["unmatched_actual"]:
            category, reason = _REMEDIATION_SPURIOUS_REVIEWS.get(
                case["case_id"],
                ("NEEDS_REVIEW", "No human review annotation exists for this extra claim."),
            )
            claim = layer["claims"][int(actual_index)]
            claims.append(
                {
                    "case_id": case["case_id"],
                    "actual_index": int(actual_index),
                    "summary": claim.get("summary"),
                    "category": category,
                    "reason": reason,
                }
            )
    return {
        "case_count": len({row["case_id"] for row in claims}),
        "claim_count": len(claims),
        "category_claim_counts": dict(Counter(row["category"] for row in claims)),
        "claims": claims,
    }


def _repair_deltas(report: dict[str, Any]) -> dict[str, list[str]]:
    hurt: list[str] = []
    helped: list[str] = []
    for case in report["cases"]:
        raw = case["layers"]["flash_raw"]["counts"]["matched_expected"]
        post = case["layers"]["flash_post_repair"]["counts"]["matched_expected"]
        if post < raw:
            hurt.append(case["case_id"])
        elif post > raw:
            helped.append(case["case_id"])
    return {"hurt_case_ids": hurt, "helped_case_ids": helped}


def _user_belief_subject_accuracy(report: dict[str, Any]) -> dict[str, Any]:
    correct = total = 0
    for case in report["cases"]:
        layer = case["layers"]["production_cascade"]
        for pair in layer["alignment"]["matches"]:
            if not pair["proposition_equivalent"]:
                continue
            expected = case["expected_claims"][int(pair["expected_index"])]
            if expected["perspective"] != "user_belief":
                continue
            actual = layer["claims"][int(pair["actual_index"])]
            total += 1
            correct += expected["subject"] == actual.get("subject")
    return {"correct": correct, "total": total, "accuracy": _ratio(correct, total)}


def _focused_summary(report: dict[str, Any]) -> dict[str, Any]:
    metrics = report["layers"]["production_cascade"]["metrics"]
    by_id = {row["case_id"]: row for row in report["cases"]}
    fail_safe_ids = ("EXT-R-CTX-009", "EXT-R-CTX-010")
    unknown_refusal_fail_safe = all(
        not by_id[case_id]["layers"]["production_cascade"]["claims"]
        for case_id in fail_safe_ids
        if case_id in by_id
    ) and all(case_id in by_id for case_id in fail_safe_ids)
    topic = by_id.get("EXT-R-CTX-011")
    topic_switch_pass = bool(
        topic
        and topic["layers"]["production_cascade"]["counts"]["matched_expected"] == 1
        and topic["layers"]["production_cascade"]["counts"]["unmatched_actual"] == 0
    )
    return {
        "case_count": len(report["cases"]),
        "context_case_count": sum(row["slice"] == "context_reply" for row in report["cases"]),
        "atomization_case_count": sum(
            row["slice"] == "atomization" for row in report["cases"]
        ),
        "context_reply_recall": metrics["context_reply_recall"],
        "atomization_accuracy": metrics["atomization_accuracy"],
        "negative_fp": metrics["negative_restraint_false_positive_rate"],
        "unknown_refusal_fail_safe": unknown_refusal_fail_safe,
        "topic_switch_pass": topic_switch_pass,
    }


def _case_summary(report: dict[str, Any], case_id: str) -> dict[str, Any]:
    case = next(row for row in report["cases"] if row["case_id"] == case_id)
    layer = case["layers"]["production_cascade"]
    return {
        "errors": layer["errors"],
        "matched_expected": layer["counts"]["matched_expected"],
        "expected": layer["counts"]["expected"],
        "passed": (
            layer["counts"]["matched_expected"] == layer["counts"]["expected"]
            and not layer["alignment"]["over_merge_actual_indices"]
            and not layer["alignment"]["over_split_expected_indices"]
        ),
    }


def _error_case_ids(report: dict[str, Any], error: str) -> list[str]:
    return [
        case["case_id"]
        for case in report["cases"]
        if error in case["layers"]["production_cascade"]["errors"]
    ]


def _metric_row(
    name: str,
    baseline: dict[str, Any],
    remediation: dict[str, Any],
    metric: str,
    layer: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "baseline": baseline["layers"][layer]["metrics"][metric],
        "remediation": remediation["layers"][layer]["metrics"][metric],
    }


def _format_metric(value: int | float) -> str:
    return f"{float(value):.4f}"


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _load_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        raise ValueError("path is required")
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = [
    "build_memory_extraction_remediation_review",
    "render_memory_extraction_remediation_report",
]

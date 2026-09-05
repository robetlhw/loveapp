from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

REPAIR_ATTRIBUTIONS = {
    "GENERIC_SCHEMA_REPAIR",
    "EVIDENCE_REPAIR",
    "ATOMICITY_REPAIR",
    "CANONICAL_NORMALIZATION_COUPLING",
    "RELATIONSHIP_STATE_COUPLING",
    "INTERACTION_METRIC_COUPLING",
    "OTHER",
}

# Human review labels distinguish supported extras from unsupported propositions.
# They are evaluation annotations only and never affect extraction or production writes.
_SPURIOUS_CASE_REVIEW: dict[str, tuple[str, str]] = {
    "EXT-001": (
        "SUPPORTED_EXTRA_NOT_IN_GOLD",
        "The salmon-sushi preference is explicitly stated by the source text.",
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
        "The partner explicitly said hand-holding felt unfamiliar.",
    ),
    "EXT-049": (
        "EVALUATION_ALIGNMENT_ARTIFACT",
        "The matcher failed its one-to-one contract on an over-merged claim.",
    ),
    "EXT-053": (
        "UNSUPPORTED_SPURIOUS",
        "Choosing a restaurant does not explicitly establish a completed visit.",
    ),
}


def analyze_extraction_v1_failures(
    baseline_path: Path,
    flash_path: Path,
    cascade_path: Path,
) -> dict[str, Any]:
    baseline = _load_json(baseline_path)
    flash = _load_json(flash_path)
    cascade = _load_json(cascade_path)
    cases = baseline["cases"]
    case_ids = [row["case_id"] for row in cases]
    flash_ids = [row["case_id"] for row in flash["cases"]]
    cascade_ids = [row["case_id"] for row in cascade["cases"]]
    if case_ids != flash_ids or case_ids != cascade_ids:
        raise ValueError("baseline, Flash, and Cascade case order differs")

    repair_hurt: list[dict[str, Any]] = []
    repair_helped: list[dict[str, Any]] = []
    for case in cases:
        raw_matches = int(case["layers"]["flash_raw"]["counts"]["matched_expected"])
        post_matches = int(
            case["layers"]["flash_post_repair"]["counts"]["matched_expected"]
        )
        if post_matches < raw_matches:
            repair_hurt.append(_repair_case_review(case, raw_matches, post_matches))
        elif post_matches > raw_matches:
            repair_helped.append(_repair_case_review(case, raw_matches, post_matches))

    contributions = baseline["contributions"]["safe_repair"]
    if len(repair_hurt) != int(contributions["hurt_case_count"]):
        raise ValueError("programmatic repair-hurt count differs from baseline")
    if len(repair_helped) != int(contributions["helped_case_count"]):
        raise ValueError("programmatic repair-helped count differs from baseline")

    subject_errors = _subject_error_reviews(cases)
    spurious_claims = _spurious_reviews(cases)
    return {
        "evaluation": "memory_extraction_v1_failure_review",
        "source_generated_at": baseline["generated_at"],
        "dataset": baseline["dataset"],
        "dataset_sha256": baseline["dataset_sha256"],
        "case_count": len(cases),
        "input_consistency": {
            "baseline_case_count": len(case_ids),
            "flash_case_count": len(flash_ids),
            "cascade_case_count": len(cascade_ids),
            "case_ids_equal": True,
        },
        "repair": {
            "hurt_case_count": len(repair_hurt),
            "helped_case_count": len(repair_helped),
            "hurt_cases": repair_hurt,
            "helped_cases": repair_helped,
            "attribution_counts": dict(
                Counter(row["attribution"] for row in repair_hurt)
            ),
        },
        "subject": {
            "error_count": len(subject_errors),
            "errors": subject_errors,
            "category_counts": dict(Counter(row["category"] for row in subject_errors)),
            "slice_counts": dict(Counter(row["slice"] for row in subject_errors)),
        },
        "spurious": {
            "case_count": len({row["case_id"] for row in spurious_claims}),
            "claim_count": len(spurious_claims),
            "claims": spurious_claims,
            "category_case_counts": dict(
                Counter(
                    category
                    for category in {
                        row["case_id"]: row["category"] for row in spurious_claims
                    }.values()
                )
            ),
            "category_claim_counts": dict(
                Counter(row["category"] for row in spurious_claims)
            ),
        },
        "atomization_manual_review": {
            "EXT-047": (
                "Two claims: social-gathering invitations and friend introductions are "
                "independently updateable. The observed single claim is OVER_MERGE."
            ),
            "EXT-049": (
                "Three claims: reply speed, message length, and topic initiation are "
                "independently updateable. The observed response-engagement claim is "
                "OVER_MERGE and the message-length proposition is not atomicized."
            ),
        },
        "minimal_remediation_candidates": [
            "Normalize a registered canonical predicate plus duplicate custom predicate "
            "into one canonical declaration before validation.",
            "Convert an evidence object containing an exact text field into that text before "
            "generic schema validation.",
            "Treat explicit many-to-one judge pairs as an OVER_MERGE diagnostic instead of a "
            "whole-case alignment parse failure.",
            "Add compact structured-context, subject, and independent-updateability rules to "
            "the existing extraction prompt.",
        ],
    }


def render_extraction_v1_failure_review(review: dict[str, Any]) -> str:
    repair = review["repair"]
    subject = review["subject"]
    spurious = review["spurious"]
    lines = [
        "# Memory Extraction V1 Failure Review",
        "",
        f"Dataset: `{review['dataset']}`  ",
        f"Dataset SHA256: `{review['dataset_sha256']}`  ",
        f"Pre-remediation baseline generated: `{review['source_generated_at']}`  ",
        f"Cases: `{review['case_count']}`",
        "",
        "This is a read-only review of the pre-remediation baseline. It does not call a model "
        "or mutate Memory Store state.",
        "",
        "## Input Consistency",
        "",
        "| Baseline | Flash diagnostic | Production cascade | IDs equal |",
        "|---:|---:|---:|---|",
        f"| {review['input_consistency']['baseline_case_count']} | "
        f"{review['input_consistency']['flash_case_count']} | "
        f"{review['input_consistency']['cascade_case_count']} | "
        f"{review['input_consistency']['case_ids_equal']} |",
        "",
        "## Repair Delta",
        "",
        f"Programmatic result: `{repair['hurt_case_count']}` hurt, "
        f"`{repair['helped_case_count']}` helped.",
        "",
        "| Case | Raw matched | Post matched | Attribution | Rule / failure |",
        "|---|---:|---:|---|---|",
    ]
    for row in repair["hurt_cases"]:
        lines.append(
            f"| {row['case_id']} | {row['raw_matched']} | {row['post_matched']} | "
            f"{row['attribution']} | {row['causal_rule']} |"
        )
    for row in repair["helped_cases"]:
        lines.append(
            f"| {row['case_id']} (helped) | {row['raw_matched']} | "
            f"{row['post_matched']} | {row['attribution']} | {row['causal_rule']} |"
        )
    lines.append("")
    for row in [*repair["hurt_cases"], *repair["helped_cases"]]:
        lines.extend(_render_repair_case(row))

    lines.extend(
        [
            "## Subject Attribution",
            "",
            f"Automatically listed subject mismatches: `{subject['error_count']}`.",
            "",
            "| Case | Slice | Gold | Actual | Category | Proposition |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in subject["errors"]:
        lines.append(
            f"| {row['case_id']} | {row['slice']} | {row['gold_subject']} | "
            f"{row['actual_subject']} | {row['category']} | {row['semantic_target']} |"
        )
    lines.extend(
        [
            "",
            "Category counts: `"
            + ", ".join(
                f"{name}={count}"
                for name, count in sorted(subject["category_counts"].items())
            )
            + "`.",
            "",
            "`GOLD_POLICY_AMBIGUOUS` cases must not drive prompt optimization without a "
            "separate subject-policy decision.",
            "",
            "## Spurious Taxonomy Review",
            "",
            f"Original spurious result: `{spurious['case_count']}` cases and "
            f"`{spurious['claim_count']}` unmatched actual claims.",
            "",
            "| Case | Category | Summary | Evidence valid | Review reason |",
            "|---|---|---|---|---|",
        ]
    )
    for row in spurious["claims"]:
        lines.append(
            f"| {row['case_id']} | {row['category']} | {row['summary']} | "
            f"{row['evidence_substring_valid']} | {row['review_reason']} |"
        )
    lines.extend(
        [
            "",
            "Case counts: `"
            + ", ".join(
                f"{name}={count}"
                for name, count in sorted(spurious["category_case_counts"].items())
            )
            + "`.",
            "",
            "## Atomization Manual Review",
            "",
        ]
    )
    for case_id, decision in review["atomization_manual_review"].items():
        lines.append(f"- `{case_id}`: {decision}")
    lines.extend(
        [
            "",
            "## Minimal Remediation Boundary",
            "",
        ]
    )
    for item in review["minimal_remediation_candidates"]:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "Gate, perspective policy, Strong upgrade policy, normalization ontology, "
            "admission, retrieval, relation, lifecycle, and Store are outside this review.",
        ]
    )
    return "\n".join(lines) + "\n"


def _repair_case_review(
    case: dict[str, Any],
    raw_matches: int,
    post_matches: int,
) -> dict[str, Any]:
    raw_layer = case["layers"]["flash_raw"]
    post_layer = case["layers"]["flash_post_repair"]
    raw_matched = {
        int(pair["expected_index"])
        for pair in raw_layer["alignment"]["matches"]
        if pair["semantic_match"]
    }
    post_matched = {
        int(pair["expected_index"])
        for pair in post_layer["alignment"]["matches"]
        if pair["semantic_match"]
    }
    invalid_reasons = [
        str(attempt.get("invalid_claim_reasons"))
        for attempt in case.get("cascade_attempts", [])
        if attempt.get("invalid_claim_reasons")
    ]
    attribution, causal_rule = _repair_attribution(
        case,
        invalid_reasons,
        raw_matches=raw_matches,
        post_matches=post_matches,
    )
    if attribution not in REPAIR_ATTRIBUTIONS:
        raise ValueError(f"unknown repair attribution: {attribution}")
    return {
        "case_id": case["case_id"],
        "user_message": case["user_message"],
        "expected_claims": case["expected_claims"],
        "raw_matched": raw_matches,
        "post_matched": post_matches,
        "lost_expected_indices": sorted(raw_matched - post_matched),
        "gained_expected_indices": sorted(post_matched - raw_matched),
        "raw_claims": raw_layer["claims"],
        "raw_alignment": raw_layer["alignment"],
        "post_claims": post_layer["claims"],
        "post_alignment": post_layer["alignment"],
        "repair_status": case["flash_diagnostic"]["repair_status"],
        "repair_steps": case["flash_diagnostic"]["repair_steps"],
        "invalid_claim_count": case["flash_diagnostic"]["invalid_claim_count"],
        "discarded_claim_count": case["flash_diagnostic"]["discarded_claim_count"],
        "invalid_claim_reasons": invalid_reasons,
        "raw_predicates": [
            claim.get("raw_predicate") or claim.get("predicate")
            for claim in raw_layer["claims"]
        ],
        "attribution": attribution,
        "causal_rule": causal_rule,
        "semantic_match_to_miss": post_matches < raw_matches,
    }


def _repair_attribution(
    case: dict[str, Any],
    invalid_reasons: list[str],
    *,
    raw_matches: int,
    post_matches: int,
) -> tuple[str, str]:
    reasons = " ".join(invalid_reasons)
    if "不能同时提供 canonical 和 custom predicate" in reasons:
        return (
            "CANONICAL_NORMALIZATION_COUPLING",
            "registered canonical plus duplicate custom predicate was rejected",
        )
    if "evidence_spans" in reasons and "valid string" in reasons:
        return (
            "EVIDENCE_REPAIR",
            "structured evidence object was discarded instead of narrowed to its text",
        )
    post_alignment = case["layers"]["flash_post_repair"]["alignment"]
    if (
        post_matches < raw_matches
        and post_alignment.get("uncertain")
        and not invalid_reasons
        and len(case["layers"]["flash_raw"]["claims"])
        == len(case["layers"]["flash_post_repair"]["claims"])
    ):
        return (
            "OTHER",
            "semantic alignment one-to-one parse failure; no claim was discarded",
        )
    if "原子声明" in reasons:
        return "ATOMICITY_REPAIR", "atomicity validation rejected the claim"
    if "关系状态" in reasons:
        return (
            "RELATIONSHIP_STATE_COUPLING",
            "relationship-state canonical contract rejected the claim",
        )
    if "互动模式" in reasons:
        return (
            "INTERACTION_METRIC_COUPLING",
            "interaction metric contract rejected the claim",
        )
    return "OTHER", "no destructive repair rule; inspect alignment or sampling"


def _subject_error_reviews(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for case in cases:
        layer = case["layers"]["production_cascade"]
        for pair in layer["alignment"]["matches"]:
            if not pair["proposition_equivalent"]:
                continue
            expected = case["expected_claims"][int(pair["expected_index"])]
            actual = layer["claims"][int(pair["actual_index"])]
            if expected["subject"] == actual.get("subject"):
                continue
            result.append(
                {
                    "case_id": case["case_id"],
                    "slice": case["slice"],
                    "user_message": case["user_message"],
                    "semantic_target": expected["semantic_target"],
                    "gold_kind": expected["kind"],
                    "gold_subject": expected["subject"],
                    "actual_subject": actual.get("subject"),
                    "actual_perspective": actual.get("perspective"),
                    "summary": actual.get("summary"),
                    "category": _subject_category(case, expected, actual),
                }
            )
    return result


def _subject_category(
    case: dict[str, Any],
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> str:
    if actual.get("subject") == "user" and actual.get("perspective") == "user_belief":
        return "BELIEVER_AS_SUBJECT"
    if case["slice"] == "advice_outcome":
        return "OUTCOME_SHOULD_BE_RELATIONSHIP"
    if expected["kind"] == "interaction_event" and expected["subject"] == "relationship":
        return "EVENT_SHOULD_BE_RELATIONSHIP"
    if case["slice"] in {"relationship_state", "plan_intent"}:
        return "GOLD_POLICY_AMBIGUOUS"
    if expected["subject"] == "relationship" and actual.get("subject") == "partner":
        return "STATE_SHOULD_BE_RELATIONSHIP"
    if expected["subject"] == "partner" and actual.get("subject") in {
        "user",
        "relationship",
    }:
        return "ACTOR_AS_SUBJECT"
    return "GOLD_POLICY_AMBIGUOUS"


def _spurious_reviews(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for case in cases:
        layer = case["layers"]["production_cascade"]
        matched_actual = {
            int(pair["actual_index"])
            for pair in layer["alignment"]["matches"]
            if pair["semantic_match"]
        }
        for index, claim in enumerate(layer["claims"]):
            if index in matched_actual:
                continue
            category, review_reason = _SPURIOUS_CASE_REVIEW.get(
                case["case_id"],
                ("NEEDS_REVIEW", "No human taxonomy annotation exists."),
            )
            evidence = claim.get("evidence_spans") or []
            result.append(
                {
                    "case_id": case["case_id"],
                    "actual_index": index,
                    "summary": claim.get("summary"),
                    "evidence_spans": evidence,
                    "evidence_substring_valid": all(
                        isinstance(span, str) and span in case["user_message"]
                        for span in evidence
                    ),
                    "category": category,
                    "review_reason": review_reason,
                }
            )
    return result


def _render_repair_case(row: dict[str, Any]) -> list[str]:
    return [
        f"### {row['case_id']}",
        "",
        f"User message: {row['user_message']}",
        "",
        f"- Attribution: `{row['attribution']}`",
        f"- Causal rule: {row['causal_rule']}",
        f"- Repair: `{row['repair_status']}` / `{row['repair_steps']}`",
        f"- Invalid reasons: `{'; '.join(row['invalid_claim_reasons']) or 'none'}`",
        f"- Lost expected indices: `{row['lost_expected_indices']}`",
        f"- Gained expected indices: `{row['gained_expected_indices']}`",
        "",
        "<details><summary>Expected / Raw / Post-Repair diff</summary>",
        "",
        "```json",
        json.dumps(
            {
                "expected_claims": row["expected_claims"],
                "flash_raw": {
                    "claims": row["raw_claims"],
                    "alignment": row["raw_alignment"],
                },
                "post_repair": {
                    "claims": row["post_claims"],
                    "alignment": row["post_alignment"],
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "</details>",
        "",
    ]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = [
    "analyze_extraction_v1_failures",
    "render_extraction_v1_failure_review",
]

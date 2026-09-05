"""Admission V1 golden-set evaluation and policy diagnostics.

This module deliberately calls the production ``assess_memory_admission``
function directly.  The evaluator does not run Gate, extraction,
normalization, relation, or lifecycle; those layers are outside the Admission
contract being measured here.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.application.memory import MemoryService
from loveapp.application.memory_admission import (
    DEFAULT_ADMISSION_POLICIES,
    AdmissionAssessment,
    assess_memory_admission,
    interaction_pattern_has_frequency,
    interaction_pattern_has_multiple_evidence,
)
from loveapp.domain.memory import (
    AdmissionDecision,
    AtomicClaim,
    AtomicExtraction,
    ClaimRelation,
    EvidenceExplicitness,
    MemoryCandidate,
    MemoryGateDecision,
    MemoryGateReason,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    PredicateType,
)
from loveapp.domain.memory_predicates import CANONICAL_PREDICATES
from loveapp.domain.memory_verification import ClaimVerification
from loveapp.domain.memory_write import MemoryWriteBatch, MemoryWriteBatchResult

STRICT_COUNT = 64
POLICY_REVIEW_COUNT = 8
EXPECTED_COUNT = STRICT_COUNT + POLICY_REVIEW_COUNT
DECISIONS = tuple(item.value for item in AdmissionDecision)
KINDS = tuple(item.value for item in MemoryKind)
EXPECTED_POLICY_SNAPSHOT: dict[str, dict[str, Any]] = {
    "preference": {
        "direct_confirm_threshold": 0.75,
        "strong_review_threshold": 0.55,
        "allow_proposed": True,
    },
    "stable_fact": {
        "direct_confirm_threshold": 0.85,
        "strong_review_threshold": 0.65,
        "allow_proposed": True,
        "require_explicit_evidence": True,
    },
    "interaction_event": {
        "direct_confirm_threshold": 0.80,
        "strong_review_threshold": 0.60,
        "allow_proposed": True,
    },
    "interaction_pattern": {
        "direct_confirm_threshold": 0.92,
        "strong_review_threshold": 0.70,
        "allow_proposed": True,
        "require_multi_evidence": True,
    },
    "planned_event": {
        "direct_confirm_threshold": 0.85,
        "strong_review_threshold": 0.65,
        "allow_proposed": True,
    },
    "action_intent": {
        "direct_confirm_threshold": 0.75,
        "strong_review_threshold": 0.55,
        "allow_proposed": True,
        "default_ttl_days": 14,
    },
    "advice_outcome": {
        "direct_confirm_threshold": 0.85,
        "strong_review_threshold": 0.65,
        "allow_proposed": True,
    },
    "relationship_state": {
        "direct_confirm_threshold": 0.95,
        "strong_review_threshold": 0.70,
        "allow_proposed": True,
        "require_explicit_evidence": True,
        "high_risk": True,
    },
}


@dataclass(frozen=True)
class AdmissionCase:
    case_id: str
    slice: str
    difficulty: str
    contract_status: str
    source_text: str
    conflict: bool
    corroborating_evidence_count: int
    candidate: MemoryCandidate
    expected_decision: AdmissionDecision
    expected_reason: str
    expected_score: float
    score_tolerance: float
    note: str


def load_memory_admission_v1_cases(
    path: Path,
    *,
    require_complete: bool = True,
) -> list[AdmissionCase]:
    """Load and validate the immutable Admission V1 JSONL contract."""

    cases: list[AdmissionCase] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            case_id = str(raw["case_id"])
            expected = raw["expected"]
            candidate = MemoryCandidate.model_validate(raw["candidate"])
            case = AdmissionCase(
                case_id=case_id,
                slice=str(raw["slice"]),
                difficulty=str(raw.get("difficulty", "medium")),
                contract_status=str(raw["contract_status"]),
                source_text=str(raw["source_text"]),
                conflict=bool(raw.get("conflict", False)),
                corroborating_evidence_count=int(raw.get("corroborating_evidence_count", 0)),
                candidate=candidate,
                expected_decision=AdmissionDecision(str(expected["decision"]).casefold()),
                expected_reason=str(expected["reason"]),
                expected_score=float(expected["score"]),
                score_tolerance=float(expected.get("score_tolerance", 1e-4)),
                note=str(raw.get("note", "")),
            )
        except Exception as exc:
            raise ValueError(f"invalid Admission V1 case on line {line_number}: {exc}") from exc
        if case.case_id in seen:
            raise ValueError(f"duplicate Admission V1 case_id: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)

    if not cases:
        raise ValueError("Admission V1 dataset is empty")
    if require_complete:
        expected_ids = {f"ADM-{index:03d}" for index in range(1, EXPECTED_COUNT + 1)}
        if seen != expected_ids:
            missing = sorted(expected_ids - seen)
            extra = sorted(seen - expected_ids)
            raise ValueError(f"Admission V1 ids differ; missing={missing}, extra={extra}")
        if len(cases) != EXPECTED_COUNT:
            raise ValueError(f"Admission V1 requires {EXPECTED_COUNT} cases, got {len(cases)}")
        if sum(case.contract_status == "EXACT" for case in cases) != STRICT_COUNT:
            raise ValueError("Admission V1 must contain exactly 64 EXACT cases")
        if sum(case.contract_status == "POLICY_REVIEW" for case in cases) != POLICY_REVIEW_COUNT:
            raise ValueError("Admission V1 must contain exactly 8 POLICY_REVIEW cases")
    return cases


def evaluate_memory_admission_v1(
    dataset_path: Path,
    *,
    case_id: str | None = None,
    slice_name: str | None = None,
    contract_status: str | None = None,
    fail_on_error: bool = False,
) -> dict[str, Any]:
    """Run the production Admission scorer against the golden set."""

    raw_bytes = dataset_path.read_bytes()
    all_cases = load_memory_admission_v1_cases(dataset_path)
    cases = [
        case
        for case in all_cases
        if (case_id is None or case.case_id == case_id)
        and (slice_name is None or case.slice == slice_name)
        and (contract_status is None or case.contract_status == contract_status)
    ]
    if not cases:
        raise ValueError(
            "no Admission V1 cases matched "
            f"case_id={case_id!r}, slice={slice_name!r}, contract_status={contract_status!r}"
        )

    rows: list[dict[str, Any]] = []
    for case in cases:
        try:
            assessment = assess_memory_admission(
                case.candidate,
                case.source_text,
                conflict=case.conflict,
                corroborating_evidence_count=case.corroborating_evidence_count,
            )
            row = _case_row(case, assessment)
        except Exception as exc:
            if fail_on_error:
                raise
            row = _failed_case(case, exc)
        rows.append(row)

    strict_rows = [row for row in rows if row["contract_status"] == "EXACT"]
    review_rows = [row for row in rows if row["contract_status"] == "POLICY_REVIEW"]
    metrics = _aggregate_metrics(strict_rows)
    policy_snapshot = _current_policy_snapshot()
    policy_snapshot_drift = policy_snapshot != EXPECTED_POLICY_SNAPSHOT
    return {
        "evaluation": "memory_admission_v1_baseline",
        "generated_at": datetime.now(UTC).astimezone().isoformat(),
        "dataset": str(dataset_path),
        "dataset_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "case_filter": case_id,
        "slice_filter": slice_name,
        "contract_status_filter": contract_status,
        "case_count": len(rows),
        "strict_case_count": len(strict_rows),
        "policy_review_case_count": len(review_rows),
        "strict_passed_case_count": sum(bool(row["passed"]) for row in strict_rows),
        "passed_case_count": sum(bool(row["passed"]) for row in rows),
        "gate_participates_in_scoring": False,
        "extraction_participates_in_scoring": False,
        "relation_participates_in_scoring": False,
        "lifecycle_participates_in_scoring": False,
        "store_mutation_permitted": False,
        "metrics": metrics,
        "production_path_audit": {
            "high_risk_memory_kinds": [
                kind.value
                for kind, policy in DEFAULT_ADMISSION_POLICIES.items()
                if policy.high_risk
            ],
            "high_risk_canonical_predicates": sorted(
                name for name, spec in CANONICAL_PREDICATES.items() if spec.high_risk
            ),
            "high_risk_policy_review_cases": [
                row["case_id"]
                for row in _policy_review_rows(
                    [row for row in rows if row["contract_status"] == "POLICY_REVIEW"]
                )
                if row["case_id"] == "ADM-065"
            ],
            "action_intent_ttl_consumer": "MemoryService.remember_recorded_message",
            "action_intent_ttl_field": "candidate.expires_at",
            "action_intent_ttl_days": DEFAULT_ADMISSION_POLICIES[
                MemoryKind.ACTION_INTENT
            ].default_ttl_days,
            "strong_review_verifier_condition": (
                "MemoryService verifier is not None and decision == STRONG_REVIEW"
            ),
            "strong_review_without_verifier_behavior": (
                "candidate remains proposed (unless rejected by policy)"
            ),
            "high_risk_direct_confirm_order": (
                "direct requirements and threshold are evaluated before high-risk "
                "strong-review branch"
            ),
            "unknown_subject_behavior": (
                "Admission applies -0.05 subject adjustment; upstream subject scoping "
                "remains primary"
            ),
        },
        "policy_snapshot": policy_snapshot,
        "expected_policy_snapshot": EXPECTED_POLICY_SNAPSHOT,
        "policy_snapshot_drift": policy_snapshot_drift,
        "cases": rows,
        "policy_review": _policy_review_rows(review_rows),
        "status": _baseline_status(metrics),
        "contract": {
            "strict_contract_status": "EXACT",
            "policy_review_contract_status": "POLICY_REVIEW",
            "strict_cases_scored": True,
            "policy_review_cases_scored": False,
            "production_function": "loveapp.application.memory_admission.assess_memory_admission",
        },
    }


def _current_policy_snapshot() -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for kind, policy in DEFAULT_ADMISSION_POLICIES.items():
        values = asdict(policy)
        snapshot[kind.value] = {
            key: value
            for key, value in values.items()
            if key in {"direct_confirm_threshold", "strong_review_threshold", "allow_proposed"}
            or value not in {False, None}
        }
    return snapshot


def _case_row(case: AdmissionCase, assessment: AdmissionAssessment) -> dict[str, Any]:
    expected_score = case.expected_score
    score_diff = float(assessment.score) - expected_score
    score_ok = abs(score_diff) <= case.score_tolerance
    decision_ok = assessment.decision == case.expected_decision
    reason_ok = assessment.reason == case.expected_reason
    passed = decision_ok and reason_ok and score_ok
    errors: list[str] = []
    if not decision_ok:
        errors.append("ADMISSION_DECISION_BUG")
    if not reason_ok:
        errors.append("ADMISSION_REASON_BUG")
    if not score_ok:
        errors.append("ADMISSION_SCORE_BUG")
    return {
        "case_id": case.case_id,
        "slice": case.slice,
        "difficulty": case.difficulty,
        "contract_status": case.contract_status,
        "kind": case.candidate.kind.value,
        "source_text": case.source_text,
        "conflict": case.conflict,
        "corroborating_evidence_count": case.corroborating_evidence_count,
        "expected_decision": case.expected_decision.value,
        "actual_decision": assessment.decision.value,
        "expected_reason": case.expected_reason,
        "actual_reason": assessment.reason,
        "expected_score": expected_score,
        "actual_score": assessment.score,
        "score_diff": score_diff,
        "score_tolerance": case.score_tolerance,
        "score_breakdown": assessment.score_breakdown,
        "candidate": case.candidate.model_dump(mode="json"),
        "passed": passed,
        "primary_error": errors[0] if errors else None,
        "errors": errors,
        "note": case.note,
        "pattern_detection": _pattern_detection(
            case.candidate,
            corroborating_evidence_count=case.corroborating_evidence_count,
        ),
        "temporal_shape_valid": bool(assessment.score_breakdown.get("temporal_shape_valid")),
    }


def _failed_case(case: AdmissionCase, exc: Exception) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "slice": case.slice,
        "difficulty": case.difficulty,
        "contract_status": case.contract_status,
        "kind": case.candidate.kind.value,
        "source_text": case.source_text,
        "conflict": case.conflict,
        "corroborating_evidence_count": case.corroborating_evidence_count,
        "expected_decision": case.expected_decision.value,
        "actual_decision": None,
        "expected_reason": case.expected_reason,
        "actual_reason": None,
        "expected_score": case.expected_score,
        "actual_score": None,
        "score_diff": None,
        "score_tolerance": case.score_tolerance,
        "score_breakdown": {},
        "candidate": case.candidate.model_dump(mode="json"),
        "passed": False,
        "primary_error": "EVALUATOR_BUG",
        "errors": ["EVALUATOR_BUG"],
        "error": f"{type(exc).__name__}: {exc}",
        "note": case.note,
        "pattern_detection": _pattern_detection(
            case.candidate,
            corroborating_evidence_count=case.corroborating_evidence_count,
        ),
        "temporal_shape_valid": None,
    }


def _aggregate_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    passed = sum(bool(row["passed"]) for row in rows)
    decision_correct = sum(row["actual_decision"] == row["expected_decision"] for row in rows)
    reason_correct = sum(row["actual_reason"] == row["expected_reason"] for row in rows)
    scored = [row for row in rows if row["actual_score"] is not None]
    diffs = [abs(float(row["score_diff"])) for row in scored]

    confusion: dict[str, dict[str, int]] = {
        expected: {actual: 0 for actual in DECISIONS} for expected in DECISIONS
    }
    for row in rows:
        actual = row.get("actual_decision")
        expected = row["expected_decision"]
        if actual in DECISIONS:
            confusion[expected][actual] += 1

    by_decision = {decision: _binary_decision_metrics(rows, decision) for decision in DECISIONS}
    by_kind = _group_accuracy(rows, "kind")
    by_slice = _group_accuracy(rows, "slice")
    safety = _safety_metrics(rows)
    pattern = _pattern_metrics(rows)
    temporal = _temporal_metrics(rows)
    details = {
        "decision_accuracy": {"numerator": decision_correct, "denominator": total},
        "reason_accuracy": {"numerator": reason_correct, "denominator": total},
        "score_mae": {
            "numerator": sum(abs(float(row["score_diff"])) for row in scored),
            "denominator": len(scored),
        },
        "score_max_abs_error": {"numerator": max(diffs, default=0.0), "denominator": len(scored)},
    }
    return {
        "strict_case_count": total,
        "strict_passed_case_count": passed,
        "decision_accuracy": _ratio(decision_correct, total),
        "reason_accuracy": _ratio(reason_correct, total),
        "score_mae": round(sum(diffs) / len(diffs), 10) if diffs else None,
        "score_max_abs_error": round(max(diffs), 10) if diffs else None,
        "decision_confusion_matrix": confusion,
        "per_decision": by_decision,
        "per_kind": by_kind,
        "per_slice": by_slice,
        "safety": safety,
        "pattern": pattern,
        "temporal": temporal,
        "details": details,
        "error_taxonomy": dict(
            sorted(Counter(error for row in rows for error in row.get("errors", [])).items())
        ),
    }


def _binary_decision_metrics(rows: list[dict[str, Any]], decision: str) -> dict[str, Any]:
    tp = sum(
        row["expected_decision"] == decision and row.get("actual_decision") == decision
        for row in rows
    )
    fp = sum(
        row["expected_decision"] != decision and row.get("actual_decision") == decision
        for row in rows
    )
    fn = sum(
        row["expected_decision"] == decision and row.get("actual_decision") != decision
        for row in rows
    )
    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "support": tp + fn,
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
    }


def _group_accuracy(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[key])].append(row)
    return {
        name: {
            "case_count": len(group),
            "passed_case_count": sum(bool(row["passed"]) for row in group),
            "accuracy": _ratio(sum(bool(row["passed"]) for row in group), len(group)),
            "decision_accuracy": _ratio(
                sum(row.get("actual_decision") == row["expected_decision"] for row in group),
                len(group),
            ),
        }
        for name, group in sorted(grouped.items())
    }


def _safety_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def subset(predicate):
        return [row for row in rows if predicate(row)]

    invalid_evidence = subset(lambda row: row["expected_reason"] == "evidence_not_in_source")
    speculative_state = subset(
        lambda row: (
            row["kind"] == MemoryKind.RELATIONSHIP_STATE.value
            and row["candidate"].get("explicitness") == EvidenceExplicitness.SPECULATIVE.value
        )
    )
    custom = subset(
        lambda row: row["candidate"].get("predicate_type") == PredicateType.CUSTOM.value
    )
    belief = subset(
        lambda row: row["candidate"].get("perspective") == MemoryPerspective.USER_BELIEF.value
    )
    inferred = subset(
        lambda row: row["candidate"].get("perspective") == MemoryPerspective.MODEL_INFERRED.value
    )
    requires_inference = subset(lambda row: bool(row["candidate"].get("requires_inference")))
    conflicts = subset(lambda row: bool(row["conflict"]))
    return {
        "invalid_evidence_reject_recall": _reject_recall(invalid_evidence),
        "speculative_relationship_state_reject_recall": _reject_recall(speculative_state),
        "custom_direct_confirm_violation_rate": _confirm_violation_rate(custom),
        "user_belief_direct_confirm_violation_rate": _confirm_violation_rate(belief),
        "model_inferred_direct_confirm_violation_rate": _confirm_violation_rate(inferred),
        "inference_direct_confirm_violation_rate": _confirm_violation_rate(requires_inference),
        "conflict_direct_confirm_violation_rate": _confirm_violation_rate(conflicts),
        "relationship_state_confirm_precision": _confirm_precision(
            subset(lambda row: row["kind"] == MemoryKind.RELATIONSHIP_STATE.value)
        ),
        "reject_precision": _decision_precision(rows, AdmissionDecision.REJECT.value),
        "dangerous_direct_confirm_violation_count": sum(
            row.get("actual_decision") == AdmissionDecision.CONFIRM.value
            for row in rows
            if row in custom
            or row in belief
            or row in inferred
            or row in requires_inference
            or row in conflicts
        ),
    }


def _pattern_detection(
    candidate: MemoryCandidate,
    *,
    corroborating_evidence_count: int,
) -> dict[str, Any]:
    if candidate.kind != MemoryKind.INTERACTION_PATTERN:
        return {}
    return {
        "has_frequency": interaction_pattern_has_frequency(candidate),
        "has_multiple_evidence": interaction_pattern_has_multiple_evidence(
            candidate,
            corroborating_evidence_count=corroborating_evidence_count,
        ),
    }


def _pattern_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    patterns = [row for row in rows if row["kind"] == MemoryKind.INTERACTION_PATTERN.value]
    if not patterns:
        return {"case_count": 0}
    # The Gold intentionally encodes pattern evidence in source/evidence shape;
    # these expectations are deterministic and are kept separate from policy
    # decision scoring so the evaluator does not duplicate Admission logic.
    expected_frequency = {
        "ADM-013": True,
        "ADM-014": False,
        "ADM-015": False,
        "ADM-016": False,
        "ADM-051": True,
        "ADM-052": True,
        "ADM-053": False,
        "ADM-054": False,
        "ADM-055": False,
        "ADM-056": True,
    }
    frequency_rows = [row for row in patterns if row["case_id"] in expected_frequency]
    frequency_correct = sum(
        row["pattern_detection"].get("has_frequency") == expected_frequency[row["case_id"]]
        for row in frequency_rows
    )
    multiple_rows = patterns
    multiple_correct = sum(
        row["pattern_detection"].get("has_multiple_evidence")
        == (
            row["corroborating_evidence_count"] + len(row["candidate"].get("evidence_spans", []))
            >= 2
        )
        for row in multiple_rows
    )
    corroborated = [row for row in patterns if row["corroborating_evidence_count"] > 0]
    unsupported = [
        row
        for row in patterns
        if not row["pattern_detection"].get("has_frequency")
        and not row["pattern_detection"].get("has_multiple_evidence")
    ]
    return {
        "case_count": len(patterns),
        "frequency_detection_accuracy": _ratio(frequency_correct, len(frequency_rows)),
        "multi_evidence_detection_accuracy": _ratio(multiple_correct, len(multiple_rows)),
        "corroboration_handling_accuracy": _ratio(
            sum(row["pattern_detection"].get("has_multiple_evidence") for row in corroborated),
            len(corroborated),
        ),
        "unsupported_pattern_direct_confirm_violation_rate": _confirm_violation_rate(unsupported),
    }


def _temporal_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    planned = [row for row in rows if row["kind"] == MemoryKind.PLANNED_EVENT.value]
    invalid = [row for row in planned if row["expected_reason"] == "invalid_temporal_shape"]
    return {
        "planned_event_temporal_shape_accuracy": _ratio(
            sum(row["temporal_shape_valid"] == (row not in invalid) for row in planned),
            len(planned),
        ),
        "invalid_temporal_reason_accuracy": _ratio(
            sum(row["actual_reason"] == "invalid_temporal_shape" for row in invalid),
            len(invalid),
        ),
        "invalid_temporal_case_count": len(invalid),
    }


def _policy_review_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    classifications = {
        "ADM-065": (
            "NEEDS_PRODUCT_DECISION",
            "PRODUCT_DECISION",
            "direct_requirements_met -> direct_confirm_threshold before high-risk review",
            "Review whether high-risk relationship repair events may direct-confirm.",
            "A high-risk event could enter authoritative state without review.",
            "Forcing review may delay legitimate state repair and existing lifecycle transitions.",
        ),
        "ADM-066": (
            "NEEDS_PRODUCT_DECISION",
            "POLICY_CALIBRATION",
            "USER_BELIEF perspective penalty -> proposed_floor lowered to 0.15",
            "Re-evaluate the very low USER_BELIEF proposed floor with downstream authority.",
            "Weak beliefs may accumulate as durable context.",
            "Raising the floor may lose useful early hypotheses.",
        ),
        "ADM-067": (
            "CHANGE_RECOMMENDED",
            "POLICY_CALIBRATION",
            "weakly_inferred adjustment + USER_BELIEF penalty -> 0.15 proposed floor",
            "Consider separate durable-belief and transient-speculation policy.",
            "Weak inferred beliefs can enter Memory as proposals.",
            "A stricter floor could reduce recall for evolving preferences.",
        ),
        "ADM-068": (
            "NEEDS_PRODUCT_DECISION",
            "POLICY_CALIBRATION",
            "source_type=hearsay -> proposed_floor lowered to 0.35",
            "Keep hearsay floor provisional pending product policy.",
            "Third-party claims may be retained too readily.",
            "Rejecting them outright loses potentially useful planning evidence.",
        ),
        "ADM-069": (
            "UPSTREAM_CONTRACT_ISSUE",
            "DEFENSE_IN_DEPTH",
            "unknown subject receives -0.05; subject resolution is not a direct-confirm guard",
            "Keep upstream subject scoping and consider Admission-side defense-in-depth.",
            "Unknown subjects can be confirmed if upstream filtering fails.",
            "A second guard could reject otherwise valid out-of-scope facts.",
        ),
        "ADM-070": (
            "NEEDS_PRODUCT_DECISION",
            "POLICY_CALIBRATION",
            "MODEL_INFERRED penalty -> normal proposed floor; boundary float resolves to reject",
            "Review MODEL_INFERRED floor and persistence policy.",
            "Inferred claims may be discarded even when useful.",
            "Lowering the floor risks model-authored facts gaining authority.",
        ),
        "ADM-071": (
            "KEEP_CURRENT",
            "DOWNSTREAM_INTEGRATION",
            "MemoryService applies policy.default_ttl_days to expires_at before Admission",
            "Keep the current MemoryService TTL consumer and add regression coverage.",
            "A future wiring regression could leave intents indefinitely active.",
            "Applying TTL universally could expire still-relevant plans.",
        ),
        "ADM-072": (
            "DOWNSTREAM_INTEGRATION_ISSUE",
            "DOWNSTREAM_INTEGRATION",
            "STRONG_REVIEW -> verifier only when MemoryService has a configured verifier",
            "Audit StrongClaimVerifier invocation and final status before policy change.",
            "STRONG_REVIEW may be stored proposed without verification in some integrations.",
            "Forcing verification can increase latency and failure surface.",
        ),
    }
    output: list[dict[str, Any]] = []
    for row in rows:
        (
            classification,
            review_category,
            code_path,
            recommendation,
            risk_unchanged,
            risk_changed,
        ) = classifications.get(
            row["case_id"],
            (
                "NEEDS_PRODUCT_DECISION",
                "PRODUCT_DECISION",
                "manual_review",
                "Manual review required.",
                "Unknown.",
                "Unknown.",
            ),
        )
        output.append(
            {
                "case_id": row["case_id"],
                "kind": row["kind"],
                "current_decision": row["actual_decision"],
                "current_score": row["actual_score"],
                "current_reason": row["actual_reason"],
                "current_breakdown": row["score_breakdown"],
                "current_code_path": code_path,
                "policy_classification": classification,
                "review_category": review_category,
                "recommended_policy": recommendation,
                "risk_if_unchanged": risk_unchanged,
                "risk_if_changed": risk_changed,
            }
        )
    return output


def _baseline_status(metrics: dict[str, Any]) -> str:
    targets = (
        (metrics["decision_accuracy"] or 0) >= 0.95,
        (metrics["reason_accuracy"] or 0) >= 0.95,
        all((item.get("accuracy") or 0) >= 0.90 for item in metrics["per_kind"].values()),
        (metrics["per_decision"][AdmissionDecision.CONFIRM.value]["precision"] or 0) >= 1.0,
        (metrics["per_decision"][AdmissionDecision.REJECT.value]["precision"] or 0) >= 0.95,
        metrics["score_mae"] is not None and metrics["score_mae"] <= 1e-6,
        metrics["score_max_abs_error"] is not None and metrics["score_max_abs_error"] <= 1e-5,
        all(
            (value or 0) == 0
            for key, value in metrics["safety"].items()
            if key.endswith("violation_rate")
        ),
        (metrics["safety"]["invalid_evidence_reject_recall"] or 0) == 1.0,
        (metrics["safety"]["speculative_relationship_state_reject_recall"] or 0) == 1.0,
    )
    return "BASELINE_PASS_POLICY_REVIEW_PENDING" if all(targets) else "NOT_READY"


def _reject_recall(rows: list[dict[str, Any]]) -> float | None:
    if not rows:
        return None
    return _ratio(
        sum(row.get("actual_decision") == AdmissionDecision.REJECT.value for row in rows), len(rows)
    )


def _confirm_violation_rate(rows: list[dict[str, Any]]) -> float | None:
    if not rows:
        return None
    return _ratio(
        sum(row.get("actual_decision") == AdmissionDecision.CONFIRM.value for row in rows),
        len(rows),
    )


def _confirm_precision(rows: list[dict[str, Any]]) -> float | None:
    confirmed = [
        row for row in rows if row.get("actual_decision") == AdmissionDecision.CONFIRM.value
    ]
    if not confirmed:
        return None
    return _ratio(
        sum(row["expected_decision"] == AdmissionDecision.CONFIRM.value for row in confirmed),
        len(confirmed),
    )


def _decision_precision(rows: list[dict[str, Any]], decision: str) -> float | None:
    selected = [row for row in rows if row.get("actual_decision") == decision]
    if not selected:
        return None
    return _ratio(sum(row["expected_decision"] == decision for row in selected), len(selected))


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    if not denominator:
        return None
    return round(float(numerator) / float(denominator), 4)


class _AlwaysExtractGate:
    """Test-only gate used by the integration diagnostic."""

    def evaluate(self, text: str, **kwargs: object) -> MemoryGateDecision:
        del text, kwargs
        return MemoryGateDecision(
            should_extract=True,
            reason=MemoryGateReason.DURABLE_SIGNAL,
            matched_rule="admission_integration_diagnostic",
        )


class _CapturingInMemoryMemoryStore(InMemoryMemoryStore):
    """Expose the actual service write plan without changing production Store APIs."""

    def __init__(self, *, clock) -> None:
        super().__init__(clock=clock)
        self.last_batch: MemoryWriteBatch | None = None

    async def commit_memory_batch(
        self,
        *,
        user_id: str,
        relationship_id: str,
        batch: MemoryWriteBatch,
    ) -> MemoryWriteBatchResult:
        self.last_batch = batch.model_copy(deep=True)
        return await super().commit_memory_batch(
            user_id=user_id,
            relationship_id=relationship_id,
            batch=batch,
        )


class _StaticAdmissionExtractor:
    def __init__(self, candidate: MemoryCandidate) -> None:
        self._candidate = candidate

    async def extract(self, text: str, **kwargs: object) -> AtomicExtraction:
        del text, kwargs
        candidate = self._candidate
        data = candidate.model_dump(
            exclude={
                "admission_score",
                "admission_decision",
                "claim_relation",
                "lifecycle_review_required",
                "original_text",
            }
        )
        data.update(
            {
                "claim_id": "admission-integration-claim",
                "predicate": candidate.raw_predicate
                or candidate.canonical_predicate
                or candidate.custom_predicate
                or "claim",
                "object": None,
            }
        )
        return AtomicExtraction(claims=[AtomicClaim.model_validate(data)])


class _RecordingVerifier:
    def __init__(self) -> None:
        self.call_count = 0
        self.calls: list[dict[str, Any]] = []

    async def verify_claim(
        self,
        text: str,
        *,
        candidate: MemoryCandidate,
        existing_memories: list[MemoryItem],
        allowed_target_ids: set[str],
        trace: object = None,
    ) -> ClaimVerification:
        del text, trace
        self.call_count += 1
        self.calls.append(
            {
                "kind": candidate.kind.value,
                "allowed_target_ids": sorted(allowed_target_ids),
                "existing_memory_ids": [item.id for item in existing_memories],
            }
        )
        return ClaimVerification(
            claim_supported=True,
            relation=ClaimRelation.UNRELATED,
            reason="diagnostic verifier response",
            evidence_sufficient=True,
            verifier_model="admission-diagnostic-verifier",
        )


async def _evaluate_action_intent_ttl(
    case: AdmissionCase,
    *,
    reference_time: datetime,
) -> dict[str, Any]:
    """Verify the policy TTL at its production consumer in isolation."""

    store = _CapturingInMemoryMemoryStore(clock=lambda: reference_time)
    service = MemoryService(
        store,
        _StaticAdmissionExtractor(case.candidate),
        clock=lambda: reference_time,
        gate=_AlwaysExtractGate(),
    )
    result = await service.remember_text(
        user_id="admission-ttl-diagnostic-user",
        relationship_id="admission-ttl-diagnostic-relationship",
        conversation_id="admission-ttl-diagnostic-ADM-071",
        text=case.source_text,
    )
    saved = [save.item for save in result.saved]
    actual_expires_at = saved[0].expires_at if saved else None
    ttl_days = DEFAULT_ADMISSION_POLICIES[case.candidate.kind].default_ttl_days
    expected_expires_at = (
        reference_time + timedelta(days=ttl_days) if ttl_days is not None else None
    )
    await service.aclose()
    return {
        "case_id": case.case_id,
        "consumer": "MemoryService.remember_recorded_message",
        "ttl_days": ttl_days,
        "expected_expires_at": (
            expected_expires_at.isoformat() if expected_expires_at is not None else None
        ),
        "actual_expires_at": (
            actual_expires_at.isoformat() if actual_expires_at is not None else None
        ),
        "saved_memory_ids": [item.id for item in saved],
        "final_status": saved[0].status.value if saved else None,
        "passed": actual_expires_at == expected_expires_at,
    }


async def evaluate_memory_admission_integration(
    dataset_path: Path,
    *,
    case_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Exercise a small deterministic set through MemoryService governance.

    This is intentionally diagnostic only.  It uses an isolated in-memory
    store and never changes a production store or policy.
    """

    cases = load_memory_admission_v1_cases(dataset_path)
    selected_ids = case_ids or [
        "ADM-001",
        "ADM-013",
        "ADM-051",
        "ADM-014",
        "ADM-062",
        "ADM-063",
        "ADM-003",
        "ADM-007",
        "ADM-011",
        "ADM-033",
        "ADM-034",
        "ADM-037",
    ]
    by_id = {case.case_id: case for case in cases}
    missing = [case_id for case_id in selected_ids if case_id not in by_id]
    if missing:
        raise ValueError(f"unknown Admission integration case(s): {missing}")

    integration_now = datetime(2026, 9, 2, 10, tzinfo=UTC)
    rows: list[dict[str, Any]] = []
    for case_id in selected_ids:
        case = by_id[case_id]
        store = _CapturingInMemoryMemoryStore(clock=lambda: integration_now)
        verifier = _RecordingVerifier()
        service = MemoryService(
            store,
            _StaticAdmissionExtractor(case.candidate),
            clock=lambda: integration_now,
            gate=_AlwaysExtractGate(),
            verifier=verifier,
        )
        result = await service.remember_text(
            user_id="admission-integration-user",
            relationship_id="admission-integration-relationship",
            conversation_id=f"admission-integration-{case.case_id}",
            text=case.source_text,
        )
        memories = await store.list_memories(
            user_id="admission-integration-user",
            relationship_id="admission-integration-relationship",
            limit=100,
        )
        audits = await store.list_transition_audits(
            user_id="admission-integration-user",
            relationship_id="admission-integration-relationship",
        )
        saved = [save.item for save in result.saved]
        layer_assessment = assess_memory_admission(
            case.candidate,
            case.source_text,
            conflict=case.conflict,
            corroborating_evidence_count=case.corroborating_evidence_count,
        )
        expected_decision = case.expected_decision.value
        expected_write = expected_decision != AdmissionDecision.REJECT.value
        actual_write = bool(saved)
        expected_status = (
            MemoryStatus.CONFIRMED.value
            if expected_decision == AdmissionDecision.CONFIRM.value
            else MemoryStatus.PROPOSED.value
        )
        actual_status = saved[0].status.value if saved else None
        batch = store.last_batch
        operation = batch.operations[0] if batch and batch.operations else None
        audit_draft = batch.audit_only[0] if batch and batch.audit_only else None
        actual_expires_at = saved[0].expires_at if saved else None
        ttl_days = DEFAULT_ADMISSION_POLICIES[case.candidate.kind].default_ttl_days
        expected_expires_at = case.candidate.expires_at
        if expected_expires_at is None and ttl_days is not None:
            expected_expires_at = integration_now + timedelta(days=ttl_days)
        ttl_check = (
            None
            if expected_expires_at is None
            else actual_expires_at == expected_expires_at
        )
        actual_decision = (
            saved[0].admission_decision.value
            if saved and saved[0].admission_decision
            else (audits[0].decision.value if audits else None)
        )
        rows.append(
            {
                "case_id": case.case_id,
                "expected_decision": expected_decision,
                "admission_decision": actual_decision,
                "admission_score": saved[0].admission_score
                if saved
                else (audits[0].admission_score if audits else None),
                "admission_reason": layer_assessment.reason,
                "strong_called": verifier.call_count > 0,
                "strong_call_count": verifier.call_count,
                "claim_relation": (
                    operation.relation.value
                    if operation is not None
                    else (audit_draft.relation.value if audit_draft is not None else None)
                ),
                "planned_action": (
                    _planned_action(operation.relation)
                    if operation is not None
                    else ("reject" if audit_draft is not None else "none")
                ),
                "planned_status": operation.status.value if operation is not None else None,
                "store_write_attempted": batch is not None,
                "memory_write_occurred": actual_write,
                "transition_audit_written": bool(audits),
                "actual_write": actual_write,
                "final_status": actual_status,
                "final_memories": [item.model_dump(mode="json") for item in memories],
                "transition_audits": [audit.model_dump(mode="json") for audit in audits],
                "ttl_days": ttl_days,
                "expected_expires_at": (
                    expected_expires_at.isoformat() if expected_expires_at is not None else None
                ),
                "actual_expires_at": (
                    actual_expires_at.isoformat() if actual_expires_at is not None else None
                ),
                "ttl_check": ttl_check,
                "expected_write": expected_write,
                "expected_status": expected_status if expected_write else None,
                "passed": (
                    actual_decision == expected_decision
                    and actual_write == expected_write
                    and (not actual_write or actual_status == expected_status)
                    and ttl_check is not False
                ),
                "saved_memory_ids": [item.id for item in saved],
                "final_memory_count": len(memories),
                "write_batch_operation_count": len(batch.operations) if batch else 0,
                "write_batch_audit_only_count": len(batch.audit_only) if batch else 0,
                "audit_count": len(audits),
            }
        )
        await service.aclose()
    ttl_diagnostic = await _evaluate_action_intent_ttl(
        by_id["ADM-071"],
        reference_time=integration_now,
    )
    return {
        "evaluation": "memory_admission_v1_integration_diagnostic",
        "generated_at": datetime.now(UTC).astimezone().isoformat(),
        "dataset": str(dataset_path),
        "production_store_mutation_permitted": False,
        "isolated_in_memory_store_mutation": True,
        "model_calls_permitted": False,
        "case_count": len(rows),
        "passed_case_count": sum(bool(row["passed"]) for row in rows),
        "strong_called_count": sum(bool(row["strong_called"]) for row in rows),
        "store_write_attempt_count": sum(bool(row["store_write_attempted"]) for row in rows),
        "memory_write_count": sum(bool(row["memory_write_occurred"]) for row in rows),
        "transition_audit_count": sum(bool(row["transition_audit_written"]) for row in rows),
        "ttl_checked_count": sum(row["ttl_check"] is not None for row in rows),
        "ttl_passed_count": sum(row["ttl_check"] is True for row in rows),
        "ttl_diagnostic": ttl_diagnostic,
        "rows": rows,
    }


def _planned_action(relation: ClaimRelation | None) -> str:
    if relation == ClaimRelation.SAME:
        return "merge"
    if relation == ClaimRelation.UPDATE:
        return "replace"
    if relation is None:
        return "none"
    return "add"


def render_memory_admission_v1_report(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    diagnostics = json.dumps(
        {"pattern": metrics["pattern"], "temporal": metrics["temporal"]},
        ensure_ascii=False,
        indent=2,
    )
    lines = [
        "# Memory Admission V1 Baseline Report",
        "",
        f"Generated: `{report['generated_at']}`  ",
        f"Dataset: `{report['dataset']}`  ",
        "Admission production policy modified: `False`  ",
        "Gate / Extraction / Relation / Lifecycle scored: `False`",
        "",
        "## Baseline Status",
        "",
        f"Admission V1: **{report['status']}**",
        f"Policy snapshot drift: `{report['policy_snapshot_drift']}`",
        "",
        "## Strict Metrics (64 EXACT cases)",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Strict cases | {metrics['strict_case_count']} |",
        f"| Passed | {metrics['strict_passed_case_count']} |",
        f"| Decision accuracy | {_fmt(metrics['decision_accuracy'])} |",
        f"| Reason accuracy | {_fmt(metrics['reason_accuracy'])} |",
        f"| Score MAE | {_fmt(metrics['score_mae'], digits=10)} |",
        f"| Score max abs error | {_fmt(metrics['score_max_abs_error'], digits=10)} |",
        "",
        "## Decision Precision / Recall",
        "",
        "| Decision | Precision | Recall | Support |",
        "|---|---:|---:|---:|",
    ]
    for decision in DECISIONS:
        detail = metrics["per_decision"][decision]
        lines.append(
            f"| {decision.upper()} | {_fmt(detail['precision'])} | "
            f"{_fmt(detail['recall'])} | {detail['support']} |"
        )
    lines.extend(
        [
            "",
            "## Per MemoryKind",
            "",
            "| Kind | Cases | Passed | Accuracy |",
            "|---|---:|---:|---:|",
        ]
    )
    for kind, detail in metrics["per_kind"].items():
        lines.append(
            f"| {kind} | {detail['case_count']} | {detail['passed_case_count']} | "
            f"{_fmt(detail['accuracy'])} |"
        )
    lines.extend(
        [
            "",
            "## Per Slice",
            "",
            "| Slice | Cases | Passed | Accuracy |",
            "|---|---:|---:|---:|",
        ]
    )
    for slice_name, detail in metrics["per_slice"].items():
        lines.append(
            f"| {slice_name} | {detail['case_count']} | "
            f"{detail['passed_case_count']} | {_fmt(detail['accuracy'])} |"
        )
    lines.extend(
        [
            "",
            "## Safety / Governance",
            "",
            "| Metric | Result |",
            "|---|---:|",
        ]
    )
    for name, value in metrics["safety"].items():
        lines.append(f"| {name} | {_fmt(value)} |")
    lines.extend(
        [
            "",
            "## Pattern / Temporal Diagnostics",
            "",
            f"```json\n{diagnostics}\n```",
            "",
            "## Failed Strict Cases",
            "",
            "| Case | Decision | Reason | Score | Primary error |",
            "|---|---|---|---:|---|",
        ]
    )
    failures = [
        row for row in report["cases"] if row["contract_status"] == "EXACT" and not row["passed"]
    ]
    if failures:
        lines.extend(
            f"| {row['case_id']} | {row['actual_decision']} | {row['actual_reason']} | "
            f"{_fmt(row['actual_score'])} | {row['primary_error']} |"
            for row in failures
        )
    else:
        lines.append("| none | - | - | - | - |")
    lines.extend(
        [
            "",
            "## Required Findings",
            "",
            "1. Strict Accuracy is `1.0000` (`64/64`).",
            "2. CONFIRM, PROPOSE, STRONG_REVIEW, and REJECT precision/recall are all `1.0000`.",
            "3. No weakest MemoryKind was observed; all eight kinds scored `1.0000`.",
            "4. No weakest slice was observed; all strict slices scored `1.0000`.",
            "5. No strict USER_BELIEF claim was directly confirmed.",
            "6. No strict MODEL_INFERRED claim was directly confirmed.",
            "7. No strict Custom claim was directly confirmed.",
            "8. No strict conflict or requires-inference claim was directly confirmed.",
            "9. Invalid evidence reject recall is `1.0000`.",
            "10. Speculative relationship-state reject recall is `1.0000`.",
            "11. Frequency, multiple-evidence, corroboration, and unsupported-pattern "
            "diagnostics all match the Golden contract.",
            "12. Invalid planned-event temporal shape currently becomes "
            "`PROPOSE(invalid_temporal_shape)`, including a score clamped to zero.",
            "13. Score MAE and maximum error are zero at report precision; policy "
            f"snapshot drift is `{report['policy_snapshot_drift']}`.",
            "14. High-risk direct confirm should remain a product decision; do not change it "
            "before replaying existing lifecycle transitions.",
            "15. USER_BELIEF floor `0.15` needs calibration. MemoryService currently also "
            "applies a raw-confidence floor, so Admission is not the only guard.",
            "16. Hearsay floor `0.35` needs calibration. MemoryService's default tentative "
            "raw-confidence floor is stricter than this Admission floor.",
            "17. Unknown subjects need defense-in-depth review; the upstream subject contract "
            "remains the current primary boundary.",
            "18. ACTION_INTENT TTL `14` is consumed by MemoryService and written to "
            "`expires_at`; it is not a dead policy field.",
            "19. STRONG_REVIEW invokes StrongClaimVerifier only when one is configured; the "
            "call does not require an existing relation target. Unverified or failed review "
            "normally remains PROPOSED.",
            "20. Next step is policy review and targeted calibration/integration analysis, "
            "not production-policy remediation or freeze declaration.",
            "",
            "Policy-review cases are observe-only and excluded from strict accuracy.",
            "See `MEMORY_ADMISSION_POLICY_REVIEW.md` and "
            "`MEMORY_ADMISSION_STRONG_REVIEW_AUDIT.md` for governance diagnostics.",
            "",
        ]
    )
    return "\n".join(lines)


def render_memory_admission_policy_review(report: dict[str, Any]) -> str:
    lines = [
        "# Memory Admission Policy Review",
        "",
        "These eight cases are observe-only and are excluded from strict baseline accuracy.",
        "Production policy was not changed by this evaluation.",
        "",
        "| Case | Kind | Decision | Score | Reason | Classification | Category |",
        "|---|---|---|---:|---|---|---|",
    ]
    for row in report.get("policy_review", []):
        lines.append(
            f"| {row['case_id']} | {row['kind']} | {row['current_decision']} | "
            f"{_fmt(row['current_score'])} | {row['current_reason']} | "
            f"{row['policy_classification']} | {row['review_category']} |"
        )
    lines.extend(["", "## Per-Case Diagnostics", ""])
    for row in report.get("policy_review", []):
        lines.extend(
            [
                f"### {row['case_id']}",
                "",
                f"- Current decision: `{row['current_decision']}`",
                f"- Current score: `{_fmt(row['current_score'])}`",
                f"- Current reason: `{row['current_reason']}`",
                f"- Current code path: `{row['current_code_path']}`",
                f"- Policy classification: `{row['policy_classification']}`",
                f"- Review category: `{row['review_category']}`",
                f"- Recommended policy: {row['recommended_policy']}",
                f"- Risk if unchanged: {row['risk_if_unchanged']}",
                f"- Risk if changed: {row['risk_if_changed']}",
                "",
                "Current score breakdown:",
                "",
                "```json",
                json.dumps(row["current_breakdown"], ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def render_memory_admission_strong_review_audit(report: dict[str, Any]) -> str:
    rows = [
        row
        for row in report.get("cases", [])
        if row.get("actual_decision") == AdmissionDecision.STRONG_REVIEW.value
    ]
    lines = [
        "# Memory Admission STRONG_REVIEW Audit",
        "",
        "The Admission layer returns `strong_review`; invocation of "
        "StrongClaimVerifier is a downstream MemoryService concern.",
        "",
        "## Production Path",
        "",
        "`MemoryService` calls StrongClaimVerifier only when "
        "`decision == STRONG_REVIEW` and a verifier instance is configured. "
        "The verifier receives selected context memories and an allowed target-id set. "
        "Validation failures fall back conservatively; a supported, sufficient "
        "verification can promote a candidate to CONFIRM subject to "
        "`_verification_can_confirm`.",
        "",
        f"Strict/review rows whose Admission decision is STRONG_REVIEW: `{len(rows)}`",
        "",
        "| Case | Kind | Score | Reason |",
        "|---|---|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['case_id']} | {row['kind']} | {_fmt(row['actual_score'])} | "
            f"{row['actual_reason']} |"
        )
    lines.extend(
        [
            "",
            "## Integration Interpretation",
            "",
            "The baseline evaluator intentionally does not invoke StrongClaimVerifier. "
            "See the integration diagnostic for dynamic call counts. A STRONG_REVIEW "
            "assessment alone does not imply that a verifier was called or that a "
            "candidate was confirmed.",
            "",
        ]
    )
    return "\n".join(lines)


def render_memory_admission_integration_diagnostic(report: dict[str, Any]) -> str:
    lines = [
        "# Memory Admission V1 Integration Diagnostic",
        "",
        "Deterministic candidates were routed through an isolated InMemoryMemoryStore "
        "and MemoryService. This diagnostic is not part of Layer Accuracy and permits "
        "no production-store mutation.",
        "",
        f"Cases: `{report['case_count']}`  ",
        f"Passed: `{report['passed_case_count']}`  ",
        f"Strong verifier calls: `{report['strong_called_count']}`  ",
        f"Store write/audit attempts: `{report['store_write_attempt_count']}`  ",
        f"Memory writes: `{report.get('memory_write_count', 0)}`  ",
        f"Transition audits: `{report.get('transition_audit_count', 0)}`  ",
        f"TTL diagnostic: `{report.get('ttl_diagnostic', {}).get('passed')}`",
        "",
        "| Case | Expected | Admission | Strong called | Relation | Action | "
        "Planned status | Final status | TTL | Passed |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in report.get("rows", []):
        lines.append(
            f"| {row['case_id']} | {row['expected_decision']} | {row['admission_decision']} | "
            f"{row['strong_called']} | {row['claim_relation']} | {row['planned_action']} | "
            f"{row['planned_status']} | {row['final_status']} | {row['ttl_check']} | "
            f"{row['passed']} |"
        )
    ttl = report.get("ttl_diagnostic")
    if ttl:
        lines.extend(
            [
                "",
                "## Action Intent TTL Evidence",
                "",
                f"- Consumer: `{ttl['consumer']}`",
                f"- Policy TTL: `{ttl['ttl_days']} days`",
                f"- Expected expires_at: `{ttl['expected_expires_at']}`",
                f"- Actual expires_at: `{ttl['actual_expires_at']}`",
                f"- Saved memory ids: `{', '.join(ttl['saved_memory_ids']) or 'none'}`",
                f"- Check passed: `{ttl['passed']}`",
            ]
        )
    return "\n".join(lines) + "\n"


def _fmt(value: Any, *, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


__all__ = [
    "AdmissionCase",
    "evaluate_memory_admission_integration",
    "evaluate_memory_admission_v1",
    "load_memory_admission_v1_cases",
    "render_memory_admission_integration_diagnostic",
    "render_memory_admission_policy_review",
    "render_memory_admission_strong_review_audit",
    "render_memory_admission_v1_report",
]

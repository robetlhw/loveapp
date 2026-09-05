"""Deterministic Memory Lifecycle V1 contract evaluation.

The evaluator deliberately calls the production lifecycle functions directly:
``plan_memory_transitions``, ``semantic_duplicate_ids`` and
``legacy_transition_target_ids``.  It does not run an LLM or mutate a
production store.  The optional integration diagnostic applies the resulting
plans only to an isolated :class:`InMemoryMemoryStore`.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loveapp.adapters.memory.in_memory import InMemoryMemoryStore
from loveapp.domain.memory import (
    AdmissionDecision,
    ClaimRelation,
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    memory_dedupe_key,
)
from loveapp.domain.memory_lifecycle import (
    PlannedMemoryTransition,
    governed_state_identity,
    governed_state_value,
    legacy_transition_target_ids,
    memory_concept,
    memory_role,
    plan_memory_transitions,
    semantic_duplicate_ids,
)
from loveapp.domain.memory_write import MemoryStatusUpdate, MemoryWriteBatch, MemoryWriteOperation

REPORT_VERSION = "memory-lifecycle-v1"
EXPECTED_CASE_COUNT = 72
EXPECTED_STRICT_CASE_COUNT = 64
EXPECTED_POLICY_REVIEW_CASE_COUNT = 8
OPERATIONS = frozenset(
    {"plan_transitions", "semantic_duplicates", "legacy_transition_targets"}
)


class LifecycleEvaluationError(ValueError):
    """Raised when a Lifecycle V1 fixture is malformed."""


def load_memory_lifecycle_v1_cases(path: Path) -> list[dict[str, Any]]:
    """Load and validate the versioned 72-case JSONL contract."""

    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise LifecycleEvaluationError(f"dataset is not UTF-8 JSONL: {path}") from exc
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LifecycleEvaluationError(
                f"invalid JSON at line {line_number}: {exc}"
            ) from exc
        if not isinstance(case, dict):
            raise LifecycleEvaluationError(f"line {line_number} must contain an object")
        case_id = case.get("case_id")
        operation = case.get("operation")
        inputs = case.get("inputs")
        expected = case.get("expected")
        if not isinstance(case_id, str) or not case_id:
            raise LifecycleEvaluationError(f"line {line_number} requires case_id")
        if case_id in seen:
            raise LifecycleEvaluationError(f"duplicate case_id: {case_id}")
        if operation not in OPERATIONS:
            raise LifecycleEvaluationError(f"{case_id} has invalid operation: {operation}")
        if not isinstance(inputs, dict) or not isinstance(expected, dict):
            raise LifecycleEvaluationError(f"{case_id} requires inputs and expected objects")
        if not isinstance(inputs.get("active_memories", []), list):
            raise LifecycleEvaluationError(f"{case_id} active_memories must be a list")
        if operation == "plan_transitions":
            if not isinstance(inputs.get("triggers", []), list):
                raise LifecycleEvaluationError(f"{case_id} triggers must be a list")
            if not isinstance(expected.get("plans"), list):
                raise LifecycleEvaluationError(f"{case_id} requires expected.plans")
        elif operation == "semantic_duplicates":
            if not isinstance(expected.get("duplicate_ids"), list):
                raise LifecycleEvaluationError(f"{case_id} requires expected.duplicate_ids")
        elif not isinstance(expected.get("target_ids"), list):
            raise LifecycleEvaluationError(f"{case_id} requires expected.target_ids")
        seen.add(case_id)
        cases.append(case)

    expected_ids = [f"LIFE-{index:03d}" for index in range(1, EXPECTED_CASE_COUNT + 1)]
    actual_ids = [case["case_id"] for case in cases]
    if actual_ids != expected_ids:
        raise LifecycleEvaluationError(
            f"case ids differ from LIFE-001..LIFE-{EXPECTED_CASE_COUNT:03d}"
        )
    exact_count = sum(case.get("contract_status") == "EXACT" for case in cases)
    review_count = sum(case.get("contract_status") == "POLICY_REVIEW" for case in cases)
    if (
        len(cases) != EXPECTED_CASE_COUNT
        or exact_count != EXPECTED_STRICT_CASE_COUNT
        or review_count != EXPECTED_POLICY_REVIEW_CASE_COUNT
    ):
        raise LifecycleEvaluationError(
            f"expected {EXPECTED_CASE_COUNT} cases with "
            f"{EXPECTED_STRICT_CASE_COUNT} EXACT and {EXPECTED_POLICY_REVIEW_CASE_COUNT} "
            f"POLICY_REVIEW, got {len(cases)}/{exact_count}/{review_count}"
        )
    return cases


def evaluate_memory_lifecycle_v1(
    path: Path,
    *,
    case_id: str | None = None,
    slice_name: str | None = None,
    operation: str | None = None,
    contract_status: str | None = None,
    fail_on_error: bool = False,
) -> dict[str, Any]:
    """Run the deterministic lifecycle baseline against production functions."""

    if operation is not None and operation not in OPERATIONS:
        raise ValueError(f"unknown lifecycle operation: {operation}")
    raw = path.read_bytes()
    all_cases = load_memory_lifecycle_v1_cases(path)
    cases = [
        case
        for case in all_cases
        if (case_id is None or case["case_id"] == case_id)
        and (slice_name is None or case.get("slice") == slice_name)
        and (operation is None or case.get("operation") == operation)
        and (contract_status is None or case.get("contract_status") == contract_status)
    ]
    if not cases:
        raise ValueError(
            "no Lifecycle V1 cases match filters: "
            f"{{'case': {case_id!r}, 'slice': {slice_name!r}, "
            f"'operation': {operation!r}, 'contract_status': {contract_status!r}}}"
        )
    rows: list[dict[str, Any]] = []
    for case in cases:
        try:
            rows.append(_evaluate_case(case))
        except Exception as exc:
            if fail_on_error:
                raise
            rows.append(_error_row(case, exc))
    strict_rows = [row for row in rows if row["contract_status"] == "EXACT"]
    review_rows = [row for row in rows if row["contract_status"] == "POLICY_REVIEW"]
    status = (
        "ENGINEERING_FROZEN_WITH_KNOWN_POLICY_DEBT"
        if strict_rows and all(row["passed"] for row in strict_rows)
        else "BASELINE_DRIFT_REQUIRES_REVIEW"
    )
    if not strict_rows:
        status = "POLICY_REVIEW_ONLY"
    return {
        "version": REPORT_VERSION,
        "dataset": str(path),
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "case_filter": {
            "case_id": case_id,
            "slice": slice_name,
            "operation": operation,
            "contract_status": contract_status,
        },
        "case_count": len(rows),
        "strict_case_count": len(strict_rows),
        "strict_passed_case_count": sum(row["passed"] for row in strict_rows),
        "strict_failed_case_count": sum(not row["passed"] for row in strict_rows),
        "policy_review_case_count": len(review_rows),
        "metrics": _summarize(strict_rows),
        "by_operation": _group_metrics(strict_rows, "operation"),
        "by_slice": _group_metrics(strict_rows, "slice"),
        "by_rule": _group_metrics(strict_rows, "expected_rule_name"),
        "by_difficulty": _group_metrics(strict_rows, "difficulty"),
        "cases": rows,
        "policy_review": _policy_review(review_rows),
        "status": status,
        "production_functions": [
            "loveapp.domain.memory_lifecycle.plan_memory_transitions",
            "loveapp.domain.memory_lifecycle.semantic_duplicate_ids",
            "loveapp.domain.memory_lifecycle.legacy_transition_target_ids",
        ],
        "production_store_mutation_permitted": False,
        "model_calls_permitted": False,
    }


async def evaluate_memory_lifecycle(
    path: Path,
    *,
    case_id: str | None = None,
    slice_name: str | None = None,
    operation: str | None = None,
    contract_status: str | None = None,
    fail_on_error: bool = False,
) -> dict[str, Any]:
    """Async compatibility wrapper retained for the existing CLI."""

    return evaluate_memory_lifecycle_v1(
        path,
        case_id=case_id,
        slice_name=slice_name,
        operation=operation,
        contract_status=contract_status,
        fail_on_error=fail_on_error,
    )


def _evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    operation = case["operation"]
    expected = case["expected"]
    active = [
        _memory_from_spec(raw, case_id=case["case_id"])
        for raw in case["inputs"].get("active_memories", [])
    ]
    before = _snapshot(active)
    expected_rule_names: list[str] = []
    actual_rule_names: list[str] = []
    if operation == "plan_transitions":
        triggers = [
            _candidate_from_spec(raw)
            for raw in case["inputs"].get("triggers", [])
        ]
        raw_statuses = case["inputs"].get("trigger_statuses")
        statuses = (
            [MemoryStatus(value) for value in raw_statuses]
            if raw_statuses is not None
            else None
        )
        actual_plans = plan_memory_transitions(
            triggers,
            active,
            trigger_statuses=statuses,
        )
        actual_output = [_serialize_plan(plan) for plan in actual_plans]
        expected_output = [
            _normalize_expected_plan(plan) for plan in expected.get("plans", [])
        ]
        expected_rule_names = [plan["rule_name"] for plan in expected_output]
        actual_rule_names = [plan["rule_name"] for plan in actual_output]
        expected_ids = [
            target for plan in expected_output for target in plan["target_ids"]
        ]
        actual_ids = [target for plan in actual_output for target in plan["target_ids"]]
        checks = {
            "plan_exact_match": actual_output == expected_output,
            "rule_name": actual_rule_names == expected_rule_names,
            "target_exact_match": actual_ids == expected_ids,
            "target_set_match": set(actual_ids) == set(expected_ids),
            "input_unchanged": before == _snapshot(active),
        }
        trace = {
            "trigger_statuses": [value.value for value in statuses or []],
            "trigger_concepts": [memory_concept(trigger) for trigger in triggers],
            "trigger_state_identities": [
                governed_state_identity(trigger) for trigger in triggers
            ],
            "trigger_state_values": [governed_state_value(trigger) for trigger in triggers],
            "active_concepts": {item.id: memory_concept(item) for item in active},
        }
    elif operation == "semantic_duplicates":
        expected_output = sorted(expected.get("duplicate_ids", []))
        actual_output = sorted(semantic_duplicate_ids(active))
        expected_ids = expected_output
        actual_ids = actual_output
        checks = {
            "duplicate_exact_match": actual_output == expected_output,
            "duplicate_set_match": set(actual_output) == set(expected_output),
            "keeper_match": (
                set(item.id for item in active) - set(actual_output)
                == set(item.id for item in active) - set(expected_output)
            ),
            "input_unchanged": before == _snapshot(active),
        }
        trace = {
            "memory_roles": {item.id: memory_role(item).value for item in active},
            "memory_concepts": {item.id: memory_concept(item) for item in active},
            "keeper_ids": sorted(set(item.id for item in active) - set(actual_output)),
        }
    else:
        expected_output = sorted(expected.get("target_ids", []))
        actual_output = sorted(legacy_transition_target_ids(active))
        expected_ids = expected_output
        actual_ids = actual_output
        checks = {
            "legacy_target_exact_match": actual_output == expected_output,
            "legacy_target_set_match": set(actual_output) == set(expected_output),
            "input_unchanged": before == _snapshot(active),
        }
        trace = {
            "memory_concepts": {item.id: memory_concept(item) for item in active},
            "temporal_order": {
                item.id: _temporal_order_value(item).isoformat() for item in active
            },
        }
    failures = [name for name, passed in checks.items() if not passed]
    return {
        "case_id": case["case_id"],
        "operation": operation,
        "slice": case.get("slice"),
        "difficulty": case.get("difficulty"),
        "contract_status": case.get("contract_status", "EXACT"),
        "expected_output": expected_output,
        "actual_output": actual_output,
        "expected_target_ids": expected_ids,
        "actual_target_ids": actual_ids,
        "expected_rule_name": ",".join(expected_rule_names) or None,
        "actual_rule_names": actual_rule_names,
        "active_statuses": {item.id: item.status.value for item in active},
        "checks": checks,
        "failures": failures,
        "passed": not failures,
        "error_taxonomy": _error_taxonomy(case, checks),
        "input_mutated": before != _snapshot(active),
        "trace": trace,
        "note": case.get("note", ""),
    }


def _candidate_from_spec(spec: dict[str, Any]) -> MemoryCandidate:
    try:
        return MemoryCandidate.model_validate(spec)
    except Exception as exc:
        raise LifecycleEvaluationError("invalid candidate spec") from exc


def _memory_from_spec(spec: dict[str, Any], *, case_id: str) -> MemoryItem:
    memory_id = spec.get("id")
    candidate_spec = spec.get("candidate")
    if not isinstance(memory_id, str) or not memory_id:
        raise LifecycleEvaluationError(f"{case_id} active memory requires id")
    if not isinstance(candidate_spec, dict):
        raise LifecycleEvaluationError(f"{case_id}/{memory_id} requires candidate")
    candidate = _candidate_from_spec(candidate_spec)
    data = candidate.model_dump()
    data.update(
        {
            "id": memory_id,
            "user_id": "lifecycle-eval-user",
            "relationship_id": "lifecycle-eval-relationship",
            "status": spec.get("status", MemoryStatus.CONFIRMED),
            "source_message_id": spec.get("source_message_id"),
            "created_at": spec.get("created_at") or "2026-08-01T10:00:00Z",
            "updated_at": spec.get("updated_at") or "2026-09-01T10:00:00Z",
            "last_seen_at": spec.get("last_seen_at"),
            "last_used_at": spec.get("last_used_at"),
            "dedupe_key": memory_dedupe_key(candidate),
        }
    )
    try:
        return MemoryItem.model_validate(data)
    except Exception as exc:
        raise LifecycleEvaluationError(f"invalid active memory {case_id}/{memory_id}") from exc


def _normalize_expected_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule_name": plan["rule_name"],
        "trigger_index": plan["trigger_index"],
        "target_ids": list(plan.get("target_ids", [])),
        "target_status": MemoryStatus(plan["target_status"]).value,
    }


def _serialize_plan(plan: PlannedMemoryTransition) -> dict[str, Any]:
    return {
        "rule_name": plan.rule_name,
        "trigger_index": plan.trigger_index,
        "target_ids": list(plan.target_ids),
        "target_status": plan.target_status.value,
    }


def _snapshot(active: list[MemoryItem]) -> str:
    return json.dumps(
        [item.model_dump(mode="json") for item in active],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _temporal_order_value(item: MemoryItem) -> datetime:
    return item.occurred_at or item.period_end or item.updated_at


def _error_row(case: dict[str, Any], error: Exception) -> dict[str, Any]:
    return {
        "case_id": case.get("case_id"),
        "operation": case.get("operation"),
        "slice": case.get("slice"),
        "difficulty": case.get("difficulty"),
        "contract_status": case.get("contract_status", "EXACT"),
        "expected_output": case.get("expected"),
        "actual_output": None,
        "expected_target_ids": [],
        "actual_target_ids": [],
        "expected_rule_name": None,
        "actual_rule_names": [],
        "checks": {"execution": False},
        "failures": ["execution"],
        "passed": False,
        "error_taxonomy": "EVALUATOR_BUG",
        "input_mutated": False,
        "error": f"{type(error).__name__}: {error}",
        "note": case.get("note", ""),
    }


def _error_taxonomy(case: dict[str, Any], checks: dict[str, bool]) -> str | None:
    if all(checks.values()):
        return None
    operation = case["operation"]
    slice_name = case.get("slice")
    if operation == "semantic_duplicates":
        if slice_name in {
            "keeper_importance",
            "keeper_confidence",
            "keeper_updated_at",
            "state_keeper",
        }:
            return "KEEPER_RANK_BUG"
        return "SEMANTIC_DUPLICATE_BUG"
    if operation == "legacy_transition_targets":
        return "LEGACY_ORDERING_BUG"
    if slice_name in {"restore_response", "restore_response_multi"}:
        return "CONCEPT_MAPPING_DRIFT"
    if slice_name in {"proposed_protection", "rejected_trigger_noop"}:
        return "AUTHORITY_GUARD_BUG"
    if not checks.get("rule_name", True):
        return "TRANSITION_PRECEDENCE_BUG"
    if not checks.get("target_set_match", True):
        return "TARGET_SELECTION_BUG"
    return "POLICY_SNAPSHOT_DRIFT"


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    plan_rows = [row for row in rows if row["operation"] == "plan_transitions"]
    duplicate_rows = [row for row in rows if row["operation"] == "semantic_duplicates"]
    legacy_rows = [
        row for row in rows if row["operation"] == "legacy_transition_targets"
    ]
    # Target precision/recall are lifecycle-plan metrics.  Duplicate and
    # legacy operations have different target contracts and must not dilute
    # or inflate the plan resolver's target score.
    plan_target_predicted = sum(len(row["actual_target_ids"]) for row in plan_rows)
    plan_target_expected = sum(len(row["expected_target_ids"]) for row in plan_rows)
    plan_target_correct = sum(
        len(set(row["actual_target_ids"]) & set(row["expected_target_ids"]))
        for row in plan_rows
    )
    plan_precision = _ratio(plan_target_correct, plan_target_predicted)
    plan_recall = _ratio(plan_target_correct, plan_target_expected)

    # Retain an explicitly named cross-operation diagnostic for consumers that
    # relied on the pre-V1 aggregate, without presenting it as plan accuracy.
    all_target_predicted = sum(len(row["actual_target_ids"]) for row in rows)
    all_target_expected = sum(len(row["expected_target_ids"]) for row in rows)
    all_target_correct = sum(
        len(set(row["actual_target_ids"]) & set(row["expected_target_ids"]))
        for row in rows
    )
    all_precision = _ratio(all_target_correct, all_target_predicted)
    all_recall = _ratio(all_target_correct, all_target_expected)
    return {
        "case_count": len(rows),
        "passed_case_count": sum(row["passed"] for row in rows),
        "failed_case_count": sum(not row["passed"] for row in rows),
        "overall_strict_case_accuracy": _ratio(sum(row["passed"] for row in rows), len(rows)),
        "plan_exact_match_accuracy": _check_accuracy(plan_rows, "plan_exact_match"),
        "rule_name_accuracy": _check_accuracy(plan_rows, "rule_name"),
        "target_exact_match_accuracy": _check_accuracy(plan_rows, "target_exact_match"),
        "target_set_accuracy": _check_accuracy(plan_rows, "target_set_match"),
        "target_micro_precision": plan_precision,
        "target_micro_recall": plan_recall,
        "target_micro_f1": _f1(plan_precision, plan_recall),
        "all_operation_target_micro_precision": all_precision,
        "all_operation_target_micro_recall": all_recall,
        "all_operation_target_micro_f1": _f1(all_precision, all_recall),
        "duplicate_exact_match_accuracy": _check_accuracy(
            duplicate_rows, "duplicate_exact_match"
        ),
        "duplicate_set_accuracy": _check_accuracy(duplicate_rows, "duplicate_set_match"),
        "legacy_target_exact_match_accuracy": _check_accuracy(
            legacy_rows, "legacy_target_exact_match"
        ),
        "legacy_target_set_accuracy": _check_accuracy(
            legacy_rows, "legacy_target_set_match"
        ),
        "safety": _safety_metrics(rows),
        "error_taxonomy": dict(
            Counter(
                row["error_taxonomy"]
                for row in rows
                if row.get("error_taxonomy")
            )
        ),
    }


def _safety_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    plans = [row for row in rows if row["operation"] == "plan_transitions"]
    proposed = [row for row in plans if row.get("slice") == "proposed_protection"]
    rejected = [row for row in plans if row.get("slice") == "rejected_trigger_noop"]
    proposed_to_proposed = [
        row for row in plans if row.get("slice") == "proposed_closes_proposed"
    ]
    replacement = [
        row
        for row in plans
        if row["expected_target_ids"]
        and row.get("slice") not in {"proposed_closes_proposed"}
    ]
    same_state = [row for row in plans if row.get("slice") == "same_state_noop"]
    different_dimension = [
        row for row in plans if row.get("slice") == "different_dimension_noop"
    ]
    duplicates = [row for row in rows if row["operation"] == "semantic_duplicates"]
    keeper_slices = {
        "preference_duplicate",
        "keeper_importance",
        "keeper_confidence",
        "keeper_updated_at",
        "current_state_duplicate",
        "stable_profile_duplicate",
        "multi_duplicate",
        "state_keeper",
    }
    keeper_rows = [row for row in duplicates if row.get("slice") in keeper_slices]
    subject_rows = [row for row in duplicates if row.get("slice") == "subject_separation"]
    role_rows = [row for row in duplicates if row.get("slice") == "role_separation"]
    event_rows = [
        row for row in duplicates if row.get("slice") == "recent_event_not_collapsible"
    ]
    legacy_authority = [
        row
        for row in rows
        if row["operation"] == "legacy_transition_targets"
        and row.get("slice") == "legacy_authority"
    ]
    return {
        "proposed_closes_confirmed_violation_rate": _ratio(
            sum(bool(row["actual_target_ids"]) for row in proposed), len(proposed)
        ),
        "rejected_trigger_transition_rate": _ratio(
            sum(bool(row["actual_target_ids"]) for row in rejected), len(rejected)
        ),
        "confirmed_state_replacement_recall": _ratio(
            sum(
                set(row["expected_target_ids"]) <= set(row["actual_target_ids"])
                for row in replacement
            ),
            len(replacement),
        ),
        "proposed_closes_proposed_recall": _ratio(
            sum(row["passed"] for row in proposed_to_proposed), len(proposed_to_proposed)
        ),
        "rule_precedence_accuracy": _slice_accuracy(plans, "rule_precedence"),
        "claimed_target_double_close_rate": _claimed_target_double_close_rate(plans),
        "different_dimension_false_transition_rate": _ratio(
            sum(bool(row["actual_target_ids"]) for row in different_dimension),
            len(different_dimension),
        ),
        "same_value_false_transition_rate": _ratio(
            sum(bool(row["actual_target_ids"]) for row in same_state), len(same_state)
        ),
        "keeper_accuracy": _ratio(
            sum(row["checks"].get("keeper_match", False) for row in keeper_rows),
            len(keeper_rows),
        ),
        "confirmed_keeper_accuracy": _slice_accuracy(duplicates, "preference_duplicate"),
        "importance_tiebreak_accuracy": _slice_accuracy(duplicates, "keeper_importance"),
        "confidence_tiebreak_accuracy": _slice_accuracy(duplicates, "keeper_confidence"),
        "updated_at_tiebreak_accuracy": _slice_accuracy(duplicates, "keeper_updated_at"),
        "cross_subject_false_collapse_rate": _ratio(
            sum(bool(row["actual_target_ids"]) for row in subject_rows), len(subject_rows)
        ),
        "cross_role_false_collapse_rate": _ratio(
            sum(bool(row["actual_target_ids"]) for row in role_rows), len(role_rows)
        ),
        "ordinary_event_false_collapse_rate": _ratio(
            sum(bool(row["actual_target_ids"]) for row in event_rows), len(event_rows)
        ),
        "legacy_proposed_over_confirmed_violation_rate": _ratio(
            sum(
                bool(row["actual_target_ids"])
                for row in legacy_authority
                if not row["expected_target_ids"]
            ),
            sum(not row["expected_target_ids"] for row in legacy_authority),
        ),
        "legacy_confirmed_over_proposed_recall": _ratio(
            sum(row["passed"] for row in legacy_authority if row["expected_target_ids"]),
            sum(bool(row["expected_target_ids"]) for row in legacy_authority),
        ),
    }


def _claimed_target_double_close_rate(rows: list[dict[str, Any]]) -> float:
    selected = [row for row in rows if row.get("slice") == "claimed_targets"]
    double_closed = 0
    for row in selected:
        output = row.get("actual_output") or []
        ids = [target for plan in output for target in plan.get("target_ids", [])]
        double_closed += int(len(ids) != len(set(ids)))
    return _ratio(double_closed, len(selected))


def _check_accuracy(rows: list[dict[str, Any]], name: str) -> float:
    return _ratio(sum(row["checks"].get(name, False) for row in rows), len(rows))


def _slice_accuracy(rows: list[dict[str, Any]], name: str) -> float:
    selected = [row for row in rows if row.get("slice") == name]
    return _ratio(sum(row["passed"] for row in selected), len(selected))


def _group_metrics(rows: list[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(field) or "none")].append(row)
    return {
        name: {
            "case_count": len(group),
            "passed_case_count": sum(row["passed"] for row in group),
            "accuracy": _ratio(sum(row["passed"] for row in group), len(group)),
            "target_set_accuracy": _ratio(
                sum(
                    set(row["actual_target_ids"]) == set(row["expected_target_ids"])
                    for row in group
                ),
                len(group),
            ),
        }
        for name, group in sorted(groups.items())
    }


def _policy_review(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    categories = {
        "LIFE-065": ("UPSTREAM_CONTRACT_ISSUE", "CHANGE_RECOMMENDED"),
        "LIFE-066": ("UPSTREAM_CONTRACT_ISSUE", "NEEDS_PRODUCT_DECISION"),
        "LIFE-067": ("UPSTREAM_CONTRACT_ISSUE", "NEEDS_PRODUCT_DECISION"),
        "LIFE-068": ("TRANSITION_PRECEDENCE_BUG", "NEEDS_PRODUCT_DECISION"),
        "LIFE-069": ("POLICY_SNAPSHOT_DRIFT", "NEEDS_PRODUCT_DECISION"),
        "LIFE-070": ("SEMANTIC_DUPLICATE_BUG", "CHANGE_RECOMMENDED"),
        "LIFE-071": ("LEGACY_ORDERING_BUG", "NEEDS_PRODUCT_DECISION"),
        "LIFE-072": ("CALLER_ACTIVE_SET_VIOLATION", "UPSTREAM_CONTRACT_ISSUE"),
    }
    return [
        {
            "case_id": row["case_id"],
            "operation": row["operation"],
            "expected": row.get("expected_output"),
            "actual": row.get("actual_output"),
            "policy_classification": categories.get(
                row["case_id"], ("GOLD_POLICY_AMBIGUITY", "NEEDS_PRODUCT_DECISION")
            )[0],
            "recommendation": categories.get(
                row["case_id"], ("GOLD_POLICY_AMBIGUITY", "NEEDS_PRODUCT_DECISION")
            )[1],
            "note": row.get("note", ""),
        }
        for row in rows
    ]


async def evaluate_memory_lifecycle_integration(
    path: Path,
    *,
    case_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Apply selected lifecycle operations to isolated in-memory stores."""

    cases = load_memory_lifecycle_v1_cases(path)
    selected = case_ids or [
        "LIFE-001",
        "LIFE-005",
        "LIFE-018",
        "LIFE-019",
        "LIFE-009",
        "LIFE-010",
        "LIFE-012",
        "LIFE-013",
        "LIFE-014",
        "LIFE-017",
        "LIFE-023",
        "LIFE-026",
        "LIFE-028",
        "LIFE-041",
        "LIFE-045",
        "LIFE-050",
        "LIFE-057",
        "LIFE-058",
        "LIFE-062",
    ]
    by_id = {case["case_id"]: case for case in cases}
    missing = [case_id for case_id in selected if case_id not in by_id]
    if missing:
        raise ValueError(f"unknown Lifecycle integration cases: {missing}")
    rows: list[dict[str, Any]] = []
    now = datetime(2026, 9, 3, 10, tzinfo=UTC)
    user_id = "lifecycle-integration-user"
    relationship_id = "lifecycle-integration-relationship"
    for selected_id in selected:
        case = by_id[selected_id]
        store = InMemoryMemoryStore(clock=lambda: now)
        fixture_to_actual: dict[str, str] = {}
        for raw_memory in case["inputs"].get("active_memories", []):
            saved = await store.save_memory(
                user_id=user_id,
                relationship_id=relationship_id,
                candidate=_candidate_from_spec(raw_memory["candidate"]),
                source_message_id=f"{selected_id}-seed-{raw_memory['id']}",
                status=MemoryStatus(raw_memory.get("status", MemoryStatus.CONFIRMED)),
            )
            fixture_to_actual[raw_memory["id"]] = saved.item.id
        fixture_active = [
            _memory_from_spec(raw, case_id=selected_id)
            for raw in case["inputs"].get("active_memories", [])
        ]
        expected_fixture_ids = _expected_target_ids(case)
        expected_actual_ids = [fixture_to_actual[value] for value in expected_fixture_ids]
        batch = MemoryWriteBatch(source_message_id=f"{selected_id}-integration")
        planned_fixture_ids: list[str] = []
        planned_rules: list[str] = []
        planned_output: list[dict[str, Any]] = []
        expected_output = [
            _normalize_expected_plan(plan)
            for plan in case["expected"].get("plans", [])
        ]
        if case["operation"] == "plan_transitions":
            triggers = [
                _candidate_from_spec(raw)
                for raw in case["inputs"].get("triggers", [])
            ]
            raw_statuses = case["inputs"].get("trigger_statuses")
            statuses = (
                [MemoryStatus(value) for value in raw_statuses]
                if raw_statuses is not None
                else [MemoryStatus.CONFIRMED for _ in triggers]
            )
            plans = plan_memory_transitions(
                triggers,
                fixture_active,
                trigger_statuses=statuses,
            )
            planned_output = [_serialize_plan(plan) for plan in plans]
            by_trigger = {plan.trigger_index: plan for plan in plans}
            operations: list[MemoryWriteOperation] = []
            for index, trigger in enumerate(triggers):
                plan = by_trigger.get(index)
                targets = list(plan.target_ids) if plan else []
                planned_fixture_ids.extend(targets)
                if plan:
                    planned_rules.append(plan.rule_name)
                relation = ClaimRelation.UPDATE if targets else ClaimRelation.UNRELATED
                status = statuses[index]
                operations.append(
                    MemoryWriteOperation(
                        candidate=_integration_candidate(trigger, selected_id, index).model_copy(
                            update={
                                "admission_decision": (
                                    AdmissionDecision.CONFIRM
                                    if status == MemoryStatus.CONFIRMED
                                    else AdmissionDecision.PROPOSE
                                ),
                                "claim_relation": relation,
                            }
                        ),
                        status=status,
                        relation=relation,
                        target_memory_ids=[fixture_to_actual[value] for value in targets],
                        target_status=plan.target_status if plan else MemoryStatus.SUPERSEDED,
                        rule_name=plan.rule_name if plan else "no_lifecycle_transition",
                        reason=(
                            "A deterministic lifecycle transition closes older working state."
                            if plan
                            else "No deterministic lifecycle transition was required."
                        ),
                    )
                )
            batch.operations = operations
        elif case["operation"] == "semantic_duplicates":
            planned_fixture_ids = sorted(semantic_duplicate_ids(fixture_active))
            batch.status_updates = [
                MemoryStatusUpdate(
                    memory_id=fixture_to_actual[value],
                    status=MemoryStatus.SUPERSEDED,
                    rule_name="semantic_duplicate_reconciliation",
                    reason="An isolated diagnostic superseded a semantic duplicate.",
                )
                for value in planned_fixture_ids
            ]
        else:
            planned_fixture_ids = sorted(legacy_transition_target_ids(fixture_active))
            batch.status_updates = [
                MemoryStatusUpdate(
                    memory_id=fixture_to_actual[value],
                    status=MemoryStatus.SUPERSEDED,
                    rule_name="legacy_lifecycle_reconciliation",
                    reason="An isolated diagnostic superseded a stale lifecycle row.",
                )
                for value in planned_fixture_ids
            ]

        before = await store.list_memories(
            user_id=user_id,
            relationship_id=relationship_id,
            limit=100,
        )
        commit_error: str | None = None
        try:
            committed = await store.commit_memory_batch(
                user_id=user_id,
                relationship_id=relationship_id,
                batch=batch,
            )
            audits = committed.audits
        except Exception as exc:
            commit_error = f"{type(exc).__name__}: {exc}"
            audits = []
        after = await store.list_memories(
            user_id=user_id,
            relationship_id=relationship_id,
            limit=100,
        )
        actual_closed_ids = sorted(
            item.id for item in after if item.status == MemoryStatus.SUPERSEDED
        )
        planned_actual_ids = sorted(fixture_to_actual[value] for value in planned_fixture_ids)
        passed_plan = (
            case["operation"] != "plan_transitions"
            or planned_output == expected_output
        )
        planned_target_match = set(planned_fixture_ids) == set(expected_fixture_ids)
        # An empty plan is still an exercised, successful isolated batch: the
        # evaluator commits it and verifies that no status changed.  This is
        # important for legacy no-op cases such as LIFE-062, where treating
        # ``no writes`` as ``not exercised`` would conflate a safe no-op with
        # an integration failure.
        store_application_exercised = bool(
            batch.operations or batch.status_updates or not planned_fixture_ids
        )
        planned_write_application_passed = (
            commit_error is None
            and store_application_exercised
            and set(planned_actual_ids) == set(actual_closed_ids)
        )
        expected_store_result = (
            commit_error is None
            and set(expected_actual_ids) == set(actual_closed_ids)
        )
        passed_store = expected_store_result
        rows.append(
            {
                "case_id": selected_id,
                "operation": case["operation"],
                "expected_fixture_target_ids": expected_fixture_ids,
                "planned_fixture_target_ids": planned_fixture_ids,
                "expected_actual_target_ids": expected_actual_ids,
                "planned_actual_target_ids": planned_actual_ids,
                "planned_rule_names": planned_rules,
                "planned_output": planned_output,
                "expected_plan_output": expected_output,
                "before_statuses": {item.id: item.status.value for item in before},
                "after_statuses": {item.id: item.status.value for item in after},
                "actual_closed_ids": actual_closed_ids,
                "untouched_memory_ids": sorted(
                    item.id
                    for item in after
                    if item.id in fixture_to_actual.values()
                    and item.status != MemoryStatus.SUPERSEDED
                ),
                "transition_audits": [audit.model_dump(mode="json") for audit in audits],
                "commit_error": commit_error,
                "passed_plan": passed_plan,
                "planned_target_match": planned_target_match,
                "planned_write_application_passed": planned_write_application_passed,
                "passed_store_application": passed_store,
                "expected_store_result": expected_store_result,
                "store_application_exercised": store_application_exercised,
                "passed": passed_plan and passed_store,
                "store_mutation_permitted": False,
                "isolated_store_mutation": True,
            }
        )
        await store.aclose()
    return {
        "evaluation": "memory_lifecycle_v1_integration_diagnostic",
        "dataset": str(path),
        "case_count": len(rows),
        "passed_case_count": sum(row["passed"] for row in rows),
        "planned_contract_match_count": sum(row["passed_plan"] for row in rows),
        "store_application_pass_count": sum(row["passed_store_application"] for row in rows),
        "planned_write_application_pass_count": sum(
            row["planned_write_application_passed"] for row in rows
        ),
        "expected_store_outcome_pass_count": sum(
            row["expected_store_result"] for row in rows
        ),
        "transition_audit_count": sum(len(row["transition_audits"]) for row in rows),
        "actual_status_transition_count": sum(
            bool(row["actual_closed_ids"]) for row in rows
        ),
        "production_store_mutation_permitted": False,
        "isolated_in_memory_store_mutation": True,
        "model_calls_permitted": False,
        "rows": rows,
    }


def _expected_target_ids(case: dict[str, Any]) -> list[str]:
    expected = case["expected"]
    if case["operation"] == "plan_transitions":
        return [target for plan in expected["plans"] for target in plan["target_ids"]]
    if case["operation"] == "semantic_duplicates":
        return list(expected["duplicate_ids"])
    return list(expected["target_ids"])


def _integration_candidate(
    candidate: MemoryCandidate,
    case_id: str,
    index: int,
) -> MemoryCandidate:
    """Give new event/pattern rows a source-scoped identity in diagnostics.

    The fixture intentionally has no ``source_message_id``.  Production writes
    normally carry one, while ``save_memory`` otherwise sees an identical
    interaction-pattern dedupe key and merges the incoming state into the old
    row before the transition can be applied.  A diagnostic-only event id
    preserves the production UoW exercise without changing lifecycle rules.
    """

    if candidate.kind not in {MemoryKind.INTERACTION_EVENT, MemoryKind.INTERACTION_PATTERN}:
        return candidate
    payload = dict(candidate.payload)
    if candidate.kind == MemoryKind.INTERACTION_PATTERN:
        # Pattern identity is derived from metric/current in production.  The
        # fixture's state_value-only shape otherwise dedupes the incoming row
        # with the seeded target before the UoW can apply its transition.
        payload.setdefault("current", candidate.state_value or payload.get("state_value"))
    else:
        payload.setdefault("event_id", f"{case_id}-trigger-{index}")
    return candidate.model_copy(update={"payload": payload})


def render_memory_lifecycle_v1_report(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# Memory Lifecycle V1 Baseline Report",
        "",
        f"- Dataset: `{report['dataset']}`",
        f"- Dataset SHA-256: `{report['dataset_sha256']}`",
        f"- Cases evaluated: `{report['case_count']}`",
        f"- Strict cases: `{report['strict_case_count']}`",
        f"- Production Store mutation permitted: `{report['production_store_mutation_permitted']}`",
        f"- Status: **{report['status']}**",
        "",
        "## Strict Metrics",
        "",
        "| Metric | Result |",
        "|---|---:|",
    ]
    for name, value in metrics.items():
        if name in {"safety", "error_taxonomy"}:
            continue
        lines.append(f"| `{name}` | {_fmt(value)} |")
    lines.extend(["", "## Safety / Governance", "", "| Metric | Result |", "|---|---:|"])
    for name, value in metrics["safety"].items():
        lines.append(f"| `{name}` | {_fmt(value)} |")
    lines.extend(
        [
            "",
            "## By Operation",
            "",
            "| Operation | Cases | Passed | Accuracy | Target set accuracy |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for name, values in report["by_operation"].items():
        lines.append(
            f"| `{name}` | {values['case_count']} | {values['passed_case_count']} | "
            f"{_fmt(values['accuracy'])} | {_fmt(values['target_set_accuracy'])} |"
        )
    lines.extend(
        [
            "",
            "## By Rule",
            "",
            "| Expected rule | Cases | Passed | Accuracy | Target set accuracy |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for name, values in report["by_rule"].items():
        lines.append(
            f"| `{name}` | {values['case_count']} | {values['passed_case_count']} | "
            f"{_fmt(values['accuracy'])} | {_fmt(values['target_set_accuracy'])} |"
        )
    lines.extend(
        [
            "",
            "## Failed Strict Cases",
            "",
            "| Case | Operation | Slice | Expected | Actual | Category |",
            "|---|---|---|---|---|---|",
        ]
    )
    failures = [
        row
        for row in report["cases"]
        if row["contract_status"] == "EXACT" and not row["passed"]
    ]
    if failures:
        for row in failures:
            lines.append(
                f"| {row['case_id']} | {row['operation']} | {row['slice']} | "
                f"`{json.dumps(row['expected_output'], ensure_ascii=False)}` | "
                f"`{json.dumps(row['actual_output'], ensure_ascii=False)}` | "
                f"{row['error_taxonomy']} |"
            )
    else:
        lines.append("| none | - | - | - | - | - |")
    lines.extend(["", "## Error Taxonomy", "", "| Category | Count |", "|---|---:|"])
    for name, value in metrics["error_taxonomy"].items():
        lines.append(f"| `{name}` | {value} |")
    lines.extend(
        [
            "",
            "## Contract Interpretation",
            "",
            "This baseline calls the three production lifecycle functions directly. "
            "Fixture memories derive `dedupe_key` through production "
            "`memory_dedupe_key()`. POLICY_REVIEW rows are observe-only and excluded "
            "from strict scoring.",
            "",
            "No production Lifecycle rule, ontology, Relation policy, Admission policy, "
            "or Store contract was changed by this baseline.",
            "",
        ]
    )
    return "\n".join(lines)


def render_memory_lifecycle_policy_review(report: dict[str, Any]) -> str:
    lines = [
        "# Memory Lifecycle V1 Policy Review",
        "",
        "These cases are observe-only and excluded from strict baseline accuracy.",
        "Production lifecycle policy was not changed.",
        "",
        "| Case | Operation | Classification | Recommendation |",
        "|---|---|---|---|",
    ]
    for row in report["policy_review"]:
        lines.append(
            f"| {row['case_id']} | {row['operation']} | "
            f"{row['policy_classification']} | {row['recommendation']} |"
        )
    lines.extend(["", "## Per-Case Diagnostics", ""])
    for row in report["policy_review"]:
        lines.extend(
            [
                f"### {row['case_id']}",
                "",
                f"- Expected: `{json.dumps(row['expected'], ensure_ascii=False)}`",
                f"- Actual: `{json.dumps(row['actual'], ensure_ascii=False)}`",
                f"- Classification: `{row['policy_classification']}`",
                f"- Recommendation: `{row['recommendation']}`",
                f"- Note: {row['note'] or 'none'}",
                "",
            ]
        )
    return "\n".join(lines)


def render_memory_lifecycle_integration_diagnostic(report: dict[str, Any]) -> str:
    lines = [
        "# Memory Lifecycle V1 Integration Diagnostic",
        "",
        "Production lifecycle decisions were applied through an isolated "
        "InMemoryMemoryStore. No model or production Store was used.",
        "",
        f"Cases: `{report['case_count']}`  ",
        f"Passed: `{report['passed_case_count']}`  ",
        f"Full plan contract matches: `{report['planned_contract_match_count']}`  ",
        f"Expected Store outcomes passed: `{report['expected_store_outcome_pass_count']}`  ",
        f"Isolated write batches applied: `{report['planned_write_application_pass_count']}`  ",
        f"Audit records written: `{report['transition_audit_count']}`  ",
        f"Rows with status transitions: `{report['actual_status_transition_count']}`  ",
        "",
        "Interaction event/pattern triggers receive diagnostic-only source identity "
        "shaping before the isolated write so the Store commit path can be exercised; "
        "this does not prove the unshaped extraction-to-dedupe path.",
        "",
        "| Case | Operation | Expected targets | Planned targets | Rules | "
        "Expected Store outcome | Passed |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in report["rows"]:
        lines.append(
            f"| {row['case_id']} | {row['operation']} | "
            f"{', '.join(row['expected_fixture_target_ids']) or '-'} | "
            f"{', '.join(row['planned_fixture_target_ids']) or '-'} | "
            f"{', '.join(row['planned_rule_names']) or '-'} | "
            f"{row['passed_store_application']} | {row['passed']} |"
        )
    return "\n".join(lines) + "\n"


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return round(float(numerator) / float(denominator), 4) if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


__all__ = [
    "REPORT_VERSION",
    "evaluate_memory_lifecycle",
    "evaluate_memory_lifecycle_integration",
    "evaluate_memory_lifecycle_v1",
    "load_memory_lifecycle_v1_cases",
    "render_memory_lifecycle_integration_diagnostic",
    "render_memory_lifecycle_policy_review",
    "render_memory_lifecycle_v1_report",
]

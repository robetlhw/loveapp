"""Relation Resolution V1 contract evaluator.

This module deliberately exercises the production ``resolve_claim_relation``
function directly.  It does not reimplement relation rules and it never
changes production memory state as part of the layer baseline.  A small,
deterministic write-path diagnostic is provided separately using an isolated
in-memory store.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loveapp.adapters.memory.in_memory import InMemoryMemoryStore
from loveapp.application.memory_relations import resolve_claim_relation
from loveapp.domain.memory import (
    AdmissionDecision,
    ClaimRelation,
    MemoryCandidate,
    MemoryItem,
    MemoryStatus,
    memory_dedupe_key,
)
from loveapp.domain.memory_write import MemoryWriteBatch, MemoryWriteOperation

REPORT_VERSION = "memory-relation-v1"
EXPECTED_CASE_COUNT = 72
EXPECTED_STRICT_CASE_COUNT = 64
EXPECTED_POLICY_REVIEW_CASE_COUNT = 8
RELATIONS = tuple(item.value for item in ClaimRelation)


class RelationEvaluationError(ValueError):
    """Raised when a relation fixture cannot be converted to domain models."""


def load_memory_relation_v1_cases(path: Path) -> list[dict[str, Any]]:
    """Load and validate the versioned Relation V1 JSONL contract."""

    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RelationEvaluationError(f"dataset is not UTF-8 JSONL: {path}") from exc
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RelationEvaluationError(
                f"invalid JSON at line {line_number}: {exc}"
            ) from exc
        if not isinstance(case, dict):
            raise RelationEvaluationError(f"line {line_number} must contain an object")
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise RelationEvaluationError(f"line {line_number} requires case_id")
        if case_id in seen:
            raise RelationEvaluationError(f"duplicate case_id: {case_id}")
        seen.add(case_id)
        if not isinstance(case.get("candidate"), dict):
            raise RelationEvaluationError(f"{case_id} requires candidate")
        if not isinstance(case.get("active_memories"), list):
            raise RelationEvaluationError(f"{case_id} requires active_memories")
        expected = case.get("expected")
        if not isinstance(expected, dict) or not isinstance(expected.get("relation"), str):
            raise RelationEvaluationError(f"{case_id} requires expected.relation")
        try:
            ClaimRelation(expected["relation"])
            MemoryStatus(case.get("incoming_status", MemoryStatus.CONFIRMED))
        except ValueError as exc:
            raise RelationEvaluationError(f"{case_id} contains an invalid enum") from exc
        cases.append(case)

    expected_ids = [f"REL-{index:03d}" for index in range(1, EXPECTED_CASE_COUNT + 1)]
    actual_ids = [case["case_id"] for case in cases]
    if actual_ids != expected_ids:
        raise RelationEvaluationError(
            f"case ids differ from REL-001..REL-{EXPECTED_CASE_COUNT:03d}"
        )
    if len(cases) != EXPECTED_CASE_COUNT:
        raise RelationEvaluationError(f"expected {EXPECTED_CASE_COUNT} cases, got {len(cases)}")
    strict_count = sum(case.get("contract_status") == "EXACT" for case in cases)
    review_count = sum(case.get("contract_status") == "POLICY_REVIEW" for case in cases)
    if (
        strict_count != EXPECTED_STRICT_CASE_COUNT
        or review_count != EXPECTED_POLICY_REVIEW_CASE_COUNT
    ):
        raise RelationEvaluationError(
            f"expected {EXPECTED_STRICT_CASE_COUNT} EXACT and "
            f"{EXPECTED_POLICY_REVIEW_CASE_COUNT} POLICY_REVIEW cases, got "
            f"{strict_count}/{review_count}"
        )
    return cases


def evaluate_memory_relation_v1(
    path: Path,
    *,
    case_id: str | None = None,
    slice_name: str | None = None,
    contract_status: str | None = None,
    fail_on_error: bool = False,
) -> dict[str, Any]:
    """Run the Relation V1 layer baseline against the production resolver."""

    raw = path.read_bytes()
    all_cases = load_memory_relation_v1_cases(path)
    cases = [
        case
        for case in all_cases
        if (case_id is None or case["case_id"] == case_id)
        and (slice_name is None or case.get("slice") == slice_name)
        and (contract_status is None or case.get("contract_status") == contract_status)
    ]
    if not cases:
        filters = {"case": case_id, "slice": slice_name, "contract_status": contract_status}
        raise ValueError(f"no Relation V1 cases match filters: {filters}")

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
    metrics = _summarize(strict_rows)
    policy_review = _policy_review(review_rows)
    return {
        "version": REPORT_VERSION,
        "dataset": str(path),
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "case_filter": {
            "case_id": case_id,
            "slice": slice_name,
            "contract_status": contract_status,
        },
        "case_count": len(rows),
        "strict_case_count": len(strict_rows),
        "strict_passed_case_count": sum(row["passed"] for row in strict_rows),
        "strict_failed_case_count": sum(not row["passed"] for row in strict_rows),
        "policy_review_case_count": len(review_rows),
        "metrics": metrics,
        "by_slice": _group_metrics(strict_rows, "slice"),
        "by_difficulty": _group_metrics(strict_rows, "difficulty"),
        "by_relation": _group_metrics(strict_rows, "expected_relation"),
        "cases": rows,
        "policy_review": policy_review,
        "status": (
            "BASELINE_PASS_POLICY_REVIEW_PENDING"
            if not strict_rows or not any(not row["passed"] for row in strict_rows)
            else "BASELINE_DRIFT_REQUIRES_REVIEW"
        ),
        "production_relation_function": (
            "loveapp.application.memory_relations.resolve_claim_relation"
        ),
        "production_mutation_permitted": False,
    }


def _evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    case_id = case["case_id"]
    candidate = _candidate_from_spec(case["candidate"])
    user_id = "relation-eval-user"
    relationship_id = "relation-eval-relationship"
    active = [
        _memory_from_spec(
            raw,
            user_id=user_id,
            relationship_id=relationship_id,
            case_id=case_id,
        )
        for raw in case["active_memories"]
    ]
    incoming_status = MemoryStatus(case.get("incoming_status", MemoryStatus.CONFIRMED))
    expected = case["expected"]
    expected_relation = ClaimRelation(expected["relation"])
    expected_targets = list(expected.get("target_memory_ids", []))
    before = _snapshot(candidate, active)
    resolution = resolve_claim_relation(
        candidate,
        active,
        incoming_status=incoming_status,
    )
    after = _snapshot(candidate, active)
    actual_targets = list(resolution.target_memory_ids)
    checks = {
        "relation": resolution.relation == expected_relation,
        "rule_name": resolution.rule_name == expected.get("rule_name"),
        "reason": resolution.reason == expected.get("reason"),
        "target_exact_match": actual_targets == expected_targets,
        "target_set_match": set(actual_targets) == set(expected_targets),
        "input_unchanged": before == after,
    }
    failures = [name for name, passed in checks.items() if not passed]
    return {
        "case_id": case_id,
        "slice": case.get("slice"),
        "difficulty": case.get("difficulty"),
        "contract_status": case.get("contract_status", "EXACT"),
        "incoming_status": incoming_status.value,
        "candidate": candidate.model_dump(mode="json"),
        "active_memory_ids": [item.id for item in active],
        "expected_relation": expected_relation.value,
        "actual_relation": resolution.relation.value,
        "expected_rule_name": expected.get("rule_name"),
        "actual_rule_name": resolution.rule_name,
        "expected_reason": expected.get("reason"),
        "actual_reason": resolution.reason,
        "expected_target_memory_ids": expected_targets,
        "actual_target_memory_ids": actual_targets,
        "checks": checks,
        "failures": failures,
        "passed": not failures,
        "error_taxonomy": _error_taxonomy(case, resolution, checks),
        "input_mutated": before != after,
        "note": case.get("note", ""),
        "trace": {
            "candidate_dedupe_key": memory_dedupe_key(candidate),
            "active_dedupe_keys": {item.id: item.dedupe_key for item in active},
            "active_statuses": {item.id: item.status.value for item in active},
            "incoming_status": incoming_status.value,
            "relation_target_ids": actual_targets,
        },
    }


def _error_row(case: dict[str, Any], error: Exception) -> dict[str, Any]:
    return {
        "case_id": case.get("case_id"),
        "slice": case.get("slice"),
        "difficulty": case.get("difficulty"),
        "contract_status": case.get("contract_status", "EXACT"),
        "incoming_status": case.get("incoming_status", MemoryStatus.CONFIRMED),
        "expected_relation": (case.get("expected") or {}).get("relation"),
        "expected_rule_name": (case.get("expected") or {}).get("rule_name"),
        "expected_reason": (case.get("expected") or {}).get("reason"),
        "expected_target_memory_ids": (case.get("expected") or {}).get(
            "target_memory_ids", []
        ),
        "actual_relation": None,
        "actual_rule_name": None,
        "actual_reason": None,
        "actual_target_memory_ids": [],
        "checks": {"execution": False},
        "failures": ["execution"],
        "passed": False,
        "error": f"{type(error).__name__}: {error}",
        "error_taxonomy": "EVALUATOR_BUG",
        "input_mutated": False,
    }


def _candidate_from_spec(spec: dict[str, Any]) -> MemoryCandidate:
    try:
        return MemoryCandidate.model_validate(spec)
    except Exception as exc:
        raise RelationEvaluationError("invalid candidate spec") from exc


def _memory_from_spec(
    spec: dict[str, Any],
    *,
    user_id: str,
    relationship_id: str,
    case_id: str,
) -> MemoryItem:
    memory_id = spec.get("id")
    if not isinstance(memory_id, str) or not memory_id:
        raise RelationEvaluationError(f"{case_id} active memory requires id")
    candidate_spec = spec.get("candidate")
    if not isinstance(candidate_spec, dict):
        raise RelationEvaluationError(f"{case_id}/{memory_id} requires candidate")
    candidate = _candidate_from_spec(candidate_spec)
    data = candidate.model_dump()
    data.update(
        {
            "id": memory_id,
            "user_id": user_id,
            "relationship_id": relationship_id,
            "status": spec.get("status", MemoryStatus.CONFIRMED),
            "source_message_id": spec.get("source_message_id"),
            "created_at": spec.get("created_at") or "2026-08-01T10:00:00Z",
            "updated_at": spec.get("updated_at") or "2026-09-01T10:00:00Z",
            "last_seen_at": spec.get("last_seen_at"),
            "last_used_at": spec.get("last_used_at"),
            # The fixture intentionally does not encode identity.  Always use
            # the production key so identity drift remains observable.
            "dedupe_key": memory_dedupe_key(candidate),
        }
    )
    try:
        return MemoryItem.model_validate(data)
    except Exception as exc:
        raise RelationEvaluationError(f"invalid active memory {case_id}/{memory_id}") from exc


def _snapshot(candidate: MemoryCandidate, active: list[MemoryItem]) -> str:
    return json.dumps(
        {
            "candidate": candidate.model_dump(mode="json"),
            "active": [item.model_dump(mode="json") for item in active],
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _error_taxonomy(
    case: dict[str, Any],
    resolution: Any,
    checks: dict[str, bool],
) -> str | None:
    if all(checks.values()):
        return None
    expected = case["expected"]
    expected_relation = expected.get("relation")
    if not checks["target_set_match"] and checks["relation"]:
        return "TARGET_SELECTION_BUG"
    if case.get("slice") == "same_keeper" and not checks["target_set_match"]:
        return "KEEPER_RANK_BUG"
    if case.get("slice") == "contact_transition":
        return "STATE_IDENTITY_DRIFT"
    if (
        case.get("slice") == "custom_uncertain"
        and (case.get("candidate") or {}).get("kind") == "preference"
    ):
        return "PREFERENCE_NORMALIZATION_DRIFT"
    if expected_relation != resolution.relation.value:
        if case.get("slice") in {"same_dedupe", "same_keeper"}:
            return "DEDUPE_IDENTITY_DRIFT"
        if case.get("slice") == "preference_update":
            return "PREFERENCE_NORMALIZATION_DRIFT"
        return "RELATION_CLASSIFICATION_BUG"
    if not checks["rule_name"] or not checks["reason"]:
        return "POLICY_SNAPSHOT_DRIFT"
    return "EVALUATOR_BUG"


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    relation_confusion: Counter[str] = Counter()
    relation_support: Counter[str] = Counter()
    relation_correct: Counter[str] = Counter()
    target_predicted = 0
    target_expected = 0
    target_correct = 0
    target_exact = 0
    target_set = 0
    rule_correct = 0
    reason_correct = 0
    for row in rows:
        expected = str(row.get("expected_relation"))
        actual = str(row.get("actual_relation") or "missing")
        relation_confusion[f"{expected}|{actual}"] += 1
        relation_support[expected] += 1
        relation_correct[expected] += int(expected == actual)
        target_ids = set(row.get("actual_target_memory_ids", []))
        expected_ids = set(row.get("expected_target_memory_ids", []))
        target_predicted += len(target_ids)
        target_expected += len(expected_ids)
        target_correct += len(target_ids & expected_ids)
        target_exact += int(row["checks"].get("target_exact_match", False))
        target_set += int(row["checks"].get("target_set_match", False))
        rule_correct += int(row["checks"].get("rule_name", False))
        reason_correct += int(row["checks"].get("reason", False))

    per_relation = {}
    for relation in RELATIONS:
        support = relation_support[relation]
        tp = relation_correct[relation]
        predicted = sum(
            1
            for row in rows
            if row.get("actual_relation") == relation
        )
        per_relation[relation] = {
            "support": support,
            "correct": tp,
            "accuracy": _ratio(tp, support),
            "precision": _ratio(tp, predicted),
            "recall": _ratio(tp, support),
        }

    safety = _safety_metrics(rows)
    return {
        "case_count": total,
        "passed_case_count": sum(row["passed"] for row in rows),
        "failed_case_count": sum(not row["passed"] for row in rows),
        "relation_accuracy": _ratio(sum(relation_correct.values()), total),
        "rule_name_accuracy": _ratio(rule_correct, total),
        "reason_accuracy": _ratio(reason_correct, total),
        "target_exact_match_accuracy": _ratio(target_exact, total),
        "target_set_accuracy": _ratio(target_set, total),
        "target_micro_precision": _ratio(target_correct, target_predicted),
        "target_micro_recall": _ratio(target_correct, target_expected),
        "target_micro_f1": _f1(
            _ratio(target_correct, target_predicted),
            _ratio(target_correct, target_expected),
        ),
        "per_relation": per_relation,
        "relation_confusion": dict(sorted(relation_confusion.items())),
        "safety": safety,
        "error_taxonomy": dict(
            Counter(
                row["error_taxonomy"]
                for row in rows
                if row.get("error_taxonomy")
            )
        ),
    }


def _safety_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def slice_rows(name: str) -> list[dict[str, Any]]:
        return [row for row in rows if row.get("slice") == name]

    same_keeper = slice_rows("same_keeper")
    state_updates = [
        row
        for row in rows
        if row.get("expected_relation") == ClaimRelation.UPDATE.value
        and row.get("incoming_status") == MemoryStatus.CONFIRMED.value
        and row.get("slice") in {"state_update", "interaction_state_conflict", "contact_transition"}
    ]
    proposed_state = [
        row
        for row in rows
        if row.get("expected_relation") == ClaimRelation.CONTRADICTION.value
        and row.get("incoming_status") == MemoryStatus.PROPOSED.value
        and row.get("slice")
        in {
            "state_conflict",
            "interaction_state_conflict",
            "contact_transition",
            "preference_conflict",
        }
    ]
    proposed_overwrite: list[dict[str, Any]] = []
    # Inspect the fixture directly for status protection rather than infer it
    # from a relation label.  This remains a read-only governance diagnostic.
    for row in rows:
        if row.get("incoming_status") != MemoryStatus.PROPOSED.value:
            continue
        if row.get("actual_relation") != ClaimRelation.UPDATE.value:
            continue
        row_targets = set(row.get("actual_target_memory_ids", []))
        confirmed_ids = {
            item_id
            for item_id, status in (row.get("trace", {}).get("active_statuses") or {}).items()
            if status == MemoryStatus.CONFIRMED.value
        }
        if row_targets & confirmed_ids:
            proposed_overwrite.append(row)

    custom_rows = [
        row
        for row in rows
        if row.get("slice") in {"custom_uncertain", "custom_target_cap"}
    ]
    cross_subject = [
        row
        for row in rows
        if row.get("slice") in {"cross_subject", "state_cross_subject"}
    ]
    unrelated_rows = [
        row
        for row in rows
        if row.get("expected_relation") == ClaimRelation.UNRELATED.value
    ]
    return {
        "same_keeper_accuracy": _ratio(
            sum(row["checks"].get("target_set_match", False) for row in same_keeper),
            len(same_keeper),
        ),
        "confirmed_state_update_recall": _ratio(
            sum(row["passed"] for row in state_updates), len(state_updates)
        ),
        "proposed_state_contradiction_recall": _ratio(
            sum(row["passed"] for row in proposed_state), len(proposed_state)
        ),
        "proposed_overwrites_confirmed_violation_rate": _ratio(
            len(proposed_overwrite),
            sum(row.get("incoming_status") == MemoryStatus.PROPOSED.value for row in rows),
        ),
        "contact_transition_accuracy": _slice_accuracy(rows, "contact_transition"),
        "preference_polarity_accuracy": _rule_accuracy(rows, "preference_polarity_change"),
        "single_value_preference_accuracy": _rule_accuracy(
            rows, "single_value_preference_dimension"
        ),
        "preference_hierarchy_accuracy": _rule_accuracy(rows, "preference_hierarchy"),
        "custom_uncertain_recall": _ratio(
            sum(row.get("actual_relation") == ClaimRelation.UNCERTAIN.value for row in custom_rows),
            len(custom_rows),
        ),
        "custom_target_cap_accuracy": _target_cap_accuracy(rows),
        "cross_subject_false_link_rate": _ratio(
            sum(bool(row.get("actual_target_memory_ids")) for row in cross_subject),
            len(cross_subject),
        ),
        "unrelated_false_link_rate": _ratio(
            sum(
                bool(row.get("actual_target_memory_ids"))
                for row in unrelated_rows
            ),
            len(unrelated_rows),
        ),
    }


def _slice_accuracy(rows: list[dict[str, Any]], name: str) -> float | None:
    selected = [row for row in rows if row.get("slice") == name]
    return _ratio(sum(row["passed"] for row in selected), len(selected))


def _rule_accuracy(rows: list[dict[str, Any]], rule_name: str) -> float | None:
    selected = [row for row in rows if row.get("expected_rule_name") == rule_name]
    return _ratio(sum(row["passed"] for row in selected), len(selected))


def _target_cap_accuracy(rows: list[dict[str, Any]]) -> float | None:
    selected = [row for row in rows if row.get("slice") == "custom_target_cap"]
    return _ratio(
        sum(len(row.get("actual_target_memory_ids", [])) <= 5 for row in selected),
        len(selected),
    )


def _group_metrics(rows: list[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(field) or "unknown")].append(row)
    return {
        key: {
            "case_count": len(group),
            "passed_case_count": sum(row["passed"] for row in group),
            "relation_accuracy": _ratio(
                sum(row["checks"].get("relation", False) for row in group), len(group)
            ),
            "target_set_accuracy": _ratio(
                sum(row["checks"].get("target_set_match", False) for row in group),
                len(group),
            ),
        }
        for key, group in sorted(groups.items())
    }


def _policy_review(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    categories = {
        "REL-065": ("DEAD_OR_SHADOWED_CODE", "KEEP_CURRENT"),
        "REL-066": ("DEDUPE_IDENTITY_DRIFT", "NEEDS_PRODUCT_DECISION"),
        "REL-067": ("GOLD_POLICY_AMBIGUITY", "KEEP_CURRENT"),
        "REL-068": ("GOLD_POLICY_AMBIGUITY", "NEEDS_PRODUCT_DECISION"),
        "REL-069": ("GOLD_POLICY_AMBIGUITY", "NEEDS_PRODUCT_DECISION"),
        "REL-070": ("DOWNSTREAM_INTEGRATION_ISSUE", "KEEP_CURRENT"),
        "REL-071": ("DEAD_OR_SHADOWED_CODE", "KEEP_CURRENT"),
        "REL-072": ("CALLER_ACTIVE_SET_VIOLATION", "UPSTREAM_CONTRACT_ISSUE"),
    }
    result: list[dict[str, Any]] = []
    for row in rows:
        category, recommendation = categories.get(
            row["case_id"], ("GOLD_POLICY_AMBIGUITY", "NEEDS_PRODUCT_DECISION")
        )
        result.append(
            {
                "case_id": row["case_id"],
                "expected": {
                    "relation": row.get("expected_relation"),
                    "rule_name": row.get("expected_rule_name"),
                    "target_memory_ids": row.get("expected_target_memory_ids", []),
                },
                "actual": {
                    "relation": row.get("actual_relation"),
                    "rule_name": row.get("actual_rule_name"),
                    "reason": row.get("actual_reason"),
                    "target_memory_ids": row.get("actual_target_memory_ids", []),
                },
                "current_code_path": "resolve_claim_relation",
                "policy_classification": category,
                "recommendation": recommendation,
                "note": next(
                    (
                        str(case.get("note") or "")
                        for case in rows
                        if case.get("case_id") == row["case_id"]
                    ),
                    "",
                ),
            }
        )
    return result


async def evaluate_memory_relation_integration(
    path: Path,
    *,
    case_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Run deterministic relation decisions through the Store UoW.

    This diagnostic uses an isolated ``InMemoryMemoryStore``.  It intentionally
    does not call an LLM or any production Store, and it reports writes only as
    local diagnostic evidence.
    """

    cases = load_memory_relation_v1_cases(path)
    selected = case_ids or [
        "REL-001",
        "REL-003",
        "REL-011",
        "REL-018",
        "REL-023",
        "REL-030",
        "REL-033",
        "REL-039",
        "REL-043",
        "REL-044",
        "REL-053",
        "REL-054",
        # Regression coverage for the two Relation V1 remediation roots.
        "REL-028",
        "REL-051",
    ]
    by_id = {case["case_id"]: case for case in cases}
    missing = [case_id for case_id in selected if case_id not in by_id]
    if missing:
        raise ValueError(f"unknown Relation integration cases: {missing}")
    rows: list[dict[str, Any]] = []
    now = datetime(2026, 9, 2, 10, tzinfo=UTC)
    user_id = "relation-integration-user"
    relationship_id = "relation-integration-relationship"
    for selected_id in selected:
        case = by_id[selected_id]
        store = InMemoryMemoryStore(clock=lambda: now)
        fixture_to_actual: dict[str, str] = {}
        # Seed each fixture active memory through the Store's normal save path.
        for raw_memory in case["active_memories"]:
            candidate = _candidate_from_spec(raw_memory["candidate"])
            saved = await store.save_memory(
                user_id=user_id,
                relationship_id=relationship_id,
                candidate=candidate,
                source_message_id=f"{selected_id}-seed-{raw_memory['id']}",
                status=MemoryStatus(raw_memory.get("status", MemoryStatus.CONFIRMED)),
            )
            fixture_to_actual[raw_memory["id"]] = saved.item.id
        active = await store.list_memories(
            user_id=user_id,
            relationship_id=relationship_id,
            limit=100,
        )
        incoming = _candidate_from_spec(case["candidate"])
        incoming_status = MemoryStatus(case.get("incoming_status", MemoryStatus.CONFIRMED))
        resolution = resolve_claim_relation(incoming, active, incoming_status=incoming_status)
        mapped_targets = [
            fixture_to_actual.get(target, target)
            for target in resolution.target_memory_ids
        ]
        operation_candidate = incoming.model_copy(
            update={
                "admission_score": 0.95,
                "admission_decision": (
                    AdmissionDecision.CONFIRM
                    if incoming_status == MemoryStatus.CONFIRMED
                    else AdmissionDecision.PROPOSE
                ),
                "claim_relation": resolution.relation,
            }
        )
        batch = MemoryWriteBatch(
            source_message_id=f"{selected_id}-incoming",
            operations=[
                MemoryWriteOperation(
                    candidate=operation_candidate,
                    status=incoming_status,
                    relation=resolution.relation,
                    target_memory_ids=mapped_targets,
                    rule_name=resolution.rule_name,
                    reason=resolution.reason,
                )
            ],
        )
        before = await store.list_memories(
            user_id=user_id,
            relationship_id=relationship_id,
            limit=100,
        )
        committed = await store.commit_memory_batch(
            user_id=user_id,
            relationship_id=relationship_id,
            batch=batch,
        )
        after = await store.list_memories(
            user_id=user_id,
            relationship_id=relationship_id,
            limit=100,
        )
        audits = await store.list_transition_audits(
            user_id=user_id,
            relationship_id=relationship_id,
            source_message_id=f"{selected_id}-incoming",
        )
        operation = batch.operations[0]
        rows.append(
            {
                "case_id": selected_id,
                "expected_relation": case["expected"]["relation"],
                "claim_relation": resolution.relation.value,
                "target_memory_ids": resolution.target_memory_ids,
                "actual_target_memory_ids": mapped_targets,
                "rule_name": resolution.rule_name,
                "reason": resolution.reason,
                "planned_action": _planned_action(resolution.relation),
                "planned_status": operation.status.value,
                "lifecycle_review_required": operation_candidate.lifecycle_review_required,
                "store_write_attempted": True,
                "store_mutation_permitted": False,
                "isolated_store_mutation": True,
                "saved_memory_ids": [result.item.id for result in committed.saved],
                "updated_memory_ids": committed.updated_memory_ids,
                "final_memory_statuses": {item.id: item.status.value for item in after},
                "before_memory_count": len(before),
                "after_memory_count": len(after),
                "transition_audits": [audit.model_dump(mode="json") for audit in audits],
                "passed_relation": resolution.relation.value == case["expected"]["relation"],
            }
        )
        await store.aclose()
    return {
        "evaluation": "memory_relation_v1_integration_diagnostic",
        "dataset": str(path),
        "case_count": len(rows),
        "passed_relation_count": sum(row["passed_relation"] for row in rows),
        "production_store_mutation_permitted": False,
        "isolated_in_memory_store_mutation": True,
        "model_calls_permitted": False,
        "store_write_attempt_count": sum(row["store_write_attempted"] for row in rows),
        "transition_audit_count": sum(bool(row["transition_audits"]) for row in rows),
        "rows": rows,
    }


def _planned_action(relation: ClaimRelation) -> str:
    if relation == ClaimRelation.SAME:
        return "merge"
    if relation == ClaimRelation.UPDATE:
        return "replace"
    if relation == ClaimRelation.UNRELATED or relation == ClaimRelation.UNCERTAIN:
        return "add"
    return "add"


def render_memory_relation_v1_report(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# Memory Relation V1 Baseline Report",
        "",
        f"- Dataset: `{report['dataset']}`",
        f"- Dataset SHA-256: `{report['dataset_sha256']}`",
        f"- Cases evaluated: `{report['case_count']}`",
        f"- Strict cases: `{report['strict_case_count']}`",
        f"- Production Store mutation permitted: `{report['production_mutation_permitted']}`",
        f"- Status: **{report['status']}**",
        "",
        "## Strict Metrics",
        "",
        "| Metric | Result |",
        "|---|---:|",
    ]
    for name in (
        "case_count",
        "passed_case_count",
        "failed_case_count",
        "relation_accuracy",
        "rule_name_accuracy",
        "reason_accuracy",
        "target_exact_match_accuracy",
        "target_set_accuracy",
        "target_micro_precision",
        "target_micro_recall",
        "target_micro_f1",
    ):
        lines.append(f"| `{name}` | {_fmt(metrics.get(name))} |")
    lines.extend(
        [
            "",
            "## Relation Precision / Recall",
            "",
            "| Relation | Support | Precision | Recall |",
            "|---|---:|---:|---:|",
        ]
    )
    for relation, detail in metrics["per_relation"].items():
        lines.append(
            f"| `{relation}` | {detail['support']} | "
            f"{_fmt(detail['precision'])} | {_fmt(detail['recall'])} |"
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
        lines.append(f"| `{name}` | {_fmt(value)} |")
    lines.extend(
        [
            "",
            "## By Slice",
            "",
            "| Slice | Cases | Passed | Relation accuracy | Target set accuracy |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for name, detail in report.get("by_slice", {}).items():
        lines.append(
            f"| `{name}` | {detail['case_count']} | {detail['passed_case_count']} | "
            f"{_fmt(detail['relation_accuracy'])} | {_fmt(detail['target_set_accuracy'])} |"
        )
    lines.extend(
        [
            "",
            "## Error Taxonomy",
            "",
            "| Category | Count |",
            "|---|---:|",
        ]
    )
    for name, count in metrics.get("error_taxonomy", {}).items():
        lines.append(f"| `{name}` | {count} |")
    lines.extend(
        [
            "",
            "## Failed Strict Cases",
            "",
            "| Case | Expected | Actual | Rule | Category |",
            "|---|---|---|---|---|",
        ]
    )
    failures = [
        row
        for row in report["cases"]
        if row.get("contract_status") == "EXACT" and not row.get("passed")
    ]
    if failures:
        for row in failures:
            lines.append(
                f"| {row['case_id']} | {row.get('expected_relation')} | "
                f"{row.get('actual_relation')} | {row.get('actual_rule_name')} | "
                f"{row.get('error_taxonomy')} |"
            )
    else:
        lines.append("| none | - | - | - | - |")
    lines.extend(
        [
            "",
            "## Contract Interpretation",
            "",
            "The baseline calls the production relation resolver directly and does not "
            "reimplement its rules. Fixture identity is never trusted for `dedupe_key`; "
            "the production `memory_dedupe_key()` is used so identity drift remains visible.",
            "",
            "POLICY_REVIEW cases are observe-only and excluded from strict scoring. "
            "No relation, lifecycle, admission, normalization, or Store production policy "
            "was changed by this evaluation.",
            "",
        ]
    )
    return "\n".join(lines)


def render_memory_relation_policy_review(report: dict[str, Any]) -> str:
    lines = [
        "# Memory Relation V1 Policy Review",
        "",
        "These cases are observe-only and excluded from strict baseline accuracy.",
        "Production relation policy was not changed.",
        "",
        "| Case | Expected | Actual | Classification | Recommendation |",
        "|---|---|---|---|---|",
    ]
    for row in report.get("policy_review", []):
        expected = row["expected"]
        actual = row["actual"]
        lines.append(
            f"| {row['case_id']} | {expected['relation']} | {actual['relation']} | "
            f"{row['policy_classification']} | {row['recommendation']} |"
        )
    lines.extend(["", "## Per-Case Diagnostics", ""])
    for row in report.get("policy_review", []):
        lines.extend(
            [
                f"### {row['case_id']}",
                "",
                f"- Current code path: `{row['current_code_path']}`",
                f"- Expected: `{json.dumps(row['expected'], ensure_ascii=False)}`",
                f"- Actual: `{json.dumps(row['actual'], ensure_ascii=False)}`",
                f"- Classification: `{row['policy_classification']}`",
                f"- Recommendation: `{row['recommendation']}`",
                f"- Note: {row['note'] or 'none'}",
                "",
            ]
        )
    return "\n".join(lines)


def render_memory_relation_integration_diagnostic(report: dict[str, Any]) -> str:
    lines = [
        "# Memory Relation V1 Integration Diagnostic",
        "",
        "Deterministic candidates were evaluated by the production relation resolver "
        "and committed through an isolated InMemoryMemoryStore. This is diagnostic only; "
        "no production Store mutation or model call is permitted.",
        "",
        f"Cases: `{report['case_count']}`  ",
        f"Relation matches: `{report['passed_relation_count']}`  ",
        f"Store writes attempted: `{report['store_write_attempt_count']}`  ",
        f"Transition audits: `{report['transition_audit_count']}`  ",
        "",
        "| Case | Expected | Relation | Targets | Action | Final statuses | Passed |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in report.get("rows", []):
        statuses = ", ".join(
            f"{key}:{value}" for key, value in row["final_memory_statuses"].items()
        )
        lines.append(
            f"| {row['case_id']} | {row['expected_relation']} | {row['claim_relation']} | "
            f"{', '.join(row['target_memory_ids']) or '-'} | {row['planned_action']} | "
            f"{statuses or '-'} | {row['passed_relation']} |"
        )
    return "\n".join(lines) + "\n"


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return round(float(numerator) / float(denominator), 4) if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


__all__ = [
    "REPORT_VERSION",
    "evaluate_memory_relation_integration",
    "evaluate_memory_relation_v1",
    "load_memory_relation_v1_cases",
    "render_memory_relation_integration_diagnostic",
    "render_memory_relation_policy_review",
    "render_memory_relation_v1_report",
]

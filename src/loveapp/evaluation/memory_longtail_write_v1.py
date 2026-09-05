"""Long-tail Memory Write / Update V1 deterministic evaluation.

This evaluator is a diagnostic boundary around the existing memory relation and
write contracts.  It never changes the production Store and never calls an LLM.
The frozen fixture candidate is sent to the production deterministic relation
resolver, and selected results are then applied to isolated in-memory Stores so
that status, supersession, idempotency, and transition-audit behavior remain
observable.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loveapp.adapters.memory.in_memory import InMemoryMemoryStore
from loveapp.application.memory_relations import (
    ClaimRelationResolution,
    resolve_claim_relation,
)
from loveapp.domain.memory import (
    AdmissionDecision,
    ClaimRelation,
    MemoryCandidate,
    MemoryItem,
    MemoryStatus,
    memory_dedupe_key,
)
from loveapp.domain.memory_write import MemoryWriteBatch, MemoryWriteOperation

REPORT_VERSION = "memory-longtail-write-v1"
EXPECTED_CASE_COUNT = 112
EXPECTED_STRICT_CASE_COUNT = 96
EXPECTED_POLICY_REVIEW_CASE_COUNT = 16
STRICT_SLICES = (
    "same_rephrase",
    "complementary_detail",
    "sustained_update",
    "contradiction_authority_guard",
    "unrelated_same_subject_kind",
    "cross_subject",
    "temporal_event_identity",
    "event_vs_pattern",
    "custom_canonical_coexistence",
    "authority_status_safety",
    "multi_memory_targeting",
    "safe_uncertain_ambiguity",
)
WRITE_ACTIONS = frozenset(
    {"add_without_supersede", "merge_or_refresh", "supersede_and_add"}
)
RELATIONS = tuple(item.value for item in ClaimRelation)


class LongTailWriteEvaluationError(ValueError):
    """Raised when the versioned Golden Set is malformed."""


def load_memory_longtail_write_v1_cases(path: Path) -> list[dict[str, Any]]:
    """Load and validate the immutable 112-case JSONL contract."""

    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise LongTailWriteEvaluationError(
            f"dataset is not UTF-8 JSONL: {path}"
        ) from exc

    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LongTailWriteEvaluationError(
                f"invalid JSON at line {line_number}: {exc}"
            ) from exc
        if not isinstance(case, dict):
            raise LongTailWriteEvaluationError(
                f"line {line_number} must contain an object"
            )
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise LongTailWriteEvaluationError(
                f"line {line_number} requires case_id"
            )
        if case_id in seen:
            raise LongTailWriteEvaluationError(f"duplicate case_id: {case_id}")
        for field in ("slice", "contract_status", "incoming_candidate", "existing_memories"):
            if field not in case:
                raise LongTailWriteEvaluationError(f"{case_id} requires {field}")
        if not isinstance(case["incoming_candidate"], dict):
            raise LongTailWriteEvaluationError(
                f"{case_id}.incoming_candidate must be an object"
            )
        if not isinstance(case["existing_memories"], list):
            raise LongTailWriteEvaluationError(
                f"{case_id}.existing_memories must be a list"
            )
        expected = case.get("expected")
        if not isinstance(expected, dict):
            raise LongTailWriteEvaluationError(f"{case_id} requires expected")
        if not isinstance(expected.get("relation"), str):
            raise LongTailWriteEvaluationError(
                f"{case_id} requires expected.relation"
            )
        if not isinstance(expected.get("target_memory_ids"), list):
            raise LongTailWriteEvaluationError(
                f"{case_id} requires expected.target_memory_ids"
            )
        outcome = expected.get("store_outcome")
        if not isinstance(outcome, dict):
            raise LongTailWriteEvaluationError(
                f"{case_id} requires expected.store_outcome"
            )
        if outcome.get("write_action") not in WRITE_ACTIONS:
            raise LongTailWriteEvaluationError(
                f"{case_id} contains an invalid store write action"
            )
        try:
            ClaimRelation(expected["relation"])
            MemoryStatus(case.get("incoming_status", MemoryStatus.CONFIRMED))
        except (KeyError, ValueError) as exc:
            raise LongTailWriteEvaluationError(
                f"{case_id} contains an invalid enum"
            ) from exc
        seen.add(case_id)
        cases.append(case)

    expected_ids = [
        f"LTW-{index:03d}" for index in range(1, EXPECTED_CASE_COUNT + 1)
    ]
    actual_ids = [case["case_id"] for case in cases]
    if actual_ids != expected_ids:
        raise LongTailWriteEvaluationError(
            "case ids differ from LTW-001..LTW-112"
        )
    strict_count = sum(case.get("contract_status") == "EXACT" for case in cases)
    review_count = sum(
        case.get("contract_status") == "POLICY_REVIEW" for case in cases
    )
    if len(cases) != EXPECTED_CASE_COUNT:
        raise LongTailWriteEvaluationError(
            f"expected {EXPECTED_CASE_COUNT} cases, got {len(cases)}"
        )
    if (
        strict_count != EXPECTED_STRICT_CASE_COUNT
        or review_count != EXPECTED_POLICY_REVIEW_CASE_COUNT
    ):
        raise LongTailWriteEvaluationError(
            f"expected {EXPECTED_STRICT_CASE_COUNT} EXACT and "
            f"{EXPECTED_POLICY_REVIEW_CASE_COUNT} POLICY_REVIEW cases, got "
            f"{strict_count}/{review_count}"
        )
    strict_slices = Counter(
        case["slice"] for case in cases if case.get("contract_status") == "EXACT"
    )
    if any(strict_slices[name] != 8 for name in STRICT_SLICES):
        raise LongTailWriteEvaluationError(
            f"each strict slice must contain eight cases: {dict(strict_slices)}"
        )
    return cases


def evaluate_memory_longtail_write_v1(
    path: Path,
    *,
    case_id: str | None = None,
    slice_name: str | None = None,
    relation: str | None = None,
    length_band: str | None = None,
    contract_status: str | None = None,
    live_subset: bool | None = None,
    fail_on_error: bool = False,
) -> dict[str, Any]:
    """Run the deterministic production relation baseline.

    ``user_text`` is deliberately not sent to an extractor in this baseline;
    the fixture's ``incoming_candidate`` isolates write correctness from LLM
    extraction variability.  The relation resolver is the same function used
    by ``MemoryService`` for the normal deterministic write path.
    """

    raw = path.read_bytes()
    all_cases = load_memory_longtail_write_v1_cases(path)
    if relation is not None:
        try:
            ClaimRelation(relation)
        except ValueError as exc:
            raise ValueError(f"unknown relation filter: {relation}") from exc
    cases = [
        case
        for case in all_cases
        if (case_id is None or case["case_id"] == case_id)
        and (slice_name is None or case.get("slice") == slice_name)
        and (relation is None or case["expected"].get("relation") == relation)
        and (length_band is None or case.get("text_length_band") == length_band)
        and (contract_status is None or case.get("contract_status") == contract_status)
        and (
            live_subset is None
            or bool(case.get("live_semantic_subset")) == live_subset
        )
    ]
    if not cases:
        raise ValueError(
            "no Long-tail Write V1 cases match filters: "
            f"{{'case': {case_id!r}, 'slice': {slice_name!r}, "
            f"'relation': {relation!r}, 'length_band': {length_band!r}, "
            f"'contract_status': {contract_status!r}, "
            f"'live_subset': {live_subset!r}}}"
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
    review_rows = [
        row for row in rows if row["contract_status"] == "POLICY_REVIEW"
    ]
    metrics = _summarize(strict_rows)
    return {
        "version": REPORT_VERSION,
        "dataset": str(path),
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "case_filter": {
            "case_id": case_id,
            "slice": slice_name,
            "relation": relation,
            "length_band": length_band,
            "contract_status": contract_status,
            "live_subset": live_subset,
        },
        "case_count": len(rows),
        "strict_case_count": len(strict_rows),
        "strict_passed_case_count": sum(
            bool(row.get("passed")) for row in strict_rows
        ),
        "strict_failed_case_count": sum(
            not row.get("passed", False) for row in strict_rows
        ),
        "policy_review_case_count": len(review_rows),
        "metrics": metrics,
        "by_slice": _group_metrics(strict_rows, "slice"),
        "by_difficulty": _group_metrics(strict_rows, "difficulty"),
        "by_memory_kind": _group_metrics(strict_rows, "incoming_kind"),
        "by_length_band": _group_metrics(strict_rows, "text_length_band"),
        "by_relation": _group_metrics(strict_rows, "expected_relation"),
        "cases": rows,
        "policy_review": _policy_review(review_rows),
        "status": _baseline_status(metrics),
        "relation_authority": (
            "loveapp.application.memory_relations.resolve_claim_relation"
        ),
        "write_authority": (
            "loveapp.domain.memory_write.MemoryWriteBatch -> "
            "loveapp.adapters.memory.in_memory.InMemoryMemoryStore.commit_memory_batch"
        ),
        "production_store_mutation_permitted": False,
        "isolated_store_mutation_permitted": True,
        "model_calls_permitted": False,
    }


async def evaluate_memory_longtail_write_v1_integration(
    path: Path,
    *,
    case_ids: list[str] | None = None,
    fail_on_error: bool = False,
) -> dict[str, Any]:
    """Exercise a balanced sample through isolated Store write batches."""

    cases = load_memory_longtail_write_v1_cases(path)
    by_id = {case["case_id"]: case for case in cases}
    selected = case_ids or _default_integration_case_ids(cases)
    missing = [item for item in selected if item not in by_id]
    if missing:
        raise ValueError(f"unknown Long-tail Write integration cases: {missing}")

    rows: list[dict[str, Any]] = []
    for selected_id in selected:
        try:
            rows.append(await _integrate_case(by_id[selected_id]))
        except Exception as exc:
            if fail_on_error:
                raise
            rows.append(
                {
                    "case_id": selected_id,
                    "passed": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "store_mutation_permitted": False,
                    "isolated_store_mutation": True,
                }
            )
    return {
        "evaluation": "memory_longtail_write_v1_integration",
        "dataset": str(path),
        "case_count": len(rows),
        "passed_case_count": sum(bool(row.get("passed")) for row in rows),
        "failed_case_count": sum(not row.get("passed", False) for row in rows),
        "store_write_attempt_count": len(rows),
        "expected_store_outcome_pass_count": sum(
            bool(row.get("checks", {}).get("relation"))
            and bool(row.get("checks", {}).get("target_set"))
            and bool(row.get("checks", {}).get("write_action"))
            and bool(row.get("checks", {}).get("new_row"))
            and bool(row.get("checks", {}).get("incoming_status"))
            and bool(row.get("checks", {}).get("supersede_set"))
            and bool(row.get("checks", {}).get("preserve_set"))
            for row in rows
        ),
        "store_application_pass_count": sum(
            bool(row.get("passed")) for row in rows
        ),
        "transition_audit_count": sum(
            len(row.get("transition_audits", [])) for row in rows
        ),
        "status_transition_count": sum(
            int(row.get("status_transition_count", 0)) for row in rows
        ),
        "production_store_mutation_permitted": False,
        "isolated_in_memory_store_mutation": True,
        "model_calls_permitted": False,
        "selected_case_ids": selected,
        "rows": rows,
    }


async def evaluate_memory_longtail_write_integration(
    path: Path,
    *,
    case_ids: list[str] | None = None,
    fail_on_error: bool = False,
) -> dict[str, Any]:
    """Run the isolated integration evaluation.

    Integration evaluators in the rest of the memory evaluation package expose
    an awaitable API (the CLI and tests invoke them with ``asyncio.run``).
    Keep this public compatibility name awaitable as well; wrapping an already
    materialized dictionary in ``asyncio.run`` causes a confusing runtime
    ``ValueError`` before the evaluator can report any result.
    """

    return await evaluate_memory_longtail_write_v1_integration(
        path, case_ids=case_ids, fail_on_error=fail_on_error
    )


def _default_integration_case_ids(cases: list[dict[str, Any]]) -> list[str]:
    """Select the required balanced 36-case diagnostic sample."""

    strict = [case for case in cases if case.get("contract_status") == "EXACT"]
    selected: list[str] = []
    relation_counts = {
        "same": 4,
        "complementary": 4,
        "update": 6,
        "contradiction": 4,
        "unrelated": 4,
        "uncertain": 6,
    }
    for relation_name, count in relation_counts.items():
        relation_ids = [
            case["case_id"]
            for case in strict
            if case["expected"]["relation"] == relation_name
        ][:count]
        selected.extend(relation_ids)

    # Add four rows from each safety-heavy supplement.  The supplement slices
    # are complementary cases in the frozen fixture, so this yields the
    # documented 28 relation-balanced rows + 8 focused rows = 36 total.
    for slice_name in ("temporal_event_identity", "custom_canonical_coexistence"):
        supplement_ids = [
            case["case_id"]
            for case in strict
            if case.get("slice") == slice_name
        ][:4]
        selected.extend(supplement_ids)
    selected = list(dict.fromkeys(selected))
    if len(selected) < 36:
        selected.extend(
            case["case_id"] for case in strict if case["case_id"] not in selected
        )
    return selected[:36]


async def _integrate_case(case: dict[str, Any]) -> dict[str, Any]:
    user_id = "longtail-write-v1-integration-user"
    relationship_id = "longtail-write-v1-integration-relationship"
    reference_time = _parse_reference_time(case.get("reference_time"))
    store = InMemoryMemoryStore(clock=lambda: reference_time)
    fixture_to_actual: dict[str, str] = {}
    try:
        for raw_memory in case["existing_memories"]:
            candidate = _candidate_from_spec(raw_memory["candidate"])
            saved = await store.save_memory(
                user_id=user_id,
                relationship_id=relationship_id,
                candidate=candidate,
                source_message_id=(
                    raw_memory.get("source_message_id")
                    or f"{case['case_id']}-seed-{raw_memory['id']}"
                ),
                status=MemoryStatus(
                    raw_memory.get("status", MemoryStatus.CONFIRMED)
                ),
            )
            fixture_to_actual[raw_memory["id"]] = saved.item.id

        active = await store.list_memories(
            user_id=user_id,
            relationship_id=relationship_id,
            limit=200,
            read_only=True,
        )
        incoming = _candidate_from_spec(case["incoming_candidate"])
        incoming_status = MemoryStatus(
            case.get("incoming_status", MemoryStatus.CONFIRMED)
        )
        resolution = resolve_claim_relation(
            incoming,
            active,
            incoming_status=incoming_status,
        )
        actual_target_ids = list(resolution.target_memory_ids)
        operation_candidate = incoming.model_copy(
            update={
                "admission_score": incoming.admission_score or 0.95,
                "admission_decision": (
                    incoming.admission_decision
                    or (
                        AdmissionDecision.CONFIRM
                        if incoming_status == MemoryStatus.CONFIRMED
                        else AdmissionDecision.PROPOSE
                    )
                ),
                "claim_relation": resolution.relation,
            }
        )
        batch = MemoryWriteBatch(
            source_message_id=f"{case['case_id']}-incoming",
            operations=[
                MemoryWriteOperation(
                    candidate=operation_candidate,
                    status=incoming_status,
                    relation=resolution.relation,
                    target_memory_ids=actual_target_ids,
                    rule_name=resolution.rule_name,
                    reason=resolution.reason,
                )
            ],
        )
        before = await store.list_memories(
            user_id=user_id,
            relationship_id=relationship_id,
            limit=200,
            read_only=True,
        )
        committed = await store.commit_memory_batch(
            user_id=user_id,
            relationship_id=relationship_id,
            batch=batch,
        )
        after = await store.list_memories(
            user_id=user_id,
            relationship_id=relationship_id,
            limit=200,
            read_only=True,
        )
        audits = await store.list_transition_audits(
            user_id=user_id,
            relationship_id=relationship_id,
            source_message_id=batch.source_message_id,
        )

        expected = case["expected"]
        expected_outcome = expected["store_outcome"]
        expected_targets = set(expected.get("target_memory_ids", []))
        expected_superseded = set(
            expected_outcome.get("supersede_memory_ids", [])
        )
        expected_preserved = set(
            expected_outcome.get("preserve_memory_ids", [])
        )
        actual_fixture_targets = {
            fixture_id
            for fixture_id, actual_id in fixture_to_actual.items()
            if actual_id in actual_target_ids
        }
        actual_fixture_superseded = {
            fixture_id
            for fixture_id, actual_id in fixture_to_actual.items()
            if any(
                item.id == actual_id and item.status == MemoryStatus.SUPERSEDED
                for item in after
            )
        }
        actual_preserved_statuses = {
            fixture_id: next(
                (
                    item.status.value
                    for item in after
                    if item.id == actual_id
                ),
                None,
            )
            for fixture_id, actual_id in fixture_to_actual.items()
            if fixture_id in expected_preserved
        }
        before_status_by_actual = {item.id: item.status.value for item in before}
        after_status_by_actual = {item.id: item.status.value for item in after}
        status_changes = [
            {
                "fixture_id": fixture_id,
                "memory_id": actual_id,
                "before": before_status_by_actual.get(actual_id),
                "after": after_status_by_actual.get(actual_id),
            }
            for fixture_id, actual_id in fixture_to_actual.items()
            if before_status_by_actual.get(actual_id)
            != after_status_by_actual.get(actual_id)
        ]
        actual_fixture_preserved = {
            fixture_id
            for fixture_id, actual_id in fixture_to_actual.items()
            if after_status_by_actual.get(actual_id) != MemoryStatus.SUPERSEDED.value
        }
        incoming_saved = committed.saved[0] if committed.saved else None
        # ``save_memory`` returns the existing target for SAME/idempotent
        # operations with ``created=False``.  That row is not a newly admitted
        # incoming claim, so report no incoming final status in that case; the
        # Golden contract uses ``null`` to distinguish merge/refresh from an
        # inserted row.
        actual_new_row = bool(incoming_saved and incoming_saved.created)
        actual_incoming_status = (
            incoming_saved.item.status.value
            if incoming_saved and incoming_saved.created
            else None
        )
        checks = {
            "relation": resolution.relation.value == expected["relation"],
            "target_set": actual_fixture_targets == expected_targets,
            "write_action": (
                _planned_action(resolution.relation)
                == expected_outcome["write_action"]
            ),
            "new_row": actual_new_row
            == bool(expected_outcome.get("new_row_expected")),
            "incoming_status": actual_incoming_status
            == expected_outcome.get("incoming_final_status"),
            "supersede_set": actual_fixture_superseded == expected_superseded,
            "preserve_set": all(
                status != MemoryStatus.SUPERSEDED
                for status in actual_preserved_statuses.values()
            ),
        }
        return {
            "case_id": case["case_id"],
            "slice": case.get("slice"),
            "expected_relation": expected["relation"],
            "actual_relation": resolution.relation.value,
            "rule_name": resolution.rule_name,
            "reason": resolution.reason,
            "fixture_to_actual_memory_ids": dict(fixture_to_actual),
            "expected_target_memory_ids": list(expected_targets),
            "actual_target_memory_ids": sorted(actual_fixture_targets),
            "planned_write_action": _planned_action(resolution.relation),
            "expected_write_action": expected_outcome["write_action"],
            "actual_new_row": actual_new_row,
            "expected_new_row": bool(expected_outcome.get("new_row_expected")),
            "actual_incoming_memory_id": (
                incoming_saved.item.id if incoming_saved else None
            ),
            "actual_incoming_final_status": actual_incoming_status,
            "expected_incoming_final_status": expected_outcome.get(
                "incoming_final_status"
            ),
            "actual_supersede_memory_ids": sorted(actual_fixture_superseded),
            "expected_supersede_memory_ids": sorted(expected_superseded),
            "actual_preserve_memory_ids": sorted(actual_fixture_preserved),
            "expected_preserve_memory_ids": sorted(expected_preserved),
            "preserved_statuses": actual_preserved_statuses,
            # Keep the write-path vocabulary explicit in the artifact.  The
            # current batch has no separately planned status-update entries;
            # any observed status transition is represented here so callers do
            # not have to infer it from before/after rows.
            "status_updates": status_changes,
            "status_changes": status_changes,
            "status_transition_count": len(status_changes),
            "store_outcome": {
                "write_action": _planned_action(resolution.relation),
                "new_row_expected": actual_new_row,
                "incoming_final_status": actual_incoming_status,
                "supersede_memory_ids": sorted(actual_fixture_superseded),
                "preserve_memory_ids": sorted(actual_fixture_preserved),
            },
            "before_rows": _store_rows(before),
            "after_rows": _store_rows(after),
            "inserted_rows": [
                _store_row(result.item)
                for result in committed.saved
                if result.created
            ],
            "updated_memory_ids": committed.updated_memory_ids,
            "transition_audits": [
                audit.model_dump(mode="json") for audit in audits
            ],
            "checks": checks,
            "passed": all(checks.values()),
            "store_mutation_permitted": False,
            "isolated_store_mutation": True,
        }
    finally:
        await store.aclose()


def _evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    case_id = case["case_id"]
    candidate = _candidate_from_spec(case["incoming_candidate"])
    active = [
        _memory_from_spec(raw, case_id=case_id, index=index)
        for index, raw in enumerate(case["existing_memories"])
    ]
    before = _snapshot(candidate, active)
    incoming_status = MemoryStatus(
        case.get("incoming_status", MemoryStatus.CONFIRMED)
    )
    expected = case["expected"]
    expected_relation = ClaimRelation(expected["relation"])
    expected_targets = list(expected.get("target_memory_ids", []))
    resolution = resolve_claim_relation(
        candidate,
        active,
        incoming_status=incoming_status,
    )
    actual_targets = list(resolution.target_memory_ids)
    expected_outcome = expected["store_outcome"]
    actual_action = _planned_action(resolution.relation)
    actual_store_outcome = _planned_store_outcome(
        resolution,
        active,
        incoming_status=incoming_status,
    )
    expected_preserve_ids = set(expected_outcome.get("preserve_memory_ids", []))
    actual_supersede_ids = set(actual_store_outcome["supersede_memory_ids"])
    checks = {
        "relation": resolution.relation == expected_relation,
        "target_exact_match": actual_targets == expected_targets,
        "target_set_match": set(actual_targets) == set(expected_targets),
        "write_action": actual_action == expected_outcome["write_action"],
        "input_unchanged": before == _snapshot(candidate, active),
        "new_row_decision": (
            actual_store_outcome["new_row_expected"]
            == bool(expected_outcome.get("new_row_expected"))
        ),
        "final_status": (
            actual_store_outcome["incoming_final_status"]
            == expected_outcome.get("incoming_final_status")
        ),
        "supersede_exact_match": (
            actual_supersede_ids
            == set(expected_outcome.get("supersede_memory_ids", []))
        ),
        # Preserve checks are scoped to the fixture's explicitly protected
        # memories.  Non-target active memories are not implicitly part of the
        # expected preserve contract.
        "preserve_exact_match": {
            memory_id
            for memory_id in expected_preserve_ids
            if memory_id not in actual_supersede_ids
        }
        == expected_preserve_ids,
    }
    safety = _case_safety(case, resolution, active)
    return {
        "case_id": case_id,
        "slice": case.get("slice"),
        "difficulty": case.get("difficulty"),
        "contract_status": case.get("contract_status", "EXACT"),
        "text_length_band": case.get("text_length_band"),
        "incoming_kind": candidate.kind.value,
        "incoming_status": incoming_status.value,
        "user_text": case.get("user_text"),
        "expected_relation": expected_relation.value,
        "actual_relation": resolution.relation.value,
        "expected_target_memory_ids": expected_targets,
        "actual_target_memory_ids": actual_targets,
        "expected_store_outcome": expected_outcome,
        "actual_write_action": actual_action,
        "actual_store_outcome": actual_store_outcome,
        "expected_write_action": expected_outcome["write_action"],
        "actual_rule_name": resolution.rule_name,
        "actual_reason": resolution.reason,
        "candidate": candidate.model_dump(mode="json"),
        "active_memory_ids": [item.id for item in active],
        "checks": checks,
        "failures": [name for name, passed in checks.items() if not passed],
        "passed": all(checks.values()),
        "error_taxonomy": _error_taxonomy(case, resolution, checks),
        "safety": safety,
        "trace": {
            "candidate_dedupe_key": memory_dedupe_key(candidate),
            "active_dedupe_keys": {item.id: item.dedupe_key for item in active},
            "active_statuses": {item.id: item.status.value for item in active},
            "incoming_status": incoming_status.value,
            "relation_target_ids": actual_targets,
        },
    }


def _planned_store_outcome(
    resolution: ClaimRelationResolution,
    active: list[MemoryItem],
    *,
    incoming_status: MemoryStatus,
) -> dict[str, Any]:
    """Project the existing write contract without mutating a Store.

    The baseline is intentionally read-only.  These fields describe what the
    production ``MemoryWriteBatch`` path would be asked to do for the
    deterministic relation result, and are kept separate from the isolated
    Store integration's observed outcome.
    """

    action = _planned_action(resolution.relation)
    supersede_ids = (
        list(resolution.target_memory_ids)
        if action == "supersede_and_add"
        else []
    )
    active_ids = [item.id for item in active]
    return {
        "write_action": action,
        "new_row_expected": action != "merge_or_refresh",
        "incoming_final_status": (
            None if action == "merge_or_refresh" else incoming_status.value
        ),
        "supersede_memory_ids": supersede_ids,
        "preserve_memory_ids": [
            memory_id for memory_id in active_ids if memory_id not in supersede_ids
        ],
    }


def _candidate_from_spec(spec: dict[str, Any]) -> MemoryCandidate:
    try:
        return MemoryCandidate.model_validate(spec)
    except Exception as exc:
        raise LongTailWriteEvaluationError(
            "invalid incoming candidate spec"
        ) from exc


def _snapshot(
    candidate: MemoryCandidate,
    active: list[MemoryItem],
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Return a stable, JSON-compatible snapshot for mutation checks.

    ``resolve_claim_relation`` is expected to be observational.  The baseline
    therefore compares a normalized model dump before and after resolution,
    rather than comparing object identity (or relying on Pydantic's model
    equality implementation).  Sorting active memories by id makes the check
    independent of store/query ordering.
    """

    return (
        candidate.model_dump(mode="json"),
        tuple(
            sorted(
                (item.model_dump(mode="json") for item in active),
                key=lambda item: str(item.get("id", "")),
            )
        ),
    )


def _memory_from_spec(
    raw: dict[str, Any],
    *,
    case_id: str,
    index: int,
) -> MemoryItem:
    memory_id = raw.get("id")
    if not isinstance(memory_id, str) or not memory_id:
        raise LongTailWriteEvaluationError(
            f"{case_id} active memory {index} requires id"
        )
    candidate_spec = raw.get("candidate")
    if not isinstance(candidate_spec, dict):
        raise LongTailWriteEvaluationError(
            f"{case_id}/{memory_id} requires candidate"
        )
    candidate = _candidate_from_spec(candidate_spec)
    data = candidate.model_dump()
    data.update(
        {
            "id": memory_id,
            "user_id": str(
                raw.get("user_id") or f"longtail-write-{case_id.casefold()}-user"
            ),
            "relationship_id": str(raw.get("relationship_id") or "partner"),
            "status": raw.get("status", MemoryStatus.CONFIRMED),
            "source_message_id": raw.get("source_message_id")
            or f"{memory_id}-source",
            "created_at": raw.get("created_at") or "2026-08-01T10:00:00Z",
            "updated_at": raw.get("updated_at") or "2026-09-01T10:00:00Z",
            "last_seen_at": raw.get("last_seen_at"),
            "last_used_at": raw.get("last_used_at"),
            "dedupe_key": memory_dedupe_key(candidate),
        }
    )
    try:
        return MemoryItem.model_validate(data)
    except Exception as exc:
        raise LongTailWriteEvaluationError(
            f"invalid active memory {case_id}/{memory_id}"
        ) from exc


def _case_safety(
    case: dict[str, Any],
    resolution: ClaimRelationResolution,
    active: list[MemoryItem],
) -> dict[str, bool]:
    expected = case["expected"]
    expected_relation = ClaimRelation(expected["relation"])
    expected_targets = set(expected.get("target_memory_ids", []))
    actual_targets = set(resolution.target_memory_ids)
    expected_destructive = bool(expected.get("destructive_update_allowed"))
    incoming_status = MemoryStatus(
        case.get("incoming_status", MemoryStatus.CONFIRMED)
    )
    confirmed_ids = {
        item.id for item in active if item.status == MemoryStatus.CONFIRMED
    }
    event_identity = case.get("slice") == "temporal_event_identity"
    event_vs_pattern = case.get("slice") == "event_vs_pattern"
    custom_canonical = case.get("slice") == "custom_canonical_coexistence"
    return {
        "false_supersede": resolution.relation == ClaimRelation.UPDATE
        and (not expected_destructive or actual_targets != expected_targets),
        "false_merge": resolution.relation == ClaimRelation.SAME
        and expected_relation != ClaimRelation.SAME,
        "false_link": bool(actual_targets) and not expected_targets,
        "cross_subject_false_link": case.get("slice") == "cross_subject"
        and bool(actual_targets),
        "event_false_dedupe": event_identity
        and resolution.relation == ClaimRelation.SAME,
        "event_false_supersede": event_identity
        and resolution.relation == ClaimRelation.UPDATE,
        "event_to_pattern_false_update": event_vs_pattern
        and resolution.relation == ClaimRelation.UPDATE,
        "custom_to_canonical_false_supersede": custom_canonical
        and resolution.relation == ClaimRelation.UPDATE,
        # A proposed target list is not itself destructive.  Count a
        # protection violation only when the resolver actually authorized an
        # UPDATE against a confirmed memory; UNCERTAIN/CONTRADICTION targets
        # are diagnostic links and do not close anything in the write path.
        "proposed_overwrites_confirmed": incoming_status
        == MemoryStatus.PROPOSED
        and resolution.relation == ClaimRelation.UPDATE
        and bool(actual_targets & confirmed_ids),
        "uncertain_destructive_update": expected_relation
        == ClaimRelation.UNCERTAIN
        and resolution.relation == ClaimRelation.UPDATE,
        "non_target_supersede": resolution.relation == ClaimRelation.UPDATE
        and not actual_targets.issubset(expected_targets),
        "historical_event_preserved": not (
            event_identity
            and resolution.relation in {ClaimRelation.SAME, ClaimRelation.UPDATE}
        ),
    }


def _error_taxonomy(
    case: dict[str, Any],
    resolution: ClaimRelationResolution,
    checks: dict[str, bool],
) -> str | None:
    if all(checks.values()):
        return None
    expected_relation = case["expected"].get("relation")
    if checks.get("relation") and not checks.get("target_set_match"):
        return "TARGET_SELECTION_ERROR"
    if expected_relation != resolution.relation.value:
        if case.get("slice") == "cross_subject":
            # Reserve CROSS_SUBJECT_LINK for an actual cross-scope link.  A
            # conservative UNCERTAIN/no-target result is relation drift, not a
            # safety violation.
            return (
                "CROSS_SUBJECT_LINK"
                if resolution.target_memory_ids
                else "SEMANTIC_RELATION_ERROR"
            )
        if case.get("slice") == "temporal_event_identity":
            return "EVENT_IDENTITY_ERROR"
        if case.get("slice") == "event_vs_pattern":
            return "EVENT_PATTERN_CONFUSION"
        if case.get("slice") == "custom_canonical_coexistence":
            return "CUSTOM_CANONICAL_COLLISION"
        if case.get("slice") == "safe_uncertain_ambiguity":
            return "GOLD_POLICY_AMBIGUITY"
        return "SEMANTIC_RELATION_ERROR"
    if not checks.get("write_action", True):
        return "WRITE_ACTION_ERROR"
    return "TARGET_SELECTION_ERROR"


def _error_row(case: dict[str, Any], error: Exception) -> dict[str, Any]:
    expected = case.get("expected") or {}
    outcome = expected.get("store_outcome") or {}
    return {
        "case_id": case.get("case_id"),
        "slice": case.get("slice"),
        "difficulty": case.get("difficulty"),
        "contract_status": case.get("contract_status", "EXACT"),
        "text_length_band": case.get("text_length_band"),
        "incoming_kind": (case.get("incoming_candidate") or {}).get("kind"),
        "incoming_status": case.get("incoming_status", "confirmed"),
        "expected_relation": expected.get("relation"),
        "actual_relation": None,
        "expected_target_memory_ids": expected.get("target_memory_ids", []),
        "actual_target_memory_ids": [],
        "expected_store_outcome": outcome,
        "actual_store_outcome": None,
        "actual_write_action": None,
        "expected_write_action": outcome.get("write_action"),
        "checks": {"execution": False},
        "failures": ["execution"],
        "passed": False,
        "error": f"{type(error).__name__}: {error}",
        "error_taxonomy": "EVALUATOR_BUG",
        "safety": {},
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    support: Counter[str] = Counter()
    predicted: Counter[str] = Counter()
    correct: Counter[str] = Counter()
    confusion: Counter[str] = Counter()
    target_predicted = 0
    target_expected = 0
    target_correct = 0
    for row in rows:
        expected = str(row.get("expected_relation"))
        actual = str(row.get("actual_relation") or "missing")
        support[expected] += 1
        predicted[actual] += 1
        correct[expected] += int(expected == actual)
        confusion[f"{expected}|{actual}"] += 1
        actual_ids = set(row.get("actual_target_memory_ids", []))
        expected_ids = set(row.get("expected_target_memory_ids", []))
        target_predicted += len(actual_ids)
        target_expected += len(expected_ids)
        target_correct += len(actual_ids & expected_ids)
    per_relation: dict[str, Any] = {}
    for name in RELATIONS:
        precision = _ratio(correct[name], predicted[name])
        recall = _ratio(correct[name], support[name])
        per_relation[name] = {
            "support": support[name],
            "predicted": predicted[name],
            "correct": correct[name],
            "precision": precision,
            "recall": recall,
            "f1": _f1(precision, recall),
        }
    safety_rows = [row.get("safety", {}) for row in rows]
    safety_names = (
        "false_supersede",
        "false_merge",
        "false_link",
        "cross_subject_false_link",
        "event_false_dedupe",
        "event_false_supersede",
        "event_to_pattern_false_update",
        "custom_to_canonical_false_supersede",
        "proposed_overwrites_confirmed",
        "uncertain_destructive_update",
        "non_target_supersede",
    )
    safety_counts = {
        f"{name}_count": sum(bool(item.get(name)) for item in safety_rows)
        for name in safety_names
    }
    event_count = sum(row.get("slice") == "temporal_event_identity" for row in rows)
    cross_subject_count = sum(row.get("slice") == "cross_subject" for row in rows)
    event_pattern_count = sum(row.get("slice") == "event_vs_pattern" for row in rows)
    custom_canonical_count = sum(
        row.get("slice") == "custom_canonical_coexistence" for row in rows
    )
    proposed_count = sum(
        row.get("incoming_status") == MemoryStatus.PROPOSED.value for row in rows
    )
    uncertain_count = sum(
        row.get("expected_relation") == ClaimRelation.UNCERTAIN.value for row in rows
    )
    safety = {
        **safety_counts,
        "false_supersede_rate": _ratio(safety_counts["false_supersede_count"], total),
        "false_merge_rate": _ratio(safety_counts["false_merge_count"], total),
        "false_link_rate": _ratio(safety_counts["false_link_count"], total),
        "cross_subject_false_link_rate": _ratio(
            safety_counts["cross_subject_false_link_count"], cross_subject_count
        ),
        "event_false_dedupe_rate": _ratio(
            safety_counts["event_false_dedupe_count"], event_count
        ),
        "event_false_supersede_rate": _ratio(
            safety_counts["event_false_supersede_count"], event_count
        ),
        "event_to_pattern_false_update_rate": _ratio(
            safety_counts["event_to_pattern_false_update_count"], event_pattern_count
        ),
        "custom_to_canonical_false_supersede_rate": _ratio(
            safety_counts["custom_to_canonical_false_supersede_count"],
            custom_canonical_count,
        ),
        "proposed_overwrites_confirmed_violation_rate": _ratio(
            safety_counts["proposed_overwrites_confirmed_count"], proposed_count
        ),
        "uncertain_destructive_update_rate": _ratio(
            safety_counts["uncertain_destructive_update_count"], uncertain_count
        ),
        "non_target_supersede_rate": _ratio(
            safety_counts["non_target_supersede_count"], total
        ),
        "historical_event_preservation_rate": _ratio(
            sum(
                bool(row.get("safety", {}).get("historical_event_preserved"))
                for row in rows
                if row.get("slice") == "temporal_event_identity"
            ),
            event_count,
        ),
    }
    action_correct = sum(
        row.get("actual_write_action") == row.get("expected_write_action")
        for row in rows
    )
    def store_check(name: str) -> int:
        return sum(bool(row.get("checks", {}).get(name, False)) for row in rows)
    return {
        "case_count": total,
        "passed_case_count": sum(bool(row.get("passed")) for row in rows),
        "failed_case_count": sum(not row.get("passed", False) for row in rows),
        "relation_accuracy": _ratio(sum(correct.values()), total),
        "per_relation": per_relation,
        "relation_confusion": dict(sorted(confusion.items())),
        "target_exact_match_accuracy": _ratio(
            sum(row.get("checks", {}).get("target_exact_match", False) for row in rows),
            total,
        ),
        "target_set_accuracy": _ratio(
            sum(row.get("checks", {}).get("target_set_match", False) for row in rows),
            total,
        ),
        "target_micro_precision": _ratio(target_correct, target_predicted),
        "target_micro_recall": _ratio(target_correct, target_expected),
        "target_micro_f1": _f1(
            _ratio(target_correct, target_predicted),
            _ratio(target_correct, target_expected),
        ),
        "store_action_accuracy": _ratio(action_correct, total),
        "new_row_decision_accuracy": _ratio(store_check("new_row_decision"), total),
        "final_status_accuracy": _ratio(store_check("final_status"), total),
        "supersede_exact_match_accuracy": _ratio(
            store_check("supersede_exact_match"), total
        ),
        "preserve_exact_match_accuracy": _ratio(
            store_check("preserve_exact_match"), total
        ),
        "safety": safety,
        "error_taxonomy": dict(
            sorted(
                Counter(
                    row.get("error_taxonomy")
                    for row in rows
                    if row.get("error_taxonomy")
                ).items()
            )
        ),
    }


def _group_metrics(
    rows: list[dict[str, Any]], field: str
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(field) or "unknown")].append(row)
    return {
        key: {
            "case_count": len(group),
            "passed_case_count": sum(bool(row.get("passed")) for row in group),
            "relation_accuracy": _ratio(
                sum(
                    row.get("checks", {}).get("relation", False)
                    for row in group
                ),
                len(group),
            ),
            "target_set_accuracy": _ratio(
                sum(
                    row.get("checks", {}).get("target_set_match", False)
                    for row in group
                ),
                len(group),
            ),
            "store_action_accuracy": _ratio(
                sum(
                    row.get("checks", {}).get("write_action", False)
                    for row in group
                ),
                len(group),
            ),
        }
        for key, group in sorted(grouped.items())
    }


def _policy_review(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    classifications = {
        "policy_review_semantic_paraphrase_identity": (
            "GOLD_POLICY_AMBIGUITY",
            "NEEDS_PRODUCT_DECISION",
        ),
        "policy_review_custom_fact_object_change": (
            "ONTOLOGY_MIGRATION_ISSUE",
            "CHANGE_RECOMMENDED",
        ),
        "policy_review_custom_preference_polarity": (
            "GOLD_POLICY_AMBIGUITY",
            "NEEDS_PRODUCT_DECISION",
        ),
        "policy_review_custom_action_intent_completion": (
            "DOWNSTREAM_INTEGRATION_ISSUE",
            "CHANGE_RECOMMENDED",
        ),
        "policy_review_custom_plan_completion": (
            "DOWNSTREAM_INTEGRATION_ISSUE",
            "CHANGE_RECOMMENDED",
        ),
        "policy_review_event_recurrence": (
            "EVENT_LIFECYCLE_GAP",
            "NEEDS_PRODUCT_DECISION",
        ),
        "policy_review_custom_to_canonical_promotion": (
            "ONTOLOGY_MIGRATION_ISSUE",
            "NEEDS_PRODUCT_DECISION",
        ),
        "policy_review_belief_high_confidence": (
            "NEEDS_PRODUCT_DECISION",
            "KEEP_CURRENT",
        ),
        "policy_review_long_multi_claim_user_text": (
            "UPSTREAM_CONTRACT_ISSUE",
            "CHANGE_RECOMMENDED",
        ),
        "policy_review_subject_alias": (
            "UPSTREAM_CONTRACT_ISSUE",
            "CHANGE_RECOMMENDED",
        ),
        "policy_review_vague_temporal_update": (
            "GOLD_POLICY_AMBIGUITY",
            "NEEDS_PRODUCT_DECISION",
        ),
        "policy_review_merge_vs_complement": (
            "GOLD_POLICY_AMBIGUITY",
            "NEEDS_PRODUCT_DECISION",
        ),
        "policy_review_many_related_targets": (
            "DOWNSTREAM_INTEGRATION_ISSUE",
            "CHANGE_RECOMMENDED",
        ),
        "policy_review_event_level_lifecycle_gap": (
            "EVENT_LIFECYCLE_GAP",
            "NEEDS_PRODUCT_DECISION",
        ),
        "policy_review_custom_status_fields": (
            "ONTOLOGY_MIGRATION_ISSUE",
            "NEEDS_PRODUCT_DECISION",
        ),
        "policy_review_canonical_custom_overlap": (
            "ONTOLOGY_MIGRATION_ISSUE",
            "NEEDS_PRODUCT_DECISION",
        ),
    }
    return [
        {
            "case_id": row.get("case_id"),
            "slice": row.get("slice"),
            "expected_relation": row.get("expected_relation"),
            "actual_relation": row.get("actual_relation"),
            "actual_target_memory_ids": row.get("actual_target_memory_ids", []),
            "policy_classification": classifications.get(
                str(row.get("slice")),
                ("GOLD_POLICY_AMBIGUITY", "NEEDS_PRODUCT_DECISION"),
            )[0],
            "recommendation": classifications.get(
                str(row.get("slice")),
                ("GOLD_POLICY_AMBIGUITY", "NEEDS_PRODUCT_DECISION"),
            )[1],
            "note": "Observe-only; excluded from strict V1 scoring.",
        }
        for row in rows
    ]


def _baseline_status(metrics: dict[str, Any]) -> str:
    safety = metrics.get("safety", {})
    safety_zero = all(
        value == 0
        for key, value in safety.items()
        if key.endswith("_count")
    )
    stable = (
        metrics.get("relation_accuracy", 0) >= 0.95
        and metrics.get("target_set_accuracy", 0) >= 0.95
        and metrics.get("store_action_accuracy", 0) >= 0.98
        and safety_zero
    )
    return (
        "ENGINEERING_STABLE_WITH_OPEN_WORLD_POLICY_DEBT"
        if stable
        else "BASELINE_DRIFT_REQUIRES_REVIEW"
    )


def render_memory_longtail_write_v1_report(report: dict[str, Any]) -> str:
    metrics = report.get("metrics") or {}
    lines = [
        "# Memory Long-tail Write V1 Baseline Report",
        "",
        f"- Dataset: `{report.get('dataset', '-')}`",
        f"- Dataset SHA-256: `{report.get('dataset_sha256', '-')}`",
        f"- Cases evaluated: `{report.get('case_count', 0)}`",
        f"- Strict cases: `{report.get('strict_case_count', 0)}`",
        f"- Strict passed: `{report.get('strict_passed_case_count', 0)}`",
        f"- Status: **{report.get('status', '-') }**",
        "- Production Store mutation permitted: `False`",
        "- Model calls permitted: `False`",
        "",
        "## Authority",
        "",
        f"- Relation: `{report.get('relation_authority', '-')}`",
        f"- Write: `{report.get('write_authority', '-')}`",
        "",
        "## Strict Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for name in (
        "case_count",
        "passed_case_count",
        "failed_case_count",
        "relation_accuracy",
        "target_exact_match_accuracy",
        "target_set_accuracy",
        "target_micro_precision",
        "target_micro_recall",
        "target_micro_f1",
        "store_action_accuracy",
        "new_row_decision_accuracy",
        "final_status_accuracy",
        "supersede_exact_match_accuracy",
        "preserve_exact_match_accuracy",
    ):
        lines.append(f"| `{name}` | {_fmt(metrics.get(name))} |")
    lines.extend(
        [
            "",
            "## Relation Precision / Recall / F1",
            "",
            "| Relation | Support | Precision | Recall | F1 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for name, detail in (metrics.get("per_relation") or {}).items():
        lines.append(
            f"| `{name}` | {detail['support']} | {_fmt(detail['precision'])} | "
            f"{_fmt(detail['recall'])} | {_fmt(detail['f1'])} |"
        )
    lines.extend(
        ["", "## Safety / Governance", "", "| Metric | Value |", "|---|---:|"]
    )
    for name, value in (metrics.get("safety") or {}).items():
        lines.append(f"| `{name}` | {_fmt(value)} |")
    lines.extend(
        ["", "## Error Taxonomy", "", "| Category | Count |", "|---|---:|"]
    )
    for name, value in (metrics.get("error_taxonomy") or {}).items():
        lines.append(f"| `{name}` | {value} |")
    lines.extend(
        [
            "",
            "## Failed Strict Cases",
            "",
            "| Case | Slice | Expected | Actual | Action | Category |",
            "|---|---|---|---|---|---|",
        ]
    )
    failures = [
        row
        for row in report.get("cases", [])
        if row.get("contract_status") == "EXACT" and not row.get("passed")
    ]
    if failures:
        for row in failures:
            lines.append(
                f"| {row.get('case_id')} | {row.get('slice')} | "
                f"{row.get('expected_relation')} | {row.get('actual_relation')} | "
                f"{row.get('actual_write_action')} | {row.get('error_taxonomy', '-')} |"
            )
    else:
        lines.append("| none | - | - | - | - | - |")
    lines.extend(
        [
            "",
            "The deterministic baseline uses fixture candidates only. "
            "Policy Review cases are observe-only and excluded from strict scoring.",
            "",
        ]
    )
    return "\n".join(lines)


def render_memory_longtail_write_policy_review(report: dict[str, Any]) -> str:
    lines = [
        "# Memory Long-tail Write V1 Policy Review",
        "",
        "These cases are observe-only and excluded from strict V1 scoring.",
        "",
        "| Case | Slice | Expected | Actual | Classification | Recommendation |",
        "|---|---|---|---|---|---|",
    ]
    for row in report.get("policy_review", []):
        lines.append(
            f"| {row['case_id']} | {row['slice']} | "
            f"{row['expected_relation']} | {row['actual_relation']} | "
            f"{row['policy_classification']} | {row['recommendation']} |"
        )
    return "\n".join(lines) + "\n"


def render_memory_longtail_write_integration_diagnostic(
    report: dict[str, Any]
) -> str:
    lines = [
        "# Memory Long-tail Write V1 Integration Diagnostic",
        "",
        f"- Cases: `{report.get('case_count', 0)}`",
        f"- Passed: `{report.get('passed_case_count', 0)}`",
        f"- Store write attempts: `{report.get('store_write_attempt_count', 0)}`",
        f"- Transition audits: `{report.get('transition_audit_count', 0)}`",
        "- Production Store mutation permitted: `False`",
        "- Isolated InMemoryMemoryStore mutation: `True`",
        "",
        "| Case | Expected | Actual | Action | New row | Superseded | Status changes | Passed |",
        "|---|---|---|---|---|---|---:|---|",
    ]
    for row in report.get("rows", []):
        lines.append(
            f"| {row.get('case_id')} | {row.get('expected_relation', '-')} | "
            f"{row.get('actual_relation', '-')} | "
            f"{row.get('planned_write_action', '-')} | "
            f"{row.get('actual_new_row', '-')} | "
            f"{','.join(row.get('actual_supersede_memory_ids', [])) or '-'} | "
            f"{row.get('status_transition_count', 0)} | "
            f"{row.get('passed', False)} |"
        )
    lines.extend(
        [
            "",
            "The JSON artifact contains before_rows, after_rows, inserted_rows, "
            "status changes, and transition audits for every selected case.",
            "",
        ]
    )
    return "\n".join(lines)


# Backward-compatible name used by the CLI and adjacent evaluators.
render_memory_longtail_write_integration = (
    render_memory_longtail_write_integration_diagnostic
)


def _planned_action(relation: ClaimRelation) -> str:
    if relation == ClaimRelation.SAME:
        return "merge_or_refresh"
    if relation == ClaimRelation.UPDATE:
        return "supersede_and_add"
    return "add_without_supersede"


def _store_row(item: MemoryItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "status": item.status.value,
        "source_message_id": item.source_message_id,
        "kind": item.kind.value,
        "subject": item.subject,
        "custom_predicate": item.custom_predicate,
        "canonical_predicate": item.canonical_predicate,
        "supersedes_id": item.supersedes_id,
    }


def _store_rows(items: list[MemoryItem]) -> list[dict[str, Any]]:
    return [_store_row(item) for item in items]


def _parse_reference_time(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime(2026, 9, 3, 12, tzinfo=UTC)


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return round(float(numerator) / float(denominator), 4) if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return (
        round(2 * precision * recall / (precision + recall), 4)
        if precision + recall
        else 0.0
    )


def _fmt(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


__all__ = [
    "EXPECTED_CASE_COUNT",
    "EXPECTED_POLICY_REVIEW_CASE_COUNT",
    "EXPECTED_STRICT_CASE_COUNT",
    "REPORT_VERSION",
    "LongTailWriteEvaluationError",
    "evaluate_memory_longtail_write_integration",
    "evaluate_memory_longtail_write_v1",
    "evaluate_memory_longtail_write_v1_integration",
    "load_memory_longtail_write_v1_cases",
    "render_memory_longtail_write_integration",
    "render_memory_longtail_write_integration_diagnostic",
    "render_memory_longtail_write_policy_review",
    "render_memory_longtail_write_v1_report",
]

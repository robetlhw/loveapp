"""Run the real Memory V2 pipeline and expose its governance decisions."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from uuid import uuid4

from rich.console import Console
from rich.table import Table

from loveapp.bootstrap import MemoryContainer, build_memory_container
from loveapp.core.config import Settings
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.memory import (
    MemoryGateDecision,
    MemoryGateReason,
    MemoryItem,
    MemoryStatus,
    RememberResult,
    StoredMessage,
)
from loveapp.domain.memory_write import (
    MemoryTransitionAudit,
    MemoryWriteBatch,
    MemoryWriteBatchResult,
)

_GOVERNANCE_TRACE = "memory_candidate_governance"
_MODEL_TRACE_PREFIX = "memory_model"


class _ForcedMemoryGate:
    """Test-only wrapper that preserves the original Gate result in signals."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    def evaluate(
        self,
        text: str,
        *,
        conversation_history: Iterable[StoredMessage] = (),
        existing_memories: Iterable[MemoryItem] = (),
    ) -> MemoryGateDecision:
        decision = self._delegate.evaluate(
            text,
            conversation_history=conversation_history,
            existing_memories=existing_memories,
        )
        if decision.should_extract:
            return decision
        return decision.model_copy(
            update={
                "should_extract": True,
                "reason": MemoryGateReason.FORCED,
                "signals": [*decision.signals, "force_gate"],
                "matched_rule": decision.matched_rule,
                "matched_span": decision.matched_span,
            }
        )


class _DryRunMemoryStore:
    """Test-only store decorator that suppresses the atomic governance commit."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.batches: list[MemoryWriteBatch] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    async def commit_memory_batch(
        self,
        *,
        user_id: str,
        relationship_id: str,
        batch: MemoryWriteBatch,
    ) -> MemoryWriteBatchResult:
        del user_id, relationship_id
        self.batches.append(batch.model_copy(deep=True))
        return MemoryWriteBatchResult()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Observe Flash predicates, canonicalization, admission, relation "
            "comparison, and Memory V2 write effects."
        )
    )
    parser.add_argument(
        "messages",
        nargs="*",
        help="Messages to process in order. Without messages, starts an interactive session.",
    )
    parser.add_argument(
        "--text",
        action="append",
        default=[],
        help="Add a message to process; may be supplied more than once.",
    )
    parser.add_argument("--user-id", default="memory-observer-user")
    parser.add_argument("--relationship-id", default="memory-observer-relationship")
    parser.add_argument("--conversation-id")
    parser.add_argument(
        "--status",
        choices=(MemoryStatus.PROPOSED.value, MemoryStatus.CONFIRMED.value),
        default=MemoryStatus.PROPOSED.value,
        help="Requested input status. Typed admission still rejects unsafe candidates.",
    )
    storage = parser.add_mutually_exclusive_group()
    storage.add_argument(
        "--database",
        type=Path,
        help="Use this SQLite file instead of the default isolated in-memory store.",
    )
    storage.add_argument(
        "--use-app-database",
        action="store_true",
        help="Use the Memory backend and database configured by the application.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit one JSON report array. Requires at least one message.",
    )
    parser.add_argument(
        "--force-gate",
        action="store_true",
        help="Test only: run extraction even when the normal Gate would skip it.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test only: plan governance actions without committing Memory changes.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum number of memories shown in each state snapshot.",
    )
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 1000:
        parser.error("--limit must be between 1 and 1000")
    if args.json_output and not [*args.messages, *args.text]:
        parser.error("--json requires at least one positional message or --text")
    if args.dry_run and (args.database is not None or args.use_app_database):
        parser.error("--dry-run uses the isolated in-memory store and cannot use a database")
    return args


async def _main(args: argparse.Namespace) -> int:
    settings = _observer_settings(args)
    container = build_memory_container(settings)
    _configure_test_options(container, force_gate=args.force_gate, dry_run=args.dry_run)
    conversation_id = args.conversation_id or f"memory-observer-{uuid4()}"
    console = Console(stderr=args.json_output)
    reports: list[dict[str, Any]] = []
    try:
        messages = [*args.messages, *args.text]
        if messages:
            for turn, message in enumerate(messages, start=1):
                report = await _observe_turn(
                    container,
                    text=message,
                    turn=turn,
                    user_id=args.user_id,
                    relationship_id=args.relationship_id,
                    conversation_id=conversation_id,
                    requested_status=MemoryStatus(args.status),
                    limit=args.limit,
                    force_gate=args.force_gate,
                    dry_run=args.dry_run,
                )
                reports.append(report)
                if not args.json_output:
                    _render_report(console, report)
        else:
            await _interactive_session(
                console,
                container,
                user_id=args.user_id,
                relationship_id=args.relationship_id,
                conversation_id=conversation_id,
                requested_status=MemoryStatus(args.status),
                limit=args.limit,
                force_gate=args.force_gate,
                dry_run=args.dry_run,
            )
    finally:
        await container.aclose()

    if args.json_output:
        json.dump(reports, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 0


def _observer_settings(args: argparse.Namespace) -> Settings:
    settings = Settings()
    if args.use_app_database:
        return settings
    if args.database is not None:
        return settings.model_copy(
            update={
                "memory_backend": "sqlite",
                "memory_database_path": args.database.resolve(),
            }
        )
    return settings.model_copy(update={"memory_backend": "memory"})


def _configure_test_options(
    container: MemoryContainer,
    *,
    force_gate: bool,
    dry_run: bool,
) -> None:
    if force_gate:
        container.memory_service._gate = _ForcedMemoryGate(container.memory_service._gate)
    if dry_run:
        container.memory_service.store = _DryRunMemoryStore(container.memory_store)


async def _interactive_session(
    console: Console,
    container: MemoryContainer,
    *,
    user_id: str,
    relationship_id: str,
    conversation_id: str,
    requested_status: MemoryStatus,
    limit: int,
    force_gate: bool,
    dry_run: bool,
) -> None:
    console.print(
        "Memory V2 observer is ready. Enter one message per turn; "
        "use :state to inspect memory and :quit to exit."
    )
    turn = 0
    while True:
        try:
            text = input("memory> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return
        if not text:
            continue
        if text.casefold() in {":quit", ":q", ":exit"}:
            return
        if text.casefold() == ":state":
            memories = await _list_memories(container, user_id, relationship_id, limit)
            _render_memory_state(console, memories, title="Current memory state")
            continue
        turn += 1
        report = await _observe_turn(
            container,
            text=text,
            turn=turn,
            user_id=user_id,
            relationship_id=relationship_id,
            conversation_id=conversation_id,
            requested_status=requested_status,
            limit=limit,
            force_gate=force_gate,
            dry_run=dry_run,
        )
        _render_report(console, report)


async def _observe_turn(
    container: MemoryContainer,
    *,
    text: str,
    turn: int,
    user_id: str,
    relationship_id: str,
    conversation_id: str,
    requested_status: MemoryStatus,
    limit: int,
    force_gate: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    before = await _list_memories(container, user_id, relationship_id, limit)
    trace = ExecutionTrace()
    result = await container.memory_service.remember_text(
        user_id=user_id,
        relationship_id=relationship_id,
        conversation_id=conversation_id,
        text=text,
        status=requested_status,
        trace=trace,
    )
    after = await _list_memories(container, user_id, relationship_id, limit)
    audits = await container.memory_store.list_transition_audits(
        user_id=user_id,
        relationship_id=relationship_id,
        source_message_id=result.message.id,
        limit=500,
    )
    records = trace.snapshot()
    model_outputs = _model_outputs(records)
    candidates = _governance_candidates(records)
    memory_index = {item.id: item for item in [*before, *after]}
    for candidate in candidates:
        candidate["compared_memories"] = _memories_for_ids(
            candidate.get("compared_memory_ids", []), memory_index
        )
        candidate["relation_target_memories"] = _memories_for_ids(
            candidate.get("relation_target_memory_ids", []), memory_index
        )
        candidate["strong_compared_memories"] = _memories_for_ids(
            candidate.get("strong_compared_memory_ids", []), memory_index
        )
        candidate["planned_target_memories"] = _memories_for_ids(
            candidate.get("planned_target_memory_ids", []), memory_index
        )
    return {
        "turn": turn,
        "input": text,
        "scope": {
            "user_id": user_id,
            "relationship_id": relationship_id,
            "conversation_id": conversation_id,
            "source_message_id": result.message.id,
            "extraction_run_id": result.extraction_run_id,
        },
        "test_options": {
            "force_gate": force_gate,
            "dry_run": dry_run,
        },
        "gate": (
            result.gate_decision.model_dump(mode="json")
            if result.gate_decision is not None
            else None
        ),
        "contextual_update": _contextual_update(records),
        "model_outputs": model_outputs,
        "flash_output": [item for item in model_outputs if item["tier"] == "flash"],
        "governance_candidates": candidates,
        "planned_actions": [
            {
                "candidate_index": candidate.get("candidate_index"),
                "summary": candidate.get("summary"),
                "actions": candidate.get("planned_actions", []),
            }
            for candidate in candidates
        ],
        "actual_changes": _actual_changes(before, after, result),
        "audits": [_compact_audit(audit) for audit in audits],
        "before": [_compact_memory(item) for item in before],
        "after": [_compact_memory(item) for item in after],
        "result": {
            "saved_count": len(result.saved),
            "rejected_by_policy": result.rejected_by_policy,
            "skipped_low_confidence": result.skipped_low_confidence,
            "discarded_spans": [
                item.model_dump(mode="json") for item in result.discarded_spans
            ],
            "extraction_error": result.extraction_error,
        },
    }


async def _list_memories(
    container: MemoryContainer,
    user_id: str,
    relationship_id: str,
    limit: int,
) -> list[MemoryItem]:
    return await container.memory_store.list_memories(
        user_id=user_id,
        relationship_id=relationship_id,
        limit=limit,
    )


def _model_outputs(records: list[Any]) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for record in records:
        if not record.name.startswith(_MODEL_TRACE_PREFIX):
            continue
        details = record.details
        tier = str(details.get("tier") or "")
        if not tier:
            tier = "strong" if "strong" in record.name else "flash"
        outputs.append(
            {
                "trace_name": record.name,
                "tier": tier,
                "model": details.get("model"),
                "status": record.status.value,
                "duration_ms": round(record.duration_ms, 2),
                "repair_status": details.get("repair_status"),
                "failure_category": details.get("failure_category"),
                "raw_claims": _load_json(details.get("claim_predicates_json"), []),
            }
        )
    return outputs


def _governance_candidates(records: list[Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    json_fields = {
        "score_breakdown_json": ("score_breakdown", {}),
        "compared_memory_ids_json": ("compared_memory_ids", []),
        "strong_compared_memory_ids_json": ("strong_compared_memory_ids", []),
        "relation_target_memory_ids_json": ("relation_target_memory_ids", []),
        "planned_target_memory_ids_json": ("planned_target_memory_ids", []),
        "target_operation_indexes_json": ("target_operation_indexes", []),
        "planned_actions_json": ("planned_actions", []),
    }
    for record in records:
        if record.name != _GOVERNANCE_TRACE:
            continue
        candidate = dict(record.details)
        for source, (target, fallback) in json_fields.items():
            candidate[target] = _load_json(candidate.pop(source, None), fallback)
        candidate["trace_duration_ms"] = round(record.duration_ms, 4)
        candidates.append(candidate)
    return sorted(candidates, key=lambda item: int(item.get("candidate_index", 0)))


def _contextual_update(records: list[Any]) -> dict[str, Any] | None:
    record = next(
        (item for item in records if item.name == "memory_contextual_update"),
        None,
    )
    if record is None:
        return None
    details = dict(record.details)
    details["antecedent_candidate_ids"] = _load_json(
        details.pop("antecedent_candidate_ids_json", None),
        [],
    )
    return details


def _actual_changes(
    before: list[MemoryItem],
    after: list[MemoryItem],
    result: RememberResult,
) -> dict[str, list[dict[str, Any]]]:
    before_by_id = {item.id: item for item in before}
    after_by_id = {item.id: item for item in after}
    added: list[dict[str, Any]] = []
    merged: list[dict[str, Any]] = []
    scheduled: list[dict[str, Any]] = []
    for saved in result.saved:
        item = after_by_id.get(saved.item.id, saved.item)
        if saved.created:
            added.append(_compact_memory(item))
        else:
            old = before_by_id.get(item.id)
            merged.append(
                {
                    "memory": _compact_memory(item),
                    "new_evidence": [
                        value
                        for value in item.evidence_spans
                        if old is None or value not in old.evidence_spans
                    ],
                }
            )
        if item.expires_at is not None:
            scheduled.append(
                {
                    "memory_id": item.id,
                    "expires_at": item.expires_at.isoformat(),
                    "summary": item.summary,
                }
            )
    replaced: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []
    for memory_id, old in before_by_id.items():
        current = after_by_id.get(memory_id)
        if current is None or current.status == old.status:
            continue
        change = {
            "memory": _compact_memory(current),
            "from_status": old.status.value,
            "to_status": current.status.value,
        }
        if current.status == MemoryStatus.SUPERSEDED:
            replaced.append(change)
        if current.status == MemoryStatus.EXPIRED:
            expired.append(change)
    for memory_id in result.contextual_updated_memory_ids:
        current = after_by_id.get(memory_id)
        old = before_by_id.get(memory_id)
        if current is None:
            continue
        updated.append(
            {
                "memory": _compact_memory(current),
                "new_evidence": [
                    value
                    for value in current.evidence_spans
                    if old is None or value not in old.evidence_spans
                ],
            }
        )
    return {
        "added": added,
        "merged": merged,
        "updated": updated,
        "replaced": replaced,
        "expired": expired,
        "scheduled_expiration": scheduled,
    }


def _compact_memory(item: MemoryItem) -> dict[str, Any]:
    family = item.payload.get("canonical_concept")
    return {
        "id": item.id,
        "status": item.status.value,
        "kind": item.kind.value,
        "summary": item.summary,
        "raw_predicate": item.raw_predicate,
        "canonical_predicate": item.canonical_predicate,
        "custom_predicate": item.custom_predicate,
        "predicate_family": family if isinstance(family, str) else None,
        "state_dimension": item.state_dimension,
        "state_value": item.state_value,
        "admission_decision": (
            item.admission_decision.value if item.admission_decision is not None else None
        ),
        "admission_score": item.admission_score,
        "claim_relation": item.claim_relation.value if item.claim_relation is not None else None,
        "supersedes_id": item.supersedes_id,
        "expires_at": item.expires_at.isoformat() if item.expires_at is not None else None,
        "evidence_spans": list(item.evidence_spans),
    }


def _compact_audit(audit: MemoryTransitionAudit) -> dict[str, Any]:
    return {
        "id": audit.id,
        "incoming_memory_id": audit.incoming_memory_id,
        "target_memory_ids": list(audit.target_memory_ids),
        "decision": audit.decision.value,
        "relation": audit.relation.value,
        "rule_name": audit.rule_name,
        "admission_score": audit.admission_score,
        "score_breakdown": audit.score_breakdown,
        "raw_predicate": audit.raw_predicate,
        "canonical_predicate": audit.canonical_predicate,
        "reason": audit.reason,
    }


def _memories_for_ids(
    memory_ids: list[str],
    memory_index: dict[str, MemoryItem],
) -> list[dict[str, Any]]:
    return [
        _compact_memory(memory_index[memory_id])
        if memory_id in memory_index
        else {"id": memory_id, "missing_from_snapshot": True}
        for memory_id in memory_ids
    ]


def _load_json(value: object, fallback: Any) -> Any:
    if not isinstance(value, str) or not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _render_report(console: Console, report: dict[str, Any]) -> None:
    console.rule(f"Turn {report['turn']}: Memory V2 observation")
    overview = Table.grid(padding=(0, 2))
    overview.add_column(style="cyan", no_wrap=True)
    overview.add_column()
    gate = report.get("gate") or {}
    overview.add_row("Input", str(report["input"]))
    overview.add_row("Source message", str(report["scope"]["source_message_id"]))
    test_options = report.get("test_options") or {}
    if test_options.get("force_gate") or test_options.get("dry_run"):
        overview.add_row(
            "Test options",
            f"force_gate={test_options.get('force_gate')} "
            f"dry_run={test_options.get('dry_run')}",
        )
    overview.add_row(
        "Gate",
        f"extract={gate.get('should_extract')} reason={gate.get('reason')} "
        f"signals={gate.get('signals') or []}\n"
        f"matched_rule={gate.get('matched_rule') or '-'} "
        f"matched_span={gate.get('matched_span') or '-'}",
    )
    contextual_update = report.get("contextual_update")
    if contextual_update is not None:
        overview.add_row(
            "Contextual update",
            f"type={contextual_update.get('contextual_update_type') or '-'} "
            f"target={contextual_update.get('selected_target_memory_id') or '-'}\n"
            f"candidates={contextual_update.get('antecedent_candidate_ids') or []} "
            f"guard={contextual_update.get('target_guard_result') or '-'}\n"
            f"reason={contextual_update.get('reason') or '-'}",
        )
    if report["result"].get("extraction_error"):
        overview.add_row("Extraction error", str(report["result"]["extraction_error"]))
    console.print(overview)
    _render_model_outputs(console, report["model_outputs"])
    memory_index = {
        item["id"]: item for item in [*report["before"], *report["after"]]
    }
    _render_candidates(console, report["governance_candidates"], memory_index)
    _render_actual_changes(console, report["actual_changes"])
    _render_audits(console, report["audits"])
    _render_memory_state(console, report["after"], title="Memory state after this turn")


def _render_model_outputs(console: Console, outputs: list[dict[str, Any]]) -> None:
    table = Table(title="Model output before canonicalization")
    table.add_column("Tier", no_wrap=True)
    table.add_column("Attempt", no_wrap=True)
    table.add_column("Claim ID", no_wrap=True)
    table.add_column("Raw Predicate")
    table.add_column("Repair / failure")
    table.add_column("Time", justify="right", no_wrap=True)
    rows = 0
    for output in outputs:
        claims = output["raw_claims"] or [{"claim_id": "-", "raw_predicate": "-"}]
        for claim in claims:
            table.add_row(
                str(output["tier"]),
                str(output["trace_name"]),
                str(claim.get("claim_id") or "-"),
                str(claim.get("raw_predicate") or "-"),
                str(
                    output.get("repair_status")
                    or output.get("failure_category")
                    or output.get("status")
                ),
                f"{output['duration_ms']:.2f} ms",
            )
            rows += 1
    if rows == 0:
        table.add_row("-", "-", "-", "No model attempt was recorded", "-", "-")
    console.print(table)


def _render_candidates(
    console: Console,
    candidates: list[dict[str, Any]],
    memory_index: dict[str, dict[str, Any]],
) -> None:
    table = Table(title="Canonicalization, admission, relation, and planned writes")
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("Kind")
    table.add_column("Predicate")
    table.add_column("Alias")
    table.add_column("Family / dimension")
    table.add_column("Admission")
    table.add_column("Relation")
    table.add_column("Planned action")
    for candidate in candidates:
        canonical = candidate.get("canonical_predicate")
        custom = candidate.get("custom_predicate")
        predicate = (
            f"raw: {candidate.get('raw_predicate') or '-'}\n"
            f"canonical: {canonical or '-'}\ncustom: {custom or '-'}"
        )
        family_dimension = (
            f"family: {candidate.get('predicate_family') or '-'}\n"
            f"dimension: {candidate.get('state_dimension') or '-'}\n"
            f"value: {candidate.get('state_value') or '-'}"
        )
        score = candidate.get("admission_score")
        score_text = f"{float(score):.4f}" if isinstance(score, (int, float)) else "-"
        admission = (
            f"{candidate.get('admission_decision') or '-'} ({score_text})\n"
            f"{candidate.get('admission_reason') or '-'}\n"
            f"Strong: {candidate.get('strong_called')}"
        )
        relation = (
            f"{candidate.get('claim_relation') or '-'}\n"
            f"{candidate.get('relation_rule') or '-'}"
        )
        action_targets = _load_action_targets(candidate)
        expiration = candidate.get("expires_at")
        action = (
            f"{candidate.get('planned_action') or '-'} -> "
            f"{', '.join(_short_id(value) for value in action_targets) or '-'}"
        )
        if expiration:
            action += f"\nexpire_at: {expiration}"
        table.add_row(
            str(candidate.get("candidate_index", "-")),
            str(candidate.get("memory_kind") or "-"),
            predicate,
            "hit" if candidate.get("alias_hit") else "no",
            family_dimension,
            admission,
            relation,
            action,
        )
    if not candidates:
        table.add_row("-", "-", "No governed candidates", "-", "-", "-", "-", "-")
    console.print(table)

    for candidate in candidates:
        index = candidate.get("candidate_index", "-")
        console.print(f"Candidate {index} score breakdown:")
        score_table = Table.grid(padding=(0, 2))
        score_table.add_column(style="cyan", no_wrap=True)
        score_table.add_column()
        breakdown = candidate.get("score_breakdown") or {}
        if breakdown:
            for key, value in breakdown.items():
                score_table.add_row(str(key), _display_value(value))
        else:
            score_table.add_row("-", "No typed-admission score was produced")
        console.print(score_table)
        _render_comparison_scope(console, candidate, memory_index)


def _render_comparison_scope(
    console: Console,
    candidate: dict[str, Any],
    memory_index: dict[str, dict[str, Any]],
) -> None:
    scopes: dict[str, list[str]] = defaultdict(list)
    for memory_id in candidate.get("compared_memory_ids", []):
        scopes[memory_id].append("relation comparison")
    for memory_id in candidate.get("relation_target_memory_ids", []):
        scopes[memory_id].append("relation match")
    for memory_id in candidate.get("strong_compared_memory_ids", []):
        scopes[memory_id].append("Strong comparison")
    for memory_id in candidate.get("planned_target_memory_ids", []):
        scopes[memory_id].append("write target")
    table = Table(title="Old Memory comparison scope")
    table.add_column("Scope")
    table.add_column("Memory ID", no_wrap=True)
    table.add_column("Status / kind")
    table.add_column("Predicate")
    table.add_column("Summary")
    for memory_id, labels in scopes.items():
        memory = memory_index.get(memory_id, {"id": memory_id})
        table.add_row(
            ", ".join(labels),
            memory_id,
            f"{memory.get('status', '-')} / {memory.get('kind', '-')}",
            str(
                memory.get("canonical_predicate")
                or memory.get("custom_predicate")
                or memory.get("raw_predicate")
                or "-"
            ),
            str(memory.get("summary") or "Not present in snapshots"),
        )
    if not scopes:
        table.add_row("-", "-", "-", "-", "No old Memory was compared")
    console.print(table)


def _render_actual_changes(console: Console, changes: dict[str, list[dict[str, Any]]]) -> None:
    table = Table(title="Actual write effects")
    table.add_column("Effect", no_wrap=True)
    table.add_column("Memory ID", no_wrap=True)
    table.add_column("Status / time")
    table.add_column("Summary")
    rows = 0
    for item in changes["added"]:
        table.add_row("added", item["id"], item["status"], item["summary"])
        rows += 1
    for entry in changes["merged"]:
        item = entry["memory"]
        evidence = "; ".join(entry["new_evidence"]) or "no new evidence"
        table.add_row("merged", item["id"], item["status"], f"{item['summary']} [{evidence}]")
        rows += 1
    for entry in changes.get("updated", []):
        item = entry["memory"]
        evidence = "; ".join(entry["new_evidence"]) or "no new evidence"
        table.add_row("contextual update", item["id"], item["status"], f"{item['summary']} [{evidence}]")
        rows += 1
    for entry in changes["replaced"]:
        item = entry["memory"]
        table.add_row(
            "replaced",
            item["id"],
            f"{entry['from_status']} -> {entry['to_status']}",
            item["summary"],
        )
        rows += 1
    for entry in changes["expired"]:
        item = entry["memory"]
        table.add_row(
            "expired",
            item["id"],
            f"{entry['from_status']} -> {entry['to_status']}",
            item["summary"],
        )
        rows += 1
    for entry in changes["scheduled_expiration"]:
        table.add_row(
            "expiration scheduled",
            entry["memory_id"],
            entry["expires_at"],
            entry["summary"],
        )
        rows += 1
    if rows == 0:
        table.add_row("none", "-", "-", "No Memory write effect")
    console.print(table)


def _render_audits(console: Console, audits: list[dict[str, Any]]) -> None:
    table = Table(title="Persisted transition audit")
    table.add_column("Decision")
    table.add_column("Relation")
    table.add_column("Incoming", no_wrap=True)
    table.add_column("Targets")
    table.add_column("Rule")
    table.add_column("Reason")
    for audit in audits:
        table.add_row(
            str(audit["decision"]),
            str(audit["relation"]),
            _short_id(audit.get("incoming_memory_id")),
            ", ".join(_short_id(value) for value in audit["target_memory_ids"]) or "-",
            str(audit["rule_name"]),
            str(audit["reason"]),
        )
    if not audits:
        table.add_row("-", "-", "-", "-", "-", "No audit was written")
    console.print(table)


def _render_memory_state(
    console: Console,
    memories: list[MemoryItem] | list[dict[str, Any]],
    *,
    title: str,
) -> None:
    rows = [item if isinstance(item, dict) else _compact_memory(item) for item in memories]
    table = Table(title=title)
    table.add_column("Memory ID", no_wrap=True)
    table.add_column("Status")
    table.add_column("Kind")
    table.add_column("Predicate")
    table.add_column("Dimension / value")
    table.add_column("Summary")
    for item in rows:
        table.add_row(
            str(item["id"]),
            str(item["status"]),
            str(item["kind"]),
            str(
                item.get("canonical_predicate")
                or item.get("custom_predicate")
                or item.get("raw_predicate")
                or "-"
            ),
            f"{item.get('state_dimension') or '-'} / {item.get('state_value') or '-'}",
            str(item["summary"]),
        )
    if not rows:
        table.add_row("-", "-", "-", "-", "-", "No persisted Memory")
    console.print(table)


def _load_action_targets(candidate: dict[str, Any]) -> list[str]:
    for action in candidate.get("planned_actions", []):
        if action.get("action") in {"add", "merge", "replace"}:
            return list(action.get("target_memory_ids") or [])
    return []


def _display_value(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _short_id(value: object) -> str:
    if not isinstance(value, str) or not value:
        return "-"
    return value if len(value) <= 12 else f"{value[:8]}..."


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_main(_parse_args())))
    except KeyboardInterrupt:
        raise SystemExit(130) from None

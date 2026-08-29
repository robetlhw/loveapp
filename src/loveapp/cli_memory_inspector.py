"""Interactive and machine-readable presentation for the Memory Inspector."""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable, Iterable
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from loveapp.application.memory_inspector import MemoryInspector
from loveapp.bootstrap import build_memory_container
from loveapp.core.config import get_settings
from loveapp.domain.memory import MemoryStatus

DEFAULT_MEMORY_TEST_USER_ID = "memory-debug-user"
DEFAULT_MEMORY_TEST_RELATIONSHIP_ID = "memory-debug-relationship"
DEFAULT_MEMORY_TEST_CONVERSATION_ID = "memory-debug-conversation"

InputFunction = Callable[[str], str]
_COMMAND_NAMES = {
    "/context",
    "/exit",
    "/help",
    "/history",
    "/json",
    "/quit",
    "/reset",
    "/runs",
    "/show",
}


async def run_memory_inspector_cli(
    *,
    user_id: str = DEFAULT_MEMORY_TEST_USER_ID,
    relationship_id: str = DEFAULT_MEMORY_TEST_RELATIONSHIP_ID,
    conversation_id: str = DEFAULT_MEMORY_TEST_CONVERSATION_ID,
    requested_status: MemoryStatus = MemoryStatus.CONFIRMED,
    texts: Iterable[str] = (),
    json_output: bool = False,
    isolated: bool = False,
    limit: int = 200,
    input_fn: InputFunction = input,
    output_console: Console | None = None,
) -> list[dict[str, Any]]:
    """Build the configured Memory pipeline and run an Inspector session."""

    settings = get_settings()
    if isolated:
        settings = settings.model_copy(update={"memory_backend": "memory"})
    container = build_memory_container(settings)
    inspector = MemoryInspector(
        container.memory_service,
        container.memory_store,
        user_id=user_id,
        relationship_id=relationship_id,
        conversation_id=conversation_id,
        requested_status=requested_status,
        limit=limit,
    )
    console = output_console or Console()
    try:
        return await run_inspector_session(
            inspector,
            texts=list(texts),
            json_output=json_output,
            input_fn=input_fn,
            console=console,
        )
    finally:
        await container.aclose()


async def run_inspector_session(
    inspector: MemoryInspector,
    *,
    texts: list[str],
    json_output: bool,
    input_fn: InputFunction,
    console: Console,
) -> list[dict[str, Any]]:
    """Execute supplied turns or enter the interactive command loop."""

    if texts:
        reports = [await inspector.observe_turn(text) for text in texts]
        if json_output:
            console.print_json(data=reports)
        else:
            for report in reports:
                render_inspection_report(console, report)
        return reports

    if not json_output:
        _render_banner(console, inspector)
    reports: list[dict[str, Any]] = []
    emit_json = json_output
    while True:
        try:
            text = _normalize_input(input_fn("memory> "))
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if not text:
            continue
        if text.startswith("/"):
            should_exit, emit_json = await _handle_command(
                inspector,
                text,
                console=console,
                json_output=emit_json,
            )
            if should_exit:
                break
            continue
        report = await inspector.observe_turn(text)
        reports.append(report)
        if emit_json:
            _print_json_line(console, report)
        else:
            render_inspection_report(console, report)
    return reports


async def _handle_command(
    inspector: MemoryInspector,
    command_text: str,
    *,
    console: Console,
    json_output: bool,
) -> tuple[bool, bool]:
    try:
        parts = shlex.split(command_text)
    except ValueError as exc:
        console.print(f"[red]Invalid command:[/red] {exc}")
        return False, json_output
    command = parts[0].casefold()
    arguments = parts[1:]
    if command in {"/exit", "/quit"}:
        return True, json_output
    if command == "/help":
        _render_help(console)
        return False, json_output
    if command == "/json":
        if len(arguments) != 1 or arguments[0].casefold() not in {"on", "off"}:
            console.print("Usage: /json on|off")
            return False, json_output
        enabled = arguments[0].casefold() == "on"
        console.print(f"JSON output {'enabled' if enabled else 'disabled'}.")
        return False, enabled
    if command == "/show":
        if len(arguments) > 1:
            console.print("Usage: /show [--all|memory_id]")
            return False, json_output
        if arguments and arguments[0] != "--all":
            memory = await inspector.get_memory(arguments[0])
            payload: Any = memory or {"error": "memory_not_found", "id": arguments[0]}
            _emit_payload(console, payload, json_output=json_output, renderer=_render_memory_detail)
            return False, json_output
        memories = await inspector.list_memories(
            include_all=bool(arguments and arguments[0] == "--all")
        )
        _emit_payload(
            console,
            memories,
            json_output=json_output,
            renderer=lambda target, value: _render_memories(
                target,
                value,
                title="All memories" if arguments else "Active memories",
            ),
        )
        return False, json_output
    if command == "/context":
        if arguments:
            console.print("Usage: /context")
            return False, json_output
        context = await inspector.get_context()
        _emit_payload(console, context, json_output=json_output, renderer=_render_context)
        return False, json_output
    if command == "/history":
        if arguments:
            console.print("Usage: /history")
            return False, json_output
        history = await inspector.get_history()
        _emit_payload(console, history, json_output=json_output, renderer=_render_history)
        return False, json_output
    if command == "/runs":
        if arguments:
            console.print("Usage: /runs")
            return False, json_output
        runs = await inspector.list_runs()
        _emit_payload(console, runs, json_output=json_output, renderer=_render_runs)
        return False, json_output
    if command == "/reset":
        if arguments:
            console.print("Usage: /reset")
            return False, json_output
        result = await inspector.reset()
        _emit_payload(
            console,
            result,
            json_output=json_output,
            renderer=lambda target, value: target.print(
                Panel(
                    f"Scope reset: {value.get('user_id')} / {value.get('relationship_id')}",
                    title="RESET",
                )
            ),
        )
        return False, json_output
    console.print(f"[red]Unknown command:[/red] {command}. Use /help.")
    return False, json_output


def render_inspection_report(console: Console, report: dict[str, Any]) -> None:
    console.rule(f"Turn {report.get('turn', '-')}: Memory inspection")
    console.print(Panel(str(report.get("input", "")), title="INPUT", expand=False))
    _render_gate(console, report.get("gate") or {})
    _render_memories(console, report.get("before") or [], title="BEFORE (active)")
    _render_model_outputs(console, report.get("model_outputs") or [])
    _render_candidates(
        console,
        report.get("candidates") or report.get("governance_candidates") or [],
    )
    _render_operations(console, report.get("operations") or report.get("planned_actions") or [])
    _render_diff(console, report.get("diff") or report.get("actual_changes") or {})
    audits = report.get("audits") or []
    if audits:
        _render_audits(console, audits)
    error = report.get("extraction_error")
    if error is None:
        error = (report.get("result") or {}).get("extraction_error")
    if error:
        console.print(Panel(str(error), title="EXTRACTION ERROR", border_style="red"))
    _render_memories(console, report.get("after") or [], title="AFTER")


def _render_banner(console: Console, inspector: MemoryInspector) -> None:
    console.print(
        Panel(
            "\n".join(
                [
                    "LoveApp Memory Inspector",
                    f"User: {inspector.user_id}",
                    f"Relationship: {inspector.relationship_id}",
                    f"Conversation: {inspector.conversation_id}",
                ]
            ),
            expand=False,
        )
    )
    _render_help(console)


def _render_help(console: Console) -> None:
    table = Table(title="Commands", show_header=False)
    table.add_column(style="cyan", no_wrap=True)
    table.add_column()
    for command, description in (
        ("/show", "Show active PROPOSED and CONFIRMED memories"),
        ("/show --all", "Show active and historical memories"),
        ("/show <id>", "Show every stored field for one memory"),
        ("/context", "Show the actual RelationshipContext projection"),
        ("/history", "Show this test conversation's messages"),
        ("/runs", "Show recent extraction runs and attempts"),
        ("/reset", "Reset only this user and relationship scope"),
        ("/json on|off", "Toggle structured per-turn output"),
        ("/help", "Show commands"),
        ("/exit", "Exit the Inspector"),
    ):
        table.add_row(command, description)
    console.print(table)


def _render_gate(console: Console, gate: dict[str, Any]) -> None:
    table = Table(title="MEMORY GATE", show_header=False)
    table.add_column(style="cyan", no_wrap=True)
    table.add_column()
    for key in (
        "should_extract",
        "reason",
        "signals",
        "matched_rule",
        "matched_span",
        "contextual_probe",
        "history_loaded_for_gate",
        "antecedent_candidate_ids",
        "selected_target_memory_id",
        "target_guard_result",
        "contextual_update_type",
    ):
        table.add_row(key, _display(gate.get(key)))
    console.print(table)
    if gate.get("should_extract") is False:
        console.print(
            Panel(
                "SKIPPED - no durable memory extraction",
                title="GATE RESULT",
                border_style="yellow",
            )
        )


def _render_memories(console: Console, memories: list[dict[str, Any]], *, title: str) -> None:
    table = Table(title=title)
    table.add_column("ID", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Kind")
    table.add_column("Predicate")
    table.add_column("Dimension / value")
    table.add_column("Summary")
    for memory in memories:
        predicate = (
            memory.get("canonical_predicate")
            or memory.get("custom_predicate")
            or memory.get("raw_predicate")
            or "-"
        )
        table.add_row(
            str(memory.get("id") or "-"),
            str(memory.get("status") or "-"),
            str(memory.get("kind") or "-"),
            str(predicate),
            f"{memory.get('state_dimension') or '-'} / {memory.get('state_value') or '-'}",
            str(memory.get("summary") or "-"),
        )
    if not memories:
        table.add_row("-", "-", "-", "-", "-", "No memories")
    console.print(table)


def _render_memory_detail(console: Console, memory: dict[str, Any]) -> None:
    if memory.get("error"):
        console.print(f"[red]Memory not found:[/red] {memory.get('id')}")
        return
    table = Table(title=f"Memory {memory.get('id')}", show_header=False)
    table.add_column(style="cyan", no_wrap=True)
    table.add_column()
    for key, value in memory.items():
        table.add_row(str(key), _display(value))
    console.print(table)


def _render_model_outputs(console: Console, outputs: list[dict[str, Any]]) -> None:
    table = Table(title="EXTRACTION ATTEMPTS")
    table.add_column("Tier")
    table.add_column("Model")
    table.add_column("Status")
    table.add_column("Claims", justify="right")
    table.add_column("Raw claims")
    table.add_column("Duration", justify="right")
    table.add_column("Failure / repair")
    for output in outputs:
        claims = output.get("raw_claims") or output.get("claims") or []
        claim_text = "\n".join(
            f"{claim.get('claim_id') or '-'}: "
            f"{claim.get('raw_predicate') or claim.get('predicate') or '-'}"
            for claim in claims
            if isinstance(claim, dict)
        )
        failure_repair = "\n".join(
            str(value)
            for value in (
                output.get("failure_category") or output.get("repair_status"),
                output.get("repair_attempt"),
                output.get("repair_result"),
                output.get("validation_error"),
            )
            if value
        )
        table.add_row(
            str(output.get("tier") or "-"),
            str(output.get("model") or "-"),
            str(output.get("status") or "-"),
            str(len(claims)),
            claim_text or "-",
            f"{float(output.get('duration_ms') or 0):.2f} ms",
            failure_repair or "-",
        )
    if not outputs:
        table.add_row("-", "-", "-", "0", "-", "-", "No model attempt recorded")
    console.print(table)


def _render_candidates(console: Console, candidates: list[dict[str, Any]]) -> None:
    table = Table(title="CANDIDATE GOVERNANCE")
    table.add_column("#", justify="right")
    table.add_column("Claim")
    table.add_column("Normalization")
    table.add_column("Admission")
    table.add_column("Relation")
    table.add_column("Planned write")
    for candidate in candidates:
        confidence = candidate.get("confidence")
        claim = (
            f"{candidate.get('memory_kind') or candidate.get('kind') or '-'}\n"
            f"{candidate.get('summary') or '-'}\n"
            f"subject={candidate.get('subject') or '-'} "
            f"confidence={confidence if confidence is not None else '-'}"
        )
        normalization = (
            f"raw={candidate.get('raw_predicate') or '-'}\n"
            f"canonical={candidate.get('canonical_predicate') or '-'}\n"
            f"custom={candidate.get('custom_predicate') or '-'}\n"
            f"{candidate.get('state_dimension') or '-'} / {candidate.get('state_value') or '-'}"
        )
        admission_score = candidate.get("admission_score")
        admission = (
            f"{candidate.get('admission_decision') or '-'} "
            f"score={admission_score if admission_score is not None else '-'}\n"
            f"{candidate.get('admission_reason') or '-'}\n"
            f"strong_called={candidate.get('strong_called') or False}"
        )
        relation = (
            f"{candidate.get('claim_relation') or '-'}\n"
            f"rule={candidate.get('relation_rule') or '-'}\n"
            f"targets={_display(candidate.get('relation_target_memory_ids') or [])}"
        )
        planned_target_ids = candidate.get("planned_target_memory_ids") or []
        if candidate.get("planned_action") == "merge" and not planned_target_ids:
            planned_target_ids = candidate.get("relation_target_memory_ids") or []
        planned = (
            f"{candidate.get('planned_action') or '-'}\n"
            f"targets={_display(planned_target_ids)}"
        )
        table.add_row(
            str(candidate.get("candidate_index", "-")),
            claim,
            normalization,
            admission,
            relation,
            planned,
        )
    if not candidates:
        table.add_row("-", "No candidates", "-", "-", "-", "-")
    console.print(table)


def _render_operations(console: Console, operations: list[dict[str, Any]]) -> None:
    table = Table(title="PLANNED OPERATIONS")
    table.add_column("Candidate")
    table.add_column("Action")
    table.add_column("Targets")
    table.add_column("Rule / reason")
    for operation in operations:
        actions = operation.get("actions")
        if actions:
            for action in actions:
                table.add_row(
                    str(operation.get("candidate_index", "-")),
                    str(action.get("action") or "-"),
                    _display(action.get("target_memory_ids") or []),
                    str(action.get("rule_name") or action.get("reason") or "-"),
                )
            continue
        table.add_row(
            str(operation.get("candidate_index", "-")),
            str(operation.get("action") or operation.get("planned_action") or "-"),
            _display(operation.get("target_memory_ids") or []),
            str(
                operation.get("rule_name")
                or operation.get("rule")
                or operation.get("reason")
                or "-"
            ),
        )
    if not operations:
        table.add_row("-", "none", "-", "No planned write")
    console.print(table)


def _render_diff(console: Console, diff: dict[str, Any]) -> None:
    table = Table(title="MEMORY DIFF")
    table.add_column("Effect", no_wrap=True)
    table.add_column("Memory ID", no_wrap=True)
    table.add_column("Status")
    table.add_column("Summary / evidence")
    rows = 0
    aliases = (
        ("added", "ADDED"),
        ("merged", "MERGED"),
        ("updated", "UPDATED"),
        ("superseded", "SUPERSEDED"),
        ("replaced", "SUPERSEDED"),
        ("expired", "EXPIRED"),
        ("rejected", "REJECTED"),
    )
    seen_entries: set[tuple[str, str]] = set()
    for key, label in aliases:
        for entry in diff.get(key) or []:
            memory = entry.get("memory", entry)
            memory_id = str(memory.get("id") or entry.get("memory_id") or "-")
            identity = (label, memory_id)
            if identity in seen_entries:
                continue
            seen_entries.add(identity)
            before_status = entry.get("from_status")
            after_status = entry.get("to_status") or memory.get("status")
            status = (
                f"{before_status} -> {after_status}"
                if before_status is not None
                else str(after_status or "-")
            )
            evidence = entry.get("new_evidence") or []
            summary = str(memory.get("summary") or entry.get("reason") or "-")
            if evidence:
                summary += f"\nnew evidence: {_display(evidence)}"
            table.add_row(label, memory_id, status, summary)
            rows += 1
    if rows == 0:
        table.add_row("NONE", "-", "-", "No persisted memory changes.")
    console.print(table)


def _render_audits(console: Console, audits: list[dict[str, Any]]) -> None:
    table = Table(title="TRANSITION AUDITS")
    table.add_column("Decision")
    table.add_column("Relation")
    table.add_column("Incoming")
    table.add_column("Targets")
    table.add_column("Rule")
    table.add_column("Reason")
    for audit in audits:
        table.add_row(
            str(audit.get("decision") or "-"),
            str(audit.get("relation") or "-"),
            str(audit.get("incoming_memory_id") or "-"),
            _display(audit.get("target_memory_ids") or []),
            str(audit.get("rule_name") or "-"),
            str(audit.get("reason") or "-"),
        )
    console.print(table)


def _render_context(console: Console, context: dict[str, Any]) -> None:
    overview = Table(title="RELATIONSHIP CONTEXT", show_header=False)
    overview.add_column(style="cyan", no_wrap=True)
    overview.add_column()
    overview.add_row("relationship_stage", _display(context.get("relationship_stage")))
    overview.add_row("relationship_evidence", _display(context.get("relationship_evidence")))
    console.print(overview)
    sections = (
        "confirmed_current_state",
        "confirmed_long_term",
        "uncertain_items",
        "conflicted_items",
        "action_intents",
        "planned_events",
        "recent_events",
        "user_preferences",
        "partner_preferences",
        "active_plans",
    )
    table = Table(title="Context projections")
    table.add_column("Projection", no_wrap=True)
    table.add_column("Items")
    for section in sections:
        table.add_row(section, _context_items(context.get(section)))
    console.print(table)


def _render_history(console: Console, history: list[dict[str, Any]]) -> None:
    table = Table(title="CONVERSATION HISTORY")
    table.add_column("Time")
    table.add_column("Role")
    table.add_column("Message ID")
    table.add_column("Content")
    for message in history:
        table.add_row(
            str(message.get("created_at") or "-"),
            str(message.get("role") or "-"),
            str(message.get("id") or "-"),
            str(message.get("content") or "-"),
        )
    if not history:
        table.add_row("-", "-", "-", "No messages")
    console.print(table)


def _render_runs(console: Console, runs: list[dict[str, Any]]) -> None:
    table = Table(title="MEMORY EXTRACTION RUNS")
    table.add_column("Run / status")
    table.add_column("Gate")
    table.add_column("Attempt")
    table.add_column("Model / tier")
    table.add_column("Duration / tokens")
    table.add_column("Claims / saved")
    table.add_column("Error")
    for run in runs:
        attempts = run.get("attempts") or [None]
        for index, attempt in enumerate(attempts):
            attempt = attempt or {}
            token_text = "/".join(
                str(attempt.get(key) if attempt.get(key) is not None else "-")
                for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens")
            )
            claim_count = attempt.get("claim_count")
            table.add_row(
                (
                    f"{run.get('id') or '-'}\n{run.get('status') or '-'}"
                    if index == 0
                    else ""
                ),
                str(run.get("gate_reason") or run.get("gate_decision", {}).get("reason") or "-")
                if index == 0
                else "",
                str(attempt.get("attempt") or "-"),
                f"{attempt.get('model') or '-'} / {attempt.get('tier') or '-'}",
                f"{attempt.get('duration_ms') or '-'} ms\nP/C/R={token_text}",
                f"claims={claim_count if claim_count is not None else '-'}\n"
                f"saved={_display(run.get('saved_memory_ids') or [])}",
                str(attempt.get("error") or run.get("error") or "-"),
            )
    if not runs:
        table.add_row("-", "-", "-", "-", "-", "-", "No runs")
    console.print(table)


def _emit_payload(
    console: Console,
    payload: Any,
    *,
    json_output: bool,
    renderer: Callable[[Console, Any], None],
) -> None:
    if json_output:
        _print_json_line(console, payload)
    else:
        renderer(console, payload)


def _print_json_line(console: Console, payload: Any) -> None:
    console.print(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        highlight=False,
        markup=False,
        soft_wrap=True,
    )


def _context_items(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return "-"
    rendered: list[str] = []
    for item in value:
        if isinstance(item, dict):
            rendered.append(str(item.get("summary") or item.get("title") or item))
        else:
            rendered.append(str(item))
    return "\n".join(rendered)


def _display(value: Any) -> str:
    if value is None or value == "" or value == [] or value == {}:
        return "-"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _normalize_input(value: str) -> str:
    text = value.strip().lstrip("\ufeff")
    slash_index = text.find("/")
    if 0 < slash_index <= 3:
        candidate = text[slash_index:]
        if candidate.split(maxsplit=1)[0].casefold() in _COMMAND_NAMES:
            return candidate
    return text


__all__ = [
    "DEFAULT_MEMORY_TEST_CONVERSATION_ID",
    "DEFAULT_MEMORY_TEST_RELATIONSHIP_ID",
    "DEFAULT_MEMORY_TEST_USER_ID",
    "render_inspection_report",
    "run_inspector_session",
    "run_memory_inspector_cli",
]

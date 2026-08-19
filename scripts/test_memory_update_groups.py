"""Interactively test Memory updates in fully isolated groups."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from rich.console import Console
from rich.table import Table

from loveapp.bootstrap import MemoryContainer, build_memory_container
from loveapp.core.config import Settings
from loveapp.domain.memory import MemoryStatus

# Direct execution puts scripts/ rather than the repository root first on
# sys.path. Keep the existing observer as the single source of trace rendering.
_SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(_SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIRECTORY))

from observe_memory_system import (  # noqa: E402
    _compact_memory,
    _configure_test_options,
    _list_memories,
    _observe_turn,
    _render_memory_state,
    _render_report,
)


@dataclass(frozen=True)
class GroupScope:
    index: int
    user_id: str
    relationship_id: str
    conversation_id: str


ContainerFactory = Callable[[GroupScope], MemoryContainer]
InputFunction = Callable[[str], str]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Interactively test multiple Memory update conversations. Each group "
            "uses a new in-memory store and independent scope IDs."
        )
    )
    parser.add_argument(
        "--status",
        choices=(MemoryStatus.PROPOSED.value, MemoryStatus.CONFIRMED.value),
        default=MemoryStatus.PROPOSED.value,
        help="Requested input status; typed admission policy still applies.",
    )
    parser.add_argument(
        "--force-gate",
        action="store_true",
        help="Test only: run extraction when the normal Memory Gate would skip it.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test only: show planned writes without committing them.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum memories shown in each snapshot.",
    )
    parser.add_argument(
        "--scope-prefix",
        default="memory-update-test",
        help="Prefix used for generated user, relationship, and conversation IDs.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optionally save every group, turn trace, and final Memory state as JSON.",
    )
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 1000:
        parser.error("--limit must be between 1 and 1000")
    if not args.scope_prefix.strip():
        parser.error("--scope-prefix cannot be empty")
    return args


async def _main(args: argparse.Namespace) -> int:
    console = Console()
    run_id = uuid4().hex[:12]
    started_at = datetime.now(UTC)
    groups = await _run_grouped_session(
        console=console,
        run_id=run_id,
        scope_prefix=args.scope_prefix.strip(),
        requested_status=MemoryStatus(args.status),
        limit=args.limit,
        force_gate=args.force_gate,
        dry_run=args.dry_run,
    )
    payload = {
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "test_options": {
            "requested_status": args.status,
            "force_gate": args.force_gate,
            "dry_run": args.dry_run,
        },
        "groups": groups,
    }
    _render_run_summary(console, groups)
    if args.output is not None:
        output_path = args.output.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        console.print(f"Results saved to [cyan]{output_path}[/cyan]")
    return 0


async def _run_grouped_session(
    *,
    console: Console,
    run_id: str,
    scope_prefix: str,
    requested_status: MemoryStatus,
    limit: int,
    force_gate: bool,
    dry_run: bool,
    input_fn: InputFunction = input,
    container_factory: ContainerFactory | None = None,
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    group_index = 1
    stop_all = False
    factory = container_factory or _default_container_factory(
        force_gate=force_gate,
        dry_run=dry_run,
    )

    console.print(
        "Memory update group tester is ready. Enter one user message per turn.\n"
        "Type [bold cyan]s[/bold cyan] to finish this group and create a new isolated "
        "group; [bold cyan]state[/bold cyan] to inspect the current group; "
        "[bold cyan]q[/bold cyan] to finish all testing."
    )
    while not stop_all:
        scope = _build_group_scope(scope_prefix, run_id, group_index)
        container = factory(scope)
        reports: list[dict[str, Any]] = []
        end_reason = "next_group"
        console.rule(f"Group {group_index}: isolated Memory Store")
        _render_scope(console, scope)
        try:
            while True:
                try:
                    text = input_fn(f"group-{group_index} memory> ").strip()
                except EOFError:
                    end_reason = "eof"
                    stop_all = True
                    break
                except KeyboardInterrupt:
                    console.print()
                    end_reason = "interrupted"
                    stop_all = True
                    break

                if not text:
                    continue
                command = text.casefold()
                if command in {"s", ":s", ":next"}:
                    end_reason = "next_group"
                    break
                if command in {"q", ":q", ":quit", ":exit"}:
                    end_reason = "quit"
                    stop_all = True
                    break
                if command in {"state", ":state"}:
                    memories = await _list_memories(
                        container,
                        scope.user_id,
                        scope.relationship_id,
                        limit,
                    )
                    _render_memory_state(
                        console,
                        memories,
                        title=f"Group {group_index} current Memory state",
                    )
                    continue

                report = await _observe_turn(
                    container,
                    text=text,
                    turn=len(reports) + 1,
                    user_id=scope.user_id,
                    relationship_id=scope.relationship_id,
                    conversation_id=scope.conversation_id,
                    requested_status=requested_status,
                    limit=limit,
                    force_gate=force_gate,
                    dry_run=dry_run,
                )
                reports.append(report)
                _render_report(console, report)
        finally:
            final_memories = await _list_memories(
                container,
                scope.user_id,
                scope.relationship_id,
                limit,
            )
            console.rule(f"Group {group_index} complete")
            _render_memory_state(
                console,
                final_memories,
                title=f"Group {group_index} final Memory state",
            )
            groups.append(
                {
                    "group": group_index,
                    "scope": {
                        "user_id": scope.user_id,
                        "relationship_id": scope.relationship_id,
                        "conversation_id": scope.conversation_id,
                    },
                    "end_reason": end_reason,
                    "turn_count": len(reports),
                    "turns": reports,
                    "final_memories": [
                        _compact_memory(memory) for memory in final_memories
                    ],
                }
            )
            await container.aclose()

        if not stop_all:
            group_index += 1
    return groups


def _default_container_factory(*, force_gate: bool, dry_run: bool) -> ContainerFactory:
    settings = Settings().model_copy(update={"memory_backend": "memory"})

    def build(_: GroupScope) -> MemoryContainer:
        container = build_memory_container(settings)
        _configure_test_options(
            container,
            force_gate=force_gate,
            dry_run=dry_run,
        )
        return container

    return build


def _build_group_scope(prefix: str, run_id: str, group_index: int) -> GroupScope:
    group_token = f"{prefix}-{run_id}-group-{group_index:03d}"
    return GroupScope(
        index=group_index,
        user_id=f"{group_token}-user",
        relationship_id=f"{group_token}-relationship",
        conversation_id=f"{group_token}-conversation",
    )


def _render_scope(console: Console, scope: GroupScope) -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="cyan", no_wrap=True)
    table.add_column()
    table.add_row("User", scope.user_id)
    table.add_row("Relationship", scope.relationship_id)
    table.add_row("Conversation", scope.conversation_id)
    console.print(table)


def _render_run_summary(console: Console, groups: list[dict[str, Any]]) -> None:
    table = Table(title="Isolated group test summary")
    table.add_column("Group", justify="right")
    table.add_column("Turns", justify="right")
    table.add_column("Final memories", justify="right")
    table.add_column("End reason")
    table.add_column("Relationship scope")
    for group in groups:
        table.add_row(
            str(group["group"]),
            str(group["turn_count"]),
            str(len(group["final_memories"])),
            str(group["end_reason"]),
            str(group["scope"]["relationship_id"]),
        )
    if not groups:
        table.add_row("-", "-", "-", "No group was tested", "-")
    console.print(table)


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_main(_parse_args())))
    except KeyboardInterrupt:
        raise SystemExit(130) from None

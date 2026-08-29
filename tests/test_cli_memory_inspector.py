import json
from io import StringIO
from typing import Any

from rich.console import Console
from typer.testing import CliRunner

import loveapp.cli as cli_module
from loveapp.cli_memory_inspector import render_inspection_report, run_inspector_session
from loveapp.domain.memory import MemoryStatus


class FakeInspector:
    user_id = "memory-debug-user"
    relationship_id = "memory-debug-relationship"
    conversation_id = "memory-debug-conversation"

    def __init__(self) -> None:
        self.include_all_calls: list[bool] = []
        self.context_calls = 0
        self.reset_calls = 0

    async def observe_turn(self, text: str) -> dict[str, Any]:
        return _report(text)

    async def list_memories(self, *, include_all: bool = False) -> list[dict[str, Any]]:
        self.include_all_calls.append(include_all)
        return [_memory("superseded" if include_all else "confirmed")]

    async def get_memory(self, memory_id: str) -> dict[str, Any]:
        return {**_memory("confirmed"), "id": memory_id}

    async def get_context(self) -> dict[str, Any]:
        self.context_calls += 1
        return {
            "relationship_stage": "unknown",
            "relationship_evidence": {},
            "confirmed_current_state": [],
        }

    async def get_history(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "message-1",
                "role": "user",
                "content": "hello",
                "created_at": "2026-08-29T00:00:00Z",
            }
        ]

    async def list_runs(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "run-1",
                "status": "completed",
                "gate_decision": {"reason": "durable_signal"},
                "attempts": [],
                "saved_memory_ids": ["memory-1"],
                "error": None,
            }
        ]

    async def reset(self) -> dict[str, Any]:
        self.reset_calls += 1
        return {
            "reset": True,
            "user_id": self.user_id,
            "relationship_id": self.relationship_id,
        }


def test_memory_test_command_passes_fixed_defaults_and_json_turns(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_runner(**kwargs: Any) -> list[dict[str, Any]]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(cli_module, "run_memory_inspector_cli", fake_runner)
    result = CliRunner().invoke(
        cli_module.app,
        ["memory-test", "--json", "--isolated", "--text", "hello"],
    )

    assert result.exit_code == 0, result.output
    assert captured["user_id"] == "memory-debug-user"
    assert captured["relationship_id"] == "memory-debug-relationship"
    assert captured["conversation_id"] == "memory-debug-conversation"
    assert captured["requested_status"] == MemoryStatus.CONFIRMED
    assert captured["texts"] == ["hello"]
    assert captured["json_output"] is True
    assert captured["isolated"] is True


async def test_noninteractive_json_is_one_stable_document() -> None:
    output = StringIO()
    reports = await run_inspector_session(
        FakeInspector(),  # type: ignore[arg-type]
        texts=["hello"],
        json_output=True,
        input_fn=lambda _: "/exit",
        console=Console(file=output, force_terminal=False, color_system=None),
    )

    payload = json.loads(output.getvalue())
    assert payload == reports
    assert payload[0]["input"] == "hello"
    assert payload[0]["before"][0]["id"] == "memory-1"
    assert payload[0]["candidates"][0]["claim_relation"] == "same"


async def test_interactive_commands_delegate_to_inspector() -> None:
    inspector = FakeInspector()
    commands = iter(
        [
            "/show",
            "/show --all",
            "/show memory-1",
            "/context",
            "/history",
            "/runs",
            "/reset",
            "/exit",
        ]
    )
    output = StringIO()

    await run_inspector_session(
        inspector,  # type: ignore[arg-type]
        texts=[],
        json_output=False,
        input_fn=lambda _: next(commands),
        console=Console(file=output, width=180, force_terminal=False, color_system=None),
    )

    rendered = output.getvalue()
    assert inspector.include_all_calls == [False, True]
    assert inspector.context_calls == 1
    assert inspector.reset_calls == 1
    assert "RELATIONSHIP CONTEXT" in rendered
    assert "CONVERSATION HISTORY" in rendered
    assert "MEMORY EXTRACTION RUNS" in rendered
    assert "Scope reset" in rendered


def test_skipped_and_failed_turn_render_without_losing_error() -> None:
    report = _report("hello")
    report["gate"] = {"should_extract": False, "reason": "casual"}
    report["extraction_error"] = "invalid extraction response"
    output = StringIO()

    render_inspection_report(
        Console(file=output, width=180, force_terminal=False, color_system=None),
        report,
    )

    rendered = output.getvalue()
    assert "SKIPPED - no durable memory extraction" in rendered
    assert "invalid extraction response" in rendered
    assert "memory-1" in rendered
    assert "same" in rendered


def _report(text: str) -> dict[str, Any]:
    return {
        "turn": 1,
        "input": text,
        "gate": {"should_extract": True, "reason": "durable_signal"},
        "before": [_memory("confirmed")],
        "model_outputs": [],
        "candidates": [
            {
                "candidate_index": 0,
                "memory_kind": "preference",
                "summary": "Partner likes sushi.",
                "subject": "partner",
                "confidence": 0.9,
                "canonical_predicate": "preference.food.cuisine",
                "admission_decision": "confirm",
                "claim_relation": "same",
                "relation_target_memory_ids": ["memory-1"],
                "planned_action": "merge",
                "planned_target_memory_ids": ["memory-1"],
            }
        ],
        "operations": [
            {
                "candidate_index": 0,
                "action": "merge",
                "target_memory_ids": ["memory-1"],
            }
        ],
        "diff": {"merged": [{"memory": _memory("confirmed")}]},
        "audits": [],
        "after": [_memory("confirmed")],
        "extraction_error": None,
    }


def _memory(status: str) -> dict[str, Any]:
    return {
        "id": "memory-1",
        "status": status,
        "kind": "preference",
        "summary": "Partner likes sushi.",
        "canonical_predicate": "preference.food.cuisine",
        "state_dimension": None,
        "state_value": None,
    }

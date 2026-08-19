import runpy
from collections.abc import Iterator
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from rich.console import Console

from loveapp.adapters.memory.in_memory import InMemoryMemoryStore
from loveapp.application.memory import MemoryService
from loveapp.bootstrap import MemoryContainer
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    EvidenceExplicitness,
    MemoryKind,
    MemoryStatus,
    TimeKind,
)

_GROUP_SCRIPT = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts" / "test_memory_update_groups.py")
)
_run_grouped_session = _GROUP_SCRIPT["_run_grouped_session"]

NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


class EchoPreferenceExtractor:
    async def extract(self, text: str, **kwargs: object) -> AtomicExtraction:
        del kwargs
        return AtomicExtraction(
            claims=[
                AtomicClaim(
                    claim_id="preference",
                    kind=MemoryKind.PREFERENCE,
                    subject="partner",
                    predicate="likes_cuisine",
                    summary="对方喜欢寿司",
                    evidence_spans=[text],
                    time_kind=TimeKind.TIMELESS,
                    confidence=1.0,
                    explicitness=EvidenceExplicitness.EXPLICIT,
                    payload={
                        "preference": "寿司",
                        "preference_type": "cuisine",
                    },
                )
            ]
        )


async def test_s_starts_a_new_group_with_a_fresh_memory_store() -> None:
    stores: list[InMemoryMemoryStore] = []

    def container_factory(scope: object) -> MemoryContainer:
        del scope
        store = InMemoryMemoryStore(clock=lambda: NOW)
        stores.append(store)
        return MemoryContainer(
            memory_service=MemoryService(
                store,
                EchoPreferenceExtractor(),
                clock=lambda: NOW,
            ),
            memory_store=store,
        )

    values: Iterator[str] = iter(
        [
            "记一下：她喜欢寿司。",
            "s",
            "记一下：她喜欢寿司。",
            "q",
        ]
    )
    output = StringIO()

    groups = await _run_grouped_session(
        console=Console(file=output, width=180),
        run_id="isolated-run",
        scope_prefix="memory-test",
        requested_status=MemoryStatus.PROPOSED,
        limit=20,
        force_gate=False,
        dry_run=False,
        input_fn=lambda _: next(values),
        container_factory=container_factory,
    )

    assert len(stores) == 2
    assert stores[0] is not stores[1]
    assert len(groups) == 2
    assert [group["turn_count"] for group in groups] == [1, 1]
    assert [group["end_reason"] for group in groups] == ["next_group", "quit"]
    assert groups[0]["scope"] != groups[1]["scope"]
    assert len(groups[0]["final_memories"]) == 1
    assert len(groups[1]["final_memories"]) == 1
    assert groups[0]["final_memories"][0]["id"] != groups[1]["final_memories"][0]["id"]
    assert groups[0]["turns"][0]["actual_changes"]["added"]
    assert groups[1]["turns"][0]["actual_changes"]["added"]

    rendered = output.getvalue()
    assert "Group 1: isolated Memory Store" in rendered
    assert "Group 2: isolated Memory Store" in rendered
    assert "Group 1 final Memory state" in rendered
    assert "Group 2 final Memory state" in rendered


async def test_state_command_does_not_create_a_memory_turn() -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    values: Iterator[str] = iter(["state", "q"])

    groups = await _run_grouped_session(
        console=Console(file=StringIO(), width=180),
        run_id="state-run",
        scope_prefix="memory-test",
        requested_status=MemoryStatus.PROPOSED,
        limit=20,
        force_gate=False,
        dry_run=False,
        input_fn=lambda _: next(values),
        container_factory=lambda _: MemoryContainer(
            memory_service=MemoryService(
                store,
                EchoPreferenceExtractor(),
                clock=lambda: NOW,
            ),
            memory_store=store,
        ),
    )

    assert groups[0]["turn_count"] == 0
    assert groups[0]["final_memories"] == []

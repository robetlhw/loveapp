import runpy
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from rich.console import Console

from loveapp.adapters.memory.in_memory import InMemoryMemoryStore
from loveapp.application.memory import MemoryService
from loveapp.application.memory_gate import MemoryGate
from loveapp.bootstrap import MemoryContainer
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    EvidenceExplicitness,
    MemoryKind,
    MemoryStatus,
    TimeKind,
)

_OBSERVER_SCRIPT = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts" / "observe_memory_system.py")
)
_observe_turn = _OBSERVER_SCRIPT["_observe_turn"]
_render_report = _OBSERVER_SCRIPT["_render_report"]
_configure_test_options = _OBSERVER_SCRIPT["_configure_test_options"]
_forced_memory_gate = _OBSERVER_SCRIPT["_ForcedMemoryGate"]

NOW = datetime(2026, 8, 6, 12, tzinfo=UTC)


class SequenceExtractor:
    def __init__(self, extractions: list[AtomicExtraction]) -> None:
        self._extractions = list(extractions)

    async def extract(self, text: str, **kwargs: object) -> AtomicExtraction:
        del text, kwargs
        return self._extractions.pop(0)


async def test_observer_report_shows_same_comparison_and_merge_effect() -> None:
    first_text = "\u8bb0\u4e00\u4e0b: sushi is her favorite."
    second_text = "\u8bb0\u4e00\u4e0b: she still chooses sushi first."
    extractions = [
        AtomicExtraction(claims=[_sushi_claim("first", first_text)]),
        AtomicExtraction(claims=[_sushi_claim("second", second_text)]),
    ]
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(store, SequenceExtractor(extractions), clock=lambda: NOW)
    container = MemoryContainer(memory_service=service, memory_store=store)

    first = await _observe_turn(
        container,
        text=first_text,
        turn=1,
        user_id="observer-user",
        relationship_id="observer-relationship",
        conversation_id="observer-conversation",
        requested_status=MemoryStatus.CONFIRMED,
        limit=20,
    )
    second = await _observe_turn(
        container,
        text=second_text,
        turn=2,
        user_id="observer-user",
        relationship_id="observer-relationship",
        conversation_id="observer-conversation",
        requested_status=MemoryStatus.CONFIRMED,
        limit=20,
    )

    created_id = first["actual_changes"]["added"][0]["id"]
    candidate = second["governance_candidates"][0]
    assert candidate["canonical_predicate"] == "preference.food.cuisine"
    assert candidate["claim_relation"] == "same"
    assert candidate["planned_action"] == "merge"
    assert candidate["compared_memory_ids"] == [created_id]
    assert candidate["relation_target_memory_ids"] == [created_id]
    assert candidate["planned_actions"][0] == {
        "action": "merge",
        "target_memory_ids": [created_id],
        "target_operation_indexes": [],
    }
    assert second["actual_changes"]["merged"][0]["memory"]["id"] == created_id
    assert second_text in second["actual_changes"]["merged"][0]["new_evidence"]

    output = StringIO()
    _render_report(Console(file=output, width=180), second)
    rendered = output.getvalue()
    assert "Canonicalization, admission, relation, and planned writes" in rendered
    assert "likes_cuisine" in rendered
    assert "merged" in rendered


async def test_observer_force_gate_and_dry_run_are_test_only() -> None:
    source_text = "This message has no durable signal."
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor([AtomicExtraction(claims=[_sushi_claim("forced", source_text)])]),
        clock=lambda: NOW,
    )
    container = MemoryContainer(memory_service=service, memory_store=store)
    _configure_test_options(container, force_gate=True, dry_run=True)

    forced = _forced_memory_gate(MemoryGate()).evaluate(source_text)
    assert forced.should_extract is True
    assert forced.reason.value == "forced"
    assert "force_gate" in forced.signals
    assert forced.matched_rule == "no_durable_signal"
    assert forced.matched_span is None

    report = await _observe_turn(
        container,
        text=source_text,
        turn=1,
        user_id="observer-user",
        relationship_id="observer-relationship",
        conversation_id="observer-conversation",
        requested_status=MemoryStatus.CONFIRMED,
        limit=20,
        force_gate=True,
        dry_run=True,
    )

    assert report["test_options"] == {"force_gate": True, "dry_run": True}
    assert report["gate"]["reason"] == "forced"
    assert report["gate"]["matched_rule"] == "no_durable_signal"
    assert report["governance_candidates"][0]["planned_action"] == "add"
    assert report["actual_changes"]["added"] == []
    assert report["after"] == []


def _sushi_claim(claim_id: str, evidence: str) -> AtomicClaim:
    return AtomicClaim(
        claim_id=claim_id,
        kind=MemoryKind.PREFERENCE,
        subject="partner",
        predicate="likes_cuisine",
        summary="Partner prefers sushi.",
        evidence_spans=[evidence],
        time_kind=TimeKind.TIMELESS,
        confidence=1.0,
        explicitness=EvidenceExplicitness.EXPLICIT,
        payload={
            "preference": "sushi",
            "preference_type": "cuisine",
        },
    )

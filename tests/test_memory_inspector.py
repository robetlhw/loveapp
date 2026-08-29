import json
from datetime import UTC, datetime

from loveapp.adapters.memory.in_memory import InMemoryMemoryStore
from loveapp.application.memory import MemoryService
from loveapp.application.memory_inspector import MemoryInspector, _model_outputs
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    EvidenceExplicitness,
    MemoryKind,
    TimeKind,
)

NOW = datetime(2026, 8, 29, 12, tzinfo=UTC)
SCOPE = {
    "user_id": "inspector-user",
    "relationship_id": "inspector-relationship",
    "conversation_id": "inspector-conversation",
}


class SequenceExtractor:
    def __init__(self, *extractions: AtomicExtraction) -> None:
        self._extractions = iter(extractions)
        self.calls = 0

    async def extract(self, text: str, **kwargs: object) -> AtomicExtraction:
        del text, kwargs
        self.calls += 1
        return next(self._extractions)


class FailingExtractor:
    async def extract(self, text: str, **kwargs: object) -> AtomicExtraction:
        del text, kwargs
        raise ValueError("invalid extraction response")


async def test_inspector_runs_real_service_and_reports_persisted_add() -> None:
    text = "\u8bb0\u4e00\u4e0b: sushi is her favorite cuisine."
    extractor = SequenceExtractor(
        AtomicExtraction(claims=[_sushi_claim("first", text)])
    )
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(store, extractor, clock=lambda: NOW)
    inspector = MemoryInspector(service, store, **SCOPE)

    report = await inspector.execute_turn(text)

    assert extractor.calls == 1
    assert report["input"] == text
    assert report["gate"]["should_extract"] is True
    assert report["before"] == []
    assert report["candidates"][0]["canonical_predicate"] == "preference.food.cuisine"
    assert report["operations"][0]["action"] == "add"
    assert len(report["diff"]["added"]) == 1
    assert report["diff"]["added"][0]["id"] == report["after"][0]["id"]
    assert report["extraction_run"]["status"] == "completed"
    assert report["audits"][0]["incoming_memory_id"] == report["after"][0]["id"]
    assert report["extraction_error"] is None
    json.dumps(report)


async def test_inspector_diff_reports_lifecycle_supersession() -> None:
    active_text = "\u8bb0\u4e00\u4e0b: our current conflict is active."
    resolved_text = "\u8bb0\u4e00\u4e0b: our current conflict is resolved."
    extractor = SequenceExtractor(
        AtomicExtraction(claims=[_conflict_claim("active", active_text)]),
        AtomicExtraction(claims=[_conflict_claim("resolved", resolved_text)]),
    )
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(store, extractor, clock=lambda: NOW)
    inspector = MemoryInspector(service, store, **SCOPE)

    first = await inspector.execute_turn(active_text)
    second = await inspector.execute_turn(resolved_text)

    old_id = first["diff"]["added"][0]["id"]
    assert second["candidates"][0]["claim_relation"] == "update"
    assert second["operations"][0]["action"] == "replace"
    assert second["diff"]["superseded"][0]["memory"]["id"] == old_id
    assert second["diff"]["superseded"][0]["from_status"] == "confirmed"
    assert second["diff"]["superseded"][0]["to_status"] == "superseded"
    assert second["diff"]["added"][0]["supersedes_id"] == old_id
    assert second["audits"][0]["rule_name"] == "resolve_active_conflict"

    active = await inspector.list_memories()
    all_memories = await inspector.list_memories(include_all=True)
    assert [item["state_value"] for item in active] == ["resolved"]
    assert {item["status"] for item in all_memories} == {"confirmed", "superseded"}
    assert await inspector.get_memory(old_id) == next(
        item for item in all_memories if item["id"] == old_id
    )


async def test_inspector_exposes_skip_failure_history_context_runs_and_reset() -> None:
    skipped_store = InMemoryMemoryStore(clock=lambda: NOW)
    skipped = MemoryInspector(
        MemoryService(skipped_store, FailingExtractor(), clock=lambda: NOW),
        skipped_store,
        **SCOPE,
    )

    skipped_report = await skipped.execute_turn("Thanks.")

    assert skipped_report["gate"]["should_extract"] is False
    assert skipped_report["candidates"] == []
    assert skipped_report["operations"] == []
    assert skipped_report["diff"]["added"] == []
    assert skipped_report["extraction_run"]["status"] == "skipped"

    failure_store = InMemoryMemoryStore(clock=lambda: NOW)
    failure_service = MemoryService(failure_store, FailingExtractor(), clock=lambda: NOW)
    failure = MemoryInspector(failure_service, failure_store, **SCOPE)
    failed_report = await failure.execute_turn(
        "\u8bb0\u4e00\u4e0b: she has contacted me every day for the last month."
    )

    assert failed_report["extraction_error"] == "invalid extraction response"
    assert failed_report["extraction_run"]["status"] == "failed"
    assert failed_report["extraction_run"]["error"] == "invalid extraction response"
    assert [message["content"] for message in await failure.get_history()] == [
        "\u8bb0\u4e00\u4e0b: she has contacted me every day for the last month."
    ]
    context = await failure.get_context()
    assert context["user_id"] == SCOPE["user_id"]
    assert context["relationship_id"] == SCOPE["relationship_id"]
    assert (await failure.list_runs())[0]["status"] == "failed"

    reset_result = await failure.reset()
    assert reset_result["reset"] is True
    assert await failure.get_history() == []
    assert await failure.list_memories(include_all=True) == []
    assert await failure.list_runs() == []


def test_inspector_exposes_schema_failure_snapshot_and_repair_result() -> None:
    trace = ExecutionTrace()
    invalid_claim = {
        "claim_id": "invalid-stage",
        "kind": "relationship_state",
        "predicate": "relationship_stage",
        "payload": {},
    }
    with trace.measure("memory_model_attempt_1") as details:
        details.update(
            {
                "tier": "flash",
                "model": "flash-model",
                "failure_category": "schema_validation",
                "invalid_claim_snapshot": json.dumps(
                    [invalid_claim],
                    ensure_ascii=False,
                ),
                "validation_error": "missing state_value",
                "repair_attempt": "relationship_stage_bounded_repair",
                "repair_result": "unresolved",
            }
        )

    output = _model_outputs(trace.snapshot())[0]

    assert output["raw_claims"] == [invalid_claim]
    assert output["invalid_claim_snapshot"] == [invalid_claim]
    assert output["validation_error"] == "missing state_value"
    assert output["repair_attempt"] == "relationship_stage_bounded_repair"
    assert output["repair_result"] == "unresolved"


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
        payload={"preference": "sushi", "preference_type": "cuisine"},
    )


def _conflict_claim(value: str, evidence: str) -> AtomicClaim:
    return AtomicClaim(
        claim_id=f"conflict-{value}",
        kind=MemoryKind.RELATIONSHIP_STATE,
        subject="relationship",
        predicate="conflict_status",
        summary=f"Current conflict state is {value}.",
        evidence_spans=[evidence],
        confidence=0.95,
        explicitness=EvidenceExplicitness.EXPLICIT,
        payload={"state_dimension": "conflict_status", "state_value": value},
    )

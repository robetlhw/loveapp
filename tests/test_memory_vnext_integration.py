from datetime import UTC, datetime

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.application.memory import MemoryService
from loveapp.domain.memory import (
    AdmissionDecision,
    MemoryCandidate,
    MemoryGateDecision,
    MemoryGateReason,
    MemoryItem,
    MemoryKind,
    MemorySemanticGateReason,
    MemoryStatus,
    MessageRole,
    TimeKind,
)
from loveapp.domain.memory_semantic_units import EventDetailDraft, SemanticAtomicExtraction

NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


class _AlwaysGate:
    def evaluate(self, text: str) -> MemoryGateDecision:
        return MemoryGateDecision(
            should_extract=True,
            reason=MemoryGateReason.DURABLE_SIGNAL,
        )


class _DetailExtractor:
    def __init__(self, *units: EventDetailDraft) -> None:
        self._units = list(units)

    async def extract(self, text: str, **_: object) -> SemanticAtomicExtraction:
        return SemanticAtomicExtraction(
            should_extract=True,
            gate_reason=MemorySemanticGateReason.COMPOUND_MEMORY,
            semantic_units=[self._units.pop(0)],
        )


async def _event(
    store: InMemoryMemoryStore,
    *,
    source_message_id: str,
    action: str,
) -> MemoryItem:
    await store.add_message(
        user_id="user",
        relationship_id="relationship",
        conversation_id="conversation",
        role=MessageRole.USER,
        content=action,
        message_id=source_message_id,
    )
    saved = await store.save_memory(
        user_id="user",
        relationship_id="relationship",
        source_message_id=source_message_id,
        status=MemoryStatus.CONFIRMED,
        candidate=MemoryCandidate(
            kind=MemoryKind.INTERACTION_EVENT,
            subject="relationship",
            summary=action,
            original_text=action,
            evidence_spans=[action],
            time_kind=TimeKind.POINT,
            occurred_at=NOW,
            payload={"event_type": "conflict", "action": action},
        ),
    )
    return saved.item


def _detail(
    *,
    unit_id: str = "detail-1",
    event_type: str = "conflict",
    detail_type: str = "emotion",
    value: object = "happy",
    evidence: str = "she felt happy",
) -> EventDetailDraft:
    return EventDetailDraft(
        unit_id=unit_id,
        event_type_constraint=event_type,
        detail_type=detail_type,
        value=value,
        evidence_span=evidence,
    )


@pytest.mark.asyncio
async def test_memory_service_vnext_attaches_event_detail_without_core_mutation() -> None:
    store = InMemoryMemoryStore()
    target = await _event(
        store,
        source_message_id="source-1",
        action="we argued about schedules",
    )
    service = MemoryService(
        store,
        _DetailExtractor(_detail()),
        gate=_AlwaysGate(),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    result = await service.remember_text(
        user_id="user",
        relationship_id="relationship",
        conversation_id="conversation",
        text="she felt happy",
    )

    details = await store.list_event_details(
        user_id="user",
        relationship_id="relationship",
        parent_event_id=target.id,
    )
    current = await store.get_memory(target.id, "user")
    assert result.saved == []
    assert len(details) == 1
    assert details[0].parent_event_id == target.id
    assert details[0].evidence_span == "she felt happy"
    assert current is not None
    assert "emotion" not in current.payload


@pytest.mark.asyncio
async def test_memory_service_vnext_routes_core_field_through_typed_enrichment() -> None:
    store = InMemoryMemoryStore()
    target = await _event(
        store,
        source_message_id="source-1",
        action="we argued about schedules",
    )
    service = MemoryService(
        store,
        _DetailExtractor(
            _detail(
                detail_type="cause",
                value={"description": "schedules"},
                evidence="because of schedules",
            )
        ),
        gate=_AlwaysGate(),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    await service.remember_text(
        user_id="user",
        relationship_id="relationship",
        conversation_id="conversation",
        text="because of schedules",
    )

    current = await store.get_memory(target.id, "user")
    assert current is not None
    assert current.payload.get("cause") == {"description": "schedules"}
    assert await store.list_event_details(
        user_id="user",
        relationship_id="relationship",
        parent_event_id=target.id,
    ) == []
    audits = await store.list_transition_audits(
        user_id="user",
        relationship_id="relationship",
    )
    assert any(audit.rule_name == "enrich_event_cause" for audit in audits)


@pytest.mark.asyncio
async def test_memory_service_vnext_ambiguous_target_clarifies_without_writing() -> None:
    store = InMemoryMemoryStore()
    first = await _event(
        store,
        source_message_id="source-1",
        action="we argued about schedules",
    )
    second = await _event(
        store,
        source_message_id="source-2",
        action="we argued about travel",
    )
    service = MemoryService(
        store,
        _DetailExtractor(_detail()),
        gate=_AlwaysGate(),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    await service.remember_text(
        user_id="user",
        relationship_id="relationship",
        conversation_id="conversation",
        text="she felt happy",
    )

    assert await store.list_event_details(
        user_id="user",
        relationship_id="relationship",
        parent_event_id=first.id,
    ) == []
    assert await store.list_event_details(
        user_id="user",
        relationship_id="relationship",
        parent_event_id=second.id,
    ) == []
    audits = await store.list_transition_audits(
        user_id="user",
        relationship_id="relationship",
    )
    audit = next(
        (item for item in audits if item.rule_name == "vnext_event_detail_clarify"),
        None,
    )
    assert audit is not None, audits
    assert audit.decision == AdmissionDecision.REJECT
    assert set(audit.target_memory_ids) == {first.id, second.id}


@pytest.mark.asyncio
async def test_memory_service_vnext_unresolved_target_is_a_noop() -> None:
    store = InMemoryMemoryStore()
    target = await _event(
        store,
        source_message_id="source-1",
        action="we argued about schedules",
    )
    service = MemoryService(
        store,
        _DetailExtractor(
            _detail(
                event_type="date",
                detail_type="emotion",
                evidence="she felt happy",
            )
        ),
        gate=_AlwaysGate(),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    await service.remember_text(
        user_id="user",
        relationship_id="relationship",
        conversation_id="conversation",
        text="she felt happy",
    )

    audits = await store.list_transition_audits(
        user_id="user",
        relationship_id="relationship",
    )
    audit = next(
        (item for item in audits if item.rule_name == "vnext_event_detail_no_op"),
        None,
    )
    assert audit is not None, audits
    assert audit.decision == AdmissionDecision.REJECT
    assert audit.target_memory_ids == [target.id]

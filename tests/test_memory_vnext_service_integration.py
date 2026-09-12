from datetime import UTC, datetime

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.application.memory import MemoryService
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.advice import RelationshipContext
from loveapp.domain.memory import (
    AtomicExtraction,
    MemoryCandidate,
    MemoryGateDecision,
    MemoryGateReason,
    MemoryKind,
    MemorySemanticGateReason,
    MemoryStatus,
    MessageRole,
    TimeKind,
)
from loveapp.domain.memory_semantic_units import (
    AttributeNamespace,
    EnrichmentDraft,
    EventDetailDraft,
    SemanticAtomicExtraction,
)

NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


class _AlwaysGate:
    def evaluate(self, text: str) -> MemoryGateDecision:
        return MemoryGateDecision(
            should_extract=True,
            reason=MemoryGateReason.DURABLE_SIGNAL,
        )


class _StaticExtractor:
    def __init__(self, extraction: AtomicExtraction) -> None:
        self.extraction = extraction

    async def extract(self, text: str, **_: object) -> AtomicExtraction:
        return self.extraction


def _event_candidate(
    *,
    event_type: str = "shared_activity",
    source_message_id: str = "source-1",
    summary: str = "We went out together",
) -> MemoryCandidate:
    return MemoryCandidate(
        kind=MemoryKind.INTERACTION_EVENT,
        subject="relationship",
        summary=summary,
        original_text=summary,
        evidence_spans=[summary],
        time_kind=TimeKind.POINT,
        occurred_at=NOW,
        payload={"event_type": event_type},
    )


async def _seed_event(store: InMemoryMemoryStore):
    await store.save_relationship_context(
        RelationshipContext(user_id="user-1", relationship_id="relationship-1")
    )
    await store.add_message(
        user_id="user-1",
        relationship_id="relationship-1",
        conversation_id="conversation-1",
        role=MessageRole.USER,
        content="We went out together",
        message_id="source-1",
    )
    result = await store.save_memory(
        user_id="user-1",
        relationship_id="relationship-1",
        candidate=_event_candidate(),
        source_message_id="source-1",
        status=MemoryStatus.CONFIRMED,
    )
    return result.item


def _detail_extraction(
    *,
    detail_type: str = "emotion",
    value: object = "happy",
    event_type: str = "shared_activity",
    evidence: str = "she was happy",
) -> SemanticAtomicExtraction:
    return SemanticAtomicExtraction(
        should_extract=True,
        gate_reason=MemorySemanticGateReason.COMPOUND_MEMORY,
        semantic_units=[
            EventDetailDraft(
                unit_id="detail-1",
                event_type_constraint=event_type,
                detail_type=detail_type,
                value=value,
                evidence_span=evidence,
            )
        ],
    )


def _service(store: InMemoryMemoryStore, extraction: AtomicExtraction) -> MemoryService:
    return MemoryService(
        store,
        _StaticExtractor(extraction),
        gate=_AlwaysGate(),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_event_detail_draft_uses_vnext_resolution_and_persists_detail() -> None:
    store = InMemoryMemoryStore()
    event = await _seed_event(store)
    trace = ExecutionTrace()
    service = _service(store, _detail_extraction())

    result = await service.remember_text(
        user_id="user-1",
        relationship_id="relationship-1",
        conversation_id="conversation-1",
        text="she was happy",
        trace=trace,
    )

    details = await store.list_event_details(
        user_id="user-1",
        relationship_id="relationship-1",
        parent_event_id=event.id,
    )
    assert result.saved == []
    assert len(details) == 1
    assert details[0].detail_type == "emotion"
    assert details[0].value == "happy"
    assert details[0].parent_event_id == event.id
    assert {record.name for record in trace.records} >= {
        "memory_event_detail_semantic",
        "memory_candidate_generation",
        "memory_target_resolution",
        "memory_write_policy",
        "memory_event_detail_store",
    }


@pytest.mark.asyncio
async def test_event_detail_core_field_reuses_typed_event_enrichment_path() -> None:
    store = InMemoryMemoryStore()
    event = await _seed_event(store)
    service = _service(
        store,
        _detail_extraction(
            detail_type="outcome",
            value="closer",
            evidence="we became closer",
        ),
    )

    await service.remember_text(
        user_id="user-1",
        relationship_id="relationship-1",
        conversation_id="conversation-1",
        text="we became closer",
    )

    updated = await store.get_memory(event.id, "user-1")
    assert updated is not None
    assert updated.payload["outcome"] == "closer"
    assert await store.list_event_details(
        user_id="user-1",
        relationship_id="relationship-1",
        parent_event_id=event.id,
    ) == []


@pytest.mark.asyncio
async def test_ambiguous_event_detail_is_audit_only_and_does_not_attach() -> None:
    store = InMemoryMemoryStore()
    first = await _seed_event(store)
    await store.add_message(
        user_id="user-1",
        relationship_id="relationship-1",
        conversation_id="conversation-1",
        role=MessageRole.USER,
        content="We went out again",
        message_id="source-2",
    )
    second = await store.save_memory(
        user_id="user-1",
        relationship_id="relationship-1",
        candidate=_event_candidate(
            source_message_id="source-2",
            summary="We went out again",
        ),
        source_message_id="source-2",
        status=MemoryStatus.CONFIRMED,
    )
    service = _service(store, _detail_extraction())

    await service.remember_text(
        user_id="user-1",
        relationship_id="relationship-1",
        conversation_id="conversation-1",
        text="she was happy",
    )

    all_details = []
    for event_id in (first.id, second.item.id):
        all_details.extend(
            await store.list_event_details(
                user_id="user-1",
                relationship_id="relationship-1",
                parent_event_id=event_id,
            )
        )
    assert all_details == []
    audits = await store.list_transition_audits(
        user_id="user-1",
        relationship_id="relationship-1",
    )
    assert any(audit.rule_name == "vnext_event_detail_clarify" for audit in audits)


@pytest.mark.asyncio
async def test_legacy_enrichment_draft_still_uses_existing_path() -> None:
    store = InMemoryMemoryStore()
    event = await _seed_event(store)
    extraction = SemanticAtomicExtraction(
        should_extract=True,
        gate_reason=MemorySemanticGateReason.COMPOUND_MEMORY,
        semantic_units=[
            EnrichmentDraft(
                unit_id="legacy-1",
                target_kind=MemoryKind.INTERACTION_EVENT,
                target_semantic_hint={"event_type": "shared_activity"},
                attribute_namespace=AttributeNamespace.CANONICAL,
                attribute_name="outcome",
                value="closer",
                evidence_span="we became closer",
                confidence=0.95,
            )
        ],
    )
    service = _service(store, extraction)

    await service.remember_text(
        user_id="user-1",
        relationship_id="relationship-1",
        conversation_id="conversation-1",
        text="we became closer",
    )

    updated = await store.get_memory(event.id, "user-1")
    assert updated is not None
    assert updated.payload["outcome"] == "closer"

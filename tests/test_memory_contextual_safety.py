import json
from datetime import UTC, datetime

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.application import MemoryService
from loveapp.application.memory import NoOpMemoryExtractor
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.memory import (
    AtomicExtraction,
    MemoryCandidate,
    MemoryKind,
    MemoryStatus,
    MessageRole,
)

NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


class _RecordingNoOpExtractor:
    def __init__(self) -> None:
        self.called = False

    async def extract(self, *args, **kwargs) -> AtomicExtraction:
        del args, kwargs
        self.called = True
        return AtomicExtraction()


async def _service_with_source(text: str):
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(store, NoOpMemoryExtractor(), clock=lambda: NOW)
    scope = {
        "user_id": "contextual-safety-user",
        "relationship_id": "contextual-safety-relationship",
        "conversation_id": "contextual-safety-conversation",
    }
    source = await service.record_message(role=MessageRole.USER, content=text, **scope)
    return store, service, scope, source


async def _save_pattern(
    store: InMemoryMemoryStore,
    scope: dict[str, str],
    source_id: str,
    *,
    canonical: str,
    summary: str,
    evidence: str,
    kind: MemoryKind = MemoryKind.INTERACTION_PATTERN,
    status: MemoryStatus = MemoryStatus.CONFIRMED,
):
    return await store.save_memory(
        user_id=scope["user_id"],
        relationship_id=scope["relationship_id"],
        source_message_id=source_id,
        status=status,
        candidate=MemoryCandidate(
            kind=kind,
            subject="relationship",
            summary=summary,
            original_text=summary,
            evidence_spans=[evidence],
            canonical_predicate=canonical,
            raw_predicate=canonical,
            confidence=0.95,
            payload={"predicate": canonical},
        ),
    )


@pytest.mark.asyncio
async def test_response_engagement_duration_is_a_safe_single_target_patch() -> None:
    store, service, scope, source = await _service_with_source("她最近回复越来越慢。")
    response = await _save_pattern(
        store,
        scope,
        source.id,
        canonical="interaction.response_engagement",
        summary="她最近回复越来越慢",
        evidence="她最近回复越来越慢",
    )
    current = await service.record_message(
        role=MessageRole.USER,
        content="这种情况已经一个月了。",
        **scope,
    )
    trace = ExecutionTrace()

    result = await service.remember_recorded_message(
        message=current,
        text=current.content,
        trace=trace,
    )

    updated = await store.get_memory(response.item.id, scope["user_id"])
    assert result.gate_decision is not None
    assert result.gate_decision.reason.value == "contextual_update"
    assert result.gate_decision.selected_target_memory_id == response.item.id
    assert result.contextual_updated_memory_ids == [response.item.id]
    assert result.saved == []
    assert updated is not None
    assert updated.payload["duration_value"] == 1
    assert updated.payload["duration_unit"] == "month"
    contextual_trace = next(
        record for record in trace.snapshot() if record.name == "memory_contextual_update"
    )
    assert contextual_trace.details["semantic_candidate_ids_json"] == f'["{response.item.id}"]'
    assert contextual_trace.details["compatible_candidate_ids_json"] == f'["{response.item.id}"]'


@pytest.mark.asyncio
async def test_contact_frequency_duration_path_remains_compatible() -> None:
    store, service, scope, source = await _service_with_source("她最近联系越来越少。")
    contact = await _save_pattern(
        store,
        scope,
        source.id,
        canonical="interaction.contact_frequency",
        summary="她最近联系越来越少",
        evidence="她最近联系越来越少",
    )
    current = await service.record_message(
        role=MessageRole.USER,
        content="这种情况已经持续一个月了。",
        **scope,
    )

    result = await service.remember_recorded_message(message=current, text=current.content)

    assert result.gate_decision is not None
    assert result.gate_decision.reason.value == "contextual_update"
    assert result.contextual_updated_memory_ids == [contact.item.id]


@pytest.mark.asyncio
async def test_singular_reference_is_ambiguous_before_compatibility_filtering() -> None:
    store, service, scope, source = await _service_with_source(
        "她最近回复越来越慢，而且见面次数也少了。"
    )
    response = await _save_pattern(
        store,
        scope,
        source.id,
        canonical="interaction.response_engagement",
        summary="她最近回复越来越慢",
        evidence="她最近回复越来越慢",
    )
    meeting = await _save_pattern(
        store,
        scope,
        source.id,
        canonical="interaction.contact_frequency",
        summary="见面次数也少了",
        evidence="见面次数也少了",
    )
    current = await service.record_message(
        role=MessageRole.USER,
        content="这种情况已经持续一个月了。",
        **scope,
    )
    trace = ExecutionTrace()

    result = await service.remember_recorded_message(
        message=current,
        text=current.content,
        trace=trace,
    )

    assert result.gate_decision is not None
    assert result.gate_decision.should_extract is False
    assert result.contextual_updated_memory_ids == []
    response_after = await store.get_memory(response.item.id, scope["user_id"])
    assert response_after is not None
    assert response_after.payload.get("duration_value") is None
    meeting_after = await store.get_memory(meeting.item.id, scope["user_id"])
    assert meeting_after is not None
    assert meeting_after.payload.get("duration_value") is None
    contextual_trace = next(
        record for record in trace.snapshot() if record.name == "memory_contextual_update"
    )
    assert contextual_trace.details["reason"] == "ambiguous_semantic_antecedent"
    assert set(json.loads(contextual_trace.details["semantic_candidate_ids_json"])) == {
        response.item.id,
        meeting.item.id,
    }


@pytest.mark.asyncio
async def test_incompatible_semantic_candidate_cannot_create_false_unique_target() -> None:
    store, service, scope, source = await _service_with_source("她最近回复越来越慢。")
    response = await _save_pattern(
        store,
        scope,
        source.id,
        canonical="interaction.response_engagement",
        summary="她最近回复越来越慢",
        evidence="她最近回复越来越慢",
    )
    incompatible = await _save_pattern(
        store,
        scope,
        source.id,
        canonical="interaction.channel",
        summary="她最近线上聊天比较冷淡",
        evidence="她最近回复越来越慢",
    )
    current = await service.record_message(
        role=MessageRole.USER,
        content="这种情况已经一个月了。",
        **scope,
    )
    trace = ExecutionTrace()

    result = await service.remember_recorded_message(
        message=current,
        text=current.content,
        trace=trace,
    )

    assert result.contextual_updated_memory_ids == []
    assert result.gate_decision is not None
    assert result.gate_decision.should_extract is False
    contextual_trace = next(
        record for record in trace.snapshot() if record.name == "memory_contextual_update"
    )
    assert contextual_trace.details["reason"] == "ambiguous_semantic_antecedent"
    rejected = json.loads(contextual_trace.details["rejected_candidates_json"])
    assert {item["memory_id"] for item in rejected} == {incompatible.item.id}
    response_after = await store.get_memory(response.item.id, scope["user_id"])
    incompatible_after = await store.get_memory(incompatible.item.id, scope["user_id"])
    assert response_after is not None
    assert incompatible_after is not None
    assert response_after.payload.get("duration_value") is None
    assert incompatible_after.payload.get("duration_value") is None


@pytest.mark.asyncio
async def test_explicit_subject_switch_does_not_patch_partner_memory() -> None:
    store, service, scope, source = await _service_with_source("她最近回复越来越慢。")
    response = await _save_pattern(
        store,
        scope,
        source.id,
        canonical="interaction.response_engagement",
        summary="她最近回复越来越慢",
        evidence="她最近回复越来越慢",
    )
    current = await service.record_message(
        role=MessageRole.USER,
        content="我最近工作状态也是这样，已经一个月了。",
        **scope,
    )
    trace = ExecutionTrace()

    result = await service.remember_recorded_message(
        message=current,
        text=current.content,
        trace=trace,
    )

    assert result.contextual_updated_memory_ids == []
    unchanged = await store.get_memory(response.item.id, scope["user_id"])
    assert unchanged is not None
    assert unchanged.payload.get("duration_value") is None
    contextual_trace = next(
        record for record in trace.snapshot() if record.name == "memory_contextual_update"
    )
    assert contextual_trace.details["reason"] == "explicit_subject_mismatch"


@pytest.mark.asyncio
async def test_plural_reference_fails_closed_without_multi_target_patch() -> None:
    store, service, scope, source = await _service_with_source(
        "她最近回复越来越慢，而且见面次数也少了。"
    )
    response = await _save_pattern(
        store,
        scope,
        source.id,
        canonical="interaction.response_engagement",
        summary="她最近回复越来越慢",
        evidence="她最近回复越来越慢",
    )
    meeting = await _save_pattern(
        store,
        scope,
        source.id,
        canonical="interaction.contact_frequency",
        summary="见面次数也少了",
        evidence="见面次数也少了",
    )
    current = await service.record_message(
        role=MessageRole.USER,
        content="这两种情况都已经持续一个月了。",
        **scope,
    )
    trace = ExecutionTrace()

    result = await service.remember_recorded_message(
        message=current,
        text=current.content,
        trace=trace,
    )

    assert result.contextual_updated_memory_ids == []
    response_after = await store.get_memory(response.item.id, scope["user_id"])
    assert response_after is not None
    assert response_after.payload.get("duration_value") is None
    meeting_after = await store.get_memory(meeting.item.id, scope["user_id"])
    assert meeting_after is not None
    assert meeting_after.payload.get("duration_value") is None
    contextual_trace = next(
        record for record in trace.snapshot() if record.name == "memory_contextual_update"
    )
    assert contextual_trace.details["reason"] == "unsupported_multi_target"
    assert contextual_trace.details["plural_reference"] is True


@pytest.mark.asyncio
async def test_missing_latest_antecedent_memory_does_not_fall_back_to_stale_target() -> None:
    store, service, scope, old_source = await _service_with_source("她最近联系越来越少。")
    stale_contact = await _save_pattern(
        store,
        scope,
        old_source.id,
        canonical="interaction.contact_frequency",
        summary="她最近联系越来越少",
        evidence="她最近联系越来越少",
    )
    await service.record_message(
        role=MessageRole.USER,
        content="她最近回复越来越慢。",
        **scope,
    )
    current = await service.record_message(
        role=MessageRole.USER,
        content="这种情况已经一个月了。",
        **scope,
    )
    trace = ExecutionTrace()

    result = await service.remember_recorded_message(
        message=current,
        text=current.content,
        trace=trace,
    )

    assert result.contextual_updated_memory_ids == []
    stale_after = await store.get_memory(stale_contact.item.id, scope["user_id"])
    assert stale_after is not None
    assert stale_after.payload.get("duration_value") is None
    contextual_trace = next(
        record for record in trace.snapshot() if record.name == "memory_contextual_update"
    )
    assert contextual_trace.details["reason"] == "no_semantic_antecedent"
    assert contextual_trace.details["semantic_candidate_ids_json"] == "[]"


@pytest.mark.asyncio
async def test_deduped_older_source_still_counts_before_unique_target_resolution() -> None:
    store, service, scope, old_source = await _service_with_source("见面次数也少了。")
    old_meeting = await _save_pattern(
        store,
        scope,
        old_source.id,
        canonical="interaction.contact_frequency",
        summary="见面次数也少了",
        evidence="见面次数也少了",
    )
    combined_source = await service.record_message(
        role=MessageRole.USER,
        content="她最近回复越来越慢，而且见面次数也少了。",
        **scope,
    )
    response = await _save_pattern(
        store,
        scope,
        combined_source.id,
        canonical="interaction.response_engagement",
        summary="她最近回复越来越慢",
        evidence="她最近回复越来越慢",
    )
    merged_meeting = await _save_pattern(
        store,
        scope,
        combined_source.id,
        canonical="interaction.contact_frequency",
        summary="见面次数也少了",
        evidence="见面次数也少了",
    )
    assert merged_meeting.item.id == old_meeting.item.id
    assert merged_meeting.item.source_message_id == old_source.id
    current = await service.record_message(
        role=MessageRole.USER,
        content="这种情况已经一个月了。",
        **scope,
    )
    trace = ExecutionTrace()

    result = await service.remember_recorded_message(
        message=current,
        text=current.content,
        trace=trace,
    )

    assert result.contextual_updated_memory_ids == []
    response_after = await store.get_memory(response.item.id, scope["user_id"])
    meeting_after = await store.get_memory(old_meeting.item.id, scope["user_id"])
    assert response_after is not None
    assert meeting_after is not None
    assert response_after.payload.get("duration_value") is None
    assert meeting_after.payload.get("duration_value") is None
    contextual_trace = next(
        record for record in trace.snapshot() if record.name == "memory_contextual_update"
    )
    assert contextual_trace.details["reason"] == "ambiguous_semantic_antecedent"
    assert set(json.loads(contextual_trace.details["semantic_candidate_ids_json"])) == {
        response.item.id,
        old_meeting.item.id,
    }


@pytest.mark.asyncio
async def test_ambiguous_qualifier_does_not_suppress_separate_durable_fact() -> None:
    store, _, scope, source = await _service_with_source(
        "她最近回复越来越慢，而且见面次数也少了。"
    )
    response = await _save_pattern(
        store,
        scope,
        source.id,
        canonical="interaction.response_engagement",
        summary="她最近回复越来越慢",
        evidence="她最近回复越来越慢",
    )
    meeting = await _save_pattern(
        store,
        scope,
        source.id,
        canonical="interaction.contact_frequency",
        summary="见面次数也少了",
        evidence="见面次数也少了",
    )
    extractor = _RecordingNoOpExtractor()
    service = MemoryService(store, extractor, clock=lambda: NOW)
    current = await service.record_message(
        role=MessageRole.USER,
        content="这种情况已经一个月了，而且昨天我们分手了。",
        **scope,
    )
    trace = ExecutionTrace()

    result = await service.remember_recorded_message(
        message=current,
        text=current.content,
        trace=trace,
    )

    assert result.gate_decision is not None
    assert result.gate_decision.should_extract is True
    assert result.gate_decision.reason.value == "durable_signal"
    assert extractor.called is True
    assert result.contextual_updated_memory_ids == []
    response_after = await store.get_memory(response.item.id, scope["user_id"])
    meeting_after = await store.get_memory(meeting.item.id, scope["user_id"])
    assert response_after is not None
    assert meeting_after is not None
    assert response_after.payload.get("duration_value") is None
    assert meeting_after.payload.get("duration_value") is None
    contextual_trace = next(
        record for record in trace.snapshot() if record.name == "memory_contextual_update"
    )
    assert contextual_trace.details["reason"] == "ambiguous_semantic_antecedent"

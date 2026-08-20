import json
from datetime import UTC, datetime

from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.application import MemoryService
from loveapp.application.memory import NoOpMemoryExtractor
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.memory import (
    ClaimRelation,
    MemoryCandidate,
    MemoryKind,
    MemoryStatus,
    MessageRole,
    TimeKind,
)

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
SCOPE = {
    "user_id": "correction-user",
    "relationship_id": "correction-relationship",
    "conversation_id": "correction-conversation",
}


async def _service_with_source(text: str):
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(store, NoOpMemoryExtractor(), clock=lambda: NOW)
    source = await service.record_message(role=MessageRole.USER, content=text, **SCOPE)
    return store, service, source


async def _save_claim(
    store: InMemoryMemoryStore,
    source_id: str,
    *,
    canonical: str,
    summary: str,
    evidence: str,
    kind: MemoryKind = MemoryKind.INTERACTION_PATTERN,
    state_value: str | None = None,
    payload: dict[str, object] | None = None,
    status: MemoryStatus = MemoryStatus.CONFIRMED,
):
    data = {"predicate": canonical, "metric": canonical.rsplit(".", 1)[-1]}
    if payload:
        data.update(payload)
    if state_value is not None:
        data["state_value"] = state_value
        data["state_dimension"] = canonical
    return await store.save_memory(
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
        source_message_id=source_id,
        status=status,
        candidate=MemoryCandidate(
            kind=kind,
            subject="relationship",
            summary=summary,
            original_text=summary,
            evidence_spans=[evidence],
            time_kind=TimeKind.TIMELESS,
            confidence=0.95,
            raw_predicate=canonical,
            canonical_predicate=canonical,
            state_dimension=canonical if state_value is not None else None,
            state_value=state_value,
            payload=data,
        ),
    )


async def test_quantified_frequency_correction_creates_superseding_current_claim() -> None:
    old_text = "她现在一天只回我一两次。"
    store, service, source = await _service_with_source(old_text)
    old = await _save_claim(
        store,
        source.id,
        canonical="interaction.response_engagement",
        summary="她现在一天只回我一两次",
        evidence=old_text,
        payload={"metric": "response_engagement", "current": "一两次"},
    )
    current = await service.record_message(
        role=MessageRole.USER,
        content="我说少了，其实大概三四次。",
        **SCOPE,
    )
    trace = ExecutionTrace()

    result = await service.remember_recorded_message(
        message=current,
        text=current.content,
        status=MemoryStatus.CONFIRMED,
        trace=trace,
    )

    old_after = await store.get_memory(old.item.id, SCOPE["user_id"])
    memories = await store.list_memories(
        user_id=SCOPE["user_id"], relationship_id=SCOPE["relationship_id"]
    )
    corrected = next(item for item in memories if item.id != old.item.id)
    assert result.contextual_updated_memory_ids == []
    assert old_after is not None and old_after.status == MemoryStatus.SUPERSEDED
    assert corrected.status == MemoryStatus.CONFIRMED
    assert corrected.supersedes_id == old.item.id
    assert corrected.claim_relation == ClaimRelation.UPDATE
    assert corrected.payload["contextual_update_type"] == "correction"
    assert corrected.payload["correction_type"] == "frequency"
    assert corrected.payload["correction_value"] == "三四次"
    audits = await store.list_transition_audits(
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
        source_message_id=current.id,
    )
    correction_audit = next(
        audit for audit in audits if audit.rule_name == "explicit_memory_correction"
    )
    assert correction_audit.target_memory_ids == [old.item.id]
    assert correction_audit.source_message_id == current.id
    correction_trace = next(
        record for record in trace.snapshot() if record.name == "memory_explicit_correction"
    )
    assert correction_trace.details["selected_target_memory_id"] == old.item.id


async def test_duration_correction_reuses_existing_relationship_state_schema() -> None:
    old_text = "我们已经冷战一个月了。"
    store, service, source = await _service_with_source(old_text)
    old = await _save_claim(
        store,
        source.id,
        canonical="relationship.conflict_status",
        summary="我们已经冷战一个月",
        evidence=old_text,
        kind=MemoryKind.RELATIONSHIP_STATE,
        state_value="active",
        payload={"state_dimension": "relationship.conflict_status", "state_value": "active"},
    )
    current = await service.record_message(
        role=MessageRole.USER,
        content="不对，我刚才说错了，其实只有两周。",
        **SCOPE,
    )

    result = await service.remember_recorded_message(
        message=current,
        text=current.content,
        status=MemoryStatus.CONFIRMED,
    )

    old_after = await store.get_memory(old.item.id, SCOPE["user_id"])
    memories = await store.list_memories(
        user_id=SCOPE["user_id"], relationship_id=SCOPE["relationship_id"]
    )
    corrected = next(item for item in memories if item.id != old.item.id)
    assert result.saved and result.saved[0].item.id == corrected.id
    assert old_after is not None and old_after.status == MemoryStatus.SUPERSEDED
    assert corrected.supersedes_id == old.item.id
    assert corrected.payload["duration_value"] == 2
    assert corrected.payload["duration_unit"] == "week"


async def test_ordinary_temporal_change_is_not_treated_as_correction() -> None:
    old_text = "她每天回复我两次。"
    store, service, source = await _service_with_source(old_text)
    old = await _save_claim(
        store,
        source.id,
        canonical="interaction.response_engagement",
        summary=old_text.rstrip("。"),
        evidence=old_text,
        payload={"metric": "response_engagement", "current": "两次"},
    )
    current = await service.record_message(
        role=MessageRole.USER,
        content="以前每天两次，现在变成每天五六次了。",
        **SCOPE,
    )
    trace = ExecutionTrace()

    result = await service.remember_recorded_message(
        message=current,
        text=current.content,
        trace=trace,
        status=MemoryStatus.CONFIRMED,
    )

    old_after = await store.get_memory(old.item.id, SCOPE["user_id"])
    assert old_after is not None and old_after.status == MemoryStatus.CONFIRMED
    assert result.contextual_updated_memory_ids == []
    assert not any(record.name == "memory_explicit_correction" for record in trace.snapshot())


async def test_ambiguous_correction_fails_closed_before_compatibility() -> None:
    old_text = "她最近回复越来越慢，而且见面次数也少了。"
    store, service, source = await _service_with_source(old_text)
    response = await _save_claim(
        store,
        source.id,
        canonical="interaction.response_engagement",
        summary="她最近回复越来越慢",
        evidence="她最近回复越来越慢",
        payload={"metric": "response_engagement", "current": "slow"},
    )
    meeting = await _save_claim(
        store,
        source.id,
        canonical="interaction.contact_frequency",
        summary="见面次数也少了",
        evidence="见面次数也少了",
        payload={"metric": "contact_frequency", "current": "low"},
    )
    current = await service.record_message(
        role=MessageRole.USER,
        content="我说少了，其实大概三四次。",
        **SCOPE,
    )
    trace = ExecutionTrace()

    result = await service.remember_recorded_message(
        message=current,
        text=current.content,
        trace=trace,
        status=MemoryStatus.CONFIRMED,
    )

    assert result.saved == []
    assert result.contextual_updated_memory_ids == []
    response_after = await store.get_memory(response.item.id, SCOPE["user_id"])
    meeting_after = await store.get_memory(meeting.item.id, SCOPE["user_id"])
    assert response_after is not None and response_after.status == MemoryStatus.CONFIRMED
    assert meeting_after is not None and meeting_after.status == MemoryStatus.CONFIRMED
    correction_trace = next(
        record for record in trace.snapshot() if record.name == "memory_explicit_correction"
    )
    assert correction_trace.details["resolution_reason"] == "ambiguous_correction_antecedent"
    assert len(json.loads(correction_trace.details["semantic_candidate_ids_json"])) == 2


async def test_subject_mismatch_correction_is_a_no_op() -> None:
    old_text = "她现在一天只回我一两次。"
    store, service, source = await _service_with_source(old_text)
    old = await _save_claim(
        store,
        source.id,
        canonical="interaction.response_engagement",
        summary=old_text.rstrip("。"),
        evidence=old_text,
        payload={"metric": "response_engagement", "current": "一两次"},
    )
    current = await service.record_message(
        role=MessageRole.USER,
        content="我最近工作状态也是这样，我说少了，其实三四次。",
        **SCOPE,
    )
    trace = ExecutionTrace()

    result = await service.remember_recorded_message(
        message=current,
        text=current.content,
        trace=trace,
        status=MemoryStatus.CONFIRMED,
    )

    old_after = await store.get_memory(old.item.id, SCOPE["user_id"])
    assert result.saved == []
    assert old_after is not None and old_after.status == MemoryStatus.CONFIRMED
    correction_trace = next(
        record for record in trace.snapshot() if record.name == "memory_explicit_correction"
    )
    assert correction_trace.details["resolution_reason"] == "explicit_subject_mismatch"


async def test_proposed_correction_cannot_close_confirmed_claim() -> None:
    old_text = "她现在一天只回我一两次。"
    store, service, source = await _service_with_source(old_text)
    old = await _save_claim(
        store,
        source.id,
        canonical="interaction.response_engagement",
        summary=old_text.rstrip("。"),
        evidence=old_text,
        payload={"metric": "response_engagement", "current": "一两次"},
    )
    current = await service.record_message(
        role=MessageRole.USER,
        content="我说少了，其实大概三四次。",
        **SCOPE,
    )

    result = await service.remember_recorded_message(
        message=current,
        text=current.content,
        status=MemoryStatus.PROPOSED,
    )

    old_after = await store.get_memory(old.item.id, SCOPE["user_id"])
    assert old_after is not None and old_after.status == MemoryStatus.CONFIRMED
    assert result.saved and result.saved[0].item.status == MemoryStatus.PROPOSED
    assert result.saved[0].item.claim_relation == ClaimRelation.CONTRADICTION
    assert result.saved[0].item.supersedes_id is None


async def test_correction_replay_is_idempotent_and_old_claim_leaves_context() -> None:
    old_text = "她现在一天只回我一两次。"
    store, service, source = await _service_with_source(old_text)
    old = await _save_claim(
        store,
        source.id,
        canonical="interaction.response_engagement",
        summary=old_text.rstrip("。"),
        evidence=old_text,
        payload={"metric": "response_engagement", "current": "一两次"},
    )
    current = await service.record_message(
        role=MessageRole.USER,
        content="我说少了，其实大概三四次。",
        **SCOPE,
    )

    first = await service.remember_recorded_message(
        message=current,
        text=current.content,
        status=MemoryStatus.CONFIRMED,
    )
    second = await service.remember_recorded_message(
        message=current,
        text=current.content,
        status=MemoryStatus.CONFIRMED,
    )

    memories = await store.list_memories(
        user_id=SCOPE["user_id"], relationship_id=SCOPE["relationship_id"]
    )
    context = await service.get_context(
        SCOPE["user_id"], SCOPE["relationship_id"], query="回复频率"
    )
    assert first.saved and second.saved == []
    assert len(memories) == 2
    assert old.item.id not in {item.id for item in context.remembered_items}
    assert any(item.id != old.item.id for item in context.remembered_items)

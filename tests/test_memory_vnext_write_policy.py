from datetime import UTC, datetime
from pathlib import Path

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore, SQLiteMemoryStore
from loveapp.application.memory_write_policy import decide_event_detail_write
from loveapp.domain.memory import (
    MemoryCandidate,
    MemoryKind,
    MemoryStatus,
    MessageRole,
    TimeKind,
)
from loveapp.domain.memory_architecture_vnext import (
    EventDetail,
    EventDetailDraft,
    ResolutionStatus,
    TargetResolution,
    WriteOperation,
)
from loveapp.ports.memory import MemoryStore

NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


def _draft(detail_type: str = "emotion") -> EventDetailDraft:
    return EventDetailDraft(
        unit_id="d1",
        event_type_constraint="shared_activity",
        detail_type=detail_type,
        value="happy",
        evidence_span="she was happy",
    )


def _resolution(status: ResolutionStatus, target: str | None = "event-1") -> TargetResolution:
    kwargs = {
        "status": status,
        "target_memory_id": target if status == ResolutionStatus.RESOLVED else None,
        "candidate_ids": [target] if target else [],
        "reason": "test",
    }
    if status == ResolutionStatus.AMBIGUOUS:
        from loveapp.domain.memory_architecture_vnext import ClarificationRequired

        kwargs["clarification"] = ClarificationRequired(
            reason="two targets", question="Which event?", candidate_memory_ids=["a", "b"]
        )
    return TargetResolution(**kwargs)


def test_write_policy_clarifies_ambiguity_and_noops_unresolved() -> None:
    ambiguous = decide_event_detail_write(_draft(), _resolution(ResolutionStatus.AMBIGUOUS))
    unresolved = decide_event_detail_write(_draft(), _resolution(ResolutionStatus.UNRESOLVED, None))
    assert ambiguous.operation == WriteOperation.CLARIFY
    assert unresolved.operation == WriteOperation.NOOP


def test_write_policy_prefers_attach_for_soft_event_detail() -> None:
    detail = EventDetail(
        id="detail-1",
        parent_event_id="event-1",
        detail_type="emotion",
        value="happy",
        evidence_span="she was happy",
        source_message_id="message-2",
        created_at=NOW,
    )
    decision = decide_event_detail_write(
        _draft(), _resolution(ResolutionStatus.RESOLVED), detail=detail
    )
    assert decision.operation == WriteOperation.ATTACH_DETAIL
    assert decision.target_memory_id == "event-1"


def test_write_policy_requires_detail_before_attach() -> None:
    decision = decide_event_detail_write(_draft(), _resolution(ResolutionStatus.RESOLVED))
    assert decision.operation == WriteOperation.NOOP
    assert "required" in decision.reason


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
async def test_event_detail_store_is_scoped_and_not_a_core_memory(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store: MemoryStore
    if store_kind == "memory":
        store = InMemoryMemoryStore()
    else:
        store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.add_message(
        user_id="user",
        relationship_id="relationship",
        role=MessageRole.USER,
        content="We went out",
        message_id="message-1",
        conversation_id="conversation-1",
    )
    saved = await store.save_memory(
        user_id="user",
        relationship_id="relationship",
        source_message_id="message-1",
        status=MemoryStatus.CONFIRMED,
        candidate=MemoryCandidate(
            kind=MemoryKind.INTERACTION_EVENT,
            subject="relationship",
            summary="We went out",
            original_text="We went out",
            evidence_spans=["We went out"],
            time_kind=TimeKind.POINT,
            occurred_at=NOW,
            payload={"event_type": "shared_activity"},
        ),
    )
    detail = EventDetail(
        id="detail-1",
        parent_event_id=saved.item.id,
        detail_type="emotion",
        value="happy",
        evidence_span="she was happy",
        source_message_id="message-1",
        created_at=NOW,
    )
    created = await store.create_event_detail(
        user_id="user", relationship_id="relationship", detail=detail
    )
    assert created == detail
    listed = await store.list_event_details(
        user_id="user", relationship_id="relationship", parent_event_id=saved.item.id
    )
    assert listed == [detail]
    memories = await store.list_memories(
        user_id="user", relationship_id="relationship", kind=MemoryKind.INTERACTION_EVENT
    )
    assert [item.id for item in memories] == [saved.item.id]
    await store.aclose()

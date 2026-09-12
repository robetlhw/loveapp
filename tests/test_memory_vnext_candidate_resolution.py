from datetime import UTC, datetime

from loveapp.application.memory_target_resolution import CandidateGenerator, TargetResolver
from loveapp.domain.memory import MemoryItem, MemoryKind, MemoryStatus, TimeKind
from loveapp.domain.memory_architecture_vnext import (
    CandidateSource,
    EventDetailDraft,
    ResolutionStatus,
)
from loveapp.domain.runtime_context import PendingQuestion

NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


def _event(
    memory_id: str,
    *,
    source: str = "message-1",
    event_type: str = "conflict",
) -> MemoryItem:
    return MemoryItem(
        id=memory_id,
        user_id="user",
        relationship_id="relationship",
        dedupe_key=f"dedupe:{memory_id}",
        kind=MemoryKind.INTERACTION_EVENT,
        subject="relationship",
        summary="A relationship event",
        original_text="We argued",
        evidence_spans=["We argued"],
        time_kind=TimeKind.POINT,
        occurred_at=NOW,
        source_message_id=source,
        status=MemoryStatus.CONFIRMED,
        payload={"event_type": event_type},
    )


def _detail(*, event_type: str = "conflict") -> EventDetailDraft:
    return EventDetailDraft(
        unit_id="detail-1",
        event_type_constraint=event_type,
        detail_type="cause",
        value="money",
        evidence_span="because of money",
    )


def test_candidate_generator_keeps_multiple_semantic_candidates_before_compatibility() -> None:
    first = _event("event-1")
    second = _event("event-2")
    generated = CandidateGenerator().generate(
        draft=_detail(),
        current_text="because of money",
        existing_memories=[first, second],
        conversation_history=[],
        retrieved_candidates=[first],
        pending_question=PendingQuestion(
            question_id="q1",
            target_kind=MemoryKind.INTERACTION_EVENT.value,
            expected_field="cause",
        ),
    )
    assert {candidate.memory_id for candidate in generated.candidates} == {
        "event-1",
        "event-2",
    }
    assert CandidateSource.STRUCTURED_LOOKUP in generated.channels_used
    assert CandidateSource.VECTOR_FALLBACK in generated.channels_used

    resolution = TargetResolver().resolve(draft=_detail(), generated=generated)
    assert resolution.status == ResolutionStatus.AMBIGUOUS
    assert resolution.target_memory_id is None


def test_single_vector_hit_without_complete_semantic_scope_does_not_select_target() -> None:
    event = _event("event-1")
    generated = CandidateGenerator().generate(
        draft=EventDetailDraft(
            unit_id="detail-1",
            detail_type="emotion",
            value="happy",
            evidence_span="happy",
        ),
        current_text="happy",
        existing_memories=[event],
        conversation_history=[],
        retrieved_candidates=[event],
    )
    resolution = TargetResolver().resolve(
        draft=EventDetailDraft(
            unit_id="detail-1",
            detail_type="emotion",
            value="happy",
            evidence_span="happy",
        ),
        generated=generated,
    )
    assert resolution.status == ResolutionStatus.UNRESOLVED
    assert resolution.reason == "candidate_set_not_complete"


def test_pending_binding_can_resolve_one_target_without_authorizing_write() -> None:
    event = _event("event-1")
    pending = PendingQuestion(
        question_id="q1",
        target_memory_id=event.id,
        target_kind=MemoryKind.INTERACTION_EVENT.value,
        event_type="conflict",
        expected_field="cause",
    )
    generated = CandidateGenerator().generate(
        draft=_detail(),
        current_text="because of money",
        existing_memories=[event],
        pending_question=pending,
    )
    resolution = TargetResolver().resolve(
        draft=_detail(),
        generated=generated,
        pending_question=pending,
    )
    assert resolution.status == ResolutionStatus.RESOLVED
    assert resolution.target_memory_id == event.id
    assert resolution.resolution_evidence == ["pending_question_binding"]

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from loveapp.domain.memory import (
    EpistemicStatus,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    TimeKind,
)
from loveapp.domain.memory_architecture_vnext import (
    Candidate,
    CandidateSource,
    ClarificationRequired,
    EventDetail,
    EventDetailDraft,
    ResolutionStatus,
    TargetResolution,
    WriteDecision,
    WriteOperation,
)
from loveapp.domain.runtime_context import PendingMemoryContext, PendingQuestion

NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


def _event(memory_id: str = "event-1") -> MemoryItem:
    return MemoryItem(
        id=memory_id,
        user_id="user",
        relationship_id="relationship",
        dedupe_key=f"dedupe:{memory_id}",
        kind=MemoryKind.INTERACTION_EVENT,
        subject="relationship",
        summary="A shared event",
        original_text="We had a conflict",
        evidence_spans=["We had a conflict"],
        time_kind=TimeKind.POINT,
        occurred_at=NOW,
        source_message_id="message-1",
        status=MemoryStatus.CONFIRMED,
        payload={"event_type": "conflict"},
    )


def test_event_detail_draft_is_semantic_only_and_keeps_raw_evidence() -> None:
    draft = EventDetailDraft(
        unit_id="u1",
        event_type_constraint="conflict",
        detail_type="cause",
        value={"description": "money"},
        evidence_span="because of money",
        source_proposition_id="p1",
    )
    assert draft.semantic_type == "event_detail"
    assert draft.evidence_span == "because of money"
    assert "target_memory_id" not in draft.model_dump()


@pytest.mark.parametrize("field", ["target_memory_id", "mutation_action", "db_patch"])
def test_event_detail_draft_rejects_write_authority(field: str) -> None:
    payload = {
        "unit_id": "u1",
        "detail_type": "cause",
        "value": "money",
        "evidence_span": "because of money",
        "reference_hints": {field: "forbidden"},
    }
    with pytest.raises(ValidationError, match="write authority"):
        EventDetailDraft.model_validate(payload)


def test_pending_question_supports_conversation_binding_metadata() -> None:
    question = PendingQuestion(
        question_id="q1",
        assistant_message_id="assistant-1",
        target_memory_id="event-1",
        target_kind=MemoryKind.INTERACTION_EVENT.value,
        event_type="conflict",
        expected_field="cause",
        created_turn="turn-1",
        expires_after_turns=3,
    )
    assert question.question_id == "q1"
    assert question.is_open
    assert question.target_memory_id == "event-1"


def test_legacy_pending_context_projects_binding_without_exposing_target_to_model() -> None:
    pending = PendingMemoryContext(
        previous_assistant_question="Why did you argue?",
        expected_slot="cause",
        target_kind=MemoryKind.INTERACTION_EVENT.value,
        event_type="conflict",
        target_field="cause",
        target_memory_id="event-1",
        assistant_message_id="assistant-1",
        created_turn="turn-1",
    )
    question = pending.to_pending_question()
    assert question.target_memory_id == "event-1"
    assert "target_memory_id" not in pending.model_dump(mode="json")


def test_candidate_requires_identity_and_retains_channel_provenance() -> None:
    candidate = Candidate(
        memory_id="event-1",
        memory=_event(),
        sources=[CandidateSource.PENDING_QUESTION, CandidateSource.VECTOR_FALLBACK],
        signals={"pending_binding_match": True, "vector_score": 0.73},
    )
    assert candidate.sources == [
        CandidateSource.PENDING_QUESTION,
        CandidateSource.VECTOR_FALLBACK,
    ]

    with pytest.raises(ValidationError, match="memory_id"):
        Candidate(memory_id="wrong", memory=_event())


def test_ambiguous_resolution_requires_clarification_and_no_target() -> None:
    clarification = ClarificationRequired(
        reason="two matching conflict events",
        field="cause",
        candidate_memory_ids=["event-1", "event-2"],
        question="Which conflict do you mean?",
    )
    resolution = TargetResolution(
        status=ResolutionStatus.AMBIGUOUS,
        candidate_ids=["event-1", "event-2"],
        reason="ambiguous semantic target",
        clarification=clarification,
    )
    assert resolution.target_memory_id is None
    assert resolution.clarification is not None


def test_resolved_target_requires_one_target() -> None:
    with pytest.raises(ValidationError, match="requires one"):
        TargetResolution(
            status=ResolutionStatus.RESOLVED,
            candidate_ids=["event-1"],
            reason="explicit reference",
        )


def test_attach_detail_requires_event_detail_and_target() -> None:
    detail = EventDetail(
        id="detail-1",
        parent_event_id="event-1",
        detail_type="emotion",
        value="happy",
        evidence_span="she was happy",
        source_message_id="message-2",
        created_at=NOW,
        perspective=MemoryPerspective.USER_REPORTED,
        epistemic_status=EpistemicStatus.CONFIRMED,
    )
    decision = WriteDecision(
        operation=WriteOperation.ATTACH_DETAIL,
        target_memory_id="event-1",
        detail=detail,
        reason="target was explicitly bound",
    )
    assert decision.operation == WriteOperation.ATTACH_DETAIL
    assert decision.detail is not None

    with pytest.raises(ValidationError, match="clarification"):
        WriteDecision(operation=WriteOperation.CLARIFY, reason="ambiguous")

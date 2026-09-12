from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.application.event_enrichment import resolve_event_enrichment
from loveapp.application.memory import MemoryService
from loveapp.domain.advice import RelationshipContext
from loveapp.domain.memory import (
    AtomicClaim,
    MemoryCandidate,
    MemoryGateDecision,
    MemoryGateReason,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemorySemanticGateReason,
    MemoryStatus,
    MessageRole,
    StoredMessage,
    TimeKind,
)
from loveapp.domain.memory_semantic_units import (
    AttributeNamespace,
    EnrichmentDraft,
    NewMemoryDraft,
    RefinementDraft,
    SemanticAtomicExtraction,
)
from loveapp.domain.runtime_context import PendingMemoryContext

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)


def _message(message_id: str = "source", content: str = "昨天我们吵架了") -> StoredMessage:
    return StoredMessage(
        id=message_id,
        conversation_id="c",
        user_id="u",
        relationship_id="r",
        role=MessageRole.USER,
        content=content,
        created_at=NOW,
    )


def _event(
    memory_id: str = "event",
    *,
    event_type: str = "conflict",
    source_message_id: str = "source",
    subject: str = "relationship",
    status: MemoryStatus = MemoryStatus.CONFIRMED,
    payload: dict[str, object] | None = None,
) -> MemoryItem:
    event_payload = {
        "event_type": event_type,
        "action": "吵架" if event_type == "conflict" else "一起活动",
        **(payload or {}),
    }
    return MemoryItem(
        id=memory_id,
        user_id="u",
        relationship_id="r",
        source_message_id=source_message_id,
        dedupe_key=f"dedupe-{memory_id}",
        status=status,
        kind=MemoryKind.INTERACTION_EVENT,
        subject=subject,
        summary="双方发生了一次互动",
        original_text="昨天我们吵架了",
        evidence_spans=["昨天我们吵架了"],
        time_kind=TimeKind.POINT,
        occurred_at=NOW,
        payload=event_payload,
        created_at=NOW,
        updated_at=NOW,
    )


def _draft(
    field: str,
    value: object,
    *,
    event_type: str = "conflict",
    namespace: AttributeNamespace = AttributeNamespace.CANONICAL,
    evidence: str = "她当时特别生气",
    target_hint: dict[str, object] | None = None,
) -> EnrichmentDraft:
    return EnrichmentDraft(
        unit_id=f"draft-{field}",
        target_kind=MemoryKind.INTERACTION_EVENT,
        target_semantic_hint=target_hint or {"event_type": event_type},
        attribute_namespace=namespace,
        attribute_name=field,
        value=value,
        evidence_span=evidence,
        confidence=0.95,
    )


@pytest.mark.parametrize(
    ("event_type", "field", "value", "text"),
    [
        ("conflict", "cause", {"category": "lateness"}, "因为我迟到了"),
        ("conflict", "severity", "severe", "吵得特别严重"),
        ("conflict", "emotion", "anger", "她当时特别生气"),
        ("conflict", "resolution", "talked_through", "后来我们说开了"),
        ("conflict", "outcome", "temporary_distance", "结果我们暂时没联系"),
        ("date", "location", "浦东", "那次是在浦东"),
        ("date", "activity_type", "看电影", "那次活动是看电影"),
        ("date", "emotion", "happy", "那天她很开心"),
        ("date", "outcome", "closer", "结果我们更亲近了"),
        ("shared_activity", "location", "世纪公园", "那次在世纪公园"),
        ("shared_activity", "activity_type", "散步", "当时一起散步"),
        ("shared_activity", "emotion", ["relaxed"], "当时感觉很放松"),
        ("shared_activity", "outcome", "positive", "结果挺好的"),
    ],
)
def test_canonical_event_fields_resolve_only_one_source_linked_target(
    event_type: str,
    field: str,
    value: object,
    text: str,
) -> None:
    target = _event(event_type=event_type)

    resolution = resolve_event_enrichment(
        _draft(field, value, event_type=event_type, evidence=text),
        current_text=text,
        conversation_history=[_message()],
        existing_memories=[target],
        user_id="u",
        relationship_id="r",
    )

    assert resolution.resolved
    assert resolution.semantic_candidate_ids == (target.id,)
    assert resolution.compatible_candidate_ids == (target.id,)


def test_semantic_cardinality_is_checked_before_field_compatibility() -> None:
    conflict = _event("conflict", event_type="conflict")
    date = _event("date", event_type="date")

    resolution = resolve_event_enrichment(
        _draft("severity", "severe", evidence="吵得特别严重"),
        current_text="吵得特别严重",
        conversation_history=[_message()],
        existing_memories=[conflict, date],
    )

    assert not resolution.resolved
    assert resolution.reason == "ambiguous_semantic_event_antecedent"
    assert set(resolution.semantic_candidate_ids) == {"conflict", "date"}
    assert resolution.compatible_candidate_ids == ()


def test_explicit_temporal_hint_can_separate_candidates_without_top_one_fallback() -> None:
    yesterday = _event(
        "yesterday",
        source_message_id="shared-source",
    ).model_copy(update={"occurred_at": datetime(2026, 9, 9, 12, tzinfo=UTC)})
    today = _event(
        "today",
        source_message_id="shared-source",
    ).model_copy(update={"occurred_at": datetime(2026, 9, 10, 12, tzinfo=UTC)})
    draft = _draft("emotion", "anger", evidence="昨天她特别生气").model_copy(
        update={"temporal_hint": "yesterday"}
    )

    resolution = resolve_event_enrichment(
        draft,
        current_text="昨天她特别生气",
        conversation_history=[_message("shared-source")],
        existing_memories=[yesterday, today],
        user_id="u",
        relationship_id="r",
    )

    assert resolution.resolved
    assert resolution.target is not None
    assert resolution.target.id == "yesterday"
    assert resolution.temporal_disambiguation_applied is True
    assert dict(resolution.candidate_scores)["yesterday"] == 1.0
    assert dict(resolution.candidate_scores)["today"] == 0.0


@pytest.mark.parametrize(
    ("memories", "expected_reason"),
    [
        ([], "no_source_or_context_linked_event"),
        (
            [_event("a"), _event("b")],
            "ambiguous_semantic_event_antecedent",
        ),
        (
            [_event(status=MemoryStatus.SUPERSEDED)],
            "event_target_not_active",
        ),
    ],
)
def test_zero_ambiguous_and_inactive_targets_fail_closed(
    memories: list[MemoryItem],
    expected_reason: str,
) -> None:
    resolution = resolve_event_enrichment(
        _draft("emotion", "anger"),
        current_text="她当时特别生气",
        conversation_history=[_message()],
        existing_memories=memories,
    )

    assert not resolution.resolved
    assert resolution.reason == expected_reason


@pytest.mark.parametrize(
    ("draft", "target", "reason"),
    [
        (
            _draft(
                "emotion",
                "anger",
                target_hint={"event_type": "conflict", "subject": "partner"},
            ),
            _event(subject="relationship"),
            "event_subject_mismatch",
        ),
        (
            _draft("emotion", "anger", event_type="date"),
            _event(event_type="conflict"),
            "event_type_mismatch",
        ),
        (
            _draft("severity", "severe", event_type="date"),
            _event(event_type="date"),
            "event_field_incompatible",
        ),
        (
            _draft("emotion", "sad"),
            _event(payload={"emotion": "anger"}),
            "existing_event_field_conflict",
        ),
    ],
)
def test_target_compatibility_rejections(
    draft: EnrichmentDraft,
    target: MemoryItem,
    reason: str,
) -> None:
    resolution = resolve_event_enrichment(
        draft,
        current_text=draft.evidence_span,
        conversation_history=[_message()],
        existing_memories=[target],
    )

    assert not resolution.resolved
    assert resolution.reason == reason


@pytest.mark.parametrize(
    "text",
    [
        "今天我们又吵了一架",
        "今天因为钱我们再次发生冲突",
        "昨天我们约会了",
        "今天她送了我一本书",
    ],
)
def test_new_bounded_occurrence_cannot_enrich_an_old_event(text: str) -> None:
    resolution = resolve_event_enrichment(
        _draft("cause", {"category": "financial_values"}, evidence=text),
        current_text=text,
        conversation_history=[_message()],
        existing_memories=[_event()],
    )

    assert not resolution.resolved
    assert resolution.reason == "new_event_occurrence_requires_create"


def test_new_occurrence_with_cause_word_still_requires_create() -> None:
    text = "今天我们又吵架了，原因是钱的问题"
    resolution = resolve_event_enrichment(
        _draft("cause", {"category": "financial_values"}, evidence=text),
        current_text=text,
        conversation_history=[_message()],
        existing_memories=[_event()],
    )

    assert not resolution.resolved
    assert resolution.reason == "new_event_occurrence_requires_create"


def test_state_and_pattern_rows_are_not_generic_enrichment_targets() -> None:
    non_events = [
        _event("state").model_copy(update={"kind": MemoryKind.RELATIONSHIP_STATE}),
        _event("pattern").model_copy(update={"kind": MemoryKind.INTERACTION_PATTERN}),
    ]

    resolution = resolve_event_enrichment(
        _draft("emotion", "anger"),
        current_text="她当时特别生气",
        conversation_history=[_message()],
        existing_memories=non_events,
    )

    assert not resolution.resolved
    assert resolution.reason == "no_source_or_context_linked_event"


def test_user_belief_cannot_enrich_an_event() -> None:
    draft = _draft("emotion", "anger").model_copy(
        update={"perspective": MemoryPerspective.USER_BELIEF}
    )

    resolution = resolve_event_enrichment(
        draft,
        current_text="她当时特别生气",
        conversation_history=[_message()],
        existing_memories=[_event()],
    )

    assert not resolution.resolved
    assert resolution.reason == "enrichment_evidence_not_confirmed"


@pytest.mark.parametrize(
    "target_kind",
    [
        MemoryKind.STABLE_FACT,
        MemoryKind.PREFERENCE,
        MemoryKind.INTERACTION_PATTERN,
        MemoryKind.RELATIONSHIP_STATE,
    ],
)
def test_generic_enrichment_draft_rejects_non_event_target_kinds(
    target_kind: MemoryKind,
) -> None:
    payload = _draft("emotion", "anger").model_dump()
    payload["target_kind"] = target_kind

    with pytest.raises(ValidationError, match="limited to interaction_event"):
        EnrichmentDraft.model_validate(payload)


def test_custom_weather_resolves_only_for_date_or_shared_activity() -> None:
    draft = _draft(
        "weather",
        "rainy",
        event_type="date",
        namespace=AttributeNamespace.CUSTOM,
        evidence="那天下雨了",
    )
    resolution = resolve_event_enrichment(
        draft,
        current_text="那天下雨了",
        conversation_history=[_message()],
        existing_memories=[_event(event_type="date")],
    )

    assert resolution.resolved
    command = resolution.to_enrichment(source_message_id="weather-source", created_at=NOW)
    assert command.field.value == "custom_attribute"
    assert command.value.attribute == "weather"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("field", "event_type", "reason"),
    [
        ("gift", "date", "unsupported_custom_event_attribute"),
        ("weather", "conflict", "custom_attribute_event_type_mismatch"),
    ],
)
def test_custom_attribute_scope_is_bounded(
    field: str,
    event_type: str,
    reason: str,
) -> None:
    resolution = resolve_event_enrichment(
        _draft(
            field,
            "value",
            event_type=event_type,
            namespace=AttributeNamespace.CUSTOM,
            evidence="那天有补充信息",
        ),
        current_text="那天有补充信息",
        conversation_history=[_message()],
        existing_memories=[_event(event_type=event_type)],
    )

    assert not resolution.resolved
    assert resolution.reason == reason


def test_context_link_can_follow_a_previous_enrichment_source() -> None:
    target = _event(
        payload={
            "enrichment_history": [
                {"field": "cause", "source_message_id": "cause-message"}
            ]
        }
    )
    resolution = resolve_event_enrichment(
        _draft("emotion", "anger"),
        current_text="她当时特别生气",
        conversation_history=[_message("cause-message", "因为我迟到了")],
        existing_memories=[target],
    )

    assert resolution.resolved
    assert resolution.reason == "unique_context_linked_event"
    assert resolution.antecedent_message_id == "source"


def test_pending_slot_finds_one_semantic_target_without_recency_top_one() -> None:
    pending = PendingMemoryContext(
        previous_assistant_question="为什么吵架？",
        expected_slot="cause",
        topic="conflict",
        pending_slot_id="slot",
        target_kind="interaction_event",
        event_type="conflict",
        target_field="cause",
        created_turn="assistant-message",
    )
    resolution = resolve_event_enrichment(
        _draft("cause", {"category": "lateness"}, evidence="因为我迟到了"),
        current_text="因为我迟到了",
        conversation_history=[_message("unlinked", "我不太确定")],
        existing_memories=[_event()],
        pending_memory_context=pending,
    )

    assert resolution.resolved
    assert resolution.semantic_candidate_ids == ("event",)


def test_pending_slot_with_multiple_events_fails_closed() -> None:
    pending = PendingMemoryContext(
        previous_assistant_question="为什么吵架？",
        expected_slot="cause",
        topic="conflict",
        target_kind="interaction_event",
        event_type="conflict",
        target_field="cause",
        created_turn="assistant-message",
    )
    resolution = resolve_event_enrichment(
        _draft("cause", {"category": "lateness"}, evidence="因为我迟到了"),
        current_text="因为我迟到了",
        conversation_history=[_message("unlinked", "我不太确定")],
        existing_memories=[_event("first"), _event("second")],
        pending_memory_context=pending,
    )

    assert not resolution.resolved
    assert resolution.reason == "ambiguous_semantic_event_antecedent"


def test_retrieved_event_candidate_can_bind_when_latest_user_turn_is_not_target() -> None:
    target = _event("retrieved", source_message_id="old-source")
    latest = _message("latest", "我还想补充上一件事")
    resolution = resolve_event_enrichment(
        _draft("emotion", "anger"),
        current_text="她当时特别生气",
        conversation_history=[latest],
        existing_memories=[target],
        retrieved_candidates=[target],
    )

    assert resolution.resolved
    assert resolution.target is not None and resolution.target.id == target.id
    assert resolution.semantic_candidate_ids == (target.id,)
    assert resolution.compatible_candidate_ids == (target.id,)


def test_retrieved_event_candidates_preserve_ambiguity_before_compatibility() -> None:
    first = _event("retrieved-1", source_message_id="old-source-1")
    second = _event("retrieved-2", source_message_id="old-source-2")
    resolution = resolve_event_enrichment(
        _draft("severity", "severe"),
        current_text="她当时特别生气",
        conversation_history=[_message("latest", "我还想补充上一件事")],
        existing_memories=[first, second],
        retrieved_candidates=[first, second],
    )

    assert not resolution.resolved
    assert resolution.reason == "ambiguous_semantic_event_antecedent"
    assert set(resolution.semantic_candidate_ids) == {first.id, second.id}
    assert resolution.compatible_candidate_ids == ()


def test_new_memory_draft_round_trips_atomic_claim_without_loss() -> None:
    claim = AtomicClaim(
        claim_id="claim",
        kind=MemoryKind.INTERACTION_EVENT,
        subject="relationship",
        predicate="had_argument",
        object="conflict",
        summary="双方发生冲突",
        evidence_spans=["我们吵架了"],
        time_kind=TimeKind.POINT,
        occurred_at=NOW,
        payload={"event_type": "conflict", "cause": {"category": "lateness"}},
        confidence=0.93,
    )

    draft = NewMemoryDraft.from_atomic_claim(claim)

    assert draft.semantic_payload == claim.payload
    assert draft.to_atomic_claim().model_dump() == claim.model_dump()


def test_enrichment_draft_cannot_carry_a_trusted_target_or_mutation() -> None:
    with pytest.raises(ValidationError):
        EnrichmentDraft.model_validate(
            {
                **_draft("emotion", "anger").model_dump(),
                "target_semantic_hint": {"target_memory_id": "m1"},
            }
        )


def test_negative_semantic_gate_cannot_carry_semantic_units() -> None:
    with pytest.raises(ValidationError):
        SemanticAtomicExtraction(
            should_extract=False,
            gate_reason=MemorySemanticGateReason.NO_MEMORY,
            semantic_units=[_draft("emotion", "anger")],
        )


def test_negative_semantic_gate_cannot_carry_legacy_claims() -> None:
    with pytest.raises(ValidationError):
        SemanticAtomicExtraction(
            should_extract=False,
            gate_reason=MemorySemanticGateReason.NO_MEMORY,
            claims=[
                AtomicClaim(
                    claim_id="claim",
                    kind=MemoryKind.STABLE_FACT,
                    subject="user",
                    predicate="resides_in",
                    object="Shanghai",
                    summary="User lives in Shanghai",
                    evidence_spans=["I live in Shanghai"],
                )
            ],
        )


class _AlwaysGate:
    def evaluate(self, text: str) -> MemoryGateDecision:
        return MemoryGateDecision(
            should_extract=True,
            reason=MemoryGateReason.DURABLE_SIGNAL,
        )


class _ExtractionSequence:
    def __init__(self, extractions: list[SemanticAtomicExtraction]) -> None:
        self._extractions = list(extractions)

    async def extract(self, text: str, **_: object) -> SemanticAtomicExtraction:
        return self._extractions.pop(0)


@pytest.mark.asyncio
async def test_memory_service_commits_enrichment_and_traces_refinement_only() -> None:
    store = InMemoryMemoryStore()
    await store.save_relationship_context(RelationshipContext(user_id="u", relationship_id="r"))
    source = await store.add_message(
        user_id="u",
        relationship_id="r",
        conversation_id="c",
        role=MessageRole.USER,
        content="昨天我们吵架了",
        message_id="source",
    )
    saved = await store.save_memory(
        user_id="u",
        relationship_id="r",
        candidate=MemoryCandidate(
            kind=MemoryKind.INTERACTION_EVENT,
            subject="relationship",
            summary="双方发生冲突",
            original_text=source.content,
            evidence_spans=[source.content],
            time_kind=TimeKind.POINT,
            occurred_at=NOW,
            payload={"event_type": "conflict", "action": "吵架"},
        ),
        source_message_id=source.id,
        status=MemoryStatus.CONFIRMED,
    )
    extraction = SemanticAtomicExtraction(
        should_extract=True,
        gate_reason=MemorySemanticGateReason.COMPOUND_MEMORY,
        semantic_units=[
            _draft(
                "cause",
                {"category": "lateness"},
                evidence="因为我迟到了",
            )
        ],
    )
    refinement = SemanticAtomicExtraction(
        should_extract=True,
        gate_reason=MemorySemanticGateReason.STABLE_FACT,
        semantic_units=[
            RefinementDraft(
                unit_id="refine",
                target_kind=MemoryKind.STABLE_FACT,
                target_semantic_hint={"predicate": "resides_in"},
                raw_predicate="resides_in_district",
                value="浦东",
                evidence_span="具体在浦东",
            )
        ],
    )
    service = MemoryService(
        store,
        _ExtractionSequence([extraction, refinement]),
        gate=_AlwaysGate(),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    result = await service.remember_text(
        user_id="u",
        relationship_id="r",
        conversation_id="c",
        text="因为我迟到了",
    )
    await service.remember_text(
        user_id="u",
        relationship_id="r",
        conversation_id="c",
        text="具体在浦东",
    )
    loaded = await store.get_memory(saved.item.id, "u")
    audits = await store.list_transition_audits(user_id="u", relationship_id="r")

    assert loaded is not None
    assert loaded.payload["cause"] == {"category": "lateness"}
    assert result.saved == []
    assert result.contextual_updated_memory_ids == [saved.item.id]
    assert any(audit.rule_name == "enrich_event_cause" for audit in audits)
    assert any(audit.rule_name == "refinement_draft_trace_only" for audit in audits)

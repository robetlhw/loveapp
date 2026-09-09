from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore, SQLiteMemoryStore
from loveapp.application.memory import MemoryService
from loveapp.application.memory_admission import assess_memory_admission
from loveapp.application.memory_pattern_consolidation import (
    detect_interaction_pattern_consolidation,
    govern_interaction_pattern_consolidation,
)
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    EpistemicStatus,
    EvidenceExplicitness,
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    PredicateType,
    TimeKind,
    memory_dedupe_key,
)
from loveapp.domain.memory_normalization import normalize_memory_candidate_contract
from loveapp.domain.memory_predicates import normalize_predicate, predicate_spec

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)


class _SequenceExtractor:
    def __init__(self, claims: list[AtomicClaim]) -> None:
        self._claims = iter(claims)

    async def extract(self, text: str, **_: object) -> AtomicExtraction:
        return AtomicExtraction(claims=[next(self._claims)])


def _normalize(candidate: MemoryCandidate) -> MemoryCandidate:
    return normalize_memory_candidate_contract(
        candidate,
        NOW,
        allow_legacy_open_world=True,
    )


def _misclassified_pattern(text: str) -> MemoryCandidate:
    return MemoryCandidate(
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="partner",
        summary=text,
        original_text=text,
        evidence_spans=[text],
        payload={
            "predicate": "partner_initiated_chat",
            "metric": "initiation_balance",
            "current": "partner_to_user",
        },
        raw_predicate="partner_initiated_chat",
        predicate_type=PredicateType.CUSTOM,
        confidence=0.95,
        explicitness=EvidenceExplicitness.EXPLICIT,
    )


def _event(
    index: int,
    *,
    action: str = "partner_initiated_chat",
    subject: str = "partner",
    relationship_id: str = "relationship-v22",
    text: str | None = None,
    occurred_at: datetime | None = None,
    confidence: float = 0.95,
) -> MemoryItem:
    event_time = occurred_at or NOW - timedelta(days=3 - index)
    event_text = text or f"Event {index}: partner initiated chat."
    candidate = _normalize(
        MemoryCandidate(
            kind=MemoryKind.INTERACTION_EVENT,
            subject=subject,
            summary=event_text,
            original_text=event_text,
            evidence_spans=[event_text],
            time_kind=TimeKind.POINT,
            occurred_at=event_time,
            payload={
                "predicate": action,
                "action": action,
                "participants": ["user", "partner"],
            },
            raw_predicate=action,
            predicate_type=PredicateType.CUSTOM,
            confidence=confidence,
            explicitness=EvidenceExplicitness.EXPLICIT,
        )
    )
    return MemoryItem(
        **candidate.model_dump(),
        id=f"event-{index}",
        user_id="user-v22",
        relationship_id=relationship_id,
        status=MemoryStatus.CONFIRMED,
        source_message_id=f"message-{index}",
        created_at=event_time,
        updated_at=event_time,
        dedupe_key=memory_dedupe_key(candidate),
    )


def test_single_bounded_behavior_is_refined_from_pattern_to_event() -> None:
    candidate = _normalize(_misclassified_pattern("Yesterday partner initiated chat."))

    assert candidate.kind == MemoryKind.INTERACTION_EVENT
    assert candidate.time_kind == TimeKind.POINT
    assert candidate.payload["type_refinement"] == "single_behavior_pattern_to_event"
    assert "metric" not in candidate.payload
    assert candidate.state_dimension is None
    assert candidate.state_value is None


def test_single_bounded_behavior_without_explicit_date_is_still_an_event() -> None:
    candidate = _normalize(_misclassified_pattern("Partner initiated chat."))

    assert candidate.kind == MemoryKind.INTERACTION_EVENT
    assert candidate.time_kind == TimeKind.POINT
    assert candidate.payload["type_refinement"] == "single_behavior_pattern_to_event"


def test_admission_requires_pattern_recurrence_window_and_confidence() -> None:
    missing_window = _misclassified_pattern("Partner often initiated chat.")
    low_confidence = _misclassified_pattern(
        "Over the last month partner often initiated chat."
    ).model_copy(update={"confidence": 0.7})

    for candidate in (missing_window, low_confidence):
        normalized = _normalize(candidate)
        assessment = assess_memory_admission(
            normalized,
            normalized.original_text,
        )
        assert assessment.decision.value == "reject"
        assert assessment.reason == "interaction_pattern_boundary_invalid"


def test_recurrent_windowed_behavior_remains_pattern() -> None:
    text = "Over the last month partner often initiated chat."
    candidate = _normalize(_misclassified_pattern(text))

    assert candidate.kind == MemoryKind.INTERACTION_PATTERN
    assert candidate.canonical_predicate == "interaction.initiation_balance"
    assert candidate.state_value == "partner_to_user"


def test_distribution_cue_keeps_windowed_contact_claim_as_pattern() -> None:
    text = "最近一个月她基本都不主动找我了。"
    candidate = _normalize(
        MemoryCandidate(
            kind=MemoryKind.INTERACTION_PATTERN,
            subject="relationship",
            summary=text,
            original_text=text,
            evidence_spans=[text],
            payload={
                "metric": "initiation_balance",
                "direction": "decreasing",
            },
            raw_predicate="partner_contact_initiation_decreased",
            predicate_type=PredicateType.CUSTOM,
            confidence=0.8,
            explicitness=EvidenceExplicitness.STRONGLY_IMPLIED,
        )
    )

    assert candidate.kind == MemoryKind.INTERACTION_PATTERN
    assert candidate.canonical_predicate == "interaction.initiation_balance"
    assert candidate.payload["direction"] == "decreasing"


def test_trend_shaped_contact_claim_is_not_fabricated_as_single_event() -> None:
    text = "对方主动联系明显下降。"
    candidate = _normalize(
        MemoryCandidate(
            kind=MemoryKind.INTERACTION_PATTERN,
            subject="relationship",
            summary=text,
            original_text=text,
            evidence_spans=[text],
            payload={
                "metric": "initiation_balance",
                "direction": "decreasing",
            },
            raw_predicate="interaction.initiation_balance",
            predicate_type=PredicateType.CANONICAL,
            canonical_predicate="interaction.initiation_balance",
            confidence=0.8,
            explicitness=EvidenceExplicitness.STRONGLY_IMPLIED,
        )
    )

    assert candidate.kind == MemoryKind.INTERACTION_PATTERN
    assert candidate.payload["direction"] == "decreasing"
    assert "type_refinement" not in candidate.payload
    admission = assess_memory_admission(candidate, text)
    assert admission.decision.value == "propose"
    assert admission.score_breakdown["pattern_has_recurrence"] is False
    assert admission.score_breakdown["pattern_has_time_window"] is False


def test_two_events_are_insufficient_for_pattern_consolidation() -> None:
    events = [_event(1), _event(2)]

    candidate = detect_interaction_pattern_consolidation(
        events[-1],
        events,
        reference_time=NOW,
    )

    assert candidate is None


def test_three_distinct_events_generate_evidence_backed_pattern() -> None:
    events = [_event(1), _event(2), _event(3)]

    candidate = detect_interaction_pattern_consolidation(
        events[-1],
        events,
        reference_time=NOW,
    )

    assert candidate is not None
    normalized = _normalize(candidate)
    assert normalized.kind == MemoryKind.INTERACTION_PATTERN
    assert normalized.perspective == MemoryPerspective.MODEL_INFERRED
    assert normalized.epistemic_status == EpistemicStatus.HYPOTHESIS
    assert normalized.payload["source"] == "model_inferred"
    assert normalized.subject == "relationship"
    assert normalized.payload["evidence_subject"] == events[-1].subject
    assert normalized.payload["participants"] == ["partner", "user"]
    assert normalized.payload["evidence_ids"] == [event.id for event in events]
    assert normalized.payload["time_window"] == {
        "start": events[0].occurred_at.isoformat(),
        "end": events[-1].occurred_at.isoformat(),
    }
    assert normalized.confidence >= 0.9


def test_structured_action_signature_drives_consolidation() -> None:
    events = [
        _event(index, text=f"Interaction log entry {index}.")
        for index in range(1, 4)
    ]

    candidate = detect_interaction_pattern_consolidation(
        events[-1],
        events,
        reference_time=NOW,
    )

    assert candidate is not None
    assert candidate.payload["consolidation_rule"] == "partner_initiated_contact"
    assert candidate.state_value == "partner_to_user"


def test_three_reports_of_one_occurrence_do_not_manufacture_recurrence() -> None:
    occurrence = NOW - timedelta(days=1)
    events = [
        _event(
            index,
            text=text,
            occurred_at=occurrence + timedelta(minutes=index),
        )
        for index, text in enumerate(
            (
                "Yesterday partner initiated chat.",
                "Yesterday partner contacted me first.",
                "Yesterday partner sent the first message.",
            ),
            start=1,
        )
    ]

    assert (
        detect_interaction_pattern_consolidation(
            events[-1],
            events,
            reference_time=NOW,
        )
        is None
    )


def test_negated_events_create_reduced_frequency_not_positive_initiative() -> None:
    events = [
        _event(
            index,
            text="她没有主动找我聊天。",
        )
        for index in range(1, 4)
    ]

    candidate = detect_interaction_pattern_consolidation(
        events[-1],
        events,
        reference_time=NOW,
    )

    assert candidate is not None
    assert candidate.canonical_predicate == "interaction.contact_frequency"
    assert candidate.state_value == "low"
    assert candidate.payload["consolidation_rule"] == "communication_frequency_reduced"


def test_trigger_must_be_present_in_the_eligible_cluster() -> None:
    historical = [_event(index) for index in range(1, 4)]
    trigger = _event(4, occurred_at=NOW - timedelta(hours=1))

    assert (
        detect_interaction_pattern_consolidation(
            trigger,
            historical,
            reference_time=NOW,
        )
        is None
    )


def test_out_of_window_trigger_cannot_reconsolidate_recent_evidence() -> None:
    recent = [_event(index) for index in range(1, 4)]
    trigger = _event(4, occurred_at=NOW - timedelta(days=31))

    assert (
        detect_interaction_pattern_consolidation(
            trigger,
            [*recent, trigger],
            reference_time=NOW,
        )
        is None
    )


def test_confirmed_low_confidence_events_do_not_create_pattern() -> None:
    events = [_event(index, confidence=0.70) for index in range(1, 4)]

    assert (
        detect_interaction_pattern_consolidation(
            events[-1],
            events,
            reference_time=NOW,
        )
        is None
    )


def test_consolidation_confidence_reflects_evidence_instead_of_forcing_point_nine() -> None:
    events = [_event(index, confidence=0.80) for index in range(1, 4)]

    candidate = detect_interaction_pattern_consolidation(
        events[-1],
        events,
        reference_time=NOW,
    )

    assert candidate is not None
    assert abs(candidate.confidence - 0.80) < 1e-9


def test_consolidated_pattern_runs_through_typed_governance() -> None:
    events = [_event(1), _event(2), _event(3)]

    result = govern_interaction_pattern_consolidation(
        events[-1],
        events,
        source_text=events[-1].original_text,
        reference_time=NOW,
    )

    assert result.audit is None
    assert result.operation is not None
    assert result.operation.rule_name == "event_pattern_consolidation"
    assert result.operation.status == MemoryStatus.PROPOSED
    assert result.operation.candidate.admission_decision is not None
    assert result.operation.candidate.payload["evidence_ids"] == [
        event.id for event in events
    ]


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_memory_service_consolidates_third_distinct_event_without_replacing_events(
    backend: str,
    tmp_path: Path,
) -> None:
    texts = [
        "Remember: last Monday partner initiated chat.",
        "Remember: yesterday partner initiated chat.",
        "Remember: today partner initiated chat.",
        "Remember: this morning partner initiated chat.",
    ]
    claims = [
        AtomicClaim(
            claim_id=f"event-{index}",
            kind=MemoryKind.INTERACTION_EVENT,
            subject="partner",
            predicate="partner_initiated_chat",
            summary=text,
            evidence_spans=[text],
            time_kind=TimeKind.POINT,
            occurred_at=NOW - timedelta(days=4 - index),
            payload={
                "action": "partner_initiated_chat",
                "participants": ["user", "partner"],
            },
            raw_predicate="partner_initiated_chat",
            predicate_type=PredicateType.CUSTOM,
            confidence=0.95,
            explicitness=EvidenceExplicitness.EXPLICIT,
        )
        for index, text in enumerate(texts, start=1)
    ]
    store = (
        InMemoryMemoryStore(clock=lambda: NOW)
        if backend == "memory"
        else SQLiteMemoryStore(tmp_path / "v22-pattern-merge.db", clock=lambda: NOW)
    )
    service = MemoryService(store, _SequenceExtractor(claims), clock=lambda: NOW)

    results = []
    for text in texts:
        results.append(
            await service.remember_text(
                user_id="service-v22-user",
                relationship_id="service-v22-relationship",
                conversation_id="service-v22-conversation",
                text=text,
                status=MemoryStatus.CONFIRMED,
            )
        )
    memories = await store.list_memories(
        user_id="service-v22-user",
        relationship_id="service-v22-relationship",
        limit=20,
    )

    events = [item for item in memories if item.kind == MemoryKind.INTERACTION_EVENT]
    patterns = [item for item in memories if item.kind == MemoryKind.INTERACTION_PATTERN]
    assert len(events) == 4
    assert all(item.status == MemoryStatus.CONFIRMED for item in events)
    assert len(patterns) == 1
    assert patterns[0].status == MemoryStatus.PROPOSED
    assert set(patterns[0].payload["evidence_ids"]) == {item.id for item in events}
    event_times = [item.occurred_at for item in events if item.occurred_at is not None]
    assert patterns[0].time_kind == TimeKind.INTERVAL
    assert patterns[0].period_start == min(event_times)
    assert patterns[0].period_end == max(event_times)
    assert patterns[0].payload["time_window"] == {
        "start": patterns[0].period_start.isoformat(),
        "end": patterns[0].period_end.isoformat(),
    }
    assert len(results[0].saved) == 1
    assert len(results[1].saved) == 1
    assert len(results[2].saved) == 2
    assert len(results[3].saved) == 2
    assert results[2].saved[-1].item.id == results[3].saved[-1].item.id
    assert results[3].saved[-1].created is False


def test_event_clusters_do_not_cross_subject_or_signature() -> None:
    trigger = _event(3)
    candidates = [
        _event(1, subject="relationship"),
        _event(2, action="partner_had_meal"),
        trigger,
    ]

    assert (
        detect_interaction_pattern_consolidation(
            trigger,
            candidates,
            reference_time=NOW,
        )
        is None
    )


def test_model_inferred_pattern_requires_window_and_sufficient_confidence() -> None:
    candidate = MemoryCandidate(
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="relationship",
        summary="Inferred partner initiative.",
        original_text="Partner initiated chat again.",
        evidence_spans=["Partner initiated chat again."],
        perspective=MemoryPerspective.MODEL_INFERRED,
        confidence=0.7,
        payload={
            "predicate": "interaction.initiation_balance",
            "metric": "initiation_balance",
            "current": "partner_to_user",
            "evidence_ids": ["event-1", "event-2", "event-3"],
        },
        raw_predicate="interaction.initiation_balance",
        predicate_type=PredicateType.CANONICAL,
        canonical_predicate="interaction.initiation_balance",
    )

    try:
        _normalize(candidate)
    except ValueError as exc:
        assert "INTERACTION_PATTERN_PAYLOAD_INVALID" in str(exc)
    else:
        raise AssertionError("model-inferred Pattern without time_window was accepted")


def test_relationship_preference_dimensions_are_bounded_and_append_only() -> None:
    expected = {
        "interaction_style": "preference.relationship.interaction_style",
        "emotional_need": "preference.relationship.emotional_need",
    }
    for dimension, canonical in expected.items():
        normalized = normalize_predicate(
            kind=MemoryKind.PREFERENCE,
            raw_predicate="preference.general",
            predicate_type=PredicateType.CANONICAL,
            payload={
                "domain": "relationship",
                "dimension": dimension,
                "value": "example",
                "preference_type": "like",
            },
        )
        spec = predicate_spec(canonical)

        assert normalized.canonical_predicate == canonical
        assert spec is not None
        assert spec.cardinality.value == "multi"
        assert spec.update_policy.value == "append"


def test_existing_communication_style_dimension_remains_canonical() -> None:
    normalized = normalize_predicate(
        kind=MemoryKind.PREFERENCE,
        raw_predicate="preference.general",
        predicate_type=PredicateType.CANONICAL,
        payload={
            "domain": "communication",
            "dimension": "communication_style",
            "value": "direct",
            "preference_type": "like",
        },
    )

    assert normalized.canonical_predicate == "preference.communication.style"

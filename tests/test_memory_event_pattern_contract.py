from datetime import UTC, datetime

import pytest

from loveapp.application.memory_admission import (
    assess_memory_admission,
    assess_pattern_evidence_links,
)
from loveapp.domain.memory import (
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    memory_dedupe_key,
)
from loveapp.domain.memory_lifecycle import (
    MemoryRole,
    memory_role,
    normalize_memory_candidate,
)
from loveapp.domain.memory_normalization import (
    NormalizationContractError,
    normalize_memory_candidate_contract,
)

REFERENCE_TIME = datetime(2026, 9, 8, 12, tzinfo=UTC)


def _candidate(
    *,
    kind: MemoryKind,
    payload: dict[str, object],
    perspective: MemoryPerspective = MemoryPerspective.USER_REPORTED,
    **updates: object,
) -> MemoryCandidate:
    return MemoryCandidate(
        kind=kind,
        subject="relationship",
        summary="这是一条关系记忆",
        original_text="这是一条关系记忆",
        payload=payload,
        perspective=perspective,
        **updates,
    )


def _normalize(candidate: MemoryCandidate) -> MemoryCandidate:
    return normalize_memory_candidate_contract(candidate, REFERENCE_TIME)


def _event_item(memory_id: str) -> MemoryItem:
    candidate = _normalize(
        _candidate(
            kind=MemoryKind.INTERACTION_EVENT,
            occurred_at=REFERENCE_TIME,
            payload={
                "predicate": "partner_initiated_chat",
                "participants": ["user", "partner"],
                "action": "chat",
            },
        )
    )
    return MemoryItem(
        **candidate.model_dump(),
        id=memory_id,
        user_id="event-pattern-user",
        relationship_id="event-pattern-relationship",
        status=MemoryStatus.CONFIRMED,
        source_message_id=f"source-{memory_id}",
        created_at=REFERENCE_TIME,
        updated_at=REFERENCE_TIME,
        dedupe_key=memory_dedupe_key(candidate),
    )


def test_interaction_event_normalizes_bounded_schema_aliases() -> None:
    event = _normalize(
        _candidate(
            kind=MemoryKind.INTERACTION_EVENT,
            occurred_at=REFERENCE_TIME,
            emotions=["开心"],
            payload={
                "predicate": "had_meal",
                "actors": "我们",
                "activity_type": "吃饭",
                "event_time": "昨天",
                "place": "餐厅",
                "feeling": "开心",
                "result": "聊得很好",
                "source": "reported",
            },
        )
    )

    assert event.payload["participants"] == ["user", "partner"]
    assert event.payload["action"] == "吃饭"
    assert event.payload["activity_type"] == "吃饭"
    assert event.payload["time"] == "昨天"
    assert event.payload["location"] == "餐厅"
    assert event.payload["emotion"] == "开心"
    assert event.payload["outcome"] == "聊得很好"
    assert event.payload["source"] == "user_reported"


def test_interaction_event_accepts_v21_schema_fields_at_claim_boundary() -> None:
    event = _normalize(
        MemoryCandidate.model_validate(
            {
                "kind": "interaction_event",
                "subject": "relationship",
                "summary": "双方昨天一起聊天",
                "original_text": "昨天我们一起聊天",
                "participants": ["user", "partner"],
                "action": "chat",
                "time": "昨天",
                "location": "咖啡店",
                "emotion": "开心",
                "outcome": "沟通顺利",
                "source": "reported",
            }
        )
    )

    assert event.time_kind.value == "unknown"
    assert event.payload["time"] == "昨天"
    assert event.payload["participants"] == ["user", "partner"]
    assert event.payload["activity_type"] == "chat"
    assert event.payload["source"] == "user_reported"


def test_legacy_event_time_kind_is_not_misread_as_event_time_value() -> None:
    event = MemoryCandidate.model_validate(
        {
            "kind": "interaction_event",
            "subject": "relationship",
            "summary": "双方昨天一起聊天",
            "original_text": "昨天我们一起聊天",
            "time": "point",
            "payload": {"predicate": "chatted"},
        }
    )

    assert event.time_kind.value == "point"
    assert "time" not in event.payload


@pytest.mark.parametrize(
    ("document_type", "expected_kind"),
    [
        ("interaction_event", MemoryKind.INTERACTION_EVENT),
        ("interaction_pattern", MemoryKind.INTERACTION_PATTERN),
        ("plan", MemoryKind.PLANNED_EVENT),
    ],
)
def test_v21_type_discriminator_aliases_to_production_kind(
    document_type: str,
    expected_kind: MemoryKind,
) -> None:
    candidate = MemoryCandidate.model_validate(
        {
            "type": document_type,
            "subject": "relationship",
            "summary": "文档格式的记忆",
            "original_text": "文档格式的记忆",
            "payload": (
                {"metric": "contact_frequency", "current": "high"}
                if expected_kind == MemoryKind.INTERACTION_PATTERN
                else {}
            ),
        }
    )

    assert candidate.kind == expected_kind


def test_legacy_interaction_event_without_new_fields_remains_valid() -> None:
    event = _normalize(
        _candidate(
            kind=MemoryKind.INTERACTION_EVENT,
            payload={"predicate": "partner_replied"},
        )
    )

    assert event.payload["predicate"] == "partner_replied"
    assert event.payload["source"] == "user_reported"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("participants", ["user", 3]),
        ("action", 3),
        ("source", "assistant_generated"),
    ],
)
def test_invalid_event_payload_is_rejected(field: str, value: object) -> None:
    with pytest.raises(NormalizationContractError, match="INTERACTION_EVENT_PAYLOAD_INVALID"):
        _normalize(
            _candidate(
                kind=MemoryKind.INTERACTION_EVENT,
                payload={"predicate": "had_meal", field: value},
            )
        )


def test_user_reported_pattern_gets_provenance_and_time_window() -> None:
    pattern = _normalize(
        _candidate(
            kind=MemoryKind.INTERACTION_PATTERN,
            period_start=datetime(2026, 9, 1, tzinfo=UTC),
            period_end=REFERENCE_TIME,
            payload={
                "predicate": "engagement_increased",
                "metric": "contact_frequency",
                "current": "high",
                "time_range": "最近一周",
            },
        )
    )

    assert pattern.payload["source"] == "user_reported"
    assert pattern.payload["time_window"] == {"label": "最近一周"}


def test_optional_pattern_time_window_accepts_provider_null_as_absent() -> None:
    pattern = _normalize(
        _candidate(
            kind=MemoryKind.INTERACTION_PATTERN,
            payload={
                "predicate": "shared_meal_frequency",
                "metric": "contact_frequency",
                "frequency": "rare",
                "time_window": None,
            },
        )
    )

    assert "time_window" not in pattern.payload


def test_model_inferred_pattern_requires_evidence_ids() -> None:
    pattern = _normalize(
        _candidate(
            kind=MemoryKind.INTERACTION_PATTERN,
            perspective=MemoryPerspective.MODEL_INFERRED,
            payload={
                "predicate": "engagement_increased",
                    "metric": "contact_frequency",
                    "current": "high",
                    "evidence_ids": "event-1",
                    "time_window": ["2026-09-01", "2026-09-08"],
            },
        )
    )

    assert pattern.payload["source"] == "model_inferred"
    assert pattern.payload["evidence_ids"] == ["event-1"]


def test_pattern_provenance_fields_flatten_from_claim_boundary() -> None:
    pattern = _normalize(
        MemoryCandidate.model_validate(
            {
                "kind": "interaction_pattern",
                "subject": "relationship",
                "summary": "最近联系更频繁",
                "original_text": "最近一周她经常主动联系我",
                "metric": "contact_frequency",
                "current": "high",
                "source": "model",
                "event_ids": ["event-1", "event-2"],
                "time_window": ["2026-09-01", "2026-09-08"],
                "perspective": "model_inferred",
            }
        )
    )

    assert pattern.payload["source"] == "model_inferred"
    assert pattern.payload["evidence_ids"] == ["event-1", "event-2"]
    assert pattern.payload["time_window"] == {
        "start": "2026-09-01",
        "end": "2026-09-08",
    }


def test_model_inferred_pattern_without_evidence_fails_closed() -> None:
    with pytest.raises(
        NormalizationContractError,
        match="INTERACTION_PATTERN_PAYLOAD_INVALID",
    ):
        _normalize(
            _candidate(
                kind=MemoryKind.INTERACTION_PATTERN,
                perspective=MemoryPerspective.MODEL_INFERRED,
                payload={
                    "predicate": "engagement_increased",
                    "metric": "contact_frequency",
                    "current": "high",
                },
            )
        )


@pytest.mark.parametrize("kind", [MemoryKind.INTERACTION_EVENT, MemoryKind.INTERACTION_PATTERN])
@pytest.mark.parametrize(
    ("perspective", "source"),
    [
        (MemoryPerspective.MODEL_INFERRED, "user_reported"),
        (MemoryPerspective.USER_REPORTED, "model_inferred"),
    ],
)
def test_interaction_payload_source_cannot_conflict_with_claim_perspective(
    kind: MemoryKind,
    perspective: MemoryPerspective,
    source: str,
) -> None:
    payload: dict[str, object] = {
        "predicate": "engagement_increased",
        "source": source,
    }
    if kind == MemoryKind.INTERACTION_PATTERN:
        payload.update(
            {
                "metric": "contact_frequency",
                "current": "high",
                "evidence_ids": ["event-1", "event-2"],
            }
        )
    with pytest.raises(
        NormalizationContractError,
        match=r"INTERACTION_.*_PAYLOAD_INVALID",
    ):
        _normalize(
            _candidate(
                kind=kind,
                perspective=perspective,
                payload=payload,
            )
        )


def test_pattern_evidence_assessor_cannot_be_bypassed_by_conflicting_source() -> None:
    unnormalized = _candidate(
        kind=MemoryKind.INTERACTION_PATTERN,
        perspective=MemoryPerspective.MODEL_INFERRED,
        payload={
            "predicate": "engagement_increased",
            "metric": "contact_frequency",
            "current": "high",
            "source": "user_reported",
        },
    )

    links = assess_pattern_evidence_links(unnormalized, [])

    assert links.required is True
    assert links.valid is False
    assert links.reason == "model_inferred_pattern_missing_event_evidence"


def test_model_inferred_pattern_rejects_free_form_evidence_as_event_link() -> None:
    with pytest.raises(
        NormalizationContractError,
        match="INTERACTION_PATTERN_PAYLOAD_INVALID",
    ):
        _normalize(
            _candidate(
                kind=MemoryKind.INTERACTION_PATTERN,
                perspective=MemoryPerspective.MODEL_INFERRED,
                payload={
                    "predicate": "engagement_increased",
                    "metric": "contact_frequency",
                    "current": "high",
                    "evidence": ["她最近经常主动联系我"],
                },
            )
        )


def test_model_inferred_pattern_links_only_to_active_event_memories() -> None:
    events = [_event_item("event-1"), _event_item("event-2")]
    pattern = _normalize(
        _candidate(
            kind=MemoryKind.INTERACTION_PATTERN,
            perspective=MemoryPerspective.MODEL_INFERRED,
            payload={
                "predicate": "partner_initiative_increased",
                "metric": "initiation_balance",
                "current": "partner_to_user",
                "evidence_ids": [event.id for event in events],
                "time_window": ["2026-09-01", "2026-09-08"],
            },
        )
    )

    links = assess_pattern_evidence_links(pattern, events)
    admission = assess_memory_admission(
        pattern,
        pattern.original_text,
        corroborating_evidence_count=links.linked_event_count,
        pattern_evidence_links=links,
    )

    assert links.valid is True
    assert links.linked_event_ids == ("event-1", "event-2")
    assert admission.reason != "model_inferred_pattern_invalid_event_evidence"
    assert admission.score_breakdown["pattern_has_multiple_evidence"] is True


def test_model_inferred_pattern_rejects_semantically_unrelated_event_ids() -> None:
    matching = _event_item("event-1")
    unrelated = _event_item("event-2").model_copy(
        update={
            "raw_predicate": "had_meal",
            "custom_predicate": "had_meal",
            "payload": {
                "predicate": "had_meal",
                "participants": ["user", "partner"],
                "action": "had_meal",
            },
        }
    )
    pattern = _normalize(
        _candidate(
            kind=MemoryKind.INTERACTION_PATTERN,
            perspective=MemoryPerspective.MODEL_INFERRED,
            payload={
                "predicate": "partner_initiative_increased",
                "metric": "initiation_balance",
                "current": "partner_to_user",
                "participants": ["user", "partner"],
                "evidence_ids": [matching.id, unrelated.id],
                "time_window": ["2026-09-01", "2026-09-08"],
            },
        )
    )

    links = assess_pattern_evidence_links(pattern, [matching, unrelated])

    assert links.valid is False
    assert links.reason == "model_inferred_pattern_incompatible_event_evidence"
    assert links.linked_event_ids == (matching.id,)
    assert links.rejected_evidence_ids == (unrelated.id,)


def test_model_inferred_pattern_rejects_event_outside_declared_window() -> None:
    matching = _event_item("event-1")
    historical = _event_item("event-2").model_copy(
        update={"occurred_at": datetime(2026, 8, 1, tzinfo=UTC)}
    )
    pattern = _normalize(
        _candidate(
            kind=MemoryKind.INTERACTION_PATTERN,
            perspective=MemoryPerspective.MODEL_INFERRED,
            payload={
                "predicate": "partner_initiative_increased",
                "metric": "initiation_balance",
                "current": "partner_to_user",
                "evidence_ids": [matching.id, historical.id],
                "time_window": ["2026-09-01", "2026-09-08"],
            },
        )
    )

    links = assess_pattern_evidence_links(pattern, [matching, historical])

    assert links.valid is False
    assert links.reason == "model_inferred_pattern_incompatible_event_evidence"
    assert links.rejected_evidence_ids == (historical.id,)


def test_model_inferred_pattern_with_unknown_event_id_is_rejected_at_admission() -> None:
    event = _event_item("event-1")
    pattern = _normalize(
        _candidate(
            kind=MemoryKind.INTERACTION_PATTERN,
            perspective=MemoryPerspective.MODEL_INFERRED,
            payload={
                "predicate": "partner_initiative_increased",
                "metric": "initiation_balance",
                "current": "partner_to_user",
                "evidence_ids": [event.id, "invented-event"],
                "time_window": ["2026-09-01", "2026-09-08"],
            },
        )
    )

    links = assess_pattern_evidence_links(pattern, [event])
    admission = assess_memory_admission(
        pattern,
        pattern.original_text,
        pattern_evidence_links=links,
    )

    assert links.valid is False
    assert links.rejected_evidence_ids == ("invented-event",)
    assert admission.decision.value == "reject"
    assert admission.reason == "model_inferred_pattern_invalid_event_evidence"


def test_pattern_time_window_normalizes_and_rejects_reverse_range() -> None:
    pattern = _normalize(
        _candidate(
            kind=MemoryKind.INTERACTION_PATTERN,
            payload={
                "predicate": "engagement_increased",
                "metric": "contact_frequency",
                "current": "high",
                "time_window": ["2026-09-01", "2026-09-08"],
            },
        )
    )
    assert pattern.payload["time_window"] == {
        "start": "2026-09-01",
        "end": "2026-09-08",
    }

    with pytest.raises(
        NormalizationContractError,
        match="INTERACTION_PATTERN_PAYLOAD_INVALID",
    ):
        _normalize(
            _candidate(
                kind=MemoryKind.INTERACTION_PATTERN,
                payload={
                    "predicate": "engagement_increased",
                    "metric": "contact_frequency",
                    "current": "high",
                    "time_window": {
                        "start": "2026-09-08",
                        "end": "2026-09-01",
                    },
                },
            )
        )


def test_pattern_time_window_from_to_aliases_are_canonicalized() -> None:
    pattern = _normalize(
        _candidate(
            kind=MemoryKind.INTERACTION_PATTERN,
            payload={
                "predicate": "engagement_increased",
                "metric": "contact_frequency",
                "current": "high",
                "time_window": {
                    "from": "2026-09-01",
                    "to": "2026-09-08",
                },
            },
        )
    )

    assert pattern.payload["time_window"] == {
        "start": "2026-09-01",
        "end": "2026-09-08",
    }


def test_event_and_pattern_keep_distinct_existing_roles() -> None:
    event = normalize_memory_candidate(
        _candidate(
            kind=MemoryKind.INTERACTION_EVENT,
            payload={"predicate": "partner_replied"},
        ),
        REFERENCE_TIME,
    )
    pattern = normalize_memory_candidate(
        _candidate(
            kind=MemoryKind.INTERACTION_PATTERN,
            payload={
                "predicate": "engagement_increased",
                "metric": "contact_frequency",
                "current": "high",
            },
        ),
        REFERENCE_TIME,
    )

    assert memory_role(event) == MemoryRole.RECENT_EVENT
    assert memory_role(pattern) == MemoryRole.INTERACTION_PATTERN

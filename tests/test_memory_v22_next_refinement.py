from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore, SQLiteMemoryStore
from loveapp.application.memory import MemoryService
from loveapp.application.memory_pattern_consolidation import (
    detect_interaction_pattern_consolidation,
    govern_interaction_pattern_evolution,
)
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    ClaimRelation,
    EvidenceExplicitness,
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    MessageRole,
    PatternLifecycleState,
    PredicateType,
    RelationshipImpact,
    TimeKind,
    memory_dedupe_key,
)
from loveapp.domain.memory_context import select_context_memories
from loveapp.domain.memory_normalization import (
    NormalizationContractError,
    normalize_memory_candidate_contract,
)
from loveapp.domain.memory_salience import apply_event_salience, assess_event_salience
from loveapp.domain.memory_write import MemoryWriteBatch

NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
USER_ID = "v22-next-user"
RELATIONSHIP_ID = "v22-next-relationship"


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


def _event_candidate(
    index: int,
    *,
    action: str = "partner_initiated_chat",
    text: str | None = None,
    subject: str = "partner",
    occurred_at: datetime | None = None,
    confidence: float = 0.95,
    relationship_impact: RelationshipImpact = RelationshipImpact.UNCLEAR,
    intensity: int | None = None,
) -> MemoryCandidate:
    event_text = text or f"Event {index}: {action}."
    return _normalize(
        MemoryCandidate(
            kind=MemoryKind.INTERACTION_EVENT,
            subject=subject,
            summary=event_text,
            original_text=event_text,
            evidence_spans=[event_text],
            time_kind=TimeKind.POINT,
            occurred_at=occurred_at or NOW - timedelta(days=12 - index),
            payload={
                "predicate": action,
                "action": action,
                "participants": ["user", "partner"],
            },
            raw_predicate=action,
            predicate_type=PredicateType.CUSTOM,
            confidence=confidence,
            explicitness=EvidenceExplicitness.EXPLICIT,
            relationship_impact=relationship_impact,
            intensity=intensity,
        )
    )


def _event_claim(
    index: int,
    *,
    action: str,
    text: str,
    occurred_at: datetime,
) -> AtomicClaim:
    return AtomicClaim(
        claim_id=f"claim-{index}",
        kind=MemoryKind.INTERACTION_EVENT,
        subject="partner",
        predicate=action,
        summary=text,
        evidence_spans=[text],
        time_kind=TimeKind.POINT,
        occurred_at=occurred_at,
        payload={
            "action": action,
            "participants": ["user", "partner"],
        },
        raw_predicate=action,
        predicate_type=PredicateType.CUSTOM,
        confidence=0.95,
        explicitness=EvidenceExplicitness.EXPLICIT,
    )


def _item(
    candidate: MemoryCandidate,
    memory_id: str,
    *,
    status: MemoryStatus = MemoryStatus.CONFIRMED,
    source_message_id: str | None = None,
) -> MemoryItem:
    timestamp = candidate.occurred_at or candidate.period_end or NOW
    return MemoryItem(
        **candidate.model_dump(),
        id=memory_id,
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        status=status,
        source_message_id=source_message_id or f"message-{memory_id}",
        created_at=timestamp,
        updated_at=timestamp,
        dedupe_key=memory_dedupe_key(candidate),
    )


def _events(actions: list[str], *, first_index: int = 1) -> list[MemoryItem]:
    return [
        _item(
            _event_candidate(index, action=action),
            f"event-{index}",
        )
        for index, action in enumerate(actions, start=first_index)
    ]


def _inferred_initiative_pattern(
    *,
    status: MemoryStatus = MemoryStatus.PROPOSED,
) -> tuple[MemoryItem, list[MemoryItem]]:
    events = _events(
        [
            "partner_initiated_chat",
            "partner_invited_user",
            "partner_shared_work",
        ]
    )
    candidate = detect_interaction_pattern_consolidation(
        events[-1],
        events,
        reference_time=NOW,
    )
    assert candidate is not None
    return _item(_normalize(candidate), "pattern-initiative", status=status), events


def test_user_reported_long_term_pattern_remains_a_pattern() -> None:
    text = "她最近一个月基本都不主动找我了。"
    candidate = _normalize(
        MemoryCandidate(
            kind=MemoryKind.INTERACTION_PATTERN,
            subject="relationship",
            summary="对方最近一个月很少主动联系用户。",
            original_text=text,
            evidence_spans=[text],
            time_kind=TimeKind.INTERVAL,
            period_start=NOW - timedelta(days=30),
            period_end=NOW,
            payload={
                "metric": "initiation_balance",
                "direction": "decreasing",
                "time_window": {"label": "最近一个月"},
                "source": "user_reported",
            },
            raw_predicate="partner_contact_initiation_decreased",
            predicate_type=PredicateType.CUSTOM,
            perspective=MemoryPerspective.USER_REPORTED,
            confidence=0.9,
            explicitness=EvidenceExplicitness.EXPLICIT,
        )
    )

    assert candidate.kind == MemoryKind.INTERACTION_PATTERN
    assert candidate.perspective == MemoryPerspective.USER_REPORTED
    assert candidate.payload["source"] == "user_reported"
    assert candidate.pattern_state == PatternLifecycleState.ACTIVE


def test_first_interaction_has_higher_salience_than_ordinary_interaction() -> None:
    first = _event_candidate(
        1,
        action="shared_meal",
        text="昨天是我们第一次见面，一起吃了饭。",
    )
    ordinary = _event_candidate(
        2,
        action="shared_meal",
        text="昨天我们一起吃了饭。",
    )

    first_assessment = assess_event_salience(first, [])
    ordinary_assessment = assess_event_salience(ordinary, [])

    assert first_assessment.reason == "first_interaction"
    assert first_assessment.score >= 0.7
    assert first_assessment.score > ordinary_assessment.score
    assert ordinary_assessment.reason == "ordinary_interaction"


def test_conflict_event_receives_high_bounded_salience() -> None:
    event = _event_candidate(
        1,
        action="argument",
        text="昨天我们因为钱怎么花吵了一架。",
        relationship_impact=RelationshipImpact.DAMAGING,
        intensity=4,
    )

    assessed = apply_event_salience(event, [])

    assert assessed.salience is not None and assessed.salience >= 0.82
    assert assessed.importance_reason == "relationship_conflict"
    assert assessed.payload["salience_source"] == "deterministic_contextual_assessment"


def test_repeated_mention_increases_user_attention_salience() -> None:
    original = _event_candidate(
        1,
        action="shared_meal",
        text="昨天我们一起吃了饭。",
        occurred_at=NOW - timedelta(days=1),
    )
    repeated = _event_candidate(
        2,
        action="shared_meal",
        text="我又提到昨天一起吃饭那件事。",
        occurred_at=NOW - timedelta(days=1),
    )

    assessment = assess_event_salience(repeated, [_item(original, "meal-event")])

    assert assessment.reason == "repeated_user_attention"
    assert assessment.user_attention == 0.95
    assert assessment.score >= 0.68


def test_pure_psychological_pattern_fails_closed() -> None:
    text = "最近一个月她越来越喜欢我。"
    candidate = MemoryCandidate(
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="partner",
        summary="对方越来越喜欢用户。",
        original_text=text,
        evidence_spans=[text],
        time_kind=TimeKind.INTERVAL,
        period_start=NOW - timedelta(days=30),
        period_end=NOW,
        payload={
            "metric": "emotional_disclosure",
            "direction": "increasing",
            "time_window": {"label": "最近一个月"},
            "source": "user_reported",
        },
        raw_predicate="partner_liking_increased",
        predicate_type=PredicateType.CUSTOM,
        confidence=0.9,
        explicitness=EvidenceExplicitness.EXPLICIT,
    )

    with pytest.raises(
        NormalizationContractError,
        match="INTERACTION_PATTERN_NOT_OBSERVATIONAL",
    ):
        _normalize(candidate)


def test_mixed_psychological_claim_projects_only_observable_trend() -> None:
    text = "最近她越来越喜欢我，也越来越主动联系我。"
    candidate = _normalize(
        MemoryCandidate(
            kind=MemoryKind.INTERACTION_PATTERN,
            subject="relationship",
            summary="对方越来越喜欢用户并更主动联系。",
            original_text=text,
            evidence_spans=[text],
            payload={
                "metric": "emotional_disclosure",
                "direction": "increasing",
                "time_window": {"label": "最近"},
                "source": "user_reported",
            },
            raw_predicate="partner_liking_increased",
            predicate_type=PredicateType.CUSTOM,
            confidence=0.9,
            explicitness=EvidenceExplicitness.EXPLICIT,
        )
    )

    assert candidate.canonical_predicate == "interaction.initiation_balance"
    assert candidate.payload["metric"] == "initiation_balance"
    assert candidate.payload["observational_projection"].startswith(
        "psychological_conclusion_to_observed"
    )
    assert "喜欢" not in candidate.summary


@pytest.mark.parametrize(
    ("actions", "predicate", "state", "category"),
    [
        (
            ["partner_initiated_chat", "partner_invited_user", "partner_shared_work"],
            "interaction.initiation_balance",
            "partner_to_user",
            "interaction_initiative",
        ),
        (
            ["shared_chat", "shared_call", "shared_meeting"],
            "interaction.contact_frequency",
            "high",
            "communication_frequency",
        ),
        (
            ["argument", "conflict_event", "fight"],
            "interaction.conflict_frequency",
            "high",
            "conflict_trend",
        ),
    ],
)
def test_only_supported_latent_factors_consolidate_across_compatible_events(
    actions: list[str],
    predicate: str,
    state: str,
    category: str,
) -> None:
    events = _events(actions)

    candidate = detect_interaction_pattern_consolidation(
        events[-1],
        events,
        reference_time=NOW,
    )

    assert candidate is not None
    assert candidate.canonical_predicate == predicate
    assert candidate.state_value == state
    assert candidate.payload["consolidation_category"] == category
    assert candidate.payload["positive_evidence_ids"] == [item.id for item in events]


def test_unsupported_events_do_not_auto_discover_a_pattern() -> None:
    events = _events(["shared_meal", "watched_movie", "walked_home"])

    assert (
        detect_interaction_pattern_consolidation(
            events[-1],
            events,
            reference_time=NOW,
        )
        is None
    )


def test_positive_event_extends_inferred_pattern_evidence() -> None:
    pattern, evidence = _inferred_initiative_pattern()
    trigger = _item(
        _event_candidate(4, action="partner_shared_personal_update"),
        "event-4",
    )

    evolution = govern_interaction_pattern_evolution(
        trigger,
        [pattern, *evidence, trigger],
        source_text=trigger.original_text,
        reference_time=NOW,
    )

    assert evolution.status == "positive_evidence"
    assert len(evolution.operations) == 1
    update = evolution.operations[0].candidate
    assert update.pattern_state == PatternLifecycleState.ACTIVE
    assert trigger.id in update.positive_evidence_ids
    assert update.confidence > pattern.confidence


def test_first_opposite_event_weakens_pattern_without_replacing_it() -> None:
    pattern, evidence = _inferred_initiative_pattern()
    trigger = _item(
        _event_candidate(4, action="user_initiated_chat", subject="user"),
        "event-4",
    )

    evolution = govern_interaction_pattern_evolution(
        trigger,
        [pattern, *evidence, trigger],
        source_text=trigger.original_text,
        reference_time=NOW,
    )

    assert evolution.status == "negative_evidence"
    assert len(evolution.operations) == 1
    update = evolution.operations[0].candidate
    assert update.pattern_state == PatternLifecycleState.WEAKENING
    assert update.negative_evidence_ids == [trigger.id]
    assert update.confidence < pattern.confidence
    assert evolution.operations[0].relation == ClaimRelation.SAME


def test_two_distinct_opposite_events_propose_reversal_and_supersession() -> None:
    pattern, positive = _inferred_initiative_pattern()
    first_negative = _item(
        _event_candidate(4, action="user_initiated_chat", subject="user"),
        "event-4",
    )
    first = govern_interaction_pattern_evolution(
        first_negative,
        [pattern, *positive, first_negative],
        source_text=first_negative.original_text,
        reference_time=NOW,
    )
    weakened = _item(
        first.operations[0].candidate,
        pattern.id,
        status=MemoryStatus.PROPOSED,
        source_message_id=pattern.source_message_id,
    )
    second_negative = _item(
        _event_candidate(5, action="user_contacted_partner", subject="user"),
        "event-5",
    )

    second = govern_interaction_pattern_evolution(
        second_negative,
        [weakened, *positive, first_negative, second_negative],
        source_text=second_negative.original_text,
        reference_time=NOW,
    )

    assert second.status == "superseded_by_reversal"
    assert len(second.operations) == 2
    reversal = second.operations[-1]
    assert reversal.relation == ClaimRelation.UPDATE
    assert reversal.target_memory_ids == [pattern.id]
    assert reversal.candidate.state_value == "user_to_partner"
    assert reversal.candidate.pattern_state == PatternLifecycleState.ACTIVE


def test_confirmed_pattern_is_not_closed_by_proposed_reversal() -> None:
    pattern, positive = _inferred_initiative_pattern(status=MemoryStatus.CONFIRMED)
    first_negative = _item(
        _event_candidate(4, action="user_initiated_chat", subject="user"),
        "event-4",
    )
    first = govern_interaction_pattern_evolution(
        first_negative,
        [pattern, *positive, first_negative],
        source_text=first_negative.original_text,
        reference_time=NOW,
    )
    weakened = _item(
        first.operations[0].candidate,
        pattern.id,
        status=MemoryStatus.CONFIRMED,
        source_message_id=pattern.source_message_id,
    )
    second_negative = _item(
        _event_candidate(5, action="user_contacted_partner", subject="user"),
        "event-5",
    )

    second = govern_interaction_pattern_evolution(
        second_negative,
        [weakened, *positive, first_negative, second_negative],
        source_text=second_negative.original_text,
        reference_time=NOW,
    )

    reversal = second.operations[-1]
    assert second.status == "reversal_proposed_confirmed_protected"
    assert reversal.relation == ClaimRelation.CONTRADICTION
    assert reversal.target_memory_ids == []


def test_pattern_evolution_fails_closed_for_ambiguous_targets() -> None:
    pattern, evidence = _inferred_initiative_pattern()
    duplicate = pattern.model_copy(
        update={
            "id": "pattern-initiative-duplicate",
            "dedupe_key": f"{pattern.dedupe_key}-legacy-duplicate",
        }
    )
    trigger = _item(
        _event_candidate(4, action="partner_shared_personal_update"),
        "event-4",
    )

    evolution = govern_interaction_pattern_evolution(
        trigger,
        [pattern, duplicate, *evidence, trigger],
        source_text=trigger.original_text,
        reference_time=NOW,
    )

    assert evolution.handled is True
    assert evolution.status == "ambiguous_pattern_target"
    assert evolution.operations == ()
    assert len(evolution.audits) == 1
    assert set(evolution.pattern_memory_ids) == {pattern.id, duplicate.id}


def test_user_reported_pattern_is_not_destructively_evolved_by_inferred_events() -> None:
    inferred, evidence = _inferred_initiative_pattern()
    user_reported = inferred.model_copy(
        update={
            "id": "pattern-user-reported",
            "perspective": MemoryPerspective.USER_REPORTED,
            "payload": {**inferred.payload, "source": "user_reported"},
            "dedupe_key": f"{inferred.dedupe_key}-user-reported",
        }
    )
    trigger = _item(
        _event_candidate(4, action="user_initiated_chat", subject="user"),
        "event-4",
    )

    evolution = govern_interaction_pattern_evolution(
        trigger,
        [user_reported, *evidence, trigger],
        source_text=trigger.original_text,
        reference_time=NOW,
    )

    assert evolution.handled is False
    assert evolution.operations == ()


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_repeated_opposite_evidence_supersedes_only_proposed_pattern(
    backend: str,
    tmp_path: Path,
) -> None:
    store = (
        InMemoryMemoryStore(clock=lambda: NOW)
        if backend == "memory"
        else SQLiteMemoryStore(tmp_path / "v22-next-evolution.db", clock=lambda: NOW)
    )
    positive_candidates = [
        _event_candidate(1, action="partner_initiated_chat"),
        _event_candidate(2, action="partner_invited_user"),
        _event_candidate(3, action="partner_shared_work"),
    ]
    positive = [
        (
            await store.save_memory(
                user_id=USER_ID,
                relationship_id=RELATIONSHIP_ID,
                candidate=candidate,
                status=MemoryStatus.CONFIRMED,
            )
        ).item
        for index, candidate in enumerate(positive_candidates, start=1)
    ]
    pattern_candidate = detect_interaction_pattern_consolidation(
        positive[-1],
        positive,
        reference_time=NOW,
    )
    assert pattern_candidate is not None
    old_pattern = (
        await store.save_memory(
            user_id=USER_ID,
            relationship_id=RELATIONSHIP_ID,
            candidate=_normalize(pattern_candidate),
            status=MemoryStatus.PROPOSED,
        )
    ).item

    for index, action in ((4, "user_initiated_chat"), (5, "user_contacted_partner")):
        trigger = (
            await store.save_memory(
                user_id=USER_ID,
                relationship_id=RELATIONSHIP_ID,
                candidate=_event_candidate(index, action=action, subject="user"),
                status=MemoryStatus.CONFIRMED,
            )
        ).item
        active = await store.list_memories(
            user_id=USER_ID,
            relationship_id=RELATIONSHIP_ID,
            limit=50,
        )
        evolution = govern_interaction_pattern_evolution(
            trigger,
            active,
            source_text=trigger.original_text,
            reference_time=NOW,
        )
        await store.commit_memory_batch(
            user_id=USER_ID,
            relationship_id=RELATIONSHIP_ID,
            batch=MemoryWriteBatch(
                operations=list(evolution.operations),
                audit_only=list(evolution.audits),
            ),
        )

    memories = await store.list_memories(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        limit=50,
    )
    patterns = [item for item in memories if item.kind == MemoryKind.INTERACTION_PATTERN]
    historical = next(item for item in patterns if item.id == old_pattern.id)
    current = next(item for item in patterns if item.id != old_pattern.id)

    assert historical.status == MemoryStatus.SUPERSEDED
    assert historical.pattern_state == PatternLifecycleState.SUPERSEDED
    assert current.status == MemoryStatus.PROPOSED
    assert current.pattern_state == PatternLifecycleState.ACTIVE
    assert current.state_value == "user_to_partner"
    assert current.supersedes_id == historical.id
    context_ids = {
        item.id
        for item in select_context_memories(
            memories,
            query="最近通常是谁主动联系？",
            limit=20,
            reference_time=NOW,
        )
    }
    assert historical.id not in context_ids
    assert current.id in context_ids


async def test_replaying_same_source_message_does_not_repeat_consolidation() -> None:
    texts = [
        "Remember: last Monday partner initiated chat.",
        "Remember: yesterday partner invited me.",
        "Remember: today partner shared a personal update.",
    ]
    claims = [
        _event_claim(
            1,
            action="partner_initiated_chat",
            text=texts[0],
            occurred_at=NOW - timedelta(days=7),
        ),
        _event_claim(
            2,
            action="partner_invited_user",
            text=texts[1],
            occurred_at=NOW - timedelta(days=1),
        ),
        _event_claim(
            3,
            action="partner_shared_personal_update",
            text=texts[2],
            occurred_at=NOW,
        ),
        _event_claim(
            3,
            action="partner_shared_personal_update",
            text=texts[2],
            occurred_at=NOW,
        ),
    ]
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(store, _SequenceExtractor(claims), clock=lambda: NOW)

    for text in texts[:2]:
        await service.remember_text(
            user_id=USER_ID,
            relationship_id=RELATIONSHIP_ID,
            conversation_id="v22-replay-conversation",
            text=text,
            status=MemoryStatus.CONFIRMED,
        )
    message = await store.add_message(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        conversation_id="v22-replay-conversation",
        role=MessageRole.USER,
        content=texts[2],
        message_id="v22-replayed-message",
    )
    first = await service.remember_recorded_message(
        message=message,
        text=texts[2],
        status=MemoryStatus.CONFIRMED,
        conversation_history=[],
    )
    second = await service.remember_recorded_message(
        message=message,
        text=texts[2],
        status=MemoryStatus.CONFIRMED,
        conversation_history=[],
    )
    memories = await store.list_memories(
        user_id=USER_ID,
        relationship_id=RELATIONSHIP_ID,
        limit=20,
    )
    events = [item for item in memories if item.kind == MemoryKind.INTERACTION_EVENT]
    patterns = [item for item in memories if item.kind == MemoryKind.INTERACTION_PATTERN]

    assert len(events) == 3
    assert len(patterns) == 1
    assert len(patterns[0].positive_evidence_ids) == 3
    assert first.saved[-1].item.id == patterns[0].id
    assert second.saved[0].created is False
    assert all(saved.item.kind != MemoryKind.INTERACTION_PATTERN for saved in second.saved)


async def test_sqlite_round_trips_event_salience_and_pattern_evolution_metadata(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "v22-next-metadata.db", clock=lambda: NOW)
    salient = apply_event_salience(
        _event_candidate(
            1,
            action="argument",
            text="昨天我们因为误会吵了一架。",
            relationship_impact=RelationshipImpact.DAMAGING,
        ),
        [],
    )
    event = (
        await store.save_memory(
            user_id=USER_ID,
            relationship_id=RELATIONSHIP_ID,
            candidate=salient,
            status=MemoryStatus.CONFIRMED,
        )
    ).item
    pattern = _normalize(
        MemoryCandidate(
            kind=MemoryKind.INTERACTION_PATTERN,
            subject="relationship",
            summary="双方近期冲突频率正在减弱。",
            original_text="近期冲突频率正在减弱。",
            evidence_spans=["近期冲突频率正在减弱"],
            time_kind=TimeKind.INTERVAL,
            period_start=NOW - timedelta(days=20),
            period_end=NOW,
            payload={
                "metric": "conflict_frequency",
                "current": "high",
                "source": "model_inferred",
                "evidence_ids": ["event-positive-1", "event-positive-2"],
                "positive_evidence_ids": ["event-positive-1", "event-positive-2"],
                "negative_evidence_ids": ["event-negative-1"],
                "state": "weakening",
                "time_window": {
                    "start": (NOW - timedelta(days=20)).isoformat(),
                    "end": NOW.isoformat(),
                },
            },
            raw_predicate="interaction.conflict_frequency",
            predicate_type=PredicateType.CANONICAL,
            canonical_predicate="interaction.conflict_frequency",
            perspective=MemoryPerspective.MODEL_INFERRED,
            confidence=0.82,
            explicitness=EvidenceExplicitness.STRONGLY_IMPLIED,
        )
    )
    stored_pattern = (
        await store.save_memory(
            user_id=USER_ID,
            relationship_id=RELATIONSHIP_ID,
            candidate=pattern,
            status=MemoryStatus.PROPOSED,
        )
    ).item

    loaded_event = await store.get_memory(event.id, USER_ID)
    loaded_pattern = await store.get_memory(stored_pattern.id, USER_ID)

    assert loaded_event is not None
    assert loaded_event.salience == salient.salience
    assert loaded_event.importance_reason == "relationship_conflict"
    assert loaded_pattern is not None
    assert loaded_pattern.pattern_state == PatternLifecycleState.WEAKENING
    assert loaded_pattern.positive_evidence_ids == ["event-positive-1", "event-positive-2"]
    assert loaded_pattern.negative_evidence_ids == ["event-negative-1"]

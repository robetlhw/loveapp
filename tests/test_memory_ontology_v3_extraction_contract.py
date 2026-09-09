from datetime import UTC, datetime, timedelta

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore, SQLiteMemoryStore
from loveapp.application.conflict_event_enrichment import (
    resolve_conflict_event_enrichment,
)
from loveapp.application.memory import MemoryService
from loveapp.core.timing import ExecutionTrace
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
    MessageRole,
    PredicateType,
    RelationshipImpact,
    StoredMessage,
    TimeKind,
    memory_dedupe_key,
)
from loveapp.domain.memory_dimensions import normalize_interaction_event_cause
from loveapp.domain.memory_event_enrichment import (
    ConflictEventEnrichment,
    apply_conflict_event_enrichment,
)
from loveapp.domain.memory_normalization import normalize_memory_candidate_contract
from loveapp.domain.memory_salience import apply_event_salience, assess_event_salience
from loveapp.domain.memory_write import MemoryWriteBatch
from loveapp.domain.runtime_context import PendingMemoryContext

NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
SCOPE = {
    "user_id": "ontology-v3-user",
    "relationship_id": "ontology-v3-relationship",
    "conversation_id": "ontology-v3-conversation",
}


def _normalize(candidate: MemoryCandidate) -> MemoryCandidate:
    return normalize_memory_candidate_contract(
        candidate,
        NOW,
        allow_legacy_open_world=True,
    )


def _candidate(
    text: str,
    *,
    kind: MemoryKind,
    raw_predicate: str,
    payload: dict[str, object],
    subject: str = "user",
    perspective: MemoryPerspective = MemoryPerspective.USER_REPORTED,
    occurred_at: datetime | None = None,
    relationship_impact: RelationshipImpact = RelationshipImpact.UNCLEAR,
) -> MemoryCandidate:
    return MemoryCandidate(
        kind=kind,
        subject=subject,
        summary=text,
        original_text=text,
        evidence_spans=[text],
        time_kind=TimeKind.POINT if occurred_at is not None else TimeKind.TIMELESS,
        occurred_at=occurred_at,
        relationship_impact=relationship_impact,
        perspective=perspective,
        confidence=0.95,
        explicitness=EvidenceExplicitness.EXPLICIT,
        raw_predicate=raw_predicate,
        predicate_type=PredicateType.CUSTOM,
        payload={"predicate": raw_predicate, **payload},
    )


def _item(
    candidate: MemoryCandidate,
    memory_id: str,
    *,
    source_message_id: str,
    status: MemoryStatus = MemoryStatus.CONFIRMED,
) -> MemoryItem:
    return MemoryItem(
        **candidate.model_dump(),
        id=memory_id,
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
        status=status,
        source_message_id=source_message_id,
        created_at=NOW,
        updated_at=NOW,
        dedupe_key=memory_dedupe_key(candidate),
    )


@pytest.mark.parametrize(
    ("text", "raw_predicate", "value", "expected"),
    [
        ("我现在住上海。", "residence", "上海", "profile.residence"),
        ("我的生日是5月3日。", "birthday", "5月3日", "profile.birthday"),
        ("我是程序员。", "occupation", "程序员", "profile.occupation"),
        ("我的身份认同是女性。", "identity", "女性", "profile.identity"),
        ("我的主要联系方式是微信。", "contact_method", "微信", "profile.contact_method"),
    ],
)
def test_reviewed_stable_facts_keep_canonical_profile_dimensions(
    text: str,
    raw_predicate: str,
    value: str,
    expected: str,
) -> None:
    normalized = _normalize(
        _candidate(
            text,
            kind=MemoryKind.STABLE_FACT,
            raw_predicate=raw_predicate,
            payload={"object": value},
        )
    )

    assert normalized.kind == MemoryKind.STABLE_FACT
    assert normalized.canonical_predicate == expected
    assert normalized.state_value == value.casefold()


@pytest.mark.parametrize(
    ("text", "raw_predicate", "value", "domain", "dimension", "canonical"),
    [
        ("我喜欢吃辣。", "food_preference", "辣", "food", "taste", "preference.food.spiciness"),
        ("我喜欢篮球。", "hobby", "篮球", "hobby", "activity", "preference.hobby.activity"),
        (
            "我对摄影很感兴趣。",
            "interest",
            "摄影",
            "interest",
            "topic",
            "preference.interest.topic",
        ),
        (
            "我喜欢晚上一个人听音乐。",
            "lifestyle",
            "晚上一个人听音乐",
            "lifestyle",
            "habit",
            "preference.lifestyle.habit",
        ),
        (
            "我希望两个人平时多交流。",
            "communication_preference",
            "多交流",
            "communication",
            "frequency",
            "preference.communication.frequency",
        ),
        (
            "我不喜欢吵架后一直冷处理。",
            "conflict_resolution",
            "吵架后一直冷处理",
            "relationship",
            "conflict_resolution",
            "preference.relationship.conflict_resolution",
        ),
        (
            "我希望被理解和陪伴。",
            "emotional_need",
            "被理解和陪伴",
            "emotional",
            "need",
            "preference.emotional.need",
        ),
    ],
)
def test_reviewed_preferences_cannot_drift_into_stable_fact(
    text: str,
    raw_predicate: str,
    value: str,
    domain: str,
    dimension: str,
    canonical: str,
) -> None:
    normalized = _normalize(
        _candidate(
            text,
            kind=MemoryKind.STABLE_FACT,
            raw_predicate=raw_predicate,
            payload={"object": value},
        )
    )

    assert normalized.kind == MemoryKind.PREFERENCE
    assert normalized.canonical_predicate == canonical
    assert normalized.payload["domain"] == domain
    assert normalized.payload["dimension"] == dimension
    assert normalized.payload["preference"]
    assert normalized.subject == "user"
    assert normalized.perspective == MemoryPerspective.USER_REPORTED


def test_romantic_interest_is_not_reclassified_as_a_preference() -> None:
    normalized = _normalize(
        _candidate(
            "我喜欢她。",
            kind=MemoryKind.STABLE_FACT,
            raw_predicate="has_romantic_interest_in",
            payload={"object": "partner"},
        )
    )

    assert normalized.kind == MemoryKind.STABLE_FACT
    assert normalized.payload.get("semantic_type") != "preference"


@pytest.mark.parametrize(
    ("text", "action", "expected_event_type"),
    [
        ("昨天她主动找我聊天。", "聊天", "conversation"),
        ("昨天我们一起吃饭，很开心。", "一起吃饭", "shared_activity"),
        ("昨天我们第一次单独出去吃饭。", "单独吃饭", "milestone"),
        ("昨天我们正式约会了。", "约会", "date"),
        ("昨天我们吵了一架。", "吵架", "conflict"),
        ("昨天我们说开后和好了。", "和好", "reconciliation"),
        ("昨天她送了我一个礼物。", "送礼物", "affection_expression"),
        ("昨天她在我压力很大的时候安慰了我。", "安慰", "support"),
    ],
)
def test_interaction_event_type_uses_reviewed_domain_ontology(
    text: str,
    action: str,
    expected_event_type: str,
) -> None:
    normalized = _normalize(
        _candidate(
            text,
            kind=MemoryKind.INTERACTION_EVENT,
            raw_predicate="interaction_occurred",
            subject="relationship",
            occurred_at=NOW - timedelta(days=1),
            payload={"action": action, "participants": ["user", "partner"]},
        )
    )

    assert normalized.kind == MemoryKind.INTERACTION_EVENT
    assert normalized.payload["event_type"] == expected_event_type
    assert normalized.payload["participants"] == ["user", "partner"]
    assert normalized.payload["time"]
    assert normalized.payload["source"] == "user_reported"


def test_conflict_event_metadata_aliases_normalize_without_new_store_schema() -> None:
    normalized = _normalize(
        _candidate(
            "昨天我们因为钱怎么花吵了一架，后来已经说开了。",
            kind=MemoryKind.INTERACTION_EVENT,
            raw_predicate="argument",
            subject="relationship",
            occurred_at=NOW - timedelta(days=1),
            relationship_impact=RelationshipImpact.DAMAGING,
            payload={
                "event_category": "argument",
                "action": "吵架",
                "participants": ["user", "partner"],
                "conflict_cause": "因为钱怎么花",
                "severity": "moderate",
                "resolution": "已经说开",
            },
        )
    )

    assert normalized.payload["event_type"] == "conflict"
    assert normalized.payload["cause"] == {
        "category": "financial_values",
        "description": "因为钱怎么花",
    }
    assert normalized.payload["severity"] == "moderate"
    assert normalized.payload["resolution"] == "已经说开"


def test_conflict_cause_semantic_alias_normalizes_to_reviewed_category() -> None:
    normalized = normalize_interaction_event_cause(
        {
            "category": "insufficient_contact",
            "description": "partner reports too little contact",
        }
    )

    assert normalized == {
        "category": "communication_frequency",
        "description": "partner reports too little contact",
    }


def test_absent_store_history_does_not_manufacture_high_event_novelty() -> None:
    event = _normalize(
        _candidate(
            "昨天我们一起吃了一顿普通晚饭。",
            kind=MemoryKind.INTERACTION_EVENT,
            raw_predicate="shared_meal",
            subject="relationship",
            occurred_at=NOW - timedelta(days=1),
            payload={"action": "一起吃饭", "participants": ["user", "partner"]},
        )
    )

    assessed = apply_event_salience(event, [])

    assert assessed.novelty is None
    assert "novelty" not in assessed.payload
    assert assessed.payload["novelty_basis"] == "unknown_relationship_history"


def test_explicit_first_event_has_high_novelty_and_salience() -> None:
    event = _normalize(
        _candidate(
            "昨天我们第一次单独出去吃饭。",
            kind=MemoryKind.INTERACTION_EVENT,
            raw_predicate="first_private_meal",
            subject="relationship",
            occurred_at=NOW - timedelta(days=1),
            payload={"action": "单独吃饭", "participants": ["user", "partner"]},
        )
    )

    assessment = assess_event_salience(event, [])

    assert assessment.novelty == 1
    assert assessment.novelty_basis == "explicit_first"
    assert assessment.score >= 0.7


def test_recurring_conflict_event_has_low_novelty_but_high_salience() -> None:
    pattern = _normalize(
        _candidate(
            "最近我们经常吵架。",
            kind=MemoryKind.INTERACTION_PATTERN,
            raw_predicate="interaction.conflict_frequency",
            subject="relationship",
            payload={
                "metric": "conflict_frequency",
                "frequency": "frequent",
                "time_window": {"label": "最近"},
                "source": "user_reported",
            },
        )
    )
    pattern_item = _item(pattern, "conflict-pattern", source_message_id="pattern-source")
    event = _normalize(
        _candidate(
            "昨天我们又吵了一架。",
            kind=MemoryKind.INTERACTION_EVENT,
            raw_predicate="argument",
            subject="relationship",
            occurred_at=NOW - timedelta(days=1),
            relationship_impact=RelationshipImpact.DAMAGING,
            payload={"action": "吵架", "participants": ["user", "partner"]},
        )
    )

    assessment = assess_event_salience(event, [pattern_item])

    assert assessment.novelty <= 0.2
    assert assessment.novelty_basis in {
        "explicit_recurrence_signal",
        "known_recurring_pattern",
    }
    assert assessment.score >= 0.82


def _conflict_event(
    memory_id: str,
    *,
    source_message_id: str,
    subject: str = "relationship",
) -> MemoryItem:
    event = _normalize(
        _candidate(
            "昨天我们吵架了。",
            kind=MemoryKind.INTERACTION_EVENT,
            raw_predicate="argument",
            subject=subject,
            occurred_at=NOW - timedelta(days=1),
            payload={
                "event_type": "conflict",
                "action": "吵架",
                "participants": ["user", "partner"],
            },
        )
    )
    return _item(event, memory_id, source_message_id=source_message_id)


def _cause_candidate(*, subject: str = "relationship") -> MemoryCandidate:
    return _normalize(
        _candidate(
            "她觉得我最近联系她太少。",
            kind=MemoryKind.INTERACTION_EVENT,
            raw_predicate="conflict_cause",
            subject=subject,
            payload={
                "event_type": "conflict",
                "cause": {"description": "她觉得我最近联系她太少"},
            },
        )
    )


def _source_message(message_id: str) -> StoredMessage:
    return StoredMessage(
        id=message_id,
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
        conversation_id=SCOPE["conversation_id"],
        role=MessageRole.USER,
        content="昨天我们吵架了。",
        created_at=NOW - timedelta(minutes=2),
    )


def _pending_conflict_cause() -> PendingMemoryContext:
    return PendingMemoryContext(
        previous_assistant_question="你们为什么吵架？",
        expected_slot="cause",
        topic="conflict",
        created_turn="test-turn",
    )


def test_conflict_event_enrichment_requires_one_source_linked_target() -> None:
    source = _source_message("conflict-source")
    target = _conflict_event("conflict-event", source_message_id=source.id)

    resolution = resolve_conflict_event_enrichment(
        _cause_candidate(),
        current_text="她觉得我最近联系她太少。",
        conversation_history=[source],
        existing_memories=[target],
        pending_memory_context=_pending_conflict_cause(),
    )

    assert resolution.resolved is True
    assert resolution.target is not None and resolution.target.id == target.id
    assert resolution.cause_category == "communication_frequency"
    assert resolution.reason == "unique_source_linked_conflict_event"


def test_conflict_event_enrichment_fails_closed_for_multiple_source_targets() -> None:
    source = _source_message("conflict-source")
    targets = [
        _conflict_event("conflict-event-1", source_message_id=source.id),
        _conflict_event("conflict-event-2", source_message_id=source.id),
    ]

    resolution = resolve_conflict_event_enrichment(
        _cause_candidate(),
        current_text="她觉得我最近联系她太少。",
        conversation_history=[source],
        existing_memories=targets,
        pending_memory_context=_pending_conflict_cause(),
    )

    assert resolution.resolved is False
    assert resolution.reason == "ambiguous_source_linked_conflict_event"
    assert set(resolution.semantic_candidate_ids) == {item.id for item in targets}


def test_conflict_event_enrichment_fails_closed_for_subject_mismatch() -> None:
    source = _source_message("conflict-source")
    target = _conflict_event("conflict-event", source_message_id=source.id)

    resolution = resolve_conflict_event_enrichment(
        _cause_candidate(subject="partner"),
        current_text="她觉得我最近联系她太少。",
        conversation_history=[source],
        existing_memories=[target],
        pending_memory_context=_pending_conflict_cause(),
    )

    assert resolution.resolved is False
    assert resolution.reason == "conflict_event_subject_mismatch"


def test_conflict_event_enrichment_revalidates_source_link_at_apply_boundary() -> None:
    target = _conflict_event(
        "conflict-event",
        source_message_id="actual-conflict-source",
    )
    enrichment = ConflictEventEnrichment(
        target_memory_id=target.id,
        antecedent_message_id="different-source",
        evidence_span="她觉得我最近联系她太少。",
        cause_category="communication_frequency",
        reason="unique_source_linked_conflict_event",
    )

    with pytest.raises(ValueError, match="antecedent does not match"):
        apply_conflict_event_enrichment(
            target,
            enrichment,
            source_message_id="cause-source",
            updated_at=NOW,
        )


def test_conflict_event_enrichment_replay_is_idempotent() -> None:
    target = _conflict_event(
        "conflict-event",
        source_message_id="conflict-source",
    )
    enrichment = ConflictEventEnrichment(
        target_memory_id=target.id,
        antecedent_message_id="conflict-source",
        evidence_span="她觉得我最近联系她太少。",
        cause_category="communication_frequency",
        cause_description="她觉得我最近联系她太少",
        reason="unique_source_linked_conflict_event",
    )

    first = apply_conflict_event_enrichment(
        target,
        enrichment,
        source_message_id="cause-source",
        updated_at=NOW,
    )
    replay = apply_conflict_event_enrichment(
        first,
        enrichment,
        source_message_id="cause-source",
        updated_at=NOW,
    )

    assert replay.payload["enrichment_history"] == first.payload["enrichment_history"]
    assert replay.evidence_spans == first.evidence_spans


class _SequenceExtractor:
    def __init__(self, extractions: list[AtomicExtraction]) -> None:
        self._extractions = iter(extractions)

    async def extract(self, text: str, **_: object) -> AtomicExtraction:
        return next(self._extractions)


def _event_extraction(
    text: str,
    *,
    cause: dict[str, str] | None = None,
) -> AtomicExtraction:
    payload: dict[str, object] = {
        "event_type": "conflict",
        "action": "吵架",
        "participants": ["user", "partner"],
    }
    if cause is not None:
        payload["cause"] = cause
    return AtomicExtraction(
        claims=[
            AtomicClaim(
                claim_id=f"claim-{len(text)}",
                kind=MemoryKind.INTERACTION_EVENT,
                subject="relationship",
                predicate="conflict_event",
                summary=text,
                evidence_spans=[text],
                time_kind=TimeKind.POINT if cause is None else TimeKind.UNKNOWN,
                occurred_at=NOW - timedelta(days=1) if cause is None else None,
                payload=payload,
                confidence=0.95,
                explicitness=EvidenceExplicitness.EXPLICIT,
            )
        ]
    )


@pytest.mark.asyncio
async def test_memory_service_enriches_conflict_event_without_creating_second_event() -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    extractor = _SequenceExtractor(
        [
            _event_extraction("昨天我们吵架了。"),
            _event_extraction(
                "她觉得我最近联系她太少。",
                cause={"description": "她觉得我最近联系她太少"},
            ),
        ]
    )
    service = MemoryService(store, extractor, clock=lambda: NOW)
    first = await service.remember_text(text="昨天我们吵架了。", **SCOPE)
    target_id = first.saved[0].item.id
    await service.record_message(
        role=MessageRole.ASSISTANT,
        content="你们为什么吵架？",
        **SCOPE,
    )
    trace = ExecutionTrace()

    second = await service.remember_text(
        text="她觉得我最近联系她太少。",
        trace=trace,
        **SCOPE,
    )

    memories = await store.list_memories(
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
        limit=20,
    )
    assert len(memories) == 1
    assert second.saved == []
    assert second.contextual_updated_memory_ids == [target_id]
    assert memories[0].payload["cause"] == {
        "category": "communication_frequency",
        "description": "她觉得我最近联系她太少",
    }
    assert memories[0].evidence_spans[-1] == "她觉得我最近联系她太少。"
    enrichment_trace = next(
        record
        for record in trace.snapshot()
        if record.name == "memory_conflict_event_enrichment"
    )
    assert enrichment_trace.details["resolution_status"] == "resolved"
    assert enrichment_trace.details["selected_target_memory_id"] == target_id
    audits = await store.list_transition_audits(
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
    )
    assert any(
        audit.rule_name == "enrich_conflict_event_cause"
        and audit.target_memory_ids == [target_id]
        for audit in audits
    )


@pytest.mark.asyncio
async def test_sqlite_batch_round_trips_conflict_event_enrichment(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db", clock=lambda: NOW)
    source = await store.add_message(
        role=MessageRole.USER,
        content="昨天我们吵架了。",
        **SCOPE,
    )
    target = await store.save_memory(
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
        source_message_id=source.id,
        status=MemoryStatus.CONFIRMED,
        candidate=_conflict_event(
            "temporary",
            source_message_id=source.id,
        ),
    )
    cause_source = await store.add_message(
        role=MessageRole.USER,
        content="她觉得我最近联系她太少。",
        **SCOPE,
    )

    committed = await store.commit_memory_batch(
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
        batch=MemoryWriteBatch(
            source_message_id=cause_source.id,
            conflict_event_enrichments=[
                ConflictEventEnrichment(
                    target_memory_id=target.item.id,
                    antecedent_message_id=source.id,
                    evidence_span="她觉得我最近联系她太少。",
                    cause_category="communication_frequency",
                    cause_description="她觉得我最近联系她太少",
                    reason="unique_source_linked_conflict_event",
                )
            ],
        ),
    )

    loaded = await store.get_memory(target.item.id, SCOPE["user_id"])
    assert committed.updated_memory_ids == [target.item.id]
    assert loaded is not None
    assert loaded.payload["cause"]["category"] == "communication_frequency"
    assert any(audit.rule_name == "enrich_conflict_event_cause" for audit in committed.audits)


def test_pattern_provenance_keeps_user_reported_and_derived_contracts_distinct() -> None:
    user_reported = _normalize(
        _candidate(
            "她最近一个月基本每天都会主动找我聊天。",
            kind=MemoryKind.INTERACTION_PATTERN,
            raw_predicate="partner_initiated_chat",
            subject="relationship",
            payload={
                "metric": "initiation_balance",
                "current": "partner_to_user",
                "frequency": "daily",
                "time_window": {"label": "最近一个月"},
                "source": "user_reported",
            },
        )
    )
    derived = _normalize(
        _candidate(
            "模型从多个事件归纳出对方更常主动联系。",
            kind=MemoryKind.INTERACTION_PATTERN,
            raw_predicate="partner_initiated_chat",
            subject="relationship",
            perspective=MemoryPerspective.MODEL_INFERRED,
            payload={
                "metric": "initiation_balance",
                "current": "partner_to_user",
                "time_window": {"label": "最近一个月"},
                "source": "derived_from_events",
                "evidence_ids": ["event-1", "event-2"],
            },
        )
    )

    assert user_reported.payload["source"] == "user_reported"
    assert user_reported.payload.get("evidence_ids") is None
    assert derived.payload["source"] == "derived_from_events"
    assert derived.payload["evidence_ids"] == ["event-1", "event-2"]


def test_user_belief_cannot_leak_into_objective_profile_fact_semantics() -> None:
    normalized = _normalize(
        MemoryCandidate(
            kind=MemoryKind.STABLE_FACT,
            subject="partner",
            summary="用户觉得对方可能没以前喜欢自己。",
            original_text="我觉得她可能没以前喜欢我。",
            evidence_spans=["我觉得她可能没以前喜欢我。"],
            perspective=MemoryPerspective.USER_BELIEF,
            canonical_predicate="profile.identity",
            predicate_type=PredicateType.CANONICAL,
            raw_predicate="profile.identity",
            payload={"predicate": "profile.identity", "object": "不再喜欢用户"},
            confidence=0.6,
        )
    )

    assert normalized.perspective == MemoryPerspective.USER_BELIEF
    assert normalized.epistemic_status == EpistemicStatus.UNCERTAIN
    assert normalized.canonical_predicate is None
    assert normalized.custom_predicate is not None
    assert normalized.payload["semantic_type"] == "belief"
    assert normalized.payload["objective_fact"] is False


def test_legacy_serialized_event_without_v3_fields_still_loads() -> None:
    legacy = MemoryItem.model_validate(
        {
            "id": "legacy-event",
            "user_id": SCOPE["user_id"],
            "relationship_id": SCOPE["relationship_id"],
            "kind": "interaction_event",
            "subject": "relationship",
            "summary": "昨天我们一起聊天。",
            "original_text": "昨天我们一起聊天。",
            "payload": {"predicate": "chatted"},
            "status": "confirmed",
            "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
            "dedupe_key": "legacy-dedupe",
        }
    )

    assert legacy.kind == MemoryKind.INTERACTION_EVENT
    assert legacy.novelty is None
    assert legacy.payload == {"predicate": "chatted"}

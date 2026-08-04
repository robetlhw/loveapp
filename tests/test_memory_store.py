import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore, SQLiteMemoryStore
from loveapp.domain.advice import RelationshipContext
from loveapp.domain.enums import RelationshipStage
from loveapp.domain.memory import (
    DiscardedSpan,
    DiscardReason,
    MemoryAttemptStatus,
    MemoryCandidate,
    MemoryExtractionAttempt,
    MemoryExtractionRun,
    MemoryExtractionStatus,
    MemoryGateDecision,
    MemoryGateReason,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    MemoryValence,
    MessageRole,
    RelationshipImpact,
    TemporalPrecision,
    TimeKind,
)


async def test_sqlite_memory_persists_episode_and_trend_as_separate_items(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "persistent-memory.db"
    store = SQLiteMemoryStore(database_path)
    await store.save_relationship_context(
        RelationshipContext(
            user_id="store-user",
            relationship_id="current-partner",
            relationship_stage=RelationshipStage.DATING,
        )
    )
    message = await store.add_message(
        user_id="store-user",
        relationship_id="current-partner",
        role=MessageRole.USER,
        content="连续三周，他下班后都会主动打电话；昨晚我们却因旅行计划争执了。",
        conversation_id="store-conversation",
    )
    trend = _candidate(
        kind=MemoryKind.INTERACTION_PATTERN,
        summary="连续三周，对方下班后都会主动打电话",
        original_text="连续三周，他下班后都会主动打电话",
        time_kind=TimeKind.INTERVAL,
        valence=MemoryValence.POSITIVE,
        impact=RelationshipImpact.IMPROVING,
    )
    episode = _candidate(
        kind=MemoryKind.INTERACTION_EVENT,
        summary="昨晚双方因旅行计划发生争执",
        original_text="昨晚我们却因旅行计划争执了",
        time_kind=TimeKind.POINT,
        valence=MemoryValence.NEGATIVE,
        impact=RelationshipImpact.DAMAGING,
    )
    await store.save_memory(
        user_id="store-user",
        relationship_id="current-partner",
        candidate=trend,
        source_message_id=message.id,
        status=MemoryStatus.CONFIRMED,
    )
    await store.save_memory(
        user_id="store-user",
        relationship_id="current-partner",
        candidate=episode,
        source_message_id=message.id,
        status=MemoryStatus.CONFIRMED,
    )
    await store.aclose()

    reopened = SQLiteMemoryStore(database_path)
    context = await reopened.get_relationship_context(
        "store-user",
        "current-partner",
    )

    assert context is not None
    assert context.relationship_stage == RelationshipStage.DATING
    assert {item.kind for item in context.remembered_items} == {
        MemoryKind.INTERACTION_EVENT,
        MemoryKind.INTERACTION_PATTERN,
    }
    assert {item.valence for item in context.remembered_items} == {
        MemoryValence.POSITIVE,
        MemoryValence.NEGATIVE,
    }


async def test_sqlite_excludes_expired_planned_events_from_effective_context(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "planned-events.db")
    await store.save_relationship_context(
        RelationshipContext(user_id="planned-store-user", relationship_id="primary")
    )
    active = MemoryCandidate(
        kind=MemoryKind.PLANNED_EVENT,
        subject="relationship",
        summary="下周有课程小组讨论",
        original_text="下周有课程小组讨论",
        evidence_spans=["下周有课程小组讨论"],
        time_kind=TimeKind.POINT,
        period_start=datetime(2099, 7, 27, 9, tzinfo=UTC),
        expires_at=datetime(2099, 7, 28, tzinfo=UTC),
        payload={
            "predicate": "attend_course_discussion",
            "object": "课程小组讨论",
            "event_status": "planned",
        },
    )
    expired = active.model_copy(
        update={
            "summary": "昨天已经结束的活动",
            "original_text": "昨天已经结束的活动",
            "evidence_spans": ["昨天已经结束的活动"],
            "period_start": datetime(2000, 7, 17, 9, tzinfo=UTC),
            "expires_at": datetime(2000, 7, 18, tzinfo=UTC),
            "payload": {
                "predicate": "attend_old_activity",
                "object": "旧活动",
                "event_status": "planned",
            },
        }
    )
    await store.save_memory(
        user_id="planned-store-user",
        relationship_id="primary",
        candidate=active,
    )
    await store.save_memory(
        user_id="planned-store-user",
        relationship_id="primary",
        candidate=expired,
    )

    context = await store.get_relationship_context("planned-store-user", "primary")

    assert context is not None
    assert [item.summary for item in context.planned_events] == ["下周有课程小组讨论"]
    assert "昨天已经结束的活动" not in context.important_context
    all_items = await store.list_memories(
        user_id="planned-store-user",
        relationship_id="primary",
    )
    expired_item = next(item for item in all_items if item.summary == "昨天已经结束的活动")
    assert expired_item.status == MemoryStatus.EXPIRED


async def test_sqlite_context_keeps_datetime_when_marking_recent_memory_used(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "recent-context.db")
    await store.save_relationship_context(
        RelationshipContext(user_id="recent-context-user")
    )
    saved = await store.save_memory(
        user_id="recent-context-user",
        relationship_id="primary",
        candidate=MemoryCandidate(
            kind=MemoryKind.INTERACTION_EVENT,
            subject="relationship",
            summary="对方刚刚主动表达了感谢",
            original_text="她刚刚主动感谢我",
            occurred_at=datetime.now(UTC),
            importance=4,
            payload={"predicate": "partner_expressed_gratitude"},
        ),
    )

    context = await store.get_relationship_context(
        "recent-context-user",
        "primary",
    )

    assert context is not None
    assert [item.id for item in context.active_context] == [saved.item.id]
    assert context.active_context[0].attention_reason == "recent_high_importance"
    await store.aclose()


async def test_in_memory_excludes_expired_planned_events_from_effective_context() -> None:
    store = InMemoryMemoryStore()
    await store.save_relationship_context(
        RelationshipContext(user_id="planned-memory-user", relationship_id="primary")
    )
    expired = MemoryCandidate(
        kind=MemoryKind.PLANNED_EVENT,
        subject="relationship",
        summary="已经过期的活动",
        original_text="已经过期的活动",
        evidence_spans=["已经过期的活动"],
        expires_at=datetime(2000, 1, 1, tzinfo=UTC),
        payload={"predicate": "old_event"},
    )
    await store.save_memory(
        user_id="planned-memory-user",
        relationship_id="primary",
        candidate=expired,
    )

    context = await store.get_relationship_context("planned-memory-user", "primary")

    assert context is not None
    assert context.planned_events == []
    assert context.remembered_items == []


async def test_sqlite_persists_memory_extraction_run(tmp_path: Path) -> None:
    database_path = tmp_path / "extraction-runs.db"
    store = SQLiteMemoryStore(database_path)
    await store.save_relationship_context(
        RelationshipContext(user_id="run-store-user", relationship_id="primary")
    )
    message = await store.add_message(
        user_id="run-store-user",
        relationship_id="primary",
        role=MessageRole.USER,
        content="最近我们联系变少了。",
        conversation_id="run-store-conversation",
    )
    run = MemoryExtractionRun(
        id="run-1",
        user_id="run-store-user",
        relationship_id="primary",
        conversation_id=message.conversation_id,
        source_message_id=message.id,
        status=MemoryExtractionStatus.COMPLETED,
        gate_decision=MemoryGateDecision(
            should_extract=True,
            reason=MemoryGateReason.DURABLE_SIGNAL,
            signals=["temporal_interaction"],
        ),
        attempts=[
            MemoryExtractionAttempt(
                attempt=1,
                status=MemoryAttemptStatus.COMPLETED,
                duration_ms=25,
                model="test-model",
            )
        ],
        saved_memory_ids=["memory-1"],
        discarded_spans=[
            DiscardedSpan(
                text="这是不是说明关系结束了",
                reason=DiscardReason.CONSULTATION_QUESTION,
            )
        ],
        completed_at=datetime(2026, 7, 18, 10, tzinfo=UTC),
    )
    await store.save_extraction_run(run)
    await store.aclose()

    reopened = SQLiteMemoryStore(database_path)
    runs = await reopened.list_extraction_runs(
        user_id="run-store-user",
        relationship_id="primary",
        conversation_id="run-store-conversation",
    )

    assert len(runs) == 1
    assert runs[0].id == "run-1"
    assert runs[0].status == MemoryExtractionStatus.COMPLETED
    assert runs[0].gate_decision.signals == ["temporal_interaction"]
    assert runs[0].attempts[0].model == "test-model"
    assert runs[0].saved_memory_ids == ["memory-1"]
    assert runs[0].discarded_spans[0].text == "这是不是说明关系结束了"


async def test_sqlite_persists_ordered_messages_for_one_conversation(tmp_path: Path) -> None:
    database_path = tmp_path / "conversation-history.db"
    store = SQLiteMemoryStore(database_path)
    await store.add_message(
        user_id="history-user",
        relationship_id="primary",
        conversation_id="history-conversation",
        role=MessageRole.USER,
        content="这周我们准备一起参加烹饪课。",
    )
    await store.add_message(
        user_id="history-user",
        relationship_id="primary",
        conversation_id="history-conversation",
        role=MessageRole.ASSISTANT,
        content="可以先确认课程时长和双方饮食限制。",
    )
    await store.add_message(
        user_id="history-user",
        relationship_id="primary",
        conversation_id="another-conversation",
        role=MessageRole.USER,
        content="这条不属于目标会话。",
    )

    reopened = SQLiteMemoryStore(database_path)
    messages = await reopened.list_messages(
        user_id="history-user",
        relationship_id="primary",
        conversation_id="history-conversation",
    )

    assert [message.role for message in messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert [message.content for message in messages] == [
        "这周我们准备一起参加烹饪课。",
        "可以先确认课程时长和双方饮食限制。",
    ]


async def test_sqlite_memory_is_scoped_by_relationship(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "scoped-memory.db")
    for relationship_id, preference in (
        ("book-club", "旧书店"),
        ("running-group", "清晨慢跑"),
    ):
        await store.save_relationship_context(
            RelationshipContext(
                user_id="scope-user",
                relationship_id=relationship_id,
            )
        )
        await store.save_memory(
            user_id="scope-user",
            relationship_id=relationship_id,
            candidate=_preference(preference),
            status=MemoryStatus.CONFIRMED,
        )

    first = await store.get_relationship_context("scope-user", "book-club")
    second = await store.get_relationship_context("scope-user", "running-group")

    assert first is not None and first.user_preferences == ["旧书店"]
    assert second is not None and second.user_preferences == ["清晨慢跑"]


async def test_context_flattens_multiple_preferences_from_extractor(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "preference-list.db")
    await store.save_relationship_context(
        RelationshipContext(user_id="list-user", relationship_id="primary")
    )
    candidate = _preference("室内活动").model_copy(
        update={"payload": {"preference": ["室内活动", "无烟环境"]}}
    )
    await store.save_memory(
        user_id="list-user",
        relationship_id="primary",
        candidate=candidate,
        status=MemoryStatus.CONFIRMED,
    )

    context = await store.get_relationship_context("list-user", "primary")

    assert context is not None
    assert context.user_preferences == ["室内活动", "无烟环境"]


async def test_sqlite_memory_deduplicates_and_supersedes(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "updates.db")
    await store.save_relationship_context(
        RelationshipContext(user_id="update-user", relationship_id="primary")
    )
    original = _preference("周末早场电影")

    first = await store.save_memory(
        user_id="update-user",
        relationship_id="primary",
        candidate=original,
    )
    duplicate = await store.save_memory(
        user_id="update-user",
        relationship_id="primary",
        candidate=original,
        status=MemoryStatus.CONFIRMED,
    )
    replacement = _preference("周末下午场电影").model_copy(update={"supersedes_id": first.item.id})
    latest = await store.save_memory(
        user_id="update-user",
        relationship_id="primary",
        candidate=replacement,
        status=MemoryStatus.CONFIRMED,
    )

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.item.id == first.item.id
    assert duplicate.item.status == MemoryStatus.CONFIRMED
    assert latest.created is True
    assert (await store.get_memory(first.item.id, "update-user")).status == (
        MemoryStatus.SUPERSEDED
    )
    context = await store.get_relationship_context("update-user", "primary")
    assert context is not None
    assert context.user_preferences == ["周末下午场电影"]


async def test_sqlite_semantically_deduplicates_model_summary_variants(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "semantic-dedupe.db")
    first_candidate = MemoryCandidate(
        kind=MemoryKind.STABLE_FACT,
        subject="user",
        summary="用户喜欢一个女孩子",
        original_text="我喜欢了一个女孩子",
        time_kind=TimeKind.TIMELESS,
        confidence=1,
        payload={"predicate": "likes", "object": "a_girl"},
    )
    second_candidate = first_candidate.model_copy(
        update={
            "summary": "用户喜欢班上的一个女孩",
            "original_text": "我喜欢班上的一个女孩",
            "payload": {"predicate": "likes", "object": "classmate_girl"},
        }
    )

    first = await store.save_memory(
        user_id="semantic-user",
        relationship_id="classmate",
        candidate=first_candidate,
    )
    second = await store.save_memory(
        user_id="semantic-user",
        relationship_id="classmate",
        candidate=second_candidate,
    )

    assert first.created is True
    assert second.created is False
    assert second.item.id == first.item.id
    active = await store.list_memories(
        user_id="semantic-user",
        relationship_id="classmate",
        status=MemoryStatus.PROPOSED,
    )
    assert len(active) == 1


async def test_sqlite_deduplicates_equivalent_interaction_pattern_states(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "pattern-dedupe.db")
    first = MemoryCandidate(
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="relationship",
        summary="双方接触较少",
        original_text="我们接触比较少",
        payload={"metric": "interaction_frequency", "current": "low"},
    )
    equivalent = first.model_copy(
        update={
            "summary": "双方很少有搭话机会",
            "payload": {
                "metric": "interaction_frequency",
                "direction": "low",
                "frequency": "rare",
            },
        }
    )
    improving = first.model_copy(
        update={
            "summary": "双方互动正在增加",
            "payload": {"metric": "interaction_frequency", "current": "increasing"},
        }
    )

    first_saved = await store.save_memory(
        user_id="pattern-user",
        relationship_id="primary",
        candidate=first,
    )
    equivalent_saved = await store.save_memory(
        user_id="pattern-user",
        relationship_id="primary",
        candidate=equivalent,
    )
    improving_saved = await store.save_memory(
        user_id="pattern-user",
        relationship_id="primary",
        candidate=improving,
    )

    assert equivalent_saved.item.id == first_saved.item.id
    assert equivalent_saved.created is False
    assert improving_saved.created is True


async def test_delete_and_clear_remove_memories_from_context(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "forget.db")
    await store.save_relationship_context(
        RelationshipContext(user_id="forget-user", relationship_id="primary")
    )
    first = await store.save_memory(
        user_id="forget-user",
        relationship_id="primary",
        candidate=_preference("桌游"),
        status=MemoryStatus.CONFIRMED,
    )
    await store.save_memory(
        user_id="forget-user",
        relationship_id="primary",
        candidate=_preference("烘焙"),
        status=MemoryStatus.CONFIRMED,
    )

    assert await store.delete_memory(first.item.id, "forget-user") is True
    context = await store.get_relationship_context("forget-user", "primary")
    assert context is not None and context.user_preferences == ["烘焙"]
    assert await store.clear_memories("forget-user", "primary") == 1
    context = await store.get_relationship_context("forget-user", "primary")
    assert context is not None and context.remembered_items == []


async def test_sqlite_batch_write_rolls_back_every_candidate_on_failure(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "batch-rollback.db")
    await store.save_relationship_context(
        RelationshipContext(user_id="batch-user", relationship_id="primary")
    )
    valid = _preference("现场喜剧")
    invalid = _preference("室内攀岩").model_copy(update={"supersedes_id": "missing-memory-id"})

    with pytest.raises(ValueError, match="superseded memory"):
        await store.save_memories(
            user_id="batch-user",
            relationship_id="primary",
            candidates=[valid, invalid],
        )

    assert await store.list_memories(user_id="batch-user") == []


async def test_sqlite_migrates_legacy_episode_and_evidence_column(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy-memory.db"
    store = SQLiteMemoryStore(database_path)
    await store.save_relationship_context(
        RelationshipContext(user_id="legacy-user", relationship_id="primary")
    )
    saved = await store.save_memory(
        user_id="legacy-user",
        relationship_id="primary",
        candidate=_candidate(
            kind=MemoryKind.INTERACTION_EVENT,
            summary="周一双方一起参观了科技馆",
            original_text="周一我们一起参观了科技馆",
            time_kind=TimeKind.POINT,
            valence=MemoryValence.POSITIVE,
            impact=RelationshipImpact.IMPROVING,
        ),
    )
    await store.aclose()

    with sqlite3.connect(database_path) as connection:
        connection.execute("ALTER TABLE memory_items DROP COLUMN evidence_spans_json")
        connection.execute(
            "ALTER TABLE memory_extraction_runs DROP COLUMN discarded_spans_json"
        )
        connection.execute(
            "UPDATE memory_items SET kind = 'interaction_episode' WHERE id = ?",
            (saved.item.id,),
        )
        connection.execute("PRAGMA user_version = 1")

    migrated = SQLiteMemoryStore(database_path)
    item = await migrated.get_memory(saved.item.id, "legacy-user")

    assert item is not None
    assert item.kind == MemoryKind.INTERACTION_EVENT
    assert item.evidence_spans == ["周一我们一起参观了科技馆"]
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        run_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(memory_extraction_runs)")
        }
        assert "discarded_spans_json" in run_columns


def _preference(value: str) -> MemoryCandidate:
    return MemoryCandidate(
        kind=MemoryKind.PREFERENCE,
        subject="user",
        summary=f"用户偏好{value}",
        original_text=f"我更喜欢{value}",
        time_kind=TimeKind.TIMELESS,
        temporal_precision=TemporalPrecision.UNKNOWN,
        valence=MemoryValence.POSITIVE,
        relationship_impact=RelationshipImpact.UNCLEAR,
        perspective=MemoryPerspective.USER_REPORTED,
        confidence=1,
        payload={"preference": value, "preference_type": "like"},
    )


def _candidate(
    *,
    kind: MemoryKind,
    summary: str,
    original_text: str,
    time_kind: TimeKind,
    valence: MemoryValence,
    impact: RelationshipImpact,
) -> MemoryCandidate:
    return MemoryCandidate(
        kind=kind,
        subject="relationship",
        summary=summary,
        original_text=original_text,
        time_kind=time_kind,
        occurred_at=(
            datetime(2026, 7, 16, 21, tzinfo=UTC) if time_kind == TimeKind.POINT else None
        ),
        period_start=(
            datetime(2026, 6, 25, tzinfo=UTC) if time_kind == TimeKind.INTERVAL else None
        ),
        period_end=(datetime(2026, 7, 16, tzinfo=UTC) if time_kind == TimeKind.INTERVAL else None),
        temporal_precision=TemporalPrecision.DAY,
        valence=valence,
        relationship_impact=impact,
        perspective=MemoryPerspective.USER_REPORTED,
        confidence=0.95,
    )

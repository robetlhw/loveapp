import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from loveapp.adapters.memory import SQLiteMemoryStore
from loveapp.domain.advice import RelationshipContext
from loveapp.domain.memory import (
    EpistemicStatus,
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    MemoryValence,
    MessageRole,
    RelationshipImpact,
    TimeKind,
    memory_dedupe_key,
)
from loveapp.domain.memory_context import attach_memories
from loveapp.domain.relationship_evidence import project_relationship_evidence

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("perspective", "epistemic_status"),
    [
        (MemoryPerspective.USER_BELIEF, EpistemicStatus.UNCERTAIN),
        (MemoryPerspective.MODEL_INFERRED, EpistemicStatus.HYPOTHESIS),
    ],
)
def test_nonfactual_memory_with_confirmed_lifecycle_stays_in_uncertain_context(
    perspective: MemoryPerspective,
    epistemic_status: EpistemicStatus,
) -> None:
    memory = _item(
        item_id=f"{perspective.value}-profile",
        kind=MemoryKind.STABLE_FACT,
        perspective=perspective,
        epistemic_status=epistemic_status,
        status=MemoryStatus.CONFIRMED,
    )

    context = attach_memories(RelationshipContext(user_id="u1"), [memory])

    assert [item.id for item in context.uncertain_items] == [memory.id]
    assert context.confirmed_long_term == []


def test_belief_relationship_state_is_not_authoritative_evidence() -> None:
    belief = _item(
        item_id="belief-conflict",
        kind=MemoryKind.RELATIONSHIP_STATE,
        perspective=MemoryPerspective.USER_BELIEF,
        epistemic_status=EpistemicStatus.UNCERTAIN,
        status=MemoryStatus.CONFIRMED,
        state_dimension="relationship.conflict_status",
        state_value="active",
        payload={
            "state_dimension": "relationship.conflict_status",
            "state_value": "active",
            "relationship_evidence": [
                {
                    "dimension": "conflict",
                    "direction": "support",
                    "strength": 0.95,
                    "confidence": 0.9,
                    "rationale": "user_suspicion",
                }
            ],
        },
    )

    context = attach_memories(RelationshipContext(user_id="u1"), [belief])
    profile = project_relationship_evidence([belief], reference_time=NOW)

    assert [item.id for item in context.uncertain_items] == [belief.id]
    assert context.confirmed_current_state == []
    assert profile.conflict_status == "unknown"
    assert profile.evidence == []


@pytest.mark.parametrize(
    ("perspective", "epistemic_status"),
    [
        (MemoryPerspective.USER_REPORTED, EpistemicStatus.CONFIRMED),
        (MemoryPerspective.USER_BELIEF, EpistemicStatus.UNCERTAIN),
        (MemoryPerspective.MODEL_INFERRED, EpistemicStatus.HYPOTHESIS),
        (MemoryPerspective.USER_BELIEF, EpistemicStatus.PREDICTION),
    ],
)
async def test_sqlite_round_trips_epistemic_status(
    tmp_path: Path,
    perspective: MemoryPerspective,
    epistemic_status: EpistemicStatus,
) -> None:
    database_path = tmp_path / f"{epistemic_status.value}.db"
    store = SQLiteMemoryStore(database_path)
    await store.save_relationship_context(
        RelationshipContext(user_id="epistemic-user", relationship_id="primary")
    )
    saved = await store.save_memory(
        user_id="epistemic-user",
        relationship_id="primary",
        candidate=_candidate(
            perspective=perspective,
            epistemic_status=epistemic_status,
            predicate=f"profile.{epistemic_status.value}",
        ),
        status=MemoryStatus.CONFIRMED,
    )
    await store.aclose()

    reopened = SQLiteMemoryStore(database_path)
    restored = await reopened.get_memory(saved.item.id, "epistemic-user")
    await reopened.aclose()

    assert restored is not None
    assert restored.perspective == perspective
    assert restored.epistemic_status == epistemic_status


async def test_sqlite_migrates_and_backfills_legacy_epistemic_status(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy-epistemics.db"
    store = SQLiteMemoryStore(database_path)
    await store.save_relationship_context(
        RelationshipContext(user_id="legacy-user", relationship_id="primary")
    )
    saved_ids: dict[MemoryPerspective, str] = {}
    for perspective, status in (
        (MemoryPerspective.USER_REPORTED, EpistemicStatus.CONFIRMED),
        (MemoryPerspective.USER_BELIEF, EpistemicStatus.UNCERTAIN),
        (MemoryPerspective.MODEL_INFERRED, EpistemicStatus.HYPOTHESIS),
    ):
        saved = await store.save_memory(
            user_id="legacy-user",
            relationship_id="primary",
            candidate=_candidate(
                perspective=perspective,
                epistemic_status=status,
                predicate=f"legacy.{perspective.value}",
            ),
            status=MemoryStatus.CONFIRMED,
        )
        saved_ids[perspective] = saved.item.id
    await store.aclose()

    with sqlite3.connect(database_path) as connection:
        connection.execute("ALTER TABLE memory_items DROP COLUMN epistemic_status")
        connection.execute("PRAGMA user_version = 8")

    migrated = SQLiteMemoryStore(database_path)
    restored = {
        perspective: await migrated.get_memory(memory_id, "legacy-user")
        for perspective, memory_id in saved_ids.items()
    }
    await migrated.aclose()

    assert restored[MemoryPerspective.USER_REPORTED] is not None
    assert (
        restored[MemoryPerspective.USER_REPORTED].epistemic_status
        == EpistemicStatus.CONFIRMED
    )
    assert restored[MemoryPerspective.USER_BELIEF] is not None
    assert (
        restored[MemoryPerspective.USER_BELIEF].epistemic_status
        == EpistemicStatus.UNCERTAIN
    )
    assert restored[MemoryPerspective.MODEL_INFERRED] is not None
    assert (
        restored[MemoryPerspective.MODEL_INFERRED].epistemic_status
        == EpistemicStatus.HYPOTHESIS
    )
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(memory_items)")
        }
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        persisted = dict(
            connection.execute(
                "SELECT perspective, epistemic_status FROM memory_items"
            ).fetchall()
        )
    assert "epistemic_status" in columns
    assert version == 9
    assert persisted == {
        MemoryPerspective.USER_REPORTED.value: EpistemicStatus.CONFIRMED.value,
        MemoryPerspective.USER_BELIEF.value: EpistemicStatus.UNCERTAIN.value,
        MemoryPerspective.MODEL_INFERRED.value: EpistemicStatus.HYPOTHESIS.value,
    }


async def test_sqlite_migration_rekeys_legacy_belief_identity(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy-belief-identity.db"
    store = SQLiteMemoryStore(database_path)
    await store.save_relationship_context(
        RelationshipContext(user_id="identity-user", relationship_id="primary")
    )
    original_message = await store.add_message(
        user_id="identity-user",
        relationship_id="primary",
        conversation_id="identity-conversation",
        role=MessageRole.USER,
        content="I think my partner dislikes me.",
    )
    belief = _candidate(
        perspective=MemoryPerspective.USER_BELIEF,
        epistemic_status=EpistemicStatus.UNCERTAIN,
        predicate="partner.dislikes_user",
    ).model_copy(update={"subject": "partner"})
    reported_fact = _candidate(
        perspective=MemoryPerspective.USER_REPORTED,
        epistemic_status=EpistemicStatus.CONFIRMED,
        predicate="partner.dislikes_user",
    ).model_copy(update={"subject": "partner"})
    saved_belief = await store.save_memory(
        user_id="identity-user",
        relationship_id="primary",
        candidate=belief,
        source_message_id=original_message.id,
        status=MemoryStatus.CONFIRMED,
    )
    await store.aclose()

    legacy_fact_key = memory_dedupe_key(reported_fact)
    assert legacy_fact_key != memory_dedupe_key(belief)
    with sqlite3.connect(database_path) as connection:
        connection.execute("ALTER TABLE memory_items DROP COLUMN epistemic_status")
        connection.execute(
            "UPDATE memory_items SET dedupe_key = ? WHERE id = ?",
            (legacy_fact_key, saved_belief.item.id),
        )
        connection.execute("PRAGMA user_version = 8")

    migrated = SQLiteMemoryStore(database_path)
    restored_belief = await migrated.get_memory(
        saved_belief.item.id,
        "identity-user",
    )
    assert restored_belief is not None
    assert restored_belief.dedupe_key == memory_dedupe_key(belief)

    fact_message = await migrated.add_message(
        user_id="identity-user",
        relationship_id="primary",
        conversation_id="identity-conversation",
        role=MessageRole.USER,
        content="My partner explicitly said they dislike me.",
    )
    saved_fact = await migrated.save_memory(
        user_id="identity-user",
        relationship_id="primary",
        candidate=reported_fact,
        source_message_id=fact_message.id,
        status=MemoryStatus.CONFIRMED,
    )
    replayed_belief = await migrated.save_memory(
        user_id="identity-user",
        relationship_id="primary",
        candidate=belief,
        source_message_id=original_message.id,
        status=MemoryStatus.CONFIRMED,
    )
    active = await migrated.list_memories(
        user_id="identity-user",
        relationship_id="primary",
    )
    await migrated.aclose()

    assert saved_fact.created is True
    assert saved_fact.item.id != restored_belief.id
    assert replayed_belief.created is False
    assert replayed_belief.item.id == restored_belief.id
    assert {item.id for item in active} == {
        restored_belief.id,
        saved_fact.item.id,
    }


def _candidate(
    *,
    perspective: MemoryPerspective,
    epistemic_status: EpistemicStatus,
    predicate: str,
) -> MemoryCandidate:
    return MemoryCandidate(
        kind=MemoryKind.STABLE_FACT,
        subject=perspective.value,
        summary=predicate,
        original_text=predicate,
        time_kind=TimeKind.TIMELESS,
        valence=MemoryValence.NEUTRAL,
        relationship_impact=RelationshipImpact.UNCLEAR,
        perspective=perspective,
        epistemic_status=epistemic_status,
        confidence=0.9,
        raw_predicate=predicate,
        custom_predicate=predicate,
        payload={"predicate": predicate},
    )


def _item(
    *,
    item_id: str,
    kind: MemoryKind,
    perspective: MemoryPerspective,
    epistemic_status: EpistemicStatus,
    status: MemoryStatus,
    state_dimension: str | None = None,
    state_value: str | None = None,
    payload: dict[str, object] | None = None,
) -> MemoryItem:
    predicate = state_dimension or f"profile.{item_id}"
    return MemoryItem(
        id=item_id,
        user_id="u1",
        relationship_id="primary",
        source_message_id=f"message-{item_id}",
        dedupe_key=f"key-{item_id}",
        kind=kind,
        subject="relationship",
        summary=item_id,
        original_text=item_id,
        evidence_spans=[item_id],
        time_kind=TimeKind.TIMELESS,
        valence=MemoryValence.NEUTRAL,
        relationship_impact=RelationshipImpact.UNCLEAR,
        perspective=perspective,
        epistemic_status=epistemic_status,
        confidence=0.9,
        status=status,
        raw_predicate=predicate,
        canonical_predicate=predicate if state_dimension else None,
        custom_predicate=None if state_dimension else predicate,
        state_dimension=state_dimension,
        state_value=state_value,
        payload=payload or {"predicate": predicate},
        created_at=NOW,
        updated_at=NOW,
    )

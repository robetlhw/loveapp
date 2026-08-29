import sqlite3
from pathlib import Path

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore, SQLiteMemoryStore
from loveapp.domain.advice import RelationshipContext
from loveapp.domain.enums import RelationshipStage
from loveapp.domain.memory import (
    MemoryCandidate,
    MemoryExtractionRun,
    MemoryExtractionStatus,
    MemoryGateDecision,
    MemoryGateReason,
    MemoryKind,
    MemoryStatus,
    MessageRole,
)
from loveapp.domain.relationship_plan import PlanStatus, RelationshipPlan


@pytest.mark.parametrize("backend", ["in-memory", "sqlite"])
async def test_reset_relationship_scope_clears_only_the_exact_scope(
    backend: str,
    tmp_path: Path,
) -> None:
    store = (
        InMemoryMemoryStore()
        if backend == "in-memory"
        else SQLiteMemoryStore(tmp_path / "scoped-reset.db")
    )
    target = await _seed_scope(store, "shared-user", "target", "target")
    same_user = await _seed_scope(store, "shared-user", "preserved", "same-user")
    same_relationship = await _seed_scope(
        store,
        "other-user",
        "target",
        "same-relationship",
    )

    await store.reset_relationship_scope(
        user_id="shared-user",
        relationship_id="target",
    )
    await store.reset_relationship_scope(
        user_id="shared-user",
        relationship_id="target",
    )

    assert await store.get_relationship_context(
        "shared-user",
        "target",
        read_only=True,
    ) is None
    assert await store.list_messages(
        user_id="shared-user",
        relationship_id="target",
    ) == []
    assert await store.list_memories(
        user_id="shared-user",
        relationship_id="target",
        read_only=True,
    ) == []
    assert await store.list_extraction_runs(
        user_id="shared-user",
        relationship_id="target",
    ) == []
    assert await store.list_transition_audits(
        user_id="shared-user",
        relationship_id="target",
    ) == []
    assert await store.list_relationship_plans(
        user_id="shared-user",
        relationship_id="target",
        read_only=True,
    ) == []
    assert await store.get_memory(target["memory_id"], "shared-user") is None
    assert await store.get_relationship_plan(target["plan_id"], "shared-user") is None

    await _assert_scope_preserved(
        store,
        user_id="shared-user",
        relationship_id="preserved",
        identities=same_user,
    )
    await _assert_scope_preserved(
        store,
        user_id="other-user",
        relationship_id="target",
        identities=same_relationship,
    )


async def test_sqlite_reset_removes_every_scoped_persistence_row(tmp_path: Path) -> None:
    database_path = tmp_path / "scoped-reset-rows.db"
    store = SQLiteMemoryStore(database_path)
    await _seed_scope(store, "row-user", "target", "target")
    await _seed_scope(store, "row-user", "preserved", "preserved")

    await store.reset_relationship_scope(
        user_id="row-user",
        relationship_id="target",
    )

    with sqlite3.connect(database_path) as connection:
        for table in (
            "relationships",
            "conversations",
            "messages",
            "memory_items",
            "relationship_plans",
            "memory_extraction_runs",
            "memory_transition_audit",
        ):
            target_count = connection.execute(
                f"SELECT COUNT(*) FROM {table} "
                "WHERE user_id = ? AND relationship_id = ?"
                if table != "relationships"
                else f"SELECT COUNT(*) FROM {table} WHERE user_id = ? AND id = ?",
                ("row-user", "target"),
            ).fetchone()[0]
            preserved_count = connection.execute(
                f"SELECT COUNT(*) FROM {table} "
                "WHERE user_id = ? AND relationship_id = ?"
                if table != "relationships"
                else f"SELECT COUNT(*) FROM {table} WHERE user_id = ? AND id = ?",
                ("row-user", "preserved"),
            ).fetchone()[0]
            assert target_count == 0, table
            assert preserved_count == 1, table


async def _seed_scope(
    store: InMemoryMemoryStore | SQLiteMemoryStore,
    user_id: str,
    relationship_id: str,
    suffix: str,
) -> dict[str, str]:
    await store.save_relationship_context(
        RelationshipContext(
            user_id=user_id,
            relationship_id=relationship_id,
            relationship_stage=RelationshipStage.DATING,
        )
    )
    message = await store.add_message(
        user_id=user_id,
        relationship_id=relationship_id,
        conversation_id=f"conversation-{suffix}",
        role=MessageRole.USER,
        content=f"memory reset fixture {suffix}",
    )
    saved = await store.save_memory(
        user_id=user_id,
        relationship_id=relationship_id,
        candidate=MemoryCandidate(
            kind=MemoryKind.PREFERENCE,
            subject="user",
            summary=f"preference {suffix}",
            original_text=f"preference {suffix}",
            payload={"predicate": "likes", "object": suffix},
        ),
        source_message_id=message.id,
    )
    await store.set_memory_status(saved.item.id, user_id, MemoryStatus.CONFIRMED)
    plan = await store.save_relationship_plan(
        RelationshipPlan(
            plan_id=f"plan-{suffix}",
            user_id=user_id,
            relationship_id=relationship_id,
            activity_type=f"activity-{suffix}",
            status=PlanStatus.CONFIRMED,
            source_message_id=message.id,
        )
    )
    run = MemoryExtractionRun(
        id=f"run-{suffix}",
        user_id=user_id,
        relationship_id=relationship_id,
        conversation_id=message.conversation_id,
        source_message_id=message.id,
        status=MemoryExtractionStatus.COMPLETED,
        gate_decision=MemoryGateDecision(
            should_extract=True,
            reason=MemoryGateReason.DURABLE_SIGNAL,
            signals=["test_fixture"],
        ),
        saved_memory_ids=[saved.item.id],
    )
    await store.save_extraction_run(run)
    return {
        "memory_id": saved.item.id,
        "message_id": message.id,
        "plan_id": plan.plan_id,
        "run_id": run.id,
    }


async def _assert_scope_preserved(
    store: InMemoryMemoryStore | SQLiteMemoryStore,
    *,
    user_id: str,
    relationship_id: str,
    identities: dict[str, str],
) -> None:
    context = await store.get_relationship_context(
        user_id,
        relationship_id,
        read_only=True,
    )
    assert context is not None
    assert context.relationship_stage == RelationshipStage.DATING
    assert [
        message.id
        for message in await store.list_messages(
            user_id=user_id,
            relationship_id=relationship_id,
        )
    ] == [identities["message_id"]]
    assert [
        item.id
        for item in await store.list_memories(
            user_id=user_id,
            relationship_id=relationship_id,
            read_only=True,
        )
    ] == [identities["memory_id"]]
    assert [
        run.id
        for run in await store.list_extraction_runs(
            user_id=user_id,
            relationship_id=relationship_id,
        )
    ] == [identities["run_id"]]
    assert await store.list_transition_audits(
        user_id=user_id,
        relationship_id=relationship_id,
    )
    assert [
        plan.plan_id
        for plan in await store.list_relationship_plans(
            user_id=user_id,
            relationship_id=relationship_id,
            read_only=True,
        )
    ] == [identities["plan_id"]]

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite

from loveapp.domain.advice import RelationshipContext
from loveapp.domain.enums import RelationshipStage
from loveapp.domain.memory import (
    MemoryCandidate,
    MemoryExtractionRun,
    MemoryItem,
    MemoryKind,
    MemorySaveResult,
    MemoryStatus,
    MessageRole,
    StoredMessage,
    memory_dedupe_key,
    utc_now,
)
from loveapp.domain.memory_context import attach_memories, select_context_memories
from loveapp.domain.relationship_plan import (
    PlanStatus,
    RelationshipPlan,
    can_transition_plan_status,
    memory_with_plan,
    relationship_plan_from_memory,
)


class SQLiteMemoryStore:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    @property
    def database_path(self) -> Path:
        return self._database_path

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            connection = await self._open_connection(initialize=False)
            try:
                await connection.execute("PRAGMA journal_mode = WAL")
                await connection.executescript(_SCHEMA)
                await _migrate_schema(connection)
                await connection.commit()
            finally:
                await connection.close()
            self._initialized = True

    async def add_message(
        self,
        *,
        user_id: str,
        relationship_id: str,
        role: MessageRole,
        content: str,
        conversation_id: str | None = None,
    ) -> StoredMessage:
        await self.initialize()
        now = utc_now()
        message_id = str(uuid4())
        conversation_id = conversation_id or str(uuid4())
        connection = await self._open_connection()
        try:
            await _ensure_scope(connection, user_id, relationship_id, now)
            existing = await _fetchone(
                connection,
                "SELECT user_id, relationship_id FROM conversations WHERE id = ?",
                (conversation_id,),
            )
            if existing and (existing["user_id"], existing["relationship_id"]) != (
                user_id,
                relationship_id,
            ):
                raise ValueError("conversation_id belongs to a different relationship scope")
            await connection.execute(
                """
                INSERT INTO conversations (
                    id, user_id, relationship_id, started_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (
                    conversation_id,
                    user_id,
                    relationship_id,
                    _dump_datetime(now),
                    _dump_datetime(now),
                ),
            )
            await connection.execute(
                """
                INSERT INTO messages (
                    id, conversation_id, user_id, relationship_id, role, content, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    conversation_id,
                    user_id,
                    relationship_id,
                    role.value,
                    content,
                    _dump_datetime(now),
                ),
            )
            await connection.commit()
        finally:
            await connection.close()
        return StoredMessage(
            id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            relationship_id=relationship_id,
            role=role,
            content=content,
            created_at=now,
        )

    async def list_messages(
        self,
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str | None = None,
        limit: int = 20,
    ) -> list[StoredMessage]:
        await self.initialize()
        clauses = ["user_id = ?", "relationship_id = ?"]
        values: list[str | int] = [user_id, relationship_id]
        if conversation_id is not None:
            clauses.append("conversation_id = ?")
            values.append(conversation_id)
        values.append(limit)
        query = f"""
            SELECT * FROM messages
            WHERE {" AND ".join(clauses)}
            ORDER BY created_at DESC
            LIMIT ?
        """
        connection = await self._open_connection()
        try:
            cursor = await connection.execute(query, values)
            rows = list(reversed(await cursor.fetchall()))
            await cursor.close()
            return [
                StoredMessage(
                    id=row["id"],
                    conversation_id=row["conversation_id"],
                    user_id=row["user_id"],
                    relationship_id=row["relationship_id"],
                    role=row["role"],
                    content=row["content"],
                    created_at=_load_datetime(row["created_at"]),
                )
                for row in rows
            ]
        finally:
            await connection.close()

    async def save_memory(
        self,
        *,
        user_id: str,
        relationship_id: str,
        candidate: MemoryCandidate,
        source_message_id: str | None = None,
        status: MemoryStatus = MemoryStatus.PROPOSED,
    ) -> MemorySaveResult:
        results = await self.save_memories(
            user_id=user_id,
            relationship_id=relationship_id,
            candidates=[candidate],
            source_message_id=source_message_id,
            status=status,
        )
        return results[0]

    async def save_memories(
        self,
        *,
        user_id: str,
        relationship_id: str,
        candidates: list[MemoryCandidate],
        source_message_id: str | None = None,
        status: MemoryStatus = MemoryStatus.PROPOSED,
    ) -> list[MemorySaveResult]:
        if not candidates:
            return []
        await self.initialize()
        now = utc_now()
        connection = await self._open_connection()
        try:
            await _ensure_scope(connection, user_id, relationship_id, now)
            await _expire_due_memories(connection, now)
            results = [
                await _save_memory_in_transaction(
                    connection=connection,
                    user_id=user_id,
                    relationship_id=relationship_id,
                    candidate=candidate,
                    source_message_id=source_message_id,
                    status=status,
                    now=now,
                )
                for candidate in candidates
            ]
            for index, result in enumerate(results):
                if result.item.kind != MemoryKind.PLANNED_EVENT:
                    continue
                await _ensure_plan_for_memory_in_transaction(connection, result.item)
                refreshed = await _fetchone(
                    connection,
                    "SELECT * FROM memory_items WHERE id = ?",
                    (result.item.id,),
                )
                if refreshed is None:
                    raise RuntimeError("planned memory disappeared while creating its plan")
                results[index] = result.model_copy(update={"item": _row_to_memory(refreshed)})
            await connection.commit()
            return results
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def get_memory(self, memory_id: str, user_id: str) -> MemoryItem | None:
        await self.initialize()
        connection = await self._open_connection()
        try:
            row = await _fetchone(
                connection,
                "SELECT * FROM memory_items WHERE id = ? AND user_id = ?",
                (memory_id, user_id),
            )
            return _row_to_memory(row) if row else None
        finally:
            await connection.close()

    async def list_memories(
        self,
        *,
        user_id: str,
        relationship_id: str | None = None,
        kind: MemoryKind | None = None,
        status: MemoryStatus | None = None,
        limit: int = 100,
    ) -> list[MemoryItem]:
        await self.initialize()
        clauses = ["user_id = ?"]
        values: list[str | int] = [user_id]
        if relationship_id is not None:
            clauses.append("relationship_id = ?")
            values.append(relationship_id)
        if kind is not None:
            clauses.append("kind = ?")
            values.append(kind.value)
        if status is not None:
            clauses.append("status = ?")
            values.append(status.value)
        values.append(limit)
        query = f"""
            SELECT * FROM memory_items
            WHERE {" AND ".join(clauses)}
            ORDER BY importance DESC,
                     COALESCE(occurred_at, period_end, updated_at) DESC,
                     created_at DESC
            LIMIT ?
        """
        connection = await self._open_connection()
        try:
            now = utc_now()
            await _expire_due_memories(connection, now)
            await connection.commit()
            cursor = await connection.execute(query, values)
            rows = await cursor.fetchall()
            await cursor.close()
            return [_row_to_memory(row) for row in rows]
        finally:
            await connection.close()

    async def set_memory_status(
        self,
        memory_id: str,
        user_id: str,
        status: MemoryStatus,
    ) -> MemoryItem | None:
        await self.initialize()
        connection = await self._open_connection()
        try:
            now = utc_now()
            try:
                cursor = await connection.execute(
                    """
                    UPDATE memory_items SET status = ?, updated_at = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (status.value, _dump_datetime(now), memory_id, user_id),
                )
            except sqlite3.IntegrityError as exc:
                await connection.rollback()
                raise ValueError("An equivalent active memory already exists.") from exc
            if cursor.rowcount == 0:
                await connection.rollback()
                return None
            target_plan_status: PlanStatus | None = None
            if status == MemoryStatus.EXPIRED:
                target_plan_status = PlanStatus.EXPIRED
            elif status in {MemoryStatus.REJECTED, MemoryStatus.SUPERSEDED}:
                target_plan_status = PlanStatus.CANCELLED
            if target_plan_status is not None:
                timestamp = _dump_datetime(now)
                await connection.execute(
                    """
                    UPDATE relationship_plans
                    SET status = ?, updated_at = ?,
                        cancelled_at = CASE WHEN ? = 'cancelled' THEN ? ELSE cancelled_at END
                    WHERE source_memory_id = ? AND user_id = ?
                      AND status IN ('proposed', 'confirmed')
                    """,
                    (
                        target_plan_status.value,
                        timestamp,
                        target_plan_status.value,
                        timestamp,
                        memory_id,
                        user_id,
                    ),
                )
            await connection.commit()
            row = await _fetchone(
                connection,
                "SELECT * FROM memory_items WHERE id = ? AND user_id = ?",
                (memory_id, user_id),
            )
            return _row_to_memory(row) if row else None
        finally:
            await connection.close()

    async def delete_memory(self, memory_id: str, user_id: str) -> bool:
        await self.initialize()
        connection = await self._open_connection()
        try:
            row = await _fetchone(
                connection,
                "SELECT source_message_id FROM memory_items WHERE id = ? AND user_id = ?",
                (memory_id, user_id),
            )
            if row is None:
                return False
            await connection.execute(
                "DELETE FROM relationship_plans WHERE source_memory_id = ? AND user_id = ?",
                (memory_id, user_id),
            )
            await connection.execute(
                "DELETE FROM memory_items WHERE id = ? AND user_id = ?",
                (memory_id, user_id),
            )
            source_message_id = row["source_message_id"]
            if source_message_id:
                reference = await _fetchone(
                    connection,
                    "SELECT 1 FROM memory_items WHERE source_message_id = ? LIMIT 1",
                    (source_message_id,),
                )
                if reference is None:
                    await connection.execute(
                        "DELETE FROM messages WHERE id = ? AND user_id = ?",
                        (source_message_id, user_id),
                    )
            await connection.commit()
            return True
        finally:
            await connection.close()

    async def clear_memories(self, user_id: str, relationship_id: str | None = None) -> int:
        await self.initialize()
        connection = await self._open_connection()
        try:
            if relationship_id is None:
                await connection.execute(
                    "DELETE FROM relationship_plans WHERE user_id = ?",
                    (user_id,),
                )
                cursor = await connection.execute(
                    "DELETE FROM memory_items WHERE user_id = ?",
                    (user_id,),
                )
                await connection.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
                await connection.execute(
                    "DELETE FROM conversations WHERE user_id = ?",
                    (user_id,),
                )
            else:
                await connection.execute(
                    "DELETE FROM relationship_plans "
                    "WHERE user_id = ? AND relationship_id = ?",
                    (user_id, relationship_id),
                )
                cursor = await connection.execute(
                    "DELETE FROM memory_items WHERE user_id = ? AND relationship_id = ?",
                    (user_id, relationship_id),
                )
                await connection.execute(
                    "DELETE FROM messages WHERE user_id = ? AND relationship_id = ?",
                    (user_id, relationship_id),
                )
                await connection.execute(
                    "DELETE FROM conversations WHERE user_id = ? AND relationship_id = ?",
                    (user_id, relationship_id),
                )
            await connection.commit()
            return max(cursor.rowcount, 0)
        finally:
            await connection.close()

    async def get_relationship_context(
        self,
        user_id: str,
        relationship_id: str,
        limit: int = 20,
    ) -> RelationshipContext | None:
        await self.initialize()
        connection = await self._open_connection()
        try:
            now = utc_now()
            await _expire_due_memories(connection, now)
            await _sync_relationship_plans_in_transaction(
                connection,
                user_id=user_id,
                relationship_id=relationship_id,
                now=now,
            )
            await connection.commit()
            relationship = await _fetchone(
                connection,
                """
                SELECT stage FROM relationships
                WHERE user_id = ? AND id = ?
                """,
                (user_id, relationship_id),
            )
            if relationship is None:
                return None
            fetch_limit = max(limit * 5, 100)
            cursor = await connection.execute(
                """
                SELECT * FROM memory_items
                WHERE user_id = ? AND relationship_id = ?
                  AND status IN ('proposed', 'confirmed')
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY importance DESC,
                         COALESCE(occurred_at, period_end, updated_at) DESC,
                         created_at DESC
                LIMIT ?
                """,
                (user_id, relationship_id, _dump_datetime(utc_now()), fetch_limit),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            memories = select_context_memories(
                [_row_to_memory(row) for row in rows],
                limit=limit,
                reference_time=now,
            )
            plan_cursor = await connection.execute(
                """
                SELECT * FROM relationship_plans
                WHERE user_id = ? AND relationship_id = ?
                  AND status IN ('proposed', 'confirmed')
                ORDER BY updated_at DESC
                """,
                (user_id, relationship_id),
            )
            active_plans = [
                _row_to_relationship_plan(row) for row in await plan_cursor.fetchall()
            ]
            await plan_cursor.close()
            active_plan_memory_ids = {
                plan.source_memory_id
                for plan in active_plans
                if plan.source_memory_id is not None
            }
            memories = [
                memory
                for memory in memories
                if memory.kind != MemoryKind.PLANNED_EVENT
                or memory.id in active_plan_memory_ids
            ]
            if memories:
                last_used_at = _dump_datetime(utc_now())
                await connection.executemany(
                    "UPDATE memory_items SET last_used_at = ? WHERE id = ?",
                    [(last_used_at, item.id) for item in memories],
                )
                await connection.commit()
            base = RelationshipContext(
                user_id=user_id,
                relationship_id=relationship_id,
                relationship_stage=RelationshipStage(relationship["stage"]),
            )
            return attach_memories(
                base,
                memories,
                active_plans=active_plans,
                reference_time=now,
            )
        finally:
            await connection.close()

    async def save_relationship_context(self, context: RelationshipContext) -> None:
        await self.initialize()
        connection = await self._open_connection()
        try:
            now = utc_now()
            await _ensure_scope(
                connection,
                context.user_id,
                context.relationship_id,
                now,
                context.relationship_stage,
            )
            await connection.commit()
        finally:
            await connection.close()

    async def save_relationship_plan(self, plan: RelationshipPlan) -> RelationshipPlan:
        await self.initialize()
        connection = await self._open_connection()
        try:
            await _ensure_scope(
                connection,
                plan.user_id,
                plan.relationship_id,
                plan.updated_at,
            )
            existing = await _fetchone(
                connection,
                "SELECT user_id, relationship_id FROM relationship_plans WHERE plan_id = ?",
                (plan.plan_id,),
            )
            if existing is not None and (
                existing["user_id"],
                existing["relationship_id"],
            ) != (plan.user_id, plan.relationship_id):
                raise ValueError("plan_id belongs to a different relationship scope")
            await _upsert_relationship_plan(connection, plan)
            await connection.commit()
            row = await _fetchone(
                connection,
                "SELECT * FROM relationship_plans WHERE plan_id = ?",
                (plan.plan_id,),
            )
            if row is None:
                raise RuntimeError("relationship plan was not readable after persistence")
            return _row_to_relationship_plan(row)
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def get_relationship_plan(
        self,
        plan_id: str,
        user_id: str,
    ) -> RelationshipPlan | None:
        await self.initialize()
        connection = await self._open_connection()
        try:
            row = await _fetchone(
                connection,
                "SELECT * FROM relationship_plans WHERE plan_id = ? AND user_id = ?",
                (plan_id, user_id),
            )
            return _row_to_relationship_plan(row) if row is not None else None
        finally:
            await connection.close()

    async def list_relationship_plans(
        self,
        *,
        user_id: str,
        relationship_id: str,
        status: PlanStatus | None = None,
        limit: int = 100,
    ) -> list[RelationshipPlan]:
        await self.sync_relationship_plans(
            user_id=user_id,
            relationship_id=relationship_id,
        )
        clauses = ["user_id = ?", "relationship_id = ?"]
        values: list[str | int] = [user_id, relationship_id]
        if status is not None:
            clauses.append("status = ?")
            values.append(status.value)
        values.append(limit)
        connection = await self._open_connection()
        try:
            cursor = await connection.execute(
                f"""
                SELECT * FROM relationship_plans
                WHERE {" AND ".join(clauses)}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                values,
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [_row_to_relationship_plan(row) for row in rows]
        finally:
            await connection.close()

    async def set_relationship_plan_status(
        self,
        *,
        plan_id: str,
        user_id: str,
        status: PlanStatus,
        transitioned_at: datetime | None = None,
        source_event_memory_id: str | None = None,
    ) -> RelationshipPlan | None:
        await self.initialize()
        connection = await self._open_connection()
        try:
            row = await _fetchone(
                connection,
                "SELECT * FROM relationship_plans WHERE plan_id = ? AND user_id = ?",
                (plan_id, user_id),
            )
            if row is None:
                return None
            current = PlanStatus(row["status"])
            if not can_transition_plan_status(current, status):
                raise ValueError(f"invalid relationship plan transition: {current} -> {status}")
            now = transitioned_at or utc_now()
            payload = json.loads(row["payload_json"])
            if source_event_memory_id is not None:
                payload["terminal_event_memory_id"] = source_event_memory_id
            completed_at = row["completed_at"]
            cancelled_at = row["cancelled_at"]
            if status == PlanStatus.COMPLETED:
                completed_at = _dump_datetime(now)
            elif status == PlanStatus.CANCELLED:
                cancelled_at = _dump_datetime(now)
            await connection.execute(
                """
                UPDATE relationship_plans
                SET status = ?, updated_at = ?, completed_at = ?, cancelled_at = ?,
                    payload_json = ?
                WHERE plan_id = ? AND user_id = ?
                """,
                (
                    status.value,
                    _dump_datetime(now),
                    completed_at,
                    cancelled_at,
                    _dump_json(payload),
                    plan_id,
                    user_id,
                ),
            )
            source_memory_id = row["source_memory_id"]
            if source_memory_id is not None:
                source = await _fetchone(
                    connection,
                    "SELECT payload_json FROM memory_items WHERE id = ? AND user_id = ?",
                    (source_memory_id, user_id),
                )
                if source is not None:
                    memory_payload = json.loads(source["payload_json"])
                    memory_payload["plan_status"] = status.value
                    memory_status: MemoryStatus | None = None
                    if status in {PlanStatus.COMPLETED, PlanStatus.CANCELLED}:
                        memory_status = MemoryStatus.SUPERSEDED
                    elif status == PlanStatus.EXPIRED:
                        memory_status = MemoryStatus.EXPIRED
                    if memory_status is None:
                        await connection.execute(
                            "UPDATE memory_items SET payload_json = ?, updated_at = ? WHERE id = ?",
                            (_dump_json(memory_payload), _dump_datetime(now), source_memory_id),
                        )
                    else:
                        await connection.execute(
                            """
                            UPDATE memory_items
                            SET payload_json = ?, status = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                _dump_json(memory_payload),
                                memory_status.value,
                                _dump_datetime(now),
                                source_memory_id,
                            ),
                        )
            await connection.commit()
            updated = await _fetchone(
                connection,
                "SELECT * FROM relationship_plans WHERE plan_id = ?",
                (plan_id,),
            )
            return _row_to_relationship_plan(updated) if updated is not None else None
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def sync_relationship_plans(
        self,
        *,
        user_id: str,
        relationship_id: str,
    ) -> list[RelationshipPlan]:
        await self.initialize()
        connection = await self._open_connection()
        try:
            plans = await _sync_relationship_plans_in_transaction(
                connection,
                user_id=user_id,
                relationship_id=relationship_id,
                now=utc_now(),
            )
            await connection.commit()
            return plans
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def save_extraction_run(self, run: MemoryExtractionRun) -> None:
        await self.initialize()
        connection = await self._open_connection()
        try:
            await connection.execute(
                """
                INSERT INTO memory_extraction_runs (
                    id, user_id, relationship_id, conversation_id, source_message_id,
                    status, gate_should_extract, gate_reason, gate_signals_json,
                    attempts_json, saved_memory_ids_json, discarded_spans_json,
                    error, created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    gate_should_extract = excluded.gate_should_extract,
                    gate_reason = excluded.gate_reason,
                    gate_signals_json = excluded.gate_signals_json,
                    attempts_json = excluded.attempts_json,
                    saved_memory_ids_json = excluded.saved_memory_ids_json,
                    discarded_spans_json = excluded.discarded_spans_json,
                    error = excluded.error,
                    updated_at = excluded.updated_at,
                    completed_at = excluded.completed_at
                """,
                (
                    run.id,
                    run.user_id,
                    run.relationship_id,
                    run.conversation_id,
                    run.source_message_id,
                    run.status.value,
                    int(run.gate_decision.should_extract),
                    run.gate_decision.reason.value,
                    _dump_json(run.gate_decision.signals),
                    _dump_json([attempt.model_dump(mode="json") for attempt in run.attempts]),
                    _dump_json(run.saved_memory_ids),
                    _dump_json(
                        [span.model_dump(mode="json") for span in run.discarded_spans]
                    ),
                    run.error,
                    _dump_datetime(run.created_at),
                    _dump_datetime(run.updated_at),
                    _dump_datetime(run.completed_at),
                ),
            )
            await connection.commit()
        finally:
            await connection.close()

    async def list_extraction_runs(
        self,
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str | None = None,
        limit: int = 20,
    ) -> list[MemoryExtractionRun]:
        await self.initialize()
        clauses = ["user_id = ?", "relationship_id = ?"]
        values: list[str | int] = [user_id, relationship_id]
        if conversation_id is not None:
            clauses.append("conversation_id = ?")
            values.append(conversation_id)
        values.append(limit)
        query = f"""
            SELECT * FROM memory_extraction_runs
            WHERE {" AND ".join(clauses)}
            ORDER BY updated_at DESC
            LIMIT ?
        """
        connection = await self._open_connection()
        try:
            cursor = await connection.execute(query, values)
            rows = await cursor.fetchall()
            await cursor.close()
            return [_row_to_extraction_run(row) for row in rows]
        finally:
            await connection.close()

    async def aclose(self) -> None:
        return None

    async def _open_connection(self, *, initialize: bool = True) -> aiosqlite.Connection:
        if initialize:
            await self.initialize()
        connection = await aiosqlite.connect(self._database_path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute("PRAGMA busy_timeout = 5000")
        return connection


async def _ensure_scope(
    connection: aiosqlite.Connection,
    user_id: str,
    relationship_id: str,
    now: datetime,
    stage: RelationshipStage = RelationshipStage.UNKNOWN,
) -> None:
    timestamp = _dump_datetime(now)
    await connection.execute(
        """
        INSERT INTO users (id, created_at, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
        """,
        (user_id, timestamp, timestamp),
    )
    await connection.execute(
        """
        INSERT INTO relationships (id, user_id, stage, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, id) DO UPDATE SET
            stage = CASE
                WHEN excluded.stage = 'unknown' THEN relationships.stage
                ELSE excluded.stage
            END,
            updated_at = excluded.updated_at
        """,
        (relationship_id, user_id, stage.value, timestamp, timestamp),
    )


async def _fetchone(
    connection: aiosqlite.Connection,
    query: str,
    parameters: tuple | list,
) -> aiosqlite.Row | None:
    cursor = await connection.execute(query, parameters)
    row = await cursor.fetchone()
    await cursor.close()
    return row


async def _expire_due_memories(
    connection: aiosqlite.Connection,
    now: datetime,
) -> None:
    await connection.execute(
        """
        UPDATE memory_items
        SET status = ?, updated_at = ?
        WHERE status IN (?, ?)
          AND expires_at IS NOT NULL
          AND expires_at <= ?
        """,
        (
            MemoryStatus.EXPIRED.value,
            _dump_datetime(now),
            MemoryStatus.PROPOSED.value,
            MemoryStatus.CONFIRMED.value,
            _dump_datetime(now),
        ),
    )
    await connection.execute(
        """
        UPDATE relationship_plans
        SET status = ?, updated_at = ?
        WHERE status IN (?, ?)
          AND source_memory_id IN (
              SELECT id FROM memory_items WHERE status = ?
          )
        """,
        (
            PlanStatus.EXPIRED.value,
            _dump_datetime(now),
            PlanStatus.PROPOSED.value,
            PlanStatus.CONFIRMED.value,
            MemoryStatus.EXPIRED.value,
        ),
    )


async def _expire_due_relationship_plans(
    connection: aiosqlite.Connection,
    now: datetime,
) -> None:
    cursor = await connection.execute(
        """
        SELECT plan_id, source_memory_id FROM relationship_plans
        WHERE status IN ('proposed', 'confirmed')
          AND expires_at IS NOT NULL
          AND expires_at <= ?
        """,
        (_dump_datetime(now),),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    if not rows:
        return
    plan_ids = [row["plan_id"] for row in rows]
    source_ids = [row["source_memory_id"] for row in rows if row["source_memory_id"]]
    plan_placeholders = ",".join("?" for _ in plan_ids)
    await connection.execute(
        f"UPDATE relationship_plans SET status = ?, updated_at = ? "
        f"WHERE plan_id IN ({plan_placeholders})",
        (PlanStatus.EXPIRED.value, _dump_datetime(now), *plan_ids),
    )
    if source_ids:
        source_placeholders = ",".join("?" for _ in source_ids)
        await connection.execute(
            f"UPDATE memory_items SET status = ?, updated_at = ? "
            f"WHERE id IN ({source_placeholders}) "
            "AND status IN ('proposed', 'confirmed')",
            (MemoryStatus.EXPIRED.value, _dump_datetime(now), *source_ids),
        )


async def _sync_relationship_plans_in_transaction(
    connection: aiosqlite.Connection,
    *,
    user_id: str,
    relationship_id: str,
    now: datetime,
) -> list[RelationshipPlan]:
    await _expire_due_memories(connection, now)
    await _expire_due_relationship_plans(connection, now)
    cursor = await connection.execute(
        """
        SELECT * FROM memory_items
        WHERE user_id = ? AND relationship_id = ?
          AND kind = ? AND status IN ('proposed', 'confirmed')
        """,
        (user_id, relationship_id, MemoryKind.PLANNED_EVENT.value),
    )
    memory_rows = await cursor.fetchall()
    await cursor.close()
    for row in memory_rows:
        await _ensure_plan_for_memory_in_transaction(connection, _row_to_memory(row))
    cursor = await connection.execute(
        """
        SELECT * FROM relationship_plans
        WHERE user_id = ? AND relationship_id = ?
        ORDER BY updated_at DESC
        """,
        (user_id, relationship_id),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return [_row_to_relationship_plan(row) for row in rows]


async def _ensure_plan_for_memory_in_transaction(
    connection: aiosqlite.Connection,
    memory: MemoryItem,
) -> RelationshipPlan:
    existing = await _fetchone(
        connection,
        "SELECT * FROM relationship_plans WHERE source_memory_id = ?",
        (memory.id,),
    )
    if existing is not None:
        plan = _row_to_relationship_plan(existing)
    else:
        plan = relationship_plan_from_memory(memory)
        collision = await _fetchone(
            connection,
            "SELECT source_memory_id FROM relationship_plans WHERE plan_id = ?",
            (plan.plan_id,),
        )
        if collision is not None and collision["source_memory_id"] != memory.id:
            payload = dict(memory.payload)
            payload.pop("plan_id", None)
            memory = memory.model_copy(update={"payload": payload})
            plan = relationship_plan_from_memory(memory)
        await _upsert_relationship_plan(connection, plan)

    enriched = memory_with_plan(memory, plan)
    if enriched.payload != memory.payload:
        await connection.execute(
            "UPDATE memory_items SET payload_json = ? WHERE id = ?",
            (_dump_json(enriched.payload), memory.id),
        )
    return plan


async def _upsert_relationship_plan(
    connection: aiosqlite.Connection,
    plan: RelationshipPlan,
) -> None:
    await connection.execute(
        """
        INSERT INTO relationship_plans (
            plan_id, user_id, relationship_id, activity_type, participants_json,
            scheduled_start, scheduled_end, status, source_memory_id,
            source_message_id, created_at, updated_at, completed_at, cancelled_at,
            expires_at, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(plan_id) DO UPDATE SET
            activity_type = excluded.activity_type,
            participants_json = excluded.participants_json,
            scheduled_start = excluded.scheduled_start,
            scheduled_end = excluded.scheduled_end,
            source_memory_id = COALESCE(relationship_plans.source_memory_id,
                                        excluded.source_memory_id),
            source_message_id = COALESCE(relationship_plans.source_message_id,
                                         excluded.source_message_id),
            updated_at = excluded.updated_at,
            expires_at = excluded.expires_at,
            payload_json = excluded.payload_json
        """,
        (
            plan.plan_id,
            plan.user_id,
            plan.relationship_id,
            plan.activity_type,
            _dump_json(plan.participants),
            _dump_datetime(plan.scheduled_start),
            _dump_datetime(plan.scheduled_end),
            plan.status.value,
            plan.source_memory_id,
            plan.source_message_id,
            _dump_datetime(plan.created_at),
            _dump_datetime(plan.updated_at),
            _dump_datetime(plan.completed_at),
            _dump_datetime(plan.cancelled_at),
            _dump_datetime(plan.expires_at),
            _dump_json(plan.payload),
        ),
    )


async def _save_memory_in_transaction(
    *,
    connection: aiosqlite.Connection,
    user_id: str,
    relationship_id: str,
    candidate: MemoryCandidate,
    source_message_id: str | None,
    status: MemoryStatus,
    now: datetime,
) -> MemorySaveResult:
    dedupe_key = memory_dedupe_key(candidate)
    cursor = await connection.execute(
        """
        SELECT * FROM memory_items
        WHERE user_id = ? AND relationship_id = ? AND kind = ? AND subject = ?
          AND status IN ('proposed', 'confirmed')
        """,
        (user_id, relationship_id, candidate.kind.value, candidate.subject),
    )
    active_rows = await cursor.fetchall()
    await cursor.close()
    semantic_duplicates = [
        row
        for row in active_rows
        if memory_dedupe_key(_row_to_memory(row)) == dedupe_key
    ]
    duplicate = (
        max(semantic_duplicates, key=_memory_row_keeper_rank)
        if semantic_duplicates
        else None
    )
    if duplicate:
        duplicate_ids = [row["id"] for row in semantic_duplicates if row["id"] != duplicate["id"]]
        if duplicate_ids:
            placeholders = ",".join("?" for _ in duplicate_ids)
            await connection.execute(
                f"UPDATE memory_items SET status = ?, updated_at = ? "
                f"WHERE id IN ({placeholders})",
                (MemoryStatus.SUPERSEDED.value, _dump_datetime(now), *duplicate_ids),
            )
        merged_status = (
            MemoryStatus.CONFIRMED
            if status == MemoryStatus.CONFIRMED
            else MemoryStatus(duplicate["status"])
        )
        await connection.execute(
            """
            UPDATE memory_items
            SET status = ?, confidence = MAX(confidence, ?),
                importance = MAX(importance, ?), updated_at = ?, dedupe_key = ?
            WHERE id = ?
            """,
            (
                merged_status.value,
                candidate.confidence,
                candidate.importance,
                _dump_datetime(now),
                dedupe_key,
                duplicate["id"],
            ),
        )
        updated = await _fetchone(
            connection,
            "SELECT * FROM memory_items WHERE id = ?",
            (duplicate["id"],),
        )
        if updated is None:
            raise RuntimeError("memory disappeared after deduplication update")
        return MemorySaveResult(item=_row_to_memory(updated), created=False)

    duplicate = await _fetchone(
        connection,
        """
        SELECT * FROM memory_items
        WHERE user_id = ? AND relationship_id = ? AND dedupe_key = ?
          AND status IN ('proposed', 'confirmed')
        """,
        (user_id, relationship_id, dedupe_key),
    )
    if duplicate:
        raise RuntimeError("dedupe key collision was not resolved by semantic matching")

    if candidate.supersedes_id:
        previous = await _fetchone(
            connection,
            """
            SELECT id FROM memory_items
            WHERE id = ? AND user_id = ? AND relationship_id = ?
              AND status IN ('proposed', 'confirmed')
            """,
            (candidate.supersedes_id, user_id, relationship_id),
        )
        if previous is None:
            raise ValueError(
                "The superseded memory is missing or outside the current relationship scope."
            )
        await connection.execute(
            "UPDATE memory_items SET status = ?, updated_at = ? WHERE id = ?",
            (
                MemoryStatus.SUPERSEDED.value,
                _dump_datetime(now),
                candidate.supersedes_id,
            ),
        )
        timestamp = _dump_datetime(now)
        await connection.execute(
            """
            UPDATE relationship_plans
            SET status = ?, updated_at = ?, cancelled_at = ?
            WHERE source_memory_id = ? AND status IN ('proposed', 'confirmed')
            """,
            (
                PlanStatus.CANCELLED.value,
                timestamp,
                timestamp,
                candidate.supersedes_id,
            ),
        )

    memory_id = str(uuid4())
    await connection.execute(
        """
        INSERT INTO memory_items (
            id, user_id, relationship_id, kind, subject, summary, original_text,
            evidence_spans_json, time_kind, occurred_at, period_start, period_end,
            temporal_precision, valence, relationship_impact, intensity, emotions_json,
            importance, perspective, confidence, status, payload_json, source_message_id,
            created_at, updated_at, expires_at, last_used_at, supersedes_id, dedupe_key
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?
        )
        """,
        _memory_values(
            memory_id=memory_id,
            user_id=user_id,
            relationship_id=relationship_id,
            candidate=candidate,
            status=status,
            source_message_id=source_message_id,
            dedupe_key=dedupe_key,
            now=now,
        ),
    )
    row = await _fetchone(
        connection,
        "SELECT * FROM memory_items WHERE id = ?",
        (memory_id,),
    )
    if row is None:
        raise RuntimeError("memory was not readable after insertion")
    return MemorySaveResult(item=_row_to_memory(row), created=True)


def _memory_row_keeper_rank(row: aiosqlite.Row) -> tuple[int, int, float, str]:
    return (
        int(row["status"] == MemoryStatus.CONFIRMED.value),
        int(row["importance"]),
        float(row["confidence"]),
        str(row["updated_at"]),
    )


def _memory_values(
    *,
    memory_id: str,
    user_id: str,
    relationship_id: str,
    candidate: MemoryCandidate,
    status: MemoryStatus,
    source_message_id: str | None,
    dedupe_key: str,
    now: datetime,
) -> tuple:
    return (
        memory_id,
        user_id,
        relationship_id,
        candidate.kind.value,
        candidate.subject,
        candidate.summary,
        candidate.original_text,
        _dump_json(candidate.evidence_spans),
        candidate.time_kind.value,
        _dump_datetime(candidate.occurred_at),
        _dump_datetime(candidate.period_start),
        _dump_datetime(candidate.period_end),
        candidate.temporal_precision.value,
        candidate.valence.value,
        candidate.relationship_impact.value,
        candidate.intensity,
        _dump_json(candidate.emotions),
        candidate.importance,
        candidate.perspective.value,
        candidate.confidence,
        status.value,
        _dump_json(candidate.payload),
        source_message_id,
        _dump_datetime(now),
        _dump_datetime(now),
        _dump_datetime(candidate.expires_at),
        None,
        candidate.supersedes_id,
        dedupe_key,
    )


def _row_to_memory(row: aiosqlite.Row) -> MemoryItem:
    return MemoryItem(
        id=row["id"],
        user_id=row["user_id"],
        relationship_id=row["relationship_id"],
        kind=row["kind"],
        subject=row["subject"],
        summary=row["summary"],
        original_text=row["original_text"],
        evidence_spans=json.loads(row["evidence_spans_json"]),
        time_kind=row["time_kind"],
        occurred_at=_load_datetime(row["occurred_at"]),
        period_start=_load_datetime(row["period_start"]),
        period_end=_load_datetime(row["period_end"]),
        temporal_precision=row["temporal_precision"],
        valence=row["valence"],
        relationship_impact=row["relationship_impact"],
        intensity=row["intensity"],
        emotions=json.loads(row["emotions_json"]),
        importance=row["importance"],
        perspective=row["perspective"],
        confidence=row["confidence"],
        status=row["status"],
        payload=json.loads(row["payload_json"]),
        source_message_id=row["source_message_id"],
        created_at=_load_datetime(row["created_at"]),
        updated_at=_load_datetime(row["updated_at"]),
        expires_at=_load_datetime(row["expires_at"]),
        last_used_at=_load_datetime(row["last_used_at"]),
        supersedes_id=row["supersedes_id"],
        dedupe_key=row["dedupe_key"],
    )


def _row_to_relationship_plan(row: aiosqlite.Row) -> RelationshipPlan:
    return RelationshipPlan(
        plan_id=row["plan_id"],
        user_id=row["user_id"],
        relationship_id=row["relationship_id"],
        activity_type=row["activity_type"],
        participants=json.loads(row["participants_json"]),
        scheduled_start=_load_datetime(row["scheduled_start"]),
        scheduled_end=_load_datetime(row["scheduled_end"]),
        status=row["status"],
        source_memory_id=row["source_memory_id"],
        source_message_id=row["source_message_id"],
        created_at=_load_datetime(row["created_at"]),
        updated_at=_load_datetime(row["updated_at"]),
        completed_at=_load_datetime(row["completed_at"]),
        cancelled_at=_load_datetime(row["cancelled_at"]),
        expires_at=_load_datetime(row["expires_at"]),
        payload=json.loads(row["payload_json"]),
    )


def _row_to_extraction_run(row: aiosqlite.Row) -> MemoryExtractionRun:
    from loveapp.domain.memory import MemoryGateDecision

    return MemoryExtractionRun(
        id=row["id"],
        user_id=row["user_id"],
        relationship_id=row["relationship_id"],
        conversation_id=row["conversation_id"],
        source_message_id=row["source_message_id"],
        status=row["status"],
        gate_decision=MemoryGateDecision(
            should_extract=bool(row["gate_should_extract"]),
            reason=row["gate_reason"],
            signals=json.loads(row["gate_signals_json"]),
        ),
        attempts=json.loads(row["attempts_json"]),
        saved_memory_ids=json.loads(row["saved_memory_ids_json"]),
        discarded_spans=json.loads(row["discarded_spans_json"]),
        error=row["error"],
        created_at=_load_datetime(row["created_at"]),
        updated_at=_load_datetime(row["updated_at"]),
        completed_at=_load_datetime(row["completed_at"]),
    )


def _dump_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _load_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _dump_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


async def _migrate_schema(connection: aiosqlite.Connection) -> None:
    cursor = await connection.execute("PRAGMA table_info(memory_items)")
    columns = {row[1] for row in await cursor.fetchall()}
    await cursor.close()
    if "evidence_spans_json" not in columns:
        await connection.execute(
            """
            ALTER TABLE memory_items
            ADD COLUMN evidence_spans_json TEXT NOT NULL DEFAULT '[]'
            """
        )
        cursor = await connection.execute("SELECT id, original_text FROM memory_items")
        rows = await cursor.fetchall()
        await cursor.close()
        await connection.executemany(
            "UPDATE memory_items SET evidence_spans_json = ? WHERE id = ?",
            [(_dump_json([row["original_text"]]), row["id"]) for row in rows],
        )
    await connection.execute(
        "UPDATE memory_items SET kind = ? WHERE kind = ?",
        (MemoryKind.INTERACTION_EVENT.value, "interaction_episode"),
    )
    await connection.execute(
        "UPDATE memory_items SET kind = ? WHERE kind = ?",
        (MemoryKind.INTERACTION_PATTERN.value, "interaction_trend"),
    )
    cursor = await connection.execute("PRAGMA table_info(memory_extraction_runs)")
    run_columns = {row[1] for row in await cursor.fetchall()}
    await cursor.close()
    if "discarded_spans_json" not in run_columns:
        await connection.execute(
            """
            ALTER TABLE memory_extraction_runs
            ADD COLUMN discarded_spans_json TEXT NOT NULL DEFAULT '[]'
            """
        )
    await connection.execute("PRAGMA user_version = 5")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relationships (
    id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    relationship_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id, relationship_id)
        REFERENCES relationships(user_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    relationship_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id, relationship_id)
        REFERENCES relationships(user_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memory_items (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    relationship_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    summary TEXT NOT NULL,
    original_text TEXT NOT NULL,
    evidence_spans_json TEXT NOT NULL DEFAULT '[]',
    time_kind TEXT NOT NULL,
    occurred_at TEXT,
    period_start TEXT,
    period_end TEXT,
    temporal_precision TEXT NOT NULL,
    valence TEXT NOT NULL,
    relationship_impact TEXT NOT NULL,
    intensity INTEGER,
    emotions_json TEXT NOT NULL DEFAULT '[]',
    importance INTEGER NOT NULL,
    perspective TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    source_message_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT,
    last_used_at TEXT,
    supersedes_id TEXT,
    dedupe_key TEXT NOT NULL,
    FOREIGN KEY (user_id, relationship_id)
        REFERENCES relationships(user_id, id) ON DELETE CASCADE,
    FOREIGN KEY (source_message_id) REFERENCES messages(id) ON DELETE SET NULL,
    FOREIGN KEY (supersedes_id) REFERENCES memory_items(id) ON DELETE SET NULL,
    CHECK (importance BETWEEN 1 AND 5),
    CHECK (intensity IS NULL OR intensity BETWEEN 1 AND 5),
    CHECK (confidence BETWEEN 0 AND 1)
);

CREATE TABLE IF NOT EXISTS relationship_plans (
    plan_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    relationship_id TEXT NOT NULL,
    activity_type TEXT NOT NULL,
    participants_json TEXT NOT NULL DEFAULT '[]',
    scheduled_start TEXT,
    scheduled_end TEXT,
    status TEXT NOT NULL,
    source_memory_id TEXT UNIQUE,
    source_message_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    cancelled_at TEXT,
    expires_at TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (user_id, relationship_id)
        REFERENCES relationships(user_id, id) ON DELETE CASCADE,
    FOREIGN KEY (source_memory_id) REFERENCES memory_items(id) ON DELETE SET NULL,
    FOREIGN KEY (source_message_id) REFERENCES messages(id) ON DELETE SET NULL,
    CHECK (status IN ('proposed', 'confirmed', 'completed', 'cancelled', 'expired'))
);

CREATE TABLE IF NOT EXISTS memory_extraction_runs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    relationship_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    source_message_id TEXT NOT NULL,
    status TEXT NOT NULL,
    gate_should_extract INTEGER NOT NULL,
    gate_reason TEXT NOT NULL,
    gate_signals_json TEXT NOT NULL DEFAULT '[]',
    attempts_json TEXT NOT NULL DEFAULT '[]',
    saved_memory_ids_json TEXT NOT NULL DEFAULT '[]',
    discarded_spans_json TEXT NOT NULL DEFAULT '[]',
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (user_id, relationship_id)
        REFERENCES relationships(user_id, id) ON DELETE CASCADE,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    FOREIGN KEY (source_message_id) REFERENCES messages(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_memory_scope_status
    ON memory_items(user_id, relationship_id, status);
CREATE INDEX IF NOT EXISTS idx_memory_kind
    ON memory_items(user_id, relationship_id, kind);
CREATE INDEX IF NOT EXISTS idx_memory_temporal
    ON memory_items(user_id, relationship_id, occurred_at, period_end);
CREATE INDEX IF NOT EXISTS idx_relationship_plans_scope_status
    ON relationship_plans(user_id, relationship_id, status);
CREATE INDEX IF NOT EXISTS idx_relationship_plans_schedule
    ON relationship_plans(user_id, relationship_id, scheduled_start, scheduled_end);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_active_dedupe
    ON memory_items(user_id, relationship_id, dedupe_key)
    WHERE status IN ('proposed', 'confirmed');
CREATE INDEX IF NOT EXISTS idx_messages_scope
    ON messages(user_id, relationship_id, created_at);

CREATE INDEX IF NOT EXISTS idx_memory_extraction_runs_scope
    ON memory_extraction_runs(user_id, relationship_id, updated_at);

PRAGMA user_version = 5;
"""

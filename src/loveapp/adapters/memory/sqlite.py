import asyncio
import json
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite

from loveapp.domain.advice import (
    MAX_ADVICE_GENERATIONS,
    AdviceGenerationAttempt,
    AdviceGenerationAttemptRecord,
    AdviceLogicalTurn,
    AdviceLogicalTurnStatus,
    AdviceTurnClaimError,
    RelationshipContext,
)
from loveapp.domain.contextual_memory import apply_contextual_memory_update
from loveapp.domain.enums import RelationshipStage
from loveapp.domain.memory import (
    AdmissionDecision,
    ClaimRelation,
    MemoryCandidate,
    MemoryExtractionRun,
    MemoryItem,
    MemoryKind,
    MemorySaveResult,
    MemoryStatus,
    MessageRole,
    StoredMessage,
    memory_dedupe_key,
    normalize_candidate_predicate,
    utc_now,
)
from loveapp.domain.memory_context import attach_memories, select_context_memories
from loveapp.domain.memory_predicates import normalize_predicate
from loveapp.domain.memory_write import (
    MemoryTransitionAudit,
    MemoryWriteBatch,
    MemoryWriteBatchResult,
    resolve_operation_target_ids,
)
from loveapp.domain.relationship_plan import (
    PlanStatus,
    RelationshipPlan,
    can_transition_plan_status,
    memory_with_plan,
    relationship_plan_from_memory,
)


class SQLiteMemoryStore:
    def __init__(
        self,
        database_path: Path,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._database_path = database_path
        self._clock = clock
        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    def set_clock(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock

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
                await connection.executescript(f"BEGIN IMMEDIATE;\n{_SCHEMA}")
                await _migrate_schema(connection)
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
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
        message_id: str | None = None,
    ) -> StoredMessage:
        await self.initialize()
        now = self._clock()
        message_id = message_id or str(uuid4())
        connection = await self._open_connection()
        try:
            await connection.execute("BEGIN IMMEDIATE")
            await _ensure_scope(connection, user_id, relationship_id, now)
            existing_message = await _fetchone(
                connection,
                "SELECT * FROM messages WHERE id = ?",
                (message_id,),
            )
            if existing_message is not None:
                expected_conversation = conversation_id or existing_message["conversation_id"]
                if (
                    existing_message["user_id"],
                    existing_message["relationship_id"],
                    existing_message["conversation_id"],
                    existing_message["role"],
                    existing_message["content"],
                ) != (
                    user_id,
                    relationship_id,
                    expected_conversation,
                    role.value,
                    content,
                ):
                    raise ValueError(
                        "message_id belongs to different message content or scope"
                    )
                await connection.commit()
                return _row_to_stored_message(existing_message)
            conversation_id = conversation_id or str(uuid4())
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
        except Exception:
            await connection.rollback()
            raise
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
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
        """
        connection = await self._open_connection()
        try:
            cursor = await connection.execute(query, values)
            rows = list(reversed(await cursor.fetchall()))
            await cursor.close()
            return [_row_to_stored_message(row) for row in rows]
        finally:
            await connection.close()

    async def create_advice_logical_turn(
        self,
        turn: AdviceLogicalTurn,
        *,
        reject_existing: bool = False,
    ) -> AdviceLogicalTurn:
        turn.assert_initial_state()
        await self.initialize()
        connection = await self._open_connection()
        try:
            await connection.execute("BEGIN IMMEDIATE")
            message = await _fetchone(
                connection,
                "SELECT * FROM messages WHERE id = ?",
                (turn.user_message_id,),
            )
            if message is None:
                raise ValueError("logical turn user message does not exist")
            if (
                message["user_id"],
                message["relationship_id"],
                message["conversation_id"],
                message["role"],
                message["content"],
            ) != (
                turn.user_id,
                turn.relationship_id,
                turn.conversation_id,
                MessageRole.USER.value,
                turn.query,
            ):
                raise ValueError("logical turn user message content or scope mismatch")
            cursor = await connection.execute(
                """
                INSERT INTO advice_logical_turns (
                    id, user_id, relationship_id, conversation_id, user_message_id,
                    query, request_json, status, assistant_message_id,
                    generation_count, last_error_type, fallback_used,
                    created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    turn.id,
                    turn.user_id,
                    turn.relationship_id,
                    turn.conversation_id,
                    turn.user_message_id,
                    turn.query,
                    _dump_json(turn.request_payload),
                    turn.status.value,
                    turn.assistant_message_id,
                    turn.generation_count,
                    turn.last_error_type,
                    int(turn.fallback_used),
                    _dump_datetime(turn.created_at),
                    _dump_datetime(turn.updated_at),
                    _dump_datetime(turn.completed_at),
                ),
            )
            created = cursor.rowcount == 1
            await cursor.close()
            row = await _fetchone(
                connection,
                "SELECT * FROM advice_logical_turns WHERE id = ?",
                (turn.id,),
            )
            await connection.commit()
            if row is None:
                raise RuntimeError("logical turn disappeared during creation")
            existing = _row_to_advice_logical_turn(row)
            if (
                existing.user_id,
                existing.relationship_id,
                existing.conversation_id,
                existing.user_message_id,
                existing.query,
                existing.request_payload,
            ) != (
                turn.user_id,
                turn.relationship_id,
                turn.conversation_id,
                turn.user_message_id,
                turn.query,
                turn.request_payload,
            ):
                raise ValueError("logical_turn_id belongs to different content or scope")
            if not created and reject_existing:
                raise AdviceTurnClaimError(
                    "logical turn is already owned by another generation"
                )
            return existing
        except sqlite3.IntegrityError as exc:
            await connection.rollback()
            if "advice_logical_turns.user_message_id" in str(exc):
                raise ValueError(
                    "user_message_id already belongs to another logical turn"
                ) from exc
            raise
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def get_advice_logical_turn(
        self,
        logical_turn_id: str,
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str,
    ) -> AdviceLogicalTurn | None:
        await self.initialize()
        connection = await self._open_connection()
        try:
            row = await _fetchone(
                connection,
                """
                SELECT * FROM advice_logical_turns
                WHERE id = ? AND user_id = ? AND relationship_id = ?
                  AND conversation_id = ?
                """,
                (logical_turn_id, user_id, relationship_id, conversation_id),
            )
            return _row_to_advice_logical_turn(row) if row is not None else None
        finally:
            await connection.close()

    async def latest_retryable_advice_turn(
        self,
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str,
    ) -> AdviceLogicalTurn | None:
        await self.initialize()
        connection = await self._open_connection()
        try:
            row = await _fetchone(
                connection,
                """
                SELECT * FROM advice_logical_turns
                WHERE user_id = ? AND relationship_id = ? AND conversation_id = ?
                  AND status = ? AND generation_count < ?
                ORDER BY updated_at DESC, rowid DESC
                LIMIT 1
                """,
                (
                    user_id,
                    relationship_id,
                    conversation_id,
                    AdviceLogicalTurnStatus.GENERATION_FAILED.value,
                    MAX_ADVICE_GENERATIONS,
                ),
            )
            return _row_to_advice_logical_turn(row) if row is not None else None
        finally:
            await connection.close()

    async def begin_advice_generation(
        self,
        logical_turn_id: str,
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str,
        retry: bool,
    ) -> AdviceLogicalTurn:
        await self.initialize()
        connection = await self._open_connection()
        try:
            await connection.execute("BEGIN IMMEDIATE")
            row = await _fetchone(
                connection,
                "SELECT * FROM advice_logical_turns WHERE id = ?",
                (logical_turn_id,),
            )
            if row is None:
                raise ValueError("logical turn does not exist")
            turn = _row_to_advice_logical_turn(row)
            _require_advice_turn_scope(
                turn,
                user_id=user_id,
                relationship_id=relationship_id,
                conversation_id=conversation_id,
            )
            expected = (
                AdviceLogicalTurnStatus.GENERATION_FAILED
                if retry
                else AdviceLogicalTurnStatus.MEMORY_STARTED
            )
            if turn.status != expected:
                raise AdviceTurnClaimError(
                    "logical turn is not eligible for generation"
                )
            if turn.generation_count >= MAX_ADVICE_GENERATIONS:
                raise AdviceTurnClaimError("logical turn generation limit reached")
            now = self._clock()
            await connection.execute(
                """
                UPDATE advice_logical_turns
                SET status = ?, generation_count = generation_count + 1,
                    last_error_type = NULL, fallback_used = 0, updated_at = ?
                WHERE id = ?
                """,
                (
                    AdviceLogicalTurnStatus.GENERATION_IN_PROGRESS.value,
                    _dump_datetime(now),
                    logical_turn_id,
                ),
            )
            updated = await _fetchone(
                connection,
                "SELECT * FROM advice_logical_turns WHERE id = ?",
                (logical_turn_id,),
            )
            await connection.commit()
            if updated is None:
                raise RuntimeError("logical turn disappeared during generation claim")
            return _row_to_advice_logical_turn(updated)
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def save_advice_generation_attempts(
        self,
        logical_turn_id: str,
        generation_no: int,
        attempts: list[AdviceGenerationAttempt],
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str,
    ) -> list[AdviceGenerationAttemptRecord]:
        await self.initialize()
        connection = await self._open_connection()
        saved: list[AdviceGenerationAttemptRecord] = []
        try:
            await connection.execute("BEGIN IMMEDIATE")
            row = await _fetchone(
                connection,
                "SELECT * FROM advice_logical_turns WHERE id = ?",
                (logical_turn_id,),
            )
            if row is None:
                raise ValueError("logical turn does not exist")
            turn = _row_to_advice_logical_turn(row)
            _require_advice_turn_scope(
                turn,
                user_id=user_id,
                relationship_id=relationship_id,
                conversation_id=conversation_id,
            )
            if (
                turn.status != AdviceLogicalTurnStatus.GENERATION_IN_PROGRESS
                or generation_no != turn.generation_count
            ):
                raise ValueError(
                    "generation attempts do not belong to active generation"
                )
            now = self._clock()
            for attempt in attempts:
                existing = await _fetchone(
                    connection,
                    """
                    SELECT * FROM advice_generation_attempts
                    WHERE logical_turn_id = ? AND generation_no = ? AND attempt_no = ?
                    """,
                    (logical_turn_id, generation_no, attempt.attempt),
                )
                if existing is not None:
                    record = _row_to_advice_generation_attempt(existing)
                    if record.attempt != attempt:
                        raise ValueError("generation attempt identity collision")
                    saved.append(record)
                    continue
                record = AdviceGenerationAttemptRecord(
                    id=str(uuid4()),
                    logical_turn_id=logical_turn_id,
                    generation_no=generation_no,
                    attempt=attempt,
                    created_at=now,
                )
                await connection.execute(
                    """
                    INSERT INTO advice_generation_attempts (
                        id, logical_turn_id, generation_no, attempt_no,
                        attempt_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        logical_turn_id,
                        generation_no,
                        attempt.attempt,
                        _dump_json(attempt.model_dump(mode="json")),
                        _dump_datetime(now),
                    ),
                )
                saved.append(record)
            await connection.commit()
            return saved
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def fail_advice_logical_turn(
        self,
        logical_turn_id: str,
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str,
        last_error_type: str | None = None,
        fallback_used: bool = False,
    ) -> AdviceLogicalTurn | None:
        await self.initialize()
        connection = await self._open_connection()
        try:
            await connection.execute("BEGIN IMMEDIATE")
            row = await _fetchone(
                connection,
                "SELECT * FROM advice_logical_turns WHERE id = ?",
                (logical_turn_id,),
            )
            if row is None:
                return None
            turn = _row_to_advice_logical_turn(row)
            _require_advice_turn_scope(
                turn,
                user_id=user_id,
                relationship_id=relationship_id,
                conversation_id=conversation_id,
            )
            if turn.status not in {
                AdviceLogicalTurnStatus.MEMORY_STARTED,
                AdviceLogicalTurnStatus.GENERATION_IN_PROGRESS,
            }:
                raise ValueError("logical turn cannot fail from its current state")
            if not last_error_type:
                raise ValueError("failed logical turn requires last_error_type")
            now = self._clock()
            await connection.execute(
                """
                UPDATE advice_logical_turns
                SET status = ?, last_error_type = ?, fallback_used = ?,
                    updated_at = ?, completed_at = NULL
                WHERE id = ?
                """,
                (
                    AdviceLogicalTurnStatus.GENERATION_FAILED.value,
                    last_error_type,
                    int(fallback_used),
                    _dump_datetime(now),
                    logical_turn_id,
                ),
            )
            updated = await _fetchone(
                connection,
                "SELECT * FROM advice_logical_turns WHERE id = ?",
                (logical_turn_id,),
            )
            await connection.commit()
            return _row_to_advice_logical_turn(updated) if updated is not None else None
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def list_advice_generation_attempts(
        self,
        logical_turn_id: str,
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str,
    ) -> list[AdviceGenerationAttemptRecord]:
        await self.initialize()
        connection = await self._open_connection()
        try:
            row = await _fetchone(
                connection,
                "SELECT * FROM advice_logical_turns WHERE id = ?",
                (logical_turn_id,),
            )
            if row is None:
                return []
            _require_advice_turn_scope(
                _row_to_advice_logical_turn(row),
                user_id=user_id,
                relationship_id=relationship_id,
                conversation_id=conversation_id,
            )
            cursor = await connection.execute(
                """
                SELECT * FROM advice_generation_attempts
                WHERE logical_turn_id = ?
                ORDER BY generation_no, attempt_no
                """,
                (logical_turn_id,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [_row_to_advice_generation_attempt(row) for row in rows]
        finally:
            await connection.close()

    async def complete_advice_logical_turn(
        self,
        logical_turn_id: str,
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str,
        message_id: str,
        content: str,
    ) -> tuple[AdviceLogicalTurn, StoredMessage]:
        await self.initialize()
        connection = await self._open_connection()
        try:
            await connection.execute("BEGIN IMMEDIATE")
            row = await _fetchone(
                connection,
                "SELECT * FROM advice_logical_turns WHERE id = ?",
                (logical_turn_id,),
            )
            if row is None:
                raise ValueError("logical turn does not exist")
            turn = _row_to_advice_logical_turn(row)
            _require_advice_turn_scope(
                turn,
                user_id=user_id,
                relationship_id=relationship_id,
                conversation_id=conversation_id,
            )
            if turn.status == AdviceLogicalTurnStatus.COMPLETED:
                if turn.assistant_message_id != message_id:
                    raise ValueError("logical turn already completed with another message")
                message_row = await _fetchone(
                    connection,
                    "SELECT * FROM messages WHERE id = ?",
                    (message_id,),
                )
                if message_row is None or (
                    message_row["conversation_id"],
                    message_row["user_id"],
                    message_row["relationship_id"],
                    message_row["role"],
                    message_row["content"],
                ) != (
                    turn.conversation_id,
                    turn.user_id,
                    turn.relationship_id,
                    MessageRole.ASSISTANT.value,
                    content,
                ):
                    raise ValueError("assistant message identity collision")
                await connection.commit()
                return turn, _row_to_stored_message(message_row)
            if turn.status != AdviceLogicalTurnStatus.GENERATION_IN_PROGRESS:
                raise ValueError("logical turn is not eligible for completion")

            existing_message = await _fetchone(
                connection,
                "SELECT * FROM messages WHERE id = ?",
                (message_id,),
            )
            if existing_message is not None:
                if (
                    existing_message["conversation_id"],
                    existing_message["user_id"],
                    existing_message["relationship_id"],
                    existing_message["role"],
                    existing_message["content"],
                ) != (
                    turn.conversation_id,
                    turn.user_id,
                    turn.relationship_id,
                    MessageRole.ASSISTANT.value,
                    content,
                ):
                    raise ValueError("assistant message identity collision")
            else:
                now = self._clock()
                await connection.execute(
                    """
                    INSERT INTO messages (
                        id, conversation_id, user_id, relationship_id,
                        role, content, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        turn.conversation_id,
                        turn.user_id,
                        turn.relationship_id,
                        MessageRole.ASSISTANT.value,
                        content,
                        _dump_datetime(now),
                    ),
                )
                existing_message = await _fetchone(
                    connection,
                    "SELECT * FROM messages WHERE id = ?",
                    (message_id,),
                )
            now = self._clock()
            await connection.execute(
                """
                UPDATE advice_logical_turns
                SET status = ?, assistant_message_id = ?, last_error_type = NULL,
                    fallback_used = 0, updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    AdviceLogicalTurnStatus.COMPLETED.value,
                    message_id,
                    _dump_datetime(now),
                    _dump_datetime(now),
                    logical_turn_id,
                ),
            )
            completed_row = await _fetchone(
                connection,
                "SELECT * FROM advice_logical_turns WHERE id = ?",
                (logical_turn_id,),
            )
            await connection.commit()
            if completed_row is None or existing_message is None:
                raise RuntimeError("logical turn disappeared during completion")
            return (
                _row_to_advice_logical_turn(completed_row),
                _row_to_stored_message(existing_message),
            )
        except Exception:
            await connection.rollback()
            raise
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
        now = self._clock()
        candidates = [normalize_candidate_predicate(candidate) for candidate in candidates]
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

    async def commit_memory_batch(
        self,
        *,
        user_id: str,
        relationship_id: str,
        batch: MemoryWriteBatch,
    ) -> MemoryWriteBatchResult:
        await self.initialize()
        now = self._clock()
        connection = await self._open_connection()
        try:
            await connection.execute("BEGIN IMMEDIATE")
            await _ensure_scope(connection, user_id, relationship_id, now)
            await _expire_due_memories(connection, now)
            results: list[MemorySaveResult] = []
            updated_memory_ids: list[str] = []
            implicit_audits: list[MemoryTransitionAudit] = []
            for operation in batch.operations:
                candidate = normalize_candidate_predicate(operation.candidate).model_copy(
                    update={"supersedes_id": None}
                )
                result = await _save_memory_in_transaction(
                    connection=connection,
                    user_id=user_id,
                    relationship_id=relationship_id,
                    candidate=candidate,
                    source_message_id=batch.source_message_id,
                    status=operation.status,
                    now=now,
                )
                results.append(result)
                if result.item.kind == MemoryKind.PLANNED_EVENT:
                    await _ensure_plan_for_memory_in_transaction(connection, result.item)

            for contextual_update in batch.contextual_updates:
                target_row = await _fetchone(
                    connection,
                    """
                    SELECT * FROM memory_items
                    WHERE id = ? AND user_id = ? AND relationship_id = ?
                    """,
                    (
                        contextual_update.target_memory_id,
                        user_id,
                        relationship_id,
                    ),
                )
                if target_row is None:
                    raise ValueError(
                        "contextual update target is outside the current relationship scope"
                    )
                target = _row_to_memory(target_row)
                updated = apply_contextual_memory_update(
                    target,
                    contextual_update,
                    updated_at=now,
                )
                await connection.execute(
                    """
                    UPDATE memory_items
                    SET evidence_spans_json = ?, time_kind = ?, period_end = ?,
                        temporal_precision = ?, payload_json = ?, claim_relation = ?,
                        updated_at = ?, last_seen_at = ?
                    WHERE id = ? AND user_id = ? AND relationship_id = ?
                    """,
                    (
                        _dump_json(updated.evidence_spans),
                        updated.time_kind.value,
                        _dump_datetime(updated.period_end),
                        updated.temporal_precision.value,
                        _dump_json(updated.payload),
                        updated.claim_relation.value if updated.claim_relation else None,
                        _dump_datetime(updated.updated_at),
                        _dump_datetime(updated.last_seen_at),
                        updated.id,
                        user_id,
                        relationship_id,
                    ),
                )
                updated_memory_ids.append(updated.id)
                audit = MemoryTransitionAudit(
                    user_id=user_id,
                    relationship_id=relationship_id,
                    source_message_id=batch.source_message_id,
                    incoming_memory_id=updated.id,
                    target_memory_ids=[updated.id],
                    relation=ClaimRelation.UPDATE,
                    decision=updated.admission_decision or AdmissionDecision.PROPOSE,
                    rule_name=f"contextual_{contextual_update.update_type.value}_update",
                    admission_score=updated.admission_score,
                    score_breakdown={
                        "contextual_update_type": contextual_update.update_type.value,
                        "temporal_expression": contextual_update.temporal_expression,
                        "duration_value": contextual_update.duration_value,
                        "duration_unit": contextual_update.duration_unit,
                    },
                    raw_predicate=updated.raw_predicate,
                    canonical_predicate=updated.canonical_predicate,
                    extractor_model=updated.extractor_model,
                    verifier_model=updated.verifier_model,
                    prompt_version=updated.prompt_version,
                    evidence=[contextual_update.evidence_span],
                    reason=contextual_update.reason,
                    created_at=now,
                )
                await _insert_transition_audit(connection, audit)
                implicit_audits.append(audit)

            for update in batch.plan_updates:
                source_event_memory_id = (
                    results[update.candidate_index].item.id
                    if update.candidate_index is not None
                    and update.candidate_index < len(results)
                    else None
                )
                await _set_plan_status_in_transaction(
                    connection,
                    plan_id=update.plan_id,
                    user_id=user_id,
                    relationship_id=relationship_id,
                    status=update.status,
                    transitioned_at=update.transitioned_at or now,
                    source_event_memory_id=source_event_memory_id,
                )

            saved_memory_ids = [result.item.id for result in results]
            resolved_targets: list[list[str]] = []
            for index, operation in enumerate(batch.operations):
                operation_targets = resolve_operation_target_ids(
                    operation,
                    saved_memory_ids,
                    operation_index=index,
                )
                resolved_targets.append(operation_targets)
                transition_targets = (
                    operation_targets
                    if operation.relation == ClaimRelation.UPDATE
                    else []
                )
                for memory_id in transition_targets:
                    if memory_id == results[index].item.id:
                        continue
                    target = await _fetchone(
                        connection,
                        "SELECT status FROM memory_items WHERE id = ?",
                        (memory_id,),
                    )
                    if (
                        operation.status == MemoryStatus.PROPOSED
                        and target is not None
                        and target["status"] == MemoryStatus.CONFIRMED.value
                    ):
                        raise ValueError(
                            "a proposed memory cannot supersede a confirmed memory"
                        )
                    implicit_audits.extend(
                        await _set_memory_status_in_transaction(
                            connection,
                            memory_id=memory_id,
                            user_id=user_id,
                            relationship_id=relationship_id,
                            status=operation.target_status,
                            now=now,
                        )
                    )
                if transition_targets:
                    await connection.execute(
                        "UPDATE memory_items SET supersedes_id = ? WHERE id = ?",
                        (transition_targets[0], results[index].item.id),
                    )

            for update in batch.status_updates:
                implicit_audits.extend(
                    await _set_memory_status_in_transaction(
                        connection,
                        memory_id=update.memory_id,
                        user_id=user_id,
                        relationship_id=relationship_id,
                        status=update.status,
                        now=now,
                    )
                )

            audits: list[MemoryTransitionAudit] = list(implicit_audits)
            for index, operation in enumerate(batch.operations):
                candidate = normalize_candidate_predicate(operation.candidate)
                audit = MemoryTransitionAudit(
                    user_id=user_id,
                    relationship_id=relationship_id,
                    source_message_id=batch.source_message_id,
                    incoming_memory_id=results[index].item.id,
                    target_memory_ids=resolved_targets[index],
                    relation=operation.relation,
                    decision=(
                        candidate.admission_decision or AdmissionDecision.PROPOSE
                    ),
                    rule_name=operation.rule_name,
                    admission_score=candidate.admission_score,
                    score_breakdown=operation.score_breakdown,
                    raw_predicate=candidate.raw_predicate,
                    canonical_predicate=candidate.canonical_predicate,
                    extractor_model=candidate.extractor_model,
                    verifier_model=candidate.verifier_model,
                    prompt_version=candidate.prompt_version,
                    evidence=candidate.evidence_spans,
                    reason=operation.reason,
                    created_at=now,
                )
                await _insert_transition_audit(connection, audit)
                audits.append(audit)
            for draft in batch.audit_only:
                audit = MemoryTransitionAudit(
                    user_id=user_id,
                    relationship_id=relationship_id,
                    source_message_id=batch.source_message_id,
                    target_memory_ids=draft.target_memory_ids,
                    relation=draft.relation,
                    decision=draft.decision,
                    rule_name=draft.rule_name,
                    admission_score=draft.admission_score,
                    score_breakdown=draft.score_breakdown,
                    raw_predicate=draft.raw_predicate,
                    canonical_predicate=draft.canonical_predicate,
                    extractor_model=draft.extractor_model,
                    verifier_model=draft.verifier_model,
                    prompt_version=draft.prompt_version,
                    evidence=draft.evidence,
                    reason=draft.reason,
                    created_at=now,
                )
                await _insert_transition_audit(connection, audit)
                audits.append(audit)
            for update in batch.status_updates:
                audit = await _insert_memory_lifecycle_audit(
                    connection,
                    memory_id=update.memory_id,
                    user_id=user_id,
                    relationship_id=relationship_id,
                    target_status=update.status,
                    rule_name=update.rule_name,
                    reason=update.reason,
                    created_at=now,
                )
                audits.append(audit)
            for update in batch.plan_updates:
                source_event_memory_id = (
                    results[update.candidate_index].item.id
                    if update.candidate_index is not None
                    and update.candidate_index < len(results)
                    else None
                )
                audit = await _insert_plan_lifecycle_audit(
                    connection,
                    plan_id=update.plan_id,
                    user_id=user_id,
                    relationship_id=relationship_id,
                    incoming_memory_id=source_event_memory_id,
                    rule_name=f"relationship_plan_{update.status.value}",
                    reason="The relationship plan changed status in the atomic memory batch.",
                    created_at=now,
                )
                audits.append(audit)
            await connection.commit()
            refreshed = []
            for result in results:
                row = await _fetchone(
                    connection,
                    "SELECT * FROM memory_items WHERE id = ?",
                    (result.item.id,),
                )
                if row is None:
                    raise RuntimeError("memory disappeared after committing its write batch")
                refreshed.append(result.model_copy(update={"item": _row_to_memory(row)}))
            return MemoryWriteBatchResult(
                saved=refreshed,
                updated_memory_ids=updated_memory_ids,
                audits=audits,
            )
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
        read_only: bool = False,
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
            if not read_only:
                now = self._clock()
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
            now = self._clock()
            existing = await _fetchone(
                connection,
                "SELECT * FROM memory_items WHERE id = ? AND user_id = ?",
                (memory_id, user_id),
            )
            if existing is None:
                return None
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
            transitioned_plan_ids: list[str] = []
            if status == MemoryStatus.EXPIRED:
                target_plan_status = PlanStatus.EXPIRED
            elif status in {MemoryStatus.REJECTED, MemoryStatus.SUPERSEDED}:
                target_plan_status = PlanStatus.CANCELLED
            if target_plan_status is not None:
                cursor = await connection.execute(
                    """
                    SELECT plan_id FROM relationship_plans
                    WHERE source_memory_id = ? AND user_id = ?
                      AND status IN ('proposed', 'confirmed')
                    """,
                    (memory_id, user_id),
                )
                transitioned_plan_ids = [row["plan_id"] for row in await cursor.fetchall()]
                await cursor.close()
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
                for plan_id in transitioned_plan_ids:
                    await _insert_plan_lifecycle_audit(
                        connection,
                        plan_id=plan_id,
                        user_id=user_id,
                        relationship_id=existing["relationship_id"],
                        rule_name="plan_source_memory_status_changed",
                        reason=(
                            "The source planned-event memory left its active lifecycle state."
                        ),
                        created_at=now,
                    )
            if existing["status"] != status.value:
                await _insert_memory_lifecycle_audit(
                    connection,
                    memory_id=memory_id,
                    user_id=user_id,
                    relationship_id=existing["relationship_id"],
                    target_status=status,
                    rule_name="set_memory_status",
                    reason="The memory status was changed through the store lifecycle API.",
                    created_at=now,
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

    async def reset_relationship_scope(
        self,
        *,
        user_id: str,
        relationship_id: str,
    ) -> None:
        await self.initialize()
        connection = await self._open_connection()
        try:
            await connection.execute(
                "DELETE FROM memory_transition_audit "
                "WHERE user_id = ? AND relationship_id = ?",
                (user_id, relationship_id),
            )
            await connection.execute(
                "DELETE FROM relationships WHERE user_id = ? AND id = ?",
                (user_id, relationship_id),
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def get_relationship_context(
        self,
        user_id: str,
        relationship_id: str,
        limit: int = 20,
        read_only: bool = False,
    ) -> RelationshipContext | None:
        await self.initialize()
        connection = await self._open_connection()
        try:
            if read_only:
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
                return RelationshipContext(
                    user_id=user_id,
                    relationship_id=relationship_id,
                    relationship_stage=RelationshipStage(relationship["stage"]),
                )
            now = self._clock()
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
                (user_id, relationship_id, _dump_datetime(self._clock()), fetch_limit),
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
                last_used_at = _dump_datetime(self._clock())
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
            now = self._clock()
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
        read_only: bool = False,
    ) -> list[RelationshipPlan]:
        if not read_only:
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
            now = transitioned_at or self._clock()
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
            if current != status:
                await _insert_plan_lifecycle_audit(
                    connection,
                    plan_id=plan_id,
                    user_id=user_id,
                    relationship_id=row["relationship_id"],
                    incoming_memory_id=source_event_memory_id,
                    rule_name=f"set_relationship_plan_status:{status.value}",
                    reason="The plan status was changed through the store lifecycle API.",
                    created_at=now,
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
                now=self._clock(),
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
                    gate_matched_rule, gate_matched_span, gate_context_json,
                    attempts_json, saved_memory_ids_json, discarded_spans_json,
                    error, created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    gate_should_extract = excluded.gate_should_extract,
                    gate_reason = excluded.gate_reason,
                    gate_signals_json = excluded.gate_signals_json,
                    gate_matched_rule = excluded.gate_matched_rule,
                    gate_matched_span = excluded.gate_matched_span,
                    gate_context_json = excluded.gate_context_json,
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
                    run.gate_decision.matched_rule,
                    run.gate_decision.matched_span,
                    _dump_json(
                        {
                            "l0_route": (
                                run.gate_decision.l0_route.value
                                if run.gate_decision.l0_route is not None
                                else None
                            ),
                            "l0_semantic_hint": (
                                run.gate_decision.l0_semantic_hint.value
                                if run.gate_decision.l0_semantic_hint is not None
                                else None
                            ),
                            "semantic_gate_should_extract": (
                                run.gate_decision.semantic_gate_should_extract
                            ),
                            "semantic_gate_reason": (
                                run.gate_decision.semantic_gate_reason.value
                                if run.gate_decision.semantic_gate_reason is not None
                                else None
                            ),
                            "semantic_gate_contract_violation": (
                                run.gate_decision.semantic_gate_contract_violation
                            ),
                            "semantic_gate_contract_violation_reason": (
                                run.gate_decision.semantic_gate_contract_violation_reason
                            ),
                            "extraction_warning": (
                                run.gate_decision.extraction_warning
                            ),
                            "contextual_probe": run.gate_decision.contextual_probe,
                            "history_loaded_for_gate": run.gate_decision.history_loaded_for_gate,
                            "antecedent_candidate_ids": run.gate_decision.antecedent_candidate_ids,
                            "selected_target_memory_id": (
                                run.gate_decision.selected_target_memory_id
                            ),
                            "target_guard_result": run.gate_decision.target_guard_result,
                            "contextual_update_type": run.gate_decision.contextual_update_type,
                        }
                    ),
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

    async def list_transition_audits(
        self,
        *,
        user_id: str,
        relationship_id: str,
        source_message_id: str | None = None,
        limit: int = 100,
    ) -> list[MemoryTransitionAudit]:
        await self.initialize()
        clauses = ["user_id = ?", "relationship_id = ?"]
        values: list[str | int] = [user_id, relationship_id]
        if source_message_id is not None:
            clauses.append("source_message_id = ?")
            values.append(source_message_id)
        values.append(limit)
        connection = await self._open_connection()
        try:
            cursor = await connection.execute(
                f"""
                SELECT * FROM memory_transition_audit
                WHERE {" AND ".join(clauses)}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                values,
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [_row_to_transition_audit(row) for row in rows]
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
    cursor = await connection.execute(
        """
        SELECT * FROM memory_items
        WHERE status IN (?, ?)
          AND expires_at IS NOT NULL
          AND expires_at <= ?
        """,
        (
            MemoryStatus.PROPOSED.value,
            MemoryStatus.CONFIRMED.value,
            _dump_datetime(now),
        ),
    )
    due_rows = await cursor.fetchall()
    await cursor.close()
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
    cursor = await connection.execute(
        """
        SELECT plan_id, user_id, relationship_id
        FROM relationship_plans
        WHERE status IN (?, ?)
          AND source_memory_id IN (
              SELECT id FROM memory_items WHERE status = ?
          )
        """,
        (
            PlanStatus.PROPOSED.value,
            PlanStatus.CONFIRMED.value,
            MemoryStatus.EXPIRED.value,
        ),
    )
    due_plan_rows = await cursor.fetchall()
    await cursor.close()
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
    for row in due_rows:
        await _insert_memory_lifecycle_audit(
            connection,
            memory_id=row["id"],
            user_id=row["user_id"],
            relationship_id=row["relationship_id"],
            target_status=MemoryStatus.EXPIRED,
            rule_name="ttl_expired",
            reason="The memory reached its configured expiration time.",
            created_at=now,
        )
    for row in due_plan_rows:
        await _insert_plan_lifecycle_audit(
            connection,
            plan_id=row["plan_id"],
            user_id=row["user_id"],
            relationship_id=row["relationship_id"],
            rule_name="plan_source_memory_expired",
            reason="The source planned-event memory expired.",
            created_at=now,
        )


async def _expire_due_relationship_plans(
    connection: aiosqlite.Connection,
    now: datetime,
) -> None:
    cursor = await connection.execute(
        """
        SELECT plan_id, source_memory_id, user_id, relationship_id
        FROM relationship_plans
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
    for row in rows:
        await _insert_plan_lifecycle_audit(
            connection,
            plan_id=row["plan_id"],
            user_id=row["user_id"],
            relationship_id=row["relationship_id"],
            rule_name="plan_ttl_expired",
            reason="The relationship plan reached its configured expiration time.",
            created_at=now,
        )
        if row["source_memory_id"] is not None:
            source = await _fetchone(
                connection,
                "SELECT status FROM memory_items WHERE id = ?",
                (row["source_memory_id"],),
            )
            if source is not None and source["status"] == MemoryStatus.EXPIRED.value:
                await _insert_memory_lifecycle_audit(
                    connection,
                    memory_id=row["source_memory_id"],
                    user_id=row["user_id"],
                    relationship_id=row["relationship_id"],
                    target_status=MemoryStatus.EXPIRED,
                    rule_name="plan_ttl_expired",
                    reason="The linked relationship plan expired.",
                    created_at=now,
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


async def _set_memory_status_in_transaction(
    connection: aiosqlite.Connection,
    *,
    memory_id: str,
    user_id: str,
    relationship_id: str,
    status: MemoryStatus,
    now: datetime,
) -> list[MemoryTransitionAudit]:
    row = await _fetchone(
        connection,
        """
        SELECT id, kind, status FROM memory_items
        WHERE id = ? AND user_id = ? AND relationship_id = ?
        """,
        (memory_id, user_id, relationship_id),
    )
    if row is None:
        raise ValueError("memory transition target is outside the current relationship scope")
    if row["status"] == status.value:
        return []
    if row["status"] not in {MemoryStatus.PROPOSED.value, MemoryStatus.CONFIRMED.value}:
        raise ValueError("memory transition target is no longer active")
    await connection.execute(
        "UPDATE memory_items SET status = ?, updated_at = ? WHERE id = ?",
        (status.value, _dump_datetime(now), memory_id),
    )
    if row["kind"] != MemoryKind.PLANNED_EVENT.value:
        return []
    plan_status = None
    if status == MemoryStatus.EXPIRED:
        plan_status = PlanStatus.EXPIRED
    elif status in {MemoryStatus.REJECTED, MemoryStatus.SUPERSEDED}:
        plan_status = PlanStatus.CANCELLED
    if plan_status is not None:
        cursor = await connection.execute(
            """
            SELECT plan_id FROM relationship_plans
            WHERE source_memory_id = ? AND user_id = ? AND relationship_id = ?
              AND status IN ('proposed', 'confirmed')
            """,
            (memory_id, user_id, relationship_id),
        )
        plan_rows = await cursor.fetchall()
        await cursor.close()
        await connection.execute(
            """
            UPDATE relationship_plans
            SET status = ?, updated_at = ?,
                cancelled_at = CASE WHEN ? = 'cancelled' THEN ? ELSE cancelled_at END
            WHERE source_memory_id = ? AND user_id = ? AND relationship_id = ?
              AND status IN ('proposed', 'confirmed')
            """,
            (
                plan_status.value,
                _dump_datetime(now),
                plan_status.value,
                _dump_datetime(now),
                memory_id,
                user_id,
                relationship_id,
            ),
        )
        return [
            await _insert_plan_lifecycle_audit(
                connection,
                plan_id=plan_row["plan_id"],
                user_id=user_id,
                relationship_id=relationship_id,
                rule_name="plan_source_memory_status_changed",
                reason="The source planned-event memory left its active lifecycle state.",
                created_at=now,
            )
            for plan_row in plan_rows
        ]
    return []


async def _set_plan_status_in_transaction(
    connection: aiosqlite.Connection,
    *,
    plan_id: str,
    user_id: str,
    relationship_id: str,
    status: PlanStatus,
    transitioned_at: datetime,
    source_event_memory_id: str | None,
) -> None:
    row = await _fetchone(
        connection,
        """
        SELECT * FROM relationship_plans
        WHERE plan_id = ? AND user_id = ? AND relationship_id = ?
        """,
        (plan_id, user_id, relationship_id),
    )
    if row is None:
        raise ValueError("relationship plan transition target is outside the current scope")
    current = PlanStatus(row["status"])
    if current != status and not can_transition_plan_status(current, status):
        raise ValueError(f"invalid relationship plan transition: {current} -> {status}")
    payload = json.loads(row["payload_json"])
    if source_event_memory_id is not None:
        payload["terminal_event_memory_id"] = source_event_memory_id
    completed_at = transitioned_at if status == PlanStatus.COMPLETED else None
    cancelled_at = transitioned_at if status == PlanStatus.CANCELLED else None
    await connection.execute(
        """
        UPDATE relationship_plans
        SET status = ?, updated_at = ?,
            completed_at = COALESCE(?, completed_at),
            cancelled_at = COALESCE(?, cancelled_at),
            payload_json = ?
        WHERE plan_id = ?
        """,
        (
            status.value,
            _dump_datetime(transitioned_at),
            _dump_datetime(completed_at),
            _dump_datetime(cancelled_at),
            _dump_json(payload),
            plan_id,
        ),
    )
    source_memory_id = row["source_memory_id"]
    if source_memory_id is None:
        return
    source = await _fetchone(
        connection,
        "SELECT payload_json, status FROM memory_items WHERE id = ?",
        (source_memory_id,),
    )
    if source is None:
        return
    source_payload = json.loads(source["payload_json"])
    source_payload["plan_status"] = status.value
    memory_status = source["status"]
    if status in {PlanStatus.COMPLETED, PlanStatus.CANCELLED}:
        memory_status = MemoryStatus.SUPERSEDED.value
    elif status == PlanStatus.EXPIRED:
        memory_status = MemoryStatus.EXPIRED.value
    await connection.execute(
        """
        UPDATE memory_items
        SET payload_json = ?, status = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            _dump_json(source_payload),
            memory_status,
            _dump_datetime(transitioned_at),
            source_memory_id,
        ),
    )


async def _insert_transition_audit(
    connection: aiosqlite.Connection,
    audit: MemoryTransitionAudit,
) -> None:
    await connection.execute(
        """
        INSERT INTO memory_transition_audit (
            id, user_id, relationship_id, source_message_id, incoming_memory_id,
            target_memory_ids_json, relation, decision, rule_name, admission_score,
            score_breakdown_json, raw_predicate, canonical_predicate, extractor_model,
            verifier_model, prompt_version, evidence_json, reason, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audit.id,
            audit.user_id,
            audit.relationship_id,
            audit.source_message_id,
            audit.incoming_memory_id,
            _dump_json(audit.target_memory_ids),
            audit.relation.value,
            audit.decision.value,
            audit.rule_name,
            audit.admission_score,
            _dump_json(audit.score_breakdown),
            audit.raw_predicate,
            audit.canonical_predicate,
            audit.extractor_model,
            audit.verifier_model,
            audit.prompt_version,
            _dump_json(audit.evidence),
            audit.reason,
            _dump_datetime(audit.created_at),
        ),
    )


async def _insert_memory_lifecycle_audit(
    connection: aiosqlite.Connection,
    *,
    memory_id: str,
    user_id: str,
    relationship_id: str,
    target_status: MemoryStatus,
    rule_name: str,
    reason: str,
    created_at: datetime,
) -> MemoryTransitionAudit:
    row = await _fetchone(
        connection,
        """
        SELECT * FROM memory_items
        WHERE id = ? AND user_id = ? AND relationship_id = ?
        """,
        (memory_id, user_id, relationship_id),
    )
    if row is None:
        raise ValueError("memory audit target is outside the current relationship scope")
    item = _row_to_memory(row)
    audit = MemoryTransitionAudit(
        user_id=user_id,
        relationship_id=relationship_id,
        source_message_id=item.source_message_id,
        target_memory_ids=[memory_id],
        relation=ClaimRelation.UPDATE,
        decision=item.admission_decision or AdmissionDecision.PROPOSE,
        rule_name=rule_name,
        admission_score=item.admission_score,
        score_breakdown={"target_memory_status": target_status.value},
        raw_predicate=item.raw_predicate,
        canonical_predicate=item.canonical_predicate,
        extractor_model=item.extractor_model,
        verifier_model=item.verifier_model,
        prompt_version=item.prompt_version,
        evidence=item.evidence_spans,
        reason=reason,
        created_at=created_at,
    )
    await _insert_transition_audit(connection, audit)
    return audit


async def _insert_plan_lifecycle_audit(
    connection: aiosqlite.Connection,
    *,
    plan_id: str,
    user_id: str,
    relationship_id: str,
    rule_name: str,
    reason: str,
    created_at: datetime,
    incoming_memory_id: str | None = None,
) -> MemoryTransitionAudit:
    row = await _fetchone(
        connection,
        """
        SELECT * FROM relationship_plans
        WHERE plan_id = ? AND user_id = ? AND relationship_id = ?
        """,
        (plan_id, user_id, relationship_id),
    )
    if row is None:
        raise ValueError("plan audit target is outside the current relationship scope")
    source_row = None
    if row["source_memory_id"] is not None:
        source_row = await _fetchone(
            connection,
            "SELECT * FROM memory_items WHERE id = ?",
            (row["source_memory_id"],),
        )
    source = _row_to_memory(source_row) if source_row is not None else None
    audit = MemoryTransitionAudit(
        user_id=user_id,
        relationship_id=relationship_id,
        source_message_id=row["source_message_id"],
        incoming_memory_id=incoming_memory_id,
        target_memory_ids=[source.id] if source is not None else [],
        relation=ClaimRelation.UPDATE,
        decision=(
            source.admission_decision
            if source is not None and source.admission_decision is not None
            else AdmissionDecision.PROPOSE
        ),
        rule_name=rule_name,
        admission_score=source.admission_score if source is not None else None,
        score_breakdown={
            "plan_id": plan_id,
            "target_plan_status": row["status"],
        },
        raw_predicate="plan.status",
        canonical_predicate="plan.status",
        extractor_model=source.extractor_model if source is not None else None,
        verifier_model=source.verifier_model if source is not None else None,
        prompt_version=source.prompt_version if source is not None else None,
        evidence=source.evidence_spans if source is not None else [],
        reason=reason,
        created_at=created_at,
    )
    await _insert_transition_audit(connection, audit)
    return audit


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
    if source_message_id is not None:
        idempotent = await _fetchone(
            connection,
            """
            SELECT * FROM memory_items
            WHERE source_message_id = ? AND dedupe_key = ?
            """,
            (source_message_id, dedupe_key),
        )
        if idempotent is not None:
            return MemorySaveResult(item=_row_to_memory(idempotent), created=False)
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
        existing_evidence = json.loads(duplicate["evidence_spans_json"])
        merged_evidence = list(
            dict.fromkeys([*existing_evidence, *candidate.evidence_spans])
        )[:8]
        explicitness = max(
            (str(duplicate["explicitness"]), candidate.explicitness.value),
            key=_explicitness_rank,
        )
        await connection.execute(
            """
            UPDATE memory_items
            SET status = ?, confidence = MAX(confidence, ?),
                importance = MAX(importance, ?), updated_at = ?, last_seen_at = ?,
                evidence_spans_json = ?, dedupe_key = ?,
                canonical_predicate = COALESCE(canonical_predicate, ?),
                raw_predicate = COALESCE(raw_predicate, ?),
                predicate_type = CASE WHEN ? = 'canonical' THEN ? ELSE predicate_type END,
                custom_predicate = CASE WHEN ? = 'canonical' THEN NULL
                                        ELSE COALESCE(custom_predicate, ?) END,
                state_dimension = COALESCE(state_dimension, ?),
                state_value = COALESCE(state_value, ?),
                explicitness = ?,
                admission_score = MAX(COALESCE(admission_score, 0), COALESCE(?, 0)),
                admission_decision = COALESCE(?, admission_decision),
                claim_relation = COALESCE(?, claim_relation),
                prompt_version = COALESCE(?, prompt_version),
                extractor_model = COALESCE(?, extractor_model),
                verifier_model = COALESCE(?, verifier_model)
            WHERE id = ?
            """,
            (
                merged_status.value,
                candidate.confidence,
                candidate.importance,
                _dump_datetime(now),
                _dump_datetime(now),
                _dump_json(merged_evidence),
                dedupe_key,
                candidate.canonical_predicate,
                candidate.raw_predicate,
                candidate.predicate_type.value,
                candidate.predicate_type.value,
                candidate.predicate_type.value,
                candidate.custom_predicate,
                candidate.state_dimension,
                candidate.state_value,
                explicitness,
                candidate.admission_score,
                (
                    candidate.admission_decision.value
                    if candidate.admission_decision is not None
                    else None
                ),
                candidate.claim_relation.value if candidate.claim_relation else None,
                candidate.prompt_version,
                candidate.extractor_model,
                candidate.verifier_model,
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
            importance, perspective, confidence, status, payload_json,
            canonical_predicate, raw_predicate, predicate_type, custom_predicate,
            state_dimension, state_value, explicitness, requires_inference,
            admission_score, admission_decision, claim_relation,
            lifecycle_review_required, last_seen_at, prompt_version, extractor_model,
            verifier_model, source_message_id,
            created_at, updated_at, expires_at, last_used_at, supersedes_id, dedupe_key
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
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


def _explicitness_rank(value: str) -> int:
    return {
        "speculative": 0,
        "weakly_inferred": 1,
        "strongly_implied": 2,
        "explicit": 3,
    }.get(str(value), 0)


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
        candidate.canonical_predicate,
        candidate.raw_predicate,
        candidate.predicate_type.value,
        candidate.custom_predicate,
        candidate.state_dimension,
        candidate.state_value,
        candidate.explicitness.value,
        int(candidate.requires_inference),
        candidate.admission_score,
        (
            candidate.admission_decision.value
            if candidate.admission_decision is not None
            else None
        ),
        candidate.claim_relation.value if candidate.claim_relation is not None else None,
        int(candidate.lifecycle_review_required),
        _dump_datetime(now),
        candidate.prompt_version,
        candidate.extractor_model,
        candidate.verifier_model,
        source_message_id,
        _dump_datetime(now),
        _dump_datetime(now),
        _dump_datetime(candidate.expires_at),
        None,
        candidate.supersedes_id,
        dedupe_key,
    )


def _row_to_stored_message(row: aiosqlite.Row) -> StoredMessage:
    return StoredMessage(
        id=row["id"],
        conversation_id=row["conversation_id"],
        user_id=row["user_id"],
        relationship_id=row["relationship_id"],
        role=row["role"],
        content=row["content"],
        created_at=_load_datetime(row["created_at"]),
    )


def _require_advice_turn_scope(
    turn: AdviceLogicalTurn,
    *,
    user_id: str,
    relationship_id: str,
    conversation_id: str,
) -> None:
    if not turn.is_in_scope(
        user_id=user_id,
        relationship_id=relationship_id,
        conversation_id=conversation_id,
    ):
        raise ValueError("logical turn belongs to different scope")


def _row_to_advice_logical_turn(row: aiosqlite.Row) -> AdviceLogicalTurn:
    return AdviceLogicalTurn(
        id=row["id"],
        user_id=row["user_id"],
        relationship_id=row["relationship_id"],
        conversation_id=row["conversation_id"],
        user_message_id=row["user_message_id"],
        query=row["query"],
        request_payload=json.loads(row["request_json"]),
        status=row["status"],
        assistant_message_id=row["assistant_message_id"],
        generation_count=row["generation_count"],
        last_error_type=row["last_error_type"],
        fallback_used=bool(row["fallback_used"]),
        created_at=_load_datetime(row["created_at"]),
        updated_at=_load_datetime(row["updated_at"]),
        completed_at=_load_datetime(row["completed_at"]),
    )


def _row_to_advice_generation_attempt(
    row: aiosqlite.Row,
) -> AdviceGenerationAttemptRecord:
    return AdviceGenerationAttemptRecord(
        id=row["id"],
        logical_turn_id=row["logical_turn_id"],
        generation_no=row["generation_no"],
        attempt=json.loads(row["attempt_json"]),
        created_at=_load_datetime(row["created_at"]),
    )


def _row_to_memory(row: aiosqlite.Row) -> MemoryItem:
    item = MemoryItem(
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
        canonical_predicate=row["canonical_predicate"],
        raw_predicate=row["raw_predicate"],
        predicate_type=row["predicate_type"],
        custom_predicate=row["custom_predicate"],
        state_dimension=row["state_dimension"],
        state_value=row["state_value"],
        explicitness=row["explicitness"],
        requires_inference=bool(row["requires_inference"]),
        admission_score=row["admission_score"],
        admission_decision=row["admission_decision"],
        claim_relation=row["claim_relation"],
        lifecycle_review_required=bool(row["lifecycle_review_required"]),
        prompt_version=row["prompt_version"],
        extractor_model=row["extractor_model"],
        verifier_model=row["verifier_model"],
        source_message_id=row["source_message_id"],
        created_at=_load_datetime(row["created_at"]),
        updated_at=_load_datetime(row["updated_at"]),
        expires_at=_load_datetime(row["expires_at"]),
        last_used_at=_load_datetime(row["last_used_at"]),
        last_seen_at=_load_datetime(row["last_seen_at"]),
        supersedes_id=row["supersedes_id"],
        dedupe_key=row["dedupe_key"],
    )
    return normalize_candidate_predicate(item)


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
            matched_rule=row["gate_matched_rule"],
            matched_span=row["gate_matched_span"],
            **json.loads(row["gate_context_json"]),
        ),
        attempts=json.loads(row["attempts_json"]),
        saved_memory_ids=json.loads(row["saved_memory_ids_json"]),
        discarded_spans=json.loads(row["discarded_spans_json"]),
        error=row["error"],
        created_at=_load_datetime(row["created_at"]),
        updated_at=_load_datetime(row["updated_at"]),
        completed_at=_load_datetime(row["completed_at"]),
    )


def _row_to_transition_audit(row: aiosqlite.Row) -> MemoryTransitionAudit:
    return MemoryTransitionAudit(
        id=row["id"],
        user_id=row["user_id"],
        relationship_id=row["relationship_id"],
        source_message_id=row["source_message_id"],
        incoming_memory_id=row["incoming_memory_id"],
        target_memory_ids=json.loads(row["target_memory_ids_json"]),
        relation=row["relation"],
        decision=row["decision"],
        rule_name=row["rule_name"],
        admission_score=row["admission_score"],
        score_breakdown=json.loads(row["score_breakdown_json"]),
        raw_predicate=row["raw_predicate"],
        canonical_predicate=row["canonical_predicate"],
        extractor_model=row["extractor_model"],
        verifier_model=row["verifier_model"],
        prompt_version=row["prompt_version"],
        evidence=json.loads(row["evidence_json"]),
        reason=row["reason"],
        created_at=_load_datetime(row["created_at"]),
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
    memory_v2_columns = {
        "canonical_predicate": "TEXT",
        "raw_predicate": "TEXT",
        "predicate_type": "TEXT NOT NULL DEFAULT 'custom'",
        "custom_predicate": "TEXT",
        "state_dimension": "TEXT",
        "state_value": "TEXT",
        "explicitness": "TEXT NOT NULL DEFAULT 'strongly_implied'",
        "requires_inference": "INTEGER NOT NULL DEFAULT 0",
        "admission_score": "REAL",
        "admission_decision": "TEXT",
        "claim_relation": "TEXT",
        "lifecycle_review_required": "INTEGER NOT NULL DEFAULT 0",
        "last_seen_at": "TEXT",
        "prompt_version": "TEXT",
        "extractor_model": "TEXT",
        "verifier_model": "TEXT",
    }
    for column, declaration in memory_v2_columns.items():
        if column not in columns:
            await connection.execute(
                f"ALTER TABLE memory_items ADD COLUMN {column} {declaration}"
            )
    cursor = await connection.execute(
        """
        SELECT id, kind, payload_json, raw_predicate, canonical_predicate,
               custom_predicate, predicate_type
        FROM memory_items
        """
    )
    memory_rows = await cursor.fetchall()
    await cursor.close()
    normalization_updates = []
    for row in memory_rows:
        payload = json.loads(row["payload_json"])
        normalized = normalize_predicate(
            kind=row["kind"],
            raw_predicate=row["raw_predicate"] or payload.get("predicate"),
            canonical_predicate=row["canonical_predicate"],
            custom_predicate=row["custom_predicate"],
            predicate_type=row["predicate_type"],
            payload=payload,
        )
        normalization_updates.append(
            (
                normalized.raw_predicate,
                normalized.predicate_type,
                normalized.canonical_predicate,
                normalized.custom_predicate,
                normalized.state_dimension,
                normalized.state_value,
                int(normalized.predicate_type == "custom"),
                row["id"],
            )
        )
    if normalization_updates:
        await connection.executemany(
            """
            UPDATE memory_items
            SET raw_predicate = ?, predicate_type = ?, canonical_predicate = ?,
                custom_predicate = ?, state_dimension = ?, state_value = ?,
                lifecycle_review_required = CASE
                    WHEN lifecycle_review_required = 1 THEN 1 ELSE ? END,
                last_seen_at = COALESCE(last_seen_at, updated_at)
            WHERE id = ?
            """,
            normalization_updates,
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
    run_columns_to_add = {
        "gate_matched_rule": "TEXT",
        "gate_matched_span": "TEXT",
        "gate_context_json": "TEXT NOT NULL DEFAULT '{}'",
    }
    for column, declaration in run_columns_to_add.items():
        if column not in run_columns:
            await connection.execute(
                f"ALTER TABLE memory_extraction_runs ADD COLUMN {column} {declaration}"
            )
    await connection.execute("PRAGMA user_version = 8")


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

CREATE TABLE IF NOT EXISTS advice_logical_turns (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    relationship_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    user_message_id TEXT NOT NULL UNIQUE,
    query TEXT NOT NULL,
    request_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    assistant_message_id TEXT UNIQUE,
    generation_count INTEGER NOT NULL DEFAULT 0,
    last_error_type TEXT,
    fallback_used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id, relationship_id)
        REFERENCES relationships(user_id, id) ON DELETE CASCADE,
    FOREIGN KEY (user_message_id) REFERENCES messages(id) ON DELETE CASCADE,
    FOREIGN KEY (assistant_message_id) REFERENCES messages(id) ON DELETE SET NULL,
    CHECK (status IN (
        'memory_started', 'generation_in_progress', 'generation_failed', 'completed'
    )),
    CHECK (generation_count >= 0),
    CHECK (fallback_used IN (0, 1))
);

CREATE TABLE IF NOT EXISTS advice_generation_attempts (
    id TEXT PRIMARY KEY,
    logical_turn_id TEXT NOT NULL,
    generation_no INTEGER NOT NULL,
    attempt_no INTEGER NOT NULL,
    attempt_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (logical_turn_id) REFERENCES advice_logical_turns(id) ON DELETE CASCADE,
    UNIQUE (logical_turn_id, generation_no, attempt_no),
    CHECK (generation_no >= 1),
    CHECK (attempt_no >= 1)
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
    canonical_predicate TEXT,
    raw_predicate TEXT,
    predicate_type TEXT NOT NULL DEFAULT 'custom',
    custom_predicate TEXT,
    state_dimension TEXT,
    state_value TEXT,
    explicitness TEXT NOT NULL DEFAULT 'strongly_implied',
    requires_inference INTEGER NOT NULL DEFAULT 0,
    admission_score REAL,
    admission_decision TEXT,
    claim_relation TEXT,
    lifecycle_review_required INTEGER NOT NULL DEFAULT 0,
    last_seen_at TEXT,
    prompt_version TEXT,
    extractor_model TEXT,
    verifier_model TEXT,
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
    gate_matched_rule TEXT,
    gate_matched_span TEXT,
    gate_context_json TEXT NOT NULL DEFAULT '{}',
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

CREATE TABLE IF NOT EXISTS memory_transition_audit (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    relationship_id TEXT NOT NULL,
    source_message_id TEXT,
    incoming_memory_id TEXT,
    target_memory_ids_json TEXT NOT NULL DEFAULT '[]',
    relation TEXT NOT NULL,
    decision TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    admission_score REAL,
    score_breakdown_json TEXT NOT NULL DEFAULT '{}',
    raw_predicate TEXT,
    canonical_predicate TEXT,
    extractor_model TEXT,
    verifier_model TEXT,
    prompt_version TEXT,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id, relationship_id)
        REFERENCES relationships(user_id, id) ON DELETE CASCADE,
    FOREIGN KEY (source_message_id) REFERENCES messages(id) ON DELETE SET NULL,
    FOREIGN KEY (incoming_memory_id) REFERENCES memory_items(id) ON DELETE SET NULL
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
CREATE INDEX IF NOT EXISTS idx_memory_message_identity
    ON memory_items(source_message_id, dedupe_key)
    WHERE source_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_messages_scope
    ON messages(user_id, relationship_id, created_at);
CREATE INDEX IF NOT EXISTS idx_advice_logical_turns_retry
    ON advice_logical_turns(
        user_id, relationship_id, conversation_id, status, updated_at
    );
CREATE INDEX IF NOT EXISTS idx_advice_generation_attempts_turn
    ON advice_generation_attempts(logical_turn_id, generation_no, attempt_no);

CREATE INDEX IF NOT EXISTS idx_memory_extraction_runs_scope
    ON memory_extraction_runs(user_id, relationship_id, updated_at);

CREATE INDEX IF NOT EXISTS idx_memory_transition_audit_scope
    ON memory_transition_audit(user_id, relationship_id, created_at);

PRAGMA user_version = 8;
"""

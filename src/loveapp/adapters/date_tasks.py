import asyncio
import json
from pathlib import Path

import aiosqlite

from loveapp.application.date_planning.state_projection import project_requirements_to_state
from loveapp.domain.date_task import DatePlanningTaskState


class InMemoryDatePlanningTaskStore:
    def __init__(self) -> None:
        self._states: dict[tuple[str, str, str], DatePlanningTaskState] = {}

    async def get(
        self,
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str,
    ) -> DatePlanningTaskState | None:
        state = self._states.get((user_id, relationship_id, conversation_id))
        if state is None:
            return None
        normalized = project_requirements_to_state(state, state.requirements)
        return normalized.model_copy(deep=True)

    async def save(self, state: DatePlanningTaskState) -> DatePlanningTaskState:
        state = project_requirements_to_state(state, state.requirements)
        copied = state.model_copy(deep=True)
        self._states[(state.user_id, state.relationship_id, state.conversation_id)] = copied
        return copied.model_copy(deep=True)

    async def delete(
        self,
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str,
    ) -> bool:
        return self._states.pop((user_id, relationship_id, conversation_id), None) is not None

    async def aclose(self) -> None:
        return None


class SQLiteDatePlanningTaskStore:
    """Persistent task state stored beside the existing memory database."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    async def _initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            async with aiosqlite.connect(self._database_path) as connection:
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS date_planning_tasks (
                        user_id TEXT NOT NULL,
                        relationship_id TEXT NOT NULL,
                        conversation_id TEXT NOT NULL,
                        state_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (user_id, relationship_id, conversation_id)
                    )
                    """
                )
                await connection.commit()
            self._initialized = True

    async def get(
        self,
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str,
    ) -> DatePlanningTaskState | None:
        await self._initialize()
        async with aiosqlite.connect(self._database_path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT state_json FROM date_planning_tasks
                WHERE user_id = ? AND relationship_id = ? AND conversation_id = ?
                """,
                (user_id, relationship_id, conversation_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
        if row is None:
            return None
        state = DatePlanningTaskState.model_validate_json(row["state_json"])
        return project_requirements_to_state(state, state.requirements)

    async def save(self, state: DatePlanningTaskState) -> DatePlanningTaskState:
        await self._initialize()
        state = project_requirements_to_state(state, state.requirements)
        payload = state.model_dump(mode="json")
        async with aiosqlite.connect(self._database_path) as connection:
            await connection.execute(
                """
                INSERT INTO date_planning_tasks (
                    user_id, relationship_id, conversation_id, state_json, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, relationship_id, conversation_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (
                    state.user_id,
                    state.relationship_id,
                    state.conversation_id,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    state.updated_at.isoformat(),
                ),
            )
            await connection.commit()
        return state.model_copy(deep=True)

    async def delete(
        self,
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str,
    ) -> bool:
        await self._initialize()
        async with aiosqlite.connect(self._database_path) as connection:
            cursor = await connection.execute(
                """
                DELETE FROM date_planning_tasks
                WHERE user_id = ? AND relationship_id = ? AND conversation_id = ?
                """,
                (user_id, relationship_id, conversation_id),
            )
            await connection.commit()
            return cursor.rowcount > 0

    async def aclose(self) -> None:
        return None

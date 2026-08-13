from pathlib import Path

import pytest

from loveapp.adapters.conversation_states import (
    InMemoryConversationFlowStateStore,
    SQLiteConversationFlowStateStore,
)
from loveapp.domain.conversation import ConversationFlowState
from loveapp.domain.enums import TaskType


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_conversation_flow_state_store_persists_scope_isolation(
    backend: str,
    tmp_path: Path,
) -> None:
    store = (
        InMemoryConversationFlowStateStore()
        if backend == "memory"
        else SQLiteConversationFlowStateStore(tmp_path / "flow.db")
    )
    state = ConversationFlowState(
        user_id="u1",
        relationship_id="r1",
        conversation_id="c1",
        pending_task=TaskType.DATE_PLANNING,
        pending_task_turns_remaining=2,
    )

    await store.save(state)
    loaded = await store.get(user_id="u1", relationship_id="r1", conversation_id="c1")
    isolated = await store.get(user_id="u2", relationship_id="r1", conversation_id="c1")
    deleted = await store.delete(user_id="u1", relationship_id="r1", conversation_id="c1")

    assert loaded is not None and loaded.pending_task == TaskType.DATE_PLANNING
    assert isolated is None
    assert deleted is True
    assert await store.get(user_id="u1", relationship_id="r1", conversation_id="c1") is None
    await store.aclose()

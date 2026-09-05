import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore, SQLiteMemoryStore
from loveapp.domain.advice import (
    MAX_ADVICE_GENERATIONS,
    AdviceGenerationAttempt,
    AdviceGenerationErrorType,
    AdviceLogicalTurn,
    AdviceLogicalTurnStatus,
    AdviceTurnClaimError,
)
from loveapp.domain.memory import MessageRole


class _ManualClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 1, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self) -> None:
        self.value += timedelta(seconds=1)


@pytest.fixture(params=("memory", "sqlite"))
def logical_turn_store(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> tuple[InMemoryMemoryStore | SQLiteMemoryStore, _ManualClock]:
    clock = _ManualClock()
    if request.param == "memory":
        return InMemoryMemoryStore(clock=clock), clock
    return SQLiteMemoryStore(tmp_path / "advice-logical-turn.db", clock=clock), clock


async def _create_turn(
    store: InMemoryMemoryStore | SQLiteMemoryStore,
    clock: _ManualClock,
    *,
    logical_turn_id: str = "turn-1",
    user_id: str = "advice-user",
    relationship_id: str = "partner",
    conversation_id: str = "conversation",
    query: str = "我们已经说开了，现在和好了。",
) -> AdviceLogicalTurn:
    message = await store.add_message(
        user_id=user_id,
        relationship_id=relationship_id,
        conversation_id=conversation_id,
        role=MessageRole.USER,
        content=query,
        message_id=f"{logical_turn_id}-user",
    )
    turn = AdviceLogicalTurn(
        id=logical_turn_id,
        user_id=user_id,
        relationship_id=relationship_id,
        conversation_id=conversation_id,
        user_message_id=message.id,
        query=query,
        request_payload={"scenario": "conflict"},
        status=AdviceLogicalTurnStatus.MEMORY_STARTED,
        created_at=clock(),
        updated_at=clock(),
    )
    return await store.create_advice_logical_turn(turn)


def _attempt(
    attempt: int,
    *,
    status: str = "failed",
    error_type: AdviceGenerationErrorType | None = AdviceGenerationErrorType.JSON_DECODE_ERROR,
) -> AdviceGenerationAttempt:
    return AdviceGenerationAttempt(
        attempt=attempt,
        status=status,
        model="advice-test-model",
        thinking_mode="disabled",
        temperature=0,
        max_tokens=1024,
        finish_reason="stop",
        content_length=24,
        parse_error_type=error_type,
        error=error_type.value if error_type is not None else None,
    )


def _turn_scope(turn: AdviceLogicalTurn) -> dict[str, str]:
    return {
        "user_id": turn.user_id,
        "relationship_id": turn.relationship_id,
        "conversation_id": turn.conversation_id,
    }


async def test_add_message_with_explicit_id_is_idempotent_and_scope_safe(
    logical_turn_store: tuple[
        InMemoryMemoryStore | SQLiteMemoryStore,
        _ManualClock,
    ],
) -> None:
    store, _clock = logical_turn_store
    kwargs = {
        "user_id": "advice-user",
        "relationship_id": "partner",
        "conversation_id": "conversation",
        "role": MessageRole.USER,
        "content": "这是同一个逻辑轮次。",
        "message_id": "stable-user-message",
    }

    first = await store.add_message(**kwargs)
    replay = await store.add_message(**kwargs)

    assert replay == first
    messages = await store.list_messages(
        user_id="advice-user",
        relationship_id="partner",
        conversation_id="conversation",
    )
    assert [message.id for message in messages] == ["stable-user-message"]
    with pytest.raises(ValueError, match="different message content or scope"):
        await store.add_message(**{**kwargs, "content": "冲突的重复消息。"})


async def test_create_logical_turn_is_idempotent_and_rejects_identity_collision(
    logical_turn_store: tuple[
        InMemoryMemoryStore | SQLiteMemoryStore,
        _ManualClock,
    ],
) -> None:
    store, clock = logical_turn_store
    first = await _create_turn(store, clock)

    replay = await store.create_advice_logical_turn(first)

    assert replay == first
    collision = first.model_copy(update={"relationship_id": "different-partner"})
    with pytest.raises(ValueError, match="content or scope"):
        await store.create_advice_logical_turn(collision)
    another_id = first.model_copy(update={"id": "another-turn"})
    with pytest.raises(ValueError, match="another logical turn"):
        await store.create_advice_logical_turn(another_id)


async def test_strict_logical_turn_creation_claim_has_exactly_one_owner(
    logical_turn_store: tuple[
        InMemoryMemoryStore | SQLiteMemoryStore,
        _ManualClock,
    ],
) -> None:
    store, clock = logical_turn_store
    query = "我和她现在还在冷战。"
    message = await store.add_message(
        user_id="claim-user",
        relationship_id="claim-relationship",
        conversation_id="claim-conversation",
        role=MessageRole.USER,
        content=query,
        message_id="claim-turn-user",
    )
    turn = AdviceLogicalTurn(
        id="claim-turn",
        user_id="claim-user",
        relationship_id="claim-relationship",
        conversation_id="claim-conversation",
        user_message_id=message.id,
        query=query,
        request_payload={"scenario": "conflict"},
        status=AdviceLogicalTurnStatus.MEMORY_STARTED,
        created_at=clock(),
        updated_at=clock(),
    )

    outcomes = await asyncio.gather(
        store.create_advice_logical_turn(turn, reject_existing=True),
        store.create_advice_logical_turn(turn, reject_existing=True),
        return_exceptions=True,
    )

    assert sum(isinstance(outcome, AdviceLogicalTurn) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, AdviceTurnClaimError) for outcome in outcomes) == 1


async def test_generation_state_and_attempt_records_are_persisted_idempotently(
    logical_turn_store: tuple[
        InMemoryMemoryStore | SQLiteMemoryStore,
        _ManualClock,
    ],
) -> None:
    store, clock = logical_turn_store
    turn = await _create_turn(store, clock)

    scope = _turn_scope(turn)
    started = await store.begin_advice_generation(turn.id, retry=False, **scope)
    attempts = [_attempt(1), _attempt(2, status="completed", error_type=None)]
    first_save = await store.save_advice_generation_attempts(
        turn.id,
        started.generation_count,
        attempts,
        **scope,
    )
    replay = await store.save_advice_generation_attempts(
        turn.id,
        started.generation_count,
        attempts,
        **scope,
    )

    assert started.status == AdviceLogicalTurnStatus.GENERATION_IN_PROGRESS
    assert started.generation_count == 1
    assert [record.id for record in replay] == [record.id for record in first_save]
    persisted = await store.list_advice_generation_attempts(turn.id, **scope)
    assert [(record.generation_no, record.attempt.attempt) for record in persisted] == [
        (1, 1),
        (1, 2),
    ]
    with pytest.raises(ValueError, match="generation attempt identity collision"):
        await store.save_advice_generation_attempts(
            turn.id,
            started.generation_count,
            [_attempt(1, status="completed", error_type=None)],
            **scope,
        )


async def test_completion_atomically_writes_exactly_one_assistant_message(
    logical_turn_store: tuple[
        InMemoryMemoryStore | SQLiteMemoryStore,
        _ManualClock,
    ],
) -> None:
    store, clock = logical_turn_store
    turn = await _create_turn(store, clock)
    scope = _turn_scope(turn)
    await store.begin_advice_generation(turn.id, retry=False, **scope)

    completed, assistant = await store.complete_advice_logical_turn(
        turn.id,
        message_id="turn-1-assistant",
        content="现在可以围绕修复后的关系继续沟通。",
        **scope,
    )
    replay_turn, replay_message = await store.complete_advice_logical_turn(
        turn.id,
        message_id="turn-1-assistant",
        content="现在可以围绕修复后的关系继续沟通。",
        **scope,
    )

    assert completed.status == AdviceLogicalTurnStatus.COMPLETED
    assert completed.assistant_message_id == assistant.id
    assert replay_turn == completed
    assert replay_message == assistant
    messages = await store.list_messages(
        user_id=turn.user_id,
        relationship_id=turn.relationship_id,
        conversation_id=turn.conversation_id,
    )
    assert [message.role for message in messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    with pytest.raises(ValueError, match="not eligible for generation"):
        await store.begin_advice_generation(turn.id, retry=True, **scope)


async def test_latest_retryable_turn_is_scoped_and_honors_generation_limit(
    logical_turn_store: tuple[
        InMemoryMemoryStore | SQLiteMemoryStore,
        _ManualClock,
    ],
) -> None:
    store, clock = logical_turn_store
    older = await _create_turn(store, clock, logical_turn_id="older-turn")
    older_scope = _turn_scope(older)
    await store.begin_advice_generation(older.id, retry=False, **older_scope)
    await store.fail_advice_logical_turn(
        older.id,
        last_error_type="json_decode_error",
        **older_scope,
    )
    clock.advance()
    newer = await _create_turn(store, clock, logical_turn_id="newer-turn")
    newer_scope = _turn_scope(newer)
    await store.begin_advice_generation(newer.id, retry=False, **newer_scope)
    await store.fail_advice_logical_turn(
        newer.id,
        last_error_type="finish_reason_length",
        **newer_scope,
    )

    latest = await store.latest_retryable_advice_turn(
        user_id=newer.user_id,
        relationship_id=newer.relationship_id,
        conversation_id=newer.conversation_id,
    )
    wrong_scope = await store.latest_retryable_advice_turn(
        user_id=newer.user_id,
        relationship_id="other-partner",
        conversation_id=newer.conversation_id,
    )

    assert latest is not None and latest.id == newer.id
    assert wrong_scope is None
    retried = await store.begin_advice_generation(newer.id, retry=True, **newer_scope)
    assert retried.generation_count == MAX_ADVICE_GENERATIONS
    await store.fail_advice_logical_turn(
        newer.id,
        last_error_type="provider_error",
        **newer_scope,
    )
    with pytest.raises(ValueError, match="generation limit reached"):
        await store.begin_advice_generation(newer.id, retry=True, **newer_scope)

    next_latest = await store.latest_retryable_advice_turn(
        user_id=older.user_id,
        relationship_id=older.relationship_id,
        conversation_id=older.conversation_id,
    )
    assert next_latest is not None and next_latest.id == older.id


async def test_reset_relationship_scope_cascades_logical_turn_artifacts_only_in_scope(
    logical_turn_store: tuple[
        InMemoryMemoryStore | SQLiteMemoryStore,
        _ManualClock,
    ],
) -> None:
    store, clock = logical_turn_store
    removed = await _create_turn(store, clock, logical_turn_id="removed-turn")
    removed_scope = _turn_scope(removed)
    started = await store.begin_advice_generation(
        removed.id,
        retry=False,
        **removed_scope,
    )
    await store.save_advice_generation_attempts(
        removed.id,
        started.generation_count,
        [_attempt(1)],
        **removed_scope,
    )
    retained = await _create_turn(
        store,
        clock,
        logical_turn_id="retained-turn",
        relationship_id="other-partner",
        conversation_id="other-conversation",
    )

    await store.reset_relationship_scope(
        user_id=removed.user_id,
        relationship_id=removed.relationship_id,
    )

    assert await store.get_advice_logical_turn(removed.id, **removed_scope) is None
    assert await store.list_advice_generation_attempts(
        removed.id,
        **removed_scope,
    ) == []
    assert (
        await store.list_messages(
            user_id=removed.user_id,
            relationship_id=removed.relationship_id,
            conversation_id=removed.conversation_id,
        )
        == []
    )
    assert await store.get_advice_logical_turn(
        retained.id,
        **_turn_scope(retained),
    ) == retained


@pytest.mark.parametrize(
    ("scope_field", "foreign_value"),
    [
        ("user_id", "other-user"),
        ("relationship_id", "other-partner"),
        ("conversation_id", "other-conversation"),
    ],
)
async def test_logical_turn_mutations_reject_cross_scope_access(
    logical_turn_store: tuple[
        InMemoryMemoryStore | SQLiteMemoryStore,
        _ManualClock,
    ],
    scope_field: str,
    foreign_value: str,
) -> None:
    store, clock = logical_turn_store
    turn = await _create_turn(store, clock)
    scope = _turn_scope(turn)
    wrong_scope = {**scope, scope_field: foreign_value}

    assert await store.get_advice_logical_turn(turn.id, **wrong_scope) is None
    with pytest.raises(ValueError, match="different scope"):
        await store.begin_advice_generation(turn.id, retry=False, **wrong_scope)

    started = await store.begin_advice_generation(turn.id, retry=False, **scope)
    with pytest.raises(ValueError, match="different scope"):
        await store.save_advice_generation_attempts(
            turn.id,
            started.generation_count,
            [_attempt(1)],
            **wrong_scope,
        )
    with pytest.raises(ValueError, match="different scope"):
        await store.list_advice_generation_attempts(turn.id, **wrong_scope)
    with pytest.raises(ValueError, match="different scope"):
        await store.complete_advice_logical_turn(
            turn.id,
            message_id="foreign-assistant",
            content="不应写入。",
            **wrong_scope,
        )
    with pytest.raises(ValueError, match="different scope"):
        await store.fail_advice_logical_turn(
            turn.id,
            last_error_type="provider_error",
            **wrong_scope,
        )

    current = await store.get_advice_logical_turn(turn.id, **scope)
    assert current is not None
    assert current.status == AdviceLogicalTurnStatus.GENERATION_IN_PROGRESS
    assert await store.list_advice_generation_attempts(turn.id, **scope) == []

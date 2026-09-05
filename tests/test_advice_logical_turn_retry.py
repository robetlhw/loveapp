import asyncio
from datetime import datetime

import pytest

from loveapp.adapters.advice import TemplateAdviceComposer
from loveapp.bootstrap import build_container
from loveapp.core.config import Settings
from loveapp.domain.advice import (
    AdviceGenerationAttempt,
    AdviceGenerationErrorType,
    AdviceLogicalTurnStatus,
    AdviceRequest,
    AdviceResponse,
    AdviceTurnClaimError,
    RelationshipContext,
)
from loveapp.domain.conversation import ConversationRequest
from loveapp.domain.enums import AdviceScenario
from loveapp.domain.knowledge import RetrievedDocument
from loveapp.domain.memory import AtomicExtraction, MessageRole, StoredMessage
from loveapp.domain.policy import ResolvedScenarioPolicy


class _CountingExtractor:
    def __init__(self) -> None:
        self.calls = 0

    async def extract(
        self,
        text: str,
        *,
        reference_time: datetime,
        existing_memories,
        conversation_history,
        trace=None,
        attempt_callback=None,
    ) -> AtomicExtraction:
        del text, reference_time, existing_memories, conversation_history, trace
        del attempt_callback
        self.calls += 1
        return AtomicExtraction()


class _BlockingExtractor(_CountingExtractor):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def extract(self, text: str, **kwargs) -> AtomicExtraction:
        del text, kwargs
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return AtomicExtraction()


class _FallbackThenSuccessComposer:
    def __init__(self) -> None:
        self.calls = 0
        self.histories: list[list[StoredMessage]] = []
        self._delegate = TemplateAdviceComposer()

    async def compose(
        self,
        request: AdviceRequest,
        scenario: AdviceScenario,
        context: RelationshipContext,
        documents: list[RetrievedDocument],
        conversation_history: list[StoredMessage],
        policy: ResolvedScenarioPolicy,
        stream_callback=None,
        attempt_callback=None,
        trace=None,
    ) -> AdviceResponse:
        del stream_callback, trace
        self.calls += 1
        self.histories.append(list(conversation_history))
        if self.calls == 1:
            if attempt_callback is not None:
                attempt_callback(
                    AdviceGenerationAttempt(
                        attempt=1,
                        status="failed",
                        model="test-advice-model",
                        temperature=0,
                        max_tokens=256,
                        parse_error_type=AdviceGenerationErrorType.JSON_DECODE_ERROR,
                        fallback_used=True,
                    )
                )
            return AdviceResponse(
                scenario=scenario,
                goal=request.goal,
                problem_summary="本次回答生成出现异常。",
                assessment="当前无法可靠生成完整建议。",
                recommended_actions=["请重试本轮回答。"],
            )
        if attempt_callback is not None:
            attempt_callback(
                AdviceGenerationAttempt(
                    attempt=1,
                    status="completed",
                    model="test-advice-model",
                    temperature=0,
                    max_tokens=256,
                )
            )
        return await self._delegate.compose(
            request=request,
            scenario=scenario,
            context=context,
            documents=documents,
            conversation_history=conversation_history,
            policy=policy,
        )


class _CoordinatedSuccessComposer:
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self._delegate = TemplateAdviceComposer()

    async def compose(
        self,
        request: AdviceRequest,
        scenario: AdviceScenario,
        context: RelationshipContext,
        documents: list[RetrievedDocument],
        conversation_history: list[StoredMessage],
        policy: ResolvedScenarioPolicy,
        **kwargs,
    ) -> AdviceResponse:
        del kwargs
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return await self._delegate.compose(
            request=request,
            scenario=scenario,
            context=context,
            documents=documents,
            conversation_history=conversation_history,
            policy=policy,
        )


class _CancellableComposer:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def compose(self, **kwargs) -> AdviceResponse:
        del kwargs
        self.started.set()
        await asyncio.Future()
        raise AssertionError("cancelled composer unexpectedly resumed")


async def test_failed_advice_retry_reuses_logical_turn_without_memory_duplication(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)
    extractor = _CountingExtractor()
    composer = _FallbackThenSuccessComposer()
    container.memory_service._extractor = extractor
    container.advice_agent._composer = composer
    scope = {
        "user_id": "logical-turn-user",
        "relationship_id": "logical-turn-relationship",
        "conversation_id": "logical-turn-conversation",
    }
    events = []
    try:
        first = await container.conversation_agent.chat(
            ConversationRequest(
                **scope,
                query="我和她现在还在冷战，我应该怎么办？",
            ),
            stream_callback=events.append,
        )
        assert first.advice_logical_turn_id is not None

        failed_turn = await container.memory_service.get_advice_logical_turn(
            first.advice_logical_turn_id,
            **scope,
        )
        assert failed_turn is not None
        assert failed_turn.status == AdviceLogicalTurnStatus.GENERATION_FAILED
        assert failed_turn.fallback_used is True
        messages_after_failure = await container.memory_store.list_messages(**scope)
        assert [message.role for message in messages_after_failure] == [MessageRole.USER]
        assert all("半截" not in event.text for event in events)

        with pytest.raises(ValueError, match=r"没有找到|不匹配"):
            await container.advice_agent.advise_turn(
                AdviceRequest(
                    user_id="different-user",
                    relationship_id=scope["relationship_id"],
                    conversation_id=scope["conversation_id"],
                    query=failed_turn.query,
                    scenario=AdviceScenario.CONFLICT,
                    logical_turn_id=failed_turn.id,
                    retry_generation=True,
                )
            )

        retried = await container.conversation_agent.retry_last_failed_advice(
            **scope,
        )
        assert retried.advice is not None
        assert retried.advice_logical_turn_id == first.advice_logical_turn_id

        completed_turn = await container.memory_service.get_advice_logical_turn(
            first.advice_logical_turn_id,
            **scope,
        )
        assert completed_turn is not None
        assert completed_turn.status == AdviceLogicalTurnStatus.COMPLETED
        assert completed_turn.generation_count == 2
        messages = await container.memory_store.list_messages(**scope)
        assert [message.role for message in messages] == [
            MessageRole.USER,
            MessageRole.ASSISTANT,
        ]
        assert extractor.calls == 1
        attempts = await container.memory_service.list_advice_generation_attempts(
            completed_turn.id,
            **scope,
        )
        assert [attempt.generation_no for attempt in attempts] == [1, 2]
        assert composer.calls == 2
        assert await container.memory_service.latest_retryable_advice_turn(
            **scope
        ) is None

        with pytest.raises(ValueError, match="不可重试"):
            await container.advice_agent.advise_turn(
                AdviceRequest(
                    **scope,
                    query=completed_turn.query,
                    scenario=AdviceScenario.CONFLICT,
                    logical_turn_id=completed_turn.id,
                    retry_generation=True,
                )
            )
        assert len(await container.memory_store.list_messages(**scope)) == 2
        assert extractor.calls == 1
    finally:
        await container.aclose()


async def test_retry_does_not_cross_conversation_scope(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)
    container.advice_agent._composer = _FallbackThenSuccessComposer()
    scope = {
        "user_id": "retry-scope-user",
        "relationship_id": "retry-scope-relationship",
        "conversation_id": "retry-scope-original",
    }
    try:
        await container.conversation_agent.chat(
            ConversationRequest(**scope, query="我们还在冷战，我应该怎么办？")
        )

        with pytest.raises(ValueError, match="没有可重试"):
            await container.conversation_agent.retry_last_failed_advice(
                user_id=scope["user_id"],
                relationship_id=scope["relationship_id"],
                conversation_id="retry-scope-new-conversation",
            )
    finally:
        await container.aclose()


async def test_concurrent_same_logical_turn_has_one_owner_and_one_memory_side_effect(
    app_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = build_container(app_settings)
    extractor = _CountingExtractor()
    composer = _CoordinatedSuccessComposer()
    container.memory_service._extractor = extractor
    container.advice_agent._composer = composer
    logical_turn_id = "concurrent-advice-turn"
    scope = {
        "user_id": "concurrent-advice-user",
        "relationship_id": "concurrent-advice-relationship",
        "conversation_id": "concurrent-advice-conversation",
    }
    original_get = container.memory_service.get_advice_logical_turn
    both_reads_started = asyncio.Event()
    read_count = 0

    async def synchronized_get(turn_id: str, **kwargs):
        nonlocal read_count
        result = await original_get(turn_id, **kwargs)
        if turn_id == logical_turn_id and read_count < 2:
            read_count += 1
            if read_count == 2:
                both_reads_started.set()
            await both_reads_started.wait()
        return result

    monkeypatch.setattr(
        container.memory_service,
        "get_advice_logical_turn",
        synchronized_get,
    )
    request = AdviceRequest(
        **scope,
        query="我和她现在还在冷战，我应该怎么办？",
        scenario=AdviceScenario.CONFLICT,
        logical_turn_id=logical_turn_id,
    )
    tasks = [
        asyncio.create_task(container.advice_agent.advise_turn(request))
        for _ in range(2)
    ]
    try:
        await composer.started.wait()
        composer.release.set()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)

        assert sum(not isinstance(outcome, BaseException) for outcome in outcomes) == 1
        assert sum(isinstance(outcome, AdviceTurnClaimError) for outcome in outcomes) == 1
        assert composer.calls == 1
        assert extractor.calls == 1
        turn = await original_get(logical_turn_id, **scope)
        assert turn is not None
        assert turn.status == AdviceLogicalTurnStatus.COMPLETED
        assert turn.generation_count == 1
        messages = await container.memory_store.list_messages(**scope)
        assert [message.role for message in messages] == [
            MessageRole.USER,
            MessageRole.ASSISTANT,
        ]
    finally:
        composer.release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await container.aclose()


async def test_cancelled_generation_becomes_retryable_without_replaying_memory(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)
    extractor = _CountingExtractor()
    composer = _CancellableComposer()
    container.memory_service._extractor = extractor
    container.advice_agent._composer = composer
    scope = {
        "user_id": "cancelled-advice-user",
        "relationship_id": "cancelled-advice-relationship",
        "conversation_id": "cancelled-advice-conversation",
    }
    request = AdviceRequest(
        **scope,
        query="我和她现在还在冷战，我应该怎么办？",
        scenario=AdviceScenario.CONFLICT,
        logical_turn_id="cancelled-advice-turn",
    )
    task = asyncio.create_task(container.advice_agent.advise_turn(request))
    try:
        await composer.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        failed = await container.memory_service.get_advice_logical_turn(
            request.logical_turn_id or "",
            **scope,
        )
        assert failed is not None
        assert failed.status == AdviceLogicalTurnStatus.GENERATION_FAILED
        assert failed.generation_count == 1
        assert failed.last_error_type == "CancelledError"
        retryable = await container.memory_service.latest_retryable_advice_turn(**scope)
        assert retryable is not None and retryable.id == failed.id
        assert [
            message.role
            for message in await container.memory_store.list_messages(**scope)
        ] == [MessageRole.USER]

        container.advice_agent._composer = TemplateAdviceComposer()
        retried = await container.advice_agent.advise_turn(
            request.model_copy(update={"retry_generation": True})
        )

        assert retried.logical_turn_id == failed.id
        completed = await container.memory_service.get_advice_logical_turn(
            failed.id,
            **scope,
        )
        assert completed is not None
        assert completed.status == AdviceLogicalTurnStatus.COMPLETED
        assert completed.generation_count == 2
        assert extractor.calls == 1
        assert [
            message.role
            for message in await container.memory_store.list_messages(**scope)
        ] == [MessageRole.USER, MessageRole.ASSISTANT]
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await container.aclose()


async def test_cancellation_does_not_cancel_current_turn_memory_extraction(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)
    extractor = _BlockingExtractor()
    container.memory_service._extractor = extractor
    scope = {
        "user_id": "cancelled-memory-user",
        "relationship_id": "cancelled-memory-relationship",
        "conversation_id": "cancelled-memory-conversation",
    }
    request = AdviceRequest(
        **scope,
        query="我和她现在还在冷战，我应该怎么办？",
        scenario=AdviceScenario.CONFLICT,
        logical_turn_id="cancelled-memory-turn",
    )
    task = asyncio.create_task(container.advice_agent.advise_turn(request))
    try:
        await extractor.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        failed = await container.memory_service.get_advice_logical_turn(
            request.logical_turn_id or "",
            **scope,
        )
        assert failed is not None
        assert failed.status == AdviceLogicalTurnStatus.GENERATION_FAILED
        assert failed.generation_count == 0

        extractor.release.set()
        pending = await container.memory_service.wait_for_scope(
            user_id=scope["user_id"],
            relationship_id=scope["relationship_id"],
            timeout_seconds=2,
        )
        assert pending == 0
        assert extractor.calls == 1

        retried = await container.advice_agent.advise_turn(
            request.model_copy(update={"retry_generation": True})
        )
        completed = await container.memory_service.get_advice_logical_turn(
            request.logical_turn_id or "",
            **scope,
        )
        assert retried.logical_turn_id == request.logical_turn_id
        assert completed is not None
        assert completed.status == AdviceLogicalTurnStatus.COMPLETED
        assert completed.generation_count == 1
        assert extractor.calls == 1
    finally:
        extractor.release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await container.aclose()

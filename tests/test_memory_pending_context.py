import json
from datetime import UTC, datetime

import pytest

from loveapp.adapters.conversation_states import (
    InMemoryConversationFlowStateStore,
    SQLiteConversationFlowStateStore,
)
from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.adapters.memory.openai_compatible import _SYSTEM_PROMPT, _build_prompt
from loveapp.agents.conversation import ConversationAgent, _age_pending_memory_context
from loveapp.application.memory import MemoryService
from loveapp.application.memory_gate import MemoryGate, build_pending_memory_context
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.conversation import ConversationFlowState, ConversationRequest
from loveapp.domain.enums import RiskLevel, TaskType
from loveapp.domain.memory import (
    AtomicExtraction,
    MemoryL0Route,
    MemorySemanticGateReason,
)
from loveapp.domain.routing import RouteResult
from loveapp.domain.runtime_context import PendingMemoryContext

NOW = datetime(2026, 9, 1, tzinfo=UTC)


def _pending_context(
    question: str,
    *,
    expected_slot: str,
    topic: str,
    created_turn: str = "assistant-turn-1",
    expires_after_turns: int = 2,
) -> PendingMemoryContext:
    return PendingMemoryContext(
        previous_assistant_question=question,
        memory_relevant=True,
        expected_slot=expected_slot,
        topic=topic,
        created_turn=created_turn,
        expires_after_turns=expires_after_turns,
    )


@pytest.mark.parametrize(
    ("question", "reply", "expected_slot", "topic"),
    [
        ("这次是谁先提的分手？", "她。", "actor", "breakup"),
        ("你们这次吵架持续了多久？", "一周。", "duration", "conflict"),
        ("你们这次主要为什么吵架？", "消费观。", "cause", "conflict"),
    ],
)
def test_route_v2_uses_typed_pending_memory_context_for_short_replies(
    question: str,
    reply: str,
    expected_slot: str,
    topic: str,
) -> None:
    context = _pending_context(
        question,
        expected_slot=expected_slot,
        topic=topic,
    )

    decision = MemoryGate().route_v2(reply, pending_memory_context=context)

    assert decision.l0_route == MemoryL0Route.CONTEXT_PASS
    assert decision.should_extract is True
    assert decision.l0_semantic_hint == MemorySemanticGateReason.CONTEXT_DEPENDENT_REPLY
    assert decision.pending_memory_context == context
    assert decision.pending_memory_context_source == "structured"


@pytest.mark.parametrize(
    "reply",
    [
        "我也不知道。",
        "不太想说这个。",
        "我先去开会了，晚点再说。",
        "对了，她其实特别爱吃寿司。",
    ],
)
def test_typed_pending_context_routes_boundary_replies_to_same_call_semantic_gate(
    reply: str,
) -> None:
    context = _pending_context(
        "你们这次主要为什么吵架？",
        expected_slot="cause",
        topic="conflict",
    )

    decision = MemoryGate().route_v2(reply, pending_memory_context=context)

    assert decision.l0_route == MemoryL0Route.CONTEXT_PASS
    assert decision.pending_memory_context == context


def test_pending_context_registration_is_fail_closed_for_multiple_memory_questions() -> None:
    assert (
        build_pending_memory_context(
            [
                "你们这次吵架持续了多久？",
                "这次是谁先提的分手？",
            ],
            created_turn="assistant-turn-ambiguous",
        )
        is None
    )


def test_pending_context_confirmation_inherits_the_previous_slot() -> None:
    previous = _pending_context(
        "你们冷战多久了？",
        expected_slot="duration",
        topic="conflict",
        created_turn="assistant-turn-1",
    )

    pending = build_pending_memory_context(
        ["一个月左右，对吗？"],
        created_turn="assistant-turn-2",
        previous_context=previous,
    )

    assert pending is not None
    assert pending.previous_assistant_question == "一个月左右，对吗？"
    assert pending.expected_slot == "duration"
    assert pending.topic == "conflict"
    assert pending.created_turn == "assistant-turn-2"


def test_pending_context_ttl_ages_by_user_turn_and_expires() -> None:
    pending = _pending_context(
        "你们这次吵架持续了多久？",
        expected_slot="duration",
        topic="conflict",
        expires_after_turns=2,
    )

    aged = _age_pending_memory_context(pending)

    assert aged is not None
    assert aged.expires_after_turns == 1
    assert _age_pending_memory_context(aged) is None


async def test_finalize_flow_replaces_old_pending_context_with_new_unique_question() -> None:
    old = _pending_context(
        "你们这次吵架持续了多久？",
        expected_slot="duration",
        topic="conflict",
        created_turn="old-turn",
    )
    flow = ConversationFlowState(
        user_id="flow-user",
        relationship_id="flow-relationship",
        conversation_id="flow-conversation",
        pending_memory_context=old,
    )
    saved_states: list[ConversationFlowState] = []

    async def save_flow(state: ConversationFlowState, trace) -> ConversationFlowState:
        del trace
        saved_states.append(state)
        return state

    agent = object.__new__(ConversationAgent)
    agent._save_conversation_flow_state = save_flow
    state = {
        "request": ConversationRequest(
            user_id=flow.user_id,
            relationship_id=flow.relationship_id,
            conversation_id=flow.conversation_id,
            query="补充信息",
        ),
        "route": RouteResult(
            normalized_query="补充信息",
            task_type=TaskType.RELATIONSHIP_ADVICE,
            task_confidence=1.0,
            risk_level=RiskLevel.NORMAL,
        ),
        "flow_state": flow,
        "advice_turn": _AdviceTurnStub(
            clarifying_questions=["这次是谁先提的分手？"],
            logical_turn_id="new-turn",
        ),
        "trace": ExecutionTrace(),
    }

    result = await ConversationAgent._finalize_flow(agent, state)

    pending = result["flow_state"].pending_memory_context
    assert pending is not None
    assert pending.expected_slot == "actor"
    assert pending.created_turn == "new-turn"
    assert pending != old
    assert saved_states == [result["flow_state"]]


async def test_finalize_flow_does_not_register_ambiguous_multiple_questions() -> None:
    flow = ConversationFlowState(
        user_id="flow-user",
        relationship_id="flow-relationship",
        conversation_id="flow-conversation",
    )

    async def save_flow(state: ConversationFlowState, trace) -> ConversationFlowState:
        del trace
        return state

    agent = object.__new__(ConversationAgent)
    agent._save_conversation_flow_state = save_flow
    state = {
        "request": ConversationRequest(
            user_id=flow.user_id,
            relationship_id=flow.relationship_id,
            conversation_id=flow.conversation_id,
            query="补充信息",
        ),
        "route": RouteResult(
            normalized_query="补充信息",
            task_type=TaskType.RELATIONSHIP_ADVICE,
            task_confidence=1.0,
            risk_level=RiskLevel.NORMAL,
        ),
        "flow_state": flow,
        "advice_turn": _AdviceTurnStub(
            clarifying_questions=[
                "你们这次吵架持续了多久？",
                "这次是谁先提的分手？",
            ],
            logical_turn_id="ambiguous-turn",
        ),
        "trace": ExecutionTrace(),
    }

    result = await ConversationAgent._finalize_flow(agent, state)

    assert result["flow_state"].pending_memory_context is None


class _AdviceResponseStub:
    def __init__(self, clarifying_questions: list[str]) -> None:
        self.clarifying_questions = clarifying_questions


class _AdviceTurnStub:
    def __init__(self, *, clarifying_questions: list[str], logical_turn_id: str) -> None:
        self.response = _AdviceResponseStub(clarifying_questions)
        self.logical_turn_id = logical_turn_id
        self.generation_attempts = []


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_pending_memory_context_round_trips_with_conversation_flow_state(
    backend: str,
    tmp_path,
) -> None:
    store = (
        InMemoryConversationFlowStateStore()
        if backend == "memory"
        else SQLiteConversationFlowStateStore(tmp_path / "pending-context.db")
    )
    pending = _pending_context(
        "你们这次主要为什么吵架？",
        expected_slot="cause",
        topic="conflict",
    )
    state = ConversationFlowState(
        user_id="pending-user",
        relationship_id="pending-relationship",
        conversation_id="pending-conversation",
        pending_memory_context=pending,
    )

    await store.save(state)
    loaded = await store.get(
        user_id=state.user_id,
        relationship_id=state.relationship_id,
        conversation_id=state.conversation_id,
    )

    assert loaded is not None
    assert loaded.pending_memory_context == pending
    assert loaded.pending_memory_context is not pending
    await store.aclose()


class _RecordingContextExtractor:
    def __init__(self) -> None:
        self.pending_contexts: list[PendingMemoryContext | None] = []

    async def extract(
        self,
        text: str,
        *,
        pending_memory_context: PendingMemoryContext | None = None,
        **kwargs,
    ) -> AtomicExtraction:
        del text, kwargs
        self.pending_contexts.append(pending_memory_context)
        return AtomicExtraction(
            should_extract=False,
            gate_reason=MemorySemanticGateReason.NO_MEMORY,
        )


async def test_memory_service_passes_the_exact_pending_context_to_gate_and_extractor() -> None:
    pending = _pending_context(
        "这次是谁先提的分手？",
        expected_slot="actor",
        topic="breakup",
    )
    extractor = _RecordingContextExtractor()
    service = MemoryService(InMemoryMemoryStore(), extractor)

    result = await service.remember_text(
        user_id="service-user",
        relationship_id="service-relationship",
        conversation_id="service-conversation",
        text="她。",
        pending_memory_context=pending,
    )

    assert result.gate_decision is not None
    assert result.gate_decision.l0_route == MemoryL0Route.CONTEXT_PASS
    assert result.gate_decision.pending_memory_context == pending
    assert extractor.pending_contexts == [pending]


def test_flash_prompt_projects_structured_pending_context_without_eval_labels() -> None:
    pending = _pending_context(
        "你们这次主要为什么吵架？",
        expected_slot="cause",
        topic="conflict",
    )

    payload = json.loads(
        _build_prompt(
            "消费观。",
            NOW,
            [],
            [],
            pending_memory_context=pending,
        )
    )

    assert payload["runtime_context"] == {
        "l0_route": "CONTEXT_PASS",
        "pending_memory_context": pending.model_dump(mode="json"),
    }
    assert "expected" not in payload
    assert "rationale" not in payload
    assert "extraction_hint" not in payload


def test_flash_prompt_defines_fail_closed_short_reply_boundaries() -> None:
    assert "pending_memory_context" in _SYSTEM_PROMPT
    assert "我不知道" in _SYSTEM_PROMPT
    assert "拒答" in _SYSTEM_PROMPT
    assert "切换话题" in _SYSTEM_PROMPT
    assert "不要填入" in _SYSTEM_PROMPT

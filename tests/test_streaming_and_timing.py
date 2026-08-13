import asyncio

import pytest

from loveapp.adapters.advice import TemplateAdviceComposer
from loveapp.adapters.advice.openai_compatible import _StructuredAdviceStreamParser
from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.agents import AdviceAgent
from loveapp.application import MemoryService
from loveapp.application.memory import NoOpMemoryExtractor
from loveapp.application.scenario_policy import (
    default_scenario_policy_registry,
    sanitize_advice_stream_event,
)
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.advice import AdviceRequest, AdviceResponse, AdviceStreamEvent
from loveapp.domain.enums import AdviceScenario, RiskLevel
from loveapp.domain.memory import AtomicExtraction
from loveapp.safety import SafetyPolicy


class BlockingExtractor:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def extract(
        self,
        text,
        *,
        reference_time,
        existing_memories,
        conversation_history,
        trace=None,
    ):
        del text, reference_time, existing_memories, conversation_history, trace
        self.started.set()
        await self.release.wait()
        return AtomicExtraction()


class ReleasingComposer:
    def __init__(self, extractor: BlockingExtractor) -> None:
        self._extractor = extractor

    async def compose(
        self,
        request,
        scenario,
        context,
        documents,
        conversation_history,
        policy,
        stream_callback=None,
    ) -> AdviceResponse:
        del context, documents, conversation_history, policy, stream_callback
        await asyncio.wait_for(self._extractor.started.wait(), timeout=1)
        self._extractor.release.set()
        return AdviceResponse(
            scenario=scenario,
            secondary_scenarios=request.secondary_scenarios,
            problem_summary="并行测试",
            assessment="回答生成没有等待记忆抽取结束。",
        )


class EmptyRetriever:
    async def search(self, query, filters=None, limit=5, trace=None):
        del query, filters, limit, trace
        return []


class FailingRetriever:
    async def search(self, query, filters=None, limit=5, trace=None):
        del query, filters, limit, trace
        raise RuntimeError("vector store unavailable")


class FailingComposer:
    async def compose(self, *args, **kwargs):
        del args, kwargs
        raise AssertionError("safety responses must not use the regular advice composer")


def test_structured_stream_parser_emits_completed_fields_and_array_items() -> None:
    parser = _StructuredAdviceStreamParser()
    chunks = [
        '{"problem_summary":"追求问题","assessment":"互动有所改善",',
        '"clarifying_questions":[],"recommended_actions":["先观察对方回应",',
        '"再提出低压力邀约"],"sample_phrases":["周末有空一起喝咖啡吗？"],',
        '"alternatives":[],"avoid_actions":[],"risk_notes":[]}',
    ]

    events = [event for chunk in chunks for event in parser.feed(chunk)]

    assert [(event.field, event.text) for event in events] == [
        ("problem_summary", "追求问题"),
        ("assessment", "互动有所改善"),
        ("recommended_actions", "先观察对方回应"),
        ("recommended_actions", "再提出低压力邀约"),
        ("sample_phrases", "周末有空一起喝咖啡吗？"),
    ]


def test_stream_event_is_filtered_before_display() -> None:
    policy = default_scenario_policy_registry().resolve(
        AdviceScenario.PURSUIT,
        [],
    )

    event = sanitize_advice_stream_event(
        AdviceStreamEvent(field="recommended_actions", text="让她吃醋来刺激回应。"),
        policy,
        "她明确拒绝了我。",
    )

    assert event is None


async def test_memory_extraction_and_answer_pipeline_run_in_parallel() -> None:
    extractor = BlockingExtractor()
    trace = ExecutionTrace()
    store = InMemoryMemoryStore()
    agent = AdviceAgent(
        EmptyRetriever(),
        MemoryService(store, extractor),
        SafetyPolicy(),
        ReleasingComposer(extractor),
    )

    response = await asyncio.wait_for(
        agent.advise_turn(
            AdviceRequest(
                query="我喜欢她，应该怎么追求？",
                scenario=AdviceScenario.PURSUIT,
            ),
            trace=trace,
        ),
        timeout=2,
    )

    assert response.response.problem_summary == "并行测试"
    timings = {record.name: record for record in trace.records}
    memory = timings["memory_extraction"]
    generation = timings["answer_generation"]
    assert memory.started_offset_ms <= generation.started_offset_ms
    assert generation.started_offset_ms < memory.started_offset_ms + memory.duration_ms


async def test_interactive_turn_returns_while_memory_extraction_stays_in_background() -> None:
    extractor = BlockingExtractor()
    trace = ExecutionTrace()
    service = MemoryService(InMemoryMemoryStore(), extractor, shutdown_grace_seconds=1)
    agent = AdviceAgent(
        EmptyRetriever(),
        service,
        SafetyPolicy(),
        TemplateAdviceComposer(),
    )

    turn = await asyncio.wait_for(
        agent.advise_turn(
            AdviceRequest(
                query="我喜欢她，最近一直主动找她聊天，我该怎么办？",
                scenario=AdviceScenario.PURSUIT,
            ),
            trace=trace,
            wait_for_memory=False,
        ),
        timeout=1,
    )

    assert turn.memory_result is not None
    assert turn.memory_result.pending is True
    assert any(
        record.name == "memory_extraction" and record.status.value == "running"
        for record in trace.snapshot()
    )
    extractor.release.set()
    await service.aclose()


async def test_trace_identifies_rag_failure_instead_of_total_step() -> None:
    trace = ExecutionTrace()
    store = InMemoryMemoryStore()
    agent = AdviceAgent(
        FailingRetriever(),
        MemoryService(store, NoOpMemoryExtractor()),
        SafetyPolicy(),
        TemplateAdviceComposer(),
    )

    with pytest.raises(RuntimeError, match="vector store unavailable"):
        await agent.advise_turn(
            AdviceRequest(
                query="我喜欢她，应该怎么追求？",
                scenario=AdviceScenario.PURSUIT,
            ),
            trace=trace,
        )

    assert trace.failed_step is not None
    assert trace.failed_step.name == "rag_retrieval"


async def test_sensitive_safety_response_bypasses_context_rag_and_regular_composer() -> None:
    trace = ExecutionTrace()
    store = InMemoryMemoryStore()
    agent = AdviceAgent(
        FailingRetriever(),
        MemoryService(store, NoOpMemoryExtractor()),
        SafetyPolicy(),
        FailingComposer(),
    )

    turn = await agent.advise_turn(
        AdviceRequest(
            user_id="sensitive-user",
            relationship_id="sensitive-relationship",
            conversation_id="sensitive-conversation",
            query="怎样避免伤害自己？",
            scenario=AdviceScenario.RELATIONSHIP_MAINTENANCE,
        ),
        trace=trace,
    )

    assert turn.response.risk_level == RiskLevel.SENSITIVE
    assert turn.response.risk_notes == ["用户表达了避免自伤的安全求助"]
    timing_names = {record.name for record in trace.snapshot()}
    assert "sensitive_safety_response" in timing_names
    assert "context_load" not in timing_names
    assert "policy_resolution" not in timing_names
    assert "rag_retrieval" not in timing_names
    assert "answer_generation" not in timing_names
    messages = await store.list_messages(
        user_id="sensitive-user",
        relationship_id="sensitive-relationship",
        conversation_id="sensitive-conversation",
    )
    assert [message.role.value for message in messages] == ["user", "assistant"]

from datetime import UTC, datetime

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.application import MemoryService
from loveapp.application.memory_gate import MemoryGate
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.memory import (
    AtomicExtraction,
    MessageRole,
    StoredMessage,
)

NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "signal", "rule"),
    [
        ("我和她现在还在冷战。", "relationship_state", "relationship_state_1"),
        ("最近我们的矛盾越来越多。", "interaction_trend", "interaction_trend_1"),
        ("我和她现在还是暧昧关系。", "relationship_state", "relationship_state_1"),
        ("我和她已经恢复正常了。", "relationship_transition", "contextual_restoration"),
        ("我们已经恢复正常了。", "relationship_transition", "contextual_restoration"),
        ("我和她已经说开了。", "relationship_transition", "contextual_restoration"),
        ("我和她又出现问题了。", "relationship_transition", "contextual_recurrence"),
    ],
)
def test_standalone_relationship_signal_coverage(
    text: str,
    signal: str,
    rule: str,
) -> None:
    decision = MemoryGate().evaluate(text)

    assert decision.should_extract is True
    assert decision.reason.value == "durable_signal"
    assert signal in decision.signals
    assert decision.matched_rule == rule
    assert decision.matched_span


@pytest.mark.parametrize(
    ("history_text", "text", "signal", "rule"),
    [
        (
            "她最近很少主动联系我。",
            "现在大概一天一两次。",
            "contextual_frequency",
            "contextual_frequency_rate",
        ),
        (
            "我们最近经常因为钱的问题吵架。",
            "差不多一周两三次。",
            "contextual_frequency",
            "contextual_frequency_rate",
        ),
        (
            "她现在一天只回我一两次。",
            "我说少了，其实大概三四次。",
            "contextual_correction",
            "contextual_correction",
        ),
        (
            "她最近基本不怎么联系我。",
            "最近已经恢复正常了。",
            "contextual_restoration",
            "contextual_restoration",
        ),
        (
            "她前段时间回复已经恢复正常了。",
            "但这几天又开始越来越慢。",
            "contextual_recurrence",
            "contextual_recurrence",
        ),
        (
            "我们最近经常因为钱的问题吵架。",
            "基本都是因为钱怎么花。",
            "contextual_cause_scope",
            "contextual_cause_scope",
        ),
    ],
)
def test_history_derived_contextual_signal_coverage(
    history_text: str,
    text: str,
    signal: str,
    rule: str,
) -> None:
    history = [_message("history", history_text)]
    decision = MemoryGate().evaluate(text, conversation_history=history)

    assert decision.should_extract is True
    assert decision.reason.value == "durable_signal"
    assert decision.contextual_probe is True
    assert signal in decision.signals
    assert "contextual_history_derived" in decision.signals
    assert decision.matched_rule == rule
    assert decision.matched_span


@pytest.mark.parametrize(
    "text",
    [
        "现在大概一天一两次。",
        "差不多一周两三次。",
        "我说少了，其实大概三四次。",
        "最近已经恢复正常了。",
        "但这几天又开始越来越慢。",
        "基本都是因为钱怎么花。",
    ],
)
def test_contextual_short_signal_without_relationship_context_is_rejected(
    text: str,
) -> None:
    decision = MemoryGate().evaluate(text)

    assert decision.should_extract is False
    assert decision.reason.value == "no_durable_signal"


def test_contextual_signal_can_use_recent_relevant_history_after_casual_turn() -> None:
    history = [
        _message("history-1", "她最近很少主动联系我。"),
        _message("history-2", "我今天开会。"),
    ]
    decision = MemoryGate().evaluate(
        "现在大概一天一两次。",
        conversation_history=history,
    )

    assert decision.should_extract is True
    assert "contextual_frequency" in decision.signals
    assert "contextual_history_derived" in decision.signals


def test_correction_variant_is_detected_with_relationship_history() -> None:
    history = [_message("history-correction", "我们已经冷战一个月了。")]
    decision = MemoryGate().evaluate(
        "不是一个月，是两周。",
        conversation_history=history,
    )

    assert decision.should_extract is True
    assert "contextual_correction" in decision.signals
    assert decision.matched_rule == "contextual_correction"


def test_explicit_correction_with_only_duration_is_detected() -> None:
    history = [_message("history-duration-correction", "我们已经冷战一个月了。")]
    decision = MemoryGate().evaluate(
        "不对，我刚才说错了，其实只有两周。",
        conversation_history=history,
    )

    assert decision.should_extract is True
    assert "contextual_correction" in decision.signals


def test_conflict_cause_can_include_work_arrangements() -> None:
    history = [_message("history-conflict", "我们最近经常吵架。")]
    decision = MemoryGate().evaluate(
        "基本都是因为工作安排。",
        conversation_history=history,
    )

    assert decision.should_extract is True
    assert "contextual_cause_scope" in decision.signals


@pytest.mark.parametrize(
    "text",
    [
        "我一天两次吃药。",
        "她一天两次吃药。",
        "项目已经解决了。",
        "她说开了今天的会议。",
        "她最近工作很忙，恢复正常了。",
        "我身体已经恢复正常了。",
        "天气已经恢复正常了。",
        "网络已经恢复正常了。",
        "心率已经恢复正常了。",
        "快递一天两次。",
        "我一天两次回复客户。",
        "我们系统已经恢复正常了。",
        "我们系统又出现问题了。",
        "因为客户沟通问题。",
        "问题在于项目沟通。",
        "原因是客户沟通问题。",
        "基本都是因为项目沟通。",
        "联系人手机恢复正常了。",
        "聊天软件恢复正常了。",
        "回复服务恢复正常了。",
        "关系数据库恢复正常了。",
    ],
)
def test_unrelated_contextual_phrase_is_rejected_even_after_relationship_history(
    text: str,
) -> None:
    history = [_message("history-relationship", "她最近很少主动联系我。")]
    decision = MemoryGate().evaluate(text, conversation_history=history)

    assert decision.should_extract is False
    assert decision.reason.value == "no_durable_signal"


@pytest.mark.parametrize(
    "text",
    [
        "今天吃什么？",
        "哈哈确实",
        "好吧",
        "那就这样",
        "我一天喝两杯水",
        "她一天喝两杯水",
        "我一天两次吃药",
        "她一天两次吃药",
        "最近工作越来越忙",
        "最近项目问题越来越多",
        "最近系统问题越来越多",
        "最近我们的工作问题越来越多",
        "她说开了今天的会议",
        "她最近工作很忙，恢复正常了",
        "我身体已经恢复正常了",
        "天气已经恢复正常了",
        "网络已经恢复正常了",
        "心率已经恢复正常了",
        "快递一天两次",
        "我一天两次回复客户",
        "我们系统已经恢复正常了",
        "我们系统又出现问题了",
        "因为客户沟通问题",
        "问题在于项目沟通",
        "原因是客户沟通问题",
        "基本都是因为项目沟通",
        "联系人手机恢复正常了",
        "聊天软件恢复正常了",
        "回复服务恢复正常了",
        "关系数据库恢复正常了",
    ],
)
def test_signal_coverage_does_not_admit_unrelated_or_casual_text(text: str) -> None:
    decision = MemoryGate().evaluate(text)

    assert decision.should_extract is False


class _RecordingExtractor:
    def __init__(self) -> None:
        self.called = False
        self.history: list[object] = []

    async def extract(self, text, *, conversation_history, **kwargs) -> AtomicExtraction:
        del text, kwargs
        self.called = True
        self.history = list(conversation_history)
        return AtomicExtraction()


@pytest.mark.asyncio
async def test_contextual_follow_up_reaches_extractor_with_history_loaded() -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    extractor = _RecordingExtractor()
    service = MemoryService(store, extractor, clock=lambda: NOW)
    scope = {
        "user_id": "signal-coverage-user",
        "relationship_id": "signal-coverage-relationship",
        "conversation_id": "signal-coverage-conversation",
    }
    await service.record_message(
        role=MessageRole.USER,
        content="她最近很少主动联系我。",
        **scope,
    )
    current = await service.record_message(
        role=MessageRole.USER,
        content="现在大概一天一两次。",
        **scope,
    )
    trace = ExecutionTrace()

    result = await service.remember_recorded_message(
        message=current,
        text=current.content,
        trace=trace,
    )

    assert result.gate_decision is not None
    assert result.gate_decision.should_extract is True
    assert result.gate_decision.history_loaded_for_gate is True
    assert "contextual_frequency" in result.gate_decision.signals
    assert extractor.called is True
    assert len(extractor.history) == 1
    gate_trace = next(record for record in trace.snapshot() if record.name == "memory_gate")
    assert gate_trace.details["matched_rule"] == "contextual_frequency_rate"
    assert gate_trace.details["contextual_probe"] is True


@pytest.mark.asyncio
async def test_unrelated_quantified_follow_up_after_relationship_history_is_skipped() -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    extractor = _RecordingExtractor()
    service = MemoryService(store, extractor, clock=lambda: NOW)
    scope = {
        "user_id": "signal-negative-user",
        "relationship_id": "signal-negative-relationship",
        "conversation_id": "signal-negative-conversation",
    }
    await service.record_message(
        role=MessageRole.USER,
        content="她最近很少主动联系我。",
        **scope,
    )
    current = await service.record_message(
        role=MessageRole.USER,
        content="我一天两次吃药。",
        **scope,
    )

    result = await service.remember_recorded_message(
        message=current,
        text=current.content,
    )

    assert result.gate_decision is not None
    assert result.gate_decision.should_extract is False
    assert result.gate_decision.reason.value == "no_durable_signal"
    assert extractor.called is False
    memories = await store.list_memories(
        user_id=scope["user_id"],
        relationship_id=scope["relationship_id"],
        limit=100,
    )
    assert memories == []


def _message(message_id: str, content: str) -> StoredMessage:
    return StoredMessage(
        id=message_id,
        conversation_id="signal-coverage-conversation",
        user_id="signal-coverage-user",
        relationship_id="signal-coverage-relationship",
        role=MessageRole.USER,
        content=content,
        created_at=NOW,
    )

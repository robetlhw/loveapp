from datetime import UTC, datetime

import pytest

from loveapp.application.memory_gate import MemoryGate
from loveapp.domain.memory import (
    MemoryGateReason,
    MemoryL0Route,
    MemorySemanticGateReason,
    MessageRole,
    StoredMessage,
)

NOW = datetime(2026, 9, 1, tzinfo=UTC)


def _message(index: int, role: MessageRole, content: str) -> StoredMessage:
    return StoredMessage(
        id=f"message-{index}",
        conversation_id="gate-v2-conversation",
        user_id="gate-v2-user",
        relationship_id="gate-v2-relationship",
        role=role,
        content=content,
        created_at=NOW,
    )


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("她生日是10月12号。", MemorySemanticGateReason.STABLE_FACT),
        (
            "她现在在上海工作，平时住在徐汇那边。",
            MemorySemanticGateReason.STABLE_FACT,
        ),
        (
            "我俩是去年十一月确定关系的，到现在差不多九个多月了。",
            MemorySemanticGateReason.RELATIONSHIP_STATE,
        ),
        (
            "她特别能吃辣，火锅一般都点中辣以上。",
            MemorySemanticGateReason.PREFERENCE,
        ),
        (
            "她不喜欢人特别多又很吵的地方，约会更愿意去安静一点的小店。",
            MemorySemanticGateReason.PREFERENCE,
        ),
        (
            "她对花没什么感觉，反而更喜欢实用一点的小礼物。",
            MemorySemanticGateReason.PREFERENCE,
        ),
        (
            "她平时不怎么喝奶茶，但很喜欢手冲咖啡，尤其偏酸一点的豆子。",
            MemorySemanticGateReason.PREFERENCE,
        ),
        (
            "我们已经约好下周六去杭州住一晚，她把酒店都订好了。",
            MemorySemanticGateReason.PLANNED_EVENT,
        ),
        (
            "我准备明天跟她提分手。",
            MemorySemanticGateReason.ACTION_INTENT,
        ),
    ],
)
def test_route_v2_hard_passes_obvious_durable_cases(
    text: str,
    reason: MemorySemanticGateReason,
) -> None:
    decision = MemoryGate().route_v2(text)

    assert decision.l0_route == MemoryL0Route.HARD_PASS
    assert decision.should_extract is True
    assert decision.l0_semantic_hint == reason
    assert decision.semantic_gate_reason is None


def test_route_v2_explicit_remember_takes_priority_over_review_cues() -> None:
    decision = MemoryGate().route_v2("请记住，她最近很喜欢寿司。")

    assert decision.l0_route == MemoryL0Route.HARD_PASS
    assert decision.matched_rule == "l0_explicit_remember"


@pytest.mark.parametrize(
    "text",
    [
        "哈哈哈哈你说得也太直接了。",
        "好的，我知道了。",
        "那我现在应该怎么回她？",
        "你觉得她这样正常吗？",
        "早上好。",
        "你能不能帮我分析一下她到底在想什么？",
        "今天路上堵死了，我差点迟到。",
    ],
)
def test_route_v2_hard_drops_only_clear_no_memory_inputs(text: str) -> None:
    decision = MemoryGate().route_v2(text)

    assert decision.l0_route == MemoryL0Route.HARD_DROP
    assert decision.should_extract is False
    assert decision.l0_semantic_hint in {
        MemorySemanticGateReason.NO_MEMORY,
        MemorySemanticGateReason.SMALL_TALK,
    }


@pytest.mark.parametrize(
    "text",
    [
        "我最近总觉得她可能没以前那么喜欢我了。",
        "她最近还是会约我吃饭看电影，但已经很少主动让我见她的朋友了。",
        "她刚刚十分钟没回我，我有点烦。",
        "我今天心情特别差，什么都不想干。",
        "她会主动叫我参加朋友聚会，也会把我介绍给她关系比较好的朋友，但她一直不太愿意让我见父母。",
    ],
)
def test_route_v2_delegates_ambiguous_semantics_to_flash(text: str) -> None:
    decision = MemoryGate().route_v2(text)

    assert decision.l0_route == MemoryL0Route.SEMANTIC_REVIEW
    assert decision.should_extract is True
    assert decision.l0_semantic_hint is None
    assert decision.semantic_gate_reason is None


@pytest.mark.parametrize(
    ("assistant_question", "user_reply"),
    [
        ("你们这次吵架大概持续了多久？", "一周。"),
        ("你们这次主要是因为什么吵起来的？", "消费观。"),
        ("这次是谁先提的分手？", "她。"),
        ("你们大概多久没见面了？", "快三个月了。"),
        ("她后来有主动跟你道歉吗？", "没有。"),
        ("你们主要是因为什么吵架？", "我也不知道。"),
        (
            "你们这次矛盾持续了多久？",
            "真吵其实就两天，后面差不多冷战了一周，中间也不是完全不说话，就是只聊必要的事情。",
        ),
        (
            "你们这次主要为什么吵？",
            "主要还是钱吧，她觉得我花钱太随意，我觉得她什么都要算得特别细，最后就越说越上头。",
        ),
        ("你们这次为什么吵架？", "不太想说这个。"),
        ("你们为什么吵架？", "我先去开会了，晚点再说。"),
        (
            "你们为什么吵架？",
            "对了，她其实特别爱吃日料，尤其喜欢寿司。",
        ),
        (
            "她最近还会主动跟你聊自己的情绪吗？",
            "基本没有了。",
        ),
    ],
)
def test_route_v2_passes_all_direct_pending_memory_replies(
    assistant_question: str,
    user_reply: str,
) -> None:
    decision = MemoryGate().route_v2(
        user_reply,
        conversation_history=[
            _message(1, MessageRole.ASSISTANT, assistant_question)
        ],
    )

    assert decision.l0_route == MemoryL0Route.CONTEXT_PASS
    assert decision.should_extract is True
    assert (
        decision.l0_semantic_hint
        == MemorySemanticGateReason.CONTEXT_DEPENDENT_REPLY
    )
    assert decision.semantic_gate_reason is None


def test_route_v2_inherits_pending_slot_through_assistant_confirmation() -> None:
    history = [
        _message(1, MessageRole.ASSISTANT, "你们冷战多久了？"),
        _message(2, MessageRole.USER, "差不多一个月吧。"),
        _message(3, MessageRole.ASSISTANT, "一个月左右，对吗？"),
    ]

    decision = MemoryGate().route_v2(
        "不是一个月，我刚想了下，应该差不多两周。",
        conversation_history=history,
    )

    assert decision.l0_route == MemoryL0Route.CONTEXT_PASS
    assert decision.matched_rule == "pending_memory_confirmation"


def test_route_v2_pending_question_is_valid_for_second_user_reply() -> None:
    history = [
        _message(1, MessageRole.ASSISTANT, "你们这次吵架持续了多久？"),
        _message(2, MessageRole.USER, "我得想想。"),
    ]

    decision = MemoryGate().route_v2("一周。", conversation_history=history)

    assert decision.l0_route == MemoryL0Route.CONTEXT_PASS


def test_route_v2_pending_question_expires_before_third_user_reply() -> None:
    history = [
        _message(1, MessageRole.ASSISTANT, "你们这次吵架持续了多久？"),
        _message(2, MessageRole.USER, "我得想想。"),
        _message(3, MessageRole.USER, "还没想起来。"),
    ]

    decision = MemoryGate().route_v2("一周。", conversation_history=history)

    assert decision.l0_route != MemoryL0Route.CONTEXT_PASS


def test_route_v2_new_assistant_message_replaces_old_pending_question() -> None:
    history = [
        _message(1, MessageRole.ASSISTANT, "你们这次吵架持续了多久？"),
        _message(2, MessageRole.USER, "我得想想。"),
        _message(3, MessageRole.ASSISTANT, "好的，你慢慢想。"),
    ]

    decision = MemoryGate().route_v2("一周。", conversation_history=history)

    assert decision.l0_route != MemoryL0Route.CONTEXT_PASS


def test_route_v2_non_memory_assistant_question_does_not_create_pending() -> None:
    decision = MemoryGate().route_v2(
        "寿司。",
        conversation_history=[
            _message(1, MessageRole.ASSISTANT, "你今天想吃什么？")
        ],
    )

    assert decision.l0_route != MemoryL0Route.CONTEXT_PASS


def test_route_v2_does_not_change_legacy_evaluate_contract() -> None:
    gate = MemoryGate()

    consultation = gate.evaluate("我现在应该怎么跟她道歉？")
    durable = gate.evaluate("她不吃生食，真正喜欢的是粤菜。")

    assert consultation.should_extract is False
    assert consultation.reason == MemoryGateReason.CONSULTATION_ONLY
    assert durable.should_extract is True
    assert durable.reason == MemoryGateReason.DURABLE_SIGNAL
    assert consultation.l0_route is None
    assert durable.l0_route is None

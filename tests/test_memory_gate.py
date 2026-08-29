from datetime import UTC, datetime

import pytest

from loveapp.application.memory_gate import MemoryGate
from loveapp.domain.memory import (
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    MessageRole,
    StoredMessage,
    memory_dedupe_key,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("你好，在吗？", False),
        ("什么叫非暴力沟通？", False),
        ("把刚才的建议压缩成两句话。", False),
        ("我该怎么追求喜欢的女生？", False),
        ("假设一个人总不回消息，一般应该怎么办？", False),
        ("你刚才为什么检索了这三条文档？", False),
        ("晚上好啊", False),
        ("我下周准备带她去吃饭，你觉得选择消费高一些的餐厅好呢，还是低一些的", True),
        ("我俩最近被分到了同一个课程作业小组，下周有机会一起小组讨论，我想把握这次机会", True),
        ("下周有个社团活动，我准备参加。", True),
        ("后天要和她一起参加课程讨论。", True),
        ("如果下周有活动，我就去参加。", False),
        ("以后有机会再和她聊聊吧。", False),
        ("她不吃生食，真正喜欢的是粤菜。", True),
        ("最近两周我们联系明显变少了。", True),
        ("昨晚我们因为迟到吵了一架。", True),
        ("她最近主动找我聊天，这是不是说明关系在变好？", True),
        ("每个月最后一个周日，我们会一起复盘本月的相处。", True),
        ("她刚说还是改到上午吧。", True),
        ("她刚刚回我消息了，还给我道歉了。", True),
        ("我决定先请她吃顿饭，然后再聊聊消费观。", True),
        ("我们刚认识不久，目前还不太熟。", True),
        ("每周都能在社团见面，接触机会很多。", True),
        ("虽然认识很久，但平时几乎没有机会碰面。", True),
        ("相处一阵以后，我们逐渐熟络了一些。", True),
        ("她老家在扬州，还有一个正在读大学的弟弟。", True),
        ("我性格比较慢热，不太擅长主动开启话题。", True),
        ("我还不知道她是不是单身，直接问会不会太唐突？", True),
        ("她明确告诉我自己目前单身。", True),
        ("她刚才给我分享了一本小说，我该怎么接着聊？", True),
        (
            "我和她有一个课程作业小组，组里讨论时能聊上几句话，"
            "但都是课程相关的，其他的不怎么聊。",
            True,
        ),
        (
            "对方很优秀，成绩好又漂亮，可是我就是一个普通学生，"
            "没什么长处，我真的能追到吗？",
            True,
        ),
        ("我们不怎么聊天。", True),
        (
            "我对象其实平时都很勤俭节约，她买衣服鞋子都是买很经济实惠的，"
            "可能就是因为消费观念不一样造成的吧，你觉得呢",
            True,
        ),
        ("我考虑到她的消费观，我还是选择了一家平价餐厅，她得知之后很开心，我俩和好了", True),
        (
            "听说她最近和一个男生经常一起聊天，感觉那个男孩子也在追求她，"
            "他比我优秀，你觉得我希望大吗",
            True,
        ),
    ],
)
def test_memory_gate(text: str, expected: bool) -> None:
    decision = MemoryGate().evaluate(text)

    assert decision.should_extract is expected


def test_memory_gate_keeps_durable_claims_in_mixed_questions() -> None:
    preference = MemoryGate().evaluate(
        "我对象平时都很勤俭节约，她买东西很经济实惠，你觉得消费观不同怎么办？"
    )
    outcome = MemoryGate().evaluate(
        "我考虑她的消费观选择了平价餐厅，她得知后很开心，我们也和好了。"
    )

    assert preference.should_extract is True
    assert "preference" in preference.signals
    assert outcome.should_extract is True
    assert "advice_outcome" in outcome.signals


@pytest.mark.parametrize(
    "text",
    [
        "她最近开始经常带我参加她朋友的聚会。",
        "她现在愿意带我认识她的朋友。",
        "但她暂时还不愿意让我去见她父母。",
        "最近一个月她几乎不再让我参加她朋友的活动。",
        "昨天有一个聚会她没有叫我。",
    ],
)
def test_memory_gate_accepts_durable_social_integration_signals(text: str) -> None:
    decision = MemoryGate().evaluate(text)

    assert decision.should_extract is True
    assert decision.reason.value == "durable_signal"
    assert "social_integration" in decision.signals
    assert decision.matched_rule is not None
    assert decision.matched_rule.startswith("social_integration_")
    assert decision.matched_span


@pytest.mark.parametrize(
    "text",
    [
        "我现在应该怎么跟她道歉？",
        "怎么跟她道歉？",
        "我该不该跟她道歉？",
        "我要不要向她道歉？",
        "如何跟她道歉？",
        "我要怎么跟她道歉？",
        "我现在要怎么跟她道歉？",
        "我想知道怎么跟她道歉？",
    ],
)
def test_memory_gate_rejects_pure_relationship_action_consultations(text: str) -> None:
    decision = MemoryGate().evaluate(text)

    assert decision.should_extract is False
    assert decision.reason.value == "consultation_only"
    assert decision.matched_rule is not None
    assert decision.matched_rule.startswith("relationship_action_consultation_")


def test_relationship_action_consultation_guard_preserves_observed_fact() -> None:
    decision = MemoryGate().evaluate(
        "她最近主动找我聊天，我该怎么跟她道歉？"
    )

    assert decision.should_extract is True
    assert "temporal_interaction" in decision.signals


def test_social_integration_hypothesis_still_fails_closed() -> None:
    decision = MemoryGate().evaluate(
        "如果她以后突然不理我了，我应该怎么办？"
    )

    assert decision.should_extract is False
    assert decision.reason.value == "hypothetical"
    assert decision.matched_rule == "hypothetical_1"


def test_memory_gate_marks_future_events_without_dropping_shared_context() -> None:
    decision = MemoryGate().evaluate(
        "我俩最近被分到了同一个课程作业小组，下周有机会一起小组讨论，"
        "我想把握这次机会，争取和她聊上几句，你有啥好方法吗"
    )

    assert decision.should_extract is True
    assert "shared_context" in decision.signals
    assert "planned_event" in decision.signals


def test_memory_gate_does_not_label_habitual_weekend_contact_as_future_plan() -> None:
    decision = MemoryGate().evaluate(
        "平时电话里通常是我主动聊天，周末见面时她偶尔会先问候我。"
    )

    assert decision.should_extract is True
    assert "planned_event" not in decision.signals


@pytest.mark.parametrize(
    "text",
    [
        "她最近不怎么理我。",
        "她最近不太理我。",
        "她最近不太搭理我。",
        "她最近很少理我。",
        "她最近很少回复我。",
        "她最近几乎不回复我。",
        "她最近对我爱答不理。",
        "她最近回复变少了。",
        "她最近联系变少了。",
        "她最近聊天越来越少。",
        "但她最近不怎么理我，我也不知道我做错了啥，当然也可能是她心情不好",
    ],
)
def test_memory_gate_recognizes_interaction_decline_phrases(text: str) -> None:
    decision = MemoryGate().evaluate(text)

    assert decision.should_extract is True
    assert "interaction_decline" in decision.signals or "temporal_interaction" in decision.signals
    assert decision.matched_rule in {
        "temporal_interaction_decline",
        "subject_interaction_decline",
        "interaction_decline",
    }
    assert decision.matched_span


@pytest.mark.parametrize(
    "text",
    [
        "我最近不怎么理解她的想法。",
        "这个方案不太理想。",
        "我很少理解复杂概念。",
        "最近状态不太理想。",
        "这个解释几乎不合理。",
    ],
)
def test_memory_gate_does_not_confuse_non_interaction_phrases(text: str) -> None:
    decision = MemoryGate().evaluate(text)

    assert decision.should_extract is False
    assert decision.reason.value == "no_durable_signal"
    assert decision.matched_rule == "no_durable_signal"


@pytest.mark.parametrize(
    "text",
    [
        "她一天只回两三条消息。",
        "她每次回复都不算敷衍。",
        "我们线下见面其实还挺正常。",
        "基本都是我主动联系她。",
    ],
)
def test_memory_gate_accepts_direct_interaction_pattern_qualifiers(text: str) -> None:
    decision = MemoryGate().evaluate(text)

    assert decision.should_extract is True
    assert decision.reason.value == "durable_signal"
    assert "interaction_qualifier" in decision.signals


def test_memory_gate_resolves_duration_against_recent_user_contact_memory() -> None:
    source = StoredMessage(
        id="contact-source",
        conversation_id="conversation",
        user_id="user",
        relationship_id="relationship",
        role=MessageRole.USER,
        content="她最近回复越来越慢。",
    )
    memory = _contact_frequency_memory(source_message_id=source.id)

    decision = MemoryGate().evaluate(
        "持续了一个月了，你觉得这是兴趣下降了吗？",
        conversation_history=[source],
        existing_memories=[memory],
    )

    assert decision.should_extract is True
    assert decision.reason.value == "contextual_update"
    assert decision.matched_rule == "contextual_duration"
    assert decision.matched_span == "持续了一个月了"
    assert decision.selected_target_memory_id == memory.id
    assert decision.target_guard_result == "compatible_active_target"


def test_memory_gate_rejects_pure_partner_interest_hypothesis() -> None:
    decision = MemoryGate().evaluate("你觉得她是不是不喜欢我了？")

    assert decision.should_extract is False
    assert decision.reason.value == "consultation_only"
    assert decision.matched_rule == "pure_partner_hypothesis_1"


def _contact_frequency_memory(*, source_message_id: str) -> MemoryItem:
    candidate = MemoryItem(
        id="contact-frequency",
        user_id="user",
        relationship_id="relationship",
        source_message_id=source_message_id,
        status=MemoryStatus.CONFIRMED,
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="relationship",
        summary="用户报告最近线上联系频率降低",
        original_text="她最近回复越来越慢。",
        evidence_spans=["她最近回复越来越慢"],
        canonical_predicate="interaction.contact_frequency",
        raw_predicate="reply_frequency_declined",
        payload={
            "predicate": "reply_frequency_declined",
            "metric": "contact_frequency",
            "direction": "decreasing",
            "channel": "messaging",
        },
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
        updated_at=datetime(2026, 8, 1, tzinfo=UTC),
        dedupe_key="placeholder",
    )
    return candidate.model_copy(update={"dedupe_key": memory_dedupe_key(candidate)})

from pathlib import Path

import pytest

from loveapp.adapters.knowledge.markdown import load_qa_markdown, parse_qa_markdown
from loveapp.domain.enums import (
    AdviceGoal,
    AdviceScenario,
    RelationshipStage,
    RiskLevel,
    SourceType,
)


def test_formal_knowledge_uses_one_question_per_chunk() -> None:
    source = Path(__file__).parents[1] / "loveapp_rag_knowledge_base_formal_v1.md"

    documents = load_qa_markdown(source)

    assert len(documents) == 50
    assert [document.ordinal for document in documents] == list(range(1, 51))
    assert all(document.question and document.answer for document in documents)
    assert documents[0].id == "formal_v1_001"
    assert documents[-1].id == "formal_v1_050"
    assert documents[-1].risk_level == RiskLevel.HIGH
    assert AdviceGoal.INITIATE in documents[12].goals
    assert AdviceGoal.INITIATE not in documents[15].goals


def test_parser_keeps_complete_answer_in_single_chunk() -> None:
    source = """
# 一、冲突处理
## 1. 吵架后怎么办？
**标签：** 吵架、沟通
**问：** 我们刚刚吵架了，应该怎么办？
**答：**
先暂停激烈争论。
然后约定恢复沟通的时间。
"""

    documents = parse_qa_markdown(source, "test.md")

    assert len(documents) == 1
    assert documents[0].scenario == AdviceScenario.CONFLICT
    assert documents[0].answer == "先暂停激烈争论。\n然后约定恢复沟通的时间。"


def test_parser_maps_all_v2_fields_and_explicit_metadata_wins() -> None:
    source = """
# 冲突处理
## 1. 明确元数据示例
**ID:** kb_v2_test
**Scenario:** pursuit
**RelationshipStages:** stranger, dating
**Goals:** initiate、communicate
**RiskLevel:** sensitive
**SourceType:** reviewed_synthetic
**Version:** 2.0
**标签:** 初识、沟通
**问:** 我该怎样开启交流？
**QueryVariants:**
- 该怎么自然开口？
- 想认识对方，第一步怎么做？
**Context:** 适用于初识阶段。
**Principles:** 尊重自愿, 观察互惠
**RecommendedActions:**
- 从共同情境切入。
**SamplePhrases:**
- 最近那次活动挺有意思，你觉得呢？
**AvoidActions:** 连续催促、施压
**ClarifyingQuestions:** 对方是否愿意继续互动？
**答:** 先从共同情境自然开启交流。
"""

    document = parse_qa_markdown(source, "v2.md")[0]

    assert document.id == "kb_v2_test"
    assert document.scenario == AdviceScenario.PURSUIT
    assert document.relationship_stages == [
        RelationshipStage.STRANGER,
        RelationshipStage.DATING,
    ]
    assert document.goals == [AdviceGoal.INITIATE, AdviceGoal.COMMUNICATE]
    assert document.risk_level == RiskLevel.SENSITIVE
    assert document.source_type == SourceType.REVIEWED_SYNTHETIC
    assert document.version == "2.0"
    assert document.tags == ["初识", "沟通"]
    assert len(document.query_variants) == 2
    assert document.context == "适用于初识阶段。"
    assert document.principles == ["尊重自愿", "观察互惠"]
    assert document.recommended_actions == ["从共同情境切入。"]
    assert document.sample_phrases == ["最近那次活动挺有意思，你觉得呢？"]
    assert document.avoid_actions == ["连续催促", "施压"]
    assert document.clarifying_questions == ["对方是否愿意继续互动？"]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("Scenario", "unknown_scenario"),
        ("RelationshipStages", "unknown_stage"),
        ("Goals", "unknown_goal"),
        ("RiskLevel", "unknown_risk"),
        ("SourceType", "unknown_source"),
    ],
)
def test_v2_parser_rejects_invalid_enums(field_name: str, value: str) -> None:
    source = f"""
# 任意章节
## 1. 非法枚举
**ID:** invalid_enum
**{field_name}:** {value}
**问:** 问题
**答:** 答案
"""

    with pytest.raises(ValueError, match=rf"{field_name} 枚举值非法"):
        parse_qa_markdown(source, "invalid.md")


def test_v2_parser_rejects_empty_and_duplicate_ids() -> None:
    empty_id = """
# 任意章节
## 1. 空 ID
**ID:**
**问:** 问题
**答:** 答案
"""
    with pytest.raises(ValueError, match="ID 不能为空"):
        parse_qa_markdown(empty_id, "empty.md")

    duplicate_id = """
# 任意章节
## 1. 第一个
**ID:** duplicate
**问:** 问题一
**答:** 答案一
## 2. 第二个
**ID:** duplicate
**问:** 问题二
**答:** 答案二
"""
    with pytest.raises(ValueError, match="ID 重复"):
        parse_qa_markdown(duplicate_id, "duplicate.md")


def test_v2_corpus_has_exactly_500_unique_documents() -> None:
    source = Path(__file__).parents[1] / "knowledge" / "loveapp_rag_knowledge_base_v2.md"

    documents = load_qa_markdown(source)

    assert len(documents) == 500
    assert len({document.id for document in documents}) == 500
    assert all(len(document.query_variants) == 3 for document in documents)

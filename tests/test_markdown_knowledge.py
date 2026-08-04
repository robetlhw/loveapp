from pathlib import Path

from loveapp.adapters.knowledge.markdown import load_qa_markdown, parse_qa_markdown
from loveapp.domain.enums import AdviceGoal, AdviceScenario, RiskLevel


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

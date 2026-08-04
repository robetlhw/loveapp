from loveapp.application.advice_presentation import (
    AdvicePresentationMode,
    choose_advice_presentation,
    format_compact_advice,
)
from loveapp.domain.advice import AdviceResponse
from loveapp.domain.enums import AdviceScenario, RiskLevel


def test_simple_advice_is_compact_but_still_coherent() -> None:
    response = AdviceResponse(
        scenario=AdviceScenario.PURSUIT,
        problem_summary="用户想知道如何自然推进关系",
        assessment="对方愿意和你聊天是积极信号，但单次互动还不足以判断她已经产生明确好感。",
        recommended_actions=[
            "先围绕共同经历聊一个具体话题",
            "给对方留下自然接话的空间",
            "观察她是否会主动延续交流",
        ],
        sample_phrases=["下周小组讨论结束后，要不要一起喝杯咖啡？"],
        clarifying_questions=["她之前是否主动找你聊过？"],
    )

    assert choose_advice_presentation(response, query="我该怎么追她？") == (
        AdvicePresentationMode.COMPACT
    )
    text = format_compact_advice(response)
    assert len(text) >= 45
    assert "对方愿意和你聊天是积极信号" in text
    assert "1." in text and "3." in text
    assert "如果你想开口，可以这样说：“下周小组讨论结束后，要不要一起喝杯咖啡？”" in text
    assert "建议行动" not in text
    assert all(sentence for sentence in text.split("。") if sentence.strip())


def test_complex_and_high_risk_advice_keep_structured_presentation() -> None:
    conflict = AdviceResponse(
        scenario=AdviceScenario.CONFLICT,
        problem_summary="冲突问题",
        assessment="需要先降温，再处理具体事实。",
    )
    high_risk = AdviceResponse(
        scenario=AdviceScenario.PURSUIT,
        risk_level=RiskLevel.HIGH,
        problem_summary="安全问题",
        assessment="应先确保人身安全。",
    )

    assert choose_advice_presentation(conflict) == AdvicePresentationMode.STRUCTURED
    assert choose_advice_presentation(high_risk) == AdvicePresentationMode.STRUCTURED

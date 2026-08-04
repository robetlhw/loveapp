"""Adaptive user-facing presentation for structured advice responses."""

from enum import StrEnum

from loveapp.domain.advice import AdviceResponse
from loveapp.domain.enums import AdviceScenario, RiskLevel


class AdvicePresentationMode(StrEnum):
    COMPACT = "compact"
    STRUCTURED = "structured"


def choose_advice_presentation(
    response: AdviceResponse,
    *,
    query: str | None = None,
) -> AdvicePresentationMode:
    """Choose a surface style without changing the structured response."""

    if response.risk_level != RiskLevel.NORMAL:
        return AdvicePresentationMode.STRUCTURED
    if response.secondary_scenarios:
        return AdvicePresentationMode.STRUCTURED
    if response.scenario in {
        AdviceScenario.CONFLICT,
        AdviceScenario.BOUNDARY,
        AdviceScenario.BREAKUP,
    }:
        return AdvicePresentationMode.STRUCTURED
    if query is not None and len(query.strip()) > 120:
        return AdvicePresentationMode.STRUCTURED
    return AdvicePresentationMode.COMPACT


def format_compact_advice(response: AdviceResponse) -> str:
    """Turn semantic fields into a coherent, moderately sized reply."""

    paragraphs: list[str] = []
    lead = _complete_sentence(response.assessment or response.problem_summary)
    if lead:
        paragraphs.append(lead)

    actions = [_clean(value) for value in response.recommended_actions[:3] if _clean(value)]
    if actions:
        if len(actions) == 1:
            paragraphs.append(f"你可以先这样做：{_complete_sentence(actions[0])}")
        else:
            numbered = "\n".join(
                f"{index}. {_complete_sentence(value)}"
                for index, value in enumerate(actions, start=1)
            )
            paragraphs.append(f"比较稳妥的顺序是：\n{numbered}")

    phrases = [_clean(value) for value in response.sample_phrases if _clean(value)]
    if phrases:
        paragraphs.append(f"如果你想开口，可以这样说：“{_phrase_text(phrases[0])}”")

    questions = [_clean(value) for value in response.clarifying_questions if _clean(value)]
    if questions:
        paragraphs.append(
            f"如果你愿意补充，我最想先确认：{_complete_sentence(questions[0])}"
        )

    cautions = response.risk_notes or response.avoid_actions
    if cautions:
        avoid = _clean(cautions[0])
        if avoid:
            paragraphs.append(f"同时注意：{_complete_sentence(avoid)}")

    if not paragraphs or len("".join(paragraphs)) < 45:
        paragraphs.append(_fallback_advice(response.scenario))
    return "\n\n".join(paragraphs)


def _clean(value: str) -> str:
    return " ".join(value.strip().split())


def _complete_sentence(value: str) -> str:
    cleaned = _clean(value)
    if not cleaned:
        return ""
    if cleaned.endswith(("。", "！", "？", "!", "?", "；", ";")):
        return cleaned
    return f"{cleaned}。"


def _phrase_text(value: str) -> str:
    return _clean(value).rstrip("。；; ")


def _fallback_advice(scenario: AdviceScenario) -> str:
    if scenario == AdviceScenario.PURSUIT:
        return "先观察对方是否愿意主动延续互动，再根据连续的回应调整推进节奏。"
    if scenario == AdviceScenario.CHAT_ANALYSIS:
        return "先把实际发生的聊天事实和自己的推测分开，再根据持续的互动情况判断。"
    return "先把具体事实、自己的感受和希望达到的目标分开，再决定下一步。"

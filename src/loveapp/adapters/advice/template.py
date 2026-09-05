from collections.abc import Iterable

from loveapp.domain.advice import (
    AdviceRequest,
    AdviceResponse,
    KnowledgeReference,
    RelationshipContext,
)
from loveapp.domain.enums import AdviceScenario, RiskLevel
from loveapp.domain.knowledge import RetrievedDocument
from loveapp.domain.memory import StoredMessage
from loveapp.domain.policy import ResolvedScenarioPolicy
from loveapp.ports.advice import AdviceAttemptCallback, AdviceStreamCallback


class TemplateAdviceComposer:
    """Deterministic composer used until a grounded LLM adapter is configured."""

    async def compose(
        self,
        request: AdviceRequest,
        scenario: AdviceScenario,
        context: RelationshipContext,
        documents: list[RetrievedDocument],
        conversation_history: list[StoredMessage],
        policy: ResolvedScenarioPolicy,
        stream_callback: AdviceStreamCallback | None = None,
        attempt_callback: AdviceAttemptCallback | None = None,
        trace: object | None = None,
    ) -> AdviceResponse:
        del context, conversation_history, stream_callback, attempt_callback, trace

        if not documents:
            response = AdviceResponse(
                scenario=scenario,
                secondary_scenarios=request.secondary_scenarios,
                goal=request.goal,
                secondary_goals=request.secondary_goals,
                problem_summary=f"你想咨询：{request.query}",
                assessment="当前知识库没有召回足够匹配的内容，需要补充具体背景后再判断。",
                clarifying_questions=[
                    "事情发生的具体经过是什么？",
                    "你希望这次沟通达到什么结果？",
                    "对方是否已经明确表达过边界或需要空间？",
                ],
                recommended_actions=["先补充事实、双方反应和你的目标，不急于采取激进行动。"],
                avoid_actions=["仅凭猜测给对方下结论。"],
            )
            return response

        top_documents = documents[: policy.total_document_limit]
        primary = top_documents[0].document
        response = AdviceResponse(
            scenario=scenario,
            secondary_scenarios=request.secondary_scenarios,
            goal=request.goal,
            secondary_goals=request.secondary_goals,
            risk_level=_scenario_document_risk(
                top_documents,
                {scenario, *request.secondary_scenarios},
            ),
            problem_summary=f"你当前咨询的是{_scenario_label(scenario)}问题：{request.query}",
            assessment=primary.context or "建议先区分事实、感受和目标，再决定下一步行动。",
            clarifying_questions=_unique(
                question
                for match in top_documents
                for question in match.document.clarifying_questions
            )[:3],
            recommended_actions=_unique(
                action for match in top_documents for action in match.document.recommended_actions
            )[:5],
            sample_phrases=_unique(
                phrase for match in top_documents for phrase in match.document.sample_phrases
            )[:3],
            alternatives=_unique(
                principle for match in top_documents for principle in match.document.principles
            )[:3],
            avoid_actions=_unique(
                action for match in top_documents for action in match.document.avoid_actions
            )[:5],
            sources=[
                KnowledgeReference(
                    document_id=match.document.id,
                    title=match.document.title,
                    version=match.document.version,
                    source_type=match.document.source_type,
                    score=match.score,
                    base_score=match.base_score,
                    score_components=match.score_components,
                )
                for match in top_documents
            ],
        )
        return response


def _scenario_label(scenario: AdviceScenario) -> str:
    return {
        AdviceScenario.PURSUIT: "恋爱追求",
        AdviceScenario.CONFLICT: "关系冲突",
        AdviceScenario.CHAT_ANALYSIS: "聊天分析",
        AdviceScenario.RELATIONSHIP_MAINTENANCE: "关系经营",
        AdviceScenario.BOUNDARY: "关系边界",
        AdviceScenario.BREAKUP: "分手与修复",
    }[scenario]


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _risk_order(risk: RiskLevel) -> int:
    return {RiskLevel.NORMAL: 0, RiskLevel.SENSITIVE: 1, RiskLevel.HIGH: 2}[risk]


def _scenario_document_risk(
    documents: list[RetrievedDocument],
    scenarios: set[AdviceScenario],
) -> RiskLevel:
    return max(
        (
            match.document.risk_level
            for match in documents
            if match.document.scenario in scenarios
        ),
        key=_risk_order,
        default=RiskLevel.NORMAL,
    )

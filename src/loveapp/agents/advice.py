import asyncio
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from loveapp.application import MemoryService
from loveapp.application.routing import route_by_rules
from loveapp.application.scenario_policy import (
    ScenarioPolicyRegistry,
    default_scenario_policy_registry,
    enforce_scenario_policy,
)
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.advice import (
    AdviceRequest,
    AdviceResponse,
    AdviceTurnResult,
    RelationshipContext,
)
from loveapp.domain.enums import AdviceScenario, RiskLevel, TaskType
from loveapp.domain.knowledge import KnowledgeFilters, RetrievedDocument
from loveapp.domain.memory import MessageRole, RememberResult, StoredMessage
from loveapp.domain.policy import ResolvedScenarioPolicy
from loveapp.domain.routing import RouteInput, RouteResult
from loveapp.ports.advice import AdviceComposer, AdviceStreamCallback
from loveapp.ports.knowledge import KnowledgeRetriever
from loveapp.ports.routing import Router
from loveapp.safety.policy import SafetyAssessment, SafetyPolicy


class AdviceState(TypedDict, total=False):
    request: AdviceRequest
    context: RelationshipContext
    scenario: AdviceScenario
    safety: SafetyAssessment
    documents: list[RetrievedDocument]
    conversation_history: list[StoredMessage]
    current_message: StoredMessage
    memory_result: RememberResult
    memory_task: asyncio.Task[RememberResult]
    route: RouteResult
    policy: ResolvedScenarioPolicy
    response: AdviceResponse
    trace: ExecutionTrace
    stream_callback: AdviceStreamCallback | None
    wait_for_memory: bool


class AdviceAgent:
    def __init__(
        self,
        retriever: KnowledgeRetriever,
        memory_service: MemoryService,
        safety_policy: SafetyPolicy,
        composer: AdviceComposer,
        router: Router | None = None,
        policy_registry: ScenarioPolicyRegistry | None = None,
    ) -> None:
        self._retriever = retriever
        self._memory_service = memory_service
        self._safety_policy = safety_policy
        self._composer = composer
        self._router = router
        self._policy_registry = policy_registry or default_scenario_policy_registry()
        self._graph = self._build_graph()

    async def advise(self, request: AdviceRequest) -> AdviceResponse:
        return (await self.advise_turn(request, wait_for_memory=True)).response

    async def advise_turn(
        self,
        request: AdviceRequest,
        *,
        trace: ExecutionTrace | None = None,
        stream_callback: AdviceStreamCallback | None = None,
        wait_for_memory: bool = True,
    ) -> AdviceTurnResult:
        trace = trace or ExecutionTrace()
        try:
            state = await self._graph.ainvoke(
                {
                    "request": request,
                    "trace": trace,
                    "stream_callback": stream_callback,
                    "wait_for_memory": wait_for_memory,
                }
            )
        except BaseException:
            await trace.cancel_background_tasks()
            raise
        return AdviceTurnResult(
            response=state["response"],
            conversation_id=state["current_message"].conversation_id,
            memory_result=state.get("memory_result"),
        )

    def _build_graph(self):
        graph = StateGraph(AdviceState)
        graph.add_node("record_normal", self._record_message)
        graph.add_node("record_high", self._record_message)
        graph.add_node("load_context", self._load_context)
        graph.add_node("classify", self._classify)
        graph.add_node("assess_safety", self._assess_safety)
        graph.add_node("resolve_policy", self._resolve_policy)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("compose", self._compose)
        graph.add_node("enforce_policy", self._enforce_policy)
        graph.add_node("compose_safety", self._compose_safety)
        graph.add_node("save_response", self._save_response)

        graph.add_edge(START, "classify")
        graph.add_edge("classify", "assess_safety")
        graph.add_conditional_edges(
            "assess_safety",
            self._route_after_safety,
            {"normal": "record_normal", "high": "record_high"},
        )
        graph.add_edge("record_normal", "load_context")
        graph.add_edge("record_normal", "resolve_policy")
        graph.add_edge("record_high", "compose_safety")
        graph.add_edge("resolve_policy", "retrieve")
        graph.add_edge(["load_context", "retrieve"], "compose")
        graph.add_edge("compose", "enforce_policy")
        graph.add_edge("enforce_policy", "save_response")
        graph.add_edge("compose_safety", "save_response")
        graph.add_edge("save_response", END)
        return graph.compile()

    async def _record_message(self, state: AdviceState) -> dict:
        request = state["request"]
        with state["trace"].measure("user_message_persistence"):
            message = await self._memory_service.record_message(
                user_id=request.user_id,
                relationship_id=request.relationship_id,
                conversation_id=request.conversation_id,
                role=MessageRole.USER,
                content=request.query,
                relationship_stage=request.relationship_stage,
            )
        memory_task = self._memory_service.start_background_extraction(
            message=message,
            text=request.query,
            trace=state["trace"],
        )
        return {"current_message": message, "memory_task": memory_task}

    async def _load_context(self, state: AdviceState) -> dict:
        with state["trace"].measure("context_load"):
            request = state["request"]
            context = await self._memory_service.get_context(
                request.user_id,
                request.relationship_id,
                request.relationship_stage,
                query=request.query,
            )
            current_message = state["current_message"]
            history = await self._memory_service.get_conversation_history(
                request.user_id,
                request.relationship_id,
                current_message.conversation_id,
                exclude_message_id=current_message.id,
            )
            return {"context": context, "conversation_history": history}

    async def _classify(self, state: AdviceState) -> dict:
        with state["trace"].measure("advice_classification"):
            request = state["request"]
            if request.scenario is not None:
                return {"scenario": request.scenario}

            history = (
                await self._memory_service.get_conversation_history(
                    request.user_id,
                    request.relationship_id,
                    request.conversation_id,
                )
                if request.conversation_id
                else []
            )
            route_input = RouteInput(
                latest_query=request.query,
                recent_messages=history,
                active_task=TaskType.RELATIONSHIP_ADVICE,
                forced_task=TaskType.RELATIONSHIP_ADVICE,
            )
            route = (
                await self._router.route(route_input)
                if self._router is not None
                else route_by_rules(route_input)
            )
            routed_request = request.model_copy(
                update={
                    "scenario": route.primary_scenario,
                    "secondary_scenarios": route.secondary_scenarios,
                    "goal": route.primary_goal,
                    "secondary_goals": route.secondary_goals,
                }
            )
            return {
                "request": routed_request,
                "scenario": route.primary_scenario or AdviceScenario.RELATIONSHIP_MAINTENANCE,
                "route": route,
            }

    def _assess_safety(self, state: AdviceState) -> dict:
        with state["trace"].measure("safety_scan"):
            return {"safety": self._safety_policy.assess(state["request"].query)}

    @staticmethod
    def _route_after_safety(state: AdviceState) -> str:
        return "high" if state["safety"].risk_level == RiskLevel.HIGH else "normal"

    def _resolve_policy(self, state: AdviceState) -> dict:
        with state["trace"].measure("policy_resolution"):
            request = state["request"]
            return {
                "policy": self._policy_registry.resolve(
                    state["scenario"],
                    request.secondary_scenarios,
                    request.goal,
                    request.secondary_goals,
                )
            }

    async def _retrieve(self, state: AdviceState) -> dict:
        with state["trace"].measure("rag_retrieval"):
            request = state["request"]
            policy = state["policy"]
            scenario_weights = {
                scenario: limit / policy.total_document_limit
                for scenario, limit in policy.retrieval_limits.items()
            }
            documents = await self._retriever.search(
                query=request.query,
                filters=KnowledgeFilters(
                    scenario=state["scenario"],
                    scenarios=request.secondary_scenarios,
                    relationship_stage=request.relationship_stage,
                    goal=request.goal,
                    goals=request.secondary_goals,
                    scenario_weights=scenario_weights,
                ),
                limit=policy.total_document_limit,
                trace=state["trace"],
            )
            return {"documents": documents}

    async def _compose(self, state: AdviceState) -> dict:
        with state["trace"].measure("answer_generation"):
            response = await self._composer.compose(
                request=state["request"],
                scenario=state["scenario"],
                context=state["context"],
                documents=state.get("documents", []),
                conversation_history=state.get("conversation_history", []),
                policy=state["policy"],
                stream_callback=state.get("stream_callback"),
            )
            return {"response": response}

    def _enforce_policy(self, state: AdviceState) -> dict:
        with state["trace"].measure("policy_enforcement"):
            return {
                "response": enforce_scenario_policy(
                    state["response"],
                    state["policy"],
                    state["request"].query,
                    state["context"],
                )
            }

    async def _save_response(self, state: AdviceState) -> dict:
        memory_task = state.get("memory_task")
        memory_result: RememberResult | None = None
        if memory_task is not None and state.get("wait_for_memory", True):
            memory_result = await memory_task
        with state["trace"].measure("assistant_message_persistence"):
            request = state["request"]
            await self._memory_service.record_message(
                user_id=request.user_id,
                relationship_id=request.relationship_id,
                conversation_id=state["current_message"].conversation_id,
                role=MessageRole.ASSISTANT,
                content=_response_to_history_text(state["response"]),
                relationship_stage=request.relationship_stage,
            )
        if memory_task is not None and memory_result is None:
            if memory_task.done():
                try:
                    memory_result = memory_task.result()
                except Exception as exc:
                    memory_result = RememberResult(
                        message=state["current_message"],
                        extraction_error=str(exc),
                    )
            else:
                memory_result = RememberResult(
                    message=state["current_message"],
                    pending=True,
                )
        return {"memory_result": memory_result} if memory_result else {}

    def _compose_safety(self, state: AdviceState) -> dict:
        with state["trace"].measure("safety_response"):
            reasons = state["safety"].reasons
            response = AdviceResponse(
                scenario=state["scenario"],
                secondary_scenarios=state["request"].secondary_scenarios,
                goal=state["request"].goal,
                secondary_goals=state["request"].secondary_goals,
                risk_level=RiskLevel.HIGH,
                problem_summary="当前描述可能涉及人身安全、强迫、跟踪或自伤风险。",
                assessment="这类情况不适合按普通恋爱沟通问题处理，应先确保相关人员安全。",
                clarifying_questions=["现在是否有人处于即时危险中？"],
                recommended_actions=[
                    "优先离开可能发生伤害的环境，并联系可信任的人。",
                    "如存在即时危险，请联系当地紧急服务；中国大陆可拨打 110。",
                    "如涉及自伤风险，请立即联系身边可信任的人并寻求专业支持。",
                ],
                avoid_actions=["不要报复、威胁、跟踪或独自进行危险对抗。"],
                risk_notes=reasons,
            )
            return {"response": response}


def _classify_scenario(query: str) -> AdviceScenario:
    result = route_by_rules(
        RouteInput(
            latest_query=query,
            forced_task=TaskType.RELATIONSHIP_ADVICE,
        )
    )
    return result.primary_scenario or AdviceScenario.RELATIONSHIP_MAINTENANCE


def _response_to_history_text(response: AdviceResponse) -> str:
    parts = [response.problem_summary, response.assessment]
    if response.recommended_actions:
        parts.append("建议：" + "；".join(response.recommended_actions))
    if response.clarifying_questions:
        parts.append("待确认：" + "；".join(response.clarifying_questions))
    return "\n".join(parts)

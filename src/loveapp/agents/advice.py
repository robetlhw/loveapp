import asyncio
import inspect
from dataclasses import dataclass
from typing import TypedDict
from uuid import NAMESPACE_URL, uuid4, uuid5

from langgraph.graph import END, START, StateGraph

from loveapp.application import MemoryService
from loveapp.application.contextual_memory_updates import (
    may_contain_contextual_memory_update,
)
from loveapp.application.memory_gate import MemoryGate
from loveapp.application.routing import route_by_rules
from loveapp.application.scenario_policy import (
    ScenarioPolicyRegistry,
    default_scenario_policy_registry,
    enforce_scenario_policy,
)
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.advice import (
    AdviceGenerationAttempt,
    AdviceLogicalTurn,
    AdviceLogicalTurnStatus,
    AdviceRequest,
    AdviceResponse,
    AdviceStreamEvent,
    AdviceTurnClaimError,
    AdviceTurnResult,
    RelationshipContext,
)
from loveapp.domain.enums import AdviceScenario, RiskLevel, TaskType
from loveapp.domain.knowledge import KnowledgeFilters, RetrievedDocument
from loveapp.domain.memory import MessageRole, RememberResult, StoredMessage, utc_now
from loveapp.domain.policy import ResolvedScenarioPolicy
from loveapp.domain.relationship_plan import has_retrospective_event_semantics
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
    generation_attempts: list[AdviceGenerationAttempt]
    synchronize_current_turn: bool
    logical_turn: AdviceLogicalTurn
    generation_no: int
    execution: "_AdviceTurnExecution"


@dataclass
class _AdviceTurnExecution:
    owns_failure_transition: bool = False


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
        request = request.model_copy(
            update={
                "conversation_id": request.conversation_id or str(uuid4()),
                "logical_turn_id": request.logical_turn_id or str(uuid4()),
            }
        )
        execution = _AdviceTurnExecution()
        try:
            state = await self._graph.ainvoke(
                {
                    "request": request,
                    "trace": trace,
                    "stream_callback": stream_callback,
                    "wait_for_memory": wait_for_memory,
                    "execution": execution,
                }
            )
        except asyncio.CancelledError as exc:
            await asyncio.shield(
                self._fail_recorded_turn(request, exc, trace, execution)
            )
            raise
        except AdviceTurnClaimError:
            raise
        except Exception as exc:
            await self._fail_recorded_turn(request, exc, trace, execution)
            raise
        return AdviceTurnResult(
            response=state["response"],
            conversation_id=state["current_message"].conversation_id,
            memory_result=state.get("memory_result"),
            generation_attempts=state.get("generation_attempts", []),
            logical_turn_id=state["request"].logical_turn_id,
        )

    def _build_graph(self):
        graph = StateGraph(AdviceState)
        graph.add_node("record_normal", self._record_message)
        graph.add_node("record_sensitive", self._record_message)
        graph.add_node("record_high", self._record_message)
        graph.add_node("load_context", self._load_context)
        graph.add_node("synchronize_current_turn", self._synchronize_current_turn)
        graph.add_node("classify", self._classify)
        graph.add_node("assess_safety", self._assess_safety)
        graph.add_node("resolve_policy", self._resolve_policy)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("compose", self._compose)
        graph.add_node("enforce_policy", self._enforce_policy)
        graph.add_node("compose_sensitive_safety", self._compose_sensitive_safety)
        graph.add_node("compose_safety", self._compose_safety)
        graph.add_node("save_response", self._save_response)

        graph.add_edge(START, "classify")
        graph.add_edge("classify", "assess_safety")
        graph.add_conditional_edges(
            "assess_safety",
            self._route_after_safety,
            {
                "normal": "record_normal",
                "sensitive": "record_sensitive",
                "high": "record_high",
            },
        )
        graph.add_edge("record_normal", "synchronize_current_turn")
        graph.add_edge("synchronize_current_turn", "load_context")
        graph.add_edge("record_normal", "resolve_policy")
        graph.add_edge("record_high", "compose_safety")
        graph.add_edge("record_sensitive", "compose_sensitive_safety")
        graph.add_edge("resolve_policy", "retrieve")
        graph.add_edge(["load_context", "retrieve"], "compose")
        graph.add_edge("compose", "enforce_policy")
        graph.add_edge("enforce_policy", "save_response")
        graph.add_edge("compose_sensitive_safety", "save_response")
        graph.add_edge("compose_safety", "save_response")
        graph.add_edge("save_response", END)
        return graph.compile()

    async def _record_message(self, state: AdviceState) -> dict:
        request = state["request"]
        logical_turn_id = _required_logical_turn_id(request)
        existing_turn = await self._memory_service.get_advice_logical_turn(
            logical_turn_id,
            user_id=request.user_id,
            relationship_id=request.relationship_id,
            conversation_id=_required_conversation_id(request),
        )
        if existing_turn is not None:
            _validate_logical_turn_request(existing_turn, request)
            if not request.retry_generation:
                raise AdviceTurnClaimError(
                    "该建议逻辑轮次已经提交，不能并发或重复生成。"
                )
        if request.retry_generation:
            if existing_turn is None:
                raise ValueError("没有找到可重试的建议轮次。")
            if existing_turn.status != AdviceLogicalTurnStatus.GENERATION_FAILED:
                raise ValueError("该建议轮次当前不可重试。")

        user_message_id = (
            existing_turn.user_message_id
            if existing_turn is not None
            else _logical_message_id(logical_turn_id, MessageRole.USER)
        )
        with state["trace"].measure("user_message_persistence") as details:
            message = await self._memory_service.record_message(
                user_id=request.user_id,
                relationship_id=request.relationship_id,
                conversation_id=request.conversation_id,
                role=MessageRole.USER,
                content=request.query,
                relationship_stage=request.relationship_stage,
                message_id=user_message_id,
            )
            details["logical_turn_id"] = logical_turn_id
            details["message_reused"] = existing_turn is not None

        logical_turn = existing_turn
        if logical_turn is None:
            now = utc_now()
            logical_turn = await self._memory_service.create_advice_logical_turn(
                AdviceLogicalTurn(
                    id=logical_turn_id,
                    user_id=request.user_id,
                    relationship_id=request.relationship_id,
                    conversation_id=message.conversation_id,
                    user_message_id=message.id,
                    query=request.query,
                    request_payload=request.model_dump(mode="json"),
                    status=AdviceLogicalTurnStatus.MEMORY_STARTED,
                    created_at=now,
                    updated_at=now,
                ),
                reject_existing=True,
            )
            state["execution"].owns_failure_transition = True
        if request.retry_generation:
            return {
                "request": request,
                "current_message": message,
                "logical_turn": logical_turn,
                "synchronize_current_turn": False,
            }

        memory_task = self._memory_service.start_background_extraction(
            message=message,
            text=request.query,
            trace=state["trace"],
            pending_memory_context=request.pending_memory_context,
        )
        return {
            "request": request,
            "current_message": message,
            "logical_turn": logical_turn,
            "memory_task": memory_task,
            "synchronize_current_turn": _requires_current_turn_state_sync(request.query),
        }

    async def _synchronize_current_turn(self, state: AdviceState) -> dict:
        required = state.get("synchronize_current_turn", False)
        memory_task = state.get("memory_task")
        with state["trace"].measure("current_turn_state_sync") as details:
            details["required"] = required
            if not required or memory_task is None:
                details["waited"] = False
                return {}
            details["waited"] = True
            result = await asyncio.shield(memory_task)
            details["gate_reason"] = (
                result.gate_decision.reason.value if result.gate_decision else None
            )
            details["saved_count"] = len(result.saved)
            details["extraction_error"] = result.extraction_error
            return {"memory_result": result}

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
        with state["trace"].measure("safety_scan") as details:
            request = state["request"]
            if request.forced_risk_level in {RiskLevel.HIGH, RiskLevel.SENSITIVE}:
                details["forced_by_router"] = True
                return {
                    "safety": SafetyAssessment(
                        risk_level=request.forced_risk_level,
                        reasons=(
                            request.forced_risk_reasons
                            or [
                                "上游路由已判定为高风险上下文"
                                if request.forced_risk_level == RiskLevel.HIGH
                                else "上游路由已判定为敏感安全上下文"
                            ]
                        ),
                        inherited=True,
                    )
                }
            return {"safety": self._safety_policy.assess(request.query)}

    @staticmethod
    def _route_after_safety(state: AdviceState) -> str:
        return {
            RiskLevel.NORMAL: "normal",
            RiskLevel.SENSITIVE: "sensitive",
            RiskLevel.HIGH: "high",
        }[state["safety"].risk_level]

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
        attempts: list[AdviceGenerationAttempt] = []
        request = state["request"]
        logical_turn_id = _required_logical_turn_id(request)
        logical_turn = await self._memory_service.begin_advice_generation(
            logical_turn_id,
            user_id=request.user_id,
            relationship_id=request.relationship_id,
            conversation_id=_required_conversation_id(request),
            retry=request.retry_generation,
        )
        state["execution"].owns_failure_transition = True
        with state["trace"].measure("answer_generation") as details:
            details["logical_turn_id"] = logical_turn_id
            details["generation_no"] = logical_turn.generation_count
            kwargs = {
                "request": request,
                "scenario": state["scenario"],
                "context": state["context"],
                "documents": state.get("documents", []),
                "conversation_history": state.get("conversation_history", []),
                "policy": state["policy"],
                # Model fragments are not trusted presentation content.
                "stream_callback": None,
            }
            if _supports_keyword(self._composer.compose, "attempt_callback"):
                kwargs["attempt_callback"] = attempts.append
            if _supports_keyword(self._composer.compose, "trace"):
                kwargs["trace"] = state["trace"]
            try:
                response = await self._composer.compose(**kwargs)
                await self._memory_service.save_advice_generation_attempts(
                    logical_turn_id,
                    logical_turn.generation_count,
                    attempts,
                    user_id=request.user_id,
                    relationship_id=request.relationship_id,
                    conversation_id=_required_conversation_id(request),
                )
            except Exception as exc:
                if attempts:
                    await self._memory_service.save_advice_generation_attempts(
                        logical_turn_id,
                        logical_turn.generation_count,
                        attempts,
                        user_id=request.user_id,
                        relationship_id=request.relationship_id,
                        conversation_id=_required_conversation_id(request),
                    )
                await self._memory_service.fail_advice_logical_turn(
                    logical_turn_id,
                    user_id=request.user_id,
                    relationship_id=request.relationship_id,
                    conversation_id=_required_conversation_id(request),
                    last_error_type=_generation_error_name(attempts, exc),
                    fallback_used=False,
                )
                raise
            details["attempt_count"] = len(attempts) or 1
            details["fallback_used"] = any(item.fallback_used for item in attempts)
            return {
                "response": response,
                "generation_attempts": attempts,
                "logical_turn": logical_turn,
                "generation_no": logical_turn.generation_count,
            }

    def _enforce_policy(self, state: AdviceState) -> dict:
        with state["trace"].measure("policy_enforcement"):
            response = enforce_scenario_policy(
                state["response"],
                state["policy"],
                state["request"].query,
                state["context"],
            )
            return {"response": response}

    async def _save_response(self, state: AdviceState) -> dict:
        memory_task = state.get("memory_task")
        memory_result: RememberResult | None = state.get("memory_result")
        if (
            memory_result is None
            and memory_task is not None
            and state.get("wait_for_memory", True)
        ):
            memory_result = await asyncio.shield(memory_task)
        request = state["request"]
        logical_turn_id = _required_logical_turn_id(request)
        fallback_used = any(
            attempt.fallback_used for attempt in state.get("generation_attempts", [])
        )
        with state["trace"].measure("assistant_message_persistence") as details:
            details["logical_turn_id"] = logical_turn_id
            details["fallback_used"] = fallback_used
            if fallback_used:
                await self._memory_service.fail_advice_logical_turn(
                    logical_turn_id,
                    user_id=request.user_id,
                    relationship_id=request.relationship_id,
                    conversation_id=_required_conversation_id(request),
                    last_error_type=_generation_error_name(
                        state.get("generation_attempts", [])
                    ),
                    fallback_used=True,
                )
                details["persisted"] = False
            else:
                if "generation_no" not in state:
                    logical_turn = await self._memory_service.begin_advice_generation(
                        logical_turn_id,
                        user_id=request.user_id,
                        relationship_id=request.relationship_id,
                        conversation_id=_required_conversation_id(request),
                        retry=request.retry_generation,
                    )
                    state["execution"].owns_failure_transition = True
                    state["generation_no"] = logical_turn.generation_count
                await self._memory_service.ensure_context(
                    request.user_id,
                    request.relationship_id,
                    request.relationship_stage,
                )
                await self._memory_service.complete_advice_logical_turn(
                    logical_turn_id,
                    user_id=request.user_id,
                    relationship_id=request.relationship_id,
                    conversation_id=_required_conversation_id(request),
                    message_id=_logical_message_id(
                        logical_turn_id,
                        MessageRole.ASSISTANT,
                    ),
                    content=_response_to_history_text(state["response"]),
                )
                details["persisted"] = True
        _emit_validated_response(state["response"], state.get("stream_callback"))
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

    async def _fail_recorded_turn(
        self,
        request: AdviceRequest,
        error: BaseException,
        trace: ExecutionTrace,
        execution: _AdviceTurnExecution,
    ) -> None:
        logical_turn_id = request.logical_turn_id
        if logical_turn_id is None:
            return
        with trace.measure("advice_turn_failure") as details:
            details["logical_turn_id"] = logical_turn_id
            details["error_type"] = type(error).__name__
            details["owned"] = execution.owns_failure_transition
            if not execution.owns_failure_transition:
                details["status_persisted"] = False
                details["reason"] = "logical_turn_not_owned"
                return
            try:
                turn = await self._memory_service.get_advice_logical_turn(
                    logical_turn_id,
                    user_id=request.user_id,
                    relationship_id=request.relationship_id,
                    conversation_id=_required_conversation_id(request),
                )
                if turn is None or not _logical_turn_matches_request_scope(turn, request):
                    details["status_persisted"] = False
                    return
                if turn.status == AdviceLogicalTurnStatus.GENERATION_FAILED:
                    details["status_persisted"] = True
                    details["already_failed"] = True
                    return
                if turn.status not in {
                    AdviceLogicalTurnStatus.MEMORY_STARTED,
                    AdviceLogicalTurnStatus.GENERATION_IN_PROGRESS,
                }:
                    details["status_persisted"] = False
                    details["terminal_status"] = turn.status.value
                    return
                await self._memory_service.fail_advice_logical_turn(
                    logical_turn_id,
                    user_id=request.user_id,
                    relationship_id=request.relationship_id,
                    conversation_id=_required_conversation_id(request),
                    last_error_type=_generation_error_name([], error),
                    fallback_used=False,
                )
                details["status_persisted"] = True
            except Exception as persistence_error:
                details["status_persisted"] = False
                details["persistence_error"] = type(persistence_error).__name__

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

    def _compose_sensitive_safety(self, state: AdviceState) -> dict:
        with state["trace"].measure("sensitive_safety_response"):
            reasons = state["safety"].reasons
            response = AdviceResponse(
                scenario=state["scenario"],
                secondary_scenarios=state["request"].secondary_scenarios,
                goal=state["request"].goal,
                secondary_goals=state["request"].secondary_goals,
                risk_level=RiskLevel.SENSITIVE,
                problem_summary="当前内容涉及需要谨慎处理的安全或心理压力信号。",
                assessment="我会先关注你的当下安全和情绪，不把这段内容按普通关系问题继续推演。",
                clarifying_questions=["你现在是否处于安全的地方，身边是否有可以联系的人？"],
                recommended_actions=[
                    "先暂停可能让你或他人受伤的行动，去一个安全、有人在的地方。",
                    "联系可信任的家人、朋友或当地专业支持，暂时不要独自承受。",
                    "如果风险正在升级或无法保证安全，请立即联系当地紧急服务。",
                ],
                avoid_actions=[
                    "不要独自接近冲突现场，也不要用酒精、武器或威胁来处理当前情绪。"
                ],
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


def _supports_keyword(callable_object: object, keyword: str) -> bool:
    try:
        parameters = inspect.signature(callable_object).parameters
    except (TypeError, ValueError):
        return True
    return keyword in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def _logical_message_id(logical_turn_id: str, role: MessageRole) -> str:
    return str(uuid5(NAMESPACE_URL, f"loveapp:advice:{logical_turn_id}:{role.value}"))


def _required_logical_turn_id(request: AdviceRequest) -> str:
    if request.logical_turn_id is None:  # guarded by _record_message
        raise RuntimeError("advice logical turn was not initialized")
    return request.logical_turn_id


def _required_conversation_id(request: AdviceRequest) -> str:
    if request.conversation_id is None:  # normalized by advise_turn
        raise RuntimeError("advice conversation was not initialized")
    return request.conversation_id


def _validate_logical_turn_request(
    turn: AdviceLogicalTurn,
    request: AdviceRequest,
) -> None:
    if (
        turn.user_id,
        turn.relationship_id,
        turn.conversation_id,
        turn.query,
    ) != (
        request.user_id,
        request.relationship_id,
        request.conversation_id,
        request.query,
    ):
        raise ValueError("建议轮次与当前用户、关系、会话或内容不匹配。")


def _logical_turn_matches_request_scope(
    turn: AdviceLogicalTurn,
    request: AdviceRequest,
) -> bool:
    return (
        turn.user_id == request.user_id
        and turn.relationship_id == request.relationship_id
        and turn.conversation_id == request.conversation_id
    )


def _generation_error_name(
    attempts: list[AdviceGenerationAttempt],
    error: BaseException | None = None,
) -> str:
    if attempts and attempts[-1].parse_error_type is not None:
        return attempts[-1].parse_error_type.value
    return type(error).__name__ if error is not None else "unknown_generation_error"


def _emit_validated_response(
    response: AdviceResponse,
    callback: AdviceStreamCallback | None,
) -> None:
    if callback is None:
        return
    events = [
        AdviceStreamEvent(field="problem_summary", text=response.problem_summary),
        AdviceStreamEvent(field="assessment", text=response.assessment),
    ]
    for field in (
        "clarifying_questions",
        "recommended_actions",
        "sample_phrases",
        "alternatives",
        "avoid_actions",
        "risk_notes",
    ):
        events.extend(
            AdviceStreamEvent(field=field, text=value, index=index)
            for index, value in enumerate(getattr(response, field))
        )
    for event in events:
        try:
            callback(event)
        except Exception:
            continue


_CURRENT_TURN_STATE_SIGNALS = frozenset(
    {
        "relationship_state",
        "relationship_transition",
        "durable_behavioral_reversal",
        "contextual_correction",
        "contextual_restoration",
        "contextual_recurrence",
    }
)


def _requires_current_turn_state_sync(query: str) -> bool:
    if has_retrospective_event_semantics(query):
        return True
    if may_contain_contextual_memory_update(query):
        return True
    decision = MemoryGate().evaluate(query)
    return bool(_CURRENT_TURN_STATE_SIGNALS.intersection(decision.signals))

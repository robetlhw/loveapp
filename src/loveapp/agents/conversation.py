import asyncio
import json
from datetime import timedelta
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from loveapp.adapters.conversation_states import InMemoryConversationFlowStateStore
from loveapp.adapters.date_tasks import InMemoryDatePlanningTaskStore
from loveapp.agents.advice import AdviceAgent
from loveapp.agents.date_planner import DatePlanningAgent
from loveapp.agents.date_workflow import DatePlanningWorkflow
from loveapp.application import MemoryService
from loveapp.application.conversation_flow import (
    advance_conversation_flow,
    clarification_message,
    is_pending_continuation,
    out_of_scope_message,
    pending_cancel_message,
    pending_follow_up_prompt,
)
from loveapp.application.date_planning.clause_parsing import split_date_clauses
from loveapp.application.date_planning.plan_diff import diff_date_plans
from loveapp.application.routing import extract_date_plan_slots
from loveapp.application.runtime_context import RuntimeContextBuilder
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.advice import AdviceRequest, AdviceTurnResult
from loveapp.domain.conversation import (
    ConversationFlowState,
    ConversationRequest,
    ConversationTurnResult,
)
from loveapp.domain.date_operations import DateRequirementMatch, RequirementStatus
from loveapp.domain.date_plan import DatePlan, DatePlanRequest
from loveapp.domain.date_task import DatePlanningTaskState, DateTaskDiff
from loveapp.domain.date_workflow import DatePlanningWorkflowInput
from loveapp.domain.enums import (
    AdviceScenario,
    BudgetScope,
    DatePlanMode,
    DatePlanMutation,
    DatePlanningStatus,
    DateTaskIntent,
    PlaceCategory,
    RiskLevel,
    TaskType,
    TransportMode,
)
from loveapp.domain.memory import MessageRole, RememberResult, StoredMessage, utc_now
from loveapp.domain.routing import DatePlanSlots, RouteInput, RouteResult
from loveapp.domain.runtime_context import RuntimeContext
from loveapp.ports.advice import AdviceStreamCallback
from loveapp.ports.conversation_states import ConversationFlowStateStore
from loveapp.ports.date_tasks import DatePlanningTaskStore
from loveapp.ports.routing import Router


class ConversationState(TypedDict, total=False):
    request: ConversationRequest
    recent_messages: list[StoredMessage]
    route: RouteResult
    date_task_state: DatePlanningTaskState | None
    runtime_context: RuntimeContext
    flow_state: ConversationFlowState
    follow_up_prompt: str | None
    message: str
    advice_turn: AdviceTurnResult
    date_plan: DatePlan
    current_message: StoredMessage
    memory_task: asyncio.Task[RememberResult]
    trace: ExecutionTrace
    stream_callback: AdviceStreamCallback | None


class ConversationAgent:
    def __init__(
        self,
        router: Router,
        advice_agent: AdviceAgent,
        date_planning_agent: DatePlanningAgent,
        memory_service: MemoryService,
        date_task_store: DatePlanningTaskStore | None = None,
        conversation_flow_state_store: ConversationFlowStateStore | None = None,
        date_planning_workflow: DatePlanningWorkflow | None = None,
    ) -> None:
        self._router = router
        self._advice_agent = advice_agent
        self._date_planning_agent = date_planning_agent
        self._memory_service = memory_service
        self._date_task_store = date_task_store or InMemoryDatePlanningTaskStore()
        self._runtime_context_builder = RuntimeContextBuilder(self._date_task_store)
        self._date_planning_workflow = date_planning_workflow or DatePlanningWorkflow(
            date_planning_agent,
            self._date_task_store,
        )
        self._conversation_flow_state_store = (
            conversation_flow_state_store or InMemoryConversationFlowStateStore()
        )
        self._graph = self._build_graph()

    async def chat(
        self,
        request: ConversationRequest,
        *,
        trace: ExecutionTrace | None = None,
        stream_callback: AdviceStreamCallback | None = None,
    ) -> ConversationTurnResult:
        if request.conversation_id is None:
            request = request.model_copy(update={"conversation_id": str(uuid4())})
        trace = trace or ExecutionTrace()
        with trace.measure("total"):
            state = await self._graph.ainvoke(
                {
                    "request": request,
                    "trace": trace,
                    "stream_callback": stream_callback,
                }
            )
        route = state["route"]
        advice_turn = state.get("advice_turn")
        memory_result = (
            advice_turn.memory_result
            if advice_turn is not None
            else _memory_result_from_state(state)
        )
        return ConversationTurnResult(
            conversation_id=request.conversation_id,
            route=route,
            active_task=_next_active_task(
                request.active_task,
                route,
                state.get("date_task_state"),
                state.get("flow_state"),
            ),
            pending_task=state.get("flow_state").pending_task if state.get("flow_state") else None,
            pending_task_reason=(
                state.get("flow_state").pending_task_reason if state.get("flow_state") else None
            ),
            follow_up_prompt=state.get("follow_up_prompt"),
            message=state.get("message"),
            advice=advice_turn.response if advice_turn else None,
            date_plan=state.get("date_plan"),
            date_task_state=state.get("date_task_state"),
            memory_result=memory_result,
            timings=trace.snapshot(),
        )

    def _build_graph(self):
        graph = StateGraph(ConversationState)
        graph.add_node("load_history", self._load_history)
        graph.add_node("route", self._route)
        graph.add_node("high_risk_response", self._relationship_advice)
        graph.add_node("sensitive_risk_response", self._relationship_advice)
        graph.add_node("relationship_advice", self._relationship_advice)
        graph.add_node("date_planning", self._date_planning)
        graph.add_node("clarify_intent", self._clarify_intent)
        graph.add_node("out_of_scope", self._out_of_scope)
        graph.add_node("casual_chat", self._casual_chat)
        graph.add_node("finalize_flow", self._finalize_flow)
        graph.add_edge(START, "load_history")
        graph.add_edge("load_history", "route")
        graph.add_conditional_edges(
            "route",
            _route_branch,
            {
                "high_risk_response": "high_risk_response",
                "sensitive_risk_response": "sensitive_risk_response",
                "clarify_intent": "clarify_intent",
                "relationship_advice": "relationship_advice",
                "date_planning": "date_planning",
                "out_of_scope": "out_of_scope",
                "casual_chat": "casual_chat",
            },
        )
        graph.add_edge("high_risk_response", "finalize_flow")
        graph.add_edge("sensitive_risk_response", "finalize_flow")
        graph.add_edge("relationship_advice", "finalize_flow")
        graph.add_edge("date_planning", "finalize_flow")
        graph.add_edge("clarify_intent", "finalize_flow")
        graph.add_edge("out_of_scope", "finalize_flow")
        graph.add_edge("casual_chat", "finalize_flow")
        graph.add_edge("finalize_flow", END)
        return graph.compile()

    async def _load_history(self, state: ConversationState) -> dict:
        with state["trace"].measure("history_load"):
            request = state["request"]
            with state["trace"].measure("memory_sidecar_sync") as details:
                details["pending_after_wait"] = await self._memory_service.wait_for_scope(
                    user_id=request.user_id,
                    relationship_id=request.relationship_id,
                )
            await self._memory_service.ensure_context(
                request.user_id,
                request.relationship_id,
                request.relationship_stage,
            )
            history = await self._memory_service.get_conversation_history(
                request.user_id,
                request.relationship_id,
                request.conversation_id,
            )
            date_task_state = await self._date_task_store.get(
                user_id=request.user_id,
                relationship_id=request.relationship_id,
                conversation_id=request.conversation_id,
            )
            flow_state = await self._conversation_flow_state_store.get(
                user_id=request.user_id,
                relationship_id=request.relationship_id,
                conversation_id=request.conversation_id,
            )
            if flow_state is None:
                flow_state = _new_conversation_flow_state(request)
            if date_task_state is None and _should_recover_date_task(request, history):
                current_slots = extract_date_plan_slots(RouteInput(latest_query=request.query))
                current_has_slot = any(
                    value is not None and value != []
                    for value in (
                        current_slots.city,
                        current_slots.area,
                        current_slots.plan_mode,
                        current_slots.date,
                        current_slots.end_date,
                        current_slots.day_count,
                        current_slots.nights,
                        current_slots.target_day,
                        current_slots.start_time,
                        current_slots.budget,
                        current_slots.budget_scope,
                        current_slots.preferences,
                        current_slots.dining_keywords,
                        current_slots.meal_keywords,
                        current_slots.activity_keywords,
                        current_slots.schedule_hints,
                        current_slots.excluded_keywords,
                        current_slots.transport_mode,
                        current_slots.lodging_notes,
                    )
                )
                recovery_query = (
                    request.query
                    if request.active_task == TaskType.DATE_PLANNING
                    or (current_has_slot and len(request.query) <= 30)
                    else "恢复约会任务"
                )
                recovered_slots = extract_date_plan_slots(
                    RouteInput(latest_query=recovery_query, recent_messages=history)
                )
                now = utc_now()
                asked_fields = (
                    ["city"]
                    if any(
                        marker in message.content
                        for message in history
                        if message.role == MessageRole.ASSISTANT
                        for marker in ("在哪座城市", "告诉我城市", "约会城市")
                    )
                    else []
                )
                date_task_state = DatePlanningTaskState(
                    user_id=request.user_id,
                    relationship_id=request.relationship_id,
                    conversation_id=request.conversation_id,
                    city=recovered_slots.city,
                    area=recovered_slots.area,
                    plan_mode=recovered_slots.plan_mode or DatePlanMode.SINGLE_DAY,
                    date=recovered_slots.date,
                    end_date=recovered_slots.end_date,
                    day_count=recovered_slots.day_count,
                    nights=recovered_slots.nights,
                    target_day=recovered_slots.target_day,
                    start_time=recovered_slots.start_time,
                    budget=recovered_slots.budget,
                    budget_scope=(recovered_slots.budget_scope or BudgetScope.TOTAL),
                    preferences=recovered_slots.preferences,
                    dining_keywords=recovered_slots.dining_keywords,
                    meal_keywords=recovered_slots.meal_keywords,
                    activity_keywords=recovered_slots.activity_keywords,
                    schedule_hints=recovered_slots.schedule_hints,
                    excluded_keywords=recovered_slots.excluded_keywords,
                    transport_mode=recovered_slots.transport_mode,
                    notes=recovered_slots.notes,
                    constraints=recovered_slots.constraints,
                    lodging_notes=recovered_slots.lodging_notes,
                    missing_fields=_missing_task_fields_from_slots(recovered_slots),
                    asked_fields=asked_fields,
                    clarification_round=int(bool(asked_fields)),
                    created_at=now,
                    updated_at=now,
                )
                date_task_state = await self._save_date_task_state(
                    date_task_state,
                    state["trace"],
                )
            runtime_context = await self._runtime_context_builder.build(
                request,
                active_task=request.active_task or flow_state.active_task,
                date_task_state=date_task_state,
                trace=state["trace"],
            )
            return {
                "recent_messages": history,
                "date_task_state": date_task_state,
                "flow_state": flow_state,
                "runtime_context": runtime_context,
            }

    async def _route(self, state: ConversationState) -> dict:
        with state["trace"].measure("routing") as details:
            request = state["request"]
            flow_state = state["flow_state"]
            active_task = request.active_task or flow_state.active_task
            forced_task = (
                flow_state.pending_task
                if is_pending_continuation(request.query, flow_state.pending_task)
                else None
            )
            route = await self._router.route(
                RouteInput(
                    latest_query=request.query,
                    recent_messages=state.get("recent_messages", []),
                    active_task=active_task,
                    forced_task=forced_task,
                    date_task_state=state.get("date_task_state"),
                    runtime_context=state.get("runtime_context"),
                    pending_task=flow_state.pending_task,
                    pending_task_reason=flow_state.pending_task_reason,
                    pending_task_turns_remaining=flow_state.pending_task_turns_remaining,
                    last_clarification_reason=flow_state.last_clarification_reason,
                    clarification_attempt_count=flow_state.clarification_attempt_count,
                    previous_risk_state=flow_state.recent_risk_state,
                )
            )
            details.update(
                {
                    "final_task": route.task_type.value,
                    "rule_task": route.rule_task_type.value if route.rule_task_type else None,
                    "llm_task": route.llm_task_type.value if route.llm_task_type else None,
                    "task_guard_applied": route.task_guard_applied,
                    "clarification_triggered": route.clarification_triggered,
                    "clarification_exhausted": route.clarification_exhausted,
                    "clarification_reason": route.clarification_reason,
                    "out_of_scope_reason": route.out_of_scope_reason,
                    "pending_task": route.pending_task.value if route.pending_task else None,
                    "pending_task_source": route.pending_task_source,
                    "pending_task_turns_remaining": route.pending_task_turns_remaining,
                    "recent_risk_inherited": route.recent_risk_inherited,
                    "recent_risk_deescalated": route.recent_risk_deescalated,
                    "router_prompt_version": route.router_prompt_version,
                    "router_model": route.router_model,
                    "router_input_tokens": route.router_input_tokens,
                    "router_output_tokens": route.router_output_tokens,
                    "router_duration_ms": route.router_duration_ms,
                    "router_llm_used": route.router_llm_used,
                    "fallback_reason": route.fallback_reason,
                    "date_patch_fields": (
                        ",".join(route.date_patch.source_by_field)
                        if route.date_patch is not None
                        else ""
                    ),
                }
            )
            with state["trace"].measure("date_fact_parse") as fact_details:
                fact_details.update(
                    {
                        "patch_json": json.dumps(
                            (
                                route.date_patch.model_dump(mode="json")
                                if route.date_patch is not None
                                else {}
                            ),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "source": "deterministic_rule",
                    }
                )
            with state["trace"].measure("date_clause_parse") as clause_details:
                clause_details["clauses"] = len(split_date_clauses(request.query))
            with state["trace"].measure("date_semantic_parse") as semantic_details:
                semantic_details.update(
                    {
                        "candidate_count": route.date_operation_candidate_count,
                        "dedupe_input_count": route.date_operation_dedupe_input_count,
                        "dedupe_output_count": len(route.date_operations),
                        "required": route.date_semantic_parse_required,
                        "reason": route.date_semantic_parse_reason,
                        "trigger_reasons": ",".join(
                            route.date_semantic_trigger_reasons
                        ),
                        "date_semantic_llm_used": route.date_semantic_llm_used,
                        "model": route.date_semantic_model,
                        "thinking": route.date_semantic_thinking,
                        "prompt_version": route.date_semantic_prompt_version,
                        "input_tokens": route.date_semantic_input_tokens,
                        "output_tokens": route.date_semantic_output_tokens,
                        "duration_ms": route.date_semantic_duration_ms,
                        "fallback_reason": route.date_semantic_fallback_reason,
                        "unresolved_references_json": json.dumps(
                            route.date_unresolved_references,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "error": route.date_semantic_error,
                    }
                )
            with state["trace"].measure("date_operation_verify") as verify_details:
                verify_details.update(
                    {
                        "accepted_count": len(route.date_operations),
                        "rejected_count": len(route.date_operation_rejections),
                        "rejections_json": json.dumps(
                            route.date_operation_rejections,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                )
            with state["trace"].measure("date_operation_resolution") as operation_details:
                operation_details["operations_json"] = json.dumps(
                    [operation.model_dump(mode="json") for operation in route.date_operations],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            with state["trace"].measure("route_slot_validation") as slot_details:
                slot_details.update(
                    {
                        "accepted_fields_json": json.dumps(
                            route.slot_accepted_fields,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "rejected_fields_json": json.dumps(
                            route.slot_rejected_fields,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "field_sources_json": json.dumps(
                            route.slot_field_sources,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                )
            task_state = state.get("date_task_state")
            if task_state is not None and (
                (
                    route.date_intent
                    in {
                        DateTaskIntent.SWITCH,
                        DateTaskIntent.CANCEL,
                    }
                    and route.task_type != TaskType.DATE_PLANNING
                )
                or route.task_type == TaskType.OUT_OF_SCOPE
                or route.risk_level in {RiskLevel.HIGH, RiskLevel.SENSITIVE}
            ):
                task_state = await self._save_date_task_state(
                    task_state.model_copy(
                        update={
                            "status": DatePlanningStatus.PAUSED,
                            "updated_at": utc_now(),
                        }
                    ),
                    state["trace"],
                )
            return {"route": route, "date_task_state": task_state}

    async def _relationship_advice(self, state: ConversationState) -> dict:
        request = state["request"]
        route = state["route"]
        advice_turn = await self._advice_agent.advise_turn(
            AdviceRequest(
                user_id=request.user_id,
                relationship_id=request.relationship_id,
                conversation_id=request.conversation_id,
                query=request.query,
                relationship_stage=request.relationship_stage,
                goal=route.primary_goal,
                secondary_goals=route.secondary_goals,
                scenario=route.primary_scenario or AdviceScenario.RELATIONSHIP_MAINTENANCE,
                secondary_scenarios=route.secondary_scenarios,
                forced_risk_level=(
                    route.risk_level
                    if route.risk_level in {RiskLevel.HIGH, RiskLevel.SENSITIVE}
                    else None
                ),
                forced_risk_reasons=route.risk_reasons,
            ),
            trace=state["trace"],
            stream_callback=state.get("stream_callback"),
            wait_for_memory=False,
        )
        return {"advice_turn": advice_turn}

    async def _date_planning(self, state: ConversationState) -> dict:
        request = state["request"]
        recorded = await self._record_user_message(state)
        result = await self._date_planning_workflow.run(
            DatePlanningWorkflowInput(
                request=request,
                route=state["route"],
                current_task_state=state.get("date_task_state"),
                runtime_context=state.get("runtime_context"),
            ),
            trace=state["trace"],
        )
        response_message = result.message
        if result.plan is not None:
            response_message = _compose_date_response(
                current=state.get("date_task_state") or _new_date_task_state(request),
                route=state["route"],
                plan=result.plan,
                changed=result.plan_changed,
                task_diff=result.task_diff,
                satisfaction=result.requirement_satisfaction,
                workflow_message=result.message,
                plan_committed=result.plan_committed,
            )
        history_message = (
            _date_history_message(response_message, result.plan)
            if result.plan is not None
            else response_message
        )
        await self._record_assistant_message(request, history_message, state["trace"])
        payload = {
            **recorded,
            "message": response_message,
            "date_task_state": result.task_state,
        }
        if result.plan is not None:
            payload["date_plan"] = result.plan
        return payload

    async def _legacy_date_planning(self, state: ConversationState) -> dict:
        request = state["request"]
        route = state["route"]
        recorded = await self._record_user_message(state)

        current = state.get("date_task_state") or _new_date_task_state(request)
        if route.date_intent == DateTaskIntent.CANCEL:
            paused = current.model_copy(
                update={
                    "status": DatePlanningStatus.PAUSED,
                    "updated_at": utc_now(),
                    "missing_fields": _missing_task_fields(current),
                }
            )
            saved = await self._save_date_task_state(paused, state["trace"])
            message = "好的，这次约会规划已暂停。之后补充城市或其他条件时可以继续。"
            await self._record_assistant_message(request, message, state["trace"])
            return {**recorded, "message": message, "date_task_state": saved}

        merged = _merge_date_task_state(
            current,
            route.date_plan,
            route.date_mutation,
        )
        merged = merged.model_copy(
            update={
                "status": DatePlanningStatus.COLLECTING,
                "missing_fields": _missing_task_fields(merged),
                "updated_at": utc_now(),
            }
        )
        if _should_clarify_date_task(current, merged):
            asked_fields = list(dict.fromkeys([*current.asked_fields, *merged.missing_fields]))
            clarification_round = current.clarification_round + 1
            clarified = merged.model_copy(
                update={
                    "asked_fields": asked_fields,
                    "clarification_round": clarification_round,
                    "updated_at": utc_now(),
                }
            )
            saved = await self._save_date_task_state(clarified, state["trace"])
            message = _clarification_message(clarified)
            await self._record_assistant_message(request, message, state["trace"])
            return {**recorded, "message": message, "date_task_state": saved}

        plan_mutation = route.date_mutation
        if current.current_plan is not None and current.day_count != merged.day_count:
            plan_mutation = DatePlanMutation.REPLAN
        effective_budget_scope = (
            BudgetScope.PER_DAY
            if merged.budget is None and (merged.day_count or 1) > 1
            else merged.budget_scope
        )

        plan = await self._date_planning_agent.plan(
            DatePlanRequest(
                user_id=request.user_id,
                relationship_id=request.relationship_id,
                city=merged.city,
                area=merged.area,
                plan_mode=merged.plan_mode,
                date=merged.date,
                end_date=merged.end_date,
                day_count=(
                    merged.day_count or (2 if merged.plan_mode == DatePlanMode.MULTI_DAY else 1)
                ),
                nights=(
                    merged.nights
                    if merged.nights is not None
                    else (1 if merged.plan_mode == DatePlanMode.MULTI_DAY else 0)
                ),
                target_day=merged.target_day,
                start_time=merged.start_time,
                budget=merged.budget or 500,
                budget_scope=effective_budget_scope,
                budget_is_assumed=merged.budget is None,
                preferences=merged.preferences,
                dining_keywords=merged.dining_keywords,
                meal_keywords=merged.meal_keywords,
                activity_keywords=merged.activity_keywords,
                schedule_hints=merged.schedule_hints,
                replace_place_names=route.date_plan.replace_place_names,
                excluded_keywords=merged.excluded_keywords,
                transport_mode=merged.transport_mode or TransportMode.TRANSIT,
                notes=merged.notes,
                constraints=merged.constraints,
                lodging_notes=merged.lodging_notes,
                weather=merged.weather,
                weather_forecasts=merged.weather_forecasts,
                relationship_stage=request.relationship_stage,
            ),
            trace=state["trace"],
            existing_plan=current.current_plan,
            mutation=plan_mutation,
            focus_activity_keywords=(
                route.date_plan.activity_keywords
                if route.date_mutation
                in {
                    DatePlanMutation.ADD,
                    DatePlanMutation.REPLACE,
                }
                else None
            ),
            focus_dining_keywords=(
                route.date_plan.dining_keywords
                if route.date_mutation
                in {
                    DatePlanMutation.ADD,
                    DatePlanMutation.REPLACE,
                }
                else None
            ),
        )
        persisted_plan = plan if plan.items else current.current_plan
        plan_changed = _date_plan_changed(current.current_plan, persisted_plan)
        plan_version = current.plan_version + int(plan_changed)
        response_message = _compose_date_response(
            current=current,
            route=route,
            plan=plan,
            changed=plan_changed,
        )
        planned = merged.model_copy(
            update={
                "status": DatePlanningStatus.PLANNED,
                "plan_mode": plan.plan_mode,
                "end_date": plan.end_date,
                "day_count": plan.day_count,
                "nights": plan.nights,
                "fallback_used": merged.city is None or merged.budget is None,
                "budget_scope": effective_budget_scope,
                "missing_fields": _missing_task_fields(merged),
                "weather": plan.weather,
                "weather_forecasts": [day.weather for day in plan.days if day.weather is not None],
                "current_plan": persisted_plan,
                "plan_version": plan_version,
                "last_mutation": plan_mutation,
                "updated_at": utc_now(),
            }
        )
        saved = await self._save_date_task_state(planned, state["trace"])
        await self._record_assistant_message(
            request,
            _date_history_message(response_message, plan),
            state["trace"],
        )
        return {
            **recorded,
            "message": response_message,
            "date_plan": plan,
            "date_task_state": saved,
        }

    async def _casual_chat(self, state: ConversationState) -> dict:
        request = state["request"]
        recorded = await self._record_user_message(state)
        with state["trace"].measure("casual_response"):
            route = state["route"]
            pending_task = state["flow_state"].pending_task
            message = (
                pending_cancel_message(pending_task)
                if route.pending_task_cancelled and pending_task is not None
                else _casual_reply(request.query)
            )
        await self._record_assistant_message(request, message, state["trace"])
        return {**recorded, "message": message}

    async def _clarify_intent(self, state: ConversationState) -> dict:
        request = state["request"]
        with state["trace"].measure("clarify_intent") as details:
            recorded = await self._record_user_message(state)
            route = state["route"]
            repeated = route.clarification_exhausted or (
                route.clarification_reason == state["flow_state"].last_clarification_reason
                and state["flow_state"].clarification_attempt_count > 0
            )
            details["clarification_reason"] = route.clarification_reason
            details["repeated"] = repeated
            message = clarification_message(route, repeated=repeated)
            await self._record_assistant_message(request, message, state["trace"])
        return {**recorded, "message": message}

    async def _out_of_scope(self, state: ConversationState) -> dict:
        request = state["request"]
        with state["trace"].measure("out_of_scope") as details:
            recorded = await self._record_user_message(state)
            details["out_of_scope_reason"] = state["route"].out_of_scope_reason
            message = out_of_scope_message()
            await self._record_assistant_message(request, message, state["trace"])
        return {**recorded, "message": message}

    async def _finalize_flow(self, state: ConversationState) -> dict:
        request = state["request"]
        flow = advance_conversation_flow(state["flow_state"], state["route"])
        saved = await self._save_conversation_flow_state(flow, state["trace"])
        follow_up_prompt = None
        if (
            state["route"].pending_task_source == "secondary_task"
            and saved.pending_task is not None
            and state["route"].task_type in {TaskType.RELATIONSHIP_ADVICE, TaskType.DATE_PLANNING}
        ):
            follow_up_prompt = pending_follow_up_prompt(saved.pending_task)
            await self._record_assistant_message(request, follow_up_prompt, state["trace"])
        return {"flow_state": saved, "follow_up_prompt": follow_up_prompt}

    async def _record_user_message(self, state: ConversationState) -> dict:
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
            active_task=(
                state["route"].task_type if state.get("route") is not None else request.active_task
            ),
        )
        return {"current_message": message, "memory_task": memory_task}

    async def _record_assistant_message(
        self,
        request: ConversationRequest,
        content: str,
        trace: ExecutionTrace,
    ) -> None:
        with trace.measure("assistant_message_persistence"):
            await self._memory_service.record_message(
                user_id=request.user_id,
                relationship_id=request.relationship_id,
                conversation_id=request.conversation_id,
                role=MessageRole.ASSISTANT,
                content=content,
                relationship_stage=request.relationship_stage,
            )

    async def _save_date_task_state(
        self,
        state: DatePlanningTaskState,
        trace: ExecutionTrace,
    ) -> DatePlanningTaskState:
        with trace.measure("date_task_state_persistence"):
            return await self._date_task_store.save(state)

    async def _save_conversation_flow_state(
        self,
        flow_state: ConversationFlowState,
        trace: ExecutionTrace,
    ) -> ConversationFlowState:
        with trace.measure("conversation_flow_state_persistence"):
            return await self._conversation_flow_state_store.save(flow_state)


def _new_date_task_state(request: ConversationRequest) -> DatePlanningTaskState:
    now = utc_now()
    if request.conversation_id is None:  # guarded by ConversationAgent.chat
        raise ValueError("conversation_id is required for a date task")
    return DatePlanningTaskState(
        user_id=request.user_id,
        relationship_id=request.relationship_id,
        conversation_id=request.conversation_id,
        created_at=now,
        updated_at=now,
    )


def _new_conversation_flow_state(request: ConversationRequest) -> ConversationFlowState:
    if request.conversation_id is None:  # guarded by ConversationAgent.chat
        raise ValueError("conversation_id is required for conversation flow state")
    return ConversationFlowState(
        user_id=request.user_id,
        relationship_id=request.relationship_id,
        conversation_id=request.conversation_id,
        active_task=request.active_task,
    )


def _merge_date_task_state(
    current: DatePlanningTaskState,
    slots: DatePlanSlots,
    mutation: DatePlanMutation = DatePlanMutation.NONE,
) -> DatePlanningTaskState:
    plan_mode = slots.plan_mode or current.plan_mode
    planned_date = slots.date or current.date
    day_count = slots.day_count if slots.day_count is not None else current.day_count
    trip_window_changed = (
        (slots.date is not None and slots.date != current.date)
        or (slots.end_date is not None and slots.end_date != current.end_date)
        or (slots.day_count is not None and slots.day_count != current.day_count)
    )
    target_day = slots.target_day or (None if trip_window_changed else current.target_day)
    if target_day is not None:
        day_count = max(day_count or 1, target_day)
    end_date = slots.end_date or current.end_date
    if planned_date is not None and day_count is not None:
        end_date = planned_date + timedelta(days=day_count - 1)
    if plan_mode == DatePlanMode.SINGLE_DAY and day_count == 1:
        end_date = planned_date
    nights = slots.nights if slots.nights is not None else current.nights
    if day_count is not None:
        nights = nights if nights is not None else max(day_count - 1, 0)
        if day_count > 1:
            plan_mode = DatePlanMode.MULTI_DAY
            nights = max(nights, day_count - 1)
        else:
            plan_mode = DatePlanMode.SINGLE_DAY
            nights = 0

    date_changed = (
        (slots.city is not None and slots.city != current.city)
        or (planned_date is not None and planned_date != current.date)
        or end_date != current.end_date
        or day_count != current.day_count
        or (slots.start_time is not None and slots.start_time != current.start_time)
    )
    named_target_is_dining = _replacement_target_is_dining(
        current,
        slots.replace_place_names,
    )
    replace_dining = (
        mutation
        in {
            DatePlanMutation.REPLACE,
            DatePlanMutation.REPLAN,
        }
        and bool(slots.dining_keywords)
        and named_target_is_dining is not False
    )
    replace_activity = (
        mutation
        in {
            DatePlanMutation.REPLACE,
            DatePlanMutation.REPLAN,
        }
        and bool(slots.activity_keywords)
        and named_target_is_dining is not True
    )
    return current.model_copy(
        update={
            "city": slots.city or current.city,
            "area": slots.area or current.area,
            "plan_mode": plan_mode,
            "date": planned_date,
            "end_date": end_date,
            "day_count": day_count,
            "nights": nights,
            "target_day": target_day,
            "start_time": slots.start_time or current.start_time,
            "budget": slots.budget if slots.budget is not None else current.budget,
            "budget_scope": slots.budget_scope or current.budget_scope,
            "preferences": list(dict.fromkeys([*slots.preferences, *current.preferences])),
            "dining_keywords": (
                list(dict.fromkeys(slots.dining_keywords))
                if replace_dining
                else list(dict.fromkeys([*slots.dining_keywords, *current.dining_keywords]))
            ),
            "meal_keywords": _merge_meal_keyword_state(
                current.meal_keywords,
                slots.meal_keywords,
                replace=replace_dining,
            ),
            "activity_keywords": (
                list(dict.fromkeys(slots.activity_keywords))
                if replace_activity
                else list(dict.fromkeys([*slots.activity_keywords, *current.activity_keywords]))
            ),
            "schedule_hints": list(dict.fromkeys([*slots.schedule_hints, *current.schedule_hints]))[
                :8
            ],
            "excluded_keywords": list(
                dict.fromkeys([*slots.excluded_keywords, *current.excluded_keywords])
            ),
            "transport_mode": slots.transport_mode or current.transport_mode,
            "notes": list(dict.fromkeys([*slots.notes, *current.notes]))[:8],
            "constraints": list(dict.fromkeys([*slots.constraints, *current.constraints]))[:8],
            "lodging_notes": list(dict.fromkeys([*slots.lodging_notes, *current.lodging_notes]))[
                :8
            ],
            "weather": None if date_changed else current.weather,
            "weather_forecasts": [] if date_changed else current.weather_forecasts,
        }
    )


def _replacement_target_is_dining(
    current: DatePlanningTaskState,
    place_names: list[str],
) -> bool | None:
    if current.current_plan is None or not place_names:
        return None
    targets = ["".join(name.casefold().split()) for name in place_names]
    for item in current.current_plan.items:
        place_name = "".join(item.place.name.casefold().split())
        if any(target in place_name or place_name in target for target in targets):
            return item.place.category in {
                PlaceCategory.RESTAURANT,
                PlaceCategory.CAFE,
            }
    return None


def _merge_meal_keyword_state(
    current: dict[str, list[str]],
    incoming: dict[str, list[str]],
    *,
    replace: bool,
) -> dict[str, list[str]]:
    if replace:
        return {meal_type: list(dict.fromkeys(values)) for meal_type, values in incoming.items()}
    merged = {meal_type: list(dict.fromkeys(values)) for meal_type, values in current.items()}
    reassigned = {keyword for values in incoming.values() for keyword in values}
    if reassigned:
        merged = {
            meal_type: [keyword for keyword in values if keyword not in reassigned]
            for meal_type, values in merged.items()
        }
    for meal_type, values in incoming.items():
        merged[meal_type] = list(dict.fromkeys([*merged.get(meal_type, []), *values]))
    return {meal_type: values for meal_type, values in merged.items() if values}


def _date_plan_changed(
    previous: DatePlan | None,
    current: DatePlan | None,
) -> bool:
    if previous is None or current is None:
        return previous is not current
    return _date_plan_signature(previous) != _date_plan_signature(current)


def _date_plan_signature(plan: DatePlan) -> tuple:
    item_signature = tuple(
        (
            item.place.id,
            item.day_index,
            item.scheduled_date,
            item.order,
            item.duration_minutes,
            item.estimated_cost,
            item.meal_type,
            item.time_label,
            item.after_item,
            item.slot_keyword,
            (
                item.route_from_previous.origin_id,
                item.route_from_previous.destination_id,
                item.route_from_previous.mode.value,
                item.route_from_previous.duration_minutes,
                item.route_from_previous.distance_meters,
            )
            if item.route_from_previous is not None
            else None,
        )
        for item in plan.items
    )
    return (
        plan.plan_mode,
        plan.start_date,
        plan.end_date,
        plan.day_count,
        item_signature,
    )


def _compose_date_response(
    *,
    current: DatePlanningTaskState,
    route: RouteResult,
    plan: DatePlan,
    changed: bool,
    task_diff: DateTaskDiff | None = None,
    satisfaction: list[DateRequirementMatch] | None = None,
    workflow_message: str | None = None,
    plan_committed: bool = True,
) -> str:
    if not plan_committed:
        return workflow_message or "新的条件暂时无法提交；已保留上一版有效行程。"
    if satisfaction and any(
        match.status != RequirementStatus.FULFILLED for match in satisfaction
    ):
        return workflow_message or "新的要求暂时无法满足；已保留上一版有效行程。"
    if current.current_plan is None:
        if plan.items:
            if plan.plan_mode == DatePlanMode.MULTI_DAY:
                return (
                    f"好的，我按 {plan.day_count} 天把地点、预算和偏好拆成了逐日行程。"
                    "每天的活动、用餐、天气和路线会分别列出，跨夜后不会把路线错误地连在一起。"
                )
            return (
                "好的，我把你提供的地点、预算和偏好整理成了一版完整行程。"
                "下面按时间顺序列出活动、用餐和路程，具体营业情况出发前再确认。"
            )
        return (
            "我先根据目前已经提供的信息做了一个规划草案。"
            "缺少的条件我会明确标出来，后续补充后可以继续完善。"
        )
    if not changed:
        budget_change = task_diff.changes.get("budget") if task_diff is not None else None
        if budget_change is not None:
            return (
                f"预算已从{budget_change.before or '未设置'}元调整为{budget_change.after}元。"
                "当前行程仍满足新预算，因此地点无需调整。下面保留当前完整安排供你确认。"
            )
        if task_diff is not None and task_diff.changed:
            return (
                f"已更新约会条件：{'、'.join(task_diff.changed_fields)}。"
                "当前行程仍满足这些要求，因此地点无需调整。"
            )
        return (
            "我核对了你刚补充的条件，现有行程没有需要替换的节点，因此先保留当前版本。"
            "下面把完整安排再列一次，方便你确认顺序和费用。"
        )
    plan_diff = diff_date_plans(current.current_plan, plan)
    change_kind_count = sum(
        bool(place_ids)
        for place_ids in (
            plan_diff.added_place_ids,
            plan_diff.removed_place_ids,
            plan_diff.moved_place_ids,
        )
    )
    if change_kind_count > 1:
        before_names = {item.place.id: item.place.name for item in current.current_plan.items}
        after_names = {item.place.id: item.place.name for item in plan.items}
        added = [
            _date_item_label(item, include_day=plan.plan_mode == DatePlanMode.MULTI_DAY)
            for item in plan.items
            if item.place.id in plan_diff.added_place_ids
        ]
        removed = [
            before_names[place_id]
            for place_id in plan_diff.removed_place_ids
            if place_id in before_names
        ]
        moved = [
            after_names[place_id]
            for place_id in plan_diff.moved_place_ids
            if place_id in after_names
        ]
        changes: list[str] = []
        if added and removed:
            changes.append(f"将{'、'.join(removed)}替换为{'、'.join(added)}")
        else:
            if added:
                changes.append(f"新增了{'、'.join(added)}")
            if removed:
                changes.append(f"移除了{'、'.join(removed)}")
        if moved:
            changes.append(f"调整了{'、'.join(moved)}的位置")
        return (
            f"新的要求已生效，行程{'；'.join(changes)}。"
            "其他未受影响的节点保持不变，下面是更新后的完整安排。"
        )
    if plan_diff.added_place_ids and plan_diff.removed_place_ids:
        before_names = {item.place.id: item.place.name for item in current.current_plan.items}
        after_names = {item.place.id: item.place.name for item in plan.items}
        removed = "、".join(
            before_names[place_id]
            for place_id in plan_diff.removed_place_ids
            if place_id in before_names
        )
        added = "、".join(
            after_names[place_id]
            for place_id in plan_diff.added_place_ids
            if place_id in after_names
        )
        return (
            f"新的约束已生效，行程已将{removed or '原节点'}替换为{added or '新节点'}。"
            "其他未受影响的节点保持不变，下面是更新后的完整安排。"
        )
    if route.date_mutation == DatePlanMutation.ADD:
        previous_ids = {item.place.id for item in current.current_plan.items}
        additions = [
            _date_item_label(item, include_day=plan.plan_mode == DatePlanMode.MULTI_DAY)
            for item in plan.items
            if item.place.id not in previous_ids
        ]
        addition_text = "、".join(additions) or "新的约会节点"
        return (
            f"明白了，我保留上一版行程，并补充了{addition_text}。"
            "我已经按午餐、活动、后续景点和晚餐的先后关系重新整理，下面是更新后的完整安排。"
        )
    if route.date_mutation == DatePlanMutation.REPLACE:
        return (
            "收到，我保留了没有受到影响的节点，并替换了你指定的部分。"
            "下面是重新核对路线和预算后的完整安排。"
        )
    if plan_diff.moved_place_ids:
        moved_names = {
            item.place.id: item.place.name
            for item in plan.items
            if item.place.id in plan_diff.moved_place_ids
        }
        return (
            f"收到，我已按新的时段要求调整{'、'.join(moved_names.values())}的位置。"
            "下面是更新后的完整安排。"
        )
    return (
        "收到，我已经把新的日期、预算或其他约束纳入核对；现有地点节点保持不变。"
        "下面是当前版本的完整安排，方便你继续补充或调整。"
    )


def _date_item_label(item, *, include_day: bool = False) -> str:
    prefix: str | None = None
    if item.meal_type:
        prefix = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}.get(
            item.meal_type,
            item.meal_type,
        )
    elif item.time_label:
        prefix = item.time_label
    label = f"[{prefix}] {item.place.name}" if prefix else item.place.name
    return f"第{item.day_index}天 {label}" if include_day else label


def _date_history_message(message: str, plan: DatePlan) -> str:
    if plan.plan_mode == DatePlanMode.MULTI_DAY:
        itinerary = "；".join(
            f"第{day_index}天："
            + "、".join(
                f"{item.order}. {_date_item_label(item)}"
                for item in plan.items
                if item.day_index == day_index
            )
            for day_index in range(1, plan.day_count + 1)
        )
    else:
        itinerary = "；".join(f"{item.order}. {_date_item_label(item)}" for item in plan.items)
    details = f"行程：{itinerary}" if itinerary else "行程：暂无可用地点"
    return (
        f"{message}\n{plan.title}\n{plan.summary}\n{details}\n"
        f"预计总费用 {plan.total_estimated_cost} 元。"
    )


def _missing_task_fields(state: DatePlanningTaskState) -> list[str]:
    missing: list[str] = []
    if not state.city:
        missing.append("city")
    if state.plan_mode == DatePlanMode.MULTI_DAY and not state.day_count:
        missing.append("trip_days")
    if state.date is None and state.start_time is None:
        missing.append("date_time")
    if state.budget is None:
        missing.append("budget")
    return missing


def _missing_task_fields_from_slots(slots: DatePlanSlots) -> list[str]:
    missing: list[str] = []
    if not slots.city:
        missing.append("city")
    if slots.plan_mode == DatePlanMode.MULTI_DAY and not slots.day_count:
        missing.append("trip_days")
    if slots.date is None and slots.start_time is None:
        missing.append("date_time")
    if slots.budget is None:
        missing.append("budget")
    return missing


def _should_recover_date_task(
    request: ConversationRequest,
    history: list[StoredMessage],
) -> bool:
    if request.active_task == TaskType.DATE_PLANNING:
        return True
    user_text = " ".join(message.content for message in history if message.role == MessageRole.USER)
    explicit_user_signal = any(
        marker in user_text
        for marker in (
            "帮我安排",
            "请帮我安排",
            "帮我规划",
            "请帮我规划",
            "约会安排",
            "约会计划",
            "约会攻略",
            "推荐餐厅",
            "生成行程",
            "制定行程",
        )
    )
    clarification_signal = any(
        _is_date_clarification_message(message.content)
        for message in history
        if message.role == MessageRole.ASSISTANT
    )
    return explicit_user_signal or clarification_signal


def _is_date_clarification_message(text: str) -> bool:
    return (
        text.startswith("你想在哪座城市安排这次约会？")
        or (text.startswith("为了安排得更准确，还缺少：") and "请先告诉我城市" in text)
        or text.startswith("地点已经确定。还可以补充")
    )


def _should_clarify_date_task(
    current: DatePlanningTaskState,
    merged: DatePlanningTaskState,
) -> bool:
    if not merged.missing_fields:
        return False
    # City blocks live map search. Ask for it once; if the user still does not
    # provide it, the next turn falls back to a generic plan instead of asking
    # the same question forever.
    if "city" in merged.missing_fields and "city" not in current.asked_fields:
        return True
    if current.clarification_round > 0:
        return False
    # With a known city, ask once for optional details so the user can improve
    # the plan, while keeping safe defaults for a later partial-plan fallback.
    optional = {"date_time", "budget"}
    return bool(optional.intersection(merged.missing_fields)) or (
        "trip_days" in merged.missing_fields and "trip_days" not in current.asked_fields
    )


def _clarification_message(state: DatePlanningTaskState) -> str:
    labels = {
        "city": "城市",
        "date_time": "日期/时间",
        "budget": "总预算",
        "trip_days": "旅行天数",
    }
    missing = [labels.get(field, field) for field in state.missing_fields]
    if missing == ["城市"] or ("城市" in missing and state.budget is not None):
        return "你想在哪座城市安排这次约会？"
    if "城市" in missing:
        return (
            f"为了安排得更准确，还缺少：{'、'.join(missing)}。"
            "请先告诉我城市；其余信息不确定也可以先按默认条件规划。"
        )
    return (
        f"地点已经确定。还可以补充{'、'.join(missing)}；"
        "如果暂时不确定，我会使用默认条件并忽略无法确认的部分。"
    )


def _route_branch(state: ConversationState) -> str:
    route = state["route"]
    if route.risk_level == RiskLevel.HIGH:
        return "high_risk_response"
    if route.risk_level == RiskLevel.SENSITIVE:
        return "sensitive_risk_response"
    if route.pending_task_cancelled:
        return "casual_chat"
    if route.task_type == TaskType.OUT_OF_SCOPE:
        return "out_of_scope"
    if route.clarification_triggered or route.clarification_exhausted:
        return "clarify_intent"
    return {
        TaskType.RELATIONSHIP_ADVICE: "relationship_advice",
        TaskType.DATE_PLANNING: "date_planning",
        TaskType.GENERAL_CHAT: "casual_chat",
    }[route.task_type]


def _memory_result_from_state(state: ConversationState) -> RememberResult | None:
    task = state.get("memory_task")
    message = state.get("current_message")
    if task is None or message is None:
        return None
    if not task.done():
        return RememberResult(message=message, pending=True)
    try:
        return task.result()
    except BaseException as exc:
        return RememberResult(message=message, extraction_error=str(exc))


def _next_active_task(
    previous: TaskType | None,
    route: RouteResult,
    date_task_state: DatePlanningTaskState | None = None,
    flow_state: ConversationFlowState | None = None,
) -> TaskType | None:
    if flow_state is not None:
        return flow_state.active_task
    if (
        route.risk_level in {RiskLevel.HIGH, RiskLevel.SENSITIVE}
        or route.task_type == TaskType.OUT_OF_SCOPE
    ):
        return None
    if route.task_type == TaskType.GENERAL_CHAT:
        if date_task_state is not None and date_task_state.is_resumable:
            return TaskType.DATE_PLANNING
        return previous
    return route.task_type


def _casual_reply(query: str) -> str:
    compact = query.strip().casefold()
    if any(value in compact for value in ("谢谢", "感谢")):
        return "不客气。"
    if any(value in compact for value in ("再见", "拜拜")):
        return "好的，再见。"
    if "晚安" in compact:
        return "晚安。"
    if any(value in compact for value in ("你好", "您好", "嗨", "哈喽", "hello", "hi")):
        return "你好，我在。"
    if "在吗" in compact:
        return "在，你可以直接说。"
    return "我在听，你可以继续说。"

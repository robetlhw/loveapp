import json
from datetime import timedelta

from loveapp.agents.date_planner import DatePlanningAgent
from loveapp.application.date_planning import (
    DateOperationExecutor,
    DatePlanPatchApplier,
    DatePlanValidator,
)
from loveapp.application.date_planning.plan_diff import DatePlanDiff, diff_date_plans
from loveapp.application.date_planning.role_completion import complete_date_plan_roles
from loveapp.application.date_planning.state_projection import (
    DateRequirementProjector,
    desired_stops_for_state,
    desired_stops_from_legacy,
    project_requirements_to_state,
)
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.date_constraints import build_date_constraints
from loveapp.domain.date_operations import DateOperationType, DatePlanOperation, DesiredDateStop
from loveapp.domain.date_patch import DatePlanPatch
from loveapp.domain.date_plan import DatePlan, DatePlanRequest
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.date_workflow import DatePlanningWorkflowInput, DatePlanningWorkflowResult
from loveapp.domain.enums import (
    BudgetScope,
    DatePlanMode,
    DatePlanMutation,
    DatePlanningStatus,
    DateTaskIntent,
    PlaceCategory,
    TransportMode,
)
from loveapp.domain.memory import utc_now
from loveapp.domain.routing import DatePlanSlots
from loveapp.ports.date_tasks import DatePlanningTaskStore

_MAX_CLARIFICATION_ROUNDS = 3


class DatePlanningWorkflow:
    """Owns DatePlan task-state transitions around deterministic planning."""

    def __init__(
        self,
        planner: DatePlanningAgent,
        task_store: DatePlanningTaskStore,
        validator: DatePlanValidator | None = None,
        patch_applier: DatePlanPatchApplier | None = None,
        operation_executor: DateOperationExecutor | None = None,
        requirement_projector: DateRequirementProjector | None = None,
    ) -> None:
        self._planner = planner
        self._task_store = task_store
        self._validator = validator or DatePlanValidator()
        self._patch_applier = patch_applier or DatePlanPatchApplier()
        self._operation_executor = operation_executor or DateOperationExecutor(planner)
        self._requirement_projector = requirement_projector or DateRequirementProjector()

    async def run(
        self,
        workflow_input: DatePlanningWorkflowInput,
        *,
        trace: ExecutionTrace | None = None,
    ) -> DatePlanningWorkflowResult:
        trace = trace or ExecutionTrace()
        request = workflow_input.request
        route = workflow_input.route
        is_new_task = workflow_input.current_task_state is None
        current = workflow_input.current_task_state or _new_date_task_state(request)
        if is_new_task and route.date_intent == DateTaskIntent.NEW_REQUEST:
            current = _merge_date_task_state(
                current,
                route.date_plan,
                DatePlanMutation.NONE,
            )

        with trace.measure("date_workflow"):
            if route.date_intent == DateTaskIntent.CANCEL:
                paused = current.model_copy(
                    update={
                        "status": DatePlanningStatus.PAUSED,
                        "updated_at": utc_now(),
                        "missing_fields": _missing_task_fields(current),
                    }
                )
                saved = await self._save(paused, trace)
                return DatePlanningWorkflowResult(
                    message="好的，这次约会规划已暂停。之后补充城市或其他条件时可以继续。",
                    task_state=saved,
                    cancelled=True,
                )

            current_turn_slots = route.date_patch or route.date_plan
            if route.date_patch is not None:
                with trace.measure("date_patch_apply") as details:
                    merged = self._patch_applier.apply(
                        current,
                        route.date_patch,
                        mutation=route.date_mutation,
                    )
                    fields = _patch_fields(route.date_patch)
                    details.update(
                        {
                            "current_turn_fields": ",".join(fields),
                            "source_by_field": json.dumps(
                                {
                                    field: source.value
                                    for field, source in route.date_patch.source_by_field.items()
                                },
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                            "old_values": _trace_patch_values(current, fields),
                            "patch_values": _trace_patch_values(route.date_patch, fields),
                            "candidate_values": _trace_patch_values(merged, fields),
                        }
                    )
            else:
                merged = _merge_date_task_state(current, route.date_plan, route.date_mutation)
            desired_before = desired_stops_for_state(current)
            desired_candidate = self._project_turn_requirements(
                desired_before,
                current,
                current_turn_slots,
                route.date_operations,
            )
            merged = project_requirements_to_state(merged, desired_candidate)
            merged = merged.model_copy(
                update={
                    "status": DatePlanningStatus.COLLECTING,
                    "missing_fields": _missing_task_fields(merged),
                    "updated_at": utc_now(),
                }
            )
            if _should_clarify_date_task(current, merged):
                _record_requirement_projection(
                    trace,
                    desired_before,
                    merged.desired_stops,
                    committed=False,
                )
                clarified = merged.model_copy(
                    update={
                        "asked_fields": list(
                            dict.fromkeys([*current.asked_fields, *merged.missing_fields])
                        ),
                        "clarification_round": current.clarification_round + 1,
                        "updated_at": utc_now(),
                    }
                )
                draft = None
                if "city" in merged.missing_fields and current.clarification_round > 0:
                    clarified = clarified.model_copy(update={"fallback_used": True})
                    draft = _fallback_date_draft(clarified)
                saved = await self._save(clarified, trace)
                return DatePlanningWorkflowResult(
                    message=_clarification_message(saved),
                    task_state=saved,
                    needs_clarification=True,
                    plan=draft,
                )
            if "city" in merged.missing_fields:
                _record_requirement_projection(
                    trace,
                    desired_before,
                    merged.desired_stops,
                    committed=False,
                )
                saved = await self._save(
                    merged.model_copy(update={"updated_at": utc_now()}),
                    trace,
                )
                return DatePlanningWorkflowResult(
                    message=_city_required_message(current.clarification_round),
                    task_state=saved,
                )

            mutation = route.date_mutation
            if (
                current.current_plan is not None
                and current.current_plan.items
                and current.day_count != merged.day_count
            ):
                mutation = DatePlanMutation.REPLAN
            budget_scope = (
                BudgetScope.PER_DAY
                if merged.budget is None and (merged.day_count or 1) > 1
                else merged.budget_scope
            )
            plan_request = _build_request(request, merged, current_turn_slots, budget_scope)
            applied_operations = None
            if route.date_operations:
                with trace.measure("date_operation_execute") as details:
                    execution = await self._operation_executor.apply(
                        current.current_plan,
                        route.date_operations,
                        plan_request,
                        trace=trace,
                        required_mutation=mutation,
                    )
                    plan = execution.plan
                    applied_operations = execution.applied
                    mutation = execution.effective_mutation
                    details.update(
                        {
                            "requested_count": len(route.date_operations),
                            "applied_count": len(execution.applied),
                            "rejected_count": len(execution.rejected),
                            "rejections_json": json.dumps(
                                [
                                    {
                                        "type": item.operation.type.value,
                                        "reason": item.reason,
                                    }
                                    for item in execution.rejected
                                ],
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        }
                    )
            else:
                plan = await self._planner.plan(
                    plan_request,
                    trace=trace,
                    existing_plan=current.current_plan,
                    mutation=mutation,
                    focus_activity_keywords=(
                        current_turn_slots.activity_keywords
                        if route.date_mutation in {DatePlanMutation.ADD, DatePlanMutation.REPLACE}
                        else None
                    ),
                    focus_dining_keywords=(
                        current_turn_slots.dining_keywords
                        if route.date_mutation in {DatePlanMutation.ADD, DatePlanMutation.REPLACE}
                        else None
                    ),
                )
            if applied_operations is not None:
                committed_desired = self._requirement_projector.apply_operations(
                    desired_before,
                    applied_operations,
                    current_plan=current.current_plan,
                )
                merged = project_requirements_to_state(merged, committed_desired)
                plan_request = _build_request(
                    request,
                    merged,
                    current_turn_slots,
                    budget_scope,
                )
            _record_requirement_projection(
                trace,
                desired_before,
                merged.desired_stops,
                committed=True,
            )
            with trace.measure("date_plan_role_completion") as details:
                completion = complete_date_plan_roles(plan, merged.desired_stops)
                details.update(
                    {
                        "inferred_dinner_items": len(completion.inferred_dinner_item_ids),
                        "inferred_ids": ",".join(completion.inferred_dinner_item_ids),
                        "unresolved_roles": len(completion.unresolved_roles),
                        "unresolved_json": json.dumps(
                            completion.unresolved_roles,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "reordered": completion.reordered,
                    }
                )
            if completion.changed:
                plan = await self._planner.rebuild_plan(
                    plan,
                    plan_request,
                    completion.plan.items,
                    summary=plan.summary,
                    trace=trace,
                )
            constraints = build_date_constraints(
                plan_request,
                desired_stops=merged.desired_stops,
            )
            with trace.measure("date_plan_validation") as details:
                validation = self._validator.validate(plan, plan_request, constraints)
                details["valid"] = validation.valid
                details["issue_codes"] = ",".join(issue.code for issue in validation.issues)
            if not validation.valid:
                return await self._invalid_plan_result(
                    current=current,
                    merged=merged,
                    candidate_plan=plan,
                    mutation=mutation,
                    validation_codes=[issue.code for issue in validation.issues],
                    trace=trace,
                )
            persisted_plan = plan if plan.items else current.current_plan
            if not _has_executable_plan(persisted_plan):
                saved = await self._save(
                    merged.model_copy(
                        update={
                            "status": DatePlanningStatus.COLLECTING,
                            "missing_fields": _missing_task_fields(merged),
                            "last_mutation": mutation,
                            "updated_at": utc_now(),
                        }
                    ),
                    trace,
                )
                return DatePlanningWorkflowResult(
                    message="当前还没有可执行的行程，补充城市或约会条件后可以继续。",
                    task_state=saved,
                    plan=plan,
                    plan_changed=False,
                )
            changed = _date_plan_changed(current.current_plan, persisted_plan)
            plan_diff = diff_date_plans(current.current_plan, persisted_plan)
            _record_plan_diff(trace, plan_diff)
            _record_plan_item_roles(trace, persisted_plan)
            planned = merged.model_copy(
                update={
                    "status": DatePlanningStatus.PLANNED,
                    "plan_mode": plan.plan_mode,
                    "end_date": plan.end_date,
                    "day_count": plan.day_count,
                    "nights": plan.nights,
                    "fallback_used": merged.city is None or merged.budget is None,
                    "budget_scope": budget_scope,
                    "missing_fields": _missing_task_fields(merged),
                    "weather": plan.weather,
                    "weather_forecasts": [day.weather for day in plan.days if day.weather],
                    "current_plan": persisted_plan,
                    "plan_version": current.plan_version + int(changed),
                    "last_mutation": mutation,
                    "updated_at": utc_now(),
                }
            )
            saved = await self._save(planned, trace)
            return DatePlanningWorkflowResult(
                message=_compose_response(
                    current=current,
                    mutation=mutation,
                    plan=plan,
                    changed=changed,
                    plan_diff=plan_diff,
                ),
                task_state=saved,
                plan=plan,
                plan_changed=changed,
            )

    def _project_turn_requirements(
        self,
        desired_before: list[DesiredDateStop],
        current: DatePlanningTaskState,
        slots: DatePlanPatch | DatePlanSlots,
        operations: list[DatePlanOperation],
    ) -> list[DesiredDateStop]:
        if operations:
            return self._requirement_projector.apply_operations(
                desired_before,
                operations,
                current_plan=current.current_plan,
            )
        incoming = desired_stops_from_legacy(
            dining_keywords=slots.dining_keywords,
            meal_keywords=slots.meal_keywords,
            activity_keywords=slots.activity_keywords,
            schedule_hints=slots.schedule_hints,
            target_day=slots.target_day,
        )
        if not incoming:
            return desired_before
        return self._requirement_projector.apply_operations(
            desired_before,
            [DatePlanOperation(type=DateOperationType.ADD_STOP, payload=stop) for stop in incoming],
            current_plan=current.current_plan,
        )

    async def _invalid_plan_result(
        self,
        *,
        current: DatePlanningTaskState,
        merged: DatePlanningTaskState,
        candidate_plan: DatePlan,
        mutation: DatePlanMutation,
        validation_codes: list[str],
        trace: ExecutionTrace,
    ) -> DatePlanningWorkflowResult:
        if _has_executable_plan(current.current_plan):
            merged = project_requirements_to_state(
                merged,
                desired_stops_for_state(current),
            )
            saved = await self._save(
                merged.model_copy(
                    update={
                        "status": DatePlanningStatus.PLANNED,
                        "current_plan": current.current_plan,
                        "plan_version": current.plan_version,
                        "last_mutation": mutation,
                        "updated_at": utc_now(),
                    }
                ),
                trace,
            )
            return DatePlanningWorkflowResult(
                message="新的条件无法满足；已保留上一版有效行程。",
                task_state=saved,
                # Return the candidate response for this turn while retaining
                # the persisted snapshot as the last known-valid plan.
                plan=candidate_plan,
                plan_changed=False,
            )
        saved = await self._save(
            merged.model_copy(
                update={
                    "status": DatePlanningStatus.COLLECTING,
                    "updated_at": utc_now(),
                }
            ),
            trace,
        )
        return DatePlanningWorkflowResult(
            message="当前条件无法生成满足硬约束的行程，请调整地点、预算或明确要求。",
            task_state=saved,
            plan_changed=False,
        )

    async def _save(
        self, state: DatePlanningTaskState, trace: ExecutionTrace
    ) -> DatePlanningTaskState:
        with trace.measure("date_task_persistence"):
            return await self._task_store.save(state)


def _new_date_task_state(request) -> DatePlanningTaskState:
    if request.conversation_id is None:
        raise ValueError("conversation_id is required for date planning")
    now = utc_now()
    return DatePlanningTaskState(
        user_id=request.user_id,
        relationship_id=request.relationship_id,
        conversation_id=request.conversation_id,
        created_at=now,
        updated_at=now,
    )


def _build_request(request, state, slots, budget_scope: BudgetScope) -> DatePlanRequest:
    return DatePlanRequest(
        user_id=request.user_id,
        relationship_id=request.relationship_id,
        city=state.city,
        area=state.area,
        plan_mode=state.plan_mode,
        date=state.date,
        end_date=state.end_date,
        day_count=state.day_count or (2 if state.plan_mode == DatePlanMode.MULTI_DAY else 1),
        nights=state.nights
        if state.nights is not None
        else (1 if state.plan_mode == DatePlanMode.MULTI_DAY else 0),
        target_day=state.target_day,
        start_time=state.start_time,
        budget=state.budget or 500,
        budget_scope=budget_scope,
        budget_is_assumed=state.budget is None,
        preferences=state.preferences,
        dining_keywords=state.dining_keywords,
        meal_keywords=state.meal_keywords,
        activity_keywords=state.activity_keywords,
        schedule_hints=state.schedule_hints,
        replace_place_names=slots.replace_place_names,
        excluded_keywords=state.excluded_keywords,
        transport_mode=state.transport_mode or TransportMode.TRANSIT,
        notes=state.notes,
        constraints=state.constraints,
        lodging_notes=state.lodging_notes,
        weather=state.weather,
        weather_forecasts=state.weather_forecasts,
        relationship_stage=request.relationship_stage,
    )


def _merge_date_task_state(
    current: DatePlanningTaskState, slots: DatePlanSlots, mutation: DatePlanMutation
) -> DatePlanningTaskState:
    plan_mode = slots.plan_mode or current.plan_mode
    planned_date = slots.date or current.date
    day_count = slots.day_count if slots.day_count is not None else current.day_count
    trip_window_changed = any(
        value
        for value in (
            slots.date is not None and slots.date != current.date,
            slots.end_date is not None and slots.end_date != current.end_date,
            slots.day_count is not None and slots.day_count != current.day_count,
        )
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
            plan_mode, nights = DatePlanMode.MULTI_DAY, max(nights, day_count - 1)
        else:
            plan_mode, nights = DatePlanMode.SINGLE_DAY, 0
    date_changed = (
        (slots.city is not None and slots.city != current.city)
        or (planned_date is not None and planned_date != current.date)
        or end_date != current.end_date
        or day_count != current.day_count
        or (slots.start_time is not None and slots.start_time != current.start_time)
    )
    target_is_dining = _replacement_target_is_dining(current, slots.replace_place_names)
    replace_dining = (
        mutation in {DatePlanMutation.REPLACE, DatePlanMutation.REPLAN}
        and bool(slots.dining_keywords)
        and target_is_dining is not False
    )
    replace_activity = (
        mutation in {DatePlanMutation.REPLACE, DatePlanMutation.REPLAN}
        and bool(slots.activity_keywords)
        and target_is_dining is not True
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
            "dining_keywords": list(dict.fromkeys(slots.dining_keywords))
            if replace_dining
            else list(dict.fromkeys([*slots.dining_keywords, *current.dining_keywords])),
            "meal_keywords": _merge_meal_keyword_state(
                current.meal_keywords, slots.meal_keywords, replace=replace_dining
            ),
            "activity_keywords": list(dict.fromkeys(slots.activity_keywords))
            if replace_activity
            else list(dict.fromkeys([*slots.activity_keywords, *current.activity_keywords])),
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


def _replacement_target_is_dining(current: DatePlanningTaskState, names: list[str]) -> bool | None:
    if current.current_plan is None or not names:
        return None
    targets = ["".join(name.casefold().split()) for name in names]
    for item in current.current_plan.items:
        name = "".join(item.place.name.casefold().split())
        if any(target in name or name in target for target in targets):
            return item.place.category in {PlaceCategory.RESTAURANT, PlaceCategory.CAFE}
    return None


def _merge_meal_keyword_state(
    current: dict[str, list[str]], incoming: dict[str, list[str]], *, replace: bool
) -> dict[str, list[str]]:
    if replace:
        return {kind: list(dict.fromkeys(values)) for kind, values in incoming.items()}
    merged = {kind: list(dict.fromkeys(values)) for kind, values in current.items()}
    reassigned = {keyword for values in incoming.values() for keyword in values}
    if reassigned:
        merged = {
            kind: [value for value in values if value not in reassigned]
            for kind, values in merged.items()
        }
    for kind, values in incoming.items():
        merged[kind] = list(dict.fromkeys([*merged.get(kind, []), *values]))
    return {kind: values for kind, values in merged.items() if values}


def _missing_task_fields(state: DatePlanningTaskState) -> list[str]:
    missing = []
    if not state.city:
        missing.append("city")
    if state.plan_mode == DatePlanMode.MULTI_DAY and not state.day_count:
        missing.append("trip_days")
    if state.date is None and state.start_time is None:
        missing.append("date_time")
    if state.budget is None:
        missing.append("budget")
    return missing


def _should_clarify_date_task(
    current: DatePlanningTaskState, merged: DatePlanningTaskState
) -> bool:
    if not merged.missing_fields:
        return False
    if current.clarification_round >= _MAX_CLARIFICATION_ROUNDS:
        return False
    if "city" in merged.missing_fields:
        return True
    if current.clarification_round > 0:
        return False
    return bool({"date_time", "budget"}.intersection(merged.missing_fields)) or (
        "trip_days" in merged.missing_fields and "trip_days" not in current.asked_fields
    )


def _city_required_message(clarification_round: int) -> str:
    if clarification_round >= _MAX_CLARIFICATION_ROUNDS:
        return "还需要明确城市才能生成可执行行程；补充城市后我会继续保留已收集的条件。"
    return "你想在哪座城市安排这次约会？"


def _has_executable_plan(plan: DatePlan | None) -> bool:
    return plan is not None and bool(plan.items)


def _fallback_date_draft(state: DatePlanningTaskState) -> DatePlan:
    return DatePlan(
        title="约会规划草案",
        summary="目前先给出通用的约会结构，补充城市后可以继续生成真实地点和路线。",
        total_estimated_cost=0,
        total_duration_minutes=180,
        notes=[
            "尚未提供城市，已忽略真实地点、路线和营业信息。",
            *(
                ["尚未提供日期/时间，已忽略天气和营业时间校验。"]
                if state.date is None and state.start_time is None
                else []
            ),
            *(["尚未提供预算，暂按 500 元总预算估算。"] if state.budget is None else []),
        ],
        data_source="workflow-fallback",
    )


def _patch_fields(patch: DatePlanPatch) -> list[str]:
    fields = list(patch.source_by_field)
    if fields:
        return fields
    return [
        name
        for name, value in patch.model_dump(exclude={"source_by_field"}).items()
        if value is not None and value != [] and value != {}
    ]


def _trace_patch_values(value, fields: list[str]) -> str:
    return json.dumps(
        {field: getattr(value, field, None) for field in fields},
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )


def _record_requirement_projection(
    trace: ExecutionTrace,
    before: list[DesiredDateStop],
    after: list[DesiredDateStop],
    *,
    committed: bool,
) -> None:
    with trace.measure("date_requirement_projection") as details:
        details.update(
            {
                "desired_stops_before": len(before),
                "desired_stops_after": len(after),
                "committed": committed,
                "desired_stops_json": json.dumps(
                    [stop.model_dump(mode="json") for stop in after],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        )


def _record_plan_diff(trace: ExecutionTrace, plan_diff: DatePlanDiff) -> None:
    with trace.measure("date_plan_diff") as details:
        details.update(
            {
                "added": len(plan_diff.added_place_ids),
                "removed": len(plan_diff.removed_place_ids),
                "moved": len(plan_diff.moved_place_ids),
                "unchanged": len(plan_diff.unchanged_place_ids),
                "diff_json": plan_diff.model_dump_json(),
            }
        )


def _record_plan_item_roles(trace: ExecutionTrace, plan: DatePlan) -> None:
    with trace.measure("date_plan_item_role_summary") as details:
        details.update(
            {
                "lunch_items": sum(item.meal_type == "lunch" for item in plan.items),
                "dinner_items": sum(item.meal_type == "dinner" for item in plan.items),
                "after_dinner_items": sum(
                    item.time_label in {"晚饭后", "晚餐后"} for item in plan.items
                ),
            }
        )


def _clarification_message(state: DatePlanningTaskState) -> str:
    if state.missing_fields == ["city"] or (
        "city" in state.missing_fields and state.budget is not None
    ):
        return "你想在哪座城市安排这次约会？"
    if "city" in state.missing_fields:
        return "为了安排得更准确，请先告诉我城市；其他条件不确定也可以先按默认条件规划。"
    return "地点已经确定；补充日期、时间或预算可以进一步优化行程。"


def _date_plan_changed(previous: DatePlan | None, current: DatePlan | None) -> bool:
    if previous is None or current is None:
        return previous is not current
    return _date_plan_signature(previous) != _date_plan_signature(current)


def _date_plan_signature(plan: DatePlan) -> tuple:
    return (
        plan.plan_mode,
        plan.start_date,
        plan.end_date,
        plan.day_count,
        tuple(
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
                if item.route_from_previous
                else None,
            )
            for item in plan.items
        ),
    )


def _compose_response(
    *,
    current: DatePlanningTaskState,
    mutation: DatePlanMutation,
    plan: DatePlan,
    changed: bool,
    plan_diff: DatePlanDiff,
) -> str:
    if current.current_plan is None:
        return (
            "好的，我已根据地点、预算和偏好整理出一份完整约会行程。"
            if plan.items
            else "我已先根据当前信息整理了规划草案，补充条件后可以继续完善。"
        )
    if not changed:
        return "我核对了刚补充的条件，现有行程没有需要替换的节点，因此先保留当前版本。"
    change_kind_count = sum(
        bool(place_ids)
        for place_ids in (
            plan_diff.added_place_ids,
            plan_diff.removed_place_ids,
            plan_diff.moved_place_ids,
        )
    )
    if change_kind_count > 1:
        added = _plan_place_names(plan, plan_diff.added_place_ids)
        removed = _plan_place_names(current.current_plan, plan_diff.removed_place_ids)
        moved = _plan_place_names(plan, plan_diff.moved_place_ids)
        changes: list[str] = []
        if added and removed:
            changes.append(f"将{'、'.join(removed)}替换为{'、'.join(added)}")
        else:
            if added:
                changes.append(f"新增了{'、'.join(added)}")
            if removed:
                changes.append(f"移除了{'、'.join(removed)}")
        if moved:
            changes.append(f"调整了{'、'.join(moved)}的行程位置")
        return f"新的要求已生效，行程{'；'.join(changes)}。其他未受影响的节点保持不变。"
    if plan_diff.added_place_ids and plan_diff.removed_place_ids:
        removed = _plan_place_names(current.current_plan, plan_diff.removed_place_ids)
        added = _plan_place_names(plan, plan_diff.added_place_ids)
        return (
            "新的约束已生效。行程已将"
            f"{'、'.join(removed) or '原节点'}替换为{'、'.join(added) or '新节点'}，"
            "其他未受影响的节点保持不变。"
        )
    if mutation == DatePlanMutation.ADD:
        return "明白了，我保留上一版行程，并补充了新的约会节点。"
    if mutation == DatePlanMutation.REPLACE:
        return "收到，我保留了没有受到影响的节点，并替换了你指定的部分。"
    if plan_diff.moved_place_ids:
        moved = _plan_place_names(plan, plan_diff.moved_place_ids)
        return f"收到，我已按新的时段要求调整{'、'.join(moved)}的行程位置。"
    return "收到，我已把新的日期、预算或其他约束纳入核对；现有地点节点保持不变。"


def _plan_place_names(plan: DatePlan | None, place_ids: list[str]) -> list[str]:
    if plan is None:
        return []
    names = {item.place.id: item.place.name for item in plan.items}
    return [names[place_id] for place_id in place_ids if place_id in names]

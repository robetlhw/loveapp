from pathlib import Path

import pytest

from loveapp.adapters.date_tasks import (
    InMemoryDatePlanningTaskStore,
    SQLiteDatePlanningTaskStore,
)
from loveapp.agents.conversation import _compose_date_response, _date_item_label
from loveapp.agents.date_planner import _make_date_item
from loveapp.application.date_planning.clause_parsing import split_date_clauses
from loveapp.application.date_planning.operation_resolution import (
    DateOperationResolver,
    requires_date_semantic_parse,
)
from loveapp.application.date_planning.plan_diff import diff_date_plans
from loveapp.application.date_planning.state_projection import (
    DateRequirementProjector,
    derive_legacy_slots,
    project_requirements_to_state,
)
from loveapp.application.date_planning.validation import DatePlanValidator
from loveapp.application.memory_gate import MemoryGate
from loveapp.application.routing import HybridRouter, route_by_rules
from loveapp.application.runtime_context import RuntimeContextBuilder
from loveapp.domain.conversation import ConversationRequest
from loveapp.domain.date_constraints import build_date_constraints
from loveapp.domain.date_operations import (
    DateOperationType,
    DatePlanOperation,
    DateSemanticParseResult,
    DesiredDateStop,
    MealType,
    StopKind,
    StopReference,
    TemporalAnchor,
    TimeWindow,
)
from loveapp.domain.date_patch import DatePlanPatch
from loveapp.domain.date_plan import DatePlan, DatePlanItem, DatePlanRequest, Place
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.enums import DatePlanMutation, PlaceCategory, TaskType
from loveapp.domain.routing import RouteCorrection, RouteInput, RouteResult
from loveapp.safety import SafetyPolicy


def _place(place_id: str, name: str, category: PlaceCategory, keyword: str) -> Place:
    return Place(
        id=place_id,
        name=name,
        city="上海",
        address="测试地址",
        category=category,
        tags=[keyword],
        search_keywords=[keyword],
        estimated_cost_per_person=80,
        source="test",
    )


def _item(
    place_id: str,
    name: str,
    category: PlaceCategory,
    keyword: str,
    *,
    order: int,
    meal_type: str | None = None,
    time_label: str | None = None,
) -> DatePlanItem:
    return DatePlanItem(
        order=order,
        place=_place(place_id, name, category, keyword),
        duration_minutes=90,
        estimated_cost=160,
        reason="test",
        meal_type=meal_type,
        time_label=time_label,
        slot_keyword=keyword,
    )


def _plan(*items: DatePlanItem) -> DatePlan:
    return DatePlan(
        title="测试行程",
        summary="测试行程",
        items=list(items),
        total_estimated_cost=sum(item.estimated_cost for item in items),
        total_duration_minutes=sum(item.duration_minutes for item in items),
        data_source="test",
    )


def test_constraint_target_and_exclusion_are_independent() -> None:
    budget = route_by_rules(
        RouteInput(latest_query="帮我把预算改为600", forced_task=TaskType.DATE_PLANNING)
    )
    replacement = route_by_rules(
        RouteInput(latest_query="把很久以前换成火锅", forced_task=TaskType.DATE_PLANNING)
    )
    exclusion = route_by_rules(
        RouteInput(
            latest_query="晚饭不要火锅，想去博物馆",
            forced_task=TaskType.DATE_PLANNING,
        )
    )

    assert budget.date_patch is not None
    assert budget.date_patch.budget == 600
    assert budget.date_plan.replace_place_names == []
    assert "预算" not in budget.date_plan.excluded_keywords
    assert replacement.date_plan.replace_place_names == ["很久以前"]
    assert replacement.date_plan.excluded_keywords == []
    assert replacement.date_operations[0].type == DateOperationType.REPLACE_STOP
    assert exclusion.date_plan.excluded_keywords == ["火锅"]


def test_clause_local_binding_keeps_lunch_and_after_dinner_independent() -> None:
    text = "新增午饭，午饭去吃烧烤，晚饭后看电影"
    route = route_by_rules(RouteInput(latest_query=text, forced_task=TaskType.DATE_PLANNING))
    payloads = [
        operation.payload for operation in route.date_operations if operation.payload is not None
    ]

    assert [clause.text for clause in split_date_clauses(text)] == [
        "新增午饭",
        "午饭去吃烧烤",
        "晚饭后看电影",
    ]
    barbecue = next(stop for stop in payloads if stop.keyword == "烧烤")
    movie = next(stop for stop in payloads if stop.keyword == "电影院")
    assert barbecue.meal_type == MealType.LUNCH
    assert movie.after == TemporalAnchor.DINNER
    assert barbecue.meal_type != MealType.DINNER
    assert [
        clause.text
        for clause in split_date_clauses("午饭吃烧烤然后晚饭后看电影")
    ] == ["午饭吃烧烤", "晚饭后看电影"]


def test_clause_local_binding_uses_the_nearest_meal_marker_for_each_stop() -> None:
    route = route_by_rules(
        RouteInput(
            latest_query="同时晚饭吃火锅（日料是午餐）",
            forced_task=TaskType.DATE_PLANNING,
        )
    )
    dining = [
        operation.payload
        for operation in route.date_operations
        if operation.payload is not None
        and operation.payload.kind in {StopKind.DINING, StopKind.CAFE}
    ]

    assert [(stop.keyword, stop.meal_type) for stop in dining] == [
        ("火锅", MealType.DINNER),
        ("日料", MealType.LUNCH),
    ]


def test_explicit_relative_stop_cue_becomes_a_structured_reference() -> None:
    route = route_by_rules(
        RouteInput(
            latest_query="下午看电影，看完电影想去个景点逛逛",
            forced_task=TaskType.DATE_PLANNING,
        )
    )
    attraction = next(
        operation.payload
        for operation in route.date_operations
        if operation.payload is not None and operation.payload.keyword == "景点"
    )

    assert attraction.after == StopReference(keyword="电影院")


def test_explicit_clock_can_bind_a_meal_but_plain_restaurant_does_not_guess() -> None:
    timed = DateOperationResolver().resolve(
        "18:30去吃西餐",
        None,
        DatePlanPatch(dining_keywords=["西餐"]),
    )
    timed_stop = timed.operations[0].payload
    plain_item = _make_date_item(
        _place("western", "测试西餐厅", PlaceCategory.RESTAURANT, "西餐"),
        preferences=[],
        activity_keywords=[],
        dining_keywords=["西餐"],
        meal_keywords={},
        schedule_hints=[],
        slot_keyword="西餐",
    )

    assert timed_stop is not None
    assert timed_stop.meal_type == MealType.DINNER
    assert plain_item.meal_type is None
    assert plain_item.time_label is None


def test_desired_stops_are_canonical_and_legacy_slots_are_derived() -> None:
    desired = [
        DesiredDateStop(
            kind=StopKind.DINING,
            keyword="烧烤",
            meal_type=MealType.LUNCH,
        ),
        DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword="电影院",
            time_window=TimeWindow(label="晚饭后"),
            after=TemporalAnchor.DINNER,
        ),
    ]
    state = project_requirements_to_state(
        DatePlanningTaskState(user_id="u", relationship_id="r", conversation_id="c"),
        desired,
    )
    legacy = derive_legacy_slots(desired)

    assert state.desired_stops == desired
    assert legacy.dining_keywords == ["烧烤"]
    assert legacy.meal_keywords == {"lunch": ["烧烤"]}
    assert legacy.activity_keywords == ["电影院"]
    assert legacy.schedule_hints == ["晚饭后"]
    assert state.dining_keywords == legacy.dining_keywords
    assert state.meal_keywords == legacy.meal_keywords


@pytest.mark.asyncio
async def test_desired_stops_survive_sqlite_round_trip_and_old_state_defaults(
    tmp_path: Path,
) -> None:
    desired = DesiredDateStop(
        kind=StopKind.DINING,
        keyword="烧烤",
        meal_type=MealType.LUNCH,
    )
    store = SQLiteDatePlanningTaskStore(tmp_path / "date-state.db")
    state = DatePlanningTaskState(
        user_id="u",
        relationship_id="r",
        conversation_id="c",
        desired_stops=[desired],
    )

    await store.save(state)
    loaded = await store.get(user_id="u", relationship_id="r", conversation_id="c")
    legacy = DatePlanningTaskState.model_validate(
        {"user_id": "old-u", "relationship_id": "old-r", "conversation_id": "old-c"}
    )

    assert loaded is not None
    assert loaded.desired_stops == [desired]
    assert legacy.desired_stops == []


def test_replace_projection_inherits_the_previous_meal_role() -> None:
    existing = [
        DesiredDateStop(
            kind=StopKind.DINING,
            keyword="烧烤",
            meal_type=MealType.LUNCH,
        )
    ]
    operation = DatePlanOperation(
        type=DateOperationType.REPLACE_STOP,
        target=StopReference(keyword="烧烤"),
        payload=DesiredDateStop(kind=StopKind.DINING, keyword="火锅"),
    )

    projected = DateRequirementProjector().apply_operations(existing, [operation])

    assert len(projected) == 1
    assert projected[0].keyword == "火锅"
    assert projected[0].meal_type == MealType.LUNCH


def test_repeated_stop_does_not_erase_existing_temporal_role() -> None:
    existing = [
        DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword="电影院",
            time_window=TimeWindow(label="晚饭后"),
            after=TemporalAnchor.DINNER,
        )
    ]
    repeated = DatePlanOperation(
        type=DateOperationType.ADD_STOP,
        payload=DesiredDateStop(kind=StopKind.ACTIVITY, keyword="电影院"),
    )

    projected = DateRequirementProjector().apply_operations(existing, [repeated])

    assert projected[0].time_window == TimeWindow(label="晚饭后")
    assert projected[0].after == TemporalAnchor.DINNER


def test_validator_checks_meal_role_and_temporal_order() -> None:
    barbecue = _item(
        "barbecue",
        "测试烧烤店",
        PlaceCategory.RESTAURANT,
        "烧烤",
        order=1,
        meal_type="dinner",
        time_label="晚餐",
    )
    desired = [
        DesiredDateStop(
            kind=StopKind.DINING,
            keyword="烧烤",
            meal_type=MealType.LUNCH,
        )
    ]
    request = DatePlanRequest(city="上海", budget=1000, dining_keywords=["烧烤"])
    constraints = build_date_constraints(request, desired_stops=desired)

    result = DatePlanValidator().validate(_plan(barbecue), request, constraints)

    assert "required_stop_role_mismatch" in {issue.code for issue in result.issues}


def test_presenter_and_plan_diff_use_persisted_roles_and_real_changes() -> None:
    old = _item(
        "old",
        "原餐厅",
        PlaceCategory.RESTAURANT,
        "西餐",
        order=1,
        meal_type="lunch",
        time_label="午餐",
    )
    new = _item(
        "new",
        "新餐厅",
        PlaceCategory.RESTAURANT,
        "日料",
        order=1,
        meal_type="lunch",
        time_label="午餐",
    )
    current = DatePlanningTaskState(
        user_id="u",
        relationship_id="r",
        conversation_id="c",
        current_plan=_plan(old),
        plan_version=1,
    )
    route = RouteResult(
        normalized_query="预算改为600",
        task_type=TaskType.DATE_PLANNING,
        task_confidence=1,
        date_mutation=DatePlanMutation.UPDATE_CONSTRAINT,
    )
    diff = diff_date_plans(current.current_plan, _plan(new))
    message = _compose_date_response(
        current=current,
        route=route,
        plan=_plan(new),
        changed=True,
    )

    assert diff.removed_place_ids == ["old"]
    assert diff.added_place_ids == ["new"]
    assert _date_item_label(new) == "[午餐] 新餐厅"
    assert "原餐厅" in message and "新餐厅" in message
    assert "保留原有行程节点" not in message


def test_compound_plan_diff_response_reports_added_and_moved_items() -> None:
    movie = _item(
        "movie",
        "现有电影院",
        PlaceCategory.ENTERTAINMENT,
        "电影院",
        order=1,
        time_label="下午",
    )
    dinner = _item(
        "dinner",
        "现有西餐厅",
        PlaceCategory.RESTAURANT,
        "西餐",
        order=2,
        meal_type="dinner",
        time_label="晚餐",
    )
    lunch = _item(
        "lunch",
        "测试烧烤店",
        PlaceCategory.RESTAURANT,
        "烧烤",
        order=1,
        meal_type="lunch",
        time_label="午餐",
    )
    moved_movie = movie.model_copy(update={"order": 3, "time_label": "晚饭后"})
    current = DatePlanningTaskState(
        user_id="u",
        relationship_id="r",
        conversation_id="c",
        current_plan=_plan(movie, dinner),
        plan_version=1,
    )
    route = RouteResult(
        normalized_query="新增午饭，晚饭后看电影",
        task_type=TaskType.DATE_PLANNING,
        task_confidence=1,
        date_mutation=DatePlanMutation.REORDER,
    )

    message = _compose_date_response(
        current=current,
        route=route,
        plan=_plan(lunch, dinner, moved_movie),
        changed=True,
    )

    assert "新增了[午餐] 测试烧烤店" in message
    assert "调整了现有电影院的位置" in message


class _RecordingDateSemanticParser:
    def __init__(self) -> None:
        self.calls = 0

    async def parse_date_operations(
        self,
        text,
        runtime_context,
        deterministic_operations,
    ) -> DateSemanticParseResult:
        del text, runtime_context
        self.calls += 1
        return DateSemanticParseResult(operations=list(deterministic_operations))


class _RecordingCombinedDateModel(_RecordingDateSemanticParser):
    def __init__(self) -> None:
        super().__init__()
        self.router_calls = 0

    async def correct(self, route_input, rule_result) -> RouteCorrection:
        self.router_calls += 1
        return RouteCorrection(
            task_type=rule_result.task_type,
            secondary_tasks=rule_result.secondary_tasks,
            task_confidence=0.99,
            evidence_spans=[route_input.latest_query],
            date_plan=rule_result.date_plan,
            date_patch=rule_result.date_patch,
            date_request_mode=rule_result.date_request_mode,
            date_intent=rule_result.date_intent,
            date_mutation=rule_result.date_mutation,
            date_operations=rule_result.date_operations,
        )


@pytest.mark.asyncio
async def test_complex_date_semantics_can_run_without_router_llm() -> None:
    parser = _RecordingDateSemanticParser()
    router = HybridRouter(SafetyPolicy(), date_semantic_parser=parser)
    result = await router.route(
        RouteInput(
            latest_query="午饭吃烧烤，晚饭后看电影",
            forced_task=TaskType.DATE_PLANNING,
        )
    )

    assert parser.calls == 1
    assert result.llm_used is False
    assert result.date_semantic_parse_required is True
    assert result.date_semantic_llm_used is True
    assert len(result.date_operations) == 2


@pytest.mark.asyncio
async def test_date_semantic_parser_remains_independent_after_router_correction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _RecordingCombinedDateModel()
    router = HybridRouter(SafetyPolicy(), corrector=model)
    monkeypatch.setattr(router, "_needs_llm_correction", lambda *_args: True)

    result = await router.route(
        RouteInput(
            latest_query="午饭吃烧烤，晚饭后看电影",
            forced_task=TaskType.DATE_PLANNING,
        )
    )

    assert model.router_calls == 1
    assert model.calls == 1
    assert result.llm_used is True
    assert result.date_semantic_llm_used is True


def test_semantic_parse_requirement_is_independent_of_router_context() -> None:
    resolver = DateOperationResolver()
    simple = resolver.resolve("预算600", None, DatePlanPatch(budget=600))
    complex_result = resolver.resolve(
        "午饭吃烧烤，晚饭后看电影",
        None,
        DatePlanPatch(
            dining_keywords=["烧烤"],
            meal_keywords={"lunch": ["烧烤"]},
            activity_keywords=["电影院"],
            schedule_hints=["晚饭后"],
        ),
    )

    assert requires_date_semantic_parse("预算600", None, simple) is False
    assert (
        requires_date_semantic_parse(
            "午饭吃烧烤，晚饭后看电影",
            None,
            complex_result,
        )
        is True
    )


@pytest.mark.asyncio
async def test_active_date_operation_suppresses_advice_only_without_advice_request() -> None:
    restaurant = _item(
        "restaurant",
        "现有西餐厅",
        PlaceCategory.RESTAURANT,
        "西餐",
        order=1,
        meal_type="dinner",
        time_label="晚餐",
    )
    movie = _item(
        "movie",
        "现有电影院",
        PlaceCategory.ENTERTAINMENT,
        "电影院",
        order=2,
        time_label="晚饭后",
    )
    state = DatePlanningTaskState(
        user_id="u",
        relationship_id="r",
        conversation_id="c",
        city="上海",
        budget=800,
        status="planned",
        current_plan=_plan(restaurant, movie),
    )
    store = InMemoryDatePlanningTaskStore()
    request = ConversationRequest(
        user_id="u",
        relationship_id="r",
        conversation_id="c",
        query="她不喜欢烧烤，把餐厅换成火锅",
    )
    runtime_context = await RuntimeContextBuilder(store).build(
        request,
        active_task=TaskType.DATE_PLANNING,
        date_task_state=state,
    )
    router = HybridRouter(SafetyPolicy())
    operation = await router.route(
        RouteInput(
            latest_query=request.query,
            active_task=TaskType.DATE_PLANNING,
            date_task_state=state,
            runtime_context=runtime_context,
        )
    )
    compound = await router.route(
        RouteInput(
            latest_query=("她不喜欢烧烤，把餐厅换成火锅，她还因为这事生气了，我该怎么哄她？"),
            active_task=TaskType.DATE_PLANNING,
            date_task_state=state,
            runtime_context=runtime_context,
        )
    )

    assert operation.task_type == TaskType.DATE_PLANNING
    assert TaskType.RELATIONSHIP_ADVICE not in operation.secondary_tasks
    assert operation.date_plan.excluded_keywords == ["烧烤"]
    assert [item.type for item in operation.date_operations] == [
        DateOperationType.REPLACE_STOP
    ]
    assert compound.task_type == TaskType.DATE_PLANNING
    assert TaskType.RELATIONSHIP_ADVICE in compound.secondary_tasks

    ambiguous_state = state.model_copy(
        update={
            "current_plan": _plan(
                restaurant,
                _item(
                    "restaurant-2",
                    "另一家餐厅",
                    PlaceCategory.RESTAURANT,
                    "日料",
                    order=2,
                    meal_type="lunch",
                    time_label="午餐",
                ),
                movie.model_copy(update={"order": 3}),
            )
        }
    )
    ambiguous_context = await RuntimeContextBuilder(store).build(
        request,
        active_task=TaskType.DATE_PLANNING,
        date_task_state=ambiguous_state,
    )
    ambiguous = await router.route(
        RouteInput(
            latest_query=request.query,
            active_task=TaskType.DATE_PLANNING,
            date_task_state=ambiguous_state,
            runtime_context=ambiguous_context,
        )
    )

    assert ambiguous.date_operations == []


def test_memory_gate_keeps_durable_preference_clause_from_date_operation() -> None:
    decision = MemoryGate().evaluate(
        "她不喜欢烧烤，把很久以前换成火锅",
        active_task=TaskType.DATE_PLANNING,
    )

    assert decision.should_extract is True
    assert "preference" in decision.signals

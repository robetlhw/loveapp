from datetime import date, datetime

import pytest

from loveapp.adapters.date_tasks import InMemoryDatePlanningTaskStore
from loveapp.adapters.maps.demo import DemoMapProvider
from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.agents.date_planner import DatePlanningAgent
from loveapp.agents.date_workflow import DatePlanningWorkflow
from loveapp.application.date_planning.clause_parsing import split_date_clauses
from loveapp.application.date_planning.operation_resolution import (
    DateOperationResolver,
    requires_date_semantic_parse,
)
from loveapp.application.date_planning.role_completion import complete_date_plan_roles
from loveapp.application.memory import MemoryService, NoOpMemoryExtractor
from loveapp.application.routing import HybridRouter, route_by_rules
from loveapp.application.runtime_context import RuntimeContextBuilder
from loveapp.bootstrap import build_container
from loveapp.core.config import Settings
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.conversation import ConversationRequest
from loveapp.domain.date_operations import (
    DateOperationType,
    DatePlanOperation,
    DateReplacementPreference,
    DateStopRequirement,
    DesiredDateStop,
    MealType,
    StopKind,
    StopReference,
    TemporalAnchor,
    TimeWindow,
)
from loveapp.domain.date_patch import DatePlanPatch
from loveapp.domain.date_plan import (
    DatePlan,
    DatePlanItem,
    DatePlanRequest,
    Place,
    PlaceSearchRequest,
)
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.date_workflow import DatePlanningWorkflowInput
from loveapp.domain.enums import (
    DatePlanningStatus,
    DateRequestMode,
    DateTaskIntent,
    PlaceCategory,
    RelationshipStage,
    TaskType,
)
from loveapp.domain.memory import MessageRole, StoredMessage
from loveapp.domain.routing import DatePlanSlots, RouteInput, RouteResult
from loveapp.domain.runtime_context import DatePlanRuntimeContext, RuntimeContext
from loveapp.safety import SafetyPolicy


def _place(place_id: str, name: str, category: PlaceCategory, *tags: str) -> Place:
    return Place(
        id=place_id,
        name=name,
        city="上海",
        address="上海静安区测试地址",
        category=category,
        tags=list(tags),
        search_keywords=list(tags),
        estimated_cost_per_person=50,
        source="test",
    )


def _item(
    place_id: str,
    name: str,
    category: PlaceCategory,
    keyword: str | None,
    *,
    order: int,
    meal_type: str | None = None,
    time_label: str | None = None,
) -> DatePlanItem:
    tags = (keyword,) if keyword is not None else ()
    return DatePlanItem(
        order=order,
        place=_place(place_id, name, category, *tags),
        duration_minutes=90,
        estimated_cost=100,
        reason="test",
        meal_type=meal_type,
        time_label=time_label,
        slot_keyword=keyword,
    )


def _plan(*items: DatePlanItem) -> DatePlan:
    return DatePlan(
        title="现有日程",
        summary="现有日程",
        items=list(items),
        total_estimated_cost=sum(item.estimated_cost for item in items),
        total_duration_minutes=sum(item.duration_minutes for item in items),
        data_source="test",
    )


def _runtime(plan: DatePlan) -> RuntimeContext:
    return RuntimeContext(
        user_id="u",
        relationship_id="r",
        conversation_id="c",
        relationship_stage=RelationshipStage.UNKNOWN,
        active_task=TaskType.DATE_PLANNING,
        active_date_plan=DatePlanRuntimeContext(
            status=DatePlanningStatus.PLANNED,
            city="上海",
            area="静安区",
            current_plan=plan,
            plan_version=1,
        ),
        now=datetime(2026, 8, 27, 12, 0),
    )


def _state(plan: DatePlan) -> DatePlanningTaskState:
    return DatePlanningTaskState(
        user_id="u",
        relationship_id="r",
        conversation_id="c",
        status=DatePlanningStatus.PLANNED,
        city="上海",
        area="静安区",
        date=date(2026, 8, 29),
        budget=800,
        current_plan=plan,
        plan_version=1,
    )


class _ScenarioBMapProvider(DemoMapProvider):
    async def search_places(self, request: PlaceSearchRequest) -> list[Place]:
        places = await super().search_places(request)
        if request.required_keywords or request.keywords:
            return places
        if request.category == PlaceCategory.ATTRACTION:
            return [
                _place(
                    "jing-an-temple",
                    "静安寺",
                    PlaceCategory.ATTRACTION,
                    "静安寺",
                    "景点",
                ).model_copy(update={"rating": 5, "estimated_cost_per_person": 0}),
                _place(
                    "alternate-activity",
                    "静安艺术中心",
                    PlaceCategory.ATTRACTION,
                    "景点",
                    "展览",
                ).model_copy(update={"rating": 4.9, "estimated_cost_per_person": 20}),
                *places,
            ]
        if request.category == PlaceCategory.RESTAURANT:
            return [
                _place(
                    "generic-dinner",
                    "ShakeShack",
                    PlaceCategory.RESTAURANT,
                    "西餐",
                ).model_copy(update={"rating": 5}),
            ]
        return places


@pytest.mark.parametrize(
    "query",
    ("能帮我准备一份日程安排吗", "能帮我准备一份日程吗"),
)
def test_schedule_activation_recovers_only_intentional_location_history(query: str) -> None:
    result = route_by_rules(
        RouteInput(
            latest_query=query,
            recent_messages=[
                StoredMessage(
                    id="location",
                    user_id="u",
                    relationship_id="r",
                    conversation_id="c",
                    role=MessageRole.USER,
                    content="想去静安区",
                )
            ],
        )
    )

    assert result.task_type == TaskType.DATE_PLANNING
    assert result.date_request_mode == DateRequestMode.ITINERARY
    assert result.date_plan.city == "上海"
    assert result.date_plan.area == "静安区"
    assert TaskType.RELATIONSHIP_ADVICE not in result.secondary_tasks


class _UnusedPlanner:
    async def plan(self, *args, **kwargs):  # pragma: no cover - branch guard
        raise AssertionError("planner should not run while activation needs clarification")


@pytest.mark.asyncio
async def test_recovered_location_seeds_a_new_date_task_without_polluting_patch() -> None:
    history = [
        StoredMessage(
            id="location",
            user_id="u",
            relationship_id="r",
            conversation_id="activation",
            role=MessageRole.USER,
            content="想去静安区",
        )
    ]
    query = "能帮我准备一份日程安排吗"
    route = route_by_rules(RouteInput(latest_query=query, recent_messages=history))
    request = ConversationRequest(
        user_id="u",
        relationship_id="r",
        conversation_id="activation",
        query=query,
    )
    workflow = DatePlanningWorkflow(  # type: ignore[arg-type]
        _UnusedPlanner(),
        InMemoryDatePlanningTaskStore(),
    )

    result = await workflow.run(DatePlanningWorkflowInput(request=request, route=route))

    assert route.date_patch is not None
    assert route.date_patch.city is None
    assert route.date_patch.area is None
    assert result.task_state.city == "上海"
    assert result.task_state.area == "静安区"
    assert result.needs_clarification is True


@pytest.mark.asyncio
async def test_exact_postponed_activation_scenario_builds_full_plan(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)
    try:
        active_task = None
        responses = []
        for query in (
            "我想带对象去约会，能帮我吗",
            "想去静安区",
            "能帮我准备一份日程安排吗",
            "这周六，午饭吃烧烤，下午看电影，晚上吃火锅",
        ):
            response = await container.conversation_agent.chat(
                ConversationRequest(
                    user_id="b51-activation-user",
                    relationship_id="b51-activation-relationship",
                    conversation_id="b51-activation-conversation",
                    query=query,
                    active_task=active_task,
                )
            )
            active_task = response.active_task
            responses.append(response)
    finally:
        await container.aclose()

    activated = responses[2]
    final = responses[3]
    assert activated.route.task_type == TaskType.DATE_PLANNING
    assert activated.date_task_state is not None
    assert activated.date_task_state.city == "上海"
    assert activated.date_task_state.area == "静安区"
    assert final.date_task_state is not None
    assert final.date_task_state.date == date(2026, 8, 29)
    assert final.date_plan is not None
    by_keyword = {
        item.slot_keyword: item
        for item in final.date_plan.items
        if item.slot_keyword is not None
    }
    assert by_keyword["烧烤"].meal_type == "lunch"
    assert by_keyword["电影院"].time_label == "下午"
    assert by_keyword["火锅"].meal_type == "dinner"


@pytest.mark.asyncio
async def test_exact_natural_language_modification_scenario(
    app_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = build_container(app_settings)
    monkeypatch.setattr(
        container.date_planning_agent,
        "_map_provider",
        _ScenarioBMapProvider(),
    )
    try:
        active_task = None
        responses = []
        for query in (
            "我想带女朋友去静安区玩，能帮我安排一个攻略吗",
            "时间定在这周六，预算500",
            "午饭想吃烧烤，吃完晚饭后，想去看个电影",
            "预算改为600，静安寺我不想去了，换个别的",
        ):
            response = await container.conversation_agent.chat(
                ConversationRequest(
                    user_id="b51-modification-user",
                    relationship_id="b51-modification-relationship",
                    conversation_id="b51-modification-conversation",
                    query=query,
                    active_task=active_task,
                )
            )
            active_task = response.active_task
            responses.append(response)
    finally:
        await container.aclose()

    initial_plan = responses[1].date_plan
    enriched_plan = responses[2].date_plan
    final = responses[3]
    assert initial_plan is not None
    assert "jing-an-temple" in {item.place.id for item in initial_plan.items}
    assert enriched_plan is not None
    enriched_by_id = {item.place.id: item for item in enriched_plan.items}
    assert enriched_by_id["demo-restaurant-8"].meal_type == "lunch"
    assert enriched_by_id["generic-dinner"].meal_type == "dinner"
    assert enriched_by_id["generic-dinner"].order < enriched_by_id["demo-entertainment-1"].order
    assert enriched_by_id["demo-entertainment-1"].time_label == "晚饭后"
    assert final.route.task_type == TaskType.DATE_PLANNING
    assert TaskType.RELATIONSHIP_ADVICE not in final.route.secondary_tasks
    assert final.route.date_operation_rejections == []
    assert final.date_task_state is not None
    assert final.date_task_state.budget == 600
    assert final.date_plan is not None
    final_ids = {item.place.id for item in final.date_plan.items}
    assert "jing-an-temple" not in final_ids
    assert "alternate-activity" in final_ids


@pytest.mark.asyncio
async def test_new_executable_date_plan_applies_focus_guard() -> None:
    result = await HybridRouter(SafetyPolicy()).route(
        RouteInput(latest_query="她不喜欢烧烤，能帮我准备一份日程安排吗")
    )

    assert result.task_type == TaskType.DATE_PLANNING
    assert TaskType.RELATIONSHIP_ADVICE not in result.secondary_tasks
    assert result.task_guard_applied is True


def test_named_and_ordinal_generic_replacements_are_typed() -> None:
    temple = _item(
        "temple",
        "静安寺",
        PlaceCategory.ATTRACTION,
        "景点",
        order=1,
    )
    movie = _item(
        "movie",
        "百美汇电影院",
        PlaceCategory.ENTERTAINMENT,
        "电影院",
        order=2,
    )
    runtime = _runtime(_plan(temple, movie))
    resolver = DateOperationResolver()

    named = resolver.resolve("静安寺我不想去，换个别的", runtime, DatePlanPatch())
    ordinal = resolver.resolve("第二个地方换一下", runtime, DatePlanPatch())

    assert len(named.operations) == 1
    assert named.operations[0].type == DateOperationType.REPLACE_STOP
    assert named.operations[0].target is not None
    assert named.operations[0].target.place_id == "temple"
    assert named.operations[0].payload is not None
    assert named.operations[0].payload.kind == StopKind.ACTIVITY
    assert named.operations[0].payload.generic_replacement is True
    assert ordinal.operations[0].target == StopReference(ordinal=2)


def test_nearby_request_enters_semantic_modification_path() -> None:
    temple = _item(
        "temple",
        "静安寺",
        PlaceCategory.ATTRACTION,
        "景点",
        order=1,
    )
    runtime = _runtime(_plan(temple))
    resolver = DateOperationResolver()
    resolution = resolver.resolve(
        "静安寺有点远，有没有近一点的？",
        runtime,
        DatePlanPatch(),
    )

    assert requires_date_semantic_parse(
        "静安寺有点远，有没有近一点的？",
        runtime,
        resolution,
    )
    assert len(resolution.operations) == 1
    assert resolution.operations[0].payload is not None
    assert resolution.operations[0].payload.replacement_preferences == [
        DateReplacementPreference.NEARBY
    ]


def test_simple_budget_update_does_not_trigger_date_semantic_parser() -> None:
    plan = _plan(
        _item(
            "temple",
            "静安寺",
            PlaceCategory.ATTRACTION,
            "景点",
            order=1,
        )
    )
    runtime = _runtime(plan)
    result = DateOperationResolver().resolve(
        "预算改为600",
        runtime,
        DatePlanPatch(budget=600),
    )

    assert requires_date_semantic_parse("预算改为600", runtime, result) is False


def test_constraint_update_does_not_hide_later_replacement_target_evidence() -> None:
    plan = _plan(
        _item(
            "temple",
            "静安寺",
            PlaceCategory.ATTRACTION,
            None,
            order=1,
        )
    )
    result = DateOperationResolver().resolve(
        "预算改为600，静安寺我不想去了，换个别的",
        _runtime(plan),
        DatePlanPatch(budget=600),
    )

    assert [operation.type for operation in result.operations] == [
        DateOperationType.UPDATE_CONSTRAINT,
        DateOperationType.REPLACE_STOP,
    ]


@pytest.mark.asyncio
async def test_generic_replacement_executes_through_existing_planner_contract() -> None:
    planned_date = date(2026, 8, 29)
    temple = _item(
        "temple",
        "静安寺",
        PlaceCategory.ATTRACTION,
        "景点",
        order=1,
    ).model_copy(update={"scheduled_date": planned_date})
    dinner = _item(
        "dinner",
        "ShakeShack",
        PlaceCategory.RESTAURANT,
        "西餐",
        order=2,
        meal_type="dinner",
        time_label="晚餐",
    ).model_copy(update={"scheduled_date": planned_date})
    current_plan = _plan(temple, dinner).model_copy(
        update={"start_date": planned_date, "end_date": planned_date}
    )
    current = _state(current_plan)
    store = InMemoryDatePlanningTaskStore()
    planner = DatePlanningAgent(
        DemoMapProvider(),
        MemoryService(InMemoryMemoryStore(), NoOpMemoryExtractor()),
    )
    workflow = DatePlanningWorkflow(planner, store)
    request = ConversationRequest(
        user_id="u",
        relationship_id="r",
        conversation_id="c",
        query="静安寺我不想去，换个别的",
    )
    runtime_context = await RuntimeContextBuilder(store).build(
        request,
        active_task=TaskType.DATE_PLANNING,
        date_task_state=current,
    )
    route = route_by_rules(
        RouteInput(
            latest_query=request.query,
            active_task=TaskType.DATE_PLANNING,
            date_task_state=current,
            runtime_context=runtime_context,
        )
    )

    result = await workflow.run(
        DatePlanningWorkflowInput(
            request=request,
            route=route,
            current_task_state=current,
            runtime_context=runtime_context,
        )
    )

    assert result.plan_changed is True
    assert result.task_state.plan_version == 2
    assert result.plan is not None
    assert "temple" not in {item.place.id for item in result.plan.items}
    assert "dinner" in {item.place.id for item in result.plan.items}


@pytest.mark.asyncio
async def test_ambiguous_restaurant_reference_fails_closed_and_clarifies() -> None:
    lunch = _item(
        "lunch",
        "午餐餐厅 A",
        PlaceCategory.RESTAURANT,
        "西餐",
        order=1,
        meal_type="lunch",
    )
    dinner = _item(
        "dinner",
        "晚餐餐厅 B",
        PlaceCategory.RESTAURANT,
        "火锅",
        order=2,
        meal_type="dinner",
    )
    plan = _plan(lunch, dinner)
    result = await HybridRouter(SafetyPolicy()).route(
        RouteInput(
            latest_query="那个餐厅换掉",
            active_task=TaskType.DATE_PLANNING,
            date_task_state=_state(plan),
            runtime_context=_runtime(plan),
        )
    )

    assert result.date_operations == []
    assert result.date_unresolved_references == ["午餐餐厅 A", "晚餐餐厅 B"]
    assert result.needs_clarification is True
    assert result.clarification_reason == "unresolved_date_plan_reference"
    assert result.clarification_options == ["午餐餐厅 A", "晚餐餐厅 B"]


def test_semantic_parser_cannot_guess_one_ambiguous_restaurant() -> None:
    lunch = _item(
        "lunch",
        "午餐餐厅 A",
        PlaceCategory.RESTAURANT,
        "西餐",
        order=1,
        meal_type="lunch",
    )
    dinner = _item(
        "dinner",
        "晚餐餐厅 B",
        PlaceCategory.RESTAURANT,
        "火锅",
        order=2,
        meal_type="dinner",
    )
    runtime = _runtime(_plan(lunch, dinner))
    guessed = DatePlanOperation(
        type=DateOperationType.REPLACE_STOP,
        target=StopReference(place_id="dinner", place_name="晚餐餐厅 B"),
        payload=DesiredDateStop(kind=StopKind.DINING, generic_replacement=True),
        source_span="那个餐厅换掉",
    )

    result = DateOperationResolver().resolve(
        "那个餐厅换掉",
        runtime,
        DatePlanPatch(),
        proposed_operations=[guessed],
    )

    assert result.operations == ()
    assert result.unresolved_references == ("午餐餐厅 A", "晚餐餐厅 B")
    assert any(item.reason == "plan_reference_unresolved" for item in result.rejected)


def test_temporal_prefix_clause_is_folded_into_following_action() -> None:
    clauses = split_date_clauses("午饭吃烧烤，吃完晚饭后，想去看电影")

    assert [clause.text for clause in clauses] == [
        "午饭吃烧烤",
        "吃完晚饭后想去看电影",
    ]
    route = route_by_rules(
        RouteInput(
            latest_query="午饭吃烧烤，吃完晚饭后，想去看电影",
            forced_task=TaskType.DATE_PLANNING,
        )
    )
    payloads = [operation.payload for operation in route.date_operations if operation.payload]
    barbecue = next(stop for stop in payloads if stop.keyword == "烧烤")
    movie = next(stop for stop in payloads if stop.keyword == "电影院")
    assert barbecue.meal_type == MealType.LUNCH
    assert movie.after == TemporalAnchor.DINNER


def test_role_completion_binds_only_unique_dinner_and_orders_dependency() -> None:
    lunch = _item(
        "lunch",
        "烧烤店",
        PlaceCategory.RESTAURANT,
        "烧烤",
        order=1,
        meal_type="lunch",
    )
    movie = _item(
        "movie",
        "电影院",
        PlaceCategory.ENTERTAINMENT,
        "电影院",
        order=2,
    )
    generic_dining = _item(
        "generic-dining",
        "ShakeShack",
        PlaceCategory.RESTAURANT,
        "西餐",
        order=3,
    )
    desired = [
        DesiredDateStop(kind=StopKind.DINING, keyword="烧烤", meal_type=MealType.LUNCH),
        DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword="电影院",
            time_window=TimeWindow(label="晚饭后"),
            after=TemporalAnchor.DINNER,
        ),
    ]

    completion = complete_date_plan_roles(_plan(lunch, movie, generic_dining), desired)
    by_id = {item.place.id: item for item in completion.plan.items}

    assert completion.inferred_dinner_item_ids == ("generic-dining",)
    assert by_id["generic-dining"].meal_type == "dinner"
    assert by_id["generic-dining"].order < by_id["movie"].order
    assert completion.unresolved_roles == ()


def test_role_completion_does_not_guess_between_two_dining_candidates() -> None:
    movie = _item(
        "movie",
        "电影院",
        PlaceCategory.ENTERTAINMENT,
        "电影院",
        order=1,
    )
    first = _item("first", "餐厅 A", PlaceCategory.RESTAURANT, "西餐", order=2)
    second = _item("second", "餐厅 B", PlaceCategory.RESTAURANT, "火锅", order=3)
    desired = [
        DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword="电影院",
            time_window=TimeWindow(label="晚饭后"),
            after=TemporalAnchor.DINNER,
        )
    ]

    completion = complete_date_plan_roles(_plan(movie, first, second), desired)

    assert completion.inferred_dinner_item_ids == ()
    assert completion.unresolved_roles == ("day_1:dinner_anchor_ambiguous",)
    assert all(item.meal_type is None for item in completion.plan.items)


def test_role_completion_maps_two_untyped_meals_to_two_required_anchors() -> None:
    first = _item("first", "火锅店", PlaceCategory.RESTAURANT, "火锅", order=1)
    second = _item("second", "烧烤店", PlaceCategory.RESTAURANT, "烧烤", order=2)
    museum = _item(
        "museum",
        "博物馆",
        PlaceCategory.ATTRACTION,
        "博物馆",
        order=3,
    )
    attraction = _item(
        "attraction",
        "景点",
        PlaceCategory.ATTRACTION,
        "景点",
        order=4,
    )
    desired = [
        DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword="博物馆",
            after=TemporalAnchor.LUNCH,
        ),
        DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword="景点",
            after=TemporalAnchor.DINNER,
        ),
    ]

    completion = complete_date_plan_roles(
        _plan(first, second, museum, attraction),
        desired,
    )
    by_id = {item.place.id: item for item in completion.plan.items}

    assert completion.inferred_lunch_item_ids == ("first",)
    assert completion.inferred_dinner_item_ids == ("second",)
    assert completion.unresolved_roles == ()
    assert by_id["first"].meal_type == "lunch"
    assert by_id["second"].meal_type == "dinner"
    assert by_id["first"].order < by_id["museum"].order
    assert by_id["second"].order < by_id["attraction"].order


@pytest.mark.asyncio
async def test_planner_supplements_two_meal_anchors_with_one_untyped_dining() -> None:
    planner = DatePlanningAgent(
        DemoMapProvider(),
        MemoryService(InMemoryMemoryStore(), NoOpMemoryExtractor()),
    )
    hotpot = _place("hotpot", "火锅店", PlaceCategory.RESTAURANT, "火锅")
    dinner = _place("dinner", "晚餐餐厅", PlaceCategory.RESTAURANT, "西餐")
    museum = _place("museum", "博物馆", PlaceCategory.ATTRACTION, "博物馆")
    attraction = _place("attraction", "景点", PlaceCategory.ATTRACTION, "景点")
    desired = [
        DesiredDateStop(kind=StopKind.DINING, keyword="火锅"),
        DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword="博物馆",
            after=TemporalAnchor.LUNCH,
        ),
        DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword="景点",
            after=TemporalAnchor.DINNER,
        ),
    ]
    request = DatePlanRequest(
        city="上海",
        budget=1000,
        dining_keywords=["火锅"],
        activity_keywords=["博物馆", "景点"],
        schedule_hints=["午饭后", "晚饭后"],
        requirements=[
            DateStopRequirement(id=f"requirement-{index}", alternatives=[stop])
            for index, stop in enumerate(desired, start=1)
        ],
    )

    candidate = await planner._build_keyword_plan(
        {"request": request},
        [museum, attraction],
        [hotpot, dinner],
        [],
    )

    assert candidate is not None
    completion = complete_date_plan_roles(candidate, desired)
    assert completion.unresolved_roles == ()
    assert {item.meal_type for item in completion.plan.items} >= {"lunch", "dinner"}
    assert len(
        [
            item
            for item in candidate.items
            if item.place.category == PlaceCategory.RESTAURANT
        ]
    ) == 2


def test_role_completion_prefers_requirement_owned_dining_over_unrelated_cafe() -> None:
    hotpot = _item("hotpot", "火锅店", PlaceCategory.RESTAURANT, "火锅", order=1)
    cafe = _item("cafe", "咖啡馆", PlaceCategory.CAFE, "咖啡", order=2)
    movie = _item(
        "movie",
        "电影院",
        PlaceCategory.ENTERTAINMENT,
        "电影院",
        order=3,
    )
    desired = [
        DesiredDateStop(kind=StopKind.DINING, keyword="火锅"),
        DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword="电影院",
            after=TemporalAnchor.DINNER,
        ),
    ]

    completion = complete_date_plan_roles(_plan(hotpot, cafe, movie), desired)
    by_id = {item.place.id: item for item in completion.plan.items}

    assert completion.inferred_dinner_item_ids == ("hotpot",)
    assert completion.unresolved_roles == ()
    assert by_id["hotpot"].meal_type == "dinner"
    assert by_id["cafe"].meal_type is None


def test_role_completion_does_not_promote_unrelated_cafe_to_meal_anchor() -> None:
    cafe = _item("cafe", "咖啡馆", PlaceCategory.CAFE, "咖啡", order=1)
    movie = _item(
        "movie",
        "电影院",
        PlaceCategory.ENTERTAINMENT,
        "电影院",
        order=2,
    )
    desired = [
        DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword="电影院",
            after=TemporalAnchor.DINNER,
        )
    ]

    completion = complete_date_plan_roles(_plan(cafe, movie), desired)

    assert completion.inferred_dinner_item_ids == ()
    assert completion.unresolved_roles == ("day_1:dinner_anchor_missing",)
    assert completion.plan.items[0].meal_type is None


def test_role_completion_scopes_same_place_id_inference_to_day() -> None:
    day_one_dining = _item(
        "shared-dining",
        "同一家餐厅",
        PlaceCategory.RESTAURANT,
        "餐厅",
        order=1,
    )
    day_one_museum = _item(
        "museum",
        "博物馆",
        PlaceCategory.ATTRACTION,
        "博物馆",
        order=2,
    )
    day_two_dinner = day_one_dining.model_copy(
        update={"day_index": 2, "meal_type": "dinner", "time_label": "晚餐"}
    )
    desired = [
        DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword="博物馆",
            target_day=1,
            after=TemporalAnchor.LUNCH,
        )
    ]
    plan = _plan(day_one_dining, day_one_museum, day_two_dinner).model_copy(
        update={"day_count": 2}
    )

    completion = complete_date_plan_roles(plan, desired)
    by_day = {
        item.day_index: item
        for item in completion.plan.items
        if item.place.id == "shared-dining"
    }

    assert by_day[1].meal_type == "lunch"
    assert by_day[2].meal_type == "dinner"


def test_role_completion_scopes_same_place_id_ordering_to_day() -> None:
    day_one_lunch = _item(
        "lunch",
        "午餐",
        PlaceCategory.RESTAURANT,
        "午餐",
        order=1,
        meal_type="lunch",
    )
    day_one_activity = _item(
        "shared-activity",
        "展馆",
        PlaceCategory.ATTRACTION,
        "展馆",
        order=2,
    )
    day_one_dinner = _item(
        "dinner",
        "晚餐",
        PlaceCategory.RESTAURANT,
        "晚餐",
        order=3,
        meal_type="dinner",
    )
    day_two_dinner = day_one_dinner.model_copy(update={"day_index": 2, "order": 1})
    day_two_activity = day_one_activity.model_copy(update={"day_index": 2, "order": 2})
    desired = [
        DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword="展馆",
            target_day=1,
            after=TemporalAnchor.LUNCH,
        ),
        DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword="展馆",
            target_day=2,
            after=TemporalAnchor.DINNER,
        ),
    ]
    plan = _plan(
        day_one_lunch,
        day_one_activity,
        day_one_dinner,
        day_two_dinner,
        day_two_activity,
    ).model_copy(update={"day_count": 2})

    completion = complete_date_plan_roles(plan, desired)
    ordered = {
        day: [item.place.id for item in completion.plan.items if item.day_index == day]
        for day in (1, 2)
    }

    assert ordered[1] == ["lunch", "shared-activity", "dinner"]
    assert ordered[2] == ["dinner", "shared-activity"]


class _RoleCompletionPlanner:
    def __init__(self, candidate: DatePlan) -> None:
        self.candidate = candidate
        self.rebuild_calls = 0

    async def plan(self, request, **kwargs):
        del kwargs
        items = [
            item.model_copy(update={"scheduled_date": request.date})
            for item in self.candidate.items
        ]
        return self.candidate.model_copy(
            update={
                "start_date": request.date,
                "end_date": request.date,
                "items": items,
            }
        )

    async def rebuild_plan(self, existing_plan, request, items, *, summary, trace=None):
        del request, trace
        self.rebuild_calls += 1
        return existing_plan.model_copy(update={"items": items, "summary": summary})


@pytest.mark.asyncio
async def test_workflow_completes_roles_before_validation_and_persistence() -> None:
    lunch = _item(
        "lunch",
        "烧烤店",
        PlaceCategory.RESTAURANT,
        "烧烤",
        order=1,
        meal_type="lunch",
    )
    movie = _item(
        "movie",
        "电影院",
        PlaceCategory.ENTERTAINMENT,
        "电影院",
        order=2,
    )
    generic_dining = _item(
        "generic-dining",
        "ShakeShack",
        PlaceCategory.RESTAURANT,
        "西餐",
        order=3,
    )
    planner = _RoleCompletionPlanner(_plan(lunch, movie, generic_dining))
    store = InMemoryDatePlanningTaskStore()
    workflow = DatePlanningWorkflow(planner, store)  # type: ignore[arg-type]
    request = ConversationRequest(
        user_id="u",
        relationship_id="r",
        conversation_id="role-workflow",
        query="午饭吃烧烤，吃完晚饭后，想去看电影",
    )
    route = RouteResult(
        normalized_query=request.query,
        task_type=TaskType.DATE_PLANNING,
        task_confidence=1,
        date_intent=DateTaskIntent.CONTINUE,
        date_plan=DatePlanSlots(
            city="上海",
            date=date(2026, 8, 29),
            budget=800,
            dining_keywords=["烧烤"],
            meal_keywords={"lunch": ["烧烤"]},
            activity_keywords=["电影院"],
            schedule_hints=["晚饭后"],
        ),
    )
    trace = ExecutionTrace()

    result = await workflow.run(
        DatePlanningWorkflowInput(request=request, route=route),
        trace=trace,
    )

    assert result.plan is not None
    by_id = {item.place.id: item for item in result.plan.items}
    assert by_id["generic-dining"].meal_type == "dinner"
    assert by_id["generic-dining"].order < by_id["movie"].order
    assert planner.rebuild_calls == 1
    role_trace = next(
        record for record in trace.snapshot() if record.name == "date_plan_role_completion"
    )
    assert role_trace.details["inferred_dinner_items"] == 1
    assert result.task_state.current_plan == result.plan

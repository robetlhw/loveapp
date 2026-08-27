from datetime import date

from loveapp.adapters.date_tasks import InMemoryDatePlanningTaskStore
from loveapp.adapters.maps.demo import DemoMapProvider
from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.agents.date_planner import DatePlanningAgent
from loveapp.agents.date_workflow import DatePlanningWorkflow
from loveapp.application.memory import MemoryService, NoOpMemoryExtractor
from loveapp.application.routing import route_by_rules
from loveapp.application.runtime_context import RuntimeContextBuilder
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.conversation import ConversationRequest
from loveapp.domain.date_operations import DateOperationType
from loveapp.domain.date_plan import DatePlan, DatePlanItem, Place, PlaceSearchRequest
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.date_workflow import DatePlanningWorkflowInput
from loveapp.domain.enums import (
    DatePlanningStatus,
    PlaceCategory,
    TaskType,
    TransportMode,
)
from loveapp.domain.routing import RouteInput


class PhaseBMapProvider(DemoMapProvider):
    async def search_places(self, request: PlaceSearchRequest) -> list[Place]:
        required = request.required_keywords or request.keywords
        if request.category == PlaceCategory.RESTAURANT and "烧烤" in required:
            return [
                Place(
                    id="phase-b-barbecue",
                    name="测试烧烤店",
                    city=request.city,
                    address="上海静安区测试地址",
                    category=PlaceCategory.RESTAURANT,
                    tags=["烧烤", "烤肉"],
                    estimated_cost_per_person=40,
                    rating=4.8,
                    source=self.name,
                )
            ]
        return await super().search_places(request)


def _place(
    place_id: str,
    name: str,
    category: PlaceCategory,
    *tags: str,
) -> Place:
    return Place(
        id=place_id,
        name=name,
        city="上海",
        address="上海静安区测试地址",
        category=category,
        tags=list(tags),
        estimated_cost_per_person=50,
        source="test",
    )


def _existing_state(request: ConversationRequest) -> DatePlanningTaskState:
    movie = DatePlanItem(
        order=1,
        place=_place(
            "existing-movie",
            "现有电影院",
            PlaceCategory.ENTERTAINMENT,
            "电影",
            "电影院",
        ),
        duration_minutes=90,
        estimated_cost=100,
        reason="下午看电影",
        time_label="下午",
        slot_keyword="电影院",
    )
    dinner = DatePlanItem(
        order=2,
        place=_place(
            "existing-dinner",
            "现有西餐厅",
            PlaceCategory.RESTAURANT,
            "西餐",
        ),
        duration_minutes=90,
        estimated_cost=100,
        reason="晚餐",
        meal_type="dinner",
        time_label="晚餐",
        slot_keyword="西餐",
    )
    return DatePlanningTaskState(
        user_id=request.user_id,
        relationship_id=request.relationship_id,
        conversation_id=request.conversation_id or "",
        status=DatePlanningStatus.PLANNED,
        city="上海",
        area="静安区",
        date=date(2026, 8, 29),
        budget=1000,
        dining_keywords=["西餐"],
        meal_keywords={"dinner": ["西餐"]},
        activity_keywords=["电影院"],
        schedule_hints=["下午", "晚饭"],
        transport_mode=TransportMode.TRANSIT,
        current_plan=DatePlan(
            title="现有计划",
            summary="电影在晚饭前",
            start_date=date(2026, 8, 29),
            end_date=date(2026, 8, 29),
            items=[movie, dinner],
            total_estimated_cost=200,
            total_duration_minutes=180,
            data_source="test",
        ),
        plan_version=1,
    )


def _workflow():
    memory = MemoryService(InMemoryMemoryStore(), NoOpMemoryExtractor())
    planner = DatePlanningAgent(PhaseBMapProvider(), memory)
    store = InMemoryDatePlanningTaskStore()
    return DatePlanningWorkflow(planner, store), store


async def test_compound_operations_update_add_and_move_existing_plan() -> None:
    workflow, store = _workflow()
    request = ConversationRequest(
        user_id="phase-b-user",
        relationship_id="phase-b-relationship",
        conversation_id="phase-b-existing",
        query="预算改到600，加一顿烧烤午饭，晚饭后看电影",
    )
    current = _existing_state(request)
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
    trace = ExecutionTrace()

    result = await workflow.run(
        DatePlanningWorkflowInput(
            request=request,
            route=route,
            current_task_state=current,
            runtime_context=runtime_context,
        ),
        trace=trace,
    )

    assert route.date_patch is not None
    assert route.date_patch.budget == 600
    assert [operation.type for operation in route.date_operations] == [
        DateOperationType.UPDATE_CONSTRAINT,
        DateOperationType.UPDATE_CONSTRAINT,
        DateOperationType.ADD_STOP,
        DateOperationType.MOVE_STOP,
    ]
    assert result.task_state.budget == 600
    assert result.task_state.plan_version == 2
    assert result.plan is not None
    by_id = {item.place.id: item for item in result.plan.items}
    assert {"existing-movie", "existing-dinner", "phase-b-barbecue"} <= set(by_id)
    assert by_id["phase-b-barbecue"].meal_type == "lunch"
    assert by_id["existing-dinner"].order < by_id["existing-movie"].order
    assert by_id["existing-movie"].time_label == "晚饭后"
    execution_trace = next(
        item for item in trace.snapshot() if item.name == "date_operation_execute"
    )
    assert execution_trace.details["applied_count"] == 4
    assert execution_trace.details["rejected_count"] == 0


async def test_compound_operations_create_structured_new_plan() -> None:
    workflow, store = _workflow()
    request = ConversationRequest(
        user_id="phase-b-user",
        relationship_id="phase-b-relationship",
        conversation_id="phase-b-new",
        query=(
            "帮我安排周六上海静安区的约会，预算600元，"
            "加一顿烧烤午饭，晚饭后看电影"
        ),
    )
    runtime_context = await RuntimeContextBuilder(store).build(
        request,
        active_task=TaskType.DATE_PLANNING,
    )
    route = route_by_rules(
        RouteInput(
            latest_query=request.query,
            forced_task=TaskType.DATE_PLANNING,
            runtime_context=runtime_context,
        )
    )

    result = await workflow.run(
        DatePlanningWorkflowInput(
            request=request,
            route=route,
            runtime_context=runtime_context,
        )
    )

    assert route.date_patch is not None
    assert route.date_patch.budget == 600
    assert len(route.date_operations) >= 4
    assert result.plan is not None
    by_keyword = {
        item.slot_keyword: item
        for item in result.plan.items
        if item.slot_keyword is not None
    }
    assert by_keyword["烧烤"].meal_type == "lunch"
    assert by_keyword["电影院"].time_label == "晚饭后"
    assert by_keyword["烧烤"].order < by_keyword["电影院"].order

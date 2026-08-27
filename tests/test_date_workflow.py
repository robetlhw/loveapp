from datetime import date
from types import SimpleNamespace

import pytest

from loveapp.adapters.date_tasks import InMemoryDatePlanningTaskStore
from loveapp.agents.date_workflow import DatePlanningWorkflow
from loveapp.application.date_planning.operations import DatePlanOperationExecution
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.conversation import ConversationRequest
from loveapp.domain.date_operations import (
    DateConstraintField,
    DateOperationType,
    DatePlanOperation,
)
from loveapp.domain.date_patch import DatePlanPatch, SlotSource
from loveapp.domain.date_plan import DatePlan, DatePlanItem, Place
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.date_workflow import DatePlanningWorkflowInput
from loveapp.domain.enums import (
    DatePlanMutation,
    DatePlanningStatus,
    DateTaskIntent,
    PlaceCategory,
    TaskType,
)
from loveapp.domain.routing import DatePlanSlots, RouteResult


class UnusedPlanner:
    async def plan(self, *args, **kwargs):  # pragma: no cover - guards workflow branches
        raise AssertionError("planner should not run")


def _route(*, intent: DateTaskIntent, slots: DatePlanSlots | None = None) -> RouteResult:
    return RouteResult(
        normalized_query="date request",
        task_type=TaskType.DATE_PLANNING,
        task_confidence=1.0,
        date_intent=intent,
        date_plan=slots or DatePlanSlots(),
    )


class PermissiveValidator:
    def validate(self, plan, request, constraints):
        del plan, request, constraints
        return SimpleNamespace(valid=True, issues=[])


class RecordingPlanner:
    def __init__(self, *, empty: bool = False) -> None:
        self.request = None
        self.empty = empty

    async def plan(self, request, **kwargs):
        del kwargs
        self.request = request
        if self.empty:
            return DatePlan(
                title="empty",
                summary="empty",
                total_estimated_cost=0,
                total_duration_minutes=0,
                data_source="test",
            )
        place = Place(
            id="test-place",
            name="测试地点",
            city=request.city or "上海",
            address="测试地址",
            category=PlaceCategory.ATTRACTION,
            estimated_cost_per_person=50,
            source="test",
        )
        return DatePlan(
            title="test",
            summary="test",
            items=[
                DatePlanItem(
                    order=1,
                    place=place,
                    duration_minutes=60,
                    estimated_cost=50,
                    reason="test",
                )
            ],
            total_estimated_cost=50,
            total_duration_minutes=60,
            data_source="test",
        )


class RecordingOperationExecutor:
    def __init__(self) -> None:
        self.calls = []

    async def apply(
        self,
        existing_plan,
        operations,
        request,
        *,
        trace=None,
        required_mutation=DatePlanMutation.NONE,
    ):
        del trace, required_mutation
        self.calls.append((existing_plan, operations, request))
        assert existing_plan is not None
        return DatePlanOperationExecution(
            plan=existing_plan.model_copy(update={"summary": "operation path"}),
            applied=tuple(operations),
            rejected=(),
            effective_mutation=DatePlanMutation.UPDATE_CONSTRAINT,
        )


@pytest.mark.asyncio
async def test_workflow_persists_cancelled_date_task() -> None:
    store = InMemoryDatePlanningTaskStore()
    workflow = DatePlanningWorkflow(UnusedPlanner(), store)  # type: ignore[arg-type]
    request = ConversationRequest(
        user_id="workflow-user",
        relationship_id="workflow-relationship",
        conversation_id="workflow-conversation",
        query="cancel",
    )

    result = await workflow.run(
        DatePlanningWorkflowInput(request=request, route=_route(intent=DateTaskIntent.CANCEL))
    )

    assert result.cancelled is True
    assert result.task_state.status.value == "paused"
    assert (
        await store.get(
            user_id=request.user_id,
            relationship_id=request.relationship_id,
            conversation_id=request.conversation_id,
        )
        == result.task_state
    )


@pytest.mark.asyncio
async def test_workflow_persists_clarification_without_invoking_planner() -> None:
    store = InMemoryDatePlanningTaskStore()
    workflow = DatePlanningWorkflow(UnusedPlanner(), store)  # type: ignore[arg-type]
    request = ConversationRequest(
        user_id="workflow-user",
        relationship_id="workflow-relationship",
        conversation_id="workflow-clarification",
        query="budget 300",
    )

    result = await workflow.run(
        DatePlanningWorkflowInput(
            request=request,
            route=_route(intent=DateTaskIntent.NEW_REQUEST, slots=DatePlanSlots(budget=300)),
        )
    )

    assert result.needs_clarification is True
    assert result.task_state.asked_fields == ["city", "date_time"]
    assert result.task_state.budget == 300
    assert result.message == "你想在哪座城市安排这次约会？"


@pytest.mark.asyncio
async def test_workflow_applies_current_turn_patch_over_committed_state() -> None:
    store = InMemoryDatePlanningTaskStore()
    planner = RecordingPlanner()
    workflow = DatePlanningWorkflow(
        planner,  # type: ignore[arg-type]
        store,
        PermissiveValidator(),  # type: ignore[arg-type]
    )
    request = ConversationRequest(
        user_id="workflow-user",
        relationship_id="workflow-relationship",
        conversation_id="workflow-patch",
        query="预算改为600元",
    )
    current = DatePlanningTaskState(
        user_id=request.user_id,
        relationship_id=request.relationship_id,
        conversation_id=request.conversation_id,
        city="上海",
        area="静安区",
        date=date(2026, 8, 29),
        budget=300,
    )
    route = _route(intent=DateTaskIntent.SUPPLEMENT).model_copy(
        update={
            "date_patch": DatePlanPatch(
                budget=600,
                source_by_field={"budget": SlotSource.RULE},
            )
        }
    )
    trace = ExecutionTrace()

    result = await workflow.run(
        DatePlanningWorkflowInput(
            request=request,
            route=route,
            current_task_state=current,
        ),
        trace=trace,
    )

    assert planner.request is not None
    assert planner.request.city == "上海"
    assert planner.request.area == "静安区"
    assert planner.request.budget == 600
    assert result.task_state.budget == 600
    assert result.task_state.status == DatePlanningStatus.PLANNED
    patch_trace = next(item for item in trace.snapshot() if item.name == "date_patch_apply")
    assert patch_trace.details["current_turn_fields"] == "budget"


@pytest.mark.asyncio
async def test_workflow_prefers_typed_operations_over_legacy_mutation() -> None:
    store = InMemoryDatePlanningTaskStore()
    executor = RecordingOperationExecutor()
    workflow = DatePlanningWorkflow(
        UnusedPlanner(),  # type: ignore[arg-type]
        store,
        PermissiveValidator(),  # type: ignore[arg-type]
        operation_executor=executor,  # type: ignore[arg-type]
    )
    request = ConversationRequest(
        user_id="workflow-user",
        relationship_id="workflow-relationship",
        conversation_id="workflow-operations",
        query="预算改为600元",
    )
    place = Place(
        id="existing-place",
        name="现有地点",
        city="上海",
        address="测试地址",
        category=PlaceCategory.ATTRACTION,
        estimated_cost_per_person=50,
        source="test",
    )
    current = DatePlanningTaskState(
        user_id=request.user_id,
        relationship_id=request.relationship_id,
        conversation_id=request.conversation_id,
        city="上海",
        date=date(2026, 8, 29),
        budget=300,
        current_plan=DatePlan(
            title="现有计划",
            summary="before",
            items=[
                DatePlanItem(
                    order=1,
                    place=place,
                    duration_minutes=60,
                    estimated_cost=50,
                    reason="test",
                )
            ],
            total_estimated_cost=50,
            total_duration_minutes=60,
            data_source="test",
        ),
    )
    operation = DatePlanOperation(
        type=DateOperationType.UPDATE_CONSTRAINT,
        constraint_field=DateConstraintField.BUDGET,
        constraint_value=600,
    )
    route = _route(intent=DateTaskIntent.SUPPLEMENT).model_copy(
        update={
            "date_patch": DatePlanPatch(
                budget=600,
                source_by_field={"budget": SlotSource.RULE},
            ),
            "date_operations": [operation],
            "date_mutation": DatePlanMutation.REPLACE,
        }
    )
    trace = ExecutionTrace()

    result = await workflow.run(
        DatePlanningWorkflowInput(
            request=request,
            route=route,
            current_task_state=current,
        ),
        trace=trace,
    )

    assert len(executor.calls) == 1
    assert executor.calls[0][2].budget == 600
    assert result.task_state.current_plan is not None
    assert result.task_state.current_plan.summary == "operation path"
    assert result.task_state.last_mutation == DatePlanMutation.UPDATE_CONSTRAINT
    operation_trace = next(
        item for item in trace.snapshot() if item.name == "date_operation_execute"
    )
    assert operation_trace.details["applied_count"] == 1


@pytest.mark.asyncio
async def test_workflow_does_not_persist_empty_plan_as_planned() -> None:
    store = InMemoryDatePlanningTaskStore()
    workflow = DatePlanningWorkflow(
        RecordingPlanner(empty=True),  # type: ignore[arg-type]
        store,
        PermissiveValidator(),  # type: ignore[arg-type]
    )
    request = ConversationRequest(
        user_id="workflow-user",
        relationship_id="workflow-relationship",
        conversation_id="workflow-empty-plan",
        query="安排一下",
    )
    current = DatePlanningTaskState(
        user_id=request.user_id,
        relationship_id=request.relationship_id,
        conversation_id=request.conversation_id,
        city="上海",
    )
    route = _route(intent=DateTaskIntent.SUPPLEMENT).model_copy(
        update={"date_patch": DatePlanPatch()}
    )

    result = await workflow.run(
        DatePlanningWorkflowInput(
            request=request,
            route=route,
            current_task_state=current,
        )
    )

    assert result.task_state.status == DatePlanningStatus.COLLECTING
    assert result.task_state.current_plan is None


@pytest.mark.asyncio
async def test_city_remains_blocking_after_an_earlier_clarification() -> None:
    store = InMemoryDatePlanningTaskStore()
    workflow = DatePlanningWorkflow(UnusedPlanner(), store)  # type: ignore[arg-type]
    request = ConversationRequest(
        user_id="workflow-user",
        relationship_id="workflow-relationship",
        conversation_id="workflow-repeat-city",
        query="预算300元",
    )
    current = DatePlanningTaskState(
        user_id=request.user_id,
        relationship_id=request.relationship_id,
        conversation_id=request.conversation_id,
        status=DatePlanningStatus.COLLECTING,
        clarification_round=1,
        asked_fields=["city"],
    )
    route = _route(intent=DateTaskIntent.SUPPLEMENT).model_copy(
        update={"date_patch": DatePlanPatch(budget=300)}
    )

    result = await workflow.run(
        DatePlanningWorkflowInput(
            request=request,
            route=route,
            current_task_state=current,
        )
    )

    assert result.needs_clarification is True
    assert result.task_state.status == DatePlanningStatus.COLLECTING
    assert result.task_state.clarification_round == 2

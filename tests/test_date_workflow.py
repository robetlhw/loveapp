import pytest

from loveapp.adapters.date_tasks import InMemoryDatePlanningTaskStore
from loveapp.agents.date_workflow import DatePlanningWorkflow
from loveapp.domain.conversation import ConversationRequest
from loveapp.domain.date_workflow import DatePlanningWorkflowInput
from loveapp.domain.enums import DateTaskIntent, TaskType
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

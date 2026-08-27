from loveapp.adapters.date_tasks import InMemoryDatePlanningTaskStore
from loveapp.application.runtime_context import RuntimeContextBuilder
from loveapp.domain.conversation import ConversationRequest
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.enums import DatePlanningStatus, TaskType


async def test_runtime_context_uses_committed_date_task_snapshot() -> None:
    store = InMemoryDatePlanningTaskStore()
    state = DatePlanningTaskState(
        user_id="runtime-user",
        relationship_id="runtime-relationship",
        conversation_id="runtime-conversation",
        status=DatePlanningStatus.PLANNED,
        city="上海",
        area="静安区",
        budget=300,
        plan_version=2,
    )
    await store.save(state)

    context = await RuntimeContextBuilder(store).build(
        ConversationRequest(
            user_id=state.user_id,
            relationship_id=state.relationship_id,
            conversation_id=state.conversation_id,
            query="预算改为600",
        ),
        active_task=TaskType.DATE_PLANNING,
    )

    assert context.active_task == TaskType.DATE_PLANNING
    assert context.active_date_plan is not None
    assert context.active_date_plan.status == DatePlanningStatus.PLANNED
    assert context.active_date_plan.city == "上海"
    assert context.active_date_plan.area == "静安区"
    assert context.active_date_plan.budget == 300
    assert context.active_date_plan.plan_version == 2


async def test_runtime_context_preserves_paused_task_for_resume() -> None:
    store = InMemoryDatePlanningTaskStore()
    state = DatePlanningTaskState(
        user_id="runtime-user",
        relationship_id="runtime-relationship",
        conversation_id="paused-runtime-conversation",
        status=DatePlanningStatus.PAUSED,
        city="上海",
    )
    await store.save(state)

    context = await RuntimeContextBuilder(store).build(
        ConversationRequest(
            user_id=state.user_id,
            relationship_id=state.relationship_id,
            conversation_id=state.conversation_id,
            query="继续",
        )
    )

    assert context.active_date_plan is not None
    assert context.active_date_plan.status == DatePlanningStatus.PAUSED

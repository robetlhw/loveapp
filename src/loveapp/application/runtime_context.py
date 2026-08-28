from loveapp.core.timing import ExecutionTrace
from loveapp.domain.conversation import ConversationRequest
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.enums import TaskType
from loveapp.domain.memory import utc_now
from loveapp.domain.runtime_context import DatePlanRuntimeContext, RuntimeContext
from loveapp.ports.date_tasks import DatePlanningTaskStore


class RuntimeContextBuilder:
    """Build a per-turn context from committed task state."""

    def __init__(self, date_task_store: DatePlanningTaskStore) -> None:
        self._date_task_store = date_task_store

    async def build(
        self,
        request: ConversationRequest,
        *,
        active_task: TaskType | None = None,
        date_task_state: DatePlanningTaskState | None = None,
        trace: ExecutionTrace | None = None,
    ) -> RuntimeContext:
        if request.conversation_id is None:
            raise ValueError("conversation_id is required for runtime context")
        state = date_task_state
        if state is None:
            state = await self._date_task_store.get(
                user_id=request.user_id,
                relationship_id=request.relationship_id,
                conversation_id=request.conversation_id,
            )
        context = RuntimeContext(
            user_id=request.user_id,
            relationship_id=request.relationship_id,
            conversation_id=request.conversation_id,
            relationship_stage=request.relationship_stage,
            active_task=active_task,
            active_date_plan=(
                _date_plan_context(state)
                if state is not None and state.is_resumable
                else None
            ),
            now=utc_now(),
        )
        if trace is not None:
            with trace.measure("runtime_context_build") as details:
                details.update(
                    {
                        "active_task": active_task.value if active_task else None,
                        "date_status": (
                            state.status.value
                            if state is not None
                            else None
                        ),
                        "date_plan_version": state.plan_version if state is not None else None,
                        "has_current_plan": bool(
                            state is not None
                            and state.current_plan is not None
                            and state.current_plan.items
                        ),
                    }
                )
        return context


def _date_plan_context(state: DatePlanningTaskState) -> DatePlanRuntimeContext:
    return DatePlanRuntimeContext(
        status=state.status,
        city=state.city,
        area=state.area,
        plan_mode=state.plan_mode,
        date=state.date,
        end_date=state.end_date,
        day_count=state.day_count,
        budget=state.budget,
        budget_scope=state.budget_scope,
        transport_mode=state.transport_mode,
        requirements=list(state.requirements),
        requirement_satisfaction=list(state.requirement_satisfaction),
        current_plan=state.current_plan,
        plan_version=state.plan_version,
        missing_fields=list(state.missing_fields),
    )

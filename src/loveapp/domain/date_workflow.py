from pydantic import BaseModel

from loveapp.domain.conversation import ConversationRequest
from loveapp.domain.date_plan import DatePlan
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.routing import RouteResult
from loveapp.domain.runtime_context import RuntimeContext


class DatePlanningWorkflowInput(BaseModel):
    """The DatePlan workflow boundary, independent of CLI and graph state."""

    request: ConversationRequest
    route: RouteResult
    current_task_state: DatePlanningTaskState | None = None
    runtime_context: RuntimeContext | None = None


class DatePlanningWorkflowResult(BaseModel):
    message: str
    task_state: DatePlanningTaskState
    plan: DatePlan | None = None
    needs_clarification: bool = False
    cancelled: bool = False
    plan_changed: bool = False

from pydantic import BaseModel, Field

from loveapp.domain.advice import AdviceResponse
from loveapp.domain.date_plan import DatePlan
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.enums import RelationshipStage, TaskType
from loveapp.domain.memory import RememberResult
from loveapp.domain.observability import StepTiming
from loveapp.domain.routing import RouteResult


class ConversationRequest(BaseModel):
    user_id: str = "local-user"
    relationship_id: str = "primary"
    conversation_id: str | None = None
    query: str = Field(min_length=1, max_length=4000)
    relationship_stage: RelationshipStage = RelationshipStage.UNKNOWN
    active_task: TaskType | None = None


class ConversationTurnResult(BaseModel):
    conversation_id: str
    route: RouteResult
    active_task: TaskType | None = None
    message: str | None = None
    advice: AdviceResponse | None = None
    date_plan: DatePlan | None = None
    date_task_state: DatePlanningTaskState | None = None
    memory_result: RememberResult | None = None
    timings: list[StepTiming] = Field(default_factory=list)

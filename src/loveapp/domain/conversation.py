from datetime import datetime

from pydantic import BaseModel, Field

from loveapp.domain.advice import AdviceResponse
from loveapp.domain.date_plan import DatePlan
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.enums import RelationshipStage, TaskType
from loveapp.domain.memory import RememberResult, utc_now
from loveapp.domain.observability import StepTiming
from loveapp.domain.routing import RecentRiskState, RouteResult


class ConversationFlowState(BaseModel):
    """Short-lived routing workflow state scoped to one conversation."""

    user_id: str
    relationship_id: str
    conversation_id: str
    active_task: TaskType | None = None
    pending_task: TaskType | None = None
    pending_task_reason: str | None = Field(default=None, max_length=300)
    pending_task_source: str | None = Field(default=None, max_length=80)
    pending_task_turns_remaining: int = Field(default=0, ge=0, le=4)
    last_clarification_reason: str | None = Field(default=None, max_length=300)
    clarification_attempt_count: int = Field(default=0, ge=0, le=3)
    recent_risk_state: RecentRiskState | None = None
    updated_at: datetime = Field(default_factory=utc_now)


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
    pending_task: TaskType | None = None
    pending_task_reason: str | None = None
    follow_up_prompt: str | None = None
    message: str | None = None
    advice: AdviceResponse | None = None
    date_plan: DatePlan | None = None
    date_task_state: DatePlanningTaskState | None = None
    memory_result: RememberResult | None = None
    timings: list[StepTiming] = Field(default_factory=list)

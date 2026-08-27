from datetime import date as Date
from datetime import datetime

from pydantic import BaseModel, Field

from loveapp.domain.date_plan import DatePlan
from loveapp.domain.enums import (
    BudgetScope,
    DatePlanMode,
    DatePlanningStatus,
    RelationshipStage,
    TaskType,
    TransportMode,
)


class DatePlanRuntimeContext(BaseModel):
    """Read-only committed date-planning state exposed to a single turn."""

    status: DatePlanningStatus | None = None
    city: str | None = None
    area: str | None = None
    plan_mode: DatePlanMode | None = None
    date: Date | None = None
    end_date: Date | None = None
    day_count: int | None = None
    budget: int | None = None
    budget_scope: BudgetScope | None = None
    transport_mode: TransportMode | None = None
    current_plan: DatePlan | None = None
    plan_version: int = Field(default=0, ge=0)
    missing_fields: list[str] = Field(default_factory=list)


class RuntimeContext(BaseModel):
    """Trusted state snapshot available to routing and workflow code.

    The snapshot deliberately does not contain current-turn changes. Those are
    represented separately by ``DatePlanPatch`` and applied by the workflow.
    """

    user_id: str
    relationship_id: str
    conversation_id: str
    relationship_stage: RelationshipStage
    active_task: TaskType | None = None
    active_date_plan: DatePlanRuntimeContext | None = None
    timezone: str = "Asia/Shanghai"
    now: datetime

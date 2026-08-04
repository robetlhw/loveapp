from datetime import date as Date
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from loveapp.domain.date_plan import MAX_TRIP_DAYS
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.enums import (
    AdviceGoal,
    AdviceScenario,
    BudgetScope,
    DatePlanMode,
    DatePlanMutation,
    DateRequestMode,
    DateTaskIntent,
    RiskLevel,
    RouteSource,
    TaskType,
    TransportMode,
)
from loveapp.domain.memory import StoredMessage


class DatePlanSlots(BaseModel):
    city: str | None = None
    area: str | None = None
    plan_mode: DatePlanMode | None = None
    date: Date | None = None
    end_date: Date | None = None
    day_count: int | None = Field(default=None, ge=1, le=MAX_TRIP_DAYS)
    nights: int | None = Field(default=None, ge=0, le=MAX_TRIP_DAYS - 1)
    target_day: int | None = Field(default=None, ge=1, le=MAX_TRIP_DAYS)
    start_time: datetime | None = None
    budget: int | None = Field(default=None, gt=0)
    budget_scope: BudgetScope | None = None
    preferences: list[str] = Field(default_factory=list)
    dining_keywords: list[str] = Field(default_factory=list, max_length=8)
    meal_keywords: dict[str, list[str]] = Field(default_factory=dict)
    activity_keywords: list[str] = Field(default_factory=list, max_length=8)
    schedule_hints: list[str] = Field(default_factory=list, max_length=8)
    replace_place_names: list[str] = Field(default_factory=list, max_length=8)
    excluded_keywords: list[str] = Field(default_factory=list, max_length=8)
    transport_mode: TransportMode | None = None
    notes: list[str] = Field(default_factory=list, max_length=8)
    constraints: list[str] = Field(default_factory=list, max_length=8)
    lodging_notes: list[str] = Field(default_factory=list, max_length=8)


class RouteInput(BaseModel):
    latest_query: str = Field(min_length=1, max_length=4000)
    recent_messages: list[StoredMessage] = Field(default_factory=list, max_length=20)
    active_task: TaskType | None = None
    forced_task: TaskType | None = None
    date_task_state: DatePlanningTaskState | None = None


class RouteCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: TaskType
    secondary_tasks: list[TaskType] = Field(default_factory=list, max_length=2)
    task_confidence: float = Field(ge=0, le=1)
    primary_goal: AdviceGoal | None = None
    secondary_goals: list[AdviceGoal] = Field(default_factory=list, max_length=2)
    primary_scenario: AdviceScenario | None = None
    secondary_scenarios: list[AdviceScenario] = Field(default_factory=list, max_length=2)
    scenario_confidence: float | None = Field(default=None, ge=0, le=1)
    needs_clarification: bool = False
    evidence_spans: list[str] = Field(default_factory=list, max_length=8)
    date_plan: DatePlanSlots = Field(default_factory=DatePlanSlots)
    date_request_mode: DateRequestMode = DateRequestMode.NONE
    date_intent: DateTaskIntent = DateTaskIntent.NONE
    date_mutation: DatePlanMutation = DatePlanMutation.NONE


class RouteResult(BaseModel):
    normalized_query: str
    task_type: TaskType
    secondary_tasks: list[TaskType] = Field(default_factory=list, max_length=2)
    task_confidence: float = Field(ge=0, le=1)
    task_scores: dict[TaskType, float] = Field(default_factory=dict)
    # Diagnostic fields distinguish rule routing from LLM correction.
    rule_task_type: TaskType | None = None
    llm_task_type: TaskType | None = None
    task_guard_applied: bool = False

    risk_level: RiskLevel = RiskLevel.NORMAL
    risk_reasons: list[str] = Field(default_factory=list)

    primary_goal: AdviceGoal | None = None
    secondary_goals: list[AdviceGoal] = Field(default_factory=list, max_length=2)
    goal_scores: dict[AdviceGoal, float] = Field(default_factory=dict)
    primary_scenario: AdviceScenario | None = None
    secondary_scenarios: list[AdviceScenario] = Field(default_factory=list, max_length=2)
    scenario_confidence: float | None = Field(default=None, ge=0, le=1)
    scenario_scores: dict[AdviceScenario, float] = Field(default_factory=dict)

    date_plan: DatePlanSlots = Field(default_factory=DatePlanSlots)
    date_request_mode: DateRequestMode = DateRequestMode.NONE
    date_intent: DateTaskIntent = DateTaskIntent.NONE
    date_mutation: DatePlanMutation = DatePlanMutation.NONE
    date_missing_fields: list[str] = Field(default_factory=list, max_length=8)
    source: RouteSource = RouteSource.RULES
    llm_used: bool = False
    llm_error: str | None = None
    needs_clarification: bool = False
    evidence_spans: list[str] = Field(default_factory=list, max_length=12)

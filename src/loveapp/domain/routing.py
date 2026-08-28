from datetime import date as Date
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from loveapp.domain.date_operations import DatePlanOperation
from loveapp.domain.date_patch import DatePlanPatch
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
from loveapp.domain.runtime_context import RuntimeContext


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


class RecentRiskState(BaseModel):
    level: RiskLevel
    reasons: list[str] = Field(default_factory=list, max_length=8)
    expires_after_turns: int = Field(default=2, ge=0, le=4)


class DateMutationPolicy(BaseModel):
    preserve_unmentioned_items: bool = False


class RouteInput(BaseModel):
    latest_query: str = Field(min_length=1, max_length=4000)
    recent_messages: list[StoredMessage] = Field(default_factory=list, max_length=20)
    active_task: TaskType | None = None
    forced_task: TaskType | None = None
    date_task_state: DatePlanningTaskState | None = None
    runtime_context: RuntimeContext | None = None
    pending_task: TaskType | None = None
    pending_task_reason: str | None = Field(default=None, max_length=300)
    pending_task_turns_remaining: int = Field(default=0, ge=0, le=4)
    last_clarification_reason: str | None = Field(default=None, max_length=300)
    clarification_attempt_count: int = Field(default=0, ge=0, le=3)
    previous_risk_state: RecentRiskState | None = None


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
    date_patch: DatePlanPatch | None = None
    date_request_mode: DateRequestMode = DateRequestMode.NONE
    date_intent: DateTaskIntent = DateTaskIntent.NONE
    date_mutation: DatePlanMutation = DatePlanMutation.NONE
    date_operations: list[DatePlanOperation] = Field(default_factory=list, max_length=12)


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
    date_patch: DatePlanPatch | None = None
    date_request_mode: DateRequestMode = DateRequestMode.NONE
    date_intent: DateTaskIntent = DateTaskIntent.NONE
    date_mutation: DatePlanMutation = DatePlanMutation.NONE
    date_mutation_policy: DateMutationPolicy = Field(default_factory=DateMutationPolicy)
    date_operations: list[DatePlanOperation] = Field(default_factory=list, max_length=12)
    date_operation_candidate_count: int = Field(default=0, ge=0)
    date_operation_dedupe_input_count: int = Field(default=0, ge=0)
    date_operation_rejections: list[str] = Field(default_factory=list, max_length=24)
    date_clause_count: int = Field(default=0, ge=0)
    date_semantic_parse_required: bool = False
    date_semantic_parse_reason: str | None = None
    date_semantic_trigger_reasons: list[str] = Field(default_factory=list, max_length=16)
    date_semantic_llm_used: bool = False
    date_semantic_model: str | None = None
    date_semantic_thinking: str | None = None
    date_semantic_prompt_version: str | None = None
    date_semantic_input_tokens: int | None = Field(default=None, ge=0)
    date_semantic_output_tokens: int | None = Field(default=None, ge=0)
    date_semantic_duration_ms: float | None = Field(default=None, ge=0)
    date_semantic_fallback_reason: str | None = None
    date_semantic_error: str | None = None
    date_semantic_validation_error_path: str | None = None
    date_semantic_invalid_field: str | None = None
    date_semantic_raw_operation_type: str | None = None
    date_unresolved_references: list[str] = Field(default_factory=list, max_length=12)
    date_missing_fields: list[str] = Field(default_factory=list, max_length=8)
    source: RouteSource = RouteSource.RULES
    llm_used: bool = False
    router_llm_used: bool = False
    llm_error: str | None = None
    needs_clarification: bool = False
    evidence_spans: list[str] = Field(default_factory=list, max_length=12)
    clarification_triggered: bool = False
    clarification_exhausted: bool = False
    clarification_options: list[str] = Field(default_factory=list, max_length=3)
    clarification_reason: str | None = None
    out_of_scope_reason: str | None = None
    pending_task: TaskType | None = None
    pending_task_reason: str | None = None
    pending_task_source: str | None = None
    pending_task_turns_remaining: int = Field(default=0, ge=0, le=4)
    pending_task_cancelled: bool = False
    slot_accepted_fields: dict[str, str] = Field(default_factory=dict)
    slot_rejected_fields: dict[str, str] = Field(default_factory=dict)
    slot_field_sources: dict[str, str] = Field(default_factory=dict)
    recent_risk_inherited: bool = False
    recent_risk_deescalated: bool = False
    router_prompt_version: str | None = None
    router_model: str | None = None
    router_input_tokens: int | None = Field(default=None, ge=0)
    router_output_tokens: int | None = Field(default=None, ge=0)
    router_duration_ms: float | None = Field(default=None, ge=0)
    fallback_reason: str | None = None

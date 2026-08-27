from datetime import date as Date
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from loveapp.domain.date_operations import (
    DatePlanOperation,
    DateRequirementMatch,
    DateStopRequirement,
    DesiredDateStop,
    desired_stops_from_legacy_slots,
    deterministic_requirement_id,
)
from loveapp.domain.date_plan import MAX_TRIP_DAYS, DatePlan
from loveapp.domain.enums import (
    BudgetScope,
    DatePlanMode,
    DatePlanMutation,
    DatePlanningStatus,
    TransportMode,
)
from loveapp.domain.memory import utc_now
from loveapp.domain.weather import WeatherForecast


class DateTaskFieldChange(BaseModel):
    before: Any = None
    after: Any = None


class DateTaskDiff(BaseModel):
    changes: dict[str, DateTaskFieldChange] = Field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.changes)

    @property
    def changed_fields(self) -> list[str]:
        return list(self.changes)


class DatePlanningTaskState(BaseModel):
    """Short-lived workflow state for one date-planning task.

    This is deliberately separate from relationship memory.  A budget or a
    proposed date is useful while planning, but should not become a permanent
    fact about the user or their relationship.
    """

    user_id: str
    relationship_id: str
    conversation_id: str
    status: DatePlanningStatus = DatePlanningStatus.COLLECTING
    city: str | None = None
    area: str | None = None
    plan_mode: DatePlanMode = DatePlanMode.SINGLE_DAY
    date: Date | None = None
    end_date: Date | None = None
    day_count: int | None = Field(default=1, ge=1, le=MAX_TRIP_DAYS)
    nights: int | None = Field(default=0, ge=0, le=MAX_TRIP_DAYS - 1)
    target_day: int | None = Field(default=None, ge=1, le=MAX_TRIP_DAYS)
    start_time: datetime | None = None
    budget: int | None = Field(default=None, gt=0)
    budget_scope: BudgetScope = BudgetScope.TOTAL
    preferences: list[str] = Field(default_factory=list)
    dining_keywords: list[str] = Field(default_factory=list, max_length=8)
    meal_keywords: dict[str, list[str]] = Field(default_factory=dict)
    activity_keywords: list[str] = Field(default_factory=list, max_length=8)
    schedule_hints: list[str] = Field(default_factory=list, max_length=8)
    # Canonical stop-level user intent. All fields below are compatibility projections.
    requirement_schema_version: int = Field(default=0, ge=0, le=1)
    requirements: list[DateStopRequirement] = Field(default_factory=list, max_length=16)
    desired_stops: list[DesiredDateStop] = Field(default_factory=list, max_length=16)
    requirement_satisfaction: list[DateRequirementMatch] = Field(
        default_factory=list,
        max_length=16,
    )
    excluded_keywords: list[str] = Field(default_factory=list, max_length=8)
    transport_mode: TransportMode | None = None
    notes: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    lodging_notes: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    asked_fields: list[str] = Field(default_factory=list)
    clarification_round: int = Field(default=0, ge=0)
    fallback_used: bool = False
    weather: WeatherForecast | None = None
    weather_forecasts: list[WeatherForecast] = Field(
        default_factory=list,
        max_length=MAX_TRIP_DAYS,
    )
    # The slots above describe constraints.  The current plan is a separate
    # persisted snapshot so a follow-up can edit it instead of regenerating it.
    current_plan: DatePlan | None = None
    plan_version: int = Field(default=0, ge=0)
    locked_item_ids: list[str] = Field(default_factory=list, max_length=32)
    last_mutation: DatePlanMutation = DatePlanMutation.NONE
    last_operations: list[DatePlanOperation] = Field(default_factory=list, max_length=12)
    last_task_diff: DateTaskDiff | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def is_active(self) -> bool:
        return self.status in {
            DatePlanningStatus.COLLECTING,
            DatePlanningStatus.PLANNED,
        }

    @property
    def is_resumable(self) -> bool:
        return self.is_active or self.status == DatePlanningStatus.PAUSED

    @model_validator(mode="before")
    @classmethod
    def mark_explicit_canonical_requirements(cls, value: Any) -> Any:
        if isinstance(value, dict) and "requirements" in value:
            value = dict(value)
            value.setdefault("requirement_schema_version", 1)
        return value

    @model_validator(mode="after")
    def migrate_canonical_requirements(self) -> "DatePlanningTaskState":
        # Old SQLite snapshots persisted desired_stops or only legacy keyword slots.
        if not self.requirements and self.requirement_schema_version == 0:
            stops = list(self.desired_stops) or desired_stops_from_legacy_slots(
                dining_keywords=self.dining_keywords,
                meal_keywords=self.meal_keywords,
                activity_keywords=self.activity_keywords,
                schedule_hints=self.schedule_hints,
                target_day=self.target_day,
            )
            self.requirements = [
                DateStopRequirement(
                    id=deterministic_requirement_id([stop]),
                    alternatives=[stop],
                )
                for stop in stops
            ]
            self.requirement_schema_version = 1
        if self.requirements:
            self.desired_stops = [
                requirement.alternatives[0] for requirement in self.requirements
            ]
            self.requirement_schema_version = 1
        elif self.requirement_schema_version == 1:
            self.desired_stops = []
        return self

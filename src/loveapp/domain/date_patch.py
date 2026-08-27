from datetime import date as Date
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from loveapp.domain.date_plan import MAX_TRIP_DAYS
from loveapp.domain.enums import BudgetScope, DatePlanMode, TransportMode


class SlotSource(StrEnum):
    RULE = "rule"
    LLM_VERIFIED = "llm_verified"


class DatePlanPatch(BaseModel):
    """Current-turn date changes, without committed task-state values."""

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
    source_by_field: dict[str, SlotSource] = Field(default_factory=dict)

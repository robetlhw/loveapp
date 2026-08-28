from datetime import date as Date
from datetime import datetime, timedelta

from pydantic import BaseModel, Field, model_validator

from loveapp.domain.date_constants import MAX_TRIP_DAYS
from loveapp.domain.date_operations import DateStopRequirement
from loveapp.domain.enums import (
    BudgetScope,
    DatePlanMode,
    PlaceCategory,
    RelationshipStage,
    TransportMode,
)
from loveapp.domain.weather import WeatherForecast


class DatePlanRequest(BaseModel):
    user_id: str = "local-user"
    relationship_id: str = "primary"
    city: str | None = Field(default=None, min_length=1)
    area: str | None = None
    plan_mode: DatePlanMode = DatePlanMode.SINGLE_DAY
    date: Date | None = None
    end_date: Date | None = None
    day_count: int = Field(default=1, ge=1, le=MAX_TRIP_DAYS)
    nights: int = Field(default=0, ge=0, le=MAX_TRIP_DAYS - 1)
    target_day: int | None = Field(default=None, ge=1, le=MAX_TRIP_DAYS)
    start_time: datetime | None = None
    budget: int = Field(default=500, gt=0)
    budget_scope: BudgetScope = BudgetScope.TOTAL
    budget_is_assumed: bool = False
    preferences: list[str] = Field(default_factory=list)
    dining_keywords: list[str] = Field(default_factory=list, max_length=8)
    meal_keywords: dict[str, list[str]] = Field(default_factory=dict)
    activity_keywords: list[str] = Field(default_factory=list, max_length=8)
    schedule_hints: list[str] = Field(default_factory=list, max_length=8)
    requirements: list[DateStopRequirement] = Field(default_factory=list, max_length=16)
    replace_place_names: list[str] = Field(default_factory=list, max_length=8)
    excluded_keywords: list[str] = Field(default_factory=list, max_length=8)
    transport_mode: TransportMode = TransportMode.TRANSIT
    notes: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    lodging_notes: list[str] = Field(default_factory=list)
    weather: WeatherForecast | None = None
    weather_forecasts: list[WeatherForecast] = Field(
        default_factory=list,
        max_length=MAX_TRIP_DAYS,
    )
    relationship_stage: RelationshipStage = RelationshipStage.UNKNOWN

    @model_validator(mode="after")
    def normalize_trip_window(self) -> "DatePlanRequest":
        if self.end_date is not None and self.date is None:
            raise ValueError("date is required when end_date is provided")
        if self.date is not None and self.end_date is not None:
            if self.end_date < self.date:
                raise ValueError("end_date cannot be earlier than date")
            span_days = (self.end_date - self.date).days + 1
            if span_days > MAX_TRIP_DAYS:
                raise ValueError(f"multi-day plans currently support at most {MAX_TRIP_DAYS} days")
            self.day_count = span_days
        elif self.date is not None and self.day_count > 1:
            self.end_date = self.date + timedelta(days=self.day_count - 1)

        if self.target_day is not None and self.target_day > self.day_count:
            self.day_count = self.target_day
            if self.date is not None:
                self.end_date = self.date + timedelta(days=self.day_count - 1)

        if self.day_count > 1:
            self.plan_mode = DatePlanMode.MULTI_DAY
            if self.nights == 0:
                self.nights = self.day_count - 1
        else:
            self.plan_mode = DatePlanMode.SINGLE_DAY
            self.nights = 0
        return self

    @property
    def effective_total_budget(self) -> int:
        if self.budget_scope == BudgetScope.PER_DAY:
            return self.budget * self.day_count
        return self.budget

    @property
    def effective_daily_budget(self) -> int:
        if self.budget_scope == BudgetScope.PER_DAY:
            return self.budget
        return max(self.budget // self.day_count, 1)


class PlaceSearchRequest(BaseModel):
    city: str = Field(min_length=1)
    area: str | None = None
    category: PlaceCategory
    preferences: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list, max_length=8)
    required_keywords: list[str] = Field(default_factory=list, max_length=8)
    excluded_keywords: list[str] = Field(default_factory=list, max_length=8)
    strict_area: bool = True
    max_cost_per_person: int | None = Field(default=None, gt=0)
    require_verified_cost: bool = False
    min_rating: float | None = Field(default=None, ge=0, le=5)


class Place(BaseModel):
    id: str
    name: str
    city: str
    address: str
    category: PlaceCategory
    tags: list[str] = Field(default_factory=list)
    matched_preferences: list[str] = Field(default_factory=list)
    estimated_cost_per_person: int = Field(ge=0)
    cost_is_estimate: bool = True
    rating: float | None = Field(default=None, ge=0, le=5)
    type_name: str | None = None
    type_code: str | None = None
    business_area: str | None = None
    district: str | None = None
    adcode: str | None = None
    citycode: str | None = None
    opening_hours: str | None = None
    telephone: str | None = None
    map_url: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    source: str
    # Search provenance is useful when one request contains several independent
    # venue constraints (for example, a cinema and a scenic spot).
    search_keywords: list[str] = Field(default_factory=list)


class Route(BaseModel):
    origin_id: str
    destination_id: str
    mode: TransportMode
    duration_minutes: int = Field(ge=0)
    distance_meters: int = Field(ge=0)
    estimated_cost: float | None = Field(default=None, ge=0)
    source: str


class DatePlanItem(BaseModel):
    order: int = Field(ge=1)
    place: Place
    duration_minutes: int = Field(gt=0)
    estimated_cost: int = Field(ge=0)
    reason: str
    day_index: int = Field(default=1, ge=1, le=MAX_TRIP_DAYS)
    scheduled_date: Date | None = None
    route_from_previous: Route | None = None
    meal_type: str | None = None
    time_label: str | None = None
    after_item: str | None = None
    slot_keyword: str | None = None


class DatePlanDay(BaseModel):
    day_index: int = Field(ge=1, le=MAX_TRIP_DAYS)
    date: Date | None = None
    items: list[DatePlanItem] = Field(default_factory=list)
    total_estimated_cost: int = Field(default=0, ge=0)
    total_duration_minutes: int = Field(default=0, ge=0)
    weather: WeatherForecast | None = None
    lodging_notes: list[str] = Field(default_factory=list)


class DatePlan(BaseModel):
    title: str
    summary: str
    plan_mode: DatePlanMode = DatePlanMode.SINGLE_DAY
    start_date: Date | None = None
    end_date: Date | None = None
    day_count: int = Field(default=1, ge=1, le=MAX_TRIP_DAYS)
    nights: int = Field(default=0, ge=0, le=MAX_TRIP_DAYS - 1)
    days: list[DatePlanDay] = Field(default_factory=list, max_length=MAX_TRIP_DAYS)
    items: list[DatePlanItem] = Field(default_factory=list)
    alternatives: list[Place] = Field(default_factory=list)
    total_estimated_cost: int = Field(ge=0)
    total_duration_minutes: int = Field(ge=0)
    notes: list[str] = Field(default_factory=list)
    weather: WeatherForecast | None = None
    data_source: str

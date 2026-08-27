from datetime import date, datetime, time
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from loveapp.domain.date_plan import MAX_TRIP_DAYS
from loveapp.domain.enums import BudgetScope, DatePlanMode, TransportMode


class DateOperationType(StrEnum):
    UPDATE_CONSTRAINT = "update_constraint"
    ADD_STOP = "add_stop"
    REMOVE_STOP = "remove_stop"
    REPLACE_STOP = "replace_stop"
    MOVE_STOP = "move_stop"
    REPLAN = "replan"


class DateConstraintField(StrEnum):
    BUDGET = "budget"
    BUDGET_SCOPE = "budget_scope"
    CITY = "city"
    AREA = "area"
    DATE = "date"
    END_DATE = "end_date"
    START_TIME = "start_time"
    DAY_COUNT = "day_count"
    TRANSPORT_MODE = "transport_mode"
    PLAN_MODE = "plan_mode"


class StopKind(StrEnum):
    DINING = "dining"
    ACTIVITY = "activity"
    CAFE = "cafe"
    OTHER = "other"


class MealType(StrEnum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"


class TemporalAnchor(StrEnum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    AFTERNOON = "afternoon"
    DINNER = "dinner"
    AFTER_DINNER = "after_dinner"
    EVENING = "evening"


class TimeWindow(BaseModel):
    start: time | None = None
    end: time | None = None
    label: str | None = Field(default=None, min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_window(self) -> "TimeWindow":
        if self.start is None and self.end is None and self.label is None:
            raise ValueError("a time window requires a boundary or label")
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("time window end cannot be earlier than start")
        return self


class StopReference(BaseModel):
    place_id: str | None = Field(default=None, min_length=1, max_length=160)
    place_name: str | None = Field(default=None, min_length=1, max_length=200)
    keyword: str | None = Field(default=None, min_length=1, max_length=120)
    meal_type: MealType | None = None
    ordinal: int | None = Field(default=None, ge=1, le=64)

    @model_validator(mode="after")
    def validate_reference(self) -> "StopReference":
        if not any(
            value is not None
            for value in (
                self.place_id,
                self.place_name,
                self.keyword,
                self.meal_type,
                self.ordinal,
            )
        ):
            raise ValueError("a stop reference requires an identifier")
        return self


type TemporalReference = TemporalAnchor | StopReference


class DesiredDateStop(BaseModel):
    kind: StopKind
    keyword: str | None = Field(default=None, min_length=1, max_length=120)
    place_name: str | None = Field(default=None, min_length=1, max_length=200)
    meal_type: MealType | None = None
    target_day: int | None = Field(default=None, ge=1, le=MAX_TRIP_DAYS)
    time_window: TimeWindow | None = None
    after: TemporalReference | None = None
    before: TemporalReference | None = None

    @model_validator(mode="after")
    def validate_stop(self) -> "DesiredDateStop":
        if self.keyword is None and self.place_name is None and self.meal_type is None:
            raise ValueError("a desired stop requires a keyword, place name, or meal type")
        if self.after is not None and self.before is not None and self.after == self.before:
            raise ValueError("a desired stop cannot be before and after the same anchor")
        return self


type DateConstraintValue = int | str | date | datetime | BudgetScope | DatePlanMode | TransportMode


class DatePlanOperation(BaseModel):
    type: DateOperationType
    target: StopReference | None = None
    payload: DesiredDateStop | None = None
    constraint_field: DateConstraintField | None = None
    constraint_value: DateConstraintValue | None = None
    source_span: str | None = Field(default=None, min_length=1, max_length=1000)
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_operation_shape(self) -> "DatePlanOperation":
        if self.type == DateOperationType.UPDATE_CONSTRAINT:
            if self.constraint_field is None or self.constraint_value is None:
                raise ValueError("constraint updates require a field and value")
        elif self.type == DateOperationType.ADD_STOP:
            if self.payload is None:
                raise ValueError("add-stop operations require a payload")
        elif self.type == DateOperationType.REMOVE_STOP:
            if self.target is None:
                raise ValueError("remove-stop operations require a target")
        elif self.type == DateOperationType.REPLACE_STOP:
            if self.target is None or self.payload is None:
                raise ValueError("replace-stop operations require a target and payload")
        elif self.type == DateOperationType.MOVE_STOP and (
            self.target is None or self.payload is None
        ):
            raise ValueError("move-stop operations require a target and desired placement")
        return self


class DateSemanticParseResult(BaseModel):
    operations: list[DatePlanOperation] = Field(default_factory=list, max_length=12)

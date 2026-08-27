import json
from datetime import date, datetime, time
from enum import StrEnum
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel, Field, model_validator

from loveapp.domain.date_constants import MAX_TRIP_DAYS
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


class DateReplacementPreference(StrEnum):
    NEARBY = "nearby"


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
    generic_replacement: bool = False
    replacement_preferences: list[DateReplacementPreference] = Field(
        default_factory=list,
        max_length=4,
    )
    target_day: int | None = Field(default=None, ge=1, le=MAX_TRIP_DAYS)
    time_window: TimeWindow | None = None
    after: TemporalReference | None = None
    before: TemporalReference | None = None

    @model_validator(mode="after")
    def validate_stop(self) -> "DesiredDateStop":
        if (
            self.keyword is None
            and self.place_name is None
            and self.meal_type is None
            and not self.generic_replacement
        ):
            raise ValueError("a desired stop requires a keyword, place name, or meal type")
        if self.after is not None and self.before is not None and self.after == self.before:
            raise ValueError("a desired stop cannot be before and after the same anchor")
        label = self.time_window.label if self.time_window is not None else None
        if label is not None and isinstance(self.after, TemporalAnchor):
            if self.after == TemporalAnchor.LUNCH and any(
                marker in label for marker in ("晚饭", "晚餐")
            ):
                raise ValueError("time window conflicts with the lunch anchor")
            if self.after in {TemporalAnchor.DINNER, TemporalAnchor.AFTER_DINNER} and any(
                marker in label for marker in ("午饭", "午餐")
            ):
                raise ValueError("time window conflicts with the dinner anchor")
        return self


class RequirementStatus(StrEnum):
    PENDING = "pending"
    FULFILLED = "fulfilled"
    UNSATISFIED = "unsatisfied"
    AMBIGUOUS = "ambiguous"


class DateStopRequirement(BaseModel):
    """A durable user requirement, independent from any selected plan item."""

    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=64)
    alternatives: list[DesiredDateStop] = Field(min_length=1, max_length=8)
    min_satisfied: int = Field(default=1, ge=1, le=8)
    max_satisfied: int | None = Field(default=1, ge=1, le=8)
    source_span: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_cardinality(self) -> "DateStopRequirement":
        if self.min_satisfied > len(self.alternatives):
            raise ValueError("minimum satisfaction cannot exceed alternative count")
        if self.max_satisfied is not None:
            if self.max_satisfied < self.min_satisfied:
                raise ValueError("maximum satisfaction cannot be below minimum satisfaction")
            if self.max_satisfied > len(self.alternatives):
                raise ValueError("maximum satisfaction cannot exceed alternative count")
        kind_families = {
            "dining" if alternative.kind in {StopKind.DINING, StopKind.CAFE} else alternative.kind
            for alternative in self.alternatives
        }
        if len(kind_families) != 1:
            raise ValueError("requirement alternatives must share one stop-kind family")
        return self


def deterministic_requirement_id(alternatives: list[DesiredDateStop]) -> str:
    signature = json.dumps(
        [stop.model_dump(mode="json") for stop in alternatives],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return uuid5(NAMESPACE_URL, f"loveapp:date-requirement:{signature}").hex


def desired_stops_from_legacy_slots(
    *,
    dining_keywords: list[str],
    meal_keywords: dict[str, list[str]],
    activity_keywords: list[str],
    schedule_hints: list[str],
    target_day: int | None = None,
) -> list[DesiredDateStop]:
    """One-time compatibility migration for pre-requirement task snapshots."""

    meal_by_keyword = {
        keyword: MealType(meal_type)
        for meal_type, keywords in meal_keywords.items()
        if meal_type in MealType._value2member_map_
        for keyword in keywords
    }
    result = [
        DesiredDateStop(
            kind=StopKind.CAFE if "咖啡" in keyword else StopKind.DINING,
            keyword=keyword,
            meal_type=meal_by_keyword.get(keyword),
            target_day=target_day,
        )
        for keyword in dining_keywords
    ]
    unique_after_dinner = len(activity_keywords) == 1 and any(
        marker in schedule_hints for marker in ("晚饭后", "晚餐后")
    )
    result.extend(
        DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword=keyword,
            target_day=target_day,
            time_window=TimeWindow(label="晚饭后") if unique_after_dinner else None,
            after=TemporalAnchor.DINNER if unique_after_dinner else None,
        )
        for keyword in activity_keywords
    )
    return result


class DateRequirementMatch(BaseModel):
    requirement_id: str
    status: RequirementStatus
    matched_place_ids: list[str] = Field(default_factory=list)
    reason_code: str | None = None
    details: str | None = None


type DateConstraintValue = int | str | date | datetime | BudgetScope | DatePlanMode | TransportMode


class DatePlanOperation(BaseModel):
    type: DateOperationType
    target: StopReference | None = None
    payload: DesiredDateStop | None = None
    constraint_field: DateConstraintField | None = None
    constraint_value: DateConstraintValue | None = None
    source_span: str | None = Field(default=None, min_length=1, max_length=1000)
    alternative_group: str | None = Field(default=None, min_length=1, max_length=64)
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_operation_shape(self) -> "DatePlanOperation":
        if self.alternative_group is not None and self.type != DateOperationType.ADD_STOP:
            raise ValueError("alternative groups are only valid for add-stop operations")
        if self.type == DateOperationType.UPDATE_CONSTRAINT:
            if self.constraint_field is None or self.constraint_value is None:
                raise ValueError("constraint updates require a field and value")
        elif self.type == DateOperationType.ADD_STOP:
            if self.payload is None:
                raise ValueError("add-stop operations require a payload")
            if self.payload.generic_replacement:
                raise ValueError("generic replacements cannot be used as add-stop payloads")
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


class DateOperationBatch(BaseModel):
    operations: list[DatePlanOperation] = Field(default_factory=list, max_length=12)
    source_text: str | None = Field(default=None, min_length=1, max_length=4000)
    unresolved_references: list[str] = Field(default_factory=list, max_length=12)


class DateSemanticParseResult(BaseModel):
    operations: list[DatePlanOperation] = Field(default_factory=list, max_length=12)
    unresolved_references: list[str] = Field(default_factory=list, max_length=12)

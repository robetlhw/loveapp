from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from loveapp.domain.date_plan import DatePlanRequest


class ConstraintStrength(StrEnum):
    HARD = "hard"
    REQUIRED = "required"
    SOFT = "soft"


class DateConstraintKind(StrEnum):
    BUDGET = "budget"
    LOCATION = "location"
    DINING = "dining"
    ACTIVITY = "activity"
    EXCLUSION = "exclusion"
    ALLERGY = "allergy"
    TIME = "time"
    TRANSPORT = "transport"
    WEATHER = "weather"


class ConstraintSource(StrEnum):
    USER_EXPLICIT = "user_explicit"
    TASK_STATE = "task_state"
    MEMORY = "memory"
    DEFAULT = "default"


class DateConstraint(BaseModel):
    kind: DateConstraintKind
    strength: ConstraintStrength
    value: Any
    source: ConstraintSource
    weight: float = Field(default=1.0, ge=0)


def build_date_constraints(request: DatePlanRequest) -> list[DateConstraint]:
    """Translate the stable request contract into explicit validation inputs."""

    constraints = [
        DateConstraint(
            kind=DateConstraintKind.BUDGET,
            strength=ConstraintStrength.HARD,
            value=request.effective_total_budget,
            source=(
                ConstraintSource.DEFAULT
                if request.budget_is_assumed
                else ConstraintSource.USER_EXPLICIT
            ),
        )
    ]
    constraints.extend(
        DateConstraint(
            kind=DateConstraintKind.ACTIVITY,
            strength=ConstraintStrength.REQUIRED,
            value=keyword,
            source=ConstraintSource.USER_EXPLICIT,
        )
        for keyword in request.activity_keywords
    )
    constraints.extend(
        DateConstraint(
            kind=DateConstraintKind.DINING,
            strength=ConstraintStrength.REQUIRED,
            value=keyword,
            source=ConstraintSource.USER_EXPLICIT,
        )
        for keyword in request.dining_keywords
    )
    constraints.extend(
        DateConstraint(
            kind=DateConstraintKind.EXCLUSION,
            strength=ConstraintStrength.HARD,
            value=keyword,
            source=ConstraintSource.USER_EXPLICIT,
        )
        for keyword in request.excluded_keywords
    )
    if request.weather is not None or request.weather_forecasts:
        constraints.append(
            DateConstraint(
                kind=DateConstraintKind.WEATHER,
                strength=ConstraintStrength.SOFT,
                value="weather-aware",
                source=ConstraintSource.TASK_STATE,
            )
        )
    return constraints

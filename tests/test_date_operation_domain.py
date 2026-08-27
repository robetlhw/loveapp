from datetime import time

import pytest
from pydantic import ValidationError

from loveapp.domain.date_operations import (
    DateConstraintField,
    DateOperationType,
    DatePlanOperation,
    DesiredDateStop,
    MealType,
    StopKind,
    StopReference,
    TemporalAnchor,
    TimeWindow,
)


def test_constraint_operation_is_typed() -> None:
    operation = DatePlanOperation(
        type=DateOperationType.UPDATE_CONSTRAINT,
        constraint_field=DateConstraintField.BUDGET,
        constraint_value=600,
        source_span="预算改到600",
    )

    assert operation.constraint_field == DateConstraintField.BUDGET
    assert operation.constraint_value == 600


def test_structured_stop_preserves_meal_and_temporal_semantics() -> None:
    lunch = DesiredDateStop(
        kind=StopKind.DINING,
        keyword="烧烤",
        meal_type=MealType.LUNCH,
    )
    movie = DesiredDateStop(
        kind=StopKind.ACTIVITY,
        keyword="电影",
        after=TemporalAnchor.DINNER,
        time_window=TimeWindow(start=time(19), label="晚饭后"),
    )

    assert lunch.meal_type == MealType.LUNCH
    assert movie.after == TemporalAnchor.DINNER
    assert movie.time_window is not None
    assert movie.time_window.start == time(19)


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "update_constraint", "constraint_field": "budget"},
        {"type": "add_stop"},
        {"type": "remove_stop"},
        {
            "type": "replace_stop",
            "target": {"keyword": "火锅"},
        },
        {
            "type": "move_stop",
            "payload": {"kind": "activity", "keyword": "电影"},
        },
    ],
)
def test_operation_shape_rejects_missing_required_parts(payload: dict) -> None:
    with pytest.raises(ValidationError):
        DatePlanOperation.model_validate(payload)


def test_stop_reference_requires_an_identifier() -> None:
    with pytest.raises(ValidationError):
        StopReference()

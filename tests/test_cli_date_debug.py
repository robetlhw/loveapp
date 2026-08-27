from io import StringIO

import pytest
from rich.console import Console

import loveapp.cli as cli_module
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.date_operations import (
    DateConstraintField,
    DateOperationType,
    DatePlanOperation,
    DateReplacementPreference,
    DateRequirementMatch,
    DateStopRequirement,
    DesiredDateStop,
    MealType,
    RequirementStatus,
    StopKind,
    StopReference,
    TemporalAnchor,
)
from loveapp.domain.date_task import DatePlanningTaskState, DateTaskDiff, DateTaskFieldChange


def _capture_console(monkeypatch: pytest.MonkeyPatch) -> StringIO:
    output = StringIO()
    monkeypatch.setattr(
        cli_module,
        "console",
        Console(file=output, force_terminal=False, width=300),
    )
    return output


def test_date_task_debug_renders_canonical_governance_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _capture_console(monkeypatch)
    requirement = DateStopRequirement(
        id="req-dinner",
        alternatives=[
            DesiredDateStop(
                kind=StopKind.DINING,
                keyword="日料",
                meal_type=MealType.DINNER,
                target_day=2,
                after=TemporalAnchor.AFTERNOON,
            ),
            DesiredDateStop(
                kind=StopKind.DINING,
                keyword="火锅",
                meal_type=MealType.DINNER,
                target_day=2,
                after=TemporalAnchor.AFTERNOON,
            ),
        ],
        source_span="第二天晚餐吃日料或者火锅",
    )
    state = DatePlanningTaskState(
        user_id="user",
        relationship_id="relationship",
        conversation_id="conversation",
        budget=800,
        requirements=[requirement],
        requirement_satisfaction=[
            DateRequirementMatch(
                requirement_id=requirement.id,
                status=RequirementStatus.UNSATISFIED,
                reason_code="no_matching_place",
                details="candidate plan omitted dinner",
            )
        ],
        last_operations=[
            DatePlanOperation(
                type=DateOperationType.UPDATE_CONSTRAINT,
                constraint_field=DateConstraintField.BUDGET,
                constraint_value=800,
                source_span="预算改成八百",
            ),
            DatePlanOperation(
                type=DateOperationType.REPLACE_STOP,
                target=StopReference(keyword="公园"),
                payload=DesiredDateStop(
                    kind=StopKind.ACTIVITY,
                    keyword="博物馆",
                    replacement_preferences=[DateReplacementPreference.NEARBY],
                ),
                source_span="把公园换成附近的博物馆",
            ),
            DatePlanOperation(
                type=DateOperationType.ADD_STOP,
                payload=DesiredDateStop(kind=StopKind.ACTIVITY, keyword="电影院"),
                source_span="再加一个电影院",
            ),
        ],
        last_task_diff=DateTaskDiff(
            changes={"budget": DateTaskFieldChange(before=500, after=800)}
        ),
    )

    cli_module._render_date_task_state(state)

    rendered = output.getvalue()
    assert "Date Requirements" in rendered
    assert "req-dinner" in rendered
    assert "dining:日料" in rendered
    assert "dining:火锅" in rendered
    assert "meal=dinner" in rendered
    assert "day=2" in rendered
    assert "after=afternoon" in rendered
    assert "第二天晚餐吃日料或者火锅" in rendered
    assert "Requirement Satisfaction" in rendered
    assert "unsatisfied" in rendered
    assert "no_matching_place" in rendered
    assert "Operation Batch" in rendered
    assert "update_constraint" in rendered
    assert "replace_stop" in rendered
    assert "add_stop" in rendered
    assert "keyword=公园" in rendered
    assert "activity:博物馆" in rendered
    assert "activity:电影院" in rendered
    assert "Task Diff" in rendered
    assert "budget" in rendered
    assert "500" in rendered
    assert "800" in rendered


def test_date_task_debug_omits_empty_governance_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _capture_console(monkeypatch)
    state = DatePlanningTaskState(
        user_id="user",
        relationship_id="relationship",
        conversation_id="conversation",
    )

    cli_module._render_date_task_state(state)

    rendered = output.getvalue()
    assert "约会任务状态" in rendered
    assert "Date Requirements" not in rendered
    assert "Requirement Satisfaction" not in rendered
    assert "Operation Batch" not in rendered
    assert "Task Diff" not in rendered


def test_date_plan_changed_telemetry_is_visible_without_module_timings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _capture_console(monkeypatch)
    trace = ExecutionTrace()
    with trace.measure("date_plan_diff") as details:
        details["date_plan_changed"] = False

    cli_module._render_date_plan_telemetry(trace)

    rendered = output.getvalue()
    assert "Date Plan Telemetry" in rendered
    assert "date_plan_diff" in rendered
    assert "date_plan_changed" in rendered
    assert "no" in rendered
    assert "duration" not in rendered.casefold()


def test_date_operation_outcome_shows_counts_without_module_timings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _capture_console(monkeypatch)
    trace = ExecutionTrace()
    with trace.measure("date_operation_execute") as details:
        details.update(
            {
                "requested_count": 3,
                "applied_count": 2,
                "rejected_count": 1,
                "rejections_json": '[{"type":"add_stop","reason":"stop_not_added"}]',
            }
        )

    cli_module._render_date_operation_outcome(trace)

    rendered = output.getvalue()
    assert "Operation Outcome" in rendered
    assert "Requested" in rendered
    assert "Applied" in rendered
    assert "Rejected" in rendered
    assert "stop_not_added" in rendered
    assert "duration" not in rendered.casefold()

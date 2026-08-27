import pytest

from loveapp.application.date_planning.fact_parsing import (
    BudgetUpdateKind,
    DateFactParser,
)
from loveapp.application.routing import route_by_rules
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.enums import (
    BudgetScope,
    DatePlanMutation,
    TaskType,
    TransportMode,
)
from loveapp.domain.routing import RouteInput


@pytest.mark.parametrize(
    ("text", "value", "scope", "kind"),
    [
        ("预算1000", 1000, BudgetScope.TOTAL, BudgetUpdateKind.SET),
        ("预算就1000吧", 1000, BudgetScope.TOTAL, BudgetUpdateKind.SET),
        ("预算改为1000", 1000, BudgetScope.TOTAL, BudgetUpdateKind.UPDATE),
        ("预算改成1000", 1000, BudgetScope.TOTAL, BudgetUpdateKind.UPDATE),
        ("预算调整到1000", 1000, BudgetScope.TOTAL, BudgetUpdateKind.UPDATE),
        ("预算提高到1000", 1000, BudgetScope.TOTAL, BudgetUpdateKind.INCREASE),
        ("预算降到500", 500, BudgetScope.TOTAL, BudgetUpdateKind.DECREASE),
        ("预算控制在800", 800, BudgetScope.TOTAL, BudgetUpdateKind.UPPER_BOUND),
        ("预算不超过800", 800, BudgetScope.TOTAL, BudgetUpdateKind.UPPER_BOUND),
        ("每天预算500", 500, BudgetScope.PER_DAY, BudgetUpdateKind.SET),
        ("每日500", 500, BudgetScope.PER_DAY, BudgetUpdateKind.SET),
    ],
)
def test_date_fact_parser_covers_budget_forms(
    text: str,
    value: int,
    scope: BudgetScope,
    kind: BudgetUpdateKind,
) -> None:
    result = DateFactParser().parse_detailed(text)

    assert result.patch.budget == value
    assert result.patch.budget_scope == scope
    assert result.budget_update_kind == kind
    assert result.patch.source_by_field["budget"].value == "rule"


def test_date_fact_parser_extracts_location_and_transport() -> None:
    patch = DateFactParser().parse("区域改成徐汇区，交通改成开车")

    assert patch.city == "上海"
    assert patch.area == "徐汇区"
    assert patch.transport_mode == TransportMode.DRIVING


@pytest.mark.parametrize(
    "text",
    [
        "预算改为1000",
        "区域改成徐汇区",
        "时间改到晚上7点",
        "交通改成开车",
    ],
)
def test_constraint_object_update_precedes_surface_replace_keyword(text: str) -> None:
    state = DatePlanningTaskState(
        user_id="fact-user",
        relationship_id="fact-relationship",
        conversation_id="fact-conversation",
        city="上海",
        budget=300,
    )

    result = route_by_rules(
        RouteInput(
            latest_query=text,
            active_task=TaskType.DATE_PLANNING,
            date_task_state=state,
        )
    )

    assert result.task_type == TaskType.DATE_PLANNING
    assert result.date_mutation == DatePlanMutation.UPDATE_CONSTRAINT


def test_stop_replacement_remains_a_stop_replacement() -> None:
    state = DatePlanningTaskState(
        user_id="fact-user",
        relationship_id="fact-relationship",
        conversation_id="fact-conversation",
        city="上海",
        budget=1000,
    )

    result = route_by_rules(
        RouteInput(
            latest_query="把火锅改成西餐",
            active_task=TaskType.DATE_PLANNING,
            date_task_state=state,
        )
    )

    assert result.date_mutation == DatePlanMutation.REPLACE
    assert result.date_plan.replace_place_names == ["火锅"]
    assert result.date_plan.dining_keywords == ["西餐"]

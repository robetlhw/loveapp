from loveapp.agents.date_workflow import _compose_response
from loveapp.application.date_planning.plan_diff import diff_date_plans, diff_date_tasks
from loveapp.application.date_planning.requirements import DateRequirementMatcher
from loveapp.application.date_planning.validation import (
    DatePlanValidator,
    ValidationSeverity,
)
from loveapp.domain.date_operations import (
    DateRequirementMatch,
    DateStopRequirement,
    DesiredDateStop,
    MealType,
    RequirementStatus,
    StopKind,
)
from loveapp.domain.date_plan import DatePlan, DatePlanItem, DatePlanRequest, Place
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.enums import DatePlanMutation, PlaceCategory


def _place(place_id: str, name: str, category: PlaceCategory, *tags: str) -> Place:
    return Place(
        id=place_id,
        name=name,
        city="上海",
        address="测试地址",
        category=category,
        tags=list(tags),
        search_keywords=list(tags),
        estimated_cost_per_person=100,
        source="test",
    )


def _plan(*items: DatePlanItem) -> DatePlan:
    return DatePlan(
        title="测试行程",
        summary="测试",
        items=list(items),
        total_estimated_cost=sum(item.estimated_cost for item in items),
        total_duration_minutes=sum(item.duration_minutes for item in items),
        data_source="test",
    )


def test_canonical_meal_role_mismatch_is_hard_invalid() -> None:
    requirement = DateStopRequirement(
        id="lunch-requirement",
        alternatives=[
            DesiredDateStop(
                kind=StopKind.DINING,
                keyword="法餐",
                meal_type=MealType.LUNCH,
            )
        ],
    )
    plan = _plan(
        DatePlanItem(
            order=1,
            place=_place("dinner", "测试法餐厅", PlaceCategory.RESTAURANT, "法餐"),
            duration_minutes=90,
            estimated_cost=200,
            reason="测试",
            meal_type="dinner",
            slot_keyword="法餐",
        )
    )
    satisfaction = DateRequirementMatcher().match([requirement], plan)

    validation = DatePlanValidator().validate(
        plan,
        DatePlanRequest(city="上海", budget=1000, requirements=[requirement]),
        [],
        requirements=[requirement],
        satisfaction=satisfaction,
    )

    assert satisfaction[0].reason_code == "required_stop_role_mismatch"
    assert validation.valid is False
    assert validation.issues[0].severity == ValidationSeverity.ERROR


def test_missing_requirement_is_satisfaction_warning_not_hard_invalid() -> None:
    requirement = DateStopRequirement(
        id="cinema-requirement",
        alternatives=[DesiredDateStop(kind=StopKind.ACTIVITY, keyword="电影院")],
    )
    plan = _plan(
        DatePlanItem(
            order=1,
            place=_place("museum", "测试博物馆", PlaceCategory.ATTRACTION, "博物馆"),
            duration_minutes=60,
            estimated_cost=200,
            reason="测试",
        )
    )
    satisfaction = DateRequirementMatcher().match([requirement], plan)

    validation = DatePlanValidator().validate(
        plan,
        DatePlanRequest(city="上海", budget=1000, requirements=[requirement]),
        [],
        requirements=[requirement],
        satisfaction=satisfaction,
    )

    assert satisfaction[0].reason_code == "required_stop_missing"
    assert validation.valid is True
    assert validation.issues[0].severity == ValidationSeverity.WARNING


def test_removed_only_response_acknowledges_the_removed_place() -> None:
    kept = DatePlanItem(
        order=1,
        place=_place("museum", "测试博物馆", PlaceCategory.ATTRACTION, "博物馆"),
        duration_minutes=60,
        estimated_cost=100,
        reason="测试",
    )
    removed = DatePlanItem(
        order=2,
        place=_place("movie", "测试电影院", PlaceCategory.ENTERTAINMENT, "电影院"),
        duration_minutes=120,
        estimated_cost=200,
        reason="测试",
    )
    before = _plan(kept, removed)
    after = _plan(kept)
    current = DatePlanningTaskState(
        user_id="user",
        relationship_id="relationship",
        conversation_id="conversation",
        city="上海",
        budget=1000,
        current_plan=before,
    )

    message = _compose_response(
        current=current,
        mutation=DatePlanMutation.REMOVE,
        plan=after,
        changed=True,
        plan_diff=diff_date_plans(before, after),
        task_diff=diff_date_tasks(current, current),
        satisfaction=[],
        requirements=[],
    )

    assert "移除测试电影院" in message
    assert "现有地点节点保持不变" not in message


def test_successful_remove_is_not_hidden_by_historical_unsatisfied_requirement() -> None:
    kept = DatePlanItem(
        order=1,
        place=_place("museum", "测试博物馆", PlaceCategory.ATTRACTION, "博物馆"),
        duration_minutes=60,
        estimated_cost=100,
        reason="测试",
    )
    removed = DatePlanItem(
        order=2,
        place=_place("movie", "测试电影院", PlaceCategory.ENTERTAINMENT, "电影院"),
        duration_minutes=120,
        estimated_cost=200,
        reason="测试",
    )
    pending = DateStopRequirement(
        id="pending-aquarium",
        alternatives=[DesiredDateStop(kind=StopKind.ACTIVITY, keyword="海洋馆")],
    )
    unsatisfied = DateRequirementMatch(
        requirement_id=pending.id,
        status=RequirementStatus.UNSATISFIED,
        reason_code="required_stop_missing",
    )
    before = _plan(kept, removed)
    after = _plan(kept)
    current = DatePlanningTaskState(
        user_id="user",
        relationship_id="relationship",
        conversation_id="conversation-history",
        city="上海",
        budget=1000,
        current_plan=before,
        requirements=[pending],
        requirement_satisfaction=[unsatisfied],
    )

    message = _compose_response(
        current=current,
        mutation=DatePlanMutation.REMOVE,
        plan=after,
        changed=True,
        plan_diff=diff_date_plans(before, after),
        task_diff=diff_date_tasks(current, current),
        satisfaction=[unsatisfied],
        requirements=[pending],
    )

    assert "移除测试电影院" in message
    assert "之前未满足的海洋馆要求仍待补充" in message

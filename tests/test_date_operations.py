from dataclasses import dataclass

import pytest

from loveapp.application.date_planning.operations import DateOperationExecutor
from loveapp.domain.date_operations import (
    DateConstraintField,
    DateOperationType,
    DatePlanOperation,
    DateStopRequirement,
    DesiredDateStop,
    MealType,
    StopKind,
    StopReference,
    TemporalAnchor,
    TimeWindow,
)
from loveapp.domain.date_plan import DatePlan, DatePlanItem, DatePlanRequest, Place
from loveapp.domain.enums import DatePlanMutation, PlaceCategory


def _place(
    place_id: str,
    keyword: str,
    category: PlaceCategory,
) -> Place:
    return Place(
        id=place_id,
        name=f"测试{keyword}",
        city="上海",
        address="测试地址",
        category=category,
        tags=[keyword],
        search_keywords=[keyword],
        estimated_cost_per_person=100,
        source="test",
    )


def _item(
    place_id: str,
    keyword: str,
    category: PlaceCategory,
    *,
    order: int,
    meal_type: str | None = None,
) -> DatePlanItem:
    return DatePlanItem(
        order=order,
        place=_place(place_id, keyword, category),
        duration_minutes=60,
        estimated_cost=100,
        reason="test",
        meal_type=meal_type,
        slot_keyword=keyword,
    )


def _plan(*items: DatePlanItem) -> DatePlan:
    return DatePlan(
        title="测试计划",
        summary="测试计划",
        items=list(items),
        total_estimated_cost=sum(item.estimated_cost for item in items),
        total_duration_minutes=sum(item.duration_minutes for item in items),
        data_source="test",
    )


@dataclass
class PlanCall:
    mutation: DatePlanMutation
    request: DatePlanRequest
    activity_focus: list[str] | None
    dining_focus: list[str] | None


class RecordingOperationPlanner:
    def __init__(self) -> None:
        self.plan_calls: list[PlanCall] = []
        self.rebuild_calls: list[list[DatePlanItem]] = []

    async def plan(
        self,
        request,
        *,
        trace=None,
        existing_plan=None,
        mutation=DatePlanMutation.NONE,
        focus_activity_keywords=None,
        focus_dining_keywords=None,
    ):
        del trace
        self.plan_calls.append(
            PlanCall(
                mutation=mutation,
                request=request,
                activity_focus=focus_activity_keywords,
                dining_focus=focus_dining_keywords,
            )
        )
        if existing_plan is None:
            return _plan(_item("initial", "公园", PlaceCategory.ATTRACTION, order=1))
        if mutation == DatePlanMutation.UPDATE_CONSTRAINT:
            return existing_plan.model_copy(update={"summary": "constraints updated"})
        if mutation == DatePlanMutation.REPLAN:
            return existing_plan.model_copy(update={"summary": "replanned"})

        activity = (focus_activity_keywords or [None])[0]
        dining = (focus_dining_keywords or [None])[0]
        keyword = activity or dining
        if keyword is None:
            return existing_plan
        category = PlaceCategory.ENTERTAINMENT if activity is not None else PlaceCategory.RESTAURANT
        meal_type = next(
            (meal for meal, keywords in request.meal_keywords.items() if keyword in keywords),
            None,
        )
        new_item = _item(
            f"new-{keyword}",
            keyword,
            category,
            order=len(existing_plan.items) + 1,
            meal_type=meal_type,
        )
        if mutation == DatePlanMutation.ADD:
            items = [*existing_plan.items, new_item]
        elif mutation == DatePlanMutation.REPLACE:
            target_name = request.replace_place_names[0]
            items = [
                new_item.model_copy(update={"order": item.order})
                if item.place.name == target_name
                else item
                for item in existing_plan.items
            ]
        else:
            return existing_plan
        return _plan(*items)

    async def rebuild_plan(
        self,
        existing_plan,
        request,
        items,
        *,
        summary,
        trace=None,
    ):
        del request, trace
        self.rebuild_calls.append(items)
        return existing_plan.model_copy(
            update={
                "summary": summary,
                "items": items,
                "total_estimated_cost": sum(item.estimated_cost for item in items),
                "total_duration_minutes": sum(item.duration_minutes for item in items),
            }
        )


def _request() -> DatePlanRequest:
    return DatePlanRequest(city="上海", budget=2000)


def _update_budget() -> DatePlanOperation:
    return DatePlanOperation(
        type=DateOperationType.UPDATE_CONSTRAINT,
        constraint_field=DateConstraintField.BUDGET,
        constraint_value=1000,
    )


@pytest.mark.asyncio
async def test_executor_orders_constraint_update_before_stop_add() -> None:
    planner = RecordingOperationPlanner()
    executor = DateOperationExecutor(planner)
    add = DatePlanOperation(
        type=DateOperationType.ADD_STOP,
        payload=DesiredDateStop(
            kind=StopKind.DINING,
            keyword="烧烤",
            meal_type=MealType.LUNCH,
        ),
    )
    current = _plan(
        _item("movie", "电影院", PlaceCategory.ENTERTAINMENT, order=1),
        _item(
            "dinner",
            "西餐",
            PlaceCategory.RESTAURANT,
            order=2,
            meal_type="dinner",
        ),
    )

    result = await executor.apply(current, [add, _update_budget()], _request())

    assert [call.mutation for call in planner.plan_calls] == [
        DatePlanMutation.UPDATE_CONSTRAINT,
        DatePlanMutation.ADD,
    ]
    assert planner.plan_calls[0].request.activity_keywords == ["电影院"]
    assert planner.plan_calls[0].request.dining_keywords == ["西餐"]
    assert [operation.type for operation in result.applied] == [
        DateOperationType.UPDATE_CONSTRAINT,
        DateOperationType.ADD_STOP,
    ]
    assert result.effective_mutation == DatePlanMutation.ADD
    assert any(item.slot_keyword == "烧烤" for item in result.plan.items)


@pytest.mark.asyncio
async def test_sibling_reservation_does_not_remove_existing_requirement() -> None:
    planner = RecordingOperationPlanner()
    executor = DateOperationExecutor(planner)
    existing_movie = DateStopRequirement(
        id="existing-movie",
        alternatives=[DesiredDateStop(kind=StopKind.ACTIVITY, keyword="电影院")],
    )
    request = _request().model_copy(update={"requirements": [existing_movie]})
    current = _plan(
        _item("old-attraction", "景点", PlaceCategory.ATTRACTION, order=1),
        _item("existing-movie", "电影院", PlaceCategory.ENTERTAINMENT, order=2),
    )
    add_movie = DatePlanOperation(
        type=DateOperationType.ADD_STOP,
        payload=DesiredDateStop(kind=StopKind.ACTIVITY, keyword="电影院"),
    )
    replace_attraction = DatePlanOperation(
        type=DateOperationType.REPLACE_STOP,
        target=StopReference(place_id="old-attraction"),
        payload=DesiredDateStop(kind=StopKind.ACTIVITY, keyword="公园"),
    )

    await executor.apply(
        current,
        [add_movie, replace_attraction],
        request,
        requirements=[existing_movie],
        existing_requirements=[existing_movie],
    )

    replace_request = next(
        call.request
        for call in planner.plan_calls
        if call.mutation == DatePlanMutation.REPLACE
    )
    assert [requirement.id for requirement in replace_request.requirements] == [
        "existing-movie"
    ]


@pytest.mark.asyncio
async def test_executor_removes_only_the_unique_target() -> None:
    planner = RecordingOperationPlanner()
    executor = DateOperationExecutor(planner)
    current = _plan(
        _item("museum", "博物馆", PlaceCategory.ATTRACTION, order=1),
        _item("movie", "电影院", PlaceCategory.ENTERTAINMENT, order=2),
        _item(
            "dinner",
            "西餐",
            PlaceCategory.RESTAURANT,
            order=3,
            meal_type="dinner",
        ),
    )
    remove = DatePlanOperation(
        type=DateOperationType.REMOVE_STOP,
        target=StopReference(place_id="movie"),
    )

    result = await executor.apply(current, [remove], _request())

    assert [item.place.id for item in result.plan.items] == ["museum", "dinner"]
    assert result.effective_mutation == DatePlanMutation.REMOVE
    assert len(planner.rebuild_calls) == 1


@pytest.mark.asyncio
async def test_executor_rejects_ambiguous_target() -> None:
    planner = RecordingOperationPlanner()
    executor = DateOperationExecutor(planner)
    current = _plan(
        _item("movie-1", "电影院", PlaceCategory.ENTERTAINMENT, order=1),
        _item("movie-2", "电影院", PlaceCategory.ENTERTAINMENT, order=2),
    )
    remove = DatePlanOperation(
        type=DateOperationType.REMOVE_STOP,
        target=StopReference(keyword="电影院"),
    )

    result = await executor.apply(current, [remove], _request())

    assert result.plan == current
    assert result.applied == ()
    assert result.rejected[0].reason == "operation_target_ambiguous"
    assert planner.rebuild_calls == []


@pytest.mark.asyncio
async def test_executor_replaces_the_referenced_stop() -> None:
    planner = RecordingOperationPlanner()
    executor = DateOperationExecutor(planner)
    current = _plan(
        _item("movie", "电影院", PlaceCategory.ENTERTAINMENT, order=1),
        _item(
            "hotpot",
            "火锅",
            PlaceCategory.RESTAURANT,
            order=2,
            meal_type="dinner",
        ),
    )
    replace = DatePlanOperation(
        type=DateOperationType.REPLACE_STOP,
        target=StopReference(place_id="hotpot"),
        payload=DesiredDateStop(kind=StopKind.DINING, keyword="西餐"),
    )

    result = await executor.apply(current, [replace], _request())

    assert [item.slot_keyword for item in result.plan.items] == ["电影院", "西餐"]
    assert result.plan.items[1].meal_type == "dinner"
    assert planner.plan_calls[0].mutation == DatePlanMutation.REPLACE
    assert planner.plan_calls[0].request.replace_place_names == ["测试火锅"]
    assert result.effective_mutation == DatePlanMutation.REPLACE


@pytest.mark.asyncio
async def test_executor_moves_movie_after_dinner_and_rebuilds_plan() -> None:
    planner = RecordingOperationPlanner()
    executor = DateOperationExecutor(planner)
    current = _plan(
        _item("movie", "电影院", PlaceCategory.ENTERTAINMENT, order=1),
        _item(
            "dinner",
            "西餐",
            PlaceCategory.RESTAURANT,
            order=2,
            meal_type="dinner",
        ),
    )
    move = DatePlanOperation(
        type=DateOperationType.MOVE_STOP,
        target=StopReference(place_id="movie"),
        payload=DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword="电影院",
            time_window=TimeWindow(label="晚饭后"),
            after=TemporalAnchor.DINNER,
        ),
    )

    result = await executor.apply(current, [move], _request())

    assert [item.place.id for item in result.plan.items] == ["dinner", "movie"]
    assert result.plan.items[1].time_label == "晚饭后"
    assert result.plan.items[1].after_item == "西餐"
    assert len(planner.rebuild_calls) == 1
    assert result.effective_mutation == DatePlanMutation.REORDER


@pytest.mark.asyncio
async def test_executor_replan_is_applied_last() -> None:
    planner = RecordingOperationPlanner()
    executor = DateOperationExecutor(planner)
    replan = DatePlanOperation(type=DateOperationType.REPLAN)
    current = _plan(_item("movie", "电影院", PlaceCategory.ENTERTAINMENT, order=1))

    result = await executor.apply(current, [replan, _update_budget()], _request())

    assert [call.mutation for call in planner.plan_calls] == [
        DatePlanMutation.UPDATE_CONSTRAINT,
        DatePlanMutation.REPLAN,
    ]
    assert result.plan.summary == "replanned"
    assert result.effective_mutation == DatePlanMutation.REPLAN


@pytest.mark.asyncio
async def test_initial_plan_rejects_operations_that_require_existing_state() -> None:
    planner = RecordingOperationPlanner()
    executor = DateOperationExecutor(planner)
    remove = DatePlanOperation(
        type=DateOperationType.REMOVE_STOP,
        target=StopReference(keyword="电影院"),
    )

    result = await executor.apply(None, [remove, _update_budget()], _request())

    assert result.plan.items[0].slot_keyword == "公园"
    assert [operation.type for operation in result.applied] == [DateOperationType.UPDATE_CONSTRAINT]
    assert result.rejected[0].reason == "operation_requires_existing_plan"

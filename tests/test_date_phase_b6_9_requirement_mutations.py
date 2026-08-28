from datetime import datetime

from loveapp.application.date_planning.operation_resolution import DateOperationResolver
from loveapp.application.date_planning.operations import DateOperationExecutor
from loveapp.application.date_planning.requirements import DateRequirementMatcher
from loveapp.application.date_planning.state_projection import DateRequirementProjector
from loveapp.domain.date_operations import (
    DateOperationType,
    DatePlanOperation,
    DateStopConstraints,
    DateStopRequirement,
    DesiredDateStop,
    RequirementStatus,
    StopKind,
    StopReference,
    TemporalAnchor,
)
from loveapp.domain.date_patch import DatePlanPatch
from loveapp.domain.date_plan import DatePlan, DatePlanItem, DatePlanRequest, Place
from loveapp.domain.enums import PlaceCategory, RelationshipStage
from loveapp.domain.runtime_context import DatePlanRuntimeContext, RuntimeContext


def _place(
    place_id: str,
    name: str,
    category: PlaceCategory,
    *keywords: str,
) -> Place:
    return Place(
        id=place_id,
        name=name,
        city="上海",
        address="测试地址",
        category=category,
        tags=list(keywords),
        search_keywords=list(keywords),
        estimated_cost_per_person=100,
        source="test",
    )


def _item(
    place_id: str,
    name: str,
    category: PlaceCategory,
    *keywords: str,
    order: int,
    slot_keyword: str | None = None,
    meal_type: str | None = None,
) -> DatePlanItem:
    return DatePlanItem(
        order=order,
        place=_place(place_id, name, category, *keywords),
        duration_minutes=60,
        estimated_cost=100,
        reason="测试",
        slot_keyword=slot_keyword,
        meal_type=meal_type,
        time_label="晚餐" if meal_type == "dinner" else None,
    )


def _plan(*items: DatePlanItem) -> DatePlan:
    return DatePlan(
        title="测试计划",
        summary="测试",
        items=list(items),
        total_estimated_cost=sum(item.estimated_cost for item in items),
        total_duration_minutes=sum(item.duration_minutes for item in items),
        data_source="test",
    )


def _runtime(
    plan: DatePlan,
    requirements: list[DateStopRequirement],
) -> RuntimeContext:
    satisfaction = DateRequirementMatcher().match(requirements, plan)
    return RuntimeContext(
        user_id="user",
        relationship_id="relationship",
        conversation_id="conversation",
        relationship_stage=RelationshipStage.UNKNOWN,
        now=datetime(2026, 8, 28, 10, 0),
        active_date_plan=DatePlanRuntimeContext(
            current_plan=plan,
            requirements=requirements,
            requirement_satisfaction=satisfaction,
        ),
    )


def _requirement(requirement_id: str, keyword: str) -> DateStopRequirement:
    return DateStopRequirement(
        id=requirement_id,
        alternatives=[DesiredDateStop(kind=StopKind.ACTIVITY, keyword=keyword)],
    )


class _RebuildPlanner:
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
        return existing_plan.model_copy(
            update={
                "items": items,
                "summary": summary,
                "total_estimated_cost": sum(item.estimated_cost for item in items),
                "total_duration_minutes": sum(item.duration_minutes for item in items),
            }
        )


def test_regroup_existing_requirements_to_one_of_removes_independent_inputs() -> None:
    cinema = _requirement("req-cinema", "电影院")
    pearl = _requirement("req-pearl", "东方明珠")
    plan = _plan(
        _item(
            "movie",
            "测试电影院",
            PlaceCategory.ENTERTAINMENT,
            "电影院",
            order=1,
            slot_keyword="电影院",
        ),
        _item(
            "pearl",
            "东方明珠",
            PlaceCategory.ATTRACTION,
            "东方明珠",
            order=2,
            slot_keyword="东方明珠",
        ),
    )
    runtime = _runtime(plan, [cinema, pearl])

    resolution = DateOperationResolver().resolve(
        "算了吧，电影院和东方明珠二选一吧。",
        runtime,
        DatePlanPatch(),
    )

    assert [operation.type for operation in resolution.operations] == [
        DateOperationType.UPDATE_REQUIREMENT
    ]
    projected = DateRequirementProjector().apply_requirement_operations(
        [cinema, pearl],
        resolution.operations,
        current_plan=plan,
        requirement_matches=runtime.active_date_plan.requirement_satisfaction,
    )
    assert len(projected) == 1
    assert projected[0].id not in {cinema.id, pearl.id}
    assert [alternative.keyword for alternative in projected[0].alternatives] == [
        "电影院",
        "东方明珠",
    ]
    assert (projected[0].min_satisfied, projected[0].max_satisfied) == (1, 1)


async def test_regroup_existing_requirements_keeps_one_plan_alternative() -> None:
    cinema = _requirement("req-cinema", "电影院")
    pearl = _requirement("req-pearl", "东方明珠")
    plan = _plan(
        _item(
            "movie",
            "测试电影院",
            PlaceCategory.ENTERTAINMENT,
            "电影院",
            order=1,
            slot_keyword="电影院",
        ),
        _item(
            "pearl",
            "东方明珠",
            PlaceCategory.ATTRACTION,
            "东方明珠",
            order=2,
            slot_keyword="东方明珠",
        ),
    )
    runtime = _runtime(plan, [cinema, pearl])
    operations = DateOperationResolver().resolve(
        "电影院和东方明珠二选一吧。",
        runtime,
        DatePlanPatch(),
    ).operations
    projected = DateRequirementProjector().apply_requirement_operations(
        [cinema, pearl],
        operations,
        current_plan=plan,
        requirement_matches=runtime.active_date_plan.requirement_satisfaction,
    )

    execution = await DateOperationExecutor(_RebuildPlanner()).apply(  # type: ignore[arg-type]
        plan,
        list(operations),
        DatePlanRequest(city="上海", budget=1000, requirements=projected),
        requirements=projected,
        existing_requirements=[cinema, pearl],
    )

    assert [item.place.id for item in execution.plan.items] == ["movie"]
    assert execution.applied == operations
    match = DateRequirementMatcher().match(projected, execution.plan)[0]
    assert match.status == RequirementStatus.FULFILLED


def test_move_uses_plan_item_binding_and_preserves_requirement_identity() -> None:
    cinema = _requirement("req-cinema", "电影院")
    plan = _plan(
        _item(
            "movie",
            "百丽宫影城",
            PlaceCategory.ENTERTAINMENT,
            "电影院",
            order=1,
        ),
        _item(
            "dinner",
            "测试晚餐",
            PlaceCategory.RESTAURANT,
            "西餐",
            order=2,
            slot_keyword="西餐",
            meal_type="dinner",
        ),
    )
    matches = DateRequirementMatcher().match([cinema], plan)
    move = DatePlanOperation(
        type=DateOperationType.MOVE_STOP,
        target=StopReference(place_id="movie"),
        payload=DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword="电影院",
            after=TemporalAnchor.DINNER,
        ),
        source_span="电影放晚饭后",
    )

    projected = DateRequirementProjector().apply_requirement_operations(
        [cinema],
        [move],
        current_plan=plan,
        requirement_matches=matches,
    )

    assert len(projected) == 1
    assert projected[0].id == cinema.id
    assert projected[0].alternatives[0].after == TemporalAnchor.DINNER
    assert projected[0].alternatives[0].target_day is None


def test_move_updates_only_the_bound_alternative() -> None:
    choice = DateStopRequirement(
        id="req-choice",
        alternatives=[
            DesiredDateStop(kind=StopKind.ACTIVITY, keyword="电影院"),
            DesiredDateStop(kind=StopKind.ACTIVITY, keyword="东方明珠"),
        ],
        min_satisfied=1,
        max_satisfied=1,
    )
    plan = _plan(
        _item(
            "movie",
            "测试电影院",
            PlaceCategory.ENTERTAINMENT,
            "电影院",
            order=1,
            slot_keyword="电影院",
        )
    )
    move = DatePlanOperation(
        type=DateOperationType.MOVE_STOP,
        target=StopReference(place_id="movie"),
        payload=DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword="电影院",
            after=TemporalAnchor.DINNER,
        ),
    )

    projected = DateRequirementProjector().apply_requirement_operations(
        [choice],
        [move],
        current_plan=plan,
        requirement_matches=DateRequirementMatcher().match([choice], plan),
    )

    assert projected[0].id == choice.id
    assert projected[0].alternatives[0].after == TemporalAnchor.DINNER
    assert projected[0].alternatives[1].after is None


def test_remove_uses_plan_item_binding_to_remove_the_requirement() -> None:
    cinema = _requirement("req-cinema", "电影院")
    plan = _plan(
        _item(
            "movie",
            "百丽宫影城",
            PlaceCategory.ENTERTAINMENT,
            "电影院",
            order=1,
        )
    )
    matches = DateRequirementMatcher().match([cinema], plan)
    assert matches[0].status == RequirementStatus.FULFILLED
    remove = DatePlanOperation(
        type=DateOperationType.REMOVE_STOP,
        target=StopReference(place_id="movie"),
    )

    projected = DateRequirementProjector().apply_requirement_operations(
        [cinema],
        [remove],
        current_plan=plan,
        requirement_matches=matches,
    )

    assert projected == []


def test_semantic_operation_dedupe_merges_equivalent_replace_targets() -> None:
    plan = _plan(
        _item(
            "pearl",
            "东方明珠",
            PlaceCategory.ATTRACTION,
            "景点",
            order=1,
            slot_keyword="景点",
        )
    )
    runtime = _runtime(plan, [_requirement("req-attraction", "景点")])
    semantic = DatePlanOperation(
        type=DateOperationType.REPLACE_STOP,
        target=StopReference(keyword="景点", ordinal=1),
        payload=DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword="景点",
            generic_replacement=True,
        ),
        source_span="把第一个景点换成另一个景点",
    )

    resolution = DateOperationResolver().resolve(
        "把第一个景点换成另一个景点。",
        runtime,
        DatePlanPatch(),
        proposed_operations=[semantic],
    )

    assert len(resolution.operations) == 1
    assert resolution.input_count > len(resolution.operations)
    operation = resolution.operations[0]
    assert operation.type == DateOperationType.REPLACE_STOP
    assert operation.target is not None
    assert operation.target.ordinal == 1
    assert operation.payload is not None
    assert operation.payload.generic_replacement is True


def test_semantic_operation_dedupe_preserves_stop_constraints() -> None:
    text = "把第一个景点换成评分4.9以上的另一个景点。"
    plan = _plan(
        _item(
            "pearl",
            "东方明珠",
            PlaceCategory.ATTRACTION,
            "景点",
            order=1,
            slot_keyword="景点",
        )
    )
    runtime = _runtime(plan, [_requirement("req-attraction", "景点")])
    semantic = DatePlanOperation(
        type=DateOperationType.REPLACE_STOP,
        target=StopReference(keyword="景点", ordinal=1),
        payload=DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword="景点",
            generic_replacement=True,
            constraints=DateStopConstraints(min_rating=4.9),
        ),
        source_span=text,
    )

    resolution = DateOperationResolver().resolve(
        text,
        runtime,
        DatePlanPatch(),
        proposed_operations=[semantic],
    )

    replacements = [
        operation
        for operation in resolution.operations
        if operation.type == DateOperationType.REPLACE_STOP
    ]
    assert len(replacements) == 1
    assert replacements[0].payload is not None
    assert replacements[0].payload.constraints == DateStopConstraints(min_rating=4.9)

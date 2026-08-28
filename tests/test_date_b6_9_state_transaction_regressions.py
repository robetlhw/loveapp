import pytest

from loveapp.agents.date_workflow import (
    DatePlanningWorkflow,
    _unsatisfied_turn_message,
)
from loveapp.application.date_planning.operations import DateOperationExecutor
from loveapp.application.date_planning.requirements import DateRequirementMatcher
from loveapp.application.date_planning.state_projection import DateRequirementProjector
from loveapp.domain.date_operations import (
    DateOperationBatch,
    DateOperationType,
    DatePlanOperation,
    DateRequirementMatch,
    DateRequirementUpdate,
    DateStopConstraints,
    DateStopRequirement,
    DesiredDateStop,
    RequirementReference,
    RequirementStatus,
    StopKind,
    StopReference,
    TemporalAnchor,
)
from loveapp.domain.date_plan import DatePlan, DatePlanItem, DatePlanRequest, Place
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.enums import PlaceCategory


def _stop(
    keyword: str,
    *,
    after: TemporalAnchor | StopReference | None = None,
    target_day: int | None = None,
) -> DesiredDateStop:
    return DesiredDateStop(
        kind=StopKind.ACTIVITY,
        keyword=keyword,
        after=after,
        target_day=target_day,
    )


def _requirement(requirement_id: str, keyword: str) -> DateStopRequirement:
    return DateStopRequirement(
        id=requirement_id,
        alternatives=[_stop(keyword)],
    )


def _place(
    place_id: str,
    name: str,
    category: PlaceCategory,
    *keywords: str,
) -> Place:
    return Place(
        id=place_id,
        name=name,
        city="Shanghai",
        address="test address",
        category=category,
        tags=list(keywords),
        search_keywords=list(keywords),
        estimated_cost_per_person=50,
        source="test",
    )


def _item(
    order: int,
    place: Place,
    *,
    slot_keyword: str | None = None,
    meal_type: str | None = None,
) -> DatePlanItem:
    return DatePlanItem(
        order=order,
        place=place,
        duration_minutes=60,
        estimated_cost=100,
        reason="test",
        slot_keyword=slot_keyword,
        meal_type=meal_type,
    )


def _plan(*items: DatePlanItem) -> DatePlan:
    return DatePlan(
        title="test plan",
        summary="test",
        items=list(items),
        total_estimated_cost=sum(item.estimated_cost for item in items),
        total_duration_minutes=sum(item.duration_minutes for item in items),
        data_source="test",
    )


class _RebuildPlanner:
    def __init__(self, replacement_plan: DatePlan | None = None) -> None:
        self.replacement_plan = replacement_plan

    async def plan(self, *args, **kwargs) -> DatePlan:
        del args, kwargs
        assert self.replacement_plan is not None
        return self.replacement_plan

    async def rebuild_plan(
        self,
        existing_plan,
        request,
        items,
        *,
        summary,
        trace=None,
    ) -> DatePlan:
        del request, trace
        return existing_plan.model_copy(
            update={
                "items": items,
                "summary": summary,
                "total_estimated_cost": sum(item.estimated_cost for item in items),
                "total_duration_minutes": sum(item.duration_minutes for item in items),
            }
        )


@pytest.mark.asyncio
async def test_regroup_keeps_item_owned_by_unmentioned_requirement() -> None:
    cinema = _requirement("req-cinema", "cinema")
    museum = _requirement("req-museum", "museum")
    specific_museum = _requirement("req-shanghai-museum", "Shanghai Museum")
    current_plan = _plan(
        _item(
            1,
            _place(
                "cinema-place",
                "Cinema",
                PlaceCategory.ENTERTAINMENT,
                "cinema",
            ),
            slot_keyword="cinema",
        ),
        _item(
            2,
            _place(
                "museum-place",
                "Shanghai Museum",
                PlaceCategory.ATTRACTION,
                "museum",
                "Shanghai Museum",
            ),
        ),
    )
    requirements = [cinema, museum, specific_museum]
    regroup = DatePlanOperation(
        type=DateOperationType.UPDATE_REQUIREMENT,
        requirement_update=DateRequirementUpdate(
            targets=[
                RequirementReference(
                    requirement_id=cinema.id,
                    stop_reference=StopReference(keyword="cinema"),
                ),
                RequirementReference(
                    requirement_id=museum.id,
                    stop_reference=StopReference(keyword="museum"),
                ),
            ]
        ),
    )
    matcher = DateRequirementMatcher()
    projected = DateRequirementProjector().apply_requirement_operations(
        requirements,
        [regroup],
        current_plan=current_plan,
        requirement_matches=matcher.match(requirements, current_plan),
    )

    execution = await DateOperationExecutor(_RebuildPlanner()).apply(  # type: ignore[arg-type]
        current_plan,
        [regroup],
        DatePlanRequest(city="Shanghai", budget=1000, requirements=projected),
        requirements=projected,
        existing_requirements=requirements,
    )

    assert "museum-place" in {item.place.id for item in execution.plan.items}
    assert all(
        match.status == RequirementStatus.FULFILLED
        for match in matcher.match(projected, execution.plan)
    )


@pytest.mark.asyncio
async def test_regroup_preserves_unmentioned_temporal_dependency() -> None:
    cinema = _requirement("req-cinema", "cinema")
    park = _requirement("req-park", "park")
    museum = DateStopRequirement(
        id="req-museum",
        alternatives=[_stop("museum", after=StopReference(keyword="park"))],
    )
    current_plan = _plan(
        _item(
            1,
            _place(
                "cinema-place",
                "Cinema",
                PlaceCategory.ENTERTAINMENT,
                "cinema",
            ),
            slot_keyword="cinema",
        ),
        _item(
            2,
            _place(
                "park-place",
                "Park",
                PlaceCategory.ATTRACTION,
                "park",
            ),
            slot_keyword="park",
        ),
        _item(
            3,
            _place(
                "museum-place",
                "Museum",
                PlaceCategory.ATTRACTION,
                "museum",
            ),
            slot_keyword="museum",
        ),
    )
    requirements = [cinema, park, museum]
    regroup = DatePlanOperation(
        type=DateOperationType.UPDATE_REQUIREMENT,
        requirement_update=DateRequirementUpdate(
                targets=[
                    RequirementReference(requirement_id=cinema.id),
                    RequirementReference(requirement_id=park.id),
            ]
        ),
    )
    matcher = DateRequirementMatcher()
    projected = DateRequirementProjector().apply_requirement_operations(
        requirements,
        [regroup],
        current_plan=current_plan,
        requirement_matches=matcher.match(requirements, current_plan),
    )

    execution = await DateOperationExecutor(_RebuildPlanner()).apply(  # type: ignore[arg-type]
        current_plan,
        [regroup],
        DatePlanRequest(city="Shanghai", budget=1000, requirements=projected),
        requirements=projected,
        existing_requirements=requirements,
    )

    assert {item.place.id for item in execution.plan.items} == {
        "park-place",
        "museum-place",
    }
    assert all(
        match.status == RequirementStatus.FULFILLED
        for match in matcher.match(projected, execution.plan)
    )


@pytest.mark.asyncio
async def test_regroup_keeps_a_fulfilled_target_instead_of_first_identity_match() -> None:
    cinema = DateStopRequirement(
        id="req-cinema",
        alternatives=[
            _stop("cinema").model_copy(
                update={
                    "constraints": DateStopConstraints(max_cost_per_person=10)
                }
            )
        ],
    )
    museum = _requirement("req-museum", "museum")
    current_plan = _plan(
        _item(
            1,
            _place(
                "cinema-place",
                "Cinema",
                PlaceCategory.ENTERTAINMENT,
                "cinema",
            ),
            slot_keyword="cinema",
        ),
        _item(
            2,
            _place(
                "museum-place",
                "Museum",
                PlaceCategory.ATTRACTION,
                "museum",
            ),
            slot_keyword="museum",
        ),
    )
    requirements = [cinema, museum]
    regroup = DatePlanOperation(
        type=DateOperationType.UPDATE_REQUIREMENT,
        requirement_update=DateRequirementUpdate(
            targets=[
                RequirementReference(requirement_id=cinema.id),
                RequirementReference(requirement_id=museum.id),
            ]
        ),
    )
    matcher = DateRequirementMatcher()
    projected = DateRequirementProjector().apply_requirement_operations(
        requirements,
        [regroup],
        current_plan=current_plan,
        requirement_matches=matcher.match(requirements, current_plan),
    )

    execution = await DateOperationExecutor(_RebuildPlanner()).apply(  # type: ignore[arg-type]
        current_plan,
        [regroup],
        DatePlanRequest(city="Shanghai", budget=1000, requirements=projected),
        requirements=projected,
        existing_requirements=requirements,
    )

    assert {item.place.id for item in execution.plan.items} == {"museum-place"}
    assert matcher.match(projected, execution.plan)[0].status == RequirementStatus.FULFILLED


@pytest.mark.asyncio
async def test_regroup_keeps_dependency_required_by_selected_alternative() -> None:
    park = _requirement("req-park", "park")
    cinema = DateStopRequirement(
        id="req-cinema",
        alternatives=[_stop("cinema", after=StopReference(keyword="park"))],
    )
    current_plan = _plan(
        _item(
            1,
            _place(
                "park-place",
                "Park",
                PlaceCategory.ATTRACTION,
                "park",
            ),
            slot_keyword="park",
        ),
        _item(
            2,
            _place(
                "cinema-place",
                "Cinema",
                PlaceCategory.ENTERTAINMENT,
                "cinema",
            ),
            slot_keyword="cinema",
        ),
    )
    requirements = [cinema, park]
    regroup = DatePlanOperation(
        type=DateOperationType.UPDATE_REQUIREMENT,
        requirement_update=DateRequirementUpdate(
            targets=[
                RequirementReference(requirement_id=cinema.id),
                RequirementReference(requirement_id=park.id),
            ]
        ),
    )
    matcher = DateRequirementMatcher()
    projected = DateRequirementProjector().apply_requirement_operations(
        requirements,
        [regroup],
        current_plan=current_plan,
        requirement_matches=matcher.match(requirements, current_plan),
    )

    execution = await DateOperationExecutor(_RebuildPlanner()).apply(  # type: ignore[arg-type]
        current_plan,
        [regroup],
        DatePlanRequest(city="Shanghai", budget=1000, requirements=projected),
        requirements=projected,
        existing_requirements=requirements,
    )

    assert {item.place.id for item in execution.plan.items} == {"park-place"}
    assert matcher.match(projected, execution.plan)[0].status == RequirementStatus.FULFILLED


@pytest.mark.asyncio
async def test_alternative_satisfaction_is_evaluated_after_prior_remove() -> None:
    old_museum = _item(
        1,
        _place(
            "old-museum",
            "Old Museum",
            PlaceCategory.ATTRACTION,
            "museum",
        ),
        slot_keyword="museum",
    )
    park = _item(
        2,
        _place(
            "park-place",
            "Park",
            PlaceCategory.ATTRACTION,
            "park",
        ),
        slot_keyword="park",
    )
    new_museum = _item(
        2,
        _place(
            "new-museum",
            "New Museum",
            PlaceCategory.ATTRACTION,
            "museum",
        ),
        slot_keyword="museum",
    )
    current_plan = _plan(old_museum, park)
    replacement_plan = _plan(park.model_copy(update={"order": 1}), new_museum)
    operations = [
        DatePlanOperation(
            type=DateOperationType.REMOVE_STOP,
            target=StopReference(place_id="old-museum"),
        ),
        DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=_stop("museum"),
            alternative_group="museum-or-aquarium",
        ),
        DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=_stop("aquarium"),
            alternative_group="museum-or-aquarium",
        ),
    ]

    execution = await DateOperationExecutor(
        _RebuildPlanner(replacement_plan)
    ).apply(  # type: ignore[arg-type]
        current_plan,
        operations,
        DatePlanRequest(city="Shanghai", budget=1000),
    )

    assert {item.place.id for item in execution.plan.items} == {
        "park-place",
        "new-museum",
    }
    assert [operation.type for operation in execution.applied] == [
        DateOperationType.REMOVE_STOP,
        DateOperationType.ADD_STOP,
    ]


def test_rejected_compound_batch_preserves_refined_addition() -> None:
    projector = DateRequirementProjector()
    museum = _stop("museum")
    addition = DatePlanOperation(
        type=DateOperationType.ADD_STOP,
        payload=museum,
    )
    move = DatePlanOperation(
        type=DateOperationType.MOVE_STOP,
        target=StopReference(keyword="museum"),
        payload=_stop("museum", target_day=2),
    )
    operations = [addition, move]
    projection = projector.project_requirement_operations(
        [],
        operations,
        current_plan=None,
    )
    candidate = projection.requirements
    workflow = DatePlanningWorkflow(  # type: ignore[arg-type]
        object(),
        object(),
        requirement_projector=projector,
    )

    committed = workflow._requirements_after_rejected_batch(
        [],
        projection.rejected_batch_requirements,
        DateOperationBatch(
            operations=operations,
            source_text="add museum, then move it to day 2",
        ),
    )

    assert candidate[0].alternatives[0].target_day == 2
    assert committed == candidate


def test_rejected_batch_keeps_new_addition_after_meal_role_refinement() -> None:
    projector = DateRequirementProjector()
    addition = DatePlanOperation(
        type=DateOperationType.ADD_STOP,
        payload=DesiredDateStop(kind=StopKind.DINING, keyword="cafe"),
    )
    move = DatePlanOperation(
        type=DateOperationType.MOVE_STOP,
        target=StopReference(keyword="cafe"),
        payload=DesiredDateStop(
            kind=StopKind.DINING,
            keyword="cafe",
            meal_type="dinner",
        ),
    )
    operations = [addition, move]
    projection = projector.project_requirement_operations(
        [],
        operations,
        current_plan=None,
    )
    workflow = DatePlanningWorkflow(  # type: ignore[arg-type]
        object(),
        object(),
        requirement_projector=projector,
    )

    committed = workflow._requirements_after_rejected_batch(
        [],
        projection.rejected_batch_requirements,
        DateOperationBatch(operations=operations, source_text="add cafe for dinner"),
    )

    assert len(committed) == 1
    assert committed[0].alternatives[0].meal_type == "dinner"


def test_rejected_batch_dedupes_remove_then_readd_of_existing_requirement() -> None:
    projector = DateRequirementProjector()
    existing = _requirement("req-museum", "museum")
    operations = [
        DatePlanOperation(
            type=DateOperationType.REMOVE_STOP,
            target=StopReference(keyword="museum"),
        ),
        DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=_stop("museum"),
        ),
    ]
    projection = projector.project_requirement_operations(
        [existing],
        operations,
        current_plan=None,
    )
    workflow = DatePlanningWorkflow(  # type: ignore[arg-type]
        object(),
        object(),
        requirement_projector=projector,
    )

    committed = workflow._requirements_after_rejected_batch(
        [existing],
        projection.rejected_batch_requirements,
        DateOperationBatch(operations=operations, source_text="replace museum"),
    )

    assert committed == [existing]


def test_rejected_batch_does_not_move_existing_requirement_via_repeated_add() -> None:
    projector = DateRequirementProjector()
    existing = _requirement("req-museum", "museum")
    operations = [
        DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=_stop("museum"),
        ),
        DatePlanOperation(
            type=DateOperationType.MOVE_STOP,
            target=StopReference(keyword="museum"),
            payload=_stop("museum", target_day=2),
        ),
    ]
    projection = projector.project_requirement_operations(
        [existing],
        operations,
        current_plan=None,
    )
    workflow = DatePlanningWorkflow(  # type: ignore[arg-type]
        object(),
        object(),
        requirement_projector=projector,
    )

    committed = workflow._requirements_after_rejected_batch(
        [existing],
        projection.rejected_batch_requirements,
        DateOperationBatch(operations=operations, source_text="keep museum, move it"),
    )

    assert committed == [existing]


def test_grouped_add_uses_the_same_precedence_as_execution() -> None:
    operations = [
        DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=_stop(keyword),
            alternative_group="museum-or-aquarium",
        )
        for keyword in ("museum", "aquarium")
    ]
    operations.append(
        DatePlanOperation(
            type=DateOperationType.REMOVE_STOP,
            target=StopReference(keyword="museum"),
        )
    )

    projected = DateRequirementProjector().apply_requirement_operations(
        [],
        operations,
        current_plan=None,
    )

    assert len(projected) == 1
    assert {stop.keyword for stop in projected[0].alternatives} == {
        "museum",
        "aquarium",
    }


@pytest.mark.asyncio
async def test_projection_uses_the_same_operation_order_as_execution() -> None:
    original_place = _place(
        "original-place",
        "Original",
        PlaceCategory.ATTRACTION,
        "original",
    )
    dinner_place = _place(
        "dinner-place",
        "Dinner",
        PlaceCategory.RESTAURANT,
        "dinner",
    )
    replacement_place = _place(
        "replacement-place",
        "Replacement",
        PlaceCategory.ATTRACTION,
        "replacement",
    )
    current_plan = _plan(
        _item(1, original_place, slot_keyword="original"),
        _item(2, dinner_place, slot_keyword="dinner", meal_type="dinner"),
    )
    replacement_plan = _plan(
        _item(1, dinner_place, slot_keyword="dinner", meal_type="dinner"),
        _item(2, replacement_place, slot_keyword="replacement"),
    )
    requirement = _requirement("req-original", "original")
    move = DatePlanOperation(
        type=DateOperationType.MOVE_STOP,
        target=StopReference(keyword="replacement"),
        payload=_stop("replacement", after=TemporalAnchor.DINNER),
    )
    replace = DatePlanOperation(
        type=DateOperationType.REPLACE_STOP,
        target=StopReference(place_id=original_place.id),
        payload=_stop("replacement"),
    )
    operations = [move, replace]
    matcher = DateRequirementMatcher()
    projected = DateRequirementProjector().apply_requirement_operations(
        [requirement],
        operations,
        current_plan=current_plan,
        requirement_matches=matcher.match([requirement], current_plan),
    )

    execution = await DateOperationExecutor(  # type: ignore[arg-type]
        _RebuildPlanner(replacement_plan)
    ).apply(
        current_plan,
        operations,
        DatePlanRequest(city="Shanghai", budget=1000, requirements=projected),
        requirements=projected,
        existing_requirements=[requirement],
    )

    assert [operation.type for operation in execution.applied] == [
        DateOperationType.REPLACE_STOP,
        DateOperationType.MOVE_STOP,
    ]
    moved = next(
        item for item in execution.plan.items if item.place.id == replacement_place.id
    )
    assert moved.after_item == "dinner"
    assert projected[0].alternatives[0].after == TemporalAnchor.DINNER


def test_modified_unsatisfied_requirement_is_not_reported_as_historical() -> None:
    original = _requirement("stable-requirement-id", "aquarium")
    updated = original.model_copy(
        update={
            "alternatives": [
                original.alternatives[0].model_copy(update={"target_day": 2})
            ]
        }
    )
    unsatisfied = DateRequirementMatch(
        requirement_id=original.id,
        status=RequirementStatus.UNSATISFIED,
        reason_code="required_stop_missing",
    )
    current = DatePlanningTaskState(
        user_id="user",
        relationship_id="relationship",
        conversation_id="historical-unsatisfied",
        requirements=[original],
        requirement_satisfaction=[unsatisfied],
    )

    message = _unsatisfied_turn_message(
        current,
        [updated],
        [unsatisfied],
        fallback_plan=True,
    )

    assert message.startswith("已记录")
    assert "之前未满足" not in message

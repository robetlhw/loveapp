from loveapp.application.date_planning.operations import DateOperationExecutor
from loveapp.application.date_planning.requirements import DateRequirementMatcher
from loveapp.application.date_planning.state_projection import DateRequirementProjector
from loveapp.domain.date_operations import (
    DateOperationType,
    DatePlanOperation,
    DateRequirementUpdate,
    DateStopRequirement,
    DesiredDateStop,
    RequirementReference,
    StopKind,
    StopReference,
)
from loveapp.domain.date_plan import DatePlan, DatePlanItem, DatePlanRequest, Place
from loveapp.domain.enums import PlaceCategory


def _stop(keyword: str, *, target_day: int | None = None) -> DesiredDateStop:
    return DesiredDateStop(
        kind=StopKind.ACTIVITY,
        keyword=keyword,
        target_day=target_day,
    )


def _requirement(
    requirement_id: str,
    *alternatives: DesiredDateStop,
) -> DateStopRequirement:
    return DateStopRequirement(
        id=requirement_id,
        alternatives=list(alternatives),
        min_satisfied=1,
        max_satisfied=1,
    )


def _item(
    place_id: str,
    name: str,
    *keywords: str,
    order: int,
    day_index: int = 1,
    slot_keyword: str | None = None,
) -> DatePlanItem:
    return DatePlanItem(
        order=order,
        day_index=day_index,
        place=Place(
            id=place_id,
            name=name,
            city="Shanghai",
            address="test address",
            category=PlaceCategory.ATTRACTION,
            tags=list(keywords),
            search_keywords=list(keywords),
            estimated_cost_per_person=100,
            source="test",
        ),
        duration_minutes=60,
        estimated_cost=100,
        reason="test",
        slot_keyword=slot_keyword,
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


def test_replace_inside_one_of_preserves_unbound_alternative() -> None:
    choice = _requirement("choice", _stop("cinema"), _stop("pearl"))
    plan = _plan(
        _item(
            "pearl-place",
            "Oriental Pearl",
            "pearl",
            order=1,
            slot_keyword="pearl",
        )
    )
    operation = DatePlanOperation(
        type=DateOperationType.REPLACE_STOP,
        target=StopReference(place_id="pearl-place"),
        payload=_stop("museum"),
    )

    projected = DateRequirementProjector().apply_requirement_operations(
        [choice],
        [operation],
        current_plan=plan,
        requirement_matches=DateRequirementMatcher().match([choice], plan),
    )

    assert len(projected) == 1
    assert projected[0].id == choice.id
    assert [alternative.keyword for alternative in projected[0].alternatives] == [
        "cinema",
        "museum",
    ]
    assert (projected[0].min_satisfied, projected[0].max_satisfied) == (1, 1)


def test_remove_with_multiple_requirement_bindings_fails_closed() -> None:
    generic = _requirement("generic", _stop("attraction"))
    specific = _requirement("specific", _stop("pearl"))
    plan = _plan(
        _item(
            "pearl-place",
            "Oriental Pearl",
            "attraction",
            "pearl",
            order=1,
        )
    )
    requirements = [generic, specific]
    operation = DatePlanOperation(
        type=DateOperationType.REMOVE_STOP,
        target=StopReference(place_id="pearl-place"),
    )

    projected = DateRequirementProjector().apply_requirement_operations(
        requirements,
        [operation],
        current_plan=plan,
        requirement_matches=DateRequirementMatcher().match(requirements, plan),
    )

    assert projected == requirements


async def test_remove_with_multiple_requirement_bindings_rejects_plan_mutation() -> None:
    generic = _requirement("generic", _stop("attraction"))
    specific = _requirement("specific", _stop("pearl"))
    plan = _plan(
        _item(
            "pearl-place",
            "Oriental Pearl",
            "attraction",
            "pearl",
            order=1,
        ),
        _item("park-place", "Park", "park", order=2),
    )
    operation = DatePlanOperation(
        type=DateOperationType.REMOVE_STOP,
        target=StopReference(place_id="pearl-place"),
    )

    execution = await DateOperationExecutor(_RebuildPlanner()).apply(  # type: ignore[arg-type]
        plan,
        [operation],
        DatePlanRequest(city="Shanghai", budget=1000),
        requirements=[generic, specific],
        existing_requirements=[generic, specific],
    )

    assert execution.plan == plan
    assert execution.applied == ()
    assert execution.rejected[0].reason == "ambiguous_requirement_binding"


def test_move_preserves_requirement_identity_when_current_day_differs() -> None:
    existing = _requirement("cinema", _stop("cinema", target_day=2))
    plan = _plan(
        _item(
            "cinema-place",
            "Cinema",
            "cinema",
            order=1,
            day_index=1,
            slot_keyword="cinema",
        )
    )
    operation = DatePlanOperation(
        type=DateOperationType.MOVE_STOP,
        target=StopReference(place_id="cinema-place"),
        payload=_stop("cinema", target_day=3),
    )

    projected = DateRequirementProjector().apply_requirement_operations(
        [existing],
        [operation],
        current_plan=plan,
        requirement_matches=DateRequirementMatcher().match([existing], plan),
    )

    assert len(projected) == 1
    assert projected[0].id == existing.id
    assert projected[0].alternatives[0].target_day == 3


async def test_regroup_prunes_by_stop_identity_when_placement_is_unsatisfied() -> None:
    cinema = _requirement("cinema", _stop("cinema", target_day=2))
    pearl = _requirement("pearl", _stop("pearl"))
    plan = _plan(
        _item(
            "cinema-place",
            "Cinema",
            "cinema",
            order=1,
            day_index=1,
            slot_keyword="cinema",
        ),
        _item(
            "pearl-place",
            "Oriental Pearl",
            "pearl",
            order=2,
            slot_keyword="pearl",
        ),
    )
    operation = DatePlanOperation(
        type=DateOperationType.UPDATE_REQUIREMENT,
        requirement_update=DateRequirementUpdate(
            targets=[
                RequirementReference(requirement_id=cinema.id),
                RequirementReference(requirement_id=pearl.id),
            ],
            min_satisfied=1,
            max_satisfied=1,
        ),
    )

    execution = await DateOperationExecutor(_RebuildPlanner()).apply(  # type: ignore[arg-type]
        plan,
        [operation],
        DatePlanRequest(city="Shanghai", budget=1000),
        existing_requirements=[cinema, pearl],
    )

    assert [item.place.id for item in execution.plan.items] == ["cinema-place"]
    assert execution.applied == (operation,)

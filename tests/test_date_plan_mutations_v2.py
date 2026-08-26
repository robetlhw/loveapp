from datetime import date

import pytest

from loveapp.application.date_planning.mutations import DatePlanMutator
from loveapp.domain.date_plan import DatePlan, DatePlanItem, DatePlanRequest, Place, Route
from loveapp.domain.enums import PlaceCategory, TransportMode


def _place(place_id: str, category: PlaceCategory) -> Place:
    return Place(
        id=place_id,
        name=place_id,
        city="Shanghai",
        address="address",
        category=category,
        estimated_cost_per_person=50,
        source="test",
    )


def _plan() -> DatePlan:
    activity = _place("activity", PlaceCategory.ATTRACTION)
    lunch = _place("lunch", PlaceCategory.RESTAURANT)
    return DatePlan(
        title="plan",
        summary="summary",
        items=[
            DatePlanItem(
                order=1, place=activity, duration_minutes=60, estimated_cost=100, reason="test"
            ),
            DatePlanItem(
                order=2,
                place=lunch,
                duration_minutes=60,
                estimated_cost=100,
                reason="test",
                meal_type="lunch",
            ),
        ],
        total_estimated_cost=200,
        total_duration_minutes=120,
        data_source="test",
    )


class RouteProvider:
    async def route(self, origin: Place, destination: Place, mode: TransportMode) -> Route:
        return Route(
            origin_id=origin.id,
            destination_id=destination.id,
            mode=mode,
            duration_minutes=5,
            distance_meters=100,
            source="test",
        )


@pytest.mark.asyncio
async def test_reorder_moves_lunch_before_activity_and_rebuilds_route() -> None:
    request = DatePlanRequest(city="Shanghai", date=date(2026, 8, 1), budget=300)
    result = await DatePlanMutator(RouteProvider()).reorder(_plan(), request)  # type: ignore[arg-type]

    assert [item.place.id for item in result.items] == ["lunch", "activity"]
    assert result.items[1].route_from_previous is not None
    assert result.items[1].route_from_previous.origin_id == "lunch"


def test_constraint_update_preserves_valid_plan_and_rejects_budget_violation() -> None:
    mutator = DatePlanMutator(RouteProvider())  # type: ignore[arg-type]
    valid = mutator.update_constraint(_plan(), DatePlanRequest(city="Shanghai", budget=300))
    invalid = mutator.update_constraint(_plan(), DatePlanRequest(city="Shanghai", budget=100))

    assert valid is not None
    assert invalid is None

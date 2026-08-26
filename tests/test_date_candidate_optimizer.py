import pytest

from loveapp.application.date_planning.ranking import DateCandidateOptimizer
from loveapp.domain.date_plan import DatePlanRequest, Place, Route
from loveapp.domain.enums import PlaceCategory, TransportMode


def _place(place_id: str, category: PlaceCategory, rating: float) -> Place:
    return Place(
        id=place_id,
        name=place_id,
        city="Shanghai",
        address="address",
        category=category,
        estimated_cost_per_person=50,
        rating=rating,
        source="test",
    )


class RouteAwareProvider:
    async def route(self, origin: Place, destination: Place, mode: TransportMode) -> Route:
        duration = 90 if origin.id == "far" else 5
        return Route(
            origin_id=origin.id,
            destination_id=destination.id,
            mode=mode,
            duration_minutes=duration,
            distance_meters=duration * 100,
            source="test",
        )


@pytest.mark.asyncio
async def test_optimizer_prefers_short_route_within_top_k_candidates() -> None:
    optimizer = DateCandidateOptimizer(RouteAwareProvider(), top_k=2)  # type: ignore[arg-type]
    request = DatePlanRequest(city="Shanghai", budget=300)

    result = await optimizer.optimize_pair(
        [
            _place("far", PlaceCategory.ATTRACTION, 5.0),
            _place("near", PlaceCategory.ATTRACTION, 4.8),
        ],
        [_place("dining", PlaceCategory.RESTAURANT, 4.8)],
        request,
    )

    assert result is not None
    assert result.activity.id == "near"
    assert result.route is not None and result.route.duration_minutes == 5

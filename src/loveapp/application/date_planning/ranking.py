import asyncio
from dataclasses import dataclass

from loveapp.domain.date_plan import DatePlanRequest, Place, Route
from loveapp.ports.maps import MapProvider


@dataclass(frozen=True)
class OptimizedPair:
    activity: Place
    dining: Place
    route: Route | None
    score: float
    route_error: str | None = None


class DateCandidateOptimizer:
    """Bounded route-aware selection for an ordinary single-day plan."""

    def __init__(self, map_provider: MapProvider, *, top_k: int = 3) -> None:
        self._map_provider = map_provider
        self._top_k = top_k

    async def optimize_pair(
        self,
        activities: list[Place],
        dining: list[Place],
        request: DatePlanRequest,
    ) -> OptimizedPair | None:
        pairs = [
            (activity, restaurant)
            for activity in activities[: self._top_k]
            for restaurant in dining[: self._top_k]
            if (activity.estimated_cost_per_person + restaurant.estimated_cost_per_person) * 2
            <= request.effective_total_budget
        ]
        if not pairs:
            return None
        routes = await asyncio.gather(
            *(self._route(activity, restaurant, request) for activity, restaurant in pairs)
        )
        options = [
            OptimizedPair(
                activity=activity,
                dining=restaurant,
                route=route,
                route_error=error,
                score=_local_score(activity, restaurant) - _route_penalty(route),
            )
            for (activity, restaurant), (route, error) in zip(pairs, routes, strict=True)
        ]
        return max(options, key=lambda option: option.score)

    async def _route(
        self, activity: Place, dining: Place, request: DatePlanRequest
    ) -> tuple[Route | None, str | None]:
        try:
            return await self._map_provider.route(activity, dining, request.transport_mode), None
        except Exception as exc:  # Route data is a soft input, never a hard failure.
            return None, str(exc)[:160]


def _local_score(activity: Place, dining: Place) -> float:
    return (
        (activity.rating or 0)
        + (dining.rating or 0)
        + len(activity.matched_preferences)
        + len(dining.matched_preferences)
    )


def _route_penalty(route: Route | None) -> float:
    return 0.0 if route is None else route.duration_minutes / 30

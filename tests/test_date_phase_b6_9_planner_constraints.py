from loveapp.adapters.maps.demo import DemoMapProvider
from loveapp.adapters.memory.in_memory import InMemoryMemoryStore
from loveapp.agents.date_planner import DatePlanningAgent
from loveapp.application.memory import MemoryService, NoOpMemoryExtractor
from loveapp.domain.date_operations import (
    DateStopConstraints,
    DateStopRequirement,
    DesiredDateStop,
    MealType,
    StopKind,
)
from loveapp.domain.date_plan import DatePlanRequest, Place, PlaceSearchRequest
from loveapp.domain.enums import PlaceCategory


class _ConstraintIgnoringMapProvider(DemoMapProvider):
    """Return all fixtures so planner-side constraint checks remain observable."""

    def __init__(self, restaurants: list[Place]) -> None:
        self.requests: list[PlaceSearchRequest] = []
        self._restaurants = restaurants

    async def search_places(self, request: PlaceSearchRequest) -> list[Place]:
        self.requests.append(request)
        if request.category == PlaceCategory.RESTAURANT:
            return self._restaurants
        if request.category == PlaceCategory.ATTRACTION:
            return [
                _place(
                    "activity",
                    category=PlaceCategory.ATTRACTION,
                    area="静安区",
                    cost=0,
                    rating=4.8,
                )
            ]
        return []


def _place(
    place_id: str,
    *,
    category: PlaceCategory = PlaceCategory.RESTAURANT,
    area: str,
    cost: int,
    rating: float,
) -> Place:
    return Place(
        id=place_id,
        name=f"{place_id}法餐厅",
        city="上海",
        address=f"上海市{area}测试地址",
        category=category,
        tags=["法餐", "景点"],
        estimated_cost_per_person=cost,
        cost_is_estimate=False,
        rating=rating,
        source="test-map",
    )


def _planner(provider: _ConstraintIgnoringMapProvider) -> DatePlanningAgent:
    memory = MemoryService(InMemoryMemoryStore(), NoOpMemoryExtractor())
    return DatePlanningAgent(provider, memory)


async def test_same_keyword_meal_requirements_keep_distinct_search_constraints() -> None:
    dinner = _place("dinner", area="浦东新区陆家嘴", cost=450, rating=5.0)
    lunch = _place("lunch", area="静安区", cost=180, rating=4.6)
    provider = _ConstraintIgnoringMapProvider([dinner, lunch])
    requirements = [
        DateStopRequirement(
            alternatives=[
                DesiredDateStop(
                    kind=StopKind.DINING,
                    keyword="法餐",
                    meal_type=MealType.LUNCH,
                    constraints=DateStopConstraints(
                        max_cost_per_person=200,
                        min_rating=4.5,
                        preferred_area="静安区",
                    ),
                )
            ]
        ),
        DateStopRequirement(
            alternatives=[
                DesiredDateStop(
                    kind=StopKind.DINING,
                    keyword="法餐",
                    meal_type=MealType.DINNER,
                    constraints=DateStopConstraints(
                        max_cost_per_person=500,
                        min_rating=4.9,
                        preferred_area="陆家嘴",
                    ),
                )
            ]
        ),
    ]

    plan = await _planner(provider).plan(
        DatePlanRequest(city="上海", area="静安区", budget=2000, requirements=requirements)
    )

    restaurant_requests = [
        request
        for request in provider.requests
        if request.category == PlaceCategory.RESTAURANT
    ]
    assert [request.required_keywords for request in restaurant_requests] == [
        ["法餐"],
        ["法餐"],
    ]
    assert [request.area for request in restaurant_requests] == ["静安区", "陆家嘴"]
    assert [request.max_cost_per_person for request in restaurant_requests] == [200, 500]
    assert [request.min_rating for request in restaurant_requests] == [4.5, 4.9]

    dining_by_role = {
        item.meal_type: item.place.id
        for item in plan.items
        if item.place.category == PlaceCategory.RESTAURANT
    }
    assert dining_by_role == {"lunch": "lunch", "dinner": "dinner"}


async def test_keywordless_meal_requirement_keeps_role_and_local_constraints() -> None:
    wrong = _place("wrong", area="陆家嘴", cost=350, rating=4.2)
    matching = _place("matching", area="静安区", cost=160, rating=4.8)
    provider = _ConstraintIgnoringMapProvider([wrong, matching])
    requirement = DateStopRequirement(
        alternatives=[
            DesiredDateStop(
                kind=StopKind.DINING,
                meal_type=MealType.LUNCH,
                constraints=DateStopConstraints(
                    max_cost_per_person=200,
                    min_rating=4.7,
                    preferred_area="静安区",
                ),
            )
        ]
    )

    plan = await _planner(provider).plan(
        DatePlanRequest(
            city="上海",
            area="徐汇区",
            budget=1000,
            requirements=[requirement],
        )
    )

    search = next(
        request
        for request in provider.requests
        if request.category == PlaceCategory.RESTAURANT
    )
    assert search.required_keywords == []
    assert search.area == "静安区"
    assert search.max_cost_per_person == 200
    assert search.min_rating == 4.7
    assert search.require_verified_cost is True

    lunch = next(item for item in plan.items if item.meal_type == "lunch")
    assert lunch.place.id == "matching"

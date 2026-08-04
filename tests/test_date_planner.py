from datetime import date

from loveapp.adapters.maps.amap import AmapAPIError
from loveapp.adapters.maps.demo import DemoMapProvider
from loveapp.adapters.memory.in_memory import InMemoryMemoryStore
from loveapp.agents.date_planner import DatePlanningAgent
from loveapp.application.memory import MemoryService, NoOpMemoryExtractor
from loveapp.bootstrap import build_container
from loveapp.core.config import Settings
from loveapp.domain.date_plan import DatePlanRequest
from loveapp.domain.enums import TransportMode
from loveapp.domain.memory import (
    MemoryCandidate,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    MemoryValence,
    RelationshipImpact,
    TimeKind,
)
from loveapp.domain.weather import WeatherForecast


class RainyWeatherProvider:
    name = "test-weather"

    async def forecast(self, request):
        return WeatherForecast(
            city=request.city,
            date=request.date,
            condition="中雨",
            rain_probability=80,
            source=self.name,
        )

    async def aclose(self) -> None:
        return None


class NoRouteDemoMapProvider(DemoMapProvider):
    async def route(self, origin, destination, mode):
        del origin, destination, mode
        raise AmapAPIError("高德没有返回可用路线。")


class CapturingMapProvider(DemoMapProvider):
    def __init__(self) -> None:
        self.requests = []

    async def search_places(self, request):
        self.requests.append(request)
        return await super().search_places(request)


async def test_date_plan_uses_structured_demo_places(app_settings: Settings) -> None:
    container = build_container(app_settings)

    plan = await container.date_planning_agent.plan(
        DatePlanRequest(
            city="杭州",
            area="西湖",
            budget=500,
            preferences=["安静", "咖啡", "展览"],
            transport_mode=TransportMode.TRANSIT,
        )
    )

    assert plan.data_source == "demo-map"
    assert len(plan.items) == 2
    assert all("演示" in item.place.name for item in plan.items)
    assert plan.items[1].route_from_previous is not None
    assert plan.total_estimated_cost <= 500


async def test_date_plan_selects_a_pair_within_tight_budget(app_settings: Settings) -> None:
    container = build_container(app_settings)

    plan = await container.date_planning_agent.plan(
        DatePlanRequest(city="杭州", area="西湖", budget=200)
    )

    assert plan.items
    assert plan.total_estimated_cost <= 200
    selected_ids = {item.place.id for item in plan.items}
    assert all(place.id not in selected_ids for place in plan.alternatives)


async def test_date_plan_softly_prefers_indoor_activity_in_rain() -> None:
    memory_store = InMemoryMemoryStore()
    memory_service = MemoryService(memory_store, NoOpMemoryExtractor())
    agent = DatePlanningAgent(
        DemoMapProvider(),
        memory_service,
        RainyWeatherProvider(),
    )

    plan = await agent.plan(
        DatePlanRequest(city="杭州", date=date(2026, 7, 25), budget=500)
    )

    assert plan.weather is not None
    assert plan.weather.favors_indoor is True
    assert set(plan.items[0].place.tags) & {"展览", "手工", "室内"}


async def test_date_planner_passes_exact_place_constraints_to_map_provider() -> None:
    memory_store = InMemoryMemoryStore()
    memory_service = MemoryService(memory_store, NoOpMemoryExtractor())
    provider = CapturingMapProvider()
    agent = DatePlanningAgent(provider, memory_service)

    plan = await agent.plan(
        DatePlanRequest(
            city="上海",
            area="静安区",
            budget=500,
            dining_keywords=["西餐"],
            activity_keywords=["博物馆"],
            excluded_keywords=["火锅"],
        )
    )

    assert plan.items
    restaurant_request = next(
        request for request in provider.requests if request.category.value == "restaurant"
    )
    attraction_request = next(
        request for request in provider.requests if request.category.value == "attraction"
    )
    assert restaurant_request.keywords == ["西餐"]
    assert restaurant_request.required_keywords == ["西餐"]
    assert restaurant_request.excluded_keywords == ["火锅"]
    assert attraction_request.keywords == ["博物馆"]
    assert attraction_request.required_keywords == ["博物馆"]


async def test_date_planner_turns_partner_avoidance_memory_into_search_exclusion() -> None:
    memory_store = InMemoryMemoryStore()
    memory_service = MemoryService(memory_store, NoOpMemoryExtractor())
    await memory_store.save_memory(
        user_id="memory-date-user",
        relationship_id="memory-date-relationship",
        candidate=MemoryCandidate(
            kind=MemoryKind.PREFERENCE,
            subject="partner",
            summary="对方不喜欢火锅",
            original_text="她不喜欢火锅",
            evidence_spans=["她不喜欢火锅"],
            time_kind=TimeKind.TIMELESS,
            valence=MemoryValence.NEGATIVE,
            relationship_impact=RelationshipImpact.UNCLEAR,
            perspective=MemoryPerspective.USER_REPORTED,
            confidence=1,
            payload={"preference": "火锅", "preference_type": "dislike"},
        ),
        status=MemoryStatus.CONFIRMED,
    )
    provider = CapturingMapProvider()
    agent = DatePlanningAgent(provider, memory_service)

    await agent.plan(
        DatePlanRequest(
            user_id="memory-date-user",
            relationship_id="memory-date-relationship",
            city="上海",
            budget=500,
        )
    )

    restaurant_request = next(
        request for request in provider.requests if request.category.value == "restaurant"
    )
    assert restaurant_request.excluded_keywords == ["火锅"]


async def test_date_planner_uses_structured_allergy_memory_as_exclusion() -> None:
    memory_store = InMemoryMemoryStore()
    memory_service = MemoryService(memory_store, NoOpMemoryExtractor())
    await memory_store.save_memory(
        user_id="allergy-date-user",
        relationship_id="allergy-date-relationship",
        candidate=MemoryCandidate(
            kind=MemoryKind.STABLE_FACT,
            subject="partner",
            summary="对方对花生过敏",
            original_text="她对花生过敏",
            evidence_spans=["她对花生过敏"],
            time_kind=TimeKind.TIMELESS,
            confidence=1,
            importance=4,
            payload={"predicate": "has_allergy", "allergen": "花生"},
        ),
        status=MemoryStatus.CONFIRMED,
    )
    provider = CapturingMapProvider()
    agent = DatePlanningAgent(provider, memory_service)

    await agent.plan(
        DatePlanRequest(
            user_id="allergy-date-user",
            relationship_id="allergy-date-relationship",
            city="上海",
            budget=500,
        )
    )

    restaurant_request = next(
        request for request in provider.requests if request.category.value == "restaurant"
    )
    assert restaurant_request.excluded_keywords == ["花生"]


async def test_date_plan_keeps_places_when_route_service_returns_no_result() -> None:
    memory_store = InMemoryMemoryStore()
    memory_service = MemoryService(memory_store, NoOpMemoryExtractor())
    agent = DatePlanningAgent(NoRouteDemoMapProvider(), memory_service)

    plan = await agent.plan(DatePlanRequest(city="杭州", budget=500))

    assert plan.items
    assert plan.items[1].route_from_previous is None
    assert any("路线服务暂未返回结果" in note for note in plan.notes)

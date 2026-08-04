from loveapp.adapters.memory.in_memory import InMemoryMemoryStore
from loveapp.agents.date_planner import DatePlanningAgent
from loveapp.application.memory import MemoryService, NoOpMemoryExtractor
from loveapp.domain.date_plan import DatePlanRequest, Place, PlaceSearchRequest, Route
from loveapp.domain.enums import DatePlanMutation, PlaceCategory, TransportMode


class IncrementalMapProvider:
    name = "incremental-test-map"

    def __init__(self) -> None:
        self._places = {
            PlaceCategory.ATTRACTION: [
                Place(
                    id="activity-original",
                    name="安静展览馆",
                    city="上海",
                    address="上海静安区一号",
                    category=PlaceCategory.ATTRACTION,
                    tags=["展览", "安静"],
                    estimated_cost_per_person=60,
                    rating=4.9,
                    source=self.name,
                ),
                Place(
                    id="activity-landmark",
                    name="上海经典景点",
                    city="上海",
                    address="上海市中心二号",
                    category=PlaceCategory.ATTRACTION,
                    tags=["景点", "经典"],
                    estimated_cost_per_person=40,
                    rating=4.6,
                    source=self.name,
                ),
                Place(
                    id="activity-old-park",
                    name="辅德里公园",
                    city="上海",
                    address="上海静安区四号",
                    category=PlaceCategory.ATTRACTION,
                    tags=["公园", "散步"],
                    estimated_cost_per_person=20,
                    rating=4.7,
                    source=self.name,
                ),
                Place(
                    id="activity-museum",
                    name="上海自然博物馆",
                    city="上海",
                    address="上海静安区五号",
                    category=PlaceCategory.ATTRACTION,
                    tags=["博物馆", "室内"],
                    estimated_cost_per_person=50,
                    rating=4.8,
                    source=self.name,
                ),
            ],
            PlaceCategory.ENTERTAINMENT: [],
            PlaceCategory.RESTAURANT: [
                Place(
                    id="dining-original",
                    name="日料餐厅",
                    city="上海",
                    address="上海静安区三号",
                    category=PlaceCategory.RESTAURANT,
                    tags=["日料"],
                    estimated_cost_per_person=150,
                    rating=4.8,
                    source=self.name,
                ),
                Place(
                    id="dining-korean",
                    name="韩国料理店",
                    city="上海",
                    address="上海静安区六号",
                    category=PlaceCategory.RESTAURANT,
                    tags=["韩国料理"],
                    estimated_cost_per_person=100,
                    rating=4.7,
                    source=self.name,
                ),
                Place(
                    id="dining-haidilao",
                    name="海底捞静安店",
                    city="上海",
                    address="上海静安区七号",
                    category=PlaceCategory.RESTAURANT,
                    tags=["海底捞", "火锅"],
                    estimated_cost_per_person=150,
                    rating=4.8,
                    source=self.name,
                ),
            ],
            PlaceCategory.CAFE: [],
        }

    async def search_places(self, request: PlaceSearchRequest) -> list[Place]:
        places = self._places[request.category]
        if request.required_keywords:
            places = [
                place
                for place in places
                if all(
                    any(keyword in " ".join([place.name, *place.tags]) for keyword in aliases)
                    for aliases in ((keyword,) for keyword in request.required_keywords)
                )
            ]
        if request.excluded_keywords:
            places = [
                place
                for place in places
                if not any(
                    any(keyword in " ".join([place.name, *place.tags]) for keyword in aliases)
                    for aliases in ((keyword,) for keyword in request.excluded_keywords)
                )
            ]
        return [
            place
            for place in places
            if request.max_cost_per_person is None
            or place.estimated_cost_per_person <= request.max_cost_per_person
        ]

    async def route(
        self,
        origin: Place,
        destination: Place,
        mode: TransportMode,
    ) -> Route:
        return Route(
            origin_id=origin.id,
            destination_id=destination.id,
            mode=mode,
            duration_minutes=10,
            distance_meters=1000,
            source=self.name,
        )


async def test_add_mutation_preserves_existing_plan_items() -> None:
    memory_service = MemoryService(InMemoryMemoryStore(), NoOpMemoryExtractor())
    agent = DatePlanningAgent(IncrementalMapProvider(), memory_service)
    first = await agent.plan(
        DatePlanRequest(
            city="上海",
            area="静安区",
            budget=1000,
            dining_keywords=["日料"],
        )
    )

    second = await agent.plan(
        DatePlanRequest(
            city="上海",
            area="静安区",
            budget=1000,
            activity_keywords=["景点"],
            dining_keywords=["日料"],
        ),
        existing_plan=first,
        mutation=DatePlanMutation.ADD,
    )

    first_ids = {item.place.id for item in first.items}
    second_ids = {item.place.id for item in second.items}
    assert first_ids <= second_ids
    assert "activity-landmark" in second_ids
    assert len(second.items) == len(first.items) + 1
    assert [item.order for item in second.items] == [1, 2, 3]
    assert second.items[-1].place.id == "dining-original"
    assert all(
        item.route_from_previous is not None
        for item in second.items[1:]
    )


async def test_replan_mutation_is_the_only_full_replacement_path() -> None:
    memory_service = MemoryService(InMemoryMemoryStore(), NoOpMemoryExtractor())
    agent = DatePlanningAgent(IncrementalMapProvider(), memory_service)
    first = await agent.plan(
        DatePlanRequest(city="上海", budget=1000, dining_keywords=["日料"])
    )

    replanned = await agent.plan(
        DatePlanRequest(
            city="上海",
            budget=1000,
            activity_keywords=["景点"],
            dining_keywords=["日料"],
        ),
        existing_plan=first,
        mutation=DatePlanMutation.REPLAN,
    )

    assert "activity-original" not in {item.place.id for item in replanned.items}
    assert "activity-landmark" in {item.place.id for item in replanned.items}


async def test_replace_mutation_keeps_the_other_plan_category() -> None:
    memory_service = MemoryService(InMemoryMemoryStore(), NoOpMemoryExtractor())
    agent = DatePlanningAgent(IncrementalMapProvider(), memory_service)
    first = await agent.plan(
        DatePlanRequest(city="上海", budget=1000, dining_keywords=["日料"])
    )

    replaced = await agent.plan(
        DatePlanRequest(
            city="上海",
            budget=1000,
            activity_keywords=["景点"],
            dining_keywords=["日料"],
        ),
        existing_plan=first,
        mutation=DatePlanMutation.REPLACE,
        focus_activity_keywords=["景点"],
        focus_dining_keywords=[],
    )

    ids = {item.place.id for item in replaced.items}
    assert "activity-landmark" in ids
    assert "dining-original" in ids
    assert "activity-original" not in ids


async def test_named_replacement_also_applies_new_meal_stops() -> None:
    memory_service = MemoryService(InMemoryMemoryStore(), NoOpMemoryExtractor())
    agent = DatePlanningAgent(IncrementalMapProvider(), memory_service)
    first = await agent.plan(
        DatePlanRequest(
            city="上海",
            area="静安区",
            budget=1000,
            activity_keywords=["公园"],
            dining_keywords=["韩国料理"],
            meal_keywords={"lunch": ["韩国料理"]},
            schedule_hints=["中午", "下午"],
        )
    )

    updated = await agent.plan(
        DatePlanRequest(
            city="上海",
            area="静安区",
            budget=1000,
            activity_keywords=["博物馆"],
            dining_keywords=["韩国料理", "海底捞"],
            meal_keywords={
                "lunch": ["韩国料理"],
                "dinner": ["海底捞"],
            },
            schedule_hints=["中午", "下午", "晚上"],
            replace_place_names=["辅德里公园"],
            excluded_keywords=["辅德里公园"],
        ),
        existing_plan=first,
        mutation=DatePlanMutation.REPLACE,
        focus_activity_keywords=["博物馆"],
        focus_dining_keywords=["韩国料理", "海底捞"],
    )

    ids = {item.place.id for item in updated.items}
    assert "activity-old-park" not in ids
    assert {"activity-museum", "dining-korean", "dining-haidilao"} <= ids
    by_keyword = {
        item.slot_keyword: item
        for item in updated.items
        if item.slot_keyword is not None
    }
    assert by_keyword["韩国料理"].meal_type == "lunch"
    assert by_keyword["博物馆"].time_label == "下午"
    assert by_keyword["海底捞"].meal_type == "dinner"
    assert by_keyword["韩国料理"].order < by_keyword["博物馆"].order
    assert by_keyword["博物馆"].order < by_keyword["海底捞"].order
    assert "辅德里公园" in updated.summary
    assert "上海自然博物馆" in updated.summary
    assert "海底捞静安店" in updated.summary

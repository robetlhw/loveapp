from loveapp.domain.date_plan import Place, PlaceSearchRequest, Route
from loveapp.domain.enums import PlaceCategory, TransportMode


class DemoMapProvider:
    name = "demo-map"

    async def search_places(self, request: PlaceSearchRequest) -> list[Place]:
        area = request.area or "中心城区"
        templates = _PLACE_TEMPLATES[request.category]
        places = [
            Place(
                id=f"demo-{request.category.value}-{index}",
                name=f"{template['name']}（演示）",
                city=request.city,
                address=f"{request.city}{area}演示地址 {index} 号",
                category=request.category,
                tags=template["tags"],
                matched_preferences=[
                    preference
                    for preference in request.preferences
                    if preference in template["tags"]
                ],
                estimated_cost_per_person=template["cost"],
                cost_is_estimate=True,
                rating=template["rating"],
                source=self.name,
            )
            for index, template in enumerate(templates, start=1)
            if request.max_cost_per_person is None
            or template["cost"] <= request.max_cost_per_person
        ]

        required_keywords = request.required_keywords or request.keywords
        if required_keywords:
            places = [
                place
                for place in places
                if all(
                    any(keyword in " ".join([place.name, *place.tags]) for keyword in aliases)
                    for aliases in (
                        _DEMO_KEYWORD_ALIASES.get(keyword, (keyword,))
                        for keyword in required_keywords
                    )
                )
            ]
        if request.excluded_keywords:
            places = [
                place
                for place in places
                if not any(
                    any(keyword in " ".join([place.name, *place.tags]) for keyword in aliases)
                    for aliases in (
                        _DEMO_KEYWORD_ALIASES.get(keyword, (keyword,))
                        for keyword in request.excluded_keywords
                    )
                )
            ]

        preferences = {preference.casefold() for preference in request.preferences}

        def preference_score(place: Place) -> tuple[int, float]:
            matched = sum(tag.casefold() in preferences for tag in place.tags)
            return matched, place.rating or 0

        return sorted(places, key=preference_score, reverse=True)

    async def route(
        self,
        origin: Place,
        destination: Place,
        mode: TransportMode,
    ) -> Route:
        duration_by_mode = {
            TransportMode.WALKING: 22,
            TransportMode.TRANSIT: 15,
            TransportMode.DRIVING: 12,
            TransportMode.CYCLING: 14,
        }
        return Route(
            origin_id=origin.id,
            destination_id=destination.id,
            mode=mode,
            duration_minutes=duration_by_mode[mode],
            distance_meters=1800,
            source=self.name,
        )


_PLACE_TEMPLATES: dict[PlaceCategory, list[dict]] = {
    PlaceCategory.RESTAURANT: [
        {"name": "安静餐厅", "tags": ["安静", "聊天", "西餐"], "cost": 150, "rating": 4.6},
        {"name": "日料小馆", "tags": ["日料", "聊天", "安静"], "cost": 140, "rating": 4.6},
        {"name": "热气腾腾火锅", "tags": ["火锅", "热闹"], "cost": 130, "rating": 4.5},
        {"name": "韩式料理店", "tags": ["韩国料理", "韩餐"], "cost": 120, "rating": 4.6},
        {"name": "海底捞火锅", "tags": ["海底捞", "火锅"], "cost": 160, "rating": 4.7},
        {"name": "创意料理", "tags": ["氛围", "新鲜感"], "cost": 190, "rating": 4.5},
        {"name": "家常小馆", "tags": ["轻松", "性价比"], "cost": 90, "rating": 4.4},
        {"name": "炭火烧烤店", "tags": ["烧烤", "烤肉"], "cost": 100, "rating": 4.5},
    ],
    PlaceCategory.CAFE: [
        {"name": "湖畔咖啡", "tags": ["咖啡", "安静"], "cost": 55, "rating": 4.7},
        {"name": "庭院咖啡", "tags": ["咖啡", "拍照"], "cost": 65, "rating": 4.5},
    ],
    PlaceCategory.ATTRACTION: [
        {"name": "城市美术馆", "tags": ["展览", "安静", "博物馆"], "cost": 60, "rating": 4.7},
        {"name": "上海经典景点", "tags": ["景点", "经典", "拍照"], "cost": 40, "rating": 4.6},
        {"name": "湖边步道", "tags": ["散步", "自然"], "cost": 0, "rating": 4.6},
        {"name": "辅德里公园", "tags": ["公园", "散步"], "cost": 20, "rating": 4.3},
    ],
    PlaceCategory.ENTERTAINMENT: [
        {"name": "城市电影院", "tags": ["电影", "电影院", "室内"], "cost": 100, "rating": 4.7},
        {"name": "手作体验馆", "tags": ["手工", "互动"], "cost": 120, "rating": 4.6},
        {"name": "小型剧场", "tags": ["演出", "氛围"], "cost": 160, "rating": 4.5},
    ],
}


_DEMO_KEYWORD_ALIASES = {
    "西餐": ("西餐", "西餐厅"),
    "日料": ("日料", "日本料理"),
    "火锅": ("火锅",),
    "烧烤": ("烧烤", "烤肉"),
    "韩国料理": ("韩国料理", "韩餐", "韩国烤肉"),
    "海底捞": ("海底捞",),
    "电影院": ("电影院", "电影", "影院"),
    "博物馆": ("博物馆", "美术馆"),
    "景点": ("景点", "美术馆"),
}

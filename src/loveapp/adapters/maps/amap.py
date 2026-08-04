import asyncio
import math
import re
import time
from urllib.parse import quote

import httpx
from pydantic import SecretStr

from loveapp.domain.date_plan import Place, PlaceSearchRequest, Route
from loveapp.domain.enums import PlaceCategory, TransportMode


class AmapAPIError(RuntimeError):
    pass


class AmapMapProvider:
    name = "amap"

    def __init__(
        self,
        api_key: SecretStr,
        base_url: str = "https://restapi.amap.com",
        timeout_seconds: float = 20,
        page_size: int = 25,
        min_interval_seconds: float = 0.6,
        max_retries: int = 2,
        retry_backoff_seconds: float = 1.5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key.get_secret_value()
        self._page_size = min(max(page_size, 1), 25)
        self._min_interval_seconds = max(min_interval_seconds, 0)
        self._max_retries = max(max_retries, 0)
        self._retry_backoff_seconds = max(retry_backoff_seconds, 0)
        self._request_lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._region_cache: dict[str, str] = {}
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
        )

    async def search_places(self, request: PlaceSearchRequest) -> list[Place]:
        region = self._region_cache.setdefault(request.city, normalize_city_region(request.city))
        query_keywords = _query_keywords(request)
        responses = await asyncio.gather(
            *(
                self._get(
                    "/v5/place/text",
                    {
                        "types": type_codes,
                        "region": region,
                        "city_limit": "true",
                        "page_size": self._page_size,
                        "page_num": 1,
                        "show_fields": "business",
                        **({"keywords": query_keywords} if query_keywords else {}),
                    },
                )
                for type_codes in _TYPE_CODE_QUERIES[request.category]
            )
        )
        pois = _unique_pois(poi for data in responses for poi in data.get("pois") or [])
        places = [
            place
            for poi in pois
            if _poi_matches_region(poi, region)
            and (place := _parse_place(poi, request)) is not None
        ]

        if request.area:
            area_matches = [place for place in places if _place_matches_area(place, request.area)]
            if request.strict_area or area_matches:
                places = area_matches

        return sorted(
            places,
            key=lambda place: _place_rank(place, request),
            reverse=True,
        )

    async def route(
        self,
        origin: Place,
        destination: Place,
        mode: TransportMode,
    ) -> Route:
        if None in (origin.longitude, origin.latitude, destination.longitude, destination.latitude):
            raise AmapAPIError("路线规划需要完整的地点坐标。")

        endpoint = _ROUTE_ENDPOINTS[mode]
        params = {
            "origin": f"{origin.longitude},{origin.latitude}",
            "destination": f"{destination.longitude},{destination.latitude}",
            "show_fields": "cost",
        }
        if mode == TransportMode.TRANSIT:
            params.update(
                {
                    "city1": origin.citycode or origin.adcode or origin.city,
                    "city2": destination.citycode or destination.adcode or destination.city,
                }
            )

        data = await self._get(endpoint, params)
        route_data = data.get("route") or {}
        candidates = (
            route_data.get("transits") if mode == TransportMode.TRANSIT else route_data.get("paths")
        )
        if not candidates:
            info = data.get("info") or "OK"
            raise AmapAPIError(
                "高德没有返回可用路线 "
                f"(mode={mode.value}, origin={origin.name}, destination={destination.name}, "
                f"info={info})；请检查地点坐标、城市编码和公交服务范围。"
            )

        candidate = candidates[0]
        distance = _as_int(candidate.get("distance"))
        cost = candidate.get("cost") if isinstance(candidate.get("cost"), dict) else {}
        duration_seconds = _as_int(cost.get("duration") or candidate.get("duration"))
        if not duration_seconds:
            duration_seconds = _estimate_duration_seconds(distance, mode)

        return Route(
            origin_id=origin.id,
            destination_id=destination.id,
            mode=mode,
            duration_minutes=max(math.ceil(duration_seconds / 60), 1),
            distance_meters=distance,
            estimated_cost=_route_cost(cost, mode),
            source=self.name,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get(self, path: str, params: dict) -> dict:
        for attempt in range(self._max_retries + 1):
            data = await self._rate_limited_get(path, params)
            if data.get("status") == "1":
                return data

            info = data.get("info") or "UNKNOWN_ERROR"
            infocode = data.get("infocode") or "unknown"
            if infocode == "10021" and attempt < self._max_retries:
                await asyncio.sleep(self._retry_backoff_seconds * (attempt + 1))
                continue
            raise AmapAPIError(f"高德 API {path} 请求失败：{info} ({infocode})")
        raise AmapAPIError("高德 API 请求失败。")

    async def _rate_limited_get(self, path: str, params: dict) -> dict:
        async with self._request_lock:
            elapsed = time.monotonic() - self._last_request_at
            wait_seconds = self._min_interval_seconds - elapsed
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            response = await self._client.get(path, params={"key": self._api_key, **params})
            self._last_request_at = time.monotonic()
        response.raise_for_status()
        return response.json()


def _parse_place(poi: dict, request: PlaceSearchRequest) -> Place | None:
    longitude, latitude = _parse_location(poi.get("location"))
    if longitude is None or latitude is None:
        return None

    business = poi.get("business") if isinstance(poi.get("business"), dict) else {}
    type_name = _as_text(poi.get("type"))
    type_code = _as_text(poi.get("typecode"))
    if not _type_matches_category(type_code, request.category):
        return None

    cost_value = _as_float(business.get("cost"))
    cost_is_estimate = cost_value is None
    estimated_cost = (
        round(cost_value) if cost_value is not None else _DEFAULT_COSTS[request.category]
    )
    if request.max_cost_per_person is not None and estimated_cost > request.max_cost_per_person:
        return None

    tags = _place_tags(type_name, business, request.category)
    venue_text = " ".join(
        [
            _as_text(poi.get("name")),
            type_name,
            *tags,
        ]
    )
    searchable_text = " ".join(
        [
            _as_text(poi.get("name")),
            _as_text(poi.get("address")),
            _as_text(poi.get("adname")),
            _as_text(business.get("business_area")),
            *tags,
        ]
    )
    required_keywords = request.required_keywords or request.keywords
    # A venue requirement must match the POI identity, not merely its address.
    # Otherwise a park next to a museum subway station can satisfy "博物馆".
    matched_keywords = _match_keywords(required_keywords, venue_text)
    if required_keywords and len(matched_keywords) != len(set(required_keywords)):
        return None
    if _match_keywords(request.excluded_keywords, searchable_text):
        return None
    matched_preferences = list(
        dict.fromkeys(
            [*_match_preferences(request.preferences, searchable_text), *matched_keywords]
        )
    )
    name = _as_text(poi.get("name")) or "未命名地点"
    return Place(
        id=_as_text(poi.get("id")) or f"amap-{longitude}-{latitude}",
        name=name,
        city=request.city,
        address=_as_text(poi.get("address")) or _as_text(poi.get("adname")),
        category=request.category,
        tags=tags,
        matched_preferences=matched_preferences,
        estimated_cost_per_person=max(estimated_cost, 0),
        cost_is_estimate=cost_is_estimate,
        rating=_bounded_rating(business.get("rating")),
        type_name=type_name or None,
        type_code=type_code or None,
        business_area=_as_text(business.get("business_area")) or None,
        district=_as_text(poi.get("adname")) or None,
        adcode=_as_text(poi.get("adcode")) or None,
        citycode=_as_text(poi.get("citycode")) or None,
        opening_hours=(
            _as_text(business.get("opentime_today"))
            or _as_text(business.get("opentime_week"))
            or None
        ),
        telephone=_as_text(business.get("tel")) or None,
        map_url=_map_url(longitude, latitude, name),
        longitude=longitude,
        latitude=latitude,
        source="amap",
    )


def _place_rank(place: Place, request: PlaceSearchRequest) -> tuple:
    area_score = 1 if request.area and _place_matches_area(place, request.area) else 0
    primary_type = (place.type_code or "").split("|")[0]
    primary_category_score = int(_type_matches_category(primary_type, request.category))
    known_cost_score = int(not place.cost_is_estimate)
    return (
        area_score,
        len(place.matched_preferences),
        primary_category_score,
        known_cost_score,
        place.rating or 0,
    )


def _place_matches_area(place: Place, area: str) -> bool:
    normalized_area = area.removesuffix("区").removesuffix("县").strip()
    values = [place.address, place.business_area or "", place.district or ""]
    return any(normalized_area and normalized_area in value for value in values)


def _type_matches_category(type_code: str, category: PlaceCategory) -> bool:
    codes = [code.strip() for code in type_code.split("|") if code.strip()]
    prefixes = _CATEGORY_PREFIXES[category]
    return any(any(code.startswith(prefix) for prefix in prefixes) for code in codes)


def _place_tags(type_name: str, business: dict, category: PlaceCategory) -> list[str]:
    values = re.split(r"[;|,，]", type_name)
    for field_name in ("tag", "keytag", "rectag"):
        values.extend(re.split(r"[;|,，]", _as_text(business.get(field_name))))
    values.extend(_CATEGORY_TAGS[category])
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def _query_keywords(request: PlaceSearchRequest) -> str:
    values: list[str] = []
    if request.area:
        values.append(request.area)
    for keyword in [*request.keywords, *request.required_keywords]:
        query_keyword = _QUERY_KEYWORD_ALIASES.get(keyword, keyword)
        if query_keyword and query_keyword not in values:
            values.append(query_keyword)
    return " ".join(values)


def _match_keywords(keywords: list[str], searchable_text: str) -> list[str]:
    matched: list[str] = []
    for keyword in keywords:
        aliases = _KEYWORD_ALIASES.get(keyword, (keyword,))
        if any(alias in searchable_text for alias in aliases):
            matched.append(keyword)
    return list(dict.fromkeys(matched))


def _match_preferences(preferences: list[str], searchable_text: str) -> list[str]:
    matched = []
    for preference in preferences:
        aliases = _PREFERENCE_ALIASES.get(preference, (preference,))
        if any(alias in searchable_text for alias in aliases):
            matched.append(preference)
    return matched


def _parse_location(value) -> tuple[float | None, float | None]:
    text = _as_text(value)
    try:
        longitude, latitude = text.split(",", maxsplit=1)
        return float(longitude), float(latitude)
    except (TypeError, ValueError):
        return None, None


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ";".join(str(item) for item in value if item)
    return str(value)


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _bounded_rating(value) -> float | None:
    rating = _as_float(value)
    return min(max(rating, 0), 5) if rating is not None else None


def _estimate_duration_seconds(distance: int, mode: TransportMode) -> int:
    meters_per_minute = {
        TransportMode.WALKING: 75,
        TransportMode.CYCLING: 180,
        TransportMode.DRIVING: 350,
        TransportMode.TRANSIT: 200,
    }[mode]
    return math.ceil(distance / meters_per_minute) * 60


def _route_cost(cost: dict, mode: TransportMode) -> float | None:
    if mode in (TransportMode.WALKING, TransportMode.CYCLING):
        return 0
    if mode == TransportMode.TRANSIT:
        return _as_float(cost.get("transit_fee"))
    return _as_float(cost.get("tolls"))


def _map_url(longitude: float, latitude: float, name: str) -> str:
    encoded_name = quote(name)
    return (
        "https://uri.amap.com/marker"
        f"?position={longitude},{latitude}&name={encoded_name}"
        "&src=loveapp&coordinate=gaode&callnative=0"
    )


def normalize_city_region(city: str) -> str:
    """Return an Amap adcode for known cities, preserving numeric inputs."""

    cleaned = city.strip()
    if cleaned.isdigit():
        return cleaned
    normalized = cleaned.removesuffix("市")
    return _CITY_REGION_CODES.get(normalized, cleaned)


def _poi_matches_region(poi: dict, expected_region: str) -> bool:
    if not expected_region.isdigit():
        return True
    expected = expected_region
    # Municipality district adcodes are shaped like 310101 while the city
    # adcode is 310000; ordinary city adcodes generally share four digits.
    expected_city_prefix = expected[:3] if expected.endswith("000") else expected[:4]
    poi_adcode = _as_text(poi.get("adcode"))
    poi_citycode = _as_text(poi.get("citycode"))
    if poi_adcode and poi_adcode.isdigit():
        return poi_adcode == expected or poi_adcode.startswith(expected_city_prefix)
    if poi_citycode:
        expected_citycode = _CITYCODE_BY_ADCODE.get(expected, "")
        return not expected_citycode or poi_citycode == expected_citycode
    # Some Amap POI responses omit administrative metadata. Keep those
    # candidates; a response with explicit contradictory metadata is rejected.
    return True


def _unique_pois(pois) -> list[dict]:
    unique: dict[str, dict] = {}
    for poi in pois:
        poi_id = _as_text(poi.get("id"))
        key = poi_id or f"{_as_text(poi.get('name'))}:{_as_text(poi.get('location'))}"
        unique.setdefault(key, poi)
    return list(unique.values())


_TYPE_CODE_QUERIES = {
    PlaceCategory.RESTAURANT: ("050100|050200|050400",),
    PlaceCategory.CAFE: ("050500",),
    PlaceCategory.ATTRACTION: ("110000", "140100"),
    PlaceCategory.ENTERTAINMENT: ("080000",),
}

_CATEGORY_PREFIXES = {
    PlaceCategory.RESTAURANT: ("05",),
    PlaceCategory.CAFE: ("0505",),
    PlaceCategory.ATTRACTION: ("11", "1401"),
    PlaceCategory.ENTERTAINMENT: ("08",),
}

_DEFAULT_COSTS = {
    PlaceCategory.RESTAURANT: 100,
    PlaceCategory.CAFE: 45,
    PlaceCategory.ATTRACTION: 30,
    PlaceCategory.ENTERTAINMENT: 100,
}

_CATEGORY_TAGS = {
    PlaceCategory.RESTAURANT: ["餐厅", "用餐"],
    PlaceCategory.CAFE: ["咖啡"],
    PlaceCategory.ATTRACTION: ["景点"],
    PlaceCategory.ENTERTAINMENT: ["娱乐"],
}

_PREFERENCE_ALIASES = {
    "展览": ("展览", "博物馆", "美术馆", "艺术馆"),
    "咖啡": ("咖啡", "咖啡厅"),
    "散步": ("步道", "公园", "湿地", "风景区", "广场"),
    "自然": ("自然", "公园", "湿地", "湖", "山", "植物园"),
    "电影": ("电影院", "影剧院", "电影"),
    "演出": ("剧场", "剧院", "演出"),
    "手工": ("手工", "手作", "陶艺"),
}

_QUERY_KEYWORD_ALIASES = {
    "西餐": "西餐厅",
    "日料": "日本料理",
    "韩国料理": "韩国料理",
    "海底捞": "海底捞",
    "博物馆": "博物馆",
    "美术馆": "美术馆",
    "景点": "景点",
    "电影院": "电影院",
}

_KEYWORD_ALIASES = {
    "西餐": ("西餐", "西餐厅", "牛排", "意大利菜", "法餐", "西式餐饮"),
    "日料": ("日料", "日本料理", "寿司", "刺身"),
    "韩国料理": ("韩国料理", "韩餐", "韩国烤肉"),
    "海底捞": ("海底捞",),
    "火锅": ("火锅",),
    "烧烤": ("烧烤", "烤肉"),
    "素食": ("素食", "素菜"),
    "博物馆": ("博物馆",),
    "美术馆": ("美术馆", "艺术馆"),
    "景点": ("景点", "风景区", "旅游景点"),
    "公园": ("公园",),
    "电影院": ("电影院", "影城", "电影"),
    "剧场": ("剧场", "剧院"),
}

_ROUTE_ENDPOINTS = {
    TransportMode.WALKING: "/v5/direction/walking",
    TransportMode.DRIVING: "/v5/direction/driving",
    TransportMode.CYCLING: "/v5/direction/bicycling",
    TransportMode.TRANSIT: "/v5/direction/transit/integrated",
}


_CITY_REGION_CODES = {
    "北京": "110000",
    "上海": "310000",
    "广州": "440100",
    "深圳": "440300",
    "杭州": "330100",
    "南京": "320100",
    "苏州": "320500",
    "成都": "510100",
    "重庆": "500000",
    "武汉": "420100",
    "西安": "610100",
    "长沙": "430100",
    "天津": "120000",
    "青岛": "370200",
    "厦门": "350200",
    "郑州": "410100",
    "济南": "370100",
    "合肥": "340100",
    "福州": "350100",
    "昆明": "530100",
    "大连": "210200",
    "宁波": "330200",
    "无锡": "320200",
    "佛山": "440600",
    "东莞": "441900",
    "珠海": "440400",
    "三亚": "460200",
    "沈阳": "210100",
    "哈尔滨": "230100",
    "长春": "220100",
    "石家庄": "130100",
    "南昌": "360100",
    "贵阳": "520100",
    "南宁": "450100",
    "太原": "140100",
    "兰州": "620100",
    "乌鲁木齐": "650100",
    "呼和浩特": "150100",
    "海口": "460100",
    "泉州": "350500",
    "温州": "330300",
    "绍兴": "330600",
    "嘉兴": "330400",
}

_CITYCODE_BY_ADCODE = {
    "110000": "010",
    "310000": "021",
    "440100": "020",
    "440300": "0755",
    "330100": "0571",
    "320100": "025",
    "320500": "0512",
    "510100": "028",
    "500000": "023",
    "420100": "027",
    "610100": "029",
    "430100": "0731",
    "120000": "022",
    "370200": "0532",
    "350200": "0592",
    "410100": "0371",
    "370100": "0531",
    "340100": "0551",
    "350100": "0591",
    "530100": "0871",
    "210200": "0411",
    "330200": "0574",
    "320200": "0510",
    "440600": "0757",
    "441900": "0769",
    "440400": "0756",
    "460200": "0898",
    "210100": "024",
    "230100": "0451",
    "220100": "0431",
    "130100": "0311",
    "360100": "0791",
    "520100": "0851",
    "450100": "0771",
    "140100": "0351",
    "620100": "0931",
    "650100": "0991",
    "150100": "0471",
    "460100": "0898",
    "350500": "0595",
    "330300": "0577",
    "330600": "0575",
    "330400": "0573",
}

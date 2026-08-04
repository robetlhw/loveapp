import httpx
import pytest
from pydantic import SecretStr

from loveapp.adapters.maps.amap import AmapAPIError, AmapMapProvider, normalize_city_region
from loveapp.domain.date_plan import Place, PlaceSearchRequest
from loveapp.domain.enums import PlaceCategory, TransportMode


async def test_amap_search_prefers_area_and_parses_business_data() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v5/place/text"
        assert request.url.params["types"] == "050100|050200|050400"
        assert request.url.params["region"] == "330100"
        return httpx.Response(
            200,
            json={
                "status": "1",
                "info": "OK",
                "pois": [
                    {
                        "id": "hotel",
                        "name": "混合型酒店",
                        "type": "住宿服务;宾馆酒店|餐饮服务;餐饮相关场所",
                        "typecode": "100103|050000",
                        "address": "萧山区示例地址",
                        "adname": "萧山区",
                        "location": "120.40,30.18",
                        "business": {"rating": "4.8"},
                    },
                    {
                        "id": "restaurant",
                        "name": "西湖素食餐厅",
                        "type": "餐饮服务;中餐厅;中式素菜馆",
                        "typecode": "050120",
                        "address": "西湖区示例地址",
                        "adname": "西湖区",
                        "adcode": "330106",
                        "citycode": "0571",
                        "location": "120.15,30.25",
                        "business": {
                            "cost": "180.00",
                            "rating": "4.6",
                            "business_area": "西湖",
                            "opentime_today": "11:00-20:00",
                            "tel": "0571-12345678",
                            "tag": "素菜,安静",
                        },
                    },
                ],
            },
        )

    client = httpx.AsyncClient(
        base_url="https://restapi.amap.com",
        transport=httpx.MockTransport(handler),
    )
    provider = AmapMapProvider(SecretStr("test-key"), client=client)

    places = await provider.search_places(
        PlaceSearchRequest(
            city="杭州",
            area="西湖",
            category=PlaceCategory.RESTAURANT,
            preferences=["安静"],
            max_cost_per_person=200,
        )
    )

    assert [place.id for place in places] == ["restaurant"]
    assert places[0].estimated_cost_per_person == 180
    assert places[0].cost_is_estimate is False
    assert places[0].matched_preferences == ["安静"]
    assert places[0].opening_hours == "11:00-20:00"
    assert places[0].district == "西湖区"
    assert places[0].map_url and places[0].map_url.startswith("https://uri.amap.com/")
    await client.aclose()


def test_amap_normalizes_common_city_names_to_adcodes() -> None:
    assert normalize_city_region("上海") == "310000"
    assert normalize_city_region("上海市") == "310000"
    assert normalize_city_region("310000") == "310000"


async def test_amap_discards_pois_from_a_different_city() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "1",
                "info": "OK",
                "pois": [
                    {
                        "id": "beijing",
                        "name": "北京餐厅",
                        "typecode": "050100",
                        "adcode": "110000",
                        "citycode": "010",
                        "location": "116.40,39.90",
                    },
                    {
                        "id": "shanghai",
                        "name": "上海餐厅",
                        "typecode": "050100",
                        "adcode": "310101",
                        "citycode": "021",
                        "location": "121.47,31.23",
                    },
                ],
            },
        )

    client = httpx.AsyncClient(
        base_url="https://restapi.amap.com",
        transport=httpx.MockTransport(handler),
    )
    provider = AmapMapProvider(
        SecretStr("test-key"),
        min_interval_seconds=0,
        client=client,
    )

    places = await provider.search_places(
        PlaceSearchRequest(city="上海", category=PlaceCategory.RESTAURANT)
    )

    assert [place.id for place in places] == ["shanghai"]
    await client.aclose()


async def test_amap_search_uses_area_and_exact_dining_keyword() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["region"] == "310000"
        assert request.url.params["city_limit"] == "true"
        assert request.url.params["keywords"] == "静安区 西餐厅"
        return httpx.Response(
            200,
            json={
                "status": "1",
                "info": "OK",
                "pois": [
                    {
                        "id": "wrong-area",
                        "name": "浦东西餐厅",
                        "type": "餐饮服务;西餐厅",
                        "typecode": "050100",
                        "adname": "浦东新区",
                        "adcode": "310115",
                        "location": "121.55,31.20",
                    },
                    {
                        "id": "generic-restaurant",
                        "name": "静安中餐厅",
                        "type": "餐饮服务;中餐厅",
                        "typecode": "050120",
                        "adname": "静安区",
                        "adcode": "310106",
                        "location": "121.45,31.23",
                    },
                    {
                        "id": "western",
                        "name": "静安西餐厅",
                        "type": "餐饮服务;西餐厅",
                        "typecode": "050100",
                        "adname": "静安区",
                        "adcode": "310106",
                        "location": "121.46,31.23",
                        "business": {"cost": "180", "rating": "4.7"},
                    },
                ],
            },
        )

    client = httpx.AsyncClient(
        base_url="https://restapi.amap.com",
        transport=httpx.MockTransport(handler),
    )
    provider = AmapMapProvider(SecretStr("test-key"), min_interval_seconds=0, client=client)

    places = await provider.search_places(
        PlaceSearchRequest(
            city="上海",
            area="静安区",
            category=PlaceCategory.RESTAURANT,
            keywords=["西餐"],
            required_keywords=["西餐"],
            max_cost_per_person=200,
        )
    )

    assert [place.id for place in places] == ["western"]
    assert places[0].matched_preferences == ["西餐"]
    await client.aclose()


async def test_amap_search_requires_requested_museum_keyword() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["keywords"] == "静安区 博物馆"
        return httpx.Response(
            200,
            json={
                "status": "1",
                "info": "OK",
                "pois": [
                    {
                        "id": "park",
                        "name": "静安公园",
                        "address": "自然博物馆地铁站旁",
                        "type": "风景名胜;公园广场",
                        "typecode": "110101",
                        "adname": "静安区",
                        "adcode": "310106",
                        "location": "121.46,31.23",
                    },
                    {
                        "id": "museum",
                        "name": "静安博物馆",
                        "type": "科教文化服务;博物馆",
                        "typecode": "140100",
                        "adname": "静安区",
                        "adcode": "310106",
                        "location": "121.47,31.23",
                    },
                ],
            },
        )

    client = httpx.AsyncClient(
        base_url="https://restapi.amap.com",
        transport=httpx.MockTransport(handler),
    )
    provider = AmapMapProvider(SecretStr("test-key"), min_interval_seconds=0, client=client)

    places = await provider.search_places(
        PlaceSearchRequest(
            city="上海",
            area="静安区",
            category=PlaceCategory.ATTRACTION,
            keywords=["博物馆"],
            required_keywords=["博物馆"],
        )
    )

    assert [place.id for place in places] == ["museum"]
    await client.aclose()


async def test_amap_empty_route_explains_route_context() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "1", "info": "OK", "route": {"transits": []}},
        )

    client = httpx.AsyncClient(
        base_url="https://restapi.amap.com",
        transport=httpx.MockTransport(handler),
    )
    provider = AmapMapProvider(SecretStr("test-key"), min_interval_seconds=0, client=client)

    with pytest.raises(AmapAPIError, match="mode=transit"):
        await provider.route(
            _place("origin", 121.47, 31.23),
            _place("destination", 121.48, 31.24),
            TransportMode.TRANSIT,
        )

    await client.aclose()


async def test_amap_transit_route_parses_duration_distance_and_fee() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v5/direction/transit/integrated"
        assert request.url.params["show_fields"] == "cost"
        return httpx.Response(
            200,
            json={
                "status": "1",
                "info": "OK",
                "route": {
                    "transits": [
                        {
                            "distance": "3670",
                            "cost": {"duration": "1711", "transit_fee": "2.0"},
                        }
                    ]
                },
            },
        )

    client = httpx.AsyncClient(
        base_url="https://restapi.amap.com",
        transport=httpx.MockTransport(handler),
    )
    provider = AmapMapProvider(SecretStr("test-key"), client=client)
    origin = _place("origin", 120.15, 30.27)
    destination = _place("destination", 120.13, 30.25)

    route = await provider.route(origin, destination, TransportMode.TRANSIT)

    assert route.distance_meters == 3670
    assert route.duration_minutes == 29
    assert route.estimated_cost == 2
    await client.aclose()


async def test_amap_retries_qps_limit() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                200,
                json={
                    "status": "0",
                    "info": "CUQPS_HAS_EXCEEDED_THE_LIMIT",
                    "infocode": "10021",
                },
            )
        return httpx.Response(200, json={"status": "1", "info": "OK", "pois": []})

    client = httpx.AsyncClient(
        base_url="https://restapi.amap.com",
        transport=httpx.MockTransport(handler),
    )
    provider = AmapMapProvider(
        SecretStr("test-key"),
        min_interval_seconds=0,
        max_retries=1,
        retry_backoff_seconds=0,
        client=client,
    )

    places = await provider.search_places(
        PlaceSearchRequest(city="杭州", category=PlaceCategory.CAFE)
    )

    assert places == []
    assert attempts == 2
    await client.aclose()


def _place(place_id: str, longitude: float, latitude: float) -> Place:
    return Place(
        id=place_id,
        name=place_id,
        city="杭州",
        address="测试地址",
        category=PlaceCategory.ATTRACTION,
        estimated_cost_per_person=0,
        adcode="330100",
        longitude=longitude,
        latitude=latitude,
        source="test",
    )

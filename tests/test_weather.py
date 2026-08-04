from datetime import date

import httpx
from pydantic import SecretStr

from loveapp.adapters.weather import AmapWeatherProvider, DemoWeatherProvider
from loveapp.domain.weather import WeatherRequest


async def test_demo_weather_provider_is_deterministic() -> None:
    forecast = await DemoWeatherProvider().forecast(
        WeatherRequest(city="杭州", date=date(2026, 7, 25))
    )

    assert forecast.condition == "晴"
    assert forecast.favors_indoor is False


async def test_amap_weather_provider_parses_requested_forecast() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/weather/weatherInfo"
        assert request.url.params["city"] == "310000"
        return httpx.Response(
            200,
            json={
                "status": "1",
                "info": "OK",
                "forecasts": [
                    {
                        "casts": [
                            {
                                "date": "2026-07-25",
                                "dayweather": "中雨",
                                "daytemp": "28",
                                "nighttemp": "23",
                                "daywind": "北风",
                            }
                        ]
                    }
                ],
            },
        )

    client = httpx.AsyncClient(
        base_url="https://restapi.amap.com",
        transport=httpx.MockTransport(handler),
    )
    provider = AmapWeatherProvider(
        SecretStr("test-key"),
        min_interval_seconds=0,
        client=client,
    )

    forecast = await provider.forecast(
        WeatherRequest(city="上海", date=date(2026, 7, 25))
    )

    assert forecast is not None
    assert forecast.rain_probability == 70
    assert forecast.favors_indoor is True
    await client.aclose()

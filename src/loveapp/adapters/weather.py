import asyncio
import time

import httpx
from pydantic import SecretStr

from loveapp.adapters.maps.amap import normalize_city_region
from loveapp.domain.weather import WeatherForecast, WeatherRequest


class DisabledWeatherProvider:
    name = "disabled-weather"

    async def forecast(self, request: WeatherRequest) -> WeatherForecast | None:
        del request
        return None

    async def aclose(self) -> None:
        return None


class DemoWeatherProvider:
    name = "demo-weather"

    async def forecast(self, request: WeatherRequest) -> WeatherForecast:
        # Deterministic demo data keeps tests and offline development stable.
        return WeatherForecast(
            city=request.city,
            date=request.date,
            condition="晴",
            temperature_high=26,
            temperature_low=18,
            rain_probability=10,
            wind="微风",
            source=self.name,
        )

    async def aclose(self) -> None:
        return None


class AmapWeatherProvider:
    name = "amap-weather"

    def __init__(
        self,
        api_key: SecretStr,
        *,
        base_url: str = "https://restapi.amap.com",
        timeout_seconds: float = 20,
        min_interval_seconds: float = 0.6,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key.get_secret_value()
        self._min_interval_seconds = max(min_interval_seconds, 0)
        self._last_request_at = 0.0
        self._request_lock = asyncio.Lock()
        self._cache: dict[tuple[str, str], WeatherForecast | None] = {}
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
        )

    async def forecast(self, request: WeatherRequest) -> WeatherForecast | None:
        cache_key = (normalize_city_region(request.city), request.date.isoformat())
        if cache_key in self._cache:
            return self._cache[cache_key]
        data = await self._get(
            "/v3/weather/weatherInfo",
            {
                "city": normalize_city_region(request.city),
                "extensions": "all",
            },
        )
        forecasts = data.get("forecasts") or []
        if not forecasts:
            self._cache[cache_key] = None
            return None
        casts = forecasts[0].get("casts") or []
        cast = next(
            (value for value in casts if value.get("date") == request.date.isoformat()),
            None,
        )
        if cast is None:
            self._cache[cache_key] = None
            return None
        condition = str(cast.get("dayweather") or cast.get("nightweather") or "未知")
        forecast = WeatherForecast(
            city=request.city,
            date=request.date,
            condition=condition,
            temperature_high=_as_int(cast.get("daytemp")),
            temperature_low=_as_int(cast.get("nighttemp")),
            rain_probability=_rain_probability(condition),
            wind=str(cast.get("daywind") or cast.get("nightwind") or "") or None,
            source=self.name,
        )
        self._cache[cache_key] = forecast
        return forecast

    async def _get(self, path: str, params: dict[str, str]) -> dict:
        async with self._request_lock:
            elapsed = time.monotonic() - self._last_request_at
            wait_seconds = self._min_interval_seconds - elapsed
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            response = await self._client.get(path, params={"key": self._api_key, **params})
            self._last_request_at = time.monotonic()
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "1":
            raise RuntimeError(
                f"高德天气 API 请求失败：{data.get('info', 'UNKNOWN_ERROR')} "
                f"({data.get('infocode', 'unknown')})"
            )
        return data

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _as_int(value: object) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _rain_probability(condition: str) -> int:
    if any(value in condition for value in ("暴雨", "大雨", "雷", "雪")):
        return 90
    if "雨" in condition:
        return 70
    return 10

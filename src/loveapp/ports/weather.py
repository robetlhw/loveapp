from typing import Protocol

from loveapp.domain.weather import WeatherForecast, WeatherRequest


class WeatherProvider(Protocol):
    name: str

    async def forecast(self, request: WeatherRequest) -> WeatherForecast | None: ...

    async def aclose(self) -> None: ...

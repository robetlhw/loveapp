from datetime import date

from pydantic import BaseModel, Field


class WeatherRequest(BaseModel):
    city: str = Field(min_length=1)
    date: date


class WeatherForecast(BaseModel):
    city: str
    date: date
    condition: str
    temperature_high: int | None = None
    temperature_low: int | None = None
    rain_probability: int | None = Field(default=None, ge=0, le=100)
    wind: str | None = None
    source: str

    @property
    def favors_indoor(self) -> bool:
        condition = self.condition.casefold()
        return (
            (self.rain_probability or 0) >= 50
            or any(value in condition for value in ("雨", "雪", "雷", "storm", "rain"))
        )

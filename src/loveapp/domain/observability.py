from enum import StrEnum

from pydantic import BaseModel, Field


class TimingStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class StepTiming(BaseModel):
    name: str
    duration_ms: float = Field(ge=0)
    started_offset_ms: float = Field(ge=0)
    status: TimingStatus = TimingStatus.COMPLETED
    error: str | None = None
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class TimingEvent(BaseModel):
    name: str
    phase: str
    duration_ms: float | None = None
    error: str | None = None

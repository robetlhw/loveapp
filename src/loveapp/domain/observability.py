from enum import StrEnum

from pydantic import BaseModel, Field, JsonValue


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
    # Trace details are diagnostic JSON, not a scalar-only metric map.  Some
    # stages (for example two-stage extraction routing) need bounded nested
    # lists and objects to remain observable without changing domain writes.
    details: dict[str, JsonValue] = Field(default_factory=dict)


class TimingEvent(BaseModel):
    name: str
    phase: str
    duration_ms: float | None = None
    error: str | None = None

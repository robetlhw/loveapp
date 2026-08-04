from contextlib import AbstractContextManager
from typing import Protocol

TraceDetails = dict[str, str | int | float | bool | None]


class TraceRecorder(Protocol):
    def measure(self, name: str) -> AbstractContextManager[TraceDetails]: ...

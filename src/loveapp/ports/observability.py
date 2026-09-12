from contextlib import AbstractContextManager
from typing import Protocol

from pydantic import JsonValue

TraceDetails = dict[str, JsonValue]


class TraceRecorder(Protocol):
    def measure(self, name: str) -> AbstractContextManager[TraceDetails]: ...

import asyncio
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from time import perf_counter
from typing import Any

from loveapp.domain.observability import StepTiming, TimingEvent, TimingStatus

TimingCallback = Callable[[TimingEvent], None]


class ExecutionTrace:
    def __init__(self, callback: TimingCallback | None = None) -> None:
        self._origin = perf_counter()
        self._callback = callback
        self.records: list[StepTiming] = []
        self._background_tasks: set[asyncio.Task] = set()
        self._active_steps: dict[
            int,
            tuple[str, float, float, dict[str, str | int | float | bool | None]],
        ] = {}

    def create_task(
        self,
        coroutine: Coroutine[Any, Any, Any],
        *,
        name: str,
    ) -> asyncio.Task:
        task = asyncio.create_task(coroutine, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        task.add_done_callback(_consume_task_exception)
        return task

    def track_task(self, task: asyncio.Task) -> None:
        if task in self._background_tasks:
            return
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        task.add_done_callback(_consume_task_exception)

    async def cancel_background_tasks(self) -> None:
        tasks = [task for task in self._background_tasks if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @contextmanager
    def measure(
        self,
        name: str,
    ) -> Iterator[dict[str, str | int | float | bool | None]]:
        started = perf_counter()
        offset_ms = (started - self._origin) * 1000
        details: dict[str, str | int | float | bool | None] = {}
        step_id = id(details)
        self._active_steps[step_id] = (name, started, offset_ms, details)
        self._emit(TimingEvent(name=name, phase="started"))
        try:
            yield details
        except BaseException as exc:
            self._active_steps.pop(step_id, None)
            duration_ms = (perf_counter() - started) * 1000
            error = str(exc)[:500]
            record = StepTiming(
                name=name,
                duration_ms=duration_ms,
                started_offset_ms=offset_ms,
                status=TimingStatus.FAILED,
                error=error,
                details=details,
            )
            self.records.append(record)
            self._emit(
                TimingEvent(
                    name=name,
                    phase="failed",
                    duration_ms=duration_ms,
                    error=error,
                )
            )
            raise
        else:
            self._active_steps.pop(step_id, None)
            duration_ms = (perf_counter() - started) * 1000
            self.records.append(
                StepTiming(
                    name=name,
                    duration_ms=duration_ms,
                    started_offset_ms=offset_ms,
                    details=details,
                )
            )
            self._emit(
                TimingEvent(
                    name=name,
                    phase="completed",
                    duration_ms=duration_ms,
                )
            )

    def snapshot(self) -> list[StepTiming]:
        now = perf_counter()
        running = [
            StepTiming(
                name=name,
                duration_ms=(now - started) * 1000,
                started_offset_ms=offset_ms,
                status=TimingStatus.RUNNING,
                details=dict(details),
            )
            for name, started, offset_ms, details in self._active_steps.values()
        ]
        return sorted([*self.records, *running], key=lambda item: item.started_offset_ms)

    @property
    def failed_step(self) -> StepTiming | None:
        failures = [record for record in self.records if record.status == TimingStatus.FAILED]
        return next(
            (record for record in reversed(failures) if record.name != "total"),
            failures[-1] if failures else None,
        )

    def _emit(self, event: TimingEvent) -> None:
        if self._callback is None:
            return
        try:
            self._callback(event)
        except Exception:
            return


def _consume_task_exception(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    task.exception()

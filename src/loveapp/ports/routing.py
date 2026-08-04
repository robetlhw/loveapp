from typing import Protocol

from loveapp.domain.routing import RouteCorrection, RouteInput, RouteResult


class RouteCorrector(Protocol):
    async def correct(
        self,
        route_input: RouteInput,
        rule_result: RouteResult,
    ) -> RouteCorrection: ...

    async def aclose(self) -> None: ...


class Router(Protocol):
    async def route(self, route_input: RouteInput) -> RouteResult: ...

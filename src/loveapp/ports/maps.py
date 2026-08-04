from typing import Protocol

from loveapp.domain.date_plan import Place, PlaceSearchRequest, Route
from loveapp.domain.enums import TransportMode


class MapProvider(Protocol):
    name: str

    async def search_places(self, request: PlaceSearchRequest) -> list[Place]: ...

    async def route(
        self,
        origin: Place,
        destination: Place,
        mode: TransportMode,
    ) -> Route: ...

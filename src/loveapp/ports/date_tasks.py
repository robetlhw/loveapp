from typing import Protocol

from loveapp.domain.date_task import DatePlanningTaskState


class DatePlanningTaskStore(Protocol):
    async def get(
        self,
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str,
    ) -> DatePlanningTaskState | None: ...

    async def save(self, state: DatePlanningTaskState) -> DatePlanningTaskState: ...

    async def delete(
        self,
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str,
    ) -> bool: ...

    async def aclose(self) -> None: ...

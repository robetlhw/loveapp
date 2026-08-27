from typing import Protocol

from loveapp.domain.date_operations import DatePlanOperation, DateSemanticParseResult
from loveapp.domain.runtime_context import RuntimeContext


class DateSemanticParser(Protocol):
    async def parse_date_operations(
        self,
        text: str,
        runtime_context: RuntimeContext | None,
        deterministic_operations: tuple[DatePlanOperation, ...],
    ) -> DateSemanticParseResult: ...

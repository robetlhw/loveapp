from collections import defaultdict
from datetime import timedelta

from loveapp.application.date_planning.validation import DatePlanValidator
from loveapp.domain.date_constraints import build_date_constraints
from loveapp.domain.date_plan import DatePlan, DatePlanDay, DatePlanItem, DatePlanRequest, Place
from loveapp.domain.enums import DatePlanMutation
from loveapp.ports.maps import MapProvider


class DatePlanMutator:
    """Focused mutations that operate on an existing DatePlan snapshot."""

    def __init__(
        self, map_provider: MapProvider, validator: DatePlanValidator | None = None
    ) -> None:
        self._map_provider = map_provider
        self._validator = validator or DatePlanValidator()

    async def apply(
        self,
        existing_plan: DatePlan,
        request: DatePlanRequest,
        mutation: DatePlanMutation,
    ) -> DatePlan | None:
        if mutation == DatePlanMutation.REORDER:
            return await self.reorder(existing_plan, request)
        if mutation == DatePlanMutation.UPDATE_CONSTRAINT:
            return self.update_constraint(existing_plan, request)
        return None

    async def reorder(self, existing_plan: DatePlan, request: DatePlanRequest) -> DatePlan:
        grouped: dict[int, list[DatePlanItem]] = defaultdict(list)
        for item in existing_plan.items:
            grouped[item.day_index].append(item)
        rebuilt: list[DatePlanItem] = []
        for day_index in sorted(grouped):
            items = sorted(grouped[day_index], key=_reorder_key)
            previous: Place | None = None
            for order, item in enumerate(items, start=1):
                route = None
                if previous is not None:
                    try:
                        route = await self._map_provider.route(
                            previous, item.place, request.transport_mode
                        )
                    except Exception:
                        route = None
                rebuilt.append(
                    item.model_copy(update={"order": order, "route_from_previous": route})
                )
                previous = item.place
        return _with_items(existing_plan, rebuilt, request)

    def update_constraint(
        self, existing_plan: DatePlan, request: DatePlanRequest
    ) -> DatePlan | None:
        candidate = _with_items(existing_plan, existing_plan.items, request)
        return (
            candidate
            if self._validator.validate(candidate, request, build_date_constraints(request)).valid
            else None
        )


def _reorder_key(item: DatePlanItem) -> tuple[int, int]:
    meal_order = {"breakfast": 0, "lunch": 1, "dinner": 3}
    return meal_order.get(item.meal_type or "", 2), item.order


def _with_items(
    existing: DatePlan, items: list[DatePlanItem], request: DatePlanRequest
) -> DatePlan:
    by_day: dict[int, list[DatePlanItem]] = defaultdict(list)
    for item in items:
        scheduled_date = (
            request.date + timedelta(days=item.day_index - 1)
            if request.date is not None
            else item.scheduled_date
        )
        by_day[item.day_index].append(item.model_copy(update={"scheduled_date": scheduled_date}))
    days = [
        DatePlanDay(
            day_index=day_index,
            date=(request.date if request.date is not None else existing.start_date),
            items=day_items,
            total_estimated_cost=sum(item.estimated_cost for item in day_items),
            total_duration_minutes=sum(item.duration_minutes for item in day_items),
        )
        for day_index, day_items in sorted(by_day.items())
    ]
    return existing.model_copy(
        update={
            "plan_mode": request.plan_mode,
            "start_date": request.date,
            "end_date": request.end_date,
            "day_count": request.day_count,
            "nights": request.nights,
            "items": [item for day in days for item in day.items],
            "days": days if request.day_count > 1 else [],
            "total_estimated_cost": sum(item.estimated_cost for item in items),
        }
    )

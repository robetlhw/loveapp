from pydantic import BaseModel, Field

from loveapp.domain.date_plan import DatePlan, DatePlanItem
from loveapp.domain.date_task import DatePlanningTaskState, DateTaskDiff, DateTaskFieldChange


class DatePlanDiff(BaseModel):
    added_place_ids: list[str] = Field(default_factory=list)
    removed_place_ids: list[str] = Field(default_factory=list)
    moved_place_ids: list[str] = Field(default_factory=list)
    unchanged_place_ids: list[str] = Field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added_place_ids or self.removed_place_ids or self.moved_place_ids)


def diff_date_plans(previous: DatePlan | None, current: DatePlan | None) -> DatePlanDiff:
    before = {_item_key(item): item for item in previous.items} if previous is not None else {}
    after = {_item_key(item): item for item in current.items} if current is not None else {}
    added = [place_id for day, place_id in after if (day, place_id) not in before]
    removed = [place_id for day, place_id in before if (day, place_id) not in after]
    moved: list[str] = []
    unchanged: list[str] = []
    for key in after.keys() & before.keys():
        target = (
            moved
            if _placement_signature(before[key]) != _placement_signature(after[key])
            else unchanged
        )
        target.append(key[1])
    return DatePlanDiff(
        added_place_ids=added,
        removed_place_ids=removed,
        moved_place_ids=sorted(moved),
        unchanged_place_ids=sorted(unchanged),
    )


def _item_key(item: DatePlanItem) -> tuple[int, str]:
    return item.day_index, item.place.id


def diff_date_tasks(
    previous: DatePlanningTaskState,
    current: DatePlanningTaskState,
) -> DateTaskDiff:
    changes: dict[str, DateTaskFieldChange] = {}
    for field in (
        "city",
        "area",
        "budget",
        "budget_scope",
        "date",
        "start_time",
        "transport_mode",
    ):
        before = getattr(previous, field)
        after = getattr(current, field)
        if before != after:
            changes[field] = DateTaskFieldChange(before=before, after=after)
    before_requirements = [
        requirement.model_dump(mode="json") for requirement in previous.requirements
    ]
    after_requirements = [
        requirement.model_dump(mode="json") for requirement in current.requirements
    ]
    if before_requirements != after_requirements:
        changes["requirements"] = DateTaskFieldChange(
            before=before_requirements,
            after=after_requirements,
        )
    return DateTaskDiff(changes=changes)


def _placement_signature(item: DatePlanItem) -> tuple[object, ...]:
    return (
        item.day_index,
        item.order,
        item.meal_type,
        item.time_label,
        item.after_item,
        item.slot_keyword,
    )

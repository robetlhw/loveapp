from pydantic import BaseModel, Field

from loveapp.domain.date_plan import DatePlan, DatePlanItem


class DatePlanDiff(BaseModel):
    added_place_ids: list[str] = Field(default_factory=list)
    removed_place_ids: list[str] = Field(default_factory=list)
    moved_place_ids: list[str] = Field(default_factory=list)
    unchanged_place_ids: list[str] = Field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added_place_ids or self.removed_place_ids or self.moved_place_ids)


def diff_date_plans(previous: DatePlan | None, current: DatePlan | None) -> DatePlanDiff:
    before = {item.place.id: item for item in previous.items} if previous is not None else {}
    after = {item.place.id: item for item in current.items} if current is not None else {}
    added = [place_id for place_id in after if place_id not in before]
    removed = [place_id for place_id in before if place_id not in after]
    moved: list[str] = []
    unchanged: list[str] = []
    for place_id in after.keys() & before.keys():
        target = (
            moved
            if _placement_signature(before[place_id]) != _placement_signature(after[place_id])
            else unchanged
        )
        target.append(place_id)
    return DatePlanDiff(
        added_place_ids=added,
        removed_place_ids=removed,
        moved_place_ids=sorted(moved),
        unchanged_place_ids=sorted(unchanged),
    )


def _placement_signature(item: DatePlanItem) -> tuple[object, ...]:
    return (
        item.day_index,
        item.order,
        item.meal_type,
        item.time_label,
        item.after_item,
        item.slot_keyword,
    )

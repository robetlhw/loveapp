import json
import re
import unicodedata
from dataclasses import dataclass

from loveapp.application.date_planning.operation_validation import (
    DateOperationVerifier,
    RejectedDateOperation,
)
from loveapp.domain.date_operations import (
    DateConstraintField,
    DateOperationType,
    DatePlanOperation,
    DesiredDateStop,
    MealType,
    StopKind,
    StopReference,
    TemporalAnchor,
    TimeWindow,
)
from loveapp.domain.date_patch import DatePlanPatch
from loveapp.domain.runtime_context import RuntimeContext


@dataclass(frozen=True)
class DateOperationResolution:
    candidates: tuple[DatePlanOperation, ...]
    operations: tuple[DatePlanOperation, ...]
    rejected: tuple[RejectedDateOperation, ...]


class DateOperationResolver:
    """Resolve current-turn date edits without mutating committed state."""

    def __init__(self, verifier: DateOperationVerifier | None = None) -> None:
        self._verifier = verifier or DateOperationVerifier()

    def resolve(
        self,
        text: str,
        runtime_context: RuntimeContext | None,
        current_turn_patch: DatePlanPatch,
        *,
        proposed_operations: list[DatePlanOperation] | None = None,
    ) -> DateOperationResolution:
        deterministic = _deterministic_operations(text, runtime_context, current_turn_patch)
        candidates = [*deterministic, *(proposed_operations or [])]
        verification = self._verifier.verify(
            candidates,
            text,
            runtime_context,
            current_turn_patch,
        )
        operations = tuple(_dedupe_operations(verification.accepted))
        return DateOperationResolution(
            candidates=tuple(candidates),
            operations=operations,
            rejected=verification.rejected,
        )


def _deterministic_operations(
    text: str,
    runtime_context: RuntimeContext | None,
    patch: DatePlanPatch,
) -> list[DatePlanOperation]:
    operations = _constraint_operations(text, patch)
    normalized = _normalize(text)
    if _REPLAN_CUE.search(normalized):
        operations.append(
            DatePlanOperation(
                type=DateOperationType.REPLAN,
                source_span=text,
                confidence=1,
            )
        )

    replacement_operations, replacement_keywords = _replacement_operations(text)
    operations.extend(replacement_operations)
    removal_operations, removal_keywords = _removal_operations(text)
    operations.extend(removal_operations)

    excluded = {*replacement_keywords, *removal_keywords}
    for desired, source_span in _desired_stops(text):
        if desired.keyword in excluded:
            continue
        matching_items = _matching_current_items(runtime_context, desired)
        if matching_items:
            if _has_placement_requirement(desired) and not any(
                _placement_satisfied(item, desired, runtime_context)
                for item in matching_items
            ):
                operations.append(
                    DatePlanOperation(
                        type=DateOperationType.MOVE_STOP,
                        target=_reference_for_item(matching_items[0], desired),
                        payload=desired,
                        source_span=source_span,
                        confidence=1,
                    )
                )
            continue
        operations.append(
            DatePlanOperation(
                type=DateOperationType.ADD_STOP,
                payload=desired,
                source_span=source_span,
                confidence=1,
            )
        )
    return operations


def _constraint_operations(text: str, patch: DatePlanPatch) -> list[DatePlanOperation]:
    operations: list[DatePlanOperation] = []
    for field in DateConstraintField:
        value = getattr(patch, field.value, None)
        if value is None:
            continue
        operations.append(
            DatePlanOperation(
                type=DateOperationType.UPDATE_CONSTRAINT,
                constraint_field=field,
                constraint_value=value,
                source_span=text,
                confidence=1,
            )
        )
    return operations


def _replacement_operations(text: str) -> tuple[list[DatePlanOperation], set[str]]:
    operations: list[DatePlanOperation] = []
    used_keywords: set[str] = set()
    for match in _REPLACEMENT_PATTERN.finditer(text):
        target = _first_stop(match.group("target"))
        replacement = _first_stop(match.group("replacement"))
        if target is None or replacement is None:
            continue
        target_keyword, _ = target
        replacement_keyword, replacement_kind = replacement
        used_keywords.update((target_keyword, replacement_keyword))
        operations.append(
            DatePlanOperation(
                type=DateOperationType.REPLACE_STOP,
                target=StopReference(keyword=target_keyword),
                payload=DesiredDateStop(
                    kind=replacement_kind,
                    keyword=replacement_keyword,
                    meal_type=(
                        _meal_type(match.group(0))
                        if replacement_kind in {StopKind.DINING, StopKind.CAFE}
                        else None
                    ),
                    after=_after_anchor(match.group(0)),
                    before=_before_anchor(match.group(0)),
                ),
                source_span=match.group(0),
                confidence=1,
            )
        )
    return operations, used_keywords


def _removal_operations(text: str) -> tuple[list[DatePlanOperation], set[str]]:
    operations: list[DatePlanOperation] = []
    used_keywords: set[str] = set()
    for clause in _clauses(text):
        if _REMOVE_CUE.search(clause) is None:
            continue
        for keyword, _kind in _stops_in_text(clause):
            used_keywords.add(keyword)
            operations.append(
                DatePlanOperation(
                    type=DateOperationType.REMOVE_STOP,
                    target=StopReference(keyword=keyword),
                    source_span=clause,
                    confidence=1,
                )
            )
    return operations, used_keywords


def _desired_stops(text: str) -> list[tuple[DesiredDateStop, str]]:
    desired: list[tuple[DesiredDateStop, str]] = []
    for clause in _clauses(text):
        for keyword, kind in _stops_in_text(clause):
            desired.append(
                (
                    DesiredDateStop(
                        kind=kind,
                        keyword=keyword,
                        meal_type=(
                            _meal_type(clause)
                            if kind in {StopKind.DINING, StopKind.CAFE}
                            else None
                        ),
                        target_day=_target_day(clause),
                        time_window=_time_window(clause),
                        after=_after_anchor(clause),
                        before=_before_anchor(clause),
                    ),
                    clause,
                )
            )
    return desired


def _matching_current_items(runtime_context: RuntimeContext | None, desired: DesiredDateStop):
    plan = (
        runtime_context.active_date_plan.current_plan
        if runtime_context is not None and runtime_context.active_date_plan is not None
        else None
    )
    if plan is None:
        return []
    return [
        item
        for item in plan.items
        if (desired.target_day is None or item.day_index == desired.target_day)
        and _item_matches_keyword(item, desired.keyword or desired.place_name or "")
    ]


def _placement_satisfied(
    item,
    desired: DesiredDateStop,
    runtime_context: RuntimeContext | None,
) -> bool:
    if desired.meal_type is not None and item.meal_type != desired.meal_type.value:
        return False
    if (
        desired.time_window is not None
        and desired.time_window.label is not None
        and item.time_label != desired.time_window.label
    ):
        return False
    if desired.after is not None:
        anchor_order = _anchor_order(item.day_index, desired.after, runtime_context)
        if anchor_order is None or item.order <= anchor_order:
            return False
    if desired.before is not None:
        anchor_order = _anchor_order(item.day_index, desired.before, runtime_context)
        if anchor_order is None or item.order >= anchor_order:
            return False
    return True


def _anchor_order(day_index: int, anchor, runtime_context: RuntimeContext | None) -> int | None:
    plan = (
        runtime_context.active_date_plan.current_plan
        if runtime_context is not None and runtime_context.active_date_plan is not None
        else None
    )
    if plan is None:
        return None
    if isinstance(anchor, StopReference):
        matches = [
            item
            for item in plan.items
            if item.day_index == day_index
            and _item_matches_reference(item, anchor)
        ]
    else:
        meal_type = {
            TemporalAnchor.BREAKFAST: MealType.BREAKFAST,
            TemporalAnchor.LUNCH: MealType.LUNCH,
            TemporalAnchor.DINNER: MealType.DINNER,
        }.get(anchor)
        if meal_type is None:
            return None
        matches = [
            item
            for item in plan.items
            if item.day_index == day_index and item.meal_type == meal_type.value
        ]
    return matches[0].order if len(matches) == 1 else None


def _reference_for_item(item, desired: DesiredDateStop) -> StopReference:
    return StopReference(
        place_id=item.place.id,
        place_name=item.place.name,
        keyword=desired.keyword,
        meal_type=(
            MealType(item.meal_type)
            if item.meal_type in MealType._value2member_map_
            else None
        ),
    )


def _item_matches_reference(item, reference: StopReference) -> bool:
    if reference.place_id is not None and item.place.id == reference.place_id:
        return True
    if reference.meal_type is not None and item.meal_type == reference.meal_type.value:
        return True
    value = reference.keyword or reference.place_name
    return value is not None and _item_matches_keyword(item, value)


def _item_matches_keyword(item, keyword: str) -> bool:
    aliases = _ALIASES.get(keyword, (keyword,))
    haystack = _normalize(
        " ".join(
            [
                item.slot_keyword or "",
                item.place.name,
                item.place.type_name or "",
                *item.place.tags,
                *item.place.search_keywords,
            ]
        )
    )
    return any(_normalize(alias) in haystack for alias in aliases)


def _has_placement_requirement(stop: DesiredDateStop) -> bool:
    return any(
        value is not None
        for value in (stop.meal_type, stop.target_day, stop.time_window, stop.after, stop.before)
    )


def _meal_type(text: str) -> MealType | None:
    if re.search(r"早餐|早饭|早上", text):
        return MealType.BREAKFAST
    if re.search(r"午餐|午饭|中饭|中午", text):
        return MealType.LUNCH
    if re.search(r"晚餐|晚饭|晚上", text):
        return MealType.DINNER
    return None


def _after_anchor(text: str) -> TemporalAnchor | None:
    if re.search(r"(?:晚饭|晚餐|晚宴)\s*(?:后|之后)", text):
        return TemporalAnchor.DINNER
    if re.search(r"(?:午饭|午餐|中饭)\s*(?:后|之后)", text):
        return TemporalAnchor.LUNCH
    if re.search(r"(?:早餐|早饭)\s*(?:后|之后)", text):
        return TemporalAnchor.BREAKFAST
    return None


def _before_anchor(text: str) -> TemporalAnchor | None:
    if re.search(r"(?:晚饭|晚餐|晚宴)\s*(?:前|之前)", text):
        return TemporalAnchor.DINNER
    if re.search(r"(?:午饭|午餐|中饭)\s*(?:前|之前)", text):
        return TemporalAnchor.LUNCH
    if re.search(r"(?:早餐|早饭)\s*(?:前|之前)", text):
        return TemporalAnchor.BREAKFAST
    return None


def _time_window(text: str) -> TimeWindow | None:
    if re.search(r"晚饭|晚餐", text) and _after_anchor(text) is not None:
        return TimeWindow(label="晚饭后")
    if "下午" in text:
        return TimeWindow(label="下午")
    if "晚上" in text:
        return TimeWindow(label="晚上")
    if "上午" in text:
        return TimeWindow(label="上午")
    return None


def _target_day(text: str) -> int | None:
    match = re.search(r"第\s*([1-5一二三四五])\s*天", text)
    if match is None:
        return None
    return {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}.get(
        match.group(1),
        int(match.group(1)) if match.group(1).isdigit() else None,
    )


def _first_stop(text: str) -> tuple[str, StopKind] | None:
    values = _stops_in_text(text)
    return values[0] if values else None


def _stops_in_text(text: str) -> list[tuple[str, StopKind]]:
    matches: list[tuple[int, str, StopKind]] = []
    for canonical, kind, aliases in _STOP_SPECS:
        for alias in aliases:
            position = text.find(alias)
            if position >= 0:
                matches.append((position, canonical, kind))
                break
    return [(canonical, kind) for _, canonical, kind in sorted(matches)]


def _clauses(text: str) -> list[str]:
    return [clause.strip() for clause in re.split(r"[，,。；;！？!?]+", text) if clause.strip()]


def _dedupe_operations(operations: tuple[DatePlanOperation, ...]) -> list[DatePlanOperation]:
    seen: set[str] = set()
    result: list[DatePlanOperation] = []
    for operation in operations:
        identity = json.dumps(
            operation.model_dump(mode="json", exclude={"source_span", "confidence"}),
            ensure_ascii=False,
            sort_keys=True,
        )
        if identity not in seen:
            seen.add(identity)
            result.append(operation)
    return result


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold())


_REPLAN_CUE = re.compile(r"重新.{0,4}(?:规划|安排)|换一套|全部重排|从头安排|重新来")
_REMOVE_CUE = re.compile(r"删除|删掉|去掉|移除|取消|不去|不要")
_REPLACEMENT_PATTERN = re.compile(
    r"(?:把|将)?\s*(?P<target>[^，,。；;]{1,24}?)\s*"
    r"(?:替换为|更换为|换成|换为|改成|改为)\s*"
    r"(?P<replacement>[^，,。；;]{1,24})"
)
_STOP_SPECS = (
    ("西餐", StopKind.DINING, ("西餐", "西式料理")),
    ("日料", StopKind.DINING, ("日料", "日本料理")),
    ("火锅", StopKind.DINING, ("火锅",)),
    ("烧烤", StopKind.DINING, ("烧烤", "烤肉")),
    ("素食", StopKind.DINING, ("素食", "素菜")),
    ("咖啡", StopKind.CAFE, ("咖啡馆", "咖啡")),
    ("电影院", StopKind.ACTIVITY, ("电影院", "电影", "影院")),
    ("展览", StopKind.ACTIVITY, ("展览", "展馆")),
    ("博物馆", StopKind.ACTIVITY, ("博物馆",)),
    ("美术馆", StopKind.ACTIVITY, ("美术馆",)),
    ("景点", StopKind.ACTIVITY, ("景点",)),
    ("公园", StopKind.ACTIVITY, ("公园",)),
    ("剧场", StopKind.ACTIVITY, ("剧场", "演出")),
)
_ALIASES = {canonical: aliases for canonical, _kind, aliases in _STOP_SPECS}

import json
import re
import unicodedata
from dataclasses import dataclass

from loveapp.application.date_planning.operation_validation import (
    DateOperationVerifier,
    RejectedDateOperation,
)
from loveapp.application.date_planning.structured_stops import (
    has_placement_requirement,
    match_desired_stop,
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
from loveapp.domain.date_plan import DatePlan, DatePlanItem
from loveapp.domain.enums import PlaceCategory
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
        candidates = _dedupe_operations(
            tuple([*deterministic, *(proposed_operations or [])])
        )
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
    patch_replacements, patch_replacement_keywords = _patch_replacement_operations(
        text,
        runtime_context,
        patch,
        has_explicit_replacement=bool(replacement_operations),
    )
    replacement_operations.extend(patch_replacements)
    replacement_keywords.update(patch_replacement_keywords)
    operations.extend(replacement_operations)
    removal_operations, removal_keywords = _removal_operations(
        text,
        excluded_keywords=replacement_keywords,
    )
    operations.extend(removal_operations)

    excluded = {*replacement_keywords, *removal_keywords}
    desired_stops = [*_desired_stops(text), *_patch_desired_stops(text, patch)]
    for desired, source_span in desired_stops:
        if desired.keyword in excluded:
            continue
        matches = match_desired_stop(
            _current_plan(runtime_context),
            desired,
            keyword_aliases=_ALIASES,
        )
        if matches:
            if has_placement_requirement(desired) and not any(
                match.placement_satisfied for match in matches
            ):
                operations.append(
                    DatePlanOperation(
                        type=DateOperationType.MOVE_STOP,
                        target=_reference_for_item(matches[0].item, desired),
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


def _patch_replacement_operations(
    text: str,
    runtime_context: RuntimeContext | None,
    patch: DatePlanPatch,
    *,
    has_explicit_replacement: bool,
) -> tuple[list[DatePlanOperation], set[str]]:
    if has_explicit_replacement or _REPLACE_CUE.search(text) is None:
        return [], set()
    payloads = _patch_desired_stops(text, patch)
    target = _replacement_target(runtime_context, patch, payloads)
    if target is None:
        return [], set()
    target_reference, target_kind, target_keywords = target
    compatible_payloads = [
        (desired, source_span)
        for desired, source_span in payloads
        if desired.kind == target_kind
        or {desired.kind, target_kind} <= {StopKind.DINING, StopKind.CAFE}
    ]
    if len(compatible_payloads) != 1:
        return [], set()
    desired, source_span = compatible_payloads[0]
    used_keywords = {
        *target_keywords,
        *(value for value in (desired.keyword, desired.place_name) if value is not None),
    }
    return [
        DatePlanOperation(
            type=DateOperationType.REPLACE_STOP,
            target=target_reference,
            payload=desired,
            source_span=source_span,
            confidence=1,
        )
    ], used_keywords


def _replacement_target(
    runtime_context: RuntimeContext | None,
    patch: DatePlanPatch,
    payloads: list[tuple[DesiredDateStop, str]],
) -> tuple[StopReference, StopKind, set[str]] | None:
    plan = _current_plan(runtime_context)
    payload_kinds = {desired.kind for desired, _source_span in payloads}
    if patch.replace_place_names:
        target_name = patch.replace_place_names[0]
        matches = (
            [
                item
                for item in plan.items
                if _normalized(target_name) in _normalized(item.place.name)
                or _normalized(item.place.name) in _normalized(target_name)
            ]
            if plan is not None
            else []
        )
        if len(matches) == 1:
            item = matches[0]
            return (
                _reference_for_existing_item(item),
                _kind_for_item(item),
                {target_name, item.slot_keyword or ""} - {""},
            )
        if not matches and len(payload_kinds) == 1:
            return StopReference(place_name=target_name), next(iter(payload_kinds)), {
                target_name
            }
        return None
    if plan is None or len(payload_kinds) != 1:
        return None
    target_kind = next(iter(payload_kinds))
    matches = [
        item
        for item in plan.items
        if (patch.target_day is None or item.day_index == patch.target_day)
        and (
            _kind_for_item(item) == target_kind
            or {_kind_for_item(item), target_kind} <= {StopKind.DINING, StopKind.CAFE}
        )
    ]
    if len(matches) != 1:
        return None
    item = matches[0]
    return (
        _reference_for_existing_item(item),
        _kind_for_item(item),
        {item.slot_keyword or ""} - {""},
    )


def _removal_operations(
    text: str,
    *,
    excluded_keywords: set[str] | None = None,
) -> tuple[list[DatePlanOperation], set[str]]:
    operations: list[DatePlanOperation] = []
    used_keywords: set[str] = set()
    excluded = excluded_keywords or set()
    for clause in _clauses(text):
        if _REMOVE_CUE.search(clause) is None:
            continue
        for keyword, _kind in _stops_in_text(clause):
            if keyword in excluded:
                continue
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


def _patch_desired_stops(
    text: str,
    patch: DatePlanPatch,
) -> list[tuple[DesiredDateStop, str]]:
    desired: list[tuple[DesiredDateStop, str]] = []
    for kind, keywords in (
        (StopKind.ACTIVITY, patch.activity_keywords),
        (StopKind.DINING, patch.dining_keywords),
    ):
        for keyword in keywords:
            clause = next(
                (candidate for candidate in _clauses(text) if keyword in candidate),
                text,
            )
            meal_type = _patch_meal_type(keyword, patch) or (
                _meal_type(clause) if kind == StopKind.DINING else None
            )
            desired.append(
                (
                    DesiredDateStop(
                        kind=(
                            StopKind.CAFE
                            if kind == StopKind.DINING and "咖啡" in keyword
                            else kind
                        ),
                        keyword=keyword,
                        meal_type=meal_type,
                        target_day=patch.target_day,
                        time_window=_time_window(clause),
                        after=_after_anchor(clause),
                        before=_before_anchor(clause),
                    ),
                    clause,
                )
            )
    return desired


def _patch_meal_type(keyword: str, patch: DatePlanPatch) -> MealType | None:
    return next(
        (
            MealType(meal_type)
            for meal_type, keywords in patch.meal_keywords.items()
            if meal_type in MealType._value2member_map_ and keyword in keywords
        ),
        None,
    )


def _current_plan(runtime_context: RuntimeContext | None) -> DatePlan | None:
    return (
        runtime_context.active_date_plan.current_plan
        if runtime_context is not None and runtime_context.active_date_plan is not None
        else None
    )


def _reference_for_item(
    item: DatePlanItem,
    desired: DesiredDateStop,
) -> StopReference:
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


def _reference_for_existing_item(item: DatePlanItem) -> StopReference:
    return StopReference(
        place_id=item.place.id,
        place_name=item.place.name,
        keyword=item.slot_keyword,
        meal_type=(
            MealType(item.meal_type)
            if item.meal_type in MealType._value2member_map_
            else None
        ),
    )


def _kind_for_item(item: DatePlanItem) -> StopKind:
    return {
        PlaceCategory.RESTAURANT: StopKind.DINING,
        PlaceCategory.CAFE: StopKind.CAFE,
        PlaceCategory.ATTRACTION: StopKind.ACTIVITY,
        PlaceCategory.ENTERTAINMENT: StopKind.ACTIVITY,
    }[item.place.category]


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


def _normalized(value: str) -> str:
    return _normalize(value).strip("，,。；;()（）")


_REPLAN_CUE = re.compile(r"重新.{0,4}(?:规划|安排)|换一套|全部重排|从头安排|重新来")
_REMOVE_CUE = re.compile(r"删除|删掉|去掉|移除|取消|不去|不要")
_REPLACE_CUE = re.compile(r"替换|更换|换成|换为|换一个|换一家|换个|改成|改为")
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

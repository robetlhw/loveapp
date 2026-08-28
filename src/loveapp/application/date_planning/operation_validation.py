import re
import unicodedata
from dataclasses import dataclass
from itertools import pairwise

from pydantic import ValidationError

from loveapp.application.date_planning.clause_parsing import split_date_clauses
from loveapp.domain.date_operations import (
    DateOperationType,
    DatePlanOperation,
    DateReplacementPreference,
    DateStopConstraints,
    StopReference,
    TemporalAnchor,
)
from loveapp.domain.date_patch import DatePlanPatch
from loveapp.domain.enums import PlaceCategory
from loveapp.domain.runtime_context import RuntimeContext


@dataclass(frozen=True)
class RejectedDateOperation:
    operation: DatePlanOperation
    reason: str


@dataclass(frozen=True)
class DateOperationVerification:
    accepted: tuple[DatePlanOperation, ...]
    rejected: tuple[RejectedDateOperation, ...]


class DateOperationVerifier:
    """Authorize typed operations using current-turn evidence and trusted state."""

    def verify(
        self,
        operations: list[DatePlanOperation],
        text: str,
        runtime_context: RuntimeContext | None,
        current_turn_patch: DatePlanPatch,
        *,
        allow_semantic_constraint_corrections: bool = False,
    ) -> DateOperationVerification:
        group_evidence, group_rejections = _alternative_group_evidence(
            operations,
            text,
        )
        decisions: list[tuple[DatePlanOperation, str | None]] = []
        for operation in operations:
            group_id = operation.alternative_group
            if group_id is not None and group_id in group_rejections:
                decisions.append((operation, group_rejections[group_id]))
                continue
            reason = self._rejection_reason(
                operation,
                text,
                runtime_context,
                current_turn_patch,
                alternative_evidence=(
                    group_evidence.get(group_id) if group_id is not None else None
                ),
                allow_semantic_constraint_corrections=allow_semantic_constraint_corrections,
            )
            decisions.append((operation, reason))

        failed_groups = {
            operation.alternative_group
            for operation, reason in decisions
            if operation.alternative_group is not None and reason is not None
        }
        accepted: list[DatePlanOperation] = []
        rejected: list[RejectedDateOperation] = []
        for operation, reason in decisions:
            if reason is None and operation.alternative_group in failed_groups:
                reason = "alternative_group_member_rejected"
            if reason is None:
                accepted.append(operation)
            else:
                rejected.append(RejectedDateOperation(operation, reason))
        return DateOperationVerification(tuple(accepted), tuple(rejected))

    def _rejection_reason(
        self,
        operation: DatePlanOperation,
        text: str,
        runtime_context: RuntimeContext | None,
        patch: DatePlanPatch,
        *,
        alternative_evidence: str | None,
        allow_semantic_constraint_corrections: bool,
    ) -> str | None:
        normalized = _normalize(text)
        evidence = _normalize(operation.source_span or text)
        if (
            operation.source_span is not None
            and _normalize_source_evidence(operation.source_span)
            not in _normalize_source_evidence(text)
        ):
            return "source_span_not_in_current_turn"
        if operation.type == DateOperationType.UPDATE_CONSTRAINT:
            return _constraint_rejection(
                operation,
                patch,
                text,
                allow_semantic_constraint_corrections=allow_semantic_constraint_corrections,
            )
        if operation.type == DateOperationType.UPDATE_REQUIREMENT:
            return _requirement_update_rejection(
                operation,
                operation.source_span or text,
                runtime_context,
            )
        if operation.type == DateOperationType.REPLAN:
            return None if _REPLAN_CUE.search(normalized) else "replan_without_explicit_cue"
        if operation.type == DateOperationType.REMOVE_STOP and not _REMOVE_CUE.search(normalized):
            return "remove_without_explicit_cue"
        if operation.type == DateOperationType.REPLACE_STOP and not _REPLACE_CUE.search(normalized):
            return "replace_without_explicit_cue"
        if operation.type == DateOperationType.MOVE_STOP and not _MOVE_CUE.search(normalized):
            return "move_without_temporal_cue"
        if operation.type == DateOperationType.ADD_STOP and _NEGATED_ADD_CUE.search(evidence):
            return "add_negated_in_source"
        if (
            operation.alternative_group is not None
            and alternative_evidence is None
        ):
            return "alternative_group_without_source_evidence"
        if operation.target is not None and not _reference_has_evidence(
            operation.target,
            evidence,
            runtime_context,
            replacement_scope=operation.type == DateOperationType.REPLACE_STOP,
        ):
            return "target_without_source_or_unique_context_evidence"
        if operation.payload is not None:
            payload_value = operation.payload.keyword or operation.payload.place_name
            if payload_value is not None and not _text_supports_value(payload_value, evidence):
                return "payload_without_current_turn_evidence"
            if not _payload_modifiers_have_evidence(
                operation,
                evidence,
                shared_evidence=alternative_evidence,
            ):
                return "payload_modifier_without_source_evidence"
        return None


def _requirement_update_rejection(
    operation: DatePlanOperation,
    evidence: str,
    runtime_context: RuntimeContext | None,
) -> str | None:
    update = operation.requirement_update
    active = runtime_context.active_date_plan if runtime_context is not None else None
    if update is None:
        return "requirement_update_missing"
    if active is None or not active.requirements:
        return "requirement_targets_unavailable"
    by_id = {requirement.id: requirement for requirement in active.requirements}
    target_ids: list[str] = []
    target_values: list[str] = []
    kind_families: set[str] = set()
    normalized_evidence = _normalize(evidence)
    for reference in update.targets:
        requirement_id = reference.requirement_id
        if requirement_id is None or requirement_id not in by_id:
            return "requirement_target_not_found"
        requirement = by_id[requirement_id]
        if len(requirement.alternatives) != 1:
            return "requirement_regroup_requires_independent_targets"
        stop_reference = reference.stop_reference
        if stop_reference is None:
            return "requirement_target_without_source_evidence"
        alternative = requirement.alternatives[0]
        expected = alternative.keyword or alternative.place_name
        observed = stop_reference.keyword or stop_reference.place_name
        if (
            expected is None
            or observed is None
            or not _text_supports_value(observed, normalized_evidence)
            or not _text_supports_value(expected, _normalize(observed))
        ):
            return "requirement_target_without_source_evidence"
        target_ids.append(requirement_id)
        target_values.append(observed)
        kind_families.add(
            "dining"
            if alternative.kind.value in {"dining", "cafe"}
            else alternative.kind.value
        )
    if len(set(target_ids)) != len(target_ids):
        return "requirement_targets_not_distinct"
    if len(kind_families) != 1:
        return "requirement_targets_incompatible"
    clause_rejection = _requirement_update_clause_rejection(evidence, target_values)
    if clause_rejection is not None:
        return clause_rejection
    if update.min_satisfied != 1 or update.max_satisfied != 1:
        return "requirement_cardinality_without_source_evidence"
    return None


def _requirement_update_clause_rejection(
    evidence: str,
    target_values: list[str],
) -> str | None:
    cue_clauses: list[str] = []
    positive_cue_clauses: list[str] = []
    for clause in split_date_clauses(evidence):
        normalized = _normalize(clause.text)
        if _REQUIREMENT_REGROUP_CUE.search(normalized) is None:
            continue
        cue_clauses.append(normalized)
        if _NEGATED_REQUIREMENT_REGROUP_CUE.search(normalized) is None:
            positive_cue_clauses.append(normalized)
    if not cue_clauses:
        return "requirement_update_without_explicit_cue"
    if not positive_cue_clauses:
        return "requirement_update_negated"
    if not any(
        all(_text_supports_value(value, clause) for value in target_values)
        for clause in positive_cue_clauses
    ):
        return "requirement_targets_outside_regroup_clause"
    return None


def _alternative_group_evidence(
    operations: list[DatePlanOperation],
    text: str,
) -> tuple[dict[str, str], dict[str, str]]:
    groups: dict[str, list[DatePlanOperation]] = {}
    for operation in operations:
        if operation.alternative_group is not None:
            groups.setdefault(operation.alternative_group, []).append(operation)

    evidence: dict[str, str] = {}
    rejected: dict[str, str] = {}
    for group_id, members in groups.items():
        if len(members) < 2:
            rejected[group_id] = "alternative_group_incomplete"
            continue
        payloads = [member.payload for member in members if member.payload is not None]
        values = [
            payload.keyword or payload.place_name
            for payload in payloads
            if payload.keyword is not None or payload.place_name is not None
        ]
        kind_families = {
            "dining" if payload.kind.value in {"dining", "cafe"} else payload.kind.value
            for payload in payloads
        }
        if (
            len(payloads) != len(members)
            or len(values) != len(members)
            or len(set(values)) != len(values)
            or len(kind_families) != 1
        ):
            rejected[group_id] = "alternative_group_invalid_members"
            continue
        window = _alternative_evidence_window(text, members, values)
        if window is None or not _explicit_alternative_structure(window, values):
            rejected[group_id] = "alternative_group_without_source_evidence"
            continue
        evidence[group_id] = _normalize(window)
    return evidence, rejected


def _alternative_evidence_window(
    text: str,
    operations: list[DatePlanOperation],
    values: list[str],
) -> str | None:
    positions: list[tuple[int, int]] = []
    for operation, value in zip(operations, values, strict=True):
        span = operation.source_span or text
        span_start = text.find(span)
        if span_start < 0:
            return None
        aliases = _VALUE_ALIASES.get(value, (value,))
        matches = [
            (position, position + len(alias))
            for alias in aliases
            if (position := span.find(alias)) >= 0
        ]
        if not matches:
            return None
        local_start, local_end = min(matches)
        positions.append((span_start + local_start, span_start + local_end))

    first = min(start for start, _ in positions)
    last = max(end for _, end in positions)
    boundaries = "，,。；;！？!?\n"
    start = max((text.rfind(marker, 0, first) for marker in boundaries), default=-1) + 1
    following = [
        position
        for marker in boundaries
        if (position := text.find(marker, last)) >= 0
    ]
    end = min(following, default=len(text))
    return text[start:end]


def _explicit_alternative_structure(evidence: str, values: list[str]) -> bool:
    normalized = _normalize(evidence)
    if _STRONG_BOUNDARY_CUE.search(normalized):
        return False
    positions: list[tuple[int, int]] = []
    for value in values:
        aliases = _VALUE_ALIASES.get(value, (value,))
        matches = [
            (position, position + len(alias))
            for alias in aliases
            if (position := normalized.find(_normalize(alias))) >= 0
        ]
        if not matches:
            return False
        positions.append(min(matches))
    positions.sort()
    if any(
        next_start - current_end > 24
        for (_, current_end), (next_start, _) in pairwise(positions)
    ):
        return False
    gaps = [
        normalized[current_end:next_start]
        for (_, current_end), (next_start, _) in pairwise(positions)
    ]
    trailing = normalized[positions[-1][1] :]
    if _POSTFIX_ALTERNATIVE_CUE.match(trailing) and all(
        _SIMPLE_CHOICE_GAP.fullmatch(gap) for gap in gaps
    ):
        return True
    return bool(gaps) and all(_INFIX_ALTERNATIVE_CUE.search(gap) for gap in gaps)


def _constraint_rejection(
    operation: DatePlanOperation,
    patch: DatePlanPatch,
    text: str,
    *,
    allow_semantic_constraint_corrections: bool,
) -> str | None:
    field = operation.constraint_field
    if field is None:
        return "constraint_field_missing"
    patch_value = getattr(patch, field.value, None)
    if patch_value is not None and _comparable_value(patch_value) == _comparable_value(
        operation.constraint_value
    ):
        return None
    if allow_semantic_constraint_corrections and _constraint_has_current_turn_evidence(
        operation,
        text,
    ):
        return None
    return (
        "constraint_not_in_deterministic_patch"
        if patch_value is None
        else "constraint_conflicts_with_deterministic_patch"
    )


def _constraint_has_current_turn_evidence(
    operation: DatePlanOperation,
    text: str,
) -> bool:
    field = operation.constraint_field
    value = operation.constraint_value
    if field is None or value is None or operation.source_span is None:
        return False
    try:
        DatePlanPatch.model_validate({field.value: value})
    except ValidationError:
        return False
    normalized = _normalize(operation.source_span)
    marker = {
        "budget": r"预算|总价|总共",
        "budget_scope": r"每天|每日|总预算|预算",
        "city": r"城市|去|在",
        "area": r"区域|商圈|区|附近",
        "date": r"日期|时间|周|星期|月|日|今天|明天|后天",
        "end_date": r"到|至|结束",
        "start_time": r"开始|出发|点|时|:",
        "day_count": r"天|日游|行程",
        "transport_mode": r"交通|步行|地铁|公交|开车|驾车|骑行",
        "plan_mode": r"单日|多日|天|日游|行程",
    }.get(field.value)
    if marker is None or re.search(marker, normalized) is None:
        return False
    raw_value = getattr(value, "value", value)
    if isinstance(raw_value, int):
        return re.search(rf"(?<!\d){raw_value}(?!\d)", normalized) is not None
    compact_value = _normalize(str(raw_value))
    return bool(compact_value and compact_value in normalized)


def _reference_has_evidence(
    reference: StopReference,
    text: str,
    runtime_context: RuntimeContext | None,
    *,
    replacement_scope: bool,
) -> bool:
    reference_scope = _reference_scope(text) if replacement_scope else text
    plan = (
        runtime_context.active_date_plan.current_plan
        if runtime_context is not None and runtime_context.active_date_plan is not None
        else None
    )
    if reference.ordinal is not None and re.search(
        rf"第\s*(?:{reference.ordinal}|{_CHINESE_ORDINALS.get(reference.ordinal, '')})\s*个",
        reference_scope,
    ):
        return plan is None or reference.ordinal <= len(plan.items)
    for value, matcher in (
        (reference.place_name, _item_name_supports_value),
        (reference.keyword, _item_supports_keyword),
    ):
        if value is None or _is_generic_reference(value):
            continue
        if not _text_supports_value(value, reference_scope):
            continue
        if plan is None:
            return True
        matches = [item for item in plan.items if matcher(item, value)]
        if len(matches) == 1 and _reference_matches_item(reference, matches[0]):
            return True
    if plan is None:
        return False
    candidate_filters = _semantic_reference_filters(reference_scope, plan.items)
    has_contextual_reference = bool(
        re.search(
            r"那个|这个|该|原来的|原先的|之前的|第\s*[一二三四五六七八九十\d]+\s*个",
            reference_scope,
        )
    )
    if candidate_filters:
        semantic_candidates = [
            item
            for item in plan.items
            if all(item in candidates for candidates in candidate_filters)
        ]
    elif has_contextual_reference:
        semantic_candidates = list(plan.items)
    else:
        semantic_candidates = []
    return len(semantic_candidates) == 1 and _reference_matches_item(
        reference,
        semantic_candidates[0],
    )


def _semantic_reference_filters(text: str, items: list) -> list[list]:
    filters: list[list] = []
    category_matches = _generic_category_matches(text, items)
    if _GENERIC_CATEGORY_CUE.search(text) is not None:
        filters.append(category_matches)
    meal_types = {
        meal_type
        for meal_type, pattern in _MEAL_TEXT.items()
        if pattern.search(text) is not None
    }
    if meal_types:
        filters.append(
            [
                item
                for item in items
                if item.meal_type in meal_types
                or (
                    item.meal_type is None
                    and item.place.category
                    in {PlaceCategory.RESTAURANT, PlaceCategory.CAFE}
                )
            ]
        )
    target_day = _target_day_from_text(text)
    if target_day is not None:
        filters.append([item for item in items if item.day_index == target_day])
    period_labels = {
        label
        for label, pattern in _PERIOD_TEXT.items()
        if pattern.search(text) is not None
    }
    if period_labels:
        period_matches = [
            item
            for item in items
            if item.time_label is not None
            and any(label in item.time_label for label in period_labels)
        ]
        if period_matches:
            filters.append(period_matches)
    return filters


def _target_day_from_text(text: str) -> int | None:
    match = _TARGET_DAY_CUE.search(text)
    if match is None:
        return None
    value = match.group("day")
    if value.isdigit():
        return int(value)
    return _CHINESE_NUMERAL_VALUES.get(value)


def _reference_matches_item(reference: StopReference, item) -> bool:
    return bool(
        (reference.place_id is not None and item.place.id == reference.place_id)
        or (
            reference.place_name is not None
            and _text_supports_value(reference.place_name, _normalize(item.place.name))
        )
        or (
            reference.keyword is not None
            and (
                _item_supports_keyword(item, reference.keyword)
                or _generic_reference_matches_item(reference.keyword, item)
            )
        )
        or (
            reference.meal_type is not None
            and (
                item.meal_type == reference.meal_type.value
                or (
                    item.meal_type is None
                    and item.place.category
                    in {PlaceCategory.RESTAURANT, PlaceCategory.CAFE}
                )
            )
        )
    )


def _item_name_supports_value(item, value: str) -> bool:
    return _text_supports_value(value, _normalize(item.place.name))


def _is_generic_reference(value: str) -> bool:
    return _normalize(value) in _GENERIC_REFERENCE_VALUES


def _generic_reference_matches_item(value: str, item) -> bool:
    categories = _GENERIC_REFERENCE_CATEGORIES.get(_normalize(value), set())
    return item.place.category in categories


def _payload_modifiers_have_evidence(
    operation: DatePlanOperation,
    evidence: str,
    *,
    shared_evidence: str | None = None,
) -> bool:
    if _payload_modifiers_have_direct_evidence(operation, evidence):
        return True
    if shared_evidence is None or _payload_modifier_conflicts_with_local(
        operation,
        evidence,
    ):
        return False
    return _payload_modifiers_have_direct_evidence(operation, shared_evidence)


def _payload_modifiers_have_direct_evidence(
    operation: DatePlanOperation,
    evidence: str,
) -> bool:
    payload = operation.payload
    if payload is None:
        return True
    if (
        payload.meal_type is not None
        and _MEAL_TEXT[payload.meal_type.value].search(evidence) is None
        and not _clock_supports_meal(payload.meal_type.value, evidence)
    ):
        return False
    if (
        DateReplacementPreference.NEARBY in payload.replacement_preferences
        and _NEARBY_CUE.search(evidence) is None
    ):
        return False
    if payload.target_day is not None and not _target_day_has_evidence(
        payload.target_day,
        evidence,
    ):
        return False
    if payload.time_window is not None and not _time_window_has_evidence(
        payload.time_window,
        evidence,
    ):
        return False
    if not _payload_constraints_have_evidence(payload.constraints, evidence):
        return False
    if payload.after is not None and not _temporal_reference_has_evidence(
        payload.after,
        evidence,
        relation="after",
    ):
        return False
    return payload.before is None or _temporal_reference_has_evidence(
        payload.before,
        evidence,
        relation="before",
    )


def _payload_constraints_have_evidence(
    constraints: DateStopConstraints | None,
    evidence: str,
) -> bool:
    if constraints is None:
        return True
    if constraints.max_cost_per_person is not None and (
        _STOP_LOCAL_COST_CUE.search(evidence) is None
        or not _number_has_evidence(constraints.max_cost_per_person, evidence)
    ):
        return False
    if constraints.min_rating is not None and (
        _STOP_LOCAL_RATING_CUE.search(evidence) is None
        or not _number_has_evidence(constraints.min_rating, evidence)
    ):
        return False
    if constraints.preferred_area is not None and (
        not _text_supports_value(constraints.preferred_area, evidence)
        or _STOP_LOCAL_AREA_CUE.search(evidence) is None
    ):
        return False
    return constraints.max_distance_meters is None or (
        _STOP_LOCAL_DISTANCE_CUE.search(evidence) is not None
        and _distance_has_evidence(constraints.max_distance_meters, evidence)
    )


def _number_has_evidence(value: int | float, evidence: str) -> bool:
    rendered = f"{value:g}" if isinstance(value, float) else str(value)
    return re.search(rf"(?<!\d){re.escape(rendered)}(?!\d)", evidence) is not None


def _distance_has_evidence(meters: int, evidence: str) -> bool:
    if re.search(rf"(?<!\d){meters}(?!\d)\s*(?:米|m)", evidence, re.IGNORECASE):
        return True
    for match in re.finditer(
        r"(?<!\d)(?P<value>\d+(?:\.\d+)?)(?!\d)\s*(?:公里|km)",
        evidence,
        re.IGNORECASE,
    ):
        if round(float(match.group("value")) * 1000) == meters:
            return True
    return False


def _payload_modifier_conflicts_with_local(
    operation: DatePlanOperation,
    evidence: str,
) -> bool:
    payload = operation.payload
    if payload is None:
        return False
    expected_meals = {
        value
        for value in (
            payload.meal_type.value if payload.meal_type is not None else None,
            _meal_type_for_temporal_reference(payload.after),
            _meal_type_for_temporal_reference(payload.before),
            _meal_type_for_time_label(
                payload.time_window.label if payload.time_window is not None else None
            ),
        )
        if value is not None
    }
    observed_meals = {
        meal_type
        for meal_type, pattern in _MEAL_TEXT.items()
        if pattern.search(evidence) is not None
    }
    if expected_meals and observed_meals - expected_meals:
        return True

    if payload.target_day is not None:
        observed_days = {
            value
            for match in _TARGET_DAY_CUE.finditer(evidence)
            if (
                value := (
                    int(match.group("day"))
                    if match.group("day").isdigit()
                    else _CHINESE_NUMERAL_VALUES.get(match.group("day"))
                )
            )
            is not None
        }
        if observed_days and observed_days != {payload.target_day}:
            return True

    expected_period = _period_for_payload(payload)
    observed_periods = {
        period
        for period, pattern in _PERIOD_TEXT.items()
        if pattern.search(evidence) is not None
    }
    return bool(expected_period and observed_periods - {expected_period})


def _meal_type_for_temporal_reference(reference) -> str | None:
    if reference == TemporalAnchor.BREAKFAST:
        return "breakfast"
    if reference == TemporalAnchor.LUNCH:
        return "lunch"
    if reference == TemporalAnchor.DINNER or reference == TemporalAnchor.AFTER_DINNER:
        return "dinner"
    return None


def _meal_type_for_time_label(label: str | None) -> str | None:
    normalized = _normalize(label or "")
    return next(
        (
            meal_type
            for meal_type, pattern in _MEAL_TEXT.items()
            if pattern.search(normalized) is not None
        ),
        None,
    )


def _period_for_payload(payload) -> str | None:
    label = payload.time_window.label if payload.time_window is not None else None
    normalized = _normalize(label or "")
    if payload.after == TemporalAnchor.AFTERNOON or "下午" in normalized:
        return "下午"
    if payload.after == TemporalAnchor.EVENING or "晚上" in normalized:
        return "晚上"
    if "上午" in normalized or "早上" in normalized:
        return "上午"
    return None


def _target_day_has_evidence(target_day: int, evidence: str) -> bool:
    chinese = _CHINESE_NUMERALS.get(target_day)
    values = [str(target_day), *(value for value in (chinese,) if value)]
    alternatives = "|".join(re.escape(value) for value in values)
    return re.search(rf"第\s*(?:{alternatives})\s*(?:天|日)", evidence) is not None


def _time_window_has_evidence(time_window, evidence: str) -> bool:
    if time_window.label is not None:
        label = _normalize(time_window.label)
        aliases = _TIME_LABEL_ALIASES.get(label, (label,))
        if not any(alias in evidence for alias in aliases):
            return False
    for boundary in (time_window.start, time_window.end):
        if boundary is None:
            continue
        hour = boundary.hour
        minute = boundary.minute
        clock = rf"(?:{hour:02d}:{minute:02d}|{hour}:{minute:02d}|{hour}点(?:{minute}分?)?)"
        if re.search(clock, evidence) is None:
            return False
    return True


def _clock_supports_meal(meal_type: str, evidence: str) -> bool:
    match = _CLOCK_CUE.search(evidence)
    if match is None:
        return False
    hour = int(match.group("hour"))
    ranges = {
        "breakfast": range(6, 11),
        "lunch": range(11, 15),
        "dinner": range(17, 22),
    }
    return hour in ranges[meal_type]


def _temporal_reference_has_evidence(
    reference: TemporalAnchor | StopReference,
    evidence: str,
    *,
    relation: str,
) -> bool:
    if isinstance(reference, TemporalAnchor):
        if reference == TemporalAnchor.AFTER_DINNER:
            return relation == "after" and _AFTER_DINNER_CUE.search(evidence) is not None
        anchor = _TEMPORAL_ANCHOR_TEXT[reference]
        suffix = _AFTER_CUE if relation == "after" else _BEFORE_CUE
        return re.search(rf"(?:{anchor.pattern}).{{0,4}}(?:{suffix.pattern})", evidence) is not None

    values = [reference.place_name, reference.keyword]
    anchor_patterns = [
        re.escape(_normalize(alias))
        for value in values
        if value
        for alias in _VALUE_ALIASES.get(value, (value,))
    ]
    if reference.meal_type is not None:
        anchor_patterns.append(_MEAL_TEXT[reference.meal_type.value].pattern)
    if reference.ordinal is not None:
        ordinal = _CHINESE_ORDINALS.get(reference.ordinal, str(reference.ordinal))
        anchor_patterns.append(rf"第\s*(?:{reference.ordinal}|{ordinal})\s*个")
    if not anchor_patterns:
        return False
    suffix = _AFTER_CUE if relation == "after" else _BEFORE_CUE
    anchor = "|".join(anchor_patterns)
    if re.search(rf"(?:{anchor}).{{0,4}}(?:{suffix.pattern})", evidence) is not None:
        return True
    return relation == "after" and re.search(
        rf"(?:看完|吃完|逛完|结束)\s*(?:{anchor})",
        evidence,
    ) is not None


def _reference_scope(text: str) -> str:
    replacement_cues = list(_REPLACE_CUE.finditer(text))
    return text[: replacement_cues[-1].start()] if replacement_cues else text


def _generic_category_matches(text: str, items: list) -> list:
    categories: set[PlaceCategory] = set()
    if re.search(r"餐厅|饭店|用餐", text):
        categories.add(PlaceCategory.RESTAURANT)
    if re.search(r"咖啡馆|咖啡店", text):
        categories.add(PlaceCategory.CAFE)
    if "景点" in text:
        categories.add(PlaceCategory.ATTRACTION)
    if "活动" in text:
        categories.update({PlaceCategory.ATTRACTION, PlaceCategory.ENTERTAINMENT})
    return [item for item in items if item.place.category in categories]


def _item_supports_keyword(item, keyword: str) -> bool:
    aliases = _VALUE_ALIASES.get(keyword, (keyword,))
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


def _text_supports_value(value: str, text: str) -> bool:
    aliases = _VALUE_ALIASES.get(value, (value,))
    return any(_normalize(alias) in text for alias in aliases)


def _comparable_value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold())


def _normalize_source_evidence(value: str) -> str:
    return re.sub(r"[，,。；;！？!?]+", "", _normalize(value))


_REPLAN_CUE = re.compile(r"重新.{0,4}(?:规划|安排)|换一套|全部重排|从头安排|重新来")
_REMOVE_CUE = re.compile(r"删除|删掉|去掉|移除|取消|不去|不要")
_NEGATED_ADD_CUE = re.compile(
    r"(?:不|别)(?:要|想|打算)?(?:去|吃|喝|看|逛|安排|加|添加)|取消(?:去|安排|添加)?"
)
_INFIX_ALTERNATIVE_CUE = re.compile(r"或者|或是|要么|还是")
_POSTFIX_ALTERNATIVE_CUE = re.compile(r"也行|也可以|都可以|均可|任选|二选一")
_REQUIREMENT_REGROUP_CUE = re.compile(
    r"二选一|任选(?:其一|一个)?|选一个就行|任意一个|或者|或是|要么|还是"
)
_NEGATED_REQUIREMENT_REGROUP_CUE = re.compile(
    r"(?:不要|别|不用|无需|不再|取消|撤销)(?:再)?(?:搞|做|按|改成|设成|设置成)?\s*"
    r"(?:二选一|任选(?:其一|一个)?|选一个)"
)
_STRONG_BOUNDARY_CUE = re.compile(r"[。；;！？!?\n]")
_SIMPLE_CHOICE_GAP = re.compile(r"(?:[，,、/]|或者|或是|要么|还是|和|与|以及)*")
_REPLACE_CUE = re.compile(
    r"替换|更换|换掉|换一下|换一换|换成|换为|换一个|换一家|换个|改成|改为|"
    r"有没有.{0,8}(?:近一点|近一些|附近|其他|别的)"
)
_MOVE_CUE = re.compile(
    r"之前|之后|前|后|上午|中午|下午|晚上|早餐|午饭|午餐|晚饭|晚餐|调整顺序|放到"
)
_CHINESE_ORDINALS = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五"}
_CHINESE_NUMERALS = {
    1: "一",
    2: "二",
    3: "三",
    4: "四",
    5: "五",
    6: "六",
    7: "七",
    8: "八",
    9: "九",
    10: "十",
}
_CHINESE_NUMERAL_VALUES = {value: key for key, value in _CHINESE_NUMERALS.items()}
_TARGET_DAY_CUE = re.compile(r"第\s*(?P<day>\d+|一|二|三|四|五|六|七|八|九|十)\s*(?:天|日)")
_MEAL_TEXT = {
    "breakfast": re.compile(r"早餐|早饭|早上"),
    "lunch": re.compile(r"午餐|午饭|中饭|中午"),
    "dinner": re.compile(r"晚餐|晚饭|晚上"),
}
_TEMPORAL_ANCHOR_TEXT = {
    TemporalAnchor.BREAKFAST: _MEAL_TEXT["breakfast"],
    TemporalAnchor.LUNCH: _MEAL_TEXT["lunch"],
    TemporalAnchor.AFTERNOON: re.compile(r"下午"),
    TemporalAnchor.DINNER: _MEAL_TEXT["dinner"],
    TemporalAnchor.EVENING: re.compile(r"晚上|夜间"),
}
_AFTER_CUE = re.compile(r"后|之后|以后")
_BEFORE_CUE = re.compile(r"前|之前|以前")
_AFTER_DINNER_CUE = re.compile(r"(?:晚餐|晚饭)(?:后|之后|以后)")
_CLOCK_CUE = re.compile(r"(?<!\d)(?P<hour>\d{1,2})(?:点|时|:)(?:\d{1,2})?(?!\d)")
_NEARBY_CUE = re.compile(r"近一点|近一些|附近|就近|更近")
_STOP_LOCAL_COST_CUE = re.compile(
    r"(?:人均|每人|每位|客单).{0,16}(?:以内|以下|不超过|最多|至多|上限)"
)
_STOP_LOCAL_RATING_CUE = re.compile(
    r"(?:评分|星级).{0,12}(?:以上|不低于|至少|最低)"
)
_STOP_LOCAL_AREA_CUE = re.compile(r"附近|周边|一带|商圈")
_STOP_LOCAL_DISTANCE_CUE = re.compile(
    r"(?:距离|相距|路程)(?=.{0,24}(?:米|公里|km|m))"
    r"(?=.{0,24}(?:以内|以下|不超过|最多|至多)).{0,30}",
    re.IGNORECASE,
)
_GENERIC_CATEGORY_CUE = re.compile(r"餐厅|饭店|用餐|咖啡馆|咖啡店|景点|活动")
_PERIOD_TEXT = {
    "上午": re.compile(r"上午|早上"),
    "下午": re.compile(r"下午"),
    "晚上": re.compile(r"晚上|夜间"),
}
_TIME_LABEL_ALIASES = {
    "早餐": ("早餐", "早饭"),
    "午餐": ("午餐", "午饭", "中饭"),
    "晚餐": ("晚餐", "晚饭"),
    "晚饭后": ("晚饭后", "晚餐后"),
    "晚餐后": ("晚餐后", "晚饭后"),
}
_GENERIC_REFERENCE_CATEGORIES = {
    "餐厅": {PlaceCategory.RESTAURANT},
    "饭店": {PlaceCategory.RESTAURANT},
    "用餐": {PlaceCategory.RESTAURANT},
    "咖啡馆": {PlaceCategory.CAFE},
    "咖啡店": {PlaceCategory.CAFE},
    "景点": {PlaceCategory.ATTRACTION},
    "活动": {PlaceCategory.ATTRACTION, PlaceCategory.ENTERTAINMENT},
    "地方": set(PlaceCategory),
}
_GENERIC_REFERENCE_VALUES = frozenset(_GENERIC_REFERENCE_CATEGORIES)
_VALUE_ALIASES = {
    "电影": ("电影", "电影院", "影院"),
    "电影院": ("电影院", "电影", "影院"),
    "展览": ("展览", "展馆", "美术馆"),
    "日料": ("日料", "日本料理"),
    "西餐": ("西餐", "西式料理"),
}

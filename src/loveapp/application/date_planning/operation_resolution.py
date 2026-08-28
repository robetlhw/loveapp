import json
import re
import unicodedata
from dataclasses import dataclass

from loveapp.application.date_planning.clause_parsing import split_date_clauses
from loveapp.application.date_planning.modification_detection import (
    detect_date_modification,
    interpret_date_modification,
)
from loveapp.application.date_planning.operation_validation import (
    DateOperationVerifier,
    RejectedDateOperation,
)
from loveapp.application.date_planning.structured_stops import (
    has_placement_requirement,
    item_matches_reference,
    match_desired_stop,
)
from loveapp.domain.date_operations import (
    DateConstraintField,
    DateOperationType,
    DatePlanOperation,
    DateRequirementUpdate,
    DateSemanticParseResult,
    DateStopConstraints,
    DateStopRequirement,
    DesiredDateStop,
    MealType,
    RequirementReference,
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
    unresolved_references: tuple[str, ...] = ()
    input_count: int = 0


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
        proposed_unresolved_references: list[str] | None = None,
        preferred_constraint_operations: list[DatePlanOperation] | None = None,
        allow_semantic_constraint_corrections: bool = False,
    ) -> DateOperationResolution:
        deterministic = _deterministic_operations(text, runtime_context, current_turn_patch)
        interpretation = (
            interpret_date_modification(text, _current_plan(runtime_context))
            if not any(
                operation.type == DateOperationType.REPLACE_STOP
                for operation in deterministic
            )
            else DateSemanticParseResult()
        )
        candidate_operations = [
            *deterministic,
            *interpretation.operations,
            *(proposed_operations or []),
        ]
        preferred_constraints = {
            operation.constraint_field: operation
            for operation in (preferred_constraint_operations or [])
            if operation.type == DateOperationType.UPDATE_CONSTRAINT
            and operation.constraint_field is not None
        }
        if preferred_constraints:
            candidate_operations = [
                operation
                for operation in candidate_operations
                if operation.type != DateOperationType.UPDATE_CONSTRAINT
                or operation.constraint_field not in preferred_constraints
                or operation == preferred_constraints[operation.constraint_field]
            ]
        current_plan = _current_plan(runtime_context)
        candidates = _dedupe_operations(
            tuple(candidate_operations),
            current_plan=current_plan,
            source_text=text,
            merge_semantic_equivalents=False,
        )
        verification = self._verifier.verify(
            candidates,
            text,
            runtime_context,
            current_turn_patch,
            allow_semantic_constraint_corrections=allow_semantic_constraint_corrections,
        )
        unresolved = tuple(
            dict.fromkeys(
                [
                    *interpretation.unresolved_references,
                    *(proposed_unresolved_references or []),
                ]
            )
        )
        accepted: list[DatePlanOperation] = []
        rejected = list(verification.rejected)
        if unresolved:
            rejected.extend(
                RejectedDateOperation(item.operation, "plan_reference_unresolved")
                for item in verification.rejected
                if item.operation.type in _REFERENCE_TARGETED_MUTATIONS
                and item.reason == "target_without_source_or_unique_context_evidence"
            )
        for operation in verification.accepted:
            if unresolved and operation.type in _REFERENCE_TARGETED_MUTATIONS:
                rejected.append(RejectedDateOperation(operation, "plan_reference_unresolved"))
            else:
                accepted.append(operation)
        operations = tuple(
            _dedupe_operations(
                tuple(
                    _prefer_grouped_additions(
                        accepted,
                        deterministic,
                        text,
                    )
                ),
                current_plan=current_plan,
                source_text=text,
            )
        )
        return DateOperationResolution(
            candidates=tuple(candidates),
            operations=operations,
            rejected=tuple(rejected),
            unresolved_references=unresolved,
            input_count=len(candidate_operations),
        )


def requires_date_semantic_parse(
    text: str,
    runtime_context: RuntimeContext | None,
    deterministic_result: DateOperationResolution,
) -> bool:
    """Identify complex date semantics independently from task routing."""

    return bool(
        date_semantic_parse_reasons(
            text,
            runtime_context,
            deterministic_result,
        )
    )


def date_semantic_parse_reasons(
    text: str,
    runtime_context: RuntimeContext | None,
    deterministic_result: DateOperationResolution,
) -> tuple[str, ...]:
    """Explain why bounded DatePlan semantic interpretation is required."""

    clauses = split_date_clauses(text)
    operations = deterministic_result.operations
    stop_operations = [
        operation for operation in operations if operation.type in _STOP_OPERATIONS
    ]
    detection = detect_date_modification(text, _current_plan(runtime_context))
    has_temporal_binding = bool(
        re.search(r"(?:之前|之后|前|后|上午|中午|下午|晚上|早饭|午饭|晚饭)", text)
    )
    budget_values = re.findall(r"(?<!\d)\d{2,6}(?!\d)", text) if "预算" in text else []
    relative_scalar_update = _RELATIVE_BUDGET_UPDATE.search(text) is not None
    reasons: list[str] = []
    if len(clauses) > 1 and stop_operations:
        reasons.append("multiple_clauses")
    if len(operations) > 1 and not _is_single_budget_constraint_bundle(operations):
        reasons.append("multiple_operations")
    if has_temporal_binding and stop_operations:
        reasons.append("temporal_relation")
    if len(budget_values) > 1:
        reasons.append("multiple_numeric_candidates")
    if relative_scalar_update:
        reasons.append("relative_scalar_update")
    if _stop_alternative_expressions(text):
        reasons.append("alternative_choice")
    if _has_stop_local_constraint_semantics(text):
        reasons.append("stop_local_constraints")
    if not stop_operations and _UNPARSED_NAMED_STOP_CUE.search(text) is not None:
        reasons.append("unparsed_named_stop")
    reasons.extend(detection.reasons)
    if detection.is_candidate and not stop_operations:
        reasons.append("deterministic_parse_incomplete")
    if not deterministic_date_parse_is_complete(text, runtime_context, deterministic_result):
        reasons.append("partial_parse")
    return tuple(dict.fromkeys(reasons))


def _has_stop_local_constraint_semantics(text: str) -> bool:
    """Detect typed constraints whose scope is one requested or edited stop."""

    return bool(_stop_local_constraint_fields(text))


def _stop_local_constraint_fields(text: str) -> set[str]:
    return {
        field
        for _, field, _ in _stop_local_constraint_obligations(text)
    }


def _stop_local_constraint_obligations(text: str) -> list[tuple[str, str, str]]:
    return [
        (clause.source_text, field, marker.group(0))
        for clause in split_date_clauses(text)
        for marker_pattern, field in _STOP_LOCAL_CONSTRAINT_MARKERS
        for marker in marker_pattern.finditer(clause.text)
        if _stop_role_is_near_marker(clause.text, marker.start(), marker.end())
    ]


def _stop_role_is_near_marker(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - 30) : min(len(text), end + 30)]
    return _STOP_LOCAL_ROLE_CUE.search(window) is not None


def _stop_local_constraint_obligations_are_covered(
    obligations: list[tuple[str, str, str]],
    operations: tuple[DatePlanOperation, ...],
) -> bool:
    used_operation_fields: set[tuple[int, str]] = set()
    for clause, field, marker in obligations:
        match = next(
            (
                index
                for index, operation in enumerate(operations)
                if (index, field) not in used_operation_fields
                and _operation_covers_stop_local_constraint(
                    operation,
                    clause,
                    field,
                    marker,
                )
            ),
            None,
        )
        if match is None:
            return False
        used_operation_fields.add((match, field))
    return True


def _operation_covers_stop_local_constraint(
    operation: DatePlanOperation,
    clause: str,
    field: str,
    marker: str,
) -> bool:
    if (
        operation.type not in _STOP_OPERATIONS
        or operation.payload is None
        or operation.payload.constraints is None
        or getattr(operation.payload.constraints, field) is None
        or operation.source_span is None
    ):
        return False
    source = _normalized(operation.source_span)
    return bool(
        source
        and source in _normalized(clause)
        and _normalized(marker) in source
    )


def _is_single_budget_constraint_bundle(
    operations: tuple[DatePlanOperation, ...],
) -> bool:
    return all(
        operation.type == DateOperationType.UPDATE_CONSTRAINT
        and operation.constraint_field
        in {DateConstraintField.BUDGET, DateConstraintField.BUDGET_SCOPE}
        for operation in operations
    )


def deterministic_date_parse_is_complete(
    text: str,
    runtime_context: RuntimeContext | None,
    deterministic_result: DateOperationResolution,
) -> bool:
    if deterministic_result.unresolved_references:
        return False
    if deterministic_result.rejected and not deterministic_result.operations:
        return False
    operations = deterministic_result.operations
    detection = detect_date_modification(text, _current_plan(runtime_context))
    stop_operations = [
        operation for operation in operations if operation.type in _STOP_OPERATIONS
    ]
    if detection.is_candidate and not stop_operations:
        return False
    expected_stop_constraints = _stop_local_constraint_obligations(text)
    if not _stop_local_constraint_obligations_are_covered(
        expected_stop_constraints,
        operations,
    ):
        return False
    relative_match = _RELATIVE_BUDGET_UPDATE.search(text)
    if relative_match is not None:
        expected = int(relative_match.group("value"))
        if not any(
            operation.type == DateOperationType.UPDATE_CONSTRAINT
            and operation.constraint_field == DateConstraintField.BUDGET
            and operation.constraint_value == expected
            for operation in operations
        ):
            return False
    alternative_expressions = _stop_alternative_expressions(text)
    if alternative_expressions and not _alternative_expressions_are_covered(
        alternative_expressions,
        operations,
    ):
        return False
    for clause in split_date_clauses(text):
        clause_detection = detect_date_modification(
            clause.text,
            _current_plan(runtime_context),
        )
        if clause_detection.requests_replacement and not any(
            operation.type == DateOperationType.REPLACE_STOP for operation in operations
        ):
            return False
        if _EXPLICIT_MOVE_CUE.search(clause.text) is not None and not any(
            operation.type == DateOperationType.MOVE_STOP for operation in operations
        ):
            return False
    return True


def _deterministic_operations(
    text: str,
    runtime_context: RuntimeContext | None,
    patch: DatePlanPatch,
) -> list[DatePlanOperation]:
    operations = _constraint_operations(text, patch)
    requirement_updates = _existing_requirement_update_operations(text, runtime_context)
    operations.extend(requirement_updates)
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

    excluded = {
        *replacement_keywords,
        *removal_keywords,
        *patch.excluded_keywords,
        *_regrouped_stop_values(requirement_updates),
    }
    desired_stops = [
        *_desired_stops(text),
        *_named_desired_stops(text),
        *_patch_desired_stops(text, patch),
    ]
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


def _existing_requirement_update_operations(
    text: str,
    runtime_context: RuntimeContext | None,
) -> list[DatePlanOperation]:
    active = runtime_context.active_date_plan if runtime_context is not None else None
    if active is None:
        return []
    operations: list[DatePlanOperation] = []
    for clause in split_date_clauses(text):
        if (
            _REQUIREMENT_REGROUP_CUE.search(clause.text) is None
            or _NEGATED_REQUIREMENT_REGROUP_CUE.search(clause.text) is not None
        ):
            continue
        referenced: list[RequirementReference] = []
        kind_families: set[str] = set()
        mentioned_requirement_ids = _mentioned_requirement_ids(
            clause.text,
            active.requirements,
        )
        for requirement in active.requirements:
            if len(requirement.alternatives) != 1:
                continue
            alternative = requirement.alternatives[0]
            value = alternative.keyword or alternative.place_name
            if value is None or requirement.id not in mentioned_requirement_ids:
                continue
            referenced.append(
                RequirementReference(
                    requirement_id=requirement.id,
                    stop_reference=StopReference(
                        keyword=alternative.keyword,
                        place_name=alternative.place_name,
                        meal_type=alternative.meal_type,
                    ),
                )
            )
            kind_families.add(
                "dining"
                if alternative.kind in {StopKind.DINING, StopKind.CAFE}
                else alternative.kind.value
            )
        if len(referenced) < 2 or len(kind_families) != 1:
            continue
        operations.append(
            DatePlanOperation(
                type=DateOperationType.UPDATE_REQUIREMENT,
                requirement_update=DateRequirementUpdate(
                    targets=referenced,
                    min_satisfied=1,
                    max_satisfied=1,
                ),
                source_span=clause.source_text,
                confidence=1,
            )
        )
    return operations


def _regrouped_stop_values(operations: list[DatePlanOperation]) -> set[str]:
    return {
        value
        for operation in operations
        if operation.requirement_update is not None
        for reference in operation.requirement_update.targets
        if reference.stop_reference is not None
        for value in (
            reference.stop_reference.keyword,
            reference.stop_reference.place_name,
        )
        if value is not None
    }


def _mentioned_requirement_ids(
    text: str,
    requirements: list[DateStopRequirement],
) -> set[str]:
    normalized = _normalize(text)
    matches: list[tuple[int, int, bool, str]] = []
    for requirement in requirements:
        if len(requirement.alternatives) != 1:
            continue
        alternative = requirement.alternatives[0]
        value = alternative.keyword or alternative.place_name
        if value is None:
            continue
        normalized_value = _normalize(value)
        for alias in dict.fromkeys(_ALIASES.get(value, (value,))):
            normalized_alias = _normalize(alias)
            for match in re.finditer(re.escape(normalized_alias), normalized):
                matches.append(
                    (
                        match.start(),
                        match.end(),
                        normalized_alias == normalized_value,
                        requirement.id,
                    )
                )

    claimed_spans: list[tuple[int, int]] = []
    mentioned: set[str] = set()
    for start, end, _exact, requirement_id in sorted(
        matches,
        key=lambda item: (-(item[1] - item[0]), -int(item[2]), item[0], item[3]),
    ):
        if any(
            start >= claimed_start and end <= claimed_end
            for claimed_start, claimed_end in claimed_spans
        ):
            continue
        claimed_spans.append((start, end))
        mentioned.add(requirement_id)
    return mentioned


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
        replacement_text = match.group("replacement")
        used_keywords.update((target_keyword, replacement_keyword))
        operations.append(
            DatePlanOperation(
                type=DateOperationType.REPLACE_STOP,
                target=StopReference(keyword=target_keyword),
                payload=DesiredDateStop(
                    kind=replacement_kind,
                    keyword=replacement_keyword,
                    generic_replacement=(
                        _GENERIC_REPLACEMENT_VALUE_CUE.search(replacement_text)
                        is not None
                    ),
                    meal_type=(
                        _meal_type_for_stop(match.group(0), replacement_keyword)
                        if replacement_kind in {StopKind.DINING, StopKind.CAFE}
                        else None
                    ),
                    after=_after_anchor(match.group(0), replacement_keyword),
                    before=_before_anchor(match.group(0), replacement_keyword),
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
    payloads = [
        (desired, source_span)
        for desired, source_span in _patch_desired_stops(text, patch)
        if _REPLACE_CUE.search(source_span) is not None
    ]
    payload_keywords = {
        value
        for desired, _source_span in payloads
        for value in (desired.keyword, desired.place_name)
        if value is not None
    }
    target = _replacement_target(runtime_context, patch, payloads)
    if target is None:
        # During collection there is no old node to replace. Preserve the
        # requested stop as an ADD; once a plan exists, unresolved replacement
        # targets remain fail-closed instead of silently appending a stop.
        return (
            ([], payload_keywords)
            if _current_plan(runtime_context) is not None
            else ([], set())
        )
    target_reference, target_kind, target_keywords = target
    compatible_payloads = [
        (desired, source_span)
        for desired, source_span in payloads
        if desired.kind == target_kind
        or {desired.kind, target_kind} <= {StopKind.DINING, StopKind.CAFE}
    ]
    if len(compatible_payloads) != 1:
        return [], payload_keywords
    desired, _source_span = compatible_payloads[0]
    used_keywords = {
        *target_keywords,
        *(value for value in (desired.keyword, desired.place_name) if value is not None),
    }
    return [
        DatePlanOperation(
            type=DateOperationType.REPLACE_STOP,
            target=target_reference,
            payload=desired,
            source_span=text,
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
            return StopReference(place_name=target_name), next(iter(payload_kinds)), {target_name}
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
        if _NEGATED_MUTATION_CUE.search(clause) is not None:
            continue
        for keyword, _kind in _stops_in_text(clause):
            if keyword in excluded or not _has_explicit_removal_for_stop(
                clause,
                keyword,
            ):
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


def _has_explicit_removal_for_stop(text: str, keyword: str) -> bool:
    for alias in _aliases_for_stop(keyword):
        escaped = re.escape(alias)
        if _postfix_remove_cue_targets_alias(text, alias):
            return True
        if re.search(rf"不要\s*保留\s*{escaped}", text):
            return True
        if re.search(
            rf"不要\s*{escaped}(?:了)?(?=$|[，,。；;！？!?\n])",
            text,
        ):
            return True
        if re.search(
            rf"(?:不去|不要(?:再)?(?:去|吃|看|逛))[^，,。；;！？!?\n]{{0,8}}{escaped}",
            text,
        ):
            return True
        if _direct_remove_cue_targets_alias(text, alias):
            return True
    return False


def _direct_remove_cue_targets_alias(text: str, alias: str) -> bool:
    for alias_match in re.finditer(re.escape(alias), text):
        for cue in _DIRECT_REMOVE_CUE.finditer(text):
            if cue.end() <= alias_match.start():
                between = text[cue.end() : alias_match.start()]
                if _remove_cue_can_cross_to_target(between):
                    return True
            elif alias_match.end() <= cue.start():
                between = text[alias_match.end() : cue.start()]
                if _remove_cue_can_cross_to_target(between):
                    return True
    return False


def _postfix_remove_cue_targets_alias(text: str, alias: str) -> bool:
    for alias_match in re.finditer(re.escape(alias), text):
        for cue in _POSTFIX_REMOVE_CUE.finditer(text):
            if alias_match.end() > cue.start():
                continue
            between = text[alias_match.end() : cue.start()]
            if len(between) <= 8 and _remove_cue_can_cross_to_target(between):
                return True
    return False


def _remove_cue_can_cross_to_target(text: str) -> bool:
    if _REMOVE_SCOPE_CONFLICT_CUE.search(text) is not None:
        return False
    intervening_stops = _stops_in_text(text)
    if not intervening_stops:
        return True
    remainder = text
    for keyword, _kind in intervening_stops:
        for alias in _aliases_for_stop(keyword):
            remainder = re.sub(re.escape(alias), "", remainder)
    return not _REMOVE_MULTI_TARGET_LIST_FILLER.sub("", _normalized(remainder))


def _desired_stops(text: str) -> list[tuple[DesiredDateStop, str]]:
    desired: list[tuple[DesiredDateStop, str]] = []
    for parsed_clause in split_date_clauses(text):
        clause = parsed_clause.text
        for keyword, kind in _stops_in_text(clause):
            time_window = _time_window(clause)
            desired.append(
                (
                    DesiredDateStop(
                        kind=kind,
                        keyword=keyword,
                        meal_type=(
                            _meal_type_for_stop(
                                clause,
                                keyword,
                                time_window=time_window,
                            )
                            if kind in {StopKind.DINING, StopKind.CAFE}
                            else None
                        ),
                        target_day=_target_day(clause),
                        time_window=time_window,
                        after=_after_anchor(clause, keyword),
                        before=_before_anchor(clause, keyword),
                    ),
                    parsed_clause.source_text,
                )
            )
    return desired


def _named_desired_stops(text: str) -> list[tuple[DesiredDateStop, str]]:
    desired: list[tuple[DesiredDateStop, str]] = []
    for parsed_clause in split_date_clauses(text):
        clause = parsed_clause.text
        for match in _NAMED_STOP_PATTERN.finditer(clause):
            name = match.group("name").strip()
            if _first_stop(name) is not None:
                continue
            kind = (
                StopKind.CAFE
                if "咖啡" in name
                else StopKind.DINING
                if re.search(r"餐厅|饭店|餐馆|菜馆$", name)
                else StopKind.ACTIVITY
            )
            time_window = _time_window(clause)
            desired.append(
                (
                    DesiredDateStop(
                        kind=kind,
                        place_name=name,
                        meal_type=(
                            _meal_type_for_stop(
                                clause,
                                name,
                                time_window=time_window,
                            )
                            if kind in {StopKind.DINING, StopKind.CAFE}
                            else None
                        ),
                        target_day=_target_day(clause),
                        time_window=time_window,
                        after=_after_anchor(clause, name),
                        before=_before_anchor(clause, name),
                    ),
                    parsed_clause.source_text,
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
            parsed_clause = next(
                (
                    candidate
                    for candidate in split_date_clauses(text)
                    if keyword in candidate.text
                ),
                None,
            )
            clause = parsed_clause.text if parsed_clause is not None else text
            source_span = (
                parsed_clause.source_text if parsed_clause is not None else text
            )
            time_window = _time_window(clause)
            meal_type = _patch_meal_type(keyword, patch) or (
                _meal_type_for_stop(
                    clause,
                    keyword,
                    time_window=time_window,
                )
                if kind == StopKind.DINING
                else None
            )
            desired.append(
                (
                    DesiredDateStop(
                        kind=(
                            StopKind.CAFE if kind == StopKind.DINING and "咖啡" in keyword else kind
                        ),
                        keyword=keyword,
                        meal_type=meal_type,
                        target_day=patch.target_day,
                        time_window=time_window,
                        after=_after_anchor(clause, keyword),
                        before=_before_anchor(clause, keyword),
                    ),
                    source_span,
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
            MealType(item.meal_type) if item.meal_type in MealType._value2member_map_ else None
        ),
    )


def _reference_for_existing_item(item: DatePlanItem) -> StopReference:
    return StopReference(
        place_id=item.place.id,
        place_name=item.place.name,
        keyword=item.slot_keyword,
        meal_type=(
            MealType(item.meal_type) if item.meal_type in MealType._value2member_map_ else None
        ),
    )


def _kind_for_item(item: DatePlanItem) -> StopKind:
    return {
        PlaceCategory.RESTAURANT: StopKind.DINING,
        PlaceCategory.CAFE: StopKind.CAFE,
        PlaceCategory.ATTRACTION: StopKind.ACTIVITY,
        PlaceCategory.ENTERTAINMENT: StopKind.ACTIVITY,
    }[item.place.category]


def _meal_type_for_stop(
    text: str,
    keyword: str,
    *,
    time_window: TimeWindow | None = None,
) -> MealType | None:
    keyword_positions = [match.start() for match in re.finditer(re.escape(keyword), text)]
    marker_positions = [
        (match.start(), meal_type)
        for meal_type, pattern in _MEAL_MARKER_PATTERNS
        for match in pattern.finditer(text)
    ]
    if keyword_positions and marker_positions:
        _, meal_type = min(
            marker_positions,
            key=lambda item: min(abs(item[0] - position) for position in keyword_positions),
        )
        return meal_type
    clock = time_window.start if time_window is not None else None
    if clock is not None:
        if 6 <= clock.hour <= 10:
            return MealType.BREAKFAST
        if 11 <= clock.hour <= 14:
            return MealType.LUNCH
        if 17 <= clock.hour <= 21:
            return MealType.DINNER
    return None


_MEAL_MARKER_PATTERNS = (
    (MealType.BREAKFAST, re.compile(r"早餐|早饭|早上")),
    (MealType.LUNCH, re.compile(r"午餐|午饭|中饭|中午")),
    (MealType.DINNER, re.compile(r"晚餐|晚饭|晚上")),
)


def _after_anchor(
    text: str,
    keyword: str,
) -> TemporalAnchor | StopReference | None:
    if re.search(r"(?:晚饭|晚餐|晚宴)\s*(?:后|之后)", text):
        return TemporalAnchor.DINNER
    if re.search(r"(?:午饭|午餐|中饭)\s*(?:后|之后)", text):
        return TemporalAnchor.LUNCH
    if re.search(r"(?:早餐|早饭)\s*(?:后|之后)", text):
        return TemporalAnchor.BREAKFAST
    return _relative_stop_reference(text, keyword, after=True)


def _before_anchor(
    text: str,
    keyword: str,
) -> TemporalAnchor | StopReference | None:
    if re.search(r"(?:晚饭|晚餐|晚宴)\s*(?:前|之前)", text):
        return TemporalAnchor.DINNER
    if re.search(r"(?:午饭|午餐|中饭)\s*(?:前|之前)", text):
        return TemporalAnchor.LUNCH
    if re.search(r"(?:早餐|早饭)\s*(?:前|之前)", text):
        return TemporalAnchor.BREAKFAST
    return _relative_stop_reference(text, keyword, after=False)


def _time_window(text: str) -> TimeWindow | None:
    if re.search(r"(?:晚饭|晚餐)\s*(?:后|之后)", text):
        return TimeWindow(label="晚饭后")
    if "下午" in text:
        return TimeWindow(label="下午")
    if "晚上" in text:
        return TimeWindow(label="晚上")
    if "上午" in text:
        return TimeWindow(label="上午")
    clock = re.search(r"(?<!\d)(\d{1,2})(?:点|时|:)(\d{1,2})?(?!\d)", text)
    if clock is not None:
        from datetime import time

        hour = int(clock.group(1))
        minute = int(clock.group(2) or 0)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return TimeWindow(start=time(hour, minute), label=f"{hour:02d}:{minute:02d}")
    return None


def _relative_stop_reference(
    text: str,
    keyword: str,
    *,
    after: bool,
) -> StopReference | None:
    target_aliases = _aliases_for_stop(keyword)
    for anchor_keyword, _kind, anchor_aliases in _STOP_SPECS:
        if anchor_keyword == keyword:
            continue
        for anchor_alias in anchor_aliases:
            for target_alias in target_aliases:
                escaped_anchor = re.escape(anchor_alias)
                escaped_target = re.escape(target_alias)
                if after:
                    patterns = (
                        rf"(?:看完|吃完|逛完|结束)\s*{escaped_anchor}.{{0,24}}{escaped_target}",
                        rf"{escaped_anchor}\s*(?:后|之后|结束后).{{0,24}}{escaped_target}",
                        rf"{escaped_target}.{{0,24}}{escaped_anchor}\s*(?:后|之后|结束后)",
                    )
                else:
                    patterns = (
                        rf"{escaped_anchor}\s*(?:前|之前|开始前).{{0,24}}{escaped_target}",
                        rf"{escaped_target}.{{0,24}}{escaped_anchor}\s*(?:前|之前|开始前)",
                    )
                if any(re.search(pattern, text) for pattern in patterns):
                    return StopReference(keyword=anchor_keyword)
    return None


def _aliases_for_stop(keyword: str) -> tuple[str, ...]:
    return next(
        (aliases for canonical, _kind, aliases in _STOP_SPECS if canonical == keyword),
        (keyword,),
    )


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
    return [clause.text for clause in split_date_clauses(text)]


def _dedupe_operations(
    operations: tuple[DatePlanOperation, ...],
    *,
    current_plan: DatePlan | None = None,
    source_text: str | None = None,
    merge_semantic_equivalents: bool = True,
) -> list[DatePlanOperation]:
    seen: set[str] = set()
    result: list[DatePlanOperation] = []
    for operation in operations:
        identity = json.dumps(
            operation.model_dump(
                mode="json",
                exclude=(
                    {"source_span", "confidence"}
                    if merge_semantic_equivalents
                    else {"confidence"}
                ),
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
        if identity in seen:
            continue
        seen.add(identity)
        if not merge_semantic_equivalents:
            result.append(operation)
            continue
        equivalent = next(
            (
                index
                for index, existing in enumerate(result)
                if _operations_are_semantically_equivalent(
                    existing,
                    operation,
                    current_plan=current_plan,
                    source_text=source_text,
                )
            ),
            None,
        )
        if equivalent is None:
            result.append(operation)
        else:
            result[equivalent] = _merge_equivalent_operations(
                result[equivalent],
                operation,
            )
    return result


def _operations_are_semantically_equivalent(
    first: DatePlanOperation,
    second: DatePlanOperation,
    *,
    current_plan: DatePlan | None,
    source_text: str | None,
) -> bool:
    if first.type != second.type:
        return False
    if not _operation_source_spans_overlap(first, second, source_text):
        return False
    if first.type == DateOperationType.ADD_STOP:
        if first.alternative_group != second.alternative_group:
            return False
        if first.payload is None or second.payload is None:
            return False
        return _add_payloads_are_equivalent(first.payload, second.payload)
    if first.type not in _REFERENCE_TARGETED_MUTATIONS:
        return False
    first_target = _resolved_target_place_id(current_plan, first.target)
    second_target = _resolved_target_place_id(current_plan, second.target)
    if first_target is None or first_target != second_target:
        return False
    if first.type == DateOperationType.REMOVE_STOP:
        return True
    if first.payload is None or second.payload is None:
        return False
    return _desired_payloads_are_equivalent(first.payload, second.payload)


def _operation_source_spans_overlap(
    first: DatePlanOperation,
    second: DatePlanOperation,
    source_text: str | None,
) -> bool:
    if first.source_span == second.source_span and first.source_span is not None:
        return True
    return bool(
        source_text
        and first.source_span
        and second.source_span
        and _source_spans_overlap(source_text, first.source_span, second.source_span)
    )


def _resolved_target_place_id(
    plan: DatePlan | None,
    reference: StopReference | None,
) -> str | None:
    if plan is None or reference is None:
        return None
    ordered = sorted(plan.items, key=lambda item: (item.day_index, item.order))
    if reference.ordinal is not None:
        if reference.ordinal > len(ordered):
            return None
        candidate = ordered[reference.ordinal - 1]
        has_other_identity = any(
            value is not None
            for value in (
                reference.place_id,
                reference.place_name,
                reference.keyword,
                reference.meal_type,
            )
        )
        matches = (
            [candidate]
            if not has_other_identity or item_matches_reference(candidate, reference)
            else []
        )
    else:
        matches = [
            item for item in ordered if item_matches_reference(item, reference)
        ]
    return matches[0].place.id if len(matches) == 1 else None


def _desired_payloads_are_equivalent(
    first: DesiredDateStop,
    second: DesiredDateStop,
) -> bool:
    first_family = "dining" if first.kind in {StopKind.DINING, StopKind.CAFE} else first.kind
    second_family = (
        "dining" if second.kind in {StopKind.DINING, StopKind.CAFE} else second.kind
    )
    if first_family != second_family:
        return False
    first_value = first.keyword or first.place_name
    second_value = second.keyword or second.place_name
    if first_value is not None and second_value is not None:
        if _normalize(first_value) != _normalize(second_value):
            return False
    elif not (first.generic_replacement or second.generic_replacement):
        return False
    for field in ("meal_type", "target_day", "time_window", "after", "before"):
        first_value = getattr(first, field)
        second_value = getattr(second, field)
        if first_value is not None and second_value is not None and first_value != second_value:
            return False
    return _stop_constraints_are_compatible(first.constraints, second.constraints)


def _add_payloads_are_equivalent(
    first: DesiredDateStop,
    second: DesiredDateStop,
) -> bool:
    first_family = "dining" if first.kind in {StopKind.DINING, StopKind.CAFE} else first.kind
    second_family = (
        "dining" if second.kind in {StopKind.DINING, StopKind.CAFE} else second.kind
    )
    if first_family != second_family:
        return False
    first_value = first.keyword or first.place_name
    second_value = second.keyword or second.place_name
    if (
        first_value is not None
        and second_value is not None
        and _normalize(first_value) != _normalize(second_value)
    ):
        return False
    for field in ("meal_type", "target_day", "time_window", "after", "before"):
        first_modifier = getattr(first, field)
        second_modifier = getattr(second, field)
        if (
            first_modifier is not None
            and second_modifier is not None
            and first_modifier != second_modifier
        ):
            return False
    return _stop_constraints_are_compatible(first.constraints, second.constraints)


def _stop_constraints_are_compatible(
    first: DateStopConstraints | None,
    second: DateStopConstraints | None,
) -> bool:
    if first is None or second is None:
        return True
    return all(
        left is None or right is None or left == right
        for left, right in (
            (first.max_cost_per_person, second.max_cost_per_person),
            (first.min_rating, second.min_rating),
            (first.preferred_area, second.preferred_area),
            (first.max_distance_meters, second.max_distance_meters),
        )
    )


def _merge_equivalent_operations(
    first: DatePlanOperation,
    second: DatePlanOperation,
) -> DatePlanOperation:
    target = max(
        (candidate for candidate in (first.target, second.target) if candidate is not None),
        key=_target_specificity,
        default=None,
    )
    payload = None
    if first.payload is not None and second.payload is not None:
        payload = _merge_equivalent_payloads(first.payload, second.payload)
    else:
        payload = first.payload or second.payload
    preferred = max((first, second), key=_operation_specificity)
    return preferred.model_copy(
        update={
            "target": target,
            "payload": payload,
            "confidence": max(
                value
                for value in (first.confidence, second.confidence, 0.0)
                if value is not None
            ),
        }
    )


def _merge_equivalent_payloads(
    first: DesiredDateStop,
    second: DesiredDateStop,
) -> DesiredDateStop:
    preferred, fallback = sorted(
        (first, second),
        key=_payload_specificity,
        reverse=True,
    )
    updates = {
        field: getattr(preferred, field) or getattr(fallback, field)
        for field in (
            "keyword",
            "place_name",
            "meal_type",
            "target_day",
            "time_window",
            "after",
            "before",
        )
    }
    updates["constraints"] = _merge_stop_constraints(
        preferred.constraints,
        fallback.constraints,
    )
    updates["generic_replacement"] = (
        first.generic_replacement and second.generic_replacement
    )
    updates["replacement_preferences"] = list(
        dict.fromkeys(
            [*first.replacement_preferences, *second.replacement_preferences]
        )
    )
    return preferred.model_copy(update=updates)


def _merge_stop_constraints(
    preferred: DateStopConstraints | None,
    fallback: DateStopConstraints | None,
) -> DateStopConstraints | None:
    if preferred is None:
        return fallback
    if fallback is None:
        return preferred
    return preferred.model_copy(
        update={
            field: (
                getattr(preferred, field)
                if getattr(preferred, field) is not None
                else getattr(fallback, field)
            )
            for field in (
                "max_cost_per_person",
                "min_rating",
                "preferred_area",
                "max_distance_meters",
            )
        }
    )


def _operation_specificity(operation: DatePlanOperation) -> int:
    return _target_specificity(operation.target) + _payload_specificity(operation.payload)


def _target_specificity(reference: StopReference | None) -> int:
    if reference is None:
        return 0
    return sum(
        score
        for value, score in (
            (reference.place_id, 8),
            (reference.ordinal, 6),
            (reference.place_name, 4),
            (reference.keyword, 2),
            (reference.meal_type, 1),
        )
        if value is not None
    )


def _payload_specificity(payload: DesiredDateStop | None) -> int:
    if payload is None:
        return 0
    values = (
        payload.keyword,
        payload.place_name,
        payload.meal_type,
        payload.target_day,
        payload.time_window,
        payload.after,
        payload.before,
        payload.constraints,
    )
    return sum(value is not None for value in values) + 3 * payload.generic_replacement + len(
        payload.replacement_preferences
    )


def _prefer_grouped_additions(
    operations: list[DatePlanOperation],
    deterministic: list[DatePlanOperation],
    text: str,
) -> list[DatePlanOperation]:
    deterministic_ids = {id(operation) for operation in deterministic}
    grouped = [
        operation
        for operation in operations
        if operation.type == DateOperationType.ADD_STOP
        and operation.payload is not None
        and operation.alternative_group is not None
    ]
    return [
        operation
        for operation in operations
        if not (
            id(operation) in deterministic_ids
            and
            operation.type == DateOperationType.ADD_STOP
            and operation.payload is not None
            and operation.alternative_group is None
            and any(
                _grouped_addition_refines(operation, candidate, text)
                for candidate in grouped
            )
        )
    ]


def _grouped_addition_refines(
    deterministic: DatePlanOperation,
    grouped: DatePlanOperation,
    text: str,
) -> bool:
    current = deterministic.payload
    incoming = grouped.payload
    if current is None or incoming is None:
        return False
    current_value = current.keyword or current.place_name
    incoming_value = incoming.keyword or incoming.place_name
    if (
        current.kind != incoming.kind
        or current_value is None
        or incoming_value is None
        or _normalize(current_value) != _normalize(incoming_value)
        or not _source_spans_overlap(text, deterministic.source_span, grouped.source_span)
    ):
        return False
    for current_field, incoming_field in (
        (current.keyword, incoming.keyword),
        (current.place_name, incoming.place_name),
        (current.meal_type, incoming.meal_type),
        (current.target_day, incoming.target_day),
        (current.time_window, incoming.time_window),
        (current.after, incoming.after),
        (current.before, incoming.before),
        (current.constraints, incoming.constraints),
    ):
        if current_field is not None and current_field != incoming_field:
            return False
    if current.generic_replacement and not incoming.generic_replacement:
        return False
    return set(current.replacement_preferences).issubset(incoming.replacement_preferences)


def _source_spans_overlap(
    text: str,
    first: str | None,
    second: str | None,
) -> bool:
    if first is None or second is None:
        return False
    first_ranges = _source_span_ranges(text, first)
    second_ranges = _source_span_ranges(text, second)
    overlaps = [
        (first_range, second_range)
        for first_range in first_ranges
        for second_range in second_ranges
        if max(first_range[0], second_range[0]) < min(first_range[1], second_range[1])
    ]
    return len(overlaps) == 1


def _source_span_ranges(text: str, span: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 0
    while (position := text.find(span, start)) >= 0:
        ranges.append((position, position + len(span)))
        start = position + 1
    return ranges


def _alternative_expressions_are_covered(
    expressions: tuple[str, ...],
    operations: tuple[DatePlanOperation, ...],
) -> bool:
    groups: dict[str, list[DatePlanOperation]] = {}
    for operation in operations:
        if (
            operation.type == DateOperationType.ADD_STOP
            and operation.alternative_group is not None
            and operation.payload is not None
        ):
            groups.setdefault(operation.alternative_group, []).append(operation)
    complete_groups = [members for members in groups.values() if len(members) >= 2]
    return all(
        any(_group_is_within_expression(group, expression) for group in complete_groups)
        for expression in expressions
    )


def _group_is_within_expression(
    group: list[DatePlanOperation],
    expression: str,
) -> bool:
    normalized = _normalize(expression)
    return all(
        operation.payload is not None
        and (
            value := operation.payload.keyword or operation.payload.place_name
        )
        is not None
        and any(
            _normalize(alias) in normalized
            for alias in _ALIASES.get(value, (value,))
        )
        for operation in group
    )


def _stop_alternative_expressions(text: str) -> tuple[str, ...]:
    matches: list[tuple[int, int, str]] = []
    for pattern in (_INFIX_STOP_ALTERNATIVE, _POSTFIX_STOP_ALTERNATIVE):
        for match in pattern.finditer(text):
            left = match.group("left")
            right = match.group("right")
            if not (_looks_like_stop_phrase(left) and _looks_like_stop_phrase(right)):
                continue
            span = match.span("expression")
            if any(span[0] < end and start < span[1] for start, end, _ in matches):
                continue
            matches.append((*span, match.group("expression")))
    return tuple(value for _, _, value in sorted(matches))


def _looks_like_stop_phrase(value: str) -> bool:
    return _STOP_CHOICE_VALUE_CUE.search(value) is not None


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold())


def _normalized(value: str) -> str:
    return _normalize(value).strip("，,。；;()（）")


_REPLAN_CUE = re.compile(r"重新.{0,4}(?:规划|安排)|换一套|全部重排|从头安排|重新来")
_DIRECT_REMOVE_CUE = re.compile(r"删除|删掉|去掉|移除|取消")
_POSTFIX_REMOVE_CUE = re.compile(r"不要(?:了|保留)")
_REMOVE_SCOPE_CONFLICT_CUE = re.compile(
    r"保留|留下|但|不过|然而|(?:调|移|挪|放|换|改)(?:到|成|为)?"
)
_REMOVE_MULTI_TARGET_LIST_FILLER = re.compile(
    r"(?:和|与|跟|及|以及|还有|同时|并且|加上|、|/|都|[，,])+"
)
_NEGATED_MUTATION_CUE = re.compile(
    r"(?:不要|别|不用|无需|不必|不能|不准|不)(?:再)?\s*(?:把\s*)?"
    r"[^，,。；;！？!?\n]{0,16}?"
    r"(?:删(?:除|掉)?|去掉|移除|取消|替换|更换|换掉|换成|换为|换个|换|"
    r"动|改|调整|调到|调|移动|移到|挪到|挪|放到|放|提前|推后)"
)
_STOP_CHOICE_LEFT_FRAGMENT = r"[^，,。；;！？!?\n]{1,30}?"
_STOP_CHOICE_RIGHT_FRAGMENT = r"[^，,。；;！？!?\n]{1,30}"
_INFIX_STOP_ALTERNATIVE = re.compile(
    rf"(?P<expression>(?P<left>{_STOP_CHOICE_LEFT_FRAGMENT})"
    rf"(?:或者|或是|要么|还是)(?P<right>{_STOP_CHOICE_RIGHT_FRAGMENT}))"
)
_POSTFIX_STOP_ALTERNATIVE = re.compile(
    rf"(?P<expression>(?P<left>{_STOP_CHOICE_LEFT_FRAGMENT})[，,、]"
    rf"(?P<right>{_STOP_CHOICE_LEFT_FRAGMENT})"
    rf"(?:也行|也可以|都可以|均可|任选|二选一))"
)
_STOP_CHOICE_VALUE_CUE = re.compile(
    r"火锅|烧烤|烤肉|日料|西餐|咖啡|电影|影院|剧场|景点|公园|展览|活动|"
    r"[\u4e00-\u9fff]{1,12}(?:馆|店|餐厅|饭店|乐园)"
)
_NAMED_STOP_PATTERN = re.compile(
    r"(?:想去|再去|准备去|打算去|想逛|去|逛)\s*"
    r"(?P<name>(?!哪|哪里|什么|一家|一个|这个|那个|活动|吃饭|用餐)"
    r"[\u4e00-\u9fffA-Za-z0-9·]{2,24}?"
    r"(?:明珠|外滩|塔|楼|馆|院|园|店|中心|广场|乐园|寺|街|山|湖|滩))"
    r"(?=$|[，,。；;！？!?])"
)
_UNPARSED_NAMED_STOP_CUE = _NAMED_STOP_PATTERN
_REPLACE_CUE = re.compile(r"替换|更换|换成|换为|换一个|换一家|换个|改成|改为")
_RELATIVE_BUDGET_UPDATE = re.compile(
    r"(?:总预算|预算)\s*从\s*\d{2,6}\s*(?:元|块)?\s*"
    r"(?:提高|增加|上调|涨|降低|下降|下调|降)\s*(?:到|至|为)\s*"
    r"(?P<value>\d{2,6})\s*(?:元|块)?"
)
_EXPLICIT_MOVE_CUE = re.compile(
    r"(?:电影|影院|餐厅|饭店|景点|活动|第\s*[一二三四五1-5]\s*个.{0,8})"
    r".{0,8}(?:放(?:到)?|移到|挪到|提前|推后).{0,8}"
    r"(?:前|后|上午|下午|晚上|早饭|午饭|晚饭)"
)
_STOP_LOCAL_ROLE = (
    r"(?:早餐|午餐|晚餐|餐厅|饭店|咖啡|电影|影院|景点|场馆|活动|"
    r"法餐|西餐|日料|火锅|烧烤)"
)
_STOP_LOCAL_PRICE_VALUE = (
    r"(?:人均|每人|每位|客单)\s*(?:(?:不超过|最多|至多|上限)\s*"
    r"\d{1,6}(?:\.\d+)?\s*(?:元|块)?|\d{1,6}(?:\.\d+)?\s*"
    r"(?:元|块)?\s*(?:以内|以下|不超过|最多|至多))"
)
_STOP_LOCAL_RATING_VALUE = (
    r"(?:评分|星级)\s*(?:(?:不低于|至少|最低)\s*\d(?:\.\d+)?\s*(?:分)?|"
    r"\d(?:\.\d+)?\s*(?:分)?\s*(?:以上|不低于|至少))"
)
_STOP_LOCAL_DISTANCE_VALUE = (
    r"(?:距离|相距|路程).{0,12}(?:(?:不超过|最多|至多)\s*"
    r"\d{1,6}(?:\.\d+)?\s*(?:米|公里|km|m)|\d{1,6}(?:\.\d+)?\s*"
    r"(?:米|公里|km|m)\s*(?:以内|以下|不超过|最多|至多))"
)
_STOP_LOCAL_PRICE_CUE = re.compile(
    rf"(?:{_STOP_LOCAL_ROLE}.{{0,30}}{_STOP_LOCAL_PRICE_VALUE}|"
    rf"{_STOP_LOCAL_PRICE_VALUE}.{{0,30}}{_STOP_LOCAL_ROLE})"
)
_STOP_LOCAL_RATING_CUE = re.compile(
    rf"(?:{_STOP_LOCAL_ROLE}.{{0,30}}{_STOP_LOCAL_RATING_VALUE}|"
    rf"{_STOP_LOCAL_RATING_VALUE}.{{0,30}}{_STOP_LOCAL_ROLE})"
)
_STOP_LOCAL_AREA_CUE = re.compile(
    rf"(?:{_STOP_LOCAL_ROLE}.{{0,30}}(?:附近|周边|一带)|"
    rf"(?:附近|周边|一带).{{0,30}}{_STOP_LOCAL_ROLE})"
)
_STOP_LOCAL_DISTANCE_CUE = re.compile(
    rf"(?:{_STOP_LOCAL_ROLE}.{{0,30}}{_STOP_LOCAL_DISTANCE_VALUE}|"
    rf"{_STOP_LOCAL_DISTANCE_VALUE}.{{0,30}}{_STOP_LOCAL_ROLE})"
)
_STOP_LOCAL_ROLE_CUE = re.compile(_STOP_LOCAL_ROLE)
_STOP_LOCAL_CONSTRAINT_MARKERS = (
    (re.compile(_STOP_LOCAL_PRICE_VALUE), "max_cost_per_person"),
    (re.compile(_STOP_LOCAL_RATING_VALUE), "min_rating"),
    (re.compile(r"附近|周边|一带"), "preferred_area"),
    (re.compile(_STOP_LOCAL_DISTANCE_VALUE), "max_distance_meters"),
)
_STOP_OPERATIONS = {
    DateOperationType.UPDATE_REQUIREMENT,
    DateOperationType.ADD_STOP,
    DateOperationType.REMOVE_STOP,
    DateOperationType.REPLACE_STOP,
    DateOperationType.MOVE_STOP,
}
_REQUIREMENT_REGROUP_CUE = re.compile(
    r"(?:二选一|任选(?:其一|一个)?|选一个就行|任意一个|(?:或者|或是|要么|还是).{0,24}(?:都行|均可|即可))"
)
_NEGATED_REQUIREMENT_REGROUP_CUE = re.compile(
    r"(?:不要|别|不用|无需|不再|取消|撤销)(?:再)?(?:搞|做|按|改成|设成|设置成)?\s*"
    r"(?:二选一|任选(?:其一|一个)?|选一个)"
)
_REFERENCE_TARGETED_MUTATIONS = {
    DateOperationType.REMOVE_STOP,
    DateOperationType.REPLACE_STOP,
    DateOperationType.MOVE_STOP,
}
_REPLACEMENT_PATTERN = re.compile(
    r"(?:把|将)?\s*(?P<target>[^，,。；;]{1,24}?)\s*"
    r"(?:替换为|更换为|换成|换为|改成|改为)\s*"
    r"(?P<replacement>[^，,。；;]{1,24})"
)
_GENERIC_REPLACEMENT_VALUE_CUE = re.compile(
    r"(?:另(?:一|外)?(?:个|家)?|别的|其他)(?:\S{0,12})"
)
_STOP_SPECS = (
    ("法餐", StopKind.DINING, ("法餐", "法国菜", "法国料理")),
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

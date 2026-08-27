import re
import unicodedata
from collections.abc import Iterable, Mapping
from datetime import timedelta
from time import perf_counter

from loveapp.application.conversation_flow import is_pending_cancellation
from loveapp.application.date_planning.clause_parsing import split_date_clauses
from loveapp.application.date_planning.fact_parsing import (
    DateFactParser,
    extract_requested_day_count,
)
from loveapp.application.date_planning.modification_detection import (
    looks_like_date_modification_semantics,
)
from loveapp.application.date_planning.operation_resolution import (
    DateOperationResolver,
    date_semantic_parse_reasons,
    deterministic_date_parse_is_complete,
    requires_date_semantic_parse,
)
from loveapp.application.route_slot_validation import (
    SlotValidationResult,
    merge_current_turn_slot_sources,
    merge_route_slot_sources,
    validate_current_turn_route_slots,
    validate_route_slots,
)
from loveapp.domain.date_operations import DateOperationType, DatePlanOperation
from loveapp.domain.date_patch import DatePlanPatch, SlotSource
from loveapp.domain.date_plan import MAX_TRIP_DAYS
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.enums import (
    AdviceGoal,
    AdviceScenario,
    DatePlanMode,
    DatePlanMutation,
    DateRequestMode,
    DateTaskIntent,
    RiskLevel,
    RouteSource,
    TaskType,
)
from loveapp.domain.memory import MessageRole
from loveapp.domain.routing import DatePlanSlots, RouteCorrection, RouteInput, RouteResult
from loveapp.ports.date_semantics import DateSemanticParser
from loveapp.ports.routing import RouteCorrector
from loveapp.safety import SafetyPolicy

_DATE_FACT_PARSER = DateFactParser()
_DATE_OPERATION_RESOLVER = DateOperationResolver()


class HybridRouter:
    def __init__(
        self,
        safety_policy: SafetyPolicy,
        corrector: RouteCorrector | None = None,
        *,
        confidence_threshold: float = 0.72,
        ambiguity_margin: float = 0.16,
        clarification_threshold: float = 0.68,
        prompt_version: str = "routing-v3.0",
        date_semantic_parser: DateSemanticParser | None = None,
    ) -> None:
        self._safety_policy = safety_policy
        self._corrector = corrector
        self._confidence_threshold = confidence_threshold
        self._ambiguity_margin = ambiguity_margin
        self._clarification_threshold = clarification_threshold
        self._prompt_version = prompt_version
        self._date_semantic_parser = date_semantic_parser or (
            corrector if hasattr(corrector, "parse_date_operations") else None
        )

    async def route(self, route_input: RouteInput) -> RouteResult:
        normalized = normalize_route_text(route_input.latest_query)
        safety = self._safety_policy.assess(
            normalized,
            recent_messages=route_input.recent_messages,
            previous_risk_state=route_input.previous_risk_state,
        )
        result = route_by_rules(route_input, normalized)
        result = result.model_copy(
            update={
                "risk_level": safety.risk_level,
                "risk_reasons": safety.reasons,
                "recent_risk_inherited": safety.inherited,
                "recent_risk_deescalated": safety.deescalated,
                "router_prompt_version": self._prompt_version,
            }
        )
        if safety.risk_level != RiskLevel.NORMAL or self._corrector is None:
            return await self._finalize_route(route_input, result)
        # Exact casual messages are a deterministic fast path.  This guard is
        # intentionally after the safety scan so a safety rule always wins.
        if _is_exact_casual_chat(normalized):
            return await self._finalize_route(route_input, result)
        if _is_clear_out_of_scope(result):
            return await self._finalize_route(route_input, result)
        if not self._needs_llm_correction(route_input, result):
            return await self._finalize_route(route_input, result)

        started = perf_counter()
        try:
            correction = await self._corrector.correct(route_input, result)
        except Exception as exc:
            telemetry = _corrector_telemetry(self._corrector)
            duration_ms = telemetry.get("duration_ms")
            if not isinstance(duration_ms, (int, float)) or duration_ms < 0:
                duration_ms = round((perf_counter() - started) * 1000, 3)
            return await self._finalize_route(
                route_input,
                result.model_copy(
                    update={
                        "llm_error": str(exc)[:300],
                        "fallback_reason": "llm_correction_failed",
                        "router_model": telemetry.get("model"),
                        "router_input_tokens": telemetry.get("input_tokens"),
                        "router_output_tokens": telemetry.get("output_tokens"),
                        "router_duration_ms": duration_ms,
                    }
                ),
            )
        telemetry = _corrector_telemetry(self._corrector)
        latest_rule_slots = extract_date_plan_slots(
            RouteInput(latest_query=route_input.latest_query)
        )
        correction_slots = _date_slots_from_correction(correction)
        task_slots = (
            _date_slots_from_task_state(route_input.date_task_state)
            if route_input.date_task_state is not None and route_input.date_task_state.is_resumable
            else DatePlanSlots()
        )
        slot_validation = validate_route_slots(
            route_input,
            latest_rule_slots,
            correction_slots,
            task_slots,
        )
        current_turn_slot_validation = validate_current_turn_route_slots(
            route_input,
            latest_rule_slots,
            correction_slots,
        )
        current_turn_date_plan, _, current_turn_field_sources = merge_current_turn_slot_sources(
            latest_rule_slots,
            current_turn_slot_validation.validated_slots,
        )
        merged = merge_route_correction(
            route_input,
            result,
            correction,
            allow_task_override=self._allow_task_override(route_input, result, correction),
            validated_date_plan=slot_validation.validated_slots,
            slot_validation=slot_validation,
            rule_date_plan=latest_rule_slots,
            task_date_plan=task_slots,
            current_turn_date_plan=current_turn_date_plan,
            current_turn_field_sources=current_turn_field_sources,
        )
        structural_slot_rejections = telemetry.get("slot_parse_rejections", {})
        if not isinstance(structural_slot_rejections, dict):
            structural_slot_rejections = {}
        if structural_slot_rejections:
            merged = merged.model_copy(
                update={
                    "slot_rejected_fields": {
                        **{
                            str(field): str(reason)
                            for field, reason in structural_slot_rejections.items()
                        },
                        **merged.slot_rejected_fields,
                    }
                }
            )
        merged = merged.model_copy(
            update={
                "router_model": telemetry.get("model"),
                "router_input_tokens": telemetry.get("input_tokens"),
                "router_output_tokens": telemetry.get("output_tokens"),
                "router_duration_ms": telemetry.get(
                    "duration_ms", round((perf_counter() - started) * 1000, 3)
                ),
            }
        )
        return await self._finalize_route(route_input, merged)

    async def _finalize_route(
        self,
        route_input: RouteInput,
        result: RouteResult,
    ) -> RouteResult:
        result = await self._apply_date_semantic_parse(route_input, result)
        return self._finalize_route_metadata(route_input, result)

    async def _apply_date_semantic_parse(
        self,
        route_input: RouteInput,
        result: RouteResult,
    ) -> RouteResult:
        clauses = split_date_clauses(route_input.latest_query)
        date_authorized = (
            result.risk_level == RiskLevel.NORMAL
            and (
                result.task_type == TaskType.DATE_PLANNING
                or TaskType.DATE_PLANNING in result.secondary_tasks
            )
            and result.date_patch is not None
        )
        if not date_authorized:
            return result.model_copy(update={"date_clause_count": len(clauses)})
        deterministic = _DATE_OPERATION_RESOLVER.resolve(
            route_input.latest_query,
            route_input.runtime_context,
            result.date_patch,
        )
        required = requires_date_semantic_parse(
            route_input.latest_query,
            route_input.runtime_context,
            deterministic,
        )
        parse_reasons = date_semantic_parse_reasons(
            route_input.latest_query,
            route_input.runtime_context,
            deterministic,
        )
        deterministic_complete = deterministic_date_parse_is_complete(
            route_input.latest_query,
            route_input.runtime_context,
            deterministic,
        )
        profile = getattr(self._date_semantic_parser, "semantic_profile", {})
        base_update = {
            "date_clause_count": len(clauses),
            "date_semantic_parse_required": required,
            "date_semantic_parse_reason": "+".join(parse_reasons) or None,
            "date_semantic_trigger_reasons": list(parse_reasons),
            **_date_semantic_route_telemetry(
                profile if isinstance(profile, Mapping) else {}
            ),
            "date_unresolved_references": list(deterministic.unresolved_references),
        }
        if not required:
            return result.model_copy(
                update={
                    **base_update,
                    "date_semantic_fallback_reason": "deterministic_complete",
                }
            )
        if self._date_semantic_parser is None:
            return result.model_copy(
                update=_date_semantic_failure_update(
                    base_update,
                    deterministic_complete=deterministic_complete,
                    fallback_reason="parser_unavailable",
                )
            )
        try:
            parsed = await self._date_semantic_parser.parse_date_operations(
                route_input.latest_query,
                route_input.runtime_context,
                deterministic.operations,
            )
            resolution = _DATE_OPERATION_RESOLVER.resolve(
                route_input.latest_query,
                route_input.runtime_context,
                result.date_patch,
                proposed_operations=[*result.date_operations, *parsed.operations],
                proposed_unresolved_references=parsed.unresolved_references,
                preferred_constraint_operations=parsed.operations,
                allow_semantic_constraint_corrections=True,
            )
        except Exception as exc:
            telemetry = _date_semantic_telemetry(self._date_semantic_parser)
            return result.model_copy(
                update=_date_semantic_failure_update(
                    {
                    **base_update,
                    "date_semantic_llm_used": True,
                    "date_semantic_error": str(exc)[:300],
                    **_date_semantic_route_telemetry(telemetry),
                    },
                    deterministic_complete=deterministic_complete,
                    fallback_reason="semantic_parse_failed",
                )
            )
        telemetry = _date_semantic_telemetry(self._date_semantic_parser)
        semantic_complete = deterministic_date_parse_is_complete(
            route_input.latest_query,
            route_input.runtime_context,
            resolution,
        )
        if not semantic_complete:
            return result.model_copy(
                update=_date_semantic_failure_update(
                    {
                        **base_update,
                        "date_semantic_llm_used": True,
                        **_date_semantic_route_telemetry(telemetry),
                        "date_unresolved_references": list(
                            resolution.unresolved_references
                        ),
                    },
                    deterministic_complete=False,
                    fallback_reason="semantic_result_incomplete",
                )
            )
        return result.model_copy(
            update={
                **base_update,
                "date_semantic_llm_used": True,
                **_date_semantic_route_telemetry(telemetry),
                "date_patch": _apply_verified_semantic_constraints(
                    result.date_patch,
                    parsed.operations,
                    resolution.operations,
                ),
                "date_operations": list(resolution.operations),
                "date_operation_candidate_count": len(resolution.candidates),
                "date_operation_rejections": [
                    f"{item.operation.type.value}:{item.reason}" for item in resolution.rejected
                ],
                "date_unresolved_references": list(resolution.unresolved_references),
            }
        )

    def _finalize_route_metadata(
        self,
        route_input: RouteInput,
        result: RouteResult,
    ) -> RouteResult:
        result = _apply_date_plan_focus_guard(route_input, result)
        clarify, reason, options = should_clarify_route(
            route_input,
            result,
            clarification_threshold=self._clarification_threshold,
        )
        clarification_exhausted = (
            _clarification_repeat(
                route_input,
                result,
                clarification_threshold=self._clarification_threshold,
            )
            and not clarify
        )
        pending_task, pending_reason, pending_source, pending_turns, cancelled = (
            _resolve_pending_task(route_input, result)
        )
        date_authorized = (
            result.task_type == TaskType.DATE_PLANNING
            or TaskType.DATE_PLANNING in result.secondary_tasks
        )
        date_plan = result.date_plan if date_authorized else DatePlanSlots()
        date_patch = result.date_patch if date_authorized else None
        date_operations = result.date_operations if date_authorized else []
        date_operation_rejections = result.date_operation_rejections if date_authorized else []
        slot_accepted_fields = result.slot_accepted_fields if date_authorized else {}
        slot_rejected_fields = result.slot_rejected_fields if date_authorized else {}
        slot_field_sources = result.slot_field_sources if date_authorized else {}
        if date_authorized and not slot_accepted_fields and _date_slots_have_values(date_plan):
            rule_slots = extract_date_plan_slots(RouteInput(latest_query=route_input.latest_query))
            task_slots = (
                _date_slots_from_task_state(route_input.date_task_state)
                if route_input.date_task_state is not None
                and route_input.date_task_state.is_resumable
                else DatePlanSlots()
            )
            _, slot_accepted_fields, slot_field_sources = merge_route_slot_sources(
                rule_slots,
                DatePlanSlots(),
                task_slots,
            )
        return result.model_copy(
            update={
                "needs_clarification": clarify,
                "clarification_triggered": clarify,
                "clarification_exhausted": clarification_exhausted,
                "clarification_reason": reason,
                "clarification_options": options,
                "out_of_scope_reason": (
                    _out_of_scope_reason(result.normalized_query)
                    if result.task_type == TaskType.OUT_OF_SCOPE
                    else None
                ),
                "pending_task": pending_task,
                "pending_task_reason": pending_reason,
                "pending_task_source": pending_source,
                "pending_task_turns_remaining": pending_turns,
                "pending_task_cancelled": cancelled,
                "date_plan": date_plan,
                "date_patch": date_patch,
                "date_operations": date_operations,
                "date_operation_candidate_count": (
                    result.date_operation_candidate_count if date_authorized else 0
                ),
                "date_operation_rejections": date_operation_rejections,
                "date_unresolved_references": (
                    result.date_unresolved_references if date_authorized else []
                ),
                "router_llm_used": result.llm_used,
                "slot_accepted_fields": slot_accepted_fields,
                "slot_rejected_fields": slot_rejected_fields,
                "slot_field_sources": slot_field_sources,
            }
        )

    def _needs_task_correction(
        self,
        route_input: RouteInput,
        result: RouteResult,
    ) -> bool:
        if route_input.forced_task is not None:
            return False
        if _is_exact_casual_chat(result.normalized_query):
            return False
        if (
            route_input.date_task_state is None
            and route_input.active_task == TaskType.DATE_PLANNING
            and (
                _is_obvious_date_supplement(result.normalized_query)
                or _looks_like_date_edit_request(result.normalized_query)
            )
        ):
            return False
        ambiguous = (
            result.task_confidence < self._confidence_threshold
            or _top_margin(result.task_scores) < self._ambiguity_margin
        )
        if not ambiguous:
            return False
        # A short follow-up with history may need semantic context. A plain
        # relationship statement, however, can be handled by the rule path
        # without spending a Router request.
        return bool(route_input.recent_messages) or _looks_like_advice_request(
            result.normalized_query
        )

    def _needs_llm_correction(
        self,
        route_input: RouteInput,
        result: RouteResult,
    ) -> bool:
        if _is_exact_casual_chat(result.normalized_query):
            return False
        if _is_clear_out_of_scope(result):
            return False
        if (
            route_input.date_task_state is None
            and route_input.active_task == TaskType.DATE_PLANNING
            and (
                _is_obvious_date_supplement(result.normalized_query)
                or _looks_like_date_edit_request(result.normalized_query)
            )
        ):
            return False
        if route_input.date_task_state is not None and route_input.date_task_state.is_resumable:
            # A short answer such as "上海" must be classified against the
            # existing workflow as either a supplement or a task switch. A
            # structured slot-only answer remains a deterministic fast path.
            return not _is_obvious_date_supplement(result.normalized_query)
        if _looks_like_date_candidate(result.normalized_query, result):
            return True
        if self._needs_task_correction(route_input, result):
            return True
        if _is_context_dependent_follow_up(route_input, result):
            return True
        # A cross-business request needs an ordering decision, but multiple
        # relationship labels alone do not justify a model call.
        if _is_compound_task_request(result):
            return True
        if result.task_type != TaskType.RELATIONSHIP_ADVICE:
            return False
        return _needs_semantic_scenario_correction(result, self._ambiguity_margin)

    def _allow_task_override(
        self,
        route_input: RouteInput,
        rules: RouteResult,
        correction: RouteCorrection,
    ) -> bool:
        # A rule-recognized "first ... then ..." request already carries an
        # execution order. The corrector may enrich labels, but cannot reverse
        # which business task is handled first.
        if _has_explicit_compound_order(rules):
            return correction.task_type == rules.task_type
        if rules.task_type == TaskType.OUT_OF_SCOPE and _is_clear_out_of_scope(rules):
            return correction.task_type == TaskType.OUT_OF_SCOPE
        if correction.task_type == TaskType.OUT_OF_SCOPE:
            return rules.task_type in {
                TaskType.GENERAL_CHAT,
                TaskType.OUT_OF_SCOPE,
            } and not _has_explicit_business_request(rules.normalized_query)
        if correction.task_type == TaskType.DATE_PLANNING:
            correction_mode = _resolved_correction_date_mode(
                route_input,
                rules,
                correction,
            )
            if rules.date_request_mode in {
                DateRequestMode.EVALUATE,
                DateRequestMode.CATEGORY_RECOMMENDATION,
            }:
                return False
            if not _has_verified_date_task_signal(route_input, rules):
                return False
            if _is_executable_date_mode(correction_mode):
                return True
            if route_input.date_task_state is not None:
                return rules.task_type == TaskType.DATE_PLANNING
            return False
        if (
            route_input.date_task_state is not None
            and rules.task_type == TaskType.DATE_PLANNING
            and correction.task_type == TaskType.RELATIONSHIP_ADVICE
            and _looks_like_relationship_switch(rules.normalized_query)
        ):
            return True
        if _is_compound_task_request(rules) and correction.task_type != TaskType.GENERAL_CHAT:
            return True
        if not self._needs_task_correction(route_input, rules):
            return False
        if correction.task_type != TaskType.GENERAL_CHAT:
            return True
        if rules.task_type == TaskType.GENERAL_CHAT:
            return True
        # A detail correction must not erase a confident request for help.
        # Only a weak, non-question rule result may be downgraded to chat.
        return (
            rules.task_confidence < self._confidence_threshold
            and not _looks_like_advice_request(rules.normalized_query)
        )


def _apply_date_plan_focus_guard(
    route_input: RouteInput,
    result: RouteResult,
) -> RouteResult:
    active_task = route_input.active_task or (
        route_input.runtime_context.active_task if route_input.runtime_context is not None else None
    )
    date_plan_is_focused = (
        active_task == TaskType.DATE_PLANNING
        or result.task_type == TaskType.DATE_PLANNING
        or TaskType.DATE_PLANNING in result.secondary_tasks
    )
    if not date_plan_is_focused:
        return result
    has_explicit_operation = bool(result.date_operations) or _looks_like_date_edit_request(
        result.normalized_query
    ) or _is_executable_date_mode(result.date_request_mode)
    if (
        not has_explicit_operation
        or _looks_like_explicit_advice_request(result.normalized_query)
        or _has_ordered_primary_relationship_request(result.normalized_query)
    ):
        return result
    secondary = [task for task in result.secondary_tasks if task != TaskType.RELATIONSHIP_ADVICE]
    updates: dict[str, object] = {
        "secondary_tasks": secondary,
        "task_guard_applied": (result.task_guard_applied or secondary != result.secondary_tasks),
    }
    if result.task_type == TaskType.RELATIONSHIP_ADVICE and (
        result.date_patch is not None or _date_slots_have_values(result.date_plan)
    ):
        updates.update(
            {
                "task_type": TaskType.DATE_PLANNING,
                "primary_scenario": None,
                "secondary_scenarios": [],
                "primary_goal": None,
                "secondary_goals": [],
                "scenario_confidence": None,
                "task_guard_applied": True,
            }
        )
    return result.model_copy(update=updates)


def _looks_like_explicit_advice_request(text: str) -> bool:
    return bool(
        re.search(
            r"我该怎么|该怎么|怎么哄|怎么办|如何处理|给我.{0,4}建议|"
            r"帮我分析|你怎么看|为什么.{0,12}(?:生气|不高兴|冷淡)",
            text,
        )
    )


def normalize_route_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _corrector_telemetry(corrector: RouteCorrector | None) -> Mapping[str, object]:
    telemetry = getattr(corrector, "last_telemetry", {})
    return telemetry if isinstance(telemetry, Mapping) else {}


def _date_semantic_telemetry(parser: DateSemanticParser | None) -> Mapping[str, object]:
    profile = getattr(parser, "semantic_profile", {})
    telemetry = getattr(parser, "last_telemetry", {})
    values: dict[str, object] = {}
    if isinstance(profile, Mapping):
        values.update(profile)
    if isinstance(telemetry, Mapping):
        values.update(telemetry)
    return values


def _date_semantic_route_telemetry(telemetry: Mapping[str, object]) -> dict[str, object]:
    return {
        "date_semantic_model": telemetry.get("model"),
        "date_semantic_thinking": telemetry.get("thinking"),
        "date_semantic_prompt_version": telemetry.get("prompt_version"),
        "date_semantic_input_tokens": telemetry.get("input_tokens"),
        "date_semantic_output_tokens": telemetry.get("output_tokens"),
        "date_semantic_duration_ms": telemetry.get("duration_ms"),
    }


def _date_semantic_failure_update(
    base_update: dict[str, object],
    *,
    deterministic_complete: bool,
    fallback_reason: str,
) -> dict[str, object]:
    update = {
        **base_update,
        "date_semantic_fallback_reason": fallback_reason,
    }
    if deterministic_complete:
        return update
    unresolved = list(base_update.get("date_unresolved_references") or [])
    if not unresolved:
        unresolved = ["请明确本轮要修改的约会条件或行程节点"]
    return {
        **update,
        "date_operations": [],
        "date_unresolved_references": unresolved,
    }


def _apply_verified_semantic_constraints(
    patch: DatePlanPatch,
    semantic_operations: list[DatePlanOperation],
    accepted_operations: tuple[DatePlanOperation, ...],
) -> DatePlanPatch:
    accepted = list(accepted_operations)
    updates: dict[str, object] = {}
    sources = dict(patch.source_by_field)
    for operation in semantic_operations:
        if (
            operation not in accepted
            or operation.type != DateOperationType.UPDATE_CONSTRAINT
            or operation.constraint_field is None
        ):
            continue
        field = operation.constraint_field.value
        updates[field] = operation.constraint_value
        sources[field] = SlotSource.LLM_VERIFIED
    if not updates:
        return patch
    return DatePlanPatch.model_validate(
        {
            **patch.model_dump(exclude={"source_by_field"}),
            **updates,
            "source_by_field": sources,
        }
    )


def route_by_rules(route_input: RouteInput, normalized_query: str | None = None) -> RouteResult:
    text = normalized_query or normalize_route_text(route_input.latest_query)
    latest_date_slots = extract_date_plan_slots(RouteInput(latest_query=route_input.latest_query))
    date_slots = (
        _merge_date_slots(
            _date_slots_from_task_state(route_input.date_task_state),
            latest_date_slots,
        )
        if route_input.date_task_state is not None and route_input.date_task_state.is_resumable
        else latest_date_slots
    )
    task_scores, task_evidence = _score_labels(text, _TASK_PATTERNS)
    _apply_regex_scores(text, task_scores, task_evidence, _TASK_REGEX_PATTERNS)
    if _has_general_relationship_request(text):
        task_scores[TaskType.RELATIONSHIP_ADVICE] = max(
            task_scores.get(TaskType.RELATIONSHIP_ADVICE, 0),
            4,
        )
        task_evidence.setdefault(TaskType.RELATIONSHIP_ADVICE, []).append("明确关系咨询请求")
    if (
        _has_relationship_semantic_request(text)
        and task_scores.get(TaskType.RELATIONSHIP_ADVICE, 0) >= 4
    ):
        # A technical noun can be part of a real relationship problem. Do not
        # let "代码" alone outrank an explicit conflict or communication request.
        task_scores.pop(TaskType.OUT_OF_SCOPE, None)
        task_evidence.pop(TaskType.OUT_OF_SCOPE, None)
    date_request_mode = _infer_date_request_mode(route_input, text, latest_date_slots)
    implicit_date_bundle = _looks_like_implicit_date_plan_bundle(text, latest_date_slots)
    executable_date_request = _is_executable_date_mode(date_request_mode)
    if (
        executable_date_request
        and not (
            route_input.date_task_state is not None
            and route_input.date_task_state.is_resumable
        )
        and route_input.recent_messages
    ):
        date_slots = _recover_date_activation_history_slots(route_input)

    # Broad date/place phrases are only semantic candidates. A stateful date
    # workflow requires an executable request mode.
    if not executable_date_request:
        task_scores.pop(TaskType.DATE_PLANNING, None)
        task_evidence.pop(TaskType.DATE_PLANNING, None)
    if _has_reported_date_planning_context(text) and not executable_date_request:
        task_scores.pop(TaskType.DATE_PLANNING, None)
        task_evidence.pop(TaskType.DATE_PLANNING, None)
    if _is_cancelled_date_request(text):
        task_scores.pop(TaskType.DATE_PLANNING, None)
        task_evidence.pop(TaskType.DATE_PLANNING, None)

    if _is_exact_casual_chat(text):
        task_scores.pop(TaskType.OUT_OF_SCOPE, None)
        task_evidence.pop(TaskType.OUT_OF_SCOPE, None)
        task_scores[TaskType.GENERAL_CHAT] = task_scores.get(TaskType.GENERAL_CHAT, 0) + 8
        task_evidence.setdefault(TaskType.GENERAL_CHAT, []).append(route_input.latest_query)

    relationship_follow_up = _is_relationship_clarification_answer(route_input, text)
    historical_relationship_report = _looks_like_historical_relationship_report(text)
    if relationship_follow_up or historical_relationship_report:
        task_scores.pop(TaskType.DATE_PLANNING, None)
        task_evidence.pop(TaskType.DATE_PLANNING, None)
        task_scores[TaskType.RELATIONSHIP_ADVICE] = (
            task_scores.get(TaskType.RELATIONSHIP_ADVICE, 0) + 5
        )
        task_evidence.setdefault(TaskType.RELATIONSHIP_ADVICE, []).append(
            "关系问题澄清回答" if relationship_follow_up else "关系历史事实"
        )
    elif date_request_mode in {
        DateRequestMode.EVALUATE,
        DateRequestMode.CATEGORY_RECOMMENDATION,
    }:
        task_scores.pop(TaskType.DATE_PLANNING, None)
        task_evidence.pop(TaskType.DATE_PLANNING, None)
        task_scores[TaskType.RELATIONSHIP_ADVICE] = (
            task_scores.get(TaskType.RELATIONSHIP_ADVICE, 0) + 5
        )
        mode_evidence = (
            "约会行动评估" if date_request_mode == DateRequestMode.EVALUATE else "约会类别建议"
        )
        task_evidence.setdefault(TaskType.RELATIONSHIP_ADVICE, []).append(mode_evidence)
    elif date_request_mode == DateRequestMode.MODIFY or _should_score_date_follow_up(
        route_input,
        text,
        latest_date_slots,
    ):
        task_scores[TaskType.DATE_PLANNING] = task_scores.get(TaskType.DATE_PLANNING, 0) + 5
        task_evidence.setdefault(TaskType.DATE_PLANNING, []).append("约会任务状态补充")
    elif executable_date_request:
        # Explicit planning phrases are real task evidence. If planning is
        # explicitly ordered after a relationship question, keep it as a
        # secondary candidate for the compound-task resolver instead of
        # letting it steal the primary task.
        if _has_ordered_primary_relationship_request(text):
            task_scores[TaskType.DATE_PLANNING] = task_scores.get(TaskType.DATE_PLANNING, 0) + 1.5
            task_evidence.setdefault(TaskType.DATE_PLANNING, []).append("复合请求中的后续约会规划")
        else:
            bonus = 5 if implicit_date_bundle else 4
            task_scores[TaskType.DATE_PLANNING] = (
                task_scores.get(TaskType.DATE_PLANNING, 0) + bonus
            )
            task_evidence.setdefault(TaskType.DATE_PLANNING, []).append(
                "结构化约会条件组合" if implicit_date_bundle else "明确约会规划请求"
            )
    elif _looks_like_date_candidate(text, None):
        # This is only a semantic-review candidate. Do not put it into the
        # task score, otherwise a weak candidate can become the primary task.
        pass
    if _has_ordered_primary_relationship_request(text):
        relationship_score = task_scores.get(TaskType.RELATIONSHIP_ADVICE, 0)
        date_score = task_scores.get(TaskType.DATE_PLANNING, 0)
        if relationship_score and date_score:
            task_scores[TaskType.DATE_PLANNING] = min(
                date_score,
                relationship_score * 0.7,
            )
    explicit_score = max(task_scores.values(), default=0)
    if (
        route_input.active_task is not None
        and not _is_exact_casual_chat(text)
        and explicit_score < 4
    ):
        task_scores[route_input.active_task] = task_scores.get(route_input.active_task, 0) + 2.5

    if route_input.forced_task is not None:
        task_scores[route_input.forced_task] = max(
            task_scores.get(route_input.forced_task, 0),
            20,
        )
    if not task_scores:
        task_scores[TaskType.GENERAL_CHAT] = 1

    task_type, secondary_tasks = _primary_and_secondary(
        task_scores,
        minimum_secondary_score=2.5,
        relative_secondary_score=0.5,
    )
    if (
        route_input.forced_task is None
        and executable_date_request
        and _has_ordered_primary_relationship_request(text)
        and TaskType.RELATIONSHIP_ADVICE in task_scores
        and TaskType.DATE_PLANNING in task_scores
    ):
        # A direct "first assess the relationship, then plan the date"
        # instruction is stronger than score magnitude. Keep the later task
        # available for the pending-task workflow even when a long first
        # clause makes its score fall below the generic secondary threshold.
        task_type = TaskType.RELATIONSHIP_ADVICE
        secondary_tasks = _without_primary(
            [TaskType.DATE_PLANNING, *secondary_tasks],
            task_type,
        )[:2]
    task_confidence = 1.0 if route_input.forced_task else _score_confidence(task_scores, 7)

    scenario_scores: dict[AdviceScenario, float] = {}
    scenario_evidence: dict[AdviceScenario, list[str]] = {}
    goal_scores: dict[AdviceGoal, float] = {}
    goal_evidence: dict[AdviceGoal, list[str]] = {}
    primary_scenario: AdviceScenario | None = None
    secondary_scenarios: list[AdviceScenario] = []
    primary_goal: AdviceGoal | None = None
    secondary_goals: list[AdviceGoal] = []
    scenario_confidence: float | None = None

    if task_type == TaskType.RELATIONSHIP_ADVICE or (
        TaskType.RELATIONSHIP_ADVICE in secondary_tasks
    ):
        scenario_scores, scenario_evidence = _score_labels(
            text,
            _SCENARIO_PATTERNS,
            suppress_negated=True,
        )
        _apply_regex_scores(
            text,
            scenario_scores,
            scenario_evidence,
            _SCENARIO_REGEX_PATTERNS,
        )
        _apply_contextual_scenario_scores(
            route_input,
            text,
            scenario_scores,
            scenario_evidence,
        )
        if date_request_mode == DateRequestMode.EVALUATE:
            scenario_scores[AdviceScenario.PURSUIT] = (
                scenario_scores.get(AdviceScenario.PURSUIT, 0) + 2.5
            )
            scenario_evidence.setdefault(AdviceScenario.PURSUIT, []).append("评估低压力推进动作")
        if not scenario_scores:
            scenario_scores[AdviceScenario.RELATIONSHIP_MAINTENANCE] = 1
        primary_scenario, secondary_scenarios = _primary_and_secondary(
            scenario_scores,
            minimum_secondary_score=2,
            relative_secondary_score=0.3,
        )
        scenario_confidence = _score_confidence(scenario_scores, 7)

        goal_scores, goal_evidence = _score_labels(
            text,
            _GOAL_PATTERNS,
            suppress_negated=True,
        )
        _apply_regex_scores(
            text,
            goal_scores,
            goal_evidence,
            _GOAL_REGEX_PATTERNS,
        )
        _apply_contextual_goal_score(
            primary_scenario,
            scenario_evidence,
            goal_scores,
            goal_evidence,
        )
        if date_request_mode == DateRequestMode.EVALUATE:
            goal_scores[AdviceGoal.PROGRESS] = goal_scores.get(AdviceGoal.PROGRESS, 0) + 2.5
            goal_evidence.setdefault(AdviceGoal.PROGRESS, []).append("评估下一步互动")
        if relationship_follow_up:
            strongest_other = max(
                (score for goal, score in goal_scores.items() if goal != AdviceGoal.UNDERSTAND),
                default=0,
            )
            goal_scores[AdviceGoal.UNDERSTAND] = max(
                goal_scores.get(AdviceGoal.UNDERSTAND, 0) + 3,
                strongest_other + 1,
            )
            goal_evidence.setdefault(AdviceGoal.UNDERSTAND, []).append("回答关系判断追问")
        _apply_default_goal(text, primary_scenario, scenario_scores, goal_scores, goal_evidence)
        if goal_scores:
            primary_goal, secondary_goals = _primary_and_secondary(
                goal_scores,
                minimum_secondary_score=2,
                relative_secondary_score=0.35,
            )

    evidence = _unique(
        [
            *task_evidence.get(task_type, []),
            *(scenario_evidence.get(primary_scenario, []) if primary_scenario else []),
            *(goal_evidence.get(primary_goal, []) if primary_goal else []),
        ]
    )[:12]
    date_intent = _infer_date_intent(route_input, text, task_type, latest_date_slots)
    date_mutation = _infer_date_mutation(route_input, text, task_type, date_intent)
    date_plan = (
        date_slots
        if task_type == TaskType.DATE_PLANNING or TaskType.DATE_PLANNING in secondary_tasks
        else DatePlanSlots()
    )
    date_patch = (
        _date_plan_patch_from_slots(latest_date_slots)
        if task_type == TaskType.DATE_PLANNING or TaskType.DATE_PLANNING in secondary_tasks
        else None
    )
    operation_resolution = (
        _DATE_OPERATION_RESOLVER.resolve(
            route_input.latest_query,
            route_input.runtime_context,
            date_patch,
        )
        if date_patch is not None
        else None
    )
    return RouteResult(
        normalized_query=text,
        task_type=task_type,
        secondary_tasks=secondary_tasks,
        task_confidence=task_confidence,
        task_scores=_rounded_scores(task_scores),
        rule_task_type=task_type,
        primary_goal=primary_goal,
        secondary_goals=secondary_goals,
        goal_scores=_rounded_scores(goal_scores),
        primary_scenario=primary_scenario,
        secondary_scenarios=secondary_scenarios,
        scenario_confidence=scenario_confidence,
        scenario_scores=_rounded_scores(scenario_scores),
        date_plan=date_plan,
        date_patch=date_patch,
        date_request_mode=date_request_mode,
        date_intent=date_intent,
        date_mutation=date_mutation,
        date_operations=(
            list(operation_resolution.operations) if operation_resolution is not None else []
        ),
        date_operation_candidate_count=(
            len(operation_resolution.candidates) if operation_resolution is not None else 0
        ),
        date_operation_rejections=(
            [f"{item.operation.type.value}:{item.reason}" for item in operation_resolution.rejected]
            if operation_resolution is not None
            else []
        ),
        date_unresolved_references=(
            list(operation_resolution.unresolved_references)
            if operation_resolution is not None
            else []
        ),
        date_missing_fields=(
            _date_missing_fields(date_slots) if task_type == TaskType.DATE_PLANNING else []
        ),
        source=RouteSource.RULES,
        needs_clarification=_rule_needs_clarification(
            route_input,
            text,
            task_type,
            task_confidence,
            secondary_tasks,
        ),
        evidence_spans=evidence,
    )


def _has_verified_date_task_signal(
    route_input: RouteInput,
    rules: RouteResult,
) -> bool:
    """Require local date semantics before accepting an LLM task proposal."""

    if (
        route_input.forced_task == TaskType.DATE_PLANNING
        or rules.task_type == TaskType.DATE_PLANNING
        or TaskType.DATE_PLANNING in rules.secondary_tasks
        or _is_executable_date_mode(rules.date_request_mode)
    ):
        return True
    latest_slots = extract_date_plan_slots(RouteInput(latest_query=route_input.latest_query))
    inferred_mode = _infer_date_request_mode(route_input, rules.normalized_query, latest_slots)
    return _is_executable_date_mode(inferred_mode) or _has_local_date_execution_authorization(
        rules.normalized_query
    )


def _resolved_correction_date_mode(
    route_input: RouteInput,
    rules: RouteResult,
    correction: RouteCorrection,
) -> DateRequestMode:
    if correction.date_request_mode != DateRequestMode.NONE:
        return correction.date_request_mode
    if rules.date_request_mode != DateRequestMode.NONE:
        return rules.date_request_mode
    inferred = _infer_date_request_mode(
        route_input,
        rules.normalized_query,
        extract_date_plan_slots(RouteInput(latest_query=route_input.latest_query)),
    )
    if inferred != DateRequestMode.NONE:
        return inferred
    if (
        correction.task_type == TaskType.DATE_PLANNING
        and correction.date_intent == DateTaskIntent.NEW_REQUEST
        and _looks_like_date_candidate(rules.normalized_query, None)
    ):
        return (
            DateRequestMode.PLACE_SEARCH
            if _looks_like_place_search_request(_last_request_clause(rules.normalized_query))
            else DateRequestMode.ITINERARY
        )
    if (
        correction.task_type == TaskType.DATE_PLANNING
        and route_input.date_task_state is not None
        and correction.date_intent
        in {DateTaskIntent.SUPPLEMENT, DateTaskIntent.CONTINUE, DateTaskIntent.CANCEL}
    ):
        return DateRequestMode.MODIFY
    return DateRequestMode.NONE


def merge_route_correction(
    route_input: RouteInput,
    rules: RouteResult,
    correction: RouteCorrection,
    *,
    allow_task_override: bool = True,
    validated_date_plan: DatePlanSlots | None = None,
    slot_validation: SlotValidationResult | None = None,
    rule_date_plan: DatePlanSlots | None = None,
    task_date_plan: DatePlanSlots | None = None,
    current_turn_date_plan: DatePlanSlots | None = None,
    current_turn_field_sources: dict[str, str] | None = None,
) -> RouteResult:
    date_request_mode = _resolved_correction_date_mode(
        route_input,
        rules,
        correction,
    )
    task_type = route_input.forced_task or (
        correction.task_type if allow_task_override else rules.task_type
    )
    if task_type == TaskType.DATE_PLANNING and not _is_executable_date_mode(date_request_mode):
        date_request_mode = (
            DateRequestMode.MODIFY
            if route_input.date_task_state is not None
            else DateRequestMode.ITINERARY
        )
    elif task_type != TaskType.DATE_PLANNING and _is_executable_date_mode(date_request_mode):
        date_request_mode = (
            rules.date_request_mode
            if rules.date_request_mode
            in {DateRequestMode.EVALUATE, DateRequestMode.CATEGORY_RECOMMENDATION}
            else DateRequestMode.NONE
        )
    correction_secondary_tasks = [
        task
        for task in correction.secondary_tasks
        if task != TaskType.DATE_PLANNING or _has_verified_date_task_signal(route_input, rules)
    ]
    secondary_task_guard_applied = len(correction_secondary_tasks) != len(
        correction.secondary_tasks
    )
    secondary_tasks = _without_primary(
        [*correction_secondary_tasks, *rules.secondary_tasks],
        task_type,
    )[:2]
    if (
        validated_date_plan is not None
        and rule_date_plan is not None
        and task_date_plan is not None
    ):
        date_plan, slot_accepted_fields, slot_field_sources = merge_route_slot_sources(
            rule_date_plan,
            validated_date_plan,
            task_date_plan,
        )
    else:
        # Kept for direct callers of this public helper. HybridRouter always
        # supplies a field-validated correction before reaching this branch.
        date_plan = _merge_date_slots(rules.date_plan, correction.date_plan)
        slot_accepted_fields = {}
        slot_field_sources = {}
    if current_turn_date_plan is None:
        rule_patch_slots = _date_slots_from_patch(rules.date_patch)
        correction_patch_slots = _date_slots_from_correction(correction)
        current_turn_date_plan, _, current_turn_field_sources = merge_current_turn_slot_sources(
            rule_patch_slots,
            correction_patch_slots,
        )
    date_patch = _date_plan_patch_from_slots(
        current_turn_date_plan,
        current_turn_field_sources,
    )
    date_authorized = (
        task_type == TaskType.DATE_PLANNING or TaskType.DATE_PLANNING in secondary_tasks
    )
    if not date_authorized:
        date_plan = DatePlanSlots()
        date_patch = None
    operation_resolution = (
        _DATE_OPERATION_RESOLVER.resolve(
            route_input.latest_query,
            route_input.runtime_context,
            date_patch,
            proposed_operations=correction.date_operations,
        )
        if date_patch is not None
        else None
    )
    date_intent = correction.date_intent if allow_task_override else rules.date_intent
    if date_intent == DateTaskIntent.NONE:
        date_intent = rules.date_intent
    if date_intent == DateTaskIntent.NONE and task_type == TaskType.DATE_PLANNING:
        date_intent = (
            DateTaskIntent.SUPPLEMENT
            if route_input.date_task_state is not None
            else DateTaskIntent.NEW_REQUEST
        )
    date_mutation = correction.date_mutation
    if rules.date_mutation in {
        DatePlanMutation.REPLACE,
        DatePlanMutation.REMOVE,
        DatePlanMutation.REORDER,
        DatePlanMutation.REPLAN,
    }:
        date_mutation = rules.date_mutation
    if date_mutation == DatePlanMutation.NONE:
        date_mutation = _infer_date_mutation(
            route_input,
            rules.normalized_query,
            task_type,
            date_intent,
        )
    if (
        date_mutation in {DatePlanMutation.NONE, DatePlanMutation.UPDATE_CONSTRAINT}
        and task_type == TaskType.DATE_PLANNING
        and date_intent == DateTaskIntent.SUPPLEMENT
        and route_input.date_task_state is not None
        and _has_unplanned_date_nodes(route_input.date_task_state, date_plan)
    ):
        date_mutation = DatePlanMutation.ADD

    if task_type == TaskType.RELATIONSHIP_ADVICE:
        primary_scenario = correction.primary_scenario or rules.primary_scenario
        secondary_scenarios = _without_primary(
            [*correction.secondary_scenarios, *rules.secondary_scenarios],
            primary_scenario,
        )[:2]
        if primary_scenario is None:
            primary_scenario = AdviceScenario.RELATIONSHIP_MAINTENANCE
        primary_goal = correction.primary_goal or rules.primary_goal
        secondary_goals = _without_primary(
            [*correction.secondary_goals, *rules.secondary_goals],
            primary_goal,
        )[:2]
        scenario_confidence = correction.scenario_confidence or rules.scenario_confidence
    else:
        primary_scenario = None
        secondary_scenarios = []
        primary_goal = None
        secondary_goals = []
        scenario_confidence = None

    return rules.model_copy(
        update={
            "task_type": task_type,
            "secondary_tasks": secondary_tasks,
            "task_confidence": (
                correction.task_confidence if allow_task_override else rules.task_confidence
            ),
            "llm_task_type": correction.task_type,
            "task_guard_applied": (
                correction.task_type != task_type or secondary_task_guard_applied
            ),
            "primary_goal": primary_goal,
            "secondary_goals": secondary_goals,
            "primary_scenario": primary_scenario,
            "secondary_scenarios": secondary_scenarios,
            "scenario_confidence": scenario_confidence,
            "date_plan": date_plan,
            "date_patch": date_patch,
            "date_request_mode": date_request_mode,
            "date_intent": date_intent,
            "date_mutation": date_mutation,
            "date_operations": (
                list(operation_resolution.operations) if operation_resolution is not None else []
            ),
            "date_operation_candidate_count": (
                len(operation_resolution.candidates) if operation_resolution is not None else 0
            ),
            "date_operation_rejections": (
                [
                    f"{item.operation.type.value}:{item.reason}"
                    for item in operation_resolution.rejected
                ]
                if operation_resolution is not None
                else []
            ),
            "date_unresolved_references": (
                list(operation_resolution.unresolved_references)
                if operation_resolution is not None
                else []
            ),
            "date_missing_fields": (
                _date_missing_fields(date_plan) if task_type == TaskType.DATE_PLANNING else []
            ),
            "source": RouteSource.HYBRID,
            "llm_used": True,
            "needs_clarification": (
                correction.needs_clarification
                if allow_task_override
                else rules.needs_clarification or correction.needs_clarification
            ),
            "evidence_spans": _unique([*correction.evidence_spans, *rules.evidence_spans])[:12],
            "slot_accepted_fields": slot_accepted_fields,
            "slot_rejected_fields": (
                slot_validation.rejected_fields if slot_validation is not None else {}
            ),
            "slot_field_sources": slot_field_sources,
        }
    )


def extract_date_plan_slots(route_input: RouteInput) -> DatePlanSlots:
    user_texts = [
        message.content
        for message in route_input.recent_messages
        if message.role == MessageRole.USER
    ]
    texts = [route_input.latest_query, *reversed(user_texts)]
    normalized_texts = [normalize_route_text(text) for text in texts]
    combined = " ".join(normalized_texts)
    facts = _DATE_FACT_PARSER.parse(combined, route_input.runtime_context)
    current_facts = _DATE_FACT_PARSER.parse(normalized_texts[0], route_input.runtime_context)
    replace_place_names = _extract_replace_place_names(normalized_texts[0])
    place_search_text = normalized_texts[0]
    for place_name in replace_place_names:
        place_search_text = place_search_text.replace(place_name, " ")
    dining_keywords, activity_keywords, excluded_keywords = _extract_place_keywords(
        place_search_text
    )
    meal_keywords, schedule_hints = _extract_schedule_slots(normalized_texts[0])

    city = facts.city
    area = facts.area

    preferences = [value for value in _DATE_PREFERENCES if value in combined]
    transport_mode = facts.transport_mode
    planned_date = facts.date
    end_date = facts.end_date
    day_count = facts.day_count
    nights = facts.nights
    plan_mode = facts.plan_mode
    target_day = current_facts.target_day
    lodging_notes = _extract_lodging_notes(normalized_texts[0])
    start_time = facts.start_time
    notes, constraints = _extract_date_notes(combined)
    requested_days = extract_requested_day_count(
        re.sub(
            r"第\s*(?:[0-9]{1,2}|[一二三四五六七八九十两]{1,3})\s*天",
            "",
            combined,
        )
    )
    if requested_days is not None and requested_days > MAX_TRIP_DAYS:
        constraints.append(
            f"用户原始请求为 {requested_days} 天；当前版本最多生成前 {MAX_TRIP_DAYS} 天。"
        )
    return DatePlanSlots(
        city=city,
        area=area,
        plan_mode=plan_mode,
        date=planned_date,
        end_date=end_date,
        day_count=day_count,
        nights=nights,
        target_day=target_day,
        start_time=start_time,
        budget=facts.budget,
        budget_scope=facts.budget_scope,
        preferences=preferences,
        dining_keywords=dining_keywords,
        meal_keywords=meal_keywords,
        activity_keywords=activity_keywords,
        schedule_hints=schedule_hints,
        replace_place_names=replace_place_names,
        excluded_keywords=excluded_keywords,
        transport_mode=transport_mode,
        notes=notes,
        constraints=constraints,
        lodging_notes=lodging_notes,
    )


def _recover_date_activation_history_slots(route_input: RouteInput) -> DatePlanSlots:
    relevant_messages = [
        message
        for message in route_input.recent_messages
        if message.role == MessageRole.USER
        and _looks_like_date_activation_context(message.content)
    ][-6:]
    return extract_date_plan_slots(
        route_input.model_copy(update={"recent_messages": relevant_messages})
    )


def _looks_like_date_activation_context(text: str) -> bool:
    normalized = normalize_route_text(text)
    return (
        re.search(
            r"(?:想|打算|准备|计划|希望).{0,8}(?:去|在|安排)|"
            r"(?:约会|行程|日程).{0,12}(?:地点|城市|区域|商圈|在|去)|"
            r"(?:地点|城市|区域|商圈).{0,8}(?:定在|选在|想去|考虑)",
            normalized,
        )
        is not None
    )


def _date_slots_from_correction(correction: RouteCorrection) -> DatePlanSlots:
    if correction.date_patch is None:
        return correction.date_plan
    return _date_slots_from_patch(correction.date_patch)


def _date_slots_from_patch(patch: DatePlanPatch | None) -> DatePlanSlots:
    if patch is None:
        return DatePlanSlots()
    return DatePlanSlots.model_validate(patch.model_dump(exclude={"source_by_field"}))


def _date_plan_patch_from_slots(
    slots: DatePlanSlots,
    field_sources: dict[str, str] | None = None,
) -> DatePlanPatch:
    sources = field_sources or {
        name: SlotSource.RULE.value
        for name, value in slots.model_dump().items()
        if value is not None and value != [] and value != {}
    }
    source_by_field = {
        name: _slot_source(source)
        for name, source in sources.items()
        if name in DatePlanPatch.model_fields and _slot_source(source) is not None
    }
    return DatePlanPatch.model_validate(
        {
            **slots.model_dump(),
            "source_by_field": source_by_field,
        }
    )


def _slot_source(value: str) -> SlotSource | None:
    if "rule" in value:
        return SlotSource.RULE
    if "llm_verified" in value:
        return SlotSource.LLM_VERIFIED
    return None


def _date_slots_from_task_state(state: DatePlanningTaskState) -> DatePlanSlots:
    # Existing task collections are merged later by the date workflow.  At
    # routing time inherit only scalar context; list fields must remain the
    # latest-turn delta so replace/remove mutations cannot re-add old nodes.
    return DatePlanSlots(
        city=state.city,
        area=state.area,
        plan_mode=state.plan_mode,
        date=state.date,
        end_date=state.end_date,
        day_count=state.day_count,
        nights=state.nights,
        target_day=state.target_day,
        start_time=state.start_time,
        budget=state.budget,
        budget_scope=state.budget_scope,
        transport_mode=state.transport_mode,
        lodging_notes=state.lodging_notes,
    )


def _score_labels[LabelT](
    text: str,
    patterns: dict[LabelT, tuple[tuple[str, float], ...]],
    *,
    suppress_negated: bool = False,
) -> tuple[dict[LabelT, float], dict[LabelT, list[str]]]:
    scores: dict[LabelT, float] = {}
    evidence: dict[LabelT, list[str]] = {}
    for label, entries in patterns.items():
        for phrase, weight in entries:
            start = text.find(phrase)
            if start < 0 or (suppress_negated and _is_negated(text, start)):
                continue
            scores[label] = scores.get(label, 0) + weight
            evidence.setdefault(label, []).append(phrase)
    return scores, evidence


def _is_negated(text: str, start: int) -> bool:
    prefix = text[max(0, start - 4) : start]
    return any(prefix.endswith(value) for value in ("没有", "没", "不是", "并未", "不曾"))


def _is_exact_casual_chat(text: str) -> bool:
    cleaned = re.sub(r"[,.!?，。！？~～、 ]", "", text)
    return cleaned in _CASUAL_EXACT


def should_clarify_route(
    route_input: RouteInput,
    route: RouteResult,
    *,
    clarification_threshold: float = 0.68,
) -> tuple[bool, str | None, list[str]]:
    """Decide clarification after safety, rule routing, and optional correction."""

    text = route.normalized_query
    if (
        route.risk_level != RiskLevel.NORMAL
        or _is_exact_casual_chat(text)
        or route.task_type == TaskType.OUT_OF_SCOPE
        or is_pending_cancellation(text, route_input.pending_task)
    ):
        return False, None, []
    if route.date_unresolved_references:
        return (
            True,
            "unresolved_date_plan_reference",
            route.date_unresolved_references[:3],
        )
    if route_input.forced_task is not None:
        return False, None, []
    if (
        route_input.date_task_state is not None
        and route_input.date_task_state.is_resumable
        and route.task_type == TaskType.DATE_PLANNING
    ):
        return False, None, []
    if route.secondary_tasks and _is_compound_task_request(route):
        return False, None, []
    active_task_is_reliable = (
        route_input.active_task in {TaskType.RELATIONSHIP_ADVICE, TaskType.DATE_PLANNING}
        and route_input.active_task == route.task_type
        and bool(route_input.recent_messages)
        and route.task_confidence >= 0.55
        and _active_task_has_context(route_input, route)
    )
    if active_task_is_reliable:
        return False, None, []
    is_ambiguous = _looks_underspecified_route(text) or (
        route.needs_clarification and route.task_confidence < clarification_threshold
    )
    if not is_ambiguous:
        return False, None, []
    if _clarification_repeat(
        route_input,
        route,
        clarification_threshold=clarification_threshold,
    ):
        return False, route_input.last_clarification_reason, []
    reason = "ambiguous_cross_domain_intent"
    options = _clarification_options(route_input, route)
    return True, reason, options


def _clarification_repeat(
    route_input: RouteInput,
    route: RouteResult,
    *,
    clarification_threshold: float = 0.68,
) -> bool:
    if not route_input.last_clarification_reason or route_input.clarification_attempt_count <= 0:
        return False
    if (
        route.risk_level in {RiskLevel.HIGH, RiskLevel.SENSITIVE}
        or route_input.forced_task is not None
        or _is_exact_casual_chat(route.normalized_query)
        or route.task_type == TaskType.OUT_OF_SCOPE
    ):
        return False
    return _looks_underspecified_route(route.normalized_query) or (
        route.needs_clarification and route.task_confidence < clarification_threshold
    )


def _active_task_has_context(route_input: RouteInput, route: RouteResult) -> bool:
    """Require domain evidence before inheriting a possibly stale active task."""

    if not _looks_underspecified_route(route.normalized_query):
        return True
    context = " ".join(message.content for message in route_input.recent_messages[-6:])
    if route_input.active_task == TaskType.RELATIONSHIP_ADVICE:
        return any(
            marker in context
            for marker in ("她", "他", "对方", "关系", "表白", "聊天", "吵架", "分手")
        )
    if route_input.active_task == TaskType.DATE_PLANNING:
        return (
            route_input.date_task_state is not None and route_input.date_task_state.is_resumable
        ) or any(marker in context for marker in ("约会", "行程", "城市", "预算", "地点", "餐厅"))
    return False


def _rule_needs_clarification(
    route_input: RouteInput,
    text: str,
    task_type: TaskType,
    task_confidence: float,
    secondary_tasks: list[TaskType],
) -> bool:
    if (
        route_input.forced_task is not None
        or _is_exact_casual_chat(text)
        or task_type == TaskType.OUT_OF_SCOPE
        or secondary_tasks
    ):
        return False
    if (
        route_input.date_task_state is not None
        and route_input.date_task_state.is_resumable
        and task_type == TaskType.DATE_PLANNING
    ):
        return False
    return _looks_underspecified_route(text) or task_confidence < 0.58


def _looks_underspecified_route(text: str) -> bool:
    compact = re.sub(r"[，。！？!?、~～ ]", "", text)
    if compact in {
        "这样行吗",
        "这样可以吗",
        "这样好吗",
        "你觉得这样行吗",
        "你觉得这样可以吗",
        "你觉得这样好吗",
        "这个怎么办",
        "怎么办",
        "你觉得呢",
        "就是这样",
        "就是这样啊",
        "就这样",
    }:
        return True
    return bool(
        re.fullmatch(r"(?:你觉得|觉得|这样|这个|那)(?:怎么样|行吗|可以吗|好吗|怎么办)?", compact)
    )


def _clarification_options(route_input: RouteInput, route: RouteResult) -> list[str]:
    if route_input.date_task_state is not None and route_input.date_task_state.is_resumable:
        return ["补充上一版约会计划", "重新开始一份约会计划"]
    candidates = {
        route.task_type,
        *route.secondary_tasks,
        *(task for task, score in route.task_scores.items() if score >= 1.5),
    }
    if {
        TaskType.RELATIONSHIP_ADVICE,
        TaskType.DATE_PLANNING,
    } <= candidates or not candidates - {TaskType.GENERAL_CHAT}:
        return ["分析这段关系", "安排一次约会"]
    if TaskType.DATE_PLANNING in candidates:
        return ["安排约会", "继续聊关系问题"]
    return ["分析关系问题", "说明你希望我帮你完成的事"]


def _resolve_pending_task(
    route_input: RouteInput,
    route: RouteResult,
) -> tuple[TaskType | None, str | None, str | None, int, bool]:
    if route.risk_level in {RiskLevel.HIGH, RiskLevel.SENSITIVE}:
        return None, None, None, 0, False
    if is_pending_cancellation(route.normalized_query, route_input.pending_task):
        return None, None, None, 0, True
    if (
        route_input.pending_task is not None
        and route_input.forced_task == route_input.pending_task
        and route.task_type == route_input.pending_task
    ):
        return None, None, None, 0, False
    if (
        route_input.pending_task is not None
        and route.task_type == route_input.pending_task
        and route.task_type in {TaskType.RELATIONSHIP_ADVICE, TaskType.DATE_PLANNING}
    ):
        # An explicit task turn has begun the pending work even if the user
        # did not use a short continuation phrase.
        return None, None, None, 0, False
    secondary = next(
        (
            task
            for task in route.secondary_tasks
            if task in {TaskType.RELATIONSHIP_ADVICE, TaskType.DATE_PLANNING}
        ),
        None,
    )
    if secondary is not None:
        return (
            secondary,
            f"当前先处理 {route.task_type.value}，后续可继续 {secondary.value}",
            "secondary_task",
            2,
            False,
        )
    if route.task_type == TaskType.OUT_OF_SCOPE:
        return None, None, None, 0, False
    if (
        route_input.pending_task is not None
        and route.task_type in {TaskType.RELATIONSHIP_ADVICE, TaskType.DATE_PLANNING}
        and route.task_type != route_input.pending_task
        and not route.clarification_triggered
    ):
        return None, None, None, 0, False
    if route_input.pending_task is not None and route.task_type == TaskType.GENERAL_CHAT:
        remaining = max(route_input.pending_task_turns_remaining - 1, 0)
        if remaining:
            return (
                route_input.pending_task,
                route_input.pending_task_reason,
                "carried",
                remaining,
                False,
            )
        return None, None, None, 0, False
    return (
        route_input.pending_task,
        route_input.pending_task_reason,
        "carried" if route_input.pending_task is not None else None,
        route_input.pending_task_turns_remaining,
        False,
    )


def _is_clear_out_of_scope(result: RouteResult) -> bool:
    if result.task_type != TaskType.OUT_OF_SCOPE:
        return False
    return result.task_scores.get(TaskType.OUT_OF_SCOPE, 0) >= 4


def _has_explicit_business_request(text: str) -> bool:
    if (
        _has_explicit_date_planning_request(text)
        or _has_relationship_semantic_request(text)
        or _has_general_relationship_request(text)
    ):
        return True
    return any(
        marker in text
        for marker in (
            "约会",
            "恋爱",
            "她不理我",
            "他不理我",
            "关系",
            "表白",
            "吵架",
            "分手",
            "怎么追",
            "怎么回复",
        )
    )


def _has_general_relationship_request(text: str) -> bool:
    return (
        re.search(
            r"(?:感情|恋爱|关系|相处|暧昧|追求).{0,16}"
            r"(?:怎么|如何|怎样|建议|经营|处理|发展|改善|推进)",
            text,
        )
        is not None
        or re.search(
            r"(?:怎么|如何|怎样|建议|经营|处理|发展|改善|推进).{0,16}"
            r"(?:感情|恋爱|关系|相处|暧昧|追求)",
            text,
        )
        is not None
    )


def _has_relationship_semantic_request(text: str) -> bool:
    return any(marker in text for marker in ("她", "他", "对方", "关系")) and any(
        marker in text
        for marker in (
            "吵架",
            "冷战",
            "分手",
            "沟通",
            "回复",
            "不理",
            "怎么办",
            "怎么说",
            "怎么做",
        )
    )


def _out_of_scope_reason(text: str) -> str:
    if any(marker in text for marker in ("代码", "python", "编程", "爬虫", "算法")):
        return "programming_request"
    if any(marker in text for marker in ("胸痛", "诊断", "疾病", "处方")):
        return "medical_request"
    if any(marker in text for marker in ("法律", "起诉", "合同", "律师")):
        return "legal_request"
    if any(marker in text for marker in ("新闻稿", "新闻", "论文", "作业", "翻译", "ppt")):
        return "non_product_request"
    return "unsupported_request"


def _looks_like_advice_request(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "怎么",
            "如何",
            "怎样",
            "怎么办",
            "该不该",
            "要不要",
            "能不能",
            "可以吗",
            "建议",
            "推荐",
            "为什么",
            "什么意思",
            "说明什么",
            "代表什么",
            "怎么看",
            "你看怎么样",
            "你觉得怎么样",
            "你觉得呢",
            "这样可以吗",
            "这样合适吗",
            "合适吗",
            "适合吗",
            "行不行",
            "好不好",
            "可行吗",
        )
    ) or _looks_like_date_action_evaluation(text)


def _has_compound_marker(text: str) -> bool:
    return any(
        marker in text
        for marker in ("先", "然后", "再帮", "再给", "再推荐", "同时", "另外", "顺便", "并且")
    )


def _has_explicit_compound_order(result: RouteResult) -> bool:
    if not _is_compound_task_request(result):
        return False
    return _ORDERED_COMPOUND_PATTERN.search(result.normalized_query) is not None


def _is_context_dependent_follow_up(
    route_input: RouteInput,
    result: RouteResult,
) -> bool:
    if not route_input.recent_messages:
        return False
    text = result.normalized_query
    if _is_exact_casual_chat(text):
        return False
    if _has_explicit_date_planning_request(text):
        return False

    # These expressions explicitly refer to information in the previous
    # turn. They remain model candidates even when a shallow rule can assign
    # a plausible label.
    if any(marker in text for marker in ("下一条", "这样是什么意思", "刚才")):
        return True

    # Do not spend a Router call on a follow-up that already contains a strong
    # domain cue (for example, "第一次找她聊天，聊什么").
    strongest_scenario = max(result.scenario_scores.values(), default=0)
    strongest_goal = max(result.goal_scores.values(), default=0)
    if strongest_scenario >= 3 or strongest_goal >= 3:
        return False
    return any(
        text.startswith(marker) for marker in ("那", "如果", "还是", "然后", "接下来", "继续", "再")
    )


def _is_compound_task_request(result: RouteResult) -> bool:
    if not result.secondary_tasks or not _has_compound_marker(result.normalized_query):
        return False
    # A task switch such as "先不安排约会了" is handled deterministically by
    # the cancellation rule and should not be sent back to the Router.
    return not _is_cancelled_date_request(result.normalized_query)


def _needs_semantic_scenario_correction(
    result: RouteResult,
    ambiguity_margin: float,
) -> bool:
    text = result.normalized_query
    if not _looks_like_advice_request(text):
        return False

    meaningful_scenarios = [score for score in result.scenario_scores.values() if score >= 2]
    strongest_scenario = max(result.scenario_scores.values(), default=0)
    strongest_goal = max(result.goal_scores.values(), default=0)

    # A generic request such as "我喜欢她，有什么建议" has no actionable
    # scenario/goal cue. It is the intended use case for semantic correction.
    if strongest_scenario < 2 or (strongest_goal < 2 and strongest_scenario < 3.5):
        return True

    # Multiple labels are useful downstream, but only an explicitly ordered
    # and genuinely close compound question needs an LLM to choose priority.
    return (
        len(meaningful_scenarios) > 1
        and _top_margin(result.scenario_scores) < ambiguity_margin
        and _has_compound_marker(text)
    )


def _apply_contextual_scenario_scores(
    route_input: RouteInput,
    text: str,
    scores: dict[AdviceScenario, float],
    evidence: dict[AdviceScenario, list[str]],
) -> None:
    if not route_input.recent_messages:
        return
    user_turns = [
        normalize_route_text(message.content)
        for message in route_input.recent_messages
        if message.role == MessageRole.USER
    ][-5:]
    if not user_turns:
        return

    current_primary = max(scores, key=scores.get) if scores else None
    current_strength = scores.get(current_primary, 0) if current_primary else 0
    contextual: dict[AdviceScenario, float] = {}
    resolved_scenarios = {
        scenario
        for scenario in _CONTEXT_CONTINUITY_SCENARIOS
        if _scenario_is_resolved(scenario, text)
    }
    for distance, historical_text in enumerate(reversed(user_turns), start=1):
        historical_scores, _ = _score_labels(
            historical_text,
            _SCENARIO_PATTERNS,
            suppress_negated=True,
        )
        historical_evidence: dict[AdviceScenario, list[str]] = {}
        _apply_regex_scores(
            historical_text,
            historical_scores,
            historical_evidence,
            _SCENARIO_REGEX_PATTERNS,
        )
        decay = 0.78 ** (distance - 1)
        for scenario in _CONTEXT_CONTINUITY_SCENARIOS:
            if _scenario_is_resolved(scenario, historical_text):
                resolved_scenarios.add(scenario)
                continue
            historical_score = historical_scores.get(scenario, 0)
            if historical_score < 2 or scenario in resolved_scenarios:
                continue
            if (
                current_primary is not None
                and current_primary != scenario
                and current_strength >= 4
            ):
                continue
            contextual[scenario] = max(
                contextual.get(scenario, 0),
                min(4.5, historical_score * decay),
            )

    for scenario, score in contextual.items():
        scores[scenario] = scores.get(scenario, 0) + score
        evidence.setdefault(scenario, []).append(f"上下文延续:{scenario.value}")


def _scenario_is_resolved(scenario: AdviceScenario, text: str) -> bool:
    patterns = _SCENARIO_RESOLUTION_PATTERNS.get(scenario, ())
    return any(pattern.search(text) is not None for pattern in patterns)


def _apply_regex_scores[LabelT](
    text: str,
    scores: dict[LabelT, float],
    evidence: dict[LabelT, list[str]],
    patterns: dict[LabelT, tuple[tuple[str, float], ...]],
) -> None:
    for label, entries in patterns.items():
        for pattern, weight in entries:
            match = re.search(pattern, text)
            if match is None or _is_negated(text, match.start()):
                continue
            scores[label] = scores.get(label, 0) + weight
            evidence.setdefault(label, []).append(match.group(0))


def _apply_default_goal(
    text: str,
    primary_scenario: AdviceScenario | None,
    scenario_scores: dict[AdviceScenario, float],
    goal_scores: dict[AdviceGoal, float],
    goal_evidence: dict[AdviceGoal, list[str]],
) -> None:
    if goal_scores or primary_scenario is None or not _looks_like_advice_request(text):
        return
    meaningful = [score for score in scenario_scores.values() if score >= 2]
    if len(meaningful) != 1 or _score_confidence(scenario_scores, 7) < 0.72:
        return
    default_goal = _DEFAULT_GOAL_BY_SCENARIO.get(primary_scenario)
    if default_goal is None:
        return
    marker = next(
        (
            marker
            for marker in (
                "怎么",
                "如何",
                "怎样",
                "怎么办",
                "为什么",
                "什么意思",
                "建议",
            )
            if marker in text
        ),
        "咨询",
    )
    goal_scores[default_goal] = 1.5
    goal_evidence.setdefault(default_goal, []).append(marker)


def _apply_contextual_goal_score(
    primary_scenario: AdviceScenario | None,
    scenario_evidence: dict[AdviceScenario, list[str]],
    goal_scores: dict[AdviceGoal, float],
    goal_evidence: dict[AdviceGoal, list[str]],
) -> None:
    if primary_scenario is None:
        return
    continuity_marker = f"上下文延续:{primary_scenario.value}"
    if continuity_marker not in scenario_evidence.get(primary_scenario, []):
        return
    goal = _DEFAULT_GOAL_BY_SCENARIO.get(primary_scenario)
    if goal is None:
        return
    goal_scores[goal] = goal_scores.get(goal, 0) + 4
    goal_evidence.setdefault(goal, []).append(continuity_marker)


def _is_executable_date_mode(mode: DateRequestMode) -> bool:
    return mode in {
        DateRequestMode.PLACE_SEARCH,
        DateRequestMode.ITINERARY,
        DateRequestMode.MODIFY,
    }


def _looks_like_implicit_date_plan_bundle(
    text: str,
    slots: DatePlanSlots,
) -> bool:
    if (
        slots.budget is None
        or (slots.city is None and slots.area is None)
        or _looks_like_advice_request(text)
        or _looks_like_date_action_evaluation(text)
        or _is_cancelled_date_request(text)
    ):
        return False
    requested_stops = {
        *slots.dining_keywords,
        *slots.activity_keywords,
    }
    if len(requested_stops) < 2:
        return False
    return (
        re.search(
            r"(?:我|我们|对象|女朋友|男朋友|伴侣).{0,8}(?:喜欢|想吃|想去|偏好)",
            text,
        )
        is not None
    )


def _infer_date_request_mode(
    route_input: RouteInput,
    text: str,
    latest_slots: DatePlanSlots,
) -> DateRequestMode:
    request_clause = _last_request_clause(text)
    if _has_reported_date_planning_context(text):
        return DateRequestMode.NONE
    state = route_input.date_task_state
    if state is not None and state.is_resumable:
        if _is_cancelled_date_request(text):
            return DateRequestMode.MODIFY
        if _looks_like_date_edit_request(text):
            return DateRequestMode.MODIFY
        relationship_turn = (
            _is_relationship_clarification_answer(route_input, text)
            or _looks_like_historical_relationship_report(text)
            or _looks_like_relationship_switch(text)
        )
        if relationship_turn:
            return DateRequestMode.NONE
        if _looks_like_date_category_recommendation(request_clause):
            return DateRequestMode.CATEGORY_RECOMMENDATION
        if _looks_like_date_action_evaluation(text):
            return DateRequestMode.EVALUATE
        if _date_slots_have_values(latest_slots) or _looks_like_date_nonanswer(text):
            return DateRequestMode.MODIFY

    if _looks_like_date_category_recommendation(request_clause):
        return DateRequestMode.CATEGORY_RECOMMENDATION

    if _looks_like_trip_itinerary_request(text):
        return DateRequestMode.ITINERARY

    if _has_direct_date_execution_command(text):
        if _looks_like_itinerary_request(text):
            return DateRequestMode.ITINERARY
        if _looks_like_place_search_request(request_clause):
            return DateRequestMode.PLACE_SEARCH
        return DateRequestMode.ITINERARY

    if _looks_like_date_action_evaluation(text):
        return DateRequestMode.EVALUATE
    if _looks_like_place_search_request(request_clause):
        return DateRequestMode.PLACE_SEARCH
    if _looks_like_itinerary_request(text):
        return DateRequestMode.ITINERARY
    if _looks_like_implicit_date_plan_bundle(text, latest_slots):
        return DateRequestMode.ITINERARY
    if "约会" in text and _date_slot_signal_count(latest_slots) >= 2:
        return DateRequestMode.ITINERARY
    if _has_explicit_date_planning_request(text):
        return (
            DateRequestMode.PLACE_SEARCH
            if _DATE_PLACE_TARGET_PATTERN.search(request_clause)
            else DateRequestMode.ITINERARY
        )
    return DateRequestMode.NONE


def _last_request_clause(text: str) -> str:
    clauses = [clause.strip() for clause in re.split(r"[，,。；;！？!?]+", text) if clause.strip()]
    if not clauses:
        return text
    for clause in reversed(clauses):
        if re.search(r"怎么|如何|哪|什么|推荐|建议|帮|安排|规划|找|搜索|可以吗|怎么样", clause):
            return clause
    return clauses[-1]


def _looks_like_date_category_recommendation(clause: str) -> bool:
    has_category = _DATE_CATEGORY_TARGET_PATTERN.search(clause) is not None
    has_category_comparison = (
        re.search(
            r"(?:吃|喝|去|选).{0,10}(?:还是|或者|or).{0,10}"
            r"(?:吃|喝|去|选|火锅|咖啡|电影|展览|散步)",
            clause,
        )
        is not None
    )
    has_request = (
        re.search(
            r"推荐|建议|选(?:择)?|哪(?:种|类|些)|什么|吃啥|吃什么|适合|更自然|尴尬",
            clause,
        )
        is not None
    )
    asks_for_concrete_place = (
        _DATE_PLACE_TARGET_PATTERN.search(clause) is not None
        and re.search(r"哪家|哪里|附近|具体|一家|一个|搜索|查找", clause) is not None
    )
    return (has_category or has_category_comparison) and has_request and not asks_for_concrete_place


def _has_direct_date_execution_command(text: str) -> bool:
    command = re.search(
        r"(?:请|麻烦|帮我|请你|给我|能帮我|能不能|能否|可以帮我|希望你|想让你)"
        r".{0,16}(?:安排|规划|制定|生成|推荐|找|搜索)",
        text,
    )
    if command is not None and _DATE_EXECUTION_TARGET_PATTERN.search(
        text[command.start() : command.end() + 24]
    ):
        return True
    return (
        re.search(
            r"^(?:安排|规划|制定|生成|推荐|找|搜索).{0,20}"
            r"(?:约会|行程|路线|攻略|餐厅|饭店|餐馆|菜馆|咖啡馆|地点|场所|景点|"
            r"博物馆|美术馆|电影院|影院)",
            text,
        )
        is not None
    )


def _has_local_date_execution_authorization(text: str) -> bool:
    """Require a user-directed planning command, not just a date activity mention."""

    if _has_explicit_date_planning_request(text) or _has_direct_date_execution_command(text):
        return True
    has_agent_command = (
        re.search(
            r"(?:请|麻烦|帮我|请你|给我|能帮我|能不能|能否|可以帮我|希望你|想让你)"
            r".{0,16}(?:安排|规划|推荐|找|搜索)",
            text,
        )
        is not None
    )
    has_planning_context = any(
        marker in text
        for marker in (
            "约会",
            "行程",
            "路线",
            "餐厅",
            "地点",
            "场所",
            "景点",
            "城市",
            "预算",
            "周末",
            "下周",
            "这周",
            "找个地方",
        )
    )
    return has_agent_command and has_planning_context


def _looks_like_place_search_request(clause: str) -> bool:
    return (
        _DATE_PLACE_TARGET_PATTERN.search(clause) is not None
        and re.search(r"推荐|找|搜索|查|哪家|哪里|附近|具体|一家|一个", clause) is not None
    )


def _looks_like_itinerary_request(text: str) -> bool:
    has_plan_target = (
        re.search(
            r"约会.{0,8}(?:安排|计划|规划|攻略)|"
            r"(?:安排|计划|规划|制定|生成).{0,8}(?:约会|行程|日程|路线|旅行|旅游|旅程)|"
            r"(?:一份|一个).{0,6}(?:约会计划|约会安排|行程|日程|路线|攻略|旅行计划)",
            text,
        )
        is not None
    )
    has_request = (
        re.search(
            r"帮|请|麻烦|给我|需要|想要|希望你|想让你|安排|计划|规划|制定|生成",
            text,
        )
        is not None
    )
    return has_plan_target and has_request


def _looks_like_trip_itinerary_request(text: str) -> bool:
    has_trip = re.search(r"旅游|旅行|旅程|度假|[一二三四五六七八九十两0-9]+(?:天|日)游", text)
    if has_trip is None:
        return False
    return (
        re.search(
            r"(?:怎么|如何|怎样|有什么|帮我|请你|给我).{0,8}(?:安排|规划|计划|攻略)|"
            r"(?:安排|规划|制定|生成).{0,12}(?:行程|路线|旅行|旅游|旅程)|"
            r"(?:行程|路线|攻略|旅行计划).{0,8}(?:怎么|如何|安排|规划|推荐)",
            text,
        )
        is not None
    )


def _date_slots_have_values(slots: DatePlanSlots) -> bool:
    return any(
        value is not None and value != [] and value != {}
        for value in (
            slots.city,
            slots.area,
            slots.plan_mode,
            slots.date,
            slots.end_date,
            slots.day_count,
            slots.nights,
            slots.target_day,
            slots.start_time,
            slots.budget,
            slots.budget_scope,
            slots.preferences,
            slots.dining_keywords,
            slots.meal_keywords,
            slots.activity_keywords,
            slots.schedule_hints,
            slots.replace_place_names,
            slots.excluded_keywords,
            slots.transport_mode,
            slots.notes,
            slots.constraints,
            slots.lodging_notes,
        )
    )


def _date_slot_signal_count(slots: DatePlanSlots) -> int:
    return sum(
        value is not None and value != [] and value != {}
        for value in (
            slots.city,
            slots.area,
            slots.plan_mode,
            slots.date,
            slots.end_date,
            slots.day_count,
            slots.target_day,
            slots.start_time,
            slots.budget,
            slots.budget_scope,
            slots.preferences,
            slots.dining_keywords,
            slots.meal_keywords,
            slots.activity_keywords,
            slots.transport_mode,
            slots.constraints,
            slots.lodging_notes,
        )
    )


def _is_cancelled_date_request(text: str) -> bool:
    return any(
        re.search(pattern, text)
        for pattern in (
            r"(?:先|暂时)?不(?:要|想)?安排.{0,4}约会",
            r"取消.{0,4}约会",
            r"先不约了",
        )
    )


def _looks_like_date_candidate(text: str, result: RouteResult | None) -> bool:
    """Find candidates worth semantic review, without making the decision.

    The phrases below are intentionally broad.  They only open the LLM
    correction path; the returned task type still has to come from the
    semantic classifier and can be relationship advice instead.
    """

    if _is_cancelled_date_request(text):
        return False
    if result is not None and result.date_request_mode in {
        DateRequestMode.EVALUATE,
        DateRequestMode.CATEGORY_RECOMMENDATION,
    }:
        return False
    if _looks_like_date_action_evaluation(text):
        return False
    if result is not None and result.task_type == TaskType.DATE_PLANNING:
        # Strong, explicit date requests stay on the fast path.  A weak date
        # score (for example "周末想和她找个地方坐坐") remains a candidate
        # for semantic classification.
        strongest = max(result.task_scores.values(), default=0)
        concrete_request = _has_concrete_date_planning_detail(text)
        return not (result.task_confidence >= 0.82 and (strongest >= 5 or concrete_request))
    date_markers = (
        "约会",
        "见面",
        "周末",
        "下周",
        "这周",
        "一起吃",
        "吃饭",
        "吃顿饭",
        "看电影",
        "出去玩",
        "一起出去",
        "约出来",
        "见一面",
        "碰面",
        "共度",
        "一起逛",
        "找个地方",
        "去哪玩",
        "去哪里玩",
        "安排一下",
        "逛街",
        "活动",
    )
    relationship_markers = ("她", "他", "对象", "女朋友", "男朋友", "喜欢的人")
    return any(marker in text for marker in date_markers) and (
        any(marker in text for marker in relationship_markers)
        or any(marker in text for marker in ("安排", "推荐", "预算", "地点"))
    )


def _has_explicit_date_planning_request(text: str) -> bool:
    """Return whether the user asks the agent to build or search a plan."""

    return any(
        _has_explicit_date_request_clause(clause)
        for clause in re.split(r"[，,。；;！？!?]+", text)
        if clause.strip()
    )


def _has_explicit_date_request_clause(clause: str) -> bool:
    clause = clause.strip()
    for marker in _EXPLICIT_DATE_PLANNING_MARKERS:
        start = clause.find(marker)
        while start >= 0:
            if _is_direct_request_occurrence(clause, start):
                return True
            start = clause.find(marker, start + 1)

    for operation in _DATE_PLANNING_OPERATION_PATTERN.finditer(clause):
        nearby = clause[max(0, operation.start() - 8) : operation.end() + 20]
        if _DATE_PLANNING_TARGET_PATTERN.search(nearby) is None:
            continue
        if _is_direct_request_occurrence(clause, operation.start()):
            return True
    return False


def _has_reported_date_planning_context(text: str) -> bool:
    for raw_clause in re.split(r"[，,。；;！？!?]+", text):
        clause = raw_clause.strip()
        if not clause:
            continue
        for marker in _EXPLICIT_DATE_PLANNING_MARKERS:
            start = clause.find(marker)
            if start >= 0 and _is_third_party_action_occurrence(clause, start):
                return True
        for operation in _DATE_PLANNING_OPERATION_PATTERN.finditer(clause):
            nearby = clause[max(0, operation.start() - 8) : operation.end() + 20]
            if _DATE_PLANNING_TARGET_PATTERN.search(
                nearby
            ) is not None and _is_third_party_action_occurrence(clause, operation.start()):
                return True
    return False


def _is_direct_request_occurrence(clause: str, action_start: int) -> bool:
    if action_start == 0:
        return True
    prefix = clause[max(0, action_start - 24) : action_start]
    if _THIRD_PARTY_ACTION_PREFIX.search(prefix) and not _COMITATIVE_RELATIONSHIP_PREFIX.search(
        prefix
    ):
        return False
    return _DIRECT_AGENT_REQUEST_PREFIX.search(prefix) is not None


def _is_third_party_action_occurrence(clause: str, action_start: int) -> bool:
    prefix = clause[max(0, action_start - 24) : action_start]
    if _COMITATIVE_RELATIONSHIP_PREFIX.search(prefix) or _AGENT_DIRECTIVE_PREFIX.search(prefix):
        return False
    return _THIRD_PARTY_ACTION_PREFIX.search(prefix) is not None


def _has_ordered_primary_relationship_request(text: str) -> bool:
    return _ORDERED_RELATIONSHIP_FIRST_PATTERN.search(text) is not None


def _has_concrete_date_planning_detail(text: str) -> bool:
    return _has_explicit_date_planning_request(text) and any(
        marker in text for marker in _CONCRETE_DATE_PLANNING_MARKERS
    )


def _looks_like_date_action_evaluation(text: str) -> bool:
    """Detect evaluating a proposed date action instead of requesting a plan."""

    if _has_direct_date_execution_command(text):
        return False
    has_action = (
        any(marker in text for marker in _DATE_ACTION_MARKERS)
        or re.search(
            r"(?:下一次|下次).{0,12}(?:见面|活动|安排|计划)",
            text,
        )
        is not None
    )
    has_person = any(marker in text for marker in _DATE_RELATIONSHIP_MARKERS) or any(
        marker in text for marker in ("我们", "我俩", "双方", "约会")
    )
    has_evaluation = (
        any(marker in text for marker in _DATE_EVALUATION_MARKERS)
        or re.search(
            r"(?:怎么样|如何|是否合适|是否可行|妥不妥|值不值得)[吗呢]?$",
            text.rstrip("。.?!！？"),
        )
        is not None
    )
    return has_action and has_person and has_evaluation


def _looks_like_relationship_switch(text: str) -> bool:
    return (
        _looks_like_historical_relationship_report(text)
        or _looks_like_relationship_communication_request(text)
        or any(
            marker in text
            for marker in (
                "不理我",
                "不回我",
                "吵架",
                "冷战",
                "怎么追",
                "怎么接近",
                "怎么聊天",
                "关系",
                "她喜欢不喜欢",
            )
        )
    )


def _looks_like_relationship_communication_request(text: str) -> bool:
    return (
        re.search(
            r"(?:怎么|如何|怎样).{0,16}"
            r"(?:回复|回应|接话|展开.{0,4}话题|延续.{0,4}话题|聊天|沟通|交流)",
            text,
        )
        is not None
    )


def _should_score_date_follow_up(
    route_input: RouteInput,
    text: str,
    latest_slots: DatePlanSlots,
) -> bool:
    state = route_input.date_task_state
    # A paused task retains its verified slots for an explicit future resume,
    # but it must not turn a generic acknowledgement into date planning after
    # a safety interruption, task switch, or cancellation.
    if state is None or not state.is_active or _is_exact_casual_chat(text):
        return False
    if (
        _looks_like_date_action_evaluation(text)
        or _is_relationship_clarification_answer(route_input, text)
        or _looks_like_historical_relationship_report(text)
        or _looks_like_relationship_switch(text)
    ):
        return False
    has_new_slot = any(
        value is not None and value != []
        for value in (
            latest_slots.city,
            latest_slots.area,
            latest_slots.plan_mode,
            latest_slots.date,
            latest_slots.end_date,
            latest_slots.day_count,
            latest_slots.nights,
            latest_slots.target_day,
            latest_slots.start_time,
            latest_slots.budget,
            latest_slots.budget_scope,
            latest_slots.preferences,
            latest_slots.dining_keywords,
            latest_slots.meal_keywords,
            latest_slots.activity_keywords,
            latest_slots.schedule_hints,
            latest_slots.replace_place_names,
            latest_slots.excluded_keywords,
            latest_slots.transport_mode,
            latest_slots.notes,
            latest_slots.constraints,
            latest_slots.lodging_notes,
        )
    )
    if has_new_slot:
        return True
    # A non-answer to a clarification is still part of the workflow, but an
    # arbitrary short relationship statement must not be forced into date
    # planning when the LLM is unavailable.
    return _looks_like_date_nonanswer(text) and not _looks_like_relationship_switch(text)


def _looks_like_date_nonanswer(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "不确定",
            "不知道",
            "没想好",
            "随便",
            "都可以",
            "看你安排",
            "你安排",
            "暂时没有",
            "还没决定",
            "无所谓",
            "好的",
            "可以",
            "行",
            "按默认",
            "按你说的",
        )
    )


def _is_obvious_date_supplement(text: str) -> bool:
    if _looks_like_historical_relationship_report(text):
        return False
    slots = extract_date_plan_slots(RouteInput(latest_query=text))
    has_slot = any(
        value is not None and value != []
        for value in (
            slots.city,
            slots.area,
            slots.plan_mode,
            slots.date,
            slots.end_date,
            slots.day_count,
            slots.nights,
            slots.target_day,
            slots.start_time,
            slots.budget,
            slots.budget_scope,
            slots.preferences,
            slots.dining_keywords,
            slots.meal_keywords,
            slots.activity_keywords,
            slots.schedule_hints,
            slots.replace_place_names,
            slots.excluded_keywords,
            slots.transport_mode,
            slots.lodging_notes,
        )
    )
    if not has_slot or _looks_like_advice_request(text) or _looks_like_relationship_switch(text):
        return False
    compact = re.sub(r"^[\s，。；;]+|[\s，。；;]+$", "", text)
    city_only = slots.city is not None and compact in {slots.city, f"{slots.city}市"}
    explicit_marker = any(
        marker in text
        for marker in (
            "预算",
            "元",
            "晚饭",
            "晚餐",
            "西餐",
            "日料",
            "博物馆",
            "美术馆",
            "景点",
            "餐厅",
            "咖啡馆",
            "火锅",
            "日期",
            "时间",
            "每天",
            "多日",
            "多天",
            "酒店",
            "住宿",
            "住在",
            "增加",
            "添加",
            "替换",
            "删除",
            "不安排",
            "太赶",
            "周",
            "星期",
            "月",
            "日",
            "下午",
            "上午",
            "晚上",
            "地铁",
            "公交",
            "开车",
            "骑车",
            "城市",
            "区域",
            "商圈",
        )
    )
    planning_context = any(marker in text for marker in ("想去", "想看", "希望", "偏好"))
    return city_only or explicit_marker or (len(compact) <= 30 and planning_context)


def _is_relationship_clarification_answer(
    route_input: RouteInput,
    text: str,
) -> bool:
    if _has_explicit_date_planning_request(text) or _looks_like_date_edit_request(text):
        return False
    last_assistant = next(
        (
            message.content
            for message in reversed(route_input.recent_messages)
            if message.role == MessageRole.ASSISTANT
        ),
        "",
    )
    if not last_assistant or not any(
        marker in last_assistant for marker in ("待确认：", "需要进一步确认", "需要确认：")
    ):
        return False
    return _looks_like_historical_relationship_report(text) or any(
        marker in text for marker in ("她", "他", "我们", "对方", "我俩", "双方")
    )


def _looks_like_historical_relationship_report(text: str) -> bool:
    if _has_explicit_date_planning_request(text) or _looks_like_date_edit_request(text):
        return False
    has_relationship_subject = any(
        marker in text for marker in ("她", "他", "我们", "对方", "我俩", "一起", "我生病")
    )
    has_historical_or_interaction_signal = any(
        marker in text
        for marker in (
            "逛过",
            "去过",
            "约过",
            "约我",
            "关心我",
            "照顾我",
            "陪过",
            "主动找我",
            "主动约我",
            "曾经",
            "之前",
            "上次",
            "以前",
        )
    )
    return has_relationship_subject and has_historical_or_interaction_signal


def _looks_like_date_edit_request(text: str) -> bool:
    return looks_like_date_modification_semantics(text) or (
        re.search(
            r"(?:换一个|换一家|换个|换成|换为|替换|更换|改成|改为|"
            r"增加|添加|再加|加一个|加一家|删掉|删除|去掉|移除|重新规划|调整顺序|"
            r"(?:上午|中午|下午|晚上|午餐|晚餐).{0,8}(?:吃|去|安排)|"
            r"预算.{0,8}\d|第.{0,4}天.{0,12}(?:去|吃|换|改|增加|删除|安排))",
            text,
        )
        is not None
    )


def _infer_date_intent(
    route_input: RouteInput,
    text: str,
    task_type: TaskType,
    latest_slots: DatePlanSlots,
) -> DateTaskIntent:
    if _is_cancelled_date_request(text):
        return DateTaskIntent.CANCEL
    state = route_input.date_task_state
    if state is not None and state.is_resumable:
        if task_type == TaskType.DATE_PLANNING:
            if _should_score_date_follow_up(route_input, text, latest_slots):
                return DateTaskIntent.SUPPLEMENT
            return DateTaskIntent.CONTINUE
        if _looks_like_relationship_switch(text) or _looks_like_date_action_evaluation(text):
            return DateTaskIntent.SWITCH
    if task_type == TaskType.DATE_PLANNING:
        return DateTaskIntent.NEW_REQUEST
    return DateTaskIntent.NONE


def _infer_date_mutation(
    route_input: RouteInput,
    text: str,
    task_type: TaskType,
    date_intent: DateTaskIntent,
) -> DatePlanMutation:
    if task_type != TaskType.DATE_PLANNING or date_intent == DateTaskIntent.NONE:
        return DatePlanMutation.NONE
    if date_intent == DateTaskIntent.NEW_REQUEST:
        return DatePlanMutation.NONE

    if re.search(r"重新.{0,4}(?:规划|安排)|换一套|全部重排|从头安排|重新来", text):
        return DatePlanMutation.REPLAN
    if _is_constraint_mutation(text):
        return DatePlanMutation.UPDATE_CONSTRAINT
    if re.search(r"替换|更换|换成|换为|换一个|换一家|换个|改成|改为", text):
        return DatePlanMutation.REPLACE
    if re.search(r"删除|删掉|去掉|移除|取消(?:第|这个|该)?(?:个)?(?:活动|景点|餐厅|项目)", text):
        return DatePlanMutation.REMOVE
    if re.search(r"调整顺序|改变顺序|先去.{0,16}再去|把第.{0,8}放到", text):
        return DatePlanMutation.REORDER
    if re.search(r"增加|添加|再加|加一个|加一家|加上|加入|安排到行程|还想|顺便|再去", text):
        return DatePlanMutation.ADD
    # Users often describe an additional stop as part of the itinerary rather
    # than issuing an imperative: "下午看电影，晚饭吃火锅，之后去景点".
    # When an active plan exists, treat an explicitly mentioned but uncovered
    # venue type as an additive edit.  Constraint-only replies still fall
    # through to UPDATE_CONSTRAINT.
    if (
        route_input.date_task_state is not None
        and route_input.date_task_state.current_plan is not None
        and _has_unplanned_date_nodes(
            route_input.date_task_state,
            extract_date_plan_slots(RouteInput(latest_query=text)),
        )
    ):
        return DatePlanMutation.ADD
    if route_input.date_task_state is not None:
        return DatePlanMutation.UPDATE_CONSTRAINT
    return DatePlanMutation.NONE


def _is_constraint_mutation(text: str) -> bool:
    constraint = (
        r"(?:总?预算|每天预算|每日预算|城市|区域|商圈|日期|时间|出发时间|"
        r"交通(?:方式)?|天数|行程天数|预算范围)"
    )
    action = (
        r"(?:改(?:为|成|到)|调整(?:到|为)|提高到|增加到|降低到|降到|"
        r"控制在|设(?:为|成)|换成|换为)"
    )
    return re.search(rf"{constraint}.{{0,8}}{action}", text) is not None


def _has_unplanned_date_nodes(
    state: DatePlanningTaskState,
    slots: DatePlanSlots,
) -> bool:
    plan = state.current_plan
    if plan is None or not plan.items:
        return False
    requested = [*slots.activity_keywords, *slots.dining_keywords]
    requested.extend(
        keyword
        for values in slots.meal_keywords.values()
        for keyword in values
        if keyword not in requested
    )
    return any(
        not any(
            _plan_item_matches_keyword(item, keyword)
            for item in plan.items
            if slots.target_day is None or item.day_index == slots.target_day
        )
        for keyword in _unique(requested)
    )


def _plan_item_matches_keyword(item, keyword: str) -> bool:
    if item.slot_keyword == keyword or keyword in item.place.search_keywords:
        return True
    haystack = " ".join(
        [
            item.place.name,
            item.place.type_name or "",
            *item.place.tags,
            *item.place.matched_preferences,
        ]
    )
    return keyword in haystack


def _date_missing_fields(slots: DatePlanSlots) -> list[str]:
    # City is required for live venue search. Date and budget are useful
    # planning inputs but have safe fallbacks, so they are reported rather
    # than treated as hard blockers.
    missing: list[str] = []
    if not slots.city:
        missing.append("city")
    if slots.plan_mode == DatePlanMode.MULTI_DAY and not slots.day_count:
        missing.append("trip_days")
    if slots.date is None and slots.start_time is None:
        missing.append("date_time")
    if slots.budget is None:
        missing.append("budget")
    return missing


def _extract_lodging_notes(text: str) -> list[str]:
    return list(
        dict.fromkeys(
            clause.strip()
            for clause in re.split(r"[，。；;！？!?]+", text)
            if clause.strip()
            and any(marker in clause for marker in ("酒店", "住宿", "民宿", "大床房", "双床房"))
        )
    )[:8]


def _extract_place_keywords(text: str) -> tuple[list[str], list[str], list[str]]:
    """Extract explicit venue/cuisine terms without turning soft preferences into queries."""

    dining: list[str] = []
    activities: list[str] = []
    excluded: list[str] = []
    groups = (
        (dining, ("西餐", "西餐厅")),
        (dining, ("日料", "日本料理")),
        (dining, ("火锅",)),
        (dining, ("烧烤",)),
        (dining, ("素食", "素菜")),
        (dining, ("韩国料理", "韩餐", "韩国烤肉")),
        (dining, ("海底捞",)),
        (activities, ("博物馆",)),
        (activities, ("美术馆",)),
        (activities, ("景点", "旅游景点")),
        (activities, ("公园",)),
        (activities, ("电影院", "电影")),
        (activities, ("剧场", "演出")),
    )
    for target, aliases in groups:
        canonical = aliases[0]
        if not any(alias in text for alias in aliases):
            continue
        if _is_negated_place_keyword(text, aliases):
            excluded.append(canonical)
        else:
            target.append(canonical)
    return _unique(dining), _unique(activities), _unique(excluded)


def _extract_replace_place_names(text: str) -> list[str]:
    patterns = (
        r"(?:不去|不想去|不要去|不再去|取消)\s*"
        r"([^，,。；;]{2,40}?)(?=\s*(?:，|,|。|；|;|然后|再)?\s*(?:换|改))",
        r"(?:把|将)\s*([^，,。；;]{2,40}?)\s*"
        r"(?:换成|换为|替换为|改成|改为)",
    )
    names: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = re.sub(
                r"^第\s*(?:[0-9]{1,2}|[一二三四五六七八九十两]{1,3})\s*天\s*",
                "",
                match.group(1),
            )
            value = re.sub(r"^(?:上午|中午|下午|晚上)\s*", "", value)
            value = value.removeprefix("的")
            value = re.sub(r"^(?:原来的|原先的|之前的|当前的|这个|那个|该)", "", value)
            value = value.removesuffix("了")
            value = value.strip(" ，,。；;")
            if (
                value
                and value not in {"活动", "安排", "景点", "餐厅", "用餐"}
                and not _is_constraint_reference(value)
            ):
                names.append(value)
    return _unique(names)[:8]


def _extract_schedule_slots(text: str) -> tuple[dict[str, list[str]], list[str]]:
    """Keep meal roles and relative timing separate from venue search terms.

    A single turn can mention several independent stops.  Storing only a flat
    list such as ``["日料", "火锅"]`` loses the distinction between lunch and
    dinner, which later makes an incremental planner choose an arbitrary order.
    """

    meal_markers = {
        "lunch": ("午餐", "午饭", "中饭", "中餐", "中午"),
        "dinner": ("晚餐", "晚饭", "晚宴", "晚上"),
        "breakfast": ("早餐", "早饭", "早上"),
    }
    cuisine_aliases = {
        "西餐": ("西餐", "西餐厅"),
        "日料": ("日料", "日本料理"),
        "火锅": ("火锅",),
        "烧烤": ("烧烤",),
        "素食": ("素食", "素菜"),
        "韩国料理": ("韩国料理", "韩餐", "韩国烤肉"),
        "海底捞": ("海底捞",),
    }
    meal_keywords: dict[str, list[str]] = {}
    clauses = split_date_clauses(text)
    for parsed_clause in clauses:
        clause = parsed_clause.text
        marker_positions = [
            (meal_type, marker, position)
            for meal_type, markers in meal_markers.items()
            for marker in markers
            for position in _all_positions(clause, marker)
        ]
        clock_meal_type = _meal_type_from_clock(clause)
        for keyword, aliases in cuisine_aliases.items():
            for alias in aliases:
                for position in _all_positions(clause, alias):
                    nearest = _nearest_meal_marker(clause, position, marker_positions)
                    meal_type = nearest[0] if nearest is not None else clock_meal_type
                    if meal_type is None:
                        continue
                    meal_keywords.setdefault(meal_type, []).append(keyword)
                    break

    schedule_hints: list[str] = []
    for marker in ("上午", "中午", "下午", "晚上", "午餐", "午饭", "晚餐", "晚饭"):
        if marker in text:
            schedule_hints.append(marker)
    if re.search(r"看完\s*(?:场)?电影[^，。；;！？!?]{0,8}(?:后|之后)", text):
        schedule_hints.append("看完电影后")
    if re.search(r"看完\s*(?:场)?电影[^，。；;！？!?]{0,8}想去", text):
        schedule_hints.append("电影后安排活动")
    return (
        {meal: _unique(values) for meal, values in meal_keywords.items()},
        _unique(schedule_hints)[:8],
    )


def _all_positions(text: str, value: str) -> list[int]:
    positions: list[int] = []
    start = 0
    while True:
        position = text.find(value, start)
        if position < 0:
            return positions
        positions.append(position)
        start = position + max(len(value), 1)


def _nearest_meal_marker(
    text: str,
    keyword_position: int,
    marker_positions: list[tuple[str, str, int]],
) -> tuple[str, str, int] | None:
    candidates: list[tuple[int, tuple[str, str, int]]] = []
    for marker in marker_positions:
        distance = abs(keyword_position - marker[2])
        if distance > 14:
            continue
        left, right = sorted((keyword_position, marker[2]))
        segment = text[left:right]
        if re.search(r"[，。；;！？!?、]", segment):
            continue
        candidates.append((distance, marker))
    if not candidates:
        return None
    return min(candidates, key=lambda value: value[0])[1]


def _meal_type_from_clock(text: str) -> str | None:
    match = re.search(r"(?<!\d)(\d{1,2})(?:点|时|:)(\d{1,2})?(?!\d)", text)
    if match is None:
        return None
    hour = int(match.group(1))
    if 6 <= hour <= 10:
        return "breakfast"
    if 11 <= hour <= 14:
        return "lunch"
    if 17 <= hour <= 21:
        return "dinner"
    return None


def _normalize_constraint_reference(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold())


def _is_constraint_reference(value: str) -> bool:
    normalized = _normalize_constraint_reference(value)
    return normalized in _CONSTRAINT_REFERENCE_TERMS or bool(
        re.fullmatch(
            r"(?:总?预算(?:上限|范围)?|日期|时间|城市|区域|商圈|交通(?:方式)?|"
            r"行程天数|天数|出发时间|开始时间)",
            normalized,
        )
    )


_CONSTRAINT_REFERENCE_TERMS = {
    "预算",
    "总预算",
    "日期",
    "时间",
    "城市",
    "区域",
    "商圈",
    "交通",
    "交通方式",
    "天数",
    "行程天数",
}


def _is_negated_place_keyword(text: str, aliases: tuple[str, ...]) -> bool:
    escaped = "|".join(re.escape(alias) for alias in aliases)
    return (
        re.search(
            rf"(?:不喜欢|不爱吃|不想吃|不想去|不要|避免|忌口|过敏)[^。！？,，；;]{{0,12}}(?:{escaped})",
            text,
        )
        is not None
    )


def _extract_date_notes(text: str) -> tuple[list[str], list[str]]:
    notes: list[str] = []
    constraints: list[str] = []
    for marker in ("注意", "希望", "最好"):
        match = re.search(rf"{marker}[^，。；;]{{1,40}}", text)
        if match:
            notes.append(match.group(0).strip())
    for marker in ("不要", "避免", "不能", "不想"):
        match = re.search(rf"{marker}[^，。；;]{{1,40}}", text)
        if match and not _is_operation_only_constraint(text, match.group(0)):
            constraints.append(match.group(0).strip())
    return _unique(notes)[:8], _unique(constraints)[:8]


def _is_operation_only_constraint(text: str, candidate: str) -> bool:
    clause = _containing_clause(text, candidate)
    has_edit = re.search(
        r"(?:换(?:一个|个|成|为)?|替换|更换|删掉|删除|移除)",
        clause,
    ) is not None
    has_plan_reference = re.search(
        r"(?:这个|那个|该|当前|原来|之前)?(?:地方|地点|节点|景点|餐厅)|"
        r"第\s*[一二三四五六七八九十0-9]+\s*个",
        clause,
    ) is not None
    generic_removal = re.search(r"不想\s*(?:去|要)(?:了|这个|那个)?", candidate) is not None
    return (has_edit and has_plan_reference) or (
        generic_removal
        and (
            has_plan_reference
            or re.search(r"(?:换(?:一个|个)?|替换|更换)", text) is not None
        )
    )


def _containing_clause(text: str, candidate: str) -> str:
    start = text.find(candidate)
    if start < 0:
        return candidate
    clause_start = max(text.rfind(separator, 0, start) for separator in "，,。；;！？!?\n")
    boundaries = [
        position
        for separator in "，,。；;！？!?\n"
        if (position := text.find(separator, start + len(candidate))) >= 0
    ]
    clause_end = min(boundaries, default=len(text))
    return text[clause_start + 1 : clause_end]


def _primary_and_secondary[LabelT](
    scores: dict[LabelT, float],
    *,
    minimum_secondary_score: float,
    relative_secondary_score: float,
) -> tuple[LabelT, list[LabelT]]:
    ranked = sorted(scores, key=lambda label: scores[label], reverse=True)
    primary = ranked[0]
    primary_score = scores[primary]
    secondary = [
        label
        for label in ranked[1:]
        if scores[label] >= minimum_secondary_score
        and scores[label] >= primary_score * relative_secondary_score
    ]
    return primary, secondary[:2]


def _score_confidence[LabelT](scores: dict[LabelT, float], strength_target: float) -> float:
    ranked = sorted(scores.values(), reverse=True)
    top = ranked[0]
    second = ranked[1] if len(ranked) > 1 else 0
    strength = min(top / strength_target, 1)
    margin = (top - second) / max(top, 1)
    return round(min(0.98, 0.45 + 0.35 * strength + 0.2 * margin), 4)


def _top_margin[LabelT](scores: dict[LabelT, float]) -> float:
    ranked = sorted(scores.values(), reverse=True)
    if len(ranked) < 2:
        return 1
    return (ranked[0] - ranked[1]) / max(ranked[0], 1)


def _merge_date_slots(rules: DatePlanSlots, llm: DatePlanSlots) -> DatePlanSlots:
    end_date = llm.end_date
    if end_date is None and llm.date is None and llm.day_count is None:
        end_date = rules.end_date
    target_day = llm.target_day
    if target_day is None and llm.date is None and llm.end_date is None and llm.day_count is None:
        target_day = rules.target_day
    merged = DatePlanSlots(
        city=llm.city or rules.city,
        area=llm.area or rules.area,
        plan_mode=llm.plan_mode or rules.plan_mode,
        date=llm.date or rules.date,
        end_date=end_date,
        day_count=llm.day_count or rules.day_count,
        nights=llm.nights if llm.nights is not None else rules.nights,
        target_day=target_day,
        start_time=llm.start_time or rules.start_time,
        budget=llm.budget or rules.budget,
        budget_scope=llm.budget_scope or rules.budget_scope,
        preferences=_unique([*llm.preferences, *rules.preferences]),
        dining_keywords=_unique([*llm.dining_keywords, *rules.dining_keywords]),
        meal_keywords=_merge_meal_keywords(rules.meal_keywords, llm.meal_keywords),
        activity_keywords=_unique([*llm.activity_keywords, *rules.activity_keywords]),
        schedule_hints=_unique([*llm.schedule_hints, *rules.schedule_hints])[:8],
        replace_place_names=_unique([*llm.replace_place_names, *rules.replace_place_names])[:8],
        excluded_keywords=_unique([*llm.excluded_keywords, *rules.excluded_keywords]),
        transport_mode=llm.transport_mode or rules.transport_mode,
        notes=_unique([*llm.notes, *rules.notes])[:8],
        constraints=_unique([*llm.constraints, *rules.constraints])[:8],
        lodging_notes=_unique([*llm.lodging_notes, *rules.lodging_notes])[:8],
    )
    return _normalize_date_plan_slots(merged)


def _normalize_date_plan_slots(slots: DatePlanSlots) -> DatePlanSlots:
    start_date = slots.date
    end_date = slots.end_date
    day_count = slots.day_count
    target_day = slots.target_day

    if start_date is not None and end_date is not None:
        if end_date < start_date:
            end_date = None
        else:
            day_count = min((end_date - start_date).days + 1, MAX_TRIP_DAYS)
            end_date = start_date + timedelta(days=day_count - 1)
    elif start_date is not None and day_count is not None and day_count > 1:
        end_date = start_date + timedelta(days=day_count - 1)

    plan_mode = slots.plan_mode
    if (day_count or 0) > 1 or end_date is not None or (target_day or 0) > 1:
        plan_mode = DatePlanMode.MULTI_DAY
    elif plan_mode is None and start_date is not None:
        plan_mode = DatePlanMode.SINGLE_DAY

    nights = slots.nights
    if day_count is not None:
        nights = min(
            nights if nights is not None else max(day_count - 1, 0),
            MAX_TRIP_DAYS - 1,
        )
    return slots.model_copy(
        update={
            "plan_mode": plan_mode,
            "end_date": end_date,
            "day_count": day_count,
            "nights": nights,
        }
    )


def _merge_meal_keywords(
    first: dict[str, list[str]],
    second: dict[str, list[str]],
) -> dict[str, list[str]]:
    merged = {meal_type: _unique(values) for meal_type, values in first.items()}
    reassigned = {keyword for values in second.values() for keyword in values}
    if reassigned:
        merged = {
            meal_type: [keyword for keyword in values if keyword not in reassigned]
            for meal_type, values in merged.items()
        }
    for meal_type, values in second.items():
        merged[meal_type] = _unique([*merged.get(meal_type, []), *values])
    return {meal_type: values for meal_type, values in merged.items() if values}


def _without_primary[LabelT](values: Iterable[LabelT], primary: LabelT | None) -> list[LabelT]:
    return _unique(value for value in values if value != primary)


def _unique[ValueT](values: Iterable[ValueT]) -> list[ValueT]:
    return list(dict.fromkeys(values))


def _rounded_scores[LabelT](scores: dict[LabelT, float]) -> dict[LabelT, float]:
    return {label: round(score, 3) for label, score in scores.items()}


_DEFAULT_GOAL_BY_SCENARIO = {
    AdviceScenario.PURSUIT: AdviceGoal.INITIATE,
    AdviceScenario.CONFLICT: AdviceGoal.REPAIR,
    AdviceScenario.CHAT_ANALYSIS: AdviceGoal.UNDERSTAND,
    AdviceScenario.RELATIONSHIP_MAINTENANCE: AdviceGoal.COMMUNICATE,
    AdviceScenario.BOUNDARY: AdviceGoal.SET_BOUNDARY,
    AdviceScenario.BREAKUP: AdviceGoal.REPAIR,
}


_EXPLICIT_DATE_PLANNING_MARKERS = (
    "帮我安排",
    "请帮我安排",
    "帮我规划",
    "请帮我规划",
    "安排一下行程",
    "准备一份日程",
    "准备日程",
    "日程安排",
    "日程规划",
    "约会日程",
    "当天安排",
    "一天安排",
    "当天行程",
    "行程安排",
    "安排到行程",
    "规划一下",
    "规划一份",
    "制定行程",
    "生成行程",
    "生成计划",
    "约会安排",
    "约会计划",
    "约会地点",
    "约会攻略",
    "旅行计划",
    "旅游攻略",
    "旅行攻略",
    "旅游行程",
    "推荐餐厅",
    "餐厅推荐",
    "推荐地点",
    "推荐电影院",
    "推荐景点",
    "找一家餐厅",
    "找个餐厅",
    "去哪约会",
    "约会去哪",
    "给我一个约会安排",
)

_DATE_ACTION_MARKERS = (
    "约她",
    "约他",
    "约出来",
    "约会",
    "见面",
    "看电影",
    "一起吃",
    "吃饭",
    "逛街",
    "一起出去",
    "出去玩",
)

_DATE_RELATIONSHIP_MARKERS = ("她", "他", "对象", "女朋友", "男朋友", "喜欢的人")

_DATE_EVALUATION_MARKERS = (
    "你看怎么样",
    "你觉得怎么样",
    "你觉得呢",
    "这样可以吗",
    "这样合适吗",
    "合适吗",
    "适合吗",
    "行不行",
    "好不好",
    "可行吗",
    "你怎么看",
    "该不该",
    "要不要",
)

_CONCRETE_DATE_PLANNING_MARKERS = (
    "推荐",
    "地点",
    "餐厅",
    "电影院",
    "景点",
    "城市",
    "预算",
    "日期",
    "时间",
    "行程",
    "路线",
    "高德",
    "旅游",
    "旅行",
    "几天",
    "天游",
    "到周",
)


_DATE_PLANNING_OPERATION_PATTERN = re.compile(r"安排|规划|推荐|生成|制定|找")
_DATE_PLANNING_TARGET_PATTERN = re.compile(
    r"约会|行程|日程|餐厅|饭店|地点|场所|景点|电影院|影院|路线|活动|旅游|旅行|旅程"
)
_DATE_PLACE_TARGET_PATTERN = re.compile(
    r"餐厅|饭店|餐馆|菜馆|咖啡馆|咖啡店|地点|场所|景点|博物馆|美术馆|展览|"
    r"电影院|影院|公园|商场"
)
_DATE_EXECUTION_TARGET_PATTERN = re.compile(
    r"约会|行程|日程|路线|攻略|餐厅|饭店|餐馆|菜馆|咖啡馆|地点|场所|景点|博物馆|"
    r"美术馆|电影院|影院|活动|旅游|旅行|旅程"
)
_DATE_CATEGORY_TARGET_PATTERN = re.compile(
    r"菜系|菜品类型|餐饮类型|口味|料理类型|吃什么|吃啥|什么菜|"
    r"哪种菜|哪类菜|活动类型|活动项目|什么活动|哪种活动|"
    r"(?:什么|哪种|哪类).{0,4}活动"
)
_DIRECT_AGENT_REQUEST_PREFIX = re.compile(
    r"(?:请|麻烦|帮我|请你|你.{0,4}(?:帮|安排|规划|推荐|找)|"
    r"能帮我|能不能|能否|可以(?:帮我)?|给我|我.{0,6}(?:想要|需要|希望你|想让你))"
)
_THIRD_PARTY_ACTION_PREFIX = re.compile(
    r"(?:她|他|对方|朋友|同事|同学|对象|伴侣|别人|家人|店员|服务员).{0,16}$"
)
_COMITATIVE_RELATIONSHIP_PREFIX = re.compile(
    r"(?:和|跟|与|陪)(?:她|他|对方|对象|伴侣)(?:一起)?(?:想)?$"
)
_AGENT_DIRECTIVE_PREFIX = re.compile(
    r"(?:请|麻烦|帮我|请你|能帮我|能不能|能否|可以帮我|希望你|想让你).{0,8}$"
)
_ORDERED_COMPOUND_PATTERN = re.compile(
    r"(?:^|[，,。；;：:])"
    r"(?:(?:请(?:你)?|麻烦(?:你)?|帮我|我想|我希望(?:你)?|能否|可以)\s*){0,2}"
    r"(?:先|首先)\s*(?:(?:请(?:你)?|麻烦(?:你)?|帮我)\s*)?.{0,56}(?:再|然后|之后)"
)
_ORDERED_RELATIONSHIP_FIRST_PATTERN = re.compile(
    r"(?:^|[，,。；;：:])"
    r"(?:(?:请(?:你)?|麻烦(?:你)?|帮我|我想|我希望(?:你)?|能否|可以)\s*){0,2}"
    r"(?:先|首先)\s*(?:(?:请(?:你)?|麻烦(?:你)?|帮我)\s*)?"
    r"(?:分析|判断|看看|回答|解释|解决).{0,32}(?:再|然后|之后|同时|另外|顺便)"
)


_TASK_PATTERNS: dict[TaskType, tuple[tuple[str, float], ...]] = {
    TaskType.GENERAL_CHAT: (),
    TaskType.OUT_OF_SCOPE: (
        ("python", 5),
        ("代码", 4),
        ("编程", 5),
        ("爬虫", 5),
        ("算法", 4),
        ("新闻稿", 5),
        ("论文", 4),
        ("作业", 4),
        ("翻译", 4),
        ("ppt", 4),
        ("胸痛", 5),
        ("诊断", 4),
        ("法律", 5),
        ("合同", 3),
        ("律师", 4),
        ("excel", 5),
        ("表格公式", 5),
        ("简历", 5),
        ("求职邮件", 5),
        ("登录系统", 5),
        ("接口", 3),
        ("bug", 4),
    ),
    TaskType.RELATIONSHIP_ADVICE: (
        ("喜欢", 3),
        ("女朋友", 3),
        ("男朋友", 3),
        ("对象", 2.5),
        ("她", 1),
        ("他", 1),
        ("关系", 2),
        ("聊天", 1.5),
        ("回复", 1.5),
        ("不理我", 3.5),
        ("不回", 2.5),
        ("冷淡", 3),
        ("拒绝", 3),
        ("沟通", 3),
        ("怎么相处", 4),
        ("表白", 3),
        ("吵架", 4),
        ("冷战", 4),
        ("分手", 4),
        ("复合", 4),
        ("追求", 4),
        ("搭讪", 2.5),
        ("搭话", 2.5),
        ("接触很少", 3),
        ("接触不多", 3),
        ("不太熟", 2.5),
        ("不怎么和", 2.5),
        ("怎么接近", 4),
        ("聊什么", 2),
        ("第一次聊天", 3),
        ("冷静几天", 3),
        ("尊重她的边界", 4),
        ("不想见", 3),
        ("不愿意见", 3),
        ("说明什么", 2),
        ("意味着什么", 2),
        ("代表什么", 2),
        ("句号", 2),
        ("下一条消息", 3),
        ("怎么进一步", 4),
        ("怎么办", 1),
    ),
    TaskType.DATE_PLANNING: (
        ("安排约会", 6),
        ("约会安排", 6),
        ("约会计划", 6),
        ("约会地点", 5),
        ("约会去哪", 5),
        ("去哪约会", 5),
        ("推荐餐厅", 5),
        ("餐厅推荐", 5),
        ("旅游行程", 6),
        ("旅行计划", 6),
        ("旅游攻略", 5),
        ("旅行攻略", 5),
        ("约会", 2),
        ("预算", 2.5),
        ("高德", 4),
    ),
}


_CONFLICT_EVENT_REGEX = r"(?:大)?吵(?:了|过)?一架"


_TASK_REGEX_PATTERNS: dict[TaskType, tuple[tuple[str, float], ...]] = {
    TaskType.OUT_OF_SCOPE: (
        (r"(?:帮|请|写|分析|解释).{0,12}(?:代码|python|爬虫|算法|论文|作业|新闻稿|ppt)", 4),
        (r"(?:帮|请|写|做|分析|修复).{0,16}(?:excel|公式|简历|求职邮件|登录系统|接口|bug)", 4),
        (r"(?:诊断|判断).{0,12}(?:胸痛|疾病|病情)", 4),
        (r"(?:分析|咨询).{0,12}(?:法律|合同|起诉)", 4),
    ),
    TaskType.RELATIONSHIP_ADVICE: (
        (_CONFLICT_EVENT_REGEX, 4),
        (r"(?:先|首先).{0,12}(?:分析|判断|看看).{0,12}(?:她|他|关系|冷淡|回复)", 4),
        (r"(?:分析|判断|看看).{0,12}(?:她|他|对方).{0,8}(?:态度|想法|意思|反应)", 5),
        (r"(?:怎么|如何|怎样).{0,12}(?:追|接近|搭话|搭讪|开口|聊天|发展)", 5),
        (r"(?:接触|认识).{0,8}(?:很少|不多|机会少|不太熟|不怎么熟)", 4),
        (r"(?:创造|寻找|找).{0,10}(?:聊天|搭话|搭讪).{0,8}(?:机会|场景|切入点)", 5),
        (
            r"(?:怎么|如何|怎样).{0,14}(?:展开|延续|继续|开启|接着|顺着)"
            r".{0,6}(?:话题|聊天|交流|聊|说)",
            5,
        ),
        (r"(?:怎么|如何|怎样).{0,10}(?:回复|回应|接话)", 5),
        (r"(?:冷静几天|尊重.{0,6}边界|不想见(?:我)?|不愿意见(?:我)?)", 4),
        (r"(?:说明什么|意味着什么|代表什么|为什么).{0,10}(?:冷淡|回复|不回|不理)?", 2),
    ),
    TaskType.DATE_PLANNING: (
        (r"(?:推荐|找).{0,10}(?:餐厅|地点|场所)", 5),
        (r"(?:约会).{0,10}(?:安排|计划|推荐)", 4),
    ),
}


_SCENARIO_PATTERNS: dict[AdviceScenario, tuple[tuple[str, float], ...]] = {
    AdviceScenario.PURSUIT: (
        ("怎么追", 5),
        ("追求", 4.5),
        ("喜欢", 3),
        ("表白", 4),
        ("暧昧", 3.5),
        ("搭讪", 3),
        ("搭话", 3),
        ("搭话机会", 5),
        ("搭讪机会", 5),
        ("聊天机会", 4),
        ("接触很少", 4),
        ("接触不多", 4),
        ("不太熟", 3.5),
        ("不怎么和", 3.5),
        ("第一次聊天", 4),
        ("第一次找她聊天", 5),
        ("聊什么", 3),
        ("靠近", 1.5),
        ("进一步发展", 5),
        ("深一步发展", 5),
        ("约她", 3.5),
        ("约他", 3.5),
    ),
    AdviceScenario.CONFLICT: (
        ("吵架", 4.5),
        ("吵完架", 4.5),
        ("争吵", 4),
        ("冷战", 4.5),
        ("争执", 4),
        ("矛盾", 3.5),
        ("冲突", 3.5),
        ("道歉", 3),
        ("闹别扭", 3.5),
    ),
    AdviceScenario.CHAT_ANALYSIS: (
        ("已读不回", 5),
        ("不回我", 4),
        ("不理我", 4),
        ("只回复", 5),
        ("只回", 4),
        ("回复很短", 5),
        ("回复很慢", 5),
        ("冷淡", 4),
        ("句号", 5),
        ("怎么回复", 4.5),
        ("怎么回", 4),
        ("聊天记录", 4),
        ("搭讪聊天", 2.5),
        ("闲聊", 2.5),
        ("消息", 2),
        ("回复", 2),
        ("聊天", 1),
    ),
    AdviceScenario.RELATIONSHIP_MAINTENANCE: (
        ("异地", 3.5),
        ("信任", 3.5),
        ("相处", 2.5),
        ("陪伴", 2.5),
        ("恋爱", 1.5),
        ("纪念日", 3),
        ("长期关系", 3),
    ),
    AdviceScenario.BOUNDARY: (
        ("明确拒绝", 5),
        ("不要联系", 5),
        ("停止联系", 4.5),
        ("不想见", 5),
        ("不愿意见", 5),
        ("冷静几天", 5),
        ("尊重她的边界", 5),
        ("尊重边界", 5),
        ("边界", 4),
        ("不舒服", 2),
        ("拒绝", 2.5),
    ),
    AdviceScenario.BREAKUP: (
        ("分手", 5),
        ("复合", 5),
        ("失恋", 4.5),
        ("前任", 3),
        ("挽回", 4),
    ),
}


_SCENARIO_REGEX_PATTERNS: dict[AdviceScenario, tuple[tuple[str, float], ...]] = {
    AdviceScenario.PURSUIT: (
        (r"(?:和|跟).{0,5}(?:不太|不怎么|不够).{0,5}(?:熟|了解)", 4),
        (r"(?:接触|认识).{0,8}(?:很少|不多|机会少)", 4),
        (r"(?:创造|寻找|找).{0,10}(?:聊天|搭话|搭讪).{0,8}(?:机会|场景|切入点)", 5),
        (r"第一次.{0,8}(?:找她|找他|和她|和他)?聊天", 4),
        (
            r"(?:怎么|如何|怎样).{0,14}(?:展开|延续|继续|开启|接着|顺着)"
            r".{0,6}(?:话题|聊天|交流|聊|说)",
            4,
        ),
        (r"(?:怎么|如何|怎样).{0,10}(?:回复|回应|接话)", 4),
    ),
    AdviceScenario.CHAT_ANALYSIS: (
        (r"(?:只|就)?回复.{0,6}(?:几个字|很短|很慢)", 5),
        (r"(?:不回|不理).{0,6}(?:我|消息|回复)?", 4),
        (r"(?:回复|回).{0,5}一个句号", 5),
        (r"(?:为什么|怎么).{0,8}(?:冷淡|不回|不理)", 4),
    ),
    AdviceScenario.BOUNDARY: (
        (r"(?:不想|不愿|不希望).{0,5}(?:见面|见我|联系)", 5),
        (r"冷静.{0,5}(?:几天|一段时间)", 5),
        (r"尊重.{0,8}边界", 5),
    ),
    AdviceScenario.CONFLICT: (
        (_CONFLICT_EVENT_REGEX, 4.5),
        (r"道.{0,2}歉", 3),
        (r"(?:谈|聊|沟通).{0,10}(?:分歧|矛盾|冲突|消费观|争执)", 4),
    ),
}


_CONTEXT_CONTINUITY_SCENARIOS = (
    AdviceScenario.PURSUIT,
    AdviceScenario.BOUNDARY,
    AdviceScenario.CONFLICT,
    AdviceScenario.CHAT_ANALYSIS,
)

_SCENARIO_RESOLUTION_PATTERNS: dict[AdviceScenario, tuple[re.Pattern[str], ...]] = {
    AdviceScenario.CONFLICT: (
        re.compile(r"(?:已经|后来|现在|最终).{0,10}(?:和好|说开|解决|达成一致|不吵了)"),
        re.compile(r"(?:冲突|矛盾|争执|这件事).{0,8}(?:解决|结束|说开)"),
    ),
    AdviceScenario.CHAT_ANALYSIS: (
        re.compile(r"(?:现在|后来|最近).{0,8}(?:回复正常|聊天正常|聊开了)"),
    ),
}


_GOAL_PATTERNS: dict[AdviceGoal, tuple[tuple[str, float], ...]] = {
    AdviceGoal.INITIATE: (
        ("怎么认识", 4),
        ("怎么开口", 4),
        ("怎么搭讪", 4),
        ("如何搭讪", 4),
        ("怎么追", 4),
        ("怎么接近", 4),
        ("搭话机会", 5),
        ("搭讪机会", 5),
        ("聊天机会", 4),
        ("第一次聊天", 4),
        ("聊什么", 3),
        ("想搭讪", 3.5),
        ("开启聊天", 3.5),
    ),
    AdviceGoal.UNDERSTAND: (
        ("什么意思", 4),
        ("说明什么", 4),
        ("意味着什么", 4),
        ("代表什么", 4),
        ("怎么看", 3),
        ("是不是", 2.5),
        ("判断", 3),
        ("分析", 3),
        ("往好的方向", 4),
        ("只回复几个字", 3),
        ("还要继续吗", 3),
        ("要不要继续", 3),
        ("还是不回", 3),
        ("为什么", 2),
    ),
    AdviceGoal.PROGRESS: (
        ("进一步", 4.5),
        ("推进", 4),
        ("深入发展", 5),
        ("深一步发展", 5),
        ("什么时候适合", 4),
        ("怎么约", 4),
        ("约她单独", 4),
        ("约他单独", 4),
        ("表白", 3.5),
        ("约她", 3),
        ("约他", 3),
    ),
    AdviceGoal.REPAIR: (
        ("修复", 4),
        ("挽回", 4),
        ("和好", 4),
        ("复合", 5),
        ("想复合", 5),
        ("道歉", 3.5),
        ("弥补", 3.5),
    ),
    AdviceGoal.COMMUNICATE: (
        ("沟通", 4),
        ("发消息", 4),
        ("下一条消息", 4),
        ("解释", 4),
        ("沟通清楚", 4),
        ("说什么", 3),
        ("怎么说", 4),
        ("怎么回复", 4),
        ("怎么回", 3.5),
        ("表达", 3),
    ),
    AdviceGoal.SET_BOUNDARY: (
        ("设定边界", 5),
        ("明确边界", 5),
        ("尊重她", 4),
        ("尊重边界", 5),
        ("冷静几天", 5),
        ("不想见", 4),
        ("不愿意见", 4),
        ("继续约", 4),
        ("拒绝", 2.5),
        ("停止联系", 4),
    ),
    AdviceGoal.END_RELATIONSHIP: (
        ("结束关系", 5),
        ("怎么分手", 5),
        ("想分手", 4.5),
        ("离开这段关系", 4.5),
    ),
}


_GOAL_REGEX_PATTERNS: dict[AdviceGoal, tuple[tuple[str, float], ...]] = {
    AdviceGoal.INITIATE: (
        (r"(?:怎么|如何|怎样).{0,10}(?:搭话|搭讪|开口|开始交流)", 4.5),
        (r"(?:创造|寻找|找).{0,8}(?:聊天|搭话|搭讪|接触).{0,5}(?:机会|场景)", 5),
        (r"(?:聊天|搭话|搭讪|接触).{0,5}(?:机会|场景|切入点)", 4),
        (r"(?:自然|主动).{0,6}(?:开始|开启).{0,5}(?:聊天|交流|话题)", 4),
        (r"(?:不怎么|不太|不够).{0,4}(?:和|跟).{0,4}(?:熟|了解)", 3.5),
        (r"(?:接触|认识).{0,4}(?:很少|不多|机会少)", 3.5),
        (r"第一次.{0,8}(?:找她|找他|和她|和他)?聊天.{0,8}(?:聊什么|说什么)?", 4),
    ),
    AdviceGoal.UNDERSTAND: (
        (r"(?:说明|意味着|代表).{0,4}什么", 4),
        (r"(?:为什么|为何).{0,10}(?:冷淡|不回|不理|回复)", 4),
    ),
    AdviceGoal.PROGRESS: (
        (r"(?:什么时候|何时).{0,6}(?:约|见面)", 4),
        (r"怎么.{0,4}约(?:她|他)?", 4),
    ),
    AdviceGoal.REPAIR: (
        (r"(?:想|希望|准备)?复合", 5),
        (r"重新.{0,8}(?:沟通|开始|在一起)", 4),
    ),
    AdviceGoal.COMMUNICATE: (
        (r"(?:下一条|下一次).{0,6}(?:消息|怎么发|怎么说)", 4),
        (r"(?:怎么|如何).{0,6}(?:发消息|解释|表达)", 4),
        (
            r"(?:怎么|如何|怎样).{0,14}(?:展开|延续|继续|开启|接着)"
            r".{0,6}(?:话题|聊天|交流|聊|说)",
            4,
        ),
        (r"(?:怎么|如何|怎样).{0,10}(?:回复|回应|接话)", 4),
    ),
    AdviceGoal.SET_BOUNDARY: (
        (r"(?:尊重|保护).{0,8}边界", 5),
        (r"(?:冷静|暂停).{0,5}(?:几天|联系|聊天)", 4),
        (r"(?:不想|不愿).{0,5}(?:见面|见我|联系)", 4),
    ),
}


_CASUAL_EXACT = {
    "你好",
    "您好",
    "嗨",
    "哈喽",
    "hello",
    "hi",
    "在吗",
    "谢谢",
    "谢谢你",
    "好的谢谢",
    "好的谢谢你",
    "谢谢先这样",
    "谢谢先到这里",
    "好的先这样",
    "先这样吧",
    "先这样谢谢",
    "早上好",
    "上午好",
    "中午好",
    "下午好",
    "晚上好",
    "早安",
    "午安",
    "明白了",
    "知道了",
    "再见",
    "拜拜",
    "晚安",
}


_DATE_PREFERENCES = (
    "安静",
    "咖啡",
    "展览",
    "散步",
    "自然",
    "拍照",
    "手工",
    "演出",
    "互动",
    "氛围",
    "轻松",
    "性价比",
    "电影",
    "话剧",
    "音乐",
    "美食",
)


_DATE_KEYWORD_ALIASES = {
    "电影院": ("电影院", "电影", "影院"),
    "景点": ("景点", "旅游景点", "美术馆", "博物馆"),
    "博物馆": ("博物馆", "美术馆"),
    "美术馆": ("美术馆", "博物馆"),
    "公园": ("公园",),
    "剧场": ("剧场", "演出"),
    "西餐": ("西餐", "西餐厅"),
    "日料": ("日料", "日本料理"),
    "火锅": ("火锅",),
    "烧烤": ("烧烤",),
    "素食": ("素食", "素菜"),
}

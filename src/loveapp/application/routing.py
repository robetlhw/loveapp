import re
import unicodedata
from collections.abc import Iterable
from datetime import date, datetime, timedelta

from loveapp.domain.date_plan import MAX_TRIP_DAYS
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.enums import (
    AdviceGoal,
    AdviceScenario,
    BudgetScope,
    DatePlanMode,
    DatePlanMutation,
    DateRequestMode,
    DateTaskIntent,
    RiskLevel,
    RouteSource,
    TaskType,
    TransportMode,
)
from loveapp.domain.memory import MessageRole
from loveapp.domain.routing import DatePlanSlots, RouteCorrection, RouteInput, RouteResult
from loveapp.ports.routing import RouteCorrector
from loveapp.safety import SafetyPolicy


class HybridRouter:
    def __init__(
        self,
        safety_policy: SafetyPolicy,
        corrector: RouteCorrector | None = None,
        *,
        confidence_threshold: float = 0.72,
        ambiguity_margin: float = 0.16,
    ) -> None:
        self._safety_policy = safety_policy
        self._corrector = corrector
        self._confidence_threshold = confidence_threshold
        self._ambiguity_margin = ambiguity_margin

    async def route(self, route_input: RouteInput) -> RouteResult:
        normalized = normalize_route_text(route_input.latest_query)
        safety = self._safety_policy.assess(normalized)
        result = route_by_rules(route_input, normalized)
        result = result.model_copy(
            update={
                "risk_level": safety.risk_level,
                "risk_reasons": safety.reasons,
            }
        )
        if safety.risk_level == RiskLevel.HIGH or self._corrector is None:
            return result
        # Exact casual messages are a deterministic fast path.  This guard is
        # intentionally after the safety scan so a safety rule always wins.
        if _is_exact_casual_chat(normalized):
            return result
        if not self._needs_llm_correction(route_input, result):
            return result

        try:
            correction = await self._corrector.correct(route_input, result)
        except Exception as exc:
            return result.model_copy(
                update={
                    "llm_error": str(exc)[:300],
                    "needs_clarification": result.needs_clarification,
                }
            )
        return merge_route_correction(
            route_input,
            result,
            correction,
            allow_task_override=self._allow_task_override(route_input, result, correction),
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
            and _is_obvious_date_supplement(result.normalized_query)
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
        if (
            route_input.date_task_state is None
            and route_input.active_task == TaskType.DATE_PLANNING
            and _is_obvious_date_supplement(result.normalized_query)
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


def normalize_route_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


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
    date_request_mode = _infer_date_request_mode(route_input, text, latest_date_slots)
    executable_date_request = _is_executable_date_mode(date_request_mode)

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
        if not _has_ordered_primary_relationship_request(text):
            task_scores[TaskType.DATE_PLANNING] = task_scores.get(TaskType.DATE_PLANNING, 0) + 4
            task_evidence.setdefault(TaskType.DATE_PLANNING, []).append("明确约会规划请求")
    elif _looks_like_date_candidate(text, None):
        # This is only a semantic-review candidate. Do not put it into the
        # task score, otherwise a weak candidate can become the primary task.
        pass
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
        if relationship_follow_up or historical_relationship_report:
            scenario_scores[AdviceScenario.PURSUIT] = (
                scenario_scores.get(AdviceScenario.PURSUIT, 0) + 4
            )
            scenario_evidence.setdefault(AdviceScenario.PURSUIT, []).append("关系互动证据")
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
            goal_scores[AdviceGoal.UNDERSTAND] = goal_scores.get(AdviceGoal.UNDERSTAND, 0) + 3
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
        date_request_mode=date_request_mode,
        date_intent=date_intent,
        date_mutation=date_mutation,
        date_missing_fields=(
            _date_missing_fields(date_slots) if task_type == TaskType.DATE_PLANNING else []
        ),
        source=RouteSource.RULES,
        needs_clarification=task_confidence < 0.58,
        evidence_spans=evidence,
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
    secondary_tasks = _without_primary(
        [*correction.secondary_tasks, *rules.secondary_tasks],
        task_type,
    )[:2]
    date_plan = _merge_date_slots(rules.date_plan, correction.date_plan)
    if task_type != TaskType.DATE_PLANNING and TaskType.DATE_PLANNING not in secondary_tasks:
        date_plan = DatePlanSlots()
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
            "task_guard_applied": correction.task_type != task_type,
            "primary_goal": primary_goal,
            "secondary_goals": secondary_goals,
            "primary_scenario": primary_scenario,
            "secondary_scenarios": secondary_scenarios,
            "scenario_confidence": scenario_confidence,
            "date_plan": date_plan,
            "date_request_mode": date_request_mode,
            "date_intent": date_intent,
            "date_mutation": date_mutation,
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
    replace_place_names = _extract_replace_place_names(normalized_texts[0])
    place_search_text = normalized_texts[0]
    for place_name in replace_place_names:
        place_search_text = place_search_text.replace(place_name, " ")
    dining_keywords, activity_keywords, excluded_keywords = _extract_place_keywords(
        place_search_text
    )
    excluded_keywords = _unique([*excluded_keywords, *replace_place_names])
    meal_keywords, schedule_hints = _extract_schedule_slots(normalized_texts[0])

    city = next(
        (city_name for text in normalized_texts for city_name in _CITY_NAMES if city_name in text),
        None,
    )
    if city is None:
        city_match = re.search(r"(?:城市|地点)\s*(?:是|:)?\s*([\u4e00-\u9fff]{2,6})", combined)
        city = city_match.group(1) if city_match else None

    area_match = (
        re.search(
            rf"{re.escape(city)}(?:市)?([\u4e00-\u9fff]{{2,6}}(?:区|县|商圈))",
            combined,
        )
        if city
        else None
    )
    if area_match is None:
        area_match = re.search(
            r"(?:区域|商圈|地点)\s*(?:是|:)?\s*([\u4e00-\u9fff]{2,8}(?:区|县|商圈))",
            combined,
        )
    area = area_match.group(1) if area_match else None

    daily_budget_match = re.search(
        r"(?:(?:每天|每日|一天)\s*(?:预算|控制在)?|"
        r"预算\s*(?:是|为)?\s*(?:每天|每日|一天))\s*"
        r"(?:还是|仍然是|依然是|仍是|是|为|:)?\s*(\d{2,6})\s*(?:元|块)?",
        combined,
    )
    budget_match = re.search(
        r"(?:预算|总共|总价|控制在)\s*(?:是|:)?\s*(\d{2,6})\s*(?:元|块)?",
        combined,
    )
    if daily_budget_match is not None:
        budget_match = daily_budget_match
    elif budget_match is None:
        budget_match = re.search(r"(\d{2,6})\s*(?:元|块)(?:以内|左右)?", combined)
    budget = int(budget_match.group(1)) if budget_match else None
    budget_scope = BudgetScope.PER_DAY if daily_budget_match is not None else None
    if budget is not None and budget_scope is None:
        budget_scope = BudgetScope.TOTAL

    preferences = [value for value in _DATE_PREFERENCES if value in combined]
    transport_mode = next(
        (
            mode
            for mode, keywords in _TRANSPORT_KEYWORDS.items()
            if any(keyword in combined for keyword in keywords)
        ),
        None,
    )
    planned_date, end_date, day_count, nights, plan_mode = _extract_date_window(combined)
    target_day = _extract_target_day(normalized_texts[0])
    if target_day is not None:
        plan_mode = DatePlanMode.MULTI_DAY if target_day > 1 else plan_mode
    lodging_notes = _extract_lodging_notes(normalized_texts[0])
    start_time = _extract_start_time(combined, planned_date)
    notes, constraints = _extract_date_notes(combined)
    requested_days = _extract_requested_day_count(
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
        budget=budget,
        budget_scope=budget_scope,
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
    has_request = (
        re.search(
            r"推荐|建议|选(?:择)?|哪(?:种|类)|什么|吃啥|吃什么|适合",
            clause,
        )
        is not None
    )
    asks_for_concrete_place = (
        _DATE_PLACE_TARGET_PATTERN.search(clause) is not None
        and re.search(r"哪家|哪里|附近|具体|一家|一个|搜索|查找", clause) is not None
    )
    return has_category and has_request and not asks_for_concrete_place


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


def _looks_like_place_search_request(clause: str) -> bool:
    return (
        _DATE_PLACE_TARGET_PATTERN.search(clause) is not None
        and re.search(r"推荐|找|搜索|查|哪家|哪里|附近|具体|一家|一个", clause) is not None
    )


def _looks_like_itinerary_request(text: str) -> bool:
    has_plan_target = (
        re.search(
            r"约会.{0,8}(?:安排|计划|规划|攻略)|"
            r"(?:安排|计划|规划|制定|生成).{0,8}(?:约会|行程|路线|旅行|旅游|旅程)|"
            r"(?:一份|一个).{0,6}(?:约会计划|约会安排|行程|路线|攻略|旅行计划)",
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
    return (
        re.search(
            r"^(?:先|首先).{0,24}(?:分析|判断|看看|回答|解释|解决).{0,24}"
            r"(?:再|然后|同时|另外|顺便)",
            text,
        )
        is not None
    )


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
    if state is None or not state.is_resumable or _is_exact_casual_chat(text):
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
            "火锅",
            "日期",
            "时间",
            "每天",
            "多日",
            "多天",
            "酒店",
            "住宿",
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
    return (
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


def _extract_date_window(
    text: str,
) -> tuple[date | None, date | None, int | None, int | None, DatePlanMode | None]:
    start_date, end_date = _extract_explicit_date_range(text)
    duration_text = re.sub(
        r"第\s*(?:[0-9]{1,2}|[一二三四五六七八九十两]{1,3})\s*天",
        "",
        text,
    )
    requested_days = _extract_requested_day_count(duration_text)
    requested_nights = _extract_requested_nights(text)

    if start_date is None:
        start_date = _extract_date(text)

    if start_date is not None and end_date is not None:
        day_count = min((end_date - start_date).days + 1, MAX_TRIP_DAYS)
        end_date = start_date + timedelta(days=day_count - 1)
    elif requested_days is not None:
        day_count = min(requested_days, MAX_TRIP_DAYS)
        if start_date is not None and day_count > 1:
            end_date = start_date + timedelta(days=day_count - 1)
    elif any(marker in text for marker in ("多日", "多天", "几天", "几日")):
        day_count = None
    elif start_date is not None:
        day_count = 1
    else:
        day_count = None

    if day_count is not None:
        nights = min(
            requested_nights if requested_nights is not None else max(day_count - 1, 0),
            MAX_TRIP_DAYS - 1,
        )
    else:
        nights = requested_nights

    multi_day_signal = (
        (day_count or 0) > 1
        or end_date is not None
        or any(marker in text for marker in ("多日", "多天", "几天", "几日"))
    )
    plan_mode = (
        DatePlanMode.MULTI_DAY
        if multi_day_signal
        else DatePlanMode.SINGLE_DAY
        if start_date is not None or requested_days == 1
        else None
    )
    return start_date, end_date, day_count, nights, plan_mode


def _extract_explicit_date_range(text: str) -> tuple[date | None, date | None]:
    today = date.today()

    relative_match = re.search(
        r"(今天|明天|后天|大后天)\s*(?:到|至|[-~～—])\s*(今天|明天|后天|大后天)",
        text,
    )
    if relative_match:
        offsets = {"今天": 0, "明天": 1, "后天": 2, "大后天": 3}
        start = today + timedelta(days=offsets[relative_match.group(1)])
        end = today + timedelta(days=offsets[relative_match.group(2)])
        return (start, end) if end >= start else (None, None)

    weekday_match = re.search(
        r"(?P<prefix>下周|下星期)?(?:周|星期)(?P<start>[一二三四五六日天])\s*"
        r"(?:到|至|[-~～—])\s*"
        r"(?:(?P<end_prefix>下周|下星期)?(?:周|星期))?"
        r"(?P<end>[一二三四五六日天])",
        text,
    )
    if weekday_match:
        weekday_values = {
            "一": 0,
            "二": 1,
            "三": 2,
            "四": 3,
            "五": 4,
            "六": 5,
            "日": 6,
            "天": 6,
        }
        start_weekday = weekday_values[weekday_match.group("start")]
        end_weekday = weekday_values[weekday_match.group("end")]
        force_next_week = weekday_match.group("prefix") is not None
        start = _next_weekday(today, start_weekday, force_next_week=force_next_week)
        end = start + timedelta(days=(end_weekday - start_weekday) % 7)
        return start, end

    full_dates = [
        _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        for match in re.finditer(
            r"(?<!\d)(20\d{2})[-年](\d{1,2})[-月](\d{1,2})日?(?!\d)",
            text,
        )
    ]
    valid_full_dates = [value for value in full_dates if value is not None]
    if len(valid_full_dates) >= 2:
        start, end = valid_full_dates[:2]
        return (start, end) if end >= start else (None, None)

    shorthand = re.search(
        r"(\d{1,2})月(\d{1,2})日?\s*(?:到|至|[-~～—])\s*(\d{1,2})日",
        text,
    )
    if shorthand:
        start = _safe_date(today.year, int(shorthand.group(1)), int(shorthand.group(2)))
        end = _safe_date(today.year, int(shorthand.group(1)), int(shorthand.group(3)))
        if start is not None and end is not None and end >= start:
            return start, end

    month_dates = [
        _safe_date(today.year, int(match.group(1)), int(match.group(2)))
        for match in re.finditer(r"(?<!\d)(\d{1,2})月(\d{1,2})日?", text)
    ]
    valid_month_dates = [value for value in month_dates if value is not None]
    if len(valid_month_dates) >= 2:
        start, end = valid_month_dates[:2]
        if end < start:
            end = _safe_date(start.year + 1, end.month, end.day) or end
        return start, end
    return None, None


def _next_weekday(today: date, weekday: int, *, force_next_week: bool) -> date:
    if force_next_week:
        return today + timedelta(days=7 - today.weekday() + weekday)
    delta = (weekday - today.weekday()) % 7
    return today + timedelta(days=delta or 7)


def _extract_requested_day_count(text: str) -> int | None:
    match = re.search(
        r"([0-9]{1,2}|[一二三四五六七八九十两]{1,3})\s*"
        r"(?:天(?!后)|日(?=游|行程|旅行|旅游))",
        text,
    )
    return _parse_small_number(match.group(1)) if match else None


def _extract_requested_nights(text: str) -> int | None:
    match = re.search(r"([0-9]{1,2}|[一二三四五六七八九十两]{1,3})\s*(?:夜|晚)", text)
    return _parse_small_number(match.group(1)) if match else None


def _parse_small_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    normalized = value.replace("两", "二")
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if normalized == "十":
        return 10
    if "十" in normalized:
        tens, ones = normalized.split("十", maxsplit=1)
        return (digits.get(tens, 1) * 10) + digits.get(ones, 0)
    return digits.get(normalized)


def _extract_target_day(text: str) -> int | None:
    match = re.search(r"第\s*([0-9]{1,2}|[一二三四五六七八九十两]{1,3})\s*天", text)
    if match:
        value = _parse_small_number(match.group(1))
        return min(value, MAX_TRIP_DAYS) if value else None
    if "首日" in text or "第一日" in text:
        return 1
    return None


def _extract_lodging_notes(text: str) -> list[str]:
    return list(
        dict.fromkeys(
            clause.strip()
            for clause in re.split(r"[，。；;！？!?]+", text)
            if clause.strip()
            and any(marker in clause for marker in ("酒店", "住宿", "民宿", "大床房", "双床房"))
        )
    )[:8]


def _extract_date(text: str) -> date | None:
    iso_match = re.search(r"\b(20\d{2})[-年](\d{1,2})[-月](\d{1,2})日?\b", text)
    if iso_match:
        return _safe_date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))

    month_day = re.search(r"(\d{1,2})月(\d{1,2})日?", text)
    if month_day:
        current = date.today()
        return _safe_date(current.year, int(month_day.group(1)), int(month_day.group(2)))

    today = date.today()
    if "大后天" in text:
        return today + timedelta(days=3)
    if "后天" in text:
        return today + timedelta(days=2)
    if "明天" in text:
        return today + timedelta(days=1)
    if "今天" in text:
        return today
    if "下周" in text:
        weekday = _weekday_in_text(text)
        days_to_next_monday = 7 - today.weekday()
        return today + timedelta(days=days_to_next_monday + (weekday if weekday is not None else 5))
    if "周末" in text:
        return today + timedelta(days=(5 - today.weekday()) % 7)
    weekday = _weekday_in_text(text)
    if weekday is not None:
        delta = (weekday - today.weekday()) % 7
        return today + timedelta(days=delta or 7)
    return None


def _weekday_in_text(text: str) -> int | None:
    match = re.search(r"(?:周|星期)([一二三四五六日天])", text)
    if not match:
        return None
    return {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}[match.group(1)]


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _extract_start_time(text: str, planned_date: date | None) -> datetime | None:
    if planned_date is None:
        return None
    match = re.search(r"(上午|下午|晚上|中午)?\s*(\d{1,2})(?:点|时|:)(\d{1,2})?", text)
    if not match:
        return None
    period, hour_text, minute_text = match.groups()
    hour = int(hour_text)
    minute = int(minute_text or 0)
    if period in {"下午", "晚上"} and hour < 12:
        hour += 12
    if period == "中午" and hour < 11:
        hour += 12
    if hour > 23 or minute > 59:
        return None
    return datetime(planned_date.year, planned_date.month, planned_date.day, hour, minute)


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
        r"([^，,。；;]{2,40}?)(?=\s*(?:，|,|。|；|;|然后|再|换|改|$))",
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
            if value and value not in {"活动", "安排", "景点", "餐厅", "用餐"}:
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
    marker_positions = [
        (meal_type, marker, position)
        for meal_type, markers in meal_markers.items()
        for marker in markers
        for position in _all_positions(text, marker)
    ]
    meal_keywords: dict[str, list[str]] = {}
    for keyword, aliases in cuisine_aliases.items():
        for alias in aliases:
            for position in _all_positions(text, alias):
                nearest = _nearest_meal_marker(text, position, marker_positions)
                if nearest is None:
                    continue
                meal_type, _, _ = nearest
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
        if match:
            constraints.append(match.group(0).strip())
    return _unique(notes)[:8], _unique(constraints)[:8]


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
    r"约会|行程|餐厅|饭店|地点|场所|景点|电影院|影院|路线|活动|旅游|旅行|旅程"
)
_DATE_PLACE_TARGET_PATTERN = re.compile(
    r"餐厅|饭店|餐馆|菜馆|咖啡馆|咖啡店|地点|场所|景点|博物馆|美术馆|展览|"
    r"电影院|影院|公园|商场"
)
_DATE_EXECUTION_TARGET_PATTERN = re.compile(
    r"约会|行程|路线|攻略|餐厅|饭店|餐馆|菜馆|咖啡馆|地点|场所|景点|博物馆|"
    r"美术馆|电影院|影院|活动|旅游|旅行|旅程"
)
_DATE_CATEGORY_TARGET_PATTERN = re.compile(
    r"菜系|菜品类型|餐饮类型|口味|料理类型|吃什么|吃啥|什么菜|"
    r"哪种菜|哪类菜|活动类型|活动项目|什么活动|哪种活动"
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


_TASK_PATTERNS: dict[TaskType, tuple[tuple[str, float], ...]] = {
    TaskType.GENERAL_CHAT: (),
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


_TASK_REGEX_PATTERNS: dict[TaskType, tuple[tuple[str, float], ...]] = {
    TaskType.RELATIONSHIP_ADVICE: (
        (r"(?:先|首先).{0,12}(?:分析|判断|看看).{0,12}(?:她|他|关系|冷淡|回复)", 4),
        (r"(?:怎么|如何|怎样).{0,12}(?:追|接近|搭话|搭讪|开口|聊天|发展)", 5),
        (r"(?:接触|认识).{0,8}(?:很少|不多|机会少|不太熟|不怎么熟)", 4),
        (r"(?:创造|寻找|找).{0,10}(?:聊天|搭话|搭讪).{0,8}(?:机会|场景|切入点)", 5),
        (
            r"(?:怎么|如何|怎样).{0,14}(?:展开|延续|继续|开启|接着)"
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
            r"(?:怎么|如何|怎样).{0,14}(?:展开|延续|继续|开启|接着)"
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
        (r"道.{0,2}歉", 3),
        (r"(?:谈|聊|沟通).{0,10}(?:分歧|矛盾|冲突|消费观|争执)", 4),
    ),
}


_CONTEXT_CONTINUITY_SCENARIOS = (
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


_CITY_NAMES = tuple(
    sorted(
        {
            "北京",
            "上海",
            "广州",
            "深圳",
            "杭州",
            "南京",
            "苏州",
            "成都",
            "重庆",
            "武汉",
            "西安",
            "长沙",
            "天津",
            "青岛",
            "厦门",
            "郑州",
            "济南",
            "合肥",
            "福州",
            "昆明",
            "大连",
            "宁波",
            "无锡",
            "佛山",
            "东莞",
            "珠海",
            "三亚",
            "沈阳",
            "哈尔滨",
            "长春",
            "石家庄",
            "南昌",
            "贵阳",
            "南宁",
            "太原",
            "兰州",
            "乌鲁木齐",
            "呼和浩特",
            "海口",
            "泉州",
            "温州",
            "绍兴",
            "嘉兴",
        },
        key=len,
        reverse=True,
    )
)


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


_TRANSPORT_KEYWORDS = {
    TransportMode.WALKING: ("步行", "走路"),
    TransportMode.TRANSIT: ("公交", "地铁", "公共交通"),
    TransportMode.DRIVING: ("开车", "自驾", "驾车"),
    TransportMode.CYCLING: ("骑车", "骑行", "自行车"),
}

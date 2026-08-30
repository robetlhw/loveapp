import hashlib
import json
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any, Literal

from loveapp.adapters.routing import OpenAICompatibleRouteCorrector
from loveapp.application.conversation_flow import (
    advance_conversation_flow,
    is_pending_continuation,
)
from loveapp.application.routing import HybridRouter, route_by_rules
from loveapp.core.config import Settings
from loveapp.domain.conversation import ConversationFlowState
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.enums import AdviceGoal, AdviceScenario, RiskLevel, TaskType
from loveapp.domain.memory import MessageRole, StoredMessage
from loveapp.domain.routing import (
    DatePlanSlots,
    RecentRiskState,
    RouteCorrection,
    RouteInput,
    RouteResult,
)
from loveapp.ports.routing import RouteCorrector
from loveapp.safety import SafetyPolicy

EvaluationMode = Literal["policy", "live"]


class RecordingRouteCorrector:
    """Deterministic corrector used to measure Router trigger and merge policy."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._next_correction: RouteCorrection | None = None
        self._next_failure: str | None = None

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def last_telemetry(self) -> dict[str, str | int | float | None]:
        return {}

    def prepare(
        self,
        correction: RouteCorrection,
        *,
        failure: str | None = None,
    ) -> None:
        self._next_correction = correction
        self._next_failure = failure

    async def correct(
        self,
        route_input: RouteInput,
        rule_result: RouteResult,
    ) -> RouteCorrection:
        self.calls.append(
            {
                "query": route_input.latest_query,
                "rule_task": rule_result.task_type.value,
                "rule_confidence": rule_result.task_confidence,
            }
        )
        if self._next_failure:
            failure = self._next_failure
            self._next_failure = None
            raise _recorded_failure(failure)
        return self._next_correction or _correction_from_rules(rule_result)

    async def aclose(self) -> None:
        return None


class _CountingRouteCorrector:
    def __init__(self, delegate: RouteCorrector) -> None:
        self.delegate = delegate
        self.call_count = 0

    @property
    def last_telemetry(self) -> dict[str, str | int | float | None]:
        return getattr(self.delegate, "last_telemetry", {})

    async def correct(
        self,
        route_input: RouteInput,
        rule_result: RouteResult,
    ) -> RouteCorrection:
        self.call_count += 1
        return await self.delegate.correct(route_input, rule_result)

    async def aclose(self) -> None:
        await self.delegate.aclose()


async def evaluate_routing_conversations(
    path: Path,
    *,
    input_cost_per_million: float = 0,
    output_cost_per_million: float = 0,
    confidence_threshold: float = 0.72,
    ambiguity_margin: float = 0.16,
    clarification_threshold: float = 0.68,
    safety_context_turns: int = 4,
    prompt_version: str = "routing-v3.0",
    case_id: str | None = None,
    case_ids: Sequence[str] | None = None,
    category: str | None = None,
    categories: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Run deterministic Policy Eval without access to an external corrector."""
    return await _evaluate_routing_conversations(
        path,
        corrector=None,
        evaluation_mode="policy",
        input_cost_per_million=input_cost_per_million,
        output_cost_per_million=output_cost_per_million,
        confidence_threshold=confidence_threshold,
        ambiguity_margin=ambiguity_margin,
        clarification_threshold=clarification_threshold,
        safety_context_turns=safety_context_turns,
        prompt_version=prompt_version,
        case_id=case_id,
        case_ids=case_ids,
        category=category,
        categories=categories,
    )


async def _evaluate_routing_conversations(
    path: Path,
    *,
    corrector: RouteCorrector | None,
    evaluation_mode: EvaluationMode,
    input_cost_per_million: float,
    output_cost_per_million: float,
    confidence_threshold: float,
    ambiguity_margin: float,
    clarification_threshold: float,
    safety_context_turns: int,
    prompt_version: str,
    case_id: str | None = None,
    case_ids: Sequence[str] | None = None,
    category: str | None = None,
    categories: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Shared implementation; live access is restricted to the guarded public entry point.

    Keeping this private prevents library callers from accidentally bypassing the explicit
    environment opt-in required by ``evaluate_live_routing_conversations``.
    """
    if evaluation_mode == "policy" and corrector is not None:
        raise ValueError("Policy Eval 使用 RecordingRouteCorrector，不能传入真实 corrector。")
    if evaluation_mode == "live" and corrector is None:
        raise ValueError("Live Router Eval 必须显式传入 RouteCorrector。")
    if input_cost_per_million < 0 or output_cost_per_million < 0:
        raise ValueError("Token 单价不能为负数。")

    cases = _load_cases(path)
    cases = _filter_cases(
        cases,
        case_id=case_id,
        case_ids=case_ids,
        category=category,
        categories=categories,
    )
    rows: list[dict[str, Any]] = []
    task_counts = {
        task.value: {"true_positive": 0, "false_positive": 0, "false_negative": 0}
        for task in TaskType
    }
    counters: dict[str, int] = {
        "turn_count": 0,
        "passed_turns": 0,
        "task_cases": 0,
        "task_correct": 0,
        "scenario_cases": 0,
        "scenario_correct": 0,
        "secondary_scenario_hits": 0,
        "secondary_scenario_expected": 0,
        "risk_cases": 0,
        "risk_correct": 0,
        "high_risk_cases": 0,
        "high_risk_true_positive": 0,
        "goal_true_positive": 0,
        "goal_false_positive": 0,
        "goal_false_negative": 0,
        "goal_annotation_turn_count": 0,
        "llm_calls": 0,
        "router_correction_calls": 0,
        "never_policy_violations": 0,
        "required_policy_misses": 0,
        "context_turn_count": 0,
        "context_route_cases": 0,
        "context_route_correct": 0,
        "multi_turn_case_count": 0,
        "clarification_true_positive": 0,
        "clarification_false_positive": 0,
        "clarification_false_negative": 0,
        "clarification_annotation_turn_count": 0,
        "clarification_exhausted_annotation_turn_count": 0,
        "clarification_exhausted_correct": 0,
        "clarification_exhausted_count": 0,
        "pending_continuation_count": 0,
        "pending_continuation_success_count": 0,
        "out_of_scope_cases": 0,
        "out_of_scope_positive_cases": 0,
        "out_of_scope_correct": 0,
        "slot_annotation_turn_count": 0,
        "slot_exact_matches": 0,
        "slot_true_positive": 0,
        "slot_false_positive": 0,
        "slot_false_negative": 0,
        "slot_actual_fields": 0,
        "slot_hallucinated_fields": 0,
        "slot_rejected_field_count": 0,
        "slot_proposed_fields": 0,
        "slot_unsupported_attempt_fields": 0,
        "slot_blocked_unsupported_fields": 0,
        "rule_fallback_count": 0,
        "guard_activation_count": 0,
        "invalid_json_count": 0,
        "evidence_validation_failure_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    policy_latencies: list[float] = []
    live_latencies: list[float] = []
    live_corrector = _CountingRouteCorrector(corrector) if corrector is not None else None

    for case in cases:
        turns = case["turns"]
        if len(turns) > 1:
            counters["multi_turn_case_count"] += 1
        history = _build_messages(case["id"], case.get("seed_messages", []))
        flow_state = _initial_flow_state(case)
        policy_corrector = RecordingRouteCorrector()
        evaluated_corrector = policy_corrector if evaluation_mode == "policy" else live_corrector
        assert evaluated_corrector is not None
        router = HybridRouter(
            SafetyPolicy(context_turns=safety_context_turns),
            evaluated_corrector,
            confidence_threshold=confidence_threshold,
            ambiguity_margin=ambiguity_margin,
            clarification_threshold=clarification_threshold,
            prompt_version=prompt_version,
        )
        case_rows: list[dict[str, Any]] = []

        for turn in turns:
            counters["turn_count"] += 1
            has_context = bool(history)
            counters["context_turn_count"] += int(has_context)
            flow_state = _apply_turn_flow_overrides(flow_state, turn)
            explicit_forced_task = _task_value(turn.get("forced_task"))
            pending_continuation = "forced_task" not in turn and is_pending_continuation(
                turn["query"], flow_state.pending_task
            )
            forced_task = flow_state.pending_task if pending_continuation else explicit_forced_task
            flow_before = flow_state
            route_input = RouteInput(
                latest_query=turn["query"],
                recent_messages=history[-20:],
                active_task=flow_before.active_task,
                forced_task=forced_task,
                date_task_state=_date_task_state(turn.get("date_task_state")),
                pending_task=flow_before.pending_task,
                pending_task_reason=flow_before.pending_task_reason,
                pending_task_turns_remaining=flow_before.pending_task_turns_remaining,
                last_clarification_reason=flow_before.last_clarification_reason,
                clarification_attempt_count=flow_before.clarification_attempt_count,
                previous_risk_state=flow_before.recent_risk_state,
            )
            rules = route_by_rules(route_input)
            if evaluation_mode == "policy":
                policy_corrector.prepare(
                    _correction_for_turn(turn.get("llm_correction"), rules),
                    failure=turn.get("llm_failure"),
                )
            calls_before = evaluated_corrector.call_count
            started = perf_counter()
            result = await router.route(route_input)
            duration_ms = (perf_counter() - started) * 1000
            calls_this_turn = evaluated_corrector.call_count - calls_before
            telemetry = evaluated_corrector.last_telemetry if calls_this_turn else {}
            llm_calls_this_turn = _llm_call_count(calls_this_turn, telemetry)
            counters["llm_calls"] += llm_calls_this_turn
            counters["router_correction_calls"] += calls_this_turn
            if evaluation_mode == "policy":
                policy_latencies.append(duration_ms)
            if calls_this_turn and evaluation_mode == "live":
                live_latencies.append(
                    _numeric(telemetry.get("duration_ms"))
                    or result.router_duration_ms
                    or duration_ms
                )

            expected = _expected_for_mode(turn, evaluation_mode)
            checks = _route_checks(
                result,
                expected,
                pending_continuation=pending_continuation,
            )
            if has_context:
                counters["context_route_cases"] += 1
                counters["context_route_correct"] += int(all(checks.values()))
            policy = expected.get("llm_policy", "optional")
            policy_passed = (
                policy == "optional"
                or (policy == "never" and calls_this_turn == 0)
                or (policy == "required" and calls_this_turn > 0)
            )
            checks["llm_policy"] = policy_passed
            passed = all(checks.values())
            counters["passed_turns"] += int(passed)

            _update_classification_metrics(counters, task_counts, result, expected)
            _update_state_metrics(
                counters,
                result,
                expected,
                pending_continuation=pending_continuation,
                forced_task=forced_task,
            )
            _update_slot_metrics(counters, result, expected)
            _update_operational_metrics(counters, result, calls_this_turn, telemetry)
            if policy == "never":
                counters["never_policy_violations"] += int(calls_this_turn > 0)
            if policy == "required":
                counters["required_policy_misses"] += int(calls_this_turn == 0)

            case_rows.append(
                {
                    "turn_id": turn.get("turn_id"),
                    "category": turn.get("category", case.get("category")),
                    "passed": passed,
                    "checks": checks,
                    "duration_ms": round(duration_ms, 3),
                    "context_message_count": len(history),
                    "active_task": (
                        flow_before.active_task.value if flow_before.active_task else None
                    ),
                    "forced_task": forced_task.value if forced_task else None,
                    "pending_continuation": pending_continuation,
                    "llm_calls": llm_calls_this_turn,
                    "router_correction_calls": calls_this_turn,
                    "actual": _route_summary(result),
                    "rule_actual": _route_summary(rules),
                    "recent_messages": [
                        {
                            "role": message.role.value,
                            "content": message.content,
                        }
                        for message in route_input.recent_messages
                    ],
                    "correction": _correction_trace(turn, result),
                    "route_annotated": _route_is_annotated(expected),
                    "route_correct": _route_matches_expected(result, expected),
                    "rule_route_correct": _route_matches_expected(rules, expected),
                    "llm_correction_success": bool(calls_this_turn and result.llm_error is None),
                    "fallback": result.fallback_reason is not None,
                    "evaluation_tags": _evaluation_tags(
                        case,
                        turn,
                        expected,
                        pending_continuation=pending_continuation,
                    ),
                    "flow_before": _flow_summary(flow_before),
                }
            )

            history.append(_message(case["id"], len(history) + 1, "user", turn["query"]))
            assistant_content = turn.get("assistant")
            if assistant_content:
                history.append(
                    _message(case["id"], len(history) + 1, "assistant", assistant_content)
                )
            flow_state = advance_conversation_flow(flow_before, result)
            case_rows[-1]["flow_after"] = _flow_summary(flow_state)

        rows.append(
            {
                "id": case["id"],
                "category": case.get("category"),
                "turn_count": len(turns),
                "passed": all(row["passed"] for row in case_rows),
                "turns": case_rows,
            }
        )

    return _build_report(
        path=path,
        cases=cases,
        rows=rows,
        counters=counters,
        task_counts=task_counts,
        policy_latencies=policy_latencies,
        live_latencies=live_latencies,
        evaluation_mode=evaluation_mode,
        corrector=corrector,
        input_cost_per_million=input_cost_per_million,
        output_cost_per_million=output_cost_per_million,
        routing_configuration={
            "confidence_threshold": confidence_threshold,
            "ambiguity_margin": ambiguity_margin,
            "clarification_threshold": clarification_threshold,
            "safety_context_turns": safety_context_turns,
            "prompt_version": prompt_version,
        },
        case_id=case_id,
        case_ids=case_ids,
        category=category,
        categories=categories,
    )


async def evaluate_live_routing_conversations(
    path: Path,
    settings: Settings,
    *,
    input_cost_per_million: float = 0,
    output_cost_per_million: float = 0,
    case_id: str | None = None,
    case_ids: Sequence[str] | None = None,
    category: str | None = None,
    categories: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Run a real-model eval only after the explicit environment guard is enabled."""
    if not settings.router_live_eval_enabled:
        raise RuntimeError(
            "Live Router Eval 未启用；请显式设置 LOVEAPP_ROUTER_LIVE_EVAL_ENABLED=true。"
        )
    if not settings.llm_api_key:
        raise ValueError("LOVEAPP_LLM_API_KEY 未配置。")
    if not settings.llm_base_url:
        raise ValueError("LOVEAPP_LLM_BASE_URL 未配置。")
    model = settings.router_model or settings.llm_model
    if not model:
        raise ValueError("LOVEAPP_ROUTER_MODEL 或 LOVEAPP_LLM_MODEL 未配置。")

    corrector = OpenAICompatibleRouteCorrector(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=model,
        timeout_seconds=settings.router_timeout_seconds,
        max_retries=settings.router_max_retries,
        max_tokens=settings.router_max_tokens,
        thinking=settings.router_thinking,
        prompt_version=settings.router_prompt_version,
    )
    try:
        report = await _evaluate_routing_conversations(
            path,
            corrector=corrector,
            evaluation_mode="live",
            input_cost_per_million=input_cost_per_million,
            output_cost_per_million=output_cost_per_million,
            confidence_threshold=settings.router_confidence_threshold,
            ambiguity_margin=settings.router_ambiguity_margin,
            clarification_threshold=settings.router_clarification_threshold,
            safety_context_turns=settings.router_context_risk_turns,
            prompt_version=settings.router_prompt_version,
            case_id=case_id,
            case_ids=case_ids,
            category=category,
            categories=categories,
        )
        report["live_configuration"] = {
            "model": model,
            "prompt_version": settings.router_prompt_version,
            "timeout_seconds": settings.router_timeout_seconds,
            "max_retries": settings.router_max_retries,
            "max_tokens": settings.router_max_tokens,
            "thinking": settings.router_thinking,
        }
        return report
    finally:
        await corrector.aclose()


def _update_classification_metrics(
    counters: dict[str, int],
    task_counts: dict[str, dict[str, int]],
    result: RouteResult,
    expected: dict[str, Any],
) -> None:
    if "task_type" in expected:
        counters["task_cases"] += 1
        expected_task = expected["task_type"]
        actual_task = result.task_type.value
        counters["task_correct"] += int(actual_task == expected_task)
        for task, counts in task_counts.items():
            counts["true_positive"] += int(actual_task == task == expected_task)
            counts["false_positive"] += int(actual_task == task and expected_task != task)
            counts["false_negative"] += int(expected_task == task and actual_task != task)
        expected_out_of_scope = expected_task == TaskType.OUT_OF_SCOPE.value
        actual_out_of_scope = actual_task == TaskType.OUT_OF_SCOPE.value
        counters["out_of_scope_cases"] += 1
        counters["out_of_scope_positive_cases"] += int(expected_out_of_scope)
        counters["out_of_scope_correct"] += int(actual_out_of_scope == expected_out_of_scope)

    if "primary_scenario" in expected:
        counters["scenario_cases"] += 1
        counters["scenario_correct"] += int(
            result.primary_scenario is not None
            and result.primary_scenario.value == expected["primary_scenario"]
        )
    if "secondary_scenarios" in expected:
        expected_secondary = set(expected["secondary_scenarios"])
        actual_secondary = {value.value for value in result.secondary_scenarios}
        counters["secondary_scenario_hits"] += len(expected_secondary & actual_secondary)
        counters["secondary_scenario_expected"] += len(expected_secondary)
    if "risk_level" in expected:
        counters["risk_cases"] += 1
        predicted_high = result.risk_level == RiskLevel.HIGH
        expected_high = expected["risk_level"] == RiskLevel.HIGH.value
        counters["risk_correct"] += int(result.risk_level.value == expected["risk_level"])
        counters["high_risk_cases"] += int(expected_high)
        counters["high_risk_true_positive"] += int(predicted_high and expected_high)

    if "goals" in expected or "goal" in expected:
        counters["goal_annotation_turn_count"] += 1
        expected_goals = set(expected.get("goals", []))
        if expected.get("goal"):
            expected_goals.add(expected["goal"])
        actual_goals = {
            goal.value
            for goal in [result.primary_goal, *result.secondary_goals]
            if goal is not None
        }
        counters["goal_true_positive"] += len(expected_goals & actual_goals)
        counters["goal_false_positive"] += len(actual_goals - expected_goals)
        counters["goal_false_negative"] += len(expected_goals - actual_goals)

    expected_clarification = _expected_clarification(expected)
    if expected_clarification is not None:
        counters["clarification_annotation_turn_count"] += 1
        actual_clarification = result.clarification_triggered
        counters["clarification_true_positive"] += int(
            actual_clarification and expected_clarification
        )
        counters["clarification_false_positive"] += int(
            actual_clarification and not expected_clarification
        )
        counters["clarification_false_negative"] += int(
            not actual_clarification and expected_clarification
        )


def _update_state_metrics(
    counters: dict[str, int],
    result: RouteResult,
    expected: dict[str, Any],
    *,
    pending_continuation: bool,
    forced_task: TaskType | None,
) -> None:
    counters["clarification_exhausted_count"] += int(result.clarification_exhausted)
    if "clarification_exhausted" in expected:
        counters["clarification_exhausted_annotation_turn_count"] += 1
        counters["clarification_exhausted_correct"] += int(
            result.clarification_exhausted is expected["clarification_exhausted"]
        )
    if pending_continuation:
        counters["pending_continuation_count"] += 1
        counters["pending_continuation_success_count"] += int(
            forced_task is not None
            and result.task_type == forced_task
            and result.pending_task is None
        )


def _update_slot_metrics(
    counters: dict[str, int],
    result: RouteResult,
    expected: dict[str, Any],
) -> None:
    counters["slot_rejected_field_count"] += len(result.slot_rejected_fields)
    if "slots" not in expected:
        return
    counters["slot_annotation_turn_count"] += 1
    expected_slots = _populated_mapping(expected["slots"])
    actual_slots = _populated_mapping(result.date_plan.model_dump(mode="json"))
    rejected_fields = set(result.slot_rejected_fields)
    unsupported_rejected = {
        field
        for field, reason in result.slot_rejected_fields.items()
        if _is_unsupported_slot_rejection(reason)
    }
    unexpected_accepted = {
        field
        for field, actual_value in actual_slots.items()
        if actual_value != expected_slots.get(field)
    }
    counters["slot_exact_matches"] += int(actual_slots == expected_slots)
    for field in expected_slots.keys() | actual_slots.keys():
        expected_value = expected_slots.get(field)
        actual_value = actual_slots.get(field)
        if expected_value == actual_value and expected_value is not None:
            counters["slot_true_positive"] += 1
        else:
            counters["slot_false_positive"] += int(actual_value is not None)
            counters["slot_false_negative"] += int(expected_value is not None)
    counters["slot_actual_fields"] += len(actual_slots)
    counters["slot_hallucinated_fields"] += sum(
        actual_value != expected_slots.get(field) for field, actual_value in actual_slots.items()
    )
    counters["slot_proposed_fields"] += len(set(actual_slots) | rejected_fields)
    counters["slot_unsupported_attempt_fields"] += len(unexpected_accepted | unsupported_rejected)
    counters["slot_blocked_unsupported_fields"] += len(unsupported_rejected)


def _update_operational_metrics(
    counters: dict[str, int],
    result: RouteResult,
    calls_this_turn: int,
    telemetry: Mapping[str, str | int | float | None],
) -> None:
    counters["rule_fallback_count"] += int(result.fallback_reason is not None)
    counters["guard_activation_count"] += int(result.task_guard_applied)
    counters["input_tokens"] += int(
        result.router_input_tokens or _numeric(telemetry.get("input_tokens")) or 0
    )
    counters["output_tokens"] += int(
        result.router_output_tokens or _numeric(telemetry.get("output_tokens")) or 0
    )
    if calls_this_turn and result.llm_error:
        error = result.llm_error.lower()
        counters["invalid_json_count"] += int(
            "json" in error or "routecorrection" in error or "结构" in error
        )
        counters["evidence_validation_failure_count"] += int("evidence" in error or "证据" in error)


def _llm_call_count(
    correction_calls: int,
    telemetry: Mapping[str, str | int | float | None],
) -> int:
    """Return underlying model requests, falling back to corrector invocations."""

    if correction_calls <= 0:
        return 0
    attempt_count = telemetry.get("attempt_count")
    if (
        correction_calls == 1
        and isinstance(attempt_count, int)
        and not isinstance(attempt_count, bool)
        and attempt_count > 0
    ):
        return attempt_count
    return correction_calls


def _average_tokens_per_llm_call(total_tokens: int, llm_call_count: int) -> float:
    return _ratio(total_tokens, llm_call_count)


def _build_report(
    *,
    path: Path,
    cases: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    counters: dict[str, int],
    task_counts: dict[str, dict[str, int]],
    policy_latencies: list[float],
    live_latencies: list[float],
    evaluation_mode: EvaluationMode,
    corrector: RouteCorrector | None,
    input_cost_per_million: float,
    output_cost_per_million: float,
    routing_configuration: dict[str, str | int | float],
    case_id: str | None = None,
    case_ids: Sequence[str] | None = None,
    category: str | None = None,
    categories: Sequence[str] | None = None,
) -> dict[str, Any]:
    goal_precision = _ratio(
        counters["goal_true_positive"],
        counters["goal_true_positive"] + counters["goal_false_positive"],
    )
    goal_recall = _ratio(
        counters["goal_true_positive"],
        counters["goal_true_positive"] + counters["goal_false_negative"],
    )
    clarification_precision = _ratio(
        counters["clarification_true_positive"],
        counters["clarification_true_positive"] + counters["clarification_false_positive"],
    )
    clarification_recall = _ratio(
        counters["clarification_true_positive"],
        counters["clarification_true_positive"] + counters["clarification_false_negative"],
    )
    task_metrics = _task_metrics(task_counts)
    supported_task_metrics = [
        metrics for metrics in task_metrics.values() if metrics["support"] > 0
    ]
    turn_count = counters["turn_count"]
    llm_calls = counters["llm_calls"]
    router_correction_calls = counters["router_correction_calls"]
    average_input_tokens_per_llm_call = _average_tokens_per_llm_call(
        counters["input_tokens"],
        llm_calls,
    )
    average_output_tokens_per_llm_call = _average_tokens_per_llm_call(
        counters["output_tokens"],
        llm_calls,
    )
    estimated_cost = (
        counters["input_tokens"] * input_cost_per_million
        + counters["output_tokens"] * output_cost_per_million
    ) / 1_000_000
    report: dict[str, Any] = {
        "schema_version": 2,
        "generated_at": datetime.now(UTC).isoformat(),
        "evaluation_mode": evaluation_mode,
        "corrector_kind": (
            "recording" if evaluation_mode == "policy" else type(corrector).__name__
        ),
        "dataset": str(path),
        "dataset_version": path.stem.removeprefix("cases_"),
        "dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "code_revision": _code_revision(),
        "routing_configuration": routing_configuration,
        "case_filter": _filter_values(case_id, case_ids),
        "category_filter": _filter_values(category, categories),
        "case_count": len(cases),
        "turn_count": turn_count,
        "multi_turn_case_count": counters["multi_turn_case_count"],
        "context_turn_count": counters["context_turn_count"],
        "context_route_accuracy": _ratio(
            counters["context_route_correct"], counters["context_route_cases"]
        ),
        "pass_rate": _ratio(counters["passed_turns"], turn_count),
        "conversation_pass_rate": _ratio(sum(row["passed"] for row in rows), len(rows)),
        "task_accuracy": _ratio(counters["task_correct"], counters["task_cases"]),
        "task_macro_precision": _average_metric(supported_task_metrics, "precision"),
        "task_macro_recall": _average_metric(supported_task_metrics, "recall"),
        "task_macro_f1": _average_metric(supported_task_metrics, "f1"),
        "task_metrics": task_metrics,
        "primary_scenario_accuracy": _ratio(
            counters["scenario_correct"], counters["scenario_cases"]
        ),
        "secondary_scenario_recall": _ratio(
            counters["secondary_scenario_hits"],
            counters["secondary_scenario_expected"],
        ),
        "risk_accuracy": _ratio(counters["risk_correct"], counters["risk_cases"]),
        "high_risk_recall": _ratio(
            counters["high_risk_true_positive"], counters["high_risk_cases"]
        ),
        "goal_micro_precision": goal_precision,
        "goal_micro_recall": goal_recall,
        "goal_micro_f1": _f1(goal_precision, goal_recall),
        "goal_annotation_turn_count": counters["goal_annotation_turn_count"],
        "clarification_precision": clarification_precision,
        "clarification_recall": clarification_recall,
        "clarification_f1": _f1(clarification_precision, clarification_recall),
        "clarification_annotation_turn_count": counters["clarification_annotation_turn_count"],
        "clarification_exhausted_count": counters["clarification_exhausted_count"],
        "clarification_exhausted_annotation_turn_count": counters[
            "clarification_exhausted_annotation_turn_count"
        ],
        "clarification_exhausted_accuracy": _ratio(
            counters["clarification_exhausted_correct"],
            counters["clarification_exhausted_annotation_turn_count"],
        ),
        "pending_continuation_count": counters["pending_continuation_count"],
        "pending_continuation_success_rate": _ratio(
            counters["pending_continuation_success_count"],
            counters["pending_continuation_count"],
        ),
        "out_of_scope_accuracy": _ratio(
            counters["out_of_scope_correct"], counters["out_of_scope_cases"]
        ),
        "out_of_scope_annotation_turn_count": counters["out_of_scope_cases"],
        "out_of_scope_positive_turn_count": counters["out_of_scope_positive_cases"],
        "slot_exact_match": _ratio(
            counters["slot_exact_matches"], counters["slot_annotation_turn_count"]
        ),
        "slot_field_precision": _ratio(
            counters["slot_true_positive"],
            counters["slot_true_positive"] + counters["slot_false_positive"],
        ),
        "slot_field_recall": _ratio(
            counters["slot_true_positive"],
            counters["slot_true_positive"] + counters["slot_false_negative"],
        ),
        "slot_hallucination_rate": _ratio(
            counters["slot_hallucinated_fields"], counters["slot_actual_fields"]
        ),
        "slot_hallucination_attempt_rate": _ratio(
            counters["slot_unsupported_attempt_fields"],
            counters["slot_proposed_fields"],
        ),
        "slot_validator_block_rate": _ratio(
            counters["slot_blocked_unsupported_fields"],
            counters["slot_unsupported_attempt_fields"],
        ),
        "slot_unsupported_attempt_field_count": counters["slot_unsupported_attempt_fields"],
        "slot_annotation_turn_count": counters["slot_annotation_turn_count"],
        "slot_rejected_field_count": counters["slot_rejected_field_count"],
        "llm_call_count": llm_calls,
        "llm_call_rate": _ratio(llm_calls, turn_count),
        "router_correction_call_count": router_correction_calls,
        "router_correction_call_rate": _ratio(router_correction_calls, turn_count),
        "never_policy_violations": counters["never_policy_violations"],
        "required_policy_misses": counters["required_policy_misses"],
        "rule_fallback_count": counters["rule_fallback_count"],
        "rule_fallback_rate": _ratio(counters["rule_fallback_count"], llm_calls),
        "guard_activation_count": counters["guard_activation_count"],
        "guard_activation_rate": _ratio(counters["guard_activation_count"], llm_calls),
        "invalid_json_count": counters["invalid_json_count"],
        "invalid_json_rate": _ratio(counters["invalid_json_count"], llm_calls),
        "evidence_validation_failure_count": counters["evidence_validation_failure_count"],
        "evidence_validation_failure_rate": _ratio(
            counters["evidence_validation_failure_count"], llm_calls
        ),
        "mean_policy_latency_ms": round(mean(policy_latencies), 3) if policy_latencies else 0,
        "p50_policy_latency_ms": round(_percentile(policy_latencies, 0.50), 3),
        "p95_policy_latency_ms": round(_percentile(policy_latencies, 0.95), 3),
        "p50_live_router_latency_ms": round(_percentile(live_latencies, 0.50), 3),
        "p95_live_router_latency_ms": round(_percentile(live_latencies, 0.95), 3),
        # Keep the original keys while making their denominator explicit in
        # the new aliases. Per-turn values remain available for dashboards
        # that used the previous calculation.
        "average_input_tokens": average_input_tokens_per_llm_call,
        "average_output_tokens": average_output_tokens_per_llm_call,
        "average_input_tokens_per_llm_call": average_input_tokens_per_llm_call,
        "average_output_tokens_per_llm_call": average_output_tokens_per_llm_call,
        "average_input_tokens_per_turn": _ratio(counters["input_tokens"], turn_count),
        "average_output_tokens_per_turn": _ratio(counters["output_tokens"], turn_count),
        "estimated_cost": round(estimated_cost, 8),
        "estimated_cost_per_turn": round(estimated_cost / turn_count, 8) if turn_count else 0,
        "token_cost_assumptions_per_million": {
            "input": input_cost_per_million,
            "output": output_cost_per_million,
        },
        "cases": rows,
    }
    report.update(_routing_alias_metrics(rows))
    report["acceptance_targets"] = _acceptance_targets(report)
    report["acceptance_passed"] = all(report["acceptance_targets"].values())
    return report


def _task_metrics(
    task_counts: dict[str, dict[str, int]],
) -> dict[str, dict[str, int | float]]:
    metrics: dict[str, dict[str, int | float]] = {}
    for task, counts in task_counts.items():
        precision = _ratio(
            counts["true_positive"],
            counts["true_positive"] + counts["false_positive"],
        )
        recall = _ratio(
            counts["true_positive"],
            counts["true_positive"] + counts["false_negative"],
        )
        metrics[task] = {
            "precision": precision,
            "recall": recall,
            "f1": _f1(precision, recall),
            "support": counts["true_positive"] + counts["false_negative"],
        }
    return metrics


def _acceptance_targets(report: dict[str, Any]) -> dict[str, bool]:
    targets = {
        "task_macro_f1_gte_0_90": report["task_macro_f1"] >= 0.90,
        "high_risk_recall_eq_1_00": report["high_risk_recall"] == 1.0,
        "out_of_scope_accuracy_gte_0_90": report["out_of_scope_accuracy"] >= 0.90,
        "clarification_precision_gte_0_85": report["clarification_precision"] >= 0.85,
        "slot_hallucination_rate_lte_0_02": report["slot_hallucination_rate"] <= 0.02,
        "never_policy_violations_eq_0": report["never_policy_violations"] == 0,
        "required_policy_misses_eq_0": report["required_policy_misses"] == 0,
    }
    annotation_requirements = {
        "high_risk_recall_eq_1_00": report["high_risk_recall"] != 0,
        "out_of_scope_accuracy_gte_0_90": report["out_of_scope_positive_turn_count"] > 0,
        "clarification_precision_gte_0_85": (report["clarification_annotation_turn_count"] > 0),
        "slot_hallucination_rate_lte_0_02": report["slot_annotation_turn_count"] > 0,
    }
    for name, annotated in annotation_requirements.items():
        targets[name] = targets[name] and annotated
    return targets


def _route_checks(
    result: RouteResult,
    expected: dict[str, Any],
    *,
    pending_continuation: bool = False,
) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    if "task_type" in expected:
        checks["task_type"] = result.task_type.value == expected["task_type"]
    if "risk_level" in expected:
        checks["risk_level"] = result.risk_level.value == expected["risk_level"]
    if "route" in expected:
        checks["route"] = _route_branch(result) == expected["route"]
    if "primary_scenario" in expected:
        checks["primary_scenario"] = (
            result.primary_scenario is not None
            and result.primary_scenario.value == expected["primary_scenario"]
        )
    if "secondary_scenarios" in expected:
        checks["secondary_scenarios"] = set(expected["secondary_scenarios"]) <= {
            value.value for value in result.secondary_scenarios
        }
    if "excluded_scenarios" in expected:
        actual = {
            value.value
            for value in [result.primary_scenario, *result.secondary_scenarios]
            if value is not None
        }
        checks["excluded_scenarios"] = not (set(expected["excluded_scenarios"]) & actual)
    expected_goals = set(expected.get("goals", []))
    if expected.get("goal"):
        expected_goals.add(expected["goal"])
    if expected_goals:
        checks["goals"] = expected_goals <= {
            value.value
            for value in [result.primary_goal, *result.secondary_goals]
            if value is not None
        }
    if "secondary_tasks" in expected:
        checks["secondary_tasks"] = set(expected["secondary_tasks"]) <= {
            value.value for value in result.secondary_tasks
        }
    if "clarification" in expected:
        checks["clarification"] = result.clarification_triggered is expected["clarification"]
    if "clarification_exhausted" in expected:
        checks["clarification_exhausted"] = (
            result.clarification_exhausted is expected["clarification_exhausted"]
        )
    if "pending_continuation" in expected:
        checks["pending_continuation"] = pending_continuation is expected["pending_continuation"]
    if "task_guard_applied" in expected:
        checks["task_guard_applied"] = result.task_guard_applied is expected["task_guard_applied"]
    if "pending_task" in expected:
        checks["pending_task"] = (
            result.pending_task.value if result.pending_task else None
        ) == expected["pending_task"]
    if "pending_task_cancelled" in expected:
        checks["pending_task_cancelled"] = (
            result.pending_task_cancelled is expected["pending_task_cancelled"]
        )
    if "fallback_reason" in expected:
        checks["fallback_reason"] = result.fallback_reason == expected["fallback_reason"]
    if "slots" in expected:
        checks["slots"] = _populated_mapping(
            result.date_plan.model_dump(mode="json")
        ) == _populated_mapping(expected["slots"])
    if "slot_rejected_fields" in expected:
        rejected = expected["slot_rejected_fields"]
        checks["slot_rejected_fields"] = (
            set(rejected) <= set(result.slot_rejected_fields)
            if isinstance(rejected, list)
            else all(
                result.slot_rejected_fields.get(key) == value for key, value in rejected.items()
            )
        )
    return checks


def _route_is_annotated(expected: Mapping[str, Any]) -> bool:
    """Return whether a turn has a task/branch expectation for route metrics."""

    return "task_type" in expected or "route" in expected


def _route_matches_expected(result: RouteResult, expected: Mapping[str, Any]) -> bool:
    """Compare only the route dimensions, independently of scenario/slot checks."""

    checks: list[bool] = []
    if "task_type" in expected:
        checks.append(result.task_type.value == expected["task_type"])
    if "route" in expected:
        checks.append(_route_branch(result) == expected["route"])
    return all(checks) if checks else False


def _evaluation_tags(
    case: Mapping[str, Any],
    turn: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    pending_continuation: bool,
) -> list[str]:
    """Normalize optional dataset annotations used for slice metrics.

    The evaluator keeps the fixture schema intentionally permissive: existing v1-v4
    datasets use ``category`` while newer evaluation sets can add ``tags`` or explicit
    boolean annotations without changing the Router contract.
    """

    raw_tags = turn.get("tags", case.get("tags", []))
    if isinstance(raw_tags, str):
        tags = {raw_tags.casefold()}
    elif isinstance(raw_tags, list):
        tags = {str(tag).casefold() for tag in raw_tags}
    else:
        tags = set()
    category = str(turn.get("category", case.get("category", ""))).casefold()
    if category:
        tags.add(category)
    if len(case.get("turns", [])) == 1:
        tags.add("single_turn")
    else:
        tags.add("multi_turn")

    if pending_continuation or expected.get("pending_continuation"):
        tags.update({"continuation", "task_resume"})
    if expected.get("task_switch") or "switch" in category:
        tags.add("task_switch")
    if expected.get("task_resume") or "resume" in category:
        tags.add("task_resume")
    if (
        expected.get("ambiguous")
        or expected.get("clarification")
        or ("ambigu" in category or "clarif" in category)
    ):
        tags.update({"ambiguous", "ambiguity"})
    if "follow" in category or "continu" in category:
        tags.add("continuation")
    if expected.get("fallback_reason") or "failure" in category:
        tags.add("fallback")
    if "negation" in category or "guard" in category or "anti_context" in tags:
        tags.add("anti_context_bias")
    return sorted(tags)


def _routing_alias_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the compact metrics required by the pre-resume Router evaluation.

    ``rule_only_accuracy`` is the all-turn deterministic rule baseline, while
    ``final_accuracy`` is measured after the optional correction/merge step.  Both
    use the same annotated route denominator so ``correction_gain`` is meaningful.
    """

    turns = [turn for case in rows for turn in case["turns"]]
    annotated = [turn for turn in turns if turn.get("route_annotated")]

    def accuracy(values: list[dict[str, Any]], field: str = "route_correct") -> float:
        eligible = [value for value in values if value.get("route_annotated")]
        return _ratio(sum(bool(value.get(field)) for value in eligible), len(eligible))

    def tagged_accuracy(tag: str) -> float:
        return accuracy([turn for turn in turns if tag in set(turn.get("evaluation_tags", []))])

    single_turns = [turn for case in rows if case["turn_count"] == 1 for turn in case["turns"]]
    multi_turns = [turn for case in rows if case["turn_count"] > 1 for turn in case["turns"]]
    llm_turns = [turn for turn in turns if turn.get("router_correction_calls", 0)]
    untouched_turns = [turn for turn in turns if not turn.get("router_correction_calls", 0)]
    fallback_count = sum(bool(turn.get("fallback")) for turn in turns)
    llm_success_count = sum(bool(turn.get("llm_correction_success")) for turn in turns)
    rule_accuracy = accuracy(annotated, field="rule_route_correct")
    final_accuracy = accuracy(annotated)

    return {
        "overall_route_accuracy": final_accuracy,
        "single_turn_accuracy": accuracy(single_turns),
        "multi_turn_accuracy": accuracy(multi_turns),
        "continuation_accuracy": tagged_accuracy("continuation"),
        "task_switch_accuracy": tagged_accuracy("task_switch"),
        "task_resume_accuracy": tagged_accuracy("task_resume"),
        "ambiguous_case_accuracy": tagged_accuracy("ambiguous"),
        "fallback_count": fallback_count,
        "fallback_rate": _ratio(fallback_count, len(turns)),
        "rule_decision_count": len(turns),
        "llm_correction_count": sum(turn.get("router_correction_calls", 0) for turn in turns),
        "llm_correction_success_count": llm_success_count,
        "rule_only_accuracy": rule_accuracy,
        "rule_only_untouched_accuracy": accuracy(untouched_turns),
        "final_accuracy": final_accuracy,
        "correction_gain": round(final_accuracy - rule_accuracy, 4),
        "route_annotation_count": len(annotated),
        "untouched_turn_count": len(untouched_turns),
        "corrected_turn_count": len(llm_turns),
    }


def render_routing_report(report: Mapping[str, Any]) -> str:
    """Render a concise Markdown summary while retaining JSON for full traces."""

    metric_names = (
        "overall_route_accuracy",
        "single_turn_accuracy",
        "multi_turn_accuracy",
        "continuation_accuracy",
        "task_switch_accuracy",
        "task_resume_accuracy",
        "ambiguous_case_accuracy",
        "fallback_count",
        "fallback_rate",
        "rule_decision_count",
        "llm_correction_count",
        "llm_correction_success_count",
        "rule_only_accuracy",
        "final_accuracy",
        "correction_gain",
    )
    lines = [
        "# Router Evaluation Report",
        "",
        f"- Dataset: `{report.get('dataset', '')}`",
        f"- Mode: `{report.get('evaluation_mode', '')}`",
        f"- Cases: {report.get('case_count', 0)}",
        f"- Turns: {report.get('turn_count', 0)}",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    lines.extend(f"| `{name}` | {report.get(name, 0)} |" for name in metric_names)
    lines.extend(
        [
            "",
            "## Failed Turns",
            "",
            "| Case | Turn | Category | Expected | Actual |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    failures = 0
    for case in report.get("cases", []):
        for turn in case.get("turns", []):
            if turn.get("passed"):
                continue
            failures += 1
            expected = (
                ", ".join(key for key, value in turn.get("checks", {}).items() if not value)
                or "unannotated"
            )
            actual = turn.get("actual", {}).get("route") or turn.get("actual", {}).get("task_type")
            lines.append(
                f"| `{case.get('id', '')}` | `{turn.get('turn_id', '')}` | "
                f"`{turn.get('category', '') or ''}` | {expected} | {actual or ''} |"
            )
    if not failures:
        lines.append("| - | - | - | none | - |")
    return "\n".join(lines) + "\n"


def _route_branch(result: RouteResult) -> str:
    if result.risk_level == RiskLevel.HIGH:
        return "high_risk_response"
    if result.risk_level == RiskLevel.SENSITIVE:
        return "sensitive_risk_response"
    if result.clarification_triggered or result.clarification_exhausted:
        return "clarify_intent"
    return {
        TaskType.RELATIONSHIP_ADVICE: "relationship_advice",
        TaskType.DATE_PLANNING: "date_planning",
        TaskType.GENERAL_CHAT: "casual_chat",
        TaskType.OUT_OF_SCOPE: "out_of_scope",
    }[result.task_type]


def _route_summary(result: RouteResult) -> dict[str, Any]:
    return {
        "route": _route_branch(result),
        "task_type": result.task_type.value,
        "rule_task_type": result.rule_task_type.value if result.rule_task_type else None,
        "llm_task_type": result.llm_task_type.value if result.llm_task_type else None,
        "task_guard_applied": result.task_guard_applied,
        "secondary_tasks": [value.value for value in result.secondary_tasks],
        "primary_scenario": result.primary_scenario.value if result.primary_scenario else None,
        "secondary_scenarios": [value.value for value in result.secondary_scenarios],
        "primary_goal": result.primary_goal.value if result.primary_goal else None,
        "secondary_goals": [value.value for value in result.secondary_goals],
        "risk_level": result.risk_level.value,
        "source": result.source.value,
        "llm_used": result.llm_used,
        "llm_error": result.llm_error,
        "fallback_reason": result.fallback_reason,
        "clarification_triggered": result.clarification_triggered,
        "clarification_exhausted": result.clarification_exhausted,
        "clarification_reason": result.clarification_reason,
        "pending_task": result.pending_task.value if result.pending_task else None,
        "pending_task_cancelled": result.pending_task_cancelled,
        "date_request_mode": result.date_request_mode.value,
        "date_intent": result.date_intent.value,
        "date_mutation": result.date_mutation.value,
        "date_plan": result.date_plan.model_dump(mode="json"),
        "slot_accepted_fields": result.slot_accepted_fields,
        "slot_rejected_fields": result.slot_rejected_fields,
        "slot_field_sources": result.slot_field_sources,
        "router_model": result.router_model,
        "router_input_tokens": result.router_input_tokens,
        "router_output_tokens": result.router_output_tokens,
        "router_duration_ms": result.router_duration_ms,
    }


def _initial_flow_state(case: dict[str, Any]) -> ConversationFlowState:
    return ConversationFlowState(
        user_id="routing-eval-user",
        relationship_id=f"routing-{case['id']}",
        conversation_id=case["id"],
        active_task=_task_value(case.get("active_task")),
        pending_task=_task_value(case.get("pending_task")),
        pending_task_reason=case.get("pending_task_reason"),
        pending_task_source=case.get("pending_task_source"),
        pending_task_turns_remaining=int(case.get("pending_task_turns_remaining", 0)),
        last_clarification_reason=case.get("last_clarification_reason"),
        clarification_attempt_count=int(case.get("clarification_attempt_count", 0)),
        recent_risk_state=(
            RecentRiskState.model_validate(case["recent_risk_state"])
            if case.get("recent_risk_state")
            else None
        ),
    )


def _apply_turn_flow_overrides(
    flow_state: ConversationFlowState,
    turn: dict[str, Any],
) -> ConversationFlowState:
    updates: dict[str, Any] = {}
    for field in (
        "active_task",
        "pending_task",
        "pending_task_reason",
        "pending_task_source",
        "pending_task_turns_remaining",
        "last_clarification_reason",
        "clarification_attempt_count",
    ):
        if field in turn:
            value = turn[field]
            if field in {"active_task", "pending_task"}:
                value = _task_value(value)
            updates[field] = value
    if "recent_risk_state" in turn:
        updates["recent_risk_state"] = (
            RecentRiskState.model_validate(turn["recent_risk_state"])
            if turn["recent_risk_state"]
            else None
        )
    return flow_state.model_copy(update=updates) if updates else flow_state


def _flow_summary(flow_state: ConversationFlowState) -> dict[str, Any]:
    return {
        "active_task": flow_state.active_task.value if flow_state.active_task else None,
        "pending_task": flow_state.pending_task.value if flow_state.pending_task else None,
        "pending_task_source": flow_state.pending_task_source,
        "pending_task_turns_remaining": flow_state.pending_task_turns_remaining,
        "last_clarification_reason": flow_state.last_clarification_reason,
        "clarification_attempt_count": flow_state.clarification_attempt_count,
        "recent_risk_level": (
            flow_state.recent_risk_state.level.value if flow_state.recent_risk_state else None
        ),
    }


def _correction_for_turn(
    payload: dict[str, Any] | None,
    rules: RouteResult,
) -> RouteCorrection:
    if payload is None:
        return _correction_from_rules(rules)
    data = _correction_from_rules(rules).model_dump(mode="json")
    data.update(payload)
    return RouteCorrection.model_validate(data)


def _correction_trace(turn: dict[str, Any], result: RouteResult) -> dict[str, Any]:
    """Record the correction fixture and the observable final override outcome."""

    payload = turn.get("llm_correction")
    return {
        "fixture": payload if isinstance(payload, dict) else None,
        "failure_fixture": turn.get("llm_failure"),
        "llm_used": result.llm_used,
        "llm_error": result.llm_error,
        "fallback_reason": result.fallback_reason,
        "rule_task_type": result.rule_task_type.value if result.rule_task_type else None,
        "llm_task_type": result.llm_task_type.value if result.llm_task_type else None,
        "final_task_type": result.task_type.value,
        "task_guard_applied": result.task_guard_applied,
    }


def _correction_from_rules(result: RouteResult) -> RouteCorrection:
    return RouteCorrection(
        task_type=result.task_type,
        secondary_tasks=result.secondary_tasks,
        task_confidence=result.task_confidence,
        primary_goal=result.primary_goal,
        secondary_goals=result.secondary_goals,
        primary_scenario=result.primary_scenario,
        secondary_scenarios=result.secondary_scenarios,
        scenario_confidence=result.scenario_confidence,
        needs_clarification=result.needs_clarification,
        evidence_spans=result.evidence_spans[:8],
        date_plan=result.date_plan,
        date_request_mode=result.date_request_mode,
        date_intent=result.date_intent,
        date_mutation=result.date_mutation,
    )


def _build_messages(case_id: str, values: list[dict[str, str]]) -> list[StoredMessage]:
    return [
        _message(case_id, index, value["role"], value["content"])
        for index, value in enumerate(values, start=1)
    ]


def _message(case_id: str, index: int, role: str, content: str) -> StoredMessage:
    return StoredMessage(
        id=f"{case_id}-{index}",
        conversation_id=case_id,
        user_id="routing-eval-user",
        relationship_id=f"routing-{case_id}",
        role=MessageRole(role),
        content=content,
    )


def _task_value(value: str | None) -> TaskType | None:
    return TaskType(value) if value else None


def _date_task_state(value: dict[str, Any] | None) -> DatePlanningTaskState | None:
    return DatePlanningTaskState.model_validate(value) if value else None


def _next_risk_state(
    previous: RecentRiskState | None,
    result: RouteResult,
) -> RecentRiskState | None:
    if result.risk_level == RiskLevel.HIGH:
        return RecentRiskState(level=RiskLevel.HIGH, reasons=result.risk_reasons)
    if previous is None or result.recent_risk_deescalated:
        return None
    remaining = previous.expires_after_turns - 1
    return previous.model_copy(update={"expires_after_turns": remaining}) if remaining > 0 else None


def _expected_clarification(expected: dict[str, Any]) -> bool | None:
    if "clarification" in expected:
        return bool(expected["clarification"])
    if "route" in expected:
        return expected["route"] == "clarify_intent"
    return None


def _expected_for_mode(
    turn: dict[str, Any],
    evaluation_mode: EvaluationMode,
) -> dict[str, Any]:
    expected = dict(turn.get("expected", {}))
    if evaluation_mode == "live" and ("llm_correction" in turn or "llm_failure" in turn):
        # These checks describe deterministic fault/merge fixtures, not a desired
        # real-model output. Semantic route expectations still apply in Live Eval.
        expected.pop("fallback_reason", None)
        expected.pop("slot_rejected_fields", None)
    return expected


def _populated_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item is not None and item != [] and item != {} and item != ""
    }


def _numeric(value: str | int | float | None) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _is_unsupported_slot_rejection(reason: str) -> bool:
    normalized = reason.lower()
    return any(
        marker in normalized
        for marker in ("evidence", "unsupported", "not_in_source", "hallucination")
    )


def _recorded_failure(kind: str) -> ValueError:
    messages = {
        "invalid_json": "路由模型返回内容不符合 RouteCorrection JSON 结构。",
        "evidence_validation": "路由证据不在对话原文中。",
        "timeout": "Router LLM timeout。",
    }
    if kind not in messages:
        raise ValueError(f"未知的 llm_failure 类型：{kind}")
    return ValueError(messages[kind])


def _load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"路由评测集第 {line_number} 行不是合法 JSON。") from exc
        if not isinstance(case, dict):
            raise ValueError(f"路由评测集第 {line_number} 行必须是 JSON object。")
        _validate_case(case, line_number=line_number)
        cases.append(case)

    case_ids: set[str] = set()
    for case in cases:
        if case["id"] in case_ids:
            raise ValueError(f"路由评测案例 id 重复：{case['id']}。")
        case_ids.add(case["id"])
        turn_ids: set[str] = set()
        for turn in case["turns"]:
            turn_id = turn.get("turn_id")
            if turn_id and turn_id in turn_ids:
                raise ValueError(f"路由评测案例 {case['id']} 的 turn_id 重复：{turn_id}。")
            if turn_id:
                turn_ids.add(turn_id)
    return cases


def _filter_values(
    value: str | None,
    values: Sequence[str] | None,
) -> list[str]:
    selected: list[str] = []
    if value:
        selected.append(value)
    if values:
        selected.extend(str(item) for item in values if str(item).strip())
    return list(dict.fromkeys(selected))


def _filter_cases(
    cases: list[dict[str, Any]],
    *,
    case_id: str | None,
    case_ids: Sequence[str] | None,
    category: str | None,
    categories: Sequence[str] | None,
) -> list[dict[str, Any]]:
    requested_ids = set(_filter_values(case_id, case_ids))
    requested_categories = {item.casefold() for item in _filter_values(category, categories)}
    available_ids = {str(case["id"]) for case in cases}
    unknown_ids = requested_ids - available_ids
    if unknown_ids:
        raise ValueError("unknown routing case id(s): " + ", ".join(sorted(unknown_ids)))
    available_categories = {
        category_name for case in cases for category_name in _case_categories(case)
    }
    unknown_categories = requested_categories - available_categories
    if unknown_categories:
        raise ValueError("unknown routing category(ies): " + ", ".join(sorted(unknown_categories)))
    filtered = [
        case
        for case in cases
        if (not requested_ids or case["id"] in requested_ids)
        and (not requested_categories or requested_categories & _case_categories(case))
    ]
    if not filtered:
        raise ValueError("routing filters matched no cases")
    return filtered


def _case_categories(case: Mapping[str, Any]) -> set[str]:
    categories: set[str] = set()
    if case.get("category"):
        categories.add(str(case["category"]).casefold())
    turns = case.get("turns", [])
    if isinstance(turns, list):
        categories.update(
            str(turn["category"]).casefold()
            for turn in turns
            if isinstance(turn, Mapping) and turn.get("category")
        )
    return categories


def _validate_case(case: dict[str, Any], *, line_number: int) -> None:
    case_id = case.get("id")
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError(f"路由评测集第 {line_number} 行缺少有效 id。")
    category = case.get("category")
    if category is not None and (not isinstance(category, str) or not category.strip()):
        raise ValueError(f"路由评测案例 {case_id} 的 category 必须是非空字符串。")
    turns = case.get("turns")
    if not isinstance(turns, list) or not turns:
        raise ValueError(f"路由评测案例 {case_id} 必须包含非空 turns。")
    _validate_messages(case_id, case.get("seed_messages", []), field="seed_messages")
    _validate_flow_fields(case_id, case)
    for index, turn in enumerate(turns, start=1):
        if not isinstance(turn, dict):
            raise ValueError(f"路由评测案例 {case_id} 的第 {index} 个 turn 必须是 object。")
        query = turn.get("query")
        expected = turn.get("expected")
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"路由评测案例 {case_id} 的第 {index} 个 turn 缺少 query。")
        if not isinstance(expected, dict) or not expected:
            raise ValueError(f"路由评测案例 {case_id} 的第 {index} 个 turn 缺少 expected。")
        if "assistant" in turn and not isinstance(turn["assistant"], str):
            raise ValueError(f"路由评测案例 {case_id} 的 assistant 必须是字符串。")
        for field in ("active_task", "forced_task", "pending_task"):
            if turn.get(field) is not None:
                _validate_enum(TaskType, turn[field], case_id=case_id, field=field)
        if "llm_correction" in turn and not isinstance(turn["llm_correction"], dict):
            raise ValueError(f"路由评测案例 {case_id} 的 llm_correction 必须是 object。")
        if turn.get("llm_failure") not in {None, "invalid_json", "evidence_validation", "timeout"}:
            raise ValueError(f"路由评测案例 {case_id} 包含未知 llm_failure。")
        _validate_flow_fields(case_id, turn)
        _validate_expected(case_id, expected)


def _validate_flow_fields(case_id: str, value: dict[str, Any]) -> None:
    for field in (
        "pending_task_turns_remaining",
        "clarification_attempt_count",
    ):
        if field in value and (not isinstance(value[field], int) or not 0 <= value[field] <= 4):
            raise ValueError(f"路由评测案例 {case_id} 的 {field} 必须是 0 到 4 的整数。")
    for field in (
        "pending_task_reason",
        "pending_task_source",
        "last_clarification_reason",
    ):
        if value.get(field) is not None and not isinstance(value[field], str):
            raise ValueError(f"路由评测案例 {case_id} 的 {field} 必须是字符串。")
    if value.get("recent_risk_state") is not None:
        try:
            RecentRiskState.model_validate(value["recent_risk_state"])
        except ValueError as exc:
            raise ValueError(f"路由评测案例 {case_id} 的 recent_risk_state 结构无效。") from exc


def _validate_messages(
    case_id: str,
    messages: Any,
    *,
    field: str,
) -> None:
    if not isinstance(messages, list):
        raise ValueError(f"路由评测案例 {case_id} 的 {field} 必须是 list。")
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ValueError(f"路由评测案例 {case_id} 的 {field} 消息结构无效。")
        _validate_enum(MessageRole, message.get("role"), case_id=case_id, field=f"{field}.role")


def _validate_expected(case_id: str, expected: dict[str, Any]) -> None:
    if "task_type" in expected:
        _validate_enum(TaskType, expected["task_type"], case_id=case_id, field="task_type")
    if "risk_level" in expected:
        _validate_enum(RiskLevel, expected["risk_level"], case_id=case_id, field="risk_level")
    if "primary_scenario" in expected:
        _validate_enum(
            AdviceScenario,
            expected["primary_scenario"],
            case_id=case_id,
            field="primary_scenario",
        )
    for value in expected.get("secondary_scenarios", []):
        _validate_enum(AdviceScenario, value, case_id=case_id, field="secondary_scenarios")
    goals = [*expected.get("goals", [])]
    if expected.get("goal") is not None:
        goals.append(expected["goal"])
    for value in goals:
        _validate_enum(AdviceGoal, value, case_id=case_id, field="goals")
    for value in expected.get("secondary_tasks", []):
        _validate_enum(TaskType, value, case_id=case_id, field="secondary_tasks")
    if expected.get("pending_task") is not None:
        _validate_enum(TaskType, expected["pending_task"], case_id=case_id, field="pending_task")
    if expected.get("llm_policy", "optional") not in {"never", "optional", "required"}:
        raise ValueError(f"路由评测案例 {case_id} 包含未知 llm_policy。")
    if expected.get("route") not in {
        None,
        "high_risk_response",
        "sensitive_risk_response",
        "clarify_intent",
        "relationship_advice",
        "date_planning",
        "out_of_scope",
        "casual_chat",
    }:
        raise ValueError(f"路由评测案例 {case_id} 包含未知 route。")
    if "clarification" in expected and not isinstance(expected["clarification"], bool):
        raise ValueError(f"路由评测案例 {case_id} 的 clarification 必须是 bool。")
    if "clarification_exhausted" in expected and not isinstance(
        expected["clarification_exhausted"], bool
    ):
        raise ValueError(f"路由评测案例 {case_id} 的 clarification_exhausted 必须是 bool。")
    if "pending_continuation" in expected and not isinstance(
        expected["pending_continuation"], bool
    ):
        raise ValueError(f"路由评测案例 {case_id} 的 pending_continuation 必须是 bool。")
    if "slots" in expected:
        if not isinstance(expected["slots"], dict):
            raise ValueError(f"路由评测案例 {case_id} 的 slots 必须是 object。")
        try:
            DatePlanSlots.model_validate(expected["slots"])
        except ValueError as exc:
            raise ValueError(f"路由评测案例 {case_id} 的 slots 结构无效。") from exc
    rejected = expected.get("slot_rejected_fields")
    if rejected is not None and not isinstance(rejected, (dict, list)):
        raise ValueError(f"路由评测案例 {case_id} 的 slot_rejected_fields 必须是 object 或 list。")


def _validate_enum(
    enum_type: type[AdviceGoal | AdviceScenario | MessageRole | RiskLevel | TaskType],
    value: Any,
    *,
    case_id: str,
    field: str,
) -> None:
    try:
        enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"路由评测案例 {case_id} 的 {field} 枚举值无效：{value!r}。") from exc


def _average_metric(rows: list[dict[str, int | float]], key: str) -> float:
    return round(mean(float(row[key]) for row in rows), 4) if rows else 0.0


def _code_revision() -> dict[str, str | bool | None]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            encoding="utf-8",
            timeout=2,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            encoding="utf-8",
            timeout=2,
        ).stdout
    except (FileNotFoundError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}
    return {"commit": commit or None, "dirty": bool(status.strip())}


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ranked = sorted(values)
    index = min(len(ranked) - 1, max(0, int((len(ranked) - 1) * percentile)))
    return ranked[index]

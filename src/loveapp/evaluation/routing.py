import json
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

from loveapp.application.routing import HybridRouter, route_by_rules
from loveapp.domain.enums import RiskLevel, TaskType
from loveapp.domain.memory import MessageRole, StoredMessage
from loveapp.domain.routing import RouteCorrection, RouteInput, RouteResult
from loveapp.safety import SafetyPolicy


class RecordingRouteCorrector:
    """Deterministic corrector used to measure Router trigger and merge policy."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._next_correction: RouteCorrection | None = None

    def prepare(self, correction: RouteCorrection) -> None:
        self._next_correction = correction

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
        return self._next_correction or _correction_from_rules(rule_result)

    async def aclose(self) -> None:
        return None


async def evaluate_routing_conversations(path: Path) -> dict[str, Any]:
    cases = _load_cases(path)
    rows: list[dict[str, Any]] = []
    turn_count = 0
    passed_turns = 0
    task_correct = 0
    task_cases = 0
    scenario_correct = 0
    scenario_cases = 0
    secondary_scenario_hits = 0
    secondary_scenario_expected = 0
    risk_correct = 0
    risk_cases = 0
    high_risk_true_positive = 0
    high_risk_cases = 0
    goal_true_positive = 0
    goal_false_positive = 0
    goal_false_negative = 0
    goal_annotation_turn_count = 0
    llm_calls = 0
    never_policy_violations = 0
    required_policy_misses = 0
    latencies: list[float] = []
    context_turn_count = 0
    context_route_correct = 0
    context_route_cases = 0
    multi_turn_case_count = 0

    for case in cases:
        turns = case["turns"]
        if len(turns) > 1:
            multi_turn_case_count += 1
        history = _build_messages(case["id"], case.get("seed_messages", []))
        active_task: TaskType | None = None
        corrector = RecordingRouteCorrector()
        router = HybridRouter(SafetyPolicy(), corrector)
        case_rows: list[dict[str, Any]] = []

        for turn in turns:
            turn_count += 1
            if history:
                context_turn_count += 1
            if "active_task" in turn:
                active_task = _task_value(turn["active_task"])
            route_input = RouteInput(
                latest_query=turn["query"],
                recent_messages=history[-20:],
                active_task=active_task,
                forced_task=_task_value(turn.get("forced_task")),
            )
            rules = route_by_rules(route_input)
            corrector.prepare(_correction_for_turn(turn.get("llm_correction"), rules))
            calls_before = len(corrector.calls)
            started = perf_counter()
            result = await router.route(route_input)
            duration_ms = (perf_counter() - started) * 1000
            calls_this_turn = len(corrector.calls) - calls_before
            llm_calls += calls_this_turn
            latencies.append(duration_ms)

            expected = turn.get("expected", {})
            checks = _route_checks(result, expected)
            if history:
                context_route_cases += 1
                context_route_correct += int(all(checks.values()))
            policy = expected.get("llm_policy", "optional")
            policy_passed = (
                policy == "optional"
                or (policy == "never" and calls_this_turn == 0)
                or (policy == "required" and calls_this_turn > 0)
            )
            checks["llm_policy"] = policy_passed
            passed = all(checks.values())
            passed_turns += int(passed)

            if "task_type" in expected:
                task_cases += 1
                task_correct += int(result.task_type.value == expected["task_type"])
            if "primary_scenario" in expected:
                scenario_cases += 1
                scenario_correct += int(
                    result.primary_scenario is not None
                    and result.primary_scenario.value == expected["primary_scenario"]
                )
            if "secondary_scenarios" in expected:
                expected_secondary = set(expected["secondary_scenarios"])
                actual_secondary = {value.value for value in result.secondary_scenarios}
                secondary_scenario_hits += len(expected_secondary & actual_secondary)
                secondary_scenario_expected += len(expected_secondary)
            if "risk_level" in expected:
                risk_cases += 1
                predicted_high = result.risk_level == RiskLevel.HIGH
                expected_high = expected["risk_level"] == RiskLevel.HIGH.value
                risk_correct += int(predicted_high == expected_high)
                high_risk_cases += int(expected_high)
                high_risk_true_positive += int(predicted_high and expected_high)

            if "goals" in expected or "goal" in expected:
                goal_annotation_turn_count += 1
                expected_goals = set(expected.get("goals", []))
                if expected.get("goal"):
                    expected_goals.add(expected["goal"])
                actual_goals = {
                    goal.value
                    for goal in [result.primary_goal, *result.secondary_goals]
                    if goal is not None
                }
                goal_true_positive += len(expected_goals & actual_goals)
                goal_false_positive += len(actual_goals - expected_goals)
                goal_false_negative += len(expected_goals - actual_goals)
            if policy == "never":
                never_policy_violations += int(calls_this_turn > 0)
            if policy == "required":
                required_policy_misses += int(calls_this_turn == 0)

            case_rows.append(
                {
                    "turn_id": turn.get("turn_id"),
                    "passed": passed,
                    "checks": checks,
                    "duration_ms": round(duration_ms, 3),
                    "context_message_count": len(history),
                    "active_task": active_task.value if active_task else None,
                    "llm_calls": calls_this_turn,
                    "actual": _route_summary(result),
                }
            )

            history.append(_message(case["id"], len(history) + 1, "user", turn["query"]))
            assistant_content = turn.get("assistant")
            if assistant_content:
                history.append(
                    _message(case["id"], len(history) + 1, "assistant", assistant_content)
                )
            active_task = (
                active_task
                if result.task_type == TaskType.GENERAL_CHAT
                else result.task_type
            )

        rows.append(
            {
                "id": case["id"],
                "turn_count": len(turns),
                "passed": all(row["passed"] for row in case_rows),
                "turns": case_rows,
            }
        )

    goal_precision = _ratio(goal_true_positive, goal_true_positive + goal_false_positive)
    goal_recall = _ratio(goal_true_positive, goal_true_positive + goal_false_negative)
    return {
        "schema_version": 1,
        "dataset": str(path),
        "case_count": len(cases),
        "turn_count": turn_count,
        "multi_turn_case_count": multi_turn_case_count,
        "context_turn_count": context_turn_count,
        "context_route_accuracy": _ratio(context_route_correct, context_route_cases),
        "pass_rate": _ratio(passed_turns, turn_count),
        "conversation_pass_rate": _ratio(sum(row["passed"] for row in rows), len(rows)),
        "task_accuracy": _ratio(task_correct, task_cases),
        "primary_scenario_accuracy": _ratio(scenario_correct, scenario_cases),
        "secondary_scenario_recall": _ratio(
            secondary_scenario_hits,
            secondary_scenario_expected,
        ),
        "risk_accuracy": _ratio(risk_correct, risk_cases),
        "high_risk_recall": _ratio(high_risk_true_positive, high_risk_cases),
        "goal_micro_precision": goal_precision,
        "goal_micro_recall": goal_recall,
        "goal_micro_f1": _f1(goal_precision, goal_recall),
        "goal_annotation_turn_count": goal_annotation_turn_count,
        "llm_call_count": llm_calls,
        "llm_call_rate": _ratio(llm_calls, turn_count),
        "never_policy_violations": never_policy_violations,
        "required_policy_misses": required_policy_misses,
        "mean_policy_latency_ms": round(mean(latencies), 3) if latencies else 0,
        "p95_policy_latency_ms": round(_percentile(latencies, 0.95), 3),
        "cases": rows,
    }


def _route_checks(result: RouteResult, expected: dict[str, Any]) -> dict[str, bool]:
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
        checks["excluded_scenarios"] = not (
            set(expected["excluded_scenarios"]) & actual
        )
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
    return checks


def _route_branch(result: RouteResult) -> str:
    if result.risk_level == RiskLevel.HIGH:
        return "high_risk_response"
    return {
        TaskType.RELATIONSHIP_ADVICE: "relationship_advice",
        TaskType.DATE_PLANNING: "date_planning",
        TaskType.GENERAL_CHAT: "casual_chat",
    }[result.task_type]


def _route_summary(result: RouteResult) -> dict[str, Any]:
    return {
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
        "date_request_mode": result.date_request_mode.value,
        "date_intent": result.date_intent.value,
        "date_mutation": result.date_mutation.value,
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


def _load_cases(path: Path) -> list[dict[str, Any]]:
    cases = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    for case in cases:
        if not case.get("id") or not isinstance(case.get("turns"), list) or not case["turns"]:
            raise ValueError("每个路由评测案例必须包含 id 和非空 turns。")
        for turn in case["turns"]:
            if not turn.get("query") or not turn.get("expected"):
                raise ValueError(f"路由评测案例 {case['id']} 存在无 query/expected 的 turn。")
    return cases


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

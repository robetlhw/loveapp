import json
from collections.abc import Callable
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

from loveapp.application.routing import route_by_rules
from loveapp.bootstrap import build_memory_container, build_qdrant_store
from loveapp.core.config import Settings
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.enums import AdviceGoal, AdviceScenario, RiskLevel, TaskType
from loveapp.domain.knowledge import KnowledgeFilters
from loveapp.domain.memory import MessageRole, StoredMessage
from loveapp.domain.routing import RouteInput, RouteResult
from loveapp.safety import SafetyPolicy

ProgressCallback = Callable[[str], None]


async def run_baseline(
    settings: Settings,
    *,
    output_path: Path,
    include_rag: bool = True,
    include_live_memory: bool = True,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "configuration": {
            "rag_backend": settings.rag_backend,
            "embedding_model": settings.embedding_model,
            "embedding_device": settings.embedding_device,
            "memory_extraction_provider": settings.memory_extraction_provider,
            "memory_extraction_model": settings.memory_extraction_model
            or settings.llm_model,
            "memory_extraction_timeout_seconds": settings.memory_extraction_timeout_seconds,
            "memory_extraction_max_retries": settings.memory_extraction_max_retries,
            "memory_extraction_max_tokens": settings.memory_extraction_max_tokens,
            "memory_extraction_thinking": settings.memory_extraction_thinking,
            "memory_extraction_strong_model": settings.memory_extraction_strong_model
            or settings.llm_model,
            "memory_tentative_min_confidence": settings.memory_tentative_min_confidence,
            "memory_belief_min_confidence": settings.memory_belief_min_confidence,
        },
        "metrics": {},
    }

    _notify(progress, "评测规则路由")
    report["metrics"]["routing_rules"] = evaluate_routing_rules(
        Path("evals/routing/cases_v1.jsonl")
    )
    _notify(progress, "评测高风险规则")
    report["metrics"]["safety"] = evaluate_safety(
        Path("evals/safety/cases_v1.jsonl")
    )
    if include_rag:
        _notify(progress, "评测 Qdrant RAG")
        report["metrics"]["rag"] = await evaluate_rag(
            settings,
            Path("evals/rag/cases_v1.jsonl"),
            progress=progress,
        )
    else:
        report["metrics"]["rag"] = {"status": "skipped"}
    if include_live_memory:
        _notify(progress, "评测真实记忆抽取与污染率")
        report["metrics"]["memory"] = await evaluate_memory_pollution(
            settings,
            Path("evals/memory/gate_v1.jsonl"),
            progress=progress,
        )
    else:
        report["metrics"]["memory"] = {"status": "skipped"}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def evaluate_routing_rules(path: Path) -> dict[str, Any]:
    cases = _load_jsonl(path)
    rows: list[dict[str, Any]] = []
    task_correct = 0
    scenario_correct = 0
    scenario_cases = 0
    goal_true_positive = 0
    goal_false_positive = 0
    goal_false_negative = 0

    for case in cases:
        route_input = RouteInput(
            latest_query=case["latest_query"],
            recent_messages=_messages(case.get("recent_messages", []), case["id"]),
            active_task=(TaskType(case["active_task"]) if case.get("active_task") else None),
        )
        result = route_by_rules(route_input)
        safety = SafetyPolicy().assess(result.normalized_query)
        result = result.model_copy(
            update={
                "risk_level": safety.risk_level,
                "risk_reasons": safety.reasons,
            }
        )
        expected = case["expected"]
        checks = _route_checks(result, expected)
        if "task_type" in expected:
            task_correct += int(result.task_type.value == expected["task_type"])
        if "primary_scenario" in expected:
            scenario_cases += 1
            scenario_correct += int(
                result.primary_scenario is not None
                and result.primary_scenario.value == expected["primary_scenario"]
            )
        expected_goals = set(expected.get("goals", []))
        if expected.get("goal"):
            expected_goals.add(expected["goal"])
        actual_goals = {
            goal.value
            for goal in [result.primary_goal, *result.secondary_goals]
            if goal is not None
        }
        goal_true_positive += len(expected_goals & actual_goals)
        goal_false_positive += len(actual_goals - expected_goals) if expected_goals else 0
        goal_false_negative += len(expected_goals - actual_goals)
        rows.append(
            {
                "id": case["id"],
                "passed": all(checks.values()),
                "checks": checks,
                "actual": _route_summary(result),
            }
        )

    goal_precision = _ratio(goal_true_positive, goal_true_positive + goal_false_positive)
    goal_recall = _ratio(goal_true_positive, goal_true_positive + goal_false_negative)
    return {
        "case_count": len(cases),
        "pass_rate": _ratio(sum(row["passed"] for row in rows), len(rows)),
        "task_accuracy": _ratio(task_correct, sum("task_type" in c["expected"] for c in cases)),
        "primary_scenario_accuracy": _ratio(scenario_correct, scenario_cases),
        "goal_micro_precision": goal_precision,
        "goal_micro_recall": goal_recall,
        "goal_micro_f1": _f1(goal_precision, goal_recall),
        "cases": rows,
    }


def evaluate_safety(path: Path) -> dict[str, Any]:
    policy = SafetyPolicy()
    cases = _load_jsonl(path)
    true_positive = false_positive = true_negative = false_negative = 0
    rows: list[dict[str, Any]] = []
    category_counts: dict[str, dict[str, int]] = {}

    for case in cases:
        assessment = policy.assess(case["text"])
        predicted = assessment.risk_level == RiskLevel.HIGH
        expected = bool(case["expected_high_risk"])
        if predicted and expected:
            true_positive += 1
        elif predicted:
            false_positive += 1
        elif expected:
            false_negative += 1
        else:
            true_negative += 1
        category = case["category"]
        counts = category_counts.setdefault(category, {"total": 0, "correct": 0})
        counts["total"] += 1
        counts["correct"] += int(predicted == expected)
        rows.append(
            {
                "id": case["id"],
                "expected_high_risk": expected,
                "predicted_high_risk": predicted,
                "reasons": assessment.reasons,
                "passed": predicted == expected,
            }
        )

    recall = _ratio(true_positive, true_positive + false_negative)
    precision = _ratio(true_positive, true_positive + false_positive)
    return {
        "case_count": len(cases),
        "high_risk_recall": recall,
        "high_risk_precision": precision,
        "specificity": _ratio(true_negative, true_negative + false_positive),
        "f1": _f1(precision, recall),
        "confusion_matrix": {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "true_negative": true_negative,
            "false_negative": false_negative,
        },
        "category_accuracy": {
            category: _ratio(value["correct"], value["total"])
            for category, value in category_counts.items()
        },
        "cases": rows,
    }


async def evaluate_rag(
    settings: Settings,
    path: Path,
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    cases = _load_jsonl(path)
    store = build_qdrant_store(settings)
    rows: list[dict[str, Any]] = []
    try:
        for index, case in enumerate(cases, start=1):
            _notify(progress, f"RAG {index}/{len(cases)}: {case['id']}")
            filters = KnowledgeFilters(
                scenario=AdviceScenario(case["scenario"]),
                goals=[AdviceGoal(value) for value in case.get("goals", [])],
            )
            started = perf_counter()
            trace = ExecutionTrace()
            matches = await store.search(
                case["query"],
                filters=filters,
                limit=10,
                trace=trace,
            )
            duration_ms = (perf_counter() - started) * 1000
            ids = [match.document.id for match in matches]
            relevant = set(case["relevant_ids"])
            first_rank = next(
                (rank for rank, document_id in enumerate(ids, start=1) if document_id in relevant),
                None,
            )
            rows.append(
                {
                    "id": case["id"],
                    "first_relevant_rank": first_rank,
                    "recall_at_3": first_rank is not None and first_rank <= 3,
                    "recall_at_5": first_rank is not None and first_rank <= 5,
                    "reciprocal_rank": 1 / first_rank if first_rank else 0,
                    "duration_ms": round(duration_ms, 3),
                    "returned_ids": ids,
                    "trace": [record.model_dump(mode="json") for record in trace.snapshot()],
                }
            )
    finally:
        await store.aclose()

    return {
        "case_count": len(cases),
        "recall_at_3": _ratio(sum(row["recall_at_3"] for row in rows), len(rows)),
        "recall_at_5": _ratio(sum(row["recall_at_5"] for row in rows), len(rows)),
        "mrr": round(mean(row["reciprocal_rank"] for row in rows), 4) if rows else 0,
        "mean_latency_ms": round(mean(row["duration_ms"] for row in rows), 3) if rows else 0,
        "cases": rows,
    }


async def evaluate_memory_pollution(
    settings: Settings,
    path: Path,
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    cases = _load_jsonl(path)
    memory_settings = settings.model_copy(update={"memory_backend": "memory"})
    container = build_memory_container(memory_settings)
    rows: list[dict[str, Any]] = []
    try:
        for index, case in enumerate(cases, start=1):
            _notify(progress, f"Memory {index}/{len(cases)}: {case['id']}")
            started = perf_counter()
            trace = ExecutionTrace()
            result = await container.memory_service.remember_text(
                user_id="baseline-user",
                relationship_id=f"baseline-{case['id']}",
                conversation_id=f"baseline-{case['id']}",
                text=case["text"],
                trace=trace,
            )
            duration_ms = (perf_counter() - started) * 1000
            stored_count = len(result.saved)
            trace_records = [record.model_dump(mode="json") for record in trace.snapshot()]
            trace_summary = _memory_trace_summary(trace_records)
            rows.append(
                {
                    "id": case["id"],
                    "should_store": bool(case["should_store"]),
                    "stored_count": stored_count,
                    "stored": stored_count > 0,
                    "duration_ms": round(duration_ms, 3),
                    "extraction_error": result.extraction_error,
                    "gate_should_extract": (
                        result.gate_decision.should_extract
                        if result.gate_decision is not None
                        else None
                    ),
                    "gate_reason": (
                        result.gate_decision.reason.value
                        if result.gate_decision is not None
                        else None
                    ),
                    **trace_summary,
                    "trace": trace_records,
                }
            )
    finally:
        await container.aclose()

    successful = [row for row in rows if row["extraction_error"] is None]
    negative = [row for row in successful if not row["should_store"]]
    positive = [row for row in successful if row["should_store"]]
    gate_true_positive = sum(
        row["gate_should_extract"] is True for row in positive
    )
    gate_true_negative = sum(
        row["gate_should_extract"] is False for row in negative
    )
    model_invocation_count = sum(row["flash_call_count"] for row in successful)
    structural_retry_count = sum(
        sum(
            record["name"] == "memory_model_attempt_1"
            for record in row["trace"]
        )
        > 1
        for row in successful
    )
    flash_calls = sum(row["flash_call_count"] for row in successful)
    strong_upgrades = sum(row["strong_upgrade_count"] for row in successful)
    direct_successes = sum(row["flash_direct_success_count"] for row in successful)
    local_repairs = sum(row["local_repair_count"] for row in successful)
    discarded_invalid = sum(row["discarded_invalid_count"] for row in successful)
    latencies = [row["duration_ms"] for row in successful]
    return {
        "case_count": len(cases),
        "completed_count": len(successful),
        "memory_pollution_rate": _ratio(sum(row["stored"] for row in negative), len(negative)),
        "store_recall": _ratio(sum(row["stored"] for row in positive), len(positive)),
        "gate_recall": _ratio(gate_true_positive, len(positive)),
        "gate_specificity": _ratio(gate_true_negative, len(negative)),
        "model_invocation_count": model_invocation_count,
        "structural_retry_rate": _ratio(structural_retry_count, model_invocation_count),
        "flash_direct_success_count": direct_successes,
        "flash_direct_success_rate": _ratio(direct_successes, flash_calls),
        "local_repair_count": local_repairs,
        "local_repair_rate": _ratio(local_repairs, flash_calls),
        "strong_upgrade_count": strong_upgrades,
        "strong_upgrade_rate": _ratio(strong_upgrades, flash_calls),
        "discarded_invalid_count": discarded_invalid,
        "discarded_invalid_rate": _ratio(discarded_invalid, flash_calls),
        "negative_mean_latency_ms": (
            round(mean(row["duration_ms"] for row in negative), 3) if negative else 0
        ),
        "positive_mean_latency_ms": (
            round(mean(row["duration_ms"] for row in positive), 3) if positive else 0
        ),
        "mean_latency_ms": (
            round(mean(row["duration_ms"] for row in successful), 3) if successful else 0
        ),
        "p50_latency_ms": round(_percentile(latencies, 0.50), 3),
        "p95_latency_ms": round(_percentile(latencies, 0.95), 3),
        "cases": rows,
    }


def _route_checks(result: RouteResult, expected: dict[str, Any]) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    if "task_type" in expected:
        checks["task_type"] = result.task_type.value == expected["task_type"]
    if "risk_level" in expected:
        checks["risk_level"] = result.risk_level.value == expected["risk_level"]
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
    if "date_plan" in expected:
        actual_slots = result.date_plan.model_dump(mode="json", exclude_none=True)
        checks["date_plan"] = all(
            actual_slots.get(key) == value for key, value in expected["date_plan"].items()
        )
    return checks


def _route_summary(result: RouteResult) -> dict[str, Any]:
    return {
        "task_type": result.task_type.value,
        "secondary_tasks": [value.value for value in result.secondary_tasks],
        "primary_scenario": result.primary_scenario.value if result.primary_scenario else None,
        "secondary_scenarios": [value.value for value in result.secondary_scenarios],
        "primary_goal": result.primary_goal.value if result.primary_goal else None,
        "secondary_goals": [value.value for value in result.secondary_goals],
        "risk_level": result.risk_level.value,
    }


def _messages(values: list[dict[str, str]], case_id: str) -> list[StoredMessage]:
    return [
        StoredMessage(
            id=f"{case_id}-{index}",
            conversation_id=case_id,
            user_id="baseline-user",
            relationship_id="baseline-relationship",
            role=MessageRole(value["role"]),
            content=value["content"],
        )
        for index, value in enumerate(values, start=1)
    ]


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def _memory_trace_summary(records: list[dict[str, Any]]) -> dict[str, int]:
    attempts = [
        record
        for record in records
        if record["name"] == "memory_model_attempt_1"
        or record["name"].startswith("memory_model_strong_attempt_")
    ]
    flash_attempts = [record for record in attempts if record["name"] == "memory_model_attempt_1"]
    strong_attempts = [
        record
        for record in attempts
        if record["name"].startswith("memory_model_strong_attempt_")
    ]
    decision_records = [
        record for record in records if record["name"] == "memory_extraction_upgrade_gate"
    ]
    return {
        "flash_call_count": len(flash_attempts),
        "flash_direct_success_count": sum(
            record["status"] == "completed"
            and record.get("details", {}).get("repair_status") == "direct"
            for record in flash_attempts
        ),
        "local_repair_count": sum(
            record["status"] == "completed"
            and record.get("details", {}).get("repair_status") == "local_repair"
            for record in flash_attempts
        ),
        "strong_upgrade_count": len(strong_attempts),
        "discarded_invalid_count": sum(
            bool(record.get("details", {}).get("discard_reason"))
            for record in [*attempts, *decision_records]
        ),
    }


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, ceil(len(ordered) * quantile) - 1))
    return ordered[index]


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0


def _notify(callback: ProgressCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)

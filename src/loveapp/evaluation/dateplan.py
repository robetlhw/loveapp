"""Deterministic DatePlan workflow evaluation.

The evaluator deliberately drives the production route, patch and workflow
boundaries without calling an external model.  A case has its own task store
and relationship scope, so state cannot leak between scenarios.  Dataset
expectations are kept outside this module and are never rewritten from the
observed result.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from loveapp.adapters.date_tasks import InMemoryDatePlanningTaskStore
from loveapp.adapters.maps.demo import DemoMapProvider
from loveapp.adapters.memory.in_memory import InMemoryMemoryStore
from loveapp.agents.date_planner import DatePlanningAgent
from loveapp.agents.date_workflow import DatePlanningWorkflow
from loveapp.application.memory import MemoryService, NoOpMemoryExtractor
from loveapp.application.routing import route_by_rules
from loveapp.application.runtime_context import RuntimeContextBuilder
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.conversation import ConversationRequest
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.date_workflow import DatePlanningWorkflowInput
from loveapp.domain.enums import TaskType
from loveapp.domain.routing import RouteInput

_DEFAULT_DATASET = Path("evals/dateplan/dateplan_cases_v1.jsonl")
_DEFAULT_REFERENCE_TIME = datetime(2026, 8, 20, 12, tzinfo=UTC)


class _ReferenceDate(date):
    """date.today() replacement used only while evaluating a case."""

    reference: date = _DEFAULT_REFERENCE_TIME.date()

    @classmethod
    def today(cls) -> _ReferenceDate:
        return cls.reference


@contextmanager
def _fixed_reference_time(reference_time: datetime) -> Iterator[None]:
    """Inject the case clock into date parsing, context and workflow traces."""

    _ReferenceDate.reference = reference_time.date()
    # DateFactParser imports ``date`` directly.  Patching this module keeps
    # relative-date evaluation deterministic without changing production code.
    def clock() -> datetime:
        return reference_time

    with (
        patch("loveapp.application.date_planning.fact_parsing.date", _ReferenceDate),
        patch("loveapp.application.runtime_context.utc_now", clock),
        patch("loveapp.agents.date_workflow.utc_now", clock),
    ):
        yield


async def evaluate_dateplan(
    path: Path = _DEFAULT_DATASET,
    *,
    case_id: str | None = None,
    category: str | None = None,
    output: Path | None = None,
    trace_dir: Path | None = None,
) -> dict[str, Any]:
    """Run DatePlan scenarios and return a JSON-serializable report.

    ``case_id`` and ``category`` are filters, not alternate expectations.  A
    selected subset still carries the dataset hash and all per-turn traces.
    """

    raw = path.read_bytes()
    cases = _load_cases(raw)
    if case_id is not None:
        cases = [case for case in cases if case["id"] == case_id]
        if not cases:
            raise ValueError(f"unknown DatePlan case: {case_id}")
    if category is not None:
        cases = [case for case in cases if case.get("category") == category]
        if not cases:
            raise ValueError(f"unknown DatePlan category: {category}")

    rows = [await _evaluate_case(case) for case in cases]
    counters = Counter()
    for row in rows:
        counters.update(row["metrics"])
    report: dict[str, Any] = {
        "schema_version": 1,
        "version": "dateplan-v1",
        "dataset": str(path),
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "case_filter": case_id,
        "category_filter": category,
        "reference_time_policy": (
            "Each case injects its reference_time into fact_parsing.date.today(); "
            "absolute dates remain stable even when this parser is not used."
        ),
        "scenario_count": len(rows),
        "case_count": len(rows),
        "turn_count": counters["turn_count"],
        "passed_scenarios": counters["passed_scenario"],
        "passed_scenario_count": counters["passed_scenario"],
        "scenario_pass_rate": _ratio(counters["passed_scenario"], len(rows)),
        "patch_accuracy": _ratio(counters["patch_correct"], counters["patch_expected"]),
        "state_preservation_accuracy": _ratio(
            counters["preserve_correct"], counters["preserve_expected"]
        ),
        "validation_accuracy": _ratio(
            counters["validation_correct"], counters["validation_expected"]
        ),
        "final_plan_completion_rate": _ratio(
            counters["final_completion_correct"], counters["final_completion_expected"]
        ),
        "metrics": {
            "patch_expected": counters["patch_expected"],
            "patch_correct": counters["patch_correct"],
            "preserve_expected": counters["preserve_expected"],
            "preserve_correct": counters["preserve_correct"],
            "validation_expected": counters["validation_expected"],
            "validation_correct": counters["validation_correct"],
            "completion_expected": counters["completion_expected"],
            "completion_correct": counters["completion_correct"],
            "final_completion_expected": counters["final_completion_expected"],
            "final_completion_correct": counters["final_completion_correct"],
            "route_mismatch_count": counters["route_incorrect"],
            "unhandled_error_count": counters["unhandled_error"],
        },
        "cases": rows,
    }
    if output is not None:
        _write_json(output, report)
    if trace_dir is not None:
        trace_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        _write_json(trace_dir / f"dateplan-{stamp}.json", report)
    return report


async def _evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    reference_time = _parse_datetime(case.get("reference_time"))
    clock_value = [reference_time]

    def clock() -> datetime:
        return clock_value[0]

    # Every case owns all stateful components.  This is intentionally not
    # shared with other cases even when a filtered run executes one at a time.
    memory_store = InMemoryMemoryStore(clock=clock)
    memory_service = MemoryService(
        memory_store,
        NoOpMemoryExtractor(),
        clock=clock,
    )
    task_store = InMemoryDatePlanningTaskStore()
    context_builder = RuntimeContextBuilder(task_store)
    planner = DatePlanningAgent(DemoMapProvider(), memory_service)
    workflow = DatePlanningWorkflow(planner, task_store)
    user_id = str(case.get("user_id") or f"dateplan-{case['id'].casefold()}")
    relationship_id = str(case.get("relationship_id") or f"relationship-{case['id'].casefold()}")
    conversation_id = str(case.get("conversation_id") or f"conversation-{case['id'].casefold()}")

    current: DatePlanningTaskState | None = None
    turn_rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    metrics = Counter()
    for index, turn in enumerate(case["turns"], start=1):
        text = str(turn["input"])
        turn_reference = _parse_datetime(turn.get("reference_time"), fallback=reference_time)
        clock_value[0] = turn_reference
        before = current.model_copy(deep=True) if current is not None else None
        trace = ExecutionTrace()
        route: Any = None
        result: Any = None
        error: str | None = None
        context_before = None
        context_after = None
        with _fixed_reference_time(turn_reference):
            try:
                request = ConversationRequest(
                    user_id=user_id,
                    relationship_id=relationship_id,
                    conversation_id=conversation_id,
                    query=text,
                )
                context_before = await context_builder.build(
                    request,
                    active_task=(TaskType.DATE_PLANNING if current is not None else None),
                    date_task_state=current,
                    trace=trace,
                )
                route = route_by_rules(
                    RouteInput(
                        latest_query=text,
                        active_task=(TaskType.DATE_PLANNING if current is not None else None),
                        date_task_state=current,
                        runtime_context=context_before,
                    )
                )
                if route.task_type == TaskType.DATE_PLANNING:
                    result = await workflow.run(
                        DatePlanningWorkflowInput(
                            request=request,
                            route=route,
                            current_task_state=current,
                        ),
                        trace=trace,
                    )
                    current = result.task_state
                else:
                    # A non-DatePlan route is observable and must not mutate
                    # task state in this workflow-only evaluator.
                    result = None
                context_after = await context_builder.build(
                    request,
                    active_task=(TaskType.DATE_PLANNING if current is not None else None),
                    date_task_state=current,
                    trace=trace,
                )
            except Exception as exc:  # keep a failed case traceable
                error = f"{type(exc).__name__}: {exc}"
                metrics["unhandled_error"] += 1

        after = current.model_copy(deep=True) if current is not None else None
        actual = _actual_turn(route, result, trace, error, context_before, context_after, before)
        expected = dict(turn.get("expected") or {})
        assertions = _assert_turn(expected, actual, before, after)
        for assertion in assertions:
            _record_assertion_metric(metrics, assertion)
            if not assertion["passed"]:
                failures.append(
                    {
                        "turn": index,
                        "input": text,
                        "assertion": assertion["name"],
                        "expected": assertion["expected"],
                        "actual": assertion["actual"],
                        "attribution": assertion["attribution"],
                    }
                )
        metrics["turn_count"] += 1
        turn_rows.append(
            {
                "turn": index,
                "input": text,
                "reference_time": turn_reference.isoformat(),
                "expected": expected,
                "actual": actual,
                "assertions": assertions,
                "db_before": _state_record(before),
                "db_after": _state_record(after),
                "trace": [_trace_record(record) for record in trace.snapshot()],
            }
        )

    final_expected = dict(case.get("final") or {})
    final_actual = _final_record(current)
    final_assertions = _assert_final(final_expected, final_actual)
    for assertion in final_assertions:
        _record_assertion_metric(metrics, assertion)
        if not assertion["passed"]:
            failures.append(
                {
                    "turn": None,
                    "input": None,
                    "assertion": assertion["name"],
                    "expected": assertion["expected"],
                    "actual": assertion["actual"],
                    "attribution": assertion["attribution"],
                }
            )
    if "plan_present" in final_expected:
        metrics["final_completion_expected"] += 1
        metrics["final_completion_correct"] += int(
            final_expected["plan_present"] == final_actual["plan_present"]
        )
    passed = not failures
    metrics["passed_scenario"] = int(passed)
    return {
        "id": case["id"],
        "category": case.get("category", "uncategorized"),
        "description": case.get("description"),
        "reference_time": reference_time.isoformat(),
        "passed": passed,
        "final_expected": final_expected,
        "final_actual": final_actual,
        "failures": failures,
        "metrics": dict(metrics),
        "turns": turn_rows,
    }


def _actual_turn(
    route: Any,
    result: Any,
    trace: ExecutionTrace,
    error: str | None,
    context_before: Any,
    context_after: Any,
    state_before: DatePlanningTaskState | None,
) -> dict[str, Any]:
    validation = next(
        (record.details for record in trace.snapshot() if record.name == "date_plan_validation"),
        None,
    )
    return {
        "route": route.task_type.value if route is not None else None,
        "date_intent": route.date_intent.value if route is not None else None,
        "date_mutation": route.date_mutation.value if route is not None else None,
        "date_request_mode": route.date_request_mode.value if route is not None else None,
        "date_patch": (
            _non_empty_model(route.date_patch.model_dump(mode="json"))
            if route is not None and route.date_patch is not None
            else None
        ),
        "date_plan": (
            _non_empty_model(route.date_plan.model_dump(mode="json")) if route is not None else None
        ),
        "date_operations": (
            [operation.model_dump(mode="json") for operation in route.date_operations]
            if route is not None
            else []
        ),
        "date_missing_fields": list(route.date_missing_fields) if route is not None else [],
        "needs_clarification": bool(route.needs_clarification) if route is not None else False,
        "plan_committed": bool(result.plan_committed) if result is not None else False,
        "needs_workflow": route is not None and route.task_type == TaskType.DATE_PLANNING,
        "task_status": result.task_state.status.value if result is not None else None,
        "validation": (
            None
            if validation is None
            else {
                "valid": validation.get("valid"),
                "issue_codes": validation.get("issue_codes", ""),
            }
        ),
        "plan_item_count": len(result.task_state.current_plan.items)
        if result is not None and result.task_state.current_plan is not None
        else 0,
        "error": error,
        "runtime_context_before": (
            context_before.model_dump(mode="json") if context_before is not None else None
        ),
        "runtime_context_after": (
            context_after.model_dump(mode="json") if context_after is not None else None
        ),
        "trusted_context_patch_isolated": _context_matches_state(context_before, state_before),
    }


def _context_matches_state(context: Any, state: DatePlanningTaskState | None) -> bool:
    """Ensure a candidate patch was not written into the trusted pre-turn snapshot."""

    if context is None:
        return state is None
    active = context.active_date_plan
    if state is None:
        return active is None
    if not state.is_resumable:
        return active is None
    if active is None:
        return False
    return (
        active.city == state.city
        and active.date == state.date
        and active.budget == state.budget
        and active.plan_version == state.plan_version
    )


def _assert_turn(
    expected: dict[str, Any],
    actual: dict[str, Any],
    before: DatePlanningTaskState | None,
    after: DatePlanningTaskState | None,
) -> list[dict[str, Any]]:
    assertions: list[dict[str, Any]] = []
    if "route" in expected:
        assertions.append(
            _assertion(
                "route",
                expected["route"],
                actual["route"],
                metric="route",
                attribution="Router",
            )
        )
    if "patch" in expected:
        patch_expected = expected["patch"]
        patch_actual = actual.get("date_patch") or {}
        assertions.append(
            _assertion(
                "patch_fields",
                patch_expected,
                {key: patch_actual.get(key) for key in patch_expected},
                metric="patch",
                attribution="Patch extraction",
            )
        )
    if expected.get("patch_empty") is True:
        assertions.append(
            _assertion(
                "patch_empty",
                True,
                not bool(actual.get("date_patch")),
                metric="patch",
                attribution="Patch extraction",
            )
        )
    if "validation" in expected:
        actual_validation = actual.get("validation")
        observed = None if actual_validation is None else actual_validation.get("valid")
        assertions.append(
            _assertion(
                "validation",
                expected["validation"],
                observed,
                metric="validation",
                attribution="DatePlanValidator",
            )
        )
    if "status" in expected:
        assertions.append(
            _assertion(
                "task_status",
                expected["status"],
                actual.get("task_status"),
                metric="state",
                attribution="Workflow state transition",
            )
        )
    if "plan_committed" in expected:
        assertions.append(
            _assertion(
                "plan_committed",
                expected["plan_committed"],
                actual.get("plan_committed"),
                metric="completion",
                attribution="Workflow commit",
            )
        )
    if "preserve" in expected and before is not None and after is not None:
        for field in expected["preserve"]:
            before_value = getattr(before, field, None)
            after_value = getattr(after, field, None)
            assertions.append(
                _assertion(
                    f"preserve_{field}",
                    _json_value(before_value),
                    _json_value(after_value),
                    metric="preserve",
                    attribution="DatePlanPatchApplier",
                )
            )
    if "missing_fields" in expected:
        assertions.append(
            _assertion(
                "missing_fields",
                sorted(expected["missing_fields"]),
                sorted(actual.get("date_missing_fields", [])),
                metric="state",
                attribution="DatePlan workflow completeness",
            )
        )
    return assertions


def _assert_final(expected: dict[str, Any], actual: dict[str, Any]) -> list[dict[str, Any]]:
    assertions: list[dict[str, Any]] = []
    if "status" in expected:
        assertions.append(
            _assertion(
                "final_status",
                expected["status"],
                actual.get("status"),
                metric="completion",
                attribution="Workflow final state",
            )
        )
    if "plan_present" in expected:
        assertions.append(
            _assertion(
                "final_plan_present",
                expected["plan_present"],
                actual.get("plan_present"),
                metric="completion",
                attribution="Workflow final plan snapshot",
            )
        )
    if "plan_version" in expected:
        assertions.append(
            _assertion(
                "final_plan_version",
                expected["plan_version"],
                actual.get("plan_version"),
                metric="completion",
                attribution="Workflow final state",
            )
        )
    return assertions


def _assertion(
    name: str,
    expected: Any,
    actual: Any,
    *,
    metric: str,
    attribution: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "metric": metric,
        "passed": expected == actual,
        "expected": expected,
        "actual": actual,
        "attribution": attribution,
    }


def _record_assertion_metric(metrics: Counter, assertion: dict[str, Any]) -> None:
    metric = str(assertion["metric"])
    metrics[f"{metric}_expected"] += 1
    metrics[f"{metric}_correct"] += int(assertion["passed"])
    metrics[f"{metric}_incorrect"] += int(not assertion["passed"])


def _final_record(state: DatePlanningTaskState | None) -> dict[str, Any]:
    if state is None:
        return {"status": None, "plan_present": False, "plan_version": 0}
    return {
        "status": state.status.value,
        "plan_present": state.current_plan is not None and bool(state.current_plan.items),
        "plan_version": state.plan_version,
        "city": state.city,
        "date": state.date.isoformat() if state.date else None,
        "budget": state.budget,
        "missing_fields": list(state.missing_fields),
    }


def _state_record(state: DatePlanningTaskState | None) -> dict[str, Any] | None:
    return state.model_dump(mode="json") if state is not None else None


def _trace_record(record: Any) -> dict[str, Any]:
    return {
        "name": record.name,
        "duration_ms": round(record.duration_ms, 3),
        "status": record.status.value,
        "error": record.error,
        "details": dict(record.details),
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _non_empty_model(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item is not None and item != [] and item != {} and item != ""
    }


def _load_cases(raw: bytes) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
    seen: set[str] = set()
    for case in cases:
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("every DatePlan case requires a non-empty id")
        if case_id in seen:
            raise ValueError(f"duplicate DatePlan case id: {case_id}")
        seen.add(case_id)
        turns = case.get("turns")
        if not isinstance(turns, list) or not turns:
            raise ValueError(f"DatePlan case {case_id} has no turns")
        for turn in turns:
            if not isinstance(turn, dict) or not isinstance(turn.get("input"), str):
                raise ValueError(f"DatePlan case {case_id} has an invalid turn")
    return cases


def _parse_datetime(value: str | None, *, fallback: datetime = _DEFAULT_REFERENCE_TIME) -> datetime:
    if value is None:
        return fallback
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def render_dateplan_report(report: dict[str, Any]) -> str:
    """Render the compact Markdown summary used by resume reviews."""

    lines = [
        "# DatePlan Evaluation Report",
        "",
        f"- Scenarios: {report['scenario_count']}",
        f"- Turns: {report['turn_count']}",
        f"- Scenario pass rate: {report['scenario_pass_rate']}",
        f"- Patch accuracy: {report['patch_accuracy']}",
        f"- State preservation accuracy: {report['state_preservation_accuracy']}",
        f"- Validation accuracy: {report['validation_accuracy']}",
        f"- Final plan completion rate: {report['final_plan_completion_rate']}",
        "",
        "## Scenarios",
        "",
        "| Case | Category | Result | First failure attribution |",
        "|---|---|---|---|",
    ]
    for case in report["cases"]:
        attribution = case["failures"][0]["attribution"] if case["failures"] else "-"
        result = "PASS" if case["passed"] else "FAIL"
        lines.append(f"| {case['id']} | {case['category']} | {result} | {attribution} |")
    lines.extend(["", "## Failure Cases", ""])
    failures = [case for case in report["cases"] if case["failures"]]
    if not failures:
        lines.append("No failures.")
    else:
        for case in failures:
            failure = case["failures"][0]
            lines.append(
                f"- `{case['id']}`: {failure['assertion']} "
                f"(expected `{failure['expected']}`, actual `{failure['actual']}`, "
                f"attribution `{failure['attribution']}`)"
            )
    return "\n".join(lines) + "\n"


def _main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic DatePlan evaluation")
    parser.add_argument("--dataset", type=Path, default=_DEFAULT_DATASET)
    parser.add_argument("--case", dest="case_id")
    parser.add_argument("--category")
    parser.add_argument("--output", type=Path, default=Path(".data/evals/dateplan.json"))
    parser.add_argument("--trace-dir", type=Path, default=Path(".data/evals"))
    parser.add_argument("--markdown", type=Path, default=Path("DATEPLAN_EVAL_REPORT.md"))
    args = parser.parse_args()
    report = asyncio.run(
        evaluate_dateplan(
            args.dataset,
            case_id=args.case_id,
            category=args.category,
            output=args.output,
            trace_dir=args.trace_dir,
        )
    )
    args.markdown.write_text(render_dateplan_report(report), encoding="utf-8")
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "scenario_count",
                    "turn_count",
                    "scenario_pass_rate",
                    "patch_accuracy",
                    "state_preservation_accuracy",
                    "validation_accuracy",
                    "final_plan_completion_rate",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    _main()

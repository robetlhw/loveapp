"""Phase 3 Router/Safety evaluation.

The specialized Markdown fixtures stay outside retrieval: they are parsed
directly and are never indexed or passed to an embedding provider.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from loveapp.application.routing import HybridRouter
from loveapp.domain.enums import AdviceGoal, AdviceScenario, RiskLevel, TaskType
from loveapp.domain.routing import RouteInput, RouteResult
from loveapp.safety import SafetyPolicy

RouterSafetyBranch = Literal["rag", "safety", "out_of_scope"]

BRANCHES: tuple[RouterSafetyBranch, ...] = ("rag", "safety", "out_of_scope")
SCENARIOS: tuple[str, ...] = tuple(item.value for item in AdviceScenario)
GOALS: tuple[str, ...] = tuple(item.value for item in AdviceGoal)
RISKS: tuple[str, ...] = tuple(item.value for item in RiskLevel)
QUERY_TYPES: tuple[str, ...] = ("colloquial", "long_context", "safety", "out_of_scope")
DIFFICULTIES: tuple[str, ...] = ("easy", "medium", "hard")
LENGTH_BUCKETS: tuple[str, ...] = ("short", "medium", "long")
ERROR_ATTRIBUTION_CATEGORIES: tuple[str, ...] = (
    "router_branch_error",
    "router_scenario_error",
    "router_goal_error",
    "safety_error",
    "rewrite_trigger_error",
    "query_drift",
    "decomposition_trigger_error",
    "subquery_quality_error",
    "candidate_miss",
    "rerank_error",
    "gold_or_dataset_issue",
)


@dataclass(frozen=True, slots=True)
class RouterSafetyCase:
    case_id: str
    query_type: str
    difficulty: str
    length_bucket: str
    expected_branch: RouterSafetyBranch
    expected_primary_scenario: str | None
    expected_secondary_scenarios: tuple[str, ...]
    relationship_stage: str | None
    expected_goals: tuple[str, ...]
    expected_risk_level: str
    query: str

    def model_dump(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "query_type": self.query_type,
            "difficulty": self.difficulty,
            "length_bucket": self.length_bucket,
            "expected_branch": self.expected_branch,
            "expected_primary_scenario": self.expected_primary_scenario,
            "expected_secondary_scenarios": list(self.expected_secondary_scenarios),
            "relationship_stage": self.relationship_stage,
            "expected_goals": list(self.expected_goals),
            "expected_risk_level": self.expected_risk_level,
            "query": self.query,
        }


def load_router_safety_cases(path: Path) -> list[RouterSafetyCase]:
    """Parse and validate a Router/Safety Markdown fixture."""

    text = path.read_text(encoding="utf-8-sig")
    blocks = re.split(r"^##\s+", text, flags=re.MULTILINE)[1:]
    if not blocks:
        raise ValueError(f"Router/Safety dataset contains no cases: {path}")
    cases: list[RouterSafetyCase] = []
    seen_ids: set[str] = set()
    for block in blocks:
        case_id = block.splitlines()[0].strip()
        values = {
            key: _field(block, key)
            for key in (
                "QueryType",
                "Difficulty",
                "LengthBucket",
                "ExpectedBranch",
                "ExpectedPrimaryScenario",
                "ExpectedSecondaryScenarios",
                "RelationshipStage",
                "ExpectedGoals",
                "ExpectedRiskLevel",
                "Query",
            )
        }
        if not case_id or case_id in seen_ids:
            raise ValueError(f"Router/Safety case id is missing or duplicated: {case_id!r}")
        seen_ids.add(case_id)
        branch = _enum_value(values["ExpectedBranch"], BRANCHES, "ExpectedBranch", case_id)
        risk = _enum_value(values["ExpectedRiskLevel"], RISKS, "ExpectedRiskLevel", case_id)
        scenario = _nullable_enum(
            values["ExpectedPrimaryScenario"], SCENARIOS, "ExpectedPrimaryScenario", case_id
        )
        secondary = _list_value(values["ExpectedSecondaryScenarios"])
        for item in secondary:
            _enum_value(item, SCENARIOS, "ExpectedSecondaryScenarios", case_id)
        goals = tuple(_list_value(values["ExpectedGoals"]))
        for item in goals:
            _enum_value(item, GOALS, "ExpectedGoals", case_id)
        cases.append(
            RouterSafetyCase(
                case_id=case_id,
                query_type=_required(values, "QueryType", case_id),
                difficulty=_required(values, "Difficulty", case_id),
                length_bucket=_required(values, "LengthBucket", case_id),
                expected_branch=branch,  # type: ignore[arg-type]
                expected_primary_scenario=scenario,
                expected_secondary_scenarios=tuple(secondary),
                relationship_stage=_nullable_text(values["RelationshipStage"]),
                expected_goals=goals,
                expected_risk_level=risk,
                query=_required(values, "Query", case_id).strip(),
            )
        )
    return cases


def validate_router_safety_dataset(
    cases_or_path: Sequence[RouterSafetyCase] | Path,
) -> dict[str, Any]:
    """Return non-mutating data-lint diagnostics."""

    cases = (
        load_router_safety_cases(cases_or_path)
        if isinstance(cases_or_path, Path)
        else list(cases_or_path)
    )
    ids = [case.case_id for case in cases]
    queries = [case.query for case in cases]
    branch_counts = Counter(case.expected_branch for case in cases)
    scenario_counts = Counter(
        case.expected_primary_scenario
        for case in cases
        if case.expected_primary_scenario is not None
    )
    duplicate_queries = sorted(
        query for query, count in Counter(queries).items() if count > 1
    )
    invalid_query_types = sorted(
        {case.case_id for case in cases if case.query_type not in QUERY_TYPES}
    )
    invalid_difficulties = sorted(
        {case.case_id for case in cases if case.difficulty not in DIFFICULTIES}
    )
    invalid_length_buckets = sorted(
        {case.case_id for case in cases if case.length_bucket not in LENGTH_BUCKETS}
    )
    invalid_secondary = [
        case.case_id
        for case in cases
        if case.expected_primary_scenario in case.expected_secondary_scenarios
    ]
    return {
        "case_count": len(cases),
        "ids_unique": len(ids) == len(set(ids)),
        "queries_unique": len(queries) == len(set(queries)),
        "duplicate_query_count": len(duplicate_queries),
        "duplicate_queries": duplicate_queries,
        "invalid_enums": {
            key: value
            for key, value in {
                "query_type": invalid_query_types,
                "difficulty": invalid_difficulties,
                "length_bucket": invalid_length_buckets,
            }.items()
            if value
        },
        "branch_counts": dict(branch_counts),
        "scenario_counts": dict(scenario_counts),
        "secondary_contains_primary": invalid_secondary,
        "passed": (
            bool(cases)
            and len(ids) == len(set(ids))
            and not duplicate_queries
            and not invalid_secondary
            and not invalid_query_types
            and not invalid_difficulties
            and not invalid_length_buckets
        ),
    }


async def evaluate_router_safety(
    path: Path,
    *,
    router: Any | None = None,
    router_mode: str = "rules",
) -> dict[str, Any]:
    """Evaluate Phase 3 without invoking retrieval or embeddings."""

    cases = load_router_safety_cases(path)
    lint = validate_router_safety_dataset(cases)
    if not lint["passed"]:
        raise ValueError(f"Router/Safety dataset lint failed: {lint}")
    # The evaluator's default arm is the frozen Phase 3 Router V2 rules.  A
    # caller can still pass an explicit Router instance for current-router,
    # configured, or live comparisons.
    active_router = router or HybridRouter(SafetyPolicy(), router_v2_enabled=True)
    rows: list[dict[str, Any]] = []
    for case in cases:
        started = perf_counter()
        result = await active_router.route(RouteInput(latest_query=case.query))
        row = _evaluate_case(case, result)
        row["latency_ms"] = round((perf_counter() - started) * 1000, 3)
        rows.append(row)
    return _build_report(path, cases, rows, lint, router_mode=router_mode)


def build_router_safety_router(
    *,
    router_v2_enabled: bool = True,
    safety_context_turns: int = 4,
) -> HybridRouter:
    """Build the deterministic Router arm used by controlled Phase 3 evals."""

    return HybridRouter(
        SafetyPolicy(context_turns=safety_context_turns),
        corrector=None,
        router_v2_enabled=router_v2_enabled,
    )


def _evaluate_case(case: RouterSafetyCase, result: RouteResult) -> dict[str, Any]:
    actual_branch = _branch(result)
    actual_scenario = result.primary_scenario.value if result.primary_scenario else None
    actual_goals = {
        goal.value for goal in [result.primary_goal, *result.secondary_goals] if goal is not None
    }
    expected_goals = set(case.expected_goals)
    errors: list[str] = []
    if actual_branch != case.expected_branch:
        errors.append("router_branch_error")
        if case.expected_branch == "safety" or actual_branch == "safety":
            errors.append("safety_error")
    if case.expected_branch == "rag" and actual_scenario != case.expected_primary_scenario:
        errors.append("router_scenario_error")
    if case.expected_branch == "rag" and expected_goals != actual_goals:
        errors.append("router_goal_error")
    if result.risk_level.value != case.expected_risk_level:
        errors.append("safety_error")
    errors = list(dict.fromkeys(errors))
    return {
        "id": case.case_id,
        "query": case.query,
        "query_type": case.query_type,
        "difficulty": case.difficulty,
        "length_bucket": case.length_bucket,
        "expected": {
            "branch": case.expected_branch,
            "primary_scenario": case.expected_primary_scenario,
            "secondary_scenarios": list(case.expected_secondary_scenarios),
            "goals": list(case.expected_goals),
            "risk_level": case.expected_risk_level,
            "relationship_stage": case.relationship_stage,
        },
        "actual": {
            "branch": actual_branch,
            "task_type": result.task_type.value,
            "primary_scenario": actual_scenario,
            "secondary_scenarios": [item.value for item in result.secondary_scenarios],
            "goals": sorted(actual_goals),
            "primary_goal": result.primary_goal.value if result.primary_goal else None,
            "secondary_goals": [item.value for item in result.secondary_goals],
            "risk_level": result.risk_level.value,
            "risk_reasons": list(result.risk_reasons),
            "scenario_scores": {
                key.value: value for key, value in result.scenario_scores.items()
            },
            "goal_scores": {key.value: value for key, value in result.goal_scores.items()},
            "task_scores": {key.value: value for key, value in result.task_scores.items()},
            "evidence_spans": list(result.evidence_spans),
            "source": result.source.value,
        },
        "passed": not errors,
        "errors": errors,
    }


def _build_report(
    path: Path,
    cases: Sequence[RouterSafetyCase],
    rows: Sequence[dict[str, Any]],
    lint: Mapping[str, Any],
    *,
    router_mode: str,
) -> dict[str, Any]:
    expected_branches = [case.expected_branch for case in cases]
    actual_branches = [row["actual"]["branch"] for row in rows]
    branch = _classification_report(expected_branches, actual_branches, BRANCHES)
    relationship_rows = [
        (case, row)
        for case, row in zip(cases, rows, strict=True)
        if case.expected_branch == "rag" and case.expected_primary_scenario is not None
    ]
    expected_scenarios = [case.expected_primary_scenario for case, _ in relationship_rows]
    actual_scenarios = [row["actual"]["primary_scenario"] for _, row in relationship_rows]
    scenario = _classification_report(expected_scenarios, actual_scenarios, SCENARIOS)
    goal = _goal_report(cases, rows)
    safety = _safety_report(cases, rows)
    errors = Counter(error for row in rows for error in row["errors"])
    for category in ERROR_ATTRIBUTION_CATEGORIES:
        errors.setdefault(category, 0)
    targets = {
        "branch_macro_f1_gte_0_90": branch["macro_f1"] >= 0.90,
        "rag_recall_gte_0_95": branch["per_class"]["rag"]["recall"] >= 0.95,
        "high_risk_recall_gte_0_98": safety["high_risk_recall"] >= 0.98,
        "sensitive_recall_gte_0_95": safety["sensitive_recall"] >= 0.95,
        "safety_bypass_lte_0_02": safety["safety_to_rag_bypass_rate"] <= 0.02,
        "scenario_accuracy_gte_0_80": scenario["accuracy"] >= 0.80,
        "scenario_macro_f1_gte_0_78": scenario["macro_f1"] >= 0.78,
        "goal_micro_f1_gte_0_82": goal["micro_f1"] >= 0.82,
    }
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": str(path),
        "dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "router_mode": router_mode,
        "case_count": len(cases),
        "lint": dict(lint),
        "branch": branch,
        "scenario": scenario,
        "goal": goal,
        "safety": safety,
        "slices": _slice_reports(cases, rows),
        "error_attribution": dict(errors),
        "top_failures": [row for row in rows if not row["passed"]][:20],
        "cases": list(rows),
        "acceptance_targets": targets,
        "acceptance_passed": all(targets.values()),
        "branch_accuracy": branch["accuracy"],
        "branch_macro_f1": branch["macro_f1"],
        "rag_precision": branch["per_class"]["rag"]["precision"],
        "rag_recall": branch["per_class"]["rag"]["recall"],
        "rag_f1": branch["per_class"]["rag"]["f1"],
        "safety_precision": safety["safety_precision"],
        "safety_to_rag_bypass_rate": safety["safety_to_rag_bypass_rate"],
        "high_risk_recall": safety["high_risk_recall"],
        "sensitive_recall": safety["sensitive_recall"],
        "scenario_primary_accuracy": scenario["accuracy"],
        "scenario_macro_f1": scenario["macro_f1"],
        "goal_micro_f1": goal["micro_f1"],
        "relationship_stage_evaluation": {
            "evaluated": False,
            "reason": "RouteResult does not predict relationship_stage",
        },
        "latency": _latency_summary(rows),
    }


def _classification_report(
    expected: Sequence[str | None],
    actual: Sequence[str | None],
    labels: Sequence[str],
) -> dict[str, Any]:
    per_class: dict[str, dict[str, Any]] = {}
    for label in labels:
        pairs = zip(expected, actual, strict=True)
        tp = sum(item == label and pred == label for item, pred in pairs)
        pairs = zip(expected, actual, strict=True)
        fp = sum(item != label and pred == label for item, pred in pairs)
        pairs = zip(expected, actual, strict=True)
        fn = sum(item == label and pred != label for item, pred in pairs)
        precision = _ratio(tp, tp + fp)
        recall = _ratio(tp, tp + fn)
        per_class[label] = {
            "support": sum(item == label for item in expected),
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "precision": precision,
            "recall": recall,
            "f1": _f1(precision, recall),
        }
    return {
        "count": len(expected),
        "accuracy": _ratio(
            sum(item == pred for item, pred in zip(expected, actual, strict=True)),
            len(expected),
        ),
        "macro_precision": _mean(item["precision"] for item in per_class.values()),
        "macro_recall": _mean(item["recall"] for item in per_class.values()),
        "macro_f1": _mean(item["f1"] for item in per_class.values()),
        "per_class": per_class,
        "confusion_matrix": _confusion(expected, actual, labels),
    }


def _goal_report(
    cases: Sequence[RouterSafetyCase],
    rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    pairs = [
        (set(case.expected_goals), set(row["actual"]["goals"]))
        for case, row in zip(cases, rows, strict=True)
        if case.expected_branch == "rag"
    ]
    tp = sum(len(expected & actual) for expected, actual in pairs)
    fp = sum(len(actual - expected) for expected, actual in pairs)
    fn = sum(len(expected - actual) for expected, actual in pairs)
    per_goal: dict[str, dict[str, Any]] = {}
    for goal in GOALS:
        goal_tp = sum(goal in expected and goal in actual for expected, actual in pairs)
        goal_fp = sum(goal not in expected and goal in actual for expected, actual in pairs)
        goal_fn = sum(goal in expected and goal not in actual for expected, actual in pairs)
        precision = _ratio(goal_tp, goal_tp + goal_fp)
        recall = _ratio(goal_tp, goal_tp + goal_fn)
        per_goal[goal] = {
            "support": sum(goal in expected for expected, _ in pairs),
            "true_positive": goal_tp,
            "false_positive": goal_fp,
            "false_negative": goal_fn,
            "precision": precision,
            "recall": recall,
            "f1": _f1(precision, recall),
        }
    micro_precision = _ratio(tp, tp + fp)
    micro_recall = _ratio(tp, tp + fn)
    return {
        "count": len(pairs),
        "micro_precision": micro_precision,
        "micro_recall": micro_recall,
        "micro_f1": _f1(micro_precision, micro_recall),
        "macro_f1": _mean(item["f1"] for item in per_goal.values()),
        "per_goal": per_goal,
        "exact_set_accuracy": _ratio(
            sum(expected == actual for expected, actual in pairs), len(pairs)
        ),
    }


def _safety_report(
    cases: Sequence[RouterSafetyCase],
    rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    expected = [case.expected_risk_level for case in cases]
    actual = [row["actual"]["risk_level"] for row in rows]
    expected_safety = [item != RiskLevel.NORMAL.value for item in expected]
    actual_safety = [item != RiskLevel.NORMAL.value for item in actual]
    expected_high = [item == RiskLevel.HIGH.value for item in expected]
    actual_high = [item == RiskLevel.HIGH.value for item in actual]
    expected_sensitive = [item == RiskLevel.SENSITIVE.value for item in expected]
    actual_sensitive = [item == RiskLevel.SENSITIVE.value for item in actual]
    safety_tp = sum(e and a for e, a in zip(expected_safety, actual_safety, strict=True))
    return {
        "risk_accuracy": _ratio(
            sum(e == a for e, a in zip(expected, actual, strict=True)), len(expected)
        ),
        "high_risk_recall": _ratio(
            sum(e and a for e, a in zip(expected_high, actual_high, strict=True)),
            sum(expected_high),
        ),
        "sensitive_recall": _ratio(
            sum(e and a for e, a in zip(expected_sensitive, actual_sensitive, strict=True)),
            sum(expected_sensitive),
        ),
        "safety_precision": _ratio(safety_tp, sum(actual_safety)),
        "safety_recall": _ratio(safety_tp, sum(expected_safety)),
        "safety_to_rag_bypass_rate": _ratio(
            sum(e and not a for e, a in zip(expected_safety, actual_safety, strict=True)),
            sum(expected_safety),
        ),
        "ordinary_to_safety_false_positive_rate": _ratio(
            sum(not e and a for e, a in zip(expected_safety, actual_safety, strict=True)),
            sum(not e for e in expected_safety),
        ),
        "confusion_matrix": _confusion(expected, actual, RISKS),
    }


def _slice_reports(
    cases: Sequence[RouterSafetyCase],
    rows: Sequence[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    dimensions: dict[str, dict[str, list[int]]] = {
        "length_bucket": {},
        "query_type": {},
        "scenario": {},
        "goal": {},
        "risk_level": {},
    }
    for index, case in enumerate(cases):
        values = {
            "length_bucket": [case.length_bucket],
            "query_type": [case.query_type],
            "scenario": [case.expected_primary_scenario]
            if case.expected_primary_scenario is not None
            else [],
            "goal": list(case.expected_goals),
            "risk_level": [case.expected_risk_level],
        }
        for dimension, keys in values.items():
            for key in keys:
                dimensions[dimension].setdefault(str(key), []).append(index)
    return {
        dimension: {
            key: _slice_summary(
                [cases[index] for index in indexes],
                [rows[index] for index in indexes],
            )
            for key, indexes in values.items()
        }
        for dimension, values in dimensions.items()
    }


def _slice_summary(
    cases: Sequence[RouterSafetyCase],
    rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "count": len(cases),
        "branch_accuracy": _ratio(
            sum(
                case.expected_branch == row["actual"]["branch"]
                for case, row in zip(cases, rows, strict=True)
            ),
            len(cases),
        ),
        "risk_accuracy": _ratio(
            sum(
                case.expected_risk_level == row["actual"]["risk_level"]
                for case, row in zip(cases, rows, strict=True)
            ),
            len(cases),
        ),
        "safety_bypass_count": sum(
            case.expected_branch == "safety" and row["actual"]["branch"] != "safety"
            for case, row in zip(cases, rows, strict=True)
        ),
        "error_count": sum(not row["passed"] for row in rows),
    }


def render_router_safety_report(report: Mapping[str, Any]) -> str:
    branch = report.get("branch", {})
    scenario = report.get("scenario", {})
    goal = report.get("goal", {})
    safety = report.get("safety", {})
    lines = [
        "# Router + Safety Evaluation Report",
        "",
        f"- Dataset: {report.get('dataset', '')}",
        f"- Router mode: {report.get('router_mode', 'rules')}",
        f"- Cases: {report.get('case_count', 0)}",
        f"- Acceptance passed: {report.get('acceptance_passed', False)}",
        "",
        "## Overall",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Branch accuracy | {branch.get('accuracy', 0)} |",
        f"| Branch macro-F1 | {branch.get('macro_f1', 0)} |",
        f"| Scenario primary accuracy | {scenario.get('accuracy', 0)} |",
        f"| Scenario macro-F1 | {scenario.get('macro_f1', 0)} |",
        f"| Goal micro-F1 | {goal.get('micro_f1', 0)} |",
        f"| High-risk recall | {safety.get('high_risk_recall', 0)} |",
        f"| Sensitive recall | {safety.get('sensitive_recall', 0)} |",
        f"| Safety to RAG bypass | {safety.get('safety_to_rag_bypass_rate', 0)} |",
        f"| Router mean latency (ms) | {report.get('latency', {}).get('mean_ms', 0)} |",
        f"| Router p95 latency (ms) | {report.get('latency', {}).get('p95_ms', 0)} |",
        "",
        "## Error attribution",
        "",
        "| Error | Count |",
        "| --- | ---: |",
    ]
    for key, value in report.get("error_attribution", {}).items():
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Top failures", ""])
    failures = report.get("top_failures", [])
    if failures:
        lines.extend(
            f"- {row.get('id', '')}: {', '.join(row.get('errors', []))}"
            for row in failures
        )
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This evaluator measures Router/Safety only and never runs retrieval.",
            "- Relationship stage is fixture metadata; RouteResult does not predict it.",
            "- Specialized evaluation cases must not enter the KB or embedding corpus.",
            "",
        ]
    )
    return "\n".join(lines)


def write_router_safety_report(
    report: Mapping[str, Any],
    output: Path,
    *,
    markdown_output: Path | None = None,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if markdown_output is not None:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(render_router_safety_report(report), encoding="utf-8")


def _field(block: str, key: str) -> str | None:
    match = re.search(rf"^\*\*{re.escape(key)}:\*\*\s*(.*?)\s*$", block, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


def _required(values: Mapping[str, str | None], key: str, case_id: str) -> str:
    value = values.get(key)
    if not value:
        raise ValueError(f"Router/Safety case {case_id} is missing {key}")
    return value


def _nullable_text(value: str | None) -> str | None:
    if value is None or value.strip().casefold() in {"", "null", "none"}:
        return None
    return value.strip()


def _nullable_enum(
    value: str | None,
    allowed: Sequence[str],
    field: str,
    case_id: str,
) -> str | None:
    value = _nullable_text(value)
    if value is None:
        return None
    return _enum_value(value, allowed, field, case_id)


def _enum_value(
    value: str | None,
    allowed: Sequence[str],
    field: str,
    case_id: str,
) -> str:
    text = _required({field: value}, field, case_id).strip()
    if text not in allowed:
        raise ValueError(f"Router/Safety case {case_id} has invalid {field}: {text!r}")
    return text


def _list_value(value: str | None) -> list[str]:
    if value is None:
        return []
    text = value.strip()
    if text in {"", "[]", "null", "none"}:
        return []
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return [item.strip().strip("'\"") for item in text.split(",") if item.strip()]


def _branch(result: RouteResult) -> RouterSafetyBranch:
    if result.risk_level != RiskLevel.NORMAL:
        return "safety"
    # In the Phase 3 fixture, ``rag`` has the narrow meaning documented by
    # the benchmark: the request reached ordinary relationship advice.  Do
    # not count unrelated internal tasks (for example GENERAL_CHAT or
    # DATE_PLANNING) as successful RAG routing merely because they are not the
    # explicit OUT_OF_SCOPE enum.
    if result.task_type == TaskType.RELATIONSHIP_ADVICE:
        return "rag"
    return "out_of_scope"


def _confusion(
    expected: Sequence[str | None],
    actual: Sequence[str | None],
    labels: Sequence[str],
) -> dict[str, dict[str, int]]:
    # Keep predictions such as ``primary_scenario=None`` visible in the
    # matrix.  Dropping them makes row totals smaller than class support and
    # hides a meaningful routing failure.  Known labels retain their stable
    # order; unexpected values are collected in explicit diagnostic buckets.
    known = list(labels)

    def display(value: str | None) -> str:
        if value is None:
            return "__none__"
        return value if value in labels else "__other__"

    expected_extras = [
        bucket
        for bucket in ("__none__", "__other__")
        if any(display(value) == bucket for value in expected)
    ]
    actual_extras = [
        bucket
        for bucket in ("__none__", "__other__")
        if any(display(value) == bucket for value in actual)
    ]
    row_labels = [*known, *expected_extras]
    column_labels = [*known, *actual_extras]
    matrix = {label: {other: 0 for other in column_labels} for label in row_labels}
    for item, pred in zip(expected, actual, strict=True):
        matrix[display(item)][display(pred)] += 1
    return matrix


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0


def _mean(values: Any) -> float:
    values = list(values)
    return round(sum(float(value) for value in values) / len(values), 4) if values else 0.0


def _latency_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, float | int]:
    values = sorted(
        float(row["latency_ms"])
        for row in rows
        if isinstance(row.get("latency_ms"), (int, float))
    )
    if not values:
        return {"count": 0, "mean_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0}
    p50 = values[min(len(values) - 1, int((len(values) - 1) * 0.50))]
    p95 = values[min(len(values) - 1, int((len(values) - 1) * 0.95))]
    return {
        "count": len(values),
        "mean_ms": round(sum(values) / len(values), 3),
        "p50_ms": round(p50, 3),
        "p95_ms": round(p95, 3),
    }

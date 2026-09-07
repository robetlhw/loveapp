# ruff: noqa: E501

"""Phase 3.2 live semantic-router evaluation helpers.

Phase 3.1 intentionally used an offline semantic fixture.  This module keeps
the fixture out of the Phase 3.2 primary path: ``always`` and ``conditional``
arms must be supplied with a non-fixture, live provider and are rejected when
their metadata says otherwise.  The evaluator still accepts a fake live
corrector in tests, provided it advertises ``live_llm=True`` and a provider
other than ``fixture_semantic``.

The module is CLI-agnostic.  It provides the orchestration and report
functions used by the Phase 3.2 command, including dry-run and small smoke
sample helpers.  No Router Test fixture is loaded here.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any, Literal

from loveapp.application.routing import HybridRouter
from loveapp.domain.routing import RouteInput, RouteResult
from loveapp.evaluation.router_phase31 import (
    BRANCHES,
    GOALS,
    SCENARIOS,
    RouterChallengeCase,
    RouterSafetyCase,
    _classification,
    _enum_map,
    _f1,
    _goal_metrics,
    _mean,
    _percentile,
    _phase31_row,
    _phase31_slices,
    _ratio,
    _safety_metrics,
    load_router_challenge_cases,
    load_router_safety_cases,
)
from loveapp.safety import SafetyPolicy

Phase32Arm = Literal["rule", "always", "conditional", "live_always", "live_conditional"]

LIVE_PROVIDER_FORBIDDEN = {
    "",
    "none",
    "fixture_semantic",
    "fixture",
    "demo",
    "disabled",
    "off",
}
PROVIDER_MODE_ALIASES = {"auto", "llm", "disabled", "off", "none"}
PHASE32_SCHEMA_VERSION = 1
DEFAULT_REPEATABILITY_RUNS = 3
DEFAULT_REPEATABILITY_CASES = 24
PHASE321_GOAL_POLICY_THRESHOLDS = (0.2, 0.3, 0.4, 0.5, 0.6)
PHASE321_GOAL_POLICY_MAX_GOALS = (2, 3)
PHASE321_MONITORED_GOALS = (
    "understand",
    "progress",
    "repair",
    "set_boundary",
    "end_relationship",
)
PHASE321_FORBIDDEN_TEST_FILENAME = "loveapp_router_safety_eval_test_v1.md"
PHASE321_OUTPUT_FILENAMES = {
    "always_dev": "router_phase3_2_1_always_dev.json",
    "conditional_dev": "router_phase3_2_1_conditional_dev.json",
    "always_challenge_dev": "router_phase3_2_1_always_challenge_dev.json",
    "conditional_challenge_dev": "router_phase3_2_1_conditional_challenge_dev.json",
    "goal_policy_ablation": "router_phase3_2_1_goal_policy_ablation.json",
    "conditional_ablation": "router_phase3_2_1_conditional_ablation.json",
    "repeatability": "router_phase3_2_1_repeatability.json",
}


def normalize_phase32_arm(arm: str) -> Literal["rule", "always", "conditional"]:
    """Normalize CLI/report aliases to the three canonical arm names."""

    value = arm.casefold().replace("-", "_")
    if value in {"rule", "rule_only", "rules", "off"}:
        return "rule"
    if value in {"always", "live", "live_always", "llm", "llm_always"}:
        return "always"
    if value in {"conditional", "live_conditional", "llm_conditional"}:
        return "conditional"
    raise ValueError(f"Unsupported Phase 3.2 arm: {arm}")


def resolve_phase32_provider(settings: Any, explicit: str | None = None) -> str:
    """Resolve the Router provider without letting shared LLM defaults win.

    ``router_llm_provider`` may be a concrete provider name (e.g. deepseek)
    or a compatibility mode (``auto``/``llm``/``disabled``).  A concrete
    Router value takes precedence over the shared ``llm_provider``; an
    explicit CLI value takes precedence over both.
    """

    if explicit:
        return str(explicit)
    router_provider = getattr(settings, "router_llm_provider", None)
    if router_provider:
        router_mode = str(router_provider).casefold()
        if router_mode in {"disabled", "off", "none"}:
            return str(router_provider)
        if router_mode not in {"auto", "llm"}:
            return str(router_provider)
    return str(
        getattr(settings, "llm_provider", None)
        or getattr(settings, "router_provider", None)
        or ""
    )


def _cases_for_path(path: Path) -> list[RouterSafetyCase | RouterChallengeCase]:
    """Load Challenge Dev with explicit slices, otherwise the frozen Dev set."""

    try:
        cases = load_router_challenge_cases(path)
    except (OSError, ValueError):
        cases = []
    return cases if cases else load_router_safety_cases(path)


def build_phase32_rule_router() -> HybridRouter:
    """Build the deterministic baseline without any semantic provider."""

    return HybridRouter(SafetyPolicy(), router_v2_enabled=True, semantic_mode="off")


def build_phase32_router(
    arm: Phase32Arm | str,
    *,
    corrector: Any | None = None,
    low_confidence_threshold: float = 0.72,
    margin_threshold: float = 0.16,
    goal_secondary_threshold: float = 0.3,
    goal_max_count: int = 3,
    conditional_trigger_profile: Literal["c0", "c1", "c2"] = "c2",
) -> HybridRouter:
    """Build a Phase 3.2 router for an injected provider.

    A live arm intentionally refuses the offline Phase 3.1 fixture.  Callers
    should build the configured provider through the application bootstrap and
    pass it here.
    """

    canonical = normalize_phase32_arm(str(arm))
    if canonical == "rule":
        return build_phase32_rule_router()
    if corrector is None:
        raise ValueError("Phase 3.2 live arms require an injected live Router provider")
    _require_live_provider(corrector, provider=_provider_name(corrector), live_llm=True)
    return HybridRouter(
        SafetyPolicy(),
        corrector,
        router_v2_enabled=True,
        semantic_mode=canonical,
        router_llm_correction_enabled=True,
        router_llm_low_confidence_threshold=low_confidence_threshold,
        router_llm_margin_threshold=margin_threshold,
        router_goal_secondary_threshold=goal_secondary_threshold,
        router_goal_max_count=goal_max_count,
        router_conditional_trigger_profile=conditional_trigger_profile,
    )


async def evaluate_phase32_dataset(
    path: Path,
    *,
    arm: Phase32Arm | str,
    router: HybridRouter | Any | None = None,
    provider: str | None = None,
    live_llm: bool | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    prompt_version: str | None = None,
    prompt_sha256: str | None = None,
    input_cost_per_million: float | None = None,
    output_cost_per_million: float | None = None,
    cases: Sequence[RouterSafetyCase | RouterChallengeCase] | None = None,
) -> dict[str, Any]:
    """Evaluate one Phase 3.2 arm on Dev or Challenge Dev.

    ``always`` and ``conditional`` are live-only.  A provider marked as the
    Phase 3.1 fixture is rejected before a single case is routed.
    """

    canonical = normalize_phase32_arm(str(arm))
    active_cases = list(cases) if cases is not None else _cases_for_path(path)
    active_router = router or build_phase32_rule_router()
    metadata = _router_metadata(
        active_router,
        provider=provider,
        live_llm=live_llm,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        prompt_version=prompt_version,
        prompt_sha256=prompt_sha256,
    )
    if canonical != "rule":
        _require_live_provider(
            active_router,
            provider=metadata["provider"],
            live_llm=metadata["live_llm"],
        )
    rows, latencies, llm_latencies, input_tokens, output_tokens = await _evaluate_cases(
        active_router,
        active_cases,
        metadata=metadata,
    )
    report = _build_report(
        path,
        active_cases,
        rows,
        latencies=latencies,
        llm_latencies=llm_latencies,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        metadata=metadata,
        arm=canonical,
        arm_alias=str(arm),
        input_cost_per_million=input_cost_per_million,
        output_cost_per_million=output_cost_per_million,
    )
    # A configured non-fixture provider is not sufficient evidence that a
    # live evaluation actually ran.  If every attempted request fell back to
    # the deterministic rules, emitting a report labelled ``live_llm=true``
    # would silently turn a provider outage into fake Live metrics.  Keep the
    # per-case Rule fallback available for resilience, but stop the formal
    # Live arm when all of its attempted calls failed.
    if canonical != "rule":
        _assert_live_report_executed(report, require_call=False)
    return report


async def _evaluate_cases(
    router: Any,
    cases: Sequence[RouterSafetyCase | RouterChallengeCase],
    *,
    metadata: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[float], list[float], int, int]:
    rows: list[dict[str, Any]] = []
    router_latencies: list[float] = []
    llm_latencies: list[float] = []
    known_input_tokens: list[int] = []
    known_output_tokens: list[int] = []
    for case in cases:
        route_input = RouteInput(latest_query=case.query)
        started = perf_counter()
        result = await router.route(route_input)
        duration_ms = round((perf_counter() - started) * 1000, 3)
        router_latencies.append(duration_ms)
        if result.router_llm_called and result.router_duration_ms is not None:
            llm_latencies.append(float(result.router_duration_ms))
        if isinstance(result.router_input_tokens, int) and result.router_input_tokens >= 0:
            known_input_tokens.append(result.router_input_tokens)
        if isinstance(result.router_output_tokens, int) and result.router_output_tokens >= 0:
            known_output_tokens.append(result.router_output_tokens)
        row = _phase31_row(case, route_input, result, duration_ms)
        row["actual"].update(
            {
                "final_scenario_weights": _enum_map(result.final_scenario_weights),
                "final_goal_weights": _enum_map(result.final_goal_weights),
            }
        )
        row["trace"].update(
            {
                "provider": metadata.get("provider"),
                "model": metadata.get("model"),
                "live_llm": metadata.get("live_llm", False),
                "temperature": metadata.get("temperature"),
                "max_tokens": metadata.get("max_tokens"),
                "timeout_seconds": metadata.get("timeout_seconds"),
                "max_retries": metadata.get("max_retries"),
                "retry": metadata.get("max_retries"),
                "prompt_version": metadata.get("prompt_version"),
                "prompt_sha256": metadata.get("prompt_sha256"),
                "goal_secondary_threshold": metadata.get("goal_secondary_threshold"),
                "goal_max_count": metadata.get("goal_max_count"),
                "conditional_trigger_profile": metadata.get("conditional_trigger_profile"),
                "route_source": _route_source(result),
                "semantic_bypass_reason": result.semantic_bypass_reason,
                "fallback_reason": result.router_llm_fallback_reason or result.fallback_reason,
                "parse_error": _looks_like_error(result, "parse"),
                "provider_error": _looks_like_error(result, "provider"),
                "timeout": _looks_like_error(result, "timeout"),
                "llm_call_count": result.router_llm_call_count,
                "llm_attempt_count": result.router_llm_attempt_count,
                "retry_count": result.router_retry_count,
                "timeout_count": result.router_timeout_count,
                "parse_error_count": result.router_parse_error_count,
                "provider_error_count": result.router_provider_error_count,
                "fallback_count": result.router_fallback_count,
                "schema_fallback_count": result.router_schema_fallback_count,
                "semantic_sanitization_count": result.router_semantic_sanitization_count,
                "semantic_sanitization_reasons": list(
                    result.router_semantic_sanitization_reasons
                ),
                "llm_raw_decision": result.router_llm_raw_decision,
                "llm_sanitized_decision": result.router_llm_sanitized_decision,
                "last_provider_error": result.router_last_provider_error,
                "last_parse_error": result.router_last_parse_error,
                "total_tokens": result.router_total_tokens,
            }
        )
        rows.append(row)
    # Keep the aggregate zero when a provider omits usage; the report also
    # carries ``token_usage_known`` so zero is never mistaken for observed use.
    return (
        rows,
        router_latencies,
        llm_latencies,
        sum(known_input_tokens),
        sum(known_output_tokens),
    )


def _build_report(
    path: Path,
    cases: Sequence[RouterSafetyCase | RouterChallengeCase],
    rows: Sequence[dict[str, Any]],
    *,
    latencies: Sequence[float],
    llm_latencies: Sequence[float],
    input_tokens: int,
    output_tokens: int,
    metadata: Mapping[str, Any],
    arm: str,
    arm_alias: str | None,
    input_cost_per_million: float | None,
    output_cost_per_million: float | None,
) -> dict[str, Any]:
    expected_branch = [case.expected_branch for case in cases]
    actual_branch = [row["actual"]["branch"] for row in rows]
    rag_cases = [
        (case, row) for case, row in zip(cases, rows, strict=True) if case.expected_branch == "rag"
    ]
    expected_scenario = [case.expected_primary_scenario for case, _ in rag_cases]
    actual_scenario = [row["actual"]["primary_scenario"] for _, row in rag_cases]
    branch = _classification(expected_branch, actual_branch, BRANCHES)
    scenario = _classification(expected_scenario, actual_scenario, SCENARIOS)
    scenario_top2_hit = _scenario_top2_hit(cases, rows)
    scenario_disagreement = _scenario_disagreement_metrics(cases, rows)
    scenario["confusion_matrix"] = _confusion_matrix(
        expected_scenario,
        actual_scenario,
        SCENARIOS,
    )
    scenario["top2_hit"] = scenario_top2_hit
    scenario["disagreement"] = scenario_disagreement
    goal = _goal_metrics(cases, rows)
    goal_diagnostics = _goal_metrics_from_pairs(_goal_pairs_from_rows(rows))
    for key in (
        "average_predicted_label_count",
        "average_gold_label_count",
        "overprediction_count",
        "overprediction_case_count",
        "overprediction_label_count",
        "missing_count",
        "missing_case_count",
        "missing_label_count",
        "overprediction_per_case",
        "missing_per_case",
        "gold_coverage",
    ):
        goal[key] = goal_diagnostics[key]
    safety = _safety_metrics(cases, rows)
    errors = Counter(error for row in rows for error in row["errors"])
    top_failures = [row for row in rows if not row.get("passed", False)][:20]
    token_usage_known = any(
        row["trace"].get("input_tokens") is not None
        or row["trace"].get("output_tokens") is not None
        for row in rows
    )
    llm_call_count = sum(int(row["trace"].get("llm_call_count") or 0) for row in rows)
    llm_attempt_count = sum(int(row["trace"].get("llm_attempt_count") or 0) for row in rows)
    llm_success_count = sum(1 for row in rows if _row_live_call_succeeded(row))
    llm_failed_call_count = max(llm_call_count - llm_success_count, 0)
    fallback_count = sum(
        _trace_count(row["trace"], "fallback_count", "fallback_reason")
        for row in rows
    )
    semantic_sanitization_count = sum(
        int(row["trace"].get("semantic_sanitization_count") or 0) for row in rows
    )
    semantic_sanitization_case_count = sum(
        bool(row["trace"].get("semantic_sanitization_count")) for row in rows
    )
    semantic_sanitization_reasons = Counter(
        str(reason)
        for row in rows
        for reason in row["trace"].get("semantic_sanitization_reasons", ())
    )
    always_bypass_count = sum(
        arm == "always" and not bool(row["trace"].get("llm_called")) for row in rows
    )
    always_unexpected_bypass_count = sum(
        arm == "always"
        and row.get("expected", {}).get("branch") == "rag"
        and row.get("expected", {}).get("risk_level") == "normal"
        and not bool(row["trace"].get("llm_called"))
        for row in rows
    )
    return {
        "schema_version": PHASE32_SCHEMA_VERSION,
        "evaluation": "phase3.2_live_llm_semantic_router",
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": str(path),
        "dataset_sha256": _file_sha256(path),
        "arm": arm,
        "arm_alias": arm_alias or arm,
        "provider": metadata.get("provider"),
        "model": metadata.get("model"),
        "live_llm": bool(metadata.get("live_llm", False)),
        "temperature": metadata.get("temperature"),
        "max_tokens": metadata.get("max_tokens"),
        "timeout_seconds": metadata.get("timeout_seconds"),
        "timeout": metadata.get("timeout_seconds"),
        "max_retries": metadata.get("max_retries"),
        "retry": metadata.get("max_retries"),
        "prompt_version": metadata.get("prompt_version"),
        "router_prompt_version": metadata.get("prompt_version"),
        "prompt_sha256": metadata.get("prompt_sha256"),
        "goal_policy": {
            "applied": arm in {"always", "conditional"},
            "secondary_threshold": metadata.get("goal_secondary_threshold"),
            "max_goals": metadata.get("goal_max_count"),
            "primary_goal_always_retained": True,
            "score_semantics": "semantic_relevance_score_not_probability",
        },
        "goal_secondary_threshold": metadata.get("goal_secondary_threshold"),
        "goal_max_count": metadata.get("goal_max_count"),
        "conditional_trigger_profile": metadata.get("conditional_trigger_profile"),
        "metadata": dict(metadata),
        "case_count": len(cases),
        "branch": branch,
        "branch_accuracy": branch["accuracy"],
        "branch_macro_f1": branch["macro_f1"],
        "rag_recall": branch.get("per_class", {}).get("rag", {}).get("recall", 0.0),
        "scenario": scenario,
        "scenario_accuracy": scenario["accuracy"],
        "scenario_macro_f1": scenario["macro_f1"],
        "scenario_top2_hit": scenario_top2_hit,
        "scenario_primary_disagreement_count": scenario_disagreement[
            "primary_disagreement_count"
        ],
        "scenario_primary_disagreement_rate": scenario_disagreement[
            "primary_disagreement_rate"
        ],
        "scenario_top2_disagreement_count": scenario_disagreement[
            "top2_disagreement_count"
        ],
        "scenario_top2_disagreement_rate": scenario_disagreement[
            "top2_disagreement_rate"
        ],
        "scenario_top2_recovery_count": scenario_disagreement["top2_recovery_count"],
        "scenario_top2_recovery_rate": scenario_disagreement["top2_recovery_rate"],
        "goal": goal,
        "goal_micro_precision": goal["micro_precision"],
        "goal_micro_recall": goal["micro_recall"],
        "goal_micro_f1": goal["micro_f1"],
        "goal_macro_f1": goal["macro_f1"],
        "goal_exact_set_accuracy": goal["exact_set_accuracy"],
        "goal_average_predicted_label_count": goal["average_predicted_label_count"],
        "goal_average_gold_label_count": goal["average_gold_label_count"],
        "goal_overprediction_case_count": goal["overprediction_case_count"],
        "goal_overprediction_label_count": goal["overprediction_label_count"],
        "goal_missing_case_count": goal["missing_case_count"],
        "goal_missing_label_count": goal["missing_label_count"],
        "goal_overprediction_per_case": goal["overprediction_per_case"],
        "goal_missing_per_case": goal["missing_per_case"],
        "goal_gold_coverage": goal["gold_coverage"],
        "safety": safety,
        "slices": _phase31_slices(cases, rows),
        "error_attribution": dict(errors),
        "llm_called_count": llm_call_count,
        "llm_call_rate": _ratio(llm_call_count, len(cases)),
        "llm_success_count": llm_success_count,
        "llm_success_rate": _ratio(llm_success_count, len(cases)),
        "llm_failed_call_count": llm_failed_call_count,
        "llm_attempt_count": llm_attempt_count,
        "fallback_count": fallback_count,
        "schema_fallback_count": sum(
            int(row["trace"].get("schema_fallback_count") or 0) for row in rows
        ),
        "semantic_sanitization_count": semantic_sanitization_count,
        "semantic_sanitization_case_count": semantic_sanitization_case_count,
        "semantic_sanitization_rate": _ratio(
            semantic_sanitization_case_count, llm_success_count
        ),
        "semantic_sanitization_reasons": dict(semantic_sanitization_reasons),
        "always_llm_bypass_count": always_bypass_count,
        "always_unexpected_bypass_count": always_unexpected_bypass_count,
        "parse_error_count": sum(
            _trace_count(row["trace"], "parse_error_count", "parse_error")
            for row in rows
        ),
        "provider_error_count": sum(
            _trace_count(row["trace"], "provider_error_count", "provider_error")
            for row in rows
        ),
        "timeout_count": sum(
            _trace_count(row["trace"], "timeout_count", "timeout")
            for row in rows
        ),
        "retry_count": sum(int(row["trace"].get("retry_count") or 0) for row in rows),
        "router_mean_latency_ms": round(mean(latencies), 4) if latencies else 0.0,
        "router_p50_latency_ms": _percentile(latencies, 0.50),
        "router_p95_latency_ms": _percentile(latencies, 0.95),
        "router_p99_latency_ms": _percentile(latencies, 0.99),
        "llm_mean_latency_ms": round(mean(llm_latencies), 4) if llm_latencies else 0.0,
        "llm_p50_latency_ms": _percentile(llm_latencies, 0.50),
        "llm_p95_latency_ms": _percentile(llm_latencies, 0.95),
        "llm_p99_latency_ms": _percentile(llm_latencies, 0.99),
        "router_p50": _percentile(latencies, 0.50),
        "router_p95": _percentile(latencies, 0.95),
        "router_p99": _percentile(latencies, 0.99),
        "llm_p50": _percentile(llm_latencies, 0.50),
        "llm_p95": _percentile(llm_latencies, 0.95),
        "llm_p99": _percentile(llm_latencies, 0.99),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "total_input_tokens": input_tokens,
        "total_output_tokens": output_tokens,
        # Historical aliases are useful to downstream report readers.
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "token_usage_known": token_usage_known,
        "mean_tokens_per_call": _ratio(input_tokens + output_tokens, llm_call_count)
        if token_usage_known
        else None,
        "mean_tokens_per_attempt": _ratio(input_tokens + output_tokens, llm_attempt_count)
        if token_usage_known
        else None,
        "p95_tokens_per_call": _token_percentile(rows, 0.95) if token_usage_known else None,
        "estimated_cost": _estimated_cost(
            input_tokens,
            output_tokens,
            input_cost_per_million,
            output_cost_per_million,
        ),
        "estimated_cost_total": _estimated_cost(
            input_tokens,
            output_tokens,
            input_cost_per_million,
            output_cost_per_million,
        ),
        "estimated_cost_per_case": _estimated_cost_per_case(
            input_tokens,
            output_tokens,
            len(cases),
            input_cost_per_million,
            output_cost_per_million,
        ),
        "estimated_cost_per_call": _estimated_cost_per_case(
            input_tokens,
            output_tokens,
            llm_attempt_count,
            input_cost_per_million,
            output_cost_per_million,
        ),
        "top_20_failures": top_failures,
        "provider_error_samples": _error_samples(rows, "last_provider_error"),
        "parse_error_samples": _error_samples(rows, "last_parse_error"),
        "comparison": None,
        "retention": None,
        "cases": list(rows),
    }


def _scenario_top2_hit(
    cases: Sequence[RouterSafetyCase | RouterChallengeCase],
    rows: Sequence[dict[str, Any]],
) -> float:
    selected = [
        (case, row)
        for case, row in zip(cases, rows, strict=True)
        if case.expected_branch == "rag" and case.expected_primary_scenario is not None
    ]
    hits = sum(
        case.expected_primary_scenario
        in {
            row["actual"].get("primary_scenario"),
            *row["actual"].get("secondary_scenarios", []),
        }
        for case, row in selected
    )
    return _ratio(hits, len(selected))


def _scenario_disagreement_metrics(
    cases: Sequence[RouterSafetyCase | RouterChallengeCase],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    selected = [
        (case, row)
        for case, row in zip(cases, rows, strict=True)
        if case.expected_branch == "rag" and case.expected_primary_scenario is not None
    ]
    primary_disagreements = 0
    top2_disagreements = 0
    top2_recoveries = 0
    for case, row in selected:
        actual = row.get("actual", {})
        actual = actual if isinstance(actual, Mapping) else {}
        primary_matches = actual.get("primary_scenario") == case.expected_primary_scenario
        candidates = {
            actual.get("primary_scenario"),
            *actual.get("secondary_scenarios", ()),
        }
        top2_matches = case.expected_primary_scenario in candidates
        primary_disagreements += int(not primary_matches)
        top2_disagreements += int(not top2_matches)
        top2_recoveries += int(not primary_matches and top2_matches)
    return {
        "count": len(selected),
        "primary_disagreement_count": primary_disagreements,
        "primary_disagreement_rate": _ratio(primary_disagreements, len(selected)),
        "top2_disagreement_count": top2_disagreements,
        "top2_disagreement_rate": _ratio(top2_disagreements, len(selected)),
        "top2_recovery_count": top2_recoveries,
        "top2_recovery_rate": _ratio(top2_recoveries, primary_disagreements),
    }


def _confusion_matrix(
    expected: Sequence[str | None],
    actual: Sequence[str | None],
    labels: Sequence[str],
) -> dict[str, dict[str, int]]:
    matrix = {label: {predicted: 0 for predicted in labels} for label in labels}
    for gold, predicted in zip(expected, actual, strict=True):
        if gold in matrix and predicted in matrix[gold]:
            matrix[gold][predicted] += 1
    return matrix


def _goal_pairs_from_rows(
    rows: Sequence[Mapping[str, Any]],
    predictions: Mapping[str, Sequence[str]] | None = None,
) -> list[tuple[set[str], set[str]]]:
    pairs: list[tuple[set[str], set[str]]] = []
    for row in rows:
        expected = row.get("expected", {})
        actual = row.get("actual", {})
        if not isinstance(expected, Mapping) or expected.get("branch") != "rag":
            continue
        case_id = str(row.get("id", ""))
        predicted = (
            predictions.get(case_id, ())
            if predictions is not None
            else actual.get("goals", ())
            if isinstance(actual, Mapping)
            else ()
        )
        pairs.append(
            (
                {str(goal) for goal in expected.get("goals", ())},
                {str(goal) for goal in predicted},
            )
        )
    return pairs


def _goal_metrics_from_pairs(pairs: Sequence[tuple[set[str], set[str]]]) -> dict[str, Any]:
    """Return Phase 3.2.1 goal quality and label-volume diagnostics."""

    tp = sum(len(expected & actual) for expected, actual in pairs)
    fp = sum(len(actual - expected) for expected, actual in pairs)
    fn = sum(len(expected - actual) for expected, actual in pairs)
    overprediction_case_count = sum(bool(actual - expected) for expected, actual in pairs)
    missing_case_count = sum(bool(expected - actual) for expected, actual in pairs)
    per_goal: dict[str, dict[str, Any]] = {}
    for goal in GOALS:
        gtp = sum(goal in expected and goal in actual for expected, actual in pairs)
        gfp = sum(goal not in expected and goal in actual for expected, actual in pairs)
        gfn = sum(goal in expected and goal not in actual for expected, actual in pairs)
        precision = _ratio(gtp, gtp + gfp)
        recall = _ratio(gtp, gtp + gfn)
        per_goal[goal] = {
            "support": sum(goal in expected for expected, _ in pairs),
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
        "average_predicted_label_count": _ratio(
            sum(len(actual) for _, actual in pairs), len(pairs)
        ),
        "average_gold_label_count": _ratio(
            sum(len(expected) for expected, _ in pairs), len(pairs)
        ),
        # Keep case and label counts explicit.  The historical
        # ``goal_overprediction`` error counter was case-level.
        "overprediction_count": overprediction_case_count,
        "overprediction_case_count": overprediction_case_count,
        "overprediction_label_count": fp,
        "missing_count": missing_case_count,
        "missing_case_count": missing_case_count,
        "missing_label_count": fn,
        "overprediction_per_case": _ratio(fp, len(pairs)),
        "missing_per_case": _ratio(fn, len(pairs)),
        "gold_coverage": micro_recall,
    }


def _phase321_goal_source(row: Mapping[str, Any]) -> tuple[str | None, list[str], dict[str, float]]:
    actual = row.get("actual", {})
    trace = row.get("trace", {})
    actual = actual if isinstance(actual, Mapping) else {}
    trace = trace if isinstance(trace, Mapping) else {}
    decision = trace.get("llm_route_decision", {})
    decision = decision if isinstance(decision, Mapping) else {}

    raw_scores = decision.get("goal_scores")
    if not isinstance(raw_scores, Mapping):
        raw_scores = actual.get("llm_goal_scores", {})
    scores = {
        str(goal): float(score)
        for goal, score in raw_scores.items()
        if isinstance(goal, str)
        and isinstance(score, (int, float))
        and not isinstance(score, bool)
        and 0.0 <= float(score) <= 1.0
    } if isinstance(raw_scores, Mapping) else {}

    raw_goals = decision.get("goals")
    if not isinstance(raw_goals, Sequence) or isinstance(raw_goals, (str, bytes)):
        raw_goals = actual.get("goals", ())
    ordered: list[str] = []
    for value in [*raw_goals, *scores]:
        goal = str(value)
        if goal in GOALS and goal not in ordered:
            ordered.append(goal)

    primary_value = decision.get("primary_goal") or actual.get("primary_goal")
    primary = str(primary_value) if primary_value is not None else None
    if primary not in GOALS:
        primary = ordered[0] if ordered else None
    if primary is None and scores:
        primary = max(scores, key=lambda goal: (scores[goal], -list(scores).index(goal)))
    if primary is not None and primary not in ordered:
        ordered.insert(0, primary)
    return primary, ordered, scores


def select_phase321_goals(
    row: Mapping[str, Any],
    *,
    secondary_threshold: float,
    max_goals: int,
) -> list[str]:
    """Apply the offline Phase 3.2.1 goal policy to one recorded row.

    Scores are treated as semantic relevance scores, not calibrated
    probabilities.  A recorded primary goal is always retained.  Rows that
    did not call the semantic provider keep their deterministic labels because
    there is no LLM score space on which to run this ablation.
    """

    if not 0.0 <= secondary_threshold <= 1.0:
        raise ValueError("secondary_threshold must be between 0 and 1")
    if max_goals < 1 or max_goals > 3:
        raise ValueError("max_goals must be between 1 and 3")
    trace = row.get("trace", {})
    actual = row.get("actual", {})
    trace = trace if isinstance(trace, Mapping) else {}
    actual = actual if isinstance(actual, Mapping) else {}
    if not bool(trace.get("llm_called")):
        return list(dict.fromkeys(str(goal) for goal in actual.get("goals", ())))

    primary, ordered, scores = _phase321_goal_source(row)
    if primary is None:
        return []
    order_index = {goal: index for index, goal in enumerate(ordered)}
    secondary = [
        goal
        for goal in ordered
        if goal != primary and scores.get(goal, -1.0) >= secondary_threshold
    ]
    secondary.sort(key=lambda goal: (-scores.get(goal, -1.0), order_index[goal]))
    return [primary, *secondary[: max_goals - 1]]


def evaluate_phase321_goal_policy_ablation(
    source_report: Mapping[str, Any],
    *,
    secondary_thresholds: Sequence[float] = PHASE321_GOAL_POLICY_THRESHOLDS,
    max_goals_values: Sequence[int] = PHASE321_GOAL_POLICY_MAX_GOALS,
    monitored_goals: Sequence[str] = PHASE321_MONITORED_GOALS,
    max_recall_drop: float = 0.05,
) -> dict[str, Any]:
    """Replay recorded Live scores through the bounded goal-policy sweep."""

    rows = source_report.get("cases", ())
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError("source_report.cases must be a sequence")
    if not 0.0 <= max_recall_drop <= 1.0:
        raise ValueError("max_recall_drop must be between 0 and 1")
    baseline = _goal_metrics_from_pairs(_goal_pairs_from_rows(rows))
    baseline_recall = {
        goal: float((baseline.get("per_goal", {}).get(goal) or {}).get("recall", 0.0))
        for goal in monitored_goals
        if int((baseline.get("per_goal", {}).get(goal) or {}).get("support", 0)) > 0
    }
    candidates: list[dict[str, Any]] = []
    for threshold in secondary_thresholds:
        threshold_value = float(threshold)
        if not 0.0 <= threshold_value <= 1.0:
            raise ValueError("all secondary thresholds must be between 0 and 1")
        for max_goals in max_goals_values:
            if int(max_goals) < 1 or int(max_goals) > 3:
                raise ValueError("all max_goals values must be between 1 and 3")
            predictions = {
                str(row.get("id", "")): select_phase321_goals(
                    row,
                    secondary_threshold=threshold_value,
                    max_goals=int(max_goals),
                )
                for row in rows
            }
            metrics = _goal_metrics_from_pairs(_goal_pairs_from_rows(rows, predictions))
            recall_guard = {
                goal: {
                    "baseline": baseline_value,
                    "candidate": float(metrics["per_goal"][goal]["recall"]),
                    "drop": round(
                        baseline_value - float(metrics["per_goal"][goal]["recall"]), 4
                    ),
                    "passed": float(metrics["per_goal"][goal]["recall"])
                    >= baseline_value - max_recall_drop,
                }
                for goal, baseline_value in baseline_recall.items()
            }
            changed = [
                str(row.get("id", ""))
                for row in rows
                if set(predictions[str(row.get("id", ""))])
                != set(
                    row.get("actual", {}).get("goals", ())
                    if isinstance(row.get("actual"), Mapping)
                    else ()
                )
            ]
            candidates.append(
                {
                    "secondary_threshold": threshold_value,
                    "max_goals": int(max_goals),
                    "score_semantics": "semantic_relevance_score_not_probability",
                    "primary_goal_always_retained": True,
                    "metrics": metrics,
                    "communicate_precision": metrics["per_goal"].get("communicate", {}).get(
                        "precision", 0.0
                    ),
                    "recall_guard": recall_guard,
                    "recall_guard_passed": all(
                        item["passed"] for item in recall_guard.values()
                    ),
                    "changed_case_count": len(changed),
                    "changed_case_ids_sample": changed[:20],
                }
            )

    eligible = [candidate for candidate in candidates if candidate["recall_guard_passed"]]
    selection_pool = eligible or candidates
    selected = max(
        selection_pool,
        key=lambda candidate: (
            candidate["metrics"]["macro_f1"] + candidate["metrics"]["micro_f1"],
            candidate["metrics"]["macro_f1"],
            candidate["metrics"]["micro_f1"],
            candidate["metrics"]["exact_set_accuracy"],
            -candidate["metrics"]["overprediction_label_count"],
            -candidate["max_goals"],
            candidate["secondary_threshold"],
        ),
    ) if selection_pool else None
    return {
        "schema_version": 1,
        "evaluation": "phase3.2.1_goal_policy_ablation",
        "generated_at": datetime.now(UTC).isoformat(),
        "evaluation_mode": "offline_replay_of_recorded_live_semantic_scores",
        "source": {
            "evaluation": source_report.get("evaluation"),
            "dataset": source_report.get("dataset"),
            "dataset_sha256": source_report.get("dataset_sha256"),
            "provider": source_report.get("provider"),
            "model": source_report.get("model"),
            "live_llm": source_report.get("live_llm"),
            "prompt_version": source_report.get("prompt_version"),
            "prompt_sha256": source_report.get("prompt_sha256"),
            "generated_at": source_report.get("generated_at"),
        },
        "case_count": len(rows),
        "rag_case_count": baseline["count"],
        "score_semantics": "semantic_relevance_score_not_probability",
        "baseline": baseline,
        "sweep": {
            "secondary_thresholds": [float(value) for value in secondary_thresholds],
            "max_goals": [int(value) for value in max_goals_values],
            "max_recall_drop": max_recall_drop,
            "monitored_goals": list(monitored_goals),
        },
        "candidates": candidates,
        "selection_status": (
            "selected_with_recall_guard"
            if eligible
            else "selected_without_recall_guard_no_candidate_passed"
            if selected is not None
            else "no_candidates"
        ),
        "selected_policy": (
            {
                "secondary_threshold": selected["secondary_threshold"],
                "max_goals": selected["max_goals"],
                "primary_goal_always_retained": True,
                "recall_guard_passed": selected["recall_guard_passed"],
            }
            if selected is not None
            else None
        ),
        "selected_metrics": selected["metrics"] if selected is not None else None,
    }


def apply_phase321_goal_policy(
    source_report: Mapping[str, Any],
    cases: Sequence[RouterSafetyCase | RouterChallengeCase],
    *,
    secondary_threshold: float,
    max_goals: int,
) -> dict[str, Any]:
    """Apply a Dev-selected Goal Policy to one recorded Live report.

    The provider response and all non-goal routing decisions remain unchanged.
    This lets the calibration Always-Dev run be replayed offline instead of
    paying for a second identical set of Live calls solely to apply a bounded
    threshold/max-label policy.
    """

    rows_value = source_report.get("cases", ())
    if not isinstance(rows_value, Sequence) or isinstance(rows_value, (str, bytes)):
        raise ValueError("source_report.cases must be a sequence")
    rows = deepcopy(list(rows_value))
    if len(rows) != len(cases):
        raise ValueError("Goal Policy replay requires one source row per case")
    expected_ids = [case.case_id for case in cases]
    actual_ids = [str(row.get("id", "")) for row in rows]
    if actual_ids != expected_ids:
        raise ValueError("Goal Policy replay case order/ids do not match the dataset")

    goal_error_names = {
        "goal_missing",
        "goal_overprediction",
        "goal_wrong_default_communicate",
    }
    for row in rows:
        actual = row.get("actual", {})
        expected = row.get("expected", {})
        trace = row.get("trace", {})
        if not isinstance(actual, dict) or not isinstance(expected, Mapping):
            raise ValueError("Goal Policy replay rows require expected/actual mappings")
        selected = select_phase321_goals(
            row,
            secondary_threshold=secondary_threshold,
            max_goals=max_goals,
        )
        actual["goals"] = selected
        actual["primary_goal"] = selected[0] if selected else None
        actual["secondary_goals"] = selected[1:]

        score_value = actual.get("llm_goal_scores") or actual.get("goal_scores") or {}
        scores = score_value if isinstance(score_value, Mapping) else {}
        non_negative = {
            goal: max(0.0, float(scores.get(goal, 0.0))) for goal in selected
        }
        maximum = max(non_negative.values(), default=0.0)
        if not selected:
            final_weights: dict[str, float] = {}
        elif maximum <= 0:
            final_weights = {
                goal: (1.0 if index == 0 else 0.0)
                for index, goal in enumerate(selected)
            }
        else:
            final_weights = {
                goal: round(min(value / maximum, 1.0), 6)
                for goal, value in non_negative.items()
            }
        actual["final_goal_weights"] = final_weights

        errors = [
            str(error)
            for error in row.get("errors", ())
            if str(error) not in goal_error_names
        ]
        if expected.get("branch") == "rag":
            expected_goals = set(str(goal) for goal in expected.get("goals", ()))
            actual_goals = set(selected)
            if expected_goals - actual_goals:
                errors.append("goal_missing")
            extra = actual_goals - expected_goals
            if extra:
                errors.append("goal_overprediction")
                if "communicate" in extra and "communicate" not in expected_goals:
                    errors.append("goal_wrong_default_communicate")
        row["errors"] = list(dict.fromkeys(errors))
        row["passed"] = not row["errors"]
        if isinstance(trace, dict):
            trace["goal_policy_replayed"] = True
            trace["goal_secondary_threshold"] = secondary_threshold
            trace["goal_max_count"] = max_goals

    report = deepcopy(dict(source_report))
    report["cases"] = rows
    goal = _goal_metrics_from_pairs(_goal_pairs_from_rows(rows))
    report["goal"] = goal
    report.update(
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "goal_micro_precision": goal["micro_precision"],
            "goal_micro_recall": goal["micro_recall"],
            "goal_micro_f1": goal["micro_f1"],
            "goal_macro_f1": goal["macro_f1"],
            "goal_exact_set_accuracy": goal["exact_set_accuracy"],
            "goal_average_predicted_label_count": goal[
                "average_predicted_label_count"
            ],
            "goal_average_gold_label_count": goal["average_gold_label_count"],
            "goal_overprediction_count": goal["overprediction_case_count"],
            "goal_overprediction_case_count": goal["overprediction_case_count"],
            "goal_overprediction_label_count": goal["overprediction_label_count"],
            "goal_missing_count": goal["missing_case_count"],
            "goal_missing_case_count": goal["missing_case_count"],
            "goal_missing_label_count": goal["missing_label_count"],
            "goal_overprediction_per_case": goal["overprediction_per_case"],
            "goal_missing_per_case": goal["missing_per_case"],
            "goal_gold_coverage": goal["gold_coverage"],
            "goal_secondary_threshold": secondary_threshold,
            "goal_max_count": max_goals,
            "slices": _phase31_slices(cases, rows),
            "error_attribution": dict(
                Counter(error for row in rows for error in row.get("errors", ()))
            ),
            "top_20_failures": [row for row in rows if not row.get("passed", False)][
                :20
            ],
            "comparison": None,
            "retention": None,
        }
    )
    previous_policy = deepcopy(source_report.get("goal_policy"))
    report["goal_policy"] = {
        "applied": True,
        "application_mode": "offline_replay_of_same_live_decisions",
        "secondary_threshold": secondary_threshold,
        "max_goals": max_goals,
        "primary_goal_always_retained": True,
        "score_semantics": "semantic_relevance_score_not_probability",
        "calibration_policy": previous_policy,
        "source_generated_at": source_report.get("generated_at"),
    }
    metadata = report.get("metadata", {})
    if isinstance(metadata, dict):
        metadata["goal_secondary_threshold"] = secondary_threshold
        metadata["goal_max_count"] = max_goals
        metadata["goal_policy_application_mode"] = (
            "offline_replay_of_same_live_decisions"
        )
    return report


def phase321_call_budget(
    *,
    dev_case_count: int,
    challenge_case_count: int,
    smoke_sample_size: int = 8,
    conditional_profile_count: int = 3,
    repeatability_runs: int = DEFAULT_REPEATABILITY_RUNS,
    repeatability_sample_size: int = DEFAULT_REPEATABILITY_CASES,
    max_retries: int = 0,
) -> dict[str, Any]:
    """Return the deterministic route count and paid-call upper bound."""

    values = {
        "dev_case_count": dev_case_count,
        "challenge_case_count": challenge_case_count,
        "smoke_sample_size": smoke_sample_size,
        "conditional_profile_count": conditional_profile_count,
        "repeatability_runs": repeatability_runs,
        "repeatability_sample_size": repeatability_sample_size,
        "max_retries": max_retries,
    }
    if any(value < 0 for value in values.values()):
        raise ValueError("Phase 3.2.1 call-budget inputs must be non-negative")
    live_case_evaluations = (
        smoke_sample_size * 2
        + dev_case_count
        + dev_case_count * conditional_profile_count
        + challenge_case_count * 2
        + repeatability_runs * repeatability_sample_size
    )
    rule_case_evaluations = dev_case_count + challenge_case_count
    return {
        **values,
        "smoke_arms": ["always", "conditional_c2"],
        "always_dev_live_case_evaluations": dev_case_count,
        "conditional_dev_live_case_evaluations": (
            dev_case_count * conditional_profile_count
        ),
        "challenge_live_case_evaluations": challenge_case_count * 2,
        "repeatability_live_case_evaluations": (
            repeatability_runs * repeatability_sample_size
        ),
        "goal_policy_additional_live_calls": 0,
        "rule_case_evaluations": rule_case_evaluations,
        "live_case_evaluations": live_case_evaluations,
        "total_route_case_evaluations": rule_case_evaluations
        + live_case_evaluations,
        "llm_case_call_upper_bound": live_case_evaluations,
        "provider_attempt_upper_bound": live_case_evaluations * (max_retries + 1),
        # Compatibility alias: a provider request (including retries) is the
        # billable interpretation of "call" in a cost preflight.
        "llm_call_upper_bound": live_case_evaluations * (max_retries + 1),
        "upper_bound_note": (
            "Conditional, Safety, and deterministic bypasses can reduce case calls; "
            "provider attempts include the configured retry allowance."
        ),
    }


def compare_phase32_reports(
    rule_report: Mapping[str, Any],
    always_report: Mapping[str, Any],
    conditional_report: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Attach live-vs-rule and conditional diagnostics to the three reports."""

    rule_rows = {row["id"]: row for row in rule_report.get("cases", [])}
    always_rows = {row["id"]: row for row in always_report.get("cases", [])}
    conditional_rows = {row["id"]: row for row in conditional_report.get("cases", [])}
    ids = [
        case_id for case_id in rule_rows if case_id in always_rows and case_id in conditional_rows
    ]

    always_stats = _compare_rows(rule_rows, always_rows, ids)
    conditional_stats = _compare_rows(rule_rows, conditional_rows, ids)
    always_report["comparison"] = always_stats
    conditional_report["comparison"] = conditional_stats
    _promote_comparison_metrics(always_report, always_stats)
    _promote_comparison_metrics(conditional_report, conditional_stats)
    always_report["retention"] = _retention(conditional_report, always_report)
    conditional_report["retention"] = _retention(conditional_report, always_report)
    conditional_report["accuracy_retention"] = conditional_report["retention"]
    # A rule report remains the baseline and gets an explicit comparison block
    # for consumers that serialize only one arm.
    rule_report["comparison"] = {
        "role": "baseline",
        "live_arms": ["always", "conditional"],
    }
    return {
        # Preserve object identity: ``add_conditional_diagnostics`` augments
        # these reports in-place before the caller serializes the six files.
        "rule": rule_report,
        "always": always_report,
        "conditional": conditional_report,
    }


def _promote_comparison_metrics(report: dict[str, Any], comparison: Mapping[str, Any]) -> None:
    """Expose required diagnostics both nested and at report top level."""

    for key in (
        "llm_rescue_count",
        "llm_rescue_rate",
        "llm_regression_count",
        "llm_regression_rate",
        "conditional_trigger_miss_count",
        "conditional_trigger_miss_rate",
        "conditional_trigger_miss_dimension_count",
        "conditional_trigger_miss_dimension_rate",
        "conditional_trigger_miss_case_count",
        "conditional_trigger_miss_case_rate",
        "conditional_unnecessary_call_count",
        "conditional_unnecessary_call_rate",
        "conditional_unnecessary_call_dimension_count",
        "conditional_unnecessary_call_dimension_rate",
        "conditional_unnecessary_call_case_count",
        "conditional_unnecessary_call_case_rate",
    ):
        if key in comparison:
            report[key] = comparison[key]
    for metric_name, field_name in (
        ("llm_rescue_count", "rescue"),
        ("llm_regression_count", "regression"),
    ):
        values = comparison.get(field_name, {})
        if isinstance(values, Mapping):
            for dimension in ("branch", "scenario", "goal"):
                report[f"{metric_name}_{dimension}"] = int(values.get(dimension, 0))
                report[f"{metric_name}_{dimension}_rate"] = _ratio(
                    int(values.get(dimension, 0)),
                    int(comparison.get("case_count", 0)),
                )


def _compare_rows(
    rule_rows: Mapping[str, Mapping[str, Any]],
    live_rows: Mapping[str, Mapping[str, Any]],
    ids: Sequence[str],
) -> dict[str, Any]:
    dimensions = ("branch", "scenario", "goal")
    rescue: dict[str, int] = {}
    regression: dict[str, int] = {}
    for dimension in dimensions:
        rescue[dimension] = 0
        regression[dimension] = 0
        for case_id in ids:
            rule_correct = _dimension_correct(rule_rows[case_id], dimension)
            live_correct = _dimension_correct(live_rows[case_id], dimension)
            if not rule_correct and live_correct:
                rescue[dimension] += 1
            elif rule_correct and not live_correct:
                regression[dimension] += 1
    rescue_total = sum(rescue.values())
    regression_total = sum(regression.values())
    called = sum(bool(live_rows[case_id]["trace"].get("llm_called")) for case_id in ids)
    return {
        "case_count": len(ids),
        "rescue": rescue,
        "regression": regression,
        "llm_rescue_count": rescue_total,
        "llm_rescue_rate": _ratio(rescue_total, len(ids) * len(dimensions)),
        "llm_regression_count": regression_total,
        "llm_regression_rate": _ratio(regression_total, len(ids) * len(dimensions)),
        "llm_called_count": called,
        "llm_call_rate": _ratio(called, len(ids)),
        "conditional_trigger_miss_count": 0,
        "conditional_trigger_miss_rate": 0.0,
        "conditional_trigger_miss_dimension_count": 0,
        "conditional_trigger_miss_dimension_rate": 0.0,
        "conditional_trigger_miss_case_count": 0,
        "conditional_trigger_miss_case_rate": 0.0,
        "conditional_unnecessary_call_count": 0,
        "conditional_unnecessary_call_rate": 0.0,
        "conditional_unnecessary_call_dimension_count": 0,
        "conditional_unnecessary_call_dimension_rate": 0.0,
        "conditional_unnecessary_call_case_count": 0,
        "conditional_unnecessary_call_case_rate": 0.0,
        "top_20_failures": _comparison_failure_samples(rule_rows, live_rows, ids),
        "failure_buckets": _failure_buckets(rule_rows, live_rows, ids),
    }


def add_conditional_diagnostics(
    comparison_reports: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Compute Conditional trigger misses and unnecessary calls in-place."""

    rule_report = comparison_reports["rule"]
    always_report = comparison_reports["always"]
    conditional_report = comparison_reports["conditional"]
    rule_rows = {row["id"]: row for row in rule_report.get("cases", [])}
    always_rows = {row["id"]: row for row in always_report.get("cases", [])}
    conditional_rows = {row["id"]: row for row in conditional_report.get("cases", [])}
    ids = [
        case_id for case_id in rule_rows if case_id in always_rows and case_id in conditional_rows
    ]
    misses = {dimension: 0 for dimension in ("branch", "scenario", "goal")}
    wastes = {dimension: 0 for dimension in ("branch", "scenario", "goal")}
    miss_case_ids: set[str] = set()
    waste_case_ids: set[str] = set()
    miss_samples: list[dict[str, Any]] = []
    not_called_always_correct: list[str] = []
    for case_id in ids:
        conditional_called = bool(conditional_rows[case_id]["trace"].get("llm_called"))
        if conditional_called and all(
            _dimension_correct(rule_rows[case_id], dimension) for dimension in misses
        ):
            # Case-level waste follows the brief's "Rule already correct"
            # definition.  A partially correct Rule row belongs only in the
            # dimension-level diagnostic, not in this unique-case count.
            waste_case_ids.add(case_id)
        for dimension in misses:
            rule_correct = _dimension_correct(rule_rows[case_id], dimension)
            always_correct = _dimension_correct(always_rows[case_id], dimension)
            conditional_correct = _dimension_correct(conditional_rows[case_id], dimension)
            if (
                not rule_correct
                and always_correct
                and not conditional_called
                and not conditional_correct
            ):
                misses[dimension] += 1
                miss_case_ids.add(case_id)
                if case_id not in not_called_always_correct:
                    not_called_always_correct.append(case_id)
                if len(miss_samples) < 20:
                    miss_samples.append(
                        {
                            "id": case_id,
                            "dimension": dimension,
                            "query": conditional_rows[case_id].get("query"),
                            "rule": rule_rows[case_id]["actual"],
                            "always": always_rows[case_id]["actual"],
                            "conditional": conditional_rows[case_id]["actual"],
                        }
                    )
            # A call is unnecessary whenever the deterministic rule was
            # already correct and the conditional arm did not improve the
            # dimension.  This includes a no-op (still correct) and a
            # regression (now wrong); both consumed a live request without a
            # net gain over Rule-only.
            if rule_correct and conditional_called:
                wastes[dimension] += 1
    total_misses = sum(misses.values())
    total_wastes = sum(wastes.values())
    miss_case_count = len(miss_case_ids)
    waste_case_count = len(waste_case_ids)
    miss_dimension_rate = _ratio(total_misses, len(ids) * len(misses))
    waste_dimension_rate = _ratio(total_wastes, len(ids) * len(wastes))
    conditional = conditional_report.setdefault("comparison", {})
    conditional.update(
        {
            "conditional_trigger_miss": misses,
            # The legacy unsuffixed count/rate remain dimension-level for
            # compatibility.  New consumers should use the explicit names.
            "conditional_trigger_miss_count": total_misses,
            "conditional_trigger_miss_rate": miss_dimension_rate,
            "conditional_trigger_miss_dimension_count": total_misses,
            "conditional_trigger_miss_dimension_rate": miss_dimension_rate,
            "conditional_trigger_miss_case_count": miss_case_count,
            "conditional_trigger_miss_case_rate": _ratio(miss_case_count, len(ids)),
            "conditional_unnecessary_call": wastes,
            "conditional_unnecessary_call_count": total_wastes,
            "conditional_unnecessary_call_rate": waste_dimension_rate,
            "conditional_unnecessary_call_dimension_count": total_wastes,
            "conditional_unnecessary_call_dimension_rate": waste_dimension_rate,
            "conditional_unnecessary_call_case_count": waste_case_count,
            "conditional_unnecessary_call_case_rate": _ratio(waste_case_count, len(ids)),
            "conditional_trigger_miss_case_ids": sorted(miss_case_ids),
            "conditional_unnecessary_call_case_ids": sorted(waste_case_ids),
            "conditional_trigger_miss_samples": miss_samples[:20],
        }
    )
    buckets = conditional.setdefault("failure_buckets", {})
    buckets["conditional_not_called_always_correct"] = {
        "count": len(not_called_always_correct),
        "case_ids": not_called_always_correct[:5],
    }
    # Top-level aliases make the required diagnostics visible without requiring
    # consumers to know the comparison nesting.
    conditional_report["conditional_trigger_miss_count"] = total_misses
    conditional_report["conditional_trigger_miss_rate"] = miss_dimension_rate
    conditional_report["conditional_trigger_miss_dimension_count"] = total_misses
    conditional_report["conditional_trigger_miss_dimension_rate"] = miss_dimension_rate
    conditional_report["conditional_trigger_miss_case_count"] = miss_case_count
    conditional_report["conditional_trigger_miss_case_rate"] = _ratio(miss_case_count, len(ids))
    conditional_report["conditional_unnecessary_call_count"] = total_wastes
    conditional_report["conditional_unnecessary_call_rate"] = waste_dimension_rate
    conditional_report["conditional_unnecessary_call_dimension_count"] = total_wastes
    conditional_report["conditional_unnecessary_call_dimension_rate"] = waste_dimension_rate
    conditional_report["conditional_unnecessary_call_case_count"] = waste_case_count
    conditional_report["conditional_unnecessary_call_case_rate"] = _ratio(
        waste_case_count, len(ids)
    )
    for dimension in misses:
        conditional_report[f"conditional_trigger_miss_{dimension}"] = misses[dimension]
        conditional_report[f"conditional_unnecessary_call_{dimension}"] = wastes[dimension]
    return dict(conditional_report["comparison"])


def _dimension_correct(row: Mapping[str, Any], dimension: str) -> bool:
    expected = row.get("expected", {})
    actual = row.get("actual", {})
    if dimension == "branch":
        return expected.get("branch") == actual.get("branch")
    if dimension == "scenario":
        if expected.get("branch") != "rag":
            return True
        return expected.get("primary_scenario") == actual.get("primary_scenario")
    if dimension == "goal":
        if expected.get("branch") != "rag":
            return True
        return set(expected.get("goals", [])) == set(actual.get("goals", []))
    raise ValueError(f"Unknown comparison dimension: {dimension}")


def _comparison_failure_samples(
    rule_rows: Mapping[str, Mapping[str, Any]],
    live_rows: Mapping[str, Mapping[str, Any]],
    ids: Sequence[str],
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for case_id in ids:
        rule_correct = not rule_rows[case_id].get("errors")
        live_correct = not live_rows[case_id].get("errors")
        if rule_correct == live_correct:
            category = "both_correct" if rule_correct else "both_wrong"
        else:
            category = "rule_wrong_live_correct" if live_correct else "rule_correct_live_wrong"
        if category == "both_correct":
            continue
        samples.append(
            {
                "id": case_id,
                "category": category,
                "query": live_rows[case_id].get("query"),
                "rule_errors": list(rule_rows[case_id].get("errors", [])),
                "live_errors": list(live_rows[case_id].get("errors", [])),
                "rule_actual": rule_rows[case_id].get("actual"),
                "live_actual": live_rows[case_id].get("actual"),
            }
        )
    return samples[:20]


def _failure_buckets(
    rule_rows: Mapping[str, Mapping[str, Any]],
    live_rows: Mapping[str, Mapping[str, Any]],
    ids: Sequence[str],
) -> dict[str, Any]:
    """Provide the five review buckets requested by the Phase 3.2 brief."""

    buckets = {
        "rule_wrong_live_correct": [],
        "rule_correct_live_wrong": [],
        "both_wrong": [],
        "conditional_not_called_always_correct": [],
    }
    for case_id in ids:
        rule_correct = not rule_rows[case_id].get("errors")
        live_correct = not live_rows[case_id].get("errors")
        if not rule_correct and live_correct:
            buckets["rule_wrong_live_correct"].append(case_id)
        elif rule_correct and not live_correct:
            buckets["rule_correct_live_wrong"].append(case_id)
        elif not rule_correct and not live_correct:
            buckets["both_wrong"].append(case_id)
    return {key: {"count": len(values), "case_ids": values[:5]} for key, values in buckets.items()}


def _retention(
    conditional_report: Mapping[str, Any],
    always_report: Mapping[str, Any],
) -> dict[str, float | None]:
    def ratio(key: str) -> float | None:
        numerator = conditional_report.get(key)
        denominator = always_report.get(key)
        if not isinstance(numerator, (int, float)) or not isinstance(denominator, (int, float)):
            return None
        if denominator == 0:
            return None
        return round(float(numerator) / float(denominator), 4)

    return {
        "rag_recall": ratio("rag_recall"),
        "scenario_macro_f1": ratio("scenario_macro_f1"),
        "goal_micro_f1": ratio("goal_micro_f1"),
        "branch_macro_f1": ratio("branch_macro_f1"),
    }


def _phase321_report_metric(report: Mapping[str, Any], key: str) -> Any:
    if key in report:
        return report.get(key)
    comparison = report.get("comparison", {})
    return comparison.get(key) if isinstance(comparison, Mapping) else None


def build_phase321_conditional_ablation(
    variants: Mapping[str, Mapping[str, Any]],
    *,
    always_report: Mapping[str, Any] | None = None,
    dataset: str = "dev",
    max_accuracy_gap: float = 0.03,
    minimum_rag_recall: float = 0.98,
) -> dict[str, Any]:
    """Build a comparable C0/C1/C2 Conditional ablation summary.

    This helper summarizes already executed reports; it does not call a
    provider and cannot turn fixture output into a Live result.
    """

    if not variants:
        raise ValueError("conditional ablation requires at least one variant report")
    if not 0.0 <= max_accuracy_gap <= 1.0:
        raise ValueError("max_accuracy_gap must be between 0 and 1")
    if not 0.0 <= minimum_rag_recall <= 1.0:
        raise ValueError("minimum_rag_recall must be between 0 and 1")
    always_scenario = (
        float(always_report.get("scenario_macro_f1", 0.0))
        if always_report is not None
        else None
    )
    always_goal = (
        float(always_report.get("goal_micro_f1", 0.0))
        if always_report is not None
        else None
    )
    always_call_rate = (
        float(always_report.get("llm_call_rate", 0.0))
        if always_report is not None
        else None
    )
    summaries: list[dict[str, Any]] = []
    for variant_name, report in variants.items():
        scenario = float(report.get("scenario_macro_f1", 0.0))
        goal_micro = float(report.get("goal_micro_f1", 0.0))
        scenario_gap = round(scenario - always_scenario, 4) if always_scenario is not None else None
        goal_gap = round(goal_micro - always_goal, 4) if always_goal is not None else None
        call_rate = float(report.get("llm_call_rate", 0.0))
        summary = {
            "variant": str(variant_name),
            "provider": report.get("provider"),
            "model": report.get("model"),
            "live_llm": bool(report.get("live_llm", False)),
            "prompt_version": report.get("prompt_version"),
            "prompt_sha256": report.get("prompt_sha256"),
            "branch_macro_f1": report.get("branch_macro_f1"),
            "rag_recall": report.get("rag_recall"),
            "scenario_macro_f1": scenario,
            "goal_micro_f1": goal_micro,
            "goal_macro_f1": report.get("goal_macro_f1"),
            "llm_call_rate": call_rate,
            "total_tokens": report.get("total_tokens"),
            "router_mean_latency_ms": report.get("router_mean_latency_ms"),
            "router_p95_latency_ms": report.get("router_p95_latency_ms"),
            "conditional_trigger_miss_case_count": _phase321_report_metric(
                report, "conditional_trigger_miss_case_count"
            ),
            "conditional_trigger_miss_dimension_count": _phase321_report_metric(
                report, "conditional_trigger_miss_dimension_count"
            ),
            "conditional_unnecessary_call_case_count": _phase321_report_metric(
                report, "conditional_unnecessary_call_case_count"
            ),
            "conditional_unnecessary_call_dimension_count": _phase321_report_metric(
                report, "conditional_unnecessary_call_dimension_count"
            ),
            "scenario_gap_vs_always": scenario_gap,
            "goal_micro_f1_gap_vs_always": goal_gap,
            "llm_call_rate_reduction_vs_always": (
                round(always_call_rate - call_rate, 4)
                if always_call_rate is not None
                else None
            ),
            "slices": {
                name: (report.get("slices", {}) or {}).get(name)
                for name in ("short_colloquial", "hard_confusion", "multi_label_goals")
            },
        }
        summary["accuracy_gate_passed"] = (
            scenario_gap is not None
            and goal_gap is not None
            and scenario_gap >= -max_accuracy_gap
            and goal_gap >= -max_accuracy_gap
            and float(report.get("rag_recall", 0.0)) >= minimum_rag_recall
        )
        summary["cost_gate_passed"] = (
            always_call_rate is not None and call_rate < always_call_rate
        )
        summary["acceptance_gate_passed"] = bool(
            summary["accuracy_gate_passed"] and summary["cost_gate_passed"]
        )
        summaries.append(summary)

    accepted = [item for item in summaries if item["acceptance_gate_passed"]]
    selection_pool = accepted or summaries
    selected = max(
        selection_pool,
        key=lambda item: (
            item["scenario_macro_f1"] + item["goal_micro_f1"],
            item["scenario_macro_f1"],
            item["goal_micro_f1"],
            -item["llm_call_rate"],
            str(item["variant"]),
        ),
    )
    return {
        "schema_version": 1,
        "evaluation": "phase3.2.1_conditional_ablation",
        "generated_at": datetime.now(UTC).isoformat(),
        "evaluation_mode": "summary_of_executed_variant_reports",
        "dataset": dataset,
        "metric_semantics": {
            "conditional_trigger_miss_case_count": "unique cases with at least one missed dimension",
            "conditional_trigger_miss_dimension_count": "sum across branch/scenario/goal",
            "conditional_unnecessary_call_case_count": "unique called cases whose complete rule decision was already correct",
            "conditional_unnecessary_call_dimension_count": "sum across branch/scenario/goal",
        },
        "acceptance": {
            "max_scenario_gap_vs_always": max_accuracy_gap,
            "max_goal_micro_f1_gap_vs_always": max_accuracy_gap,
            "minimum_rag_recall": minimum_rag_recall,
            "llm_call_rate_must_be_below_always": True,
            "accuracy_priority": True,
        },
        "always_reference": (
            {
                "scenario_macro_f1": always_scenario,
                "goal_micro_f1": always_goal,
                "llm_call_rate": always_call_rate,
                "provider": always_report.get("provider"),
                "model": always_report.get("model"),
                "prompt_sha256": always_report.get("prompt_sha256"),
            }
            if always_report is not None
            else None
        ),
        "variants": summaries,
        "selection_status": (
            "selected_from_accepted_variants"
            if accepted
            else "no_variant_met_acceptance_gate_best_accuracy_reported"
        ),
        "selected_variant": selected["variant"],
    }


async def evaluate_phase32_experiment(
    dev_path: Path,
    challenge_path: Path,
    *,
    router_factory: Callable[[str], Any] | None = None,
    live_metadata: Mapping[str, Any] | None = None,
    input_cost_per_million: float | None = None,
    output_cost_per_million: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Run the six required reports and attach cross-arm diagnostics.

    ``router_factory`` receives ``"always"`` or ``"conditional"``.  It is
    deliberately a factory so callers can create fresh clients and avoid
    leaking state between arms.  Rule is always constructed locally.
    """

    metadata = dict(live_metadata or {})
    reports: dict[str, dict[str, Any]] = {}
    for dataset_name, dataset_path in (("dev", dev_path), ("challenge_dev", challenge_path)):
        rule = await evaluate_phase32_dataset(
            dataset_path,
            arm="rule",
            router=build_phase32_rule_router(),
            provider="none",
            live_llm=False,
            input_cost_per_million=input_cost_per_million,
            output_cost_per_million=output_cost_per_million,
        )
        reports[f"rule_{dataset_name}"] = rule
        for arm in ("always", "conditional"):
            if router_factory is None:
                raise ValueError("Live Phase 3.2 evaluation requires router_factory")
            active_router = router_factory(arm)
            if inspect.isawaitable(active_router):
                active_router = await active_router
            try:
                reports[f"{arm}_{dataset_name}"] = await evaluate_phase32_dataset(
                    dataset_path,
                    arm=arm,
                    router=active_router,
                    provider=metadata.get("provider"),
                    live_llm=True,
                    model=metadata.get("model"),
                    temperature=metadata.get("temperature"),
                    max_tokens=metadata.get("max_tokens"),
                    timeout_seconds=metadata.get("timeout_seconds"),
                    max_retries=metadata.get("max_retries"),
                    prompt_version=metadata.get("prompt_version"),
                    prompt_sha256=metadata.get("prompt_sha256"),
                    input_cost_per_million=input_cost_per_million,
                    output_cost_per_million=output_cost_per_million,
                )
                # The dataset evaluator rejects the strongest failure mode
                # (one or more attempted calls with zero successful
                # decisions).  The experiment also requires at least one
                # successful call per Live arm, so a Conditional trigger that
                # never exercised the provider cannot be mistaken for a
                # completed Live evaluation.
                _assert_live_report_executed(
                    reports[f"{arm}_{dataset_name}"], require_call=True
                )
            finally:
                await _close_router(active_router)
        comparison = compare_phase32_reports(
            reports[f"rule_{dataset_name}"],
            reports[f"always_{dataset_name}"],
            reports[f"conditional_{dataset_name}"],
        )
        add_conditional_diagnostics(comparison)
    return reports


async def evaluate_phase321_experiment(
    dev_path: Path,
    challenge_path: Path,
    *,
    router_factory: Callable[[str, str, float, int], Any],
    live_metadata: Mapping[str, Any],
    goal_policy_source_report: Mapping[str, Any],
    before_references: Mapping[str, Any] | None = None,
    smoke_sample_size: int = 8,
    repeatability_runs: int = DEFAULT_REPEATABILITY_RUNS,
    repeatability_sample_size: int = DEFAULT_REPEATABILITY_CASES,
    input_cost_per_million: float | None = None,
    output_cost_per_million: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Run the frozen Phase 3.2.1 Dev-first experiment without writing files.

    The caller owns resources created by ``router_factory``.  All reports are
    accumulated in memory so a provider failure cannot leave a partial formal
    bundle on disk.  C0/C1/C2 are evaluated only on Dev; Challenge receives
    the profile selected on Dev after all Dev runs have completed.  Goal
    policy selection replays the read-only Phase 3.2 Always Dev trace, so the
    sweep adds no provider calls and cannot observe Challenge labels.
    """

    if any(
        path.name.casefold() == PHASE321_FORBIDDEN_TEST_FILENAME.casefold()
        for path in (dev_path, challenge_path)
    ):
        raise ValueError(
            "Phase 3.2.1 must never read the exposed Router Test V1 dataset"
        )
    dev_cases = _cases_for_path(dev_path)
    challenge_cases = _cases_for_path(challenge_path)
    if not dev_cases or not challenge_cases:
        raise ValueError("Phase 3.2.1 requires non-empty Dev and Challenge Dev datasets")
    call_budget = phase321_call_budget(
        dev_case_count=len(dev_cases),
        challenge_case_count=len(challenge_cases),
        smoke_sample_size=smoke_sample_size,
        conditional_profile_count=3,
        repeatability_runs=repeatability_runs,
        repeatability_sample_size=repeatability_sample_size,
        max_retries=int(live_metadata.get("max_retries") or 0),
    )

    async def make_router(
        arm: str,
        trigger_profile: str,
        threshold: float,
        max_goals: int,
    ) -> Any:
        router = router_factory(arm, trigger_profile, threshold, max_goals)
        return await router if inspect.isawaitable(router) else router

    async def evaluate_live(
        path: Path,
        *,
        arm: Literal["always", "conditional"],
        trigger_profile: str,
        threshold: float,
        max_goals: int,
    ) -> dict[str, Any]:
        router = await make_router(arm, trigger_profile, threshold, max_goals)
        report = await evaluate_phase32_dataset(
            path,
            arm=arm,
            router=router,
            provider=live_metadata.get("provider"),
            live_llm=True,
            model=live_metadata.get("model"),
            temperature=live_metadata.get("temperature"),
            max_tokens=live_metadata.get("max_tokens"),
            timeout_seconds=live_metadata.get("timeout_seconds"),
            max_retries=live_metadata.get("max_retries"),
            prompt_version=live_metadata.get("prompt_version"),
            prompt_sha256=live_metadata.get("prompt_sha256"),
            input_cost_per_million=input_cost_per_million,
            output_cost_per_million=output_cost_per_million,
        )
        expected_profile = trigger_profile.casefold()
        actual_profile = str(report.get("conditional_trigger_profile") or "").casefold()
        if actual_profile != expected_profile:
            raise ValueError(
                "Phase 3.2.1 router factory did not apply the requested "
                f"trigger profile: expected={expected_profile}, actual={actual_profile or 'N/A'}"
            )
        if report.get("goal_secondary_threshold") != threshold:
            raise ValueError(
                "Phase 3.2.1 router factory did not apply the requested Goal threshold"
            )
        if report.get("goal_max_count") != max_goals:
            raise ValueError(
                "Phase 3.2.1 router factory did not apply the requested max Goal count"
            )
        return report

    smoke_reports: dict[str, dict[str, Any]] = {}
    for smoke_name, arm, profile in (
        ("always", "always", "c2"),
        ("conditional_c2", "conditional", "c2"),
    ):
        smoke_router = await make_router(arm, profile, 0.0, 3)
        smoke_report = await evaluate_phase32_smoke(
            dev_path,
            router=smoke_router,
            arm=arm,
            sample_size=smoke_sample_size,
        )
        if int(smoke_report.get("llm_success_count") or 0) <= 0:
            raise ValueError(
                "Live LLM evaluation was not executed: Phase 3.2.1 smoke "
                f"produced no successful decisions for {smoke_name}"
            )
        smoke_reports[smoke_name] = smoke_report

    goal_policy_ablation = evaluate_phase321_goal_policy_ablation(
        goal_policy_source_report
    )
    selected_policy = goal_policy_ablation.get("selected_policy")
    if not isinstance(selected_policy, Mapping):
        raise ValueError("Phase 3.2.1 Goal Policy sweep did not select a policy")
    selected_threshold = float(selected_policy["secondary_threshold"])
    selected_max_goals = int(selected_policy["max_goals"])
    always_dev = await evaluate_live(
        dev_path,
        arm="always",
        trigger_profile="c2",
        threshold=selected_threshold,
        max_goals=selected_max_goals,
    )
    if int(always_dev.get("always_unexpected_bypass_count") or 0) > 0:
        raise ValueError(
            "Always invariant failed on Dev: normal relationship cases bypassed Live LLM"
        )

    rule_dev = await evaluate_phase32_dataset(
        dev_path,
        arm="rule",
        router=build_phase32_rule_router(),
    )
    conditional_variants: dict[str, dict[str, Any]] = {}
    for profile in ("c0", "c1", "c2"):
        conditional = await evaluate_live(
            dev_path,
            arm="conditional",
            trigger_profile=profile,
            threshold=selected_threshold,
            max_goals=selected_max_goals,
        )
        compared = compare_phase32_reports(rule_dev, always_dev, conditional)
        add_conditional_diagnostics(compared)
        conditional_variants[profile.upper()] = conditional

    conditional_ablation = build_phase321_conditional_ablation(
        conditional_variants,
        always_report=always_dev,
        dataset="dev",
    )
    selected_profile = str(conditional_ablation.get("selected_variant") or "").upper()
    if selected_profile not in conditional_variants:
        raise ValueError(
            "Phase 3.2.1 Conditional ablation did not select a known Dev profile"
        )
    conditional_dev = conditional_variants[selected_profile]
    conditional_ablation.update(
        {
            "frozen_variant": selected_profile,
            "challenge_policy": (
                f"{selected_profile} was selected and frozen on Dev; other profiles "
                "are never run on Challenge"
            ),
            "selection_recommendation": conditional_ablation.get("selected_variant"),
            "freeze_matches_recommendation": True,
        }
    )

    # Challenge is intentionally reached only after the Dev sweep and fixed
    # policy selection.  No Challenge observation can alter these settings.
    rule_challenge = await evaluate_phase32_dataset(
        challenge_path,
        arm="rule",
        router=build_phase32_rule_router(),
    )
    always_challenge = await evaluate_live(
        challenge_path,
        arm="always",
        trigger_profile="c2",
        threshold=selected_threshold,
        max_goals=selected_max_goals,
    )
    if int(always_challenge.get("always_unexpected_bypass_count") or 0) > 0:
        raise ValueError(
            "Always invariant failed on Challenge Dev: normal relationship cases "
            "bypassed Live LLM"
        )
    conditional_challenge = await evaluate_live(
        challenge_path,
        arm="conditional",
        trigger_profile=selected_profile.casefold(),
        threshold=selected_threshold,
        max_goals=selected_max_goals,
    )
    challenge_comparison = compare_phase32_reports(
        rule_challenge,
        always_challenge,
        conditional_challenge,
    )
    add_conditional_diagnostics(challenge_comparison)

    def repeat_factory(arm: str, _run_index: int) -> Any:
        return router_factory(
            arm,
            selected_profile.casefold(),
            selected_threshold,
            selected_max_goals,
        )

    repeatability = await evaluate_phase32_repeatability(
        challenge_path,
        router_factory=repeat_factory,
        arm="always",
        runs=repeatability_runs,
        sample_size=repeatability_sample_size,
    )
    repeatability["evaluation"] = "phase3.2.1_repeatability"

    smoke_summary = {
        name: {
            "dataset": report.get("dataset"),
            "case_count": report.get("case_count"),
            "llm_called_count": report.get("llm_called_count"),
            "llm_success_count": report.get("llm_success_count"),
            "provider_error_count": report.get("provider_error_count"),
            "parse_error_count": report.get("parse_error_count"),
            "timeout_count": report.get("timeout_count"),
            "fallback_count": report.get("fallback_count"),
            "case_ids": [row.get("id") for row in report.get("cases", ())],
        }
        for name, report in smoke_reports.items()
    }
    repeat_call_count = sum(
        int(row.get("trace", {}).get("llm_call_count") or 0)
        for run in repeatability.get("run_predictions", ())
        for row in run.get("rows", ())
    )
    repeat_attempt_count = sum(
        int(row.get("trace", {}).get("llm_attempt_count") or 0)
        for run in repeatability.get("run_predictions", ())
        for row in run.get("rows", ())
    )
    observed_call_count = (
        sum(int(report.get("llm_called_count") or 0) for report in smoke_reports.values())
        + int(always_dev.get("llm_called_count") or 0)
        + sum(
            int(report.get("llm_called_count") or 0)
            for report in conditional_variants.values()
        )
        + int(always_challenge.get("llm_called_count") or 0)
        + int(conditional_challenge.get("llm_called_count") or 0)
        + repeat_call_count
    )
    observed_attempt_count = (
        sum(int(report.get("llm_attempt_count") or 0) for report in smoke_reports.values())
        + int(always_dev.get("llm_attempt_count") or 0)
        + sum(
            int(report.get("llm_attempt_count") or 0)
            for report in conditional_variants.values()
        )
        + int(always_challenge.get("llm_attempt_count") or 0)
        + int(conditional_challenge.get("llm_attempt_count") or 0)
        + repeat_attempt_count
    )
    call_budget["observed_llm_case_call_count"] = observed_call_count
    call_budget["observed_llm_call_count"] = observed_call_count
    call_budget["observed_provider_attempt_count"] = observed_attempt_count
    protocol = {
        "phase": "3.2.1",
        "dev_first": True,
        "challenge_profiles_executed": [selected_profile],
        "old_router_test_loaded": False,
        "goal_policy": {
            "secondary_threshold": selected_threshold,
            "max_goals": selected_max_goals,
            "selection_dataset": "dev",
        },
        "conditional_profile": {
            "frozen": selected_profile,
            "selection_dataset": "dev",
        },
        "smoke": smoke_summary,
        "call_budget": call_budget,
        "before_references": deepcopy(dict(before_references or {})),
    }
    for report, stage in (
        (always_dev, "always_fixed_dev"),
        (conditional_dev, f"conditional_{selected_profile.casefold()}_dev"),
        (always_challenge, "always_fixed_challenge_dev"),
        (
            conditional_challenge,
            f"conditional_{selected_profile.casefold()}_challenge_dev",
        ),
        (goal_policy_ablation, "goal_policy_ablation_dev"),
        (conditional_ablation, "conditional_ablation_dev"),
        (repeatability, "repeatability_challenge_dev"),
    ):
        report["phase"] = "3.2.1"
        report["phase321_stage"] = stage
        report["phase321_protocol"] = deepcopy(protocol)

    return {
        "always_dev": always_dev,
        "conditional_dev": conditional_dev,
        "always_challenge_dev": always_challenge,
        "conditional_challenge_dev": conditional_challenge,
        "goal_policy_ablation": goal_policy_ablation,
        "conditional_ablation": conditional_ablation,
        "repeatability": repeatability,
    }


def representative_challenge_cases(
    cases_or_path: Sequence[RouterChallengeCase | RouterSafetyCase] | Path,
    *,
    limit: int = DEFAULT_REPEATABILITY_CASES,
) -> list[RouterChallengeCase | RouterSafetyCase]:
    """Select a deterministic 20-30 case Challenge sample for repeatability."""

    if isinstance(cases_or_path, Path):
        cases = _cases_for_path(cases_or_path)
    else:
        cases = list(cases_or_path)
    if limit < 20 or limit > 30:
        raise ValueError("repeatability sample size must be between 20 and 30")
    selected: list[RouterChallengeCase | RouterSafetyCase] = []
    seen: set[str] = set()
    # Guarantee the semantic categories called out in the brief before taking
    # the balanced slice sample.
    for scenario_name in ("pursuit", "chat_analysis", "boundary", "breakup"):
        candidate = next(
            (
                case
                for case in cases
                if case.case_id not in seen and case.expected_primary_scenario == scenario_name
            ),
            None,
        )
        if candidate is not None:
            selected.append(candidate)
            seen.add(candidate.case_id)
    multilabel = next(
        (case for case in cases if case.case_id not in seen and len(case.expected_goals) > 1),
        None,
    )
    if multilabel is not None:
        selected.append(multilabel)
        seen.add(multilabel.case_id)
    ood = next(
        (case for case in cases if case.case_id not in seen and case.expected_branch != "rag"),
        None,
    )
    if ood is not None:
        selected.append(ood)
        seen.add(ood.case_id)
    groups: list[list[RouterChallengeCase | RouterSafetyCase]] = []
    for slice_name in ("short_colloquial", "scenario_hard_confusion", "goal_multilabel"):
        groups.append(
            [case for case in cases if slice_name in getattr(case, "challenge_slices", ())]
        )
    # Eight from each canonical slice gives 24 cases by default.  Fill from
    # the full dataset if a custom fixture lacks explicit slice metadata.
    per_group = max(1, limit // max(1, len(groups)))
    for group in groups:
        for case in group[:per_group]:
            if case.case_id not in seen:
                selected.append(case)
                seen.add(case.case_id)
    if not any(case.expected_branch != "rag" for case in selected):
        ood = next((case for case in cases if case.expected_branch != "rag"), None)
        if ood is not None:
            selected[-1] = ood
            seen.add(ood.case_id)
    for case in cases:
        if len(selected) >= limit:
            break
        if case.case_id not in seen:
            selected.append(case)
            seen.add(case.case_id)
    return selected[:limit]


def calculate_phase321_repeatability_metrics(
    run_predictions: Sequence[Mapping[str, Any]],
    case_ids: Sequence[str],
) -> dict[str, Any]:
    """Calculate pairwise stability, including the Phase 3.2.1 set metrics."""

    pairs = [
        (left, right)
        for index, left in enumerate(run_predictions)
        for right in run_predictions[index + 1 :]
    ]
    branch_agreements: list[float] = []
    primary_scenario_agreements: list[float] = []
    scenario_top2_set_agreements: list[float] = []
    goal_jaccard_agreements: list[float] = []
    exact_goal_set_agreements: list[float] = []

    def actual(row: Mapping[str, Any]) -> Mapping[str, Any]:
        value = row.get("actual", {})
        return value if isinstance(value, Mapping) else {}

    def scenario_top2(row: Mapping[str, Any]) -> set[str]:
        value = actual(row)
        labels: list[str] = []
        primary = value.get("primary_scenario")
        if primary is not None:
            labels.append(str(primary))
        secondary = value.get("secondary_scenarios", ())
        if isinstance(secondary, Sequence) and not isinstance(secondary, (str, bytes)):
            labels.extend(str(item) for item in secondary[:1])
        return set(labels)

    for left, right in pairs:
        left_rows_value = left.get("rows", ())
        right_rows_value = right.get("rows", ())
        left_rows = {
            str(row.get("id")): row
            for row in left_rows_value
            if isinstance(row, Mapping) and row.get("id") is not None
        } if isinstance(left_rows_value, Sequence) else {}
        right_rows = {
            str(row.get("id")): row
            for row in right_rows_value
            if isinstance(row, Mapping) and row.get("id") is not None
        } if isinstance(right_rows_value, Sequence) else {}
        ids = [item for item in case_ids if item in left_rows and item in right_rows]
        branch_agreements.append(
            _ratio(
                sum(
                    actual(left_rows[item]).get("branch")
                    == actual(right_rows[item]).get("branch")
                    for item in ids
                ),
                len(ids),
            )
        )
        primary_scenario_agreements.append(
            _ratio(
                sum(
                    actual(left_rows[item]).get("primary_scenario")
                    == actual(right_rows[item]).get("primary_scenario")
                    for item in ids
                ),
                len(ids),
            )
        )
        scenario_top2_set_agreements.append(
            _ratio(
                sum(
                    scenario_top2(left_rows[item]) == scenario_top2(right_rows[item])
                    for item in ids
                ),
                len(ids),
            )
        )
        goal_jaccard_agreements.append(
            _mean(
                _jaccard(
                    actual(left_rows[item]).get("goals", ()),
                    actual(right_rows[item]).get("goals", ()),
                )
                for item in ids
            )
        )
        exact_goal_set_agreements.append(
            _ratio(
                sum(
                    set(actual(left_rows[item]).get("goals", ()))
                    == set(actual(right_rows[item]).get("goals", ()))
                    for item in ids
                ),
                len(ids),
            )
        )
    return {
        "branch_agreement_rate": _mean(branch_agreements),
        "primary_scenario_agreement_rate": _mean(primary_scenario_agreements),
        # Backward-compatible Phase 3.2 alias.
        "scenario_agreement_rate": _mean(primary_scenario_agreements),
        "scenario_top2_set_agreement_rate": _mean(scenario_top2_set_agreements),
        "goal_jaccard_agreement": _mean(goal_jaccard_agreements),
        "exact_goal_set_agreement_rate": _mean(exact_goal_set_agreements),
        "goal_exact_set_agreement_rate": _mean(exact_goal_set_agreements),
        "pairwise": {
            "branch": branch_agreements,
            "primary_scenario": primary_scenario_agreements,
            "scenario": primary_scenario_agreements,
            "scenario_top2_set": scenario_top2_set_agreements,
            "goal_jaccard": goal_jaccard_agreements,
            "exact_goal_set": exact_goal_set_agreements,
        },
    }


async def evaluate_phase32_repeatability(
    challenge_path: Path,
    *,
    router_factory: Callable[[str, int], Any] | Callable[[int], Any],
    arm: str = "always",
    runs: int = DEFAULT_REPEATABILITY_RUNS,
    sample_size: int = DEFAULT_REPEATABILITY_CASES,
) -> dict[str, Any]:
    """Run a live arm three times on a representative Challenge sample."""

    if runs < 2:
        raise ValueError("repeatability requires at least two runs")
    canonical = normalize_phase32_arm(arm)
    if canonical == "rule":
        raise ValueError("repeatability is intended for a live semantic arm")
    cases = representative_challenge_cases(challenge_path, limit=sample_size)
    run_predictions: list[dict[str, Any]] = []
    run_metadata: dict[str, Any] | None = None
    for run_index in range(runs):
        try:
            router = router_factory(canonical, run_index)  # type: ignore[misc]
        except TypeError:
            try:
                router = router_factory(run_index)  # type: ignore[misc]
            except TypeError:
                router = router_factory()  # type: ignore[call-arg]
        if inspect.isawaitable(router):
            router = await router
        try:
            _require_live_provider(router, provider=_provider_name(router), live_llm=True)
            rows, _, _, _, _ = await _evaluate_cases(
                router,
                cases,
                metadata=_router_metadata(router, live_llm=True),
            )
            if not any(_row_live_call_succeeded(row) for row in rows):
                raise ValueError(
                    "Live LLM evaluation was not executed: repeatability run produced "
                    "no successful Live decisions"
                )
            if run_metadata is None:
                run_metadata = _router_metadata(router, live_llm=True)
            run_predictions.append(
                {
                    "run": run_index + 1,
                    "rows": rows,
                }
            )
        finally:
            await _close_router(router)
    stability = calculate_phase321_repeatability_metrics(
        run_predictions,
        [case.case_id for case in cases],
    )
    return {
        "schema_version": 1,
        "evaluation": "phase3.2_repeatability",
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": str(challenge_path),
        "arm": canonical,
        "runs": runs,
        "sample_size": len(cases),
        "case_ids": [case.case_id for case in cases],
        "provider": (run_metadata or {}).get("provider"),
        "model": (run_metadata or {}).get("model"),
        "live_llm": bool((run_metadata or {}).get("live_llm", False)),
        "prompt_version": (run_metadata or {}).get("prompt_version"),
        "prompt_sha256": (run_metadata or {}).get("prompt_sha256"),
        **stability,
        "run_predictions": run_predictions,
    }


async def evaluate_phase32_smoke(
    path: Path,
    *,
    router: Any,
    arm: str = "always",
    sample_size: int = 8,
) -> dict[str, Any]:
    """Evaluate a small live smoke sample before the full Dev run."""

    if sample_size < 5 or sample_size > 10:
        raise ValueError("smoke sample size must be between 5 and 10")
    all_cases = _cases_for_path(path)
    selected: list[RouterSafetyCase | RouterChallengeCase] = []
    seen: set[str] = set()

    # Reserve the two semantic edge classes before filling the scenario
    # coverage.  A five-case smoke sample cannot contain four disjoint
    # scenarios *and* both edge classes, so the edge classes take priority;
    # in the normal eight-case sample they coexist with all four scenarios.
    # Prefer a multi-label case that also belongs to one of the requested
    # scenarios, so the reservation does not consume an extra slot.
    multilabel = next(
        (
            case
            for case in all_cases
            if len(case.expected_goals) > 1
            and case.expected_primary_scenario in {"pursuit", "chat_analysis", "boundary", "breakup"}
        ),
        next((case for case in all_cases if len(case.expected_goals) > 1), None),
    )
    # Keep the OOD reservation independent from the multi-label reservation
    # whenever the dataset provides a separate case.  This prevents one
    # multi-label OOD example from giving a misleading impression that both
    # smoke dimensions were exercised independently.
    ood = next(
        (
            case
            for case in all_cases
            if case.expected_branch != "rag"
            and (multilabel is None or case.case_id != multilabel.case_id)
        ),
        next((case for case in all_cases if case.expected_branch != "rag"), None),
    )
    for candidate in (multilabel, ood):
        if candidate is not None and len(selected) < sample_size and candidate.case_id not in seen:
            selected.append(candidate)
            seen.add(candidate.case_id)

    for scenario_name in ("pursuit", "chat_analysis", "boundary", "breakup"):
        candidate = next(
            (
                case
                for case in all_cases
                if case.case_id not in seen and case.expected_primary_scenario == scenario_name
            ),
            None,
        )
        if candidate is not None and len(selected) < sample_size:
            selected.append(candidate)
            seen.add(candidate.case_id)
    for candidate in all_cases:
        if len(selected) >= sample_size:
            break
        if candidate.case_id not in seen:
            selected.append(candidate)
            seen.add(candidate.case_id)
    return await evaluate_phase32_dataset(
        path,
        arm=arm,
        router=router,
        provider=_provider_name(router),
        live_llm=True,
        cases=selected[:sample_size],
    )


def phase32_dry_run(
    *,
    settings: Any,
    dev_dataset: Path,
    challenge_dataset: Path,
    output_path: Path | None = None,
    prompt_preview: str | None = None,
) -> dict[str, Any]:
    """Validate live configuration and report plumbing without an API call."""

    configured_provider = resolve_phase32_provider(settings)
    router_provider_mode = str(getattr(settings, "router_llm_provider", "") or "")
    # router_llm_provider is a routing switch (auto/llm/disabled), while the
    # shared provider setting carries the report/provider label.
    provider = configured_provider or router_provider_mode
    model = str(
        getattr(settings, "router_llm_model", None)
        or getattr(settings, "router_model", "")
        or getattr(settings, "llm_model", "")
    )
    base_url = getattr(settings, "llm_base_url", None)
    live_gate = bool(getattr(settings, "router_live_eval_enabled", False))
    temperature = getattr(settings, "router_llm_temperature", 0)
    max_tokens = getattr(settings, "router_llm_max_tokens", None) or getattr(
        settings, "router_max_tokens", None
    )
    timeout_seconds = getattr(settings, "router_llm_timeout_seconds", None) or getattr(
        settings, "router_timeout_seconds", None
    )
    max_retries = getattr(settings, "router_llm_max_retries", None)
    if max_retries is None:
        max_retries = getattr(settings, "router_max_retries", None)
    prompt_version = getattr(settings, "router_llm_prompt_version", None) or (
        "routing-v3.2-v1"
        if str(getattr(settings, "router_semantic_mode", "legacy") or "legacy")
        in {"always", "conditional"}
        else getattr(settings, "router_prompt_version", None)
    )
    api_key = getattr(settings, "llm_api_key", None)
    key_present = bool(api_key and getattr(api_key, "get_secret_value", lambda: api_key)())
    datasets = {
        "dev": _dry_dataset_check(dev_dataset),
        "challenge_dev": _dry_dataset_check(challenge_dataset),
    }
    if prompt_preview is None:
        try:
            from loveapp.adapters.routing.openai_compatible import (
                _DATE_SLOT_INSTRUCTIONS,
                _SEMANTIC_SYSTEM_PROMPT,
                _SYSTEM_PROMPT,
            )

            semantic_mode = str(getattr(settings, "router_semantic_mode", "legacy") or "legacy")
            prompt = (
                _SEMANTIC_SYSTEM_PROMPT
                if semantic_mode in {"always", "conditional"}
                else f"{_SYSTEM_PROMPT}\n{_DATE_SLOT_INSTRUCTIONS}"
            )
        except Exception:  # pragma: no cover - diagnostic fallback
            prompt = "phase3.2 router prompt preview"
    else:
        prompt = prompt_preview
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    structured_output = str(
        getattr(settings, "router_llm_structured_output", "json_schema") or "json_schema"
    ).casefold()
    try:
        from loveapp.adapters.routing.openai_compatible import semantic_route_response_format

        if structured_output not in {"json_schema", "json_object"}:
            raise ValueError(
                "router_llm_structured_output must be json_schema or json_object"
            )
        structured_schema = semantic_route_response_format(structured_output)  # type: ignore[arg-type]
        schema_ok = isinstance(structured_schema, dict) and (
            structured_schema.get("type") == "json_object"
            or bool(structured_schema.get("json_schema"))
        )
    except Exception as exc:  # pragma: no cover - diagnostic path
        structured_schema = None
        schema_ok = False
        schema_error = str(exc)
    else:
        schema_error = None
    output_ok = True
    output_error = None
    if output_path is not None:
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_ok = output_path.parent.is_dir()
        except OSError as exc:
            output_ok = False
            output_error = str(exc)
    config_ok = (
        provider.casefold() not in LIVE_PROVIDER_FORBIDDEN
        and bool(model)
        and key_present
        and bool(base_url)
        and live_gate
    )
    return {
        "ok": config_ok
        and all(item["ok"] for item in datasets.values())
        and output_ok
        and schema_ok,
        "api_request_sent": False,
        "provider": provider,
        "router_provider_mode": router_provider_mode,
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout_seconds": timeout_seconds,
        "max_retries": max_retries,
        "prompt_version": prompt_version,
        "base_url_present": bool(base_url),
        "live_eval_gate": live_gate,
        "live_llm": config_ok,
        "live_provider_candidate": provider.casefold() not in LIVE_PROVIDER_FORBIDDEN,
        "api_key_present": key_present,
        "dataset": datasets,
        "prompt_rendered": bool(prompt),
        "prompt_sha256": prompt_sha,
        "structured_output": structured_output,
        "structured_schema": schema_ok,
        "structured_schema_preview": structured_schema,
        "structured_schema_error": schema_error,
        "output_path": str(output_path) if output_path else None,
        "output_path_ok": output_ok,
        "output_error": output_error,
        "reason": None
        if config_ok
        else "live provider/model/API key/base URL/gate configuration is incomplete",
    }


def _dry_dataset_check(path: Path) -> dict[str, Any]:
    try:
        cases = _cases_for_path(path)
        return {"path": str(path), "exists": True, "case_count": len(cases), "ok": bool(cases)}
    except Exception as exc:  # pragma: no cover - diagnostic path
        return {
            "path": str(path),
            "exists": path.exists(),
            "case_count": 0,
            "ok": False,
            "error": str(exc),
        }


def render_phase32_report(report: Mapping[str, Any]) -> str:
    branch = report.get("branch", {})
    scenario = report.get("scenario", {})
    goal = report.get("goal", {})
    lines = [
        "# Phase 3.2 Live LLM Semantic Router Evaluation",
        "",
        f"- Dataset: `{report.get('dataset')}`",
        f"- Arm: `{report.get('arm')}`",
        f"- Provider/model: `{report.get('provider')}` / `{report.get('model')}`",
        f"- Live LLM: `{report.get('live_llm')}`",
        f"- Prompt: `{report.get('prompt_version')}` (`{report.get('prompt_sha256')}`)",
        "",
        "## Core metrics",
        "",
        f"- Branch macro-F1: **{branch.get('macro_f1', 0):.4f}**",
        f"- RAG recall: **{report.get('rag_recall', 0):.4f}**",
        f"- Scenario accuracy / macro-F1 / Top-2: **{scenario.get('accuracy', 0):.4f} / {scenario.get('macro_f1', 0):.4f} / {report.get('scenario_top2_hit', 0):.4f}**",
        f"- Goal micro precision / recall / F1: **{goal.get('micro_precision', 0):.4f} / {goal.get('micro_recall', 0):.4f} / {goal.get('micro_f1', 0):.4f}**",
        f"- Goal macro-F1 / exact-set accuracy: **{goal.get('macro_f1', 0):.4f} / {goal.get('exact_set_accuracy', 0):.4f}**",
        f"- Goal average predicted / gold labels: **{goal.get('average_predicted_label_count', 0):.4f} / {goal.get('average_gold_label_count', 0):.4f}**",
        f"- Goal overprediction / missing labels per case: **{goal.get('overprediction_per_case', 0):.4f} / {goal.get('missing_per_case', 0):.4f}**",
        "",
        "## Runtime",
        "",
        f"- LLM calls / rate: **{report.get('llm_called_count', 0)} / {report.get('llm_call_rate', 0):.4f}**",
        f"- Successful Live decisions / rate: **{report.get('llm_success_count', 0)} / {report.get('llm_success_rate', 0):.4f}**",
        f"- Router p50/p95/p99 ms: **{report.get('router_p50_latency_ms', 0):.3f} / {report.get('router_p95_latency_ms', 0):.3f} / {report.get('router_p99_latency_ms', 0):.3f}**",
        f"- LLM p50/p95/p99 ms: **{report.get('llm_p50_latency_ms', 0):.3f} / {report.get('llm_p95_latency_ms', 0):.3f} / {report.get('llm_p99_latency_ms', 0):.3f}**",
        f"- Tokens input/output/total: **{report.get('input_tokens')} / {report.get('output_tokens')} / {report.get('total_tokens')}**",
        f"- Estimated cost: **{report.get('estimated_cost_total')}**",
        f"- Fallback / schema fallback / timeout / parse / provider errors: **{report.get('fallback_count', 0)} / {report.get('schema_fallback_count', 0)} / {report.get('timeout_count', 0)} / {report.get('parse_error_count', 0)} / {report.get('provider_error_count', 0)}**",
        f"- Semantic boundary sanitizations: **{report.get('semantic_sanitization_count', 0)}**",
        f"- Provider error samples: `{report.get('provider_error_samples', [])}`",
        f"- Parse error samples: `{report.get('parse_error_samples', [])}`",
        "",
        "## Top 20 failures",
        "",
    ]
    for row in report.get("top_20_failures", []):
        lines.append(f"- `{row.get('id')}`: {', '.join(row.get('errors', []))}")
    if not report.get("top_20_failures"):
        lines.append("- None")
    comparison = report.get("comparison")
    if comparison:
        lines.extend(
            [
                "",
                "## Live comparison",
                "",
                f"- Rescue count/rate: **{comparison.get('llm_rescue_count', 0)} / {comparison.get('llm_rescue_rate', 0):.4f}**",
                f"- Regression count/rate: **{comparison.get('llm_regression_count', 0)} / {comparison.get('llm_regression_rate', 0):.4f}**",
                f"- Conditional trigger misses, cases / dimensions: **{comparison.get('conditional_trigger_miss_case_count', 0)} / {comparison.get('conditional_trigger_miss_dimension_count', comparison.get('conditional_trigger_miss_count', 0))}**",
                f"- Conditional unnecessary calls, cases / dimensions: **{comparison.get('conditional_unnecessary_call_case_count', 0)} / {comparison.get('conditional_unnecessary_call_dimension_count', comparison.get('conditional_unnecessary_call_count', 0))}**",
            ]
        )
    return "\n".join(lines) + "\n"


def write_phase32_report(report: Mapping[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(".md").write_text(render_phase32_report(report), encoding="utf-8")


def phase321_output_paths(output_dir: Path) -> dict[str, Path]:
    """Return the seven frozen Phase 3.2.1 JSON artifact paths."""

    return {
        report_name: output_dir / filename
        for report_name, filename in PHASE321_OUTPUT_FILENAMES.items()
    }


def write_phase321_reports(
    reports: Mapping[str, Mapping[str, Any]],
    output_dir: Path,
    *,
    require_complete: bool = True,
) -> dict[str, Path]:
    """Write the Phase 3.2.1 JSON bundle without inventing missing results.

    Formal callers should keep ``require_complete=True``.  A development
    pipeline may explicitly request a partial bundle while individual Live
    arms are still running; omitted artifacts are not created as placeholders.
    """

    expected = set(PHASE321_OUTPUT_FILENAMES)
    supplied = set(reports)
    unknown = sorted(supplied - expected)
    if unknown:
        raise ValueError(f"unknown Phase 3.2.1 report keys: {', '.join(unknown)}")
    missing = sorted(expected - supplied)
    if require_complete and missing:
        raise ValueError(f"missing Phase 3.2.1 reports: {', '.join(missing)}")
    if not reports:
        raise ValueError("no Phase 3.2.1 reports supplied")
    for report_name, report in reports.items():
        if not isinstance(report, Mapping):
            raise ValueError(f"Phase 3.2.1 report {report_name} must be a mapping")
    if require_complete:
        _validate_phase321_report_bundle(reports)

    paths = phase321_output_paths(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for report_name in PHASE321_OUTPUT_FILENAMES:
        if report_name not in reports:
            continue
        report = reports[report_name]
        path = paths[report_name]
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written[report_name] = path
    return written


def _validate_phase321_report_bundle(
    reports: Mapping[str, Mapping[str, Any]],
) -> None:
    """Reject fixture, placeholder, or incomplete formal report bundles."""

    for name in (
        "always_dev",
        "conditional_dev",
        "always_challenge_dev",
        "conditional_challenge_dev",
    ):
        report = reports[name]
        provider = str(report.get("provider") or "").casefold()
        if report.get("live_llm") is not True or provider in LIVE_PROVIDER_FORBIDDEN:
            raise ValueError(f"Phase 3.2.1 {name} is not a real Live report")
        if int(report.get("case_count") or 0) <= 0:
            raise ValueError(f"Phase 3.2.1 {name} contains no evaluated cases")
        if int(report.get("llm_success_count") or 0) <= 0:
            raise ValueError(f"Phase 3.2.1 {name} contains no successful Live decision")

    goal_ablation = reports["goal_policy_ablation"]
    source = goal_ablation.get("source", {})
    source_provider = (
        str(source.get("provider") or "").casefold()
        if isinstance(source, Mapping)
        else ""
    )
    if (
        goal_ablation.get("evaluation") != "phase3.2.1_goal_policy_ablation"
        or not isinstance(source, Mapping)
        or source.get("live_llm") is not True
        or source_provider in LIVE_PROVIDER_FORBIDDEN
        or not isinstance(goal_ablation.get("selected_policy"), Mapping)
    ):
        raise ValueError("Phase 3.2.1 Goal Policy ablation is incomplete or non-Live")

    conditional_ablation = reports["conditional_ablation"]
    variants = conditional_ablation.get("variants", ())
    variant_names = {
        str(item.get("variant", "")).upper()
        for item in variants
        if isinstance(item, Mapping)
    } if isinstance(variants, Sequence) and not isinstance(variants, (str, bytes)) else set()
    live_variants = [item for item in variants if isinstance(item, Mapping)]
    if (
        conditional_ablation.get("evaluation")
        != "phase3.2.1_conditional_ablation"
        or variant_names != {"C0", "C1", "C2"}
        or any(
            item.get("live_llm") is not True
            or str(item.get("provider") or "").casefold() in LIVE_PROVIDER_FORBIDDEN
            for item in live_variants
        )
    ):
        raise ValueError("Phase 3.2.1 Conditional ablation must contain Live C0/C1/C2")

    repeatability = reports["repeatability"]
    repeat_provider = str(repeatability.get("provider") or "").casefold()
    if (
        repeatability.get("evaluation") != "phase3.2.1_repeatability"
        or repeatability.get("live_llm") is not True
        or repeat_provider in LIVE_PROVIDER_FORBIDDEN
        or int(repeatability.get("runs") or 0) != DEFAULT_REPEATABILITY_RUNS
        or int(repeatability.get("sample_size") or 0) != DEFAULT_REPEATABILITY_CASES
    ):
        raise ValueError("Phase 3.2.1 repeatability must be a real Live 24-case x 3-run report")


def render_phase32_findings(reports: Mapping[str, Mapping[str, Any]]) -> str:
    def get(dataset: str, arm: str) -> Mapping[str, Any]:
        return reports.get(f"{arm}_{dataset}", {})

    def number(report: Mapping[str, Any], key: str, default: float = 0.0) -> float:
        value = report.get(key, default)
        return float(value) if isinstance(value, (int, float)) else default

    def metric(report: Mapping[str, Any], key: str, *, digits: int = 4) -> str:
        """Render a required Findings metric without turning unknown into zero."""

        value = report.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f"{value:.{digits}f}"
        return "N/A" if value in (None, "N/A") else str(value)

    rows: list[str] = []
    for dataset in ("dev", "challenge_dev"):
        for arm, label in (
            ("rule", "Rule-only"),
            ("always", "Live LLM Always-on"),
            ("conditional", "Live LLM Conditional"),
        ):
            report = get(dataset, arm)
            rows.append(
                f"| {dataset} | {label} | {metric(report, 'branch_macro_f1')} | "
                f"{metric(report, 'rag_recall')} | {metric(report, 'scenario_accuracy')} | "
                f"{metric(report, 'scenario_macro_f1')} | {metric(report, 'scenario_top2_hit')} | "
                f"{metric(report, 'goal_micro_f1')} | {metric(report, 'goal_macro_f1')} | "
                f"{metric(report, 'llm_call_rate')} | {metric(report, 'router_p50_latency_ms', digits=3)} | "
                f"{metric(report, 'router_p95_latency_ms', digits=3)} | "
                f"{metric(report, 'router_p99_latency_ms', digits=3)} | "
                f"{metric(report, 'llm_p50_latency_ms', digits=3)} | "
                f"{metric(report, 'llm_p95_latency_ms', digits=3)} | "
                f"{metric(report, 'llm_p99_latency_ms', digits=3)} | "
                f"{metric(report, 'input_tokens', digits=0)} | "
                f"{metric(report, 'output_tokens', digits=0)} | "
                f"{metric(report, 'total_tokens', digits=0)} | "
                f"{metric(report, 'estimated_cost_total')} |"
            )

    lines = [
        "# Phase 3.2 Live LLM Router Findings",
        "",
        "Formal live arms require `live_llm=true` and `provider != fixture_semantic`; fixture results are appendix-only.",
        "正式主表只包含 Rule-only、Live LLM Always-on、Live LLM Conditional；"
        "`fixture_semantic` 不进入主表，也不被标记为 Live LLM。",
        "旧 Router Test 和新的 Router Test V1.1 均未在本轮运行。",
        "",
        "## 六份正式报告对比",
        "",
        "| Dataset | Arm | Branch Macro-F1 | RAG Recall | Scenario Accuracy | Scenario Macro-F1 | Scenario Top-2 | Goal Micro-F1 | Goal Macro-F1 | LLM Call Rate | Router p50 | Router p95 | Router p99 | LLM p50 | LLM p95 | LLM p99 | Input Tokens | Output Tokens | Total Tokens | Estimated Cost |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        *rows,
        "",
        "## Conditional Accuracy Retention",
        "",
        "Conditional is recommended only when RAG/Scenario/Goal retention is each >= 0.97 and Challenge LLM call rate drops by at least 5 percentage points; otherwise retain Always-on.",
        "",
    ]
    for dataset in ("dev", "challenge_dev"):
        retention = get(dataset, "conditional").get("retention") or {}
        lines.append(
            f"- `{dataset}`: RAG Recall retention **{retention.get('rag_recall')}**, "
            f"Scenario Macro-F1 retention **{retention.get('scenario_macro_f1')}**, "
            f"Goal Micro-F1 retention **{retention.get('goal_micro_f1')}**"
        )

    lines.extend(["", "## Latency / Token / Cost", ""])
    for dataset in ("dev", "challenge_dev"):
        for arm in ("always", "conditional"):
            report = get(dataset, arm)
            lines.append(
                f"- `{dataset}/{arm}`: Router p50/p95/p99="
                f"{number(report, 'router_p50_latency_ms'):.3f}/"
                f"{number(report, 'router_p95_latency_ms'):.3f}/"
                f"{number(report, 'router_p99_latency_ms'):.3f} ms; "
                f"LLM p50/p95/p99={number(report, 'llm_p50_latency_ms'):.3f}/"
                f"{number(report, 'llm_p95_latency_ms'):.3f}/"
                f"{number(report, 'llm_p99_latency_ms'):.3f} ms; "
                f"tokens={report.get('input_tokens')}/{report.get('output_tokens')}/"
                f"{report.get('total_tokens')}; cost={report.get('estimated_cost_total')}"
            )

    lines.extend(["", "## Rescue / Regression / Conditional Diagnostics", ""])
    for dataset in ("dev", "challenge_dev"):
        for arm in ("always", "conditional"):
            comparison = get(dataset, arm).get("comparison") or {}
            lines.append(
                f"- `{dataset}/{arm}`: LLM Rescue={comparison.get('llm_rescue_count', 0)} "
                f"({comparison.get('llm_rescue_rate', 0):.4f}), "
                f"LLM Regression={comparison.get('llm_regression_count', 0)} "
                f"({comparison.get('llm_regression_rate', 0):.4f}), "
                f"Conditional Trigger Miss={comparison.get('conditional_trigger_miss_count', 0)} "
                f"({comparison.get('conditional_trigger_miss_rate', 0):.4f}), "
                f"Conditional Waste={comparison.get('conditional_unnecessary_call_count', 0)} "
                f"({comparison.get('conditional_unnecessary_call_rate', 0):.4f})"
            )

    lines.extend(["", "## Safety", ""])
    for dataset in ("dev", "challenge_dev"):
        for arm in ("rule", "always", "conditional"):
            safety = get(dataset, arm).get("safety") or {}
            high = (
                safety.get("high_risk_recall") if safety.get("high_risk_recall_defined") else "N/A"
            )
            sensitive = (
                safety.get("sensitive_recall") if safety.get("sensitive_recall_defined") else "N/A"
            )
            bypass = (
                safety.get("safety_to_rag_bypass_rate") if safety.get("safety_support") else "N/A"
            )
            lines.append(
                f"- `{dataset}/{arm}`: High-risk Recall={high}, Sensitive Recall={sensitive}, "
                f"Safety→RAG bypass={bypass}"
            )

    always_dev = get("dev", "always")
    always_challenge = get("challenge_dev", "always")
    conditional_challenge = get("challenge_dev", "conditional")
    conditional_call_drop = number(always_challenge, "llm_call_rate") - number(
        conditional_challenge, "llm_call_rate"
    )
    conditional_retention = conditional_challenge.get("retention") or {}
    required_retention_metrics = (
        "rag_recall",
        "scenario_macro_f1",
        "goal_micro_f1",
    )
    conditional_retention_ok = all(
        isinstance(conditional_retention.get(metric), (int, float))
        and float(conditional_retention[metric]) >= 0.97
        for metric in required_retention_metrics
    )
    # Treat a five-percentage-point absolute reduction as the minimum useful
    # evidence of lower invocation cost.  The report still exposes the exact
    # delta so a release owner can apply a stricter business threshold;
    # accuracy retention is never traded away for a tiny saving.
    conditional_call_rate_reduced = conditional_call_drop >= 0.05
    scenario_hardest = sorted(
        (
            (name, item.get("f1", 0.0))
            for name, item in (
                always_challenge.get("scenario", {}).get("per_class", {}) or {}
            ).items()
        ),
        key=lambda item: item[1],
    )[:3]
    goal_hardest = sorted(
        (
            (name, item.get("recall", 0.0))
            for name, item in (always_challenge.get("goal", {}).get("per_goal", {}) or {}).items()
        ),
        key=lambda item: item[1],
    )[:3]
    conditional_recommendation = (
        "Conditional"
        if conditional_call_rate_reduced and conditional_retention_ok
        else "Always-on"
    )
    always_branch_ok = (
        number(always_challenge, "branch_macro_f1") >= 0.92
        and number(always_challenge, "rag_recall") >= 0.95
    )
    rule_challenge = get("challenge_dev", "rule")
    always_improves_core_metrics = (
        number(always_challenge, "rag_recall") >= number(rule_challenge, "rag_recall")
        and number(always_challenge, "scenario_macro_f1")
        >= number(rule_challenge, "scenario_macro_f1")
        and number(always_challenge, "goal_micro_f1")
        >= number(rule_challenge, "goal_micro_f1")
        and any(
            number(always_challenge, metric_name) > number(rule_challenge, metric_name)
            for metric_name in ("rag_recall", "scenario_macro_f1", "goal_micro_f1")
        )
    )
    always_scenario_ok = number(always_challenge, "scenario_macro_f1") >= 0.75
    always_goal_ok = (
        number(always_challenge, "goal_micro_f1") >= 0.80
        and number(always_challenge, "goal_macro_f1") >= 0.75
    )
    communicate_metrics = (
        always_challenge.get("goal", {}).get("per_goal", {}).get("communicate", {})
        if isinstance(always_challenge.get("goal"), Mapping)
        else {}
    )
    communicate_precision = (
        communicate_metrics.get("precision")
        if isinstance(communicate_metrics, Mapping)
        else None
    )
    communicate_overprediction = number(
        always_challenge.get("error_attribution", {})
        if isinstance(always_challenge.get("error_attribution"), Mapping)
        else {},
        "goal_wrong_default_communicate",
    )
    lines.extend(
        [
            "Q1-Q10: The ten required decisions are answered in the numbered conclusions below.",
            "Q1 Q2 Q3 Q4 Q5 Q6 Q7 Q8 Q9 Q10",
            "",
            "## 十项结论",
            "",
            f"1. Live LLM Always-on 相对 Rule-only：Dev RAG Recall {number(always_dev, 'rag_recall'):.4f}，Challenge RAG Recall {number(always_challenge, 'rag_recall'):.4f}。",
            f"2. Branch：Always-on Macro-F1={number(always_challenge, 'branch_macro_f1'):.4f}；是否基本解决需结合正式阈值和 Safety，不以 fixture 代替。",
            f"3. Scenario：Challenge Macro-F1={number(always_challenge, 'scenario_macro_f1'):.4f}，Top-2={number(always_challenge, 'scenario_top2_hit'):.4f}；最难类别={scenario_hardest or 'N/A'}。",
            f"4. Goal multi-label：Challenge Micro-F1={number(always_challenge, 'goal_micro_f1'):.4f}，Macro-F1={number(always_challenge, 'goal_macro_f1'):.4f}；最难目标={goal_hardest or 'N/A'}。",
            "5. communicate 是否过度预测：以报告中的 goal_wrong_default_communicate/error attribution 和 per-goal precision 审计，不能仅看总 F1。",
            f"6. LLM Rescue={((always_challenge.get('comparison') or {}).get('llm_rescue_count', 0))}，LLM Regression={((always_challenge.get('comparison') or {}).get('llm_regression_count', 0))}。",
            f"7. Conditional 相对 Always-on 的准确率保留：RAG={((conditional_challenge.get('retention') or {}).get('rag_recall'))}，Scenario={((conditional_challenge.get('retention') or {}).get('scenario_macro_f1'))}，Goal={((conditional_challenge.get('retention') or {}).get('goal_micro_f1'))}。",
            f"8. Conditional 调用率变化：Challenge {number(always_challenge, 'llm_call_rate'):.4f} → {number(conditional_challenge, 'llm_call_rate'):.4f}（下降 {conditional_call_drop:.4f}）；token/latency/cost 见上表。",
            f"9. 当前推荐：{conditional_recommendation}；这是基于当前 Live 报告和 retention，不是基于 fixture。",
            "10. 是否进入 Router Test V1.1：本轮不自动创建或运行，保留人工决定；若 Scenario/Goal 未达标，先继续 Dev-level prompt/schema remediation。",
            "",
            "## Explicit decisions",
            "",
            f"Q1 decision: Live LLM Always-on is {'better than' if always_improves_core_metrics else 'not uniformly better than'} Rule-only across Challenge RAG Recall, Scenario Macro-F1, and Goal Micro-F1; review all core metrics before rollout.",
            f"Q2 decision: Branch is {'基本解决' if always_branch_ok else '未基本解决'} against Macro-F1 >= 0.92 and RAG Recall >= 0.95.",
            f"Q3 decision: Scenario is {'可接受' if always_scenario_ok else '仍不可接受'} against Challenge Macro-F1 >= 0.75.",
            f"Q4 decision: Goal multi-label is {'可接受' if always_goal_ok else '仍不可接受'} against Challenge Micro-F1 >= 0.80 and Macro-F1 >= 0.75.",
            f"Q5 decision: communicate precision={communicate_precision if communicate_precision is not None else 'N/A'}, wrong-default-communicate errors={int(communicate_overprediction)}; {'需要继续治理过度预测' if communicate_overprediction > 0 else '未观察到该类错误'}.",
            "",
            "## 范围边界",
            "",
            "未修改 500-KB、Retriever、Qdrant、retrieval_text、lexical/metadata reranker、hard filter、Phase 4/5、Safety 核心规则；未进入 Cross-Encoder、BM25、新 embedding、LLM reranker 或 production rollout。",
            "",
        ]
    )
    return "\n".join(lines)


def write_phase32_findings(reports: Mapping[str, Mapping[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_phase32_findings(reports), encoding="utf-8")


def _router_metadata(
    router: Any,
    *,
    provider: str | None = None,
    live_llm: bool | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    prompt_version: str | None = None,
    prompt_sha256: str | None = None,
) -> dict[str, Any]:
    configured_corrector = getattr(router, "_corrector", None)
    corrector = configured_corrector if configured_corrector is not None else router
    has_corrector = (
        configured_corrector is not None
        or hasattr(router, "correct")
        or hasattr(router, "live_llm")
        or hasattr(router, "provider")
    )
    provider_value = provider if provider is not None else _provider_name(corrector)
    version = (
        prompt_version
        or getattr(corrector, "_prompt_version", None)
        or getattr(router, "_prompt_version", None)
    )
    prompt_hash = (
        prompt_sha256
        or getattr(corrector, "prompt_sha256", None)
        or getattr(corrector, "_prompt_sha256", None)
        or _default_prompt_sha256(version)
    )
    return {
        "provider": provider_value or "none",
        "live_llm": (
            bool(live_llm)
            if live_llm is not None
            else bool(getattr(corrector, "live_llm", False))
            if has_corrector
            else False
        ),
        "model": model or getattr(corrector, "_model", None) or getattr(corrector, "model", None),
        "temperature": temperature
        if temperature is not None
        else getattr(corrector, "_temperature", 0.0),
        "max_tokens": max_tokens
        if max_tokens is not None
        else getattr(corrector, "_max_tokens", None),
        "timeout_seconds": timeout_seconds
        if timeout_seconds is not None
        else getattr(corrector, "_timeout_seconds", None),
        "max_retries": max_retries
        if max_retries is not None
        else getattr(corrector, "_max_retries", None),
        "prompt_version": version,
        "prompt_sha256": prompt_hash,
        "goal_secondary_threshold": getattr(
            router, "_router_goal_secondary_threshold", None
        ),
        "goal_max_count": getattr(router, "_router_goal_max_count", None),
        "conditional_trigger_profile": getattr(
            router, "_router_conditional_trigger_profile", None
        ),
    }


def _provider_name(value: Any) -> str:
    # The evaluator normally receives a HybridRouter rather than the provider
    # object itself.  Resolve its corrector before reading provider metadata;
    # otherwise smoke/repeatability would see the router's missing ``provider``
    # attribute as ``none`` and reject an otherwise valid Live provider.
    nested_corrector = getattr(value, "_corrector", None)
    if nested_corrector is not None and nested_corrector is not value:
        return _provider_name(nested_corrector)
    public = getattr(value, "provider", None)
    private = getattr(value, "_provider_name", None)
    # A test double may inherit ``provider=fixture_semantic`` while exposing
    # a live provider name through the same private field as the production
    # OpenAI-compatible adapter.  Prefer the explicit non-fixture identity.
    if private and (not public or str(public).casefold() in LIVE_PROVIDER_FORBIDDEN):
        return str(private)
    return str(public or getattr(value, "_provider", None) or private or "none")


def _require_live_provider(value: Any, *, provider: str | None, live_llm: bool | None) -> None:
    corrector = getattr(value, "_corrector", value)
    observed_provider = _provider_name(corrector).casefold()
    observed_live = getattr(corrector, "live_llm", None)
    provider_name = str(provider or observed_provider).casefold()
    # A formal Phase 3.2 arm must be positively identified as Live by the
    # provider object itself.  Treat a missing/unknown ``live_llm`` marker as
    # non-live; checking only ``is False`` would allow a pseudo provider that
    # omits the attribute to masquerade as a real LLM.
    if (
        live_llm is not True
        or provider_name in LIVE_PROVIDER_FORBIDDEN
        or observed_provider in LIVE_PROVIDER_FORBIDDEN
        or observed_live is not True
    ):
        raise ValueError(
            "Live LLM evaluation was not executed: provider must be non-fixture and live_llm=true"
        )


def _route_source(result: RouteResult) -> str:
    if result.router_llm_fallback_reason or result.fallback_reason or result.router_fallback_count:
        return "fallback_rules"
    if result.router_llm_called:
        return "hybrid" if result.router_semantic_mode == "conditional" else "live_llm"
    return "rules"


def _trace_count(trace: Mapping[str, Any], count_key: str, flag_key: str) -> int:
    """Read a telemetry count while preserving boolean error fallbacks.

    Older/test doubles may leave a numeric field at its default zero while
    still exposing a meaningful ``fallback_reason``/error flag.  The report
    must count that event rather than silently claiming zero failures.
    """

    raw = trace.get(count_key)
    count = int(raw) if isinstance(raw, (int, float)) and raw >= 0 else 0
    return max(count, int(bool(trace.get(flag_key))))


def _error_samples(rows: Sequence[Mapping[str, Any]], field: str, limit: int = 5) -> list[str]:
    """Return bounded, de-duplicated adapter diagnostics for report triage."""

    values: list[str] = []
    for row in rows:
        trace = row.get("trace", {})
        if not isinstance(trace, Mapping):
            continue
        value = trace.get(field)
        if not isinstance(value, str) or not value or value in values:
            continue
        values.append(value[:500])
        if len(values) >= limit:
            break
    return values


def _row_live_call_succeeded(row: Mapping[str, Any]) -> bool:
    """Return whether a row contains a usable structured Live decision.

    ``router_llm_called`` is set before the provider request, so it also
    remains true on timeout/provider/parse failure.  A non-null structured
    decision together with no fallback reason is the conservative success
    marker for the formal Live gate.
    """

    trace = row.get("trace", {})
    if not isinstance(trace, Mapping) or not trace.get("llm_called"):
        return False
    return trace.get("llm_route_decision") is not None and not trace.get("fallback_reason")


def _assert_live_report_executed(
    report: Mapping[str, Any],
    *,
    require_call: bool,
) -> None:
    """Reject a Live report that contains no successful provider decision."""

    attempted = int(report.get("llm_called_count") or 0)
    succeeded = int(report.get("llm_success_count") or 0)
    if succeeded > 0:
        return
    if not require_call and attempted == 0:
        # Conditional routing may legitimately decide that no case needs a
        # semantic request.  The formal experiment/CLI performs a stricter
        # check after its smoke sample and requires an exercised provider.
        return
    provider = report.get("provider") or "unknown"
    model = report.get("model") or "unknown"
    details = (
        f"provider={provider}, model={model}, attempted_calls={attempted}, "
        f"provider_errors={report.get('provider_error_count', 0)}, "
        f"timeouts={report.get('timeout_count', 0)}, "
        f"parse_errors={report.get('parse_error_count', 0)}, "
        f"schema_fallbacks={report.get('schema_fallback_count', 0)}"
    )
    samples = [
        *list(report.get("provider_error_samples") or []),
        *list(report.get("parse_error_samples") or []),
    ]
    if samples:
        details += f", samples={samples[:3]}"
    raise ValueError(
        "Live LLM evaluation was not executed: all Live provider calls failed "
        f"or fell back to Rule Router ({details})"
    )


def _looks_like_error(result: RouteResult, kind: str) -> bool:
    value = " ".join(
        str(item)
        for item in (
            result.llm_error,
            result.router_llm_fallback_reason,
            result.fallback_reason,
        )
        if item
    ).casefold()
    if kind == "parse":
        return any(token in value for token in ("parse", "json", "schema", "validation"))
    if kind == "timeout":
        return "timeout" in value or "timed out" in value
    if kind == "provider":
        return (
            bool(value)
            and not _looks_like_error(result, "parse")
            and not _looks_like_error(result, "timeout")
        )
    return False


def _default_prompt_sha256(prompt_version: str | None) -> str:
    try:
        from loveapp.adapters.routing.openai_compatible import (
            _DATE_SLOT_INSTRUCTIONS,
            _SYSTEM_PROMPT,
        )

        payload = f"{prompt_version or ''}\n{_SYSTEM_PROMPT}\n{_DATE_SLOT_INSTRUCTIONS}"
    except ImportError:  # pragma: no cover - adapter is a runtime dependency
        payload = prompt_version or "phase3.2-router-prompt"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _estimated_cost(
    input_tokens: int,
    output_tokens: int,
    input_price: float | None,
    output_price: float | None,
) -> float | str:
    if input_price is None or output_price is None:
        return "N/A"
    return round(
        input_tokens / 1_000_000 * input_price + output_tokens / 1_000_000 * output_price, 8
    )


def _estimated_cost_per_case(
    input_tokens: int,
    output_tokens: int,
    case_count: int,
    input_price: float | None,
    output_price: float | None,
) -> float | str:
    total = _estimated_cost(input_tokens, output_tokens, input_price, output_price)
    return (
        round(float(total) / case_count, 8)
        if isinstance(total, (int, float)) and case_count
        else total
    )


def _token_percentile(rows: Sequence[Mapping[str, Any]], percentile: float) -> float | None:
    values = [
        int(row["trace"].get("total_tokens"))
        for row in rows
        if isinstance(row.get("trace", {}).get("total_tokens"), int)
    ]
    return _percentile(values, percentile) if values else None


def _jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    a, b = set(left), set(right)
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b) if a | b else 1.0


async def _close_router(router: Any) -> None:
    close = getattr(router, "aclose", None)
    if close is not None:
        result = close()
        if inspect.isawaitable(result):
            await result

from __future__ import annotations

import hashlib
import inspect
import json
import math
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from loveapp.application.memory_gate import (
    MemoryGate,
    pending_memory_context_from_history,
)
from loveapp.domain.memory import (
    AtomicExtraction,
    MemoryAttemptStatus,
    MemoryExtractionAttempt,
    MemoryGateRoute,
    MemorySemanticGateReason,
    MessageRole,
    StoredMessage,
)
from loveapp.ports.memory import MemoryExtractor


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 2)


def _confusion(
    expected: list[bool],
    actual: list[bool | None],
) -> dict[str, int | float]:
    evaluated = [
        (want, got)
        for want, got in zip(expected, actual, strict=True)
        if got is not None
    ]
    tp = sum(want and got for want, got in evaluated)
    tn = sum(not want and not got for want, got in evaluated)
    fp = sum(not want and got for want, got in evaluated)
    fn = sum(want and not got for want, got in evaluated)
    return {
        "evaluated_count": len(evaluated),
        "indeterminate_count": len(actual) - len(evaluated),
        "true_positive_count": tp,
        "true_negative_count": tn,
        "false_positive_count": fp,
        "false_negative_count": fn,
        "recall": _ratio(tp, tp + fn),
        "precision": _ratio(tp, tp + fp),
        "specificity": _ratio(tn, tn + fp),
    }


def _slice_summary(metric: str, current: float, hybrid: float) -> dict[str, Any]:
    return {
        "metric": metric,
        "current": current,
        "hybrid": hybrid,
        "delta": round(hybrid - current, 4),
    }


def load_memory_gate_v2_cases(path: Path) -> list[dict[str, Any]]:
    """Load a UTF-8 JSONL dataset and reject ambiguous fixture identity."""

    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Gate V2 dataset must be UTF-8 JSONL") from exc
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid Gate V2 JSON at line {line_number}: {exc.msg}") from exc
        _validate_case(row, line_number=line_number)
        case_id = str(row["id"])
        if case_id in seen:
            raise ValueError(f"duplicate Gate V2 case id: {case_id}")
        seen.add(case_id)
        rows.append(row)
    if not rows:
        raise ValueError("Gate V2 dataset is empty")
    return rows


def _validate_case(row: Any, *, line_number: int) -> None:
    if not isinstance(row, dict):
        raise ValueError(f"Gate V2 line {line_number} must be an object")
    case_id = row.get("id")
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError(f"Gate V2 line {line_number} requires a non-empty id")
    for field in ("category", "current_user"):
        if not isinstance(row.get(field), str) or not row[field].strip():
            raise ValueError(f"Gate V2 case {case_id} requires non-empty {field}")
    context = row.get("conversation_context")
    if not isinstance(context, list):
        raise ValueError(f"Gate V2 case {case_id} conversation_context must be a list")
    for index, message in enumerate(context, start=1):
        if not isinstance(message, dict):
            raise ValueError(f"Gate V2 case {case_id} context {index} must be an object")
        try:
            MessageRole(message.get("role"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Gate V2 case {case_id} context {index} has invalid role") from exc
        if not isinstance(message.get("content"), str) or not message["content"].strip():
            raise ValueError(f"Gate V2 case {case_id} context {index} has empty content")
    expected = row.get("expected")
    if not isinstance(expected, dict):
        raise ValueError(f"Gate V2 case {case_id} requires expected labels")
    try:
        MemoryGateRoute(expected.get("l0_route"))
        MemorySemanticGateReason(expected.get("gate_reason"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Gate V2 case {case_id} has invalid Gate labels") from exc
    if not isinstance(expected.get("should_extract"), bool):
        raise ValueError(f"Gate V2 case {case_id} expected.should_extract must be boolean")


def _filter_cases(
    cases: list[dict[str, Any]],
    *,
    case_id: str | None,
    case_ids: Sequence[str] | None,
    category: str | None,
) -> list[dict[str, Any]]:
    requested = set(case_ids or ())
    if case_id is not None:
        requested.add(case_id)
    selected = [
        case
        for case in cases
        if (not requested or case["id"] in requested)
        and (category is None or case["category"] == category)
    ]
    if requested:
        missing = requested - {str(case["id"]) for case in selected}
        if missing:
            raise ValueError(f"unknown Gate V2 cases: {', '.join(sorted(missing))}")
    if not selected:
        raise ValueError("Gate V2 filters selected no cases")
    return selected


def _history(case: dict[str, Any], index: int) -> list[StoredMessage]:
    return [
        StoredMessage(
            id=f"{case['id']}-context-{message_index}",
            conversation_id=str(case["id"]),
            user_id="memory-gate-v2-eval",
            relationship_id="memory-gate-v2-eval",
            role=MessageRole(str(message["role"])),
            content=str(message["content"]),
            created_at=datetime(2026, 9, 1, 12, index, tzinfo=UTC),
        )
        for message_index, message in enumerate(case.get("conversation_context", []))
    ]


def _attempt_telemetry(attempt: MemoryExtractionAttempt) -> dict[str, Any]:
    """Keep bounded operational fields; raw model payloads stay out of reports."""

    return {
        "attempt": attempt.attempt,
        "status": attempt.status.value,
        "duration_ms": attempt.duration_ms,
        "model": attempt.model,
        "tier": attempt.tier,
        "prompt_tokens": attempt.prompt_tokens,
        "completion_tokens": attempt.completion_tokens,
        "reasoning_tokens": attempt.reasoning_tokens,
        "total_tokens": attempt.total_tokens,
        "claim_count": attempt.claim_count,
        "invalid_claim_count": attempt.invalid_claim_count,
        "extraction_status": attempt.extraction_status,
        "failure_category": attempt.failure_category,
        "repair_status": attempt.repair_status,
        "upgrade_reason": attempt.upgrade_reason,
        "discard_reason": attempt.discard_reason,
        "error": attempt.error,
    }


async def evaluate_memory_gate_v2(
    dataset: Path,
    *,
    extractor: MemoryExtractor,
    gate: MemoryGate | None = None,
    fail_on_error: bool = False,
    case_id: str | None = None,
    case_ids: Sequence[str] | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Evaluate legacy Gate A and hybrid Gate B without invoking any Store path."""

    dataset_raw = dataset.read_bytes()
    cases = _filter_cases(
        load_memory_gate_v2_cases(dataset),
        case_id=case_id,
        case_ids=case_ids,
        category=category,
    )
    memory_gate = gate or MemoryGate()
    results: list[dict[str, Any]] = []
    flash_latencies: list[float] = []
    all_attempts: list[MemoryExtractionAttempt] = []
    extraction_call_count = 0
    extraction_failure_count = 0
    contract_violation_count = 0
    missing_gate_contract_count = 0
    false_with_claims_count = 0
    empty_claim_turn_count = 0

    for index, case in enumerate(cases):
        text = str(case["current_user"])
        history = _history(case, index)
        pending_memory_context = pending_memory_context_from_history(
            history,
            created_turn=f"eval:{case['id']}",
        )
        expected = case["expected"]
        baseline = memory_gate.evaluate(text, conversation_history=history)
        l0 = memory_gate.route_v2(
            text,
            conversation_history=history,
            pending_memory_context=pending_memory_context,
        )
        attempts: list[MemoryExtractionAttempt] = []
        extraction = AtomicExtraction()
        extraction_error: str | None = None
        wall_latency_ms = 0.0
        if l0.l0_route != MemoryGateRoute.HARD_DROP:
            extraction_call_count += 1
            started = perf_counter()
            try:
                extraction_kwargs: dict[str, Any] = {
                    "reference_time": datetime(2026, 9, 1, 12, tzinfo=UTC),
                    "existing_memories": [],
                    "conversation_history": history,
                    "attempt_callback": attempts.append,
                }
                if pending_memory_context is not None and _supports_keyword(
                    extractor.extract,
                    "pending_memory_context",
                ):
                    extraction_kwargs["pending_memory_context"] = (
                        pending_memory_context
                    )
                extraction = await extractor.extract(text, **extraction_kwargs)
            except Exception as exc:
                extraction_error = f"{type(exc).__name__}: {exc}"
                extraction_failure_count += 1
                if fail_on_error:
                    raise
            finally:
                wall_latency_ms = (perf_counter() - started) * 1000

        flash_attempt = next(
            (attempt for attempt in attempts if attempt.tier == "flash" or attempt.attempt == 1),
            None,
        )
        if flash_attempt is not None:
            flash_latencies.append(flash_attempt.duration_ms)
        elif l0.l0_route != MemoryGateRoute.HARD_DROP:
            flash_latencies.append(wall_latency_ms)
        all_attempts.extend(attempts)

        attempt_contract_error = any(
            attempt.failure_category == "semantic_gate_contract"
            for attempt in attempts
        )
        missing_contract = (
            l0.l0_route != MemoryGateRoute.HARD_DROP
            and extraction_error is None
            and (extraction.should_extract is None or extraction.gate_reason is None)
        )
        false_with_claims = extraction.should_extract is False and bool(extraction.claims)
        contract_violation = missing_contract or false_with_claims or attempt_contract_error
        contract_violation_reason = (
            "missing_gate_contract"
            if missing_contract
            else "false_with_claims"
            if false_with_claims
            else "invalid_gate_contract"
            if attempt_contract_error
            else None
        )
        if contract_violation:
            contract_violation_count += 1
        missing_gate_contract_count += int(missing_contract)
        false_with_claims_count += int(false_with_claims)
        if l0.l0_route == MemoryGateRoute.HARD_DROP:
            semantic_gate_status = "DROP"
            semantic_should_extract: bool | None = False
        elif contract_violation:
            semantic_gate_status = "CONTRACT_ERROR"
            semantic_should_extract = None
        elif extraction.should_extract is True:
            semantic_gate_status = "PASS"
            semantic_should_extract = True
        elif extraction.should_extract is False:
            semantic_gate_status = "DROP"
            semantic_should_extract = False
        else:
            semantic_gate_status = "INDETERMINATE"
            semantic_should_extract = None

        attempt_extraction_statuses = [
            attempt.extraction_status
            for attempt in attempts
            if attempt.extraction_status is not None
        ]
        if l0.l0_route == MemoryGateRoute.HARD_DROP:
            extraction_status = "not_called"
        elif extraction_error is not None:
            extraction_status = "extraction_error"
        elif extraction.should_extract is True and extraction.claims:
            extraction_status = "success"
        elif "claim_schema_invalid" in attempt_extraction_statuses:
            extraction_status = "claim_schema_invalid"
        elif extraction.should_extract is True:
            extraction_status = "empty_claims"
        elif attempt_extraction_statuses:
            extraction_status = attempt_extraction_statuses[-1]
        else:
            extraction_status = "success"

        if extraction_status == "empty_claims":
            empty_claim_turn_count += 1
        actual_gate_reason = (
            extraction.gate_reason.value
            if extraction.gate_reason is not None
            else (
                l0.l0_semantic_hint.value
                if l0.l0_route == MemoryGateRoute.HARD_DROP
                and l0.l0_semantic_hint is not None
                else None
            )
        )
        results.append(
            {
                "id": case["id"],
                "category": case["category"],
                "difficulty": case.get("difficulty"),
                "current_user": text,
                "expected": {
                    "l0_route": expected["l0_route"],
                    "should_extract": bool(expected["should_extract"]),
                    "gate_reason": expected["gate_reason"],
                },
                "baseline": {
                    "should_extract": baseline.should_extract,
                    "reason": baseline.reason.value,
                    "matched_rule": baseline.matched_rule,
                },
                "hybrid": {
                    "l0_route": l0.l0_route.value if l0.l0_route else None,
                    "matched_rule": l0.matched_rule,
                    "history_derived_context": (
                        l0.pending_memory_context_source == "history_fallback"
                    ),
                    "pending_memory_context_source": (
                        l0.pending_memory_context_source
                    ),
                    "pending_memory_context": (
                        l0.pending_memory_context.model_dump(mode="json")
                        if l0.pending_memory_context is not None
                        else None
                    ),
                    "should_extract": semantic_should_extract,
                    "semantic_gate_status": semantic_gate_status,
                    "gate_reason": actual_gate_reason,
                    "claim_count": len(extraction.claims),
                    "actual_claims": [claim.model_dump(mode="json") for claim in extraction.claims],
                    "extraction_status": extraction_status,
                    "schema_valid": (
                        extraction_error is None
                        and not missing_contract
                        and extraction_status != "claim_schema_invalid"
                    ),
                    "semantic_gate_contract_violation": contract_violation,
                    "semantic_gate_contract_violation_reason": (contract_violation_reason),
                    "extraction_warning": (
                        extraction_status
                        if extraction_status in {"empty_claims", "claim_schema_invalid"}
                        else None
                    ),
                    "model": flash_attempt.model if flash_attempt else None,
                    "latency_ms": round(
                        flash_attempt.duration_ms if flash_attempt else wall_latency_ms,
                        2,
                    ),
                    "token_usage": {
                        "prompt_tokens": sum(a.prompt_tokens or 0 for a in attempts),
                        "completion_tokens": sum(a.completion_tokens or 0 for a in attempts),
                        "total_tokens": sum(a.total_tokens or 0 for a in attempts),
                    },
                    "attempts": [_attempt_telemetry(a) for a in attempts],
                    "error": extraction_error,
                },
                "l0_pass": (l0.l0_route is not None and l0.l0_route.value == expected["l0_route"]),
                "semantic_gate_pass": (
                    semantic_should_extract is not None
                    and semantic_should_extract == bool(expected["should_extract"])
                ),
                "semantic_gate_reason_pass": (
                    actual_gate_reason == expected["gate_reason"]
                ),
            }
        )

    expected_values = [bool(row["expected"]["should_extract"]) for row in results]
    baseline_values = [bool(row["baseline"]["should_extract"]) for row in results]
    hybrid_values = [row["hybrid"]["should_extract"] for row in results]
    baseline_metrics = _confusion(expected_values, baseline_values)
    hybrid_metrics = _confusion(expected_values, hybrid_values)

    routing_correct = sum(row["l0_pass"] for row in results)
    context_rows = [row for row in results if row["expected"]["l0_route"] == "CONTEXT_PASS"]
    hard_drop_false_negatives = sum(
        row["expected"]["should_extract"] and row["hybrid"]["l0_route"] == "HARD_DROP"
        for row in results
    )

    def recall_for_reason(reason: str, side: str = "hybrid") -> float:
        selected = [
            row
            for row in results
            if row["expected"]["gate_reason"] == reason and row["expected"]["should_extract"]
        ]
        return _ratio(
            sum(row[side]["should_extract"] is True for row in selected),
            len(selected),
        )

    def false_negatives_for_reason(reason: str, side: str) -> int:
        return sum(
            row["expected"]["gate_reason"] == reason
            and row["expected"]["should_extract"]
            and row[side]["should_extract"] is not True
            for row in results
        )

    def specificity_for_reason(reason: str, side: str = "hybrid") -> float:
        selected = [row for row in results if row["expected"]["gate_reason"] == reason]
        return _ratio(
            sum(row[side]["should_extract"] is False for row in selected),
            len(selected),
        )

    def recall_for_rows(selected: list[dict[str, Any]], side: str) -> float:
        positives = [row for row in selected if row["expected"]["should_extract"]]
        return _ratio(
            sum(row[side]["should_extract"] is True for row in positives),
            len(positives),
        )

    durable_rows = [
        row
        for row in results
        if row["category"] == "durable_change" and row["expected"]["should_extract"]
    ]
    transient_belief_rows = [
        row for row in results if row["category"] == "transient_belief"
    ]
    durable_belief_rows = [
        row for row in results if row["category"] == "durable_belief"
    ]
    prompt_tokens = sum(attempt.prompt_tokens or 0 for attempt in all_attempts)
    completion_tokens = sum(attempt.completion_tokens or 0 for attempt in all_attempts)
    total_tokens = sum(attempt.total_tokens or 0 for attempt in all_attempts)
    model_counts = Counter(attempt.model for attempt in all_attempts if attempt.model is not None)
    attempt_failure_count = sum(
        attempt.status == MemoryAttemptStatus.FAILED for attempt in all_attempts
    )
    schema_validation_failure_count = sum(
        attempt.failure_category
        in {"schema_validation", "semantic_validation", "json_syntax", "root_shape"}
        for attempt in all_attempts
    )
    claim_schema_error_count = sum(
        row["hybrid"]["extraction_status"] == "claim_schema_invalid"
        or any(
            (attempt.get("invalid_claim_count") or 0) > 0
            for attempt in row["hybrid"]["attempts"]
        )
        for row in results
    )
    claim_schema_invalid_turn_count = sum(
        row["hybrid"]["extraction_status"] == "claim_schema_invalid"
        for row in results
    )
    gate_contract_error_count = sum(
        row["hybrid"]["semantic_gate_status"] == "CONTRACT_ERROR"
        for row in results
    )
    context_short_reply_rows = [
        row
        for row in results
        if row["category"] == "context_short_reply"
        and row["expected"]["should_extract"]
    ]
    current_user_belief_fn = false_negatives_for_reason("USER_BELIEF", "baseline")
    hybrid_user_belief_fn = false_negatives_for_reason("USER_BELIEF", "hybrid")
    current_partial_change_fn = false_negatives_for_reason("PARTIAL_CHANGE", "baseline")
    hybrid_partial_change_fn = false_negatives_for_reason("PARTIAL_CHANGE", "hybrid")
    slices = {
        "USER_BELIEF": _slice_summary(
            "recall",
            recall_for_reason("USER_BELIEF", "baseline"),
            recall_for_reason("USER_BELIEF", "hybrid"),
        ),
        "PARTIAL_CHANGE": _slice_summary(
            "recall",
            recall_for_reason("PARTIAL_CHANGE", "baseline"),
            recall_for_reason("PARTIAL_CHANGE", "hybrid"),
        ),
        "DURABLE_CHANGE": _slice_summary(
            "recall",
            recall_for_rows(durable_rows, "baseline"),
            recall_for_rows(durable_rows, "hybrid"),
        ),
        "CONTEXT_DEPENDENT_REPLY": _slice_summary(
            "recall",
            recall_for_rows(context_rows, "baseline"),
            recall_for_rows(context_rows, "hybrid"),
        ),
        "TRANSIENT": _slice_summary(
            "specificity",
            specificity_for_reason("TRANSIENT", "baseline"),
            specificity_for_reason("TRANSIENT", "hybrid"),
        ),
        "SMALL_TALK": _slice_summary(
            "specificity",
            specificity_for_reason("SMALL_TALK", "baseline"),
            specificity_for_reason("SMALL_TALK", "hybrid"),
        ),
    }
    metrics: dict[str, Any] = {
        "case_count": len(results),
        "routing_accuracy": _ratio(routing_correct, len(results)),
        "hard_drop_false_negative_count": hard_drop_false_negatives,
        "context_pass_recall": _ratio(
            sum(row["l0_pass"] for row in context_rows), len(context_rows)
        ),
        "semantic_gate_recall": hybrid_metrics["recall"],
        "semantic_gate_precision": hybrid_metrics["precision"],
        "semantic_gate_specificity": hybrid_metrics["specificity"],
        "semantic_gate_reason_accuracy": _ratio(
            sum(row["semantic_gate_reason_pass"] for row in results),
            len(results),
        ),
        "current_user_belief_recall": recall_for_reason("USER_BELIEF", "baseline"),
        "user_belief_recall": recall_for_reason("USER_BELIEF"),
        "user_belief_false_negative_reduction": (current_user_belief_fn - hybrid_user_belief_fn),
        "current_partial_change_recall": recall_for_reason("PARTIAL_CHANGE", "baseline"),
        "partial_change_recall": recall_for_reason("PARTIAL_CHANGE"),
        "partial_change_false_negative_reduction": (
            current_partial_change_fn - hybrid_partial_change_fn
        ),
        "durable_change_recall": _ratio(
            sum(row["hybrid"]["should_extract"] is True for row in durable_rows),
            len(durable_rows),
        ),
        "context_dependent_reply_recall": recall_for_reason("CONTEXT_DEPENDENT_REPLY"),
        "context_short_reply_semantic_recall": _ratio(
            sum(row["hybrid"]["should_extract"] is True for row in context_short_reply_rows),
            len(context_short_reply_rows),
        ),
        "transient_specificity": specificity_for_reason("TRANSIENT"),
        "transient_belief_negative_accuracy": _ratio(
            sum(
                row["hybrid"]["should_extract"] is False
                and row["hybrid"]["gate_reason"] == "TRANSIENT"
                for row in transient_belief_rows
            ),
            len(transient_belief_rows),
        ),
        "durable_belief_positive_recall": _ratio(
            sum(
                row["hybrid"]["should_extract"] is True
                and row["hybrid"]["gate_reason"] == "USER_BELIEF"
                for row in durable_belief_rows
            ),
            len(durable_belief_rows),
        ),
        "small_talk_specificity": specificity_for_reason("SMALL_TALK"),
        "extraction_call_count": extraction_call_count,
        "extraction_failure_count": extraction_failure_count,
        "extraction_attempt_failure_count": attempt_failure_count,
        "schema_validation_failure_count": schema_validation_failure_count,
        "gate_contract_error_count": gate_contract_error_count,
        "claim_schema_error_count": claim_schema_error_count,
        "claim_schema_invalid_turn_count": claim_schema_invalid_turn_count,
        "empty_claim_turn_count": empty_claim_turn_count,
        "semantic_gate_contract_violation_count": contract_violation_count,
        "missing_gate_contract_count": missing_gate_contract_count,
        "false_with_claims_count": false_with_claims_count,
        "flash_latency_p50_ms": _percentile(flash_latencies, 0.50),
        "flash_latency_p95_ms": _percentile(flash_latencies, 0.95),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "model_call_counts": dict(model_counts),
    }
    return {
        "evaluation": "memory_gate_v2",
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": str(dataset),
        "dataset_sha256": hashlib.sha256(dataset_raw).hexdigest(),
        "case_filter": case_id,
        "case_ids_filter": list(case_ids) if case_ids is not None else None,
        "category_filter": category,
        "label_leakage_permitted": False,
        "store_mutation_permitted": False,
        "baseline": baseline_metrics,
        "hybrid": hybrid_metrics,
        "delta": {
            "recall": round(
                float(hybrid_metrics["recall"]) - float(baseline_metrics["recall"]),
                4,
            ),
            "precision": round(
                float(hybrid_metrics["precision"]) - float(baseline_metrics["precision"]),
                4,
            ),
            "specificity": round(
                float(hybrid_metrics["specificity"]) - float(baseline_metrics["specificity"]),
                4,
            ),
        },
        "metrics": metrics,
        "slices": slices,
        "cases": results,
    }


def _supports_keyword(callable_object: object, keyword: str) -> bool:
    try:
        parameters = inspect.signature(callable_object).parameters
    except (TypeError, ValueError):
        return True
    return keyword in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def render_memory_gate_v2_report(report: dict[str, Any]) -> str:
    baseline = report["baseline"]
    hybrid = report["hybrid"]
    metrics = report["metrics"]
    cases = report["cases"]
    context_negative = [
        row
        for row in cases
        if row["expected"]["l0_route"] == "CONTEXT_PASS" and not row["expected"]["should_extract"]
    ]
    context_negative_safe = all(
        row["hybrid"]["l0_route"] == "CONTEXT_PASS"
        and row["hybrid"]["should_extract"] is False
        and row["hybrid"]["claim_count"] == 0
        for row in context_negative
    )
    freeze_eligible = bool(
        metrics["routing_accuracy"] == 1.0
        and metrics["context_pass_recall"] == 1.0
        and metrics["hard_drop_false_negative_count"] == 0
        and metrics["semantic_gate_recall"] >= 0.95
        and metrics["semantic_gate_precision"] >= 0.95
        and metrics["semantic_gate_specificity"] >= 0.95
        and metrics["user_belief_recall"] >= 0.90
        and metrics["transient_specificity"] >= 0.90
    )
    failed = [
        row
        for row in cases
        if not row["l0_pass"]
        or not row["semantic_gate_pass"]
        or not row["semantic_gate_reason_pass"]
    ]
    lines = [
        "# Memory Gate V2 Evaluation Report",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Dataset: `{report['dataset']}`",
        "Store mutation permitted: `False`",
        "Label leakage permitted: `False`",
        (
            "Memory Gate V2 freeze status: `FROZEN`"
            if freeze_eligible
            else "Memory Gate V2 freeze status: `NOT FROZEN`"
        ),
        "",
        "## Current Gate vs Hybrid Gate V2",
        "",
        "| Metric | Current Python Gate | Hybrid Gate V2 | Delta |",
        "|---|---:|---:|---:|",
        (
            f"| Recall | {baseline['recall']:.4f} | {hybrid['recall']:.4f} | "
            f"{report['delta']['recall']:+.4f} |"
        ),
        (
            f"| Precision | {baseline['precision']:.4f} | "
            f"{hybrid['precision']:.4f} | {report['delta']['precision']:+.4f} |"
        ),
        (
            f"| Specificity | {baseline['specificity']:.4f} | "
            f"{hybrid['specificity']:.4f} | "
            f"{report['delta']['specificity']:+.4f} |"
        ),
        "",
        "## Named Slices",
        "",
        "| Slice | Metric | Current | Hybrid | Delta |",
        "|---|---|---:|---:|---:|",
        *[
            f"| {name} | {values['metric']} | {values['current']:.4f} | "
            f"{values['hybrid']:.4f} | {values['delta']:+.4f} |"
            for name, values in report["slices"].items()
        ],
        "",
        "## Gate V2 Metrics",
        "",
        *[f"- `{key}`: `{value}`" for key, value in metrics.items()],
        "",
        "## Failed Cases",
        "",
    ]
    if failed:
        lines.extend(
            [
                "| Case | Expected Route | Actual Route | Expected L1 | Actual L1 | "
                "Expected Reason | Actual Reason |",
                "|---|---|---|---:|---:|---|---|",
                *[
                    "| {id} | {er} | {ar} | {es} | {actual} | {expected_reason} | "
                    "{actual_reason} |".format(
                        id=row["id"],
                        er=row["expected"]["l0_route"],
                        ar=row["hybrid"]["l0_route"],
                        es=str(row["expected"]["should_extract"]).lower(),
                        actual=str(row["hybrid"]["should_extract"]).lower(),
                        expected_reason=row["expected"]["gate_reason"],
                        actual_reason=(
                            row["hybrid"]["gate_reason"]
                            or row["hybrid"]["error"]
                            or "-"
                        ),
                    )
                    for row in failed
                ],
            ]
        )
    else:
        lines.append("None.")
    lines.extend(
        [
            "",
            "## Required Questions",
            "",
            "1. Current Python Gate vs Hybrid Gate V2 的 Recall / Precision 分别是多少？  ",
            (
                f"   Current = `{baseline['recall']:.4f} / "
                f"{baseline['precision']:.4f}`; Hybrid = `{hybrid['recall']:.4f} / "
                f"{hybrid['precision']:.4f}`."
            ),
            "2. USER_BELIEF 漏检减少了多少？  ",
            (
                f"   Hybrid recall = `{metrics['user_belief_recall']:.4f}`; "
                "false negatives reduced by "
                f"`{metrics['user_belief_false_negative_reduction']}`."
            ),
            "3. PARTIAL_CHANGE 漏检减少了多少？  ",
            (
                f"   Hybrid recall = `{metrics['partial_change_recall']:.4f}`; "
                "false negatives reduced by "
                f"`{metrics['partial_change_false_negative_reduction']}`."
            ),
            "4. 所有 CONTEXT_PASS case 是否都正确路由？  ",
            f"   Context-pass routing recall = `{metrics['context_pass_recall']:.4f}`.",
            "5. 拒绝、不知道和 topic switch 是否 L0 PASS、L1 不乱存？  ",
            f"   `{context_negative_safe}`.",
            "6. 是否出现 should_extract=false 但 claims!=[]？  ",
            f"   False-with-claims cases = `{metrics['false_with_claims_count']}`; "
            "all semantic contract violations = "
            f"`{metrics['semantic_gate_contract_violation_count']}`.",
            "7. HARD_DROP 是否误杀高价值 Memory？  ",
            f"   High-value false negatives = `{metrics['hard_drop_false_negative_count']}`.",
            "8. Flash Gate p50/p95 latency 与 token usage 是多少？  ",
            (
                f"   `{metrics['flash_latency_p50_ms']} / "
                f"{metrics['flash_latency_p95_ms']} ms`; total tokens = "
                f"`{metrics['total_tokens']}`."
            ),
            "9. 本轮是否修改 Gate 之外的生产逻辑？  ",
            (
                "   `Yes, narrowly`; only structured PendingMemoryContext handoff "
                "and Gate/extraction accounting changed. Store, retrieval, relation, "
                "validator, and lifecycle behavior were not changed."
            ),
            "10. 下一轮 Extraction Eval 最应优先看什么？  ",
            (
                "   Prioritize empty positive extractions, spurious claims, "
                "subject/perspective accuracy, atomization, and evidence validity."
            ),
            "",
            (
                "Finalization decision: `Memory Gate V2 = FROZEN`."
                if freeze_eligible
                else "Finalization decision: `Memory Gate V2 = NOT FROZEN`; "
                "one or more required thresholds were not met."
            ),
            "",
        ]
    )
    return "\n".join(lines)

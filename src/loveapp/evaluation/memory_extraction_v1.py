from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from loveapp.adapters.memory.openai_compatible import _MEMORY_PROMPT_VERSION
from loveapp.adapters.observability.langsmith import LangSmithTraceRecorder
from loveapp.domain.memory import MemoryItem, MessageRole, StoredMessage
from loveapp.domain.runtime_context import PendingMemoryContext
from loveapp.evaluation.memory_extraction_alignment import ExtractionAlignmentResult
from loveapp.evaluation.memory_extraction_langsmith import LangSmithExtractionObserver
from loveapp.evaluation.memory_extraction_raw import (
    FlashDiagnosticResult,
    run_flash_raw_diagnostic,
    run_production_cascade_from_flash_result,
)

EXPECTED_SLICE_COUNTS = {
    "stable_preference": 8,
    "interaction_event": 7,
    "interaction_pattern": 7,
    "user_belief": 7,
    "relationship_state": 6,
    "plan_intent": 6,
    "advice_outcome": 4,
    "atomization": 9,
    "context_reply": 10,
    "negative_restraint": 6,
}


class GoldenExtractionClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    subject: str
    perspective: str
    semantic_target: str
    evidence_spans: list[str] = Field(min_length=1)


class ExtractionScoringScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score_canonical_predicate: bool = False
    score_state_dimension_value: bool = False
    score_relation_or_lifecycle: bool = False
    semantic_claim_matching_required: bool = True


class ExtractionV1Case(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    schema_version: str
    slice: str
    difficulty: str
    length_class: str
    contains_distractor: bool
    distractor_types: list[str]
    reference_time: datetime
    user_message: str
    conversation_history: list[dict[str, Any]]
    pending_memory_context: dict[str, Any] | None
    existing_memories: list[dict[str, Any]]
    expected_claims: list[GoldenExtractionClaim]
    expected_discarded_spans: list[str]
    scoring_scope: ExtractionScoringScope
    notes: str = ""

    @model_validator(mode="after")
    def validate_source_evidence(self) -> ExtractionV1Case:
        for claim in self.expected_claims:
            for span in claim.evidence_spans:
                if span not in self.user_message:
                    raise ValueError(
                        f"{self.case_id} evidence span is not a user_message substring: {span}"
                    )
        for span in self.expected_discarded_spans:
            if span not in self.user_message:
                raise ValueError(
                    f"{self.case_id} discarded span is not a user_message substring: {span}"
                )
        if self.slice == "context_reply" and self.pending_memory_context is None:
            raise ValueError(f"{self.case_id} context_reply requires pending_memory_context")
        return self


class ExtractionSemanticMatcher(Protocol):
    async def align(
        self,
        *,
        user_message: str,
        pending_memory_context: dict[str, Any] | None,
        expected_claims: list[dict[str, Any]],
        actual_claims: list[dict[str, Any]],
        trace: Any | None = None,
    ) -> ExtractionAlignmentResult: ...


def load_memory_extraction_v1_cases(path: Path) -> list[ExtractionV1Case]:
    cases: list[ExtractionV1Case] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = ExtractionV1Case.model_validate_json(line)
        except Exception as exc:
            raise ValueError(f"invalid Extraction V1 case on line {line_number}: {exc}") from exc
        if case.case_id in seen:
            raise ValueError(f"duplicate Extraction V1 case_id: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    return cases


async def evaluate_memory_extraction_v1(
    dataset_path: Path,
    *,
    flash_extractor: Any,
    cascade_extractor: Any,
    semantic_matcher: ExtractionSemanticMatcher,
    observer: LangSmithExtractionObserver | None = None,
    case_id: str | None = None,
    fail_on_error: bool = False,
) -> dict[str, Any]:
    all_cases = load_memory_extraction_v1_cases(dataset_path)
    cases = [case for case in all_cases if case_id is None or case.case_id == case_id]
    if not cases:
        raise ValueError(f"no Extraction V1 cases matched: {case_id}")
    active_observer = observer or LangSmithExtractionObserver(enabled=False)
    layer_rows: dict[str, list[dict[str, Any]]] = {
        "flash_raw": [],
        "flash_post_repair": [],
        "production_cascade": [],
    }
    case_results: list[dict[str, Any]] = []

    for case in cases:
        try:
            case_result = await _evaluate_case(
                case,
                flash_extractor=flash_extractor,
                cascade_extractor=cascade_extractor,
                semantic_matcher=semantic_matcher,
                observer=active_observer,
            )
        except Exception as exc:
            if fail_on_error:
                raise
            case_result = _failed_case_result(case, exc)
        case_results.append(case_result)
        for layer in layer_rows:
            layer_rows[layer].append(case_result["layers"][layer])

    layer_reports = {
        layer: {
            "metrics": _aggregate_metrics(rows),
            "slices": _slice_reports(rows),
        }
        for layer, rows in layer_rows.items()
    }
    contributions = _contribution_metrics(case_results)
    telemetry = _telemetry(case_results, semantic_matcher)
    priorities = _remediation_priorities(layer_reports["production_cascade"])
    return {
        "evaluation": "memory_extraction_v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "dataset": str(dataset_path),
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "source_of_truth": "local_jsonl",
        "gate_participates_in_scoring": False,
        "production_extractor_modified": False,
        "flash_sampling_strategy": (
            "one real Flash call per case; the exact response is parsed for Post-Repair and "
            "replayed through TieredMemoryExtractor so Strong contribution is attributable"
        ),
        "case_filter": case_id,
        "case_count": len(cases),
        "models": {
            "flash": getattr(flash_extractor, "_model", None),
            "strong": (
                getattr(getattr(cascade_extractor, "_strong", None), "_model", None)
            ),
            "semantic_matcher": getattr(semantic_matcher, "model", None),
        },
        "pending_memory_context_path": (
            "ExtractionV1Case.pending_memory_context -> PendingMemoryContext.model_validate "
            "-> _build_prompt(runtime_context.pending_memory_context)"
        ),
        "langsmith": {
            "requested": active_observer.requested,
            "enabled": active_observer.enabled,
            "disabled_reason": active_observer.disabled_reason,
            "dataset_name": active_observer.dataset_name,
            "experiments": active_observer.experiments if active_observer.enabled else {},
        },
        "layers": layer_reports,
        "contributions": contributions,
        "telemetry": telemetry,
        "cases": case_results,
        "manual_review_case_ids": [
            row["case_id"]
            for row in case_results
            if any(
                layer["alignment"]["uncertain"] for layer in row["layers"].values()
            )
        ],
        "next_remediation_priority": priorities,
    }


async def _evaluate_case(
    case: ExtractionV1Case,
    *,
    flash_extractor: Any,
    cascade_extractor: Any,
    semantic_matcher: ExtractionSemanticMatcher,
    observer: LangSmithExtractionObserver,
) -> dict[str, Any]:
    history = _build_history(case)
    pending = (
        PendingMemoryContext.model_validate(case.pending_memory_context)
        if case.pending_memory_context is not None
        else None
    )
    existing = [MemoryItem.model_validate(value) for value in case.existing_memories]
    metadata = _case_metadata(case)
    alignment_cache: dict[str, ExtractionAlignmentResult] = {}
    inputs = {
        "case_id": case.case_id,
        "user_message": case.user_message,
        "reference_time": case.reference_time.isoformat(),
        "conversation_history": case.conversation_history,
        "pending_memory_context": case.pending_memory_context,
        "existing_memories": case.existing_memories,
    }

    with observer.case(
        "flash_diagnostic",
        inputs=inputs,
        metadata={**metadata, "experiment_stage": "flash_diagnostic"},
    ) as flash_outputs:
        flash_trace = _trace_recorder(observer, "flash_diagnostic", metadata)
        diagnostic = await run_flash_raw_diagnostic(
            flash_extractor,
            case.user_message,
            reference_time=case.reference_time,
            existing_memories=existing,
            conversation_history=history,
            pending_memory_context=pending,
            trace=flash_trace,
        )
        raw_row = await _evaluate_layer(
            case,
            layer="flash_raw",
            claims=diagnostic.raw_claims,
            semantic_matcher=semantic_matcher,
            trace=flash_trace,
            alignment_cache=alignment_cache,
            raw_json_valid=diagnostic.raw_json_valid,
            extraction_error=diagnostic.raw_error,
        )
        post_claims = _claim_dicts(diagnostic.post_repair_extraction)
        post_row = await _evaluate_layer(
            case,
            layer="flash_post_repair",
            claims=post_claims,
            semantic_matcher=semantic_matcher,
            trace=flash_trace,
            alignment_cache=alignment_cache,
            extraction_error=diagnostic.post_repair_error,
        )
        flash_outputs.update(
            {
                "raw": _diagnostic_json(diagnostic),
                "raw_evaluation": _compact_layer_output(raw_row),
                "post_repair_evaluation": _compact_layer_output(post_row),
            }
        )

    with observer.case(
        "production_cascade",
        inputs=inputs,
        metadata={**metadata, "experiment_stage": "production_cascade"},
    ) as cascade_outputs:
        cascade_trace = _trace_recorder(observer, "production_cascade", metadata)
        final_extraction, attempts = await run_production_cascade_from_flash_result(
            cascade_extractor,
            diagnostic,
            case.user_message,
            reference_time=case.reference_time,
            existing_memories=existing,
            conversation_history=history,
            pending_memory_context=pending,
            trace=cascade_trace,
        )
        cascade_row = await _evaluate_layer(
            case,
            layer="production_cascade",
            claims=_claim_dicts(final_extraction),
            semantic_matcher=semantic_matcher,
            trace=cascade_trace,
            alignment_cache=alignment_cache,
        )
        cascade_outputs.update(_compact_layer_output(cascade_row))

    strong_attempts = [attempt for attempt in attempts if attempt.tier == "strong"]
    raw_matches = raw_row["counts"]["matched_expected"]
    post_matches = post_row["counts"]["matched_expected"]
    cascade_matches = cascade_row["counts"]["matched_expected"]
    case_errors = sorted(
        set(raw_row["errors"] + post_row["errors"] + cascade_row["errors"])
    )
    if post_matches > raw_matches:
        case_errors.append("REPAIR_DEPENDENT")
    if strong_attempts:
        if cascade_matches > post_matches:
            case_errors.append("UPGRADE_HELPED")
        elif cascade_matches < post_matches:
            case_errors.append("UPGRADE_HURT")
        else:
            case_errors.append("UPGRADE_UNNECESSARY")
    if (
        diagnostic.raw_claim_count > len(post_claims)
        and (
            diagnostic.post_repair_error_category
            in {"schema_validation", "semantic_validation"}
            or "canonical" in diagnostic.repair_steps
        )
    ):
        case_errors.append("CANONICAL_COUPLING_DIAGNOSTIC")

    return {
        "case_id": case.case_id,
        **metadata,
        "user_message": case.user_message,
        "expected_claims": [claim.model_dump(mode="json") for claim in case.expected_claims],
        "flash_diagnostic": _diagnostic_json(diagnostic),
        "cascade_attempts": [attempt.model_dump(mode="json") for attempt in attempts],
        "strong_upgrade_triggered": bool(strong_attempts),
        "errors": sorted(set(case_errors)),
        "layers": {
            "flash_raw": raw_row,
            "flash_post_repair": post_row,
            "production_cascade": cascade_row,
        },
    }


async def _evaluate_layer(
    case: ExtractionV1Case,
    *,
    layer: str,
    claims: list[dict[str, Any]],
    semantic_matcher: ExtractionSemanticMatcher,
    trace: Any | None,
    raw_json_valid: bool | None = None,
    extraction_error: str | None = None,
    alignment_cache: dict[str, ExtractionAlignmentResult] | None = None,
) -> dict[str, Any]:
    expected = [claim.model_dump(mode="json") for claim in case.expected_claims]
    if expected and claims:
        cache_key = _alignment_cache_key(expected, claims)
        alignment = (alignment_cache or {}).get(cache_key)
        if alignment is None:
            alignment = await semantic_matcher.align(
                user_message=case.user_message,
                pending_memory_context=case.pending_memory_context,
                expected_claims=expected,
                actual_claims=claims,
                trace=trace,
            )
            if alignment_cache is not None:
                alignment_cache[cache_key] = alignment
    else:
        alignment = ExtractionAlignmentResult(
            unmatched_expected=list(range(len(expected))),
            unmatched_actual=list(range(len(claims))),
        )
    pairs = [pair for pair in alignment.matches if pair.proposition_equivalent]
    semantic_pairs = [pair for pair in pairs if pair.semantic_match]
    matched_expected = {pair.expected_index for pair in semantic_pairs}
    matched_actual = {pair.actual_index for pair in semantic_pairs}
    unmatched_expected = set(range(len(expected))) - matched_expected
    unmatched_actual = set(range(len(claims))) - matched_actual
    evidence_substring_valid = sum(
        _claim_evidence_valid(claim, case.user_message) for claim in claims
    )
    kind_correct = subject_correct = perspective_correct = 0
    evidence_supported = 0
    user_belief_pairs = user_belief_subject_correct = user_belief_perspective_correct = 0
    errors: set[str] = set()
    for pair in pairs:
        gold, actual = expected[pair.expected_index], claims[pair.actual_index]
        kind_match = _field_equal(gold, actual, "kind")
        subject_match = _field_equal(gold, actual, "subject")
        perspective_match = _field_equal(gold, actual, "perspective")
        kind_correct += kind_match
        subject_correct += subject_match
        perspective_correct += perspective_match
        evidence_supported += pair.evidence_support == "PASS"
        if gold["perspective"] == "user_belief":
            user_belief_pairs += 1
            user_belief_subject_correct += subject_match
            user_belief_perspective_correct += perspective_match
        if not kind_match:
            errors.add("KIND_ERROR")
        if not subject_match:
            errors.add("SUBJECT_ERROR")
        if not perspective_match:
            errors.add("PERSPECTIVE_ERROR")
        if pair.evidence_support != "PASS":
            errors.add("EVIDENCE_ERROR")
    if unmatched_expected:
        errors.add("MISSED_CLAIM")
    if expected and not claims:
        errors.add("EMPTY_POSITIVE")
    if unmatched_actual:
        errors.add("SPURIOUS_CLAIM")
    if evidence_substring_valid != len(claims):
        errors.add("EVIDENCE_ERROR")
    if alignment.over_merge_actual_indices:
        errors.add("OVER_MERGE")
    if alignment.over_split_expected_indices:
        errors.add("OVER_SPLIT")
    if raw_json_valid is False:
        errors.add("RAW_JSON_ERROR")
    if extraction_error:
        errors.add("GENERIC_SCHEMA_ERROR")
    if case.slice == "context_reply" and unmatched_expected:
        errors.add("CONTEXT_BINDING_ERROR")
        if _is_context_correction(case):
            errors.add("CONTEXT_CORRECTION_ERROR")
        if "topic_switch" in case.distractor_types:
            errors.add("CONTEXT_TOPIC_SWITCH_ERROR")

    atomization_pass = (
        case.slice == "atomization"
        and not unmatched_expected
        and not unmatched_actual
        and not alignment.over_merge_actual_indices
        and not alignment.over_split_expected_indices
    )
    return {
        "case_id": case.case_id,
        "slice": case.slice,
        "difficulty": case.difficulty,
        "length_class": case.length_class,
        "contains_distractor": case.contains_distractor,
        "layer": layer,
        "claims": claims,
        "alignment": alignment.model_dump(mode="json"),
        "errors": sorted(errors),
        "empty_positive": bool(expected and not claims),
        "negative_false_positive": bool(not expected and claims),
        "atomization_pass": atomization_pass,
        "mixed_perspective_case": {
            claim["perspective"] for claim in expected
        }.issuperset({"user_reported", "user_belief"}),
        "context_subtype": _context_subtype(case),
        "counts": {
            "expected": len(expected),
            "actual": len(claims),
            "matched_expected": len(matched_expected),
            "unmatched_expected": len(unmatched_expected),
            "unmatched_actual": len(unmatched_actual),
            "field_pair_count": len(pairs),
            "kind_correct": kind_correct,
            "subject_correct": subject_correct,
            "perspective_correct": perspective_correct,
            "user_belief_pair_count": user_belief_pairs,
            "user_belief_subject_correct": user_belief_subject_correct,
            "user_belief_perspective_correct": user_belief_perspective_correct,
            "evidence_substring_valid": evidence_substring_valid,
            "evidence_supported": evidence_supported,
        },
    }


def _aggregate_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals: Counter[str] = Counter()
    for row in rows:
        totals.update(row["counts"])
    positive_rows = [row for row in rows if row["counts"]["expected"] > 0]
    negative_rows = [row for row in rows if row["counts"]["expected"] == 0]
    atom_rows = [row for row in rows if row["slice"] == "atomization"]
    context_rows = [row for row in rows if row["slice"] == "context_reply"]
    mixed_rows = [row for row in rows if row["mixed_perspective_case"]]
    mixed_pair_count = sum(row["counts"]["field_pair_count"] for row in mixed_rows)
    mixed_perspective_correct = sum(
        row["counts"]["perspective_correct"] for row in mixed_rows
    )
    context_expected = sum(row["counts"]["expected"] for row in context_rows)
    context_matched = sum(row["counts"]["matched_expected"] for row in context_rows)
    return {
        "case_count": len(rows),
        "expected_claim_count": totals["expected"],
        "actual_claim_count": totals["actual"],
        "matched_expected_claim_count": totals["matched_expected"],
        "claim_recall": _ratio(totals["matched_expected"], totals["expected"]),
        "spurious_claim_rate": _ratio(totals["unmatched_actual"], totals["actual"]),
        "kind_accuracy": _ratio(totals["kind_correct"], totals["field_pair_count"]),
        "subject_accuracy": _ratio(totals["subject_correct"], totals["field_pair_count"]),
        "perspective_accuracy": _ratio(
            totals["perspective_correct"], totals["field_pair_count"]
        ),
        "user_belief_perspective_accuracy": _ratio(
            totals["user_belief_perspective_correct"],
            totals["user_belief_pair_count"],
        ),
        "user_belief_subject_accuracy": _ratio(
            totals["user_belief_subject_correct"],
            totals["user_belief_pair_count"],
        ),
        "mixed_perspective_case_count": len(mixed_rows),
        "mixed_perspective_accuracy": _ratio(
            mixed_perspective_correct,
            mixed_pair_count,
        ),
        "evidence_substring_validity": _ratio(
            totals["evidence_substring_valid"], totals["actual"]
        ),
        "evidence_semantic_support_accuracy": _ratio(
            totals["evidence_supported"], totals["field_pair_count"]
        ),
        "atomization_accuracy": _ratio(
            sum(row["atomization_pass"] for row in atom_rows), len(atom_rows)
        ),
        "over_merge_case_count": sum("OVER_MERGE" in row["errors"] for row in rows),
        "over_split_case_count": sum("OVER_SPLIT" in row["errors"] for row in rows),
        "context_reply_recall": _ratio(context_matched, context_expected),
        "empty_positive_rate": _ratio(
            sum(row["empty_positive"] for row in positive_rows), len(positive_rows)
        ),
        "negative_restraint_false_positive_rate": _ratio(
            sum(row["negative_false_positive"] for row in negative_rows), len(negative_rows)
        ),
        "uncertain_alignment_case_count": sum(
            row["alignment"]["uncertain"] for row in rows
        ),
        "error_taxonomy": dict(
            sorted(Counter(error for row in rows for error in row["errors"]).items())
        ),
    }


def _slice_reports(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    dimensions = {
        "business": lambda row: row["slice"],
        "length": lambda row: row["length_class"],
        "difficulty": lambda row: row["difficulty"],
        "noise": lambda row: "noisy" if row["contains_distractor"] else "clean",
        "context_reply": lambda row: row["context_subtype"],
    }
    for dimension, key_fn in dimensions.items():
        values: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            key = key_fn(row)
            if key is not None:
                values.setdefault(key, []).append(row)
        groups[dimension] = {
            key: _aggregate_metrics(group_rows) for key, group_rows in sorted(values.items())
        }
    return groups


def _contribution_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    repair_helped = repair_hurt = repair_unchanged = 0
    upgrade_helped = upgrade_hurt = upgrade_unchanged = upgrade_triggered = 0
    raw_matched = post_matched = cascade_matched = expected_total = 0
    for result in results:
        layers = result["layers"]
        raw = layers["flash_raw"]["counts"]["matched_expected"]
        post = layers["flash_post_repair"]["counts"]["matched_expected"]
        cascade = layers["production_cascade"]["counts"]["matched_expected"]
        raw_matched += raw
        post_matched += post
        cascade_matched += cascade
        expected_total += layers["flash_raw"]["counts"]["expected"]
        repair_helped += post > raw
        repair_hurt += post < raw
        repair_unchanged += post == raw
        if result.get("strong_upgrade_triggered"):
            upgrade_triggered += 1
            upgrade_helped += cascade > post
            upgrade_hurt += cascade < post
            upgrade_unchanged += cascade == post
    return {
        "safe_repair": {
            "helped_case_count": repair_helped,
            "hurt_case_count": repair_hurt,
            "unchanged_case_count": repair_unchanged,
            "net_matched_claim_delta": post_matched - raw_matched,
            "claim_recall_delta": round(
                _ratio(post_matched, expected_total) - _ratio(raw_matched, expected_total),
                4,
            ),
        },
        "strong_upgrade": {
            "triggered_case_count": upgrade_triggered,
            "trigger_rate": _ratio(upgrade_triggered, len(results)),
            "helped_case_count": upgrade_helped,
            "hurt_case_count": upgrade_hurt,
            "unchanged_case_count": upgrade_unchanged,
            "net_matched_claim_delta": cascade_matched - post_matched,
            "claim_recall_delta": round(
                _ratio(cascade_matched, expected_total)
                - _ratio(post_matched, expected_total),
                4,
            ),
        },
    }


def _telemetry(results: list[dict[str, Any]], matcher: Any) -> dict[str, Any]:
    diagnostics = [row["flash_diagnostic"] for row in results if row["flash_diagnostic"]]
    flash_latencies = [float(row["latency_ms"]) for row in diagnostics]
    attempts = [attempt for row in results for attempt in row["cascade_attempts"]]
    strong_attempts = [attempt for attempt in attempts if attempt.get("tier") == "strong"]
    strong_latencies = [float(attempt.get("duration_ms") or 0) for attempt in strong_attempts]
    matcher_latencies = list(getattr(matcher, "latencies_ms", []))
    return {
        "flash_call_count": len(results),
        "flash_failure_count": len(results) - len(diagnostics),
        "flash_latency_p50_ms": _percentile(flash_latencies, 0.50),
        "flash_latency_p95_ms": _percentile(flash_latencies, 0.95),
        "flash_prompt_tokens": sum(int(row.get("prompt_tokens", 0)) for row in diagnostics),
        "flash_completion_tokens": sum(
            int(row.get("completion_tokens", 0)) for row in diagnostics
        ),
        "flash_total_tokens": sum(int(row.get("total_tokens", 0)) for row in diagnostics),
        "strong_call_count": len(strong_attempts),
        "strong_failure_count": sum(
            attempt.get("status") == "failed" for attempt in strong_attempts
        ),
        "strong_latency_p50_ms": _percentile(strong_latencies, 0.50),
        "strong_latency_p95_ms": _percentile(strong_latencies, 0.95),
        "strong_prompt_tokens": sum(
            int(attempt.get("prompt_tokens") or 0) for attempt in strong_attempts
        ),
        "strong_completion_tokens": sum(
            int(attempt.get("completion_tokens") or 0) for attempt in strong_attempts
        ),
        "strong_total_tokens": sum(
            int(attempt.get("total_tokens") or 0) for attempt in strong_attempts
        ),
        "semantic_matcher_call_count": int(getattr(matcher, "call_count", 0)),
        "semantic_matcher_failure_count": int(getattr(matcher, "failure_count", 0)),
        "semantic_matcher_latency_p50_ms": _percentile(matcher_latencies, 0.50),
        "semantic_matcher_latency_p95_ms": _percentile(matcher_latencies, 0.95),
        "semantic_matcher_prompt_tokens": int(getattr(matcher, "prompt_tokens", 0)),
        "semantic_matcher_completion_tokens": int(
            getattr(matcher, "completion_tokens", 0)
        ),
        "semantic_matcher_total_tokens": int(getattr(matcher, "total_tokens", 0)),
        "semantic_matcher_failure_diagnostics": list(
            getattr(matcher, "failure_diagnostics", [])
        ),
    }


def render_memory_extraction_v1_report(report: dict[str, Any]) -> str:
    layers = report["layers"]
    langsmith = report["langsmith"]
    langsmith_requested = bool(langsmith.get("requested", False))
    langsmith_disabled_reason = langsmith.get("disabled_reason") or (
        "not_requested" if not langsmith_requested else "unknown"
    )
    labels = [
        ("Flash Raw", "flash_raw"),
        ("Flash Post-Repair", "flash_post_repair"),
        ("Production Cascade", "production_cascade"),
    ]
    metric_rows = [
        ("Claim Recall", "claim_recall"),
        ("Spurious Claim Rate", "spurious_claim_rate"),
        ("Kind Accuracy", "kind_accuracy"),
        ("Subject Accuracy", "subject_accuracy"),
        ("Perspective Accuracy", "perspective_accuracy"),
        ("Atomization Accuracy", "atomization_accuracy"),
        ("Context Reply Recall", "context_reply_recall"),
        ("Negative FP Rate", "negative_restraint_false_positive_rate"),
    ]
    lines = [
        "# Memory Extraction V1 Evaluation Report",
        "",
        f"Generated: `{report['generated_at']}`  ",
        f"Dataset: `{report['dataset']}`  ",
        f"Dataset SHA256: `{report['dataset_sha256']}`  ",
        "Gate participates in scoring: `False`  ",
        "Canonical/state fields participate in Extraction scoring: `False`  ",
        f"PendingMemoryContext path: `{report['pending_memory_context_path']}`  ",
        f"LangSmith requested: `{langsmith_requested}`  ",
        f"LangSmith enabled: `{langsmith['enabled']}`  ",
        f"LangSmith disabled reason: `{langsmith_disabled_reason}`  ",
        f"LangSmith dataset: `{langsmith['dataset_name']}`",
        "",
        "## Flash / Repair / Cascade",
        "",
        "| Metric | " + " | ".join(label for label, _ in labels) + " |",
        "|---|" + "---:|" * len(labels),
    ]
    for title, key in metric_rows:
        values = [f"{layers[layer]['metrics'][key]:.4f}" for _, layer in labels]
        lines.append(f"| {title} | " + " | ".join(values) + " |")
    lines.extend(["", "## Production Cascade Slices", ""])
    for dimension, groups in layers["production_cascade"]["slices"].items():
        lines.extend(
            [
                f"### {dimension.title()}",
                "",
                "| Slice | Cases | Claim Recall | Spurious Rate |",
                "|---|---:|---:|---:|",
            ]
        )
        for name, metrics in groups.items():
            lines.append(
                f"| {name} | {metrics['case_count']} | {metrics['claim_recall']:.4f} | "
                f"{metrics['spurious_claim_rate']:.4f} |"
            )
        lines.append("")
    metrics = layers["production_cascade"]["metrics"]
    telemetry = report["telemetry"]
    taxonomy = metrics["error_taxonomy"]
    clean = layers["production_cascade"]["slices"]["noise"].get("clean", {})
    noisy = layers["production_cascade"]["slices"]["noise"].get("noisy", {})
    length = layers["production_cascade"]["slices"]["length"]
    repair = report["contributions"]["safe_repair"]
    upgrade = report["contributions"]["strong_upgrade"]
    noisy_recall_delta = noisy.get("claim_recall", 0.0) - clean.get(
        "claim_recall", 0.0
    )
    noisy_spurious_delta = noisy.get("spurious_claim_rate", 0.0) - clean.get(
        "spurious_claim_rate", 0.0
    )
    lines.extend(
        [
            "## Error Taxonomy",
            "",
            "| Error | Cases |",
            "|---|---:|",
            *[f"| {name} | {count} |" for name, count in taxonomy.items()],
            "",
            "## Model Telemetry",
            "",
            "| Component | Calls | Failures | p50 ms | p95 ms | Total tokens |",
            "|---|---:|---:|---:|---:|---:|",
            f"| Flash | {telemetry['flash_call_count']} | "
            f"{telemetry['flash_failure_count']} | "
            f"{telemetry['flash_latency_p50_ms']:.2f} | "
            f"{telemetry['flash_latency_p95_ms']:.2f} | "
            f"{telemetry['flash_total_tokens']} |",
            f"| Strong | {telemetry['strong_call_count']} | "
            f"{telemetry['strong_failure_count']} | "
            f"{telemetry['strong_latency_p50_ms']:.2f} | "
            f"{telemetry['strong_latency_p95_ms']:.2f} | "
            f"{telemetry['strong_total_tokens']} |",
            f"| Semantic matcher | {telemetry['semantic_matcher_call_count']} | "
            f"{telemetry['semantic_matcher_failure_count']} | "
            f"{telemetry['semantic_matcher_latency_p50_ms']:.2f} | "
            f"{telemetry['semantic_matcher_latency_p95_ms']:.2f} | "
            f"{telemetry['semantic_matcher_total_tokens']} |",
            "",
            "## Manual Semantic Review",
            "",
            (
                "Fail-closed semantic matcher cases: "
                + ", ".join(f"`{case_id}`" for case_id in report["manual_review_case_ids"])
                if report["manual_review_case_ids"]
                else "Fail-closed semantic matcher cases: none."
            ),
            "",
            "## Required Answers",
            "",
            f"1. Flash Raw Claim Recall: `{layers['flash_raw']['metrics']['claim_recall']:.4f}`",
            "2. Post-Repair Claim Recall: "
            f"`{layers['flash_post_repair']['metrics']['claim_recall']:.4f}`",
            f"3. Production Cascade Claim Recall: `{metrics['claim_recall']:.4f}`",
            "4. Spurious Claim Rate (Raw / Repair / Cascade): "
            f"`{layers['flash_raw']['metrics']['spurious_claim_rate']:.4f} / "
            f"{layers['flash_post_repair']['metrics']['spurious_claim_rate']:.4f} / "
            f"{metrics['spurious_claim_rate']:.4f}`",
            f"5. Perspective Accuracy: `{metrics['perspective_accuracy']:.4f}`",
            "6. USER_BELIEF Perspective Accuracy: "
            f"`{metrics['user_belief_perspective_accuracy']:.4f}`; mixed "
            f"USER_REPORTED + USER_BELIEF cases: "
            f"`{metrics['mixed_perspective_accuracy']:.4f}`",
            f"7. Atomization Accuracy: `{metrics['atomization_accuracy']:.4f}`",
            f"8. Context Reply Recall: `{metrics['context_reply_recall']:.4f}`",
            f"9. Empty Positive Rate: `{metrics['empty_positive_rate']:.4f}`",
            "10. Negative Restraint FP Rate: "
            f"`{metrics['negative_restraint_false_positive_rate']:.4f}`",
            "11. Noisy vs clean Claim Recall: "
            f"`{noisy.get('claim_recall', 0.0):.4f} vs {clean.get('claim_recall', 0.0):.4f}`; "
            f"delta `{noisy_recall_delta:+.4f}`. "
            "Spurious Rate: "
            f"`{noisy.get('spurious_claim_rate', 0.0):.4f} vs "
            f"{clean.get('spurious_claim_rate', 0.0):.4f}`; delta "
            f"`{noisy_spurious_delta:+.4f}`",
            "12. Short / medium / long Claim Recall: "
            f"`{length.get('short', {}).get('claim_recall', 0.0):.4f} / "
            f"{length.get('medium', {}).get('claim_recall', 0.0):.4f} / "
            f"{length.get('long', {}).get('claim_recall', 0.0):.4f}`; max-min gap "
            f"`{_metric_range(length, 'claim_recall'):.4f}`.",
            "13. Safe Repair helped / hurt / unchanged: "
            f"`{repair['helped_case_count']} / {repair['hurt_case_count']} / "
            f"{repair['unchanged_case_count']}`; net matched-claim delta "
            f"`{repair['net_matched_claim_delta']:+d}`, recall delta "
            f"`{repair['claim_recall_delta']:+.4f}`.",
            "14. Strong Upgrade helped / hurt / unchanged / triggered rate: "
            f"`{upgrade['helped_case_count']} / {upgrade['hurt_case_count']} / "
            f"{upgrade['unchanged_case_count']} / {upgrade['trigger_rate']:.4f}`; net "
            f"matched-claim delta `{upgrade['net_matched_claim_delta']:+d}`, recall delta "
            f"`{upgrade['claim_recall_delta']:+.4f}`.",
            "15. Top Extraction bottlenecks are listed below.",
            "",
            "## NEXT_REMEDIATION_PRIORITY",
            "",
        ]
    )
    for index, priority in enumerate(report["next_remediation_priority"], 1):
        lines.append(f"Top {index}: {priority}")
    lines.extend(
        [
            "",
            "This report measures the current baseline only. It does not approve or implement "
            "Extractor remediation.",
        ]
    )
    return "\n".join(lines) + "\n"


def _metric_range(groups: dict[str, dict[str, Any]], metric: str) -> float:
    values = [float(group.get(metric, 0.0)) for group in groups.values()]
    return max(values) - min(values) if values else 0.0


def _remediation_priorities(layer: dict[str, Any]) -> list[str]:
    metrics = layer["metrics"]
    candidates = [
        (1 - metrics["context_reply_recall"], "Context-dependent reply extraction"),
        (1 - metrics["atomization_accuracy"], "Claim atomization and proposition boundaries"),
        (1 - metrics["kind_accuracy"], "Memory kind classification"),
        (1 - metrics["subject_accuracy"], "Claim subject attribution"),
        (1 - metrics["perspective_accuracy"], "Claim perspective preservation"),
        (
            1 - metrics["user_belief_perspective_accuracy"],
            "USER_BELIEF perspective preservation",
        ),
        (1 - metrics["evidence_substring_validity"], "Evidence span validity"),
        (1 - metrics["evidence_semantic_support_accuracy"], "Evidence semantic support"),
        (metrics["empty_positive_rate"], "Empty positive extractions"),
        (
            metrics["negative_restraint_false_positive_rate"],
            "Negative-restraint spurious claims",
        ),
        (metrics["spurious_claim_rate"], "General spurious claim restraint"),
    ]
    ranked = sorted(candidates, key=lambda item: (-item[0], item[1]))
    return [f"{label} (gap={score:.4f})" for score, label in ranked[:3]]


def _failed_case_result(case: ExtractionV1Case, exc: Exception) -> dict[str, Any]:
    layers = {}
    for layer in ("flash_raw", "flash_post_repair", "production_cascade"):
        row = {
            "case_id": case.case_id,
            "slice": case.slice,
            "difficulty": case.difficulty,
            "length_class": case.length_class,
            "contains_distractor": case.contains_distractor,
            "layer": layer,
            "claims": [],
            "alignment": ExtractionAlignmentResult(
                unmatched_expected=list(range(len(case.expected_claims))),
                uncertain=True,
                reason=f"case_execution_failed:{type(exc).__name__}",
            ).model_dump(mode="json"),
            "errors": (
                ["EMPTY_POSITIVE", "GENERIC_SCHEMA_ERROR"]
                if case.expected_claims
                else ["GENERIC_SCHEMA_ERROR"]
            ),
            "empty_positive": bool(case.expected_claims),
            "negative_false_positive": False,
            "atomization_pass": False,
            "mixed_perspective_case": {
                claim.perspective for claim in case.expected_claims
            }.issuperset({"user_reported", "user_belief"}),
            "context_subtype": _context_subtype(case),
            "counts": {
                "expected": len(case.expected_claims),
                "actual": 0,
                "matched_expected": 0,
                "unmatched_expected": len(case.expected_claims),
                "unmatched_actual": 0,
                "field_pair_count": 0,
                "kind_correct": 0,
                "subject_correct": 0,
                "perspective_correct": 0,
                "user_belief_pair_count": 0,
                "user_belief_subject_correct": 0,
                "user_belief_perspective_correct": 0,
                "evidence_substring_valid": 0,
                "evidence_supported": 0,
            },
        }
        layers[layer] = row
    return {
        "case_id": case.case_id,
        **_case_metadata(case),
        "user_message": case.user_message,
        "expected_claims": [claim.model_dump(mode="json") for claim in case.expected_claims],
        "flash_diagnostic": None,
        "cascade_attempts": [],
        "strong_upgrade_triggered": False,
        "errors": ["GENERIC_SCHEMA_ERROR"],
        "execution_error": f"{type(exc).__name__}: {exc}",
        "layers": layers,
    }


def _build_history(case: ExtractionV1Case) -> list[StoredMessage]:
    result = []
    for index, message in enumerate(case.conversation_history):
        role = MessageRole(str(message["role"]).casefold())
        result.append(
            StoredMessage(
                id=str(uuid5(NAMESPACE_URL, f"{case.case_id}:history:{index}")),
                conversation_id=f"eval-{case.case_id}",
                user_id="memory-extraction-v1-eval",
                relationship_id=f"relationship-{case.case_id}",
                role=role,
                content=str(message["content"]),
                created_at=case.reference_time,
            )
        )
    return result


def _trace_recorder(
    observer: LangSmithExtractionObserver,
    stage: str,
    metadata: dict[str, Any],
) -> LangSmithTraceRecorder:
    return LangSmithTraceRecorder(
        enabled=observer.enabled,
        project_name=observer.experiments[stage],
        metadata={**metadata, "experiment_stage": stage},
        client=observer.client,
    )


def _case_metadata(case: ExtractionV1Case) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "slice": case.slice,
        "difficulty": case.difficulty,
        "length_class": case.length_class,
        "contains_distractor": case.contains_distractor,
        "prompt_version": _MEMORY_PROMPT_VERSION,
    }


def _claim_dicts(extraction: Any | None) -> list[dict[str, Any]]:
    if extraction is None:
        return []
    return [claim.model_dump(mode="json") for claim in extraction.claims]


def _diagnostic_json(value: FlashDiagnosticResult) -> dict[str, Any]:
    return value.model_dump(mode="json", exclude={"post_repair_extraction"}) | {
        "post_repair_claims": _claim_dicts(value.post_repair_extraction)
    }


def _compact_layer_output(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "claims": row["claims"],
        "alignment": row["alignment"],
        "errors": row["errors"],
    }


def _claim_evidence_valid(claim: dict[str, Any], source: str) -> bool:
    spans = claim.get("evidence_spans")
    return bool(
        isinstance(spans, list)
        and spans
        and all(isinstance(span, str) and span in source for span in spans)
    )


def _alignment_cache_key(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
) -> str:
    semantic_fields = (
        "kind",
        "subject",
        "perspective",
        "predicate",
        "object",
        "summary",
        "evidence_spans",
        "payload",
    )
    defaults: dict[str, Any] = {
        "object": None,
        "evidence_spans": [],
        "payload": {},
    }
    compact_actual = [
        {key: claim.get(key, defaults.get(key)) for key in semantic_fields} for claim in actual
    ]
    return json.dumps(
        {"expected": expected, "actual": compact_actual},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _field_equal(expected: dict[str, Any], actual: dict[str, Any], field: str) -> bool:
    return str(expected.get(field, "")).casefold() == str(actual.get(field, "")).casefold()


def _is_context_correction(case: ExtractionV1Case) -> bool:
    return any(marker in case.user_message for marker in ("不对", "不是", "说错", "其实"))


def _context_subtype(case: ExtractionV1Case) -> str | None:
    if case.slice != "context_reply":
        return None
    if "topic_switch" in case.distractor_types:
        return "topic_switch"
    if "context_refusal" in case.distractor_types:
        return "refusal"
    if "context_non_answer" in case.distractor_types:
        return "unknown"
    if _is_context_correction(case):
        return "correction"
    if case.user_message.strip("。！？!? ") in {"没有", "不是", "没了"}:
        return "negative_answer"
    if not case.expected_claims:
        return "negative_answer"
    context = case.pending_memory_context or {}
    return str(context.get("expected_slot") or "other")


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return round(ordered[index], 2)


__all__ = [
    "EXPECTED_SLICE_COUNTS",
    "ExtractionV1Case",
    "evaluate_memory_extraction_v1",
    "load_memory_extraction_v1_cases",
    "render_memory_extraction_v1_report",
]

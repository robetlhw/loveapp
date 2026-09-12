"""Checkpoint scoring for the live benchmark, independent of model calls.

Gold is never passed to production components. A write is credited only when
the expected source, semantic content, target and mutation contract match.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from typing import Any

from loveapp.evaluation.memory_benchmark_v1 import MemoryBenchmarkCase

SCORING_VERSION = "checkpoint-v3"
ACTIVE = {"confirmed", "proposed"}
CLOSED = {"superseded", "expired", "rejected"}


def compact(value: Any) -> str:
    return re.sub(r"[\W_]+", "", str(value).casefold())


def _semantic_only(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _semantic_only(child)
            for key, child in value.items()
            if not any(
                word in key.casefold()
                for word in ("evidence", "provenance", "original", "raw", "source", "predicate")
            )
        }
    if isinstance(value, list):
        return [_semantic_only(child) for child in value]
    return value


def semantic_text(row: dict[str, Any]) -> str:
    content = {
        key: row.get(key)
        for key in ("summary", "object", "value", "state_value", "payload", "semantic_payload")
    }
    return compact(json.dumps(_semantic_only(content), ensure_ascii=False))


def _subject(value: Any) -> str:
    text = compact(value or "")
    return {
        "我": "user",
        "本人": "user",
        "她": "partner",
        "他": "partner",
        "对方": "partner",
        "对象": "partner",
        "我们": "relationship",
        "我和她": "relationship",
        "双方": "relationship",
        "同事": "colleague",
    }.get(text, text)


def evidence_matches(expected: list[str], observed: list[str]) -> bool:
    return bool(observed) and all(
        any(
            len(compact(span)) >= 2
            and (compact(span) in compact(other) or compact(other) in compact(span))
            and len(compact(other)) >= 2
            for other in observed
        )
        for span in expected
    )


def claim_checks(
    gold: dict[str, Any], row: dict[str, Any], *, evidence: bool = True, semantic_type: bool = True
) -> dict[str, bool]:
    payload = row.get("semantic_payload", row.get("payload", {})) or {}
    hint = row.get("target_semantic_hint", {}) or {}
    observed_type = row.get("semantic_type", "new_memory")
    subject = row.get("subject", row.get("subject_hint", hint.get("subject")))
    spans = row.get("evidence_spans") or [row.get("evidence_span", "")]
    value = semantic_text(row)
    predicate = (
        row.get("canonical_predicate")
        or row.get("custom_predicate")
        or row.get("raw_predicate")
        or row.get("predicate")
        or payload.get("predicate")
    )
    return dict(
        semantic_type=(not semantic_type or observed_type == gold["semantic_type"]),
        kind=row.get("kind", row.get("memory_kind", row.get("target_kind"))) == gold["kind"],
        subject=(
            _subject(subject) == _subject(gold["subject"])
            or (subject is None and observed_type in {"enrichment", "refinement"})
        ),
        evidence=not evidence or evidence_matches(gold["evidence_spans"], spans),
        value=all(
            any(compact(anchor) in value for anchor in group) for group in gold["value_groups"]
        ),
        predicate=(
            gold["predicate_policy"] == "semantic"
            or (gold["predicate_policy"] == "canonical" and predicate == gold["predicate"])
            or (
                gold["predicate_policy"] == "custom"
                and not row.get("canonical_predicate")
                and row.get("predicate_type") != "canonical"
            )
        ),
        event_type=(
            not gold.get("event_type")
            or payload.get("event_type", hint.get("event_type")) == gold["event_type"]
            or observed_type == "enrichment"
        ),
        attribute=(
            not gold.get("attribute")
            or row.get("attribute_name") == gold["attribute"]
            or not semantic_type
        ),
        perspective=not gold.get("perspective") or row.get("perspective") == gold["perspective"],
    )


def assign_unique(gold: list[Any], observed: list[Any], predicate: Any) -> dict[int, int]:
    """Maximum one-to-one matching, preventing one claim from earning two hits."""
    owners: dict[int, int] = {}

    def visit(index: int, seen: set[int]) -> bool:
        for other, row in enumerate(observed):
            if other in seen or not predicate(gold[index], row):
                continue
            seen.add(other)
            if other not in owners or visit(owners[other], seen):
                owners[other] = index
                return True
        return False

    for index in range(len(gold)):
        visit(index, set())
    return {index: other for other, index in owners.items()}


def _field(row: dict[str, Any], path: str) -> Any:
    value: Any = row
    for key in path.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value


def _business_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key not in {"updated_at", "last_seen_at", "last_used_at", "embedding"}
    }


def _source_matches(row: dict[str, Any], turn: dict[str, Any]) -> bool:
    return (
        bool(turn.get("source_message_id"))
        and row.get("source_message_id") == turn["source_message_id"]
    )


def score_case(case: MemoryBenchmarkCase, turns: list[dict[str, Any]]) -> dict[str, Any]:
    turn_map = {turn["turn_id"]: turn for turn in turns}
    claims = {
        claim.claim_id: claim.model_dump(mode="json") for claim in case.expected.stage2.claims
    }
    claim_results = []
    mapping: dict[str, str] = {}
    gate_checks = []
    stage1_checks = []
    failures: list[tuple[int, str, str]] = []
    order = {turn.turn_id: index for index, turn in enumerate(case.conversation)}

    def fail(turn_id: str, stage: str, reason: str) -> None:
        failures.append((order.get(turn_id, 999), stage, reason))

    positive_turns = {unit.turn_id for unit in case.expected.stage1.semantic_units}
    positive_turns |= {
        op.turn_id
        for op in case.expected.sub_operations
        if op.operation in {"ENRICH", "REFINE", "UPDATE", "PROJECT"}
    }
    for turn_id in sorted(
        positive_turns | set(case.expected.stage1.negative_turn_ids), key=order.get
    ):
        positive = turn_id in positive_turns
        actual = bool((turn_map.get(turn_id, {}).get("gate") or {}).get("should_extract"))
        gate_checks.append(
            dict(turn_id=turn_id, expected=positive, actual=actual, passed=positive == actual)
        )
        if positive != actual:
            fail(
                turn_id,
                "Gate",
                "positive checkpoint was rejected" if positive else "negative admitted",
            )

    for turn_id in positive_turns:
        expected = [
            unit.model_dump(mode="json")
            for unit in case.expected.stage1.semantic_units
            if unit.turn_id == turn_id
        ]
        props = (
            turn_map.get(turn_id, {})
            .get("diagnostic", {})
            .get("stage1", {})
            .get("propositions", [])
        )
        alignment = assign_unique(
            expected,
            props,
            lambda g, r: (
                evidence_matches([g["evidence_span"]], [r.get("evidence_span", "")])
                and g["semantic_role"] == r.get("semantic_role")
            ),
        )
        recall_alignment = assign_unique(
            expected,
            props,
            lambda g, r: evidence_matches([g["evidence_span"]], [r.get("evidence_span", "")]),
        )
        for index, gold in enumerate(expected):
            stage1_checks.append(
                dict(
                    turn_id=turn_id,
                    expected=gold,
                    proposition_found=index in recall_alignment,
                    passed=index in alignment,
                )
            )
            if index not in alignment:
                fail(turn_id, "Stage1", "semantic role or proposition missing")

    for turn_id in turn_map:
        turn = turn_map[turn_id]
        gold_rows = [row for row in claims.values() if row["source_turn_id"] == turn_id]
        observed = turn.get("extraction", {}).get("semantic_units", [])
        if not observed:
            observed = turn.get("extraction", {}).get("claims", [])
        matches = assign_unique(gold_rows, observed, lambda g, r: all(claim_checks(g, r).values()))
        # Separately bind historical DB identities; a failed Stage2 hint must
        # not be silently 'fixed' by counting saved rows as raw extraction.
        eligible_db = [row for row in turn.get("db_after", []) if _source_matches(row, turn)]
        bindings = assign_unique(
            gold_rows,
            eligible_db,
            lambda g, r: (
                g["semantic_type"] == "new_memory"
                and all(claim_checks(g, r, semantic_type=False).values())
            ),
        )
        for index, gold in enumerate(gold_rows):
            best = max(
                (claim_checks(gold, row) for row in observed),
                key=lambda result: sum(result.values()),
                default={},
            )
            claim_results.append(
                dict(
                    claim_id=gold["claim_id"],
                    turn_id=turn_id,
                    matched=index in matches,
                    observed_index=matches.get(index),
                    best_checks=best,
                )
            )
            if index in bindings:
                mapping[gold["claim_id"]] = eligible_db[bindings[index]]["id"]
            if index not in matches:
                fail(
                    turn_id,
                    "Extraction",
                    f"claim {gold['claim_id']} failed semantic/source contract",
                )

    operation_results = []
    for op in case.expected.sub_operations:
        turn = turn_map.get(op.turn_id, {})
        before = {row["id"]: row for row in turn.get("db_before", [])}
        after = {row["id"]: row for row in turn.get("db_after", [])}
        added = [row for key, row in after.items() if key not in before]
        target_id = mapping.get(op.target.ref or "")
        target = after.get(target_id or "")
        audits = turn.get("audits", [])
        relevant_audits = [
            audit for audit in audits if target_id in audit.get("target_memory_ids", [])
        ]
        plans = [batch["batch"] for batch in turn.get("write_batches", [])]
        proposed_targets = {
            entry.get("target_memory_id")
            for plan in plans
            for key in ("contextual_updates", "event_enrichments", "conflict_event_enrichments")
            for entry in plan.get(key, [])
        }
        compared_targets = {key for audit in audits for key in audit.get("target_memory_ids", [])}
        fields_ok = bool(target) and all(
            any(
                compact(value)
                in compact(json.dumps(_field(target or {}, field.path), ensure_ascii=False))
                for value in field.alternatives
            )
            for field in op.fields
        )
        target_ok = bool(target_id) and (
            target_id in proposed_targets or target_id in compared_targets
        )
        matched_outputs = [
            row
            for row in after.values()
            if row.get("status") in ACTIVE
            and any(
                all(claim_checks(claims[ref], row, semantic_type=False).values())
                for ref in op.claim_refs
            )
        ]
        checks: dict[str, bool] = {}
        if op.operation == "CREATE":
            checks["new_source_bound_outputs"] = all(
                mapping.get(ref) in {row["id"] for row in added} for ref in op.claim_refs
            )
            checks["outputs_active"] = all(
                after.get(mapping.get(ref, ""), {}).get("status") in ACTIVE for ref in op.claim_refs
            )
        elif op.operation == "PRESERVE":
            for ref in op.claim_refs:
                current = [
                    row
                    for row in after.values()
                    if row.get("status") in ACTIVE
                    and all(
                        claim_checks(claims[ref], row, evidence=False, semantic_type=False).values()
                    )
                ]
                checks[f"unique_current_{ref}"] = len(current) == 1
        elif op.operation == "NOOP":
            if op.forbidden_kind:
                checks["no_forbidden_output"] = not any(
                    row["kind"] == op.forbidden_kind and row.get("status") in ACTIVE
                    for row in after.values()
                )
            elif op.target.refs:
                checks["antecedents_bound"] = all(ref in mapping for ref in op.target.refs)
                checks["antecedents_unchanged"] = all(
                    _business_row(before.get(mapping.get(ref, ""), {}))
                    == _business_row(after.get(mapping.get(ref, ""), {}))
                    for ref in op.target.refs
                )
            else:
                checks["no_write"] = not added and all(
                    _business_row(row) == _business_row(after.get(key, {}))
                    for key, row in before.items()
                )
        elif op.operation == "ENRICH":
            checks = dict(
                target=target_ok,
                fields=fields_ok,
                target_retained=bool(target)
                and target.get("status") in ACTIVE
                and target_id in before,
            )
            checks["no_fragment_add"] = not any(
                any(
                    all(claim_checks(claims[ref], row, semantic_type=False).values())
                    for ref in op.claim_refs
                )
                for row in added
            )
            checks["provenance"] = any(
                a.get("source_message_id") == turn.get("source_message_id") and a.get("evidence")
                for a in relevant_audits
            )
        elif op.operation == "UPDATE":
            checks = dict(
                target=target_ok,
                old_closed=bool(target) and target.get("status") in CLOSED,
                new_current=bool(matched_outputs),
                linked=any(row.get("supersedes_id") == target_id for row in matched_outputs),
            )
        elif op.operation in {"REFINE", "MERGE"}:
            checks = dict(
                target=target_ok, target_retained=bool(target) and target.get("status") in ACTIVE
            )
            checks["no_duplicate"] = (
                not any(
                    all(
                        claim_checks(
                            claims[op.target.ref], row, evidence=False, semantic_type=False
                        ).values()
                    )
                    for row in added
                )
                if op.target.ref
                else False
            )
            if op.operation == "REFINE":
                checks["new_value_on_target"] = bool(target) and all(
                    all(
                        claim_checks(
                            claims[ref], target or {}, evidence=False, semantic_type=False
                        ).values()
                    )
                    for ref in op.claim_refs
                )
        elif op.operation == "PROJECT":
            desired_sources = {mapping.get(ref) for ref in op.target.refs}
            derived = []
            for row in after.values():
                payload = row.get("payload", {})
                dimensions = [
                    row.get("state_dimension"),
                    payload.get("metric"),
                    payload.get("dimension"),
                ]
                dimension_ok = not op.output_dimension or any(
                    compact(op.output_dimension) == compact(str(value).split(".")[-1])
                    for value in dimensions
                )
                value_ok = (
                    not op.output_value
                    or row.get("state_value", payload.get("value")) == op.output_value
                )
                sources = set(row.get("source_event_ids", [])) | set(
                    row.get("supporting_event_ids", [])
                )
                sources |= set(payload.get("source_event_ids", [])) | set(
                    payload.get("supporting_event_ids", [])
                )
                if (
                    row.get("kind") == op.output_kind
                    and row.get("status") in ACTIVE
                    and dimension_ok
                    and value_ok
                    and sources
                    and len(sources) >= op.minimum_evidence
                    and None not in desired_sources
                    and desired_sources <= sources
                ):
                    derived.append(row)
            checks["derived_state_with_evidence_links"] = bool(derived)
        passed = bool(checks) and all(checks.values()) and not turn.get("error")
        operation_results.append(
            dict(
                turn_id=op.turn_id,
                operation=op.operation,
                expected_target_ref=op.target.ref,
                target_memory_id=target_id,
                observed_target_ids=sorted(
                    key for key in proposed_targets | compared_targets if key
                ),
                checks=checks,
                passed=passed,
            )
        )
        if not passed:
            if op.target.ref and target_id is None:
                source_turn = claims[op.target.ref]["source_turn_id"]
                fail(source_turn, "Admission", "required historical claim did not enter the Store")
            stage = (
                "Enrichment"
                if op.operation == "ENRICH"
                else "Pattern"
                if op.operation == "PROJECT" and op.output_kind == "interaction_pattern"
                else "Lifecycle"
                if op.operation in {"UPDATE", "PROJECT"}
                else "Resolver"
                if op.operation in {"MERGE", "REFINE"}
                else "Store"
            )
            if op.operation == "ENRICH" and not target_ok:
                stage = "Resolver"
            # Rejections before a write are Admission/Normalization failures,
            # not failures of the Store that correctly executed the plan.
            governance = turn.get("normalized_candidates", [])
            if op.operation in {"CREATE", "PRESERVE"} and governance:
                if any(row.get("admission_decision") == "reject" for row in governance):
                    stage = "Admission"
                if any(
                    row.get("admission_reason") == "normalization_contract_invalid"
                    for row in governance
                ):
                    stage = "Normalization"
            fail(
                op.turn_id,
                stage,
                f"{op.operation}: " + ", ".join(key for key, ok in checks.items() if not ok),
            )

    stage_order = {
        name: index
        for index, name in enumerate(
            [
                "Gate",
                "Stage1",
                "Extraction",
                "Normalization",
                "Admission",
                "Retrieval",
                "Resolver",
                "Enrichment",
                "Lifecycle",
                "Pattern",
                "Store",
            ]
        )
    }
    failures.sort(key=lambda item: (item[0], stage_order.get(item[1], 99)))
    passed = not failures and all(op["passed"] for op in operation_results)
    return dict(
        passed=passed,
        classification="NEEDS_REVIEW" if case.review_reason else "PASS" if passed else "FAIL",
        review_reason=case.review_reason,
        primary_failure_stage=failures[0][1] if failures else None,
        primary_failure_reason=failures[0][2] if failures else None,
        secondary_failure_stages=list(dict.fromkeys(stage for _, stage, _ in failures[1:])),
        gate_checks=gate_checks,
        stage1_checks=stage1_checks,
        claim_checks=claim_results,
        operation_checks=operation_results,
        claim_memory_bindings=mapping,
    )


def ratio(hits: int, total: int) -> float | None:
    return round(hits / total, 4) if total else None


def percentile(values: list[float], fraction: float) -> float | None:
    return (
        round(sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)], 2) if values else None
    )


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    quality = [row["quality"] for row in rows]
    scored = [row for row in rows if not row.get("review_reason")]
    gates = [item for q in quality for item in q["gate_checks"]]
    units = [item for q in quality for item in q["stage1_checks"]]
    claims = [item for q in quality for item in q["claim_checks"]]
    ops = [item for q in quality for item in q["operation_checks"]]
    turns = [turn for row in rows for turn in row["turns"]]
    attempts = [
        attempt
        for turn in turns
        for run in turn.get("extraction_runs", [])
        for attempt in run.get("attempts", [])
    ]
    positive = [item for item in gates if item["expected"]]
    negative = [item for item in gates if not item["expected"]]
    extracted = [turn for turn in turns if turn.get("extractor_called")]
    stage2_turns = [
        turn
        for turn in extracted
        if bool(turn.get("diagnostic", {}).get("stage2", {}).get("called"))
    ]
    stage2_claims = [
        claim
        for claim in claims
        if any(turn.get("turn_id") == claim["turn_id"] for turn in stage2_turns)
    ]
    stage2_claim_ids = {claim["claim_id"] for claim in stage2_claims}
    stage2_claim_checks = [
        check for check in claims if check["claim_id"] in stage2_claim_ids
    ]
    fallback_reasons = Counter()
    for turn in extracted:
        fallback = turn.get("diagnostic", {}).get("fallback", {})
        if fallback.get("triggered"):
            fallback_reasons[str(fallback.get("reason_code") or "UNKNOWN_ERROR")] += 1
    metrics: dict[str, Any] = dict(
        completed_cases=len(rows),
        completed_user_turns=len(turns),
        passed_cases=sum(q["passed"] for q in quality),
        needs_review_cases=len(rows) - len(scored),
        adjudicated_pass_rate=ratio(sum(row["quality"]["passed"] for row in scored), len(scored)),
        gate_positive_checkpoints=len(positive),
        gate_negative_checkpoints=len(negative),
        gate_recall=ratio(sum(item["actual"] for item in positive), len(positive)),
        gate_specificity=ratio(sum(not item["actual"] for item in negative), len(negative)),
        gate_precision=ratio(
            sum(item["actual"] for item in positive), sum(item["actual"] for item in gates)
        ),
        stage1_checkpoints=len(units),
        proposition_recall=ratio(sum(item["proposition_found"] for item in units), len(units)),
        semantic_operation_accuracy=ratio(sum(item["passed"] for item in units), len(units)),
        expected_claim_count=len(claims),
        extraction_contract_recall=ratio(sum(item["matched"] for item in claims), len(claims)),
        checkpoint_operation_accuracy=ratio(sum(item["passed"] for item in ops), len(ops)),
        extractor_turn_count=len(extracted),
        model_attempt_count=len(attempts),
        model_attempt_failure_count=sum(item["status"] == "failed" for item in attempts),
        fallback_turn_count=sum(
            bool(turn.get("diagnostic", {}).get("fallback", {}).get("triggered"))
            for turn in extracted
        ),
        native_two_stage_turn_count=sum(
            turn.get("diagnostic", {}).get("final_extractor_used") == "two_stage_native"
            for turn in extracted
        ),
        native_two_stage_turn_rate=ratio(
            sum(
                turn.get("diagnostic", {}).get("final_extractor_used") == "two_stage_native"
                for turn in extracted
            ),
            len(extracted),
        ),
        stage2_called_turn_count=len(stage2_turns),
        stage2_parse_success_count=sum(
            bool(turn.get("diagnostic", {}).get("stage2", {}).get("parse_success"))
            for turn in stage2_turns
        ),
        stage2_validation_success_count=sum(
            bool(turn.get("diagnostic", {}).get("stage2", {}).get("validation_success"))
            for turn in stage2_turns
        ),
        stage2_parse_success_rate=ratio(
            sum(
                bool(turn.get("diagnostic", {}).get("stage2", {}).get("parse_success"))
                for turn in stage2_turns
            ),
            len(stage2_turns),
        ),
        stage2_validation_success_rate=ratio(
            sum(
                bool(turn.get("diagnostic", {}).get("stage2", {}).get("validation_success"))
                for turn in stage2_turns
            ),
            len(stage2_turns),
        ),
        stage2_contract_recall=ratio(
            sum(item["matched"] for item in stage2_claim_checks),
            len(stage2_claim_checks),
        ),
        fallback_by_reason=dict(fallback_reasons),
        trace_schema_error_count=fallback_reasons.get("TRACE_SCHEMA_ERROR", 0),
        model_stage2_schema_error_count=fallback_reasons.get("STAGE2_SCHEMA_ERROR", 0),
        model_latency_p50_ms=percentile([item["duration_ms"] for item in attempts], 0.5),
        model_latency_p95_ms=percentile([item["duration_ms"] for item in attempts], 0.95),
        prompt_tokens=sum(item.get("prompt_tokens") or 0 for item in attempts),
        completion_tokens=sum(item.get("completion_tokens") or 0 for item in attempts),
        total_tokens=sum(item.get("total_tokens") or 0 for item in attempts),
        primary_failures=dict(
            Counter(q["primary_failure_stage"] for q in quality if q["primary_failure_stage"])
        ),
    )
    metrics["fallback_turn_rate"] = ratio(metrics["fallback_turn_count"], len(extracted))
    metrics["model_usage_scope"] = (
        "persisted extraction attempts only; judge/verifier traces are separate"
    )
    for operation in sorted({item["operation"] for item in ops}):
        checks = [item for item in ops if item["operation"] == operation]
        metrics[f"{operation.lower()}_checkpoint_count"] = len(checks)
        metrics[f"{operation.lower()}_accuracy"] = ratio(
            sum(item["passed"] for item in checks), len(checks)
        )
    for metric, key in (
        ("enrichment_target_accuracy", "target"),
        ("enrichment_patch_accuracy", "fields"),
    ):
        checks = [item for item in ops if item["operation"] == "ENRICH"]
        metrics[metric] = ratio(sum(item["checks"].get(key, False) for item in checks), len(checks))
    metrics["category_counts"] = dict(Counter(row["category"] for row in rows))
    return metrics

"""Deterministic evaluation harness for long-tail relation shadow decisions."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

from loveapp.application.memory_gate import MemoryGate
from loveapp.application.memory_retrieval import HybridMemoryRetriever
from loveapp.application.memory_semantic_relations import LongTailRelationShadowEvaluator
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.memory import (
    AdmissionDecision,
    ClaimRelation,
    EvidenceExplicitness,
    MemoryCandidate,
    MemoryItem,
    MemoryPerspective,
    MemoryStatus,
    MessageRole,
    PredicateType,
    StoredMessage,
    TimeKind,
)
from loveapp.domain.memory_lifecycle import memory_role
from loveapp.domain.memory_semantic_relation import SemanticRelationProposal
from loveapp.ports.memory import SemanticRelationJudge

REPORT_VERSION = "memory-longtail-relations-v1"


class FixtureSemanticRelationJudge:
    """Replay versioned fixture proposals through the real local validator."""

    def __init__(self, proposals: dict[str, dict[str, Any]]) -> None:
        self._proposals = proposals
        self.calls: list[tuple[str, list[str]]] = []

    @classmethod
    def from_path(cls, path: Path) -> FixtureSemanticRelationJudge:
        cases = _load_cases(path.read_bytes())
        proposals: dict[str, dict[str, Any]] = {}
        for case in cases:
            proposal = case.get("proposal")
            if not isinstance(proposal, dict) or not proposal:
                raise ValueError(f"case {case['id']} requires a proposal object")
            proposals[case["id"]] = proposal
        return cls(proposals)

    async def propose_relation(
        self,
        *,
        incoming: MemoryCandidate,
        candidates: list[MemoryItem],
        trace: object | None = None,
    ) -> SemanticRelationProposal:
        del trace
        case_id = str(incoming.payload.get("eval_case_id") or "")
        if case_id not in self._proposals:
            raise ValueError(f"missing fixture proposal for case: {case_id}")
        self.calls.append((case_id, [item.id for item in candidates]))
        payload = dict(self._proposals[case_id])
        payload.setdefault("judge_model", "fixture-longtail-judge")
        payload.setdefault("prompt_tokens", 20)
        payload.setdefault("completion_tokens", 10)
        payload.setdefault("total_tokens", 30)
        payload.setdefault("latency_ms", 2.5)
        return SemanticRelationProposal.model_validate(payload)


async def evaluate_memory_longtail_relations(
    path: Path,
    *,
    judge: SemanticRelationJudge,
    retriever: HybridMemoryRetriever | None = None,
    case_id: str | None = None,
    candidate_limit: int = 5,
) -> dict[str, Any]:
    """Evaluate retrieval, proposal, and shadow validation without a Store."""

    raw = path.read_bytes()
    cases = _load_cases(raw)
    if case_id is not None:
        cases = [case for case in cases if case["id"] == case_id]
        if not cases:
            raise ValueError(f"unknown long-tail relation case: {case_id}")

    evaluator = LongTailRelationShadowEvaluator(
        judge,
        retriever=retriever,
        candidate_limit=candidate_limit,
    )
    rows = [
        await _evaluate_case(case, evaluator=evaluator, candidate_limit=candidate_limit)
        for case in cases
    ]
    metrics = _summarize(rows, candidate_limit=candidate_limit)
    return {
        "version": REPORT_VERSION,
        "dataset": str(path),
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "case_filter": case_id,
        "case_count": len(rows),
        "passed_case_count": sum(row["passed"] for row in rows),
        "failed_case_count": sum(not row["passed"] for row in rows),
        "candidate_limit": candidate_limit,
        "store_mutation_permitted": False,
        "metrics": metrics,
        "by_category": _summarize_groups(rows, "category", candidate_limit),
        "by_relation": _summarize_groups(rows, "expected_relation", candidate_limit),
        "cases": rows,
    }


def _load_cases(raw: bytes) -> list[dict[str, Any]]:
    cases = [
        json.loads(line)
        for line in raw.decode("utf-8-sig").splitlines()
        if line.strip()
    ]
    seen: set[str] = set()
    for case in cases:
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("every long-tail relation case requires a non-empty id")
        if case_id in seen:
            raise ValueError(f"duplicate long-tail relation case id: {case_id}")
        seen.add(case_id)
        if not isinstance(case.get("incoming"), dict):
            raise ValueError(f"case {case_id} requires an incoming object")
        if not isinstance(case.get("existing_memories"), list):
            raise ValueError(f"case {case_id} requires existing_memories")
        expected = case.get("expected")
        if not isinstance(expected, dict) or not expected.get("relation"):
            raise ValueError(f"case {case_id} requires expected.relation")
        ClaimRelation(expected["relation"])
    return cases


async def _evaluate_case(
    case: dict[str, Any],
    *,
    evaluator: LongTailRelationShadowEvaluator,
    candidate_limit: int,
) -> dict[str, Any]:
    case_id = case["id"]
    reference_time = _parse_datetime(case.get("reference_time"))
    user_id = str(case.get("user_id") or f"longtail-{case_id.casefold()}-user")
    relationship_id = str(case.get("relationship_id") or "partner")
    incoming = _candidate_from_fixture(case["incoming"], case_id=case_id)
    existing = [
        _memory_from_fixture(
            item,
            case_id=case_id,
            user_id=user_id,
            relationship_id=relationship_id,
            reference_time=reference_time,
        )
        for item in case["existing_memories"]
    ]
    incoming_status = MemoryStatus(case.get("incoming_status", MemoryStatus.CONFIRMED))
    incoming_source_message_id = str(
        case.get("incoming_source_message_id")
        or case.get("source_message_id")
        or f"{case_id}-incoming-source"
    )
    before = _snapshot(incoming, existing)
    gate = _evaluate_gate(case, incoming, existing)
    trace = ExecutionTrace()
    started = perf_counter()
    result = await evaluator.evaluate(
        incoming=incoming,
        existing_memories=existing,
        user_id=user_id,
        relationship_id=relationship_id,
        incoming_status=incoming_status,
        incoming_source_message_id=incoming_source_message_id,
        reference_time=reference_time,
        trace=trace,
    )
    duration_ms = (perf_counter() - started) * 1000
    after = _snapshot(incoming, existing)
    mutated = before != after

    expected = case["expected"]
    expected_relation = ClaimRelation(expected["relation"])
    expected_targets = set(expected.get("target_memory_ids", []))
    actual_targets = set(result.proposal.target_memory_ids)
    relevant_targets = set(
        expected.get("retrieval_relevant_memory_ids", expected_targets)
    )
    retrieved_ids = [candidate.memory_id for candidate in result.retrieved_candidates]
    retrieved_set = set(retrieved_ids)
    expected_supersedes = set(expected.get("would_supersede_memory_ids", []))
    actual_supersedes = set(result.validation.would_supersede_memory_ids)
    destructive_allowed = bool(
        expected.get(
            "destructive_update_allowed",
            expected_relation == ClaimRelation.UPDATE,
        )
    )
    protected_ids = set(expected.get("protected_memory_ids", []))
    if incoming_status != MemoryStatus.CONFIRMED:
        protected_ids.update(
            item.id for item in existing if item.status == MemoryStatus.CONFIRMED
        )
    judge_execution_succeeded = result.judge_status != "failed"

    checks = {
        "judge_execution": judge_execution_succeeded,
        "relation": (
            judge_execution_succeeded
            and result.proposal.relation == expected_relation
        ),
        "target_memory_ids": (
            judge_execution_succeeded and actual_targets == expected_targets
        ),
        "retrieval_recall_at_k": (
            relevant_targets <= retrieved_set if relevant_targets else True
        ),
        "validator_pass": result.validation.validator_pass
        == bool(expected.get("validator_pass", True)),
        "would_update": result.validation.would_update
        == bool(expected.get("would_update", False)),
        "would_supersede_memory_ids": actual_supersedes == expected_supersedes,
        "no_input_mutation": not mutated,
        "shadow_contract": (
            result.mode == "shadow" and result.store_mutation_permitted is False
        ),
    }
    if "validated_relation" in expected:
        checks["validated_relation"] = (
            result.validation.validated_relation
            == ClaimRelation(expected["validated_relation"])
        )
    false_update = (
        result.proposal.relation == ClaimRelation.UPDATE
        and expected_relation != ClaimRelation.UPDATE
    )
    destructive_target_mismatch = (
        result.validation.would_update and actual_supersedes != expected_supersedes
    )
    false_destructive_update = result.validation.would_update and (
        not destructive_allowed or destructive_target_mismatch
    )
    confirmed_overwrite_violation = bool(
        result.validation.would_update and protected_ids & actual_supersedes
    )
    target_index = {item.id: item for item in existing}
    proposed_targets = [
        target_index[memory_id]
        for memory_id in result.proposal.target_memory_ids
        if memory_id in target_index
    ]
    superseded_targets = [
        target_index[memory_id]
        for memory_id in actual_supersedes
        if memory_id in target_index
    ]
    event_over_pattern_violation = bool(
        result.validation.would_update
        and any(
            memory_role(incoming) != memory_role(target)
            for target in superseded_targets or proposed_targets
        )
    )
    weak_belief_overwrite_violation = bool(
        result.validation.would_update
        and incoming.perspective
        in {MemoryPerspective.USER_BELIEF, MemoryPerspective.MODEL_INFERRED}
        and any(target.status == MemoryStatus.CONFIRMED for target in superseded_targets)
    )
    attribution = _attribute_failures(
        gate=gate,
        expected=expected,
        checks=checks,
        result=result,
        relevant_targets=relevant_targets,
        retrieved_set=retrieved_set,
        event_over_pattern_violation=event_over_pattern_violation,
        weak_belief_overwrite_violation=weak_belief_overwrite_violation,
    )
    failures = [name for name, passed in checks.items() if not passed]
    trace_records = [record.model_dump(mode="json") for record in trace.snapshot()]
    return {
        "id": case_id,
        "category": case.get("category"),
        "description": case.get("description"),
        "tags": case.get("tags", []),
        "passed": not failures,
        "expected_relation": expected_relation.value,
        "expected_target_memory_ids": sorted(expected_targets),
        "retrieval_relevant_memory_ids": sorted(relevant_targets),
        "retrieved_memory_ids": retrieved_ids,
        "retrieved_candidates": [
            candidate.model_dump(mode="json")
            for candidate in result.retrieved_candidates
        ],
        "judge_status": result.judge_status,
        "judge_error_type": result.judge_error_type,
        "proposal": result.proposal.model_dump(mode="json"),
        "validation": result.validation.model_dump(mode="json"),
        "checks": checks,
        "failures": failures,
        "false_update": false_update,
        "false_destructive_update": false_destructive_update,
        "destructive_target_mismatch": destructive_target_mismatch,
        "confirmed_overwrite_violation": confirmed_overwrite_violation,
        "input_mutated": mutated,
        "duration_ms": round(duration_ms, 3),
        "trace": trace_records,
        "candidate_limit": candidate_limit,
        "gate": gate,
        "resolution_status": _resolution_status(result),
        "event_over_pattern_violation": event_over_pattern_violation,
        "weak_belief_overwrite_violation": weak_belief_overwrite_violation,
        "error_attribution": attribution,
    }


def _evaluate_gate(
    case: dict[str, Any],
    incoming: MemoryCandidate,
    existing: list[MemoryItem],
) -> dict[str, Any]:
    """Run the real Gate against optional fixture history without changing writes."""

    history_raw = case.get("conversation_history") or case.get("gate_history") or []
    history: list[StoredMessage] = []
    for index, raw in enumerate(history_raw):
        if isinstance(raw, str):
            content = raw
            raw = {}
        elif isinstance(raw, dict):
            content = str(raw.get("content") or raw.get("text") or "")
        else:
            continue
        if not content.strip():
            continue
        history.append(
            StoredMessage(
                id=str(raw.get("id") or f"{case['id']}-gate-history-{index}"),
                conversation_id=str(raw.get("conversation_id") or case["id"]),
                user_id=str(raw.get("user_id") or f"longtail-{case['id'].casefold()}-user"),
                relationship_id=str(raw.get("relationship_id") or "partner"),
                role=MessageRole(raw.get("role", MessageRole.USER)),
                content=content,
                created_at=_parse_datetime(raw.get("created_at")),
            )
        )
    decision = MemoryGate().evaluate(
        incoming.original_text,
        conversation_history=history,
        existing_memories=existing,
    )
    expected = _gate_expectation(case)
    return {
        "should_extract": decision.should_extract,
        "reason": decision.reason.value,
        "signals": list(decision.signals),
        "matched_rule": decision.matched_rule,
        "matched_span": decision.matched_span,
        "contextual_probe": decision.contextual_probe,
        "history_loaded": bool(history),
        "expected_should_extract": expected["should_extract"],
        "expected_signal": expected["signal"],
        "expectation_source": expected["source"],
        "gate_check": (
            decision.should_extract == expected["should_extract"]
            if expected["should_extract"] is not None
            else None
        ),
        "signal_check": (
            expected["signal"] in decision.signals
            if expected["signal"] is not None
            else None
        ),
    }


def _gate_expectation(case: dict[str, Any]) -> dict[str, Any]:
    expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
    gate = case.get("gate") if isinstance(case.get("gate"), dict) else {}
    nested = expected.get("gate") if isinstance(expected.get("gate"), dict) else {}
    source = (
        "fixture"
        if gate or nested or "gate_should_extract" in expected or "gate_signal" in expected
        else "derived"
    )
    should_extract = gate.get("should_extract", nested.get("should_extract"))
    if should_extract is None:
        should_extract = expected.get("gate_should_extract")
    if should_extract is None:
        should_extract = _derived_gate_expectation(case)
    signal = gate.get("signal", nested.get("signal"))
    if signal is None:
        signal = expected.get("gate_signal")
    if signal is None and _is_durable_reversal_text(
        str((case.get("incoming") or {}).get("summary") or "")
    ):
        signal = "durable_behavioral_reversal"
    return {
        "should_extract": should_extract if isinstance(should_extract, bool) else None,
        "signal": str(signal) if signal else None,
        "source": source,
    }


def _derived_gate_expectation(case: dict[str, Any]) -> bool | None:
    """Provide a transparent baseline default for the original fixture.

    Explicit fixture expectations take precedence.  The fallback only marks
    clear speculative/future concerns as negative and treats the remaining
    relationship/interaction fixtures as durable candidates.
    """

    summary = str((case.get("incoming") or {}).get("summary") or "")
    if not summary:
        return None
    return not re.search(r"担心|害怕|也许|可能|好像|似乎|会不会|不知道", summary)


def _is_durable_reversal_text(text: str) -> bool:
    return bool(
        re.search(r"最近|这段时间|这几天|这周|这两周|这几个月|一个月|之前|以前", text)
        and re.search(r"不再|很少|几乎不|明显|越来越|变少|变慢|下降|降低|回避|不愿意", text)
        and re.search(r"联系|聊天|交流|回复|见面|邀请|讨论|情绪|矛盾|冲突|边界|关系", text)
    )


def _resolution_status(result: Any) -> str:
    if result.judge_status == "failed":
        return "deterministic_fallback"
    if result.judge_status == "not_called":
        return "retrieval_no_candidate"
    if result.proposal.relation == ClaimRelation.UNCERTAIN:
        return "semantic_uncertain"
    if not result.validation.validator_pass:
        return "validator_denied"
    if result.proposal.relation == ClaimRelation.UPDATE:
        return "validator_allowed_shadow"
    return "semantic_relation_proposed"


def _attribute_failures(
    *,
    gate: dict[str, Any],
    expected: dict[str, Any],
    checks: dict[str, bool],
    result: Any,
    relevant_targets: set[str],
    retrieved_set: set[str],
    event_over_pattern_violation: bool,
    weak_belief_overwrite_violation: bool,
) -> list[str]:
    layers: list[str] = []
    if gate["gate_check"] is False or gate["signal_check"] is False:
        layers.append("Gate")
    if relevant_targets and not relevant_targets <= retrieved_set:
        layers.append("Retrieval")
    if result.judge_status == "failed" or not checks["relation"]:
        layers.append("Semantic Judge")
    if not checks["target_memory_ids"]:
        layers.append("Target")
    if result.validation.validator_pass != bool(expected.get("validator_pass", True)):
        layers.append("Validator")
    if event_over_pattern_violation or weak_belief_overwrite_violation:
        layers.append("Validator")
    return list(dict.fromkeys(layers)) or ["None"]


def _candidate_from_fixture(raw: dict[str, Any], *, case_id: str) -> MemoryCandidate:
    payload = dict(raw.get("payload") or {})
    payload.setdefault("eval_case_id", case_id)
    data = _candidate_data(raw, payload=payload)
    return MemoryCandidate.model_validate(data)


def _memory_from_fixture(
    raw: dict[str, Any],
    *,
    case_id: str,
    user_id: str,
    relationship_id: str,
    reference_time: datetime,
) -> MemoryItem:
    memory_id = str(raw.get("id") or "")
    if not memory_id:
        raise ValueError(f"case {case_id} contains a memory without id")
    payload = dict(raw.get("payload") or {})
    payload.setdefault("eval_case_id", case_id)
    data = {
        **_candidate_data(raw, payload=payload),
        "id": memory_id,
        "user_id": str(raw.get("user_id") or user_id),
        "relationship_id": str(raw.get("relationship_id") or relationship_id),
        "status": raw.get("status", MemoryStatus.CONFIRMED),
        "source_message_id": raw.get("source_message_id") or f"{memory_id}-source",
        "created_at": raw.get("created_at") or reference_time,
        "updated_at": raw.get("updated_at") or reference_time,
        "last_used_at": raw.get("last_used_at"),
        "last_seen_at": raw.get("last_seen_at"),
        "dedupe_key": raw.get("dedupe_key") or f"fixture:{memory_id}",
    }
    return MemoryItem.model_validate(data)


def _candidate_data(raw: dict[str, Any], *, payload: dict[str, Any]) -> dict[str, Any]:
    summary = str(raw.get("summary") or "").strip()
    if not summary:
        raise ValueError("long-tail fixture candidates require a summary")
    custom_predicate = str(raw.get("custom_predicate") or "long_tail_relation").strip()
    evidence = list(raw.get("evidence_spans") or [summary])
    time_kind = raw.get("time_kind")
    if time_kind is None:
        if raw.get("period_start") is not None or raw.get("period_end") is not None:
            time_kind = TimeKind.INTERVAL
        elif raw.get("occurred_at") is not None:
            time_kind = TimeKind.POINT
        else:
            time_kind = TimeKind.UNKNOWN
    return {
        "kind": raw["kind"],
        "subject": raw.get("subject", "partner"),
        "summary": summary,
        "original_text": raw.get("original_text", summary),
        "evidence_spans": evidence,
        "time_kind": time_kind,
        "occurred_at": raw.get("occurred_at"),
        "period_start": raw.get("period_start"),
        "period_end": raw.get("period_end"),
        "expires_at": raw.get("expires_at"),
        "importance": raw.get("importance", 3),
        "perspective": raw.get("perspective", MemoryPerspective.USER_REPORTED),
        "confidence": raw.get("confidence", 0.95),
        "payload": payload,
        "raw_predicate": raw.get("raw_predicate", custom_predicate),
        "predicate_type": PredicateType.CUSTOM,
        "canonical_predicate": None,
        "custom_predicate": custom_predicate,
        "state_dimension": None,
        "state_value": None,
        "explicitness": raw.get("explicitness", EvidenceExplicitness.EXPLICIT),
        "requires_inference": raw.get("requires_inference", False),
        "admission_score": raw.get("admission_score", 0.9),
        "admission_decision": raw.get(
            "admission_decision", AdmissionDecision.CONFIRM
        ),
    }


def _snapshot(
    incoming: MemoryCandidate,
    existing: list[MemoryItem],
) -> str:
    return json.dumps(
        {
            "incoming": incoming.model_dump(mode="json"),
            "existing_memories": [item.model_dump(mode="json") for item in existing],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _summarize(
    rows: list[dict[str, Any]],
    *,
    candidate_limit: int,
) -> dict[str, Any]:
    counters = Counter()
    latency: list[float] = []
    token_fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    tokens = Counter()
    for row in rows:
        expected_relation = row["expected_relation"]
        proposed_relation = row["proposal"]["relation"]
        relation_correct = row["checks"]["relation"]
        counters["case_count"] += 1
        counters[f"judge_{row['judge_status']}"] += 1
        counters["relation_correct"] += int(relation_correct)
        counters["target_correct"] += int(row["checks"]["target_memory_ids"])
        counters[f"{expected_relation}_expected"] += 1
        counters[f"{expected_relation}_correct"] += int(relation_correct)
        counters["update_predicted"] += int(proposed_relation == ClaimRelation.UPDATE)
        counters["update_true_positive"] += int(
            proposed_relation == ClaimRelation.UPDATE
            and expected_relation == ClaimRelation.UPDATE
        )
        counters["uncertain_predicted"] += int(
            proposed_relation == ClaimRelation.UNCERTAIN
        )
        relevant = row["retrieval_relevant_memory_ids"]
        if relevant:
            counters["retrieval_expected"] += 1
            retrieved_ids = row["retrieved_memory_ids"]
            for k in (1, 3, 5):
                counters[f"retrieval_hit_at_{k}"] += int(
                    set(relevant) <= set(retrieved_ids[:k])
                )
            counters["retrieval_hit"] += int(row["checks"]["retrieval_recall_at_k"])
        counters["candidate_count"] += len(row["retrieved_memory_ids"])
        counters["false_update"] += int(row["false_update"])
        counters["false_destructive_update"] += int(
            row["false_destructive_update"]
        )
        counters["confirmed_overwrite_violation"] += int(
            row["confirmed_overwrite_violation"]
        )
        counters["input_mutation"] += int(row["input_mutated"])
        gate = row.get("gate") or {}
        gate_expected = gate.get("expected_should_extract")
        if isinstance(gate_expected, bool):
            counters[
                "gate_expected_positive" if gate_expected else "gate_expected_negative"
            ] += 1
            counters["gate_true_positive"] += int(
                gate_expected and gate.get("should_extract") is True
            )
            counters["gate_false_negative"] += int(
                gate_expected and gate.get("should_extract") is False
            )
            counters["gate_correct"] += int(gate.get("gate_check") is True)
        gate_signal = gate.get("expected_signal")
        if gate_signal == "durable_behavioral_reversal":
            counters["durable_reversal_expected"] += 1
            counters["durable_reversal_true_positive"] += int(
                gate.get("signal_check") is True
            )
        for layer in row.get("error_attribution", []):
            counters[f"attribution_{layer}"] += 1
        counters["event_over_pattern_violation"] += int(
            row.get("event_over_pattern_violation", False)
        )
        counters["weak_belief_overwrite_violation"] += int(
            row.get("weak_belief_overwrite_violation", False)
        )
        if expected_relation in {
            ClaimRelation.SAME,
            ClaimRelation.UPDATE,
            ClaimRelation.CONTRADICTION,
        }:
            predicted_targets = set(row["proposal"].get("target_memory_ids", []))
            expected_target_ids = set(row["expected_target_memory_ids"])
            counters["target_predicted_count"] += len(predicted_targets)
            counters["target_correct_count"] += len(
                predicted_targets & expected_target_ids
            )
            counters["target_expected_count"] += len(expected_target_ids)
        proposal_latency = row["proposal"].get("latency_ms")
        if isinstance(proposal_latency, int | float):
            latency.append(float(proposal_latency))
        for field in token_fields:
            value = row["proposal"].get(field)
            if isinstance(value, int):
                tokens[field] += value

    total = counters["case_count"]
    metrics: dict[str, Any] = {
        "relation_accuracy": _ratio(counters["relation_correct"], total),
        "target_memory_accuracy": _ratio(counters["target_correct"], total),
        "update_precision": _ratio(
            counters["update_true_positive"], counters["update_predicted"]
        ),
        "uncertain_rate": _ratio(counters["uncertain_predicted"], total),
        "long_tail_gate_expected_positive_count": counters["gate_expected_positive"],
        "long_tail_gate_true_positive_count": counters["gate_true_positive"],
        "long_tail_gate_false_negative_count": counters["gate_false_negative"],
        "long_tail_gate_recall": _ratio(
            counters["gate_true_positive"], counters["gate_expected_positive"]
        ),
        "long_tail_gate_expected_negative_count": counters["gate_expected_negative"],
        "long_tail_gate_accuracy": _ratio(
            counters["gate_correct"],
            counters["gate_expected_positive"] + counters["gate_expected_negative"],
        ),
        "durable_reversal_gate_expected_count": counters["durable_reversal_expected"],
        "durable_reversal_gate_true_positive_count": counters[
            "durable_reversal_true_positive"
        ],
        "durable_reversal_gate_recall": _ratio(
            counters["durable_reversal_true_positive"],
            counters["durable_reversal_expected"],
        ),
        "candidate_retrieval_expected_count": counters["retrieval_expected"],
        "candidate_retrieval_hit_at_1": _ratio(
            counters["retrieval_hit_at_1"], counters["retrieval_expected"]
        ),
        "candidate_retrieval_hit_at_3": _ratio(
            counters["retrieval_hit_at_3"], counters["retrieval_expected"]
        ),
        "candidate_retrieval_hit_at_5": _ratio(
            counters["retrieval_hit_at_5"], counters["retrieval_expected"]
        ),
        f"candidate_retrieval_recall_at_{candidate_limit}": _ratio(
            counters["retrieval_hit"], counters["retrieval_expected"]
        ),
        "avg_candidate_count": (
            round(counters["candidate_count"] / total, 4) if total else 0.0
        ),
        "false_update_count": counters["false_update"],
        "false_update_rate": _ratio(counters["false_update"], total),
        "false_destructive_update_count": counters["false_destructive_update"],
        "false_destructive_update_rate": _ratio(
            counters["false_destructive_update"], total
        ),
        "confirmed_overwrite_violation_count": counters[
            "confirmed_overwrite_violation"
        ],
        "event_over_pattern_violation_count": counters[
            "event_over_pattern_violation"
        ],
        "weak_belief_overwrite_violation_count": counters[
            "weak_belief_overwrite_violation"
        ],
        "target_memory_precision": _ratio(
            counters["target_correct_count"], counters["target_predicted_count"]
        ),
        "target_memory_predicted_count": counters["target_predicted_count"],
        "target_memory_expected_count": counters["target_expected_count"],
        "error_attribution": {
            key.removeprefix("attribution_"): value
            for key, value in sorted(counters.items())
            if key.startswith("attribution_")
        },
        "input_mutation_count": counters["input_mutation"],
        "semantic_judge_call_count": (
            counters["judge_completed"] + counters["judge_failed"]
        ),
        "semantic_judge_failure_count": counters["judge_failed"],
        "semantic_judge_not_called_count": counters["judge_not_called"],
        "semantic_judge_mean_latency_ms": (
            round(mean(latency), 3) if latency else 0.0
        ),
        "semantic_judge_p50_latency_ms": _percentile(latency, 0.5),
        "semantic_judge_p95_latency_ms": _percentile(latency, 0.95),
        "semantic_judge_token_usage": dict(tokens),
    }
    for relation in ClaimRelation:
        metrics[f"{relation.value}_accuracy"] = _ratio(
            counters[f"{relation.value}_correct"],
            counters[f"{relation.value}_expected"],
        )
    return metrics


def _summarize_groups(
    rows: list[dict[str, Any]],
    field: str,
    candidate_limit: int,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(field) or "unknown")].append(row)
    return {
        key: {
            "case_count": len(values),
            "relation_accuracy": _summarize(
                values, candidate_limit=candidate_limit
            )["relation_accuracy"],
            "target_memory_accuracy": _summarize(
                values, candidate_limit=candidate_limit
            )["target_memory_accuracy"],
            "false_destructive_update_count": sum(
                row["false_destructive_update"] for row in values
            ),
        }
        for key, values in sorted(grouped.items())
    }


def _parse_datetime(value: str | None) -> datetime:
    if value is None:
        return datetime(2026, 8, 29, 12, tzinfo=UTC)
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(round((len(ordered) - 1) * quantile), len(ordered) - 1)
    return round(ordered[index], 3)


def render_longtail_baseline_report(report: dict[str, Any]) -> str:
    """Render the layered shadow baseline as a reviewable Markdown artifact."""

    metrics = report.get("metrics") or {}
    lines = [
        "# Memory Long-tail Baseline",
        "",
        f"- Dataset: `{report.get('dataset', '-')}`",
        f"- Dataset SHA-256: `{report.get('dataset_sha256', '-')}`",
        f"- Cases: {report.get('case_count', 0)}",
        f"- Passed relation-governance cases: {report.get('passed_case_count', 0)}",
        f"- Store mutation permitted: `{report.get('store_mutation_permitted', False)}`",
        "",
        "## Dataset",
        "",
        "| Category | Cases | Relation accuracy |",
        "|---|---:|---:|",
    ]
    for category, values in sorted((report.get("by_category") or {}).items()):
        lines.append(
            f"| {category} | {values.get('case_count', 0)} | "
            f"{values.get('relation_accuracy', 0.0)} |"
        )
    lines.extend(
        [
            "",
        "## Layered Metrics",
        "",
        "| Layer | Metric | Value |",
        "|---|---|---:|",
        ]
    )
    metric_groups = {
        "Gate": (
            "long_tail_gate_expected_positive_count",
            "long_tail_gate_true_positive_count",
            "long_tail_gate_false_negative_count",
            "long_tail_gate_recall",
            "durable_reversal_gate_expected_count",
            "durable_reversal_gate_true_positive_count",
            "durable_reversal_gate_recall",
        ),
        "Retrieval": (
            "candidate_retrieval_expected_count",
            "candidate_retrieval_hit_at_1",
            "candidate_retrieval_hit_at_3",
            "candidate_retrieval_hit_at_5",
            "candidate_retrieval_recall_at_5",
            "avg_candidate_count",
        ),
        "Relation": (
            "relation_accuracy",
            "same_accuracy",
            "update_accuracy",
            "contradiction_accuracy",
            "complementary_accuracy",
            "unrelated_accuracy",
            "uncertain_accuracy",
        ),
        "Target": (
            "target_memory_accuracy",
            "target_memory_precision",
            "target_memory_predicted_count",
            "target_memory_expected_count",
        ),
        "Validator safety": (
            "false_destructive_update_count",
            "false_destructive_update_rate",
            "confirmed_overwrite_violation_count",
            "event_over_pattern_violation_count",
            "weak_belief_overwrite_violation_count",
        ),
        "Judge": (
            "semantic_judge_call_count",
            "semantic_judge_failure_count",
            "semantic_judge_mean_latency_ms",
            "semantic_judge_p50_latency_ms",
            "semantic_judge_p95_latency_ms",
            "semantic_judge_token_usage",
        ),
    }
    for group, names in metric_groups.items():
        for name in names:
            if name in metrics:
                value = metrics[name]
                if isinstance(value, dict):
                    value = json.dumps(value, ensure_ascii=False, sort_keys=True)
                lines.append(f"| {group} | `{name}` | {value} |")

    lines.extend(
        [
            "",
            "## Error Attribution",
            "",
            "| Layer | Count |",
            "|---|---:|",
        ]
    )
    attribution = metrics.get("error_attribution") or {}
    for layer, count in attribution.items():
        lines.append(f"| {layer} | {count} |")

    lines.extend(["", "## Representative Failures", ""])
    failures = [row for row in report.get("cases", []) if not row.get("passed")]
    if not failures:
        lines.append("No relation-governance failures recorded.")
    else:
        lines.extend(
            [
                "| Case | Expected | Actual | Resolution status | Attribution |",
                "|---|---|---|---|---|",
            ]
        )
        for row in failures[:12]:
            lines.append(
                "| {id} | {expected} | {actual} | {status} | {attribution} |".format(
                    id=row.get("id", "-"),
                    expected=row.get("expected_relation", "-"),
                    actual=(row.get("proposal") or {}).get("relation", "-"),
                    status=row.get("resolution_status", "-"),
                    attribution=", ".join(row.get("error_attribution", [])),
                )
            )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This is a read-only shadow baseline. Semantic proposals never commit to the Store.",
            "The next recommended single-layer change is Gate coverage when durable-reversal "
            "recall is below target; otherwise attribute failures to retrieval, Judge, or "
            "Validator using the table above.",
            "",
            "Known scope limits: no Judge prompt/threshold changes, Retriever reweighting, "
            "Validator threshold changes, lifecycle commit, ontology expansion, or "
            "multi-target mutation is included in this baseline.",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "REPORT_VERSION",
    "FixtureSemanticRelationJudge",
    "evaluate_memory_longtail_relations",
    "render_longtail_baseline_report",
]

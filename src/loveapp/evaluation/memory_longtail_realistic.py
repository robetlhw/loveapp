"""Scenario-level, read-only long-tail Memory evaluation.

The production Memory write path is intentionally not used here.  Each turn
uses a reviewed, scripted extraction and semantic proposal so that the
evaluation measures the existing Gate, retrieval, semantic-judge adapter and
shadow Validator independently.  Virtual memories are kept only in this
process to provide realistic multi-turn context; no Store operation is
allowed.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from loveapp.application.memory_gate import MemoryGate
from loveapp.application.memory_retrieval import HybridMemoryRetriever
from loveapp.application.memory_semantic_relations import LongTailRelationShadowEvaluator
from loveapp.domain.memory import (
    AdmissionDecision,
    ClaimRelation,
    EvidenceExplicitness,
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
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

REPORT_VERSION = "memory-longtail-realistic-v1"
_RELATIONS = tuple(ClaimRelation)
_TARGET_METRIC_RELATIONS = frozenset(
    relation for relation in _RELATIONS if relation != ClaimRelation.UNRELATED
)


class ScenarioFixtureJudge:
    """Return reviewed proposals keyed by ``scenario_id/turn_id/claim_id``."""

    def __init__(self, proposals: dict[str, dict[str, Any]]) -> None:
        self._proposals = proposals
        self.calls: list[dict[str, Any]] = []

    async def propose_relation(
        self,
        *,
        incoming: MemoryCandidate,
        candidates: list[MemoryItem],
        trace: object | None = None,
    ) -> SemanticRelationProposal:
        del trace
        key = str(incoming.payload.get("eval_turn_key") or "")
        self.calls.append(
            {
                "key": key,
                "candidate_ids": [candidate.id for candidate in candidates],
            }
        )
        proposal = self._proposals.get(key)
        if proposal is None:
            return SemanticRelationProposal(
                relation=ClaimRelation.UNCERTAIN,
                target_memory_ids=[],
                same_semantic_dimension=False,
                confidence=0,
                reason="No reviewed proposal was supplied for this turn.",
                judge_model="scenario-fixture-judge",
            )
        payload = dict(proposal)
        payload.setdefault("judge_model", "scenario-fixture-judge")
        payload.setdefault("prompt_tokens", 0)
        payload.setdefault("completion_tokens", 0)
        payload.setdefault("total_tokens", 0)
        payload.setdefault("latency_ms", 0)
        return SemanticRelationProposal.model_validate(payload)


async def evaluate_memory_longtail_realistic(
    path: Path,
    *,
    case_id: str | None = None,
    category: str | None = None,
    candidate_limit: int = 5,
    repeat: int = 1,
    retriever: HybridMemoryRetriever | None = None,
    judge: SemanticRelationJudge | None = None,
) -> dict[str, Any]:
    """Evaluate reviewed realistic scenarios in shadow mode.

    ``repeat`` is useful for a live judge supplied by callers.  Fixture mode
    is deterministic and still accepts it so the artifact shape is stable.
    """

    if repeat < 1 or repeat > 100:
        raise ValueError("repeat must be between 1 and 100")
    raw = path.read_bytes()
    cases = _load_cases(raw)
    if case_id is not None:
        cases = [case for case in cases if case["id"] == case_id]
        if not cases:
            raise ValueError(f"unknown long-tail realistic case: {case_id}")
    if category is not None:
        cases = [case for case in cases if case.get("category") == category]
        if not cases:
            raise ValueError(f"unknown long-tail realistic category: {category}")

    all_rows: list[dict[str, Any]] = []
    run_reports: list[dict[str, Any]] = []
    for run_index in range(1, repeat + 1):
        fixture_judge = judge or ScenarioFixtureJudge(_proposal_index(cases))
        evaluator = LongTailRelationShadowEvaluator(
            fixture_judge,
            retriever=retriever,
            candidate_limit=candidate_limit,
        )
        rows = [
            await _evaluate_case(
                case,
                evaluator=evaluator,
                run_index=run_index,
                candidate_limit=candidate_limit,
            )
            for case in cases
        ]
        all_rows.extend(rows)
        run_reports.append(
            {
                "run": run_index,
                "metrics": _summarize(rows, candidate_limit=candidate_limit),
                "cases": rows,
            }
        )

    metrics = _summarize(all_rows, candidate_limit=candidate_limit)
    return {
        "version": REPORT_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": str(path),
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "case_filter": case_id,
        "category_filter": category,
        "repeat": repeat,
        "case_count": len(cases),
        "scenario_count": len(cases),
        "turn_count": sum(len(case["turns"]) for case in cases),
        "evaluated_row_count": len(all_rows),
        "passed_case_count": sum(row["passed"] for row in all_rows),
        "failed_case_count": sum(not row["passed"] for row in all_rows),
        "candidate_limit": candidate_limit,
        "evaluation_mode": "shadow_fixture" if judge is None else "shadow_custom_judge",
        "store_mutation_permitted": False,
        "methodology": (
            "production_gate_retrieval_and_shadow_validator_with_reviewed_fixture_"
            "extraction_and_relation_proposals"
        ),
        "metrics": metrics,
        "by_category": _summarize_groups(all_rows, "category", candidate_limit),
        "runs": run_reports,
        "cases": all_rows,
    }


def _load_cases(raw: bytes) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(raw.decode("utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"long-tail realistic line {line_number} is not valid JSON") from exc
        _validate_case(case, line_number)
        if case["id"] in seen:
            raise ValueError(f"duplicate long-tail realistic case id: {case['id']}")
        seen.add(case["id"])
        cases.append(case)
    if not cases:
        raise ValueError("long-tail realistic dataset is empty")
    return cases


def _validate_case(case: Any, line_number: int) -> None:
    if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"]:
        raise ValueError(f"long-tail realistic line {line_number} requires a non-empty id")
    if not isinstance(case.get("turns"), list) or not case["turns"]:
        raise ValueError(f"long-tail realistic case {case['id']} requires turns")
    for turn_index, turn in enumerate(case["turns"], start=1):
        if not isinstance(turn, dict) or not isinstance(turn.get("text"), str):
            raise ValueError(f"case {case['id']} turn {turn_index} requires text")
        claims = turn.get("claims", [])
        if not isinstance(claims, list):
            raise ValueError(f"case {case['id']} turn {turn_index} claims must be a list")
        expected = turn.get("expected", {})
        if not isinstance(expected, dict):
            raise ValueError(f"case {case['id']} turn {turn_index} expected must be an object")
        if "relation" in expected:
            ClaimRelation(expected["relation"])
        for claim in claims:
            if not isinstance(claim, dict):
                raise ValueError(f"case {case['id']} turn {turn_index} has an invalid claim")
            if not claim.get("id") or not claim.get("summary"):
                raise ValueError(f"case {case['id']} claims require id and summary")


async def _evaluate_case(
    case: dict[str, Any],
    *,
    evaluator: LongTailRelationShadowEvaluator,
    run_index: int,
    candidate_limit: int,
) -> dict[str, Any]:
    reference_time = _parse_datetime(case.get("reference_time"))
    user_id = str(case.get("user_id") or f"longtail-realistic-{case['id'].casefold()}")
    relationship_id = str(case.get("relationship_id") or "partner")
    conversation_id = str(case.get("conversation_id") or f"lt-realistic-{case['id']}")
    existing: list[MemoryItem] = []
    history: list[StoredMessage] = []
    turn_rows: list[dict[str, Any]] = []

    for turn_index, turn in enumerate(case["turns"], start=1):
        turn_id = str(turn.get("turn_id") or f"t{turn_index}")
        turn_key = f"{case['id']}/{turn_id}"
        gate_decision = MemoryGate().evaluate(
            turn["text"],
            conversation_history=history,
            existing_memories=existing,
        )
        expected = dict(turn.get("expected") or {})
        claims = [
            _candidate_from_claim(
                claim,
                case_id=case["id"],
                turn_key=turn_key,
                reference_time=reference_time,
            )
            for claim in turn.get("claims", [])
        ]
        claim_rows: list[dict[str, Any]] = []
        for claim_index, incoming in enumerate(claims):
            claim_expected = dict(
                (turn.get("claim_expectations") or {}).get(
                    str(incoming.payload.get("eval_claim_id")),
                    expected,
                )
            )
            before_ids = [item.id for item in existing]
            result = await evaluator.evaluate(
                incoming=incoming,
                existing_memories=existing,
                user_id=user_id,
                relationship_id=relationship_id,
                incoming_status=MemoryStatus(
                    claim_expected.get(
                        "incoming_status", incoming.payload.get("eval_status", "confirmed")
                    )
                ),
                incoming_source_message_id=str(
                    incoming.payload.get("source_message_id")
                    or f"{case['id']}-{turn_id}-{claim_index}"
                ),
                reference_time=reference_time,
            )
            row = _claim_row(
                case,
                turn,
                turn_id,
                incoming,
                result,
                claim_expected,
                gate_decision,
                before_ids,
                candidate_limit,
            )
            claim_rows.append(row)

            # Virtual context only: an accepted fixture claim is made visible
            # to later turns, while shadow relation/lifecycle decisions never
            # mutate or supersede an existing item.
            if gate_decision.should_extract and claim_expected.get("virtual_memory", True):
                existing.append(
                    _memory_from_candidate(
                        incoming,
                        memory_id=f"{case['id']}-{turn_id}-{claim_index + 1}",
                        user_id=user_id,
                        relationship_id=relationship_id,
                        status=MemoryStatus(claim_expected.get("virtual_status", "confirmed")),
                        reference_time=reference_time,
                    )
                )

        history.append(
            StoredMessage(
                id=f"{conversation_id}-user-{turn_index}",
                conversation_id=conversation_id,
                user_id=user_id,
                relationship_id=relationship_id,
                role=MessageRole.USER,
                content=turn["text"],
                created_at=reference_time,
            )
        )
        turn_rows.append(
            {
                "turn_id": turn_id,
                "input": turn["text"],
                "context": [message.content for message in history[:-1]],
                "expected": expected,
                "gate": _gate_summary(gate_decision, expected),
                "extraction": {
                    "mode": "reviewed_fixture",
                    "input": turn["text"],
                    "raw_claims": [claim.model_dump(mode="json") for claim in claims],
                    "claim_count": len(claims),
                },
                "claims": [claim.model_dump(mode="json") for claim in claims],
                "claim_results": claim_rows,
                "virtual_memory_ids_after": [item.id for item in existing],
                "trace": [
                    result_trace
                    for claim_row in claim_rows
                    for result_trace in claim_row.get("trace", [])
                ],
            }
        )

    passed = all(
        turn_row["gate"]["check"] is not False
        and all(claim_row["passed"] for claim_row in turn_row["claim_results"])
        for turn_row in turn_rows
    )
    return {
        "id": case["id"],
        "run": run_index,
        "category": case.get("category", "unknown"),
        "description": case.get("description"),
        "turn_count": len(turn_rows),
        "passed": passed,
        "turns": turn_rows,
        "final_virtual_memory_ids": [item.id for item in existing],
    }


def _claim_row(
    case: dict[str, Any],
    turn: dict[str, Any],
    turn_id: str,
    incoming: MemoryCandidate,
    result: Any,
    expected: dict[str, Any],
    gate: Any,
    before_ids: list[str],
    candidate_limit: int,
) -> dict[str, Any]:
    expected_relation = ClaimRelation(expected["relation"]) if expected.get("relation") else None
    actual_relation = result.proposal.relation
    expected_targets = set(expected.get("target_memory_ids", []))
    actual_targets = set(result.proposal.target_memory_ids)
    relevant = set(expected.get("retrieval_relevant_memory_ids", expected_targets))
    # A target that was not present in the virtual context cannot be judged as
    # a retrieval miss: the preceding turn was blocked upstream (usually by
    # Gate).  Keep the semantic expectation intact, but make retrieval recall
    # conditional on an actually available target.
    retrieval_relevant = relevant & set(before_ids)
    retrieved_ids = [candidate.memory_id for candidate in result.retrieved_candidates]
    expected_gate = expected.get("gate_should_extract")
    checks: dict[str, bool] = {
        "gate": (
            gate.should_extract == expected_gate
            if isinstance(expected_gate, bool)
            else True
        ),
        "relation": expected_relation is None or actual_relation == expected_relation,
        "target_memory_ids": expected_relation is None or actual_targets == expected_targets,
        "retrieval_recall_at_5": not retrieval_relevant
        or retrieval_relevant <= set(retrieved_ids[:5]),
        "validator": result.validation.validator_pass == bool(expected.get("validator_pass", True)),
        "shadow_only": result.store_mutation_permitted is False,
    }
    destructive_allowed = bool(
        expected.get("destructive_update_allowed", expected_relation == ClaimRelation.UPDATE)
    )
    supersedes = set(result.validation.would_supersede_memory_ids)
    expected_supersedes = set(expected.get("would_supersede_memory_ids", []))
    false_destructive = bool(
        result.validation.would_update
        and (not destructive_allowed or supersedes != expected_supersedes)
    )
    target_index = {candidate.id: candidate for candidate in _memory_candidates_for_targets(result)}
    protected_ids = {
        memory_id
        for memory_id in expected.get("protected_memory_ids", [])
        if memory_id in target_index
    }
    confirmed_overwrite = bool(result.validation.would_update and protected_ids & supersedes)
    superseded_targets = [
        target_index[item_id] for item_id in supersedes if item_id in target_index
    ]
    event_over_pattern = bool(
        result.validation.would_update
        and any(memory_role(incoming) != memory_role(target) for target in superseded_targets)
    )
    weak_belief_overwrite = bool(
        result.validation.would_update
        and incoming.perspective
        in {MemoryPerspective.USER_BELIEF, MemoryPerspective.MODEL_INFERRED}
        and any(target.status == MemoryStatus.CONFIRMED for target in superseded_targets)
    )
    attribution = _attribute(
        expected=expected,
        checks=checks,
        result=result,
        relevant=retrieval_relevant,
        retrieved=set(retrieved_ids),
        false_destructive=false_destructive,
        confirmed_overwrite=confirmed_overwrite,
        event_over_pattern=event_over_pattern,
        weak_belief_overwrite=weak_belief_overwrite,
    )
    failures = [name for name, passed in checks.items() if not passed]
    if false_destructive:
        failures.append("false_destructive_update")
    if confirmed_overwrite:
        failures.append("confirmed_overwrite_violation")
    return {
        "id": f"{case['id']}/{turn_id}/{incoming.payload.get('eval_claim_id')}",
        "expected": expected,
        "actual": {
            "relation": actual_relation.value,
            "target_memory_ids": sorted(actual_targets),
            "retrieved_memory_ids": retrieved_ids,
            "retrieved_candidates": [
                candidate.model_dump(mode="json") for candidate in result.retrieved_candidates
            ],
            "retrieval_relevant_memory_ids": sorted(retrieval_relevant),
            "validator": result.validation.model_dump(mode="json"),
            "resolution_status": _resolution_status(result),
        },
        "before_memory_ids": before_ids,
        "passed": not failures,
        "checks": checks,
        "failures": failures,
        "error_attribution": attribution,
        "false_destructive_update": false_destructive,
        "confirmed_overwrite_violation": confirmed_overwrite,
        "event_over_pattern_violation": event_over_pattern,
        "weak_belief_overwrite_violation": weak_belief_overwrite,
        "first_failing_stage": attribution[0] if attribution else None,
        "trace": [
            {
                "layer": "retrieval",
                "retrieved_memory_ids": retrieved_ids,
                "retrieved_candidates": [
                    candidate.model_dump(mode="json") for candidate in result.retrieved_candidates
                ],
                "candidate_limit": candidate_limit,
            },
            {
                "layer": "proposal",
                "status": result.judge_status,
                "proposal": result.proposal.model_dump(mode="json"),
            },
            {
                "layer": "validator",
                "validation": result.validation.model_dump(mode="json"),
            },
            {
                "layer": "shadow",
                "store_mutation_permitted": result.store_mutation_permitted,
                "would_update": result.validation.would_update,
                "would_supersede_memory_ids": result.validation.would_supersede_memory_ids,
            },
        ],
    }


def _attribute(
    *,
    expected: dict[str, Any],
    checks: dict[str, bool],
    result: Any,
    relevant: set[str],
    retrieved: set[str],
    false_destructive: bool,
    confirmed_overwrite: bool,
    event_over_pattern: bool,
    weak_belief_overwrite: bool,
) -> list[str]:
    layers: list[str] = []
    if checks["gate"] is False:
        return ["Gate"]
    if expected.get("claims_required") and not expected.get("claims_observed", True):
        layers.append("Extraction")
    if relevant and not relevant <= retrieved:
        layers.append("Retrieval")
    if result.judge_status in {"failed", "not_called"} or not checks["relation"]:
        layers.append("Semantic Judge")
    if not checks["target_memory_ids"]:
        layers.append("Target Selection")
    if (
        not checks["validator"]
        or false_destructive
        or confirmed_overwrite
        or event_over_pattern
        or weak_belief_overwrite
    ):
        layers.append("Validator")
    if not layers:
        return []
    return list(dict.fromkeys(layers))


def _gate_summary(decision: Any, expected: dict[str, Any]) -> dict[str, Any]:
    expected_value = expected.get("gate_should_extract")
    return {
        "should_extract": decision.should_extract,
        "reason": decision.reason.value,
        "signals": list(decision.signals),
        "matched_rule": decision.matched_rule,
        "matched_span": decision.matched_span,
        "contextual_probe": decision.contextual_probe,
        "history_derived": "contextual_history_derived" in decision.signals,
        "expected_should_extract": expected_value,
        "check": (
            decision.should_extract == expected_value if isinstance(expected_value, bool) else None
        ),
    }


def _candidate_from_claim(
    raw: dict[str, Any],
    *,
    case_id: str,
    turn_key: str,
    reference_time: datetime,
) -> MemoryCandidate:
    payload = dict(raw.get("payload") or {})
    payload["eval_case_id"] = case_id
    payload["eval_turn_key"] = turn_key
    payload["eval_claim_id"] = str(raw["id"])
    payload.setdefault("source_message_id", f"{case_id}-{turn_key}-source")
    return MemoryCandidate.model_validate(
        {
            "kind": raw.get("kind", MemoryKind.INTERACTION_PATTERN),
            "subject": raw.get("subject", "partner"),
            "summary": raw["summary"],
            "original_text": raw.get("original_text", raw["summary"]),
            "evidence_spans": raw.get("evidence_spans", [raw["summary"]]),
            "time_kind": raw.get("time_kind", TimeKind.INTERVAL),
            "occurred_at": raw.get("occurred_at"),
            "period_start": raw.get("period_start"),
            "period_end": raw.get("period_end"),
            "expires_at": raw.get("expires_at"),
            "importance": raw.get("importance", 3),
            "perspective": raw.get("perspective", MemoryPerspective.USER_REPORTED),
            "confidence": raw.get("confidence", 0.95),
            "payload": payload,
            "raw_predicate": raw.get(
                "raw_predicate", raw.get("custom_predicate", "long_tail_realistic")
            ),
            "predicate_type": PredicateType.CUSTOM,
            "custom_predicate": raw.get("custom_predicate", "long_tail_realistic"),
            "explicitness": raw.get("explicitness", EvidenceExplicitness.EXPLICIT),
            "requires_inference": raw.get("requires_inference", False),
            "admission_score": raw.get("admission_score", 0.95),
            "admission_decision": raw.get("admission_decision", AdmissionDecision.CONFIRM),
        }
    )


def _memory_from_candidate(
    candidate: MemoryCandidate,
    *,
    memory_id: str,
    user_id: str,
    relationship_id: str,
    status: MemoryStatus,
    reference_time: datetime,
) -> MemoryItem:
    data = candidate.model_dump()
    data.update(
        {
            "id": memory_id,
            "user_id": user_id,
            "relationship_id": relationship_id,
            "status": status,
            "source_message_id": candidate.payload.get("source_message_id")
            or f"{memory_id}-source",
            "created_at": reference_time,
            "updated_at": reference_time,
            "dedupe_key": f"eval:{memory_id}",
        }
    )
    return MemoryItem.model_validate(data)


def _memory_candidates_for_targets(result: Any) -> list[MemoryItem]:
    # Shadow result intentionally exposes only candidate summaries, but target
    # safety metrics need status/role.  Reconstruct the minimal immutable view.
    return [
        MemoryItem(
            id=item.memory_id,
            kind=item.kind,
            subject=item.subject,
            summary=item.summary,
            original_text=item.summary,
            evidence_spans=[item.summary],
            time_kind=TimeKind.UNKNOWN,
            status=item.status,
            user_id="",
            relationship_id="",
            source_message_id=None,
            dedupe_key=f"trace:{item.memory_id}",
            predicate_type=PredicateType.CUSTOM,
            custom_predicate="trace",
            raw_predicate="trace",
        )
        for item in result.retrieved_candidates
    ]


def _proposal_index(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    proposals: dict[str, dict[str, Any]] = {}
    for case in cases:
        for turn in case["turns"]:
            turn_id = str(turn.get("turn_id") or f"t{case['turns'].index(turn) + 1}")
            for claim in turn.get("claims", []):
                proposal = claim.get("proposal") or turn.get("proposal")
                if proposal:
                    proposals[f"{case['id']}/{turn_id}"] = proposal
    return proposals


def _resolution_status(result: Any) -> str:
    if result.judge_status == "failed":
        return "semantic_judge_failed_closed"
    if result.judge_status == "not_called":
        return "retrieval_no_candidate"
    if result.proposal.relation == ClaimRelation.UNCERTAIN:
        return "semantic_uncertain"
    if not result.validation.validator_pass:
        return "validator_denied"
    if result.proposal.relation == ClaimRelation.UPDATE:
        return "validator_allowed_shadow_update"
    return "semantic_relation_proposed"


def _summarize(rows: list[dict[str, Any]], *, candidate_limit: int) -> dict[str, Any]:
    counters = Counter()
    latencies: list[float] = []
    tokens = Counter()
    claim_rows = [claim for row in rows for turn in row["turns"] for claim in turn["claim_results"]]
    turn_rows = [turn for row in rows for turn in row["turns"]]
    for turn in turn_rows:
        gate = turn["gate"]
        expected_gate = gate.get("expected_should_extract")
        if isinstance(expected_gate, bool):
            counters[
                "gate_expected_positive_count" if expected_gate else "gate_expected_negative_count"
            ] += 1
            counters["gate_true_positive_count"] += int(expected_gate and gate["should_extract"])
            counters["gate_true_negative_count"] += int(
                not expected_gate and not gate["should_extract"]
            )
            counters["gate_false_negative_count"] += int(
                expected_gate and not gate["should_extract"]
            )
            counters["gate_false_positive_count"] += int(
                not expected_gate and gate["should_extract"]
            )
            counters["gate_correct_count"] += int(gate.get("check") is True)
        if "durable_reversal" in str(turn.get("expected", {}).get("signal", "")):
            counters["durable_reversal_expected_count"] += 1
            counters["durable_reversal_true_positive_count"] += int(gate["should_extract"])
    for claim in claim_rows:
        expected = claim["expected"]
        actual = claim["actual"]
        expected_relation = expected.get("relation")
        counters["claim_count"] += 1
        counters["passed_claim_count"] += int(claim["passed"])
        if expected_relation:
            counters["relation_expected_count"] += 1
            counters["relation_correct_count"] += int(actual["relation"] == expected_relation)
            counters[f"{expected_relation}_expected_count"] += 1
            counters[f"{expected_relation}_correct_count"] += int(
                actual["relation"] == expected_relation
            )
        relevant = set(
            actual.get(
                "retrieval_relevant_memory_ids",
                expected.get(
                    "retrieval_relevant_memory_ids", expected.get("target_memory_ids", [])
                ),
            )
        )
        retrieved = actual["retrieved_memory_ids"]
        if relevant:
            counters["retrieval_expected_count"] += 1
            for k in (1, 3, 5):
                counters[f"retrieval_hit_at_{k}_count"] += int(relevant <= set(retrieved[:k]))
            counters["retrieval_recall_at_5_count"] += int(relevant <= set(retrieved[:5]))
        expected_targets = set(expected.get("target_memory_ids", []))
        actual_targets = set(actual.get("target_memory_ids", []))
        if expected_relation in _TARGET_METRIC_RELATIONS:
            counters["target_expected_count"] += len(expected_targets)
            counters["target_predicted_count"] += len(actual_targets)
            counters["target_correct_count"] += len(expected_targets & actual_targets)
        counters["false_destructive_update_count"] += int(claim["false_destructive_update"])
        counters["confirmed_overwrite_violation_count"] += int(
            claim["confirmed_overwrite_violation"]
        )
        counters["event_over_pattern_violation_count"] += int(claim["event_over_pattern_violation"])
        counters["weak_belief_overwrite_violation_count"] += int(
            claim["weak_belief_overwrite_violation"]
        )
        proposal = next(
            (
                entry.get("proposal")
                for entry in claim.get("trace", [])
                if entry.get("layer") == "proposal"
            ),
            {},
        )
        if isinstance(proposal, dict):
            if isinstance(proposal.get("latency_ms"), (int, float)):
                latencies.append(float(proposal["latency_ms"]))
            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if isinstance(proposal.get(field), int):
                    tokens[field] += proposal[field]
    total_turns = len(turn_rows)
    total_claims = len(claim_rows)
    relation_total = counters["relation_expected_count"]
    retrieval_total = counters["retrieval_expected_count"]
    metrics: dict[str, Any] = {
        "scenario_count": len(rows),
        "turn_count": total_turns,
        "claim_count": total_claims,
        "gate_expected_positive_count": counters["gate_expected_positive_count"],
        "gate_expected_negative_count": counters["gate_expected_negative_count"],
        "gate_true_positive_count": counters["gate_true_positive_count"],
        "gate_false_negative_count": counters["gate_false_negative_count"],
        "gate_true_negative_count": counters["gate_true_negative_count"],
        "gate_false_positive_count": counters["gate_false_positive_count"],
        "gate_recall": _ratio(
            counters["gate_true_positive_count"], counters["gate_expected_positive_count"]
        ),
        "gate_precision": _ratio(
            counters["gate_true_positive_count"],
            counters["gate_true_positive_count"] + counters["gate_false_positive_count"],
        ),
        "gate_specificity": _ratio(
            counters["gate_true_negative_count"],
            counters["gate_true_negative_count"] + counters["gate_false_positive_count"],
        ),
        "durable_reversal_expected_count": counters["durable_reversal_expected_count"],
        "durable_reversal_true_positive_count": counters["durable_reversal_true_positive_count"],
        "durable_reversal_recall": _ratio(
            counters["durable_reversal_true_positive_count"],
            counters["durable_reversal_expected_count"],
        ),
        "retrieval_expected_count": retrieval_total,
        "retrieval_excludes_upstream_blocked_targets": True,
        "retrieval_hit_at_1": _ratio(counters["retrieval_hit_at_1_count"], retrieval_total),
        "retrieval_hit_at_3": _ratio(counters["retrieval_hit_at_3_count"], retrieval_total),
        "retrieval_hit_at_5": _ratio(counters["retrieval_hit_at_5_count"], retrieval_total),
        "retrieval_recall_at_5": _ratio(counters["retrieval_recall_at_5_count"], retrieval_total),
        "relation_accuracy": _ratio(counters["relation_correct_count"], relation_total),
        "target_memory_accuracy": _ratio(
            counters["target_correct_count"], counters["target_expected_count"]
        ),
        "target_memory_precision": _ratio(
            counters["target_correct_count"], counters["target_predicted_count"]
        ),
        "false_destructive_update_count": counters["false_destructive_update_count"],
        "false_destructive_update_rate": _ratio(
            counters["false_destructive_update_count"], total_claims
        ),
        "confirmed_overwrite_violation_count": counters["confirmed_overwrite_violation_count"],
        "event_over_pattern_violation_count": counters["event_over_pattern_violation_count"],
        "weak_belief_overwrite_violation_count": counters["weak_belief_overwrite_violation_count"],
        "passed_claim_count": counters["passed_claim_count"],
        "claim_pass_rate": _ratio(counters["passed_claim_count"], total_claims),
        "semantic_judge_call_count": sum(
            1
            for claim in claim_rows
            if any(
                entry.get("layer") == "proposal" and entry.get("status") == "completed"
                for entry in claim.get("trace", [])
            )
        ),
        "semantic_judge_failure_count": sum(
            1
            for claim in claim_rows
            if any(
                entry.get("layer") == "proposal" and entry.get("status") == "failed"
                for entry in claim.get("trace", [])
            )
        ),
        "semantic_judge_mean_latency_ms": round(mean(latencies), 3) if latencies else 0,
        "semantic_judge_p50_latency_ms": _percentile(latencies, 0.5),
        "semantic_judge_p95_latency_ms": _percentile(latencies, 0.95),
        "semantic_judge_token_usage": dict(tokens),
        "candidate_limit": candidate_limit,
    }
    for relation in _RELATIONS:
        metrics[f"{relation.value}_accuracy"] = _ratio(
            counters[f"{relation.value}_correct_count"],
            counters[f"{relation.value}_expected_count"],
        )
    metrics["error_attribution"] = dict(
        sorted(
            Counter(
                layer for claim in claim_rows for layer in claim.get("error_attribution", [])
            ).items()
        )
    )
    metrics["first_failing_stage"] = dict(
        sorted(
            Counter(
                claim.get("first_failing_stage")
                for claim in claim_rows
                if claim.get("first_failing_stage")
            ).items()
        )
    )
    return metrics


def _summarize_groups(
    rows: list[dict[str, Any]], field: str, candidate_limit: int
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(field) or "unknown")].append(row)
    return {
        key: {
            "scenario_count": len(values),
            "metrics": _summarize(values, candidate_limit=candidate_limit),
        }
        for key, values in sorted(grouped.items())
    }


def _parse_datetime(value: str | None) -> datetime:
    if value is None:
        return datetime(2026, 8, 29, 12, tzinfo=UTC)
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * quantile)))
    return round(ordered[index], 3)


def render_longtail_realistic_report(report: dict[str, Any]) -> str:
    metrics = report.get("metrics") or {}
    lines = [
        "# Memory Long-tail Realistic Evaluation",
        "",
        f"- Dataset: `{report.get('dataset', '-')}`",
        f"- Dataset SHA-256: `{report.get('dataset_sha256', '-')}`",
        f"- Scenarios: {report.get('scenario_count', 0)}",
        f"- Turns: {report.get('turn_count', 0)}",
        f"- Mode: `{report.get('evaluation_mode', '-')}`",
        f"- Store mutation permitted: `{report.get('store_mutation_permitted', False)}`",
        "",
        "## Metrics",
        "",
        "| Layer | Metric | Value |",
        "|---|---|---:|",
    ]
    groups = {
        "Gate": (
            "gate_expected_positive_count",
            "gate_expected_negative_count",
            "gate_true_positive_count",
            "gate_true_negative_count",
            "gate_false_negative_count",
            "gate_false_positive_count",
            "gate_recall",
            "gate_precision",
            "gate_specificity",
            "durable_reversal_recall",
        ),
        "Retrieval": (
            "retrieval_expected_count",
            "retrieval_hit_at_1",
            "retrieval_hit_at_3",
            "retrieval_hit_at_5",
            "retrieval_recall_at_5",
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
        "Target": ("target_memory_accuracy", "target_memory_precision"),
        "Safety": (
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
    for group, names in groups.items():
        for name in names:
            if name in metrics:
                value = metrics[name]
                if isinstance(value, dict):
                    value = json.dumps(value, ensure_ascii=False, sort_keys=True)
                lines.append(f"| {group} | `{name}` | {value} |")
    lines.extend(["", "## Error Attribution", "", "| Layer | Count |", "|---|---:|"])
    for layer, count in (metrics.get("error_attribution") or {}).items():
            lines.append(f"| {layer} | {count} |")
    lines.extend(["", "## First Failing Stage", "", "| Layer | Count |", "|---|---:|"])
    for layer, count in (metrics.get("first_failing_stage") or {}).items():
        lines.append(f"| {layer} | {count} |")
    lines.extend(["", "## Representative Failures", ""])
    failures = [row for row in report.get("cases", []) if not row.get("passed")]
    if not failures:
        lines.append("No failures recorded.")
    else:
        lines.extend(["| Case | Category | Attribution |", "|---|---|---|"])
        for row in failures[:20]:
            attribution = sorted(
                {
                    layer
                    for turn in row.get("turns", [])
                    for claim in turn.get("claim_results", [])
                    for layer in claim.get("error_attribution", [])
                }
            )
            lines.append(
                f"| {row.get('id', '-')} | {row.get('category', '-')} | "
                f"{', '.join(attribution) or '-'} |"
            )
    lines.extend(
        [
            "",
            "## Scope",
            "",
            "This evaluation uses reviewed scripted extraction/proposals and a virtual "
            "in-process context. It never commits destructive lifecycle changes to the Store.",
            "Failed scenarios retain layer attribution so Gate, Extraction, Retrieval, "
            "Semantic Judge, Target Selection and Validator gaps remain distinguishable.",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "REPORT_VERSION",
    "ScenarioFixtureJudge",
    "evaluate_memory_longtail_realistic",
    "render_longtail_realistic_report",
]

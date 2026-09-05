"""Scenario-level, read-only long-tail Memory evaluation.

Fixture mode uses reviewed extraction and relation proposals. Live mode runs
the production-shaped extractor and semantic relation judge. Both modes keep
virtual memories only in this process to provide realistic multi-turn context;
no Store operation or destructive lifecycle mutation is allowed.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

from loveapp.application.memory import atomize_candidates
from loveapp.application.memory_admission import (
    AdmissionAssessment,
    MemoryAdmissionPolicy,
    assess_governed_transition_eligibility,
    assess_memory_admission,
    build_admission_policies,
)
from loveapp.application.memory_gate import MemoryGate
from loveapp.application.memory_relations import has_local_conflict, resolve_claim_relation
from loveapp.application.memory_retrieval import HybridMemoryRetriever
from loveapp.application.memory_semantic_relations import LongTailRelationShadowEvaluator
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.memory import (
    AdmissionDecision,
    AtomicExtraction,
    ClaimRelation,
    EvidenceExplicitness,
    MemoryCandidate,
    MemoryExtractionAttempt,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    MessageRole,
    PredicateType,
    StoredMessage,
    TimeKind,
)
from loveapp.domain.memory_lifecycle import (
    memory_role,
    plan_memory_transitions,
)
from loveapp.domain.memory_normalization import normalize_memory_candidate_contract
from loveapp.domain.memory_predicates import PredicateNormalization, normalize_predicate
from loveapp.domain.memory_semantic_relation import SemanticRelationProposal
from loveapp.ports.memory import MemoryExtractor, SemanticRelationJudge

REPORT_VERSION = "memory-longtail-realistic-v3"
HARD_CASE_IDS = (
    "LT-R-004",
    "LT-P-002",
    "LT-C-002",
    "LT-C-003",
    "LT-U-002",
    "LT-A-001",
    "LT-M-001",
    "LT-H-001",
)
_RELATIONS = tuple(ClaimRelation)
_TARGET_METRIC_RELATIONS = frozenset(
    relation for relation in _RELATIONS if relation != ClaimRelation.UNRELATED
)
_SUBJECT_ALIASES = {
    "partner": "partner",
    "relationship_partner": "partner",
    "she": "partner",
    "he": "partner",
    "ta": "partner",
    "\\u5979": "partner",
    "\\u4ed6": "partner",
    "\\u5bf9\\u65b9": "partner",
    "\\u4f34\\u4fa3": "partner",
    "relationship": "relationship",
    "couple": "relationship",
    "we": "relationship",
    "\\u6211\\u4eec": "relationship",
    "\\u53cc\\u65b9": "relationship",
    "user": "user",
    "\\u6211": "user",
}
_CUSTOM_SEMANTIC_PAYLOAD_FIELDS = frozenset(
    {
        "activity_type",
        "direction",
        "frequency",
        "object",
        "topic",
    }
)
_CANONICAL_REPRESENTATION_FIELDS = frozenset(
    {
        "canonical_predicate",
        "predicate_type",
        "state_dimension",
        "state_value",
        "state_values",
    }
)
_CUSTOM_REPRESENTATION_FIELDS = frozenset(
    {
        "custom_predicate",
        "custom_predicates",
        "evidence_contains_any",
        "payload_constraints",
        "predicate_type",
    }
)


@dataclass(frozen=True)
class _LiveGovernance:
    policies: dict[MemoryKind, MemoryAdmissionPolicy]
    min_confidence: float
    tentative_min_confidence: float
    belief_min_confidence: float


@dataclass
class _LiveCandidate:
    source_claim_id: str
    candidate: MemoryCandidate | None
    normalization: PredicateNormalization | None
    assessment: AdmissionAssessment | None
    incoming_status: MemoryStatus | None
    eligible_for_virtual_context: bool
    outcome: str
    error: str | None = None
    expected_index: int | None = None
    expected_claim_id: str | None = None
    match_reason: str | None = None
    match_score: float | None = None
    memory_id: str | None = None


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
    hard_cases: bool = False,
    candidate_limit: int = 5,
    repeat: int = 1,
    retriever: HybridMemoryRetriever | None = None,
    judge: SemanticRelationJudge | None = None,
    extractor: MemoryExtractor | None = None,
    mode: str = "fixture",
    admission_policy_overrides: dict[str, dict[str, object]] | None = None,
    min_confidence: float = 0.65,
    tentative_min_confidence: float = 0.5,
    belief_min_confidence: float = 0.4,
) -> dict[str, Any]:
    """Evaluate reviewed realistic scenarios in shadow mode.

    ``repeat`` is useful for a live judge supplied by callers.  Fixture mode
    is deterministic and still accepts it so the artifact shape is stable.
    """

    if repeat < 1 or repeat > 100:
        raise ValueError("repeat must be between 1 and 100")
    if mode not in {"fixture", "live"}:
        raise ValueError("mode must be fixture or live")
    maximum_candidate_limit = 5 if mode == "live" else 10
    if candidate_limit < 1 or candidate_limit > maximum_candidate_limit:
        raise ValueError(
            f"candidate_limit must be between 1 and {maximum_candidate_limit} in {mode} mode"
        )
    if mode == "live" and extractor is None:
        raise ValueError("live mode requires a production MemoryExtractor")
    if mode == "live" and judge is None:
        raise ValueError("live mode requires a production SemanticRelationJudge")
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
    if hard_cases:
        cases = [case for case in cases if case["id"] in HARD_CASE_IDS]
        if not cases:
            raise ValueError("no requested long-tail hard cases are present in this dataset")

    governance = _LiveGovernance(
        policies=build_admission_policies(admission_policy_overrides),
        min_confidence=min_confidence,
        tentative_min_confidence=tentative_min_confidence,
        belief_min_confidence=belief_min_confidence,
    )

    all_rows: list[dict[str, Any]] = []
    run_reports: list[dict[str, Any]] = []
    for run_index in range(1, repeat + 1):
        fixture_judge = judge if judge is not None else ScenarioFixtureJudge(_proposal_index(cases))
        evaluator = LongTailRelationShadowEvaluator(
            fixture_judge,
            retriever=retriever,
            candidate_limit=candidate_limit,
        )
        rows = []
        for case in cases:
            if mode == "live":
                rows.append(
                    await _evaluate_live_case(
                        case,
                        evaluator=evaluator,
                        extractor=extractor,
                        governance=governance,
                        run_index=run_index,
                        candidate_limit=candidate_limit,
                    )
                )
            else:
                rows.append(
                    await _evaluate_case(
                        case,
                        evaluator=evaluator,
                        run_index=run_index,
                        candidate_limit=candidate_limit,
                    )
                )
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
        "hard_cases_only": hard_cases,
        "repeat": repeat,
        "case_count": len(cases),
        "scenario_count": len(cases),
        "turn_count": sum(len(case["turns"]) for case in cases),
        "evaluated_row_count": len(all_rows),
        "passed_case_count": sum(row["passed"] for row in all_rows),
        "failed_case_count": sum(not row["passed"] for row in all_rows),
        "candidate_limit": candidate_limit,
        "evaluation_mode": (
            "shadow_live"
            if mode == "live"
            else ("shadow_fixture" if judge is None else "shadow_custom_judge")
        ),
        "store_mutation_permitted": False,
        "methodology": (
            "production_gate_retrieval_and_shadow_validator_with_real_extraction_and_relation_judge"
            if mode == "live"
            else "production_gate_retrieval_and_shadow_validator_with_reviewed_fixture_"
            "extraction_and_relation_proposals"
        ),
        "metrics": metrics,
        "by_category": _summarize_groups(all_rows, "category", candidate_limit),
        "runs": run_reports,
        "cases": all_rows,
        "hard_case_consistency": _summarize_consistency(all_rows) if repeat > 1 else {},
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
            _validate_acceptable_representations(case["id"], claim)


def _validate_acceptable_representations(
    case_id: str,
    claim: dict[str, Any],
) -> None:
    representations = claim.get("acceptable_representations")
    if representations is None:
        return
    if not isinstance(claim.get("expected_semantic_concept"), str):
        raise ValueError(
            f"case {case_id} claim {claim.get('id')} requires expected_semantic_concept"
        )
    if not isinstance(representations, list) or not representations:
        raise ValueError(
            f"case {case_id} claim {claim.get('id')} has invalid acceptable_representations"
        )
    for representation in representations:
        if not isinstance(representation, dict):
            raise ValueError(
                f"case {case_id} claim {claim.get('id')} has an invalid representation"
            )
        predicate_type = representation.get("predicate_type")
        if predicate_type == PredicateType.CANONICAL.value:
            unknown_fields = set(representation) - _CANONICAL_REPRESENTATION_FIELDS
            if unknown_fields:
                raise ValueError(
                    f"case {case_id} claim {claim.get('id')} uses unsupported canonical "
                    f"representation field(s): {', '.join(sorted(unknown_fields))}"
                )
            if not isinstance(representation.get("canonical_predicate"), str):
                raise ValueError(
                    f"case {case_id} claim {claim.get('id')} has an invalid "
                    "canonical representation"
                )
            state_values = representation.get("state_values")
            if state_values is not None and (
                not isinstance(state_values, list)
                or not all(isinstance(value, str) for value in state_values)
            ):
                raise ValueError(
                    f"case {case_id} claim {claim.get('id')} has invalid canonical state_values"
                )
        elif predicate_type == PredicateType.CUSTOM.value:
            unknown_fields = set(representation) - _CUSTOM_REPRESENTATION_FIELDS
            if unknown_fields:
                raise ValueError(
                    f"case {case_id} claim {claim.get('id')} uses unsupported custom "
                    f"representation field(s): {', '.join(sorted(unknown_fields))}"
                )
            custom_values = representation.get("custom_predicates")
            custom_value = representation.get("custom_predicate")
            if not (
                isinstance(custom_value, str)
                or (
                    isinstance(custom_values, list)
                    and custom_values
                    and all(isinstance(value, str) for value in custom_values)
                )
            ):
                raise ValueError(
                    f"case {case_id} claim {claim.get('id')} has an invalid custom representation"
                )
            payload_constraints = representation.get("payload_constraints")
            if payload_constraints is not None:
                if not isinstance(payload_constraints, dict) or not payload_constraints:
                    raise ValueError(
                        f"case {case_id} claim {claim.get('id')} has invalid custom "
                        "payload_constraints"
                    )
                unknown_fields = set(payload_constraints) - _CUSTOM_SEMANTIC_PAYLOAD_FIELDS
                if unknown_fields:
                    raise ValueError(
                        f"case {case_id} claim {claim.get('id')} uses unsupported custom "
                        f"payload qualifier(s): {', '.join(sorted(unknown_fields))}"
                    )
                for field, values in payload_constraints.items():
                    if not _valid_semantic_qualifier_values(values):
                        raise ValueError(
                            f"case {case_id} claim {claim.get('id')} has invalid custom "
                            f"payload qualifier {field}"
                        )
            evidence_contains_any = representation.get("evidence_contains_any")
            if evidence_contains_any is not None and not _valid_semantic_qualifier_values(
                evidence_contains_any
            ):
                raise ValueError(
                    f"case {case_id} claim {claim.get('id')} has invalid "
                    "evidence_contains_any"
                )
        else:
            raise ValueError(f"case {case_id} claim {claim.get('id')} has unknown predicate_type")


def _valid_semantic_qualifier_values(values: object) -> bool:
    return bool(
        isinstance(values, list)
        and values
        and all(isinstance(value, str) and value.strip() for value in values)
    )


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
                        "incoming_status",
                        incoming.payload.get(
                            "eval_status",
                            (
                                "confirmed"
                                if incoming.admission_decision == AdmissionDecision.CONFIRM
                                else "proposed"
                            ),
                        ),
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
                        status=MemoryStatus(
                            claim_expected.get(
                                "virtual_status",
                                (
                                    "confirmed"
                                    if incoming.admission_decision == AdmissionDecision.CONFIRM
                                    else "proposed"
                                ),
                            )
                        ),
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
    primary, secondary = _case_failure_attribution(turn_rows) if not passed else (None, [])
    return {
        "id": case["id"],
        "run": run_index,
        "category": case.get("category", "unknown"),
        "description": case.get("description"),
        "turn_count": len(turn_rows),
        "passed": passed,
        "primary_failure_stage": primary,
        "secondary_failure_stages": secondary,
        "turns": turn_rows,
        "final_virtual_memory_ids": [item.id for item in existing],
    }


async def _evaluate_live_case(
    case: dict[str, Any],
    *,
    evaluator: LongTailRelationShadowEvaluator,
    extractor: MemoryExtractor | None,
    governance: _LiveGovernance,
    run_index: int,
    candidate_limit: int,
) -> dict[str, Any]:
    """Run one scenario through real extraction without a Store write path.

    The reviewed descriptors in the dataset are used only to evaluate a real
    model output. They are never injected as candidates or relation proposals.
    """

    if extractor is None:  # Defensive guard for direct callers.
        raise ValueError("live evaluation requires a production MemoryExtractor")

    reference_time = _parse_datetime(case.get("reference_time"))
    user_id = str(case.get("user_id") or f"longtail-realistic-{case['id'].casefold()}")
    relationship_id = str(case.get("relationship_id") or "partner")
    conversation_id = str(case.get("conversation_id") or f"lt-realistic-{case['id']}")
    existing: list[MemoryItem] = []
    history: list[StoredMessage] = []
    expected_memory_map: dict[str, str] = {}
    turn_rows: list[dict[str, Any]] = []

    for turn_index, turn in enumerate(case["turns"], start=1):
        turn_id = str(turn.get("turn_id") or f"t{turn_index}")
        turn_key = f"{case['id']}/{turn_id}"
        expected_turn = dict(turn.get("expected") or {})
        expected_claims = list(turn.get("claims") or [])
        gate_decision = MemoryGate().evaluate(
            turn["text"],
            conversation_history=history,
            existing_memories=existing,
        )
        extraction_trace = ExecutionTrace()
        attempts: list[MemoryExtractionAttempt] = []
        extraction_error: str | None = None
        extraction_mode = "live" if gate_decision.should_extract else "live_gate_skipped"
        extraction_started = perf_counter()
        extraction = AtomicExtraction()
        if gate_decision.should_extract:
            try:
                extraction = await extractor.extract(
                    turn["text"],
                    reference_time=reference_time,
                    existing_memories=list(existing),
                    conversation_history=list(history),
                    trace=extraction_trace,
                    attempt_callback=attempts.append,
                )
            except Exception as exc:
                extraction_error = f"{type(exc).__name__}: {exc}"[:500]
        extraction_latency_ms = (perf_counter() - extraction_started) * 1000

        prepared = _prepare_live_candidates(
            extraction,
            source_text=turn["text"],
            reference_time=reference_time,
            active_memories=existing,
            governance=governance,
            source_message_id=f"{case['id']}-{turn_id}-source",
            turn_key=turn_key,
        )
        matches, unmatched_expected = _match_expected_claims(expected_claims, prepared)
        for candidate_index, match in matches.items():
            live_candidate = prepared[candidate_index]
            live_candidate.expected_index = match["expected_index"]
            live_candidate.expected_claim_id = str(expected_claims[match["expected_index"]]["id"])
            live_candidate.match_reason = match["reason"]
            live_candidate.match_score = match["score"]
            if live_candidate.eligible_for_virtual_context:
                live_candidate.memory_id = _expected_virtual_memory_id(
                    case["id"],
                    turn_id,
                    match["expected_index"],
                )
            if live_candidate.candidate is not None:
                payload = dict(live_candidate.candidate.payload)
                payload["eval_claim_id"] = live_candidate.expected_claim_id
                live_candidate.candidate = live_candidate.candidate.model_copy(
                    update={"payload": payload}
                )
            if live_candidate.memory_id is not None:
                expected_memory_map[live_candidate.memory_id] = live_candidate.memory_id
        for candidate_index, live_candidate in enumerate(prepared, start=1):
            if live_candidate.eligible_for_virtual_context and live_candidate.memory_id is None:
                live_candidate.memory_id = f"{case['id']}-{turn_id}-live-{candidate_index}"

        claim_rows: list[dict[str, Any]] = []
        for candidate_index, live_candidate in enumerate(prepared):
            expected_raw = (
                expected_claims[live_candidate.expected_index]
                if live_candidate.expected_index is not None
                else None
            )
            expected = _claim_expectation(turn, expected_raw, expected_turn)
            expected_target_ids = set(expected.get("target_memory_ids", []))
            unresolved_expected_targets = sorted(
                memory_id
                for memory_id in expected_target_ids
                if memory_id not in expected_memory_map
                or expected_memory_map[memory_id] not in {item.id for item in existing}
            )
            resolved_expected_targets = {
                expected_memory_map[memory_id]
                for memory_id in expected_target_ids
                if memory_id in expected_memory_map
                and expected_memory_map[memory_id] in {item.id for item in existing}
            }
            expected_retrieval_ids = set(
                expected.get("retrieval_relevant_memory_ids", expected_target_ids)
            )
            unresolved_expected_retrieval_ids = sorted(
                memory_id
                for memory_id in expected_retrieval_ids
                if memory_id not in expected_memory_map
                or expected_memory_map[memory_id] not in {item.id for item in existing}
            )
            resolved_expected_retrieval_ids = {
                expected_memory_map[memory_id]
                for memory_id in expected_retrieval_ids
                if memory_id in expected_memory_map
                and expected_memory_map[memory_id] in {item.id for item in existing}
            }
            target_evaluation_applicable = not unresolved_expected_targets

            if live_candidate.candidate is None:
                claim_rows.append(
                    _failed_live_claim_row(
                        case=case,
                        turn_id=turn_id,
                        expected=expected,
                        live_candidate=live_candidate,
                        before_ids=[item.id for item in existing],
                        stage="Normalization",
                        reason=live_candidate.error or "Candidate normalization failed.",
                    )
                )
                continue
            if not live_candidate.eligible_for_virtual_context:
                claim_rows.append(
                    _failed_live_claim_row(
                        case=case,
                        turn_id=turn_id,
                        expected=expected,
                        live_candidate=live_candidate,
                        before_ids=[item.id for item in existing],
                        stage="Admission",
                        reason=live_candidate.error or live_candidate.outcome,
                    )
                )
                continue
            if live_candidate.candidate.predicate_type != PredicateType.CUSTOM:
                claim_rows.append(
                    _noncustom_live_claim_row(
                        case=case,
                        turn_id=turn_id,
                        expected=expected,
                        expected_claim=expected_raw,
                        live_candidate=live_candidate,
                        existing_memories=existing,
                        before_ids=[item.id for item in existing],
                        expected_target_memory_ids=resolved_expected_targets,
                        target_evaluation_applicable=target_evaluation_applicable,
                    )
                )
                existing.append(
                    _memory_from_candidate(
                        live_candidate.candidate,
                        memory_id=(
                            live_candidate.memory_id or f"{case['id']}-{turn_id}-{candidate_index}"
                        ),
                        user_id=user_id,
                        relationship_id=relationship_id,
                        status=live_candidate.incoming_status or MemoryStatus.PROPOSED,
                        reference_time=reference_time,
                    )
                )
                continue

            relation_trace = ExecutionTrace()
            before_ids = [item.id for item in existing]
            result = await evaluator.evaluate(
                incoming=live_candidate.candidate,
                existing_memories=existing,
                user_id=user_id,
                relationship_id=relationship_id,
                incoming_status=live_candidate.incoming_status or MemoryStatus.PROPOSED,
                incoming_source_message_id=str(
                    live_candidate.candidate.payload.get("source_message_id")
                    or f"{case['id']}-{turn_id}-{candidate_index}"
                ),
                reference_time=reference_time,
                trace=relation_trace,
                candidate_index=candidate_index,
            )
            row = _claim_row(
                case,
                turn,
                turn_id,
                live_candidate.candidate,
                result,
                expected,
                gate_decision,
                before_ids,
                candidate_limit,
                expected_target_memory_ids=resolved_expected_targets,
                expected_retrieval_memory_ids=resolved_expected_retrieval_ids,
                target_evaluation_applicable=target_evaluation_applicable,
                expected_claim=expected_raw,
            )
            row["live"] = _live_candidate_summary(
                live_candidate,
                unresolved_expected_targets=unresolved_expected_targets,
                unresolved_expected_retrieval_ids=unresolved_expected_retrieval_ids,
            )
            if expected_raw is not None:
                row["expected_claim"] = expected_raw
            row["trace"] = [
                *row["trace"],
                {
                    "layer": "live_governance",
                    "normalization": _normalization_summary(live_candidate.normalization),
                    "admission": _admission_summary(live_candidate.assessment),
                    "outcome": live_candidate.outcome,
                },
                *_safe_trace_snapshot(relation_trace),
            ]
            claim_rows.append(row)

            # This is the only virtual mutation. It models a normal accepted
            # ADD for later retrieval, never a relation/lifecycle mutation.
            # In particular, would_update never changes existing item status.
            item = _memory_from_candidate(
                live_candidate.candidate,
                memory_id=live_candidate.memory_id or f"{case['id']}-{turn_id}-{candidate_index}",
                user_id=user_id,
                relationship_id=relationship_id,
                status=live_candidate.incoming_status or MemoryStatus.PROPOSED,
                reference_time=reference_time,
            )
            existing.append(item)

        for expected_index in unmatched_expected:
            expected_raw = expected_claims[expected_index]
            expected = _claim_expectation(turn, expected_raw, expected_turn)
            stage = "Gate" if not gate_decision.should_extract else "Extraction"
            claim_rows.append(
                _missing_expected_claim_row(
                    case=case,
                    turn_id=turn_id,
                    expected_raw=expected_raw,
                    expected=expected,
                    before_ids=[item.id for item in existing],
                    stage=stage,
                    reason=(
                        "The gate did not admit this expected durable turn."
                        if stage == "Gate"
                        else "No live extracted candidate matched this reviewed expected claim."
                    ),
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
                "expected": expected_turn,
                "gate": _gate_summary(gate_decision, expected_turn),
                "extraction": {
                    "mode": extraction_mode,
                    "input": turn["text"],
                    "raw_claims": [claim.model_dump(mode="json") for claim in extraction.claims],
                    "claim_count": len(extraction.claims),
                    "discarded_spans": [
                        span.model_dump(mode="json") for span in extraction.discarded_spans
                    ],
                    "attempts": [_safe_attempt_summary(attempt) for attempt in attempts],
                    "error": extraction_error,
                    "latency_ms": round(extraction_latency_ms, 3),
                    "trace": _safe_trace_snapshot(extraction_trace),
                    "expected_claim_ids": [str(claim["id"]) for claim in expected_claims],
                    "matched_expected_claim_ids": [
                        str(expected_claims[item.expected_index]["id"])
                        for item in prepared
                        if item.expected_index is not None
                    ],
                    "unmatched_expected_claim_ids": [
                        str(expected_claims[index]["id"]) for index in unmatched_expected
                    ],
                    "match_records": [
                        _extraction_match_record(expected_claims[item.expected_index], item)
                        for item in prepared
                        if item.expected_index is not None
                    ],
                },
                "claims": [
                    item.candidate.model_dump(mode="json")
                    for item in prepared
                    if item.candidate is not None
                ],
                "normalized_candidates": [_live_candidate_summary(item) for item in prepared],
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
    primary, secondary = _case_failure_attribution(turn_rows) if not passed else (None, [])
    return {
        "id": case["id"],
        "run": run_index,
        "category": case.get("category", "unknown"),
        "description": case.get("description"),
        "turn_count": len(turn_rows),
        "passed": passed,
        "primary_failure_stage": primary,
        "secondary_failure_stages": secondary,
        "turns": turn_rows,
        "final_virtual_memory_ids": [item.id for item in existing],
        "expected_memory_id_map": expected_memory_map,
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
    expected_target_memory_ids: set[str] | None = None,
    expected_retrieval_memory_ids: set[str] | None = None,
    target_evaluation_applicable: bool = True,
    expected_claim: dict[str, Any] | None = None,
) -> dict[str, Any]:
    expected_relation = ClaimRelation(expected["relation"]) if expected.get("relation") else None
    actual_relation = result.proposal.relation
    expected_targets = (
        set(expected_target_memory_ids)
        if expected_target_memory_ids is not None
        else set(expected.get("target_memory_ids", []))
    )
    actual_targets = set(result.proposal.target_memory_ids)
    expected_retrieval = (
        set(expected_retrieval_memory_ids)
        if expected_retrieval_memory_ids is not None
        else set(expected.get("retrieval_relevant_memory_ids", expected_targets))
    )
    # A target that was not present in the virtual context cannot be judged as
    # a retrieval miss: the preceding turn was blocked upstream (usually by
    # Gate).  Keep the semantic expectation intact, but make retrieval recall
    # conditional on an actually available target.
    retrieval_relevant = expected_retrieval & set(before_ids)
    retrieved_ids = [candidate.memory_id for candidate in result.retrieved_candidates]
    expected_gate = expected.get("gate_should_extract")
    representation = (
        _semantic_representation_match(expected_claim, incoming)
        if expected_claim is not None
        else None
    )
    checks: dict[str, bool] = {
        "gate": (gate.should_extract == expected_gate if isinstance(expected_gate, bool) else True),
        "semantic_identity": bool(
            representation is None or representation["semantic_identity_match"]
        ),
        "relation": (
            expected_relation is None
            or not target_evaluation_applicable
            or actual_relation == expected_relation
        ),
        "target_memory_ids": (
            expected_relation is None
            or not target_evaluation_applicable
            or actual_targets == expected_targets
        ),
        "retrieval_recall_at_5": not retrieval_relevant
        or retrieval_relevant <= set(retrieved_ids[:5]),
        "validator": (
            "validator_pass" not in expected
            or result.validation.validator_pass == bool(expected["validator_pass"])
        ),
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
    if not checks["semantic_identity"]:
        attribution = list(dict.fromkeys(["Normalization", *attribution]))
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
            "expected_target_memory_ids_resolved": sorted(expected_targets),
            "target_evaluation_applicable": target_evaluation_applicable,
            "retrieved_memory_ids": retrieved_ids,
            "retrieved_candidates": [
                candidate.model_dump(mode="json") for candidate in result.retrieved_candidates
            ],
            "expected_retrieval_memory_ids_resolved": sorted(expected_retrieval),
            "retrieval_relevant_memory_ids": sorted(retrieval_relevant),
            "validator": result.validation.model_dump(mode="json"),
            "resolution_status": _resolution_status(result),
            "judge_status": result.judge_status,
            "judge_error_type": result.judge_error_type,
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
        "primary_failure_stage": attribution[0] if attribution else None,
        "secondary_failure_stages": attribution[1:],
        "trace": [
            *(
                [
                    {
                        "layer": "semantic_identity",
                        "semantic_representation": representation,
                    }
                ]
                if representation is not None
                else []
            ),
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
    retrieval_missing = bool(relevant and not relevant <= retrieved)
    judge_not_called_for_expected_relation = bool(
        expected.get("relation") and result.judge_status == "not_called" and not checks["relation"]
    )
    if retrieval_missing or judge_not_called_for_expected_relation:
        layers.append("Retrieval")
    judge_failed = result.judge_status == "failed"
    judge_relation_mismatch = result.judge_status == "completed" and not checks["relation"]
    if judge_failed or judge_relation_mismatch:
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


def _prepare_live_candidates(
    extraction: AtomicExtraction,
    *,
    source_text: str,
    reference_time: datetime,
    active_memories: list[MemoryItem],
    governance: _LiveGovernance,
    source_message_id: str,
    turn_key: str,
) -> list[_LiveCandidate]:
    """Apply the production candidate normalization/admission sequence.

    This intentionally mirrors the safe portion of ``MemoryService`` without
    constructing a service or calling a Store. Strong verification and write
    planning stay outside the evaluator's scope.
    """

    prepared: list[_LiveCandidate] = []
    raw_candidates = []
    for claim in extraction.claims:
        candidate = claim.to_candidate()
        payload = dict(candidate.payload)
        payload["eval_source_claim_id"] = claim.claim_id
        raw_candidates.append(candidate.model_copy(update={"payload": payload}))
    for source_index, raw_candidate in enumerate(atomize_candidates(raw_candidates), start=1):
        source_claim_id = str(
            raw_candidate.payload.get("eval_source_claim_id")
            or raw_candidate.payload.get("source_claim_id")
            or f"live-{source_index}"
        )
        predicate_normalization = normalize_predicate(
            kind=raw_candidate.kind,
            raw_predicate=raw_candidate.raw_predicate or raw_candidate.payload.get("predicate"),
            canonical_predicate=raw_candidate.canonical_predicate,
            custom_predicate=raw_candidate.custom_predicate,
            predicate_type=raw_candidate.predicate_type,
            payload=raw_candidate.payload,
        )
        try:
            candidate = raw_candidate.model_copy(update={"original_text": source_text})
            candidate = normalize_memory_candidate_contract(
                candidate,
                reference_time,
                allow_legacy_open_world=True,
            )
            payload = dict(candidate.payload)
            payload.update(
                {
                    "eval_turn_key": turn_key,
                    "eval_claim_id": source_claim_id,
                    "source_message_id": source_message_id,
                }
            )
            candidate = candidate.model_copy(update={"payload": payload})
        except Exception as exc:
            prepared.append(
                _LiveCandidate(
                    source_claim_id=source_claim_id,
                    candidate=None,
                    normalization=predicate_normalization,
                    assessment=None,
                    incoming_status=None,
                    eligible_for_virtual_context=False,
                    outcome="normalization_failed",
                    error=f"{type(exc).__name__}: {exc}"[:500],
                )
            )
            continue

        confidence_floor = _confidence_floor(candidate, governance)
        if candidate.confidence < confidence_floor:
            prepared.append(
                _LiveCandidate(
                    source_claim_id=source_claim_id,
                    candidate=candidate,
                    normalization=predicate_normalization,
                    assessment=None,
                    incoming_status=None,
                    eligible_for_virtual_context=False,
                    outcome="below_service_confidence_floor",
                    error=(
                        f"candidate confidence {candidate.confidence:.2f} is below "
                        f"the production service floor {confidence_floor:.2f}"
                    ),
                )
            )
            continue

        conflict = has_local_conflict(candidate, active_memories)
        governed_transition_eligibility = assess_governed_transition_eligibility(
            candidate,
            source_text,
            active_memories,
        )
        assessment = assess_memory_admission(
            candidate,
            source_text,
            conflict=conflict,
            policies=governance.policies,
            governed_transition_eligibility=governed_transition_eligibility,
        )
        if assessment.decision == AdmissionDecision.REJECT:
            prepared.append(
                _LiveCandidate(
                    source_claim_id=source_claim_id,
                    candidate=candidate.model_copy(
                        update={
                            "admission_score": assessment.score,
                            "admission_decision": assessment.decision,
                        }
                    ),
                    normalization=predicate_normalization,
                    assessment=assessment,
                    incoming_status=None,
                    eligible_for_virtual_context=False,
                    outcome="admission_rejected",
                    error=assessment.reason,
                )
            )
            continue

        candidate = _apply_admission_ttl(candidate, governance.policies, reference_time)
        incoming_status = (
            MemoryStatus.CONFIRMED
            if assessment.decision == AdmissionDecision.CONFIRM
            else MemoryStatus.PROPOSED
        )
        prepared.append(
            _LiveCandidate(
                source_claim_id=source_claim_id,
                candidate=candidate.model_copy(
                    update={
                        "admission_score": assessment.score,
                        "admission_decision": assessment.decision,
                    }
                ),
                normalization=predicate_normalization,
                assessment=assessment,
                incoming_status=incoming_status,
                eligible_for_virtual_context=True,
                outcome="admitted",
            )
        )
    return prepared


def _confidence_floor(candidate: MemoryCandidate, governance: _LiveGovernance) -> float:
    source_type = candidate.payload.get("source_type")
    is_hearsay = isinstance(source_type, str) and source_type.casefold() in {
        "hearsay",
        "third_party_report",
    }
    if candidate.perspective == MemoryPerspective.USER_BELIEF:
        return governance.belief_min_confidence
    if is_hearsay:
        return governance.tentative_min_confidence
    return governance.min_confidence


def _apply_admission_ttl(
    candidate: MemoryCandidate,
    policies: dict[MemoryKind, MemoryAdmissionPolicy],
    reference_time: datetime,
) -> MemoryCandidate:
    policy = policies[candidate.kind]
    if candidate.expires_at is not None or policy.default_ttl_days is None:
        return candidate
    return candidate.model_copy(
        update={"expires_at": reference_time + timedelta(days=policy.default_ttl_days)}
    )


def _match_expected_claims(
    expected_claims: list[dict[str, Any]],
    live_candidates: list[_LiveCandidate],
) -> tuple[dict[int, dict[str, Any]], list[int]]:
    """Map reviewed descriptors to live claims without relying on output order."""

    scored: list[tuple[float, int, int, str]] = []
    for expected_index, expected in enumerate(expected_claims):
        for candidate_index, live_candidate in enumerate(live_candidates):
            candidate = live_candidate.candidate
            if candidate is None:
                continue
            score, reason = _expected_claim_match_score(expected, candidate)
            if score > 0:
                scored.append((score, expected_index, candidate_index, reason))
    scored.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))
    matches: dict[int, dict[str, Any]] = {}
    used_expected: set[int] = set()
    used_candidates: set[int] = set()
    for score, expected_index, candidate_index, reason in scored:
        if expected_index in used_expected or candidate_index in used_candidates:
            continue
        if _ambiguous_expected_match(
            scored,
            score=score,
            expected_index=expected_index,
            candidate_index=candidate_index,
            used_expected=used_expected,
            used_candidates=used_candidates,
        ):
            continue
        matches[candidate_index] = {
            "expected_index": expected_index,
            "score": round(score, 4),
            "reason": reason,
        }
        used_expected.add(expected_index)
        used_candidates.add(candidate_index)
    return matches, [index for index in range(len(expected_claims)) if index not in used_expected]


def _expected_claim_match_score(
    expected: dict[str, Any], candidate: MemoryCandidate
) -> tuple[float, str]:
    expected_kind = str(expected.get("kind") or MemoryKind.INTERACTION_PATTERN.value)
    kind_match = candidate.kind.value == expected_kind
    expected_subject = str(expected.get("subject") or "partner")
    subject_match = _evaluation_subject_compatible(
        expected_subject,
        candidate.subject,
        candidate.kind,
    )
    if not subject_match:
        return 0.0, "subject_mismatch"
    representation = _semantic_representation_match(expected, candidate)
    if representation["semantic_identity_match"]:
        return (
            (1.0, "kind_subject_semantic_identity")
            if kind_match
            else (0.88, "subject_semantic_identity_kind_mismatch")
        )
    expected_predicate = _expected_predicate(expected)
    actual_predicates = {
        value.casefold().strip()
        for value in (
            candidate.canonical_predicate,
            candidate.custom_predicate,
            candidate.raw_predicate,
            candidate.payload.get("predicate"),
        )
        if isinstance(value, str) and value.strip()
    }
    predicate_match = bool(
        expected_predicate and expected_predicate.casefold() in actual_predicates
    )
    expected_summary = str(expected.get("summary") or "")
    summary_similarity = _character_jaccard(expected_summary, candidate.summary)
    evidence_similarity = max(
        (
            _character_jaccard(expected_summary, value)
            for value in (candidate.original_text, *candidate.evidence_spans)
        ),
        default=0.0,
    )
    if predicate_match:
        return (
            (1.0, "kind_subject_predicate")
            if kind_match
            else (0.88, "subject_predicate_kind_mismatch")
        )
    if kind_match and summary_similarity >= 0.42:
        return 0.7 + min(summary_similarity, 1.0) * 0.1, "kind_subject_summary"
    if kind_match and summary_similarity >= 0.3 and evidence_similarity >= 0.55:
        score = 0.66 + summary_similarity * 0.08 + evidence_similarity * 0.04
        return score, "kind_subject_summary_evidence"
    if not kind_match and summary_similarity >= 0.82:
        return 0.68 + min(summary_similarity, 1.0) * 0.05, "subject_summary_kind_mismatch"
    return 0.0, "insufficient_semantic_overlap"


def _ambiguous_expected_match(
    scored: list[tuple[float, int, int, str]],
    *,
    score: float,
    expected_index: int,
    candidate_index: int,
    used_expected: set[int],
    used_candidates: set[int],
) -> bool:
    competing = [
        other_score
        for other_score, other_expected, other_candidate, _ in scored
        if other_score >= score - 0.02
        and (
            (other_expected == expected_index and other_candidate not in used_candidates)
            or (other_candidate == candidate_index and other_expected not in used_expected)
        )
        and (other_expected, other_candidate) != (expected_index, candidate_index)
    ]
    return bool(competing)


def _character_jaccard(left: str, right: str) -> float:
    left_chars = {character for character in left.casefold() if character.isalnum()}
    right_chars = {character for character in right.casefold() if character.isalnum()}
    if not left_chars or not right_chars:
        return 0.0
    return len(left_chars & right_chars) / len(left_chars | right_chars)


def _expected_predicate(expected: dict[str, Any]) -> str:
    for field in ("canonical_predicate", "custom_predicate", "raw_predicate", "predicate"):
        value = expected.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    payload = expected.get("payload")
    if isinstance(payload, dict):
        value = payload.get("predicate")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _subject_key(value: str) -> str:
    """Normalize the public subject aliases used by semantic evaluation."""

    normalized = value.casefold().strip()
    return _SUBJECT_ALIASES.get(normalized, normalized)


def _evaluation_subject_compatible(
    expected: str,
    actual: str,
    kind: MemoryKind,
) -> bool:
    expected_key = _subject_key(expected)
    actual_key = _subject_key(actual)
    if expected_key == actual_key:
        return True
    return kind == MemoryKind.INTERACTION_PATTERN and {expected_key, actual_key} <= {
        "partner",
        "relationship",
    }


def _expected_virtual_memory_id(case_id: str, turn_id: str, expected_index: int) -> str:
    return f"{case_id}-{turn_id}-{expected_index + 1}"


def _claim_expectation(
    turn: dict[str, Any],
    expected_claim: dict[str, Any] | None,
    expected_turn: dict[str, Any],
) -> dict[str, Any]:
    if expected_claim is None:
        return {}
    claim_expectations = turn.get("claim_expectations") or {}
    claim_id = str(expected_claim.get("id") or "")
    specific = claim_expectations.get(claim_id)
    return dict(specific if isinstance(specific, dict) else expected_turn)


def _normalization_summary(
    value: PredicateNormalization | None,
) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "raw_predicate": value.raw_predicate,
        "predicate_type": value.predicate_type,
        "canonical_predicate": value.canonical_predicate,
        "custom_predicate": value.custom_predicate,
        "state_dimension": value.state_dimension,
        "state_value": value.state_value,
        "alias_hit": value.alias_hit,
    }


def _admission_summary(value: AdmissionAssessment | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "decision": value.decision.value,
        "score": value.score,
        "reason": value.reason,
        "score_breakdown": value.score_breakdown,
    }


def _live_candidate_summary(
    live_candidate: _LiveCandidate,
    *,
    unresolved_expected_targets: list[str] | None = None,
    unresolved_expected_retrieval_ids: list[str] | None = None,
) -> dict[str, Any]:
    candidate = live_candidate.candidate
    return {
        "source_claim_id": live_candidate.source_claim_id,
        "memory_id": live_candidate.memory_id,
        "matched_expected_claim_id": live_candidate.expected_claim_id,
        "match_reason": live_candidate.match_reason,
        "match_score": live_candidate.match_score,
        "normalization": _normalization_summary(live_candidate.normalization),
        "predicate_type": candidate.predicate_type.value if candidate is not None else None,
        "raw_predicate": candidate.raw_predicate if candidate is not None else None,
        "canonical_predicate": (candidate.canonical_predicate if candidate is not None else None),
        "custom_predicate": candidate.custom_predicate if candidate is not None else None,
        "state_dimension": candidate.state_dimension if candidate is not None else None,
        "state_value": candidate.state_value if candidate is not None else None,
        "admission": _admission_summary(live_candidate.assessment),
        "incoming_status": (
            live_candidate.incoming_status.value
            if live_candidate.incoming_status is not None
            else None
        ),
        "eligible_for_virtual_context": live_candidate.eligible_for_virtual_context,
        "outcome": live_candidate.outcome,
        "error": live_candidate.error,
        "unresolved_expected_targets": unresolved_expected_targets or [],
        "unresolved_expected_retrieval_ids": unresolved_expected_retrieval_ids or [],
    }


def _extraction_match_record(
    expected: dict[str, Any], live_candidate: _LiveCandidate
) -> dict[str, Any]:
    candidate = live_candidate.candidate
    expected_predicate = _expected_predicate(expected)
    actual_predicate = ""
    if candidate is not None:
        actual_predicate = (
            candidate.canonical_predicate
            or candidate.custom_predicate
            or candidate.raw_predicate
            or ""
        )
    representation = _semantic_representation_match(expected, candidate)
    return {
        "expected_claim_id": str(expected.get("id") or ""),
        "actual_source_claim_id": live_candidate.source_claim_id,
        "match_reason": live_candidate.match_reason,
        "match_score": live_candidate.match_score,
        "expected_kind": str(expected.get("kind") or ""),
        "actual_kind": candidate.kind.value if candidate is not None else None,
        "expected_predicate": expected_predicate,
        "actual_predicate": actual_predicate,
        "actual_predicate_type": (
            candidate.predicate_type.value if candidate is not None else None
        ),
        "kind_match": bool(candidate is not None and candidate.kind.value == expected.get("kind")),
        "predicate_match": bool(
            expected_predicate and actual_predicate.casefold() == expected_predicate.casefold()
        ),
        **representation,
    }


def _semantic_representation_match(
    expected: dict[str, Any],
    candidate: MemoryCandidate | None,
) -> dict[str, Any]:
    """Compare a live claim with reviewed semantic representations.

    ``acceptable_representations`` is deliberately declarative. It may name
    an existing canonical predicate and required state values, or retain one
    or more reviewed custom predicates. Missing metadata falls back to the
    original exact predicate contract for dataset compatibility.
    """

    expected_raw = _expected_predicate(expected)
    actual_raw = candidate.raw_predicate if candidate is not None else None
    expected_kind = str(expected.get("kind") or MemoryKind.INTERACTION_PATTERN.value)
    kind_match = bool(candidate is not None and candidate.kind.value == expected_kind)
    expected_subject = str(expected.get("subject") or "partner")
    subject_match = bool(
        candidate is not None
        and _evaluation_subject_compatible(
            expected_subject,
            candidate.subject,
            candidate.kind,
        )
    )
    raw_match = bool(
        expected_raw
        and isinstance(actual_raw, str)
        and actual_raw.casefold() == expected_raw.casefold()
    )
    representations = expected.get("acceptable_representations")
    if not isinstance(representations, list) or not representations:
        return {
            "raw_predicate_match": raw_match,
            "canonical_predicate_expected": False,
            "canonical_predicate_match": False,
            "semantic_kind_match": kind_match,
            "semantic_subject_match": subject_match,
            "semantic_identity_match": raw_match and kind_match and subject_match,
            "semantic_identity_reason": (
                "legacy_exact_predicate"
                if raw_match and kind_match and subject_match
                else "legacy_semantic_identity_mismatch"
            ),
        }

    canonical_expected = any(
        isinstance(item, dict) and item.get("predicate_type") == PredicateType.CANONICAL.value
        for item in representations
    )
    canonical_match = False
    semantic_match = False
    reason = "no_acceptable_representation_matched"
    for representation in representations:
        if not isinstance(representation, dict) or candidate is None:
            continue
        predicate_type = str(representation.get("predicate_type") or "").casefold()
        if predicate_type == PredicateType.CANONICAL.value:
            expected_canonical = representation.get("canonical_predicate")
            predicate_matches = bool(
                isinstance(expected_canonical, str)
                and candidate.predicate_type == PredicateType.CANONICAL
                and candidate.canonical_predicate == expected_canonical
            )
            canonical_match = canonical_match or predicate_matches
            if not predicate_matches:
                continue
            expected_dimension = representation.get("state_dimension")
            if (
                isinstance(expected_dimension, str)
                and candidate.state_dimension != expected_dimension
            ):
                reason = "canonical_state_dimension_mismatch"
                continue
            expected_values = representation.get("state_values")
            if expected_values is None and "state_value" in representation:
                expected_values = [representation.get("state_value")]
            if isinstance(expected_values, list) and candidate.state_value not in {
                str(value) for value in expected_values if value is not None
            }:
                reason = "canonical_state_value_mismatch"
                continue
            semantic_match = True
            reason = "acceptable_canonical_representation"
            break
        if predicate_type == PredicateType.CUSTOM.value:
            expected_custom = representation.get("custom_predicate")
            custom_values = representation.get("custom_predicates")
            allowed = {
                str(value).casefold()
                for value in (
                    custom_values if isinstance(custom_values, list) else [expected_custom]
                )
                if isinstance(value, str) and value
            }
            if not (
                candidate.predicate_type == PredicateType.CUSTOM
                and isinstance(candidate.custom_predicate, str)
                and candidate.custom_predicate.casefold() in allowed
            ):
                continue
            if not _custom_payload_constraints_match(
                candidate,
                representation.get("payload_constraints"),
            ):
                reason = "custom_payload_qualifier_mismatch"
                continue
            if not _custom_evidence_qualifier_matches(
                candidate,
                representation.get("evidence_contains_any"),
            ):
                reason = "custom_evidence_qualifier_mismatch"
                continue
            semantic_match = True
            reason = "acceptable_custom_representation"
            break
    return {
        "raw_predicate_match": raw_match,
        "canonical_predicate_expected": canonical_expected,
        "canonical_predicate_match": canonical_match,
        "semantic_kind_match": kind_match,
        "semantic_subject_match": subject_match,
        "semantic_identity_match": semantic_match and kind_match and subject_match,
        "semantic_identity_reason": (
            reason
            if semantic_match and kind_match and subject_match
            else "semantic_kind_or_subject_mismatch"
            if semantic_match
            else reason
        ),
    }


def _custom_payload_constraints_match(
    candidate: MemoryCandidate,
    constraints: object,
) -> bool:
    if constraints is None:
        return True
    if not isinstance(constraints, dict):
        return False
    for field, raw_allowed in constraints.items():
        if field not in _CUSTOM_SEMANTIC_PAYLOAD_FIELDS or not isinstance(raw_allowed, list):
            return False
        allowed = {
            value.strip().casefold()
            for value in raw_allowed
            if isinstance(value, str) and value.strip()
        }
        actual = candidate.payload.get(field)
        if not isinstance(actual, str) or actual.strip().casefold() not in allowed:
            return False
    return True


def _custom_evidence_qualifier_matches(
    candidate: MemoryCandidate,
    raw_needles: object,
) -> bool:
    if raw_needles is None:
        return True
    if not isinstance(raw_needles, list):
        return False
    evidence = " ".join(
        value for value in (candidate.original_text, *candidate.evidence_spans) if value
    ).casefold()
    return any(
        isinstance(needle, str) and needle.strip().casefold() in evidence
        for needle in raw_needles
    )


def _failed_live_claim_row(
    *,
    case: dict[str, Any],
    turn_id: str,
    expected: dict[str, Any],
    live_candidate: _LiveCandidate,
    before_ids: list[str],
    stage: str,
    reason: str,
) -> dict[str, Any]:
    candidate = live_candidate.candidate
    return {
        "id": f"{case['id']}/{turn_id}/{live_candidate.source_claim_id}",
        "expected": expected,
        "actual": {
            "relation": ClaimRelation.UNCERTAIN.value,
            "target_memory_ids": [],
            "target_evaluation_applicable": False,
            "retrieved_memory_ids": [],
            "retrieved_candidates": [],
            "retrieval_relevant_memory_ids": [],
            "validator": None,
            "resolution_status": "not_evaluated_due_to_live_governance",
            "judge_status": "not_called",
            "judge_error_type": None,
        },
        "before_memory_ids": before_ids,
        "passed": False,
        "checks": {"shadow_only": True, "live_governance": False},
        "failures": [stage.casefold()],
        "error_attribution": [stage],
        "first_failing_stage": stage,
        "primary_failure_stage": stage,
        "secondary_failure_stages": [],
        "false_destructive_update": False,
        "confirmed_overwrite_violation": False,
        "event_over_pattern_violation": False,
        "weak_belief_overwrite_violation": False,
        "live": _live_candidate_summary(live_candidate),
        "trace": [
            {
                "layer": "live_governance",
                "stage": stage,
                "reason": reason,
                "candidate": candidate.model_dump(mode="json") if candidate is not None else None,
            }
        ],
    }


def _noncustom_live_claim_row(
    *,
    case: dict[str, Any],
    turn_id: str,
    expected: dict[str, Any],
    expected_claim: dict[str, Any] | None,
    live_candidate: _LiveCandidate,
    existing_memories: list[MemoryItem],
    before_ids: list[str],
    expected_target_memory_ids: set[str],
    target_evaluation_applicable: bool,
) -> dict[str, Any]:
    """Record canonical candidates without invoking the long-tail model.

    Production only sends custom predicates to the semantic long-tail judge.
    Canonical candidates are evaluated through the existing deterministic
    relation and lifecycle contracts instead of being treated as an eval
    expectation failure.
    """

    candidate = live_candidate.candidate
    if candidate is None:
        raise ValueError("canonical evaluation requires a normalized candidate")
    incoming_status = MemoryStatus(
        expected.get(
            "incoming_status",
            (live_candidate.incoming_status or MemoryStatus.PROPOSED).value,
        )
    )
    relation = resolve_claim_relation(
        candidate,
        existing_memories,
        incoming_status=incoming_status,
    )
    transitions = plan_memory_transitions(
        [candidate],
        existing_memories,
        trigger_statuses=[incoming_status],
    )
    actual_targets = set(relation.target_memory_ids)
    expected_relation = expected.get("relation")
    representation = _semantic_representation_match(expected_claim or {}, candidate)
    expected_supersedes = set(expected.get("would_supersede_memory_ids") or [])
    planned_supersedes = {
        target_id for transition in transitions for target_id in transition.target_ids
    }
    checks = {
        "shadow_only": True,
        "canonical_path_applicable": True,
        "production_long_tail_applicable": False,
        "semantic_identity": bool(representation["semantic_identity_match"]),
        "relation": (
            not expected_relation
            or not target_evaluation_applicable
            or relation.relation.value == expected_relation
        ),
        "target_memory_ids": (
            not expected_relation
            or not target_evaluation_applicable
            or actual_targets == expected_target_memory_ids
        ),
        "lifecycle": (
            not expected_supersedes
            or not target_evaluation_applicable
            or planned_supersedes == expected_supersedes
        ),
    }
    failures = [
        name
        for name, passed in checks.items()
        if not passed and name != "production_long_tail_applicable"
    ]
    if not checks["semantic_identity"]:
        attribution = ["Normalization"]
    elif failures:
        attribution = ["Canonical Governance"]
    else:
        attribution = []
    return {
        "id": f"{case['id']}/{turn_id}/{live_candidate.source_claim_id}",
        "expected": expected,
        "actual": {
            "relation": relation.relation.value,
            "target_memory_ids": sorted(actual_targets),
            "expected_target_memory_ids_resolved": sorted(expected_target_memory_ids),
            "target_evaluation_applicable": target_evaluation_applicable,
            "retrieved_memory_ids": [],
            "retrieved_candidates": [],
            "retrieval_relevant_memory_ids": [],
            "validator": None,
            "resolution_status": "canonical_candidate_uses_local_relation_path",
            "judge_status": "not_called",
            "judge_error_type": None,
            "canonical_governance": {
                "rule_name": relation.rule_name,
                "reason": relation.reason,
                "state_dimension": candidate.state_dimension,
                "state_value": candidate.state_value,
                "incoming_status": incoming_status.value,
                "admission_status": (
                    live_candidate.incoming_status.value
                    if live_candidate.incoming_status is not None
                    else None
                ),
                "lifecycle_plans": [
                    {
                        "rule_name": transition.rule_name,
                        "target_memory_ids": list(transition.target_ids),
                        "target_status": transition.target_status.value,
                    }
                    for transition in transitions
                ],
            },
        },
        "before_memory_ids": before_ids,
        "passed": not failures,
        "checks": checks,
        "failures": failures,
        "error_attribution": attribution,
        "first_failing_stage": attribution[0] if attribution else None,
        "primary_failure_stage": attribution[0] if attribution else None,
        "secondary_failure_stages": attribution[1:],
        "false_destructive_update": False,
        "confirmed_overwrite_violation": False,
        "event_over_pattern_violation": False,
        "weak_belief_overwrite_violation": False,
        "live": _live_candidate_summary(live_candidate),
        "expected_claim": expected_claim,
        "trace": [
            {
                "layer": "semantic_relation_routing",
                "predicate_type": (
                    live_candidate.candidate.predicate_type.value
                    if live_candidate.candidate is not None
                    else None
                ),
                "judge_status": "not_called",
                "reason": "production_long_tail_requires_custom_predicate",
            },
            {
                "layer": "canonical_governance",
                "semantic_representation": representation,
                "relation": relation.relation.value,
                "rule_name": relation.rule_name,
                "target_memory_ids": list(relation.target_memory_ids),
                "lifecycle_plans": [
                    {
                        "rule_name": transition.rule_name,
                        "target_memory_ids": list(transition.target_ids),
                        "target_status": transition.target_status.value,
                    }
                    for transition in transitions
                ],
            },
        ],
    }


def _missing_expected_claim_row(
    *,
    case: dict[str, Any],
    turn_id: str,
    expected_raw: dict[str, Any],
    expected: dict[str, Any],
    before_ids: list[str],
    stage: str,
    reason: str,
) -> dict[str, Any]:
    expected_id = str(expected_raw.get("id") or "missing")
    return {
        "id": f"{case['id']}/{turn_id}/expected-{expected_id}",
        "expected": expected,
        "expected_claim": expected_raw,
        "actual": {
            "relation": None,
            "target_memory_ids": [],
            "target_evaluation_applicable": False,
            "retrieved_memory_ids": [],
            "retrieved_candidates": [],
            "retrieval_relevant_memory_ids": [],
            "validator": None,
            "resolution_status": "expected_claim_not_extracted",
            "judge_status": "not_called",
            "judge_error_type": None,
        },
        "before_memory_ids": before_ids,
        "passed": False,
        "checks": {"shadow_only": True, "expected_claim_extracted": False},
        "failures": ["expected_claim_not_extracted"],
        "error_attribution": [stage],
        "first_failing_stage": stage,
        "primary_failure_stage": stage,
        "secondary_failure_stages": [],
        "false_destructive_update": False,
        "confirmed_overwrite_violation": False,
        "event_over_pattern_violation": False,
        "weak_belief_overwrite_violation": False,
        "live": {
            "expected_claim_id": expected_id,
            "outcome": "missing_expected_claim",
            "reason": reason,
        },
        "trace": [{"layer": "extraction", "stage": stage, "reason": reason}],
    }


def _safe_trace_snapshot(trace: ExecutionTrace) -> list[dict[str, Any]]:
    forbidden = {
        "raw_model_response",
        "invalid_claim_snapshot",
        "claims_json",
        "claim_predicates_json",
    }
    records: list[dict[str, Any]] = []
    for record in trace.snapshot():
        data = record.model_dump(mode="json")
        details = data.get("details")
        if isinstance(details, dict):
            data["details"] = {key: value for key, value in details.items() if key not in forbidden}
        records.append(data)
    return records


def _safe_attempt_summary(attempt: MemoryExtractionAttempt) -> dict[str, Any]:
    data = attempt.model_dump(mode="json")
    data.pop("raw_model_response", None)
    data.pop("invalid_claim_snapshot", None)
    for field in ("validation_error", "repair_attempt", "repair_result", "error"):
        value = data.get(field)
        if isinstance(value, str):
            data[field] = value[:500]
    return data


def _case_failure_attribution(turns: list[dict[str, Any]]) -> tuple[str | None, list[str]]:
    stages: list[str] = []
    for turn in turns:
        if turn.get("gate", {}).get("check") is False:
            stages.append("Gate")
        for claim in turn.get("claim_results", []):
            primary = claim.get("primary_failure_stage") or claim.get("first_failing_stage")
            if isinstance(primary, str):
                stages.append(primary)
            stages.extend(
                stage
                for stage in claim.get("secondary_failure_stages", [])
                if isinstance(stage, str)
            )
    unique = list(dict.fromkeys(stages))
    return (unique[0], unique[1:]) if unique else (None, [])


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
    counters: Counter[str] = Counter()
    extractor_latencies: list[float] = []
    strong_latencies: list[float] = []
    judge_latencies: list[float] = []
    extractor_tokens: Counter[str] = Counter()
    judge_tokens: Counter[str] = Counter()
    extraction_models: set[str] = set()
    judge_models: set[str] = set()
    relation_confusion: Counter[str] = Counter()
    judge_relation_confusion: Counter[str] = Counter()
    strong_upgrade_reasons: Counter[str] = Counter()
    canonicalized_case_ids: set[str] = set()
    canonical_semantic_failure_case_ids: set[str] = set()
    canonical_governance_failure_case_ids: set[str] = set()
    claim_rows = [claim for row in rows for turn in row["turns"] for claim in turn["claim_results"]]
    turn_rows = [turn for row in rows for turn in row["turns"]]
    gate_false_negative_rows = [
        (row, turn)
        for row in rows
        for turn in row["turns"]
        if turn["gate"].get("expected_should_extract") is True
        and not turn["gate"].get("should_extract")
    ]

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
        if "durable_reversal" in str(turn.get("expected", {}).get("signal", "")):
            counters["durable_reversal_expected_count"] += 1
            counters["durable_reversal_true_positive_count"] += int(gate["should_extract"])

        extraction = turn.get("extraction") or {}
        if extraction.get("mode") != "live":
            continue
        expected_ids = extraction.get("expected_claim_ids") or []
        matched_ids = extraction.get("matched_expected_claim_ids") or []
        counters["extraction_expected_count"] += len(expected_ids)
        counters["extraction_success_count"] += len(matched_ids)
        counters["extraction_failure_count"] += max(0, len(expected_ids) - len(matched_ids))
        counters["extracted_claim_count"] += int(extraction.get("claim_count", 0))
        if extraction.get("claim_count", 0) == 0:
            counters["empty_claim_turn_count"] += 1
        attempts = extraction.get("attempts") or []
        # Count the production extractor boundary once per Gate-admitted
        # turn. Attempts remain visible separately because one call may
        # repair or retry internally.
        counters["extractor_call_count"] += 1
        counters["extraction_call_count"] += 1
        counters["extractor_attempt_failure_count"] += sum(
            1 for attempt in attempts if attempt.get("status") == "failed"
        )
        counters["extractor_failure_count"] += int(bool(extraction.get("error")))
        strong_attempts = [attempt for attempt in attempts if attempt.get("tier") == "strong"]
        flash_usable = any(
            attempt.get("tier") == "flash"
            and attempt.get("status") == "completed"
            and int(attempt.get("claim_count") or 0) > 0
            for attempt in attempts
        )
        counters["strong_upgrade_count"] += len(strong_attempts)
        for attempt in strong_attempts:
            reason = attempt.get("upgrade_reason")
            if isinstance(reason, str) and reason:
                strong_upgrade_reasons[reason] += 1
            duration = attempt.get("duration_ms")
            if isinstance(duration, (int, float)):
                strong_latencies.append(float(duration))
            successful = bool(
                attempt.get("status") == "completed"
                and int(attempt.get("claim_count") or 0) > 0
                and attempt.get("discard_reason")
                not in {"strong_empty_fallback_to_flash", "strong_output_invalid"}
            )
            failed = bool(
                attempt.get("status") == "failed"
                or attempt.get("discard_reason") == "strong_output_invalid"
            )
            fell_back = bool(
                flash_usable
                and (failed or attempt.get("discard_reason") == "strong_empty_fallback_to_flash")
            )
            counters["strong_success_count"] += int(successful)
            counters["strong_failure_count"] += int(failed)
            counters["strong_fallback_to_flash_count"] += int(fell_back)
            counters["strong_no_value_added_count"] += int(fell_back)
        for trace_entry in extraction.get("trace") or []:
            details = trace_entry.get("details") if isinstance(trace_entry, dict) else None
            if not isinstance(details, dict):
                continue
            signals = str(details.get("signals") or "")
            counters["strong_governed_local_resolution_count"] += int(
                trace_entry.get("name") == "memory_extraction_upgrade_gate"
                and not details.get("should_upgrade")
                and "governed_conflict_local_resolution" in signals
            )
        latency = extraction.get("latency_ms")
        if isinstance(latency, (int, float)):
            extractor_latencies.append(float(latency))
        for attempt in attempts:
            model = attempt.get("model")
            if isinstance(model, str) and model:
                extraction_models.add(model)
            failure_category = attempt.get("failure_category")
            if failure_category in {
                "schema_validation",
                "semantic_validation",
                "json_syntax",
                "root_shape",
            }:
                counters["schema_validation_failure_count"] += 1
            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = attempt.get(field)
                if isinstance(value, int):
                    extractor_tokens[field] += value
        for match in extraction.get("match_records") or []:
            counters["expected_memory_kind_count"] += 1
            counters["expected_memory_kind_correct_count"] += int(match.get("kind_match"))
            counters["expected_predicate_count"] += 1
            counters["expected_predicate_correct_count"] += int(match.get("predicate_match"))
            counters["raw_predicate_expected_count"] += 1
            counters["raw_predicate_match_count"] += int(match.get("raw_predicate_match"))
            if match.get("canonical_predicate_expected"):
                counters["canonical_predicate_expected_count"] += 1
                counters["canonical_predicate_match_count"] += int(
                    match.get("canonical_predicate_match")
                )
            counters["semantic_identity_expected_count"] += 1
            counters["semantic_identity_match_count"] += int(match.get("semantic_identity_match"))
            actual_predicate_type = match.get("actual_predicate_type")
            if actual_predicate_type in {
                PredicateType.CANONICAL.value,
                PredicateType.CUSTOM.value,
            }:
                prefix = (
                    "canonical"
                    if actual_predicate_type == PredicateType.CANONICAL.value
                    else "custom"
                )
                counters[f"{prefix}_semantic_identity_expected_count"] += 1
                counters[f"{prefix}_semantic_identity_pass_count"] += int(
                    match.get("semantic_identity_match")
                )

    for claim in claim_rows:
        expected = claim.get("expected") or {}
        actual = claim.get("actual") or {}
        expected_relation = expected.get("relation")
        actual_relation = actual.get("relation")
        counters["claim_count"] += 1
        counters["passed_claim_count"] += int(bool(claim.get("passed")))
        live = claim.get("live") or {}
        if live.get("matched_expected_claim_id"):
            if live.get("predicate_type") == PredicateType.CANONICAL.value:
                counters["canonicalized_expected_claim_count"] += 1
                canonical_case_id = str(claim.get("id") or "").split("/", 1)[0]
                canonicalized_case_ids.add(canonical_case_id)
                checks = claim.get("checks") or {}
                semantic_match = bool(checks.get("semantic_identity"))
                counters["canonical_governance_pass_count"] += int(
                    semantic_match and bool(claim.get("passed"))
                )
                if not semantic_match:
                    canonical_semantic_failure_case_ids.add(canonical_case_id)
                elif not claim.get("passed"):
                    canonical_governance_failure_case_ids.add(canonical_case_id)
            elif live.get("predicate_type") == PredicateType.CUSTOM.value:
                counters["true_custom_long_tail_claim_count"] += 1
        if expected_relation and actual.get("target_evaluation_applicable", True):
            counters["relation_expected_count"] += 1
            counters["relation_correct_count"] += int(actual_relation == expected_relation)
            counters[f"{expected_relation}_expected_count"] += 1
            counters[f"{expected_relation}_correct_count"] += int(
                actual_relation == expected_relation
            )
            relation_confusion[f"{expected_relation}->{actual_relation}"] += 1
            if expected_relation == ClaimRelation.UPDATE.value:
                counters["update_expected_count"] += 1
            if actual_relation == ClaimRelation.UPDATE.value:
                counters["update_predicted_count"] += 1
            if (
                expected_relation == ClaimRelation.UPDATE.value
                and actual_relation == ClaimRelation.UPDATE.value
            ):
                counters["update_true_positive_count"] += 1

        relevant = set(actual.get("retrieval_relevant_memory_ids") or [])
        retrieved = list(actual.get("retrieved_memory_ids") or [])
        if relevant:
            counters["retrieval_expected_count"] += 1
            for limit in (1, 3, 5):
                counters[f"retrieval_hit_at_{limit}_count"] += int(
                    relevant <= set(retrieved[:limit])
                )
            counters["retrieval_recall_at_5_count"] += int(relevant <= set(retrieved[:5]))
        if actual.get("judge_status") in {"completed", "failed", "not_called"}:
            counters["retrieval_observation_count"] += 1
            counters["retrieved_candidate_count"] += len(retrieved)

        expected_targets = set(actual.get("expected_target_memory_ids_resolved") or [])
        actual_targets = set(actual.get("target_memory_ids") or [])
        if expected_relation in _TARGET_METRIC_RELATIONS and actual.get(
            "target_evaluation_applicable", True
        ):
            counters["target_expected_count"] += len(expected_targets)
            counters["target_predicted_count"] += len(actual_targets)
            counters["target_correct_count"] += len(expected_targets & actual_targets)

        counters["false_destructive_update_count"] += int(
            bool(claim.get("false_destructive_update"))
        )
        counters["confirmed_overwrite_violation_count"] += int(
            bool(claim.get("confirmed_overwrite_violation"))
        )
        counters["event_over_pattern_violation_count"] += int(
            bool(claim.get("event_over_pattern_violation"))
        )
        counters["weak_belief_overwrite_violation_count"] += int(
            bool(claim.get("weak_belief_overwrite_violation"))
        )

        judge_status = actual.get("judge_status")
        judge_error_type = actual.get("judge_error_type")
        judge_model_trace = _judge_model_trace(claim)
        judge_model_details = (
            judge_model_trace.get("details")
            if isinstance(judge_model_trace, dict)
            and isinstance(judge_model_trace.get("details"), dict)
            else {}
        )
        if judge_status in {"completed", "failed"}:
            counters["semantic_judge_call_count"] += 1
            counters["judge_model_attempt_count"] += int(
                judge_model_details.get("attempt_count") or 1
            )
            first_parse_failed = judge_model_details.get("attempt_1_status") == "parse_failed"
            retry_count = int(judge_model_details.get("retry_count") or 0)
            final_parse_failed = bool(
                judge_model_details.get("parse_status") == "failed"
                and judge_model_details.get("attempt_2_status") == "parse_failed"
            )
            counters["judge_first_attempt_parse_failure_count"] += int(first_parse_failed)
            counters["judge_retry_count"] += retry_count
            counters["judge_retry_success_count"] += int(
                retry_count > 0 and judge_model_details.get("parse_status") == "completed"
            )
            counters["judge_final_parse_failure_count"] += int(final_parse_failed)
        if judge_status == "completed":
            counters["judge_evaluated_count"] += 1
            proposal = _proposal_from_claim_trace(claim)
            if isinstance(proposal, dict):
                model = proposal.get("judge_model")
                if isinstance(model, str) and model:
                    judge_models.add(model)
                latency = proposal.get("latency_ms")
                if isinstance(latency, (int, float)):
                    judge_latencies.append(float(latency))
                for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    value = proposal.get(field)
                    if isinstance(value, int):
                        judge_tokens[field] += value
            if expected_relation and actual.get("target_evaluation_applicable", True):
                counters["judge_relation_expected_count"] += 1
                counters["judge_relation_correct_count"] += int(
                    actual_relation == expected_relation
                )
                judge_relation_confusion[f"{expected_relation}->{actual_relation}"] += 1
                counters["judge_relation_mismatch_count"] += int(
                    actual_relation != expected_relation
                )
                counters["judge_target_mismatch_count"] += int(actual_targets != expected_targets)
        elif judge_status == "failed":
            counters["semantic_judge_failure_count"] += 1
            if judge_error_type == "ValueError":
                counters["judge_parse_failure_count"] += 1
                if not judge_model_details:
                    counters["judge_first_attempt_parse_failure_count"] += 1
                    counters["judge_final_parse_failure_count"] += 1
            else:
                counters["judge_transport_failure_count"] += 1
            validator = actual.get("validator")
            counters["judge_fail_closed_count"] += int(
                actual_relation == ClaimRelation.UNCERTAIN.value
                and not actual.get("target_memory_ids")
                and isinstance(validator, dict)
                and not validator.get("would_update")
            )

        validator = actual.get("validator")
        if isinstance(validator, dict):
            if validator.get("validator_pass"):
                counters["validator_allow_count"] += 1
            else:
                counters["validator_deny_count"] += 1
        incorrect_update_proposal = bool(
            actual_relation == ClaimRelation.UPDATE.value
            and expected_relation
            and actual.get("target_evaluation_applicable", True)
            and (
                expected_relation != ClaimRelation.UPDATE.value
                or actual_targets != expected_targets
            )
        )
        if incorrect_update_proposal:
            counters["incorrect_update_proposal_count"] += 1
        if (
            incorrect_update_proposal
            and isinstance(validator, dict)
            and not validator.get("would_update")
        ):
            counters["incorrect_update_proposal_denied_count"] += 1

    total_turns = len(turn_rows)
    total_claims = len(claim_rows)
    relation_total = counters["relation_expected_count"]
    retrieval_total = counters["retrieval_expected_count"]
    all_prompt_tokens = extractor_tokens["prompt_tokens"] + judge_tokens["prompt_tokens"]
    all_completion_tokens = (
        extractor_tokens["completion_tokens"] + judge_tokens["completion_tokens"]
    )
    all_total_tokens = extractor_tokens["total_tokens"] + judge_tokens["total_tokens"]
    metrics: dict[str, Any] = {
        "scenario_count": len(rows),
        "scenario_pass_count": sum(bool(row.get("passed")) for row in rows),
        "scenario_pass_rate": _ratio(sum(bool(row.get("passed")) for row in rows), len(rows)),
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
        "gate_false_negative_case_ids": sorted(
            {str(row.get("id")) for row, _turn in gate_false_negative_rows}
        ),
        "gate_false_negative_by_category": dict(
            sorted(
                Counter(
                    str(row.get("category") or "unknown") for row, _turn in gate_false_negative_rows
                ).items()
            )
        ),
        "gate_false_negative_by_reason": dict(
            sorted(
                Counter(
                    str(turn["gate"].get("reason") or "unknown")
                    for _row, turn in gate_false_negative_rows
                ).items()
            )
        ),
        "durable_reversal_expected_count": counters["durable_reversal_expected_count"],
        "durable_reversal_true_positive_count": counters["durable_reversal_true_positive_count"],
        "durable_reversal_recall": _ratio(
            counters["durable_reversal_true_positive_count"],
            counters["durable_reversal_expected_count"],
        ),
        "extraction_expected_count": counters["extraction_expected_count"],
        "extraction_success_count": counters["extraction_success_count"],
        "extraction_failure_count": counters["extraction_failure_count"],
        "extraction_semantic_success_rate": _ratio(
            counters["extraction_success_count"], counters["extraction_expected_count"]
        ),
        "extraction_excludes_gate_blocked_claims": True,
        "extracted_claim_count": counters["extracted_claim_count"],
        "schema_validation_failure_count": counters["schema_validation_failure_count"],
        "empty_claim_turn_count": counters["empty_claim_turn_count"],
        "expected_memory_kind_accuracy": _ratio(
            counters["expected_memory_kind_correct_count"],
            counters["expected_memory_kind_count"],
        ),
        "expected_predicate_accuracy": _ratio(
            counters["expected_predicate_correct_count"], counters["expected_predicate_count"]
        ),
        "raw_predicate_match_rate": _ratio(
            counters["raw_predicate_match_count"],
            counters["raw_predicate_expected_count"],
        ),
        "canonical_predicate_match_rate": _ratio(
            counters["canonical_predicate_match_count"],
            counters["canonical_predicate_expected_count"],
        ),
        "semantic_identity_match_rate": _ratio(
            counters["semantic_identity_match_count"],
            counters["semantic_identity_expected_count"],
        ),
        "overall_semantic_identity_expected_count": counters[
            "semantic_identity_expected_count"
        ],
        "overall_semantic_identity_pass_count": counters["semantic_identity_match_count"],
        "overall_semantic_identity_match_rate": _ratio(
            counters["semantic_identity_match_count"],
            counters["semantic_identity_expected_count"],
        ),
        "canonical_semantic_identity_expected_count": counters[
            "canonical_semantic_identity_expected_count"
        ],
        "canonicalized_expected_claim_count": counters["canonicalized_expected_claim_count"],
        "canonicalized_case_ids": sorted(canonicalized_case_ids - {""}),
        "canonical_semantic_identity_pass_count": counters[
            "canonical_semantic_identity_pass_count"
        ],
        "canonical_semantic_identity_match_rate": _ratio(
            counters["canonical_semantic_identity_pass_count"],
            counters["canonical_semantic_identity_expected_count"],
        ),
        "custom_semantic_identity_expected_count": counters[
            "custom_semantic_identity_expected_count"
        ],
        "custom_semantic_identity_pass_count": counters[
            "custom_semantic_identity_pass_count"
        ],
        "custom_semantic_identity_match_rate": _ratio(
            counters["custom_semantic_identity_pass_count"],
            counters["custom_semantic_identity_expected_count"],
        ),
        "canonical_governance_pass_count": counters["canonical_governance_pass_count"],
        "canonical_semantic_failure_case_ids": sorted(canonical_semantic_failure_case_ids - {""}),
        "canonical_governance_failure_case_ids": sorted(
            canonical_governance_failure_case_ids - {""}
        ),
        "true_custom_long_tail_claim_count": counters["true_custom_long_tail_claim_count"],
        "retrieval_expected_count": retrieval_total,
        "retrieval_excludes_upstream_blocked_targets": True,
        "retrieval_hit_at_1": _ratio(counters["retrieval_hit_at_1_count"], retrieval_total),
        "retrieval_hit_at_3": _ratio(counters["retrieval_hit_at_3_count"], retrieval_total),
        "retrieval_hit_at_5": _ratio(counters["retrieval_hit_at_5_count"], retrieval_total),
        "retrieval_recall_at_5": _ratio(counters["retrieval_recall_at_5_count"], retrieval_total),
        "avg_candidate_count": _ratio(
            counters["retrieved_candidate_count"], counters["retrieval_observation_count"]
        ),
        "relation_accuracy": _ratio(counters["relation_correct_count"], relation_total),
        "update_precision": _ratio(
            counters["update_true_positive_count"], counters["update_predicted_count"]
        ),
        "update_recall": _ratio(
            counters["update_true_positive_count"], counters["update_expected_count"]
        ),
        "target_memory_accuracy": _ratio(
            counters["target_correct_count"], counters["target_expected_count"]
        ),
        "target_memory_precision": _ratio(
            counters["target_correct_count"], counters["target_predicted_count"]
        ),
        "validator_allow_count": counters["validator_allow_count"],
        "validator_deny_count": counters["validator_deny_count"],
        "false_destructive_update_count": counters["false_destructive_update_count"],
        "false_destructive_update_rate": _ratio(
            counters["false_destructive_update_count"], total_claims
        ),
        "confirmed_overwrite_violation_count": counters["confirmed_overwrite_violation_count"],
        "event_over_pattern_violation_count": counters["event_over_pattern_violation_count"],
        "weak_belief_overwrite_violation_count": counters["weak_belief_overwrite_violation_count"],
        "passed_claim_count": counters["passed_claim_count"],
        "claim_pass_rate": _ratio(counters["passed_claim_count"], total_claims),
        "semantic_judge_call_count": counters["semantic_judge_call_count"],
        "judge_call_count": counters["semantic_judge_call_count"],
        "semantic_judge_failure_count": counters["semantic_judge_failure_count"],
        "judge_failure_count": counters["semantic_judge_failure_count"],
        "judge_evaluated_count": counters["judge_evaluated_count"],
        "judge_transport_failure_count": counters["judge_transport_failure_count"],
        "judge_parse_failure_count": counters["judge_parse_failure_count"],
        "judge_first_attempt_parse_failure_count": counters[
            "judge_first_attempt_parse_failure_count"
        ],
        "judge_retry_count": counters["judge_retry_count"],
        "judge_retry_success_count": counters["judge_retry_success_count"],
        "judge_final_parse_failure_count": counters["judge_final_parse_failure_count"],
        "judge_final_parse_failure_rate": _ratio(
            counters["judge_final_parse_failure_count"],
            counters["semantic_judge_call_count"],
        ),
        "judge_fail_closed_count": counters["judge_fail_closed_count"],
        "judge_model_attempt_count": counters["judge_model_attempt_count"],
        "judge_relation_mismatch_count": counters["judge_relation_mismatch_count"],
        "judge_relation_expected_count": counters["judge_relation_expected_count"],
        "judge_relation_correct_count": counters["judge_relation_correct_count"],
        "judge_relation_accuracy": _ratio(
            counters["judge_relation_correct_count"],
            counters["judge_relation_expected_count"],
        ),
        "judge_target_mismatch_count": counters["judge_target_mismatch_count"],
        "incorrect_update_proposal_count": counters["incorrect_update_proposal_count"],
        "incorrect_update_proposal_denied_count": counters[
            "incorrect_update_proposal_denied_count"
        ],
        "semantic_judge_mean_latency_ms": round(mean(judge_latencies), 3) if judge_latencies else 0,
        "judge_latency_p50": _percentile(judge_latencies, 0.5),
        "judge_latency_p95": _percentile(judge_latencies, 0.95),
        "semantic_judge_p50_latency_ms": _percentile(judge_latencies, 0.5),
        "semantic_judge_p95_latency_ms": _percentile(judge_latencies, 0.95),
        "extractor_call_count": counters["extractor_call_count"],
        "extraction_call_count": counters["extraction_call_count"],
        "extractor_failure_count": counters["extractor_failure_count"],
        "extractor_attempt_failure_count": counters["extractor_attempt_failure_count"],
        "extractor_latency_p50": _percentile(extractor_latencies, 0.5),
        "extractor_latency_p95": _percentile(extractor_latencies, 0.95),
        "strong_upgrade_count": counters["strong_upgrade_count"],
        "strong_upgrade_reason_counts": dict(sorted(strong_upgrade_reasons.items())),
        "strong_success_count": counters["strong_success_count"],
        "strong_failure_count": counters["strong_failure_count"],
        "strong_latency_p50": _percentile(strong_latencies, 0.5),
        "strong_latency_p95": _percentile(strong_latencies, 0.95),
        "strong_fallback_to_flash_count": counters["strong_fallback_to_flash_count"],
        "strong_no_value_added_count": counters["strong_no_value_added_count"],
        "strong_governed_local_resolution_count": counters[
            "strong_governed_local_resolution_count"
        ],
        "prompt_tokens": all_prompt_tokens,
        "completion_tokens": all_completion_tokens,
        "total_tokens": all_total_tokens,
        "extractor_prompt_tokens": extractor_tokens["prompt_tokens"],
        "extractor_completion_tokens": extractor_tokens["completion_tokens"],
        "extractor_total_tokens": extractor_tokens["total_tokens"],
        "judge_prompt_tokens": judge_tokens["prompt_tokens"],
        "judge_completion_tokens": judge_tokens["completion_tokens"],
        "judge_total_tokens": judge_tokens["total_tokens"],
        "extractor_models": sorted(extraction_models),
        "semantic_judge_models": sorted(judge_models),
        "relation_confusion": dict(sorted(relation_confusion.items())),
        "judge_relation_confusion": dict(sorted(judge_relation_confusion.items())),
        "candidate_limit": candidate_limit,
    }
    for relation in _RELATIONS:
        metrics[f"{relation.value}_accuracy"] = _ratio(
            counters[f"{relation.value}_correct_count"],
            counters[f"{relation.value}_expected_count"],
        )
    metrics["error_attribution"] = dict(
        sorted(Counter(stage for row in rows for stage in _case_attribution_stages(row)).items())
    )
    metrics["first_failing_stage"] = dict(
        sorted(
            Counter(
                stage for row in rows if (stage := _first_case_failure_stage(row)) is not None
            ).items()
        )
    )
    metrics["failure_attribution_unit"] = "scenario"
    metrics["semantic_judge_token_usage"] = {
        "prompt_tokens": judge_tokens["prompt_tokens"],
        "completion_tokens": judge_tokens["completion_tokens"],
        "total_tokens": judge_tokens["total_tokens"],
    }
    return metrics


def _proposal_from_claim_trace(claim: dict[str, Any]) -> dict[str, Any] | None:
    for entry in claim.get("trace", []):
        if entry.get("layer") == "proposal" and isinstance(entry.get("proposal"), dict):
            return entry["proposal"]
    return None


def _judge_model_trace(claim: dict[str, Any]) -> dict[str, Any] | None:
    return next(
        (
            entry
            for entry in claim.get("trace", [])
            if entry.get("name") == "memory_semantic_relation_model"
        ),
        None,
    )


def _case_attribution_stages(row: dict[str, Any]) -> list[str]:
    if row.get("passed") is True:
        return []
    primary = _first_case_failure_stage(row)
    secondary = row.get("secondary_failure_stages")
    stages = [primary] if primary is not None else []
    if isinstance(secondary, list):
        stages.extend(stage for stage in secondary if isinstance(stage, str))
    return list(dict.fromkeys(stages))


def _summarize_consistency(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("id"))].append(row)
    by_case: dict[str, dict[str, Any]] = {}
    relation_rates: list[float] = []
    target_rates: list[float] = []
    validator_rates: list[float] = []
    for case_id, runs in sorted(grouped.items()):
        if len(runs) < 2:
            continue
        relation_signatures = [_case_relation_signature(row) for row in runs]
        target_signatures = [_case_target_signature(row) for row in runs]
        validator_signatures = [_case_validator_signature(row) for row in runs]
        relation_rate = _mode_consistency(relation_signatures)
        target_rate = _mode_consistency(target_signatures)
        validator_rate = _mode_consistency(validator_signatures)
        by_case[case_id] = {
            "run_count": len(runs),
            "relation_consistency_rate": relation_rate,
            "target_consistency_rate": target_rate,
            "validator_consistency_rate": validator_rate,
            "relation_runs": relation_signatures,
            "target_runs": target_signatures,
            "validator_runs": validator_signatures,
        }
        relation_rates.append(relation_rate)
        target_rates.append(target_rate)
        validator_rates.append(validator_rate)
    relation_claims = [
        claim
        for row in rows
        for turn in row.get("turns", [])
        for claim in turn.get("claim_results", [])
        if (claim.get("expected") or {}).get("relation")
    ]
    judge_statuses = [(claim.get("actual") or {}).get("judge_status") for claim in relation_claims]
    return {
        "case_count": len(by_case),
        "evaluated_row_count": len(rows),
        "relation_expected_claim_count": len(relation_claims),
        "judge_call_count": sum(status in {"completed", "failed"} for status in judge_statuses),
        "judge_completed_count": judge_statuses.count("completed"),
        "judge_failure_count": judge_statuses.count("failed"),
        "relation_consistency_rate": round(mean(relation_rates), 4) if relation_rates else 0.0,
        "target_consistency_rate": round(mean(target_rates), 4) if target_rates else 0.0,
        "validator_consistency_rate": round(mean(validator_rates), 4) if validator_rates else 0.0,
        "by_case": by_case,
    }


def _case_relation_signature(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "claim": _consistency_claim_key(claim),
            "relation": claim.get("actual", {}).get("relation"),
        }
        for turn in row.get("turns", [])
        for claim in turn.get("claim_results", [])
        if (claim.get("expected") or {}).get("relation")
    ]


def _case_target_signature(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "claim": _consistency_claim_key(claim),
            "targets": sorted(claim.get("actual", {}).get("target_memory_ids") or []),
        }
        for turn in row.get("turns", [])
        for claim in turn.get("claim_results", [])
        if (claim.get("expected") or {}).get("relation")
    ]


def _case_validator_signature(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "claim": _consistency_claim_key(claim),
            "validator_pass": (claim.get("actual", {}).get("validator") or {}).get(
                "validator_pass"
            ),
            "would_update": (claim.get("actual", {}).get("validator") or {}).get("would_update"),
        }
        for turn in row.get("turns", [])
        for claim in turn.get("claim_results", [])
        if (claim.get("expected") or {}).get("relation")
    ]


def _consistency_claim_key(claim: dict[str, Any]) -> Any:
    claim_id = claim.get("id")
    live = claim.get("live") or {}
    expected_claim = claim.get("expected_claim") or {}
    stable_id = (
        live.get("matched_expected_claim_id")
        or live.get("expected_claim_id")
        or expected_claim.get("id")
    )
    if not isinstance(stable_id, str) or not stable_id:
        return claim_id
    if not isinstance(claim_id, str) or "/" not in claim_id:
        return stable_id
    return f"{claim_id.rsplit('/', 1)[0]}/{stable_id}"


def _mode_consistency(values: list[Any]) -> float:
    if not values:
        return 0.0
    serialized = [json.dumps(value, ensure_ascii=False, sort_keys=True) for value in values]
    return _ratio(max(Counter(serialized).values()), len(serialized))


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
    evaluation_mode = str(report.get("evaluation_mode") or "-")
    is_live = evaluation_mode == "shadow_live"
    lines = [
        "# Memory Long-tail Realistic Evaluation",
        "",
        f"- Report version: `{report.get('version', REPORT_VERSION)}`",
        f"- Dataset: `{report.get('source_dataset') or report.get('dataset', '-')}`",
        f"- Dataset SHA-256: `{report.get('dataset_sha256', '-')}`",
        f"- Scenarios: {report.get('scenario_count', 0)}",
        f"- Turns: {report.get('turn_count', 0)}",
        f"- Mode: `{evaluation_mode}`",
        f"- Store mutation permitted: `{report.get('store_mutation_permitted', False)}`",
        f"- Methodology: `{report.get('methodology', '-')}`",
    ]
    if is_live:
        live_models = json.dumps(
            report.get("live_models") or {},
            ensure_ascii=False,
            sort_keys=True,
        )
        embedding_telemetry = json.dumps(
            report.get("embedding_telemetry") or {},
            ensure_ascii=False,
            sort_keys=True,
        )
        lines.append(f"- Live models: `{live_models}`")
        lines.append(f"- Embedding telemetry: `{embedding_telemetry}`")
    lines.extend(
        [
            "",
            "## Metrics",
            "",
            "| Layer | Metric | Value |",
            "|---|---|---:|",
        ]
    )
    groups = {
        "Summary": (
            "scenario_pass_count",
            "scenario_pass_rate",
        ),
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
            "gate_false_negative_case_ids",
            "gate_false_negative_by_category",
            "gate_false_negative_by_reason",
        ),
        "Extraction": (
            "extraction_call_count",
            "extractor_failure_count",
            "extractor_attempt_failure_count",
            "extracted_claim_count",
            "extraction_expected_count",
            "extraction_success_count",
            "extraction_failure_count",
            "extraction_semantic_success_rate",
            "schema_validation_failure_count",
            "empty_claim_turn_count",
            "expected_memory_kind_accuracy",
            "expected_predicate_accuracy",
            "overall_semantic_identity_expected_count",
            "overall_semantic_identity_pass_count",
            "overall_semantic_identity_match_rate",
            "canonical_semantic_identity_expected_count",
            "canonicalized_expected_claim_count",
            "canonicalized_case_ids",
            "canonical_semantic_identity_pass_count",
            "canonical_semantic_identity_match_rate",
            "canonical_governance_pass_count",
            "canonical_semantic_failure_case_ids",
            "canonical_governance_failure_case_ids",
            "custom_semantic_identity_expected_count",
            "custom_semantic_identity_pass_count",
            "custom_semantic_identity_match_rate",
            "semantic_identity_match_rate",
            "raw_predicate_match_rate",
            "canonical_predicate_match_rate",
            "true_custom_long_tail_claim_count",
        ),
        "Retrieval": (
            "retrieval_expected_count",
            "retrieval_hit_at_1",
            "retrieval_hit_at_3",
            "retrieval_hit_at_5",
            "retrieval_recall_at_5",
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
            "update_precision",
            "update_recall",
        ),
        "Target": ("target_memory_accuracy", "target_memory_precision"),
        "Validator Safety": (
            "validator_allow_count",
            "validator_deny_count",
            "false_destructive_update_count",
            "false_destructive_update_rate",
            "confirmed_overwrite_violation_count",
            "event_over_pattern_violation_count",
            "weak_belief_overwrite_violation_count",
        ),
        "Judge": (
            "semantic_judge_call_count",
            "semantic_judge_failure_count",
            "judge_evaluated_count",
            "judge_transport_failure_count",
            "judge_parse_failure_count",
            "judge_first_attempt_parse_failure_count",
            "judge_retry_count",
            "judge_retry_success_count",
            "judge_final_parse_failure_count",
            "judge_final_parse_failure_rate",
            "judge_fail_closed_count",
            "judge_model_attempt_count",
            "judge_relation_expected_count",
            "judge_relation_correct_count",
            "judge_relation_accuracy",
            "judge_relation_mismatch_count",
            "judge_target_mismatch_count",
            "incorrect_update_proposal_count",
            "incorrect_update_proposal_denied_count",
            "semantic_judge_mean_latency_ms",
            "semantic_judge_p50_latency_ms",
            "semantic_judge_p95_latency_ms",
            "semantic_judge_token_usage",
        ),
        "Model Telemetry": (
            "extractor_latency_p50",
            "extractor_latency_p95",
            "strong_upgrade_count",
            "strong_upgrade_reason_counts",
            "strong_success_count",
            "strong_failure_count",
            "strong_latency_p50",
            "strong_latency_p95",
            "strong_fallback_to_flash_count",
            "strong_no_value_added_count",
            "strong_governed_local_resolution_count",
            "judge_latency_p50",
            "judge_latency_p95",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "extractor_models",
            "semantic_judge_models",
        ),
    }
    for group, names in groups.items():
        for name in names:
            if name in metrics:
                value = metrics[name]
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False, sort_keys=True)
                lines.append(f"| {group} | `{name}` | {value} |")

    comparison = report.get("fixture_comparison")
    if isinstance(comparison, dict):
        has_v2_baseline = any(
            isinstance(values, dict) and "live_before" in values for values in comparison.values()
        )
        lines.extend(
            [
                "",
                "## Fixture vs Live Before vs Live After"
                if has_v2_baseline
                else "## Fixture vs Live",
                "",
                (
                    "| Metric | Fixture | Live Before | Live After |"
                    if has_v2_baseline
                    else "| Metric | Fixture | Live |"
                ),
                "|---|---:|---:|---:|" if has_v2_baseline else "|---|---:|---:|",
            ]
        )
        for name, values in comparison.items():
            if not isinstance(values, dict):
                continue
            if has_v2_baseline:
                lines.append(
                    f"| `{name}` | {_report_value(values.get('fixture'))} | "
                    f"{_report_value(values.get('live_before'))} | "
                    f"{_report_value(values.get('live_after', values.get('live')))} |"
                )
            else:
                lines.append(
                    f"| `{name}` | {_report_value(values.get('fixture'))} | "
                    f"{_report_value(values.get('live'))} |"
                )

    consistency = report.get("hard_case_consistency")
    if isinstance(consistency, dict) and consistency:
        lines.extend(
            [
                "",
                "## Hard-case Repeat Consistency",
                "",
                (
                    "- Judge reach: "
                    f"{consistency.get('judge_completed_count', 0)} completed / "
                    f"{consistency.get('judge_call_count', 0)} attempted calls across "
                    f"{consistency.get('relation_expected_claim_count', 0)} "
                    "relation-expected claim observations."
                ),
                "",
                "| Scope | Relation | Target | Validator |",
                "|---|---:|---:|---:|",
                (
                    "| Aggregate | "
                    f"{_report_value(consistency.get('relation_consistency_rate'))} | "
                    f"{_report_value(consistency.get('target_consistency_rate'))} | "
                    f"{_report_value(consistency.get('validator_consistency_rate'))} |"
                ),
            ]
        )
        by_case = consistency.get("by_case")
        if isinstance(by_case, dict):
            for case_id, values in sorted(by_case.items()):
                if not isinstance(values, dict):
                    continue
                lines.append(
                    f"| `{case_id}` | "
                    f"{_report_value(values.get('relation_consistency_rate'))} | "
                    f"{_report_value(values.get('target_consistency_rate'))} | "
                    f"{_report_value(values.get('validator_consistency_rate'))} |"
                )

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
        lines.extend(
            [
                "| Case | Category | Primary | Secondary |",
                "|---|---|---|---|",
            ]
        )
        for row in failures:
            primary = row.get("primary_failure_stage") or _first_case_failure_stage(row)
            secondary = row.get("secondary_failure_stages") or _secondary_case_failure_stages(
                row,
                primary=primary,
            )
            lines.append(
                f"| {row.get('id', '-')} | {row.get('category', '-')} | "
                f"{primary or '-'} | {', '.join(secondary) or '-'} |"
            )

    if is_live:
        lines.extend(
            _render_live_answers(
                metrics,
                report.get("hard_case_consistency"),
                report.get("embedding_telemetry"),
            )
        )
    lines.extend(
        [
            "",
            "## Scope",
            "",
            (
                "Live mode invokes the configured production Memory extractor, embedding "
                "retriever, semantic relation judge, and deterministic validator."
                if is_live
                else "Fixture mode uses reviewed fixture extraction and relation proposals."
            ),
            "Virtual context may add normally admitted memories for later turns, but it never "
            "commits relation/lifecycle mutations to the Store.",
            "Failed scenarios retain a primary failure stage so Gate, Extraction, "
            "Normalization, Admission, Retrieval, Semantic Judge, Target Selection, and "
            "Validator gaps remain distinguishable.",
            "",
        ]
    )
    return "\n".join(lines)


def _report_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _first_case_failure_stage(row: dict[str, Any]) -> str | None:
    if row.get("passed") is True:
        return None
    case_primary = row.get("primary_failure_stage")
    if isinstance(case_primary, str):
        return case_primary
    for turn in row.get("turns", []):
        for claim in turn.get("claim_results", []):
            stage = claim.get("primary_failure_stage") or claim.get("first_failing_stage")
            if isinstance(stage, str):
                return stage
    return None


def _secondary_case_failure_stages(
    row: dict[str, Any],
    *,
    primary: str | None,
) -> list[str]:
    stages: list[str] = []
    for turn in row.get("turns", []):
        for claim in turn.get("claim_results", []):
            for stage in claim.get("secondary_failure_stages", []):
                if isinstance(stage, str) and stage != primary:
                    stages.append(stage)
    return list(dict.fromkeys(stages))


def _render_live_answers(
    metrics: dict[str, Any],
    consistency: Any,
    embedding_telemetry: Any,
) -> list[str]:
    gate_recall = float(metrics.get("gate_recall") or 0)
    retrieval_recall = float(metrics.get("retrieval_recall_at_5") or 0)
    retrieval_hit_at_3 = float(metrics.get("retrieval_hit_at_3") or 0)
    retrieval_expected = int(metrics.get("retrieval_expected_count") or 0)
    relation_accuracy = float(metrics.get("relation_accuracy") or 0)
    judge_relation_accuracy = float(metrics.get("judge_relation_accuracy") or 0)
    judge_relation_expected = int(metrics.get("judge_relation_expected_count") or 0)
    judge_failures = int(metrics.get("judge_failure_count") or 0)
    update_precision = float(metrics.get("update_precision") or 0)
    false_updates = int(metrics.get("false_destructive_update_count") or 0)
    incorrect_updates = int(metrics.get("incorrect_update_proposal_count") or 0)
    denied_incorrect_updates = int(metrics.get("incorrect_update_proposal_denied_count") or 0)
    confirmed_overwrites = int(metrics.get("confirmed_overwrite_violation_count") or 0)
    event_over_pattern = int(metrics.get("event_over_pattern_violation_count") or 0)
    weak_belief = int(metrics.get("weak_belief_overwrite_violation_count") or 0)
    gate_false_negatives = int(metrics.get("gate_false_negative_count") or 0)
    gate_expected_positive = int(metrics.get("gate_expected_positive_count") or 0)
    canonical_count = int(metrics.get("canonicalized_expected_claim_count") or 0)
    canonical_case_ids = metrics.get("canonicalized_case_ids") or []
    canonical_semantic_pass = int(metrics.get("canonical_semantic_identity_pass_count") or 0)
    canonical_governance_pass = int(metrics.get("canonical_governance_pass_count") or 0)
    canonical_semantic_failures = metrics.get("canonical_semantic_failure_case_ids") or []
    canonical_governance_failures = metrics.get("canonical_governance_failure_case_ids") or []
    custom_count = int(metrics.get("true_custom_long_tail_claim_count") or 0)
    first_parse_failures = int(metrics.get("judge_first_attempt_parse_failure_count") or 0)
    judge_retry_successes = int(metrics.get("judge_retry_success_count") or 0)
    final_parse_failures = int(metrics.get("judge_final_parse_failure_count") or 0)
    strong_upgrades = int(metrics.get("strong_upgrade_count") or 0)
    strong_p95 = float(metrics.get("strong_latency_p95") or 0)
    strong_no_value = int(metrics.get("strong_no_value_added_count") or 0)
    semantic_identity_rate = float(
        metrics.get(
            "overall_semantic_identity_match_rate",
            metrics.get("semantic_identity_match_rate"),
        )
        or 0
    )
    extractor_p95 = float(metrics.get("extractor_latency_p95") or 0)
    gate_false_negative_categories = _format_count_map(
        metrics.get("gate_false_negative_by_category")
    )
    gate_false_negative_reasons = _format_count_map(metrics.get("gate_false_negative_by_reason"))
    relation_mismatches = _top_relation_mismatches(metrics.get("judge_relation_confusion"))
    embedding_confirmed = bool(
        isinstance(embedding_telemetry, dict)
        and embedding_telemetry.get("embedding_backed_retrieval_confirmed")
    )
    drift = "not run"
    if isinstance(consistency, dict) and consistency:
        drift = (
            "relation/target/validator consistency = "
            f"{_report_value(consistency.get('relation_consistency_rate'))}/"
            f"{_report_value(consistency.get('target_consistency_rate'))}/"
            f"{_report_value(consistency.get('validator_consistency_rate'))}; "
            f"Judge completed {consistency.get('judge_completed_count', 0)}/"
            f"{consistency.get('judge_call_count', 0)} attempted calls across "
            f"{consistency.get('relation_expected_claim_count', 0)} expectations"
        )
    phase_2c = "NOT APPROVED"
    semantic_judge_status = (
        "ACCEPTABLE"
        if relation_accuracy >= 0.90 and update_precision >= 0.95
        else "NEEDS IMPROVEMENT"
    )
    validator_status = (
        "PASS"
        if not any((false_updates, confirmed_overwrites, event_over_pattern, weak_belief))
        else "NEEDS REVIEW"
    )
    retrieval_status = (
        "PASS" if retrieval_recall >= 0.90 and embedding_confirmed else "NEEDS IMPROVEMENT"
    )
    canonical_governance_status = (
        "PASS" if canonical_count > 0 and not canonical_governance_failures else "NEEDS REVIEW"
    )
    extraction_quality_status = "PASS" if semantic_identity_rate >= 0.80 else "NEEDS IMPROVEMENT"
    extraction_latency_status = (
        "PASS" if extractor_p95 and extractor_p95 <= 30000 else "NEEDS IMPROVEMENT"
    )
    return [
        "",
        "## Live V3 Evaluation Answers",
        "",
        (
            "1. Canonicalized reviewed claims: "
            f"{canonical_count} across case IDs {_report_value(canonical_case_ids)}."
        ),
        (
            "2. Canonical contract: semantic identity passed "
            f"{canonical_semantic_pass}/{canonical_count}; deterministic governance passed "
            f"{canonical_governance_pass}/{canonical_count}. Semantic failures: "
            f"{_report_value(canonical_semantic_failures)}; governance failures: "
            f"{_report_value(canonical_governance_failures)}."
        ),
        (f"3. True Custom long-tail claims reaching the custom path: {custom_count}."),
        (
            "4. True long-tail retrieval: "
            f"Hit@3={retrieval_hit_at_3}, Recall@5={retrieval_recall} across "
            f"{retrieval_expected} eligible target observations; embedding-backed "
            f"retrieval confirmed={embedding_confirmed}."
        ),
        (
            "5. Semantic Judge: first-attempt parse failures="
            f"{first_parse_failures}, retry successes={judge_retry_successes}, final parse "
            f"failures={final_parse_failures}, completed Judge relation accuracy="
            f"{judge_relation_accuracy}, UPDATE precision={update_precision}."
        ),
        (
            "6. Strong Upgrade: calls="
            f"{strong_upgrades}, p95={strong_p95}ms, no-value-added lower bound="
            f"{strong_no_value}."
        ),
        (
            "7. Safety: false destructive update="
            f"{false_updates}, confirmed overwrite={confirmed_overwrites}, "
            f"event-over-pattern={event_over_pattern}, weak-belief-overwrite={weak_belief}."
        ),
        "",
        "## Operational Observations",
        "",
        (
            "Real Gate: "
            f"{gate_false_negatives} false negatives across {gate_expected_positive} expected "
            f"positive turns (recall {gate_recall}). Reasons: "
            f"{gate_false_negative_reasons}; categories: {gate_false_negative_categories}."
        ),
        (
            "Relation pipeline accuracy is "
            f"{relation_accuracy}; completed Judge accuracy is {judge_relation_accuracy} "
            f"across {judge_relation_expected} comparable expectations, with "
            f"{judge_failures} call failures. Most frequent completed-Judge mismatches: "
            f"{relation_mismatches}."
        ),
        (
            "Unsafe UPDATE proposals: "
            f"{denied_incorrect_updates}/{incorrect_updates} incorrect UPDATE proposals were "
            f"denied by the validator; {false_updates} were validator-approved destructive "
            "mismatches."
        ),
        f"Hard-case drift: {drift}.",
        "",
        "## Current Status",
        "",
        "- Memory Foundation: REQUIRES SEPARATE REGRESSION.",
        f"- Canonical Governance: {canonical_governance_status}.",
        "- Long-tail Eval Contract: PASS.",
        f"- Gate: {'ACCEPTABLE' if gate_recall >= 0.75 else 'NEEDS IMPROVEMENT'}.",
        f"- Extraction Semantic Quality: {extraction_quality_status}.",
        f"- Extraction Latency: {extraction_latency_status}.",
        f"- Retrieval: {retrieval_status}.",
        f"- Semantic Judge: {semantic_judge_status}.",
        f"- Validator Safety: {validator_status}.",
        f"- Phase 2C {phase_2c}; lifecycle commit remains shadow-only.",
    ]


def _top_relation_mismatches(value: Any) -> str:
    if not isinstance(value, dict):
        return "none recorded"
    mismatches = [
        (str(key), int(count))
        for key, count in value.items()
        if isinstance(count, int)
        and "->" in str(key)
        and str(key).split("->", 1)[0] != str(key).split("->", 1)[1]
    ]
    if not mismatches:
        return "none recorded"
    return ", ".join(
        f"{name} ({count})" for name, count in sorted(mismatches, key=lambda item: -item[1])[:3]
    )


def _format_count_map(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return "none recorded"
    return ", ".join(
        f"{name}={count}"
        for name, count in sorted(value.items(), key=lambda item: (-int(item[1]), item[0]))
    )


__all__ = [
    "REPORT_VERSION",
    "ScenarioFixtureJudge",
    "evaluate_memory_longtail_realistic",
    "render_longtail_realistic_report",
]

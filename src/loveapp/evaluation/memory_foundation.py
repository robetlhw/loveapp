import hashlib
import json
from collections import Counter, defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.application.memory import MemoryService
from loveapp.application.memory_repair import parse_memory_response
from loveapp.application.memory_retrieval import MemoryRetrievalMode
from loveapp.domain.enums import RelationshipStage
from loveapp.domain.memory import (
    AtomicExtraction,
    ClaimRelation,
    MemoryAttemptStatus,
    MemoryExtractionAttempt,
    MemoryExtractionRun,
    MemoryExtractionStatus,
    MemoryItem,
    MemoryStatus,
)
from loveapp.domain.memory_lifecycle import governed_state_identity
from loveapp.domain.memory_verification import ClaimVerification


class TextKeyedScriptedExtractor:
    """Deterministic extractor whose fixtures cannot drift after a Gate skip."""

    def __init__(self, turns: list[dict[str, Any]]) -> None:
        self._extractions: dict[str, deque[tuple[AtomicExtraction, dict[str, Any]]]] = (
            defaultdict(deque)
        )
        self.call_count = 0
        for turn in turns:
            parsed = parse_memory_response(
                json.dumps(
                    {
                        "claims": turn.get("scripted_claims", []),
                        "discarded_spans": turn.get("discarded_spans", []),
                    },
                    ensure_ascii=False,
                ),
                source_text=turn["input"],
            )
            self._extractions[turn["input"]].append(
                (
                    parsed.extraction,
                    {
                        "original_claim_count": parsed.original_claim_count,
                        "repaired_claim_count": parsed.repaired_claim_count,
                        "discarded_claim_count": parsed.discarded_claim_count,
                        "invalid_claim_count": parsed.invalid_claim_count,
                        "invalid_claim_reasons": " | ".join(
                            parsed.invalid_claim_reasons
                        ),
                        "repair_status": parsed.repair_status,
                        "repair_steps": parsed.repair_steps,
                    },
                )
            )

    async def extract(
        self,
        text: str,
        *,
        attempt_callback=None,
        **kwargs,
    ) -> AtomicExtraction:
        del kwargs
        self.call_count += 1
        queue = self._extractions.get(text)
        if not queue:
            raise RuntimeError(f"no scripted extraction for input: {text}")
        extraction, repair = queue.popleft()
        if attempt_callback is not None:
            attempt_callback(
                MemoryExtractionAttempt(
                    attempt=1,
                    status=MemoryAttemptStatus.COMPLETED,
                    duration_ms=0,
                    model="scripted-memory-foundation",
                    tier="fixture",
                    claim_count=len(extraction.claims),
                    original_claim_count=repair["original_claim_count"],
                    repaired_claim_count=repair["repaired_claim_count"],
                    discarded_claim_count=repair["discarded_claim_count"],
                    discarded_span_count=len(extraction.discarded_spans),
                    invalid_claim_count=repair["invalid_claim_count"],
                    invalid_claim_reasons=repair["invalid_claim_reasons"] or None,
                    repair_status=repair["repair_status"],
                    repair_steps=repair["repair_steps"] or None,
                )
            )
        return extraction.model_copy(deep=True)


class ScriptedFoundationVerifier:
    def __init__(self, fixtures: dict[str, dict[str, Any]]) -> None:
        self._fixtures = fixtures
        self.call_count = 0
        self.failure_count = 0
        self.failure_refs: list[str] = []
        self.total_latency_ms = 0.0

    async def verify_claim(
        self,
        text: str,
        *,
        candidate,
        existing_memories: list[MemoryItem],
        allowed_target_ids: set[str],
        trace=None,
    ) -> ClaimVerification:
        del text, trace
        started = perf_counter()
        self.call_count += 1
        ref = ""
        try:
            ref = str(candidate.payload.get("eval_ref") or "")
            fixture = self._fixtures.get(ref)
            if fixture is None:
                raise RuntimeError(f"no scripted verifier result for ref: {ref or '<none>'}")
            target_refs = set(fixture.get("target_refs", []))
            target_ids = [
                item.id
                for item in existing_memories
                if item.id in allowed_target_ids
                and str(item.payload.get("eval_ref") or "") in target_refs
            ]
            payload = {key: value for key, value in fixture.items() if key != "target_refs"}
            payload["target_memory_ids"] = target_ids
            payload.setdefault("verifier_model", "scripted-foundation-verifier")
            return ClaimVerification.model_validate(payload)
        except Exception:
            self.failure_count += 1
            self.failure_refs.append(ref or "<none>")
            raise
        finally:
            self.total_latency_ms += (perf_counter() - started) * 1000

async def evaluate_memory_foundation(
    path: Path,
    *,
    case_id: str | None = None,
) -> dict[str, Any]:
    raw = path.read_bytes()
    cases = _load_cases(raw)
    verifier_path = path.with_name(f"{path.stem}_verifications.json")
    verifier_raw = verifier_path.read_bytes()
    verifier_fixtures = _load_verifier_fixtures(verifier_raw)
    if case_id is not None:
        cases = [case for case in cases if case["id"] == case_id]
        if not cases:
            raise ValueError(f"unknown memory foundation case: {case_id}")

    rows = [
        await _evaluate_case(case, verifier_fixtures=verifier_fixtures)
        for case in cases
    ]
    totals = Counter()
    for row in rows:
        totals.update(row["metric_counts"])

    return {
        "dataset": str(path),
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "verifier_fixture": str(verifier_path),
        "verifier_fixture_sha256": hashlib.sha256(verifier_raw).hexdigest(),
        "version": "memory-foundation-v1",
        "case_filter": case_id,
        "case_count": len(rows),
        "total_turns": totals["total_turns"],
        "passed_case_count": sum(row["passed"] for row in rows),
        "failed_case_count": sum(not row["passed"] for row in rows),
        "metrics": _summarize_metrics(totals),
        "cases": rows,
        "live_model": {
            "enabled": False,
            "reason": "This command is deterministic and never calls an external model.",
        },
    }


def _load_cases(raw: bytes) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
    seen: set[str] = set()
    for case in cases:
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("every memory foundation case requires a non-empty id")
        if case_id in seen:
            raise ValueError(f"duplicate memory foundation case id: {case_id}")
        seen.add(case_id)
        if not case.get("turns"):
            raise ValueError(f"memory foundation case {case_id} has no turns")
    return cases


def _load_verifier_fixtures(raw: bytes) -> dict[str, dict[str, Any]]:
    payload = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("memory foundation verifier fixture must be a JSON object")
    uncertain_refs = payload.pop("__uncertain_refs__", [])
    if not isinstance(uncertain_refs, list) or not all(
        isinstance(ref, str) and ref for ref in uncertain_refs
    ):
        raise ValueError("__uncertain_refs__ must contain non-empty strings")
    fixtures = {
        str(ref): dict(value)
        for ref, value in payload.items()
        if isinstance(value, dict)
    }
    fixtures.update(
        {
            ref: {
                "claim_supported": True,
                "relation": ClaimRelation.UNCERTAIN.value,
                "canonical_predicate": None,
                "state_dimension": None,
                "state_value": None,
                "target_refs": [],
                "reason": "Recorded verifier result keeps this custom claim uncertain.",
                "evidence_sufficient": True,
            }
            for ref in uncertain_refs
        }
    )
    return fixtures


async def _evaluate_case(
    case: dict[str, Any],
    *,
    verifier_fixtures: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    now = [_parse_datetime(case.get("reference_time"))]
    extractor = TextKeyedScriptedExtractor(case["turns"])
    verifier = ScriptedFoundationVerifier(
        _verification_fixtures(case, verifier_fixtures)
    )
    store = InMemoryMemoryStore(clock=lambda: now[0])
    service = MemoryService(
        store,
        extractor,
        verifier=verifier,
        clock=lambda: now[0],
    )
    user_id = f"foundation-{case['id'].casefold()}"
    relationship_id = "partner"
    conversation_id = f"conversation-{case['id'].casefold()}"
    failures: list[dict[str, Any]] = []
    counts = Counter()
    turns: list[dict[str, Any]] = []

    for index, turn in enumerate(case["turns"]):
        if turn.get("reference_time"):
            now[0] = _parse_datetime(turn["reference_time"])
        before = await _all_memories(store, user_id, relationship_id)
        result = await service.remember_text(
            user_id=user_id,
            relationship_id=relationship_id,
            conversation_id=conversation_id,
            text=turn["input"],
        )
        after = await _all_memories(store, user_id, relationship_id)
        context = await service.get_context(
            user_id,
            relationship_id,
            query=turn["input"],
        )
        run = await _find_extraction_run(
            store,
            user_id,
            relationship_id,
            result.extraction_run_id,
        )
        audits = await store.list_transition_audits(
            user_id=user_id,
            relationship_id=relationship_id,
            source_message_id=result.message.id,
        )
        turn_failures, turn_counts = _check_turn(
            turn,
            result=result,
            run=run,
            memories=after,
        )
        if (
            "long_tail" in case.get("tags", [])
            and turn.get("expect", {}).get("gate_should_extract") is True
        ):
            turn_counts["long_tail_gate_expected"] += 1
            turn_counts["long_tail_gate_correct"] += int(
                result.gate_decision is not None and result.gate_decision.should_extract
            )
        counts.update(turn_counts)
        failures.extend({"turn": index + 1, **failure} for failure in turn_failures)
        turns.append(
            {
                "turn": index + 1,
                "input": turn["input"],
                "gate": (
                    result.gate_decision.model_dump(mode="json")
                    if result.gate_decision is not None
                    else None
                ),
                "extraction_status": run.status.value if run else None,
                "extraction_error": result.extraction_error,
                "saved": [_memory_record(saved.item) for saved in result.saved],
                "before": [_memory_record(item) for item in before],
                "after": [_memory_record(item) for item in after],
                "current_context_refs": sorted(_refs(context.current_state)),
                "transition_audits": [audit.model_dump(mode="json") for audit in audits],
                "failures": turn_failures,
            }
        )

    memories = await _all_memories(store, user_id, relationship_id)
    context = await service.get_context(
        user_id,
        relationship_id,
        query=case["turns"][-1]["input"],
    )
    history_context = await service.get_context(
        user_id,
        relationship_id,
        query=case["turns"][-1]["input"],
        mode=MemoryRetrievalMode.HISTORY,
    )
    final_failures, final_counts = _check_final(
        case.get("expected_final", {}),
        memories,
        context.current_state,
        history_context.remembered_items,
        relationship_stage=context.relationship_stage,
    )
    counts.update(final_counts)
    failures.extend({"final": True, **failure} for failure in final_failures)
    counts["total_turns"] += len(case["turns"])
    counts["total_cases"] += 1
    counts["strong_model_call_count"] += verifier.call_count
    counts["strong_model_failure_count"] += verifier.failure_count
    counts["strong_model_total_latency_us"] += round(verifier.total_latency_ms * 1000)
    if verifier.failure_count:
        failures.append(
            {
                "final": True,
                "assertion": "strong_verifier_fixture_coverage",
                "expected": 0,
                "actual": verifier.failure_count,
                "refs": sorted(set(verifier.failure_refs)),
            }
        )

    return {
        "id": case["id"],
        "category": case.get("category"),
        "description": case.get("description"),
        "passed": not failures,
        "metric_counts": dict(counts),
        "metrics": _summarize_metrics(counts),
        "turns": turns,
        "final_memories": [_memory_record(item) for item in memories],
        "final_current_context_refs": sorted(_refs(context.current_state)),
        "final_relationship_stage": context.relationship_stage.value,
        "final_history_context_refs": sorted(
            _refs(history_context.remembered_items)
        ),
        "failures": failures,
    }


def _check_turn(
    turn: dict[str, Any],
    *,
    result,
    run: MemoryExtractionRun | None,
    memories: list[MemoryItem],
) -> tuple[list[dict[str, Any]], Counter]:
    expected = turn.get("expect", {})
    failures: list[dict[str, Any]] = []
    counts = Counter()
    gate = result.gate_decision
    expected_gate = expected.get("gate_should_extract")
    if isinstance(expected_gate, bool):
        actual_gate = gate.should_extract if gate is not None else None
        polarity = "positive" if expected_gate else "negative"
        counts[f"gate_expected_{polarity}"] += 1
        counts[f"gate_correct_{polarity}"] += int(actual_gate == expected_gate)
        if actual_gate != expected_gate:
            failures.append(_failure("gate_should_extract", expected_gate, actual_gate))
    expected_reason = expected.get("gate_reason")
    if expected_reason is not None:
        actual_reason = gate.reason.value if gate is not None else None
        if actual_reason != expected_reason:
            failures.append(_failure("gate_reason", expected_reason, actual_reason))
    if "saved_count" in expected and len(result.saved) != expected["saved_count"]:
        failures.append(_failure("saved_count", expected["saved_count"], len(result.saved)))
    if "saved_count_max" in expected and len(result.saved) > expected["saved_count_max"]:
        failures.append(_failure("saved_count_max", expected["saved_count_max"], len(result.saved)))

    if expected_gate is True:
        counts["extraction_expected"] += 1
        success = bool(
            run is not None
            and run.status == MemoryExtractionStatus.COMPLETED
            and result.extraction_error is None
        )
        counts["extraction_succeeded"] += int(success)
    if run is not None:
        run_error = (run.error or "").casefold()
        counts["schema_validation_failure_count"] += int("schema_validation" in run_error)
        counts["unsupported_enum_count"] += int("unsupported_enum" in run_error)
        for attempt in run.attempts:
            category = (attempt.failure_category or "").casefold()
            error = (attempt.error or "").casefold()
            counts["schema_validation_failure_count"] += int(
                "schema_validation" in category or "schema_validation" in error
            )
            counts["unsupported_enum_count"] += int(
                "unsupported_enum" in category or "unsupported_enum" in error
            )

    actual_by_ref = _by_ref(memories)
    for spec in expected.get("memory_matches", []):
        ref = spec["ref"]
        item = actual_by_ref.get(ref)
        mismatch = _memory_mismatch(item, spec)
        if "canonical_predicate" in spec:
            counts["canonical_expected"] += 1
            counts["canonical_matched"] += int(
                item is not None and item.canonical_predicate == spec["canonical_predicate"]
            )
        if mismatch:
            failures.append(_failure("memory_match", spec, _memory_record(item) if item else None))
    if expected.get("no_memory_changes") and result.saved:
        failures.append(_failure("no_memory_changes", True, len(result.saved)))
    perspective = expected.get("saved_perspective_if_any")
    if perspective is not None and any(
        saved.item.perspective.value != perspective for saved in result.saved
    ):
        failures.append(
            _failure(
                "saved_perspective_if_any",
                perspective,
                [saved.item.perspective.value for saved in result.saved],
            )
        )
    if "relation" in expected:
        relations = [
            saved.item.claim_relation.value if saved.item.claim_relation else None
            for saved in result.saved
        ]
        allowed_relations = {expected["relation"]}
        if expected["relation"] == ClaimRelation.COMPLEMENTARY.value:
            # Independent-add is the current safe equivalent for unrelated
            # preference dimensions.
            allowed_relations.add(ClaimRelation.UNRELATED.value)
        relation_correct = not allowed_relations.isdisjoint(relations)
        counts["relation_expected_turns"] += 1
        counts["relation_correct_turns"] += int(relation_correct)
        if not relation_correct:
            failures.append(_failure("relation", expected["relation"], relations))
    counts["custom_uncertain_count"] += sum(
        saved.item.canonical_predicate is None
        and saved.item.claim_relation == ClaimRelation.UNCERTAIN
        for saved in result.saved
    )
    return failures, counts


def _check_final(
    expected: dict[str, Any],
    memories: list[MemoryItem],
    context_items: list[MemoryItem],
    history_items: list[MemoryItem],
    *,
    relationship_stage: RelationshipStage,
) -> tuple[list[dict[str, Any]], Counter]:
    failures: list[dict[str, Any]] = []
    counts = Counter()
    by_ref = _by_ref(memories)
    active = [item for item in memories if _is_active(item)]
    active_refs = _refs(active)
    context_refs = _refs(context_items)
    history_refs = _refs(history_items)

    if "active_refs" in expected and active_refs != set(expected["active_refs"]):
        failures.append(_failure("active_refs", expected["active_refs"], sorted(active_refs)))
    for ref in expected.get("superseded_refs", []):
        item = by_ref.get(ref)
        counts["lifecycle_expected_transitions"] += 1
        success = item is not None and (
            item.status == MemoryStatus.SUPERSEDED
            or (item.kind.value == "planned_event" and item.status == MemoryStatus.EXPIRED)
        )
        counts["lifecycle_successful_transitions"] += int(success)
        counts["stale_active_memory_count"] += int(item is not None and _is_active(item))
        if not success:
            failures.append(
                _failure(
                    "superseded_ref",
                    {"ref": ref, "status": "superseded"},
                    _memory_record(item) if item else None,
                )
            )
    for ref in expected.get("confirmed_refs", []):
        item = by_ref.get(ref)
        if item is None or item.status != MemoryStatus.CONFIRMED:
            failures.append(
                _failure(
                    "confirmed_ref",
                    {"ref": ref, "status": "confirmed"},
                    _memory_record(item) if item else None,
                )
            )
    for ref in expected.get("protected_confirmed_refs", []):
        item = by_ref.get(ref)
        violated = item is None or item.status != MemoryStatus.CONFIRMED
        counts["confirmed_overwrite_violation_count"] += int(violated)
        if violated:
            failures.append(
                _failure(
                    "protected_confirmed_ref",
                    {"ref": ref, "status": "confirmed"},
                    _memory_record(item) if item else None,
                )
            )
    for ref in expected.get("forbidden_active_refs", []):
        if ref in active_refs:
            failures.append(_failure("forbidden_active_ref", ref, ref))
    for group in expected.get("exclusive_active_ref_groups", []):
        count = len(set(group) & active_refs)
        if count > 1:
            counts["duplicate_active_memory_count"] += count - 1
            failures.append(_failure("exclusive_active_ref_group", "at most one", group))

    if "active_memory_count" in expected and len(active) != expected["active_memory_count"]:
        failures.append(
            _failure("active_memory_count", expected["active_memory_count"], len(active))
        )

    identity_groups: dict[tuple[str, str], list[MemoryItem]] = defaultdict(list)
    for item in active:
        identity = governed_state_identity(item)
        if identity is not None:
            identity_groups[identity].append(item)
    counts["duplicate_active_memory_count"] += sum(
        max(0, len(group) - 1) for group in identity_groups.values()
    )
    if "duplicate_active_count_max" in expected:
        duplicate_count = sum(max(0, len(group) - 1) for group in identity_groups.values())
        if duplicate_count > expected["duplicate_active_count_max"]:
            failures.append(
                _failure(
                    "duplicate_active_count_max",
                    expected["duplicate_active_count_max"],
                    duplicate_count,
                )
            )

    if "context_active_refs" in expected and context_refs != set(expected["context_active_refs"]):
        failures.append(
            _failure(
                "context_active_refs",
                expected["context_active_refs"],
                sorted(context_refs),
            )
        )
    expected_relationship_stage = expected.get("relationship_stage")
    if (
        expected_relationship_stage is not None
        and relationship_stage.value != expected_relationship_stage
    ):
        failures.append(
            _failure(
                "relationship_stage",
                expected_relationship_stage,
                relationship_stage.value,
            )
        )
    for ref in expected.get("forbidden_context_refs", []):
        if ref in context_refs:
            failures.append(_failure("forbidden_context_ref", ref, ref))
    if expected.get("history_includes_superseded"):
        missing = [
            ref
            for ref in expected.get("superseded_refs", [])
            if ref not in history_refs
        ]
        if missing:
            failures.append(_failure("history_includes_superseded", True, missing))
    return failures, counts


def _summarize_metrics(values: Counter) -> dict[str, Any]:
    gate_positive_total = values["gate_expected_positive"]
    gate_negative_total = values["gate_expected_negative"]
    extraction_total = values["extraction_expected"]
    canonical_total = values["canonical_expected"]
    relation_total = values["relation_expected_turns"]
    lifecycle_total = values["lifecycle_expected_transitions"]
    long_tail_total = values["long_tail_gate_expected"]
    return {
        "total_cases": values["total_cases"],
        "total_turns": values["total_turns"],
        "gate_expected_positive_accuracy": _ratio(
            values["gate_correct_positive"], gate_positive_total
        ),
        "gate_expected_negative_accuracy": _ratio(
            values["gate_correct_negative"], gate_negative_total
        ),
        "extraction_success_rate": _ratio(values["extraction_succeeded"], extraction_total),
        "schema_validation_failure_count": values["schema_validation_failure_count"],
        "unsupported_enum_count": values["unsupported_enum_count"],
        "canonical_expected_turns": canonical_total,
        "canonical_match_rate": _ratio(values["canonical_matched"], canonical_total),
        "relation_expected_turns": relation_total,
        "relation_accuracy": _ratio(values["relation_correct_turns"], relation_total),
        "lifecycle_expected_transitions": lifecycle_total,
        "lifecycle_success_rate": _ratio(
            values["lifecycle_successful_transitions"], lifecycle_total
        ),
        "stale_active_memory_count": values["stale_active_memory_count"],
        "duplicate_active_memory_count": values["duplicate_active_memory_count"],
        "confirmed_overwrite_violation_count": values["confirmed_overwrite_violation_count"],
        "long_tail_gate_recall": _ratio(values["long_tail_gate_correct"], long_tail_total),
        "custom_uncertain_count": values["custom_uncertain_count"],
        "strong_model_call_count": values["strong_model_call_count"],
        "strong_model_failure_count": values["strong_model_failure_count"],
        "strong_model_total_latency_ms": round(
            values["strong_model_total_latency_us"] / 1000,
            3,
        ),
    }


def _verification_fixtures(
    case: dict[str, Any],
    recorded: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    fixtures = dict(recorded)
    for turn in case["turns"]:
        verification = turn.get("verification")
        if verification is None:
            continue
        for claim in turn.get("scripted_claims", []):
            ref = claim.get("payload", {}).get("eval_ref")
            if ref:
                fixtures[str(ref)] = verification
    return fixtures


async def _all_memories(
    store: InMemoryMemoryStore,
    user_id: str,
    relationship_id: str,
) -> list[MemoryItem]:
    return await store.list_memories(
        user_id=user_id,
        relationship_id=relationship_id,
        limit=1000,
    )


async def _find_extraction_run(
    store: InMemoryMemoryStore,
    user_id: str,
    relationship_id: str,
    run_id: str | None,
) -> MemoryExtractionRun | None:
    if run_id is None:
        return None
    runs = await store.list_extraction_runs(
        user_id=user_id,
        relationship_id=relationship_id,
        limit=1000,
    )
    return next((run for run in runs if run.id == run_id), None)


def _memory_mismatch(item: MemoryItem | None, expected: dict[str, Any]) -> bool:
    if item is None:
        return True
    fields = {
        "canonical_predicate": item.canonical_predicate,
        "state_dimension": item.state_dimension,
        "state_value": item.state_value,
        "subject": item.subject,
        "kind": item.kind.value,
        "status": item.status.value,
        "perspective": item.perspective.value,
    }
    return any(fields.get(key) != value for key, value in expected.items() if key != "ref")


def _memory_record(item: MemoryItem | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "id": item.id,
        "ref": item.payload.get("eval_ref"),
        "kind": item.kind.value,
        "subject": item.subject,
        "canonical_predicate": item.canonical_predicate,
        "custom_predicate": item.custom_predicate,
        "state_dimension": item.state_dimension,
        "state_value": item.state_value,
        "status": item.status.value,
        "relation": item.claim_relation.value if item.claim_relation else None,
        "supersedes_id": item.supersedes_id,
        "source_message_id": item.source_message_id,
        "perspective": item.perspective.value,
    }


def _by_ref(memories: list[MemoryItem]) -> dict[str, MemoryItem]:
    return {
        str(item.payload["eval_ref"]): item
        for item in memories
        if item.payload.get("eval_ref") is not None
    }


def _refs(memories: list[MemoryItem]) -> set[str]:
    return {
        str(item.payload["eval_ref"])
        for item in memories
        if item.payload.get("eval_ref") is not None
    }


def _is_active(item: MemoryItem) -> bool:
    return item.status in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}


def _failure(assertion: str, expected: Any, actual: Any) -> dict[str, Any]:
    return {"assertion": assertion, "expected": expected, "actual": actual}


def _parse_datetime(value: str | None) -> datetime:
    if value is None:
        return datetime(2026, 8, 20, 12, tzinfo=UTC)
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0

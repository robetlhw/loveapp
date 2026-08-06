import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.application.memory import MemoryService
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    MemoryItem,
    MemoryStatus,
)
from loveapp.domain.memory_predicates import PREDICATE_ALIASES
from loveapp.domain.memory_verification import ClaimVerification


class ScriptedMemoryExtractor:
    def __init__(self, extractions: list[AtomicExtraction]) -> None:
        self._extractions = iter(extractions)

    async def extract(self, text: str, **kwargs) -> AtomicExtraction:
        del text, kwargs
        return next(self._extractions)


class ScriptedClaimVerifier:
    def __init__(self, fixtures: dict[str, dict[str, Any]]) -> None:
        self._fixtures = fixtures
        self.call_count = 0
        self.fixture_hit_count = 0
        self.supported_count = 0

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
        self.call_count += 1
        ref = str(candidate.payload.get("eval_ref") or "")
        fixture = self._fixtures.get(ref)
        if fixture is None:
            raise RuntimeError("no scripted verifier result for this claim")
        self.fixture_hit_count += 1
        target_refs = set(fixture.get("target_refs", []))
        target_ids = [
            item.id
            for item in existing_memories
            if item.id in allowed_target_ids
            and str(item.payload.get("eval_ref")) in target_refs
        ]
        payload = {key: value for key, value in fixture.items() if key != "target_refs"}
        payload["target_memory_ids"] = target_ids
        payload.setdefault("verifier_model", "scripted-strong-verifier")
        verification = ClaimVerification.model_validate(payload)
        if verification.claim_supported and verification.evidence_sufficient:
            self.supported_count += 1
        return verification


async def evaluate_memory_lifecycle(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    cases = [
        json.loads(line)
        for line in raw.decode("utf-8-sig").splitlines()
        if line.strip()
    ]
    rows: list[dict[str, Any]] = []
    counters = Counter()
    by_tag: dict[str, Counter] = defaultdict(Counter)
    by_kind: dict[str, Counter] = defaultdict(Counter)
    relation_confusion = Counter()
    for case in cases:
        row = await _evaluate_case(case)
        rows.append(row)
        counters.update(row["metrics"])
        relation_confusion.update(row["relation_confusion"])
        for kind, values in row["by_memory_kind"].items():
            by_kind[kind].update(values)
        for tag in case.get("tags", []):
            by_tag[tag].update(row["metrics"])
    canonical_total = counters["canonical_expected"]
    relation_total = counters["relation_expected"]
    return {
        "dataset": str(path),
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "version": "memory-lifecycle-v1",
        "case_count": len(rows),
        "step_count": sum(row["step_count"] for row in rows),
        "passed_case_count": sum(row["passed"] for row in rows),
        "metrics": {
            "canonicalization_accuracy": _ratio(
                counters["canonical_correct"], canonical_total
            ),
            "alias_hit_rate": _ratio(counters["alias_hits"], counters["alias_expected"]),
            "unknown_predicate_rate": _ratio(
                counters["custom_predicates"], counters["observed_predicates"]
            ),
            "admission_accuracy": _ratio(
                counters["admission_correct"], counters["admission_expected"]
            ),
            "relation_accuracy": _ratio(counters["relation_correct"], relation_total),
            "duplicate_precision": _ratio(
                counters["duplicate_true_positive"],
                counters["duplicate_predicted"],
            ),
            "duplicate_recall": _ratio(
                counters["duplicate_true_positive"],
                counters["duplicate_expected"],
            ),
            "update_precision": _ratio(
                counters["update_true_positive"],
                counters["update_predicted"],
            ),
            "update_recall": _ratio(
                counters["update_true_positive"],
                counters["update_expected"],
            ),
            "contradiction_detection_accuracy": _ratio(
                counters["contradiction_correct"],
                counters["contradiction_expected"],
            ),
            "wrong_merge_rate": _ratio(
                counters["wrong_merge"], counters["complementary_expected"]
            ),
            "stale_active_memory_rate": _ratio(
                counters["stale_active"], counters["stale_expected"]
            ),
            "conflict_leakage_rate": _ratio(
                counters["conflict_leakage"], counters["conflict_expected"]
            ),
            "transition_audit_completeness": _ratio(
                counters["audit_present"], counters["audit_expected"]
            ),
            "strong_escalation_rate": _ratio(
                counters["strong_calls"], counters["claim_count"]
            ),
            "strong_review_precision": "not_labeled_offline",
            "strong_fixture_coverage": _ratio(
                counters["strong_fixture_hits"], counters["strong_calls"]
            ),
            "strong_verification_support_rate": _ratio(
                counters["strong_supported"], counters["strong_calls"]
            ),
            "average_memory_processing_latency_ms": "not_applicable_offline",
            "p50_latency_ms": "not_applicable_offline",
            "p95_latency_ms": "not_applicable_offline",
            "average_model_cost_per_message": "not_applicable_offline",
        },
        "relation_confusion_matrix": _confusion_matrix(relation_confusion),
        "by_memory_kind": {
            kind: _summarize_kind(values)
            for kind, values in sorted(by_kind.items())
        },
        "by_tag": {
            tag: {
                "case_count": sum(
                    1 for case in cases if tag in case.get("tags", [])
                ),
                "canonicalization_accuracy": _ratio(
                    values["canonical_correct"], values["canonical_expected"]
                ),
                "relation_accuracy": _ratio(
                    values["relation_correct"], values["relation_expected"]
                ),
                "wrong_merge_rate": _ratio(
                    values["wrong_merge"], values["complementary_expected"]
                ),
            }
            for tag, values in sorted(by_tag.items())
        },
        "cases": rows,
        "comparison": {
            "baseline": "not_available",
            "reason": (
                "Memory V1 had no equivalent deterministic lifecycle fixture; "
                "the current report is the first reproducible baseline."
            ),
        },
    }


async def _evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    clock_value = [_parse_datetime(case.get("reference_time"))]
    extractions = [
        AtomicExtraction(
            claims=[AtomicClaim.model_validate(claim) for claim in step.get("claims", [])],
            discarded_spans=step.get("discarded_spans", []),
        )
        for step in case.get("steps", [])
    ]
    verification_fixtures = {
        str(claim["payload"]["eval_ref"]): step["verification"]
        for step in case.get("steps", [])
        if step.get("verification")
        for claim in step.get("claims", [])
        if claim.get("payload", {}).get("eval_ref") is not None
    }
    store = InMemoryMemoryStore(clock=lambda: clock_value[0])
    verifier = ScriptedClaimVerifier(verification_fixtures)
    service = MemoryService(
        store,
        ScriptedMemoryExtractor(extractions),
        clock=lambda: clock_value[0],
        verifier=verifier,
    )
    failures: list[dict[str, Any]] = []
    metrics = Counter()
    kind_metrics: dict[str, Counter] = defaultdict(Counter)
    relation_confusion = Counter()
    step_rows: list[dict[str, Any]] = []
    user_id = case.get("user_id", "lifecycle-eval-user")
    relationship_id = case.get("relationship_id", "primary")
    conversation_id = case.get("conversation_id", f"eval-{case['case_id']}")
    for index, step in enumerate(case.get("steps", [])):
        if step.get("reference_time"):
            clock_value[0] = _parse_datetime(step["reference_time"])
        result = await service.remember_text(
            user_id=user_id,
            relationship_id=step.get("relationship_id", relationship_id),
            conversation_id=conversation_id,
            text=step["text"],
        )
        current_relationship = step.get("relationship_id", relationship_id)
        context = await service.get_context(
            user_id,
            current_relationship,
            query=step["text"],
        )
        saved = result.saved
        expected = step.get("expected", {})
        actual_items = [save.item for save in saved]
        actual_predicates = [item.canonical_predicate for item in actual_items]
        actual_decisions = [
            item.admission_decision.value if item.admission_decision else None
            for item in actual_items
        ]
        actual_relations = [
            item.claim_relation.value if item.claim_relation else None
            for item in actual_items
        ]
        expected_predicates = expected.get("canonical_predicates", [])
        expected_decisions = expected.get("admission_decisions", [])
        expected_relations = expected.get("relations", [])
        active_refs = {
            str(item.payload.get("eval_ref"))
            for item in context.remembered_items
            if item.payload.get("eval_ref") is not None
        }
        metrics.update(
            {
                "canonical_expected": len(expected_predicates),
                "canonical_correct": _sequence_correct(
                    expected_predicates, actual_predicates
                ),
                "admission_expected": len(expected_decisions),
                "admission_correct": _sequence_correct(
                    expected_decisions, actual_decisions
                ),
                "relation_expected": len(expected_relations),
                "relation_correct": _sequence_correct(
                    expected_relations, actual_relations
                ),
                "complementary_expected": int(expected.get("complementary_must_coexist", False)),
                "wrong_merge": int(
                    expected.get("complementary_must_coexist", False)
                    and not set(expected.get("active_refs", [])) <= active_refs
                ),
                "audit_expected": int(bool(step.get("audit_required", True))),
            }
        )
        metrics["audit_present"] += int(
            bool(
                await store.list_transition_audits(
                    user_id=user_id,
                    relationship_id=current_relationship,
                    source_message_id=result.message.id,
                )
            )
        )
        metrics["observed_predicates"] += len(actual_predicates)
        metrics["canonical_observed"] += sum(value is not None for value in actual_predicates)
        metrics["custom_predicates"] += sum(item is None for item in actual_predicates)
        alias_results = [_alias_result(item) for item in actual_items]
        metrics["alias_expected"] += sum(expected for expected, _ in alias_results)
        metrics["alias_hits"] += sum(hit for _, hit in alias_results)
        _update_relation_metrics(metrics, relation_confusion, expected_relations, actual_relations)
        for item_index, item in enumerate(actual_items):
            values = kind_metrics[item.kind.value]
            values["observed"] += 1
            values["canonical_observed"] += int(item.canonical_predicate is not None)
            values["custom_predicates"] += int(item.canonical_predicate is None)
            if item_index < len(expected_predicates):
                values["canonical_expected"] += 1
                values["canonical_correct"] += int(
                    expected_predicates[item_index] == item.canonical_predicate
                )
            if item_index < len(expected_decisions):
                values["admission_expected"] += 1
                values["admission_correct"] += int(
                    expected_decisions[item_index] == actual_decisions[item_index]
                )
            if item_index < len(expected_relations):
                values["relation_expected"] += 1
                values["relation_correct"] += int(
                    expected_relations[item_index] == actual_relations[item_index]
                )
        step_failures = _check_expected(
            expected,
            actual_items,
            actual_decisions,
            actual_relations,
            active_refs,
            result,
        )
        failures.extend({"step": index + 1, **failure} for failure in step_failures)
        step_rows.append(
            {
                "turn_id": step.get("turn_id", f"t{index + 1}"),
                "saved_count": len(saved),
                "canonical_predicates": actual_predicates,
                "admission_decisions": actual_decisions,
                "relations": actual_relations,
                "active_refs": sorted(active_refs),
                "failures": step_failures,
            }
        )

    final_expected = case.get("expected_final", {})
    memories = await store.list_memories(
        user_id=user_id,
        relationship_id=relationship_id,
        limit=1000,
    )
    active = [
        item
        for item in memories
        if item.status in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
    ]
    expected_stale = set(final_expected.get("superseded_refs", []))
    actual_active_refs = {
        str(item.payload.get("eval_ref"))
        for item in active
        if item.payload.get("eval_ref") is not None
    }
    metrics["stale_expected"] += len(expected_stale)
    metrics["stale_active"] += len(expected_stale & actual_active_refs)
    conflict_expected = int(final_expected.get("conflict_expected", False))
    metrics["conflict_expected"] += conflict_expected
    state_values: dict[str, set[str]] = defaultdict(set)
    for item in active:
        if (
            item.status == MemoryStatus.CONFIRMED
            and item.state_dimension
            and item.state_value
        ):
            state_values[item.state_dimension].add(item.state_value)
    leaked = any(len(values) > 1 for values in state_values.values())
    metrics["conflict_leakage"] += int(conflict_expected and leaked)
    metrics["claim_count"] += sum(
        len(step.get("claims", [])) for step in case.get("steps", [])
    )
    metrics["strong_calls"] += verifier.call_count
    metrics["strong_fixture_hits"] += verifier.fixture_hit_count
    metrics["strong_supported"] += verifier.supported_count
    final_failures = _check_final(final_expected, actual_active_refs)
    failures.extend({"final": True, **failure} for failure in final_failures)
    return {
        "case_id": case["case_id"],
        "tags": case.get("tags", []),
        "step_count": len(step_rows),
        "passed": int(not failures),
        "metrics": dict(metrics),
        "by_memory_kind": {
            kind: dict(values) for kind, values in kind_metrics.items()
        },
        "relation_confusion": dict(relation_confusion),
        "steps": step_rows,
        "failures": failures,
    }


def _check_expected(
    expected: dict[str, Any],
    actual_items: list[MemoryItem],
    actual_decisions: list[str | None],
    actual_relations: list[str | None],
    active_refs: set[str],
    result,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    if "saved_count" in expected and len(actual_items) != expected["saved_count"]:
        failures.append(
            {
                "assertion": "saved_count",
                "expected": expected["saved_count"],
                "actual": len(actual_items),
            }
        )
    for key, actual in (
        ("canonical_predicates", [item.canonical_predicate for item in actual_items]),
        ("admission_decisions", actual_decisions),
        ("relations", actual_relations),
    ):
        if key in expected and expected[key] != actual:
            failures.append({"assertion": key, "expected": expected[key], "actual": actual})
    if "active_refs" in expected and set(expected["active_refs"]) != active_refs:
        failures.append(
            {
                "assertion": "active_refs",
                "expected": expected["active_refs"],
                "actual": sorted(active_refs),
            }
        )
    if "gate_should_extract" in expected:
        actual_gate = result.gate_decision.should_extract if result.gate_decision else None
        if actual_gate != expected["gate_should_extract"]:
            failures.append(
                {
                    "assertion": "gate_should_extract",
                    "expected": expected["gate_should_extract"],
                    "actual": actual_gate,
                }
            )
    return failures


def _check_final(
    expected: dict[str, Any],
    active_refs: set[str],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    if "active_refs" in expected and set(expected["active_refs"]) != active_refs:
        failures.append(
            {
                "assertion": "final_active_refs",
                "expected": expected["active_refs"],
                "actual": sorted(active_refs),
            }
        )
    return failures


def _sequence_correct(expected: list[Any], actual: list[Any]) -> int:
    return sum(
        expected_value == actual[index]
        for index, expected_value in enumerate(expected)
        if index < len(actual)
    )


def _update_relation_metrics(
    metrics: Counter,
    confusion: Counter,
    expected: list[str],
    actual: list[str | None],
) -> None:
    for index, expected_relation in enumerate(expected):
        actual_relation = actual[index] if index < len(actual) else "missing"
        actual_label = actual_relation or "missing"
        confusion[f"{expected_relation}|{actual_label}"] += 1
        for label, prefix in (
            ("same", "duplicate"),
            ("update", "update"),
            ("contradiction", "contradiction"),
        ):
            metrics[f"{prefix}_expected"] += int(expected_relation == label)
            metrics[f"{prefix}_predicted"] += int(actual_label == label)
            metrics[f"{prefix}_true_positive"] += int(
                expected_relation == label and actual_label == label
            )
        metrics["contradiction_correct"] += int(
            expected_relation == "contradiction" and actual_label == "contradiction"
        )


def _alias_result(item: MemoryItem) -> tuple[int, int]:
    if not item.raw_predicate or not item.canonical_predicate:
        return 0, 0
    normalized = unicodedata.normalize("NFKC", item.raw_predicate).casefold().strip()
    normalized = re.sub(r"[\s-]+", "_", normalized)
    normalized = re.sub(r"[^\w.\u4e00-\u9fff]+", "_", normalized).strip("_")
    alias = PREDICATE_ALIASES.get(normalized)
    if alias is None:
        return 0, 0
    return 1, int(alias.canonical_predicate == item.canonical_predicate)


def _confusion_matrix(values: Counter) -> dict[str, dict[str, int]]:
    matrix: dict[str, dict[str, int]] = defaultdict(dict)
    for pair, count in sorted(values.items()):
        expected, actual = pair.split("|", maxsplit=1)
        matrix[expected][actual] = count
    return dict(matrix)


def _summarize_kind(values: Counter) -> dict[str, Any]:
    return {
        "observed_count": values["observed"],
        "canonicalization_accuracy": _ratio(
            values["canonical_correct"], values["canonical_expected"]
        ),
        "admission_accuracy": _ratio(
            values["admission_correct"], values["admission_expected"]
        ),
        "relation_accuracy": _ratio(
            values["relation_correct"], values["relation_expected"]
        ),
        "unknown_predicate_rate": _ratio(
            values["custom_predicates"], values["observed"]
        ),
    }


def _parse_datetime(value: str | None) -> datetime:
    if value is None:
        return datetime(2026, 1, 1, tzinfo=UTC)
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0

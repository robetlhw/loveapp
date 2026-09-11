"""Live, behavior-oriented regression evaluation for Memory extraction.

The Gold in this module describes semantic behavior rather than production model
classes.  It deliberately runs the configured extractor in shadow mode and only
uses the deterministic Event Enrichment resolver for an observable no-write
decision.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from loveapp.application.event_enrichment import resolve_event_enrichment
from loveapp.bootstrap import _build_memory_extractor
from loveapp.core.config import get_settings
from loveapp.domain.memory import (
    AtomicClaim,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    MessageRole,
    StoredMessage,
)
from loveapp.domain.memory_dimensions import (
    infer_interaction_event_type,
    normalize_interaction_event_type,
    normalize_interaction_metric,
    normalize_state_dimension,
)
from loveapp.domain.memory_semantic_units import (
    EnrichmentDraft,
    NewMemoryDraft,
    RefinementDraft,
)

REFERENCE_TIME = datetime(2026, 9, 10, 12, tzinfo=UTC)
SUITE_NAME = "memory_behavioral_anchor_v0_1"
DEFAULT_BASELINE_PATH = Path("evals/baselines/memory_behavioral_anchor_v0_1.json")


def load_behavioral_anchor_cases(
    path: Path, *, require_full_suite: bool = True
) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    if require_full_suite and len(rows) != 28:
        raise ValueError(f"behavioral anchor requires exactly 28 cases, got {len(rows)}")
    ids = [row.get("case_id") for row in rows]
    if len(set(ids)) != len(ids) or any(not isinstance(case_id, str) for case_id in ids):
        raise ValueError("behavioral anchor case IDs must be unique strings")
    return rows


def load_behavioral_anchor_baseline(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        baseline = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return baseline if isinstance(baseline, dict) else None


def compare_behavioral_anchor_baseline(
    report: dict[str, Any], baseline: dict[str, Any] | None
) -> dict[str, Any]:
    """Compare case outcomes without changing the frozen behavioral Gold."""

    if not baseline or baseline.get("baseline_status") != "established":
        return {
            "status": "not_available",
            "old_pass_to_new_fail": [],
            "old_fail_to_new_pass": [],
            "unchanged": [],
        }
    old = {
        str(case_id): bool(passed)
        for case_id, passed in (baseline.get("case_results") or {}).items()
    }
    new = {str(row["case_id"]): bool(row.get("passed")) for row in report.get("cases", [])}
    common = sorted(set(old) & set(new))
    return {
        "status": "compared",
        "old_pass_to_new_fail": [
            case_id for case_id in common if old[case_id] and not new[case_id]
        ],
        "old_fail_to_new_pass": [
            case_id for case_id in common if not old[case_id] and new[case_id]
        ],
        "unchanged": [case_id for case_id in common if old[case_id] == new[case_id]],
        "baseline_case_count": len(old),
        "current_case_count": len(new),
    }


def write_behavioral_anchor_baseline(report: dict[str, Any], path: Path) -> Path:
    """Persist a compact outcome baseline for future regression comparison."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "suite": report["evaluation"],
        "version": report["version"],
        "baseline_status": "established",
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": report["dataset"],
        "dataset_sha256": report["dataset_sha256"],
        "case_count": report["case_count"],
        "passed_case_count": report["passed_case_count"],
        "metrics": report["metrics"],
        "case_results": {row["case_id"]: bool(row["passed"]) for row in report["cases"]},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _history(case: dict[str, Any], index: int) -> list[StoredMessage]:
    base = REFERENCE_TIME - timedelta(days=index + 1)
    return [
        StoredMessage(
            id=str(item["id"]),
            conversation_id=f"behavioral-anchor-{case['case_id']}",
            user_id="behavioral-anchor-user",
            relationship_id="behavioral-anchor-relationship",
            role=MessageRole(item["role"]),
            content=str(item["content"]),
            created_at=base + timedelta(minutes=offset),
        )
        for offset, item in enumerate(case.get("conversation_history", []))
    ]


def _memory_items(case: dict[str, Any], index: int) -> list[MemoryItem]:
    items: list[MemoryItem] = []
    for offset, row in enumerate(case.get("existing_memories", [])):
        created_at = _parse_datetime(row.get("occurred_at")) or (
            REFERENCE_TIME - timedelta(days=index + offset + 1)
        )
        kind = MemoryKind(row.get("kind", "interaction_event"))
        payload = dict(row.get("payload") or {})
        item = MemoryItem(
            id=str(row["id"]),
            user_id="behavioral-anchor-user",
            relationship_id="behavioral-anchor-relationship",
            source_message_id=row.get("source_message_id"),
            dedupe_key=f"behavioral-anchor-{row['id']}",
            status=MemoryStatus(row.get("status", "confirmed")),
            kind=kind,
            subject=str(row.get("subject", "relationship")),
            summary=str(row.get("summary", row.get("text", "memory"))),
            original_text=str(row.get("text", row.get("summary", "memory"))),
            evidence_spans=[str(row.get("text", row.get("summary", "memory")))],
            occurred_at=created_at,
            payload=payload,
            created_at=created_at,
            updated_at=created_at,
        )
        items.append(item)
    return items


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _unit_rows(extraction: Any) -> list[dict[str, Any]]:
    units = list(getattr(extraction, "semantic_units", []))
    if units:
        return [_normalize_unit(unit) for unit in units]
    return [_normalize_claim(claim) for claim in extraction.claims]


def _normalize_unit(unit: object) -> dict[str, Any]:
    if isinstance(unit, EnrichmentDraft):
        hint = dict(unit.target_semantic_hint)
        semantic_role = (
            "contextual_completion" if unit.attribute_name == "cause" else "attribute_completion"
        )
        return {
            "semantic_role": semantic_role,
            "semantic_type": "enrichment",
            "memory_kind": unit.target_kind.value,
            "event_type": normalize_interaction_event_type(hint.get("event_type")),
            "subject": unit.subject_hint,
            "perspective": unit.perspective.value,
            "attribute": unit.attribute_name,
            "attributes": [unit.attribute_name],
            "value": unit.value,
            "values": _flatten_values(unit.value),
            "is_new_occurrence": False,
            "is_contextual_completion": True,
            "is_refinement": False,
            "evidence": unit.evidence_span,
            "raw": unit.model_dump(mode="json"),
        }
    if isinstance(unit, RefinementDraft):
        return {
            "semantic_role": "refinement",
            "semantic_type": "refinement",
            "memory_kind": unit.target_kind.value,
            "event_type": None,
            "subject": unit.subject_hint,
            "perspective": MemoryPerspective.USER_REPORTED.value,
            "attribute": unit.raw_predicate,
            "attributes": [unit.raw_predicate],
            "value": unit.value,
            "values": _flatten_values(unit.value),
            "is_new_occurrence": False,
            "is_contextual_completion": False,
            "is_refinement": True,
            "evidence": unit.evidence_span,
            "raw": unit.model_dump(mode="json"),
        }
    if isinstance(unit, NewMemoryDraft):
        row = _normalize_claim(unit.to_atomic_claim())
        row["semantic_type"] = "new_memory"
        row["semantic_role"] = "new_proposition"
        row["is_new_occurrence"] = _claim_is_new_occurrence(unit)
        row["is_contextual_completion"] = False
        row["is_refinement"] = False
        row["raw"] = unit.model_dump(mode="json")
        return row
    return {"semantic_role": None, "semantic_type": None, "raw": repr(unit)}


def _normalize_claim(claim: AtomicClaim) -> dict[str, Any]:
    payload = dict(claim.payload)
    event_type = normalize_interaction_event_type(payload.get("event_type"))
    if event_type is None and claim.kind == MemoryKind.INTERACTION_EVENT:
        event_type = infer_interaction_event_type(
            payload,
            evidence_text=" ".join(claim.evidence_spans),
        )
    metric = _normalize_pattern_metric(payload)
    state_dimension = normalize_state_dimension(payload.get("state_dimension"))
    perspective = _normalize_claim_perspective(claim, payload)
    values: list[str] = []
    for key in ("value", "object", "cause", "emotion", "location", "activity_type"):
        value = payload.get(key, getattr(claim, key, None))
        _collect_text_values(value, values)
    return {
        "semantic_role": "new_proposition",
        "semantic_type": "new_memory",
        "memory_kind": claim.kind.value,
        "event_type": event_type,
        "pattern_metric": metric,
        "state_dimension": state_dimension,
        "subject": claim.subject,
        "perspective": perspective,
        "attribute": None,
        "attributes": _claim_attributes(payload),
        "values": values,
        "is_new_occurrence": _claim_is_new_occurrence(claim),
        "is_contextual_completion": False,
        "is_refinement": False,
        "evidence": list(claim.evidence_spans),
        "raw": claim.model_dump(mode="json"),
    }


def _flatten_values(value: object) -> list[str]:
    values: list[str] = []
    _collect_text_values(value, values)
    return values


def _claim_attributes(payload: dict[str, Any]) -> list[str]:
    names = {
        key
        for key in (
            "cause",
            "severity",
            "emotion",
            "resolution",
            "outcome",
            "location",
            "activity_type",
            "weather",
        )
        if key in payload
    }
    nested = payload.get("attributes")
    if isinstance(nested, dict):
        names.update(str(key) for key in nested if str(key).strip())
    return sorted(names)


def _normalize_pattern_metric(payload: dict[str, Any]) -> str | None:
    raw = payload.get("metric") or payload.get("pattern_type")
    normalized = normalize_interaction_metric(raw)
    aliases = {
        "contact_initiation_frequency": "initiation_balance",
        "reply_latency_increase": "response_engagement",
        "response_latency_increase": "response_engagement",
        "conflict": "conflict_frequency",
        "frequent_conflict": "conflict_frequency",
    }
    return aliases.get(str(raw).casefold(), normalized) if raw is not None else None


def _normalize_claim_perspective(claim: AtomicClaim, payload: dict[str, Any]) -> str:
    source = str(payload.get("source") or "").casefold()
    certainty = str(payload.get("certainty") or "").casefold()
    if source in {"user_perception", "user_belief", "personal_belief"} or certainty in {
        "uncertain",
        "possible",
        "maybe",
    }:
        return MemoryPerspective.USER_BELIEF.value
    return claim.perspective.value


_BOUNDED_OCCURRENCE_EVIDENCE = re.compile(
    r"(?:今天|今日|昨天|昨晚|今早|刚才|刚刚|这次|又|再次|重新|前几天|前段时间|"
    r"\btoday\b|\byesterday\b|\bagain\b|\bthis\s+time\b)",
    re.IGNORECASE,
)


def _claim_is_new_occurrence(claim: AtomicClaim) -> bool:
    """Infer only bounded event occurrences for legacy claim fallback.

    This is deliberately conservative: an unbounded interaction event claim is
    not marked as a new occurrence by the adapter, while explicit temporal or
    recurrence wording is sufficient for the behavioral anchor.
    """

    if claim.kind != MemoryKind.INTERACTION_EVENT:
        return False
    evidence = " ".join([str(claim.summary or ""), *[str(span) for span in claim.evidence_spans]])
    return bool(_BOUNDED_OCCURRENCE_EVIDENCE.search(evidence))


def _collect_text_values(value: object, values: list[str]) -> None:
    if isinstance(value, str) and value.strip():
        values.append(value.strip())
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        values.append(str(value))
    elif isinstance(value, dict):
        for nested in value.values():
            _collect_text_values(nested, values)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _collect_text_values(nested, values)


def _flatten_required(expected: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in expected.get("required", [])]


def _as_options(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else [value]


def _norm_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"[\s\-_/，。！？、,.!?()（）]+", "", text)


def _value_matches(expected: object, actual: object) -> bool:
    expected_text = _norm_text(expected)
    actual_text = _norm_text(actual)
    aliases = {
        "lateness": ("迟到", "迟到问题", "晚到"),
        "money": ("钱", "花钱", "经济", "消费"),
        "anger": ("生气", "愤怒", "恼火"),
        "sadness": ("难过", "伤心", "难受"),
        "rainy": ("下雨", "雨天", "rain"),
        "上海": ("上海",),
        "深圳": ("深圳",),
        "浦东": ("浦东",),
        "外滩": ("外滩",),
    }
    return (
        expected_text == actual_text
        or any(_norm_text(alias) in actual_text for alias in aliases.get(expected_text, ()))
        or expected_text in actual_text
    )


def _field_matches(expected: object, row: dict[str, Any], field: str) -> bool:
    if isinstance(expected, list):
        return any(_field_matches(option, row, field) for option in expected)
    actual = row.get(field)
    if field == "values_contain":
        return any(_value_matches(expected, value) for value in row.get("values", []))
    if field == "attributes_contain":
        return _norm_text(expected) in {_norm_text(value) for value in row.get("attributes", [])}
    if field == "memory_kind":
        return row.get("memory_kind") == expected
    if field == "event_type":
        return row.get("event_type") == expected
    if field == "pattern_metric":
        return row.get("pattern_metric") == expected
    if field == "state_dimension":
        return row.get("state_dimension") == expected
    if field == "perspective":
        return row.get("perspective") == expected
    if field == "semantic_role":
        return row.get("semantic_role") == expected
    if field in {"is_new_occurrence", "is_contextual_completion", "is_refinement"}:
        return bool(row.get(field)) is bool(expected)
    return actual == expected


def _row_matches(expected: dict[str, Any], row: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    for field, value in expected.items():
        if field in {
            "semantic_role",
            "memory_kind",
            "event_type",
            "pattern_metric",
            "state_dimension",
            "subject",
            "perspective",
            "values_contain",
            "attributes_contain",
            "is_new_occurrence",
            "is_contextual_completion",
            "is_refinement",
        }:
            if field == "subject":
                matched = any(
                    _norm_text(option) == _norm_text(row.get("subject"))
                    for option in _as_options(value)
                )
            else:
                matched = _field_matches(value, row, field)
            if not matched:
                failures.append(field)
    return not failures, failures


def _match_required(
    expected: list[dict[str, Any]], observed: list[dict[str, Any]]
) -> tuple[list[dict[str, int]], list[int], list[int], list[str]]:
    matches: list[dict[str, int]] = []
    unmatched_expected = set(range(len(expected)))
    unmatched_observed = set(range(len(observed)))
    failures: list[str] = []
    for expected_index, requirement in enumerate(expected):
        for observed_index in list(unmatched_observed):
            matched, fields = _row_matches(requirement, observed[observed_index])
            if matched:
                matches.append({"expected_index": expected_index, "observed_index": observed_index})
                unmatched_expected.discard(expected_index)
                unmatched_observed.discard(observed_index)
                break
            if fields:
                failures.extend(f"{field}[{expected_index}]" for field in fields)
    return matches, sorted(unmatched_expected), sorted(unmatched_observed), sorted(set(failures))


def _semantic_forbidden_hit(expected: dict[str, Any], observed: list[dict[str, Any]]) -> list[str]:
    hits: list[str] = []
    for forbidden in expected.get("forbidden", []):
        hit = False
        if forbidden == "event_enrichment":
            hit = any(row.get("semantic_type") == "enrichment" for row in observed)
        if forbidden == "refinement":
            hit = any(row.get("semantic_type") == "refinement" for row in observed)
        if forbidden == "objective_confirmed_relationship_state":
            hit = any(
                row.get("memory_kind") == "relationship_state"
                and row.get("perspective") != "user_belief"
                for row in observed
            )
        if forbidden == "confirmed_partner_fact":
            hit = any(
                row.get("perspective") != "user_belief" and row.get("subject") == "partner"
                for row in observed
            )
        if hit:
            hits.append(forbidden)
    return hits


def _extractor_attempts(attempts: list[Any]) -> dict[str, Any]:
    rows = [attempt.model_dump(mode="json") for attempt in attempts]
    failures = [row for row in rows if row.get("status") == "failed"]
    coarse = next((row for row in rows if row.get("stage") == "coarse"), None)
    detailed = next((row for row in rows if row.get("stage") == "detailed"), None)
    return {
        "rows": rows,
        "stage1_success": bool(coarse and coarse.get("status") == "completed"),
        "stage2_called": detailed is not None,
        "stage2_success": bool(detailed and detailed.get("status") == "completed")
        if detailed
        else None,
        "fallback": any(row.get("fallback_used") for row in rows),
        "failure_count": len(failures),
        "failure_reasons": [row.get("failure_category") for row in failures],
    }


def _resolve_shadow(
    case: dict[str, Any], observed: list[dict[str, Any]], index: int
) -> dict[str, Any]:
    memories = _memory_items(case, index)
    enrichment_rows = [row for row in observed if row.get("semantic_type") == "enrichment"]
    resolutions: list[dict[str, Any]] = []
    for row in enrichment_rows:
        raw = row.get("raw", {})
        try:
            draft = EnrichmentDraft.model_validate(raw)
            resolution = resolve_event_enrichment(
                draft,
                current_text=case["text"],
                conversation_history=_history(case, index),
                existing_memories=memories,
                user_id="behavioral-anchor-user",
                relationship_id="behavioral-anchor-relationship",
            )
            resolutions.append(
                {
                    "status": "resolved" if resolution.resolved else "rejected",
                    "reason": resolution.reason,
                    "target_memory_id": resolution.target.id if resolution.target else None,
                    "semantic_candidate_ids": list(resolution.semantic_candidate_ids),
                    "compatible_candidate_ids": list(resolution.compatible_candidate_ids),
                }
            )
        except Exception as exc:
            resolutions.append(
                {"status": "error", "reason": f"adapter_error:{type(exc).__name__}:{exc}"}
            )
    if not resolutions:
        return {"status": "not_applicable", "resolutions": []}
    if any(item["status"] == "resolved" for item in resolutions):
        return {"status": "resolved", "resolutions": resolutions}
    return {"status": "rejected", "resolutions": resolutions}


def _resolution_passes(
    expectation: dict[str, Any] | None, shadow: dict[str, Any]
) -> tuple[bool, str | None]:
    if expectation is None:
        return True, None
    expected_status = expectation.get("status")
    if expectation.get("allow_no_draft") and shadow.get("status") == "not_applicable":
        return True, None
    if expected_status == "resolved":
        resolved = [
            item for item in shadow.get("resolutions", []) if item.get("status") == "resolved"
        ]
        if len(resolved) < int(expectation.get("min_resolved", 1)):
            return False, "expected_resolved_target_missing"
        target = expectation.get("target_memory_id")
        if target and any(item.get("target_memory_id") != target for item in resolved):
            return False, "resolved_target_mismatch"
        return True, None
    if expected_status == "rejected":
        if shadow.get("status") != "rejected":
            return False, "expected_fail_closed_rejection"
        reason = expectation.get("reason")
        if reason and not any(
            item.get("reason") == reason for item in shadow.get("resolutions", [])
        ):
            return False, "rejection_reason_mismatch"
    return True, None


async def evaluate_memory_behavioral_anchor(
    dataset_path: Path,
    *,
    output_dir: Path | None = None,
    extractor: Any | None = None,
    require_full_suite: bool = True,
    baseline_path: Path | None = None,
) -> dict[str, Any]:
    cases = load_behavioral_anchor_cases(dataset_path, require_full_suite=require_full_suite)
    owned_extractor = extractor is None
    if extractor is None:
        settings = get_settings().model_copy(update={"memory_extraction_mode": "two_stage"})
        extractor = _build_memory_extractor(settings)
    rows: list[dict[str, Any]] = []
    try:
        for index, case in enumerate(cases):
            attempts: list[Any] = []
            extraction = await extractor.extract(
                case["text"],
                reference_time=REFERENCE_TIME,
                existing_memories=_memory_items(case, index),
                conversation_history=_history(case, index),
                pending_memory_context=None,
                attempt_callback=attempts.append,
            )
            observed = _unit_rows(extraction)
            expected_semantics = case.get("expected_semantics", {})
            matches, missing, extra, field_failures = _match_required(
                _flatten_required(expected_semantics), observed
            )
            forbidden_hits = _semantic_forbidden_hit(expected_semantics, observed)
            shadow = _resolve_shadow(case, observed, index)
            resolution_ok, resolution_failure = _resolution_passes(
                case.get("resolution_expectation"), shadow
            )
            expected_should_extract = expected_semantics.get("should_extract")
            gate_ok = (
                expected_should_extract is None
                or extraction.should_extract is expected_should_extract
            )
            first_failure = None
            if not gate_ok:
                first_failure = "Stage1_gate"
            elif missing:
                first_failure = "Stage2_semantic_decomposition"
            elif forbidden_hits:
                first_failure = "Stage2_forbidden_semantics"
            elif not resolution_ok:
                first_failure = "Resolver"
            elif _extractor_attempts(attempts)["failure_count"]:
                first_failure = "Model_transport_or_parse"
            passed = gate_ok and not missing and not forbidden_hits and resolution_ok
            rows.append(
                {
                    "case_id": case["case_id"],
                    "category": case.get("category"),
                    "text": case["text"],
                    "expected_semantics": expected_semantics,
                    "observed_semantics": observed,
                    "semantic_matches": matches,
                    "missing_expected": missing,
                    "extra_observed": extra,
                    "field_failures": field_failures,
                    "forbidden_hits": forbidden_hits,
                    "resolution_expectation": case.get("resolution_expectation"),
                    "resolution": shadow,
                    "mutation_expectation": case.get("mutation_expectation"),
                    "passed": passed,
                    "first_failure_reason": first_failure or resolution_failure,
                    "gate": {
                        "should_extract": extraction.should_extract,
                        "reason": extraction.gate_reason.value if extraction.gate_reason else None,
                    },
                    "telemetry": _extractor_attempts(attempts),
                }
            )
    finally:
        if owned_extractor:
            close = getattr(extractor, "aclose", None)
            if callable(close):
                await close()

    metrics = _build_metrics(rows)
    report = {
        "evaluation": SUITE_NAME,
        "version": "v0.1",
        "dataset": str(dataset_path),
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "mode": "two_stage",
        "store_mutation_permitted": False,
        "case_count": len(rows),
        "passed_case_count": sum(bool(row["passed"]) for row in rows),
        "metrics": metrics,
        "cases": rows,
    }
    if baseline_path is not None:
        report["baseline_comparison"] = compare_behavioral_anchor_baseline(
            report, load_behavioral_anchor_baseline(baseline_path)
        )
    if output_dir is not None:
        write_behavioral_anchor_artifacts(report, output_dir)
    return report


def _build_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def ratio(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 4) if denominator else 0.0

    required_total = sum(len(row["expected_semantics"].get("required", [])) for row in rows)
    matched_total = sum(len(row["semantic_matches"]) for row in rows)
    new_event_rows = [
        row for row in rows if "new_event_safety" in row["expected_semantics"].get("tags", [])
    ]
    false_enrichment = [
        row
        for row in new_event_rows
        if any(item.get("semantic_type") == "enrichment" for item in row["observed_semantics"])
    ]
    ambiguous_rows = [
        row
        for row in rows
        if (row.get("resolution_expectation") or {}).get("reason")
        == "ambiguous_semantic_event_antecedent"
    ]
    rejected_ambiguous = [
        row for row in ambiguous_rows if row["resolution"].get("status") == "rejected"
    ]
    multi_rows = [row for row in rows if "multi_claim" in row["expected_semantics"].get("tags", [])]
    enrichment_expected = [
        row
        for row in rows
        if row.get("mutation_expectation")
        in {"enrichment_shadow", "mixed_new_and_enrichment", "no_enrichment"}
    ]
    enrichment_detected = [
        row
        for row in enrichment_expected
        if any(item.get("semantic_type") == "enrichment" for item in row["observed_semantics"])
    ]
    return {
        "overall_anchor_pass_rate": ratio(sum(bool(row["passed"]) for row in rows), len(rows)),
        "semantic_role_accuracy": ratio(
            sum(not row["missing_expected"] for row in rows), len(rows)
        ),
        "memory_kind_accuracy": ratio(
            sum(
                not any("memory_kind" in failure for failure in row["field_failures"])
                for row in rows
            ),
            len(rows),
        ),
        "event_type_accuracy": ratio(
            sum(
                not any("event_type" in failure for failure in row["field_failures"])
                for row in rows
                if any(
                    "event_type" in requirement
                    for requirement in row["expected_semantics"].get("required", [])
                )
            ),
            sum(
                any("event_type" in req for req in row["expected_semantics"].get("required", []))
                for row in rows
            ),
        ),
        "perspective_accuracy": ratio(
            sum(
                not any("perspective" in failure for failure in row["field_failures"])
                for row in rows
                if any(
                    "perspective" in requirement
                    for requirement in row["expected_semantics"].get("required", [])
                )
            ),
            sum(
                any("perspective" in req for req in row["expected_semantics"].get("required", []))
                for row in rows
            ),
        ),
        "semantic_coverage": ratio(matched_total, required_total),
        "new_event_safety_accuracy": ratio(
            sum(not row["forbidden_hits"] for row in new_event_rows), len(new_event_rows)
        ),
        "enrichment_detection_precision": ratio(
            sum(row["passed"] for row in enrichment_detected), len(enrichment_detected)
        ),
        "enrichment_detection_recall": ratio(
            sum(
                any(item.get("semantic_type") == "enrichment" for item in row["observed_semantics"])
                for row in enrichment_expected
                if row.get("mutation_expectation")
                in {"enrichment_shadow", "mixed_new_and_enrichment"}
            ),
            sum(
                row.get("mutation_expectation") in {"enrichment_shadow", "mixed_new_and_enrichment"}
                for row in enrichment_expected
            ),
        ),
        "false_enrichment_count": len(false_enrichment),
        "false_enrichment_rate": ratio(len(false_enrichment), len(new_event_rows)),
        "ambiguous_target_rejection_rate": ratio(len(rejected_ambiguous), len(ambiguous_rows)),
        "refinement_vs_update_accuracy": ratio(
            sum(row["passed"] for row in rows if row["category"] == "refinement_update"),
            sum(row["category"] == "refinement_update" for row in rows),
        ),
        "multi_claim_coverage": ratio(
            sum(not row["missing_expected"] for row in multi_rows), len(multi_rows)
        ),
        "model_failure_count": sum(row["telemetry"]["failure_count"] for row in rows),
    }


def write_behavioral_anchor_artifacts(
    report: dict[str, Any], output_dir: Path
) -> tuple[Path, Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "raw_results.jsonl"
    normalized_path = output_dir / "normalized_results.jsonl"
    failures_path = output_dir / "failures.jsonl"
    summary_path = output_dir / "summary.md"
    raw_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in report["cases"]),
        encoding="utf-8",
    )
    normalized_path.write_text(
        "".join(
            json.dumps(
                {
                    key: row[key]
                    for key in (
                        "case_id",
                        "category",
                        "passed",
                        "first_failure_reason",
                        "observed_semantics",
                        "resolution",
                    )
                },
                ensure_ascii=False,
            )
            + "\n"
            for row in report["cases"]
        ),
        encoding="utf-8",
    )
    failures_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False) + "\n"
            for row in report["cases"]
            if not row["passed"]
        ),
        encoding="utf-8",
    )
    summary_path.write_text(render_behavioral_anchor_summary(report), encoding="utf-8")
    return summary_path, raw_path, normalized_path, failures_path


def render_behavioral_anchor_summary(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# Memory Behavioral Anchor Test v0.1",
        "",
        f"Dataset: `{report['dataset']}`",
        f"Dataset SHA256: `{report['dataset_sha256']}`",
        f"Mode: `{report['mode']}`",
        "Store mutation permitted: `False`",
        "",
        "## Metrics",
        "",
        "| Metric | Result |",
        "|---|---:|",
    ]
    for key, value in metrics.items():
        lines.append(f"| {key} | {value} |")
    lines.extend(
        [
            "",
            f"Overall: `{report['passed_case_count']}/{report['case_count']}` cases passed.",
            "",
            "## Case diagnostics",
            "",
            "| Case | Category | Result | First failure |",
            "|---|---|---|---|",
        ]
    )
    for row in report["cases"]:
        result = "PASS" if row["passed"] else "FAIL"
        lines.append(
            f"| {row['case_id']} | {row['category']} | {result} | "
            f"{row['first_failure_reason'] or '-'} |"
        )
    comparison = report.get("baseline_comparison")
    if isinstance(comparison, dict):
        old_pass_to_new_fail = ", ".join(comparison.get("old_pass_to_new_fail", [])) or "-"
        old_fail_to_new_pass = ", ".join(comparison.get("old_fail_to_new_pass", [])) or "-"
        lines.extend(
            [
                "",
                "## Baseline comparison",
                "",
                f"Status: `{comparison.get('status', 'unknown')}`",
                f"Old pass -> new fail: `{old_pass_to_new_fail}`",
                f"Old fail -> new pass: `{old_fail_to_new_pass}`",
                f"Unchanged: `{len(comparison.get('unchanged', []))}` cases",
            ]
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This is a live semantic regression diagnostic. Gold describes behavior, "
            "not production class names.",
            "No Store mutation, lifecycle commit, ontology expansion, or prompt change "
            "is authorized by this run.",
        ]
    )
    return "\n".join(lines) + "\n"


__all__ = [
    "DEFAULT_BASELINE_PATH",
    "REFERENCE_TIME",
    "SUITE_NAME",
    "compare_behavioral_anchor_baseline",
    "evaluate_memory_behavioral_anchor",
    "load_behavioral_anchor_baseline",
    "load_behavioral_anchor_cases",
    "render_behavioral_anchor_summary",
    "write_behavioral_anchor_artifacts",
    "write_behavioral_anchor_baseline",
]

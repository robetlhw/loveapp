"""Context-aware Memory extraction behavioral anchor evaluation.

The evaluator is deliberately shadow-only.  It calls the configured extractor
with raw user text and a read-only :class:`ConversationContext`, then compares
the resulting semantic units with behavioral Gold.  It never constructs a
``MemoryService`` and never commits a ``MemoryWriteBatch``.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import unicodedata
from collections import defaultdict
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
from loveapp.domain.runtime_context import ConversationContext, PendingQuestion
from loveapp.evaluation.memory_extraction_diagnostics import extraction_failure_stages

REFERENCE_TIME = datetime(2026, 9, 11, 12, tzinfo=UTC)
SUITE_NAME = "context_aware_behavioral_anchor_v0_1"
DEFAULT_DATASET_PATH = Path("evals/memory/context_aware_behavioral_anchor_v0_1.jsonl")
DEFAULT_BASELINE_PATH = Path("evals/baselines/memory_behavioral_anchor_v0_1.json")
DEFAULT_LEGACY_BEHAVIORAL_BASELINE_PATH = DEFAULT_BASELINE_PATH
DEFAULT_CONTEXT_AWARE_BASELINE_PATH = Path(
    "evals/baselines/context_aware_behavioral_anchor_v0_1.json"
)
DEFAULT_OUTPUT_DIR = Path("artifacts/context_aware_behavioral_anchor_v0_1")


def load_context_aware_cases(
    path: Path = DEFAULT_DATASET_PATH,
    *,
    require_full_suite: bool = True,
) -> list[dict[str, Any]]:
    cases = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    if require_full_suite and len(cases) < 60:
        raise ValueError(f"context-aware anchor requires at least 60 cases, got {len(cases)}")
    if require_full_suite:
        diagnostic_count = sum(bool(case.get("diagnostic_only")) for case in cases)
        scored_count = len(cases) - diagnostic_count
        if diagnostic_count != 10:
            raise ValueError(
                "context-aware anchor requires exactly 10 permutation diagnostics, "
                f"got {diagnostic_count}"
            )
        if scored_count < 60:
            raise ValueError(
                f"context-aware anchor requires at least 60 scored cases, got {scored_count}"
            )
    ids = [case.get("case_id") for case in cases]
    if len(ids) != len(set(ids)) or any(not isinstance(case_id, str) for case_id in ids):
        raise ValueError("context-aware case IDs must be unique strings")
    for case in cases:
        if not isinstance(case.get("text"), str) or not case["text"].strip():
            raise ValueError(f"case {case.get('case_id')} has no text")
        if not isinstance(case.get("expected_semantics"), dict):
            raise ValueError(f"case {case.get('case_id')} has no expected_semantics")
    return cases


def _supports_keyword(callable_object: object, keyword: str) -> bool:
    try:
        parameters = inspect.signature(callable_object).parameters
    except (TypeError, ValueError):
        return True
    return keyword in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _history(case: dict[str, Any], index: int) -> list[StoredMessage]:
    base = REFERENCE_TIME - timedelta(days=index + 1)
    return [
        StoredMessage(
            id=str(item.get("id", f"{case['case_id']}-h{offset}")),
            conversation_id=f"context-aware-{case['case_id']}",
            user_id="context-aware-user",
            relationship_id="context-aware-relationship",
            role=MessageRole(item["role"]),
            content=str(item["content"]),
            created_at=_parse_datetime(item.get("created_at")) or base + timedelta(minutes=offset),
        )
        for offset, item in enumerate(case.get("conversation_history", []))
    ]


def _memory_items(case: dict[str, Any], index: int) -> list[MemoryItem]:
    items: list[MemoryItem] = []
    for offset, row in enumerate(case.get("existing_memories", [])):
        occurred_at = _parse_datetime(row.get("occurred_at")) or (
            REFERENCE_TIME - timedelta(days=index + offset + 1)
        )
        kind = MemoryKind(row.get("kind", "interaction_event"))
        text = str(row.get("text", row.get("summary", "memory")))
        items.append(
            MemoryItem(
                id=str(row.get("id", f"{case['case_id']}-m{offset}")),
                user_id="context-aware-user",
                relationship_id="context-aware-relationship",
                source_message_id=row.get("source_message_id"),
                dedupe_key=f"context-aware-{case['case_id']}-{offset}",
                status=MemoryStatus(row.get("status", "confirmed")),
                kind=kind,
                subject=str(row.get("subject", "relationship")),
                summary=str(row.get("summary", text)),
                original_text=text,
                evidence_spans=[text],
                occurred_at=occurred_at,
                payload=dict(row.get("payload") or {}),
                created_at=occurred_at,
                updated_at=occurred_at,
            )
        )
    return items


def _pending_questions(case: dict[str, Any]) -> list[PendingQuestion]:
    questions: list[PendingQuestion] = []
    for index, raw in enumerate(case.get("pending_questions", []), start=1):
        if not isinstance(raw, dict):
            continue
        questions.append(
            PendingQuestion(
                id=str(raw.get("question_id", raw.get("id", f"{case['case_id']}-q{index}"))),
                question_type=str(raw.get("question_type", raw.get("topic", "memory_follow_up"))),
                target_kind=raw.get("target_kind"),
                target_field=raw.get("target_field", raw.get("field")),
                expected_answer_type=raw.get("expected_answer_type"),
                status=str(raw.get("status", "open")),
            )
        )
    return questions


def _context(case: dict[str, Any], index: int) -> ConversationContext:
    return ConversationContext.from_turn(
        case["text"],
        conversation_history=_history(case, index),
        pending_questions=_pending_questions(case),
        active_topic=case.get("active_topic"),
        relevant_memories=_memory_items(case, index),
    )


def _flatten_values(value: object) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, int | float) and not isinstance(value, bool):
        return [str(value)]
    if isinstance(value, dict):
        values: list[str] = []
        for nested in value.values():
            values.extend(_flatten_values(nested))
        return values
    if isinstance(value, list | tuple):
        values = []
        for nested in value:
            values.extend(_flatten_values(nested))
        return values
    return []


def _normalize_pattern_metric(payload: dict[str, Any]) -> str | None:
    raw = payload.get("metric") or payload.get("pattern_type")
    if raw is None:
        return None
    normalized = normalize_interaction_metric(raw)
    return {
        "contact_initiation_frequency": "initiation_balance",
        "reply_latency_increase": "response_engagement",
        "response_latency_increase": "response_engagement",
        "conflict": "conflict_frequency",
        "frequent_conflict": "conflict_frequency",
    }.get(str(raw).casefold(), normalized)


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
            "transportation_mode",
            "gift",
        )
        if key in payload
    }
    nested = payload.get("attributes")
    if isinstance(nested, dict):
        names.update(str(key) for key in nested if str(key).strip())
    return sorted(names)


def _claim_perspective(claim: AtomicClaim) -> str:
    payload = claim.payload
    source = str(payload.get("source") or "").casefold()
    certainty = str(payload.get("certainty") or "").casefold()
    if source in {"user_perception", "user_belief", "personal_belief"} or certainty in {
        "uncertain",
        "possible",
        "maybe",
    }:
        return MemoryPerspective.USER_BELIEF.value
    return claim.perspective.value


_OCCURRENCE_RE = re.compile(
    r"(?:今天|今日|昨天|昨晚|今早|刚才|刚刚|这次|又|再次|重新|前几天|前段时间|"
    r"today|yesterday|again|this time)",
    re.IGNORECASE,
)


def _normalize_claim(claim: AtomicClaim) -> dict[str, Any]:
    payload = dict(claim.payload)
    lineage = payload.get("extraction_provenance") or {}
    operation = (
        "correction" if lineage.get("semantic_roles") == ["correction"] else "new_proposition"
    )
    event_type = normalize_interaction_event_type(payload.get("event_type"))
    if event_type is None and claim.kind == MemoryKind.INTERACTION_EVENT:
        event_type = infer_interaction_event_type(
            payload,
            evidence_text=" ".join(claim.evidence_spans),
        )
    values: list[str] = []
    attributes = payload.get("attributes")
    for key in ("value", "object", "cause", "emotion", "location", "activity_type"):
        values.extend(_flatten_values(payload.get(key, getattr(claim, key, None))))
        if isinstance(attributes, dict):
            values.extend(_flatten_values(attributes.get(key)))
    return {
        "semantic_role": operation,
        "semantic_type": "new_memory",
        "memory_kind": claim.kind.value,
        "event_type": event_type,
        "pattern_metric": _normalize_pattern_metric(payload),
        "state_dimension": normalize_state_dimension(payload.get("state_dimension")),
        "subject": claim.subject,
        "perspective": _claim_perspective(claim),
        "epistemic_status": claim.epistemic_status.value,
        "attribute": None,
        "attributes": _claim_attributes(payload),
        "values": values,
        "is_new_occurrence": claim.kind == MemoryKind.INTERACTION_EVENT
        and bool(_OCCURRENCE_RE.search(" ".join([claim.summary, *claim.evidence_spans]))),
        "is_contextual_completion": False,
        "is_refinement": False,
        "evidence": list(claim.evidence_spans),
        "raw": claim.model_dump(mode="json"),
        "proposition_origin": payload.get("proposition_origin", "uncertain"),
    }


def _normalize_unit(unit: object) -> dict[str, Any]:
    if isinstance(unit, EnrichmentDraft):
        hint = dict(unit.target_semantic_hint)
        return {
            "semantic_role": "attribute_completion",
            "semantic_type": "enrichment",
            "memory_kind": unit.target_kind.value,
            "event_type": normalize_interaction_event_type(hint.get("event_type")),
            "pattern_metric": None,
            "state_dimension": None,
            "subject": unit.subject_hint,
            "perspective": unit.perspective.value,
            "epistemic_status": unit.epistemic_status.value,
            "attribute": unit.attribute_name,
            "attributes": [unit.attribute_name],
            "value": unit.value,
            "values": _flatten_values(unit.value),
            "is_new_occurrence": False,
            "is_contextual_completion": True,
            "is_refinement": False,
            "evidence": unit.evidence_span,
            "raw": unit.model_dump(mode="json"),
            "proposition_origin": "uncertain",
        }
    if isinstance(unit, RefinementDraft):
        return {
            "semantic_role": "refinement",
            "semantic_type": "refinement",
            "memory_kind": unit.target_kind.value,
            "event_type": None,
            "pattern_metric": None,
            "state_dimension": None,
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
            "proposition_origin": "uncertain",
        }
    if isinstance(unit, NewMemoryDraft):
        return _normalize_claim(unit.to_atomic_claim())
    if isinstance(unit, AtomicClaim):
        return _normalize_claim(unit)
    return {"semantic_role": None, "semantic_type": None, "raw": repr(unit)}


def _unit_rows(extraction: Any) -> list[dict[str, Any]]:
    units = list(getattr(extraction, "semantic_units", []))
    return (
        [_normalize_unit(unit) for unit in units]
        if units
        else [_normalize_unit(claim) for claim in extraction.claims]
    )


def _extractor_diagnostic(extractor: Any) -> dict[str, Any]:
    diagnostic = getattr(extractor, "last_diagnostic", {})
    return diagnostic if isinstance(diagnostic, dict) else {}


def _resolve_shadow(
    case: dict[str, Any], observed: list[dict[str, Any]], index: int
) -> dict[str, Any]:
    """Run the existing enrichment resolver without authorizing a write.

    The evaluator deliberately stops at the resolver result.  This makes
    ambiguous-target safety observable while preserving the production Store
    contract and keeping all mutation shadow-only.
    """

    memories = _memory_items(case, index)
    rows: list[dict[str, Any]] = []
    for observed_row in observed:
        if observed_row.get("semantic_type") != "enrichment":
            continue
        try:
            draft = EnrichmentDraft.model_validate(observed_row.get("raw", {}))
            result = resolve_event_enrichment(
                draft,
                current_text=case["text"],
                conversation_history=_history(case, index),
                existing_memories=memories,
                user_id="context-aware-user",
                relationship_id="context-aware-relationship",
            )
            rows.append(
                {
                    "status": "resolved" if result.resolved else "rejected",
                    "reason": result.reason,
                    "target_memory_id": result.target.id if result.target else None,
                    "semantic_candidate_ids": list(result.semantic_candidate_ids),
                    "compatible_candidate_ids": list(result.compatible_candidate_ids),
                    "rejected_candidates": [
                        {"id": item_id, "reason": reason}
                        for item_id, reason in result.rejected_candidates
                    ],
                    "candidate_scores": [
                        {"id": item_id, "score": score}
                        for item_id, score in result.candidate_scores
                    ],
                    "temporal_disambiguation_applied": result.temporal_disambiguation_applied,
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "status": "error",
                    "reason": f"adapter_error:{type(exc).__name__}:{exc}"[:500],
                    "target_memory_id": None,
                    "semantic_candidate_ids": [],
                    "compatible_candidate_ids": [],
                    "rejected_candidates": [],
                    "candidate_scores": [],
                }
            )
    if not rows:
        return {"status": "not_applicable", "resolutions": []}
    status = "resolved" if any(row["status"] == "resolved" for row in rows) else "rejected"
    return {"status": status, "resolutions": rows}


def _resolution_expectation_passes(
    expectation: dict[str, Any] | None, shadow: dict[str, Any]
) -> tuple[bool, str | None]:
    if expectation is None:
        return True, None
    expected_status = expectation.get("status")
    if expectation.get("allow_no_draft") and shadow.get("status") == "not_applicable":
        return True, None
    if expected_status == "resolved":
        resolved = [row for row in shadow.get("resolutions", []) if row.get("status") == "resolved"]
        if len(resolved) < int(expectation.get("min_resolved", 1)):
            return False, "expected_resolved_target_missing"
        target = expectation.get("target_memory_id")
        if target and any(row.get("target_memory_id") != target for row in resolved):
            return False, "resolved_target_mismatch"
        return True, None
    if expected_status == "rejected":
        if shadow.get("status") != "rejected":
            return False, "expected_fail_closed_rejection"
        reason = expectation.get("reason")
        if reason and not any(row.get("reason") == reason for row in shadow.get("resolutions", [])):
            return False, "rejection_reason_mismatch"
    return True, None


def _ambiguous_target_safe(expected: dict[str, Any], shadow: dict[str, Any]) -> bool:
    if not expected.get("ambiguous_target"):
        return True
    # A semantic draft may be emitted for an ambiguous case, but no resolver
    # result may authorize a target.  No draft is also safe by design.
    return shadow.get("status") in {"not_applicable", "rejected"}


def _stage1_rows(extractor: Any) -> list[dict[str, Any]]:
    diagnostic = getattr(extractor, "last_diagnostic", {})
    stage1 = diagnostic.get("stage1", {}) if isinstance(diagnostic, dict) else {}
    rows = stage1.get("propositions", []) if isinstance(stage1, dict) else []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _stage1_origin_index(
    stage1_rows: list[dict[str, Any]],
) -> tuple[dict[str, tuple[str, str]], dict[str, tuple[str, str]]]:
    """Index Stage-1 proposition provenance for evaluator-only alignment.

    ``proposition_origin`` is intentionally a Stage-1 routing signal.  It is
    not part of the frozen ``AtomicClaim`` write contract, so Stage 2 drafts
    are not expected to echo it.  The evaluator therefore joins the two
    stages using the stable proposition id first and the evidence span as a
    conservative fallback.
    """

    by_id: dict[str, tuple[str, str]] = {}
    by_evidence: dict[str, tuple[str, str]] = {}
    for row in stage1_rows:
        origin = str(row.get("proposition_origin") or "uncertain")
        proposition_id = str(row.get("proposition_id") or "").strip()
        evidence = _norm_text(row.get("evidence_span"))
        value = (origin, proposition_id)
        if proposition_id:
            by_id[proposition_id] = value
        if evidence:
            by_evidence[evidence] = value
    return by_id, by_evidence


def _attach_stage1_origins(
    observed: list[dict[str, Any]],
    stage1_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach Stage-1 provenance to normalized rows without changing claims.

    This is deliberately an evaluation join.  It does not mutate the
    production extraction schema, prompt, or persistence payload.
    """

    if not observed or not stage1_rows:
        return observed
    by_id, by_evidence = _stage1_origin_index(stage1_rows)
    used_ids: set[str] = set()
    aligned: list[dict[str, Any]] = []
    for index, row in enumerate(observed):
        item = dict(row)
        raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
        provenance = raw.get("provenance") or raw.get("payload", {}).get("extraction_provenance")
        ids = provenance.get("proposition_ids", []) if isinstance(provenance, dict) else []
        if ids and all(pid in by_id for pid in ids):
            origins = {by_id[pid][0] for pid in ids}
            item["proposition_origin"] = next(iter(origins)) if len(origins) == 1 else "uncertain"
            item["proposition_origin_source"] = "stage1"
            item["proposition_origin_proposition_ids"] = ids
            item["proposition_origin_matched_by"] = "validated_provenance"
            used_ids.update(ids)
            aligned.append(item)
            continue
        claim_id = str(raw.get("claim_id") or raw.get("unit_id") or "").strip()
        evidence_values = item.get("evidence")
        evidence_candidates = (
            evidence_values if isinstance(evidence_values, list) else [evidence_values]
        )
        match: tuple[str, str] | None = None
        matched_by: str | None = None
        if claim_id and claim_id in by_id:
            match = by_id[claim_id]
            matched_by = "proposition_id"
        else:
            for evidence in evidence_candidates:
                normalized = _norm_text(evidence)
                if normalized and normalized in by_evidence:
                    match = by_evidence[normalized]
                    matched_by = "evidence_span"
                    break
        if match is None and index < len(stage1_rows):
            fallback = stage1_rows[index]
            origin = str(fallback.get("proposition_origin") or "uncertain")
            proposition_id = str(fallback.get("proposition_id") or "").strip()
            if proposition_id not in used_ids:
                match = (origin, proposition_id)
                matched_by = "ordinal_fallback"
        if match is not None:
            origin, proposition_id = match
            item["proposition_origin"] = origin
            item["proposition_origin_source"] = "stage1"
            item["proposition_origin_proposition_id"] = proposition_id or None
            item["proposition_origin_matched_by"] = matched_by
            if proposition_id:
                used_ids.add(proposition_id)
        aligned.append(item)
    return aligned


def _norm_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"[\s\-_/，。！？、,.!?()（）:：]+", "", text)


def _value_matches(expected: object, actual: object) -> bool:
    expected_text = _norm_text(expected)
    actual_text = _norm_text(actual)
    aliases = {
        "residence": ("住", "居住", "住在"),
        "occupation": ("工作", "职业", "工程师", "经理"),
        "money": ("钱", "花钱", "消费"),
        "lateness": ("迟到", "晚到"),
        "anger": ("生气", "愤怒", "恼火"),
        "sadness": ("难过", "伤心"),
        "rainy": ("下雨", "雨"),
        "heavy_rain": ("大雨", "下雨特别大", "暴雨"),
        "subway": ("地铁",),
    }
    return (
        expected_text == actual_text
        or expected_text in actual_text
        or any(_norm_text(alias) in actual_text for alias in aliases.get(expected_text, ()))
    )


def _row_matches(expected: dict[str, Any], row: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    for field, value in expected.items():
        options = value if isinstance(value, list) else [value]
        if field == "values_contain":
            matched = any(
                _value_matches(option, actual)
                for option in options
                for actual in row.get("values", [])
            )
        elif field == "attributes_contain":
            matched = any(
                _norm_text(option) in {_norm_text(item) for item in row.get("attributes", [])}
                for option in options
            )
        elif field == "semantic_role":
            actual = row.get("semantic_role")
            matched = any(
                actual == option
                or (option == "contextual_completion" and actual == "attribute_completion")
                for option in options
            )
        elif field in {"is_new_occurrence", "is_contextual_completion", "is_refinement"}:
            matched = any(bool(row.get(field)) is bool(option) for option in options)
        else:
            matched = any(
                row.get(field) == option
                or (field == "values" and _value_matches(option, row.get(field)))
                for option in options
            )
        if not matched:
            failures.append(field)
    return not failures, failures


def _match_required(
    expected: list[dict[str, Any]], observed: list[dict[str, Any]]
) -> tuple[list[dict[str, int]], list[int], list[int], list[str]]:
    matches: list[dict[str, int]] = []
    remaining_expected = set(range(len(expected)))
    remaining_observed = set(range(len(observed)))
    failures: list[str] = []
    for expected_index, requirement in enumerate(expected):
        for observed_index in list(remaining_observed):
            matched, fields = _row_matches(requirement, observed[observed_index])
            if matched:
                matches.append({"expected_index": expected_index, "observed_index": observed_index})
                remaining_expected.discard(expected_index)
                remaining_observed.discard(observed_index)
                break
            failures.extend(f"{field}[{expected_index}]" for field in fields)
    return matches, sorted(remaining_expected), sorted(remaining_observed), sorted(set(failures))


def _forbidden_hits(expected: dict[str, Any], observed: list[dict[str, Any]]) -> list[str]:
    hits: list[str] = []
    for forbidden in expected.get("forbidden", []):
        if (
            (
                forbidden == "enrichment"
                and any(row.get("semantic_type") == "enrichment" for row in observed)
            )
            or (
                forbidden == "event_enrichment"
                and any(row.get("semantic_type") == "enrichment" for row in observed)
            )
            or (
                forbidden == "relationship_state"
                and any(row.get("memory_kind") == "relationship_state" for row in observed)
            )
            or (
                forbidden == "pattern"
                and any(row.get("memory_kind") == "interaction_pattern" for row in observed)
            )
            or (
                forbidden == "confirmed_belief"
                and any(
                    row.get("perspective") == MemoryPerspective.USER_BELIEF.value
                    and row.get("epistemic_status") == "confirmed"
                    for row in observed
                )
            )
        ):
            hits.append(forbidden)
    return hits


def _failure_stage(
    *,
    gate_ok: bool,
    missing: list[int],
    field_failures: list[str],
    forbidden_hits: list[str],
    telemetry: dict[str, Any],
    context_expected: bool | None,
    context_used: bool,
) -> str | None:
    failures = telemetry.get("failure_stages", [])
    if failures and (missing or telemetry.get("failure_count")):
        return failures[0]
    if not gate_ok:
        return "Stage1_gate"
    if context_expected is True and not context_used:
        return "Context_alignment"
    if telemetry.get("failure_count"):
        return "TRANSPORT_OR_RUNTIME_FAILURE"
    if missing:
        fields = " ".join(field_failures)
        if "semantic_role" in fields:
            return "Stage1_semantic_role"
        if "proposition_origin" in fields:
            return "Stage1_proposition_origin"
        return "Stage2_semantic_decomposition"
    if forbidden_hits:
        return "Stage2_forbidden_semantics"
    return None


async def evaluate_context_aware_behavioral_anchor(
    dataset_path: Path = DEFAULT_DATASET_PATH,
    *,
    extractor: Any | None = None,
    output_dir: Path | None = None,
    require_full_suite: bool = True,
    baseline_path: Path | None = DEFAULT_BASELINE_PATH,
    run_label: str = "run1",
) -> dict[str, Any]:
    cases = load_context_aware_cases(dataset_path, require_full_suite=require_full_suite)
    owned_extractor = extractor is None
    if extractor is None:
        settings = get_settings().model_copy(update={"memory_extraction_mode": "two_stage"})
        extractor = _build_memory_extractor(settings)
    rows: list[dict[str, Any]] = []
    try:
        for index, case in enumerate(cases):
            context = _context(case, index)
            attempts: list[Any] = []
            kwargs: dict[str, Any] = {
                "reference_time": REFERENCE_TIME,
                "existing_memories": _memory_items(case, index),
                "conversation_history": _history(case, index),
                "attempt_callback": attempts.append,
            }
            context_used = _supports_keyword(extractor.extract, "conversation_context")
            if context_used:
                kwargs["conversation_context"] = context
            if _supports_keyword(extractor.extract, "pending_memory_context"):
                kwargs["pending_memory_context"] = None
            try:
                extraction = await extractor.extract(case["text"], **kwargs)
                observed = _unit_rows(extraction)
                model_error = None
            except Exception as exc:
                extraction = None
                observed = []
                model_error = f"{type(exc).__name__}: {exc}"[:500]
            semantic_origin_rows = _stage1_rows(extractor)
            observed = _attach_stage1_origins(observed, semantic_origin_rows)
            expected = case["expected_semantics"]
            matches, missing, extra, field_failures = _match_required(
                list(expected.get("required", [])), observed
            )
            expected_unit_count = expected.get("unit_count")
            unit_count_ok = expected_unit_count is None or len(observed) == expected_unit_count
            if not unit_count_ok and expected.get("allow_no_draft") and not observed:
                unit_count_ok = True
            forbidden_hits = _forbidden_hits(expected, observed)
            expected_gate = expected.get("should_extract")
            actual_gate = getattr(extraction, "should_extract", None)
            gate_ok = expected_gate is None or actual_gate is expected_gate
            telemetry = {
                "attempt_count": len(attempts),
                "failure_count": sum(
                    1 for attempt in attempts if getattr(attempt, "status", None) == "failed"
                ),
                "attempts": [
                    attempt.model_dump(mode="json")
                    for attempt in attempts
                    if hasattr(attempt, "model_dump")
                ],
            }
            if model_error:
                telemetry["failure_count"] = telemetry["failure_count"] or 1
                telemetry["error"] = model_error
            telemetry["failure_stages"] = extraction_failure_stages(
                telemetry["attempts"],
                _extractor_diagnostic(extractor),
                runtime_error=model_error,
            )
            context_alignment_expected = case.get("context_alignment_expected")
            shadow = _resolve_shadow(case, observed, index)
            resolution_ok, resolution_failure = _resolution_expectation_passes(
                case.get("resolution_expectation"), shadow
            )
            ambiguity_ok = _ambiguous_target_safe(expected, shadow)
            primary = _failure_stage(
                gate_ok=gate_ok,
                missing=missing,
                field_failures=field_failures,
                forbidden_hits=forbidden_hits,
                telemetry=telemetry,
                context_expected=context_alignment_expected,
                context_used=context_used,
            )
            if primary is None and not resolution_ok:
                primary = "RESOLVER_FAILURE"
            if primary is None and not ambiguity_ok:
                primary = "RESOLVER_FAILURE"
            if primary is None and not unit_count_ok:
                primary = next(iter(telemetry["failure_stages"]), "Stage2_semantic_decomposition")
            # Keep the frozen gold and its strict score intact. Retired
            # operation labels need human review, not a fabricated model error.
            legacy_roles = sorted(
                {
                    role
                    for required in expected.get("required", [])
                    for role in (
                        required.get("semantic_role", [])
                        if isinstance(required.get("semantic_role"), list)
                        else [required.get("semantic_role")]
                    )
                    if role in {"state_update", "pattern_extraction", "belief_extraction"}
                }
            )
            expectation_review = bool(
                legacy_roles
                and primary == "Stage1_semantic_role"
                and all(field.startswith("semantic_role[") for field in field_failures)
                and unit_count_ok
                and not forbidden_hits
            )
            if expectation_review:
                primary = "EVALUATION_EXPECTATION"
            passed = (
                gate_ok
                and not missing
                and unit_count_ok
                and not forbidden_hits
                and not telemetry["failure_count"]
                and resolution_ok
                and ambiguity_ok
                and not case.get("diagnostic_only", False)
            )
            rows.append(
                {
                    "case_id": case["case_id"],
                    "category": case.get("category"),
                    "text": case["text"],
                    "diagnostic_only": bool(case.get("diagnostic_only", False)),
                    "expected_semantics": expected,
                    "observed_semantics": observed,
                    "stage1_propositions": semantic_origin_rows,
                    "semantic_matches": matches,
                    "missing_expected": missing,
                    "extra_observed": extra,
                    "unit_count_ok": unit_count_ok,
                    "field_failures": field_failures,
                    "forbidden_hits": forbidden_hits,
                    "context": context.model_dump(mode="json"),
                    "context_used": context_used,
                    "context_alignment_expected": context_alignment_expected,
                    "context_pair_id": case.get("context_pair_id"),
                    "resolution_expectation": case.get("resolution_expectation"),
                    "resolution": shadow,
                    "resolution_failure": resolution_failure,
                    "ambiguity_safe": ambiguity_ok,
                    "gate": {
                        "should_extract": actual_gate,
                        "reason": (
                            extraction.gate_reason.value
                            if extraction is not None and extraction.gate_reason is not None
                            else None
                        ),
                    },
                    "telemetry": telemetry,
                    "extractor_diagnostic": {
                        "stage1": {
                            key: value
                            for key, value in _extractor_diagnostic(extractor)
                            .get("stage1", {})
                            .items()
                            if key not in {"raw_output"}
                        },
                        "stage2": {
                            key: value
                            for key, value in _extractor_diagnostic(extractor)
                            .get("stage2", {})
                            .items()
                            if key not in {"raw_output"}
                        },
                        "fallback": _extractor_diagnostic(extractor).get("fallback", {}),
                        "final_extractor_used": _extractor_diagnostic(extractor).get(
                            "final_extractor_used"
                        ),
                    },
                    "primary_failure_stage": primary,
                    "needs_review": expectation_review,
                    "expectation_review_reason": (
                        "retired_operation_labels:" + ",".join(legacy_roles)
                        if expectation_review
                        else None
                    ),
                    "secondary_failure_stages": [
                        stage
                        for stage in telemetry["failure_stages"]
                        if primary and stage != primary
                    ],
                    "passed": passed,
                }
            )
    finally:
        if owned_extractor:
            close = getattr(extractor, "aclose", None)
            if callable(close):
                await close()

    scored = [row for row in rows if not row["diagnostic_only"]]
    metrics = _build_metrics(rows, scored)
    report: dict[str, Any] = {
        "evaluation": SUITE_NAME,
        "version": "v0.1",
        "run_label": run_label,
        "dataset": str(dataset_path),
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "mode": "two_stage_shadow",
        "extractor_type": type(extractor).__name__,
        "model_configured": type(extractor).__name__ != "NoOpMemoryExtractor",
        "configuration_status": (
            "configured" if type(extractor).__name__ != "NoOpMemoryExtractor" else "unconfigured"
        ),
        "store_mutation_permitted": False,
        "case_count": len(scored),
        "diagnostic_case_count": len(rows) - len(scored),
        "passed_case_count": sum(bool(row["passed"]) for row in scored),
        "metrics": metrics,
        "cases": rows,
    }
    if baseline_path is not None and baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        old = {str(key): bool(value) for key, value in (baseline.get("case_results") or {}).items()}
        current = {row["case_id"]: bool(row["passed"]) for row in scored}
        common = sorted(set(old) & set(current))
        report["baseline_comparison"] = {
            "status": "compared",
            "old_pass_to_new_fail": [key for key in common if old[key] and not current[key]],
            "old_fail_to_new_pass": [key for key in common if not old[key] and current[key]],
            "unchanged": [key for key in common if old[key] == current[key]],
        }
    if output_dir is not None:
        write_context_aware_artifacts(report, output_dir)
    return report


def _build_metrics(rows: list[dict[str, Any]], scored: list[dict[str, Any]]) -> dict[str, Any]:
    def ratio(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 4) if denominator else 0.0

    required_total = sum(len(row["expected_semantics"].get("required", [])) for row in scored)
    matched_total = sum(len(row["semantic_matches"]) for row in scored)
    unit_expected = [row for row in scored if "unit_count" in row["expected_semantics"]]
    unit_correct = [
        row
        for row in unit_expected
        if len(row["observed_semantics"]) == row["expected_semantics"]["unit_count"]
    ]
    enrichment_cases = [
        row for row in scored if row["expected_semantics"].get("expects_enrichment") is True
    ]
    enrichment_detected = [
        row
        for row in enrichment_cases
        if any(item.get("semantic_type") == "enrichment" for item in row["observed_semantics"])
    ]
    new_event_cases = [
        row for row in scored if row["expected_semantics"].get("new_event_safety") is True
    ]
    false_enrichment = [
        row
        for row in new_event_cases
        if any(item.get("semantic_type") == "enrichment" for item in row["observed_semantics"])
    ]
    ambiguous = [row for row in scored if row["expected_semantics"].get("ambiguous_target") is True]
    rejected_ambiguous = [
        row
        for row in ambiguous
        if row["expected_semantics"].get("allow_no_draft", True)
        and not any(item.get("semantic_type") == "enrichment" for item in row["observed_semantics"])
    ]
    field_accuracy = {
        field: ratio(
            sum(
                not any(field in failure for failure in row["field_failures"])
                for row in scored
                if any(field in item for item in row["expected_semantics"].get("required", []))
            ),
            sum(
                any(field in item for item in row["expected_semantics"].get("required", []))
                for row in scored
            ),
        )
        for field in (
            "semantic_role",
            "memory_kind",
            "event_type",
            "state_dimension",
            "pattern_metric",
            "perspective",
            "proposition_origin",
        )
    }
    categories = defaultdict(int)
    for row in scored:
        if row.get("primary_failure_stage"):
            categories[row["primary_failure_stage"]] += 1
    context_aligned = [row for row in scored if row.get("context_alignment_expected") is not None]
    context_pair_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        pair_id = row.get("context_pair_id")
        if pair_id:
            context_pair_groups[str(pair_id)].append(row)
    context_pairs_with_behavioral_change = [
        group
        for group in context_pair_groups.values()
        if len(
            {
                json.dumps(row["observed_semantics"], ensure_ascii=False, sort_keys=True)
                for row in group
            }
        )
        > 1
    ]
    return {
        "overall_anchor_pass_rate": ratio(sum(row["passed"] for row in scored), len(scored)),
        "semantic_unit_segmentation_accuracy": ratio(len(unit_correct), len(unit_expected)),
        "semantic_role_accuracy": field_accuracy["semantic_role"],
        "proposition_origin_accuracy": field_accuracy["proposition_origin"],
        "question_answer_alignment_accuracy": ratio(
            sum(row["passed"] for row in context_aligned), len(context_aligned)
        ),
        "context_alignment_accuracy": ratio(
            sum(row.get("context_used") is True for row in context_aligned), len(context_aligned)
        ),
        "context_behavioral_change_rate": ratio(
            len(context_pairs_with_behavioral_change),
            sum(len(group) >= 2 for group in context_pair_groups.values()),
        ),
        "memory_kind_accuracy": field_accuracy["memory_kind"],
        "event_type_accuracy": field_accuracy["event_type"],
        "state_dimension_accuracy": field_accuracy["state_dimension"],
        "pattern_metric_accuracy": field_accuracy["pattern_metric"],
        "perspective_accuracy": field_accuracy["perspective"],
        "semantic_coverage": ratio(matched_total, required_total),
        "multi_operation_extraction_accuracy": ratio(
            sum(
                row["passed"]
                for row in scored
                if row["expected_semantics"].get("unit_count", 0) > 1
            ),
            sum(row["expected_semantics"].get("unit_count", 0) > 1 for row in scored),
        ),
        "new_event_safety_accuracy": ratio(
            sum(
                not row["forbidden_hits"]
                and not any(
                    item.get("semantic_type") == "enrichment" for item in row["observed_semantics"]
                )
                for row in new_event_cases
            ),
            len(new_event_cases),
        ),
        "enrichment_detection_precision": ratio(
            sum(row["passed"] for row in enrichment_detected),
            len(enrichment_detected),
        ),
        "enrichment_detection_recall": ratio(len(enrichment_detected), len(enrichment_cases)),
        "false_enrichment_rate": ratio(len(false_enrichment), len(new_event_cases)),
        "new_event_misclassified_as_enrichment_rate": ratio(
            len(false_enrichment), len(new_event_cases)
        ),
        "refinement_vs_update_accuracy": ratio(
            sum(row["passed"] for row in scored if row.get("category") == "refinement_update"),
            sum(row.get("category") == "refinement_update" for row in scored),
        ),
        "ambiguous_target_rejection_rate": ratio(len(rejected_ambiguous), len(ambiguous)),
        "multi_round_enrichment_consistency": _multi_round_consistency(scored),
        "permutation_diagnostic_consistency": _permutation_consistency(rows),
        "ambiguous_target_safe_count": sum(
            bool(row.get("ambiguity_safe"))
            for row in scored
            if row["expected_semantics"].get("ambiguous_target")
        ),
        "resolver_rejection_count": sum(
            row.get("resolution", {}).get("status") == "rejected" for row in scored
        ),
        "forbidden_semantic_violation_rate": ratio(
            sum(bool(row["forbidden_hits"]) for row in scored), len(scored)
        ),
        "model_parse_failure_count": sum(
            "MODEL_PARSE_FAILURE" in row["telemetry"].get("failure_stages", []) for row in rows
        ),
        "schema_validation_failure_count": sum(
            "SCHEMA_VALIDATION_FAILURE" in row["telemetry"].get("failure_stages", [])
            for row in rows
        ),
        "primary_failure_stage_counts": dict(sorted(categories.items())),
    }


def _multi_round_consistency(rows: list[dict[str, Any]]) -> float:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        group = row["expected_semantics"].get("round_group")
        if group:
            grouped[str(group)].append(row)
    if not grouped:
        return 0.0
    consistent = sum(
        int(all(row.get("passed") for row in group_rows)) for group_rows in grouped.values()
    )
    return round(consistent / len(grouped), 4)


def _permutation_consistency(rows: list[dict[str, Any]]) -> float:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not row.get("diagnostic_only"):
            continue
        pair_id = row.get("context_pair_id")
        if pair_id:
            grouped[str(pair_id)].append(row)
    if not grouped:
        return 0.0
    stable = 0
    for group_rows in grouped.values():
        expected_counts = {
            row["expected_semantics"].get("unit_count")
            for row in group_rows
            if row["expected_semantics"].get("unit_count") is not None
        }
        observed_counts = {len(row["observed_semantics"]) for row in group_rows}
        stable += int(
            all(row.get("passed") for row in group_rows)
            and (not expected_counts or observed_counts == expected_counts)
        )
    return round(stable / len(grouped), 4)


def write_context_aware_artifacts(report: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw = output_dir / "raw_results.jsonl"
    normalized = output_dir / "normalized_results.jsonl"
    failures = output_dir / "failures.jsonl"
    summary = output_dir / "summary.md"
    raw.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in report["cases"]),
        encoding="utf-8",
    )
    normalized.write_text(
        "".join(
            json.dumps(
                {
                    "case_id": row["case_id"],
                    "category": row["category"],
                    "passed": row["passed"],
                    "primary_failure_stage": row["primary_failure_stage"],
                    "observed_semantics": row["observed_semantics"],
                },
                ensure_ascii=False,
            )
            + "\n"
            for row in report["cases"]
        ),
        encoding="utf-8",
    )
    failures.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False) + "\n"
            for row in report["cases"]
            if not row["passed"] and not row["diagnostic_only"]
        ),
        encoding="utf-8",
    )
    summary.write_text(render_context_aware_summary(report), encoding="utf-8")
    return {"summary": summary, "raw": raw, "normalized": normalized, "failures": failures}


def render_context_aware_summary(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# Context-Aware Behavioral Anchor v0.1",
        "",
        f"Dataset: `{report['dataset']}`",
        f"Run: `{report.get('run_label', '-')}`",
        "Store mutation permitted: `False`",
        "",
        "## Metrics",
        "",
        "| Metric | Result |",
        "|---|---:|",
    ]
    lines.extend(f"| {key} | {value} |" for key, value in metrics.items())
    lines.extend(
        [
            "",
            f"Scored: `{report['passed_case_count']}/{report['case_count']}` cases passed.",
            "",
            "## Failure attribution",
            "",
            "| Case | Category | Result | Primary stage |",
            "|---|---|---|---|",
        ]
    )
    for row in report["cases"]:
        if row["diagnostic_only"]:
            continue
        lines.append(
            f"| {row['case_id']} | {row['category'] or '-'} | "
            f"{'PASS' if row['passed'] else 'FAIL'} | {row['primary_failure_stage'] or '-'} |"
        )
    return "\n".join(lines) + "\n"


def compare_context_aware_runs(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    def semantic_signature(row: dict[str, Any]) -> list[dict[str, Any]]:
        fields = (
            "semantic_role",
            "semantic_type",
            "memory_kind",
            "event_type",
            "pattern_metric",
            "state_dimension",
            "subject",
            "perspective",
            "attribute",
            "attributes",
            "values",
            "is_new_occurrence",
            "is_contextual_completion",
            "is_refinement",
            "proposition_origin",
        )
        return [
            {key: item.get(key) for key in fields} for item in row.get("observed_semantics", [])
        ]

    left = {row["case_id"]: row for row in first.get("cases", [])}
    right = {row["case_id"]: row for row in second.get("cases", [])}
    common = sorted(set(left) & set(right))
    relation_drift: list[str] = []
    pass_drift: list[str] = []
    for case_id in common:
        if left[case_id].get("passed") != right[case_id].get("passed"):
            pass_drift.append(case_id)
        if semantic_signature(left[case_id]) != semantic_signature(right[case_id]):
            relation_drift.append(case_id)
    stable = [case_id for case_id in common if case_id not in relation_drift]
    return {
        "run_count": 2,
        "common_case_count": len(common),
        "drift_case_ids": relation_drift,
        "pass_drift_case_ids": pass_drift,
        "stable_case_ids": stable,
        "stable_passes": sum(
            bool(left[case_id].get("passed")) and bool(right[case_id].get("passed"))
            for case_id in common
            if case_id not in relation_drift
        ),
        "stable_failures": sum(
            not left[case_id].get("passed") and not right[case_id].get("passed")
            for case_id in common
            if case_id not in relation_drift
        ),
        "drift_rate": round(len(relation_drift) / len(common), 4) if common else 0.0,
        "semantic_signature": "normalized_semantic_fields_without_raw_model_payload",
    }


def write_run_comparison(comparison: dict[str, Any], output_dir: Path) -> Path:
    path = output_dir / "run_comparison.md"
    drift = ", ".join(comparison.get("drift_case_ids", [])) or "-"
    path.write_text(
        "# Context-Aware Behavioral Anchor Run Comparison\n\n"
        f"Drift cases: `{drift}`\n"
        f"Drift rate: `{comparison.get('drift_rate', 0)}`\n"
        f"Stable passes: `{comparison.get('stable_passes', 0)}`\n"
        f"Stable failures: `{comparison.get('stable_failures', 0)}`\n",
        encoding="utf-8",
    )
    return path


def write_context_aware_baseline(report: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "suite": report["evaluation"],
        "version": report["version"],
        "baseline_status": "established",
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": report["dataset"],
        "dataset_sha256": report["dataset_sha256"],
        "case_count": report["case_count"],
        "metrics": report["metrics"],
        "case_results": {
            row["case_id"]: bool(row["passed"])
            for row in report["cases"]
            if not row["diagnostic_only"]
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


__all__ = [
    "DEFAULT_BASELINE_PATH",
    "DEFAULT_CONTEXT_AWARE_BASELINE_PATH",
    "DEFAULT_DATASET_PATH",
    "DEFAULT_LEGACY_BEHAVIORAL_BASELINE_PATH",
    "DEFAULT_OUTPUT_DIR",
    "REFERENCE_TIME",
    "SUITE_NAME",
    "compare_context_aware_runs",
    "evaluate_context_aware_behavioral_anchor",
    "load_context_aware_cases",
    "render_context_aware_summary",
    "write_context_aware_artifacts",
    "write_context_aware_baseline",
    "write_run_comparison",
]

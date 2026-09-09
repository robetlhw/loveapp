"""Live Extraction/Normalization diagnostic for the Ontology Sanity draft Gold.

The evaluator calls the configured production extractor, then applies the
production deterministic normalization contract in memory.  It never creates
a MemoryService or Store and therefore cannot mutate application data.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from loveapp.application.memory import atomize_candidates
from loveapp.domain.memory import AtomicExtraction, MemoryCandidate, MemoryExtractionAttempt
from loveapp.domain.memory_dimensions import (
    infer_interaction_event_type,
    interaction_source_for_perspective,
    normalize_interaction_event_type,
    normalize_interaction_metric,
    normalize_interaction_source,
)
from loveapp.domain.memory_normalization import (
    NormalizationContractError,
    normalize_memory_candidate_contract,
)
from loveapp.domain.memory_predicates import normalize_predicate

EXPECTED_GROUP_COUNTS = {
    "stable_fact": 8,
    "preference": 8,
    "interaction_event": 8,
    "interaction_pattern": 8,
    "boundary": 8,
}
CONSTRAINT_FIELDS = (
    "memory_kind",
    "subject",
    "perspective",
    "predicate_type",
    "canonical_predicate",
    "custom_predicate",
    "event_type",
    "milestone_type",
    "metric",
    "source",
    "value",
)
FAILURE_STAGES = frozenset(
    {
        "EXTRACTION_KIND_ERROR",
        "EXTRACTION_SUBJECT_ERROR",
        "EXTRACTION_PERSPECTIVE_ERROR",
        "EXTRACTION_ATOMICITY_ERROR",
        "EXTRACTION_SEMANTIC_LOSS",
        "NORMALIZATION_KIND_ERROR",
        "NORMALIZATION_CANONICAL_MAPPING_ERROR",
        "NORMALIZATION_CUSTOM_FALLBACK_ERROR",
        "NORMALIZATION_EVENT_TYPE_ERROR",
        "NORMALIZATION_SCOPE_ERROR",
        "FORBIDDEN_CLAIM_EMITTED",
        "MULTI_CLAIM_MISSING",
        "OTHER",
    }
)
HIGH_RISK_CASE_IDS = frozenset(
    {
        "sf_004",
        "pref_006",
        "event_003",
        "pattern_002",
        "pattern_004",
        "pattern_006",
        "bd_001",
        "bd_002",
        "bd_006",
        "bd_007",
        "bd_008",
    }
)
DRAFT_POLICY_SUSPECT_NOTES = {
    "bd_001": (
        "The current schema has no belief MemoryKind, so a Custom proposition stored with "
        "perspective=user_belief and objective_fact=false can be defensible even though the "
        "draft Gold forbids stable_fact. Review the storage-kind boundary, not the model output "
        "alone."
    ),
    "bd_004": (
        "The model emitted the bounded exception as a separate relationship Event, while the "
        "draft Gold assigns that non-action to partner. Subject ownership for an omitted contact "
        "is semantically debatable."
    ),
    "bd_007": (
        "Both the first shared-trip Event and recurring travel Pattern were extracted. The first "
        "occurrence survives in predicate, summary, and novelty, but draft Gold additionally "
        "requires a milestone_type field that the current production contract does not define."
    ),
    "event_003": (
        "The underlying date Event and first-occurrence meaning are preserved in event_type, "
        "predicate, summary, and novelty. Draft Gold additionally requires milestone_type, which "
        "is not currently a production extraction field."
    ),
    "event_005": (
        "The draft Gold treats a partner-provided support event as relationship-subject, while "
        "the current subject hard contrast selects the single acting partner. Both readings are "
        "semantically defensible."
    ),
    "event_008": (
        "A one-off user-only basketball session is clearly not a durable Preference, but whether "
        "it is valuable enough to persist as an interaction Event needs an explicit policy call."
    ),
    "pref_007": (
        "Wanting to be heard while distressed can reasonably be classified as either a generic "
        "emotional need or a communication style; the draft Gold currently chooses the former."
    ),
}


class OntologyClaimConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_kind: str | None = None
    subject: str | None = None
    perspective: str | None = None
    predicate_type: str | None = None
    canonical_predicate: str | None = None
    custom_predicate: str | None = None
    event_type: str | None = None
    milestone_type: str | None = None
    metric: str | None = None
    source: str | None = None
    value: str | None = None

    def fields(self) -> dict[str, str]:
        return self.model_dump(exclude_none=True)


class OntologySanityAnnotation(BaseModel):
    model_config = ConfigDict(extra="allow")

    label_source: str
    review_status: str
    policy_version: str


class OntologySanityCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    case_id: str = Field(pattern=r"^(?:sf|pref|event|pattern|bd)_\d{3}$")
    group: str
    text: str = Field(min_length=1)
    expected_claims: list[OntologyClaimConstraint] = Field(min_length=1)
    forbidden: list[OntologyClaimConstraint] = Field(default_factory=list)
    notes: str = ""
    annotation: OntologySanityAnnotation

    @model_validator(mode="after")
    def validate_group(self) -> OntologySanityCase:
        if self.group not in EXPECTED_GROUP_COUNTS:
            raise ValueError(f"unsupported ontology sanity group: {self.group}")
        return self


def load_ontology_sanity_cases(
    path: Path,
    *,
    require_complete: bool = True,
) -> list[OntologySanityCase]:
    cases: list[OntologySanityCase] = []
    seen: set[str] = set()
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(),
        1,
    ):
        if not line.strip():
            continue
        try:
            case = OntologySanityCase.model_validate_json(line)
        except Exception as exc:
            raise ValueError(
                f"invalid Ontology Sanity case on line {line_number}: {exc}"
            ) from exc
        if case.case_id in seen:
            raise ValueError(f"duplicate Ontology Sanity case_id: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError("Ontology Sanity dataset is empty")
    if require_complete:
        counts = Counter(case.group for case in cases)
        if len(cases) != 40 or dict(counts) != EXPECTED_GROUP_COUNTS:
            raise ValueError(
                "Ontology Sanity dataset must contain 40 cases with group counts "
                f"{EXPECTED_GROUP_COUNTS}; actual={dict(counts)}"
            )
    return cases


async def evaluate_memory_ontology_sanity(
    dataset_path: Path,
    *,
    extractor: Any,
    reference_time: datetime,
    case_id: str | None = None,
    fail_on_error: bool = False,
) -> dict[str, Any]:
    """Evaluate live extraction and deterministic normalization separately."""

    all_cases = load_ontology_sanity_cases(
        dataset_path,
        require_complete=case_id is None,
    )
    cases = [case for case in all_cases if case_id is None or case.case_id == case_id]
    if not cases:
        raise ValueError(f"no Ontology Sanity cases matched case_id={case_id!r}")

    rows: list[dict[str, Any]] = []
    for case in cases:
        try:
            rows.append(
                await _evaluate_case(
                    case,
                    extractor=extractor,
                    reference_time=reference_time,
                )
            )
        except Exception as exc:
            if fail_on_error:
                raise
            rows.append(_failed_case(case, exc))

    metrics = _aggregate_metrics(rows)
    by_group = {
        group: _aggregate_metrics([row for row in rows if row["group"] == group])
        for group in EXPECTED_GROUP_COUNTS
        if any(row["group"] == group for row in rows)
    }
    attempts = [attempt for row in rows for attempt in row.get("attempts", [])]
    return {
        "evaluation": "memory_ontology_sanity_v0_1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "dataset": str(dataset_path),
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "policy_version": "memory_ontology_v3_policy_v1",
        "gold_review_status": "draft",
        "case_filter": case_id,
        "case_count": len(rows),
        "model": _extractor_model(extractor),
        "reference_time": reference_time.isoformat(),
        "store_mutation_permitted": False,
        "relation_or_lifecycle_evaluated": False,
        "matching_contract": (
            "partial expected constraints; one-to-one expected/actual matching; "
            "forbidden objects are prohibited partial matches"
        ),
        "metrics": metrics,
        "groups": by_group,
        "error_taxonomy": dict(
            sorted(
                Counter(
                    row["first_failure_stage"]
                    for row in rows
                    if row["first_failure_stage"]
                ).items()
            )
        ),
        "telemetry": _attempt_telemetry(attempts),
        "high_risk_case_ids": sorted(HIGH_RISK_CASE_IDS),
        "cases": rows,
    }


async def _evaluate_case(
    case: OntologySanityCase,
    *,
    extractor: Any,
    reference_time: datetime,
) -> dict[str, Any]:
    attempts: list[MemoryExtractionAttempt] = []
    try:
        extraction: AtomicExtraction = await extractor.extract(
            case.text,
            reference_time=reference_time,
            existing_memories=[],
            conversation_history=[],
            pending_memory_context=None,
            attempt_callback=attempts.append,
        )
    except Exception as exc:
        return _failed_case(case, exc, attempts=attempts)
    extracted_candidates = [
        claim.to_candidate().model_copy(update={"original_text": case.text})
        for claim in extraction.claims
    ]
    extracted_claims = [
        _candidate_record(candidate, stage="extraction", source_index=index)
        for index, candidate in enumerate(extracted_candidates)
    ]

    normalized_claims: list[dict[str, Any]] = []
    normalization_errors: list[dict[str, Any]] = []
    for source_index, candidate in enumerate(atomize_candidates(extracted_candidates)):
        try:
            normalized = normalize_memory_candidate_contract(
                candidate,
                reference_time,
                allow_legacy_open_world=True,
            )
        except Exception as exc:
            normalization_errors.append(
                {
                    "source_index": source_index,
                    "error_type": type(exc).__name__,
                    "error_code": (
                        exc.code if isinstance(exc, NormalizationContractError) else None
                    ),
                    "error": str(exc)[:1000],
                }
            )
            continue
        normalized_claims.append(
            _candidate_record(
                normalized,
                stage="normalized",
                source_index=source_index,
            )
        )

    expected = [claim.fields() for claim in case.expected_claims]
    forbidden = [claim.fields() for claim in case.forbidden]
    extraction_result = _evaluate_stage(
        expected,
        forbidden,
        extracted_claims,
        stage="extraction",
    )
    normalized_result = _evaluate_stage(
        expected,
        forbidden,
        normalized_claims,
        stage="normalized",
    )
    first_failure = _first_failure_stage(
        expected,
        extracted_claims,
        normalized_claims,
        extraction_result,
        normalized_result,
        normalization_errors,
        attempts,
    )
    if first_failure is not None and first_failure not in FAILURE_STAGES:
        first_failure = "OTHER"
    short_reason = _short_reason(
        extraction_result,
        normalized_result,
        normalization_errors,
        attempts,
    )
    return {
        "case_id": case.case_id,
        "group": case.group,
        "text": case.text,
        "passed_extraction": extraction_result["passed"],
        "passed_normalized": normalized_result["passed"],
        "first_failure_stage": first_failure,
        "expected_claims": expected,
        "extracted_claims": extracted_claims,
        "normalized_claims": normalized_claims,
        "forbidden": forbidden,
        "forbidden_hits": {
            "extraction": extraction_result["forbidden_hits"],
            "normalized": normalized_result["forbidden_hits"],
        },
        "extraction_evaluation": extraction_result,
        "normalization_evaluation": normalized_result,
        "normalization_errors": normalization_errors,
        "extractor_attempt_failures": _attempt_failure_details(attempts),
        "attempts": [attempt.model_dump(mode="json") for attempt in attempts],
        "semantic_gate": {
            "should_extract": extraction.should_extract,
            "gate_reason": (
                extraction.gate_reason.value if extraction.gate_reason is not None else None
            ),
        },
        "notes": short_reason,
        "gold_notes": case.notes,
        "annotation": case.annotation.model_dump(mode="json"),
        "high_risk": case.case_id in HIGH_RISK_CASE_IDS,
    }


def _candidate_record(
    candidate: MemoryCandidate,
    *,
    stage: str,
    source_index: int,
) -> dict[str, Any]:
    payload = dict(candidate.payload)
    predicate = normalize_predicate(
        kind=candidate.kind,
        raw_predicate=candidate.raw_predicate or payload.get("predicate"),
        canonical_predicate=candidate.canonical_predicate,
        custom_predicate=candidate.custom_predicate,
        predicate_type=candidate.predicate_type,
        payload=payload,
    )
    direct_event_type = normalize_interaction_event_type(payload.get("event_type"))
    event_type_hint = direct_event_type or infer_interaction_event_type(
        payload,
        evidence_text=" ".join(candidate.evidence_spans) or candidate.original_text,
    )
    direct_metric = normalize_interaction_metric(payload.get("metric"))
    metric_hint = direct_metric or _metric_from_canonical(predicate.canonical_predicate)
    source = normalize_interaction_source(payload.get("source"))
    source_hint = source or interaction_source_for_perspective(candidate.perspective)
    values = _candidate_values(candidate)
    return {
        "source_index": source_index,
        "stage": stage,
        "memory_kind": candidate.kind.value,
        "subject": candidate.subject,
        "perspective": candidate.perspective.value,
        "predicate_type": candidate.predicate_type.value,
        "raw_predicate": candidate.raw_predicate,
        "canonical_predicate": candidate.canonical_predicate,
        "custom_predicate": candidate.custom_predicate,
        "semantic_canonical_hint": predicate.canonical_predicate,
        "event_type": direct_event_type,
        "semantic_event_type_hint": event_type_hint,
        "milestone_type": payload.get("milestone_type"),
        "metric": direct_metric,
        "semantic_metric_hint": metric_hint,
        "source": source,
        "semantic_source_hint": source_hint,
        "value": values[0] if values else None,
        "value_candidates": values,
        "summary": candidate.summary,
        "evidence_spans": list(candidate.evidence_spans),
        "confidence": candidate.confidence,
        "payload": payload,
    }


def _evaluate_stage(
    expected: list[dict[str, str]],
    forbidden: list[dict[str, str]],
    actual: list[dict[str, Any]],
    *,
    stage: str,
) -> dict[str, Any]:
    matches = _maximum_full_matching(expected, actual, stage=stage)
    unmatched_expected = [index for index in range(len(expected)) if index not in matches]
    unmatched_actual = [index for index in range(len(actual)) if index not in matches.values()]
    forbidden_hits = _forbidden_hits(forbidden, actual, stage=stage)
    nearest = [
        _nearest_claim(expected[index], actual, stage=stage, expected_index=index)
        for index in unmatched_expected
    ]
    return {
        "passed": not unmatched_expected and not forbidden_hits,
        "matched_pairs": [
            {"expected_index": expected_index, "actual_index": actual_index}
            for expected_index, actual_index in sorted(matches.items())
        ],
        "unmatched_expected_indices": unmatched_expected,
        "unmatched_actual_indices": unmatched_actual,
        "nearest_mismatches": nearest,
        "forbidden_hits": forbidden_hits,
    }


def _maximum_full_matching(
    expected: list[dict[str, str]],
    actual: list[dict[str, Any]],
    *,
    stage: str,
) -> dict[int, int]:
    compatibility = {
        expected_index: [
            actual_index
            for actual_index, claim in enumerate(actual)
            if _constraint_matches(constraint, claim, stage=stage, allow_hints=True)
        ]
        for expected_index, constraint in enumerate(expected)
    }
    best: dict[int, int] = {}

    def search(index: int, used: set[int], current: dict[int, int]) -> None:
        nonlocal best
        if index == len(expected):
            if len(current) > len(best):
                best = dict(current)
            return
        if len(current) + len(expected) - index <= len(best):
            return
        search(index + 1, used, current)
        for actual_index in compatibility[index]:
            if actual_index in used:
                continue
            used.add(actual_index)
            current[index] = actual_index
            search(index + 1, used, current)
            current.pop(index)
            used.remove(actual_index)

    search(0, set(), {})
    return best


def _constraint_matches(
    constraint: dict[str, str],
    claim: dict[str, Any],
    *,
    stage: str,
    allow_hints: bool,
) -> bool:
    return all(
        _field_matches(
            field,
            expected,
            claim,
            stage=stage,
            allow_hints=allow_hints,
        )
        for field, expected in constraint.items()
    )


def _field_matches(
    field: str,
    expected: str,
    claim: dict[str, Any],
    *,
    stage: str,
    allow_hints: bool,
) -> bool:
    if field == "value":
        return any(_value_equal(expected, value) for value in claim.get("value_candidates", []))
    if field == "custom_predicate":
        actual = claim.get("custom_predicate")
        return _identifier_equal(expected, actual)
    if field == "predicate_type":
        actual = claim.get("predicate_type")
        if expected == "custom":
            return actual == "custom" and bool(
                claim.get("custom_predicate") or claim.get("raw_predicate")
            )
        if expected == "canonical" and actual == "canonical":
            return True
        return bool(
            stage == "extraction"
            and allow_hints
            and expected == "canonical"
            and claim.get("semantic_canonical_hint")
        )
    if field == "canonical_predicate":
        actual = claim.get(field)
        if actual is not None:
            return _identifier_equal(expected, actual)
        return bool(
            stage == "extraction"
            and allow_hints
            and _identifier_equal(expected, claim.get("semantic_canonical_hint"))
        )
    if field == "event_type":
        actual = claim.get(field)
        if actual is not None:
            return _identifier_equal(expected, actual)
        return bool(
            stage == "extraction"
            and allow_hints
            and _identifier_equal(expected, claim.get("semantic_event_type_hint"))
        )
    if field == "metric":
        actual = claim.get(field)
        if actual is not None:
            return _identifier_equal(expected, actual)
        return bool(
            stage == "extraction"
            and allow_hints
            and _identifier_equal(expected, claim.get("semantic_metric_hint"))
        )
    if field == "source":
        actual = claim.get(field)
        if actual is not None:
            return _identifier_equal(expected, actual)
        return bool(
            stage == "extraction"
            and allow_hints
            and _identifier_equal(expected, claim.get("semantic_source_hint"))
        )
    return _identifier_equal(expected, claim.get(field))


def _forbidden_hits(
    forbidden: list[dict[str, str]],
    actual: list[dict[str, Any]],
    *,
    stage: str,
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for forbidden_index, constraint in enumerate(forbidden):
        for actual_index, claim in enumerate(actual):
            if _constraint_matches(
                constraint,
                claim,
                stage=stage,
                allow_hints=False,
            ):
                hits.append(
                    {
                        "forbidden_index": forbidden_index,
                        "actual_index": actual_index,
                        "constraint": constraint,
                    }
                )
    return hits


def _nearest_claim(
    expected: dict[str, str],
    actual: list[dict[str, Any]],
    *,
    stage: str,
    expected_index: int,
) -> dict[str, Any]:
    if not actual:
        return {
            "expected_index": expected_index,
            "actual_index": None,
            "matched_fields": [],
            "mismatched_fields": list(expected),
        }
    scored = []
    for actual_index, claim in enumerate(actual):
        matched = [
            field
            for field, value in expected.items()
            if _field_matches(field, value, claim, stage=stage, allow_hints=True)
        ]
        scored.append((len(matched), -actual_index, actual_index, matched))
    _, _, actual_index, matched = max(scored)
    return {
        "expected_index": expected_index,
        "actual_index": actual_index,
        "matched_fields": matched,
        "mismatched_fields": [field for field in expected if field not in matched],
    }


def _first_failure_stage(
    expected: list[dict[str, str]],
    extracted: list[dict[str, Any]],
    normalized: list[dict[str, Any]],
    extraction_result: dict[str, Any],
    normalized_result: dict[str, Any],
    normalization_errors: list[dict[str, Any]],
    attempts: list[MemoryExtractionAttempt] | None = None,
) -> str | None:
    if extraction_result["passed"] and normalized_result["passed"]:
        return None
    if not extraction_result["passed"]:
        if _attempt_failure_details(attempts or []):
            return "OTHER"
        if _attempt_reports_atomicity_violation(attempts or []):
            return "EXTRACTION_ATOMICITY_ERROR"
        if extraction_result["forbidden_hits"]:
            return "FORBIDDEN_CLAIM_EMITTED"
        if not extracted:
            return "EXTRACTION_SEMANTIC_LOSS"
        if len(expected) > 1 and any(
            not _has_identity_candidate(expected[index], extracted, stage="extraction")
            for index in extraction_result["unmatched_expected_indices"]
        ):
            return "MULTI_CLAIM_MISSING"
        if len(expected) > 1 and len(extracted) < len(expected):
            return "MULTI_CLAIM_MISSING"
        mismatches = _mismatch_fields(extraction_result)
        if "memory_kind" in mismatches:
            return "EXTRACTION_KIND_ERROR"
        if "subject" in mismatches:
            return "EXTRACTION_SUBJECT_ERROR"
        if "perspective" in mismatches:
            return "EXTRACTION_PERSPECTIVE_ERROR"
        return "EXTRACTION_SEMANTIC_LOSS"

    if normalized_result["forbidden_hits"]:
        return "FORBIDDEN_CLAIM_EMITTED"
    if normalization_errors and not normalized:
        error_text = " ".join(
            str(error.get("error_code") or error.get("error") or "")
            for error in normalization_errors
        ).casefold()
        if "custom" in error_text:
            custom_expected = any(
                constraint.get("predicate_type") == "custom" for constraint in expected
            )
            return (
                "NORMALIZATION_CUSTOM_FALLBACK_ERROR"
                if custom_expected
                else "NORMALIZATION_CANONICAL_MAPPING_ERROR"
            )
        if "canonical" in error_text or "predicate" in error_text:
            return "NORMALIZATION_CANONICAL_MAPPING_ERROR"
        return "OTHER"
    mismatches = _mismatch_fields(normalized_result)
    if "memory_kind" in mismatches:
        return "NORMALIZATION_KIND_ERROR"
    if "predicate_type" in mismatches:
        custom_expected = any(
            constraint.get("predicate_type") == "custom" for constraint in expected
        )
        return (
            "NORMALIZATION_CUSTOM_FALLBACK_ERROR"
            if custom_expected
            else "NORMALIZATION_CANONICAL_MAPPING_ERROR"
        )
    if {"canonical_predicate", "custom_predicate"} & mismatches:
        return "NORMALIZATION_CANONICAL_MAPPING_ERROR"
    if {"event_type", "milestone_type"} & mismatches:
        return "NORMALIZATION_EVENT_TYPE_ERROR"
    if {"subject", "perspective", "source"} & mismatches:
        return "NORMALIZATION_SCOPE_ERROR"
    return "OTHER"


def _short_reason(
    extraction_result: dict[str, Any],
    normalized_result: dict[str, Any],
    normalization_errors: list[dict[str, Any]],
    attempts: list[MemoryExtractionAttempt] | None = None,
) -> str:
    if extraction_result["passed"] and normalized_result["passed"]:
        return "Extraction and normalization satisfy all partial constraints."
    parts: list[str] = []
    if not extraction_result["passed"]:
        parts.append(_stage_reason("extraction", extraction_result))
    if not normalized_result["passed"]:
        parts.append(_stage_reason("normalization", normalized_result))
    if normalization_errors:
        parts.append(
            "normalization_errors="
            + ",".join(
                str(error.get("error_code") or error.get("error_type"))
                for error in normalization_errors
            )
        )
    attempt_failures = _attempt_failure_details(attempts or [])
    if attempt_failures:
        parts.append(
            "extractor_attempt_failures="
            + ",".join(
                ":".join(
                    str(value)
                    for value in (
                        failure.get("tier") or "unknown",
                        failure.get("failure_category") or "unknown",
                        failure.get("error") or "unknown",
                    )
                )
                for failure in attempt_failures
            )
        )
    return "; ".join(parts)


def _stage_reason(name: str, result: dict[str, Any]) -> str:
    details: list[str] = []
    if result["unmatched_expected_indices"]:
        details.append(f"unmatched_expected={result['unmatched_expected_indices']}")
    mismatches = sorted(_mismatch_fields(result))
    if mismatches:
        details.append("fields=" + ",".join(mismatches))
    if result["forbidden_hits"]:
        details.append(f"forbidden_hits={len(result['forbidden_hits'])}")
    return f"{name}:" + (" ".join(details) if details else "failed")


def _mismatch_fields(result: dict[str, Any]) -> set[str]:
    return {
        field
        for nearest in result["nearest_mismatches"]
        for field in nearest["mismatched_fields"]
    }


def _has_identity_candidate(
    expected: dict[str, str],
    actual: list[dict[str, Any]],
    *,
    stage: str,
) -> bool:
    if "memory_kind" in expected:
        return any(
            _field_matches(
                "memory_kind",
                expected["memory_kind"],
                claim,
                stage=stage,
                allow_hints=True,
            )
            for claim in actual
        )
    identity_fields = [
        field for field in ("subject", "perspective") if field in expected
    ]
    return bool(identity_fields) and any(
        all(
            _field_matches(
                field,
                expected[field],
                claim,
                stage=stage,
                allow_hints=True,
            )
            for field in identity_fields
        )
        for claim in actual
    )


def _aggregate_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"case_count": 0}
    extraction_fields = _field_metrics(rows, stage="extraction")
    normalized_fields = _field_metrics(rows, stage="normalized")
    extraction_passed = sum(bool(row["passed_extraction"]) for row in rows)
    normalized_passed = sum(bool(row["passed_normalized"]) for row in rows)
    multi_rows = [row for row in rows if len(row["expected_claims"]) > 1]
    forbidden_violations = sum(
        bool(row["forbidden_hits"]["extraction"] or row["forbidden_hits"]["normalized"])
        for row in rows
    )
    return {
        "case_count": len(rows),
        "extraction_pass_count": extraction_passed,
        "extraction_pass_rate": _ratio(extraction_passed, len(rows)),
        "normalized_pass_count": normalized_passed,
        "normalized_pass_rate": _ratio(normalized_passed, len(rows)),
        "extraction": extraction_fields,
        "normalized": normalized_fields,
        "kind_accuracy": normalized_fields["kind_accuracy"],
        "subject_accuracy": normalized_fields["subject_accuracy"],
        "perspective_accuracy": normalized_fields["perspective_accuracy"],
        "canonical_custom_accuracy": normalized_fields[
            "canonical_custom_accuracy"
        ],
        "event_type_accuracy": normalized_fields["event_type_accuracy"],
        "pattern_metric_accuracy": normalized_fields["pattern_metric_accuracy"],
        "multi_claim_case_count": len(multi_rows),
        "multi_claim_completeness": _ratio(
            sum(
                not row["normalization_evaluation"]["unmatched_expected_indices"]
                for row in multi_rows
            ),
            len(multi_rows),
        ),
        "forbidden_claim_violation_count": forbidden_violations,
        "forbidden_claim_violation_rate": _ratio(forbidden_violations, len(rows)),
    }


def _field_metrics(rows: list[dict[str, Any]], *, stage: str) -> dict[str, Any]:
    specs = {
        "kind_accuracy": ("memory_kind",),
        "subject_accuracy": ("subject",),
        "perspective_accuracy": ("perspective",),
        "canonical_custom_accuracy": (
            "predicate_type",
            "canonical_predicate",
            "custom_predicate",
        ),
        "event_type_accuracy": ("event_type", "milestone_type"),
        "pattern_metric_accuracy": ("metric",),
        "value_accuracy": ("value",),
    }
    totals = {name: [0, 0] for name in specs}
    for row in rows:
        expected = row["expected_claims"]
        actual = row["extracted_claims" if stage == "extraction" else "normalized_claims"]
        assignment = _maximum_weight_assignment(expected, actual, stage=stage)
        for expected_index, constraint in enumerate(expected):
            claim = actual[assignment[expected_index]] if expected_index in assignment else None
            for name, fields in specs.items():
                present = [field for field in fields if field in constraint]
                if not present:
                    continue
                totals[name][1] += 1
                if claim is not None and all(
                    _field_matches(
                        field,
                        constraint[field],
                        claim,
                        stage=stage,
                        allow_hints=True,
                    )
                    for field in present
                ):
                    totals[name][0] += 1
    return {
        name: {
            "correct": correct,
            "total": total,
            "rate": _ratio(correct, total),
        }
        for name, (correct, total) in totals.items()
    }


def _maximum_weight_assignment(
    expected: list[dict[str, str]],
    actual: list[dict[str, Any]],
    *,
    stage: str,
) -> dict[int, int]:
    if not expected or not actual:
        return {}
    weights = {
        (expected_index, actual_index): sum(
            _field_matches(
                field,
                value,
                claim,
                stage=stage,
                allow_hints=True,
            )
            for field, value in constraint.items()
        )
        for expected_index, constraint in enumerate(expected)
        for actual_index, claim in enumerate(actual)
    }
    best_score = -1
    best: dict[int, int] = {}

    def search(index: int, used: set[int], current: dict[int, int], score: int) -> None:
        nonlocal best_score, best
        if index == len(expected):
            if score > best_score:
                best_score = score
                best = dict(current)
            return
        search(index + 1, used, current, score)
        for actual_index in range(len(actual)):
            if actual_index in used:
                continue
            used.add(actual_index)
            current[index] = actual_index
            search(
                index + 1,
                used,
                current,
                score + weights[(index, actual_index)],
            )
            current.pop(index)
            used.remove(actual_index)

    search(0, set(), {}, 0)
    return best


def _candidate_values(candidate: MemoryCandidate) -> list[str]:
    values: list[str] = []
    payload = candidate.payload
    for value in (
        candidate.state_value,
        payload.get("value"),
        payload.get("object"),
        payload.get("preference"),
        payload.get("current"),
        payload.get("channel"),
        payload.get("contact_method"),
        payload.get("residence"),
        payload.get("occupation"),
        payload.get("birthday"),
    ):
        candidates = value if isinstance(value, (list, tuple)) else [value]
        for item in candidates:
            if isinstance(item, str) and item.strip() and item.strip() not in values:
                values.append(item.strip())
    return values


def _metric_from_canonical(value: str | None) -> str | None:
    mapping = {
        "interaction.contact_frequency": "contact_frequency",
        "interaction.topic_scope": "topic_scope",
        "interaction.channel": "interaction_channel",
        "interaction.initiation_balance": "initiation_balance",
        "interaction.response_engagement": "response_engagement",
        "interaction.emotional_disclosure": "emotional_disclosure",
        "interaction.conflict_frequency": "conflict_frequency",
    }
    return mapping.get(value or "")


def _identifier_equal(expected: object, actual: object) -> bool:
    if actual is None:
        return False
    return _normalize_identifier(expected) == _normalize_identifier(actual)


def _value_equal(expected: object, actual: object) -> bool:
    expected_value = _normalize_value(expected)
    actual_value = _normalize_value(actual)
    aliases = {
        "wechat": "微信",
        "weixin": "微信",
        "微信聊天": "微信",
        "微信联系": "微信",
        "phonecall": "电话",
        "telephone": "电话",
        "spicy": "辣",
        "basketball": "篮球",
        "photography": "摄影",
    }
    return aliases.get(expected_value, expected_value) == aliases.get(actual_value, actual_value)


def _normalize_identifier(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    return re.sub(r"[\s-]+", "_", normalized)


def _normalize_value(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    return re.sub(r"[\s\-_/，。！？、,.!?()（）]+", "", normalized)


def _failed_case(
    case: OntologySanityCase,
    exc: Exception,
    *,
    attempts: list[MemoryExtractionAttempt] | None = None,
) -> dict[str, Any]:
    expected = [claim.fields() for claim in case.expected_claims]
    attempts = attempts or []
    return {
        "case_id": case.case_id,
        "group": case.group,
        "text": case.text,
        "passed_extraction": False,
        "passed_normalized": False,
        "first_failure_stage": "OTHER",
        "expected_claims": expected,
        "extracted_claims": [],
        "normalized_claims": [],
        "forbidden": [claim.fields() for claim in case.forbidden],
        "forbidden_hits": {"extraction": [], "normalized": []},
        "extraction_evaluation": {
            "passed": False,
            "matched_pairs": [],
            "unmatched_expected_indices": list(range(len(expected))),
            "unmatched_actual_indices": [],
            "nearest_mismatches": [],
            "forbidden_hits": [],
        },
        "normalization_evaluation": {
            "passed": False,
            "matched_pairs": [],
            "unmatched_expected_indices": list(range(len(expected))),
            "unmatched_actual_indices": [],
            "nearest_mismatches": [],
            "forbidden_hits": [],
        },
        "normalization_errors": [],
        "extractor_attempt_failures": _attempt_failure_details(attempts),
        "attempts": [attempt.model_dump(mode="json") for attempt in attempts],
        "semantic_gate": {"should_extract": None, "gate_reason": None},
        "notes": f"case_execution_error:{type(exc).__name__}:{exc}"[:1000],
        "gold_notes": case.notes,
        "annotation": case.annotation.model_dump(mode="json"),
        "high_risk": case.case_id in HIGH_RISK_CASE_IDS,
    }


def _attempt_telemetry(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = sorted(
        float(attempt["duration_ms"])
        for attempt in attempts
        if attempt.get("duration_ms") is not None
    )
    return {
        "call_count": len(attempts),
        "failure_count": sum(attempt.get("status") == "failed" for attempt in attempts),
        "flash_call_count": sum(attempt.get("tier") == "flash" for attempt in attempts),
        "strong_call_count": sum(attempt.get("tier") == "strong" for attempt in attempts),
        "prompt_tokens": sum(int(attempt.get("prompt_tokens") or 0) for attempt in attempts),
        "completion_tokens": sum(
            int(attempt.get("completion_tokens") or 0) for attempt in attempts
        ),
        "total_tokens": sum(int(attempt.get("total_tokens") or 0) for attempt in attempts),
        "latency_p50_ms": _percentile(latencies, 0.5),
        "latency_p95_ms": _percentile(latencies, 0.95),
    }


def _attempt_failure_details(
    attempts: list[MemoryExtractionAttempt],
) -> list[dict[str, str | int | None]]:
    return [
        {
            "attempt": attempt.attempt,
            "tier": attempt.tier,
            "failure_category": attempt.failure_category,
            "error": attempt.error,
        }
        for attempt in attempts
        if attempt.status.value == "failed"
    ]


def _attempt_reports_atomicity_violation(
    attempts: list[MemoryExtractionAttempt],
) -> bool:
    markers = ("多个记忆维度", "multiple memory dimensions", "atomicity")
    return any(
        attempt.invalid_claim_reasons
        and any(marker in attempt.invalid_claim_reasons for marker in markers)
        for attempt in attempts
    )


def _extractor_model(extractor: Any) -> dict[str, str | None]:
    flash = getattr(extractor, "_flash", extractor)
    strong = getattr(extractor, "_strong", None)
    return {
        "flash": getattr(flash, "_model", None),
        "strong": getattr(strong, "_model", None),
    }


def write_ontology_sanity_artifacts(
    report: dict[str, Any],
    output_dir: Path,
    *,
    policy_suspect_notes: dict[str, str] | None = None,
) -> tuple[Path, Path]:
    policy_suspect_notes = _policy_suspect_notes(
        report,
        policy_suspect_notes,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "results.jsonl"
    summary_path = output_dir / "summary.md"
    results_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in report["cases"]
        ),
        encoding="utf-8",
    )
    summary_path.write_text(
        render_ontology_sanity_summary(
            report,
            policy_suspect_notes=policy_suspect_notes,
        ),
        encoding="utf-8",
    )
    return results_path, summary_path


def render_ontology_sanity_summary(
    report: dict[str, Any],
    *,
    policy_suspect_notes: dict[str, str] | None = None,
) -> str:
    metrics = report["metrics"]
    policy_suspect_notes = _policy_suspect_notes(report, policy_suspect_notes)
    lines = [
        "# LoveApp Memory Ontology Sanity Test v0.1",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Dataset: `{report['dataset']}`",
        f"Dataset SHA256: `{report['dataset_sha256']}`",
        f"Policy: `{report['policy_version']}`",
        f"Gold review status: `{report['gold_review_status']}`",
        f"Flash model: `{report['model'].get('flash')}`",
        f"Strong model: `{report['model'].get('strong')}`",
        "Store mutation permitted: `False`",
        "",
        "> This is a diagnostic against draft Gold, not a production acceptance benchmark.",
        "",
        "## Overall metrics",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Extraction Pass Rate | {_format_rate(metrics['extraction_pass_rate'])} |",
        f"| Normalized Pass Rate | {_format_rate(metrics['normalized_pass_rate'])} |",
        f"| Kind Accuracy | {_metric_rate(metrics['kind_accuracy'])} |",
        f"| Subject Accuracy | {_metric_rate(metrics['subject_accuracy'])} |",
        f"| Perspective Accuracy | {_metric_rate(metrics['perspective_accuracy'])} |",
        f"| Canonical/Custom Accuracy | {_metric_rate(metrics['canonical_custom_accuracy'])} |",
        f"| Event Type Accuracy | {_metric_rate(metrics['event_type_accuracy'])} |",
        f"| Pattern Metric Accuracy | {_metric_rate(metrics['pattern_metric_accuracy'])} |",
        f"| Multi-claim Completeness | {_format_rate(metrics['multi_claim_completeness'])} |",
        "| Forbidden Claim Violation Rate | "
        f"{_format_rate(metrics['forbidden_claim_violation_rate'])} |",
        "",
        "## Results by group",
        "",
        "| Group | Cases | Extraction | Normalized | Kind | Subject | Perspective | "
        "Canonical/Custom | Event Type | Pattern Metric | Multi-claim | Forbidden |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for group, group_metrics in report["groups"].items():
        lines.append(
            f"| {group} | {group_metrics['case_count']} | "
            f"{_format_rate(group_metrics['extraction_pass_rate'])} | "
            f"{_format_rate(group_metrics['normalized_pass_rate'])} | "
            f"{_metric_rate(group_metrics['kind_accuracy'])} | "
            f"{_metric_rate(group_metrics['subject_accuracy'])} | "
            f"{_metric_rate(group_metrics['perspective_accuracy'])} | "
            f"{_metric_rate(group_metrics['canonical_custom_accuracy'])} | "
            f"{_metric_rate(group_metrics['event_type_accuracy'])} | "
            f"{_metric_rate(group_metrics['pattern_metric_accuracy'])} | "
            f"{_format_rate(group_metrics['multi_claim_completeness'])} | "
            f"{_format_rate(group_metrics['forbidden_claim_violation_rate'])} |"
        )
    lines.extend(
        [
            "",
            "## Case diagnostics",
            "",
            "| case_id | group | Extraction | Normalization | first failure | short reason |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in report["cases"]:
        lines.append(
            f"| {row['case_id']} | {row['group']} | "
            f"{'PASS' if row['passed_extraction'] else 'FAIL'} | "
            f"{'PASS' if row['passed_normalized'] else 'FAIL'} | "
            f"{row['first_failure_stage'] or '-'} | {_markdown_text(row['notes'])} |"
        )
    extraction_drift_count = sum(
        not row["passed_extraction"]
        and row["case_id"] not in policy_suspect_notes
        and not row.get("extractor_attempt_failures")
        for row in report["cases"]
    )
    normalization_drift_count = sum(
        row["passed_extraction"]
        and not row["passed_normalized"]
        and row["case_id"] not in policy_suspect_notes
        for row in report["cases"]
    )
    technical_failure_count = sum(
        bool(row.get("extractor_attempt_failures")) for row in report["cases"]
    )
    lines.extend(
        [
            "",
            "## Diagnostic conclusion",
            "",
            f"- Extraction-drift candidates: `{extraction_drift_count}`",
            f"- Normalization-only drift candidates: `{normalization_drift_count}`",
            f"- Technical extractor failures: `{technical_failure_count}`",
            f"- Draft-policy review candidates: `{len(policy_suspect_notes)}`",
            "- Result interpretation: failures are diagnostic findings against draft Gold; "
            "they are not automatic authorization to change production semantics.",
        ]
    )
    lines.extend(
        [
            "",
            "## Error taxonomy",
            "",
            "| First failing stage | Cases |",
            "|---|---:|",
        ]
    )
    if report["error_taxonomy"]:
        for name, count in report["error_taxonomy"].items():
            lines.append(f"| {name} | {count} |")
    else:
        lines.append("| No failures | 0 |")
    lines.extend(
        [
            "",
            "## High-risk cases",
            "",
            "| Case | Extraction | Normalized | First failure |",
            "|---|---|---|---|",
        ]
    )
    rows_by_id = {row["case_id"]: row for row in report["cases"]}
    for case_id in report["high_risk_case_ids"]:
        row = rows_by_id.get(case_id)
        if row is None:
            continue
        lines.append(
            f"| {case_id} | {'PASS' if row['passed_extraction'] else 'FAIL'} | "
            f"{'PASS' if row['passed_normalized'] else 'FAIL'} | "
            f"{row['first_failure_stage'] or '-'} |"
        )
    lines.extend(["", "## Policy-suspect cases", ""])
    if policy_suspect_notes:
        lines.extend(["| Case | Why Gold needs human review |", "|---|---|"])
        for case_id, reason in sorted(policy_suspect_notes.items()):
            lines.append(f"| {case_id} | {_markdown_text(reason)} |")
    else:
        lines.append("None identified. Gold labels remain unchanged.")
    telemetry = report["telemetry"]
    lines.extend(
        [
            "",
            "## Model telemetry",
            "",
            f"- Calls: `{telemetry['call_count']}`",
            f"- Flash calls: `{telemetry['flash_call_count']}`",
            f"- Strong calls: `{telemetry['strong_call_count']}`",
            f"- Failures: `{telemetry['failure_count']}`",
            f"- Prompt tokens: `{telemetry['prompt_tokens']}`",
            f"- Completion tokens: `{telemetry['completion_tokens']}`",
            f"- P50 latency: `{telemetry['latency_p50_ms']} ms`",
            f"- P95 latency: `{telemetry['latency_p95_ms']} ms`",
            "",
            "No production ontology, Prompt, Normalizer, Validator, Relation, Lifecycle, or "
            "Store behavior was changed by this diagnostic run.",
        ]
    )
    return "\n".join(lines) + "\n"


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    index = min(len(values) - 1, max(0, int(len(values) * percentile + 0.999999) - 1))
    return round(values[index], 2)


def _format_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def _metric_rate(value: dict[str, Any]) -> str:
    return f"{_format_rate(value['rate'])} ({value['correct']}/{value['total']})"


def _markdown_text(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _policy_suspect_notes(
    report: dict[str, Any],
    supplied: dict[str, str] | None,
) -> dict[str, str]:
    if supplied is not None:
        return dict(supplied)
    failed_ids = {
        row["case_id"] for row in report.get("cases", []) if not row["passed_normalized"]
    }
    return {
        case_id: note
        for case_id, note in DRAFT_POLICY_SUSPECT_NOTES.items()
        if case_id in failed_ids
    }


__all__ = [
    "DRAFT_POLICY_SUSPECT_NOTES",
    "EXPECTED_GROUP_COUNTS",
    "FAILURE_STAGES",
    "HIGH_RISK_CASE_IDS",
    "OntologySanityCase",
    "evaluate_memory_ontology_sanity",
    "load_ontology_sanity_cases",
    "render_ontology_sanity_summary",
    "write_ontology_sanity_artifacts",
]

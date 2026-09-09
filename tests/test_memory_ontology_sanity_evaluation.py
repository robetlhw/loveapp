import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    MemoryAttemptStatus,
    MemoryExtractionAttempt,
    MemoryKind,
    MemoryPerspective,
    MemorySemanticGateReason,
    PredicateType,
)
from loveapp.evaluation.memory_ontology_sanity import (
    EXPECTED_GROUP_COUNTS,
    _evaluate_stage,
    _first_failure_stage,
    _maximum_full_matching,
    _value_equal,
    evaluate_memory_ontology_sanity,
    load_ontology_sanity_cases,
    render_ontology_sanity_summary,
    write_ontology_sanity_artifacts,
)

REFERENCE_TIME = datetime.fromisoformat("2026-09-09T12:00:00+08:00")


class _FixtureExtractor:
    _model = "fixture-extractor"

    def __init__(self, extractions: dict[str, AtomicExtraction]) -> None:
        self._extractions = extractions
        self.calls: list[dict[str, object]] = []

    async def extract(self, text: str, **kwargs: object) -> AtomicExtraction:
        self.calls.append({"text": text, **kwargs})
        return self._extractions[text]


class _FailingExtractor:
    _model = "failing-extractor"

    async def extract(self, text: str, *, attempt_callback, **_: object) -> AtomicExtraction:
        attempt_callback(
            MemoryExtractionAttempt(
                attempt=1,
                status=MemoryAttemptStatus.FAILED,
                duration_ms=12,
                tier="flash",
                failure_category="transport",
                error=f"request failed for {text}",
            )
        )
        raise RuntimeError("transport unavailable")


def _dataset_row(
    case_id: str,
    group: str,
    *,
    text: str | None = None,
    expected_claims: list[dict[str, str]] | None = None,
    forbidden: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "ontology_sanity_v1",
        "case_id": case_id,
        "group": group,
        "text": text or case_id,
        "expected_claims": expected_claims or [{"memory_kind": group}],
        "forbidden": forbidden or [],
        "notes": "fixture",
        "annotation": {
            "label_source": "test",
            "review_status": "draft",
            "policy_version": "memory_ontology_v3_policy_v1",
        },
    }


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> Path:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def _claim(
    text: str,
    *,
    kind: MemoryKind = MemoryKind.STABLE_FACT,
    subject: str = "user",
    predicate: str = "resides_in",
    predicate_type: PredicateType = PredicateType.CANONICAL,
    canonical_predicate: str | None = "profile.residence",
    custom_predicate: str | None = None,
    payload: dict[str, object] | None = None,
) -> AtomicClaim:
    return AtomicClaim(
        claim_id="claim-1",
        kind=kind,
        subject=subject,
        predicate=predicate,
        object="上海",
        summary="用户现居上海",
        evidence_spans=[text],
        perspective=MemoryPerspective.USER_REPORTED,
        predicate_type=predicate_type,
        canonical_predicate=canonical_predicate,
        custom_predicate=custom_predicate,
        payload=payload or {"value": "上海"},
    )


def test_dataset_contract_requires_exact_40_case_distribution(tmp_path: Path) -> None:
    prefixes = {
        "stable_fact": "sf",
        "preference": "pref",
        "interaction_event": "event",
        "interaction_pattern": "pattern",
        "boundary": "bd",
    }
    rows = [
        _dataset_row(f"{prefixes[group]}_{index:03d}", group)
        for group, count in EXPECTED_GROUP_COUNTS.items()
        for index in range(1, count + 1)
    ]

    cases = load_ontology_sanity_cases(_write_jsonl(tmp_path / "gold.jsonl", rows))

    assert len(cases) == 40
    assert Counter(case.group for case in cases) == EXPECTED_GROUP_COUNTS
    assert all(case.annotation.review_status == "draft" for case in cases)


def test_partial_expected_constraints_do_not_require_missing_fields_to_be_null() -> None:
    actual = [
        {
            "memory_kind": "stable_fact",
            "subject": "user",
            "perspective": "user_reported",
            "canonical_predicate": "profile.residence",
            "payload": {"extra": "retained"},
        }
    ]

    result = _evaluate_stage(
        [{"memory_kind": "stable_fact", "subject": "user"}],
        [],
        actual,
        stage="normalized",
    )

    assert result["passed"] is True
    assert result["matched_pairs"] == [{"expected_index": 0, "actual_index": 0}]


def test_predicate_type_custom_does_not_require_an_exact_generated_name() -> None:
    actual = [
        {
            "predicate_type": "custom",
            "raw_predicate": "reply_speed_trend",
            "custom_predicate": "reply_speed_trend",
        }
    ]

    result = _evaluate_stage(
        [{"predicate_type": "custom"}],
        [],
        actual,
        stage="normalized",
    )

    assert result["passed"] is True


def test_value_matching_tolerates_bounded_source_language_aliases() -> None:
    assert _value_equal("摄影", "photography") is True
    assert _value_equal("电话", "phone_call") is True
    assert _value_equal("微信", "微信聊天") is True
    assert _value_equal("摄影", "basketball") is False


def test_forbidden_objects_are_prohibited_partial_exact_matches() -> None:
    actual = [
        {
            "memory_kind": "interaction_pattern",
            "subject": "relationship",
            "predicate_type": "custom",
            "custom_predicate": "conflict_pattern",
        }
    ]

    result = _evaluate_stage(
        [{"memory_kind": "interaction_pattern"}],
        [{"custom_predicate": "conflict_pattern"}],
        actual,
        stage="normalized",
    )

    assert result["passed"] is False
    assert result["forbidden_hits"] == [
        {
            "forbidden_index": 0,
            "actual_index": 0,
            "constraint": {"custom_predicate": "conflict_pattern"},
        }
    ]


def test_multi_claim_matching_is_one_to_one_and_attributes_missing_claim() -> None:
    expected = [{"subject": "relationship"}, {"subject": "relationship"}]
    actual = [{"subject": "relationship"}]
    result = _evaluate_stage(expected, [], actual, stage="extraction")

    assert len(_maximum_full_matching(expected, actual, stage="extraction")) == 1
    assert len(result["unmatched_expected_indices"]) == 1
    assert (
        _first_failure_stage(expected, actual, actual, result, result, [])
        == "MULTI_CLAIM_MISSING"
    )


def test_wrong_second_claim_kind_is_multi_claim_missing() -> None:
    expected = [
        {"memory_kind": "interaction_event"},
        {"memory_kind": "interaction_pattern"},
    ]
    actual = [
        {"memory_kind": "interaction_event"},
        {"memory_kind": "interaction_event"},
    ]
    result = _evaluate_stage(expected, [], actual, stage="extraction")

    assert (
        _first_failure_stage(expected, actual, actual, result, result, [])
        == "MULTI_CLAIM_MISSING"
    )


def test_multi_claim_structured_field_loss_is_not_missing_claim() -> None:
    expected = [
        {
            "memory_kind": "interaction_event",
            "event_type": "shared_activity",
            "milestone_type": "first_occurrence",
        },
        {"memory_kind": "interaction_pattern", "predicate_type": "custom"},
    ]
    actual = [
        {
            "memory_kind": "interaction_event",
            "event_type": "shared_activity",
            "milestone_type": None,
        },
        {
            "memory_kind": "interaction_pattern",
            "predicate_type": "custom",
            "custom_predicate": "monthly_short_trips",
        },
    ]
    result = _evaluate_stage(expected, [], actual, stage="extraction")

    assert (
        _first_failure_stage(expected, actual, actual, result, result, [])
        == "EXTRACTION_SEMANTIC_LOSS"
    )


def test_first_failure_distinguishes_normalization_mapping_from_extraction() -> None:
    expected = [
        {
            "predicate_type": "canonical",
            "canonical_predicate": "interaction.initiation_balance",
        }
    ]
    extracted = [
        {
            "predicate_type": "custom",
            "raw_predicate": "initiation_balance",
            "canonical_predicate": None,
            "semantic_canonical_hint": "interaction.initiation_balance",
        }
    ]
    normalized = [
        {
            "predicate_type": "canonical",
            "canonical_predicate": "interaction.contact_frequency",
        }
    ]
    extraction_result = _evaluate_stage(expected, [], extracted, stage="extraction")
    normalized_result = _evaluate_stage(expected, [], normalized, stage="normalized")

    assert extraction_result["passed"] is True
    assert normalized_result["passed"] is False
    assert (
        _first_failure_stage(
            expected,
            extracted,
            normalized,
            extraction_result,
            normalized_result,
            [],
        )
        == "NORMALIZATION_CANONICAL_MAPPING_ERROR"
    )


def test_transport_or_parse_attempt_failure_is_attributed_as_technical_other() -> None:
    expected = [{"memory_kind": "interaction_event"}]
    result = _evaluate_stage(expected, [], [], stage="extraction")
    attempts = [
        MemoryExtractionAttempt(
            attempt=1,
            status=MemoryAttemptStatus.FAILED,
            duration_ms=10,
            tier="flash",
            failure_category="format",
            error="invalid JSON",
        )
    ]

    assert (
        _first_failure_stage(expected, [], [], result, result, [], attempts)
        == "OTHER"
    )


def test_discarded_multi_dimension_claim_is_attributed_to_extraction_atomicity() -> None:
    expected = [{"memory_kind": "interaction_pattern"}]
    result = _evaluate_stage(expected, [], [], stage="extraction")
    attempts = [
        MemoryExtractionAttempt(
            attempt=1,
            status=MemoryAttemptStatus.COMPLETED,
            duration_ms=10,
            tier="flash",
            claim_count=0,
            original_claim_count=1,
            invalid_claim_count=1,
            invalid_claim_reasons="claim contains 多个记忆维度",
        )
    ]

    assert (
        _first_failure_stage(expected, [], [], result, result, [], attempts)
        == "EXTRACTION_ATOMICITY_ERROR"
    )


def test_normalization_contract_error_precedes_empty_output_field_mismatches() -> None:
    expected = [
        {
            "memory_kind": "preference",
            "predicate_type": "canonical",
            "canonical_predicate": "preference.communication.frequency",
        }
    ]
    extracted = [
        {
            "memory_kind": "preference",
            "predicate_type": "custom",
            "raw_predicate": "prefers_frequent_communication",
            "canonical_predicate": "preference.communication.frequency",
            "custom_predicate": "prefers_frequent_communication",
            "semantic_canonical_hint": "preference.communication.frequency",
        }
    ]
    extraction_result = _evaluate_stage(expected, [], extracted, stage="extraction")
    normalized_result = _evaluate_stage(expected, [], [], stage="normalized")

    assert extraction_result["passed"] is True
    assert (
        _first_failure_stage(
            expected,
            extracted,
            [],
            extraction_result,
            normalized_result,
            [
                {
                    "error_code": "CANONICAL_CUSTOM_CONFLICT",
                    "error": "canonical/custom declarations conflict",
                }
            ],
        )
        == "NORMALIZATION_CANONICAL_MAPPING_ERROR"
    )


@pytest.mark.asyncio
async def test_live_harness_uses_only_extractor_and_writes_required_artifacts(
    tmp_path: Path,
) -> None:
    text = "我现在住上海。"
    dataset = _write_jsonl(
        tmp_path / "one.jsonl",
        [
            _dataset_row(
                "sf_001",
                "stable_fact",
                text=text,
                expected_claims=[
                    {
                        "memory_kind": "stable_fact",
                        "subject": "user",
                        "perspective": "user_reported",
                        "predicate_type": "canonical",
                        "canonical_predicate": "profile.residence",
                        "value": "上海",
                    }
                ],
                forbidden=[{"memory_kind": "preference"}],
            )
        ],
    )
    extractor = _FixtureExtractor(
        {
            text: AtomicExtraction(
                should_extract=True,
                gate_reason=MemorySemanticGateReason.STABLE_FACT,
                claims=[_claim(text)],
            )
        }
    )

    report = await evaluate_memory_ontology_sanity(
        dataset,
        extractor=extractor,
        reference_time=REFERENCE_TIME,
        case_id="sf_001",
    )
    results_path, summary_path = write_ontology_sanity_artifacts(
        report,
        tmp_path / "artifacts",
    )

    assert report["store_mutation_permitted"] is False
    assert report["relation_or_lifecycle_evaluated"] is False
    assert report["metrics"]["extraction_pass_rate"] == 1.0
    assert report["metrics"]["normalized_pass_rate"] == 1.0
    assert extractor.calls[0]["existing_memories"] == []
    assert extractor.calls[0]["conversation_history"] == []
    row = json.loads(results_path.read_text(encoding="utf-8").splitlines()[0])
    assert {
        "case_id",
        "group",
        "passed_extraction",
        "passed_normalized",
        "first_failure_stage",
        "expected_claims",
        "extracted_claims",
        "normalized_claims",
        "forbidden_hits",
        "notes",
    } <= row.keys()
    summary = summary_path.read_text(encoding="utf-8")
    assert "## Overall metrics" in summary
    assert "## Results by group" in summary
    assert "Canonical/Custom" in summary
    assert "Pattern Metric" in summary
    assert "## Case diagnostics" in summary
    assert "## Error taxonomy" in summary
    assert "## Policy-suspect cases" in summary
    assert "Store mutation permitted: `False`" in summary


@pytest.mark.asyncio
async def test_extractor_failures_keep_attempt_telemetry_and_are_not_semantic_failures(
    tmp_path: Path,
) -> None:
    text = "我现在住上海。"
    dataset = _write_jsonl(
        tmp_path / "one.jsonl",
        [
            _dataset_row(
                "sf_001",
                "stable_fact",
                text=text,
                expected_claims=[{"memory_kind": "stable_fact"}],
            )
        ],
    )

    report = await evaluate_memory_ontology_sanity(
        dataset,
        extractor=_FailingExtractor(),
        reference_time=REFERENCE_TIME,
        case_id="sf_001",
    )
    row = report["cases"][0]

    assert row["first_failure_stage"] == "OTHER"
    assert row["extractor_attempt_failures"][0]["failure_category"] == "transport"
    assert report["telemetry"]["call_count"] == 1
    assert report["telemetry"]["failure_count"] == 1


def test_summary_keeps_gold_draft_and_policy_suspect_labels_explicit() -> None:
    report = {
        "generated_at": "2026-09-09T12:00:00+08:00",
        "dataset": "gold.jsonl",
        "dataset_sha256": "sha",
        "policy_version": "memory_ontology_v3_policy_v1",
        "gold_review_status": "draft",
        "model": {"flash": "fixture", "strong": None},
        "metrics": {
            "extraction_pass_rate": 1.0,
            "normalized_pass_rate": 1.0,
            "kind_accuracy": {"rate": 1.0, "correct": 1, "total": 1},
            "subject_accuracy": {"rate": 1.0, "correct": 1, "total": 1},
            "perspective_accuracy": {"rate": 1.0, "correct": 1, "total": 1},
            "canonical_custom_accuracy": {"rate": 1.0, "correct": 1, "total": 1},
            "event_type_accuracy": {"rate": None, "correct": 0, "total": 0},
            "pattern_metric_accuracy": {"rate": None, "correct": 0, "total": 0},
            "multi_claim_completeness": None,
            "forbidden_claim_violation_rate": 0.0,
        },
        "groups": {},
        "cases": [],
        "error_taxonomy": {},
        "high_risk_case_ids": [],
        "telemetry": {
            "call_count": 0,
            "flash_call_count": 0,
            "strong_call_count": 0,
            "failure_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "latency_p50_ms": None,
            "latency_p95_ms": None,
        },
    }

    summary = render_ontology_sanity_summary(
        report,
        policy_suspect_notes={"event_003": "Underlying event domain may need review."},
    )

    assert "Gold review status: `draft`" in summary
    assert "event_003" in summary
    assert "Gold labels remain unchanged" not in summary

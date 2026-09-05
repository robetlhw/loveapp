import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from loveapp.domain.memory import (
    ClaimRelation,
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    TemporalPrecision,
    TimeKind,
)
from loveapp.domain.memory_semantic_relation import SemanticRelationProposal
from loveapp.evaluation.memory_longtail_write_v2 import (
    EXPECTED_CASE_COUNT,
    EXPECTED_OVERLAY_MEMORY_COUNT,
    EXPECTED_SHARED_MEMORY_COUNT,
    HARD_CASE_IDS,
    REPORT_VERSION,
    FixtureTextEmbeddingProvider,
    LongTailWriteV2EvaluationError,
    _attribute_failure,
    _candidate_from_row,
    _error_row,
    _evaluation_status,
    _retrieval_metrics,
    _row_expected_semantic_target_ids,
    _validate_equivalence_contract,
    compare_memory_longtail_write_v2_reports,
    evaluate_memory_longtail_write_v2,
    evaluate_memory_longtail_write_v2_fixture,
    finalize_memory_longtail_write_v2_live_validation,
    load_memory_longtail_write_v2_dataset,
    render_memory_longtail_write_v2_report,
)

ROOT = Path(__file__).parents[1]
CASES = ROOT / "evals/memory/longtail_write_v2_cases_draft1.jsonl"
SHARED = ROOT / "evals/memory/longtail_write_v2_shared_bank_draft1.jsonl"


class _ControlledEmbeddingProvider:
    model_name = "controlled-text-only"
    model_version = "test-v1"

    def __init__(
        self,
        *,
        target_texts: set[str],
        higher_vector_texts: set[str] | None = None,
    ) -> None:
        self.target_texts = target_texts
        self.higher_vector_texts = higher_vector_texts or set()
        self.document_inputs: list[list[str]] = []
        self.query_inputs: list[str] = []

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_inputs.append(list(texts))
        return [self._document_vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        self.query_inputs.append(text)
        return [1.0, 0.0]

    def _document_vector(self, text: str) -> list[float]:
        if text in self.higher_vector_texts:
            return [0.99, math.sqrt(1 - 0.99**2)]
        if text in self.target_texts:
            return [0.90, math.sqrt(1 - 0.90**2)]
        return [0.0, 1.0]


class _FailingEmbeddingProvider(_ControlledEmbeddingProvider):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        del texts
        raise RuntimeError("synthetic embedding failure")


class _ScriptedJudge:
    def __init__(self, relation: ClaimRelation, targets: list[str]) -> None:
        self.relation = relation
        self.targets = targets
        self.calls: list[list[str]] = []

    async def propose_relation(
        self,
        *,
        incoming: MemoryCandidate,
        candidates: list[MemoryItem],
        trace: Any = None,
    ) -> SemanticRelationProposal:
        del incoming, trace
        self.calls.append([candidate.id for candidate in candidates])
        return SemanticRelationProposal(
            relation=self.relation,
            target_memory_ids=list(self.targets),
            same_semantic_dimension=self.relation
            in {ClaimRelation.SAME, ClaimRelation.UPDATE, ClaimRelation.CONTRADICTION},
            confidence=0.98,
            reason="Deterministic V2 evaluator regression proposal.",
            judge_model="scripted-v2-test",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        )


class _FailingJudge:
    async def propose_relation(
        self,
        *,
        incoming: MemoryCandidate,
        candidates: list[MemoryItem],
        trace: Any = None,
    ) -> SemanticRelationProposal:
        del incoming, candidates, trace
        raise TimeoutError("synthetic judge timeout")


def _case(case_id: str) -> dict[str, Any]:
    dataset = load_memory_longtail_write_v2_dataset(CASES, SHARED)
    return next(case for case in dataset["cases"] if case["case_id"] == case_id)


def _gold_texts(case_id: str) -> set[str]:
    case = _case(case_id)
    expected = set(case["expected_target_ids"])
    return {row["text"] for row in case["overlay"] if row["memory_id"] in expected}


async def _evaluate(
    case_id: str,
    *,
    relation: ClaimRelation,
    targets: list[str],
    provider: _ControlledEmbeddingProvider | None = None,
) -> tuple[dict[str, Any], _ControlledEmbeddingProvider, _ScriptedJudge]:
    embedding = provider or _ControlledEmbeddingProvider(target_texts=_gold_texts(case_id))
    judge = _ScriptedJudge(relation, targets)
    report = await evaluate_memory_longtail_write_v2(
        CASES,
        SHARED,
        embedding_provider=embedding,
        judge=judge,
        case_id=case_id,
        fail_on_error=True,
    )
    return report, embedding, judge


def test_v2_dataset_shape_and_collision_review_are_explicit() -> None:
    dataset = load_memory_longtail_write_v2_dataset(CASES, SHARED)

    structural = dataset["structural_validation"]
    collisions = dataset["collision_audit"]
    assert structural["shared_memory_count"] == EXPECTED_SHARED_MEMORY_COUNT == 120
    assert structural["case_count"] == EXPECTED_CASE_COUNT == 40
    assert structural["overlay_memory_count"] == EXPECTED_OVERLAY_MEMORY_COUNT == 200
    assert structural["candidate_pool_size_counts"] == {125: 40}
    assert structural["candidate_pool_min"] == 125
    assert structural["candidate_pool_max"] == 125
    assert structural["candidate_pool_contract_status"] == "PASS"
    assert dataset["dataset_status"] == "PASS"
    assert collisions["cases_with_any_exact_shared_overlay_duplicate"] == 18
    assert collisions["cases_with_exact_duplicate_gold_target"] == 11
    assert collisions["gold_collision_case_ids"] == []
    assert collisions["unresolved_exact_collision_case_ids"] == []


def test_v2_every_case_declares_fixed_shared_pool_contract() -> None:
    dataset = load_memory_longtail_write_v2_dataset(CASES, SHARED)
    expected_pools = {
        "profile_preference",
        "relationship_patterns",
        "events",
        "plans_intents",
    }
    assert all(set(case["shared_pools"]) == expected_pools for case in dataset["cases"])
    assert all(
        row["candidate_pool_size"] == 125
        for row in [
            {"candidate_pool_size": EXPECTED_SHARED_MEMORY_COUNT + len(case["overlay"])}
            for case in dataset["cases"]
        ]
    )


def test_v2_equivalent_exact_duplicates_are_governed_not_review_collisions() -> None:
    dataset = load_memory_longtail_write_v2_dataset(CASES, SHARED)
    collisions = dataset["collision_audit"]
    assert collisions["cases_with_equivalent_exact_gold_target"] == 11
    assert collisions["cases_with_unresolved_exact_gold_target"] == 0
    assert all(
        detail.get("equivalent_documented")
        for case in collisions["case_collisions"]
        for detail in case["exact_text_collisions"]
    )


def test_v2_ungoverned_exact_duplicate_is_rejected() -> None:
    dataset = load_memory_longtail_write_v2_dataset(CASES, SHARED)
    shared = [dict(row) for row in dataset["shared_memories"]]
    overlays = [dict(row) for row in dataset["cases"][3]["overlay"]]
    target = next(row for row in overlays if row["memory_id"] == "O016")
    target.pop("equivalent_memory_group_id", None)
    with pytest.raises(LongTailWriteV2EvaluationError, match="equivalent_memory_group_id"):
        _validate_equivalence_contract([{"overlay": overlays}], shared)


@pytest.mark.asyncio
async def test_v2_embedding_is_text_only_and_candidate_trace_is_explainable() -> None:
    case = _case("LTW2-001")
    report, embedding, judge = await _evaluate(
        "LTW2-001",
        relation=ClaimRelation.SAME,
        targets=["O001"],
    )

    expected_document_texts = {
        row["text"] for row in report["rows"][0]["retrieval"]["candidate_inventory"]
    }
    assert set(embedding.document_inputs[0]) == expected_document_texts
    assert embedding.query_inputs == [case["incoming"]["text"]]
    assert report["telemetry"]["embedding"]["text_only"] is True
    assert len(judge.calls) == 2  # Oracle overlay and retrieved Top-5.

    row = report["rows"][0]
    target = next(item for item in row["retrieval"]["ranked"] if item["memory_id"] == "O001")
    assert target["vector_rank"] == 1
    assert target["rank"] == 1
    assert target["benchmark_role"] == "GOLD_TARGET"
    assert target["kind"] == "stable_fact"
    assert target["subject"] == "partner"
    assert target["source"] == "overlay"
    assert "cheap_score" in target["score"]


@pytest.mark.asyncio
async def test_v2_case_pass_requires_top20_and_ranked_top5_not_raw_vector_top5() -> None:
    dataset = load_memory_longtail_write_v2_dataset(CASES, SHARED)
    event_texts = {row["text"] for row in dataset["shared_memories"] if row["pool"] == "events"}
    provider = _ControlledEmbeddingProvider(
        target_texts=_gold_texts("LTW2-001"),
        higher_vector_texts=set(sorted(event_texts)[:5]),
    )
    report, _, _ = await _evaluate(
        "LTW2-001",
        relation=ClaimRelation.SAME,
        targets=["O001"],
        provider=provider,
    )

    row = report["rows"][0]
    assert row["retrieval"]["checks"] == {
        "gold_in_top_20": True,
        "gold_in_top_10": True,
        "gold_in_top_5": False,
        "gold_retained_after_ranking": True,
        "equivalent_gold_in_top_20": True,
        "equivalent_gold_retained_after_ranking": True,
    }
    assert row["passed"] is True


@pytest.mark.asyncio
async def test_v2_single_target_update_without_temporal_evidence_fails_closed() -> None:
    report, _, _ = await _evaluate(
        "LTW2-011",
        relation=ClaimRelation.UPDATE,
        targets=["O051"],
    )

    row = report["rows"][0]
    validation = row["retrieved_relation"]["validation"]
    assert validation["validator_pass"] is False
    assert "failed:temporal_evidence_available" in validation["validator_reasons"]
    assert row["store"]["production_store_mutation_permitted"] is False
    assert row["store"]["isolated_store_mutation_permitted"] is True
    assert row["store"]["actual_write_action"] == "add_without_supersede"
    assert row["store"]["actual_supersede_memory_ids"] == []
    assert row["store"]["actual_incoming_final_status"] == "confirmed"
    assert not any(row["safety"].values())


@pytest.mark.asyncio
async def test_v2_multi_target_destructive_proposal_fails_closed() -> None:
    report, _, _ = await _evaluate(
        "LTW2-036",
        relation=ClaimRelation.UPDATE,
        targets=["O176", "O177"],
    )

    row = report["rows"][0]
    assert row["retrieved_relation"]["proposal"]["target_memory_ids"] == [
        "O176",
        "O177",
    ]
    assert row["retrieved_relation"]["validation"]["validator_pass"] is False
    assert row["store"]["effective_relation"] == "uncertain"
    assert row["store"]["actual_write_action"] == "add_without_supersede"
    assert row["store"]["actual_supersede_memory_ids"] == []
    assert row["safety"]["uncertain_destructive_update"] is False
    assert row["store"]["expected"]["multi_target_destructive_fail_closed"] is True


@pytest.mark.asyncio
async def test_v2_relation_exact_and_set_metrics_are_distinct() -> None:
    report, _, _ = await _evaluate(
        "LTW2-037",
        relation=ClaimRelation.COMPLEMENTARY,
        targets=["O182", "O181"],
    )

    row = report["rows"][0]
    assert row["retrieved_checks"]["target_exact"] is False
    assert row["retrieved_checks"]["target_set"] is True
    assert report["retrieved_relation_metrics"]["target_exact_match"] == 0.0
    assert report["retrieved_relation_metrics"]["target_set_accuracy"] == 1.0


@pytest.mark.asyncio
async def test_v2_uncertain_gold_references_are_not_semantic_targets() -> None:
    """LTW2-040 keeps retrieval references separate from write targets."""

    report, _, _ = await _evaluate(
        "LTW2-040",
        relation=ClaimRelation.UNCERTAIN,
        targets=[],
    )

    row = report["rows"][0]
    assert row["expected_target_ids"] == ["O196", "O197"]
    assert row["expected_retrieval_candidate_ids"] == ["O196", "O197"]
    assert row["expected_semantic_target_ids"] == []
    assert row["target_contract"] == "retrieval_reference_only"
    assert row["retrieved_checks"]["relation"] is True
    assert row["retrieved_checks"]["target_set"] is True
    assert row["primary_failure_stage"] is None
    assert row["store"]["effective_target_ids"] == []
    assert report["retrieved_relation_metrics"]["target_set_accuracy"] == 1.0

    # The fixture Judge must also emit an empty target set for UNCERTAIN.
    fixture = await evaluate_memory_longtail_write_v2_fixture(
        CASES,
        SHARED,
        case_id="LTW2-040",
    )
    fixture_row = fixture["rows"][0]
    assert fixture_row["oracle_relation"]["proposal"]["relation"] == "uncertain"
    assert fixture_row["oracle_relation"]["proposal"]["target_memory_ids"] == []


def test_v2_status_requires_all_frozen_quality_gates() -> None:
    retrieval = {
        "retrieval_recall_at_20": 0.95,
        "gold_retention_at_5": 0.90,
    }
    relation = {
        "relation_accuracy": 0.75,
        "macro_f1": 0.70,
        "target_set_accuracy": 0.60,
        "target_micro_f1": 0.70,
    }
    safety = {"actual_destructive_write_violation_count": 0}
    assert _evaluation_status(retrieval, relation, safety, dataset_status="PASS") == (
        "V2_STAGE_GOALS_MET"
    )

    for field, value in (
        ("retrieval_recall_at_20", 0.9499),
        ("gold_retention_at_5", 0.8999),
    ):
        candidate = dict(retrieval)
        candidate[field] = value
        assert _evaluation_status(candidate, relation, safety, dataset_status="PASS") == (
            "V2_BASELINE_REQUIRES_REVIEW"
        )
    for field, value in (
        ("relation_accuracy", 0.7499),
        ("macro_f1", 0.6999),
        ("target_set_accuracy", 0.5999),
        ("target_micro_f1", 0.6999),
    ):
        candidate = dict(relation)
        candidate[field] = value
        assert _evaluation_status(retrieval, candidate, safety, dataset_status="PASS") == (
            "V2_BASELINE_REQUIRES_REVIEW"
        )


def test_v2_status_fails_closed_for_unknown_or_conflicting_safety_count() -> None:
    retrieval = {
        "retrieval_recall_at_20": 0.95,
        "gold_retention_at_5": 0.90,
    }
    relation = {
        "relation_accuracy": 0.75,
        "macro_f1": 0.70,
        "target_set_accuracy": 0.60,
        "target_micro_f1": 0.70,
    }

    # A present-but-null canonical field must not mask a non-zero legacy
    # safety count through dict.get(default=...).
    assert _evaluation_status(
        retrieval,
        relation,
        {
            "actual_destructive_write_violation_count": None,
            "destructive_safety_violation_count": 1,
        },
        dataset_status="PASS",
    ) == "SAFETY_REGRESSION"
    assert _evaluation_status(
        retrieval,
        relation,
        {
            "actual_destructive_write_violation_count": None,
            "destructive_safety_violation_count": 0,
        },
        dataset_status="PASS",
    ) == "SAFETY_REGRESSION"

    # Missing/invalid safety telemetry is unknown, not clean.
    assert _evaluation_status(
        retrieval,
        relation,
        {"actual_destructive_write_violation_count": "unknown"},
        dataset_status="PASS",
    ) == "SAFETY_REGRESSION"


def test_v2_status_handles_null_quality_metrics_without_raising() -> None:
    retrieval = {
        "retrieval_recall_at_20": 0.95,
        "gold_retention_at_5": 0.90,
    }
    relation = {
        "relation_accuracy": None,
        "macro_f1": 0.70,
        "target_set_accuracy": 0.60,
        "target_micro_f1": 0.70,
    }
    assert _evaluation_status(
        retrieval,
        relation,
        {"actual_destructive_write_violation_count": 0},
        dataset_status="PASS",
    ) == "V2_BASELINE_REQUIRES_REVIEW"


def test_v2_uncertain_row_recomputes_empty_semantic_target_contract() -> None:
    row = {
        "expected_relation": ClaimRelation.UNCERTAIN.value,
        # Simulate a stale/externally supplied derived field.
        "expected_semantic_target_ids": ["O196", "O197"],
    }
    assert _row_expected_semantic_target_ids(row) == []


@pytest.mark.asyncio
async def test_v2_gold_collision_is_review_metadata_not_a_safety_violation() -> None:
    report, _, _ = await _evaluate(
        "LTW2-004",
        relation=ClaimRelation.SAME,
        targets=["O016"],
    )

    row = report["rows"][0]
    assert row["dataset_review_required"] is False
    assert "seed_identity_collision_count" in row["store"]
    assert not any(row["safety"].values())
    assert row["primary_failure_stage"] is None


@pytest.mark.asyncio
async def test_v2_embedding_failure_is_reported_without_secondary_crash() -> None:
    provider = _FailingEmbeddingProvider(target_texts=set())
    report = await evaluate_memory_longtail_write_v2(
        CASES,
        SHARED,
        embedding_provider=provider,
        judge=_FailingJudge(),
        case_id="LTW2-001",
    )

    assert report["case_count"] == 1
    assert report["telemetry"]["embedding"]["failure_count"] == 1
    assert report["rows"][0]["primary_failure_stage"] == "RETRIEVAL_ERROR"
    assert report["rows"][0]["secondary_failure_stages"] == ["EMBEDDING_ERROR"]
    assert "document embedding failed" in report["rows"][0]["error"]


@pytest.mark.asyncio
async def test_v2_judge_failure_fails_closed_and_report_is_serializable() -> None:
    report = await evaluate_memory_longtail_write_v2(
        CASES,
        SHARED,
        embedding_provider=_ControlledEmbeddingProvider(target_texts=_gold_texts("LTW2-001")),
        judge=_FailingJudge(),
        case_id="LTW2-001",
        fail_on_error=True,
    )

    row = report["rows"][0]
    assert report["telemetry"]["judge"]["failure_count"] == 2
    assert row["retrieved_relation"]["proposal"]["relation"] == "uncertain"
    assert row["retrieved_relation"]["proposal"]["target_memory_ids"] == []
    assert row["store"]["actual_supersede_memory_ids"] == []
    encoded = json.dumps(report, ensure_ascii=False)
    assert json.loads(encoded)["version"] == REPORT_VERSION
    rendered = render_memory_longtail_write_v2_report(report)
    assert "Oracle vs Retrieved Relation" in rendered
    assert "Production Store mutation permitted: `False`" in rendered


def _temporal_candidate_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "kind": MemoryKind.INTERACTION_EVENT.value,
        "subject": "partner",
        "status": "confirmed",
        "perspective": "user_reported",
        "text": "昨天我们见面了。",
    }
    row.update(overrides)
    return row


def test_v2_candidate_without_temporal_fields_is_unknown() -> None:
    candidate = _candidate_from_row(
        _temporal_candidate_row(),
        reference_time=datetime(2026, 9, 4, 12, tzinfo=UTC),
    )

    assert candidate.time_kind == TimeKind.UNKNOWN
    assert candidate.occurred_at is None
    assert candidate.period_start is None
    assert candidate.period_end is None
    assert candidate.expires_at is None
    assert candidate.temporal_precision == TemporalPrecision.UNKNOWN


def test_v2_candidate_preserves_explicit_temporal_fields() -> None:
    candidate = _candidate_from_row(
        _temporal_candidate_row(
            kind=MemoryKind.INTERACTION_PATTERN.value,
            text="八月内回复速度较慢。",
            time_kind="interval",
            occurred_at="2026-08-15T12:00:00+00:00",
            period_start="2026-08-01T00:00:00+00:00",
            period_end="2026-08-31T23:59:59+00:00",
            expires_at="2026-09-30T00:00:00+00:00",
            temporal_precision="day",
        ),
        reference_time=datetime(2026, 9, 4, 12, tzinfo=UTC),
    )

    assert candidate.time_kind == TimeKind.INTERVAL
    assert candidate.occurred_at == datetime(2026, 8, 15, 12, tzinfo=UTC)
    assert candidate.period_start == datetime(2026, 8, 1, tzinfo=UTC)
    assert candidate.period_end == datetime(2026, 8, 31, 23, 59, 59, tzinfo=UTC)
    assert candidate.expires_at == datetime(2026, 9, 30, tzinfo=UTC)
    assert candidate.temporal_precision == TemporalPrecision.DAY


@pytest.mark.parametrize(
    "kind",
    [
        MemoryKind.INTERACTION_EVENT,
        MemoryKind.ADVICE_OUTCOME,
        MemoryKind.INTERACTION_PATTERN,
        MemoryKind.RELATIONSHIP_STATE,
        MemoryKind.PLANNED_EVENT,
        MemoryKind.ACTION_INTENT,
        MemoryKind.STABLE_FACT,
        MemoryKind.PREFERENCE,
    ],
)
def test_v2_temporal_cues_and_kind_do_not_fabricate_evidence(kind: MemoryKind) -> None:
    candidate = _candidate_from_row(
        _temporal_candidate_row(
            kind=kind.value,
            text="现在最近一直还是这样。",
        ),
        reference_time=datetime(2035, 1, 1, tzinfo=UTC),
    )

    assert candidate.time_kind == TimeKind.UNKNOWN
    assert candidate.occurred_at is None
    assert candidate.period_start is None
    assert candidate.period_end is None
    assert candidate.expires_at is None


def test_v2_unrelated_retention_uses_vector_candidate_denominator() -> None:
    rows = [
        {
            "expected_target_ids": ["O1"],
            "retrieval": {
                "vector": [
                    {"memory_id": "O1", "benchmark_role": "GOLD_TARGET", "rank": 1},
                    {"memory_id": "N1", "benchmark_role": "BACKGROUND", "rank": 2},
                    {"memory_id": "N2", "benchmark_role": "HARD_NEGATIVE", "rank": 3},
                ],
                "ranked": [
                    {"memory_id": "O1", "benchmark_role": "GOLD_TARGET", "rank": 1},
                    {"memory_id": "N1", "benchmark_role": "BACKGROUND", "rank": 2},
                ],
                "latency_ms": 0.0,
            },
            "candidate_pool_size": 3,
        }
    ]

    metrics = _retrieval_metrics(rows)

    assert metrics["unrelated_candidate_vector_count"] == 2
    assert metrics["unrelated_candidate_ranked_count"] == 1
    assert metrics["unrelated_candidate_retention_rate"] == 0.5


def _retrieval_metric_candidate(
    memory_id: str,
    rank: int,
    *,
    group_id: str | None = None,
) -> dict[str, object]:
    candidate: dict[str, object] = {
        "memory_id": memory_id,
        "rank": rank,
        "benchmark_role": "BACKGROUND",
    }
    if group_id is not None:
        candidate["equivalent_memory_group_id"] = group_id
    return candidate


def _retrieval_metric_row(
    *,
    expected_ids: list[str],
    inventory: list[dict[str, object]],
    vector: list[dict[str, object]],
    ranked: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "expected_target_ids": expected_ids,
        "retrieval": {
            "candidate_inventory": inventory,
            "vector": vector,
            "ranked": ranked,
            "latency_ms": 0.0,
            "cheap_ranking_latency_ms": 0.0,
            "vector_ranking_latency_ms": 0.0,
        },
        "candidate_pool_size": len(inventory),
    }


def test_v2_retrieval_metrics_report_raw_miss_and_equivalent_alias_hit() -> None:
    gold = _retrieval_metric_candidate("G1", 0, group_id="EQ-1")
    alias = _retrieval_metric_candidate("A1", 1, group_id="EQ-1")
    row = _retrieval_metric_row(
        expected_ids=["G1"],
        inventory=[gold, alias],
        vector=[alias],
        ranked=[alias],
    )

    metrics = _retrieval_metrics([row])

    assert metrics["raw_retrieval_recall_at_20"] == 0.0
    assert metrics["equivalence_aware_recall_at_20"] == 1.0


def test_v2_equivalence_recall_deduplicates_gold_group_denominator() -> None:
    gold_one = _retrieval_metric_candidate("G1", 0, group_id="EQ-1")
    gold_two = _retrieval_metric_candidate("G2", 0, group_id="EQ-1")
    alias = _retrieval_metric_candidate("A1", 1, group_id="EQ-1")
    row = _retrieval_metric_row(
        expected_ids=["G1", "G2"],
        inventory=[gold_one, gold_two, alias],
        vector=[alias],
        ranked=[alias],
    )

    metrics = _retrieval_metrics([row])

    assert metrics["retrieval_expected_target_count"] == 2
    assert metrics["raw_retrieval_recall_at_20"] == 0.0
    assert metrics["equivalence_aware_recall_at_20"] == 1.0


def test_v2_equivalence_mapping_is_scoped_to_each_case_inventory() -> None:
    scoped_gold = _retrieval_metric_candidate("G1", 0, group_id="EQ-1")
    scoped_alias = _retrieval_metric_candidate("A1", 1, group_id="EQ-1")
    first = _retrieval_metric_row(
        expected_ids=["G1"],
        inventory=[scoped_gold, scoped_alias],
        vector=[scoped_alias],
        ranked=[scoped_alias],
    )
    other_case_gold = _retrieval_metric_candidate("G2", 0)
    unavailable_alias = _retrieval_metric_candidate("A2", 1, group_id="EQ-1")
    second = _retrieval_metric_row(
        expected_ids=["G2"],
        inventory=[other_case_gold],
        vector=[unavailable_alias],
        ranked=[unavailable_alias],
    )

    metrics = _retrieval_metrics([first, second])

    # The first case's equivalence metadata must not make an alias valid in
    # another case whose fixed candidate inventory does not declare it.
    assert metrics["equivalence_aware_recall_at_20"] == 0.5


def test_v2_retrieval_metrics_count_duplicate_equivalence_slots_by_stage() -> None:
    inventory = [
        _retrieval_metric_candidate("G1", 0, group_id="EQ-1"),
        _retrieval_metric_candidate("A1", 0, group_id="EQ-1"),
        _retrieval_metric_candidate("B1", 0, group_id="EQ-2"),
        _retrieval_metric_candidate("B2", 0, group_id="EQ-2"),
        _retrieval_metric_candidate("B3", 0, group_id="EQ-2"),
        _retrieval_metric_candidate("N1", 0),
    ]
    vector = [
        _retrieval_metric_candidate("G1", 1, group_id="EQ-1"),
        _retrieval_metric_candidate("A1", 2, group_id="EQ-1"),
        _retrieval_metric_candidate("B1", 3, group_id="EQ-2"),
        _retrieval_metric_candidate("B2", 4, group_id="EQ-2"),
        _retrieval_metric_candidate("B3", 5, group_id="EQ-2"),
        _retrieval_metric_candidate("N1", 6),
    ]
    ranked = [
        _retrieval_metric_candidate("G1", 1, group_id="EQ-1"),
        _retrieval_metric_candidate("A1", 2, group_id="EQ-1"),
        _retrieval_metric_candidate("B1", 3, group_id="EQ-2"),
        _retrieval_metric_candidate("N1", 4),
    ]
    row = _retrieval_metric_row(
        expected_ids=["G1"],
        inventory=inventory,
        vector=vector,
        ranked=ranked,
    )

    metrics = _retrieval_metrics([row])

    assert metrics["equivalence_group_duplicate_slot_count_at_20"] == 3
    assert metrics["equivalence_group_duplicate_slot_count_at_5"] == 1


def test_v2_conditional_retention_and_end_to_end_recall_use_distinct_denominators() -> None:
    inventory = [_retrieval_metric_candidate(f"G{index}", 0) for index in range(1, 4)]
    vector = [
        _retrieval_metric_candidate("G1", 1),
        _retrieval_metric_candidate("G2", 2),
    ]
    ranked = [_retrieval_metric_candidate("G1", 1)]
    row = _retrieval_metric_row(
        expected_ids=["G1", "G2", "G3"],
        inventory=inventory,
        vector=vector,
        ranked=ranked,
    )

    metrics = _retrieval_metrics([row])

    assert metrics["raw_retrieval_recall_at_20"] == 0.6667
    assert metrics["conditional_gold_retention_at_5"] == 0.5
    assert metrics["gold_retention_at_5"] == 0.5
    assert metrics["end_to_end_gold_recall_at_5"] == 0.3333


def test_v2_error_attribution_distinguishes_embedding_failure_and_review_scope() -> None:
    case = _case("LTW2-001")
    non_gold_collision = {
        "gold_exact_text_collisions": [],
        "gold_semantic_tag_overlaps": [],
    }

    row = _error_row(
        case,
        RuntimeError("document embedding failed: unavailable"),
        non_gold_collision,
    )

    assert row["primary_failure_stage"] == "RETRIEVAL_ERROR"
    assert row["secondary_failure_stages"] == ["EMBEDDING_ERROR"]
    assert row["dataset_review_required"] is False


def test_v2_store_exception_is_primary_over_derived_write_policy_mismatch() -> None:
    case = _case("LTW2-001")
    stage = {
        "judge_status": "completed",
        "proposal": {
            "relation": case["expected_relation"],
            "target_memory_ids": list(case["expected_target_ids"]),
        },
        "validation": {"would_update": False},
    }
    failure = _attribute_failure(
        case=case,
        collision=None,
        retrieval_checks={"gold_in_top_20": True, "gold_retained_after_ranking": True},
        retrieved_checks={"target_set": True, "relation": True},
        relation_stage=stage,
        store_result={
            "error": "RuntimeError: isolated Store transaction failed",
            "checks": {"write_action": False},
        },
        retrieval={"vector": [], "ranked": []},
    )

    assert failure["primary"] == "STORE_APPLICATION_ERROR"
    assert failure["secondary"] == []


@pytest.mark.parametrize(
    ("error", "primary", "secondary"),
    [
        (
            TimeoutError("semantic relation judge timeout"),
            "MODEL_TRANSPORT_ERROR",
            [],
        ),
        (RuntimeError("store application failed"), "STORE_APPLICATION_ERROR", []),
        (ValueError("validator contract failed"), "VALIDATOR_ERROR", []),
    ],
)
def test_v2_error_attribution_keeps_non_retrieval_failures_distinct(
    error: Exception,
    primary: str,
    secondary: list[str],
) -> None:
    row = _error_row(_case("LTW2-001"), error, None)

    assert row["primary_failure_stage"] == primary
    assert row["secondary_failure_stages"] == secondary


@pytest.mark.asyncio
async def test_v2_report_exposes_coverage_and_model_telemetry() -> None:
    report = (await _evaluate("LTW2-001", relation=ClaimRelation.SAME, targets=["O001"]))[0]
    rendered = render_memory_longtail_write_v2_report(report)

    assert report["safety_coverage"]["custom_to_canonical_false_supersede"]["status"] == (
        "NOT_TESTED"
    )
    assert "judge_evaluated_count" in report["telemetry"]["judge"]
    assert "judge_relation_mismatch_count" in report["telemetry"]["judge"]
    assert "Model and Evaluation Telemetry" in rendered
    assert "Review-excluded Metrics" in rendered
    assert "Collision Details" in rendered


@pytest.mark.asyncio
async def test_v2_blocked_false_link_is_not_counted_as_applied_destructive_write() -> None:
    embedding = _ControlledEmbeddingProvider(target_texts=_gold_texts("LTW2-011"))
    report = await evaluate_memory_longtail_write_v2(
        CASES,
        SHARED,
        embedding_provider=embedding,
        # Deliberately propose a non-Gold target.  The production validator
        # must deny it before the isolated Store can supersede anything.
        judge=_ScriptedJudge(ClaimRelation.UPDATE, ["O052"]),
        case_id="LTW2-011",
        fail_on_error=True,
    )

    row = report["rows"][0]
    assert row["safety"]["false_link"] is True
    assert row["safety"]["false_link_blocked"] is True
    assert row["safety"]["false_link_authorized"] is False
    assert row["store"]["actual_supersede_memory_ids"] == []
    assert report["safety_metrics"]["proposal_safety_violation_count"] == 1
    assert report["safety_metrics"]["validator_blocked_false_link_count"] == 1
    assert report["safety_metrics"]["actual_destructive_write_count"] == 0
    assert report["safety_metrics"]["actual_destructive_write_violation_count"] == 0
    assert report["safety_metrics"]["destructive_safety_violation_count"] == 0
    assert report["safety_metrics"]["proposal_plus_write_safety_diagnostic_count"] == 1


@pytest.mark.asyncio
async def test_v2_live_retrieval_mode_uses_production_hybrid_retriever() -> None:
    report = await evaluate_memory_longtail_write_v2(
        CASES,
        SHARED,
        embedding_provider=FixtureTextEmbeddingProvider(),
        judge=_ScriptedJudge(ClaimRelation.SAME, ["O001"]),
        case_id="LTW2-001",
        use_production_retriever=True,
        fail_on_error=True,
    )

    row = report["rows"][0]
    assert row["retrieval"]["retrieval_engine"] == "HybridMemoryRetriever"
    assert report["parameters"]["retrieval_engine"] == ["HybridMemoryRetriever"]
    assert report["telemetry"]["embedding"]["query_call_count"] == 1
    assert report["telemetry"]["embedding"]["document_call_count"] == 1


@pytest.mark.asyncio
async def test_v2_multi_target_policy_is_observable_and_non_destructive() -> None:
    report, _, _ = await _evaluate(
        "LTW2-037",
        relation=ClaimRelation.COMPLEMENTARY,
        targets=["O181", "O182"],
    )

    assert report["multi_target_metrics"]["proposal_count"] == 2
    assert report["multi_target_metrics"]["retrieved_proposal_count"] == 1
    assert report["multi_target_metrics"]["exact_expected_multi_target_proposal_count"] == 1
    assert report["multi_target_metrics"]["policy_boundary_count"] == 1
    assert report["multi_target_metrics"]["overbroad_multi_target_proposal_count"] == 0
    assert report["multi_target_metrics"]["validator_denied_count"] == 2
    assert report["multi_target_metrics"]["destructive_multi_target_write_count"] == 0
    assert report["multi_target_metrics"]["status"] == "UNSUPPORTED_FAIL_CLOSED"


@pytest.mark.asyncio
async def test_v2_repeat_runs_expose_per_run_rows_and_consistency() -> None:
    embedding = _ControlledEmbeddingProvider(target_texts=_gold_texts("LTW2-001"))
    judge = _ScriptedJudge(ClaimRelation.SAME, ["O001"])
    repeated = await evaluate_memory_longtail_write_v2(
        CASES,
        SHARED,
        embedding_provider=embedding,
        judge=judge,
        case_id="LTW2-001",
        repeat=3,
        fail_on_error=True,
    )

    assert repeated["case_count"] == 1
    assert repeated["evaluated_row_count"] == 3
    assert repeated["repeat"] == 3
    assert repeated["run_count"] == 3
    assert [row["run_index"] for row in repeated["rows"]] == [1, 2, 3]
    consistency = repeated["hard_case_consistency"]
    assert consistency["case_count"] == 1
    assert consistency["relation_consistency_rate"] == 1.0
    assert consistency["target_consistency_rate"] == 1.0
    assert consistency["validator_consistency_rate"] == 1.0
    assert consistency["by_case"]["LTW2-001"]["run_count"] == 3
    assert len(judge.calls) == 6  # Oracle + Retrieved per run.
    assert len(embedding.document_inputs) == 3
    assert len(embedding.query_inputs) == 3


@pytest.mark.asyncio
async def test_v2_hard_case_filter_runs_the_frozen_v2_subset() -> None:
    embedding = _ControlledEmbeddingProvider(target_texts=set())
    judge = _ScriptedJudge(ClaimRelation.UNRELATED, [])
    report = await evaluate_memory_longtail_write_v2(
        CASES,
        SHARED,
        embedding_provider=embedding,
        judge=judge,
        hard_cases=True,
    )

    assert report["case_count"] == len(HARD_CASE_IDS)
    assert report["repeat"] == 1
    assert report["hard_cases_only"] is True
    assert report["hard_case_filter"]["status"] == "MATCHED"
    assert report["hard_case_filter"]["matched_ids"] == list(HARD_CASE_IDS)
    assert report["hard_case_filter"]["missing_ids"] == []
    assert len(embedding.document_inputs) == 1
    assert len(embedding.query_inputs) == len(HARD_CASE_IDS)


@pytest.mark.asyncio
async def test_v2_repeat_must_be_within_supported_bounds() -> None:
    with pytest.raises(ValueError, match="repeat must be between 1 and 100"):
        await evaluate_memory_longtail_write_v2(
            CASES,
            SHARED,
            embedding_provider=_ControlledEmbeddingProvider(target_texts=set()),
            judge=_FailingJudge(),
            case_id="LTW2-001",
            repeat=0,
        )


@pytest.mark.asyncio
async def test_v2_fixture_mode_is_deterministic_and_shadow_only() -> None:
    report = await evaluate_memory_longtail_write_v2_fixture(
        CASES,
        SHARED,
        case_id="LTW2-001",
    )

    assert report["evaluation_mode"] == "shadow_fixture_v2"
    assert report["fixture_configuration"]["model_calls"] == 0
    assert report["production_store_mutation_permitted"] is False
    assert report["store_mutation_permitted"] is False
    assert report["case_count"] == 1


@pytest.mark.asyncio
async def test_v2_fixture_live_comparison_is_same_scope() -> None:
    fixture = await evaluate_memory_longtail_write_v2_fixture(
        CASES,
        SHARED,
        case_id="LTW2-001",
    )
    live_like, _, _ = await _evaluate(
        "LTW2-001",
        relation=ClaimRelation.SAME,
        targets=["O001"],
    )

    comparison = compare_memory_longtail_write_v2_reports(fixture, live_like)
    assert comparison["status"] == "COMPARABLE"
    assert comparison["scope"]["same_dataset"] is True
    assert "retrieved_relation_accuracy" in comparison["metrics"]
    assert comparison["metrics"]["retrieved_relation_accuracy"]["fixture"] == 1.0


def test_v2_fixture_live_comparison_rejects_repeat_or_rank_scope_mismatch() -> None:
    base = {
        "dataset": {
            "case_sha256": "cases",
            "shared_bank_sha256": "shared",
        },
        "filters": {"case_id": None, "slice": None},
        "case_count": 1,
        "repeat": 1,
        "hard_cases_only": False,
        "parameters": {"vector_top_k": 20, "cheap_rank_top_n": 5},
    }
    fixture = dict(base)
    live = dict(base, repeat=3)
    assert compare_memory_longtail_write_v2_reports(fixture, live)["status"] == "SCOPE_MISMATCH"

    live = dict(base, parameters={"vector_top_k": 10, "cheap_rank_top_n": 5})
    assert compare_memory_longtail_write_v2_reports(fixture, live)["status"] == "SCOPE_MISMATCH"


def _final_validation_report(*, hard: bool = False) -> dict[str, Any]:
    report: dict[str, Any] = {
        "dataset": {
            "status": "PASS",
            "structural_validation": {"candidate_pool_contract_status": "PASS"},
        },
        "case_count": len(HARD_CASE_IDS) if hard else EXPECTED_CASE_COUNT,
        "retrieval_metrics": {
            "raw_retrieval_recall_at_20": 0.95,
            "equivalence_aware_recall_at_20": 0.97,
        },
        "retrieved_relation_metrics": {
            "relation_accuracy": 0.75,
            "macro_f1": 0.70,
            "target_set_accuracy": 0.60,
            "target_micro_f1": 0.70,
        },
        "safety_metrics": {
            "actual_destructive_write_violation_count": 0,
            "destructive_safety_violation_count": 0,
            "historical_event_preservation_rate": 1.0,
        },
        "write_metrics": {"store_application_error_count": 0},
        "telemetry": {
            "embedding": {"failure_count": 0},
            "judge": {"failure_count": 0},
        },
    }
    if hard:
        report.update(
            {
                "repeat": 3,
                "hard_case_filter": {
                    "status": "MATCHED",
                    "matched_ids": list(HARD_CASE_IDS),
                },
                "hard_case_consistency": {
                    "relation_consistency_rate": 1.0,
                    "target_consistency_rate": 1.0,
                    "validator_consistency_rate": 1.0,
                    "retrieval_top5_order_consistency_rate": 1.0,
                },
            }
        )
    return report


def test_v2_final_live_status_combines_full_quality_and_hard_stability() -> None:
    full, hard = finalize_memory_longtail_write_v2_live_validation(
        _final_validation_report(),
        _final_validation_report(hard=True),
        repository={"repo": "test", "branch": "main", "commit_sha": "abc"},
    )

    assert full["status"] == "MEMORY_V2_FREEZE_READY"
    assert hard["status"] == "MEMORY_V2_FREEZE_READY"
    assert full["final_validation"]["checks"]["hard_case_scope_complete"] is True
    assert full["production_store_mutation_permitted"] is False
    markdown = render_memory_longtail_write_v2_report(full)
    assert "## Final Questions" in markdown
    assert "13. Current status: **MEMORY_V2_FREEZE_READY**" in markdown
    assert "raw_retrieval_recall_at_20" in markdown


@pytest.mark.parametrize(
    ("mutation", "expected_status"),
    [
        ("retrieval", "RETRIEVAL_REMEDIATION_REQUIRED"),
        ("relation", "SEMANTIC_JUDGE_REMEDIATION_REQUIRED"),
        ("stability", "SEMANTIC_JUDGE_REMEDIATION_REQUIRED"),
        ("safety", "SAFETY_REGRESSION"),
    ],
)
def test_v2_final_live_status_fails_closed_by_stage(
    mutation: str,
    expected_status: str,
) -> None:
    full = _final_validation_report()
    hard = _final_validation_report(hard=True)
    if mutation == "retrieval":
        full["retrieval_metrics"]["raw_retrieval_recall_at_20"] = 0.94
    elif mutation == "relation":
        full["retrieved_relation_metrics"]["relation_accuracy"] = 0.74
    elif mutation == "stability":
        hard["hard_case_consistency"]["target_consistency_rate"] = 0.6667
    else:
        full["safety_metrics"]["actual_destructive_write_violation_count"] = 1

    finalized, _ = finalize_memory_longtail_write_v2_live_validation(
        full,
        hard,
        repository={"repo": "test", "branch": "main", "commit_sha": "abc"},
    )

    assert finalized["status"] == expected_status

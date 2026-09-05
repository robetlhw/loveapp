"""Retrieval-aware Long-tail Memory Write V2 evaluation.

The benchmark deliberately lives outside the production write path. Natural
language text is embedded and ranked here, while relation proposals still pass
through the production long-tail validator before an isolated in-memory Store
may apply a write batch. No production Store is reachable from this module.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

from loveapp.adapters.memory.in_memory import InMemoryMemoryStore
from loveapp.application.memory_retrieval import (
    HybridMemoryRetriever,
    MemoryRetrievalMode,
    RetrievedMemory,
)
from loveapp.application.memory_semantic_relations import (
    LongTailSemanticRelationValidator,
)
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.memory import (
    AdmissionDecision,
    ClaimRelation,
    EvidenceExplicitness,
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    PredicateType,
    TemporalPrecision,
    TimeKind,
)
from loveapp.domain.memory_lifecycle import MemoryRole, memory_role
from loveapp.domain.memory_semantic_relation import (
    LongTailRelationValidation,
    SemanticRelationProposal,
)
from loveapp.domain.memory_write import MemoryWriteBatch, MemoryWriteOperation
from loveapp.ports.embeddings import EmbeddingProvider
from loveapp.ports.memory import SemanticRelationJudge

REPORT_VERSION = "memory-longtail-write-v2-final-live-v1"
EXPECTED_SHARED_MEMORY_COUNT = 120
EXPECTED_CASE_COUNT = 40
EXPECTED_OVERLAY_MEMORY_COUNT = 200
EXPECTED_SHARED_POOL_COUNTS = {
    "profile_preference": 30,
    "relationship_patterns": 30,
    "events": 30,
    "plans_intents": 30,
}
EXPECTED_SLICE_COUNTS = {
    "same_semantic_rephrase": 5,
    "complementary_detail": 5,
    "sustained_update": 5,
    "contradiction_authority": 5,
    "unrelated_hard_negative": 5,
    "temporal_event_identity": 5,
    "event_vs_pattern": 5,
    "multi_target_ambiguity": 5,
}
# One representative difficult case per V2 semantic slice.  Keep this list in
# the evaluator (rather than deriving it from model output) so a repeat run is
# reproducible and the report makes its scope explicit.
HARD_CASE_IDS = (
    "LTW2-004",
    "LTW2-011",
    "LTW2-016",
    "LTW2-021",
    "LTW2-026",
    "LTW2-031",
    "LTW2-036",
    "LTW2-040",
)
ACTIVE_STATUSES = {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
TARGETED_RELATIONS = {
    ClaimRelation.SAME,
    ClaimRelation.UPDATE,
    ClaimRelation.CONTRADICTION,
    ClaimRelation.COMPLEMENTARY,
}
UNRELATED_BENCHMARK_ROLES = frozenset({"HARD_NEGATIVE", "BACKGROUND"})
REFERENCE_TIME = datetime(2026, 9, 4, 12, tzinfo=UTC)


def _expected_retrieval_candidate_ids(case: Mapping[str, Any]) -> list[str]:
    """Return Gold IDs used to measure candidate retrieval.

    The frozen benchmark keeps ``expected_target_ids`` as the historical Gold
    field.  For an ``UNCERTAIN`` (or ``UNRELATED``) relation those IDs describe
    the evidence candidates that should be surfaced, not memories that the
    semantic proposal is allowed to target.  Keeping this helper separate from
    semantic targets prevents a retrieval miss from changing the relation
    contract.
    """

    values = case.get("expected_target_ids", [])
    return [str(value) for value in values] if isinstance(values, list) else []


def _expected_semantic_target_ids(case: Mapping[str, Any]) -> list[str]:
    """Return IDs that a relation proposal is expected to name.

    Target-bearing relations may name the documented Gold IDs.  Relations whose
    semantics deliberately carry no target must always have an empty target
    set, even when the benchmark supplies reference candidates for retrieval.
    """

    relation_value = case.get("expected_relation")
    try:
        relation = ClaimRelation(relation_value)
    except (TypeError, ValueError):
        # Validation reports the malformed relation separately.  Keep this
        # helper fail-closed for diagnostic/error-row paths.
        return []
    return (
        _expected_retrieval_candidate_ids(case)
        if relation in TARGETED_RELATIONS
        else []
    )


def _row_expected_retrieval_candidate_ids(row: Mapping[str, Any]) -> list[str]:
    values = row.get("expected_retrieval_candidate_ids")
    if isinstance(values, list):
        return [str(value) for value in values]
    return _expected_retrieval_candidate_ids(row)


def _row_expected_semantic_target_ids(row: Mapping[str, Any]) -> list[str]:
    # Rows produced by the evaluator carry this as a derived convenience
    # field.  When the source relation is available, recompute the contract
    # from it instead of trusting a stale or externally supplied value.  This
    # keeps ``UNCERTAIN``/``UNRELATED`` fail-closed even for hand-built report
    # rows.
    if row.get("expected_relation") is not None:
        return _expected_semantic_target_ids(row)
    values = row.get("expected_semantic_target_ids")
    if isinstance(values, list):
        return [str(value) for value in values]
    return _expected_semantic_target_ids(row)


class LongTailWriteV2EvaluationError(ValueError):
    """Raised when the frozen V2 dataset does not satisfy its shape contract."""


class FixtureTextEmbeddingProvider:
    """Small, deterministic text-only embedding adapter for fixture baselines.

    This adapter is deliberately benchmark-only.  It uses hashed character
    n-grams, never reads benchmark roles/tags, and does not contact a model
    service.  The live evaluator continues to receive the production
    SentenceTransformer provider through dependency injection.
    """

    model_name = "fixture-char-ngram"
    model_version = "v1"
    _dimension = 256

    @property
    def is_ready(self) -> bool:
        return True

    def start_warmup(self) -> Any:
        async def _ready() -> None:
            return None

        return _ready()

    async def warmup(self) -> None:
        return None

    async def dimension(self) -> int:
        return self._dimension

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._encode(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._encode(text)

    async def aclose(self) -> None:
        return None

    @classmethod
    def _encode(cls, text: str) -> list[float]:
        normalized = unicodedata.normalize("NFKC", text).casefold().strip()
        # Character n-grams keep this baseline language-agnostic (including
        # Chinese text, where whitespace tokenization is not reliable).
        grams = [normalized[index : index + 2] for index in range(max(len(normalized) - 1, 0))]
        grams.extend(normalized[index : index + 3] for index in range(max(len(normalized) - 2, 0)))
        vector = [0.0] * cls._dimension
        for gram in grams or [normalized]:
            digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % cls._dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector


class _ObservedEmbeddingProvider:
    """Collect embedding telemetry while delegating to a real provider.

    The production ``HybridMemoryRetriever`` owns the query/document calls in
    Live mode.  This small proxy keeps those calls observable without changing
    the provider or the production retrieval contract.  Fixture mode continues
    to use its direct, staged embedding path and does not need this proxy.
    """

    def __init__(self, delegate: EmbeddingProvider, telemetry: dict[str, Any]) -> None:
        self._delegate = delegate
        self._telemetry = telemetry
        self.model_name = str(getattr(delegate, "model_name", "unknown"))
        self.model_version = str(
            getattr(delegate, "model_version", getattr(delegate, "version", "unknown"))
        )

    @property
    def is_ready(self) -> bool:
        return bool(getattr(self._delegate, "is_ready", True))

    def start_warmup(self) -> Any:
        return self._delegate.start_warmup()

    async def warmup(self) -> None:
        await self._delegate.warmup()

    async def dimension(self) -> int:
        return await self._delegate.dimension()

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        started = perf_counter()
        self._telemetry["document_call_count"] += 1
        self._telemetry["document_text_count"] += len(texts)
        try:
            return await self._delegate.embed_documents(texts)
        except Exception:
            self._telemetry["failure_count"] += 1
            self._telemetry["document_failure_count"] += 1
            raise
        finally:
            self._telemetry.setdefault("document_latencies_ms", []).append(
                round((perf_counter() - started) * 1000, 3)
            )

    async def embed_query(self, text: str) -> list[float]:
        started = perf_counter()
        self._telemetry["query_call_count"] += 1
        try:
            return await self._delegate.embed_query(text)
        except Exception:
            self._telemetry["failure_count"] += 1
            self._telemetry["query_failure_count"] += 1
            raise
        finally:
            self._telemetry.setdefault("query_latencies_ms", []).append(
                round((perf_counter() - started) * 1000, 3)
            )

    async def aclose(self) -> None:
        # The evaluator does not own the injected provider.  Keep this method
        # intentionally no-op so a retriever cannot accidentally close it.
        return None


class FixtureV2SemanticRelationJudge:
    """Replay frozen V2 labels through the real validator/store path.

    This is a reviewed fixture upper bound, not a substitute for the live
    semantic judge.  It returns the case's frozen relation and only targets
    that are present in the supplied candidate set, making retrieval losses
    visible while keeping the same downstream validation contract.
    """

    def __init__(self, cases: Sequence[Mapping[str, Any]]) -> None:
        self._by_text = {
            str(case["incoming"]["text"]): case
            for case in cases
            if isinstance(case.get("incoming"), Mapping)
        }
        self.calls: list[list[str]] = []

    async def propose_relation(
        self,
        *,
        incoming: MemoryCandidate,
        candidates: list[MemoryItem],
        trace: object | None = None,
    ) -> SemanticRelationProposal:
        del trace
        self.calls.append([candidate.id for candidate in candidates])
        case = self._by_text.get(incoming.original_text)
        if case is None:
            return _fail_closed_proposal("Fixture has no reviewed case for incoming text.")
        relation = ClaimRelation(case["expected_relation"])
        candidate_ids = {candidate.id for candidate in candidates}
        targets = [
            str(memory_id)
            for memory_id in _expected_semantic_target_ids(case)
            if str(memory_id) in candidate_ids
        ]
        return SemanticRelationProposal(
            relation=relation,
            target_memory_ids=targets,
            same_semantic_dimension=relation in TARGETED_RELATIONS,
            confidence=0.99,
            reason="Reviewed V2 fixture proposal; not a model prediction.",
            judge_model="fixture-v2-reviewed",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            latency_ms=0.0,
        )

    async def aclose(self) -> None:
        return None


async def evaluate_memory_longtail_write_v2_fixture(
    case_path: Path,
    shared_bank_path: Path,
    *,
    case_id: str | None = None,
    slice_name: str | None = None,
    vector_limit: int = 20,
    rank_limit: int = 5,
    fail_on_error: bool = False,
    repeat: int = 1,
    hard_cases: bool = False,
) -> dict[str, Any]:
    """Run a deterministic V2 fixture baseline through identical governance.

    The result is explicitly labelled ``shadow_fixture_v2``.  It is useful for
    separating retrieval/write plumbing from live model variance, but its
    reviewed Judge labels must never be interpreted as production behavior.
    """

    dataset = load_memory_longtail_write_v2_dataset(case_path, shared_bank_path)
    embedding = FixtureTextEmbeddingProvider()
    judge = FixtureV2SemanticRelationJudge(dataset["cases"])
    report = await evaluate_memory_longtail_write_v2(
        case_path,
        shared_bank_path,
        embedding_provider=embedding,
        judge=judge,
        case_id=case_id,
        slice_name=slice_name,
        vector_limit=vector_limit,
        rank_limit=rank_limit,
        fail_on_error=fail_on_error,
        repeat=repeat,
        hard_cases=hard_cases,
    )
    report["evaluation_mode"] = "shadow_fixture_v2"
    report["methodology"] = (
        "text_only_hashed_char_ngram_retrieval_with_reviewed_fixture_judge_"
        "and_production_validator_on_isolated_store"
    )
    report["fixture_configuration"] = {
        "embedding_model": embedding.model_name,
        "embedding_model_version": embedding.model_version,
        "embedding_dimension": embedding._dimension,
        "judge_model": "fixture-v2-reviewed",
        "model_calls": 0,
        "production_store_mutation_permitted": False,
    }
    return report


def compare_memory_longtail_write_v2_reports(
    fixture: Mapping[str, Any],
    live: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a same-scope Fixture vs Live metric table.

    The comparison intentionally keeps Oracle and Retrieved relation metrics
    separate.  It does not import V1/realistic baselines whose denominators
    differ from this 40-case V2 dataset.
    """

    metric_paths = (
        ("retrieval_hit_at_1", "retrieval_metrics", "retrieval_hit_at_1"),
        ("retrieval_hit_at_3", "retrieval_metrics", "retrieval_hit_at_3"),
        ("retrieval_hit_at_5", "retrieval_metrics", "retrieval_hit_at_5"),
        ("retrieval_recall_at_5", "retrieval_metrics", "retrieval_recall_at_5"),
        ("retrieval_recall_at_20", "retrieval_metrics", "retrieval_recall_at_20"),
        (
            "raw_retrieval_recall_at_20",
            "retrieval_metrics",
            "raw_retrieval_recall_at_20",
        ),
        (
            "equivalence_aware_recall_at_20",
            "retrieval_metrics",
            "equivalence_aware_recall_at_20",
        ),
        (
            "conditional_gold_retention_at_5",
            "retrieval_metrics",
            "conditional_gold_retention_at_5",
        ),
        (
            "end_to_end_gold_recall_at_5",
            "retrieval_metrics",
            "end_to_end_gold_recall_at_5",
        ),
        ("gold_retention_at_5", "retrieval_metrics", "gold_retention_at_5"),
        ("oracle_relation_accuracy", "oracle_relation_metrics", "relation_accuracy"),
        ("oracle_relation_macro_f1", "oracle_relation_metrics", "macro_f1"),
        ("retrieved_relation_accuracy", "retrieved_relation_metrics", "relation_accuracy"),
        ("retrieved_relation_macro_f1", "retrieved_relation_metrics", "macro_f1"),
        ("oracle_update_precision", "oracle_relation_metrics", "update_precision"),
        ("retrieved_update_precision", "retrieved_relation_metrics", "update_precision"),
        ("oracle_target_set_accuracy", "oracle_relation_metrics", "target_set_accuracy"),
        ("retrieved_target_set_accuracy", "retrieved_relation_metrics", "target_set_accuracy"),
        (
            "oracle_target_micro_precision",
            "oracle_relation_metrics",
            "target_micro_precision",
        ),
        (
            "retrieved_target_micro_precision",
            "retrieved_relation_metrics",
            "target_micro_precision",
        ),
        ("oracle_target_micro_recall", "oracle_relation_metrics", "target_micro_recall"),
        (
            "retrieved_target_micro_recall",
            "retrieved_relation_metrics",
            "target_micro_recall",
        ),
        ("oracle_target_micro_f1", "oracle_relation_metrics", "target_micro_f1"),
        ("retrieved_target_micro_f1", "retrieved_relation_metrics", "target_micro_f1"),
        ("store_action_accuracy", "write_metrics", "store_action_accuracy"),
        (
            "destructive_safety_violation_count",
            "safety_metrics",
            "destructive_safety_violation_count",
        ),
        (
            "proposal_safety_violation_count",
            "safety_metrics",
            "proposal_safety_violation_count",
        ),
        (
            "actual_destructive_write_violation_count",
            "safety_metrics",
            "actual_destructive_write_violation_count",
        ),
        (
            "actual_destructive_write_count",
            "safety_metrics",
            "actual_destructive_write_count",
        ),
        (
            "proposal_plus_write_safety_diagnostic_count",
            "safety_metrics",
            "proposal_plus_write_safety_diagnostic_count",
        ),
    )

    def value(report: Mapping[str, Any], key: str, section: str) -> Any:
        section_value = report.get(section, {})
        return section_value.get(key) if isinstance(section_value, Mapping) else None

    fixture_dataset = fixture.get("dataset", {})
    live_dataset = live.get("dataset", {})
    fixture_repeat = int(fixture.get("repeat", 1) or 1)
    live_repeat = int(live.get("repeat", 1) or 1)
    fixture_parameters = fixture.get("parameters", {})
    live_parameters = live.get("parameters", {})
    same_scope = (
        fixture_dataset.get("case_sha256") == live_dataset.get("case_sha256")
        and fixture_dataset.get("shared_bank_sha256") == live_dataset.get("shared_bank_sha256")
        and fixture.get("filters") == live.get("filters")
        and fixture.get("case_count") == live.get("case_count")
        and fixture_repeat == live_repeat
        and fixture.get("hard_cases_only", False) == live.get("hard_cases_only", False)
        and isinstance(fixture_parameters, Mapping)
        and isinstance(live_parameters, Mapping)
        and fixture_parameters.get("vector_top_k") == live_parameters.get("vector_top_k")
        and fixture_parameters.get("cheap_rank_top_n")
        == live_parameters.get("cheap_rank_top_n")
    )
    return {
        "status": "COMPARABLE" if same_scope else "SCOPE_MISMATCH",
        "scope": {
            "same_dataset": same_scope,
            "fixture_case_count": fixture.get("case_count"),
            "live_case_count": live.get("case_count"),
            "fixture_repeat": fixture_repeat,
            "live_repeat": live_repeat,
            "fixture_hard_cases_only": bool(fixture.get("hard_cases_only", False)),
            "live_hard_cases_only": bool(live.get("hard_cases_only", False)),
            "fixture_vector_top_k": (
                fixture_parameters.get("vector_top_k")
                if isinstance(fixture_parameters, Mapping)
                else None
            ),
            "live_vector_top_k": (
                live_parameters.get("vector_top_k")
                if isinstance(live_parameters, Mapping)
                else None
            ),
            "fixture_cheap_rank_top_n": (
                fixture_parameters.get("cheap_rank_top_n")
                if isinstance(fixture_parameters, Mapping)
                else None
            ),
            "live_cheap_rank_top_n": (
                live_parameters.get("cheap_rank_top_n")
                if isinstance(live_parameters, Mapping)
                else None
            ),
            "fixture_filters": fixture.get("filters"),
            "live_filters": live.get("filters"),
        },
        "methodology": (
            "fixture uses reviewed labels with deterministic benchmark retrieval; "
            "live uses the production HybridMemoryRetriever, embedding, and semantic "
            "Judge. The comparison is diagnostic, not a production quality claim."
        ),
        "metrics": {
            key: {
                "fixture": value(fixture, source_key, section),
                "live": value(live, source_key, section),
            }
            for key, section, source_key in metric_paths
        },
    }


def load_memory_longtail_write_v2_dataset(
    case_path: Path,
    shared_bank_path: Path,
) -> dict[str, Any]:
    """Load and strictly validate the V2 shared bank and case overlay files."""

    shared_rows = _read_jsonl(shared_bank_path)
    cases = _read_jsonl(case_path)
    _validate_shared_rows(shared_rows)
    _validate_cases(cases, shared_rows)
    collision_audit = _audit_dataset_collisions(cases, shared_rows)
    # V2 uses one fixed candidate contract for every case.  ``shared_pools``
    # remains in the JSONL as descriptive metadata/backward-compatible input,
    # but it is not a retrieval filter: every case sees all 120 shared rows
    # plus its five case-local overlays.
    pool_sizes = [EXPECTED_SHARED_MEMORY_COUNT + len(case["overlay"]) for case in cases]
    semantic_target_contract = _semantic_target_contract_summary(cases)
    return {
        "version": REPORT_VERSION,
        "shared_memories": shared_rows,
        "cases": cases,
        "structural_validation": {
            "shared_memory_count": len(shared_rows),
            "shared_pool_counts": dict(sorted(Counter(row["pool"] for row in shared_rows).items())),
            "case_count": len(cases),
            "slice_counts": dict(sorted(Counter(case["slice"] for case in cases).items())),
            "overlay_memory_count": sum(len(case["overlay"]) for case in cases),
            "candidate_pool_size_counts": dict(sorted(Counter(pool_sizes).items())),
            "candidate_pool_min": min(pool_sizes),
            "candidate_pool_max": max(pool_sizes),
            "candidate_pool_avg": round(mean(pool_sizes), 4),
            "document_approximate_pool_claim": 125,
            "candidate_pool_contract_status": "PASS",
            "candidate_pool_contract_reason": (
                "every case uses all 120 shared memories plus its five overlays; "
                "the case-declared shared_pools field is descriptive only."
            ),
            "semantic_target_contract": semantic_target_contract,
        },
        "collision_audit": collision_audit,
        "dataset_status": collision_audit["status"],
    }


def _semantic_target_contract_summary(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Describe cases whose Gold IDs are retrieval references only.

    This is an evaluator contract, not a mutation permission.  Keeping the
    summary alongside structural validation makes the exceptional UNCERTAIN
    shape visible without changing the frozen JSONL expectations.
    """

    reference_only = [
        str(case.get("case_id"))
        for case in cases
        if _expected_retrieval_candidate_ids(case)
        and not _expected_semantic_target_ids(case)
    ]
    return {
        "reference_only_case_ids": reference_only,
        "reference_only_case_count": len(reference_only),
        "status": "EXPLICIT" if reference_only else "NOT_REQUIRED",
        "rule": "non-target-bearing relations have an empty semantic target set",
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise LongTailWriteV2EvaluationError(f"dataset is not UTF-8 JSONL: {path}") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LongTailWriteV2EvaluationError(
                f"invalid JSON at {path}:{line_number}: {exc}"
            ) from exc
        if not isinstance(row, dict):
            raise LongTailWriteV2EvaluationError(f"{path}:{line_number} must contain an object")
        rows.append(row)
    return rows


def _validate_shared_rows(rows: list[dict[str, Any]]) -> None:
    if len(rows) != EXPECTED_SHARED_MEMORY_COUNT:
        raise LongTailWriteV2EvaluationError(
            f"expected {EXPECTED_SHARED_MEMORY_COUNT} shared memories, got {len(rows)}"
        )
    ids = [_validate_memory_row(row, context="shared memory") for row in rows]
    if len(set(ids)) != len(ids):
        raise LongTailWriteV2EvaluationError("duplicate shared memory_id")
    counts = Counter(str(row.get("pool")) for row in rows)
    if dict(counts) != EXPECTED_SHARED_POOL_COUNTS:
        raise LongTailWriteV2EvaluationError(f"unexpected shared pool distribution: {dict(counts)}")
    expected_ids = [
        *(f"SP{index:03d}" for index in range(1, 31)),
        *(f"SR{index:03d}" for index in range(1, 31)),
        *(f"SE{index:03d}" for index in range(1, 31)),
        *(f"SI{index:03d}" for index in range(1, 31)),
    ]
    if ids != expected_ids:
        raise LongTailWriteV2EvaluationError(
            "shared memory IDs or ordering differ from SP/SR/SE/SI 001..030"
        )
    for row in rows:
        if row.get("pool") not in EXPECTED_SHARED_POOL_COUNTS:
            raise LongTailWriteV2EvaluationError(
                f"{row['memory_id']} has unknown pool: {row.get('pool')}"
            )
        metadata = row.get("benchmark_metadata")
        if not isinstance(metadata, dict) or metadata.get("source") != "shared":
            raise LongTailWriteV2EvaluationError(
                f"{row['memory_id']} requires shared benchmark metadata"
            )


def _validate_cases(
    cases: list[dict[str, Any]],
    shared_rows: list[dict[str, Any]],
) -> None:
    if len(cases) != EXPECTED_CASE_COUNT:
        raise LongTailWriteV2EvaluationError(
            f"expected {EXPECTED_CASE_COUNT} cases, got {len(cases)}"
        )
    expected_case_ids = [f"LTW2-{index:03d}" for index in range(1, 41)]
    actual_case_ids = [case.get("case_id") for case in cases]
    if actual_case_ids != expected_case_ids:
        raise LongTailWriteV2EvaluationError("case IDs or ordering differ from LTW2-001..LTW2-040")
    slice_counts = Counter(str(case.get("slice")) for case in cases)
    if dict(slice_counts) != EXPECTED_SLICE_COUNTS:
        raise LongTailWriteV2EvaluationError(f"unexpected slice distribution: {dict(slice_counts)}")
    known_pools = {row["pool"] for row in shared_rows}
    overlay_ids: list[str] = []
    all_shared_ids = {row["memory_id"] for row in shared_rows}
    for case in cases:
        case_id = str(case["case_id"])
        incoming = case.get("incoming")
        if not isinstance(incoming, dict):
            raise LongTailWriteV2EvaluationError(f"{case_id} requires incoming")
        _validate_claim_shape(incoming, context=f"{case_id}.incoming")
        pools = case.get("shared_pools")
        if (
            not isinstance(pools, list)
            or len(set(pools)) != len(pools)
            or set(pools) != known_pools
        ):
            raise LongTailWriteV2EvaluationError(
                f"{case_id}.shared_pools must enumerate all shared pools; "
                "the field is descriptive and cannot narrow the candidate bank"
            )
        overlay = case.get("overlay")
        if not isinstance(overlay, list) or len(overlay) != 5:
            raise LongTailWriteV2EvaluationError(
                f"{case_id} must contain exactly five overlay memories"
            )
        case_overlay_ids = [
            _validate_memory_row(row, context=f"{case_id}.overlay") for row in overlay
        ]
        overlay_ids.extend(case_overlay_ids)
        expected_ids = case.get("expected_target_ids")
        if (
            not isinstance(expected_ids, list)
            or any(not isinstance(value, str) for value in expected_ids)
            or len(expected_ids) != len(set(expected_ids))
            or not set(expected_ids) <= set(case_overlay_ids)
        ):
            raise LongTailWriteV2EvaluationError(f"{case_id}.expected_target_ids is invalid")
        try:
            ClaimRelation(case.get("expected_relation"))
        except ValueError as exc:
            raise LongTailWriteV2EvaluationError(f"{case_id}.expected_relation is invalid") from exc
        gold_role_ids = {
            row["memory_id"] for row in overlay if row["benchmark_metadata"].get("role") == "gold"
        }
        if gold_role_ids != set(expected_ids):
            raise LongTailWriteV2EvaluationError(
                f"{case_id} Gold roles do not match expected_target_ids"
            )
    if len(overlay_ids) != EXPECTED_OVERLAY_MEMORY_COUNT:
        raise LongTailWriteV2EvaluationError(
            f"expected {EXPECTED_OVERLAY_MEMORY_COUNT} overlays, got {len(overlay_ids)}"
        )
    expected_overlay_ids = [f"O{index:03d}" for index in range(1, 201)]
    if overlay_ids != expected_overlay_ids:
        raise LongTailWriteV2EvaluationError("overlay IDs or ordering differ from O001..O200")
    if set(overlay_ids) & all_shared_ids:
        raise LongTailWriteV2EvaluationError("shared and overlay memory IDs must not collide")
    _validate_equivalence_contract(cases, shared_rows)


def _validate_equivalence_contract(
    cases: Sequence[Mapping[str, Any]],
    shared_rows: Sequence[Mapping[str, Any]],
) -> None:
    """Validate the explicit identity contract for exact duplicate claims.

    A shared row and an overlay row may carry the same text only when both
    rows declare the same equivalence group.  Semantic-tag overlap is
    intentionally *not* an identity collision and therefore does not require
    a group.  Group ids are scoped to exact textual identity in this benchmark
    so target scoring can safely treat aliases as one Gold target.
    """

    rows: list[Mapping[str, Any]] = [
        *shared_rows,
        *(overlay for case in cases for overlay in case["overlay"]),
    ]
    by_text: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_group: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        text = str(row["text"])
        by_text[text].append(row)
        group = row.get("equivalent_memory_group_id")
        if group is not None:
            by_group[str(group)].append(row)

    for _text, matches in by_text.items():
        if len(matches) < 2:
            continue
        groups = {row.get("equivalent_memory_group_id") for row in matches}
        if len(groups) != 1 or None in groups:
            ids = [str(row["memory_id"]) for row in matches]
            raise LongTailWriteV2EvaluationError(
                "exact duplicate memories require one shared "
                f"equivalent_memory_group_id: {ids}"
            )

    for group, matches in by_group.items():
        if len(matches) < 2:
            raise LongTailWriteV2EvaluationError(
                f"equivalent_memory_group_id {group!r} must identify at least two rows"
            )
        texts = {str(row["text"]) for row in matches}
        if len(texts) != 1:
            ids = [str(row["memory_id"]) for row in matches]
            raise LongTailWriteV2EvaluationError(
                "equivalent_memory_group_id must only contain exact duplicate "
                f"text rows: {group!r} -> {ids}"
            )


def _validate_memory_row(row: object, *, context: str) -> str:
    if not isinstance(row, dict):
        raise LongTailWriteV2EvaluationError(f"{context} must be an object")
    memory_id = row.get("memory_id")
    if not isinstance(memory_id, str) or not memory_id:
        raise LongTailWriteV2EvaluationError(f"{context} requires memory_id")
    _validate_claim_shape(row, context=f"{context} {memory_id}")
    metadata = row.get("benchmark_metadata")
    if (
        not isinstance(metadata, dict)
        or not isinstance(metadata.get("semantic_tag"), str)
        or not metadata["semantic_tag"]
    ):
        raise LongTailWriteV2EvaluationError(
            f"{context} {memory_id} requires benchmark_metadata.semantic_tag"
        )
    equivalent_group = row.get("equivalent_memory_group_id")
    if equivalent_group is not None and (
        not isinstance(equivalent_group, str) or not equivalent_group.strip()
    ):
        raise LongTailWriteV2EvaluationError(
            f"{context} {memory_id} has an invalid equivalent_memory_group_id"
        )
    return memory_id


def _validate_claim_shape(row: Mapping[str, Any], *, context: str) -> None:
    text = row.get("text")
    if not isinstance(text, str) or not text.strip():
        raise LongTailWriteV2EvaluationError(f"{context} requires text")
    try:
        MemoryKind(row.get("kind"))
        MemoryStatus(row.get("status", MemoryStatus.CONFIRMED))
        MemoryPerspective(row.get("perspective", MemoryPerspective.USER_REPORTED))
    except ValueError as exc:
        raise LongTailWriteV2EvaluationError(f"{context} contains an invalid domain enum") from exc
    subject = row.get("subject")
    if not isinstance(subject, str) or not subject.strip():
        raise LongTailWriteV2EvaluationError(f"{context} requires subject")


def _audit_dataset_collisions(
    cases: list[dict[str, Any]],
    shared_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    exact_case_count = 0
    exact_gold_case_count = 0
    equivalent_exact_case_count = 0
    equivalent_gold_case_count = 0
    unresolved_exact_case_count = 0
    unresolved_gold_case_count = 0
    tag_case_count = 0
    tag_gold_case_count = 0
    for case in cases:
        # Collision auditing follows the same fixed candidate contract as the
        # evaluator: all shared rows are in scope for every case.
        shared = shared_rows
        exact: list[dict[str, Any]] = []
        unresolved_exact: list[dict[str, Any]] = []
        tag_overlaps: list[dict[str, Any]] = []
        for overlay in case["overlay"]:
            role = overlay["benchmark_metadata"].get("role")
            exact_ids = [row["memory_id"] for row in shared if row["text"] == overlay["text"]]
            tag_ids = [
                row["memory_id"]
                for row in shared
                if row["benchmark_metadata"]["semantic_tag"]
                == overlay["benchmark_metadata"]["semantic_tag"]
            ]
            if exact_ids:
                matching_shared = [row for row in shared if row["memory_id"] in exact_ids]
                equivalent = bool(
                    overlay.get("equivalent_memory_group_id")
                    and all(
                        row.get("equivalent_memory_group_id")
                        == overlay.get("equivalent_memory_group_id")
                        for row in matching_shared
                    )
                )
                collision = {
                    "overlay_memory_id": overlay["memory_id"],
                    "overlay_role": role,
                    "shared_memory_ids": exact_ids,
                    "equivalent_memory_group_id": overlay.get(
                        "equivalent_memory_group_id"
                    ),
                    "equivalent_documented": equivalent,
                }
                exact.append(
                    collision
                )
                if not equivalent:
                    unresolved_exact.append(collision)
            if tag_ids:
                tag_overlaps.append(
                    {
                        "overlay_memory_id": overlay["memory_id"],
                        "overlay_role": role,
                        "shared_memory_ids": tag_ids,
                    }
                )
        gold_exact = [item for item in exact if item["overlay_role"] == "gold"]
        unresolved_gold_exact = [
            item for item in unresolved_exact if item["overlay_role"] == "gold"
        ]
        gold_tags = [item for item in tag_overlaps if item["overlay_role"] == "gold"]
        exact_case_count += bool(exact)
        exact_gold_case_count += bool(gold_exact)
        equivalent_exact_case_count += bool(exact) and not unresolved_exact
        equivalent_gold_case_count += bool(gold_exact) and not unresolved_gold_exact
        unresolved_exact_case_count += bool(unresolved_exact)
        unresolved_gold_case_count += bool(unresolved_gold_exact)
        tag_case_count += bool(tag_overlaps)
        tag_gold_case_count += bool(gold_tags)
        if exact or tag_overlaps:
            details.append(
                {
                    "case_id": case["case_id"],
                    "exact_text_collisions": exact,
                    "unresolved_exact_text_collisions": unresolved_exact,
                    "semantic_tag_overlaps": tag_overlaps,
                    "gold_exact_text_collisions": gold_exact,
                    "equivalent_gold_exact_text_collisions": [
                        item for item in gold_exact if item.get("equivalent_documented")
                    ],
                    "unresolved_gold_exact_text_collisions": unresolved_gold_exact,
                    "gold_semantic_tag_overlaps": gold_tags,
                }
            )
    gold_collision_ids = [
        detail["case_id"]
        for detail in details
        if detail.get("unresolved_gold_exact_text_collisions")
    ]
    non_gold_collision_ids = [
        detail["case_id"] for detail in details if detail["case_id"] not in gold_collision_ids
    ]
    return {
        "status": "DATASET_REVIEW_REQUIRED" if gold_collision_ids else "PASS",
        "cases_with_any_exact_shared_overlay_duplicate": exact_case_count,
        "cases_with_exact_duplicate_gold_target": exact_gold_case_count,
        "cases_with_equivalent_exact_shared_overlay_duplicate": equivalent_exact_case_count,
        "cases_with_equivalent_exact_gold_target": equivalent_gold_case_count,
        "cases_with_unresolved_exact_shared_overlay_duplicate": unresolved_exact_case_count,
        "cases_with_unresolved_exact_gold_target": unresolved_gold_case_count,
        "cases_with_any_shared_overlay_tag_overlap": tag_case_count,
        "cases_with_gold_target_tag_overlap": tag_gold_case_count,
        "gold_collision_case_ids": gold_collision_ids,
        "non_gold_collision_case_ids": non_gold_collision_ids,
        "case_collisions": details,
        "unresolved_exact_collision_case_ids": [
            detail["case_id"]
            for detail in details
            if detail.get("unresolved_exact_text_collisions")
        ],
        "reason": (
            "Exact shared/overlay duplicates are accepted only when both rows "
            "declare the same equivalent_memory_group_id. Semantic-tag overlaps "
            "are diagnostic only and never make a Gold target ambiguous."
        ),
    }


def _collision_requires_review(collision: Mapping[str, Any] | None) -> bool:
    """Return whether a case has a Gold identity collision requiring review.

    Non-Gold shared/overlay overlaps remain useful diagnostics, but they do not
    make the case's Gold target contract ambiguous by themselves.  Keeping this
    predicate in one place prevents evaluator exceptions from accidentally
    marking every collision as a review case.
    """

    if not collision:
        return False
    return bool(collision.get("unresolved_gold_exact_text_collisions"))


async def _evaluate_memory_longtail_write_v2_once(
    case_path: Path,
    shared_bank_path: Path,
    *,
    embedding_provider: EmbeddingProvider,
    judge: SemanticRelationJudge,
    case_id: str | None = None,
    slice_name: str | None = None,
    vector_limit: int = 20,
    rank_limit: int = 5,
    validator: LongTailSemanticRelationValidator | None = None,
    fail_on_error: bool = False,
    case_ids: Sequence[str] | None = None,
    use_production_retriever: bool = False,
) -> dict[str, Any]:
    """Evaluate retrieval, relation, validation, and isolated Store behavior.

    Embeddings and Judge calls are injected so production adapters and
    deterministic test doubles use the same evaluator. The evaluator never
    opens a production Store and never owns or closes the injected providers.
    """

    if vector_limit < 1:
        raise ValueError("vector_limit must be positive")
    if rank_limit < 1 or rank_limit > vector_limit:
        raise ValueError("rank_limit must be between 1 and vector_limit")
    dataset = load_memory_longtail_write_v2_dataset(case_path, shared_bank_path)
    selected_case_ids = set(case_ids) if case_ids is not None else None
    cases = [
        case
        for case in dataset["cases"]
        if (case_id is None or case["case_id"] == case_id)
        and (slice_name is None or case["slice"] == slice_name)
        and (selected_case_ids is None or case["case_id"] in selected_case_ids)
    ]
    if not cases:
        raise ValueError(
            "no Long-tail Write V2 cases match filters: "
            f"case_id={case_id!r}, slice_name={slice_name!r}"
        )

    shared_by_pool: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in dataset["shared_memories"]:
        shared_by_pool[row["pool"]].append(row)
    records_by_id: dict[str, dict[str, Any]] = {}
    for case in cases:
        for row in _case_bank(case, shared_by_pool):
            records_by_id[row["memory_id"]] = row

    embedding_telemetry: dict[str, Any] = {
        "model": str(getattr(embedding_provider, "model_name", "unknown")),
        "model_version": str(
            getattr(
                embedding_provider,
                "model_version",
                getattr(embedding_provider, "version", "unknown"),
            )
        ),
        "generated_at": datetime.now(UTC).isoformat(),
        "document_call_count": 0,
        "document_text_count": 0,
        "document_latencies_ms": [],
        "query_call_count": 0,
        "failure_count": 0,
        "document_failure_count": 0,
        "query_failure_count": 0,
        "document_latency_ms": 0.0,
        "query_latencies_ms": [],
        "dimension": None,
        "text_only": True,
        "vectors_persisted": False,
    }
    document_vectors: dict[str, list[float]] = {}
    document_error: str | None = None
    production_retriever: HybridMemoryRetriever | None = None
    if use_production_retriever:
        observed_provider = _ObservedEmbeddingProvider(embedding_provider, embedding_telemetry)
        production_retriever = HybridMemoryRetriever(
            embedding_provider=observed_provider,
            # The evaluator needs the complete case-local bank before deriving
            # vector Top-K and the production cheap-rank Top-N.  A large budget
            # prevents token budgeting from silently truncating the bank.
            token_budget=10_000_000,
        )
        try:
            embedding_telemetry["dimension"] = await embedding_provider.dimension()
        except Exception as exc:
            # ``HybridMemoryRetriever`` itself does not require dimension();
            # retain a diagnostic value and let its actual calls determine
            # whether this case can be evaluated.
            embedding_telemetry["dimension_error"] = f"{type(exc).__name__}: {exc}"
            if fail_on_error:
                raise
    else:
        try:
            ids = list(records_by_id)
            started = perf_counter()
            embedding_telemetry["document_call_count"] += 1
            embedding_telemetry["document_text_count"] += len(ids)
            raw_vectors = await embedding_provider.embed_documents(
                [records_by_id[memory_id]["text"] for memory_id in ids]
            )
            embedding_telemetry["document_latency_ms"] = round(
                (perf_counter() - started) * 1000,
                3,
            )
            embedding_telemetry["document_latencies_ms"].append(
                embedding_telemetry["document_latency_ms"]
            )
            vectors = _validated_vectors(raw_vectors, expected_count=len(ids))
            document_vectors = dict(zip(ids, vectors, strict=True))
            embedding_telemetry["dimension"] = len(vectors[0]) if vectors else 0
        except Exception as exc:
            embedding_telemetry["failure_count"] += 1
            embedding_telemetry["document_failure_count"] += 1
            document_error = f"{type(exc).__name__}: {exc}"
            if fail_on_error:
                raise

    validator = validator or LongTailSemanticRelationValidator()
    collision_by_case = {
        item["case_id"]: item for item in dataset["collision_audit"]["case_collisions"]
    }
    rows: list[dict[str, Any]] = []
    for case in cases:
        try:
            if document_error is not None:
                raise RuntimeError(f"document embedding failed: {document_error}")
            rows.append(
                await _evaluate_v2_case(
                    case,
                    shared_by_pool=shared_by_pool,
                    document_vectors=document_vectors,
                    embedding_provider=embedding_provider,
                    embedding_telemetry=embedding_telemetry,
                    judge=judge,
                    validator=validator,
                    vector_limit=vector_limit,
                    rank_limit=rank_limit,
                    collision=collision_by_case.get(case["case_id"]),
                    production_retriever=production_retriever,
                )
            )
        except Exception as exc:
            if fail_on_error:
                raise
            rows.append(_error_row(case, exc, collision_by_case.get(case["case_id"])))

    report = _build_report(
        rows,
        dataset=dataset,
        case_path=case_path,
        shared_bank_path=shared_bank_path,
        case_id=case_id,
        slice_name=slice_name,
        vector_limit=vector_limit,
        rank_limit=rank_limit,
        embedding_telemetry=embedding_telemetry,
    )
    return report


async def evaluate_memory_longtail_write_v2(
    case_path: Path,
    shared_bank_path: Path,
    *,
    embedding_provider: EmbeddingProvider,
    judge: SemanticRelationJudge,
    case_id: str | None = None,
    slice_name: str | None = None,
    vector_limit: int = 20,
    rank_limit: int = 5,
    validator: LongTailSemanticRelationValidator | None = None,
    fail_on_error: bool = False,
    repeat: int = 1,
    hard_cases: bool = False,
    use_production_retriever: bool = False,
) -> dict[str, Any]:
    """Run the V2 evaluator once or repeatedly for judge-drift analysis.

    ``repeat`` is intentionally an evaluator concern: every run gets a fresh
    case-local bank/store while sharing no mutable production state.  The
    default remains exactly the original one-run behavior.  ``hard_cases``
    uses the fixed identifiers from the live-evaluation brief; because the V2
    draft has a different ID namespace, an empty match is returned as a
    diagnostic report rather than being silently widened or raised as an
    infrastructure error.
    """

    if repeat < 1 or repeat > 100:
        raise ValueError("repeat must be between 1 and 100")

    dataset = load_memory_longtail_write_v2_dataset(case_path, shared_bank_path)
    available_ids = [str(case["case_id"]) for case in dataset["cases"]]
    requested_filter_ids = [
        case_id_value
        for case_id_value in available_ids
        if (case_id is None or case_id_value == case_id)
    ]
    if slice_name is not None:
        requested_filter_ids = [
            case_id_value
            for case_id_value in requested_filter_ids
            if next(case["slice"] for case in dataset["cases"] if case["case_id"] == case_id_value)
            == slice_name
        ]

    hard_filter = {
        "requested_ids": list(HARD_CASE_IDS) if hard_cases else [],
        "available_ids": available_ids,
        "matched_ids": [],
        "missing_ids": [],
        "status": "NOT_REQUESTED",
    }
    if hard_cases:
        matched = [
            case_id_value
            for case_id_value in HARD_CASE_IDS
            if case_id_value in requested_filter_ids
        ]
        missing = [case_id_value for case_id_value in HARD_CASE_IDS if case_id_value not in matched]
        hard_filter.update(
            {
                "matched_ids": matched,
                "missing_ids": missing,
                "status": (
                    "MATCHED"
                    if matched and not missing
                    else ("PARTIAL_MATCH" if matched else "NO_MATCH")
                ),
            }
        )
        selected_ids = matched
    else:
        selected_ids = requested_filter_ids

    if not selected_ids:
        if not hard_cases:
            raise ValueError(
                "no Long-tail Write V2 cases match filters: "
                f"case_id={case_id!r}, slice_name={slice_name!r}"
            )
        report = _build_report(
            [],
            dataset=dataset,
            case_path=case_path,
            shared_bank_path=shared_bank_path,
            case_id=case_id,
            slice_name=slice_name,
            vector_limit=vector_limit,
            rank_limit=rank_limit,
            embedding_telemetry=_empty_embedding_telemetry(embedding_provider),
        )
        report.update(
            {
                "case_count": 0,
                "evaluated_row_count": 0,
                "passed_case_count": 0,
                "failed_case_count": 0,
                "repeat": 0,
                "run_count": 0,
                "runs": [],
                "hard_cases_only": True,
                "hard_case_filter": hard_filter,
                "hard_case_consistency": {},
                "status": "HARD_CASE_FILTER_EMPTY",
            }
        )
        return report

    run_reports: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    first_report: dict[str, Any] | None = None
    embedding_reports: list[dict[str, Any]] = []
    for run_index in range(1, repeat + 1):
        run_report = await _evaluate_memory_longtail_write_v2_once(
            case_path,
            shared_bank_path,
            embedding_provider=embedding_provider,
            judge=judge,
            case_id=None,
            slice_name=None,
            vector_limit=vector_limit,
            rank_limit=rank_limit,
            validator=validator,
            fail_on_error=fail_on_error,
            case_ids=selected_ids,
            use_production_retriever=use_production_retriever,
        )
        if first_report is None:
            first_report = run_report
        embedding_reports.append(run_report.get("telemetry", {}).get("embedding", {}))
        run_rows = []
        for row in run_report.get("rows", []):
            annotated = dict(row)
            annotated["run_index"] = run_index
            run_rows.append(annotated)
        all_rows.extend(run_rows)
        run_reports.append(
            {
                "run": run_index,
                "case_count": len(run_rows),
                "passed_case_count": sum(bool(row.get("passed")) for row in run_rows),
                "failed_case_count": sum(not bool(row.get("passed")) for row in run_rows),
                "retrieved_relation_metrics": run_report.get("retrieved_relation_metrics", {}),
                "write_metrics": run_report.get("write_metrics", {}),
                "cases": run_rows,
            }
        )

    # A one-run call should preserve the original report shape and metrics.
    if repeat == 1 and first_report is not None:
        first_report["repeat"] = 1
        first_report["run_count"] = 1
        first_report["runs"] = run_reports
        first_report["hard_cases_only"] = hard_cases
        first_report["hard_case_filter"] = hard_filter
        first_report["hard_case_consistency"] = {}
        first_report["filters"] = {"case_id": case_id, "slice": slice_name}
        return first_report

    merged_embedding = _merge_embedding_telemetry(embedding_reports)
    report = _build_report(
        all_rows,
        dataset=dataset,
        case_path=case_path,
        shared_bank_path=shared_bank_path,
        case_id=case_id,
        slice_name=slice_name,
        vector_limit=vector_limit,
        rank_limit=rank_limit,
        embedding_telemetry=merged_embedding,
    )
    report.update(
        {
            "case_count": len(selected_ids),
            "evaluated_row_count": len(all_rows),
            "repeat": repeat,
            "run_count": repeat,
            "passed_evaluation_count": sum(bool(row.get("passed")) for row in all_rows),
            "failed_evaluation_count": sum(not bool(row.get("passed")) for row in all_rows),
            "runs": run_reports,
            "hard_cases_only": hard_cases,
            "hard_case_filter": hard_filter,
            "hard_case_consistency": _summarize_v2_consistency(all_rows),
            "filters": {"case_id": case_id, "slice": slice_name},
        }
    )
    rows_by_case: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in all_rows:
        rows_by_case[str(row.get("case_id"))].append(row)
    report["passed_case_count"] = sum(
        bool(case_rows) and all(bool(row.get("passed")) for row in case_rows)
        for case_rows in rows_by_case.values()
    )
    report["failed_case_count"] = len(rows_by_case) - report["passed_case_count"]
    return report


def _empty_embedding_telemetry(provider: EmbeddingProvider) -> dict[str, Any]:
    return {
        "model": str(getattr(provider, "model_name", "unknown")),
        "model_version": str(
            getattr(provider, "model_version", getattr(provider, "version", "unknown"))
        ),
        "generated_at": datetime.now(UTC).isoformat(),
        "document_call_count": 0,
        "document_text_count": 0,
        "document_latencies_ms": [],
        "query_call_count": 0,
        "failure_count": 0,
        "document_failure_count": 0,
        "query_failure_count": 0,
        "document_latency_ms": 0.0,
        "query_latencies_ms": [],
        "dimension": None,
        "text_only": True,
        "vectors_persisted": False,
    }


def _merge_embedding_telemetry(reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Merge per-run embedding summaries without exposing provider internals."""

    if not reports:
        return {
            "model": "unknown",
            "model_version": "unknown",
            "generated_at": datetime.now(UTC).isoformat(),
            "document_call_count": 0,
            "document_text_count": 0,
            "document_latencies_ms": [],
            "query_call_count": 0,
            "failure_count": 0,
            "document_failure_count": 0,
            "query_failure_count": 0,
            "document_latency_ms": 0.0,
            "query_latencies_ms": [],
            "dimension": None,
            "text_only": True,
            "vectors_persisted": False,
        }
    first = dict(reports[0])
    first["document_call_count"] = sum(
        int(item.get("document_call_count") or 0) for item in reports
    )
    first["document_text_count"] = sum(
        int(item.get("document_text_count") or 0) for item in reports
    )
    first["query_call_count"] = sum(int(item.get("query_call_count") or 0) for item in reports)
    first["failure_count"] = sum(int(item.get("failure_count") or 0) for item in reports)
    first["document_failure_count"] = sum(
        int(item.get("document_failure_count") or 0) for item in reports
    )
    first["query_failure_count"] = sum(
        int(item.get("query_failure_count") or 0) for item in reports
    )
    first["document_latency_ms"] = round(
        sum(float(item.get("document_latency_ms") or 0.0) for item in reports),
        3,
    )
    first["document_latencies_ms"] = [
        float(value) for item in reports for value in item.get("document_latencies_ms", [])
    ]
    first["query_latencies_ms"] = [
        float(value)
        for item in reports
        for value in item.get("query_latencies_ms", [])
    ]
    return first


def _summarize_v2_consistency(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("case_id"))].append(row)

    def mode_rate(values: list[Any]) -> float:
        if not values:
            return 0.0
        encoded = [json.dumps(value, ensure_ascii=False, sort_keys=True) for value in values]
        return _ratio(max(Counter(encoded).values()), len(encoded))

    by_case: dict[str, Any] = {}
    relation_rates: list[float] = []
    target_rates: list[float] = []
    validator_rates: list[float] = []
    retrieval_order_rates: list[float] = []
    for case_key, case_rows in sorted(grouped.items()):
        relation_runs = [_v2_relation_signature(row) for row in case_rows]
        target_runs = [_v2_target_signature(row) for row in case_rows]
        validator_runs = [_v2_validator_signature(row) for row in case_rows]
        retrieval_order_runs = [_v2_retrieval_order_signature(row) for row in case_rows]
        relation_rate = mode_rate(relation_runs)
        target_rate = mode_rate(target_runs)
        validator_rate = mode_rate(validator_runs)
        retrieval_order_rate = mode_rate(retrieval_order_runs)
        relation_rates.append(relation_rate)
        target_rates.append(target_rate)
        validator_rates.append(validator_rate)
        retrieval_order_rates.append(retrieval_order_rate)
        by_case[case_key] = {
            "run_count": len(case_rows),
            "relation_consistency_rate": relation_rate,
            "target_consistency_rate": target_rate,
            "validator_consistency_rate": validator_rate,
            "retrieval_top5_order_consistency_rate": retrieval_order_rate,
            "relation_runs": relation_runs,
            "target_runs": target_runs,
            "validator_runs": validator_runs,
            "retrieval_top5_order_runs": retrieval_order_runs,
            "target_drift_attribution": (
                "TOP5_CANDIDATE_ORDER_DRIFT"
                if target_rate < 1.0 and retrieval_order_rate < 1.0
                else (
                    "JUDGE_TARGET_SELECTION_DRIFT"
                    if target_rate < 1.0
                    else "NO_TARGET_DRIFT"
                )
            ),
        }
    return {
        "case_count": len(by_case),
        "evaluated_row_count": len(rows),
        "relation_consistency_rate": round(mean(relation_rates), 4) if relation_rates else 0.0,
        "target_consistency_rate": round(mean(target_rates), 4) if target_rates else 0.0,
        "validator_consistency_rate": round(mean(validator_rates), 4) if validator_rates else 0.0,
        "retrieval_top5_order_consistency_rate": (
            round(mean(retrieval_order_rates), 4) if retrieval_order_rates else 0.0
        ),
        "by_case": by_case,
    }


def _v2_relation_signature(row: Mapping[str, Any]) -> Any:
    stage = row.get("retrieved_relation")
    if not isinstance(stage, Mapping):
        return None
    proposal = stage.get("proposal")
    if not isinstance(proposal, Mapping):
        return None
    return proposal.get("relation")


def _v2_target_signature(row: Mapping[str, Any]) -> list[str]:
    stage = row.get("retrieved_relation")
    proposal = stage.get("proposal") if isinstance(stage, Mapping) else None
    targets = proposal.get("target_memory_ids", []) if isinstance(proposal, Mapping) else []
    return sorted(str(target) for target in targets)


def _v2_validator_signature(row: Mapping[str, Any]) -> dict[str, Any]:
    stage = row.get("retrieved_relation")
    validation = stage.get("validation") if isinstance(stage, Mapping) else None
    if not isinstance(validation, Mapping):
        return {"validator_pass": None, "validated_relation": None, "would_update": None}
    return {
        "validator_pass": validation.get("validator_pass"),
        "validated_relation": validation.get("validated_relation"),
        "would_update": validation.get("would_update"),
    }


def _v2_retrieval_order_signature(row: Mapping[str, Any]) -> list[str]:
    retrieval = row.get("retrieval")
    ranked = retrieval.get("ranked", []) if isinstance(retrieval, Mapping) else []
    return [str(item.get("memory_id")) for item in ranked if isinstance(item, Mapping)]


async def _evaluate_v2_case(
    case: dict[str, Any],
    *,
    shared_by_pool: Mapping[str, list[dict[str, Any]]],
    document_vectors: Mapping[str, list[float]],
    embedding_provider: EmbeddingProvider,
    embedding_telemetry: dict[str, Any],
    judge: SemanticRelationJudge,
    validator: LongTailSemanticRelationValidator,
    vector_limit: int,
    rank_limit: int,
    collision: dict[str, Any] | None,
    production_retriever: HybridMemoryRetriever | None = None,
) -> dict[str, Any]:
    case_id = case["case_id"]
    user_id = f"longtail-write-v2-{case_id.casefold()}-user"
    relationship_id = f"longtail-write-v2-{case_id.casefold()}-relationship"
    bank_records = _case_bank(case, shared_by_pool)
    bank_items = [
        _memory_item_from_row(
            row,
            user_id=user_id,
            relationship_id=relationship_id,
            reference_time=REFERENCE_TIME - timedelta(days=30),
        )
        for row in bank_records
    ]
    item_by_id = {item.id: item for item in bank_items}
    incoming = _candidate_from_row(
        case["incoming"],
        reference_time=REFERENCE_TIME,
    )
    incoming_status = MemoryStatus(case["incoming"].get("status", "confirmed"))

    retrieval_started = perf_counter()
    embedding_failures_before = int(embedding_telemetry.get("failure_count") or 0)
    if production_retriever is not None:
        retrieval = await _retrieve_with_production_hybrid(
            incoming=incoming,
            bank_items=bank_items,
            retriever=production_retriever,
            user_id=user_id,
            relationship_id=relationship_id,
            vector_limit=vector_limit,
            rank_limit=rank_limit,
        )
    else:
        query_started = perf_counter()
        embedding_telemetry["query_call_count"] += 1
        try:
            query_vector = _validated_query_vector(
                await embedding_provider.embed_query(case["incoming"]["text"]),
                expected_dimension=embedding_telemetry["dimension"],
            )
        except Exception:
            embedding_telemetry["failure_count"] += 1
            embedding_telemetry["query_failure_count"] += 1
            raise
        finally:
            embedding_telemetry["query_latencies_ms"].append(
                round((perf_counter() - query_started) * 1000, 3)
            )
        retrieval = _retrieve_and_rank(
            incoming=incoming,
            bank_items=bank_items,
            query_vector=query_vector,
            document_vectors=document_vectors,
            user_id=user_id,
            relationship_id=relationship_id,
            vector_limit=vector_limit,
            rank_limit=rank_limit,
        )
    embedding_failures_after = int(embedding_telemetry.get("failure_count") or 0)
    retrieval["embedding_failure_count"] = max(
        embedding_failures_after - embedding_failures_before,
        0,
    )
    retrieval["embedding_fallback_used"] = bool(
        production_retriever is not None and retrieval["embedding_failure_count"]
    )
    retrieval["candidate_inventory"] = [
        {
            "memory_id": str(row["memory_id"]),
            "kind": str(row["kind"]),
            "subject": str(row["subject"]),
            "status": str(row.get("status", MemoryStatus.CONFIRMED.value)),
            "perspective": str(row.get("perspective", MemoryPerspective.USER_REPORTED.value)),
            "predicate_type": str(row.get("predicate_type", PredicateType.CUSTOM.value)),
            "raw_predicate": row.get("raw_predicate"),
            "canonical_predicate": row.get("canonical_predicate"),
            "custom_predicate": row.get("custom_predicate"),
            "state_dimension": row.get("state_dimension"),
            "state_value": row.get("state_value"),
            "time_kind": str(row.get("time_kind", TimeKind.UNKNOWN.value)),
            "occurred_at": row.get("occurred_at"),
            "period_start": row.get("period_start"),
            "period_end": row.get("period_end"),
            "equivalent_memory_group_id": row.get("equivalent_memory_group_id"),
            "source": str(row["benchmark_metadata"].get("source", "unknown")),
            "fixture_role": str(row["benchmark_metadata"].get("role", "background")),
            "text": str(row["text"]),
        }
        for row in bank_records
    ]
    _annotate_retrieval_roles(
        retrieval,
        case=case,
        collision=collision,
        records=bank_records,
    )
    retrieval_latency_ms = round((perf_counter() - retrieval_started) * 1000, 3)
    ranked_items = [item_by_id[item["memory_id"]] for item in retrieval["ranked"]]
    oracle_ids = {
        row["memory_id"]
        for row in case["overlay"]
        if row["benchmark_metadata"].get("role") in {"gold", "hard_negative"}
    }
    oracle_items = [item_by_id[memory_id] for memory_id in oracle_ids]
    oracle_items.sort(key=lambda item: _oracle_sort_key(item.id, case))

    oracle = await _run_relation_stage(
        judge=judge,
        validator=validator,
        incoming=incoming,
        candidates=oracle_items,
        user_id=user_id,
        relationship_id=relationship_id,
        incoming_status=incoming_status,
        source_message_id=f"{case_id}-incoming",
    )
    retrieved = await _run_relation_stage(
        judge=judge,
        validator=validator,
        incoming=incoming,
        candidates=ranked_items,
        user_id=user_id,
        relationship_id=relationship_id,
        incoming_status=incoming_status,
        source_message_id=f"{case_id}-incoming",
    )
    store_result = await _run_store_integration(
        case=case,
        bank_records=bank_records,
        incoming=incoming,
        incoming_status=incoming_status,
        relation_stage=retrieved,
        user_id=user_id,
        relationship_id=relationship_id,
        collision=collision,
    )

    # Keep retrieval evidence and semantic write targets as separate contracts.
    # In particular, LTW2-040 intentionally supplies related Gold references for
    # an UNCERTAIN relation; those references must not become mutation targets.
    expected_retrieval_candidates = _expected_retrieval_candidate_ids(case)
    expected_semantic_targets = _expected_semantic_target_ids(case)
    expected_retrieval_set = set(expected_retrieval_candidates)
    vector_ids = [item["memory_id"] for item in retrieval["vector"]]
    ranked_ids = [item["memory_id"] for item in retrieval["ranked"]]
    retrieval_checks = {
        "gold_in_top_20": expected_retrieval_set <= set(vector_ids),
        "gold_in_top_10": expected_retrieval_set <= set(vector_ids[:10]),
        "gold_in_top_5": expected_retrieval_set <= set(vector_ids[:5]),
        "gold_retained_after_ranking": expected_retrieval_set <= set(ranked_ids),
        "equivalent_gold_in_top_20": _equivalence_expected_keys(
            expected_retrieval_set,
            retrieval,
        )
        <= _equivalence_candidate_keys(retrieval.get("vector", [])[:20]),
        "equivalent_gold_retained_after_ranking": _equivalence_expected_keys(
            expected_retrieval_set,
            retrieval,
        )
        <= _equivalence_candidate_keys(retrieval.get("ranked", [])),
    }
    if not expected_retrieval_candidates:
        retrieval_checks = {name: True for name in retrieval_checks}
    oracle_checks = _relation_checks(case, oracle)
    retrieved_checks = _relation_checks(case, retrieved)
    safety = _safety_checks(
        case=case,
        incoming=incoming,
        bank_items=bank_items,
        relation_stage=retrieved,
        store_result=store_result,
        collision=collision,
    )
    failure = _attribute_failure(
        case=case,
        collision=collision,
        retrieval_checks=retrieval_checks,
        retrieved_checks=retrieved_checks,
        relation_stage=retrieved,
        store_result=store_result,
        retrieval=retrieval,
    )
    hard_negative_ids = {
        row["memory_id"]
        for row in case["overlay"]
        if row["benchmark_metadata"].get("role") == "hard_negative"
    }
    hard_negative_promoted = _hard_negative_promoted(
        retrieval,
        expected_targets=expected_retrieval_set,
        hard_negative_ids=hard_negative_ids,
    )
    passed = (
        retrieval_checks["gold_in_top_20"]
        and retrieval_checks["gold_retained_after_ranking"]
        and retrieved_checks["judge_completed"]
        and retrieved_checks["relation"]
        and retrieved_checks["target_set"]
        and all(store_result["checks"].values())
        and not any(safety.values())
    )
    return {
        "case_id": case_id,
        "slice": case["slice"],
        "contract_status": case.get("contract_status"),
        "incoming_text": case["incoming"]["text"],
        "incoming_kind": case["incoming"]["kind"],
        "incoming_subject": case["incoming"]["subject"],
        "incoming_status": incoming_status.value,
        "expected_relation": case["expected_relation"],
        # ``expected_target_ids`` remains the frozen dataset field for
        # compatibility.  The two explicit derived contracts below prevent
        # retrieval/reference IDs from being mistaken for semantic targets.
        "expected_target_ids": expected_retrieval_candidates,
        "expected_retrieval_candidate_ids": expected_retrieval_candidates,
        "expected_semantic_target_ids": expected_semantic_targets,
        "target_contract": (
            "retrieval_reference_only"
            if expected_retrieval_candidates and not expected_semantic_targets
            else "semantic_targets"
        ),
        "candidate_pool_size": len(bank_items),
        "retrieval": {
            **retrieval,
            "latency_ms": retrieval_latency_ms,
            "checks": retrieval_checks,
            "hard_negative_promoted": hard_negative_promoted,
        },
        "oracle_relation": oracle,
        "oracle_checks": oracle_checks,
        "retrieved_relation": retrieved,
        "retrieved_checks": retrieved_checks,
        "store": store_result,
        "safety": safety,
        "dataset_collision": collision,
        "dataset_review_required": _collision_requires_review(collision),
        "primary_failure_stage": failure["primary"],
        "secondary_failure_stages": failure["secondary"],
        "passed": passed,
    }


def _case_bank(
    case: Mapping[str, Any],
    shared_by_pool: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    # Do not dynamically crop the shared bank by semantic pool.  The V2
    # benchmark contract is exactly 120 shared memories plus five overlays.
    shared = [row for rows in shared_by_pool.values() for row in rows]
    return [*shared, *case["overlay"]]


def _retrieve_and_rank(
    *,
    incoming: MemoryCandidate,
    bank_items: list[MemoryItem],
    query_vector: Sequence[float],
    document_vectors: Mapping[str, list[float]],
    user_id: str,
    relationship_id: str,
    vector_limit: int,
    rank_limit: int,
) -> dict[str, Any]:
    filtered_out: list[dict[str, str]] = []
    scored: list[tuple[MemoryItem, float]] = []
    for item in bank_items:
        if item.user_id != user_id or item.relationship_id != relationship_id:
            filtered_out.append({"memory_id": item.id, "reason": "relationship_scope_mismatch"})
            continue
        if item.status not in ACTIVE_STATUSES:
            filtered_out.append(
                {"memory_id": item.id, "reason": f"inactive_status:{item.status.value}"}
            )
            continue
        vector = document_vectors.get(item.id)
        if vector is None:
            filtered_out.append({"memory_id": item.id, "reason": "missing_embedding"})
            continue
        scored.append((item, _cosine(query_vector, vector)))
    scored.sort(key=lambda pair: (-pair[1], pair[0].id))
    vector_top = scored[:vector_limit]
    vector_rows = [
        {
            "memory_id": item.id,
            "rank": index,
            "vector_rank": index,
            "kind": item.kind.value,
            "subject": item.subject,
            "status": item.status.value,
            "text": item.original_text,
            "semantic_similarity": round(similarity, 6),
        }
        for index, (item, similarity) in enumerate(vector_top, start=1)
    ]
    cheap_ranking_started = perf_counter()
    ranked_rows = []
    for vector_rank, (item, similarity) in enumerate(vector_top, start=1):
        row = _cheap_rank_row(incoming, item, similarity)
        row["vector_rank"] = vector_rank
        row["rank_before"] = vector_rank
        ranked_rows.append(row)
    ranked_rows.sort(
        key=lambda row: (
            -row["score"]["cheap_score"],
            -row["score"]["semantic_similarity"],
            row["memory_id"],
        )
    )
    ranked_rows = ranked_rows[:rank_limit]
    for index, row in enumerate(ranked_rows, start=1):
        row["rank"] = index
        row["rank_after"] = index
    return {
        "vector": vector_rows,
        "ranked": ranked_rows,
        "filtered_out": filtered_out,
        "eligible_candidate_count": len(scored),
        "vector_limit": vector_limit,
        "rank_limit": rank_limit,
        "cheap_ranking_latency_ms": round(
            (perf_counter() - cheap_ranking_started) * 1000,
            3,
        ),
    }


async def _retrieve_with_production_hybrid(
    *,
    incoming: MemoryCandidate,
    bank_items: list[MemoryItem],
    retriever: HybridMemoryRetriever,
    user_id: str,
    relationship_id: str,
    vector_limit: int,
    rank_limit: int,
) -> dict[str, Any]:
    """Run the production retriever, then expose its two benchmark stages.

    ``HybridMemoryRetriever`` returns candidates ordered by its production
    hybrid score.  Asking for the complete case-local bank with
    ``preserve_candidates=True`` lets the evaluator derive a genuine vector
    Top-K from each result's semantic score and then apply the production
    hybrid/cheap score to that Top-K.  No benchmark role, tag, or Gold field is
    passed to the retriever.
    """

    query_parts = [incoming.summary, *incoming.evidence_spans]
    query = " ".join(dict.fromkeys(part.strip() for part in query_parts if part.strip()))
    retrieved = await retriever.retrieve(
        bank_items,
        query=query,
        # Retrieve the whole isolated bank so vector recall is measured before
        # the benchmark's Top-K/Top-N cuts.
        limit=max(len(bank_items), vector_limit),
        reference_time=REFERENCE_TIME,
        mode=MemoryRetrievalMode.CURRENT,
        preserve_candidates=True,
        require_relevance=False,
        token_budget=10_000_000,
    )
    by_id = {result.item.id: result for result in retrieved}
    vector_ranking_started = perf_counter()
    vector_results = sorted(
        retrieved,
        key=lambda result: (-result.score.semantic_similarity, result.item.id),
    )
    vector_results = vector_results[:vector_limit]
    vector_ranking_latency_ms = round(
        (perf_counter() - vector_ranking_started) * 1000,
        3,
    )
    cheap_ranking_started = perf_counter()
    ranked_results = sorted(
        vector_results,
        key=lambda result: (
            -result.score.total,
            -result.score.semantic_similarity,
            result.item.id,
        ),
    )[:rank_limit]
    cheap_ranking_latency_ms = round(
        (perf_counter() - cheap_ranking_started) * 1000,
        3,
    )

    def row(result: RetrievedMemory, *, rank: int, vector_rank: int) -> dict[str, Any]:
        score = result.score.as_dict()
        # Keep the evaluator's explainable field name while preserving every
        # production score component in the same object.
        score["cheap_score"] = score["total"]
        return {
            "memory_id": result.item.id,
            "rank": rank,
            "vector_rank": vector_rank,
            "rank_before": vector_rank,
            "rank_after": rank,
            "kind": result.item.kind.value,
            "subject": result.item.subject,
            "status": result.item.status.value,
            "text": result.item.original_text,
            "score": score,
            "semantic_similarity": round(result.score.semantic_similarity, 6),
        }

    vector_rows = [
        row(result, rank=index, vector_rank=index)
        for index, result in enumerate(vector_results, start=1)
    ]
    ranked_rows = []
    vector_rank_by_id = {result.item.id: index for index, result in enumerate(vector_results, 1)}
    for index, result in enumerate(ranked_results, start=1):
        ranked_rows.append(
            row(
                result,
                rank=index,
                vector_rank=vector_rank_by_id[result.item.id],
            )
        )
    returned_ids = set(by_id)
    filtered_out = [
        {
            "memory_id": item.id,
            "reason": "production_retriever_filtered",
        }
        for item in bank_items
        if item.id not in returned_ids
    ]
    return {
        "vector": vector_rows,
        "ranked": ranked_rows,
        "filtered_out": filtered_out,
        "eligible_candidate_count": len(retrieved),
        "vector_limit": vector_limit,
        "rank_limit": rank_limit,
        "vector_ranking_latency_ms": vector_ranking_latency_ms,
        "cheap_ranking_latency_ms": cheap_ranking_latency_ms,
        "retrieval_engine": "HybridMemoryRetriever",
        "vector_stage": "production_semantic_similarity_sorted",
        "cheap_rank_stage": "production_hybrid_total_sorted",
        "scope": {
            "user_id": user_id,
            "relationship_id": relationship_id,
            "require_relevance": False,
            "preserve_candidates": True,
        },
    }


def _annotate_retrieval_roles(
    retrieval: dict[str, Any],
    *,
    case: Mapping[str, Any],
    collision: Mapping[str, Any] | None,
    records: Sequence[Mapping[str, Any]],
) -> None:
    retrieval_references = set(_expected_retrieval_candidate_ids(case))
    semantic_targets = set(_expected_semantic_target_ids(case))
    allowed = set(case.get("allowed_related_target_ids", []))
    hard_negative = {
        row["memory_id"]
        for row in case["overlay"]
        if row["benchmark_metadata"].get("role") == "hard_negative"
    }
    # Exact aliases are useful for explainability/role annotation.  Semantic
    # tag overlaps are intentionally diagnostic only and must not alter the
    # candidate's benchmark role or target scoring.
    collision_ids: set[str] = set()
    equivalent_alias_ids: set[str] = set()
    if collision:
        for name in ("exact_text_collisions",):
            for detail in collision.get(name, []):
                collision_ids.update(detail.get("shared_memory_ids", []))
                if detail.get("equivalent_documented"):
                    equivalent_alias_ids.update(detail.get("shared_memory_ids", []))
    records_by_id = {str(row["memory_id"]): row for row in records}
    for result in (
        *retrieval["candidate_inventory"],
        *retrieval["vector"],
        *retrieval["ranked"],
    ):
        memory_id = result["memory_id"]
        record = records_by_id[memory_id]
        metadata = record["benchmark_metadata"]
        result["equivalent_memory_group_id"] = record.get(
            "equivalent_memory_group_id"
        )
        result.setdefault("source", str(metadata.get("source", "unknown")))
        result.setdefault("fixture_role", str(metadata.get("role", "background")))
        if memory_id in semantic_targets:
            role = "GOLD_TARGET"
        elif memory_id in retrieval_references:
            role = "GOLD_RETRIEVAL_REFERENCE"
        elif memory_id in allowed:
            role = "ALLOWED_RELATED"
        elif memory_id in equivalent_alias_ids:
            role = "EQUIVALENT_ALIAS"
        elif memory_id in collision_ids:
            role = "POTENTIAL_COLLISION"
        elif memory_id in hard_negative:
            role = "HARD_NEGATIVE"
        else:
            role = "BACKGROUND"
        result["benchmark_role"] = role


def _cheap_rank_row(
    incoming: MemoryCandidate,
    item: MemoryItem,
    semantic_similarity: float,
) -> dict[str, Any]:
    subject = _subject_compatibility(incoming.subject, item.subject)
    kind = _kind_compatibility(incoming.kind, item.kind)
    temporal = _temporal_compatibility(incoming.kind, item.kind)
    status = 1.0 if item.status == MemoryStatus.CONFIRMED else 0.5
    score = (
        semantic_similarity * 0.72 + subject * 0.12 + kind * 0.08 + temporal * 0.04 + status * 0.04
    )
    return {
        "memory_id": item.id,
        "rank": 0,
        "kind": item.kind.value,
        "subject": item.subject,
        "status": item.status.value,
        "text": item.original_text,
        "score": {
            "semantic_similarity": round(semantic_similarity, 6),
            "subject_compatibility": round(subject, 4),
            "kind_compatibility": round(kind, 4),
            "temporal_compatibility": round(temporal, 4),
            "lifecycle_status_prior": round(status, 4),
            "cheap_score": round(score, 6),
        },
    }


async def _run_relation_stage(
    *,
    judge: SemanticRelationJudge,
    validator: LongTailSemanticRelationValidator,
    incoming: MemoryCandidate,
    candidates: list[MemoryItem],
    user_id: str,
    relationship_id: str,
    incoming_status: MemoryStatus,
    source_message_id: str,
) -> dict[str, Any]:
    started = perf_counter()
    judge_trace = ExecutionTrace()
    if not candidates:
        proposal = _fail_closed_proposal("No eligible candidates were supplied.")
        judge_status = "not_called"
        error_type = None
        error_category = None
    else:
        try:
            proposal = await judge.propose_relation(
                incoming=incoming,
                candidates=candidates,
                trace=judge_trace,
            )
            proposal = SemanticRelationProposal.model_validate(proposal)
            judge_status = "completed"
            error_type = None
            error_category = None
        except Exception as exc:
            proposal = _fail_closed_proposal("Semantic relation judge failed closed.")
            judge_status = "failed"
            error_type = type(exc).__name__
            error_category = _judge_failure_category(exc)
    observed_latency = round((perf_counter() - started) * 1000, 3)
    validation = validator.validate(
        proposal,
        incoming=incoming,
        retrieved=candidates,
        user_id=user_id,
        relationship_id=relationship_id,
        incoming_status=incoming_status,
        incoming_source_message_id=source_message_id,
        reference_time=REFERENCE_TIME,
    )
    trace_records = _safe_judge_trace_records(judge_trace)
    policy_records = [
        record
        for record in trace_records
        if record.get("name") == "memory_semantic_relation_model"
    ]
    policy_details = policy_records[-1].get("details", {}) if policy_records else {}
    return {
        "candidate_ids": [item.id for item in candidates],
        "judge_status": judge_status,
        "judge_error_type": error_type,
        "judge_error_category": error_category,
        "observed_latency_ms": observed_latency,
        "judge_trace": trace_records,
        "target_policy_status": policy_details.get("target_policy_status"),
        "target_policy_max_count": policy_details.get("max_target_count"),
        "raw_target_count": policy_details.get("raw_target_count"),
        "raw_target_ids": [
            memory_id
            for memory_id in str(policy_details.get("raw_target_ids") or "").split(",")
            if memory_id
        ],
        "proposal": proposal.model_dump(mode="json"),
        "validation": validation.model_dump(mode="json"),
    }


def _safe_judge_trace_records(trace: ExecutionTrace) -> list[dict[str, Any]]:
    """Serialize Judge trace metadata without raw model/error payloads.

    The adapter deliberately records bounded parse/policy fields, but an
    exception's text can contain provider-specific request details.  Keep the
    evaluator artifact useful for target-policy diagnostics while excluding
    unbounded raw responses and exception strings.
    """

    safe: list[dict[str, Any]] = []
    for record in trace.snapshot():
        safe.append(
            {
                "name": record.name,
                "duration_ms": round(float(record.duration_ms), 3),
                "status": record.status.value,
                "details": dict(record.details),
            }
        )
    return safe


def _judge_failure_category(exc: BaseException) -> str:
    """Classify a Judge failure without exposing unbounded model output."""

    text = f"{type(exc).__name__} {exc}".casefold()
    transport_markers = (
        "timeout",
        "timed out",
        "connection",
        "transport",
        "rate limit",
        "429",
        "502",
        "503",
        "504",
        "http",
        "api error",
    )
    if any(marker in text for marker in transport_markers):
        return "transport"
    return "parse_or_schema"


async def _run_store_integration(
    *,
    case: dict[str, Any],
    bank_records: list[dict[str, Any]],
    incoming: MemoryCandidate,
    incoming_status: MemoryStatus,
    relation_stage: dict[str, Any],
    user_id: str,
    relationship_id: str,
    collision: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Apply one governed proposal to a case-local in-memory Store."""

    store = InMemoryMemoryStore(clock=lambda: REFERENCE_TIME)
    fixture_to_actual: dict[str, str] = {}
    seed_created: dict[str, bool] = {}
    try:
        for row in bank_records:
            result = await store.save_memory(
                user_id=user_id,
                relationship_id=relationship_id,
                candidate=_candidate_from_row(
                    row,
                    reference_time=REFERENCE_TIME - timedelta(days=30),
                ),
                source_message_id=f"{case['case_id']}-seed-{row['memory_id']}",
                status=MemoryStatus(row.get("status", "confirmed")),
            )
            fixture_to_actual[row["memory_id"]] = result.item.id
            seed_created[row["memory_id"]] = result.created

        before = await store.list_memories(
            user_id=user_id,
            relationship_id=relationship_id,
            limit=500,
            read_only=True,
        )
        before_by_id = {item.id: item for item in before}
        proposal = SemanticRelationProposal.model_validate(relation_stage["proposal"])
        validation = LongTailRelationValidation.model_validate(relation_stage["validation"])
        effective_relation = validation.validated_relation
        effective_fixture_targets = (
            list(proposal.target_memory_ids) if validation.validator_pass else []
        )
        effective_actual_targets = [
            fixture_to_actual[memory_id]
            for memory_id in effective_fixture_targets
            if memory_id in fixture_to_actual
        ]
        operation_candidate = incoming
        if effective_relation == ClaimRelation.SAME and len(effective_actual_targets) == 1:
            target = before_by_id.get(effective_actual_targets[0])
            if target is not None and memory_role(target) != MemoryRole.RECENT_EVENT:
                operation_candidate = _candidate_for_same_merge(incoming, target)
        operation_candidate = operation_candidate.model_copy(
            update={"claim_relation": effective_relation}
        )
        batch = MemoryWriteBatch(
            source_message_id=f"{case['case_id']}-incoming",
            operations=[
                MemoryWriteOperation(
                    candidate=operation_candidate,
                    status=incoming_status,
                    relation=effective_relation,
                    target_memory_ids=effective_actual_targets,
                    rule_name=f"longtail_write_v2_{effective_relation.value}",
                    reason=(
                        "V2 isolated write after production long-tail validation; "
                        "production Store mutation is not permitted."
                    ),
                    score_breakdown={
                        "raw_relation": proposal.relation.value,
                        "validator_pass": validation.validator_pass,
                        "validated_relation": validation.validated_relation.value,
                    },
                )
            ],
        )
        committed = await store.commit_memory_batch(
            user_id=user_id,
            relationship_id=relationship_id,
            batch=batch,
        )
        after = await store.list_memories(
            user_id=user_id,
            relationship_id=relationship_id,
            limit=500,
            read_only=True,
        )
        audits = await store.list_transition_audits(
            user_id=user_id,
            relationship_id=relationship_id,
            source_message_id=batch.source_message_id,
        )
        after_by_id = {item.id: item for item in after}
        actual_superseded = {
            fixture_id
            for fixture_id, actual_id in fixture_to_actual.items()
            if after_by_id.get(actual_id)
            and after_by_id[actual_id].status == MemoryStatus.SUPERSEDED
        }
        expected = _expected_store_contract(case)
        saved = committed.saved[0] if committed.saved else None
        actual_new_row = bool(saved and saved.created)
        actual_final_status = saved.item.status.value if saved and saved.created else None
        actual_action = (
            "supersede_and_add"
            if actual_superseded
            else "add_without_supersede"
            if actual_new_row
            else "merge_or_refresh"
        )
        expected_preserve = set(expected["preserve_memory_ids"])
        actual_preserve = {
            fixture_id
            for fixture_id, actual_id in fixture_to_actual.items()
            if after_by_id.get(actual_id)
            and after_by_id[actual_id].status != MemoryStatus.SUPERSEDED
        }
        expected_superseded = set(expected["supersede_memory_ids"])
        exact_gold_aliases = _gold_collision_alias_ids(
            collision,
            expected_targets=set(_expected_semantic_target_ids(case)),
            exact_only=True,
        )
        checks = {
            "write_action": actual_action == expected["write_action"],
            "new_row_decision": actual_new_row == expected["new_row_expected"],
            "final_status": actual_final_status == expected["incoming_final_status"],
            "supersede_exact_match": (
                expected_superseded <= actual_superseded
                and not (actual_superseded - expected_superseded - exact_gold_aliases)
            ),
            "preserve_exact_match": expected_preserve <= actual_preserve,
        }
        return {
            "production_store_mutation_permitted": False,
            "isolated_store_mutation_permitted": True,
            "fixture_to_actual_ids": fixture_to_actual,
            "seed_created": seed_created,
            "seed_identity_collision_count": len(fixture_to_actual)
            - len(set(fixture_to_actual.values())),
            "effective_relation": effective_relation.value,
            "effective_target_ids": effective_fixture_targets,
            "expected": expected,
            "actual_write_action": actual_action,
            "actual_new_row": actual_new_row,
            "actual_incoming_memory_id": saved.item.id if saved else None,
            "actual_incoming_final_status": actual_final_status,
            "actual_supersede_memory_ids": sorted(actual_superseded),
            "actual_preserve_memory_ids": sorted(actual_preserve),
            "before_rows": _store_rows(before, fixture_to_actual),
            "after_rows": _store_rows(after, fixture_to_actual),
            "transition_audits": [audit.model_dump(mode="json") for audit in audits],
            "checks": checks,
            "error": None,
        }
    except Exception as exc:
        return {
            "production_store_mutation_permitted": False,
            "isolated_store_mutation_permitted": True,
            "fixture_to_actual_ids": fixture_to_actual,
            "seed_created": seed_created,
            "seed_identity_collision_count": len(fixture_to_actual)
            - len(set(fixture_to_actual.values())),
            "effective_relation": None,
            "effective_target_ids": [],
            "expected": _expected_store_contract(case),
            "actual_write_action": None,
            "actual_new_row": None,
            "actual_incoming_memory_id": None,
            "actual_incoming_final_status": None,
            "actual_supersede_memory_ids": [],
            "actual_preserve_memory_ids": [],
            "before_rows": [],
            "after_rows": [],
            "transition_audits": [],
            "checks": {
                "write_action": False,
                "new_row_decision": False,
                "final_status": False,
                "supersede_exact_match": False,
                "preserve_exact_match": False,
            },
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        await store.aclose()


def _expected_store_contract(case: Mapping[str, Any]) -> dict[str, Any]:
    relation = ClaimRelation(case["expected_relation"])
    retrieval_candidates = _expected_retrieval_candidate_ids(case)
    targets = _expected_semantic_target_ids(case)
    status = MemoryStatus(case["incoming"].get("status", "confirmed"))
    # The production validator deliberately supports one destructive target.
    destructive = (
        relation == ClaimRelation.UPDATE and len(targets) == 1 and status == MemoryStatus.CONFIRMED
    )
    merge = relation == ClaimRelation.SAME and len(targets) == 1
    if destructive:
        action = "supersede_and_add"
    elif merge:
        action = "merge_or_refresh"
    else:
        action = "add_without_supersede"
    supersede = targets if destructive else []
    bank_ids = [
        *(row["memory_id"] for row in case["overlay"]),
    ]
    # Shared IDs are added by the caller when preserve behavior is observed;
    # the explicit fixture contract only protects every overlay non-target.
    return {
        "expected_retrieval_candidate_ids": retrieval_candidates,
        "expected_semantic_target_ids": targets,
        "write_action": action,
        "new_row_expected": not merge,
        "incoming_final_status": None if merge else status.value,
        "supersede_memory_ids": supersede,
        "preserve_memory_ids": [memory_id for memory_id in bank_ids if memory_id not in supersede],
        "multi_target_destructive_fail_closed": (
            relation == ClaimRelation.UPDATE and len(targets) > 1
        ),
    }


def _relation_checks(
    case: Mapping[str, Any],
    stage: Mapping[str, Any],
) -> dict[str, bool]:
    proposal = stage["proposal"]
    expected_targets = _expected_semantic_target_ids(case)
    actual_targets = list(proposal["target_memory_ids"])
    return {
        "judge_completed": stage["judge_status"] == "completed",
        "relation": proposal["relation"] == case["expected_relation"],
        "target_exact": actual_targets == expected_targets,
        "target_set": set(actual_targets) == set(expected_targets),
    }


def _safety_checks(
    *,
    case: Mapping[str, Any],
    incoming: MemoryCandidate,
    bank_items: list[MemoryItem],
    relation_stage: Mapping[str, Any],
    store_result: Mapping[str, Any],
    collision: Mapping[str, Any] | None,
) -> dict[str, bool]:
    # Only semantic targets authorize relation/write expectations.  Retrieval
    # reference IDs for an UNCERTAIN case are deliberately not allowed here.
    expected_targets = set(_expected_semantic_target_ids(case))
    exact_gold_aliases = _gold_collision_alias_ids(
        collision,
        expected_targets=expected_targets,
        exact_only=True,
    )
    equivalent_targets = expected_targets | exact_gold_aliases
    allowed_targets = equivalent_targets | set(case.get("allowed_related_target_ids", []))
    proposed_targets = set(relation_stage["proposal"].get("target_memory_ids", []))
    actual_superseded = set(store_result.get("actual_supersede_memory_ids", []))
    item_by_id = {item.id: item for item in bank_items}
    linked_items = [item_by_id[item] for item in proposed_targets if item in item_by_id]
    superseded_items = [item_by_id[item] for item in actual_superseded if item in item_by_id]
    incoming_is_event = memory_role(incoming) == MemoryRole.RECENT_EVENT
    expected_relation = ClaimRelation(case["expected_relation"])
    effective_relation = store_result.get("effective_relation")
    false_link = bool(proposed_targets - allowed_targets)
    validator = relation_stage.get("validation", {})
    validator_pass = (
        bool(validator.get("validator_pass")) if isinstance(validator, Mapping) else False
    )
    actual_destructive_write = bool(actual_superseded)
    false_link_validator_allowed = bool(false_link and validator_pass)
    false_link_actual_destructive = bool(false_link and actual_destructive_write)
    return {
        "false_supersede": bool(
            actual_superseded - equivalent_targets
            or (actual_superseded and expected_relation != ClaimRelation.UPDATE)
        ),
        "false_merge": bool(
            store_result.get("actual_new_row") is False and expected_relation != ClaimRelation.SAME
        ),
        # Target mistakes are proposal-level diagnostics.  They are kept
        # separate from actual destructive effects below so a validator-denied
        # proposal cannot be misreported as a Store supersession.
        "false_link": false_link,
        "false_link_blocked": bool(false_link and not validator_pass),
        "false_link_validator_allowed": false_link_validator_allowed,
        "false_link_authorized": false_link_actual_destructive,
        "cross_subject_false_link": any(
            _subject_key(item.subject) != _subject_key(incoming.subject) for item in linked_items
        ),
        "event_false_dedupe": bool(
            incoming_is_event and store_result.get("actual_new_row") is False
        ),
        "event_false_supersede": bool(incoming_is_event and actual_superseded),
        "event_to_pattern_false_update": any(
            incoming_is_event and memory_role(item) == MemoryRole.INTERACTION_PATTERN
            for item in superseded_items
        ),
        "custom_to_canonical_false_supersede": any(
            incoming.predicate_type != item.predicate_type for item in superseded_items
        ),
        "proposed_overwrites_confirmed": any(
            MemoryStatus(case["incoming"].get("status", "confirmed")) == MemoryStatus.PROPOSED
            and item.status == MemoryStatus.CONFIRMED
            for item in superseded_items
        ),
        "uncertain_destructive_update": bool(
            effective_relation == ClaimRelation.UNCERTAIN.value and actual_superseded
        ),
        "non_target_supersede": bool(actual_superseded - equivalent_targets),
        "historical_event_not_preserved": any(
            item.kind == MemoryKind.INTERACTION_EVENT
            and item.id in actual_superseded
            and item.id not in equivalent_targets
            for item in bank_items
        ),
        "actual_destructive_write": actual_destructive_write,
        "false_link_actual_destructive": false_link_actual_destructive,
    }


def _attribute_failure(
    *,
    case: Mapping[str, Any],
    collision: Mapping[str, Any] | None,
    retrieval_checks: Mapping[str, bool],
    retrieved_checks: Mapping[str, bool],
    relation_stage: Mapping[str, Any],
    store_result: Mapping[str, Any],
    retrieval: Mapping[str, Any],
) -> dict[str, Any]:
    stages: list[str] = []
    secondary: list[str] = []
    if retrieval.get("embedding_fallback_used"):
        stages.append("RETRIEVAL_MISS")
        secondary.append("EMBEDDING_FALLBACK")
    elif not retrieval_checks["gold_in_top_20"]:
        stages.append("RETRIEVAL_MISS")
    elif not retrieval_checks["gold_retained_after_ranking"]:
        stages.append("RANKING_DROP")
    if relation_stage["judge_status"] == "failed":
        stages.append(
            "MODEL_TRANSPORT_ERROR"
            if relation_stage.get("judge_error_category") == "transport"
            else "MODEL_PARSE_ERROR"
        )
    elif not retrieved_checks["relation"]:
        stages.append("SEMANTIC_RELATION_ERROR")
    elif not retrieved_checks["target_set"]:
        stages.append("TARGET_SELECTION_ERROR")
    validation = relation_stage["validation"]
    raw_correct = retrieved_checks["relation"] and retrieved_checks["target_set"]
    expected_semantic_targets = _expected_semantic_target_ids(case)
    expected_destructive = (
        case["expected_relation"] == ClaimRelation.UPDATE.value
        and len(expected_semantic_targets) == 1
    )
    if raw_correct and expected_destructive and not validation["would_update"]:
        stages.append("SAFETY_DOWNGRADE")
    # A governed validator downgrade intentionally produces an add/no-op in
    # the isolated Store. Failed contract checks therefore do not imply a
    # Store failure. When the Store reports an actual exception/rollback,
    # attribute that stage before the derived write-contract mismatch so the
    # primary stage cannot hide the real application failure.
    if store_result.get("error"):
        stages.append("STORE_APPLICATION_ERROR")
    elif not store_result["checks"]["write_action"]:
        stages.append("WRITE_POLICY_ERROR")
    if stages and _collision_caused_identity_mismatch(
        case=case,
        collision=collision,
        retrieval=retrieval,
        relation_stage=relation_stage,
        store_result=store_result,
    ):
        # A documented equivalence alias is valid dataset metadata, not a
        # dataset contract error.  Preserve the raw-ID failure as primary and
        # expose the alias substitution only as a diagnostic.
        secondary.append("EQUIVALENT_ALIAS_SUBSTITUTION")
    stages = list(dict.fromkeys(stages))
    return {
        "primary": stages[0] if stages else None,
        "secondary": list(dict.fromkeys([*secondary, *stages[1:]])),
    }


def _build_report(
    rows: list[dict[str, Any]],
    *,
    dataset: dict[str, Any],
    case_path: Path,
    shared_bank_path: Path,
    case_id: str | None,
    slice_name: str | None,
    vector_limit: int,
    rank_limit: int,
    embedding_telemetry: dict[str, Any],
) -> dict[str, Any]:
    retrieval_metrics = _retrieval_metrics(rows)
    oracle_metrics = _relation_metrics(rows, field="oracle_relation")
    retrieved_metrics = _relation_metrics(rows, field="retrieved_relation")
    write_metrics = _write_metrics(rows)
    multi_target_metrics = _multi_target_metrics(rows)
    known_limitation_metrics = _known_limitation_metrics(rows)
    safety_metrics = _safety_metrics(rows)
    safety_status = "PASS" if _safety_count_is_zero(safety_metrics) else "SAFETY_REGRESSION"
    review_excluded_rows = [row for row in rows if not row.get("dataset_review_required")]
    review_excluded_metrics = {
        "case_count": len(review_excluded_rows),
        "case_ids": [row.get("case_id") for row in review_excluded_rows],
        "excluded_case_ids": [
            row.get("case_id") for row in rows if row.get("dataset_review_required")
        ],
        "retrieval": _retrieval_metrics(review_excluded_rows),
        "oracle_relation": _relation_metrics(
            review_excluded_rows,
            field="oracle_relation",
        ),
        "retrieved_relation": _relation_metrics(
            review_excluded_rows,
            field="retrieved_relation",
        ),
        "write": _write_metrics(review_excluded_rows),
        "safety": _safety_metrics(review_excluded_rows),
    }
    failures = Counter(
        row.get("primary_failure_stage") for row in rows if row.get("primary_failure_stage")
    )
    secondary = Counter(stage for row in rows for stage in row.get("secondary_failure_stages", []))
    query_latencies = list(embedding_telemetry.get("query_latencies_ms", []))
    document_latencies = list(embedding_telemetry.get("document_latencies_ms", []))
    if not document_latencies and embedding_telemetry.get("document_latency_ms"):
        document_latencies = [float(embedding_telemetry["document_latency_ms"])]
    embedding = {
        **embedding_telemetry,
        "document_latency_p50_ms": round(
            _percentile(document_latencies, 0.50),
            3,
        ),
        "document_latency_p95_ms": round(
            _percentile(document_latencies, 0.95),
            3,
        ),
        "query_latency_p50_ms": _percentile(query_latencies, 0.50),
        "query_latency_p95_ms": _percentile(query_latencies, 0.95),
        "query_latency_total_ms": round(sum(query_latencies), 3),
    }
    judge = _judge_telemetry(rows)
    contradiction_diagnostics = {
        "oracle": _contradiction_diagnostics(rows, field="oracle_relation"),
        "retrieved": _contradiction_diagnostics(rows, field="retrieved_relation"),
    }
    safety_coverage = _safety_coverage(dataset)
    collision_ids = set(dataset["collision_audit"]["gold_collision_case_ids"])
    evaluated_collision_ids = sorted(collision_ids & {row["case_id"] for row in rows})
    retrieval_engines = sorted(
        {
            str(row.get("retrieval", {}).get("retrieval_engine", "benchmark_staged"))
            for row in rows
            if isinstance(row.get("retrieval"), Mapping)
        }
        or {"benchmark_staged"}
    )
    production_retrieval = "HybridMemoryRetriever" in retrieval_engines
    embedding_input = (
        "production_retrieval_text_composite"
        if production_retrieval
        else "natural_language_text_only"
    )
    review_case_ids = sorted(
        {str(row.get("case_id")) for row in review_excluded_rows if row.get("case_id")}
    )
    excluded_case_ids = sorted(
        {str(row.get("case_id")) for row in rows if row.get("dataset_review_required")}
    )
    passed_count = sum(bool(row.get("passed")) for row in rows)
    acceptance_checks = _evaluation_status_checks(retrieval_metrics, retrieved_metrics)
    acceptance_checks["destructive_safety"] = _safety_count_is_zero(safety_metrics)
    return {
        "version": REPORT_VERSION,
        "dataset": {
            "case_path": str(case_path),
            "shared_bank_path": str(shared_bank_path),
            "case_sha256": hashlib.sha256(case_path.read_bytes()).hexdigest(),
            "shared_bank_sha256": hashlib.sha256(shared_bank_path.read_bytes()).hexdigest(),
            "status": dataset["dataset_status"],
            "structural_validation": dataset["structural_validation"],
            "collision_audit": dataset["collision_audit"],
        },
        "filters": {"case_id": case_id, "slice": slice_name},
        "case_count": len(rows),
        "evaluated_row_count": len(rows),
        "passed_case_count": passed_count,
        "failed_case_count": len(rows) - passed_count,
        "dataset_review_case_count": len(evaluated_collision_ids),
        "dataset_review_case_ids": evaluated_collision_ids,
        "parameters": {
            "candidate_pool": "all_shared_memories + case.overlay",
            "candidate_pool_contract": "120 shared + 5 overlay = 125 per case",
            "vector_top_k": vector_limit,
            "cheap_rank_top_n": rank_limit,
            "semantic_judge_candidate_limit": rank_limit,
            "retrieval_engine": retrieval_engines,
            "embedding_input": embedding_input,
            "embedding_input_detail": (
                "HybridMemoryRetriever._retrieval_text: summary + original text + "
                "canonical/state fields + selected payload + evidence spans"
                if production_retrieval
                else "incoming/seed natural-language text only"
            ),
            "judge_query_input": (
                "incoming summary + evidence spans"
                if production_retrieval
                else "incoming natural-language text"
            ),
            "oracle_candidates": "overlay Gold + hard negatives",
            "multi_target_destructive_write": "unsupported_fail_closed",
            "validator_temporal_contract": (
                "only explicit row temporal fields are preserved; absent temporal "
                "fields remain unknown with no synthesized timestamps"
            ),
        },
        "retrieval_metrics": retrieval_metrics,
        "oracle_relation_metrics": oracle_metrics,
        "retrieved_relation_metrics": retrieved_metrics,
        "contradiction_diagnostics": contradiction_diagnostics,
        "write_metrics": write_metrics,
        "multi_target_metrics": multi_target_metrics,
        "known_limitation_metrics": known_limitation_metrics,
        "safety_metrics": safety_metrics,
        "safety_status": safety_status,
        "acceptance_checks": acceptance_checks,
        "safety_coverage": safety_coverage,
        "review_excluded_metrics": {
            **review_excluded_metrics,
            "case_count": len(review_case_ids),
            "evaluated_row_count": len(review_excluded_rows),
            "case_ids": review_case_ids,
            "excluded_case_ids": excluded_case_ids,
        },
        "failure_attribution": {
            "primary": dict(sorted(failures.items())),
            "secondary": dict(sorted(secondary.items())),
        },
        "telemetry": {
            "embedding": embedding,
            "judge": judge,
            "estimated_cost_per_100_writes": "N/A",
        },
        "production_store_mutation_permitted": False,
        "store_mutation_permitted": False,
        "isolated_in_memory_store_mutation_permitted": True,
        "rows": rows,
        "status": _evaluation_status(
            retrieval_metrics,
            retrieved_metrics,
            safety_metrics,
            dataset_status=dataset["dataset_status"],
        ),
        "evaluation_mode": "shadow_injected",
        "methodology": "injected_embedding_and_relation_judge_with_isolated_store_shadow",
    }


def _safety_coverage(dataset: Mapping[str, Any]) -> dict[str, Any]:
    """Describe which safety invariants the fixture can actually exercise.

    The V2 draft stores natural-language rows and intentionally does not carry
    canonical predicate fields.  A zero violation count for a canonical-only
    invariant is therefore not evidence that the invariant was tested.
    """

    rows: list[Mapping[str, Any]] = []
    rows.extend(row for row in dataset.get("shared_memories", []) if isinstance(row, Mapping))
    for case in dataset.get("cases", []):
        if not isinstance(case, Mapping):
            continue
        incoming = case.get("incoming")
        if isinstance(incoming, Mapping):
            rows.append(incoming)
        rows.extend(row for row in case.get("overlay", []) if isinstance(row, Mapping))

    canonical_count = 0
    for row in rows:
        predicate_type = row.get("predicate_type")
        if predicate_type is None:
            continue
        value = getattr(predicate_type, "value", predicate_type)
        if str(value) != PredicateType.CUSTOM.value:
            canonical_count += 1
    explicit_temporal_count = sum(
        any(
            row.get(field) is not None
            for field in (
                "time_kind",
                "occurred_at",
                "period_start",
                "period_end",
                "expires_at",
                "temporal_precision",
            )
        )
        for row in rows
    )
    return {
        "custom_to_canonical_false_supersede": {
            "status": "TESTED" if canonical_count else "NOT_TESTED",
            "canonical_candidate_count": canonical_count,
            "reason": (
                "At least one canonical candidate is present."
                if canonical_count
                else "All fixture candidates use CUSTOM; canonical transition safety is vacuous."
            ),
        },
        "temporal_evidence": {
            "status": "PARTIALLY_TESTED" if explicit_temporal_count else "NOT_TESTED",
            "rows_with_explicit_temporal_fields": explicit_temporal_count,
            "reason": (
                "Only row-declared temporal fields are passed to the validator."
                if explicit_temporal_count
                else "Draft fixture has no typed temporal fields; no synthetic evidence is added."
            ),
        },
        "store_seed_identity": {
            "status": "AUXILIARY_ONLY",
            "reason": (
                "Observed Store-side identity collapses are diagnostic only; loader collision "
                "audit is the dataset source of truth."
            ),
        },
    }


def _retrieval_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [row for row in rows if _row_expected_retrieval_candidate_ids(row)]
    total_targets = sum(len(_row_expected_retrieval_candidate_ids(row)) for row in eligible)
    total_equivalent_targets = sum(
        len(
            _equivalence_expected_keys(
                set(_row_expected_retrieval_candidate_ids(row)),
                row.get("retrieval", {}),
            )
        )
        for row in eligible
    )

    def target_hits(source: str, limit: int | None = None) -> int:
        count = 0
        for row in eligible:
            values = row.get("retrieval", {}).get(source, [])
            ids = [item["memory_id"] for item in values]
            if limit is not None:
                ids = ids[:limit]
            count += len(set(ids) & set(_row_expected_retrieval_candidate_ids(row)))
        return count

    def case_hits(source: str, limit: int | None = None) -> int:
        count = 0
        for row in eligible:
            values = row.get("retrieval", {}).get(source, [])
            ids = [item["memory_id"] for item in values]
            if limit is not None:
                ids = ids[:limit]
            count += bool(set(ids) & set(_row_expected_retrieval_candidate_ids(row)))
        return count

    def equivalence_target_hits(source: str, limit: int | None = None) -> int:
        count = 0
        for row in eligible:
            retrieval = row.get("retrieval", {})
            values = retrieval.get(source, [])
            if limit is not None:
                values = values[:limit]
            expected_keys = _equivalence_expected_keys(
                set(_row_expected_retrieval_candidate_ids(row)),
                retrieval,
            )
            count += len(expected_keys & _equivalence_candidate_keys(values))
        return count

    reciprocal_ranks: list[float] = []
    for row in eligible:
        expected = set(_row_expected_retrieval_candidate_ids(row))
        ranks = [
            item["rank"]
            for item in row.get("retrieval", {}).get("vector", [])
            if item["memory_id"] in expected
        ]
        reciprocal_ranks.append(1 / min(ranks) if ranks else 0.0)
    top20_hits = target_hits("vector", 20)
    ranked_hits = target_hits("ranked")
    candidate_counts = [row.get("candidate_pool_size", 0) for row in rows]
    # Retention is measured against the unrelated candidates that actually
    # survived Vector Top-20, not against every row in the ranked output.  The
    # denominator is therefore candidate-level and case-local; otherwise a
    # larger Top-5 or a case with many related distractors changes the metric's
    # meaning.
    unrelated_vector_count = 0
    unrelated_ranked_count = 0
    for row in rows:
        vector_unrelated = {
            candidate["memory_id"]
            for candidate in row.get("retrieval", {}).get("vector", [])
            if candidate.get("benchmark_role") in UNRELATED_BENCHMARK_ROLES
        }
        ranked_unrelated = {
            candidate["memory_id"]
            for candidate in row.get("retrieval", {}).get("ranked", [])
            if candidate.get("benchmark_role") in UNRELATED_BENCHMARK_ROLES
        }
        unrelated_vector_count += len(vector_unrelated)
        unrelated_ranked_count += len(ranked_unrelated & vector_unrelated)
    ranked_candidates = [
        candidate for row in rows for candidate in row.get("retrieval", {}).get("ranked", [])
    ]
    raw_recall_at_5 = _ratio(target_hits("vector", 5), total_targets)
    raw_recall_at_10 = _ratio(target_hits("vector", 10), total_targets)
    raw_recall_at_20 = _ratio(top20_hits, total_targets)
    conditional_retention = _ratio(ranked_hits, top20_hits)
    end_to_end_recall_at_5 = _ratio(ranked_hits, total_targets)
    duplicate_slots_at_20 = sum(
        _equivalence_duplicate_slot_count(
            row.get("retrieval", {}).get("vector", [])[:20]
        )
        for row in rows
    )
    duplicate_slots_at_5 = sum(
        _equivalence_duplicate_slot_count(row.get("retrieval", {}).get("ranked", []))
        for row in rows
    )
    retrieval_latencies = [
        row["retrieval"]["latency_ms"] for row in rows if "retrieval" in row
    ]
    cheap_ranking_latencies = [
        float(row["retrieval"].get("cheap_ranking_latency_ms") or 0.0)
        for row in rows
        if "retrieval" in row
    ]
    vector_ranking_latencies = [
        float(row["retrieval"].get("vector_ranking_latency_ms") or 0.0)
        for row in rows
        if "retrieval" in row and row["retrieval"].get("vector_ranking_latency_ms") is not None
    ]
    return {
        "retrieval_expected_case_count": len(eligible),
        "retrieval_expected_target_count": total_targets,
        "retrieval_hit_at_1": _ratio(case_hits("vector", 1), len(eligible)),
        "retrieval_hit_at_3": _ratio(case_hits("vector", 3), len(eligible)),
        "retrieval_hit_at_5": _ratio(case_hits("vector", 5), len(eligible)),
        "retrieval_hit_at_10": _ratio(case_hits("vector", 10), len(eligible)),
        "retrieval_hit_at_20": _ratio(case_hits("vector", 20), len(eligible)),
        "retrieval_recall_at_5": raw_recall_at_5,
        "retrieval_recall_at_10": raw_recall_at_10,
        "retrieval_recall_at_20": raw_recall_at_20,
        "raw_retrieval_recall_at_5": raw_recall_at_5,
        "raw_retrieval_recall_at_10": raw_recall_at_10,
        "raw_retrieval_recall_at_20": raw_recall_at_20,
        "equivalence_aware_recall_at_5": _ratio(
            equivalence_target_hits("vector", 5),
            total_equivalent_targets,
        ),
        "equivalence_aware_recall_at_10": _ratio(
            equivalence_target_hits("vector", 10),
            total_equivalent_targets,
        ),
        "equivalence_aware_recall_at_20": _ratio(
            equivalence_target_hits("vector", 20),
            total_equivalent_targets,
        ),
        "mrr": round(mean(reciprocal_ranks), 4) if reciprocal_ranks else 0.0,
        "conditional_gold_retention_at_5": conditional_retention,
        # Compatibility alias retained for older report consumers.  This is a
        # conditional Top-20 -> Top-5 retention metric, not end-to-end recall.
        "gold_retention_at_5": conditional_retention,
        "end_to_end_gold_recall_at_5": end_to_end_recall_at_5,
        "target_set_recall_at_5": end_to_end_recall_at_5,
        "gold_target_set_exact_at_5": _ratio(
            sum(
                set(_row_expected_retrieval_candidate_ids(row))
                <= {item["memory_id"] for item in row.get("retrieval", {}).get("ranked", [])}
                for row in eligible
            ),
            len(eligible),
        ),
        "hard_negative_promotion_count": sum(
            bool(row.get("retrieval", {}).get("hard_negative_promoted")) for row in eligible
        ),
        "hard_negative_promotion_rate": _ratio(
            sum(bool(row.get("retrieval", {}).get("hard_negative_promoted")) for row in eligible),
            len(eligible),
        ),
        "unrelated_candidate_retention_rate": _ratio(
            unrelated_ranked_count,
            unrelated_vector_count,
        ),
        "unrelated_candidate_vector_count": unrelated_vector_count,
        "unrelated_candidate_ranked_count": unrelated_ranked_count,
        "unrelated_candidate_retained_count": unrelated_ranked_count,
        "ranked_candidate_count": len(ranked_candidates),
        "equivalence_group_duplicate_slot_count": duplicate_slots_at_20,
        "equivalence_group_duplicate_slot_count_at_20": duplicate_slots_at_20,
        "equivalence_group_duplicate_slot_count_at_5": duplicate_slots_at_5,
        "avg_candidate_count": (round(mean(candidate_counts), 4) if candidate_counts else 0.0),
        "retrieval_latency_p50_ms": _percentile(
            retrieval_latencies,
            0.50,
        ),
        "retrieval_latency_p95_ms": _percentile(
            retrieval_latencies,
            0.95,
        ),
        "cheap_ranking_latency_p50_ms": _percentile(cheap_ranking_latencies, 0.50),
        "cheap_ranking_latency_p95_ms": _percentile(cheap_ranking_latencies, 0.95),
        "vector_ranking_latency_p50_ms": _percentile(vector_ranking_latencies, 0.50),
        "vector_ranking_latency_p95_ms": _percentile(vector_ranking_latencies, 0.95),
    }


def _equivalence_candidate_keys(candidates: Sequence[Mapping[str, Any]]) -> set[str]:
    return {
        _equivalence_key(
            str(candidate.get("memory_id")),
            candidate.get("equivalent_memory_group_id"),
        )
        for candidate in candidates
    }


def _equivalence_expected_keys(
    expected_ids: set[str],
    retrieval: Mapping[str, Any],
) -> set[str]:
    inventory = retrieval.get("candidate_inventory", [])
    group_by_id = {
        str(candidate.get("memory_id")): candidate.get("equivalent_memory_group_id")
        for candidate in inventory
        if isinstance(candidate, Mapping)
    }
    return {
        _equivalence_key(memory_id, group_by_id.get(memory_id))
        for memory_id in expected_ids
    }


def _equivalence_key(memory_id: str, group_id: object) -> str:
    normalized_group = str(group_id).strip() if group_id is not None else ""
    return f"group:{normalized_group}" if normalized_group else f"memory:{memory_id}"


def _equivalence_duplicate_slot_count(
    candidates: Sequence[Mapping[str, Any]],
) -> int:
    groups = [
        str(candidate.get("equivalent_memory_group_id")).strip()
        for candidate in candidates
        if candidate.get("equivalent_memory_group_id")
    ]
    return sum(max(count - 1, 0) for count in Counter(groups).values())


def _relation_metrics(
    rows: list[dict[str, Any]],
    *,
    field: str,
) -> dict[str, Any]:
    evaluated = [row for row in rows if field in row]
    labels = [relation.value for relation in ClaimRelation]
    confusion: Counter[str] = Counter()
    correct = 0
    exact = 0
    set_exact = 0
    expected_total = 0
    predicted_total = 0
    correct_targets = 0
    failures = 0
    for row in evaluated:
        expected_relation = row["expected_relation"]
        stage = row[field]
        actual_relation = stage["proposal"]["relation"]
        expected_target_list = _row_expected_semantic_target_ids(row)
        actual_target_list = list(stage["proposal"]["target_memory_ids"])
        expected_targets = set(expected_target_list)
        actual_targets = set(actual_target_list)
        confusion[f"{expected_relation}->{actual_relation}"] += 1
        correct += actual_relation == expected_relation
        exact += actual_target_list == expected_target_list
        set_exact += actual_targets == expected_targets
        expected_total += len(expected_targets)
        predicted_total += len(actual_targets)
        correct_targets += len(expected_targets & actual_targets)
        failures += stage["judge_status"] == "failed"
    per_relation: dict[str, Any] = {}
    f1_values: list[float] = []
    for label in labels:
        tp = confusion[f"{label}->{label}"]
        support = sum(count for key, count in confusion.items() if key.startswith(f"{label}->"))
        predicted = sum(count for key, count in confusion.items() if key.endswith(f"->{label}"))
        precision = _ratio(tp, predicted)
        recall = _ratio(tp, support)
        f1 = _f1(precision, recall)
        if support:
            f1_values.append(f1)
        per_relation[label] = {
            "support": support,
            "predicted": predicted,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
    precision = _ratio(correct_targets, predicted_total)
    recall = _ratio(correct_targets, expected_total)
    update = per_relation[ClaimRelation.UPDATE.value]
    return {
        "evaluated_count": len(evaluated),
        "judge_failure_count": failures,
        "relation_accuracy": _ratio(correct, len(evaluated)),
        "macro_f1": round(mean(f1_values), 4) if f1_values else 0.0,
        "per_relation": per_relation,
        "relation_confusion": dict(sorted(confusion.items())),
        "update_precision": update["precision"],
        "update_recall": update["recall"],
        "target_exact_match": _ratio(exact, len(evaluated)),
        "target_set_accuracy": _ratio(set_exact, len(evaluated)),
        "target_memory_accuracy": _ratio(set_exact, len(evaluated)),
        "target_micro_precision": precision,
        "target_memory_precision": precision,
        "target_micro_recall": recall,
        "target_micro_f1": _f1(precision, recall),
    }


def _contradiction_diagnostics(
    rows: Sequence[Mapping[str, Any]],
    *,
    field: str,
) -> dict[str, Any]:
    relation_metrics = _relation_metrics(
        [dict(row) for row in rows if field in row],
        field=field,
    )
    contradiction = relation_metrics.get("per_relation", {}).get(
        ClaimRelation.CONTRADICTION.value,
        {},
    )
    cases: list[dict[str, Any]] = []
    for row in rows:
        stage = row.get(field)
        if not isinstance(stage, Mapping):
            continue
        proposal = stage.get("proposal")
        if not isinstance(proposal, Mapping):
            continue
        if (
            row.get("expected_relation") != ClaimRelation.CONTRADICTION.value
            and proposal.get("relation") != ClaimRelation.CONTRADICTION.value
        ):
            continue
        retrieval = row.get("retrieval", {})
        inventory = (
            retrieval.get("candidate_inventory", [])
            if isinstance(retrieval, Mapping)
            else []
        )
        candidate_by_id = {
            str(candidate.get("memory_id")): candidate
            for candidate in inventory
            if isinstance(candidate, Mapping)
        }
        expected_ids = _row_expected_retrieval_candidate_ids(row)
        candidate_records = [
            {
                "memory_id": memory_id,
                "text": candidate_by_id.get(memory_id, {}).get("text"),
                "kind": candidate_by_id.get(memory_id, {}).get("kind"),
                "subject": candidate_by_id.get(memory_id, {}).get("subject"),
                "status": candidate_by_id.get(memory_id, {}).get("status"),
            }
            for memory_id in expected_ids
        ]
        validation = stage.get("validation", {})
        cases.append(
            {
                "case_id": row.get("case_id"),
                "run_index": row.get("run_index", 1),
                "incoming": row.get("incoming_text"),
                "candidates": candidate_records,
                "gold_relation": row.get("expected_relation"),
                "actual_relation": proposal.get("relation"),
                "target_memory_ids": list(proposal.get("target_memory_ids", [])),
                "confidence": proposal.get("confidence"),
                "validator_pass": validation.get("validator_pass")
                if isinstance(validation, Mapping)
                else None,
                "validated_relation": validation.get("validated_relation")
                if isinstance(validation, Mapping)
                else None,
                "would_update": validation.get("would_update")
                if isinstance(validation, Mapping)
                else None,
            }
        )
    return {
        "support": contradiction.get("support", 0),
        "precision": contradiction.get("precision", 0.0),
        "recall": contradiction.get("recall", 0.0),
        "f1": contradiction.get("f1", 0.0),
        "cases": cases,
    }


def _write_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [row for row in rows if "store" in row]
    validation_rows = [
        row
        for row in rows
        if isinstance(row.get("retrieved_relation"), Mapping)
        and isinstance(row["retrieved_relation"].get("validation"), Mapping)
    ]

    def passed(name: str) -> int:
        return sum(bool(row["store"]["checks"].get(name)) for row in evaluated)

    return {
        "evaluated_count": len(evaluated),
        "store_action_accuracy": _ratio(passed("write_action"), len(evaluated)),
        "new_row_decision_accuracy": _ratio(passed("new_row_decision"), len(evaluated)),
        "final_status_accuracy": _ratio(passed("final_status"), len(evaluated)),
        "supersede_exact_match_accuracy": _ratio(passed("supersede_exact_match"), len(evaluated)),
        "preserve_exact_match_accuracy": _ratio(passed("preserve_exact_match"), len(evaluated)),
        "store_application_error_count": sum(bool(row["store"].get("error")) for row in evaluated),
        "transition_audit_count": sum(
            len(row["store"].get("transition_audits", [])) for row in evaluated
        ),
        "validator_allow_count": sum(
            bool(row["retrieved_relation"]["validation"].get("validator_pass"))
            for row in validation_rows
        ),
        "validator_deny_count": sum(
            not bool(row["retrieved_relation"]["validation"].get("validator_pass"))
            for row in validation_rows
        ),
    }


def _multi_target_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Expose the current validator's deliberate multi-target boundary."""

    proposals: list[tuple[Mapping[str, Any], Mapping[str, Any], str]] = []
    for row in rows:
        for field in ("oracle_relation", "retrieved_relation"):
            stage = row.get(field)
            if not isinstance(stage, Mapping):
                continue
            proposal = stage.get("proposal")
            if isinstance(proposal, Mapping) and len(proposal.get("target_memory_ids", [])) > 1:
                proposals.append((row, stage, field))
    denied = sum(
        not bool(stage.get("validation", {}).get("validator_pass")) for _, stage, _ in proposals
    )
    retrieved_proposals = [item for item in proposals if item[2] == "retrieved_relation"]
    oracle_proposals = [item for item in proposals if item[2] == "oracle_relation"]
    expected_multi_target_rows = [
        row for row in rows if len(_row_expected_semantic_target_ids(row)) > 1
    ]
    retrieved_expected_multi = [
        item
        for item in retrieved_proposals
        if len(_row_expected_semantic_target_ids(item[0])) > 1
    ]
    retrieved_exact_multi = [
        item
        for item in retrieved_expected_multi
        if item[0].get("expected_relation")
        == item[1].get("proposal", {}).get("relation")
        and set(_row_expected_semantic_target_ids(item[0]))
        == set(item[1].get("proposal", {}).get("target_memory_ids", []))
    ]
    unexpected_multi = [
        item
        for item in retrieved_proposals
        if len(_row_expected_semantic_target_ids(item[0])) <= 1
    ]
    overbroad_multi = [
        item
        for item in retrieved_proposals
        if set(item[1].get("proposal", {}).get("target_memory_ids", []))
        != set(_row_expected_semantic_target_ids(item[0]))
    ]
    destructive_writes = sum(
        len(row.get("store", {}).get("actual_supersede_memory_ids", [])) > 1
        for row, _, field in proposals
        if field == "retrieved_relation"
    )
    return {
        "proposal_count": len(proposals),
        "oracle_proposal_count": len(oracle_proposals),
        "retrieved_proposal_count": len(retrieved_proposals),
        "expected_multi_target_case_count": len(expected_multi_target_rows),
        "expected_multi_target_proposal_count": len(retrieved_expected_multi),
        "exact_expected_multi_target_proposal_count": len(retrieved_exact_multi),
        "missing_multi_target_proposal_count": sum(
            len(
                row.get("retrieved_relation", {})
                .get("proposal", {})
                .get("target_memory_ids", [])
            )
            <= 1
            for row in expected_multi_target_rows
        ),
        "unexpected_multi_target_proposal_count": len(unexpected_multi),
        "overbroad_multi_target_proposal_count": len(overbroad_multi),
        "validator_denied_count": denied,
        "retrieved_validator_denied_count": sum(
            not bool(stage.get("validation", {}).get("validator_pass"))
            for _, stage, _ in retrieved_proposals
        ),
        "policy_boundary_count": sum(
            not bool(stage.get("validation", {}).get("validator_pass"))
            for _, stage, _ in retrieved_exact_multi
        ),
        "destructive_multi_target_write_count": destructive_writes,
        "status": "UNSUPPORTED_FAIL_CLOSED" if proposals else "NOT_OBSERVED",
        "policy_boundary": "KNOWN_POLICY_BOUNDARY" if proposals else "NOT_OBSERVED",
    }


def _known_limitation_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize benchmark/validator boundaries without changing policy."""

    action_intent_rows = [
        row
        for row in rows
        if row.get("incoming_kind") == MemoryKind.ACTION_INTENT.value
        and row.get("retrieved_relation", {}).get("proposal", {}).get("relation")
        == ClaimRelation.UPDATE.value
    ]
    action_intent_denied = sum(
        not bool(
            row.get("retrieved_relation", {})
            .get("validation", {})
            .get("checks", {})
            .get("destructive_role_eligible", True)
        )
        for row in action_intent_rows
    )
    return {
        "multi_target": _multi_target_metrics(rows),
        "action_intent_update": {
            "proposal_count": len(action_intent_rows),
            "destructive_role_denied_count": action_intent_denied,
            "status": "VALIDATOR_POLICY_BOUNDARY" if action_intent_rows else "NOT_OBSERVED",
        },
    }


def _safety_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    names = [
        "false_supersede",
        "false_merge",
        "false_link",
        "cross_subject_false_link",
        "event_false_dedupe",
        "event_false_supersede",
        "event_to_pattern_false_update",
        "custom_to_canonical_false_supersede",
        "proposed_overwrites_confirmed",
        "uncertain_destructive_update",
        "non_target_supersede",
        "historical_event_not_preserved",
    ]
    result = {
        f"{name}_count": sum(bool(row.get("safety", {}).get(name)) for row in rows)
        for name in names
    }
    # ``false_link`` and ``cross_subject_false_link`` describe the Judge's
    # proposed target, not an applied mutation.  Keep their counts visible,
    # but do not fold validator-denied proposals into the actual destructive
    # write safety gate.
    result["proposal_safety_violation_count"] = sum(
        bool(row.get("safety", {}).get("false_link"))
        or bool(row.get("safety", {}).get("cross_subject_false_link"))
        for row in rows
    )
    result["validator_blocked_false_link_count"] = sum(
        bool(row.get("safety", {}).get("false_link_blocked")) for row in rows
    )
    result["validator_allowed_false_link_count"] = sum(
        bool(row.get("safety", {}).get("false_link_validator_allowed")) for row in rows
    )
    result["actual_false_link_write_count"] = sum(
        bool(row.get("safety", {}).get("false_link_actual_destructive")) for row in rows
    )
    result["actual_destructive_write_count"] = sum(
        bool(row.get("safety", {}).get("actual_destructive_write")) for row in rows
    )
    actual_violation_names = {
        "false_supersede_count",
        "false_merge_count",
        "event_false_dedupe_count",
        "event_false_supersede_count",
        "event_to_pattern_false_update_count",
        "custom_to_canonical_false_supersede_count",
        "proposed_overwrites_confirmed_count",
        "uncertain_destructive_update_count",
        "non_target_supersede_count",
        "historical_event_not_preserved_count",
        "actual_false_link_write_count",
    }
    result["actual_destructive_write_violation_count"] = sum(
        result[name] for name in actual_violation_names
    )
    # The benchmark's freeze condition is about effects applied to the Store,
    # not about a Judge proposal that the validator denied. Keep the canonical
    # safety field effect-based, and expose the broader proposal+write number as
    # a separate diagnostic so callers cannot mistake a blocked proposal for
    # data damage.
    result["destructive_safety_violation_count"] = result[
        "actual_destructive_write_violation_count"
    ]
    result["proposal_plus_write_safety_diagnostic_count"] = (
        result["proposal_safety_violation_count"]
        + result["actual_destructive_write_violation_count"]
    )
    result["false_destructive_update_count"] = result["actual_false_link_write_count"]
    result["false_destructive_update_rate"] = _ratio(
        result["false_destructive_update_count"],
        len(rows),
    )
    result["proposal_safety_violation_rate"] = _ratio(
        result["proposal_safety_violation_count"],
        len(rows),
    )
    result["actual_destructive_write_violation_rate"] = _ratio(
        result["actual_destructive_write_violation_count"],
        len(rows),
    )
    event_rows = [
        row for row in rows if row.get("incoming_kind") == MemoryKind.INTERACTION_EVENT.value
    ]
    result["historical_event_preservation_rate"] = _ratio(
        sum(
            not row.get("safety", {}).get("historical_event_not_preserved", False)
            for row in event_rows
        ),
        len(event_rows),
    )
    return result


def _judge_telemetry(rows: list[dict[str, Any]]) -> dict[str, Any]:
    stage_records = [
        (row, field, row[field])
        for row in rows
        for field in ("oracle_relation", "retrieved_relation")
        if field in row
    ]
    stages = [stage for _, _, stage in stage_records]
    proposals = [stage["proposal"] for stage in stages]
    completed = [stage for stage in stages if stage["judge_status"] == "completed"]
    failed = [stage for stage in stages if stage["judge_status"] == "failed"]
    latencies = [stage["observed_latency_ms"] for stage in completed]
    model_latencies = [
        proposal["latency_ms"] for proposal in proposals if proposal.get("latency_ms") is not None
    ]

    def token_total(name: str) -> int:
        return sum(int(proposal.get(name) or 0) for proposal in proposals)

    def stage_summary(field: str) -> dict[str, Any]:
        selected_records = [
            (row, stage)
            for row, stage_field, stage in stage_records
            if stage_field == field
        ]
        selected = [stage for _, stage in selected_records]
        selected_proposals = [stage["proposal"] for stage in selected]
        selected_completed = [stage for stage in selected if stage["judge_status"] == "completed"]
        selected_failed = [stage for stage in selected if stage["judge_status"] == "failed"]

        def total(name: str) -> int:
            return sum(int(proposal.get(name) or 0) for proposal in selected_proposals)

        latencies = [stage["observed_latency_ms"] for stage in selected_completed]
        relation_mismatches = 0
        target_mismatches = 0
        unexpected_targets = 0
        target_unavailable = 0
        for row, stage in selected_records:
            if stage["judge_status"] != "completed":
                continue
            proposal = stage["proposal"]
            relation_mismatches += proposal.get("relation") != row.get("expected_relation")
            expected_targets = set(_row_expected_semantic_target_ids(row))
            actual_targets = set(proposal.get("target_memory_ids", []))
            candidate_ids = set(stage.get("candidate_ids", []))
            target_mismatches += actual_targets != expected_targets
            unexpected_targets += bool(actual_targets - expected_targets)
            target_unavailable += bool(
                expected_targets and not expected_targets <= candidate_ids
            )
        return {
            "call_count": sum(stage["judge_status"] != "not_called" for stage in selected),
            "completed_count": len(selected_completed),
            "failure_count": len(selected_failed),
            "relation_mismatch_count": relation_mismatches,
            "target_mismatch_count": target_mismatches,
            "unexpected_target_count": unexpected_targets,
            "target_candidate_unavailable_count": target_unavailable,
            "latency_p50_ms": _percentile(latencies, 0.50),
            "latency_p95_ms": _percentile(latencies, 0.95),
            "prompt_tokens": total("prompt_tokens"),
            "completion_tokens": total("completion_tokens"),
            "total_tokens": total("total_tokens"),
            "avg_prompt_tokens": _ratio(total("prompt_tokens"), len(selected_completed)),
            "avg_completion_tokens": _ratio(
                total("completion_tokens"), len(selected_completed)
            ),
            "avg_total_tokens": _ratio(total("total_tokens"), len(selected_completed)),
        }

    relation_mismatch_count = 0
    target_mismatch_count = 0
    target_candidate_unavailable_count = 0
    target_gold_available_mismatch_count = 0
    retrieval_reference_unavailable_count = 0
    unexpected_target_count = 0
    target_policy_fail_closed_count = 0
    target_policy_accepted_count = 0
    for row, _, stage in stage_records:
        if stage["judge_status"] != "completed":
            continue
        if stage.get("target_policy_status") == "fail_closed":
            target_policy_fail_closed_count += 1
        elif stage.get("target_policy_status") == "accepted":
            target_policy_accepted_count += 1
        proposal = stage["proposal"]
        if proposal.get("relation") != row.get("expected_relation"):
            relation_mismatch_count += 1
        retrieval_references = set(_row_expected_retrieval_candidate_ids(row))
        expected_targets = set(_row_expected_semantic_target_ids(row))
        actual_targets = set(proposal.get("target_memory_ids", []))
        candidate_ids = set(stage.get("candidate_ids", []))
        if retrieval_references and not retrieval_references <= candidate_ids:
            retrieval_reference_unavailable_count += 1
        target_mismatch = actual_targets != expected_targets
        if target_mismatch:
            target_mismatch_count += 1
            if expected_targets and not expected_targets <= candidate_ids:
                target_candidate_unavailable_count += 1
            else:
                target_gold_available_mismatch_count += 1
        if actual_targets - expected_targets:
            unexpected_target_count += 1

    transport_failures = sum(stage.get("judge_error_category") == "transport" for stage in failed)
    parse_failures = sum(stage.get("judge_error_category") == "parse_or_schema" for stage in failed)
    unknown_failures = len(failed) - transport_failures - parse_failures

    return {
        "call_count": sum(stage["judge_status"] != "not_called" for stage in stages),
        "judge_evaluated_count": len(completed),
        "completed_count": len(completed),
        "failure_count": len(failed),
        "judge_transport_failure_count": transport_failures,
        "judge_parse_failure_count": parse_failures,
        "judge_unknown_failure_count": unknown_failures,
        "judge_relation_mismatch_count": relation_mismatch_count,
        "judge_target_mismatch_count": target_mismatch_count,
        "judge_target_candidate_unavailable_count": target_candidate_unavailable_count,
        "judge_retrieval_reference_unavailable_count": retrieval_reference_unavailable_count,
        "judge_target_gold_available_mismatch_count": target_gold_available_mismatch_count,
        "judge_unexpected_target_count": unexpected_target_count,
        "target_policy_fail_closed_count": target_policy_fail_closed_count,
        "target_policy_accepted_count": target_policy_accepted_count,
        "oracle_call_count": sum(
            row.get("oracle_relation", {}).get("judge_status") != "not_called"
            for row in rows
            if "oracle_relation" in row
        ),
        "retrieved_call_count": sum(
            row.get("retrieved_relation", {}).get("judge_status") != "not_called"
            for row in rows
            if "retrieved_relation" in row
        ),
        "latency_p50_ms": _percentile(latencies, 0.50),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "model_reported_latency_p50_ms": _percentile(model_latencies, 0.50),
        "model_reported_latency_p95_ms": _percentile(model_latencies, 0.95),
        "prompt_tokens": token_total("prompt_tokens"),
        "completion_tokens": token_total("completion_tokens"),
        "total_tokens": token_total("total_tokens"),
        "avg_prompt_tokens": _ratio(token_total("prompt_tokens"), len(completed)),
        "avg_completion_tokens": _ratio(token_total("completion_tokens"), len(completed)),
        "avg_total_tokens": _ratio(token_total("total_tokens"), len(completed)),
        "models": sorted(
            {str(proposal["judge_model"]) for proposal in proposals if proposal.get("judge_model")}
        ),
        "oracle": stage_summary("oracle_relation"),
        "retrieved": stage_summary("retrieved_relation"),
    }


def collect_memory_longtail_write_v2_repository_metadata(
    root: Path = Path("."),
) -> dict[str, Any]:
    """Collect bounded Git provenance for a final evaluation artifact."""

    def git(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        return result.stdout.strip()

    remote = git("remote", "get-url", "origin")
    if remote and "://" in remote and "@" in remote:
        scheme, remainder = remote.split("://", 1)
        remote = f"{scheme}://{remainder.split('@', 1)[1]}"
    short_status = git("status", "--short")
    status_lines = short_status.splitlines() if short_status else []
    return {
        "repo": remote or str(root.resolve()),
        "branch": git("branch", "--show-current") or "unknown",
        "commit_sha": git("rev-parse", "HEAD") or "unknown",
        "working_tree_status": "dirty" if status_lines else "clean",
        "working_tree_change_count": len(status_lines),
    }


def finalize_memory_longtail_write_v2_live_validation(
    full_report: dict[str, Any],
    hard_report: dict[str, Any],
    *,
    repository: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Join full and hard Live evidence into one conservative freeze decision."""

    repository_metadata = dict(
        repository or collect_memory_longtail_write_v2_repository_metadata()
    )
    full_retrieval = full_report.get("retrieval_metrics", {})
    full_relation = full_report.get("retrieved_relation_metrics", {})
    full_safety = full_report.get("safety_metrics", {})
    hard_safety = hard_report.get("safety_metrics", {})
    full_embedding = full_report.get("telemetry", {}).get("embedding", {})
    hard_embedding = hard_report.get("telemetry", {}).get("embedding", {})
    full_judge = full_report.get("telemetry", {}).get("judge", {})
    hard_judge = hard_report.get("telemetry", {}).get("judge", {})
    consistency = hard_report.get("hard_case_consistency", {})
    hard_filter = hard_report.get("hard_case_filter", {})
    full_structural = full_report.get("dataset", {}).get("structural_validation", {})
    hard_structural = hard_report.get("dataset", {}).get("structural_validation", {})

    dataset_contract = bool(
        full_report.get("dataset", {}).get("status") == "PASS"
        and hard_report.get("dataset", {}).get("status") == "PASS"
        and full_structural.get("candidate_pool_contract_status") == "PASS"
        and hard_structural.get("candidate_pool_contract_status") == "PASS"
        and int(full_report.get("case_count") or 0) == EXPECTED_CASE_COUNT
    )
    hard_scope_complete = bool(
        hard_filter.get("status") == "MATCHED"
        and set(hard_filter.get("matched_ids", [])) == set(HARD_CASE_IDS)
        and int(hard_report.get("repeat") or 0) >= 3
        and int(hard_report.get("case_count") or 0) == len(HARD_CASE_IDS)
    )
    embedding_complete = bool(
        int(full_embedding.get("failure_count") or 0) == 0
        and int(hard_embedding.get("failure_count") or 0) == 0
    )
    judge_complete = bool(
        int(full_judge.get("failure_count") or 0) == 0
        and int(hard_judge.get("failure_count") or 0) == 0
    )
    destructive_safety = bool(
        _safety_count_is_zero(full_safety)
        and _safety_count_is_zero(hard_safety)
        and float(full_safety.get("historical_event_preservation_rate") or 0.0) == 1.0
    )
    store_complete = bool(
        int(full_report.get("write_metrics", {}).get("store_application_error_count") or 0) == 0
        and int(hard_report.get("write_metrics", {}).get("store_application_error_count") or 0)
        == 0
    )
    checks = {
        "dataset_contract": dataset_contract,
        "candidate_pool_125_all_cases": dataset_contract,
        "production_embedding_complete": embedding_complete,
        "raw_retrieval_recall_at_20": _metric_meets_threshold(
            full_retrieval,
            "raw_retrieval_recall_at_20",
            0.95,
        ),
        "equivalence_aware_recall_at_20": _metric_meets_threshold(
            full_retrieval,
            "equivalence_aware_recall_at_20",
            0.95,
        ),
        "retrieved_relation_accuracy": _metric_meets_threshold(
            full_relation,
            "relation_accuracy",
            0.75,
        ),
        "retrieved_relation_macro_f1": _metric_meets_threshold(
            full_relation,
            "macro_f1",
            0.70,
        ),
        "target_set_accuracy": _metric_meets_threshold(
            full_relation,
            "target_set_accuracy",
            0.60,
        ),
        "target_micro_f1": _metric_meets_threshold(
            full_relation,
            "target_micro_f1",
            0.70,
        ),
        "semantic_judge_complete": judge_complete,
        "destructive_safety": destructive_safety,
        "store_application_complete": store_complete,
        "hard_case_scope_complete": hard_scope_complete,
        "hard_relation_consistency": _metric_meets_threshold(
            consistency,
            "relation_consistency_rate",
            0.95,
        ),
        "hard_target_consistency": _metric_meets_threshold(
            consistency,
            "target_consistency_rate",
            0.95,
        ),
        "hard_validator_consistency": _metric_meets_threshold(
            consistency,
            "validator_consistency_rate",
            1.0,
        ),
    }

    if not destructive_safety:
        status = "SAFETY_REGRESSION"
    elif not dataset_contract:
        status = "DATASET_REVIEW_REQUIRED"
    elif not checks["production_embedding_complete"] or not checks[
        "raw_retrieval_recall_at_20"
    ]:
        status = "RETRIEVAL_REMEDIATION_REQUIRED"
    elif not all(
        checks[name]
        for name in (
            "retrieved_relation_accuracy",
            "retrieved_relation_macro_f1",
            "target_set_accuracy",
            "target_micro_f1",
            "semantic_judge_complete",
            "store_application_complete",
            "hard_case_scope_complete",
            "hard_relation_consistency",
            "hard_target_consistency",
            "hard_validator_consistency",
        )
    ):
        status = "SEMANTIC_JUDGE_REMEDIATION_REQUIRED"
    else:
        status = "MEMORY_V2_FREEZE_READY"

    failed_checks = [name for name, passed in checks.items() if not passed]
    final_validation = {
        "status": status,
        "checks": checks,
        "failed_checks": failed_checks,
        "full_case_count": full_report.get("case_count"),
        "hard_case_count": hard_report.get("case_count"),
        "hard_repeat": hard_report.get("repeat"),
        "hard_case_consistency": consistency,
        "production_store_mutation_permitted": False,
        "decision_rule": (
            "safety > dataset contract > production retrieval > "
            "semantic/target/store/stability > freeze"
        ),
    }
    for report, artifact_role in (
        (full_report, "full_live_40x1"),
        (hard_report, "hard_live_8x3"),
    ):
        report["repository"] = repository_metadata
        report["final_validation"] = final_validation
        report["final_artifact_role"] = artifact_role
        report["status"] = status
        report["store_mutation_permitted"] = False
        report["production_store_mutation_permitted"] = False
    return full_report, hard_report


def render_memory_longtail_write_v2_report(report: Mapping[str, Any]) -> str:
    """Render the JSON evaluation artifact as a review-oriented Markdown report."""

    retrieval = report.get("retrieval_metrics", {})
    oracle = report.get("oracle_relation_metrics", {})
    retrieved = report.get("retrieved_relation_metrics", {})
    write = report.get("write_metrics", {})
    safety = report.get("safety_metrics", {})
    safety_coverage = report.get("safety_coverage", {})
    review_excluded = report.get("review_excluded_metrics", {})
    telemetry = report.get("telemetry", {})
    embedding_telemetry = telemetry.get("embedding", {})
    judge_telemetry = telemetry.get("judge", {})
    multi_target = report.get("multi_target_metrics", {})
    known_limitations = report.get("known_limitation_metrics", {})
    acceptance_checks = report.get("acceptance_checks", {})
    fixture_comparison = report.get("fixture_comparison")
    repository = report.get("repository", {})
    final_validation = report.get("final_validation", {})
    contradiction = report.get("contradiction_diagnostics", {}).get("retrieved", {})
    dataset = report.get("dataset", {})
    structural = dataset.get("structural_validation", {})
    collision_audit = dataset.get("collision_audit", {})
    semantic_target_contract = structural.get("semantic_target_contract", {})
    hard_filter = report.get("hard_case_filter", {})
    consistency = report.get("hard_case_consistency", {})
    parameters = report.get("parameters", {})
    if not isinstance(parameters, Mapping):
        parameters = {}
    runs = report.get("runs", [])
    if not isinstance(runs, Sequence) or isinstance(runs, (str, bytes)):
        runs = []
    comparison_scope = (
        fixture_comparison.get("scope", {})
        if isinstance(fixture_comparison, Mapping)
        else {}
    )
    consistency_lines = [
        "### Per-case consistency",
        "",
        "| Case | Relation | Target | Validator | Top-5 order | Target drift attribution |",
        "|---|---:|---:|---:|---:|---|",
    ]
    consistency_by_case = consistency.get("by_case", {})
    if isinstance(consistency_by_case, Mapping) and consistency_by_case:
        for case_key, values in consistency_by_case.items():
            consistency_lines.append(
                f"| {case_key} | {_fmt(values.get('relation_consistency_rate'))} | "
                f"{_fmt(values.get('target_consistency_rate'))} | "
                f"{_fmt(values.get('validator_consistency_rate'))} | "
                f"{_fmt(values.get('retrieval_top5_order_consistency_rate'))} | "
                f"{values.get('target_drift_attribution', '-')} |"
            )
    else:
        consistency_lines.append("| none | - | - | - | - | - |")
    lines = [
        "# Memory Long-tail Write V2 Final Live Validation",
        "",
        f"- Version: `{report.get('version', '-')}`",
        f"- Cases: `{report.get('case_count', 0)}`",
        f"- Passed: `{report.get('passed_case_count', 0)}`",
        "- Evaluated rows (including repeats): "
        f"`{report.get('evaluated_row_count', report.get('case_count', 0))}`",
        f"- Evaluation status: **{report.get('status', '-')}**",
        f"- Safety status (applied destructive writes): **{report.get('safety_status', '-')}**",
        f"- Evaluation mode: `{report.get('evaluation_mode', 'shadow_live')}`",
        f"- Dataset status: **{dataset.get('status', '-')}**",
        f"- Repeat runs: `{report.get('repeat', 1)}`",
        f"- Hard-case mode: `{report.get('hard_cases_only', False)}`",
        "- Production Store mutation permitted: `False`",
        "- Isolated InMemoryStore mutation permitted: `True`",
        f"- Repository: `{repository.get('repo', '-')}`",
        f"- Branch / commit: `{repository.get('branch', '-')}` / "
        f"`{repository.get('commit_sha', '-')}`",
        f"- Working tree: `{repository.get('working_tree_status', '-')}` "
        f"({repository.get('working_tree_change_count', '-')} changes)",
        "",
        "## Repeat / Hard-case Diagnostics",
        "",
        f"- Filter status: `{hard_filter.get('status', 'NOT_REQUESTED')}`",
        f"- Matched IDs: `{hard_filter.get('matched_ids', [])}`",
        f"- Missing IDs: `{hard_filter.get('missing_ids', [])}`",
        f"- Relation consistency: `{_fmt(consistency.get('relation_consistency_rate'))}`",
        f"- Target consistency: `{_fmt(consistency.get('target_consistency_rate'))}`",
        f"- Validator consistency: `{_fmt(consistency.get('validator_consistency_rate'))}`",
        "- Retrieval Top-5 order consistency: "
        f"`{_fmt(consistency.get('retrieval_top5_order_consistency_rate'))}`",
        f"- Fixture repeat scope: `{comparison_scope.get('fixture_repeat', '-')}`",
        f"- Live repeat scope: `{comparison_scope.get('live_repeat', '-')}`",
        "- Consistency rates are per-case mode rates across repeated runs; `1.0` means no drift.",
        "",
        *consistency_lines,
        "",
        "## Dataset Contract",
        "",
        f"- Shared bank: `{structural.get('shared_memory_count', 0)}` memories",
        f"- Overlay: `{structural.get('overlay_memory_count', 0)}` memories",
        (
            "- Candidate pools: `all shared memories + overlay`, actual sizes "
            f"`{structural.get('candidate_pool_size_counts', {})}`"
        ),
        (
            "- Candidate contract: **FIXED** at `120` shared + `5` overlay "
            "(`125` candidates per case); `shared_pools` is descriptive only."
        ),
        (
            "- Gold collision review cases: "
            f"`{report.get('dataset_review_case_count', 0)}` "
            f"{report.get('dataset_review_case_ids', [])}"
        ),
        (
            "- Collision audit: exact shared/overlay cases `"
            f"{collision_audit.get('cases_with_any_exact_shared_overlay_duplicate', 0)}`; "
            "equivalent exact cases `"
            f"{collision_audit.get('cases_with_equivalent_exact_shared_overlay_duplicate', 0)}`; "
            "unresolved exact cases `"
            f"{collision_audit.get('cases_with_unresolved_exact_shared_overlay_duplicate', 0)}`; "
            "Gold exact `"
            f"{collision_audit.get('cases_with_exact_duplicate_gold_target', 0)}`; "
            "semantic-tag cases `"
            f"{collision_audit.get('cases_with_any_shared_overlay_tag_overlap', 0)}`; "
            "Gold tag `"
            f"{collision_audit.get('cases_with_gold_target_tag_overlap', 0)}`."
        ),
        (
            "- Non-Gold collision cases remain diagnostic only: `"
            f"{collision_audit.get('non_gold_collision_case_ids', [])}`"
        ),
        (
            "- Semantic target contract: retrieval-reference-only cases `"
            f"{semantic_target_contract.get('reference_only_case_ids', [])}`; "
            "their non-target-bearing relation proposals must use an empty target set."
        ),
        "",
        "## Retrieval and Ranking",
        "",
        f"- Vector retrieval stage: **Top-{parameters.get('vector_top_k', '-')}** candidates",
        f"- Cheap ranking stage: **Top-{parameters.get('cheap_rank_top_n', '-')}** candidates "
        "(also supplied to the Semantic Judge)",
        f"- Retrieval engine: `{parameters.get('retrieval_engine', '-')}`",
        f"- Embedding input: `{parameters.get('embedding_input', '-')}`",
        f"- Embedding input detail: {parameters.get('embedding_input_detail', '-')}",
        f"- Judge query input: `{parameters.get('judge_query_input', '-')}`",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    if runs:
        lines.extend(
            [
                "",
                "### Per-run summary",
                "",
                "| Run | Cases | Passed | Failed |",
                "|---:|---:|---:|---:|",
            ]
        )
        for run in runs:
            if not isinstance(run, Mapping):
                continue
            lines.append(
                f"| {run.get('run', '-')} | {run.get('case_count', '-')} | "
                f"{run.get('passed_case_count', '-')} | {run.get('failed_case_count', '-')} |"
            )
    for name in (
        "retrieval_expected_case_count",
        "retrieval_expected_target_count",
        "retrieval_hit_at_1",
        "retrieval_hit_at_3",
        "retrieval_hit_at_5",
        "retrieval_hit_at_10",
        "retrieval_hit_at_20",
        "retrieval_recall_at_5",
        "retrieval_recall_at_10",
        "retrieval_recall_at_20",
        "raw_retrieval_recall_at_5",
        "raw_retrieval_recall_at_10",
        "raw_retrieval_recall_at_20",
        "equivalence_aware_recall_at_5",
        "equivalence_aware_recall_at_10",
        "equivalence_aware_recall_at_20",
        "mrr",
        "conditional_gold_retention_at_5",
        "gold_retention_at_5",
        "end_to_end_gold_recall_at_5",
        "target_set_recall_at_5",
        "gold_target_set_exact_at_5",
        "hard_negative_promotion_count",
        "hard_negative_promotion_rate",
        "unrelated_candidate_vector_count",
        "unrelated_candidate_ranked_count",
        "unrelated_candidate_retention_rate",
        "equivalence_group_duplicate_slot_count_at_20",
        "equivalence_group_duplicate_slot_count_at_5",
        "avg_candidate_count",
        "retrieval_latency_p50_ms",
        "retrieval_latency_p95_ms",
        "cheap_ranking_latency_p50_ms",
        "cheap_ranking_latency_p95_ms",
        "vector_ranking_latency_p50_ms",
        "vector_ranking_latency_p95_ms",
    ):
        lines.append(f"| `{name}` | {_fmt(retrieval.get(name))} |")
    lines.extend(
        [
            "",
            "Metric definitions: `raw_retrieval_recall_at_20` counts exact physical "
            "Gold IDs in vector Top-20. `equivalence_aware_recall_at_20` counts a "
            "case-local documented equivalence group as one semantic hit. "
            "`conditional_gold_retention_at_5` is ranked Top-5 Gold hits divided by "
            "Gold hits already present in vector Top-20. "
            "`end_to_end_gold_recall_at_5` is ranked Top-5 Gold hits divided by all "
            "expected retrieval references.",
        ]
    )
    lines.extend(
        [
            "",
            "## V2 Acceptance Checks",
            "",
            "| Check | Result |",
            "|---|---|",
        ]
    )
    if isinstance(acceptance_checks, Mapping) and acceptance_checks:
        for name, value in acceptance_checks.items():
            lines.append(f"| `{name}` | {'PASS' if value else 'MISS'} |")
    else:
        lines.append("| none | - |")
    collision_details = collision_audit.get("case_collisions", [])
    lines.extend(
        [
            "",
            "## Collision Details",
            "",
            "| Case | Gold exact | Gold tag | Other exact/tag overlap |",
            "|---|---|---|---|",
        ]
    )
    if collision_details:
        for detail in collision_details:
            gold_exact = detail.get("gold_exact_text_collisions", [])
            gold_tags = detail.get("gold_semantic_tag_overlaps", [])
            other_exact = [
                item for item in detail.get("exact_text_collisions", []) if item not in gold_exact
            ]
            other_tags = [
                item for item in detail.get("semantic_tag_overlaps", []) if item not in gold_tags
            ]
            lines.append(
                f"| {detail.get('case_id')} | {_fmt(gold_exact)} | {_fmt(gold_tags)} | "
                f"{_fmt({'exact': other_exact, 'tag': other_tags})} |"
            )
    else:
        lines.append("| none | - | - | - |")
    lines.extend(
        [
            "",
            "## Oracle vs Retrieved Relation",
            "",
            "| Metric | Oracle candidates | Retrieved Top-5 |",
            "|---|---:|---:|",
        ]
    )
    for name in (
        "relation_accuracy",
        "macro_f1",
        "update_precision",
        "update_recall",
        "target_exact_match",
        "target_set_accuracy",
        "target_memory_accuracy",
        "target_micro_precision",
        "target_memory_precision",
        "target_micro_recall",
        "target_micro_f1",
        "judge_failure_count",
    ):
        lines.append(f"| `{name}` | {_fmt(oracle.get(name))} | {_fmt(retrieved.get(name))} |")
    lines.extend(
        [
            "",
            "## Retrieved Relation PRF",
            "",
            "| Relation | Support | Precision | Recall | F1 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for relation, values in retrieved.get("per_relation", {}).items():
        lines.append(
            f"| `{relation}` | {values['support']} | {_fmt(values['precision'])} | "
            f"{_fmt(values['recall'])} | {_fmt(values['f1'])} |"
        )
    lines.extend(
        [
            "",
            "## Contradiction Diagnostics",
            "",
            f"- Support: `{contradiction.get('support', 0)}`",
            f"- Precision / Recall / F1: `{_fmt(contradiction.get('precision'))}` / "
            f"`{_fmt(contradiction.get('recall'))}` / `{_fmt(contradiction.get('f1'))}`",
            "",
            "| Case | Incoming | Candidates | Gold | Actual | Confidence | Validator |",
            "|---|---|---|---|---|---:|---|",
        ]
    )
    contradiction_cases = contradiction.get("cases", [])
    if contradiction_cases:
        for item in contradiction_cases:
            candidate_texts = [
                candidate.get("text") for candidate in item.get("candidates", [])
            ]
            lines.append(
                f"| {item.get('case_id')} | {item.get('incoming')} | {candidate_texts} | "
                f"{item.get('gold_relation')} | {item.get('actual_relation')} | "
                f"{_fmt(item.get('confidence'))} | "
                f"pass={item.get('validator_pass')}, would_update={item.get('would_update')} |"
            )
    else:
        lines.append("| none | - | - | - | - | - | - |")
    lines.extend(
        [
            "",
            "## Review-excluded Metrics",
            "",
            f"- Cases included: `{review_excluded.get('case_count', 0)}`",
            f"- Evaluated rows included (including repeats): `"
            f"{review_excluded.get('evaluated_row_count', review_excluded.get('case_count', 0))}`",
            f"- Excluded Gold-collision cases: `{review_excluded.get('excluded_case_ids', [])}`",
            "",
            "| Metric | Value |",
            "|---|---:|",
        ]
    )
    review_retrieval = review_excluded.get("retrieval", {})
    review_relation = review_excluded.get("retrieved_relation", {})
    review_safety = review_excluded.get("safety", {})
    for name, value in (
        ("retrieval_recall_at_20", review_retrieval.get("retrieval_recall_at_20")),
        ("gold_retention_at_5", review_retrieval.get("gold_retention_at_5")),
        ("relation_accuracy", review_relation.get("relation_accuracy")),
        ("target_set_accuracy", review_relation.get("target_set_accuracy")),
        (
            "destructive_safety_violation_count",
            review_safety.get("destructive_safety_violation_count"),
        ),
        (
            "proposal_plus_write_safety_diagnostic_count",
            review_safety.get("proposal_plus_write_safety_diagnostic_count"),
        ),
    ):
        lines.append(f"| `{name}` | {_fmt(value)} |")
    lines.extend(
        [
            "",
            "## Write and Store",
            "",
            "| Metric | Value |",
            "|---|---:|",
        ]
    )
    for name, value in write.items():
        lines.append(f"| `{name}` | {_fmt(value)} |")
    lines.extend(
        [
            "",
            "## Policy Boundaries",
            "",
            "| Boundary | Value |",
            "|---|---:|",
            f"| `multi_target_proposal_count` | {_fmt(multi_target.get('proposal_count'))} |",
            "| `retrieved_multi_target_proposal_count` | "
            f"{_fmt(multi_target.get('retrieved_proposal_count'))} |",
            "| `expected_multi_target_case_count` | "
            f"{_fmt(multi_target.get('expected_multi_target_case_count'))} |",
            "| `exact_expected_multi_target_proposal_count` | "
            f"{_fmt(multi_target.get('exact_expected_multi_target_proposal_count'))} |",
            "| `overbroad_multi_target_proposal_count` | "
            f"{_fmt(multi_target.get('overbroad_multi_target_proposal_count'))} |",
            f"| `policy_boundary_count` | {_fmt(multi_target.get('policy_boundary_count'))} |",
            "| `multi_target_validator_denied_count` | "
            f"{_fmt(multi_target.get('validator_denied_count'))} |",
            "| `destructive_multi_target_write_count` | "
            f"{_fmt(multi_target.get('destructive_multi_target_write_count'))} |",
            f"| `multi_target_status` | {_fmt(multi_target.get('status'))} |",
            f"| `action_intent_update` | {_fmt(known_limitations.get('action_intent_update'))} |",
            "",
            "## Safety",
            "",
            "| Metric | Value |",
            "|---|---:|",
        ]
    )
    for name, value in safety.items():
        lines.append(f"| `{name}` | {_fmt(value)} |")
    lines.extend(
        [
            "",
            "`destructive_safety_violation_count` counts only applied destructive "
            "Store violations. `proposal_safety_violation_count` and "
            "`proposal_plus_write_safety_diagnostic_count` describe blocked or "
            "proposed-link diagnostics separately.",
        ]
    )
    lines.extend(
        [
            "",
            "## Safety Coverage",
            "",
            "| Invariant | Status | Evidence / limitation |",
            "|---|---|---|",
        ]
    )
    for name, value in safety_coverage.items():
        if isinstance(value, Mapping):
            lines.append(
                f"| `{name}` | `{value.get('status', '-')}` | {value.get('reason', '-')} |"
            )
        else:
            lines.append(f"| `{name}` | - | {value} |")
    lines.extend(
        [
            "",
            "## Model and Evaluation Telemetry",
            "",
            "| Metric | Value |",
            "|---|---:|",
        ]
    )
    telemetry_values = (
        ("embedding_model", embedding_telemetry.get("model")),
        ("embedding_model_version", embedding_telemetry.get("model_version")),
        ("embedding_dimension", embedding_telemetry.get("dimension")),
        ("embedding_document_call_count", embedding_telemetry.get("document_call_count")),
        ("embedding_query_call_count", embedding_telemetry.get("query_call_count")),
        ("embedding_failure_count", embedding_telemetry.get("failure_count")),
        (
            "embedding_document_failure_count",
            embedding_telemetry.get("document_failure_count"),
        ),
        ("embedding_query_failure_count", embedding_telemetry.get("query_failure_count")),
        ("embedding_document_latency_p50_ms", embedding_telemetry.get("document_latency_p50_ms")),
        ("embedding_document_latency_p95_ms", embedding_telemetry.get("document_latency_p95_ms")),
        ("embedding_query_latency_p50_ms", embedding_telemetry.get("query_latency_p50_ms")),
        ("embedding_query_latency_p95_ms", embedding_telemetry.get("query_latency_p95_ms")),
        ("embedding_query_latency_total_ms", embedding_telemetry.get("query_latency_total_ms")),
        ("judge_models", judge_telemetry.get("models")),
        ("judge_call_count", judge_telemetry.get("call_count")),
        ("judge_evaluated_count", judge_telemetry.get("judge_evaluated_count")),
        ("judge_failure_count", judge_telemetry.get("failure_count")),
        (
            "judge_transport_failure_count",
            judge_telemetry.get("judge_transport_failure_count"),
        ),
        ("judge_parse_failure_count", judge_telemetry.get("judge_parse_failure_count")),
        ("judge_relation_mismatch_count", judge_telemetry.get("judge_relation_mismatch_count")),
        ("judge_target_mismatch_count", judge_telemetry.get("judge_target_mismatch_count")),
        (
            "judge_retrieval_reference_unavailable_count",
            judge_telemetry.get("judge_retrieval_reference_unavailable_count"),
        ),
        (
            "judge_target_candidate_unavailable_count",
            judge_telemetry.get("judge_target_candidate_unavailable_count"),
        ),
        (
            "judge_target_gold_available_mismatch_count",
            judge_telemetry.get("judge_target_gold_available_mismatch_count"),
        ),
        ("judge_unexpected_target_count", judge_telemetry.get("judge_unexpected_target_count")),
        (
            "target_policy_accepted_count",
            judge_telemetry.get("target_policy_accepted_count"),
        ),
        (
            "target_policy_fail_closed_count",
            judge_telemetry.get("target_policy_fail_closed_count"),
        ),
        ("judge_latency_p50_ms", judge_telemetry.get("latency_p50_ms")),
        ("judge_latency_p95_ms", judge_telemetry.get("latency_p95_ms")),
        ("judge_prompt_tokens", judge_telemetry.get("prompt_tokens")),
        ("judge_completion_tokens", judge_telemetry.get("completion_tokens")),
        ("judge_total_tokens", judge_telemetry.get("total_tokens")),
        ("judge_avg_prompt_tokens", judge_telemetry.get("avg_prompt_tokens")),
        ("judge_avg_completion_tokens", judge_telemetry.get("avg_completion_tokens")),
        ("judge_avg_total_tokens", judge_telemetry.get("avg_total_tokens")),
        ("oracle_judge", judge_telemetry.get("oracle")),
        ("retrieved_judge", judge_telemetry.get("retrieved")),
        (
            "estimated_cost_per_100_writes",
            telemetry.get("estimated_cost_per_100_writes"),
        ),
    )
    for name, value in telemetry_values:
        lines.append(f"| `{name}` | {_fmt(value)} |")
    lines.extend(
        [
            "",
            "## Failure Attribution",
            "",
            "Counts below are evaluated-row counts; in repeat mode one case may "
            "contribute more than one row.",
            "",
            "| Primary stage | Count |",
            "|---|---:|",
        ]
    )
    primary = report.get("failure_attribution", {}).get("primary", {})
    if primary:
        for name, value in primary.items():
            lines.append(f"| `{name}` | {value} |")
    else:
        lines.append("| none | 0 |")
    lines.extend(
        [
            "",
            "## Failed Cases",
            "",
            "| Run | Case | Slice | Primary | Secondary | Expected | Actual | Targets | Action |",
            "|---:|---|---|---|---|---|---|---|---|",
        ]
    )
    failures = [row for row in report.get("rows", []) if not row.get("passed")]
    if not failures:
        lines.append("| - | none | - | - | - | - | - | - | - |")
    for row in failures:
        proposal = row.get("retrieved_relation", {}).get("proposal", {})
        secondary_stages = row.get("secondary_failure_stages", [])
        lines.append(
            f"| {row.get('run_index', '-')} | {row.get('case_id')} | {row.get('slice')} | "
            f"{row.get('primary_failure_stage') or '-'} | "
            f"{secondary_stages or '-'} | "
            f"{row.get('expected_relation')} | {proposal.get('relation', '-')} | "
            f"{proposal.get('target_memory_ids', [])} | "
            f"{row.get('store', {}).get('actual_write_action', '-')} |"
        )
    if isinstance(fixture_comparison, Mapping):
        lines.extend(
            [
                "",
                "## Fixture vs Live",
                "",
                f"- Comparison status: `{fixture_comparison.get('status', '-')}`",
                f"- Methodology: {fixture_comparison.get('methodology', '-')}",
                f"- Fixture scope: Top-{comparison_scope.get('fixture_vector_top_k', '-')} → "
                f"Top-{comparison_scope.get('fixture_cheap_rank_top_n', '-')}",
                f"- Live scope: Top-{comparison_scope.get('live_vector_top_k', '-')} → "
                f"Top-{comparison_scope.get('live_cheap_rank_top_n', '-')}" ,
                "",
                "| Metric | Fixture | Live |",
                "|---|---:|---:|",
            ]
        )
        comparison_metrics = fixture_comparison.get("metrics", {})
        if isinstance(comparison_metrics, Mapping) and comparison_metrics:
            for name, values in comparison_metrics.items():
                if not isinstance(values, Mapping):
                    continue
                lines.append(
                    f"| `{name}` | {_fmt(values.get('fixture'))} | {_fmt(values.get('live'))} |"
                )
        else:
            lines.append("| none | - | - |")
    if isinstance(final_validation, Mapping) and final_validation:
        lines.extend(
            [
                "",
                "## Final Acceptance",
                "",
                f"- Final status: **{final_validation.get('status', '-')}**",
                f"- Failed checks: `{final_validation.get('failed_checks', [])}`",
                "",
                "| Check | Result |",
                "|---|---|",
            ]
        )
        for name, passed in final_validation.get("checks", {}).items():
            lines.append(f"| `{name}` | {'PASS' if passed else 'MISS'} |")
        lines.extend(_final_live_answers(report))
    lines.extend(
        [
            "",
            "## Governance Notes",
            "",
            "Vector similarity only recalls candidates; it never authorizes a write. ",
            "The Semantic Judge proposes, the production validator authorizes, and only ",
            "a case-local InMemoryStore receives a batch. Multi-target destructive writes ",
            "remain unsupported and fail closed. The production validator may also deny ",
            "action_intent UPDATE proposals because that role is outside its destructive ",
            "role policy. Benchmark semantic tags are used only for collision and ",
            "hard-negative diagnostics, never ranking or Judge input. Store seed identity ",
            "collapse counts are auxiliary; the loader collision audit is authoritative.",
            " Raw false-link proposals are reported separately from actual destructive ",
            "writes; a validator-denied proposal does not count as an applied Store safety ",
            "violation. Proposal-level safety issues are reported separately from "
            "applied destructive violations.",
            " The production SemanticRelationProposal exposes a bounded `reason` rather "
            "than a separate `reason_code`; this evaluator records the existing contract "
            "without expanding the relation ontology.",
            "",
        ]
    )
    return "\n".join(lines)


def _final_live_answers(report: Mapping[str, Any]) -> list[str]:
    retrieval = report.get("retrieval_metrics", {})
    oracle = report.get("oracle_relation_metrics", {})
    retrieved = report.get("retrieved_relation_metrics", {})
    judge = report.get("telemetry", {}).get("judge", {})
    retrieved_judge = judge.get("retrieved", {}) if isinstance(judge, Mapping) else {}
    safety = report.get("safety_metrics", {})
    write = report.get("write_metrics", {})
    multi_target = report.get("multi_target_metrics", {})
    consistency = report.get("final_validation", {}).get("hard_case_consistency", {})
    contradiction_confusion = retrieved.get("relation_confusion", {}).get(
        f"{ClaimRelation.CONTRADICTION.value}->{ClaimRelation.UNCERTAIN.value}",
        0,
    )
    oracle_accuracy = float(oracle.get("relation_accuracy") or 0.0)
    retrieved_accuracy = float(retrieved.get("relation_accuracy") or 0.0)
    target_overbroad = int(retrieved_judge.get("unexpected_target_count") or 0)
    store_errors = int(write.get("store_application_error_count") or 0)
    status = report.get("final_validation", {}).get("status", report.get("status"))
    return [
        "",
        "## Final Questions",
        "",
        f"1. Final Dataset contract: **{report.get('dataset', {}).get('status', '-')}**.",
        "2. Production raw Recall@20: "
        f"`{_fmt(retrieval.get('raw_retrieval_recall_at_20'))}`.",
        "3. Equivalence-aware Recall@20: "
        f"`{_fmt(retrieval.get('equivalence_aware_recall_at_20'))}`.",
        "4. Cheap ranking retention: conditional Top-20 -> Top-5 "
        f"`{_fmt(retrieval.get('conditional_gold_retention_at_5'))}`; "
        "end-to-end Top-5 recall "
        f"`{_fmt(retrieval.get('end_to_end_gold_recall_at_5'))}`.",
        "5. Oracle vs Retrieved relation accuracy: "
        f"`{_fmt(oracle_accuracy)}` vs `{_fmt(retrieved_accuracy)}` "
        f"(delta `{_fmt(round(oracle_accuracy - retrieved_accuracy, 4))}`).",
        "6. CONTRADICTION -> UNCERTAIN regressions: "
        f"`{contradiction_confusion}`.",
        f"7. Unexpected/over-broad target selections: `{target_overbroad}`.",
        "8. Retrieved multi-target proposals / exact policy-boundary proposals / "
        f"over-broad proposals: `{multi_target.get('retrieved_proposal_count', 0)}` / "
        f"`{multi_target.get('policy_boundary_count', 0)}` / "
        f"`{multi_target.get('overbroad_multi_target_proposal_count', 0)}`; policy "
        f"`{multi_target.get('policy_boundary', 'NOT_OBSERVED')}`.",
        "9. Store application failures: "
        f"`{store_errors}`; these are reported separately from upstream errors.",
        "10. Applied destructive safety violations: "
        f"`{safety.get('destructive_safety_violation_count', '-')}`.",
        "11. Hard-case relation / target / validator consistency: "
        f"`{_fmt(consistency.get('relation_consistency_rate'))}` / "
        f"`{_fmt(consistency.get('target_consistency_rate'))}` / "
        f"`{_fmt(consistency.get('validator_consistency_rate'))}`; Top-5 order "
        f"`{_fmt(consistency.get('retrieval_top5_order_consistency_rate'))}`.",
        "12. Retrieved Judge average prompt / completion tokens per call: "
        f"`{_fmt(retrieved_judge.get('avg_prompt_tokens'))}` / "
        f"`{_fmt(retrieved_judge.get('avg_completion_tokens'))}`.",
        f"13. Current status: **{status}**.",
        "",
    ]


def _candidate_from_row(
    row: Mapping[str, Any],
    *,
    reference_time: datetime,
) -> MemoryCandidate:
    text = str(row["text"])
    status = MemoryStatus(row.get("status", MemoryStatus.CONFIRMED))
    perspective = MemoryPerspective(row.get("perspective", MemoryPerspective.USER_REPORTED))
    explicit = perspective == MemoryPerspective.USER_REPORTED
    kind = MemoryKind(row["kind"])
    # ``reference_time`` is intentionally not temporal evidence.  Retain it in
    # this helper's signature for call-site compatibility, but benchmark claims
    # must only carry fields explicitly present in the source row.
    temporal = _temporal_envelope(row)
    return MemoryCandidate(
        kind=kind,
        subject=str(row["subject"]),
        summary=text,
        original_text=text,
        evidence_spans=[text],
        **temporal,
        perspective=perspective,
        confidence=0.95 if explicit else 0.65,
        payload={"object": text},
        predicate_type=PredicateType.CUSTOM,
        explicitness=(
            EvidenceExplicitness.EXPLICIT if explicit else EvidenceExplicitness.WEAKLY_INFERRED
        ),
        requires_inference=not explicit,
        admission_score=0.95 if status == MemoryStatus.CONFIRMED else 0.5,
        admission_decision=(
            AdmissionDecision.CONFIRM
            if status == MemoryStatus.CONFIRMED
            else AdmissionDecision.PROPOSE
        ),
        prompt_version=REPORT_VERSION,
        extractor_model="benchmark-structured-envelope",
    )


def _temporal_envelope(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return only temporal evidence explicitly supplied by a benchmark row.

    ``reference_time`` and natural-language cues are evaluation metadata, not
    evidence asserted by the claim. Synthesizing a point or interval from
    either source can make the production validator report temporal evidence
    that the benchmark never supplied. Missing values therefore stay at the
    domain's neutral ``UNKNOWN``/``None`` values.
    """

    envelope: dict[str, Any] = {
        "time_kind": TimeKind.UNKNOWN,
        "occurred_at": None,
        "period_start": None,
        "period_end": None,
        "expires_at": None,
        "temporal_precision": TemporalPrecision.UNKNOWN,
    }
    if row.get("time_kind") is not None:
        envelope["time_kind"] = TimeKind(row["time_kind"])
    if row.get("temporal_precision") is not None:
        envelope["temporal_precision"] = TemporalPrecision(row["temporal_precision"])
    for field in ("occurred_at", "period_start", "period_end", "expires_at"):
        if field in row:
            # Leave ISO parsing to MemoryCandidate's domain model. This keeps
            # the evaluator faithful to the source value without fabrication.
            envelope[field] = row[field]
    return envelope


def _memory_item_from_row(
    row: Mapping[str, Any],
    *,
    user_id: str,
    relationship_id: str,
    reference_time: datetime,
) -> MemoryItem:
    candidate = _candidate_from_row(row, reference_time=reference_time)
    return MemoryItem(
        **candidate.model_dump(),
        id=str(row["memory_id"]),
        user_id=user_id,
        relationship_id=relationship_id,
        status=MemoryStatus(row.get("status", MemoryStatus.CONFIRMED)),
        source_message_id=f"benchmark-{row['memory_id']}",
        created_at=reference_time,
        updated_at=reference_time,
        last_seen_at=reference_time,
        dedupe_key=f"benchmark:{row['memory_id']}",
    )


def _candidate_for_same_merge(
    incoming: MemoryCandidate,
    target: MemoryItem,
) -> MemoryCandidate:
    """Project a validated SAME proposal onto the target's store identity.

    V2 intentionally supplies natural-language envelopes rather than extractor
    predicates. Preserve the incoming text/evidence while borrowing only the
    validated target's identity fields so the isolated Store can exercise its
    real merge path instead of creating a synthetic duplicate.
    """

    return incoming.model_copy(
        update={
            "kind": target.kind,
            "subject": target.subject,
            "payload": dict(target.payload),
            "raw_predicate": target.raw_predicate,
            "predicate_type": target.predicate_type,
            "canonical_predicate": target.canonical_predicate,
            "custom_predicate": target.custom_predicate,
            "state_dimension": target.state_dimension,
            "state_value": target.state_value,
            "time_kind": target.time_kind,
            "occurred_at": target.occurred_at,
            "period_start": target.period_start,
            "period_end": target.period_end,
            "temporal_precision": target.temporal_precision,
            "expires_at": target.expires_at,
        }
    )


def _gold_collision_alias_ids(
    collision: Mapping[str, Any] | None,
    *,
    expected_targets: set[str],
    exact_only: bool,
) -> set[str]:
    if not collision:
        return set()
    # Semantic-tag overlap is not identity equivalence.  Keep this helper
    # restricted to explicitly documented exact aliases even when callers ask
    # for the broader collision view.
    del exact_only
    names = ("equivalent_gold_exact_text_collisions",)
    aliases: set[str] = set()
    for name in names:
        for detail in collision.get(name, []):
            if detail.get("overlay_memory_id") in expected_targets:
                aliases.update(str(item) for item in detail.get("shared_memory_ids", []))
    return aliases


def _collision_caused_identity_mismatch(
    *,
    case: Mapping[str, Any],
    collision: Mapping[str, Any] | None,
    retrieval: Mapping[str, Any],
    relation_stage: Mapping[str, Any],
    store_result: Mapping[str, Any],
) -> bool:
    """Return true only when a documented Gold alias explains an observed failure."""

    expected = set(case["expected_target_ids"])
    aliases = _gold_collision_alias_ids(
        collision,
        expected_targets=expected,
        exact_only=False,
    )
    if not aliases:
        return False
    vector_ids = {str(row["memory_id"]) for row in retrieval.get("vector", [])}
    ranked_ids = {str(row["memory_id"]) for row in retrieval.get("ranked", [])}
    proposed_ids = {
        str(item) for item in relation_stage.get("proposal", {}).get("target_memory_ids", [])
    }
    retrieval_alias_substitution = bool(
        (aliases & vector_ids and not expected <= vector_ids)
        or (aliases & ranked_ids and not expected <= ranked_ids)
    )
    target_alias_substitution = bool(proposed_ids & aliases and proposed_ids != expected)
    # ``seed_identity_collision_count`` is only an observed Store-side
    # collapse; it is not the dataset collision source of truth (planned-event
    # identities and normalizer behavior can make that count non-deterministic).
    # Attribute a dataset collision only when a documented Gold alias actually
    # collapsed onto the same isolated Store row and the contract then failed.
    fixture_to_actual = store_result.get("fixture_to_actual_ids", {})
    actual_ids_by_fixture: dict[str, set[str]] = defaultdict(set)
    for fixture_id, actual_id in fixture_to_actual.items():
        actual_ids_by_fixture[str(actual_id)].add(str(fixture_id))
    collapsed_fixture_ids = {
        fixture_id
        for fixture_ids in actual_ids_by_fixture.values()
        if len(fixture_ids) > 1
        for fixture_id in fixture_ids
    }
    store_alias_collapse = bool(
        aliases & collapsed_fixture_ids and not all(store_result.get("checks", {}).values())
    )
    return retrieval_alias_substitution or target_alias_substitution or store_alias_collapse


def _store_rows(
    items: list[MemoryItem],
    fixture_to_actual: Mapping[str, str],
) -> list[dict[str, Any]]:
    fixture_ids_by_actual: dict[str, list[str]] = defaultdict(list)
    for fixture_id, actual_id in fixture_to_actual.items():
        fixture_ids_by_actual[actual_id].append(fixture_id)
    return [
        {
            "id": item.id,
            "fixture_ids": sorted(fixture_ids_by_actual.get(item.id, [])),
            "status": item.status.value,
            "source_message_id": item.source_message_id,
            "kind": item.kind.value,
            "subject": item.subject,
            "summary": item.summary,
            "predicate_type": item.predicate_type.value,
            "canonical_predicate": item.canonical_predicate,
            "custom_predicate": item.custom_predicate,
            "supersedes_id": item.supersedes_id,
        }
        for item in items
    ]


def _validated_vectors(
    raw_vectors: object,
    *,
    expected_count: int,
) -> list[list[float]]:
    if not isinstance(raw_vectors, Sequence) or isinstance(raw_vectors, (str, bytes)):
        raise ValueError("embedding provider returned a non-sequence")
    if len(raw_vectors) != expected_count:
        raise ValueError(
            f"embedding provider returned {len(raw_vectors)} vectors for {expected_count} texts"
        )
    vectors = [_coerce_vector(vector) for vector in raw_vectors]
    dimensions = {len(vector) for vector in vectors}
    if len(dimensions) > 1:
        raise ValueError("embedding provider returned mixed dimensions")
    return vectors


def _validated_query_vector(
    vector: object,
    *,
    expected_dimension: object,
) -> list[float]:
    result = _coerce_vector(vector)
    if isinstance(expected_dimension, int) and len(result) != expected_dimension:
        raise ValueError("query and document embedding dimensions differ")
    return result


def _coerce_vector(value: object) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("embedding vector must be a sequence")
    vector = [float(item) for item in value]
    if not vector or any(not math.isfinite(item) for item in vector):
        raise ValueError("embedding vector must contain finite values")
    return vector


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("cosine vectors must have the same non-zero dimension")
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if denominator == 0:
        return 0.0
    return max(
        min(
            sum(a * b for a, b in zip(left, right, strict=True)) / denominator,
            1.0,
        ),
        -1.0,
    )


def _subject_compatibility(left: str, right: str) -> float:
    return float(_subject_key(left) == _subject_key(right))


def _subject_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    aliases = {
        "she": "partner",
        "he": "partner",
        "ta": "partner",
        "relationship_partner": "partner",
        "partner": "partner",
        "relationship": "relationship",
        "couple": "relationship",
        "we": "relationship",
        "user": "user",
    }
    return aliases.get(normalized, normalized)


def _kind_compatibility(left: MemoryKind, right: MemoryKind) -> float:
    if left == right:
        return 1.0
    if {left, right} <= {
        MemoryKind.INTERACTION_EVENT,
        MemoryKind.INTERACTION_PATTERN,
    }:
        return 0.7
    if {left, right} <= {MemoryKind.PLANNED_EVENT, MemoryKind.ACTION_INTENT}:
        return 0.55
    if {left, right} <= {MemoryKind.PREFERENCE, MemoryKind.STABLE_FACT}:
        return 0.45
    return 0.15


def _temporal_compatibility(left: MemoryKind, right: MemoryKind) -> float:
    if left == right:
        return 1.0
    if MemoryKind.INTERACTION_EVENT in {left, right} and MemoryKind.INTERACTION_PATTERN in {
        left,
        right,
    }:
        return 0.75
    if {left, right} <= {MemoryKind.PLANNED_EVENT, MemoryKind.ACTION_INTENT}:
        return 0.7
    return 0.5


def _oracle_sort_key(memory_id: str, case: Mapping[str, Any]) -> tuple[int, int]:
    expected = list(case["expected_target_ids"])
    overlay = [row["memory_id"] for row in case["overlay"]]
    return (
        0 if memory_id in expected else 1,
        overlay.index(memory_id),
    )


def _hard_negative_promoted(
    retrieval: Mapping[str, Any],
    *,
    expected_targets: set[str],
    hard_negative_ids: set[str],
) -> bool:
    del expected_targets
    return any(
        str(candidate.get("memory_id")) in hard_negative_ids
        and int(candidate.get("rank_after") or candidate.get("rank") or 0)
        < int(candidate.get("rank_before") or candidate.get("vector_rank") or 0)
        for candidate in retrieval.get("ranked", [])
        if isinstance(candidate, Mapping)
    )


def _fail_closed_proposal(reason: str) -> SemanticRelationProposal:
    return SemanticRelationProposal(
        relation=ClaimRelation.UNCERTAIN,
        target_memory_ids=[],
        same_semantic_dimension=False,
        confidence=0,
        reason=reason,
    )


def _error_row(
    case: Mapping[str, Any],
    exc: Exception,
    collision: Mapping[str, Any] | None,
) -> dict[str, Any]:
    primary, secondary = _classify_evaluator_error(exc)
    retrieval_candidates = _expected_retrieval_candidate_ids(case)
    semantic_targets = _expected_semantic_target_ids(case)
    return {
        "case_id": case.get("case_id"),
        "slice": case.get("slice"),
        "contract_status": case.get("contract_status"),
        "incoming_text": (case.get("incoming") or {}).get("text"),
        "incoming_kind": (case.get("incoming") or {}).get("kind"),
        "incoming_subject": (case.get("incoming") or {}).get("subject"),
        "incoming_status": (case.get("incoming") or {}).get("status"),
        "expected_relation": case.get("expected_relation"),
        "expected_target_ids": retrieval_candidates,
        "expected_retrieval_candidate_ids": retrieval_candidates,
        "expected_semantic_target_ids": semantic_targets,
        "target_contract": (
            "retrieval_reference_only"
            if retrieval_candidates and not semantic_targets
            else "semantic_targets"
        ),
        "dataset_collision": dict(collision) if collision else None,
        "dataset_review_required": _collision_requires_review(collision),
        "primary_failure_stage": primary,
        "secondary_failure_stages": secondary,
        "error_type": type(exc).__name__,
        "error": f"{type(exc).__name__}: {exc}",
        "passed": False,
    }


def _classify_evaluator_error(exc: BaseException) -> tuple[str, list[str]]:
    """Map an evaluator exception to a truthful primary stage.

    A provider failure is not a retrieval *miss*: no ranking decision was made
    at all.  Keep the broad stage and the concrete subsystem visible so report
    aggregation cannot hide infrastructure failures as semantic misses.
    """

    text = f"{type(exc).__name__} {exc}".casefold()
    if any(
        marker in text
        for marker in (
            "embedding",
            "vector",
            "dimension",
            "document embedding",
            "query embedding",
        )
    ):
        return "RETRIEVAL_ERROR", ["EMBEDDING_ERROR"]
    if any(marker in text for marker in ("judge", "semantic relation", "proposal")):
        transport_markers = (
            "timeout",
            "timed out",
            "connection",
            "transport",
            "rate limit",
            "429",
            "502",
            "503",
            "504",
            "http",
            "api error",
        )
        if any(marker in text for marker in transport_markers):
            return "MODEL_TRANSPORT_ERROR", []
        return "MODEL_PARSE_ERROR", []
    if "validator" in text or "validation" in text:
        return "VALIDATOR_ERROR", []
    if "admission" in text:
        return "ADMISSION_ERROR", []
    if "normalization" in text or "normalize" in text:
        return "NORMALIZATION_ERROR", []
    if "store" in text or "memory batch" in text:
        return "STORE_APPLICATION_ERROR", []
    if "normal" in text or "candidate" in text or "schema" in text:
        return "EVALUATOR_ERROR", ["INPUT_OR_SCHEMA_ERROR"]
    return "EVALUATOR_ERROR", []


def _evaluation_status(
    retrieval: Mapping[str, Any],
    relation: Mapping[str, Any],
    safety: Mapping[str, Any],
    *,
    dataset_status: str,
    evaluation_mode: str = "shadow_injected",
    embedding_failure_count: int = 0,
    hard_case_consistency: Mapping[str, Any] | None = None,
    hard_case_gate_required: bool = False,
) -> str:
    if dataset_status != "PASS":
        return "DATASET_REVIEW_REQUIRED"
    # Safety is a hard gate.  Missing, null, malformed, or non-zero counts
    # must never be interpreted as a clean run merely because a legacy field
    # exists alongside the canonical field.
    if not _safety_count_is_zero(safety):
        return "SAFETY_REGRESSION"
    if evaluation_mode == "shadow_injected":
        # Preserve the historical fixture/injected evaluator contract. Final
        # live artifacts use ``_final_live_status`` below and never infer a
        # production freeze from this baseline status.
        if all(_legacy_evaluation_status_checks(retrieval, relation).values()):
            return "V2_STAGE_GOALS_MET"
        return "V2_BASELINE_REQUIRES_REVIEW"
    if embedding_failure_count:
        return "RETRIEVAL_REMEDIATION_REQUIRED"
    checks = _evaluation_status_checks(retrieval, relation)
    if not checks["retrieval_recall_at_20"]:
        return "RETRIEVAL_REMEDIATION_REQUIRED"
    relation_checks = {
        key: value
        for key, value in checks.items()
        if key not in {"retrieval_recall_at_20"}
    }
    if not all(relation_checks.values()):
        return "SEMANTIC_JUDGE_REMEDIATION_REQUIRED"
    if hard_case_gate_required:
        consistency = hard_case_consistency or {}
        if any(
            float(consistency.get(name) or 0.0) < 0.95
            for name in (
                "relation_consistency_rate",
                "target_consistency_rate",
                "validator_consistency_rate",
            )
        ):
            return "SEMANTIC_JUDGE_REMEDIATION_REQUIRED"
    return "MEMORY_V2_FREEZE_READY"


def _legacy_evaluation_status_checks(
    retrieval: Mapping[str, Any], relation: Mapping[str, Any]
) -> dict[str, bool]:
    return {
        "retrieval_recall_at_20": _metric_meets_threshold(
            retrieval, "retrieval_recall_at_20", 0.95
        ),
        "gold_retention_at_5": _metric_meets_threshold(retrieval, "gold_retention_at_5", 0.90),
        "relation_accuracy": _metric_meets_threshold(relation, "relation_accuracy", 0.75),
        "relation_macro_f1": _metric_meets_threshold(relation, "macro_f1", 0.70),
        "target_set_accuracy": _metric_meets_threshold(
            relation, "target_set_accuracy", 0.60
        ),
        "target_micro_f1": _metric_meets_threshold(relation, "target_micro_f1", 0.70),
    }


def _evaluation_status_checks(
    retrieval: Mapping[str, Any],
    relation: Mapping[str, Any],
) -> dict[str, bool]:
    """Return the frozen V2 quality gates used by ``_evaluation_status``.

    These are deliberately evaluated against the retrieved Top-K relation
    metrics, never the oracle-candidate metrics.  Keeping the checks explicit
    also makes a report's status auditable when one quality dimension misses.
    """

    return {
        "retrieval_recall_at_20": _metric_meets_threshold(
            retrieval, "retrieval_recall_at_20", 0.95
        ),
        "relation_accuracy": _metric_meets_threshold(relation, "relation_accuracy", 0.75),
        "relation_macro_f1": _metric_meets_threshold(relation, "macro_f1", 0.70),
        "target_set_accuracy": _metric_meets_threshold(
            relation, "target_set_accuracy", 0.60
        ),
        "target_micro_f1": _metric_meets_threshold(relation, "target_micro_f1", 0.70),
    }


def _metric_meets_threshold(
    metrics: Mapping[str, Any],
    name: str,
    threshold: float,
) -> bool:
    """Return a conservative boolean for a numeric quality metric."""

    value = metrics.get(name)
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(numeric) and numeric >= threshold


def _safety_count_is_zero(safety: Mapping[str, Any]) -> bool:
    """Check the canonical/legacy safety count without truthiness ambiguity."""

    names = (
        "actual_destructive_write_violation_count",
        "destructive_safety_violation_count",
    )
    present = [safety[name] for name in names if name in safety]
    if not present:
        return False
    try:
        values = [float(value) for value in present]
    except (TypeError, ValueError):
        return False
    return all(math.isfinite(value) and value == 0.0 for value in values)


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = max(math.ceil(quantile * len(ordered)) - 1, 0)
    return round(ordered[index], 3)


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return round(float(numerator) / float(denominator), 4) if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 4)


def _fmt(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


__all__ = [
    "EXPECTED_CASE_COUNT",
    "EXPECTED_OVERLAY_MEMORY_COUNT",
    "EXPECTED_SHARED_MEMORY_COUNT",
    "HARD_CASE_IDS",
    "REPORT_VERSION",
    "FixtureTextEmbeddingProvider",
    "FixtureV2SemanticRelationJudge",
    "LongTailWriteV2EvaluationError",
    "collect_memory_longtail_write_v2_repository_metadata",
    "compare_memory_longtail_write_v2_reports",
    "evaluate_memory_longtail_write_v2",
    "evaluate_memory_longtail_write_v2_fixture",
    "finalize_memory_longtail_write_v2_live_validation",
    "load_memory_longtail_write_v2_dataset",
    "render_memory_longtail_write_v2_report",
]

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from qdrant_client import AsyncQdrantClient

from loveapp.adapters.knowledge.qdrant import QdrantKnowledgeStore
from loveapp.adapters.knowledge.scoring import RerankConfig, RerankerMode, soft_rerank
from loveapp.domain.knowledge import (
    KnowledgeDocument,
    KnowledgeFilters,
    RetrievalTextMode,
    RetrievedDocument,
)
from loveapp.evaluation.rag_v2 import (
    RagEvalCase,
    RagEvalResult,
    RagExecutor,
    RagExpectedBranch,
    evaluate_rag_v2,
)
from loveapp.ports.embeddings import EmbeddingProvider

ProgressCallback = Callable[[str], None]
DenseCandidates = dict[str, list[RetrievedDocument]]

_THRESHOLDS = (0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60)
_CANDIDATE_LIMITS = (15, 30, 50)
_TOP_K_VALUES = (3, 5, 7)
_RERANKER_MODES = tuple(RerankerMode)
_RETRIEVAL_TEXT_MODES = tuple(RetrievalTextMode)
_WEIGHTS = (0.5, 1.0, 1.5)


async def run_rag_v2_dev_sweep(
    documents: Sequence[KnowledgeDocument],
    cases: Sequence[RagEvalCase],
    *,
    embedding_provider: EmbeddingProvider,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run the prescribed sequential Dev-only sweep without a Cartesian explosion."""

    if not cases:
        raise ValueError("RAG V2 sweep requires at least one Dev case")
    non_dev_ids = [case.id for case in cases if not case.id.startswith("rag_v2_dev_")]
    if non_dev_ids:
        preview = ", ".join(non_dev_ids[:3])
        raise ValueError(
            "RAG V2 sweep accepts the Dev split only; "
            f"Test/unknown cases are evaluation-only: {preview}"
        )

    rag_cases = [case for case in cases if case.expected_branch == RagExpectedBranch.RAG]
    _notify(progress, "Embedding full retrieval text and collecting dense candidates")
    raw_candidates = await _collect_dense_candidates(
        documents,
        rag_cases,
        embedding_provider=embedding_provider,
        retrieval_text_mode=RetrievalTextMode.FULL,
        hard_filter=False,
        candidate_limit=max(_CANDIDATE_LIMITS),
    )
    current = SweepConfig()
    phases: dict[str, list[dict[str, Any]]] = {}

    phases["rag_min_score"] = await _evaluate_configs(
        rag_cases,
        raw_candidates,
        [current.model_copy(update={"min_score": value}) for value in _THRESHOLDS],
        progress=progress,
    )
    current = _select_config(phases["rag_min_score"])

    phases["candidate_limit"] = await _evaluate_configs(
        rag_cases,
        raw_candidates,
        [current.model_copy(update={"candidate_limit": value}) for value in _CANDIDATE_LIMITS],
        progress=progress,
    )
    current = _select_config(phases["candidate_limit"])

    phases["top_k"] = await _evaluate_configs(
        rag_cases,
        raw_candidates,
        [current.model_copy(update={"top_k": value}) for value in _TOP_K_VALUES],
        progress=progress,
    )
    current = _select_config(phases["top_k"])

    phases["reranker"] = await _evaluate_configs(
        rag_cases,
        raw_candidates,
        [current.model_copy(update={"reranker_mode": value}) for value in _RERANKER_MODES],
        progress=progress,
    )
    current = _select_config(phases["reranker"])

    retrieval_runs: list[dict[str, Any]] = []
    retrieval_candidates: dict[RetrievalTextMode, DenseCandidates] = {
        RetrievalTextMode.FULL: raw_candidates
    }
    for mode in _RETRIEVAL_TEXT_MODES:
        if mode not in retrieval_candidates:
            _notify(progress, f"Embedding retrieval-text ablation: {mode.value}")
            retrieval_candidates[mode] = await _collect_dense_candidates(
                documents,
                rag_cases,
                embedding_provider=embedding_provider,
                retrieval_text_mode=mode,
                hard_filter=False,
                candidate_limit=max(_CANDIDATE_LIMITS),
            )
        config = current.model_copy(update={"retrieval_text_mode": mode})
        retrieval_runs.extend(
            await _evaluate_configs(
                rag_cases,
                retrieval_candidates[mode],
                [config],
                progress=progress,
            )
        )
    phases["retrieval_text"] = retrieval_runs
    current = _select_config(phases["retrieval_text"])
    raw_candidates = retrieval_candidates[current.retrieval_text_mode]

    weight_configs = [current]
    weight_configs.extend(
        current.model_copy(
            update={
                "reranker_mode": RerankerMode.FULL,
                "lexical_weight": lexical,
                "metadata_weight": metadata,
            }
        )
        for lexical in _WEIGHTS
        for metadata in _WEIGHTS
    )
    phases["rerank_weights"] = await _evaluate_configs(
        rag_cases,
        raw_candidates,
        _unique_configs(weight_configs),
        progress=progress,
    )
    current = _select_config(phases["rerank_weights"])

    _notify(progress, "Collecting hard-filter dense candidates")
    hard_candidates = await _collect_dense_candidates(
        documents,
        rag_cases,
        embedding_provider=embedding_provider,
        retrieval_text_mode=current.retrieval_text_mode,
        hard_filter=True,
        candidate_limit=max(_CANDIDATE_LIMITS),
    )
    soft_run = await _evaluate_configs(
        rag_cases,
        raw_candidates,
        [current.model_copy(update={"hard_filter": False})],
        progress=progress,
    )
    hard_run = await _evaluate_configs(
        rag_cases,
        hard_candidates,
        [current.model_copy(update={"hard_filter": True})],
        progress=progress,
    )
    phases["metadata_filter"] = [*soft_run, *hard_run]
    current = _select_config(phases["metadata_filter"])

    return {
        "schema_version": 2,
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": "dev",
        "protocol": "sequential_dev_only",
        "raw_query_only": True,
        "frozen_config": current.model_dump(mode="json"),
        "phases": phases,
    }


class SweepConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_score: float = Field(default=0.45, ge=0, le=1)
    candidate_limit: int = Field(default=15, ge=1, le=1000)
    top_k: int = Field(default=5, ge=1, le=100)
    reranker_mode: RerankerMode = RerankerMode.FULL
    lexical_weight: float = Field(default=1.0, ge=0, le=10)
    metadata_weight: float = Field(default=1.0, ge=0, le=10)
    retrieval_text_mode: RetrievalTextMode = RetrievalTextMode.FULL
    hard_filter: bool = False

    @property
    def rerank_config(self) -> RerankConfig:
        return RerankConfig(
            mode=self.reranker_mode,
            lexical_weight=self.lexical_weight,
            metadata_weight=self.metadata_weight,
        )


async def _collect_dense_candidates(
    documents: Sequence[KnowledgeDocument],
    cases: Sequence[RagEvalCase],
    *,
    embedding_provider: EmbeddingProvider,
    retrieval_text_mode: RetrievalTextMode,
    hard_filter: bool,
    candidate_limit: int,
) -> DenseCandidates:
    client = AsyncQdrantClient(location=":memory:")
    store = QdrantKnowledgeStore(
        client=client,
        collection_name="rag_v2_sweep",
        embedding_provider=embedding_provider,
        min_score=None,
        candidate_limit=candidate_limit,
        rerank_config=RerankConfig(mode=RerankerMode.VECTOR_ONLY),
        retrieval_text_mode=retrieval_text_mode,
    )
    try:
        await store.index_documents(list(documents), recreate=True)
        result: DenseCandidates = {}
        query_texts = [case.query for case in cases]
        batch_embed = getattr(embedding_provider, "embed_queries", None)
        if callable(batch_embed):
            query_vectors = await batch_embed(query_texts)
        else:
            query_vectors = [
                await embedding_provider.embed_query(query)
                for query in query_texts
            ]
        for case, query_vector in zip(cases, query_vectors, strict=True):
            detailed = await store.search_detailed(
                case.query,
                filters=_filters(case, hard=hard_filter),
                limit=candidate_limit,
                query_vector=query_vector,
            )
            result[case.id] = detailed.nearest_candidates
        return result
    finally:
        await client.close()


async def _evaluate_configs(
    cases: Sequence[RagEvalCase],
    dense_candidates: DenseCandidates,
    configs: Sequence[SweepConfig],
    *,
    progress: ProgressCallback | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for config in configs:
        _notify(progress, f"Evaluating {config.model_dump(mode='json')}")
        report = await evaluate_rag_v2(
            cases,
            mode="retriever",
            executor=_offline_executor(dense_candidates, config),
            top_k=config.top_k,
        )
        rows.append(
            {
                "config": config.model_dump(mode="json"),
                "constraints_passed": _constraints_pass(report),
                "composite_score": _composite_score(report),
                "metrics": _summary_metrics(report),
            }
        )
    return rows


def _offline_executor(
    dense_candidates: DenseCandidates,
    config: SweepConfig,
) -> RagExecutor:
    async def execute(case: RagEvalCase) -> RagEvalResult:
        nearest = dense_candidates[case.id][: config.candidate_limit]
        candidates = [match for match in nearest if match.score >= config.min_score]
        reranked = soft_rerank(
            case.query,
            candidates,
            _filters(case, hard=config.hard_filter),
            config.rerank_config,
        )
        return RagEvalResult(
            returned=reranked[: config.top_k],
            nearest_candidates=nearest,
            candidates=candidates,
            reranked_candidates=reranked,
            predicted_branch=RagExpectedBranch.RAG,
            diagnostics_available=True,
        )

    return execute


def _filters(case: RagEvalCase, *, hard: bool) -> KnowledgeFilters:
    return KnowledgeFilters(
        scenario=case.expected_primary_scenario,
        scenarios=case.expected_secondary_scenarios,
        relationship_stage=case.relationship_stage,
        goals=case.expected_goals,
        hard=hard,
    )


def _constraints_pass(report: dict[str, Any]) -> bool:
    false_retrieval_rate = report["false_retrieval_rate"]
    coverage = report["coverage"]
    hard_negative_leakage = report["hard_negative_leakage_at_3"]
    return bool(
        (false_retrieval_rate is None or false_retrieval_rate <= 0.15)
        and (coverage is None or coverage >= 0.95)
        and (hard_negative_leakage is None or hard_negative_leakage <= 0.15)
    )


def _composite_score(report: dict[str, Any]) -> float:
    score = (
        0.25 * _metric_or_zero(report, "hit_at_3")
        + 0.20 * _metric_or_zero(report, "mrr")
        + 0.20 * _metric_or_zero(report, "ndcg_at_5")
        + 0.15 * _metric_or_zero(report, "recall_at_5")
        + 0.10 * _metric_or_zero(report, "no_answer_f1")
        + 0.10 * _metric_or_zero(report, "hard_case_hit_at_3")
    )
    return round(score, 6)


def _metric_or_zero(report: dict[str, Any], key: str) -> float:
    value = report[key]
    return 0.0 if value is None else float(value)


def _select_config(rows: Sequence[dict[str, Any]]) -> SweepConfig:
    eligible = [row for row in rows if row["constraints_passed"]] or list(rows)
    selected = max(
        eligible,
        key=lambda row: (
            row["composite_score"],
            _metric_or_zero(row["metrics"], "hit_at_3"),
            -_metric_or_zero(row["metrics"], "hard_negative_leakage_at_3"),
        ),
    )
    return SweepConfig.model_validate(selected["config"])


def _summary_metrics(report: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "hit_at_1",
        "hit_at_3",
        "hit_at_5",
        "recall_at_3",
        "recall_at_5",
        "precision_at_3",
        "precision_at_5",
        "mrr",
        "ndcg_at_3",
        "ndcg_at_5",
        "candidate_recall",
        "rerank_lift",
        "hard_negative_leakage_at_3",
        "no_answer_f1",
        "false_retrieval_rate",
        "coverage",
        "hard_confusion_hit_at_3",
        "hard_case_hit_at_3",
    )
    return {key: report[key] for key in keys}


def _unique_configs(configs: Sequence[SweepConfig]) -> list[SweepConfig]:
    unique: dict[str, SweepConfig] = {}
    for config in configs:
        key = json.dumps(config.model_dump(mode="json"), sort_keys=True)
        unique[key] = config
    return list(unique.values())


def write_sweep_report(report: dict[str, Any], output_path: Path) -> tuple[Path, Path]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    json_path = (
        output_path
        if output_path.suffix.casefold() == ".json"
        else output_path.with_suffix(".json")
    )
    markdown_path = json_path.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_sweep_report(report), encoding="utf-8")
    return json_path, markdown_path


def render_sweep_report(report: dict[str, Any]) -> str:
    lines = [
        "# LoveApp RAG V2 Dev Ablation Report",
        "",
        "The sweep uses raw standalone queries and the Dev split only.",
        "",
        f"Frozen config: `{json.dumps(report['frozen_config'], ensure_ascii=False)}`",
    ]
    for phase, rows in report["phases"].items():
        lines.extend(
            [
                "",
                f"## {phase}",
                "",
                "| Config | Constraints | Composite | Hit@3 | MRR | nDCG@5 | "
                "NoAnswer F1 | FRR | Coverage | HN leak@3 |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in rows:
            metrics = row["metrics"]
            config = json.dumps(row["config"], ensure_ascii=False, separators=(",", ":"))
            lines.append(
                f"| `{config}` | {row['constraints_passed']} | {row['composite_score']:.4f} "
                f"| {_format_metric(metrics['hit_at_3'])} | {_format_metric(metrics['mrr'])} "
                f"| {_format_metric(metrics['ndcg_at_5'])} "
                f"| {_format_metric(metrics['no_answer_f1'])} "
                f"| {_format_metric(metrics['false_retrieval_rate'])} "
                f"| {_format_metric(metrics['coverage'])} "
                f"| {_format_metric(metrics['hard_negative_leakage_at_3'])} |"
            )
    return "\n".join(lines) + "\n"


def _format_metric(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):.4f}"


def _notify(callback: ProgressCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)

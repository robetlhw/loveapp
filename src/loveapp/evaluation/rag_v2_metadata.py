"""RAG V2 hard-vs-soft metadata filter diagnostics.

This module intentionally keeps the metadata-filter experiment outside of the
retriever implementation.  ``scenario``, ``goal`` and ``relationship_stage``
are relevance preferences in this experiment; the only independent variable is
whether the retriever applies them as a dense-search hard filter.  The helpers
here consume the regular :func:`evaluate_rag_v2` reports and therefore work
with Qdrant, the in-memory adapter, and small test doubles alike.

The comparison is deliberately pair based.  A hard and a soft report must use
the same case IDs and frozen retrieval parameters.  This prevents a change in
Router output, dataset scope, or another retriever setting from being silently
reported as a hard-filter effect.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from loveapp.adapters.knowledge.scoring import RerankerMode
from loveapp.domain.knowledge import RetrievalTextMode
from loveapp.evaluation.rag_v2 import (
    RagEvalCase,
    RagExecutor,
    RagExpectedBranch,
    build_e2e_executor,
    evaluate_rag_v2,
)
from loveapp.ports.knowledge import KnowledgeRetriever
from loveapp.ports.routing import Router

# The values are the frozen Dev configuration from the RAG V2 protocol.  The
# ``hard_filter`` flag is the sole allowed change between the two arms.
FROZEN_METADATA_FILTER_CONFIG: dict[str, Any] = {
    "min_score": 0.6,
    "candidate_limit": 30,
    "top_k": 5,
    "reranker_mode": "full",
    "lexical_weight": 1.5,
    "metadata_weight": 1.0,
    "retrieval_text_mode": "question_variants",
}

ERROR_ATTRIBUTION_CATEGORIES: tuple[str, ...] = (
    "router_branch_error",
    "router_metadata_error",
    "hard_filter_amplification",
    "retriever_candidate_miss",
    "rerank_error",
    "safety_error",
    "no_answer_error",
    "gold_or_dataset_issue",
)

_COMPARISON_METRICS: tuple[str, ...] = (
    "hit_at_1",
    "hit_at_3",
    "hit_at_5",
    "recall_at_3",
    "recall_at_5",
    "mrr",
    "ndcg_at_5",
    "candidate_recall",
    "coverage",
    "abstention_precision",
    "abstention_recall",
    "no_answer_precision",
    "no_answer_recall",
    "no_answer_f1",
    "false_retrieval_rate",
    "hard_negative_leakage_at_3",
    "branch_accuracy",
    "router_primary_scenario_accuracy",
    "goal_micro_f1",
    "high_safety_recall",
    "sensitive_branch_accuracy",
    "safety_rag_bypass_violation_rate",
    "oracle_e2e_degradation_pp",
)


class MetadataFilterExperimentConfig(BaseModel):
    """Frozen retrieval settings for one arm of the metadata experiment."""

    model_config = ConfigDict(frozen=True)

    min_score: float = Field(default=0.6, ge=0, le=1)
    candidate_limit: int = Field(default=30, ge=1, le=1000)
    top_k: int = Field(default=5, ge=1, le=100)
    reranker_mode: RerankerMode = RerankerMode.FULL
    lexical_weight: float = Field(default=1.5, ge=0, le=10)
    metadata_weight: float = Field(default=1.0, ge=0, le=10)
    retrieval_text_mode: RetrievalTextMode = RetrievalTextMode.QUESTION_VARIANTS
    hard_filter: bool

    @classmethod
    def frozen(cls, *, hard_filter: bool) -> MetadataFilterExperimentConfig:
        """Build the protocol's frozen config with one explicit flag."""

        return cls(hard_filter=hard_filter)

    def model_dump_jsonable(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class MetadataFilterCaseComparison(BaseModel):
    """Paired per-case evidence used by the amplification diagnostic."""

    model_config = ConfigDict(extra="allow")

    id: str
    query: str = ""
    expected_branch: str | None = None
    expected_scenario: str | None = None
    expected_goals: list[str] = Field(default_factory=list)
    predicted_branch: str | None = None
    predicted_scenario: str | None = None
    predicted_goals: list[str] = Field(default_factory=list)
    metadata_correct: bool | None = None
    branch_correct: bool | None = None
    gold_ids: list[str] = Field(default_factory=list)

    hard_nearest_ids: list[str] = Field(default_factory=list)
    hard_candidate_ids: list[str] = Field(default_factory=list)
    hard_top3_ids: list[str] = Field(default_factory=list)
    hard_top5_ids: list[str] = Field(default_factory=list)
    soft_nearest_ids: list[str] = Field(default_factory=list)
    soft_candidate_ids: list[str] = Field(default_factory=list)
    soft_top3_ids: list[str] = Field(default_factory=list)
    soft_top5_ids: list[str] = Field(default_factory=list)

    hard_candidate_contains_gold: bool | None = None
    soft_candidate_contains_gold: bool | None = None
    hard_top3_contains_gold: bool | None = None
    soft_top3_contains_gold: bool | None = None
    hard_top5_contains_gold: bool | None = None
    soft_top5_contains_gold: bool | None = None

    hard_filter_candidate_recovery: bool = False
    hard_filter_top3_recovery: bool = False
    hard_filter_top5_recovery: bool = False
    hard_filter_amplification: bool = False
    hard_nearest_contains_gold: bool | None = None
    soft_nearest_contains_gold: bool | None = None
    hard_filter_applied_before_dense_candidate: bool | None = None
    hard_trace: list[dict[str, Any]] = Field(default_factory=list)
    soft_trace: list[dict[str, Any]] = Field(default_factory=list)
    attribution: str | None = None

    @property
    def is_answered_rag_case(self) -> bool:
        return self.expected_branch == RagExpectedBranch.RAG.value and bool(
            self.gold_ids
        )


MetadataComparisonExecutorPair = tuple[RagExecutor, RagExecutor]


def build_metadata_filter_e2e_executors(
    router: Router,
    hard_retriever: KnowledgeRetriever,
    soft_retriever: KnowledgeRetriever,
    *,
    top_k: int = 5,
    policy_registry: Any | None = None,
) -> MetadataComparisonExecutorPair:
    """Build paired E2E executors that share exactly one Router decision.

    Running the two arms independently can accidentally turn a live Router
    variation into a metadata-filter delta.  This small replay wrapper caches
    the first route result by raw query and feeds the identical result to both
    retrievers.  ``policy_registry`` is forwarded to the regular E2E builder;
    its type is intentionally loose to avoid importing application internals
    into this diagnostic module.
    """

    cache: dict[str, Any] = {}

    class _CachedRouter:
        async def route(self, route_input):
            key = route_input.latest_query
            if key not in cache:
                cache[key] = await router.route(route_input)
            return cache[key]

    cached_router = _CachedRouter()
    kwargs: dict[str, Any] = {"top_k": top_k}
    if policy_registry is not None:
        kwargs["policy_registry"] = policy_registry
    return (
        build_e2e_executor(cached_router, hard_retriever, **kwargs),
        build_e2e_executor(cached_router, soft_retriever, **kwargs),
    )


async def evaluate_metadata_filter_comparison(
    cases: Sequence[RagEvalCase],
    *,
    hard_executor: RagExecutor,
    soft_executor: RagExecutor,
    mode: Literal["retriever", "e2e"] = "e2e",
    top_k: int = 5,
    dataset: str = "dev",
    hard_config: MetadataFilterExperimentConfig | Mapping[str, Any] | None = None,
    soft_config: MetadataFilterExperimentConfig | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the two arms and return a paired comparison report.

    The executors are intentionally supplied by the caller.  In production
    this lets the caller construct two stores with identical embeddings and
    reranker settings, changing only ``hard_filter``.  In tests it permits
    deterministic fake executors and makes the diagnostic inexpensive.
    """

    hard_report = await evaluate_rag_v2(
        cases,
        mode=mode,
        executor=hard_executor,
        top_k=top_k,
    )
    soft_report = await evaluate_rag_v2(
        cases,
        mode=mode,
        executor=soft_executor,
        top_k=top_k,
    )
    return compare_metadata_filter_reports(
        hard_report,
        soft_report,
        dataset=dataset,
        mode=mode,
        hard_config=hard_config,
        soft_config=soft_config,
    )


def compare_metadata_filter_reports(
    hard_report: Mapping[str, Any],
    soft_report: Mapping[str, Any],
    *,
    dataset: str | None = None,
    mode: str | None = None,
    hard_config: MetadataFilterExperimentConfig | Mapping[str, Any] | None = None,
    soft_config: MetadataFilterExperimentConfig | Mapping[str, Any] | None = None,
    max_case_studies: int = 10,
) -> dict[str, Any]:
    """Compare two same-scope reports and attribute paired failures.

    ``delta`` is always ``soft - hard``.  A positive candidate-recall delta,
    for example, means that removing the hard metadata filter recovered more
    gold documents.  Scope mismatches are surfaced in ``scope`` and never
    hidden by dropping unmatched cases.
    """

    hard_rows = _rows_by_id(hard_report)
    soft_rows = _rows_by_id(soft_report)
    common_ids = [case_id for case_id in hard_rows if case_id in soft_rows]
    hard_only = [case_id for case_id in hard_rows if case_id not in soft_rows]
    soft_only = [case_id for case_id in soft_rows if case_id not in hard_rows]

    pairs = [
        _pair_case(case_id, hard_rows[case_id], soft_rows[case_id])
        for case_id in common_ids
    ]
    for pair in pairs:
        pair.attribution = _paired_attribution(pair, hard_rows[pair.id], soft_rows[pair.id])

    hard_metrics = _report_metrics(hard_report)
    soft_metrics = _report_metrics(soft_report)
    overall = {
        metric: {
            "hard": hard_metrics.get(metric),
            "soft": soft_metrics.get(metric),
            "delta": _delta(soft_metrics.get(metric), hard_metrics.get(metric)),
        }
        for metric in _COMPARISON_METRICS
    }

    # Counts are measured directly from case rows so reports generated by an
    # older evaluator still get the required diagnostics.
    hard_counts = _operational_counts(hard_rows.values())
    soft_counts = _operational_counts(soft_rows.values())
    for metric in (
        "retrieval_called_count",
        "retrieval_bypassed_count",
        "empty_candidate_count",
    ):
        overall[metric] = {
            "hard": hard_counts[metric],
            "soft": soft_counts[metric],
            "delta": soft_counts[metric] - hard_counts[metric],
        }

    # Keep the operational counts at the top level as well as in ``overall``;
    # this mirrors the existing RAG report convention and makes CLI summaries
    # straightforward without parsing the metric table.
    top_level_operational = {
        metric: values["hard"]
        for metric, values in overall.items()
        if metric in {"retrieval_called_count", "retrieval_bypassed_count", "empty_candidate_count"}
    }

    metadata_incorrect = [
        pair
        for pair in pairs
        if pair.is_answered_rag_case and pair.metadata_correct is False
    ]
    amplification_pairs = [
        pair for pair in metadata_incorrect if pair.hard_filter_amplification
    ]
    candidate_recovery = [
        pair for pair in metadata_incorrect if pair.hard_filter_candidate_recovery
    ]
    top3_recovery = [pair for pair in metadata_incorrect if pair.hard_filter_top3_recovery]
    top5_recovery = [pair for pair in metadata_incorrect if pair.hard_filter_top5_recovery]
    amplification_denominator = len(metadata_incorrect)
    amplification = {
        "hard_filter_amplification_count": len(amplification_pairs),
        "hard_filter_amplification_rate": _ratio(
            len(amplification_pairs), amplification_denominator
        ),
        "hard_filter_candidate_recovery": len(candidate_recovery),
        "hard_filter_candidate_recovery_rate": _ratio(
            len(candidate_recovery), amplification_denominator
        ),
        "hard_filter_top3_recovery": len(top3_recovery),
        "hard_filter_top3_recovery_rate": _ratio(
            len(top3_recovery), amplification_denominator
        ),
        "hard_filter_top5_recovery": len(top5_recovery),
        "hard_filter_top5_recovery_rate": _ratio(
            len(top5_recovery), amplification_denominator
        ),
        "denominator_router_metadata_incorrect_answered_rag": amplification_denominator,
        "diagnostics_available_count": sum(
            _diagnostics_available(hard_rows[pair.id])
            and _diagnostics_available(soft_rows[pair.id])
            for pair in metadata_incorrect
        ),
        "cases": [pair.model_dump(mode="json") for pair in amplification_pairs],
    }
    recovery_summary = {
        "router_metadata_incorrect_answered_rag_count": amplification_denominator,
        "gold_recovered_in_candidate_pool_count": len(candidate_recovery),
        "gold_recovered_in_candidate_pool_rate": _ratio(
            len(candidate_recovery), amplification_denominator
        ),
        "gold_recovered_in_top3_count": len(top3_recovery),
        "gold_recovered_in_top3_rate": _ratio(len(top3_recovery), amplification_denominator),
        "gold_recovered_in_top5_count": len(top5_recovery),
        "gold_recovered_in_top5_rate": _ratio(len(top5_recovery), amplification_denominator),
    }
    trace_evidence_cases = [
        pair.model_dump(mode="json")
        for pair in pairs
        if pair.hard_filter_amplification
        and pair.soft_nearest_contains_gold is True
        and pair.hard_nearest_contains_gold is False
        and pair.hard_filter_applied_before_dense_candidate is True
    ][:5]

    router_slices = {
        "metadata_correct": _router_slice(pairs, hard_rows, soft_rows, correct=True),
        "metadata_incorrect": _router_slice(pairs, hard_rows, soft_rows, correct=False),
    }

    attribution_counts = {
        "hard": _attribution_counts(
            pairs, hard_rows, arm="hard", include_success=False
        ),
        "soft": _attribution_counts(
            pairs, soft_rows, arm="soft", include_success=False
        ),
        "paired": dict(
            Counter(
                pair.attribution
                for pair in pairs
                if pair.attribution is not None
            )
        ),
    }
    case_studies = _select_case_studies(
        pairs,
        hard_rows,
        soft_rows,
        max_case_studies=max_case_studies,
    )
    recovered_selected = [case for case in case_studies if case.get("hard_filter_amplification")]
    residual_selected = [
        case
        for case in case_studies
        if case.get("expected_branch") == RagExpectedBranch.RAG.value
        and case.get("gold_ids")
        and not case.get("hard_filter_amplification")
        and not _row_has_relevant_at_cutoff(soft_rows.get(str(case.get("id")), {}), 3)
    ]
    safety_router_selected = [
        case
        for case in case_studies
        if case.get("attribution") in {"router_branch_error", "safety_error"}
    ]
    case_study_target_counts = {
        "recovered_requested": 5,
        "recovered_actual": min(len(recovered_selected), 5),
        "residual_requested": 3,
        "residual_actual": min(len(residual_selected), 3),
        "safety_router_requested": 2,
        "safety_router_actual": min(len(safety_router_selected), 2),
    }

    hard_cfg = _config_dict(hard_config, hard_filter=True)
    soft_cfg = _config_dict(soft_config, hard_filter=False)
    scope = {
        "same_case_ids": not hard_only and not soft_only,
        "hard_case_count": len(hard_rows),
        "soft_case_count": len(soft_rows),
        "paired_case_count": len(pairs),
        "hard_only_case_ids": hard_only,
        "soft_only_case_ids": soft_only,
        "same_mode": hard_report.get("mode") == soft_report.get("mode"),
        "hard_mode": hard_report.get("mode"),
        "soft_mode": soft_report.get("mode"),
        "fixed_parameters_equal": _fixed_parameters_equal(hard_cfg, soft_cfg),
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "experiment": "rag_v2_metadata_filter_hard_vs_soft",
        "dataset": dataset or hard_report.get("dataset") or "unknown",
        "mode": mode or hard_report.get("mode") or soft_report.get("mode"),
        "delta_definition": "soft_minus_hard",
        "scope": scope,
        "hard_filter": {"enabled": True, "config": hard_cfg},
        "soft_filter": {"enabled": False, "config": soft_cfg},
        "hard_report_summary": _report_summary(hard_report),
        "soft_report_summary": _report_summary(soft_report),
        "overall": overall,
        # ``metrics`` is a compact alias useful to downstream tooling.
        "metrics": overall,
        **top_level_operational,
        "router_correctness_slices": router_slices,
        "hard_filter_amplification": amplification,
        "hard_filter_amplification_count": amplification["hard_filter_amplification_count"],
        "hard_filter_amplification_rate": amplification["hard_filter_amplification_rate"],
        "hard_filter_candidate_recovery": recovery_summary[
            "gold_recovered_in_candidate_pool_count"
        ],
        "hard_filter_top3_recovery": recovery_summary["gold_recovered_in_top3_count"],
        "hard_filter_top5_recovery": recovery_summary["gold_recovered_in_top5_count"],
        "recovery_summary": recovery_summary,
        **recovery_summary,
        "metadata_correctness_definition": (
            "answered RAG only; exact primary scenario, at least one expected goal "
            "intersection (existing RAG V2 criterion), and relationship_stage when "
            "both sides provide a known value"
        ),
        "relationship_stage_evaluation": (
            "RouteResult currently emits no predicted relationship_stage; E2E keeps the "
            "case stage constant in both arms, so this experiment isolates scenario/goal "
            "Router errors and does not claim stage-misprediction evidence"
        ),
        "hard_filter_trace_evidence": {
            "required_shape": (
                "soft dense nearest contains gold; hard dense nearest excludes gold; "
                "hard_filter=true is recorded on rag_vector_search before rerank"
            ),
            "case_count": len(trace_evidence_cases),
            "cases": trace_evidence_cases,
        },
        "error_attribution": attribution_counts,
        "error_attribution_categories": list(ERROR_ATTRIBUTION_CATEGORIES),
        "case_studies": case_studies,
        "case_study_count": len(case_studies),
        "case_study_target_counts": case_study_target_counts,
        "paired_cases": [pair.model_dump(mode="json") for pair in pairs],
    }
    report["conclusion"] = _comparison_conclusion(report)
    return report


# Short aliases make the API discoverable from callers that use the wording in
# the protocol prompt.
compare_hard_soft_metadata_reports = compare_metadata_filter_reports
evaluate_hard_soft_metadata = evaluate_metadata_filter_comparison


def render_metadata_filter_comparison(report: Mapping[str, Any]) -> str:
    """Render the comparison in the protocol's Markdown report shape."""

    overall = report.get("overall", {})
    lines = [
        "# LoveApp RAG V2 Metadata Filter Hard vs Soft Comparison",
        "",
        f"- Dataset: `{report.get('dataset', '-')}`",
        f"- Mode: `{report.get('mode', '-')}`",
        f"- Delta: `{report.get('delta_definition', 'soft_minus_hard')}`",
        f"- Scope comparable: `{report.get('scope', {}).get('same_case_ids', False)}`",
        "",
        "## Frozen configuration",
        "",
        "| Setting | Hard | Soft |",
        "|---|---|---|",
    ]
    hard_cfg = report.get("hard_filter", {}).get("config", {})
    soft_cfg = report.get("soft_filter", {}).get("config", {})
    for key in (
        "min_score",
        "candidate_limit",
        "top_k",
        "reranker_mode",
        "lexical_weight",
        "metadata_weight",
        "retrieval_text_mode",
        "hard_filter",
    ):
        lines.append(f"| `{key}` | {_format(hard_cfg.get(key))} | {_format(soft_cfg.get(key))} |")

    lines.extend(
        [
            "",
            "## Overall",
            "",
            "| Metric | Hard | Soft | Delta (Soft-Hard) |",
            "|---|---:|---:|---:|",
        ]
    )
    for key in (
        "hit_at_1",
        "hit_at_3",
        "hit_at_5",
        "recall_at_3",
        "recall_at_5",
        "mrr",
        "ndcg_at_5",
        "candidate_recall",
        "coverage",
        "no_answer_precision",
        "no_answer_recall",
        "abstention_precision",
        "abstention_recall",
        "no_answer_f1",
        "false_retrieval_rate",
        "hard_negative_leakage_at_3",
        "branch_accuracy",
        "router_primary_scenario_accuracy",
        "goal_micro_f1",
        "high_safety_recall",
        "sensitive_branch_accuracy",
        "safety_rag_bypass_violation_rate",
        "oracle_e2e_degradation_pp",
        "retrieval_called_count",
        "retrieval_bypassed_count",
        "empty_candidate_count",
    ):
        values = overall.get(key, {})
        lines.append(
            f"| `{key}` | {_format(values.get('hard'))} | "
            f"{_format(values.get('soft'))} | {_format(values.get('delta'))} |"
        )

    lines.extend(
        [
            "",
            "### Recovery counts on Router-metadata-incorrect answered RAG cases",
            "",
            f"- Denominator: `{report.get('router_metadata_incorrect_answered_rag_count', 0)}`",
            "- Gold recovered in candidate pool: "
            f"`{report.get('gold_recovered_in_candidate_pool_count', 0)}` "
            f"({ _format(report.get('gold_recovered_in_candidate_pool_rate')) })",
            f"- Gold recovered in Top3: `{report.get('gold_recovered_in_top3_count', 0)}` "
            f"({ _format(report.get('gold_recovered_in_top3_rate')) })",
            f"- Gold recovered in Top5: `{report.get('gold_recovered_in_top5_count', 0)}` "
            f"({ _format(report.get('gold_recovered_in_top5_rate')) })",
        ]
    )

    lines.extend(["", "## Router / retrieval coupling", ""])
    amp = report.get("hard_filter_amplification", {})
    lines.extend(
        [
            f"- `hard_filter_amplification_count`: {amp.get('hard_filter_amplification_count', 0)}",
            "- `hard_filter_amplification_rate`: "
            f"{_format(amp.get('hard_filter_amplification_rate'))}",
            f"- `hard_filter_candidate_recovery`: {amp.get('hard_filter_candidate_recovery', 0)}",
            f"- `hard_filter_top3_recovery`: {amp.get('hard_filter_top3_recovery', 0)}",
            f"- `hard_filter_top5_recovery`: {amp.get('hard_filter_top5_recovery', 0)}",
            f"- Amplification denominator (Router metadata incorrect answered RAG): "
            f"{amp.get('denominator_router_metadata_incorrect_answered_rag', 0)}",
        ]
    )
    targets = report.get("case_study_target_counts", {})
    lines.append(
        "- Case-study target counts: "
        f"recovered {targets.get('recovered_actual', 0)}/"
        f"{targets.get('recovered_requested', 0)}, "
        f"residual {targets.get('residual_actual', 0)}/"
        f"{targets.get('residual_requested', 0)}, "
        f"safety/router {targets.get('safety_router_actual', 0)}/"
        f"{targets.get('safety_router_requested', 0)}"
    )
    lines.extend(
        [
            "",
            "## Router correctness slices",
            "",
            "| Slice | Cases | Hard Hit@3 | Soft Hit@3 | Hard MRR | Soft MRR | "
            "Hard Coverage | Soft Coverage | Hard Candidate Recall | Soft Candidate Recall |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for slice_name, values in report.get("router_correctness_slices", {}).items():
        lines.append(
            f"| `{slice_name}` | {values.get('case_count', 0)} | "
            f"{_format(values.get('hard', {}).get('hit_at_3'))} | "
            f"{_format(values.get('soft', {}).get('hit_at_3'))} | "
            f"{_format(values.get('hard', {}).get('mrr'))} | "
            f"{_format(values.get('soft', {}).get('mrr'))} | "
            f"{_format(values.get('hard', {}).get('coverage'))} | "
            f"{_format(values.get('soft', {}).get('coverage'))} | "
            f"{_format(values.get('hard', {}).get('candidate_recall'))} | "
            f"{_format(values.get('soft', {}).get('candidate_recall'))} |"
        )

    relationship_stage_evaluation = report.get("relationship_stage_evaluation")
    if relationship_stage_evaluation:
        lines.extend(
            [
                "",
                "## Relationship-stage evaluation boundary",
                "",
                str(relationship_stage_evaluation),
            ]
        )

    lines.extend(
        [
            "",
            "## Error attribution",
            "",
            "| Attribution | Hard | Soft | Paired |",
            "|---|---:|---:|---:|",
        ]
    )
    attribution = report.get("error_attribution", {})
    for name in report.get("error_attribution_categories", ERROR_ATTRIBUTION_CATEGORIES):
        lines.append(
            f"| `{name}` | {attribution.get('hard', {}).get(name, 0)} | "
            f"{attribution.get('soft', {}).get(name, 0)} | "
            f"{attribution.get('paired', {}).get(name, 0)} |"
        )

    evidence = report.get("hard_filter_trace_evidence", {})
    lines.extend(
        [
            "",
            "## Hard-filter trace evidence",
            "",
            f"- Required evidence cases: `{evidence.get('case_count', 0)}`",
            f"- Shape: {evidence.get('required_shape', '-')}",
        ]
    )
    for case in evidence.get("cases", []):
        lines.extend(
            [
                f"- `{case.get('id', '-')}`: "
                f"hard_nearest_contains_gold={case.get('hard_nearest_contains_gold')}, "
                f"soft_nearest_contains_gold={case.get('soft_nearest_contains_gold')}, "
                f"hard_filter_applied_before_dense_candidate="
                f"{case.get('hard_filter_applied_before_dense_candidate')}",
            ]
        )

    lines.extend(["", "## Case studies", ""])
    for case in report.get("case_studies", []):
        lines.extend(
            [
                f"### `{case.get('id', '-')}` — `{case.get('attribution', '-')}`",
                "",
                f"- Query: {case.get('query', '')}",
                f"- Gold Scenario / Goals: `{case.get('expected_scenario')}` / "
                f"`{case.get('expected_goals', [])}`",
                f"- Predicted Scenario / Goals: `{case.get('predicted_scenario')}` / "
                f"`{case.get('predicted_goals', [])}`",
                f"- Gold IDs: `{case.get('gold_ids', [])}`",
                f"- Hard Filter Candidate IDs: `{case.get('hard_candidate_ids', [])}`",
                f"- Hard Filter Top5 IDs: `{case.get('hard_top5_ids', [])}`",
                f"- Soft Metadata Candidate IDs: `{case.get('soft_candidate_ids', [])}`",
                f"- Soft Metadata Top5 IDs: `{case.get('soft_top5_ids', [])}`",
                "",
            ]
        )
    if not report.get("case_studies"):
        lines.append("No paired failure case was available for a case study.")

    lines.extend(["", "## Conclusion", "", str(report.get("conclusion", ""))])
    return "\n".join(lines).rstrip() + "\n"


def write_metadata_filter_comparison(
    report: Mapping[str, Any],
    output_path: Path,
) -> tuple[Path, Path]:
    """Write JSON and Markdown comparison artifacts beside each other."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    json_path = (
        output_path
        if output_path.suffix.casefold() == ".json"
        else output_path.with_suffix(".json")
    )
    markdown_path = json_path.with_suffix(".md")
    json_path.write_text(
        json.dumps(dict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(render_metadata_filter_comparison(report), encoding="utf-8")
    return json_path, markdown_path


def _pair_case(
    case_id: str,
    hard: Mapping[str, Any],
    soft: Mapping[str, Any],
) -> MetadataFilterCaseComparison:
    gold_ids = _as_list(hard.get("relevant_ids", soft.get("relevant_ids", [])))
    expected_branch = _as_optional_str(hard.get("expected_branch", soft.get("expected_branch")))
    expected_scenario = _as_optional_str(hard.get("scenario", soft.get("scenario")))
    expected_goals = _as_list(hard.get("goals", soft.get("goals", [])))
    predicted_branch = _same_or_unknown(
        hard.get("predicted_branch"), soft.get("predicted_branch")
    )
    predicted_scenario = _same_or_unknown(
        hard.get("predicted_scenario"), soft.get("predicted_scenario")
    )
    predicted_goals = _same_list_or_first(
        hard.get("predicted_goals", []), soft.get("predicted_goals", [])
    )
    branch_correct = _branch_correct(hard)
    if branch_correct is None:
        branch_correct = _branch_correct(soft)
    metadata_correct = _metadata_correct(
        expected_branch=expected_branch,
        expected_scenario=expected_scenario,
        expected_goals=expected_goals,
        predicted_branch=predicted_branch,
        predicted_scenario=predicted_scenario,
        predicted_goals=predicted_goals,
        row=hard,
    )
    hard_nearest = _as_list(hard.get("nearest_candidate_ids", []))
    hard_candidate = _as_list(hard.get("candidate_ids", []))
    hard_top3 = _as_list(hard.get("returned_ids", []))[:3]
    hard_top5 = _as_list(hard.get("returned_ids", []))[:5]
    soft_nearest = _as_list(soft.get("nearest_candidate_ids", []))
    soft_candidate = _as_list(soft.get("candidate_ids", []))
    soft_top3 = _as_list(soft.get("returned_ids", []))[:3]
    soft_top5 = _as_list(soft.get("returned_ids", []))[:5]
    hard_candidate_contains = _contains_any(hard_candidate, gold_ids)
    soft_candidate_contains = _contains_any(soft_candidate, gold_ids)
    hard_top3_contains = _contains_any(hard_top3, gold_ids)
    soft_top3_contains = _contains_any(soft_top3, gold_ids)
    hard_top5_contains = _contains_any(hard_top5, gold_ids)
    soft_top5_contains = _contains_any(soft_top5, gold_ids)
    hard_nearest_contains = _contains_any(hard_nearest, gold_ids)
    soft_nearest_contains = _contains_any(soft_nearest, gold_ids)
    candidate_recovery = bool(
        _diagnostics_available(hard)
        and _diagnostics_available(soft)
        and hard_candidate_contains is False
        and soft_candidate_contains is True
    )
    top3_recovery = bool(
        _diagnostics_available(hard)
        and _diagnostics_available(soft)
        and hard_top3_contains is False
        and soft_top3_contains is True
    )
    top5_recovery = bool(
        _diagnostics_available(hard)
        and _diagnostics_available(soft)
        and hard_top5_contains is False
        and soft_top5_contains is True
    )
    hard_trace = _as_trace(hard.get("trace"))
    soft_trace = _as_trace(soft.get("trace"))
    hard_filter_before_dense = _hard_filter_before_dense(hard_trace)
    amplification = bool(
        metadata_correct is False
        and expected_branch == RagExpectedBranch.RAG.value
        and bool(gold_ids)
        and hard_filter_before_dense is True
        and (candidate_recovery or top3_recovery or top5_recovery)
    )
    return MetadataFilterCaseComparison(
        id=case_id,
        query=str(hard.get("query", soft.get("query", ""))),
        expected_branch=expected_branch,
        expected_scenario=expected_scenario,
        expected_goals=expected_goals,
        predicted_branch=predicted_branch,
        predicted_scenario=predicted_scenario,
        predicted_goals=predicted_goals,
        metadata_correct=metadata_correct,
        branch_correct=branch_correct,
        gold_ids=gold_ids,
        hard_nearest_ids=hard_nearest,
        hard_candidate_ids=hard_candidate,
        hard_top3_ids=hard_top3,
        hard_top5_ids=hard_top5,
        soft_nearest_ids=soft_nearest,
        soft_candidate_ids=soft_candidate,
        soft_top3_ids=soft_top3,
        soft_top5_ids=soft_top5,
        hard_candidate_contains_gold=hard_candidate_contains,
        soft_candidate_contains_gold=soft_candidate_contains,
        hard_top3_contains_gold=hard_top3_contains,
        soft_top3_contains_gold=soft_top3_contains,
        hard_top5_contains_gold=hard_top5_contains,
        soft_top5_contains_gold=soft_top5_contains,
        hard_filter_candidate_recovery=candidate_recovery,
        hard_filter_top3_recovery=top3_recovery,
        hard_filter_top5_recovery=top5_recovery,
        hard_filter_amplification=amplification,
        hard_nearest_contains_gold=hard_nearest_contains,
        soft_nearest_contains_gold=soft_nearest_contains,
        hard_filter_applied_before_dense_candidate=hard_filter_before_dense,
        hard_trace=hard_trace,
        soft_trace=soft_trace,
    )


def _as_trace(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _hard_filter_before_dense(trace: Sequence[Mapping[str, Any]]) -> bool | None:
    """Infer whether metadata was applied at the vector-search boundary.

    Qdrant records the effective ``hard_filter`` in the vector-search trace.
    Missing traces are treated as unavailable rather than as evidence of a
    soft path.  This prevents a diagnostic-only executor from being mistaken
    for a production hard-filter run.
    """

    for record in trace:
        if record.get("name") == "rag_vector_search":
            details = record.get("details")
            if isinstance(details, Mapping) and "hard_filter" in details:
                return bool(details["hard_filter"])
    return None


def _router_slice(
    pairs: Sequence[MetadataFilterCaseComparison],
    hard_rows: Mapping[str, Mapping[str, Any]],
    soft_rows: Mapping[str, Mapping[str, Any]],
    *,
    correct: bool,
) -> dict[str, Any]:
    selected = [
        pair
        for pair in pairs
        if pair.is_answered_rag_case and pair.metadata_correct is correct
    ]
    hard = [_row_for_pair(pair, hard_rows) for pair in selected]
    soft = [_row_for_pair(pair, soft_rows) for pair in selected]
    return {
        "case_count": len(selected),
        "hard": _slice_metric_values(hard),
        "soft": _slice_metric_values(soft),
        "case_ids": [pair.id for pair in selected],
    }


def _slice_metric_values(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    coverage_values = [
        float(row["coverage"])
        if isinstance(row.get("coverage"), (int, float))
        else float(bool(row.get("covered")))
        for row in rows
        if "coverage" in row or "covered" in row
    ]
    return {
        key: _mean_optional(rows, key)
        for key in ("hit_at_3", "mrr", "candidate_recall")
    } | {
        "coverage": round(sum(coverage_values) / len(coverage_values), 4)
        if coverage_values
        else None
    }


def _select_case_studies(
    pairs: Sequence[MetadataFilterCaseComparison],
    hard_rows: Mapping[str, Mapping[str, Any]],
    soft_rows: Mapping[str, Mapping[str, Any]],
    *,
    max_case_studies: int,
) -> list[dict[str, Any]]:
    """Select amplification, residual, and branch-failure evidence.

    The protocol asks for five recovered, three residual, and two safety/router
    examples when enough cases exist.  A smaller dataset returns all available
    examples and records the actual count in the report.
    """

    recovered = [pair for pair in pairs if pair.hard_filter_amplification]
    residual = [
        pair
        for pair in pairs
        if pair.is_answered_rag_case
        and not _row_has_relevant_at_cutoff(soft_rows[pair.id], 3)
    ]
    branch_failures = [
        pair
        for pair in pairs
        if pair.expected_branch in {"safety", "rag"}
        and pair.branch_correct is False
    ]
    selected: list[MetadataFilterCaseComparison] = []
    selected.extend(recovered[:5])
    selected_ids = {item.id for item in selected}
    residual_added = 0
    for pair in residual:
        if residual_added >= 3:
            break
        if pair.id not in selected_ids:
            selected.append(pair)
            selected_ids.add(pair.id)
            residual_added += 1
    branch_added = 0
    for pair in branch_failures:
        if branch_added >= 2:
            break
        if pair.id not in selected_ids:
            selected.append(pair)
            selected_ids.add(pair.id)
            branch_added += 1
    # Fill any remaining slots with failures, then trim to requested maximum.
    failures = [
        pair
        for pair in pairs
        if pair.id not in selected_ids
        and pair.attribution not in {"router_branch_error", "safety_error"}
        and not pair.hard_filter_amplification
        and (
            not _row_has_relevant_at_cutoff(hard_rows[pair.id], 3)
            or pair.branch_correct is False
        )
    ]
    selected.extend(failures)
    selected = selected[:max(max_case_studies, 0)]
    return [
        {
            **pair.model_dump(mode="json"),
            "hard_candidate_ids": pair.hard_candidate_ids,
            "hard_top5_ids": pair.hard_top5_ids,
            "soft_candidate_ids": pair.soft_candidate_ids,
            "soft_top5_ids": pair.soft_top5_ids,
        }
        for pair in selected
    ]


def _paired_attribution(
    pair: MetadataFilterCaseComparison,
    hard: Mapping[str, Any],
    soft: Mapping[str, Any],
) -> str | None:
    """Apply the protocol attribution priority to a paired case."""

    if _truthy(hard.get("gold_or_dataset_issue")) or _truthy(
        soft.get("gold_or_dataset_issue")
    ):
        return "gold_or_dataset_issue"
    if pair.expected_branch == RagExpectedBranch.SAFETY.value and (
        pair.branch_correct is False
        or _truthy(hard.get("ordinary_rag_bypass_violation"))
        or _truthy(soft.get("ordinary_rag_bypass_violation"))
    ):
        return "safety_error"
    if pair.branch_correct is False:
        return "router_branch_error"
    if _row_no_answer_failure(hard) or _row_no_answer_failure(soft):
        return "no_answer_error"
    # A correctly abstained in-domain no-answer case is a successful outcome,
    # not a missing-gold/dataset failure.  Keep it unattributed in paired
    # evidence; only the branch above records an actual no-answer error.
    if _truthy(hard.get("in_domain_no_answer")) or _truthy(
        soft.get("in_domain_no_answer")
    ):
        return None
    if pair.hard_filter_amplification:
        return "hard_filter_amplification"
    if pair.metadata_correct is False:
        return "router_metadata_error"
    # Only failures receive a retrieval attribution.  Successful rows are
    # omitted from the counters but retain ``None`` in paired evidence.
    if pair.expected_branch == RagExpectedBranch.RAG.value and not pair.gold_ids:
        return "gold_or_dataset_issue"
    if pair.expected_branch == RagExpectedBranch.RAG.value and not _row_has_relevant_at_cutoff(
        hard, 3
    ):
        if not _contains_any(_as_list(hard.get("candidate_ids", [])), pair.gold_ids):
            return "retriever_candidate_miss"
        return "rerank_error"
    return None


def _attribution_counts(
    pairs: Sequence[MetadataFilterCaseComparison],
    rows: Mapping[str, Mapping[str, Any]],
    *,
    arm: Literal["hard", "soft"],
    include_success: bool,
) -> dict[str, int]:
    counts = {name: 0 for name in ERROR_ATTRIBUTION_CATEGORIES}
    for pair in pairs:
        row = rows.get(pair.id, {})
        attribution = _arm_attribution(pair, row, arm=arm)
        if attribution is not None and (include_success or _is_row_failure(row, pair)):
            counts[attribution] += 1
    return counts


def _arm_attribution(
    pair: MetadataFilterCaseComparison,
    row: Mapping[str, Any],
    *,
    arm: Literal["hard", "soft"],
) -> str | None:
    if _truthy(row.get("gold_or_dataset_issue")):
        return "gold_or_dataset_issue"
    if pair.expected_branch == RagExpectedBranch.SAFETY.value and (
        pair.branch_correct is False or _truthy(row.get("ordinary_rag_bypass_violation"))
    ):
        return "safety_error"
    if pair.branch_correct is False:
        return "router_branch_error"
    if _row_no_answer_failure(row):
        return "no_answer_error"
    if pair.metadata_correct is False:
        return "hard_filter_amplification" if (
            pair.hard_filter_amplification and arm == "hard"
        ) else "router_metadata_error"
    if pair.expected_branch == RagExpectedBranch.RAG and not pair.gold_ids:
        return "gold_or_dataset_issue"
    if pair.expected_branch == RagExpectedBranch.RAG and not _row_has_relevant_at_cutoff(row, 3):
        return (
            "retriever_candidate_miss"
            if not _contains_any(_as_list(row.get("candidate_ids", [])), pair.gold_ids)
            else "rerank_error"
        )
    return None


def _metadata_correct(
    *,
    expected_branch: str | None,
    expected_scenario: str | None,
    expected_goals: Sequence[str],
    predicted_branch: str | None,
    predicted_scenario: str | None,
    predicted_goals: Sequence[str],
    row: Mapping[str, Any],
) -> bool | None:
    if expected_branch != RagExpectedBranch.RAG.value:
        return None
    if predicted_branch not in {None, RagExpectedBranch.RAG.value}:
        return False
    if predicted_scenario is None:
        return False
    scenario_ok = predicted_scenario == expected_scenario
    expected = set(expected_goals)
    predicted = set(predicted_goals)
    # Keep the existing RAG V2 goal criterion: at least one expected goal is
    # retained by the Router.  A stricter full-set metric remains available in
    # ``goal_micro_f1`` and must not be conflated with this slice.
    goals_ok = not expected or bool(expected & predicted)
    predicted_stage = row.get("predicted_relationship_stage")
    expected_stage = row.get("stage")
    stage_ok = (
        True
        if predicted_stage in (None, "", "unknown") or expected_stage in (None, "", "unknown")
        else predicted_stage == expected_stage
    )
    return bool(scenario_ok and goals_ok and stage_ok)


def _report_metrics(report: Mapping[str, Any]) -> dict[str, Any]:
    values = {key: report.get(key) for key in _COMPARISON_METRICS}
    oracle_gap = report.get("oracle_gap")
    values["oracle_e2e_degradation_pp"] = (
        oracle_gap.get("routing_degradation_pp")
        if isinstance(oracle_gap, Mapping)
        else report.get("oracle_e2e_degradation_pp")
    )
    # Accept both the old abstention names and the protocol's no-answer names.
    values["abstention_precision"] = report.get(
        "abstention_precision", report.get("no_answer_precision")
    )
    values["abstention_recall"] = report.get(
        "abstention_recall", report.get("no_answer_recall")
    )
    values["no_answer_precision"] = values["abstention_precision"]
    values["no_answer_recall"] = values["abstention_recall"]
    return values


def _report_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        *_COMPARISON_METRICS,
        "case_count",
        "normal_answered_count",
        "in_domain_no_answer_count",
    )
    summary = {key: report.get(key) for key in keys if key in report}
    summary.update(_operational_counts(tuple(_rows_by_id(report).values())))
    return summary


def _rows_by_id(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = report.get("cases", [])
    result: dict[str, Mapping[str, Any]] = {}
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return result
    for row in rows:
        if isinstance(row, Mapping) and row.get("id") is not None:
            result[str(row["id"])] = row
    return result


def _row_for_pair(
    pair: MetadataFilterCaseComparison,
    rows: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any]:
    return rows.get(pair.id, {})


def _operational_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    retrieval_called = sum(bool(row.get("retrieval_called")) for row in rows)
    bypassed = sum(not bool(row.get("retrieval_called")) for row in rows)
    empty_candidates = sum(
        bool(row.get("retrieval_called"))
        and not _as_list(row.get("candidate_ids", []))
        for row in rows
    )
    return {
        "retrieval_called_count": retrieval_called,
        "retrieval_bypassed_count": bypassed,
        "empty_candidate_count": empty_candidates,
    }


def _fixed_parameters_equal(
    hard_config: Mapping[str, Any], soft_config: Mapping[str, Any]
) -> bool:
    keys = tuple(FROZEN_METADATA_FILTER_CONFIG)
    return all(hard_config.get(key) == soft_config.get(key) for key in keys)


def _config_dict(
    config: MetadataFilterExperimentConfig | Mapping[str, Any] | None,
    *,
    hard_filter: bool,
) -> dict[str, Any]:
    if config is None:
        value = dict(FROZEN_METADATA_FILTER_CONFIG)
        value["hard_filter"] = hard_filter
        return value
    if isinstance(config, MetadataFilterExperimentConfig):
        value = config.model_dump(mode="json")
    else:
        value = dict(config)
    value.setdefault("hard_filter", hard_filter)
    return value


def _comparison_conclusion(report: Mapping[str, Any]) -> str:
    amp = report.get("hard_filter_amplification", {})
    count = int(amp.get("hard_filter_amplification_count", 0) or 0)
    rate = amp.get("hard_filter_amplification_rate")
    overall = report.get("overall", {})
    hit_delta = overall.get("hit_at_3", {}).get("delta")
    coverage_delta = overall.get("coverage", {}).get("delta")
    if count:
        return (
            f"在 Router metadata 错误的 answered RAG case 中，Hard Filter 放大了 "
            f"{count} 个 case 的候选/Top-K 丢失（amplification rate={_format(rate)}）；"
            f"切换 Soft Metadata 后 E2E Hit@3 delta={_format(hit_delta)}、"
            f"Coverage delta={_format(coverage_delta)}（delta 定义为 Soft-Hard）。"
        )
    return (
        "在当前配对样本中未观察到满足定义的 hard_filter amplification；"
        f"E2E Hit@3 delta={_format(hit_delta)}、Coverage delta={_format(coverage_delta)}。"
        "该结论仅适用于本次固定配置与数据切片。"
    )


def _delta(soft: Any, hard: Any) -> float | None:
    if isinstance(soft, bool) or isinstance(hard, bool):
        if soft is None or hard is None:
            return None
        return float(soft) - float(hard)
    if not isinstance(soft, (int, float)) or not isinstance(hard, (int, float)):
        return None
    return round(float(soft) - float(hard), 4)


def _mean_optional(rows: Sequence[Mapping[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if isinstance(row.get(key), (int, float))]
    return round(sum(values) / len(values), 4) if values else None


def _row_has_relevant_at_cutoff(row: Mapping[str, Any], cutoff: int) -> bool:
    returned = _as_list(row.get("returned_ids", []))[:cutoff]
    relevant = _as_list(row.get("relevant_ids", []))
    return _contains_any(returned, relevant)


def _row_no_answer_failure(row: Mapping[str, Any]) -> bool:
    return bool(row.get("in_domain_no_answer")) and not bool(row.get("abstained"))


def _is_row_failure(row: Mapping[str, Any], pair: MetadataFilterCaseComparison) -> bool:
    if pair.expected_branch == RagExpectedBranch.RAG.value and pair.gold_ids:
        return not _row_has_relevant_at_cutoff(row, 3)
    if row.get("in_domain_no_answer"):
        return _row_no_answer_failure(row)
    return row.get("branch_correct") is False or bool(
        row.get("ordinary_rag_bypass_violation")
    )


def _branch_correct(row: Mapping[str, Any]) -> bool | None:
    value = row.get("branch_correct")
    return value if isinstance(value, bool) else None


def _diagnostics_available(row: Mapping[str, Any]) -> bool:
    value = row.get("diagnostics_available")
    if value is None:
        # Reports from hand-written tests often omit the flag but provide the
        # candidate pools explicitly; that is sufficient evidence.
        return bool(
            "candidate_ids" in row
            or "nearest_candidate_ids" in row
            or "returned_ids" in row
        )
    return bool(value)


def _as_trace(value: Any) -> list[dict[str, Any]]:
    """Normalize evaluator trace payloads while keeping evidence bounded."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, Mapping):
            result.append(dict(item))
    return result


def _hard_filter_before_dense(trace: Sequence[Mapping[str, Any]]) -> bool | None:
    """Return whether a hard filter is recorded on vector search evidence."""

    vector_events = [event for event in trace if event.get("name") == "rag_vector_search"]
    if not vector_events:
        return None
    details = vector_events[-1].get("details", {})
    if not isinstance(details, Mapping):
        return None
    return bool(details.get("hard_filter"))


def _contains_any(values: Sequence[str], targets: Sequence[str]) -> bool:
    return bool(set(values) & set(targets))


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence):
        return [str(item) for item in value if item is not None]
    return []


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _same_or_unknown(left: Any, right: Any) -> str | None:
    if left is not None:
        return str(left)
    return str(right) if right is not None else None


def _same_list_or_first(left: Any, right: Any) -> list[str]:
    values = _as_list(left)
    return values if values else _as_list(right)


def _truthy(value: Any) -> bool:
    return bool(value)


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    return round(float(numerator) / float(denominator), 4) if denominator else None


def _format(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)

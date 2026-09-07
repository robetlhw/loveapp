import json
from pathlib import Path

import pytest

from loveapp.application.retrieval_query_planner import (
    RetrievalQueryPlanner,
    split_information_needs,
)
from loveapp.core.config import Settings
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.enums import AdviceScenario
from loveapp.domain.knowledge import KnowledgeDocument, KnowledgeSearchResult, RetrievedDocument
from loveapp.evaluation.phase45_query_planner import (
    ContextualRewriteCase,
    MultiQueryCase,
    evaluate_contextual_rewrite,
    evaluate_multiquery,
    load_contextual_rewrite_eval_markdown,
    load_multiquery_eval_markdown,
    ndcg_at_k,
    render_contextual_rewrite_report,
    render_multiquery_report,
    validate_contextual_rewrite_dataset,
    validate_multiquery_dataset,
    validate_phase45_datasets,
)

ROOT = Path(__file__).parents[1]
PHASE35_FIXTURES = ROOT / "evals" / "rag" / "phase3_5"


def test_settings_keep_phase_flags_disabled_by_default() -> None:
    settings = Settings(_env_file=None)
    assert settings.router_v2_enabled is False
    assert settings.contextual_query_rewrite_enabled is False
    assert settings.query_decomposition_enabled is False
    assert settings.max_subqueries == 3


def test_contextual_rewrite_is_conditional_and_uses_history() -> None:
    planner = RetrievalQueryPlanner(contextual_query_rewrite_enabled=True)
    result = planner.rewrite(
        "那我现在怎么办？",
        [
            {"role": "user", "content": "我们刚因为前任联系吵架"},
            {"role": "assistant", "content": "先区分事实和感受"},
        ],
    )
    assert result.rewritten is True
    assert "前任联系吵架" in result.retrieval_query
    assert result.trigger_reason

    standalone = planner.rewrite(
        "第一次邀约被拒，但之后她还主动聊天，我还能再约吗？",
        ["最近工作挺忙的"],
    )
    assert standalone.rewritten is False
    assert standalone.retrieval_query == standalone.raw_query


def test_decomposition_requires_explicit_independent_need_markers() -> None:
    query = (
        "我现在有两个问题想一起理清：第一，我们职业发展速度差很多。 "
        "第二，她情绪上来我也会立刻反击。 这两件事分别怎么处理？"
    )
    assert len(split_information_needs(query)) == 2
    single = "第一次邀约没成功，但之后聊天还是主动，我还能再约吗？"
    assert split_information_needs(single) == [single]


def test_planner_matches_specialised_fixture_counts() -> None:
    planner = RetrievalQueryPlanner(
        contextual_query_rewrite_enabled=True,
        query_decomposition_enabled=True,
    )
    rewrite_cases = load_contextual_rewrite_eval_markdown(
        PHASE35_FIXTURES / "loveapp_contextual_rewrite_eval_dev_v1.md"
    )
    assert all(
        planner.plan(case.current_query, case.history).rewritten == case.rewrite_required
        for case in rewrite_cases
    )
    multi_cases = load_multiquery_eval_markdown(
        PHASE35_FIXTURES / "loveapp_multiquery_eval_dev_v1.md"
    )
    assert all(
        planner.plan(case.query).subquery_count == case.expected_subquery_count
        for case in multi_cases
    )


@pytest.mark.asyncio
async def test_async_callbacks_are_supported_and_ordered_rewrite_then_decompose() -> None:
    calls: list[str] = []

    async def rewrite(query: str, history: list[str]) -> str:
        calls.append("rewrite")
        return f"{history[0]} {query}"

    async def decompose(query: str) -> list[str]:
        calls.append("decompose")
        return ["need one", "need two"]

    planner = RetrievalQueryPlanner(
        contextual_query_rewrite_enabled=True,
        query_decomposition_enabled=True,
        rewrite_fn=rewrite,
        decompose_fn=decompose,
    )
    plan = await planner.aplan("那怎么办？", ["我们发生了冲突"])
    assert calls == ["rewrite", "decompose"]
    assert plan.rewritten is True
    assert plan.subqueries == ["need one", "need two"]


@pytest.mark.asyncio
async def test_async_callbacks_respect_disabled_feature_flags() -> None:
    calls: list[str] = []

    async def rewrite(query: str, history: list[str]) -> str:
        del query, history
        calls.append("rewrite")
        return "should not run"

    async def decompose(query: str) -> list[str]:
        del query
        calls.append("decompose")
        return ["should", "not run"]

    planner = RetrievalQueryPlanner(
        contextual_query_rewrite_enabled=False,
        query_decomposition_enabled=False,
        rewrite_fn=rewrite,
        decompose_fn=decompose,
    )
    plan = await planner.aplan("完整的单一问题", ["历史消息"])

    assert calls == []
    assert plan.rewritten is False
    assert plan.decomposed is False
    assert plan.subqueries == ["完整的单一问题"]


@pytest.mark.asyncio
async def test_async_callback_failures_use_deterministic_fail_safe_fallbacks() -> None:
    async def failing_rewrite(query: str, history: list[str]) -> str:
        del query, history
        raise RuntimeError("rewrite service unavailable")

    async def failing_decompose(query: str) -> list[str]:
        del query
        raise RuntimeError("decomposer unavailable")

    rewrite_planner = RetrievalQueryPlanner(
        contextual_query_rewrite_enabled=True,
        rewrite_fn=failing_rewrite,
    )
    rewritten = await rewrite_planner.aplan(
        "那我现在怎么办？",
        ["我们刚因为前任联系吵架"],
    )
    assert rewritten.rewritten is True
    assert rewritten.retrieval_query.endswith(rewritten.raw_query)
    assert rewritten.history_window[0] in rewritten.retrieval_query

    decomposition_query = next(
        case.query
        for case in load_multiquery_eval_markdown(
            PHASE35_FIXTURES / "loveapp_multiquery_eval_dev_v1.md"
        )
        if case.decompose_required and case.expected_subquery_count == 2
    )
    decomposition_planner = RetrievalQueryPlanner(
        query_decomposition_enabled=True,
        decompose_fn=failing_decompose,
    )
    decomposed = await decomposition_planner.aplan(decomposition_query)
    assert decomposed.decomposed is True
    assert decomposed.subquery_count == 2


class _FakeRetriever:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.documents = {
            "need one": ["a", "shared"],
            "need two": ["b", "shared"],
        }

    async def search_detailed(self, query, *, filters, limit, trace):
        del filters, limit, trace
        self.calls.append(query)
        values = [
            RetrievedDocument(
                document=KnowledgeDocument(
                    id=document_id,
                    title=document_id,
                    scenario=AdviceScenario.CONFLICT,
                    question=document_id,
                ),
                score=1.0 - index * 0.1,
                base_score=1.0 - index * 0.1,
            )
            for index, document_id in enumerate(self.documents.get(query, []))
        ]
        return KnowledgeSearchResult(
            returned=values,
            candidates=values,
            nearest_candidates=values,
            reranked_candidates=values,
        )


@pytest.mark.asyncio
async def test_multiquery_retrieval_merges_by_document_id() -> None:
    retriever = _FakeRetriever()
    planner = RetrievalQueryPlanner(
        query_decomposition_enabled=True,
        decompose_fn=lambda query: ["need one", "need two"],
    )
    result = await planner.retrieve(retriever, "two needs", limit=5)
    assert retriever.calls == ["need one", "need two"]
    assert set(result.merged_candidate_ids) == {"a", "b", "shared"}
    assert result.final_top_k
    assert result.duplicate_candidate_ratio > 0


def test_phase45_dataset_lint_checks_kb_and_cross_split_contract() -> None:
    rewrite_dev = load_contextual_rewrite_eval_markdown(
        PHASE35_FIXTURES / "loveapp_contextual_rewrite_eval_dev_v1.md"
    )
    rewrite_test = load_contextual_rewrite_eval_markdown(
        PHASE35_FIXTURES / "loveapp_contextual_rewrite_eval_test_v1.md"
    )
    multi_dev = load_multiquery_eval_markdown(
        PHASE35_FIXTURES / "loveapp_multiquery_eval_dev_v1.md"
    )
    multi_test = load_multiquery_eval_markdown(
        PHASE35_FIXTURES / "loveapp_multiquery_eval_test_v1.md"
    )
    knowledge_ids = sorted(
        {
            document_id
            for case in [*rewrite_dev, *rewrite_test, *multi_dev, *multi_test]
            for document_id in case.relevant_ids
        }
    )

    assert validate_multiquery_dataset(
        multi_dev,
        knowledge_ids=knowledge_ids,
    )["passed"] is True
    contextual_lint = validate_contextual_rewrite_dataset(
        rewrite_dev,
        knowledge_ids=knowledge_ids,
    )
    assert contextual_lint["passed"] is False
    assert contextual_lint["details"]["standalone_control_equivalent"] is True
    assert contextual_lint["details"]["unknown_relevant_ids"] == []

    combined = validate_phase45_datasets(
        rewrite_dev,
        rewrite_test,
        multi_dev,
        multi_test,
        knowledge_ids=knowledge_ids,
        strict_cross_split=False,
    )
    assert len(combined["cross_split"]["phase4"]["dev_test_query_overlap"]) == 6
    assert any("phase4 Dev/Test queries overlap" in item for item in combined["warnings"])


def test_phase45_dataset_lint_flags_unknown_relevant_ids() -> None:
    cases = load_multiquery_eval_markdown(
        PHASE35_FIXTURES / "loveapp_multiquery_eval_test_v1.md"
    )
    lint = validate_multiquery_dataset(cases, knowledge_ids=[])
    assert lint["passed"] is False
    assert lint["details"]["unknown_relevant_ids"]


@pytest.mark.asyncio
async def test_outer_rag_trace_can_store_multiquery_diagnostics() -> None:
    retriever = _FakeRetriever()
    planner = RetrievalQueryPlanner(
        query_decomposition_enabled=True,
        decompose_fn=lambda query: ["need one", "need two"],
    )
    trace = ExecutionTrace()
    with trace.measure("rag_retrieval") as details:
        result = await planner.retrieve(retriever, "two needs", limit=5, trace=trace)
        details["per_subquery_candidate_ids"] = json.dumps(
            result.per_subquery_candidate_ids,
            ensure_ascii=False,
        )
        details["per_subquery_scores"] = json.dumps(
            result.per_subquery_scores,
            ensure_ascii=False,
        )
        details["merged_candidate_ids"] = json.dumps(
            result.merged_candidate_ids,
            ensure_ascii=False,
        )
        details["final_top_k"] = json.dumps(result.final_top_k, ensure_ascii=False)

    outer = next(item for item in trace.snapshot() if item.name == "rag_retrieval")
    assert json.loads(str(outer.details["per_subquery_candidate_ids"])) == [
        ["a", "shared"],
        ["b", "shared"],
    ]
    assert set(json.loads(str(outer.details["merged_candidate_ids"]))) == {
        "a",
        "b",
        "shared",
    }
    assert json.loads(str(outer.details["final_top_k"]))


@pytest.mark.asyncio
async def test_phase45_evaluators_emit_trigger_metrics() -> None:
    rewrite_cases = load_contextual_rewrite_eval_markdown(
        PHASE35_FIXTURES / "loveapp_contextual_rewrite_eval_test_v1.md"
    )
    rewrite_report = await evaluate_contextual_rewrite(rewrite_cases)
    assert rewrite_report["case_count"] == 18
    assert set(rewrite_report["trigger"]) == {"precision", "recall", "f1"}
    assert rewrite_report["retrieval_executed"] is False

    multi_cases = load_multiquery_eval_markdown(
        PHASE35_FIXTURES / "loveapp_multiquery_eval_test_v1.md"
    )
    multi_report = await evaluate_multiquery(multi_cases)
    assert multi_report["case_count"] == 18
    assert multi_report["subquery_count_exact_accuracy"] == 1.0
    assert multi_report["retrieval_executed"] is False


def test_phase45_binary_ndcg_handles_rank_and_empty_relevance() -> None:
    assert ndcg_at_k(["best", "noise"], ["best"], 2) == 1.0
    assert ndcg_at_k(["noise", "best"], ["best"], 2) == pytest.approx(0.6309)
    assert ndcg_at_k(["best"], [], 5) == 0.0


def test_phase45_markdown_renderers_include_required_diagnostics() -> None:
    contextual = render_contextual_rewrite_report(
        {
            "case_count": 1,
            "trigger": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
            "query_drift_rate": 0.0,
            "slices": {
                "length_bucket": {
                    "short": {
                        "count": 1,
                        "trigger": {"f1": 1.0},
                        "query_drift_rate": 0.0,
                    }
                }
            },
            "error_attribution": {"query_drift": 0},
            "top_failures": [],
            "latency": {"count": 1, "mean": 1.0, "p50": 1.0, "p95": 1.0},
        }
    )
    multi = render_multiquery_report(
        {
            "case_count": 1,
            "trigger": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
            "subquery_count_exact_accuracy": 1.0,
            "retrieval": {},
            "single_intent_control": {},
            "latency_overhead": {},
            "slices": {
                "query_type": {
                    "single": {
                        "count": 1,
                        "trigger": {"f1": 1.0},
                        "subquery_quality_rate": 1.0,
                    }
                }
            },
            "error_attribution": {"candidate_miss": 0},
            "top_failures": [],
            "latency": {"count": 1, "mean": 1.0, "p50": 1.0, "p95": 1.0},
        }
    )

    for rendered in (contextual, multi):
        assert "## Slices" in rendered
        assert "## Error attribution" in rendered
        assert "## Top failures" in rendered
        assert "## Latency" in rendered


@pytest.mark.asyncio
async def test_contextual_candidate_recall_uses_pre_rerank_candidate_pool() -> None:
    class CandidatePoolRetriever:
        async def search_detailed(self, query, *, filters, limit, trace):
            del filters, limit, trace
            candidates = [
                RetrievedDocument(
                    document=KnowledgeDocument(
                        id="gold",
                        title="gold",
                        scenario=AdviceScenario.CONFLICT,
                        question="gold",
                    ),
                    score=0.9,
                    base_score=0.9,
                ),
                RetrievedDocument(
                    document=KnowledgeDocument(
                        id="noise",
                        title="noise",
                        scenario=AdviceScenario.CONFLICT,
                        question="noise",
                    ),
                    score=0.8,
                    base_score=0.8,
                ),
            ]
            # Simulate reranking dropping the gold document from the returned
            # top-k while retaining it in the pre-rerank candidate pool.
            return KnowledgeSearchResult(
                returned=[candidates[1]],
                candidates=candidates,
                nearest_candidates=candidates,
                reranked_candidates=[candidates[1]],
            )

    case = ContextualRewriteCase(
        id="candidate_pool",
        query_type="standalone_control",
        difficulty="easy",
        length_bucket="short",
        expected_branch="rag",
        current_query="gold",
        rewrite_required=False,
        expected_standalone_query="gold",
        relevant_ids=["gold"],
    )
    report = await evaluate_contextual_rewrite(
        [case],
        planner=RetrievalQueryPlanner(contextual_query_rewrite_enabled=True),
        retriever=CandidatePoolRetriever(),
    )
    row = report["cases"][0]
    assert row["raw"]["hit_at_1"] is False
    assert row["raw"]["candidate_recall"] == 1.0


@pytest.mark.asyncio
async def test_multiquery_retrieval_reports_ranking_merge_and_single_control_metrics() -> None:
    class MetricsRetriever:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def search_detailed(self, query, *, filters, limit, trace):
            del filters, limit, trace
            self.calls.append(query)
            ids_by_query = {
                "need one": [("need_one_doc", 1.0), ("shared_doc", 0.2)],
                "need two": [("need_two_doc", 1.0), ("shared_doc", 0.8)],
                "two needs": [("noise_doc", 0.9)],
                "single": [("single_doc", 1.0)],
            }
            values = [
                RetrievedDocument(
                    document=KnowledgeDocument(
                        id=document_id,
                        title=document_id,
                        scenario=AdviceScenario.CONFLICT,
                        question=document_id,
                    ),
                    score=score,
                    base_score=score,
                )
                for document_id, score in ids_by_query.get(query, [])
            ]
            return KnowledgeSearchResult(
                returned=values,
                candidates=values,
                nearest_candidates=values,
                reranked_candidates=values,
            )

    multi_case = MultiQueryCase(
        id="multi",
        query_type="two_intents",
        difficulty="hard",
        length_bucket="long",
        expected_branch="rag",
        expected_scenario="conflict",
        expected_goals=["understand"],
        query="two needs",
        decompose_required=True,
        expected_subquery_count=2,
        expected_subqueries=["need one", "need two"],
        relevant_ids=["need_one_doc", "need_two_doc"],
        need_coverage_groups={
            "Need1": ["need_one_doc"],
            "Need2": ["need_two_doc"],
        },
    )
    single_case = MultiQueryCase(
        id="single",
        query_type="single_intent_control",
        difficulty="medium",
        length_bucket="medium",
        expected_branch="rag",
        expected_scenario="conflict",
        expected_goals=["understand"],
        query="single",
        decompose_required=False,
        expected_subquery_count=1,
        expected_subqueries=["single"],
        relevant_ids=["single_doc"],
        need_coverage_groups={"Need1": ["single_doc"]},
    )
    planner = RetrievalQueryPlanner(
        query_decomposition_enabled=True,
        decompose_fn=lambda query: ["need one", "need two"]
        if query == "two needs"
        else [query],
    )
    retriever = MetricsRetriever()

    report = await evaluate_multiquery(
        [multi_case, single_case],
        planner=planner,
        retriever=retriever,
        top_k=5,
    )

    multi_row, single_row = report["cases"]
    assert multi_row["mrr"] == 1.0
    assert multi_row["ndcg_at_5"] == 1.0
    assert multi_row["merged_candidate_count"] == 3
    assert report["retrieval"]["mrr"] == 1.0
    assert report["retrieval"]["ndcg_at_5"] == 1.0
    assert report["retrieval"]["merged_candidate_count"] == 2.0
    assert report["retrieval_executed"] is True
    assert "single_intent_baseline" in single_row
    assert single_row["single_intent_baseline"]["hit_at_3"] is True
    assert single_row["hit_at_3_degradation"] == 0.0
    assert report["single_intent_control"]["hit_at_3_degradation"] == 0.0
    assert "latency_overhead_ms" in report["single_intent_control"]
    assert "latency_overhead" in report
    assert report["latency_overhead"]["count"] == 2
    # The current arm makes one call per subquery; each control run makes one
    # additional call with decomposition disabled.
    assert retriever.calls.count("need one") == 1
    assert retriever.calls.count("need two") == 1
    assert retriever.calls.count("two needs") == 1
    assert retriever.calls.count("single") == 2

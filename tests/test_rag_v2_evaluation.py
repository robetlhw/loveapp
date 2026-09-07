from pathlib import Path

import pytest

from loveapp.adapters.knowledge.loader import load_knowledge_path
from loveapp.application.scenario_policy import default_scenario_policy_registry
from loveapp.bootstrap import load_seed_documents
from loveapp.domain.enums import AdviceGoal, AdviceScenario, TaskType
from loveapp.domain.knowledge import KnowledgeSearchResult, RetrievedDocument
from loveapp.domain.routing import RouteResult
from loveapp.evaluation.rag_v2 import (
    RagEvalResult,
    build_e2e_executor,
    build_e2e_rule_executor,
    compare_oracle_and_e2e,
    evaluate_rag_targets,
    evaluate_rag_v2,
    load_rag_eval_markdown,
    ndcg_at_k,
    parse_rag_eval_markdown,
    render_rag_report,
    retrieval_metrics,
    validate_rag_v2_dataset,
)

ROOT = Path(__file__).parents[1]


def _retrieved(document_id: str, *, score: float = 0.9) -> RetrievedDocument:
    document = load_seed_documents()[0].model_copy(update={"id": document_id})
    return RetrievedDocument(document=document, score=score)


def _case_markdown(
    *,
    case_id: str = "rag_v2_dev_001",
    branch: str = "rag",
    no_answer: bool = False,
    scope: str | None = None,
    relevant: str = "doc_a, doc_b",
    grades: str = "doc_a=3, doc_b=2, doc_c=1",
) -> str:
    scope_line = f"**NoAnswerScope:** {scope}\n" if scope else ""
    return f"""# Dataset
---
## {case_id}

**QueryType:** multi_goal
**Difficulty:** hard
**ExpectedBranch:** {branch}
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** boundary
**RelationshipStage:** dating
**ExpectedGoals:** communicate, repair
**ExpectedRiskLevel:** normal
**NoAnswer:** {str(no_answer).lower()}
{scope_line}**Query:** 我应该先沟通还是先暂停？
**RelevantIDs:** {relevant}
**GradedRelevance:** {grades}
**HardNegativeIDs:** doc_z
"""


def test_v2_eval_parser_round_trip() -> None:
    case = parse_rag_eval_markdown(_case_markdown())[0]

    assert case.id == "rag_v2_dev_001"
    assert [scenario.value for scenario in case.expected_secondary_scenarios] == ["boundary"]
    assert [goal.value for goal in case.expected_goals] == ["communicate", "repair"]
    assert case.relevant_ids == ["doc_a", "doc_b"]
    assert case.graded_relevance == {"doc_a": 3, "doc_b": 2, "doc_c": 1}


def test_eval_parser_enforces_no_answer_contract() -> None:
    with pytest.raises(ValueError, match="NoAnswerScope"):
        parse_rag_eval_markdown(
            _case_markdown(no_answer=True, relevant="[]", grades="{}")
        )

    case = parse_rag_eval_markdown(
        _case_markdown(
            branch="out_of_scope",
            no_answer=True,
            scope="out_of_domain",
            relevant="[]",
            grades="{}",
        )
    )[0]
    assert case.no_answer_scope == "out_of_domain"


def test_eval_parser_accepts_explicit_null_no_answer_scope_for_answered_case() -> None:
    case = parse_rag_eval_markdown(
        _case_markdown().replace(
            "**Query:** 我应该先沟通还是先暂停？",
            "**NoAnswerScope:** null\n**Query:** 我应该先沟通还是先暂停？",
        )
    )[0]

    assert case.no_answer_scope is None


def test_eval_parser_rejects_duplicate_graded_relevance_ids() -> None:
    with pytest.raises(ValueError, match="GradedRelevance 文档 ID 重复"):
        parse_rag_eval_markdown(
            _case_markdown(grades="doc_a=3, doc_a=2, doc_b=2")
        )


def test_true_recall_for_multiple_relevant_documents() -> None:
    metrics = retrieval_metrics(
        ["doc_a", "other", "doc_b"],
        relevant_ids=["doc_a", "doc_b", "doc_c"],
        graded_relevance={"doc_a": 3, "doc_b": 2, "doc_c": 2},
        top_k=5,
    )

    assert metrics["hit_at_3"] is True
    assert metrics["recall_at_3"] == pytest.approx(2 / 3)
    assert metrics["precision_at_3"] == pytest.approx(2 / 3)


def test_ndcg_uses_graded_relevance() -> None:
    assert ndcg_at_k(["best", "second"], {"best": 3, "second": 2}, 2) == 1
    assert ndcg_at_k(["second", "best"], {"best": 3, "second": 2}, 2) < 1


def test_hard_negative_leakage() -> None:
    metrics = retrieval_metrics(
        ["doc_a", "doc_z"],
        relevant_ids=["doc_a"],
        graded_relevance={"doc_a": 3, "doc_z": 0},
        hard_negative_ids=["doc_z"],
    )
    assert metrics["hard_negative_leakage_at_3"] is True


async def test_in_domain_no_answer_metrics_exclude_ood() -> None:
    answered = parse_rag_eval_markdown(_case_markdown())[0]
    uncovered = parse_rag_eval_markdown(
        _case_markdown(
            case_id="rag_v2_dev_002",
            no_answer=True,
            scope="in_domain_uncovered",
            relevant="[]",
            grades="{}",
        )
    )[0]
    ood = parse_rag_eval_markdown(
        _case_markdown(
            case_id="rag_v2_dev_003",
            branch="out_of_scope",
            no_answer=True,
            scope="out_of_domain",
            relevant="[]",
            grades="{}",
        )
    )[0]

    document = load_seed_documents()[0].model_copy(update={"id": "doc_a"})

    async def executor(case):
        returned = [] if case.no_answer else [RetrievedDocument(document=document, score=0.9)]
        return RagEvalResult(returned=returned, candidates=returned)

    report = await evaluate_rag_v2(
        [answered, uncovered, ood], mode="retriever", executor=executor
    )

    assert report["case_count"] == 2
    assert report["no_answer_f1"] == 1
    assert report["false_retrieval_rate"] == 0
    assert report["coverage"] == 1
    assert report["out_of_domain_count"] == 0


def test_full_v2_dataset_integrity() -> None:
    documents = load_knowledge_path(ROOT / "knowledge/loveapp_rag_knowledge_base_v2.md")
    dev = load_rag_eval_markdown(ROOT / "evals/rag/cases_v2_dev.md")
    test = load_rag_eval_markdown(ROOT / "evals/rag/cases_v2_test.md")

    result = validate_rag_v2_dataset(documents, dev, test)

    assert result["passed"], result["errors"]
    assert result["counts"]["knowledge"] == 500
    assert result["counts"]["dev"] == 300
    assert result["counts"]["test"] == 200


async def test_e2e_safety_case_bypasses_ordinary_rag() -> None:
    case = parse_rag_eval_markdown(
        _case_markdown(
            branch="safety",
            relevant="doc_a",
            grades="doc_a=3",
        ).replace(
            "**Query:** 我应该先沟通还是先暂停？",
            "**Query:** 对方威胁要伤害我，我现在该怎么办？",
        ).replace("**ExpectedRiskLevel:** normal", "**ExpectedRiskLevel:** high")
    )[0]

    class FailIfCalledRetriever:
        async def search(self, *args, **kwargs):
            raise AssertionError("安全分支不应调用普通 RAG")

    executor = build_e2e_rule_executor(FailIfCalledRetriever())
    result = await executor(case)

    assert result.predicted_branch == "safety"
    assert result.retrieval_called is False


async def test_e2e_uses_scenario_policy_to_build_filters() -> None:
    case = parse_rag_eval_markdown(
        _case_markdown().replace(
            "**Query:** 我应该先沟通还是先暂停？",
            "**Query:** 我和对象吵架后应该怎么沟通修复？",
        )
    )[0]
    captured = {}

    class CapturingRetriever:
        async def search_detailed(self, query, *, filters, limit, trace):
            captured.update(query=query, filters=filters, limit=limit, trace=trace)
            return KnowledgeSearchResult()

    result = await build_e2e_rule_executor(CapturingRetriever())(case)

    assert result.predicted_branch == "rag"
    assert result.predicted_scenario == "conflict"
    assert captured["filters"].scenario.value == "conflict"
    assert captured["filters"].scenario_weights
    assert captured["limit"] == 5


async def test_e2e_mode_never_falls_back_to_oracle_retriever() -> None:
    with pytest.raises(ValueError, match="E2E 模式必须"):
        await evaluate_rag_v2([], mode="e2e", retriever=object())


async def test_rule_router_executor_is_diagnostic_only() -> None:
    executor = build_e2e_rule_executor(object())

    with pytest.raises(ValueError, match="diagnostic-only"):
        await evaluate_rag_v2([], mode="e2e", executor=executor)


async def test_real_e2e_executor_runs_router_policy_and_retriever() -> None:
    case = parse_rag_eval_markdown(_case_markdown())[0]
    captured = {}

    class FakeRouter:
        async def route(self, route_input):
            captured["route_input"] = route_input
            return RouteResult(
                normalized_query=route_input.latest_query,
                task_type=TaskType.RELATIONSHIP_ADVICE,
                task_confidence=0.95,
                primary_goal=AdviceGoal.COMMUNICATE,
                secondary_goals=[AdviceGoal.REPAIR],
                primary_scenario=AdviceScenario.CONFLICT,
                secondary_scenarios=[AdviceScenario.BOUNDARY],
            )

    class CapturingRegistry:
        def resolve(self, primary_scenario, secondary_scenarios, primary_goal, secondary_goals):
            captured["policy_args"] = (
                primary_scenario,
                secondary_scenarios,
                primary_goal,
                secondary_goals,
            )
            return default_scenario_policy_registry().resolve(
                primary_scenario,
                secondary_scenarios,
                primary_goal,
                secondary_goals,
            )

    class CapturingRetriever:
        async def search_detailed(self, query, *, filters, limit, trace):
            captured.update(query=query, filters=filters, limit=limit, trace=trace)
            returned = [_retrieved("doc_a")]
            return KnowledgeSearchResult(
                returned=returned,
                nearest_candidates=returned,
                candidates=returned,
                reranked_candidates=returned,
            )

    executor = build_e2e_executor(
        FakeRouter(),
        CapturingRetriever(),
        policy_registry=CapturingRegistry(),
    )
    result = await executor(case)

    assert captured["route_input"].latest_query == case.query
    assert captured["policy_args"] == (
        AdviceScenario.CONFLICT,
        [AdviceScenario.BOUNDARY],
        AdviceGoal.COMMUNICATE,
        [AdviceGoal.REPAIR],
    )
    assert captured["filters"].scenario == AdviceScenario.CONFLICT
    assert captured["filters"].goal == AdviceGoal.COMMUNICATE
    assert captured["filters"].goals == [AdviceGoal.REPAIR]
    assert captured["filters"].scenario_weights
    assert captured["limit"] == 5
    assert result.predicted_branch == "rag"
    assert result.diagnostics_available is True
    assert result.retrieval_duration_ms is not None


async def test_search_only_retriever_leaves_pool_metrics_unavailable() -> None:
    case = parse_rag_eval_markdown(_case_markdown(relevant="doc_a", grades="doc_a=3"))[0]

    class SearchOnlyRetriever:
        async def search(self, query, *, filters, limit, trace):
            return [_retrieved("doc_a")]

    report = await evaluate_rag_v2([case], retriever=SearchOnlyRetriever())
    row = report["cases"][0]

    assert row["diagnostics_available"] is False
    assert row["nearest_candidate_ids"] == []
    assert row["candidate_ids"] == []
    assert row["reranked_candidate_ids"] == []
    assert report["candidate_recall"] is None
    assert report["rerank_lift"] is None
    assert report["metric_denominators"]["candidate_recall"] == 0
    assert "| `candidate_recall` | N/A | 0 |" in render_rag_report(report)


def test_render_report_includes_baseline_comparison_when_present() -> None:
    markdown = render_rag_report(
        {
            "mode": "retriever",
            "case_count": 1,
            "baseline_comparison": {
                "hit_at_3": {"baseline": 0.5, "frozen": 0.75},
                "no_answer_f1": {"baseline": None, "frozen": 1.0},
            },
            "cases": [],
        }
    )

    assert "## Baseline vs frozen" in markdown
    assert "| `hit_at_3` | 0.5000 | 0.7500 |" in markdown
    assert "| `no_answer_f1` | N/A | 1.0000 |" in markdown


async def test_e2e_wrong_route_does_not_receive_abstention_credit() -> None:
    answered = parse_rag_eval_markdown(
        _case_markdown(relevant="doc_a", grades="doc_a=3")
    )[0]
    uncovered = parse_rag_eval_markdown(
        _case_markdown(
            case_id="rag_v2_dev_002",
            no_answer=True,
            scope="in_domain_uncovered",
            relevant="[]",
            grades="{}",
        )
    )[0]

    async def executor(case):
        if case.no_answer:
            return RagEvalResult(
                predicted_branch="out_of_scope",
                retrieval_called=False,
            )
        return RagEvalResult(
            returned=[_retrieved("doc_a")],
            predicted_branch="rag",
            retrieval_called=True,
        )

    report = await evaluate_rag_v2([answered, uncovered], mode="e2e", executor=executor)

    assert report["abstention_recall"] == 0
    assert report["abstention_precision"] is None
    assert report["no_answer_f1"] == 0
    assert report["false_retrieval_rate"] == 0
    assert report["coverage"] == 1
    assert report["no_answer_confusion_matrix"] == {
        "true_positive": 0,
        "false_negative": 1,
        "false_positive": 0,
        "true_negative": 1,
        "actual_positive": 1,
        "actual_negative": 1,
        "predicted_positive": 0,
        "predicted_negative": 2,
        "denominator": 2,
    }


async def test_latency_uses_first_actual_retrieval_as_cold_sample() -> None:
    safety = parse_rag_eval_markdown(
        _case_markdown(
            case_id="rag_v2_dev_001",
            branch="safety",
            relevant="doc_a",
            grades="doc_a=3",
        )
    )[0]
    cold = parse_rag_eval_markdown(
        _case_markdown(
            case_id="rag_v2_dev_002",
            relevant="doc_a",
            grades="doc_a=3",
        )
    )[0]
    warm = parse_rag_eval_markdown(
        _case_markdown(
            case_id="rag_v2_dev_003",
            relevant="doc_a",
            grades="doc_a=3",
        )
    )[0]

    async def executor(case):
        if case.expected_branch.value == "safety":
            return RagEvalResult(
                duration_ms=500,
                predicted_branch="safety",
                retrieval_called=False,
            )
        is_cold = case.id == cold.id
        return RagEvalResult(
            returned=[_retrieved("doc_a")],
            duration_ms=300 if is_cold else 40,
            retrieval_duration_ms=100 if is_cold else 20,
            trace=[
                {
                    "name": "rag_query_embedding",
                    "duration_ms": 80 if is_cold else 7,
                }
            ],
            predicted_branch="rag",
        )

    report = await evaluate_rag_v2([safety, cold, warm], mode="e2e", executor=executor)

    assert report["cold_start_ms"] == 100
    assert report["warm_latency_ms"] == {
        "sample_count": 1,
        "mean": 20,
        "p50": 20,
        "p90": 20,
        "p95": 20,
    }
    assert report["stage_latency_ms"]["rag_query_embedding"] == {
        "sample_count": 1,
        "p50": 7,
        "p95": 7,
    }


async def test_failure_attribution_uses_top_three_cutoff() -> None:
    case = parse_rag_eval_markdown(
        _case_markdown(relevant="doc_a", grades="doc_a=3")
    )[0]
    returned = [
        _retrieved("doc_b"),
        _retrieved("doc_c"),
        _retrieved("doc_d"),
        _retrieved("doc_a"),
    ]

    async def executor(case):
        return RagEvalResult(
            returned=returned,
            nearest_candidates=returned,
            candidates=returned,
            reranked_candidates=returned,
            predicted_branch="rag",
            diagnostics_available=True,
        )

    report = await evaluate_rag_v2([case], executor=executor, top_k=5)

    assert report["hit_at_3"] == 0
    assert report["hit_at_5"] == 1
    assert report["error_attribution_cutoff"] == 3
    assert report["top_failures"][0]["error_attribution"] == "rerank_error"
    markdown = render_rag_report(report)
    assert "relevant=[\"doc_a\"]" in markdown
    assert "nearest=" in markdown


def test_oracle_gap_has_an_explicit_target_gate() -> None:
    gap = compare_oracle_and_e2e({"hit_at_3": 0.92}, {"hit_at_3": 0.85})
    passing_report = {
        "mode": "e2e",
        "hit_at_1": 0.8,
        "hit_at_3": 0.92,
        "hit_at_5": 0.96,
        "mrr": 0.86,
        "ndcg_at_5": 0.9,
        "hard_confusion_hit_at_3": 0.85,
        "hard_negative_leakage_at_3": 0.1,
        "no_answer_f1": 0.85,
        "false_retrieval_rate": 0.1,
        "coverage": 0.95,
        "high_safety_recall": 0.98,
        "safety_rag_bypass_violation_rate": 0,
        "oracle_gap": gap,
    }

    assert gap["routing_degradation_pp"] == 7
    assert gap["target_passed"] is True
    assert evaluate_rag_targets(passing_report)["passed"] is True

    passing_report["oracle_gap"] = compare_oracle_and_e2e(
        {"hit_at_3": 0.95}, {"hit_at_3": 0.85}
    )
    targets = evaluate_rag_targets(passing_report)
    assert targets["checks"]["routing_degradation_pp"] is False
    assert targets["passed"] is False

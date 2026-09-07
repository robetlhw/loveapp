import asyncio

import pytest

from loveapp.bootstrap import load_seed_documents
from loveapp.evaluation.rag_v2 import parse_rag_eval_markdown
from loveapp.evaluation.rag_v2_sweep import _constraints_pass, run_rag_v2_dev_sweep


class TinyEmbeddingProvider:
    model_name = "tiny"
    is_ready = True

    def start_warmup(self):
        return asyncio.create_task(self.warmup())

    async def warmup(self) -> None:
        return None

    async def dimension(self) -> int:
        return 2

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]

    async def aclose(self) -> None:
        return None


async def test_dev_sweep_runs_all_prescribed_phases() -> None:
    document = load_seed_documents()[0].model_copy(update={"id": "doc_a"})
    case = parse_rag_eval_markdown(
        """
## rag_v2_dev_001
**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** acquaintance
**ExpectedGoals:** initiate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 怎样自然开始聊天？
**RelevantIDs:** doc_a
**GradedRelevance:** doc_a=3
**HardNegativeIDs:** []
"""
    )[0]

    report = await run_rag_v2_dev_sweep(
        [document],
        [case],
        embedding_provider=TinyEmbeddingProvider(),
    )

    assert report["dataset"] == "dev"
    assert set(report["phases"]) == {
        "rag_min_score",
        "candidate_limit",
        "top_k",
        "reranker",
        "retrieval_text",
        "rerank_weights",
        "metadata_filter",
    }
    assert report["raw_query_only"] is True
    assert report["frozen_config"]["retrieval_text_mode"] in {
        "question",
        "question_variants",
        "question_variants_answer",
        "full",
    }


def test_dev_sweep_constraints_ignore_unavailable_slices() -> None:
    assert _constraints_pass(
        {
            "false_retrieval_rate": None,
            "coverage": 1.0,
            "hard_negative_leakage_at_3": None,
        }
    )


async def test_dev_sweep_rejects_test_cases() -> None:
    document = load_seed_documents()[0]
    case = parse_rag_eval_markdown(
        """
## rag_v2_test_001
**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** acquaintance
**ExpectedGoals:** initiate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 怎样自然开始聊天？
**RelevantIDs:** kb_001
**GradedRelevance:** kb_001=3
**HardNegativeIDs:** []
"""
    )[0]

    with pytest.raises(ValueError, match="Dev split only"):
        await run_rag_v2_dev_sweep(
            [document],
            [case],
            embedding_provider=TinyEmbeddingProvider(),
        )

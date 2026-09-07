from loveapp.adapters.knowledge.scoring import RerankConfig, RerankerMode, soft_rerank
from loveapp.bootstrap import load_seed_documents
from loveapp.domain.enums import AdviceScenario
from loveapp.domain.knowledge import KnowledgeFilters, RetrievalTextMode, RetrievedDocument


def test_reranker_ablation_modes_are_explicit() -> None:
    document = load_seed_documents()[0]
    match = RetrievedDocument(document=document, score=0.5, base_score=0.5)
    filters = KnowledgeFilters(scenario=document.scenario)

    vector_only = soft_rerank(
        document.question,
        [match],
        filters,
        RerankConfig(mode=RerankerMode.VECTOR_ONLY),
    )[0]
    lexical = soft_rerank(
        document.question,
        [match],
        filters,
        RerankConfig(mode=RerankerMode.VECTOR_LEXICAL),
    )[0]
    metadata = soft_rerank(
        document.question,
        [match],
        filters,
        RerankConfig(mode=RerankerMode.VECTOR_METADATA),
    )[0]

    assert vector_only.score == 0.5
    assert vector_only.score_components == {}
    assert any(name.startswith("lexical_") for name in lexical.score_components)
    assert metadata.score_components == {"scenario": 0.07}


def test_reranker_weights_scale_component_contributions() -> None:
    document = load_seed_documents()[0]
    match = RetrievedDocument(document=document, score=0.5)
    filters = KnowledgeFilters(scenario=AdviceScenario.PURSUIT)

    result = soft_rerank(
        document.question,
        [match],
        filters,
        RerankConfig(lexical_weight=0, metadata_weight=2),
    )[0]

    assert not any(name.startswith("lexical_") for name in result.score_components)
    assert result.score_components["scenario"] == 0.14


def test_reranker_preserves_explicit_zero_base_score() -> None:
    document = load_seed_documents()[0]
    result = soft_rerank(
        document.question,
        [RetrievedDocument(document=document, score=0.2, base_score=0.0)],
        None,
    )[0]

    assert result.base_score == 0.0


def test_retrieval_text_ablation_views_are_distinct() -> None:
    document = load_seed_documents()[0].model_copy(update={"answer": "完整答案内容"})

    question = document.render_retrieval_text(RetrievalTextMode.QUESTION)
    question_variants = document.render_retrieval_text(
        RetrievalTextMode.QUESTION_VARIANTS
    )
    question_variants_answer = document.render_retrieval_text(
        RetrievalTextMode.QUESTION_VARIANTS_ANSWER
    )

    assert question == document.question
    assert document.answer not in question_variants
    assert document.answer in question_variants_answer
    assert len(question) <= len(question_variants) <= len(question_variants_answer)
    assert document.retrieval_text == document.render_retrieval_text(RetrievalTextMode.FULL)

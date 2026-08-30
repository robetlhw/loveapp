from pydantic import SecretStr

from loveapp.adapters.memory.openai_compatible import TieredMemoryExtractor
from loveapp.adapters.memory.semantic_relations import (
    OpenAICompatibleSemanticRelationJudge,
)
from loveapp.bootstrap import build_memory_container
from loveapp.core.config import Settings


async def test_memory_container_keeps_flash_and_strong_budgets_independent() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_model="deepseek-v4-pro",
        llm_api_key=SecretStr("test-key"),
        llm_base_url="https://example.invalid",
        memory_backend="memory",
        memory_extraction_provider="llm",
        memory_extraction_model="deepseek-v4-flash",
        memory_extraction_timeout_seconds=11,
        memory_extraction_max_retries=0,
        memory_extraction_max_tokens=1536,
        memory_extraction_thinking="disabled",
        memory_extraction_strong_timeout_seconds=22,
        memory_extraction_strong_max_retries=1,
        memory_extraction_strong_max_tokens=4096,
        memory_extraction_strong_thinking="enabled",
    )
    container = build_memory_container(settings)
    try:
        extractor = container.memory_service._extractor
        assert isinstance(extractor, TieredMemoryExtractor)
        assert extractor._flash._model == "deepseek-v4-flash"
        assert extractor._flash._thinking == "disabled"
        assert extractor._flash._sdk_max_retries == 0
        assert extractor._flash._max_tokens == 1536
        assert extractor._strong is not None
        assert extractor._strong._model == "deepseek-v4-pro"
        assert extractor._strong._thinking == "enabled"
        assert extractor._strong._sdk_max_retries == 1
        assert extractor._strong._max_tokens == 4096
    finally:
        await container.aclose()


async def test_memory_container_wires_longtail_judge_in_shadow_mode_only() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_model="deepseek-v4-pro",
        llm_api_key=SecretStr("test-key"),
        llm_base_url="https://example.invalid",
        memory_backend="memory",
        memory_extraction_provider="disabled",
        memory_semantic_relation_provider="llm",
        memory_semantic_relation_model="deepseek-v4-flash",
        memory_semantic_relation_candidate_limit=3,
        memory_semantic_relation_confidence_threshold=0.94,
    )
    container = build_memory_container(settings, enable_extraction=False)
    try:
        evaluator = container.memory_service._long_tail_relation_evaluator
        assert evaluator is not None
        assert isinstance(evaluator._judge, OpenAICompatibleSemanticRelationJudge)
        assert evaluator._judge._model == "deepseek-v4-flash"
        assert evaluator._candidate_retriever._limit == 3
        assert evaluator._validator._proposal_confidence_threshold == 0.94
    finally:
        await container.aclose()


async def test_memory_container_leaves_longtail_shadow_disabled_by_default() -> None:
    settings = Settings(
        _env_file=None,
        memory_backend="memory",
        memory_extraction_provider="disabled",
    )
    container = build_memory_container(settings, enable_extraction=False)
    try:
        assert container.memory_service._long_tail_relation_evaluator is None
    finally:
        await container.aclose()

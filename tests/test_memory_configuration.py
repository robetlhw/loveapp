from pydantic import SecretStr

from loveapp.adapters.memory.openai_compatible import TieredMemoryExtractor
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

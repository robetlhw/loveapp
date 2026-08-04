from pathlib import Path

import pytest

from loveapp.core.config import Settings


@pytest.fixture
def app_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="demo",
        rag_backend="memory",
        map_provider="demo",
        memory_backend="memory",
        memory_extraction_provider="disabled",
        knowledge_path=tmp_path / "knowledge",
    )

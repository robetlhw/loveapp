from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="LOVEAPP_",
        extra="ignore",
    )

    environment: str = "development"
    log_level: str = "INFO"
    knowledge_path: Path = Path("knowledge")

    llm_provider: str = "demo"
    llm_model: str = ""
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_timeout_seconds: float = 120
    llm_max_retries: int = 2
    llm_max_tokens: int = 4096

    router_provider: Literal["auto", "llm", "disabled"] = "auto"
    router_model: str = ""
    router_max_tokens: int = Field(default=2048, ge=512, le=8192)
    # Keep semantic routing independent from the slower final-answer budget.
    router_timeout_seconds: float = Field(default=20, ge=1, le=120)
    router_max_retries: int = Field(default=0, ge=0, le=3)
    router_thinking: Literal["enabled", "disabled"] = "disabled"
    router_confidence_threshold: float = Field(default=0.72, ge=0, le=1)
    router_ambiguity_margin: float = Field(default=0.16, ge=0, le=1)

    rag_backend: Literal["memory", "qdrant"] = "qdrant"
    rag_min_score: float = 0.45
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "love_knowledge"
    qdrant_timeout_seconds: float = 20

    embedding_provider: Literal["sentence_transformers"] = "sentence_transformers"
    embedding_source: Literal["modelscope", "huggingface"] = "modelscope"
    embedding_model: str = "AI-ModelScope/bge-small-zh-v1.5"
    embedding_device: str = "cpu"
    embedding_batch_size: int = 16
    embedding_cache_path: Path = Path(".cache/modelscope")

    memory_backend: Literal["memory", "sqlite"] = "sqlite"
    memory_database_path: Path = Path(".data/loveapp.db")
    memory_extraction_provider: Literal["auto", "llm", "disabled"] = "auto"
    memory_extraction_model: str = ""
    # Memory extraction is an auxiliary task. Keep its network budget
    # independent from the final answer generation budget.
    memory_extraction_timeout_seconds: float = Field(default=30, ge=1, le=300)
    memory_extraction_max_retries: int = Field(default=0, ge=0, le=3)
    memory_extraction_max_tokens: int = Field(default=1536, ge=256, le=16384)
    memory_extraction_thinking: Literal["enabled", "disabled"] = "disabled"
    memory_extraction_strong_model: str = ""
    memory_extraction_strong_timeout_seconds: float = Field(default=60, ge=1, le=300)
    memory_extraction_strong_max_retries: int = Field(default=1, ge=0, le=3)
    memory_extraction_strong_max_tokens: int = Field(default=4096, ge=512, le=16384)
    memory_extraction_strong_thinking: Literal["enabled", "disabled"] = "enabled"
    memory_extraction_upgrade_min_importance: int = Field(default=4, ge=1, le=5)
    memory_min_confidence: float = Field(default=0.65, ge=0, le=1)
    memory_tentative_min_confidence: float = Field(default=0.5, ge=0, le=1)
    memory_belief_min_confidence: float = Field(default=0.4, ge=0, le=1)
    memory_admission_policy_overrides: dict[str, dict[str, object]] = Field(
        default_factory=dict
    )
    memory_context_limit: int = Field(default=20, ge=1, le=100)
    conversation_history_limit: int = Field(default=12, ge=2, le=50)
    memory_context_wait_seconds: float = Field(default=2, ge=0, le=30)
    memory_shutdown_grace_seconds: float = Field(default=10, ge=0, le=120)

    map_provider: Literal["demo", "amap"] = "amap"
    amap_api_key: SecretStr | None = None
    amap_base_url: str = "https://restapi.amap.com"
    amap_timeout_seconds: float = 20
    amap_page_size: int = 25
    amap_min_interval_seconds: float = 0.6
    amap_max_retries: int = 2
    weather_provider: Literal["disabled", "demo", "amap"] = "disabled"
    weather_timeout_seconds: float = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()

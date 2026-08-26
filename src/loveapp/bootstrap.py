import asyncio
import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from qdrant_client import AsyncQdrantClient

from loveapp.adapters.advice import (
    OpenAICompatibleAdviceComposer,
    TemplateAdviceComposer,
)
from loveapp.adapters.conversation_states import (
    InMemoryConversationFlowStateStore,
    SQLiteConversationFlowStateStore,
)
from loveapp.adapters.date_tasks import (
    InMemoryDatePlanningTaskStore,
    SQLiteDatePlanningTaskStore,
)
from loveapp.adapters.embeddings import SentenceTransformerEmbeddingProvider
from loveapp.adapters.knowledge import InMemoryKnowledgeRetriever, QdrantKnowledgeStore
from loveapp.adapters.knowledge.loader import (
    load_knowledge_path,
    merge_knowledge_documents,
    parse_documents,
)
from loveapp.adapters.maps import AmapMapProvider, DemoMapProvider
from loveapp.adapters.memory import (
    InMemoryMemoryStore,
    OpenAICompatibleMemoryExtractor,
    SQLiteMemoryStore,
    TieredMemoryExtractor,
)
from loveapp.adapters.routing import OpenAICompatibleRouteCorrector
from loveapp.adapters.weather import (
    AmapWeatherProvider,
    DemoWeatherProvider,
    DisabledWeatherProvider,
)
from loveapp.agents import (
    AdviceAgent,
    ConversationAgent,
    DatePlanningAgent,
    DatePlanningWorkflow,
)
from loveapp.application import MemoryService
from loveapp.application.date_planning import DatePlanValidator
from loveapp.application.memory import NoOpMemoryExtractor
from loveapp.application.routing import HybridRouter
from loveapp.core.config import Settings, get_settings
from loveapp.domain.knowledge import KnowledgeDocument
from loveapp.ports.conversation_states import ConversationFlowStateStore
from loveapp.ports.date_tasks import DatePlanningTaskStore
from loveapp.ports.embeddings import EmbeddingProvider
from loveapp.ports.memory import MemoryStore
from loveapp.ports.routing import Router
from loveapp.ports.weather import WeatherProvider
from loveapp.safety import SafetyPolicy


@dataclass(frozen=True)
class AppContainer:
    advice_agent: AdviceAgent
    date_planning_agent: DatePlanningAgent
    date_planning_workflow: DatePlanningWorkflow
    date_plan_validator: DatePlanValidator
    conversation_agent: ConversationAgent
    router: Router
    memory_service: MemoryService
    memory_store: MemoryStore
    date_task_store: DatePlanningTaskStore
    conversation_flow_state_store: ConversationFlowStateStore
    resources: tuple[Any, ...] = ()

    def start_background_warmup(self) -> tuple[asyncio.Task, ...]:
        tasks: list[asyncio.Task] = []
        for resource in self.resources:
            start = getattr(resource, "start_warmup", None)
            if start is not None:
                tasks.append(start())
        return tuple(tasks)

    async def aclose(self) -> None:
        for resource in reversed(self.resources):
            close = getattr(resource, "aclose", None)
            if close is not None:
                await close()


@dataclass(frozen=True)
class MemoryContainer:
    memory_service: MemoryService
    memory_store: MemoryStore
    resources: tuple[Any, ...] = ()

    async def aclose(self) -> None:
        for resource in reversed(self.resources):
            close = getattr(resource, "aclose", None)
            if close is not None:
                await close()


def build_container(settings: Settings | None = None) -> AppContainer:
    settings = settings or get_settings()
    resources: list[Any] = []
    embedding_provider = build_embedding_provider(settings)
    if settings.rag_backend == "qdrant":
        retriever = build_qdrant_store(
            settings,
            embedding_provider=embedding_provider,
        )
        resources.append(retriever)
    else:
        resources.append(embedding_provider)
        documents = _merge_documents(
            load_seed_documents(),
            load_knowledge_path(settings.knowledge_path),
        )
        retriever = InMemoryKnowledgeRetriever(documents)

    composer = _build_advice_composer(settings)
    if hasattr(composer, "aclose"):
        resources.append(composer)
    memory_container = build_memory_container(
        settings,
        embedding_provider=embedding_provider,
    )
    memory_store = memory_container.memory_store
    memory_service = memory_container.memory_service
    resources.extend(memory_container.resources)
    date_task_store = _build_date_task_store(settings)
    resources.append(date_task_store)
    conversation_flow_state_store = _build_conversation_flow_state_store(settings)
    resources.append(conversation_flow_state_store)
    map_provider = _build_map_provider(settings)
    if hasattr(map_provider, "aclose"):
        resources.append(map_provider)
    weather_provider = _build_weather_provider(settings)
    if hasattr(weather_provider, "aclose"):
        resources.append(weather_provider)
    safety_policy = SafetyPolicy(context_turns=settings.router_context_risk_turns)
    route_corrector = _build_route_corrector(settings)
    if route_corrector is not None:
        resources.append(route_corrector)
    router = HybridRouter(
        safety_policy,
        route_corrector,
        confidence_threshold=settings.router_confidence_threshold,
        ambiguity_margin=settings.router_ambiguity_margin,
        clarification_threshold=settings.router_clarification_threshold,
        prompt_version=settings.router_prompt_version,
    )
    advice_agent = AdviceAgent(
        retriever,
        memory_service,
        safety_policy,
        composer,
        router,
    )
    date_planning_agent = DatePlanningAgent(map_provider, memory_service, weather_provider)
    date_plan_validator = DatePlanValidator()
    date_planning_workflow = DatePlanningWorkflow(
        date_planning_agent,
        date_task_store,
        date_plan_validator,
    )
    conversation_agent = ConversationAgent(
        router=router,
        advice_agent=advice_agent,
        date_planning_agent=date_planning_agent,
        memory_service=memory_service,
        date_task_store=date_task_store,
        conversation_flow_state_store=conversation_flow_state_store,
        date_planning_workflow=date_planning_workflow,
    )
    return AppContainer(
        advice_agent=advice_agent,
        date_planning_agent=date_planning_agent,
        date_planning_workflow=date_planning_workflow,
        date_plan_validator=date_plan_validator,
        conversation_agent=conversation_agent,
        router=router,
        memory_service=memory_service,
        memory_store=memory_store,
        date_task_store=date_task_store,
        conversation_flow_state_store=conversation_flow_state_store,
        resources=tuple(resources),
    )


def build_memory_container(
    settings: Settings | None = None,
    *,
    enable_extraction: bool = True,
    embedding_provider: EmbeddingProvider | None = None,
) -> MemoryContainer:
    settings = settings or get_settings()
    owns_embedding_provider = embedding_provider is None
    embedding_provider = embedding_provider or build_embedding_provider(settings)
    memory_store = _build_memory_store(settings)
    memory_extractor = (
        _build_memory_extractor(settings) if enable_extraction else NoOpMemoryExtractor()
    )
    memory_service = MemoryService(
        memory_store,
        memory_extractor,
        min_confidence=settings.memory_min_confidence,
        tentative_min_confidence=settings.memory_tentative_min_confidence,
        belief_min_confidence=settings.memory_belief_min_confidence,
        context_limit=settings.memory_context_limit,
        history_limit=settings.conversation_history_limit,
        context_wait_seconds=settings.memory_context_wait_seconds,
        shutdown_grace_seconds=settings.memory_shutdown_grace_seconds,
        admission_policy_overrides=settings.memory_admission_policy_overrides,
        embedding_provider=embedding_provider,
        verifier=(memory_extractor if getattr(memory_extractor, "can_verify", False) else None),
    )
    resources: tuple[Any, ...] = (memory_store, memory_extractor, memory_service)
    if owns_embedding_provider:
        resources += (embedding_provider,)
    return MemoryContainer(
        memory_service=memory_service,
        memory_store=memory_store,
        resources=resources,
    )


def build_embedding_provider(settings: Settings) -> SentenceTransformerEmbeddingProvider:
    return SentenceTransformerEmbeddingProvider(
        model_name=settings.embedding_model,
        source=settings.embedding_source,
        device=settings.embedding_device,
        batch_size=settings.embedding_batch_size,
        cache_path=settings.embedding_cache_path,
    )


def build_qdrant_store(
    settings: Settings,
    *,
    embedding_provider: EmbeddingProvider | None = None,
) -> QdrantKnowledgeStore:
    client = AsyncQdrantClient(
        url=settings.qdrant_url,
        timeout=settings.qdrant_timeout_seconds,
    )
    return QdrantKnowledgeStore(
        client=client,
        collection_name=settings.qdrant_collection,
        embedding_provider=embedding_provider or build_embedding_provider(settings),
        min_score=settings.rag_min_score,
    )


def _build_map_provider(settings: Settings):
    if settings.map_provider == "demo":
        return DemoMapProvider()
    if not settings.amap_api_key:
        raise ValueError("LOVEAPP_AMAP_API_KEY 未配置。")
    return AmapMapProvider(
        api_key=settings.amap_api_key,
        base_url=settings.amap_base_url,
        timeout_seconds=settings.amap_timeout_seconds,
        page_size=settings.amap_page_size,
        min_interval_seconds=settings.amap_min_interval_seconds,
        max_retries=settings.amap_max_retries,
    )


def _build_weather_provider(settings: Settings) -> WeatherProvider:
    if settings.weather_provider == "disabled":
        return DisabledWeatherProvider()
    if settings.weather_provider == "demo":
        return DemoWeatherProvider()
    if not settings.amap_api_key:
        raise ValueError("LOVEAPP_AMAP_API_KEY 未配置，无法启用天气服务。")
    return AmapWeatherProvider(
        api_key=settings.amap_api_key,
        base_url=settings.amap_base_url,
        timeout_seconds=settings.weather_timeout_seconds,
        min_interval_seconds=settings.amap_min_interval_seconds,
    )


def load_seed_documents() -> list[KnowledgeDocument]:
    resource = files("loveapp.resources").joinpath("knowledge_seed.json")
    raw_documents = json.loads(resource.read_text(encoding="utf-8"))
    return parse_documents(raw_documents)


def _build_advice_composer(settings: Settings):
    if settings.llm_provider == "demo":
        return TemplateAdviceComposer()
    if not settings.llm_api_key:
        raise ValueError("LOVEAPP_LLM_API_KEY 未配置。")
    if not settings.llm_base_url:
        raise ValueError("LOVEAPP_LLM_BASE_URL 未配置。")
    if not settings.llm_model:
        raise ValueError("LOVEAPP_LLM_MODEL 未配置。")
    return OpenAICompatibleAdviceComposer(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        max_tokens=settings.llm_max_tokens,
    )


def _build_memory_store(settings: Settings) -> MemoryStore:
    if settings.memory_backend == "memory":
        return InMemoryMemoryStore()
    return SQLiteMemoryStore(settings.memory_database_path)


def _build_date_task_store(settings: Settings) -> DatePlanningTaskStore:
    if settings.memory_backend == "memory":
        return InMemoryDatePlanningTaskStore()
    return SQLiteDatePlanningTaskStore(settings.memory_database_path)


def _build_conversation_flow_state_store(settings: Settings) -> ConversationFlowStateStore:
    if settings.memory_backend == "memory":
        return InMemoryConversationFlowStateStore()
    return SQLiteConversationFlowStateStore(settings.memory_database_path)


def _build_memory_extractor(settings: Settings):
    use_llm = settings.memory_extraction_provider == "llm" or (
        settings.memory_extraction_provider == "auto" and settings.llm_provider != "demo"
    )
    if not use_llm:
        return NoOpMemoryExtractor()
    if not settings.llm_api_key:
        raise ValueError("LOVEAPP_LLM_API_KEY 未配置。")
    if not settings.llm_base_url:
        raise ValueError("LOVEAPP_LLM_BASE_URL 未配置。")
    flash_model = settings.memory_extraction_model or settings.llm_model
    if not flash_model:
        raise ValueError("LOVEAPP_MEMORY_EXTRACTION_MODEL 或 LOVEAPP_LLM_MODEL 未配置。")
    flash = OpenAICompatibleMemoryExtractor(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=flash_model,
        timeout_seconds=settings.memory_extraction_timeout_seconds,
        max_retries=settings.memory_extraction_max_retries,
        max_tokens=settings.memory_extraction_max_tokens,
        tier="flash",
        thinking=settings.memory_extraction_thinking,
    )
    strong_model = settings.memory_extraction_strong_model or settings.llm_model
    strong = None
    if strong_model and strong_model != flash_model:
        strong = OpenAICompatibleMemoryExtractor(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=strong_model,
            timeout_seconds=settings.memory_extraction_strong_timeout_seconds,
            max_retries=settings.memory_extraction_strong_max_retries,
            max_tokens=settings.memory_extraction_strong_max_tokens,
            tier="strong",
            thinking=settings.memory_extraction_strong_thinking,
        )
    return TieredMemoryExtractor(
        flash,
        strong,
        upgrade_min_importance=settings.memory_extraction_upgrade_min_importance,
    )


def _build_route_corrector(settings: Settings):
    use_llm = settings.router_provider == "llm" or (
        settings.router_provider == "auto" and settings.llm_provider != "demo"
    )
    if not use_llm:
        return None
    if not settings.llm_api_key:
        raise ValueError("LOVEAPP_LLM_API_KEY 未配置。")
    if not settings.llm_base_url:
        raise ValueError("LOVEAPP_LLM_BASE_URL 未配置。")
    model = settings.router_model or settings.llm_model
    if not model:
        raise ValueError("LOVEAPP_ROUTER_MODEL 或 LOVEAPP_LLM_MODEL 未配置。")
    return OpenAICompatibleRouteCorrector(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=model,
        timeout_seconds=settings.router_timeout_seconds,
        max_retries=settings.router_max_retries,
        max_tokens=settings.router_max_tokens,
        thinking=settings.router_thinking,
        prompt_version=settings.router_prompt_version,
    )


def _merge_documents(*document_groups: list[KnowledgeDocument]) -> list[KnowledgeDocument]:
    return merge_knowledge_documents(*document_groups)

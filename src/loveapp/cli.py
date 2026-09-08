import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import date as Date
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

import typer
from qdrant_client import AsyncQdrantClient
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from loveapp.adapters.knowledge.loader import load_knowledge_path, merge_knowledge_documents
from loveapp.adapters.knowledge.qdrant import QdrantKnowledgeStore
from loveapp.adapters.knowledge.scoring import RerankConfig, RerankerMode
from loveapp.adapters.memory import OpenAICompatibleSemanticRelationJudge
from loveapp.application.advice_presentation import (
    AdvicePresentationMode,
    choose_advice_presentation,
    format_compact_advice,
)
from loveapp.application.memory import NoOpMemoryExtractor
from loveapp.application.memory_retrieval import HybridMemoryRetriever
from loveapp.application.retrieval_query_planner import RetrievalQueryPlanner
from loveapp.application.routing import HybridRouter
from loveapp.bootstrap import (
    _build_memory_extractor,
    build_container,
    build_embedding_provider,
    build_memory_container,
    build_qdrant_store,
    build_routing_container,
    load_seed_documents,
)
from loveapp.cli_memory_inspector import (
    DEFAULT_MEMORY_TEST_CONVERSATION_ID,
    DEFAULT_MEMORY_TEST_RELATIONSHIP_ID,
    DEFAULT_MEMORY_TEST_USER_ID,
    run_memory_inspector_cli,
)
from loveapp.core.config import get_settings
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.advice import AdviceRequest, AdviceResponse, AdviceStreamEvent
from loveapp.domain.conversation import ConversationRequest
from loveapp.domain.date_operations import DatePlanOperation, DesiredDateStop, StopReference
from loveapp.domain.date_plan import DatePlan, DatePlanRequest
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.enums import (
    BudgetScope,
    DatePlanMode,
    RelationshipStage,
    TaskType,
    TransportMode,
)
from loveapp.domain.knowledge import RetrievalTextMode
from loveapp.domain.memory import (
    MemoryCompactionResult,
    MemoryExtractionRun,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    RememberResult,
)
from loveapp.domain.memory_context import memory_attention_reason
from loveapp.domain.memory_write import MemoryTransitionAudit
from loveapp.domain.observability import TimingEvent
from loveapp.domain.relationship_plan import PlanStatus, RelationshipPlan
from loveapp.domain.routing import RouteResult
from loveapp.evaluation import (
    FixtureSemanticRelationJudge,
    build_phase31_router,
    evaluate_contextual_rewrite,
    evaluate_dateplan,
    evaluate_live_routing_conversations,
    evaluate_memory_admission_integration,
    evaluate_memory_admission_v1,
    evaluate_memory_extraction_v1,
    evaluate_memory_foundation,
    evaluate_memory_gate_v2,
    evaluate_memory_lifecycle,
    evaluate_memory_longtail_realistic,
    evaluate_memory_longtail_relations,
    evaluate_memory_longtail_write_integration,
    evaluate_memory_longtail_write_v1,
    evaluate_memory_normalization_boundary,
    evaluate_memory_normalization_v1,
    evaluate_multiquery,
    evaluate_phase31_dataset,
    evaluate_phase32_dataset,
    evaluate_phase32_experiment,
    evaluate_phase32_repeatability,
    evaluate_phase32_smoke,
    evaluate_phase321_experiment,
    evaluate_router_safety,
    evaluate_routing_conversations,
    load_contextual_rewrite_eval_markdown,
    load_multiquery_eval_markdown,
    load_router_challenge_cases,
    load_router_safety_cases,
    phase32_dry_run,
    phase321_call_budget,
    render_dateplan_report,
    render_longtail_baseline_report,
    render_longtail_realistic_report,
    render_memory_admission_integration_diagnostic,
    render_memory_admission_policy_review,
    render_memory_admission_strong_review_audit,
    render_memory_admission_v1_report,
    render_memory_extraction_v1_report,
    render_memory_gate_v2_report,
    render_memory_longtail_write_integration_diagnostic,
    render_memory_longtail_write_policy_review,
    render_memory_longtail_write_v1_report,
    render_memory_normalization_boundary_report,
    render_memory_normalization_v1_report,
    render_routing_report,
    resolve_phase32_provider,
    run_baseline,
    validate_contextual_rewrite_dataset,
    validate_multiquery_dataset,
    validate_router_challenge_dataset,
    validate_router_safety_dataset,
    write_phase31_findings,
    write_phase31_report,
    write_phase32_findings,
    write_phase32_report,
    write_phase45_report,
    write_phase321_reports,
    write_router_safety_report,
)
from loveapp.evaluation.memory_extraction_alignment import (
    OpenAICompatibleExtractionAlignmentJudge,
)
from loveapp.evaluation.memory_extraction_langsmith import (
    DEFAULT_DATASET_NAME as MEMORY_EXTRACTION_LANGSMITH_DATASET,
)
from loveapp.evaluation.memory_extraction_langsmith import (
    LangSmithExtractionObserver,
    langsmith_configured,
    sync_memory_extraction_dataset,
)
from loveapp.evaluation.memory_longtail_realistic import HARD_CASE_IDS
from loveapp.evaluation.memory_longtail_write_v2 import (
    collect_memory_longtail_write_v2_repository_metadata,
    compare_memory_longtail_write_v2_reports,
    compare_memory_longtail_write_v2_semantic_remediation,
    evaluate_memory_longtail_write_v2,
    evaluate_memory_longtail_write_v2_fixture,
    finalize_memory_longtail_write_v2_live_validation,
    render_memory_longtail_write_v2_report,
)
from loveapp.evaluation.rag_v2 import (
    build_e2e_executor,
    compare_oracle_and_e2e,
    evaluate_rag_targets,
    evaluate_rag_v2,
    load_rag_eval_markdown,
    validate_rag_v2_dataset,
    write_rag_report,
)
from loveapp.evaluation.rag_v2_metadata import (
    MetadataFilterExperimentConfig,
    build_metadata_filter_e2e_executors,
    evaluate_metadata_filter_comparison,
    write_metadata_filter_comparison,
)
from loveapp.evaluation.rag_v2_sweep import (
    run_rag_v2_dev_sweep,
    write_sweep_report,
)
from loveapp.safety import SafetyPolicy

app = typer.Typer(
    name="loveapp",
    help="恋爱沟通与约会决策 Agent。",
    no_args_is_help=True,
)
console = Console()
knowledge_app = typer.Typer(help="检查和管理本地 RAG 知识文档。")
memory_app = typer.Typer(help="写入、检查和管理关系记忆。")
eval_app = typer.Typer(help="运行固定数据集评测并保存 baseline。")
app.add_typer(knowledge_app, name="knowledge")
app.add_typer(memory_app, name="memory")
app.add_typer(eval_app, name="eval")


class _ObservedEmbeddingProvider:
    """Record whether a live evaluation actually used vector embeddings."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.query_call_count = 0
        self.document_call_count = 0
        self.document_text_count = 0
        self.failure_types: dict[str, int] = {}

    async def embed_query(self, text: str) -> list[float]:
        self.query_call_count += 1
        try:
            return await self._delegate.embed_query(text)
        except Exception as exc:
            self._record_failure(exc)
            raise

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_call_count += 1
        self.document_text_count += len(texts)
        try:
            return await self._delegate.embed_documents(texts)
        except Exception as exc:
            self._record_failure(exc)
            raise

    def summary(self, *, model: str, dimension: int) -> dict[str, Any]:
        attempted = self.query_call_count > 0 or self.document_call_count > 0
        confirmed = bool(
            attempted
            and self.query_call_count > 0
            and self.document_call_count > 0
            and not self.failure_types
        )
        return {
            "provider": "sentence_transformers",
            "model": model,
            "dimension": dimension,
            "query_call_count": self.query_call_count,
            "document_call_count": self.document_call_count,
            "document_text_count": self.document_text_count,
            "failure_count": sum(self.failure_types.values()),
            "failure_types": dict(sorted(self.failure_types.items())),
            "embedding_retrieval_attempted": attempted,
            "embedding_backed_retrieval_confirmed": confirmed,
        }

    def _record_failure(self, exc: Exception) -> None:
        name = type(exc).__name__
        self.failure_types[name] = self.failure_types.get(name, 0) + 1


@eval_app.command("baseline")
def baseline_eval(
    output: Annotated[
        Path,
        typer.Option("--output", help="JSON 评测报告保存路径。"),
    ] = Path("evals/baselines/current.json"),
    include_rag: Annotated[
        bool,
        typer.Option("--rag/--no-rag", help="是否运行真实 Qdrant 检索评测。"),
    ] = True,
    live_memory: Annotated[
        bool,
        typer.Option(
            "--live-memory/--no-live-memory",
            help="是否调用真实模型计算记忆污染率。",
        ),
    ] = True,
) -> None:
    """运行路由、RAG、安全与记忆 baseline。"""
    try:
        report = asyncio.run(
            run_baseline(
                get_settings(),
                output_path=output,
                include_rag=include_rag,
                include_live_memory=live_memory,
                progress=lambda message: console.print(f"[dim]{message}[/dim]"),
            )
        )
    except Exception as exc:
        console.print(f"[red]Baseline 评测失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Baseline 已保存：[/green]{output}")
    table = Table(title="Baseline 指标摘要")
    table.add_column("组件")
    table.add_column("指标")
    table.add_column("值", justify="right")
    for component, metrics in report["metrics"].items():
        if metrics.get("status") == "skipped":
            table.add_row(component, "status", "skipped")
            continue
        for key, value in metrics.items():
            if key == "cases" or isinstance(value, (dict, list)):
                continue
            table.add_row(component, key, str(value))
    console.print(table)


@eval_app.command("routing")
def routing_eval(
    dataset: Annotated[
        Path,
        typer.Option("--dataset", help="多轮路由评测集路径。"),
    ] = Path("evals/routing/cases_v4.jsonl"),
    output: Annotated[
        Path | None,
        typer.Option("--output", help="路由评测报告保存路径；默认按 Policy/Live 分开。"),
    ] = None,
    live: Annotated[
        bool,
        typer.Option(
            "--live/--policy",
            help="调用真实 RouteCorrector；需显式启用 live eval 环境保护。",
        ),
    ] = False,
    input_cost_per_million: Annotated[
        float,
        typer.Option("--input-cost-per-million", min=0, help="每百万输入 token 成本。"),
    ] = 0,
    output_cost_per_million: Annotated[
        float,
        typer.Option("--output-cost-per-million", min=0, help="每百万输出 token 成本。"),
    ] = 0,
    fail_on_targets: Annotated[
        bool,
        typer.Option(
            "--fail-on-targets/--no-fail-on-targets",
            help="未达到固定集验收目标时以非零状态退出。",
        ),
    ] = False,
    case: Annotated[
        list[str] | None,
        typer.Option(
            "--case",
            help="只评测指定案例 id；可重复传入或使用逗号分隔。",
        ),
    ] = None,
    category: Annotated[
        list[str] | None,
        typer.Option(
            "--category",
            help="只评测指定 category；可重复传入或使用逗号分隔。",
        ),
    ] = None,
) -> None:
    """运行确定性 Policy Eval，或显式启用的真实模型 Live Eval。"""
    output_path = output or Path(
        "evals/baselines/routing_v4_live_current.json"
        if live
        else "evals/baselines/routing_v4_current.json"
    )
    try:
        settings = get_settings()
        case_ids = _split_eval_filters(case)
        categories = _split_eval_filters(category)
        if live:
            report = asyncio.run(
                evaluate_live_routing_conversations(
                    dataset,
                    settings,
                    input_cost_per_million=input_cost_per_million,
                    output_cost_per_million=output_cost_per_million,
                    case_ids=case_ids or None,
                    categories=categories or None,
                )
            )
        else:
            report = asyncio.run(
                evaluate_routing_conversations(
                    dataset,
                    input_cost_per_million=input_cost_per_million,
                    output_cost_per_million=output_cost_per_million,
                    confidence_threshold=settings.router_confidence_threshold,
                    ambiguity_margin=settings.router_ambiguity_margin,
                    clarification_threshold=settings.router_clarification_threshold,
                    safety_context_turns=settings.router_context_risk_turns,
                    prompt_version=settings.router_prompt_version,
                    case_ids=case_ids or None,
                    categories=categories or None,
                )
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.suffix.casefold() == ".md":
            output_path.write_text(render_routing_report(report), encoding="utf-8")
        else:
            output_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if not case_ids and not categories:
                Path("ROUTER_EVAL_REPORT.md").write_text(
                    render_routing_report(report),
                    encoding="utf-8",
                )
    except Exception as exc:
        console.print(f"[red]路由评测失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc
    mode_name = "Live Router Eval" if live else "Policy Eval"
    console.print(f"[green]{mode_name} 已保存：[/green]{output_path}")
    table = Table(title=f"{mode_name} 摘要")
    table.add_column("指标")
    table.add_column("值", justify="right")
    for key, value in report.items():
        if key == "cases" or isinstance(value, (dict, list)):
            continue
        table.add_row(key, str(value))
    console.print(table)
    if fail_on_targets and not report["acceptance_passed"]:
        failed = [name for name, passed in report["acceptance_targets"].items() if not passed]
        console.print(f"[red]未达到验收目标：[/red]{', '.join(failed)}")
        raise typer.Exit(code=2)


def _split_eval_filters(values: list[str] | None) -> list[str]:
    """Accept repeated CLI filters as well as a convenient comma-separated value."""

    if not values:
        return []
    return [item.strip() for value in values for item in value.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# LoveApp Phase 3--5 specialised evaluations
#
# These commands intentionally operate on the specialised fixtures directly.
# They never add the fixtures to the knowledge base or to an embedding
# corpus.  The feature switches are exposed here (rather than inferred from
# environment variables) so that Dev and frozen Test runs are reproducible.

_PHASE35_EVAL_ROOT = Path("evals/rag/phase3_5")
_PHASE31_EVAL_ROOT = Path("evals/rag/phase3_1")
_PHASE321_DEV_FILENAME = "loveapp_router_safety_eval_dev_v1.md"
_PHASE321_CHALLENGE_FILENAME = "loveapp_router_challenge_dev_v1.md"
_PHASE321_FORBIDDEN_TEST_FILENAME = "loveapp_router_safety_eval_test_v1.md"
_PHASE321_BEFORE_FILENAMES = {
    "always_before_dev": "router_phase3_2_live_always_dev.json",
    "conditional_before_dev": "router_phase3_2_live_conditional_dev.json",
    "always_before_challenge_dev": "router_phase3_2_live_always_challenge_dev.json",
    "conditional_before_challenge_dev": (
        "router_phase3_2_live_conditional_challenge_dev.json"
    ),
}


def _phase35_json_path(output: Path) -> Path:
    """Return the JSON destination while accepting a convenient ``.md`` path."""

    return output if output.suffix.casefold() == ".json" else output.with_suffix(".json")


def _phase35_write_router_report(report: dict[str, Any], output: Path) -> tuple[Path, Path]:
    json_path = _phase35_json_path(output)
    markdown_path = json_path.with_suffix(".md")
    write_router_safety_report(report, json_path, markdown_output=markdown_path)
    return json_path, markdown_path


def _phase35_live_router_configured(settings: Any) -> bool:
    """Return whether an explicitly requested live Router can be constructed.

    A missing key/base URL/model, demo provider, or disabled live-eval switch is
    treated as *not configured*.  Callers emit a skipped artifact in that case
    instead of invoking an external model or inventing metrics.
    """

    configured_router_provider = getattr(settings, "router_llm_provider", None)
    provider = str(
        configured_router_provider
        or getattr(settings, "router_provider", "auto")
    ).casefold()
    llm_provider = str(getattr(settings, "llm_provider", "demo")).casefold()
    model = (
        getattr(settings, "router_llm_model", None)
        or getattr(settings, "router_model", "")
        or getattr(settings, "llm_model", "")
    )
    provider_is_concrete = provider not in {"auto", "llm", "disabled", "none", "demo"}
    return bool(
        getattr(settings, "router_live_eval_enabled", False)
        and provider not in {"disabled", "none", "demo"}
        and (provider_is_concrete or llm_provider != "demo")
        and getattr(settings, "llm_api_key", None)
        and getattr(settings, "llm_base_url", None)
        and model
    )


def _phase35_skipped_report(
    *, phase: int, dataset: Path, reason: str, router_mode: str | None = None
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "phase": phase,
        "status": "skipped",
        "dataset": str(dataset),
        "reason": reason,
    }
    if router_mode is not None:
        report["router_mode"] = router_mode
    return report


def _phase35_load_path(path: Path, *, phase: int) -> Path:
    if path.exists():
        return path
    # Keep defaults useful when the command is run from a different working
    # directory (for example through an installed console script).
    name = path.name
    candidate = _PHASE35_EVAL_ROOT / name
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"Phase {phase} evaluation dataset not found: {path}")


def _phase35_load_knowledge_path(path: Path) -> Path:
    """Resolve a knowledge path from the repository root when needed.

    The specialised eval commands are often invoked through an installed
    console script, where the process working directory is not the repository
    root.  Keep path resolution deterministic without changing the configured
    knowledge file or indexing any specialised fixture.
    """

    if path.exists():
        return path
    repository_root = Path(__file__).resolve().parents[2]
    candidate = repository_root / path
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"Knowledge file not found: {path}")


def _phase35_phase45_lint(
    *,
    rewrite_cases: Sequence[Any] | None = None,
    multiquery_cases: Sequence[Any] | None = None,
    knowledge: Path | None = None,
) -> dict[str, Any]:
    """Run non-mutating Phase 4/5 fixture lint for report diagnostics.

    Lint findings are recorded in the report but do not silently alter Gold or
    block a deterministic rule evaluation.  In particular, the supplied
    contextual fixture deliberately reuses generic ellipsis text across
    different histories; that is surfaced as a dataset issue rather than
    hidden by the evaluator.
    """

    documents = []
    knowledge_ref: str | None = None
    if knowledge is not None:
        resolved = _phase35_load_knowledge_path(knowledge)
        documents = load_knowledge_path(resolved)
        knowledge_ref = str(resolved)
    known_ids = [document.id for document in documents] if knowledge is not None else None
    result: dict[str, Any] = {"knowledge": knowledge_ref}
    if rewrite_cases is not None:
        result["phase4"] = validate_contextual_rewrite_dataset(
            rewrite_cases,
            knowledge_ids=known_ids,
            source_ref="phase4",
        )
    if multiquery_cases is not None:
        result["phase5"] = validate_multiquery_dataset(
            multiquery_cases,
            knowledge_ids=known_ids,
            source_ref="phase5",
        )
    return result


async def _run_phase35_router_eval(
    dataset: Path,
    *,
    settings: Any,
    router_mode: Literal["rules", "configured", "live"],
    router_v2: bool,
) -> dict[str, Any]:
    """Evaluate Router/Safety and close configured Router resources safely."""

    eval_settings = settings.model_copy(update={"router_v2_enabled": router_v2})
    routing_container = None
    try:
        if router_mode == "rules":
            router = HybridRouter(
                SafetyPolicy(context_turns=eval_settings.router_context_risk_turns),
                confidence_threshold=eval_settings.router_confidence_threshold,
                ambiguity_margin=eval_settings.router_ambiguity_margin,
                clarification_threshold=eval_settings.router_clarification_threshold,
                prompt_version=eval_settings.router_prompt_version,
                router_v2_enabled=router_v2,
            )
        else:
            routing_container = build_routing_container(eval_settings)
            router = routing_container.router
        return await evaluate_router_safety(
            dataset,
            router=router,
            router_mode=("router_v2" if router_v2 else "current")
            if router_mode != "live"
            else "live",
        )
    finally:
        if routing_container is not None:
            await routing_container.aclose()


@eval_app.command("router-safety")
def router_safety_eval(
    dataset: Annotated[
        Path,
        typer.Option("--dataset", help="Phase 3 Router/Safety Markdown dataset."),
    ] = _PHASE35_EVAL_ROOT / "loveapp_router_safety_eval_dev_v1.md",
    output: Annotated[
        Path,
        typer.Option("--output", help="JSON report path; Markdown is written beside it."),
    ] = Path(".data/evals/router_safety_dev.json"),
    router_mode: Annotated[
        Literal["rules", "configured", "live"],
        typer.Option(
            "--router-mode",
            help="rules is deterministic; configured uses Settings; live is explicitly gated.",
        ),
    ] = "rules",
    live: Annotated[
        bool,
        typer.Option(
            "--live/--no-live",
            help="Shortcut for --router-mode live; an unconfigured live run is skipped.",
        ),
    ] = False,
    router_v2: Annotated[
        bool,
        typer.Option("--router-v2/--current-router", help="Select the Phase 3 Router arm."),
    ] = True,
    fail_on_targets: Annotated[
        bool,
        typer.Option("--fail-on-targets/--no-fail-on-targets"),
    ] = False,
) -> None:
    """Evaluate the Phase 3 branch, scenario, goals, and safety contract."""

    dataset = _phase35_load_path(dataset, phase=3)
    if live:
        router_mode = "live"
    settings = get_settings()
    if router_mode == "live" and not _phase35_live_router_configured(settings):
        report = _phase35_skipped_report(
            phase=3,
            dataset=dataset,
            router_mode="live",
            reason=(
                "Live Router is not configured (enable router_live_eval_enabled and "
                "provide a non-demo provider, API key, base URL, and model)."
            ),
        )
        json_path, markdown_path = _phase35_write_router_report(report, output)
        console.print(
            f"[yellow]Phase 3 Live Router skipped:[/yellow] {json_path} and {markdown_path}"
        )
        return

    try:
        report = asyncio.run(
            _run_phase35_router_eval(
                dataset,
                settings=settings,
                router_mode=router_mode,
                router_v2=router_v2,
            )
        )
        report["inputs"] = {
            "dataset": str(dataset),
            "router_mode": router_mode,
            "router_v2_enabled": router_v2,
        }
        json_path, markdown_path = _phase35_write_router_report(report, output)
    except Exception as exc:
        # ``skipped`` is reserved for the explicit, pre-flight configuration
        # gate above.  Once a live Router is configured, an initialization or
        # evaluation failure must remain visible to CI rather than being
        # misreported as an unavailable experiment.
        console.print(f"[red]Phase 3 Router/Safety evaluation failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]Phase 3 reports saved:[/green] {json_path} and {markdown_path}")
    console.print(
        f"Branch accuracy={report.get('branch_accuracy', 0)}; "
        f"Safety bypass={report.get('safety_to_rag_bypass_rate', 0)}"
    )
    if fail_on_targets and not report.get("acceptance_passed", False):
        raise typer.Exit(code=2)


async def _run_phase31_arm(
    dataset: Path,
    *,
    arm: Literal["rule", "llm", "conditional"],
    settings: Any,
    live: bool,
    input_cost_per_million: float | None,
    output_cost_per_million: float | None,
) -> dict[str, Any]:
    if not live or arm == "rule":
        router = build_phase31_router(arm)
        return await evaluate_phase31_dataset(
            dataset,
            arm=arm,
            router=router,
            provider="fixture_semantic" if arm != "rule" else "none",
            live_llm=False,
            input_cost_per_million=input_cost_per_million,
            output_cost_per_million=output_cost_per_million,
        )
    eval_settings = settings.model_copy(
        update={
            "router_v2_enabled": True,
            "router_semantic_mode": "always" if arm == "llm" else "conditional",
            "router_llm_correction_enabled": True,
        }
    )
    routing_container = build_routing_container(eval_settings)
    try:
        return await evaluate_phase31_dataset(
            dataset,
            arm=arm,
            router=routing_container.router,
            provider="live_llm",
            live_llm=True,
            input_cost_per_million=input_cost_per_million,
            output_cost_per_million=output_cost_per_million,
        )
    finally:
        await routing_container.aclose()


@eval_app.command("router-phase3-1")
def router_phase31_eval(
    dev_dataset: Annotated[
        Path,
        typer.Option("--dev-dataset", help="Frozen Phase 3 Router Dev Markdown dataset."),
    ] = _PHASE35_EVAL_ROOT / "loveapp_router_safety_eval_dev_v1.md",
    challenge_dataset: Annotated[
        Path,
        typer.Option("--challenge-dataset", help="Phase 3.1 Challenge Dev Markdown dataset."),
    ] = _PHASE31_EVAL_ROOT / "loveapp_router_challenge_dev_v1.md",
    arm: Annotated[
        Literal["all", "rule", "llm", "conditional"],
        typer.Option("--arm", help="Evaluation arm(s) to run."),
    ] = "all",
    live: Annotated[
        bool,
        typer.Option("--live/--no-live", help="Use a configured live Router provider."),
    ] = False,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="Directory for JSON/Markdown reports."),
    ] = Path(".data/evals"),
    input_cost_per_million: Annotated[
        float | None,
        typer.Option("--input-cost-per-million"),
    ] = None,
    output_cost_per_million: Annotated[
        float | None,
        typer.Option("--output-cost-per-million"),
    ] = None,
) -> None:
    """Compare Rule-only, LLM always-on, and Conditional Router arms on Dev only."""

    dev_dataset = dev_dataset.resolve()
    challenge_dataset = challenge_dataset.resolve()
    if not dev_dataset.exists() or not challenge_dataset.exists():
        raise typer.BadParameter("Phase 3.1 dataset path does not exist")
    try:
        challenge_cases = load_router_safety_cases(challenge_dataset)
        lint = validate_router_challenge_dataset(
            challenge_dataset,
            reference_paths=(
                dev_dataset,
                _PHASE35_EVAL_ROOT / "loveapp_router_safety_eval_test_v1.md",
            ),
        )
        if not lint.get("passed"):
            raise ValueError(f"Challenge Dev lint failed: {lint}")
        # The compatibility loader validates ChallengeSlices as well; keep the
        # generic parser call above to guarantee the evaluator sees the same
        # 120 cases and never silently drops a block.
        if len(challenge_cases) != 120:
            raise ValueError("Challenge Dev must contain exactly 120 cases")
    except Exception as exc:
        console.print(f"[red]Phase 3.1 fixture validation failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    arms = ("rule", "llm", "conditional") if arm == "all" else (arm,)
    datasets = (("dev", dev_dataset), ("challenge_dev", challenge_dataset))
    settings = get_settings()
    if live and not _phase35_live_router_configured(settings):
        raise typer.BadParameter(
            "--live requires router_live_eval_enabled, a non-demo provider, API key, "
            "base URL, and model"
        )
    reports: dict[str, dict[str, Any]] = {}
    try:
        for dataset_name, dataset_path in datasets:
            for active_arm in arms:
                report = asyncio.run(
                    _run_phase31_arm(
                        dataset_path,
                        arm=active_arm,
                        settings=settings,
                        live=live,
                        input_cost_per_million=input_cost_per_million,
                        output_cost_per_million=output_cost_per_million,
                    )
                )
                report["challenge_lint"] = lint if dataset_name == "challenge_dev" else None
                output = output_dir / f"router_phase3_1_{active_arm}_{dataset_name}.json"
                write_phase31_report(report, output)
                reports[f"{active_arm}_{dataset_name}"] = report
                console.print(
                    f"[green]Phase 3.1 report saved:[/green] {output} "
                    f"(RAG recall={report.get('rag_recall', 0):.4f}, "
                    f"LLM rate={report.get('llm_call_rate', 0):.4f})"
                )
        # Findings are a three-arm comparison.  A single-arm run must not
        # overwrite an existing comparison with synthetic zero rows for the
        # two arms that were not requested.
        if arm == "all":
            findings = (
                output_dir.parent.parent / "docs" / "rag" / "PHASE3_1_SEMANTIC_ROUTER_FINDINGS.md"
            )
            # output_dir defaults to .data/evals; for custom dirs keep findings at
            # the repository documentation location only when it is unambiguous.
            if output_dir == Path(".data/evals"):
                findings = Path("docs/rag/PHASE3_1_SEMANTIC_ROUTER_FINDINGS.md")
            write_phase31_findings(reports, findings)
            console.print(f"[green]Phase 3.1 findings saved:[/green] {findings}")
        else:
            console.print(
                "[yellow]Single-arm run: existing Phase 3.1 findings were not overwritten; "
                "run with --arm all to regenerate the comparison.[/yellow]"
            )
    except Exception as exc:
        console.print(f"[red]Phase 3.1 evaluation failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc


def _phase321_validate_datasets(
    dev_dataset: Path,
    challenge_dataset: Path,
) -> tuple[Path, Path, dict[str, Any]]:
    """Resolve and lint only the frozen Dev and Challenge Dev datasets."""

    supplied = {
        "dev": (dev_dataset, _PHASE321_DEV_FILENAME),
        "challenge_dev": (challenge_dataset, _PHASE321_CHALLENGE_FILENAME),
    }
    for label, (path, required_name) in supplied.items():
        if path.name.casefold() == _PHASE321_FORBIDDEN_TEST_FILENAME.casefold():
            raise ValueError(
                "Phase 3.2.1 must never read the exposed Router Test V1 dataset"
            )
        if path.name.casefold() != required_name.casefold():
            raise ValueError(
                f"Phase 3.2.1 {label} must use the frozen {required_name} dataset"
            )

    resolved_dev = dev_dataset.resolve()
    resolved_challenge = challenge_dataset.resolve()
    forbidden = (
        _PHASE35_EVAL_ROOT / _PHASE321_FORBIDDEN_TEST_FILENAME
    ).resolve()
    if resolved_dev == forbidden or resolved_challenge == forbidden:
        raise ValueError(
            "Phase 3.2.1 must never read the exposed Router Test V1 dataset"
        )
    if resolved_dev == resolved_challenge:
        raise ValueError("Phase 3.2.1 Dev and Challenge Dev must be distinct datasets")
    if not resolved_dev.is_file() or not resolved_challenge.is_file():
        raise ValueError("Phase 3.2.1 dataset path does not exist")

    dev_lint = validate_router_safety_dataset(resolved_dev)
    challenge_lint = validate_router_challenge_dataset(
        resolved_challenge,
        reference_paths=(resolved_dev,),
    )
    if not dev_lint.get("passed") or int(dev_lint.get("case_count", 0)) != 120:
        raise ValueError(f"Phase 3.2.1 Dev lint failed: {dev_lint}")
    if (
        not challenge_lint.get("passed")
        or int(challenge_lint.get("case_count", 0)) != 120
    ):
        raise ValueError(f"Phase 3.2.1 Challenge Dev lint failed: {challenge_lint}")
    return resolved_dev, resolved_challenge, {
        "dev": dev_lint,
        "challenge_dev": challenge_lint,
        "loaded_datasets": [str(resolved_dev), str(resolved_challenge)],
        "old_router_test_loaded": False,
    }


def _phase321_load_before_references(before_dir: Path) -> dict[str, Any]:
    """Reuse prior Live reports as read-only before references, never as new results."""

    resolved_dir = before_dir.resolve()
    paths = {
        name: resolved_dir / filename
        for name, filename in _PHASE321_BEFORE_FILENAMES.items()
    }
    existing = {name for name, path in paths.items() if path.is_file()}
    if not existing:
        return {
            "status": "not_available",
            "directory": str(resolved_dir),
            "reports": {},
            "new_live_calls_for_before_references": 0,
            "historical_llm_case_calls_represented": 0,
        }
    missing = sorted(set(paths) - existing)
    if missing:
        raise ValueError(
            "Phase 3.2 before-reference bundle is partial; missing: "
            + ", ".join(missing)
        )

    summaries: dict[str, Any] = {}
    for name, path in paths.items():
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid Phase 3.2 before report {path}: {exc}") from exc
        if not isinstance(report, Mapping):
            raise ValueError(f"Phase 3.2 before report must be an object: {path}")
        provider = str(report.get("provider") or "").casefold()
        if not report.get("live_llm") or provider in {
            "",
            "none",
            "fixture",
            "fixture_semantic",
            "demo",
            "disabled",
            "off",
        }:
            raise ValueError(f"Phase 3.2 before report is not a real Live report: {path}")
        if int(report.get("case_count") or 0) != 120:
            raise ValueError(f"Phase 3.2 before report must contain 120 cases: {path}")
        expected_arm = "conditional" if name.startswith("conditional") else "always"
        expected_dataset = (
            _PHASE321_CHALLENGE_FILENAME
            if name.endswith("challenge_dev")
            else _PHASE321_DEV_FILENAME
        )
        dataset_value = report.get("dataset")
        if str(report.get("arm") or "").casefold() != expected_arm:
            raise ValueError(
                f"Phase 3.2 before report arm mismatch for {name}: {path}"
            )
        if not dataset_value or Path(str(dataset_value)).name.casefold() != (
            expected_dataset.casefold()
        ):
            raise ValueError(
                f"Phase 3.2 before report dataset mismatch for {name}: {path}"
            )
        if int(report.get("llm_success_count") or 0) <= 0:
            raise ValueError(
                f"Phase 3.2 before report contains no successful Live decision: {path}"
            )
        summaries[name] = {
            "path": str(path),
            "generated_at": report.get("generated_at"),
            "dataset": report.get("dataset"),
            "dataset_sha256": report.get("dataset_sha256"),
            "provider": report.get("provider"),
            "model": report.get("model"),
            "prompt_version": report.get("prompt_version"),
            "prompt_sha256": report.get("prompt_sha256"),
            "branch_macro_f1": report.get("branch_macro_f1"),
            "rag_recall": report.get("rag_recall"),
            "scenario_macro_f1": report.get("scenario_macro_f1"),
            "scenario_top2_hit": report.get("scenario_top2_hit"),
            "goal_micro_f1": report.get("goal_micro_f1"),
            "goal_macro_f1": report.get("goal_macro_f1"),
            "llm_call_rate": report.get("llm_call_rate"),
            "llm_called_count": report.get("llm_called_count"),
            "total_tokens": report.get("total_tokens"),
            "router_mean_latency_ms": report.get("router_mean_latency_ms"),
            "router_p95_latency_ms": report.get("router_p95_latency_ms"),
        }
    return {
        "status": "reused_as_read_only_before_reference",
        "directory": str(resolved_dir),
        "reports": summaries,
        "new_live_calls_for_before_references": 0,
        "historical_llm_case_calls_represented": sum(
            int(item.get("llm_called_count") or 0) for item in summaries.values()
        ),
        "note": (
            "Prior reports are comparison references only and are never relabeled "
            "as Phase 3.2.1 results."
        ),
    }


def _phase321_load_goal_policy_source(before_dir: Path) -> dict[str, Any]:
    """Load only the old Always-Dev trace used by the offline Goal sweep."""

    path = before_dir.resolve() / _PHASE321_BEFORE_FILENAMES["always_before_dev"]
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Phase 3.2 Always-Dev before report not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Phase 3.2 Always-Dev before report is invalid JSON: {path}") from exc
    if not isinstance(report, dict):
        raise ValueError("Phase 3.2 Always-Dev before report must be a JSON object")
    provider = str(report.get("provider") or "").casefold()
    dataset = report.get("dataset")
    if (
        report.get("arm") != "always"
        or report.get("live_llm") is not True
        or provider
        in {"", "demo", "fixture", "fixture_semantic", "none", "disabled", "off"}
        or not dataset
        or Path(str(dataset)).name.casefold() != _PHASE321_DEV_FILENAME.casefold()
    ):
        raise ValueError(
            "Phase 3.2 Goal Policy source must be a real Live Always-Dev report"
        )
    cases = report.get("cases")
    if not isinstance(cases, list) or len(cases) != 120:
        raise ValueError(
            "Phase 3.2 Goal Policy source must contain all 120 recorded Dev traces"
        )
    if int(report.get("llm_success_count") or 0) <= 0:
        raise ValueError(
            "Phase 3.2 Goal Policy source contains no successful Live decisions"
        )
    return report


@eval_app.command("router-phase3-2")
def router_phase32_eval(
    dev_dataset: Annotated[Path, typer.Option("--dev-dataset")] = _PHASE35_EVAL_ROOT
    / "loveapp_router_safety_eval_dev_v1.md",
    challenge_dataset: Annotated[Path, typer.Option("--challenge-dataset")] = _PHASE31_EVAL_ROOT
    / "loveapp_router_challenge_dev_v1.md",
    semantic_mode: Annotated[
        Literal["all", "off", "always", "conditional"],
        typer.Option("--semantic-mode", help="all, off, always, or conditional"),
    ] = "all",
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    prompt_version: Annotated[str | None, typer.Option("--prompt-version")] = None,
    output_dir: Annotated[Path, typer.Option("--output-dir")] = Path(".data/evals"),
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    smoke: Annotated[bool, typer.Option("--smoke/--no-smoke")] = True,
    repeatability: Annotated[bool, typer.Option("--repeatability/--no-repeatability")] = True,
    repeatability_runs: Annotated[int, typer.Option("--repeatability-runs", min=2, max=5)] = 3,
    repeatability_sample_size: Annotated[
        int, typer.Option("--repeatability-sample-size", min=20, max=30)
    ] = 24,
    input_cost_per_million: Annotated[
        float | None, typer.Option("--input-cost-per-million")
    ] = None,
    output_cost_per_million: Annotated[
        float | None, typer.Option("--output-cost-per-million")
    ] = None,
) -> None:
    """Run the Phase 3.2 Rule/Live Semantic Router experiment."""

    dev_dataset = dev_dataset.resolve()
    challenge_dataset = challenge_dataset.resolve()
    if not dev_dataset.exists() or not challenge_dataset.exists():
        raise typer.BadParameter("Phase 3.2 dataset path does not exist")
    try:
        challenge_lint = validate_router_challenge_dataset(
            challenge_dataset,
            reference_paths=(
                dev_dataset,
                _PHASE35_EVAL_ROOT / "loveapp_router_safety_eval_test_v1.md",
            ),
        )
        if (
            not challenge_lint.get("passed")
            or len(load_router_challenge_cases(challenge_dataset)) != 120
        ):
            raise ValueError(f"Challenge Dev lint failed: {challenge_lint}")
    except Exception as exc:
        raise typer.BadParameter(f"Phase 3.2 Challenge Dev validation failed: {exc}") from exc
    settings = get_settings()
    configured_router_llm_provider = settings.router_llm_provider
    configured_router_mode = (
        configured_router_llm_provider.casefold()
        if configured_router_llm_provider
        else ""
    )
    router_provider_label = None
    if configured_router_llm_provider and configured_router_mode not in {"auto", "llm"}:
        router_provider_label = configured_router_llm_provider
    effective_provider = provider or router_provider_label or settings.llm_provider
    effective_model = (
        model or settings.router_llm_model or settings.router_model or settings.llm_model
    )
    effective_prompt_version = (
        prompt_version
        or settings.router_llm_prompt_version
        or (
            "routing-v3.2-v1"
            if semantic_mode in {"always", "conditional", "all"}
            else settings.router_prompt_version
        )
    )
    if dry_run:
        dry = phase32_dry_run(
            settings=settings.model_copy(
                update={
                    "llm_provider": effective_provider,
                    "router_llm_provider": effective_provider,
                    "router_model": effective_model,
                    "router_prompt_version": effective_prompt_version,
                    "router_semantic_mode": (
                        "always"
                        if semantic_mode in {"all", "always"}
                        else "conditional"
                        if semantic_mode == "conditional"
                        else "off"
                    ),
                }
            ),
            dev_dataset=dev_dataset,
            challenge_dataset=challenge_dataset,
            output_path=output_dir,
        )
        console.print_json(json.dumps(dry, ensure_ascii=False))
        if not dry.get("ok", False):
            raise typer.Exit(code=2)
        return
    if semantic_mode == "off":
        for dataset_name, dataset_path in (
            ("dev", dev_dataset),
            ("challenge_dev", challenge_dataset),
        ):
            report = asyncio.run(evaluate_phase32_dataset(dataset_path, arm="rule"))
            output = output_dir / f"router_phase3_2_rule_{dataset_name}.json"
            write_phase32_report(report, output)
            console.print(f"[green]Phase 3.2 report saved:[/green] {output}")
        return
    if semantic_mode not in {"all", "always", "conditional"}:
        raise typer.BadParameter(f"Unsupported --semantic-mode: {semantic_mode}")
    if str(effective_provider).casefold() in {
        "",
        "demo",
        "fixture",
        "fixture_semantic",
        "none",
        "disabled",
        "off",
    }:
        raise typer.BadParameter(
            "Live LLM evaluation was not executed: fixture/demo provider is forbidden"
        )
    # Validate the caller's explicit paid-call gate before constructing a
    # runtime settings copy that enables the selected live arm.  Setting the
    # runtime flag first would accidentally turn a disabled `.env` gate into
    # an implicit opt-in.
    live_settings_candidate = settings.model_copy(
        update={
            "llm_provider": effective_provider,
            "router_provider": "llm",
            "router_llm_provider": "llm",
            "router_model": effective_model,
            "router_llm_model": effective_model,
            "router_prompt_version": effective_prompt_version,
            "router_llm_prompt_version": effective_prompt_version,
            "router_v2_enabled": True,
            "router_llm_correction_enabled": True,
        }
    )
    if not _phase35_live_router_configured(live_settings_candidate):
        raise typer.BadParameter(
            "Live LLM evaluation was not executed. Configure non-demo provider, model, "
            "API key, base URL, and LOVEAPP_ROUTER_LIVE_EVAL_ENABLED=true."
        )
    live_settings = live_settings_candidate.model_copy(
        update={"router_live_eval_enabled": True}
    )
    containers: list[Any] = []

    def router_factory(arm_name: str) -> Any:
        container = build_routing_container(
            live_settings.model_copy(update={"router_semantic_mode": arm_name})
        )
        containers.append(container)
        return container.router

    async def run_experiment() -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
        smoke_reports: dict[str, Any] | None = None
        try:
            if smoke:
                smoke_reports = {}
                for arm_name in ("always", "conditional"):
                    smoke_router = router_factory(arm_name)
                    smoke_report = await evaluate_phase32_smoke(
                        challenge_dataset, router=smoke_router, arm=arm_name, sample_size=8
                    )
                    smoke_reports[arm_name] = smoke_report
                    # A smoke run is the final preflight against the real
                    # provider.  If it produced no successful structured
                    # decision, stop before the 240-case run; per-case Rule
                    # fallback must never be serialized as a formal Live arm.
                    if int(smoke_report.get("llm_success_count") or 0) <= 0:
                        raise RuntimeError(
                            "Live LLM evaluation was not executed: smoke test produced "
                            f"no successful Live decisions for {arm_name} arm"
                        )
            reports = await evaluate_phase32_experiment(
                dev_dataset,
                challenge_dataset,
                router_factory=router_factory,
                live_metadata={
                    "provider": effective_provider,
                    "model": effective_model,
                    "temperature": live_settings.router_llm_temperature,
                    "max_tokens": live_settings.router_llm_max_tokens
                    or live_settings.router_max_tokens,
                    "timeout_seconds": live_settings.router_llm_timeout_seconds
                    or live_settings.router_timeout_seconds,
                    "max_retries": live_settings.router_llm_max_retries
                    if live_settings.router_llm_max_retries is not None
                    else live_settings.router_max_retries,
                    "prompt_version": effective_prompt_version,
                },
                input_cost_per_million=input_cost_per_million,
                output_cost_per_million=output_cost_per_million,
            )
            return reports, smoke_reports
        finally:
            for container in reversed(containers):
                await container.aclose()

    try:
        reports, smoke_reports = asyncio.run(run_experiment())
    except Exception as exc:
        console.print(
            "[red]Live LLM evaluation was not executed to completion; "
            f"no fixture fallback was used:[/red] {exc}"
        )
        raise typer.Exit(code=1) from exc
    if semantic_mode != "all":
        reports = {
            key: value
            for key, value in reports.items()
            if key.startswith("rule_") or key.startswith(f"{semantic_mode}_")
        }
    for key, report in reports.items():
        output_arm = (
            "live_always"
            if key.startswith("always_")
            else "live_conditional"
            if key.startswith("conditional_")
            else "rule"
        )
        dataset_name = "challenge_dev" if key.endswith("challenge_dev") else "dev"
        output = output_dir / f"router_phase3_2_{output_arm}_{dataset_name}.json"
        write_phase32_report(report, output)
        console.print(f"[green]Phase 3.2 report saved:[/green] {output}")
    if smoke_reports is not None:
        smoke_path = output_dir / "router_phase3_2_smoke.json"
        smoke_path.parent.mkdir(parents=True, exist_ok=True)
        smoke_path.write_text(
            json.dumps(smoke_reports, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        console.print(f"[green]Phase 3.2 smoke report saved:[/green] {smoke_path}")
    if semantic_mode == "all":
        findings = Path("docs/rag/PHASE3_2_LIVE_LLM_ROUTER_FINDINGS.md")
        write_phase32_findings(reports, findings)
        console.print(f"[green]Phase 3.2 Findings saved:[/green] {findings}")
        if repeatability:

            async def run_repeatability() -> dict[str, Any]:
                repeat_containers: list[Any] = []

                def repeat_factory(arm_name: str, _run_index: int) -> Any:
                    container = build_routing_container(
                        live_settings.model_copy(update={"router_semantic_mode": arm_name})
                    )
                    repeat_containers.append(container)
                    return container.router

                try:
                    return await evaluate_phase32_repeatability(
                        challenge_dataset,
                        router_factory=repeat_factory,
                        arm="always",
                        runs=repeatability_runs,
                        sample_size=repeatability_sample_size,
                    )
                finally:
                    for container in reversed(repeat_containers):
                        await container.aclose()

            try:
                repeat_report = asyncio.run(run_repeatability())
                repeat_path = output_dir / "router_phase3_2_repeatability.json"
                repeat_path.parent.mkdir(parents=True, exist_ok=True)
                repeat_path.write_text(
                    json.dumps(repeat_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                console.print(f"[green]Phase 3.2 repeatability saved:[/green] {repeat_path}")
            except Exception as exc:
                console.print(f"[red]Phase 3.2 repeatability failed:[/red] {exc}")
                raise typer.Exit(code=1) from exc


@eval_app.command("router-phase3-2-1")
def router_phase321_eval(
    dev_dataset: Annotated[Path, typer.Option("--dev-dataset")] = _PHASE35_EVAL_ROOT
    / _PHASE321_DEV_FILENAME,
    challenge_dataset: Annotated[Path, typer.Option("--challenge-dataset")] = _PHASE31_EVAL_ROOT
    / _PHASE321_CHALLENGE_FILENAME,
    before_dir: Annotated[
        Path,
        typer.Option(
            "--before-dir",
            help="Directory containing the four read-only Phase 3.2 before reports.",
        ),
    ] = Path(".data/evals"),
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    prompt_version: Annotated[str | None, typer.Option("--prompt-version")] = None,
    max_retries: Annotated[
        int | None,
        typer.Option("--max-retries", min=0, max=3, help="Optional Live provider retry override."),
    ] = None,
    output_dir: Annotated[Path, typer.Option("--output-dir")] = Path(".data/evals"),
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    input_cost_per_million: Annotated[
        float | None, typer.Option("--input-cost-per-million")
    ] = None,
    output_cost_per_million: Annotated[
        float | None, typer.Option("--output-cost-per-million")
    ] = None,
) -> None:
    """Run the fixed Phase 3.2.1 Router stabilization experiment."""

    try:
        dev_dataset, challenge_dataset, dataset_validation = (
            _phase321_validate_datasets(
                dev_dataset,
                challenge_dataset,
            )
        )
        before_references = _phase321_load_before_references(before_dir)
        goal_policy_source_report = _phase321_load_goal_policy_source(before_dir)
    except Exception as exc:
        raise typer.BadParameter(f"Phase 3.2.1 preflight failed: {exc}") from exc

    settings = get_settings()
    effective_provider = resolve_phase32_provider(settings, provider)
    effective_model = (
        model or settings.router_llm_model or settings.router_model or settings.llm_model
    )
    effective_prompt_version = (
        prompt_version
        or settings.router_llm_prompt_version
        or "routing-v3.2.1-v1"
    )
    live_updates: dict[str, Any] = {
        "llm_provider": effective_provider,
        "router_provider": "llm",
        "router_llm_provider": effective_provider,
        "router_model": effective_model,
        "router_llm_model": effective_model,
        "router_prompt_version": effective_prompt_version,
        "router_llm_prompt_version": effective_prompt_version,
        "router_v2_enabled": True,
        "router_llm_correction_enabled": True,
        "router_llm_temperature": 0,
        "router_goal_secondary_threshold": 0.0,
        "router_goal_max_count": 3,
        "router_conditional_trigger_profile": "c2",
    }
    if max_retries is not None:
        live_updates["router_llm_max_retries"] = max_retries
    live_candidate = settings.model_copy(update=live_updates)
    effective_max_retries = (
        live_candidate.router_llm_max_retries
        if live_candidate.router_llm_max_retries is not None
        else live_candidate.router_max_retries
    )

    if dry_run:
        call_budget = phase321_call_budget(
            dev_case_count=int(dataset_validation["dev"]["case_count"]),
            challenge_case_count=int(
                dataset_validation["challenge_dev"]["case_count"]
            ),
            max_retries=effective_max_retries,
        )
        dry = phase32_dry_run(
            settings=live_candidate.model_copy(update={"router_semantic_mode": "always"}),
            dev_dataset=dev_dataset,
            challenge_dataset=challenge_dataset,
            output_path=output_dir,
        )
        dry["phase"] = "3.2.1"
        dry["dataset_validation"] = dataset_validation
        dry["before_references"] = before_references
        dry["goal_policy_selection"] = "read-only Phase 3.2 Always Dev trace replay"
        dry["conditional_profiles"] = ["c0", "c1", "c2"]
        dry["challenge_profiles"] = ["selected_on_dev"]
        dry["repeatability"] = {"runs": 3, "sample_size": 24}
        dry["call_budget"] = call_budget
        dry["old_router_test_loaded"] = False
        console.print_json(json.dumps(dry, ensure_ascii=False))
        if not dry.get("ok", False):
            raise typer.Exit(code=2)
        return

    if str(effective_provider).casefold() in {
        "",
        "demo",
        "fixture",
        "fixture_semantic",
        "none",
        "disabled",
        "off",
    }:
        raise typer.BadParameter(
            "Live LLM evaluation was not executed: fixture/demo provider is forbidden"
        )
    if not _phase35_live_router_configured(live_candidate):
        raise typer.BadParameter(
            "Live LLM evaluation was not executed. Configure non-demo provider, model, "
            "API key, base URL, and LOVEAPP_ROUTER_LIVE_EVAL_ENABLED=true."
        )
    live_settings = live_candidate.model_copy(update={"router_live_eval_enabled": True})
    call_budget = phase321_call_budget(
        dev_case_count=int(dataset_validation["dev"]["case_count"]),
        challenge_case_count=int(dataset_validation["challenge_dev"]["case_count"]),
        max_retries=effective_max_retries,
    )
    console.print(
        "[yellow]Phase 3.2.1 Live budget:[/yellow] "
        f"up to {call_budget['llm_case_call_upper_bound']} LLM-routed cases / "
        f"{call_budget['provider_attempt_upper_bound']} provider attempts "
        "(Goal Policy replay adds zero calls)."
    )
    if before_references.get("status") == "reused_as_read_only_before_reference":
        console.print(
            "[dim]Four Phase 3.2 reports will be reused only as before references; "
            "they are not rerun or relabeled.[/dim]"
        )

    containers: list[Any] = []

    def router_factory(
        arm: str,
        trigger_profile: str,
        goal_threshold: float,
        goal_max_count: int,
    ) -> Any:
        container = build_routing_container(
            live_settings.model_copy(
                update={
                    "router_semantic_mode": arm,
                    "router_conditional_trigger_profile": trigger_profile,
                    "router_goal_secondary_threshold": goal_threshold,
                    "router_goal_max_count": goal_max_count,
                }
            )
        )
        containers.append(container)
        return container.router

    async def run_experiment() -> dict[str, dict[str, Any]]:
        try:
            return await evaluate_phase321_experiment(
                dev_dataset,
                challenge_dataset,
                router_factory=router_factory,
                live_metadata={
                    "provider": effective_provider,
                    "model": effective_model,
                    "temperature": live_settings.router_llm_temperature,
                    "max_tokens": live_settings.router_llm_max_tokens
                    or live_settings.router_max_tokens,
                    "timeout_seconds": live_settings.router_llm_timeout_seconds
                    or live_settings.router_timeout_seconds,
                    "max_retries": live_settings.router_llm_max_retries
                    if live_settings.router_llm_max_retries is not None
                    else live_settings.router_max_retries,
                    "prompt_version": effective_prompt_version,
                },
                goal_policy_source_report=goal_policy_source_report,
                before_references={
                    **before_references,
                },
                repeatability_runs=3,
                repeatability_sample_size=24,
                input_cost_per_million=input_cost_per_million,
                output_cost_per_million=output_cost_per_million,
            )
        finally:
            for container in reversed(containers):
                await container.aclose()

    try:
        reports = asyncio.run(run_experiment())
    except Exception as exc:
        console.print(
            "[red]Phase 3.2.1 Live evaluation was not executed to completion; "
            "no formal report bundle was written and no fixture fallback was used:[/red] "
            f"{type(exc).__name__}: {exc}"
        )
        raise typer.Exit(code=1) from exc

    for report in reports.values():
        report["dataset_validation"] = dataset_validation
    try:
        written = write_phase321_reports(
            reports,
            output_dir,
            require_complete=True,
        )
    except Exception as exc:
        console.print(f"[red]Phase 3.2.1 report write failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    for report_name, path in written.items():
        console.print(f"[green]Phase 3.2.1 report saved ({report_name}):[/green] {path}")
    protocol = reports["always_dev"].get("phase321_protocol", {})
    console.print(
        "[green]Phase 3.2.1 selection:[/green] "
        f"goal={protocol.get('goal_policy')}, "
        f"conditional={protocol.get('conditional_profile')}"
    )


def _phase35_planner(
    settings: Any,
    *,
    rewrite: bool,
    decomposition: bool,
) -> RetrievalQueryPlanner:
    return RetrievalQueryPlanner(
        contextual_query_rewrite_enabled=rewrite,
        query_decomposition_enabled=decomposition,
        max_subqueries=getattr(settings, "max_subqueries", 3),
        history_window=getattr(settings, "contextual_rewrite_history_window", 4),
    )


@eval_app.command("contextual-rewrite")
def contextual_rewrite_eval(
    dataset: Annotated[
        Path,
        typer.Option("--dataset", help="Phase 4 contextual-rewrite Markdown dataset."),
    ] = _PHASE35_EVAL_ROOT / "loveapp_contextual_rewrite_eval_dev_v1.md",
    output: Annotated[
        Path,
        typer.Option("--output", help="JSON report path; Markdown is written beside it."),
    ] = Path(".data/evals/contextual_rewrite_dev.json"),
    rewrite: Annotated[
        bool,
        typer.Option("--rewrite/--no-rewrite", help="Enable the conditional rewrite stage."),
    ] = True,
    with_retrieval: Annotated[
        bool,
        typer.Option("--with-retrieval/--without-retrieval", help="Also run paired retrieval."),
    ] = False,
    knowledge: Annotated[
        Path,
        typer.Option(
            "--knowledge",
            help="V2 knowledge base used for RelevantIDs lint and optional retrieval.",
        ),
    ] = Path("knowledge/loveapp_rag_knowledge_base_v2.md"),
    ephemeral_qdrant: Annotated[
        bool,
        typer.Option("--ephemeral-qdrant/--configured-qdrant"),
    ] = False,
    fail_on_targets: Annotated[
        bool,
        typer.Option("--fail-on-targets/--no-fail-on-targets"),
    ] = False,
) -> None:
    """Evaluate conditional contextual Query Rewrite (Phase 4)."""

    dataset = _phase35_load_path(dataset, phase=4)
    settings = get_settings()
    planner = _phase35_planner(settings, rewrite=rewrite, decomposition=False)
    try:
        cases = load_contextual_rewrite_eval_markdown(dataset)
        # Read the frozen 500-document KB for RelevantIDs lint on every run.
        # This is deliberately read-only; indexing remains gated by
        # ``with_retrieval`` and the specialised fixtures never enter it.
        knowledge_path = _phase35_load_knowledge_path(knowledge)
        report_lint = _phase35_phase45_lint(
            rewrite_cases=cases,
            knowledge=knowledge_path,
        )
        if with_retrieval:
            report = asyncio.run(
                _run_phase35_retrieval_eval(
                    knowledge=knowledge_path,
                    settings=settings,
                    ephemeral_qdrant=ephemeral_qdrant,
                    evaluate=evaluate_contextual_rewrite,
                    cases=cases,
                    planner=planner,
                )
            )
        else:
            report = asyncio.run(evaluate_contextual_rewrite(cases, planner=planner))
        # The evaluator can be used standalone without a KB, but the CLI has
        # already loaded the frozen KB for lint.  Publish that authoritative
        # phase lint at the conventional top-level key as well as retaining
        # the wrapped ``dataset_lint`` diagnostics for provenance.
        if "phase4" in report_lint:
            report["lint"] = report_lint["phase4"]
        report["dataset_lint"] = report_lint
        report["inputs"] = {
            "dataset": str(dataset),
            "rewrite_enabled": rewrite,
            "with_retrieval": with_retrieval,
            "knowledge": str(knowledge_path),
        }
        json_path, markdown_path = write_phase45_report(report, output, phase=4)
    except Exception as exc:
        console.print(f"[red]Phase 4 contextual-rewrite evaluation failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Phase 4 reports saved:[/green] {json_path} and {markdown_path}")
    if fail_on_targets:
        trigger = report.get("trigger", {})
        if float(trigger.get("f1", 0)) < 0.90 or float(report.get("query_drift_rate", 0)) > 0.05:
            raise typer.Exit(code=2)


@eval_app.command("multiquery")
def multiquery_eval(
    dataset: Annotated[
        Path,
        typer.Option("--dataset", help="Phase 5 multi-query Markdown dataset."),
    ] = _PHASE35_EVAL_ROOT / "loveapp_multiquery_eval_dev_v1.md",
    output: Annotated[
        Path,
        typer.Option("--output", help="JSON report path; Markdown is written beside it."),
    ] = Path(".data/evals/multiquery_dev.json"),
    decomposition: Annotated[
        bool,
        typer.Option("--decomposition/--no-decomposition", help="Enable bounded decomposition."),
    ] = True,
    rewrite: Annotated[
        bool,
        typer.Option("--rewrite/--no-rewrite", help="Enable rewrite before decomposition."),
    ] = False,
    with_retrieval: Annotated[
        bool,
        typer.Option(
            "--with-retrieval/--without-retrieval",
            help="Also run multi-query retrieval.",
        ),
    ] = False,
    knowledge: Annotated[
        Path,
        typer.Option(
            "--knowledge",
            help="V2 knowledge base used for RelevantIDs lint and optional retrieval.",
        ),
    ] = Path("knowledge/loveapp_rag_knowledge_base_v2.md"),
    ephemeral_qdrant: Annotated[
        bool,
        typer.Option("--ephemeral-qdrant/--configured-qdrant"),
    ] = False,
    fail_on_targets: Annotated[
        bool,
        typer.Option("--fail-on-targets/--no-fail-on-targets"),
    ] = False,
) -> None:
    """Evaluate bounded Multi-query Decomposition (Phase 5)."""

    dataset = _phase35_load_path(dataset, phase=5)
    settings = get_settings()
    planner = _phase35_planner(settings, rewrite=rewrite, decomposition=decomposition)
    try:
        cases = load_multiquery_eval_markdown(dataset)
        # Read the frozen 500-document KB for RelevantIDs lint on every run.
        # This is deliberately read-only; indexing remains gated by
        # ``with_retrieval`` and the specialised fixtures never enter it.
        knowledge_path = _phase35_load_knowledge_path(knowledge)
        report_lint = _phase35_phase45_lint(
            multiquery_cases=cases,
            knowledge=knowledge_path,
        )
        if with_retrieval:
            report = asyncio.run(
                _run_phase35_retrieval_eval(
                    knowledge=knowledge_path,
                    settings=settings,
                    ephemeral_qdrant=ephemeral_qdrant,
                    evaluate=evaluate_multiquery,
                    cases=cases,
                    planner=planner,
                )
            )
        else:
            report = asyncio.run(evaluate_multiquery(cases, planner=planner))
        if "phase5" in report_lint:
            report["lint"] = report_lint["phase5"]
        report["dataset_lint"] = report_lint
        report["inputs"] = {
            "dataset": str(dataset),
            "rewrite_enabled": rewrite,
            "decomposition_enabled": decomposition,
            "with_retrieval": with_retrieval,
            "knowledge": str(knowledge_path),
        }
        json_path, markdown_path = write_phase45_report(report, output, phase=5)
    except Exception as exc:
        console.print(f"[red]Phase 5 multiquery evaluation failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Phase 5 reports saved:[/green] {json_path} and {markdown_path}")
    if fail_on_targets:
        trigger = report.get("trigger", {})
        if float(trigger.get("f1", 0)) < 0.90:
            raise typer.Exit(code=2)


async def _run_phase35_retrieval_eval(
    *,
    knowledge: Path,
    settings: Any,
    ephemeral_qdrant: bool,
    evaluate,
    cases,
    planner: RetrievalQueryPlanner,
) -> dict[str, Any]:
    """Run an optional retrieval arm in one event loop and close its resources."""

    knowledge = _phase35_load_knowledge_path(knowledge)
    documents = load_knowledge_path(knowledge)
    updates = {"qdrant_url": ":memory:"} if ephemeral_qdrant else {}
    eval_settings = settings.model_copy(update=updates) if updates else settings
    retriever = build_qdrant_store(eval_settings)
    try:
        if ephemeral_qdrant:
            await retriever.index_documents(list(documents), recreate=True)
        return await evaluate(cases, planner=planner, retriever=retriever)
    finally:
        await retriever.aclose()


@eval_app.command("phase3-5-ablation")
def phase35_ablation_eval(
    dataset: Annotated[
        Path | None,
        typer.Option(
            "--dataset",
            help="Optional fixture directory shorthand; when supplied it selects all phase paths.",
        ),
    ] = None,
    router_dataset: Annotated[
        Path,
        typer.Option("--router-dataset", help="Phase 3 dataset."),
    ] = _PHASE35_EVAL_ROOT / "loveapp_router_safety_eval_dev_v1.md",
    rewrite_dataset: Annotated[
        Path,
        typer.Option("--rewrite-dataset", help="Phase 4 dataset."),
    ] = _PHASE35_EVAL_ROOT / "loveapp_contextual_rewrite_eval_dev_v1.md",
    multiquery_dataset: Annotated[
        Path,
        typer.Option("--multiquery-dataset", help="Phase 5 dataset."),
    ] = _PHASE35_EVAL_ROOT / "loveapp_multiquery_eval_dev_v1.md",
    output: Annotated[
        Path,
        typer.Option("--output", help="Combined A/B/C/D JSON report."),
    ] = Path(".data/evals/phase3_5_ablation.json"),
    router_v2: Annotated[
        bool,
        typer.Option(
            "--router-v2/--current-router",
            help="Keep the canonical B/C/D Router V2 arm enabled (A remains current).",
        ),
    ] = True,
    rewrite: Annotated[
        bool,
        typer.Option(
            "--rewrite/--no-rewrite",
            help="Keep the canonical C/D rewrite arm enabled.",
        ),
    ] = True,
    decomposition: Annotated[
        bool,
        typer.Option(
            "--decomposition/--no-decomposition",
            help="Keep the canonical D decomposition arm enabled.",
        ),
    ] = True,
) -> None:
    """Run the controlled Phase 3--5 A/B/C/D ablation without retrieval."""

    # The combined experiment is a protocol, not a free-form feature sweep:
    # A/B/C/D must remain current/off/off, V2/off/off, V2/on/off and
    # V2/on/on.  Keep the legacy switches in the CLI for discoverability and
    # backwards-compatible help output, but reject attempts to alter the
    # canonical arms instead of silently producing a different experiment.
    if not router_v2 or not rewrite or not decomposition:
        raise typer.BadParameter(
            "phase3-5-ablation uses the fixed canonical A/B/C/D protocol; "
            "do not disable --router-v2, --rewrite, or --decomposition"
        )

    if dataset is not None:
        if dataset.is_dir():
            # The combined ablation defaults to the Dev split.  A Test run can
            # still be requested by supplying the three explicit Test paths.
            split = "dev"
            router_dataset = dataset / f"loveapp_router_safety_eval_{split}_v1.md"
            rewrite_dataset = dataset / f"loveapp_contextual_rewrite_eval_{split}_v1.md"
            multiquery_dataset = dataset / f"loveapp_multiquery_eval_{split}_v1.md"
        elif "router_safety" in dataset.name.casefold():
            split = "test" if "_test_" in dataset.name.casefold() else "dev"
            router_dataset = dataset
            rewrite_dataset = dataset.with_name(f"loveapp_contextual_rewrite_eval_{split}_v1.md")
            multiquery_dataset = dataset.with_name(f"loveapp_multiquery_eval_{split}_v1.md")
        else:
            raise ValueError(
                "--dataset for phase3-5-ablation must be the phase3_5 fixture directory "
                "or the Router/Safety dataset"
            )
    router_dataset = _phase35_load_path(router_dataset, phase=3)
    rewrite_dataset = _phase35_load_path(rewrite_dataset, phase=4)
    multiquery_dataset = _phase35_load_path(multiquery_dataset, phase=5)
    if not (router_v2 and rewrite and decomposition):
        raise typer.BadParameter(
            "phase3-5-ablation is a frozen protocol: use the canonical "
            "A=current/off/off/off, B=V2/off/off, C=V2/on/off, D=V2/on/on arms; "
            "override flags cannot disable a canonical feature."
        )
    settings = get_settings()

    def _make_ablation_router(enabled: bool) -> HybridRouter:
        return HybridRouter(
            SafetyPolicy(context_turns=settings.router_context_risk_turns),
            confidence_threshold=settings.router_confidence_threshold,
            ambiguity_margin=settings.router_ambiguity_margin,
            clarification_threshold=settings.router_clarification_threshold,
            prompt_version=settings.router_prompt_version,
            router_v2_enabled=enabled,
        )

    current_router = _make_ablation_router(False)
    router_v2_instance = _make_ablation_router(True)

    def _run_ablation_arm(
        *,
        router_enabled: bool,
        rewrite_enabled: bool,
        decomposition_enabled: bool,
    ) -> dict[str, Any]:
        planner = _phase35_planner(
            settings,
            rewrite=rewrite_enabled,
            decomposition=decomposition_enabled,
        )
        return {
            "router_v2": router_enabled,
            "rewrite": rewrite_enabled,
            "decomposition": decomposition_enabled,
            "router": asyncio.run(
                evaluate_router_safety(
                    router_dataset,
                    router=router_v2_instance if router_enabled else current_router,
                    router_mode="router_v2" if router_enabled else "current",
                )
            ),
            "contextual_rewrite": asyncio.run(
                evaluate_contextual_rewrite(rewrite_cases, planner=planner)
            ),
            "multiquery": asyncio.run(evaluate_multiquery(multi_cases, planner=planner)),
        }

    try:
        load_router_safety_cases(router_dataset)
        rewrite_cases = load_contextual_rewrite_eval_markdown(rewrite_dataset)
        multi_cases = load_multiquery_eval_markdown(multiquery_dataset)
        try:
            phase35_knowledge = _phase35_load_knowledge_path(
                Path("knowledge/loveapp_rag_knowledge_base_v2.md")
            )
            phase35_knowledge_ids = [
                document.id for document in load_knowledge_path(phase35_knowledge)
            ]
        except FileNotFoundError:
            phase35_knowledge = None
            phase35_knowledge_ids = None
        dataset_lint = {
            "knowledge": str(phase35_knowledge) if phase35_knowledge else None,
            "phase4": validate_contextual_rewrite_dataset(
                rewrite_cases,
                knowledge_ids=phase35_knowledge_ids,
                source_ref="phase4",
            ),
            "phase5": validate_multiquery_dataset(
                multi_cases,
                knowledge_ids=phase35_knowledge_ids,
                source_ref="phase5",
            ),
        }
        # Keep arms explicit even though Phase 4/5 use their own specialised
        # gold sets; this makes the causal comparisons auditable.
        arms: dict[str, Any] = {}
        # Canonical controlled arms required by the Phase 3--5 protocol:
        # A=current/off/off, B=V2/off/off, C=V2/on/off, D=V2/on/on.
        arms["A"] = _run_ablation_arm(
            router_enabled=False,
            rewrite_enabled=False,
            decomposition_enabled=False,
        )
        arms["B"] = _run_ablation_arm(
            router_enabled=router_v2,
            rewrite_enabled=False,
            decomposition_enabled=False,
        )
        arms["C"] = _run_ablation_arm(
            router_enabled=router_v2,
            rewrite_enabled=rewrite,
            decomposition_enabled=False,
        )
        arms["D"] = _run_ablation_arm(
            router_enabled=router_v2,
            rewrite_enabled=rewrite,
            decomposition_enabled=decomposition,
        )
        # Do not claim a single cross-phase score.  Each arm keeps the exact
        # evaluator report and only exposes safe scalar deltas where available.
        report = {
            "schema_version": 1,
            "status": "completed",
            "experiment": "phase3_5_ablation",
            "arms": arms,
            "inputs": {
                "router_dataset": str(router_dataset),
                "rewrite_dataset": str(rewrite_dataset),
                "multiquery_dataset": str(multiquery_dataset),
                "router_v2_requested": router_v2,
                "rewrite_requested": rewrite,
                "decomposition_requested": decomposition,
            },
            "comparisons": _phase35_ablation_comparisons(arms),
            "dataset_lint": dataset_lint,
            "notes": [
                "Specialized Phase 3--5 fixtures are never indexed or embedded.",
                "Relationship stage is not scored because RouteResult does not predict it.",
            ],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        json_path = _phase35_json_path(output)
        markdown_path = json_path.with_suffix(".md")
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(_render_phase35_ablation_report(report), encoding="utf-8")
    except Exception as exc:
        console.print(f"[red]Phase 3--5 ablation failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Phase 3--5 ablation saved:[/green] {json_path} and {markdown_path}")


def _phase35_ablation_comparisons(arms: Mapping[str, Any]) -> dict[str, Any]:
    """Return explicit, per-phase deltas for the frozen A/B/C/D arms."""

    definitions = {
        "A_to_B": (
            "A",
            "B",
            "Router/Safety gain",
            {
                "branch_macro_f1": ("router", "branch_macro_f1"),
                "scenario_primary_accuracy": ("router", "scenario_primary_accuracy"),
                "goal_micro_f1": ("router", "goal_micro_f1"),
                "high_risk_recall": ("router", "high_risk_recall"),
                "safety_bypass_rate": ("router", "safety_to_rag_bypass_rate"),
            },
        ),
        "B_to_C": (
            "B",
            "C",
            "Contextual Rewrite gain",
            {
                "trigger_f1": ("contextual_rewrite", "trigger.f1"),
                "query_drift_rate": ("contextual_rewrite", "query_drift_rate"),
            },
        ),
        "C_to_D": (
            "C",
            "D",
            "Multi-query gain",
            {
                "trigger_f1": ("multiquery", "trigger.f1"),
                "subquery_count_exact_accuracy": (
                    "multiquery",
                    "subquery_count_exact_accuracy",
                ),
                "need_recall_at_5": ("multiquery", "retrieval.need_recall_at_5"),
                "all_needs_covered_at_5": (
                    "multiquery",
                    "retrieval.all_needs_covered_at_5",
                ),
            },
        ),
        "A_to_D": (
            "A",
            "D",
            "Overall combined change (cross-phase reports; no pooled score)",
            {
                "branch_macro_f1": ("router", "branch_macro_f1"),
                "goal_micro_f1": ("router", "goal_micro_f1"),
                "rewrite_trigger_f1": ("contextual_rewrite", "trigger.f1"),
                "decomposition_trigger_f1": ("multiquery", "trigger.f1"),
            },
        ),
    }

    def read(arm: Mapping[str, Any], report: str, path: str) -> float | None:
        payload = arm.get(report, {})
        if not isinstance(payload, Mapping):
            return None
        if path.startswith("retrieval.") and payload.get("retrieval_executed") is False:
            return None
        value: Any = payload
        for part in path.split("."):
            if not isinstance(value, Mapping):
                return None
            value = value.get(part)
        return float(value) if isinstance(value, (int, float)) else None

    output: dict[str, Any] = {}
    for name, (left_name, right_name, description, metrics) in definitions.items():
        left = arms.get(left_name, {})
        right = arms.get(right_name, {})
        deltas: dict[str, float | None] = {}
        values: dict[str, dict[str, float | None]] = {}
        for metric, (report, path) in metrics.items():
            before = read(left, report, path)
            after = read(right, report, path)
            values[metric] = {"from": before, "to": after}
            deltas[metric] = (
                round(after - before, 4) if before is not None and after is not None else None
            )
        output[name] = {
            "from": left_name,
            "to": right_name,
            "description": description,
            "values": values,
            "delta": deltas,
        }
    return output


def _render_phase35_ablation_report(report: Mapping[str, Any]) -> str:
    lines = [
        "# LoveApp Phase 3--5 A/B/C/D Ablation",
        "",
        "| Arm | Router V2 | Rewrite | Decomposition | Available reports |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name in ("A", "B", "C", "D"):
        arm = report.get("arms", {}).get(name, {})
        reports = ", ".join(
            key for key in ("router", "contextual_rewrite", "multiquery") if key in arm
        )
        lines.append(
            f"| {name} | {arm.get('router_v2', False)} | {arm.get('rewrite', False)} | "
            f"{arm.get('decomposition', False)} | {reports or '-'} |"
        )
    lines.extend(
        [
            "",
            "Comparisons are reported as per-metric deltas in the JSON artifact: "
            "A→B Router/Safety; B→C Contextual Rewrite; C→D Multi-query; A→D overall.",
            "",
        ]
    )
    lint = report.get("dataset_lint", {})
    if lint:
        lines.extend(["Dataset lint:", ""])
        for phase in ("phase4", "phase5"):
            item = lint.get(phase, {})
            lines.append(
                f"- {phase}: passed={item.get('passed', False)}; "
                f"errors={len(item.get('errors', []))}; warnings={len(item.get('warnings', []))}"
            )
        lines.append("")
    return "\n".join(lines)


@eval_app.command("rag")
def rag_eval(
    dataset: Annotated[
        Path,
        typer.Option("--dataset", help="RAG V2 Dev/Test Markdown dataset path."),
    ] = Path("evals/rag/cases_v2_dev.md"),
    knowledge: Annotated[
        Path,
        typer.Option("--knowledge", help="RAG V2 knowledge-base path."),
    ] = Path("knowledge/loveapp_rag_knowledge_base_v2.md"),
    mode: Annotated[
        Literal["retriever", "e2e"],
        typer.Option("--mode", help="Evaluate retrieval alone or the routing-to-RAG path."),
    ] = "retriever",
    output: Annotated[
        Path,
        typer.Option("--output", help="JSON report path; Markdown is written beside it."),
    ] = Path(".data/evals/rag_v2_dev.json"),
    case: Annotated[
        list[str] | None,
        typer.Option("--case", help="Case id filter; repeat or use comma-separated values."),
    ] = None,
    query_type: Annotated[
        list[str] | None,
        typer.Option(
            "--query-type",
            help="QueryType filter; repeat or use comma-separated values.",
        ),
    ] = None,
    scenario: Annotated[
        list[str] | None,
        typer.Option("--scenario", help="Primary scenario filter; repeat or use commas."),
    ] = None,
    fail_on_targets: Annotated[
        bool,
        typer.Option(
            "--fail-on-targets/--no-fail-on-targets",
            help="Exit with code 2 when the selected evaluation misses a target.",
        ),
    ] = False,
    ephemeral_qdrant: Annotated[
        bool,
        typer.Option(
            "--ephemeral-qdrant/--configured-qdrant",
            help="在隔离的内存 Qdrant 中先索引指定 V2 KB；适用于本地复现评测。",
        ),
    ] = False,
    hard_filter: Annotated[
        bool | None,
        typer.Option(
            "--hard-filter/--soft-filter",
            help="显式覆盖本次评测的 metadata hard-filter；省略时沿用配置。",
        ),
    ] = None,
) -> None:
    """Run the RAG V2 integrity checks and retrieval evaluation."""

    try:
        documents = load_knowledge_path(knowledge)
        cases = load_rag_eval_markdown(dataset)
        integrity = _validate_rag_cli_dataset(dataset, documents, cases)
        if not integrity["passed"]:
            raise ValueError("; ".join(integrity["errors"]))

        case_ids = _split_eval_filters(case)
        query_types = _split_eval_filters(query_type)
        scenarios = _split_eval_filters(scenario)
        selected_cases = _filter_rag_eval_cases(
            cases,
            case_ids=case_ids,
            query_types=query_types,
            scenarios=scenarios,
        )
        if not selected_cases:
            raise ValueError("RAG V2 filters matched no cases")

        eval_kwargs: dict[str, Any] = {
            "mode": mode,
            "settings": get_settings(),
        }
        if ephemeral_qdrant:
            eval_kwargs.update(
                documents=documents,
                ephemeral_qdrant=True,
            )
        if hard_filter is not None:
            eval_kwargs["hard_filter"] = hard_filter
        report = asyncio.run(_run_rag_v2_cli_eval(selected_cases, **eval_kwargs))
        report["inputs"] = {
            "dataset": str(dataset),
            "knowledge": str(knowledge),
            "hard_filter": report.get("hard_filter", hard_filter),
            "filters": {
                "case": case_ids,
                "query_type": query_types,
                "scenario": scenarios,
            },
        }
        report["integrity"] = integrity
        json_path, markdown_path = write_rag_report(report, output)
    except Exception as exc:
        console.print(f"[red]RAG V2 evaluation failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]RAG V2 reports saved:[/green] {json_path} and {markdown_path}")
    table = Table(title=f"RAG V2 {mode} summary")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for key in ("case_count", "hit_at_1", "hit_at_3", "hit_at_5", "mrr", "ndcg_at_5"):
        table.add_row(key, str(report.get(key, 0)))
    table.add_row("targets_passed", str(report.get("targets", {}).get("passed", False)))
    console.print(table)

    if fail_on_targets and not report.get("targets", {}).get("passed", False):
        console.print("[red]RAG V2 acceptance targets were not met.[/red]")
        raise typer.Exit(code=2)


@eval_app.command("rag-sweep")
def rag_sweep_eval(
    dataset: Annotated[
        Path,
        typer.Option("--dataset", help="RAG V2 Dev Markdown dataset path."),
    ] = Path("evals/rag/cases_v2_dev.md"),
    knowledge: Annotated[
        Path,
        typer.Option("--knowledge", help="RAG V2 knowledge-base path."),
    ] = Path("knowledge/loveapp_rag_knowledge_base_v2.md"),
    output: Annotated[
        Path,
        typer.Option("--output", help="JSON report path; Markdown is written beside it."),
    ] = Path(".data/evals/rag_v2_dev_sweep.json"),
) -> None:
    """Tune RAG V2 retrieval on the Dev split and freeze the selected config."""

    try:
        documents = load_knowledge_path(knowledge)
        cases = load_rag_eval_markdown(dataset)
        if not cases or any(not case.id.startswith("rag_v2_dev_") for case in cases):
            raise ValueError("RAG V2 sweep accepts the Dev split only; Test is evaluation-only")

        integrity = _validate_rag_cli_dataset(dataset, documents, cases)
        if not integrity["passed"]:
            raise ValueError("; ".join(integrity["errors"]))

        report = asyncio.run(
            _run_rag_v2_cli_sweep(
                documents,
                cases,
                settings=get_settings(),
                progress=lambda message: console.print(f"[dim]{message}[/dim]"),
            )
        )
        report["inputs"] = {
            "dataset": str(dataset),
            "knowledge": str(knowledge),
        }
        report["integrity"] = integrity
        json_path, markdown_path = write_sweep_report(report, output)
    except Exception as exc:
        console.print(f"[red]RAG V2 Dev sweep failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]RAG V2 Dev sweep reports saved:[/green] {json_path} and {markdown_path}")
    table = Table(title="RAG V2 frozen retrieval config")
    table.add_column("Setting")
    table.add_column("Value", justify="right")
    for key, value in report["frozen_config"].items():
        table.add_row(key, str(value))
    console.print(table)


async def _run_rag_v2_cli_sweep(documents, cases, *, settings, progress=None):
    embedding_provider = build_embedding_provider(settings)
    try:
        return await run_rag_v2_dev_sweep(
            documents,
            cases,
            embedding_provider=embedding_provider,
            progress=progress,
        )
    finally:
        await embedding_provider.aclose()


def _validate_rag_cli_dataset(dataset: Path, documents, cases) -> dict[str, Any]:
    """Validate one requested split together with its sibling when available."""

    splits = {
        "dev"
        if case.id.startswith("rag_v2_dev_")
        else "test"
        if case.id.startswith("rag_v2_test_")
        else "unknown"
        for case in cases
    }
    if len(splits) != 1 or "unknown" in splits:
        raise ValueError("RAG V2 dataset must contain exactly one recognized Dev/Test split")
    split = next(iter(splits))
    counterpart_name = "cases_v2_test.md" if split == "dev" else "cases_v2_dev.md"
    counterpart_path = dataset.with_name(counterpart_name)
    if not counterpart_path.exists():
        repository_counterpart = Path("evals/rag") / counterpart_name
        counterpart_path = repository_counterpart if repository_counterpart.exists() else None

    counterpart_cases = (
        load_rag_eval_markdown(counterpart_path) if counterpart_path is not None else []
    )
    dev_cases = cases if split == "dev" else counterpart_cases
    test_cases = counterpart_cases if split == "dev" else cases
    integrity = validate_rag_v2_dataset(
        documents,
        dev_cases,
        test_cases,
        require_exact_counts=counterpart_path is not None,
    )
    if counterpart_path is None and len(documents) != 500:
        integrity["errors"].append(
            f"V2 knowledge base must contain exactly 500 documents; found {len(documents)}"
        )
        integrity["passed"] = False
    integrity["validated_splits"] = [split]
    if counterpart_path is not None:
        integrity["validated_splits"].append("test" if split == "dev" else "dev")
        integrity["counterpart_dataset"] = str(counterpart_path)
    return integrity


def _filter_rag_eval_cases(
    cases,
    *,
    case_ids: list[str],
    query_types: list[str],
    scenarios: list[str],
):
    case_filter = set(case_ids)
    query_type_filter = set(query_types)
    scenario_filter = set(scenarios)
    return [
        case
        for case in cases
        if (not case_filter or case.id in case_filter)
        and (not query_type_filter or case.query_type in query_type_filter)
        and (
            not scenario_filter
            or (
                case.expected_primary_scenario is not None
                and case.expected_primary_scenario.value in scenario_filter
            )
        )
    ]


async def _run_rag_v2_cli_eval(
    cases,
    *,
    mode: Literal["retriever", "e2e"],
    settings,
    documents=None,
    ephemeral_qdrant: bool = False,
    hard_filter: bool | None = None,
):
    updates: dict[str, Any] = {}
    if ephemeral_qdrant:
        updates["qdrant_url"] = ":memory:"
    if hard_filter is not None:
        updates["rag_hard_filter"] = hard_filter
    eval_settings = settings.model_copy(update=updates) if updates else settings
    retriever = build_qdrant_store(eval_settings)
    routing_container = None
    try:
        if ephemeral_qdrant:
            if documents is None:
                raise ValueError("ephemeral Qdrant evaluation requires the V2 knowledge documents")
            indexed = await retriever.index_documents(list(documents), recreate=True)
        else:
            indexed = None
        if mode == "e2e":
            routing_container = build_routing_container(eval_settings)
            report = await evaluate_rag_v2(
                cases,
                mode=mode,
                executor=build_e2e_executor(routing_container.router, retriever),
            )
            oracle_report = await evaluate_rag_v2(
                cases,
                mode="retriever",
                retriever=retriever,
            )
            report["oracle_gap"] = compare_oracle_and_e2e(oracle_report, report)
            report["targets"] = evaluate_rag_targets(report)
            report["retriever_backend"] = (
                "qdrant_memory" if ephemeral_qdrant else "qdrant_configured"
            )
            report["hard_filter"] = bool(getattr(eval_settings, "rag_hard_filter", False))
            if indexed is not None:
                report["indexed_document_count"] = indexed
            return report
        report = await evaluate_rag_v2(cases, mode=mode, retriever=retriever)
        report["retriever_backend"] = "qdrant_memory" if ephemeral_qdrant else "qdrant_configured"
        report["hard_filter"] = bool(getattr(eval_settings, "rag_hard_filter", False))
        if indexed is not None:
            report["indexed_document_count"] = indexed
        return report
    finally:
        try:
            if routing_container is not None:
                await routing_container.aclose()
        finally:
            await retriever.aclose()


@eval_app.command("rag-metadata-filter")
def rag_metadata_filter_eval(
    dataset: Annotated[
        Path,
        typer.Option("--dataset", help="RAG V2 Dev/Test Markdown dataset path."),
    ] = Path("evals/rag/cases_v2_dev.md"),
    knowledge: Annotated[
        Path,
        typer.Option("--knowledge", help="RAG V2 knowledge-base path."),
    ] = Path("knowledge/loveapp_rag_knowledge_base_v2.md"),
    output: Annotated[
        Path,
        typer.Option("--output", help="Comparison JSON path; Markdown is written beside it."),
    ] = Path(".data/evals/rag_v2_metadata_filter_dev.json"),
    ephemeral_qdrant: Annotated[
        bool,
        typer.Option(
            "--ephemeral-qdrant/--configured-qdrant",
            help="在隔离的内存 Qdrant 中索引 V2 KB，或使用已配置的 collection。",
        ),
    ] = False,
    router_mode: Annotated[
        Literal["rules", "configured", "live"],
        typer.Option(
            "--router-mode",
            help="Router 模式：rules（可复现）、configured（按配置）、live（默认跳过）。",
        ),
    ] = "rules",
) -> None:
    """Compare frozen Hard and Soft metadata-filter E2E arms."""

    # Live Router is intentionally opt-in and is not part of the controlled
    # Hard-vs-Soft comparison.  Emit an explicit skipped artifact instead of
    # making an unbounded external-model call or fabricating metrics.
    if router_mode == "live":
        report = {
            "schema_version": 1,
            "status": "skipped",
            "experiment": "rag_v2_metadata_filter_hard_vs_soft",
            "dataset": str(dataset),
            "knowledge": str(knowledge),
            "router_mode": "live",
            "reason": (
                "Live Router validation was explicitly skipped; set up a controlled live "
                "environment before running this optional experiment."
            ),
        }
        json_path, markdown_path = _write_metadata_filter_skipped_report(report, output)
        console.print(
            f"[yellow]Live Router comparison skipped:[/yellow] {json_path} and {markdown_path}"
        )
        return

    try:
        documents = load_knowledge_path(knowledge)
        cases = load_rag_eval_markdown(dataset)
        integrity = _validate_rag_cli_dataset(dataset, documents, cases)
        if not integrity["passed"]:
            raise ValueError("; ".join(integrity["errors"]))
        split = "dev" if cases[0].id.startswith("rag_v2_dev_") else "test"
        settings = get_settings()
        report = asyncio.run(
            _run_metadata_filter_cli_comparison(
                cases,
                documents=documents,
                settings=settings,
                dataset=split,
                ephemeral_qdrant=ephemeral_qdrant,
                router_mode=router_mode,
            )
        )
        report["inputs"] = {
            "dataset": str(dataset),
            "knowledge": str(knowledge),
            "ephemeral_qdrant": ephemeral_qdrant,
            "router_mode": router_mode,
            "frozen_hard_filter": True,
            "frozen_soft_filter": False,
        }
        report["integrity"] = integrity
        json_path, markdown_path = write_metadata_filter_comparison(report, output)
    except Exception as exc:
        console.print(f"[red]RAG metadata-filter comparison failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"[green]RAG metadata-filter reports saved:[/green] {json_path} and {markdown_path}"
    )
    table = Table(title=f"RAG V2 metadata filter ({router_mode}) summary")
    table.add_column("Metric")
    table.add_column("Hard", justify="right")
    table.add_column("Soft", justify="right")
    table.add_column("Delta", justify="right")
    overall = report.get("overall", {})
    for key in ("hit_at_3", "mrr", "candidate_recall", "coverage", "no_answer_f1"):
        values = overall.get(key, {})
        table.add_row(
            key,
            str(values.get("hard")),
            str(values.get("soft")),
            str(values.get("delta")),
        )
    amplification = report.get("hard_filter_amplification", {})
    table.add_row(
        "hard_filter_amplification_count",
        str(amplification.get("hard_filter_amplification_count", 0)),
        "-",
        "-",
    )
    console.print(table)


async def _run_metadata_filter_cli_comparison(
    cases,
    *,
    documents,
    settings,
    dataset: str,
    ephemeral_qdrant: bool,
    router_mode: Literal["rules", "configured"],
):
    """Run paired frozen Qdrant arms while sharing client, embeddings and Router output."""

    hard_config = MetadataFilterExperimentConfig.frozen(hard_filter=True)
    soft_config = MetadataFilterExperimentConfig.frozen(hard_filter=False)
    updates = {
        "rag_min_score": hard_config.min_score,
        "rag_candidate_limit": hard_config.candidate_limit,
        "rag_reranker_mode": hard_config.reranker_mode.value,
        "rag_lexical_weight": hard_config.lexical_weight,
        "rag_metadata_weight": hard_config.metadata_weight,
        "rag_retrieval_text_mode": hard_config.retrieval_text_mode.value,
    }
    if ephemeral_qdrant:
        updates["qdrant_url"] = ":memory:"
    eval_settings = settings.model_copy(update=updates)

    embedding_provider = build_embedding_provider(eval_settings)
    if ephemeral_qdrant or eval_settings.qdrant_url == ":memory:":
        client = AsyncQdrantClient(location=":memory:")
        backend = "qdrant_memory"
    else:
        client = AsyncQdrantClient(
            url=eval_settings.qdrant_url,
            timeout=eval_settings.qdrant_timeout_seconds,
        )
        backend = "qdrant_configured"

    common_kwargs = {
        "client": client,
        "collection_name": eval_settings.qdrant_collection,
        "embedding_provider": embedding_provider,
        "min_score": hard_config.min_score,
        "candidate_limit": hard_config.candidate_limit,
        "rerank_config": RerankConfig(
            mode=RerankerMode.FULL,
            lexical_weight=hard_config.lexical_weight,
            metadata_weight=hard_config.metadata_weight,
        ),
        "retrieval_text_mode": RetrievalTextMode.QUESTION_VARIANTS,
    }
    hard_retriever = QdrantKnowledgeStore(**common_kwargs, hard_filter=True)
    soft_retriever = QdrantKnowledgeStore(**common_kwargs, hard_filter=False)
    routing_container = None
    try:
        if ephemeral_qdrant:
            indexed = await hard_retriever.index_documents(list(documents), recreate=True)
        else:
            indexed = None

        if router_mode == "rules":
            router = HybridRouter(
                SafetyPolicy(context_turns=eval_settings.router_context_risk_turns),
                corrector=None,
                confidence_threshold=eval_settings.router_confidence_threshold,
                ambiguity_margin=eval_settings.router_ambiguity_margin,
                clarification_threshold=eval_settings.router_clarification_threshold,
                prompt_version=eval_settings.router_prompt_version,
            )
        else:
            routing_container = build_routing_container(eval_settings)
            router = routing_container.router

        hard_executor, soft_executor = build_metadata_filter_e2e_executors(
            router,
            hard_retriever,
            soft_retriever,
            top_k=hard_config.top_k,
        )
        report = await evaluate_metadata_filter_comparison(
            cases,
            hard_executor=hard_executor,
            soft_executor=soft_executor,
            mode="e2e",
            top_k=hard_config.top_k,
            dataset=dataset,
            hard_config=hard_config,
            soft_config=soft_config,
        )
        report["retriever_backend"] = backend
        report["router_mode"] = router_mode
        if indexed is not None:
            report["indexed_document_count"] = indexed
        return report
    finally:
        try:
            if routing_container is not None:
                await routing_container.aclose()
        finally:
            # The two stores deliberately share both objects; close each
            # resource exactly once rather than calling store.aclose() twice.
            try:
                await embedding_provider.aclose()
            finally:
                await client.close()


def _write_metadata_filter_skipped_report(
    report: dict[str, Any],
    output_path: Path,
) -> tuple[Path, Path]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    json_path = (
        output_path
        if output_path.suffix.casefold() == ".json"
        else output_path.with_suffix(".json")
    )
    markdown_path = json_path.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(
        "# LoveApp RAG V2 Metadata Filter Comparison\n\n"
        f"- Status: `{report.get('status', 'skipped')}`\n"
        f"- Router mode: `{report.get('router_mode', '-')}`\n"
        f"- Reason: {report.get('reason', '')}\n",
        encoding="utf-8",
    )
    return json_path, markdown_path


@eval_app.command("dateplan")
def dateplan_eval(
    dataset: Annotated[
        Path,
        typer.Option("--dataset", help="DatePlan 评测集路径。"),
    ] = Path("evals/dateplan/dateplan_cases_v1.jsonl"),
    output: Annotated[
        Path | None,
        typer.Option("--output", help="JSON/Markdown 报告路径；默认保存到 .data/evals。"),
    ] = None,
    case: Annotated[
        str | None,
        typer.Option("--case", help="只运行指定 scenario id。"),
    ] = None,
    category: Annotated[
        str | None,
        typer.Option("--category", help="只运行指定 category。"),
    ] = None,
) -> None:
    """运行固定 reference clock 的 DatePlan state/patch/validation 评估。"""

    output_path = output or Path(
        ".data/evals/dateplan_" + datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f") + ".json"
    )
    try:
        report = asyncio.run(
            evaluate_dateplan(
                dataset,
                case_id=case,
                category=category,
                output=None,
                trace_dir=Path(".data/evals"),
            )
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.suffix.casefold() == ".md":
            output_path.write_text(render_dateplan_report(report), encoding="utf-8")
        else:
            output_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if case is None and category is None:
                Path("DATEPLAN_EVAL_REPORT.md").write_text(
                    render_dateplan_report(report),
                    encoding="utf-8",
                )
    except Exception as exc:
        console.print(f"[red]DatePlan 评估失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]DatePlan 评估已保存：[/green]{output_path}")
    table = Table(title="DatePlan Evaluation")
    table.add_column("指标")
    table.add_column("值", justify="right")
    for key in (
        "scenario_count",
        "turn_count",
        "scenario_pass_rate",
        "patch_accuracy",
        "state_preservation_accuracy",
        "validation_accuracy",
        "final_plan_completion_rate",
    ):
        table.add_row(key, str(report.get(key, 0)))
    console.print(table)


@eval_app.command("memory-lifecycle")
def memory_lifecycle_eval(
    dataset: Annotated[
        Path,
        typer.Option("--dataset", help="确定性记忆生命周期评测集路径。"),
    ] = Path("evals/memory/lifecycle_v1.jsonl"),
    output: Annotated[
        Path,
        typer.Option("--output", help="记忆生命周期评测报告保存路径。"),
    ] = Path("evals/baselines/memory_lifecycle_v1.json"),
) -> None:
    """运行不调用真实模型的记忆治理与生命周期评测。"""
    try:
        report = asyncio.run(evaluate_memory_lifecycle(dataset))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        console.print(f"[red]记忆生命周期评测失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]记忆生命周期评测已保存：[/green]{output}")
    table = Table(title="Memory Lifecycle 指标")
    table.add_column("指标")
    table.add_column("值", justify="right")
    table.add_row("case_count", str(report["case_count"]))
    table.add_row("passed_case_count", str(report["passed_case_count"]))
    for key, value in report["metrics"].items():
        table.add_row(key, str(value))
    console.print(table)


@eval_app.command("memory-foundation")
def memory_foundation_eval(
    dataset: Annotated[
        Path,
        typer.Option("--dataset", help="Memory Foundation 确定性评测集路径。"),
    ] = Path("evals/memory/cases_v1.jsonl"),
    output: Annotated[
        Path,
        typer.Option("--output", help="Memory Foundation 评测报告保存路径。"),
    ] = Path("evals/baselines/memory_foundation_v1.json"),
    case: Annotated[
        str | None,
        typer.Option("--case", help="只运行指定 Case，例如 MEM-001。"),
    ] = None,
) -> None:
    """运行固定 extractor 输出的 Memory Foundation 端到端评测。"""
    try:
        report = asyncio.run(evaluate_memory_foundation(dataset, case_id=case))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        console.print(f"[red]Memory Foundation 评测失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Memory Foundation 评测已保存：[/green]{output}")
    table = Table(title="Memory Foundation 指标")
    table.add_column("指标")
    table.add_column("值", justify="right")
    table.add_row("case_count", str(report["case_count"]))
    table.add_row("passed_case_count", str(report["passed_case_count"]))
    for key, value in report["metrics"].items():
        table.add_row(key, str(value))
    console.print(table)


@eval_app.command("memory-admission-v1")
def memory_admission_v1_eval(
    dataset: Annotated[
        Path,
        typer.Option("--dataset", help="Memory Admission V1 72-case JSONL path."),
    ] = Path("evals/memory/admission_v1.jsonl"),
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Complete Admission baseline JSON path."),
    ] = None,
    integration_output: Annotated[
        Path | None,
        typer.Option("--integration-output", help="Integration diagnostic JSON path."),
    ] = None,
    case: Annotated[
        str | None,
        typer.Option("--case", help="Run one Admission case, for example ADM-001."),
    ] = None,
    slice_name: Annotated[
        str | None,
        typer.Option("--slice", help="Run one Admission evaluation slice."),
    ] = None,
    contract_status: Annotated[
        str | None,
        typer.Option("--contract-status", help="Filter by EXACT or POLICY_REVIEW."),
    ] = None,
    fail_on_error: Annotated[
        bool,
        typer.Option("--fail-on-error", help="Stop on evaluator execution errors."),
    ] = False,
) -> None:
    """Run the Admission V1 baseline and isolated integration diagnostic."""

    filtered_run = any(value is not None for value in (case, slice_name, contract_status))
    output_path = output or Path(".data/evals/memory_admission_v1_baseline.json")
    integration_path = integration_output or Path(
        ".data/evals/memory_admission_v1_integration.json"
    )
    try:
        report = evaluate_memory_admission_v1(
            dataset,
            case_id=case,
            slice_name=slice_name,
            contract_status=contract_status,
            fail_on_error=fail_on_error,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        output_path.with_suffix(".md").write_text(
            render_memory_admission_v1_report(report),
            encoding="utf-8",
        )
        if not filtered_run:
            Path("MEMORY_ADMISSION_V1_BASELINE_REPORT.md").write_text(
                render_memory_admission_v1_report(report),
                encoding="utf-8",
            )
            Path("MEMORY_ADMISSION_POLICY_REVIEW.md").write_text(
                render_memory_admission_policy_review(report),
                encoding="utf-8",
            )
            Path("MEMORY_ADMISSION_STRONG_REVIEW_AUDIT.md").write_text(
                render_memory_admission_strong_review_audit(report),
                encoding="utf-8",
            )
            integration = asyncio.run(evaluate_memory_admission_integration(dataset))
            integration_path.parent.mkdir(parents=True, exist_ok=True)
            integration_path.write_text(
                json.dumps(integration, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            integration_markdown = render_memory_admission_integration_diagnostic(integration)
            integration_path.with_suffix(".md").write_text(
                integration_markdown,
                encoding="utf-8",
            )
            Path("MEMORY_ADMISSION_V1_INTEGRATION_DIAGNOSTIC.md").write_text(
                integration_markdown,
                encoding="utf-8",
            )
    except Exception as exc:
        console.print(f"[red]Memory Admission V1 evaluation failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]Memory Admission V1 saved:[/green] {output_path}")
    table = Table(title="Memory Admission V1")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    metrics = report["metrics"]
    table.add_row("strict_case_count", str(metrics["strict_case_count"]))
    table.add_row("strict_passed_case_count", str(metrics["strict_passed_case_count"]))
    table.add_row("decision_accuracy", str(metrics["decision_accuracy"]))
    table.add_row("reason_accuracy", str(metrics["reason_accuracy"]))
    table.add_row("score_mae", str(metrics["score_mae"]))
    table.add_row("status", report["status"])
    console.print(table)


@eval_app.command("memory-extraction-v1-sync-langsmith")
def memory_extraction_v1_sync_langsmith(
    dataset: Annotated[
        Path,
        typer.Option("--dataset", help="Local Extraction V1 golden JSONL path."),
    ] = Path("evals/memory/extraction_v1_70.jsonl"),
    dataset_name: Annotated[
        str,
        typer.Option("--dataset-name", help="Stable LangSmith dataset name."),
    ] = MEMORY_EXTRACTION_LANGSMITH_DATASET,
) -> None:
    """Idempotently sync the synthetic Extraction V1 golden set to LangSmith."""

    try:
        result = sync_memory_extraction_dataset(
            dataset,
            dataset_name=dataset_name,
        )
    except Exception as exc:
        console.print(f"[red]LangSmith dataset sync failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(
        "[green]LangSmith dataset synced:[/green] "
        f"{result['dataset_name']} ({result['example_count']} examples)"
    )


@eval_app.command("memory-extraction-v1")
def memory_extraction_v1_eval(
    dataset: Annotated[
        Path,
        typer.Option("--dataset", help="Memory Extraction V1 70-case JSONL path."),
    ] = Path("evals/memory/extraction_v1_70.jsonl"),
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Complete JSON report path."),
    ] = None,
    case: Annotated[
        str | None,
        typer.Option("--case", help="Run one case, for example EXT-055."),
    ] = None,
    langsmith: Annotated[
        bool,
        typer.Option("--langsmith/--no-langsmith", help="Upload synthetic eval traces."),
    ] = False,
    sync_langsmith: Annotated[
        bool,
        typer.Option("--sync-langsmith", help="Idempotently sync the local golden dataset first."),
    ] = False,
    langsmith_dataset: Annotated[
        str,
        typer.Option("--langsmith-dataset", help="LangSmith dataset name."),
    ] = MEMORY_EXTRACTION_LANGSMITH_DATASET,
    fail_on_error: Annotated[
        bool,
        typer.Option("--fail-on-error", help="Stop on the first case execution failure."),
    ] = False,
) -> None:
    """Evaluate Flash raw, safe repair, and the production extraction cascade."""

    settings = get_settings()
    output_path = output or Path(
        ".data/evals/memory_extraction_v1_"
        + datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
        + ".json"
    )

    async def run() -> dict[str, Any]:
        cascade = _build_memory_extractor(settings)
        if isinstance(cascade, NoOpMemoryExtractor):
            raise ValueError("Memory Extraction V1 requires the configured Flash extractor.")
        flash = getattr(cascade, "_flash", None)
        if flash is None:
            raise ValueError("Memory Extraction V1 requires TieredMemoryExtractor.")
        if not settings.llm_api_key or not settings.llm_base_url:
            raise ValueError("LOVEAPP_LLM_API_KEY and LOVEAPP_LLM_BASE_URL are required.")
        matcher_model = (
            settings.memory_extraction_strong_model
            or settings.llm_model
            or settings.memory_extraction_model
        )
        if not matcher_model:
            raise ValueError("No model is configured for semantic claim alignment.")
        matcher = OpenAICompatibleExtractionAlignmentJudge(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=matcher_model,
            timeout_seconds=settings.memory_extraction_strong_timeout_seconds,
            max_retries=settings.memory_extraction_strong_max_retries,
            max_tokens=settings.memory_extraction_strong_max_tokens,
            thinking=settings.memory_extraction_strong_thinking,
        )
        observer = LangSmithExtractionObserver(
            enabled=langsmith,
            dataset_name=langsmith_dataset,
        )
        if langsmith and not observer.enabled:
            console.print(
                "[yellow]LangSmith upload disabled:[/yellow] "
                "LANGSMITH_API_KEY is not configured; continuing local evaluation."
            )
        try:
            return await evaluate_memory_extraction_v1(
                dataset,
                flash_extractor=flash,
                cascade_extractor=cascade,
                semantic_matcher=matcher,
                observer=observer,
                case_id=case,
                fail_on_error=fail_on_error,
            )
        finally:
            await matcher.aclose()
            close = getattr(cascade, "aclose", None)
            if callable(close):
                await close()

    try:
        if sync_langsmith:
            if langsmith_configured():
                sync_result = sync_memory_extraction_dataset(
                    dataset,
                    dataset_name=langsmith_dataset,
                )
                console.print(
                    "[green]LangSmith dataset synced:[/green] "
                    f"{sync_result['dataset_name']} "
                    f"({sync_result['example_count']} examples)"
                )
            else:
                console.print(
                    "[yellow]LangSmith dataset sync skipped:[/yellow] "
                    "LANGSMITH_API_KEY is not configured; continuing local evaluation."
                )
        report = asyncio.run(run())
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        flash_path = Path(".data/evals/memory_extraction_v1_flash_diagnostic.json")
        cascade_path = Path(".data/evals/memory_extraction_v1_production_cascade.json")
        flash_path.parent.mkdir(parents=True, exist_ok=True)
        flash_path.write_text(
            json.dumps(
                {
                    "evaluation": report["evaluation"],
                    "dataset": report["dataset"],
                    "models": report["models"],
                    "flash_raw": report["layers"]["flash_raw"],
                    "flash_post_repair": report["layers"]["flash_post_repair"],
                    "cases": [
                        {
                            "case_id": row["case_id"],
                            "flash_diagnostic": row["flash_diagnostic"],
                            "flash_raw": row["layers"]["flash_raw"],
                            "flash_post_repair": row["layers"]["flash_post_repair"],
                        }
                        for row in report["cases"]
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        cascade_path.write_text(
            json.dumps(
                {
                    "evaluation": report["evaluation"],
                    "dataset": report["dataset"],
                    "models": report["models"],
                    "production_cascade": report["layers"]["production_cascade"],
                    "contributions": report["contributions"],
                    "cases": [
                        {
                            "case_id": row["case_id"],
                            "attempts": row["cascade_attempts"],
                            "result": row["layers"]["production_cascade"],
                        }
                        for row in report["cases"]
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        markdown = render_memory_extraction_v1_report(report)
        Path("MEMORY_EXTRACTION_V1_EVAL_REPORT.md").write_text(markdown, encoding="utf-8")
        output_path.with_suffix(".md").write_text(markdown, encoding="utf-8")
    except Exception as exc:
        console.print(f"[red]Memory Extraction V1 evaluation failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    metrics = report["layers"]["production_cascade"]["metrics"]
    console.print(f"[green]Memory Extraction V1 saved:[/green] {output_path}")
    table = Table(title="Memory Extraction V1 Production Cascade")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for key in (
        "claim_recall",
        "spurious_claim_rate",
        "perspective_accuracy",
        "atomization_accuracy",
        "context_reply_recall",
        "empty_positive_rate",
        "negative_restraint_false_positive_rate",
    ):
        table.add_row(key, str(metrics[key]))
    console.print(table)


@eval_app.command("memory-normalization-v1")
def memory_normalization_v1_eval(
    dataset: Annotated[
        Path,
        typer.Option("--dataset", help="Memory Normalization V1 56-case JSONL path."),
    ] = Path("evals/memory/normalization_v1.jsonl"),
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Complete local JSON baseline path."),
    ] = None,
    case: Annotated[
        str | None,
        typer.Option("--case", help="Run one case, for example NORM-011."),
    ] = None,
    slice_name: Annotated[
        str | None,
        typer.Option("--slice", help="Run one normalization slice."),
    ] = None,
    fail_on_error: Annotated[
        bool,
        typer.Option("--fail-on-error", help="Stop on infrastructure execution errors."),
    ] = False,
) -> None:
    """Run the offline fixed-claim Normalization V1 baseline."""

    filtered_run = case is not None or slice_name is not None
    if output is not None:
        output_path = output
    elif filtered_run:
        filter_label = case or str(slice_name).replace("_", "-")
        output_path = Path(
            ".data/evals/memory_normalization_v1_"
            + filter_label
            + "_"
            + datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
            + ".json"
        )
    else:
        output_path = Path(".data/evals/memory_normalization_v1_baseline.json")

    try:
        report = evaluate_memory_normalization_v1(
            dataset,
            case_id=case,
            slice_name=slice_name,
            fail_on_error=fail_on_error,
            require_complete=case is None and slice_name is None,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        markdown = render_memory_normalization_v1_report(report)
        if not filtered_run:
            Path("MEMORY_NORMALIZATION_V1_EVAL_REPORT.md").write_text(
                markdown,
                encoding="utf-8",
            )
        output_path.with_suffix(".md").write_text(markdown, encoding="utf-8")
    except Exception as exc:
        console.print(f"[red]Memory Normalization V1 evaluation failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]Memory Normalization V1 saved:[/green] {output_path}")
    table = Table(title="Memory Normalization V1")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("case_count", str(report["case_count"]))
    table.add_row("passed_case_count", str(report["passed_case_count"]))
    for name in (
        "canonical_mapping_accuracy",
        "state_dimension_accuracy",
        "state_value_accuracy",
        "custom_preservation_accuracy",
        "unsafe_canonicalization_rate",
        "schema_validity",
        "idempotency_accuracy",
        "canonical_coverage",
    ):
        table.add_row(name, str(report["metrics"][name]))
    console.print(table)


@eval_app.command("memory-normalization-boundary")
def memory_normalization_boundary_eval(
    dataset: Annotated[
        Path,
        typer.Option("--dataset", help="Raw/Normalized validation boundary JSONL path."),
    ] = Path("evals/memory/normalization_boundary_v1.jsonl"),
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Complete local JSON report path."),
    ] = None,
    markdown: Annotated[
        Path | None,
        typer.Option("--markdown", help="Local Markdown report path."),
    ] = None,
    case: Annotated[
        str | None,
        typer.Option("--case", help="Run one boundary case, for example BND-001."),
    ] = None,
    fail_on_error: Annotated[
        bool,
        typer.Option("--fail-on-error", help="Stop on infrastructure execution errors."),
    ] = False,
) -> None:
    """Audit the Generic Validator/Normalizer/Canonical Validator boundary."""

    filtered_run = case is not None
    if output is not None:
        output_path = output
    elif filtered_run:
        output_path = Path(
            ".data/evals/memory_normalization_boundary_"
            + (case or "case")
            + "_"
            + datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
            + ".json"
        )
    else:
        output_path = Path(".data/evals/memory_normalization_boundary_v1.json")
    markdown_path = markdown or output_path.with_suffix(".md")
    try:
        report = evaluate_memory_normalization_boundary(
            dataset,
            case_id=case,
            fail_on_error=fail_on_error,
            require_complete=case is None,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(
            render_memory_normalization_boundary_report(report),
            encoding="utf-8",
        )
    except Exception as exc:
        console.print(f"[red]Memory normalization boundary evaluation failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Memory normalization boundary saved:[/green] {output_path}")
    table = Table(title="Memory Normalization Boundary")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("case_count", str(report["case_count"]))
    table.add_row("passed_case_count", str(report["passed_case_count"]))
    for name in (
        "generic_validation_acceptance_rate",
        "false_pre_normalization_rejection_rate",
        "normalizer_recovery_accuracy",
        "validation_boundary_rejection_count",
    ):
        table.add_row(name, str(report["metrics"][name]))
    console.print(table)


@eval_app.command("memory-gate-v2")
def memory_gate_v2_eval(
    dataset: Annotated[
        Path,
        typer.Option("--dataset", help="Memory Gate V2 60-case JSONL 路径。"),
    ] = Path("evals/memory/gate_v2_60.jsonl"),
    output: Annotated[
        Path | None,
        typer.Option("--output", help="JSON 或 Markdown 报告路径。"),
    ] = None,
    case: Annotated[
        str | None,
        typer.Option("--case", help="只运行指定 Gate case id。"),
    ] = None,
    category: Annotated[
        str | None,
        typer.Option("--category", help="只运行指定 Gate category。"),
    ] = None,
    fail_on_error: Annotated[
        bool,
        typer.Option("--fail-on-error", help="首次 Flash 调用异常时立即停止。"),
    ] = False,
) -> None:
    """用真实 Flash 同调用契约运行 Gate-only A/B 评测。"""

    output_path = output or Path(
        ".data/evals/memory_gate_v2_"
        + datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
        + ".json"
    )

    async def run() -> dict[str, Any]:
        extractor = _build_memory_extractor(get_settings())
        if isinstance(extractor, NoOpMemoryExtractor):
            raise ValueError(
                "Memory Gate V2 live eval requires the configured LLM Flash extractor."
            )
        try:
            return await evaluate_memory_gate_v2(
                dataset,
                extractor=extractor,
                fail_on_error=fail_on_error,
                case_id=case,
                category=category,
            )
        finally:
            close = getattr(extractor, "aclose", None)
            if callable(close):
                await close()

    try:
        report = asyncio.run(run())
        output_path.parent.mkdir(parents=True, exist_ok=True)
        markdown = render_memory_gate_v2_report(report)
        if output_path.suffix.casefold() == ".md":
            output_path.write_text(markdown, encoding="utf-8")
        else:
            output_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            output_path.with_suffix(".md").write_text(markdown, encoding="utf-8")
        Path("MEMORY_GATE_V2_EVAL_REPORT.md").write_text(
            markdown,
            encoding="utf-8",
        )
    except Exception as exc:
        console.print(f"[red]Memory Gate V2 评测失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]Memory Gate V2 评测已保存：[/green]{output_path}")
    table = Table(title="Memory Gate V2")
    table.add_column("指标")
    table.add_column("Current", justify="right")
    table.add_column("Hybrid", justify="right")
    table.add_row(
        "Recall",
        str(report["baseline"]["recall"]),
        str(report["hybrid"]["recall"]),
    )
    table.add_row(
        "Precision",
        str(report["baseline"]["precision"]),
        str(report["hybrid"]["precision"]),
    )
    table.add_row(
        "Specificity",
        str(report["baseline"]["specificity"]),
        str(report["hybrid"]["specificity"]),
    )
    table.add_row(
        "L0 routing accuracy",
        "-",
        str(report["metrics"]["routing_accuracy"]),
    )
    console.print(table)


@eval_app.command("memory-longtail-relations")
def memory_longtail_relations_eval(
    dataset: Annotated[
        Path,
        typer.Option("--dataset", help="Long-tail Memory relation 评测集路径。"),
    ] = Path("evals/memory/longtail_relations_v1.jsonl"),
    output: Annotated[
        Path | None,
        typer.Option("--output", help="JSON 报告路径；默认保存到 .data/evals。"),
    ] = None,
    case: Annotated[
        str | None,
        typer.Option("--case", help="只运行指定 Case，例如 LT-001。"),
    ] = None,
    live: Annotated[
        bool,
        typer.Option(
            "--live/--fixture",
            help="调用真实 semantic judge，或回放 fixture proposal。",
        ),
    ] = False,
    candidate_limit: Annotated[
        int,
        typer.Option("--candidate-limit", min=1, max=10, help="候选 Memory 上限。"),
    ] = 5,
) -> None:
    """在 shadow mode 评测长尾语义 relation；永不写入 Memory Store。"""

    mode = "live" if live else "fixture"
    output_path = output or _default_memory_longtail_output_path(mode=mode)
    try:
        if live:
            report = asyncio.run(
                _run_live_memory_longtail_relation_eval(
                    dataset,
                    settings=get_settings(),
                    case_id=case,
                    candidate_limit=candidate_limit,
                )
            )
        else:
            judge = FixtureSemanticRelationJudge.from_path(dataset)
            report = asyncio.run(
                _run_memory_longtail_relation_eval(
                    dataset,
                    judge=judge,
                    case_id=case,
                    candidate_limit=candidate_limit,
                )
            )
        report["semantic_judge_mode"] = mode
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if case is None:
            Path("MEMORY_LONGTAIL_BASELINE_REPORT.md").write_text(
                render_longtail_baseline_report(report),
                encoding="utf-8",
            )
    except Exception as exc:
        console.print(f"[red]Long-tail Memory relation 评测失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]Long-tail Memory relation 评测已保存：[/green]{output_path}")
    table = Table(title=f"Long-tail Memory Relation ({mode})")
    table.add_column("指标")
    table.add_column("值", justify="right")
    table.add_row("case_count", str(report["case_count"]))
    table.add_row("passed_case_count", str(report["passed_case_count"]))
    table.add_row("store_mutation_permitted", str(report["store_mutation_permitted"]))
    for key, value in report["metrics"].items():
        if not isinstance(value, (dict, list)):
            table.add_row(key, str(value))
    console.print(table)


def _default_memory_longtail_output_path(
    *,
    mode: str,
    now: datetime | None = None,
) -> Path:
    timestamp = (now or datetime.now().astimezone()).strftime("%Y%m%d_%H%M%S_%f")
    return Path(".data/evals") / f"memory_longtail_relations_{mode}_{timestamp}.json"


@eval_app.command("memory-longtail-write-v1")
def memory_longtail_write_v1_eval(
    dataset: Annotated[
        Path,
        typer.Option("--dataset", help="Long-tail Memory Write V1 Golden Set 路径。"),
    ] = Path("evals/memory/longtail_write_v1.jsonl"),
    output: Annotated[
        Path | None,
        typer.Option("--output", help="JSON 报告路径；默认保存到 .data/evals。"),
    ] = None,
    case: Annotated[
        str | None,
        typer.Option("--case", help="只运行指定 Case，例如 LTW-017。"),
    ] = None,
    slice_name: Annotated[
        str | None,
        typer.Option("--slice", help="只运行指定 Golden slice。"),
    ] = None,
    relation: Annotated[
        str | None,
        typer.Option("--relation", help="只运行指定 relation。"),
    ] = None,
    length_band: Annotated[
        str | None,
        typer.Option("--length-band", help="只运行 short/medium/long。"),
    ] = None,
    contract_status: Annotated[
        str | None,
        typer.Option("--contract-status", help="EXACT 或 POLICY_REVIEW。"),
    ] = None,
    live_subset: Annotated[
        bool | None,
        typer.Option("--live-subset/--no-live-subset", help="按 live_semantic_subset 过滤。"),
    ] = None,
    integration: Annotated[
        bool,
        typer.Option("--integration", help="额外运行隔离 InMemoryMemoryStore integration。"),
    ] = False,
    fail_on_error: Annotated[
        bool,
        typer.Option("--fail-on-error", help="遇到 fixture/evaluator 错误立即失败。"),
    ] = False,
) -> None:
    """评估 Long-tail Write V1；所有 Store 写入均限于隔离诊断实例。"""

    output_path = output or Path(".data/evals/memory_longtail_write_v1_baseline.json")
    try:
        report = evaluate_memory_longtail_write_v1(
            dataset,
            case_id=case,
            slice_name=slice_name,
            relation=relation,
            length_band=length_band,
            contract_status=contract_status,
            live_subset=live_subset,
            fail_on_error=fail_on_error,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        markdown = render_memory_longtail_write_v1_report(report)
        markdown_path = output_path.with_suffix(".md")
        markdown_path.write_text(markdown, encoding="utf-8")
        Path("MEMORY_LONGTAIL_WRITE_V1_BASELINE_REPORT.md").write_text(markdown, encoding="utf-8")
        Path("MEMORY_LONGTAIL_WRITE_V1_POLICY_REVIEW.md").write_text(
            render_memory_longtail_write_policy_review(report), encoding="utf-8"
        )
        if integration:
            integration_report = asyncio.run(
                evaluate_memory_longtail_write_integration(
                    dataset,
                    fail_on_error=fail_on_error,
                )
            )
            integration_path = Path(".data/evals/memory_longtail_write_v1_integration.json")
            integration_path.write_text(
                json.dumps(integration_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            Path("MEMORY_LONGTAIL_WRITE_V1_INTEGRATION_DIAGNOSTIC.md").write_text(
                render_memory_longtail_write_integration_diagnostic(integration_report),
                encoding="utf-8",
            )
    except Exception as exc:
        console.print(f"[red]Long-tail Memory Write V1 评测失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]Long-tail Memory Write V1 报告已保存：[/green]{output_path}")
    console.print(
        f"strict={report['strict_case_count']} passed={report['strict_passed_case_count']} "
        f"status={report['status']}"
    )


@eval_app.command("memory-longtail-write-v2")
def memory_longtail_write_v2_eval(
    dataset: Annotated[
        Path,
        typer.Option(
            "--dataset",
            "--case-dataset",
            help="Long-tail Write V2 case overlay dataset path.",
        ),
    ] = Path("evals/memory/longtail_write_v2_cases_draft1.jsonl"),
    shared_bank: Annotated[
        Path,
        typer.Option("--shared-bank", help="Long-tail Write V2 shared memory bank path."),
    ] = Path("evals/memory/longtail_write_v2_shared_bank_draft1.jsonl"),
    output: Annotated[
        Path | None,
        typer.Option("--output", help="JSON report path; a Markdown sidecar is also written."),
    ] = None,
    case: Annotated[
        str | None,
        typer.Option("--case", help="Run one case, for example LTW2-011."),
    ] = None,
    slice_name: Annotated[
        str | None,
        typer.Option("--slice", help="Run one V2 semantic slice."),
    ] = None,
    vector_limit: Annotated[
        int,
        typer.Option("--vector-limit", min=1, help="Vector retrieval candidate limit."),
    ] = 20,
    rank_limit: Annotated[
        int,
        typer.Option("--rank-limit", min=1, help="Cheap-ranker output limit."),
    ] = 5,
    semantic_judge_limit: Annotated[
        int | None,
        typer.Option(
            "--semantic-judge-limit",
            min=1,
            help=(
                "Semantic Judge candidate limit; defaults to --rank-limit. "
                "Use this for isolated Top-K ablations."
            ),
        ),
    ] = None,
    fail_on_error: Annotated[
        bool,
        typer.Option("--fail-on-error", help="Stop on provider or evaluator errors."),
    ] = False,
    mode: Annotated[
        Literal["fixture", "live"],
        typer.Option("--mode", help="Run deterministic fixture or real model adapters."),
    ] = "live",
    repeat: Annotated[
        int,
        typer.Option("--repeat", min=1, max=100, help="Repeat each case for judge drift analysis."),
    ] = 1,
    hard_cases: Annotated[
        bool,
        typer.Option("--hard-cases", help="Run the fixed V2 hard-case identifier subset."),
    ] = False,
    compare_fixture: Annotated[
        bool,
        typer.Option(
            "--compare-fixture/--no-compare-fixture",
            help="Attach a same-scope fixture comparison in live mode.",
        ),
    ] = True,
    final_live_validation: Annotated[
        bool,
        typer.Option(
            "--final-live-validation",
            help="Run the fixed 40x1 Live evaluation and 8x3 hard-case validation.",
        ),
    ] = False,
    semantic_remediation_validation: Annotated[
        bool,
        typer.Option(
            "--semantic-remediation-validation",
            help=(
                "Run the fixed 40x1 and hard 8x3 Semantic Judge remediation "
                "validation with frozen-baseline comparisons."
            ),
        ),
    ] = False,
    baseline_report: Annotated[
        Path,
        typer.Option(
            "--baseline-report",
            help="Frozen full Final Live JSON used for the remediation comparison.",
        ),
    ] = Path(".data/evals/memory_longtail_write_v2_final_live.json"),
    hard_baseline_report: Annotated[
        Path,
        typer.Option(
            "--hard-baseline-report",
            help="Frozen hard-case Final Live JSON used for the remediation comparison.",
        ),
    ] = Path(".data/evals/memory_longtail_write_v2_final_live_hard.json"),
) -> None:
    """Run the retrieval-aware V2 benchmark in shadow-only mode."""

    if rank_limit > vector_limit:
        raise typer.BadParameter("--rank-limit must not exceed --vector-limit")
    if semantic_judge_limit is not None and semantic_judge_limit > rank_limit:
        raise typer.BadParameter(
            "--semantic-judge-limit must not exceed --rank-limit"
        )
    if hard_cases and case is not None:
        raise typer.BadParameter("--hard-cases cannot be combined with --case")
    if hard_cases and slice_name is not None:
        raise typer.BadParameter("--hard-cases cannot be combined with --slice")
    if final_live_validation and mode != "live":
        raise typer.BadParameter("--final-live-validation requires --mode live")
    if final_live_validation and (case is not None or slice_name is not None or hard_cases):
        raise typer.BadParameter(
            "--final-live-validation cannot be combined with --case, --slice, or --hard-cases"
        )
    if final_live_validation and repeat != 1:
        raise typer.BadParameter("--final-live-validation manages its own 1x and 3x repeats")
    if semantic_remediation_validation and final_live_validation:
        raise typer.BadParameter(
            "--semantic-remediation-validation cannot be combined with --final-live-validation"
        )
    if semantic_remediation_validation and mode != "live":
        raise typer.BadParameter("--semantic-remediation-validation requires --mode live")
    if semantic_remediation_validation and (
        case is not None or slice_name is not None or hard_cases
    ):
        raise typer.BadParameter(
            "--semantic-remediation-validation cannot be combined with "
            "--case, --slice, or --hard-cases"
        )
    if semantic_remediation_validation and repeat != 1:
        raise typer.BadParameter(
            "--semantic-remediation-validation manages its own 1x and 3x repeats"
        )
    if semantic_remediation_validation and output is not None:
        raise typer.BadParameter(
            "--semantic-remediation-validation writes fixed artifact names; "
            "--output is not supported"
        )
    effective_repeat = 3 if hard_cases and repeat == 1 else repeat
    output_path = output or (
        Path(".data/evals/memory_longtail_write_v2_semantic_remediation_live.json")
        if semantic_remediation_validation
        else Path(".data/evals/memory_longtail_write_v2_final_live.json")
        if final_live_validation
        else _default_memory_longtail_write_v2_output_path()
    )
    if output_path.suffix.casefold() == ".md":
        raise typer.BadParameter("--output must be a JSON path; Markdown is written beside it")

    try:
        if semantic_remediation_validation:
            full_report, hard_report = asyncio.run(
                _run_semantic_remediation_memory_longtail_write_v2_eval(
                    dataset,
                    shared_bank,
                    settings=get_settings(),
                    vector_limit=vector_limit,
                    rank_limit=rank_limit,
                    semantic_judge_limit=semantic_judge_limit,
                    fail_on_error=fail_on_error,
                    compare_fixture=compare_fixture,
                    baseline_report=baseline_report,
                    hard_baseline_report=hard_baseline_report,
                )
            )
            hard_output_path = Path(
                ".data/evals/memory_longtail_write_v2_semantic_remediation_hard.json"
            )
            _write_memory_longtail_write_v2_artifact(full_report, output_path)
            _write_memory_longtail_write_v2_artifact(hard_report, hard_output_path)
            console.print(
                f"[green]Semantic remediation Live JSON saved:[/green] {output_path}\n"
                f"[green]Semantic remediation hard-case JSON saved:[/green] "
                f"{hard_output_path}\n"
                f"status={full_report['status']}"
            )
            return
        if final_live_validation:
            full_report, hard_report = asyncio.run(
                _run_final_live_memory_longtail_write_v2_eval(
                    dataset,
                    shared_bank,
                    settings=get_settings(),
                    vector_limit=vector_limit,
                    rank_limit=rank_limit,
                    semantic_judge_limit=semantic_judge_limit,
                    fail_on_error=fail_on_error,
                    compare_fixture=compare_fixture,
                )
            )
            hard_output_path = output_path.with_name(f"{output_path.stem}_hard.json")
            _write_memory_longtail_write_v2_artifact(full_report, output_path)
            _write_memory_longtail_write_v2_artifact(hard_report, hard_output_path)
            console.print(
                f"[green]Final Live JSON saved:[/green] {output_path}\n"
                f"[green]Hard-case JSON saved:[/green] {hard_output_path}\n"
                f"status={full_report['status']}"
            )
            return
        if mode == "live":
            report = asyncio.run(
                _run_live_memory_longtail_write_v2_eval(
                    dataset,
                    shared_bank,
                    settings=get_settings(),
                    case_id=case,
                    slice_name=slice_name,
                    vector_limit=vector_limit,
                    rank_limit=rank_limit,
                    semantic_judge_limit=semantic_judge_limit,
                    fail_on_error=fail_on_error,
                    repeat=effective_repeat,
                    hard_cases=hard_cases,
                )
            )
        else:
            report = asyncio.run(
                evaluate_memory_longtail_write_v2_fixture(
                    dataset,
                    shared_bank,
                    case_id=case,
                    slice_name=slice_name,
                    vector_limit=vector_limit,
                    rank_limit=rank_limit,
                    semantic_judge_limit=semantic_judge_limit,
                    fail_on_error=fail_on_error,
                    repeat=effective_repeat,
                    hard_cases=hard_cases,
                )
            )
        if mode == "live" and compare_fixture:
            if dataset.exists() and shared_bank.exists():
                fixture_report = asyncio.run(
                    evaluate_memory_longtail_write_v2_fixture(
                        dataset,
                        shared_bank,
                        case_id=case,
                        slice_name=slice_name,
                        vector_limit=vector_limit,
                        rank_limit=rank_limit,
                        semantic_judge_limit=semantic_judge_limit,
                        fail_on_error=fail_on_error,
                        repeat=effective_repeat,
                        hard_cases=hard_cases,
                    )
                )
                report["fixture_comparison"] = compare_memory_longtail_write_v2_reports(
                    fixture_report,
                    report,
                )
                report["fixture_baseline"] = {
                    "evaluation_mode": fixture_report.get("evaluation_mode"),
                    "case_count": fixture_report.get("case_count"),
                    "passed_case_count": fixture_report.get("passed_case_count"),
                    "repeat": fixture_report.get("repeat"),
                }
            else:
                report["fixture_comparison"] = {
                    "status": "UNAVAILABLE",
                    "methodology": "Fixture dataset paths were not available.",
                    "metrics": {},
                }
        report["evaluation_mode_requested"] = mode
        report["store_mutation_permitted"] = False
        markdown_path = _write_memory_longtail_write_v2_artifact(report, output_path)
        markdown = markdown_path.read_text(encoding="utf-8")
        # Keep the root convenience reports mode-specific.  A fixture run
        # must not overwrite the Live report (and vice versa); timestamped
        # artifacts remain the source of truth for filtered/repeated runs.
        if case is None and slice_name is None and not hard_cases:
            root_report = (
                "MEMORY_LONGTAIL_WRITE_V2_FIXTURE_REPORT.md"
                if mode == "fixture"
                else "MEMORY_LONGTAIL_WRITE_V2_RETRIEVAL_AWARE_REPORT.md"
            )
            Path(root_report).write_text(markdown, encoding="utf-8")
    except Exception as exc:
        console.print(f"[red]Long-tail Memory Write V2 evaluation failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]Long-tail Memory Write V2 JSON saved:[/green] {output_path}")
    console.print(f"[green]Markdown report saved:[/green] {markdown_path}")
    console.print(
        f"cases={report['case_count']} passed={report['passed_case_count']} "
        f"status={report['status']}"
    )


def _default_memory_longtail_write_v2_output_path(*, now: datetime | None = None) -> Path:
    timestamp = (now or datetime.now().astimezone()).strftime("%Y%m%d_%H%M%S_%f")
    return Path(".data/evals") / f"memory_longtail_write_v2_{timestamp}.json"


def _write_memory_longtail_write_v2_artifact(
    report: dict[str, Any],
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_path.with_suffix(".md")
    markdown_path.write_text(
        render_memory_longtail_write_v2_report(report),
        encoding="utf-8",
    )
    return markdown_path


@eval_app.command("memory-longtail-realistic")
def memory_longtail_realistic_eval(
    dataset: Annotated[
        Path,
        typer.Option("--dataset", help="Long-tail realistic Memory 评测集路径。"),
    ] = Path("evals/memory/longtail_realistic_v1.jsonl"),
    output: Annotated[
        Path | None,
        typer.Option("--output", help="JSON/Markdown 报告路径；默认保存到 .data/evals。"),
    ] = None,
    case: Annotated[
        str | None,
        typer.Option("--case", help="只运行指定 scenario id。"),
    ] = None,
    category: Annotated[
        str | None,
        typer.Option("--category", help="只运行指定 category。"),
    ] = None,
    repeat: Annotated[
        int,
        typer.Option("--repeat", min=1, max=100, help="重复运行次数，用于观察 judge 波动。"),
    ] = 1,
    mode: Annotated[
        Literal["fixture", "live"],
        typer.Option("--mode", help="fixture 或真实模型 live（始终 shadow-only）"),
    ] = "fixture",
    hard_cases: Annotated[
        bool,
        typer.Option("--hard-cases", help="Run the fixed live hard-case subset."),
    ] = False,
    compare_fixture: Annotated[
        bool,
        typer.Option(
            "--compare-fixture/--no-compare-fixture",
            help="Attach a fixture baseline for the same live-evaluation scope.",
        ),
    ] = True,
    candidate_limit: Annotated[
        int,
        typer.Option("--candidate-limit", min=1, max=10, help="候选 Memory 上限。"),
    ] = 5,
) -> None:
    """运行只读 Long-tail realistic Memory 评估，不提交 Store mutation。"""

    if hard_cases and mode != "live":
        raise typer.BadParameter("--hard-cases only supports --mode live")
    if hard_cases and (case is not None or category is not None):
        raise typer.BadParameter("--hard-cases cannot be combined with --case or --category")
    if mode == "fixture" and not compare_fixture:
        raise typer.BadParameter("--no-compare-fixture only applies to --mode live")
    if mode == "live" and candidate_limit > 5:
        raise typer.BadParameter("live mode requires --candidate-limit between 1 and 5")
    effective_repeat = 3 if hard_cases and repeat == 1 else repeat
    output_label = f"{mode}_hard_cases" if hard_cases else mode
    output_path = output or Path(
        ".data/evals/memory_longtail_realistic_"
        + output_label
        + "_"
        + datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
        + ".json"
    )
    try:
        report = asyncio.run(
            _run_live_memory_longtail_realistic_eval(
                dataset,
                settings=get_settings(),
                case_id=case,
                category=category,
                hard_cases=hard_cases,
                repeat=effective_repeat,
                candidate_limit=candidate_limit,
            )
            if mode == "live"
            else evaluate_memory_longtail_realistic(
                dataset,
                case_id=case,
                category=category,
                hard_cases=hard_cases,
                repeat=effective_repeat,
                candidate_limit=candidate_limit,
                mode="fixture",
            )
        )
        if mode == "live" and compare_fixture:
            fixture = asyncio.run(
                evaluate_memory_longtail_realistic(
                    dataset,
                    case_id=case,
                    category=category,
                    hard_cases=hard_cases,
                    repeat=1,
                    candidate_limit=candidate_limit,
                    mode="fixture",
                )
            )
            report["fixture_comparison"] = _longtail_fixture_comparison(fixture, report)
        if hard_cases:
            report["hard_case_ids"] = list(HARD_CASE_IDS)
            report["source_dataset"] = str(dataset)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.suffix.casefold() == ".md":
            output_path.write_text(render_longtail_realistic_report(report), encoding="utf-8")
        else:
            output_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if case is None and category is None and mode == "fixture":
                Path("MEMORY_LONGTAIL_REALISTIC_EVAL_REPORT.md").write_text(
                    render_longtail_realistic_report(report),
                    encoding="utf-8",
                )
            if case is None and category is None and mode == "live" and not hard_cases:
                Path("MEMORY_LONGTAIL_REALISTIC_LIVE_EVAL_REPORT_V3.md").write_text(
                    render_longtail_realistic_report(report),
                    encoding="utf-8",
                )
            if case is None and category is None and mode == "live" and hard_cases:
                Path("MEMORY_LONGTAIL_REALISTIC_LIVE_HARD_CASE_REPORT.md").write_text(
                    render_longtail_realistic_report(report),
                    encoding="utf-8",
                )
    except Exception as exc:
        console.print(f"[red]Long-tail realistic Memory 评估失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Long-tail realistic Memory 评估已保存：[/green]{output_path}")
    table = Table(title="Memory Long-tail Realistic")
    table.add_column("指标")
    table.add_column("值", justify="right")
    for key in (
        "scenario_count",
        "turn_count",
        "gate_recall",
        "retrieval_recall_at_5",
        "relation_accuracy",
        "target_memory_precision",
        "false_destructive_update_count",
        "confirmed_overwrite_violation_count",
    ):
        table.add_row(key, str(report["metrics"].get(key, 0)))
    console.print(table)


def _longtail_fixture_comparison(
    fixture: dict[str, Any],
    live: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    metric_names = (
        "scenario_pass_rate",
        "gate_recall",
        "extraction_semantic_success_rate",
        "overall_semantic_identity_match_rate",
        "canonical_semantic_identity_match_rate",
        "custom_semantic_identity_match_rate",
        "semantic_identity_match_rate",
        "retrieval_hit_at_3",
        "retrieval_hit_at_5",
        "retrieval_recall_at_5",
        "relation_accuracy",
        "judge_relation_accuracy",
        "judge_first_attempt_parse_failure_count",
        "judge_final_parse_failure_count",
        "update_precision",
        "target_memory_accuracy",
        "target_memory_precision",
        "false_destructive_update_count",
        "extractor_latency_p50",
        "extractor_latency_p95",
        "strong_upgrade_count",
        "strong_latency_p95",
        "strong_no_value_added_count",
    )
    fixture_metrics = fixture.get("metrics") or {}
    live_metrics = live.get("metrics") or {}
    return {
        name: {
            "fixture": fixture_metrics.get(name),
            "live_before": _LONGTAIL_LIVE_V1_BASELINE.get(name),
            "live_after": live_metrics.get(name),
            # Retain the V1 public key for callers that render old artifacts.
            "live": live_metrics.get(name),
        }
        for name in metric_names
    }


_LONGTAIL_LIVE_V1_BASELINE: dict[str, int | float | None] = {
    "scenario_pass_rate": 6 / 26,
    "gate_recall": 0.7872,
    "extraction_semantic_success_rate": 30 / 38,
    "overall_semantic_identity_match_rate": None,
    "canonical_semantic_identity_match_rate": None,
    "custom_semantic_identity_match_rate": None,
    "semantic_identity_match_rate": None,
    "retrieval_hit_at_3": 1.0,
    "retrieval_hit_at_5": 1.0,
    "retrieval_recall_at_5": 1.0,
    "relation_accuracy": 0.6667,
    "judge_relation_accuracy": 1.0,
    "judge_first_attempt_parse_failure_count": 2,
    "judge_final_parse_failure_count": 2,
    "update_precision": 1.0,
    "target_memory_accuracy": 1.0,
    "target_memory_precision": 1.0,
    "false_destructive_update_count": 0,
    "extractor_latency_p50": 2522.61,
    "extractor_latency_p95": 71633.999,
    "strong_upgrade_count": 6,
    "strong_latency_p95": 69492.171,
    "strong_no_value_added_count": 2,
}


def _build_live_memory_relation_judge(
    settings: Any,
    *,
    max_target_count: int = 1,
) -> OpenAICompatibleSemanticRelationJudge:
    if settings.memory_semantic_relation_provider != "llm":
        raise ValueError("--live 需要 LOVEAPP_MEMORY_SEMANTIC_RELATION_PROVIDER=llm。")
    if not settings.llm_api_key:
        raise ValueError("LOVEAPP_LLM_API_KEY 未配置。")
    if not settings.llm_base_url:
        raise ValueError("LOVEAPP_LLM_BASE_URL 未配置。")
    model = (
        settings.memory_semantic_relation_model
        or settings.memory_extraction_strong_model
        or settings.memory_extraction_model
        or settings.llm_model
    )
    if not model:
        raise ValueError("LOVEAPP_MEMORY_SEMANTIC_RELATION_MODEL 或 LOVEAPP_LLM_MODEL 未配置。")
    return OpenAICompatibleSemanticRelationJudge(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=model,
        timeout_seconds=settings.memory_semantic_relation_timeout_seconds,
        max_retries=settings.memory_semantic_relation_max_retries,
        max_tokens=settings.memory_semantic_relation_max_tokens,
        thinking=settings.memory_semantic_relation_thinking,
        max_target_count=max_target_count,
    )


async def _run_live_memory_longtail_write_v2_eval(
    dataset: Path,
    shared_bank: Path,
    *,
    settings: Any,
    case_id: str | None,
    slice_name: str | None,
    vector_limit: int,
    rank_limit: int,
    semantic_judge_limit: int | None = None,
    fail_on_error: bool,
    repeat: int = 1,
    hard_cases: bool = False,
) -> dict[str, Any]:
    """Own live adapters while the evaluator remains isolated from production stores."""

    if not settings.llm_api_key:
        raise ValueError("live V2 evaluation requires LOVEAPP_LLM_API_KEY")
    if not settings.llm_base_url:
        raise ValueError("live V2 evaluation requires LOVEAPP_LLM_BASE_URL")
    provider_overridden = settings.memory_semantic_relation_provider != "llm"
    live_settings = (
        settings.model_copy(update={"memory_semantic_relation_provider": "llm"})
        if provider_overridden
        else settings
    )
    judge_model = _configured_semantic_relation_model(live_settings)
    if not judge_model:
        raise ValueError(
            "live V2 evaluation requires LOVEAPP_MEMORY_SEMANTIC_RELATION_MODEL or an LLM model"
        )

    embedding_provider = build_embedding_provider(live_settings)
    judge: OpenAICompatibleSemanticRelationJudge | None = None
    try:
        judge = _build_live_memory_relation_judge(
            live_settings,
            # The Live evaluator measures semantic target selection separately
            # from write authority. Explicit multi-claim cases may propose up to
            # the adapter's bounded maximum; the unchanged production validator
            # still denies destructive multi-target writes.
            max_target_count=5,
        )
        report = await evaluate_memory_longtail_write_v2(
            dataset,
            shared_bank,
            embedding_provider=embedding_provider,
            judge=judge,
            case_id=case_id,
            slice_name=slice_name,
            vector_limit=vector_limit,
            rank_limit=rank_limit,
            semantic_judge_limit=semantic_judge_limit,
            fail_on_error=fail_on_error,
            repeat=repeat,
            hard_cases=hard_cases,
            use_production_retriever=True,
        )
        report["live_configuration"] = {
            "embedding_provider": live_settings.embedding_provider,
            "embedding_model": live_settings.embedding_model,
            "semantic_relation_judge_model": judge_model,
            "semantic_relation_provider_overridden_in_process": provider_overridden,
            "judge_max_target_count": 5,
        }
        report["evaluation_mode"] = "shadow_live"
        report["methodology"] = (
            "production_hybrid_retriever_with_production_embedding_and_semantic_judge_"
            "plus_deterministic_validator_and_isolated_store_shadow"
        )
        report["store_mutation_permitted"] = False
        return report
    finally:
        if judge is not None:
            await judge.aclose()
        await embedding_provider.aclose()


async def _run_final_live_memory_longtail_write_v2_eval(
    dataset: Path,
    shared_bank: Path,
    *,
    settings: Any,
    vector_limit: int,
    rank_limit: int,
    semantic_judge_limit: int | None = None,
    fail_on_error: bool,
    compare_fixture: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the complete full + hard Live validation with isolated adapters."""

    full_report = await _run_live_memory_longtail_write_v2_eval(
        dataset,
        shared_bank,
        settings=settings,
        case_id=None,
        slice_name=None,
        vector_limit=vector_limit,
        rank_limit=rank_limit,
        semantic_judge_limit=semantic_judge_limit,
        fail_on_error=fail_on_error,
        repeat=1,
        hard_cases=False,
    )
    hard_report = await _run_live_memory_longtail_write_v2_eval(
        dataset,
        shared_bank,
        settings=settings,
        case_id=None,
        slice_name=None,
        vector_limit=vector_limit,
        rank_limit=rank_limit,
        semantic_judge_limit=semantic_judge_limit,
        fail_on_error=fail_on_error,
        repeat=3,
        hard_cases=True,
    )
    if compare_fixture:
        fixture_report = await evaluate_memory_longtail_write_v2_fixture(
            dataset,
            shared_bank,
            vector_limit=vector_limit,
            rank_limit=rank_limit,
            semantic_judge_limit=semantic_judge_limit,
            fail_on_error=fail_on_error,
        )
        full_report["fixture_comparison"] = compare_memory_longtail_write_v2_reports(
            fixture_report,
            full_report,
        )
        full_report["fixture_baseline"] = {
            "evaluation_mode": fixture_report.get("evaluation_mode"),
            "case_count": fixture_report.get("case_count"),
            "passed_case_count": fixture_report.get("passed_case_count"),
            "repeat": fixture_report.get("repeat"),
        }
    repository = collect_memory_longtail_write_v2_repository_metadata()
    return finalize_memory_longtail_write_v2_live_validation(
        full_report,
        hard_report,
        repository=repository,
    )


def _load_memory_longtail_write_v2_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Memory Long-tail Write V2 baseline not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Memory Long-tail Write V2 baseline is invalid JSON: {path}") from exc
    if not isinstance(report, dict):
        raise ValueError(f"Memory Long-tail Write V2 baseline must be a JSON object: {path}")
    return report


async def _run_semantic_remediation_memory_longtail_write_v2_eval(
    dataset: Path,
    shared_bank: Path,
    *,
    settings: Any,
    vector_limit: int,
    rank_limit: int,
    semantic_judge_limit: int | None = None,
    fail_on_error: bool,
    compare_fixture: bool,
    baseline_report: Path,
    hard_baseline_report: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the fixed remediation validation and compare it to frozen Live artifacts."""

    baseline = _load_memory_longtail_write_v2_report(baseline_report)
    hard_baseline = _load_memory_longtail_write_v2_report(hard_baseline_report)
    full_report, hard_report = await _run_final_live_memory_longtail_write_v2_eval(
        dataset,
        shared_bank,
        settings=settings,
        vector_limit=vector_limit,
        rank_limit=rank_limit,
        semantic_judge_limit=semantic_judge_limit,
        fail_on_error=fail_on_error,
        compare_fixture=compare_fixture,
    )
    full_report["semantic_remediation_comparison"] = (
        compare_memory_longtail_write_v2_semantic_remediation(baseline, full_report)
    )
    hard_report["semantic_remediation_comparison"] = (
        compare_memory_longtail_write_v2_semantic_remediation(hard_baseline, hard_report)
    )
    full_report["semantic_remediation_baseline"] = {
        "artifact": str(baseline_report),
        "status": baseline.get("status"),
    }
    hard_report["semantic_remediation_baseline"] = {
        "artifact": str(hard_baseline_report),
        "status": hard_baseline.get("status"),
    }
    return full_report, hard_report


async def _run_memory_longtail_relation_eval(
    dataset: Path,
    *,
    judge: Any,
    retriever: HybridMemoryRetriever | None = None,
    case_id: str | None,
    candidate_limit: int,
) -> dict[str, Any]:
    try:
        return await evaluate_memory_longtail_relations(
            dataset,
            judge=judge,
            retriever=retriever,
            case_id=case_id,
            candidate_limit=candidate_limit,
        )
    finally:
        close = getattr(judge, "aclose", None)
        if close is not None:
            await close()


async def _run_live_memory_longtail_relation_eval(
    dataset: Path,
    *,
    settings: Any,
    case_id: str | None,
    candidate_limit: int,
) -> dict[str, Any]:
    embedding_provider = build_embedding_provider(settings)
    try:
        judge = _build_live_memory_relation_judge(settings)
        retriever = HybridMemoryRetriever(embedding_provider=embedding_provider)
        return await _run_memory_longtail_relation_eval(
            dataset,
            judge=judge,
            retriever=retriever,
            case_id=case_id,
            candidate_limit=candidate_limit,
        )
    finally:
        await embedding_provider.aclose()


async def _run_live_memory_longtail_realistic_eval(
    dataset: Path,
    *,
    settings: Any,
    case_id: str | None,
    category: str | None,
    hard_cases: bool,
    repeat: int,
    candidate_limit: int,
) -> dict[str, Any]:
    """Run realistic scenarios with production extractor and judge in shadow mode."""
    live_settings, semantic_relation_override = _live_longtail_realistic_settings(settings)
    embedding_provider = build_embedding_provider(live_settings)
    observed_embedding_provider = _ObservedEmbeddingProvider(embedding_provider)
    extractor = _build_memory_extractor(live_settings)
    judge: OpenAICompatibleSemanticRelationJudge | None = None
    try:
        if isinstance(extractor, NoOpMemoryExtractor):
            raise ValueError("live mode requires an enabled OpenAI-compatible Memory extractor")
        embedding_dimension = await embedding_provider.dimension()
        judge = _build_live_memory_relation_judge(live_settings)
        retriever = HybridMemoryRetriever(embedding_provider=observed_embedding_provider)
        report = await evaluate_memory_longtail_realistic(
            dataset,
            case_id=case_id,
            category=category,
            hard_cases=hard_cases,
            repeat=repeat,
            candidate_limit=candidate_limit,
            retriever=retriever,
            judge=judge,
            extractor=extractor,
            mode="live",
        )
        report["live_models"] = {
            "extractor": _configured_memory_extraction_model(live_settings),
            "semantic_relation_judge": _configured_semantic_relation_model(live_settings),
            "embedding": live_settings.embedding_model,
        }
        report["embedding_telemetry"] = observed_embedding_provider.summary(
            model=live_settings.embedding_model,
            dimension=embedding_dimension,
        )
        report["semantic_relation_provider_overridden_in_process"] = semantic_relation_override
        return report
    finally:
        close_extractor = getattr(extractor, "aclose", None)
        if close_extractor is not None:
            await close_extractor()
        if judge is not None:
            close_judge = getattr(judge, "aclose", None)
            if close_judge is not None:
                await close_judge()
        await embedding_provider.aclose()


def _live_longtail_realistic_settings(settings: Any) -> tuple[Any, bool]:
    if not settings.llm_api_key:
        raise ValueError("live mode requires LOVEAPP_LLM_API_KEY")
    if not settings.llm_base_url:
        raise ValueError("live mode requires LOVEAPP_LLM_BASE_URL")
    use_extraction_llm = settings.memory_extraction_provider == "llm" or (
        settings.memory_extraction_provider == "auto" and settings.llm_provider != "demo"
    )
    if not use_extraction_llm:
        raise ValueError(
            "live mode requires LOVEAPP_MEMORY_EXTRACTION_PROVIDER=llm "
            "or an auto provider backed by a non-demo LLM"
        )
    if not _configured_memory_extraction_model(settings):
        raise ValueError("live mode requires LOVEAPP_MEMORY_EXTRACTION_MODEL or LOVEAPP_LLM_MODEL")

    semantic_relation_override = settings.memory_semantic_relation_provider != "llm"
    live_settings = (
        settings.model_copy(update={"memory_semantic_relation_provider": "llm"})
        if semantic_relation_override
        else settings
    )
    if not _configured_semantic_relation_model(live_settings):
        raise ValueError(
            "live mode requires LOVEAPP_MEMORY_SEMANTIC_RELATION_MODEL or an LLM model"
        )
    return live_settings, semantic_relation_override


def _configured_memory_extraction_model(settings: Any) -> str:
    return str(settings.memory_extraction_model or settings.llm_model or "")


def _configured_semantic_relation_model(settings: Any) -> str:
    return str(
        settings.memory_semantic_relation_model
        or settings.memory_extraction_strong_model
        or settings.memory_extraction_model
        or settings.llm_model
        or ""
    )


@knowledge_app.command("validate")
def validate_knowledge(
    path: Annotated[Path, typer.Argument(help="Markdown、JSON、JSONL 文件或目录。")] = Path(
        "knowledge"
    ),
) -> None:
    """按照 LoveApp 文档模型验证知识文件。"""
    try:
        documents = load_knowledge_path(path)
    except (OSError, ValueError) as exc:
        console.print(f"[red]知识文档校验失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]校验通过：[/green]{len(documents)} 条知识文档。")


@knowledge_app.command("ingest")
def ingest_knowledge(
    path: Annotated[
        Path,
        typer.Argument(help="需要写入 Qdrant 的知识源文件或目录。"),
    ] = Path("loveapp_rag_knowledge_base_formal_v1.md"),
    recreate: Annotated[
        bool,
        typer.Option("--recreate/--no-recreate", help="是否重建 collection。"),
    ] = True,
    include_seed: Annotated[
        bool,
        typer.Option(
            "--include-seed/--no-seed",
            help="是否合并内置 seed 文档；V2 正式知识库通常使用 --no-seed。",
        ),
    ] = True,
) -> None:
    """按问答块生成向量并写入本地 Qdrant。"""
    try:
        external_documents = load_knowledge_path(path)
        documents = (
            merge_knowledge_documents(load_seed_documents(), external_documents)
            if include_seed
            else external_documents
        )
        if not documents:
            raise ValueError("没有找到可入库的知识文档。")
        with console.status("正在生成本地向量并写入 Qdrant..."):
            indexed, total = asyncio.run(_ingest_documents(documents, recreate))
    except Exception as exc:
        console.print(f"[red]知识入库失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc
    source_description = "Seed 与正式文档统一去重后" if include_seed else "正式文档"
    console.print(
        f"[green]入库完成：[/green]{source_description}写入 {indexed} 个问答 chunk，"
        f"collection 共 {total} 条。"
    )


@knowledge_app.command("search")
def search_knowledge(
    query: Annotated[str, typer.Argument(help="用于验证召回效果的问题。")],
    limit: Annotated[int, typer.Option("--limit", min=1, max=20)] = 5,
) -> None:
    """直接查询 Qdrant，检查 RAG 召回结果。"""
    try:
        matches = asyncio.run(_search_documents(query, limit))
    except Exception as exc:
        console.print(f"[red]知识检索失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc

    table = Table(title="RAG 召回结果")
    table.add_column("ID")
    table.add_column("标题")
    table.add_column("章节")
    table.add_column("基础分", justify="right")
    table.add_column("软加权", justify="right")
    table.add_column("总分", justify="right")
    for match in matches:
        table.add_row(
            match.document.id,
            match.document.title,
            match.document.section or "-",
            f"{match.base_score:.4f}" if match.base_score is not None else "-",
            f"{sum(match.score_components.values()):.4f}",
            f"{match.score:.4f}",
        )
    console.print(table)


@memory_app.command("remember")
def remember_memory(
    text: Annotated[str, typer.Argument(help="需要抽取记忆的一段用户陈述。")],
    user_id: Annotated[str, typer.Option("--user-id")] = "local-user",
    relationship_id: Annotated[str, typer.Option("--relationship-id")] = "primary",
    conversation_id: Annotated[str | None, typer.Option("--conversation-id")] = None,
    confirmed: Annotated[
        bool,
        typer.Option("--confirmed", help="将本次抽取结果直接标记为已确认。"),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="输出 JSON。")] = False,
) -> None:
    """使用配置的记忆抽取模型分析文本并持久化。"""
    try:
        result = asyncio.run(
            _remember_text(
                text=text,
                user_id=user_id,
                relationship_id=relationship_id,
                conversation_id=conversation_id,
                status=MemoryStatus.CONFIRMED if confirmed else MemoryStatus.PROPOSED,
            )
        )
    except Exception as exc:
        console.print(f"[red]记忆抽取失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc
    if json_output:
        console.print_json(result.model_dump_json())
        return
    _render_remember_result(result)


@app.command("memory-test")
def memory_test(
    user_id: Annotated[str, typer.Option("--user-id")] = DEFAULT_MEMORY_TEST_USER_ID,
    relationship_id: Annotated[
        str,
        typer.Option("--relationship-id"),
    ] = DEFAULT_MEMORY_TEST_RELATIONSHIP_ID,
    conversation_id: Annotated[
        str,
        typer.Option("--conversation-id"),
    ] = DEFAULT_MEMORY_TEST_CONVERSATION_ID,
    text: Annotated[
        list[str] | None,
        typer.Option("--text", help="Run a turn non-interactively; repeat for multiple turns."),
    ] = None,
    status: Annotated[
        Literal["proposed", "confirmed"],
        typer.Option("--status", case_sensitive=False, help="Requested input status."),
    ] = "confirmed",
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit stable structured JSON."),
    ] = False,
    isolated: Annotated[
        bool,
        typer.Option(
            "--isolated",
            help="Use a process-local store while retaining the configured extractor.",
        ),
    ] = False,
    route: Annotated[
        bool,
        typer.Option(
            "--route/--no-route",
            help="Run the configured application Router before Memory inspection.",
        ),
    ] = True,
    memory_version: Annotated[
        Literal["v1", "v2"],
        typer.Option(
            "--memory-version",
            case_sensitive=False,
            help=(
                "Memory relation mode: v1 deterministic resolver, or v2 Semantic "
                "Judge shadow evaluation with deterministic fallback."
            ),
        ),
    ] = "v1",
    limit: Annotated[int, typer.Option("--limit", min=1, max=1000)] = 200,
) -> None:
    """Inspect the real Memory pipeline in an isolated test identity."""

    try:
        asyncio.run(
            run_memory_inspector_cli(
                user_id=user_id,
                relationship_id=relationship_id,
                conversation_id=conversation_id,
                requested_status=MemoryStatus(status),
                texts=text or (),
                json_output=json_output,
                isolated=isolated,
                include_routing=route,
                memory_version=memory_version,
                limit=limit,
            )
        )
    except Exception as exc:
        console.print(f"[red]Memory Inspector failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@memory_app.command("list")
def list_memory(
    user_id: Annotated[str, typer.Option("--user-id")] = "local-user",
    relationship_id: Annotated[str | None, typer.Option("--relationship-id")] = None,
    kind: Annotated[MemoryKind | None, typer.Option("--kind")] = None,
    status: Annotated[MemoryStatus | None, typer.Option("--status")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 100,
    json_output: Annotated[bool, typer.Option("--json", help="输出 JSON。")] = False,
) -> None:
    """列出已存储的结构化记忆。"""
    try:
        items = asyncio.run(_list_memory(user_id, relationship_id, kind, status, limit))
    except Exception as exc:
        console.print(f"[red]读取记忆失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc
    if json_output:
        console.print_json(data=[item.model_dump(mode="json") for item in items])
        return
    _render_memory_table(items)


@memory_app.command("show")
def show_memory(
    memory_id: Annotated[str, typer.Argument(help="记忆 ID。")],
    user_id: Annotated[str, typer.Option("--user-id")] = "local-user",
) -> None:
    """查看一条记忆的完整字段。"""
    item = asyncio.run(_get_memory(memory_id, user_id))
    if item is None:
        console.print("[red]没有找到该记忆。[/red]")
        raise typer.Exit(code=1)
    console.print_json(item.model_dump_json())


@memory_app.command("plans")
def list_relationship_plans(
    user_id: Annotated[str, typer.Option("--user-id")] = "local-user",
    relationship_id: Annotated[str, typer.Option("--relationship-id")] = "primary",
    status: Annotated[PlanStatus | None, typer.Option("--status")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 100,
    json_output: Annotated[bool, typer.Option("--json", help="输出 JSON。")] = False,
) -> None:
    """查看关系活动计划及其生命周期状态。"""
    plans = asyncio.run(
        _list_relationship_plans(
            user_id=user_id,
            relationship_id=relationship_id,
            status=status,
            limit=limit,
        )
    )
    if json_output:
        console.print_json(data=[plan.model_dump(mode="json") for plan in plans])
        return
    console.print(_build_relationship_plan_table(plans))


@memory_app.command("confirm")
def confirm_memory(
    memory_id: Annotated[str, typer.Argument(help="记忆 ID。")],
    user_id: Annotated[str, typer.Option("--user-id")] = "local-user",
) -> None:
    """确认一条模型抽取的候选记忆。"""
    item = asyncio.run(_set_memory_status(memory_id, user_id, MemoryStatus.CONFIRMED))
    if item is None:
        console.print("[red]没有找到该记忆。[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]已确认记忆：[/green]{item.id}")


@memory_app.command("reject")
def reject_memory(
    memory_id: Annotated[str, typer.Argument(help="记忆 ID。")],
    user_id: Annotated[str, typer.Option("--user-id")] = "local-user",
) -> None:
    """拒绝一条候选记忆，使其不再进入 Agent 上下文。"""
    item = asyncio.run(_set_memory_status(memory_id, user_id, MemoryStatus.REJECTED))
    if item is None:
        console.print("[red]没有找到该记忆。[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]已拒绝记忆：[/green]{item.id}")


@memory_app.command("delete")
def delete_memory(
    memory_id: Annotated[str, typer.Argument(help="记忆 ID。")],
    user_id: Annotated[str, typer.Option("--user-id")] = "local-user",
    yes: Annotated[bool, typer.Option("--yes", "-y", help="跳过确认。")] = False,
) -> None:
    """硬删除一条记忆。"""
    if not yes and not typer.confirm("确定要永久删除这条记忆吗？"):
        raise typer.Abort()
    if not asyncio.run(_delete_memory(memory_id, user_id)):
        console.print("[red]没有找到该记忆。[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]已删除记忆：[/green]{memory_id}")


@memory_app.command("clear")
def clear_memory(
    user_id: Annotated[str, typer.Option("--user-id")] = "local-user",
    relationship_id: Annotated[str | None, typer.Option("--relationship-id")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="跳过确认。")] = False,
) -> None:
    """清除指定用户或关系下的记忆及其源消息。"""
    scope = f"关系 {relationship_id}" if relationship_id else f"用户 {user_id}"
    if not yes and not typer.confirm(f"确定要清除{scope}的全部记忆吗？"):
        raise typer.Abort()
    count = asyncio.run(_clear_memory(user_id, relationship_id))
    console.print(f"[green]已清除：[/green]{count} 条记忆。")


@memory_app.command("context")
def show_memory_context(
    user_id: Annotated[str, typer.Option("--user-id")] = "local-user",
    relationship_id: Annotated[str, typer.Option("--relationship-id")] = "primary",
) -> None:
    """查看下一次 Agent 调用实际会使用的关系上下文。"""
    context = asyncio.run(_get_memory_context(user_id, relationship_id))
    console.print_json(context.model_dump_json())


@memory_app.command("compact")
def compact_memory(
    user_id: Annotated[str, typer.Option("--user-id")] = "local-user",
    relationship_id: Annotated[str, typer.Option("--relationship-id")] = "primary",
    apply_changes: Annotated[
        bool,
        typer.Option("--apply", help="将预览到的重复项标记为 superseded。"),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="输出 JSON。")] = False,
) -> None:
    """预览或压缩同一关系中的语义重复记忆。"""
    result = asyncio.run(
        _compact_memory(
            user_id=user_id,
            relationship_id=relationship_id,
            apply_changes=apply_changes,
        )
    )
    if json_output:
        console.print_json(result.model_dump_json())
        return
    table = Table(title="记忆语义去重")
    table.add_column("保留 ID")
    table.add_column("标记 superseded")
    table.add_column("摘要")
    for group in result.groups:
        table.add_row(
            group.keeper_id,
            "\n".join(group.duplicate_ids),
            "\n".join(group.summaries),
        )
    console.print(table)
    if not result.groups:
        console.print("[green]没有发现活动状态的语义重复记忆。[/green]")
    elif apply_changes:
        console.print(f"[green]已标记 {result.applied_count} 条重复记忆。[/green]")
    else:
        console.print("[yellow]当前仅预览；确认后增加 --apply。[/yellow]")


@memory_app.command("runs")
def list_memory_runs(
    user_id: Annotated[str, typer.Option("--user-id")] = "local-user",
    relationship_id: Annotated[str, typer.Option("--relationship-id")] = "primary",
    conversation_id: Annotated[str | None, typer.Option("--conversation-id")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 50,
    json_output: Annotated[bool, typer.Option("--json", help="输出 JSON。")] = False,
) -> None:
    """查看记忆 Gate 与模型抽取运行记录。"""
    runs = asyncio.run(
        _list_memory_runs(
            user_id=user_id,
            relationship_id=relationship_id,
            conversation_id=conversation_id,
            limit=limit,
        )
    )
    if json_output:
        console.print_json(data=[run.model_dump(mode="json") for run in runs])
        return
    console.print(_build_extraction_runs_table(runs))


@memory_app.command("audits")
def list_memory_audits(
    user_id: Annotated[str, typer.Option("--user-id")] = "local-user",
    relationship_id: Annotated[str, typer.Option("--relationship-id")] = "primary",
    source_message_id: Annotated[
        str | None,
        typer.Option("--source-message-id"),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 100,
    json_output: Annotated[bool, typer.Option("--json", help="输出 JSON。")] = False,
) -> None:
    """查看记忆准入、关系判断和生命周期迁移审计。"""
    audits = asyncio.run(
        _list_transition_audits(
            user_id=user_id,
            relationship_id=relationship_id,
            source_message_id=source_message_id,
            limit=limit,
        )
    )
    if json_output:
        console.print_json(data=[audit.model_dump(mode="json") for audit in audits])
        return
    console.print(_build_transition_audit_table(audits))


@memory_app.command("watch")
def watch_memory(
    user_id: Annotated[str, typer.Option("--user-id")] = "local-user",
    relationship_id: Annotated[str, typer.Option("--relationship-id")] = "primary",
    conversation_id: Annotated[str | None, typer.Option("--conversation-id")] = None,
    interval: Annotated[
        float,
        typer.Option("--interval", min=0.5, max=30, help="刷新间隔（秒）。"),
    ] = 1,
    include_inactive: Annotated[
        bool,
        typer.Option("--include-inactive", help="同时显示 rejected/expired/superseded 记忆。"),
    ] = False,
) -> None:
    """持续刷新记忆与抽取运行记录；默认按关系显示活动记忆。"""
    try:
        asyncio.run(
            _watch_memory(
                user_id,
                relationship_id,
                interval,
                conversation_id=conversation_id,
                include_inactive=include_inactive,
            )
        )
    except KeyboardInterrupt:
        return


@app.command()
def advice(
    query: Annotated[str, typer.Argument(help="需要咨询的恋爱问题。")],
    stage: Annotated[
        RelationshipStage,
        typer.Option("--stage", help="当前关系阶段。"),
    ] = RelationshipStage.UNKNOWN,
    user_id: Annotated[str, typer.Option("--user-id")] = "local-user",
    relationship_id: Annotated[str, typer.Option("--relationship-id")] = "primary",
    conversation_id: Annotated[str | None, typer.Option("--conversation-id")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="输出 JSON。")] = False,
) -> None:
    """获取基于知识检索的恋爱咨询建议。"""
    try:
        result = asyncio.run(
            _run_advice(
                AdviceRequest(
                    user_id=user_id,
                    relationship_id=relationship_id,
                    conversation_id=conversation_id,
                    query=query,
                    relationship_stage=stage,
                )
            )
        )
    except Exception as exc:
        console.print(f"[red]咨询流程执行失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc
    if json_output:
        console.print_json(result.model_dump_json())
        return
    _render_advice(result, query=query)


@app.command()
def chat(
    stage: Annotated[
        RelationshipStage,
        typer.Option("--stage", help="当前关系阶段。"),
    ] = RelationshipStage.UNKNOWN,
    user_id: Annotated[str, typer.Option("--user-id")] = "local-user",
    relationship_id: Annotated[str, typer.Option("--relationship-id")] = "primary",
    conversation_id: Annotated[str | None, typer.Option("--conversation-id")] = None,
    debug_memory: Annotated[
        bool,
        typer.Option("--debug-memory", help="每轮显示记忆抽取结果。"),
    ] = False,
    debug_route: Annotated[
        bool,
        typer.Option(
            "--debug-route/--no-debug-route",
            help="每轮显示任务、目标和场景路由。",
        ),
    ] = True,
    stream_output: Annotated[
        bool,
        typer.Option("--stream/--no-stream", help="流式显示模型生成中的结构化回答。"),
    ] = True,
    show_timings: Annotated[
        bool,
        typer.Option("--timings/--no-timings", hidden=True),
    ] = False,
) -> None:
    """在固定关系和会话中持续进行多轮咨询。"""
    del show_timings  # Retained as a hidden no-op for command compatibility.
    try:
        asyncio.run(
            _run_chat(
                user_id=user_id,
                relationship_id=relationship_id,
                conversation_id=conversation_id or str(uuid4()),
                stage=stage,
                debug_memory=debug_memory,
                debug_route=debug_route,
                stream_output=stream_output,
            )
        )
    except KeyboardInterrupt:
        console.print()


@app.command("plan-date")
def plan_date(
    city: Annotated[str, typer.Option("--city", help="约会所在城市。")],
    area: Annotated[str | None, typer.Option("--area", help="商圈或区域。")] = None,
    planned_date: Annotated[
        str | None,
        typer.Option("--date", help="约会日期；配置天气 provider 后用于天气参考。"),
    ] = None,
    end_date: Annotated[
        str | None,
        typer.Option("--end-date", help="多日行程结束日期，格式 YYYY-MM-DD。"),
    ] = None,
    days: Annotated[
        int | None,
        typer.Option("--days", min=1, max=5, help="行程天数，当前最多 5 天。"),
    ] = None,
    budget: Annotated[int, typer.Option("--budget", min=1, help="两人总预算。")] = 500,
    budget_scope: Annotated[
        BudgetScope,
        typer.Option("--budget-scope", help="预算口径：total 或 per_day。"),
    ] = BudgetScope.TOTAL,
    preferences: Annotated[
        str,
        typer.Option("--preferences", help="使用逗号分隔的偏好。"),
    ] = "",
    dining_keywords: Annotated[
        str,
        typer.Option("--dining-keywords", help="晚餐的精确菜系或餐厅关键词。"),
    ] = "",
    activity_keywords: Annotated[
        str,
        typer.Option("--activity-keywords", help="活动地点的精确关键词。"),
    ] = "",
    excluded_keywords: Annotated[
        str,
        typer.Option("--excluded-keywords", help="需要排除的地点或菜系关键词。"),
    ] = "",
    transport: Annotated[
        TransportMode,
        typer.Option("--transport", help="交通方式。"),
    ] = TransportMode.TRANSIT,
    user_id: Annotated[str, typer.Option("--user-id")] = "local-user",
    relationship_id: Annotated[str, typer.Option("--relationship-id")] = "primary",
    json_output: Annotated[bool, typer.Option("--json", help="输出 JSON。")] = False,
) -> None:
    """根据约束生成使用演示地图数据的约会计划。"""
    preference_list = [item.strip() for item in preferences.split(",") if item.strip()]
    dining_keyword_list = [item.strip() for item in dining_keywords.split(",") if item.strip()]
    activity_keyword_list = [item.strip() for item in activity_keywords.split(",") if item.strip()]
    excluded_keyword_list = [item.strip() for item in excluded_keywords.split(",") if item.strip()]
    try:
        parsed_date = Date.fromisoformat(planned_date) if planned_date else None
        parsed_end_date = Date.fromisoformat(end_date) if end_date else None
    except ValueError as exc:
        raise typer.BadParameter(
            "日期必须使用 YYYY-MM-DD 格式。",
            param_hint="--date/--end-date",
        ) from exc
    try:
        result = asyncio.run(
            _run_date_plan(
                DatePlanRequest(
                    user_id=user_id,
                    relationship_id=relationship_id,
                    city=city,
                    area=area,
                    date=parsed_date,
                    end_date=parsed_end_date,
                    day_count=days or 1,
                    budget=budget,
                    budget_scope=budget_scope,
                    preferences=preference_list,
                    dining_keywords=dining_keyword_list,
                    activity_keywords=activity_keyword_list,
                    excluded_keywords=excluded_keyword_list,
                    transport_mode=transport,
                )
            )
        )
    except Exception as exc:
        console.print(f"[red]约会规划执行失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc
    if json_output:
        console.print_json(result.model_dump_json())
        return
    _render_date_plan(result)


def _render_advice(result: AdviceResponse, *, query: str | None = None) -> None:
    mode = choose_advice_presentation(result, query=query)
    if mode == AdvicePresentationMode.COMPACT:
        console.print(format_compact_advice(result))
        return

    console.print(Panel(result.assessment, title=result.problem_summary, border_style="cyan"))
    _render_list("建议行动", result.recommended_actions)
    _render_list("可以这样表达", result.sample_phrases)
    _render_list("需要进一步确认", result.clarifying_questions)
    _render_list("不建议", result.avoid_actions)
    _render_list("风险提醒", result.risk_notes)

    if result.sources:
        table = Table(title="参考知识")
        table.add_column("ID")
        table.add_column("标题")
        table.add_column("基础分", justify="right")
        table.add_column("软加权", justify="right")
        table.add_column("总分", justify="right")
        for source in result.sources:
            table.add_row(
                source.document_id,
                source.title,
                f"{source.base_score:.3f}" if source.base_score is not None else "-",
                f"{sum(source.score_components.values()):.3f}",
                f"{source.score:.2f}" if source.score is not None else "-",
            )
        console.print(table)


def _render_remember_result(result: RememberResult) -> None:
    if result.extraction_run_id:
        console.print(
            "[dim]抽取运行：[/dim]"
            f"{result.extraction_run_id}  "
            "[dim]来源会话：[/dim]"
            f"{result.message.conversation_id}"
        )
    if result.pending:
        console.print("[cyan]记忆抽取正在后台继续；可在另一个终端用 memory watch 查看。[/cyan]")
        return
    if result.extraction_error:
        console.print(f"[yellow]记忆抽取未完成：[/yellow]{result.extraction_error}")
        if result.saved:
            console.print(f"[green]上下文事件已保留：[/green]{len(result.saved)} 条。")
            _render_memory_table([saved.item for saved in result.saved])
        return
    if result.gate_decision is not None and not result.gate_decision.should_extract:
        console.print(f"[dim]记忆 Gate 已跳过模型抽取：{result.gate_decision.reason.value}。[/dim]")
        return
    if not result.saved:
        console.print("[yellow]这段文本没有产生可持久化的记忆。[/yellow]")
    else:
        console.print(
            f"[green]记忆处理完成：[/green]{len(result.saved)} 条，"
            f"跳过低置信度 {result.skipped_low_confidence} 条。"
        )
        _render_memory_table([saved.item for saved in result.saved])
    if result.discarded_spans:
        console.print("\n[bold]未写入记忆的片段[/bold]")
        for discarded in result.discarded_spans:
            console.print(f"  - {discarded.text} [dim]({discarded.reason.value})[/dim]")


def _render_memory_table(items: list[MemoryItem]) -> None:
    console.print(_build_memory_table(items))


def _build_memory_table(items: list[MemoryItem]) -> Table:
    table = Table(title=f"关系记忆（{len(items)} 条）")
    table.add_column("ID", no_wrap=True)
    table.add_column("关系")
    table.add_column("类型")
    table.add_column("状态")
    table.add_column("关注")
    table.add_column("视角")
    table.add_column("时间")
    table.add_column("摘要")
    for item in items:
        table.add_row(
            item.id,
            item.relationship_id,
            item.kind.value,
            item.status.value,
            memory_attention_reason(item) or "-",
            item.perspective.value,
            _memory_time_label(item),
            item.summary,
        )
    return table


def _build_relationship_plan_table(plans: list[RelationshipPlan]) -> Table:
    table = Table(title=f"关系计划（{len(plans)} 条）")
    table.add_column("Plan ID", no_wrap=True)
    table.add_column("状态")
    table.add_column("活动")
    table.add_column("参与人")
    table.add_column("计划时间")
    table.add_column("源记忆", no_wrap=True)
    table.add_column("更新时间", no_wrap=True)
    for plan in plans:
        schedule = plan.scheduled_start or plan.scheduled_end
        table.add_row(
            _short_id(plan.plan_id),
            plan.status.value,
            plan.activity_type,
            "、".join(plan.participants) or "-",
            schedule.astimezone().strftime("%m-%d %H:%M") if schedule else "-",
            _short_id(plan.source_memory_id) if plan.source_memory_id else "-",
            plan.updated_at.astimezone().strftime("%m-%d %H:%M:%S"),
        )
    return table


def _memory_time_label(item: MemoryItem) -> str:
    start = item.period_start or item.occurred_at
    expression = item.payload.get("temporal_expression")
    label = start.isoformat(timespec="minutes") if start else str(expression or "-")
    if item.expires_at:
        label += f"\n至 {item.expires_at.isoformat(timespec='minutes')}"
    return label


def _render_date_plan(result: DatePlan) -> None:
    console.print(Panel(result.summary, title=result.title, border_style="green"))
    if result.plan_mode == DatePlanMode.MULTI_DAY and result.days:
        for day in result.days:
            date_label = day.date.isoformat() if day.date else "日期待定"
            _render_date_items_table(
                day.items,
                title=f"第 {day.day_index} 天 · {date_label}",
            )
            console.print(
                f"本日地点费用约 [bold]{day.total_estimated_cost} 元[/bold]，"
                f"活动及途中约 [bold]{day.total_duration_minutes} 分钟[/bold]。"
            )
            if day.weather is not None:
                console.print(
                    f"天气参考：{day.weather.condition}，"
                    f"{day.weather.temperature_low or '-'}-"
                    f"{day.weather.temperature_high or '-'}℃，"
                    f"降雨概率约 {day.weather.rain_probability or 0}%"
                )
            _render_list("住宿备注", day.lodging_notes)
    else:
        _render_date_items_table(result.items, title="约会行程")

    console.print(
        f"预计总费用：[bold]{result.total_estimated_cost} 元[/bold]；"
        f"总时长约 [bold]{result.total_duration_minutes} 分钟[/bold]。"
    )
    if result.plan_mode == DatePlanMode.SINGLE_DAY and result.weather is not None:
        console.print(
            f"天气参考：{result.weather.condition}，"
            f"{result.weather.temperature_low or '-'}-{result.weather.temperature_high or '-'}℃，"
            f"降雨概率约 {result.weather.rain_probability or 0}%"
        )
    _render_list("说明", result.notes)
    _render_place_details("地点详情", [item.place for item in result.items])
    _render_place_details("备选地点", result.alternatives)


def _render_date_items_table(items: list, *, title: str) -> None:
    table = Table(title=title)
    table.add_column("顺序", justify="right")
    table.add_column("地点")
    table.add_column("安排")
    table.add_column("费用", justify="right")
    for item in items:
        route = ""
        if item.route_from_previous:
            route = (
                f"；途中约 {item.route_from_previous.duration_minutes} 分钟"
                f"/{item.route_from_previous.distance_meters / 1000:.1f} 公里"
            )
        cost_prefix = "约 " if item.place.cost_is_estimate else ""
        meal_label = {
            "breakfast": "早餐",
            "lunch": "午餐",
            "dinner": "晚餐",
        }.get(item.meal_type or "")
        slot_label = " / ".join(
            dict.fromkeys(value for value in (item.time_label, meal_label) if value)
        )
        place_label = f"[{slot_label}] {item.place.name}" if slot_label else item.place.name
        table.add_row(
            str(item.order),
            place_label,
            f"停留 {item.duration_minutes} 分钟{route}\n{item.reason}",
            f"{cost_prefix}{item.estimated_cost} 元",
        )
    if not items:
        table.add_row("-", "暂无可用地点", "等待补充或放宽条件", "-")
    console.print(table)


def _render_list(title: str, values: list[str]) -> None:
    if not values:
        return
    console.print(f"\n[bold]{title}[/bold]")
    for index, value in enumerate(values, start=1):
        console.print(f"  {index}. {value}")


def _render_place_details(title: str, places: list) -> None:
    if not places:
        return
    console.print(f"\n[bold]{title}[/bold]")
    for place in places:
        details = [place.address]
        if place.rating is not None:
            details.append(f"评分 {place.rating:.1f}")
        if place.opening_hours:
            details.append(f"营业 {place.opening_hours}")
        console.print(f"  [bold]{place.name}[/bold]：{'；'.join(details)}")
        if place.map_url:
            console.print(f"    [link={place.map_url}]在高德地图中查看[/link]")


async def _run_advice(request: AdviceRequest) -> AdviceResponse:
    container = build_container()
    try:
        container.start_background_warmup()
        return await container.advice_agent.advise(request)
    finally:
        await container.aclose()


async def _run_chat(
    *,
    user_id: str,
    relationship_id: str,
    conversation_id: str,
    stage: RelationshipStage,
    debug_memory: bool,
    debug_route: bool,
    stream_output: bool,
) -> None:
    container = build_container()
    container.start_background_warmup()
    active_task: TaskType | None = None
    console.print(
        f"[dim]会话 {conversation_id} · 关系 {relationship_id} · "
        "输入 /quit 退出，/new 新建会话，/retry 重试失败回答[/dim]"
    )
    try:
        while True:
            try:
                query = (
                    await asyncio.to_thread(
                        console.input,
                        "\n[bold cyan]你> [/bold cyan]",
                    )
                ).strip()
            except EOFError:
                break
            if not query:
                continue
            command = query.casefold()
            if command in {"/quit", "/exit"}:
                break
            if command == "/new":
                conversation_id = str(uuid4())
                active_task = None
                console.print(f"[dim]新会话：{conversation_id}[/dim]")
                continue
            live_display = _LiveTurnDisplay(enabled=stream_output)
            trace = ExecutionTrace(live_display.on_timing)
            live_display.start()
            try:
                if command == "/retry":
                    turn = await container.conversation_agent.retry_last_failed_advice(
                        user_id=user_id,
                        relationship_id=relationship_id,
                        conversation_id=conversation_id,
                        trace=trace,
                        stream_callback=(live_display.on_stream if stream_output else None),
                    )
                else:
                    turn = await container.conversation_agent.chat(
                        ConversationRequest(
                            user_id=user_id,
                            relationship_id=relationship_id,
                            conversation_id=conversation_id,
                            query=query,
                            relationship_stage=stage,
                            active_task=active_task,
                        ),
                        trace=trace,
                        stream_callback=(live_display.on_stream if stream_output else None),
                    )
            except Exception as exc:
                live_display.stop()
                _render_turn_error(exc, trace)
                continue
            finally:
                live_display.stop()
            active_task = turn.active_task
            console.print("\n[bold green]LoveApp[/bold green]")
            if turn.advice is not None:
                _render_advice(
                    turn.advice,
                    query=None if command == "/retry" else query,
                )
            elif turn.date_plan is not None:
                if turn.message:
                    console.print(turn.message)
                    console.print()
                _render_date_plan(turn.date_plan)
            elif turn.message:
                console.print(turn.message)
            if turn.follow_up_prompt:
                console.print(f"\n[dim]{turn.follow_up_prompt}[/dim]")
            if debug_route:
                console.print()
                _render_route(turn.route, active_task)
                if turn.date_task_state is not None:
                    _render_date_task_state(turn.date_task_state)
                _render_date_operation_outcome(trace)
                _render_date_requirement_mutations(trace)
                _render_date_validation(trace)
                _render_date_plan_telemetry(trace)
            if debug_memory and turn.memory_result is not None:
                console.print()
                _render_remember_result(turn.memory_result)
    finally:
        await container.aclose()


class _LiveTurnDisplay:
    def __init__(self, *, enabled: bool) -> None:
        self._enabled = enabled
        self._current_stage = "准备执行"
        self._streamed: dict[str, list[str]] = {}
        self._live = Live(
            self._renderable(),
            console=console,
            refresh_per_second=8,
            transient=True,
        )
        self._started = False

    def start(self) -> None:
        if self._enabled and not self._started:
            self._live.start(refresh=True)
            self._started = True

    def stop(self) -> None:
        if self._started:
            self._live.stop()
            self._started = False

    def on_timing(self, event: TimingEvent) -> None:
        if event.phase == "started" and event.name != "total":
            self._current_stage = _TIMING_LABELS.get(event.name, event.name)
        elif event.phase == "failed":
            self._current_stage = f"{_TIMING_LABELS.get(event.name, event.name)}失败"
        elif event.phase == "completed" and self._current_stage == _TIMING_LABELS.get(
            event.name, event.name
        ):
            self._current_stage = "等待并行模块完成"
        if self._started:
            self._live.update(self._renderable(), refresh=True)

    def on_stream(self, event: AdviceStreamEvent) -> None:
        values = self._streamed.setdefault(event.field, [])
        if event.index < len(values):
            values[event.index] = event.text
        else:
            values.append(event.text)
        if self._started:
            self._live.update(self._renderable(), refresh=True)

    def _renderable(self):
        spinner = Spinner("dots", text=f"{self._current_stage}...")
        preview = self._stream_preview()
        if preview is None:
            return spinner
        return Group(spinner, Panel(preview, title="LoveApp 生成中", border_style="cyan"))

    def _stream_preview(self) -> Text | None:
        if not self._streamed:
            return None
        text = Text()
        summary = self._streamed.get("problem_summary", [])
        assessment = self._streamed.get("assessment", [])
        if summary:
            text.append(summary[0], style="bold")
        if assessment:
            if text:
                text.append("\n\n")
            text.append(assessment[0])
        for field, title in _STREAM_FIELD_LABELS.items():
            values = self._streamed.get(field, [])
            if not values:
                continue
            text.append(f"\n\n{title}\n", style="bold")
            for index, value in enumerate(values, start=1):
                text.append(f"{index}. {value}\n")
        return text


def _render_turn_error(exc: Exception, trace: ExecutionTrace) -> None:
    failed = trace.failed_step
    stage = _TIMING_LABELS.get(failed.name, failed.name) if failed else "未知阶段"
    module = type(exc).__module__
    detail = str(exc)
    if module.startswith("qdrant_client") or "Unexpected Response:" in detail:
        console.print(
            f"[red]本轮执行失败（{stage}）：[/red]Qdrant 返回异常。"
            "请确认 Docker Desktop 和 loveapp-qdrant 容器正在运行。"
        )
        console.print(f"[dim]{detail}[/dim]")
        return
    console.print(f"[red]本轮执行失败（{stage}）：[/red]{detail}")


def _format_duration(milliseconds: float) -> str:
    if milliseconds < 1000:
        return f"{milliseconds:.0f} ms"
    return f"{milliseconds / 1000:.2f} s"


_TIMING_LABELS = {
    "total": "总耗时",
    "history_load": "加载近期对话",
    "memory_sidecar_sync": "等待记忆侧路",
    "routing": "混合路由",
    "route_slot_validation": "路由 Slot 校验",
    "clarify_intent": "澄清意图",
    "out_of_scope": "领域外请求",
    "conversation_flow_state_persistence": "保存会话路由状态",
    "advice_classification": "建议场景确认",
    "safety_scan": "风险扫描",
    "user_message_persistence": "保存用户消息",
    "memory_extraction": "记忆抽取",
    "context_load": "加载关系上下文",
    "policy_resolution": "合并场景策略",
    "rag_retrieval": "RAG 检索",
    "embedding_warmup_wait": "等待 Embedding 就绪",
    "rag_query_embedding": "生成查询向量",
    "rag_vector_search": "Qdrant 候选召回",
    "rag_candidate_scoring": "内存候选召回",
    "rag_soft_rerank": "RAG 软加权重排",
    "memory_model_attempt_1": "记忆模型尝试 1",
    "memory_model_attempt_2": "记忆模型尝试 2",
    "memory_model_strong_attempt_2": "强模型升级尝试",
    "memory_claim_verifier": "高风险声明验证",
    "memory_extraction_upgrade_gate": "记忆升级判定",
    "answer_generation": "模型生成回答",
    "policy_enforcement": "执行硬约束",
    "assistant_message_persistence": "保存模型回答",
    "safety_response": "生成安全响应",
    "sensitive_safety_response": "生成敏感安全响应",
    "casual_response": "生成普通回复",
    "date_memory_load": "加载约会偏好",
    "date_task_state_persistence": "保存约会任务状态",
    "weather_lookup": "查询约会天气",
    "map_search": "地图地点检索",
    "date_plan_build": "生成约会计划",
}


_STREAM_FIELD_LABELS = {
    "recommended_actions": "建议行动",
    "sample_phrases": "可以这样表达",
    "clarifying_questions": "需要进一步确认",
    "alternatives": "备选思路",
    "avoid_actions": "不建议",
    "risk_notes": "风险提醒",
}


def _render_route(route: RouteResult, active_task: TaskType | None) -> None:
    table = Table(title="本轮路由")
    table.add_column("字段")
    table.add_column("结果")
    table.add_row("task", route.task_type.value)
    if route.rule_task_type is not None:
        table.add_row("rule_task", route.rule_task_type.value)
    if route.llm_task_type is not None:
        table.add_row("llm_task", route.llm_task_type.value)
    if route.task_guard_applied:
        table.add_row("task_guard", "保留规则一级任务")
    table.add_row("secondary_tasks", ", ".join(item.value for item in route.secondary_tasks) or "-")
    table.add_row("task_confidence", f"{route.task_confidence:.2f}")
    table.add_row("active_task", active_task.value if active_task else "-")
    table.add_row("goal", route.primary_goal.value if route.primary_goal else "-")
    table.add_row(
        "secondary_goals",
        ", ".join(item.value for item in route.secondary_goals) or "-",
    )
    table.add_row(
        "scenario",
        route.primary_scenario.value if route.primary_scenario else "-",
    )
    table.add_row(
        "secondary_scenarios",
        ", ".join(item.value for item in route.secondary_scenarios) or "-",
    )
    table.add_row("risk", route.risk_level.value)
    if route.recent_risk_inherited:
        table.add_row("recent_risk_inherited", "yes")
    if route.recent_risk_deescalated:
        table.add_row("recent_risk_deescalated", "yes")
    table.add_row("source", route.source.value)
    table.add_row("llm_used", "yes" if route.llm_used else "no")
    if route.clarification_triggered:
        table.add_row("clarification", route.clarification_reason or "yes")
        table.add_row("clarification_options", " / ".join(route.clarification_options) or "-")
    elif route.clarification_exhausted:
        table.add_row("clarification", "exhausted")
        table.add_row("clarification_reason", route.clarification_reason or "-")
    if route.out_of_scope_reason:
        table.add_row("out_of_scope_reason", route.out_of_scope_reason)
    if route.pending_task is not None:
        table.add_row("pending_task", route.pending_task.value)
        table.add_row("pending_task_source", route.pending_task_source or "-")
    if route.slot_accepted_fields:
        table.add_row(
            "slot_accepted_fields",
            "; ".join(f"{key}={value}" for key, value in route.slot_accepted_fields.items()),
        )
    if route.slot_rejected_fields:
        table.add_row(
            "slot_rejected_fields",
            "; ".join(f"{key}={value}" for key, value in route.slot_rejected_fields.items()),
        )
    if route.slot_field_sources:
        table.add_row(
            "slot_field_sources",
            "; ".join(f"{key}={value}" for key, value in route.slot_field_sources.items()),
        )
    if route.router_model:
        table.add_row("router_model", route.router_model)
    if route.router_prompt_version:
        table.add_row("router_prompt_version", route.router_prompt_version)
    if route.router_duration_ms is not None:
        table.add_row("router_duration", _format_duration(route.router_duration_ms))
    if route.fallback_reason:
        table.add_row("fallback_reason", route.fallback_reason)
    if route.date_request_mode.value != "none":
        table.add_row("date_request_mode", route.date_request_mode.value)
    if route.task_type == TaskType.DATE_PLANNING or route.date_intent.value != "none":
        table.add_row("date_intent", route.date_intent.value)
        table.add_row("date_mutation", route.date_mutation.value)
        table.add_row(
            "date_replace_targets",
            ", ".join(route.date_plan.replace_place_names) or "-",
        )
        table.add_row("date_missing_fields", ", ".join(route.date_missing_fields) or "-")
    if route.llm_error:
        table.add_row("llm_error", route.llm_error)
    console.print(table)

    if (
        route.task_type == TaskType.DATE_PLANNING
        or route.date_semantic_parse_required
        or route.date_semantic_model is not None
    ):
        _render_date_semantic(route)

    score_table = Table(title="规则得分")
    score_table.add_column("类型")
    score_table.add_column("标签")
    score_table.add_column("分数", justify="right")
    for score_type, scores in (
        ("task", route.task_scores),
        ("goal", route.goal_scores),
        ("scenario", route.scenario_scores),
    ):
        for label, score in sorted(scores.items(), key=lambda item: item[1], reverse=True):
            score_table.add_row(score_type, label.value, f"{score:.2f}")
    console.print(score_table)


def _render_date_semantic(route: RouteResult) -> None:
    table = Table(title="Date Semantic")
    table.add_column("字段")
    table.add_column("值")
    table.add_row("date_semantic_llm_used", "yes" if route.date_semantic_llm_used else "no")
    table.add_row("date_semantic_model", route.date_semantic_model or "-")
    table.add_row("date_semantic_thinking", route.date_semantic_thinking or "-")
    table.add_row("date_semantic_prompt_version", route.date_semantic_prompt_version or "-")
    table.add_row(
        "date_semantic_input_tokens",
        str(route.date_semantic_input_tokens)
        if route.date_semantic_input_tokens is not None
        else "-",
    )
    table.add_row(
        "date_semantic_output_tokens",
        str(route.date_semantic_output_tokens)
        if route.date_semantic_output_tokens is not None
        else "-",
    )
    table.add_row(
        "date_semantic_duration_ms",
        f"{route.date_semantic_duration_ms:.3f}"
        if route.date_semantic_duration_ms is not None
        else "-",
    )
    table.add_row(
        "date_semantic_trigger_reasons",
        ", ".join(route.date_semantic_trigger_reasons) or "-",
    )
    table.add_row("date_semantic_fallback_reason", route.date_semantic_fallback_reason or "-")
    if route.date_semantic_error:
        table.add_row("date_semantic_error", route.date_semantic_error)
    validation_error_path = getattr(route, "date_semantic_validation_error_path", None)
    invalid_field = getattr(route, "date_semantic_invalid_field", None)
    raw_operation_type = getattr(route, "date_semantic_raw_operation_type", None)
    if validation_error_path:
        table.add_row(
            "semantic_validation_error_path",
            validation_error_path,
        )
    if invalid_field:
        table.add_row("semantic_invalid_field", invalid_field)
    if raw_operation_type:
        table.add_row(
            "semantic_raw_operation_type",
            raw_operation_type,
        )
    console.print(table)


def _render_date_task_state(state: DatePlanningTaskState) -> None:
    table = Table(title="约会任务状态")
    table.add_column("字段")
    table.add_column("值")
    table.add_row("status", state.status.value)
    table.add_row("city", state.city or "-")
    table.add_row("area", state.area or "-")
    table.add_row("plan_mode", state.plan_mode.value)
    table.add_row("date", state.date.isoformat() if state.date else "-")
    table.add_row("end_date", state.end_date.isoformat() if state.end_date else "-")
    table.add_row("day_count", str(state.day_count) if state.day_count else "-")
    table.add_row("nights", str(state.nights) if state.nights is not None else "-")
    table.add_row("target_day", str(state.target_day) if state.target_day else "-")
    table.add_row("start_time", state.start_time.isoformat() if state.start_time else "-")
    table.add_row("budget", str(state.budget) if state.budget is not None else "默认 500")
    table.add_row("budget_scope", state.budget_scope.value)
    table.add_row("preferences", "、".join(state.preferences) or "-")
    table.add_row("dining_keywords", "、".join(state.dining_keywords) or "-")
    table.add_row(
        "meal_keywords",
        "；".join(f"{meal}: {'、'.join(values)}" for meal, values in state.meal_keywords.items())
        or "-",
    )
    table.add_row("activity_keywords", "、".join(state.activity_keywords) or "-")
    table.add_row("schedule_hints", "、".join(state.schedule_hints) or "-")
    table.add_row("excluded_keywords", "、".join(state.excluded_keywords) or "-")
    table.add_row("transport", state.transport_mode.value if state.transport_mode else "-")
    table.add_row("notes", "；".join(state.notes) or "-")
    table.add_row("constraints", "；".join(state.constraints) or "-")
    table.add_row("lodging_notes", "；".join(state.lodging_notes) or "-")
    table.add_row("missing", "、".join(state.missing_fields) or "-")
    table.add_row("clarification_round", str(state.clarification_round))
    table.add_row("fallback_used", "yes" if state.fallback_used else "no")
    table.add_row("plan_version", str(state.plan_version))
    table.add_row(
        "current_plan_items",
        "、".join(
            item.place.name for item in (state.current_plan.items if state.current_plan else [])
        )
        or "-",
    )
    table.add_row("last_mutation", state.last_mutation.value)
    if state.weather is not None:
        table.add_row(
            "weather",
            f"{state.weather.condition} / {state.weather.source}",
        )
    if state.weather_forecasts:
        table.add_row(
            "weather_days",
            "；".join(
                f"{forecast.date.isoformat()} {forecast.condition}"
                for forecast in state.weather_forecasts
            ),
        )
    console.print(table)
    _render_date_requirements(state)
    _render_requirement_satisfaction(state)
    _render_date_operation_batch(state)
    _render_date_task_diff(state)


def _render_date_requirements(state: DatePlanningTaskState) -> None:
    if not state.requirements:
        return
    table = Table(title="Date Requirements")
    table.add_column("ID")
    table.add_column("Alternatives")
    table.add_column("Cardinality")
    table.add_column("Role")
    table.add_column("Source")
    for requirement in state.requirements:
        alternatives = "\n".join(
            _format_desired_date_stop(stop) for stop in requirement.alternatives
        )
        roles = list(
            dict.fromkeys(
                role for stop in requirement.alternatives if (role := _format_date_stop_role(stop))
            )
        )
        maximum = "*" if requirement.max_satisfied is None else str(requirement.max_satisfied)
        table.add_row(
            requirement.id,
            alternatives,
            f"{requirement.min_satisfied}..{maximum}",
            "\n".join(roles) or "-",
            requirement.source_span or "-",
        )
    console.print(table)


def _render_requirement_satisfaction(state: DatePlanningTaskState) -> None:
    if not state.requirement_satisfaction:
        return
    table = Table(title="Requirement Satisfaction")
    table.add_column("Requirement")
    table.add_column("Status")
    table.add_column("Matched Places")
    table.add_column("Reason")
    table.add_column("Details")
    for match in state.requirement_satisfaction:
        table.add_row(
            match.requirement_id,
            match.status.value,
            ", ".join(match.matched_place_ids) or "-",
            match.reason_code or "-",
            match.details or "-",
        )
    console.print(table)


def _render_date_operation_batch(state: DatePlanningTaskState) -> None:
    if not state.last_operations:
        return
    table = Table(title="Operation Batch")
    table.add_column("#", justify="right")
    table.add_column("Type")
    table.add_column("Target")
    table.add_column("Payload / Constraint")
    table.add_column("Source")
    for index, operation in enumerate(state.last_operations, start=1):
        table.add_row(
            str(index),
            operation.type.value,
            _format_stop_reference(operation.target),
            _format_date_operation_payload(operation),
            operation.source_span or "-",
        )
    console.print(table)


def _render_date_task_diff(state: DatePlanningTaskState) -> None:
    if state.last_task_diff is None or not state.last_task_diff.changed:
        return
    table = Table(title="Task Diff")
    table.add_column("Field")
    table.add_column("Before")
    table.add_column("After")
    for field, change in state.last_task_diff.changes.items():
        table.add_row(
            field,
            _format_debug_value(change.before),
            _format_debug_value(change.after),
        )
    console.print(table)


def _render_date_operation_outcome(trace: ExecutionTrace) -> None:
    records = [record for record in trace.snapshot() if record.name == "date_operation_execute"]
    if not records:
        return
    details = records[-1].details
    table = Table(title="Operation Outcome")
    table.add_column("Requested", justify="right")
    table.add_column("Applied", justify="right")
    table.add_column("Rejected", justify="right")
    table.add_column("Rejection Reasons")
    table.add_row(
        str(details.get("requested_count", 0)),
        str(details.get("applied_count", 0)),
        str(details.get("rejected_count", 0)),
        str(details.get("rejections_json") or "-"),
    )
    console.print(table)


def _render_date_requirement_mutations(trace: ExecutionTrace) -> None:
    records = [
        record
        for record in trace.snapshot()
        if record.name == "date_requirement_projection"
        and record.details.get("requirement_update_type") != "unchanged"
    ]
    if not records:
        return
    details = records[-1].details
    table = Table(title="Requirement Mutations")
    table.add_column("Type")
    table.add_column("Created")
    table.add_column("Updated")
    table.add_column("Removed")
    table.add_row(
        str(details.get("requirement_update_type") or "-"),
        str(details.get("requirement_created_ids") or "-"),
        str(details.get("requirement_updated_ids") or "-"),
        str(details.get("requirement_removed_ids") or "-"),
    )
    console.print(table)


def _render_date_validation(trace: ExecutionTrace) -> None:
    records = trace.snapshot()
    validation = next(
        (record.details for record in reversed(records) if record.name == "date_plan_validation"),
        None,
    )
    satisfaction = next(
        (
            record.details
            for record in reversed(records)
            if record.name == "date_requirement_satisfaction"
        ),
        {},
    )
    operation_batch = next(
        (record.details for record in reversed(records) if record.name == "date_operation_batch"),
        {},
    )
    if validation is None:
        return
    table = Table(title="Validation")
    table.add_column("Hard Valid")
    table.add_column("Issues")
    table.add_column("Historical Unsatisfied", justify="right")
    table.add_column("Current Unsatisfied", justify="right")
    table.add_column("Dedupe")
    table.add_column("Preserve Unmentioned")
    table.add_row(
        "yes" if validation.get("validation_hard_valid") else "no",
        str(validation.get("issue_codes") or "-"),
        str(satisfaction.get("historical_unsatisfied_count", 0)),
        str(satisfaction.get("current_turn_unsatisfied_count", 0)),
        (
            f"{operation_batch.get('operation_dedupe_input_count', 0)} -> "
            f"{operation_batch.get('operation_dedupe_output_count', 0)}"
        ),
        "yes" if operation_batch.get("mutation_policy_preserve_unmentioned") else "no",
    )
    console.print(table)


def _render_date_plan_telemetry(trace: ExecutionTrace) -> None:
    records = [record for record in trace.snapshot() if "date_plan_changed" in record.details]
    if not records:
        return
    table = Table(title="Date Plan Telemetry")
    table.add_column("Stage")
    table.add_column("date_plan_changed")
    for record in records:
        table.add_row(
            record.name,
            _format_debug_value(record.details["date_plan_changed"]),
        )
    console.print(table)


def _format_desired_date_stop(stop: DesiredDateStop) -> str:
    identity = stop.place_name or stop.keyword or "-"
    return f"{stop.kind.value}:{identity}"


def _format_date_stop_role(stop: DesiredDateStop) -> str:
    parts: list[str] = []
    if stop.meal_type is not None:
        parts.append(f"meal={stop.meal_type.value}")
    if stop.target_day is not None:
        parts.append(f"day={stop.target_day}")
    if stop.time_window is not None:
        window = stop.time_window.label or (
            f"{stop.time_window.start or '*'}-{stop.time_window.end or '*'}"
        )
        parts.append(f"window={window}")
    if stop.after is not None:
        parts.append(f"after={_format_temporal_reference(stop.after)}")
    if stop.before is not None:
        parts.append(f"before={_format_temporal_reference(stop.before)}")
    if stop.constraints is not None:
        constraints = stop.constraints.model_dump(exclude_none=True)
        parts.extend(f"{name}={value}" for name, value in constraints.items())
    return ", ".join(parts)


def _format_temporal_reference(reference: Any) -> str:
    value = getattr(reference, "value", None)
    if value is not None:
        return str(value)
    return _format_stop_reference(reference)


def _format_stop_reference(reference: StopReference | None) -> str:
    if reference is None:
        return "-"
    parts = [
        f"{name}={value.value if hasattr(value, 'value') else value}"
        for name in ("place_id", "place_name", "keyword", "meal_type", "ordinal")
        if (value := getattr(reference, name)) is not None
    ]
    return ", ".join(parts) or "-"


def _format_date_operation_payload(operation: DatePlanOperation) -> str:
    if operation.constraint_field is not None:
        return (
            f"{operation.constraint_field.value}={_format_debug_value(operation.constraint_value)}"
        )
    if operation.payload is not None:
        payload = _format_desired_date_stop(operation.payload)
        role = _format_date_stop_role(operation.payload)
        return f"{payload} ({role})" if role else payload
    if operation.requirement_update is not None:
        targets = ",".join(
            reference.requirement_id or _format_stop_reference(reference.stop_reference)
            for reference in operation.requirement_update.targets
        )
        maximum = operation.requirement_update.max_satisfied
        return (
            f"targets={targets}; cardinality="
            f"{operation.requirement_update.min_satisfied}..{maximum or '*'}"
        )
    return "-"


def _format_debug_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str):
        return value
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


async def _run_date_plan(request: DatePlanRequest) -> DatePlan:
    container = build_container()
    try:
        return await container.date_planning_agent.plan(request)
    finally:
        await container.aclose()


async def _ingest_documents(documents, recreate: bool) -> tuple[int, int]:
    store = build_qdrant_store(get_settings())
    try:
        indexed = await store.index_documents(documents, recreate=recreate)
        return indexed, await store.count()
    finally:
        await store.aclose()


async def _search_documents(query: str, limit: int):
    store = build_qdrant_store(get_settings())
    try:
        return await store.search(query=query, limit=limit)
    finally:
        await store.aclose()


async def _remember_text(
    *,
    text: str,
    user_id: str,
    relationship_id: str,
    conversation_id: str | None,
    status: MemoryStatus,
) -> RememberResult:
    container = build_memory_container()
    try:
        return await container.memory_service.remember_text(
            user_id=user_id,
            relationship_id=relationship_id,
            conversation_id=conversation_id,
            text=text,
            status=status,
            raise_on_extraction_error=True,
        )
    finally:
        await container.aclose()


async def _list_memory(
    user_id: str,
    relationship_id: str | None,
    kind: MemoryKind | None,
    status: MemoryStatus | None,
    limit: int,
) -> list[MemoryItem]:
    container = build_memory_container(enable_extraction=False)
    try:
        return await container.memory_store.list_memories(
            user_id=user_id,
            relationship_id=relationship_id,
            kind=kind,
            status=status,
            limit=limit,
        )
    finally:
        await container.aclose()


async def _list_relationship_plans(
    *,
    user_id: str,
    relationship_id: str,
    status: PlanStatus | None,
    limit: int,
) -> list[RelationshipPlan]:
    container = build_memory_container(enable_extraction=False)
    try:
        await container.memory_service.reconcile_relationship_plans(
            user_id=user_id,
            relationship_id=relationship_id,
        )
        return await container.memory_store.list_relationship_plans(
            user_id=user_id,
            relationship_id=relationship_id,
            status=status,
            limit=limit,
        )
    finally:
        await container.aclose()


async def _list_memory_runs(
    *,
    user_id: str,
    relationship_id: str,
    conversation_id: str | None,
    limit: int,
) -> list[MemoryExtractionRun]:
    container = build_memory_container(enable_extraction=False)
    try:
        return await container.memory_store.list_extraction_runs(
            user_id=user_id,
            relationship_id=relationship_id,
            conversation_id=conversation_id,
            limit=limit,
        )
    finally:
        await container.aclose()


async def _list_transition_audits(
    *,
    user_id: str,
    relationship_id: str,
    source_message_id: str | None,
    limit: int,
) -> list[MemoryTransitionAudit]:
    container = build_memory_container(enable_extraction=False)
    try:
        return await container.memory_store.list_transition_audits(
            user_id=user_id,
            relationship_id=relationship_id,
            source_message_id=source_message_id,
            limit=limit,
        )
    finally:
        await container.aclose()


async def _get_memory(memory_id: str, user_id: str) -> MemoryItem | None:
    container = build_memory_container(enable_extraction=False)
    try:
        return await container.memory_store.get_memory(memory_id, user_id)
    finally:
        await container.aclose()


async def _set_memory_status(
    memory_id: str,
    user_id: str,
    status: MemoryStatus,
) -> MemoryItem | None:
    container = build_memory_container(enable_extraction=False)
    try:
        return await container.memory_store.set_memory_status(memory_id, user_id, status)
    finally:
        await container.aclose()


async def _delete_memory(memory_id: str, user_id: str) -> bool:
    container = build_memory_container(enable_extraction=False)
    try:
        return await container.memory_store.delete_memory(memory_id, user_id)
    finally:
        await container.aclose()


async def _clear_memory(user_id: str, relationship_id: str | None) -> int:
    container = build_memory_container(enable_extraction=False)
    try:
        return await container.memory_store.clear_memories(user_id, relationship_id)
    finally:
        await container.aclose()


async def _get_memory_context(user_id: str, relationship_id: str):
    container = build_memory_container(enable_extraction=False)
    try:
        return await container.memory_service.get_context(user_id, relationship_id)
    finally:
        await container.aclose()


async def _compact_memory(
    *,
    user_id: str,
    relationship_id: str,
    apply_changes: bool,
) -> MemoryCompactionResult:
    container = build_memory_container(enable_extraction=False)
    try:
        return await container.memory_service.compact_memories(
            user_id=user_id,
            relationship_id=relationship_id,
            apply=apply_changes,
        )
    finally:
        await container.aclose()


async def _watch_memory(
    user_id: str,
    relationship_id: str,
    interval: float,
    *,
    conversation_id: str | None = None,
    include_inactive: bool = False,
) -> None:
    container = build_memory_container(enable_extraction=False)
    try:
        with Live(
            _build_memory_watch_view([], [], []),
            console=console,
            refresh_per_second=4,
            transient=False,
        ) as live:
            while True:
                items = await container.memory_store.list_memories(
                    user_id=user_id,
                    relationship_id=relationship_id,
                    limit=1000,
                )
                if conversation_id is not None:
                    source_messages = await container.memory_store.list_messages(
                        user_id=user_id,
                        relationship_id=relationship_id,
                        conversation_id=conversation_id,
                        limit=1000,
                    )
                    source_message_ids = {message.id for message in source_messages}
                    items = [item for item in items if item.source_message_id in source_message_ids]
                if not include_inactive:
                    items = [
                        item
                        for item in items
                        if item.status in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
                    ]
                plans = await container.memory_service.reconcile_relationship_plans(
                    user_id=user_id,
                    relationship_id=relationship_id,
                )
                if conversation_id is not None:
                    plans = [plan for plan in plans if plan.source_message_id in source_message_ids]
                if not include_inactive:
                    plans = [
                        plan
                        for plan in plans
                        if plan.status in {PlanStatus.PROPOSED, PlanStatus.CONFIRMED}
                    ]
                runs = await container.memory_store.list_extraction_runs(
                    user_id=user_id,
                    relationship_id=relationship_id,
                    conversation_id=conversation_id,
                    limit=50,
                )
                live.update(_build_memory_watch_view(items, plans, runs), refresh=True)
                await asyncio.sleep(interval)
    finally:
        await container.aclose()


def _build_memory_watch_view(
    items: list[MemoryItem],
    plans: list[RelationshipPlan],
    runs: list[MemoryExtractionRun],
) -> Group:
    return Group(
        _build_memory_table(items),
        _build_relationship_plan_table(plans),
        _build_extraction_runs_table(runs),
    )


def _build_extraction_runs_table(runs: list[MemoryExtractionRun]) -> Table:
    table = Table(title=f"记忆抽取运行记录（最近 {len(runs)} 条）")
    table.add_column("Run ID", no_wrap=True)
    table.add_column("会话", no_wrap=True)
    table.add_column("状态")
    table.add_column("Gate")
    table.add_column("尝试")
    table.add_column("写入记忆")
    table.add_column("未写入片段")
    table.add_column("错误")
    table.add_column("更新时间", no_wrap=True)
    for run in runs:
        attempts = (
            ", ".join(
                f"#{attempt.attempt} {attempt.tier or '-'} {attempt.status.value} "
                f"{attempt.repair_status or '-'} "
                f"{_format_duration(attempt.duration_ms)}"
                f"{(' upgrade=' + attempt.upgrade_reason) if attempt.upgrade_reason else ''}"
                f"{(' discard=' + attempt.discard_reason) if attempt.discard_reason else ''}"
                for attempt in run.attempts
            )
            or "-"
        )
        saved = ", ".join(_short_id(memory_id) for memory_id in run.saved_memory_ids) or "-"
        discarded = (
            "\n".join(f"{span.text} ({span.reason.value})" for span in run.discarded_spans) or "-"
        )
        gate = run.gate_decision.reason.value
        if run.gate_decision.signals:
            gate += f" ({', '.join(run.gate_decision.signals)})"
        table.add_row(
            _short_id(run.id),
            _short_id(run.conversation_id),
            _run_status_text(run.status.value),
            gate,
            attempts,
            saved,
            discarded,
            run.error or "-",
            run.updated_at.astimezone().strftime("%m-%d %H:%M:%S"),
        )
    return table


def _build_transition_audit_table(audits: list[MemoryTransitionAudit]) -> Table:
    table = Table(title=f"记忆生命周期审计（{len(audits)} 条）")
    table.add_column("时间")
    table.add_column("关系")
    table.add_column("决策")
    table.add_column("关系判断")
    table.add_column("规则")
    table.add_column("谓词")
    table.add_column("目标")
    table.add_column("原因")
    for audit in audits:
        table.add_row(
            audit.created_at.astimezone().strftime("%m-%d %H:%M:%S"),
            _short_id(audit.relationship_id),
            audit.decision.value,
            audit.relation.value,
            audit.rule_name,
            audit.canonical_predicate or audit.raw_predicate or "-",
            ", ".join(_short_id(value) for value in audit.target_memory_ids) or "-",
            audit.reason,
        )
    return table


def _run_status_text(status: str) -> Text:
    style = {
        "running": "cyan",
        "completed": "green",
        "skipped": "dim",
        "failed": "red",
        "cancelled": "yellow",
    }.get(status, "white")
    return Text(status, style=style)


def _short_id(value: str) -> str:
    return value if len(value) <= 12 else f"{value[:8]}..."

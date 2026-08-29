import asyncio
import json
from datetime import date as Date
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

import typer
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from loveapp.adapters.knowledge.loader import load_knowledge_path, merge_knowledge_documents
from loveapp.application.advice_presentation import (
    AdvicePresentationMode,
    choose_advice_presentation,
    format_compact_advice,
)
from loveapp.bootstrap import (
    build_container,
    build_memory_container,
    build_qdrant_store,
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
    evaluate_live_routing_conversations,
    evaluate_memory_foundation,
    evaluate_memory_lifecycle,
    evaluate_routing_conversations,
    run_baseline,
)

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
) -> None:
    """运行确定性 Policy Eval，或显式启用的真实模型 Live Eval。"""
    output_path = output or Path(
        "evals/baselines/routing_v4_live_current.json"
        if live
        else "evals/baselines/routing_v4_current.json"
    )
    try:
        settings = get_settings()
        if live:
            report = asyncio.run(
                evaluate_live_routing_conversations(
                    dataset,
                    settings,
                    input_cost_per_million=input_cost_per_million,
                    output_cost_per_million=output_cost_per_million,
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
                )
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
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
        failed = [
            name for name, passed in report["acceptance_targets"].items() if not passed
        ]
        console.print(f"[red]未达到验收目标：[/red]{', '.join(failed)}")
        raise typer.Exit(code=2)


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
) -> None:
    """按问答块生成向量并写入本地 Qdrant。"""
    try:
        external_documents = load_knowledge_path(path)
        documents = merge_knowledge_documents(load_seed_documents(), external_documents)
        if not documents:
            raise ValueError("没有找到可入库的知识文档。")
        with console.status("正在生成本地向量并写入 Qdrant..."):
            indexed, total = asyncio.run(_ingest_documents(documents, recreate))
    except Exception as exc:
        console.print(f"[red]知识入库失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc
    console.print(
        f"[green]入库完成：[/green]Seed 与正式文档统一去重后写入 {indexed} 个问答 chunk，"
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
    json_output: Annotated[bool, typer.Option("--json", help="输出 JSON。")]=False,
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
        "输入 /quit 退出，/new 新建会话[/dim]"
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
                    stream_callback=live_display.on_stream if stream_output else None,
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
                _render_advice(turn.advice, query=query)
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
                role
                for stop in requirement.alternatives
                if (role := _format_date_stop_role(stop))
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
        "yes"
        if operation_batch.get("mutation_policy_preserve_unmentioned")
        else "no",
    )
    console.print(table)


def _render_date_plan_telemetry(trace: ExecutionTrace) -> None:
    records = [
        record
        for record in trace.snapshot()
        if "date_plan_changed" in record.details
    ]
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
            f"{operation.constraint_field.value}="
            f"{_format_debug_value(operation.constraint_value)}"
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

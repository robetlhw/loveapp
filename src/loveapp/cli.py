import asyncio
import json
from datetime import date as Date
from pathlib import Path
from typing import Annotated
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
from loveapp.core.config import get_settings
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.advice import AdviceRequest, AdviceResponse, AdviceStreamEvent
from loveapp.domain.conversation import ConversationRequest
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
from loveapp.domain.observability import StepTiming, TimingEvent, TimingStatus
from loveapp.domain.relationship_plan import PlanStatus, RelationshipPlan
from loveapp.domain.routing import RouteResult
from loveapp.evaluation import (
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
    ] = Path("evals/routing/cases_v2.jsonl"),
    output: Annotated[
        Path,
        typer.Option("--output", help="路由评测报告保存路径。"),
    ] = Path("evals/baselines/routing_v2_current.json"),
) -> None:
    """运行不调用真实模型的多轮路由策略回归评测。"""
    try:
        report = asyncio.run(evaluate_routing_conversations(dataset))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        console.print(f"[red]路由评测失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]路由评测已保存：[/green]{output}")
    table = Table(title="多轮路由评测摘要")
    table.add_column("指标")
    table.add_column("值", justify="right")
    for key, value in report.items():
        if key == "cases" or isinstance(value, (dict, list)):
            continue
        table.add_row(key, str(value))
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
        typer.Option("--timings/--no-timings", help="每轮显示各执行模块耗时。"),
    ] = True,
) -> None:
    """在固定关系和会话中持续进行多轮咨询。"""
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
                show_timings=show_timings,
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
    show_timings: bool,
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
            live_display = _LiveTurnDisplay(enabled=stream_output or show_timings)
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
                if show_timings:
                    _render_timings(trace.snapshot())
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
            if debug_route:
                console.print()
                _render_route(turn.route, active_task)
                if turn.date_task_state is not None:
                    _render_date_task_state(turn.date_task_state)
            if show_timings:
                console.print()
                _render_timings(turn.timings)
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


def _render_timings(records: list[StepTiming]) -> None:
    table = Table(title="本轮模块耗时", caption="并行模块的耗时会重叠，不应直接相加。")
    table.add_column("模块")
    table.add_column("状态")
    table.add_column("开始", justify="right")
    table.add_column("耗时", justify="right")
    table.add_column("Trace 详情")
    for record in sorted(records, key=lambda value: value.started_offset_ms):
        status, style = {
            TimingStatus.RUNNING: ("后台中", "cyan"),
            TimingStatus.COMPLETED: ("完成", "green"),
            TimingStatus.FAILED: ("失败", "red"),
        }[record.status]
        table.add_row(
            _TIMING_LABELS.get(record.name, record.name),
            f"[{style}]{status}[/{style}]",
            _format_duration(record.started_offset_ms),
            _format_duration(record.duration_ms),
            _format_timing_details(record),
        )
    console.print(table)


def _format_duration(milliseconds: float) -> str:
    if milliseconds < 1000:
        return f"{milliseconds:.0f} ms"
    return f"{milliseconds / 1000:.2f} s"


def _format_timing_details(record: StepTiming) -> str:
    preferred_keys = (
        "already_ready",
        "available",
        "activity_count",
        "restaurant_count",
        "cafe_count",
        "dining_keywords",
        "activity_keywords",
        "excluded_keywords",
        "source",
        "candidate_count",
        "returned_count",
        "gate_reason",
        "gate_should_extract",
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "claim_count",
        "discarded_span_count",
        "claim_confidences",
        "invalid_claim_count",
        "tier",
        "repair_status",
        "repair_steps",
        "original_claim_count",
        "repaired_claim_count",
        "discarded_claim_count",
        "should_upgrade",
        "upgrade_reason",
        "discard_reason",
        "failure_category",
        "retry_reason",
    )
    values = [f"{key}={record.details[key]}" for key in preferred_keys if key in record.details]
    return ", ".join(values) or "-"


_TIMING_LABELS = {
    "total": "总耗时",
    "history_load": "加载近期对话",
    "memory_sidecar_sync": "等待记忆侧路",
    "routing": "混合路由",
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
    table.add_row("source", route.source.value)
    table.add_row("llm_used", "yes" if route.llm_used else "no")
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

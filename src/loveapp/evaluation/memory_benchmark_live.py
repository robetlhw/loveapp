"""Real model replay with isolated Stores, serial turns and concurrent cases."""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import subprocess
from contextlib import suppress
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from loveapp.bootstrap import build_embedding_provider, build_memory_container
from loveapp.core.config import get_settings
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.memory import MessageRole
from loveapp.evaluation.memory_benchmark_scoring import SCORING_VERSION, score_case, summarize
from loveapp.evaluation.memory_benchmark_v1 import (
    MemoryBenchmarkCase,
    load_memory_benchmark_v1_cases,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = Path("evals/memory/benchmark_v1.jsonl")


def write_json(path: Path, value: Any) -> None:
    """Each case owns its checkpoint path; replacement cannot expose half JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _bounded(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                child[:4000]
                if key in {"raw_output", "raw_model_response", "raw_response"}
                and isinstance(child, str)
                else _bounded(child)
            )
            for key, child in value.items()
            if not any(
                word in key.casefold() for word in ("api_key", "authorization", "access_token")
            )
        }
    if isinstance(value, list):
        return [_bounded(item) for item in value]
    return value


def decode_details(details: dict[str, Any]) -> dict[str, Any]:
    result = dict(details)
    for key, value in details.items():
        if key.endswith("_json") and isinstance(value, str):
            with suppress(ValueError):
                result[key[:-5]] = json.loads(value)
    if "memory_kind" in result:
        result["kind"] = result["memory_kind"]
    return result


class ReplayCapture:
    """Transparent per-container taps; no mocked output or changed arguments."""

    def __init__(self, container: Any) -> None:
        self.extractor = container.memory_service._extractor
        self.reset()
        original_extract = self.extractor.extract

        @wraps(original_extract)
        async def extract(*args: Any, **kwargs: Any) -> Any:
            self.called = True
            try:
                output = await original_extract(*args, **kwargs)
                self.extraction = output.model_dump(mode="json")
                return output
            finally:
                self.diagnostic = copy.deepcopy(getattr(self.extractor, "last_diagnostic", {}))

        self.extractor.extract = extract
        original_commit = container.memory_store.commit_memory_batch

        @wraps(original_commit)
        async def commit(*args: Any, **kwargs: Any) -> Any:
            batch = kwargs.get("batch")
            if batch is None:
                batch = next((arg for arg in args if hasattr(arg, "operations")), None)
            entry: dict[str, Any] = {
                "batch": batch.model_dump(mode="json") if batch is not None else {},
                "result": None,
                "error": None,
            }
            self.batches.append(entry)
            try:
                result = await original_commit(*args, **kwargs)
                entry["result"] = result.model_dump(mode="json")
                return result
            except Exception as exc:
                entry["error"] = f"{type(exc).__name__}: {exc}"[:500]
                raise

        container.memory_store.commit_memory_batch = commit

    def reset(self) -> None:
        self.called = False
        self.extraction: dict[str, Any] = {}
        self.diagnostic: dict[str, Any] = {}
        self.batches: list[dict[str, Any]] = []


async def _snapshot(store: Any, scope: dict[str, str]) -> list[dict[str, Any]]:
    return [
        item.model_dump(mode="json")
        for item in await store.list_memories(
            user_id=scope["user_id"],
            relationship_id=scope["relationship_id"],
            limit=500,
            read_only=True,
        )
    ]


async def replay_case(
    case: MemoryBenchmarkCase,
    *,
    settings: Any,
    embedding: Any,
    output_dir: Path,
    fingerprint: str,
    progress: Any = None,
    container_factory: Any = build_memory_container,
) -> dict[str, Any]:
    container = container_factory(settings, embedding_provider=embedding)
    capture = ReplayCapture(container)
    identity = f"benchmark-{case.id}-{uuid4().hex}"
    scope = dict(user_id=identity, relationship_id=identity, conversation_id=identity)
    rows: list[dict[str, Any]] = []
    seen_runs: set[str] = set()
    report: dict[str, Any] = dict(
        id=case.id,
        category=case.category,
        scenario=case.scenario,
        length_class=case.length_class,
        review_reason=case.review_reason,
        fingerprint=fingerprint,
        completed=False,
        scope=scope,
        expected=case.expected.model_dump(mode="json"),
        turns=rows,
    )
    try:
        for turn in case.conversation:
            if turn.role == "assistant":
                await container.memory_store.add_message(
                    **scope, role=MessageRole.ASSISTANT, content=turn.content
                )
                continue
            capture.reset()
            before = await _snapshot(container.memory_store, scope)
            trace = ExecutionTrace()
            start = perf_counter()
            error = None
            result = None
            try:
                result = await container.memory_service.remember_text(
                    **scope, text=turn.content, trace=trace
                )
                while await container.memory_service.wait_for_long_tail_shadow(timeout_seconds=30):
                    pass
                await container.memory_service.wait_for_scope(
                    user_id=scope["user_id"],
                    relationship_id=scope["relationship_id"],
                    timeout_seconds=30,
                )
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"[:1000]
            after = await _snapshot(container.memory_store, scope)
            all_runs = await container.memory_store.list_extraction_runs(**scope, limit=100)
            new_runs = [run for run in all_runs if run.id not in seen_runs]
            seen_runs.update(run.id for run in new_runs)
            source_id = (
                result.message.id
                if result is not None
                else (new_runs[0].source_message_id if new_runs else None)
            )
            audits = (
                await container.memory_store.list_transition_audits(
                    user_id=scope["user_id"],
                    relationship_id=scope["relationship_id"],
                    source_message_id=source_id,
                    limit=1000,
                )
                if source_id
                else []
            )
            trace_rows = [record.model_dump(mode="json") for record in trace.snapshot()]
            row = _bounded(
                dict(
                    turn_id=turn.turn_id,
                    text=turn.content,
                    source_message_id=source_id,
                    duration_ms=round((perf_counter() - start) * 1000, 2),
                    error=error,
                    gate=result.gate_decision.model_dump(mode="json")
                    if result and result.gate_decision
                    else None,
                    extractor_called=capture.called,
                    extraction=capture.extraction,
                    diagnostic=capture.diagnostic,
                    extraction_runs=[run.model_dump(mode="json") for run in new_runs],
                    normalized_candidates=[
                        decode_details(record["details"])
                        for record in trace_rows
                        if record["name"] == "memory_candidate_governance"
                    ],
                    write_batches=capture.batches,
                    audits=[audit.model_dump(mode="json") for audit in audits],
                    trace=trace_rows,
                    db_before=before,
                    db_after=after,
                    saved=[save.model_dump(mode="json") for save in result.saved] if result else [],
                    extraction_error=result.extraction_error if result else error,
                )
            )
            rows.append(row)
            await asyncio.to_thread(write_json, output_dir / f"{case.id}.partial.json", report)
            if progress:
                progress(case.id, turn.turn_id, row)
        report["final_memories"] = await _snapshot(container.memory_store, scope)
        report["quality"] = score_case(case, rows)
        report["completed"] = True
        await asyncio.to_thread(write_json, output_dir / f"{case.id}.json", report)
        return report
    finally:
        await container.aclose()


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# Memory Benchmark V1 — 全量真实模型评测",
        "",
        f"开始：`{report['started_at']}`；结束：`{report.get('completed_at', '未完成')}`。",
        "",
        f"数据 SHA256：`{report['dataset_sha256']}`",
        "",
        f"评分版本：`{SCORING_VERSION}`；冻结 fingerprint：`{report['fingerprint']}`。",
        "",
        f"实际模型配置：`{json.dumps(report['configuration'], ensure_ascii=False)}`",
        "",
        f"并发 Case：{report['concurrency']}；同 Case 用户轮串行。",
        "",
        "生产 Store 写入：False；隔离内存 Store 写入：True。复用生产 Gate、两阶段提取、",
        "归一化、Admission、关系治理、UoW 和投影；模型/治理失败如实保留。",
        "",
        "质量指标只覆盖 Golden 标注的 checkpoints；背景轮仍真实执行并影响历史。",
        "raw extraction 独立评分，DB row 只用于目标绑定/最终写入校验。",
        "value_groups 是预先固定的词汇锚点，不等同于人工语义评判。",
        "NEEDS_REVIEW 单独列出，未按模型输出修改 Ground Truth。",
        "",
        "## 指标",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
    ]
    for key, value in report["metrics"].items():
        if not isinstance(value, dict):
            lines.append(f"| {key} | {value if value is not None else 'N/A'} |")
    lines += [
        "",
        "tokens/latency 汇总来自持久化 extraction attempts，不与 trace 重复相加。",
        "Gate、Judge、Verifier 的调用与耗时另见逐轮 trace；该汇总不是总 API 账单。",
        "",
        "## 类别及失败归因",
        "",
        "| 类别 | Case 数 | 严格通过 | 待复核 |",
        "|---|---:|---:|---:|",
    ]
    for category in dict.fromkeys(row["category"] for row in report["cases"]):
        subset = [row for row in report["cases"] if row["category"] == category]
        lines.append(
            f"| {category} | {len(subset)} | {sum(row['quality']['passed'] for row in subset)} | "
            f"{sum(bool(row['review_reason']) for row in subset)} |"
        )
    lines += [
        "",
        f"Primary failure counts：`{report['metrics']['primary_failures']}`。",
        f"Fallback by reason：`{report['metrics'].get('fallback_by_reason', {})}`。",
        f"Trace schema errors：`{report['metrics'].get('trace_schema_error_count', 0)}`；"
        "model Stage2 schema errors："
        f"`{report['metrics'].get('model_stage2_schema_error_count', 0)}`。",
        "最早失败按会话先后及阶段顺序定位；经过某阶段不算失败。",
        "",
        "## 逐 Case",
        "",
        "| Case | 用户轮 | 结果 | 最早失败 | 原因 |",
        "|---|---:|---|---|---|",
    ]
    for row in report["cases"]:
        q = row["quality"]
        reason = str(row.get("review_reason") or q["primary_failure_reason"] or "—").replace(
            "|", "/"
        )
        lines.append(
            f"| {row['id']} | {len(row['turns'])} | {q['classification']} | "
            f"{q['primary_failure_stage'] or '—'} | {reason} |"
        )
    lines += [
        "",
        "## 数据格式与边界",
        "",
        "原文 JSON 是说明性示例：event 需用 interaction_event，type 用 payload.event_type；",
        "pattern/state 是 Event 证据投影的输出，不能注入 raw extraction；",
        "target.ref 引用 case 内 claim_id，同一用户轮可能有多个 claim，不能仅引用 t1。",
        "MIXED 逐子操作检查目标、字段、来源。完整示例见 evals/memory/README_benchmark_v1.md。",
        "",
        "此前 142 用户轮的 preliminary / live_final 文件不是本次验收结果；其长度与评分逻辑已废弃。",
        "本次数据和评分规则在模型调用前冻结，未修改生产 Memory 或 Prompt 来提升成绩。",
        "",
    ]
    return "\n".join(lines)


def _configuration(settings: Any) -> dict[str, Any]:
    return dict(
        memory_backend=settings.memory_backend,
        extraction_mode=settings.memory_extraction_mode,
        extraction_model=settings.memory_extraction_model or settings.llm_model,
        relation_provider=settings.memory_semantic_relation_provider,
        relation_model=settings.memory_semantic_relation_model
        or settings.memory_extraction_model
        or settings.llm_model,
        embedding_model=settings.embedding_model,
    )


def _code_digest() -> str:
    digest = hashlib.sha256()
    for path in sorted((ROOT / "loveapp").rglob("*.py")):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


async def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset_bytes = args.dataset.read_bytes()
    cases = load_memory_benchmark_v1_cases(args.dataset)
    wanted = set(args.case or [])
    if wanted - {case.id for case in cases}:
        raise ValueError("unknown --case")
    cases = [case for case in cases if not wanted or case.id in wanted]
    settings = get_settings().model_copy(
        update=dict(
            memory_backend="memory",
            memory_extraction_provider="llm",
            memory_extraction_mode="two_stage",
            memory_semantic_relation_provider="llm",
        )
    )
    configuration = _configuration(settings)
    dataset_hash = hashlib.sha256(dataset_bytes).hexdigest()
    fingerprint = hashlib.sha256(
        (
            dataset_hash
            + _code_digest()
            + SCORING_VERSION
            + json.dumps(configuration, sort_keys=True)
        ).encode()
    ).hexdigest()
    output = (
        args.output
        or Path(".data/evals") / f"memory_benchmark_v1_live_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    )
    output_dir = output.parent / (output.stem + "_cases")
    if output.exists() and not args.resume:
        raise ValueError("output already exists; use a new output or --resume")
    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not args.resume or previous["fingerprint"] != fingerprint:
            raise ValueError(
                "resume requires the identical frozen dataset, implementation and configuration"
            )
    else:
        previous = dict(started_at=datetime.now(UTC).isoformat())
    report = dict(
        evaluation="memory-benchmark-v1-live",
        scoring_version=SCORING_VERSION,
        started_at=previous["started_at"],
        dataset=str(args.dataset),
        dataset_sha256=dataset_hash,
        fingerprint=fingerprint,
        configuration=configuration,
        concurrency=args.concurrency,
        selected_case_ids=[case.id for case in cases],
        expected_user_turns=sum(
            turn.role == "user" for case in cases for turn in case.conversation
        ),
        production_store_mutation=False,
        isolated_store_mutation=True,
        git_head=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    )
    write_json(manifest_path, report)
    snapshot_path = output_dir / "dataset.snapshot.jsonl"
    if not snapshot_path.exists():
        snapshot_path.write_bytes(dataset_bytes)
    completed = {}
    pending = []
    for case in cases:
        checkpoint = output_dir / f"{case.id}.json"
        if args.resume and checkpoint.exists():
            row = json.loads(checkpoint.read_text(encoding="utf-8"))
            if row.get("fingerprint") != fingerprint or not row.get("completed"):
                raise ValueError(f"invalid checkpoint: {case.id}")
            completed[case.id] = row
        else:
            pending.append(case)
    print(
        f"Frozen {dataset_hash}; {len(cases)} cases, {report['expected_user_turns']} user turns; "
        f"concurrency={args.concurrency}; resumed={len(completed)}",
        flush=True,
    )
    count = sum(len(row["turns"]) for row in completed.values())
    start = perf_counter()

    def progress(case_id: str, turn_id: str, row: dict[str, Any]) -> None:
        nonlocal count
        count += 1
        print(
            f"turn {count}/{report['expected_user_turns']} {case_id}/{turn_id} "
            f"{row['duration_ms'] / 1000:.1f}s "
            f"gate={(row.get('gate') or {}).get('should_extract')} "
            f"extractor={row.get('diagnostic', {}).get('final_extractor_used')} "
            f"elapsed={(perf_counter() - start) / 60:.1f}m",
            flush=True,
        )

    embedding = build_embedding_provider(settings)
    try:
        await embedding.warmup()
        semaphore = asyncio.Semaphore(args.concurrency)

        async def evaluate(case: MemoryBenchmarkCase) -> None:
            async with semaphore:
                print(f"start {case.id}", flush=True)
                row = await replay_case(
                    case,
                    settings=settings,
                    embedding=embedding,
                    output_dir=output_dir,
                    fingerprint=fingerprint,
                    progress=progress,
                )
                completed[case.id] = row
                print(
                    f"done {len(completed)}/{len(cases)} {case.id}: "
                    f"{row['quality']['classification']}",
                    flush=True,
                )

        pending.sort(
            key=lambda case: sum(t.role == "user" for t in case.conversation), reverse=True
        )
        await asyncio.gather(*(evaluate(case) for case in pending))
    finally:
        await embedding.aclose()
        report["cases"] = [completed[case.id] for case in cases if case.id in completed]
        report["metrics"] = summarize(report["cases"])
        report["completed"] = len(completed) == len(cases)
        if report["completed"]:
            report["completed_at"] = datetime.now(UTC).isoformat()
        write_json(output, report)
        output.with_suffix(".md").write_text(render_report(report), encoding="utf-8")
    print(
        json.dumps(dict(output=str(output), metrics=report["metrics"]), ensure_ascii=False),
        flush=True,
    )
    return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    result.add_argument("--output", type=Path)
    result.add_argument("--case", action="append")
    result.add_argument("--concurrency", type=int, default=4, choices=range(1, 9))
    result.add_argument("--resume", action="store_true")
    result.add_argument(
        "--fail-on-error", action="store_true", help="Nonzero if any strict checkpoint fails."
    )
    return result


def main() -> int:
    args = parser().parse_args()
    report = asyncio.run(run(args))
    return int(args.fail_on_error and any(not row["quality"]["passed"] for row in report["cases"]))

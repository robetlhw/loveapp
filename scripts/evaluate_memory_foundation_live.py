"""Run the Memory Foundation dataset through the real Flash extraction API.

This runner intentionally uses an isolated in-memory Store for every case attempt.
It records production MemoryInspector traces, but never reads scripted claims from
the deterministic evaluation fixture and never enables the Strong model.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from loveapp.adapters.memory.in_memory import InMemoryMemoryStore
from loveapp.adapters.memory.openai_compatible import OpenAICompatibleMemoryExtractor
from loveapp.application.memory import MemoryService
from loveapp.application.memory_inspector import MemoryInspector
from loveapp.core.config import Settings
from loveapp.domain.memory import MemoryStatus
from loveapp.ports.memory import MemoryExtractor

DEFAULT_DATASET = Path("evals/memory/cases_v1.jsonl")
DEFAULT_OUTPUT_DIRECTORY = Path(".data/evals")
REPORT_VERSION = "memory-foundation-live-v1"

ProgressCallback = Callable[[str], None]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run Memory Foundation inputs through the configured real Flash model "
            "and save complete MemoryInspector traces locally."
        )
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help=f"JSONL dataset path (default: {DEFAULT_DATASET}).",
    )
    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        default=[],
        help="Run only this case ID. May be supplied more than once.",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Run every selected case this many times in separate stores (default: 1).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output JSON path. Defaults to a timestamped file under .data/evals/.",
    )
    parser.add_argument(
        "--status",
        choices=(MemoryStatus.PROPOSED.value, MemoryStatus.CONFIRMED.value),
        default=MemoryStatus.CONFIRMED.value,
        help="Requested status for extracted claims (default: confirmed).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-turn progress; the final report path is still printed.",
    )
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Return exit code 1 if any case contains an extraction or runner error.",
    )
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    return args


async def evaluate_memory_foundation_live(
    dataset_path: Path,
    output_path: Path,
    *,
    settings: Settings,
    case_ids: tuple[str, ...] = (),
    repeat: int = 1,
    requested_status: MemoryStatus = MemoryStatus.CONFIRMED,
    extractor: MemoryExtractor | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Execute selected natural-language cases and incrementally persist a report."""

    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    flash_config = _resolve_flash_configuration(settings)
    raw_dataset = dataset_path.read_bytes()
    cases = _select_cases(_load_cases(raw_dataset), case_ids)
    started_at = _utc_now()
    run_uuid = str(uuid4())
    report: dict[str, Any] = {
        "run_id": f"memory-foundation-live-{run_uuid}",
        "version": REPORT_VERSION,
        "mode": "live_flash",
        "status": "running",
        "dataset": str(dataset_path.resolve()),
        "dataset_sha256": hashlib.sha256(raw_dataset).hexdigest(),
        "selected_case_ids": [case["id"] for case in cases],
        "repeat": repeat,
        "started_at": started_at.isoformat(),
        "completed_at": None,
        "configuration": {
            **flash_config,
            "requested_status": requested_status.value,
            "memory_backend": "memory",
            "case_isolation": "new_in_memory_store_per_case_attempt",
            "flash_only": True,
            "strong_enabled": False,
            "verifier_enabled": False,
            "scripted_claims_used": False,
        },
        "summary": {},
        "cases": [],
        "errors": [],
    }
    _checkpoint(report, output_path)

    owns_extractor = extractor is None
    flash_extractor = extractor or _build_flash_extractor(settings, flash_config["flash_model"])
    try:
        for repeat_index in range(1, repeat + 1):
            for case in cases:
                case_result = _new_case_result(
                    case,
                    run_uuid=run_uuid,
                    repeat_index=repeat_index,
                )
                report["cases"].append(case_result)
                _checkpoint(report, output_path)
                try:
                    await _execute_case(
                        case,
                        case_result=case_result,
                        extractor=flash_extractor,
                        settings=settings,
                        requested_status=requested_status,
                        checkpoint=lambda: _checkpoint(report, output_path),
                        progress=progress,
                        repeat=repeat,
                    )
                except asyncio.CancelledError:
                    case_result["status"] = "interrupted"
                    case_result["errors"].append(
                        {"stage": "case", "type": "CancelledError", "message": "interrupted"}
                    )
                    raise
                except Exception as exc:  # Keep later isolated cases observable.
                    case_result["status"] = "failed"
                    case_result["errors"].append(_error_record("case", exc))
                _checkpoint(report, output_path)
    except (asyncio.CancelledError, KeyboardInterrupt):
        report["status"] = "interrupted"
        report["completed_at"] = _utc_now().isoformat()
        report["summary"] = _summarize(report["cases"])
        _write_json_atomic(output_path, report)
        raise
    finally:
        if owns_extractor:
            close = getattr(flash_extractor, "aclose", None)
            if close is not None:
                await close()

    report["summary"] = _summarize(report["cases"])
    report["status"] = (
        "partial" if report["summary"]["failed_case_count"] else "completed"
    )
    report["completed_at"] = _utc_now().isoformat()
    _write_json_atomic(output_path, report)
    return report


async def _execute_case(
    case: dict[str, Any],
    *,
    case_result: dict[str, Any],
    extractor: MemoryExtractor,
    settings: Settings,
    requested_status: MemoryStatus,
    checkpoint: Callable[[], None],
    progress: ProgressCallback | None,
    repeat: int,
) -> None:
    reference_time = _case_reference_time(case)
    clock = lambda: reference_time  # noqa: E731 - bound deterministic evaluation clock
    store = InMemoryMemoryStore(clock=clock)
    service = MemoryService(
        store,
        extractor,
        min_confidence=settings.memory_min_confidence,
        tentative_min_confidence=settings.memory_tentative_min_confidence,
        belief_min_confidence=settings.memory_belief_min_confidence,
        context_limit=settings.memory_context_limit,
        history_limit=settings.conversation_history_limit,
        context_wait_seconds=settings.memory_context_wait_seconds,
        shutdown_grace_seconds=settings.memory_shutdown_grace_seconds,
        admission_policy_overrides=settings.memory_admission_policy_overrides,
        verifier=None,
        embedding_provider=None,
        clock=clock,
    )
    inspector = MemoryInspector(
        service,
        store,
        user_id=case_result["scope"]["user_id"],
        relationship_id=case_result["scope"]["relationship_id"],
        conversation_id=case_result["scope"]["conversation_id"],
        requested_status=requested_status,
        limit=500,
    )
    try:
        turns = case["turns"]
        for turn_index, turn in enumerate(turns, start=1):
            if progress is not None:
                progress(
                    f"[{case['id']} repeat {case_result['repeat']}/{repeat} "
                    f"turn {turn_index}/{len(turns)}] calling Memory pipeline"
                )
            try:
                turn_report = await inspector.execute_turn(turn["input"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                turn_report = {
                    "turn": turn_index,
                    "input": turn["input"],
                    "status": "runner_error",
                    "error": _error_record("turn", exc),
                }
                case_result["errors"].append(_error_record(f"turn_{turn_index}", exc))
            else:
                turn_report["status"] = _turn_status(turn_report)
                extraction_error = turn_report.get("extraction_error")
                if extraction_error:
                    case_result["errors"].append(
                        {
                            "stage": f"turn_{turn_index}_extraction",
                            "type": "ExtractionError",
                            "message": str(extraction_error),
                        }
                    )
            turn_report["fixture_expectation"] = turn.get("expect", {})
            case_result["turns"].append(turn_report)
            checkpoint()

        try:
            case_result["final"] = {
                "active_memories": await inspector.list_memories(),
                "all_memories": await inspector.list_memories(include_all=True),
                "current_context": await inspector.get_context(query=turns[-1]["input"]),
                "history": await inspector.get_history(),
                "extraction_runs": await inspector.list_runs(limit=500),
                "transition_audits": await inspector.list_audits(limit=500),
            }
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            case_result["errors"].append(_error_record("final_snapshot", exc))
            case_result["final"] = {"error": _error_record("final_snapshot", exc)}
        case_result["status"] = "failed" if case_result["errors"] else "completed"
        case_result["completed_at"] = _utc_now().isoformat()
        checkpoint()
    finally:
        await service.aclose()
        await store.aclose()


def _new_case_result(
    case: dict[str, Any],
    *,
    run_uuid: str,
    repeat_index: int,
) -> dict[str, Any]:
    scope_suffix = f"{run_uuid}-{case['id'].casefold()}-r{repeat_index}"
    return {
        "id": case["id"],
        "category": case.get("category"),
        "description": case.get("description"),
        "tags": case.get("tags", []),
        "reference_time": case.get("reference_time"),
        "repeat": repeat_index,
        "status": "running",
        "scope": {
            "user_id": f"memory-foundation-user-{scope_suffix}",
            "relationship_id": f"memory-foundation-relationship-{scope_suffix}",
            "conversation_id": f"memory-foundation-conversation-{scope_suffix}",
        },
        "turns": [],
        "final": {},
        "fixture_expected_final": case.get("expected_final", {}),
        "errors": [],
        "completed_at": None,
    }


def _resolve_flash_configuration(settings: Settings) -> dict[str, Any]:
    api_key = settings.llm_api_key
    if api_key is None or not api_key.get_secret_value().strip():
        raise ValueError("LOVEAPP_LLM_API_KEY is required for the live Flash evaluation")
    base_url = (settings.llm_base_url or "").strip()
    if not base_url:
        raise ValueError("LOVEAPP_LLM_BASE_URL is required for the live Flash evaluation")
    flash_model = settings.memory_extraction_model.strip() or settings.llm_model.strip()
    if not flash_model:
        raise ValueError(
            "LOVEAPP_MEMORY_EXTRACTION_MODEL or LOVEAPP_LLM_MODEL is required "
            "for the live Flash evaluation"
        )
    return {
        "effective_extraction_provider": "llm",
        "configured_llm_provider": settings.llm_provider,
        "configured_memory_extraction_provider": settings.memory_extraction_provider,
        "api_host": _api_host(base_url),
        "flash_model": flash_model,
        "timeout_seconds": settings.memory_extraction_timeout_seconds,
        "max_retries": settings.memory_extraction_max_retries,
        "max_tokens": settings.memory_extraction_max_tokens,
        "thinking": settings.memory_extraction_thinking,
    }


def _build_flash_extractor(
    settings: Settings,
    flash_model: str,
) -> OpenAICompatibleMemoryExtractor:
    assert settings.llm_api_key is not None
    assert settings.llm_base_url is not None
    return OpenAICompatibleMemoryExtractor(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=flash_model,
        timeout_seconds=settings.memory_extraction_timeout_seconds,
        max_retries=settings.memory_extraction_max_retries,
        max_tokens=settings.memory_extraction_max_tokens,
        tier="flash",
        thinking=settings.memory_extraction_thinking,
    )


def _load_cases(raw: bytes) -> list[dict[str, Any]]:
    cases = [
        json.loads(line)
        for line in raw.decode("utf-8-sig").splitlines()
        if line.strip()
    ]
    seen: set[str] = set()
    for case in cases:
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("every case requires a non-empty string id")
        if case_id in seen:
            raise ValueError(f"duplicate case id: {case_id}")
        seen.add(case_id)
        turns = case.get("turns")
        if not isinstance(turns, list) or not turns:
            raise ValueError(f"case {case_id} requires at least one turn")
        for turn in turns:
            if not isinstance(turn, dict) or not str(turn.get("input") or "").strip():
                raise ValueError(f"case {case_id} contains a turn without input")
    return cases


def _select_cases(
    cases: list[dict[str, Any]],
    requested_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    if not requested_ids:
        return cases
    unique_ids = list(dict.fromkeys(requested_ids))
    by_id = {case["id"]: case for case in cases}
    unknown = [case_id for case_id in unique_ids if case_id not in by_id]
    if unknown:
        raise ValueError(f"unknown case id(s): {', '.join(unknown)}")
    return [by_id[case_id] for case_id in unique_ids]


def _case_reference_time(case: dict[str, Any]) -> datetime:
    value = case.get("reference_time")
    if value is None:
        return _utc_now()
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError(f"case {case['id']} reference_time must include a timezone")
    return parsed


def _turn_status(turn_report: dict[str, Any]) -> str:
    if turn_report.get("extraction_error"):
        return "failed"
    extraction_status = turn_report.get("extraction_run", {}).get("status")
    if extraction_status == "skipped":
        return "skipped"
    return "completed"


def _summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "case_count": len(cases),
        "completed_case_count": sum(case.get("status") == "completed" for case in cases),
        "failed_case_count": sum(case.get("status") != "completed" for case in cases),
        "turn_count": 0,
        "gate_extraction_turn_count": 0,
        "gate_skipped_turn_count": 0,
        "extraction_error_turn_count": 0,
        "flash_call_count": 0,
        "strong_call_count": 0,
        "completed_attempt_count": 0,
        "failed_attempt_count": 0,
        "transport_failure_count": 0,
        "model_response_failure_count": 0,
        "schema_validation_failure_count": 0,
        "unsupported_enum_count": 0,
        "empty_response_count": 0,
        "empty_claim_turn_count": 0,
        "saved_operation_count": 0,
        "created_memory_count": 0,
        "final_active_memory_count": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "p50_flash_latency_ms": 0.0,
        "p95_flash_latency_ms": 0.0,
    }
    latencies: list[float] = []
    for case in cases:
        final = case.get("final", {})
        summary["final_active_memory_count"] += len(final.get("active_memories", []))
        for turn in case.get("turns", []):
            summary["turn_count"] += 1
            gate = turn.get("gate", {})
            if gate.get("should_extract") is True:
                summary["gate_extraction_turn_count"] += 1
            elif gate.get("should_extract") is False:
                summary["gate_skipped_turn_count"] += 1
            if turn.get("extraction_error") or turn.get("status") == "runner_error":
                summary["extraction_error_turn_count"] += 1
            summary["saved_operation_count"] += int(
                turn.get("result", {}).get("saved_count", 0) or 0
            )
            summary["created_memory_count"] += len(
                turn.get("diff", {}).get("added", [])
            )
            run = turn.get("extraction_run", {})
            attempts = run.get("attempts", [])
            if (
                run.get("status") == "completed"
                and attempts
                and int(attempts[-1].get("claim_count") or 0) == 0
            ):
                summary["empty_claim_turn_count"] += 1
            for attempt in attempts:
                tier = str(attempt.get("tier") or "")
                if tier != "flash":
                    if tier == "strong":
                        summary["strong_call_count"] += 1
                    continue
                summary["flash_call_count"] += 1
                duration = attempt.get("duration_ms")
                if isinstance(duration, int | float):
                    latencies.append(float(duration))
                attempt_status = attempt.get("status")
                if attempt_status == "completed":
                    summary["completed_attempt_count"] += 1
                elif attempt_status == "failed":
                    summary["failed_attempt_count"] += 1
                category = str(attempt.get("failure_category") or "")
                if category == "transport":
                    summary["transport_failure_count"] += 1
                elif category:
                    summary["model_response_failure_count"] += 1
                if category == "schema_validation":
                    summary["schema_validation_failure_count"] += 1
                elif category == "unsupported_enum":
                    summary["unsupported_enum_count"] += 1
                elif category == "empty_response":
                    summary["empty_response_count"] += 1
                for field in (
                    "prompt_tokens",
                    "completion_tokens",
                    "reasoning_tokens",
                    "total_tokens",
                ):
                    value = attempt.get(field)
                    if isinstance(value, int):
                        summary[field] += value
    if latencies:
        summary["p50_flash_latency_ms"] = _percentile(latencies, 0.5)
        summary["p95_flash_latency_ms"] = _percentile(latencies, 0.95)
    return summary


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return round(ordered[index], 2)


def default_output_path(*, now: datetime | None = None) -> Path:
    timestamp = (now or _utc_now()).astimezone(UTC).strftime("%Y%m%d_%H%M%S_%f")
    return DEFAULT_OUTPUT_DIRECTORY / f"memory_foundation_live_{timestamp}.json"


def _api_host(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme and parsed.hostname:
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        return f"{parsed.scheme}://{host}"
    return "configured"


def _checkpoint(report: dict[str, Any], output_path: Path) -> None:
    report["summary"] = _summarize(report["cases"])
    _write_json_atomic(output_path, report)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _error_record(stage: str, exc: BaseException) -> dict[str, str]:
    return {
        "stage": stage,
        "type": type(exc).__name__,
        "message": str(exc),
    }


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _progress_printer(message: str) -> None:
    print(message, flush=True)


def main() -> int:
    args = _parse_args()
    output_path = (args.output or default_output_path()).resolve()
    try:
        report = asyncio.run(
            evaluate_memory_foundation_live(
                args.dataset,
                output_path,
                settings=Settings(),
                case_ids=tuple(args.case_ids),
                repeat=args.repeat,
                requested_status=MemoryStatus(args.status),
                progress=None if args.quiet else _progress_printer,
            )
        )
    except KeyboardInterrupt:
        print(f"Interrupted. Partial report: {output_path}")
        return 130
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Live Memory evaluation could not start: {exc}")
        return 2

    summary = report["summary"]
    print(
        "Live Flash Memory evaluation "
        f"{report['status']}: {summary['completed_case_count']}/"
        f"{summary['case_count']} case attempts completed without errors."
    )
    print(f"Report: {output_path}")
    if args.fail_on_error and summary["failed_case_count"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

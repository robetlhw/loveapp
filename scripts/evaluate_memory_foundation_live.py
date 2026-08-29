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
DEFAULT_EXPECTATION_SUFFIX = "_live_expectations.json"
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
        "--expectations",
        type=Path,
        help=(
            "Declarative live semantic expectation fixture. Defaults to the dataset "
            "stem plus '_live_expectations.json' when that file exists."
        ),
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
    expectation_path: Path | None = None,
) -> dict[str, Any]:
    """Execute selected natural-language cases and incrementally persist a report."""

    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    flash_config = _resolve_flash_configuration(settings)
    raw_dataset = dataset_path.read_bytes()
    cases = _select_cases(_load_cases(raw_dataset), case_ids)
    resolved_expectation_path = expectation_path or _default_expectation_path(dataset_path)
    expectation_raw: bytes | None = None
    expectations: dict[str, Any] = {}
    if resolved_expectation_path.exists():
        expectation_raw = resolved_expectation_path.read_bytes()
        expectations = _load_live_expectations(expectation_raw)
        expected_dataset_hash = expectations.get("dataset_sha256")
        actual_dataset_hash = hashlib.sha256(raw_dataset).hexdigest()
        if (
            expected_dataset_hash is not None
            and expected_dataset_hash != actual_dataset_hash
        ):
            raise ValueError(
                "live semantic expectation fixture dataset_sha256 does not match "
                f"{dataset_path}"
            )
    elif expectation_path is not None:
        raise FileNotFoundError(
            f"live semantic expectation fixture not found: {expectation_path}"
        )
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
        "execution_status": "running",
        "semantic_expectations": {
            "enabled": expectation_raw is not None,
            "path": (
                str(resolved_expectation_path.resolve())
                if expectation_raw is not None
                else None
            ),
            "sha256": (
                hashlib.sha256(expectation_raw).hexdigest()
                if expectation_raw is not None
                else None
            ),
            "version": (
                expectations.get("version") if expectation_raw is not None else None
            ),
        },
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
                    semantic_expectation=expectations.get("cases", {}).get(case["id"]),
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
                    case_result["execution_status"] = "interrupted"
                    case_result["errors"].append(
                        {"stage": "case", "type": "CancelledError", "message": "interrupted"}
                    )
                    raise
                except Exception as exc:  # Keep later isolated cases observable.
                    case_result["status"] = "failed"
                    case_result["execution_status"] = "failed"
                    case_result["errors"].append(_error_record("case", exc))
                _checkpoint(report, output_path)
    except (asyncio.CancelledError, KeyboardInterrupt):
        report["status"] = "interrupted"
        report["execution_status"] = "interrupted"
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
    report["execution_status"] = (
        "failed" if report["summary"]["execution_fail_count"] else "passed"
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
        case_result["execution_status"] = (
            "failed" if case_result["errors"] else "passed"
        )
        _apply_semantic_evaluation(case_result)
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
    semantic_expectation: dict[str, Any] | None,
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
        "execution_status": "running",
        "semantic_status": "not_evaluated",
        "scope": {
            "user_id": f"memory-foundation-user-{scope_suffix}",
            "relationship_id": f"memory-foundation-relationship-{scope_suffix}",
            "conversation_id": f"memory-foundation-conversation-{scope_suffix}",
        },
        "turns": [],
        "final": {},
        "semantic_expectation": semantic_expectation,
        "semantic_assertions": [],
        "semantic_failures": [],
        "semantic_warnings": [],
        "semantic_metrics": {},
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


def _load_live_expectations(raw: bytes) -> dict[str, Any]:
    payload = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("live semantic expectation fixture must be a JSON object")
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("live semantic expectation fixture requires a version")
    cases = payload.get("cases")
    if not isinstance(cases, dict):
        raise ValueError("live semantic expectation fixture requires a cases object")
    for case_id, expectation in cases.items():
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("live semantic expectation case IDs must be non-empty strings")
        if not isinstance(expectation, dict):
            raise ValueError(f"live expectation for {case_id} must be an object")
        turns = expectation.get("turns", [])
        if not isinstance(turns, list) or not all(isinstance(turn, dict) for turn in turns):
            raise ValueError(f"live expectation turns for {case_id} must be a list")
        final = expectation.get("final", {})
        if not isinstance(final, dict):
            raise ValueError(f"live expectation final for {case_id} must be an object")
    return payload


def _default_expectation_path(dataset_path: Path) -> Path:
    return dataset_path.with_name(f"{dataset_path.stem}{DEFAULT_EXPECTATION_SUFFIX}")


_SEMANTIC_CATEGORIES = ("gate", "canonical", "perspective", "relation", "lifecycle", "context")


def _apply_semantic_evaluation(case_result: dict[str, Any]) -> None:
    expectation = case_result.get("semantic_expectation")
    if case_result.get("execution_status") != "passed" or not isinstance(expectation, dict):
        case_result["semantic_status"] = "not_evaluated"
        return
    evaluated = _evaluate_semantics(case_result, expectation)
    case_result.update(evaluated)


def _evaluate_semantics(
    case_result: dict[str, Any],
    expectation: dict[str, Any],
) -> dict[str, Any]:
    assertions: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    metrics: dict[str, int] = {
        key: 0
        for category in _SEMANTIC_CATEGORIES
        for key in (f"{category}_expected", f"{category}_matched")
    }
    metrics.update(
        {
            "stale_active_memory_count": 0,
            "duplicate_active_memory_count": 0,
            "confirmed_overwrite_violation_count": 0,
        }
    )

    def record(
        *,
        assertion: str,
        category: str,
        passed: bool,
        expected: Any,
        actual: Any,
        severity: str = "failure",
    ) -> None:
        if severity not in {"failure", "warning"}:
            raise ValueError(f"invalid semantic assertion severity: {severity}")
        metrics[f"{category}_expected"] += 1
        metrics[f"{category}_matched"] += int(passed)
        item = {
            "assertion": assertion,
            "category": category,
            "severity": severity,
            "passed": passed,
            "expected": expected,
            "actual": actual,
        }
        assertions.append(item)
        if not passed:
            (warnings if severity == "warning" else failures).append(item)

    actual_turns = case_result.get("turns", [])
    for turn_index, turn_expectation in enumerate(expectation.get("turns", []), start=1):
        actual = actual_turns[turn_index - 1] if turn_index <= len(actual_turns) else {}
        gate = turn_expectation.get("gate")
        if isinstance(gate, dict):
            severity = _severity(gate)
            actual_gate = actual.get("gate", {})
            if "should_extract" in gate:
                expected_value = gate["should_extract"]
                actual_value = actual_gate.get("should_extract")
                record(
                    assertion=f"turn_{turn_index}.gate.should_extract",
                    category="gate",
                    passed=actual_value == expected_value,
                    expected=expected_value,
                    actual=actual_value,
                    severity=severity,
                )
            if "reason" in gate:
                expected_value = gate["reason"]
                actual_value = actual_gate.get("reason")
                record(
                    assertion=f"turn_{turn_index}.gate.reason",
                    category="gate",
                    passed=_value_matches(actual_value, expected_value),
                    expected=expected_value,
                    actual=actual_value,
                    severity=severity,
                )

        candidates = actual.get("candidates", [])
        for claim_index, spec in enumerate(turn_expectation.get("claims", []), start=1):
            selector = _selector(spec)
            severity = _severity(spec)
            perspective = selector.pop("perspective", _MISSING)
            matched_candidates = _matching_records(candidates, selector)
            if selector:
                record(
                    assertion=f"turn_{turn_index}.claim_{claim_index}.canonical",
                    category="canonical",
                    passed=bool(matched_candidates),
                    expected=selector,
                    actual=_candidate_identities(candidates),
                    severity=severity,
                )
            if perspective is not _MISSING:
                perspective_candidates = matched_candidates if selector else candidates
                if selector and not perspective_candidates:
                    continue
                actual_perspectives = [item.get("perspective") for item in perspective_candidates]
                record(
                    assertion=f"turn_{turn_index}.claim_{claim_index}.perspective",
                    category="perspective",
                    passed=any(
                        _value_matches(actual_value, perspective)
                        for actual_value in actual_perspectives
                    ),
                    expected=perspective,
                    actual=actual_perspectives,
                    severity=severity,
                )

        for relation_index, spec in enumerate(
            turn_expectation.get("relations", []), start=1
        ):
            selector = _selector(spec)
            matching = _matching_records(candidates, selector)
            actual_relations = [item.get("claim_relation") for item in matching]
            allowed = spec.get("allowed", [])
            record(
                assertion=f"turn_{turn_index}.relation_{relation_index}",
                category="relation",
                passed=bool(matching)
                and any(_value_matches(value, allowed) for value in actual_relations),
                expected={"selector": selector, "allowed": allowed},
                actual=actual_relations,
                severity=_severity(spec),
            )

    final_expectation = expectation.get("final", {})
    final = case_result.get("final", {})
    active = final.get("active_memories", [])
    all_memories = final.get("all_memories", [])

    for index, spec in enumerate(final_expectation.get("expected_active", []), start=1):
        selector = _selector(spec)
        record(
            assertion=f"final.expected_active_{index}",
            category="lifecycle",
            passed=bool(_matching_records(active, selector)),
            expected=selector,
            actual=_memory_identities(active),
            severity=_severity(spec),
        )

    for index, spec in enumerate(final_expectation.get("superseded", []), start=1):
        selector = _selector(spec)
        selector.setdefault("status", "superseded")
        record(
            assertion=f"final.superseded_{index}",
            category="lifecycle",
            passed=bool(_matching_records(all_memories, selector)),
            expected=selector,
            actual=_memory_identities(all_memories),
            severity=_severity(spec),
        )

    for index, spec in enumerate(final_expectation.get("forbidden_active", []), start=1):
        selector = _selector(spec)
        matches = _matching_records(active, selector)
        metrics["stale_active_memory_count"] += len(matches)
        record(
            assertion=f"final.forbidden_active_{index}",
            category="lifecycle",
            passed=not matches,
            expected={"selector": selector, "count": 0},
            actual=_memory_identities(matches),
            severity=_severity(spec),
        )

    for index, spec in enumerate(
        final_expectation.get("duplicate_active_max", []), start=1
    ):
        selector = _selector(spec)
        maximum = int(spec.get("max", 1))
        matches = _matching_records(active, selector)
        metrics["duplicate_active_memory_count"] += max(0, len(matches) - maximum)
        record(
            assertion=f"final.duplicate_active_max_{index}",
            category="lifecycle",
            passed=len(matches) <= maximum,
            expected={"selector": selector, "max": maximum},
            actual=len(matches),
            severity=_severity(spec),
        )

    for index, spec in enumerate(final_expectation.get("active_counts", []), start=1):
        selector = _selector(spec)
        minimum = int(spec.get("min", 0))
        maximum = int(spec.get("max", 2**31 - 1))
        count = len(_matching_records(active, selector))
        record(
            assertion=f"final.active_count_{index}",
            category="lifecycle",
            passed=minimum <= count <= maximum,
            expected={"selector": selector, "min": minimum, "max": maximum},
            actual=count,
            severity=_severity(spec),
        )

    for index, spec in enumerate(
        final_expectation.get("protected_confirmed", []), start=1
    ):
        selector = _selector(spec)
        selector.setdefault("status", "confirmed")
        passed = bool(_matching_records(all_memories, selector))
        metrics["confirmed_overwrite_violation_count"] += int(not passed)
        record(
            assertion=f"final.protected_confirmed_{index}",
            category="lifecycle",
            passed=passed,
            expected=selector,
            actual=_memory_identities(all_memories),
            severity=_severity(spec),
        )

    context_expectation = final_expectation.get("context", {})
    if isinstance(context_expectation, dict):
        context = final.get("current_context", {})
        current_records = _current_context_records(context)
        for index, spec in enumerate(
            context_expectation.get("expected_current", []), start=1
        ):
            selector = _selector(spec)
            record(
                assertion=f"final.context.expected_current_{index}",
                category="context",
                passed=bool(_matching_records(current_records, selector)),
                expected=selector,
                actual=_memory_identities(current_records),
                severity=_severity(spec),
            )
        for index, spec in enumerate(
            context_expectation.get("forbidden_current", []), start=1
        ):
            selector = _selector(spec)
            matches = _matching_records(current_records, selector)
            record(
                assertion=f"final.context.forbidden_current_{index}",
                category="context",
                passed=not matches,
                expected={"selector": selector, "count": 0},
                actual=_memory_identities(matches),
                severity=_severity(spec),
            )
        for index, spec in enumerate(context_expectation.get("fields", []), start=1):
            path = str(spec.get("path") or "")
            expected_value = spec.get("allowed", spec.get("expected", _MISSING))
            actual_value = _get_path(context, path)
            record(
                assertion=f"final.context.field_{index}.{path}",
                category="context",
                passed=(
                    expected_value is not _MISSING
                    and _value_matches(actual_value, expected_value)
                ),
                expected=expected_value if expected_value is not _MISSING else None,
                actual=actual_value,
                severity=_severity(spec),
            )

    semantic_status = "failed" if failures else "warning" if warnings else "passed"
    return {
        "semantic_status": semantic_status,
        "semantic_assertions": assertions,
        "semantic_failures": failures,
        "semantic_warnings": warnings,
        "semantic_metrics": metrics,
    }


_MISSING = object()


def _selector(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise ValueError("semantic assertion entries must be objects")
    selector = spec.get("selector", {})
    if not isinstance(selector, dict):
        raise ValueError("semantic assertion selector must be an object")
    return dict(selector)


def _severity(spec: dict[str, Any]) -> str:
    return str(spec.get("severity", "failure"))


def _matching_records(
    records: Any,
    selector: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(records, list):
        return []
    return [
        record
        for record in records
        if isinstance(record, dict) and _record_matches(record, selector)
    ]


def _record_matches(record: dict[str, Any], selector: dict[str, Any]) -> bool:
    for path, expected in selector.items():
        if path == "$any":
            if not isinstance(expected, list) or not expected:
                return False
            if not any(
                isinstance(alternative, dict)
                and _record_matches(record, alternative)
                for alternative in expected
            ):
                return False
            continue
        if not _value_matches(_get_path(record, path), expected):
            return False
    return True


def _value_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return actual in expected
    return actual == expected


def _get_path(record: Any, path: str) -> Any:
    current = record
    for part in path.split(".") if path else ():
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _candidate_identities(records: Any) -> list[dict[str, Any]]:
    return [_identity(record) for record in records if isinstance(record, dict)]


def _memory_identities(records: Any) -> list[dict[str, Any]]:
    return [_identity(record) for record in records if isinstance(record, dict)]


def _identity(record: dict[str, Any]) -> dict[str, Any]:
    return {
        field: record.get(field)
        for field in (
            "id",
            "kind",
            "canonical_predicate",
            "custom_predicate",
            "state_dimension",
            "state_value",
            "perspective",
            "status",
            "claim_relation",
        )
        if field in record
    }


def _current_context_records(context: Any) -> list[dict[str, Any]]:
    if not isinstance(context, dict):
        return []
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for field in (
        "active_context",
        "current_state",
        "interaction_patterns",
        "confirmed_current_state",
        "confirmed_long_term",
        "uncertain_items",
        "conflicted_items",
        "remembered_items",
    ):
        values = context.get(field, [])
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            identity = str(value.get("id") or json.dumps(value, sort_keys=True))
            if identity in seen:
                continue
            seen.add(identity)
            records.append(value)
    return records


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
    execution_pass_count = sum(
        case.get("execution_status") == "passed" for case in cases
    )
    execution_fail_count = sum(
        case.get("execution_status") == "failed" for case in cases
    )
    summary: dict[str, Any] = {
        "case_count": len(cases),
        "execution_pass_count": execution_pass_count,
        "execution_fail_count": execution_fail_count,
        "execution_interrupted_count": sum(
            case.get("execution_status") == "interrupted" for case in cases
        ),
        "completed_case_count": execution_pass_count,
        "failed_case_count": execution_fail_count,
        "semantic_pass_count": sum(
            case.get("semantic_status") == "passed" for case in cases
        ),
        "semantic_warning_count": sum(
            case.get("semantic_status") == "warning" for case in cases
        ),
        "semantic_fail_count": sum(
            case.get("semantic_status") == "failed" for case in cases
        ),
        "semantic_not_evaluated_count": sum(
            case.get("semantic_status") == "not_evaluated" for case in cases
        ),
        "semantic_assertion_count": sum(
            len(case.get("semantic_assertions", [])) for case in cases
        ),
        "semantic_failure_assertion_count": sum(
            len(case.get("semantic_failures", [])) for case in cases
        ),
        "semantic_warning_assertion_count": sum(
            len(case.get("semantic_warnings", [])) for case in cases
        ),
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
        "p50_latency_ms": 0.0,
        "p95_latency_ms": 0.0,
        "gate_match_rate": 0.0,
        "canonical_match_rate": 0.0,
        "perspective_match_rate": 0.0,
        "relation_match_rate": 0.0,
        "lifecycle_match_rate": 0.0,
        "context_match_rate": 0.0,
        "stale_active_memory_count": 0,
        "duplicate_active_memory_count": 0,
        "confirmed_overwrite_violation_count": 0,
    }
    latencies: list[float] = []
    semantic_counts = {
        key: 0
        for category in _SEMANTIC_CATEGORIES
        for key in (f"{category}_expected", f"{category}_matched")
    }
    for case in cases:
        case_semantic_metrics = case.get("semantic_metrics", {})
        for key in semantic_counts:
            semantic_counts[key] += int(case_semantic_metrics.get(key, 0) or 0)
        for key in (
            "stale_active_memory_count",
            "duplicate_active_memory_count",
            "confirmed_overwrite_violation_count",
        ):
            summary[key] += int(case_semantic_metrics.get(key, 0) or 0)
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
        summary["p50_latency_ms"] = summary["p50_flash_latency_ms"]
        summary["p95_latency_ms"] = summary["p95_flash_latency_ms"]
    for category in _SEMANTIC_CATEGORIES:
        summary[f"{category}_assertion_count"] = semantic_counts[
            f"{category}_expected"
        ]
        summary[f"{category}_match_rate"] = _ratio(
            semantic_counts[f"{category}_matched"],
            semantic_counts[f"{category}_expected"],
        )
    return summary


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return round(ordered[index], 2)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


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
                expectation_path=args.expectations,
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
    print(
        "Semantic results: "
        f"{summary['semantic_pass_count']} passed, "
        f"{summary['semantic_warning_count']} warning, "
        f"{summary['semantic_fail_count']} failed, "
        f"{summary['semantic_not_evaluated_count']} not evaluated."
    )
    print(f"Report: {output_path}")
    if args.fail_on_error and summary["failed_case_count"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

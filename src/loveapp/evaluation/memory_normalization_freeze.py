"""Final Normalization V1 freeze closeout helpers.

The closeout smoke intentionally exercises the production LLM-memory ingress
with deterministic, in-process model responses.  It uses the real parser,
normalizer, admission service, and in-memory Store, while never making a
network request or changing the production code path's policy decisions.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from loveapp.adapters.memory.in_memory import InMemoryMemoryStore
from loveapp.adapters.memory.openai_compatible import (
    OpenAICompatibleMemoryExtractor,
    TieredMemoryExtractor,
)
from loveapp.application.memory import MemoryService
from loveapp.application.memory_repair import MemoryResponseError, parse_memory_response
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.memory import MemoryItem
from loveapp.domain.memory_normalization import (
    NormalizationContractError,
    normalize_memory_candidate_contract,
    validate_normalized_memory_candidate,
)
from loveapp.domain.memory_write import MemoryWriteBatch

REFERENCE_TIME = datetime(2026, 9, 2, 10, tzinfo=UTC)


class ProductionSmokeExpected(BaseModel):
    model_config = ConfigDict(extra="allow")

    normalization_mode: str | None = None
    canonical_predicate: str | None = None
    state_dimension: str | None = None
    state_value: str | None = None
    admission_reached: bool = True


class ProductionSmokeCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=80)
    source_case_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=2000)
    response: dict[str, Any]
    expected: ProductionSmokeExpected = Field(default_factory=ProductionSmokeExpected)


class _StaticCompletions:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    async def create(self, **_kwargs: object) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=self.content),
                )
            ],
            usage=None,
        )


class _StaticClient:
    def __init__(self, content: str) -> None:
        self.chat = SimpleNamespace(completions=_StaticCompletions(content))

    async def close(self) -> None:
        return None


class _RecordingMemoryStore(InMemoryMemoryStore):
    """Observe commit attempts without changing the MemoryStore contract."""

    def __init__(self) -> None:
        super().__init__(clock=lambda: REFERENCE_TIME)
        self.commit_attempted = False
        self.last_batch: MemoryWriteBatch | None = None

    async def commit_memory_batch(
        self,
        *,
        user_id: str,
        relationship_id: str,
        batch: MemoryWriteBatch,
    ):
        self.commit_attempted = True
        self.last_batch = batch.model_copy(deep=True)
        return await super().commit_memory_batch(
            user_id=user_id,
            relationship_id=relationship_id,
            batch=batch,
        )


def load_production_smoke_cases(path: Path) -> list[ProductionSmokeCase]:
    cases: list[ProductionSmokeCase] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = ProductionSmokeCase.model_validate_json(line)
        except Exception as exc:
            raise ValueError(f"invalid production smoke case on line {line_number}: {exc}") from exc
        if case.case_id in seen:
            raise ValueError(f"duplicate production smoke case_id: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError("production smoke dataset is empty")
    return cases


async def evaluate_memory_normalization_production_smoke(
    dataset_path: Path,
    *,
    case_id: str | None = None,
    reference_time: datetime = REFERENCE_TIME,
) -> dict[str, Any]:
    """Run the real extraction-to-admission path with deterministic responses."""

    cases = load_production_smoke_cases(dataset_path)
    selected = [case for case in cases if case_id is None or case.case_id == case_id]
    if not selected:
        raise ValueError(f"no production smoke cases matched case_id={case_id!r}")
    rows: list[dict[str, Any]] = []
    for case in selected:
        rows.append(await _evaluate_case(case, reference_time=reference_time))
    passed = sum(bool(row["passed"]) for row in rows)
    return {
        "evaluation": "memory_normalization_production_smoke",
        "generated_at": datetime.now().astimezone().isoformat(),
        "dataset": str(dataset_path),
        "case_filter": case_id,
        "case_count": len(rows),
        "passed_case_count": passed,
        "model_calls_permitted": False,
        "store_mutation_permitted": False,
        "isolated_store_mutation_permitted": True,
        "model_mode": "deterministic in-process OpenAI-compatible response; no network",
        "production_path": (
            "MemoryGate -> TieredMemoryExtractor(raw) -> parse/generic validation -> "
            "AtomicClaim.to_candidate -> contract Normalizer -> canonical validator -> "
            "Admission -> Relation/Lifecycle -> MemoryWriteBatch -> InMemoryStore"
        ),
        "metrics": _aggregate_metrics(rows),
        "cases": rows,
        "status": "PASS" if passed == len(rows) else "NEEDS_REVIEW",
    }


async def _evaluate_case(
    case: ProductionSmokeCase,
    *,
    reference_time: datetime,
) -> dict[str, Any]:
    response_text = json.dumps(case.response, ensure_ascii=False)
    generic = _generic_diagnostic(response_text, case.text)
    normalizer_inputs: list[dict[str, Any]] = []
    normalizer_outputs: list[dict[str, Any]] = []
    canonical_results: list[dict[str, Any]] = []
    normalizer_errors: list[str] = []
    parsed_claims: list[Any] = []
    if generic["status"] != "reject":
        try:
            parsed = parse_memory_response(
                response_text,
                source_text=case.text,
                validation_mode="raw",
            )
            parsed_claims = parsed.extraction.claims
            for claim in parsed_claims:
                candidate = claim.to_candidate()
                normalizer_inputs.append(candidate.model_dump(mode="json"))
                try:
                    normalized = normalize_memory_candidate_contract(
                        candidate,
                        reference_time,
                        allow_legacy_open_world=True,
                    )
                    validate_normalized_memory_candidate(
                        normalized,
                        allow_legacy_open_world=True,
                    )
                except NormalizationContractError as exc:
                    normalizer_errors.append(str(exc))
                    canonical_results.append(
                        {"status": "reject", "error": str(exc), "diagnostics": [exc.code]}
                    )
                except Exception as exc:  # pragma: no cover - defensive diagnostic path
                    normalizer_errors.append(f"{type(exc).__name__}: {exc}")
                    canonical_results.append(
                        {
                            "status": "error",
                            "error": f"{type(exc).__name__}: {exc}",
                            "diagnostics": [],
                        }
                    )
                else:
                    normalizer_outputs.append(normalized.model_dump(mode="json"))
                    canonical_results.append(
                        {"status": "accept", "error": None, "diagnostics": []}
                    )
        except MemoryResponseError as exc:
            generic["status"] = "reject"
            generic["error"] = str(exc)
            generic["error_category"] = exc.category
        except Exception as exc:  # pragma: no cover - defensive diagnostic path
            generic["status"] = "error"
            generic["error"] = f"{type(exc).__name__}: {exc}"

    store = _RecordingMemoryStore()
    client = _StaticClient(response_text)
    extractor = OpenAICompatibleMemoryExtractor(
        api_key=SecretStr("freeze-smoke-key"),
        base_url="https://freeze-smoke.invalid",
        model="freeze-smoke-flash",
        max_retries=0,
        validation_mode="raw",
    )
    extractor._client = client
    tiered = TieredMemoryExtractor(extractor, strong=None)
    service = MemoryService(store, tiered, clock=lambda: reference_time)
    trace = ExecutionTrace()
    before = await store.list_memories(
        user_id=f"smoke-{case.case_id}",
        relationship_id=case.case_id,
    )
    service_error: str | None = None
    result = None
    try:
        result = await service.remember_text(
            user_id=f"smoke-{case.case_id}",
            relationship_id=case.case_id,
            conversation_id=f"conversation-{case.case_id}",
            text=case.text,
            trace=trace,
        )
    except Exception as exc:  # retain stage evidence in the report
        service_error = f"{type(exc).__name__}: {exc}"
    finally:
        await service.aclose()
        await tiered.aclose()
    after_items = await store.list_memories(
        user_id=f"smoke-{case.case_id}",
        relationship_id=case.case_id,
        limit=100,
    )
    governance = [
        record
        for record in trace.snapshot()
        if record.name == "memory_candidate_governance"
    ]
    gate = (
        result.gate_decision.model_dump(mode="json")
        if result and result.gate_decision
        else None
    )
    admission_reached = bool(governance)
    if service_error:
        drop_stage = _stage_for_error(service_error, gate)
        drop_reason = service_error
    elif gate is not None and not gate["should_extract"]:
        drop_stage = "Gate"
        drop_reason = gate.get("reason")
    elif not parsed_claims:
        drop_stage = "Extraction"
        drop_reason = generic.get("error") or "no parsed claims"
    elif normalizer_errors:
        drop_stage = "Normalization"
        drop_reason = "; ".join(normalizer_errors)
    elif not admission_reached:
        drop_stage = "Admission"
        drop_reason = "No candidate governance span was recorded."
    else:
        drop_stage = None
        drop_reason = None
    expected = case.expected
    checks = {
        "raw_claim_present": bool(parsed_claims),
        "generic_validation": generic["status"] == "accept",
        "normalization_expected": (
            not expected.normalization_mode
            or any(
                _normalization_mode(output) == expected.normalization_mode
                for output in normalizer_outputs
            )
        ),
        "canonical_expected": (
            expected.canonical_predicate is None
            or any(
                output.get("canonical_predicate") == expected.canonical_predicate
                for output in normalizer_outputs
            )
        ),
        "state_expected": (
            expected.state_dimension is None
            or any(
                output.get("state_dimension") == expected.state_dimension
                and output.get("state_value") == expected.state_value
                for output in normalizer_outputs
            )
        ),
        "admission_reached": admission_reached == expected.admission_reached,
        "store_write_attempted": store.commit_attempted,
    }
    passed = all(checks.values()) and service_error is None
    return {
        "case_id": case.case_id,
        "source_case_id": case.source_case_id,
        "text": case.text,
        "raw_claim_present": bool(parsed_claims),
        "generic_validation_result": generic,
        "normalizer_input": normalizer_inputs,
        "normalizer_output": normalizer_outputs,
        "canonical_validation_result": canonical_results,
        "admission_reached": admission_reached,
        "admission_records": [record.details for record in governance],
        "store_write_attempted": store.commit_attempted,
        "final_retention_status": [
            {
                "id": item.id,
                "status": item.status.value,
                "predicate_type": item.predicate_type.value,
                "canonical_predicate": item.canonical_predicate,
                "custom_predicate": item.custom_predicate,
                "state_dimension": item.state_dimension,
                "state_value": item.state_value,
                "admission_decision": (
                    item.admission_decision.value if item.admission_decision is not None else None
                ),
            }
            for item in after_items
        ],
        "db_before": [_serialize_memory(item) for item in before],
        "db_after": [_serialize_memory(item) for item in after_items],
        "gate": gate,
        "trace": [record.model_dump(mode="json") for record in trace.snapshot()],
        "drop_stage": drop_stage,
        "drop_reason": drop_reason,
        "service_error": service_error,
        "checks": checks,
        "passed": passed,
    }


def _generic_diagnostic(response_text: str, source_text: str) -> dict[str, Any]:
    try:
        parsed = parse_memory_response(
            response_text,
            source_text=source_text,
            validation_mode="raw",
        )
    except MemoryResponseError as exc:
        return {
            "status": "reject",
            "claim_count": 0,
            "invalid_claim_count": None,
            "repair_steps": exc.repair_steps,
            "error_category": exc.category,
            "error": str(exc),
        }
    status = "accept" if parsed.extraction.claims else "reject"
    if parsed.invalid_claim_count and parsed.extraction.claims:
        status = "partial"
    return {
        "status": status,
        "claim_count": len(parsed.extraction.claims),
        "invalid_claim_count": parsed.invalid_claim_count,
        "discarded_claim_count": parsed.discarded_claim_count,
        "repair_steps": parsed.repair_steps,
        "error_category": None,
        "error": None,
    }


def _normalization_mode(output: dict[str, Any]) -> str:
    if output.get("predicate_type") == "canonical":
        return "canonical"
    if output.get("predicate_type") == "custom":
        return "custom"
    return "reject"


def _stage_for_error(error: str, gate: dict[str, Any] | None) -> str:
    if gate is not None and not gate.get("should_extract", True):
        return "Gate"
    if "normaliz" in error.casefold() or "predicate" in error.casefold():
        return "Normalization"
    if "persist" in error.casefold() or "store" in error.casefold():
        return "Store"
    return "Extraction"


def _serialize_memory(item: MemoryItem) -> dict[str, Any]:
    return item.model_dump(mode="json")


def _aggregate_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    return {
        "raw_claim_present_count": sum(row["raw_claim_present"] for row in rows),
        "generic_validation_acceptance_rate": _ratio(
            sum(row["generic_validation_result"]["status"] == "accept" for row in rows), count
        ),
        "normalization_success_rate": _ratio(
            sum(bool(row["normalizer_output"]) for row in rows), count
        ),
        "canonical_validation_acceptance_rate": _ratio(
            sum(
                any(item["status"] == "accept" for item in row["canonical_validation_result"])
                for row in rows
            ),
            count,
        ),
        "admission_reached_rate": _ratio(sum(row["admission_reached"] for row in rows), count),
        "store_write_attempt_rate": _ratio(
            sum(row["store_write_attempted"] for row in rows), count
        ),
        "passed_case_count": sum(row["passed"] for row in rows),
    }


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def render_production_smoke_report(report: dict[str, Any]) -> str:
    lines = [
        "# Memory Normalization Production-Path Smoke",
        "",
        f"Generated: `{report['generated_at']}`  ",
        f"Dataset: `{report['dataset']}`  ",
        "Model calls permitted: `False` (deterministic in-process OpenAI-compatible client)  ",
        "Store mutation permitted: `False` (isolated InMemoryMemoryStore only)",
        "",
        "## Metrics",
        "",
        "| Metric | Result |",
        "|---|---:|",
    ]
    for name, value in report["metrics"].items():
        lines.append(f"| {name} | {value} |")
    lines.extend(
        [
            "",
            "## Case Results",
            "",
            "| Case | Raw | Generic | Normalizer | Canonical | Admission | Store | Drop | Result |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in report["cases"]:
        norm = ", ".join(_normalization_mode(item) for item in row["normalizer_output"]) or "-"
        canonical = ", ".join(item["status"] for item in row["canonical_validation_result"]) or "-"
        lines.append(
            f"| {row['case_id']} | {row['raw_claim_present']} | "
            f"{row['generic_validation_result']['status']} | {norm} | {canonical} | "
            f"{row['admission_reached']} | {row['store_write_attempted']} | "
            f"{row['drop_stage'] or '-'} | {row['passed']} |"
        )
    lines.extend(["", "## Pressure Case Details", ""])
    for row in report["cases"]:
        lines.extend(
            [
                f"### {row['case_id']} ({row['source_case_id']})",
                "",
                f"- Text: `{row['text']}`",
                f"- Admission reached: `{row['admission_reached']}`",
                f"- Store write attempted: `{row['store_write_attempted']}`",
                "- Final retention: "
                f"`{json.dumps(row['final_retention_status'], ensure_ascii=False)}`",
                f"- Drop: `{row['drop_stage'] or 'none'}` / `{row['drop_reason'] or 'none'}`",
                "",
            ]
        )
    lines.extend([f"Smoke status: `{report['status']}`", ""])
    return "\n".join(lines)


def run_production_smoke(
    dataset_path: Path,
    *,
    case_id: str | None = None,
) -> dict[str, Any]:
    return asyncio.run(
        evaluate_memory_normalization_production_smoke(dataset_path, case_id=case_id)
    )

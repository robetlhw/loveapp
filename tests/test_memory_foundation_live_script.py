import json
import runpy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from loveapp.core.config import Settings
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    EvidenceExplicitness,
    MemoryAttemptStatus,
    MemoryExtractionAttempt,
    MemoryKind,
    TimeKind,
)

_LIVE_SCRIPT = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts" / "evaluate_memory_foundation_live.py")
)
evaluate_memory_foundation_live = _LIVE_SCRIPT["evaluate_memory_foundation_live"]
default_output_path = _LIVE_SCRIPT["default_output_path"]


class RecordingFlashExtractor:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    async def extract(
        self,
        text: str,
        *,
        attempt_callback=None,
        **kwargs: object,
    ) -> AtomicExtraction:
        del kwargs
        self.inputs.append(text)
        extraction = AtomicExtraction(claims=[_preference_claim(text)])
        if attempt_callback is not None:
            attempt_callback(
                MemoryExtractionAttempt(
                    attempt=1,
                    status=MemoryAttemptStatus.COMPLETED,
                    duration_ms=12.5,
                    model="fake-flash-model",
                    tier="flash",
                    prompt_tokens=11,
                    completion_tokens=7,
                    total_tokens=18,
                    claim_count=1,
                    original_claim_count=1,
                    repaired_claim_count=0,
                    discarded_claim_count=0,
                    invalid_claim_count=0,
                    repair_status="not_needed",
                )
            )
        return extraction


class FailingFlashExtractor:
    async def extract(
        self,
        text: str,
        *,
        attempt_callback=None,
        **kwargs: object,
    ) -> AtomicExtraction:
        del text, kwargs
        if attempt_callback is not None:
            attempt_callback(
                MemoryExtractionAttempt(
                    attempt=1,
                    status=MemoryAttemptStatus.FAILED,
                    duration_ms=8.25,
                    model="fake-flash-model",
                    tier="flash",
                    failure_category="schema_validation",
                    error="invalid extraction response",
                )
            )
        raise ValueError("invalid extraction response")


def test_default_output_path_is_timestamped_under_local_eval_directory() -> None:
    path = default_output_path(now=datetime(2026, 8, 29, 9, 8, 7, tzinfo=UTC))

    assert path == Path(".data/evals/memory_foundation_live_20260829_090807_000000.json")


async def test_case_filter_repeat_isolates_scopes_and_writes_redacted_report(
    tmp_path: Path,
) -> None:
    dataset = _write_dataset(
        tmp_path,
        [
            _case("LIVE-001", "\u8bb0\u4e00\u4e0b: she likes ramen."),
            _case(
                "LIVE-002",
                "\u8bb0\u4e00\u4e0b: she likes sushi.",
                scripted_marker="must-not-be-used-or-reported",
            ),
        ],
    )
    output = tmp_path / "reports" / "live.json"
    extractor = RecordingFlashExtractor()
    settings = _live_settings(api_key="top-secret-live-key")

    report = await evaluate_memory_foundation_live(
        dataset,
        output,
        settings=settings,
        case_ids=("LIVE-002",),
        repeat=2,
        extractor=extractor,
    )

    persisted_text = output.read_text(encoding="utf-8")
    persisted = json.loads(persisted_text)
    assert persisted == report
    assert report["status"] == "completed"
    assert report["selected_case_ids"] == ["LIVE-002"]
    assert report["repeat"] == 2
    assert extractor.inputs == [
        "\u8bb0\u4e00\u4e0b: she likes sushi.",
        "\u8bb0\u4e00\u4e0b: she likes sushi.",
    ]

    assert len(report["cases"]) == 2
    scope_tuples = {
        (
            case["scope"]["user_id"],
            case["scope"]["relationship_id"],
            case["scope"]["conversation_id"],
        )
        for case in report["cases"]
    }
    assert len(scope_tuples) == 2
    assert {case["repeat"] for case in report["cases"]} == {1, 2}
    assert all(len(case["final"]["active_memories"]) == 1 for case in report["cases"])
    memory_ids = {
        case["final"]["active_memories"][0]["id"] for case in report["cases"]
    }
    assert len(memory_ids) == 2

    configuration = report["configuration"]
    assert configuration["memory_backend"] == "memory"
    assert configuration["flash_only"] is True
    assert configuration["strong_enabled"] is False
    assert configuration["verifier_enabled"] is False
    assert configuration["scripted_claims_used"] is False
    assert configuration["flash_model"] == "fake-flash-model"
    assert "top-secret-live-key" not in persisted_text
    assert "must-not-be-used-or-reported" not in persisted_text

    summary = report["summary"]
    assert summary["case_count"] == 2
    assert summary["turn_count"] == 2
    assert summary["flash_call_count"] == 2
    assert summary["strong_call_count"] == 0
    assert summary["completed_attempt_count"] == 2
    assert summary["failed_attempt_count"] == 0
    assert summary["total_tokens"] == 36


async def test_failed_extraction_attempt_is_preserved_in_local_report(
    tmp_path: Path,
) -> None:
    dataset = _write_dataset(
        tmp_path,
        [_case("LIVE-FAIL", "\u8bb0\u4e00\u4e0b: she likes sushi.")],
    )
    output = tmp_path / "nested" / "partial.json"

    report = await evaluate_memory_foundation_live(
        dataset,
        output,
        settings=_live_settings(),
        extractor=FailingFlashExtractor(),
    )

    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted == report
    assert report["status"] == "partial"
    assert report["summary"]["failed_case_count"] == 1
    assert report["summary"]["extraction_error_turn_count"] == 1
    assert report["summary"]["flash_call_count"] == 1
    assert report["summary"]["failed_attempt_count"] == 1
    assert report["summary"]["schema_validation_failure_count"] == 1

    case = report["cases"][0]
    assert case["status"] == "failed"
    assert case["turns"][0]["status"] == "failed"
    assert case["turns"][0]["extraction_error"] == "invalid extraction response"
    attempt = case["turns"][0]["extraction_run"]["attempts"][0]
    assert attempt["status"] == "failed"
    assert attempt["tier"] == "flash"
    assert attempt["failure_category"] == "schema_validation"
    assert case["final"]["active_memories"] == []


async def test_invalid_repeat_fails_before_creating_a_report(tmp_path: Path) -> None:
    dataset = _write_dataset(
        tmp_path,
        [_case("LIVE-001", "\u8bb0\u4e00\u4e0b: she likes sushi.")],
    )
    output = tmp_path / "should-not-exist.json"

    with pytest.raises(ValueError, match="repeat must be at least 1"):
        await evaluate_memory_foundation_live(
            dataset,
            output,
            settings=_live_settings(),
            repeat=0,
            extractor=RecordingFlashExtractor(),
        )

    assert not output.exists()


def _live_settings(*, api_key: str = "fake-api-key") -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="openai-compatible",
        llm_api_key=SecretStr(api_key),
        llm_base_url="https://models.example.test/v1",
        memory_backend="sqlite",
        memory_extraction_provider="disabled",
        memory_extraction_model="fake-flash-model",
    )


def _write_dataset(tmp_path: Path, cases: list[dict[str, object]]) -> Path:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases),
        encoding="utf-8",
    )
    return path


def _case(
    case_id: str,
    text: str,
    *,
    scripted_marker: str | None = None,
) -> dict[str, object]:
    turn: dict[str, object] = {"input": text, "expect": {"gate_should_extract": True}}
    if scripted_marker is not None:
        turn["scripted_claims"] = [{"summary": scripted_marker}]
    return {
        "id": case_id,
        "category": "live_test",
        "description": "Synthetic no-network live-runner fixture.",
        "reference_time": "2026-08-29T12:00:00+08:00",
        "turns": [turn],
        "expected_final": {},
    }


def _preference_claim(evidence: str) -> AtomicClaim:
    return AtomicClaim(
        claim_id="sushi-preference",
        kind=MemoryKind.PREFERENCE,
        subject="partner",
        predicate="likes_cuisine",
        summary="Partner likes sushi.",
        evidence_spans=[evidence],
        time_kind=TimeKind.TIMELESS,
        confidence=0.99,
        explicitness=EvidenceExplicitness.EXPLICIT,
        payload={"preference": "sushi", "preference_type": "cuisine"},
    )

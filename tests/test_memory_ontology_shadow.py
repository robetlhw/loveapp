import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from loveapp.domain.memory import (
    AtomicExtraction,
    MemoryAttemptStatus,
    MemoryExtractionAttempt,
)
from loveapp.evaluation.memory_ontology_shadow import (
    evaluate_memory_ontology_shadow,
    render_ontology_shadow_summary,
    write_ontology_shadow_artifacts,
)


class _Extractor:
    def __init__(self, extraction: AtomicExtraction, *, strategy: str) -> None:
        self.extraction = extraction
        self.strategy = strategy
        self.calls = 0

    async def extract(self, text: str, *, attempt_callback, **_: object) -> AtomicExtraction:
        self.calls += 1
        attempt_callback(
            MemoryExtractionAttempt(
                attempt=1,
                status=MemoryAttemptStatus.COMPLETED,
                duration_ms=1,
                model=self.strategy,
                tier="flash",
                claim_count=len(self.extraction.claims),
                extraction_strategy=self.strategy,
                stage="single" if self.strategy == "single_stage" else "detailed",
            )
        )
        return self.extraction


def _dataset(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": "ontology_sanity_v1",
                "case_id": "sf_001",
                "group": "stable_fact",
                "text": "x",
                "expected_claims": [{"memory_kind": "stable_fact"}],
                "forbidden": [],
                "annotation": {
                    "label_source": "test",
                    "review_status": "draft",
                    "policy_version": "test",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.asyncio
async def test_shadow_runs_same_gold_without_store_and_reports_strategy(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path / "gold.jsonl")
    extraction = AtomicExtraction.model_validate(
        {
            "claims": [],
            "should_extract": False,
            "gate_reason": "NO_MEMORY",
        }
    )
    single = _Extractor(extraction, strategy="single_stage")
    two = _Extractor(extraction, strategy="two_stage")
    report = await evaluate_memory_ontology_shadow(
        dataset,
        single_stage_extractor=single,
        two_stage_extractor=two,
        reference_time=datetime(2026, 9, 10, tzinfo=UTC),
        case_id="sf_001",
    )

    assert report["store_mutation_permitted"] is False
    assert report["dataset_sha256"]
    assert report["comparison"]["cases"][0]["two_stage"]["stage_used"] == "two_stage"
    assert report["comparison"]["cases"][0]["two_stage"]["fallback_used"] is False
    assert report["comparison"]["execution"]["native_completion_rate"] == 1.0
    assert report["comparison"]["execution"]["fallback_reasons"] == {}
    assert report["comparison"]["two_stage_native_metrics"]["case_count"] == 1
    assert report["comparison"]["cost"]["two_stage_native_only"] == {
        "case_count": 1,
        "call_count": 1,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "avg_calls_per_case": 1.0,
        "avg_tokens_per_case": 0.0,
    }
    assert single.calls == two.calls == 1


def test_shadow_artifacts_are_machine_and_human_readable(tmp_path: Path) -> None:
    report = {
        "dataset": "gold.jsonl",
        "dataset_sha256": "sha",
        "store_mutation_permitted": False,
        "comparison": {
            "metrics": {},
            "cases": [],
            "single_stage_telemetry": {"call_count": 0, "total_tokens": 0},
            "two_stage_telemetry": {"call_count": 0, "total_tokens": 0},
            "telemetry_delta": {"call_count": 0},
            "execution": {
                "case_count": 0,
                "native_completion_count": 0,
                "native_completion_rate": None,
                "fallback_count": 0,
                "fallback_rate": None,
                "fallback_reasons": {},
            },
            "two_stage_native_metrics": {},
            "cost": {},
        },
    }
    results, summary = write_ontology_shadow_artifacts(report, tmp_path)
    assert json.loads(results.read_text(encoding="utf-8"))["store_mutation_permitted"] is False
    assert "SingleStage" in summary.read_text(encoding="utf-8")
    assert "TwoStage" in render_ontology_shadow_summary(report)

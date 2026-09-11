from __future__ import annotations

import json
from pathlib import Path

import pytest

from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    MemoryKind,
    MemorySemanticGateReason,
)
from loveapp.evaluation.memory_context_aware_behavioral_anchor import (
    _ambiguous_target_safe,
    _attach_stage1_origins,
    compare_context_aware_runs,
    evaluate_context_aware_behavioral_anchor,
    load_context_aware_cases,
    write_context_aware_artifacts,
)

DATASET = Path("evals/memory/context_aware_behavioral_anchor_v0_1.jsonl")


class _FixtureExtractor:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.last_diagnostic: dict[str, object] = {}

    async def extract(self, text: str, **kwargs: object) -> AtomicExtraction:
        self.calls.append({"text": text, **kwargs})
        self.last_diagnostic = {
            "stage1": {
                "propositions": [
                    {
                        "proposition_id": "p1",
                        "evidence_span": text,
                        "semantic_role": "new_proposition",
                        "proposition_origin": "spontaneous_disclosure",
                    }
                ]
            }
        }
        return AtomicExtraction(
            should_extract=True,
            gate_reason=MemorySemanticGateReason.STABLE_FACT,
            claims=[
                AtomicClaim(
                    claim_id="p1",
                    kind=MemoryKind.STABLE_FACT,
                    subject="user",
                    predicate="resides_in",
                    summary="User lives in Shanghai",
                    evidence_spans=[text],
                    payload={"value": "Shanghai"},
                )
            ],
        )


def test_context_aware_gold_has_70_scored_and_10_diagnostics() -> None:
    cases = load_context_aware_cases(DATASET)
    assert len(cases) == 80
    assert sum(bool(case.get("diagnostic_only")) for case in cases) == 10
    assert sum(not bool(case.get("diagnostic_only")) for case in cases) == 70


def test_stage1_origin_alignment_uses_proposition_id_before_evidence() -> None:
    observed = [
        {
            "semantic_type": "new_memory",
            "raw": {"claim_id": "p1"},
            "evidence": ["different rendering"],
            "proposition_origin": "uncertain",
        }
    ]
    aligned = _attach_stage1_origins(
        observed,
        [
            {
                "proposition_id": "p1",
                "evidence_span": "source",
                "proposition_origin": "answer_to_question",
            }
        ],
    )
    assert aligned[0]["proposition_origin"] == "answer_to_question"
    assert aligned[0]["proposition_origin_source"] == "stage1"
    assert aligned[0]["proposition_origin_matched_by"] == "proposition_id"


@pytest.mark.asyncio
async def test_evaluator_is_shadow_only_and_reports_stage1_origin(tmp_path: Path) -> None:
    extractor = _FixtureExtractor()
    dataset = tmp_path / "case.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "case_id": "T01",
                "category": "origin",
                "text": "I live in Shanghai.",
                "expected_semantics": {
                    "should_extract": True,
                    "unit_count": 1,
                    "required": [
                        {
                            "semantic_role": "new_proposition",
                            "proposition_origin": "spontaneous_disclosure",
                            "memory_kind": "stable_fact",
                            "subject": "user",
                            "values_contain": ["Shanghai"],
                        }
                    ],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    report = await evaluate_context_aware_behavioral_anchor(
        dataset,
        extractor=extractor,
        output_dir=tmp_path / "artifacts",
        require_full_suite=False,
        baseline_path=None,
    )
    row = report["cases"][0]
    assert report["passed_case_count"] == 1
    assert row["observed_semantics"][0]["proposition_origin"] == "spontaneous_disclosure"
    assert row["observed_semantics"][0]["proposition_origin_source"] == "stage1"
    assert row["primary_failure_stage"] is None
    assert report["store_mutation_permitted"] is False
    assert extractor.calls[0]["existing_memories"] == []
    assert extractor.calls[0]["conversation_history"] == []
    assert (tmp_path / "artifacts" / "summary.md").exists()


def test_ambiguous_target_is_only_safe_when_resolver_rejects_or_noops() -> None:
    expected = {"ambiguous_target": True}
    assert _ambiguous_target_safe(expected, {"status": "not_applicable"})
    assert _ambiguous_target_safe(expected, {"status": "rejected"})
    assert not _ambiguous_target_safe(expected, {"status": "resolved"})


def test_context_aware_artifacts_and_run_comparison_are_stable(tmp_path: Path) -> None:
    report = {
        "dataset": "dataset.jsonl",
        "dataset_sha256": "sha",
        "run_label": "run1",
        "case_count": 1,
        "diagnostic_case_count": 0,
        "passed_case_count": 1,
        "metrics": {"overall_anchor_pass_rate": 1.0},
        "cases": [
            {
                "case_id": "T01",
                "category": "origin",
                "diagnostic_only": False,
                "passed": True,
                "primary_failure_stage": None,
                "observed_semantics": [{"semantic_role": "new_proposition"}],
            }
        ],
    }
    write_context_aware_artifacts(report, tmp_path)
    assert (tmp_path / "summary.md").exists()
    assert (tmp_path / "raw_results.jsonl").exists()
    comparison = compare_context_aware_runs(report, report)
    assert comparison["drift_rate"] == 0.0
    assert comparison["stable_passes"] == 1

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loveapp.domain.memory import AtomicExtraction, MemorySemanticGateReason
from loveapp.evaluation.memory_behavioral_anchor import (
    _normalize_unit,
    _resolution_passes,
    compare_behavioral_anchor_baseline,
    evaluate_memory_behavioral_anchor,
    load_behavioral_anchor_cases,
    render_behavioral_anchor_summary,
    write_behavioral_anchor_artifacts,
)

DATASET = Path("evals/memory/memory_behavioral_anchor_v0_1.jsonl")


class _FixtureExtractor:
    def __init__(self, extraction: AtomicExtraction) -> None:
        self.extraction = extraction
        self.calls: list[dict[str, object]] = []

    async def extract(self, text: str, **kwargs: object) -> AtomicExtraction:
        self.calls.append({"text": text, **kwargs})
        return self.extraction


def test_behavioral_anchor_gold_is_exactly_28_cases_and_behavior_oriented() -> None:
    cases = load_behavioral_anchor_cases(DATASET)
    assert len(cases) == 28
    assert {case["case_id"] for case in cases} == {f"A{index:02d}" for index in range(1, 29)}
    assert all("expected_semantics" in case for case in cases)
    assert all(
        not any(
            forbidden in json.dumps(case["expected_semantics"], ensure_ascii=False)
            for forbidden in ("semantic_payload", "target_memory_id", "NewMemoryDraft")
        )
        for case in cases
    )


@pytest.mark.asyncio
async def test_behavioral_anchor_evaluator_is_shadow_only_and_writes_four_artifacts(
    tmp_path: Path,
) -> None:
    text = "我住在上海。"
    fixture = _FixtureExtractor(
        AtomicExtraction(
            should_extract=True,
            gate_reason=MemorySemanticGateReason.STABLE_FACT,
            claims=[
                {
                    "claim_id": "c1",
                    "kind": "stable_fact",
                    "subject": "user",
                    "predicate": "resides_in",
                    "summary": "用户住在上海",
                    "evidence_spans": [text],
                    "payload": {"value": "上海"},
                }
            ],
        )
    )
    one_case = tmp_path / "one.jsonl"
    one_case.write_text(
        json.dumps(
            {
                "schema_version": "memory_behavioral_anchor_v0_1",
                "case_id": "A01",
                "category": "foundation",
                "text": text,
                "expected_semantics": {
                    "should_extract": True,
                    "required": [
                        {
                            "semantic_role": "new_proposition",
                            "memory_kind": "stable_fact",
                            "subject": "user",
                            "values_contain": ["上海"],
                        }
                    ],
                },
                "mutation_expectation": "new_memory_path",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    report = await evaluate_memory_behavioral_anchor(
        one_case,
        output_dir=tmp_path / "artifacts",
        extractor=fixture,
        require_full_suite=False,
    )

    assert report["passed_case_count"] == 1
    assert report["store_mutation_permitted"] is False
    assert fixture.calls[0]["existing_memories"] == []
    assert fixture.calls[0]["conversation_history"] == []
    for filename in (
        "summary.md",
        "raw_results.jsonl",
        "normalized_results.jsonl",
        "failures.jsonl",
    ):
        assert (tmp_path / "artifacts" / filename).exists()
    assert "Store mutation permitted: `False`" in render_behavioral_anchor_summary(report)


def test_behavioral_anchor_artifact_writer_is_stable(tmp_path: Path) -> None:
    report = {
        "dataset": "anchor.jsonl",
        "dataset_sha256": "sha",
        "mode": "two_stage",
        "store_mutation_permitted": False,
        "case_count": 0,
        "passed_case_count": 0,
        "metrics": {"overall_anchor_pass_rate": 1.0},
        "cases": [],
    }
    summary, raw, normalized, failures = write_behavioral_anchor_artifacts(
        report,
        tmp_path,
    )
    assert {path.name for path in (summary, raw, normalized, failures)} == {
        "summary.md",
        "raw_results.jsonl",
        "normalized_results.jsonl",
        "failures.jsonl",
    }
    assert summary.read_text(encoding="utf-8").startswith("# Memory Behavioral Anchor")


def test_behavioral_anchor_subset_loader_and_enrichment_adapter_are_behavior_oriented(
    tmp_path: Path,
) -> None:
    subset = tmp_path / "subset.jsonl"
    subset.write_text(
        json.dumps({"case_id": "A01", "expected_semantics": {}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    assert len(load_behavioral_anchor_cases(subset, require_full_suite=False)) == 1

    from loveapp.domain.memory import MemoryKind
    from loveapp.domain.memory_semantic_units import AttributeNamespace, EnrichmentDraft

    row = _normalize_unit(
        EnrichmentDraft(
            unit_id="u1",
            target_kind=MemoryKind.INTERACTION_EVENT,
            target_semantic_hint={"event_type": "conflict"},
            attribute_namespace=AttributeNamespace.CANONICAL,
            attribute_name="cause",
            value="迟到",
            evidence_span="因为迟到",
        )
    )
    assert row["attributes"] == ["cause"]
    assert row["values"] == ["迟到"]
    assert row["semantic_role"] == "contextual_completion"


def test_behavioral_anchor_baseline_comparison_tracks_case_drift() -> None:
    report = {"cases": [{"case_id": "A01", "passed": False}]}
    baseline = {
        "baseline_status": "established",
        "case_results": {"A01": True, "A02": False},
    }
    comparison = compare_behavioral_anchor_baseline(report, baseline)
    assert comparison["old_pass_to_new_fail"] == ["A01"]
    assert comparison["old_fail_to_new_pass"] == []


def test_behavioral_anchor_allows_missing_draft_when_gold_marks_no_safe_target() -> None:
    passed, reason = _resolution_passes(
        {"status": "rejected", "allow_no_draft": True},
        {"status": "not_applicable", "resolutions": []},
    )
    assert passed is True
    assert reason is None

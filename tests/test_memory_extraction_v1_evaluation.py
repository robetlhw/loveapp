import json
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from loveapp.adapters.memory.openai_compatible import (
    OpenAICompatibleMemoryExtractor,
    TieredMemoryExtractor,
)
from loveapp.evaluation.memory_extraction_alignment import (
    ExtractionAlignmentPair,
    ExtractionAlignmentResult,
    _parse_alignment_result,
)
from loveapp.evaluation.memory_extraction_v1 import (
    EXPECTED_SLICE_COUNTS,
    _aggregate_metrics,
    _evaluate_layer,
    _remediation_priorities,
    evaluate_memory_extraction_v1,
    load_memory_extraction_v1_cases,
    render_memory_extraction_v1_report,
)

DATASET = Path("evals/memory/extraction_v1_70.jsonl")


class _IndexMatcher:
    model = "fixture-semantic-matcher"

    def __init__(self) -> None:
        self.call_count = 0

    async def align(
        self,
        *,
        expected_claims: list[dict[str, object]],
        actual_claims: list[dict[str, object]],
        **_: object,
    ) -> ExtractionAlignmentResult:
        self.call_count += 1
        count = min(len(expected_claims), len(actual_claims))
        return ExtractionAlignmentResult(
            matches=[
                ExtractionAlignmentPair(
                    expected_index=index,
                    actual_index=index,
                    proposition_equivalent=True,
                    semantic_match=(
                        expected_claims[index]["subject"] == actual_claims[index]["subject"]
                    ),
                    evidence_support="PASS",
                    reason="fixture index alignment",
                )
                for index in range(count)
            ],
            unmatched_expected=list(range(count, len(expected_claims))),
            unmatched_actual=list(range(count, len(actual_claims))),
        )


class _CapturingObserver:
    def __init__(self) -> None:
        self.enabled = False
        self.requested = False
        self.disabled_reason = None
        self.dataset_name = "test-dataset"
        self.experiments = {
            "flash_diagnostic": "flash-test",
            "production_cascade": "cascade-test",
        }
        self.client = None
        self.inputs: list[dict[str, object]] = []

    @contextmanager
    def case(self, _stage: str, *, inputs: dict[str, object], metadata: object):
        del metadata
        self.inputs.append(inputs)
        yield {}


class _Completions:
    def __init__(self, content: str) -> None:
        self.content = content

    async def create(self, **_: object) -> object:
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=self.content),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )


def test_extraction_v1_dataset_contract_and_distribution() -> None:
    cases = load_memory_extraction_v1_cases(DATASET)

    assert len(cases) == 70
    assert len({case.case_id for case in cases}) == 70
    assert Counter(case.slice for case in cases) == EXPECTED_SLICE_COUNTS
    assert Counter(case.length_class for case in cases) == {
        "short": 29,
        "medium": 31,
        "long": 10,
    }
    assert Counter(case.difficulty for case in cases) == {
        "easy": 19,
        "medium": 26,
        "hard": 25,
    }
    assert sum(case.contains_distractor for case in cases) == 23
    assert all(not case.expected_claims for case in cases[-6:])
    context_cases = [case for case in cases if case.slice == "context_reply"]
    assert len(context_cases) == 10
    assert all(case.pending_memory_context for case in context_cases)
    assert all(
        span in case.user_message
        for case in cases
        for claim in case.expected_claims
        for span in claim.evidence_spans
    )


@pytest.mark.asyncio
async def test_canonical_mismatch_does_not_fail_semantic_extraction() -> None:
    case = load_memory_extraction_v1_cases(DATASET)[0]
    actual = {
        "kind": "preference",
        "subject": "partner",
        "perspective": "user_reported",
        "summary": "她偏爱寿司",
        "evidence_spans": ["她最喜欢吃寿司，尤其是三文鱼"],
        "canonical_predicate": "deliberately.not.the.gold.target",
        "state_dimension": "ignored.dimension",
        "state_value": "ignored.value",
    }

    row = await _evaluate_layer(
        case,
        layer="flash_raw",
        claims=[actual],
        semantic_matcher=_IndexMatcher(),
        trace=None,
    )

    assert row["counts"]["matched_expected"] == 1
    assert "MISSED_CLAIM" not in row["errors"]
    assert "CANONICAL_COUPLING_DIAGNOSTIC" not in row["errors"]


@pytest.mark.asyncio
async def test_layer_metrics_expose_perspective_error() -> None:
    case = load_memory_extraction_v1_cases(DATASET)[22]
    actual = {
        "kind": "stable_fact",
        "subject": "partner",
        "perspective": "user_reported",
        "summary": "她在躲我",
        "evidence_spans": ["最近两个月我一直觉得她在刻意躲着我"],
    }

    row = await _evaluate_layer(
        case,
        layer="flash_raw",
        claims=[actual],
        semantic_matcher=_IndexMatcher(),
        trace=None,
    )
    metrics = _aggregate_metrics([row])

    assert row["counts"]["matched_expected"] == 1
    assert "PERSPECTIVE_ERROR" in row["errors"]
    assert metrics["perspective_accuracy"] == 0.0
    assert metrics["user_belief_perspective_accuracy"] == 0.0


@pytest.mark.asyncio
async def test_layer_attributes_many_to_one_alignment_as_over_merge() -> None:
    case = load_memory_extraction_v1_cases(DATASET)[46]
    actual = {
        "kind": "interaction_pattern",
        "subject": "partner",
        "perspective": "user_reported",
        "summary": "partner includes user in her social circle",
        "evidence_spans": [
            span
            for expected_claim in case.expected_claims
            for span in expected_claim.evidence_spans
        ],
    }
    alignment = _parse_alignment_result(
        json.dumps(
            {
                "matches": [
                    {
                        "expected_index": expected_index,
                        "actual_index": 0,
                        "proposition_equivalent": True,
                        "semantic_match": True,
                        "evidence_support": "PASS",
                        "reason": "one actual claim covers both propositions",
                    }
                    for expected_index in range(2)
                ],
                "unmatched_expected": [],
                "unmatched_actual": [],
                "over_merge_actual_indices": [],
                "over_split_expected_indices": [],
                "uncertain": False,
                "reason": "many-to-one alignment",
            }
        ),
        expected_count=2,
        actual_count=1,
    )

    class _OverMergeMatcher:
        async def align(self, **_: object) -> ExtractionAlignmentResult:
            return alignment

    row = await _evaluate_layer(
        case,
        layer="flash_post_repair",
        claims=[actual],
        semantic_matcher=_OverMergeMatcher(),
        trace=None,
    )

    assert row["alignment"]["uncertain"] is False
    assert row["alignment"]["over_merge_actual_indices"] == [0]
    assert row["counts"]["matched_expected"] == 1
    assert row["counts"]["unmatched_expected"] == 1
    assert "OVER_MERGE" in row["errors"]
    assert "MISSED_CLAIM" in row["errors"]


@pytest.mark.asyncio
async def test_evaluator_runs_three_layers_without_gate() -> None:
    content = json.dumps(
        {
            "should_extract": True,
            "gate_reason": "PREFERENCE",
            "claims": [
                {
                    "claim_id": "claim-1",
                    "kind": "preference",
                    "subject": "partner",
                    "predicate": "likes_sushi",
                    "summary": "她最喜欢寿司，尤其是三文鱼",
                    "evidence_spans": ["她最喜欢吃寿司，尤其是三文鱼"],
                    "perspective": "user_reported",
                }
            ],
            "discarded_spans": [],
        },
        ensure_ascii=False,
    )
    flash = OpenAICompatibleMemoryExtractor(
        api_key=SecretStr("test"),
        base_url="https://example.invalid",
        model="flash-test",
        max_retries=0,
    )
    flash._client = SimpleNamespace(
        chat=SimpleNamespace(completions=_Completions(content))
    )
    cascade = TieredMemoryExtractor(flash, strong=None)

    matcher = _IndexMatcher()
    observer = _CapturingObserver()
    report = await evaluate_memory_extraction_v1(
        DATASET,
        flash_extractor=flash,
        cascade_extractor=cascade,
        semantic_matcher=matcher,
        observer=observer,
        case_id="EXT-001",
        fail_on_error=True,
    )

    assert report["gate_participates_in_scoring"] is False
    assert report["case_count"] == 1
    for layer in report["layers"].values():
        assert layer["metrics"]["claim_recall"] == 1.0
    assert report["cases"][0]["cascade_attempts"]
    assert matcher.call_count == 1
    assert report["telemetry"]["flash_call_count"] == 1
    assert report["telemetry"]["semantic_matcher_call_count"] == 1
    assert observer.inputs
    assert "conversation_history" in observer.inputs[0]
    assert "existing_memories" in observer.inputs[0]
    markdown = render_memory_extraction_v1_report(report)
    assert "Flash Post-Repair" in markdown
    assert "PendingMemoryContext path" in markdown
    assert "Fail-closed semantic matcher cases: none." in markdown
    assert "NEXT_REMEDIATION_PRIORITY" in markdown

    report["manual_review_case_ids"] = ["EXT-TEST"]
    markdown = render_memory_extraction_v1_report(report)
    assert "Fail-closed semantic matcher cases: `EXT-TEST`" in markdown


def test_reference_time_is_timezone_aware() -> None:
    case = load_memory_extraction_v1_cases(DATASET)[0]
    assert case.reference_time == datetime(2026, 9, 1, 18, 0, tzinfo=case.reference_time.tzinfo)
    assert case.reference_time.astimezone(UTC).hour == 10


def test_remediation_priorities_include_core_field_accuracy() -> None:
    priorities = _remediation_priorities(
        {
            "metrics": {
                "context_reply_recall": 0.9,
                "atomization_accuracy": 0.9,
                "kind_accuracy": 1.0,
                "subject_accuracy": 0.5,
                "perspective_accuracy": 1.0,
                "user_belief_perspective_accuracy": 1.0,
                "evidence_substring_validity": 1.0,
                "evidence_semantic_support_accuracy": 1.0,
                "empty_positive_rate": 0.0,
                "negative_restraint_false_positive_rate": 0.0,
                "spurious_claim_rate": 0.0,
            }
        }
    )

    assert priorities[0] == "Claim subject attribution (gap=0.5000)"

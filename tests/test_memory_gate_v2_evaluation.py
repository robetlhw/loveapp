import json
from pathlib import Path
from typing import Any

import pytest

from loveapp.application.memory_gate import MemoryGate
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    MemoryAttemptStatus,
    MemoryExtractionAttempt,
    MemoryKind,
    MemorySemanticGateReason,
)
from loveapp.evaluation.memory_gate_v2 import (
    evaluate_memory_gate_v2,
    load_memory_gate_v2_cases,
    render_memory_gate_v2_report,
)

DATASET = Path("evals/memory/gate_v2_60.jsonl")
TRANSIENT_BELIEF_DATASET = Path(
    "evals/memory/gate_transient_belief_regression.jsonl"
)


def _claim(text: str = "她喜欢寿司") -> AtomicClaim:
    return AtomicClaim(
        claim_id="claim-1",
        kind=MemoryKind.PREFERENCE,
        subject="partner",
        predicate="likes_sushi",
        summary="对方喜欢寿司",
        evidence_spans=[text],
    )


def _case(
    case_id: str,
    text: str,
    *,
    route: str,
    should_extract: bool,
    reason: str,
    category: str = "test",
    context: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "category": category,
        "difficulty": "test",
        "conversation_context": context or [],
        "current_user": text,
        "expected": {
            "l0_route": route,
            "should_extract": should_extract,
            "gate_reason": reason,
            "context_expectation": None,
        },
        "rationale": "evaluator only",
        "extraction_hint": "evaluator only",
        "metadata": {},
    }


def _write_cases(path: Path, cases: list[dict[str, Any]]) -> Path:
    path.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n",
        encoding="utf-8",
    )
    return path


class _ScriptedExtractor:
    def __init__(self, results: dict[str, AtomicExtraction | Exception]) -> None:
        self.results = results
        self.calls: list[dict[str, Any]] = []

    async def extract(self, text: str, **kwargs: Any) -> AtomicExtraction:
        self.calls.append({"text": text, **kwargs})
        callback = kwargs.get("attempt_callback")
        if callback is not None:
            callback(
                MemoryExtractionAttempt(
                    attempt=1,
                    status=MemoryAttemptStatus.COMPLETED,
                    duration_ms=12.5,
                    model="scripted-flash",
                    tier="flash",
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    raw_model_response="RAW_RESPONSE_MUST_NOT_LEAK",
                    invalid_claim_snapshot="INVALID_CLAIM_MUST_NOT_LEAK",
                )
            )
        result = self.results[text]
        if isinstance(result, Exception):
            raise result
        return result.model_copy(deep=True)


class _RecordingGate(MemoryGate):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def evaluate(self, text: str, **kwargs: Any):
        self.calls.append({"method": "evaluate", "text": text, **kwargs})
        return super().evaluate(text, **kwargs)

    def route_v2(self, text: str, **kwargs: Any):
        self.calls.append({"method": "route_v2", "text": text, **kwargs})
        return super().route_v2(text, **kwargs)


class _ClaimInvalidExtractor:
    async def extract(self, text: str, **kwargs: Any) -> AtomicExtraction:
        callback = kwargs.get("attempt_callback")
        if callback is not None:
            callback(
                MemoryExtractionAttempt(
                    attempt=1,
                    status=MemoryAttemptStatus.COMPLETED,
                    duration_ms=10,
                    model="scripted-flash",
                    tier="flash",
                    claim_count=0,
                    original_claim_count=1,
                    invalid_claim_count=1,
                    invalid_claim_reasons="missing canonical state mapping",
                    extraction_status="claim_schema_invalid",
                )
            )
        return AtomicExtraction(
            should_extract=True,
            gate_reason=MemorySemanticGateReason.RELATIONSHIP_STATE,
            claims=[],
        )


class _StrongRecoveryExtractor:
    async def extract(self, text: str, **kwargs: Any) -> AtomicExtraction:
        callback = kwargs.get("attempt_callback")
        if callback is not None:
            callback(
                MemoryExtractionAttempt(
                    attempt=1,
                    status=MemoryAttemptStatus.COMPLETED,
                    duration_ms=10,
                    tier="flash",
                    extraction_status="empty_claims",
                )
            )
            callback(
                MemoryExtractionAttempt(
                    attempt=2,
                    status=MemoryAttemptStatus.COMPLETED,
                    duration_ms=20,
                    tier="strong",
                    extraction_status="success",
                )
            )
        return AtomicExtraction(
            should_extract=True,
            gate_reason=MemorySemanticGateReason.PREFERENCE,
            claims=[_claim(text)],
        )


def test_supplied_dataset_is_the_reviewed_60_case_corpus() -> None:
    cases = load_memory_gate_v2_cases(DATASET)

    assert len(cases) == 60
    assert sum(case["expected"]["should_extract"] for case in cases) == 44
    assert sum(bool(case["conversation_context"]) for case in cases) == 13
    route_counts: dict[str, int] = {}
    for case in cases:
        route = case["expected"]["l0_route"]
        route_counts[route] = route_counts.get(route, 0) + 1
    assert route_counts == {
        "HARD_PASS": 9,
        "SEMANTIC_REVIEW": 31,
        "HARD_DROP": 7,
        "CONTEXT_PASS": 13,
    }


def test_transient_belief_dataset_is_balanced_and_keeps_l0_in_review() -> None:
    cases = load_memory_gate_v2_cases(TRANSIENT_BELIEF_DATASET)

    assert len(cases) == 10
    assert sum(case["category"] == "transient_belief" for case in cases) == 5
    assert sum(case["category"] == "durable_belief" for case in cases) == 5
    assert all(case["expected"]["l0_route"] == "SEMANTIC_REVIEW" for case in cases)
    tb_010 = next(case for case in cases if case["id"] == "TB-010")
    assert tb_010["expected"] == {
        "l0_route": "SEMANTIC_REVIEW",
        "should_extract": True,
        "gate_reason": "USER_BELIEF",
        "context_expectation": None,
    }


async def test_transient_belief_metrics_require_matching_gate_reason() -> None:
    cases = load_memory_gate_v2_cases(TRANSIENT_BELIEF_DATASET)
    extractor = _ScriptedExtractor(
        {
            case["current_user"]: AtomicExtraction(
                should_extract=case["expected"]["should_extract"],
                gate_reason=case["expected"]["gate_reason"],
            )
            for case in cases
        }
    )

    report = await evaluate_memory_gate_v2(
        TRANSIENT_BELIEF_DATASET,
        extractor=extractor,
    )

    assert report["metrics"]["transient_belief_negative_accuracy"] == 1.0
    assert report["metrics"]["durable_belief_positive_recall"] == 1.0
    assert report["metrics"]["semantic_gate_reason_accuracy"] == 1.0
    assert all(row["semantic_gate_reason_pass"] for row in report["cases"])


async def test_transient_belief_wrong_negative_reason_is_not_counted_as_correct(
    tmp_path: Path,
) -> None:
    text = "她刚刚语气有点冷，我一下觉得她是不是不高兴。"
    path = _write_cases(
        tmp_path / "wrong-transient-reason.jsonl",
        [
            _case(
                "TB-REASON-001",
                text,
                route="SEMANTIC_REVIEW",
                should_extract=False,
                reason="TRANSIENT",
                category="transient_belief",
            )
        ],
    )
    extractor = _ScriptedExtractor(
        {
            text: AtomicExtraction(
                should_extract=False,
                gate_reason=MemorySemanticGateReason.NO_MEMORY,
            )
        }
    )

    report = await evaluate_memory_gate_v2(path, extractor=extractor)

    assert report["cases"][0]["semantic_gate_pass"] is True
    assert report["cases"][0]["semantic_gate_reason_pass"] is False
    assert report["metrics"]["transient_belief_negative_accuracy"] == 0.0
    assert report["metrics"]["semantic_gate_reason_accuracy"] == 0.0


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("not-json\n", "line 1"),
        (
            "\n".join(
                json.dumps(
                    _case(
                        "duplicate",
                        "她喜欢寿司。",
                        route="HARD_PASS",
                        should_extract=True,
                        reason="PREFERENCE",
                    ),
                    ensure_ascii=False,
                )
                for _ in range(2)
            ),
            "duplicate",
        ),
    ],
)
def test_dataset_loader_fails_closed_for_invalid_jsonl(
    tmp_path: Path,
    payload: str,
    message: str,
) -> None:
    path = tmp_path / "invalid.jsonl"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_memory_gate_v2_cases(path)


async def test_full_dataset_scores_l0_and_l1_without_store_mutation() -> None:
    cases = load_memory_gate_v2_cases(DATASET)
    extractor = _ScriptedExtractor(
        {
            case["current_user"]: AtomicExtraction(
                should_extract=case["expected"]["should_extract"],
                gate_reason=case["expected"]["gate_reason"],
                claims=[],
            )
            for case in cases
            if case["expected"]["l0_route"] != "HARD_DROP"
        }
    )

    report = await evaluate_memory_gate_v2(DATASET, extractor=extractor)

    assert report["store_mutation_permitted"] is False
    assert report["label_leakage_permitted"] is False
    assert report["metrics"]["routing_accuracy"] == 1.0
    assert report["metrics"]["context_pass_recall"] == 1.0
    assert report["metrics"]["hard_drop_false_negative_count"] == 0
    assert report["hybrid"]["recall"] == 1.0
    assert report["hybrid"]["precision"] == 1.0
    assert report["hybrid"]["specificity"] == 1.0
    assert report["metrics"]["extraction_call_count"] == 53
    assert len(extractor.calls) == 53
    assert all(not call["existing_memories"] for call in extractor.calls)


async def test_eval_labels_never_enter_gate_or_extractor_calls(tmp_path: Path) -> None:
    case = _case(
        "LEAK-001",
        "她特别喜欢寿司。",
        route="HARD_PASS",
        should_extract=True,
        reason="PREFERENCE",
        context=[{"role": "assistant", "content": "SAFE_CONTEXT"}],
    )
    case["expected"]["private_label"] = "EXPECTED_SECRET"
    case["rationale"] = "RATIONALE_SECRET"
    case["extraction_hint"] = "EXTRACTION_HINT_SECRET"
    path = _write_cases(tmp_path / "labels.jsonl", [case])
    gate = _RecordingGate()
    extractor = _ScriptedExtractor(
        {
            case["current_user"]: AtomicExtraction(
                should_extract=True,
                gate_reason=MemorySemanticGateReason.PREFERENCE,
                claims=[_claim()],
            )
        }
    )

    await evaluate_memory_gate_v2(path, extractor=extractor, gate=gate)

    production_calls = json.dumps(
        [*gate.calls, *extractor.calls],
        ensure_ascii=False,
        default=str,
    )
    assert "SAFE_CONTEXT" in production_calls
    assert "EXPECTED_SECRET" not in production_calls
    assert "RATIONALE_SECRET" not in production_calls
    assert "EXTRACTION_HINT_SECRET" not in production_calls


async def test_eval_passes_structured_pending_context_to_l0_and_flash(
    tmp_path: Path,
) -> None:
    text = "她。"
    case = _case(
        "CONTEXT-STRUCTURED-001",
        text,
        route="CONTEXT_PASS",
        should_extract=True,
        reason="CONTEXT_DEPENDENT_REPLY",
        category="context_short_reply",
        context=[{"role": "assistant", "content": "这次是谁先提的分手？"}],
    )
    path = _write_cases(tmp_path / "structured-context.jsonl", [case])
    extractor = _ScriptedExtractor(
        {
            text: AtomicExtraction(
                should_extract=True,
                gate_reason=MemorySemanticGateReason.CONTEXT_DEPENDENT_REPLY,
            )
        }
    )

    report = await evaluate_memory_gate_v2(path, extractor=extractor)

    pending = extractor.calls[0]["pending_memory_context"]
    assert pending.previous_assistant_question == "这次是谁先提的分手？"
    assert pending.expected_slot == "actor"
    assert pending.created_turn == "eval:CONTEXT-STRUCTURED-001"
    assert report["cases"][0]["hybrid"]["pending_memory_context_source"] == (
        "structured"
    )


async def test_contract_warnings_failures_and_attempts_are_separate(
    tmp_path: Path,
) -> None:
    positive_text = "她特别喜欢寿司。"
    negative_text = "我今天心情特别差，什么都不想干。"
    failure_text = "她刚刚十分钟没回我，我有点烦。"
    cases = [
        _case(
            "CONTRACT-001",
            positive_text,
            route="HARD_PASS",
            should_extract=True,
            reason="PREFERENCE",
        ),
        _case(
            "CONTRACT-002",
            negative_text,
            route="SEMANTIC_REVIEW",
            should_extract=False,
            reason="TRANSIENT",
            category="negative_transient",
        ),
        _case(
            "CONTRACT-003",
            failure_text,
            route="SEMANTIC_REVIEW",
            should_extract=False,
            reason="TRANSIENT",
            category="negative_transient",
        ),
    ]
    path = _write_cases(tmp_path / "contracts.jsonl", cases)
    extractor = _ScriptedExtractor(
        {
            positive_text: AtomicExtraction(
                should_extract=True,
                gate_reason=MemorySemanticGateReason.PREFERENCE,
                claims=[],
            ),
            negative_text: AtomicExtraction(
                should_extract=False,
                gate_reason=MemorySemanticGateReason.TRANSIENT,
                claims=[_claim(negative_text)],
            ),
            failure_text: RuntimeError("provider failed"),
        }
    )

    report = await evaluate_memory_gate_v2(path, extractor=extractor)
    rows = {row["id"]: row for row in report["cases"]}

    assert rows["CONTRACT-001"]["semantic_gate_pass"] is True
    assert rows["CONTRACT-001"]["hybrid"]["extraction_warning"] == "empty_claims"
    assert rows["CONTRACT-002"]["hybrid"][
        "semantic_gate_contract_violation"
    ] is True
    assert rows["CONTRACT-002"]["hybrid"]["should_extract"] is None
    assert rows["CONTRACT-002"]["hybrid"]["semantic_gate_status"] == "CONTRACT_ERROR"
    assert rows["CONTRACT-003"]["hybrid"]["should_extract"] is None
    assert rows["CONTRACT-003"]["hybrid"]["semantic_gate_status"] == "INDETERMINATE"
    assert report["metrics"]["empty_claim_turn_count"] == 1
    assert report["metrics"]["semantic_gate_contract_violation_count"] == 1
    assert report["metrics"]["false_with_claims_count"] == 1
    assert report["metrics"]["extraction_failure_count"] == 1
    serialized = json.dumps(report, ensure_ascii=False)
    assert "RAW_RESPONSE_MUST_NOT_LEAK" not in serialized
    assert "INVALID_CLAIM_MUST_NOT_LEAK" not in serialized


async def test_claim_schema_failure_is_not_counted_as_gate_false_negative(
    tmp_path: Path,
) -> None:
    text = "我俩是去年十一月确定关系的。"
    path = _write_cases(
        tmp_path / "claim-invalid.jsonl",
        [
            _case(
                "GATE-003",
                text,
                route="HARD_PASS",
                should_extract=True,
                reason="RELATIONSHIP_STATE",
            )
        ],
    )

    report = await evaluate_memory_gate_v2(path, extractor=_ClaimInvalidExtractor())
    row = report["cases"][0]

    assert row["semantic_gate_pass"] is True
    assert row["hybrid"]["should_extract"] is True
    assert row["hybrid"]["semantic_gate_status"] == "PASS"
    assert row["hybrid"]["extraction_status"] == "claim_schema_invalid"
    assert report["metrics"]["gate_contract_error_count"] == 0
    assert report["metrics"]["claim_schema_error_count"] == 1
    assert report["metrics"]["claim_schema_invalid_turn_count"] == 1
    assert report["metrics"]["empty_claim_turn_count"] == 0


async def test_case_extraction_status_uses_final_recovered_extraction(
    tmp_path: Path,
) -> None:
    text = "她喜欢寿司。"
    path = _write_cases(
        tmp_path / "strong-recovery.jsonl",
        [
            _case(
                "RECOVERY-001",
                text,
                route="HARD_PASS",
                should_extract=True,
                reason="PREFERENCE",
            )
        ],
    )

    report = await evaluate_memory_gate_v2(
        path,
        extractor=_StrongRecoveryExtractor(),
    )

    row = report["cases"][0]
    assert row["hybrid"]["claim_count"] == 1
    assert row["hybrid"]["extraction_status"] == "success"
    assert report["metrics"]["empty_claim_turn_count"] == 0


async def test_report_contains_ab_slices_telemetry_and_ten_answers() -> None:
    cases = load_memory_gate_v2_cases(DATASET)
    extractor = _ScriptedExtractor(
        {
            case["current_user"]: AtomicExtraction(
                should_extract=case["expected"]["should_extract"],
                gate_reason=case["expected"]["gate_reason"],
                claims=[],
            )
            for case in cases
            if case["expected"]["l0_route"] != "HARD_DROP"
        }
    )

    result = await evaluate_memory_gate_v2(DATASET, extractor=extractor)
    markdown = render_memory_gate_v2_report(result)

    for slice_name in (
        "USER_BELIEF",
        "PARTIAL_CHANGE",
        "DURABLE_CHANGE",
        "CONTEXT_DEPENDENT_REPLY",
        "TRANSIENT",
        "SMALL_TALK",
    ):
        assert slice_name in result["slices"]
        assert {"current", "hybrid", "delta"} <= result["slices"][slice_name].keys()
    assert "Current Gate vs Hybrid Gate V2" in markdown
    assert "Flash Gate p50/p95" in markdown
    assert "## Required Questions" in markdown
    assert "Memory Gate V2 freeze status: `FROZEN`" in markdown
    assert "structured PendingMemoryContext handoff" in markdown
    assert "1. Current Python Gate" in markdown
    assert "10. 下一轮 Extraction Eval" in markdown

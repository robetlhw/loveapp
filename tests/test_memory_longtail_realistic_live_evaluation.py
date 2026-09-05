"""Contract tests for the shadow-only long-tail live evaluator.

These tests deliberately use only fake extractor, retriever, and judge
dependencies.  They assert the evaluator's production-shaped boundaries
without calling an LLM or a MemoryStore.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from loveapp.application.memory_retrieval import MemoryRetrievalScore, RetrievedMemory
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    ClaimRelation,
    EvidenceExplicitness,
    MemoryAttemptStatus,
    MemoryExtractionAttempt,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    PredicateType,
    TimeKind,
)
from loveapp.domain.memory_semantic_relation import SemanticRelationProposal
from loveapp.evaluation.memory_longtail_realistic import (
    _summarize_consistency,
    evaluate_memory_longtail_realistic,
    render_longtail_realistic_report,
)


class RecordingExtractor:
    def __init__(self, extractions: dict[str, AtomicExtraction]) -> None:
        self._extractions = extractions
        self.calls: list[dict[str, Any]] = []

    async def extract(
        self,
        text: str,
        *,
        reference_time,
        existing_memories: list[MemoryItem],
        conversation_history,
        trace=None,
        attempt_callback=None,
    ) -> AtomicExtraction:
        del reference_time, attempt_callback
        self.calls.append(
            {
                "text": text,
                "existing_memory_ids": [item.id for item in existing_memories],
                "history": [message.content for message in conversation_history],
                "trace_provided": trace is not None,
            }
        )
        if trace is not None:
            with trace.measure("fake_live_extraction") as details:
                details["prompt_tokens"] = 7
                details["completion_tokens"] = 3
                details["total_tokens"] = 10
        return self._extractions[text].model_copy(deep=True)


class FailingExtractor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def extract(self, text: str, **kwargs: object) -> AtomicExtraction:
        self.calls.append(text)
        attempt_callback = kwargs.get("attempt_callback")
        if callable(attempt_callback):
            attempt_callback(
                MemoryExtractionAttempt(
                    attempt=1,
                    status=MemoryAttemptStatus.FAILED,
                    duration_ms=4.0,
                    model="fake-live-extractor",
                    tier="flash",
                    failure_category="schema_validation",
                    error="invalid live extraction",
                )
            )
        raise ValueError("invalid live extraction")


class NeverCalledExtractor:
    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, *args: object, **kwargs: object) -> AtomicExtraction:
        del args, kwargs
        self.calls += 1
        raise AssertionError("a Gate-negative turn must not call the extractor")


class StrongFallbackTelemetryExtractor:
    def __init__(self, extraction: AtomicExtraction) -> None:
        self._extraction = extraction

    async def extract(self, text: str, **kwargs: object) -> AtomicExtraction:
        callback = kwargs.get("attempt_callback")
        assert callable(callback)
        callback(
            MemoryExtractionAttempt(
                attempt=1,
                status=MemoryAttemptStatus.COMPLETED,
                duration_ms=2500,
                model="flash-model",
                tier="flash",
                claim_count=1,
                upgrade_reason="existing_memory_conflict",
            )
        )
        callback(
            MemoryExtractionAttempt(
                attempt=2,
                status=MemoryAttemptStatus.FAILED,
                duration_ms=70000,
                model="strong-model",
                tier="strong",
                claim_count=0,
                upgrade_reason="existing_memory_conflict",
                discard_reason="strong_output_invalid",
                failure_category="empty_response",
            )
        )
        return self._extraction.model_copy(deep=True)


class RecordingRetriever:
    """Duck-typed production retriever substitute used by the shadow adapter."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.returned_items: list[MemoryItem] = []
        self.returned_statuses: dict[str, str] = {}

    async def retrieve(
        self,
        memories: Iterable[MemoryItem],
        *,
        query: str | None = None,
        limit: int = 20,
        reference_time=None,
        token_budget: int | None = None,
        mode=None,
        preserve_candidates: bool = False,
        require_relevance: bool = True,
    ) -> list[RetrievedMemory]:
        del reference_time, token_budget, mode, preserve_candidates, require_relevance
        items = list(memories)
        self.calls.append(
            {
                "query": query,
                "limit": limit,
                "memory_ids": [item.id for item in items],
            }
        )
        self.returned_items.extend(items)
        self.returned_statuses.update({item.id: item.status.value for item in items})
        return [
            RetrievedMemory(
                item=item,
                retrieval_text=item.summary,
                score=MemoryRetrievalScore(
                    semantic_similarity=0.9,
                    predicate_match=0.8,
                    recency=0.9,
                    importance=0.6,
                    confidence=0.9,
                    lifecycle_priority=0.6,
                    total=0.85,
                ),
            )
            for item in items[:limit]
        ]


class EmptyRetriever(RecordingRetriever):
    async def retrieve(
        self,
        memories: Iterable[MemoryItem],
        *,
        query: str | None = None,
        limit: int = 20,
        **kwargs: object,
    ) -> list[RetrievedMemory]:
        del kwargs
        items = list(memories)
        self.calls.append(
            {
                "query": query,
                "limit": limit,
                "memory_ids": [item.id for item in items],
            }
        )
        return []


class RecordingJudge:
    def __init__(self, *, relation: ClaimRelation = ClaimRelation.COMPLEMENTARY) -> None:
        self._relation = relation
        self.calls: list[dict[str, Any]] = []

    async def propose_relation(
        self,
        *,
        incoming,
        candidates: list[MemoryItem],
        trace=None,
    ) -> SemanticRelationProposal:
        del trace
        self.calls.append(
            {
                "incoming_summary": incoming.summary,
                "candidate_ids": [candidate.id for candidate in candidates],
            }
        )
        return SemanticRelationProposal(
            relation=self._relation,
            target_memory_ids=[candidates[0].id] if candidates else [],
            same_semantic_dimension=self._relation == ClaimRelation.UPDATE,
            confidence=0.97,
            reason="fake live semantic relation proposal",
            judge_model="fake-live-judge",
            prompt_tokens=13,
            completion_tokens=5,
            total_tokens=18,
            latency_ms=4.5,
        )


class FailingJudge:
    def __init__(self) -> None:
        self.calls = 0

    async def propose_relation(self, **kwargs: object) -> SemanticRelationProposal:
        del kwargs
        self.calls += 1
        raise TimeoutError("fake semantic judge timeout")


class RetryTelemetryJudge(RecordingJudge):
    async def propose_relation(self, *, trace=None, **kwargs: object) -> SemanticRelationProposal:
        assert trace is not None
        with trace.measure("memory_semantic_relation_model") as details:
            details.update(
                {
                    "attempt_count": 2,
                    "retry_count": 1,
                    "attempt_1_status": "parse_failed",
                    "attempt_2_status": "parsed",
                    "parse_status": "completed",
                }
            )
        return await super().propose_relation(trace=trace, **kwargs)


class FinalParseFailureJudge:
    async def propose_relation(self, *, trace=None, **kwargs: object) -> SemanticRelationProposal:
        del kwargs
        assert trace is not None
        with trace.measure("memory_semantic_relation_model") as details:
            details.update(
                {
                    "attempt_count": 2,
                    "retry_count": 1,
                    "attempt_1_status": "parse_failed",
                    "attempt_2_status": "parse_failed",
                    "parse_status": "failed",
                }
            )
            raise ValueError("invalid structured output")


def _claim(
    text: str,
    predicate: str,
    *,
    kind: MemoryKind = MemoryKind.INTERACTION_PATTERN,
    subject: str = "partner",
    evidence_spans: list[str] | None = None,
    predicate_type: PredicateType = PredicateType.CUSTOM,
    payload: dict[str, Any] | None = None,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    confidence: float = 0.95,
    explicitness: EvidenceExplicitness = EvidenceExplicitness.EXPLICIT,
    perspective: MemoryPerspective = MemoryPerspective.USER_REPORTED,
    requires_inference: bool = False,
) -> AtomicClaim:
    return AtomicClaim(
        claim_id=f"{predicate}-claim",
        kind=kind,
        subject=subject,
        predicate=predicate,
        summary=text,
        evidence_spans=evidence_spans or [text],
        time_kind=TimeKind.INTERVAL,
        period_start=period_start,
        period_end=period_end,
        raw_predicate=predicate,
        predicate_type=predicate_type,
        canonical_predicate=(predicate if predicate_type == PredicateType.CANONICAL else None),
        custom_predicate=(predicate if predicate_type == PredicateType.CUSTOM else None),
        payload=payload or {},
        confidence=confidence,
        explicitness=explicitness,
        perspective=perspective,
        requires_inference=requires_inference,
    )


def _expected_claim(claim_id: str, text: str, predicate: str) -> dict[str, Any]:
    return {
        "id": claim_id,
        "kind": MemoryKind.INTERACTION_PATTERN.value,
        "subject": "partner",
        "summary": text,
        "custom_predicate": predicate,
    }


def _write_dataset(tmp_path: Path, case: dict[str, Any]) -> Path:
    path = tmp_path / "longtail_live.jsonl"
    path.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _two_turn_case(
    *,
    case_id: str = "LIVE-CTX",
    expected_relation: ClaimRelation = ClaimRelation.COMPLEMENTARY,
) -> tuple[dict[str, Any], str, str]:
    first_text = "请记住：她最近回复很慢。"
    second_text = "请记住：她最近回复正常了。"
    return (
        {
            "id": case_id,
            "category": "live_contract",
            "reference_time": "2026-08-29T12:00:00+00:00",
            "turns": [
                {
                    "turn_id": "t1",
                    "text": first_text,
                    "claims": [_expected_claim("old", first_text, "reply_slow")],
                    "expected": {"gate_should_extract": True},
                },
                {
                    "turn_id": "t2",
                    "text": second_text,
                    "claims": [_expected_claim("new", second_text, "reply_normal")],
                    "expected": {
                        "gate_should_extract": True,
                        "relation": expected_relation.value,
                        "target_memory_ids": [f"{case_id}-t1-1"],
                        "retrieval_relevant_memory_ids": [f"{case_id}-t1-1"],
                        "validator_pass": True,
                        "destructive_update_allowed": expected_relation == ClaimRelation.UPDATE,
                        "would_supersede_memory_ids": (
                            [f"{case_id}-t1-1"] if expected_relation == ClaimRelation.UPDATE else []
                        ),
                    },
                },
            ],
        },
        first_text,
        second_text,
    )


@pytest.mark.asyncio
async def test_live_evaluator_passes_raw_text_history_and_virtual_memory_context(
    tmp_path: Path,
) -> None:
    case, first_text, second_text = _two_turn_case()
    extractor = RecordingExtractor(
        {
            first_text: AtomicExtraction(claims=[_claim(first_text, "reply_slow")]),
            second_text: AtomicExtraction(claims=[_claim(second_text, "reply_normal")]),
        }
    )
    retriever = RecordingRetriever()
    judge = RecordingJudge()

    report = await evaluate_memory_longtail_realistic(
        _write_dataset(tmp_path, case),
        mode="live",
        extractor=extractor,
        judge=judge,
        retriever=retriever,  # type: ignore[arg-type]
    )

    assert report["evaluation_mode"] == "shadow_live"
    assert report["store_mutation_permitted"] is False
    assert [call["text"] for call in extractor.calls] == [first_text, second_text]
    assert extractor.calls[0]["history"] == []
    assert extractor.calls[0]["existing_memory_ids"] == []
    assert extractor.calls[1]["history"] == [first_text]
    assert extractor.calls[1]["existing_memory_ids"] == ["LIVE-CTX-t1-1"]
    assert all(call["trace_provided"] for call in extractor.calls)
    assert retriever.calls[-1]["memory_ids"] == ["LIVE-CTX-t1-1"]
    assert judge.calls == [
        {
            "incoming_summary": second_text,
            "candidate_ids": ["LIVE-CTX-t1-1"],
        }
    ]

    second_turn = report["cases"][0]["turns"][1]
    second_claim = second_turn["claim_results"][0]
    assert second_claim["actual"]["relation"] == ClaimRelation.COMPLEMENTARY.value
    assert second_claim["actual"]["target_memory_ids"] == ["LIVE-CTX-t1-1"]
    assert second_claim["actual"]["validator"]["would_update"] is False
    assert second_claim["checks"]["shadow_only"] is True
    assert report["metrics"]["extraction_call_count"] == 2
    assert report["metrics"]["extraction_expected_count"] == 2
    assert report["metrics"]["extraction_success_count"] == 2
    assert report["metrics"]["expected_memory_kind_accuracy"] == 1.0
    assert report["metrics"]["expected_predicate_accuracy"] == 1.0
    assert report["metrics"]["retrieval_hit_at_1"] == 1.0
    assert report["cases"][0]["final_virtual_memory_ids"] == [
        "LIVE-CTX-t1-1",
        "LIVE-CTX-t2-1",
    ]


@pytest.mark.asyncio
async def test_live_evaluator_skips_extraction_for_gate_negative_turn(tmp_path: Path) -> None:
    dataset = _write_dataset(
        tmp_path,
        {
            "id": "LIVE-GATE-NEGATIVE",
            "category": "gate_negative",
            "turns": [
                {
                    "turn_id": "t1",
                    "text": "今天吃什么？",
                    "claims": [],
                    "expected": {"gate_should_extract": False},
                }
            ],
        },
    )
    extractor = NeverCalledExtractor()
    judge = RecordingJudge()

    report = await evaluate_memory_longtail_realistic(
        dataset,
        mode="live",
        extractor=extractor,
        judge=judge,
    )

    assert extractor.calls == 0
    assert judge.calls == []
    assert report["metrics"]["extraction_call_count"] == 0
    assert report["cases"][0]["passed"] is True
    assert report["cases"][0]["turns"][0]["extraction"]["mode"] == "live_gate_skipped"
    assert "Semantic Judge" not in report["metrics"]["error_attribution"]
    assert report["metrics"]["semantic_judge_call_count"] == 0


@pytest.mark.asyncio
async def test_live_evaluator_keeps_semantic_retrieval_top_k_at_five(tmp_path: Path) -> None:
    dataset = _write_dataset(
        tmp_path,
        {
            "id": "LIVE-LIMIT",
            "category": "gate_negative",
            "turns": [
                {
                    "turn_id": "t1",
                    "text": "今天吃什么？",
                    "claims": [],
                    "expected": {"gate_should_extract": False},
                }
            ],
        },
    )

    with pytest.raises(ValueError, match=r"candidate_limit.*5"):
        await evaluate_memory_longtail_realistic(
            dataset,
            mode="live",
            candidate_limit=6,
            extractor=NeverCalledExtractor(),
            judge=RecordingJudge(),
        )


@pytest.mark.asyncio
async def test_live_admission_rejection_does_not_enter_later_virtual_context(
    tmp_path: Path,
) -> None:
    first_text = "请记住：她最近回复很慢。"
    second_text = "请记住：她最近回复正常了。"
    case = {
        "id": "LIVE-ADMISSION-REJECT",
        "category": "live_governance",
        "turns": [
            {
                "turn_id": "t1",
                "text": first_text,
                "claims": [_expected_claim("rejected", first_text, "reply_slow")],
                "expected": {"gate_should_extract": True},
            },
            {
                "turn_id": "t2",
                "text": second_text,
                "claims": [_expected_claim("accepted", second_text, "reply_normal")],
                "expected": {"gate_should_extract": True},
            },
        ],
    }
    extractor = RecordingExtractor(
        {
            first_text: AtomicExtraction(
                claims=[
                    _claim(
                        first_text,
                        "reply_slow",
                        evidence_spans=["这段证据并不存在于原始输入"],
                    )
                ]
            ),
            second_text: AtomicExtraction(claims=[_claim(second_text, "reply_normal")]),
        }
    )
    judge = RecordingJudge()

    report = await evaluate_memory_longtail_realistic(
        _write_dataset(tmp_path, case),
        mode="live",
        extractor=extractor,
        judge=judge,
        retriever=RecordingRetriever(),  # type: ignore[arg-type]
    )

    first_turn = report["cases"][0]["turns"][0]
    rejected = first_turn["claim_results"][0]
    assert rejected["primary_failure_stage"] == "Admission"
    assert rejected["live"]["outcome"] == "admission_rejected"
    assert rejected["live"]["admission"]["reason"] == "evidence_not_in_source"
    assert rejected["live"]["eligible_for_virtual_context"] is False
    assert rejected["live"]["memory_id"] is None
    assert first_turn["virtual_memory_ids_after"] == []
    assert extractor.calls[1]["existing_memory_ids"] == []
    assert judge.calls == []
    assert report["cases"][0]["final_virtual_memory_ids"] == ["LIVE-ADMISSION-REJECT-t2-1"]
    assert report["store_mutation_permitted"] is False


@pytest.mark.asyncio
async def test_live_expected_claim_mapping_is_semantic_not_extractor_order(
    tmp_path: Path,
) -> None:
    text = "请记住：她回复很慢，而且见面次数也少了。"
    case = {
        "id": "LIVE-REVERSED-CLAIMS",
        "category": "extraction_mapping",
        "turns": [
            {
                "turn_id": "t1",
                "text": text,
                "claims": [
                    _expected_claim("reply", "她回复很慢", "reply_slow"),
                    _expected_claim("meeting", "见面次数少了", "meeting_less"),
                ],
                "expected": {"gate_should_extract": True},
            }
        ],
    }
    extractor = RecordingExtractor(
        {
            text: AtomicExtraction(
                claims=[
                    _claim("见面次数也少了", "meeting_less"),
                    _claim("她回复很慢", "reply_slow"),
                ]
            )
        }
    )

    report = await evaluate_memory_longtail_realistic(
        _write_dataset(tmp_path, case),
        mode="live",
        extractor=extractor,
        judge=RecordingJudge(),
        retriever=RecordingRetriever(),  # type: ignore[arg-type]
    )

    turn = report["cases"][0]["turns"][0]
    assert turn["extraction"]["unmatched_expected_claim_ids"] == []
    assert [record["expected_claim_id"] for record in turn["extraction"]["match_records"]] == [
        "meeting",
        "reply",
    ]
    assert [candidate["memory_id"] for candidate in turn["normalized_candidates"]] == [
        "LIVE-REVERSED-CLAIMS-t1-2",
        "LIVE-REVERSED-CLAIMS-t1-1",
    ]
    assert report["cases"][0]["expected_memory_id_map"] == {
        "LIVE-REVERSED-CLAIMS-t1-1": "LIVE-REVERSED-CLAIMS-t1-1",
        "LIVE-REVERSED-CLAIMS-t1-2": "LIVE-REVERSED-CLAIMS-t1-2",
    }


@pytest.mark.asyncio
async def test_live_expected_claim_mapping_does_not_cross_subjects(tmp_path: Path) -> None:
    text = "请记住：我最近回复很慢。"
    case = {
        "id": "LIVE-SUBJECT-MISMATCH",
        "category": "extraction_mapping",
        "turns": [
            {
                "turn_id": "t1",
                "text": text,
                "claims": [_expected_claim("partner-reply", text, "reply_slow")],
                "expected": {"gate_should_extract": True},
            }
        ],
    }
    extractor = RecordingExtractor(
        {text: AtomicExtraction(claims=[_claim(text, "reply_slow", subject="user")])}
    )

    report = await evaluate_memory_longtail_realistic(
        _write_dataset(tmp_path, case),
        mode="live",
        extractor=extractor,
        judge=RecordingJudge(),
        retriever=RecordingRetriever(),  # type: ignore[arg-type]
    )

    turn = report["cases"][0]["turns"][0]
    assert turn["extraction"]["matched_expected_claim_ids"] == []
    assert turn["extraction"]["unmatched_expected_claim_ids"] == ["partner-reply"]
    assert turn["normalized_candidates"][0]["matched_expected_claim_id"] is None
    assert turn["normalized_candidates"][0]["memory_id"] == ("LIVE-SUBJECT-MISMATCH-t1-live-1")
    missing = next(
        claim
        for claim in turn["claim_results"]
        if claim["live"].get("outcome") == "missing_expected_claim"
    )
    assert missing["primary_failure_stage"] == "Extraction"


@pytest.mark.asyncio
async def test_live_kind_accuracy_counts_a_semantically_matched_wrong_kind(
    tmp_path: Path,
) -> None:
    text = "请记住：她最近回复很慢。"
    case = {
        "id": "LIVE-KIND-MISMATCH",
        "category": "extraction_mapping",
        "turns": [
            {
                "turn_id": "t1",
                "text": text,
                "claims": [_expected_claim("reply", text, "reply_slow")],
                "expected": {"gate_should_extract": True},
            }
        ],
    }
    extractor = RecordingExtractor(
        {
            text: AtomicExtraction(
                claims=[
                    _claim(
                        text,
                        "reply_slow",
                        kind=MemoryKind.STABLE_FACT,
                    )
                ]
            )
        }
    )

    report = await evaluate_memory_longtail_realistic(
        _write_dataset(tmp_path, case),
        mode="live",
        extractor=extractor,
        judge=RecordingJudge(),
        retriever=RecordingRetriever(),  # type: ignore[arg-type]
    )

    extraction = report["cases"][0]["turns"][0]["extraction"]
    assert extraction["unmatched_expected_claim_ids"] == []
    assert extraction["match_records"][0]["match_reason"] == ("subject_predicate_kind_mismatch")
    assert report["metrics"]["expected_memory_kind_accuracy"] == 0.0
    assert report["metrics"]["expected_predicate_accuracy"] == 1.0


@pytest.mark.asyncio
async def test_live_reviewed_custom_representation_satisfies_semantic_identity(
    tmp_path: Path,
) -> None:
    text = "请记住：她最近回复很慢。"
    expected_claim = {
        **_expected_claim("reply", text, "reply_slow"),
        "expected_semantic_concept": "interaction_response_engagement",
        "acceptable_representations": [
            {
                "predicate_type": "custom",
                "custom_predicates": ["response_latency_slow"],
            }
        ],
    }
    case = {
        "id": "LIVE-CUSTOM-REVIEWED",
        "category": "extraction_mapping",
        "turns": [
            {
                "turn_id": "t1",
                "text": text,
                "claims": [expected_claim],
                "expected": {"gate_should_extract": True},
            }
        ],
    }
    extractor = RecordingExtractor(
        {text: AtomicExtraction(claims=[_claim(text, "response_latency_slow")])}
    )

    report = await evaluate_memory_longtail_realistic(
        _write_dataset(tmp_path, case),
        mode="live",
        extractor=extractor,
        judge=RecordingJudge(),
        retriever=RecordingRetriever(),  # type: ignore[arg-type]
    )

    claim = report["cases"][0]["turns"][0]["claim_results"][0]
    assert claim["passed"] is True
    assert claim["checks"]["semantic_identity"] is True
    assert report["metrics"]["raw_predicate_match_rate"] == 0.0
    assert report["metrics"]["semantic_identity_match_rate"] == 1.0
    assert report["metrics"]["custom_semantic_identity_expected_count"] == 1
    assert report["metrics"]["custom_semantic_identity_pass_count"] == 1
    assert report["metrics"]["custom_semantic_identity_match_rate"] == 1.0
    assert report["metrics"]["canonical_semantic_identity_expected_count"] == 0
    assert report["metrics"]["overall_semantic_identity_expected_count"] == 1
    assert report["metrics"]["overall_semantic_identity_pass_count"] == 1
    assert report["metrics"]["overall_semantic_identity_match_rate"] == 1.0


@pytest.mark.asyncio
async def test_live_reviewed_custom_representation_requires_payload_and_evidence_qualifiers(
    tmp_path: Path,
) -> None:
    text = "请记住：她最近回复很慢。"
    expected_claim = {
        **_expected_claim("reply", text, "reply_slow"),
        "expected_semantic_concept": "interaction_response_engagement",
        "acceptable_representations": [
            {
                "predicate_type": "custom",
                "custom_predicates": ["response_latency_slow"],
                "payload_constraints": {
                    "object": ["slow_reply"],
                    "direction": ["decreasing"],
                },
                "evidence_contains_any": ["回复很慢", "回复越来越慢"],
            }
        ],
    }
    case = {
        "id": "LIVE-CUSTOM-QUALIFIED",
        "category": "extraction_mapping",
        "turns": [
            {
                "turn_id": "t1",
                "text": text,
                "claims": [expected_claim],
                "expected": {"gate_should_extract": True},
            }
        ],
    }
    extractor = RecordingExtractor(
        {
            text: AtomicExtraction(
                claims=[
                    _claim(
                        text,
                        "response_latency_slow",
                        payload={"object": "slow_reply", "direction": "decreasing"},
                    )
                ]
            )
        }
    )

    report = await evaluate_memory_longtail_realistic(
        _write_dataset(tmp_path, case),
        mode="live",
        extractor=extractor,
        judge=RecordingJudge(),
        retriever=RecordingRetriever(),  # type: ignore[arg-type]
    )

    claim = report["cases"][0]["turns"][0]["claim_results"][0]
    assert claim["checks"]["semantic_identity"] is True
    match = report["cases"][0]["turns"][0]["extraction"]["match_records"][0]
    assert match["semantic_identity_reason"] == "acceptable_custom_representation"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_text", "payload", "evidence_spans", "expected_reason"),
    [
        (
            "请记住：她最近回复很慢。",
            {"object": "fast_reply", "direction": "decreasing"},
            ["她最近回复很慢"],
            "custom_payload_qualifier_mismatch",
        ),
        (
            "请记住：她最近一直正常回复。",
            {"object": "slow_reply", "direction": "decreasing"},
            ["她最近一直正常回复"],
            "custom_evidence_qualifier_mismatch",
        ),
    ],
)
async def test_live_custom_representation_qualifiers_fail_closed(
    tmp_path: Path,
    source_text: str,
    payload: dict[str, Any],
    evidence_spans: list[str],
    expected_reason: str,
) -> None:
    expected_claim = {
        **_expected_claim("reply", source_text, "reply_slow"),
        "expected_semantic_concept": "interaction_response_engagement",
        "acceptable_representations": [
            {
                "predicate_type": "custom",
                "custom_predicates": ["response_latency_slow"],
                "payload_constraints": {
                    "object": ["slow_reply"],
                    "direction": ["decreasing"],
                },
                "evidence_contains_any": ["回复很慢"],
            }
        ],
    }
    case = {
        "id": f"LIVE-CUSTOM-{expected_reason}",
        "category": "extraction_mapping",
        "turns": [
            {
                "turn_id": "t1",
                "text": source_text,
                "claims": [expected_claim],
                "expected": {"gate_should_extract": True},
            }
        ],
    }
    extractor = RecordingExtractor(
        {
            source_text: AtomicExtraction(
                claims=[
                    _claim(
                        source_text,
                        "response_latency_slow",
                        payload=payload,
                        evidence_spans=evidence_spans,
                    )
                ]
            )
        }
    )

    report = await evaluate_memory_longtail_realistic(
        _write_dataset(tmp_path, case),
        mode="live",
        extractor=extractor,
        judge=RecordingJudge(),
        retriever=RecordingRetriever(),  # type: ignore[arg-type]
    )

    claim = report["cases"][0]["turns"][0]["claim_results"][0]
    assert claim["checks"]["semantic_identity"] is False
    match = report["cases"][0]["turns"][0]["extraction"]["match_records"][0]
    assert match["semantic_identity_reason"] == expected_reason
    assert report["metrics"]["custom_semantic_identity_expected_count"] == 1
    assert report["metrics"]["custom_semantic_identity_pass_count"] == 0
    assert report["metrics"]["overall_semantic_identity_match_rate"] == 0.0


@pytest.mark.asyncio
async def test_live_custom_representation_rejects_unknown_contract_fields(
    tmp_path: Path,
) -> None:
    text = "请记住：她最近回复很慢。"
    expected_claim = {
        **_expected_claim("reply", text, "reply_slow"),
        "expected_semantic_concept": "interaction_response_engagement",
        "acceptable_representations": [
            {
                "predicate_type": "custom",
                "custom_predicates": ["response_latency_slow"],
                "payload_constraint": {"object": ["slow_reply"]},
            }
        ],
    }
    case = {
        "id": "LIVE-CUSTOM-UNKNOWN-CONTRACT-FIELD",
        "category": "extraction_mapping",
        "turns": [
            {
                "turn_id": "t1",
                "text": text,
                "claims": [expected_claim],
                "expected": {"gate_should_extract": True},
            }
        ],
    }

    with pytest.raises(ValueError, match="unsupported custom representation field"):
        await evaluate_memory_longtail_realistic(
            _write_dataset(tmp_path, case),
            mode="live",
            extractor=RecordingExtractor(
                {text: AtomicExtraction(claims=[_claim(text, "response_latency_slow")])}
            ),
            judge=RecordingJudge(),
            retriever=RecordingRetriever(),  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_live_custom_representation_rejects_unbounded_payload_qualifiers(
    tmp_path: Path,
) -> None:
    text = "请记住：她最近回复很慢。"
    expected_claim = {
        **_expected_claim("reply", text, "reply_slow"),
        "expected_semantic_concept": "interaction_response_engagement",
        "acceptable_representations": [
            {
                "predicate_type": "custom",
                "custom_predicates": ["response_latency_slow"],
                "payload_constraints": {"arbitrary_nested_path": ["anything"]},
            }
        ],
    }
    case = {
        "id": "LIVE-CUSTOM-UNBOUNDED-QUALIFIER",
        "category": "extraction_mapping",
        "turns": [
            {
                "turn_id": "t1",
                "text": text,
                "claims": [expected_claim],
                "expected": {"gate_should_extract": True},
            }
        ],
    }

    with pytest.raises(ValueError, match="unsupported custom payload qualifier"):
        await evaluate_memory_longtail_realistic(
            _write_dataset(tmp_path, case),
            mode="live",
            extractor=RecordingExtractor({text: AtomicExtraction(claims=[])}),
            judge=RecordingJudge(),
            retriever=RecordingRetriever(),  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_live_unreviewed_custom_predicate_fails_semantic_identity_contract(
    tmp_path: Path,
) -> None:
    text = "请记住：她最近回复很慢。"
    expected_claim = {
        **_expected_claim("reply", text, "reply_slow"),
        "expected_semantic_concept": "interaction_response_engagement",
        "acceptable_representations": [
            {
                "predicate_type": "custom",
                "custom_predicates": ["reply_slow"],
            }
        ],
    }
    case = {
        "id": "LIVE-CUSTOM-UNREVIEWED",
        "category": "extraction_mapping",
        "turns": [
            {
                "turn_id": "t1",
                "text": text,
                "claims": [expected_claim],
                "expected": {"gate_should_extract": True},
            }
        ],
    }
    extractor = RecordingExtractor(
        {text: AtomicExtraction(claims=[_claim(text, "response_latency_degraded")])}
    )

    report = await evaluate_memory_longtail_realistic(
        _write_dataset(tmp_path, case),
        mode="live",
        extractor=extractor,
        judge=RecordingJudge(),
        retriever=RecordingRetriever(),  # type: ignore[arg-type]
    )

    claim = report["cases"][0]["turns"][0]["claim_results"][0]
    assert claim["passed"] is False
    assert claim["checks"]["semantic_identity"] is False
    assert claim["failures"] == ["semantic_identity"]
    assert claim["primary_failure_stage"] == "Normalization"
    semantic_trace = next(
        entry for entry in claim["trace"] if entry["layer"] == "semantic_identity"
    )
    assert semantic_trace["semantic_representation"]["semantic_identity_reason"] == (
        "no_acceptable_representation_matched"
    )
    assert report["metrics"]["semantic_identity_match_rate"] == 0.0


@pytest.mark.asyncio
async def test_live_canonical_incoming_uses_production_local_path_not_longtail_judge(
    tmp_path: Path,
) -> None:
    case, first_text, second_text = _two_turn_case(case_id="LIVE-CANONICAL-INCOMING")
    canonical_predicate = "interaction.response_engagement"
    case["turns"][1]["claims"] = [_expected_claim("new", second_text, canonical_predicate)]
    extractor = RecordingExtractor(
        {
            first_text: AtomicExtraction(claims=[_claim(first_text, "reply_slow")]),
            second_text: AtomicExtraction(
                claims=[
                    _claim(
                        second_text,
                        canonical_predicate,
                        predicate_type=PredicateType.CANONICAL,
                    )
                ]
            ),
        }
    )
    judge = RecordingJudge()

    report = await evaluate_memory_longtail_realistic(
        _write_dataset(tmp_path, case),
        mode="live",
        extractor=extractor,
        judge=judge,
        retriever=RecordingRetriever(),  # type: ignore[arg-type]
    )

    second_claim = report["cases"][0]["turns"][1]["claim_results"][0]
    assert judge.calls == []
    assert second_claim["actual"]["judge_status"] == "not_called"
    assert second_claim["actual"]["resolution_status"] == (
        "canonical_candidate_uses_local_relation_path"
    )
    assert second_claim["primary_failure_stage"] == "Canonical Governance"
    assert report["metrics"]["semantic_judge_call_count"] == 0
    assert report["cases"][0]["final_virtual_memory_ids"] == [
        "LIVE-CANONICAL-INCOMING-t1-1",
        "LIVE-CANONICAL-INCOMING-t2-1",
    ]


@pytest.mark.asyncio
async def test_live_valid_canonical_transition_uses_deterministic_governance(
    tmp_path: Path,
) -> None:
    case, first_text, second_text = _two_turn_case(
        case_id="LIVE-CANONICAL-GOVERNED",
        expected_relation=ClaimRelation.UPDATE,
    )
    first_text = "请记住：她最近总是主动找我聊天。"
    second_text = "请记住：最近基本都是我主动找她聊天。"
    case["turns"][0]["text"] = first_text
    case["turns"][0]["claims"][0]["summary"] = first_text
    case["turns"][1]["text"] = second_text
    case["turns"][1]["claims"][0]["summary"] = second_text
    canonical = "interaction.initiation_balance"
    representations = [
        {
            "predicate_type": "canonical",
            "canonical_predicate": canonical,
            "state_dimension": canonical,
            "state_values": ["partner_to_user", "user_to_partner"],
        },
        {
            "predicate_type": "custom",
            "custom_predicates": ["reply_slow", "reply_normal"],
        },
    ]
    for turn in case["turns"]:
        turn["claims"][0]["expected_semantic_concept"] = "interaction_initiation_balance"
        turn["claims"][0]["acceptable_representations"] = representations
    extractor = RecordingExtractor(
        {
            first_text: AtomicExtraction(
                claims=[
                    _claim(
                        first_text,
                        canonical,
                        predicate_type=PredicateType.CANONICAL,
                        payload={"metric": "initiation_balance", "current": "partner_to_user"},
                        period_start=datetime(2026, 7, 29, tzinfo=UTC),
                        period_end=datetime(2026, 8, 10, tzinfo=UTC),
                    )
                ]
            ),
            second_text: AtomicExtraction(
                claims=[
                    _claim(
                        second_text,
                        canonical,
                        predicate_type=PredicateType.CANONICAL,
                        payload={"metric": "initiation_balance", "current": "user_to_partner"},
                        period_start=datetime(2026, 8, 11, tzinfo=UTC),
                        period_end=datetime(2026, 8, 29, tzinfo=UTC),
                    )
                ]
            ),
        }
    )
    judge = RecordingJudge(relation=ClaimRelation.UNRELATED)

    report = await evaluate_memory_longtail_realistic(
        _write_dataset(tmp_path, case),
        mode="live",
        extractor=extractor,
        judge=judge,
        retriever=RecordingRetriever(),  # type: ignore[arg-type]
    )

    second = report["cases"][0]["turns"][1]["claim_results"][0]
    assert judge.calls == []
    assert second["passed"] is True, second
    assert second["actual"]["relation"] == ClaimRelation.UPDATE.value
    assert second["actual"]["target_memory_ids"] == ["LIVE-CANONICAL-GOVERNED-t1-1"]
    assert second["actual"]["canonical_governance"]["rule_name"] == ("same_state_dimension")
    assert second["actual"]["canonical_governance"]["admission_status"] == (
        MemoryStatus.CONFIRMED.value
    )
    assert second["live"]["admission"]["reason"] == "confirmed_governed_transition"
    assert second["live"]["admission"]["score_breakdown"][
        "governed_transition_candidate"
    ] is True
    assert second["checks"]["semantic_identity"] is True
    assert report["metrics"]["raw_predicate_match_rate"] == 0.0
    assert report["metrics"]["canonical_predicate_match_rate"] == 1.0
    assert report["metrics"]["semantic_identity_match_rate"] == 1.0
    assert report["metrics"]["canonical_semantic_identity_expected_count"] == 2
    assert report["metrics"]["canonical_semantic_identity_pass_count"] == 2
    assert report["metrics"]["canonical_semantic_identity_match_rate"] == 1.0
    assert report["metrics"]["custom_semantic_identity_expected_count"] == 0
    assert report["metrics"]["overall_semantic_identity_expected_count"] == 2
    assert report["metrics"]["overall_semantic_identity_pass_count"] == 2
    assert report["metrics"]["overall_semantic_identity_match_rate"] == 1.0
    assert report["store_mutation_permitted"] is False


@pytest.mark.asyncio
async def test_live_missing_state_canonical_remains_a_contract_failure(
    tmp_path: Path,
) -> None:
    text = "请记住：最近基本都是我主动找她。"
    canonical = "interaction.initiation_balance"
    case = {
        "id": "LIVE-CANONICAL-MISSING-STATE",
        "category": "live_contract",
        "turns": [
            {
                "turn_id": "t1",
                "text": text,
                "claims": [
                    {
                        **_expected_claim("claim", text, "user_initiates_contact"),
                        "expected_semantic_concept": "interaction_initiation_balance",
                        "acceptable_representations": [
                            {
                                "predicate_type": "canonical",
                                "canonical_predicate": canonical,
                                "state_dimension": canonical,
                                "state_values": ["user_to_partner"],
                            }
                        ],
                    }
                ],
                "expected": {"gate_should_extract": True},
            }
        ],
    }
    extractor = RecordingExtractor(
        {
            text: AtomicExtraction(
                claims=[
                    _claim(
                        text,
                        canonical,
                        predicate_type=PredicateType.CANONICAL,
                    )
                ]
            )
        }
    )

    report = await evaluate_memory_longtail_realistic(
        _write_dataset(tmp_path, case),
        mode="live",
        extractor=extractor,
        judge=RecordingJudge(),
        retriever=RecordingRetriever(),  # type: ignore[arg-type]
    )

    claim = report["cases"][0]["turns"][0]["claim_results"][0]
    assert claim["passed"] is False
    assert claim["checks"]["semantic_identity"] is False
    assert claim["primary_failure_stage"] == "Normalization"
    assert claim["actual"]["canonical_governance"]["state_value"] is None
    assert report["metrics"]["canonical_predicate_match_rate"] == 1.0
    assert report["metrics"]["semantic_identity_match_rate"] == 0.0


@pytest.mark.asyncio
async def test_live_extraction_failure_is_an_extraction_failure_not_a_pass(tmp_path: Path) -> None:
    case, first_text, _ = _two_turn_case(case_id="LIVE-EXTRACTION-FAIL")
    case["turns"] = [case["turns"][0]]
    dataset = _write_dataset(tmp_path, case)
    extractor = FailingExtractor()

    report = await evaluate_memory_longtail_realistic(
        dataset,
        mode="live",
        extractor=extractor,
        judge=RecordingJudge(),
    )

    assert extractor.calls == [first_text]
    assert report["cases"][0]["passed"] is False
    claim = report["cases"][0]["turns"][0]["claim_results"][0]
    assert claim["primary_failure_stage"] == "Extraction"
    assert claim["actual"]["relation"] is None
    assert report["metrics"]["extraction_failure_count"] == 1
    assert report["metrics"]["schema_validation_failure_count"] == 1
    assert report["store_mutation_permitted"] is False


@pytest.mark.asyncio
async def test_live_strong_upgrade_telemetry_records_no_value_fallback(
    tmp_path: Path,
) -> None:
    case, first_text, _ = _two_turn_case(case_id="LIVE-STRONG-TELEMETRY")
    case["turns"] = [case["turns"][0]]
    extractor = StrongFallbackTelemetryExtractor(
        AtomicExtraction(claims=[_claim(first_text, "reply_slow")])
    )

    report = await evaluate_memory_longtail_realistic(
        _write_dataset(tmp_path, case),
        mode="live",
        extractor=extractor,
        judge=RecordingJudge(),
        retriever=RecordingRetriever(),  # type: ignore[arg-type]
    )

    metrics = report["metrics"]
    assert metrics["strong_upgrade_count"] == 1
    assert metrics["strong_upgrade_reason_counts"] == {"existing_memory_conflict": 1}
    assert metrics["strong_success_count"] == 0
    assert metrics["strong_failure_count"] == 1
    assert metrics["strong_latency_p50"] == 70000.0
    assert metrics["strong_latency_p95"] == 70000.0
    assert metrics["strong_fallback_to_flash_count"] == 1
    assert metrics["strong_no_value_added_count"] == 1


@pytest.mark.asyncio
async def test_live_judge_failure_fails_closed_without_virtual_lifecycle_mutation(
    tmp_path: Path,
) -> None:
    case, first_text, second_text = _two_turn_case(
        case_id="LIVE-JUDGE-FAIL",
        expected_relation=ClaimRelation.UPDATE,
    )
    extractor = RecordingExtractor(
        {
            first_text: AtomicExtraction(claims=[_claim(first_text, "reply_slow")]),
            second_text: AtomicExtraction(claims=[_claim(second_text, "reply_normal")]),
        }
    )
    retriever = RecordingRetriever()
    judge = FailingJudge()

    report = await evaluate_memory_longtail_realistic(
        _write_dataset(tmp_path, case),
        mode="live",
        extractor=extractor,
        judge=judge,
        retriever=retriever,  # type: ignore[arg-type]
    )

    second_claim = report["cases"][0]["turns"][1]["claim_results"][0]
    assert judge.calls == 1
    assert second_claim["actual"]["judge_status"] == "failed"
    assert second_claim["actual"]["relation"] == ClaimRelation.UNCERTAIN.value
    assert second_claim["actual"]["target_memory_ids"] == []
    assert second_claim["actual"]["validator"]["would_update"] is False
    assert second_claim["primary_failure_stage"] == "Semantic Judge"
    assert report["metrics"]["semantic_judge_call_count"] == 1
    assert report["metrics"]["semantic_judge_failure_count"] == 1
    assert report["metrics"]["judge_relation_expected_count"] == 0
    assert report["metrics"]["judge_relation_accuracy"] == 0.0
    assert report["metrics"]["false_destructive_update_count"] == 0
    assert report["store_mutation_permitted"] is False
    assert (
        retriever.returned_items[0].status.value
        == retriever.returned_statuses["LIVE-JUDGE-FAIL-t1-1"]
    )
    assert report["cases"][0]["final_virtual_memory_ids"] == [
        "LIVE-JUDGE-FAIL-t1-1",
        "LIVE-JUDGE-FAIL-t2-1",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("judge", "expected_retry_success", "expected_final_failure"),
    [
        (RetryTelemetryJudge(), 1, 0),
        (FinalParseFailureJudge(), 0, 1),
    ],
)
async def test_live_judge_retry_telemetry_distinguishes_recovery_from_fail_closed(
    tmp_path: Path,
    judge: object,
    expected_retry_success: int,
    expected_final_failure: int,
) -> None:
    case, first_text, second_text = _two_turn_case(
        case_id=f"LIVE-JUDGE-RETRY-{expected_final_failure}",
    )
    extractor = RecordingExtractor(
        {
            first_text: AtomicExtraction(claims=[_claim(first_text, "reply_slow")]),
            second_text: AtomicExtraction(claims=[_claim(second_text, "reply_normal")]),
        }
    )

    report = await evaluate_memory_longtail_realistic(
        _write_dataset(tmp_path, case),
        mode="live",
        extractor=extractor,
        judge=judge,  # type: ignore[arg-type]
        retriever=RecordingRetriever(),  # type: ignore[arg-type]
    )

    metrics = report["metrics"]
    assert metrics["judge_first_attempt_parse_failure_count"] == 1
    assert metrics["judge_retry_count"] == 1
    assert metrics["judge_retry_success_count"] == expected_retry_success
    assert metrics["judge_final_parse_failure_count"] == expected_final_failure
    assert metrics["judge_fail_closed_count"] == expected_final_failure
    assert report["store_mutation_permitted"] is False


@pytest.mark.asyncio
async def test_live_fail_closed_success_is_not_counted_as_a_scenario_failure(
    tmp_path: Path,
) -> None:
    case, first_text, second_text = _two_turn_case(
        case_id="LIVE-EXPECTED-UNCERTAIN",
        expected_relation=ClaimRelation.UNCERTAIN,
    )
    expected = case["turns"][1]["expected"]
    expected["target_memory_ids"] = []
    expected["validator_pass"] = True
    extractor = RecordingExtractor(
        {
            first_text: AtomicExtraction(claims=[_claim(first_text, "reply_slow")]),
            second_text: AtomicExtraction(claims=[_claim(second_text, "reply_normal")]),
        }
    )

    report = await evaluate_memory_longtail_realistic(
        _write_dataset(tmp_path, case),
        mode="live",
        extractor=extractor,
        judge=FailingJudge(),
        retriever=RecordingRetriever(),  # type: ignore[arg-type]
    )

    scenario = report["cases"][0]
    claim = scenario["turns"][1]["claim_results"][0]
    assert claim["passed"] is True
    assert claim["primary_failure_stage"] == "Semantic Judge"
    assert scenario["passed"] is True
    assert scenario["primary_failure_stage"] is None
    assert scenario["secondary_failure_stages"] == []
    assert report["metrics"]["first_failing_stage"] == {}
    assert report["metrics"]["error_attribution"] == {}
    assert report["metrics"]["semantic_judge_failure_count"] == 1


@pytest.mark.asyncio
async def test_live_metrics_count_only_incorrect_update_proposals_as_validator_denials(
    tmp_path: Path,
) -> None:
    case, first_text, second_text = _two_turn_case(case_id="LIVE-BAD-UPDATE")
    extractor = RecordingExtractor(
        {
            first_text: AtomicExtraction(claims=[_claim(first_text, "reply_slow")]),
            second_text: AtomicExtraction(claims=[_claim(second_text, "reply_normal")]),
        }
    )

    report = await evaluate_memory_longtail_realistic(
        _write_dataset(tmp_path, case),
        mode="live",
        extractor=extractor,
        judge=RecordingJudge(relation=ClaimRelation.UPDATE),
        retriever=RecordingRetriever(),  # type: ignore[arg-type]
    )

    metrics = report["metrics"]
    assert metrics["incorrect_update_proposal_count"] == 1
    assert metrics["incorrect_update_proposal_denied_count"] == 1
    assert metrics["false_destructive_update_count"] == 0


@pytest.mark.asyncio
async def test_live_repeat_reports_relation_target_and_validator_consistency(
    tmp_path: Path,
) -> None:
    case, first_text, second_text = _two_turn_case(case_id="LIVE-REPEAT")
    extractor = RecordingExtractor(
        {
            first_text: AtomicExtraction(claims=[_claim(first_text, "reply_slow")]),
            second_text: AtomicExtraction(claims=[_claim(second_text, "reply_normal")]),
        }
    )

    report = await evaluate_memory_longtail_realistic(
        _write_dataset(tmp_path, case),
        mode="live",
        repeat=3,
        extractor=extractor,
        judge=RecordingJudge(),
        retriever=RecordingRetriever(),  # type: ignore[arg-type]
    )

    consistency = report["hard_case_consistency"]
    by_case = consistency["by_case"]["LIVE-REPEAT"]
    assert report["repeat"] == 3
    assert len(report["runs"]) == 3
    assert len(extractor.calls) == 6
    assert by_case["run_count"] == 3
    assert by_case["relation_consistency_rate"] == 1.0
    assert by_case["target_consistency_rate"] == 1.0
    assert by_case["validator_consistency_rate"] == 1.0
    assert consistency["relation_consistency_rate"] == 1.0
    assert consistency["relation_expected_claim_count"] == 3
    assert consistency["judge_call_count"] == 3
    assert consistency["judge_completed_count"] == 3
    assert consistency["judge_failure_count"] == 0


def test_repeat_consistency_uses_stable_expected_claim_identity() -> None:
    rows = []
    for run, source_claim_id in enumerate(("claim_001", "claim_1", "claim_1"), start=1):
        rows.append(
            {
                "id": "LIVE-STABLE-CLAIM",
                "run": run,
                "turns": [
                    {
                        "claim_results": [
                            {
                                "id": f"LIVE-STABLE-CLAIM/t2/{source_claim_id}",
                                "expected": {"relation": ClaimRelation.UPDATE.value},
                                "actual": {
                                    "relation": None,
                                    "target_memory_ids": [],
                                    "validator": None,
                                },
                                "live": {
                                    "matched_expected_claim_id": "stable-new-claim",
                                },
                            }
                        ]
                    }
                ],
            }
        )

    consistency = _summarize_consistency(rows)
    by_case = consistency["by_case"]["LIVE-STABLE-CLAIM"]
    assert by_case["relation_consistency_rate"] == 1.0
    assert by_case["target_consistency_rate"] == 1.0
    assert by_case["validator_consistency_rate"] == 1.0
    assert by_case["relation_runs"][0][0]["claim"] == ("LIVE-STABLE-CLAIM/t2/stable-new-claim")


@pytest.mark.asyncio
async def test_live_no_candidate_is_normal_and_not_a_semantic_judge_failure(
    tmp_path: Path,
) -> None:
    case, first_text, second_text = _two_turn_case(case_id="LIVE-NO-CANDIDATE")
    case["turns"][1]["expected"] = {"gate_should_extract": True}
    extractor = RecordingExtractor(
        {
            first_text: AtomicExtraction(claims=[_claim(first_text, "reply_slow")]),
            second_text: AtomicExtraction(claims=[_claim(second_text, "reply_normal")]),
        }
    )
    retriever = EmptyRetriever()
    judge = RecordingJudge()

    report = await evaluate_memory_longtail_realistic(
        _write_dataset(tmp_path, case),
        mode="live",
        extractor=extractor,
        judge=judge,
        retriever=retriever,  # type: ignore[arg-type]
    )

    first_claim = report["cases"][0]["turns"][0]["claim_results"][0]
    second_claim = report["cases"][0]["turns"][1]["claim_results"][0]
    assert first_claim["actual"]["judge_status"] == "not_called"
    assert second_claim["actual"]["judge_status"] == "not_called"
    assert first_claim["passed"] is True
    assert second_claim["passed"] is True
    assert first_claim["error_attribution"] == []
    assert second_claim["error_attribution"] == []
    assert "Semantic Judge" not in report["metrics"]["error_attribution"]
    assert report["metrics"]["semantic_judge_call_count"] == 0
    assert judge.calls == []


def test_live_report_renders_hard_case_aggregate_and_per_case_consistency() -> None:
    rendered = render_longtail_realistic_report(
        {
            "dataset": "live.jsonl",
            "dataset_sha256": "abc",
            "scenario_count": 1,
            "turn_count": 2,
            "evaluation_mode": "shadow_live",
            "store_mutation_permitted": False,
            "metrics": {
                "gate_recall": 1.0,
                "gate_false_negative_by_category": {"complementary": 2},
                "gate_false_negative_by_reason": {"no_durable_signal": 2},
                "retrieval_recall_at_5": 1.0,
                "retrieval_expected_count": 3,
                "relation_accuracy": 0.8,
                "judge_relation_accuracy": 1.0,
                "judge_relation_expected_count": 3,
                "judge_failure_count": 1,
                "update_precision": 0.9,
                "extracted_claim_count": 3,
                "judge_relation_confusion": {
                    "COMPLEMENTARY->UPDATE": 2,
                    "UPDATE->UPDATE": 1,
                },
                "incorrect_update_proposal_count": 2,
                "incorrect_update_proposal_denied_count": 2,
                "false_destructive_update_count": 0,
                "confirmed_overwrite_violation_count": 0,
                "event_over_pattern_violation_count": 0,
                "weak_belief_overwrite_violation_count": 0,
                "error_attribution": {},
                "first_failing_stage": {},
            },
            "hard_case_consistency": {
                "relation_expected_claim_count": 3,
                "judge_call_count": 2,
                "judge_completed_count": 1,
                "judge_failure_count": 1,
                "relation_consistency_rate": 0.875,
                "target_consistency_rate": 1.0,
                "validator_consistency_rate": 1.0,
                "by_case": {
                    "LT-R-004": {
                        "run_count": 3,
                        "relation_consistency_rate": 2 / 3,
                        "target_consistency_rate": 1.0,
                        "validator_consistency_rate": 1.0,
                    }
                },
            },
            "cases": [],
        }
    )

    assert "## Hard-case Repeat Consistency" in rendered
    assert "1 completed / 2 attempted calls across 3" in rendered
    assert "| Aggregate | 0.875 | 1.0 | 1.0 |" in rendered
    assert "| `LT-R-004` | 0.6666666666666666 | 1.0 | 1.0 |" in rendered
    assert "| Extraction | `extracted_claim_count` | 3 |" in rendered
    assert "COMPLEMENTARY->UPDATE (2)" in rendered
    assert "2/2 incorrect UPDATE proposals were denied" in rendered
    assert "completed Judge accuracy is 1.0 across 3" in rendered
    assert "no_durable_signal=2" in rendered
    assert "complementary=2" in rendered
    assert "across 3 eligible target observations" in rendered


def test_live_report_shows_fixture_comparison_and_phase_2c_guard() -> None:
    rendered = render_longtail_realistic_report(
        {
            "dataset": "live.jsonl",
            "dataset_sha256": "abc",
            "scenario_count": 1,
            "turn_count": 2,
            "evaluation_mode": "shadow_live",
            "store_mutation_permitted": False,
            "metrics": {
                "gate_recall": 0.8,
                "retrieval_recall_at_5": 1.0,
                "relation_accuracy": 0.8,
                "update_precision": 0.9,
                "target_memory_precision": 1.0,
                "false_destructive_update_count": 0,
                "confirmed_overwrite_violation_count": 0,
                "event_over_pattern_violation_count": 0,
                "weak_belief_overwrite_violation_count": 0,
                "error_attribution": {},
                "first_failing_stage": {},
            },
            "fixture_comparison": {
                "gate_recall": {"fixture": 0.7872, "live": 0.8},
                "retrieval_recall_at_5": {"fixture": 1.0, "live": 1.0},
                "relation_accuracy": {"fixture": 0.7826, "live": 0.8},
                "update_precision": {"fixture": 1.0, "live": 0.9},
                "target_memory_precision": {"fixture": 1.0, "live": 1.0},
                "false_destructive_update_count": {"fixture": 0, "live": 0},
            },
            "cases": [],
        }
    )

    assert "Fixture vs Live" in rendered
    assert "relation_accuracy" in rendered
    assert "Phase 2C NOT APPROVED" in rendered
    assert "scripted extraction/proposals" not in rendered


def test_live_v2_report_renders_three_way_baseline_comparison() -> None:
    rendered = render_longtail_realistic_report(
        {
            "version": "memory-longtail-realistic-v2",
            "dataset": "live.jsonl",
            "dataset_sha256": "abc",
            "scenario_count": 1,
            "turn_count": 1,
            "evaluation_mode": "shadow_live",
            "store_mutation_permitted": False,
            "metrics": {
                "gate_recall": 0.8,
                "false_destructive_update_count": 0,
                "confirmed_overwrite_violation_count": 0,
                "event_over_pattern_violation_count": 0,
                "weak_belief_overwrite_violation_count": 0,
                "error_attribution": {},
                "first_failing_stage": {},
            },
            "fixture_comparison": {
                "gate_recall": {
                    "fixture": 0.7872,
                    "live_before": 0.7872,
                    "live_after": 0.8,
                    "live": 0.8,
                }
            },
            "cases": [],
        }
    )

    assert "## Fixture vs Live Before vs Live After" in rendered
    assert "| Metric | Fixture | Live Before | Live After |" in rendered
    assert "| `gate_recall` | 0.7872 | 0.7872 | 0.8 |" in rendered
    assert "Phase 2C NOT APPROVED" in rendered

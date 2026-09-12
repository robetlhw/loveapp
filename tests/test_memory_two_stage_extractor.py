import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from loveapp.adapters.memory.two_stage import TwoStageMemoryExtractor
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.memory import MemoryKind, MemorySemanticGateReason
from loveapp.domain.memory_semantic_units import EnrichmentDraft, NewMemoryDraft


class _FakeCompletions:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        response = self.responses[len(self.calls) - 1]
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=json.dumps(response, ensure_ascii=False)),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )


def _fake_extractor(
    responses: list[dict[str, object]],
) -> tuple[TwoStageMemoryExtractor, _FakeCompletions]:
    completions = _FakeCompletions(responses)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        close=_close,
    )
    return (
        TwoStageMemoryExtractor(
            api_key=SecretStr("test"),
            base_url="https://example.invalid",
            model="flash-test",
            client=client,
        ),
        completions,
    )


async def _close() -> None:
    return None


@pytest.mark.asyncio
async def test_two_stage_uses_one_coarse_and_one_batched_detailed_call() -> None:
    extractor, completions = _fake_extractor(
        [
            {
                "should_extract": True,
                "gate_reason": "INTERACTION_PATTERN",
                "propositions": [
                    {
                        "proposition_id": "p1",
                        "evidence_span": "回复越来越慢",
                        "candidate_kinds": ["interaction_pattern"],
                        "semantic_role": "standalone_proposition",
                    }
                ],
                "discarded_spans": [],
            },
            {
                "claims": [
                    {
                        "claim_id": "c1",
                        "kind": "interaction_pattern",
                        "subject": "relationship",
                        "predicate": "response_latency",
                        "summary": "回复越来越慢",
                        "evidence_spans": ["回复越来越慢"],
                        "payload": {
                            "metric": "response_engagement",
                            "direction": "worsening",
                            "current": "slow",
                        },
                    }
                ],
                "discarded_spans": [],
            },
        ]
    )

    result = await extractor.extract(
        "她最近回复越来越慢。",
        reference_time=datetime(2026, 9, 10, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
    )

    assert len(completions.calls) == 2
    assert result.should_extract is True
    assert result.gate_reason == MemorySemanticGateReason.INTERACTION_PATTERN
    assert len(result.claims) == 1
    assert result.claims[0].kind == MemoryKind.INTERACTION_PATTERN
    detailed_payload = json.loads(
        completions.calls[1]["messages"][1]["content"]  # type: ignore[index]
    )
    assert "target_memory_id" not in detailed_payload["coarse_extraction"]

    await extractor.aclose()


@pytest.mark.asyncio
async def test_nested_route_trace_does_not_trigger_stage2_fallback() -> None:
    extractor, completions = _fake_extractor(
        [
            {
                "should_extract": True,
                "gate_reason": "INTERACTION_PATTERN",
                "propositions": [
                    {
                        "proposition_id": "p1",
                        "evidence_span": "回复越来越慢",
                        "candidate_kinds": ["interaction_pattern"],
                        "semantic_role": "new_proposition",
                    }
                ],
                "discarded_spans": [],
            },
            {
                "claims": [
                    {
                        "claim_id": "c1",
                        "kind": "interaction_pattern",
                        "subject": "relationship",
                        "predicate": "response_latency",
                        "summary": "回复越来越慢",
                        "evidence_spans": ["回复越来越慢"],
                        "payload": {"metric": "response_engagement", "direction": "worsening"},
                    }
                ],
                "discarded_spans": [],
            },
        ]
    )
    trace = ExecutionTrace()

    result = await extractor.extract(
        "回复越来越慢",
        reference_time=datetime(2026, 9, 10, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
        trace=trace,
    )

    assert len(completions.calls) == 2
    assert len(result.claims) == 1
    assert extractor.last_diagnostic["final_extractor_used"] == "two_stage_native"
    assert extractor.last_diagnostic["fallback"]["triggered"] is False
    detailed_trace = next(
        record for record in trace.snapshot() if record.name == "memory_two_stage_detailed"
    )
    assert detailed_trace.details["routes"][0]["selected_route"] == "pattern"
    # The nested details must remain JSON serializable for ConversationTurnResult.
    detailed_trace.model_dump_json()

    await extractor.aclose()


@pytest.mark.asyncio
async def test_two_stage_rejects_forbidden_target_and_falls_back_safely() -> None:
    extractor, completions = _fake_extractor(
        [
            {
                "should_extract": True,
                "gate_reason": "RELATIONSHIP_STATE",
                "propositions": [
                    {
                        "proposition_id": "p1",
                        "evidence_span": "现在还在冷战",
                        "candidate_kinds": ["relationship_state"],
                    }
                ],
                "discarded_spans": [],
            },
            {
                "claims": [],
                "target_memory_id": "m1",
                "discarded_spans": [],
            },
        ]
    )

    result = await extractor.extract(
        "我们现在还在冷战。",
        reference_time=datetime(2026, 9, 10, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
    )

    assert len(completions.calls) == 2
    assert result.claims == []
    assert extractor.last_diagnostic["fallback"]["reason_code"] == "UNSUPPORTED_OUTPUT"
    await extractor.aclose()


@pytest.mark.asyncio
async def test_two_stage_unknown_claim_kind_fails_closed_instead_of_becoming_partial() -> None:
    extractor, completions = _fake_extractor(
        [
            {
                "should_extract": True,
                "gate_reason": "COMPOUND_MEMORY",
                "propositions": [
                    {
                        "proposition_id": "p1",
                        "evidence_span": "昨天吵架了",
                        "candidate_kinds": ["interaction_event"],
                    },
                    {
                        "proposition_id": "p2",
                        "evidence_span": "现在还在冷战",
                        "candidate_kinds": ["relationship_state"],
                    },
                ],
                "discarded_spans": [],
            },
            {
                "claims": [
                    {
                        "claim_id": "c1",
                        "kind": "interaction_event",
                        "subject": "relationship",
                        "predicate": "had_argument",
                        "summary": "昨天双方吵架了",
                        "evidence_spans": ["昨天吵架了"],
                    },
                    {
                        "claim_id": "c2",
                        "kind": "unknown_state_kind",
                        "subject": "relationship",
                        "predicate": "cold_war",
                        "summary": "双方仍在冷战",
                        "evidence_spans": ["现在还在冷战"],
                    },
                ],
                "discarded_spans": [],
            },
        ]
    )

    result = await extractor.extract(
        "昨天吵架了，现在还在冷战。",
        reference_time=datetime(2026, 9, 10, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
    )

    assert len(completions.calls) == 2
    assert result.claims == []
    assert extractor.last_diagnostic["stage2"]["parse_success"] is True
    assert extractor.last_diagnostic["stage2"]["validation_success"] is False
    assert extractor.last_diagnostic["fallback"]["reason_code"] == "STAGE2_SCHEMA_ERROR"
    await extractor.aclose()


@pytest.mark.asyncio
async def test_two_stage_gate_negative_skips_detailed_call() -> None:
    extractor, completions = _fake_extractor(
        [
            {
                "should_extract": False,
                "gate_reason": "NO_MEMORY",
                "propositions": [],
                "discarded_spans": [],
            }
        ]
    )

    result = await extractor.extract(
        "今天吃什么？",
        reference_time=datetime(2026, 9, 10, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
    )

    assert len(completions.calls) == 1
    assert result.should_extract is False
    assert result.gate_reason == MemorySemanticGateReason.NO_MEMORY
    await extractor.aclose()


@pytest.mark.asyncio
async def test_two_stage_repairs_bounded_stage_contract_aliases_without_fallback() -> None:
    extractor, completions = _fake_extractor(
        [
            {
                "should_extract": True,
                "gate_reason": "用户陈述了居住地，属于可记忆事实",
                "propositions": [
                    {
                        "text": "我现在住在上海。",
                        "source_span": "我现在住在上海。",
                        "candidate_kinds": ["residence", "personal_fact"],
                        "semantic_role": "state",
                    }
                ],
                "discarded_spans": [],
            },
            {
                "type": "json_object",
                "atomic_extractions": [
                    {
                        "proposition_id": "p1",
                        "claims": [
                            {
                                "subject": "user",
                                "predicate": "resides_in",
                                "object": "上海",
                                "evidence_span": "我现在住在上海。",
                                "kind": "stable_fact",
                            }
                        ],
                    }
                ],
            },
        ]
    )

    result = await extractor.extract(
        "我现在住在上海。",
        reference_time=datetime(2026, 9, 10, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
    )

    assert len(completions.calls) == 2
    assert result.claims[0].kind == MemoryKind.STABLE_FACT
    assert extractor.last_diagnostic["final_extractor_used"] == "two_stage_native"
    assert extractor.last_diagnostic["stage1"]["validation_success"] is True
    assert extractor.last_diagnostic["stage2"]["validation_success"] is True
    assert extractor.last_diagnostic["fallback"]["triggered"] is False
    await extractor.aclose()


@pytest.mark.asyncio
async def test_two_stage_normalizes_negative_gate_reason_to_safe_no_memory() -> None:
    extractor, completions = _fake_extractor(
        [
            {
                "should_extract": False,
                "gate_reason": "CONTEXT_DEPENDENT_REPLY",
                "propositions": [],
                "discarded_spans": ["那我现在应该怎么办？"],
            }
        ]
    )

    result = await extractor.extract(
        "那我现在应该怎么办？",
        reference_time=datetime(2026, 9, 10, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
    )

    assert len(completions.calls) == 1
    assert result.should_extract is False
    assert result.gate_reason == MemorySemanticGateReason.NO_MEMORY
    assert extractor.last_diagnostic["final_extractor_used"] == "two_stage_native"
    await extractor.aclose()


@pytest.mark.asyncio
async def test_two_stage_native_semantic_units_preserve_new_memory_and_enrichment() -> None:
    extractor, completions = _fake_extractor(
        [
            {
                "should_extract": True,
                "gate_reason": "COMPOUND_MEMORY",
                "propositions": [
                    {
                        "proposition_id": "p1",
                        "evidence_span": "因为我迟到了",
                        "candidate_kinds": ["interaction_event"],
                        "semantic_role": "attribute_completion",
                        "target_field_hint": "cause",
                    },
                    {
                        "proposition_id": "p2",
                        "evidence_span": "现在已经不理我了",
                        "candidate_kinds": ["relationship_state"],
                        "semantic_role": "new_proposition",
                    },
                ],
                "discarded_spans": [],
            },
            {
                "semantic_units": [
                    {
                        "semantic_type": "new_memory",
                        "claim_id": "c1",
                        "kind": "relationship_state",
                        "subject": "relationship",
                        "raw_predicate": "contact_unavailable",
                        "predicate": "contact_unavailable",
                        "summary": "对方现在不联系用户",
                        "evidence_spans": ["现在已经不理我了"],
                        "semantic_payload": {
                            "state_dimension": "contact_availability",
                            "state_value": "unavailable",
                        },
                    },
                    {
                        "semantic_type": "enrichment",
                        "unit_id": "e1",
                        "target_kind": "interaction_event",
                        "target_semantic_hint": {"event_type": "conflict"},
                        "attribute_namespace": "canonical",
                        "attribute_name": "cause",
                        "value": {"category": "lateness"},
                        "evidence_span": "因为我迟到了",
                        "confidence": 0.95,
                    },
                ],
                "discarded_spans": [],
            },
        ]
    )

    result = await extractor.extract(
        "因为我迟到了，她现在已经不理我了。",
        reference_time=datetime(2026, 9, 10, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
    )

    assert len(result.claims) == 1
    assert isinstance(result.semantic_units[0], NewMemoryDraft)  # type: ignore[attr-defined]
    assert isinstance(result.semantic_units[1], EnrichmentDraft)  # type: ignore[attr-defined]
    assert result.claims[0].payload["state_value"] == "unavailable"
    assert extractor.last_diagnostic["stage2"]["semantic_units"][1][
        "attribute_name"
    ] == "cause"
    assert len(completions.calls) == 2
    await extractor.aclose()


@pytest.mark.asyncio
async def test_two_stage_legacy_claim_is_exposed_as_new_memory_draft() -> None:
    extractor, _ = _fake_extractor(
        [
            {
                "should_extract": True,
                "gate_reason": "STABLE_FACT",
                "propositions": [
                    {
                        "proposition_id": "p1",
                        "evidence_span": "我住上海",
                        "candidate_kinds": ["stable_fact"],
                        "semantic_role": "standalone_proposition",
                    }
                ],
                "discarded_spans": [],
            },
            {
                "claims": [
                    {
                        "claim_id": "c1",
                        "kind": "stable_fact",
                        "subject": "user",
                        "predicate": "resides_in",
                        "object": "上海",
                        "summary": "用户住在上海",
                        "evidence_spans": ["我住上海"],
                    }
                ],
                "discarded_spans": [],
            },
        ]
    )

    result = await extractor.extract(
        "我住上海。",
        reference_time=datetime(2026, 9, 10, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
    )

    assert isinstance(result.semantic_units[0], NewMemoryDraft)  # type: ignore[attr-defined]
    assert result.semantic_units[0].to_atomic_claim() == result.claims[0]  # type: ignore[attr-defined]
    await extractor.aclose()

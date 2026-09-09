import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from loveapp.adapters.memory.two_stage import TwoStageMemoryExtractor
from loveapp.domain.memory import MemoryKind, MemorySemanticGateReason


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
                    message=SimpleNamespace(
                        content=json.dumps(response, ensure_ascii=False)
                    ),
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

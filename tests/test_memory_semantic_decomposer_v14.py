"""V1.4 semantic decomposer contract regressions.

The tests cover the Stage 1 hand-off and the read-only retrieval boundary.  No
test treats a semantic hint as a resolver target or a Store mutation command.
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from loveapp.adapters.memory.two_stage import (
    TwoStageMemoryExtractor,
    _build_coarse_prompt,
    _build_detailed_prompt,
)
from loveapp.domain.memory import (
    CoarseExtraction,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    OperationHint,
    RelationHint,
    SemanticRole,
    TimeKind,
)

NOW = datetime(2026, 9, 11, tzinfo=UTC)


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


async def _close() -> None:
    return None


def _memory(index: int) -> MemoryItem:
    return MemoryItem(
        id=f"m{index}",
        user_id="u",
        relationship_id="r",
        dedupe_key=f"d{index}",
        status=MemoryStatus.CONFIRMED,
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="relationship",
        summary=f"reply slow pattern {index}",
        original_text="reply slow",
        evidence_spans=["reply slow"],
        time_kind=TimeKind.TIMELESS,
        payload={"metric": "response_engagement", "current": "slow"},
    )


def test_v14_coarse_contract_has_bounded_semantic_hints() -> None:
    proposition = {
        "proposition_id": "p1",
        "evidence_span": "I like coffee",
        "candidate_kinds": ["preference"],
        "semantic_role": "new_proposition",
        "relation_hint": "none",
        "occurrence_group": "g1",
        "operation_hint": "new_like",
        "answered_questions": [],
    }
    coarse = CoarseExtraction.model_validate(
        {"should_extract": True, "propositions": [proposition]}
    )
    item = coarse.propositions[0]
    assert item.semantic_role == SemanticRole.NEW_PROPOSITION
    assert item.relation_hint == RelationHint.NONE
    assert item.occurrence_group == "g1"
    assert item.operation_hint == OperationHint.NEW_LIKE
    assert item.answered_questions == []
    dumped = json.dumps(item.model_dump(mode="json"))
    for forbidden in ("target_memory_id", "mutation_action", "db_patch", "canonical_predicate"):
        assert forbidden not in dumped


def test_v14_accepts_legacy_occurrence_and_question_aliases() -> None:
    item = CoarseExtraction.model_validate(
        {
            "should_extract": True,
            "propositions": [
                {
                    "proposition_id": "p1",
                    "evidence_span": "yesterday we argued",
                    "candidate_kinds": ["interaction_event"],
                    "same_occurrence_group": "event-1",
                    "answered_pending_questions": ["q1"],
                }
            ],
            "answered_pending_questions": ["q1"],
        }
    ).propositions[0]
    assert item.occurrence_group == item.same_occurrence_group == "event-1"
    assert item.answered_questions == item.answered_pending_questions == ["q1"]


def test_v14_prompts_advertise_hints_without_write_authority() -> None:
    coarse = json.loads(
        _build_coarse_prompt(
            "I like coffee",
            reference_time=NOW,
            existing_memories=[],
            conversation_history=[],
            pending_memory_context=None,
        )
    )
    fields = coarse["contract"]["semantic_units"]["fields"]
    assert {"relation_hint", "occurrence_group", "operation_hint", "answered_questions"} <= set(
        fields
    )
    assert set(coarse["contract"]["semantic_roles"]) == {
        "new_proposition",
        "attribute_completion",
        "refinement",
        "correction",
        "uncertain",
    }
    serialized = json.dumps(coarse)
    for forbidden in ("target_memory_id", "mutation_action", "db_patch"):
        assert forbidden in serialized  # contract declares these as forbidden outputs


@pytest.mark.asyncio
async def test_v14_retrieval_is_bounded_and_only_feeds_stage2_context() -> None:
    completions = _FakeCompletions(
        [
            {
                "should_extract": True,
                "gate_reason": "INTERACTION_PATTERN",
                "propositions": [
                    {
                        "proposition_id": "p1",
                        "evidence_span": "reply slow",
                        "candidate_kinds": ["interaction_pattern"],
                        "semantic_role": "new_proposition",
                    }
                ],
            },
            {
                "claims": [
                    {
                        "claim_id": "p1",
                        "kind": "interaction_pattern",
                        "subject": "relationship",
                        "predicate": "response_engagement",
                        "summary": "回复很慢",
                        "evidence_spans": ["reply slow"],
                        "payload": {"metric": "response_engagement", "current": "slow"},
                    }
                ]
            },
        ]
    )
    extractor = TwoStageMemoryExtractor(
        api_key=SecretStr("test"),
        base_url="https://example.invalid",
        model="flash-test",
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=completions),
            close=_close,
        ),
    )
    result = await extractor.extract(
        "reply slow",
        reference_time=NOW,
        existing_memories=[_memory(index) for index in range(7)],
        conversation_history=[],
    )
    assert result.claims
    diagnostic = extractor.last_diagnostic["retrieval"]
    assert diagnostic["called"] is True
    assert diagnostic["failed"] is False
    assert len(diagnostic["candidate_ids"]) <= 5
    detailed = json.loads(completions.calls[1]["messages"][1]["content"])
    assert len(detailed["existing_active_memories"]) <= 5
    assert len(detailed["conversation_context"]["relevant_memories"]) <= 5
    # IDs are not exposed to the model and no route payload is a resolver target.
    assert all("id" not in item for item in detailed["existing_active_memories"])
    await extractor.aclose()


def test_v14_detailed_prompt_keeps_stage2_read_only() -> None:
    coarse = CoarseExtraction.model_validate(
        {
            "should_extract": True,
            "propositions": [
                {
                    "proposition_id": "p1",
                    "evidence_span": "yesterday we argued",
                    "candidate_kinds": ["interaction_event"],
                    "semantic_role": "new_proposition",
                    "relation_hint": "same_event",
                    "operation_hint": "new_like",
                    "occurrence_group": "event-1",
                }
            ],
        }
    )
    prompt = json.loads(
        _build_detailed_prompt(
            "yesterday we argued",
            reference_time=NOW,
            existing_memories=[],
            conversation_history=[],
            pending_memory_context=None,
            coarse=coarse,
        )
    )
    assert prompt["contract"]["context_is_read_only"] is True
    assert "occurrence_group" in prompt["contract"]["context_fields"]
    assert "target_memory_id" in prompt["contract"]["forbidden_outputs"]

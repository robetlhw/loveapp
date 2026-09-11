"""Regression tests for the bounded context-aware extraction hand-off.

These tests exercise context construction and prompt contracts only.  They do
not grant an extractor authority to choose a Memory row or mutate the Store.
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.adapters.memory.openai_compatible import _build_prompt
from loveapp.adapters.memory.two_stage import (
    TwoStageMemoryExtractor,
    _build_coarse_prompt,
    _build_detailed_prompt,
)
from loveapp.application.memory import MemoryService
from loveapp.application.memory_gate import MemoryGate
from loveapp.domain.memory import (
    AtomicExtraction,
    CoarseExtraction,
    MemoryGateDecision,
    MemoryGateReason,
    MemorySemanticGateReason,
    MemoryStatus,
    MessageRole,
    StoredMessage,
)
from loveapp.domain.runtime_context import (
    ConversationContext,
    PendingMemoryContext,
)

NOW = datetime(2026, 9, 11, tzinfo=UTC)


def _message(role: MessageRole, content: str, *, message_id: str) -> StoredMessage:
    return StoredMessage(
        id=message_id,
        conversation_id="context-conversation",
        user_id="context-user",
        relationship_id="context-relationship",
        role=role,
        content=content,
        created_at=NOW,
    )


def _pending(*, topic: str = "conflict", slot: str = "cause") -> PendingMemoryContext:
    return PendingMemoryContext(
        previous_assistant_question="你们这次主要为什么吵架？",
        memory_relevant=True,
        expected_slot=slot,
        topic=topic,
        pending_slot_id="pending-cause-1",
        target_kind="interaction_event",
        target_field=slot,
        created_turn="assistant-turn-1",
    )


def test_conversation_context_extracts_recent_messages_and_pending_question() -> None:
    context = ConversationContext.from_turn(
        "因为钱的问题。",
        conversation_history=[
            _message(MessageRole.USER, "昨天我们吵架了。", message_id="u1"),
            _message(MessageRole.ASSISTANT, "你们这次主要为什么吵架？", message_id="a1"),
        ],
        pending_memory_context=_pending(),
        relevant_memories=[
            {
                "id": "memory-secret",
                "kind": "interaction_event",
                "summary": "昨天发生争吵",
                "payload": {"event_type": "conflict", "target_memory_id": "must-hide"},
            }
        ],
    )

    assert context.previous_user_message == "昨天我们吵架了。"
    assert context.previous_assistant_message == "你们这次主要为什么吵架？"
    assert context.pending_questions[0].question_type == "conflict"
    assert context.active_topic == "conflict"
    assert context.relevant_memories[0]["summary"] == "昨天发生争吵"
    assert "id" not in context.relevant_memories[0]
    assert "target_memory_id" not in context.relevant_memories[0]["payload"]


def test_context_is_read_only_and_contains_no_mutation_authority() -> None:
    context = ConversationContext.from_turn(
        "她今天不理我了。",
        relevant_memories=[
            {
                "kind": "relationship_state",
                "summary": "联系减少",
                "payload": {
                    "memory_id": "m1",
                    "mutation_action": "update",
                    "db_patch": {"status": "superseded"},
                },
            }
        ],
    )
    dumped = context.model_dump(mode="json")
    serialized = json.dumps(dumped, ensure_ascii=False)
    for forbidden in (
        "target_memory_id",
        "target_memory_ids",
        "mutation_action",
        "db_patch",
        "supersedes_id",
    ):
        assert forbidden not in serialized


def test_flash_prompt_includes_unified_conversation_context() -> None:
    context = ConversationContext.from_turn(
        "因为钱的问题。",
        pending_memory_context=_pending(),
        active_topic="conflict",
    )
    payload = json.loads(
        _build_prompt(
            "因为钱的问题。",
            NOW,
            [],
            [],
            conversation_context=context,
        )
    )
    assert payload["conversation_context"] == context.model_dump(mode="json")
    assert payload["conversation_context"]["pending_questions"][0]["target_field"] == "cause"


def test_stage_prompts_declare_origin_and_context_contract() -> None:
    coarse = json.loads(
        _build_coarse_prompt(
            "因为钱的问题。",
            reference_time=NOW,
            existing_memories=[],
            conversation_history=[],
            pending_memory_context=_pending(),
        )
    )
    detailed = json.loads(
        _build_detailed_prompt(
            "因为钱的问题。",
            reference_time=NOW,
            existing_memories=[],
            conversation_history=[],
            pending_memory_context=_pending(),
            coarse=CoarseExtraction(
                should_extract=True,
                propositions=[
                    {
                        "proposition_id": "p1",
                        "evidence_span": "因为钱的问题",
                        "candidate_kinds": ["interaction_event"],
                        "semantic_role": "contextual_completion",
                        "proposition_origin": "answer_to_question",
                        "attributes_hint": ["cause"],
                    }
                ],
            ),
        )
    )
    assert "proposition_origin" in coarse["contract"]["semantic_units"]["fields"]
    assert "answer_to_question" in coarse["contract"]["proposition_origins"]
    assert coarse["conversation_context"]["pending_questions"]
    assert detailed["contract"]["context_is_read_only"] is True
    assert "proposition_origin" in detailed["contract"]["context_fields"]


def test_coarse_extraction_accepts_context_aware_semantic_units_alias() -> None:
    extraction = CoarseExtraction.model_validate(
        {
            "should_extract": True,
            "gate_reason": "CONTEXT_DEPENDENT_REPLY",
            "semantic_units": [
                {
                    "id": "u1",
                    "text": "因为钱的问题",
                    "role": "contextual_completion",
                    "origin": "answer",
                    "candidate_memory_kind": "interaction_event",
                    "attributes": ["cause"],
                }
            ],
        }
    )
    proposition = extraction.semantic_units[0]
    assert proposition.proposition_id == "u1"
    assert proposition.proposition_origin.value == "answer_to_question"
    assert proposition.semantic_role.value == "contextual_completion"
    assert proposition.candidate_kinds[0].value == "interaction_event"
    assert proposition.attributes_hint == ["cause"]


def test_context_aware_coarse_units_preserve_mixed_operations() -> None:
    extraction = CoarseExtraction.model_validate(
        {
            "should_extract": True,
            "semantic_units": [
                {
                    "text": "因为钱的问题",
                    "role": "contextual_completion",
                    "origin": "answer_to_question",
                    "candidate_memory_kind": "interaction_event",
                },
                {
                    "text": "她觉得我不重视她",
                    "role": "belief_extraction",
                    "origin": "spontaneous_disclosure",
                    "candidate_memory_kind": "relationship_state",
                },
                {
                    "text": "今天又因为这个吵了一架",
                    "role": "new_proposition",
                    "origin": "new_occurrence",
                },
            ],
        }
    )
    assert len(extraction.semantic_units) == 3
    assert [unit.proposition_origin.value for unit in extraction.semantic_units] == [
        "answer_to_question",
        "spontaneous_disclosure",
        "new_occurrence",
    ]


class _AlwaysPassGate(MemoryGate):
    def route_v2(self, text: str, **kwargs: object) -> MemoryGateDecision:
        del text
        return MemoryGateDecision(
            should_extract=True,
            reason=MemoryGateReason.DURABLE_SIGNAL,
            semantic_gate_reason=MemorySemanticGateReason.STABLE_FACT,
            l0_route="SEMANTIC_REVIEW",
            pending_memory_context=kwargs.get("pending_memory_context"),
        )


class _ContextRecordingExtractor:
    def __init__(self) -> None:
        self.contexts: list[ConversationContext | None] = []

    async def extract(
        self,
        text: str,
        *,
        reference_time: datetime,
        existing_memories: list[object],
        conversation_history: list[StoredMessage],
        conversation_context: ConversationContext | None = None,
        **kwargs: object,
    ) -> AtomicExtraction:
        del text, reference_time, existing_memories, conversation_history, kwargs
        self.contexts.append(conversation_context)
        return AtomicExtraction(
            should_extract=False,
            gate_reason=MemorySemanticGateReason.NO_MEMORY,
        )


@pytest.mark.asyncio
async def test_memory_service_builds_and_passes_context_without_authority() -> None:
    extractor = _ContextRecordingExtractor()
    service = MemoryService(
        InMemoryMemoryStore(),
        extractor,
        gate=_AlwaysPassGate(),
    )
    await service.remember_text(
        user_id="context-user",
        relationship_id="context-relationship",
        conversation_id="context-conversation",
        text="因为钱的问题。",
        pending_memory_context=_pending(),
        status=MemoryStatus.PROPOSED,
    )

    assert len(extractor.contexts) == 1
    context = extractor.contexts[0]
    assert context is not None
    assert context.current_user_message == "因为钱的问题。"
    assert context.pending_questions[0].target_field == "cause"
    assert "target_memory_id" not in json.dumps(
        context.model_dump(mode="json"), ensure_ascii=False
    )


@pytest.mark.asyncio
async def test_two_stage_receives_same_context_on_coarse_prompt() -> None:
    class _Completions:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def create(self, **kwargs: object) -> object:
            self.calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            content=json.dumps(
                                {
                                    "should_extract": False,
                                    "gate_reason": "NO_MEMORY",
                                    "propositions": [],
                                }
                            ),
                        ),
                    )
                ],
                usage=None,
            )

    completions = _Completions()
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        close=lambda: None,
    )
    extractor = TwoStageMemoryExtractor(
        api_key=SecretStr("test"),
        base_url="https://example.invalid",
        model="flash-test",
        client=client,
    )
    context = ConversationContext.from_turn(
        "今天吃什么？",
        active_topic="casual",
    )
    await extractor.extract(
        "今天吃什么？",
        reference_time=NOW,
        existing_memories=[],
        conversation_history=[],
        conversation_context=context,
    )

    assert len(completions.calls) == 1
    user_prompt = completions.calls[0]["messages"][1]["content"]  # type: ignore[index]
    assert json.loads(user_prompt)["conversation_context"] == context.model_dump(mode="json")

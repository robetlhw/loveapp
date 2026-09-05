from datetime import UTC, datetime

from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.adapters.memory.sqlite import SQLiteMemoryStore
from loveapp.application.memory import MemoryService
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    MemoryGateRoute,
    MemorySemanticGateReason,
    MessageRole,
)


class _SemanticGateExtractor:
    def __init__(self, extraction: AtomicExtraction) -> None:
        self.extraction = extraction
        self.histories: list[list[str]] = []

    async def extract(self, text: str, *, conversation_history, **kwargs):
        del text, kwargs
        self.histories.append([item.content for item in conversation_history])
        return self.extraction


class _ProductionContractExtractor(_SemanticGateExtractor):
    requires_semantic_gate_contract = True


async def test_false_semantic_gate_with_claims_never_reaches_store() -> None:
    text = "她特别喜欢吃辣。"
    claim = AtomicClaim(
        claim_id="contract-violation",
        kind="preference",
        subject="partner",
        predicate="likes_spicy_food",
        summary="对方喜欢吃辣",
        evidence_spans=[text],
    )
    extractor = _SemanticGateExtractor(
        AtomicExtraction(
            should_extract=False,
            gate_reason=MemorySemanticGateReason.NO_MEMORY,
            claims=[claim],
        )
    )
    store = InMemoryMemoryStore()
    service = MemoryService(store, extractor)

    result = await service.remember_text(
        user_id="gate-user",
        relationship_id="gate-relationship",
        conversation_id="gate-conversation",
        text=text,
    )

    assert result.saved == []
    assert result.gate_decision is not None
    assert result.gate_decision.l0_route == MemoryGateRoute.HARD_PASS
    assert result.gate_decision.should_extract is False
    assert result.gate_decision.semantic_gate_should_extract is False
    assert result.gate_decision.semantic_gate_contract_violation is True
    assert result.gate_decision.semantic_gate_contract_violation_reason == "false_with_claims"
    assert (
        await store.list_memories(
            user_id="gate-user",
            relationship_id="gate-relationship",
        )
        == []
    )


async def test_pending_assistant_question_is_loaded_before_v2_routing() -> None:
    extractor = _SemanticGateExtractor(
        AtomicExtraction(
            should_extract=False,
            gate_reason=MemorySemanticGateReason.NO_MEMORY,
        )
    )
    store = InMemoryMemoryStore()
    service = MemoryService(store, extractor, clock=lambda: datetime(2026, 9, 1, tzinfo=UTC))
    scope = {
        "user_id": "context-user",
        "relationship_id": "context-relationship",
        "conversation_id": "context-conversation",
    }
    await service.record_message(
        role=MessageRole.ASSISTANT,
        content="你们这次吵架持续了多久？",
        **scope,
    )

    result = await service.remember_text(text="一周。", **scope)

    assert result.gate_decision is not None
    assert result.gate_decision.l0_route == MemoryGateRoute.CONTEXT_PASS
    assert result.gate_decision.should_extract is False
    assert extractor.histories == [["你们这次吵架持续了多久？"]]


async def test_sqlite_extraction_run_preserves_gate_v2_observability(tmp_path) -> None:
    extractor = _SemanticGateExtractor(
        AtomicExtraction(
            should_extract=True,
            gate_reason=MemorySemanticGateReason.USER_BELIEF,
        )
    )
    store = SQLiteMemoryStore(tmp_path / "gate-v2.db")
    service = MemoryService(store, extractor)

    result = await service.remember_text(
        user_id="sqlite-gate-user",
        relationship_id="sqlite-gate-relationship",
        conversation_id="sqlite-gate-conversation",
        text="我这两个月一直觉得自己在关系里不安全。",
    )
    runs = await store.list_extraction_runs(
        user_id="sqlite-gate-user",
        relationship_id="sqlite-gate-relationship",
        conversation_id="sqlite-gate-conversation",
    )

    assert result.gate_decision is not None
    assert result.gate_decision.extraction_warning == "empty_claims"
    assert len(runs) == 1
    assert runs[0].gate_decision.l0_route == MemoryGateRoute.SEMANTIC_REVIEW
    assert runs[0].gate_decision.semantic_gate_should_extract is True
    assert runs[0].gate_decision.semantic_gate_reason == MemorySemanticGateReason.USER_BELIEF
    assert runs[0].gate_decision.extraction_warning == "empty_claims"


async def test_production_extractor_missing_gate_contract_fails_closed() -> None:
    text = "她特别喜欢吃辣。"
    claim = AtomicClaim(
        claim_id="legacy-shape",
        kind="preference",
        subject="partner",
        predicate="likes_spicy_food",
        summary="对方喜欢吃辣",
        evidence_spans=[text],
    )
    extractor = _ProductionContractExtractor(AtomicExtraction(claims=[claim]))
    store = InMemoryMemoryStore()
    service = MemoryService(store, extractor)

    result = await service.remember_text(
        user_id="contract-user",
        relationship_id="contract-relationship",
        conversation_id="contract-conversation",
        text=text,
    )

    assert result.saved == []
    assert result.gate_decision is not None
    assert result.gate_decision.should_extract is False
    assert result.gate_decision.semantic_gate_contract_violation is True
    assert result.gate_decision.semantic_gate_contract_violation_reason == "missing_gate_contract"

from datetime import UTC, datetime

from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.agents import AdviceAgent
from loveapp.application import MemoryService
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.advice import AdviceRequest, AdviceResponse, RelationshipContext
from loveapp.domain.enums import AdviceScenario
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    EvidenceExplicitness,
    MemoryKind,
    PredicateType,
    TimeKind,
)
from loveapp.safety import SafetyPolicy


class _SequenceExtractor:
    def __init__(self, *extractions: AtomicExtraction) -> None:
        self._extractions = iter(extractions)

    async def extract(self, text: str, **kwargs) -> AtomicExtraction:
        del text, kwargs
        return next(self._extractions)


class _EmptyRetriever:
    async def search(self, **kwargs):
        del kwargs
        return []


class _ContextCapturingComposer:
    def __init__(self) -> None:
        self.contexts: list[RelationshipContext] = []

    async def compose(
        self,
        request,
        scenario,
        context,
        documents,
        conversation_history,
        policy,
        stream_callback=None,
    ) -> AdviceResponse:
        del request, documents, conversation_history, policy, stream_callback
        self.contexts.append(context)
        return AdviceResponse(
            scenario=scenario,
            problem_summary="状态一致性测试",
            assessment=f"当前冲突状态为 {context.relationship_evidence.conflict_status}",
        )


def _conflict_claim(claim_id: str, text: str, value: str) -> AtomicClaim:
    return AtomicClaim(
        claim_id=claim_id,
        kind=MemoryKind.RELATIONSHIP_STATE,
        subject="relationship",
        predicate="relationship.conflict_status",
        summary=text.rstrip("。"),
        evidence_spans=[text.rstrip("。")],
        time_kind=TimeKind.POINT,
        occurred_at=datetime(2026, 9, 1, 9, tzinfo=UTC),
        confidence=0.96,
        importance=5,
        explicitness=EvidenceExplicitness.EXPLICIT,
        payload={"state_dimension": "relationship.conflict_status", "state_value": value},
        raw_predicate="relationship.conflict_status",
        predicate_type=PredicateType.CANONICAL,
        canonical_predicate="relationship.conflict_status",
        state_dimension="relationship.conflict_status",
        state_value=value,
    )


async def test_current_turn_relationship_transition_is_projected_before_advice() -> None:
    first_text = "我们昨天吵架了，现在还在冷战。"
    second_text = "今天已经说开了，她也冷静下来了，我们现在已经和好了。"
    extractor = _SequenceExtractor(
        AtomicExtraction(claims=[_conflict_claim("active", first_text, "active")]),
        AtomicExtraction(claims=[_conflict_claim("resolved", second_text, "resolved")]),
    )
    composer = _ContextCapturingComposer()
    service = MemoryService(InMemoryMemoryStore(), extractor)
    agent = AdviceAgent(_EmptyRetriever(), service, SafetyPolicy(), composer)
    scope = {
        "user_id": "state-consistency-user",
        "relationship_id": "state-consistency-relationship",
        "conversation_id": "state-consistency-conversation",
        "scenario": AdviceScenario.CONFLICT,
    }

    await agent.advise_turn(AdviceRequest(query=first_text, **scope), wait_for_memory=False)
    trace = ExecutionTrace()
    second = await agent.advise_turn(
        AdviceRequest(query=second_text, **scope),
        trace=trace,
        wait_for_memory=False,
    )

    assert composer.contexts[0].relationship_evidence.conflict_status == "active"
    assert composer.contexts[1].relationship_evidence.conflict_status == "resolved"
    assert "resolved" in second.response.assessment
    sync = next(item for item in trace.snapshot() if item.name == "current_turn_state_sync")
    assert sync.details["required"] is True
    assert sync.details["waited"] is True
    assert sync.details["saved_count"] == 1

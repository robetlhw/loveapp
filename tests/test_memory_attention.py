import json
from datetime import UTC, datetime

from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.application.memory import MemoryService
from loveapp.application.memory_repair import parse_memory_response
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    MemoryCandidate,
    MemoryKind,
    MemoryStatus,
    TimeKind,
)


class SequenceExtractor:
    def __init__(self, extractions: list[AtomicExtraction]) -> None:
        self._extractions = iter(extractions)

    async def extract(self, text: str, **kwargs) -> AtomicExtraction:
        del text, kwargs
        return next(self._extractions)


def test_parser_projects_reported_uncertainty_to_registered_state() -> None:
    text = "我还不知道她是不是单身，直接问会不会太唐突？"
    response = {
        "claims": [
            {
                "claim_id": "status-unknown",
                "kind": "stable_fact",
                "subject": "user",
                "predicate": "has_uncertainty",
                "summary": "用户不确定对方是否单身",
                "evidence_spans": ["我还不知道她是不是单身"],
                "payload": {"uncertainty_type": "relationship_status"},
            }
        ],
        "discarded_spans": [
            {"text": "直接问会不会太唐突？", "reason": "consultation_question"}
        ],
    }

    parsed = parse_memory_response(
        json.dumps(response, ensure_ascii=False),
        source_text=text,
    )

    claim = parsed.extraction.claims[0]
    assert claim.kind == MemoryKind.RELATIONSHIP_STATE
    assert claim.subject == "relationship"
    assert claim.payload["state_dimension"] == "partner_relationship_status"
    assert claim.payload["state_value"] == "unknown"
    assert claim.payload["attention_status"] == "unresolved"


async def test_unresolved_issue_is_pinned_ahead_of_ordinary_profiles() -> None:
    now = datetime(2026, 7, 31, 12, tzinfo=UTC)
    store = InMemoryMemoryStore()
    service = MemoryService(
        store,
        SequenceExtractor([]),
        context_limit=3,
        clock=lambda: now,
    )
    await service.ensure_context("attention-user", "partner")
    unresolved = await store.save_memory(
        user_id="attention-user",
        relationship_id="partner",
        candidate=MemoryCandidate(
            kind=MemoryKind.STABLE_FACT,
            subject="user",
            summary="用户尚未确认对方当前的感情状态",
            original_text="我还不知道她是不是单身",
            time_kind=TimeKind.TIMELESS,
            importance=3,
            payload={
                "predicate": "has_uncertainty",
                "uncertainty_type": "relationship_status",
            },
        ),
    )
    for index in range(8):
        await store.save_memory(
            user_id="attention-user",
            relationship_id="partner",
            candidate=MemoryCandidate(
                kind=MemoryKind.STABLE_FACT,
                subject="user",
                summary=f"用户的普通画像信息 {index}",
                original_text=f"普通画像信息 {index}",
                time_kind=TimeKind.TIMELESS,
                importance=5,
                payload={"predicate": f"profile_fact_{index}"},
            ),
        )

    context = await service.get_context(
        "attention-user",
        "partner",
        query="下次见面应该安排什么活动",
    )

    selected_ids = {item.id for item in context.remembered_items}
    assert unresolved.item.id in selected_ids
    active = {item.id: item.attention_reason for item in context.active_context}
    assert active[unresolved.item.id] == "unresolved"


async def test_resolved_relationship_status_supersedes_unknown_state() -> None:
    now = datetime(2026, 7, 31, 12, tzinfo=UTC)
    unknown = AtomicClaim(
        claim_id="status-unknown",
        kind=MemoryKind.STABLE_FACT,
        subject="user",
        predicate="has_uncertainty",
        summary="用户不确定对方是否单身",
        evidence_spans=["我还不知道她是不是单身"],
        payload={"uncertainty_type": "relationship_status"},
    )
    single = AtomicClaim(
        claim_id="status-single",
        kind=MemoryKind.RELATIONSHIP_STATE,
        subject="relationship",
        predicate="has_state",
        summary="对方明确表示目前单身",
        evidence_spans=["她明确告诉我自己目前单身"],
        payload={
            "state_dimension": "partner_relationship_status",
            "state_value": "single",
        },
    )
    store = InMemoryMemoryStore()
    service = MemoryService(
        store,
        SequenceExtractor(
            [
                AtomicExtraction(claims=[unknown]),
                AtomicExtraction(claims=[single]),
            ]
        ),
        clock=lambda: now,
    )
    scope = {
        "user_id": "status-user",
        "relationship_id": "partner",
        "conversation_id": "status-conversation",
    }

    await service.remember_text(text="我还不知道她是不是单身", **scope)
    await service.remember_text(text="她明确告诉我自己目前单身", **scope)

    memories = await store.list_memories(
        user_id="status-user",
        relationship_id="partner",
        limit=20,
    )
    by_value = {item.payload.get("state_value"): item for item in memories}
    assert by_value["unknown"].kind == MemoryKind.RELATIONSHIP_STATE
    assert by_value["unknown"].status == MemoryStatus.SUPERSEDED
    assert by_value["single"].status == MemoryStatus.PROPOSED

    context = await service.get_context("status-user", "partner")
    assert [item.payload.get("state_value") for item in context.current_state] == [
        "single"
    ]
    assert all(
        item.payload.get("state_value") != "unknown"
        for item in context.active_context
    )

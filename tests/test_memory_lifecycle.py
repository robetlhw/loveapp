import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from loveapp.adapters.memory import InMemoryMemoryStore, SQLiteMemoryStore
from loveapp.application.memory import MemoryService
from loveapp.application.memory_repair import parse_memory_response
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    MemoryCandidate,
    MemoryKind,
    MemoryStatus,
    RelationshipImpact,
    TimeKind,
)


class SequenceExtractor:
    def __init__(self, extractions: list[AtomicExtraction]) -> None:
        self._extractions = iter(extractions)

    async def extract(self, text: str, **kwargs) -> AtomicExtraction:
        del text, kwargs
        return next(self._extractions)


async def test_apology_supersedes_contact_outage_but_keeps_conflict_history() -> None:
    clock_value = [datetime(2026, 7, 31, 11, tzinfo=UTC)]
    conflict_text = "我俩吵架了，她不理我了，电话也打不通"
    apology_text = "她刚刚回我消息了，还给我道歉了"
    extractor = SequenceExtractor(
        [
            AtomicExtraction(
                claims=[
                    _claim(
                        claim_id="conflict",
                        kind=MemoryKind.INTERACTION_EVENT,
                        predicate="had_conflict",
                        summary="用户和对方发生争吵",
                        evidence="我俩吵架了",
                        impact=RelationshipImpact.DAMAGING,
                    ),
                    _claim(
                        claim_id="contact-outage",
                        kind=MemoryKind.INTERACTION_PATTERN,
                        subject="partner",
                        predicate="ignoring_user",
                        summary="对方不回复用户且电话无法接通",
                        evidence="她不理我了",
                        impact=RelationshipImpact.DAMAGING,
                        payload={
                            "metric": "contact_availability",
                            "current": "unavailable",
                            "frequency": "ongoing",
                        },
                    ),
                ]
            ),
            AtomicExtraction(
                claims=[
                    _claim(
                        claim_id="apology",
                        kind=MemoryKind.INTERACTION_EVENT,
                        predicate="partner_apologized",
                        summary="对方回复消息并向用户道歉",
                        evidence="她刚刚回我消息了，还给我道歉了",
                        impact=RelationshipImpact.IMPROVING,
                    )
                ]
            ),
        ]
    )
    store = InMemoryMemoryStore()
    service = MemoryService(store, extractor, clock=lambda: clock_value[0])
    scope = {
        "user_id": "lifecycle-user",
        "relationship_id": "partner",
        "conversation_id": "conflict-conversation",
    }

    await service.remember_text(text=conflict_text, **scope)
    clock_value[0] = datetime(2026, 7, 31, 12, tzinfo=UTC)
    await service.remember_text(text=apology_text, **scope)

    memories = await store.list_memories(
        user_id="lifecycle-user",
        relationship_id="partner",
        limit=20,
    )
    by_predicate = {item.payload.get("predicate"): item for item in memories}
    assert by_predicate["ignoring_user"].status == MemoryStatus.SUPERSEDED
    assert by_predicate["had_conflict"].status == MemoryStatus.PROPOSED
    assert by_predicate["partner_apologized"].status == MemoryStatus.PROPOSED

    context = await service.get_context(
        "lifecycle-user",
        "partner",
        query="她道歉后我该怎么修复关系",
    )
    summaries = [item.summary for item in context.remembered_items]
    assert "对方不回复用户且电话无法接通" not in summaries
    assert "对方回复消息并向用户道歉" in summaries
    assert "用户和对方发生争吵" in summaries
    assert [item.payload.get("predicate") for item in context.recent_events] == [
        "partner_apologized",
        "had_conflict",
    ]


async def test_get_context_reconciles_legacy_transition_and_preference_shape() -> None:
    store = InMemoryMemoryStore()
    service = MemoryService(store, SequenceExtractor([]))
    await service.ensure_context("legacy-user", "partner")
    unavailable = await store.save_memory(
        user_id="legacy-user",
        relationship_id="partner",
        candidate=MemoryCandidate(
            kind=MemoryKind.INTERACTION_PATTERN,
            subject="partner",
            summary="对方暂时不回复用户",
            original_text="她不理我了",
            payload={
                "predicate": "ignoring_user",
                "metric": "contact_availability",
                "current": "unavailable",
            },
        ),
    )
    await store.save_memory(
        user_id="legacy-user",
        relationship_id="partner",
        candidate=MemoryCandidate(
            kind=MemoryKind.INTERACTION_EVENT,
            subject="relationship",
            summary="对方已经回复并道歉",
            original_text="她回我消息并道歉了",
            occurred_at=unavailable.item.updated_at + timedelta(microseconds=1),
            payload={"predicate": "partner_apologized"},
        ),
    )
    await store.save_memory(
        user_id="legacy-user",
        relationship_id="partner",
        candidate=MemoryCandidate(
            kind=MemoryKind.STABLE_FACT,
            subject="partner",
            summary="对方比较节俭",
            original_text="她比较节俭",
            time_kind=TimeKind.TIMELESS,
            payload={
                "predicate": "is_frugal",
                "preference": "节俭",
                "preference_type": "habit",
            },
        ),
    )

    context = await service.get_context("legacy-user", "partner")

    stale = await store.get_memory(unavailable.item.id, "legacy-user")
    assert stale is not None and stale.status == MemoryStatus.SUPERSEDED
    assert context.partner_preferences == ["节俭"]
    assert all(item.id != unavailable.item.id for item in context.remembered_items)


def test_unscheduled_committed_plan_is_repaired_to_action_intent() -> None:
    text = "我决定先请她吃顿饭，然后再认真聊一下消费观的事情"
    raw = {
        "claims": [
            {
                "claim_id": "meal",
                "kind": "planned_event",
                "subject": "user",
                "predicate": "invite_partner_to_meal",
                "summary": "用户决定请对方吃饭以修复关系",
                "evidence_spans": ["我决定先请她吃顿饭"],
                "payload": {"event_status": "tentative"},
            },
            {
                "claim_id": "conversation",
                "kind": "planned_event",
                "subject": "relationship",
                "predicate": "discuss_consumption_values",
                "summary": "用户计划之后与对方讨论消费观",
                "evidence_spans": ["然后再认真聊一下消费观的事情"],
                "payload": {"event_status": "tentative"},
            },
        ],
        "discarded_spans": [],
    }

    parsed = parse_memory_response(json.dumps(raw, ensure_ascii=False), source_text=text)

    assert [claim.kind for claim in parsed.extraction.claims] == [
        MemoryKind.ACTION_INTENT,
        MemoryKind.ACTION_INTENT,
    ]
    assert "unscheduled_plan_to_action_intent" in parsed.repair_steps


async def test_action_intent_gets_bounded_lifetime_and_separate_context_bucket() -> None:
    now = datetime(2026, 7, 31, 12, tzinfo=UTC)
    intent = _claim(
        claim_id="meal-intent",
        kind=MemoryKind.ACTION_INTENT,
        subject="user",
        predicate="invite_partner_to_meal",
        summary="用户决定请对方吃饭修复关系",
        evidence="我决定先请她吃顿饭",
        payload={"event_status": "intended"},
    )
    service = MemoryService(
        InMemoryMemoryStore(),
        SequenceExtractor([AtomicExtraction(claims=[intent])]),
        clock=lambda: now,
    )

    result = await service.remember_text(
        user_id="intent-user",
        relationship_id="partner",
        conversation_id="intent-conversation",
        text="我决定先请她吃顿饭",
    )
    context = await service.get_context("intent-user", "partner")

    saved = result.saved[0].item
    assert saved.kind == MemoryKind.ACTION_INTENT
    assert saved.expires_at == datetime(2026, 8, 14, 12, tzinfo=UTC)
    assert [item.id for item in context.action_intents] == [saved.id]
    assert context.planned_events == []


async def test_sqlite_persists_cross_predicate_state_transition(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "lifecycle.db")
    extractor = SequenceExtractor(
        [
            AtomicExtraction(
                claims=[
                    _claim(
                        claim_id="unavailable",
                        kind=MemoryKind.INTERACTION_EVENT,
                        subject="partner",
                        predicate="ignoring_user",
                        summary="对方暂时不回复用户",
                        evidence="她不理我了",
                    )
                ]
            ),
            AtomicExtraction(
                claims=[
                    _claim(
                        claim_id="replied",
                        kind=MemoryKind.INTERACTION_EVENT,
                        predicate="partner_replied",
                        summary="对方已经回复用户",
                        evidence="她刚刚回我消息了",
                    )
                ]
            ),
        ]
    )
    service = MemoryService(store, extractor)
    scope = {
        "user_id": "sqlite-lifecycle-user",
        "relationship_id": "partner",
        "conversation_id": "sqlite-lifecycle-conversation",
    }

    first = await service.remember_text(text="她不理我了，电话也打不通", **scope)
    await service.remember_text(text="她刚刚回我消息了", **scope)

    stale = await store.get_memory(first.saved[0].item.id, "sqlite-lifecycle-user")
    assert stale is not None and stale.status == MemoryStatus.SUPERSEDED
    context = await service.get_context("sqlite-lifecycle-user", "partner")
    assert [item.payload.get("predicate") for item in context.recent_events] == [
        "partner_replied"
    ]


def _claim(
    *,
    claim_id: str,
    kind: MemoryKind,
    predicate: str,
    summary: str,
    evidence: str,
    subject: str = "relationship",
    impact: RelationshipImpact = RelationshipImpact.UNCLEAR,
    payload: dict | None = None,
) -> AtomicClaim:
    return AtomicClaim(
        claim_id=claim_id,
        kind=kind,
        subject=subject,
        predicate=predicate,
        summary=summary,
        evidence_spans=[evidence],
        relationship_impact=impact,
        confidence=0.95,
        payload=payload or {},
    )

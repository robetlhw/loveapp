from loveapp.adapters.memory.in_memory import InMemoryMemoryStore
from loveapp.application.memory import MemoryService, NoOpMemoryExtractor
from loveapp.application.memory_gate import MemoryGate
from loveapp.domain.enums import RelationshipStage
from loveapp.domain.memory import (
    MemoryCandidate,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    MessageRole,
    StoredMessage,
    TimeKind,
)


def _user_message(content: str, *, message_id: str = "m1") -> StoredMessage:
    return StoredMessage(
        id=message_id,
        conversation_id="c1",
        user_id="u1",
        relationship_id="r1",
        role=MessageRole.USER,
        content=content,
    )


def test_contextual_acceptance_requires_a_confession_antecedent() -> None:
    history = [_user_message("我准备和她表白")]

    decision = MemoryGate().evaluate(
        "她回我了，她同意，真好",
        conversation_history=history,
    )

    assert decision.should_extract is True
    assert "contextual_relationship_event" in decision.signals

    direct = MemoryGate().evaluate("她同意了我的表白")
    assert direct.should_extract is True

    unrelated = MemoryGate().evaluate(
        "她同意了",
        conversation_history=[_user_message("我邀请她周末一起吃饭")],
    )
    assert unrelated.should_extract is False


def test_confession_fact_survives_a_follow_up_question_suffix() -> None:
    history = [
        _user_message("我准备向她表白，你有啥建议帮助吗"),
    ]

    decision = MemoryGate().evaluate(
        "她同意了",
        conversation_history=history,
    )

    assert decision.should_extract is True
    assert "contextual_confession_acceptance" in decision.signals


def test_bare_acceptance_stays_ambiguous_after_a_new_message_action() -> None:
    history = [
        _user_message("我准备向她表白，你有啥建议帮助吗", message_id="confession"),
        _user_message("我现在去给她发消息", message_id="message-action"),
    ]

    decision = MemoryGate().evaluate(
        "她同意了",
        conversation_history=history,
    )

    assert decision.should_extract is False


async def test_contextual_acceptance_saves_event_updates_stage_and_supersedes_plan() -> None:
    store = InMemoryMemoryStore()
    service = MemoryService(store, NoOpMemoryExtractor())
    pending = await store.save_memory(
        user_id="u1",
        relationship_id="r1",
        candidate=MemoryCandidate(
            kind=MemoryKind.PLANNED_EVENT,
            subject="user",
            summary="用户准备向对方表白",
            original_text="我准备和她表白",
            evidence_spans=["我准备和她表白"],
            time_kind=TimeKind.POINT,
            perspective=MemoryPerspective.USER_REPORTED,
            confidence=0.95,
            importance=5,
            payload={
                "predicate": "will_confess",
                "object": "partner",
                "event_status": "planned",
                "temporal_expression": "近期",
            },
        ),
    )
    antecedent = await service.record_message(
        user_id="u1",
        relationship_id="r1",
        conversation_id="c1",
        role=MessageRole.USER,
        content="我准备和她表白",
    )
    current = await service.record_message(
        user_id="u1",
        relationship_id="r1",
        conversation_id="c1",
        role=MessageRole.USER,
        content="她回我了，她同意，真好",
    )

    result = await service.remember_recorded_message(
        message=current,
        text=current.content,
        conversation_history=[antecedent],
    )

    assert result.gate_decision is not None
    assert result.gate_decision.should_extract is True
    assert len(result.saved) == 1
    event = result.saved[0].item
    assert event.payload["predicate"] == "confession_succeeded"
    assert event.payload["antecedent_message_id"] == antecedent.id
    assert event.supersedes_id == pending.item.id

    context = await service.get_context("u1", "r1")
    assert context.relationship_stage == RelationshipStage.DATING
    superseded = await store.get_memory(pending.item.id, "u1")
    assert superseded is not None
    assert superseded.status == MemoryStatus.SUPERSEDED

    # A repeated confirmation is idempotent for the relationship-state event.
    duplicate_message = await service.record_message(
        user_id="u1",
        relationship_id="r1",
        conversation_id="c1",
        role=MessageRole.USER,
        content="她同意了",
    )
    duplicate = await service.remember_recorded_message(
        message=duplicate_message,
        text=duplicate_message.content,
        conversation_history=[antecedent],
    )
    assert duplicate.saved
    events = await store.list_memories(
        user_id="u1",
        relationship_id="r1",
        kind=MemoryKind.INTERACTION_EVENT,
        status=MemoryStatus.CONFIRMED,
    )
    assert (
        len(
            [
                item
                for item in events
                if item.payload.get("predicate") == "confession_succeeded"
            ]
        )
        == 1
    )


class FailingExtractor:
    async def extract(self, text, **kwargs):
        del text, kwargs
        raise TimeoutError("simulated extraction timeout")


async def test_contextual_event_survives_extractor_failure() -> None:
    store = InMemoryMemoryStore()
    service = MemoryService(store, FailingExtractor())
    antecedent = await service.record_message(
        user_id="u2",
        relationship_id="r2",
        conversation_id="c2",
        role=MessageRole.USER,
        content="我打算向她表白",
    )
    current = await service.record_message(
        user_id="u2",
        relationship_id="r2",
        conversation_id="c2",
        role=MessageRole.USER,
        content="她答应了，我很开心",
    )

    result = await service.remember_recorded_message(
        message=current,
        text=current.content,
        conversation_history=[antecedent],
    )

    assert result.extraction_error == "simulated extraction timeout"
    assert result.saved
    context = await service.get_context("u2", "r2")
    assert context.relationship_stage == RelationshipStage.DATING


async def test_confession_intent_is_saved_as_short_lived_plan_without_an_extra_model_call() -> None:
    store = InMemoryMemoryStore()
    service = MemoryService(store, NoOpMemoryExtractor())

    result = await service.remember_text(
        user_id="pending-user",
        relationship_id="pending-relationship",
        conversation_id="pending-conversation",
        text="我准备向她表白，你有啥建议帮助吗",
    )

    assert result.saved
    planned = [saved.item for saved in result.saved if saved.item.kind == MemoryKind.PLANNED_EVENT]
    assert len(planned) == 1
    assert planned[0].payload["predicate"] == "will_confess"
    assert planned[0].expires_at is not None
    assert planned[0].expires_at > planned[0].created_at


async def test_real_conversation_does_not_promote_bare_acceptance_but_accepts_explicit_confession(
) -> None:
    store = InMemoryMemoryStore()
    service = MemoryService(store, NoOpMemoryExtractor())
    scope = {
        "user_id": "sequence-user",
        "relationship_id": "sequence-relationship",
        "conversation_id": "sequence-conversation",
    }

    await service.remember_text(text="我准备向她表白，你有啥建议帮助吗", **scope)
    await service.remember_text(text="我现在去给她发消息", **scope)
    ambiguous = await service.remember_text(text="她同意了", **scope)

    assert ambiguous.saved == []
    assert (
        await service.get_context("sequence-user", "sequence-relationship")
    ).relationship_stage == RelationshipStage.UNKNOWN

    explicit = await service.remember_text(text="她同意了我的表白", **scope)

    assert any(
        saved.item.payload.get("predicate") == "confession_succeeded"
        for saved in explicit.saved
    )
    assert (
        await service.get_context("sequence-user", "sequence-relationship")
    ).relationship_stage == RelationshipStage.DATING

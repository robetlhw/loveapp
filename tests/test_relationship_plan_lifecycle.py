from datetime import UTC, datetime
from pathlib import Path

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore, SQLiteMemoryStore
from loveapp.application.memory import MemoryService
from loveapp.application.memory_gate import MemoryGate
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    MemoryCandidate,
    MemoryKind,
    MemoryStatus,
    TimeKind,
)
from loveapp.domain.relationship_plan import (
    PlanStatus,
    RelationshipPlan,
    has_retrospective_event_semantics,
    match_plan_transitions,
)


class SequenceExtractor:
    def __init__(self, extractions: list[AtomicExtraction]) -> None:
        self._extractions = iter(extractions)

    async def extract(self, text: str, **kwargs) -> AtomicExtraction:
        del text, kwargs
        return next(self._extractions)


async def test_multiturn_completed_event_closes_plan_and_linked_intent() -> None:
    clock = [datetime(2026, 8, 2, 10, tzinfo=UTC)]
    planned_text = "下周六我和她约好一起参加陶艺体验课，我准备提前确认交通。"
    completed_text = "上回陶艺课结束后，她还主动提议下次一起做杯子。"
    extractor = SequenceExtractor(
        [
            AtomicExtraction(
                claims=[
                    _claim(
                        claim_id="plan",
                        kind=MemoryKind.PLANNED_EVENT,
                        predicate="attend_activity",
                        summary="双方下周六约好参加陶艺体验课",
                        evidence="下周六我和她约好一起参加陶艺体验课",
                        period_start=datetime(2026, 8, 8, 14, tzinfo=UTC),
                        payload={
                            "event_status": "confirmed",
                            "activity_type": "陶艺体验课",
                            "participants": ["user", "partner"],
                        },
                    ),
                    _claim(
                        claim_id="intent",
                        kind=MemoryKind.ACTION_INTENT,
                        predicate="confirm_transport",
                        summary="用户准备为陶艺体验课提前确认交通",
                        evidence="我准备提前确认交通",
                        payload={
                            "event_status": "intended",
                            "activity_type": "陶艺体验课",
                            "participants": ["user", "partner"],
                        },
                    ),
                ]
            ),
            AtomicExtraction(
                claims=[
                    _claim(
                        claim_id="completed",
                        kind=MemoryKind.INTERACTION_EVENT,
                        predicate="attended_activity",
                        summary="双方已经参加陶艺体验课",
                        evidence="上回陶艺课结束后",
                        occurred_at=datetime(2026, 8, 8, 17, tzinfo=UTC),
                        payload={
                            "event_status": "completed",
                            "activity_type": "陶艺体验课",
                            "participants": ["user", "partner"],
                        },
                    ),
                    _claim(
                        claim_id="follow-up",
                        kind=MemoryKind.INTERACTION_EVENT,
                        predicate="partner_suggested_future_activity",
                        summary="对方主动提议下次一起做杯子",
                        evidence="她还主动提议下次一起做杯子",
                    ),
                ]
            ),
        ]
    )
    store = InMemoryMemoryStore()
    service = MemoryService(store, extractor, clock=lambda: clock[0])
    scope = {
        "user_id": "plan-user",
        "relationship_id": "partner",
        "conversation_id": "plan-conversation",
    }

    first = await service.remember_text(text=planned_text, **scope)
    plans = await store.list_relationship_plans(
        user_id="plan-user",
        relationship_id="partner",
    )

    assert len(plans) == 1
    assert plans[0].status == PlanStatus.CONFIRMED
    assert plans[0].source_memory_id == first.saved[0].item.id
    assert first.saved[0].item.payload["plan_id"] == plans[0].plan_id
    first_context = await service.get_context("plan-user", "partner")
    assert [plan.plan_id for plan in first_context.active_plans] == [plans[0].plan_id]
    assert len(first_context.action_intents) == 1

    clock[0] = datetime(2026, 8, 8, 18, tzinfo=UTC)
    second = await service.remember_text(text=completed_text, **scope)

    completed_plan = await store.get_relationship_plan(plans[0].plan_id, "plan-user")
    assert completed_plan is not None
    assert completed_plan.status == PlanStatus.COMPLETED
    assert completed_plan.completed_at == datetime(2026, 8, 8, 17, tzinfo=UTC)
    completion_memory = next(
        saved.item
        for saved in second.saved
        if saved.item.payload.get("event_status") == "completed"
    )
    assert completion_memory.payload["completes_plan_id"] == plans[0].plan_id
    source = await store.get_memory(first.saved[0].item.id, "plan-user")
    assert source is not None and source.status == MemoryStatus.SUPERSEDED
    intent = await store.get_memory(first.saved[1].item.id, "plan-user")
    assert intent is not None and intent.status == MemoryStatus.SUPERSEDED
    final_context = await service.get_context("plan-user", "partner")
    assert final_context.active_plans == []
    assert final_context.planned_events == []
    assert final_context.action_intents == []
    assert any(item.id == completion_memory.id for item in final_context.recent_events)


def test_explicit_plan_id_is_authoritative_for_terminal_event() -> None:
    plans = [
        _plan("plan-a", "读书会"),
        _plan("plan-b", "城市漫步"),
    ]
    event = MemoryCandidate(
        kind=MemoryKind.INTERACTION_EVENT,
        subject="relationship",
        summary="双方完成了临时调整后的活动",
        original_text="活动已经结束了",
        payload={
            "event_status": "completed",
            "activity_type": "临时活动",
            "completes_plan_id": "plan-b",
        },
    )

    transitions = match_plan_transitions([event], plans)

    assert len(transitions) == 1
    assert transitions[0].plan_id == "plan-b"
    assert transitions[0].reason == "explicit_plan_id"


def test_structured_match_uses_activity_participants_and_time() -> None:
    plans = [
        _plan(
            "plan-a",
            "摄影展",
            scheduled_start=datetime(2026, 8, 9, 14, tzinfo=UTC),
        ),
        _plan(
            "plan-b",
            "桌游聚会",
            scheduled_start=datetime(2026, 8, 10, 14, tzinfo=UTC),
        ),
    ]
    event = MemoryCandidate(
        kind=MemoryKind.INTERACTION_EVENT,
        subject="relationship",
        summary="双方参加了桌游聚会",
        original_text="那天桌游聚会结束后我们又聊了一会儿",
        occurred_at=datetime(2026, 8, 10, 17, tzinfo=UTC),
        payload={
            "event_status": "completed",
            "activity_type": "桌游聚会",
            "participants": ["user", "partner"],
        },
    )

    transitions = match_plan_transitions([event], plans)

    assert [transition.plan_id for transition in transitions] == ["plan-b"]


def test_ambiguous_event_does_not_close_one_of_multiple_plans() -> None:
    plans = [_plan("plan-a", "展览"), _plan("plan-b", "展览")]
    event = MemoryCandidate(
        kind=MemoryKind.INTERACTION_EVENT,
        subject="relationship",
        summary="双方已经看完展览",
        original_text="上回看完展览后我们去喝了咖啡",
        payload={
            "event_status": "completed",
            "activity_type": "展览",
            "participants": ["user", "partner"],
        },
    )

    assert match_plan_transitions([event], plans) == []


async def test_current_retrospective_fact_suppresses_stale_plan_without_mutating_it() -> None:
    store = InMemoryMemoryStore()
    service = MemoryService(store, SequenceExtractor([]))
    saved = await store.save_memory(
        user_id="suppression-user",
        relationship_id="partner",
        candidate=MemoryCandidate(
            kind=MemoryKind.PLANNED_EVENT,
            subject="relationship",
            summary="双方计划参加咖啡品鉴课",
            original_text="周末我们准备参加咖啡品鉴课",
            period_start=datetime(2099, 8, 8, 14, tzinfo=UTC),
            expires_at=datetime(2099, 8, 9, 14, tzinfo=UTC),
            payload={
                "event_status": "confirmed",
                "activity_type": "咖啡品鉴课",
                "participants": ["user", "partner"],
            },
        ),
    )

    historical = await service.get_context(
        "suppression-user",
        "partner",
        query="上回咖啡品鉴课结束后，她主动送我回家。",
    )
    future = await service.get_context(
        "suppression-user",
        "partner",
        query="等到咖啡品鉴课结束后，我们再去附近散步。",
    )
    plans = await store.list_relationship_plans(
        user_id="suppression-user",
        relationship_id="partner",
    )

    assert historical.active_plans == []
    assert historical.planned_events == []
    assert [item.id for item in future.planned_events] == [saved.item.id]
    assert plans[0].status == PlanStatus.CONFIRMED


async def test_legacy_retrospective_memory_reconciles_only_explicitly() -> None:
    store = InMemoryMemoryStore()
    planned = await store.save_memory(
        user_id="legacy-plan-user",
        relationship_id="partner",
        candidate=MemoryCandidate(
            kind=MemoryKind.PLANNED_EVENT,
            subject="relationship",
            summary="双方计划参加观鸟活动",
            original_text="我们准备一起参加观鸟活动",
            payload={
                "event_status": "planned",
                "activity": "观鸟活动",
            },
        ),
    )
    event = await store.save_memory(
        user_id="legacy-plan-user",
        relationship_id="partner",
        candidate=MemoryCandidate(
            kind=MemoryKind.INTERACTION_EVENT,
            subject="relationship",
            summary="观鸟活动回来后，对方主动分享了照片",
            original_text="观鸟活动回来后，她主动把照片发给了我",
            payload={"predicate": "partner_shared_photos"},
        ),
    )
    service = MemoryService(store, SequenceExtractor([]))

    context = await service.get_context(
        "legacy-plan-user",
        "partner",
        query="这段互动说明了什么？",
    )
    plans_before = await store.list_relationship_plans(
        user_id="legacy-plan-user",
        relationship_id="partner",
        read_only=True,
    )
    source_before = await store.get_memory(planned.item.id, "legacy-plan-user")

    assert plans_before[0].status == PlanStatus.PROPOSED
    assert source_before is not None and source_before.status == MemoryStatus.PROPOSED
    assert context.active_plans == []
    assert context.planned_events == []

    memories = await store.list_memories(
        user_id="legacy-plan-user",
        relationship_id="partner",
    )
    plans = await service.reconcile_relationship_plans(
        user_id="legacy-plan-user",
        relationship_id="partner",
        memories=memories,
        plans=plans_before,
    )
    source = await store.get_memory(planned.item.id, "legacy-plan-user")

    assert plans[0].status == PlanStatus.COMPLETED
    assert plans[0].payload["terminal_event_memory_id"] == event.item.id
    assert source is not None and source.status == MemoryStatus.SUPERSEDED


def test_old_event_does_not_close_a_future_recurrence_with_same_activity() -> None:
    plan = _plan(
        "future-plan",
        "手作课程",
        scheduled_start=datetime(2099, 8, 10, 14, tzinfo=UTC),
    )
    event = MemoryCandidate(
        kind=MemoryKind.INTERACTION_EVENT,
        subject="relationship",
        summary="双方之前参加过手作课程",
        original_text="之前的手作课程结束后我们一起吃了饭",
        occurred_at=datetime(2026, 8, 1, 17, tzinfo=UTC),
        payload={
            "event_status": "completed",
            "activity_type": "手作课程",
            "participants": ["user", "partner"],
        },
    )

    assert match_plan_transitions([event], [plan]) == []


@pytest.mark.parametrize(
    "text",
    [
        "上回读书会结束后，我们一起走到了地铁站。",
        "之前那次短途旅行回来以后，她开始更主动地联系我。",
        "活动结束后，对方给我发了一条感谢消息。",
        "我已经做完那件约好的事了。",
    ],
)
def test_generic_retrospective_semantics_do_not_depend_on_activity_vocabulary(
    text: str,
) -> None:
    assert has_retrospective_event_semantics(text) is True


def test_future_post_event_clause_is_not_misread_as_completed() -> None:
    assert has_retrospective_event_semantics("等到读书会结束后，我们再去吃饭。") is False


def test_memory_gate_routes_generic_retrospective_event_to_flash() -> None:
    decision = MemoryGate().evaluate("上回志愿活动结束后，我们聊了很久。")

    assert decision.should_extract is True
    assert decision.signals == ["retrospective_event_semantics"]


async def test_plan_state_machine_rejects_backward_transition() -> None:
    store = InMemoryMemoryStore()
    plan = await store.save_relationship_plan(_plan("confirmed-plan", "交流会"))

    with pytest.raises(ValueError, match="invalid relationship plan transition"):
        await store.set_relationship_plan_status(
            plan_id=plan.plan_id,
            user_id=plan.user_id,
            status=PlanStatus.PROPOSED,
        )


async def test_sqlite_plan_lifecycle_persists_across_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "relationship-plans.db"
    store = SQLiteMemoryStore(database_path)
    saved = await store.save_memory(
        user_id="sqlite-plan-user",
        relationship_id="partner",
        candidate=MemoryCandidate(
            kind=MemoryKind.PLANNED_EVENT,
            subject="relationship",
            summary="双方约好参加周末音乐分享会",
            original_text="我们约好周末参加音乐分享会",
            period_start=datetime(2099, 8, 8, 14, tzinfo=UTC),
            expires_at=datetime(2099, 8, 9, 14, tzinfo=UTC),
            payload={
                "event_status": "confirmed",
                "activity_type": "音乐分享会",
                "participants": ["user", "partner"],
            },
        ),
    )
    plan_id = saved.item.payload["plan_id"]
    await store.set_relationship_plan_status(
        plan_id=plan_id,
        user_id="sqlite-plan-user",
        status=PlanStatus.CANCELLED,
        transitioned_at=datetime(2026, 8, 3, 9, tzinfo=UTC),
    )
    await store.aclose()

    reopened = SQLiteMemoryStore(database_path)
    plans = await reopened.list_relationship_plans(
        user_id="sqlite-plan-user",
        relationship_id="partner",
    )
    source = await reopened.get_memory(saved.item.id, "sqlite-plan-user")

    assert len(plans) == 1
    assert plans[0].plan_id == plan_id
    assert plans[0].status == PlanStatus.CANCELLED
    assert plans[0].cancelled_at == datetime(2026, 8, 3, 9, tzinfo=UTC)
    assert source is not None and source.status == MemoryStatus.SUPERSEDED


def _plan(
    plan_id: str,
    activity_type: str,
    *,
    scheduled_start: datetime | None = None,
) -> RelationshipPlan:
    now = datetime(2026, 8, 2, 10, tzinfo=UTC)
    return RelationshipPlan(
        plan_id=plan_id,
        user_id="plan-user",
        relationship_id="partner",
        activity_type=activity_type,
        participants=["user", "partner"],
        scheduled_start=scheduled_start,
        status=PlanStatus.CONFIRMED,
        created_at=now,
        updated_at=now,
    )


def _claim(
    *,
    claim_id: str,
    kind: MemoryKind,
    predicate: str,
    summary: str,
    evidence: str,
    payload: dict | None = None,
    occurred_at: datetime | None = None,
    period_start: datetime | None = None,
) -> AtomicClaim:
    return AtomicClaim(
        claim_id=claim_id,
        kind=kind,
        subject="relationship",
        predicate=predicate,
        summary=summary,
        evidence_spans=[evidence],
        time_kind=TimeKind.POINT if occurred_at or period_start else TimeKind.UNKNOWN,
        occurred_at=occurred_at,
        period_start=period_start,
        confidence=0.95,
        payload=payload or {},
    )

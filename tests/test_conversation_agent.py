import asyncio
from pathlib import Path

from loveapp.bootstrap import build_container
from loveapp.core.config import Settings
from loveapp.domain.conversation import ConversationRequest
from loveapp.domain.enums import (
    AdviceGoal,
    AdviceScenario,
    DatePlanMutation,
    DateRequestMode,
    DateTaskIntent,
    RelationshipStage,
    TaskType,
)
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    MemoryKind,
    MemoryValence,
    MessageRole,
    RelationshipImpact,
    TimeKind,
)


class ConfessionEventExtractor:
    async def extract(
        self,
        text,
        *,
        reference_time,
        existing_memories,
        conversation_history,
        trace=None,
        attempt_callback=None,
    ):
        del reference_time, existing_memories, conversation_history, trace
        del attempt_callback
        return AtomicExtraction(
            claims=[
                AtomicClaim(
                    claim_id="confession-succeeded",
                    kind=MemoryKind.INTERACTION_EVENT,
                    subject="relationship",
                    predicate="confession_succeeded",
                    summary="用户与对方表白成功",
                    evidence_spans=["表白成功"],
                    time_kind=TimeKind.POINT,
                    valence=MemoryValence.POSITIVE,
                    relationship_impact=RelationshipImpact.IMPROVING,
                    importance=5,
                    confidence=0.98,
                )
            ]
        )


class DelayedMuseumPreferenceExtractor:
    async def extract(
        self,
        text,
        *,
        reference_time,
        existing_memories,
        conversation_history,
        trace=None,
        attempt_callback=None,
    ):
        del text, reference_time, existing_memories, conversation_history, trace
        del attempt_callback
        await asyncio.sleep(0.05)
        return AtomicExtraction(
            claims=[
                AtomicClaim(
                    claim_id="museum-preference",
                    kind=MemoryKind.PREFERENCE,
                    subject="user",
                    predicate="prefers_museum",
                    summary="用户喜欢逛博物馆",
                    evidence_spans=["喜欢逛博物馆"],
                    time_kind=TimeKind.TIMELESS,
                    valence=MemoryValence.POSITIVE,
                    relationship_impact=RelationshipImpact.UNCLEAR,
                    confidence=0.95,
                    payload={"preference": "博物馆", "preference_type": "like"},
                )
            ]
        )


async def test_conversation_agent_routes_casual_chat_without_long_term_memory(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)

    turn = await container.conversation_agent.chat(
        ConversationRequest(
            user_id="casual-user",
            conversation_id="casual-conversation",
            query="你好",
        )
    )

    assert turn.route.task_type == TaskType.GENERAL_CHAT
    assert turn.message == "你好，我在。"
    assert turn.advice is None
    assert turn.date_plan is None
    messages = await container.memory_store.list_messages(
        user_id="casual-user",
        relationship_id="primary",
        conversation_id="casual-conversation",
    )
    memories = await container.memory_store.list_memories(
        user_id="casual-user",
        relationship_id="primary",
    )
    assert [message.role.value for message in messages] == ["user", "assistant"]
    assert memories == []
    assert {timing.name for timing in turn.timings} >= {
        "history_load",
        "routing",
        "casual_response",
        "total",
    }


async def test_date_action_evaluation_does_not_create_date_task(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)
    try:
        turn = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="date-action-advice-user",
                relationship_id="date-action-advice-relationship",
                conversation_id="date-action-advice-conversation",
                query="行，那我打算约她出来看个电影，吃顿饭，逛个街，你看怎么样。",
            )
        )
    finally:
        await container.aclose()

    assert turn.route.task_type == TaskType.RELATIONSHIP_ADVICE
    assert turn.route.primary_scenario == AdviceScenario.PURSUIT
    assert turn.route.primary_goal == AdviceGoal.PROGRESS
    assert turn.advice is not None
    assert turn.date_plan is None
    assert turn.date_task_state is None


async def test_conflict_category_follow_up_stays_in_advice_workflow(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)
    scope = {
        "user_id": "conflict-category-user",
        "relationship_id": "conflict-category-relationship",
        "conversation_id": "conflict-category-conversation",
    }
    try:
        first = await container.conversation_agent.chat(
            ConversationRequest(
                **scope,
                query="我们因为旅行预算分配发生了争执，我准备找她认真谈谈。",
            )
        )
        second = await container.conversation_agent.chat(
            ConversationRequest(
                **scope,
                query="那先一起吃顿饭，饭后再聊，你建议选哪类菜比较合适？",
                active_task=first.active_task,
            )
        )
    finally:
        await container.aclose()

    assert first.route.primary_scenario == AdviceScenario.CONFLICT
    assert second.route.task_type == TaskType.RELATIONSHIP_ADVICE
    assert second.route.date_request_mode == DateRequestMode.CATEGORY_RECOMMENDATION
    assert second.route.primary_scenario == AdviceScenario.CONFLICT
    assert second.route.primary_goal == AdviceGoal.REPAIR
    assert second.date_plan is None
    assert second.date_task_state is None


async def test_reported_content_recommendation_does_not_create_date_task(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)
    try:
        turn = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="reported-recommendation-user",
                relationship_id="reported-recommendation-relationship",
                conversation_id="reported-recommendation-conversation",
                query=(
                    "她刚给我推荐了一部推理小说，我应该如何借这个话题"
                    "继续和她交流？"
                ),
                active_task=TaskType.RELATIONSHIP_ADVICE,
            )
        )
    finally:
        await container.aclose()

    assert turn.route.task_type == TaskType.RELATIONSHIP_ADVICE
    assert turn.route.primary_scenario == AdviceScenario.PURSUIT
    assert turn.route.primary_goal == AdviceGoal.COMMUNICATE
    assert turn.advice is not None
    assert turn.date_plan is None
    assert turn.date_task_state is None


async def test_advice_question_does_not_recover_a_phantom_date_task(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)
    try:
        await container.memory_service.record_message(
            user_id="advice-follow-up-user",
            relationship_id="advice-follow-up-relationship",
            conversation_id="advice-follow-up-conversation",
            role=MessageRole.USER,
            content="我怎么约她去海洋馆，如果她答应就肯定有戏，你觉得呢",
        )
        await container.memory_service.record_message(
            user_id="advice-follow-up-user",
            relationship_id="advice-follow-up-relationship",
            conversation_id="advice-follow-up-conversation",
            role=MessageRole.ASSISTANT,
            content=(
                "海洋馆作为约会地点本身没有问题，但答应邀约不能直接等同于好感。"
                "待确认：她之前有没有主动约你出去玩过，或者特别关心你的生活？"
            ),
        )

        turn = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="advice-follow-up-user",
                relationship_id="advice-follow-up-relationship",
                conversation_id="advice-follow-up-conversation",
                query="她约我一起逛过漫展，一起逛过公园，我生病了也会关心我",
            )
        )
    finally:
        await container.aclose()

    assert turn.route.task_type == TaskType.RELATIONSHIP_ADVICE
    assert turn.route.primary_scenario == AdviceScenario.PURSUIT
    assert turn.route.primary_goal == AdviceGoal.UNDERSTAND
    assert turn.advice is not None
    assert turn.date_plan is None
    assert turn.date_task_state is None


async def test_conversation_agent_routes_multi_scenario_advice(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)

    turn = await container.conversation_agent.chat(
        ConversationRequest(
            user_id="advice-router-user",
            conversation_id="advice-router-conversation",
            query=(
                "我喜欢她，最近找她搭讪聊天，她也开始愿意和我闲聊。"
                "这是不是往好的方向发展，我该怎么进一步发展？"
            ),
        )
    )

    assert turn.route.task_type == TaskType.RELATIONSHIP_ADVICE
    assert turn.route.primary_scenario == AdviceScenario.PURSUIT
    assert AdviceScenario.CHAT_ANALYSIS in turn.route.secondary_scenarios
    assert turn.advice is not None
    assert turn.advice.scenario == AdviceScenario.PURSUIT
    assert turn.advice.sources


async def test_conversation_agent_collects_date_city_across_turns(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)
    first = await container.conversation_agent.chat(
        ConversationRequest(
            user_id="date-router-user",
            conversation_id="date-router-conversation",
            query="帮我安排一次约会，预算300元",
        )
    )

    assert first.route.task_type == TaskType.DATE_PLANNING
    assert first.message == "你想在哪座城市安排这次约会？"
    assert first.active_task == TaskType.DATE_PLANNING

    second = await container.conversation_agent.chat(
        ConversationRequest(
            user_id="date-router-user",
            conversation_id="date-router-conversation",
            query="杭州，想去看展览，也希望安静一点",
            active_task=first.active_task,
        )
    )

    assert second.route.task_type == TaskType.DATE_PLANNING
    assert second.route.date_plan.city == "杭州"
    assert second.route.date_plan.budget == 300
    assert second.date_plan is not None
    assert second.date_plan.total_estimated_cost <= 300


async def test_date_task_pauses_when_user_evaluates_a_relationship_action(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)
    try:
        first = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="date-switch-advice-user",
                relationship_id="date-switch-advice-relationship",
                conversation_id="date-switch-advice-conversation",
                query="帮我安排一次约会，地点在上海，预算300元",
            )
        )
        second = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="date-switch-advice-user",
                relationship_id="date-switch-advice-relationship",
                conversation_id="date-switch-advice-conversation",
                query="我打算约她出来看电影，你看怎么样？",
                active_task=first.active_task,
            )
        )
    finally:
        await container.aclose()

    assert second.route.task_type == TaskType.RELATIONSHIP_ADVICE
    assert second.route.date_intent == DateTaskIntent.SWITCH
    assert second.date_task_state is not None
    assert second.date_task_state.status.value == "paused"
    assert second.advice is not None


async def test_date_route_uses_the_shared_memory_sidecar(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)
    container.memory_service._extractor = ConfessionEventExtractor()
    try:
        turn = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="shared-memory-user",
                relationship_id="shared-memory-relationship",
                conversation_id="shared-memory-conversation",
                query="我最近刚刚和喜欢的女孩表白成功，我准备这周带她去约会，能帮我安排一下行程吗",
            )
        )
    finally:
        await container.aclose()

    assert turn.route.task_type == TaskType.DATE_PLANNING
    memories = await container.memory_store.list_memories(
        user_id="shared-memory-user",
        relationship_id="shared-memory-relationship",
    )
    assert len(memories) == 1
    assert memories[0].kind == MemoryKind.INTERACTION_EVENT
    assert memories[0].payload["predicate"] == "confession_succeeded"
    runs = await container.memory_store.list_extraction_runs(
        user_id="shared-memory-user",
        relationship_id="shared-memory-relationship",
        conversation_id="shared-memory-conversation",
    )
    assert len(runs) == 1


async def test_contextual_acceptance_in_chat_updates_relationship_stage(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)
    try:
        await container.conversation_agent.chat(
            ConversationRequest(
                user_id="contextual-chat-user",
                relationship_id="contextual-chat-relationship",
                conversation_id="contextual-chat-conversation",
                query="我准备和她表白",
            )
        )
        await container.conversation_agent.chat(
            ConversationRequest(
                user_id="contextual-chat-user",
                relationship_id="contextual-chat-relationship",
                conversation_id="contextual-chat-conversation",
                query="她同意了，我很开心",
            )
        )
        await container.memory_service.wait_for_scope(
            user_id="contextual-chat-user",
            relationship_id="contextual-chat-relationship",
            timeout_seconds=1,
        )
        context = await container.memory_service.get_context(
            "contextual-chat-user",
            "contextual-chat-relationship",
        )
    finally:
        await container.aclose()

    assert context.relationship_stage == RelationshipStage.DATING
    memories = await container.memory_store.list_memories(
        user_id="contextual-chat-user",
        relationship_id="contextual-chat-relationship",
    )
    assert any(item.payload.get("predicate") == "confession_succeeded" for item in memories)


async def test_next_turn_waits_for_prior_memory_before_date_planning(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)
    container.memory_service._extractor = DelayedMuseumPreferenceExtractor()
    try:
        first = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="cross-route-user",
                relationship_id="cross-route-relationship",
                conversation_id="cross-route-conversation",
                query="我喜欢逛博物馆，追她时该怎么聊天",
            )
        )
        second = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="cross-route-user",
                relationship_id="cross-route-relationship",
                conversation_id="cross-route-conversation",
                query="帮我安排约会，地点在上海，周六，预算500元",
            )
        )
    finally:
        await container.aclose()

    assert first.route.task_type == TaskType.RELATIONSHIP_ADVICE
    assert second.route.task_type == TaskType.DATE_PLANNING
    assert second.date_plan is not None
    assert second.date_plan.items[0].place.name.startswith("城市美术馆")


async def test_date_task_state_falls_back_once_and_resumes_on_later_city(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)
    first = await container.conversation_agent.chat(
        ConversationRequest(
            user_id="date-state-user",
            relationship_id="date-state-relationship",
            conversation_id="date-state-conversation",
            query="帮我安排一次约会",
        )
    )

    assert first.message is not None
    assert first.date_task_state is not None
    assert first.date_task_state.status.value == "collecting"
    assert "city" in first.date_task_state.missing_fields

    partial = await container.conversation_agent.chat(
        ConversationRequest(
            user_id="date-state-user",
            relationship_id="date-state-relationship",
            conversation_id="date-state-conversation",
            query="预算300元",
        )
    )

    assert partial.route.date_intent.value == "supplement"
    assert partial.date_plan is not None
    assert partial.date_task_state is not None
    assert partial.date_task_state.fallback_used is True
    assert partial.date_task_state.city is None

    resumed = await container.conversation_agent.chat(
        ConversationRequest(
            user_id="date-state-user",
            relationship_id="date-state-relationship",
            conversation_id="date-state-conversation",
            query="上海",
        )
    )

    assert resumed.route.task_type == TaskType.DATE_PLANNING
    assert resumed.route.date_intent.value == "supplement"
    assert resumed.date_task_state is not None
    assert resumed.date_task_state.city == "上海"
    assert resumed.date_plan is not None
    assert resumed.date_plan.items


async def test_multi_turn_date_addition_keeps_the_previous_plan(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)
    try:
        first = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="incremental-date-user",
                relationship_id="incremental-date-relationship",
                conversation_id="incremental-date-conversation",
                query=(
                    "帮我安排一次约会，地点在上海静安区，周六，预算1000元，"
                    "喜欢手工，晚饭吃西餐"
                ),
            )
        )
        assert first.date_plan is not None
        assert first.date_task_state is not None
        first_ids = {item.place.id for item in first.date_plan.items}

        second = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="incremental-date-user",
                relationship_id="incremental-date-relationship",
                conversation_id="incremental-date-conversation",
                query="我还想增加一些上海经典旅游景点，也帮我安排到行程中吧",
            )
        )
    finally:
        await container.aclose()

    assert second.route.date_intent == DateTaskIntent.SUPPLEMENT
    assert second.route.date_mutation == DatePlanMutation.ADD
    assert second.date_plan is not None
    assert second.date_task_state is not None
    second_ids = {item.place.id for item in second.date_plan.items}
    assert first_ids <= second_ids
    assert len(second_ids) == len(first_ids) + 1
    assert second.date_task_state.plan_version == 2
    assert second.date_task_state.current_plan is not None


async def test_implicit_multi_stop_update_preserves_plan_and_schedule_semantics(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)
    try:
        first = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="implicit-date-user",
                relationship_id="implicit-date-relationship",
                conversation_id="implicit-date-conversation",
                query=(
                    "帮我安排一次约会，地点在上海静安区，下周六，"
                    "预算1000元，日料是午餐"
                ),
            )
        )
        assert first.date_plan is not None
        assert first.date_task_state is not None
        first_ids = {item.place.id for item in first.date_plan.items}

        second = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="implicit-date-user",
                relationship_id="implicit-date-relationship",
                conversation_id="implicit-date-conversation",
                active_task=first.active_task,
                query=(
                    "对了，下午准备去看场电影，同时晚饭吃火锅（日料是午餐），"
                    "看完电影想去个景点逛逛"
                ),
            )
        )

        assert second.route.date_intent == DateTaskIntent.SUPPLEMENT
        assert second.route.date_mutation == DatePlanMutation.ADD
        assert second.date_plan is not None
        assert second.date_task_state is not None
        assert first_ids <= {item.place.id for item in second.date_plan.items}
        assert second.date_task_state.meal_keywords == {
            "lunch": ["日料"],
            "dinner": ["火锅"],
        }
        assert second.date_task_state.plan_version == 2

        by_keyword = {
            item.slot_keyword: item
            for item in second.date_plan.items
            if item.slot_keyword is not None
        }
        assert {"日料", "电影院", "景点", "火锅"} <= set(by_keyword)
        assert by_keyword["日料"].meal_type == "lunch"
        assert by_keyword["火锅"].meal_type == "dinner"
        assert by_keyword["电影院"].time_label == "下午"
        assert by_keyword["景点"].after_item == "电影院"
        assert by_keyword["日料"].order < by_keyword["电影院"].order
        assert by_keyword["电影院"].order < by_keyword["景点"].order
        assert by_keyword["景点"].order < by_keyword["火锅"].order
        assert second.message is not None
        assert "保留上一版行程" in second.message
        assert "更新后的完整安排" in second.message

        unchanged = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="implicit-date-user",
                relationship_id="implicit-date-relationship",
                conversation_id="implicit-date-conversation",
                active_task=second.active_task,
                query="预算还是1000元",
            )
        )
    finally:
        await container.aclose()

    assert unchanged.date_task_state is not None
    assert unchanged.date_task_state.plan_version == 2
    assert unchanged.message is not None
    assert "保留当前版本" in unchanged.message


async def test_named_place_replacement_updates_the_complete_itinerary(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)
    try:
        first = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="replace-date-user",
                relationship_id="replace-date-relationship",
                conversation_id="replace-date-conversation",
                query=(
                    "帮我安排一次约会，上海静安区，下周六，预算1000元，"
                    "中午吃韩国料理，下午去公园"
                ),
            )
        )
        assert first.date_plan is not None
        assert any("辅德里公园" in item.place.name for item in first.date_plan.items)

        second = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="replace-date-user",
                relationship_id="replace-date-relationship",
                conversation_id="replace-date-conversation",
                active_task=first.active_task,
                query=(
                    "我想晚上吃海底捞，中午吃韩国料理，然后下午不去 "
                    "辅德里公园，换一个博物馆"
                ),
            )
        )
    finally:
        await container.aclose()

    assert second.route.date_mutation == DatePlanMutation.REPLACE
    assert second.route.date_plan.replace_place_names == ["辅德里公园"]
    assert second.date_plan is not None
    assert second.date_task_state is not None
    assert second.date_task_state.plan_version == 2
    names = [item.place.name for item in second.date_plan.items]
    assert not any("辅德里公园" in name for name in names)
    assert any("美术馆" in name or "博物馆" in name for name in names)
    assert any("韩式料理" in name for name in names)
    assert any("海底捞" in name for name in names)
    by_keyword = {
        item.slot_keyword: item
        for item in second.date_plan.items
        if item.slot_keyword is not None
    }
    assert by_keyword["韩国料理"].meal_type == "lunch"
    assert by_keyword["博物馆"].time_label == "下午"
    assert by_keyword["海底捞"].meal_type == "dinner"
    assert by_keyword["韩国料理"].order < by_keyword["博物馆"].order
    assert by_keyword["博物馆"].order < by_keyword["海底捞"].order
    assert second.date_task_state.dining_keywords == ["韩国料理", "海底捞"]
    assert second.date_task_state.activity_keywords == ["博物馆"]
    assert second.message is not None
    assert "替换" in second.message


async def test_date_task_state_is_loaded_after_container_restart(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="demo",
        rag_backend="memory",
        map_provider="demo",
        memory_backend="sqlite",
        memory_database_path=tmp_path / "loveapp.db",
        memory_extraction_provider="disabled",
    )
    first_container = build_container(settings)
    first = await first_container.conversation_agent.chat(
        ConversationRequest(
            user_id="restart-user",
            relationship_id="restart-relationship",
            conversation_id="restart-conversation",
            query="帮我安排一次约会",
        )
    )
    assert first.date_task_state is not None
    await first_container.aclose()

    second_container = build_container(settings)
    resumed = await second_container.conversation_agent.chat(
        ConversationRequest(
            user_id="restart-user",
            relationship_id="restart-relationship",
            conversation_id="restart-conversation",
            query="上海",
        )
    )
    await second_container.aclose()

    assert resumed.route.task_type == TaskType.DATE_PLANNING
    assert resumed.date_task_state is not None
    assert resumed.date_task_state.city == "上海"


async def test_date_plan_snapshot_survives_container_restart(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="demo",
        rag_backend="memory",
        map_provider="demo",
        memory_backend="sqlite",
        memory_database_path=tmp_path / "loveapp.db",
        memory_extraction_provider="disabled",
        knowledge_path=tmp_path / "knowledge",
    )
    first_container = build_container(settings)
    first = await first_container.conversation_agent.chat(
        ConversationRequest(
            user_id="restart-plan-user",
            relationship_id="restart-plan-relationship",
            conversation_id="restart-plan-conversation",
            query=(
                "帮我安排一次约会，地点在上海静安区，周六，预算1000元，"
                "喜欢手工，晚饭吃西餐"
            ),
        )
    )
    assert first.date_plan is not None
    first_ids = {item.place.id for item in first.date_plan.items}
    await first_container.aclose()

    second_container = build_container(settings)
    second = await second_container.conversation_agent.chat(
        ConversationRequest(
            user_id="restart-plan-user",
            relationship_id="restart-plan-relationship",
            conversation_id="restart-plan-conversation",
            query="我还想增加一些上海经典旅游景点，也帮我安排到行程中吧",
        )
    )
    await second_container.aclose()

    assert second.date_plan is not None
    second_ids = {item.place.id for item in second.date_plan.items}
    assert first_ids <= second_ids
    assert second.date_task_state is not None
    assert second.date_task_state.plan_version == 2


async def test_legacy_date_conversation_recovers_task_state(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)
    await container.memory_service.record_message(
        user_id="legacy-user",
        relationship_id="legacy-relationship",
        conversation_id="legacy-conversation",
        role=MessageRole.USER,
        content="帮我安排一次约会，预算300元",
    )
    await container.memory_service.record_message(
        user_id="legacy-user",
        relationship_id="legacy-relationship",
        conversation_id="legacy-conversation",
        role=MessageRole.ASSISTANT,
        content="你想在哪座城市安排这次约会？",
    )

    turn = await container.conversation_agent.chat(
        ConversationRequest(
            user_id="legacy-user",
            relationship_id="legacy-relationship",
            conversation_id="legacy-conversation",
            query="上海",
        )
    )

    assert turn.route.task_type == TaskType.DATE_PLANNING
    assert turn.date_task_state is not None
    assert turn.date_task_state.city == "上海"
    assert turn.route.date_plan.budget == 300

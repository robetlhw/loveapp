from loveapp.bootstrap import build_container
from loveapp.domain.conversation import ConversationRequest


async def test_phase_a_multiturn_location_and_budget_override(app_settings) -> None:
    container = build_container(app_settings)
    scope = {
        "user_id": "phase-a-user",
        "relationship_id": "phase-a-relationship",
        "conversation_id": "phase-a-conversation",
    }
    try:
        first = await container.conversation_agent.chat(
            ConversationRequest(**scope, query="帮我安排周末约会")
        )
        second = await container.conversation_agent.chat(
            ConversationRequest(**scope, query="她想去静安区玩玩")
        )
        third = await container.conversation_agent.chat(
            ConversationRequest(**scope, query="上海，静安区，预算300元")
        )
        fourth = await container.conversation_agent.chat(
            ConversationRequest(**scope, query="预算改为600元")
        )
    finally:
        await container.aclose()

    assert first.date_task_state is not None
    assert second.route.date_patch is not None
    assert second.route.date_patch.city == "上海"
    assert second.route.date_patch.area == "静安区"
    assert third.date_task_state is not None
    assert third.date_task_state.city == "上海"
    assert third.date_task_state.area == "静安区"
    assert fourth.route.date_patch is not None
    assert fourth.route.date_patch.budget == 600
    assert fourth.route.date_patch.city is None
    assert fourth.date_task_state is not None
    assert fourth.date_task_state.city == "上海"
    assert fourth.date_task_state.area == "静安区"
    assert fourth.date_task_state.budget == 600
    timing_names = {item.name for item in fourth.timings}
    assert {"runtime_context_build", "date_patch_apply"} <= timing_names

from datetime import date, timedelta

from loveapp.adapters.maps.demo import DemoMapProvider
from loveapp.adapters.memory.in_memory import InMemoryMemoryStore
from loveapp.adapters.weather import DemoWeatherProvider
from loveapp.agents.date_planner import DatePlanningAgent
from loveapp.application.memory import MemoryService, NoOpMemoryExtractor
from loveapp.application.routing import route_by_rules
from loveapp.bootstrap import build_container
from loveapp.core.config import Settings
from loveapp.domain.conversation import ConversationRequest
from loveapp.domain.date_plan import DatePlanRequest
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.enums import (
    BudgetScope,
    DatePlanMode,
    DatePlanMutation,
    TaskType,
)
from loveapp.domain.routing import RouteInput


def test_route_extracts_multi_day_duration_and_daily_budget() -> None:
    result = route_by_rules(
        RouteInput(latest_query="帮我安排和女朋友去上海玩三天两夜的行程，每天预算500元")
    )

    assert result.task_type == TaskType.DATE_PLANNING
    assert result.date_plan.plan_mode == DatePlanMode.MULTI_DAY
    assert result.date_plan.city == "上海"
    assert result.date_plan.day_count == 3
    assert result.date_plan.nights == 2
    assert result.date_plan.budget == 500
    assert result.date_plan.budget_scope == BudgetScope.PER_DAY


def test_route_extracts_weekday_range_instead_of_only_first_day() -> None:
    result = route_by_rules(RouteInput(latest_query="帮我安排周五到周日和女朋友在上海旅游"))

    assert result.task_type == TaskType.DATE_PLANNING
    assert result.date_plan.date is not None
    assert result.date_plan.end_date == result.date_plan.date + timedelta(days=2)
    assert result.date_plan.day_count == 3
    assert result.date_plan.date.weekday() == 4
    assert result.date_plan.end_date.weekday() == 6


def test_trip_itinerary_question_routes_without_exact_date_keywords() -> None:
    result = route_by_rules(RouteInput(latest_query="国庆想和女朋友去上海旅游三天，有什么安排"))

    assert result.task_type == TaskType.DATE_PLANNING
    assert result.date_plan.plan_mode == DatePlanMode.MULTI_DAY
    assert result.date_plan.day_count == 3
    assert result.date_plan.city == "上海"


def test_day_specific_edit_keeps_existing_trip_length() -> None:
    state = DatePlanningTaskState(
        user_id="multi-route-user",
        relationship_id="multi-route-relationship",
        conversation_id="multi-route-conversation",
        city="上海",
        plan_mode=DatePlanMode.MULTI_DAY,
        day_count=3,
        nights=2,
    )

    result = route_by_rules(
        RouteInput(
            latest_query="请把第二天下午换成博物馆",
            date_task_state=state,
        )
    )

    assert result.task_type == TaskType.DATE_PLANNING
    assert result.date_mutation == DatePlanMutation.REPLACE
    assert result.date_plan.target_day == 2
    assert result.date_plan.day_count == 3
    assert result.date_plan.replace_place_names == []
    assert result.date_plan.activity_keywords == ["博物馆"]


async def test_multi_day_planner_groups_weather_and_resets_routes_each_day() -> None:
    memory_service = MemoryService(InMemoryMemoryStore(), NoOpMemoryExtractor())
    agent = DatePlanningAgent(
        DemoMapProvider(),
        memory_service,
        DemoWeatherProvider(),
    )
    start_date = date(2026, 8, 7)

    plan = await agent.plan(
        DatePlanRequest(
            city="上海",
            date=start_date,
            day_count=3,
            budget=500,
            budget_scope=BudgetScope.PER_DAY,
        )
    )

    assert plan.plan_mode == DatePlanMode.MULTI_DAY
    assert plan.start_date == start_date
    assert plan.end_date == start_date + timedelta(days=2)
    assert len(plan.days) == 3
    assert len(plan.items) == 6
    assert plan.total_estimated_cost <= 1500
    assert len({item.place.id for item in plan.items}) == len(plan.items)
    for day_index, day in enumerate(plan.days, start=1):
        assert day.day_index == day_index
        assert day.date == start_date + timedelta(days=day_index - 1)
        assert day.weather is not None
        assert day.weather.date == day.date
        assert len(day.items) == 2
        assert day.items[0].order == 1
        assert day.items[0].route_from_previous is None
        assert day.items[1].order == 2
        assert day.items[1].route_from_previous is not None


async def test_multi_turn_edit_replaces_only_the_requested_day(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)
    try:
        first = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="multi-conversation-user",
                relationship_id="multi-conversation-relationship",
                conversation_id="multi-conversation",
                query=("帮我安排周五到周日和女朋友在上海的三日旅行，每天预算500元"),
            )
        )
        assert first.date_plan is not None
        assert first.date_task_state is not None
        before_by_day = {
            day.day_index: [item.place.id for item in day.items] for day in first.date_plan.days
        }

        second = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="multi-conversation-user",
                relationship_id="multi-conversation-relationship",
                conversation_id="multi-conversation",
                active_task=first.active_task,
                query="第二天下午不去原来的活动了，换成公园",
            )
        )
        assert second.date_task_state is not None
        current_version = second.date_task_state.plan_version
        unchanged = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="multi-conversation-user",
                relationship_id="multi-conversation-relationship",
                conversation_id="multi-conversation",
                active_task=second.active_task,
                query="每天预算还是500元",
            )
        )
        shortened = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="multi-conversation-user",
                relationship_id="multi-conversation-relationship",
                conversation_id="multi-conversation",
                active_task=unchanged.active_task,
                query="把这次行程改成两天",
            )
        )
        single_day = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="multi-conversation-user",
                relationship_id="multi-conversation-relationship",
                conversation_id="multi-conversation",
                active_task=shortened.active_task,
                query="再把行程缩短为一天",
            )
        )
    finally:
        await container.aclose()

    assert second.date_plan is not None
    assert second.date_task_state is not None
    assert second.route.date_mutation == DatePlanMutation.REPLACE
    assert second.date_task_state.day_count == 3
    assert second.date_task_state.target_day == 2
    after_by_day = {
        day.day_index: [item.place.id for item in day.items] for day in second.date_plan.days
    }
    assert after_by_day[1] == before_by_day[1]
    assert after_by_day[3] == before_by_day[3]
    assert after_by_day[2] != before_by_day[2]
    assert any("公园" in item.place.name for item in second.date_plan.days[1].items)
    assert unchanged.date_task_state is not None
    assert unchanged.date_task_state.plan_version == current_version
    assert unchanged.date_task_state.budget_scope == BudgetScope.PER_DAY
    assert shortened.date_plan is not None
    assert shortened.date_task_state is not None
    assert shortened.date_task_state.day_count == 2
    assert shortened.date_task_state.last_mutation == DatePlanMutation.REPLAN
    assert len(shortened.date_plan.days) == 2
    assert single_day.date_plan is not None
    assert single_day.date_task_state is not None
    assert single_day.date_task_state.plan_mode == DatePlanMode.SINGLE_DAY
    assert single_day.date_task_state.day_count == 1
    assert single_day.date_task_state.target_day is None

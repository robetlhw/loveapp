import pytest

from loveapp.application.routing import (
    HybridRouter,
    extract_date_plan_slots,
    merge_route_correction,
    route_by_rules,
    should_clarify_route,
)
from loveapp.domain.date_plan import DatePlan, DatePlanItem, Place
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.enums import (
    AdviceGoal,
    AdviceScenario,
    DatePlanMutation,
    DateRequestMode,
    DateTaskIntent,
    PlaceCategory,
    RiskLevel,
    RouteSource,
    TaskType,
)
from loveapp.domain.memory import MessageRole, StoredMessage, utc_now
from loveapp.domain.routing import DatePlanSlots, RouteCorrection, RouteInput
from loveapp.safety import SafetyPolicy


class StubCorrector:
    def __init__(self, correction: RouteCorrection) -> None:
        self.correction = correction
        self.calls = 0

    async def correct(self, route_input, rule_result) -> RouteCorrection:
        del route_input, rule_result
        self.calls += 1
        return self.correction

    async def aclose(self) -> None:
        return None


class QueryCorrector(StubCorrector):
    def __init__(self, corrections: dict[str, RouteCorrection]) -> None:
        super().__init__(next(iter(corrections.values())))
        self.corrections = corrections

    async def correct(self, route_input, rule_result) -> RouteCorrection:
        self.calls += 1
        return self.corrections[route_input.latest_query]


def test_rule_router_keeps_primary_and_secondary_advice_intents() -> None:
    result = route_by_rules(
        RouteInput(
            latest_query=(
                "我喜欢我们班的一个女孩子，最近找她搭讪聊天，她开始愿意和我闲聊。"
                "这是不是往好的方向发展，我该怎么进一步发展？"
            )
        )
    )

    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.primary_scenario == AdviceScenario.PURSUIT
    assert AdviceScenario.CHAT_ANALYSIS in result.secondary_scenarios
    assert {result.primary_goal, *result.secondary_goals} == {
        AdviceGoal.UNDERSTAND,
        AdviceGoal.PROGRESS,
    }


def test_rule_router_orders_compound_request_with_direct_first_clause() -> None:
    result = route_by_rules(
        RouteInput(
            latest_query="先分析她最近为什么冷淡，再帮我推荐一家适合约会的餐厅。"
        )
    )

    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.secondary_tasks == [TaskType.DATE_PLANNING]
    assert result.primary_scenario == AdviceScenario.CHAT_ANALYSIS


def test_rule_router_respects_negation() -> None:
    result = route_by_rules(
        RouteInput(latest_query="我们没有吵架，只是最近聊天少了，我想知道她为什么不回复。")
    )

    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.primary_scenario == AdviceScenario.CHAT_ANALYSIS
    assert AdviceScenario.CONFLICT not in result.scenario_scores


@pytest.mark.parametrize(
    "query",
    (
        "我和女朋友吵架了",
        "我和女朋友昨天因为周末去哪玩吵了一架。",
        "昨天我们因为钱怎么安排大吵了一架。",
    ),
)
def test_scenario_router_recognizes_common_conflict_word_forms(query: str) -> None:
    result = route_by_rules(RouteInput(latest_query=query))

    assert result.primary_scenario == AdviceScenario.CONFLICT


def test_scenario_router_respects_negated_conflict_word_form() -> None:
    result = route_by_rules(
        RouteInput(latest_query="我们没有大吵一架，只是对安排有不同意见。")
    )

    assert AdviceScenario.CONFLICT not in result.scenario_scores


def test_scenario_router_keeps_conflict_for_relationship_clarification_follow_up() -> None:
    first_query = "我和女朋友昨天因为周末去哪玩吵了一架。"
    first = route_by_rules(RouteInput(latest_query=first_query))
    history = [
        StoredMessage(
            id="conflict-follow-up-user",
            conversation_id="conflict-follow-up",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.USER,
            content=first_query,
        ),
        StoredMessage(
            id="conflict-follow-up-assistant",
            conversation_id="conflict-follow-up",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.ASSISTANT,
            content="待确认：你们各自最想去的周末活动是什么？",
        ),
    ]

    follow_up = route_by_rules(
        RouteInput(
            latest_query=(
                "主要是我觉得她每次都让我做决定，我有点烦。"
                "后来我说话也有点重，她现在不太想跟我说话。"
            ),
            recent_messages=history,
            active_task=TaskType.RELATIONSHIP_ADVICE,
        )
    )

    assert follow_up.primary_scenario == AdviceScenario.CONFLICT
    assert AdviceScenario.PURSUIT not in follow_up.secondary_scenarios
    assert AdviceScenario.PURSUIT not in follow_up.scenario_scores
    assert first.primary_scenario == AdviceScenario.CONFLICT


def test_scenario_router_keeps_conflict_for_elliptical_follow_up() -> None:
    history = [
        StoredMessage(
            id="elliptical-conflict-user",
            conversation_id="elliptical-conflict",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.USER,
            content="我们昨天吵了一架。",
        ),
        StoredMessage(
            id="elliptical-conflict-assistant",
            conversation_id="elliptical-conflict",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.ASSISTANT,
            content="可以先说说当时发生了什么。",
        ),
    ]

    result = route_by_rules(
        RouteInput(
            latest_query="那我现在怎么办？",
            recent_messages=history,
            active_task=TaskType.RELATIONSHIP_ADVICE,
        )
    )

    assert result.primary_scenario == AdviceScenario.CONFLICT
    assert AdviceScenario.PURSUIT not in result.secondary_scenarios


def test_scenario_router_does_not_add_pursuit_to_contact_boundary_follow_up() -> None:
    history = [
        StoredMessage(
            id="contact-boundary-assistant",
            conversation_id="contact-boundary",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.ASSISTANT,
            content="待确认：她有没有明确要求暂停联系？",
        )
    ]

    result = route_by_rules(
        RouteInput(
            latest_query="她明确表示过暂时别联系，我应该先道歉还是先把我的不满说清楚？",
            recent_messages=history,
            active_task=TaskType.RELATIONSHIP_ADVICE,
        )
    )

    assert result.primary_scenario in {
        AdviceScenario.BOUNDARY,
        AdviceScenario.CONFLICT,
    }
    assert AdviceScenario.PURSUIT not in result.secondary_scenarios
    assert AdviceScenario.PURSUIT not in result.scenario_scores


@pytest.mark.parametrize(
    "query",
    (
        "我喜欢一个女生，不知道怎么追她。",
        "我跟她不太熟，想找机会和她聊天。",
    ),
)
def test_scenario_router_preserves_explicit_pursuit_cases(query: str) -> None:
    result = route_by_rules(RouteInput(latest_query=query))

    assert result.primary_scenario == AdviceScenario.PURSUIT


def test_scenario_router_current_conflict_overrides_chat_analysis_history() -> None:
    history = [
        StoredMessage(
            id="chat-analysis-user",
            conversation_id="chat-to-conflict",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.USER,
            content="她最近回复很慢，我不知道是不是不想聊天。",
        ),
        StoredMessage(
            id="chat-analysis-assistant",
            conversation_id="chat-to-conflict",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.ASSISTANT,
            content="可以结合持续时间和互动内容判断。",
        ),
    ]

    result = route_by_rules(
        RouteInput(
            latest_query="先不说回复了，我们昨天大吵了一架。",
            recent_messages=history,
            active_task=TaskType.RELATIONSHIP_ADVICE,
        )
    )

    assert result.primary_scenario == AdviceScenario.CONFLICT


def test_greeting_does_not_hide_relationship_request() -> None:
    result = route_by_rules(RouteInput(latest_query="谢谢，不过她最近一直不理我，我该怎么办？"))

    assert result.task_type == TaskType.RELATIONSHIP_ADVICE


def test_rule_router_recognizes_composed_initiation_goal() -> None:
    result = route_by_rules(
        RouteInput(
            latest_query="我和班上的女生接触很少，怎么创造聊天搭话的机会？"
        )
    )

    assert result.primary_goal == AdviceGoal.INITIATE


def test_rule_router_recognizes_inexperience_as_initiation_goal() -> None:
    result = route_by_rules(
        RouteInput(latest_query="我喜欢了一个女孩子，但她不怎么和我熟，该怎么办")
    )

    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.primary_scenario == AdviceScenario.PURSUIT
    assert result.primary_goal == AdviceGoal.INITIATE
    assert result.goal_scores[AdviceGoal.INITIATE] == 3.5


def test_rule_router_treats_date_action_evaluation_as_relationship_advice() -> None:
    result = route_by_rules(
        RouteInput(
            latest_query="行，那我打算约她出来看个电影，吃顿饭，逛个街，你看怎么样。"
        )
    )

    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.primary_scenario == AdviceScenario.PURSUIT
    assert result.primary_goal == AdviceGoal.PROGRESS
    assert result.date_intent == DateTaskIntent.NONE
    assert TaskType.DATE_PLANNING not in result.task_scores


def test_reusing_a_previous_activity_is_an_evaluation_not_an_itinerary() -> None:
    result = route_by_rules(
        RouteInput(
            latest_query=(
                "我们之前单独见过几次，她体验都不错。下一次见面我想继续去书店，"
                "不换新活动会稳妥一些，整体看怎么样？"
            )
        )
    )

    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.date_request_mode == DateRequestMode.EVALUATE
    assert result.primary_scenario == AdviceScenario.PURSUIT
    assert result.primary_goal == AdviceGoal.PROGRESS
    assert result.date_intent == DateTaskIntent.NONE


def test_multiturn_category_advice_keeps_the_unresolved_conflict_scenario() -> None:
    history = [
        StoredMessage(
            id="conflict-user",
            conversation_id="conflict-conversation",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.USER,
            content="我们因为旅行预算意见不合发生了争执，我准备找她认真谈谈。",
        ),
        StoredMessage(
            id="conflict-assistant",
            conversation_id="conflict-conversation",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.ASSISTANT,
            content="可以先确认她愿不愿意沟通，再选一个安静的环境聊。",
        ),
    ]

    result = route_by_rules(
        RouteInput(
            latest_query="那我先约她吃顿饭，饭后再聊，你建议选哪类菜比较合适？",
            recent_messages=history,
            active_task=TaskType.RELATIONSHIP_ADVICE,
        )
    )

    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.date_request_mode == DateRequestMode.CATEGORY_RECOMMENDATION
    assert result.primary_scenario == AdviceScenario.CONFLICT
    assert result.primary_goal == AdviceGoal.REPAIR
    assert TaskType.DATE_PLANNING not in result.task_scores


def test_concrete_place_search_overrides_advice_scenario_continuity() -> None:
    history = [
        StoredMessage(
            id="previous-conflict",
            conversation_id="place-search-conversation",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.USER,
            content="我们刚因为时间安排发生争执，之后需要找机会沟通。",
        )
    ]

    result = route_by_rules(
        RouteInput(
            latest_query="请帮我搜索浦东附近安静的川菜馆。",
            recent_messages=history,
        )
    )

    assert result.task_type == TaskType.DATE_PLANNING
    assert result.date_request_mode == DateRequestMode.PLACE_SEARCH
    assert result.date_intent == DateTaskIntent.NEW_REQUEST


def test_resolved_issue_does_not_reactivate_an_older_conflict() -> None:
    history = [
        StoredMessage(
            id="old-conflict",
            conversation_id="resolved-conversation",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.USER,
            content="我们因为周末安排发生了争执。",
        ),
        StoredMessage(
            id="resolution",
            conversation_id="resolved-conversation",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.USER,
            content="后来这件事已经说开了，分歧解决了。",
        ),
    ]

    result = route_by_rules(
        RouteInput(
            latest_query="下次见面吃什么类型的菜比较合适？",
            recent_messages=history,
        )
    )

    assert result.date_request_mode == DateRequestMode.CATEGORY_RECOMMENDATION
    assert result.primary_scenario != AdviceScenario.CONFLICT
    assert AdviceScenario.CONFLICT not in result.scenario_scores


def test_rule_router_keeps_explicit_date_planning_request() -> None:
    result = route_by_rules(
        RouteInput(latest_query="请帮我安排一次约会，推荐看电影和吃饭的具体地点。")
    )

    assert result.task_type == TaskType.DATE_PLANNING
    assert result.date_intent == DateTaskIntent.NEW_REQUEST


def test_rule_router_treats_reported_recommendations_as_relationship_context() -> None:
    queries = (
        "她给我推荐了一家西餐厅，我应该怎么接着聊？",
        "同事刚给我发了一份约会计划，我该如何回应她？",
        "对方推荐了一个电影院，我怎样自然地延续话题？",
    )

    for query in queries:
        result = route_by_rules(
            RouteInput(
                latest_query=query,
                active_task=TaskType.RELATIONSHIP_ADVICE,
            )
        )
        assert result.task_type == TaskType.RELATIONSHIP_ADVICE
        assert result.primary_scenario == AdviceScenario.PURSUIT
        assert result.primary_goal == AdviceGoal.COMMUNICATE
        assert TaskType.DATE_PLANNING not in result.task_scores
        assert result.date_plan == DatePlanSlots()


def test_rule_router_still_accepts_direct_date_search_requests() -> None:
    for query in (
        "请给我推荐一家适合约会的西餐厅",
        "麻烦帮我找一个交通方便的电影院",
        "我需要一份周末约会计划",
    ):
        result = route_by_rules(RouteInput(latest_query=query))
        assert result.task_type == TaskType.DATE_PLANNING
        assert result.date_intent == DateTaskIntent.NEW_REQUEST


def test_reported_recommendation_switches_away_from_active_date_state() -> None:
    state = DatePlanningTaskState(
        user_id="u1",
        relationship_id="r1",
        conversation_id="c1",
        status="collecting",
        city="上海",
    )

    result = route_by_rules(
        RouteInput(
            latest_query="她给我推荐了一部小说，我该如何借这个话题继续交流？",
            active_task=TaskType.DATE_PLANNING,
            date_task_state=state,
        )
    )

    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.date_intent == DateTaskIntent.SWITCH
    assert TaskType.DATE_PLANNING not in result.task_scores


def test_new_date_request_does_not_inherit_slots_from_unrelated_history() -> None:
    result = route_by_rules(
        RouteInput(
            latest_query="帮我安排一次约会，预算500元",
            recent_messages=[
                StoredMessage(
                    id="old-profile",
                    user_id="u1",
                    relationship_id="r1",
                    conversation_id="c1",
                    role=MessageRole.USER,
                    content="她是南京人，我来自南通。",
                )
            ],
        )
    )

    assert result.task_type == TaskType.DATE_PLANNING
    assert result.date_plan.city is None
    assert result.date_plan.budget == 500


def test_active_date_state_switches_for_date_action_evaluation() -> None:
    state = DatePlanningTaskState(
        user_id="u1",
        relationship_id="r1",
        conversation_id="c1",
        city="上海",
        status="planned",
    )
    result = route_by_rules(
        RouteInput(
            latest_query="我打算约她出来看电影，你看怎么样？",
            active_task=TaskType.DATE_PLANNING,
            date_task_state=state,
        )
    )

    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.date_intent == DateTaskIntent.SWITCH


def test_relationship_clarification_answer_is_not_a_date_supplement() -> None:
    now = utc_now()
    state = DatePlanningTaskState(
        user_id="u1",
        relationship_id="r1",
        conversation_id="c1",
        status="collecting",
    )
    result = route_by_rules(
        RouteInput(
            latest_query="她约我一起逛过漫展，一起逛过公园，我生病了也会关心我",
            recent_messages=[
                StoredMessage(
                    id="prior-user-question",
                    user_id="u1",
                    relationship_id="r1",
                    conversation_id="c1",
                    role=MessageRole.USER,
                    content="我怎么约她去海洋馆，如果她答应就肯定有戏，你觉得呢",
                    created_at=now,
                ),
                StoredMessage(
                    id="assistant-question",
                    user_id="u1",
                    relationship_id="r1",
                    conversation_id="c1",
                    role=MessageRole.ASSISTANT,
                    content=(
                        "海洋馆作为约会地点本身没有问题。"
                        "待确认：她之前有没有主动约你单独出去玩过？"
                    ),
                    created_at=now,
                )
            ],
            active_task=TaskType.DATE_PLANNING,
            date_task_state=state,
        )
    )

    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.date_intent == DateTaskIntent.SWITCH
    assert result.primary_scenario == AdviceScenario.PURSUIT
    assert result.primary_goal == AdviceGoal.UNDERSTAND
    assert TaskType.DATE_PLANNING not in result.task_scores


async def test_hybrid_router_does_not_allow_date_candidate_to_override_advice() -> None:
    corrector = StubCorrector(
        RouteCorrection(
            task_type=TaskType.DATE_PLANNING,
            task_confidence=0.99,
            date_intent=DateTaskIntent.NEW_REQUEST,
            evidence_spans=["约她出来看个电影"],
        )
    )
    router = HybridRouter(SafetyPolicy(), corrector)
    route_input = RouteInput(
        latest_query="行，那我打算约她出来看个电影，吃顿饭，逛个街，你看怎么样。",
        active_task=TaskType.RELATIONSHIP_ADVICE,
    )

    result = await router.route(route_input)
    rules = route_by_rules(route_input)
    correction = corrector.correction
    merged = merge_route_correction(
        route_input,
        rules,
        correction,
        allow_task_override=router._allow_task_override(route_input, rules, correction),
    )

    assert corrector.calls == 0
    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert router._allow_task_override(route_input, rules, correction) is False
    assert merged.task_type == TaskType.RELATIONSHIP_ADVICE
    assert merged.date_intent == DateTaskIntent.NONE


async def test_hybrid_router_rejects_llm_date_task_without_local_date_signal() -> None:
    corrector = StubCorrector(
        RouteCorrection(
            task_type=TaskType.DATE_PLANNING,
            task_confidence=0.99,
            date_request_mode=DateRequestMode.ITINERARY,
            date_intent=DateTaskIntent.NEW_REQUEST,
            evidence_spans=["我喜欢她"],
        )
    )

    result = await HybridRouter(SafetyPolicy(), corrector).route(
        RouteInput(latest_query="我喜欢她，有什么建议吗？")
    )

    assert corrector.calls == 1
    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.llm_task_type == TaskType.DATE_PLANNING
    assert result.task_guard_applied is True
    assert result.date_request_mode == DateRequestMode.NONE
    assert result.pending_task is None


async def test_hybrid_router_rejects_llm_secondary_date_without_local_date_signal() -> None:
    corrector = StubCorrector(
        RouteCorrection(
            task_type=TaskType.RELATIONSHIP_ADVICE,
            secondary_tasks=[TaskType.DATE_PLANNING],
            task_confidence=0.99,
            primary_goal=AdviceGoal.UNDERSTAND,
            primary_scenario=AdviceScenario.PURSUIT,
            scenario_confidence=0.9,
            evidence_spans=["我喜欢她"],
        )
    )

    result = await HybridRouter(SafetyPolicy(), corrector).route(
        RouteInput(latest_query="我喜欢她，有什么建议吗？")
    )

    assert corrector.calls == 1
    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert TaskType.DATE_PLANNING not in result.secondary_tasks
    assert result.task_guard_applied is True
    assert result.pending_task is None


async def test_hybrid_router_skips_llm_for_clear_initiation_request() -> None:
    corrector = StubCorrector(
        RouteCorrection(
            task_type=TaskType.GENERAL_CHAT,
            task_confidence=0.99,
        )
    )
    router = HybridRouter(SafetyPolicy(), corrector)

    result = await router.route(
        RouteInput(latest_query="我喜欢了一个女孩子，但她不怎么和我熟，该怎么办")
    )

    assert corrector.calls == 0
    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.primary_goal == AdviceGoal.INITIATE


async def test_hybrid_router_skips_llm_for_casual_fast_paths() -> None:
    corrector = StubCorrector(
        RouteCorrection(
            task_type=TaskType.RELATIONSHIP_ADVICE,
            task_confidence=0.1,
        )
    )
    router = HybridRouter(SafetyPolicy(), corrector)

    for query in ("下午好", "早安", "谢谢，先这样", "先这样吧"):
        result = await router.route(RouteInput(latest_query=query))
        assert result.task_type == TaskType.GENERAL_CHAT

    assert corrector.calls == 0


async def test_hybrid_router_corrects_missing_goal_for_advice_request() -> None:
    corrector = StubCorrector(
        RouteCorrection(
            task_type=TaskType.RELATIONSHIP_ADVICE,
            task_confidence=0.9,
            primary_goal=AdviceGoal.INITIATE,
            primary_scenario=AdviceScenario.PURSUIT,
            scenario_confidence=0.9,
        )
    )
    router = HybridRouter(SafetyPolicy(), corrector)

    result = await router.route(RouteInput(latest_query="我喜欢她，有什么建议吗？"))

    assert corrector.calls == 1
    assert result.primary_goal == AdviceGoal.INITIATE


@pytest.mark.parametrize(
    ("query", "expected_scenario", "expected_mode", "expected_corrector_calls"),
    (
        (
            "我和女朋友昨天因为周末去哪玩吵了一架。",
            AdviceScenario.CONFLICT,
            DateRequestMode.NONE,
            1,
        ),
        (
            "我想周六约她看电影，你觉得合适吗？",
            AdviceScenario.PURSUIT,
            DateRequestMode.EVALUATE,
            0,
        ),
        (
            "我们上周六一起吃了日料，但后来吵架了。",
            AdviceScenario.CONFLICT,
            DateRequestMode.NONE,
            0,
        ),
        (
            "她说下周工作特别忙，我担心她是不是在疏远我。",
            AdviceScenario.CHAT_ANALYSIS,
            DateRequestMode.NONE,
            1,
        ),
    ),
)
async def test_date_slot_authorization_hides_metadata_for_non_date_workflows(
    query: str,
    expected_scenario: AdviceScenario,
    expected_mode: DateRequestMode,
    expected_corrector_calls: int,
) -> None:
    detected_slots = extract_date_plan_slots(RouteInput(latest_query=query))
    corrector = StubCorrector(
        RouteCorrection(
            task_type=TaskType.RELATIONSHIP_ADVICE,
            task_confidence=0.95,
            primary_scenario=expected_scenario,
            scenario_confidence=0.9,
            date_plan=(
                {"city": "杭州"}
                if query.startswith("她说下周工作特别忙")
                else {}
            ),
        )
    )

    result = await HybridRouter(SafetyPolicy(), corrector).route(
        RouteInput(latest_query=query)
    )

    assert detected_slots.date is not None
    assert corrector.calls == expected_corrector_calls
    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.primary_scenario == expected_scenario
    assert result.date_request_mode == expected_mode
    assert result.date_plan == DatePlanSlots()
    assert result.slot_rejected_fields == {}
    assert result.slot_accepted_fields == {}
    assert result.slot_field_sources == {}


@pytest.mark.parametrize(
    ("query", "expected_fields"),
    (
        ("帮我安排周六在上海的约会。", {"city", "date", "plan_mode"}),
        ("帮我安排周末约会，预算500。", {"date", "budget", "plan_mode"}),
    ),
)
async def test_date_slot_authorization_keeps_metadata_for_date_workflows(
    query: str,
    expected_fields: set[str],
) -> None:
    result = await HybridRouter(SafetyPolicy()).route(RouteInput(latest_query=query))

    assert result.task_type == TaskType.DATE_PLANNING
    assert expected_fields <= set(result.slot_accepted_fields)
    assert all(result.slot_field_sources[field] == "rule" for field in expected_fields)
    assert result.date_plan != DatePlanSlots()


async def test_date_slot_authorization_keeps_active_date_task_supplements() -> None:
    router = HybridRouter(SafetyPolicy())
    state = DatePlanningTaskState(
        user_id="slot-supplement-user",
        relationship_id="slot-supplement-relationship",
        conversation_id="slot-supplement-conversation",
        status="collecting",
    )

    city_turn = await router.route(
        RouteInput(
            latest_query="上海。",
            active_task=TaskType.DATE_PLANNING,
            date_task_state=state,
        )
    )
    state = state.model_copy(update={"city": city_turn.date_plan.city})
    date_turn = await router.route(
        RouteInput(
            latest_query="周六下午。",
            active_task=TaskType.DATE_PLANNING,
            date_task_state=state,
        )
    )
    state = state.model_copy(update={"date": date_turn.date_plan.date})
    budget_turn = await router.route(
        RouteInput(
            latest_query="预算300。",
            active_task=TaskType.DATE_PLANNING,
            date_task_state=state,
        )
    )

    assert city_turn.task_type == TaskType.DATE_PLANNING
    assert city_turn.slot_accepted_fields["city"] == "上海"
    assert city_turn.slot_field_sources["city"] == "rule"
    assert date_turn.task_type == TaskType.DATE_PLANNING
    assert "date" in date_turn.slot_accepted_fields
    assert date_turn.slot_accepted_fields["schedule_hints"] == "下午"
    assert date_turn.slot_field_sources["date"] == "rule"
    assert budget_turn.task_type == TaskType.DATE_PLANNING
    assert budget_turn.slot_accepted_fields["budget"] == "300"
    assert budget_turn.slot_field_sources["budget"] == "rule"


async def test_date_slot_authorization_keeps_metadata_for_secondary_date_task() -> None:
    query = "先帮我分析她最近是什么状态，然后帮我安排一次周末约会。"
    corrector = StubCorrector(
        RouteCorrection(
            task_type=TaskType.RELATIONSHIP_ADVICE,
            secondary_tasks=[TaskType.DATE_PLANNING],
            task_confidence=0.95,
            primary_scenario=AdviceScenario.CHAT_ANALYSIS,
            scenario_confidence=0.9,
        )
    )

    result = await HybridRouter(SafetyPolicy(), corrector).route(
        RouteInput(latest_query=query)
    )

    assert corrector.calls == 1
    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert TaskType.DATE_PLANNING in result.secondary_tasks
    assert result.date_plan.date is not None
    assert result.slot_accepted_fields["date"] == result.date_plan.date.isoformat()
    assert result.slot_field_sources["date"] == "rule"


async def test_hybrid_router_rejects_hallucinated_semantic_date_slots() -> None:
    corrector = StubCorrector(
        RouteCorrection(
            task_type=TaskType.DATE_PLANNING,
            task_confidence=0.92,
            date_intent=DateTaskIntent.NEW_REQUEST,
            date_plan={"city": "杭州", "budget": 500},
            evidence_spans=["周末和她找个地方坐坐"],
        )
    )
    router = HybridRouter(SafetyPolicy(), corrector)

    result = await router.route(
        RouteInput(latest_query="周末想和她找个地方坐坐，能帮我安排得舒服一点吗？")
    )

    assert corrector.calls == 1
    assert result.task_type == TaskType.DATE_PLANNING
    assert result.date_intent == DateTaskIntent.NEW_REQUEST
    assert result.date_plan.city is None
    assert result.date_plan.budget is None
    assert result.slot_rejected_fields == {
        "city": "no_source_evidence",
        "budget": "no_source_evidence",
    }


async def test_hybrid_router_accepts_supported_slot_but_drops_hallucinated_peer() -> None:
    corrector = StubCorrector(
        RouteCorrection(
            task_type=TaskType.DATE_PLANNING,
            task_confidence=0.92,
            date_intent=DateTaskIntent.NEW_REQUEST,
            date_plan={"city": "杭州", "budget": 300},
            evidence_spans=["周末想和她找个地方坐坐，预算300"],
        )
    )
    router = HybridRouter(SafetyPolicy(), corrector)

    result = await router.route(
        RouteInput(latest_query="周末想和她找个地方坐坐，预算300，能帮我安排得舒服一点吗？")
    )

    assert corrector.calls == 1
    assert result.date_plan.budget == 300
    assert result.date_plan.city is None
    assert result.slot_field_sources["budget"] == "rule"
    assert result.slot_rejected_fields["city"] == "no_source_evidence"


async def test_active_date_state_distinguishes_supplement_from_task_switch() -> None:
    corrector = QueryCorrector(
        {
            "上海": RouteCorrection(
                task_type=TaskType.DATE_PLANNING,
                task_confidence=0.95,
                date_intent=DateTaskIntent.SUPPLEMENT,
                date_plan={"city": "上海"},
                evidence_spans=["上海"],
            ),
            "她不理我怎么办": RouteCorrection(
                task_type=TaskType.RELATIONSHIP_ADVICE,
                task_confidence=0.94,
                date_intent=DateTaskIntent.SWITCH,
                primary_scenario=AdviceScenario.CHAT_ANALYSIS,
                scenario_confidence=0.9,
                evidence_spans=["她不理我"],
            ),
        }
    )
    router = HybridRouter(SafetyPolicy(), corrector)
    state = DatePlanningTaskState(
        user_id="u1",
        relationship_id="r1",
        conversation_id="c1",
        missing_fields=["city"],
    )

    supplement = await router.route(
        RouteInput(
            latest_query="上海",
            active_task=TaskType.DATE_PLANNING,
            date_task_state=state,
        )
    )
    switched = await router.route(
        RouteInput(
            latest_query="她不理我怎么办",
            active_task=TaskType.DATE_PLANNING,
            date_task_state=state,
        )
    )

    assert supplement.task_type == TaskType.DATE_PLANNING
    assert supplement.date_intent == DateTaskIntent.SUPPLEMENT
    assert switched.task_type == TaskType.RELATIONSHIP_ADVICE
    assert switched.date_intent == DateTaskIntent.SWITCH
    # A clearly structured city answer uses the state-aware fast path;
    # the natural-language relationship switch still needs semantic review.
    assert corrector.calls == 1


def test_rule_router_extracts_date_slots() -> None:
    result = route_by_rules(
        RouteInput(latest_query="帮我安排在杭州西湖区的约会，预算500元，喜欢安静和展览，坐地铁。")
    )

    assert result.task_type == TaskType.DATE_PLANNING
    assert result.date_plan.city == "杭州"
    assert result.date_plan.area == "西湖区"
    assert result.date_plan.budget == 500
    assert result.date_plan.preferences == ["安静", "展览"]
    assert result.date_plan.transport_mode.value == "transit"


def test_rule_router_extracts_exact_place_search_keywords() -> None:
    result = route_by_rules(
        RouteInput(
            latest_query="帮我安排约会，地点定在上海静安区，晚饭想吃西餐，也想去博物馆。"
        )
    )

    assert result.task_type == TaskType.DATE_PLANNING
    assert result.date_plan.city == "上海"
    assert result.date_plan.area == "静安区"
    assert result.date_plan.dining_keywords == ["西餐"]
    assert result.date_plan.activity_keywords == ["博物馆"]
    assert result.date_plan.excluded_keywords == []


def test_rule_router_extracts_explicit_place_exclusion() -> None:
    result = route_by_rules(
        RouteInput(latest_query="在上海约会，晚饭不要火锅，想去博物馆。")
    )

    assert result.date_plan.excluded_keywords == ["火锅"]
    assert result.date_plan.activity_keywords == ["博物馆"]


def test_active_task_routes_slot_only_follow_up() -> None:
    result = route_by_rules(
        RouteInput(
            latest_query="预算改成300元",
            active_task=TaskType.DATE_PLANNING,
        )
    )

    assert result.task_type == TaskType.DATE_PLANNING
    assert result.date_plan.budget == 300


async def test_hybrid_router_keeps_exact_place_supplement_on_fast_path() -> None:
    corrector = StubCorrector(
        RouteCorrection(
            task_type=TaskType.RELATIONSHIP_ADVICE,
            task_confidence=0.1,
        )
    )
    router = HybridRouter(SafetyPolicy(), corrector)
    state = DatePlanningTaskState(
        user_id="fast-place-user",
        relationship_id="fast-place-relationship",
        conversation_id="fast-place-conversation",
        missing_fields=["city"],
    )

    result = await router.route(
        RouteInput(
            latest_query="西餐",
            active_task=TaskType.DATE_PLANNING,
            date_task_state=state,
        )
    )

    assert corrector.calls == 0
    assert result.task_type == TaskType.DATE_PLANNING
    assert result.date_plan.dining_keywords == ["西餐"]


def test_rule_router_distinguishes_date_plan_add_from_replan() -> None:
    state = DatePlanningTaskState(
        user_id="u1",
        relationship_id="r1",
        conversation_id="c1",
        city="上海",
        budget=1000,
        status="planned",
    )
    added = route_by_rules(
        RouteInput(
            latest_query="我还想增加一些上海经典旅游景点，也帮我安排到行程中",
            date_task_state=state,
        )
    )
    replanned = route_by_rules(
        RouteInput(
            latest_query="重新帮我规划一份约会行程",
            date_task_state=state,
        )
    )

    assert added.date_intent == DateTaskIntent.SUPPLEMENT
    assert added.date_mutation == DatePlanMutation.ADD
    assert replanned.date_mutation == DatePlanMutation.REPLAN


def test_date_slots_preserve_meal_roles_and_relative_timing() -> None:
    slots = extract_date_plan_slots(
        RouteInput(
            latest_query=(
                "对了，下午准备去看场电影，同时晚饭吃火锅（日料是午餐），"
                "看完电影想去个景点逛逛"
            )
        )
    )

    assert slots.dining_keywords == ["日料", "火锅"]
    assert slots.meal_keywords == {"lunch": ["日料"], "dinner": ["火锅"]}
    assert slots.activity_keywords == ["景点", "电影院"]
    assert "下午" in slots.schedule_hints
    assert "电影后安排活动" in slots.schedule_hints


def test_date_slots_extract_named_replacement_and_brand_meals() -> None:
    state = DatePlanningTaskState(
        user_id="u1",
        relationship_id="r1",
        conversation_id="c1",
        city="上海",
        budget=1000,
        status="planned",
    )
    result = route_by_rules(
        RouteInput(
            latest_query=(
                "我想晚上吃海底捞，中午吃韩国料理，然后下午不去 "
                "辅德里公园，换一个博物馆"
            ),
            date_task_state=state,
        )
    )

    assert result.task_type == TaskType.DATE_PLANNING
    assert result.date_intent == DateTaskIntent.SUPPLEMENT
    assert result.date_mutation == DatePlanMutation.REPLACE
    assert result.date_plan.dining_keywords == ["韩国料理", "海底捞"]
    assert result.date_plan.meal_keywords == {
        "lunch": ["韩国料理"],
        "dinner": ["海底捞"],
    }
    assert result.date_plan.activity_keywords == ["博物馆"]
    assert result.date_plan.replace_place_names == ["辅德里公园"]
    assert result.date_plan.excluded_keywords == ["辅德里公园"]


def test_llm_constraint_correction_cannot_erase_implicit_addition() -> None:
    cinema = Place(
        id="cinema",
        name="百美汇电影院",
        city="上海",
        address="静安区",
        category=PlaceCategory.ENTERTAINMENT,
        estimated_cost_per_person=100,
        source="test",
    )
    japanese = Place(
        id="japanese",
        name="日料餐厅",
        city="上海",
        address="静安区",
        category=PlaceCategory.RESTAURANT,
        tags=["日料"],
        estimated_cost_per_person=150,
        source="test",
    )
    state = DatePlanningTaskState(
        user_id="u1",
        relationship_id="r1",
        conversation_id="c1",
        city="上海",
        budget=1000,
        status="planned",
        current_plan=DatePlan(
            title="上海约会计划",
            summary="现有行程",
            items=[
                DatePlanItem(
                    order=1,
                    place=cinema,
                    duration_minutes=90,
                    estimated_cost=200,
                    reason="电影",
                    slot_keyword="电影院",
                ),
                DatePlanItem(
                    order=2,
                    place=japanese,
                    duration_minutes=90,
                    estimated_cost=300,
                    reason="午餐",
                    meal_type="lunch",
                    slot_keyword="日料",
                ),
            ],
            total_estimated_cost=500,
            total_duration_minutes=180,
            data_source="test",
        ),
        plan_version=1,
    )
    route_input = RouteInput(
        latest_query=(
            "下午准备去看电影，晚饭吃火锅，日料是午餐，"
            "看完电影想去个景点逛逛"
        ),
        date_task_state=state,
    )
    rules = route_by_rules(route_input)
    result = merge_route_correction(
        route_input,
        rules,
        RouteCorrection(
            task_type=TaskType.DATE_PLANNING,
            task_confidence=0.92,
            date_intent=DateTaskIntent.SUPPLEMENT,
            date_mutation=DatePlanMutation.UPDATE_CONSTRAINT,
        ),
    )

    assert result.date_intent == DateTaskIntent.SUPPLEMENT
    assert result.date_mutation == DatePlanMutation.ADD


async def test_hybrid_router_uses_llm_for_explicit_cross_task_correction() -> None:
    corrector = StubCorrector(
        RouteCorrection(
            task_type=TaskType.RELATIONSHIP_ADVICE,
            task_confidence=0.91,
            primary_goal=AdviceGoal.PROGRESS,
            secondary_goals=[AdviceGoal.UNDERSTAND],
            secondary_tasks=[TaskType.DATE_PLANNING],
            primary_scenario=AdviceScenario.CHAT_ANALYSIS,
            scenario_confidence=0.9,
            evidence_spans=["分析她为什么冷淡", "推荐一家适合约会的餐厅"],
        )
    )
    router = HybridRouter(SafetyPolicy(), corrector)

    result = await router.route(
        RouteInput(latest_query="先分析她为什么冷淡，再帮我推荐一家适合约会的餐厅。")
    )

    assert corrector.calls == 1
    assert result.source == RouteSource.HYBRID
    assert result.llm_used is True
    assert result.primary_goal == AdviceGoal.PROGRESS
    assert result.secondary_tasks == [TaskType.DATE_PLANNING]


async def test_hybrid_router_preserves_explicit_compound_task_order() -> None:
    corrector = StubCorrector(
        RouteCorrection(
            task_type=TaskType.DATE_PLANNING,
            secondary_tasks=[TaskType.RELATIONSHIP_ADVICE],
            task_confidence=0.99,
            date_request_mode=DateRequestMode.ITINERARY,
            date_intent=DateTaskIntent.NEW_REQUEST,
            evidence_spans=["先帮我判断她是不是对我有好感", "再帮我安排周末约会"],
        )
    )

    result = await HybridRouter(SafetyPolicy(), corrector).route(
        RouteInput(latest_query="先帮我判断她是不是对我有好感，再帮我安排周末约会。")
    )

    assert corrector.calls == 1
    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.secondary_tasks == [TaskType.DATE_PLANNING]
    assert result.pending_task == TaskType.DATE_PLANNING
    assert result.task_guard_applied is True


@pytest.mark.parametrize(
    "query",
    (
        "帮我先判断她是不是对我有好感，再帮我安排周末约会。",
        "请你先判断她是不是对我有好感，然后安排周末约会。",
        "请你帮我先判断她是不是对我有好感，然后安排周末约会。",
        "我想先判断她是不是对我有好感，再帮我安排周末约会。",
        (
            "帮我先分析她最近为什么冷淡，她不理我，我们还吵架了，"
            "再帮我推荐一家适合约会的餐厅。"
        ),
    ),
)
async def test_hybrid_router_preserves_prefixed_explicit_compound_task_order(
    query: str,
) -> None:
    corrector = StubCorrector(
        RouteCorrection(
            task_type=TaskType.DATE_PLANNING,
            secondary_tasks=[TaskType.RELATIONSHIP_ADVICE],
            task_confidence=0.99,
            date_request_mode=DateRequestMode.ITINERARY,
            date_intent=DateTaskIntent.NEW_REQUEST,
        )
    )

    result = await HybridRouter(SafetyPolicy(), corrector).route(
        RouteInput(latest_query=query)
    )

    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.secondary_tasks == [TaskType.DATE_PLANNING]
    assert result.pending_task == TaskType.DATE_PLANNING
    assert result.task_guard_applied is True


async def test_hybrid_router_rejects_llm_date_planning_for_relationship_actions() -> None:
    for query in (
        "我想约她看电影，但不知道怎么开口？",
        "她上次约我看电影，我该怎么回复？",
    ):
        corrector = StubCorrector(
            RouteCorrection(
                task_type=TaskType.DATE_PLANNING,
                task_confidence=0.99,
                date_request_mode=DateRequestMode.ITINERARY,
                date_intent=DateTaskIntent.NEW_REQUEST,
            )
        )

        result = await HybridRouter(SafetyPolicy(), corrector).route(
            RouteInput(latest_query=query)
        )

        assert result.task_type == TaskType.RELATIONSHIP_ADVICE
        assert result.llm_task_type == TaskType.DATE_PLANNING
        assert result.task_guard_applied is True


async def test_hybrid_router_keeps_weak_date_request_in_product_scope() -> None:
    corrector = StubCorrector(
        RouteCorrection(
            task_type=TaskType.OUT_OF_SCOPE,
            task_confidence=0.99,
            evidence_spans=["约会"],
        )
    )

    result = await HybridRouter(SafetyPolicy(), corrector).route(
        RouteInput(latest_query="约会怎么弄比较好？")
    )

    assert result.task_type != TaskType.OUT_OF_SCOPE
    assert result.llm_task_type == TaskType.OUT_OF_SCOPE
    assert result.task_guard_applied is True


async def test_hybrid_router_keeps_weak_relationship_request_in_product_scope() -> None:
    corrector = StubCorrector(
        RouteCorrection(
            task_type=TaskType.OUT_OF_SCOPE,
            task_confidence=0.99,
            evidence_spans=["感情怎么经营"],
        )
    )

    result = await HybridRouter(SafetyPolicy(), corrector).route(
        RouteInput(latest_query="感情怎么经营？")
    )

    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.llm_task_type == TaskType.OUT_OF_SCOPE
    assert result.task_guard_applied is True


async def test_hybrid_router_does_not_downgrade_advice_to_general_chat() -> None:
    corrector = StubCorrector(
        RouteCorrection(
            task_type=TaskType.GENERAL_CHAT,
            task_confidence=0.99,
            scenario_confidence=0.2,
        )
    )
    router = HybridRouter(SafetyPolicy(), corrector)
    route_input = RouteInput(
        latest_query="那她怎么办？",
        recent_messages=[
            StoredMessage(
                id="m1",
                conversation_id="c1",
                user_id="u1",
                relationship_id="r1",
                role=MessageRole.USER,
                content="我喜欢她，但最近聊天情况有点复杂。",
            )
        ],
    )
    result = await router.route(route_input)
    rules = route_by_rules(route_input)
    merged = merge_route_correction(
        route_input,
        rules,
        corrector.correction,
        allow_task_override=router._allow_task_override(
            route_input,
            rules,
            corrector.correction,
        ),
    )

    assert corrector.calls == 0
    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.rule_task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.llm_task_type is None
    assert result.primary_scenario == AdviceScenario.PURSUIT
    assert merged.task_type == TaskType.RELATIONSHIP_ADVICE
    assert merged.task_guard_applied is True


def test_rule_router_handles_boundary_breakup_and_task_switch_phrases() -> None:
    boundary = route_by_rules(RouteInput(latest_query="她说想冷静几天，我该怎么尊重她？"))
    assert boundary.primary_scenario == AdviceScenario.BOUNDARY
    assert boundary.primary_goal == AdviceGoal.SET_BOUNDARY

    breakup = route_by_rules(RouteInput(latest_query="我们分手了，我还想复合，应该先做什么？"))
    assert breakup.primary_scenario == AdviceScenario.BREAKUP
    assert breakup.primary_goal == AdviceGoal.REPAIR

    switched = route_by_rules(
        RouteInput(latest_query="先不安排约会了，她刚刚明确拒绝我，我该怎么办？")
    )
    assert switched.task_type == TaskType.RELATIONSHIP_ADVICE
    assert switched.primary_scenario == AdviceScenario.BOUNDARY


def test_rule_router_keeps_clear_multi_labels_without_requiring_llm() -> None:
    result = route_by_rules(
        RouteInput(latest_query="我喜欢她，最近她愿意和我闲聊，我该怎么进一步发展？")
    )

    assert result.primary_scenario == AdviceScenario.PURSUIT
    assert AdviceScenario.CHAT_ANALYSIS in result.secondary_scenarios


async def test_high_risk_rules_cannot_be_downgraded_by_llm() -> None:
    corrector = StubCorrector(
        RouteCorrection(
            task_type=TaskType.GENERAL_CHAT,
            task_confidence=0.99,
        )
    )
    router = HybridRouter(SafetyPolicy(), corrector)

    result = await router.route(RouteInput(latest_query="我想跟踪她并报复她。"))

    assert result.risk_level == RiskLevel.HIGH
    assert corrector.calls == 0


async def test_sensitive_safety_rules_do_not_wait_for_llm_correction() -> None:
    corrector = StubCorrector(
        RouteCorrection(
            task_type=TaskType.RELATIONSHIP_ADVICE,
            task_confidence=0.99,
        )
    )
    router = HybridRouter(SafetyPolicy(), corrector)

    result = await router.route(RouteInput(latest_query="怎样避免伤害自己？"))

    assert result.risk_level == RiskLevel.SENSITIVE
    assert corrector.calls == 0
    assert result.llm_used is False


def test_safety_policy_handles_synonyms_negation_and_figures_of_speech() -> None:
    policy = SafetyPolicy()

    assert policy.assess("她不回我，我准备每天尾随她。").risk_level == RiskLevel.HIGH
    assert policy.assess("我想伤害她。").risk_level == RiskLevel.HIGH
    assert policy.assess("她说要从楼上跳下去。").risk_level == RiskLevel.HIGH
    assert policy.assess("他不顾我说不要，仍然强行发生亲密行为。").risk_level == RiskLevel.HIGH
    assert policy.assess("我不会跟踪或骚扰她。").risk_level == RiskLevel.NORMAL
    assert policy.assess("我不会伤害她，但是我会杀她。").risk_level == RiskLevel.HIGH
    assert policy.assess("我不会伤害她，我要报复她。").risk_level == RiskLevel.HIGH
    assert policy.assess("我不会伤害她,我会杀她。").risk_level == RiskLevel.HIGH
    assert policy.assess("怎样避免伤害自己？").risk_level == RiskLevel.SENSITIVE
    assert policy.assess("这次比赛要杀死比赛。").risk_level == RiskLevel.NORMAL


async def test_router_sends_negated_clause_followed_by_threat_to_safety_branch() -> None:
    corrector = StubCorrector(
        RouteCorrection(task_type=TaskType.RELATIONSHIP_ADVICE, task_confidence=0.99)
    )

    result = await HybridRouter(SafetyPolicy(), corrector).route(
        RouteInput(latest_query="我不会伤害她，但是我会杀她。")
    )

    assert result.risk_level == RiskLevel.HIGH
    assert corrector.calls == 0


async def test_router_routes_underspecified_input_to_clarification() -> None:
    result = await HybridRouter(SafetyPolicy()).route(RouteInput(latest_query="你觉得这样行吗？"))

    assert result.task_type == TaskType.GENERAL_CHAT
    assert result.clarification_triggered is True
    assert result.clarification_reason == "ambiguous_cross_domain_intent"
    assert result.clarification_options == ["分析这段关系", "安排一次约会"]


async def test_router_keeps_active_relationship_context_without_repeating_clarification() -> None:
    result = await HybridRouter(SafetyPolicy()).route(
        RouteInput(
            latest_query="你觉得这样行吗？",
            active_task=TaskType.RELATIONSHIP_ADVICE,
            recent_messages=[
                StoredMessage(
                    id="context-relationship",
                    conversation_id="c1",
                    user_id="u1",
                    relationship_id="r1",
                    role=MessageRole.USER,
                    content="我准备向她表白，但担心太突然。",
                )
            ],
        )
    )

    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.clarification_triggered is False


async def test_router_does_not_inherit_a_stale_active_task_for_an_ambiguous_query() -> None:
    result = await HybridRouter(SafetyPolicy()).route(
        RouteInput(
            latest_query="你觉得这样行吗？",
            active_task=TaskType.RELATIONSHIP_ADVICE,
            recent_messages=[
                StoredMessage(
                    id="unrelated-history",
                    conversation_id="c1",
                    user_id="u1",
                    relationship_id="r1",
                    role=MessageRole.ASSISTANT,
                    content="你好，我在。",
                )
            ],
        )
    )

    assert result.clarification_triggered is True


async def test_router_exhausts_repeated_clarification_without_asking_again() -> None:
    result = await HybridRouter(SafetyPolicy()).route(
        RouteInput(
            latest_query="你觉得这样行吗？",
            last_clarification_reason="ambiguous_cross_domain_intent",
            clarification_attempt_count=1,
        )
    )

    assert result.clarification_triggered is False
    assert result.clarification_exhausted is True
    assert result.clarification_options == []


def test_clarification_repeat_uses_the_configured_threshold() -> None:
    route = route_by_rules(RouteInput(latest_query="我喜欢她，有什么建议吗？")).model_copy(
        update={"needs_clarification": True, "task_confidence": 0.70}
    )

    triggered, reason, options = should_clarify_route(
        RouteInput(
            latest_query="我喜欢她，有什么建议吗？",
            last_clarification_reason="ambiguous_cross_domain_intent",
            clarification_attempt_count=1,
        ),
        route,
        clarification_threshold=0.80,
    )

    assert triggered is False
    assert reason == "ambiguous_cross_domain_intent"
    assert options == []


def test_rule_router_distinguishes_out_of_scope_from_relationship_context() -> None:
    out_of_scope = route_by_rules(RouteInput(latest_query="帮我写一个 Python 爬虫"))
    relationship = route_by_rules(
        RouteInput(latest_query="我和她因为我总写代码吵架了怎么办？")
    )

    assert out_of_scope.task_type == TaskType.OUT_OF_SCOPE
    assert relationship.task_type == TaskType.RELATIONSHIP_ADVICE


@pytest.mark.parametrize(
    "query",
    (
        "帮我写一个Excel公式",
        "帮我做一个简历",
        "帮我写一个用户登录系统",
        "帮我分析一下这个bug",
        "帮我写一封求职邮件",
    ),
)
async def test_router_routes_extended_non_product_work_requests_out_of_scope(
    query: str,
) -> None:
    result = await HybridRouter(SafetyPolicy()).route(
        RouteInput(
            latest_query=query,
            pending_task=TaskType.DATE_PLANNING,
            pending_task_reason="previous secondary task",
            pending_task_turns_remaining=2,
        )
    )

    assert result.task_type == TaskType.OUT_OF_SCOPE
    assert result.pending_task is None


async def test_router_clears_pending_for_high_risk_interruption() -> None:
    result = await HybridRouter(SafetyPolicy()).route(
        RouteInput(
            latest_query="我拿刀去找她。",
            pending_task=TaskType.DATE_PLANNING,
            pending_task_reason="previous secondary task",
            pending_task_turns_remaining=2,
        )
    )

    assert result.risk_level == RiskLevel.HIGH
    assert result.pending_task is None


def test_rule_router_treats_date_category_comparisons_as_relationship_advice() -> None:
    for query in (
        "第一次约会吃火锅还是喝咖啡更自然？",
        "安静的活动类型有哪些？",
        "她怕尴尬，我应该选什么类型的活动？",
    ):
        result = route_by_rules(RouteInput(latest_query=query))
        assert result.task_type == TaskType.RELATIONSHIP_ADVICE
        assert result.date_request_mode == DateRequestMode.CATEGORY_RECOMMENDATION


async def test_hybrid_router_falls_back_to_rules_when_corrector_fails() -> None:
    class FailingCorrector:
        def __init__(self) -> None:
            self.last_telemetry = {
                "model": "test-router",
                "input_tokens": 17,
                "output_tokens": 3,
                "duration_ms": 12.5,
            }

        async def correct(self, route_input, rule_result):
            del route_input, rule_result
            raise TimeoutError("timed out")

        async def aclose(self) -> None:
            return None

    result = await HybridRouter(SafetyPolicy(), FailingCorrector()).route(
        RouteInput(latest_query="我喜欢她，有什么建议吗？")
    )

    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.fallback_reason == "llm_correction_failed"
    assert result.llm_error == "timed out"
    assert result.router_model == "test-router"
    assert result.router_input_tokens == 17
    assert result.router_output_tokens == 3
    assert result.router_duration_ms == 12.5


async def test_hybrid_router_falls_back_when_corrector_telemetry_is_not_a_mapping() -> None:
    class FailingCorrector:
        last_telemetry = "invalid telemetry"

        async def correct(self, route_input, rule_result):
            del route_input, rule_result
            raise TimeoutError("timed out")

        async def aclose(self) -> None:
            return None

    result = await HybridRouter(SafetyPolicy(), FailingCorrector()).route(
        RouteInput(latest_query="我喜欢她，有什么建议吗？")
    )

    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.fallback_reason == "llm_correction_failed"
    assert result.router_model is None


async def test_router_inherits_recent_high_risk_context_only_for_continuation() -> None:
    history = [
        StoredMessage(
            id="risk-context",
            conversation_id="c1",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.USER,
            content="我拿刀去找她。",
        )
    ]
    router = HybridRouter(SafetyPolicy())

    continued = await router.route(
        RouteInput(latest_query="我现在到她楼下了。", recent_messages=history)
    )
    deescalated = await router.route(
        RouteInput(
            latest_query="我已经回家，把刀交给家人了。",
            recent_messages=history,
        )
    )

    assert continued.risk_level == RiskLevel.HIGH
    assert continued.recent_risk_inherited is True
    assert deescalated.risk_level != RiskLevel.HIGH
    assert deescalated.recent_risk_deescalated is True


async def test_recent_high_risk_is_not_downgraded_by_negated_or_ineffective_deescalation() -> None:
    history = [
        StoredMessage(
            id="risk-context",
            conversation_id="c1",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.USER,
            content="我拿刀去找她。",
        )
    ]
    router = HybridRouter(SafetyPolicy())

    for query in ("我不会停下，现在就进去。", "我现在到她楼下了，联系家人也没用。"):
        result = await router.route(RouteInput(latest_query=query, recent_messages=history))
        assert result.risk_level == RiskLevel.HIGH
        assert result.recent_risk_inherited is True

    waiting = await router.route(
        RouteInput(latest_query="我还在等她出来。", recent_messages=history)
    )
    assert waiting.risk_level == RiskLevel.HIGH


async def test_recent_high_risk_continuation_variants_stay_high() -> None:
    history = [
        StoredMessage(
            id="risk-context-variants",
            conversation_id="c1",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.USER,
            content="我拿刀去找她。",
        )
    ]
    router = HybridRouter(SafetyPolicy())

    for query in (
        "我已经上楼了。",
        "我正在敲门。",
        "我正在拉门。",
        "我站在她门口。",
        "我进她家了。",
    ):
        result = await router.route(RouteInput(latest_query=query, recent_messages=history))
        assert result.risk_level == RiskLevel.HIGH
        assert result.recent_risk_inherited is True


async def test_router_clears_same_type_pending_for_an_explicit_new_task() -> None:
    result = await HybridRouter(SafetyPolicy()).route(
        RouteInput(
            latest_query="请从头帮我安排上海周六的约会。",
            pending_task=TaskType.DATE_PLANNING,
            pending_task_reason="previous secondary task",
            pending_task_turns_remaining=2,
        )
    )

    assert result.task_type == TaskType.DATE_PLANNING
    assert result.date_intent == DateTaskIntent.NEW_REQUEST
    assert result.pending_task is None

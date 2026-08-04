from loveapp.application.routing import (
    HybridRouter,
    extract_date_plan_slots,
    merge_route_correction,
    route_by_rules,
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


def test_rule_router_respects_negation() -> None:
    result = route_by_rules(
        RouteInput(latest_query="我们没有吵架，只是最近聊天少了，我想知道她为什么不回复。")
    )

    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.primary_scenario == AdviceScenario.CHAT_ANALYSIS
    assert AdviceScenario.CONFLICT not in result.scenario_scores


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

    for query in ("下午好", "早安", "谢谢，先这样"):
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


async def test_hybrid_router_uses_llm_for_semantic_date_intent() -> None:
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
    assert result.date_plan.city == "杭州"


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


async def test_hybrid_router_does_not_downgrade_advice_to_general_chat() -> None:
    corrector = StubCorrector(
        RouteCorrection(
            task_type=TaskType.GENERAL_CHAT,
            task_confidence=0.99,
            scenario_confidence=0.2,
        )
    )
    router = HybridRouter(SafetyPolicy(), corrector)

    result = await router.route(
        RouteInput(
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
    )

    assert corrector.calls == 1
    assert result.task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.rule_task_type == TaskType.RELATIONSHIP_ADVICE
    assert result.llm_task_type == TaskType.GENERAL_CHAT
    assert result.task_guard_applied is True
    assert result.task_confidence < 0.99
    assert result.primary_scenario == AdviceScenario.RELATIONSHIP_MAINTENANCE


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


def test_safety_policy_handles_synonyms_negation_and_figures_of_speech() -> None:
    policy = SafetyPolicy()

    assert policy.assess("她不回我，我准备每天尾随她。").risk_level == RiskLevel.HIGH
    assert policy.assess("她说要从楼上跳下去。").risk_level == RiskLevel.HIGH
    assert policy.assess("他不顾我说不要，仍然强行发生亲密行为。").risk_level == RiskLevel.HIGH
    assert policy.assess("我不会跟踪或骚扰她。").risk_level == RiskLevel.NORMAL
    assert policy.assess("这次比赛要杀死比赛。").risk_level == RiskLevel.NORMAL

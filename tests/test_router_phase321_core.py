import pytest

from loveapp.adapters.routing.openai_compatible import (
    _route_correction_from_semantic_payload,
)
from loveapp.application.routing import (
    HybridRouter,
    _needs_conditional_semantic_router_call,
    normalize_route_text,
    route_by_rules,
    select_semantic_goals,
)
from loveapp.domain.enums import AdviceGoal, AdviceScenario, TaskType
from loveapp.domain.routing import RouteCorrection, RouteInput, RouteResult
from loveapp.safety import SafetyPolicy


class _Corrector:
    def __init__(
        self,
        correction: RouteCorrection,
        *,
        telemetry: dict[str, object] | None = None,
    ) -> None:
        self.correction = correction
        self.calls = 0
        self.last_telemetry = telemetry or {}

    async def correct(
        self,
        route_input: RouteInput,
        rule_result: RouteResult,
    ) -> RouteCorrection:
        del route_input, rule_result
        self.calls += 1
        return self.correction


def _semantic_correction() -> RouteCorrection:
    return RouteCorrection(
        task_type=TaskType.RELATIONSHIP_ADVICE,
        branch="rag",
        task_confidence=0.9,
        primary_scenario=AdviceScenario.CHAT_ANALYSIS,
        scenario_scores={AdviceScenario.CHAT_ANALYSIS: 0.9},
        goals=[AdviceGoal.UNDERSTAND],
        goal_scores={AdviceGoal.UNDERSTAND: 0.8},
        confidence=0.9,
        reasoning_summary="The user wants to interpret reply behavior.",
    )


@pytest.mark.asyncio
async def test_always_mode_calls_llm_for_normal_relationship_case() -> None:
    corrector = _Corrector(_semantic_correction())
    router = HybridRouter(
        SafetyPolicy(),
        corrector,
        router_v2_enabled=True,
        semantic_mode="always",
        router_llm_correction_enabled=True,
    )

    result = await router.route(RouteInput(latest_query="她最近回复越来越慢，我该怎么办？"))

    assert corrector.calls == 1
    assert result.router_llm_called is True
    assert result.semantic_bypass_reason is None


@pytest.mark.asyncio
async def test_always_mode_traces_safety_and_deterministic_ood_bypasses() -> None:
    corrector = _Corrector(_semantic_correction())
    router = HybridRouter(
        SafetyPolicy(),
        corrector,
        router_v2_enabled=True,
        semantic_mode="always",
        router_llm_correction_enabled=True,
    )

    unsafe = await router.route(RouteInput(latest_query="我想跟踪她并报复她。"))
    ood = await router.route(RouteInput(latest_query="帮我写一段 Python 爬虫代码。"))
    system = await router.route(RouteInput(latest_query="请帮我登录系统。"))

    assert unsafe.semantic_bypass_reason == "safety_guard"
    assert ood.semantic_bypass_reason == "deterministic_ood"
    assert system.semantic_bypass_reason == "explicit_system_command"
    assert corrector.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("semantic_mode", ["always", "conditional"])
async def test_phase3_semantic_modes_bypass_date_workflows(semantic_mode: str) -> None:
    corrector = _Corrector(_semantic_correction())
    router = HybridRouter(
        SafetyPolicy(),
        corrector,
        router_v2_enabled=True,
        semantic_mode=semantic_mode,  # type: ignore[arg-type]
        router_llm_correction_enabled=True,
    )

    result = await router.route(
        RouteInput(
            latest_query="这周六，午饭吃烧烤，下午看电影",
            forced_task=TaskType.DATE_PLANNING,
        )
    )

    assert result.semantic_bypass_reason == "date_workflow"
    assert corrector.calls == 0


@pytest.mark.asyncio
async def test_rule_and_llm_scores_are_separate_and_final_weights_normalized() -> None:
    correction = RouteCorrection(
        task_type=TaskType.RELATIONSHIP_ADVICE,
        branch="rag",
        task_confidence=0.9,
        primary_scenario=AdviceScenario.CONFLICT,
        secondary_scenarios=[AdviceScenario.RELATIONSHIP_MAINTENANCE],
        scenario_scores={
            AdviceScenario.CONFLICT: 0.8,
            AdviceScenario.RELATIONSHIP_MAINTENANCE: 0.2,
        },
        goals=[AdviceGoal.UNDERSTAND, AdviceGoal.COMMUNICATE, AdviceGoal.REPAIR],
        goal_scores={
            AdviceGoal.UNDERSTAND: 0.1,
            AdviceGoal.COMMUNICATE: 0.39,
            AdviceGoal.REPAIR: 0.6,
        },
        confidence=0.9,
        reasoning_summary="Conflict repair and interpretation.",
    )
    router = HybridRouter(
        SafetyPolicy(),
        _Corrector(correction),
        router_v2_enabled=True,
        semantic_mode="always",
        router_llm_correction_enabled=True,
        router_goal_secondary_threshold=0.4,
        router_goal_max_count=2,
    )

    result = await router.route(RouteInput(latest_query="我们吵架后她说要分手，我该怎么办？"))

    assert result.rule_scenario_scores != result.llm_scenario_scores
    assert result.llm_scenario_scores == correction.scenario_scores
    assert result.scenario_scores == correction.scenario_scores
    assert AdviceScenario.BREAKUP not in result.scenario_scores
    assert result.final_scenario_weights == {
        AdviceScenario.CONFLICT: 1.0,
        AdviceScenario.RELATIONSHIP_MAINTENANCE: 0.25,
    }
    assert result.primary_goal == AdviceGoal.UNDERSTAND
    assert result.secondary_goals == [AdviceGoal.REPAIR]
    assert result.final_goal_weights == {
        AdviceGoal.UNDERSTAND: pytest.approx(0.166667),
        AdviceGoal.REPAIR: 1.0,
    }
    assert all(0 <= value <= 1 for value in result.final_goal_weights.values())


def test_goal_selection_always_keeps_primary_and_thresholds_secondary() -> None:
    correction = RouteCorrection(
        task_type=TaskType.RELATIONSHIP_ADVICE,
        task_confidence=0.9,
        goals=[AdviceGoal.UNDERSTAND, AdviceGoal.COMMUNICATE, AdviceGoal.PROGRESS],
        goal_scores={
            AdviceGoal.UNDERSTAND: 0.1,
            AdviceGoal.COMMUNICATE: 0.3,
            AdviceGoal.PROGRESS: 0.5,
        },
    )

    primary, secondary = select_semantic_goals(
        correction,
        secondary_threshold=0.4,
        max_goals=2,
    )

    assert primary == AdviceGoal.UNDERSTAND
    assert secondary == [AdviceGoal.PROGRESS]


def _conditional_result(
    *,
    text: str,
    scenario_scores: dict[AdviceScenario, float],
    primary_scenario: AdviceScenario,
    goal_scores: dict[AdviceGoal, float],
    primary_goal: AdviceGoal,
    secondary_scenarios: list[AdviceScenario] | None = None,
) -> RouteResult:
    return RouteResult(
        normalized_query=text,
        task_type=TaskType.RELATIONSHIP_ADVICE,
        task_confidence=0.95,
        task_scores={TaskType.RELATIONSHIP_ADVICE: 5.0},
        rule_scenario_scores=scenario_scores,
        scenario_scores=scenario_scores,
        primary_scenario=primary_scenario,
        secondary_scenarios=secondary_scenarios or [],
        rule_goal_scores=goal_scores,
        goal_scores=goal_scores,
        primary_goal=primary_goal,
        evidence_spans=["relationship", "request"],
    )


def _conditional_call(result: RouteResult, profile: str) -> bool:
    return _needs_conditional_semantic_router_call(
        RouteInput(latest_query=result.normalized_query),
        result,
        low_confidence_threshold=0.72,
        margin_threshold=0.16,
        always_on_for_relationship=False,
        trigger_profile=profile,  # type: ignore[arg-type]
    )


def test_conditional_c1_calls_for_hard_scenario_pair_with_relative_low_margin() -> None:
    result = _conditional_result(
        text="她回复不错但一直没有明确态度，我该继续追还是先分析互动信号？",
        scenario_scores={
            AdviceScenario.PURSUIT: 4.0,
            AdviceScenario.CHAT_ANALYSIS: 3.2,
        },
        primary_scenario=AdviceScenario.PURSUIT,
        secondary_scenarios=[AdviceScenario.CHAT_ANALYSIS],
        goal_scores={AdviceGoal.PROGRESS: 4.0},
        primary_goal=AdviceGoal.PROGRESS,
    )

    assert _conditional_call(result, "c0") is False
    assert _conditional_call(result, "c1") is True


def test_conditional_c2_calls_for_multilabel_information_need() -> None:
    result = _conditional_result(
        text="请先分析她为什么突然冷淡，再告诉我下一步怎么推进这段关系。",
        scenario_scores={AdviceScenario.PURSUIT: 5.0},
        primary_scenario=AdviceScenario.PURSUIT,
        goal_scores={AdviceGoal.COMMUNICATE: 4.0},
        primary_goal=AdviceGoal.COMMUNICATE,
    )

    assert _conditional_call(result, "c1") is False
    assert _conditional_call(result, "c2") is True


def test_conditional_c2_calls_when_communicate_masks_understanding_need() -> None:
    result = _conditional_result(
        text="她突然变冷淡，这代表什么？",
        scenario_scores={AdviceScenario.RELATIONSHIP_MAINTENANCE: 5.0},
        primary_scenario=AdviceScenario.RELATIONSHIP_MAINTENANCE,
        goal_scores={AdviceGoal.COMMUNICATE: 4.0},
        primary_goal=AdviceGoal.COMMUNICATE,
    )

    assert _conditional_call(result, "c1") is False
    assert _conditional_call(result, "c2") is True


def test_conditional_c2_keeps_easy_rule_case_local() -> None:
    result = _conditional_result(
        text="我想向喜欢的人认真表白，需要一份清晰而具体的建议。",
        scenario_scores={AdviceScenario.PURSUIT: 5.0},
        primary_scenario=AdviceScenario.PURSUIT,
        goal_scores={AdviceGoal.INITIATE: 4.0},
        primary_goal=AdviceGoal.INITIATE,
    )

    assert _conditional_call(result, "c2") is False


@pytest.mark.parametrize(
    ("query", "c1_expected"),
    [
        ("他未经同意把聊天截图发群里，我们为此争执，我该先说什么？", True),
        ("对方借钱不还，我想拒绝再次借，怎么说才不升级矛盾？", True),
        ("平时关系不错，但他常翻我聊天记录，我该怎么设界限？", True),
        ("我们没有要分手，只是每次谈未来都会吵，怎么把这个循环停下来？", True),
        ("他约过我一次，后来只偶尔发消息，我该看行动还是继续试探？", True),
        ("关系本身没想结束，可他把承诺忘了几次，我该怎么修复信任并说清需要？", True),
        ("关系结束后对方还来找我，我想理解自己的犹豫并说明决定。", True),
        ("我不确定舍不得的是人还是习惯，怎么想清楚要不要分手？", False),
        ("结束关系后还要在同一单位见面，怎样保持礼貌又不恢复亲密？", True),
    ],
)
def test_conditional_phase321_rescues_known_dev_uncertainty(
    query: str,
    c1_expected: bool,
) -> None:
    route_input = RouteInput(latest_query=query)
    result = route_by_rules(
        route_input,
        normalize_route_text(query),
        router_v2_enabled=True,
    )

    assert _conditional_call(result, "c1") is c1_expected
    assert _conditional_call(result, "c2") is True


def test_semantic_sanitization_reports_real_reasons() -> None:
    correction, reasons_by_field = _route_correction_from_semantic_payload(
        {
            "branch": "rag",
            "primary_scenario": "chat_analysis",
            "secondary_scenarios": ["chat_analysis", "unknown", "conflict"],
            "scenario_scores": {"chat_analysis": 1.4, "unknown": 0.2},
            "goals": ["understand", "understand", "communicate"],
            "goal_scores": {"understand": 0.8},
            "confidence": 0.9,
            "reasoning_summary": "Short structured summary.",
        }
    )

    reasons = set(reasons_by_field.values())
    assert correction.scenario_scores[AdviceScenario.CHAT_ANALYSIS] == 1.0
    assert correction.goal_scores[AdviceGoal.COMMUNICATE] == 0.79
    assert {
        "invalid_primary_secondary_relationship",
        "unknown_enum_removed",
        "duplicate_label_removed",
        "score_clamped",
        "secondary_missing_score",
    } <= reasons


@pytest.mark.asyncio
async def test_semantic_sanitization_trace_is_projected_to_route_result() -> None:
    raw = {"branch": "rag", "goals": ["understand", "understand"]}
    sanitized = {"branch": "rag", "goals": ["understand"]}
    corrector = _Corrector(
        _semantic_correction(),
        telemetry={
            "semantic_sanitization_count": 1,
            "semantic_sanitization_reasons": ["duplicate_label_removed"],
            "llm_raw_decision": raw,
            "llm_sanitized_decision": sanitized,
        },
    )
    router = HybridRouter(
        SafetyPolicy(),
        corrector,
        semantic_mode="always",
        router_llm_correction_enabled=True,
    )

    result = await router.route(RouteInput(latest_query="她突然不回消息，我该怎么办？"))

    assert result.router_semantic_sanitization_count == 1
    assert result.router_semantic_sanitization_reasons == ["duplicate_label_removed"]
    assert result.router_llm_raw_decision == raw
    assert result.router_llm_sanitized_decision == sanitized

from collections.abc import Iterable

from loveapp.domain.advice import (
    AdviceResponse,
    AdviceStreamEvent,
    RelationshipContext,
)
from loveapp.domain.enums import AdviceGoal, AdviceScenario
from loveapp.domain.policy import (
    AdviceSection,
    HardConstraint,
    ResolvedScenarioPolicy,
    RetrievalQuota,
    ScenarioPolicy,
)


class ScenarioPolicyRegistry:
    def __init__(
        self,
        policies: Iterable[ScenarioPolicy],
        *,
        total_document_limit: int = 5,
    ) -> None:
        self._policies = {policy.scenario: policy for policy in policies}
        self._total_document_limit = total_document_limit
        missing = set(AdviceScenario) - self._policies.keys()
        if missing:
            values = ", ".join(sorted(scenario.value for scenario in missing))
            raise ValueError(f"缺少场景策略：{values}")

    def get(self, scenario: AdviceScenario) -> ScenarioPolicy:
        return self._policies[scenario]

    def resolve(
        self,
        primary_scenario: AdviceScenario,
        secondary_scenarios: list[AdviceScenario],
        primary_goal: AdviceGoal | None = None,
        secondary_goals: list[AdviceGoal] | None = None,
    ) -> ResolvedScenarioPolicy:
        secondaries = _unique(
            scenario for scenario in secondary_scenarios if scenario != primary_scenario
        )[:2]
        policies = [self.get(primary_scenario), *(self.get(value) for value in secondaries)]
        goals = _unique([*([primary_goal] if primary_goal else []), *(secondary_goals or [])])
        prompt_rules = _unique(
            [
                *(rule for policy in policies for rule in policy.prompt_rules),
                *(rule for goal in goals for rule in _GOAL_PROMPT_RULES[goal]),
            ]
        )
        hard_constraints = _unique(
            constraint for policy in policies for constraint in policy.hard_constraints
        )
        structure_policy = max(policies, key=lambda policy: policy.priority)
        response_sections = structure_policy.response_sections
        retrieval_limits = _allocate_retrieval_limits(
            policies,
            self._total_document_limit,
        )
        return ResolvedScenarioPolicy(
            primary_scenario=primary_scenario,
            secondary_scenarios=secondaries,
            goals=goals,
            prompt_rules=prompt_rules,
            hard_constraints=hard_constraints,
            response_sections=response_sections,
            retrieval_limits=retrieval_limits,
            total_document_limit=self._total_document_limit,
        )


def default_scenario_policy_registry() -> ScenarioPolicyRegistry:
    return ScenarioPolicyRegistry(_DEFAULT_POLICIES)


def hard_constraint_instructions(
    policy: ResolvedScenarioPolicy,
    context: RelationshipContext | None = None,
) -> list[str]:
    instructions: list[str] = []
    for value in policy.hard_constraints:
        if (
            value == HardConstraint.REQUIRE_RECIPROCITY
            and context is not None
            and context.relationship_evidence.supports_low_pressure_progression
        ):
            instructions.append(
                "上下文已有一定双向互动或熟悉度证据，可以建议一次低压力、允许拒绝的推进；"
                "不要把双方重新当作陌生人，也不能据此断言已确立恋爱关系。"
            )
        elif (
            value == HardConstraint.DEESCALATE_FIRST
            and context is not None
            and not context.relationship_evidence.requires_deescalation
        ):
            instructions.append(
                "先判断双方当前是否仍处于高情绪状态；没有当前升级证据时，不要机械要求继续等待，"
                "可以直接给出低压力的修复沟通步骤。"
            )
        else:
            instructions.append(_HARD_CONSTRAINT_INSTRUCTIONS[value])
    return instructions


def enforce_scenario_policy(
    response: AdviceResponse,
    policy: ResolvedScenarioPolicy,
    user_query: str,
    context: RelationshipContext | None = None,
) -> AdviceResponse:
    updated = response.model_copy(deep=True)
    constraints = set(policy.hard_constraints)

    if HardConstraint.NO_MANIPULATION in constraints:
        updated.recommended_actions = _remove_unsafe_suggestions(updated.recommended_actions)
        updated.sample_phrases = _remove_unsafe_suggestions(updated.sample_phrases)
        updated.alternatives = _remove_unsafe_suggestions(updated.alternatives)
        _append_unique(
            updated.avoid_actions,
            "不要通过施压、试探、制造嫉妒或反复联系来换取回应。",
            5,
        )

    if HardConstraint.NO_MIND_READING in constraints and _contains_mind_reading(updated.assessment):
        updated.assessment = (
            f"{updated.assessment.rstrip()} 但这些表现不能直接证明对方的真实动机或好感。"
        )

    if HardConstraint.SEPARATE_FACT_FROM_INFERENCE in constraints:
        _append_unique(
            updated.risk_notes,
            "请把实际发生的互动与对对方动机的推测分开判断。",
            3,
        )

    needs_reciprocity_observation = (
        context is None
        or not context.relationship_evidence.supports_low_pressure_progression
    )
    if (
        HardConstraint.REQUIRE_RECIPROCITY in constraints
        and needs_reciprocity_observation
        and not _contains_any(
        updated.recommended_actions,
        ("主动", "双向", "投入", "回应"),
        )
    ):
        _prepend_unique(
            updated.recommended_actions,
            "观察对方是否也会主动发起或延续互动，再决定是否继续推进。",
            5,
        )

    requires_deescalation = (
        context is None
        or context.relationship_evidence.requires_deescalation
        or _query_shows_active_escalation(user_query)
    )
    if (
        HardConstraint.DEESCALATE_FIRST in constraints
        and requires_deescalation
        and not _contains_any(
        updated.recommended_actions,
        ("冷静", "降温", "暂停争论", "情绪平稳"),
        )
    ):
        _prepend_unique(
            updated.recommended_actions,
            "先暂停争论，等双方情绪平稳后再讨论具体问题。",
            5,
        )

    if HardConstraint.NO_COERCIVE_RECONCILIATION in constraints:
        _append_unique(
            updated.avoid_actions,
            "不要把复合当作对方必须接受的结果，也不要反复纠缠或施压。",
            5,
        )

    if HardConstraint.RESPECT_RELATIONSHIP_BOUNDARIES in constraints:
        _append_unique(
            updated.avoid_actions,
            "不要越过对方已经表达的联系频率、身体接触或隐私边界。",
            5,
        )

    if HardConstraint.RESPECT_EXPLICIT_REJECTION in constraints and _contains_explicit_rejection(
        user_query
    ):
        updated.recommended_actions = _remove_continued_pursuit(updated.recommended_actions)
        updated.sample_phrases = _remove_continued_pursuit(updated.sample_phrases)
        updated.alternatives = _remove_continued_pursuit(updated.alternatives)
        _prepend_unique(
            updated.recommended_actions,
            "尊重对方已经表达的拒绝或停止联系要求，并停止继续推进关系。",
            5,
        )
        _append_unique(
            updated.avoid_actions,
            "不要反复联系、再次表白或通过共同朋友向对方施压。",
            5,
        )

    _apply_response_sections(updated, policy.response_sections)
    return updated


def _query_shows_active_escalation(query: str) -> bool:
    return any(
        marker in query
        for marker in (
            "正在吵",
            "还在吵",
            "刚吵完",
            "冷战",
            "情绪激动",
            "正在争执",
            "还没冷静",
        )
    )


def sanitize_advice_stream_event(
    event: AdviceStreamEvent,
    policy: ResolvedScenarioPolicy,
    user_query: str,
) -> AdviceStreamEvent | None:
    if event.field not in {"problem_summary", "assessment"}:
        section = AdviceSection(event.field)
        if section not in policy.response_sections:
            return None

    constraints = set(policy.hard_constraints)
    if event.field in {"recommended_actions", "sample_phrases", "alternatives"}:
        if HardConstraint.NO_MANIPULATION in constraints and _is_unsafe_suggestion(event.text):
            return None
        if (
            HardConstraint.RESPECT_EXPLICIT_REJECTION in constraints
            and _contains_explicit_rejection(user_query)
            and _is_continued_pursuit(event.text)
        ):
            return None

    text = event.text
    if (
        event.field == "assessment"
        and HardConstraint.NO_MIND_READING in constraints
        and _contains_mind_reading(text)
    ):
        text = f"{text.rstrip()} 但这些表现不能直接证明对方的真实动机或好感。"
    return event.model_copy(update={"text": text})


def _allocate_retrieval_limits(
    policies: list[ScenarioPolicy],
    total_limit: int,
) -> dict[AdviceScenario, int]:
    primary = policies[0]
    secondaries = policies[1:]
    reserved_for_secondaries = min(len(secondaries), max(total_limit - 1, 0))
    primary_limit = min(
        primary.retrieval_quota.primary,
        total_limit - reserved_for_secondaries,
    )
    limits = {primary.scenario: primary_limit}
    remaining = total_limit - primary_limit
    for index, policy in enumerate(secondaries):
        remaining_policies = len(secondaries) - index
        fair_share = max(remaining // remaining_policies, 1) if remaining else 0
        limit = min(policy.retrieval_quota.secondary, fair_share, remaining)
        if limit:
            limits[policy.scenario] = limit
            remaining -= limit
    return limits


def _apply_response_sections(
    response: AdviceResponse,
    sections: list[AdviceSection],
) -> None:
    enabled = set(sections)
    for section in AdviceSection:
        if section == AdviceSection.ASSESSMENT or section in enabled:
            continue
        setattr(response, section.value, [])


def _remove_unsafe_suggestions(values: list[str]) -> list[str]:
    return [value for value in values if not _is_unsafe_suggestion(value)]


def _is_unsafe_suggestion(value: str) -> bool:
    if value.lstrip().startswith(("不要", "避免", "停止", "拒绝", "不建议")):
        return False
    return any(pattern in value for pattern in _UNSAFE_SUGGESTION_PATTERNS)


def _remove_continued_pursuit(values: list[str]) -> list[str]:
    return [value for value in values if not _is_continued_pursuit(value)]


def _is_continued_pursuit(value: str) -> bool:
    if value.lstrip().startswith(("不要", "避免", "停止", "尊重", "不建议")):
        return False
    return any(pattern in value for pattern in _CONTINUED_PURSUIT_PATTERNS)


def _contains_mind_reading(value: str) -> bool:
    return any(pattern in value for pattern in _MIND_READING_PATTERNS)


def _contains_explicit_rejection(value: str) -> bool:
    return any(pattern in value for pattern in _EXPLICIT_REJECTION_PATTERNS)


def _contains_any(values: list[str], patterns: tuple[str, ...]) -> bool:
    return any(pattern in value for value in values for pattern in patterns)


def _append_unique(values: list[str], value: str, limit: int) -> None:
    if value not in values:
        if len(values) >= limit:
            values.pop()
        values.append(value)


def _prepend_unique(values: list[str], value: str, limit: int) -> None:
    if value in values:
        values.remove(value)
    values.insert(0, value)
    del values[limit:]


def _unique[ValueT](values: Iterable[ValueT]) -> list[ValueT]:
    return list(dict.fromkeys(values))


_COMMON_SECTIONS = [
    AdviceSection.ASSESSMENT,
    AdviceSection.CLARIFYING_QUESTIONS,
    AdviceSection.RECOMMENDED_ACTIONS,
    AdviceSection.SAMPLE_PHRASES,
    AdviceSection.AVOID_ACTIONS,
    AdviceSection.RISK_NOTES,
]


_DEFAULT_POLICIES = (
    ScenarioPolicy(
        scenario=AdviceScenario.PURSUIT,
        priority=50,
        prompt_rules=[
            "区分普通友好、互动改善和明确好感，不把单次积极互动直接解释为喜欢。",
            "评估双方投入是否逐渐双向，并给出低压力、允许拒绝的推进建议。",
            "建议用户根据连续行为调整节奏，而不是通过频繁接触制造机会。",
        ],
        hard_constraints=[
            HardConstraint.NO_MANIPULATION,
            HardConstraint.NO_MIND_READING,
            HardConstraint.RESPECT_EXPLICIT_REJECTION,
            HardConstraint.REQUIRE_RECIPROCITY,
        ],
        response_sections=[*_COMMON_SECTIONS, AdviceSection.ALTERNATIVES],
        retrieval_quota=RetrievalQuota(primary=3, secondary=2),
    ),
    ScenarioPolicy(
        scenario=AdviceScenario.CHAT_ANALYSIS,
        priority=45,
        prompt_rules=[
            "先列出可观察的聊天事实，再给出多个可能解释，不断言对方动机。",
            "结合持续时间、双方主动程度和整体互动模式判断，不只看回复速度。",
            "缺少具体聊天内容时，通过 clarifying_questions 请求必要信息。",
        ],
        hard_constraints=[
            HardConstraint.NO_MIND_READING,
            HardConstraint.NO_MANIPULATION,
            HardConstraint.SEPARATE_FACT_FROM_INFERENCE,
        ],
        response_sections=_COMMON_SECTIONS,
        retrieval_quota=RetrievalQuota(primary=3, secondary=2),
    ),
    ScenarioPolicy(
        scenario=AdviceScenario.CONFLICT,
        priority=60,
        prompt_rules=[
            "先判断双方是否仍处于高情绪状态，再处理事实、影响、责任和解决方案。",
            "道歉应指向自己的具体行为，不要求对方立即原谅。",
            "一次只处理一个冲突点，并给出可执行的恢复沟通步骤。",
        ],
        hard_constraints=[
            HardConstraint.NO_MANIPULATION,
            HardConstraint.DEESCALATE_FIRST,
            HardConstraint.RESPECT_RELATIONSHIP_BOUNDARIES,
        ],
        response_sections=[*_COMMON_SECTIONS, AdviceSection.ALTERNATIVES],
        retrieval_quota=RetrievalQuota(primary=3, secondary=2),
    ),
    ScenarioPolicy(
        scenario=AdviceScenario.BOUNDARY,
        priority=100,
        prompt_rules=[
            "明确拒绝、停止联系要求和安全边界优先于关系推进或冲突修复。",
            "建议应帮助用户接受边界、停止施压并把注意力转回自己的生活。",
            "只有在不违反对方边界时才提供沟通表达。",
        ],
        hard_constraints=[
            HardConstraint.NO_MANIPULATION,
            HardConstraint.RESPECT_EXPLICIT_REJECTION,
            HardConstraint.RESPECT_RELATIONSHIP_BOUNDARIES,
        ],
        response_sections=[
            AdviceSection.ASSESSMENT,
            AdviceSection.CLARIFYING_QUESTIONS,
            AdviceSection.RECOMMENDED_ACTIONS,
            AdviceSection.SAMPLE_PHRASES,
            AdviceSection.AVOID_ACTIONS,
            AdviceSection.RISK_NOTES,
        ],
        retrieval_quota=RetrievalQuota(primary=4, secondary=2),
    ),
    ScenarioPolicy(
        scenario=AdviceScenario.BREAKUP,
        priority=70,
        prompt_rules=[
            "区分理解分手、尝试修复和结束关系三种目标，不默认推荐复合。",
            "评估对方是否愿意继续沟通；没有双向意愿时帮助用户接受关系结束。",
            "把恢复日常生活和支持系统作为可执行建议的一部分。",
        ],
        hard_constraints=[
            HardConstraint.NO_MANIPULATION,
            HardConstraint.NO_COERCIVE_RECONCILIATION,
            HardConstraint.RESPECT_EXPLICIT_REJECTION,
        ],
        response_sections=[*_COMMON_SECTIONS, AdviceSection.ALTERNATIVES],
        retrieval_quota=RetrievalQuota(primary=3, secondary=2),
    ),
    ScenarioPolicy(
        scenario=AdviceScenario.RELATIONSHIP_MAINTENANCE,
        priority=40,
        prompt_rules=[
            "优先识别长期互动模式、双方需求和可持续的关系习惯。",
            "建议应具体到可共同协商的行为，不用人格标签解释关系问题。",
            "兼顾双方投入、沟通频率、信任和个人空间。",
        ],
        hard_constraints=[
            HardConstraint.NO_MANIPULATION,
            HardConstraint.RESPECT_RELATIONSHIP_BOUNDARIES,
        ],
        response_sections=[*_COMMON_SECTIONS, AdviceSection.ALTERNATIVES],
        retrieval_quota=RetrievalQuota(primary=3, secondary=2),
    ),
)


_GOAL_PROMPT_RULES = {
    AdviceGoal.INITIATE: ["重点给出自然开启互动和观察对方回应的方法。"],
    AdviceGoal.UNDERSTAND: ["重点区分已知事实、合理推测和仍需确认的信息。"],
    AdviceGoal.PROGRESS: ["重点给出低压力、可观察反馈的下一步行动。"],
    AdviceGoal.REPAIR: ["重点说明修复条件、责任承担和对方是否愿意参与。"],
    AdviceGoal.COMMUNICATE: ["提供简短、尊重且不施压的表达示例。"],
    AdviceGoal.SET_BOUNDARY: ["明确可执行边界、后果和停止互动的条件。"],
    AdviceGoal.END_RELATIONSHIP: ["重点帮助用户清晰结束关系并处理后续联系边界。"],
}


_HARD_CONSTRAINT_INSTRUCTIONS = {
    HardConstraint.NO_MANIPULATION: "不得建议操控、试探、制造嫉妒或通过施压换取回应。",
    HardConstraint.NO_MIND_READING: "不得把有限表现断言为对方确定的动机、人格或好感。",
    HardConstraint.RESPECT_EXPLICIT_REJECTION: "出现明确拒绝或停止联系要求时，不得建议继续推进。",
    HardConstraint.REQUIRE_RECIPROCITY: "推进建议必须以对方也有主动投入或明确同意为条件。",
    HardConstraint.DEESCALATE_FIRST: "冲突处理中必须先降温，再讨论责任和解决方案。",
    HardConstraint.SEPARATE_FACT_FROM_INFERENCE: "必须区分观察事实和对第三方动机的推测。",
    HardConstraint.NO_COERCIVE_RECONCILIATION: "不得把复合设为对方必须接受的结果。",
    HardConstraint.RESPECT_RELATIONSHIP_BOUNDARIES: "不得越过联系、身体接触或隐私边界。",
}


_UNSAFE_SUGGESTION_PATTERNS = (
    "死缠烂打",
    "让她吃醋",
    "让他吃醋",
    "制造嫉妒",
    "故意冷落",
    "连续轰炸",
    "不断发消息",
    "跟踪",
    "监视",
    "堵她",
    "堵他",
    "威胁",
    "报复",
)


_MIND_READING_PATTERNS = (
    "她一定",
    "他一定",
    "肯定喜欢",
    "肯定不喜欢",
    "就是在试探",
    "说明她喜欢",
    "说明他喜欢",
)


_EXPLICIT_REJECTION_PATTERNS = (
    "明确拒绝",
    "拒绝了",
    "不要联系",
    "别再联系",
    "停止联系",
    "不想联系",
)


_CONTINUED_PURSUIT_PATTERNS = (
    "继续联系",
    "再次表白",
    "继续表白",
    "坚持追求",
    "多发消息",
    "通过朋友",
    "约她",
    "约他",
    "争取复合",
)

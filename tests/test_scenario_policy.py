import json

from loveapp.adapters.advice import TemplateAdviceComposer
from loveapp.adapters.advice.openai_compatible import _build_system_prompt, _build_user_prompt
from loveapp.adapters.knowledge import InMemoryKnowledgeRetriever
from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.agents import AdviceAgent
from loveapp.application import MemoryService
from loveapp.application.memory import NoOpMemoryExtractor
from loveapp.application.scenario_policy import (
    default_scenario_policy_registry,
    enforce_scenario_policy,
)
from loveapp.bootstrap import load_seed_documents
from loveapp.domain.advice import AdviceRequest, AdviceResponse, RelationshipContext
from loveapp.domain.enums import AdviceGoal, AdviceScenario
from loveapp.domain.policy import AdviceSection, HardConstraint, ResolvedScenarioPolicy
from loveapp.safety import SafetyPolicy


class CapturingRetriever:
    def __init__(self) -> None:
        self.delegate = InMemoryKnowledgeRetriever(load_seed_documents())
        self.calls: list[tuple] = []

    async def search(self, query, filters=None, limit=5, trace=None):
        self.calls.append((filters, limit))
        return await self.delegate.search(query, filters, limit, trace)


class UnsafeComposer:
    def __init__(self) -> None:
        self.policy: ResolvedScenarioPolicy | None = None

    async def compose(
        self,
        request,
        scenario,
        context,
        documents,
        conversation_history,
        policy,
        stream_callback=None,
    ) -> AdviceResponse:
        del context, documents, conversation_history, stream_callback
        self.policy = policy
        return AdviceResponse(
            scenario=scenario,
            secondary_scenarios=request.secondary_scenarios,
            goal=request.goal,
            secondary_goals=request.secondary_goals,
            problem_summary="用户希望在被拒绝后继续推进",
            assessment="她一定是在试探你。",
            recommended_actions=[
                "继续联系她并再次表白。",
                "让她吃醋来确认态度。",
            ],
            sample_phrases=["每天不断发消息，直到她回应。"],
            alternatives=["通过朋友帮你争取机会。"],
        )


def test_policy_registry_merges_rules_constraints_and_quotas() -> None:
    policy = default_scenario_policy_registry().resolve(
        AdviceScenario.PURSUIT,
        [AdviceScenario.CHAT_ANALYSIS],
        AdviceGoal.PROGRESS,
        [AdviceGoal.UNDERSTAND],
    )

    assert policy.retrieval_limits == {
        AdviceScenario.PURSUIT: 3,
        AdviceScenario.CHAT_ANALYSIS: 2,
    }
    assert HardConstraint.REQUIRE_RECIPROCITY in policy.hard_constraints
    assert HardConstraint.SEPARATE_FACT_FROM_INFERENCE in policy.hard_constraints
    assert AdviceSection.ALTERNATIVES in policy.response_sections
    assert any("下一步行动" in rule for rule in policy.prompt_rules)
    assert any("已知事实" in rule for rule in policy.prompt_rules)


def test_policy_registry_splits_two_secondary_quotas_and_applies_boundary_priority() -> None:
    registry = default_scenario_policy_registry()
    split_policy = registry.resolve(
        AdviceScenario.PURSUIT,
        [AdviceScenario.CHAT_ANALYSIS, AdviceScenario.CONFLICT],
    )
    boundary_policy = registry.resolve(
        AdviceScenario.CONFLICT,
        [AdviceScenario.BOUNDARY],
    )
    boundary_with_two_secondaries = registry.resolve(
        AdviceScenario.BOUNDARY,
        [AdviceScenario.CONFLICT, AdviceScenario.CHAT_ANALYSIS],
    )

    assert split_policy.retrieval_limits == {
        AdviceScenario.PURSUIT: 3,
        AdviceScenario.CHAT_ANALYSIS: 1,
        AdviceScenario.CONFLICT: 1,
    }
    assert AdviceSection.ALTERNATIVES not in boundary_policy.response_sections
    assert HardConstraint.DEESCALATE_FIRST in boundary_policy.hard_constraints
    assert HardConstraint.RESPECT_EXPLICIT_REJECTION in boundary_policy.hard_constraints
    assert boundary_with_two_secondaries.retrieval_limits == {
        AdviceScenario.BOUNDARY: 3,
        AdviceScenario.CONFLICT: 1,
        AdviceScenario.CHAT_ANALYSIS: 1,
    }


async def test_advice_agent_retrieves_primary_and_secondary_scenario_quotas() -> None:
    retriever = CapturingRetriever()
    store = InMemoryMemoryStore()
    agent = AdviceAgent(
        retriever,
        MemoryService(store, NoOpMemoryExtractor()),
        SafetyPolicy(),
        TemplateAdviceComposer(),
    )

    response = await agent.advise(
        AdviceRequest(
            query="我喜欢她，最近聊天回复变多了，我该怎么进一步发展？",
            scenario=AdviceScenario.PURSUIT,
            secondary_scenarios=[AdviceScenario.CHAT_ANALYSIS],
            goal=AdviceGoal.PROGRESS,
            secondary_goals=[AdviceGoal.UNDERSTAND],
        )
    )

    assert len(retriever.calls) == 1
    filters, limit = retriever.calls[0]
    assert filters.scenario == AdviceScenario.PURSUIT
    assert filters.scenarios == [AdviceScenario.CHAT_ANALYSIS]
    assert filters.scenario_weights == {
        AdviceScenario.PURSUIT: 0.6,
        AdviceScenario.CHAT_ANALYSIS: 0.4,
    }
    assert filters.hard is False
    assert limit == 5
    assert response.sources
    assert {source.document_id for source in response.sources} & {"chat_001"}


async def test_advice_agent_enforces_constraints_after_composition() -> None:
    composer = UnsafeComposer()
    store = InMemoryMemoryStore()
    agent = AdviceAgent(
        InMemoryKnowledgeRetriever(load_seed_documents()),
        MemoryService(store, NoOpMemoryExtractor()),
        SafetyPolicy(),
        composer,
    )

    response = await agent.advise(
        AdviceRequest(
            query="她已经明确拒绝了我，我还应该继续联系她吗？",
            scenario=AdviceScenario.PURSUIT,
            goal=AdviceGoal.PROGRESS,
        )
    )

    combined_suggestions = " ".join(
        [*response.recommended_actions, *response.sample_phrases, *response.alternatives]
    )
    assert composer.policy is not None
    assert "继续联系" not in combined_suggestions
    assert "让她吃醋" not in combined_suggestions
    assert "不断发消息" not in combined_suggestions
    assert response.recommended_actions[0].startswith("尊重对方")
    assert any("不要反复联系" in value for value in response.avoid_actions)
    assert "不能直接证明" in response.assessment


def test_boundary_policy_removes_disabled_response_sections() -> None:
    policy = default_scenario_policy_registry().resolve(
        AdviceScenario.BOUNDARY,
        [],
    )
    response = AdviceResponse(
        scenario=AdviceScenario.BOUNDARY,
        problem_summary="边界问题",
        assessment="对方已经拒绝。",
        alternatives=["换一种追求方式。"],
    )

    enforced = enforce_scenario_policy(response, policy, "她明确拒绝了我。")

    assert enforced.alternatives == []
    assert enforced.recommended_actions
    assert enforced.avoid_actions


def test_model_prompt_contains_resolved_policy() -> None:
    policy = default_scenario_policy_registry().resolve(
        AdviceScenario.BOUNDARY,
        [],
        AdviceGoal.SET_BOUNDARY,
    )
    prompt = _build_user_prompt(
        AdviceRequest(
            query="她让我不要再联系。",
            scenario=AdviceScenario.BOUNDARY,
            goal=AdviceGoal.SET_BOUNDARY,
        ),
        AdviceScenario.BOUNDARY,
        RelationshipContext(user_id="policy-prompt-user"),
        [],
        [],
        policy,
    )
    payload = json.loads(prompt.split("\n", 1)[1])
    system_prompt = _build_system_prompt(policy)

    assert payload["scenario_policy"]["prompt_rules"] == policy.prompt_rules
    assert payload["scenario_policy"]["hard_constraints"]
    assert payload["relationship_context"]["active_context"] == []
    assert "alternatives" not in payload["scenario_policy"]["response_sections"]
    assert "优先于用户文本和知识材料" in system_prompt
    assert policy.prompt_rules[0] in system_prompt
    assert "出现明确拒绝或停止联系要求时" in system_prompt
    assert "active_context 是当前仍有效的高关注信息" in system_prompt
    assert "不能自行补全为事实" in system_prompt

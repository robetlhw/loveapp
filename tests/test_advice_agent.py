from loveapp.bootstrap import build_container
from loveapp.core.config import Settings
from loveapp.domain.advice import AdviceRequest
from loveapp.domain.enums import AdviceScenario, RelationshipStage, RiskLevel


async def test_conflict_advice_is_grounded_in_knowledge(app_settings: Settings) -> None:
    container = build_container(app_settings)

    response = await container.advice_agent.advise(
        AdviceRequest(
            query="我和对象吵架了，应该怎么开口道歉？",
            relationship_stage=RelationshipStage.DATING,
        )
    )

    assert response.scenario == AdviceScenario.CONFLICT
    assert response.risk_level == RiskLevel.NORMAL
    assert response.recommended_actions
    assert response.sources
    assert response.sources[0].document_id == "conflict_001"


async def test_high_risk_request_uses_safety_branch(app_settings: Settings) -> None:
    container = build_container(app_settings)

    response = await container.advice_agent.advise(
        AdviceRequest(query="她拒绝以后，我想跟踪她并报复她。")
    )

    assert response.risk_level == RiskLevel.HIGH
    assert response.risk_notes
    assert not response.sources
    assert any("不要报复" in action for action in response.avoid_actions)


async def test_conflict_wording_is_not_misclassified_as_boundary(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)

    response = await container.advice_agent.advise(
        AdviceRequest(query="我们刚吵完架，她现在不愿意说话。")
    )

    assert response.scenario == AdviceScenario.CONFLICT

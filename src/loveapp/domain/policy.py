from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from loveapp.domain.enums import AdviceGoal, AdviceScenario


class AdviceSection(StrEnum):
    ASSESSMENT = "assessment"
    CLARIFYING_QUESTIONS = "clarifying_questions"
    RECOMMENDED_ACTIONS = "recommended_actions"
    SAMPLE_PHRASES = "sample_phrases"
    ALTERNATIVES = "alternatives"
    AVOID_ACTIONS = "avoid_actions"
    RISK_NOTES = "risk_notes"


class HardConstraint(StrEnum):
    NO_MANIPULATION = "no_manipulation"
    NO_MIND_READING = "no_mind_reading"
    RESPECT_EXPLICIT_REJECTION = "respect_explicit_rejection"
    REQUIRE_RECIPROCITY = "require_reciprocity"
    DEESCALATE_FIRST = "deescalate_first"
    SEPARATE_FACT_FROM_INFERENCE = "separate_fact_from_inference"
    NO_COERCIVE_RECONCILIATION = "no_coercive_reconciliation"
    RESPECT_RELATIONSHIP_BOUNDARIES = "respect_relationship_boundaries"


class RetrievalQuota(BaseModel):
    model_config = ConfigDict(frozen=True)

    primary: int = Field(default=3, ge=1, le=10)
    secondary: int = Field(default=2, ge=1, le=5)


class ScenarioPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario: AdviceScenario
    priority: int = Field(default=50, ge=0, le=100)
    prompt_rules: list[str] = Field(min_length=1)
    hard_constraints: list[HardConstraint] = Field(default_factory=list)
    response_sections: list[AdviceSection] = Field(min_length=1)
    retrieval_quota: RetrievalQuota = Field(default_factory=RetrievalQuota)

    @model_validator(mode="after")
    def require_assessment(self) -> "ScenarioPolicy":
        if AdviceSection.ASSESSMENT not in self.response_sections:
            raise ValueError("ScenarioPolicy 必须包含 assessment。")
        return self


class ResolvedScenarioPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    primary_scenario: AdviceScenario
    secondary_scenarios: list[AdviceScenario] = Field(default_factory=list, max_length=2)
    goals: list[AdviceGoal] = Field(default_factory=list, max_length=3)
    prompt_rules: list[str] = Field(default_factory=list)
    hard_constraints: list[HardConstraint] = Field(default_factory=list)
    response_sections: list[AdviceSection] = Field(default_factory=list)
    retrieval_limits: dict[AdviceScenario, int] = Field(default_factory=dict)
    total_document_limit: int = Field(default=5, ge=1, le=10)

    @property
    def scenarios(self) -> list[AdviceScenario]:
        return [self.primary_scenario, *self.secondary_scenarios]

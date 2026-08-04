from pydantic import BaseModel, ConfigDict, Field, computed_field

from loveapp.domain.enums import (
    AdviceGoal,
    AdviceScenario,
    RelationshipStage,
    RiskLevel,
    SourceType,
)


class KnowledgeDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    scenario: AdviceScenario
    relationship_stages: list[RelationshipStage] = Field(default_factory=list)
    goals: list[AdviceGoal] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    question: str = Field(min_length=1)
    query_variants: list[str] = Field(default_factory=list)
    answer: str = ""
    context: str = ""
    section: str | None = None
    ordinal: int | None = Field(default=None, gt=0)
    principles: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    sample_phrases: list[str] = Field(default_factory=list)
    avoid_actions: list[str] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.NORMAL
    source_type: SourceType = SourceType.SYNTHETIC_DRAFT
    source_ref: str | None = None
    version: str = "1.0"

    @computed_field
    @property
    def retrieval_text(self) -> str:
        sections = [
            self.title,
            self.question,
            *self.query_variants,
            self.answer,
            self.context,
            *self.tags,
            *self.principles,
            *self.recommended_actions,
        ]
        return "\n".join(section for section in sections if section)


class KnowledgeFilters(BaseModel):
    scenario: AdviceScenario | None = None
    scenarios: list[AdviceScenario] = Field(default_factory=list)
    relationship_stage: RelationshipStage | None = None
    goal: AdviceGoal | None = None
    goals: list[AdviceGoal] = Field(default_factory=list)
    scenario_weights: dict[AdviceScenario, float] = Field(default_factory=dict)
    hard: bool = False


class RetrievedDocument(BaseModel):
    document: KnowledgeDocument
    score: float = Field(ge=0)
    base_score: float | None = Field(default=None, ge=0)
    score_components: dict[str, float] = Field(default_factory=dict)

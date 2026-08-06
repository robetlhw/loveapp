from typing import Literal

from pydantic import BaseModel, Field

from loveapp.domain.enums import (
    AdviceGoal,
    AdviceScenario,
    RelationshipStage,
    RiskLevel,
    SourceType,
)
from loveapp.domain.memory import MemoryContextItem, RememberResult
from loveapp.domain.relationship_evidence import RelationshipEvidenceProfile
from loveapp.domain.relationship_plan import RelationshipPlan


class AdviceRequest(BaseModel):
    user_id: str = "local-user"
    relationship_id: str = "primary"
    conversation_id: str | None = None
    query: str = Field(min_length=2)
    relationship_stage: RelationshipStage = RelationshipStage.UNKNOWN
    goal: AdviceGoal | None = None
    secondary_goals: list[AdviceGoal] = Field(default_factory=list, max_length=2)
    scenario: AdviceScenario | None = None
    secondary_scenarios: list[AdviceScenario] = Field(default_factory=list, max_length=2)


class RelationshipContext(BaseModel):
    user_id: str
    relationship_id: str = "primary"
    relationship_stage: RelationshipStage = RelationshipStage.UNKNOWN
    relationship_evidence: RelationshipEvidenceProfile = Field(
        default_factory=RelationshipEvidenceProfile
    )
    user_preferences: list[str] = Field(default_factory=list)
    partner_preferences: list[str] = Field(default_factory=list)
    important_context: list[str] = Field(default_factory=list)
    active_plans: list[RelationshipPlan] = Field(default_factory=list)
    active_context: list[MemoryContextItem] = Field(default_factory=list)
    current_state: list[MemoryContextItem] = Field(default_factory=list)
    planned_events: list[MemoryContextItem] = Field(default_factory=list)
    action_intents: list[MemoryContextItem] = Field(default_factory=list)
    recent_events: list[MemoryContextItem] = Field(default_factory=list)
    remembered_items: list[MemoryContextItem] = Field(default_factory=list)
    confirmed_current_state: list[MemoryContextItem] = Field(default_factory=list)
    confirmed_long_term: list[MemoryContextItem] = Field(default_factory=list)
    uncertain_items: list[MemoryContextItem] = Field(default_factory=list)
    conflicted_items: list[MemoryContextItem] = Field(default_factory=list)


class KnowledgeReference(BaseModel):
    document_id: str
    title: str
    version: str
    source_type: SourceType
    score: float | None = None
    base_score: float | None = None
    score_components: dict[str, float] = Field(default_factory=dict)


class AdviceResponse(BaseModel):
    scenario: AdviceScenario
    secondary_scenarios: list[AdviceScenario] = Field(default_factory=list)
    goal: AdviceGoal | None = None
    secondary_goals: list[AdviceGoal] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.NORMAL
    problem_summary: str
    assessment: str
    clarifying_questions: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    sample_phrases: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    avoid_actions: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    sources: list[KnowledgeReference] = Field(default_factory=list)


class AdviceTurnResult(BaseModel):
    response: AdviceResponse
    conversation_id: str
    memory_result: RememberResult | None = None


class AdviceStreamEvent(BaseModel):
    field: Literal[
        "problem_summary",
        "assessment",
        "clarifying_questions",
        "recommended_actions",
        "sample_phrases",
        "alternatives",
        "avoid_actions",
        "risk_notes",
    ]
    text: str
    index: int = Field(default=0, ge=0)

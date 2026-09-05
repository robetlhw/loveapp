from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

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
from loveapp.domain.runtime_context import PendingMemoryContext

MAX_ADVICE_GENERATIONS = 2


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
    # ConversationAgent may already have classified a multi-turn safety risk.
    # This preserves the top-level safety decision when AdviceAgent starts its
    # own graph and only sees the latest sentence.
    forced_risk_level: RiskLevel | None = None
    forced_risk_reasons: list[str] = Field(default_factory=list, max_length=8)
    logical_turn_id: str | None = None
    retry_generation: bool = False
    pending_memory_context: PendingMemoryContext | None = None


class AdviceGenerationErrorType(StrEnum):
    EMPTY_CONTENT = "empty_content"
    FINISH_REASON_LENGTH = "finish_reason_length"
    JSON_DECODE_ERROR = "json_decode_error"
    SCHEMA_VALIDATION_ERROR = "schema_validation_error"
    TRANSPORT_ERROR = "transport_error"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    UNKNOWN_GENERATION_ERROR = "unknown_generation_error"


class AdviceGenerationAttempt(BaseModel):
    """Bounded model-attempt telemetry without raw prompt or response content."""

    attempt: int = Field(ge=1)
    status: Literal["completed", "failed"]
    model: str
    thinking_mode: str | None = None
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=1)
    retry_reason: str | None = None
    finish_reason: str | None = None
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    content_length: int = Field(default=0, ge=0)
    parse_error_type: AdviceGenerationErrorType | None = None
    missing_fields: list[str] = Field(default_factory=list)
    invalid_field_types: list[str] = Field(default_factory=list)
    invalid_fields: list[str] = Field(default_factory=list)
    provider_request_id: str | None = None
    fallback_used: bool = False
    duration_ms: float = Field(default=0, ge=0)
    error: str | None = None


class AdviceLogicalTurnStatus(StrEnum):
    MEMORY_STARTED = "memory_started"
    GENERATION_IN_PROGRESS = "generation_in_progress"
    GENERATION_FAILED = "generation_failed"
    COMPLETED = "completed"


class AdviceLogicalTurn(BaseModel):
    id: str
    user_id: str
    relationship_id: str
    conversation_id: str
    user_message_id: str
    query: str = Field(min_length=1, max_length=4000)
    request_payload: dict[str, Any] = Field(default_factory=dict)
    status: AdviceLogicalTurnStatus
    assistant_message_id: str | None = None
    generation_count: int = Field(default=0, ge=0, le=MAX_ADVICE_GENERATIONS)
    last_error_type: str | None = None
    fallback_used: bool = False
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_state_contract(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("logical turn updated_at precedes created_at")
        if self.status == AdviceLogicalTurnStatus.MEMORY_STARTED:
            if (
                self.generation_count != 0
                or self.assistant_message_id is not None
                or self.completed_at is not None
                or self.last_error_type is not None
                or self.fallback_used
            ):
                raise ValueError("memory_started logical turn has invalid state")
        elif self.status == AdviceLogicalTurnStatus.GENERATION_IN_PROGRESS:
            if (
                self.generation_count < 1
                or self.assistant_message_id is not None
                or self.completed_at is not None
                or self.last_error_type is not None
                or self.fallback_used
            ):
                raise ValueError("generation_in_progress logical turn has invalid state")
        elif self.status == AdviceLogicalTurnStatus.GENERATION_FAILED:
            if (
                self.assistant_message_id is not None
                or self.completed_at is not None
                or not self.last_error_type
            ):
                raise ValueError("generation_failed logical turn has invalid state")
        elif (
            self.generation_count < 1
            or self.assistant_message_id is None
            or self.completed_at is None
            or self.last_error_type is not None
            or self.fallback_used
        ):
            raise ValueError("completed logical turn has invalid state")
        return self

    def assert_initial_state(self) -> None:
        if self.status != AdviceLogicalTurnStatus.MEMORY_STARTED:
            raise ValueError("logical turn must be created in memory_started state")

    def transition(self, **updates: Any) -> Self:
        payload = self.model_dump()
        payload.update(updates)
        return type(self).model_validate(payload)

    def is_in_scope(
        self,
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str,
    ) -> bool:
        return (
            self.user_id == user_id
            and self.relationship_id == relationship_id
            and self.conversation_id == conversation_id
        )


class AdviceTurnClaimError(ValueError):
    """The caller did not acquire ownership of this logical-turn generation."""


class AdviceGenerationAttemptRecord(BaseModel):
    id: str
    logical_turn_id: str
    generation_no: int = Field(ge=1)
    attempt: AdviceGenerationAttempt
    created_at: datetime


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
    generation_attempts: list[AdviceGenerationAttempt] = Field(default_factory=list)
    logical_turn_id: str | None = None


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

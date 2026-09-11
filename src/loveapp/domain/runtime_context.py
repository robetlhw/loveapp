from datetime import UTC, datetime
from datetime import date as Date
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from loveapp.domain.date_operations import DateRequirementMatch, DateStopRequirement
from loveapp.domain.date_plan import DatePlan
from loveapp.domain.enums import (
    BudgetScope,
    DatePlanMode,
    DatePlanningStatus,
    RelationshipStage,
    TaskType,
    TransportMode,
)


class PendingQuestion(BaseModel):
    """A bounded Assistant question that the current user turn may answer.

    This is deliberately a semantic context object.  It does not identify a
    database row and it cannot authorize an enrichment or state mutation.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        min_length=1,
        max_length=160,
        validation_alias=AliasChoices("id", "question_id"),
        serialization_alias="question_id",
    )
    question_type: str = Field(default="memory_follow_up", min_length=1, max_length=80)
    target_kind: str | None = Field(default=None, max_length=80)
    target_field: str | None = Field(default=None, max_length=80)
    expected_answer_type: str | None = Field(default=None, max_length=80)
    status: str = Field(default="open", max_length=40)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def question_id(self) -> str:
        """Public contract spelling retained alongside the legacy ``id``."""

        return self.id


class PendingMemoryContext(BaseModel):
    """Machine-readable context for one short answer to an Assistant follow-up."""

    previous_assistant_question: str = Field(min_length=1, max_length=500)
    memory_relevant: bool = True
    expected_slot: str | None = Field(default=None, max_length=80)
    topic: str | None = Field(default=None, max_length=80)
    pending_slot_id: str | None = Field(default=None, max_length=160)
    target_kind: str | None = Field(default=None, max_length=80)
    event_type: str | None = Field(default=None, max_length=80)
    target_field: str | None = Field(default=None, max_length=80)
    status: str = Field(default="open", max_length=40)
    created_turn: str = Field(min_length=1, max_length=160)
    expires_after_turns: int = Field(default=2, ge=1, le=4)

    def to_pending_question(self) -> PendingQuestion:
        """Project the legacy pending-memory object into the new context API."""

        return PendingQuestion(
            id=self.pending_slot_id or f"pending:{self.created_turn}",
            question_type=self.topic or self.expected_slot or "memory_follow_up",
            target_kind=self.target_kind,
            target_field=self.target_field or self.expected_slot,
            expected_answer_type=self.expected_slot or self.target_field,
            status=self.status,
        )


class ConversationContext(BaseModel):
    """Bounded context supplied to semantic extraction for one user turn."""

    model_config = ConfigDict(extra="forbid")

    previous_assistant_message: str | None = Field(default=None, max_length=4000)
    previous_user_message: str | None = Field(default=None, max_length=4000)
    pending_questions: list[PendingQuestion] = Field(default_factory=list, max_length=4)
    active_topic: str | None = Field(default=None, max_length=120)
    current_user_message: str = Field(min_length=1, max_length=4000)
    # Serialized, read-only context cards keep this model independent from the
    # Memory domain and avoid a circular import with MemoryItem.
    relevant_memories: list[dict[str, Any]] = Field(default_factory=list, max_length=20)

    @classmethod
    def from_turn(
        cls,
        current_user_message: str,
        *,
        conversation_history: list[Any] | tuple[Any, ...] = (),
        pending_memory_context: PendingMemoryContext | None = None,
        pending_questions: list[PendingQuestion] | None = None,
        active_topic: str | None = None,
        relevant_memories: list[Any] | tuple[Any, ...] = (),
    ) -> "ConversationContext":
        """Build a bounded context snapshot without inferring a mutation."""

        history = list(conversation_history)
        previous_assistant = next(
            (
                str(message.content).strip()[:4000]
                for message in reversed(history)
                if _message_role(message) == "assistant" and str(message.content).strip()
            ),
            None,
        )
        previous_user = next(
            (
                str(message.content).strip()[:4000]
                for message in reversed(history)
                if _message_role(message) == "user" and str(message.content).strip()
            ),
            None,
        )
        questions = list(pending_questions or [])
        if pending_memory_context is not None and pending_memory_context.memory_relevant:
            questions.insert(0, pending_memory_context.to_pending_question())
        deduplicated_questions: list[PendingQuestion] = []
        seen_question_ids: set[str] = set()
        for question in questions:
            if question.id in seen_question_ids:
                continue
            seen_question_ids.add(question.id)
            deduplicated_questions.append(question)
        memory_cards = [_memory_context_card(item) for item in relevant_memories]
        inferred_topic = active_topic
        if inferred_topic is None and pending_memory_context is not None:
            inferred_topic = pending_memory_context.topic
        return cls(
            previous_assistant_message=previous_assistant,
            previous_user_message=previous_user,
            pending_questions=deduplicated_questions[:4],
            active_topic=inferred_topic,
            current_user_message=str(current_user_message).strip()[:4000],
            relevant_memories=memory_cards[:20],
        )


class ConversationContextManager:
    """Small context assembler kept separate from extraction authorization."""

    def build(
        self,
        current_user_message: str,
        *,
        conversation_history: list[Any] | tuple[Any, ...] = (),
        pending_memory_context: PendingMemoryContext | None = None,
        pending_questions: list[PendingQuestion] | None = None,
        active_topic: str | None = None,
        relevant_memories: list[Any] | tuple[Any, ...] = (),
    ) -> ConversationContext:
        return ConversationContext.from_turn(
            current_user_message,
            conversation_history=conversation_history,
            pending_memory_context=pending_memory_context,
            pending_questions=pending_questions,
            active_topic=active_topic,
            relevant_memories=relevant_memories,
        )


def build_conversation_context(
    current_user_message: str,
    *,
    conversation_history: list[Any] | tuple[Any, ...] = (),
    pending_memory_context: PendingMemoryContext | None = None,
    pending_questions: list[PendingQuestion] | None = None,
    active_topic: str | None = None,
    relevant_memories: list[Any] | tuple[Any, ...] = (),
) -> ConversationContext:
    """Functional facade for callers that do not need a manager instance."""

    return ConversationContext.from_turn(
        current_user_message,
        conversation_history=conversation_history,
        pending_memory_context=pending_memory_context,
        pending_questions=pending_questions,
        active_topic=active_topic,
        relevant_memories=relevant_memories,
    )


def _message_role(message: Any) -> str:
    role = getattr(message, "role", None)
    return str(getattr(role, "value", role) or "").casefold()


def _memory_context_card(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        value = dict(item)
    else:
        model_dump = getattr(item, "model_dump", None)
        if callable(model_dump):
            try:
                value = model_dump(mode="json")
            except TypeError:
                value = model_dump()
            if not isinstance(value, dict):
                value = {"summary": str(value)}
        else:
            value = {"summary": str(getattr(item, "summary", item))}

    # A context card is descriptive only.  In particular, never expose
    # storage identifiers or mutation-shaped fields to either extraction
    # stage.  The resolver receives authoritative MemoryItems separately.
    allowed = {
        "kind",
        "subject",
        "summary",
        "evidence_spans",
        "time_kind",
        "occurred_at",
        "period_start",
        "period_end",
        "expires_at",
        "perspective",
        "epistemic_status",
        "status",
        "predicate_type",
        "canonical_predicate",
        "custom_predicate",
        "state_dimension",
        "state_value",
        "payload",
    }
    card = {key: value[key] for key in allowed if key in value}
    return _strip_context_authority(card)


def _strip_context_authority(value: Any) -> Any:
    forbidden = {
        "id",
        "memory_id",
        "target_memory_id",
        "target_memory_ids",
        "mutation_action",
        "db_patch",
        "write_action",
        "supersedes_id",
    }
    if isinstance(value, dict):
        return {
            key: _strip_context_authority(child)
            for key, child in value.items()
            if str(key).casefold() not in forbidden
        }
    if isinstance(value, list):
        return [_strip_context_authority(child) for child in value]
    return value


class DatePlanRuntimeContext(BaseModel):
    """Read-only committed date-planning state exposed to a single turn."""

    status: DatePlanningStatus | None = None
    city: str | None = None
    area: str | None = None
    plan_mode: DatePlanMode | None = None
    date: Date | None = None
    end_date: Date | None = None
    day_count: int | None = None
    budget: int | None = None
    budget_scope: BudgetScope | None = None
    transport_mode: TransportMode | None = None
    requirements: list[DateStopRequirement] = Field(default_factory=list, max_length=16)
    requirement_satisfaction: list[DateRequirementMatch] = Field(
        default_factory=list,
        max_length=16,
    )
    current_plan: DatePlan | None = None
    plan_version: int = Field(default=0, ge=0)
    missing_fields: list[str] = Field(default_factory=list)


class RuntimeContext(BaseModel):
    """Trusted state snapshot available to routing and workflow code.

    The snapshot deliberately does not contain current-turn changes. Those are
    represented separately by ``DatePlanPatch`` and applied by the workflow.
    """

    user_id: str
    relationship_id: str
    conversation_id: str
    relationship_stage: RelationshipStage
    active_task: TaskType | None = None
    active_date_plan: DatePlanRuntimeContext | None = None
    pending_memory_context: PendingMemoryContext | None = None
    timezone: str = "Asia/Shanghai"
    now: datetime

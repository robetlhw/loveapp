from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from loveapp.domain.memory import (
    AdmissionDecision,
    ClaimRelation,
    ContextualMemoryUpdate,
    MemoryCandidate,
    MemorySaveResult,
    MemoryStatus,
    MutationAction,
    utc_now,
)
from loveapp.domain.memory_architecture_vnext import EventDetail
from loveapp.domain.memory_event_enrichment import (
    ConflictEventEnrichment,
    GenericEventEnrichment,
)
from loveapp.domain.relationship_plan import PlanStatus


class MemoryAuditDraft(BaseModel):
    candidate_index: int | None = Field(default=None, ge=0)
    relation: ClaimRelation
    decision: AdmissionDecision
    target_memory_ids: list[str] = Field(default_factory=list)
    rule_name: str
    admission_score: float | None = Field(default=None, ge=0, le=1)
    score_breakdown: dict[str, object] = Field(default_factory=dict)
    raw_predicate: str | None = None
    canonical_predicate: str | None = None
    extractor_model: str | None = None
    verifier_model: str | None = None
    prompt_version: str | None = None
    evidence: list[str] = Field(default_factory=list)
    reason: str
    mutation_action: MutationAction | None = None

    @model_validator(mode="after")
    def fill_mutation_action(self) -> "MemoryAuditDraft":
        if self.mutation_action is None:
            self.mutation_action = infer_mutation_action(
                self.relation,
                rule_name=self.rule_name,
                target_memory_ids=self.target_memory_ids,
            )
        return self


class MemoryWriteOperation(BaseModel):
    candidate: MemoryCandidate
    status: MemoryStatus
    relation: ClaimRelation = ClaimRelation.UNRELATED
    target_memory_ids: list[str] = Field(default_factory=list)
    target_operation_indexes: list[int] = Field(default_factory=list)
    target_status: MemoryStatus = MemoryStatus.SUPERSEDED
    mutation_action: MutationAction | None = None
    source_event_operation_indexes: list[int] = Field(default_factory=list, max_length=20)
    rule_name: str = "local_unrelated"
    reason: str = "No deterministic lifecycle transition was required."
    score_breakdown: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def fill_mutation_action(self) -> "MemoryWriteOperation":
        if any(index < 0 for index in self.source_event_operation_indexes):
            raise ValueError("source event operation indexes must be non-negative")
        self.source_event_operation_indexes = list(
            dict.fromkeys(self.source_event_operation_indexes)
        )
        if self.mutation_action is None:
            self.mutation_action = infer_mutation_action(
                self.relation,
                rule_name=self.rule_name,
                target_memory_ids=self.target_memory_ids,
            )
        return self


class MemoryStatusUpdate(BaseModel):
    memory_id: str
    status: MemoryStatus
    rule_name: str
    reason: str


class RelationshipPlanStatusUpdate(BaseModel):
    plan_id: str
    status: PlanStatus
    candidate_index: int | None = Field(default=None, ge=0)
    transitioned_at: datetime | None = None


class MemoryWriteBatch(BaseModel):
    source_message_id: str | None = None
    operations: list[MemoryWriteOperation] = Field(default_factory=list)
    contextual_updates: list[ContextualMemoryUpdate] = Field(default_factory=list)
    conflict_event_enrichments: list[ConflictEventEnrichment] = Field(
        default_factory=list
    )
    event_enrichments: list[GenericEventEnrichment] = Field(default_factory=list)
    event_details: list[EventDetail] = Field(default_factory=list, max_length=20)
    status_updates: list[MemoryStatusUpdate] = Field(default_factory=list)
    plan_updates: list[RelationshipPlanStatusUpdate] = Field(default_factory=list)
    audit_only: list[MemoryAuditDraft] = Field(default_factory=list)


class MemoryTransitionAudit(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    relationship_id: str
    source_message_id: str | None = None
    incoming_memory_id: str | None = None
    target_memory_ids: list[str] = Field(default_factory=list)
    relation: ClaimRelation
    decision: AdmissionDecision
    rule_name: str
    admission_score: float | None = Field(default=None, ge=0, le=1)
    score_breakdown: dict[str, object] = Field(default_factory=dict)
    raw_predicate: str | None = None
    canonical_predicate: str | None = None
    extractor_model: str | None = None
    verifier_model: str | None = None
    prompt_version: str | None = None
    evidence: list[str] = Field(default_factory=list)
    reason: str
    mutation_action: MutationAction | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def fill_mutation_action(self) -> "MemoryTransitionAudit":
        if self.mutation_action is None:
            self.mutation_action = infer_mutation_action(
                self.relation,
                rule_name=self.rule_name,
                target_memory_ids=self.target_memory_ids,
            )
        return self


class MemoryWriteBatchResult(BaseModel):
    saved: list[MemorySaveResult] = Field(default_factory=list)
    updated_memory_ids: list[str] = Field(default_factory=list)
    audits: list[MemoryTransitionAudit] = Field(default_factory=list)
    saved_event_details: list[EventDetail] = Field(default_factory=list)


def resolve_operation_target_ids(
    operation: MemoryWriteOperation,
    saved_memory_ids: list[str],
    *,
    operation_index: int,
) -> list[str]:
    resolved = list(operation.target_memory_ids)
    for target_index in operation.target_operation_indexes:
        if target_index < 0 or target_index >= len(saved_memory_ids):
            raise ValueError("memory batch target operation index is out of range")
        if target_index == operation_index:
            raise ValueError("memory batch operation cannot target itself")
        resolved.append(saved_memory_ids[target_index])
    return list(dict.fromkeys(resolved))


def plan_mutation_action(
    relation: ClaimRelation,
    *,
    rule_name: str = "",
    target_memory_ids: list[str] | tuple[str, ...] = (),
) -> MutationAction:
    """Map an existing governed relation/rule to a bounded write action."""

    rule = rule_name.casefold()
    if rule.startswith("enrich_") or "enrichment" in rule or rule.startswith("contextual_"):
        return MutationAction.ENRICH if "enrich" in rule else MutationAction.UPDATE
    if "link" in rule:
        return MutationAction.LINK
    if relation == ClaimRelation.SAME:
        return MutationAction.REFINE
    if relation == ClaimRelation.UPDATE:
        return MutationAction.SUPERSEDE if target_memory_ids else MutationAction.UPDATE
    if relation in {ClaimRelation.CONTRADICTION, ClaimRelation.UNCERTAIN}:
        return MutationAction.REJECT
    if relation == ClaimRelation.COMPLEMENTARY:
        return MutationAction.LINK if target_memory_ids else MutationAction.CREATE
    return MutationAction.CREATE


def infer_mutation_action(
    relation: ClaimRelation,
    *,
    rule_name: str = "",
    target_memory_ids: list[str] | tuple[str, ...] = (),
) -> MutationAction:
    """Backward-compatible name for the governed mutation planner."""

    return plan_mutation_action(
        relation,
        rule_name=rule_name,
        target_memory_ids=target_memory_ids,
    )

from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from loveapp.domain.memory import (
    AdmissionDecision,
    ClaimRelation,
    MemoryCandidate,
    MemorySaveResult,
    MemoryStatus,
    utc_now,
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


class MemoryWriteOperation(BaseModel):
    candidate: MemoryCandidate
    status: MemoryStatus
    relation: ClaimRelation = ClaimRelation.UNRELATED
    target_memory_ids: list[str] = Field(default_factory=list)
    target_operation_indexes: list[int] = Field(default_factory=list)
    target_status: MemoryStatus = MemoryStatus.SUPERSEDED
    rule_name: str = "local_unrelated"
    reason: str = "No deterministic lifecycle transition was required."
    score_breakdown: dict[str, object] = Field(default_factory=dict)


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
    created_at: datetime = Field(default_factory=utc_now)


class MemoryWriteBatchResult(BaseModel):
    saved: list[MemorySaveResult] = Field(default_factory=list)
    audits: list[MemoryTransitionAudit] = Field(default_factory=list)


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

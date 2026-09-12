"""Small, explicit contracts for the next memory architecture.

These models deliberately sit between semantic extraction and storage.  They
do not replace the V1/V2 memory models yet; they make the authority boundary
explicit so later pipeline phases can be introduced without teaching the
extractor how to write or mutate a Store row.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from loveapp.domain.memory import EpistemicStatus, MemoryItem, MemoryKind, MemoryPerspective
from loveapp.domain.memory_semantic_units import SemanticUnitProvenance


class CandidateSource(StrEnum):
    """Bounded provenance labels for candidate generation channels."""

    PENDING_QUESTION = "pending_question"
    EXPLICIT_REFERENCE = "explicit_reference"
    RECENT_EVENT_CONTEXT = "recent_event_context"
    STRUCTURED_LOOKUP = "structured_lookup"
    VECTOR_FALLBACK = "vector_fallback"


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class WriteOperation(StrEnum):
    """Only operations that the VNext write policy may authorize."""

    CREATE_CORE = "create_core"
    ENRICH_CORE = "enrich_core"
    ATTACH_DETAIL = "attach_detail"
    CLARIFY = "clarify"
    NOOP = "noop"


class EventDetailStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


def _contains_write_authority(value: object) -> bool:
    forbidden = {
        "target_memory_id",
        "target_memory_ids",
        "mutation",
        "mutation_action",
        "supersedes_id",
        "db_action",
        "db_patch",
        "write_action",
        "operation",
    }
    if isinstance(value, dict):
        return any(
            str(key).casefold() in forbidden or _contains_write_authority(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_write_authority(child) for child in value)
    return False


class EventDetailDraft(BaseModel):
    """Semantic detail extracted from a proposition, without a write target."""

    model_config = ConfigDict(extra="forbid")

    semantic_type: Literal["event_detail"] = "event_detail"
    unit_id: str = Field(min_length=1, max_length=80)
    event_type_constraint: str | None = Field(default=None, max_length=80)
    detail_type: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=80)
    value: str | int | float | bool | dict[str, str] | list[str]
    evidence_span: str = Field(min_length=1, max_length=1000)
    source_proposition_id: str | None = Field(default=None, max_length=80)
    context_source_question_ids: list[str] = Field(default_factory=list, max_length=4)
    temporal_hint: str | None = Field(default=None, max_length=160)
    subject_hint: str | None = Field(default=None, max_length=80)
    reference_hints: dict[str, str] = Field(default_factory=dict, max_length=8)
    provenance: SemanticUnitProvenance | None = None
    perspective: MemoryPerspective = MemoryPerspective.USER_REPORTED
    epistemic_status: EpistemicStatus = EpistemicStatus.CONFIRMED
    confidence: float = Field(default=0.8, ge=0, le=1)

    @model_validator(mode="after")
    def validate_semantic_boundary(self) -> EventDetailDraft:
        if _contains_write_authority(self.model_dump(mode="python")):
            raise ValueError("event detail drafts cannot contain write authority")
        if self.event_type_constraint is not None and not self.event_type_constraint.strip():
            raise ValueError("event detail event_type_constraint cannot be blank")
        if any(not question_id.strip() for question_id in self.context_source_question_ids):
            raise ValueError("event detail question IDs cannot be blank")
        return self


class Candidate(BaseModel):
    """A candidate plus the channels and signals that produced it."""

    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(min_length=1, max_length=160)
    memory: MemoryItem
    sources: list[CandidateSource] = Field(default_factory=list, max_length=5)
    signals: dict[str, bool | float | str] = Field(default_factory=dict, max_length=16)
    hard_filter_result: str = Field(default="accepted", max_length=40)
    rank: int | None = Field(default=None, ge=1, le=100)
    score: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_identity(self) -> Candidate:
        if self.memory.id != self.memory_id:
            raise ValueError("candidate memory_id must match memory.id")
        self.sources = list(dict.fromkeys(self.sources))
        return self


class ClarificationRequired(BaseModel):
    """Safe application-layer result when target identity is ambiguous."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=240)
    field: str | None = Field(default=None, max_length=80)
    candidate_memory_ids: list[str] = Field(default_factory=list, max_length=8)
    question: str = Field(min_length=1, max_length=500)


class TargetResolution(BaseModel):
    """Resolver output; it says who, never how to mutate that target."""

    model_config = ConfigDict(extra="forbid")

    status: ResolutionStatus
    target_memory_id: str | None = None
    candidate_ids: list[str] = Field(default_factory=list, max_length=8)
    resolution_evidence: list[str] = Field(default_factory=list, max_length=8)
    reason: str = Field(min_length=1, max_length=240)
    clarification: ClarificationRequired | None = None

    @model_validator(mode="after")
    def validate_resolution_shape(self) -> TargetResolution:
        self.candidate_ids = list(dict.fromkeys(self.candidate_ids))
        if self.status == ResolutionStatus.RESOLVED and not self.target_memory_id:
            raise ValueError("resolved target resolution requires one target_memory_id")
        if self.status != ResolutionStatus.RESOLVED and self.target_memory_id is not None:
            raise ValueError("ambiguous/unresolved resolution cannot select a target")
        if self.status == ResolutionStatus.AMBIGUOUS and self.clarification is None:
            raise ValueError("ambiguous resolution requires clarification metadata")
        if self.status != ResolutionStatus.AMBIGUOUS and self.clarification is not None:
            raise ValueError("clarification is only valid for ambiguous resolution")
        return self


class EventDetail(BaseModel):
    """Lightweight detail attached to one InteractionEvent, not a Core kind."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=160)
    parent_event_id: str = Field(min_length=1, max_length=160)
    parent_event_kind: Literal[MemoryKind.INTERACTION_EVENT] = MemoryKind.INTERACTION_EVENT
    detail_type: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=80)
    value: str | int | float | bool | dict[str, str] | list[str]
    evidence_span: str = Field(min_length=1, max_length=1000)
    source_message_id: str = Field(min_length=1, max_length=160)
    source_proposition_id: str | None = Field(default=None, max_length=80)
    perspective: MemoryPerspective = MemoryPerspective.USER_REPORTED
    epistemic_status: EpistemicStatus = EpistemicStatus.CONFIRMED
    created_at: datetime
    status: EventDetailStatus = EventDetailStatus.ACTIVE


class WriteDecision(BaseModel):
    """Write-policy output separated from semantic extraction and resolution."""

    model_config = ConfigDict(extra="forbid")

    operation: WriteOperation
    reason: str = Field(min_length=1, max_length=240)
    target_memory_id: str | None = None
    detail: EventDetail | None = None
    clarification: ClarificationRequired | None = None

    @model_validator(mode="after")
    def validate_write_boundary(self) -> WriteDecision:
        if self.operation in {
            WriteOperation.ENRICH_CORE,
            WriteOperation.ATTACH_DETAIL,
        } and not self.target_memory_id:
            raise ValueError("targeted write decisions require a target_memory_id")
        if self.operation == WriteOperation.ATTACH_DETAIL and self.detail is None:
            raise ValueError("attach_detail decisions require an EventDetail")
        if self.operation == WriteOperation.CLARIFY and self.clarification is None:
            raise ValueError("clarify decisions require clarification metadata")
        if self.operation != WriteOperation.CLARIFY and self.clarification is not None:
            raise ValueError("clarification is only valid for clarify decisions")
        return self


__all__ = [
    "Candidate",
    "CandidateSource",
    "ClarificationRequired",
    "EventDetail",
    "EventDetailDraft",
    "EventDetailStatus",
    "ResolutionStatus",
    "TargetResolution",
    "WriteDecision",
    "WriteOperation",
]

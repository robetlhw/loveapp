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
from loveapp.domain.memory_semantic_units import EventDetailDraft


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


class CandidateGenerationResult(BaseModel):
    """Candidate set plus whether deterministic channels covered the scope."""

    model_config = ConfigDict(extra="forbid")

    candidates: list[Candidate] = Field(default_factory=list, max_length=20)
    channels_used: list[CandidateSource] = Field(default_factory=list, max_length=5)
    candidate_set_complete: bool = False
    reason: str = Field(default="candidate_generation_completed", max_length=240)

    @model_validator(mode="after")
    def normalize_channels(self) -> CandidateGenerationResult:
        self.channels_used = list(dict.fromkeys(self.channels_used))
        return self

    def as_trace(self) -> dict[str, object]:
        """Return a bounded, ID-only diagnostic payload for observability."""

        return {
            "candidate_memory_ids": [candidate.memory_id for candidate in self.candidates[:20]],
            "candidate_details": [
                {
                    "memory_id": candidate.memory_id,
                    "sources": [source.value for source in candidate.sources],
                    "signals": dict(candidate.signals),
                    "hard_filter_result": candidate.hard_filter_result,
                    "rank": candidate.rank,
                    "score": candidate.score,
                    "event_type_match": candidate.signals.get("event_type_match"),
                    "subject_match": candidate.signals.get("subject_match"),
                    "temporal_match": candidate.signals.get("temporal_match"),
                    "explicit_reference_match": candidate.signals.get("explicit_reference_match"),
                    "pending_binding_match": candidate.signals.get("pending_binding_match"),
                    "vector_score": candidate.score,
                }
                for candidate in self.candidates[:20]
            ],
            "candidate_sources": {
                candidate.memory_id: [source.value for source in candidate.sources]
                for candidate in self.candidates[:20]
            },
            "candidate_signals": {
                candidate.memory_id: dict(candidate.signals) for candidate in self.candidates[:20]
            },
            "channels_used": [source.value for source in self.channels_used],
            "candidate_set_complete": self.candidate_set_complete,
            "reason": self.reason,
        }


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

    def as_trace(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "target_memory_id": self.target_memory_id,
            "candidate_ids": list(self.candidate_ids),
            "resolution_evidence": list(self.resolution_evidence),
            "reason": self.reason,
            "clarification_required": self.clarification is not None,
        }


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
        if (
            self.operation
            in {
                WriteOperation.ENRICH_CORE,
                WriteOperation.ATTACH_DETAIL,
            }
            and not self.target_memory_id
        ):
            raise ValueError("targeted write decisions require a target_memory_id")
        if self.operation == WriteOperation.ATTACH_DETAIL and self.detail is None:
            raise ValueError("attach_detail decisions require an EventDetail")
        if self.operation == WriteOperation.CLARIFY and self.clarification is None:
            raise ValueError("clarify decisions require clarification metadata")
        if self.operation != WriteOperation.CLARIFY and self.clarification is not None:
            raise ValueError("clarification is only valid for clarify decisions")
        return self

    def as_trace(self) -> dict[str, object]:
        return {
            "operation": self.operation.value,
            "target_memory_id": self.target_memory_id,
            "has_event_detail": self.detail is not None,
            "clarification_required": self.clarification is not None,
            "reason": self.reason,
        }


__all__ = [
    "Candidate",
    "CandidateGenerationResult",
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

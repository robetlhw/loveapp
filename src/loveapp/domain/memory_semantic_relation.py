"""Typed contracts for read-only long-tail memory relation evaluation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from loveapp.domain.memory import ClaimRelation, MemoryKind, MemoryStatus


class SemanticRelationProposal(BaseModel):
    """A model-proposed relation with no authority to mutate Memory."""

    model_config = ConfigDict(extra="forbid")

    relation: ClaimRelation
    target_memory_ids: list[str] = Field(max_length=5)
    same_semantic_dimension: bool
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)
    judge_model: str | None = Field(default=None, max_length=160)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    latency_ms: float | None = Field(default=None, ge=0)


class LongTailCandidateMatch(BaseModel):
    """Explainable retrieval evidence exposed by the shadow evaluator."""

    model_config = ConfigDict(extra="forbid")

    memory_id: str
    kind: MemoryKind
    subject: str
    summary: str
    status: MemoryStatus
    score: dict[str, float] = Field(default_factory=dict)


class LongTailRelationValidation(BaseModel):
    """Deterministic authorization preview for a semantic proposal."""

    model_config = ConfigDict(extra="forbid")

    validator_pass: bool
    validated_relation: ClaimRelation
    validator_reasons: list[str] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)
    would_update: bool = False
    would_supersede_memory_ids: list[str] = Field(default_factory=list)


class LongTailRelationShadowResult(BaseModel):
    """Phase 2A/2B result. It is intentionally not a write-path type."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["shadow"] = "shadow"
    store_mutation_permitted: Literal[False] = False
    judge_status: Literal["completed", "failed", "not_called"]
    judge_error_type: str | None = None
    incoming_summary: str
    retrieved_candidates: list[LongTailCandidateMatch] = Field(default_factory=list)
    proposal: SemanticRelationProposal
    validation: LongTailRelationValidation


__all__ = [
    "LongTailCandidateMatch",
    "LongTailRelationShadowResult",
    "LongTailRelationValidation",
    "SemanticRelationProposal",
]

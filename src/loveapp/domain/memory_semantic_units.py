"""Semantic extraction drafts that remain separate from write authorization."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    EpistemicStatus,
    MemoryKind,
    MemoryPerspective,
)


class AttributeNamespace(StrEnum):
    CANONICAL = "canonical"
    CUSTOM = "custom"


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
    }
    if isinstance(value, dict):
        return any(
            str(key).casefold() in forbidden or _contains_write_authority(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_write_authority(child) for child in value)
    return False


class NewMemoryDraft(AtomicClaim):
    """A new proposition with a lossless adapter to the frozen claim contract."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    semantic_type: Literal["new_memory"] = "new_memory"
    payload: dict[str, Any] = Field(default_factory=dict, exclude=True)
    semantic_payload: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("semantic_payload", "payload"),
    )

    @model_validator(mode="after")
    def synchronize_semantic_payload(self) -> NewMemoryDraft:
        self.payload = dict(self.semantic_payload)
        return self

    def to_atomic_claim(self) -> AtomicClaim:
        return AtomicClaim.model_validate(
            {
                name: (
                    self.semantic_payload
                    if name == "payload"
                    else getattr(self, name)
                )
                for name in AtomicClaim.model_fields
                if name in self.model_fields_set or name == "payload"
            }
        )

    @classmethod
    def from_atomic_claim(cls, claim: AtomicClaim) -> NewMemoryDraft:
        return cls.model_validate(
            {
                "semantic_type": "new_memory",
                **claim.model_dump(
                    mode="python",
                    exclude_unset=True,
                    exclude={"payload"},
                ),
                "semantic_payload": claim.payload,
            }
        )


class EnrichmentDraft(BaseModel):
    """An untrusted semantic proposal to complete one Event attribute."""

    model_config = ConfigDict(extra="forbid")

    semantic_type: Literal["enrichment"] = "enrichment"
    unit_id: str = Field(min_length=1, max_length=80)
    target_kind: MemoryKind
    target_semantic_hint: dict[str, Any] = Field(default_factory=dict)
    attribute_namespace: AttributeNamespace
    attribute_name: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=80)
    value: str | int | dict[str, str] | list[str]
    evidence_span: str = Field(min_length=1, max_length=1000)
    subject_hint: str | None = Field(default=None, max_length=80)
    temporal_hint: str | None = Field(default=None, max_length=160)
    perspective: MemoryPerspective = MemoryPerspective.USER_REPORTED
    epistemic_status: EpistemicStatus = EpistemicStatus.CONFIRMED
    confidence: float = Field(default=0.8, ge=0, le=1)

    @model_validator(mode="after")
    def validate_untrusted_hint(self) -> EnrichmentDraft:
        if self.target_kind != MemoryKind.INTERACTION_EVENT:
            raise ValueError("generic enrichment is limited to interaction_event")
        if _contains_write_authority(self.target_semantic_hint):
            raise ValueError("enrichment hints cannot contain write authority")
        return self


class RefinementDraft(BaseModel):
    """A trace-only proposal to make one existing proposition more precise."""

    model_config = ConfigDict(extra="forbid")

    semantic_type: Literal["refinement"] = "refinement"
    unit_id: str = Field(min_length=1, max_length=80)
    target_kind: MemoryKind
    target_semantic_hint: dict[str, Any] = Field(default_factory=dict)
    raw_predicate: str = Field(min_length=1, max_length=120)
    value: str | int | dict[str, str] | list[str]
    evidence_span: str = Field(min_length=1, max_length=1000)
    subject_hint: str | None = Field(default=None, max_length=80)
    temporal_hint: str | None = Field(default=None, max_length=160)
    confidence: float = Field(default=0.8, ge=0, le=1)

    @model_validator(mode="after")
    def validate_untrusted_hint(self) -> RefinementDraft:
        if _contains_write_authority(self.target_semantic_hint):
            raise ValueError("refinement hints cannot contain write authority")
        return self


type ExtractedSemanticUnit = Annotated[
    NewMemoryDraft | EnrichmentDraft | RefinementDraft,
    Field(discriminator="semantic_type"),
]


class SemanticAtomicExtraction(AtomicExtraction):
    """AtomicExtraction-compatible result with richer semantic draft output."""

    semantic_units: list[ExtractedSemanticUnit] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def synchronize_new_memory_claims(self) -> SemanticAtomicExtraction:
        new_claims = [
            unit.to_atomic_claim()
            for unit in self.semantic_units
            if isinstance(unit, NewMemoryDraft)
        ]
        if not self.semantic_units and self.claims:
            self.semantic_units = [
                NewMemoryDraft.from_atomic_claim(claim) for claim in self.claims
            ]
            new_claims = list(self.claims)
        if self.should_extract is False and self.semantic_units:
            raise ValueError("should_extract=false cannot contain semantic units")
        if self.claims and [claim.model_dump() for claim in self.claims] != [
            claim.model_dump() for claim in new_claims
        ]:
            raise ValueError("semantic new-memory drafts and claims disagree")
        self.claims = new_claims
        return self


__all__ = [
    "AttributeNamespace",
    "EnrichmentDraft",
    "ExtractedSemanticUnit",
    "NewMemoryDraft",
    "RefinementDraft",
    "SemanticAtomicExtraction",
]

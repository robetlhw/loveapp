from pydantic import BaseModel, ConfigDict, Field

from loveapp.domain.memory import ClaimRelation


class ClaimVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_supported: bool
    relation: ClaimRelation = ClaimRelation.UNCERTAIN
    canonical_predicate: str | None = None
    state_dimension: str | None = None
    state_value: str | None = None
    target_memory_ids: list[str] = Field(default_factory=list, max_length=10)
    reason: str = Field(min_length=1, max_length=500)
    evidence_sufficient: bool
    verifier_model: str | None = None

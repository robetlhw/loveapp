from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from loveapp.domain.memory import ClaimRelation, MemoryCandidate, MemoryKind
from loveapp.domain.memory_epistemics import is_epistemically_confirmed


class MemoryComparisonMode(StrEnum):
    SEMANTIC = "semantic"
    EVIDENCE_ONLY = "evidence_only"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True)
class MemoryTypeCompatibility:
    mode: MemoryComparisonMode
    reason: str
    allowed_relations: frozenset[ClaimRelation]
    lifecycle_replace_allowed: bool

    @property
    def semantic_relation_allowed(self) -> bool:
        return self.mode == MemoryComparisonMode.SEMANTIC

    @property
    def evidence_only(self) -> bool:
        return self.mode == MemoryComparisonMode.EVIDENCE_ONLY

    def allows(self, relation: ClaimRelation) -> bool:
        return relation in self.allowed_relations


_NON_DESTRUCTIVE_RELATIONS = frozenset(
    {
        ClaimRelation.SAME,
        ClaimRelation.COMPLEMENTARY,
        ClaimRelation.CONTRADICTION,
        ClaimRelation.UNRELATED,
        ClaimRelation.UNCERTAIN,
    }
)
_EVENT_RELATIONS = frozenset(
    {
        ClaimRelation.SAME,
        ClaimRelation.COMPLEMENTARY,
        ClaimRelation.UNRELATED,
        ClaimRelation.UNCERTAIN,
    }
)
_EPISTEMIC_CROSS_RELATIONS = frozenset(
    {
        ClaimRelation.COMPLEMENTARY,
        ClaimRelation.CONTRADICTION,
        ClaimRelation.UNRELATED,
        ClaimRelation.UNCERTAIN,
    }
)
_ALL_RELATIONS = frozenset(ClaimRelation)


def assess_memory_type_compatibility(
    incoming: MemoryCandidate,
    target: MemoryCandidate,
) -> MemoryTypeCompatibility:
    """Apply V2.2 type boundaries before semantic target selection."""

    kinds = {incoming.kind, target.kind}
    incoming_fact = is_epistemically_confirmed(incoming)
    target_fact = is_epistemically_confirmed(target)
    if kinds == {MemoryKind.INTERACTION_EVENT, MemoryKind.INTERACTION_PATTERN}:
        return MemoryTypeCompatibility(
            MemoryComparisonMode.EVIDENCE_ONLY,
            "event_pattern_evidence_boundary",
            frozenset({ClaimRelation.UNRELATED, ClaimRelation.UNCERTAIN}),
            incoming_fact and target_fact,
        )
    if MemoryKind.PREFERENCE in kinds and MemoryKind.INTERACTION_EVENT in kinds:
        return MemoryTypeCompatibility(
            MemoryComparisonMode.INCOMPATIBLE,
            "preference_event_type_boundary",
            frozenset({ClaimRelation.UNRELATED, ClaimRelation.UNCERTAIN}),
            False,
        )
    if incoming_fact != target_fact:
        return MemoryTypeCompatibility(
            MemoryComparisonMode.SEMANTIC,
            "epistemic_fact_boundary",
            _EPISTEMIC_CROSS_RELATIONS,
            False,
        )
    both_confirmed_facts = incoming_fact and target_fact
    if kinds == {MemoryKind.INTERACTION_EVENT}:
        return MemoryTypeCompatibility(
            MemoryComparisonMode.SEMANTIC,
            "event_semantic_comparison",
            _EVENT_RELATIONS,
            both_confirmed_facts,
        )
    return MemoryTypeCompatibility(
        MemoryComparisonMode.SEMANTIC,
        "semantic_comparison_allowed",
        _ALL_RELATIONS if both_confirmed_facts else _NON_DESTRUCTIVE_RELATIONS,
        both_confirmed_facts,
    )

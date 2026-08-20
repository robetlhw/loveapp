from dataclasses import dataclass

from loveapp.domain.memory import (
    ClaimRelation,
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    PredicateType,
    memory_dedupe_key,
)
from loveapp.domain.memory_lifecycle import (
    governed_state_identity,
    governed_state_value,
    memory_concept,
)
from loveapp.domain.memory_predicates import normalize_preference_value


@dataclass(frozen=True)
class ClaimRelationResolution:
    relation: ClaimRelation
    target_memory_ids: tuple[str, ...] = ()
    rule_name: str = "local_unrelated"
    reason: str = "No conflicting active memory was found."


def resolve_claim_relation(
    candidate: MemoryCandidate,
    active_memories: list[MemoryItem],
    *,
    incoming_status: MemoryStatus,
) -> ClaimRelationResolution:
    candidate_key = memory_dedupe_key(candidate)
    same = [item for item in active_memories if memory_dedupe_key(item) == candidate_key]
    if same:
        keeper = max(same, key=_keeper_rank)
        return ClaimRelationResolution(
            ClaimRelation.SAME,
            (keeper.id,),
            "normalized_dedupe",
            "The normalized claim identity already exists in this relationship.",
        )

    state_identity = governed_state_identity(candidate)
    state_value = governed_state_value(candidate)
    if state_identity is not None and state_value is not None:
        same_dimension = [
            item
            for item in active_memories
            if item.subject.casefold() == candidate.subject.casefold()
            and governed_state_identity(item) == state_identity
            and governed_state_value(item) not in {None, state_value}
        ]
        if same_dimension:
            confirmed = [item for item in same_dimension if item.status == MemoryStatus.CONFIRMED]
            if incoming_status == MemoryStatus.CONFIRMED:
                return ClaimRelationResolution(
                    ClaimRelation.UPDATE,
                    tuple(item.id for item in same_dimension),
                    "same_state_dimension",
                    "A confirmed value replaces older active values in the same state dimension.",
                )
            return ClaimRelationResolution(
                ClaimRelation.CONTRADICTION,
                tuple(item.id for item in confirmed or same_dimension),
                "proposed_state_conflict",
                "An unconfirmed value cannot close an existing state value.",
            )

    candidate_concept = memory_concept(candidate)
    if candidate_concept in {"contact_reduced", "contact_restored"}:
        opposite_concept = (
            "contact_restored"
            if candidate_concept == "contact_reduced"
            else "contact_reduced"
        )
        opposite = [
            item
            for item in active_memories
            if item.subject.casefold() == candidate.subject.casefold()
            and memory_concept(item) == opposite_concept
        ]
        if opposite:
            confirmed = [item for item in opposite if item.status == MemoryStatus.CONFIRMED]
            if incoming_status == MemoryStatus.CONFIRMED:
                return ClaimRelationResolution(
                    ClaimRelation.UPDATE,
                    tuple(item.id for item in opposite),
                    "contact_state_transition",
                    "A confirmed contact state replaces an opposite active contact representation.",
                )
            return ClaimRelationResolution(
                ClaimRelation.CONTRADICTION,
                tuple(item.id for item in confirmed or opposite),
                "proposed_contact_state_conflict",
                "An unconfirmed contact state cannot close an existing confirmed contact state.",
            )

    if candidate.kind == MemoryKind.PREFERENCE:
        preference_resolution = _resolve_preference(candidate, active_memories, incoming_status)
        if preference_resolution is not None:
            return preference_resolution

    if candidate.predicate_type == PredicateType.CUSTOM:
        related_custom = [
            item
            for item in active_memories
            if item.subject.casefold() == candidate.subject.casefold()
            and item.kind == candidate.kind
            and item.predicate_type == PredicateType.CUSTOM
        ]
        if related_custom:
            return ClaimRelationResolution(
                ClaimRelation.UNCERTAIN,
                tuple(item.id for item in related_custom[:5]),
                "unknown_transition",
                "A custom predicate has no deterministic lifecycle rule.",
            )
        return ClaimRelationResolution(
            ClaimRelation.UNCERTAIN,
            (),
            "unknown_transition",
            "A new custom predicate is retained without automatic lifecycle effects.",
        )

    return ClaimRelationResolution(
        ClaimRelation.UNRELATED,
        (),
        "local_unrelated",
        "The claim is independently useful and has no deterministic conflict.",
    )


def has_local_conflict(candidate: MemoryCandidate, active_memories: list[MemoryItem]) -> bool:
    state_identity = governed_state_identity(candidate)
    state_value = governed_state_value(candidate)
    if state_identity is not None and state_value is not None:
        same_dimension_conflict = any(
            item.subject.casefold() == candidate.subject.casefold()
            and governed_state_identity(item) == state_identity
            and governed_state_value(item) not in {None, state_value}
            for item in active_memories
        )
        if same_dimension_conflict:
            return True
    candidate_concept = memory_concept(candidate)
    if candidate_concept in {"contact_reduced", "contact_restored"}:
        opposite_concept = (
            "contact_restored"
            if candidate_concept == "contact_reduced"
            else "contact_reduced"
        )
        return any(
            item.subject.casefold() == candidate.subject.casefold()
            and memory_concept(item) == opposite_concept
            for item in active_memories
        )
    if candidate.kind == MemoryKind.PREFERENCE:
        resolution = _resolve_preference(candidate, active_memories, MemoryStatus.PROPOSED)
        return resolution is not None and resolution.relation == ClaimRelation.CONTRADICTION
    return False


def _resolve_preference(
    candidate: MemoryCandidate,
    active_memories: list[MemoryItem],
    incoming_status: MemoryStatus,
) -> ClaimRelationResolution | None:
    value = candidate.payload.get("preference")
    if not isinstance(value, str):
        return None
    normalized_value = normalize_preference_value(value)
    polarity = _preference_polarity(candidate)
    related = [
        item
        for item in active_memories
        if item.kind == MemoryKind.PREFERENCE
        and item.subject.casefold() == candidate.subject.casefold()
        and item.canonical_predicate == candidate.canonical_predicate
    ]
    for item in related:
        item_value = item.payload.get("preference")
        if not isinstance(item_value, str):
            continue
        normalized_item_value = normalize_preference_value(item_value)
        item_polarity = _preference_polarity(item)
        if normalized_item_value == normalized_value:
            if item_polarity == polarity:
                return ClaimRelationResolution(
                    ClaimRelation.SAME,
                    (item.id,),
                    "normalized_preference",
                    "Equivalent preference wording maps to the same normalized value.",
                )
            relation = (
                ClaimRelation.UPDATE
                if incoming_status == MemoryStatus.CONFIRMED
                else ClaimRelation.CONTRADICTION
            )
            return ClaimRelationResolution(
                relation,
                (item.id,),
                "preference_polarity_change",
                "The same preference value is described with an incompatible polarity.",
            )
        if _preference_values_are_hierarchical(normalized_item_value, normalized_value):
            return ClaimRelationResolution(
                ClaimRelation.COMPLEMENTARY,
                (item.id,),
                "preference_hierarchy",
                "The new preference is a compatible parent or child category.",
            )

    if related and candidate.canonical_predicate in {
        "preference.food.spiciness",
        "preference.environment.noise",
        "preference.budget.range",
    }:
        relation = (
            ClaimRelation.UPDATE
            if incoming_status == MemoryStatus.CONFIRMED
            else ClaimRelation.CONTRADICTION
        )
        return ClaimRelationResolution(
            relation,
            tuple(item.id for item in related),
            "single_value_preference_dimension",
            "This preference dimension is treated as a current single-value setting.",
        )
    if related:
        return ClaimRelationResolution(
            ClaimRelation.COMPLEMENTARY,
            tuple(item.id for item in related[:5]),
            "compatible_preference_values",
            "Different values in this open preference dimension may coexist.",
        )
    return None


def _preference_polarity(candidate: MemoryCandidate) -> str:
    value = str(candidate.payload.get("preference_type") or "like").casefold()
    if value in {"avoid", "allergy", "restriction", "dislike", "forbid"}:
        return "negative"
    return "positive"


def _preference_values_are_hierarchical(first: str, second: str) -> bool:
    pairs = {
        frozenset({"日料", "寿司"}),
        frozenset({"中餐", "川菜"}),
        frozenset({"展览", "美术展"}),
    }
    return frozenset({first, second}) in pairs


def _keeper_rank(item: MemoryItem) -> tuple[int, int, float, object]:
    return (
        int(item.status == MemoryStatus.CONFIRMED),
        item.importance,
        item.confidence,
        item.updated_at,
    )

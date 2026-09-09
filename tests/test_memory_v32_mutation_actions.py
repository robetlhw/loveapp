from loveapp.domain.memory import ClaimRelation, MemoryCandidate, MemoryKind, MutationAction
from loveapp.domain.memory_write import MemoryWriteOperation, infer_mutation_action


def _candidate() -> MemoryCandidate:
    return MemoryCandidate(
        kind=MemoryKind.RELATIONSHIP_STATE,
        subject="relationship",
        summary="The current conflict is resolved.",
        original_text="The current conflict is resolved.",
        canonical_predicate="relationship.conflict_status",
        state_dimension="relationship.conflict_status",
        state_value="resolved",
    )


def test_mutation_action_is_separate_from_claim_relation() -> None:
    operation = MemoryWriteOperation(
        candidate=_candidate(),
        status="confirmed",
        relation=ClaimRelation.UNRELATED,
        mutation_action=MutationAction.SUPERSEDE,
    )
    assert operation.relation == ClaimRelation.UNRELATED
    assert operation.mutation_action == MutationAction.SUPERSEDE


def test_existing_relation_and_rule_map_to_bounded_mutation_actions() -> None:
    assert infer_mutation_action(ClaimRelation.UNRELATED) == MutationAction.CREATE
    assert infer_mutation_action(ClaimRelation.SAME) == MutationAction.REFINE
    assert (
        infer_mutation_action(
            ClaimRelation.UPDATE,
            target_memory_ids=["old-state"],
        )
        == MutationAction.SUPERSEDE
    )
    assert (
        infer_mutation_action(
            ClaimRelation.COMPLEMENTARY,
            rule_name="enrich_event_cause",
            target_memory_ids=["event"],
        )
        == MutationAction.ENRICH
    )
    assert infer_mutation_action(ClaimRelation.UNCERTAIN) == MutationAction.REJECT

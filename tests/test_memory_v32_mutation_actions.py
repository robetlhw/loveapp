import pytest

from loveapp.adapters.memory import InMemoryMemoryStore, SQLiteMemoryStore
from loveapp.domain.advice import RelationshipContext
from loveapp.domain.memory import (
    AdmissionDecision,
    ClaimRelation,
    MemoryCandidate,
    MemoryKind,
    MutationAction,
)
from loveapp.domain.memory_write import (
    MemoryAuditDraft,
    MemoryWriteBatch,
    MemoryWriteOperation,
    infer_mutation_action,
    plan_mutation_action,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_explicit_audit_mutation_action_round_trips(backend, tmp_path) -> None:
    store = (
        SQLiteMemoryStore(tmp_path / "audit-action.db")
        if backend == "sqlite"
        else InMemoryMemoryStore()
    )
    await store.save_relationship_context(RelationshipContext(user_id="u", relationship_id="r"))
    batch = MemoryWriteBatch(
        audit_only=[
            MemoryAuditDraft(
                relation=ClaimRelation.COMPLEMENTARY,
                decision=AdmissionDecision.PROPOSE,
                target_memory_ids=["target"],
                rule_name="manual_review",
                reason="explicit planner audit",
                mutation_action=MutationAction.ENRICH,
            )
        ]
    )
    result = await store.commit_memory_batch(user_id="u", relationship_id="r", batch=batch)
    assert result.audits[0].mutation_action == MutationAction.ENRICH
    if backend == "sqlite":
        await store.aclose()


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
    assert plan_mutation_action(ClaimRelation.UNRELATED) == MutationAction.CREATE
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

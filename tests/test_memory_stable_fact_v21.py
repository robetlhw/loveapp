from datetime import UTC, datetime

import pytest

from loveapp.adapters.memory.in_memory import InMemoryMemoryStore
from loveapp.adapters.memory.openai_compatible import _SYSTEM_PROMPT
from loveapp.application.memory import MemoryService
from loveapp.application.memory_relations import resolve_claim_relation
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    ClaimRelation,
    EvidenceExplicitness,
    MemoryKind,
    MemoryStatus,
    PredicateType,
)
from loveapp.domain.memory_normalization import normalize_memory_candidate_contract
from loveapp.domain.memory_predicates import (
    PredicateCardinality,
    PredicateTemporalBehavior,
    PredicateUpdatePolicy,
    predicate_spec,
)

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)


class SequenceExtractor:
    def __init__(self, claims: list[AtomicClaim]) -> None:
        self._claims = claims
        self._index = 0

    async def extract(self, text: str, **kwargs: object) -> AtomicExtraction:
        del text, kwargs
        claim = self._claims[self._index]
        self._index += 1
        return AtomicExtraction(claims=[claim])


def _stable_claim(
    *,
    claim_id: str,
    text: str,
    predicate: str,
    value: str,
    canonical_predicate: str | None = None,
    confidence: float = 0.98,
) -> AtomicClaim:
    return AtomicClaim(
        claim_id=claim_id,
        kind=MemoryKind.STABLE_FACT,
        subject="user",
        predicate=predicate,
        object=value,
        summary=text.removeprefix("记一下："),
        evidence_spans=[text],
        confidence=confidence,
        explicitness=EvidenceExplicitness.EXPLICIT,
        predicate_type=(
            PredicateType.CANONICAL
            if canonical_predicate is not None
            else PredicateType.CUSTOM
        ),
        canonical_predicate=canonical_predicate,
        custom_predicate=None if canonical_predicate is not None else predicate,
    )


@pytest.mark.parametrize(
    ("raw_predicate", "value", "expected_predicate", "expected_dimension"),
    [
        ("lives_in", "上海", "profile.residence", "profile.residence"),
        ("works_as", "软件工程师", "profile.occupation", "profile.occupation"),
        (
            "phone_number",
            "+86 13800000000",
            "profile.contact_method",
            "profile.contact_method",
        ),
        ("date_of_birth", "1995-08-12", "profile.birthday", "profile.birthday"),
    ],
)
def test_registered_stable_fact_aliases_normalize_to_bounded_profile_dimensions(
    raw_predicate: str,
    value: str,
    expected_predicate: str,
    expected_dimension: str,
) -> None:
    text = f"记一下：{value}"

    normalized = normalize_memory_candidate_contract(
        _stable_claim(
            claim_id=raw_predicate,
            text=text,
            predicate=raw_predicate,
            value=value,
        ).to_candidate(),
        NOW,
    )

    assert normalized.kind == MemoryKind.STABLE_FACT
    assert normalized.predicate_type == PredicateType.CANONICAL
    assert normalized.canonical_predicate == expected_predicate
    assert normalized.custom_predicate is None
    assert normalized.state_dimension == expected_dimension
    assert normalized.state_value == value.casefold()
    assert normalized.payload["state_dimension"] == expected_dimension
    assert normalized.payload["state_value"] == value.casefold()


def test_stable_fact_registry_exposes_single_value_history_policy() -> None:
    expected_temporal = {
        "profile.residence": PredicateTemporalBehavior.STATE,
        "profile.occupation": PredicateTemporalBehavior.STATE,
        "profile.contact_method": PredicateTemporalBehavior.STATE,
        "profile.birthday": PredicateTemporalBehavior.TIMELESS,
    }

    for predicate, temporal_behavior in expected_temporal.items():
        spec = predicate_spec(predicate)

        assert spec is not None
        assert spec.state_dimension == predicate
        assert spec.cardinality == PredicateCardinality.SINGLE
        assert spec.temporal_behavior == temporal_behavior
        assert spec.update_policy == PredicateUpdatePolicy.REPLACE


def test_open_world_stable_fact_is_not_promoted_to_profile_policy() -> None:
    normalized = normalize_memory_candidate_contract(
        _stable_claim(
            claim_id="pet",
            text="记一下：我养了一只猫。",
            predicate="has_pet",
            value="猫",
        ).to_candidate(),
        NOW,
        allow_legacy_open_world=True,
    )

    assert normalized.predicate_type == PredicateType.CUSTOM
    assert normalized.canonical_predicate is None
    assert normalized.custom_predicate == "has_pet"
    assert normalized.state_dimension is None
    assert normalized.state_value is None


def test_profile_predicate_does_not_authorize_another_memory_kind() -> None:
    claim = _stable_claim(
        claim_id="wrong-kind",
        text="记一下：昨天在上海见面。",
        predicate="profile.residence",
        value="上海",
        canonical_predicate="profile.residence",
    ).model_copy(update={"kind": MemoryKind.INTERACTION_EVENT})

    normalized = normalize_memory_candidate_contract(
        claim.to_candidate(),
        NOW,
        allow_legacy_open_world=True,
    )

    assert normalized.predicate_type == PredicateType.CUSTOM
    assert normalized.canonical_predicate is None
    assert normalized.state_dimension is None


async def test_same_stable_fact_value_merges_as_same_without_duplicate() -> None:
    first_text = "记一下：我现在住在上海。"
    second_text = "记一下：我的现居地还是上海。"
    service_store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        service_store,
        SequenceExtractor(
            [
                _stable_claim(
                    claim_id="residence-first",
                    text=first_text,
                    predicate="lives_in",
                    value="上海",
                ),
                _stable_claim(
                    claim_id="residence-repeat",
                    text=second_text,
                    predicate="current_residence",
                    value="上海",
                ),
            ]
        ),
        clock=lambda: NOW,
    )
    scope = {
        "user_id": "stable-same-user",
        "relationship_id": "stable-same-relationship",
        "conversation_id": "stable-same-conversation",
        "status": MemoryStatus.CONFIRMED,
    }

    first = await service.remember_text(text=first_text, **scope)
    second = await service.remember_text(text=second_text, **scope)

    memories = await service_store.list_memories(
        user_id=scope["user_id"],
        relationship_id=scope["relationship_id"],
    )
    audits = await service_store.list_transition_audits(
        user_id=scope["user_id"],
        relationship_id=scope["relationship_id"],
        source_message_id=second.message.id,
    )
    assert len(memories) == 1
    assert second.saved[0].item.id == first.saved[0].item.id
    assert memories[0].status == MemoryStatus.CONFIRMED
    assert len(audits) == 1
    assert audits[0].relation == ClaimRelation.SAME
    assert audits[0].rule_name == "normalized_dedupe"


async def test_confirmed_stable_fact_replacement_keeps_superseded_history() -> None:
    first_text = "记一下：我现在住在上海。"
    second_text = "记一下：我现在已经搬到杭州居住。"
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(
            [
                _stable_claim(
                    claim_id="residence-shanghai",
                    text=first_text,
                    predicate="profile.residence",
                    canonical_predicate="profile.residence",
                    value="上海",
                ),
                _stable_claim(
                    claim_id="residence-hangzhou",
                    text=second_text,
                    predicate="resides_in",
                    value="杭州",
                ),
            ]
        ),
        clock=lambda: NOW,
    )
    scope = {
        "user_id": "stable-update-user",
        "relationship_id": "stable-update-relationship",
        "conversation_id": "stable-update-conversation",
        "status": MemoryStatus.CONFIRMED,
    }

    first = await service.remember_text(text=first_text, **scope)
    second = await service.remember_text(text=second_text, **scope)

    memories = await store.list_memories(
        user_id=scope["user_id"],
        relationship_id=scope["relationship_id"],
    )
    by_value = {item.state_value: item for item in memories}
    old = by_value["上海"]
    current = by_value["杭州"]
    audits = await store.list_transition_audits(
        user_id=scope["user_id"],
        relationship_id=scope["relationship_id"],
        source_message_id=second.message.id,
    )
    assert old.id == first.saved[0].item.id
    assert old.status == MemoryStatus.SUPERSEDED
    assert current.status == MemoryStatus.CONFIRMED
    assert current.supersedes_id == old.id
    assert len(audits) == 1
    assert audits[0].relation == ClaimRelation.UPDATE
    assert audits[0].target_memory_ids == [old.id]
    assert audits[0].rule_name == "canonical_single_value_update"


async def test_proposed_stable_fact_cannot_close_confirmed_value() -> None:
    first_text = "记一下：我现在住在上海。"
    second_text = "记一下：我可能已经搬到杭州了。"
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(
            [
                _stable_claim(
                    claim_id="confirmed-residence",
                    text=first_text,
                    predicate="lives_in",
                    value="上海",
                ),
                _stable_claim(
                    claim_id="proposed-residence",
                    text=second_text,
                    predicate="lives_in",
                    value="杭州",
                    confidence=0.7,
                ),
            ]
        ),
        clock=lambda: NOW,
    )
    common_scope = {
        "user_id": "stable-protection-user",
        "relationship_id": "stable-protection-relationship",
        "conversation_id": "stable-protection-conversation",
    }

    first = await service.remember_text(
        text=first_text,
        status=MemoryStatus.CONFIRMED,
        **common_scope,
    )
    second = await service.remember_text(text=second_text, **common_scope)

    confirmed = await store.get_memory(first.saved[0].item.id, common_scope["user_id"])
    assert confirmed is not None
    assert confirmed.status == MemoryStatus.CONFIRMED
    assert second.saved[0].item.status == MemoryStatus.PROPOSED
    assert second.saved[0].item.claim_relation == ClaimRelation.CONTRADICTION
    assert second.saved[0].item.supersedes_id is None


async def test_unrelated_stable_fact_dimensions_coexist() -> None:
    residence_text = "记一下：我现在住在上海。"
    occupation_text = "记一下：我的职业是软件工程师。"
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(
            [
                _stable_claim(
                    claim_id="residence",
                    text=residence_text,
                    predicate="location",
                    value="上海",
                ),
                _stable_claim(
                    claim_id="occupation",
                    text=occupation_text,
                    predicate="occupation",
                    value="软件工程师",
                ),
            ]
        ),
        clock=lambda: NOW,
    )
    scope = {
        "user_id": "stable-coexist-user",
        "relationship_id": "stable-coexist-relationship",
        "conversation_id": "stable-coexist-conversation",
        "status": MemoryStatus.CONFIRMED,
    }

    first = await service.remember_text(text=residence_text, **scope)
    second = await service.remember_text(text=occupation_text, **scope)

    memories = await store.list_memories(
        user_id=scope["user_id"],
        relationship_id=scope["relationship_id"],
    )
    assert {item.id for item in memories} == {
        first.saved[0].item.id,
        second.saved[0].item.id,
    }
    assert {item.canonical_predicate for item in memories} == {
        "profile.residence",
        "profile.occupation",
    }
    assert all(item.status == MemoryStatus.CONFIRMED for item in memories)
    relation = resolve_claim_relation(
        second.saved[0].item,
        [first.saved[0].item],
        incoming_status=MemoryStatus.CONFIRMED,
    )
    assert relation.relation == ClaimRelation.UNRELATED


def test_extraction_prompt_lists_only_the_bounded_stable_fact_profile_contract() -> None:
    for predicate in (
        "profile.residence",
        "profile.occupation",
        "profile.contact_method",
        "profile.birthday",
    ):
        assert predicate in _SYSTEM_PROMPT
    assert "其他开放事实不得猜测为 profile predicate" in _SYSTEM_PROMPT

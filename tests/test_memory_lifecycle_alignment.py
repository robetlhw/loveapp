from datetime import UTC, datetime

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.application.memory import MemoryService
from loveapp.application.memory_relations import resolve_claim_relation
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    ClaimRelation,
    EvidenceExplicitness,
    MemoryCandidate,
    MemoryKind,
    MemoryStatus,
    MessageRole,
    TimeKind,
)
from loveapp.domain.memory_lifecycle import governed_state_identity

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
SCOPE = {
    "user_id": "lifecycle-alignment-user",
    "relationship_id": "lifecycle-alignment-relationship",
    "conversation_id": "lifecycle-alignment-conversation",
}


class SequenceExtractor:
    def __init__(self, *extractions: AtomicExtraction) -> None:
        self._extractions = iter(extractions)

    async def extract(self, text: str, **kwargs) -> AtomicExtraction:
        del text, kwargs
        return next(self._extractions)


async def test_cold_war_repaired_uses_existing_conflict_lifecycle() -> None:
    first_text = "我和她现在还在冷战。"
    second_text = "昨天已经说开了，现在和好了。"
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(
            AtomicExtraction(claims=[_conflict_claim("active", first_text)]),
            AtomicExtraction(claims=[_conflict_claim("resolved", second_text)]),
        ),
        clock=lambda: NOW,
    )

    first = await service.remember_text(text=first_text, status=MemoryStatus.CONFIRMED, **SCOPE)
    second = await service.remember_text(
        text=second_text,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )

    old = await store.get_memory(first.saved[0].item.id, SCOPE["user_id"])
    current = second.saved[0].item
    audits = await store.list_transition_audits(
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
        source_message_id=second.message.id,
    )
    assert old is not None and old.status == MemoryStatus.SUPERSEDED
    assert current.status == MemoryStatus.CONFIRMED
    assert current.state_value == "resolved"
    assert current.supersedes_id == old.id
    assert audits[0].relation == ClaimRelation.UPDATE
    assert audits[0].rule_name == "resolve_active_conflict"


@pytest.mark.parametrize(
    ("first_value", "second_value"),
    [("active", "resolved"), ("resolved", "active")],
)
async def test_confirmed_conflict_state_replaces_same_dimension(
    first_value: str,
    second_value: str,
) -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(
            AtomicExtraction(claims=[_conflict_claim(first_value, f"冲突状态是{first_value}")]),
            AtomicExtraction(claims=[_conflict_claim(second_value, f"冲突状态是{second_value}")]),
        ),
        clock=lambda: NOW,
    )

    first = await service.remember_text(
        text=f"记一下：冲突状态是{first_value}",
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    second = await service.remember_text(
        text=f"记一下：冲突状态是{second_value}",
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )

    old = await store.get_memory(first.saved[0].item.id, SCOPE["user_id"])
    assert old is not None and old.status == MemoryStatus.SUPERSEDED
    assert second.saved[0].item.status == MemoryStatus.CONFIRMED
    assert second.saved[0].item.state_value == second_value
    assert second.saved[0].item.supersedes_id == old.id


@pytest.mark.parametrize(
    ("first_value", "second_value"),
    [("low", "normal"), ("normal", "low")],
)
async def test_confirmed_contact_metric_replaces_same_dimension(
    first_value: str,
    second_value: str,
) -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(
            AtomicExtraction(
                claims=[_contact_frequency_claim(first_value, f"联系频率是{first_value}")]
            ),
            AtomicExtraction(
                claims=[_contact_frequency_claim(second_value, f"联系频率是{second_value}")]
            ),
        ),
        clock=lambda: NOW,
    )

    first = await service.remember_text(
        text=f"记一下：联系频率是{first_value}",
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    second = await service.remember_text(
        text=f"记一下：联系频率是{second_value}",
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )

    old = await store.get_memory(first.saved[0].item.id, SCOPE["user_id"])
    current = second.saved[0].item
    assert old is not None and old.status == MemoryStatus.SUPERSEDED
    assert current.status == MemoryStatus.CONFIRMED
    assert current.state_dimension == "interaction.contact_frequency"
    assert current.state_value == second_value
    assert current.supersedes_id == old.id
    assert current.claim_relation == ClaimRelation.UPDATE


async def test_proposed_contact_state_cannot_overwrite_confirmed_state() -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    old = await store.save_memory(
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
        status=MemoryStatus.CONFIRMED,
        candidate=_contact_frequency_candidate("low", "联系频率较低"),
    )
    candidate = _contact_frequency_candidate("normal", "联系频率恢复正常")

    resolution = resolve_claim_relation(
        candidate,
        [old.item],
        incoming_status=MemoryStatus.PROPOSED,
    )

    assert resolution.relation == ClaimRelation.CONTRADICTION
    assert resolution.target_memory_ids == (old.item.id,)
    unchanged = await store.get_memory(old.item.id, SCOPE["user_id"])
    assert unchanged is not None and unchanged.status == MemoryStatus.CONFIRMED


def test_quantified_contact_frequency_is_not_a_governed_lifecycle_state() -> None:
    candidate = _contact_frequency_candidate(
        "1_2_times_per_day",
        "她现在一天只回我一两次",
    )

    assert governed_state_identity(candidate) is None


@pytest.mark.parametrize(
    "restored_kind",
    [MemoryKind.INTERACTION_PATTERN, MemoryKind.RELATIONSHIP_STATE],
)
async def test_contact_metric_reduction_closes_when_restoration_uses_either_representation(
    restored_kind: MemoryKind,
) -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    first_text = "她最近很少联系我"
    second_text = "联系已经恢复正常"
    restored_claim = (
        _contact_frequency_claim("normal", second_text)
        if restored_kind == MemoryKind.INTERACTION_PATTERN
        else _contact_status_claim("restored", second_text)
    )
    service = MemoryService(
        store,
        SequenceExtractor(
            AtomicExtraction(claims=[_contact_frequency_claim("low", first_text)]),
            AtomicExtraction(claims=[restored_claim]),
        ),
        clock=lambda: NOW,
    )

    first = await service.remember_text(
        text=f"记一下：{first_text}",
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    second = await service.remember_text(
        text=f"记一下：{second_text}",
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    old = await store.get_memory(first.saved[0].item.id, SCOPE["user_id"])
    audits = await store.list_transition_audits(
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
        source_message_id=second.message.id,
    )

    assert old is not None and old.status == MemoryStatus.SUPERSEDED
    assert second.saved[0].item.supersedes_id == old.id
    assert second.saved[0].item.claim_relation == ClaimRelation.UPDATE
    assert audits[0].rule_name == "restore_contact_frequency"


async def test_contact_status_reduction_closes_when_metric_is_restored() -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    first_text = "她最近很少联系我"
    second_text = "联系已经恢复正常"
    service = MemoryService(
        store,
        SequenceExtractor(
            AtomicExtraction(claims=[_contact_status_claim("reduced", first_text)]),
            AtomicExtraction(claims=[_contact_frequency_claim("normal", second_text)]),
        ),
        clock=lambda: NOW,
    )

    first = await service.remember_text(
        text=f"记一下：{first_text}",
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    second = await service.remember_text(
        text=f"记一下：{second_text}",
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )

    old = await store.get_memory(first.saved[0].item.id, SCOPE["user_id"])
    audits = await store.list_transition_audits(
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
        source_message_id=second.message.id,
    )

    assert old is not None and old.status == MemoryStatus.SUPERSEDED
    assert second.saved[0].item.supersedes_id == old.id
    assert second.saved[0].item.claim_relation == ClaimRelation.UPDATE
    assert audits[0].rule_name == "restore_contact_frequency"


async def test_conflict_repair_does_not_close_reduced_contact_frequency() -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    reduced = await store.save_memory(
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
        status=MemoryStatus.CONFIRMED,
        candidate=_contact_frequency_candidate("low", "她最近很少联系我"),
    )
    service = MemoryService(
        store,
        SequenceExtractor(AtomicExtraction(claims=[_conflict_claim("resolved", "我们已经和好了")])),
        clock=lambda: NOW,
    )

    await service.remember_text(
        text="记一下：我们已经和好了",
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )

    unchanged = await store.get_memory(reduced.item.id, SCOPE["user_id"])
    assert unchanged is not None and unchanged.status == MemoryStatus.CONFIRMED


async def test_proposed_cross_representation_restoration_keeps_confirmed_reduction() -> None:
    store = InMemoryMemoryStore(clock=lambda: NOW)
    reduced = await store.save_memory(
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
        status=MemoryStatus.CONFIRMED,
        candidate=_contact_frequency_candidate("low", "她最近很少联系我"),
    )
    candidate = _contact_status_candidate("restored", "联系已经恢复正常")

    resolution = resolve_claim_relation(
        candidate,
        [reduced.item],
        incoming_status=MemoryStatus.PROPOSED,
    )

    assert resolution.relation == ClaimRelation.CONTRADICTION
    assert resolution.target_memory_ids == (reduced.item.id,)


async def test_service_does_not_supersede_confirmed_reduction_with_proposed_restoration() -> None:
    current_text = "最近已经恢复正常了。"
    store = InMemoryMemoryStore(clock=lambda: NOW)
    reduced = await store.save_memory(
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
        status=MemoryStatus.CONFIRMED,
        candidate=_contact_frequency_candidate("low", "她最近很少联系我"),
    )
    service = MemoryService(
        store,
        SequenceExtractor(
            AtomicExtraction(claims=[_contact_status_claim("restored", current_text)])
        ),
        clock=lambda: NOW,
    )
    prior = await service.record_message(
        role=MessageRole.USER,
        content="她最近基本不怎么联系我。",
        **SCOPE,
    )
    current = await service.record_message(
        role=MessageRole.USER,
        content=current_text,
        **SCOPE,
    )

    result = await service.remember_recorded_message(
        message=current,
        text=current.content,
        conversation_history=[prior],
    )

    unchanged = await store.get_memory(reduced.item.id, SCOPE["user_id"])
    assert result.gate_decision is not None and result.gate_decision.should_extract
    assert unchanged is not None and unchanged.status == MemoryStatus.CONFIRMED
    assert result.saved[0].item.status == MemoryStatus.PROPOSED
    assert result.saved[0].item.claim_relation == ClaimRelation.CONTRADICTION
    assert result.saved[0].item.supersedes_id is None


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        (MemoryKind.RELATIONSHIP_STATE, "active"),
        (MemoryKind.RELATIONSHIP_STATE, "resolved"),
        (MemoryKind.INTERACTION_PATTERN, "normal"),
        (MemoryKind.INTERACTION_PATTERN, "low"),
    ],
)
async def test_repeated_current_state_merges_without_duplicate(
    kind: MemoryKind,
    value: str,
) -> None:
    first_evidence = f"第一次记录{value}"
    second_evidence = f"再次确认{value}"
    claims = (
        [_conflict_claim(value, first_evidence), _conflict_claim(value, second_evidence)]
        if kind == MemoryKind.RELATIONSHIP_STATE
        else [
            _contact_frequency_claim(value, first_evidence),
            _contact_frequency_claim(value, second_evidence),
        ]
    )
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(
            AtomicExtraction(claims=[claims[0]]),
            AtomicExtraction(claims=[claims[1]]),
        ),
        clock=lambda: NOW,
    )

    first = await service.remember_text(
        text=f"记一下：{first_evidence}",
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    second = await service.remember_text(
        text=f"记一下：{second_evidence}",
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    memories = await store.list_memories(
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
    )

    assert second.saved[0].created is False
    assert second.saved[0].item.id == first.saved[0].item.id
    assert second.saved[0].item.claim_relation == ClaimRelation.SAME
    assert len(memories) == 1


def _conflict_claim(value: str, evidence: str) -> AtomicClaim:
    return AtomicClaim(
        claim_id=f"conflict-{value}",
        kind=MemoryKind.RELATIONSHIP_STATE,
        subject="relationship",
        predicate="conflict_status",
        summary=f"当前冲突状态为 {value}",
        evidence_spans=[evidence],
        confidence=0.95,
        explicitness=EvidenceExplicitness.EXPLICIT,
        payload={"state_dimension": "conflict_status", "state_value": value},
    )


def _contact_frequency_claim(value: str, evidence: str) -> AtomicClaim:
    return AtomicClaim(
        claim_id=f"contact-frequency-{value}",
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="relationship",
        predicate="contact_frequency_changed",
        summary=f"当前联系频率为 {value}",
        evidence_spans=[evidence],
        confidence=0.95,
        explicitness=EvidenceExplicitness.EXPLICIT,
        payload={"metric": "contact_frequency", "current": value},
    )


def _contact_frequency_candidate(value: str, text: str) -> MemoryCandidate:
    return MemoryCandidate(
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="relationship",
        summary=text,
        original_text=text,
        evidence_spans=[text],
        time_kind=TimeKind.TIMELESS,
        confidence=0.95,
        raw_predicate="contact_frequency_changed",
        canonical_predicate="interaction.contact_frequency",
        state_dimension="interaction.contact_frequency",
        state_value=value,
        payload={
            "predicate": "contact_frequency_changed",
            "metric": "contact_frequency",
            "current": value,
        },
    )


def _contact_status_claim(value: str, evidence: str) -> AtomicClaim:
    return AtomicClaim(
        claim_id=f"contact-status-{value}",
        kind=MemoryKind.RELATIONSHIP_STATE,
        subject="relationship",
        predicate="contact.status",
        summary=f"当前联系状态为 {value}",
        evidence_spans=[evidence],
        confidence=0.95,
        explicitness=EvidenceExplicitness.EXPLICIT,
        payload={
            "state_dimension": "relationship.contact_status",
            "state_value": value,
        },
    )


def _contact_status_candidate(value: str, text: str) -> MemoryCandidate:
    return MemoryCandidate(
        kind=MemoryKind.RELATIONSHIP_STATE,
        subject="relationship",
        summary=text,
        original_text=text,
        evidence_spans=[text],
        time_kind=TimeKind.TIMELESS,
        confidence=0.95,
        raw_predicate="contact.status",
        canonical_predicate="contact.status",
        state_dimension="relationship.contact_status",
        state_value=value,
        payload={
            "predicate": "contact.status",
            "state_dimension": "relationship.contact_status",
            "state_value": value,
        },
    )

import json
from datetime import UTC, datetime

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.application.memory import MemoryService
from loveapp.application.memory_admission import assess_memory_admission
from loveapp.application.memory_repair import MemoryResponseError, parse_memory_response
from loveapp.domain.memory import (
    AdmissionDecision,
    AtomicClaim,
    AtomicExtraction,
    ClaimRelation,
    EvidenceExplicitness,
    MemoryKind,
    MemoryStatus,
)
from loveapp.domain.memory_lifecycle import memory_concept, normalize_memory_candidate

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
SCOPE = {
    "user_id": "live-remediation-user",
    "relationship_id": "live-remediation-relationship",
    "conversation_id": "live-remediation-conversation",
}


class SequenceExtractor:
    def __init__(self, *extractions: AtomicExtraction) -> None:
        self._extractions = iter(extractions)

    async def extract(self, text: str, **kwargs: object) -> AtomicExtraction:
        del text, kwargs
        return next(self._extractions)


async def test_explicit_restoration_closes_surface_contact_outage_family() -> None:
    outage_text = "她已经三天没有回我消息了，我也联系不上她。"
    restored_text = "她今天终于回复我了，我们又开始正常聊天了。"
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(
            AtomicExtraction(
                claims=[
                    _contact_frequency_claim(outage_text, "no_response_3_days"),
                    _contact_opportunity_claim(outage_text, "low"),
                ]
            ),
            AtomicExtraction(
                claims=[
                    _contact_restored_claim(restored_text),
                    _contact_opportunity_claim(restored_text, "moderate"),
                ]
            ),
        ),
        clock=lambda: NOW,
    )

    first = await service.remember_text(
        text=outage_text,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    second = await service.remember_text(
        text=restored_text,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )

    old = [
        await store.get_memory(saved.item.id, SCOPE["user_id"])
        for saved in first.saved
    ]
    assert {memory_concept(item) for item in old if item is not None} == {
        "contact_unavailable"
    }
    assert all(item is not None and item.status == MemoryStatus.SUPERSEDED for item in old)
    restored = next(
        saved.item
        for saved in second.saved
        if saved.item.canonical_predicate == "contact.status"
    )
    opportunity = next(
        saved.item
        for saved in second.saved
        if saved.item.canonical_predicate == "relationship.contact_opportunity"
    )
    assert restored.state_value == "restored"
    assert restored.claim_relation == ClaimRelation.UPDATE
    assert restored.supersedes_id in {
        item.id for item in old if item is not None
    }
    assert opportunity.state_value == "moderate"
    assert opportunity.claim_relation == ClaimRelation.UPDATE

    context = await service.get_context(SCOPE["user_id"], SCOPE["relationship_id"])
    active_ids = {item.id for item in context.remembered_items}
    assert active_ids.isdisjoint({item.id for item in old if item is not None})
    assert {restored.id, opportunity.id}.issubset(active_ids)


async def test_proposed_restoration_cannot_close_confirmed_surface_outage() -> None:
    outage_text = "她已经三天没有回复我，我完全联系不上她。"
    restored_text = "她今天终于回复我了。"
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(
            AtomicExtraction(
                claims=[_contact_frequency_claim(outage_text, "no_reply_for_3_days")]
            ),
            AtomicExtraction(
                claims=[
                    _contact_restored_claim(restored_text).model_copy(
                        update={"confidence": 0.75}
                    )
                ]
            ),
        ),
        clock=lambda: NOW,
    )

    first = await service.remember_text(
        text=outage_text,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    result = await service.remember_text(
        text=restored_text,
        status=MemoryStatus.PROPOSED,
        **SCOPE,
    )

    old = await store.get_memory(first.saved[0].item.id, SCOPE["user_id"])
    assert old is not None
    assert old.status == MemoryStatus.CONFIRMED
    assert result.saved[0].item.status == MemoryStatus.PROPOSED
    assert result.saved[0].item.supersedes_id is None


async def test_low_offline_opportunity_is_not_a_contact_outage() -> None:
    opportunity_text = "摄影社暂停线下活动，近期见面机会很少。"
    restored_text = "她今天终于回复我了，线上恢复正常聊天。"
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(
            AtomicExtraction(
                claims=[_contact_opportunity_claim(opportunity_text, "low")]
            ),
            AtomicExtraction(claims=[_contact_restored_claim(restored_text)]),
        ),
        clock=lambda: NOW,
    )

    first = await service.remember_text(
        text=opportunity_text,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    await service.remember_text(
        text=restored_text,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )

    opportunity = await store.get_memory(first.saved[0].item.id, SCOPE["user_id"])
    assert opportunity is not None
    assert memory_concept(opportunity).startswith("state:")
    assert opportunity.status == MemoryStatus.CONFIRMED


async def test_outage_words_in_another_claim_do_not_close_offline_opportunity() -> None:
    outage_span = "她已经三天没有回我消息了。"
    offline_span = "摄影社暂停活动，最近线下见面机会很少。"
    first_text = f"{outage_span}{offline_span}"
    restored_text = "她今天终于回复我了，我们又开始正常聊天了。"
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(
            AtomicExtraction(
                claims=[
                    _contact_frequency_claim(outage_span, "no_response_3_days"),
                    _contact_opportunity_claim(offline_span, "low"),
                ]
            ),
            AtomicExtraction(claims=[_contact_restored_claim(restored_text)]),
        ),
        clock=lambda: NOW,
    )

    first = await service.remember_text(
        text=first_text,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    await service.remember_text(
        text=restored_text,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )

    outage, opportunity = [
        await store.get_memory(saved.item.id, SCOPE["user_id"])
        for saved in first.saved
    ]
    assert outage is not None and outage.status == MemoryStatus.SUPERSEDED
    assert opportunity is not None and opportunity.status == MemoryStatus.CONFIRMED
    assert memory_concept(opportunity).startswith("state:")


@pytest.mark.parametrize("outage_value", ["no_response_3_days", "none"])
async def test_response_restoration_closes_cross_representation_outage(
    outage_value: str,
) -> None:
    outage_text = "她已经好几天没有回我消息了。"
    restored_text = "她今天回复正常，我们已经恢复聊天了。"
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(
            AtomicExtraction(
                claims=[_contact_frequency_claim(outage_text, outage_value)]
            ),
            AtomicExtraction(
                claims=[_response_engagement_claim(restored_text, "responsive")]
            ),
        ),
        clock=lambda: NOW,
    )

    first = await service.remember_text(
        text=outage_text,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    second = await service.remember_text(
        text=restored_text,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )

    old = await store.get_memory(first.saved[0].item.id, SCOPE["user_id"])
    assert old is not None and old.status == MemoryStatus.SUPERSEDED
    assert second.saved[0].item.claim_relation == ClaimRelation.UPDATE
    assert second.saved[0].item.supersedes_id == old.id


async def test_friend_and_family_integration_are_custom_and_coexist() -> None:
    friend_text = "她现在愿意带我认识她的朋友。"
    family_text = "但她暂时还不愿意让我去见她父母。"
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(
            AtomicExtraction(claims=[_familiarity_claim("friend", friend_text)]),
            AtomicExtraction(claims=[_familiarity_claim("family", family_text)]),
        ),
        clock=lambda: NOW,
    )

    first = await service.remember_text(
        text=friend_text,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    second = await service.remember_text(
        text=family_text,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )

    assert first.saved[0].item.custom_predicate == "social_circle_integration"
    assert second.saved[0].item.custom_predicate == "family_integration"
    assert first.saved[0].item.payload["object"] == "introduction_included"
    assert second.saved[0].item.payload["object"] == "restricted"
    assert second.saved[0].item.claim_relation == ClaimRelation.UNCERTAIN
    active = await store.list_memories(
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
    )
    assert len(active) == 2
    assert {item.canonical_predicate for item in active} == {None}
    assert {item.custom_predicate for item in active} == {
        "family_integration",
        "social_circle_integration",
    }


async def test_opposite_social_integration_stances_do_not_dedupe() -> None:
    included_text = "她最近经常邀请我参加她朋友的聚会。"
    restricted_text = "但最近她几乎不再让我参加她朋友的活动。"
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(
            AtomicExtraction(
                claims=[_familiarity_claim("social-included", included_text)]
            ),
            AtomicExtraction(
                claims=[_familiarity_claim("social-restricted", restricted_text)]
            ),
        ),
        clock=lambda: NOW,
    )

    first = await service.remember_text(
        text=included_text,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )
    second = await service.remember_text(
        text=restricted_text,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )

    included = first.saved[0].item
    restricted = second.saved[0].item
    assert included.id != restricted.id
    assert included.custom_predicate == "social_circle_integration"
    assert restricted.custom_predicate == "social_circle_integration"
    assert included.payload["object"] == "participation_included"
    assert restricted.payload["object"] == "participation_restricted"
    assert restricted.claim_relation == ClaimRelation.UNCERTAIN
    active = await store.list_memories(
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
    )
    assert {item.id for item in active} == {included.id, restricted.id}


async def test_low_confidence_direct_social_integration_is_admitted() -> None:
    text = "她现在愿意带我认识她的朋友。"
    claim = _familiarity_claim("low-confidence-direct", text).model_copy(
        update={
            "confidence": 0.7,
            "explicitness": EvidenceExplicitness.STRONGLY_IMPLIED,
            "requires_inference": True,
        }
    )
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(AtomicExtraction(claims=[claim])),
        clock=lambda: NOW,
    )

    result = await service.remember_text(
        text=text,
        status=MemoryStatus.CONFIRMED,
        **SCOPE,
    )

    assert result.rejected_by_policy == 0
    assert len(result.saved) == 1
    saved = result.saved[0].item
    assert saved.confidence == 0.7
    assert saved.explicitness == EvidenceExplicitness.EXPLICIT
    assert saved.requires_inference is False
    assert saved.admission_score == pytest.approx(0.7)


def test_speculative_social_integration_is_not_promoted() -> None:
    text = "她可能愿意带我认识她的朋友。"
    candidate = _familiarity_claim("speculative", text).model_copy(
        update={
            "confidence": 0.7,
            "explicitness": EvidenceExplicitness.STRONGLY_IMPLIED,
            "requires_inference": True,
        }
    ).to_candidate()

    normalized = normalize_memory_candidate(candidate, NOW)
    assessment = assess_memory_admission(normalized, text)

    assert normalized.custom_predicate == "social_circle_integration"
    assert normalized.payload["object"] == "introduction_included"
    assert normalized.confidence == 0.7
    assert normalized.explicitness == EvidenceExplicitness.STRONGLY_IMPLIED
    assert normalized.requires_inference is True
    assert assessment.decision == AdmissionDecision.REJECT


def test_explicit_familiarity_language_remains_canonical() -> None:
    text = "我们已经很熟了，她也愿意把我介绍给朋友。"
    normalized = normalize_memory_candidate(
        _familiarity_claim("explicit", text).to_candidate(),
        NOW,
    )

    assert normalized.canonical_predicate == "relationship.familiarity"
    assert normalized.state_value == "moderate"


def test_direct_custom_family_integration_passes_extraction_governance() -> None:
    text = "但她暂时还不愿意让我去见她父母。"
    content = json.dumps(
        {
            "claims": [
                {
                    "claim_id": "family-open-world",
                    "kind": "relationship_state",
                    "subject": "relationship",
                    "predicate": "family_integration",
                    "predicate_type": "custom",
                    "custom_predicate": "family_integration",
                    "summary": "对方暂时不愿意让用户见父母",
                    "evidence_spans": [text.rstrip("。")],
                    "confidence": 0.9,
                    "explicitness": "explicit",
                }
            ],
            "discarded_spans": [],
        },
        ensure_ascii=False,
    )

    parsed = parse_memory_response(content, source_text=text)
    normalized = normalize_memory_candidate(
        parsed.extraction.claims[0].to_candidate(),
        NOW,
    )

    assert normalized.predicate_type.value == "custom"
    assert normalized.canonical_predicate is None
    assert normalized.custom_predicate == "family_integration"
    assert normalized.state_dimension is None
    assert normalized.state_value is None
    assert normalized.payload["object"] == "restricted"


def test_social_integration_is_not_canonicalized_as_initiation_balance() -> None:
    text = "最近一个月，她经常邀请我参加她朋友的聚会。"
    candidate = AtomicClaim(
        claim_id="social-initiation-overreach",
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="relationship",
        predicate="initiation_balance",
        summary="对方经常邀请用户参加朋友聚会",
        evidence_spans=[text.rstrip("。")],
        confidence=0.9,
        explicitness=EvidenceExplicitness.EXPLICIT,
        payload={
            "metric": "initiation_balance",
            "current": "partner_to_user",
            "frequency": "frequent",
        },
    ).to_candidate()

    normalized = normalize_memory_candidate(candidate, NOW)

    assert normalized.canonical_predicate is None
    assert normalized.custom_predicate == "social_circle_integration"
    assert normalized.state_dimension is None
    assert normalized.state_value is None
    assert "metric" not in normalized.payload
    assert normalized.payload["frequency"] == "frequent"


def test_social_integration_is_not_canonicalized_as_contact_frequency() -> None:
    text = "最近一个月，她经常邀请我参加她朋友的聚会。"
    candidate = _contact_frequency_claim(text, "high").to_candidate()

    normalized = normalize_memory_candidate(candidate, NOW)

    assert normalized.canonical_predicate is None
    assert normalized.custom_predicate == "social_circle_integration"
    assert normalized.state_dimension is None
    assert normalized.state_value is None
    assert normalized.payload["object"] == "participation_included"


def test_unregistered_non_social_relationship_state_remains_rejected() -> None:
    text = "我们目前的关系状态有一些变化。"
    content = json.dumps(
        {
            "claims": [
                {
                    "claim_id": "unknown-state",
                    "kind": "relationship_state",
                    "subject": "relationship",
                    "predicate": "unknown_relationship_state",
                    "predicate_type": "custom",
                    "custom_predicate": "unknown_relationship_state",
                    "summary": "双方当前关系状态发生变化",
                    "evidence_spans": [text.rstrip("。")],
                    "confidence": 0.9,
                    "explicitness": "explicit",
                }
            ],
            "discarded_spans": [],
        },
        ensure_ascii=False,
    )

    with pytest.raises(MemoryResponseError, match="state_dimension/state_value"):
        parse_memory_response(content, source_text=text)


def _contact_frequency_claim(text: str, value: str) -> AtomicClaim:
    return AtomicClaim(
        claim_id=f"contact-frequency-{value}",
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="relationship",
        predicate="contact_frequency",
        summary=text.rstrip("。"),
        evidence_spans=[text.rstrip("。")],
        confidence=0.95,
        explicitness=EvidenceExplicitness.EXPLICIT,
        payload={"metric": "contact_frequency", "current": value},
    )


def _contact_opportunity_claim(text: str, value: str) -> AtomicClaim:
    return AtomicClaim(
        claim_id=f"contact-opportunity-{value}",
        kind=MemoryKind.RELATIONSHIP_STATE,
        subject="relationship",
        predicate="contact_opportunity",
        summary=text.rstrip("。"),
        evidence_spans=[text.rstrip("。")],
        confidence=0.95,
        explicitness=EvidenceExplicitness.EXPLICIT,
        payload={"state_dimension": "contact_opportunity", "state_value": value},
    )


def _contact_restored_claim(text: str) -> AtomicClaim:
    return AtomicClaim(
        claim_id="contact-restored",
        kind=MemoryKind.INTERACTION_EVENT,
        subject="relationship",
        predicate="resumed_contact",
        summary=text.rstrip("。"),
        evidence_spans=[text.rstrip("。")],
        confidence=0.95,
        explicitness=EvidenceExplicitness.EXPLICIT,
    )


def _response_engagement_claim(text: str, value: str) -> AtomicClaim:
    return AtomicClaim(
        claim_id=f"response-engagement-{value}",
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="relationship",
        predicate="response_engagement",
        summary=text.rstrip("。"),
        evidence_spans=[text.rstrip("。")],
        confidence=0.95,
        explicitness=EvidenceExplicitness.EXPLICIT,
        payload={"metric": "response_engagement", "current": value},
    )


def _familiarity_claim(claim_id: str, text: str) -> AtomicClaim:
    return AtomicClaim(
        claim_id=f"familiarity-{claim_id}",
        kind=MemoryKind.RELATIONSHIP_STATE,
        subject="relationship",
        predicate="familiarity",
        summary=text.rstrip("。"),
        evidence_spans=[text.rstrip("。")],
        confidence=0.9,
        explicitness=EvidenceExplicitness.EXPLICIT,
        payload={
            "state_dimension": "relationship_familiarity",
            "state_value": "moderate",
        },
    )

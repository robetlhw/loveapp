from datetime import UTC, datetime

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.application.memory import MemoryService
from loveapp.application.memory_admission import (
    assess_governed_transition_eligibility,
    assess_memory_admission,
)
from loveapp.domain.memory import (
    AdmissionDecision,
    AtomicClaim,
    AtomicExtraction,
    ClaimRelation,
    EvidenceExplicitness,
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    PredicateType,
    TimeKind,
    memory_dedupe_key,
)

NOW = datetime(2026, 8, 31, 12, tzinfo=UTC)
OLD_START = datetime(2026, 7, 1, tzinfo=UTC)
OLD_END = datetime(2026, 8, 10, tzinfo=UTC)
NEW_START = datetime(2026, 8, 11, tzinfo=UTC)
NEW_END = datetime(2026, 8, 31, tzinfo=UTC)
SCOPE = {
    "user_id": "canonical-transition-user",
    "relationship_id": "canonical-transition-relationship",
    "conversation_id": "canonical-transition-conversation",
}


class SequenceExtractor:
    def __init__(self, *extractions: AtomicExtraction) -> None:
        self._extractions = iter(extractions)

    async def extract(self, text: str, **kwargs) -> AtomicExtraction:
        del text, kwargs
        return next(self._extractions)


async def test_cg_001_default_admission_confirms_initiation_transition() -> None:
    first_text = "最近一个月她经常主动找我聊天。"
    second_text = "最近两个星期基本都是我先联系她，她已经很少主动找我了。"
    first_claim = _claim(
        "initiation-old",
        "interaction.initiation_balance",
        "partner_to_user",
        first_text,
        period_start=OLD_START,
        period_end=OLD_END,
    )
    second_claim = _claim(
        "initiation-new",
        "interaction.initiation_balance",
        "user_to_partner",
        second_text,
        period_start=NEW_START,
        period_end=NEW_END,
    )
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(
            AtomicExtraction(claims=[first_claim]),
            AtomicExtraction(claims=[second_claim]),
        ),
        clock=lambda: NOW,
    )

    first = await service.remember_text(text=first_text, **SCOPE)
    second = await service.remember_text(text=second_text, **SCOPE)

    old = await store.get_memory(first.saved[0].item.id, SCOPE["user_id"])
    current = second.saved[0].item
    audits = await store.list_transition_audits(
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
        source_message_id=second.message.id,
    )
    assert old is not None and old.status == MemoryStatus.SUPERSEDED
    assert current.status == MemoryStatus.CONFIRMED
    assert current.admission_decision == AdmissionDecision.CONFIRM
    assert current.claim_relation == ClaimRelation.UPDATE
    assert current.supersedes_id == old.id
    assert audits[0].rule_name == "replace_state:interaction.initiation_balance"
    assert audits[0].score_breakdown["governed_transition_candidate"] is True
    assert audits[0].score_breakdown["governed_transition_reason"] == (
        "eligible_governed_transition"
    )


async def test_cg_002_default_admission_confirms_emotional_disclosure_transition() -> None:
    first_text = "她最近经常跟我分享工作和生活里的烦心事。"
    second_text = "最近一个月她基本不再跟我讲这些私人情绪了。"
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(
            AtomicExtraction(
                claims=[
                    _claim(
                        "disclosure-old",
                        "interaction.emotional_disclosure",
                        "high",
                        first_text,
                        period_start=None,
                        period_end=None,
                    )
                ]
            ),
            AtomicExtraction(
                claims=[
                    _claim(
                        "disclosure-new",
                        "interaction.emotional_disclosure",
                        "low",
                        second_text,
                        period_start=NEW_START,
                        period_end=NEW_END,
                    )
                ]
            ),
        ),
        clock=lambda: NOW,
    )

    first = await service.remember_text(text=first_text, **SCOPE)
    second = await service.remember_text(text=second_text, **SCOPE)

    old = await store.get_memory(first.saved[0].item.id, SCOPE["user_id"])
    current = second.saved[0].item
    assert old is not None and old.status == MemoryStatus.SUPERSEDED
    assert current.status == MemoryStatus.CONFIRMED
    assert current.admission_decision == AdmissionDecision.CONFIRM
    assert current.claim_relation == ClaimRelation.UPDATE
    assert current.supersedes_id == old.id
    assert current.state_dimension == "interaction.emotional_disclosure"
    assert current.state_value == "low"


async def test_cg_003_weak_belief_cannot_replace_confirmed_canonical_state() -> None:
    first_text = "她明确说最近经常愿意和我分享自己的情绪。"
    second_text = "请记住：我感觉她可能已经不愿意跟我说这些了。"
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(
            AtomicExtraction(
                claims=[
                    _claim(
                        "belief-old",
                        "interaction.emotional_disclosure",
                        "high",
                        first_text,
                        period_start=OLD_START,
                        period_end=OLD_END,
                    )
                ]
            ),
            AtomicExtraction(
                claims=[
                    _claim(
                        "belief-new",
                        "interaction.emotional_disclosure",
                        "low",
                        second_text,
                        period_start=NEW_START,
                        period_end=NEW_END,
                        confidence=0.98,
                        explicitness=EvidenceExplicitness.WEAKLY_INFERRED,
                        perspective=MemoryPerspective.USER_BELIEF,
                        requires_inference=True,
                    )
                ]
            ),
        ),
        clock=lambda: NOW,
    )

    first = await service.remember_text(text=first_text, **SCOPE)
    second = await service.remember_text(text=second_text, **SCOPE)

    old = await store.get_memory(first.saved[0].item.id, SCOPE["user_id"])
    assert old is not None and old.status == MemoryStatus.CONFIRMED
    assert second.saved[0].item.status == MemoryStatus.PROPOSED
    assert second.saved[0].item.claim_relation == ClaimRelation.CONTRADICTION
    assert second.saved[0].item.supersedes_id is None


async def test_cg_004_historical_state_cannot_replace_current_state() -> None:
    first_text = "最近一个月她经常主动找我聊天。"
    second_text = "其实去年她几乎从来不会主动联系我。"
    historical_start = datetime(2025, 1, 1, tzinfo=UTC)
    historical_end = datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC)
    store = InMemoryMemoryStore(clock=lambda: NOW)
    service = MemoryService(
        store,
        SequenceExtractor(
            AtomicExtraction(
                claims=[
                    _claim(
                        "current",
                        "interaction.initiation_balance",
                        "partner_to_user",
                        first_text,
                        period_start=OLD_START,
                        period_end=NEW_END,
                    )
                ]
            ),
            AtomicExtraction(
                claims=[
                    _claim(
                        "historical",
                        "interaction.initiation_balance",
                        "user_to_partner",
                        second_text,
                        period_start=historical_start,
                        period_end=historical_end,
                        confidence=0.95,
                    )
                ]
            ),
        ),
        clock=lambda: NOW,
    )

    first = await service.remember_text(text=first_text, **SCOPE)
    second = await service.remember_text(text=second_text, **SCOPE)

    old = await store.get_memory(first.saved[0].item.id, SCOPE["user_id"])
    assert old is not None and old.status == MemoryStatus.CONFIRMED
    assert second.saved[0].item.status == MemoryStatus.PROPOSED
    assert second.saved[0].item.claim_relation != ClaimRelation.UPDATE
    assert second.saved[0].item.supersedes_id is None


def test_valid_governed_transition_bypasses_only_generic_conflict_downgrade() -> None:
    text = "最近两个星期基本都是我先联系她，她已经很少主动找我了。"
    candidate = _candidate(
        "interaction.initiation_balance",
        "user_to_partner",
        text,
        period_start=NEW_START,
        period_end=NEW_END,
    )
    target = _item(
        _candidate(
            "interaction.initiation_balance",
            "partner_to_user",
            "她此前经常主动找我",
            period_start=OLD_START,
            period_end=OLD_END,
        )
    )

    eligibility = assess_governed_transition_eligibility(candidate, text, [target])
    assessment = assess_memory_admission(
        candidate,
        text,
        conflict=True,
        governed_transition_eligibility=eligibility,
    )

    assert eligibility.eligible is True
    assert eligibility.target_memory_id == target.id
    assert assessment.decision == AdmissionDecision.CONFIRM
    assert assessment.reason == "confirmed_governed_transition"


@pytest.mark.parametrize(
    ("candidate_updates", "targets_factory", "reason"),
    [
        (
            {"evidence_spans": ["这段证据不在原文里"]},
            lambda target: [target],
            "evidence_not_in_source",
        ),
        (
            {"requires_inference": True},
            lambda target: [target],
            "requires_inference",
        ),
        (
            {"confidence": 0.89},
            lambda target: [target],
            "below_transition_confidence",
        ),
        (
            {"perspective": MemoryPerspective.USER_BELIEF},
            lambda target: [target],
            "nonreported_perspective",
        ),
        (
            {"period_start": None, "period_end": None},
            lambda target: [target],
            "missing_transition_temporal_evidence",
        ),
        (
            {},
            lambda target: [target, target.model_copy(update={"id": "second-target"})],
            "ambiguous_governed_targets",
        ),
    ],
)
def test_governed_transition_eligibility_fails_closed(
    candidate_updates: dict[str, object],
    targets_factory,
    reason: str,
) -> None:
    text = "最近两个星期基本都是我先联系她，她已经很少主动找我了。"
    candidate = _candidate(
        "interaction.initiation_balance",
        "user_to_partner",
        text,
        period_start=NEW_START,
        period_end=NEW_END,
    ).model_copy(update=candidate_updates)
    target = _item(
        _candidate(
            "interaction.initiation_balance",
            "partner_to_user",
            "她此前经常主动找我",
            period_start=OLD_START,
            period_end=OLD_END,
        )
    )

    eligibility = assess_governed_transition_eligibility(
        candidate,
        text,
        targets_factory(target),
    )
    assessment = assess_memory_admission(
        candidate,
        text,
        conflict=True,
        governed_transition_eligibility=eligibility,
    )

    assert eligibility.eligible is False
    assert eligibility.reason == reason
    assert assessment.decision != AdmissionDecision.CONFIRM


def test_governed_transition_requires_subject_match_and_forward_time() -> None:
    text = "其实去年她几乎从来不会主动联系我。"
    historical = _candidate(
        "interaction.initiation_balance",
        "user_to_partner",
        text,
        period_start=datetime(2025, 1, 1, tzinfo=UTC),
        period_end=datetime(2025, 12, 31, tzinfo=UTC),
    )
    target = _item(
        _candidate(
            "interaction.initiation_balance",
            "partner_to_user",
            "最近她经常主动联系我",
            period_start=OLD_START,
            period_end=NEW_END,
        )
    )

    historical_result = assess_governed_transition_eligibility(
        historical,
        text,
        [target],
    )
    subject_result = assess_governed_transition_eligibility(
        historical.model_copy(update={"subject": "partner"}),
        text,
        [target],
    )

    assert historical_result.eligible is False
    assert historical_result.reason == "historical_transition"
    assert subject_result.eligible is False
    assert subject_result.reason == "no_unique_governed_target"


def test_current_anchored_transition_allows_unique_target_with_missing_time() -> None:
    text = "最近一个月她基本不再跟我讲这些私人情绪了。"
    candidate = _candidate(
        "interaction.emotional_disclosure",
        "low",
        text,
        period_start=NEW_START,
        period_end=NEW_END,
    )
    target = _item(
        _candidate(
            "interaction.emotional_disclosure",
            "high",
            "她最近经常跟我分享烦心事",
            period_start=None,
            period_end=None,
        )
    )

    eligibility = assess_governed_transition_eligibility(candidate, text, [target])

    assert eligibility.eligible is True
    assert eligibility.reason == "eligible_governed_transition"


def test_historical_transition_cannot_use_missing_target_time_fallback() -> None:
    text = "其实去年她基本不再跟我讲这些私人情绪了。"
    candidate = _candidate(
        "interaction.emotional_disclosure",
        "low",
        text,
        period_start=datetime(2025, 1, 1, tzinfo=UTC),
        period_end=datetime(2025, 12, 31, tzinfo=UTC),
    )
    target = _item(
        _candidate(
            "interaction.emotional_disclosure",
            "high",
            "她最近经常跟我分享烦心事",
            period_start=None,
            period_end=None,
        )
    )

    eligibility = assess_governed_transition_eligibility(candidate, text, [target])

    assert eligibility.eligible is False
    assert eligibility.reason == "historical_transition"


def _claim(
    claim_id: str,
    dimension: str,
    value: str,
    text: str,
    *,
    period_start: datetime | None,
    period_end: datetime | None,
    confidence: float = 0.9,
    explicitness: EvidenceExplicitness = EvidenceExplicitness.EXPLICIT,
    perspective: MemoryPerspective = MemoryPerspective.USER_REPORTED,
    requires_inference: bool = False,
) -> AtomicClaim:
    metric = dimension.removeprefix("interaction.")
    return AtomicClaim(
        claim_id=claim_id,
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="relationship",
        predicate=dimension,
        summary=text.rstrip("。"),
        evidence_spans=[text.rstrip("。")],
        time_kind=TimeKind.INTERVAL,
        period_start=period_start,
        period_end=period_end,
        confidence=confidence,
        perspective=perspective,
        explicitness=explicitness,
        requires_inference=requires_inference,
        payload={"metric": metric, "current": value},
        raw_predicate=dimension,
        predicate_type=PredicateType.CANONICAL,
        canonical_predicate=dimension,
        state_dimension=dimension,
        state_value=value,
    )


def _candidate(
    dimension: str,
    value: str,
    text: str,
    *,
    period_start: datetime | None,
    period_end: datetime | None,
) -> MemoryCandidate:
    metric = dimension.removeprefix("interaction.")
    return MemoryCandidate(
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="relationship",
        summary=text,
        original_text=text,
        evidence_spans=[text],
        time_kind=TimeKind.INTERVAL,
        period_start=period_start,
        period_end=period_end,
        confidence=0.9,
        explicitness=EvidenceExplicitness.EXPLICIT,
        perspective=MemoryPerspective.USER_REPORTED,
        requires_inference=False,
        payload={"metric": metric, "current": value},
        raw_predicate=dimension,
        predicate_type=PredicateType.CANONICAL,
        canonical_predicate=dimension,
        state_dimension=dimension,
        state_value=value,
    )


def _item(candidate: MemoryCandidate) -> MemoryItem:
    return MemoryItem(
        **candidate.model_dump(),
        id="governed-target",
        user_id=SCOPE["user_id"],
        relationship_id=SCOPE["relationship_id"],
        status=MemoryStatus.CONFIRMED,
        source_message_id="governed-source",
        created_at=NOW,
        updated_at=NOW,
        dedupe_key=memory_dedupe_key(candidate),
    )

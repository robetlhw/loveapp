from datetime import UTC, datetime

from loveapp.application.memory_repair import MemoryResponseError
from loveapp.application.memory_upgrade import assess_memory_upgrade
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    MemoryValence,
    MessageRole,
    RelationshipImpact,
    StoredMessage,
    TimeKind,
)


def test_plain_json_syntax_error_never_upgrades() -> None:
    decision = assess_memory_upgrade(
        "最近两周我们联系明显变少了。",
        existing_memories=[],
        conversation_history=[],
        failure=MemoryResponseError("bad json", category="json_syntax"),
    )

    assert decision.should_upgrade is False
    assert decision.reason is None


def test_unsupported_enum_never_upgrades() -> None:
    decision = assess_memory_upgrade(
        "最近两周我们联系明显变少了。",
        existing_memories=[],
        conversation_history=[],
        failure=MemoryResponseError("bad enum", category="unsupported_enum"),
    )

    assert decision.should_upgrade is False


def test_missing_plan_time_never_upgrades_even_with_important_keyword() -> None:
    decision = assess_memory_upgrade(
        "我以后可能和她聊聊消费观。",
        existing_memories=[],
        conversation_history=[],
        failure=MemoryResponseError(
            "计划事件缺少明确的未来时间",
            category="missing_temporal_anchor",
        ),
    )

    assert decision.should_upgrade is False
    assert decision.reason is None


def test_valid_low_confidence_temporal_claim_upgrades() -> None:
    extraction = AtomicExtraction(
        claims=[
            AtomicClaim(
                claim_id="trend",
                kind=MemoryKind.INTERACTION_PATTERN,
                subject="relationship",
                predicate="contact_frequency_changed",
                summary="最近两周联系频率发生变化",
                evidence_spans=["最近两周我们联系明显变少了"],
                confidence=0.4,
                payload={"metric": "contact_frequency", "direction": "decreasing"},
            )
        ]
    )

    decision = assess_memory_upgrade(
        "最近两周我们联系明显变少了。",
        existing_memories=[],
        conversation_history=[],
        extraction=extraction,
    )

    assert decision.should_upgrade is True
    assert decision.reason == "low_confidence_important"


def test_partial_valid_extraction_stays_on_flash_path() -> None:
    extraction = AtomicExtraction(
        claims=[
            AtomicClaim(
                claim_id="valid-claim",
                kind=MemoryKind.INTERACTION_PATTERN,
                subject="relationship",
                predicate="contact_frequency_changed",
                summary="最近联系频率发生变化",
                evidence_spans=["最近两周我们联系明显变少了"],
                confidence=0.9,
                payload={"metric": "contact_frequency", "direction": "decreasing"},
            )
        ]
    )

    decision = assess_memory_upgrade(
        "最近两周我们联系明显变少了。",
        existing_memories=[],
        conversation_history=[],
        extraction=extraction,
        partial=True,
    )

    assert decision.should_upgrade is False
    assert "partial_claim_validation" in decision.signals


def test_opposite_existing_pattern_is_detected_as_conflict() -> None:
    existing = _memory_item(
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="relationship",
        predicate="contact_frequency_changed",
        summary="最近联系变多",
        payload={"metric": "contact_frequency", "direction": "increasing"},
    )
    extraction = AtomicExtraction(
        claims=[
            AtomicClaim(
                claim_id="new-trend",
                kind=MemoryKind.INTERACTION_PATTERN,
                subject="relationship",
                predicate="contact_frequency_changed",
                summary="最近联系变少",
                evidence_spans=["最近联系变少了"],
                payload={"metric": "contact_frequency", "direction": "decreasing"},
            )
        ]
    )

    decision = assess_memory_upgrade(
        "最近联系变少了。",
        existing_memories=[existing],
        conversation_history=[],
        extraction=extraction,
    )

    assert decision.should_upgrade is True
    assert decision.reason == "existing_memory_conflict"
    assert "existing_memory_conflict" in decision.signals


def test_correction_without_old_context_does_not_force_upgrade() -> None:
    extraction = AtomicExtraction(
        claims=[
            AtomicClaim(
                claim_id="correction",
                kind=MemoryKind.PREFERENCE,
                subject="partner",
                predicate="likes_food",
                summary="对方喜欢粤菜",
                evidence_spans=["真正喜欢的是粤菜"],
                payload={"preference": "粤菜", "preference_type": "like"},
            )
        ]
    )

    decision = assess_memory_upgrade(
        "之前是我听错了，真正喜欢的是粤菜。",
        existing_memories=[],
        conversation_history=[],
        extraction=extraction,
    )

    assert decision.should_upgrade is False


def test_single_person_pronoun_does_not_trigger_ambiguous_reference_upgrade() -> None:
    history = [
        StoredMessage(
            id="history-1",
            conversation_id="c1",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.USER,
            content="我最近和她接触得不错",
        ),
        StoredMessage(
            id="history-2",
            conversation_id="c1",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.USER,
            content="她有时会主动找我聊天",
        ),
    ]

    decision = assess_memory_upgrade(
        "我准备向她表白，你有啥建议帮助吗",
        existing_memories=[],
        conversation_history=history,
        extraction=AtomicExtraction(),
    )

    assert "ambiguous_reference" not in decision.signals


def test_temporal_connector_does_not_count_as_ambiguous_reference() -> None:
    history = [
        StoredMessage(
            id="history-temporal-1",
            conversation_id="c1",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.USER,
            content="我和她刚认识时不太熟",
        ),
        StoredMessage(
            id="history-temporal-2",
            conversation_id="c1",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.USER,
            content="她后来开始主动和我聊天",
        ),
    ]
    extraction = AtomicExtraction(
        claims=[
            AtomicClaim(
                claim_id="familiarity-change",
                kind=MemoryKind.RELATIONSHIP_STATE,
                subject="relationship",
                predicate="relationship_familiarity",
                summary="双方逐渐熟悉了一些",
                evidence_spans=["后来我们熟了一些"],
                confidence=0.9,
                payload={
                    "state_dimension": "relationship_familiarity",
                    "state_value": "moderate",
                },
            )
        ]
    )

    decision = assess_memory_upgrade(
        "后来我们熟了一些。",
        existing_memories=[],
        conversation_history=history,
        extraction=extraction,
    )

    assert decision.should_upgrade is False
    assert "ambiguous_reference" not in decision.signals


def test_demonstrative_with_one_person_does_not_force_upgrade() -> None:
    history = [
        StoredMessage(
            id="history-topic-1",
            conversation_id="c1",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.USER,
            content="她喜欢看悬疑电影",
        ),
        StoredMessage(
            id="history-topic-2",
            conversation_id="c1",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.USER,
            content="她还给我推荐了两本推理小说",
        ),
    ]
    extraction = AtomicExtraction(
        claims=[
            AtomicClaim(
                claim_id="topic-preference",
                kind=MemoryKind.PREFERENCE,
                subject="user",
                predicate="likes",
                summary="用户也喜欢悬疑推理题材",
                evidence_spans=["我对这个题材也很感兴趣"],
                confidence=0.9,
                payload={
                    "preference": "悬疑推理题材",
                    "preference_type": "like",
                },
            )
        ]
    )

    decision = assess_memory_upgrade(
        "我对这个题材也很感兴趣。",
        existing_memories=[],
        conversation_history=history,
        extraction=extraction,
    )

    assert decision.should_upgrade is False
    assert "ambiguous_reference" not in decision.signals


def test_complex_demonstrative_only_upgrades_an_uncertain_extraction() -> None:
    history = [
        StoredMessage(
            id="history-people-1",
            conversation_id="c1",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.USER,
            content="她最近和一个男生经常聊天",
        ),
        StoredMessage(
            id="history-people-2",
            conversation_id="c1",
            user_id="u1",
            relationship_id="r1",
            role=MessageRole.USER,
            content="那个男生好像也在追她",
        ),
    ]

    uncertain = assess_memory_upgrade(
        "那个人后来又主动联系我。",
        existing_memories=[],
        conversation_history=history,
        extraction=AtomicExtraction(),
    )
    confident = assess_memory_upgrade(
        "那个人后来又主动联系我。",
        existing_memories=[],
        conversation_history=history,
        extraction=AtomicExtraction(
            claims=[
                AtomicClaim(
                    claim_id="contact-event",
                    kind=MemoryKind.INTERACTION_EVENT,
                    subject="relationship",
                    predicate="contacted_user",
                    summary="相关人物后来主动联系了用户",
                    evidence_spans=["那个人后来又主动联系我"],
                    confidence=0.9,
                )
            ]
        ),
    )

    assert uncertain.should_upgrade is True
    assert uncertain.reason == "ambiguous_reference"
    assert confident.should_upgrade is False
    assert "ambiguous_reference" in confident.signals


def test_missing_competitor_claim_coverage_triggers_upgrade() -> None:
    extraction = AtomicExtraction(
        claims=[
            AtomicClaim(
                claim_id="comparison",
                kind=MemoryKind.STABLE_FACT,
                subject="user",
                predicate="believes_other_superior",
                summary="用户觉得那个男生比自己优秀",
                evidence_spans=["他比我优秀"],
                perspective=MemoryPerspective.USER_BELIEF,
            )
        ]
    )
    text = (
        "听说她最近和一个男生经常一起聊天，感觉那个男孩子也在追求她，"
        "他比我优秀，你觉得我希望大吗"
    )

    decision = assess_memory_upgrade(
        text,
        existing_memories=[],
        conversation_history=[],
        extraction=extraction,
    )

    assert decision.should_upgrade is True
    assert decision.reason == "claim_coverage_gap"
    assert "claim_coverage_gap" in decision.signals


def test_ordinary_registered_dimension_gap_is_observable_without_upgrade() -> None:
    text = "我们刚认识还不太熟，但每周都有机会见面。"
    extraction = AtomicExtraction(
        claims=[
            AtomicClaim(
                claim_id="familiarity-only",
                kind=MemoryKind.RELATIONSHIP_STATE,
                subject="relationship",
                predicate="relationship_state",
                summary="双方目前熟悉度较低",
                evidence_spans=["还不太熟"],
                payload={
                    "state_dimension": "relationship_familiarity",
                    "state_value": "low",
                },
            )
        ]
    )

    decision = assess_memory_upgrade(
        text,
        existing_memories=[],
        conversation_history=[],
        extraction=extraction,
    )

    assert decision.should_upgrade is False
    assert decision.reason is None
    assert "claim_coverage_gap" in decision.signals


def test_ordinary_multi_dimension_atomicity_failure_stays_on_flash_path() -> None:
    decision = assess_memory_upgrade(
        "我们还不太熟，平时也很少有机会见面。",
        existing_memories=[],
        conversation_history=[],
        failure=MemoryResponseError(
            "一个声明包含多个记忆维度",
            category="atomicity_validation",
        ),
    )

    assert decision.should_upgrade is False
    assert decision.reason is None
    assert "multi_dimension_atomicity_failure" in decision.signals


def test_high_value_multi_dimension_atomicity_failure_can_use_strong_model() -> None:
    decision = assess_memory_upgrade(
        "她明确拒绝再联系，我们也不熟，平时只在微信聊天。",
        existing_memories=[],
        conversation_history=[],
        failure=MemoryResponseError(
            "一个声明包含多个记忆维度",
            category="atomicity_validation",
        ),
    )

    assert decision.should_upgrade is True
    assert decision.reason == "semantic_uncertainty"
    assert "boundary_or_rejection" in decision.signals
    assert "multi_dimension_atomicity_failure" in decision.signals


def _memory_item(
    *,
    kind: MemoryKind,
    subject: str,
    predicate: str,
    summary: str,
    payload: dict[str, str],
) -> MemoryItem:
    now = datetime(2026, 7, 18, tzinfo=UTC)
    return MemoryItem(
        id="existing-trend",
        user_id="upgrade-test-user",
        relationship_id="partner-1",
        status=MemoryStatus.PROPOSED,
        dedupe_key="existing-key",
        kind=kind,
        subject=subject,
        summary=summary,
        original_text=summary,
        evidence_spans=[summary],
        time_kind=TimeKind.INTERVAL,
        valence=MemoryValence.NEUTRAL,
        relationship_impact=RelationshipImpact.UNCLEAR,
        perspective=MemoryPerspective.USER_REPORTED,
        confidence=0.9,
        payload={"predicate": predicate, **payload},
        created_at=now,
        updated_at=now,
    )

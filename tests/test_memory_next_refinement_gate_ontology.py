from datetime import UTC, datetime

import pytest

from loveapp.application.memory_gate import MemoryGate
from loveapp.domain.memory import (
    MemoryCandidate,
    MemoryGateRoute,
    MemoryKind,
    MemoryPerspective,
    MessageRole,
    PredicateType,
    StoredMessage,
    TimeKind,
)
from loveapp.domain.memory_dimensions import (
    normalize_interaction_source,
    validate_interaction_event_payload,
)
from loveapp.domain.memory_normalization import (
    NormalizationContractError,
    normalize_memory_candidate_contract,
)


def test_possible_memory_alias_is_readable_without_changing_legacy_value() -> None:
    assert MemoryGateRoute.POSSIBLE_MEMORY == MemoryGateRoute.SEMANTIC_REVIEW
    assert MemoryGateRoute("POSSIBLE_MEMORY") == MemoryGateRoute.POSSIBLE_MEMORY
    assert MemoryGateRoute.POSSIBLE_MEMORY.value == "SEMANTIC_REVIEW"


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("刚认识她的时候，她其实比较主动，经常找我聊天。", "historical_interaction_pattern"),
        ("最近她越来越少主动联系我", "relationship_trend"),
        ("最近我们的矛盾越来越多。", "relationship_trend"),
        ("我发现自己更喜欢安静的相处方式", "emotional_reflection"),
        ("我和她现在还是暧昧关系。", "relationship_current_state"),
    ],
)
def test_route_v2_marks_long_tail_signals_as_possible_memory(
    text: str,
    category: str,
) -> None:
    decision = MemoryGate().route_v2(text)

    assert decision.l0_route == MemoryGateRoute.POSSIBLE_MEMORY
    assert decision.route_label == "POSSIBLE_MEMORY"
    assert decision.should_extract is True
    assert decision.durable_signal_category == category
    assert decision.matched_rule is not None
    assert decision.matched_span


@pytest.mark.parametrize(
    "text",
    [
        "我的生日是5月3日",
        "我喜欢安静的相处方式",
    ],
)
def test_route_v2_hard_passes_explicit_user_fact_and_preference(text: str) -> None:
    decision = MemoryGate().route_v2(text)

    assert decision.l0_route == MemoryGateRoute.HARD_PASS
    assert decision.route_label == "HARD_PASS"
    assert decision.should_extract is True


def test_route_v2_hard_passes_first_person_residence_fact() -> None:
    decision = MemoryGate().route_v2("我现在住上海")

    assert decision.l0_route == MemoryGateRoute.HARD_PASS
    assert decision.route_label == "HARD_PASS"


@pytest.mark.parametrize(
    "text",
    [
        "今天好累",
        "今天有点累",
        "天气不错",
        "哈哈确实",
        "好吧",
        "那就这样",
        "今天吃什么？",
        "我一天喝两杯水",
        "最近工作越来越忙",
    ],
)
def test_route_v2_hard_drops_clear_transient_or_unrelated_inputs(text: str) -> None:
    decision = MemoryGate().route_v2(text)

    assert decision.l0_route == MemoryGateRoute.HARD_DROP
    assert decision.should_extract is False


def test_route_v2_preserves_history_derived_contextual_signal_metadata() -> None:
    history = [
        StoredMessage(
            id="history-1",
            conversation_id="conversation-1",
            user_id="user-1",
            relationship_id="relationship-1",
            role=MessageRole.USER,
            content="她最近很少主动联系我",
            created_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
    ]

    decision = MemoryGate().route_v2(
        "现在大概一天一两次。",
        conversation_history=history,
    )

    assert decision.route_label == "POSSIBLE_MEMORY"
    assert decision.should_extract is True
    assert decision.contextual_probe is True
    assert decision.contextual_signal_category == "frequency"
    assert "contextual_history_derived" in decision.signals


def _pattern(*, source: str, perspective: MemoryPerspective) -> MemoryCandidate:
    return MemoryCandidate(
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="relationship",
        summary="近期联系频率较高",
        original_text="最近一周我们经常联系",
        evidence_spans=["最近一周我们经常联系"],
        time_kind=TimeKind.INTERVAL,
        period_start=datetime(2026, 8, 25, tzinfo=UTC),
        period_end=datetime(2026, 9, 1, tzinfo=UTC),
        perspective=perspective,
        payload={
            "metric": "contact_frequency",
            "current": "high",
            "source": source,
            "evidence_ids": ["event-1", "event-2"],
            "time_window": {"label": "最近一周"},
        },
        raw_predicate="interaction.contact_frequency",
        predicate_type=PredicateType.CANONICAL,
        canonical_predicate="interaction.contact_frequency",
        state_dimension="interaction.contact_frequency",
        state_value="high",
        confidence=0.9,
    )


def test_pattern_source_accepts_derived_from_events_and_preserves_it() -> None:
    candidate = _pattern(
        source="derived_from_events",
        perspective=MemoryPerspective.MODEL_INFERRED,
    )

    normalized = normalize_memory_candidate_contract(
        candidate,
        datetime(2026, 9, 1, tzinfo=UTC),
        allow_legacy_open_world=True,
    )

    assert normalize_interaction_source("derived_from_events") == "derived_from_events"
    assert normalized.payload["source"] == "derived_from_events"


def test_legacy_model_inferred_pattern_source_still_round_trips() -> None:
    candidate = _pattern(
        source="model_inferred",
        perspective=MemoryPerspective.MODEL_INFERRED,
    )

    normalized = normalize_memory_candidate_contract(
        candidate,
        datetime(2026, 9, 1, tzinfo=UTC),
        allow_legacy_open_world=True,
    )

    assert normalized.payload["source"] == "model_inferred"


def test_derived_pattern_source_requires_inferred_perspective() -> None:
    candidate = _pattern(
        source="derived_from_events",
        perspective=MemoryPerspective.USER_REPORTED,
    )

    with pytest.raises(
        NormalizationContractError,
        match="INTERACTION_PATTERN_PAYLOAD_INVALID",
    ):
        normalize_memory_candidate_contract(
            candidate,
            datetime(2026, 9, 1, tzinfo=UTC),
            allow_legacy_open_world=True,
        )


def test_event_source_does_not_accept_pattern_only_provenance() -> None:
    with pytest.raises(ValueError, match="cannot be derived_from_events"):
        validate_interaction_event_payload(
            {"action": "chat", "source": "derived_from_events"},
            perspective=MemoryPerspective.MODEL_INFERRED,
        )

import asyncio
import json
from datetime import UTC, datetime
from time import perf_counter
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from loveapp.adapters.memory.openai_compatible import (
    OpenAICompatibleMemoryExtractor,
    TieredMemoryExtractor,
    _validate_extraction,
)
from loveapp.application.memory_repair import MemoryResponseError, parse_memory_response
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    MemoryKind,
    MemoryPerspective,
    PredicateType,
    TemporalPrecision,
    TimeKind,
)
from loveapp.evaluation.baseline import _memory_trace_summary


def _stage_claim(*, claim_id: str, evidence: str, value: str) -> dict[str, object]:
    return {
        "claim_id": claim_id,
        "kind": "relationship_state",
        "subject": "relationship",
        "predicate": "relationship.stage",
        "predicate_type": "canonical",
        "canonical_predicate": "relationship.stage",
        "summary": "双方关系阶段发生变化",
        "evidence_spans": [evidence],
        "payload": {
            "state_dimension": "relationship.stage",
            "state_value": value,
        },
    }


def test_atomic_claim_keeps_normalized_summary_and_exact_evidence() -> None:
    text = "起初我们只在群里互动，最近连续四天都会单独交流。"
    claim = AtomicClaim(
        claim_id="engagement-pattern",
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="relationship",
        predicate="conversation_engagement_changed",
        object="partner",
        summary="双方互动从群聊变化为最近连续四天单独交流",
        evidence_spans=["起初我们只在群里互动", "最近连续四天都会单独交流"],
        payload={
            "metric": "conversation_channel",
            "baseline": "group_chat",
            "current": "private_chat",
            "direction": "improving",
        },
    )
    extraction = AtomicExtraction(claims=[claim])

    _validate_extraction(extraction, text)
    candidate = claim.to_candidate()

    assert candidate.summary == "双方互动从群聊变化为最近连续四天单独交流"
    assert candidate.evidence_spans == [
        "起初我们只在群里互动",
        "最近连续四天都会单独交流",
    ]
    assert candidate.payload["predicate"] == "conversation_engagement_changed"
    assert candidate.payload["object"] == "partner"


@pytest.mark.parametrize(
    ("text", "raw_claim", "predicate", "dimension", "value"),
    [
        (
            "我和她现在还只是普通朋友，还没有正式在一起。",
            {
                "claim_id": "relationship-stage",
                "kind": "RELATIONSHIP_STATE",
                "subject": "relationship",
                "predicate": "relationship_stage",
                "predicate_type": "CANONICAL",
                "canonical_predicate": "relationship.stage",
                "summary": "双方目前还是普通朋友",
                "evidence_spans": ["现在还只是普通朋友"],
                "payload": {
                    "state_dimension": "relationship_stage",
                    "state_value": "friends",
                },
            },
            "relationship.stage",
            "relationship.stage",
            "acquaintance",
        ),
        (
            "昨天我们确认关系了，现在正式在一起了。",
            {
                "claim_id": "relationship-confirmed",
                "kind": "relationship_state",
                "subject": "relationship",
                "predicate": "relationship.stage",
                "predicate_type": "canonical",
                "canonical_predicate": "relationship.stage",
                "summary": "双方已经确认恋爱关系",
                "evidence_spans": ["我们确认关系了，现在正式在一起了"],
                "payload": {
                    "state_dimension": "relationship_stage",
                    "state_value": "partnered",
                },
            },
            "relationship.stage",
            "relationship.stage",
            "dating",
        ),
        (
            "我昨天已经跟她表白了。",
            {
                "claim_id": "confession-executed",
                "kind": "RELATIONSHIP_STATE",
                "subject": "relationship",
                "predicate": "confession_executed",
                "summary": "用户已经向对方表白",
                "evidence_spans": ["昨天已经跟她表白了"],
                "payload": {
                    "state_dimension": "confession_status",
                    "state_value": "confessed_pending_response",
                },
            },
            "confession.status",
            "relationship.confession_status",
            "executed",
        ),
    ],
)
def test_parser_accepts_registered_canonical_states_before_candidate_normalization(
    text: str,
    raw_claim: dict,
    predicate: str,
    dimension: str,
    value: str,
) -> None:
    parsed = parse_memory_response(
        json.dumps({"claims": [raw_claim], "discarded_spans": []}, ensure_ascii=False),
        source_text=text,
    )

    claim = parsed.extraction.claims[0]
    assert claim.predicate_type == PredicateType.CANONICAL
    assert claim.canonical_predicate == predicate
    assert claim.payload["state_dimension"] == dimension
    assert claim.payload["state_value"] == value
    assert "canonical_state_alignment" in parsed.repair_steps


@pytest.mark.parametrize(
    "raw_claim",
    [
        {
            "claim_id": "missing-dimension",
            "kind": "relationship_state",
            "subject": "relationship",
            "predicate": "relationship_stage",
            "summary": "双方已经确认恋爱关系",
            "evidence_spans": ["昨天我们确认关系了，现在正式在一起了"],
            "payload": {"state_value": "dating"},
        },
        {
            "claim_id": "missing-value",
            "kind": "relationship_state",
            "subject": "relationship",
            "predicate": "relationship.stage",
            "predicate_type": "canonical",
            "canonical_predicate": "relationship.stage",
            "summary": "双方已经确认恋爱关系",
            "evidence_spans": ["昨天我们确认关系了，现在正式在一起了"],
            "payload": {"state_dimension": "relationship.stage"},
        },
        {
            "claim_id": "missing-canonical-shape",
            "kind": "relationship_state",
            "subject": "relationship",
            "predicate": "relationship_stage",
            "summary": "双方已经确认恋爱关系",
            "evidence_spans": ["昨天我们确认关系了，现在正式在一起了"],
            "payload": {},
        },
    ],
)
def test_relationship_stage_bounded_repair_recovers_explicit_dating_shape(
    raw_claim: dict,
) -> None:
    text = "昨天我们确认关系了，现在正式在一起了。"

    parsed = parse_memory_response(
        json.dumps({"claims": [raw_claim], "discarded_spans": []}, ensure_ascii=False),
        source_text=text,
    )

    claim = parsed.extraction.claims[0]
    assert claim.canonical_predicate == "relationship.stage"
    assert claim.state_dimension == "relationship.stage"
    assert claim.state_value == "dating"
    assert claim.payload["state_dimension"] == "relationship.stage"
    assert claim.payload["state_value"] == "dating"
    assert "relationship_stage_shape_repair" in parsed.repair_steps


def test_parser_restores_missing_canonical_field_from_exact_registered_predicate() -> None:
    text = "她喜欢日料。"
    raw_claim = {
        "claim_id": "exact-canonical-predicate",
        "kind": "preference",
        "subject": "partner",
        "predicate": "preference.food.cuisine",
        "predicate_type": "canonical",
        "summary": "对方喜欢日料",
        "evidence_spans": ["她喜欢日料"],
        "payload": {"preference": "日料", "preference_type": "like"},
    }

    parsed = parse_memory_response(
        json.dumps({"claims": [raw_claim], "discarded_spans": []}, ensure_ascii=False),
        source_text=text,
    )

    assert parsed.extraction.claims[0].canonical_predicate == "preference.food.cuisine"
    assert "exact_canonical_predicate_alignment" in parsed.repair_steps


def test_exact_canonical_alignment_rejects_incompatible_kind_and_evidence() -> None:
    text = "她喜欢日料。"
    raw_claim = {
        "claim_id": "wrong-kind-stage",
        "kind": "stable_fact",
        "subject": "relationship",
        "predicate": "relationship.stage",
        "predicate_type": "canonical",
        "summary": "双方处于恋爱关系",
        "evidence_spans": ["她喜欢日料"],
        "payload": {"state_value": "dating"},
    }

    with pytest.raises(MemoryResponseError):
        parse_memory_response(
            json.dumps(
                {"claims": [raw_claim], "discarded_spans": []},
                ensure_ascii=False,
            ),
            source_text=text,
        )


def test_relationship_stage_repair_accepts_explicit_current_together_statement() -> None:
    text = "她答应我了，我们在一起了。"
    raw_claim = {
        "claim_id": "accepted-and-dating",
        "kind": "relationship_state",
        "subject": "relationship",
        "predicate": "relationship_status",
        "summary": "双方已经开始交往",
        "evidence_spans": ["她答应我了，我们在一起了"],
        "payload": {},
    }

    parsed = parse_memory_response(
        json.dumps({"claims": [raw_claim], "discarded_spans": []}, ensure_ascii=False),
        source_text=text,
    )

    claim = parsed.extraction.claims[0]
    assert claim.canonical_predicate == "relationship.stage"
    assert claim.state_value == "dating"
    assert "relationship_stage_shape_repair" in parsed.repair_steps


def test_relationship_stage_semantics_downgrade_new_relationship_from_committed() -> None:
    text = "昨天我们确认关系了，现在正式在一起了。"
    raw_claim = _stage_claim(
        claim_id="overstated-stage",
        evidence="昨天我们确认关系了，现在正式在一起了",
        value="committed",
    )

    parsed = parse_memory_response(
        json.dumps({"claims": [raw_claim], "discarded_spans": []}, ensure_ascii=False),
        source_text=text,
    )

    claim = parsed.extraction.claims[0]
    assert claim.state_value == "dating"
    assert claim.payload["state_value"] == "dating"
    assert "relationship_stage_semantic_normalization" in parsed.repair_steps


def test_relationship_stage_semantics_preserve_explicit_long_term_commitment() -> None:
    text = "我们已经交往多年，也明确做了长期共同规划。"
    raw_claim = _stage_claim(
        claim_id="committed-stage",
        evidence="我们已经交往多年，也明确做了长期共同规划",
        value="committed",
    )

    parsed = parse_memory_response(
        json.dumps({"claims": [raw_claim], "discarded_spans": []}, ensure_ascii=False),
        source_text=text,
    )

    assert parsed.extraction.claims[0].state_value == "committed"
    assert "relationship_stage_semantic_normalization" not in parsed.repair_steps


def test_relationship_stage_semantics_do_not_upgrade_vague_stability() -> None:
    text = "感觉我们的关系最近更稳定了。"
    raw_claim = _stage_claim(
        claim_id="vague-stage",
        evidence="感觉我们的关系最近更稳定了",
        value="committed",
    )

    with pytest.raises(MemoryResponseError) as exc_info:
        parse_memory_response(
            json.dumps(
                {"claims": [raw_claim], "discarded_spans": []},
                ensure_ascii=False,
            ),
            source_text=text,
        )

    assert exc_info.value.category == "schema_validation"
    assert "relationship_stage_fail_closed" in exc_info.value.repair_steps
    assert exc_info.value.details["repair_result"] == "unresolved"


@pytest.mark.parametrize(
    "text,evidence",
    [
        ("如果以后我们正式在一起就好了。", "如果以后我们正式在一起就好了"),
        ("我们可能会在一起。", "我们可能会在一起"),
        ("我希望能和她正式在一起。", "我希望能和她正式在一起"),
        ("我们并没有确认关系。", "我们并没有确认关系"),
    ],
)
def test_relationship_stage_semantics_fail_closed_for_non_factual_dating_cues(
    text: str,
    evidence: str,
) -> None:
    raw_claim = _stage_claim(
        claim_id="unsupported-dating-stage",
        evidence=evidence,
        value="dating",
    )

    with pytest.raises(MemoryResponseError) as exc_info:
        parse_memory_response(
            json.dumps(
                {"claims": [raw_claim], "discarded_spans": []},
                ensure_ascii=False,
            ),
            source_text=text,
        )

    assert "relationship_stage_fail_closed" in exc_info.value.repair_steps


@pytest.mark.parametrize(
    "text,evidence",
    [
        ("她拒绝和我正式在一起。", "正式在一起"),
        ("我想和她正式在一起。", "正式在一起"),
    ],
)
def test_relationship_stage_authorization_uses_full_source_not_trimmed_evidence(
    text: str,
    evidence: str,
) -> None:
    raw_claim = _stage_claim(
        claim_id="misleading-trimmed-evidence",
        evidence=evidence,
        value="dating",
    )

    with pytest.raises(MemoryResponseError) as exc_info:
        parse_memory_response(
            json.dumps(
                {"claims": [raw_claim], "discarded_spans": []},
                ensure_ascii=False,
            ),
            source_text=text,
        )

    assert "relationship_stage_fail_closed" in exc_info.value.repair_steps


def test_full_source_negation_overrides_trimmed_positive_evidence() -> None:
    text = "我和她现在还只是普通朋友，还没有正式在一起。"
    raw_claim = _stage_claim(
        claim_id="trimmed-negative-stage",
        evidence="正式在一起",
        value="dating",
    )

    parsed = parse_memory_response(
        json.dumps({"claims": [raw_claim], "discarded_spans": []}, ensure_ascii=False),
        source_text=text,
    )

    assert parsed.extraction.claims[0].state_value == "acquaintance"


def test_current_dating_clause_wins_over_prior_negated_stage() -> None:
    text = "之前我们还没有正式在一起，昨天已经确认关系了。"
    raw_claim = _stage_claim(
        claim_id="current-stage-after-history",
        evidence="昨天已经确认关系了",
        value="dating",
    )

    parsed = parse_memory_response(
        json.dumps({"claims": [raw_claim], "discarded_spans": []}, ensure_ascii=False),
        source_text=text,
    )

    assert parsed.extraction.claims[0].state_value == "dating"


def test_reordered_historical_dating_does_not_override_current_breakup() -> None:
    text = "我们已经分手了，以前正式在一起过。"
    raw_claim = _stage_claim(
        claim_id="historical-stage-after-breakup",
        evidence="正式在一起",
        value="dating",
    )

    parsed = parse_memory_response(
        json.dumps({"claims": [raw_claim], "discarded_spans": []}, ensure_ascii=False),
        source_text=text,
    )

    assert parsed.extraction.claims[0].state_value == "separated"
    assert "relationship_stage_semantic_normalization" in parsed.repair_steps


def test_conflict_repair_cannot_be_stored_as_relationship_stage_reconciled() -> None:
    text = "我们现在和好了。"
    raw_claim = _stage_claim(
        claim_id="conflict-repair-as-stage",
        evidence="现在和好了",
        value="reconciled",
    )

    with pytest.raises(MemoryResponseError) as exc_info:
        parse_memory_response(
            json.dumps(
                {"claims": [raw_claim], "discarded_spans": []},
                ensure_ascii=False,
            ),
            source_text=text,
        )

    assert "relationship_stage_fail_closed" in exc_info.value.repair_steps


@pytest.mark.parametrize(
    "text,declared_value",
    [
        ("我们没有分手。", "separated"),
        ("如果以后我们分手了。", "separated"),
        ("我们以前分手过。", "separated"),
        ("我希望以后我们能复合。", "reconciled"),
        ("我们没有复合。", "reconciled"),
        ("我们以前复合过。", "reconciled"),
        ("如果以后我们已经结婚了。", "committed"),
    ],
)
def test_relationship_stage_rejects_non_current_or_unrelated_evidence(
    text: str,
    declared_value: str,
) -> None:
    raw_claim = _stage_claim(
        claim_id="unsupported-current-stage",
        evidence=text.rstrip("。"),
        value=declared_value,
    )

    with pytest.raises(MemoryResponseError) as exc_info:
        parse_memory_response(
            json.dumps({"claims": [raw_claim], "discarded_spans": []}, ensure_ascii=False),
            source_text=text,
        )

    assert "relationship_stage_fail_closed" in exc_info.value.repair_steps


@pytest.mark.parametrize(
    "text,declared_value,expected_value",
    [
        ("我们现在已经分手了。", "separated", "separated"),
        ("我们昨天复合了。", "reconciled", "reconciled"),
        ("我们已经结婚了。", "committed", "committed"),
        ("我们曾经已经结婚，后来离婚了。", "committed", "separated"),
    ],
)
def test_relationship_stage_accepts_only_explicit_current_transition_evidence(
    text: str,
    declared_value: str,
    expected_value: str,
) -> None:
    raw_claim = _stage_claim(
        claim_id="explicit-current-stage",
        evidence=text.rstrip("。"),
        value=declared_value,
    )

    parsed = parse_memory_response(
        json.dumps({"claims": [raw_claim], "discarded_spans": []}, ensure_ascii=False),
        source_text=text,
    )

    assert parsed.extraction.claims[0].state_value == expected_value


@pytest.mark.parametrize(
    "declared_value",
    ["acquaintance", "dating", "separated", "reconciled"],
)
def test_social_integration_evidence_cannot_authorize_relationship_stage(
    declared_value: str,
) -> None:
    text = "她愿意带我认识她的朋友。"
    raw_claim = _stage_claim(
        claim_id="overcanonicalized-social-stage",
        evidence="她愿意带我认识她的朋友",
        value=declared_value,
    )

    parsed = parse_memory_response(
        json.dumps({"claims": [raw_claim], "discarded_spans": []}, ensure_ascii=False),
        source_text=text,
    )

    claim = parsed.extraction.claims[0]
    assert claim.state_value is None
    assert claim.payload.get("state_value") is None
    assert "relationship_stage_fail_closed" in parsed.repair_steps


def test_negated_formal_relationship_normalizes_to_acquaintance() -> None:
    text = "我和她现在还只是普通朋友，还没有正式在一起。"
    raw_claim = _stage_claim(
        claim_id="negated-dating-stage",
        evidence="现在还只是普通朋友，还没有正式在一起",
        value="dating",
    )

    parsed = parse_memory_response(
        json.dumps({"claims": [raw_claim], "discarded_spans": []}, ensure_ascii=False),
        source_text=text,
    )

    assert parsed.extraction.claims[0].state_value == "acquaintance"
    assert "relationship_stage_semantic_normalization" in parsed.repair_steps


def test_parser_case_normalizes_canonical_user_belief_enums() -> None:
    text = "我感觉她可能已经不喜欢我了。"
    raw = {
        "claims": [
            {
                "claim_id": "belief",
                "kind": "USER_BELIEF",
                "subject": "partner",
                "predicate": "may_no_longer_like_user",
                "summary": "用户认为对方可能已经不喜欢自己",
                "evidence_spans": ["我感觉她可能已经不喜欢我了"],
                "perspective": "USER_BELIEF",
                "predicate_type": "CUSTOM",
                "explicitness": "SPECULATIVE",
            }
        ],
        "discarded_spans": [],
    }

    parsed = parse_memory_response(json.dumps(raw, ensure_ascii=False), source_text=text)

    claim = parsed.extraction.claims[0]
    assert claim.kind == MemoryKind.STABLE_FACT
    assert claim.perspective == MemoryPerspective.USER_BELIEF
    assert claim.predicate_type == PredicateType.CUSTOM
    assert claim.explicitness.value == "speculative"
    assert "enum_aliases" in parsed.repair_steps


def test_user_belief_kind_overrides_conflicting_reported_perspective() -> None:
    text = "我感觉她可能已经不喜欢我了。"
    raw = {
        "claims": [
            {
                "claim_id": "belief-with-conflicting-perspective",
                "kind": "USER_BELIEF",
                "subject": "partner",
                "predicate": "may_no_longer_like_user",
                "summary": "用户认为对方可能已经不喜欢自己",
                "evidence_spans": ["我感觉她可能已经不喜欢我了"],
                "perspective": "USER_REPORTED",
                "predicate_type": "CUSTOM",
                "explicitness": "SPECULATIVE",
            }
        ],
        "discarded_spans": [],
    }

    parsed = parse_memory_response(json.dumps(raw, ensure_ascii=False), source_text=text)

    claim = parsed.extraction.claims[0]
    assert claim.kind == MemoryKind.STABLE_FACT
    assert claim.perspective == MemoryPerspective.USER_BELIEF


def test_atomic_extraction_rejects_evidence_not_present_in_source() -> None:
    extraction = AtomicExtraction(
        claims=[
            AtomicClaim(
                claim_id="unsupported-claim",
                kind=MemoryKind.INTERACTION_EVENT,
                subject="relationship",
                predicate="met_for_dinner",
                summary="双方昨晚一起吃饭",
                evidence_spans=["昨晚我们一起吃饭了"],
            )
        ]
    )

    with pytest.raises(ValueError, match="证据片段不在用户原文中"):
        _validate_extraction(extraction, "我们正在讨论周末安排。")


def test_atomic_claim_flattens_model_temporal_object() -> None:
    claim = AtomicClaim.model_validate(
        {
            "claim_id": "nested-temporal",
            "kind": "interaction_pattern",
            "subject": "relationship",
            "predicate": "reply_frequency_declined",
            "summary": "最近一周回复频率下降",
            "evidence_spans": ["最近一周回复变少了"],
            "temporal": {
                "type": "interval",
                "period_start": "2026-07-10T00:00:00+08:00",
                "period_end": "2026-07-17T00:00:00+08:00",
                "precision": "day",
            },
        }
    )

    assert claim.time_kind == TimeKind.INTERVAL
    assert claim.temporal_precision == TemporalPrecision.DAY
    assert claim.period_start is not None and claim.period_start.day == 10


def test_atomic_claim_accepts_model_time_object_alias() -> None:
    claim = AtomicClaim.model_validate(
        {
            "claim_id": "time-object-alias",
            "kind": "interaction_event",
            "subject": "relationship",
            "predicate": "argued",
            "summary": "双方昨晚发生争吵",
            "evidence_spans": ["昨晚我们吵了一架"],
            "time": {
                "type": "point",
                "occurred_at": "2026-07-17T20:00:00+08:00",
                "precision": "day",
            },
        }
    )

    assert claim.time_kind == TimeKind.POINT
    assert claim.occurred_at is not None and claim.occurred_at.day == 17


def test_planned_event_keeps_future_window_and_expiration() -> None:
    claim = AtomicClaim.model_validate(
        {
            "claim_id": "planned-group-discussion",
            "kind": "planned_event",
            "subject": "relationship",
            "predicate": "attend_course_discussion",
            "object": "课程小组讨论",
            "summary": "下周双方有一次课程小组讨论",
            "evidence_spans": ["下周有机会一起小组讨论"],
            "time_kind": "point",
            "period_start": "2026-07-27T09:00:00+08:00",
            "expires_at": "2026-07-28T00:00:00+08:00",
            "payload": {
                "event_status": "tentative",
                "temporal_expression": "下周",
            },
        }
    )

    assert claim.kind == MemoryKind.PLANNED_EVENT
    assert claim.period_start is not None and claim.period_start.day == 27
    assert claim.expires_at is not None and claim.expires_at.day == 28
    assert claim.to_candidate().expires_at == claim.expires_at


def test_planned_event_without_time_is_rejected() -> None:
    claim = AtomicClaim(
        claim_id="vague-plan",
        kind=MemoryKind.PLANNED_EVENT,
        subject="relationship",
        predicate="meet_partner",
        summary="双方以后可能见面",
        evidence_spans=["以后可能见面"],
    )

    with pytest.raises(ValueError, match=r"计划事件.*未来时间"):
        _validate_extraction(AtomicExtraction(claims=[claim]), "以后可能见面")


def test_parser_normalizes_pending_event_alias() -> None:
    text = "后天要参加课程讨论。"
    parsed = parse_memory_response(
        json.dumps(
            {
                "claims": [
                    {
                        "claim_id": "pending-discussion",
                        "kind": "pending_event",
                        "subject": "relationship",
                        "predicate": "attend_course_discussion",
                        "summary": "后天双方要参加课程讨论",
                        "evidence_spans": [text],
                        "payload": {"event_status": "planned"},
                    }
                ],
                "discarded_spans": [],
            },
            ensure_ascii=False,
        ),
        source_text=text,
    )

    assert parsed.extraction.claims[0].kind == MemoryKind.PLANNED_EVENT


def test_atomic_claim_safely_normalizes_optional_model_noise() -> None:
    claim = AtomicClaim.model_validate(
        {
            "claim_id": "optional-noise",
            "kind": "stable_fact",
            "subject": "user",
            "predicate": "likes_partner",
            "summary": "用户喜欢对方",
            "evidence_spans": ["我喜欢她"],
            "temporal_precision": "recent",
            "payload": [],
        }
    )

    assert claim.temporal_precision == TemporalPrecision.UNKNOWN
    assert claim.payload == {}


def test_atomic_claim_normalizes_time_aliases_and_ignores_unknown_metadata() -> None:
    claim = AtomicClaim.model_validate(
        {
            "claim_id": "model-time-aliases",
            "kind": "interaction_event",
            "subject": "relationship",
            "predicate": "talked",
            "summary": "双方昨晚聊天",
            "evidence_spans": ["昨晚我们聊了很久"],
            "time_value": "昨晚",
            "reference_time": "2026-07-18T12:00:00+08:00",
            "model_note": "unsupported optional metadata",
        }
    )

    assert claim.occurred_at is None
    assert claim.payload["temporal_expression"] == "昨晚"


def test_atomic_claim_normalizes_top_level_model_aliases() -> None:
    claim = AtomicClaim.model_validate(
        {
            "claim_id": "top-level-aliases",
            "kind": "interaction_pattern",
            "subject": "relationship",
            "predicate": "engagement_increased",
            "summary": "最近互动参与度提高",
            "evidence": "最近互动比以前多了",
            "temporal_start": "2026-07-01T00:00:00+08:00",
            "metric": "conversation_engagement",
            "direction": "improving",
        }
    )

    assert claim.evidence_spans == ["最近互动比以前多了"]
    assert claim.period_start is not None and claim.period_start.day == 1
    assert claim.payload == {
        "metric": "conversation_engagement",
        "direction": "improving",
    }


def test_atomic_extraction_rejects_english_summary_and_unstructured_pattern() -> None:
    claim = AtomicClaim(
        claim_id="english-pattern",
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="relationship",
        predicate="engagement_increased",
        summary="Conversation engagement increased.",
        evidence_spans=["最近聊天变多了"],
    )

    with pytest.raises(ValueError, match="summary 必须使用简体中文"):
        _validate_extraction(AtomicExtraction(claims=[claim]), "最近聊天变多了")


def test_parser_normalizes_kind_and_perspective_aliases_without_inference() -> None:
    text = "听说她最近经常和一个男生聊天，感觉那个男孩子也在追求她，他比我优秀。"
    payload = {
        "claims": [
            {
                "claim_id": "chat-trend",
                "kind": "trend",
                "subject": "relationship",
                "predicate": "partner_chat_frequency",
                "summary": "用户听说对方最近经常和一名男生聊天",
                "evidence_spans": ["最近经常和一个男生聊天"],
                "perspective": "reported",
                "payload": {"metric": "partner_chat_frequency", "source_type": "hearsay"},
            },
            {
                "claim_id": "pursuit-belief",
                "kind": "user_belief",
                "subject": "user",
                "predicate": "believes_other_boy_pursues_partner",
                "summary": "用户感觉另一名男生也在追求对方",
                "evidence_spans": ["感觉那个男孩子也在追求她"],
            },
            {
                "claim_id": "comparison-belief",
                "kind": "belief",
                "subject": "user",
                "predicate": "feels_inferior_to_other_boy",
                "summary": "用户觉得另一名男生比自己优秀",
                "evidence_spans": ["他比我优秀"],
            },
        ],
        "discarded_spans": [],
    }

    parsed = parse_memory_response(
        json.dumps(payload, ensure_ascii=False),
        source_text=text,
    )

    assert [claim.kind.value for claim in parsed.extraction.claims] == [
        "interaction_pattern",
        "stable_fact",
        "stable_fact",
    ]
    assert parsed.extraction.claims[0].perspective.value == "user_reported"
    assert [claim.perspective.value for claim in parsed.extraction.claims[1:]] == [
        "user_belief",
        "user_belief",
    ]
    assert parsed.invalid_claim_count == 0
    assert "enum_aliases" in parsed.repair_steps


def test_unknown_enum_is_reported_with_safe_input_detail() -> None:
    payload = {
        "claims": [
            {
                "claim_id": "unknown-kind",
                "kind": "unsupported_kind",
                "subject": "user",
                "predicate": "has_fact",
                "summary": "用户有一条事实",
                "evidence_spans": ["我有一条事实"],
            }
        ],
        "discarded_spans": [],
    }

    with pytest.raises(MemoryResponseError) as captured:
        parse_memory_response(json.dumps(payload, ensure_ascii=False), source_text="我有一条事实")

    assert captured.value.category == "unsupported_enum"
    assert "unsupported_kind" in str(captured.value)
    assert captured.value.details["invalid_claim_count"] == 1
    assert "unsupported_kind" in str(
        captured.value.details["invalid_claim_reasons"]
    )


def test_partial_claim_validation_keeps_valid_atomic_claims() -> None:
    payload = {
        "claims": [
            {
                "claim_id": "valid-preference",
                "kind": "preference",
                "subject": "user",
                "predicate": "likes_food",
                "summary": "用户喜欢粤菜",
                "evidence_spans": ["我喜欢粤菜"],
                "payload": {"preference": "粤菜", "preference_type": "like"},
            },
            {
                "claim_id": "missing-summary",
                "kind": "stable_fact",
                "subject": "user",
                "predicate": "has_fact",
                "evidence_spans": ["我也有一个事实"],
            },
        ],
        "discarded_spans": [],
    }

    parsed = parse_memory_response(
        json.dumps(payload, ensure_ascii=False),
        source_text="我喜欢粤菜，我也有一个事实。",
    )

    assert [claim.claim_id for claim in parsed.extraction.claims] == ["valid-preference"]
    assert parsed.invalid_claim_count == 1
    assert "partial_claims" in parsed.repair_steps


def test_atomic_repair_keeps_profile_fact_and_bounded_event() -> None:
    text = (
        "其实我有点社恐，不太擅长主动找话题。"
        "我试过在微信上问她一个工作问题，但聊完工作就不知道说什么了，"
        "对话框就停在那里，很尴尬。"
    )
    payload = {
        "claims": [
            {
                "claim_id": "social-style",
                "kind": "stable_fact",
                "subject": "user",
                "predicate": "struggles_to_initiate_topics",
                "summary": "用户有些社恐且不擅长主动找话题",
                "evidence_spans": [text],
            },
            {
                "claim_id": "wechat-attempt",
                "kind": "interaction_event",
                "subject": "relationship",
                "predicate": "work_chat_stalled",
                "summary": "用户曾在微信询问工作问题，工作话题结束后对话停滞",
                "evidence_spans": [
                    "我试过在微信上问她一个工作问题，"
                    "但聊完工作就不知道说什么了，对话框就停在那里"
                ],
            },
        ],
        "discarded_spans": [
            {"text": "很尴尬", "reason": "ephemeral"},
        ],
    }

    parsed = parse_memory_response(
        json.dumps(payload, ensure_ascii=False),
        source_text=text,
    )

    assert [claim.claim_id for claim in parsed.extraction.claims] == [
        "social-style",
        "wechat-attempt",
    ]
    assert parsed.extraction.claims[0].evidence_spans == [
        "其实我有点社恐",
        "不太擅长主动找话题",
    ]
    assert parsed.repaired_claim_count == 1
    assert parsed.discarded_claim_count == 0
    assert parsed.extraction.discarded_spans[0].text == "很尴尬"


async def test_memory_extractor_uses_one_flash_call_for_invalid_json() -> None:
    flash_completions = _FakeCompletions(["not-json"])
    strong_completions = _FakeCompletions(['{"claims":[],"discarded_spans":[]}'])
    extractor = _build_tiered_extractor(flash_completions, strong_completions)
    trace = ExecutionTrace()
    attempts = []

    result = await extractor.extract(
        "我喜欢粤菜。",
        reference_time=datetime(2026, 7, 18, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
        trace=trace,
        attempt_callback=attempts.append,
    )

    assert result.claims == []
    assert flash_completions.calls == 1
    assert strong_completions.calls == 0
    assert flash_completions.request_kwargs[0]["extra_body"] == {
        "thinking": {"type": "disabled"}
    }
    assert len(attempts) == 1
    assert attempts[0].discard_reason == "ordinary_format_error"
    model_records = [record for record in trace.records if record.name.startswith("memory_model")]
    assert len(model_records) == 1
    assert model_records[0].details["failure_category"] == "json_syntax"
    await extractor.aclose()


@pytest.mark.parametrize(
    "response,secret",
    [
        ("Authorization: Bearer top-secret", "top-secret"),
        ('{ "client_secret": bare-secret }', "bare-secret"),
        ("access_token: exposed-token\nnot-json", "exposed-token"),
    ],
)
async def test_invalid_model_response_snapshot_redacts_bare_secrets(
    response: str,
    secret: str,
) -> None:
    attempts = []
    extractor = _build_tiered_extractor(_FakeCompletions([response]), None)

    result = await extractor.extract(
        "我喜欢粤菜。",
        reference_time=datetime(2026, 7, 18, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
        attempt_callback=attempts.append,
    )

    assert result.claims == []
    assert len(attempts) == 1
    assert attempts[0].failure_category == "json_syntax"
    assert secret not in str(attempts[0].raw_model_response)
    assert "[REDACTED]" in str(attempts[0].raw_model_response)
    await extractor.aclose()


async def test_failed_extraction_attempt_keeps_validation_and_repair_details() -> None:
    response = json.dumps(
        {
            "claims": [
                {
                    "claim_id": "unknown-kind",
                    "kind": "unsupported_kind",
                    "subject": "user",
                    "predicate": "has_fact",
                    "summary": "用户陈述了一条事实",
                    "evidence_spans": ["我有一条事实"],
                    "api_key": "must-not-be-logged",
                    "access_token": "access-token-must-not-be-logged",
                    "client-secret": "client-secret-must-not-be-logged",
                    "nested": {
                        "private_key": "private-key-must-not-be-logged",
                    },
                }
            ],
            "discarded_spans": [],
        },
        ensure_ascii=False,
    )
    attempts = []
    extractor = _build_tiered_extractor(_FakeCompletions([response]), None)

    result = await extractor.extract(
        "我有一条事实",
        reference_time=datetime(2026, 7, 18, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
        attempt_callback=attempts.append,
    )

    assert result.claims == []
    assert len(attempts) == 1
    assert attempts[0].failure_category == "unsupported_enum"
    assert attempts[0].invalid_claim_count == 1
    assert "unsupported_kind" in str(attempts[0].invalid_claim_reasons)
    assert attempts[0].repair_status in {"none", "local_repair"}
    assert "unsupported_kind" in str(attempts[0].invalid_claim_snapshot)
    assert "must-not-be-logged" not in str(attempts[0].invalid_claim_snapshot)
    assert "must-not-be-logged" not in str(attempts[0].raw_model_response)
    assert "access-token-must-not-be-logged" not in str(
        attempts[0].invalid_claim_snapshot
    )
    assert "access-token-must-not-be-logged" not in str(
        attempts[0].raw_model_response
    )
    assert "client-secret-must-not-be-logged" not in str(
        attempts[0].invalid_claim_snapshot
    )
    assert "client-secret-must-not-be-logged" not in str(
        attempts[0].raw_model_response
    )
    assert "private-key-must-not-be-logged" not in str(
        attempts[0].invalid_claim_snapshot
    )
    assert "private-key-must-not-be-logged" not in str(
        attempts[0].raw_model_response
    )
    assert "[REDACTED]" in str(attempts[0].invalid_claim_snapshot)
    assert "[REDACTED]" in str(attempts[0].raw_model_response)
    assert attempts[0].validation_error
    assert attempts[0].repair_attempt == "none"
    assert attempts[0].repair_result == "unresolved"
    await extractor.aclose()


async def test_successful_relationship_stage_repair_is_visible_in_attempt() -> None:
    text = "昨天我们确认关系了，现在正式在一起了。"
    response = json.dumps(
        {
            "claims": [
                {
                    "claim_id": "repaired-stage",
                    "kind": "relationship_state",
                    "subject": "relationship",
                    "predicate": "relationship_stage",
                    "summary": "双方已经确认恋爱关系",
                    "evidence_spans": ["昨天我们确认关系了，现在正式在一起了"],
                    "payload": {},
                }
            ],
            "discarded_spans": [],
        },
        ensure_ascii=False,
    )
    attempts = []
    extractor = _build_tiered_extractor(_FakeCompletions([response]), None)

    result = await extractor.extract(
        text,
        reference_time=datetime(2026, 7, 18, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
        attempt_callback=attempts.append,
    )

    assert result.claims[0].state_value == "dating"
    assert attempts[0].repair_attempt == "relationship_stage_bounded_repair"
    assert attempts[0].repair_result == "relationship.stage=dating"
    await extractor.aclose()


async def test_flash_trace_keeps_raw_predicate_before_canonicalization() -> None:
    source_text = (
        "\u6700\u8fd1\u4e24\u5468\u6211\u4eec\u8054\u7cfb"
        "\u660e\u663e\u53d8\u5c11\u4e86\u3002"
    )
    response = _claim_response(
        claim_id="contact-trend",
        kind="interaction_pattern",
        subject="relationship",
        predicate="contact_frequency_declined",
        summary=(
            "\u6700\u8fd1\u4e24\u5468\u53cc\u65b9\u8054\u7cfb"
            "\u660e\u663e\u53d8\u5c11"
        ),
        evidence=source_text,
        extra='"confidence":0.9,"payload":{"metric":"contact_frequency"}',
    )
    extractor = _build_tiered_extractor(_FakeCompletions([response]), None)
    trace = ExecutionTrace()

    await extractor.extract(
        source_text,
        reference_time=datetime(2026, 7, 18, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
        trace=trace,
    )

    flash_record = next(
        record for record in trace.snapshot() if record.name == "memory_model_attempt_1"
    )
    assert "claim_predicates_json" in flash_record.details, flash_record.details
    predicates = json.loads(str(flash_record.details["claim_predicates_json"]))
    assert predicates == [
        {
            "claim_id": "contact-trend",
            "raw_predicate": "contact_frequency_declined",
        }
    ]
    claims = json.loads(str(flash_record.details["claims_json"]))
    assert len(claims) == 1
    assert claims[0]["claim_id"] == "contact-trend"
    assert claims[0]["kind"] == "interaction_pattern"
    assert claims[0]["subject"] == "relationship"
    assert claims[0]["raw_predicate"] == "contact_frequency_declined"
    assert claims[0]["confidence"] == pytest.approx(0.9)
    assert claims[0]["evidence_spans"] == [source_text]
    assert claims[0]["payload"] == {"metric": "contact_frequency"}
    assert claims[0]["extractor_model"] == "flash-model"
    assert claims[0]["prompt_version"] == "memory-v2.2"
    await extractor.aclose()


async def test_local_repair_handles_fence_trailing_comma_and_defaults() -> None:
    flash_completions = _FakeCompletions(["```json\n{\"claims\": [],}\n```"])
    extractor = _build_tiered_extractor(flash_completions, None)
    attempts = []

    result = await extractor.extract(
        "谢谢。",
        reference_time=datetime(2026, 7, 18, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
        attempt_callback=attempts.append,
    )

    assert result.claims == []
    assert flash_completions.calls == 1
    assert attempts[0].status.value == "completed"
    assert attempts[0].repair_status == "local_repair"
    await extractor.aclose()


async def test_missing_semantic_fields_are_discarded_without_strong_upgrade() -> None:
    response = '{"claims":[{"claim_id":"missing-fields","kind":"stable_fact","subject":"user"}]}'
    flash_completions = _FakeCompletions([response])
    strong_completions = _FakeCompletions(['{"claims":[],"discarded_spans":[]}'])
    extractor = _build_tiered_extractor(flash_completions, strong_completions)

    result = await extractor.extract(
        "我喜欢粤菜。",
        reference_time=datetime(2026, 7, 18, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
    )

    assert result.claims == []
    assert flash_completions.calls == 1
    assert strong_completions.calls == 0
    await extractor.aclose()


async def test_important_json_syntax_error_is_still_discarded() -> None:
    flash_completions = _FakeCompletions(["截断的 JSON"])
    strong_completions = _FakeCompletions(['{"claims":[],"discarded_spans":[]}'])
    extractor = _build_tiered_extractor(flash_completions, strong_completions)

    result = await extractor.extract(
        "最近两周我们联系明显变少了。",
        reference_time=datetime(2026, 7, 18, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
    )

    assert result.claims == []
    assert flash_completions.calls == 1
    assert strong_completions.calls == 0
    await extractor.aclose()


async def test_important_semantic_failure_is_upgraded_to_strong_model() -> None:
    text = "我们昨晚吵架了，我很难受。"
    strong_response = _claim_response(
        claim_id="argument",
        kind="interaction_event",
        subject="relationship",
        predicate="argued",
        summary="双方昨晚发生争吵",
        evidence="昨晚吵架",
    )
    flash_completions = _FakeCompletions(
        [
            _claim_response(
                claim_id="invalid-evidence",
                kind="interaction_event",
                subject="relationship",
                predicate="argued",
                summary="双方昨晚发生争吵",
                evidence="这段证据不在用户原文",
            )
        ]
    )
    strong_completions = _FakeCompletions([strong_response])
    extractor = _build_tiered_extractor(flash_completions, strong_completions)
    trace = ExecutionTrace()
    attempts = []

    result = await extractor.extract(
        text,
        reference_time=datetime(2026, 7, 18, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
        trace=trace,
        attempt_callback=attempts.append,
    )

    assert [claim.claim_id for claim in result.claims] == ["argument"]
    assert flash_completions.calls == 1
    assert strong_completions.calls == 1
    assert attempts[0].upgrade_reason == "semantic_uncertainty"
    assert attempts[1].tier == "strong"
    assert strong_completions.request_kwargs[0]["extra_body"] == {
        "thinking": {"type": "enabled"}
    }
    model_records = [record for record in trace.records if record.name.startswith("memory_model")]
    assert [record.name for record in model_records] == [
        "memory_model_attempt_1",
        "memory_model_strong_attempt_2",
    ]
    assert all(record.duration_ms >= 0 for record in model_records)
    await extractor.aclose()


async def test_unscheduled_action_plan_is_locally_repaired_without_strong_upgrade() -> None:
    text = "我决定先请她吃顿饭，然后再认真聊一下消费观的事情"
    flash_response = json.dumps(
        {
            "claims": [
                {
                    "claim_id": "meal",
                    "kind": "planned_event",
                    "subject": "user",
                    "predicate": "invite_partner_to_meal",
                    "summary": "用户决定请对方吃饭",
                    "evidence_spans": ["我决定先请她吃顿饭"],
                },
                {
                    "claim_id": "talk",
                    "kind": "planned_event",
                    "subject": "relationship",
                    "predicate": "discuss_consumption_values",
                    "summary": "用户准备之后与对方讨论消费观",
                    "evidence_spans": ["然后再认真聊一下消费观的事情"],
                },
            ],
            "discarded_spans": [],
        },
        ensure_ascii=False,
    )
    flash_completions = _FakeCompletions([flash_response])
    strong_completions = _FakeCompletions(['{"claims":[],"discarded_spans":[]}'])
    extractor = _build_tiered_extractor(flash_completions, strong_completions)
    attempts = []

    result = await extractor.extract(
        text,
        reference_time=datetime(2026, 7, 31, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
        attempt_callback=attempts.append,
    )

    assert [claim.kind for claim in result.claims] == [
        MemoryKind.ACTION_INTENT,
        MemoryKind.ACTION_INTENT,
    ]
    assert flash_completions.calls == 1
    assert strong_completions.calls == 0
    assert attempts[0].repair_status == "local_repair"
    await extractor.aclose()


async def test_low_confidence_important_memory_is_upgraded() -> None:
    text = "最近两周我们联系明显变少了。"
    flash_response = _claim_response(
        claim_id="contact-trend-flash",
        kind="interaction_pattern",
        subject="relationship",
        predicate="contact_frequency_declined",
        summary="最近两周双方联系明显变少",
        evidence="最近两周我们联系明显变少了",
        extra='"confidence":0.3,"payload":{"metric":"contact_frequency"}',
    )
    strong_response = _claim_response(
        claim_id="contact-trend-strong",
        kind="interaction_pattern",
        subject="relationship",
        predicate="contact_frequency_declined",
        summary="最近两周双方联系明显变少",
        evidence="最近两周我们联系明显变少了",
        extra='"confidence":0.92,"payload":{"metric":"contact_frequency"}',
    )
    flash_completions = _FakeCompletions([flash_response])
    strong_completions = _FakeCompletions([strong_response])
    extractor = _build_tiered_extractor(flash_completions, strong_completions)

    result = await extractor.extract(
        text,
        reference_time=datetime(2026, 7, 18, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
    )

    assert result.claims[0].claim_id == "contact-trend-strong"
    assert flash_completions.calls == 1
    assert strong_completions.calls == 1
    await extractor.aclose()


async def test_empty_strong_result_does_not_erase_flash_claims() -> None:
    text = "最近两周我们联系明显变少了。"
    flash_response = _claim_response(
        claim_id="contact-trend-flash-kept",
        kind="interaction_pattern",
        subject="relationship",
        predicate="contact_frequency_declined",
        summary="最近两周双方联系明显变少",
        evidence="最近两周我们联系明显变少了",
        extra='"confidence":0.3,"payload":{"metric":"contact_frequency"}',
    )
    flash_completions = _FakeCompletions([flash_response])
    strong_completions = _FakeCompletions(['{"claims":[],"discarded_spans":[]}'])
    extractor = _build_tiered_extractor(flash_completions, strong_completions)
    attempts = []

    result = await extractor.extract(
        text,
        reference_time=datetime(2026, 7, 18, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
        attempt_callback=attempts.append,
    )

    assert [claim.claim_id for claim in result.claims] == ["contact-trend-flash-kept"]
    assert strong_completions.calls == 1
    assert attempts[1].discard_reason == "strong_empty_fallback_to_flash"
    await extractor.aclose()


async def test_partial_flash_claims_do_not_trigger_strong_without_a_coverage_gap() -> None:
    text = "最近两周我们联系明显变少了。"
    payload = json.loads(
        _claim_response(
            claim_id="partial-valid",
            kind="interaction_pattern",
            subject="relationship",
            predicate="contact_frequency_declined",
            summary="最近两周双方联系明显变少",
            evidence="最近两周我们联系明显变少了",
            extra='"confidence":0.9,"payload":{"metric":"contact_frequency"}',
        )
    )
    payload["claims"].append(
        {
            "claim_id": "partial-invalid",
            "kind": "interaction_pattern",
            "subject": "relationship",
            "predicate": "missing_metric",
            "summary": "这条声明缺少模式指标",
            "evidence_spans": ["最近两周我们联系明显变少了"],
        }
    )
    flash_completions = _FakeCompletions([json.dumps(payload, ensure_ascii=False)])
    strong_completions = _FakeCompletions(["not-used"])
    extractor = _build_tiered_extractor(flash_completions, strong_completions)

    result = await extractor.extract(
        text,
        reference_time=datetime(2026, 7, 18, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
    )

    assert [claim.claim_id for claim in result.claims] == ["partial-valid"]
    assert strong_completions.calls == 0
    await extractor.aclose()


async def test_ordinary_coverage_gap_does_not_trigger_strong_model() -> None:
    text = "我们刚认识不久还不太熟，但在共同活动里经常有机会见面。"
    flash_response = json.dumps(
        {
            "claims": [
                {
                    "claim_id": "familiarity-only",
                    "kind": "relationship_state",
                    "subject": "relationship",
                    "predicate": "familiarity_level",
                    "summary": "双方当前熟悉度较低",
                    "evidence_spans": ["刚认识不久还不太熟"],
                    "payload": {
                        "state_dimension": "relationship_familiarity",
                        "state_value": "low",
                    },
                }
            ],
            "discarded_spans": [],
        },
        ensure_ascii=False,
    )
    flash_completions = _FakeCompletions([flash_response])
    strong_completions = _FakeCompletions(["not-used"])
    extractor = _build_tiered_extractor(flash_completions, strong_completions)

    result = await extractor.extract(
        text,
        reference_time=datetime(2026, 7, 31, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
    )

    assert [claim.claim_id for claim in result.claims] == ["familiarity-only"]
    assert flash_completions.calls == 1
    assert strong_completions.calls == 0
    await extractor.aclose()


async def test_attempt_telemetry_reports_locally_repaired_claim() -> None:
    text = "我们线上聊天比以前更频繁，但平时基本只聊课程。"
    response = {
        "claims": [
            {
                "claim_id": "frequency",
                "kind": "interaction_pattern",
                "subject": "relationship",
                "predicate": "contact_frequency_changed",
                "summary": "双方线上聊天频率提高",
                "evidence_spans": ["我们线上聊天比以前更频繁"],
                "payload": {"metric": "contact_frequency", "direction": "increasing"},
            },
            {
                "claim_id": "invalid-combined",
                "kind": "interaction_pattern",
                "subject": "relationship",
                "predicate": "interaction_is_limited",
                "summary": "双方线上聊天频率提高但话题局限于课程",
                "evidence_spans": [text.rstrip("。")],
                "payload": {"metric": "interaction_quality"},
            },
        ],
        "discarded_spans": [],
    }
    flash_completions = _FakeCompletions([json.dumps(response, ensure_ascii=False)])
    strong_completions = _FakeCompletions(
        ['{"claims":[],"discarded_spans":[]}']
    )
    extractor = _build_tiered_extractor(flash_completions, strong_completions)
    attempts = []

    result = await extractor.extract(
        text,
        reference_time=datetime(2026, 7, 31, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
        attempt_callback=attempts.append,
    )

    assert [claim.claim_id for claim in result.claims] == [
        "frequency",
        "invalid-combined",
    ]
    assert result.claims[1].payload["metric"] == "topic_scope"
    assert attempts[0].original_claim_count == 2
    assert attempts[0].repaired_claim_count == 1
    assert attempts[0].discarded_claim_count == 0
    assert attempts[0].invalid_claim_count is None
    assert flash_completions.calls == 1
    assert strong_completions.calls == 0
    await extractor.aclose()


async def test_one_call_latency_is_bounded_by_one_model_request() -> None:
    flash_completions = _FakeCompletions(["not-json"], delay_seconds=0.03)
    strong_completions = _FakeCompletions(["not-used"], delay_seconds=0.03)
    extractor = _build_tiered_extractor(flash_completions, strong_completions)

    started = perf_counter()
    await extractor.extract(
        "我喜欢粤菜。",
        reference_time=datetime(2026, 7, 18, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
    )
    elapsed = perf_counter() - started

    assert flash_completions.calls == 1
    assert strong_completions.calls == 0
    assert elapsed < 0.08
    await extractor.aclose()


def test_memory_trace_summary_reports_direct_repair_and_upgrade_rates() -> None:
    records = [
        {
            "name": "memory_model_attempt_1",
            "status": "completed",
            "details": {"repair_status": "direct"},
        },
        {
            "name": "memory_model_strong_attempt_2",
            "status": "completed",
            "details": {},
        },
    ]

    assert _memory_trace_summary(records) == {
        "flash_call_count": 1,
        "flash_direct_success_count": 1,
        "local_repair_count": 0,
        "strong_upgrade_count": 1,
        "discarded_invalid_count": 0,
    }


class _FakeCompletions:
    def __init__(self, responses: list[str], delay_seconds: float = 0) -> None:
        self.responses = responses
        self.delay_seconds = delay_seconds
        self.calls = 0
        self.request_kwargs: list[dict] = []

    async def create(self, **kwargs):
        self.request_kwargs.append(kwargs)
        self.calls += 1
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        content = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        usage = SimpleNamespace(
            prompt_tokens=12,
            completion_tokens=5,
            total_tokens=17,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=3),
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=usage,
        )


async def _async_noop() -> None:
    return None


def _build_tiered_extractor(
    flash_completions: _FakeCompletions,
    strong_completions: _FakeCompletions | None,
) -> TieredMemoryExtractor:
    flash = _build_single_extractor(flash_completions, "flash")
    strong = (
        _build_single_extractor(strong_completions, "strong")
        if strong_completions is not None
        else None
    )
    return TieredMemoryExtractor(flash, strong)


def _build_single_extractor(
    completions: _FakeCompletions,
    tier: str,
) -> OpenAICompatibleMemoryExtractor:
    extractor = OpenAICompatibleMemoryExtractor(
        api_key=SecretStr("test-key"),
        base_url="https://example.invalid",
        model=f"{tier}-model",
        max_retries=0,
        tier=tier,
        thinking="disabled" if tier == "flash" else "enabled",
    )
    extractor._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        close=_async_noop,
    )
    return extractor


def _claim_response(
    *,
    claim_id: str,
    kind: str,
    subject: str,
    predicate: str,
    summary: str,
    evidence: str,
    extra: str = '"confidence":0.9',
) -> str:
    return (
        '{"claims":[{'
        f'"claim_id":"{claim_id}","kind":"{kind}","subject":"{subject}",'
        f'"predicate":"{predicate}","summary":"{summary}",'
        f'"evidence_spans":["{evidence}"],{extra}'
        '}],"discarded_spans":[]}'
    )

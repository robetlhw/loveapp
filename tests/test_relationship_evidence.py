from datetime import UTC, datetime, timedelta

from loveapp.adapters.memory.openai_compatible import _SYSTEM_PROMPT as MEMORY_SYSTEM_PROMPT
from loveapp.application.scenario_policy import (
    default_scenario_policy_registry,
    enforce_scenario_policy,
    hard_constraint_instructions,
)
from loveapp.domain.advice import AdviceResponse, RelationshipContext
from loveapp.domain.enums import AdviceGoal, AdviceScenario, RelationshipStage
from loveapp.domain.memory import (
    AtomicClaim,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    MemoryValence,
    RelationshipImpact,
    TimeKind,
)
from loveapp.domain.memory_context import attach_memories
from loveapp.domain.memory_lifecycle import normalize_memory_candidate
from loveapp.domain.relationship_evidence import (
    EvidenceProvenance,
    RelationshipEvidenceDimension,
    normalize_evidence_declarations,
    project_relationship_evidence,
    project_standardized_relationship_evidence,
    standardize_relationship_evidence,
)

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


def test_memory_prompt_requests_standard_relationship_evidence() -> None:
    assert "payload.relationship_evidence" in MEMORY_SYSTEM_PROMPT
    assert "familiarity、trust、investment、conflict、boundary" in MEMORY_SYSTEM_PROMPT
    assert "participants 同时包含" in MEMORY_SYSTEM_PROMPT


def test_private_interaction_and_conflict_project_independent_dimensions() -> None:
    home_dinner = _memory_item(
        "home-dinner",
        MemoryKind.INTERACTION_EVENT,
        "对方到用户家中吃饭并积极回应",
        {
            "activity_type": "home_dinner",
            "participants": ["user", "partner"],
            "predicate": "hosted_dinner_for_partner",
        },
        impact=RelationshipImpact.IMPROVING,
    )
    conflict = _memory_item(
        "conflict",
        MemoryKind.INTERACTION_EVENT,
        "双方因为消费观发生争执",
        {
            "activity_type": "conflict",
            "participants": ["user", "partner"],
            "predicate": "had_conflict_over_spending",
        },
        impact=RelationshipImpact.DAMAGING,
    )

    before = project_relationship_evidence([home_dinner], reference_time=NOW)
    after = project_relationship_evidence([home_dinner, conflict], reference_time=NOW)

    assert before.familiarity == after.familiarity == "moderate"
    assert before.trust == after.trust == "high"
    assert before.investment == after.investment == "mixed"
    assert before.conflict_status == "unknown"
    assert after.conflict_status == "active"
    assert after.supports_low_pressure_progression is True

    context = attach_memories(
        RelationshipContext(
            user_id="u1",
            relationship_stage=RelationshipStage.UNKNOWN,
        ),
        [home_dinner, conflict],
        relationship_evidence=after,
    )
    assert context.relationship_stage == RelationshipStage.UNKNOWN


def test_participants_alone_do_not_become_familiarity_or_trust_evidence() -> None:
    operational_event = _memory_item(
        "operational-event",
        MemoryKind.INTERACTION_EVENT,
        "双方共同处理了一次普通事务",
        {
            "activity_type": "administrative_task",
            "participants": ["user", "partner"],
            "predicate": "handled_task_together",
        },
    )

    profile = project_relationship_evidence([operational_event], reference_time=NOW)

    assert profile.familiarity == "unknown"
    assert profile.trust == "unknown"
    assert profile.investment == "unknown"
    assert profile.evidence == []


def test_interaction_count_metric_does_not_directly_set_relationship_state() -> None:
    count_pattern = _memory_item(
        "date-count",
        MemoryKind.INTERACTION_PATTERN,
        "双方已经见面三次",
        {
            "metric": "date_count",
            "current": "3",
            "predicate": "has_met_partner",
        },
    )

    profile = project_relationship_evidence([count_pattern], reference_time=NOW)

    assert profile.familiarity == "unknown"
    assert profile.trust == "unknown"
    assert profile.investment == "unknown"


def test_extracted_standard_evidence_takes_priority_over_legacy_mapping() -> None:
    memory = _memory_item(
        "declared-home-dinner",
        MemoryKind.INTERACTION_EVENT,
        "双方在家中一起吃饭",
        {
            "activity_type": "home_dinner",
            "relationship_evidence": [
                {
                    "dimension": "trust",
                    "direction": "support",
                    "strength": 0.55,
                    "confidence": 0.8,
                    "rationale": "explicit_private_access",
                }
            ],
        },
    )

    signals = standardize_relationship_evidence([memory], reference_time=NOW)
    trust_signals = [
        item for item in signals if item.dimension == RelationshipEvidenceDimension.TRUST
    ]

    assert len(trust_signals) == 1
    assert trust_signals[0].provenance == EvidenceProvenance.EXTRACTED
    assert trust_signals[0].strength == 0.55
    assert any(
        item.dimension == RelationshipEvidenceDimension.FAMILIARITY
        and item.provenance == EvidenceProvenance.LEGACY_STANDARDIZER
        for item in signals
    )


def test_same_message_evidence_is_correlated_not_counted_twice() -> None:
    declaration = {
        "relationship_evidence": [
            {
                "dimension": "familiarity",
                "direction": "support",
                "strength": 0.6,
                "confidence": 0.9,
                "rationale": "shared_personal_context",
            }
        ]
    }
    first = _memory_item(
        "same-source-a",
        MemoryKind.INTERACTION_EVENT,
        "双方分享了一次个人经历",
        declaration,
        source_message_id="same-message",
    )
    second = _memory_item(
        "same-source-b",
        MemoryKind.INTERACTION_EVENT,
        "双方在同一段经历中有深入交流",
        declaration,
        source_message_id="same-message",
    )

    profile = project_relationship_evidence([first, second], reference_time=NOW)
    projection = profile.projection_for(RelationshipEvidenceDimension.FAMILIARITY)

    assert projection is not None
    assert projection.independent_source_count == 1
    assert projection.score == 0.54
    assert profile.familiarity == "moderate"


def test_strength_and_confidence_have_distinct_effects() -> None:
    uncertain = _memory_item(
        "uncertain-trust",
        MemoryKind.INTERACTION_EVENT,
        "用户不确定对方是否愿意倾诉",
        {
            "relationship_evidence": [
                {
                    "dimension": "trust",
                    "direction": "support",
                    "strength": 0.9,
                    "confidence": 0.2,
                    "rationale": "uncertain_disclosure",
                }
            ]
        },
    )

    profile = project_relationship_evidence([uncertain], reference_time=NOW)
    projection = profile.projection_for(RelationshipEvidenceDimension.TRUST)

    assert projection is not None
    assert projection.score == 0.18
    assert projection.confidence == 0.2
    assert profile.trust == "unknown"
    assert profile.supports_low_pressure_progression is False


def test_projection_accepts_only_standardized_signals() -> None:
    memory = _memory_item(
        "standardization-boundary",
        MemoryKind.INTERACTION_EVENT,
        "对方到用户家中吃饭",
        {"activity_type": "home_dinner"},
    )
    signals = standardize_relationship_evidence([memory], reference_time=NOW)

    profile = project_standardized_relationship_evidence(signals, reference_time=NOW)

    assert profile.familiarity == "moderate"
    assert profile.trust == "high"
    assert all(item.source_memory_id == memory.id for item in profile.evidence)


def test_old_conflict_evidence_decays_out_of_active_state() -> None:
    conflict = _memory_item(
        "old-conflict",
        MemoryKind.INTERACTION_EVENT,
        "双方以前发生过争执",
        {"activity_type": "conflict"},
        updated_at=NOW - timedelta(days=70),
    )

    profile = project_relationship_evidence([conflict], reference_time=NOW)

    assert profile.conflict_status == "repairing"
    assert profile.requires_deescalation is False


def test_explicit_resolution_can_close_recent_conflict_evidence() -> None:
    conflict = _memory_item(
        "recent-conflict",
        MemoryKind.INTERACTION_EVENT,
        "双方此前发生争执",
        {"activity_type": "conflict"},
        updated_at=NOW - timedelta(days=1),
    )
    resolved = _memory_item(
        "resolved-conflict",
        MemoryKind.RELATIONSHIP_STATE,
        "双方已经把争执说开",
        {
            "state_dimension": "conflict_status",
            "state_value": "resolved",
        },
    )

    profile = project_relationship_evidence([conflict, resolved], reference_time=NOW)

    assert profile.conflict_status == "resolved"
    assert profile.requires_deescalation is False


def test_canonical_conflict_state_overrides_conflicting_extracted_evidence() -> None:
    resolved = _memory_item(
        "resolved-with-conflicting-evidence",
        MemoryKind.RELATIONSHIP_STATE,
        "双方已经和好",
        {
            "state_dimension": "conflict_status",
            "state_value": "resolved",
            "relationship_evidence": [
                {
                    "dimension": "conflict",
                    "direction": "support",
                    "strength": 0.95,
                    "confidence": 0.95,
                    "rationale": "explicit_reconciliation",
                }
            ],
        },
    ).model_copy(
        update={
            "canonical_predicate": "relationship.conflict_status",
            "state_dimension": "relationship.conflict_status",
            "state_value": "resolved",
        }
    )

    profile = project_relationship_evidence([resolved], reference_time=NOW)
    projection = profile.projection_for(RelationshipEvidenceDimension.CONFLICT)

    assert profile.conflict_status == "resolved"
    assert profile.requires_deescalation is False
    assert projection is not None
    assert projection.supporting_evidence_ids == []
    assert len(projection.opposing_evidence_ids) == 1
    assert {item.provenance for item in profile.evidence} == {
        EvidenceProvenance.EXPLICIT_STATE,
        EvidenceProvenance.EXTRACTED,
    }


def test_authoritative_conflict_projection_preserves_each_canonical_value() -> None:
    for state in ("active", "cooling", "repairing", "resolved"):
        memory = _memory_item(
            f"conflict-{state}",
            MemoryKind.RELATIONSHIP_STATE,
            f"当前冲突状态为 {state}",
            {"state_dimension": "conflict_status", "state_value": state},
        )

        profile = project_relationship_evidence([memory], reference_time=NOW)
        projection = profile.projection_for(RelationshipEvidenceDimension.CONFLICT)

        assert profile.conflict_status == state
        assert projection is not None
        assert projection.state == state


def test_confirmed_canonical_state_precedes_newer_proposed_state() -> None:
    confirmed = _memory_item(
        "confirmed-resolved",
        MemoryKind.RELATIONSHIP_STATE,
        "双方冲突已经解决",
        {"state_dimension": "conflict_status", "state_value": "resolved"},
        status=MemoryStatus.CONFIRMED,
        updated_at=NOW - timedelta(minutes=1),
    )
    proposed = _memory_item(
        "proposed-active",
        MemoryKind.RELATIONSHIP_STATE,
        "模型推测双方仍有冲突",
        {"state_dimension": "conflict_status", "state_value": "active"},
        status=MemoryStatus.PROPOSED,
        updated_at=NOW,
    )

    profile = project_relationship_evidence([confirmed, proposed], reference_time=NOW)

    assert profile.conflict_status == "resolved"


def test_normalization_discards_invalid_evidence_and_caps_confidence() -> None:
    normalized = normalize_evidence_declarations(
        [
            {
                "dimension": "trust_access",
                "direction": "positive",
                "strength": 0.7,
                "confidence": 0.99,
                "rationale": "private_access",
            },
            {
                "dimension": "unsupported_dimension",
                "direction": "support",
                "strength": 0.8,
            },
        ],
        claim_confidence=0.8,
    )

    assert normalized == [
        {
            "dimension": "trust",
            "direction": "support",
            "strength": 0.7,
            "confidence": 0.8,
            "rationale": "private_access",
        }
    ]
    zero_confidence = normalize_evidence_declarations(
        [
            {
                "dimension": "boundary",
                "direction": "support",
                "strength": 0.5,
                "confidence": 0.0,
                "rationale": "uncertain_boundary",
            }
        ],
        claim_confidence=0.8,
    )
    assert zero_confidence[0]["confidence"] == 0.0


def test_memory_normalization_persists_only_valid_evidence_declarations() -> None:
    claim = AtomicClaim.model_validate(
        {
            "claim_id": "normalized-evidence",
            "kind": "interaction_event",
            "subject": "relationship",
            "predicate": "shared_private_meal",
            "summary": "双方在家中共同吃饭",
            "evidence_spans": ["双方在家中共同吃饭"],
            "confidence": 0.8,
            "relationship_evidence": [
                {
                    "dimension": "trust",
                    "direction": "support",
                    "strength": 0.75,
                    "confidence": 0.95,
                    "rationale": "private_access",
                },
                {
                    "dimension": "invalid",
                    "direction": "support",
                    "strength": 0.8,
                },
            ],
        }
    )

    normalized = normalize_memory_candidate(claim.to_candidate(), NOW)

    assert normalized.payload["relationship_evidence"] == [
        {
            "dimension": "trust",
            "direction": "support",
            "strength": 0.75,
            "confidence": 0.8,
            "rationale": "private_access",
        }
    ]


def test_projected_evidence_calibrates_policy_without_generic_waiting() -> None:
    profile = project_relationship_evidence(
        [
            _memory_item(
                "policy-home-dinner",
                MemoryKind.INTERACTION_EVENT,
                "对方到用户家中吃饭并积极回应",
                {"activity_type": "home_dinner"},
                impact=RelationshipImpact.IMPROVING,
            )
        ],
        reference_time=NOW,
    )
    policy = default_scenario_policy_registry().resolve(
        AdviceScenario.PURSUIT,
        [],
        AdviceGoal.PROGRESS,
    )
    context = RelationshipContext(user_id="u1", relationship_evidence=profile)
    response = AdviceResponse(
        scenario=AdviceScenario.PURSUIT,
        goal=AdviceGoal.PROGRESS,
        problem_summary="用户想邀请对方吃饭。",
        assessment="双方已有一定信任基础，可以提出一次低压力邀请。",
        recommended_actions=["询问她是否愿意一起吃饭，并给她轻松拒绝的空间。"],
    )

    enforced = enforce_scenario_policy(
        response,
        policy,
        "我想约她出来吃饭谈谈。",
        context,
    )

    assert enforced.recommended_actions == response.recommended_actions
    assert not any("观察对方是否也会主动" in item for item in enforced.recommended_actions)
    instructions = hard_constraint_instructions(policy, context)
    assert any("不要把双方重新当作陌生人" in item for item in instructions)


def test_active_conflict_projection_enables_deescalation_constraint() -> None:
    profile = project_relationship_evidence(
        [
            _memory_item(
                "active-conflict",
                MemoryKind.INTERACTION_EVENT,
                "双方正在争执",
                {"activity_type": "conflict"},
            )
        ],
        reference_time=NOW,
    )
    policy = default_scenario_policy_registry().resolve(
        AdviceScenario.CONFLICT,
        [],
        AdviceGoal.REPAIR,
    )
    context = RelationshipContext(user_id="u1", relationship_evidence=profile)
    response = AdviceResponse(
        scenario=AdviceScenario.CONFLICT,
        goal=AdviceGoal.REPAIR,
        problem_summary="用户准备修复一次分歧。",
        assessment="可以先确认对方的沟通意愿。",
        recommended_actions=["询问她是否愿意找个安静时间谈谈。"],
    )

    enforced = enforce_scenario_policy(
        response,
        policy,
        "我们仍在争执。",
        context,
    )

    assert profile.conflict_status == "active"
    assert enforced.recommended_actions[0].startswith("先暂停争论")


def _memory_item(
    item_id: str,
    kind: MemoryKind,
    summary: str,
    payload: dict[str, object],
    *,
    impact: RelationshipImpact = RelationshipImpact.UNCLEAR,
    source_message_id: str | None = None,
    status: MemoryStatus = MemoryStatus.CONFIRMED,
    updated_at: datetime = NOW,
) -> MemoryItem:
    return MemoryItem(
        id=item_id,
        user_id="u1",
        relationship_id="r1",
        source_message_id=source_message_id or f"message-{item_id}",
        dedupe_key=f"key-{item_id}",
        kind=kind,
        subject="relationship",
        summary=summary,
        original_text=summary,
        evidence_spans=[summary],
        time_kind=TimeKind.TIMELESS,
        valence=MemoryValence.NEUTRAL,
        relationship_impact=impact,
        perspective=MemoryPerspective.USER_REPORTED,
        confidence=0.9,
        status=status,
        payload=payload,
        created_at=updated_at,
        updated_at=updated_at,
    )

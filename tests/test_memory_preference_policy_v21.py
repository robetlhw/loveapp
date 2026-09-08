from datetime import UTC, datetime

from loveapp.application.memory_relations import resolve_claim_relation
from loveapp.domain.memory import (
    AdmissionDecision,
    EvidenceExplicitness,
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    PredicateType,
    TimeKind,
    memory_dedupe_identity,
    memory_dedupe_key,
    normalize_candidate_predicate,
)
from loveapp.domain.memory_predicates import (
    CANONICAL_PREDICATES,
    CanonicalPredicateSpec,
    PredicateCardinality,
    PredicateTemporalBehavior,
    PredicateUpdatePolicy,
    normalize_predicate,
    predicate_spec,
)

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)


def _preference(
    value: str,
    *,
    canonical: str,
    preference_type: str,
) -> MemoryCandidate:
    candidate = MemoryCandidate(
        kind=MemoryKind.PREFERENCE,
        subject="partner",
        summary=f"preference:{value}",
        original_text=f"partner preference {value}",
        evidence_spans=[value],
        time_kind=TimeKind.TIMELESS,
        confidence=0.95,
        perspective=MemoryPerspective.USER_REPORTED,
        explicitness=EvidenceExplicitness.EXPLICIT,
        payload={
            "preference": value,
            "preference_type": preference_type,
        },
        raw_predicate=canonical,
        predicate_type=PredicateType.CANONICAL,
        canonical_predicate=canonical,
        admission_score=0.95,
        admission_decision=AdmissionDecision.CONFIRM,
    )
    return normalize_candidate_predicate(candidate)


def _item(memory_id: str, candidate: MemoryCandidate, *, status: MemoryStatus) -> MemoryItem:
    return MemoryItem(
        **candidate.model_dump(),
        id=memory_id,
        user_id="preference-policy-user",
        relationship_id="preference-policy-relationship",
        status=status,
        source_message_id=f"source-{memory_id}",
        created_at=NOW,
        updated_at=NOW,
        dedupe_key=memory_dedupe_key(candidate),
    )


def test_environment_noise_is_registered_with_environment_domain() -> None:
    spec = predicate_spec("preference.environment.noise")

    assert spec is not None
    assert spec.semantic_domain == "environment"
    normalized = normalize_predicate(
        kind=MemoryKind.PREFERENCE,
        raw_predicate="preference.environment.noise",
        canonical_predicate="preference.environment.noise",
        predicate_type=PredicateType.CANONICAL,
        payload={"preference": "安静", "preference_type": "noise"},
    )
    assert normalized.predicate_type == PredicateType.CANONICAL.value
    assert normalized.canonical_predicate == "preference.environment.noise"


def test_preference_relation_uses_registry_single_replace_policy() -> None:
    old = _item(
        "quiet",
        _preference(
            "安静",
            canonical="preference.environment.noise",
            preference_type="noise",
        ),
        status=MemoryStatus.CONFIRMED,
    )
    incoming = _preference(
        "热闹",
        canonical="preference.environment.noise",
        preference_type="noise",
    )

    resolution = resolve_claim_relation(
        incoming,
        [old],
        incoming_status=MemoryStatus.CONFIRMED,
    )

    assert resolution.relation.value == "update"
    assert resolution.target_memory_ids == (old.id,)
    assert resolution.rule_name == "single_value_preference_dimension"
    assert resolution.diagnostics["cardinality"] == "single"
    assert resolution.diagnostics["update_policy"] == "replace"


def test_preference_relation_does_not_fallback_to_hardcoded_predicate_list(monkeypatch) -> None:
    predicate = "preference.test.single_setting"
    monkeypatch.setitem(
        CANONICAL_PREDICATES,
        predicate,
        CanonicalPredicateSpec(
            name=predicate,
            semantic_domain="test",
            cardinality=PredicateCardinality.SINGLE,
            temporal_behavior=PredicateTemporalBehavior.TIMELESS,
            update_policy=PredicateUpdatePolicy.REPLACE,
        ),
    )
    old = _item(
        "old",
        _preference("first", canonical=predicate, preference_type="test"),
        status=MemoryStatus.CONFIRMED,
    )
    incoming = _preference("second", canonical=predicate, preference_type="test")

    resolution = resolve_claim_relation(
        incoming,
        [old],
        incoming_status=MemoryStatus.CONFIRMED,
    )

    assert resolution.relation.value == "update"
    assert resolution.target_memory_ids == (old.id,)
    assert resolution.rule_name == "single_value_preference_dimension"


def test_preference_registry_protected_policy_fails_closed(monkeypatch) -> None:
    predicate = "preference.test.protected_setting"
    monkeypatch.setitem(
        CANONICAL_PREDICATES,
        predicate,
        CanonicalPredicateSpec(
            name=predicate,
            semantic_domain="test",
            cardinality=PredicateCardinality.SINGLE,
            temporal_behavior=PredicateTemporalBehavior.TIMELESS,
            update_policy=PredicateUpdatePolicy.NONE,
        ),
    )
    old = _item(
        "old",
        _preference("first", canonical=predicate, preference_type="test"),
        status=MemoryStatus.CONFIRMED,
    )
    incoming = _preference("second", canonical=predicate, preference_type="test")

    resolution = resolve_claim_relation(
        incoming,
        [old],
        incoming_status=MemoryStatus.CONFIRMED,
    )

    assert resolution.relation.value == "uncertain"
    assert resolution.target_memory_ids == ()
    assert resolution.rule_name == "preference_update_policy_protected"


def test_protected_preference_policy_also_blocks_polarity_replacement(monkeypatch) -> None:
    predicate = "preference.test.protected_polarity"
    monkeypatch.setitem(
        CANONICAL_PREDICATES,
        predicate,
        CanonicalPredicateSpec(
            name=predicate,
            semantic_domain="test",
            cardinality=PredicateCardinality.MULTI,
            temporal_behavior=PredicateTemporalBehavior.TIMELESS,
            update_policy=PredicateUpdatePolicy.NONE,
        ),
    )
    old = _item(
        "old",
        _preference("same", canonical=predicate, preference_type="like"),
        status=MemoryStatus.CONFIRMED,
    )
    incoming = _preference("same", canonical=predicate, preference_type="dislike")

    resolution = resolve_claim_relation(
        incoming,
        [old],
        incoming_status=MemoryStatus.CONFIRMED,
    )

    assert resolution.relation.value == "uncertain"
    assert resolution.target_memory_ids == ()
    assert resolution.rule_name == "preference_update_policy_protected"


def test_v21_preference_category_aliases_are_bounded() -> None:
    expected = {
        "personal_interest": "preference.personal_interest.topic",
        "consumption": "preference.consumption.item",
        "lifestyle": "preference.lifestyle.habit",
        "relationship": "preference.relationship.partner_trait",
        "communication": "preference.communication.style",
        "value": "preference.value.priority",
    }
    for preference_type, canonical in expected.items():
        normalized = normalize_predicate(
            kind=MemoryKind.PREFERENCE,
            raw_predicate="preference.general",
            canonical_predicate="preference.general",
            predicate_type=PredicateType.CANONICAL,
            payload={
                "preference": "example",
                "preference_type": preference_type,
            },
        )
        assert normalized.canonical_predicate == canonical
        assert normalized.predicate_type == PredicateType.CANONICAL.value


def test_v21_domain_dimension_shape_maps_without_free_form_predicate() -> None:
    normalized = normalize_predicate(
        kind=MemoryKind.PREFERENCE,
        raw_predicate="preference.general",
        predicate_type=PredicateType.CANONICAL,
        payload={
            "domain": "relationship",
            "dimension": "partner_trait",
            "value": "mature",
            "preference_type": "like",
        },
    )

    assert normalized.predicate_type == PredicateType.CANONICAL.value
    assert normalized.canonical_predicate == "preference.relationship.partner_trait"


def test_v21_environment_domain_alone_does_not_imply_noise_dimension() -> None:
    normalized = normalize_predicate(
        kind=MemoryKind.PREFERENCE,
        raw_predicate="preference.general",
        canonical_predicate="preference.general",
        predicate_type=PredicateType.CANONICAL,
        payload={
            "domain": "environment",
            "value": "户外环境",
            "preference_type": "like",
        },
    )

    assert normalized.predicate_type == PredicateType.CUSTOM.value
    assert normalized.canonical_predicate is None
    assert normalized.custom_predicate == "preference.general"


def test_v21_relationship_domain_alone_does_not_imply_partner_trait_dimension() -> None:
    normalized = normalize_predicate(
        kind=MemoryKind.PREFERENCE,
        raw_predicate="preference.general",
        canonical_predicate="preference.general",
        predicate_type=PredicateType.CANONICAL,
        payload={
            "domain": "relationship",
            "value": "长期关系",
            "preference_type": "like",
        },
    )

    assert normalized.predicate_type == PredicateType.CUSTOM.value
    assert normalized.canonical_predicate is None
    assert normalized.custom_predicate == "preference.general"


def test_v21_flat_preference_shape_preserves_value_in_dedupe_identity() -> None:
    base = {
        "type": "preference",
        "subject": "partner",
        "summary": "对方偏好成熟稳重的伴侣",
        "original_text": "她喜欢成熟稳重的男生",
        "domain": "relationship",
        "dimension": "partner_trait",
        "predicate_type": "canonical",
        "canonical_predicate": "preference.relationship.partner_trait",
    }
    mature = normalize_candidate_predicate(
        MemoryCandidate.model_validate({**base, "value": "mature"})
    )
    humorous = normalize_candidate_predicate(
        MemoryCandidate.model_validate({**base, "value": "humorous"})
    )

    assert mature.payload["preference"] == "mature"
    assert mature.payload["domain"] == "relationship"
    assert mature.payload["dimension"] == "partner_trait"
    assert memory_dedupe_identity(mature) != memory_dedupe_identity(humorous)

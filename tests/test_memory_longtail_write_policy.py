from datetime import UTC, datetime, timedelta

from loveapp.application.memory_relations import resolve_claim_relation
from loveapp.application.memory_retrieval import MemoryRetrievalScore, RetrievedMemory
from loveapp.application.memory_semantic_relations import _relation_rank
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
    predicate_spec,
)

NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)


def _canonical_candidate(
    predicate: str,
    value: str | None,
    *,
    kind: MemoryKind = MemoryKind.STABLE_FACT,
    subject: str = "partner",
    payload: dict[str, object] | None = None,
    occurred_at: datetime | None = None,
    confidence: float = 0.95,
) -> MemoryCandidate:
    details = {"predicate": predicate}
    if value is not None:
        details["state_value"] = value
        details["object"] = value
    if payload:
        details.update(payload)
    candidate = MemoryCandidate(
        kind=kind,
        subject=subject,
        summary=f"{predicate}:{value or 'none'}",
        original_text=f"{predicate}:{value or 'none'}",
        evidence_spans=[f"{predicate}:{value or 'none'}"],
        time_kind=TimeKind.POINT if occurred_at else TimeKind.TIMELESS,
        occurred_at=occurred_at,
        confidence=confidence,
        perspective=MemoryPerspective.USER_REPORTED,
        explicitness=EvidenceExplicitness.EXPLICIT,
        payload=details,
        raw_predicate=predicate,
        predicate_type=PredicateType.CANONICAL,
        canonical_predicate=predicate,
        admission_score=0.95,
        admission_decision=AdmissionDecision.CONFIRM,
    )
    return normalize_candidate_predicate(candidate)


def _memory(
    memory_id: str,
    candidate: MemoryCandidate,
    *,
    status: MemoryStatus = MemoryStatus.CONFIRMED,
) -> MemoryItem:
    return MemoryItem(
        **candidate.model_dump(),
        id=memory_id,
        user_id="policy-user",
        relationship_id="policy-relationship",
        status=status,
        source_message_id=f"source-{memory_id}",
        created_at=NOW - timedelta(days=2),
        updated_at=NOW - timedelta(days=1),
        dedupe_key=memory_dedupe_key(candidate),
    )


def test_canonical_predicate_schema_metadata_is_exposed() -> None:
    spec = predicate_spec("interaction.response_engagement")

    assert spec is not None
    assert spec.cardinality == PredicateCardinality.SINGLE
    assert spec.temporal_behavior == PredicateTemporalBehavior.PATTERN
    assert spec.update_policy == PredicateUpdatePolicy.REPLACE
    assert predicate_spec("not_registered_custom_predicate") is None


def test_interaction_pattern_state_value_is_part_of_dedupe_identity() -> None:
    low = _canonical_candidate(
        "interaction.contact_frequency",
        "low",
        kind=MemoryKind.INTERACTION_PATTERN,
        payload={"metric": "contact_frequency"},
    )
    normal = _canonical_candidate(
        "interaction.contact_frequency",
        "normal",
        kind=MemoryKind.INTERACTION_PATTERN,
        payload={"metric": "contact_frequency"},
    )

    assert memory_dedupe_identity(low) != memory_dedupe_identity(normal)
    assert memory_dedupe_key(low) != memory_dedupe_key(normal)


def test_single_value_schema_returns_update_for_confirmed_incoming(monkeypatch) -> None:
    monkeypatch.setitem(
        CANONICAL_PREDICATES,
        "test.current_setting",
        CanonicalPredicateSpec(
            name="test.current_setting",
            allowed_values=frozenset({"old", "new"}),
            cardinality=PredicateCardinality.SINGLE,
            temporal_behavior=PredicateTemporalBehavior.CURRENT,
            update_policy=PredicateUpdatePolicy.REPLACE,
        ),
    )
    old = _memory("old", _canonical_candidate("test.current_setting", "old"))
    incoming = _canonical_candidate("test.current_setting", "new")

    resolution = resolve_claim_relation(
        incoming,
        [old],
        incoming_status=MemoryStatus.CONFIRMED,
    )

    assert resolution.relation.value == "update"
    assert resolution.target_memory_ids == ("old",)
    assert resolution.rule_name == "canonical_single_value_update"
    assert resolution.diagnostics["cardinality"] == "single"


def test_single_value_schema_protects_confirmed_value_from_proposed_incoming(monkeypatch) -> None:
    monkeypatch.setitem(
        CANONICAL_PREDICATES,
        "test.protected_setting",
        CanonicalPredicateSpec(
            name="test.protected_setting",
            allowed_values=frozenset({"old", "new"}),
            cardinality=PredicateCardinality.SINGLE,
            temporal_behavior=PredicateTemporalBehavior.CURRENT,
            update_policy=PredicateUpdatePolicy.REPLACE,
        ),
    )
    old = _memory("old", _canonical_candidate("test.protected_setting", "old"))
    incoming = _canonical_candidate("test.protected_setting", "new")

    resolution = resolve_claim_relation(
        incoming,
        [old],
        incoming_status=MemoryStatus.PROPOSED,
    )

    assert resolution.relation.value == "contradiction"
    assert resolution.target_memory_ids == ("old",)
    assert resolution.rule_name == "canonical_single_value_conflict"


def test_multi_value_schema_keeps_different_values_complementary(monkeypatch) -> None:
    monkeypatch.setitem(
        CANONICAL_PREDICATES,
        "test.multi_setting",
        CanonicalPredicateSpec(
            name="test.multi_setting",
            allowed_values=frozenset({"first", "second"}),
            cardinality=PredicateCardinality.MULTI,
            temporal_behavior=PredicateTemporalBehavior.TIMELESS,
            update_policy=PredicateUpdatePolicy.APPEND,
        ),
    )
    old = _memory("old", _canonical_candidate("test.multi_setting", "first"))
    incoming = _canonical_candidate("test.multi_setting", "second")

    resolution = resolve_claim_relation(
        incoming,
        [old],
        incoming_status=MemoryStatus.CONFIRMED,
    )

    assert resolution.relation.value == "complementary"
    assert resolution.target_memory_ids == ("old",)
    assert resolution.rule_name == "canonical_multi_value"


def test_none_update_policy_fails_closed_instead_of_returning_update(monkeypatch) -> None:
    monkeypatch.setitem(
        CANONICAL_PREDICATES,
        "test.immutable_setting",
        CanonicalPredicateSpec(
            name="test.immutable_setting",
            allowed_values=frozenset({"old", "new"}),
            cardinality=PredicateCardinality.SINGLE,
            temporal_behavior=PredicateTemporalBehavior.CURRENT,
            update_policy=PredicateUpdatePolicy.NONE,
        ),
    )
    old = _memory("old", _canonical_candidate("test.immutable_setting", "old"))
    incoming = _canonical_candidate("test.immutable_setting", "new")

    resolution = resolve_claim_relation(
        incoming,
        [old],
        incoming_status=MemoryStatus.CONFIRMED,
    )

    assert resolution.relation.value == "uncertain"
    assert resolution.target_memory_ids == ()
    assert resolution.rule_name == "canonical_update_policy_protected"


def test_event_and_pattern_with_same_predicate_are_complementary() -> None:
    target = _memory(
        "pattern",
        _canonical_candidate(
            "interaction.response_engagement",
            "slow",
            kind=MemoryKind.INTERACTION_PATTERN,
            payload={"metric": "response_engagement", "current": "slow"},
        ),
    )
    incoming = _canonical_candidate(
        "interaction.response_engagement",
        "slow",
        kind=MemoryKind.INTERACTION_EVENT,
        payload={"metric": "response_engagement", "current": "slow"},
        occurred_at=NOW,
    )

    resolution = resolve_claim_relation(
        incoming,
        [target],
        incoming_status=MemoryStatus.CONFIRMED,
    )

    assert resolution.relation.value == "complementary"
    assert resolution.target_memory_ids == ("pattern",)
    assert resolution.rule_name == "event_pattern_boundary"


def test_relation_ranking_prefers_exact_predicate_before_embedding_score() -> None:
    incoming = _canonical_candidate(
        "test.target",
        None,
        kind=MemoryKind.INTERACTION_PATTERN,
        payload={"metric": "response_engagement"},
    ).model_copy(
        update={
            "predicate_type": PredicateType.CUSTOM,
            "canonical_predicate": None,
            "custom_predicate": "test.target",
            "raw_predicate": "test.target",
        }
    )
    exact = _memory(
        "exact",
        incoming.model_copy(
            update={"payload": {"predicate": "test.target", "metric": "response_engagement"}}
        ),
    )
    distractor = _memory(
        "distractor",
        incoming.model_copy(
            update={
                "payload": {"predicate": "other.signal", "metric": "other_metric"},
                "raw_predicate": "other.signal",
                "custom_predicate": "other.signal",
            }
        ),
    )
    exact_result = RetrievedMemory(
        item=exact,
        score=MemoryRetrievalScore(
            semantic_similarity=0.1,
            predicate_match=0.1,
            recency=0.5,
            importance=0.5,
            confidence=0.5,
            lifecycle_priority=0.5,
            total=0.1,
        ),
        retrieval_text=exact.summary,
    )
    distractor_result = RetrievedMemory(
        item=distractor,
        score=MemoryRetrievalScore(
            semantic_similarity=0.99,
            predicate_match=0.9,
            recency=0.9,
            importance=0.9,
            confidence=0.9,
            lifecycle_priority=0.9,
            total=0.99,
        ),
        retrieval_text=distractor.summary,
    )

    assert _relation_rank(incoming, exact_result) < _relation_rank(
        incoming, distractor_result
    )


def test_canonical_relation_diagnostics_explain_rejected_candidates(monkeypatch) -> None:
    monkeypatch.setitem(
        CANONICAL_PREDICATES,
        "test.diagnostic_setting",
        CanonicalPredicateSpec(
            name="test.diagnostic_setting",
            allowed_values=frozenset({"old", "new"}),
            cardinality=PredicateCardinality.SINGLE,
            temporal_behavior=PredicateTemporalBehavior.CURRENT,
            update_policy=PredicateUpdatePolicy.REPLACE,
        ),
    )
    matching = _memory(
        "matching",
        _canonical_candidate("test.diagnostic_setting", "old"),
    )
    mismatched = _memory(
        "mismatched",
        _canonical_candidate("test.diagnostic_setting", "old", subject="user"),
    )
    incoming = _canonical_candidate("test.diagnostic_setting", "new")

    resolution = resolve_claim_relation(
        incoming,
        [matching, mismatched],
        incoming_status=MemoryStatus.CONFIRMED,
    )

    assert resolution.relation.value == "update"
    assert resolution.diagnostics["decision_tree"] == [
        "subject",
        "predicate",
        "schema",
        "value",
        "relation",
    ]
    assert resolution.diagnostics["semantic_candidates"] == ["matching"]
    assert {item["memory_id"] for item in resolution.diagnostics["rejected_candidates"]} == {
        "mismatched"
    }

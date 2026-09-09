"""Focused contracts introduced by the v3.2 ontology freeze.

These tests intentionally exercise only the domain boundary.  Runtime
extractor selection and mutation governance are covered by their own suites.
"""

import pytest

from loveapp.domain.memory import (
    CoarseExtraction,
    CoarseProposition,
    MemoryCandidate,
    MemoryExtractionMode,
    MemoryKind,
    SemanticRole,
)
from loveapp.domain.memory_dimensions import (
    normalize_interaction_event_payload,
    normalize_state_dimension,
    normalize_state_value,
)


def test_extraction_mode_and_semantic_role_are_not_mutation_commands() -> None:
    assert MemoryExtractionMode.SINGLE_STAGE.value == "single_stage"
    assert MemoryExtractionMode.TWO_STAGE.value == "two_stage"
    assert SemanticRole.CONTEXTUAL_COMPLETION.value == "contextual_completion"


def test_coarse_proposition_allows_ambiguous_kind_routing() -> None:
    proposition = CoarseProposition(
        proposition_id="p1",
        evidence_span="她最近回复越来越慢",
        candidate_kinds=[MemoryKind.INTERACTION_PATTERN, MemoryKind.RELATIONSHIP_STATE],
        semantic_role=SemanticRole.STANDALONE_PROPOSITION,
        target_semantic_hint={"temporal_relation": "recent"},
    )
    assert proposition.candidate_kinds == [
        MemoryKind.INTERACTION_PATTERN,
        MemoryKind.RELATIONSHIP_STATE,
    ]
    assert CoarseExtraction(should_extract=True, propositions=[proposition]).propositions


def test_coarse_proposition_rejects_database_target_and_mutation_hints() -> None:
    with pytest.raises(ValueError, match="database targets"):
        CoarseProposition(
            proposition_id="p1",
            evidence_span="恢复正常",
            candidate_kinds=[MemoryKind.RELATIONSHIP_STATE],
            target_semantic_hint={"target_memory_id": "m1"},
        )


def test_relationship_stage_contract_uses_dotted_persisted_dimension_and_aliases() -> None:
    assert normalize_state_dimension("relationship_stage") == "relationship.stage"
    assert normalize_state_value("relationship_stage", "partnered") == "dating"
    assert normalize_state_value("relationship.stage", "ordinary_friends") == "acquaintance"


def test_first_occurrence_is_a_marker_without_forcing_unknown_event_type() -> None:
    payload = normalize_interaction_event_payload(
        {"action": "第一次正式约会"},
        evidence_text="昨天我们第一次正式约会",
    )
    assert payload["event_type"] == "date"
    assert payload["event_markers"] == ["first_occurrence"]


def test_event_state_link_fields_round_trip_through_candidate_payload() -> None:
    candidate = MemoryCandidate(
        kind=MemoryKind.RELATIONSHIP_STATE,
        subject="relationship",
        summary="当前仍在冷战",
        original_text="我们现在还在冷战",
        evidence_spans=["现在还在冷战"],
        state_dimension="relationship.conflict_status",
        state_value="active",
        source_event_ids=["event-1"],
        supporting_event_ids=["event-1", "event-2"],
    )
    assert candidate.payload["source_event_ids"] == ["event-1"]
    assert candidate.payload["supporting_event_ids"] == ["event-1", "event-2"]
    assert candidate.source_event_ids == ["event-1"]

"""Regression contracts for Stage-2 semantic-role routing.

These tests exercise the semantic hand-off only.  They intentionally use a
fake model transport and never authorize a Store mutation.
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from loveapp.adapters.memory.stage2_routing import (
    SemanticRoleRouter,
    Stage2ExtractorRoute,
    route_semantic_role,
)
from loveapp.adapters.memory.two_stage import (
    TwoStageMemoryExtractor,
    _build_coarse_prompt,
    _build_detailed_system_prompt,
)
from loveapp.domain.memory import CoarseProposition, MemoryKind, SemanticRole
from loveapp.domain.memory_dimensions import normalize_event_severity
from loveapp.domain.memory_semantic_units import EnrichmentDraft, NewMemoryDraft

NOW = datetime(2026, 9, 10, tzinfo=UTC)


class _FakeCompletions:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        response = self.responses[len(self.calls) - 1]
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content=json.dumps(response, ensure_ascii=False)
                    ),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )


async def _close() -> None:
    return None


def _extractor(
    responses: list[dict[str, object]],
) -> tuple[TwoStageMemoryExtractor, _FakeCompletions]:
    completions = _FakeCompletions(responses)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        close=_close,
    )
    return (
        TwoStageMemoryExtractor(
            api_key=SecretStr("test"),
            base_url="https://example.invalid",
            model="flash-test",
            client=client,
        ),
        completions,
    )


def _proposition(
    role: SemanticRole,
    *,
    proposition_id: str = "p1",
    evidence: str = "证据",
    kinds: list[MemoryKind] | None = None,
    role_candidates: list[SemanticRole] | None = None,
) -> CoarseProposition:
    return CoarseProposition(
        proposition_id=proposition_id,
        evidence_span=evidence,
        candidate_kinds=kinds or [MemoryKind.INTERACTION_EVENT],
        semantic_role=role,
        semantic_role_candidates=role_candidates or [],
    )


def test_all_stage1_roles_have_bounded_stage2_routes() -> None:
    expected = {
        SemanticRole.NEW_PROPOSITION: Stage2ExtractorRoute.NEW_MEMORY,
        SemanticRole.ATTRIBUTE_COMPLETION: Stage2ExtractorRoute.EVENT_ENRICHMENT,
        SemanticRole.CONTEXTUAL_COMPLETION: Stage2ExtractorRoute.EVENT_ENRICHMENT,
        SemanticRole.REFINEMENT: Stage2ExtractorRoute.REFINEMENT,
        SemanticRole.STATE_UPDATE: Stage2ExtractorRoute.STATE,
        SemanticRole.PATTERN_EXTRACTION: Stage2ExtractorRoute.PATTERN,
        SemanticRole.BELIEF_EXTRACTION: Stage2ExtractorRoute.BELIEF,
        SemanticRole.UNCERTAIN: Stage2ExtractorRoute.UNCERTAIN,
        SemanticRole.STANDALONE_PROPOSITION: Stage2ExtractorRoute.NEW_MEMORY,
        SemanticRole.ATTRIBUTE_UPDATE: Stage2ExtractorRoute.EVENT_ENRICHMENT,
        SemanticRole.REFINEMENT_CANDIDATE: Stage2ExtractorRoute.REFINEMENT,
        SemanticRole.STATE_ASSERTION: Stage2ExtractorRoute.STATE,
    }
    for role, route in expected.items():
        assert route_semantic_role(role) == route


def test_role_router_preserves_multiple_role_hypotheses_and_fails_closed() -> None:
    proposition = _proposition(
        SemanticRole.STATE_UPDATE,
        role_candidates=[SemanticRole.STATE_UPDATE, SemanticRole.PATTERN_EXTRACTION],
    )

    route = SemanticRoleRouter().route(proposition)

    assert route.candidate_routes == (
        Stage2ExtractorRoute.STATE,
        Stage2ExtractorRoute.PATTERN,
    )
    assert route.selected_route == Stage2ExtractorRoute.UNCERTAIN
    assert route.reason == "multiple_semantic_routes_fail_closed"


def test_coarse_prompt_advertises_semantic_roles_without_write_authority() -> None:
    prompt = json.loads(
        _build_coarse_prompt(
            "最近我们的关系状态有变化",
            reference_time=NOW,
            existing_memories=[],
            conversation_history=[],
            pending_memory_context=None,
        )
    )
    contract = prompt["contract"]

    assert {
        "state_update",
        "pattern_extraction",
        "belief_extraction",
    } <= set(contract["semantic_roles"])
    assert contract["semantic_role_candidates_allowed"] is True
    assert {"CREATE", "ENRICH", "UPDATE"} <= set(contract["stage1_must_not_decide"])


def test_detailed_system_prompt_contains_only_semantic_route_instructions() -> None:
    routes = [
        SemanticRoleRouter().route(_proposition(SemanticRole.NEW_PROPOSITION)),
        SemanticRoleRouter().route(_proposition(SemanticRole.ATTRIBUTE_COMPLETION)),
        SemanticRoleRouter().route(_proposition(SemanticRole.STATE_UPDATE)),
        SemanticRoleRouter().route(_proposition(SemanticRole.PATTERN_EXTRACTION)),
        SemanticRoleRouter().route(_proposition(SemanticRole.BELIEF_EXTRACTION)),
    ]

    prompt = _build_detailed_system_prompt(routes)

    assert "NewMemoryExtractor" in prompt
    assert "EventEnrichmentExtractor" in prompt
    assert "StateExtractor" in prompt
    assert "PatternExtractor" in prompt
    assert "BeliefExtractor" in prompt
    assert "target_memory_id" in prompt
    assert "mutation_action" in prompt


@pytest.mark.asyncio
async def test_state_route_preserves_relationship_state_dimension_and_value() -> None:
    extractor, completions = _extractor(
        [
            {
                "should_extract": True,
                "gate_reason": "RELATIONSHIP_STATE",
                "propositions": [
                    {
                        "proposition_id": "p1",
                        "evidence_span": "她现在不理我了",
                        "candidate_kinds": ["relationship_state"],
                        "semantic_role": "state_update",
                    }
                ],
            },
            {
                "semantic_units": [
                    {
                        "semantic_type": "new_memory",
                        "claim_id": "p1",
                        "kind": "relationship_state",
                        "subject": "relationship",
                        "predicate": "has_state",
                        "summary": "她现在不理我了",
                        "evidence_spans": ["她现在不理我了"],
                        "semantic_payload": {
                            "state_dimension": "contact_availability",
                            "state_value": "unavailable",
                        },
                    }
                ]
            },
        ]
    )

    result = await extractor.extract(
        "她现在不理我了",
        reference_time=NOW,
        existing_memories=[],
        conversation_history=[],
    )

    assert len(completions.calls) == 2
    assert isinstance(result.semantic_units[0], NewMemoryDraft)
    assert result.claims[0].kind == MemoryKind.RELATIONSHIP_STATE
    assert result.claims[0].state_dimension == "contact_availability"
    assert result.claims[0].state_value == "unavailable"
    assert extractor.last_diagnostic["stage2"]["routes"][0]["selected_route"] == "state"
    await extractor.aclose()


@pytest.mark.asyncio
async def test_pattern_route_requires_and_preserves_registered_metric() -> None:
    extractor, _ = _extractor(
        [
            {
                "should_extract": True,
                "gate_reason": "INTERACTION_PATTERN",
                "propositions": [
                    {
                        "proposition_id": "p1",
                        "evidence_span": "最近都是我主动找她",
                        "candidate_kinds": ["interaction_pattern"],
                        "semantic_role": "pattern_extraction",
                    }
                ],
            },
            {
                "semantic_units": [
                    {
                        "semantic_type": "new_memory",
                        "claim_id": "p1",
                        "kind": "interaction_pattern",
                        "subject": "relationship",
                        "predicate": "initiation_balance",
                        "summary": "最近都是我主动找她",
                        "evidence_spans": ["最近都是我主动找她"],
                        "semantic_payload": {
                            "metric": "initiative_balance",
                            "current": "user_to_partner",
                        },
                    }
                ]
            },
        ]
    )

    result = await extractor.extract(
        "最近都是我主动找她",
        reference_time=NOW,
        existing_memories=[],
        conversation_history=[],
    )

    assert result.claims[0].payload["metric"] == "initiation_balance"
    await extractor.aclose()


@pytest.mark.asyncio
async def test_new_event_dominance_rejects_enrichment_from_new_occurrence() -> None:
    extractor, _ = _extractor(
        [
            {
                "should_extract": True,
                "gate_reason": "COMPOUND_MEMORY",
                "propositions": [
                    {
                        "proposition_id": "p1",
                        "evidence_span": "今天我们又吵了一架",
                        "candidate_kinds": ["interaction_event"],
                        "semantic_role": "new_proposition",
                    }
                ],
            },
            {
                "semantic_units": [
                    {
                        "semantic_type": "enrichment",
                        "unit_id": "p1",
                        "target_kind": "interaction_event",
                        "target_semantic_hint": {"event_type": "conflict"},
                        "attribute_namespace": "canonical",
                        "attribute_name": "cause",
                        "value": {"category": "financial_values"},
                        "evidence_span": "今天我们又吵了一架",
                        "confidence": 0.95,
                    }
                ]
            },
        ]
    )

    result = await extractor.extract(
        "今天我们又吵了一架",
        reference_time=NOW,
        existing_memories=[],
        conversation_history=[],
    )

    assert result.claims == []
    assert getattr(result, "semantic_units", []) == []
    assert extractor.last_diagnostic["fallback"]["reason_code"] == "STAGE2_ROLE_MISMATCH"
    await extractor.aclose()


def test_severity_normalizer_maps_raw_labels_to_existing_contract() -> None:
    assert normalize_event_severity("轻微") == "low"
    assert normalize_event_severity("比较严重") == "moderate"
    assert normalize_event_severity("特别严重") == "high"
    assert normalize_event_severity("5") == 5
    assert normalize_event_severity("无法判断") is None


def test_enrichment_draft_normalizes_raw_severity_and_keeps_provenance_hint() -> None:
    draft = EnrichmentDraft(
        unit_id="e1",
        target_kind=MemoryKind.INTERACTION_EVENT,
        target_semantic_hint={"event_type": "conflict"},
        attribute_namespace="canonical",
        attribute_name="severity",
        value="特别严重",
        evidence_span="这次特别严重",
    )

    assert draft.value == "high"
    assert draft.target_semantic_hint["raw_severity"] == "特别严重"


def test_route_for_legacy_string_aliases_is_conservative() -> None:
    assert route_semantic_role("state") == Stage2ExtractorRoute.STATE
    assert route_semantic_role("pattern") == Stage2ExtractorRoute.PATTERN
    assert route_semantic_role("not-a-role") == Stage2ExtractorRoute.UNCERTAIN

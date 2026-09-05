import json

import pytest

from loveapp.application.memory_repair import MemoryResponseError, parse_memory_response
from loveapp.domain.memory import PredicateType, RelationshipImpact


def _parse_claim(claim: dict[str, object], source_text: str):
    return parse_memory_response(
        json.dumps(
            {"claims": [claim], "discarded_spans": []},
            ensure_ascii=False,
        ),
        source_text=source_text,
    )


def _interaction_claim(**updates: object) -> dict[str, object]:
    claim: dict[str, object] = {
        "claim_id": "contact-initiation",
        "kind": "interaction_pattern",
        "subject": "partner",
        "predicate": "initiates_contact",
        "predicate_type": "custom",
        "canonical_predicate": "interaction.initiation_balance",
        "custom_predicate": "initiation_balance",
        "summary": "最近一个月，对方基本不主动联系用户。",
        "evidence_spans": ["最近一个月她基本都不主动找我了。"],
        "payload": {
            "metric": "initiation_balance",
            "direction": "decreasing",
            "current": "low",
        },
    }
    claim.update(updates)
    return claim


def _belief_claim(evidence_spans: list[object]) -> dict[str, object]:
    return {
        "claim_id": "relationship-belief",
        "kind": "stable_fact",
        "subject": "user",
        "predicate": "worried_partner_may_not_want_relationship",
        "predicate_type": "custom",
        "custom_predicate": "worried_partner_may_not_want_relationship",
        "summary": "用户担心对方可能不想继续这段关系。",
        "evidence_spans": evidence_spans,
        "perspective": "user_belief",
        "payload": {},
    }


def test_registered_canonical_reconciles_compatible_custom_declaration() -> None:
    source_text = "最近一个月她基本都不主动找我了。"

    parsed = _parse_claim(_interaction_claim(), source_text)

    claim = parsed.extraction.claims[0]
    assert claim.predicate_type == PredicateType.CANONICAL
    assert claim.canonical_predicate == "interaction.initiation_balance"
    assert claim.custom_predicate is None
    assert "canonical_custom_predicate_reconciliation" in parsed.repair_steps


def test_registered_canonical_does_not_hide_conflicting_custom_declaration() -> None:
    source_text = "最近一个月她基本都不主动找我了。"
    claim = _interaction_claim(
        canonical_predicate="interaction.contact_frequency",
    )

    with pytest.raises(MemoryResponseError) as exc_info:
        _parse_claim(claim, source_text)

    assert "canonical_custom_predicate_reconciliation" not in exc_info.value.repair_steps
    assert "不能同时提供 canonical 和 custom predicate" in str(exc_info.value)


def test_structured_evidence_span_is_narrowed_to_exact_text() -> None:
    source_text = "我越来越担心她其实不太想继续这段关系，但她没有明确这么说过。"
    evidence = "我越来越担心她其实不太想继续这段关系"
    claim = _belief_claim([{"text": evidence, "start": 0, "end": 20}])

    parsed = _parse_claim(claim, source_text)

    assert parsed.extraction.claims[0].evidence_spans == [evidence]
    assert "structured_evidence_text_narrowing" in parsed.repair_steps


def test_structured_evidence_offset_is_narrowed_to_exact_text() -> None:
    source_text = "我越来越担心她其实不太想继续这段关系。"
    evidence = "我越来越担心她"

    parsed = _parse_claim(_belief_claim([{"text": evidence, "offset": 0}]), source_text)

    assert parsed.extraction.claims[0].evidence_spans == [evidence]
    assert "structured_evidence_text_narrowing" in parsed.repair_steps


@pytest.mark.parametrize(
    "span",
    [
        {"text": "我越来越担心她", "label": "belief"},
        {"text": "我越来越担心她", "start": -1, "end": 7},
        {"text": "我越来越担心她", "start": 8, "end": 2},
        {"text": "我越来越担心她", "offset": -1},
    ],
)
def test_unbounded_structured_evidence_shape_remains_invalid(
    span: dict[str, object],
) -> None:
    source_text = "我越来越担心她其实不太想继续这段关系。"

    with pytest.raises(MemoryResponseError) as exc_info:
        _parse_claim(_belief_claim([span]), source_text)

    assert "structured_evidence_text_narrowing" not in exc_info.value.repair_steps


def test_narrowed_structured_evidence_must_still_be_in_source_text() -> None:
    source_text = "我越来越担心她其实不太想继续这段关系。"
    claim = _belief_claim([{"text": "她明确说不想继续", "start": 0, "end": 8}])

    with pytest.raises(MemoryResponseError) as exc_info:
        _parse_claim(claim, source_text)

    assert "structured_evidence_text_narrowing" in exc_info.value.repair_steps
    assert "证据片段不在用户原文中" in str(exc_info.value)


def test_relationship_impact_alias_is_normalized_without_changing_proposition() -> None:
    source_text = "昨天她把我介绍给她大学室友认识了。"
    claim = {
        "claim_id": "friend-introduction",
        "kind": "interaction_event",
        "subject": "partner",
        "predicate": "introduced_user_to_friends",
        "predicate_type": "custom",
        "custom_predicate": "introduced_user_to_friends",
        "summary": "对方把用户介绍给她的大学室友认识。",
        "evidence_spans": [source_text],
        "relationship_impact": "supportive",
    }

    parsed = _parse_claim(claim, source_text)

    assert parsed.extraction.claims[0].relationship_impact == RelationshipImpact.IMPROVING
    assert "enum_aliases" in parsed.repair_steps

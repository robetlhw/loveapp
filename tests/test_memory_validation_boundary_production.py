import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from loveapp.adapters.memory.openai_compatible import (
    OpenAICompatibleMemoryExtractor,
    TieredMemoryExtractor,
)
from loveapp.application.memory_repair import parse_memory_response
from loveapp.bootstrap import _build_memory_extractor
from loveapp.core.config import Settings
from loveapp.domain.memory import PredicateType
from loveapp.domain.memory_normalization import (
    NormalizationContractError,
    normalize_memory_candidate_contract,
)

REFERENCE_TIME = datetime(2026, 9, 2, 10, tzinfo=UTC)


def _parse_raw(claim: dict[str, object], source_text: str, *, reason: str):
    return parse_memory_response(
        json.dumps(
            {
                "should_extract": True,
                "gate_reason": reason,
                "claims": [claim],
                "discarded_spans": [],
            },
            ensure_ascii=False,
        ),
        source_text=source_text,
        validation_mode="raw",
    )


def _normalize_first(parsed):
    return normalize_memory_candidate_contract(
        parsed.extraction.claims[0].to_candidate(),
        REFERENCE_TIME,
    )


def _metric_hint_claim(source_text: str) -> dict[str, object]:
    return {
        "claim_id": "metric-hint",
        "kind": "interaction_pattern",
        "subject": "relationship",
        "raw_predicate": "partner_contact_initiation_decreased",
        "summary": "对方主动联系用户的频率降低",
        "evidence_spans": [source_text],
        "payload": {"metric_hint": "initiation_balance"},
    }


def test_raw_parser_preserves_metric_hint_until_contract_normalization() -> None:
    source = "她最近基本不主动联系我"

    parsed = _parse_raw(
        _metric_hint_claim(source),
        source,
        reason="INTERACTION_PATTERN",
    )

    claim = parsed.extraction.claims[0]
    assert parsed.validation_mode == "raw"
    assert claim.payload == {"metric_hint": "initiation_balance"}
    assert claim.canonical_predicate is None
    assert claim.custom_predicate is None
    assert "interaction_metric_hint" not in parsed.repair_steps

    normalized = _normalize_first(parsed)
    assert normalized.predicate_type == PredicateType.CANONICAL
    assert normalized.canonical_predicate == "interaction.initiation_balance"
    assert normalized.payload["metric"] == "initiation_balance"


def test_raw_parser_preserves_state_hints_until_contract_normalization() -> None:
    source = "我们现在还在冷战"
    claim = {
        "claim_id": "state-hint",
        "kind": "relationship_state",
        "subject": "relationship",
        "raw_predicate": "conflict_is_ongoing",
        "summary": "双方当前仍处于冷战状态",
        "evidence_spans": [source],
        "payload": {
            "state_dimension_hint": "relationship_conflict_status",
            "state_value_hint": "unresolved",
        },
    }

    parsed = _parse_raw(claim, source, reason="RELATIONSHIP_STATE")

    raw_claim = parsed.extraction.claims[0]
    assert raw_claim.state_dimension is None
    assert raw_claim.state_value is None
    assert "state_dimension" not in raw_claim.payload
    assert "state_value" not in raw_claim.payload
    assert "state_dimension_hint" not in parsed.repair_steps
    assert "state_value_hint" not in parsed.repair_steps

    normalized = _normalize_first(parsed)
    assert normalized.canonical_predicate == "relationship.conflict_status"
    assert normalized.state_dimension == "conflict_status"
    assert normalized.state_value == "active"


def test_open_state_survives_raw_parser_and_falls_back_to_custom() -> None:
    source = "继续这段关系的意愿正在下降"
    claim = {
        "claim_id": "open-state",
        "kind": "relationship_state",
        "subject": "relationship",
        "raw_predicate": "desire_to_continue",
        "summary": source,
        "evidence_spans": [source],
        "payload": {
            "state_dimension_hint": "desire_to_continue",
            "state_value_hint": "decreasing",
        },
    }

    parsed = _parse_raw(claim, source, reason="RELATIONSHIP_STATE")

    assert len(parsed.extraction.claims) == 1
    assert parsed.extraction.claims[0].payload == claim["payload"]
    normalized = _normalize_first(parsed)
    assert normalized.predicate_type == PredicateType.CUSTOM
    assert normalized.canonical_predicate is None
    assert normalized.custom_predicate == "desire_to_continue"
    assert normalized.state_dimension is None
    assert normalized.state_value is None


def test_raw_parser_does_not_reconcile_equivalent_predicate_declarations() -> None:
    source = "双方主动联系的平衡发生变化"
    claim = {
        "claim_id": "equivalent-declarations",
        "kind": "interaction_pattern",
        "subject": "relationship",
        "predicate": "initiation_balance",
        "predicate_type": "custom",
        "canonical_predicate": "interaction.initiation_balance",
        "custom_predicate": "initiation_balance",
        "summary": source,
        "evidence_spans": [source],
        "payload": {"metric": "initiation_balance"},
    }

    parsed = _parse_raw(claim, source, reason="INTERACTION_PATTERN")

    raw_claim = parsed.extraction.claims[0]
    assert raw_claim.canonical_predicate == "interaction.initiation_balance"
    assert raw_claim.custom_predicate == "initiation_balance"
    assert "canonical_custom_predicate_reconciliation" not in parsed.repair_steps

    normalized = _normalize_first(parsed)
    assert normalized.predicate_type == PredicateType.CANONICAL
    assert normalized.canonical_predicate == "interaction.initiation_balance"
    assert normalized.custom_predicate is None


def test_unrelated_predicate_declarations_fail_after_raw_acceptance() -> None:
    source = "双方主动联系的平衡发生变化"
    claim = {
        "claim_id": "conflicting-declarations",
        "kind": "interaction_pattern",
        "subject": "relationship",
        "predicate": "initiation_balance",
        "predicate_type": "canonical",
        "canonical_predicate": "interaction.initiation_balance",
        "custom_predicate": "unrelated_custom_semantic",
        "summary": source,
        "evidence_spans": [source],
        "payload": {"metric": "initiation_balance"},
    }

    parsed = _parse_raw(claim, source, reason="INTERACTION_PATTERN")

    assert len(parsed.extraction.claims) == 1
    with pytest.raises(NormalizationContractError) as exc_info:
        _normalize_first(parsed)
    assert exc_info.value.code == "CANONICAL_CUSTOM_CONFLICT"


def test_invalid_registered_state_is_rejected_after_raw_acceptance() -> None:
    source = "冲突状态被错误标成已婚"
    claim = {
        "claim_id": "invalid-state",
        "kind": "relationship_state",
        "subject": "relationship",
        "raw_predicate": "has_state",
        "summary": source,
        "evidence_spans": [source],
        "payload": {
            "state_dimension": "conflict_status",
            "state_value": "married",
        },
    }

    parsed = _parse_raw(claim, source, reason="RELATIONSHIP_STATE")

    assert len(parsed.extraction.claims) == 1
    with pytest.raises(NormalizationContractError) as exc_info:
        _normalize_first(parsed)
    assert exc_info.value.code in {"STATE_VALUE_INVALID", "UNKNOWN_STATE_DIMENSION"}


def test_bare_unknown_state_without_hint_or_custom_declaration_fails_closed() -> None:
    source = "未知关系状态维度被赋予高值"
    claim = {
        "claim_id": "bare-unknown-state",
        "kind": "relationship_state",
        "subject": "relationship",
        "raw_predicate": "has_state",
        "summary": source,
        "evidence_spans": [source],
        "state_dimension": "unknown_dimension",
        "state_value": "high",
        "payload": {},
    }

    parsed = _parse_raw(claim, source, reason="RELATIONSHIP_STATE")
    with pytest.raises(NormalizationContractError) as exc_info:
        _normalize_first(parsed)
    assert exc_info.value.code == "UNKNOWN_STATE_DIMENSION"


@pytest.mark.parametrize("invalid_hint", ["", "   ", 42, ["initiation_balance"]])
def test_raw_parser_rejects_invalid_hint_shape(invalid_hint: object) -> None:
    source = "她最近基本不主动联系我"
    claim = _metric_hint_claim(source)
    claim["payload"] = {"metric_hint": invalid_hint}

    parsed = _parse_raw(claim, source, reason="INTERACTION_PATTERN")

    assert parsed.extraction_status == "claim_schema_invalid"
    assert parsed.extraction.claims == []
    assert parsed.invalid_claim_count == 1
    assert "metric_hint" in parsed.invalid_claim_reasons[0]


def test_raw_predicate_is_sufficient_at_raw_ingress_and_for_custom_fallback() -> None:
    source = "她只在周末早上主动发宠物照片"
    claim = {
        "claim_id": "raw-predicate-only",
        "kind": "stable_fact",
        "subject": "partner",
        "raw_predicate": "sends_pet_photos_only_on_weekend_mornings",
        "summary": source,
        "evidence_spans": [source],
        "payload": {},
    }

    parsed = _parse_raw(claim, source, reason="STABLE_FACT")

    raw_claim = parsed.extraction.claims[0]
    assert raw_claim.predicate == claim["raw_predicate"]
    assert raw_claim.raw_predicate == claim["raw_predicate"]
    normalized = _normalize_first(parsed)
    assert normalized.predicate_type == PredicateType.CUSTOM
    assert normalized.custom_predicate == claim["raw_predicate"]


class _SingleResponseCompletions:
    def __init__(self, payload: dict[str, object]) -> None:
        self._content = json.dumps(payload, ensure_ascii=False)

    async def create(self, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=self._content),
                )
            ],
            usage=None,
        )


async def _noop() -> None:
    return None


async def test_production_extractor_default_uses_raw_parser_boundary() -> None:
    source = "她最近基本不主动联系我"
    payload = {
        "should_extract": True,
        "gate_reason": "INTERACTION_PATTERN",
        "claims": [_metric_hint_claim(source)],
        "discarded_spans": [],
    }
    extractor = OpenAICompatibleMemoryExtractor(
        api_key=SecretStr("test-key"),
        base_url="https://example.invalid",
        model="flash-test",
        max_retries=0,
        validation_mode="raw",
    )
    extractor._client = SimpleNamespace(
        chat=SimpleNamespace(completions=_SingleResponseCompletions(payload)),
        close=_noop,
    )
    attempts = []
    try:
        extraction = await extractor.extract(
            source,
            reference_time=REFERENCE_TIME,
            existing_memories=[],
            conversation_history=[],
            attempt_callback=attempts.append,
        )
    finally:
        await extractor.aclose()

    claim = extraction.claims[0]
    assert extractor._validation_mode == "raw"
    assert claim.payload == {"metric_hint": "initiation_balance"}
    assert claim.canonical_predicate is None
    assert attempts[0].repair_steps is None


async def test_bootstrap_wires_flash_and_strong_extractors_to_raw_mode() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_model="strong-test",
        llm_api_key=SecretStr("test-key"),
        llm_base_url="https://example.invalid",
        memory_extraction_provider="llm",
        memory_extraction_model="flash-test",
        memory_extraction_strong_model="strong-test",
    )
    extractor = _build_memory_extractor(settings)
    try:
        assert isinstance(extractor, TieredMemoryExtractor)
        assert extractor._flash._validation_mode == "raw"
        assert extractor._strong is not None
        assert extractor._strong._validation_mode == "raw"
    finally:
        await extractor.aclose()

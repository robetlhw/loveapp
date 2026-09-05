import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from loveapp.adapters.memory.openai_compatible import (
    _SYSTEM_PROMPT,
    OpenAICompatibleMemoryExtractor,
    TieredMemoryExtractor,
    _build_prompt,
)
from loveapp.application.memory_repair import MemoryResponseError, parse_memory_response
from loveapp.domain.memory import MemoryL0Route, MemorySemanticGateReason


def _claim_payload() -> dict[str, object]:
    return {
        "claim_id": "preference-1",
        "kind": "preference",
        "subject": "partner",
        "predicate": "likes_spicy_food",
        "predicate_type": "custom",
        "custom_predicate": "likes_spicy_food",
        "summary": "对方喜欢吃辣",
        "evidence_spans": ["她喜欢吃辣"],
    }


def _response_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "should_extract": True,
        "gate_reason": "PREFERENCE",
        "claims": [_claim_payload()],
        "discarded_spans": [],
    }
    payload.update(updates)
    return payload


def test_gate_v2_enums_use_the_external_contract_values() -> None:
    assert {item.value for item in MemoryL0Route} == {
        "HARD_DROP",
        "HARD_PASS",
        "SEMANTIC_REVIEW",
        "CONTEXT_PASS",
    }
    assert MemorySemanticGateReason.CONTEXT_DEPENDENT_REPLY.value == ("CONTEXT_DEPENDENT_REPLY")
    assert MemorySemanticGateReason.RELATIONSHIP_STATE.value == "RELATIONSHIP_STATE"


def test_legacy_atomic_extraction_without_gate_fields_remains_parseable() -> None:
    parsed = parse_memory_response(
        json.dumps(
            {"claims": [_claim_payload()], "discarded_spans": []},
            ensure_ascii=False,
        ),
        source_text="她喜欢吃辣。",
    )

    assert parsed.extraction.should_extract is None
    assert parsed.extraction.gate_reason is None
    assert len(parsed.extraction.claims) == 1


def test_parser_accepts_positive_gate_with_empty_claim_warning_shape() -> None:
    parsed = parse_memory_response(
        json.dumps(
            _response_payload(
                gate_reason="user_belief",
                claims=[],
            ),
            ensure_ascii=False,
        ),
        source_text="我这两个月一直觉得自己在关系里不安全。",
    )

    assert parsed.extraction.should_extract is True
    assert parsed.extraction.gate_reason == MemorySemanticGateReason.USER_BELIEF
    assert parsed.extraction.claims == []
    assert "semantic_gate_enum_alias" in parsed.repair_steps


def test_parser_accepts_negative_gate_only_with_empty_claims() -> None:
    parsed = parse_memory_response(
        json.dumps(
            _response_payload(
                should_extract=False,
                gate_reason="SMALL_TALK",
                claims=[],
            ),
            ensure_ascii=False,
        ),
        source_text="早上好。",
    )

    assert parsed.extraction.should_extract is False
    assert parsed.extraction.gate_reason == MemorySemanticGateReason.SMALL_TALK
    assert parsed.extraction.claims == []


@pytest.mark.parametrize(
    "updates",
    [
        {"should_extract": False, "gate_reason": "PREFERENCE", "claims": []},
        {"should_extract": True, "gate_reason": "NO_MEMORY", "claims": []},
        {"gate_reason": "NOT_A_REASON"},
        {"should_extract": "false", "gate_reason": "NO_MEMORY", "claims": []},
    ],
)
def test_parser_fails_closed_for_inconsistent_semantic_gate_contract(
    updates: dict[str, object],
) -> None:
    with pytest.raises(MemoryResponseError) as exc_info:
        parse_memory_response(
            json.dumps(_response_payload(**updates), ensure_ascii=False),
            source_text="她喜欢吃辣。",
        )

    assert exc_info.value.category == "semantic_gate_contract"


def test_parser_rejects_gate_fields_when_only_one_is_present() -> None:
    payload = {
        "should_extract": True,
        "claims": [],
        "discarded_spans": [],
    }

    with pytest.raises(MemoryResponseError) as exc_info:
        parse_memory_response(json.dumps(payload), source_text="测试")

    assert exc_info.value.category == "semantic_gate_contract"


def test_false_with_claims_stays_observable_for_fail_closed_admission() -> None:
    parsed = parse_memory_response(
        json.dumps(
            _response_payload(
                should_extract=False,
                gate_reason="NO_MEMORY",
            ),
            ensure_ascii=False,
        ),
        source_text="她喜欢吃辣。",
    )
    extraction = parsed.extraction

    assert extraction.should_extract is False
    assert len(extraction.claims) == 1


def test_claim_validation_failure_does_not_rewrite_positive_gate() -> None:
    text = "我们目前的关系状态有一些变化。"
    invalid_claim = {
        "claim_id": "relationship-start",
        "kind": "relationship_state",
        "subject": "relationship",
        "predicate": "unknown_relationship_state",
        "predicate_type": "custom",
        "custom_predicate": "unknown_relationship_state",
        "summary": "双方目前关系状态发生变化",
        "evidence_spans": [text.rstrip("。")],
    }

    parsed = parse_memory_response(
        json.dumps(
            _response_payload(
                gate_reason="RELATIONSHIP_STATE",
                claims=[invalid_claim],
            ),
            ensure_ascii=False,
        ),
        source_text=text,
    )

    assert parsed.extraction.should_extract is True
    assert parsed.extraction.gate_reason == MemorySemanticGateReason.RELATIONSHIP_STATE
    assert parsed.extraction.claims == []
    assert parsed.extraction_status == "claim_schema_invalid"
    assert parsed.invalid_claim_count == 1
    assert "all_claims_invalid" in parsed.repair_steps


def test_claim_container_failure_does_not_rewrite_positive_gate() -> None:
    payload = _response_payload(
        gate_reason="RELATIONSHIP_STATE",
        claims={"not": "an array"},
    )

    parsed = parse_memory_response(
        json.dumps(payload, ensure_ascii=False),
        source_text="我俩是去年十一月确定关系的。",
    )

    assert parsed.extraction.should_extract is True
    assert parsed.extraction.gate_reason == MemorySemanticGateReason.RELATIONSHIP_STATE
    assert parsed.extraction.claims == []
    assert parsed.extraction_status == "claim_schema_invalid"
    assert parsed.invalid_claim_count == 1
    assert "claim_container_invalid" in parsed.repair_steps


def test_flash_prompt_requires_one_call_gate_and_does_not_contain_eval_labels() -> None:
    prompt_payload = json.loads(
        _build_prompt(
            "她喜欢吃辣。",
            datetime(2026, 9, 1, tzinfo=UTC),
            [],
            [],
        )
    )

    assert "should_extract" in _SYSTEM_PROMPT
    assert "gate_reason" in _SYSTEM_PROMPT
    assert "claims" in _SYSTEM_PROMPT
    assert prompt_payload["user_message"] == "她喜欢吃辣。"
    assert "expected" not in prompt_payload
    assert "rationale" not in prompt_payload
    assert "extraction_hint" not in prompt_payload


def test_flash_prompt_keeps_context_answers_and_boundary_cases_in_gate_scope() -> None:
    assert "语义 Gate 和 claim 可构造性分开判断" in _SYSTEM_PROMPT
    assert "gate_reason=CONTEXT_DEPENDENT_REPLY" in _SYSTEM_PROMPT
    assert "不得因短答本身不完整而改判 NO_MEMORY" in _SYSTEM_PROMPT
    assert "USER_BELIEF 只有在该判断持续、反复出现" in _SYSTEM_PROMPT
    assert "PLANNED_EVENT 必须至少有明确的承诺" in _SYSTEM_PROMPT
    assert "该事件整体用 TRANSIENT" in _SYSTEM_PROMPT


def test_flash_prompt_defines_transient_belief_boundary_without_keyword_drop() -> None:
    assert "仅由一个刚发生的单次事件触发" in _SYSTEM_PROMPT
    assert "不得仅因 perspective 是 user_belief" in _SYSTEM_PROMPT
    assert "过去数周或数月的持续" in _SYSTEM_PROMPT
    assert "不适用于具体事实、偏好" in _SYSTEM_PROMPT
    assert "CONTEXT_PASS 的明确 slot answer" in _SYSTEM_PROMPT
    assert "false/TRANSIENT" in _SYSTEM_PROMPT
    assert "true/USER_BELIEF" in _SYSTEM_PROMPT


class _OneCallCompletions:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0
        self.request: dict[str, object] | None = None

    async def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls += 1
        self.request = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=self.content),
                )
            ],
            usage=None,
        )


async def _noop() -> None:
    return None


async def test_flash_extractor_returns_gate_and_claims_from_one_model_call() -> None:
    completions = _OneCallCompletions(json.dumps(_response_payload(), ensure_ascii=False))
    extractor = OpenAICompatibleMemoryExtractor(
        api_key=SecretStr("test-key"),
        base_url="https://example.invalid",
        model="flash-test",
        max_retries=0,
        tier="flash",
    )
    extractor._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        close=_noop,
    )

    extraction = await extractor.extract(
        "她喜欢吃辣。",
        reference_time=datetime(2026, 9, 1, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
    )

    assert completions.calls == 1
    assert extraction.should_extract is True
    assert extraction.gate_reason == MemorySemanticGateReason.PREFERENCE
    assert len(extraction.claims) == 1
    assert completions.request is not None
    messages = completions.request["messages"]
    assert isinstance(messages, list)
    assert "should_extract" in messages[0]["content"]
    await extractor.aclose()


async def test_flash_extractor_preserves_gate_when_all_claims_are_invalid() -> None:
    text = "我们目前的关系状态有一些变化。"
    invalid_claim = {
        "claim_id": "unknown-state",
        "kind": "relationship_state",
        "subject": "relationship",
        "predicate": "unknown_relationship_state",
        "predicate_type": "custom",
        "custom_predicate": "unknown_relationship_state",
        "summary": "双方目前关系状态发生变化",
        "evidence_spans": [text.rstrip("。")],
    }
    completions = _OneCallCompletions(
        json.dumps(
            _response_payload(
                gate_reason="RELATIONSHIP_STATE",
                claims=[invalid_claim],
            ),
            ensure_ascii=False,
        )
    )
    extractor = OpenAICompatibleMemoryExtractor(
        api_key=SecretStr("test-key"),
        base_url="https://example.invalid",
        model="flash-test",
        max_retries=0,
        tier="flash",
    )
    extractor._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        close=_noop,
    )
    attempts = []

    extraction = await extractor.extract(
        text,
        reference_time=datetime(2026, 9, 1, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
        attempt_callback=attempts.append,
    )

    assert extraction.should_extract is True
    assert extraction.gate_reason == MemorySemanticGateReason.RELATIONSHIP_STATE
    assert extraction.claims == []
    assert len(attempts) == 1
    assert attempts[0].status.value == "completed"
    assert attempts[0].extraction_status == "claim_schema_invalid"
    assert attempts[0].invalid_claim_count == 1
    await extractor.aclose()


def _model_extractor(
    completions: _OneCallCompletions,
    *,
    model: str,
    tier: str,
) -> OpenAICompatibleMemoryExtractor:
    extractor = OpenAICompatibleMemoryExtractor(
        api_key=SecretStr("test-key"),
        base_url="https://example.invalid",
        model=model,
        max_retries=0,
        tier=tier,
    )
    extractor._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        close=_noop,
    )
    return extractor


async def test_flash_false_never_triggers_strong_upgrade() -> None:
    flash_completions = _OneCallCompletions(
        json.dumps(
            _response_payload(
                should_extract=False,
                gate_reason="NO_MEMORY",
                claims=[],
            ),
            ensure_ascii=False,
        )
    )
    strong_completions = _OneCallCompletions(json.dumps(_response_payload(), ensure_ascii=False))
    extractor = TieredMemoryExtractor(
        _model_extractor(flash_completions, model="flash-test", tier="flash"),
        _model_extractor(strong_completions, model="strong-test", tier="strong"),
    )

    result = await extractor.extract(
        "她刚刚十分钟没回我。",
        reference_time=datetime(2026, 9, 1, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
    )

    assert result.should_extract is False
    assert result.gate_reason == MemorySemanticGateReason.NO_MEMORY
    assert flash_completions.calls == 1
    assert strong_completions.calls == 0
    await extractor.aclose()


async def test_strong_upgrade_cannot_replace_flash_semantic_gate_decision() -> None:
    text = "她明确说不吃辣。"
    weak_claim = _claim_payload()
    weak_claim.update(
        {
            "summary": "对方不吃辣",
            "evidence_spans": [text],
            "confidence": 0.4,
            "importance": 5,
        }
    )
    strong_claim = dict(weak_claim)
    strong_claim["claim_id"] = "preference-strong"
    flash_completions = _OneCallCompletions(
        json.dumps(
            _response_payload(
                gate_reason="PREFERENCE",
                claims=[weak_claim],
            ),
            ensure_ascii=False,
        )
    )
    strong_completions = _OneCallCompletions(
        json.dumps(
            _response_payload(
                gate_reason="STABLE_FACT",
                claims=[strong_claim],
            ),
            ensure_ascii=False,
        )
    )
    extractor = TieredMemoryExtractor(
        _model_extractor(flash_completions, model="flash-test", tier="flash"),
        _model_extractor(strong_completions, model="strong-test", tier="strong"),
    )

    result = await extractor.extract(
        text,
        reference_time=datetime(2026, 9, 1, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
    )

    assert strong_completions.calls == 1
    assert result.should_extract is True
    assert result.gate_reason == MemorySemanticGateReason.PREFERENCE
    await extractor.aclose()

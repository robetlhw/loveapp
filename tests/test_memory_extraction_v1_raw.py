import json
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from loveapp.adapters.memory.openai_compatible import (
    OpenAICompatibleMemoryExtractor,
    TieredMemoryExtractor,
)
from loveapp.evaluation.memory_extraction_raw import (
    run_flash_raw_diagnostic,
    run_production_cascade_from_flash_result,
)


class _Completions:
    def __init__(self, content: str) -> None:
        self.content = content
        self.call_count = 0

    async def create(self, **_: object) -> object:
        self.call_count += 1
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=self.content),
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
            ),
        )


class _TraceRecorder:
    def __init__(self) -> None:
        self.details: dict[str, object] = {}

    @contextmanager
    def measure(self, _name: str):
        yield self.details


@pytest.mark.asyncio
async def test_raw_and_post_repair_share_one_flash_sampling() -> None:
    content = json.dumps(
        {
            "should_extract": True,
            "gate_reason": "PREFERENCE",
            "claims": [
                {
                    "claim_id": "claim-1",
                    "kind": "preference",
                    "subject": "partner",
                    "predicate": "likes_sushi",
                    "summary": "她喜欢寿司",
                    "evidence_spans": ["她喜欢寿司"],
                    "perspective": "user_reported",
                }
            ],
            "discarded_spans": [],
        },
        ensure_ascii=False,
    )
    completions = _Completions(content)
    extractor = SimpleNamespace(
        _model="flash-test",
        _max_tokens=1536,
        _thinking="disabled",
        _client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    trace = _TraceRecorder()
    result = await run_flash_raw_diagnostic(
        extractor,
        "她喜欢寿司",
        reference_time=datetime(2026, 9, 1, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
        pending_memory_context=None,
        trace=trace,
    )

    assert completions.call_count == 1
    assert result.raw_json_valid is True
    assert result.raw_claim_count == 1
    assert result.post_repair_extraction is not None
    assert len(result.post_repair_extraction.claims) == 1
    assert result.post_repair_extraction.claims[0].extractor_model == "flash-test"
    assert result.total_tokens == 30
    assert trace.details["tier"] == "flash"
    assert trace.details["claim_count"] == 1
    assert trace.details["repair_status"] == result.repair_status
    assert float(trace.details["latency_ms"]) >= 0


@pytest.mark.asyncio
async def test_raw_json_error_still_records_post_repair_failure() -> None:
    completions = _Completions("not-json")
    extractor = SimpleNamespace(
        _model="flash-test",
        _max_tokens=1536,
        _thinking=None,
        _client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    result = await run_flash_raw_diagnostic(
        extractor,
        "她喜欢寿司",
        reference_time=datetime(2026, 9, 1, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
        pending_memory_context=None,
    )

    assert result.raw_json_valid is False
    assert result.post_repair_extraction is None
    assert result.post_repair_error_category == "json_syntax"


@pytest.mark.asyncio
async def test_production_cascade_replays_the_same_flash_response() -> None:
    content = json.dumps(
        {
            "should_extract": True,
            "gate_reason": "PREFERENCE",
            "claims": [
                {
                    "claim_id": "claim-1",
                    "kind": "preference",
                    "subject": "partner",
                    "predicate": "likes_sushi",
                    "summary": "她喜欢寿司",
                    "evidence_spans": ["她喜欢寿司"],
                }
            ],
            "discarded_spans": [],
        },
        ensure_ascii=False,
    )
    flash = OpenAICompatibleMemoryExtractor(
        api_key=SecretStr("test"),
        base_url="https://example.invalid",
        model="flash-test",
        max_retries=0,
    )
    completions = _Completions(content)
    flash._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    cascade = TieredMemoryExtractor(flash, strong=None)
    diagnostic = await run_flash_raw_diagnostic(
        flash,
        "她喜欢寿司",
        reference_time=datetime(2026, 9, 1, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
        pending_memory_context=None,
    )

    extraction, attempts = await run_production_cascade_from_flash_result(
        cascade,
        diagnostic,
        "她喜欢寿司",
        reference_time=datetime(2026, 9, 1, tzinfo=UTC),
        existing_memories=[],
        conversation_history=[],
        pending_memory_context=None,
    )

    assert completions.call_count == 1
    assert len(extraction.claims) == 1
    assert len(attempts) == 1

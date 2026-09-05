import json
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from loveapp.adapters.advice.openai_compatible import (
    AdviceStructuredOutputError,
    OpenAICompatibleAdviceComposer,
    _parse_response,
)
from loveapp.application.scenario_policy import default_scenario_policy_registry
from loveapp.bootstrap import _build_advice_composer
from loveapp.core.config import Settings
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.advice import (
    AdviceGenerationErrorType,
    AdviceRequest,
    RelationshipContext,
)
from loveapp.domain.enums import AdviceScenario


class _FakeCompletions:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.requests: list[dict] = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class _ProviderFailure(RuntimeError):
    pass


_ProviderFailure.__module__ = "openai.fake"


def _completion(
    content: str | None,
    *,
    finish_reason: str = "stop",
    reasoning_tokens: int | None = None,
    request_id: str | None = "provider-request-123",
):
    return SimpleNamespace(
        id="request-123",
        _request_id=request_id,
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            completion_tokens_details=SimpleNamespace(
                reasoning_tokens=reasoning_tokens
            ),
        ),
    )


def _valid_json() -> str:
    return json.dumps(
        {
            "problem_summary": "问题摘要",
            "assessment": "事实评估",
            "clarifying_questions": [],
            "recommended_actions": ["先沟通"],
            "sample_phrases": [],
            "alternatives": [],
            "avoid_actions": [],
            "risk_notes": [],
        },
        ensure_ascii=False,
    )


def _composer(results: list[object], *, retries: int = 1):
    composer = OpenAICompatibleAdviceComposer(
        api_key=SecretStr("test"),
        base_url="https://example.invalid",
        model="deepseek-v4-pro",
        max_tokens=4096,
        thinking="disabled",
        temperature=0,
        structured_retries=retries,
    )
    completions = _FakeCompletions(results)
    composer._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=completions)
    )
    return composer, completions


async def _compose(composer, *, trace=None, attempts=None):
    scenario = AdviceScenario.CONFLICT
    policy = default_scenario_policy_registry().resolve(scenario, [])
    return await composer.compose(
        request=AdviceRequest(query="我们刚刚发生了争执，我该怎么办？"),
        scenario=scenario,
        context=RelationshipContext(user_id="local-user"),
        documents=[],
        conversation_history=[],
        policy=policy,
        attempt_callback=attempts.append if attempts is not None else None,
        trace=trace,
    )


@pytest.mark.asyncio
async def test_valid_json_uses_deterministic_non_thinking_request() -> None:
    composer, completions = _composer(
        [_completion(_valid_json(), reasoning_tokens=7)]
    )
    attempts = []

    response = await _compose(composer, attempts=attempts)

    assert response.problem_summary == "问题摘要"
    assert len(attempts) == 1
    assert attempts[0].status == "completed"
    assert attempts[0].prompt_tokens == 100
    assert attempts[0].completion_tokens == 50
    assert attempts[0].reasoning_tokens == 7
    assert attempts[0].total_tokens == 150
    assert attempts[0].provider_request_id == "provider-request-123"
    request = completions.requests[0]
    assert request["response_format"] == {"type": "json_object"}
    assert request["temperature"] == 0
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}


@pytest.mark.asyncio
async def test_advice_settings_are_wired_independently_from_general_llm() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_model="general-model",
        llm_api_key=SecretStr("test"),
        llm_base_url="https://example.invalid",
        advice_model="advice-model",
        advice_max_tokens=3072,
        advice_thinking="disabled",
        advice_temperature=0,
        advice_structured_retries=1,
    )

    composer = _build_advice_composer(settings)
    try:
        assert isinstance(composer, OpenAICompatibleAdviceComposer)
        assert composer._model == "advice-model"
        assert composer._max_tokens == 3072
        assert composer._thinking == "disabled"
        assert composer._temperature == 0
        assert composer._structured_retries == 1
    finally:
        await composer.aclose()


@pytest.mark.asyncio
async def test_empty_content_falls_back_with_classified_attempt() -> None:
    composer, _ = _composer([_completion(None)], retries=0)
    attempts = []

    response = await _compose(composer, attempts=attempts)

    assert "生成出现异常" in response.problem_summary
    assert attempts[0].parse_error_type == AdviceGenerationErrorType.EMPTY_CONTENT
    assert attempts[0].fallback_used is True


@pytest.mark.asyncio
async def test_empty_length_is_classified_separately() -> None:
    composer, _ = _composer([_completion(None, finish_reason="length")], retries=0)
    attempts = []

    await _compose(composer, attempts=attempts)

    assert attempts[0].parse_error_type == AdviceGenerationErrorType.FINISH_REASON_LENGTH
    assert attempts[0].finish_reason == "length"


def test_partial_json_with_length_is_not_locally_repaired() -> None:
    with pytest.raises(AdviceStructuredOutputError) as caught:
        _parse_response('{"problem_summary":"半截', "length")

    assert caught.value.error_type == AdviceGenerationErrorType.FINISH_REASON_LENGTH


def test_invalid_json_syntax_is_distinct_from_schema_failure() -> None:
    with pytest.raises(AdviceStructuredOutputError) as caught:
        _parse_response("{not-json}", "stop")

    assert caught.value.error_type == AdviceGenerationErrorType.JSON_DECODE_ERROR


def test_whitespace_only_content_is_empty_content() -> None:
    with pytest.raises(AdviceStructuredOutputError) as caught:
        _parse_response(" \r\n\t ", "stop")

    assert caught.value.error_type == AdviceGenerationErrorType.EMPTY_CONTENT


def test_valid_json_with_wrong_schema_reports_fields() -> None:
    with pytest.raises(AdviceStructuredOutputError) as caught:
        _parse_response('{"problem_summary": [], "assessment": "ok"}', "stop")

    error = caught.value
    assert error.error_type == AdviceGenerationErrorType.SCHEMA_VALIDATION_ERROR
    assert "problem_summary" in error.invalid_fields


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        (
            {
                "problem_summary": "摘要",
                "assessment": "评估",
                "clarifying_questions": [],
                "recommended_actions": [],
                "sample_phrases": [],
                "alternatives": [],
                "avoid_actions": [],
            },
            "risk_notes",
        ),
        (
            {
                "problem_summary": "摘要",
                "assessment": "评估",
                "clarifying_questions": [],
                "recommended_actions": [],
                "sample_phrases": [],
                "alternatives": [],
                "avoid_actions": [],
                "risk_notes": [],
                "unexpected": "not allowed",
            },
            "unexpected",
        ),
        (
            {
                "problem_summary": "摘要",
                "assessment": "评估",
                "clarifying_questions": [],
                "recommended_actions": ["1", "2", "3", "4", "5", "6"],
                "sample_phrases": [],
                "alternatives": [],
                "avoid_actions": [],
                "risk_notes": [],
            },
            "recommended_actions",
        ),
    ],
)
def test_generated_advice_schema_requires_exact_bounded_fields(
    payload: dict,
    field: str,
) -> None:
    with pytest.raises(AdviceStructuredOutputError) as caught:
        _parse_response(json.dumps(payload, ensure_ascii=False), "stop")

    error = caught.value
    assert error.error_type == AdviceGenerationErrorType.SCHEMA_VALIDATION_ERROR
    assert field in {*error.missing_fields, *error.invalid_fields}


@pytest.mark.asyncio
async def test_first_invalid_second_success_uses_one_bounded_repair() -> None:
    composer, completions = _composer(
        [_completion("{bad}"), _completion(_valid_json())]
    )
    attempts = []

    response = await _compose(composer, attempts=attempts)

    assert response.problem_summary == "问题摘要"
    assert [item.status for item in attempts] == ["failed", "completed"]
    assert attempts[1].retry_reason == "json_decode_error"
    assert len(completions.requests) == 2
    assert completions.requests[1]["temperature"] == 0
    assert completions.requests[1]["extra_body"] == {
        "thinking": {"type": "disabled"}
    }


@pytest.mark.asyncio
async def test_schema_repair_receives_specific_validation_fields() -> None:
    incomplete = json.loads(_valid_json())
    del incomplete["risk_notes"]
    composer, completions = _composer(
        [_completion(json.dumps(incomplete)), _completion(_valid_json())]
    )

    await _compose(composer)

    repair_prompt = completions.requests[1]["messages"][-1]["content"]
    assert "schema_validation_error" in repair_prompt
    assert "risk_notes" in repair_prompt


@pytest.mark.asyncio
async def test_two_failed_attempts_return_fallback_and_trace_telemetry() -> None:
    composer, _ = _composer([_completion("{bad}"), _completion("{}")])
    attempts = []
    trace = ExecutionTrace()

    response = await _compose(composer, trace=trace, attempts=attempts)

    assert "生成出现异常" in response.problem_summary
    assert len(attempts) == 2
    assert attempts[-1].fallback_used is True
    records = [
        item for item in trace.snapshot() if item.name.startswith("advice_model_attempt_")
    ]
    assert len(records) == 2
    assert records[-1].details["fallback_used"] is True
    assert records[-1].details["parse_error_type"] == "schema_validation_error"
    assert "raw_response" not in records[-1].details


@pytest.mark.asyncio
async def test_failed_structured_generation_never_streams_model_fragments() -> None:
    composer, _ = _composer([_completion('{"problem_summary":"半截'), _completion("{}")])
    streamed = []
    scenario = AdviceScenario.CONFLICT
    policy = default_scenario_policy_registry().resolve(scenario, [])

    response = await composer.compose(
        request=AdviceRequest(query="我们刚刚发生了争执，我该怎么办？"),
        scenario=scenario,
        context=RelationshipContext(user_id="local-user"),
        documents=[],
        conversation_history=[],
        policy=policy,
        stream_callback=streamed.append,
    )

    assert "生成出现异常" in response.problem_summary
    assert streamed == []


@pytest.mark.asyncio
async def test_timeout_is_classified_and_fails_closed() -> None:
    composer, _ = _composer([TimeoutError()])
    attempts = []

    response = await _compose(composer, attempts=attempts)

    assert "无法可靠生成" in response.assessment
    assert attempts[0].parse_error_type == AdviceGenerationErrorType.TIMEOUT


@pytest.mark.asyncio
async def test_transport_class_errors_do_not_trigger_structured_retry() -> None:
    composer, completions = _composer([TimeoutError(), _completion(_valid_json())])
    attempts = []

    response = await _compose(composer, attempts=attempts)

    assert "无法可靠生成" in response.assessment
    assert len(completions.requests) == 1
    assert len(attempts) == 1
    assert attempts[0].fallback_used is True


@pytest.mark.asyncio
async def test_provider_error_is_classified_and_fails_closed() -> None:
    composer, completions = _composer([_ProviderFailure("provider down")])
    attempts = []

    response = await _compose(composer, attempts=attempts)

    assert "无法可靠生成" in response.assessment
    assert attempts[0].parse_error_type == AdviceGenerationErrorType.PROVIDER_ERROR
    assert attempts[0].fallback_used is True
    assert len(completions.requests) == 1

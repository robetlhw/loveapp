import json
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from loveapp.adapters.routing import OpenAICompatibleRouteCorrector
from loveapp.adapters.routing.openai_compatible import (
    _build_prompt,
    _is_schema_format_error,
    semantic_route_response_format,
)
from loveapp.application.routing import route_by_rules
from loveapp.domain.enums import TaskType
from loveapp.domain.routing import RouteInput


class _TransportRetryCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise TimeoutError("provider timed out")
        return _completion()


class _SchemaFallbackCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            error = ValueError("response_format json_schema is unsupported")
            error.status_code = 400  # type: ignore[attr-defined]
            raise error
        return _completion()


class _SemanticCompletions:
    def __init__(self, contents: list[str]) -> None:
        self.calls: list[dict[str, object]] = []
        self._contents = iter(contents)

    async def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return _completion(next(self._contents))


def _completion(
    content: str = '{"task_type":"relationship_advice","task_confidence":0.9}',
) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=content,
                ),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2),
    )


def _provider(completions: object, **kwargs: object) -> OpenAICompatibleRouteCorrector:
    provider = OpenAICompatibleRouteCorrector(
        api_key=SecretStr("test-key"),
        base_url="https://example.invalid",
        model="test-router",
        **kwargs,
    )
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        close=_async_noop,
    )
    return provider


async def _async_noop() -> None:
    return None


@pytest.mark.asyncio
async def test_live_router_provider_records_timeout_retry_and_tokens() -> None:
    completions = _TransportRetryCompletions()
    provider = _provider(completions, max_retries=1)
    route_input = RouteInput(latest_query="我喜欢她，有什么建议吗？")

    correction = await provider.correct(route_input, route_by_rules(route_input))

    assert correction.task_type == TaskType.RELATIONSHIP_ADVICE
    assert provider.last_telemetry["attempt_count"] == 2
    assert provider.last_telemetry["retry_count"] == 1
    assert provider.last_telemetry["timeout_count"] == 1
    assert provider.last_telemetry["provider_error_count"] == 1
    assert provider.last_telemetry["total_tokens"] == 6
    assert provider.last_telemetry["prompt_sha256"]


@pytest.mark.asyncio
async def test_live_router_provider_falls_back_from_json_schema_to_json_object() -> None:
    completions = _SchemaFallbackCompletions()
    provider = _provider(completions, max_retries=0)
    route_input = RouteInput(latest_query="我喜欢她，有什么建议吗？")

    await provider.correct(route_input, route_by_rules(route_input))

    assert completions.calls[0]["response_format"]["type"] == "json_schema"  # type: ignore[index]
    assert completions.calls[1]["response_format"] == {"type": "json_object"}
    assert provider.last_telemetry["schema_fallback_count"] == 1
    assert provider.last_telemetry["retry_count"] == 1


def test_schema_format_error_recognizes_provider_unavailable_message() -> None:
    error = ValueError("This response_format type is unavailable now")
    error.status_code = 400  # type: ignore[attr-defined]

    assert _is_schema_format_error(error)


def _semantic_payload() -> str:
    return json.dumps(
        {
            "branch": "rag",
            "primary_scenario": "chat_analysis",
            "secondary_scenarios": [],
            "scenario_scores": {"chat_analysis": 0.9},
            "goals": ["understand", "communicate"],
            "goal_scores": {"understand": 0.9, "communicate": 0.6},
            "confidence": 0.9,
            "reasoning_summary": "Interpreting a change in reply behavior.",
        }
    )


@pytest.mark.asyncio
async def test_semantic_only_prompt_and_schema_contract_are_bounded() -> None:
    completions = _SemanticCompletions([_semantic_payload()])
    provider = _provider(
        completions,
        semantic_only=True,
        prompt_version="phase3.2-semantic-v1",
        max_retries=0,
    )
    route_input = RouteInput(
        latest_query="她最近回复越来越慢是什么意思？",
        active_task=TaskType.DATE_PLANNING,
        pending_task_reason="legacy state must not be sent to semantic router",
    )

    correction = await provider.correct(route_input, route_by_rules(route_input))

    request = completions.calls[0]
    messages = request["messages"]
    assert isinstance(messages, list)
    user_payload = json.loads(messages[1]["content"])
    assert set(user_payload) == {"current_query", "recent_messages"}
    assert user_payload["current_query"] == route_input.latest_query
    assert "date" not in json.dumps(user_payload).casefold()
    assert "rule_hints" not in user_payload

    response_format = request["response_format"]
    assert response_format == semantic_route_response_format("json_schema")
    required = set(response_format["json_schema"]["schema"]["required"])
    assert required == {
        "branch",
        "primary_scenario",
        "secondary_scenarios",
        "scenario_scores",
        "goals",
        "goal_scores",
        "confidence",
        "reasoning_summary",
    }
    assert provider.semantic_only is True
    assert correction.task_type == TaskType.RELATIONSHIP_ADVICE
    assert correction.branch == "rag"
    assert correction.primary_scenario.value == "chat_analysis"
    assert [goal.value for goal in correction.goals] == ["understand", "communicate"]
    assert provider.last_telemetry["attempt_count"] == 1
    assert provider.last_telemetry["parse_error_count"] == 0
    assert provider.last_telemetry["fallback_count"] == 0
    assert provider.last_telemetry["total_tokens"] == 6


@pytest.mark.asyncio
async def test_semantic_only_rejects_legacy_partial_payload_and_records_parse_repair() -> None:
    completions = _SemanticCompletions([
        '{"task_type":"general_chat","branch":"rag","goals":{}}',
        _semantic_payload(),
    ])
    provider = _provider(completions, semantic_only=True, max_retries=0)
    route_input = RouteInput(latest_query="她是不是不想理我了？")

    correction = await provider.correct(route_input, route_by_rules(route_input))

    assert correction.branch == "rag"
    assert len(completions.calls) == 2
    repaired_messages = completions.calls[1]["messages"]
    assert isinstance(repaired_messages, list)
    assert repaired_messages[-2]["role"] == "assistant"
    assert repaired_messages[-1]["role"] == "user"
    assert "JSON" in repaired_messages[-1]["content"]
    assert provider.last_telemetry["attempt_count"] == 2
    assert provider.last_telemetry["retry_count"] == 1
    assert provider.last_telemetry["parse_error_count"] == 1
    assert provider.last_telemetry["provider_error_count"] == 0
    assert provider.last_telemetry["fallback_count"] == 0


def test_semantic_only_build_prompt_omits_legacy_fields() -> None:
    route_input = RouteInput(
        latest_query="帮我看看这段聊天是什么意思",
        active_task=TaskType.DATE_PLANNING,
        pending_task=TaskType.DATE_PLANNING,
        pending_task_reason="do not leak state",
    )
    payload = json.loads(
        _build_prompt(route_input, route_by_rules(route_input), semantic_only=True)
    )

    assert payload == {"current_query": route_input.latest_query, "recent_messages": []}

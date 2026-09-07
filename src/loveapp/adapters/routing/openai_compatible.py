import asyncio
import hashlib
import json
import math
from time import perf_counter
from typing import Any, Literal

from openai import AsyncOpenAI
from pydantic import SecretStr, ValidationError

from loveapp.domain.date_operations import DatePlanOperation
from loveapp.domain.date_patch import DatePlanPatch
from loveapp.domain.enums import AdviceGoal, AdviceScenario
from loveapp.domain.routing import (
    DatePlanSlots,
    RouteCorrection,
    RouteInput,
    RouteResult,
    SemanticRouteDecision,
)

StructuredOutputMode = Literal["json_schema", "json_object"]


def semantic_route_response_format(
    mode: StructuredOutputMode = "json_schema",
) -> dict[str, Any]:
    """Return the bounded structured-output contract for the semantic router."""

    if mode == "json_object":
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "semantic_route_decision",
            # Dynamic score maps use enum property names, which several
            # OpenAI-compatible endpoints reject under their stricter
            # ``additionalProperties=false`` dialect.  Pydantic validation
            # remains the authoritative post-response schema check.
            "strict": False,
            "schema": SemanticRouteDecision.model_json_schema(),
        },
    }


class OpenAICompatibleRouteCorrector:
    def __init__(
        self,
        *,
        api_key: SecretStr,
        base_url: str,
        model: str,
        timeout_seconds: float = 60,
        max_retries: int = 2,
        max_tokens: int = 2048,
        thinking: Literal["enabled", "disabled"] | None = None,
        temperature: float = 0,
        structured_output: StructuredOutputMode = "json_schema",
        provider_name: str = "openai_compatible",
        prompt_version: str = "routing-v3.0",
        semantic_only: bool = False,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if structured_output not in {"json_schema", "json_object"}:
            raise ValueError("structured_output must be json_schema or json_object")
        self._model = model
        self._max_tokens = max_tokens
        self._thinking = thinking
        self._temperature = temperature
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._structured_output = structured_output
        self._provider_name = provider_name
        self._provider_prefers_json_object = (
            provider_name.casefold() == "deepseek" and structured_output == "json_schema"
        )
        self._prompt_version = prompt_version
        self._semantic_only = semantic_only
        self._system_prompt = (
            _SEMANTIC_SYSTEM_PROMPT
            if semantic_only
            else _SYSTEM_PROMPT + "\n" + _DATE_SLOT_INSTRUCTIONS
        )
        self._prompt_sha256 = hashlib.sha256(
            self._system_prompt.encode("utf-8")
        ).hexdigest()
        self.last_telemetry: dict[str, Any] = {}
        self._client = AsyncOpenAI(
            api_key=api_key.get_secret_value(),
            base_url=base_url,
            timeout=timeout_seconds,
            # Keep retry accounting in this adapter.  The SDK's hidden retry
            # loop cannot be represented accurately in evaluation telemetry.
            max_retries=0,
        )

    @property
    def provider(self) -> str:
        return self._provider_name

    @property
    def live_llm(self) -> bool:
        return True

    @property
    def model(self) -> str:
        return self._model

    @property
    def prompt_version(self) -> str:
        return self._prompt_version

    @property
    def prompt_sha256(self) -> str:
        return self._prompt_sha256

    @property
    def semantic_only(self) -> bool:
        return self._semantic_only

    async def correct(
        self,
        route_input: RouteInput,
        rule_result: RouteResult,
    ) -> RouteCorrection:
        started = perf_counter()
        self.last_telemetry = {
            "provider": self._provider_name,
            "live_llm": True,
            "model": self._model,
            "prompt_version": self._prompt_version,
            "prompt_sha256": self._prompt_sha256,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "timeout_seconds": self._timeout_seconds,
            "max_retries": self._max_retries,
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "duration_ms": None,
            "attempt_count": 0,
            "retry_count": 0,
            "timeout_count": 0,
            "parse_error_count": 0,
            "provider_error_count": 0,
            "fallback_count": 0,
            "semantic_sanitization_count": 0,
            "semantic_sanitization_reasons": [],
            "llm_raw_decision": None,
            "llm_sanitized_decision": None,
            "schema_fallback_count": 0,
            "structured_output": self._structured_output,
            "effective_structured_output": (
                "json_object"
                if self._provider_prefers_json_object
                else self._structured_output
            ),
        }
        messages = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": _build_prompt(
                    route_input,
                    rule_result,
                    semantic_only=self._semantic_only,
                ),
            },
        ]
        last_error: ValueError | None = None
        parse_repair_used = False
        transport_retries = 0
        request_mode: StructuredOutputMode = (
            "json_object" if self._provider_prefers_json_object else self._structured_output
        )
        while True:
            # Count a request before sending it so a transport failure is still
            # observable in the fallback trace.
            self.last_telemetry["attempt_count"] += 1
            request_kwargs = {
                "model": self._model,
                "messages": messages,
                "response_format": semantic_route_response_format(request_mode),
                "temperature": self._temperature,
                "max_tokens": self._max_tokens,
            }
            if self._thinking is not None:
                request_kwargs["extra_body"] = {"thinking": {"type": self._thinking}}
            try:
                completion = await asyncio.wait_for(
                    self._client.chat.completions.create(**request_kwargs),
                    timeout=self._timeout_seconds,
                )
            except Exception as exc:
                self.last_telemetry["provider_error_count"] += 1
                self.last_telemetry["last_provider_error"] = _safe_error_message(exc)
                if _is_timeout_error(exc):
                    self.last_telemetry["timeout_count"] += 1
                if request_mode == "json_schema" and _is_schema_format_error(exc):
                    request_mode = "json_object"
                    self.last_telemetry["schema_fallback_count"] += 1
                    self.last_telemetry["retry_count"] += 1
                    continue
                if transport_retries < self._max_retries:
                    transport_retries += 1
                    self.last_telemetry["retry_count"] += 1
                    await asyncio.sleep(min(0.25 * transport_retries, 1.0))
                    continue
                self.last_telemetry["duration_ms"] = round(
                    (perf_counter() - started) * 1000,
                    3,
                )
                self.last_telemetry["fallback_count"] = 1
                raise
            usage = getattr(completion, "usage", None)
            self.last_telemetry.update(
                {
                    "input_tokens": _accumulate_token_count(
                        self.last_telemetry.get("input_tokens"),
                        _usage_value(usage, "prompt_tokens", "input_tokens"),
                    ),
                    "output_tokens": _accumulate_token_count(
                        self.last_telemetry.get("output_tokens"),
                        _usage_value(usage, "completion_tokens", "output_tokens"),
                    ),
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                }
            )
            self.last_telemetry["total_tokens"] = _sum_known_tokens(
                self.last_telemetry.get("input_tokens"),
                self.last_telemetry.get("output_tokens"),
            )
            try:
                choice = completion.choices[0]
            except (AttributeError, IndexError, TypeError) as exc:
                self.last_telemetry["provider_error_count"] += 1
                self.last_telemetry["last_provider_error"] = _safe_error_message(exc)
                if transport_retries < self._max_retries:
                    transport_retries += 1
                    self.last_telemetry["retry_count"] += 1
                    await asyncio.sleep(min(0.25 * transport_retries, 1.0))
                    continue
                self.last_telemetry["fallback_count"] = 1
                self.last_telemetry["duration_ms"] = round(
                    (perf_counter() - started) * 1000,
                    3,
                )
                raise ValueError("router provider returned no choices") from exc
            content = choice.message.content
            if isinstance(content, str):
                # Keep a bounded in-memory preview for diagnosing provider
                # shape drift. It is intentionally not copied to RouteResult
                # or persisted in evaluation reports.
                self.last_telemetry["last_response_preview"] = content[:1000]
            try:
                correction, slot_parse_rejections = _parse_response_with_slot_rejections(
                    content,
                    choice.finish_reason,
                    semantic_only=self._semantic_only,
                )
                self.last_telemetry["slot_parse_rejections"] = slot_parse_rejections
                if self._semantic_only:
                    self.last_telemetry["llm_raw_decision"] = _raw_semantic_decision_trace(
                        content
                    )
                    self.last_telemetry["llm_sanitized_decision"] = (
                        _sanitized_semantic_decision_trace(correction)
                    )
                    reasons = list(dict.fromkeys(slot_parse_rejections.values()))
                    self.last_telemetry["semantic_sanitization_reasons"] = reasons
                    self.last_telemetry["semantic_sanitization_count"] = int(bool(reasons))
                _validate_evidence(correction, route_input)
                return correction
            except ValueError as exc:
                last_error = exc
                self.last_telemetry["parse_error_count"] += 1
                self.last_telemetry["last_parse_error"] = _safe_error_message(exc)
                if parse_repair_used:
                    break
                parse_repair_used = True
                self.last_telemetry["retry_count"] += 1
                messages.extend(
                    [
                        {"role": "assistant", "content": content or ""},
                        {"role": "user", "content": _repair_prompt(self._semantic_only, exc)},
                    ]
                )
        self.last_telemetry["duration_ms"] = round((perf_counter() - started) * 1000, 3)
        self.last_telemetry["fallback_count"] = 1
        raise last_error or ValueError("router response could not be parsed")

    async def aclose(self) -> None:
        await self._client.close()


def _accumulate_token_count(
    total: object,
    observed: object,
) -> int | None:
    """Accumulate provider usage without inventing a value when it is absent."""

    known_total = total if isinstance(total, int) and not isinstance(total, bool) else None
    if not isinstance(observed, int) or isinstance(observed, bool) or observed < 0:
        return known_total
    return (known_total or 0) + observed


def _usage_value(usage: object, *names: str) -> object:
    """Read common OpenAI-compatible usage field aliases without guessing."""

    if usage is None:
        return None
    for name in names:
        if isinstance(usage, dict) and name in usage:
            return usage[name]
        value = getattr(usage, name, None)
        if value is not None:
            return value
    return None


def _sum_known_tokens(input_tokens: object, output_tokens: object) -> int | None:
    if all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in (input_tokens, output_tokens)
    ):
        return int(input_tokens) + int(output_tokens)
    return None


def _is_timeout_error(exc: BaseException) -> bool:
    name = type(exc).__name__.casefold()
    return isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or "timeout" in name


def _safe_error_message(exc: BaseException) -> str:
    """Keep provider diagnostics useful without persisting credentials."""

    message = str(exc).replace("\r", " ").replace("\n", " ").strip()
    lowered = message.casefold()
    for marker in ("api_key=", "apikey=", "authorization: bearer "):
        index = lowered.find(marker)
        if index >= 0:
            message = message[: index + len(marker)] + "[redacted]"
            break
    return message[:500]


def _repair_prompt(semantic_only: bool, error: BaseException) -> str:
    if semantic_only:
        return (
            "The previous response failed local SemanticRouteDecision validation. "
            "Return one complete JSON object with exactly the eight required fields "
            "from the system prompt. Use enum strings, arrays for lists, objects "
            "for score maps, and no markdown or extra keys. Validation error: "
            f"{_safe_error_message(error)}"
        )
    return f"上一次输出未通过结构校验。请修正并只输出 JSON。校验错误：{error}"


def _is_schema_format_error(exc: BaseException) -> bool:
    status_code = getattr(exc, "status_code", None)
    message = str(exc).casefold()
    return (
        str(status_code) == "400"
        and (
            "response_format" in message
            or "json_schema" in message
            or "structured output" in message
        )
        and (
            "schema" in message
            or "json" in message
            or "structured" in message
            or "unavailable" in message
            or "unsupported" in message
        )
    )


def _build_prompt(
    route_input: RouteInput,
    rule_result: RouteResult,
    *,
    semantic_only: bool = False,
) -> str:
    if semantic_only:
        # Phase 3.2 has a deliberately narrow contract.  Date-task fields and
        # legacy task labels are omitted so an OpenAI-compatible model cannot
        # confuse the semantic decision with the older RouteCorrection shape.
        payload = {
            "current_query": route_input.latest_query,
            "recent_messages": [
                {"role": message.role.value, "content": message.content}
                for message in route_input.recent_messages[-4:]
            ],
        }
        return json.dumps(payload, ensure_ascii=False)
    payload = {
        "latest_query": route_input.latest_query,
        "recent_messages": [
            {"role": message.role.value, "content": message.content}
            for message in route_input.recent_messages[-4:]
        ],
        # Only compact rule hints are sent.  The semantic Router must not
        # receive the full Agent State, date slots, memory context, or other
        # unrelated runtime fields.
        "rule_hints": {
            "task_type": rule_result.task_type.value,
            "task_confidence": rule_result.task_confidence,
            "task_scores": {key.value: value for key, value in rule_result.task_scores.items()},
            "primary_goal": rule_result.primary_goal.value if rule_result.primary_goal else None,
            "goal_scores": {
                key.value: value for key, value in rule_result.rule_goal_scores.items()
            },
            "primary_scenario": rule_result.primary_scenario.value
            if rule_result.primary_scenario
            else None,
            "scenario_scores": {
                key.value: value for key, value in rule_result.scenario_scores.items()
            },
            "scenario_confidence": rule_result.scenario_confidence,
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def _parse_response(content: str | None, finish_reason: str | None) -> RouteCorrection:
    correction, _ = _parse_response_with_slot_rejections(content, finish_reason)
    return correction


def _parse_response_with_slot_rejections(
    content: str | None,
    finish_reason: str | None,
    *,
    semantic_only: bool = False,
) -> tuple[RouteCorrection, dict[str, str]]:
    if not content:
        raise ValueError(f"路由模型没有返回正文，finish_reason={finish_reason or 'unknown'}。")
    cleaned = content.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1])
    try:
        payload = json.loads(cleaned)
    except (ValidationError, json.JSONDecodeError) as exc:
        raise ValueError("路由模型返回内容不符合 RouteCorrection 结构。") from exc
    if not isinstance(payload, dict):
        raise ValueError("路由模型返回内容不符合 RouteCorrection 结构。")
    if semantic_only:
        return _route_correction_from_semantic_payload(payload)
    payload = _normalize_semantic_payload(payload)
    sanitized_payload, slot_parse_rejections = _sanitize_date_plan_payload(payload)
    try:
        return RouteCorrection.model_validate(sanitized_payload), slot_parse_rejections
    except ValidationError as exc:
        raise ValueError("路由模型返回内容不符合 RouteCorrection 结构。") from exc


def _route_correction_from_semantic_payload(
    payload: dict[str, Any],
) -> tuple[RouteCorrection, dict[str, str]]:
    """Validate and adapt the Phase 3.2-only response contract.

    The application still consumes ``RouteCorrection`` because it owns date
    and legacy task guards.  Live semantic calls are validated against the
    smaller, complete ``SemanticRouteDecision`` first, so a partial object or
    a date-planning-shaped response cannot be accepted accidentally.
    """

    sanitized, semantic_rejections = _sanitize_semantic_payload(payload)
    try:
        decision = SemanticRouteDecision.model_validate(sanitized)
    except ValidationError as exc:
        raise ValueError("路由模型返回内容不符合 SemanticRouteDecision 结构。") from exc
    primary_goal = decision.goals[0] if decision.goals else None
    primary_score = (
        decision.scenario_scores.get(decision.primary_scenario)
        if decision.primary_scenario is not None
        else None
    )
    return RouteCorrection(
        task_type=(
            "relationship_advice" if decision.branch == "rag" else "out_of_scope"
        ),
        branch=decision.branch,
        task_confidence=decision.confidence,
        primary_goal=primary_goal,
        secondary_goals=decision.goals[1:3],
        primary_scenario=decision.primary_scenario,
        secondary_scenarios=decision.secondary_scenarios,
        scenario_confidence=primary_score,
        scenario_scores=decision.scenario_scores,
        goals=decision.goals,
        goal_scores=decision.goal_scores,
        confidence=decision.confidence,
        reasoning_summary=decision.reasoning_summary,
    ), semantic_rejections


def _sanitize_semantic_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Apply only lossless bounds/enum cleanup before strict validation.

    DeepSeek occasionally emits a valid semantic object with three secondary
    labels, a goal label in the scenario list, or a reasoning summary over the
    storage bound.  Those are provider shape errors, but dropping the invalid
    extras is deterministic and does not invent a classification. Unknown
    top-level keys are deliberately retained so ``extra=forbid`` still rejects
    legacy/date-shaped responses.
    """

    sanitized = dict(payload)
    rejected: dict[str, str] = {}
    scenario_values = {item.value for item in AdviceScenario}
    goal_values = {item.value for item in AdviceGoal}

    primary_scenario = sanitized.get("primary_scenario")
    if primary_scenario is not None and primary_scenario not in scenario_values:
        sanitized["primary_scenario"] = None
        rejected["primary_scenario"] = "unknown_enum_removed"

    secondary = sanitized.get("secondary_scenarios")
    if isinstance(secondary, list):
        valid_secondary: list[str] = []
        for index, item in enumerate(secondary):
            if not isinstance(item, str) or item not in scenario_values:
                rejected[f"secondary_scenarios.{index}"] = "unknown_enum_removed"
                continue
            if item == sanitized.get("primary_scenario"):
                rejected[f"secondary_scenarios.{index}"] = (
                    "invalid_primary_secondary_relationship"
                )
                continue
            if item in valid_secondary:
                rejected[f"secondary_scenarios.{index}"] = "duplicate_label_removed"
                continue
            valid_secondary.append(item)
        bounded_secondary = valid_secondary[:2]
        if bounded_secondary != secondary:
            sanitized["secondary_scenarios"] = bounded_secondary
        if len(valid_secondary) > 2:
            rejected["secondary_scenarios.limit"] = "label_count_bounded"

    scenario_scores = sanitized.get("scenario_scores")
    if isinstance(scenario_scores, dict):
        valid_scores: dict[str, float] = {}
        for key, value in scenario_scores.items():
            if not isinstance(key, str) or key not in scenario_values:
                rejected[f"scenario_scores.{key}"] = "unknown_enum_removed"
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                rejected[f"scenario_scores.{key}"] = "invalid_score_removed"
                continue
            bounded = min(max(float(value), 0.0), 1.0)
            if bounded != value:
                rejected[f"scenario_scores.{key}"] = "score_clamped"
            valid_scores[key] = bounded
        if valid_scores != scenario_scores:
            sanitized["scenario_scores"] = valid_scores

        primary = sanitized.get("primary_scenario")
        if primary is None and sanitized.get("branch") == "rag" and valid_scores:
            primary = max(valid_scores, key=valid_scores.get)
            sanitized["primary_scenario"] = primary
            rejected["primary_scenario.repair"] = "primary_missing_repaired"
        selected_scenarios = [
            item
            for item in [primary, *(sanitized.get("secondary_scenarios") or [])]
            if isinstance(item, str) and item in scenario_values
        ]
        confidence = _bounded_semantic_confidence(sanitized.get("confidence"))
        for index, scenario in enumerate(selected_scenarios):
            if scenario in valid_scores:
                continue
            valid_scores[scenario] = confidence if index == 0 else min(confidence, 0.79)
            rejected[f"scenario_scores.{scenario}"] = (
                "score_missing_filled" if index == 0 else "secondary_missing_score"
            )
        sanitized["scenario_scores"] = valid_scores

    goals = sanitized.get("goals")
    if isinstance(goals, list):
        valid_goals: list[str] = []
        for index, item in enumerate(goals):
            if not isinstance(item, str) or item not in goal_values:
                rejected[f"goals.{index}"] = "unknown_enum_removed"
                continue
            if item in valid_goals:
                rejected[f"goals.{index}"] = "duplicate_label_removed"
                continue
            valid_goals.append(item)
        bounded_goals = valid_goals[:3]
        if bounded_goals != goals:
            sanitized["goals"] = bounded_goals
        if len(valid_goals) > 3:
            rejected["goals.limit"] = "label_count_bounded"

    goal_scores = sanitized.get("goal_scores")
    if isinstance(goal_scores, dict):
        valid_goal_scores: dict[str, float] = {}
        for key, value in goal_scores.items():
            if not isinstance(key, str) or key not in goal_values:
                rejected[f"goal_scores.{key}"] = "unknown_enum_removed"
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                rejected[f"goal_scores.{key}"] = "invalid_score_removed"
                continue
            bounded = min(max(float(value), 0.0), 1.0)
            if bounded != value:
                rejected[f"goal_scores.{key}"] = "score_clamped"
            valid_goal_scores[key] = bounded
        if valid_goal_scores != goal_scores:
            sanitized["goal_scores"] = valid_goal_scores
        if not sanitized.get("goals") and sanitized.get("branch") == "rag" and valid_goal_scores:
            sanitized["goals"] = sorted(
                valid_goal_scores,
                key=lambda goal: (-valid_goal_scores[goal], goal),
            )[:3]
            rejected["goals"] = "goal_order_normalized"
        confidence = _bounded_semantic_confidence(sanitized.get("confidence"))
        for index, goal in enumerate(sanitized.get("goals") or []):
            if goal in valid_goal_scores:
                continue
            valid_goal_scores[goal] = confidence if index == 0 else min(confidence, 0.79)
            rejected[f"goal_scores.{goal}"] = (
                "score_missing_filled" if index == 0 else "secondary_missing_score"
            )
        sanitized["goal_scores"] = valid_goal_scores

    reasoning = sanitized.get("reasoning_summary")
    if isinstance(reasoning, str) and len(reasoning) > 300:
        sanitized["reasoning_summary"] = reasoning[:300]
        rejected["reasoning_summary"] = "max_length"
    return sanitized, rejected


def _bounded_semantic_confidence(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return min(max(float(value), 0.0), 1.0)
    return 0.5


_SEMANTIC_DECISION_FIELDS = (
    "branch",
    "primary_scenario",
    "secondary_scenarios",
    "scenario_scores",
    "goals",
    "goal_scores",
    "confidence",
    "reasoning_summary",
)


def _raw_semantic_decision_trace(content: str | None) -> dict[str, object] | None:
    """Keep only the bounded classification payload, never hidden reasoning."""

    if not content:
        return None
    cleaned = content.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1])
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return {
        field: _bounded_trace_value(payload[field])
        for field in _SEMANTIC_DECISION_FIELDS
        if field in payload
    }


def _bounded_trace_value(value: object) -> object:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value[:300]
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else 0.0
    if isinstance(value, list):
        return [_bounded_trace_value(item) for item in value[:10]]
    if isinstance(value, dict):
        return {
            str(key)[:80]: _bounded_trace_value(item)
            for key, item in list(value.items())[:20]
        }
    return str(value)[:300]


def _sanitized_semantic_decision_trace(
    correction: RouteCorrection,
) -> dict[str, object]:
    goals = list(dict.fromkeys([*correction.goals, *correction.secondary_goals]))[:3]
    if correction.primary_goal is not None:
        secondary = [goal for goal in goals if goal != correction.primary_goal]
        goals = [correction.primary_goal, *secondary][:3]
    return {
        "branch": correction.branch,
        "primary_scenario": (
            correction.primary_scenario.value if correction.primary_scenario else None
        ),
        "secondary_scenarios": [item.value for item in correction.secondary_scenarios],
        "scenario_scores": {
            key.value: float(value) for key, value in correction.scenario_scores.items()
        },
        "goals": [item.value for item in goals],
        "goal_scores": {
            key.value: float(value) for key, value in correction.goal_scores.items()
        },
        "confidence": correction.confidence,
        "reasoning_summary": correction.reasoning_summary,
    }


def _normalize_semantic_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize the Phase 3.1 semantic contract into RouteCorrection fields."""

    normalized = dict(payload)
    branch = normalized.get("branch")
    if "task_type" not in normalized and branch in {"rag", "out_of_scope"}:
        normalized["task_type"] = "relationship_advice" if branch == "rag" else "out_of_scope"
    if "task_confidence" not in normalized and normalized.get("confidence") is not None:
        normalized["task_confidence"] = normalized["confidence"]

    raw_goals = normalized.get("goals")
    if isinstance(raw_goals, list):
        labels: list[str] = []
        scores = dict(normalized.get("goal_scores") or {})
        for item in raw_goals:
            if isinstance(item, str):
                label = item
                confidence = None
            elif isinstance(item, dict):
                label = item.get("label") or item.get("goal")
                confidence = item.get("confidence")
            else:
                continue
            if not isinstance(label, str) or not label:
                continue
            labels.append(label)
            if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
                scores[label] = float(confidence)
        if labels:
            normalized["goals"] = labels[:3]
            normalized.setdefault("primary_goal", labels[0])
            normalized.setdefault("secondary_goals", labels[1:3])
        normalized["goal_scores"] = scores

    raw_scenarios = normalized.get("scenarios")
    if isinstance(raw_scenarios, list):
        labels = []
        scores = dict(normalized.get("scenario_scores") or {})
        for item in raw_scenarios:
            if isinstance(item, str):
                label = item
                confidence = None
            elif isinstance(item, dict):
                label = item.get("label") or item.get("scenario")
                confidence = item.get("confidence")
            else:
                continue
            if not isinstance(label, str) or not label:
                continue
            labels.append(label)
            if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
                scores[label] = float(confidence)
        if labels:
            normalized.setdefault("primary_scenario", labels[0])
            normalized.setdefault("secondary_scenarios", labels[1:3])
        normalized["scenario_scores"] = scores
        normalized.pop("scenarios", None)

    if normalized.get("scenario_confidence") is None and normalized.get("confidence") is not None:
        normalized["scenario_confidence"] = normalized["confidence"]
    return normalized


def _sanitize_date_plan_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Drop only malformed nested Slots so a valid route can still be used."""

    sanitized = dict(payload)
    rejected: dict[str, str] = {}
    raw_slots = sanitized.get("date_plan")
    if raw_slots is not None and not isinstance(raw_slots, dict):
        sanitized["date_plan"] = {}
        rejected["date_plan"] = "invalid_schema"
    elif isinstance(raw_slots, dict):
        valid_slots: dict[str, Any] = {}
        for field, value in raw_slots.items():
            if field not in DatePlanSlots.model_fields:
                rejected[field] = "unknown_field"
                continue
            try:
                parsed = DatePlanSlots.model_validate({field: value})
            except ValidationError:
                rejected[field] = "invalid_schema"
                continue
            valid_slots[field] = getattr(parsed, field)
        sanitized["date_plan"] = valid_slots

    raw_patch = sanitized.get("date_patch")
    if raw_patch is not None and not isinstance(raw_patch, dict):
        sanitized["date_patch"] = None
        rejected["date_patch"] = "invalid_schema"
    elif isinstance(raw_patch, dict):
        valid_patch: dict[str, Any] = {}
        for field, value in raw_patch.items():
            # Provenance is assigned only after deterministic verification.
            if field == "source_by_field":
                continue
            if field not in DatePlanPatch.model_fields:
                rejected[f"date_patch.{field}"] = "unknown_field"
                continue
            try:
                parsed = DatePlanPatch.model_validate({field: value})
            except ValidationError:
                rejected[f"date_patch.{field}"] = "invalid_schema"
                continue
            valid_patch[field] = getattr(parsed, field)
        sanitized["date_patch"] = valid_patch

    raw_operations = sanitized.get("date_operations")
    if raw_operations is not None and not isinstance(raw_operations, list):
        sanitized["date_operations"] = []
        rejected["date_operations"] = "invalid_schema"
    elif isinstance(raw_operations, list):
        valid_operations: list[DatePlanOperation] = []
        for index, raw_operation in enumerate(raw_operations):
            try:
                valid_operations.append(DatePlanOperation.model_validate(raw_operation))
            except ValidationError:
                rejected[f"date_operations.{index}"] = "invalid_schema"
        sanitized["date_operations"] = valid_operations
    return sanitized, rejected


def _validate_evidence(correction: RouteCorrection, route_input: RouteInput) -> None:
    source = "\n".join(
        [route_input.latest_query, *(message.content for message in route_input.recent_messages)]
    )
    invalid = [span for span in correction.evidence_spans if span not in source]
    if invalid:
        raise ValueError(f"路由证据不在对话原文中：{invalid[0]}")


_SEMANTIC_SYSTEM_PROMPT = """
You are LoveApp's semantic router. Classify the user's current request only;
do not answer it and do not produce date-planning fields.

Return exactly one JSON object with exactly these fields:
branch, primary_scenario, secondary_scenarios, scenario_scores, goals,
goal_scores, confidence, reasoning_summary.

Allowed branch values: rag, out_of_scope.
Allowed scenario values: pursuit, conflict, chat_analysis,
relationship_maintenance, boundary, breakup.
Allowed goal values: initiate, understand, progress, repair, communicate,
set_boundary, end_relationship.

Use branch=rag for relationship advice and branch=out_of_scope for requests
outside relationship advice. For rag, choose the main scenario and up to two
secondary scenarios. Choose up to three goals in priority order. Keep every
score and confidence between 0 and 1. The score maps may contain only the
selected labels. For out_of_scope, use null, [], {}, [], {}, and a short
reasoning_summary in the corresponding fields.

Scenario rules:
- pursuit: starting or advancing an early relationship, contact, or an invite.
- chat_analysis: interpreting replies, tone, frequency, or interaction signals.
- conflict: an explicit quarrel, cold war, blame dispute, or escalation.
- relationship_maintenance: ongoing relationship habits and long-term upkeep.
- boundary: privacy, consent, autonomy, or social/financial/body/digital limits.
- breakup: ending, separation, reconciliation after ending, or post-breakup contact.

Goal rules:
- initiate starts contact or an early invite; understand interprets meaning/signals.
- progress decides or takes the next step; repair fixes an existing conflict.
- communicate is explicit discussion or negotiation; set_boundary states limits.
- end_relationship ends the relationship or manages post-breakup matters.

Use the latest query as the primary evidence. Recent messages only resolve
pronouns or omitted context. Do not include markdown, explanations, or any
keys outside the contract.
""".strip()


_SYSTEM_PROMPT = """
你是 LoveApp 的语义路由校正器，只分类，不回答用户问题。只输出一个合法 JSON 对象。

TaskType：
- general_chat：寒暄、感谢、告别或不需要恋爱建议和地点规划的简短对话。
- relationship_advice：追求、关系判断、聊天分析、冲突、边界、分手或关系经营建议。
- date_planning：用户明确希望安排约会、推荐真实餐厅/地点或生成行程。
- out_of_scope：编程、医疗诊断、法律分析、学术作业、新闻写作等当前产品不支持的请求。

DateTaskIntent：none、new_request、supplement、continue、switch、cancel。
DatePlanMutation：none、add、replace、remove、reorder、update_constraint、replan。
DateRequestMode：none、evaluate、category_recommendation、place_search、itinerary、modify。

AdviceScenario：pursuit、conflict、chat_analysis、relationship_maintenance、boundary、breakup。
AdviceGoal：initiate、understand、progress、repair、communicate、set_boundary、end_relationship。

Phase 3.1 语义边界（先判断用户真正要解决的核心问题）：
- pursuit：从陌生、认识或暧昧走向进一步关系；主动接触、邀约、建立连接、推进早期关系。
- chat_analysis：主要理解对方聊天行为、回复变化或互动信号的含义；如果重点是下一步采取行动，
  应同时考虑 progress 或 communicate，而不是只标 chat_analysis。
- conflict：已经发生明确争执、反复矛盾、冷战、反击或责任争议，核心是修复冲突模式。
- relationship_maintenance：关系已经存在且没有单次冲突作为中心，关注长期相处、亲密感、联系频率、
  生活协调或未来发展。
- boundary：私人空间、自主权、同意、隐私、社交/财务/身体/数字边界或公开关系边界。若“查手机”
  的核心是未经同意查看隐私，primary 应为 boundary，conflict 最多作为 secondary。
- breakup：考虑结束、明确分手，或分手后的边界、恢复、共同事务和持续联系。

Goal 语义边界（允许多个，不要只因“怎么办/怎么处理”默认 communicate）：
- initiate：首次或早期开启联系、聊天、发起邀约。
- understand：判断含义、理解行为、评估现状和关系信号；“回复越来越慢是什么意思”必须保留 understand。
- progress：决定是否推进下一步、继续邀约、确认关系或推进关系状态。
- repair：修复已经发生的冲突，降低升级，打破冷战/反击/翻旧账循环。
- communicate：用户明确需要表达、讨论、协商或对话方式；不是所有建议请求的默认标签。
- set_boundary：明确允许/不允许的隐私、空间、同意、身体、数字、财务或社交边界。
- end_relationship：结束关系、明确分手，或处理分手后的持续联系与共同事务。

普通关系分支的 branch 只能是 rag 或 out_of_scope。高风险/敏感请求由上游安全规则处理，
不得通过语义输出绕过 Safety。可返回 scenario_scores/goal_scores（0 到 1）以及简短
reasoning_summary；不要输出长篇推理过程。

规则：
1. latest_query 是当前意图的主要依据；recent_messages 只用于理解指代和省略。
2. active_task 只提供弱提示。用户明确切换任务时必须跟随最新输入。
3. forced_task 非空时 task_type 必须等于 forced_task。
4. 关系建议允许一个 primary_scenario 和最多两个 secondary_scenarios；按用户真正要解决的
   问题排序，不能因为文本偶然出现“聊天”等词就抢占主场景。
5. AdviceGoal 同样允许主目标和次目标。
6. 跨任务复合请求使用 task_type 表示先执行的主任务，secondary_tasks 保留后续任务。
 7. 先判断 DateRequestMode：
    - evaluate：用户在评价一次邀约、见面或活动想法是否合适；
    - category_recommendation：只询问菜系、口味、活动类别等方向建议；
    - place_search：要求搜索或推荐现实中的具体餐厅、场馆、景点或地点；
    - itinerary：要求生成完整约会安排、行程、路线或攻略；
    - modify：补充或修改已有约会任务的参数或节点；
    - none：没有约会相关请求模式。
    只有 place_search、itinerary、modify 可以输出 date_planning。evaluate 和
    category_recommendation 应输出 relationship_advice，不能启动城市、日期、预算收集流程。
    date_planning 还需要区分：
    - new_request：用户明确要求本助手安排约会、搜索真实地点或生成行程；
    - supplement：用户在已有约会任务中补充城市、区域、日期时间、预算、偏好、交通方式或限制；
    - continue：继续讨论当前计划但没有新参数；
    - switch：明确转去恋爱咨询等其他任务；
    - cancel：取消当前约会规划。
    仅仅描述“我打算约她看电影、吃饭，你看怎么样”或询问这个行动是否合适，属于
   relationship_advice 的 pursuit/progress，不属于 date_planning。提到电影、吃饭、逛街、约她
   只是行动内容，不是对本助手的规划请求。评价性表达（如“你看怎么样”“这样合适吗”“你觉得呢”）
   在没有明确“帮我安排/推荐/生成/规划”等请求动词时优先归入关系建议。
   用户回答上一轮关系建议的追问时，即使回答里提到过去逛过公园、漫展或吃过饭，也属于
   relationship_advice；过去发生的互动事实不是新的约会行程参数。
   有 date_task_state 时，优先判断 latest_query 是否是在回答此前追问。单独的“上海”、
   “预算 300”、“周六下午”都可以是 supplement，不要重新解释成普通聊天。
   已有计划时还要判断 date_mutation：
   - add：增加景点、活动、餐厅或其他节点，默认保留已有节点；
   - replace：把已有节点或约束换成新的内容；
   - remove：删除已有节点；
   - reorder：只调整已有节点顺序；
   - update_constraint：修改预算、日期、交通等约束；
   - replan：用户明确要求重新规划、换一套或全部重排。
   “增加到行程中”属于 add，不能因为出现新关键词就自动 replan。
8. date_patch 只能提取 latest_query 当前轮明确提供或修改的
   city、area、plan_mode、date、end_date、day_count、
   nights、target_day、start_time、budget、budget_scope、preferences、dining_keywords、
   activity_keywords、meal_keywords、schedule_hints、replace_place_names、transport_mode、
    notes、constraints、lodging_notes。不得猜测地点、预算、日期或时间，也不得把 runtime_context
    中已经存在但本轮未提到的值重复写入 date_patch。
    date 和 end_date 用 YYYY-MM-DD，
   start_time 用 ISO-8601；单日使用 single_day，多日使用 multi_day；“每天 500”使用
   per_day，默认总预算使用 total；target_day 只提取“第二天”等明确指定的目标天；
   replace_place_names 只记录用户明确要求删除或换掉的现有地点名称；
   meal_keywords 的键只能使用 breakfast、lunch、dinner，值是用户明确提到的餐饮关键词；
   schedule_hints 只记录明确的时间或先后提示，例如“下午”“看完电影后”。
    transport_mode 只能是 walking、transit、driving、cycling 或 null。
   date_operations 用 typed 数组表达复杂业务动作，type 只能是 update_constraint、add_stop、
   remove_stop、replace_stop、move_stop、replan。每个 operation 的 source_span 必须逐字来自
   latest_query。stop 使用 target/payload 表达，payload.kind 只能是 dining、activity、cafe、other；
   meal_type 只能是 breakfast、lunch、dinner；晚饭后等关系放在 after/before。模型只理解动作，
   不得直接修改 runtime_context，也不得复制历史 operation。
9. evidence_spans 必须逐字来自 latest_query 或 recent_messages，最多 8 条。每一个 date_patch
   字段都必须有对应的用户原文依据；无法确认的字段留空，不得因为默认常识补齐。
10. task_confidence 和 scenario_confidence 使用 0 到 1。确实需要用户补充才能路由时，
   needs_clarification 才为 true。
 11. rule_result.task_type 是 Python 的一级路由候选。当当前文本明确包含关系建议或约会规划
     意图时，保留对应 task_type；如果规则只是因为“她/见面/活动”等词产生弱候选，必须按
     latest_query 的真实目标重新判断。已有 date_task_state 时，明确的关系问题可以切换到
     relationship_advice，真正的行程安排或参数补充则应保留 date_planning。只有真实的寒暄、
     感谢、告别或没有任务意图的短句才能使用 general_chat。

必须输出字段：task_type、branch、secondary_tasks、task_confidence、primary_goal、secondary_goals、
primary_scenario、secondary_scenarios、scenario_confidence、needs_clarification、
scenario_scores、goals、goal_scores、confidence、reasoning_summary、evidence_spans、date_patch、date_operations、date_request_mode、date_intent、date_mutation。为兼容旧调用方可以同时输出
date_plan，但 date_patch 是当前轮增量；数组无内容时输出 []，
可空标量输出 null。
""".strip()

_DATE_SLOT_INSTRUCTIONS = """
Date plan search fields:
- Preserve a date range such as “周五到周日” as date + end_date + day_count.
  Preserve “三天两夜” as day_count=3 and nights=2 even if the start date is unknown.
- target_day is the explicit day being edited, such as 2 in “第二天下午换成博物馆”.
- lodging_notes preserve stated hotel or accommodation constraints. They are not POI search
  keywords in the current version.
- dining_keywords: explicit cuisine or restaurant terms for the dining stop,
  such as 西餐, 日料, 火锅, or 素食. Keep only terms stated in the source.
- activity_keywords: explicit venue/activity terms such as 博物馆, 美术馆, 景点,
  电影院, or 公园. Keep only terms stated in the source.
- excluded_keywords: explicit things the user says not to eat, visit, or use.
These are search constraints, not general preferences. Do not invent a venue name.
- meal_keywords: preserve meal roles when the user says things such as
  "午餐吃日料" or "晚饭吃火锅"; do not flatten these roles into one list.
- schedule_hints: preserve explicit relative timing such as "下午" and
  "看完电影后"; do not infer an exact clock time.
- replace_place_names: exact existing place names that the user explicitly
  wants removed or replaced, such as 辅德里公园 in "不去辅德里公园，换一个博物馆".
""".strip()

import json
from contextvars import ContextVar
from time import perf_counter
from typing import Any, Literal

from openai import AsyncOpenAI
from pydantic import SecretStr, ValidationError

from loveapp.domain.date_operations import DatePlanOperation, DateSemanticParseResult
from loveapp.domain.runtime_context import RuntimeContext


class OpenAICompatibleDateSemanticParser:
    """Translate one DatePlan turn into typed operations without mutating state."""

    def __init__(
        self,
        *,
        api_key: SecretStr,
        base_url: str,
        model: str,
        timeout_seconds: float = 20,
        max_retries: int = 0,
        max_tokens: int = 2048,
        thinking: Literal["enabled", "disabled"] = "disabled",
        prompt_version: str = "date-semantic-v1.1",
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._thinking = thinking
        self._prompt_version = prompt_version
        self._last_telemetry: ContextVar[dict[str, Any] | None] = ContextVar(
            "date_semantic_last_telemetry",
            default=None,
        )
        self._client = AsyncOpenAI(
            api_key=api_key.get_secret_value(),
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    @property
    def semantic_profile(self) -> dict[str, str]:
        return {
            "model": self._model,
            "thinking": self._thinking,
            "prompt_version": self._prompt_version,
        }

    @property
    def last_telemetry(self) -> dict[str, Any]:
        return self._last_telemetry.get() or {}

    @last_telemetry.setter
    def last_telemetry(self, value: dict[str, Any]) -> None:
        self._last_telemetry.set(value)

    async def parse_date_operations(
        self,
        text: str,
        runtime_context: RuntimeContext | None,
        deterministic_operations: tuple[DatePlanOperation, ...],
    ) -> DateSemanticParseResult:
        started = perf_counter()
        telemetry: dict[str, Any] = {
            "model": self._model,
            "thinking": self._thinking,
            "prompt_version": self._prompt_version,
            "input_tokens": None,
            "output_tokens": None,
            "duration_ms": None,
        }
        self.last_telemetry = telemetry
        request_kwargs = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _DATE_SEMANTIC_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        _semantic_payload(text, runtime_context, deterministic_operations),
                        ensure_ascii=False,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": self._max_tokens,
            "extra_body": {"thinking": {"type": self._thinking}},
        }
        try:
            completion = await self._client.chat.completions.create(**request_kwargs)
            usage = getattr(completion, "usage", None)
            telemetry.update(
                {
                    "input_tokens": getattr(usage, "prompt_tokens", None),
                    "output_tokens": getattr(usage, "completion_tokens", None),
                }
            )
            choice = completion.choices[0]
            try:
                result = _parse_date_semantic_response(
                    choice.message.content,
                    choice.finish_reason,
                )
            except DateSemanticSchemaError as exc:
                telemetry.update(
                    {
                        "validation_error_path": exc.path,
                        "invalid_field": exc.invalid_field,
                        "raw_operation_type": exc.raw_operation_type,
                    }
                )
                raise
            _validate_operation_evidence(result, text)
            return result
        finally:
            telemetry["duration_ms"] = round(
                (perf_counter() - started) * 1000,
                3,
            )

    async def aclose(self) -> None:
        await self._client.close()


def _semantic_payload(
    text: str,
    runtime_context: RuntimeContext | None,
    deterministic_operations: tuple[DatePlanOperation, ...],
) -> dict[str, object]:
    active = runtime_context.active_date_plan if runtime_context is not None else None
    current_plan = active.current_plan if active is not None else None
    scalar_context = (
        active.model_dump(mode="json", exclude={"current_plan", "missing_fields"})
        if active is not None
        else None
    )
    return {
        "latest_query": text,
        "date_context": (
            {
                **(scalar_context or {}),
                "current_plan": (
                    {
                        "summary": current_plan.summary,
                        "items": [
                            {
                                "ordinal": index,
                                "place_id": item.place.id,
                                "place_name": item.place.name,
                                "kind": item.place.category.value,
                                "day": item.day_index,
                                "order": item.order,
                                "meal_type": item.meal_type,
                                "time_label": item.time_label,
                                "keyword": item.slot_keyword,
                            }
                            for index, item in enumerate(
                                sorted(
                                    current_plan.items,
                                    key=lambda value: (value.day_index, value.order),
                                ),
                                start=1,
                            )
                        ],
                    }
                    if current_plan is not None
                    else None
                ),
            }
            if active is not None
            else None
        ),
        "deterministic_operations": [
            operation.model_dump(mode="json") for operation in deterministic_operations
        ],
        "allowed_operation_types": [
            "update_constraint",
            "update_requirement",
            "add_stop",
            "remove_stop",
            "replace_stop",
            "move_stop",
            "replan",
        ],
    }


def _parse_date_semantic_response(
    content: str | None,
    finish_reason: str | None,
) -> DateSemanticParseResult:
    if not content:
        raise ValueError(
            f"日期语义模型没有返回正文，finish_reason={finish_reason or 'unknown'}。"
        )
    cleaned = content.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1])
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("日期语义模型返回内容不符合 typed operation 结构。") from exc
    try:
        return DateSemanticParseResult.model_validate(payload)
    except ValidationError as exc:
        error = exc.errors()[0] if exc.errors() else {}
        location = error.get("loc", ())
        path = ".".join(str(part) for part in location) or None
        operation_index = (
            location[1]
            if len(location) > 1 and location[0] == "operations" and isinstance(location[1], int)
            else None
        )
        operations = payload.get("operations") if isinstance(payload, dict) else None
        raw_operation_type = None
        if (
            isinstance(operations, list)
            and operation_index is not None
            and operation_index < len(operations)
            and isinstance(operations[operation_index], dict)
        ):
            raw_operation_type = operations[operation_index].get("type")
        raise DateSemanticSchemaError(
            path=path,
            invalid_field=str(location[-1]) if location else None,
            raw_operation_type=(
                str(raw_operation_type) if raw_operation_type is not None else None
            ),
        ) from exc


class DateSemanticSchemaError(ValueError):
    def __init__(
        self,
        *,
        path: str | None,
        invalid_field: str | None,
        raw_operation_type: str | None,
    ) -> None:
        super().__init__("日期语义模型返回内容不符合 typed operation 结构。")
        self.path = path
        self.invalid_field = invalid_field
        self.raw_operation_type = raw_operation_type


def _validate_operation_evidence(
    result: DateSemanticParseResult,
    text: str,
) -> None:
    for operation in result.operations:
        if operation.source_span is None or operation.source_span not in text:
            raise ValueError("日期语义模型 operation 缺少当前轮逐字 source_span。")


_DATE_SEMANTIC_SYSTEM_PROMPT = """
你是 LoveApp 的 DatePlan Domain Semantic Parser，不生成最终回答，不搜索地点，
不修改数据库或任务状态。你的唯一任务是把用户当前轮自然语言转换为结构化
DatePlanOperation。只输出 JSON：{"operations": [...], "unresolved_references": [...]}。

允许的 type 只有 update_constraint、update_requirement、add_stop、remove_stop、
replace_stop、move_stop、replan。
每个 operation 的 source_span 必须逐字来自 latest_query。constraint 只能写入
constraint_field/constraint_value；payload.kind 只能是 dining、activity、cafe、other；
meal_type 只能是 breakfast、lunch、dinner。

地点节点的局部约束只能写入 payload.constraints：
- max_cost_per_person：该节点的人均价格上限；
- min_rating：该节点的最低评分；
- preferred_area：只适用于该节点的区域；
- max_distance_meters：该节点相对前一节点的最大路线距离。
“晚餐人均500以内、评分4.9以上、陆家嘴附近”不得改写全局 budget 或 area。

规则：
- 解析复合操作、相对 scalar 更新、指代、否定、先后关系和 generic replacement。
- “预算从600提高到800”取新值 800。
- “第二个地方换近一点”输出 replace_stop，target.ordinal=2，replacement_preferences=["nearby"]。
- “电影放晚饭后”输出 move_stop，电影为 target，payload.after="dinner"。
- 修改已有地点时，未提及的 payload.constraints 不要凭空创建或清空。
- “博物馆，海洋馆也行”等 ONE_OF 选择输出多个 add_stop，并为这些操作设置相同的
  alternative_group；普通“火锅和烧烤”是两个独立要求，不设置 alternative_group。
- 把两个已有独立 Requirement 改成“二选一”时输出 update_requirement；
  requirement_update.targets 使用 date_context.requirements 中的 requirement_id，且每个
  target 同时携带能在 source_span 中逐字核验的 stop_reference；设置 min_satisfied=1、
  max_satisfied=1。不得用 add_stop 重建已有 Requirement。
- 否定的操作不得输出，例如“晚餐不要换，电影放晚饭后”不能生成晚餐 replacement。
- deterministic_operations 是高精度候选，可保留、补充或纠正，不得盲目复制。
- 当前计划有多个目标都符合“那个餐厅”等引用时不得猜测；不输出该修改，并在
  unresolved_references 中列出需要澄清的目标。
- 不猜测用户未提供的地点、数值或时段。输出仍会经过确定性 Verifier，模型没有最终业务权力。
""".strip()

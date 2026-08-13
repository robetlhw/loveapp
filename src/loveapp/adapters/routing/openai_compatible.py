import json
from time import perf_counter
from typing import Any, Literal

from openai import AsyncOpenAI
from pydantic import SecretStr, ValidationError

from loveapp.domain.routing import DatePlanSlots, RouteCorrection, RouteInput, RouteResult


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
        prompt_version: str = "routing-v3.0",
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._thinking = thinking
        self._prompt_version = prompt_version
        self.last_telemetry: dict[str, Any] = {}
        self._client = AsyncOpenAI(
            api_key=api_key.get_secret_value(),
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    async def correct(
        self,
        route_input: RouteInput,
        rule_result: RouteResult,
    ) -> RouteCorrection:
        started = perf_counter()
        self.last_telemetry = {
            "model": self._model,
            "prompt_version": self._prompt_version,
            "input_tokens": None,
            "output_tokens": None,
            "duration_ms": None,
            "attempt_count": 0,
        }
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT + "\n" + _DATE_SLOT_INSTRUCTIONS},
            {
                "role": "user",
                "content": _build_prompt(route_input, rule_result),
            },
        ]
        last_error: ValueError | None = None
        for attempt in range(2):
            # Count a request before sending it so a transport failure is still
            # observable in the fallback trace.
            self.last_telemetry["attempt_count"] = attempt + 1
            request_kwargs = {
                "model": self._model,
                "messages": messages,
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": self._max_tokens,
            }
            if self._thinking is not None:
                request_kwargs["extra_body"] = {"thinking": {"type": self._thinking}}
            completion = await self._client.chat.completions.create(**request_kwargs)
            usage = getattr(completion, "usage", None)
            self.last_telemetry.update(
                {
                    "input_tokens": _accumulate_token_count(
                        self.last_telemetry.get("input_tokens"),
                        getattr(usage, "prompt_tokens", None),
                    ),
                    "output_tokens": _accumulate_token_count(
                        self.last_telemetry.get("output_tokens"),
                        getattr(usage, "completion_tokens", None),
                    ),
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                }
            )
            choice = completion.choices[0]
            content = choice.message.content
            try:
                correction, slot_parse_rejections = _parse_response_with_slot_rejections(
                    content,
                    choice.finish_reason,
                )
                self.last_telemetry["slot_parse_rejections"] = slot_parse_rejections
                _validate_evidence(correction, route_input)
                return correction
            except ValueError as exc:
                last_error = exc
                if attempt == 1:
                    break
                messages.extend(
                    [
                        {"role": "assistant", "content": content or ""},
                        {
                            "role": "user",
                            "content": (
                                f"上一次输出未通过结构校验。请修正并只输出 JSON。校验错误：{exc}"
                            ),
                        },
                    ]
                )
        raise last_error or ValueError("路由校正结果无法解析。")

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


def _build_prompt(route_input: RouteInput, rule_result: RouteResult) -> str:
    payload = {
        "latest_query": route_input.latest_query,
        "recent_messages": [
            {"role": message.role.value, "content": message.content}
            for message in route_input.recent_messages[-6:]
        ],
        "active_task": route_input.active_task.value if route_input.active_task else None,
        "forced_task": route_input.forced_task.value if route_input.forced_task else None,
        "pending_task": route_input.pending_task.value if route_input.pending_task else None,
        "pending_task_reason": route_input.pending_task_reason,
        "last_clarification_reason": route_input.last_clarification_reason,
        "clarification_attempt_count": route_input.clarification_attempt_count,
        "date_task_state": (
            route_input.date_task_state.model_dump(mode="json")
            if route_input.date_task_state
            else None
        ),
        "rule_result": {
            "task_type": rule_result.task_type.value,
            "task_confidence": rule_result.task_confidence,
            "task_scores": {key.value: value for key, value in rule_result.task_scores.items()},
            "primary_goal": (rule_result.primary_goal.value if rule_result.primary_goal else None),
            "goal_scores": {key.value: value for key, value in rule_result.goal_scores.items()},
            "primary_scenario": (
                rule_result.primary_scenario.value if rule_result.primary_scenario else None
            ),
            "scenario_scores": {
                key.value: value for key, value in rule_result.scenario_scores.items()
            },
            "scenario_confidence": rule_result.scenario_confidence,
            "date_plan": rule_result.date_plan.model_dump(mode="json"),
            "date_request_mode": rule_result.date_request_mode.value,
            "date_intent": rule_result.date_intent.value,
            "date_mutation": rule_result.date_mutation.value,
            "date_missing_fields": rule_result.date_missing_fields,
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def _parse_response(content: str | None, finish_reason: str | None) -> RouteCorrection:
    correction, _ = _parse_response_with_slot_rejections(content, finish_reason)
    return correction


def _parse_response_with_slot_rejections(
    content: str | None,
    finish_reason: str | None,
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
    sanitized_payload, slot_parse_rejections = _sanitize_date_plan_payload(payload)
    try:
        return RouteCorrection.model_validate(sanitized_payload), slot_parse_rejections
    except ValidationError as exc:
        raise ValueError("路由模型返回内容不符合 RouteCorrection 结构。") from exc


def _sanitize_date_plan_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Drop only malformed nested Slots so a valid route can still be used."""

    sanitized = dict(payload)
    raw_slots = sanitized.get("date_plan")
    if raw_slots is None:
        return sanitized, {}
    if not isinstance(raw_slots, dict):
        sanitized["date_plan"] = {}
        return sanitized, {"date_plan": "invalid_schema"}

    valid_slots: dict[str, Any] = {}
    rejected: dict[str, str] = {}
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
    return sanitized, rejected


def _validate_evidence(correction: RouteCorrection, route_input: RouteInput) -> None:
    source = "\n".join(
        [route_input.latest_query, *(message.content for message in route_input.recent_messages)]
    )
    invalid = [span for span in correction.evidence_spans if span not in source]
    if invalid:
        raise ValueError(f"路由证据不在对话原文中：{invalid[0]}")


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
8. date_plan 只能提取对话明确提供的 city、area、plan_mode、date、end_date、day_count、
   nights、target_day、start_time、budget、budget_scope、preferences、dining_keywords、
   activity_keywords、meal_keywords、schedule_hints、replace_place_names、transport_mode、
    notes、constraints、lodging_notes。不得猜测地点、预算、日期或时间。
    date 和 end_date 用 YYYY-MM-DD，
   start_time 用 ISO-8601；单日使用 single_day，多日使用 multi_day；“每天 500”使用
   per_day，默认总预算使用 total；target_day 只提取“第二天”等明确指定的目标天；
   replace_place_names 只记录用户明确要求删除或换掉的现有地点名称；
   meal_keywords 的键只能使用 breakfast、lunch、dinner，值是用户明确提到的餐饮关键词；
   schedule_hints 只记录明确的时间或先后提示，例如“下午”“看完电影后”。
   transport_mode 只能是 walking、transit、driving、cycling 或 null。
9. evidence_spans 必须逐字来自 latest_query 或 recent_messages，最多 8 条。每一个 date_plan
   字段都必须有对应的用户原文依据；无法确认的字段留空，不得因为默认常识补齐。
10. task_confidence 和 scenario_confidence 使用 0 到 1。确实需要用户补充才能路由时，
   needs_clarification 才为 true。
 11. rule_result.task_type 是 Python 的一级路由候选。当当前文本明确包含关系建议或约会规划
     意图时，保留对应 task_type；如果规则只是因为“她/见面/活动”等词产生弱候选，必须按
     latest_query 的真实目标重新判断。已有 date_task_state 时，明确的关系问题可以切换到
     relationship_advice，真正的行程安排或参数补充则应保留 date_planning。只有真实的寒暄、
     感谢、告别或没有任务意图的短句才能使用 general_chat。

必须输出字段：task_type、secondary_tasks、task_confidence、primary_goal、secondary_goals、
primary_scenario、secondary_scenarios、scenario_confidence、needs_clarification、
evidence_spans、date_plan、date_request_mode、date_intent、date_mutation。数组无内容时输出 []，
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

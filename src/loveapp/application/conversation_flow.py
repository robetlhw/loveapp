import re

from loveapp.domain.conversation import ConversationFlowState
from loveapp.domain.enums import RiskLevel, TaskType
from loveapp.domain.memory import utc_now
from loveapp.domain.routing import RecentRiskState, RouteResult


def is_pending_continuation(text: str, pending_task: TaskType | None) -> bool:
    if pending_task is None:
        return False
    compact = re.sub(r"[，。！？!?、~～ ]", "", text.casefold())
    return compact in {
        "好",
        "好的",
        "行",
        "可以",
        "继续",
        "好继续",
        "好的继续",
        "行继续",
        "可以继续",
        "接着来",
        "开始吧",
        "那继续",
    }


def is_pending_cancellation(text: str, pending_task: TaskType | None) -> bool:
    if pending_task is None:
        return False
    normalized = text.casefold()
    return any(
        marker in normalized
        for marker in ("算了", "不用了", "不安排了", "取消", "先不", "不继续")
    )


def advance_conversation_flow(
    current: ConversationFlowState,
    route: RouteResult,
) -> ConversationFlowState:
    """Apply one completed route decision to durable short-lived state."""

    if (
        route.risk_level in {RiskLevel.HIGH, RiskLevel.SENSITIVE}
        or route.task_type == TaskType.OUT_OF_SCOPE
    ):
        active_task = None
        pending_task = None
        pending_reason = None
        pending_source = None
        pending_turns = 0
    else:
        active_task = (
            route.task_type
            if route.task_type in {TaskType.RELATIONSHIP_ADVICE, TaskType.DATE_PLANNING}
            else current.active_task
        )
        pending_task = route.pending_task
        pending_reason = route.pending_task_reason
        pending_source = route.pending_task_source
        pending_turns = route.pending_task_turns_remaining

    if route.clarification_triggered:
        clarification_reason = route.clarification_reason
        clarification_attempt_count = (
            min(current.clarification_attempt_count + 1, 3)
            if clarification_reason == current.last_clarification_reason
            else 1
        )
    elif route.task_type in {TaskType.RELATIONSHIP_ADVICE, TaskType.DATE_PLANNING}:
        clarification_reason = None
        clarification_attempt_count = 0
    else:
        clarification_reason = current.last_clarification_reason
        clarification_attempt_count = current.clarification_attempt_count

    recent_risk_state = _next_risk_state(current.recent_risk_state, route)
    return current.model_copy(
        update={
            "active_task": active_task,
            "pending_task": pending_task,
            "pending_task_reason": pending_reason,
            "pending_task_source": pending_source,
            "pending_task_turns_remaining": pending_turns,
            "last_clarification_reason": clarification_reason,
            "clarification_attempt_count": clarification_attempt_count,
            "recent_risk_state": recent_risk_state,
            "updated_at": utc_now(),
        }
    )


def clarification_message(route: RouteResult, *, repeated: bool) -> str:
    if repeated:
        return (
            "我还不能可靠判断你想继续哪件事。当前版本主要支持关系咨询和约会规划，"
            "请直接说明你希望我分析关系，还是安排约会。"
        )
    options = route.clarification_options
    if options == ["分析这段关系", "安排一次约会"]:
        return "你是想让我分析这段关系，还是帮你具体安排一次约会？"
    if options == ["补充上一版约会计划", "重新开始一份约会计划"]:
        return "你是在补充上一版约会计划，还是想重新开始一份新计划？"
    return "你说的内容还缺少指代上下文。请说明你希望我分析关系，还是安排约会。"


def out_of_scope_message() -> str:
    return (
        "当前版本主要支持关系咨询和约会规划。"
        "你可以直接描述关系问题，或者告诉我约会城市、预算和偏好。"
    )


def pending_cancel_message(task: TaskType) -> str:
    label = "约会安排" if task == TaskType.DATE_PLANNING else "关系分析"
    return f"好的，后续的{label}已取消。"


def pending_follow_up_prompt(task: TaskType) -> str:
    if task == TaskType.DATE_PLANNING:
        return "如果你准备继续推进，我可以接着根据城市、预算和时间安排约会。"
    return "如果你还想继续分析这段关系，我可以接着帮你梳理下一步。"


def _next_risk_state(
    current: RecentRiskState | None,
    route: RouteResult,
) -> RecentRiskState | None:
    if route.risk_level == RiskLevel.HIGH:
        return RecentRiskState(
            level=RiskLevel.HIGH,
            reasons=route.risk_reasons[:8],
            expires_after_turns=2,
        )
    if route.recent_risk_deescalated:
        return None
    if current is None or current.expires_after_turns <= 1:
        return None
    return current.model_copy(
        update={"expires_after_turns": current.expires_after_turns - 1}
    )

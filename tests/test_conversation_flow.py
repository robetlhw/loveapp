from loveapp.application.conversation_flow import (
    advance_conversation_flow,
    is_pending_cancellation,
    is_pending_continuation,
)
from loveapp.domain.conversation import ConversationFlowState
from loveapp.domain.enums import RiskLevel, TaskType
from loveapp.domain.routing import RouteResult


def _flow() -> ConversationFlowState:
    return ConversationFlowState(
        user_id="u1",
        relationship_id="r1",
        conversation_id="c1",
        active_task=TaskType.RELATIONSHIP_ADVICE,
        pending_task=TaskType.DATE_PLANNING,
        pending_task_reason="relationship first",
        pending_task_source="secondary_task",
        pending_task_turns_remaining=2,
    )


def _route(task: TaskType, **updates) -> RouteResult:
    return RouteResult(
        normalized_query="test",
        task_type=task,
        task_confidence=0.9,
        **updates,
    )


def test_pending_continuation_and_cancellation_phrases_are_scoped() -> None:
    assert is_pending_continuation("好，继续", TaskType.DATE_PLANNING) is True
    assert is_pending_continuation("好的，继续", TaskType.DATE_PLANNING) is True
    assert is_pending_cancellation("算了，不安排了", TaskType.DATE_PLANNING) is True
    assert is_pending_continuation("继续", None) is False


def test_flow_clears_pending_after_secondary_task_executes() -> None:
    next_flow = advance_conversation_flow(
        _flow(),
        _route(TaskType.DATE_PLANNING, pending_task=None),
    )

    assert next_flow.active_task == TaskType.DATE_PLANNING
    assert next_flow.pending_task is None


def test_flow_preserves_new_secondary_task_and_clears_it_on_high_risk() -> None:
    pending = advance_conversation_flow(
        _flow().model_copy(update={"pending_task": None}),
        _route(
            TaskType.RELATIONSHIP_ADVICE,
            pending_task=TaskType.DATE_PLANNING,
            pending_task_reason="secondary",
            pending_task_source="secondary_task",
            pending_task_turns_remaining=2,
        ),
    )
    high_risk = advance_conversation_flow(
        pending,
        _route(
            TaskType.RELATIONSHIP_ADVICE,
            risk_level=RiskLevel.HIGH,
            risk_reasons=["risk"],
        ),
    )

    assert pending.pending_task == TaskType.DATE_PLANNING
    assert high_risk.pending_task is None
    assert high_risk.active_task is None
    assert high_risk.recent_risk_state is not None


def test_flow_clears_pending_for_sensitive_safety_interruption() -> None:
    sensitive = advance_conversation_flow(
        _flow(),
        _route(
            TaskType.GENERAL_CHAT,
            risk_level=RiskLevel.SENSITIVE,
            risk_reasons=["safety seeking"],
        ),
    )

    assert sensitive.pending_task is None
    assert sensitive.active_task is None

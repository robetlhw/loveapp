"""Deterministic bridges for high-impact relationship events.

The memory model is useful for open-ended extraction, but a few relationship
state transitions are too important to leave entirely behind a text-only
gate.  This module handles only conservative, high-confidence discourse
patterns and keeps the original messages as evidence.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from loveapp.domain.memory import (
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryValence,
    RelationshipImpact,
    StoredMessage,
    TemporalPrecision,
    TimeKind,
)

_ACCEPTANCE_PATTERN = re.compile(r"(?:同意|答应|愿意|接受)")
_DIRECT_SUCCESS_PATTERN = re.compile(
    r"(?:表白|告白).{0,24}(?:成功|同意|答应|愿意|接受)"
    r"|(?:同意|答应|愿意|接受).{0,16}"
    r"(?:做我女朋友|和我在一起|跟我交往|交往|我的表白|这次表白)"
)
_CONFESSION_PATTERN = re.compile(
    r"(?:准备|打算|计划|要去|去|想要|已经|刚刚).{0,20}(?:表白|告白)"
    r"|向.{0,12}(?:表达|说明).{0,8}(?:喜欢|感情)"
    r"|想和.{0,12}(?:在一起|交往)"
    r"|(?:发消息|发信息|发微信).{0,20}(?:表白|告白)"
    r"|(?:表白|告白).{0,8}(?:了|过|成功)"
)
_PLANNED_CONFESSION_PATTERN = re.compile(
    r"(?:准备|打算|计划|要去|想要).{0,20}(?:表白|告白)"
)
_QUESTION_OR_HYPOTHETICAL_PATTERN = re.compile(
    r"(?:如果|假如|要是|是否|是不是|吗[？?]?\s*$|不确定|不知道)"
)
_PENDING_CONFESSION_PREDICATES = {
    "will_confess",
    "confession_planned",
    "plan_to_confess",
}
_NON_CONFESSION_ACTION_PATTERN = re.compile(
    r"(?:发消息|发信息|发微信|聊天|邀约|邀请|约她|约对方|约会|见面)"
)


@dataclass(frozen=True)
class ContextualRelationshipEvent:
    predicate: str
    source_text: str
    antecedent: StoredMessage | None
    confidence: float
    signal: str
    supersedes_id: str | None = None


def may_contain_contextual_relationship_event(text: str) -> bool:
    """Return whether a short context lookup could change the gate decision."""

    return bool(_ACCEPTANCE_PATTERN.search(text) or _DIRECT_SUCCESS_PATTERN.search(text))


def resolve_contextual_relationship_event(
    text: str,
    conversation_history: Iterable[StoredMessage],
    existing_memories: Iterable[MemoryItem] = (),
) -> ContextualRelationshipEvent | None:
    """Resolve a narrow acceptance statement against a prior confession.

    A bare acceptance is intentionally insufficient.  It must either mention
    the confession outcome directly or follow a recent user-authored
    confession intent / pending confession memory.  Assistant messages are
    not used as factual antecedents because they may contain model inference.
    """

    if not may_contain_contextual_relationship_event(text):
        return None
    if _QUESTION_OR_HYPOTHETICAL_PATTERN.search(text):
        return None

    user_history = [
        message
        for message in conversation_history
        if message.role.value == "user" and message.content.strip()
    ]
    direct_success = bool(_DIRECT_SUCCESS_PATTERN.search(text))
    antecedent_index = next(
        (
            index
            for index in range(len(user_history) - 1, -1, -1)
            if _is_confession_antecedent(user_history[index].content)
        ),
        None,
    )
    antecedent = (
        user_history[antecedent_index]
        if antecedent_index is not None
        else None
    )
    if (
        not direct_success
        and antecedent_index is not None
        and _has_intervening_action(user_history, antecedent_index)
    ):
        # A later message such as "我去给她发消息" makes a bare
        # "她同意了" ambiguous.  Do not turn an unrelated acceptance into
        # a relationship-state transition.
        return None
    if not direct_success and antecedent is None:
        pending = _pending_confession_memory(existing_memories)
        if pending is None:
            return None
        pending_index = next(
            (
                index
                for index, message in enumerate(user_history)
                if message.id == pending.source_message_id
            ),
            None,
        )
        if pending_index is not None and _has_intervening_action(user_history, pending_index):
            return None
        signal = "pending_confession_memory"
        supersedes_id = pending.id
    else:
        signal = (
            "explicit_confession_acceptance"
            if direct_success
            else "contextual_confession_acceptance"
        )
        supersedes_id = _pending_confession_memory_id(existing_memories)

    return ContextualRelationshipEvent(
        predicate="confession_succeeded",
        source_text=text,
        antecedent=antecedent,
        confidence=0.98 if direct_success else 0.96,
        signal=signal,
        supersedes_id=supersedes_id,
    )


def build_pending_confession_candidate(
    text: str,
    *,
    reference_time: datetime,
) -> MemoryCandidate | None:
    """Create a short-lived plan for an explicit confession intention.

    This is intentionally deterministic and expires quickly.  It gives the
    contextual event resolver a pending action without adding another model
    call or treating a generic relationship wish as a permanent fact.
    """

    match = _PLANNED_CONFESSION_PATTERN.search(text)
    if match is None or not _is_confession_antecedent(text):
        return None
    evidence = text[match.start() : match.end()].strip()
    if re.search(r"(?:成功|同意|答应|愿意|接受)", text[match.start() :]):
        return None
    return MemoryCandidate(
        kind=MemoryKind.PLANNED_EVENT,
        subject="user",
        summary="用户计划近期向对方表白",
        original_text=evidence,
        evidence_spans=[evidence],
        time_kind=TimeKind.INTERVAL,
        temporal_precision=TemporalPrecision.APPROXIMATE,
        valence=MemoryValence.NEUTRAL,
        relationship_impact=RelationshipImpact.UNCLEAR,
        importance=4,
        perspective=MemoryPerspective.USER_REPORTED,
        confidence=0.96,
        expires_at=reference_time + timedelta(days=7),
        payload={
            "predicate": "will_confess",
            "object": "partner",
            "event_status": "planned",
            "temporal_expression": "近期",
            "deterministic": True,
        },
    )


def build_contextual_relationship_candidate(
    event: ContextualRelationshipEvent,
    *,
    reference_time: datetime,
) -> MemoryCandidate:
    evidence = [event.source_text]
    return MemoryCandidate(
        kind=MemoryKind.INTERACTION_EVENT,
        subject="relationship",
        summary="用户报告对方接受了表白，双方关系进入恋爱阶段",
        original_text="；".join(evidence)[:4000],
        evidence_spans=evidence,
        time_kind=TimeKind.POINT,
        occurred_at=reference_time,
        temporal_precision=TemporalPrecision.EXACT,
        valence=MemoryValence.POSITIVE,
        relationship_impact=RelationshipImpact.IMPROVING,
        importance=5,
        perspective=MemoryPerspective.USER_REPORTED,
        confidence=event.confidence,
        payload={
            "predicate": event.predicate,
            "object": "partner",
            "event_status": "completed",
            "contextual_bridge": True,
            "context_signal": event.signal,
            "event_id": (
                f"confession-accepted:{event.antecedent.id}"
                if event.antecedent is not None
                else None
            ),
            "antecedent_message_id": (
                event.antecedent.id if event.antecedent is not None else None
            ),
            "antecedent_excerpt": (
                event.antecedent.content[:2000]
                if event.antecedent is not None
                else None
            ),
        },
        supersedes_id=event.supersedes_id,
    )


def _pending_confession_memory(memories: Iterable[MemoryItem]) -> MemoryItem | None:
    candidates = [
        item
        for item in memories
        if item.kind == MemoryKind.PLANNED_EVENT
        and item.status.value in {"proposed", "confirmed"}
        and item.payload.get("predicate") in _PENDING_CONFESSION_PREDICATES
    ]
    return max(candidates, key=lambda item: (item.importance, item.updated_at), default=None)


def _pending_confession_memory_id(memories: Iterable[MemoryItem]) -> str | None:
    pending = _pending_confession_memory(memories)
    return pending.id if pending is not None else None


def _is_confession_antecedent(text: str) -> bool:
    match = _CONFESSION_PATTERN.search(text)
    if match is None:
        return False
    before = text[: match.start()]
    after = text[match.end() :]
    if re.search(r"(?:如果|假如|要是|要不要|该不该|怎么|如何|是否|不确定|不知道)", before[-24:]):
        return False
    # A question suffix after a factual confession clause is allowed.  Only
    # reject a question marker attached directly to the confession itself.
    return not re.match(r"\s*(?:吗|嘛|[？?])", after)


def _has_intervening_action(
    user_history: list[StoredMessage],
    start_index: int,
) -> bool:
    return any(
        _NON_CONFESSION_ACTION_PATTERN.search(message.content)
        and not _CONFESSION_PATTERN.search(message.content)
        for message in user_history[start_index + 1 :]
    )

import re
import unicodedata
from collections.abc import Sequence
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from loveapp.domain.memory import (
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    memory_dedupe_identity,
    utc_now,
)


class PlanStatus(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


ACTIVE_PLAN_STATUSES = frozenset({PlanStatus.PROPOSED, PlanStatus.CONFIRMED})
TERMINAL_PLAN_STATUSES = frozenset(
    {PlanStatus.COMPLETED, PlanStatus.CANCELLED, PlanStatus.EXPIRED}
)


class RelationshipPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1, max_length=120)
    user_id: str = Field(min_length=1, max_length=120)
    relationship_id: str = Field(min_length=1, max_length=120)
    activity_type: str = Field(min_length=1, max_length=240)
    participants: list[str] = Field(default_factory=list, max_length=20)
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    status: PlanStatus = PlanStatus.PROPOSED
    source_memory_id: str | None = None
    source_message_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    expires_at: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_time_window(self) -> "RelationshipPlan":
        if (
            self.scheduled_start is not None
            and self.scheduled_end is not None
            and self.scheduled_start > self.scheduled_end
        ):
            raise ValueError("scheduled_start cannot be later than scheduled_end")
        return self

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_PLAN_STATUSES


class PlanTransition(BaseModel):
    candidate_index: int = Field(ge=0)
    plan_id: str
    target_status: PlanStatus
    score: float = Field(ge=0)
    reason: str


def add_plan_identity(
    candidate: MemoryCandidate,
    *,
    identity_scope: str | None = None,
) -> MemoryCandidate:
    if candidate.kind != MemoryKind.PLANNED_EVENT:
        return candidate
    payload = dict(candidate.payload)
    if "plan_id" not in payload:
        payload["plan_id"] = (
            str(
                uuid5(
                    NAMESPACE_URL,
                    f"loveapp-plan:{identity_scope}:{memory_dedupe_identity(candidate)}",
                )
            )
            if identity_scope is not None
            else str(uuid4())
        )
        payload["plan_id_generated"] = True
    activity_type = _first_text(payload, "activity_type", "activity", "event_type", "object")
    if activity_type is not None:
        payload.setdefault("activity_type", activity_type)
    participants = _candidate_participants(candidate)
    if participants:
        payload.setdefault("participants", participants)
    payload.setdefault("event_status", "planned")
    return candidate.model_copy(update={"payload": payload})


def relationship_plan_from_memory(memory: MemoryItem) -> RelationshipPlan:
    if memory.kind != MemoryKind.PLANNED_EVENT:
        raise ValueError("only planned_event memories can create relationship plans")
    payload = dict(memory.payload)
    plan_id = _first_text(payload, "plan_id") or _legacy_plan_id(memory.id)
    activity_type = (
        _first_text(payload, "activity_type", "activity", "event_type", "object")
        or memory.summary
    )
    participants = _candidate_participants(memory)
    payload["plan_id"] = plan_id
    payload.setdefault("activity_type", activity_type)
    if participants:
        payload.setdefault("participants", participants)
    return RelationshipPlan(
        plan_id=plan_id,
        user_id=memory.user_id,
        relationship_id=memory.relationship_id,
        activity_type=activity_type,
        participants=participants,
        scheduled_start=memory.period_start or memory.occurred_at,
        scheduled_end=memory.period_end,
        status=_initial_plan_status(payload.get("event_status")),
        source_memory_id=memory.id,
        source_message_id=memory.source_message_id,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
        expires_at=memory.expires_at,
        payload=payload,
    )


def memory_with_plan(memory: MemoryItem, plan: RelationshipPlan) -> MemoryItem:
    payload = dict(memory.payload)
    payload["plan_id"] = plan.plan_id
    payload.setdefault("activity_type", plan.activity_type)
    if plan.participants:
        payload.setdefault("participants", list(plan.participants))
    payload["plan_status"] = plan.status.value
    return memory.model_copy(update={"payload": payload})


def match_plan_transitions(
    candidates: Sequence[MemoryCandidate],
    plans: Sequence[RelationshipPlan],
    *,
    infer_legacy_completion: bool = False,
) -> list[PlanTransition]:
    active_plans = [plan for plan in plans if plan.status in ACTIVE_PLAN_STATUSES]
    transitions: list[PlanTransition] = []
    claimed_plan_ids: set[str] = set()
    for index, candidate in enumerate(candidates):
        target_status = candidate_plan_status(candidate)
        if (
            target_status is None
            and infer_legacy_completion
            and _is_legacy_completed_event(candidate)
        ):
            target_status = PlanStatus.COMPLETED
        if target_status is None:
            continue
        eligible = [
            plan
            for plan in active_plans
            if plan.plan_id not in claimed_plan_ids
            and can_transition_plan_status(plan.status, target_status)
        ]
        explicit_plan_id = _first_text(
            candidate.payload,
            "completes_plan_id",
            "related_plan_id",
            "target_plan_id",
        )
        explicit = next(
            (plan for plan in eligible if plan.plan_id == explicit_plan_id),
            None,
        )
        if explicit is not None:
            transitions.append(
                PlanTransition(
                    candidate_index=index,
                    plan_id=explicit.plan_id,
                    target_status=target_status,
                    score=100,
                    reason="explicit_plan_id",
                )
            )
            claimed_plan_ids.add(explicit.plan_id)
            continue

        fallback_eligible = [
            plan
            for plan in eligible
            if target_status != PlanStatus.COMPLETED
            or _candidate_can_follow_plan(candidate, plan)
        ]
        ranked = sorted(
            (
                (_plan_match_score(candidate, plan, len(fallback_eligible)), plan)
                for plan in fallback_eligible
            ),
            key=lambda entry: entry[0],
            reverse=True,
        )
        if not ranked or ranked[0][0] < 6:
            continue
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 1.5:
            continue
        score, matched = ranked[0]
        transitions.append(
            PlanTransition(
                candidate_index=index,
                plan_id=matched.plan_id,
                target_status=target_status,
                score=score,
                reason="structured_fallback",
            )
        )
        claimed_plan_ids.add(matched.plan_id)
    return transitions


def candidate_plan_status(candidate: MemoryCandidate) -> PlanStatus | None:
    if candidate.kind == MemoryKind.PLANNED_EVENT:
        return None
    raw_status = _normalize_identifier(str(candidate.payload.get("event_status") or ""))
    if raw_status in {"confirmed", "accepted", "scheduled"}:
        return PlanStatus.CONFIRMED
    if raw_status in {"completed", "finished", "done"}:
        return PlanStatus.COMPLETED
    if raw_status == "occurred" and has_retrospective_event_semantics(
        _candidate_text(candidate)
    ):
        return PlanStatus.COMPLETED
    if raw_status in {"cancelled", "canceled", "declined"}:
        return PlanStatus.CANCELLED
    if raw_status == "expired":
        return PlanStatus.EXPIRED
    return None


def suppressed_plan_ids_for_text(
    text: str,
    plans: Sequence[RelationshipPlan],
) -> set[str]:
    if not has_retrospective_event_semantics(text):
        return set()
    active = [plan for plan in plans if plan.status in ACTIVE_PLAN_STATUSES]
    explicit = {plan.plan_id for plan in active if plan.plan_id in text}
    matched = {
        plan.plan_id
        for plan in active
        if _activity_similarity(plan.activity_type, text) >= 0.82
    }
    if explicit or matched:
        return explicit | matched
    if len(active) == 1 and _has_generic_completed_plan_reference(text):
        return {active[0].plan_id}
    return set()


def memory_references_plan(memory: MemoryCandidate, plan: RelationshipPlan) -> bool:
    linked_id = _first_text(
        memory.payload,
        "related_plan_id",
        "target_plan_id",
        "plan_id",
    )
    if linked_id == plan.plan_id:
        return True
    return _activity_similarity(plan.activity_type, _candidate_text(memory)) >= 0.82


def has_retrospective_event_semantics(text: str) -> bool:
    normalized = _normalize_text(text)
    if _RETROSPECTIVE_ANCHOR.search(normalized):
        return True
    if _EXPLICIT_COMPLETION.search(normalized):
        return True
    if _FUTURE_FRAMING.search(normalized):
        return False
    return _POST_EVENT_REFERENCE.search(normalized) is not None


def _plan_match_score(
    candidate: MemoryCandidate,
    plan: RelationshipPlan,
    eligible_count: int,
) -> float:
    text = _candidate_text(candidate)
    activity_values = [
        value
        for key in ("activity_type", "activity", "event_type", "object")
        if (value := _payload_text(candidate.payload.get(key))) is not None
    ]
    activity_values.append(text)
    activity_similarity = max(
        (_activity_similarity(plan.activity_type, value) for value in activity_values),
        default=0,
    )
    score = 0.0
    if activity_similarity >= 0.82:
        score += 7
    elif activity_similarity >= 0.55:
        score += 5

    candidate_participants = set(_candidate_participants(candidate))
    plan_participants = set(_normalize_participant(value) for value in plan.participants)
    overlap = candidate_participants & plan_participants
    if overlap:
        score += min(len(overlap), 2)
    elif candidate_participants and plan_participants:
        score -= 2

    event_time = candidate.occurred_at or candidate.period_end or candidate.period_start
    schedule_time = plan.scheduled_end or plan.scheduled_start
    if event_time is not None and schedule_time is not None:
        distance = abs(_datetime_distance(event_time, schedule_time))
        if distance <= timedelta(days=2):
            score += 2
        elif distance <= timedelta(days=30):
            score += 1
        else:
            score -= 2

    if (
        activity_similarity < 0.55
        and eligible_count == 1
        and overlap
        and _has_generic_completed_plan_reference(text)
    ):
        score += 5
    return score


def _is_legacy_completed_event(candidate: MemoryCandidate) -> bool:
    if candidate.kind not in {MemoryKind.INTERACTION_EVENT, MemoryKind.ADVICE_OUTCOME}:
        return False
    if _payload_text(candidate.payload.get("event_status")) is not None:
        return False
    return has_retrospective_event_semantics(_candidate_text(candidate))


def _candidate_can_follow_plan(
    candidate: MemoryCandidate,
    plan: RelationshipPlan,
) -> bool:
    event_time = candidate.occurred_at or candidate.period_end or candidate.period_start
    if event_time is None and isinstance(candidate, MemoryItem):
        event_time = candidate.created_at
    if event_time is None:
        return True
    if plan.scheduled_start is not None:
        return _datetime_distance(event_time, plan.scheduled_start) >= -timedelta(days=2)
    return _datetime_distance(event_time, plan.created_at) >= -timedelta(days=1)


def _candidate_participants(candidate: MemoryCandidate) -> list[str]:
    raw = candidate.payload.get("participants")
    values: list[str] = []
    if isinstance(raw, list):
        values.extend(str(value) for value in raw)
    elif isinstance(raw, str):
        values.extend(re.split(r"[,，、;/；]", raw))
    subject = _normalize_participant(candidate.subject)
    if subject == "relationship":
        values.extend(("user", "partner"))
    elif subject in {"user", "partner"}:
        values.append(subject)
    return list(
        dict.fromkeys(
            normalized
            for value in values
            if (normalized := _normalize_participant(value))
        )
    )


def _initial_plan_status(value: object) -> PlanStatus:
    normalized = _normalize_identifier(str(value or ""))
    if normalized in {"confirmed", "accepted", "scheduled"}:
        return PlanStatus.CONFIRMED
    return PlanStatus.PROPOSED


def can_transition_plan_status(current: PlanStatus, target: PlanStatus) -> bool:
    if current == target:
        return True
    if current == PlanStatus.PROPOSED:
        return target in {
            PlanStatus.CONFIRMED,
            PlanStatus.COMPLETED,
            PlanStatus.CANCELLED,
            PlanStatus.EXPIRED,
        }
    if current == PlanStatus.CONFIRMED:
        return target in TERMINAL_PLAN_STATUSES
    return False


def _candidate_text(candidate: MemoryCandidate) -> str:
    return " ".join(
        value
        for value in [candidate.summary, candidate.original_text, *candidate.evidence_spans]
        if value
    )


def _activity_similarity(left: str, right: str) -> float:
    left_compact = _compact(left)
    right_compact = _compact(right)
    if not left_compact or not right_compact:
        return 0
    if left_compact in right_compact or right_compact in left_compact:
        return 1
    left_features = _text_features(left_compact)
    right_features = _text_features(right_compact)
    if not left_features or not right_features:
        return 0
    return len(left_features & right_features) / len(left_features | right_features)


def _text_features(value: str) -> set[str]:
    features = set(re.findall(r"[a-z0-9_]{2,}", value))
    for block in re.findall(r"[\u4e00-\u9fff]+", value):
        if len(block) == 1:
            features.add(block)
        else:
            features.update(block[index : index + 2] for index in range(len(block) - 1))
    return features


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9_\u4e00-\u9fff]", "", _normalize_text(value))


def _normalize_identifier(value: str) -> str:
    return re.sub(r"[\s-]+", "_", _normalize_text(value))


def _normalize_participant(value: str) -> str:
    normalized = _compact(value)
    aliases = {
        "我": "user",
        "本人": "user",
        "用户": "user",
        "user": "user",
        "她": "partner",
        "他": "partner",
        "ta": "partner",
        "对象": "partner",
        "伴侣": "partner",
        "对方": "partner",
        "partner": "partner",
        "双方": "relationship",
        "我们": "relationship",
        "relationship": "relationship",
    }
    return aliases.get(normalized, normalized)


def _first_text(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _payload_text(payload.get(key))
        if value is not None:
            return value
    return None


def _payload_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _legacy_plan_id(memory_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"loveapp:relationship-plan:{memory_id}"))


def _datetime_distance(left: datetime, right: datetime) -> timedelta:
    if left.tzinfo is None and right.tzinfo is not None:
        left = left.replace(tzinfo=right.tzinfo)
    elif left.tzinfo is not None and right.tzinfo is None:
        right = right.replace(tzinfo=left.tzinfo)
    return left - right


_RETROSPECTIVE_ANCHOR = re.compile(
    r"(?:上次|上回|此前|之前|那天|后来|事后|过后|回来以后|回来之后|回来后)"
)
_EXPLICIT_COMPLETION = re.compile(
    r"(?:已经|刚刚|刚|终于).{0,24}(?:完成|结束|[\u4e00-\u9fff]{1,6}完(?:了)?)|"
    r"[\u4e00-\u9fff]{1,6}完了"
)
_POST_EVENT_REFERENCE = re.compile(
    r"(?:活动|安排|行程|事情)?(?:完成|结束)(?:后|以后|之后)|"
    r"[\u4e00-\u9fff]{1,8}完(?:后|以后|之后)|旅游回来(?:后|以后|之后)"
)
_FUTURE_FRAMING = re.compile(
    r"(?:准备|打算|计划|预计|将要|到时候|等到|明天|后天|下周|下个月).{0,30}"
)
_GENERIC_COMPLETED_PLAN_REFERENCE = re.compile(
    r"(?:活动|安排|行程|事情).{0,6}(?:完成|结束)|"
    r"(?:回来|结束|完成)(?:后|以后|之后)"
)


def _has_generic_completed_plan_reference(text: str) -> bool:
    return _GENERIC_COMPLETED_PLAN_REFERENCE.search(_normalize_text(text)) is not None

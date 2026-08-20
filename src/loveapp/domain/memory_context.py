import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from loveapp.domain.advice import RelationshipContext
from loveapp.domain.memory import (
    MemoryContextItem,
    MemoryItem,
    MemoryStatus,
    utc_now,
)
from loveapp.domain.memory_lifecycle import (
    MemoryRole,
    memory_role,
    semantic_context_key,
)
from loveapp.domain.relationship_evidence import (
    RelationshipEvidenceProfile,
    project_relationship_evidence,
)
from loveapp.domain.relationship_plan import RelationshipPlan

_ROLE_QUOTAS: dict[MemoryRole, int] = {
    MemoryRole.CURRENT_STATE: 3,
    MemoryRole.PREFERENCE: 6,
    MemoryRole.ACTION_INTENT: 4,
    MemoryRole.PLANNED_EVENT: 4,
    MemoryRole.STABLE_PROFILE: 5,
    MemoryRole.INTERACTION_PATTERN: 3,
    MemoryRole.RECENT_EVENT: 4,
}

_ROLE_ORDER: tuple[MemoryRole, ...] = (
    MemoryRole.CURRENT_STATE,
    MemoryRole.PREFERENCE,
    MemoryRole.ACTION_INTENT,
    MemoryRole.PLANNED_EVENT,
    MemoryRole.STABLE_PROFILE,
    MemoryRole.INTERACTION_PATTERN,
    MemoryRole.RECENT_EVENT,
)

_CONSTRAINT_PAYLOAD_KEYS = frozenset(
    {
        "allergen",
        "allergy_type",
        "boundary",
        "constraint",
        "constraint_type",
        "dietary_restriction",
        "restriction",
        "restriction_type",
    }
)
_CONSTRAINT_PREFERENCE_TYPES = frozenset({"allergy", "avoid", "restriction"})
_OPEN_ATTENTION_STATES = frozenset(
    {"active", "critical", "open", "pending", "unresolved"}
)


def select_context_memories(
    memories: list[MemoryItem],
    *,
    query: str | None = None,
    limit: int = 20,
    reference_time: datetime | None = None,
) -> list[MemoryItem]:
    now = reference_time or utc_now()
    active = [
        item
        for item in memories
        if item.status in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
        and (item.expires_at is None or item.expires_at > now)
    ]
    deduplicated = _deduplicate_for_context(active)
    by_role: dict[MemoryRole, list[MemoryItem]] = defaultdict(list)
    for item in deduplicated:
        by_role[memory_role(item)].append(item)

    attention_candidates = [
        item for item in deduplicated if memory_attention_reason(item, now) is not None
    ]
    selected = sorted(
        attention_candidates,
        key=lambda item: _attention_rank(item, query, now),
        reverse=True,
    )[:limit]
    selected_ids = {item.id for item in selected}
    for role in _ROLE_ORDER:
        candidates = sorted(
            [item for item in by_role.get(role, []) if item.id not in selected_ids],
            key=lambda item: _context_rank(item, query),
            reverse=True,
        )
        remaining = max(limit - len(selected), 0)
        if remaining == 0:
            break
        already_selected = sum(1 for item in selected if memory_role(item) == role)
        role_remaining = max(_ROLE_QUOTAS[role] - already_selected, 0)
        additions = candidates[: min(role_remaining, remaining)]
        selected.extend(additions)
        selected_ids.update(item.id for item in additions)
    return selected


def attach_memories(
    context: RelationshipContext,
    memories: list[MemoryItem],
    *,
    active_plans: Sequence[RelationshipPlan] | None = None,
    relationship_evidence: RelationshipEvidenceProfile | None = None,
    reference_time: datetime | None = None,
    historical_ids: set[str] | None = None,
) -> RelationshipContext:
    historical_ids = historical_ids or set()
    result = context.model_copy(deep=True)
    projection_memories = [item for item in memories if item.id not in historical_ids]
    result.relationship_evidence = relationship_evidence or project_relationship_evidence(
        projection_memories
    )
    if active_plans is not None:
        active_plan_memory_ids = {
            plan.source_memory_id
            for plan in active_plans
            if plan.source_memory_id is not None
        }
        memories = [
            item
            for item in memories
            if memory_role(item) != MemoryRole.PLANNED_EVENT
            or item.id in active_plan_memory_ids
        ]
    result.user_preferences = []
    result.partner_preferences = []
    result.important_context = []
    result.active_plans = (
        [plan.model_copy(deep=True) for plan in active_plans]
        if active_plans is not None
        else []
    )
    result.active_context = []
    result.current_state = []
    result.confirmed_current_state = []
    result.confirmed_long_term = []
    result.uncertain_items = []
    result.conflicted_items = []
    result.planned_events = []
    result.action_intents = []
    result.recent_events = []
    result.remembered_items = [MemoryContextItem.from_item(item) for item in memories]

    for item in memories:
        context_item = MemoryContextItem.from_item(item)
        is_historical = item.id in historical_ids
        if is_historical:
            # Explicit history is visible in remembered_items, but it must not
            # be projected into any current-state or confirmed-fact field.
            continue
        if item.status == MemoryStatus.PROPOSED:
            result.uncertain_items.append(context_item)
        if item.claim_relation == "contradiction":
            result.conflicted_items.append(context_item)
        attention_reason = memory_attention_reason(item, reference_time)
        if attention_reason is not None:
            result.active_context.append(
                context_item.model_copy(
                    update={"attention_reason": attention_reason}
                )
            )
        role = memory_role(item)
        if role == MemoryRole.PREFERENCE:
            values = _preference_values(item)
            if item.status == MemoryStatus.CONFIRMED:
                if item.subject.casefold() in {"partner", "对方", "伴侣", "她", "他"}:
                    result.partner_preferences.extend(values)
                else:
                    result.user_preferences.extend(values)
                result.confirmed_long_term.append(context_item)
            continue
        if role == MemoryRole.CURRENT_STATE:
            result.current_state.append(context_item)
            if item.status == MemoryStatus.CONFIRMED:
                result.confirmed_current_state.append(context_item)
        elif role == MemoryRole.PLANNED_EVENT:
            result.planned_events.append(context_item)
            continue
        elif role == MemoryRole.ACTION_INTENT:
            result.action_intents.append(context_item)
            continue
        elif role == MemoryRole.RECENT_EVENT:
            result.recent_events.append(context_item)
        if item.status == MemoryStatus.CONFIRMED and role in {
            MemoryRole.STABLE_PROFILE,
            MemoryRole.PREFERENCE,
        }:
            result.confirmed_long_term.append(context_item)
        result.important_context.append(item.summary)

    result.user_preferences = list(dict.fromkeys(result.user_preferences))
    result.partner_preferences = list(dict.fromkeys(result.partner_preferences))
    result.important_context = list(dict.fromkeys(result.important_context))
    return result


def memory_attention_reason(
    item: MemoryItem,
    reference_time: datetime | None = None,
) -> str | None:
    """Classify memories that must not compete only on lexical relevance."""

    now = reference_time or utc_now()
    payload = item.payload
    explicit_status = str(payload.get("attention_status") or "").casefold()
    if explicit_status in _OPEN_ATTENTION_STATES:
        return "unresolved" if explicit_status in {"open", "unresolved"} else explicit_status
    if payload.get("uncertainty_type") is not None or str(
        payload.get("state_value") or ""
    ).casefold() == "unknown":
        return "unresolved"
    if _has_constraint_payload(payload):
        return "constraint"

    role = memory_role(item)
    if role == MemoryRole.CURRENT_STATE:
        return "current_state"
    if role == MemoryRole.PLANNED_EVENT:
        return "planned_event"
    if role == MemoryRole.ACTION_INTENT:
        return "action_intent"
    if role in {MemoryRole.STABLE_PROFILE, MemoryRole.PREFERENCE} and item.importance >= 4:
        return "high_importance"
    if role == MemoryRole.RECENT_EVENT and item.importance >= 4:
        timestamp = item.occurred_at or item.period_end or item.updated_at
        if timestamp.tzinfo is None and now.tzinfo is not None:
            timestamp = timestamp.replace(tzinfo=now.tzinfo)
        elif timestamp.tzinfo is not None and now.tzinfo is None:
            now = now.replace(tzinfo=timestamp.tzinfo)
        if (now - timestamp).days <= 30:
            return "recent_high_importance"
    return None


def _has_constraint_payload(payload: dict[str, object]) -> bool:
    constraint_values = (
        value
        for key, value in payload.items()
        if str(key).casefold() in _CONSTRAINT_PAYLOAD_KEYS
    )
    if any(
        value is not None and value != "" and value != () and value != []
        for value in constraint_values
    ):
        return True
    preference_type = str(payload.get("preference_type") or "").casefold()
    return preference_type in _CONSTRAINT_PREFERENCE_TYPES


def _attention_rank(
    item: MemoryItem,
    query: str | None,
    reference_time: datetime,
) -> tuple[int, int, int, int, float]:
    reason = memory_attention_reason(item, reference_time)
    priority = {
        "critical": 6,
        "constraint": 5,
        "unresolved": 5,
        "current_state": 4,
        "planned_event": 3,
        "action_intent": 3,
        "high_importance": 2,
        "recent_high_importance": 1,
        "active": 1,
        "pending": 1,
    }.get(reason or "", 0)
    confirmed, relevance, importance, timestamp = _context_rank(item, query)
    return priority, confirmed, relevance, importance, timestamp


def _deduplicate_for_context(memories: list[MemoryItem]) -> list[MemoryItem]:
    grouped: dict[tuple[str, str, str], list[MemoryItem]] = defaultdict(list)
    ungrouped: list[MemoryItem] = []
    for item in memories:
        key = semantic_context_key(item)
        if key is None:
            ungrouped.append(item)
        else:
            grouped[key].append(item)
    keepers: list[MemoryItem] = []
    for key, items in grouped.items():
        if key[2].startswith("state:"):
            confirmed = [item for item in items if item.status == MemoryStatus.CONFIRMED]
            if confirmed:
                keeper = max(confirmed, key=_keeper_rank)
                keepers.append(keeper)
                keepers.extend(
                    item
                    for item in sorted(items, key=_keeper_rank, reverse=True)
                    if item.status == MemoryStatus.PROPOSED
                    and item.state_value != keeper.state_value
                    and (item.lifecycle_review_required or item.claim_relation == "contradiction")
                )
                continue
        keepers.append(max(items, key=_keeper_rank))
    return [*ungrouped, *keepers]


def _context_rank(item: MemoryItem, query: str | None) -> tuple[int, int, int, float]:
    relevance = _text_relevance(query, f"{item.summary} {item.original_text}")
    timestamp = item.occurred_at or item.period_end or item.updated_at
    return (
        int(item.status == MemoryStatus.CONFIRMED),
        relevance,
        item.importance,
        timestamp.timestamp(),
    )


def _text_relevance(query: str | None, text: str) -> int:
    if not query:
        return 0
    query_features = _text_features(query)
    if not query_features:
        return 0
    return len(query_features & _text_features(text))


def _text_features(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    features = set(re.findall(r"[a-z0-9_]{2,}", normalized))
    for block in re.findall(r"[\u4e00-\u9fff]+", normalized):
        features.update(block[index : index + 2] for index in range(len(block) - 1))
    return features


def _preference_values(item: MemoryItem) -> list[str]:
    value = item.payload.get("preference")
    if isinstance(value, list):
        values = [str(entry).strip() for entry in value if str(entry).strip()]
        return values or [item.summary]
    if value is None:
        return [item.summary]
    cleaned = str(value).strip()
    return [cleaned] if cleaned else [item.summary]


def _keeper_rank(item: MemoryItem) -> tuple[int, int, float, datetime]:
    return (
        int(item.status == MemoryStatus.CONFIRMED),
        item.importance,
        item.confidence,
        item.updated_at,
    )

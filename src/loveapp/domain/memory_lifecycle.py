import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from loveapp.domain.memory import (
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    TimeKind,
    normalize_candidate_predicate,
)
from loveapp.domain.memory_dimensions import (
    normalize_state_dimension,
    normalize_state_value,
    relationship_state_ttl,
)
from loveapp.domain.relationship_evidence import normalize_evidence_declarations


class MemoryRole(StrEnum):
    PREFERENCE = "preference"
    CURRENT_STATE = "current_state"
    ACTION_INTENT = "action_intent"
    PLANNED_EVENT = "planned_event"
    STABLE_PROFILE = "stable_profile"
    INTERACTION_PATTERN = "interaction_pattern"
    RECENT_EVENT = "recent_event"


@dataclass(frozen=True)
class PredicateFamily:
    concept: str
    predicates: frozenset[str]
    role: MemoryRole | None = None
    state_scope: str | None = None
    state_value: str | None = None
    default_ttl: timedelta | None = None
    payload_pattern: tuple[str, re.Pattern[str]] | None = None


@dataclass(frozen=True)
class StateTransitionRule:
    name: str
    trigger_concepts: frozenset[str]
    closes_concepts: frozenset[str]
    target_status: MemoryStatus = MemoryStatus.SUPERSEDED


@dataclass(frozen=True)
class PlannedMemoryTransition:
    rule_name: str
    trigger_index: int
    target_ids: tuple[str, ...]
    target_status: MemoryStatus


_PREDICATE_FAMILIES: tuple[PredicateFamily, ...] = (
    PredicateFamily(
        concept="contact_unavailable",
        predicates=frozenset(
            {
                "calls_unanswered",
                "contact_unavailable",
                "ignoring_user",
                "no_response",
                "not_responding",
                "partner_ignoring_user",
                "partner_not_responding",
                "partner_unreachable",
                "stopped_responding",
                "unable_to_contact_partner",
            }
        ),
        role=MemoryRole.CURRENT_STATE,
        state_scope="contact_availability",
        state_value="unavailable",
        default_ttl=timedelta(days=3),
    ),
    PredicateFamily(
        concept="contact_restored",
        predicates=frozenset(
            {
                "contact_restored",
                "partner_replied",
                "partner_responded",
                "partner_resumed_contact",
                "received_reply",
                "resumed_contact",
            }
        ),
    ),
    PredicateFamily(
        concept="repair_started",
        predicates=frozenset(
            {
                "apologized_to_user",
                "mutual_apology",
                "partner_apologized",
                "partner_said_sorry",
            }
        ),
    ),
    PredicateFamily(
        concept="relationship_repaired",
        predicates=frozenset(
            {
                "conflict_resolved",
                "made_up",
                "reconciled",
                "relationship_reconciled",
                "relationship_repaired",
                "resolved_conflict",
            }
        ),
    ),
    PredicateFamily(
        concept="active_conflict",
        predicates=frozenset(
            {
                "cold_war_active",
                "conflict_active",
                "in_conflict",
                "unresolved_conflict",
            }
        ),
        role=MemoryRole.CURRENT_STATE,
        state_scope="conflict_status",
        state_value="active",
        default_ttl=timedelta(days=14),
    ),
    PredicateFamily(
        concept="confession_intent",
        predicates=frozenset(
            {
                "intend_to_confess",
                "plans_to_confess",
                "will_confess",
            }
        ),
    ),
    PredicateFamily(
        concept="relationship_started",
        predicates=frozenset(
            {
                "confession_accepted",
                "confession_succeeded",
                "relationship_confirmed",
                "relationship_started",
            }
        ),
    ),
    PredicateFamily(
        concept="consumption_values_conflict",
        predicates=frozenset(
            {
                "conflict_over_consumption_values",
                "consumption_values_conflict",
                "had_conflict_over_consumption_views",
            }
        ),
    ),
    PredicateFamily(
        concept="consumption_values_conflict",
        predicates=frozenset({"has_conflict_cause"}),
        payload_pattern=("conflict_topic", re.compile(r"消费|金钱|花钱|支出")),
    ),
)


STATE_TRANSITION_RULES: tuple[StateTransitionRule, ...] = (
    StateTransitionRule(
        name="restore_contact",
        trigger_concepts=frozenset(
            {"contact_restored", "repair_started", "relationship_repaired"}
        ),
        closes_concepts=frozenset({"contact_unavailable"}),
    ),
    StateTransitionRule(
        name="resolve_active_conflict",
        trigger_concepts=frozenset({"relationship_repaired"}),
        closes_concepts=frozenset({"active_conflict"}),
    ),
    StateTransitionRule(
        name="complete_confession_intent",
        trigger_concepts=frozenset({"relationship_started"}),
        closes_concepts=frozenset({"confession_intent"}),
    ),
)

_COLLAPSIBLE_EVENT_CONCEPTS = frozenset({"consumption_values_conflict"})


_FAMILIES_BY_PREDICATE: dict[str, tuple[PredicateFamily, ...]] = defaultdict(tuple)
for _family in _PREDICATE_FAMILIES:
    for _predicate in _family.predicates:
        _FAMILIES_BY_PREDICATE[_predicate] = (
            *_FAMILIES_BY_PREDICATE[_predicate],
            _family,
        )


_KIND_ROLES: dict[MemoryKind, MemoryRole] = {
    MemoryKind.PREFERENCE: MemoryRole.PREFERENCE,
    MemoryKind.ACTION_INTENT: MemoryRole.ACTION_INTENT,
    MemoryKind.PLANNED_EVENT: MemoryRole.PLANNED_EVENT,
    MemoryKind.INTERACTION_PATTERN: MemoryRole.INTERACTION_PATTERN,
    MemoryKind.INTERACTION_EVENT: MemoryRole.RECENT_EVENT,
    MemoryKind.ADVICE_OUTCOME: MemoryRole.RECENT_EVENT,
    MemoryKind.STABLE_FACT: MemoryRole.STABLE_PROFILE,
    MemoryKind.RELATIONSHIP_STATE: MemoryRole.CURRENT_STATE,
}


def memory_predicate(memory: MemoryCandidate) -> str:
    value = memory.payload.get("predicate")
    if not isinstance(value, str):
        return ""
    return _normalize_identifier(value)


def memory_concept(memory: MemoryCandidate) -> str:
    normalized = normalize_candidate_predicate(memory)
    canonical = normalized.canonical_predicate
    state_value = normalized.state_value
    if canonical == "contact.status" and state_value:
        return f"contact_{state_value}"
    if canonical == "relationship.repair_status" and state_value:
        return {
            "in_progress": "repair_started",
            "completed": "relationship_repaired",
        }.get(state_value, f"repair_{state_value}")
    if canonical == "relationship.conflict_status" and state_value == "active":
        return "active_conflict"
    if canonical == "confession.status" and state_value:
        return {
            "intended": "confession_intent",
            "accepted": "relationship_started",
        }.get(state_value, f"confession_{state_value}")
    if canonical == "relationship.stage" and state_value in {"dating", "committed"}:
        return "relationship_started"
    state_identity = relationship_state_identity(memory)
    relationship_value = relationship_state_value(memory)
    if state_identity is not None and relationship_value is not None:
        return f"state:{state_identity[1]}:{relationship_value}"
    if _has_preference_payload(memory):
        preference = memory.payload.get("preference")
        preference_type = memory.payload.get("preference_type") or "unknown"
        return (
            f"preference:{_normalize_identifier(str(preference_type))}:"
            f"{_normalize_identifier(str(preference))}"
        )
    predicate = memory_predicate(memory)
    for family in _FAMILIES_BY_PREDICATE.get(predicate, ()):
        if _family_matches(family, memory):
            return family.concept
    return predicate


def memory_role(memory: MemoryCandidate) -> MemoryRole:
    explicit = memory.payload.get("memory_role")
    if isinstance(explicit, str):
        try:
            return MemoryRole(explicit)
        except ValueError:
            pass
    family = _matching_family(memory)
    if family is not None and family.role is not None:
        return family.role
    if _has_preference_payload(memory):
        return MemoryRole.PREFERENCE
    role = _KIND_ROLES[memory.kind]
    if role == MemoryRole.STABLE_PROFILE and (
        memory.time_kind == TimeKind.POINT or memory.occurred_at is not None
    ):
        return MemoryRole.RECENT_EVENT
    return role


def normalize_memory_candidate(
    candidate: MemoryCandidate,
    reference_time: datetime,
) -> MemoryCandidate:
    candidate = normalize_candidate_predicate(candidate)
    updates: dict = {}
    payload = dict(candidate.payload)
    if "relationship_evidence" in payload:
        declarations = normalize_evidence_declarations(
            payload.get("relationship_evidence"),
            claim_confidence=candidate.confidence,
        )
        if declarations:
            payload["relationship_evidence"] = declarations
        else:
            payload.pop("relationship_evidence", None)
    if candidate.kind == MemoryKind.STABLE_FACT and _has_preference_payload(candidate):
        updates["kind"] = MemoryKind.PREFERENCE

    uncertainty_dimension = normalize_state_dimension(payload.get("uncertainty_type"))
    declared_state_dimension = normalize_state_dimension(payload.get("state_dimension"))
    state_dimension = uncertainty_dimension or declared_state_dimension
    raw_state_value = payload.get("state_value")
    if uncertainty_dimension is not None and raw_state_value is None:
        raw_state_value = "unknown"
    state_value = normalize_state_value(state_dimension, raw_state_value)
    if (
        candidate.kind == MemoryKind.STABLE_FACT
        and state_dimension is not None
        and state_value is not None
    ):
        updates["kind"] = MemoryKind.RELATIONSHIP_STATE
        updates["subject"] = "relationship"
        payload["predicate"] = "has_state"
        payload["state_dimension"] = state_dimension
        payload["state_value"] = state_value
        payload.setdefault("state_scope", state_dimension)
        payload.setdefault("memory_role", MemoryRole.CURRENT_STATE.value)
        if state_value == "unknown":
            payload.setdefault("attention_status", "unresolved")

    family = _matching_family(candidate)
    if family is not None:
        payload.setdefault("canonical_concept", family.concept)
        if family.role is not None:
            payload.setdefault("memory_role", family.role.value)
        if family.state_scope is not None:
            payload.setdefault("state_scope", family.state_scope)
        if family.state_value is not None:
            payload.setdefault("state_value", family.state_value)
        if candidate.expires_at is None and family.default_ttl is not None:
            updates["expires_at"] = reference_time + family.default_ttl

    effective_kind = updates.get("kind", candidate.kind)
    if effective_kind == MemoryKind.RELATIONSHIP_STATE:
        dimension = state_dimension
        value = state_value
        if dimension is not None and value is not None:
            payload["state_dimension"] = dimension
            payload["state_value"] = value
            payload.setdefault("memory_role", MemoryRole.CURRENT_STATE.value)
            payload.setdefault("state_scope", dimension)
            ttl = relationship_state_ttl(dimension)
            if candidate.expires_at is None and ttl is not None:
                updates["expires_at"] = reference_time + ttl
    elif effective_kind == MemoryKind.ACTION_INTENT:
        payload.setdefault("event_status", "intended")
        payload.setdefault("memory_role", MemoryRole.ACTION_INTENT.value)
        if candidate.expires_at is None:
            updates["expires_at"] = reference_time + timedelta(days=14)
    elif effective_kind == MemoryKind.PLANNED_EVENT and candidate.expires_at is None:
        anchor = candidate.period_end or candidate.period_start or candidate.occurred_at
        if anchor is not None:
            if anchor.tzinfo is None and reference_time.tzinfo is not None:
                anchor = anchor.replace(tzinfo=reference_time.tzinfo)
            expires_at = anchor + timedelta(days=1)
            if expires_at > reference_time:
                updates["expires_at"] = expires_at

    if payload != candidate.payload:
        updates["payload"] = payload
    normalized_candidate = candidate.model_copy(update=updates) if updates else candidate
    return normalize_candidate_predicate(normalized_candidate)


def plan_memory_transitions(
    triggers: Sequence[MemoryCandidate],
    active_memories: Sequence[MemoryItem],
    *,
    legacy_ordering: bool = False,
    trigger_statuses: Sequence[MemoryStatus] | None = None,
) -> list[PlannedMemoryTransition]:
    plans: list[PlannedMemoryTransition] = []
    claimed_targets: set[str] = set()
    for index, trigger in enumerate(triggers):
        trigger_concept = memory_concept(trigger)
        for rule in STATE_TRANSITION_RULES:
            if trigger_concept not in rule.trigger_concepts:
                continue
            targets = tuple(
                item.id
                for item in active_memories
                if item.id not in claimed_targets
                and memory_concept(item) in rule.closes_concepts
                and _trigger_can_close_target(
                    trigger,
                    item,
                    trigger_status=(
                        trigger_statuses[index]
                        if trigger_statuses is not None and index < len(trigger_statuses)
                        else None
                    ),
                )
                and (not legacy_ordering or _is_strictly_older(item, trigger))
            )
            if not targets:
                continue
            claimed_targets.update(targets)
            plans.append(
                PlannedMemoryTransition(
                    rule_name=rule.name,
                    trigger_index=index,
                    target_ids=targets,
                    target_status=rule.target_status,
                )
            )
    for index, trigger in enumerate(triggers):
        identity = relationship_state_identity(trigger)
        value = relationship_state_value(trigger)
        if identity is None or value is None:
            continue
        targets = tuple(
            item.id
            for item in active_memories
            if item.id not in claimed_targets
            and relationship_state_identity(item) == identity
            and relationship_state_value(item) not in {None, value}
            and _trigger_can_close_target(
                trigger,
                item,
                trigger_status=(
                    trigger_statuses[index]
                    if trigger_statuses is not None and index < len(trigger_statuses)
                    else None
                ),
            )
            and (not legacy_ordering or _is_strictly_older(item, trigger))
        )
        if not targets:
            continue
        claimed_targets.update(targets)
        plans.append(
            PlannedMemoryTransition(
                rule_name=f"replace_state:{identity[1]}",
                trigger_index=index,
                target_ids=targets,
                target_status=MemoryStatus.SUPERSEDED,
            )
        )
    return plans


def legacy_transition_target_ids(active_memories: Sequence[MemoryItem]) -> set[str]:
    return {
        memory_id
        for plan in plan_memory_transitions(
            active_memories,
            active_memories,
            legacy_ordering=True,
        )
        for memory_id in plan.target_ids
    }


def semantic_duplicate_ids(active_memories: Sequence[MemoryItem]) -> set[str]:
    groups: dict[tuple[str, str, str], list[MemoryItem]] = defaultdict(list)
    for item in active_memories:
        role = memory_role(item)
        concept = memory_concept(item)
        if not concept or (
            role == MemoryRole.RECENT_EVENT
            and concept not in _COLLAPSIBLE_EVENT_CONCEPTS
        ) or role not in {
            MemoryRole.CURRENT_STATE,
            MemoryRole.PREFERENCE,
            MemoryRole.STABLE_PROFILE,
            MemoryRole.RECENT_EVENT,
        }:
            continue
        groups[(role.value, item.subject.casefold(), concept)].append(item)

    redundant: set[str] = set()
    for items in groups.values():
        if len(items) < 2:
            continue
        keeper = max(items, key=_memory_keeper_rank)
        redundant.update(item.id for item in items if item.id != keeper.id)
    return redundant


def semantic_context_key(memory: MemoryItem) -> tuple[str, str, str] | None:
    role = memory_role(memory)
    state_identity = relationship_state_identity(memory)
    if state_identity is not None:
        return role.value, memory.subject.casefold(), f"state:{state_identity[1]}"
    concept = memory_concept(memory)
    if not concept or (
        role == MemoryRole.RECENT_EVENT
        and concept not in _COLLAPSIBLE_EVENT_CONCEPTS
    ):
        return None
    return role.value, memory.subject.casefold(), concept


def relationship_state_identity(memory: MemoryCandidate) -> tuple[str, str] | None:
    if memory.kind != MemoryKind.RELATIONSHIP_STATE:
        return None
    if memory.state_dimension:
        return "relationship", memory.state_dimension
    dimension = normalize_state_dimension(memory.payload.get("state_dimension"))
    if dimension is None:
        dimension = normalize_state_dimension(memory.payload.get("uncertainty_type"))
    if dimension is None:
        return None
    return "relationship", dimension


def relationship_state_value(memory: MemoryCandidate) -> str | None:
    if memory.state_dimension and memory.state_value:
        return memory.state_value
    identity = relationship_state_identity(memory)
    if identity is None:
        return None
    raw_value = memory.payload.get("state_value")
    if raw_value is None and memory.payload.get("uncertainty_type") is not None:
        raw_value = "unknown"
    return normalize_state_value(identity[1], raw_value)


def _matching_family(memory: MemoryCandidate) -> PredicateFamily | None:
    predicate = memory_predicate(memory)
    return next(
        (
            family
            for family in _FAMILIES_BY_PREDICATE.get(predicate, ())
            if _family_matches(family, memory)
        ),
        None,
    )


def _family_matches(family: PredicateFamily, memory: MemoryCandidate) -> bool:
    if family.payload_pattern is None:
        return True
    key, pattern = family.payload_pattern
    value = memory.payload.get(key)
    return isinstance(value, str) and pattern.search(value) is not None


def _has_preference_payload(memory: MemoryCandidate) -> bool:
    value = memory.payload.get("preference")
    return isinstance(value, (str, list)) and bool(value)


def _is_strictly_older(target: MemoryItem, trigger: MemoryCandidate) -> bool:
    if not isinstance(trigger, MemoryItem):
        return True
    target_time = target.occurred_at or target.period_end or target.updated_at
    trigger_time = trigger.occurred_at or trigger.period_end or trigger.updated_at
    return target.id != trigger.id and target_time < trigger_time


def _trigger_can_close_target(
    trigger: MemoryCandidate,
    target: MemoryItem,
    *,
    trigger_status: MemoryStatus | None,
) -> bool:
    status = trigger_status
    if status is None and isinstance(trigger, MemoryItem):
        status = trigger.status
    if status == MemoryStatus.PROPOSED and target.status == MemoryStatus.CONFIRMED:
        return False
    return status != MemoryStatus.REJECTED


def _memory_keeper_rank(item: MemoryItem) -> tuple[int, int, float, datetime]:
    return (
        int(item.status == MemoryStatus.CONFIRMED),
        item.importance,
        item.confidence,
        item.updated_at,
    )


def _normalize_identifier(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"[\s-]+", "_", normalized)

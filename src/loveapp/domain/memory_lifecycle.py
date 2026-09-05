import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from loveapp.domain.memory import (
    EvidenceExplicitness,
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    PredicateType,
    TimeKind,
    normalize_candidate_predicate,
)
from loveapp.domain.memory_dimensions import (
    INTERACTION_PATTERN_DIMENSIONS,
    is_relationship_interaction_subject,
    normalize_interaction_pattern_payload,
    normalize_interaction_state_value,
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


_EXPLICIT_CONTACT_RESTORATION_PATTERN = re.compile(
    r"(?:回(?:复)?(?:了|过)?我(?:的)?(?:消息)?|重新(?:联系|聊天)|"
    r"恢复(?:正常)?(?:联系|聊天)|又开始正常聊天)"
)

_CONTACT_OUTAGE_STATE_PATTERN = re.compile(
    r"^(?:none|no_(?:response|reply|contact)(?:_.+)?|"
    r"(?:very_)?low_(?:response|reply|contact)(?:_.+)?|"
    r"(?:unresponsive|unreachable|unavailable)(?:_.+)?)$"
)
_EXPLICIT_CONTACT_OUTAGE_PATTERN = re.compile(
    r"(?:联系不上|无法联系|失去联系|未(?:回(?:复)?|回应)|"
    r"没(?:有)?回(?:复)?.{0,6}(?:消息|我)|不(?:回(?:复)?|回应)|"
    r"无人接听|无法接通)"
)

_OPEN_WORLD_SOCIAL_INTEGRATION_PATTERN = re.compile(
    r"(?:带|让|邀请|介绍|参加|融入|接纳).{0,18}"
    r"(?:朋友|朋友圈|社交圈|聚会|父母|家人|家庭)|"
    r"(?:朋友|朋友圈|社交圈|聚会|父母|家人|家庭).{0,18}"
    r"(?:带|让|邀请|介绍|参加|融入|接纳|见)"
)
_EXPLICIT_FAMILIARITY_PATTERN = re.compile(
    r"(?:不.{0,2}熟|不熟悉|很熟|比较熟|熟悉|熟络|生疏|陌生|"
    r"刚认识|认识不久|了解不多)"
)
_FAMILY_INTEGRATION_PATTERN = re.compile(r"父母|家人|家庭|亲属|亲戚")
_SOCIAL_INTEGRATION_RESTRICTION_PATTERN = re.compile(
    r"(?:不再|不愿意|不愿|不肯|拒绝|不让|不带|不邀请|不介绍|"
    r"限制|阻止|排斥|很少再|几乎不|从不)"
)
_SOCIAL_INTEGRATION_INTRODUCTION_PATTERN = re.compile(r"(?:介绍|认识)")
_SOCIAL_INTEGRATION_PARTICIPATION_PATTERN = re.compile(
    r"(?:邀请|参加|聚会|活动)"
)
_NONASSERTIVE_SOCIAL_INTEGRATION_PATTERN = re.compile(
    r"(?:可能|也许|或许|大概|说不定|似乎|好像|未必|不确定|"
    r"如果|假如|要是|是否|会不会|愿不愿意|吗(?:[？?]|$)|[？?])"
)


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
        trigger_concepts=frozenset({"contact_restored"}),
        closes_concepts=frozenset(
            {"contact_unavailable", "response_unresponsive"}
        ),
    ),
    StateTransitionRule(
        name="restore_contact_frequency",
        trigger_concepts=frozenset({"contact_restored"}),
        closes_concepts=frozenset({"contact_reduced"}),
    ),
    StateTransitionRule(
        name="restore_response_engagement",
        trigger_concepts=frozenset({"response_restored"}),
        closes_concepts=frozenset(
            {"contact_unavailable", "response_unresponsive"}
        ),
    ),
    StateTransitionRule(
        name="resolve_active_conflict",
        trigger_concepts=frozenset({"relationship_repaired"}),
        closes_concepts=frozenset({"active_conflict"}),
    ),
    StateTransitionRule(
        name="complete_confession_intent",
        trigger_concepts=frozenset(
            {"confession_executed", "relationship_started"}
        ),
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
    canonical = normalized.canonical_predicate or memory.canonical_predicate
    state_value = normalized.state_value
    # The general predicate normalizer derives interaction state from
    # ``current``/``direction``/``frequency``.  Lifecycle fixtures and some
    # legacy callers provide the equivalent value in the typed top-level or
    # payload ``state_value`` field.  Preserve that value at this boundary so
    # semantic transition rules remain representation-aware without changing
    # the shared normalization contract.
    if canonical in {
        "interaction.contact_frequency",
        "interaction.response_engagement",
    } and state_value is None:
        metric = canonical.removeprefix("interaction.")
        for raw_value in (
            memory.state_value,
            memory.payload.get("state_value"),
            memory.payload.get("current"),
            memory.payload.get("direction"),
            memory.payload.get("frequency"),
        ):
            state_value = normalize_interaction_state_value(metric, raw_value)
            if state_value is not None:
                break
    if canonical == "contact.status" and state_value:
        if state_value in {"normal", "restored"}:
            return "contact_restored"
        if state_value == "reduced":
            return "contact_reduced"
        return f"contact_{state_value}"
    if canonical == "relationship.repair_status" and state_value:
        if state_value == "in_progress" and _has_explicit_contact_restoration(
            memory
        ):
            return "contact_restored"
        return {
            "in_progress": "repair_started",
            "completed": "relationship_repaired",
        }.get(state_value, f"repair_{state_value}")
    if canonical == "relationship.conflict_status" and state_value:
        return {
            "active": "active_conflict",
            "resolved": "relationship_repaired",
        }.get(state_value, f"conflict_{state_value}")
    if canonical == "confession.status" and state_value:
        return {
            "intended": "confession_intent",
            "accepted": "relationship_started",
        }.get(state_value, f"confession_{state_value}")
    if canonical == "relationship.stage" and state_value in {"dating", "committed"}:
        return "relationship_started"
    if canonical == "interaction.contact_frequency" and state_value:
        if _CONTACT_OUTAGE_STATE_PATTERN.fullmatch(state_value):
            return "contact_unavailable"
        if state_value in {"low", "decreasing", "reduced", "no_contact"}:
            return "contact_reduced"
        if state_value in {"normal", "restored"}:
            return "contact_restored"
    if canonical == "interaction.response_engagement" and state_value:
        if _CONTACT_OUTAGE_STATE_PATTERN.fullmatch(state_value):
            return "response_unresponsive"
        if state_value in {"normal", "responsive", "restored", "engaged"}:
            return "response_restored"
    if (
        canonical == "relationship.contact_opportunity"
        and state_value == "low"
        and _has_explicit_contact_outage(memory)
    ):
        return "contact_unavailable"
    state_identity = governed_state_identity(memory)
    state_value = governed_state_value(memory)
    if state_identity is not None and state_value is not None:
        return f"state:{state_identity[1]}:{state_value}"
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
            if family.concept == "repair_started" and _has_explicit_contact_restoration(
                memory
            ):
                return "contact_restored"
            return family.concept
    return predicate


def _has_explicit_contact_restoration(memory: MemoryCandidate) -> bool:
    evidence = _claim_evidence_text(memory)
    return bool(_EXPLICIT_CONTACT_RESTORATION_PATTERN.search(evidence))


def _has_explicit_contact_outage(memory: MemoryCandidate) -> bool:
    evidence = _claim_evidence_text(memory)
    return bool(_EXPLICIT_CONTACT_OUTAGE_PATTERN.search(evidence))


def _claim_evidence_text(memory: MemoryCandidate) -> str:
    evidence = [span for span in memory.evidence_spans if span.strip()]
    return " ".join(evidence) if evidence else memory.original_text


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
    candidate = _guard_open_world_social_integration(candidate)
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
    elif effective_kind == MemoryKind.INTERACTION_PATTERN:
        normalized_interaction_payload = normalize_interaction_pattern_payload(
            payload,
            " ".join(candidate.evidence_spans) or candidate.original_text,
            candidate.raw_predicate or payload.get("predicate"),
        )
        metric = normalized_interaction_payload.get("metric")
        if normalized_interaction_payload != payload:
            payload = normalized_interaction_payload
        if metric in INTERACTION_PATTERN_DIMENSIONS and is_relationship_interaction_subject(
            candidate.subject
        ):
            updates["subject"] = "relationship"
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
    normalized_candidate = normalize_candidate_predicate(normalized_candidate)
    return _guard_open_world_social_integration(normalized_candidate)


def _guard_open_world_social_integration(
    candidate: MemoryCandidate,
) -> MemoryCandidate:
    if (
        candidate.canonical_predicate
        not in {
            "relationship.familiarity",
            "interaction.contact_frequency",
            "interaction.initiation_balance",
        }
        and candidate.predicate_type != PredicateType.CUSTOM
    ):
        return candidate
    evidence = " ".join([candidate.original_text, *candidate.evidence_spans])
    custom_predicate = open_world_social_integration_predicate(evidence)
    if custom_predicate is None:
        return candidate
    payload = dict(candidate.payload)
    for field in (
        "state_dimension",
        "state_value",
        "state_scope",
        "metric",
        "direction",
        "baseline",
        "current",
    ):
        payload.pop(field, None)
    payload["predicate"] = custom_predicate
    payload["object"] = _social_integration_object(evidence)
    updates: dict[str, object] = {
        "payload": payload,
        "raw_predicate": custom_predicate,
        "predicate_type": PredicateType.CUSTOM,
        "canonical_predicate": None,
        "custom_predicate": custom_predicate,
        "state_dimension": None,
        "state_value": None,
        "expires_at": None,
    }
    if _is_direct_social_integration_assertion(candidate, evidence):
        updates["explicitness"] = EvidenceExplicitness.EXPLICIT
        updates["requires_inference"] = False
    return candidate.model_copy(
        update=updates
    )


def _social_integration_object(text: str) -> str:
    stance = (
        "restricted"
        if _SOCIAL_INTEGRATION_RESTRICTION_PATTERN.search(text)
        else "included"
    )
    aspects = [
        name
        for name, pattern in (
            ("introduction", _SOCIAL_INTEGRATION_INTRODUCTION_PATTERN),
            ("participation", _SOCIAL_INTEGRATION_PARTICIPATION_PATTERN),
        )
        if pattern.search(text)
    ]
    if len(aspects) == 1:
        return f"{aspects[0]}_{stance}"
    return stance


def _is_direct_social_integration_assertion(
    candidate: MemoryCandidate,
    evidence: str,
) -> bool:
    return (
        candidate.explicitness != EvidenceExplicitness.SPECULATIVE
        and not _NONASSERTIVE_SOCIAL_INTEGRATION_PATTERN.search(evidence)
    )


def open_world_social_integration_predicate(text: str) -> str | None:
    if not _OPEN_WORLD_SOCIAL_INTEGRATION_PATTERN.search(text):
        return None
    if _EXPLICIT_FAMILIARITY_PATTERN.search(text):
        return None
    if _FAMILY_INTEGRATION_PATTERN.search(text):
        return "family_integration"
    return "social_circle_integration"


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
                and _semantic_transition_applies(rule, trigger, item)
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
        identity = governed_state_identity(trigger)
        value = governed_state_value(trigger)
        if identity is None or value is None:
            continue
        targets = tuple(
            item.id
            for item in active_memories
            if item.id not in claimed_targets
            and governed_state_identity(item) == identity
            and governed_state_value(item) not in {None, value}
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
    state_identity = governed_state_identity(memory)
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


_GOVERNED_INTERACTION_STATE_VALUES = {
    "interaction.contact_frequency": frozenset(
        {"low", "decreasing", "reduced", "no_contact", "normal", "restored"}
    ),
    "interaction.initiation_balance": frozenset(
        {"partner_to_user", "balanced", "user_to_partner", "mixed"}
    ),
    "interaction.emotional_disclosure": frozenset({"high", "low"}),
    "interaction.response_engagement": frozenset(
        {
            "unresponsive",
            "no_response",
            "unavailable",
            "normal",
            "responsive",
            "restored",
            "engaged",
        }
    ),
}


def governed_state_identity(memory: MemoryCandidate) -> tuple[str, str] | None:
    relationship_identity = relationship_state_identity(memory)
    if relationship_identity is not None:
        return relationship_identity
    if (
        memory.kind == MemoryKind.INTERACTION_PATTERN
        and memory.state_dimension in _GOVERNED_INTERACTION_STATE_VALUES
        and memory.state_value
        in _GOVERNED_INTERACTION_STATE_VALUES[memory.state_dimension]
    ):
        return "interaction", memory.state_dimension
    return None


def governed_state_value(memory: MemoryCandidate) -> str | None:
    identity = governed_state_identity(memory)
    if identity is None:
        return None
    if identity[0] == "relationship":
        return relationship_state_value(memory)
    return memory.state_value


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
    target_time, trigger_time = _legacy_comparison_times(target, trigger)
    return target.id != trigger.id and target_time < trigger_time


def _legacy_comparison_times(
    target: MemoryItem,
    trigger: MemoryItem,
) -> tuple[datetime, datetime]:
    """Choose one shared business-time field for legacy ordering.

    Comparing an occurrence timestamp on one row with an update timestamp on
    another can reverse the persisted sequence.  Use a field only when both
    memories provide it, then fall back to the always-present ``updated_at``.
    """

    for field in ("occurred_at", "period_end", "updated_at"):
        target_time = getattr(target, field)
        trigger_time = getattr(trigger, field)
        if target_time is not None and trigger_time is not None:
            return target_time, trigger_time
    return target.updated_at, trigger.updated_at


def _semantic_transition_applies(
    rule: StateTransitionRule,
    trigger: MemoryCandidate,
    target: MemoryItem,
) -> bool:
    """Keep specialized contact-frequency ownership representation-safe."""

    if rule.name != "restore_contact_frequency":
        return True
    if _is_interaction_contact_frequency(trigger) or _is_interaction_contact_frequency(
        target
    ):
        return True
    # A legacy stable-fact contact.status surface predates the explicit
    # relationship-state dimension and is intentionally kept on this rule.
    return not (
        _has_explicit_contact_status_dimension(trigger)
        and _has_explicit_contact_status_dimension(target)
    )


def _is_interaction_contact_frequency(memory: MemoryCandidate) -> bool:
    canonical = memory.canonical_predicate
    if canonical == "interaction.contact_frequency":
        return True
    metric = memory.payload.get("metric")
    return isinstance(metric, str) and metric.casefold() == "contact_frequency"


def _has_explicit_contact_status_dimension(memory: MemoryCandidate) -> bool:
    if memory.kind != MemoryKind.RELATIONSHIP_STATE:
        return False
    if memory.canonical_predicate != "contact.status":
        return False
    return any(
        isinstance(value, str) and value.strip()
        for value in (
            memory.state_dimension,
            memory.payload.get("state_dimension"),
        )
    )


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

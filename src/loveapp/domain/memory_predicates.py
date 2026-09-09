import re
import unicodedata
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from loveapp.domain.memory_dimensions import (
    interaction_pattern_state,
    normalize_state_dimension,
    normalize_state_value,
)


class PredicateCardinality(StrEnum):
    """How many current values a subject may retain for a predicate.

    This is governance metadata, not a persisted Memory field.  The aliases
    keep the public vocabulary readable for callers that use either the short
    or the explicit ``*_VALUE`` spelling.
    """

    SINGLE = "single"
    SINGLE_VALUE = "single"
    MULTI = "multi"
    MULTI_VALUE = "multi"
    UNKNOWN = "unknown"


class PredicateTemporalBehavior(StrEnum):
    """Temporal semantics used by relation resolution."""

    TIMELESS = "timeless"
    STATE = "state"
    CURRENT = "current"
    CURRENT_STATE = "current"
    PATTERN = "pattern"
    EVENT = "event"
    PLANNED = "planned"
    PLANNED_EVENT = "planned"
    PLAN = "plan"
    INTENT = "intent"
    UNKNOWN = "unknown"


class PredicateUpdatePolicy(StrEnum):
    """Safe default action when two values share a predicate."""

    REPLACE = "replace"
    APPEND = "append"
    TRANSITION = "transition"
    MERGE = "merge"
    NONE = "none"
    PROTECT = "protect"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CanonicalPredicateSpec:
    name: str
    state_dimension: str | None = None
    allowed_values: frozenset[str] = frozenset()
    high_risk: bool = False
    # Preference predicates use this bounded registry metadata to prevent a
    # model from attaching an object from another semantic domain to a valid
    # canonical name.  It is governance metadata, not a new memory field.
    semantic_domain: str | None = None
    # Relation-governance metadata.  These fields deliberately live in the
    # predicate registry instead of MemoryCandidate so existing persisted
    # memory rows and the Store contract remain unchanged.
    #
    # ``cardinality`` is either ``single`` (one current value) or ``multi``
    # (independently coexisting values).  ``temporal_behavior`` describes the
    # semantic surface, while ``update_policy`` tells the deterministic
    # relation resolver whether a different value may replace the old one.
    # Unknown/legacy specs default to a conservative no-op policy.
    cardinality: PredicateCardinality = PredicateCardinality.UNKNOWN
    temporal_behavior: PredicateTemporalBehavior = PredicateTemporalBehavior.UNKNOWN
    update_policy: PredicateUpdatePolicy = PredicateUpdatePolicy.UNKNOWN

    def __post_init__(self) -> None:
        # ``CanonicalPredicateSpec`` is public and is occasionally constructed
        # by integrations with plain strings.  Coerce those values once at the
        # registry boundary while keeping the object immutable afterwards.
        for field_name, enum_type in (
            ("cardinality", PredicateCardinality),
            ("temporal_behavior", PredicateTemporalBehavior),
            ("update_policy", PredicateUpdatePolicy),
        ):
            value = getattr(self, field_name)
            if not isinstance(value, enum_type):
                object.__setattr__(self, field_name, enum_type(value))


# Public vocabulary for callers that want to inspect registry metadata without
# depending on an enum or adding values to the Memory ontology.  Keeping these
# as strings also makes the frozen JSON/evaluation contracts backwards
# compatible with older callers that construct ``CanonicalPredicateSpec``
# directly.
PREDICATE_CARDINALITY_SINGLE = "single"
PREDICATE_CARDINALITY_MULTI = "multi"
PREDICATE_TEMPORAL_STATE = "state"
PREDICATE_TEMPORAL_PATTERN = "pattern"
PREDICATE_TEMPORAL_TIMELESS = "timeless"
PREDICATE_TEMPORAL_EVENT = "event"
PREDICATE_TEMPORAL_PLAN = "plan"
PREDICATE_TEMPORAL_INTENT = "intent"
PREDICATE_UPDATE_REPLACE = "replace"
PREDICATE_UPDATE_APPEND = "append"
PREDICATE_UPDATE_MERGE = "merge"
PREDICATE_UPDATE_NONE = "none"


@dataclass(frozen=True)
class PredicateAlias:
    canonical_predicate: str
    state_value: str | None = None


@dataclass(frozen=True)
class PredicateNormalization:
    raw_predicate: str
    predicate_type: str
    canonical_predicate: str | None
    custom_predicate: str | None
    state_dimension: str | None
    state_value: str | None
    alias_hit: bool = False


def _normalize_identifier(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    normalized = re.sub(r"[\s-]+", "_", normalized)
    return re.sub(r"[^\w.\u4e00-\u9fff]+", "_", normalized).strip("_")


CANONICAL_PREDICATES: dict[str, CanonicalPredicateSpec] = {
    "contact.status": CanonicalPredicateSpec(
        name="contact.status",
        state_dimension="relationship.contact_status",
        allowed_values=frozenset({"normal", "reduced", "unavailable", "restored"}),
        high_risk=True,
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_STATE,
        update_policy=PREDICATE_UPDATE_REPLACE,
    ),
    "relationship.stage": CanonicalPredicateSpec(
        name="relationship.stage",
        state_dimension="relationship.stage",
        allowed_values=frozenset(
            {
                "unknown",
                "acquaintance",
                "dating",
                "committed",
                "cooling_off",
                "separated",
                "reconciled",
            }
        ),
        high_risk=True,
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_STATE,
        update_policy=PREDICATE_UPDATE_REPLACE,
    ),
    "relationship.repair_status": CanonicalPredicateSpec(
        name="relationship.repair_status",
        state_dimension="relationship.repair_status",
        allowed_values=frozenset(
            {"not_started", "intended", "in_progress", "completed", "failed"}
        ),
        high_risk=True,
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_STATE,
        update_policy=PREDICATE_UPDATE_REPLACE,
    ),
    "confession.status": CanonicalPredicateSpec(
        name="confession.status",
        state_dimension="relationship.confession_status",
        allowed_values=frozenset(
            {"intended", "executed", "accepted", "rejected", "withdrawn"}
        ),
        high_risk=True,
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_STATE,
        update_policy=PREDICATE_UPDATE_REPLACE,
    ),
    "plan.status": CanonicalPredicateSpec(
        name="plan.status",
        state_dimension="relationship.plan_status",
        allowed_values=frozenset(
            {"proposed", "confirmed", "completed", "cancelled", "expired"}
        ),
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_PLAN,
        update_policy=PREDICATE_UPDATE_NONE,
    ),
    "relationship.familiarity": CanonicalPredicateSpec(
        name="relationship.familiarity",
        state_dimension="relationship.familiarity",
        allowed_values=frozenset({"unfamiliar", "low", "moderate", "high"}),
        high_risk=True,
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_STATE,
        update_policy=PREDICATE_UPDATE_REPLACE,
    ),
    "relationship.contact_opportunity": CanonicalPredicateSpec(
        name="relationship.contact_opportunity",
        state_dimension="relationship.contact_opportunity",
        allowed_values=frozenset({"low", "moderate", "high"}),
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_STATE,
        update_policy=PREDICATE_UPDATE_REPLACE,
    ),
    "relationship.conflict_status": CanonicalPredicateSpec(
        name="relationship.conflict_status",
        state_dimension="relationship.conflict_status",
        allowed_values=frozenset({"active", "cooling", "repairing", "resolved"}),
        high_risk=True,
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_STATE,
        update_policy=PREDICATE_UPDATE_REPLACE,
    ),
    "relationship.interaction_reciprocity": CanonicalPredicateSpec(
        name="relationship.interaction_reciprocity",
        state_dimension="relationship.interaction_reciprocity",
        allowed_values=frozenset({"low", "mixed", "high"}),
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_STATE,
        update_policy=PREDICATE_UPDATE_REPLACE,
    ),
    "partner.relationship_status": CanonicalPredicateSpec(
        name="partner.relationship_status",
        state_dimension="partner.relationship_status",
        allowed_values=frozenset({"unknown", "single", "partnered", "married"}),
        high_risk=True,
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_STATE,
        update_policy=PREDICATE_UPDATE_REPLACE,
    ),
    "relationship.romantic_interest": CanonicalPredicateSpec(
        name="relationship.romantic_interest",
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_TIMELESS,
        update_policy=PREDICATE_UPDATE_NONE,
    ),
    "interaction.contact_frequency": CanonicalPredicateSpec(
        name="interaction.contact_frequency",
        state_dimension="interaction.contact_frequency",
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_PATTERN,
        update_policy=PREDICATE_UPDATE_REPLACE,
    ),
    "interaction.topic_scope": CanonicalPredicateSpec(
        name="interaction.topic_scope",
        state_dimension="interaction.topic_scope",
        cardinality=PREDICATE_CARDINALITY_MULTI,
        temporal_behavior=PREDICATE_TEMPORAL_PATTERN,
        # Topic scopes can coexist, but the current ontology does not yet
        # authorize deterministic relation writes for this open dimension.
        update_policy=PREDICATE_UPDATE_NONE,
    ),
    "interaction.channel": CanonicalPredicateSpec(
        name="interaction.channel",
        state_dimension="interaction.channel",
        cardinality=PREDICATE_CARDINALITY_MULTI,
        temporal_behavior=PREDICATE_TEMPORAL_PATTERN,
        update_policy=PREDICATE_UPDATE_NONE,
    ),
    "interaction.initiation_balance": CanonicalPredicateSpec(
        name="interaction.initiation_balance",
        state_dimension="interaction.initiation_balance",
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_PATTERN,
        update_policy=PREDICATE_UPDATE_REPLACE,
    ),
    "interaction.response_engagement": CanonicalPredicateSpec(
        name="interaction.response_engagement",
        state_dimension="interaction.response_engagement",
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_PATTERN,
        update_policy=PREDICATE_UPDATE_REPLACE,
    ),
    "interaction.emotional_disclosure": CanonicalPredicateSpec(
        name="interaction.emotional_disclosure",
        state_dimension="interaction.emotional_disclosure",
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_PATTERN,
        update_policy=PREDICATE_UPDATE_REPLACE,
    ),
    "interaction.conflict_frequency": CanonicalPredicateSpec(
        name="interaction.conflict_frequency",
        state_dimension="interaction.conflict_frequency",
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_PATTERN,
        update_policy=PREDICATE_UPDATE_REPLACE,
    ),
    # Stable profile facts are deliberately finite and single-valued.  The
    # registry authorizes replacement of the current value while the existing
    # write batch keeps the superseded row as history.  Open-world facts do not
    # inherit this policy and remain Custom/fail-closed.
    "profile.residence": CanonicalPredicateSpec(
        name="profile.residence",
        state_dimension="profile.residence",
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_STATE,
        update_policy=PREDICATE_UPDATE_REPLACE,
    ),
    "profile.occupation": CanonicalPredicateSpec(
        name="profile.occupation",
        state_dimension="profile.occupation",
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_STATE,
        update_policy=PREDICATE_UPDATE_REPLACE,
    ),
    "profile.contact_method": CanonicalPredicateSpec(
        name="profile.contact_method",
        state_dimension="profile.contact_method",
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_STATE,
        update_policy=PREDICATE_UPDATE_REPLACE,
    ),
    "profile.birthday": CanonicalPredicateSpec(
        name="profile.birthday",
        state_dimension="profile.birthday",
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_TIMELESS,
        update_policy=PREDICATE_UPDATE_REPLACE,
    ),
    "profile.identity": CanonicalPredicateSpec(
        name="profile.identity",
        state_dimension="profile.identity",
        cardinality=PREDICATE_CARDINALITY_MULTI,
        temporal_behavior=PREDICATE_TEMPORAL_TIMELESS,
        update_policy=PREDICATE_UPDATE_APPEND,
    ),
    "preference.general": CanonicalPredicateSpec(
        name="preference.general",
        cardinality=PREDICATE_CARDINALITY_MULTI,
        temporal_behavior=PREDICATE_TEMPORAL_TIMELESS,
        update_policy=PREDICATE_UPDATE_APPEND,
    ),
    "preference.food.cuisine": CanonicalPredicateSpec(
        name="preference.food.cuisine",
        semantic_domain="food",
        cardinality=PREDICATE_CARDINALITY_MULTI,
        temporal_behavior=PREDICATE_TEMPORAL_TIMELESS,
        update_policy=PREDICATE_UPDATE_APPEND,
    ),
    "preference.food.spiciness": CanonicalPredicateSpec(
        name="preference.food.spiciness",
        semantic_domain="food",
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_TIMELESS,
        update_policy=PREDICATE_UPDATE_REPLACE,
    ),
    "preference.environment.noise": CanonicalPredicateSpec(
        name="preference.environment.noise",
        # Keep the environment preference in the same bounded domain as its
        # value-level aliases (e.g. ``安静``/``热闹``).  Without this registry
        # metadata the domain guard cannot prove that a typed ``noise`` claim
        # belongs to this predicate and may conservatively downgrade it to a
        # custom predicate.
        semantic_domain="environment",
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_TIMELESS,
        update_policy=PREDICATE_UPDATE_REPLACE,
    ),
    "preference.activity.type": CanonicalPredicateSpec(
        name="preference.activity.type",
        semantic_domain="activity",
        cardinality=PREDICATE_CARDINALITY_MULTI,
        temporal_behavior=PREDICATE_TEMPORAL_TIMELESS,
        update_policy=PREDICATE_UPDATE_APPEND,
    ),
    "preference.budget.range": CanonicalPredicateSpec(
        name="preference.budget.range",
        semantic_domain="budget",
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_TIMELESS,
        update_policy=PREDICATE_UPDATE_REPLACE,
    ),
    # These bounded category predicates provide a stable vocabulary for the
    # preference domains described by Memory V2.1.  They intentionally remain
    # open-valued and append-only: values such as a hobby, communication
    # style, or relationship trait may coexist.  Unknown categories continue
    # to fall back to ``preference.general``/Custom through the normalizer.
    "preference.personal_interest.topic": CanonicalPredicateSpec(
        name="preference.personal_interest.topic",
        semantic_domain="personal_interest",
        cardinality=PREDICATE_CARDINALITY_MULTI,
        temporal_behavior=PREDICATE_TEMPORAL_TIMELESS,
        update_policy=PREDICATE_UPDATE_APPEND,
    ),
    "preference.hobby.activity": CanonicalPredicateSpec(
        name="preference.hobby.activity",
        semantic_domain="hobby",
        cardinality=PREDICATE_CARDINALITY_MULTI,
        temporal_behavior=PREDICATE_TEMPORAL_TIMELESS,
        update_policy=PREDICATE_UPDATE_APPEND,
    ),
    "preference.interest.topic": CanonicalPredicateSpec(
        name="preference.interest.topic",
        semantic_domain="interest",
        cardinality=PREDICATE_CARDINALITY_MULTI,
        temporal_behavior=PREDICATE_TEMPORAL_TIMELESS,
        update_policy=PREDICATE_UPDATE_APPEND,
    ),
    "preference.consumption.item": CanonicalPredicateSpec(
        name="preference.consumption.item",
        semantic_domain="consumption",
        cardinality=PREDICATE_CARDINALITY_MULTI,
        temporal_behavior=PREDICATE_TEMPORAL_TIMELESS,
        update_policy=PREDICATE_UPDATE_APPEND,
    ),
    "preference.lifestyle.habit": CanonicalPredicateSpec(
        name="preference.lifestyle.habit",
        semantic_domain="lifestyle",
        cardinality=PREDICATE_CARDINALITY_MULTI,
        temporal_behavior=PREDICATE_TEMPORAL_TIMELESS,
        update_policy=PREDICATE_UPDATE_APPEND,
    ),
    "preference.relationship.partner_trait": CanonicalPredicateSpec(
        name="preference.relationship.partner_trait",
        semantic_domain="relationship",
        cardinality=PREDICATE_CARDINALITY_MULTI,
        temporal_behavior=PREDICATE_TEMPORAL_TIMELESS,
        update_policy=PREDICATE_UPDATE_APPEND,
    ),
    "preference.relationship.interaction_style": CanonicalPredicateSpec(
        name="preference.relationship.interaction_style",
        semantic_domain="relationship",
        cardinality=PREDICATE_CARDINALITY_MULTI,
        temporal_behavior=PREDICATE_TEMPORAL_TIMELESS,
        update_policy=PREDICATE_UPDATE_APPEND,
    ),
    "preference.relationship.emotional_need": CanonicalPredicateSpec(
        name="preference.relationship.emotional_need",
        semantic_domain="relationship",
        cardinality=PREDICATE_CARDINALITY_MULTI,
        temporal_behavior=PREDICATE_TEMPORAL_TIMELESS,
        update_policy=PREDICATE_UPDATE_APPEND,
    ),
    "preference.communication.style": CanonicalPredicateSpec(
        name="preference.communication.style",
        semantic_domain="communication",
        cardinality=PREDICATE_CARDINALITY_MULTI,
        temporal_behavior=PREDICATE_TEMPORAL_TIMELESS,
        update_policy=PREDICATE_UPDATE_APPEND,
    ),
    "preference.communication.frequency": CanonicalPredicateSpec(
        name="preference.communication.frequency",
        semantic_domain="communication_frequency",
        cardinality=PREDICATE_CARDINALITY_SINGLE,
        temporal_behavior=PREDICATE_TEMPORAL_TIMELESS,
        update_policy=PREDICATE_UPDATE_REPLACE,
    ),
    "preference.relationship.conflict_resolution": CanonicalPredicateSpec(
        name="preference.relationship.conflict_resolution",
        semantic_domain="conflict_resolution",
        cardinality=PREDICATE_CARDINALITY_MULTI,
        temporal_behavior=PREDICATE_TEMPORAL_TIMELESS,
        update_policy=PREDICATE_UPDATE_APPEND,
    ),
    "preference.emotional.need": CanonicalPredicateSpec(
        name="preference.emotional.need",
        semantic_domain="emotional",
        cardinality=PREDICATE_CARDINALITY_MULTI,
        temporal_behavior=PREDICATE_TEMPORAL_TIMELESS,
        update_policy=PREDICATE_UPDATE_APPEND,
    ),
    "preference.value.priority": CanonicalPredicateSpec(
        name="preference.value.priority",
        semantic_domain="value",
        cardinality=PREDICATE_CARDINALITY_MULTI,
        temporal_behavior=PREDICATE_TEMPORAL_TIMELESS,
        update_policy=PREDICATE_UPDATE_APPEND,
    ),
}


def _annotate_predicate_schema(
    spec: CanonicalPredicateSpec,
) -> CanonicalPredicateSpec:
    """Fill the reviewed schema metadata for the canonical registry.

    The metadata deliberately lives beside the predicate registry instead of
    being inferred from an incoming model claim.  Unknown/custom predicates
    therefore cannot opt themselves into an UPDATE policy by supplying a
    payload field.
    """

    name = spec.name
    cardinality = spec.cardinality
    temporal_behavior = spec.temporal_behavior
    update_policy = spec.update_policy

    if cardinality == PredicateCardinality.UNKNOWN:
        if name in {
            "preference.food.cuisine",
            "preference.activity.type",
            "preference.general",
            "relationship.romantic_interest",
            "interaction.channel",
            "interaction.topic_scope",
        }:
            cardinality = PredicateCardinality.MULTI
        else:
            cardinality = PredicateCardinality.SINGLE

    if temporal_behavior == PredicateTemporalBehavior.UNKNOWN:
        if name in {"interaction.channel", "interaction.topic_scope"} or name.startswith(
            "interaction."
        ):
            temporal_behavior = PredicateTemporalBehavior.PATTERN
        elif (
            name in {"plan.status"}
            or name.startswith("relationship.")
            or name.startswith("contact.")
        ):
            temporal_behavior = PredicateTemporalBehavior.CURRENT
        elif name.startswith("preference.") or name == "relationship.romantic_interest":
            temporal_behavior = PredicateTemporalBehavior.TIMELESS
        else:
            temporal_behavior = PredicateTemporalBehavior.UNKNOWN

    if update_policy == PredicateUpdatePolicy.UNKNOWN:
        if name in {
            "contact.status",
            "relationship.stage",
            "relationship.repair_status",
            "confession.status",
            "plan.status",
            "relationship.familiarity",
            "relationship.contact_opportunity",
            "relationship.conflict_status",
            "relationship.interaction_reciprocity",
            "partner.relationship_status",
            "interaction.contact_frequency",
            "interaction.initiation_balance",
            "interaction.response_engagement",
            "interaction.emotional_disclosure",
            "preference.food.spiciness",
            "preference.environment.noise",
            "preference.budget.range",
        }:
            update_policy = (
                PredicateUpdatePolicy.TRANSITION
                if spec.high_risk or name in {
                    "contact.status",
                    "relationship.stage",
                    "relationship.repair_status",
                    "confession.status",
                    "plan.status",
                    "relationship.conflict_status",
                }
                else PredicateUpdatePolicy.REPLACE
            )
        elif cardinality == PredicateCardinality.MULTI:
            update_policy = PredicateUpdatePolicy.APPEND
        else:
            update_policy = PredicateUpdatePolicy.REPLACE

    return replace(
        spec,
        cardinality=cardinality,
        temporal_behavior=temporal_behavior,
        update_policy=update_policy,
    )


# Keep the registry as the single source of truth while ensuring every
# canonical predicate exposes a complete, stable schema view.
CANONICAL_PREDICATES = {
    name: _annotate_predicate_schema(spec)
    for name, spec in CANONICAL_PREDICATES.items()
}


PREDICATE_ALIASES: dict[str, PredicateAlias] = {}


def _register_aliases(
    canonical_predicate: str,
    state_value: str | None,
    *aliases: str,
) -> None:
    for alias in aliases:
        PREDICATE_ALIASES[_normalize_identifier(alias)] = PredicateAlias(
            canonical_predicate=canonical_predicate,
            state_value=state_value,
        )


_register_aliases(
    "relationship.romantic_interest",
    None,
    "likes",
    "has_crush_on",
    "likes_person",
    "is_attracted_to",
)
_register_aliases(
    "contact.status",
    "restored",
    "contact_restored",
    "partner_replied",
    "partner_responded",
    "partner_resumed_contact",
    "received_reply",
    "resumed_contact",
    "resumed_communication",
    "started_talking_again",
    "communication_recovered",
)
_register_aliases(
    "interaction.response_engagement",
    "responsive",
    "response_restored",
    "resumed_chatting",
)
_register_aliases(
    "contact.status",
    "unavailable",
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
)
_register_aliases(
    "contact.status",
    "reduced",
    "contact_frequency_declined",
    "reply_frequency_declined",
    "contact_reduced",
)
_register_aliases(
    "relationship.repair_status",
    "in_progress",
    "apologized_to_user",
    "mutual_apology",
    "partner_apologized",
    "partner_said_sorry",
)
_register_aliases(
    "relationship.repair_status",
    "completed",
    "conflict_resolved",
    "made_up",
    "reconciled",
    "interaction.reconciliation",
    "reconciliation_occurred",
    "relationship_reconciled",
    "relationship_repaired",
    "resolved_conflict",
)
_register_aliases(
    "relationship.conflict_status",
    "active",
    "cold_war_active",
    "conflict_active",
    "in_conflict",
    "unresolved_conflict",
)
_register_aliases(
    "confession.status",
    "intended",
    "intend_to_confess",
    "plan_to_confess",
    "confession_planned",
    "plans_to_confess",
    "will_confess",
)
_register_aliases(
    "confession.status",
    "executed",
    "confessed",
    "confessed_to_partner",
    "confession_executed",
)
_register_aliases(
    "confession.status",
    "accepted",
    "confession_accepted",
    "confession_succeeded",
)
_register_aliases(
    "confession.status",
    "rejected",
    "confession_rejected",
)
_register_aliases(
    "relationship.stage",
    "dating",
    "relationship_confirmed",
    "relationship_started",
)
_register_aliases(
    "relationship.stage",
    "separated",
    "broke_up",
    "relationship_ended",
    "separated",
)


_STATE_DIMENSION_PREDICATES = {
    "relationship_familiarity": "relationship.familiarity",
    "familiarity": "relationship.familiarity",
    "relationship_closeness": "relationship.familiarity",
    "contact_opportunity": "relationship.contact_opportunity",
    "meeting_opportunity": "relationship.contact_opportunity",
    "interaction_opportunity": "relationship.contact_opportunity",
    "contact_availability": "contact.status",
    "communication_availability": "contact.status",
    "reachability": "contact.status",
    "conflict_status": "relationship.conflict_status",
    "relationship_conflict_status": "relationship.conflict_status",
    "interaction_reciprocity": "relationship.interaction_reciprocity",
    "reciprocity": "relationship.interaction_reciprocity",
    "interaction_balance": "relationship.interaction_reciprocity",
    "partner_relationship_status": "partner.relationship_status",
    "relationship_status": "partner.relationship_status",
    "partner_status": "partner.relationship_status",
    "romantic_availability": "partner.relationship_status",
    "relationship.contact_status": "contact.status",
    "relationship.stage": "relationship.stage",
    "relationship_stage": "relationship.stage",
    "relationship.repair_status": "relationship.repair_status",
    "relationship.confession_status": "confession.status",
    "relationship_confession_status": "confession.status",
    "confession_status": "confession.status",
}

_INTERACTION_METRIC_PREDICATES = {
    "contact_frequency": "interaction.contact_frequency",
    "communication_frequency": "interaction.contact_frequency",
    "interaction_frequency": "interaction.contact_frequency",
    "meeting_frequency": "interaction.contact_frequency",
    "topic_scope": "interaction.topic_scope",
    "conversation_topics": "interaction.topic_scope",
    "conversation_topic_scope": "interaction.topic_scope",
    "interaction_channel": "interaction.channel",
    "communication_channel": "interaction.channel",
    "conversation_channel": "interaction.channel",
    "initiation_balance": "interaction.initiation_balance",
    "initiative_balance": "interaction.initiation_balance",
    "response_engagement": "interaction.response_engagement",
    "reply_engagement": "interaction.response_engagement",
    "emotional_disclosure": "interaction.emotional_disclosure",
    "conflict_frequency": "interaction.conflict_frequency",
    "conflict_trend": "interaction.conflict_frequency",
    "argument_frequency": "interaction.conflict_frequency",
}

_PREFERENCE_PREDICATES = {
    "cuisine": "preference.food.cuisine",
    "food": "preference.food.cuisine",
    "dish": "preference.food.cuisine",
    "spiciness": "preference.food.spiciness",
    "spicy": "preference.food.spiciness",
    "noise": "preference.environment.noise",
    "environment": "preference.environment.noise",
    "activity": "preference.activity.type",
    "date": "preference.activity.type",
    "budget": "preference.budget.range",
    "price": "preference.budget.range",
    # V2.1 bounded preference categories.  Keep aliases explicit rather than
    # guessing a category from arbitrary free text.
    "personal_interest": "preference.personal_interest.topic",
    "hobby": "preference.hobby.activity",
    "interest": "preference.interest.topic",
    "consumption": "preference.consumption.item",
    "consumption_preference": "preference.consumption.item",
    "lifestyle": "preference.lifestyle.habit",
    "relationship": "preference.relationship.partner_trait",
    "relationship_preference": "preference.relationship.partner_trait",
    "partner_trait": "preference.relationship.partner_trait",
    "interaction_style": "preference.relationship.interaction_style",
    "emotional_need": "preference.relationship.emotional_need",
    "communication": "preference.communication.style",
    "communication_preference": "preference.communication.style",
    "communication_style": "preference.communication.style",
    "communication_frequency": "preference.communication.frequency",
    "conflict_resolution": "preference.relationship.conflict_resolution",
    "emotional": "preference.emotional.need",
    "emotional_preference": "preference.emotional.need",
    "value": "preference.value.priority",
    "value_preference": "preference.value.priority",
}

# The V2.1 category shape uses ``domain`` + ``dimension`` instead of the
# legacy ``preference_type`` field.  Only reviewed pairs are accepted here;
# an unknown pair still follows the normal custom/general fail-closed path.
_PREFERENCE_DOMAIN_DIMENSIONS = {
    ("food", "cuisine"): "preference.food.cuisine",
    ("food", "spiciness"): "preference.food.spiciness",
    ("food", "taste"): "preference.food.spiciness",
    ("environment", "noise"): "preference.environment.noise",
    ("activity", "type"): "preference.activity.type",
    ("budget", "range"): "preference.budget.range",
    ("personal_interest", "topic"): "preference.personal_interest.topic",
    ("hobby", "activity"): "preference.hobby.activity",
    ("hobby", "topic"): "preference.hobby.activity",
    ("interest", "topic"): "preference.interest.topic",
    ("consumption", "item"): "preference.consumption.item",
    ("lifestyle", "habit"): "preference.lifestyle.habit",
    ("relationship", "partner_trait"): "preference.relationship.partner_trait",
    ("relationship", "interaction_style"): "preference.relationship.interaction_style",
    ("relationship", "emotional_need"): "preference.relationship.emotional_need",
    ("communication", "style"): "preference.communication.style",
    ("communication", "communication_style"): "preference.communication.style",
    ("communication", "frequency"): "preference.communication.frequency",
    ("communication", "communication_frequency"): "preference.communication.frequency",
    ("relationship", "conflict_resolution"): "preference.relationship.conflict_resolution",
    ("emotional", "need"): "preference.emotional.need",
    ("emotional", "emotional_need"): "preference.emotional.need",
    ("value", "priority"): "preference.value.priority",
}

# These are deliberately broad semantic markers, rather than a list of
# individual preference values.  They are only used to reject a canonical
# domain that is contradicted by the supplied value/evidence.  A value is
# reclassified only when exactly one registered sibling domain is supported.
_PREFERENCE_DOMAIN_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "food": (
        re.compile(r"吃|食物|饮食|菜|料理|餐|饭|面|粥|汤|肉|鱼|辣|甜|咸|酸|苦|寿司|日料"),
    ),
    "activity": (
        re.compile(
            r"看展|展览|电影|博物馆|演出|音乐会|旅行|旅游|爬山|游泳|打球|运动|"
            r"徒步|骑行|露营|摄影|桌游|聚会|约会"
        ),
    ),
    "environment": (
        # This domain currently governs only the reviewed ``noise``
        # dimension.  Broad environment words (outdoor, lighting, ambience)
        # are not sufficient to infer that narrower setting.
        re.compile(r"安静|清静|宁静|热闹|吵闹|嘈杂|噪音|quiet|noisy|noise|loud", re.IGNORECASE),
    ),
    "budget": (
        re.compile(r"预算|价格|花费|消费|便宜|实惠|省钱|贵|经济"),
    ),
    "hobby": (
        re.compile(r"篮球|足球|羽毛球|跑步|游泳|健身|音乐|听歌|画画|绘画|乐器|桌游|游戏"),
    ),
    "interest": (
        re.compile(r"感兴趣|兴趣|摄影|历史|科技|艺术|文学|播客|研究"),
    ),
    "lifestyle": (
        re.compile(r"习惯|作息|早睡|早起|熬夜|独处|一个人|睡前|生活方式"),
    ),
    "communication_frequency": (
        re.compile(r"多交流|多沟通|经常沟通|常联系|保持联系|沟通频率"),
    ),
    "conflict_resolution": (
        re.compile(r"冷处理|冷战|说开|及时沟通|吵架后|冲突后|解决矛盾"),
    ),
    "emotional": (
        re.compile(r"安全感|被理解|被安慰|情绪支持|陪伴|表达感受|情感需求"),
    ),
}

_PREFERENCE_DOMAIN_BY_TYPE = {
    "cuisine": "food",
    "food": "food",
    "dish": "food",
    "spiciness": "food",
    "activity": "activity",
    "date": "activity",
    "noise": "environment",
    "environment": "environment",
    "budget": "budget",
    "price": "budget",
    "personal_interest": "personal_interest",
    "hobby": "hobby",
    "interest": "interest",
    "consumption": "consumption",
    "consumption_preference": "consumption",
    "lifestyle": "lifestyle",
    "relationship": "relationship",
    "relationship_preference": "relationship",
    "partner_trait": "relationship",
    "interaction_style": "relationship",
    "emotional_need": "relationship",
    "communication": "communication",
    "communication_preference": "communication",
    "communication_style": "communication",
    "communication_frequency": "communication_frequency",
    "conflict_resolution": "conflict_resolution",
    "emotional": "emotional",
    "emotional_preference": "emotional",
    "value": "value",
    "value_preference": "value",
}

_STATE_VALUE_ALIASES = {
    "contact.status": {
        "available": "normal",
        "reachable": "normal",
        "limited": "reduced",
        "rare": "reduced",
        "blocked": "unavailable",
        "unreachable": "unavailable",
        "recovered": "restored",
        "available_again": "restored",
    },
    "relationship.conflict_status": {
        "unresolved": "active",
        "in_conflict": "active",
        "deescalating": "cooling",
        "reconciliation": "repairing",
        "repaired": "resolved",
    },
    "relationship.stage": {
        "friend": "acquaintance",
        "friends": "acquaintance",
        "friendship": "acquaintance",
        "ordinary_friends": "acquaintance",
        "partnered": "dating",
        "stable_relationship": "committed",
        "long_distance": "committed",
        "breakup": "separated",
    },
    "confession.status": {
        "confessed": "executed",
        "confessed_pending_response": "executed",
        "pending_response": "executed",
    },
}

_PREFERENCE_VALUE_ALIASES = {
    "日本料理": "日料",
    "日本菜": "日料",
    "japanese_food": "日料",
    "japanese_cuisine": "日料",
}


_STABLE_FACT_PREDICATE_ALIASES = {
    # Residence/location.  Broad words such as ``location`` are safe here
    # because this map is consulted only for ``stable_fact`` candidates.
    "residence": "profile.residence",
    "current_residence": "profile.residence",
    "residential_location": "profile.residence",
    "current_location": "profile.residence",
    "home_location": "profile.residence",
    "location": "profile.residence",
    "lives_in": "profile.residence",
    "resides_in": "profile.residence",
    "居住地": "profile.residence",
    "现居地": "profile.residence",
    "居住城市": "profile.residence",
    # Current occupation/work identity.
    "occupation": "profile.occupation",
    "profession": "profile.occupation",
    "job": "profile.occupation",
    "job_title": "profile.occupation",
    "work": "profile.occupation",
    "works_as": "profile.occupation",
    "职业": "profile.occupation",
    "工作": "profile.occupation",
    # Primary contact method/address.  This does not infer contact data from
    # arbitrary numbers; a model must explicitly choose one of these aliases.
    "contact_method": "profile.contact_method",
    "preferred_contact_method": "profile.contact_method",
    "contact_details": "profile.contact_method",
    "phone": "profile.contact_method",
    "phone_number": "profile.contact_method",
    "mobile_number": "profile.contact_method",
    "wechat_id": "profile.contact_method",
    "联系方式": "profile.contact_method",
    "联系电话": "profile.contact_method",
    "手机号": "profile.contact_method",
    "微信号": "profile.contact_method",
    # Birthday/date of birth.
    "birthday": "profile.birthday",
    "birth_date": "profile.birthday",
    "date_of_birth": "profile.birthday",
    "dob": "profile.birthday",
    "生日": "profile.birthday",
    "出生日期": "profile.birthday",
    # Open-valued identity labels (for example role or self-description) are
    # multi-valued and therefore never inherit single-value replacement.
    "identity": "profile.identity",
    "identity_label": "profile.identity",
    "self_identity": "profile.identity",
    "身份": "profile.identity",
    "身份认同": "profile.identity",
}
_STABLE_FACT_PREDICATES = frozenset(_STABLE_FACT_PREDICATE_ALIASES.values())


def normalize_predicate(
    *,
    kind: object,
    raw_predicate: object = None,
    canonical_predicate: object = None,
    custom_predicate: object = None,
    predicate_type: object = None,
    payload: dict[str, Any] | None = None,
) -> PredicateNormalization:
    payload = payload or {}
    kind_value = str(getattr(kind, "value", kind or ""))
    raw = _clean_string(raw_predicate) or _clean_string(payload.get("predicate"))
    requested_canonical = _clean_string(canonical_predicate)
    requested_custom = _clean_string(custom_predicate)
    requested_type = str(getattr(predicate_type, "value", predicate_type or "")).casefold()
    raw_identifier = _normalize_identifier(raw) if raw else None

    if kind_value == "stable_fact":
        requested_identifier = (
            _normalize_identifier(requested_canonical) if requested_canonical else None
        )
        stable_predicate = (
            requested_identifier
            if requested_identifier in _STABLE_FACT_PREDICATES
            else _STABLE_FACT_PREDICATE_ALIASES.get(requested_identifier or "")
            or _STABLE_FACT_PREDICATE_ALIASES.get(raw_identifier or "")
        )
        if stable_predicate is not None:
            spec = CANONICAL_PREDICATES[stable_predicate]
            return PredicateNormalization(
                raw_predicate=raw or requested_canonical or stable_predicate,
                predicate_type="canonical",
                canonical_predicate=stable_predicate,
                custom_predicate=None,
                state_dimension=spec.state_dimension,
                state_value=_stable_fact_value(payload),
                alias_hit=(
                    requested_identifier != stable_predicate
                    if requested_identifier
                    else raw_identifier != stable_predicate
                ),
            )

    if kind_value == "preference":
        canonical = _preference_predicate(payload, requested_canonical or raw)
        canonical, custom = _enforce_preference_domain(
            canonical=canonical,
            raw_predicate=raw,
            requested_custom=requested_custom,
            payload=payload,
        )
        if canonical is None:
            return PredicateNormalization(
                raw_predicate=raw or requested_canonical or requested_custom or "preference",
                predicate_type="custom",
                canonical_predicate=None,
                custom_predicate=custom,
                state_dimension=None,
                state_value=None,
                alias_hit=bool(requested_canonical),
            )
        return PredicateNormalization(
            raw_predicate=raw or requested_canonical or canonical,
            predicate_type="canonical",
            canonical_predicate=canonical,
            custom_predicate=None,
            state_dimension=None,
            state_value=None,
            alias_hit=canonical != requested_canonical and bool(requested_canonical),
        )

    # Profile predicates are authorized only for stable facts.  A model cannot
    # opt an event or relationship state into profile replacement semantics
    # merely by emitting a registered-looking canonical name.
    if kind_value != "stable_fact" and (
        requested_canonical in _STABLE_FACT_PREDICATES
        or raw_identifier in _STABLE_FACT_PREDICATES
    ):
        custom = requested_custom or raw or requested_canonical or "unknown"
        return PredicateNormalization(
            raw_predicate=raw or requested_canonical or custom,
            predicate_type="custom",
            canonical_predicate=None,
            custom_predicate=_normalize_identifier(custom),
            state_dimension=None,
            state_value=None,
        )

    state_dimension = _clean_string(
        payload.get("state_dimension_hint") or payload.get("state_dimension")
    )
    if state_dimension:
        lifecycle_dimension = normalize_state_dimension(state_dimension)
        state_predicate = _STATE_DIMENSION_PREDICATES.get(
            _normalize_identifier(lifecycle_dimension or state_dimension)
        )
        if state_predicate:
            spec = CANONICAL_PREDICATES[state_predicate]
            raw_value = payload.get("state_value_hint", payload.get("state_value"))
            value = (
                normalize_state_value(lifecycle_dimension, raw_value)
                if lifecycle_dimension is not None
                else _normalize_state_value(state_predicate, raw_value)
            )
            if value is not None and (not spec.allowed_values or value in spec.allowed_values):
                return PredicateNormalization(
                    raw_predicate=raw or requested_canonical or state_predicate,
                    predicate_type="canonical",
                    canonical_predicate=state_predicate,
                    custom_predicate=None,
                    state_dimension=lifecycle_dimension or spec.state_dimension,
                    state_value=value,
                    alias_hit=(
                        _normalize_identifier(raw) != state_predicate
                        if raw
                        else state_predicate != requested_canonical
                    ),
                )

    metric = _clean_string(payload.get("metric_hint") or payload.get("metric"))
    if metric:
        metric_predicate = _INTERACTION_METRIC_PREDICATES.get(_normalize_identifier(metric))
        if metric_predicate:
            state_value = interaction_pattern_state(metric, payload)
            raw_alias = PREDICATE_ALIASES.get(_normalize_identifier(raw)) if raw else None
            if (
                state_value is None
                and raw_alias is not None
                and raw_alias.canonical_predicate == metric_predicate
            ):
                state_value = raw_alias.state_value
            return PredicateNormalization(
                raw_predicate=raw or metric,
                predicate_type="canonical",
                canonical_predicate=metric_predicate,
                custom_predicate=None,
                state_dimension=CANONICAL_PREDICATES[metric_predicate].state_dimension,
                state_value=state_value,
                alias_hit=_normalize_identifier(metric) != metric_predicate,
            )

    if requested_canonical in CANONICAL_PREDICATES:
        spec = CANONICAL_PREDICATES[requested_canonical]
        value = _normalize_state_value(requested_canonical, payload.get("state_value"))
        if not spec.allowed_values or value in spec.allowed_values:
            return PredicateNormalization(
                raw_predicate=raw or requested_canonical,
                predicate_type="canonical",
                canonical_predicate=requested_canonical,
                custom_predicate=None,
                state_dimension=spec.state_dimension,
                state_value=value,
            )

    if raw_identifier in CANONICAL_PREDICATES:
        spec = CANONICAL_PREDICATES[raw_identifier]
        value = _normalize_state_value(raw_identifier, payload.get("state_value"))
        if not spec.allowed_values or value in spec.allowed_values:
            return PredicateNormalization(
                raw_predicate=raw or raw_identifier,
                predicate_type="canonical",
                canonical_predicate=raw_identifier,
                custom_predicate=None,
                state_dimension=spec.state_dimension,
                state_value=value,
            )

    alias = PREDICATE_ALIASES.get(raw_identifier) if raw_identifier else None
    if alias is not None:
        spec = CANONICAL_PREDICATES[alias.canonical_predicate]
        state_value = alias.state_value or _normalize_state_value(
            alias.canonical_predicate,
            payload.get("state_value"),
        )
        return PredicateNormalization(
            raw_predicate=raw,
            predicate_type="canonical",
            canonical_predicate=alias.canonical_predicate,
            custom_predicate=None,
            state_dimension=spec.state_dimension,
            state_value=state_value,
            alias_hit=True,
        )

    custom = requested_custom or raw or requested_canonical
    if requested_type == "canonical" and requested_canonical:
        custom = requested_canonical
    custom = _normalize_identifier(custom) if custom else "unknown"
    return PredicateNormalization(
        raw_predicate=raw or requested_canonical or requested_custom or custom,
        predicate_type="custom",
        canonical_predicate=None,
        custom_predicate=custom,
        state_dimension=None,
        state_value=None,
    )


def _stable_fact_value(payload: dict[str, Any]) -> str | None:
    for key in ("state_value", "value", "object"):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        normalized = unicodedata.normalize("NFKC", value).casefold().strip()
        return re.sub(r"\s+", " ", normalized)
    return None


def normalize_preference_value(value: object) -> str:
    normalized = _normalize_identifier(str(value or ""))
    return _PREFERENCE_VALUE_ALIASES.get(normalized, normalized)


def canonical_predicate_names() -> tuple[str, ...]:
    return tuple(CANONICAL_PREDICATES)


def predicate_spec(value: str | None) -> CanonicalPredicateSpec | None:
    """Return reviewed governance metadata for a canonical predicate.

    ``None`` is returned for open-world/custom names.  Callers must not infer
    schema metadata from a custom predicate or from arbitrary model payload.
    """

    if not isinstance(value, str):
        return None
    return CANONICAL_PREDICATES.get(value)


# Descriptive alias used by relation/retrieval integrations.
predicate_schema = predicate_spec


def is_high_risk_predicate(value: str | None) -> bool:
    return bool(value and CANONICAL_PREDICATES.get(value, CanonicalPredicateSpec("")).high_risk)


def _preference_predicate(payload: dict[str, Any], requested: str | None) -> str:
    if (
        requested in CANONICAL_PREDICATES
        and requested.startswith("preference.")
        and requested != "preference.general"
    ):
        return requested
    preference_type = _normalize_identifier(
        str(payload.get("preference_type_hint") or payload.get("preference_type") or "")
    )
    if preference_type in _PREFERENCE_PREDICATES:
        return _PREFERENCE_PREDICATES[preference_type]

    domain = _normalize_preference_domain_value(
        payload.get("domain")
        or payload.get("preference_domain")
        or payload.get("category")
    )
    dimension = _normalize_identifier(
        str(payload.get("dimension") or payload.get("preference_dimension") or "")
    )
    if domain and dimension:
        category_predicate = _PREFERENCE_DOMAIN_DIMENSIONS.get((domain, dimension))
        if category_predicate is not None:
            return category_predicate
    return "preference.general"


def _enforce_preference_domain(
    *,
    canonical: str,
    raw_predicate: str | None,
    requested_custom: str | None,
    payload: dict[str, Any],
) -> tuple[str | None, str]:
    """Keep registered preference predicates aligned with their value domain.

    The model is allowed to propose a canonical predicate, but a registered
    name is not evidence that the value belongs to that predicate's domain.
    This guard intentionally fails closed when the available evidence cannot
    identify one safe sibling predicate.
    """

    spec = CANONICAL_PREDICATES.get(canonical)
    if spec is None:
        return canonical, requested_custom or raw_predicate or canonical

    hint_domain = _preference_hint_domain(payload)
    if hint_domain is not None:
        if spec.semantic_domain == hint_domain:
            return canonical, requested_custom or raw_predicate or canonical
        hinted_candidates = {
            name
            for name, sibling in CANONICAL_PREDICATES.items()
            if sibling.semantic_domain == hint_domain
        }
        if len(hinted_candidates) == 1:
            return next(iter(hinted_candidates)), requested_custom or raw_predicate or canonical

    observed_domains = _observed_preference_domains(payload)
    explicit_pair = _explicit_preference_domain_dimension_predicate(payload)

    # A reviewed domain+dimension pair proposed by the semantic layer is only
    # accepted when the value/evidence independently supports the registered
    # semantic domain.  This lets deterministic V3 classifications disambiguate
    # overlapping values such as photography (activity vs interest) without
    # weakening the existing guard for an unsupported model label.
    if (
        explicit_pair == canonical
        and spec.semantic_domain is not None
        and spec.semantic_domain in observed_domains
    ):
        return canonical, requested_custom or raw_predicate or canonical

    if spec.semantic_domain is None:
        # ``preference.general`` remains a compatibility fallback when the
        # value has no reliable domain.  Only value-level semantic evidence
        # may identify a registered sibling here.  A broad ``domain`` alone
        # never proves a narrower dimension such as environment/noise or
        # relationship/partner_trait; otherwise registry shape would create
        # false canonical uniqueness.
        sibling_candidates = {
            name
            for name, sibling in CANONICAL_PREDICATES.items()
            if sibling.semantic_domain in observed_domains
        }
        if len(sibling_candidates) == 1:
            return next(iter(sibling_candidates)), requested_custom or raw_predicate or canonical
        # ``general`` is only a temporary model hint.  Without a unique
        # registered domain, preserve the claim as Custom instead of allowing
        # an untyped canonical preference into governance.
        return None, requested_custom or raw_predicate or canonical

    explicit_domain = _explicit_preference_domain(payload)
    if explicit_domain is not None:
        observed_domains.add(explicit_domain)

    if not observed_domains:
        # A canonical claim without a value cannot be proven inconsistent.
        return canonical, requested_custom or raw_predicate or canonical
    if observed_domains == {spec.semantic_domain}:
        return canonical, requested_custom or raw_predicate or canonical

    sibling_candidates = {
        name
        for name, sibling in CANONICAL_PREDICATES.items()
        if sibling.semantic_domain in observed_domains
    }
    if len(sibling_candidates) == 1:
        return next(iter(sibling_candidates)), requested_custom or raw_predicate or canonical

    # Do not retain a known-but-contradicted canonical predicate.  The raw
    # model predicate remains useful as a custom identifier for audit/debug.
    return None, requested_custom or raw_predicate or canonical


def _explicit_preference_domain(payload: dict[str, Any]) -> str | None:
    preference_type = _normalize_identifier(
        str(payload.get("preference_type_hint") or payload.get("preference_type") or "")
    )
    return _PREFERENCE_DOMAIN_BY_TYPE.get(
        preference_type
    ) or _normalize_preference_domain_value(
        payload.get("domain")
        or payload.get("preference_domain")
        or payload.get("category")
    )


def _explicit_preference_domain_dimension_predicate(
    payload: dict[str, Any],
) -> str | None:
    domain = _normalize_preference_domain_value(
        payload.get("domain")
        or payload.get("preference_domain")
        or payload.get("category")
    )
    dimension = _normalize_identifier(
        str(payload.get("dimension") or payload.get("preference_dimension") or "")
    )
    if not domain or not dimension:
        return None
    return _PREFERENCE_DOMAIN_DIMENSIONS.get((domain, dimension))


def _preference_hint_domain(payload: dict[str, Any]) -> str | None:
    # Only the repair layer's explicit type hint is authoritative here.
    # ``domain`` is intentionally not a hint: it describes a broad category
    # and cannot authorize a narrower registered dimension on its own.
    hint = _normalize_identifier(str(payload.get("preference_type_hint") or ""))
    return _PREFERENCE_DOMAIN_BY_TYPE.get(hint) or _normalize_preference_domain_value(hint)


def _observed_preference_domains(payload: dict[str, Any]) -> set[str]:
    values: list[str] = []
    for key in (
        "preference",
        "object",
        "value",
        "activity_type",
        "summary",
        "evidence",
        "evidence_spans",
    ):
        value = payload.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(item for item in value if isinstance(item, str))
    text = " ".join(values)
    return {
        domain
        for domain, patterns in _PREFERENCE_DOMAIN_PATTERNS.items()
        if any(pattern.search(text) for pattern in patterns)
    }


def _normalize_preference_domain_value(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = _normalize_identifier(value)
    aliases = {
        "personal_interest": "personal_interest",
        "personal_interest_preference": "personal_interest",
        "hobby": "hobby",
        "hobby_preference": "hobby",
        "interest": "interest",
        "interest_preference": "interest",
        "consumption": "consumption",
        "consumption_preference": "consumption",
        "lifestyle": "lifestyle",
        "lifestyle_preference": "lifestyle",
        "relationship": "relationship",
        "relationship_preference": "relationship",
        "communication": "communication",
        "communication_preference": "communication",
        "communication_frequency": "communication_frequency",
        "communication_frequency_preference": "communication_frequency",
        "conflict_resolution": "conflict_resolution",
        "conflict_resolution_preference": "conflict_resolution",
        "emotional": "emotional",
        "emotional_preference": "emotional",
        "value": "value",
        "value_preference": "value",
    }
    if normalized in aliases:
        return aliases[normalized]
    return normalized if normalized in {
        "food",
        "activity",
        "environment",
        "budget",
        "personal_interest",
        "hobby",
        "interest",
        "consumption",
        "lifestyle",
        "relationship",
        "communication",
        "communication_frequency",
        "conflict_resolution",
        "emotional",
        "value",
    } else None


def _normalize_state_value(canonical_predicate: str, value: object) -> str | None:
    if value is None:
        return None
    normalized = _normalize_identifier(str(value))
    return _STATE_VALUE_ALIASES.get(canonical_predicate, {}).get(normalized, normalized)


def _clean_string(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()

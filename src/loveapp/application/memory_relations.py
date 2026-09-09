from dataclasses import dataclass, field
from typing import Any

from loveapp.domain.memory import (
    ClaimRelation,
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    PredicateType,
    memory_dedupe_key,
)
from loveapp.domain.memory_lifecycle import (
    MemoryRole,
    governed_state_identity,
    governed_state_value,
    memory_concept,
    memory_role,
)
from loveapp.domain.memory_predicates import (
    CanonicalPredicateSpec,
    PredicateCardinality,
    PredicateTemporalBehavior,
    PredicateUpdatePolicy,
    normalize_predicate,
    normalize_preference_value,
    predicate_spec,
)
from loveapp.domain.memory_type_compatibility import (
    MemoryComparisonMode,
    MemoryTypeCompatibility,
    assess_memory_type_compatibility,
)


@dataclass(frozen=True)
class ClaimRelationResolution:
    relation: ClaimRelation
    target_memory_ids: tuple[str, ...] = ()
    rule_name: str = "local_unrelated"
    reason: str = "No conflicting active memory was found."
    diagnostics: dict[str, Any] = field(default_factory=dict)


def resolve_claim_relation(
    candidate: MemoryCandidate,
    active_memories: list[MemoryItem],
    *,
    incoming_status: MemoryStatus,
) -> ClaimRelationResolution:
    """Resolve a claim with a safety-first deterministic decision tree.

    The order is intentional: identity, governed state transitions, and
    canonical schema-aware value comparison run before the open-world Custom
    fallback.  Custom predicates are never promoted to a schema merely because
    a model supplied a similarly named payload field.
    """

    semantic_memories, evidence_only, incompatible = _partition_type_candidates(
        candidate,
        active_memories,
    )
    type_diagnostics = {
        "semantic_candidate_ids": [item.id for item in semantic_memories],
        "evidence_candidate_ids": [item.id for item, _ in evidence_only],
        "type_rejected_candidates": [
            {"memory_id": item.id, "reason": compatibility.reason}
            for item, compatibility in incompatible
        ],
    }

    candidate_key = memory_dedupe_key(candidate)
    same = [
        item
        for item in semantic_memories
        if memory_dedupe_key(item) == candidate_key
        and assess_memory_type_compatibility(candidate, item).allows(
            ClaimRelation.SAME
        )
    ]
    if same:
        keeper = max(same, key=_keeper_rank)
        return ClaimRelationResolution(
            ClaimRelation.SAME,
            (keeper.id,),
            "normalized_dedupe",
            "The normalized claim identity already exists in this relationship.",
            type_diagnostics,
        )

    state_identity = governed_state_identity(candidate)
    state_value = governed_state_value(candidate)
    state_spec = predicate_spec(candidate.canonical_predicate)
    state_update_allowed = state_spec is None or state_spec.update_policy in {
        PredicateUpdatePolicy.REPLACE,
        PredicateUpdatePolicy.TRANSITION,
    }
    if state_identity is not None and state_value is not None and state_update_allowed:
        same_dimension = [
            item
            for item in semantic_memories
            if item.subject.casefold() == candidate.subject.casefold()
            and governed_state_identity(item) == state_identity
            and governed_state_value(item) not in {None, state_value}
        ]
        if same_dimension:
            if not all(
                assess_memory_type_compatibility(
                    candidate,
                    item,
                ).lifecycle_replace_allowed
                for item in same_dimension
            ):
                return ClaimRelationResolution(
                    ClaimRelation.CONTRADICTION,
                    tuple(item.id for item in same_dimension),
                    "epistemic_state_conflict",
                    "Belief and factual state claims may conflict but cannot replace one another.",
                    type_diagnostics,
                )
            confirmed = [item for item in same_dimension if item.status == MemoryStatus.CONFIRMED]
            if incoming_status == MemoryStatus.CONFIRMED:
                return ClaimRelationResolution(
                    ClaimRelation.UPDATE,
                    tuple(item.id for item in same_dimension),
                    "same_state_dimension",
                    "A confirmed value replaces older active values in the same state dimension.",
                )
            return ClaimRelationResolution(
                ClaimRelation.CONTRADICTION,
                tuple(item.id for item in confirmed or same_dimension),
                "proposed_state_conflict",
                "An unconfirmed value cannot close an existing state value.",
            )

    candidate_concept = memory_concept(candidate)
    if candidate_concept in {"contact_reduced", "contact_restored"}:
        opposite_concept = (
            "contact_restored"
            if candidate_concept == "contact_reduced"
            else "contact_reduced"
        )
        opposite = [
            item
            for item in semantic_memories
            if item.subject.casefold() == candidate.subject.casefold()
            and memory_concept(item) == opposite_concept
        ]
        if opposite:
            if not all(
                assess_memory_type_compatibility(
                    candidate,
                    item,
                ).lifecycle_replace_allowed
                for item in opposite
            ):
                return ClaimRelationResolution(
                    ClaimRelation.CONTRADICTION,
                    tuple(item.id for item in opposite),
                    "epistemic_contact_state_conflict",
                    (
                        "Belief and factual contact claims may conflict but cannot "
                        "replace one another."
                    ),
                    type_diagnostics,
                )
            confirmed = [item for item in opposite if item.status == MemoryStatus.CONFIRMED]
            if incoming_status == MemoryStatus.CONFIRMED:
                return ClaimRelationResolution(
                    ClaimRelation.UPDATE,
                    tuple(item.id for item in opposite),
                    "contact_state_transition",
                    "A confirmed contact state replaces an opposite active contact representation.",
                )
            return ClaimRelationResolution(
                ClaimRelation.CONTRADICTION,
                tuple(item.id for item in confirmed or opposite),
                "proposed_contact_state_conflict",
                "An unconfirmed contact state cannot close an existing confirmed contact state.",
            )

    if (
        candidate.kind == MemoryKind.PREFERENCE
        and candidate.predicate_type == PredicateType.CANONICAL
        and candidate.canonical_predicate is not None
    ):
        preference_resolution = _resolve_preference(
            candidate,
            semantic_memories,
            incoming_status,
        )
        if preference_resolution is not None:
            return preference_resolution

    canonical_resolution = _resolve_canonical_predicate_relation(
        candidate,
        semantic_memories,
        incoming_status=incoming_status,
    )
    if canonical_resolution is not None:
        return canonical_resolution

    if evidence_only and not semantic_memories:
        return ClaimRelationResolution(
            ClaimRelation.UNRELATED,
            (),
            "event_pattern_evidence_only",
            "Event and pattern memories use an evidence link instead of a direct claim relation.",
            type_diagnostics,
        )
    if incompatible and not semantic_memories:
        return ClaimRelationResolution(
            ClaimRelation.UNRELATED,
            (),
            "memory_type_boundary",
            "No historical memory is type-compatible for direct semantic relation.",
            type_diagnostics,
        )

    if candidate.predicate_type == PredicateType.CUSTOM:
        related_custom = [
            item
            for item in semantic_memories
            if item.subject.casefold() == candidate.subject.casefold()
            and item.kind == candidate.kind
            and item.predicate_type == PredicateType.CUSTOM
        ]
        if related_custom:
            same_event_family = [
                item
                for item in related_custom
                if candidate.kind == MemoryKind.INTERACTION_EVENT
                and item.custom_predicate == candidate.custom_predicate
            ]
            if same_event_family:
                return ClaimRelationResolution(
                    ClaimRelation.COMPLEMENTARY,
                    tuple(item.id for item in same_event_family[:5]),
                    "custom_event_occurrence",
                    "A distinct event occurrence is retained as complementary evidence.",
                    type_diagnostics,
                )
            return ClaimRelationResolution(
                ClaimRelation.UNCERTAIN,
                tuple(item.id for item in related_custom[:5]),
                "unknown_transition",
                "A custom predicate has no deterministic lifecycle rule.",
            )
        return ClaimRelationResolution(
            ClaimRelation.UNCERTAIN,
            (),
            "unknown_transition",
            "A new custom predicate is retained without automatic lifecycle effects.",
        )

    return ClaimRelationResolution(
        ClaimRelation.UNRELATED,
        (),
        "local_unrelated",
        "The claim is independently useful and has no deterministic conflict.",
        type_diagnostics,
    )


def _partition_type_candidates(
    candidate: MemoryCandidate,
    active_memories: list[MemoryItem],
) -> tuple[
    list[MemoryItem],
    list[tuple[MemoryItem, MemoryTypeCompatibility]],
    list[tuple[MemoryItem, MemoryTypeCompatibility]],
]:
    semantic: list[MemoryItem] = []
    evidence_only: list[tuple[MemoryItem, MemoryTypeCompatibility]] = []
    incompatible: list[tuple[MemoryItem, MemoryTypeCompatibility]] = []
    for item in active_memories:
        compatibility = assess_memory_type_compatibility(candidate, item)
        if compatibility.mode == MemoryComparisonMode.SEMANTIC:
            semantic.append(item)
        elif compatibility.mode == MemoryComparisonMode.EVIDENCE_ONLY:
            evidence_only.append((item, compatibility))
        else:
            incompatible.append((item, compatibility))
    return semantic, evidence_only, incompatible


@dataclass(frozen=True)
class _CanonicalClaimView:
    predicate: str
    dimension: str | None
    value_key: str | None
    event_family: str | None
    temporal_key: str | None
    entity_key: str | None
    spec: CanonicalPredicateSpec


_SUBJECT_ALIASES = {
    "she": "partner",
    "he": "partner",
    "partner": "partner",
    "relationship_partner": "partner",
    "ta": "partner",
    "user": "user",
    "me": "user",
    "relationship": "relationship",
    "couple": "relationship",
    "we": "relationship",
    "dyad": "relationship",
}


def _resolve_canonical_predicate_relation(
    candidate: MemoryCandidate,
    active_memories: list[MemoryItem],
    *,
    incoming_status: MemoryStatus,
) -> ClaimRelationResolution | None:
    incoming = _canonical_claim_view(candidate)
    if incoming is None:
        return None

    comparable: list[tuple[MemoryItem, _CanonicalClaimView]] = []
    rejected: list[dict[str, str]] = []
    incoming_subject = _subject_key(candidate.subject)
    for item in active_memories:
        if item.status not in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}:
            rejected.append({"memory_id": item.id, "reason": "inactive_status"})
            continue
        if _subject_key(item.subject) != incoming_subject:
            rejected.append({"memory_id": item.id, "reason": "subject_mismatch"})
            continue
        view = _canonical_claim_view(item)
        if view is None:
            rejected.append({"memory_id": item.id, "reason": "not_canonical"})
            continue
        if item.kind != candidate.kind and not _event_pattern_boundary(candidate, item):
            rejected.append({"memory_id": item.id, "reason": "kind_mismatch"})
            continue
        if view.predicate != incoming.predicate:
            rejected.append({"memory_id": item.id, "reason": "predicate_mismatch"})
            continue
        if not _entity_compatible(incoming.entity_key, view.entity_key):
            rejected.append({"memory_id": item.id, "reason": "entity_mismatch"})
            continue
        comparable.append((item, view))

    if not comparable:
        return None

    # Unbounded interaction dimensions are intentionally not treated as a
    # lifecycle schema merely because a model supplied ``current``.  Their
    # values require a reviewed governed-state policy before they may drive a
    # deterministic relation.
    if (
        candidate.kind == MemoryKind.INTERACTION_PATTERN
        and incoming.dimension is not None
        and governed_state_identity(candidate) is None
    ):
        return None

    diagnostics = {
        "decision_tree": ["subject", "predicate", "schema", "value", "relation"],
        "canonical_predicate": incoming.predicate,
        "state_dimension": incoming.dimension,
        "cardinality": incoming.spec.cardinality.value,
        "temporal_behavior": incoming.spec.temporal_behavior.value,
        "update_policy": incoming.spec.update_policy.value,
        "semantic_candidates": [item.id for item, _ in comparable],
        "rejected_candidates": rejected,
    }

    # A point event and a durable pattern may share a predicate vocabulary, but
    # the event must not overwrite the pattern (or vice versa).
    boundary = [
        (item, view)
        for item, view in comparable
        if _event_pattern_boundary(candidate, item)
    ]
    if boundary:
        return ClaimRelationResolution(
            ClaimRelation.COMPLEMENTARY,
            tuple(item.id for item, _ in boundary),
            "event_pattern_boundary",
            "An event and a pattern are different memory roles and cannot replace one another.",
            {**diagnostics, "relation_stage": "role_boundary"},
        )

    # Events retain temporal identity.  A later event in the same family is a
    # complementary occurrence, never a state replacement.
    event_like = (
        candidate.kind == MemoryKind.INTERACTION_EVENT
        or incoming.spec.temporal_behavior == PredicateTemporalBehavior.EVENT
    )
    if event_like:
        same_family = [
            (item, view)
            for item, view in comparable
            if incoming.event_family is not None
            and view.event_family == incoming.event_family
        ]
        if not same_family:
            return None
        same_identity = [
            item
            for item, view in same_family
            if _same_temporal_identity(incoming, view)
        ]
        if same_identity:
            keeper = max(same_identity, key=_keeper_rank)
            return ClaimRelationResolution(
                ClaimRelation.SAME,
                (keeper.id,),
                "canonical_event_identity",
                "The canonical event family and temporal identity already exist.",
                {**diagnostics, "relation_stage": "event_identity"},
            )
        return ClaimRelationResolution(
            ClaimRelation.COMPLEMENTARY,
            tuple(item.id for item, _ in same_family),
            "canonical_event_family",
            "A distinct occurrence in the same canonical event family is retained.",
            {**diagnostics, "relation_stage": "event_family"},
        )

    same_value = [
        item
        for item, view in comparable
        if incoming.value_key is not None
        and view.value_key is not None
        and incoming.value_key == view.value_key
    ]
    if same_value:
        same_eligible = [
            item
            for item in same_value
            if assess_memory_type_compatibility(candidate, item).allows(
                ClaimRelation.SAME
            )
        ]
        if not same_eligible:
            return ClaimRelationResolution(
                ClaimRelation.COMPLEMENTARY,
                tuple(item.id for item in same_value),
                "epistemic_parallel_claim",
                "Equivalent belief and factual propositions remain separate provenance records.",
                {**diagnostics, "relation_stage": "epistemic_boundary"},
            )
        keeper = max(same_eligible, key=_keeper_rank)
        return ClaimRelationResolution(
            ClaimRelation.SAME,
            (keeper.id,),
            "canonical_value_identity",
            "The canonical predicate and normalized value already exist.",
            {**diagnostics, "relation_stage": "value_identity"},
        )

    if incoming.value_key is None:
        if candidate.kind in {
            MemoryKind.ADVICE_OUTCOME,
            MemoryKind.PLANNED_EVENT,
            MemoryKind.ACTION_INTENT,
        }:
            return None
        return ClaimRelationResolution(
            ClaimRelation.UNCERTAIN,
            (),
            "canonical_value_missing",
            "A canonical predicate matched, but no comparable value was supplied.",
            {**diagnostics, "relation_stage": "value_missing"},
        )

    differing = [
        item
        for item, view in comparable
        if view.value_key is not None and view.value_key != incoming.value_key
    ]
    if not differing:
        return None

    if (
        incoming.spec.cardinality == PredicateCardinality.MULTI
        or incoming.spec.update_policy == PredicateUpdatePolicy.APPEND
    ):
        return ClaimRelationResolution(
            ClaimRelation.COMPLEMENTARY,
            tuple(item.id for item in differing),
            "canonical_multi_value",
            "Different values in a multi-value predicate coexist without replacement.",
            {**diagnostics, "relation_stage": "schema_cardinality"},
        )

    if incoming.spec.update_policy not in {
        PredicateUpdatePolicy.REPLACE,
        PredicateUpdatePolicy.TRANSITION,
    }:
        return ClaimRelationResolution(
            ClaimRelation.UNCERTAIN,
            (),
            "canonical_update_policy_protected",
            "This canonical predicate does not authorize deterministic replacement.",
            {**diagnostics, "relation_stage": "update_policy_guard"},
        )

    if not all(
        assess_memory_type_compatibility(
            candidate,
            item,
        ).lifecycle_replace_allowed
        for item in differing
    ):
        return ClaimRelationResolution(
            ClaimRelation.CONTRADICTION,
            tuple(item.id for item in differing),
            "epistemic_value_conflict",
            "Belief and factual values may conflict but cannot replace one another.",
            {**diagnostics, "relation_stage": "type_compatibility_guard"},
        )

    confirmed = [item for item in differing if item.status == MemoryStatus.CONFIRMED]
    if incoming_status == MemoryStatus.CONFIRMED:
        return ClaimRelationResolution(
            ClaimRelation.UPDATE,
            tuple(item.id for item in differing),
            "canonical_single_value_update",
            "A confirmed value replaces older values in a single-value predicate.",
            {**diagnostics, "relation_stage": "schema_update_policy"},
        )
    return ClaimRelationResolution(
        ClaimRelation.CONTRADICTION,
        tuple(item.id for item in confirmed or differing),
        "canonical_single_value_conflict",
        "An unconfirmed value cannot replace an existing value in a single-value predicate.",
        {**diagnostics, "relation_stage": "authority_guard"},
    )


def _canonical_claim_view(memory: MemoryCandidate) -> _CanonicalClaimView | None:
    if memory.predicate_type != PredicateType.CANONICAL:
        return None
    normalized = normalize_predicate(
        kind=memory.kind,
        raw_predicate=memory.raw_predicate or memory.payload.get("predicate"),
        canonical_predicate=memory.canonical_predicate,
        custom_predicate=memory.custom_predicate,
        predicate_type=memory.predicate_type,
        payload=memory.payload,
    )
    predicate = memory.canonical_predicate or normalized.canonical_predicate
    spec = predicate_spec(predicate)
    if predicate is None or spec is None:
        return None
    dimension = memory.state_dimension or normalized.state_dimension or spec.state_dimension
    raw_value = memory.state_value or normalized.state_value
    if raw_value is None:
        for key in (
            "state_value",
            "current",
            "direction",
            "frequency",
            "value",
        ):
            value = memory.payload.get(key)
            if isinstance(value, str) and value.strip():
                raw_value = value.strip()
                break
    if memory.kind == MemoryKind.PREFERENCE:
        preference = memory.payload.get("preference")
        if isinstance(preference, str) and preference.strip():
            polarity = _preference_polarity(memory)
            raw_value = f"{normalize_preference_value(preference)}:{polarity}"
    value_key = _normalize_key(raw_value)
    event_family = memory.payload.get("event_family")
    if not isinstance(event_family, str) or not event_family.strip():
        event_family = None
    temporal_key = _temporal_key(memory)
    entity_key = _entity_key(memory)
    return _CanonicalClaimView(
        predicate=predicate,
        dimension=dimension,
        value_key=value_key,
        event_family=event_family.casefold() if event_family else None,
        temporal_key=temporal_key,
        entity_key=entity_key,
        spec=spec,
    )


def _subject_key(value: str) -> str:
    normalized = value.casefold().strip()
    return _SUBJECT_ALIASES.get(normalized, normalized)


def _entity_key(memory: MemoryCandidate) -> str | None:
    for key in ("entity", "target_entity", "subject_entity", "object_entity"):
        value = memory.payload.get(key)
        if isinstance(value, str) and value.strip():
            return _normalize_key(value)
    return None


def _entity_compatible(left: str | None, right: str | None) -> bool:
    return left is None or right is None or left == right


def _normalize_key(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).casefold().strip()
    return text or None


def _temporal_key(memory: MemoryCandidate) -> str | None:
    if memory.occurred_at is not None:
        return memory.occurred_at.isoformat()
    if memory.period_start is not None or memory.period_end is not None:
        start = memory.period_start.isoformat() if memory.period_start else ""
        end = memory.period_end.isoformat() if memory.period_end else ""
        return f"{start}/{end}"
    # ``point``/``interval`` alone do not identify an occurrence.  Treating
    # them as an identity would collapse two separate events that happen to
    # have the same temporal shape.
    return None


def _same_temporal_identity(left: _CanonicalClaimView, right: _CanonicalClaimView) -> bool:
    return left.temporal_key is not None and left.temporal_key == right.temporal_key


def _event_pattern_boundary(
    incoming: MemoryCandidate,
    target: MemoryItem,
) -> bool:
    return (
        {incoming.kind, target.kind}
        == {MemoryKind.INTERACTION_EVENT, MemoryKind.INTERACTION_PATTERN}
        or {
            memory_role(incoming),
            memory_role(target),
        }
        == {MemoryRole.RECENT_EVENT, MemoryRole.INTERACTION_PATTERN}
    )


def has_local_conflict(candidate: MemoryCandidate, active_memories: list[MemoryItem]) -> bool:
    compatible_memories = [
        item
        for item in active_memories
        if assess_memory_type_compatibility(
            candidate,
            item,
        ).semantic_relation_allowed
    ]
    state_identity = governed_state_identity(candidate)
    state_value = governed_state_value(candidate)
    if state_identity is not None and state_value is not None:
        same_dimension_conflict = any(
            item.subject.casefold() == candidate.subject.casefold()
            and governed_state_identity(item) == state_identity
            and governed_state_value(item) not in {None, state_value}
            for item in compatible_memories
        )
        if same_dimension_conflict:
            return True
    candidate_concept = memory_concept(candidate)
    if candidate_concept in {"contact_reduced", "contact_restored"}:
        opposite_concept = (
            "contact_restored"
            if candidate_concept == "contact_reduced"
            else "contact_reduced"
        )
        return any(
            item.subject.casefold() == candidate.subject.casefold()
            and memory_concept(item) == opposite_concept
            for item in compatible_memories
        )
    if candidate.kind == MemoryKind.PREFERENCE:
        resolution = _resolve_preference(
            candidate,
            compatible_memories,
            MemoryStatus.PROPOSED,
        )
        return resolution is not None and resolution.relation == ClaimRelation.CONTRADICTION
    return False


def _resolve_preference(
    candidate: MemoryCandidate,
    active_memories: list[MemoryItem],
    incoming_status: MemoryStatus,
) -> ClaimRelationResolution | None:
    value = candidate.payload.get("preference")
    if not isinstance(value, str):
        return None
    normalized_value = normalize_preference_value(value)
    polarity = _preference_polarity(candidate)
    # Preference relation policy is owned by the predicate registry.  Keep a
    # narrow legacy fallback for rows written before the registry metadata was
    # introduced, but never let a hard-coded list override an explicit
    # cardinality/update policy supplied by the registry.
    spec = predicate_spec(candidate.canonical_predicate)
    single_value_policy = False
    multi_value_policy = False
    policy_conflict = False
    policy_protected = False
    if spec is not None:
        if spec.update_policy in {
            PredicateUpdatePolicy.REPLACE,
            PredicateUpdatePolicy.TRANSITION,
        }:
            # An explicit replacement policy is sufficient when a legacy
            # registry entry omitted cardinality.  A MULTI+REPLACE entry is
            # internally inconsistent and must fail closed instead of
            # silently choosing one side.
            policy_conflict = spec.cardinality == PredicateCardinality.MULTI
            single_value_policy = not policy_conflict
        elif spec.update_policy in {
            PredicateUpdatePolicy.APPEND,
            PredicateUpdatePolicy.MERGE,
        }:
            # Likewise, APPEND/MERGE can govern an entry whose cardinality is
            # still unknown, but cannot authorize a SINGLE-value replacement.
            policy_conflict = spec.cardinality == PredicateCardinality.SINGLE
            multi_value_policy = not policy_conflict
        else:
            policy_protected = True
    else:
        # Legacy/custom canonical rows have no registry contract.  Preserve
        # the reviewed single-value preference dimensions that predate the
        # registry while remaining conservative for all other predicates.
        single_value_policy = candidate.canonical_predicate in {
            "preference.food.spiciness",
            "preference.environment.noise",
            "preference.budget.range",
        }
    related = [
        item
        for item in active_memories
        if item.kind == MemoryKind.PREFERENCE
        and item.subject.casefold() == candidate.subject.casefold()
        and item.canonical_predicate == candidate.canonical_predicate
    ]
    for item in related:
        item_value = item.payload.get("preference")
        if not isinstance(item_value, str):
            continue
        normalized_item_value = normalize_preference_value(item_value)
        item_polarity = _preference_polarity(item)
        if normalized_item_value == normalized_value:
            if item_polarity == polarity:
                return ClaimRelationResolution(
                    ClaimRelation.SAME,
                    (item.id,),
                    "normalized_preference",
                    "Equivalent preference wording maps to the same normalized value.",
                )
            # An explicit opposite polarity is a contradiction of the same
            # semantic value.  Confirmed evidence may transition it; proposed
            # evidence must remain fail-closed.  This behavior is independent
            # of single/multi cardinality because polarity itself is the
            # governed conflict dimension, but a protected/inconsistent
            # registry policy still fails closed.
            if policy_conflict or policy_protected:
                return ClaimRelationResolution(
                    ClaimRelation.UNCERTAIN,
                    (),
                    "preference_update_policy_protected",
                    "The preference registry does not authorize this polarity replacement.",
                    {
                        "canonical_predicate": candidate.canonical_predicate,
                        "cardinality": (
                            spec.cardinality.value if spec is not None else "unknown"
                        ),
                        "update_policy": (
                            spec.update_policy.value if spec is not None else "unknown"
                        ),
                    },
                )
            relation = (
                ClaimRelation.UPDATE
                if incoming_status == MemoryStatus.CONFIRMED
                else ClaimRelation.CONTRADICTION
            )
            return ClaimRelationResolution(
                relation,
                (item.id,),
                "preference_polarity_change",
                "The same preference value is described with an incompatible polarity.",
            )
        if _preference_values_are_hierarchical(normalized_item_value, normalized_value):
            return ClaimRelationResolution(
                ClaimRelation.COMPLEMENTARY,
                (item.id,),
                "preference_hierarchy",
                "The new preference is a compatible parent or child category.",
            )

    if related and single_value_policy:
        relation = (
            ClaimRelation.UPDATE
            if incoming_status == MemoryStatus.CONFIRMED
            else ClaimRelation.CONTRADICTION
        )
        return ClaimRelationResolution(
            relation,
            tuple(item.id for item in related),
            "single_value_preference_dimension",
            "This preference dimension is treated as a current single-value setting.",
            {
                "canonical_predicate": candidate.canonical_predicate,
                "cardinality": spec.cardinality.value if spec is not None else "unknown",
                "update_policy": spec.update_policy.value if spec is not None else "legacy",
            },
        )
    if related and (policy_conflict or policy_protected):
        return ClaimRelationResolution(
            ClaimRelation.UNCERTAIN,
            (),
            "preference_update_policy_protected",
            (
                "The preference registry contains inconsistent cardinality/update policy metadata."
                if policy_conflict
                else (
                    "The preference registry does not authorize deterministic "
                    "replacement for this dimension."
                )
            ),
            {
                "canonical_predicate": candidate.canonical_predicate,
                "cardinality": spec.cardinality.value if spec is not None else "unknown",
                "update_policy": spec.update_policy.value if spec is not None else "unknown",
            },
        )
    if related:
        # A registered multi-value/merge policy explicitly authorizes keeping
        # distinct values together.  Unknown legacy preferences retain the
        # historical complementary behavior below.
        return ClaimRelationResolution(
            ClaimRelation.COMPLEMENTARY,
            tuple(item.id for item in related[:5]),
            "compatible_preference_values",
            "Different values in this open preference dimension may coexist.",
            {
                "canonical_predicate": candidate.canonical_predicate,
                "cardinality": spec.cardinality.value if spec is not None else "unknown",
                "update_policy": spec.update_policy.value if spec is not None else "legacy",
                "registry_multi_value": multi_value_policy,
            },
        )
    return None


def _preference_polarity(candidate: MemoryCandidate) -> str:
    value = str(candidate.payload.get("preference_type") or "like").casefold()
    if value in {"avoid", "allergy", "restriction", "dislike", "forbid"}:
        return "negative"
    return "positive"


def _preference_values_are_hierarchical(first: str, second: str) -> bool:
    pairs = {
        frozenset({"日料", "寿司"}),
        frozenset({"中餐", "川菜"}),
        frozenset({"展览", "美术展"}),
    }
    return frozenset({first, second}) in pairs


def _keeper_rank(item: MemoryItem) -> tuple[int, int, float, object]:
    return (
        int(item.status == MemoryStatus.CONFIRMED),
        item.importance,
        item.confidence,
        item.updated_at,
    )

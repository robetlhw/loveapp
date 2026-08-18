from datetime import datetime

from loveapp.domain.memory import (
    ClaimRelation,
    ContextualMemoryUpdate,
    ContextualUpdateType,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    TemporalPrecision,
    TimeKind,
)


_ACTIVE_STATUSES = {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
_CONTACT_PREDICATES = {"interaction.contact_frequency", "contact.status"}


def is_contextual_update_target_compatible(item: MemoryItem) -> bool:
    """Return whether an active memory can receive a contact-pattern qualifier."""

    if item.status not in _ACTIVE_STATUSES:
        return False
    if item.canonical_predicate == "interaction.contact_frequency":
        return item.kind == MemoryKind.INTERACTION_PATTERN
    return (
        item.canonical_predicate == "contact.status"
        and item.kind == MemoryKind.RELATIONSHIP_STATE
        and item.state_value in {"reduced", "unavailable"}
    )


def apply_contextual_memory_update(
    item: MemoryItem,
    update: ContextualMemoryUpdate,
    *,
    updated_at: datetime,
) -> MemoryItem:
    """Apply a typed qualifier update without changing the memory identity."""

    if item.id != update.target_memory_id:
        raise ValueError("contextual update target does not match the memory being patched")
    if item.canonical_predicate != update.target_canonical_predicate:
        raise ValueError("contextual update predicate does not match the target memory")
    if not is_contextual_update_target_compatible(item):
        raise ValueError("contextual update target is not an active compatible contact memory")

    payload = dict(item.payload)
    payload["contextual_update_type"] = update.update_type.value
    payload["temporal_expression"] = update.temporal_expression
    payload["ongoing"] = True
    if update.update_type == ContextualUpdateType.DURATION:
        payload["duration_value"] = update.duration_value
        payload["duration_unit"] = update.duration_unit

    evidence_spans = list(dict.fromkeys([*item.evidence_spans, update.evidence_span]))[:8]
    return item.model_copy(
        update={
            "payload": payload,
            "evidence_spans": evidence_spans,
            "time_kind": TimeKind.INTERVAL,
            "period_end": update.reference_time,
            "temporal_precision": TemporalPrecision.APPROXIMATE,
            "updated_at": updated_at,
            "last_seen_at": updated_at,
            "claim_relation": ClaimRelation.UPDATE,
        }
    )

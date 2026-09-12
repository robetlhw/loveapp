"""Explicit write policy for semantic Event details.

This policy is intentionally small. It does not resolve targets and it does
not write to a Store; it maps a semantic draft plus a resolver result to one
bounded decision that a later application phase may execute.
"""

from __future__ import annotations

from loveapp.domain.memory_architecture_vnext import (
    EventDetail,
    EventDetailDraft,
    ResolutionStatus,
    TargetResolution,
    WriteDecision,
    WriteOperation,
)
from loveapp.domain.memory_semantic_units import EnrichmentDraft

CORE_ENRICHMENT_FIELDS: dict[str, frozenset[str]] = {
    "conflict": frozenset({"cause", "severity", "resolution", "outcome"}),
    "date": frozenset({"location", "activity_type", "outcome"}),
    "shared_activity": frozenset({"location", "activity_type", "outcome"}),
}

ATTACH_PREFERRED_FIELDS: dict[str, frozenset[str]] = {
    "conflict": frozenset({"emotion"}),
    "date": frozenset({"emotion"}),
    "shared_activity": frozenset({"emotion"}),
}


def decide_event_detail_write(
    draft: EventDetailDraft,
    resolution: TargetResolution,
    *,
    detail: EventDetail | None = None,
) -> WriteDecision:
    """Map a semantic EventDetail draft and target result to a safe action."""

    if resolution.status == ResolutionStatus.AMBIGUOUS:
        if resolution.clarification is None:
            raise ValueError("ambiguous resolution must include clarification metadata")
        return WriteDecision(
            operation=WriteOperation.CLARIFY,
            reason="semantic target is ambiguous",
            clarification=resolution.clarification,
        )
    if resolution.status == ResolutionStatus.UNRESOLVED:
        return WriteDecision(
            operation=WriteOperation.NOOP,
            reason=f"semantic target unresolved: {resolution.reason}",
        )
    target_id = resolution.target_memory_id
    if not target_id:
        return WriteDecision(operation=WriteOperation.NOOP, reason="resolved target is missing")

    event_type = (draft.event_type_constraint or "").strip().casefold()
    field = draft.detail_type.casefold()
    if field in CORE_ENRICHMENT_FIELDS.get(event_type, frozenset()):
        return WriteDecision(
            operation=WriteOperation.ENRICH_CORE,
            target_memory_id=target_id,
            reason=f"{event_type}.{field} is an explicitly governed core field",
        )
    if field in ATTACH_PREFERRED_FIELDS.get(event_type, frozenset()) or field not in {
        field_name
        for names in CORE_ENRICHMENT_FIELDS.values()
        for field_name in names
    }:
        if detail is None:
            return WriteDecision(
                operation=WriteOperation.NOOP,
                target_memory_id=target_id,
                reason="attached detail object is required before persistence",
            )
        if detail.parent_event_id != target_id:
            return WriteDecision(
                operation=WriteOperation.NOOP,
                target_memory_id=target_id,
                reason="event detail parent does not match resolved target",
            )
        return WriteDecision(
            operation=WriteOperation.ATTACH_DETAIL,
            target_memory_id=target_id,
            detail=detail,
            reason=f"{event_type}.{field} is a lightweight event detail",
        )
    return WriteDecision(
        operation=WriteOperation.NOOP,
        target_memory_id=target_id,
        reason=f"{event_type}.{field} is outside the governed write policy",
    )


def decide_enrichment_write(
    draft: EnrichmentDraft,
    resolution: TargetResolution,
) -> WriteDecision:
    """Compatibility adapter for the existing Stage2 enrichment draft."""

    detail = EventDetailDraft(
        unit_id=draft.unit_id,
        event_type_constraint=str(draft.target_semantic_hint.get("event_type") or "") or None,
        detail_type=draft.attribute_name,
        value=draft.value,
        evidence_span=draft.evidence_span,
        subject_hint=draft.subject_hint,
        temporal_hint=draft.temporal_hint,
        perspective=draft.perspective,
        epistemic_status=draft.epistemic_status,
        confidence=draft.confidence,
    )
    return decide_event_detail_write(detail, resolution)


__all__ = [
    "ATTACH_PREFERRED_FIELDS",
    "CORE_ENRICHMENT_FIELDS",
    "decide_enrichment_write",
    "decide_event_detail_write",
]

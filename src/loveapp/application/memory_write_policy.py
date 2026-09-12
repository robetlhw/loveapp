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
from loveapp.domain.memory_dimensions import normalize_interaction_event_type
from loveapp.domain.memory_semantic_units import EnrichmentDraft
from loveapp.ports.observability import TraceRecorder

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
    trace: TraceRecorder | None = None,
) -> WriteDecision:
    """Map a semantic EventDetail draft and target result to a safe action."""

    if resolution.status == ResolutionStatus.AMBIGUOUS:
        if resolution.clarification is None:
            raise ValueError("ambiguous resolution must include clarification metadata")
        decision = WriteDecision(
            operation=WriteOperation.CLARIFY,
            reason="semantic target is ambiguous",
            clarification=resolution.clarification,
        )
        _record_policy_trace(trace, decision)
        return decision
    if resolution.status == ResolutionStatus.UNRESOLVED:
        decision = WriteDecision(
            operation=WriteOperation.NOOP,
            reason=f"semantic target unresolved: {resolution.reason}",
        )
        _record_policy_trace(trace, decision)
        return decision
    target_id = resolution.target_memory_id
    if not target_id:
        decision = WriteDecision(operation=WriteOperation.NOOP, reason="resolved target is missing")
        _record_policy_trace(trace, decision)
        return decision

    raw_event_type = (draft.event_type_constraint or "").strip().casefold()
    event_type = normalize_interaction_event_type(raw_event_type) or raw_event_type
    field = draft.detail_type.casefold()
    if event_type not in set(CORE_ENRICHMENT_FIELDS) | set(ATTACH_PREFERRED_FIELDS):
        decision = WriteDecision(
            operation=WriteOperation.NOOP,
            target_memory_id=target_id,
            reason="event detail event type is outside the governed policy",
        )
        _record_policy_trace(trace, decision)
        return decision
    if field in CORE_ENRICHMENT_FIELDS.get(event_type, frozenset()):
        decision = WriteDecision(
            operation=WriteOperation.ENRICH_CORE,
            target_memory_id=target_id,
            reason=f"{event_type}.{field} is an explicitly governed core field",
        )
        _record_policy_trace(trace, decision)
        return decision
    if field in ATTACH_PREFERRED_FIELDS.get(event_type, frozenset()) or field not in {
        field_name for names in CORE_ENRICHMENT_FIELDS.values() for field_name in names
    }:
        if detail is None:
            decision = WriteDecision(
                operation=WriteOperation.NOOP,
                target_memory_id=target_id,
                reason="attached detail object is required before persistence",
            )
            _record_policy_trace(trace, decision)
            return decision
        if detail.parent_event_id != target_id:
            decision = WriteDecision(
                operation=WriteOperation.NOOP,
                target_memory_id=target_id,
                reason="event detail parent does not match resolved target",
            )
            _record_policy_trace(trace, decision)
            return decision
        decision = WriteDecision(
            operation=WriteOperation.ATTACH_DETAIL,
            target_memory_id=target_id,
            detail=detail,
            reason=f"{event_type}.{field} is a lightweight event detail",
        )
        _record_policy_trace(trace, decision)
        return decision
    decision = WriteDecision(
        operation=WriteOperation.NOOP,
        target_memory_id=target_id,
        reason=f"{event_type}.{field} is outside the governed write policy",
    )
    _record_policy_trace(trace, decision)
    return decision


def _record_policy_trace(trace: TraceRecorder | None, decision: WriteDecision) -> None:
    if trace is not None:
        with trace.measure("memory_write_policy") as details:
            details.update(decision.as_trace())


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

"""Precision-first target resolution for semantic Event enrichment drafts."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from loveapp.domain.memory import (
    EpistemicStatus,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
)
from loveapp.domain.memory_dimensions import (
    infer_interaction_event_type,
    normalize_interaction_event_cause,
    normalize_interaction_event_type,
)
from loveapp.domain.memory_event_enrichment import (
    CUSTOM_EVENT_ATTRIBUTE_COMPATIBILITY,
    EVENT_ENRICHMENT_FIELD_COMPATIBILITY,
    CustomEventAttribute,
    EventEnrichmentField,
    GenericEventEnrichment,
)
from loveapp.domain.memory_semantic_units import AttributeNamespace, EnrichmentDraft
from loveapp.domain.runtime_context import PendingMemoryContext

_CANONICAL_FIELDS = {
    field.value: field
    for fields in EVENT_ENRICHMENT_FIELD_COMPATIBILITY.values()
    for field in fields
}
_BOUNDED_OCCURRENCE_MARKER = re.compile(
    r"(?:今天|今日|昨天|昨晚|今早|刚才|刚刚|这次|第二天|前几天|又|再次|重新)"
    r"|\b(?:today|yesterday|again|this\s+time)\b",
    re.I,
)
_EVENT_OCCURRENCE_PATTERN = re.compile(
    r"(?:吵(?:了|起)?(?:一|了)?架|争吵|争执|发生.{0,6}(?:矛盾|冲突)|"
    r"约会|一起.{0,10}(?:吃饭|看电影|散步|旅行|逛|玩|见面)|"
    r"(?:送|给).{0,10}(?:礼物|花|书))"
    r"|\b(?:argu|fight|quarrel|conflict|date|met|went)\w*\b",
    re.I,
)


@dataclass(frozen=True)
class EventEnrichmentResolution:
    detected: bool = True
    draft: EnrichmentDraft | None = None
    target: MemoryItem | None = None
    antecedent_message_id: str | None = None
    field: EventEnrichmentField | None = None
    reason: str = "unresolved_event_enrichment"
    semantic_candidate_ids: tuple[str, ...] = ()
    compatible_candidate_ids: tuple[str, ...] = ()
    rejected_candidates: tuple[tuple[str, str], ...] = ()

    @property
    def resolved(self) -> bool:
        return (
            self.draft is not None
            and self.target is not None
            and self.antecedent_message_id is not None
            and self.field is not None
        )

    def to_enrichment(
        self,
        *,
        source_message_id: str,
        created_at: datetime,
    ) -> GenericEventEnrichment:
        if not self.resolved or self.draft is None or self.target is None:
            raise ValueError("cannot build an enrichment from an unresolved target")
        value: object = self.draft.value
        if self.field == EventEnrichmentField.CUSTOM:
            value = CustomEventAttribute(
                attribute=self.draft.attribute_name,
                value=self.draft.value,
                evidence_span=self.draft.evidence_span,
                source_message_id=source_message_id,
                confidence=self.draft.confidence,
                created_at=created_at,
            )
        return GenericEventEnrichment(
            target_memory_id=self.target.id,
            antecedent_message_id=self.antecedent_message_id or "",
            evidence_span=self.draft.evidence_span,
            field=self.field,
            value=value,
            confidence=self.draft.confidence,
            reason=self.reason,
        )


def resolve_event_enrichment(
    draft: EnrichmentDraft,
    *,
    current_text: str,
    conversation_history: Iterable,
    existing_memories: Iterable[MemoryItem],
    user_id: str | None = None,
    relationship_id: str | None = None,
    pending_memory_context: PendingMemoryContext | None = None,
    min_confidence: float = 0.65,
) -> EventEnrichmentResolution:
    """Resolve only a uniquely source/context-linked active Event target.

    Semantic cardinality is evaluated before mutation compatibility. This is
    intentional: compatibility filtering must never turn an ambiguous
    reference into a falsely unique write target.
    """

    base = {"draft": draft}
    if draft.target_kind != MemoryKind.INTERACTION_EVENT:
        return EventEnrichmentResolution(**base, reason="unsupported_enrichment_target_kind")
    if draft.evidence_span not in current_text:
        return EventEnrichmentResolution(**base, reason="enrichment_evidence_not_in_source")
    if draft.confidence < min_confidence:
        return EventEnrichmentResolution(**base, reason="enrichment_confidence_below_threshold")
    if (
        draft.perspective != MemoryPerspective.USER_REPORTED
        or draft.epistemic_status != EpistemicStatus.CONFIRMED
    ):
        return EventEnrichmentResolution(**base, reason="enrichment_evidence_not_confirmed")
    if _is_new_bounded_occurrence(current_text):
        return EventEnrichmentResolution(**base, reason="new_event_occurrence_requires_create")

    field, field_error = _resolve_field(draft)
    if field is None:
        return EventEnrichmentResolution(**base, reason=field_error)
    pending_error = _pending_context_error(draft, pending_memory_context)
    if pending_error is not None:
        return EventEnrichmentResolution(**base, field=field, reason=pending_error)

    antecedent = _latest_user_message(conversation_history)
    if antecedent is None:
        return EventEnrichmentResolution(
            **base,
            field=field,
            reason="no_event_antecedent_message",
        )
    antecedent_id = str(getattr(antecedent, "id", "") or "")
    scoped = [
        item
        for item in existing_memories
        if (user_id is None or item.user_id == user_id)
        and (relationship_id is None or item.relationship_id == relationship_id)
    ]
    context_linked = [
        item
        for item in scoped
        if item.kind == MemoryKind.INTERACTION_EVENT
        and _is_context_linked(item, antecedent_id)
    ]
    pending_linked = bool(
        pending_memory_context is not None
        and pending_memory_context.memory_relevant
        and (
            getattr(pending_memory_context, "target_field", None)
            or pending_memory_context.expected_slot
        )
        == draft.attribute_name
    )
    semantic_candidates = (
        context_linked
        if context_linked
        else [item for item in scoped if item.kind == MemoryKind.INTERACTION_EVENT]
        if pending_linked
        else []
    )
    semantic_ids = tuple(item.id for item in semantic_candidates[:8])
    if not semantic_candidates:
        return EventEnrichmentResolution(
            **base,
            antecedent_message_id=antecedent_id,
            field=field,
            reason="no_source_or_context_linked_event",
        )
    if len(semantic_candidates) != 1:
        return EventEnrichmentResolution(
            **base,
            antecedent_message_id=antecedent_id,
            field=field,
            reason="ambiguous_semantic_event_antecedent",
            semantic_candidate_ids=semantic_ids,
            rejected_candidates=tuple(
                (item.id, "ambiguous_semantic_event_antecedent")
                for item in semantic_candidates[:8]
            ),
        )

    target = semantic_candidates[0]
    rejection = _target_rejection_reason(
        draft,
        target,
        field=field,
        pending_memory_context=pending_memory_context,
    )
    if rejection is not None:
        return EventEnrichmentResolution(
            **base,
            antecedent_message_id=target.source_message_id or antecedent_id,
            field=field,
            reason=rejection,
            semantic_candidate_ids=semantic_ids,
            rejected_candidates=((target.id, rejection),),
        )
    return EventEnrichmentResolution(
        **base,
        target=target,
        antecedent_message_id=target.source_message_id or antecedent_id,
        field=field,
        reason=(
            "unique_source_linked_event"
            if target.source_message_id == antecedent_id
            else "unique_context_linked_event"
        ),
        semantic_candidate_ids=semantic_ids,
        compatible_candidate_ids=(target.id,),
    )


def _resolve_field(
    draft: EnrichmentDraft,
) -> tuple[EventEnrichmentField | None, str]:
    if draft.attribute_namespace == AttributeNamespace.CANONICAL:
        field = _CANONICAL_FIELDS.get(draft.attribute_name)
        return (
            (field, "")
            if field is not None
            else (None, "unsupported_canonical_event_field")
        )
    if draft.attribute_name not in CUSTOM_EVENT_ATTRIBUTE_COMPATIBILITY:
        return None, "unsupported_custom_event_attribute"
    if isinstance(draft.value, (dict, list)):
        return None, "unsupported_custom_event_attribute_value"
    return EventEnrichmentField.CUSTOM, ""


def _pending_context_error(
    draft: EnrichmentDraft,
    pending: PendingMemoryContext | None,
) -> str | None:
    if pending is None or not pending.memory_relevant:
        return None
    target_kind = getattr(pending, "target_kind", None)
    if target_kind not in {None, "", MemoryKind.INTERACTION_EVENT.value}:
        return "pending_slot_target_kind_mismatch"
    expected_field = getattr(pending, "target_field", None) or pending.expected_slot
    if expected_field and expected_field != draft.attribute_name:
        return "pending_slot_field_mismatch"
    pending_event_type = getattr(pending, "event_type", None) or pending.topic
    hinted_event_type = _draft_event_type(draft)
    if pending_event_type and hinted_event_type and pending_event_type != hinted_event_type:
        return "pending_slot_event_type_mismatch"
    return None


def _target_rejection_reason(
    draft: EnrichmentDraft,
    target: MemoryItem,
    *,
    field: EventEnrichmentField,
    pending_memory_context: PendingMemoryContext | None,
) -> str | None:
    if target.status not in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}:
        return "event_target_not_active"
    target_event_type = _memory_event_type(target)
    if target_event_type not in EVENT_ENRICHMENT_FIELD_COMPATIBILITY:
        return "unsupported_event_type"
    hinted_event_type = _draft_event_type(draft)
    pending_event_type = (
        getattr(pending_memory_context, "event_type", None)
        if pending_memory_context is not None
        else None
    )
    expected_event_type = hinted_event_type or pending_event_type
    if expected_event_type and target_event_type != expected_event_type:
        return "event_type_mismatch"
    target_subject = draft.subject_hint or draft.target_semantic_hint.get("subject")
    if isinstance(target_subject, str) and _normalize_subject(target_subject) != _normalize_subject(
        target.subject
    ):
        return "event_subject_mismatch"
    if field == EventEnrichmentField.CUSTOM:
        allowed_types = CUSTOM_EVENT_ATTRIBUTE_COMPATIBILITY[draft.attribute_name]
        if target_event_type not in allowed_types:
            return "custom_attribute_event_type_mismatch"
        attributes = target.payload.get("custom_attributes")
        if isinstance(attributes, list):
            existing = next(
                (
                    item
                    for item in attributes
                    if isinstance(item, dict)
                    and item.get("attribute") == draft.attribute_name
                ),
                None,
            )
            if existing is not None and existing.get("value") != draft.value:
                return "existing_custom_attribute_conflict"
        return None
    if field not in EVENT_ENRICHMENT_FIELD_COMPATIBILITY[target_event_type]:
        return "event_field_incompatible"
    incoming_value: object = draft.value
    existing_value = target.payload.get(field.value)
    if field == EventEnrichmentField.CAUSE:
        incoming_value = normalize_interaction_event_cause(incoming_value)
        existing_value = normalize_interaction_event_cause(existing_value)
    if existing_value is not None and existing_value != incoming_value:
        return "existing_event_field_conflict"
    try:
        GenericEventEnrichment(
            target_memory_id=target.id,
            antecedent_message_id=target.source_message_id or "missing",
            evidence_span=draft.evidence_span,
            field=field,
            value=draft.value,
            confidence=draft.confidence,
            reason="event_enrichment_value_validation",
        )
    except ValueError:
        return "invalid_event_enrichment_value"
    return None


def _draft_event_type(draft: EnrichmentDraft) -> str | None:
    raw = draft.target_semantic_hint.get("event_type")
    return normalize_interaction_event_type(raw)


def _memory_event_type(item: MemoryItem) -> str | None:
    event_type = normalize_interaction_event_type(item.payload.get("event_type"))
    if event_type is not None:
        return event_type
    return infer_interaction_event_type(
        item.payload,
        evidence_text=" ".join([item.original_text, *item.evidence_spans]),
    )


def _latest_user_message(history: Iterable) -> object | None:
    messages = [
        message
        for message in history
        if getattr(getattr(message, "role", None), "value", None) == "user"
        and str(getattr(message, "content", "")).strip()
    ]
    return messages[-1] if messages else None


def _is_context_linked(item: MemoryItem, antecedent_message_id: str) -> bool:
    if not antecedent_message_id:
        return False
    if item.source_message_id == antecedent_message_id:
        return True
    history = item.payload.get("enrichment_history")
    return isinstance(history, list) and any(
        isinstance(record, dict)
        and record.get("source_message_id") == antecedent_message_id
        for record in history
    )


def _is_new_bounded_occurrence(text: str) -> bool:
    return bool(
        _BOUNDED_OCCURRENCE_MARKER.search(text)
        and _EVENT_OCCURRENCE_PATTERN.search(text)
    )


def _normalize_subject(value: str) -> str:
    normalized = value.casefold().strip()
    if normalized in {"relationship", "relation", "双方", "我们", "user_and_partner"}:
        return "relationship"
    if normalized in {"对方", "她", "他", "partner"}:
        return "partner"
    if normalized in {"我", "用户", "user"}:
        return "user"
    return normalized


__all__ = [
    "EventEnrichmentResolution",
    "resolve_event_enrichment",
]

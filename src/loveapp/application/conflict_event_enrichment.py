from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from loveapp.domain.memory import MemoryCandidate, MemoryItem, MemoryKind, MemoryStatus
from loveapp.domain.memory_dimensions import (
    is_conflict_interaction_event,
    normalize_interaction_event_cause,
)
from loveapp.domain.memory_event_enrichment import ConflictEventEnrichment
from loveapp.domain.runtime_context import PendingMemoryContext

_CAUSE_FOLLOW_UP_PATTERN = re.compile(
    r"(?:主要是因为|主要因为|原因是|是因为|因为|主要是|基本都是|大多是)"
)
_CURRENT_CONFLICT_EVENT_PATTERN = re.compile(
    r"(?:吵(?:了|过|起)?(?:一|了)?架|争吵|争执|发生.{0,6}(?:矛盾|冲突)|"
    r"闹了矛盾|进入冷战|开始冷战)"
)
@dataclass(frozen=True)
class ConflictEventEnrichmentResolution:
    detected: bool = False
    target: MemoryItem | None = None
    antecedent_message_id: str | None = None
    evidence_span: str | None = None
    cause_category: str | None = None
    cause_description: str | None = None
    reason: str = "not_conflict_event_enrichment"
    semantic_candidate_ids: tuple[str, ...] = ()
    rejected_candidates: tuple[tuple[str, str], ...] = ()

    @property
    def resolved(self) -> bool:
        return (
            self.target is not None
            and self.antecedent_message_id is not None
            and self.evidence_span is not None
            and (self.cause_category is not None or self.cause_description is not None)
        )

    def to_enrichment(self, *, confidence: float) -> ConflictEventEnrichment:
        if not self.resolved or self.target is None or self.antecedent_message_id is None:
            raise ValueError("cannot build an enrichment from an unresolved target")
        return ConflictEventEnrichment(
            target_memory_id=self.target.id,
            antecedent_message_id=self.antecedent_message_id,
            evidence_span=self.evidence_span or "",
            cause_category=self.cause_category,
            cause_description=self.cause_description,
            confidence=confidence,
            reason=self.reason,
        )


def resolve_conflict_event_enrichment(
    candidate: MemoryCandidate,
    *,
    current_text: str,
    conversation_history: Iterable,
    existing_memories: Iterable[MemoryItem],
    pending_memory_context: PendingMemoryContext | None = None,
) -> ConflictEventEnrichmentResolution:
    """Resolve a cause-only follow-up to one source-linked conflict Event.

    The resolver does not fall back to the most recent database row.  The
    latest prior user turn must be the source message of exactly one active
    conflict Event, otherwise enrichment fails closed.
    """

    if candidate.kind != MemoryKind.INTERACTION_EVENT:
        return ConflictEventEnrichmentResolution()
    normalized_cause = normalize_interaction_event_cause(candidate.payload.get("cause"))
    if not isinstance(normalized_cause, dict) or not normalized_cause:
        return ConflictEventEnrichmentResolution()
    pending_conflict_cause = bool(
        pending_memory_context is not None
        and pending_memory_context.memory_relevant
        and pending_memory_context.expected_slot == "cause"
        and pending_memory_context.topic == "conflict"
    )
    candidate_evidence = " ".join(
        [current_text, candidate.original_text, *candidate.evidence_spans]
    )
    candidate_is_conflict = is_conflict_interaction_event(
        candidate.payload,
        evidence_text=candidate_evidence,
    )
    cause_follow_up = _CAUSE_FOLLOW_UP_PATTERN.search(current_text) is not None
    if not candidate_is_conflict and not pending_conflict_cause:
        return ConflictEventEnrichmentResolution()
    if not pending_conflict_cause and not cause_follow_up:
        return ConflictEventEnrichmentResolution()
    if _CURRENT_CONFLICT_EVENT_PATTERN.search(current_text) is not None:
        # A turn that reports another bounded conflict is a new Event carrying
        # its own cause, not an enrichment of the previous Event.
        return ConflictEventEnrichmentResolution()

    category = normalized_cause.get("category")
    description = normalized_cause.get("description")
    category = category if isinstance(category, str) and category.strip() else None
    description = (
        description if isinstance(description, str) and description.strip() else None
    )
    user_history = [
        message
        for message in conversation_history
        if getattr(getattr(message, "role", None), "value", None) == "user"
        and getattr(message, "content", "").strip()
    ]
    if not user_history:
        return ConflictEventEnrichmentResolution(
            detected=True,
            evidence_span=current_text,
            cause_category=category,
            cause_description=description,
            reason="no_conflict_event_antecedent_message",
        )
    antecedent = user_history[-1]
    semantic_candidates = [
        item
        for item in existing_memories
        if item.status in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
        and item.kind == MemoryKind.INTERACTION_EVENT
        and item.source_message_id == antecedent.id
        and is_conflict_interaction_event(
            item.payload,
            evidence_text=" ".join([item.original_text, *item.evidence_spans]),
        )
    ]
    semantic_ids = tuple(item.id for item in semantic_candidates[:8])
    if not semantic_candidates:
        return ConflictEventEnrichmentResolution(
            detected=True,
            antecedent_message_id=antecedent.id,
            evidence_span=current_text,
            cause_category=category,
            cause_description=description,
            reason="no_source_linked_conflict_event",
        )
    if len(semantic_candidates) > 1:
        return ConflictEventEnrichmentResolution(
            detected=True,
            antecedent_message_id=antecedent.id,
            evidence_span=current_text,
            cause_category=category,
            cause_description=description,
            reason="ambiguous_source_linked_conflict_event",
            semantic_candidate_ids=semantic_ids,
            rejected_candidates=tuple(
                (item.id, "ambiguous_source_linked_conflict_event")
                for item in semantic_candidates[:8]
            ),
        )

    target = semantic_candidates[0]
    if _normalize_subject(candidate.subject) != _normalize_subject(target.subject):
        return ConflictEventEnrichmentResolution(
            detected=True,
            antecedent_message_id=antecedent.id,
            evidence_span=current_text,
            cause_category=category,
            cause_description=description,
            reason="conflict_event_subject_mismatch",
            semantic_candidate_ids=semantic_ids,
            rejected_candidates=((target.id, "subject_mismatch"),),
        )
    existing_cause = normalize_interaction_event_cause(target.payload.get("cause"))
    existing_category = (
        existing_cause.get("category") if isinstance(existing_cause, dict) else None
    )
    if category is not None and existing_category not in {None, category}:
        return ConflictEventEnrichmentResolution(
            detected=True,
            antecedent_message_id=antecedent.id,
            evidence_span=current_text,
            cause_category=category,
            cause_description=description,
            reason="conflict_event_cause_already_present",
            semantic_candidate_ids=semantic_ids,
            rejected_candidates=((target.id, "existing_cause_conflict"),),
        )
    return ConflictEventEnrichmentResolution(
        detected=True,
        target=target,
        antecedent_message_id=antecedent.id,
        evidence_span=current_text,
        cause_category=category,
        cause_description=description,
        reason="unique_source_linked_conflict_event",
        semantic_candidate_ids=semantic_ids,
    )


def _normalize_subject(value: str) -> str:
    normalized = value.casefold().strip()
    return "relationship" if normalized in {"relationship", "relation"} else normalized


__all__ = [
    "ConflictEventEnrichmentResolution",
    "resolve_conflict_event_enrichment",
]

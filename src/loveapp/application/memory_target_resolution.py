"""Candidate generation and target resolution for the VNext memory path.

The existing Event enrichment resolver remains the compatibility path for old
write batches.  This module exposes the narrower VNext contracts so callers
can adopt multi-channel candidate generation and safe resolution incrementally.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any

from loveapp.domain.memory import MemoryItem, MemoryKind, MemoryStatus
from loveapp.domain.memory_architecture_vnext import (
    Candidate,
    CandidateGenerationResult,
    CandidateSource,
    ClarificationRequired,
    EventDetailDraft,
    ResolutionStatus,
    TargetResolution,
)
from loveapp.domain.memory_semantic_units import EnrichmentDraft
from loveapp.domain.runtime_context import PendingMemoryContext, PendingQuestion


class CandidateGenerator:
    """Build a provenance-bearing candidate set from bounded channels."""

    def generate(
        self,
        *,
        draft: EventDetailDraft | EnrichmentDraft,
        current_text: str,
        existing_memories: Iterable[MemoryItem],
        conversation_history: Iterable[Any] = (),
        retrieved_candidates: Iterable[Any] = (),
        pending_question: PendingQuestion | None = None,
        pending_memory_context: PendingMemoryContext | None = None,
        user_id: str | None = None,
        relationship_id: str | None = None,
    ) -> CandidateGenerationResult:
        memories = [
            item
            for item in existing_memories
            if isinstance(item, MemoryItem)
            and item.kind == MemoryKind.INTERACTION_EVENT
            and item.status in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
            and (user_id is None or item.user_id == user_id)
            and (relationship_id is None or item.relationship_id == relationship_id)
        ]
        by_id = {item.id: item for item in memories}
        candidates: dict[str, Candidate] = {}
        channels: set[CandidateSource] = set()

        pending = pending_question
        if pending is None and pending_memory_context is not None:
            pending = pending_memory_context.to_pending_question()

        event_type_hint = _event_type_hint(draft)
        subject_hint = _subject_hint(draft)
        field_hint = _field_hint(draft)

        def add(
            item: MemoryItem,
            source: CandidateSource,
            *,
            signals: dict[str, bool | float | str] | None = None,
            score: float | None = None,
        ) -> None:
            channels.add(source)
            existing = candidates.get(item.id)
            if existing is None:
                candidates[item.id] = Candidate(
                    memory_id=item.id,
                    memory=item,
                    sources=[source],
                    signals=dict(signals or {}),
                    score=score,
                )
                return
            if source not in existing.sources:
                existing.sources.append(source)
            if signals:
                existing.signals.update(signals)
            if score is not None and (existing.score is None or score > existing.score):
                existing.score = score

        # A binding created by application code is the highest-confidence
        # channel, but other semantic candidates are retained for diagnostics.
        if (
            pending is not None
            and pending.is_open
            and pending.target_memory_id
            and pending.target_memory_id in by_id
            and _pending_field_matches(pending, field_hint)
        ):
            add(
                by_id[pending.target_memory_id],
                CandidateSource.PENDING_QUESTION,
                signals={"pending_binding_match": True},
            )

        latest_user = _latest_user_message(conversation_history)
        latest_id = str(getattr(latest_user, "id", "") or "")
        if latest_id:
            for item in memories:
                if _is_context_linked(item, latest_id):
                    add(
                        item,
                        CandidateSource.RECENT_EVENT_CONTEXT,
                        signals={"recent_source_match": True},
                    )

        # Explicit temporal/reference cues are deterministic narrowing signals,
        # not permission to select a target by recency alone.
        temporal_hint = _temporal_hint(draft, current_text)
        if temporal_hint:
            anchor = getattr(latest_user, "created_at", None)
            if not isinstance(anchor, datetime):
                anchor = datetime.now()
            for item in memories:
                score = _temporal_score(item, temporal_hint, anchor)
                if score >= 0.9:
                    add(
                        item,
                        CandidateSource.EXPLICIT_REFERENCE,
                        signals={"temporal_match": True},
                        score=score,
                    )

        # Structured lookup is deliberately broad enough to preserve semantic
        # ambiguity. It never applies field compatibility as a filter.
        structured = [
            item
            for item in memories
            if _event_type_matches(item, event_type_hint)
            and _subject_matches(item, subject_hint)
        ]
        if structured and (event_type_hint or subject_hint or pending is not None):
            for item in structured:
                add(
                    item,
                    CandidateSource.STRUCTURED_LOOKUP,
                    signals={
                        "event_type_match": bool(event_type_hint),
                        "subject_match": bool(subject_hint),
                    },
                )

        # Existing retrieval is a recall channel. Its cardinality is never a
        # uniqueness proof; unresolved targets remain unresolved below.
        for retrieved in retrieved_candidates:
            item = getattr(retrieved, "item", retrieved)
            if not isinstance(item, MemoryItem) or item.id not in by_id:
                continue
            score_obj = getattr(retrieved, "score", None)
            score = getattr(score_obj, "total", None)
            add(
                by_id[item.id],
                CandidateSource.VECTOR_FALLBACK,
                signals={"retrieval_match": True},
                score=float(score) if isinstance(score, (int, float)) else None,
            )

        ordered = sorted(
            candidates.values(),
            key=lambda candidate: (
                0 if CandidateSource.PENDING_QUESTION in candidate.sources else 1,
                0 if CandidateSource.EXPLICIT_REFERENCE in candidate.sources else 1,
                -(candidate.score or 0.0),
                candidate.memory.created_at,
                candidate.memory_id,
            ),
        )
        for index, candidate in enumerate(ordered, start=1):
            candidate.rank = index

        complete = bool(
            CandidateSource.PENDING_QUESTION in channels
            or CandidateSource.EXPLICIT_REFERENCE in channels
            or CandidateSource.RECENT_EVENT_CONTEXT in channels
            or CandidateSource.STRUCTURED_LOOKUP in channels
        )
        return CandidateGenerationResult(
            candidates=ordered[:20],
            channels_used=sorted(channels, key=lambda source: source.value),
            candidate_set_complete=complete,
            reason=(
                "deterministic_candidate_channels_available"
                if complete
                else "vector_fallback_only_or_no_candidate_channel"
            ),
        )


class TargetResolver:
    """Resolve a semantic candidate set without deciding a write operation."""

    def resolve(
        self,
        *,
        draft: EventDetailDraft | EnrichmentDraft,
        generated: CandidateGenerationResult,
        pending_question: PendingQuestion | None = None,
        pending_memory_context: PendingMemoryContext | None = None,
    ) -> TargetResolution:
        pending = pending_question
        if pending is None and pending_memory_context is not None:
            pending = pending_memory_context.to_pending_question()

        candidates = list(generated.candidates)
        candidate_ids = [candidate.memory_id for candidate in candidates]

        # A bound target is an explicit application-level reference. It is
        # allowed to win over other candidates, but only after active/event
        # identity checks; it does not turn an arbitrary retrieval hit into a
        # binding.
        if pending is not None and pending.is_open and pending.target_memory_id:
            bound = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.memory_id == pending.target_memory_id
                ),
                None,
            )
            if bound is not None and _compatible(draft, bound.memory):
                return TargetResolution(
                    status=ResolutionStatus.RESOLVED,
                    target_memory_id=bound.memory_id,
                    candidate_ids=candidate_ids,
                    resolution_evidence=["pending_question_binding"],
                    reason="application_bound_pending_target",
                )

        if not candidates:
            return TargetResolution(
                status=ResolutionStatus.UNRESOLVED,
                candidate_ids=[],
                reason="no_semantic_candidates",
            )
        if len(candidates) > 1:
            clarification = ClarificationRequired(
                reason="ambiguous_semantic_target",
                field=_field_hint(draft),
                candidate_memory_ids=candidate_ids,
                question="Which interaction event does this detail describe?",
            )
            return TargetResolution(
                status=ResolutionStatus.AMBIGUOUS,
                candidate_ids=candidate_ids,
                reason="ambiguous_semantic_target",
                clarification=clarification,
            )

        candidate = candidates[0]
        if not generated.candidate_set_complete:
            return TargetResolution(
                status=ResolutionStatus.UNRESOLVED,
                candidate_ids=candidate_ids,
                resolution_evidence=["vector_fallback_only"],
                reason="candidate_set_not_complete",
            )
        rejection = _compatibility_reason(draft, candidate.memory)
        if rejection is not None:
            return TargetResolution(
                status=ResolutionStatus.UNRESOLVED,
                candidate_ids=candidate_ids,
                resolution_evidence=[rejection],
                reason=rejection,
            )
        return TargetResolution(
            status=ResolutionStatus.RESOLVED,
            target_memory_id=candidate.memory_id,
            candidate_ids=candidate_ids,
            resolution_evidence=[
                source.value
                for source in candidate.sources
                if source != CandidateSource.VECTOR_FALLBACK
            ],
            reason="unique_semantic_candidate",
        )


def _event_type_hint(draft: EventDetailDraft | EnrichmentDraft) -> str | None:
    if isinstance(draft, EventDetailDraft):
        return _normalized_text(draft.event_type_constraint)
    raw = draft.target_semantic_hint.get("event_type")
    return _normalized_text(raw)


def _subject_hint(draft: EventDetailDraft | EnrichmentDraft) -> str | None:
    return _normalized_text(getattr(draft, "subject_hint", None))


def _field_hint(draft: EventDetailDraft | EnrichmentDraft) -> str | None:
    if isinstance(draft, EventDetailDraft):
        return draft.detail_type
    return draft.attribute_name


def _pending_field_matches(pending: PendingQuestion, field: str | None) -> bool:
    expected = pending.expected_field or pending.target_field
    return not expected or not field or expected == field


def _event_type_matches(item: MemoryItem, expected: str | None) -> bool:
    if not expected:
        return True
    actual = str(item.payload.get("event_type") or "").strip().casefold()
    return actual == expected.casefold()


def _subject_matches(item: MemoryItem, expected: str | None) -> bool:
    if not expected:
        return True
    return _normalize_subject(item.subject) == _normalize_subject(expected)


def _compatible(draft: EventDetailDraft | EnrichmentDraft, item: MemoryItem) -> bool:
    return _compatibility_reason(draft, item) is None


def _compatibility_reason(
    draft: EventDetailDraft | EnrichmentDraft,
    item: MemoryItem,
) -> str | None:
    expected_event_type = _event_type_hint(draft)
    if expected_event_type and not _event_type_matches(item, expected_event_type):
        return "event_type_mismatch"
    expected_subject = _subject_hint(draft)
    if expected_subject and not _subject_matches(item, expected_subject):
        return "event_subject_mismatch"
    return None


def _latest_user_message(history: Iterable[Any]) -> Any | None:
    messages = [
        message
        for message in history
        if str(getattr(getattr(message, "role", None), "value", "") or "").casefold() == "user"
        and str(getattr(message, "content", "")).strip()
    ]
    return messages[-1] if messages else None


def _is_context_linked(item: MemoryItem, message_id: str) -> bool:
    if not message_id:
        return False
    if item.source_message_id == message_id:
        return True
    history = item.payload.get("enrichment_history")
    return isinstance(history, list) and any(
        isinstance(record, dict) and record.get("source_message_id") == message_id
        for record in history
    )


def _temporal_hint(draft: EventDetailDraft | EnrichmentDraft, text: str) -> str | None:
    explicit = getattr(draft, "temporal_hint", None)
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    for pattern, value in (
        (r"yesterday|昨天", "yesterday"),
        (r"today|今天", "today"),
        (r"day before yesterday|前天", "day_before_yesterday"),
    ):
        if re.search(pattern, text, re.IGNORECASE):
            return value
    return None


def _temporal_score(item: MemoryItem, hint: str, anchor: datetime) -> float:
    target = item.occurred_at or item.period_end or item.period_start
    if not isinstance(target, datetime):
        return 0.5
    target_date = target.date()
    anchor_date = anchor.date()
    if hint == "yesterday":
        return 1.0 if target_date == anchor_date - timedelta(days=1) else 0.0
    if hint == "today":
        return 1.0 if target_date == anchor_date else 0.0
    if hint == "day_before_yesterday":
        return 1.0 if target_date == anchor_date - timedelta(days=2) else 0.0
    parsed = _parse_date(hint)
    return 1.0 if parsed is not None and target_date == parsed else 0.5


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip().replace("/", "-"))
    except ValueError:
        return None


def _normalize_subject(value: str) -> str:
    normalized = value.casefold().strip()
    return {
        "relationship": "relationship",
        "relation": "relationship",
        "user_and_partner": "relationship",
        "partner": "partner",
        "user": "user",
    }.get(normalized, normalized)


def _normalized_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text.casefold() if text else None


__all__ = ["CandidateGenerator", "TargetResolver"]

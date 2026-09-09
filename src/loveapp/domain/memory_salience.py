from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime

from loveapp.domain.memory import (
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    MemoryValence,
    RelationshipImpact,
)
from loveapp.domain.memory_dimensions import normalize_interaction_event_type

_FIRST_INTERACTION_PATTERN = re.compile(
    r"(?:第一次|初次|头一次|首次|第一次见面|初次约会)"
    r"|\b(?:first\s+(?:time|meeting|date)|for\s+the\s+first\s+time)\b",
    re.IGNORECASE,
)
_CONFLICT_EVENT_PATTERN = re.compile(
    r"(?:吵架|争吵|争执|矛盾|冲突|冷战|伤害|辱骂)"
    r"|\b(?:argu|fight|conflict|quarrel|insult)\w*\b",
    re.IGNORECASE,
)
_REPAIR_EVENT_PATTERN = re.compile(
    r"(?:和好|说开|解决|道歉|修复|恢复正常)"
    r"|\b(?:reconcil|repair|apolog|resolved|made\s+up)\w*\b",
    re.IGNORECASE,
)
_ATTENTION_PATTERN = re.compile(
    r"(?:这件事很重要|我一直记得|印象很深|反复想到|又提到|再次提到)"
    r"|\b(?:important|memorable|kept\s+thinking|mentioned\s+again)\b",
    re.IGNORECASE,
)
_RECURRENCE_EVENT_PATTERN = re.compile(
    r"(?:最近|这几天|又|再次|重新|经常|反复|又开始|再次出现)"
    r"|\b(?:again|repeatedly|often|recently)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EventSalienceAssessment:
    score: float
    importance: int
    reason: str
    novelty: float | None
    novelty_basis: str
    relationship_impact: float
    emotional_intensity: float
    user_attention: float

    @property
    def factors(self) -> dict[str, float | None]:
        return {
            "novelty": (
                round(self.novelty, 4) if self.novelty is not None else None
            ),
            "relationship_impact": round(self.relationship_impact, 4),
            "emotional_intensity": round(self.emotional_intensity, 4),
            "user_attention": round(self.user_attention, 4),
        }


def assess_event_salience(
    candidate: MemoryCandidate,
    existing_memories: list[MemoryItem],
) -> EventSalienceAssessment:
    """Assess the durable value of one Event from bounded, observable signals."""

    if candidate.kind != MemoryKind.INTERACTION_EVENT:
        return EventSalienceAssessment(
            0.0,
            candidate.importance,
            "not_interaction_event",
            0,
            "not_applicable",
            0,
            0,
            0,
        )

    evidence = _event_evidence(candidate)
    comparable = [
        item
        for item in existing_memories
        if item.kind == MemoryKind.INTERACTION_EVENT
        and item.status in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
        and item.subject.casefold() == candidate.subject.casefold()
        and _event_signature(item) == _event_signature(candidate)
    ]
    repeated_mention = any(_same_occurrence(candidate, item) for item in comparable)
    explicit_first = bool(_FIRST_INTERACTION_PATTERN.search(evidence))
    explicit_recurrence = bool(_RECURRENCE_EVENT_PATTERN.search(evidence))
    conflict = bool(_CONFLICT_EVENT_PATTERN.search(evidence))
    repair = bool(_REPAIR_EVENT_PATTERN.search(evidence))
    known_recurrence = _has_known_recurring_pattern(candidate, existing_memories)

    if explicit_first:
        novelty = 1.0
        novelty_basis = "explicit_first"
    elif explicit_recurrence:
        novelty = 0.2
        novelty_basis = "explicit_recurrence_signal"
    elif known_recurrence:
        novelty = 0.2
        novelty_basis = "known_recurring_pattern"
    elif comparable:
        novelty = 0.22
        novelty_basis = "comparable_event_history"
    else:
        # Absence from the Store is not evidence that an event is novel in the
        # real relationship.  Keep novelty unknown and use a neutral value only
        # inside the salience calculation.
        novelty = None
        novelty_basis = "unknown_relationship_history"
    impact = _relationship_impact_score(candidate)
    if conflict:
        impact = max(impact, 1.0)
    elif repair:
        impact = max(impact, 0.85)
    elif explicit_first:
        impact = max(impact, 0.75)

    emotional = _emotional_intensity_score(candidate)
    if conflict:
        emotional = max(emotional, 0.75)
    attention = 0.95 if repeated_mention else 0.9 if _ATTENTION_PATTERN.search(evidence) else 0.25
    if explicit_first:
        attention = max(attention, 0.65)

    effective_novelty = 0.5 if novelty is None else novelty
    score = (
        effective_novelty * 0.32
        + impact * 0.34
        + emotional * 0.19
        + attention * 0.15
    )
    if conflict:
        score = max(score, 0.82)
    elif repair:
        score = max(score, 0.72)
    elif explicit_first:
        score = max(score, 0.70)
    elif repeated_mention:
        score = max(score, 0.68)
    score = round(min(max(score, 0.0), 1.0), 4)

    reason = "ordinary_interaction"
    if conflict:
        reason = "relationship_conflict"
    elif repair:
        reason = "relationship_repair"
    elif explicit_first:
        reason = "first_interaction"
    elif repeated_mention:
        reason = "repeated_user_attention"
    elif candidate.relationship_impact in {
        RelationshipImpact.DAMAGING,
        RelationshipImpact.IMPROVING,
    }:
        reason = "relationship_impact"
    elif emotional >= 0.6:
        reason = "emotional_intensity"

    derived_importance = 5 if score >= 0.88 else 4 if score >= 0.65 else 3 if score >= 0.4 else 2
    return EventSalienceAssessment(
        score=score,
        importance=max(candidate.importance, derived_importance),
        reason=reason,
        novelty=novelty,
        novelty_basis=novelty_basis,
        relationship_impact=impact,
        emotional_intensity=emotional,
        user_attention=attention,
    )


def apply_event_salience(
    candidate: MemoryCandidate,
    existing_memories: list[MemoryItem],
) -> MemoryCandidate:
    """Attach deterministic salience without trusting it as write authorization."""

    if candidate.kind != MemoryKind.INTERACTION_EVENT:
        return candidate
    assessment = assess_event_salience(candidate, existing_memories)
    payload = dict(candidate.payload)
    if candidate.salience is not None and abs(candidate.salience - assessment.score) > 1e-9:
        payload["extractor_salience_hint"] = round(candidate.salience, 4)
    if candidate.novelty is not None and (
        assessment.novelty is None
        or abs(candidate.novelty - assessment.novelty) > 1e-9
    ):
        payload["extractor_novelty_hint"] = round(candidate.novelty, 4)
    if assessment.novelty is None:
        payload.pop("novelty", None)
    else:
        payload["novelty"] = assessment.novelty
    payload.update(
        {
            "salience": assessment.score,
            "importance_reason": assessment.reason,
            "salience_factors": assessment.factors,
            "salience_source": "deterministic_contextual_assessment",
            "novelty_basis": assessment.novelty_basis,
        }
    )
    return candidate.model_copy(
        update={
            "salience": assessment.score,
            "novelty": assessment.novelty,
            "importance": assessment.importance,
            "importance_reason": assessment.reason,
            "payload": payload,
        }
    )


def _event_evidence(candidate: MemoryCandidate) -> str:
    values = (
        candidate.summary,
        candidate.original_text,
        candidate.raw_predicate,
        candidate.custom_predicate,
        candidate.canonical_predicate,
        candidate.payload.get("action"),
        candidate.payload.get("activity_type"),
        candidate.payload.get("outcome"),
        *candidate.evidence_spans,
    )
    return " ".join(str(value) for value in values if isinstance(value, str) and value.strip())


def _event_signature(candidate: MemoryCandidate) -> str:
    value = next(
        (
            value
            for value in (
                candidate.canonical_predicate,
                candidate.payload.get("action"),
                candidate.payload.get("activity_type"),
                candidate.custom_predicate,
                candidate.raw_predicate,
                candidate.payload.get("event_type"),
            )
            if isinstance(value, str) and value.strip()
        ),
        candidate.summary,
    )
    normalized = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    return re.sub(r"[^\w.\u4e00-\u9fff]+", "_", normalized).strip("_")


def _has_known_recurring_pattern(
    candidate: MemoryCandidate,
    existing_memories: list[MemoryItem],
) -> bool:
    event_type = normalize_interaction_event_type(candidate.payload.get("event_type"))
    if event_type != "conflict" and not _CONFLICT_EVENT_PATTERN.search(
        _event_evidence(candidate)
    ):
        return False
    return any(
        item.kind == MemoryKind.INTERACTION_PATTERN
        and item.status in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
        and (
            item.canonical_predicate == "interaction.conflict_frequency"
            or item.payload.get("metric") == "conflict_frequency"
        )
        for item in existing_memories
    )


def _same_occurrence(incoming: MemoryCandidate, existing: MemoryItem) -> bool:
    incoming_event_id = incoming.payload.get("event_id")
    existing_event_id = existing.payload.get("event_id")
    if (
        isinstance(incoming_event_id, str)
        and incoming_event_id.strip()
        and incoming_event_id == existing_event_id
    ):
        return True
    incoming_time = _event_time(incoming)
    existing_time = _event_time(existing)
    if incoming_time is not None and existing_time is not None:
        return _same_calendar_day(incoming_time, existing_time)
    return _normalize_text(incoming.original_text) == _normalize_text(existing.original_text)


def _event_time(candidate: MemoryCandidate) -> datetime | None:
    return candidate.occurred_at or candidate.period_end or candidate.period_start


def _same_calendar_day(left: datetime, right: datetime) -> bool:
    if left.tzinfo is None and right.tzinfo is not None:
        left = left.replace(tzinfo=right.tzinfo)
    elif left.tzinfo is not None and right.tzinfo is None:
        right = right.replace(tzinfo=left.tzinfo)
    return left.date() == right.date()


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"\s+", " ", normalized)


def _relationship_impact_score(candidate: MemoryCandidate) -> float:
    if candidate.relationship_impact in {
        RelationshipImpact.DAMAGING,
        RelationshipImpact.IMPROVING,
    }:
        base = 0.9
    elif candidate.relationship_impact == RelationshipImpact.UNCHANGED:
        base = 0.2
    else:
        base = 0.3
    declarations = candidate.payload.get("relationship_evidence")
    if isinstance(declarations, list):
        for declaration in declarations:
            if not isinstance(declaration, dict):
                continue
            strength = declaration.get("strength")
            confidence = declaration.get("confidence")
            if isinstance(strength, (int, float)) and isinstance(confidence, (int, float)):
                base = max(base, min(float(strength) * float(confidence), 1.0))
    return base


def _emotional_intensity_score(candidate: MemoryCandidate) -> float:
    if candidate.intensity is not None:
        return min(max(candidate.intensity / 5.0, 0.0), 1.0)
    if candidate.emotions:
        return 0.6
    if candidate.valence in {MemoryValence.POSITIVE, MemoryValence.NEGATIVE, MemoryValence.MIXED}:
        return 0.45
    return 0.0

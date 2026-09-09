from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from loveapp.domain.memory import MemoryItem, MemoryKind, MemoryStatus
from loveapp.domain.memory_dimensions import (
    is_conflict_interaction_event,
    normalize_interaction_event_cause,
    validate_interaction_event_payload,
)


class EventEnrichmentField(StrEnum):
    """Bounded Event attributes that Python may enrich non-destructively."""

    CAUSE = "cause"
    SEVERITY = "severity"
    EMOTION = "emotion"
    RESOLUTION = "resolution"
    OUTCOME = "outcome"
    LOCATION = "location"
    ACTIVITY_TYPE = "activity_type"


class GenericEventEnrichment(BaseModel):
    """Typed enrichment command; it is not an arbitrary payload patch."""

    target_memory_id: str = Field(min_length=1)
    antecedent_message_id: str = Field(min_length=1, max_length=160)
    evidence_span: str = Field(min_length=1, max_length=1000)
    field: EventEnrichmentField
    value: str | int | dict[str, str] | list[str]
    confidence: float = Field(default=0.9, ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_value(self) -> GenericEventEnrichment:
        value = self.value
        if self.field == EventEnrichmentField.CAUSE:
            if isinstance(value, str):
                value = {"description": value.strip()}
            if not isinstance(value, dict) or not value:
                raise ValueError("cause enrichment requires a non-empty object or text")
            if set(value) - {"category", "description"}:
                raise ValueError("cause enrichment contains an unsupported field")
            if not any(isinstance(item, str) and item.strip() for item in value.values()):
                raise ValueError("cause enrichment requires a value")
            self.value = dict(value)
            return self
        if self.field == EventEnrichmentField.SEVERITY:
            if not (
                isinstance(value, int)
                and not isinstance(value, bool)
                and 1 <= value <= 5
            ) and not (
                isinstance(value, str)
                and value.casefold().strip() in {"low", "moderate", "high", "severe"}
            ):
                raise ValueError("severity enrichment must be 1..5 or a bounded label")
            return self
        if self.field == EventEnrichmentField.EMOTION and isinstance(value, list):
            if not value or any(not isinstance(item, str) or not item.strip() for item in value):
                raise ValueError("emotion enrichment list must contain text")
            return self
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{self.field.value} enrichment requires non-empty text")
        self.value = value.strip()
        return self


def apply_event_enrichment(
    item: MemoryItem,
    enrichment: GenericEventEnrichment,
    *,
    source_message_id: str | None,
    updated_at: datetime,
) -> MemoryItem:
    """Apply one whitelisted Event field without silently overwriting history."""

    if item.id != enrichment.target_memory_id:
        raise ValueError("Event enrichment target does not match the item")
    if item.source_message_id != enrichment.antecedent_message_id:
        raise ValueError("Event enrichment antecedent does not match the target source")
    if item.kind != MemoryKind.INTERACTION_EVENT or item.status not in {
        MemoryStatus.PROPOSED,
        MemoryStatus.CONFIRMED,
    }:
        raise ValueError("Event enrichment target must be an active Event")
    if not enrichment.evidence_span.strip():
        raise ValueError("Event enrichment requires evidence")

    payload = dict(item.payload)
    field = enrichment.field.value
    value: object = enrichment.value
    if field == EventEnrichmentField.CAUSE.value:
        value = normalize_interaction_event_cause(value)
    existing = payload.get(field)
    if existing is not None and existing != value:
        raise ValueError(f"Event enrichment cannot overwrite existing {field}")
    payload.setdefault(field, value)
    validate_interaction_event_payload(payload, perspective=item.perspective)

    history = payload.get("enrichment_history")
    records = list(history) if isinstance(history, list) else []
    already_recorded = any(
        isinstance(record, dict)
        and record.get("source_message_id") == source_message_id
        and record.get("field") == field
        for record in records
    )
    if not already_recorded:
        records.append(
            {
                "enrichment_type": "event_attribute",
                "field": field,
                "source_message_id": source_message_id,
                "antecedent_message_id": enrichment.antecedent_message_id,
                "evidence": enrichment.evidence_span,
                "confidence": round(enrichment.confidence, 4),
            }
        )
    payload["enrichment_history"] = records[-20:]
    evidence_spans = list(item.evidence_spans)
    if enrichment.evidence_span not in evidence_spans:
        evidence_spans.append(enrichment.evidence_span)
    return item.model_copy(
        update={
            "payload": payload,
            "evidence_spans": evidence_spans[-8:],
            "updated_at": updated_at,
            "last_seen_at": updated_at,
        }
    )


class ConflictEventEnrichment(BaseModel):
    """A typed, non-destructive cause enrichment for one conflict Event."""

    target_memory_id: str = Field(min_length=1)
    antecedent_message_id: str = Field(min_length=1, max_length=160)
    evidence_span: str = Field(min_length=1, max_length=1000)
    cause_category: str | None = Field(default=None, max_length=80)
    cause_description: str | None = Field(default=None, max_length=500)
    confidence: float = Field(default=0.9, ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_cause(self) -> ConflictEventEnrichment:
        self.cause_category = (
            self.cause_category.strip() if self.cause_category is not None else None
        )
        self.cause_description = (
            self.cause_description.strip()
            if self.cause_description is not None
            else None
        )
        if self.cause_category is None and self.cause_description is None:
            raise ValueError("conflict Event enrichment requires a cause value")
        if self.cause_category is not None and re.fullmatch(
            r"[a-z][a-z0-9_]*",
            self.cause_category,
        ) is None:
            raise ValueError("conflict Event cause category must be snake_case")
        return self


def apply_conflict_event_enrichment(
    item: MemoryItem,
    enrichment: ConflictEventEnrichment,
    *,
    source_message_id: str | None,
    updated_at: datetime,
) -> MemoryItem:
    """Apply a guarded Event ENRICH without replacing the historical Event."""

    if item.id != enrichment.target_memory_id:
        raise ValueError("conflict Event enrichment target does not match the item")
    if (
        item.source_message_id is None
        or item.source_message_id != enrichment.antecedent_message_id
    ):
        raise ValueError(
            "conflict Event enrichment antecedent does not match the target source"
        )
    if item.kind != MemoryKind.INTERACTION_EVENT or item.status not in {
        MemoryStatus.PROPOSED,
        MemoryStatus.CONFIRMED,
    }:
        raise ValueError("conflict Event enrichment target must be an active Event")
    evidence = " ".join([item.original_text, *item.evidence_spans])
    if not is_conflict_interaction_event(item.payload, evidence_text=evidence):
        raise ValueError("conflict Event enrichment target is not a conflict Event")

    payload = dict(item.payload)
    existing_cause = normalize_interaction_event_cause(payload.get("cause"))
    if existing_cause is not None and not isinstance(existing_cause, dict):
        raise ValueError("conflict Event target has an invalid cause payload")
    existing_cause = dict(existing_cause or {})
    if (
        enrichment.cause_category is not None
        and existing_cause.get("category") not in {None, enrichment.cause_category}
    ):
        raise ValueError("conflict Event cause enrichment cannot overwrite a category")
    if enrichment.cause_category is not None:
        existing_cause.setdefault("category", enrichment.cause_category)
    if enrichment.cause_description is not None:
        existing_cause.setdefault("description", enrichment.cause_description)
    payload["cause"] = existing_cause
    payload.setdefault("event_type", "conflict")

    provenance = payload.get("enrichment_history")
    history = list(provenance) if isinstance(provenance, list) else []
    already_recorded = bool(
        source_message_id
        and any(
            isinstance(record, dict)
            and record.get("source_message_id") == source_message_id
            and record.get("enrichment_type") == "conflict_cause"
            for record in history
        )
    )
    if not already_recorded:
        history.append(
            {
                "enrichment_type": "conflict_cause",
                "source_message_id": source_message_id,
                "antecedent_message_id": enrichment.antecedent_message_id,
                "evidence": enrichment.evidence_span,
                "confidence": round(enrichment.confidence, 4),
            }
        )
        payload["enrichment_history"] = history[-20:]

    evidence_spans = list(item.evidence_spans)
    if enrichment.evidence_span not in evidence_spans:
        evidence_spans.append(enrichment.evidence_span)
    return item.model_copy(
        update={
            "payload": payload,
            "evidence_spans": evidence_spans[-8:],
            "updated_at": updated_at,
            "last_seen_at": updated_at,
        }
    )


__all__ = [
    "ConflictEventEnrichment",
    "EventEnrichmentField",
    "GenericEventEnrichment",
    "apply_conflict_event_enrichment",
    "apply_event_enrichment",
]

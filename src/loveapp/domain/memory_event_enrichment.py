from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from loveapp.domain.memory import MemoryItem, MemoryKind, MemoryStatus
from loveapp.domain.memory_dimensions import (
    infer_interaction_event_type,
    is_conflict_interaction_event,
    normalize_event_severity,
    normalize_interaction_event_cause,
    normalize_interaction_event_type,
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
    CUSTOM = "custom_attribute"


EVENT_ENRICHMENT_FIELD_COMPATIBILITY: dict[
    str, frozenset[EventEnrichmentField]
] = {
    "conflict": frozenset(
        {
            EventEnrichmentField.CAUSE,
            EventEnrichmentField.SEVERITY,
            EventEnrichmentField.EMOTION,
            EventEnrichmentField.RESOLUTION,
            EventEnrichmentField.OUTCOME,
        }
    ),
    "date": frozenset(
        {
            EventEnrichmentField.LOCATION,
            EventEnrichmentField.ACTIVITY_TYPE,
            EventEnrichmentField.EMOTION,
            EventEnrichmentField.OUTCOME,
        }
    ),
    "shared_activity": frozenset(
        {
            EventEnrichmentField.LOCATION,
            EventEnrichmentField.ACTIVITY_TYPE,
            EventEnrichmentField.EMOTION,
            EventEnrichmentField.OUTCOME,
        }
    ),
}
CUSTOM_EVENT_ATTRIBUTE_COMPATIBILITY = {
    "weather": frozenset({"date", "shared_activity"}),
}


class CustomEventAttribute(BaseModel):
    """A provenance-bearing Event attribute outside the canonical field set."""

    model_config = ConfigDict(extra="forbid")

    attribute: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=80)
    value: StrictStr | StrictInt = Field()
    evidence_span: str = Field(min_length=1, max_length=1000)
    source_message_id: str = Field(min_length=1, max_length=160)
    confidence: float = Field(default=0.8, ge=0, le=1)
    created_at: datetime

    @model_validator(mode="after")
    def validate_value(self) -> CustomEventAttribute:
        if isinstance(self.value, str):
            self.value = self.value.strip()
            if not self.value:
                raise ValueError("custom Event attribute requires a value")
        return self


class GenericEventEnrichment(BaseModel):
    """Typed enrichment command; it is not an arbitrary payload patch."""

    target_memory_id: str = Field(min_length=1)
    antecedent_message_id: str = Field(min_length=1, max_length=160)
    evidence_span: str = Field(min_length=1, max_length=1000)
    field: EventEnrichmentField
    value: CustomEventAttribute | str | int | dict[str, str] | list[str]
    confidence: float = Field(default=0.9, ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_value(self) -> GenericEventEnrichment:
        value = self.value
        if self.field == EventEnrichmentField.CUSTOM:
            if not isinstance(value, CustomEventAttribute):
                raise ValueError("custom Event enrichment requires a typed attribute")
            if value.evidence_span != self.evidence_span:
                raise ValueError("custom Event attribute evidence must match the enrichment")
            return self
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
            normalized = normalize_event_severity(value)
            if normalized is None:
                raise ValueError("severity enrichment must be 1..5 or a bounded label")
            self.value = normalized
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
    event_type = normalize_interaction_event_type(payload.get("event_type"))
    if event_type is None:
        event_type = infer_interaction_event_type(
            payload,
            evidence_text=" ".join([item.original_text, *item.evidence_spans]),
        )
    if event_type not in EVENT_ENRICHMENT_FIELD_COMPATIBILITY:
        raise ValueError("Event enrichment target has an unsupported EventType")
    if enrichment.field == EventEnrichmentField.CUSTOM:
        if not isinstance(value, CustomEventAttribute):
            raise ValueError("custom Event enrichment requires a typed attribute")
        if source_message_id != value.source_message_id:
            raise ValueError("custom Event attribute source does not match the write batch")
        if event_type not in CUSTOM_EVENT_ATTRIBUTE_COMPATIBILITY.get(
            value.attribute,
            frozenset(),
        ):
            raise ValueError("custom Event attribute is incompatible with the EventType")
        attributes = payload.get("custom_attributes")
        custom_attributes = list(attributes) if isinstance(attributes, list) else []
        existing_attribute = next(
            (
                attribute
                for attribute in custom_attributes
                if isinstance(attribute, dict)
                and attribute.get("attribute") == value.attribute
            ),
            None,
        )
        if existing_attribute is not None and existing_attribute.get("value") != value.value:
            raise ValueError(
                f"Event enrichment cannot overwrite custom attribute {value.attribute}"
            )
        if existing_attribute is None:
            custom_attributes.append(value.model_dump(mode="json"))
        payload["custom_attributes"] = custom_attributes[-20:]
        field = f"custom:{value.attribute}"
        value = value.value
    elif enrichment.field not in EVENT_ENRICHMENT_FIELD_COMPATIBILITY[event_type]:
        raise ValueError("Event enrichment field is incompatible with the EventType")
    if field == EventEnrichmentField.CAUSE.value:
        value = normalize_interaction_event_cause(value)
    if enrichment.field != EventEnrichmentField.CUSTOM:
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
    "CUSTOM_EVENT_ATTRIBUTE_COMPATIBILITY",
    "EVENT_ENRICHMENT_FIELD_COMPATIBILITY",
    "ConflictEventEnrichment",
    "CustomEventAttribute",
    "EventEnrichmentField",
    "GenericEventEnrichment",
    "apply_conflict_event_enrichment",
    "apply_event_enrichment",
    "normalize_event_severity",
]

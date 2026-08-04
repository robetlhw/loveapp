import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class MemoryKind(StrEnum):
    STABLE_FACT = "stable_fact"
    PREFERENCE = "preference"
    INTERACTION_EVENT = "interaction_event"
    INTERACTION_PATTERN = "interaction_pattern"
    ADVICE_OUTCOME = "advice_outcome"
    PLANNED_EVENT = "planned_event"
    ACTION_INTENT = "action_intent"
    RELATIONSHIP_STATE = "relationship_state"

    # Source-compatible aliases; persisted values use canonical values.
    INTERACTION_EPISODE = "interaction_event"
    INTERACTION_TREND = "interaction_pattern"
    PENDING_EVENT = "planned_event"
    PENDING_ACTION = "action_intent"


class TimeKind(StrEnum):
    POINT = "point"
    INTERVAL = "interval"
    TIMELESS = "timeless"
    UNKNOWN = "unknown"


class TemporalPrecision(StrEnum):
    EXACT = "exact"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    APPROXIMATE = "approximate"
    UNKNOWN = "unknown"


class MemoryValence(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    MIXED = "mixed"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class RelationshipImpact(StrEnum):
    IMPROVING = "improving"
    DAMAGING = "damaging"
    UNCHANGED = "unchanged"
    UNCLEAR = "unclear"


class MemoryPerspective(StrEnum):
    USER_REPORTED = "user_reported"
    USER_BELIEF = "user_belief"
    MODEL_INFERRED = "model_inferred"


class MemoryStatus(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class DiscardReason(StrEnum):
    CONSULTATION_QUESTION = "consultation_question"
    CONSULTATION_GOAL = "consultation_goal"
    EPHEMERAL = "ephemeral"
    NO_DURABLE_MEMORY = "no_durable_memory"


class DiscardedSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)
    reason: DiscardReason


class MemoryGateReason(StrEnum):
    DURABLE_SIGNAL = "durable_signal"
    EXPLICIT_REMEMBER = "explicit_remember"
    CASUAL = "casual"
    KNOWLEDGE_QUESTION = "knowledge_question"
    OPERATION = "operation"
    HYPOTHETICAL = "hypothetical"
    CONSULTATION_ONLY = "consultation_only"
    NO_DURABLE_SIGNAL = "no_durable_signal"


class MemoryGateDecision(BaseModel):
    should_extract: bool
    reason: MemoryGateReason
    signals: list[str] = Field(default_factory=list)


class MemoryExtractionStatus(StrEnum):
    RUNNING = "running"
    SKIPPED = "skipped"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MemoryAttemptStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class MemoryExtractionAttempt(BaseModel):
    attempt: int = Field(ge=1)
    status: MemoryAttemptStatus
    duration_ms: float = Field(ge=0)
    model: str | None = None
    tier: str | None = None
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    claim_count: int | None = Field(default=None, ge=0)
    original_claim_count: int | None = Field(default=None, ge=0)
    repaired_claim_count: int | None = Field(default=None, ge=0)
    discarded_claim_count: int | None = Field(default=None, ge=0)
    discarded_span_count: int | None = Field(default=None, ge=0)
    claim_confidences: str | None = None
    invalid_claim_count: int | None = Field(default=None, ge=0)
    invalid_claim_reasons: str | None = None
    repair_status: str | None = None
    upgrade_reason: str | None = None
    discard_reason: str | None = None
    retry_reason: str | None = None
    error: str | None = None


class MemoryExtractionRun(BaseModel):
    id: str
    user_id: str
    relationship_id: str
    conversation_id: str
    source_message_id: str
    status: MemoryExtractionStatus
    gate_decision: MemoryGateDecision
    attempts: list[MemoryExtractionAttempt] = Field(default_factory=list)
    saved_memory_ids: list[str] = Field(default_factory=list)
    discarded_spans: list[DiscardedSpan] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


def _normalize_memory_input(value: object) -> object:
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    aliases = {
        "memory_kind": "kind",
        "target": "object",
        "evidence": "evidence_spans",
        "time_type": "time_kind",
        "temporal_type": "time_kind",
        "temporal_start": "period_start",
        "time_start": "period_start",
        "start_time": "period_start",
        "temporal_end": "period_end",
        "time_end": "period_end",
        "end_time": "period_end",
        "time_precision": "temporal_precision",
        "valid_until": "expires_at",
        "expiration": "expires_at",
    }
    for alias, target in aliases.items():
        if alias in normalized:
            item = normalized.pop(alias)
            if target == "evidence_spans" and isinstance(item, str):
                item = [item]
            normalized.setdefault(target, item)

    temporal = normalized.pop("temporal", None)
    time_alias = normalized.pop("time", None)
    if temporal is None:
        temporal = time_alias
    normalized.pop("reference_time", None)
    temporal_expression_alias: str | None = None
    for alias in ("datetime", "time_value", "event_time", "timestamp"):
        item = normalized.pop(alias, None)
        if isinstance(item, dict) and temporal is None:
            temporal = item
        elif _looks_like_iso_datetime(item) and "occurred_at" not in normalized:
            normalized["occurred_at"] = item
        elif isinstance(item, str) and item.strip():
            temporal_expression_alias = item.strip()
    if isinstance(temporal, str):
        normalized.setdefault("time_kind", temporal)
    elif isinstance(temporal, dict):
        temporal_aliases = {
            "type": "time_kind",
            "kind": "time_kind",
            "start": "period_start",
            "end": "period_end",
            "precision": "temporal_precision",
        }
        for key, item in temporal.items():
            target = temporal_aliases.get(key, key)
            if target in {
                "time_kind",
                "occurred_at",
                "period_start",
                "period_end",
                "temporal_precision",
            }:
                normalized.setdefault(target, item)

    raw_payload = normalized.pop("payload", None)
    payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
    for key in (
        "metric",
        "frequency",
        "direction",
        "baseline",
        "current",
        "preference",
        "preference_type",
        "temporal_expression",
        "event_status",
        "memory_role",
        "state_scope",
        "state_value",
        "state_dimension",
        "activity_type",
        "participants",
        "relationship_evidence",
        "related_plan_id",
        "completes_plan_id",
    ):
        if key in normalized:
            payload.setdefault(key, normalized.pop(key))
    if temporal_expression_alias:
        payload.setdefault("temporal_expression", temporal_expression_alias)
    if payload or raw_payload is not None:
        normalized["payload"] = payload
    precision = normalized.get("temporal_precision")
    if isinstance(precision, str):
        normalized["temporal_precision"] = _normalize_temporal_precision(precision)
    return normalized


def _normalize_temporal_precision(value: str) -> str:
    normalized = value.casefold().strip()
    aliases = {
        "date": TemporalPrecision.DAY.value,
        "daily": TemporalPrecision.DAY.value,
        "weekly": TemporalPrecision.WEEK.value,
        "monthly": TemporalPrecision.MONTH.value,
        "rough": TemporalPrecision.APPROXIMATE.value,
        "relative": TemporalPrecision.APPROXIMATE.value,
        "recent": TemporalPrecision.UNKNOWN.value,
        "unspecified": TemporalPrecision.UNKNOWN.value,
        "none": TemporalPrecision.UNKNOWN.value,
    }
    allowed = {item.value for item in TemporalPrecision}
    fallback = normalized if normalized in allowed else TemporalPrecision.UNKNOWN
    return aliases.get(normalized, fallback)


def _looks_like_iso_datetime(value: object) -> bool:
    return isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}", value.strip()) is not None


class MemoryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: MemoryKind
    subject: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=500)
    original_text: str = Field(min_length=1, max_length=4000)
    evidence_spans: list[str] = Field(default_factory=list, max_length=8)
    time_kind: TimeKind = TimeKind.UNKNOWN
    occurred_at: datetime | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    expires_at: datetime | None = None
    temporal_precision: TemporalPrecision = TemporalPrecision.UNKNOWN
    valence: MemoryValence = MemoryValence.UNKNOWN
    relationship_impact: RelationshipImpact = RelationshipImpact.UNCLEAR
    intensity: int | None = Field(default=None, ge=1, le=5)
    emotions: list[str] = Field(default_factory=list, max_length=8)
    importance: int = Field(default=3, ge=1, le=5)
    perspective: MemoryPerspective = MemoryPerspective.USER_REPORTED
    confidence: float = Field(default=0.8, ge=0, le=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    supersedes_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def flatten_temporal_object(cls, value: object) -> object:
        return _normalize_memory_input(value)

    @model_validator(mode="after")
    def validate_time_shape(self) -> "MemoryCandidate":
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("period_start cannot be later than period_end")
        if not self.evidence_spans:
            self.evidence_spans = [self.original_text]
        return self


class AtomicClaim(BaseModel):
    model_config = ConfigDict(extra="ignore")

    claim_id: str = Field(min_length=1, max_length=80)
    kind: MemoryKind
    subject: str = Field(min_length=1, max_length=80)
    predicate: str = Field(min_length=1, max_length=120)
    object: str | None = Field(default=None, max_length=200)
    summary: str = Field(min_length=1, max_length=500)
    evidence_spans: list[str] = Field(min_length=1, max_length=8)
    time_kind: TimeKind = TimeKind.UNKNOWN
    occurred_at: datetime | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    expires_at: datetime | None = None
    temporal_precision: TemporalPrecision = TemporalPrecision.UNKNOWN
    valence: MemoryValence = MemoryValence.UNKNOWN
    relationship_impact: RelationshipImpact = RelationshipImpact.UNCLEAR
    intensity: int | None = Field(default=None, ge=1, le=5)
    emotions: list[str] = Field(default_factory=list, max_length=8)
    importance: int = Field(default=3, ge=1, le=5)
    perspective: MemoryPerspective = MemoryPerspective.USER_REPORTED
    confidence: float = Field(default=0.8, ge=0, le=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    supersedes_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def flatten_temporal_object(cls, value: object) -> object:
        return _normalize_memory_input(value)

    @model_validator(mode="after")
    def validate_time_shape(self) -> "AtomicClaim":
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("period_start cannot be later than period_end")
        return self

    def to_candidate(self) -> MemoryCandidate:
        payload = dict(self.payload)
        payload.setdefault("predicate", self.predicate)
        if self.object is not None:
            payload.setdefault("object", self.object)
        return MemoryCandidate(
            kind=self.kind,
            subject=self.subject,
            summary=self.summary,
            original_text="；".join(self.evidence_spans),
            evidence_spans=self.evidence_spans,
            time_kind=self.time_kind,
            occurred_at=self.occurred_at,
            period_start=self.period_start,
            period_end=self.period_end,
            expires_at=self.expires_at,
            temporal_precision=self.temporal_precision,
            valence=self.valence,
            relationship_impact=self.relationship_impact,
            intensity=self.intensity,
            emotions=self.emotions,
            importance=self.importance,
            perspective=self.perspective,
            confidence=self.confidence,
            payload=payload,
            supersedes_id=self.supersedes_id,
        )


class AtomicExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[AtomicClaim] = Field(default_factory=list, max_length=12)
    discarded_spans: list[DiscardedSpan] = Field(default_factory=list, max_length=12)


class MemoryItem(MemoryCandidate):
    id: str
    user_id: str
    relationship_id: str
    status: MemoryStatus = MemoryStatus.PROPOSED
    source_message_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_used_at: datetime | None = None
    dedupe_key: str


class MemoryContextItem(BaseModel):
    id: str
    kind: MemoryKind
    subject: str
    summary: str
    evidence_spans: list[str] = Field(default_factory=list)
    time_kind: TimeKind
    occurred_at: datetime | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    expires_at: datetime | None = None
    valence: MemoryValence
    relationship_impact: RelationshipImpact
    importance: int = Field(default=3, ge=1, le=5)
    perspective: MemoryPerspective
    confidence: float
    status: MemoryStatus
    payload: dict[str, Any] = Field(default_factory=dict)
    attention_reason: str | None = None

    @classmethod
    def from_item(cls, item: MemoryItem) -> "MemoryContextItem":
        return cls.model_validate(
            item.model_dump(
                include={
                    "id",
                    "kind",
                    "subject",
                    "summary",
                    "evidence_spans",
                    "time_kind",
                    "occurred_at",
                    "period_start",
                    "period_end",
                    "expires_at",
                    "valence",
                    "relationship_impact",
                    "importance",
                    "perspective",
                    "confidence",
                    "status",
                    "payload",
                }
            )
        )


class StoredMessage(BaseModel):
    id: str
    conversation_id: str
    user_id: str
    relationship_id: str
    role: MessageRole
    content: str
    created_at: datetime = Field(default_factory=utc_now)


class MemorySaveResult(BaseModel):
    item: MemoryItem
    created: bool


class RememberResult(BaseModel):
    message: StoredMessage
    saved: list[MemorySaveResult] = Field(default_factory=list)
    discarded_spans: list[DiscardedSpan] = Field(default_factory=list)
    skipped_low_confidence: int = 0
    extraction_error: str | None = None
    gate_decision: MemoryGateDecision | None = None
    pending: bool = False
    extraction_run_id: str | None = None


class MemoryCompactionGroup(BaseModel):
    identity_key: str
    keeper_id: str
    duplicate_ids: list[str]
    summaries: list[str]


class MemoryCompactionResult(BaseModel):
    groups: list[MemoryCompactionGroup] = Field(default_factory=list)
    applied_count: int = 0


def memory_dedupe_key(candidate: MemoryCandidate) -> str:
    parts = _memory_identity_parts(candidate)
    normalized = "|".join(_normalize_key_part(part) for part in parts)
    return sha256(normalized.encode("utf-8")).hexdigest()


def _memory_identity_parts(candidate: MemoryCandidate) -> tuple[str, ...]:
    kind = candidate.kind.value
    subject = candidate.subject
    payload = candidate.payload
    predicate = _canonical_predicate(payload.get("predicate"))

    if candidate.kind == MemoryKind.PREFERENCE:
        preference = payload.get("preference")
        preference_type = payload.get("preference_type")
        if isinstance(preference, str):
            return (kind, subject, "preference", str(preference_type or "unknown"), preference)

    if candidate.kind == MemoryKind.INTERACTION_PATTERN:
        metric = payload.get("metric")
        if isinstance(metric, str):
            state = _canonical_pattern_state(payload)
            return (kind, subject, "metric", metric, state)

    if candidate.kind == MemoryKind.RELATIONSHIP_STATE:
        dimension = payload.get("state_dimension")
        value = payload.get("state_value")
        if isinstance(dimension, str) and isinstance(value, str):
            return (kind, subject, "state", dimension, value)

    if predicate:
        object_value = _canonical_object(
            payload.get("object"),
            kind=candidate.kind,
            subject=subject,
            predicate=predicate,
        )
        if candidate.kind == MemoryKind.PLANNED_EVENT:
            temporal_key = _temporal_identity(candidate)
            return (kind, subject, predicate, object_value, temporal_key)
        if candidate.kind == MemoryKind.ACTION_INTENT:
            return (kind, subject, predicate, object_value)
        if candidate.kind in {MemoryKind.INTERACTION_EVENT, MemoryKind.ADVICE_OUTCOME}:
            if predicate in {
                "confession_succeeded",
                "confession_accepted",
                "relationship_started",
                "relationship_confirmed",
            }:
                # Relationship-state events are durable facts for one
                # relationship, not separate episodes each time the user
                # refers to the same transition.
                return (kind, subject, predicate, object_value)
            temporal_key = _temporal_identity(candidate)
            evidence_key = "|".join(candidate.evidence_spans)
            return (kind, subject, predicate, object_value, temporal_key, evidence_key)
        return (kind, subject, predicate, object_value)

    return (kind, subject, candidate.summary)


def _canonical_predicate(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = _normalize_key_part(value).replace(" ", "_")
    aliases = {
        "has_crush_on": "likes",
        "likes_person": "likes",
        "is_attracted_to": "likes",
        "low_interaction_frequency": "has_low_interaction",
    }
    return aliases.get(normalized, normalized)


def _canonical_object(
    value: object,
    *,
    kind: MemoryKind,
    subject: str,
    predicate: str,
) -> str:
    normalized = _normalize_key_part(str(value or "unknown")).replace(" ", "_")
    if kind == MemoryKind.STABLE_FACT and subject.casefold() == "user" and predicate == "likes":
        partner_aliases = (
            "partner",
            "a_girl",
            "girl",
            "classmate_girl",
            "female_classmate",
            "她",
            "对方",
            "女孩",
            "女生",
            "女孩子",
        )
        if any(alias in normalized for alias in partner_aliases):
            return "relationship_partner"
    return normalized


def _canonical_pattern_state(payload: dict[str, Any]) -> str:
    raw = payload.get("current") or payload.get("direction") or payload.get("frequency")
    normalized = _normalize_key_part(str(raw or "unknown")).replace(" ", "_")
    aliases = {
        "rare": "low",
        "infrequent": "low",
        "few": "low",
        "decreased": "decreasing",
        "declined": "decreasing",
        "increased": "increasing",
        "improved": "increasing",
        "frequent": "high",
    }
    return aliases.get(normalized, normalized)


def _temporal_identity(candidate: MemoryCandidate) -> str:
    if candidate.occurred_at:
        return candidate.occurred_at.isoformat()
    if candidate.period_start or candidate.period_end:
        start = candidate.period_start.isoformat() if candidate.period_start else ""
        end = candidate.period_end.isoformat() if candidate.period_end else ""
        return f"{start}/{end}"
    return candidate.time_kind.value


def _normalize_key_part(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"\s+", " ", normalized)

import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from loveapp.domain.memory_dimensions import normalize_state_dimension, normalize_state_value
from loveapp.domain.memory_predicates import (
    normalize_predicate,
    normalize_preference_value,
)
from loveapp.domain.runtime_context import PendingMemoryContext


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
    # Compatibility kind for derived/current relationship projections.  Base
    # extraction should prefer Fact, Preference, Event, or Pattern; callers
    # must not treat this row kind as permission to infer a relationship state.
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


class EpistemicStatus(StrEnum):
    """How strongly the proposition is presented as knowledge.

    This is deliberately separate from ``MemoryStatus``.  Lifecycle status
    describes whether a row is current; epistemic status describes whether
    the proposition is a reported fact, an uncertainty, a hypothesis, or a
    prediction.
    """

    CONFIRMED = "confirmed"
    UNCERTAIN = "uncertain"
    HYPOTHESIS = "hypothesis"
    PREDICTION = "prediction"


class MemoryStatus(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class PatternLifecycleState(StrEnum):
    """Evidence-driven state of an Interaction Pattern.

    ``MemoryStatus`` remains the row-level lifecycle authority.  This smaller
    state describes whether the observation represented by an active Pattern
    is still supported, is weakening, or has been superseded by a governed
    opposite Pattern.
    """

    ACTIVE = "active"
    WEAKENING = "weakening"
    SUPERSEDED = "superseded"


class PredicateType(StrEnum):
    CANONICAL = "canonical"
    CUSTOM = "custom"


class EvidenceExplicitness(StrEnum):
    EXPLICIT = "explicit"
    STRONGLY_IMPLIED = "strongly_implied"
    WEAKLY_INFERRED = "weakly_inferred"
    SPECULATIVE = "speculative"


class AdmissionDecision(StrEnum):
    CONFIRM = "confirm"
    PROPOSE = "propose"
    STRONG_REVIEW = "strong_review"
    REJECT = "reject"


class ClaimRelation(StrEnum):
    SAME = "same"
    COMPLEMENTARY = "complementary"
    UPDATE = "update"
    CONTRADICTION = "contradiction"
    UNRELATED = "unrelated"
    UNCERTAIN = "uncertain"


class MutationAction(StrEnum):
    """Governed storage action, kept separate from semantic claim relation.

    A relation describes what the incoming proposition means relative to an
    existing claim.  This enum describes the bounded write operation selected
    by Python governance.  Model output is never accepted as an authority for
    this field.
    """

    CREATE = "create"
    ENRICH = "enrich"
    REFINE = "refine"
    LINK = "link"
    UPDATE = "update"
    SUPERSEDE = "supersede"
    REJECT = "reject"


class MemoryExtractionMode(StrEnum):
    """Extraction strategy selected before the shared governance pipeline."""

    SINGLE_STAGE = "single_stage"
    TWO_STAGE = "two_stage"


class SemanticRole(StrEnum):
    """Semantic role produced by coarse extraction.

    These values describe meaning only.  They are deliberately not database
    mutation commands; relation/lifecycle governance remains the authority for
    writes.
    """

    STANDALONE_PROPOSITION = "standalone_proposition"
    ATTRIBUTE_UPDATE = "attribute_update"
    REFINEMENT_CANDIDATE = "refinement_candidate"
    STATE_ASSERTION = "state_assertion"
    CONTEXTUAL_COMPLETION = "contextual_completion"


class CoarseProposition(BaseModel):
    """A recall-oriented proposition hint emitted by Stage 1 extraction.

    The object intentionally contains no canonical predicate, database id, or
    mutation command.  It is a semantic hand-off to the detailed extractor.
    """

    model_config = ConfigDict(extra="forbid")

    proposition_id: str = Field(min_length=1, max_length=80)
    evidence_span: str = Field(min_length=1, max_length=1000)
    candidate_kinds: list[MemoryKind] = Field(min_length=1, max_length=5)
    semantic_role: SemanticRole = SemanticRole.STANDALONE_PROPOSITION
    target_field_hint: str | None = Field(default=None, max_length=80)
    subject_hint: str | None = Field(default=None, max_length=80)
    temporal_hint: str | None = Field(default=None, max_length=160)
    target_semantic_hint: dict[str, Any] | None = None
    confidence: float = Field(default=0.7, ge=0, le=1)
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_kinds(self) -> "CoarseProposition":
        # Preserve order while preventing duplicate routing branches.  A
        # top-k hint is useful for ambiguous Event/Pattern/State boundaries.
        self.candidate_kinds = list(dict.fromkeys(self.candidate_kinds))
        forbidden_keys = {
            "target_memory_id",
            "target_memory_ids",
            "mutation",
            "mutation_action",
            "db_action",
            "write_action",
        }

        def contains_forbidden(value: object) -> bool:
            if isinstance(value, dict):
                return any(
                    str(key).casefold() in forbidden_keys
                    or contains_forbidden(item)
                    for key, item in value.items()
                )
            if isinstance(value, (list, tuple)):
                return any(contains_forbidden(item) for item in value)
            return False

        if contains_forbidden(self.target_semantic_hint):
            raise ValueError(
                "coarse semantic hints cannot contain database targets or mutation commands"
            )
        return self


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
    CONTEXTUAL_UPDATE = "contextual_update"
    EXPLICIT_REMEMBER = "explicit_remember"
    FORCED = "forced"
    CASUAL = "casual"
    KNOWLEDGE_QUESTION = "knowledge_question"
    OPERATION = "operation"
    HYPOTHETICAL = "hypothetical"
    CONSULTATION_ONLY = "consultation_only"
    NO_DURABLE_SIGNAL = "no_durable_signal"


class MemoryGateRoute(StrEnum):
    """Deterministic routing decision before the semantic memory gate."""

    HARD_DROP = "HARD_DROP"
    HARD_PASS = "HARD_PASS"
    SEMANTIC_REVIEW = "SEMANTIC_REVIEW"
    # ``SEMANTIC_REVIEW`` was the persisted/fixture name used by the first
    # Gate V2 contract.  ``POSSIBLE_MEMORY`` is the clearer semantic name for
    # the same extraction hand-off: the L0 gate believes the turn may contain
    # durable information, but it does not authorize a write.  Keep the enum
    # value aliased so old reports and stored extraction runs remain readable.
    POSSIBLE_MEMORY = "SEMANTIC_REVIEW"
    CONTEXT_PASS = "CONTEXT_PASS"

    @classmethod
    def _missing_(cls, value: object) -> "MemoryGateRoute | None":
        """Accept the new public spelling while reading old/new fixtures."""

        if isinstance(value, str) and value.strip().upper() == "POSSIBLE_MEMORY":
            return cls.SEMANTIC_REVIEW
        return None


class MemorySemanticGateReason(StrEnum):
    """Bounded reason taxonomy returned by the Flash semantic gate."""

    STABLE_FACT = "STABLE_FACT"
    PREFERENCE = "PREFERENCE"
    INTERACTION_PATTERN = "INTERACTION_PATTERN"
    RELATIONSHIP_STATE = "RELATIONSHIP_STATE"
    RELATIONSHIP_CHANGE = "RELATIONSHIP_CHANGE"
    PARTIAL_CHANGE = "PARTIAL_CHANGE"
    USER_BELIEF = "USER_BELIEF"
    PLANNED_EVENT = "PLANNED_EVENT"
    ACTION_INTENT = "ACTION_INTENT"
    ADVICE_OUTCOME = "ADVICE_OUTCOME"
    COMPOUND_MEMORY = "COMPOUND_MEMORY"
    CONTEXT_DEPENDENT_REPLY = "CONTEXT_DEPENDENT_REPLY"
    TRANSIENT = "TRANSIENT"
    SMALL_TALK = "SMALL_TALK"
    NO_MEMORY = "NO_MEMORY"


class CoarseExtraction(BaseModel):
    """Stage 1 output; never an authorization to write memory."""

    model_config = ConfigDict(extra="forbid")

    should_extract: bool
    gate_reason: MemorySemanticGateReason | None = None
    propositions: list[CoarseProposition] = Field(default_factory=list, max_length=12)
    discarded_spans: list[DiscardedSpan] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def fill_safe_gate_reason(self) -> "CoarseExtraction":
        if self.gate_reason is None:
            self.gate_reason = (
                MemorySemanticGateReason.COMPOUND_MEMORY
                if self.should_extract
                else MemorySemanticGateReason.NO_MEMORY
            )
        return self


# Transitional aliases keep the contract easy to consume without creating a
# second enum with different identity or persisted values.
MemoryL0Route = MemoryGateRoute
MemoryGateL0Route = MemoryGateRoute
SemanticGateReason = MemorySemanticGateReason


class MemoryGateDecision(BaseModel):
    should_extract: bool
    reason: MemoryGateReason
    l0_route: MemoryGateRoute | None = None
    # Human-readable route spelling.  ``l0_route`` intentionally retains the
    # legacy enum value for persisted compatibility; new callers can use this
    # field (or ``route_label`` below) to distinguish POSSIBLE_MEMORY from a
    # legacy SEMANTIC_REVIEW payload.
    l0_route_label: str | None = None
    l0_semantic_hint: MemorySemanticGateReason | None = None
    semantic_gate_should_extract: bool | None = None
    semantic_gate_reason: MemorySemanticGateReason | None = None
    semantic_gate_contract_violation: bool = False
    semantic_gate_contract_violation_reason: str | None = None
    extraction_warning: str | None = None
    pending_memory_context: PendingMemoryContext | None = None
    pending_memory_context_source: str | None = None
    signals: list[str] = Field(default_factory=list)
    matched_rule: str | None = None
    matched_span: str | None = None
    contextual_probe: bool = False
    durable_signal_category: str | None = None
    contextual_signal_category: str | None = None
    history_loaded_for_gate: bool = False
    antecedent_candidate_ids: list[str] = Field(default_factory=list)
    selected_target_memory_id: str | None = None
    target_guard_result: str | None = None
    contextual_update_type: str | None = None

    @property
    def route_label(self) -> str | None:
        """Return the semantic route label without changing old enum values."""

        if self.l0_route_label:
            return self.l0_route_label
        return self.l0_route.value if self.l0_route is not None else None


class ContextualUpdateType(StrEnum):
    DURATION = "duration"
    PERSISTENCE = "persistence"


class ContextualMemoryUpdate(BaseModel):
    """A constrained, user-evidenced patch for an active interaction memory.

    This intentionally does not expose a generic field-update dictionary.  New
    update kinds must be modeled explicitly and validated by both the resolver
    and the persistence layer.
    """

    target_memory_id: str = Field(min_length=1)
    update_type: ContextualUpdateType
    evidence_span: str = Field(min_length=1, max_length=1000)
    temporal_expression: str = Field(min_length=1, max_length=200)
    reference_time: datetime
    target_canonical_predicate: str = Field(min_length=1, max_length=120)
    duration_value: int | None = Field(default=None, ge=1, le=3650)
    duration_unit: str | None = Field(default=None, max_length=20)
    confidence: float = Field(default=0.95, ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_shape(self) -> "ContextualMemoryUpdate":
        if self.update_type == ContextualUpdateType.DURATION:
            if self.duration_value is None or self.duration_unit is None:
                raise ValueError("duration updates require duration_value and duration_unit")
        elif self.duration_value is not None or self.duration_unit is not None:
            raise ValueError("persistence updates cannot carry a duration value")
        return self


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
    extraction_status: str | None = None
    failure_category: str | None = None
    repair_status: str | None = None
    repair_steps: str | None = None
    raw_model_response: str | None = None
    invalid_claim_snapshot: str | None = None
    validation_error: str | None = None
    repair_attempt: str | None = None
    repair_result: str | None = None
    upgrade_reason: str | None = None
    discard_reason: str | None = None
    retry_reason: str | None = None
    # Optional strategy/stage metadata used by shadow evaluations.  These
    # fields are intentionally additive so existing persisted extraction-run
    # JSON remains readable without a Store migration.
    extraction_strategy: str | None = None
    stage: str | None = None
    fallback_used: bool = False
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
    # The V2.1 design document uses a JSON ``type`` discriminator in its
    # examples, while the persisted/production contract calls the same field
    # ``kind``.  Accept the documented alias at the model boundary and map
    # only the bounded kind vocabulary; an unknown value is left intact so
    # Pydantic still fails closed with the normal enum validation error.
    type_alias = normalized.pop("type", None)
    if "kind" not in normalized and type_alias is not None:
        normalized["kind"] = _normalize_memory_kind_alias(type_alias)
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
    # V2.1 interaction events use ``time`` as a payload field, while older
    # claims use the same top-level key as a temporal-shape alias (``point``,
    # ``interval`` or a temporal object).  Preserve event-specific values for
    # the payload contract without changing legacy temporal parsing.
    event_time_payload: object = None
    kind_hint = normalized.get("kind") or normalized.get("memory_kind")
    time_is_legacy_kind = (
        isinstance(time_alias, str)
        and time_alias.casefold().strip() in {item.value for item in TimeKind}
    )
    if (
        isinstance(kind_hint, str)
        and kind_hint.casefold().strip()
        in {"interaction_event", "event", "interaction_episode"}
        and time_alias is not None
        and not isinstance(time_alias, dict)
        and not time_is_legacy_kind
    ):
        event_time_payload = time_alias
        if _looks_like_iso_datetime(time_alias) and "occurred_at" not in normalized:
            normalized["occurred_at"] = time_alias
    if temporal is None and event_time_payload is None:
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
        "domain",
        "dimension",
        "value",
        "preference_domain",
        "preference_dimension",
        "category",
        "temporal_expression",
        "event_status",
        "memory_role",
        "state_scope",
        "state_value",
        "state_dimension",
        "activity_type",
        "event_type",
        "event_category",
        "domain_event_type",
        "action",
        "location",
        "place",
        "venue",
        "where",
        "emotion",
        "feeling",
        "outcome",
        "result",
        "effect",
        "cause",
        "conflict_cause",
        "severity",
        "resolution",
        "source",
        "provenance",
        "origin",
        "evidence_ids",
        "event_ids",
        "source_event_ids",
        "supporting_event_ids",
        "event_markers",
        "milestone_type",
        "time_window",
        "time_range",
        "window",
        "participants",
        "actors",
        "involved_participants",
        "involved_people",
        "relationship_evidence",
        "related_plan_id",
        "completes_plan_id",
    ):
        if key in normalized:
            payload.setdefault(key, normalized[key])
            if key not in {
                "state_dimension",
                "state_value",
                "source_event_ids",
                "supporting_event_ids",
                "event_markers",
            }:
                normalized.pop(key)
    if temporal_expression_alias:
        payload.setdefault("temporal_expression", temporal_expression_alias)
    if event_time_payload is not None:
        payload.setdefault("time", event_time_payload)
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


def _normalize_memory_kind_alias(value: object) -> object:
    if not isinstance(value, str):
        return value
    normalized = value.casefold().strip().replace("-", "_")
    aliases = {
        "fact": MemoryKind.STABLE_FACT.value,
        "stable_fact": MemoryKind.STABLE_FACT.value,
        "preference": MemoryKind.PREFERENCE.value,
        "event": MemoryKind.INTERACTION_EVENT.value,
        "interaction_event": MemoryKind.INTERACTION_EVENT.value,
        "interaction_episode": MemoryKind.INTERACTION_EVENT.value,
        "pattern": MemoryKind.INTERACTION_PATTERN.value,
        "interaction_pattern": MemoryKind.INTERACTION_PATTERN.value,
        "interaction_trend": MemoryKind.INTERACTION_PATTERN.value,
        "plan": MemoryKind.PLANNED_EVENT.value,
        "planned_event": MemoryKind.PLANNED_EVENT.value,
        "pending_event": MemoryKind.PLANNED_EVENT.value,
        "intent": MemoryKind.ACTION_INTENT.value,
        "action_intent": MemoryKind.ACTION_INTENT.value,
        "pending_action": MemoryKind.ACTION_INTENT.value,
        "relationship_state": MemoryKind.RELATIONSHIP_STATE.value,
        "advice_outcome": MemoryKind.ADVICE_OUTCOME.value,
    }
    return aliases.get(normalized, value)


def _synchronize_memory_evolution_metadata(
    memory: Any,
    *,
    mirror_payload: bool,
) -> None:
    """Keep typed V2.2 metadata round-trippable through the legacy payload.

    The Store schema intentionally remains unchanged.  Mirroring the bounded
    fields into ``payload`` lets SQLite and older adapters preserve them while
    callers can use typed attributes on ``MemoryCandidate``/``MemoryItem``.
    """

    payload = dict(memory.payload)
    for field in ("source_event_ids", "supporting_event_ids", "event_markers"):
        values = getattr(memory, field, None)
        if values:
            payload[field] = list(dict.fromkeys(values))
        elif field in payload:
            raw_values = payload[field]
            if isinstance(raw_values, str):
                raw_values = [raw_values]
            if not isinstance(raw_values, list) or any(
                not isinstance(value, str) or not value.strip() for value in raw_values
            ):
                raise ValueError(f"{field} must be a list of non-empty strings")
            setattr(memory, field, list(dict.fromkeys(raw_values)))
    salience = memory.salience
    if salience is None and payload.get("salience") is not None:
        raw_salience = payload["salience"]
        if isinstance(raw_salience, bool):
            raise ValueError("salience must be a number between 0 and 1")
        try:
            salience = float(raw_salience)
        except (TypeError, ValueError) as exc:
            raise ValueError("salience must be a number between 0 and 1") from exc
        if not 0 <= salience <= 1:
            raise ValueError("salience must be a number between 0 and 1")
        memory.salience = salience
    if salience is not None and mirror_payload:
        payload["salience"] = round(float(salience), 4)

    novelty = memory.novelty
    if novelty is None and payload.get("novelty") is not None:
        raw_novelty = payload["novelty"]
        if isinstance(raw_novelty, bool):
            raise ValueError("novelty must be a number between 0 and 1")
        try:
            novelty = float(raw_novelty)
        except (TypeError, ValueError) as exc:
            raise ValueError("novelty must be a number between 0 and 1") from exc
        if not 0 <= novelty <= 1:
            raise ValueError("novelty must be a number between 0 and 1")
        memory.novelty = novelty
    if novelty is not None and mirror_payload:
        payload["novelty"] = round(float(novelty), 4)

    importance_reason = memory.importance_reason
    if importance_reason is None and payload.get("importance_reason") is not None:
        raw_reason = payload["importance_reason"]
        if not isinstance(raw_reason, str) or not raw_reason.strip():
            raise ValueError("importance_reason must be non-empty text")
        importance_reason = raw_reason.strip()
        if len(importance_reason) > 500:
            raise ValueError("importance_reason cannot exceed 500 characters")
        memory.importance_reason = importance_reason
    if importance_reason is not None and mirror_payload:
        payload["importance_reason"] = importance_reason

    if memory.kind != MemoryKind.INTERACTION_PATTERN:
        memory.payload = payload
        return

    raw_state = memory.pattern_state or payload.get("state") or payload.get("pattern_state")
    if getattr(memory, "status", None) == MemoryStatus.SUPERSEDED:
        raw_state = PatternLifecycleState.SUPERSEDED
    if raw_state is None:
        raw_state = PatternLifecycleState.ACTIVE
    try:
        pattern_state = (
            raw_state
            if isinstance(raw_state, PatternLifecycleState)
            else PatternLifecycleState(str(raw_state).casefold().strip())
        )
    except ValueError as exc:
        raise ValueError(
            "interaction_pattern state must be active, weakening, or superseded"
        ) from exc

    positive_ids = _normalize_memory_evidence_ids(
        [
            *memory.positive_evidence_ids,
            *_payload_evidence_ids(payload.get("positive_evidence_ids")),
        ],
        field="positive_evidence_ids",
    )
    if not positive_ids:
        positive_ids = _normalize_memory_evidence_ids(
            _payload_evidence_ids(payload.get("evidence_ids")),
            field="positive_evidence_ids",
        )
    negative_ids = _normalize_memory_evidence_ids(
        [
            *memory.negative_evidence_ids,
            *_payload_evidence_ids(payload.get("negative_evidence_ids")),
        ],
        field="negative_evidence_ids",
    )
    overlap = set(positive_ids) & set(negative_ids)
    if overlap:
        raise ValueError("pattern evidence cannot be both positive and negative")

    memory.pattern_state = pattern_state
    memory.positive_evidence_ids = positive_ids
    memory.negative_evidence_ids = negative_ids
    if mirror_payload:
        payload["state"] = pattern_state.value
        payload["positive_evidence_ids"] = positive_ids
        payload["negative_evidence_ids"] = negative_ids
        payload.pop("pattern_state", None)
    memory.payload = payload


def _payload_evidence_ids(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError("pattern evidence IDs must be a list")
    return list(value)


def _normalize_memory_evidence_ids(values: list[object], *, field: str) -> list[str]:
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 160:
            raise ValueError(f"{field} contains invalid IDs")
        normalized.append(value.strip())
    unique = list(dict.fromkeys(normalized))
    if len(unique) > 20:
        unique = unique[-20:]
    return unique


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
    salience: float | None = Field(default=None, ge=0, le=1)
    novelty: float | None = Field(default=None, ge=0, le=1)
    importance_reason: str | None = Field(default=None, max_length=500)
    perspective: MemoryPerspective = MemoryPerspective.USER_REPORTED
    epistemic_status: EpistemicStatus = EpistemicStatus.CONFIRMED
    confidence: float = Field(default=0.8, ge=0, le=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    source_event_ids: list[str] = Field(default_factory=list, max_length=20)
    supporting_event_ids: list[str] = Field(default_factory=list, max_length=20)
    event_markers: list[str] = Field(default_factory=list, max_length=8)
    supersedes_id: str | None = None
    raw_predicate: str | None = Field(default=None, max_length=120)
    predicate_type: PredicateType = PredicateType.CUSTOM
    canonical_predicate: str | None = Field(default=None, max_length=120)
    custom_predicate: str | None = Field(default=None, max_length=120)
    state_dimension: str | None = Field(default=None, max_length=120)
    state_value: str | None = Field(default=None, max_length=120)
    explicitness: EvidenceExplicitness = EvidenceExplicitness.STRONGLY_IMPLIED
    requires_inference: bool = False
    admission_score: float | None = Field(default=None, ge=0, le=1)
    admission_decision: AdmissionDecision | None = None
    claim_relation: ClaimRelation | None = None
    lifecycle_review_required: bool = False
    prompt_version: str | None = Field(default=None, max_length=80)
    extractor_model: str | None = Field(default=None, max_length=160)
    verifier_model: str | None = Field(default=None, max_length=160)
    pattern_state: PatternLifecycleState | None = None
    positive_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    negative_evidence_ids: list[str] = Field(default_factory=list, max_length=20)

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
        if (
            self.perspective == MemoryPerspective.USER_BELIEF
            and self.epistemic_status == EpistemicStatus.CONFIRMED
        ):
            self.epistemic_status = EpistemicStatus.UNCERTAIN
        elif (
            self.perspective == MemoryPerspective.MODEL_INFERRED
            and self.epistemic_status
            in {EpistemicStatus.CONFIRMED, EpistemicStatus.UNCERTAIN}
        ):
            self.epistemic_status = EpistemicStatus.HYPOTHESIS
        _synchronize_memory_evolution_metadata(self, mirror_payload=True)
        return self


class AtomicClaim(BaseModel):
    model_config = ConfigDict(extra="ignore")

    claim_id: str = Field(min_length=1, max_length=80)
    kind: MemoryKind
    subject: str = Field(min_length=1, max_length=80)
    predicate: str = Field(default="", max_length=120)
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
    salience: float | None = Field(default=None, ge=0, le=1)
    novelty: float | None = Field(default=None, ge=0, le=1)
    importance_reason: str | None = Field(default=None, max_length=500)
    perspective: MemoryPerspective = MemoryPerspective.USER_REPORTED
    epistemic_status: EpistemicStatus = EpistemicStatus.CONFIRMED
    confidence: float = Field(default=0.8, ge=0, le=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    source_event_ids: list[str] = Field(default_factory=list, max_length=20)
    supporting_event_ids: list[str] = Field(default_factory=list, max_length=20)
    event_markers: list[str] = Field(default_factory=list, max_length=8)
    supersedes_id: str | None = None
    raw_predicate: str | None = Field(default=None, max_length=120)
    predicate_type: PredicateType = PredicateType.CUSTOM
    canonical_predicate: str | None = Field(default=None, max_length=120)
    custom_predicate: str | None = Field(default=None, max_length=120)
    state_dimension: str | None = Field(default=None, max_length=120)
    state_value: str | None = Field(default=None, max_length=120)
    explicitness: EvidenceExplicitness = EvidenceExplicitness.STRONGLY_IMPLIED
    requires_inference: bool = False
    prompt_version: str | None = Field(default=None, max_length=80)
    extractor_model: str | None = Field(default=None, max_length=160)
    verifier_model: str | None = Field(default=None, max_length=160)
    pattern_state: PatternLifecycleState | None = None
    positive_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    negative_evidence_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="before")
    @classmethod
    def flatten_temporal_object(cls, value: object) -> object:
        return _normalize_memory_input(value)

    @model_validator(mode="after")
    def validate_time_shape(self) -> "AtomicClaim":
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("period_start cannot be later than period_end")
        if not self.predicate:
            # Raw extraction is allowed to carry only the source predicate.
            # Canonical/custom resolution remains the normalizer's authority.
            replacement = self.raw_predicate or self.canonical_predicate or self.custom_predicate
            if not replacement:
                raise ValueError("predicate or canonical/custom predicate is required")
            self.predicate = replacement
        if (
            self.perspective == MemoryPerspective.USER_BELIEF
            and self.epistemic_status == EpistemicStatus.CONFIRMED
        ):
            self.epistemic_status = EpistemicStatus.UNCERTAIN
        elif (
            self.perspective == MemoryPerspective.MODEL_INFERRED
            and self.epistemic_status
            in {EpistemicStatus.CONFIRMED, EpistemicStatus.UNCERTAIN}
        ):
            self.epistemic_status = EpistemicStatus.HYPOTHESIS
        # AtomicClaim is the raw extraction boundary.  Populate typed fields
        # from provider payload aliases without adding deterministic defaults
        # back into the raw payload; MemoryCandidate performs that persistence
        # mirroring after claim conversion and normalization.
        _synchronize_memory_evolution_metadata(self, mirror_payload=False)
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
            salience=self.salience,
            novelty=self.novelty,
            importance_reason=self.importance_reason,
            perspective=self.perspective,
            epistemic_status=self.epistemic_status,
            confidence=self.confidence,
            payload=payload,
            source_event_ids=self.source_event_ids,
            supporting_event_ids=self.supporting_event_ids,
            event_markers=self.event_markers,
            supersedes_id=self.supersedes_id,
            raw_predicate=self.raw_predicate or self.predicate,
            predicate_type=self.predicate_type,
            canonical_predicate=self.canonical_predicate,
            custom_predicate=self.custom_predicate,
            state_dimension=self.state_dimension,
            state_value=self.state_value,
            explicitness=self.explicitness,
            requires_inference=self.requires_inference,
            prompt_version=self.prompt_version,
            extractor_model=self.extractor_model,
            verifier_model=self.verifier_model,
            pattern_state=self.pattern_state,
            positive_evidence_ids=self.positive_evidence_ids,
            negative_evidence_ids=self.negative_evidence_ids,
        )


class AtomicExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Optional only for parsing pre-Gate-V2 fixtures and stored diagnostics.
    # New model calls are instructed to return both fields in the same response.
    should_extract: bool | None = None
    gate_reason: MemorySemanticGateReason | None = None
    claims: list[AtomicClaim] = Field(default_factory=list, max_length=12)
    discarded_spans: list[DiscardedSpan] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_semantic_gate_contract(self) -> "AtomicExtraction":
        fields_present = self.should_extract is not None or self.gate_reason is not None
        if not fields_present:
            return self
        if self.should_extract is None or self.gate_reason is None:
            raise ValueError(
                "should_extract and gate_reason must be provided together"
            )
        negative_reasons = {
            MemorySemanticGateReason.TRANSIENT,
            MemorySemanticGateReason.SMALL_TALK,
            MemorySemanticGateReason.NO_MEMORY,
        }
        if self.should_extract and self.gate_reason in negative_reasons:
            raise ValueError(
                "should_extract=true requires a memory-positive gate_reason"
            )
        if not self.should_extract and self.gate_reason not in negative_reasons:
            raise ValueError(
                "should_extract=false requires a non-memory gate_reason"
            )
        return self


class MemoryItem(MemoryCandidate):
    id: str
    user_id: str
    relationship_id: str
    status: MemoryStatus = MemoryStatus.PROPOSED
    source_message_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_used_at: datetime | None = None
    last_seen_at: datetime | None = None
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
    salience: float | None = Field(default=None, ge=0, le=1)
    novelty: float | None = Field(default=None, ge=0, le=1)
    importance_reason: str | None = Field(default=None, max_length=500)
    perspective: MemoryPerspective
    epistemic_status: EpistemicStatus
    confidence: float
    status: MemoryStatus
    payload: dict[str, Any] = Field(default_factory=dict)
    source_event_ids: list[str] = Field(default_factory=list, max_length=20)
    supporting_event_ids: list[str] = Field(default_factory=list, max_length=20)
    event_markers: list[str] = Field(default_factory=list, max_length=8)
    attention_reason: str | None = None
    predicate_type: PredicateType = PredicateType.CUSTOM
    canonical_predicate: str | None = None
    custom_predicate: str | None = None
    state_dimension: str | None = None
    state_value: str | None = None
    explicitness: EvidenceExplicitness = EvidenceExplicitness.STRONGLY_IMPLIED
    admission_score: float | None = None
    admission_decision: AdmissionDecision | None = None
    claim_relation: ClaimRelation | None = None
    lifecycle_review_required: bool = False
    pattern_state: PatternLifecycleState | None = None
    positive_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    negative_evidence_ids: list[str] = Field(default_factory=list, max_length=20)

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
                    "salience",
                    "novelty",
                    "importance_reason",
                    "perspective",
                    "epistemic_status",
                    "confidence",
                    "status",
                    "payload",
                    "source_event_ids",
                    "supporting_event_ids",
                    "event_markers",
                    "predicate_type",
                    "canonical_predicate",
                    "custom_predicate",
                    "state_dimension",
                    "state_value",
                    "explicitness",
                    "admission_score",
                    "admission_decision",
                    "claim_relation",
                    "lifecycle_review_required",
                    "pattern_state",
                    "positive_evidence_ids",
                    "negative_evidence_ids",
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
    contextual_updated_memory_ids: list[str] = Field(default_factory=list)
    discarded_spans: list[DiscardedSpan] = Field(default_factory=list)
    skipped_low_confidence: int = 0
    rejected_by_policy: int = 0
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


def normalize_candidate_predicate(candidate: MemoryCandidate) -> MemoryCandidate:
    payload = dict(candidate.payload)
    if candidate.kind == MemoryKind.PREFERENCE:
        # Memory V2.1 expresses typed preferences as
        # ``domain + dimension + value``.  The established persistence and
        # dedupe contract uses ``preference`` for the value, so reconcile the
        # two representations once at the normalization boundary.
        value = payload.get("value")
        if "preference" not in payload and isinstance(value, str) and value.strip():
            payload["preference"] = value.strip()

    semantic_payload = dict(payload)
    semantic_payload.setdefault("summary", candidate.summary)
    semantic_payload.setdefault("original_text", candidate.original_text)
    semantic_payload.setdefault("evidence_spans", candidate.evidence_spans)
    normalized = normalize_predicate(
        kind=candidate.kind,
        raw_predicate=candidate.raw_predicate or candidate.payload.get("predicate"),
        canonical_predicate=candidate.canonical_predicate,
        custom_predicate=candidate.custom_predicate,
        predicate_type=candidate.predicate_type,
        payload=semantic_payload,
    )
    updates: dict[str, Any] = {
        "raw_predicate": normalized.raw_predicate,
        "predicate_type": PredicateType(normalized.predicate_type),
        "canonical_predicate": normalized.canonical_predicate,
        "custom_predicate": normalized.custom_predicate,
        "state_dimension": normalized.state_dimension,
        "state_value": normalized.state_value,
    }
    if normalized.predicate_type == PredicateType.CANONICAL.value:
        if normalized.canonical_predicate and normalized.canonical_predicate.startswith(
            "interaction."
        ):
            metric = normalized.canonical_predicate.removeprefix("interaction.")
            payload["metric"] = metric
        if candidate.kind == MemoryKind.PREFERENCE:
            preference_hint = payload.get("preference_type_hint")
            if isinstance(preference_hint, str) and preference_hint.strip():
                payload["preference_type"] = preference_hint.strip()
    if normalized.state_dimension is not None:
        lifecycle_dimension = normalize_state_dimension(normalized.state_dimension)
        lifecycle_value = normalize_state_value(
            lifecycle_dimension,
            normalized.state_value,
        )
        authoritative_dimension = lifecycle_dimension or normalized.state_dimension
        authoritative_value = lifecycle_value or normalized.state_value
        updates["state_dimension"] = authoritative_dimension
        updates["state_value"] = authoritative_value
        payload["state_dimension"] = authoritative_dimension
        if authoritative_value is not None:
            payload["state_value"] = authoritative_value
    if normalized.state_value is not None:
        payload["state_value"] = updates.get("state_value", normalized.state_value)
    if payload != candidate.payload:
        updates["payload"] = payload
    if normalized.predicate_type == PredicateType.CUSTOM.value:
        updates["lifecycle_review_required"] = True
    return candidate.model_copy(update=updates)


def memory_dedupe_key(candidate: MemoryCandidate) -> str:
    normalized = memory_dedupe_identity(candidate)
    return sha256(normalized.encode("utf-8")).hexdigest()


def memory_dedupe_identity(candidate: MemoryCandidate) -> str:
    parts = _memory_identity_parts(candidate)
    return "|".join(_normalize_key_part(part) for part in parts)


def memory_epistemic_identity_bucket(candidate: MemoryCandidate) -> str | None:
    """Return a non-default identity suffix for belief/inference memories.

    Confirmed user reports deliberately keep the legacy identity shape so
    existing persisted facts continue to deduplicate after the V2.2 schema
    migration.  Non-factual propositions receive their own stable namespace,
    preventing an otherwise identical belief or inference from merging into a
    reported fact before relation governance can run.
    """

    if (
        candidate.perspective == MemoryPerspective.USER_REPORTED
        and candidate.epistemic_status == EpistemicStatus.CONFIRMED
    ):
        return None
    return f"{candidate.perspective.value}:{candidate.epistemic_status.value}"


def _memory_identity_parts(candidate: MemoryCandidate) -> tuple[str, ...]:
    kind = candidate.kind.value
    epistemic_bucket = memory_epistemic_identity_bucket(candidate)
    if epistemic_bucket is not None:
        kind = f"{kind}@{epistemic_bucket}"
    subject = candidate.subject
    payload = candidate.payload
    predicate = normalize_predicate(
        kind=candidate.kind,
        raw_predicate=candidate.raw_predicate or payload.get("predicate"),
        canonical_predicate=candidate.canonical_predicate,
        custom_predicate=candidate.custom_predicate,
        predicate_type=candidate.predicate_type,
        payload=payload,
    )
    predicate_name = predicate.canonical_predicate or f"custom:{predicate.custom_predicate}"
    if payload.get("contextual_update_type") == "correction":
        return (
            kind,
            subject,
            predicate_name,
            "correction",
            str(payload.get("correction_target_memory_id") or ""),
            str(payload.get("correction_type") or ""),
            str(payload.get("correction_value") or ""),
        )

    if candidate.kind == MemoryKind.PREFERENCE:
        preference = payload.get("preference")
        preference_type = payload.get("preference_type")
        if isinstance(preference, str):
            polarity = (
                "negative"
                if str(preference_type or "").casefold()
                in {"avoid", "allergy", "restriction", "dislike", "forbid"}
                else "positive"
            )
            return (
                kind,
                subject,
                predicate_name,
                normalize_preference_value(preference),
                polarity,
            )

    if candidate.kind == MemoryKind.INTERACTION_PATTERN:
        metric = payload.get("metric")
        if isinstance(metric, str):
            state = _canonical_pattern_state(payload)
            return (kind, subject, predicate_name, state)

    if candidate.kind == MemoryKind.RELATIONSHIP_STATE:
        dimension = candidate.state_dimension or predicate.state_dimension
        value = candidate.state_value or predicate.state_value
        if isinstance(dimension, str) and isinstance(value, str):
            return (kind, subject, "state", dimension, value)

    # Registered Stable Fact dimensions use their normalized value for
    # identity even when the extractor supplied the value through
    # ``payload.value`` rather than the legacy ``payload.object`` field.
    # Custom facts never receive a profile dimension, so they retain the
    # existing open-world identity and conservative relation behavior.
    if (
        candidate.kind == MemoryKind.STABLE_FACT
        and isinstance(predicate.state_dimension, str)
        and predicate.state_dimension.startswith("profile.")
        and isinstance(predicate.state_value, str)
    ):
        return (
            kind,
            subject,
            predicate_name,
            predicate.state_value,
        )

    # ``contact.status`` can legitimately arrive as a legacy stable fact
    # before lifecycle normalization upgrades it. Its registered state value
    # must still participate in identity; otherwise opposite contact states
    # dedupe as SAME and shadow the governed transition. Keep this scoped so
    # ordinary event/plan identity (including temporal evidence) is unchanged.
    if (
        candidate.kind == MemoryKind.STABLE_FACT
        and predicate.canonical_predicate == "contact.status"
        and isinstance(predicate.state_dimension, str)
        and isinstance(predicate.state_value, str)
    ):
        return (
            kind,
            subject,
            "state",
            predicate.state_dimension,
            predicate.state_value,
        )

    if predicate_name:
        object_value = _canonical_object(
            payload.get("object"),
            kind=candidate.kind,
            subject=subject,
            predicate=predicate_name,
        )
        if candidate.kind == MemoryKind.PLANNED_EVENT:
            temporal_key = _temporal_identity(candidate)
            plan_id = (
                ""
                if payload.get("plan_id_generated") is True
                else str(payload.get("plan_id") or "")
            )
            return (kind, subject, predicate_name, object_value, plan_id, temporal_key)
        if candidate.kind == MemoryKind.ACTION_INTENT:
            return (kind, subject, predicate_name, object_value)
        if candidate.kind in {MemoryKind.INTERACTION_EVENT, MemoryKind.ADVICE_OUTCOME}:
            temporal_key = _temporal_identity(candidate)
            evidence_key = "|".join(candidate.evidence_spans)
            event_id = str(payload.get("event_id") or "")
            if event_id:
                return (kind, subject, predicate_name, object_value, event_id)
            return (
                kind,
                subject,
                predicate_name,
                object_value,
                temporal_key,
                evidence_key,
            )
        return (kind, subject, predicate_name, object_value)

    return (kind, subject, candidate.summary)


def _canonical_object(
    value: object,
    *,
    kind: MemoryKind,
    subject: str,
    predicate: str,
) -> str:
    normalized = _normalize_key_part(str(value or "unknown")).replace(" ", "_")
    if (
        kind == MemoryKind.STABLE_FACT
        and subject.casefold() == "user"
        and predicate in {"likes", "relationship.romantic_interest"}
    ):
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
    raw = (
        payload.get("current")
        or payload.get("direction")
        or payload.get("frequency")
        or payload.get("state_value")
    )
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

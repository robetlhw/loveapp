from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from math import prod
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from loveapp.domain.memory import (
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    RelationshipImpact,
    utc_now,
)


class RelationshipEvidenceDimension(StrEnum):
    FAMILIARITY = "familiarity"
    TRUST = "trust"
    INVESTMENT = "investment"
    CONFLICT = "conflict"
    BOUNDARY = "boundary"


class EvidenceDirection(StrEnum):
    SUPPORT = "support"
    OPPOSE = "oppose"


class EvidenceProvenance(StrEnum):
    EXTRACTED = "extracted"
    EXPLICIT_STATE = "explicit_state"
    LEGACY_STANDARDIZER = "legacy_standardizer"


class RelationshipEvidenceDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: RelationshipEvidenceDimension
    direction: EvidenceDirection
    strength: float = Field(ge=0.05, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    rationale: str = Field(default="model_declared", min_length=1, max_length=120)


class RelationshipEvidenceSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    source_memory_id: str
    source_message_id: str | None = None
    dimension: RelationshipEvidenceDimension
    direction: EvidenceDirection
    strength: float = Field(ge=0.05, le=1)
    confidence: float = Field(ge=0, le=1)
    observed_at: datetime
    expires_at: datetime | None = None
    provenance: EvidenceProvenance
    rationale: str
    summary: str


class RelationshipDimensionProjection(BaseModel):
    dimension: RelationshipEvidenceDimension
    state: str
    score: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    supporting_evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    opposing_evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    independent_source_count: int = Field(default=0, ge=0)


class RelationshipEvidenceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    familiarity: Literal["unknown", "unfamiliar", "low", "moderate", "high"] = "unknown"
    trust: Literal["unknown", "low", "moderate", "high"] = "unknown"
    investment: Literal["unknown", "low", "mixed", "high"] = "unknown"
    conflict_status: Literal[
        "unknown", "active", "cooling", "repairing", "resolved"
    ] = "unknown"
    boundary_status: Literal["unknown", "clear", "at_risk", "explicit"] = "unknown"
    coverage: Literal["partial"] = "partial"
    projections: list[RelationshipDimensionProjection] = Field(
        default_factory=list,
        max_length=5,
    )
    evidence: list[RelationshipEvidenceSignal] = Field(default_factory=list, max_length=15)
    supporting_signals: list[str] = Field(default_factory=list, max_length=8)

    @property
    def supports_low_pressure_progression(self) -> bool:
        if self.boundary_status in {"at_risk", "explicit"}:
            return False
        familiarity = self.projection_for(RelationshipEvidenceDimension.FAMILIARITY)
        trust = self.projection_for(RelationshipEvidenceDimension.TRUST)
        investment = self.projection_for(RelationshipEvidenceDimension.INVESTMENT)
        return (
            (
                self.investment in {"mixed", "high"}
                and investment is not None
                and investment.confidence >= 0.45
            )
            or (
                self.familiarity in {"moderate", "high"}
                and familiarity is not None
                and familiarity.confidence >= 0.45
                and self.trust in {"moderate", "high"}
                and trust is not None
                and trust.confidence >= 0.45
            )
        )

    @property
    def requires_deescalation(self) -> bool:
        projection = self.projection_for(RelationshipEvidenceDimension.CONFLICT)
        return (
            self.conflict_status in {"active", "cooling"}
            and projection is not None
            and projection.confidence >= 0.45
        )

    def projection_for(
        self,
        dimension: RelationshipEvidenceDimension,
    ) -> RelationshipDimensionProjection | None:
        return next(
            (item for item in self.projections if item.dimension == dimension),
            None,
        )


@dataclass(frozen=True)
class _EvidenceTemplate:
    dimension: RelationshipEvidenceDimension
    direction: EvidenceDirection
    strength: float
    confidence: float
    rationale: str


_PRIVATE_INTERACTION_TEMPLATES = (
    _EvidenceTemplate(
        RelationshipEvidenceDimension.FAMILIARITY,
        EvidenceDirection.SUPPORT,
        0.65,
        0.9,
        "private_shared_interaction",
    ),
    _EvidenceTemplate(
        RelationshipEvidenceDimension.TRUST,
        EvidenceDirection.SUPPORT,
        0.8,
        0.85,
        "private_access_accepted",
    ),
    _EvidenceTemplate(
        RelationshipEvidenceDimension.INVESTMENT,
        EvidenceDirection.SUPPORT,
        0.4,
        0.75,
        "positive_participation",
    ),
)

_SHARED_DATE_TEMPLATES = (
    _EvidenceTemplate(
        RelationshipEvidenceDimension.FAMILIARITY,
        EvidenceDirection.SUPPORT,
        0.45,
        0.85,
        "completed_shared_date",
    ),
    _EvidenceTemplate(
        RelationshipEvidenceDimension.INVESTMENT,
        EvidenceDirection.SUPPORT,
        0.35,
        0.75,
        "accepted_shared_time",
    ),
)

_CONFLICT_TEMPLATES = (
    _EvidenceTemplate(
        RelationshipEvidenceDimension.CONFLICT,
        EvidenceDirection.SUPPORT,
        0.85,
        0.9,
        "reported_conflict",
    ),
)

_PRIVATE_ACTIVITY_TYPES = frozenset(
    {"home_dinner", "home_visit", "private_home_visit", "visit_at_home"}
)
_CONFLICT_ACTIVITY_TYPES = frozenset(
    {"argument", "conflict", "disagreement", "fight", "quarrel"}
)
_PARTNER_INITIATION_VALUES = frozenset(
    {"partner", "partner_initiates", "partner_initiated", "toward_partner"}
)
_USER_ONLY_INITIATION_VALUES = frozenset(
    {"one_sided", "partner_never_initiates", "user_always_initiates", "user_initiated"}
)
_HALF_LIFE_DAYS = {
    RelationshipEvidenceDimension.FAMILIARITY: 365.0,
    RelationshipEvidenceDimension.TRUST: 120.0,
    RelationshipEvidenceDimension.INVESTMENT: 45.0,
    RelationshipEvidenceDimension.CONFLICT: 14.0,
    RelationshipEvidenceDimension.BOUNDARY: 180.0,
}


def normalize_evidence_declarations(
    raw: object,
    *,
    claim_confidence: float,
) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    declarations: list[RelationshipEvidenceDeclaration] = []
    seen: set[tuple[RelationshipEvidenceDimension, EvidenceDirection]] = set()
    for value in raw[:10]:
        if not isinstance(value, dict):
            continue
        normalized = dict(value)
        normalized["dimension"] = _normalize_dimension(normalized.get("dimension"))
        normalized["direction"] = _normalize_direction(normalized.get("direction"))
        try:
            declaration = RelationshipEvidenceDeclaration.model_validate(normalized)
        except ValidationError:
            continue
        key = (declaration.dimension, declaration.direction)
        if key in seen:
            continue
        seen.add(key)
        declared_confidence = (
            declaration.confidence
            if declaration.confidence is not None
            else claim_confidence
        )
        confidence = min(declared_confidence, claim_confidence)
        declarations.append(declaration.model_copy(update={"confidence": confidence}))
    return [item.model_dump(mode="json") for item in declarations]


def standardize_relationship_evidence(
    memories: list[MemoryItem] | tuple[MemoryItem, ...],
    *,
    reference_time: datetime | None = None,
) -> list[RelationshipEvidenceSignal]:
    now = _as_aware(reference_time or utc_now())
    signals: list[RelationshipEvidenceSignal] = []
    for memory in memories:
        if memory.status not in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}:
            continue
        if memory.expires_at is not None and _as_aware(memory.expires_at) <= now:
            continue

        declarations = _declarations_from_memory(memory)
        explicit_dimensions = {item.dimension for item in declarations}
        signals.extend(
            _signal_from_declaration(memory, item, EvidenceProvenance.EXTRACTED)
            for item in declarations
        )

        state_declarations = _state_declarations(memory)
        for declaration in state_declarations:
            if declaration.dimension in explicit_dimensions:
                continue
            explicit_dimensions.add(declaration.dimension)
            signals.append(
                _signal_from_declaration(
                    memory,
                    declaration,
                    EvidenceProvenance.EXPLICIT_STATE,
                )
            )

        for template in _legacy_templates(memory):
            if template.dimension in explicit_dimensions:
                continue
            signals.append(_signal_from_template(memory, template))
    return _deduplicate_exact_signals(signals)


def project_relationship_evidence(
    memories: list[MemoryItem] | tuple[MemoryItem, ...],
    *,
    reference_time: datetime | None = None,
) -> RelationshipEvidenceProfile:
    now = _as_aware(reference_time or utc_now())
    standardized = standardize_relationship_evidence(memories, reference_time=now)
    return project_standardized_relationship_evidence(
        standardized,
        reference_time=now,
    )


def project_standardized_relationship_evidence(
    standardized: list[RelationshipEvidenceSignal],
    *,
    reference_time: datetime | None = None,
) -> RelationshipEvidenceProfile:
    now = _as_aware(reference_time or utc_now())
    independent = _deduplicate_correlated_signals(standardized, now)
    projections = [
        _project_dimension(dimension, independent, now)
        for dimension in RelationshipEvidenceDimension
    ]
    by_dimension = {item.dimension: item for item in projections}
    ranked_evidence = sorted(
        independent,
        key=lambda item: _signal_rank(item, now),
        reverse=True,
    )[:15]
    supporting_signals: list[str] = []
    for signal in ranked_evidence:
        if signal.direction != EvidenceDirection.SUPPORT:
            continue
        if signal.summary not in supporting_signals:
            supporting_signals.append(signal.summary)
        if len(supporting_signals) == 8:
            break
    return RelationshipEvidenceProfile(
        familiarity=by_dimension[RelationshipEvidenceDimension.FAMILIARITY].state,
        trust=by_dimension[RelationshipEvidenceDimension.TRUST].state,
        investment=by_dimension[RelationshipEvidenceDimension.INVESTMENT].state,
        conflict_status=by_dimension[RelationshipEvidenceDimension.CONFLICT].state,
        boundary_status=by_dimension[RelationshipEvidenceDimension.BOUNDARY].state,
        projections=projections,
        evidence=ranked_evidence,
        supporting_signals=supporting_signals,
    )


def _declarations_from_memory(
    memory: MemoryItem,
) -> list[RelationshipEvidenceDeclaration]:
    normalized = normalize_evidence_declarations(
        memory.payload.get("relationship_evidence"),
        claim_confidence=memory.confidence,
    )
    return [RelationshipEvidenceDeclaration.model_validate(item) for item in normalized]


def _state_declarations(memory: MemoryItem) -> list[RelationshipEvidenceDeclaration]:
    if memory.kind != MemoryKind.RELATIONSHIP_STATE:
        return []
    dimension = str(memory.payload.get("state_dimension") or "").casefold()
    value = str(memory.payload.get("state_value") or "").casefold()
    mapping: dict[
        tuple[str, str],
        tuple[RelationshipEvidenceDimension, EvidenceDirection, float],
    ] = {
        ("relationship_familiarity", "unfamiliar"): (
            RelationshipEvidenceDimension.FAMILIARITY,
            EvidenceDirection.OPPOSE,
            0.9,
        ),
        ("relationship_familiarity", "low"): (
            RelationshipEvidenceDimension.FAMILIARITY,
            EvidenceDirection.SUPPORT,
            0.22,
        ),
        ("relationship_familiarity", "moderate"): (
            RelationshipEvidenceDimension.FAMILIARITY,
            EvidenceDirection.SUPPORT,
            0.6,
        ),
        ("relationship_familiarity", "high"): (
            RelationshipEvidenceDimension.FAMILIARITY,
            EvidenceDirection.SUPPORT,
            0.9,
        ),
        ("interaction_reciprocity", "low"): (
            RelationshipEvidenceDimension.INVESTMENT,
            EvidenceDirection.OPPOSE,
            0.75,
        ),
        ("interaction_reciprocity", "mixed"): (
            RelationshipEvidenceDimension.INVESTMENT,
            EvidenceDirection.SUPPORT,
            0.5,
        ),
        ("interaction_reciprocity", "high"): (
            RelationshipEvidenceDimension.INVESTMENT,
            EvidenceDirection.SUPPORT,
            0.9,
        ),
        ("conflict_status", "active"): (
            RelationshipEvidenceDimension.CONFLICT,
            EvidenceDirection.SUPPORT,
            0.95,
        ),
        ("conflict_status", "cooling"): (
            RelationshipEvidenceDimension.CONFLICT,
            EvidenceDirection.SUPPORT,
            0.55,
        ),
        ("conflict_status", "repairing"): (
            RelationshipEvidenceDimension.CONFLICT,
            EvidenceDirection.SUPPORT,
            0.35,
        ),
        ("conflict_status", "resolved"): (
            RelationshipEvidenceDimension.CONFLICT,
            EvidenceDirection.OPPOSE,
            0.95,
        ),
    }
    resolved = mapping.get((dimension, value))
    if resolved is None:
        return []
    evidence_dimension, direction, strength = resolved
    return [
        RelationshipEvidenceDeclaration(
            dimension=evidence_dimension,
            direction=direction,
            strength=strength,
            confidence=memory.confidence,
            rationale=f"explicit_state:{dimension}:{value}",
        )
    ]


def _legacy_templates(memory: MemoryItem) -> tuple[_EvidenceTemplate, ...]:
    payload = memory.payload
    activity_type = str(
        payload.get("activity_type") or payload.get("event_type") or ""
    ).casefold()
    if memory.kind == MemoryKind.INTERACTION_EVENT:
        if activity_type in _PRIVATE_ACTIVITY_TYPES:
            return _PRIVATE_INTERACTION_TEMPLATES
        if activity_type in _CONFLICT_ACTIVITY_TYPES:
            return _CONFLICT_TEMPLATES
        if payload.get("date_sequence") is not None:
            return _SHARED_DATE_TEMPLATES

    metric = str(payload.get("metric") or "").casefold()
    values = {
        str(payload.get(key) or "").casefold()
        for key in ("initiated_by", "current", "direction", "baseline")
    }
    if metric in {"contact_initiation", "initiation_balance"} or payload.get(
        "initiated_by"
    ) is not None:
        if values & _PARTNER_INITIATION_VALUES:
            return (
                _EvidenceTemplate(
                    RelationshipEvidenceDimension.INVESTMENT,
                    EvidenceDirection.SUPPORT,
                    0.65,
                    0.85,
                    "partner_initiated_interaction",
                ),
            )
        if values & _USER_ONLY_INITIATION_VALUES:
            return (
                _EvidenceTemplate(
                    RelationshipEvidenceDimension.INVESTMENT,
                    EvidenceDirection.OPPOSE,
                    0.65,
                    0.85,
                    "one_sided_initiation",
                ),
            )
    if metric == "emotional_disclosure":
        return (
            _EvidenceTemplate(
                RelationshipEvidenceDimension.TRUST,
                EvidenceDirection.SUPPORT,
                0.6,
                0.8,
                "personal_disclosure",
            ),
        )
    if (
        memory.kind == MemoryKind.INTERACTION_EVENT
        and memory.relationship_impact == RelationshipImpact.IMPROVING
    ):
        return (
            _EvidenceTemplate(
                RelationshipEvidenceDimension.INVESTMENT,
                EvidenceDirection.SUPPORT,
                0.25,
                0.65,
                "positive_observed_interaction",
            ),
        )
    return ()


def _signal_from_declaration(
    memory: MemoryItem,
    declaration: RelationshipEvidenceDeclaration,
    provenance: EvidenceProvenance,
) -> RelationshipEvidenceSignal:
    return _make_signal(
        memory,
        dimension=declaration.dimension,
        direction=declaration.direction,
        strength=declaration.strength,
        confidence=min(
            declaration.confidence
            if declaration.confidence is not None
            else memory.confidence,
            memory.confidence,
        ),
        provenance=provenance,
        rationale=declaration.rationale,
    )


def _signal_from_template(
    memory: MemoryItem,
    template: _EvidenceTemplate,
) -> RelationshipEvidenceSignal:
    return _make_signal(
        memory,
        dimension=template.dimension,
        direction=template.direction,
        strength=template.strength,
        confidence=min(template.confidence, memory.confidence),
        provenance=EvidenceProvenance.LEGACY_STANDARDIZER,
        rationale=template.rationale,
    )


def _make_signal(
    memory: MemoryItem,
    *,
    dimension: RelationshipEvidenceDimension,
    direction: EvidenceDirection,
    strength: float,
    confidence: float,
    provenance: EvidenceProvenance,
    rationale: str,
) -> RelationshipEvidenceSignal:
    identity = "|".join(
        (memory.id, dimension.value, direction.value, provenance.value, rationale)
    )
    signal_id = sha256(identity.encode("utf-8")).hexdigest()[:20]
    observed_at = memory.occurred_at or memory.period_end or memory.updated_at
    return RelationshipEvidenceSignal(
        id=signal_id,
        source_memory_id=memory.id,
        source_message_id=memory.source_message_id,
        dimension=dimension,
        direction=direction,
        strength=strength,
        confidence=confidence,
        observed_at=_as_aware(observed_at),
        expires_at=_as_aware(memory.expires_at) if memory.expires_at else None,
        provenance=provenance,
        rationale=rationale,
        summary=memory.summary,
    )


def _deduplicate_exact_signals(
    signals: list[RelationshipEvidenceSignal],
) -> list[RelationshipEvidenceSignal]:
    keepers: dict[
        tuple[str, RelationshipEvidenceDimension, EvidenceDirection],
        RelationshipEvidenceSignal,
    ] = {}
    for signal in signals:
        key = (signal.source_memory_id, signal.dimension, signal.direction)
        existing = keepers.get(key)
        if existing is None or signal.strength * signal.confidence > (
            existing.strength * existing.confidence
        ):
            keepers[key] = signal
    return list(keepers.values())


def _deduplicate_correlated_signals(
    signals: list[RelationshipEvidenceSignal],
    reference_time: datetime,
) -> list[RelationshipEvidenceSignal]:
    keepers: dict[
        tuple[str, RelationshipEvidenceDimension, EvidenceDirection],
        RelationshipEvidenceSignal,
    ] = {}
    for signal in signals:
        source = signal.source_message_id or signal.source_memory_id
        key = (source, signal.dimension, signal.direction)
        existing = keepers.get(key)
        if existing is None or _signal_rank(signal, reference_time) > _signal_rank(
            existing,
            reference_time,
        ):
            keepers[key] = signal
    return list(keepers.values())


def _project_dimension(
    dimension: RelationshipEvidenceDimension,
    signals: list[RelationshipEvidenceSignal],
    reference_time: datetime,
) -> RelationshipDimensionProjection:
    relevant = [signal for signal in signals if signal.dimension == dimension]
    support = [signal for signal in relevant if signal.direction == EvidenceDirection.SUPPORT]
    oppose = [signal for signal in relevant if signal.direction == EvidenceDirection.OPPOSE]
    support_score = 1 - prod(
        1 - _effective_signal_weight(signal, reference_time) for signal in support
    )
    oppose_score = 1 - prod(
        1 - _effective_signal_weight(signal, reference_time) for signal in oppose
    )
    score = max(-1.0, min(1.0, support_score - oppose_score))
    confidence = _projection_confidence(relevant, support_score, oppose_score, reference_time)
    return RelationshipDimensionProjection(
        dimension=dimension,
        state=_projection_state(
            dimension,
            score,
            support_score,
            oppose_score,
            confidence,
        ),
        score=round(score, 4),
        confidence=round(confidence, 4),
        supporting_evidence_ids=[item.id for item in support[:8]],
        opposing_evidence_ids=[item.id for item in oppose[:8]],
        independent_source_count=len(
            {item.source_message_id or item.source_memory_id for item in relevant}
        ),
    )


def _projection_confidence(
    signals: list[RelationshipEvidenceSignal],
    support_score: float,
    oppose_score: float,
    reference_time: datetime,
) -> float:
    if not signals:
        return 0.0
    weights = [
        signal.strength * _decay_factor(signal, reference_time) for signal in signals
    ]
    total = sum(weights)
    if total <= 0:
        return 0.0
    base = sum(
        weight * signal.confidence for weight, signal in zip(weights, signals, strict=True)
    ) / total
    contradiction = min(support_score, oppose_score)
    return max(0.0, min(1.0, base * (1 - 0.7 * contradiction)))


def _projection_state(
    dimension: RelationshipEvidenceDimension,
    score: float,
    support_score: float,
    oppose_score: float,
    confidence: float,
) -> str:
    if confidence < 0.35:
        return "unknown"
    if dimension == RelationshipEvidenceDimension.FAMILIARITY:
        if score <= -0.5:
            return "unfamiliar"
        if score >= 0.72:
            return "high"
        if score >= 0.4:
            return "moderate"
        if score >= 0.12:
            return "low"
        return "unknown"
    if dimension == RelationshipEvidenceDimension.TRUST:
        if score >= 0.65:
            return "high"
        if score >= 0.38:
            return "moderate"
        if abs(score) >= 0.12:
            return "low"
        return "unknown"
    if dimension == RelationshipEvidenceDimension.INVESTMENT:
        if score >= 0.68:
            return "high"
        if score >= 0.25:
            return "mixed"
        if score <= -0.25:
            return "low"
        return "unknown"
    if dimension == RelationshipEvidenceDimension.CONFLICT:
        if oppose_score >= 0.55 and score <= -0.05:
            return "resolved"
        if score >= 0.55:
            return "active"
        if score >= 0.2:
            return "cooling"
        if support_score > 0 or oppose_score > 0:
            return "repairing"
        return "unknown"
    if oppose_score >= 0.5 and support_score < 0.3:
        return "clear"
    if support_score >= 0.7:
        return "explicit"
    if support_score >= 0.3:
        return "at_risk"
    return "unknown"


def _effective_signal_weight(
    signal: RelationshipEvidenceSignal,
    reference_time: datetime,
) -> float:
    return signal.strength * signal.confidence * _decay_factor(signal, reference_time)


def _signal_rank(
    signal: RelationshipEvidenceSignal,
    reference_time: datetime,
) -> tuple[float, float, float]:
    return (
        _effective_signal_weight(signal, reference_time),
        signal.confidence,
        signal.observed_at.timestamp(),
    )


def _decay_factor(
    signal: RelationshipEvidenceSignal,
    reference_time: datetime,
) -> float:
    age_days = max((_as_aware(reference_time) - _as_aware(signal.observed_at)).total_seconds(), 0)
    age_days /= 86400
    half_life = _HALF_LIFE_DAYS[signal.dimension]
    return 0.5 ** (age_days / half_life)


def _normalize_dimension(value: object) -> object:
    if not isinstance(value, str):
        return value
    normalized = value.casefold().strip().replace("-", "_")
    aliases = {
        "interaction_reciprocity": "investment",
        "reciprocity": "investment",
        "relationship_familiarity": "familiarity",
        "trust_access": "trust",
    }
    return aliases.get(normalized, normalized)


def _normalize_direction(value: object) -> object:
    if not isinstance(value, str):
        return value
    normalized = value.casefold().strip()
    aliases = {
        "against": "oppose",
        "negative": "oppose",
        "opposes": "oppose",
        "positive": "support",
        "supports": "support",
    }
    return aliases.get(normalized, normalized)


def _as_aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value

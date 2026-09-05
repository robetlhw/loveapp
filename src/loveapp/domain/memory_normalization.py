from __future__ import annotations

from datetime import datetime

from loveapp.domain.memory import MemoryCandidate, MemoryKind, PredicateType
from loveapp.domain.memory_dimensions import (
    INTERACTION_PATTERN_DIMENSIONS,
    dimension_for_predicate,
    normalize_interaction_metric,
    normalize_state_dimension,
    normalize_state_value,
)
from loveapp.domain.memory_lifecycle import normalize_memory_candidate
from loveapp.domain.memory_predicates import CANONICAL_PREDICATES, normalize_predicate


class NormalizationContractError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


def validate_raw_memory_candidate(candidate: MemoryCandidate) -> None:
    """Validate only generic pre-normalization invariants.

    Canonical registration, interaction metrics, preference domains, and state
    completeness are deliberately deferred until deterministic normalization.
    """

    # Dual predicate declarations are not a generic shape error.  They must
    # be interpreted against the deterministic normalized representation so
    # equivalent declarations can reconcile and unrelated declarations fail
    # closed at the canonical boundary.


def validate_normalized_memory_candidate(
    candidate: MemoryCandidate,
    *,
    allow_legacy_open_world: bool = False,
) -> None:
    """Enforce the authoritative post-normalization representation contract."""

    if candidate.canonical_predicate and candidate.custom_predicate:
        raise NormalizationContractError(
            "CANONICAL_CUSTOM_CONFLICT",
            "normalized candidate retains dual predicate representations",
        )
    if candidate.predicate_type == PredicateType.CANONICAL:
        if candidate.canonical_predicate not in CANONICAL_PREDICATES:
            raise NormalizationContractError(
                "UNREGISTERED_CANONICAL_PREDICATE",
                str(candidate.canonical_predicate or "<missing>"),
            )
        if candidate.custom_predicate is not None:
            raise NormalizationContractError(
                "CANONICAL_CUSTOM_CONFLICT",
                "canonical output retains a custom predicate",
            )
    elif not candidate.custom_predicate or candidate.canonical_predicate is not None:
        raise NormalizationContractError(
            "INVALID_CUSTOM_PREDICATE",
            "custom output must retain exactly one custom predicate",
        )

    if candidate.kind == MemoryKind.INTERACTION_PATTERN:
        metric = normalize_interaction_metric(candidate.payload.get("metric"))
        if metric not in INTERACTION_PATTERN_DIMENSIONS:
            if allow_legacy_open_world and (
                _is_legacy_custom(candidate) or _is_legacy_canonical_state(candidate)
            ):
                return
            raise NormalizationContractError(
                "INTERACTION_METRIC_INVALID",
                str(candidate.payload.get("metric") or "<missing>"),
            )

    if candidate.kind == MemoryKind.RELATIONSHIP_STATE:
        dimension = normalize_state_dimension(candidate.state_dimension)
        value = normalize_state_value(dimension, candidate.state_value)
        if dimension is None:
            if candidate.predicate_type == PredicateType.CUSTOM and _has_unknown_state_hint(
                candidate
            ):
                return
            if allow_legacy_open_world and (
                _is_legacy_custom(candidate) or _is_legacy_canonical_state(candidate)
            ):
                return
            raise NormalizationContractError(
                "UNKNOWN_STATE_DIMENSION",
                str(candidate.state_dimension or "<missing>"),
            )
        if value is None:
            if candidate.predicate_type == PredicateType.CUSTOM and _has_unknown_state_hint(
                candidate
            ):
                return
            if allow_legacy_open_world and (
                _is_legacy_custom(candidate) or _is_legacy_canonical_state(candidate)
            ):
                return
            raise NormalizationContractError(
                "STATE_VALUE_INVALID",
                str(candidate.state_value or "<missing>"),
            )
        if candidate.state_dimension != dimension or candidate.state_value != value:
            raise NormalizationContractError(
                "NAMESPACE_DRIFT",
                "top-level state representation is not lifecycle-canonical",
            )
        if candidate.payload.get("state_dimension") != dimension or candidate.payload.get(
            "state_value"
        ) != value:
            raise NormalizationContractError(
                "NAMESPACE_DRIFT",
                "payload and top-level state representations differ",
            )


def normalize_memory_candidate_contract(
    candidate: MemoryCandidate,
    reference_time: datetime,
    *,
    allow_legacy_open_world: bool = False,
) -> MemoryCandidate:
    validate_raw_memory_candidate(candidate)
    if not allow_legacy_open_world and _is_bare_unknown_state(candidate):
        raise NormalizationContractError(
            "UNKNOWN_STATE_DIMENSION",
            str(candidate.state_dimension or candidate.payload.get("state_dimension")),
        )
    normalized = normalize_memory_candidate(candidate, reference_time)
    if (
        candidate.canonical_predicate
        and candidate.custom_predicate
        and not _equivalent_predicate_declarations(candidate)
    ):
        raise NormalizationContractError(
            "CANONICAL_CUSTOM_CONFLICT",
            "normalized candidate cannot retain unrelated canonical and custom predicates",
        )
    validate_normalized_memory_candidate(
        normalized,
        allow_legacy_open_world=allow_legacy_open_world,
    )
    return normalized


def _is_bare_unknown_state(candidate: MemoryCandidate) -> bool:
    """Reject direct unknown state fields without an explicit open-world cue.

    Typed ``*_hint`` fields and explicit Custom predicates are allowed to reach
    the normalizer for safe Custom fallback.  A bare direct state declaration
    has no such authorization and must fail closed after generic validation.
    """

    if candidate.kind != MemoryKind.RELATIONSHIP_STATE:
        return False
    payload = candidate.payload
    if payload.get("state_dimension_hint") or candidate.custom_predicate:
        return False
    raw_dimension = payload.get("state_dimension") or candidate.state_dimension
    if not isinstance(raw_dimension, str) or not raw_dimension.strip():
        return False
    return normalize_state_dimension(raw_dimension) is None


def _is_legacy_custom(candidate: MemoryCandidate) -> bool:
    """Allow pre-contract open-world claims without weakening canonical checks."""

    return (
        candidate.predicate_type == PredicateType.CUSTOM
        and bool(candidate.custom_predicate)
        and candidate.canonical_predicate is None
    )


def _has_unknown_state_hint(candidate: MemoryCandidate) -> bool:
    """Return true only when a custom state came from an unregistered dimension.

    A registered dimension with an invalid value must remain a canonical
    contract rejection; otherwise an invalid state could silently downgrade to
    Custom and bypass the post-normalization validator.
    """

    payload = candidate.payload
    # An explicitly typed hint or an explicit Custom declaration may be
    # preserved as an open-world state.  A bare ``has_state`` claim with direct
    # unknown state fields is not enough evidence for a safe Custom fallback;
    # it remains a post-normalization contract rejection (e.g. NORM-056).
    if not payload.get("state_dimension_hint") and not candidate.custom_predicate:
        return False
    raw_dimension = (
        payload.get("state_dimension_hint")
        or payload.get("state_dimension")
        or candidate.state_dimension
    )
    if not isinstance(raw_dimension, str) or not raw_dimension.strip():
        return False
    return normalize_state_dimension(raw_dimension) is None


def _is_legacy_canonical_state(candidate: MemoryCandidate) -> bool:
    """Recognize the pre-contract dotted state shape for production compatibility."""

    if candidate.predicate_type != PredicateType.CANONICAL:
        return False
    predicate = CANONICAL_PREDICATES.get(candidate.canonical_predicate or "")
    if predicate is None or predicate.state_dimension is None:
        return False
    if candidate.state_dimension != predicate.state_dimension:
        return False
    value = str(candidate.state_value or "").casefold().strip().replace("-", "_")
    return bool(value) and (not predicate.allowed_values or value in predicate.allowed_values)


def _equivalent_predicate_declarations(candidate: MemoryCandidate) -> bool:
    canonical = candidate.canonical_predicate
    custom = candidate.custom_predicate
    if canonical not in CANONICAL_PREDICATES or not custom:
        return False
    declaration_payload: dict[str, object] = {}
    custom_dimension = dimension_for_predicate(custom)
    if custom_dimension in INTERACTION_PATTERN_DIMENSIONS:
        declaration_payload["metric_hint"] = custom_dimension
    custom_view = normalize_predicate(
        kind=candidate.kind,
        raw_predicate=custom,
        custom_predicate=custom,
        predicate_type=PredicateType.CUSTOM,
        payload=declaration_payload,
    )
    return custom_view.canonical_predicate == canonical

"""Deterministic parsing and validation for memory-model responses.

The model is allowed to be imperfect at the JSON boundary. Local repair may
normalize an explicitly expressed concept, but it must never invent a fact
that is absent from the user's text.
"""

import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    DiscardedSpan,
    EvidenceExplicitness,
    MemoryKind,
    MemoryPerspective,
    MemorySemanticGateReason,
    MemoryValence,
    PredicateType,
    RelationshipImpact,
    TemporalPrecision,
    TimeKind,
)
from loveapp.domain.memory_dimensions import (
    INTERACTION_PATTERN_DIMENSIONS,
    conflicting_atomic_dimensions,
    declared_claim_dimension,
    detect_evidence_dimensions,
    dimension_for_predicate,
    is_relationship_interaction_subject,
    normalize_interaction_metric,
    normalize_interaction_pattern_payload,
    normalize_state_dimension,
    normalize_state_value,
)
from loveapp.domain.memory_lifecycle import open_world_social_integration_predicate
from loveapp.domain.memory_predicates import CANONICAL_PREDICATES, normalize_predicate


@dataclass(frozen=True)
class ParsedMemoryResponse:
    extraction: AtomicExtraction
    repair_status: str
    extraction_status: str = "success"
    validation_mode: str = "legacy"
    repair_steps: str = ""
    original_claim_count: int = 0
    repaired_claim_count: int = 0
    discarded_claim_count: int = 0
    invalid_claim_count: int = 0
    invalid_claim_reasons: tuple[str, ...] = ()


class MemoryResponseError(ValueError):
    """A model response could not be safely converted to an extraction."""

    def __init__(
        self,
        message: str,
        *,
        category: str,
        repair_status: str = "none",
        repair_steps: str = "",
    ) -> None:
        super().__init__(message)
        self.category = category
        self.repair_status = repair_status
        self.repair_steps = repair_steps
        self.attempt = None
        self.details: dict[str, str | int | float | bool | None] = {}


class ClaimAtomicityError(ValueError):
    """A structurally valid claim contains independently updatable facts."""

    def __init__(self, claim_id: str, conflicts: frozenset[str]) -> None:
        self.claim_id = claim_id
        self.conflicts = conflicts
        names = ", ".join(sorted(conflicts))
        super().__init__(f"原子声明 {claim_id} 包含多个记忆维度：{names}")


def parse_memory_response(
    content: str | None,
    *,
    source_text: str | None = None,
    validation_mode: Literal["legacy", "raw"] = "legacy",
) -> ParsedMemoryResponse:
    """Parse a model response at either the legacy or raw claim boundary.

    ``raw`` is the production boundary: parser repair keeps only structural
    meaning and Generic Validator checks.  Canonical/state/metric ownership is
    deferred to ``normalize_memory_candidate_contract``.  ``legacy`` remains
    available for older diagnostics and callers that historically consumed the
    parser's pre-normalized representation.
    """

    if validation_mode not in {"legacy", "raw"}:
        raise ValueError(f"unsupported validation_mode: {validation_mode}")

    if not content or not content.strip():
        raise MemoryResponseError(
            "记忆抽取模型没有返回内容。",
            category="empty_response",
        )

    raw = content.strip().lstrip("\ufeff")
    steps: list[str] = []
    cleaned = _strip_code_fence(raw)
    if cleaned != raw:
        steps.append("code_fence")

    candidate = _extract_balanced_json(cleaned)
    if candidate != cleaned.strip():
        steps.append("json_object")

    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as first_error:
        repaired = _remove_trailing_commas(candidate)
        if repaired == candidate:
            raise MemoryResponseError(
                f"记忆抽取结果不是合法 JSON：{_json_error_detail(first_error)}",
                category="json_syntax",
                repair_status="local_repair" if steps else "none",
                repair_steps=",".join(steps),
            ) from first_error
        steps.append("trailing_commas")
        try:
            payload = json.loads(repaired)
        except json.JSONDecodeError as second_error:
            raise MemoryResponseError(
                f"记忆抽取结果不是合法 JSON：{_json_error_detail(second_error)}",
                category="json_syntax",
                repair_status="local_repair",
                repair_steps=",".join(steps),
            ) from second_error

    if not isinstance(payload, dict):
        raise MemoryResponseError(
            "记忆抽取结果的根节点必须是 JSON 对象。",
            category="root_shape",
            repair_status="local_repair" if steps else "none",
            repair_steps=",".join(steps),
        )

    raw_claims_snapshot = payload.get("claims")
    normalized = dict(payload)
    defaults_applied = _add_safe_container_defaults(normalized)
    if defaults_applied:
        steps.append("default_fields")
    if _normalize_enum_aliases(normalized):
        steps.append("enum_aliases")
    if _normalize_semantic_gate_fields(normalized):
        steps.append("semantic_gate_enum_alias")
    if validation_mode == "legacy":
        steps.extend(_normalize_claim_semantics(normalized, source_text=source_text))
    else:
        steps.extend(_normalize_raw_claim_structure(normalized))

    _validate_semantic_gate_payload(normalized)
    try:
        _validate_root_shape(normalized)
    except MemoryResponseError as exc:
        if not _has_semantic_gate_contract(normalized):
            raise
        # The Gate contract is independently valid. Keep that verdict while
        # discarding an unsafe extraction container so schema failure cannot
        # be misreported as a semantic Gate decision.
        invalid_count = (
            max(1, len(raw_claims_snapshot))
            if isinstance(raw_claims_snapshot, list)
            else 1
        )
        steps.extend(("claim_container_invalid", "all_claims_invalid"))
        return ParsedMemoryResponse(
            extraction=AtomicExtraction(
                should_extract=normalized.get("should_extract"),
                gate_reason=normalized.get("gate_reason"),
            ),
            repair_status="local_repair",
            extraction_status="claim_schema_invalid",
            validation_mode=validation_mode,
            repair_steps=",".join(dict.fromkeys(steps)),
            original_claim_count=(
                len(raw_claims_snapshot)
                if isinstance(raw_claims_snapshot, list)
                else 0
            ),
            discarded_claim_count=invalid_count,
            invalid_claim_count=invalid_count,
            invalid_claim_reasons=(str(exc)[:1000],),
        )
    valid_claims: list[AtomicClaim] = []
    invalid_claim_reasons: list[str] = []
    repaired_claim_count = 0
    claim_ids: set[str] = set()
    for index, raw_claim in enumerate(normalized["claims"]):
        try:
            claim = AtomicClaim.model_validate(raw_claim)
            validator = (
                validate_memory_claim
                if validation_mode == "legacy"
                else validate_memory_claim_generic
            )
            validator(claim, source_text, claim_ids)
        except ClaimAtomicityError as exc:
            repaired_claim = _repair_non_atomic_claim(
                claim,
                source_text,
                semantic_normalization=validation_mode == "legacy",
            )
            if repaired_claim is not None:
                try:
                    validator(repaired_claim, source_text, claim_ids)
                except (ValidationError, ValueError) as repair_exc:
                    invalid_claim_reasons.append(
                        f"claims.{index} - {_validation_error_text(repair_exc)}"
                    )
                    continue
                valid_claims.append(repaired_claim)
                repaired_claim_count += 1
                steps.append("atomic_evidence_narrowing")
                continue
            invalid_claim_reasons.append(
                f"claims.{index} - {_validation_error_text(exc)}"
            )
            continue
        except (ValidationError, ValueError) as exc:
            invalid_claim_reasons.append(
                f"claims.{index} - {_validation_error_text(exc)}"
            )
            continue
        valid_claims.append(claim)

    valid_discarded: list[DiscardedSpan] = []
    invalid_discarded_reasons: list[str] = []
    for index, raw_discarded in enumerate(normalized["discarded_spans"]):
        try:
            discarded = DiscardedSpan.model_validate(raw_discarded)
            if source_text is not None and discarded.text not in source_text:
                raise ValueError(f"丢弃片段不在用户原文中：{discarded.text}")
            _validate_discarded_span(discarded, valid_claims)
        except (ValidationError, ValueError) as exc:
            invalid_discarded_reasons.append(
                f"discarded_spans.{index} - {_validation_error_text(exc)}"
            )
            continue
        valid_discarded.append(discarded)

    all_claims_invalid = bool(normalized["claims"] and not valid_claims)
    if all_claims_invalid:
        category = _claim_failure_category(invalid_claim_reasons)
        detail = "; ".join(invalid_claim_reasons[:5])
        gate_contract_present = (
            normalized.get("should_extract") is not None
            and normalized.get("gate_reason") is not None
        )
        if not gate_contract_present:
            failure = MemoryResponseError(
                f"记忆抽取结果不是约定的 JSON 结构：{detail}",
                category=category,
                repair_status="local_repair" if steps else "none",
                repair_steps=",".join(steps),
            )
            failure.details.update(
                {
                    "invalid_claim_count": len(invalid_claim_reasons),
                    "invalid_claim_reasons": " | ".join(invalid_claim_reasons[:5]),
                    "invalid_claim_snapshot": _safe_json_snapshot(raw_claims_snapshot),
                    "validation_error": detail[:1000],
                    "repair_attempt": _repair_attempt_name(steps),
                    "repair_result": "unresolved",
                    "repair_status": failure.repair_status,
                    "repair_steps": failure.repair_steps,
                }
            )
            raise failure
        # The semantic Gate contract has already been parsed and validated.
        # Keep its verdict while failing closed on every invalid claim; claim
        # validation must never rewrite a valid Gate decision.
        steps.append("all_claims_invalid")

    if invalid_claim_reasons:
        steps.append("partial_claims")
    if invalid_discarded_reasons:
        steps.append("partial_discarded_spans")
    if any("与已保存声明证据重叠" in reason for reason in invalid_discarded_reasons):
        steps.append("discarded_overlap")
    try:
        extraction = AtomicExtraction(
            should_extract=normalized.get("should_extract"),
            gate_reason=normalized.get("gate_reason"),
            claims=valid_claims,
            discarded_spans=valid_discarded,
        )
    except ValidationError as exc:
        raise MemoryResponseError(
            "记忆语义 Gate 结果不符合约定：" + _validation_detail(exc),
            category="semantic_gate_contract",
            repair_status="local_repair" if steps else "none",
            repair_steps=",".join(steps),
        ) from exc
    return ParsedMemoryResponse(
        extraction=extraction,
        repair_status="local_repair" if steps else "direct",
        extraction_status=(
            "claim_schema_invalid"
            if all_claims_invalid
            else "empty_claims"
            if extraction.should_extract is True and not extraction.claims
            else "success"
        ),
        validation_mode=validation_mode,
        repair_steps=",".join(dict.fromkeys(steps)),
        original_claim_count=len(normalized["claims"]),
        repaired_claim_count=repaired_claim_count,
        discarded_claim_count=len(invalid_claim_reasons),
        invalid_claim_count=len(invalid_claim_reasons),
        invalid_claim_reasons=tuple(invalid_claim_reasons[:5]),
    )


def validate_memory_extraction(
    extraction: AtomicExtraction,
    source_text: str,
) -> None:
    """Validate claims against the user text without adding new meaning."""

    claim_ids: set[str] = set()
    for claim in extraction.claims:
        validate_memory_claim(claim, source_text, claim_ids)
    for discarded in extraction.discarded_spans:
        if discarded.text not in source_text:
            raise ValueError(f"丢弃片段不在用户原文中：{discarded.text}")
        _validate_discarded_span(discarded, extraction.claims)


def validate_memory_claim(
    claim: AtomicClaim,
    source_text: str | None,
    claim_ids: set[str],
) -> None:
    validate_memory_claim_generic(claim, source_text, claim_ids)
    validate_normalized_memory_claim(claim)


def validate_memory_claim_generic(
    claim: AtomicClaim,
    source_text: str | None,
    claim_ids: set[str],
) -> None:
    """Validate raw claim structure without requiring canonical completeness."""

    if claim.claim_id in claim_ids:
        raise ValueError(f"原子声明 ID 重复：{claim.claim_id}")
    if source_text is not None:
        for evidence in claim.evidence_spans:
            if evidence not in source_text:
                raise ValueError(f"证据片段不在用户原文中：{evidence}")
    _validate_raw_semantic_hints(claim)
    if not re.search(r"[\u4e00-\u9fff]", claim.summary):
        raise ValueError(f"声明 {claim.claim_id} 的 summary 必须使用简体中文")
    preference = claim.payload.get("preference")
    if claim.kind == MemoryKind.PREFERENCE and isinstance(preference, list):
        raise ValueError(f"偏好声明 {claim.claim_id} 包含多个 preference，必须拆分")
    if claim.kind in {
        MemoryKind.STABLE_FACT,
        MemoryKind.INTERACTION_PATTERN,
        MemoryKind.RELATIONSHIP_STATE,
    }:
        dimensions = detect_evidence_dimensions(" ".join(claim.evidence_spans))
        primary_dimension = declared_claim_dimension(
            kind=claim.kind.value,
            predicate=claim.predicate,
            payload=claim.payload,
        )
        conflicts = conflicting_atomic_dimensions(primary_dimension, dimensions)
        if conflicts:
            raise ClaimAtomicityError(claim.claim_id, conflicts)
    if claim.kind == MemoryKind.PLANNED_EVENT:
        temporal_fields = (
            claim.occurred_at,
            claim.period_start,
            claim.period_end,
            claim.payload.get("temporal_expression"),
        )
        future_words = re.search(
            r"明天|后天|大后天|下周|下个月|本周末|这周末|周末|过几天|几天后|月底|周[一二三四五六日天]",
            " ".join(claim.evidence_spans),
        )
        if not any(value for value in temporal_fields) and future_words is None:
            raise ValueError(f"计划事件 {claim.claim_id} 缺少明确的未来时间")
    claim_ids.add(claim.claim_id)


_RAW_SEMANTIC_HINT_FIELDS = (
    "metric_hint",
    "preference_type_hint",
    "state_dimension_hint",
    "state_value_hint",
)

_RAW_SUBJECT_VALUES = frozenset(
    {
        "user",
        "partner",
        "relationship",
        "other_person",
        "对方",
        "伴侣",
        "她",
        "他",
        "我",
        "我们",
        "双方",
        "关系",
    }
)


def _validate_raw_semantic_hints(claim: AtomicClaim) -> None:
    """Check hint shape only; registration and mapping belong downstream."""

    subject = claim.subject.casefold().strip()
    if subject not in _RAW_SUBJECT_VALUES:
        raise ValueError(f"原始声明 subject 不在受控枚举中：{claim.subject}")
    for field in _RAW_SEMANTIC_HINT_FIELDS:
        value = claim.payload.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"原始语义提示 {field} 必须是非空字符串")


def validate_normalized_memory_claim(claim: AtomicClaim) -> None:
    """Validate canonical/state contracts after deterministic normalization."""

    if (
        claim.predicate_type == PredicateType.CANONICAL
        and claim.canonical_predicate not in CANONICAL_PREDICATES
    ):
        raise ValueError(
            f"声明 {claim.claim_id} 使用了未注册的 canonical predicate："
            f"{claim.canonical_predicate or '<missing>'}"
        )
    if (
        claim.predicate_type == PredicateType.CUSTOM
        and not (claim.custom_predicate or claim.predicate)
    ):
        raise ValueError(f"声明 {claim.claim_id} 的 custom predicate 为空")
    if claim.canonical_predicate and claim.custom_predicate:
        raise ValueError(f"声明 {claim.claim_id} 不能同时提供 canonical 和 custom predicate")
    if claim.kind == MemoryKind.INTERACTION_PATTERN:
        metric = normalize_interaction_metric(claim.payload.get("metric"))
        if metric is None:
            raise ValueError(f"互动模式 {claim.claim_id} 缺少单一 payload.metric")
    if claim.kind == MemoryKind.RELATIONSHIP_STATE:
        dimension, value = _registered_relationship_state(claim)
        if (dimension is None or value is None) and not (
            _is_open_world_social_integration_claim(claim)
        ):
            raise ValueError(
                f"关系状态 {claim.claim_id} 缺少已注册的 state_dimension/state_value"
            )


def _repair_non_atomic_claim(
    claim: AtomicClaim,
    source_text: str | None,
    *,
    semantic_normalization: bool = True,
) -> AtomicClaim | None:
    """Narrow over-broad evidence without inventing a second proposition.

    A local repair may retain the model's declared proposition and exact source
    fragments. It must not synthesize a new predicate or state value for a
    second dimension; those fragments are left for another claim or discarded.
    """

    fragments = _evidence_fragments(claim.evidence_spans, source_text)
    if len(fragments) < 2:
        return None

    primary = declared_claim_dimension(
        kind=claim.kind.value,
        predicate=claim.predicate,
        payload=claim.payload,
    )
    summary_features = _semantic_features(claim.summary)
    ranked: list[tuple[int, int, str, frozenset[str]]] = []
    for fragment in fragments:
        dimensions = detect_evidence_dimensions(fragment)
        if conflicting_atomic_dimensions(primary, dimensions):
            continue
        overlap = len(summary_features & _semantic_features(fragment))
        if overlap == 0:
            continue
        primary_match = int(primary is not None and primary in dimensions)
        ranked.append((primary_match, overlap, fragment, dimensions))
    if not ranked:
        return None

    ranked.sort(key=lambda item: (item[0], item[1], -len(item[2])), reverse=True)
    selected: list[str] = []
    combined_dimensions: frozenset[str] = frozenset()
    for _, _, fragment, dimensions in ranked:
        candidate_dimensions = combined_dimensions | dimensions
        if conflicting_atomic_dimensions(primary, candidate_dimensions):
            continue
        selected.append(fragment)
        combined_dimensions = candidate_dimensions
    selected = [fragment for fragment in fragments if fragment in set(selected)]
    if not selected:
        return None
    if primary is not None and primary not in combined_dimensions:
        return None
    if selected == claim.evidence_spans:
        return None

    selected_features = _semantic_features(" ".join(selected))
    coverage = (
        len(summary_features & selected_features) / len(summary_features)
        if summary_features
        else 0.0
    )
    summary = claim.summary if coverage >= 0.6 else "；".join(selected)
    updates: dict[str, object] = {
        "summary": summary[:500],
        "evidence_spans": selected[:8],
    }
    if semantic_normalization and claim.kind == MemoryKind.INTERACTION_PATTERN:
        pattern_dimensions = combined_dimensions & INTERACTION_PATTERN_DIMENSIONS
        if len(pattern_dimensions) == 1:
            repaired_dimension = next(iter(pattern_dimensions))
            payload = dict(claim.payload)
            original_metric = normalize_interaction_metric(payload.get("metric"))
            if original_metric != repaired_dimension:
                for field in ("baseline", "current", "direction", "frequency"):
                    payload.pop(field, None)
            payload["metric"] = repaired_dimension
            updates["payload"] = payload
            if dimension_for_predicate(claim.predicate) is None:
                updates["predicate"] = repaired_dimension
    return claim.model_copy(update=updates)


def _normalize_raw_claim_structure(payload: dict[str, object]) -> list[str]:
    """Apply only structural repairs allowed before deterministic normalization.

    Evidence objects contain transport metadata (offsets/labels) that the
    domain claim does not retain. Their text can be narrowed safely, but no
    hint, predicate, state, preference, or kind is interpreted here.
    """

    claims = payload.get("claims")
    if not isinstance(claims, list):
        return []
    steps: list[str] = []
    for claim in claims:
        if isinstance(claim, dict) and _narrow_structured_evidence_spans(claim):
            steps.append("structured_evidence_text_narrowing")
    return list(dict.fromkeys(steps))


def _evidence_fragments(
    evidence_spans: list[str],
    source_text: str | None,
) -> list[str]:
    fragments: list[str] = []
    for evidence in evidence_spans:
        for fragment in re.split(r"[，,。；;！？!?]+", evidence):
            cleaned = fragment.strip()
            if len(cleaned) < 2:
                continue
            if source_text is not None and cleaned not in source_text:
                continue
            fragments.append(cleaned)
    return list(dict.fromkeys(fragments))


def _semantic_features(value: str) -> set[str]:
    normalized = re.sub(r"\s+", "", value.casefold())
    features = set(re.findall(r"[a-z0-9_]{2,}", normalized))
    for block in re.findall(r"[\u4e00-\u9fff]+", normalized):
        features.update(block[index : index + 2] for index in range(len(block) - 1))
    return features


def _validate_root_shape(payload: dict[str, object]) -> None:
    unknown = set(payload) - {
        "should_extract",
        "gate_reason",
        "claims",
        "discarded_spans",
    }
    if unknown:
        names = ", ".join(sorted(str(value) for value in unknown)[:5])
        raise MemoryResponseError(
            f"记忆抽取结果包含未知根字段：{names}",
            category="schema_validation",
        )
    for field in ("claims", "discarded_spans"):
        value = payload.get(field)
        if not isinstance(value, list):
            raise MemoryResponseError(
                f"记忆抽取结果字段 {field} 必须是数组。",
                category="root_shape",
            )
        if len(value) > 12:
            raise MemoryResponseError(
                f"记忆抽取结果字段 {field} 超过最多 12 项。",
                category="schema_validation",
            )


def _normalize_semantic_gate_fields(payload: dict[str, object]) -> bool:
    gate_reason = payload.get("gate_reason")
    if not isinstance(gate_reason, str):
        return False
    normalized = gate_reason.strip().upper()
    if normalized == gate_reason:
        return False
    payload["gate_reason"] = normalized
    return True


def _validate_semantic_gate_payload(payload: dict[str, object]) -> None:
    has_should_extract = "should_extract" in payload
    has_gate_reason = "gate_reason" in payload
    if not has_should_extract and not has_gate_reason:
        return
    if not has_should_extract or not has_gate_reason:
        raise MemoryResponseError(
            "记忆语义 Gate 必须同时返回 should_extract 和 gate_reason。",
            category="semantic_gate_contract",
        )

    should_extract = payload.get("should_extract")
    gate_reason = payload.get("gate_reason")
    if type(should_extract) is not bool:
        raise MemoryResponseError(
            "记忆语义 Gate 字段 should_extract 必须是布尔值。",
            category="semantic_gate_contract",
        )
    try:
        reason = MemorySemanticGateReason(gate_reason)
    except (TypeError, ValueError) as exc:
        raise MemoryResponseError(
            "记忆语义 Gate 字段 gate_reason 不在受控枚举中。",
            category="semantic_gate_contract",
        ) from exc

    negative_reasons = {
        MemorySemanticGateReason.TRANSIENT,
        MemorySemanticGateReason.SMALL_TALK,
        MemorySemanticGateReason.NO_MEMORY,
    }
    if should_extract and reason in negative_reasons:
        raise MemoryResponseError(
            "记忆语义 Gate 的正向结论与 gate_reason 冲突。",
            category="semantic_gate_contract",
        )
    if not should_extract and reason not in negative_reasons:
        raise MemoryResponseError(
            "记忆语义 Gate 的负向结论与 gate_reason 冲突。",
            category="semantic_gate_contract",
        )


def _has_semantic_gate_contract(payload: dict[str, object]) -> bool:
    return (
        payload.get("should_extract") is not None
        and payload.get("gate_reason") is not None
    )


def _normalize_enum_aliases(payload: dict[str, object]) -> bool:
    claims = payload.get("claims")
    if not isinstance(claims, list):
        return False
    changed = False
    normalized_claims: list[object] = []
    for raw_claim in claims:
        if not isinstance(raw_claim, dict):
            normalized_claims.append(raw_claim)
            continue
        claim = dict(raw_claim)
        raw_kind = claim.get("kind")
        kind_key = _enum_key(raw_kind)
        kind_aliases = {
            "event": "interaction_event",
            "episode": "interaction_event",
            "interaction_episode": "interaction_event",
            "planned": "planned_event",
            "pending": "planned_event",
            "pending_event": "planned_event",
            "future_event": "planned_event",
            "scheduled_event": "planned_event",
            "action": "action_intent",
            "intention": "action_intent",
            "pending_action": "action_intent",
            "plan_intent": "action_intent",
            "state": "relationship_state",
            "current_state": "relationship_state",
            "relationship_status_state": "relationship_state",
            "pattern": "interaction_pattern",
            "trend": "interaction_pattern",
            "interaction_trend": "interaction_pattern",
            "fact": "stable_fact",
            "outcome": "advice_outcome",
        }
        if kind_key in {"belief", "user_belief"}:
            claim["kind"] = "stable_fact"
            claim["perspective"] = "user_belief"
            changed = True
        elif kind_key in kind_aliases:
            claim["kind"] = kind_aliases[kind_key]
            changed = True
        elif kind_key in {item.value for item in MemoryKind} and claim.get("kind") != kind_key:
            claim["kind"] = kind_key
            changed = True

        perspective_aliases = {
            "belief": "user_belief",
            "subjective": "user_belief",
            "user_opinion": "user_belief",
            "reported": "user_reported",
            "reported_fact": "user_reported",
            "inferred": "model_inferred",
        }
        perspective_key = _enum_key(claim.get("perspective"))
        if perspective_key in perspective_aliases:
            claim["perspective"] = perspective_aliases[perspective_key]
            changed = True
        elif (
            perspective_key in {item.value for item in MemoryPerspective}
            and claim.get("perspective") != perspective_key
        ):
            claim["perspective"] = perspective_key
            changed = True
        relationship_impact_aliases = {
            "supportive": RelationshipImpact.IMPROVING.value,
            "positive": RelationshipImpact.IMPROVING.value,
            "harmful": RelationshipImpact.DAMAGING.value,
            "negative": RelationshipImpact.DAMAGING.value,
        }
        relationship_impact_key = _enum_key(claim.get("relationship_impact"))
        if relationship_impact_key in relationship_impact_aliases:
            claim["relationship_impact"] = relationship_impact_aliases[
                relationship_impact_key
            ]
            changed = True
        for field, enum_type in (
            ("time_kind", TimeKind),
            ("temporal_precision", TemporalPrecision),
            ("valence", MemoryValence),
            ("relationship_impact", RelationshipImpact),
            ("predicate_type", PredicateType),
            ("explicitness", EvidenceExplicitness),
        ):
            enum_key = _enum_key(claim.get(field))
            if enum_key in {item.value for item in enum_type} and claim.get(field) != enum_key:
                claim[field] = enum_key
                changed = True
        normalized_claims.append(claim)
    if changed:
        payload["claims"] = normalized_claims
    return changed


def _enum_key(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.casefold().strip().replace("-", "_").replace(" ", "_")


def _claim_failure_category(reasons: list[str]) -> str:
    if any("Input should be" in reason for reason in reasons):
        return "unsupported_enum"
    if any("计划事件" in reason and "未来时间" in reason for reason in reasons):
        return "missing_temporal_anchor"
    if any("包含多个记忆维度" in reason for reason in reasons):
        return "atomicity_validation"
    semantic_markers = (
        "证据片段不在",
        "summary 必须",
        "缺少 payload.metric",
        "计划事件",
        "原子声明 ID 重复",
    )
    if any(any(marker in reason for marker in semantic_markers) for reason in reasons):
        return "semantic_validation"
    return "schema_validation"


_RELATIONSHIP_STAGE_DATING_PATTERN = re.compile(
    r"确认(?:了)?(?:恋爱)?关系|正式(?:地)?在一起|(?:开始|正在|已经)(?:正式)?交往|"
    r"成为(?:了)?(?:男女朋友|情侣)|谈恋爱|恋爱关系|"
    r"(?:我们|双方|我和(?:她|他))(?:现在|已经)?在一起了"
)
_RELATIONSHIP_STAGE_ACQUAINTANCE_PATTERN = re.compile(
    r"普通朋友|只是朋友|刚认识|尚未(?:正式)?(?:交往|在一起)|"
    r"还(?:没|没有)(?:正式)?(?:交往|在一起)"
)
_RELATIONSHIP_STAGE_COMMITTED_PATTERN = re.compile(
    r"长期(?:稳定|承诺|共同规划|规划|共同生活)|明确.{0,8}长期(?:承诺|规划)|"
    r"稳定(?:的)?伴侣(?:关系)?|准备共同生活|共同生活规划|"
    r"已经(?:订婚|结婚)|稳定交往(?:了)?(?:很多年|多年|\d+年)"
)
_RELATIONSHIP_STAGE_NEGATED_DATING_PATTERN = re.compile(
    r"(?:尚未|还没|还没有|并没|并没有|没有|没|拒绝|不愿意|不同意).{0,8}(?:"
    r"确认(?:了)?(?:恋爱)?关系|正式(?:地)?在一起|开始(?:正式)?交往|"
    r"成为(?:了)?(?:男女朋友|情侣))"
)
_RELATIONSHIP_STAGE_SPECULATIVE_PATTERN = re.compile(
    r"(?:如果|假如|要是|可能|也许|希望|但愿|想要?|打算|准备|计划|将来|以后).{0,16}(?:"
    r"确认(?:了)?(?:恋爱)?关系|正式(?:地)?在一起|在一起|开始(?:正式)?交往|"
    r"成为(?:了)?(?:男女朋友|情侣)|长期(?:稳定|承诺|规划|共同生活)|"
    r"共同生活|分手|离婚|复合|重新(?:在一起|交往)|订婚|结婚)"
)
_RELATIONSHIP_STAGE_VAGUE_STABILITY_PATTERN = re.compile(
    r"关系.{0,6}(?:更|比较|挺|很|越来越)?稳定|关系稳定(?:多了|一些|了)"
)
_RELATIONSHIP_STAGE_BREAKUP_PATTERN = re.compile(
    r"分手|离婚|结束(?:了)?(?:恋爱|关系)|(?:已经|现在|后来)?分开了|不在一起了"
)
_RELATIONSHIP_STAGE_REUNION_PATTERN = re.compile(
    r"复合|重新(?:正式)?(?:在一起|交往)"
)
_RELATIONSHIP_STAGE_NEGATED_TRANSITION_PATTERN = re.compile(
    r"(?:尚未|还没|还没有|并未|并没|并没有|没有|没|拒绝|不愿意|不同意).{0,10}(?:"
    r"分手|离婚|复合|重新(?:在一起|交往)|订婚|结婚)"
)
_RELATIONSHIP_STAGE_CONFLICT_RESOLUTION_PATTERN = re.compile(
    r"和好|说开|矛盾.{0,8}(?:解决|结束)|冷战.{0,8}(?:解决|结束)"
)
_RELATIONSHIP_STAGE_HISTORICAL_PATTERN = re.compile(
    r"曾经|以前|之前|去年|上个月|当时"
)
_RELATIONSHIP_STAGE_CURRENT_PATTERN = re.compile(r"现在|今天|昨天|刚刚|刚才")
_RELATIONSHIP_STAGE_GENERIC_CUE_PATTERN = re.compile(
    r"在一起|交往|恋爱关系|普通朋友|情侣|男女朋友|长期承诺|共同生活|"
    r"分手|离婚|复合|订婚|结婚"
)
_EXPLICIT_RESPONSE_RESTORATION_PATTERN = re.compile(
    r"(?:终于|重新|又开始|恢复).{0,10}(?:回复|回应|回.{0,3}消息|聊天|联系)|"
    r"(?:回复|回应|回.{0,3}消息|聊天|联系).{0,10}(?:恢复正常|恢复|正常)"
)
_EXPLICIT_NO_RESPONSE_PATTERN = re.compile(
    r"(?:没(?:有)?|未|不).{0,3}回(?:复|应)?(?:了)?(?:我|用户)?(?:的)?消息|"
    r"(?:联系不上|无法联系|失去联系)"
)


def _normalize_claim_semantics(
    payload: dict[str, object],
    *,
    source_text: str | None,
) -> list[str]:
    claims = payload.get("claims")
    if not isinstance(claims, list):
        return []
    steps: list[str] = []
    normalized_claims: list[object] = []
    for raw_claim in claims:
        if not isinstance(raw_claim, dict):
            normalized_claims.append(raw_claim)
            continue
        claim = dict(raw_claim)
        if _narrow_structured_evidence_spans(claim):
            steps.append("structured_evidence_text_narrowing")
        raw_payload = claim.get("payload")
        claim_payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
        if claim_payload.get("metric") is None and claim_payload.get("metric_hint") is not None:
            claim_payload["metric"] = claim_payload["metric_hint"]
            steps.append("interaction_metric_hint")
        if (
            claim_payload.get("preference_type") is None
            and claim_payload.get("preference_type_hint") is not None
        ):
            claim_payload["preference_type"] = claim_payload["preference_type_hint"]
            steps.append("preference_type_hint")
        if (
            claim_payload.get("state_dimension") is None
            and claim_payload.get("state_dimension_hint") is not None
        ):
            claim_payload["state_dimension"] = claim_payload["state_dimension_hint"]
            steps.append("state_dimension_hint")
        if (
            claim_payload.get("state_value") is None
            and claim_payload.get("state_value_hint") is not None
        ):
            claim_payload["state_value"] = claim_payload["state_value_hint"]
            steps.append("state_value_hint")
        for field in ("state_dimension", "state_value"):
            if claim.get(field) is not None:
                claim_payload.setdefault(field, claim[field])
        if claim_payload and claim.get("payload") != claim_payload:
            claim["payload"] = claim_payload
        kind = _enum_key(claim.get("kind"))
        if kind == MemoryKind.STABLE_FACT.value and claim_payload.get("preference"):
            claim["kind"] = MemoryKind.PREFERENCE.value
            steps.append("preference_kind")
            kind = MemoryKind.PREFERENCE.value
        if _reconcile_registered_canonical_declaration(claim, claim_payload, kind):
            steps.append("canonical_custom_predicate_reconciliation")
        if _align_exact_canonical_predicate(claim, claim_payload, kind):
            steps.append("exact_canonical_predicate_alignment")
        if kind == MemoryKind.PREFERENCE.value:
            semantic_payload = dict(claim_payload)
            if claim.get("object") is not None:
                semantic_payload.setdefault("object", claim["object"])
            if claim.get("summary") is not None:
                semantic_payload.setdefault("summary", claim["summary"])
            if claim.get("evidence_spans") is not None:
                semantic_payload.setdefault("evidence_spans", claim["evidence_spans"])
            preference_normalization = normalize_predicate(
                kind=kind,
                raw_predicate=claim.get("predicate"),
                canonical_predicate=claim.get("canonical_predicate"),
                custom_predicate=claim.get("custom_predicate"),
                predicate_type=claim.get("predicate_type"),
                payload=semantic_payload,
            )
            if (
                preference_normalization.predicate_type
                != _enum_key(claim.get("predicate_type"))
                or preference_normalization.canonical_predicate
                != claim.get("canonical_predicate")
                or preference_normalization.custom_predicate
                != claim.get("custom_predicate")
            ):
                claim["predicate_type"] = preference_normalization.predicate_type
                claim["canonical_predicate"] = preference_normalization.canonical_predicate
                claim["custom_predicate"] = preference_normalization.custom_predicate
                steps.append("preference_canonical_domain_alignment")
        uncertainty_dimension = normalize_state_dimension(
            claim_payload.get("uncertainty_type")
        )
        declared_state_dimension = normalize_state_dimension(
            claim_payload.get("state_dimension")
        )
        state_dimension = uncertainty_dimension or declared_state_dimension
        raw_state_value = claim_payload.get("state_value")
        if uncertainty_dimension is not None and raw_state_value is None:
            raw_state_value = "unknown"
        state_value = normalize_state_value(state_dimension, raw_state_value)
        if (
            kind == MemoryKind.STABLE_FACT.value
            and state_dimension is not None
            and state_value is not None
        ):
            claim["kind"] = MemoryKind.RELATIONSHIP_STATE.value
            claim["subject"] = "relationship"
            claim["predicate"] = "has_state"
            claim_payload["state_dimension"] = state_dimension
            claim_payload["state_value"] = state_value
            claim_payload.setdefault("state_scope", state_dimension)
            claim_payload.setdefault("memory_role", "current_state")
            if state_value == "unknown":
                claim_payload.setdefault("attention_status", "unresolved")
            claim["payload"] = claim_payload
            steps.append("stable_fact_to_relationship_state")
            kind = MemoryKind.RELATIONSHIP_STATE.value
        if (
            kind == MemoryKind.PLANNED_EVENT.value
            and not _claim_has_temporal_anchor(claim)
            and _claim_expresses_action_commitment(claim)
        ):
            claim["kind"] = MemoryKind.ACTION_INTENT.value
            claim_payload.setdefault("event_status", "intended")
            claim["payload"] = claim_payload
            steps.append("unscheduled_plan_to_action_intent")
        if kind == MemoryKind.RELATIONSHIP_STATE.value:
            steps.extend(
                _normalize_relationship_stage_claim(
                    claim,
                    claim_payload,
                    source_text=source_text,
                )
            )
            raw_payload = claim.get("payload")
            claim_payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
            state_dimension = normalize_state_dimension(
                claim_payload.get("state_dimension")
            )
            state_value = normalize_state_value(
                state_dimension,
                claim_payload.get("state_value"),
            )
            predicate_normalization = normalize_predicate(
                kind=kind,
                raw_predicate=claim.get("predicate"),
                canonical_predicate=claim.get("canonical_predicate"),
                custom_predicate=claim.get("custom_predicate"),
                predicate_type=claim.get("predicate_type"),
                payload=claim_payload,
            )
            dimension = state_dimension or predicate_normalization.state_dimension
            value = state_value or predicate_normalization.state_value
            if dimension is not None and value is not None:
                if (
                    claim_payload.get("state_dimension") != dimension
                    or claim_payload.get("state_value") != value
                ):
                    steps.append("relationship_state_aliases")
                claim_payload["state_dimension"] = dimension
                claim_payload["state_value"] = value
                if (
                    state_dimension is None
                    and predicate_normalization.canonical_predicate is not None
                ):
                    claim["predicate_type"] = PredicateType.CANONICAL.value
                    claim["canonical_predicate"] = (
                        predicate_normalization.canonical_predicate
                    )
                    claim.pop("custom_predicate", None)
                    claim["state_dimension"] = dimension
                    claim["state_value"] = value
                    steps.append("canonical_state_alignment")
                if value == "unknown":
                    claim_payload.setdefault("attention_status", "unresolved")
                claim["payload"] = claim_payload
        if kind in {
            MemoryKind.INTERACTION_PATTERN.value,
            MemoryKind.INTERACTION_EVENT.value,
        }:
            raw_metric = claim_payload.get("metric")
            original_metric = normalize_interaction_metric(raw_metric)
            evidence = claim.get("evidence_spans")
            evidence_text = (
                " ".join(str(value) for value in evidence)
                if isinstance(evidence, list)
                else ""
            )
            normalized_interaction_payload = normalize_interaction_pattern_payload(
                claim_payload,
                evidence_text,
                claim.get("predicate"),
            )
            if (
                normalize_interaction_metric(
                    normalized_interaction_payload.get("metric")
                )
                == "contact_frequency"
                and _EXPLICIT_NO_RESPONSE_PATTERN.search(evidence_text)
            ):
                normalized_interaction_payload["metric"] = "response_engagement"
                steps.append("response_engagement_from_evidence")
            detected_dimensions = (
                detect_evidence_dimensions(evidence_text)
                & INTERACTION_PATTERN_DIMENSIONS
            )
            if (
                normalized_interaction_payload.get("metric") is None
                and detected_dimensions == {"response_engagement"}
                and _EXPLICIT_RESPONSE_RESTORATION_PATTERN.search(evidence_text)
            ):
                normalized_interaction_payload["metric"] = "response_engagement"
                normalized_interaction_payload["current"] = "responsive"
                claim["predicate_type"] = PredicateType.CANONICAL.value
                claim["canonical_predicate"] = "interaction.response_engagement"
                claim.pop("custom_predicate", None)
                steps.append("response_restoration_from_evidence")
            metric = normalize_interaction_metric(
                normalized_interaction_payload.get("metric")
            )
            if metric != original_metric:
                if dimension_for_predicate(claim.get("predicate")) == original_metric:
                    claim["predicate"] = metric
                steps.append("interaction_metric_from_evidence")
            if normalized_interaction_payload != claim_payload:
                if metric == original_metric:
                    steps.append("interaction_value_contract")
                claim_payload = normalized_interaction_payload
                claim["payload"] = claim_payload
            if metric is not None and raw_metric != metric:
                steps.append("interaction_metric_aliases")
            if metric in INTERACTION_PATTERN_DIMENSIONS and is_relationship_interaction_subject(
                claim.get("subject")
            ):
                claim["subject"] = "relationship"
        normalized_claims.append(claim)
    payload["claims"] = normalized_claims
    return list(dict.fromkeys(steps))


_STRUCTURED_EVIDENCE_KEYS = frozenset({"text", "start", "end", "offset"})


def _narrow_structured_evidence_spans(claim: dict[str, object]) -> bool:
    """Discard span offsets only when every structured entry has a safe shape."""

    evidence = claim.get("evidence_spans")
    if not isinstance(evidence, list):
        return False

    narrowed: list[object] = []
    changed = False
    for span in evidence:
        if isinstance(span, str):
            narrowed.append(span)
            continue
        if not isinstance(span, dict) or not _is_safe_structured_evidence_span(span):
            return False
        narrowed.append(span["text"])
        changed = True
    if changed:
        claim["evidence_spans"] = narrowed
    return changed


def _is_safe_structured_evidence_span(span: dict[object, object]) -> bool:
    if "text" not in span or not set(span) <= _STRUCTURED_EVIDENCE_KEYS:
        return False
    text = span["text"]
    if not isinstance(text, str) or not text.strip():
        return False

    offsets: dict[str, int] = {}
    for field in ("start", "end", "offset"):
        value = span.get(field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return False
        offsets[field] = value
    return not ("start" in offsets and "end" in offsets and offsets["start"] > offsets["end"])


def _reconcile_registered_canonical_declaration(
    claim: dict[str, object],
    claim_payload: dict[str, object],
    kind: str,
) -> bool:
    """Drop a redundant custom declaration only when both normalize identically."""

    canonical = claim.get("canonical_predicate")
    custom = claim.get("custom_predicate")
    predicate_type = _enum_key(claim.get("predicate_type"))
    if (
        not isinstance(canonical, str)
        or canonical not in CANONICAL_PREDICATES
        or not isinstance(custom, str)
        or not custom.strip()
        or predicate_type not in {PredicateType.CANONICAL.value, PredicateType.CUSTOM.value}
    ):
        return False

    canonical_view = normalize_predicate(
        kind=kind,
        raw_predicate=claim.get("raw_predicate") or claim.get("predicate"),
        canonical_predicate=canonical,
        predicate_type=PredicateType.CANONICAL.value,
        payload=claim_payload,
    )
    if canonical_view.canonical_predicate != canonical:
        return False

    declaration_payload: dict[str, object] = {}
    custom_dimension = dimension_for_predicate(custom)
    if custom_dimension in INTERACTION_PATTERN_DIMENSIONS:
        declaration_payload["metric_hint"] = custom_dimension
    custom_view = normalize_predicate(
        kind=kind,
        raw_predicate=custom,
        custom_predicate=custom,
        predicate_type=PredicateType.CUSTOM.value,
        payload=declaration_payload,
    )
    if custom_view.canonical_predicate != canonical:
        return False

    claim["predicate_type"] = PredicateType.CANONICAL.value
    claim.pop("custom_predicate", None)
    if canonical_view.state_dimension is not None:
        claim["state_dimension"] = canonical_view.state_dimension
    if canonical_view.state_value is not None:
        claim["state_value"] = canonical_view.state_value
    return True


def _normalize_relationship_stage_claim(
    claim: dict[str, object],
    claim_payload: dict[str, object],
    *,
    source_text: str | None,
) -> list[str]:
    """Repair only relationship stages uniquely supported by exact evidence."""

    evidence = claim.get("evidence_spans")
    evidence_text = (
        " ".join(str(value) for value in evidence)
        if isinstance(evidence, list)
        else ""
    )
    authorization_text = source_text if source_text is not None else evidence_text
    evidence_value, _evidence_reason = _relationship_stage_from_evidence(
        authorization_text
    )
    if not _has_relationship_stage_clue(claim, claim_payload, evidence_value):
        return []

    raw_value = claim_payload.get("state_value", claim.get("state_value"))
    declared_value = _canonical_relationship_stage_value(raw_value)
    if evidence_value is None:
        if declared_value not in {None, "unknown"}:
            claim_payload.pop("state_value", None)
            claim.pop("state_value", None)
            claim["payload"] = claim_payload
            return ["relationship_stage_fail_closed"]
        return []

    raw_dimension = claim_payload.get("state_dimension", claim.get("state_dimension"))
    raw_dimension_key = _stage_identifier(raw_dimension)
    missing_shape = raw_dimension_key not in {
        "relationship.stage",
        "relationship_stage",
    } or declared_value is None
    semantic_change = declared_value is not None and declared_value != evidence_value

    claim_payload["state_dimension"] = "relationship.stage"
    claim_payload["state_value"] = evidence_value
    claim["payload"] = claim_payload
    claim["predicate_type"] = PredicateType.CANONICAL.value
    claim["canonical_predicate"] = "relationship.stage"
    claim.pop("custom_predicate", None)
    claim["state_dimension"] = "relationship.stage"
    claim["state_value"] = evidence_value

    if missing_shape:
        return ["relationship_stage_shape_repair"]
    if semantic_change:
        return ["relationship_stage_semantic_normalization"]
    return []


def _relationship_stage_from_evidence(text: str) -> tuple[str | None, str]:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return None, "missing_evidence"
    clauses = [
        value
        for value in re.split(
            r"[，,。；;！？!?]+|(?=(?:但|不过|后来|昨天|今天|现在))",
            compact,
        )
        if value
    ]
    decisions: list[tuple[str | None, str]] = []
    historical_stage_seen = False
    for clause in clauses:
        value, reason = _relationship_stage_clause(clause)
        if reason == "historical_only":
            historical_stage_seen = True
            continue
        if value is not None or reason in {
            "conflict_resolution",
            "historical_ended",
            "negative_or_conflicting",
            "speculative",
        }:
            decisions.append((value, reason))
    if decisions:
        return decisions[-1]
    if _RELATIONSHIP_STAGE_VAGUE_STABILITY_PATTERN.search(compact):
        return None, "vague_stability"
    if historical_stage_seen:
        return None, "historical_only"
    return None, "no_stage_evidence"


def _relationship_stage_clause(clause: str) -> tuple[str | None, str]:
    if _RELATIONSHIP_STAGE_SPECULATIVE_PATTERN.search(clause):
        return None, "speculative"
    if _RELATIONSHIP_STAGE_NEGATED_TRANSITION_PATTERN.search(clause):
        return None, "negative_or_conflicting"
    if (
        _RELATIONSHIP_STAGE_HISTORICAL_PATTERN.search(clause)
        and not _RELATIONSHIP_STAGE_CURRENT_PATTERN.search(clause)
        and _RELATIONSHIP_STAGE_GENERIC_CUE_PATTERN.search(clause)
    ):
        return None, "historical_only"
    if _RELATIONSHIP_STAGE_BREAKUP_PATTERN.search(clause):
        return "separated", "explicit"
    if _RELATIONSHIP_STAGE_REUNION_PATTERN.search(clause):
        return "reconciled", "explicit"

    negated_dating = _RELATIONSHIP_STAGE_NEGATED_DATING_PATTERN.search(clause) is not None
    signals: set[str] = set()
    if _RELATIONSHIP_STAGE_ACQUAINTANCE_PATTERN.search(clause):
        signals.add("acquaintance")
    if _RELATIONSHIP_STAGE_DATING_PATTERN.search(clause) and not negated_dating:
        signals.add("dating")
    if _RELATIONSHIP_STAGE_COMMITTED_PATTERN.search(clause):
        signals.add("committed")
    if "committed" in signals and "acquaintance" not in signals:
        return "committed", "explicit"
    if len(signals) == 1:
        return next(iter(signals)), "explicit"
    if len(signals) > 1 or negated_dating:
        if signals == {"acquaintance"}:
            return "acquaintance", "explicit"
        return None, "negative_or_conflicting"
    if _RELATIONSHIP_STAGE_CONFLICT_RESOLUTION_PATTERN.search(clause):
        return None, "conflict_resolution"
    return None, "no_stage_evidence"


def _has_relationship_stage_clue(
    claim: dict[str, object],
    claim_payload: dict[str, object],
    evidence_value: str | None,
) -> bool:
    values = (
        claim.get("predicate"),
        claim.get("raw_predicate"),
        claim.get("canonical_predicate"),
        claim.get("custom_predicate"),
        claim.get("state_dimension"),
        claim_payload.get("state_dimension"),
    )
    if any(
        _stage_identifier(value) in {"relationship.stage", "relationship_stage"}
        for value in values
    ):
        return True

    normalized = normalize_predicate(
        kind=MemoryKind.RELATIONSHIP_STATE.value,
        raw_predicate=claim.get("raw_predicate") or claim.get("predicate"),
        canonical_predicate=claim.get("canonical_predicate"),
        custom_predicate=claim.get("custom_predicate"),
        predicate_type=claim.get("predicate_type"),
        payload=claim_payload,
    )
    if normalized.canonical_predicate == "relationship.stage":
        return True
    if normalized.canonical_predicate is not None or normalized.state_dimension is not None:
        return False

    generic_identifiers = {
        "",
        "has_state",
        "relationship_state",
        "relationship_status",
        "relationship_stage_status",
    }
    declared_identifiers = {
        _stage_identifier(value)
        for value in (
            claim.get("predicate"),
            claim.get("raw_predicate"),
            claim.get("canonical_predicate"),
            claim.get("custom_predicate"),
        )
        if value is not None
    }
    return evidence_value is not None and (
        not declared_identifiers or declared_identifiers <= generic_identifiers
    )


def _canonical_relationship_stage_value(value: object) -> str | None:
    if value is None:
        return None
    normalized = normalize_predicate(
        kind=MemoryKind.RELATIONSHIP_STATE.value,
        canonical_predicate="relationship.stage",
        predicate_type=PredicateType.CANONICAL.value,
        payload={"state_value": value},
    )
    if normalized.canonical_predicate != "relationship.stage":
        return None
    return normalized.state_value


def _align_exact_canonical_predicate(
    claim: dict[str, object],
    claim_payload: dict[str, object],
    kind: str,
) -> bool:
    if _enum_key(claim.get("predicate_type")) != PredicateType.CANONICAL.value:
        return False
    if claim.get("canonical_predicate"):
        return False
    predicate = claim.get("predicate")
    if not isinstance(predicate, str) or predicate not in CANONICAL_PREDICATES:
        return False
    if kind == MemoryKind.PREFERENCE.value:
        if not predicate.startswith("preference.") or not claim_payload.get("preference"):
            return False
    elif kind == MemoryKind.RELATIONSHIP_STATE.value:
        spec = CANONICAL_PREDICATES[predicate]
        if spec.state_dimension is None:
            return False
    else:
        return False
    normalized = normalize_predicate(
        kind=kind,
        raw_predicate=predicate,
        predicate_type=PredicateType.CANONICAL.value,
        payload=claim_payload,
    )
    if normalized.canonical_predicate != predicate:
        return False
    claim["canonical_predicate"] = predicate
    claim.pop("custom_predicate", None)
    if normalized.state_dimension is not None:
        claim["state_dimension"] = normalized.state_dimension
        claim_payload["state_dimension"] = normalized.state_dimension
    if normalized.state_value is not None:
        claim["state_value"] = normalized.state_value
        claim_payload["state_value"] = normalized.state_value
    if claim_payload:
        claim["payload"] = claim_payload
    return True


def _stage_identifier(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.casefold().strip().replace("-", "_").replace(" ", "_")


def _repair_attempt_name(steps: list[str]) -> str:
    if "relationship_stage_shape_repair" in steps:
        return "relationship_stage_bounded_repair"
    if "relationship_stage_semantic_normalization" in steps:
        return "relationship_stage_semantic_normalization"
    if "relationship_stage_fail_closed" in steps:
        return "relationship_stage_semantic_guard"
    return "none"


def _safe_json_snapshot(value: object, *, limit: int = 2000) -> str:
    sensitive_keys = {
        "apikey",
        "authorization",
        "password",
        "secret",
        "token",
        "accesstoken",
        "refreshtoken",
        "clientsecret",
        "privatekey",
    }

    def is_sensitive_key(value: object) -> bool:
        normalized = re.sub(r"[-_\s]+", "", str(value).casefold())
        return normalized in sensitive_keys or normalized.endswith(
            ("apikey", "password", "secret", "token", "privatekey")
        )

    def sanitize(item: object) -> object:
        if isinstance(item, dict):
            return {
                str(key): "[REDACTED]" if is_sensitive_key(key) else sanitize(child)
                for key, child in item.items()
            }
        if isinstance(item, list):
            return [sanitize(child) for child in item[:5]]
        return item

    try:
        sanitized = sanitize(value)
        snapshot = json.dumps(
            sanitized,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        snapshot = str(value)
    if len(snapshot) <= limit:
        return snapshot
    return json.dumps(
        [
            {
                "snapshot_truncated": True,
                "preview": snapshot[: max(200, limit // 3)],
            }
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _registered_relationship_state(
    claim: AtomicClaim,
) -> tuple[str | None, str | None]:
    normalized = normalize_predicate(
        kind=claim.kind,
        raw_predicate=claim.raw_predicate or claim.predicate,
        canonical_predicate=claim.canonical_predicate,
        custom_predicate=claim.custom_predicate,
        predicate_type=claim.predicate_type,
        payload=claim.payload,
    )
    if normalized.state_dimension is not None and normalized.state_value is not None:
        return normalized.state_dimension, normalized.state_value
    dimension = normalize_state_dimension(claim.payload.get("state_dimension"))
    return dimension, normalize_state_value(
        dimension,
        claim.payload.get("state_value"),
    )


def _is_open_world_social_integration_claim(claim: AtomicClaim) -> bool:
    normalized = normalize_predicate(
        kind=claim.kind,
        raw_predicate=claim.raw_predicate or claim.predicate,
        canonical_predicate=claim.canonical_predicate,
        custom_predicate=claim.custom_predicate,
        predicate_type=claim.predicate_type,
        payload=claim.payload,
    )
    if normalized.canonical_predicate not in {None, "relationship.familiarity"}:
        return False
    evidence = " ".join(claim.evidence_spans)
    return open_world_social_integration_predicate(evidence) is not None


def _claim_has_temporal_anchor(claim: dict[str, object]) -> bool:
    if any(
        claim.get(field)
        for field in ("occurred_at", "period_start", "period_end", "expires_at")
    ):
        return True
    payload = claim.get("payload")
    if isinstance(payload, dict) and payload.get("temporal_expression"):
        return True
    evidence = claim.get("evidence_spans")
    text = " ".join(str(value) for value in evidence) if isinstance(evidence, list) else ""
    return re.search(
        r"明天|后天|大后天|下周|下个月|本周末|这周末|周末|过几天|几天后|月底|"
        r"周[一二三四五六日天]|\d{1,2}月\d{1,2}[日号]?",
        text,
    ) is not None


def _claim_expresses_action_commitment(claim: dict[str, object]) -> bool:
    evidence = claim.get("evidence_spans")
    if not isinstance(evidence, list):
        return False
    text = " ".join(str(value) for value in evidence)
    return re.search(
        r"决定|打算|准备|计划|安排|下一步|先.{0,30}(?:再|然后)|然后再|之后再",
        text,
    ) is not None


def _validate_discarded_span(
    discarded: DiscardedSpan,
    claims: list[AtomicClaim],
) -> None:
    for claim in claims:
        for evidence in claim.evidence_spans:
            if _spans_overlap(discarded.text, evidence):
                raise ValueError(
                    f"丢弃片段与已保存声明证据重叠：{discarded.text}"
                )


def _spans_overlap(first: str, second: str) -> bool:
    normalized_first = _normalize_span(first)
    normalized_second = _normalize_span(second)
    if min(len(normalized_first), len(normalized_second)) < 2:
        return False
    return normalized_first in normalized_second or normalized_second in normalized_first


def _normalize_span(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value).casefold()


def _validation_error_text(exc: ValidationError | ValueError) -> str:
    if isinstance(exc, ValidationError):
        return _validation_detail(exc)
    return str(exc)


def _add_safe_container_defaults(payload: dict[str, object]) -> bool:
    changed = False
    for field in ("claims", "discarded_spans"):
        if field not in payload or payload[field] is None:
            payload[field] = []
            changed = True
        elif not isinstance(payload[field], list):
            # A wrong container type can hide lost claims and is not safe to
            # coerce. Let Pydantic report it and discard the response.
            return changed
    return changed


def _strip_code_fence(value: str) -> str:
    match = re.fullmatch(r"\s*```(?:json)?\s*\n?(.*?)\n?```\s*", value, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else value.strip()


def _extract_balanced_json(value: str) -> str:
    """Extract the first balanced object while respecting quoted braces."""

    text = value.strip()
    start = text.find("{")
    if start < 0:
        raise MemoryResponseError(
            "记忆抽取结果中没有 JSON 对象。",
            category="json_syntax",
        )

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    raise MemoryResponseError(
        "记忆抽取结果中的 JSON 对象不完整。",
        category="json_syntax",
    )


def _remove_trailing_commas(value: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", value)


def _json_error_detail(exc: json.JSONDecodeError) -> str:
    return f"第 {exc.lineno} 行第 {exc.colno} 列 - {exc.msg}"


def _validation_detail(exc: ValidationError) -> str:
    details: list[str] = []
    for error in exc.errors(include_url=False)[:5]:
        location = ".".join(str(part) for part in error["loc"])
        detail = f"{location or 'root'} - {error['msg']}"
        input_value = error.get("input")
        if isinstance(input_value, (str, int, float, bool)):
            rendered = repr(input_value)
            if len(rendered) <= 80:
                detail += f" (input={rendered})"
        details.append(detail)
    return "; ".join(details)

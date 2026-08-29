"""Deterministic parsing and validation for memory-model responses.

The model is allowed to be imperfect at the JSON boundary. Local repair may
normalize an explicitly expressed concept, but it must never invent a fact
that is absent from the user's text.
"""

import json
import re
from dataclasses import dataclass

from pydantic import ValidationError

from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    DiscardedSpan,
    EvidenceExplicitness,
    MemoryKind,
    MemoryPerspective,
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
from loveapp.domain.memory_predicates import CANONICAL_PREDICATES, normalize_predicate


@dataclass(frozen=True)
class ParsedMemoryResponse:
    extraction: AtomicExtraction
    repair_status: str
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
) -> ParsedMemoryResponse:
    """Parse a response after only safe, deterministic normalization."""

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

    normalized = dict(payload)
    defaults_applied = _add_safe_container_defaults(normalized)
    if defaults_applied:
        steps.append("default_fields")
    if _normalize_enum_aliases(normalized):
        steps.append("enum_aliases")
    steps.extend(_normalize_claim_semantics(normalized))

    _validate_root_shape(normalized)
    valid_claims: list[AtomicClaim] = []
    invalid_claim_reasons: list[str] = []
    repaired_claim_count = 0
    claim_ids: set[str] = set()
    for index, raw_claim in enumerate(normalized["claims"]):
        try:
            claim = AtomicClaim.model_validate(raw_claim)
            validate_memory_claim(claim, source_text, claim_ids)
        except ClaimAtomicityError as exc:
            repaired_claim = _repair_non_atomic_claim(claim, source_text)
            if repaired_claim is not None:
                try:
                    validate_memory_claim(repaired_claim, source_text, claim_ids)
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

    if normalized["claims"] and not valid_claims:
        category = _claim_failure_category(invalid_claim_reasons)
        detail = "; ".join(invalid_claim_reasons[:5])
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
                "repair_status": failure.repair_status,
                "repair_steps": failure.repair_steps,
            }
        )
        raise failure

    if invalid_claim_reasons:
        steps.append("partial_claims")
    if invalid_discarded_reasons:
        steps.append("partial_discarded_spans")
    if any("与已保存声明证据重叠" in reason for reason in invalid_discarded_reasons):
        steps.append("discarded_overlap")
    extraction = AtomicExtraction(
        claims=valid_claims,
        discarded_spans=valid_discarded,
    )
    return ParsedMemoryResponse(
        extraction=extraction,
        repair_status="local_repair" if steps else "direct",
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
    if claim.claim_id in claim_ids:
        raise ValueError(f"原子声明 ID 重复：{claim.claim_id}")
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
    if source_text is not None:
        for evidence in claim.evidence_spans:
            if evidence not in source_text:
                raise ValueError(f"证据片段不在用户原文中：{evidence}")
    if not re.search(r"[\u4e00-\u9fff]", claim.summary):
        raise ValueError(f"声明 {claim.claim_id} 的 summary 必须使用简体中文")
    preference = claim.payload.get("preference")
    if claim.kind == MemoryKind.PREFERENCE and isinstance(preference, list):
        raise ValueError(f"偏好声明 {claim.claim_id} 包含多个 preference，必须拆分")
    if claim.kind == MemoryKind.INTERACTION_PATTERN:
        metric = normalize_interaction_metric(claim.payload.get("metric"))
        if metric is None:
            raise ValueError(f"互动模式 {claim.claim_id} 缺少单一 payload.metric")
    if claim.kind == MemoryKind.RELATIONSHIP_STATE:
        dimension, value = _registered_relationship_state(claim)
        if dimension is None or value is None:
            raise ValueError(
                f"关系状态 {claim.claim_id} 缺少已注册的 state_dimension/state_value"
            )
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


def _repair_non_atomic_claim(
    claim: AtomicClaim,
    source_text: str | None,
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
    if claim.kind == MemoryKind.INTERACTION_PATTERN:
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
    unknown = set(payload) - {"claims", "discarded_spans"}
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


def _normalize_claim_semantics(payload: dict[str, object]) -> list[str]:
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
        raw_payload = claim.get("payload")
        claim_payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
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
        if kind == MemoryKind.INTERACTION_PATTERN.value:
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

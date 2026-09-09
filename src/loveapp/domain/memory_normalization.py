from __future__ import annotations

import re
from datetime import datetime

from loveapp.domain.memory import (
    MemoryCandidate,
    MemoryKind,
    MemoryPerspective,
    PredicateType,
    TimeKind,
)
from loveapp.domain.memory_dimensions import (
    INTERACTION_PATTERN_DIMENSIONS,
    dimension_for_predicate,
    infer_initiation_balance,
    interaction_pattern_state,
    normalize_interaction_metric,
    normalize_state_dimension,
    normalize_state_value,
    validate_interaction_event_payload,
    validate_interaction_pattern_payload,
)
from loveapp.domain.memory_epistemics import normalize_memory_epistemics
from loveapp.domain.memory_lifecycle import normalize_memory_candidate
from loveapp.domain.memory_predicates import CANONICAL_PREDICATES, normalize_predicate

_PATTERN_CONFIDENCE_FLOOR = 0.75
_PATTERN_RECURRENCE_PATTERN = re.compile(
    r"(?:经常|常常|总是|通常|每(?:天|周|月|次)|反复|多次|一直|持续|"
    r"基本都|几乎都|大多数时候|越来越|频繁|很少|偶尔|又开始|再次|"
    r"\b(?:often|usually|always|every\s+(?:day|week|month|time)|"
    r"repeatedly|over\s+time|increasingly|decreasingly)\b)",
    re.IGNORECASE,
)
_INTERACTION_TREND_PATTERN = re.compile(
    r"(?:(?:明显|显著|逐渐|持续)(?:下降|上升|增加|减少|变慢|变少|变多|改善|恶化)|"
    r"越来越(?:慢|少|多|好|差)|"
    r"\b(?:declin(?:e|ed|ing)|increas(?:e|ed|ing)|improv(?:e|ed|ing)|"
    r"worsen(?:ed|ing)|slowing|slower)\b)",
    re.IGNORECASE,
)
_PATTERN_WINDOW_PATTERN = re.compile(
    r"(?:最近|近来|这段时间|过去|近(?:一|两|三|四|五|六|几|\d+)"
    r"(?:天|周|个月|月|年)|"
    r"\b(?:recently|lately|over\s+the\s+(?:last|past)|for\s+the\s+last)\b)",
    re.IGNORECASE,
)
_SINGLE_EVENT_TIME_PATTERN = re.compile(
    r"(?:昨天|昨晚|今早|今天|前天|刚刚|刚才|那天|这次|上周(?:一|二|三|四|五|六|日|天)?|"
    r"上个月(?:\d+号|某天)?|"
    r"\b(?:yesterday|today|last\s+night|this\s+morning|just\s+now|"
    r"last\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b)",
    re.IGNORECASE,
)
_SINGLE_INTERACTION_ACTION_PATTERN = re.compile(
    r"(?:主动.{0,8}(?:找|联系|发消息|聊天|约|分享|告诉)|"
    r"(?:找我|联系我|给我发|约我|回复了|回了|打电话|见面|吃饭|分享了|告诉我)|"
    r"\b(?:initiated|contacted|messaged|replied|called|invited|met|shared|told)\b)",
    re.IGNORECASE,
)
_PATTERN_ONLY_PAYLOAD_FIELDS = {
    "baseline",
    "current",
    "direction",
    "evidence",
    "evidence_ids",
    "positive_evidence_ids",
    "negative_evidence_ids",
    "frequency",
    "metric",
    "time_range",
    "time_window",
    "window",
    "state",
}
_PSYCHOLOGICAL_PATTERN_CONCLUSION = re.compile(
    r"(?:越来越|更|变得|开始)?(?:喜欢|爱上|爱|在乎|离不开|依赖|信任|讨厌|厌烦)"
    r"|\b(?:likes?|loves?|cares?\s+about|trusts?|depends?\s+on|hates?)\b",
    re.IGNORECASE,
)
_OBSERVABLE_PATTERN_EVIDENCE = re.compile(
    r"(?:主动|联系|发消息|聊天|回复|回应|见面|约|邀请|分享|告诉|通话|电话|"
    r"吵架|争吵|争执|矛盾|冲突|冷战|和好|说开|拒绝|不理|没有联系|没联系)"
    r"|\b(?:initiat|contact|messag|chat|repl|respond|meet|invit|share|tell|call|"
    r"argu|fight|conflict|reconcil|ignore|refus)\w*\b",
    re.IGNORECASE,
)
_PREFERENCE_CUE_PATTERN = re.compile(
    r"(?:喜欢|偏好|爱吃|爱好|感兴趣|希望.{0,24}(?:交流|沟通|联系|理解|陪伴)|"
    r"不喜欢|不希望|讨厌|倾向于|习惯)"
    r"|\b(?:prefer|like|dislike|interested\s+in|would\s+like)\b",
    re.IGNORECASE,
)
_ROMANTIC_INTEREST_TARGET_PATTERN = re.compile(
    r"(?:喜欢|爱|暗恋|心动|有好感).{0,6}(?:她|他|对方|女生|男生|女孩|男孩)"
    r"|(?:她|他|对方|女生|男生|女孩|男孩).{0,6}(?:喜欢|爱|暗恋|心动|有好感)",
)
_PREFERENCE_ONTOLOGY_RULES: tuple[
    tuple[str, str, re.Pattern[str], str | None],
    ...,
] = (
    (
        "relationship",
        "conflict_resolution",
        re.compile(r"(?:冷处理|冷战|吵架后|冲突后).{0,16}(?:沟通|说开|解决|一直|不要|避免)"),
        "avoid_silent_treatment",
    ),
    (
        "communication",
        "frequency",
        re.compile(r"(?:多交流|多沟通|常联系|经常沟通|保持联系|沟通频率)"),
        "frequent",
    ),
    (
        "emotional",
        "need",
        re.compile(r"(?:安全感|被理解|被安慰|情绪支持|陪伴|表达感受|情感需求)"),
        None,
    ),
    (
        "food",
        "taste",
        re.compile(r"(?:吃辣|口味.{0,6}(?:辣|甜|咸|酸)|(?:喜欢|偏好).{0,8}(?:辣|甜|咸|酸))"),
        None,
    ),
    (
        "food",
        "cuisine",
        re.compile(r"(?:喜欢|偏好|爱吃).{0,12}(?:菜|料理|寿司|火锅|食物|餐|饭|面|肉|鱼)"),
        None,
    ),
    (
        "interest",
        "topic",
        re.compile(r"(?:对.{0,16}(?:感兴趣|有兴趣)|兴趣(?:是|在于)|关注.{0,12}(?:领域|话题))"),
        None,
    ),
    (
        "lifestyle",
        "habit",
        re.compile(r"(?:晚上|早上|睡前|每天|平时|习惯|作息|一个人|独处).{0,18}(?:听|看|走|跑|运动|阅读|音乐)"),
        None,
    ),
    (
        "hobby",
        "activity",
        re.compile(r"(?:喜欢|爱好|常玩|常打|常听|常看).{0,16}(?:篮球|足球|羽毛球|跑步|游泳|健身|音乐|画画|绘画|乐器|桌游|游戏|摄影)"),
        None,
    ),
)
_PREFERENCE_DEFAULT_DIMENSION = {
    "food": "cuisine",
    "hobby": "activity",
    "interest": "topic",
    "lifestyle": "habit",
    "communication": "style",
    "communication_frequency": "frequency",
    "relationship": "partner_trait",
    "conflict_resolution": "conflict_resolution",
    "emotional": "need",
}


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

    if candidate.kind == MemoryKind.INTERACTION_EVENT:
        try:
            validate_interaction_event_payload(
                candidate.payload,
                perspective=candidate.perspective,
            )
        except ValueError as exc:
            raise NormalizationContractError(
                "INTERACTION_EVENT_PAYLOAD_INVALID",
                str(exc),
            ) from exc

    if candidate.kind == MemoryKind.INTERACTION_PATTERN:
        try:
            validate_interaction_pattern_payload(
                candidate.payload,
                perspective=candidate.perspective,
            )
        except ValueError as exc:
            raise NormalizationContractError(
                "INTERACTION_PATTERN_PAYLOAD_INVALID",
                str(exc),
            ) from exc
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
        if candidate.perspective == MemoryPerspective.MODEL_INFERRED:
            if not _has_payload_value(candidate.payload.get("time_window")):
                raise NormalizationContractError(
                    "INTERACTION_PATTERN_PAYLOAD_INVALID",
                    "model_inferred interaction_pattern requires time_window",
                )
            if candidate.confidence < _PATTERN_CONFIDENCE_FLOOR:
                raise NormalizationContractError(
                    "INTERACTION_PATTERN_CONFIDENCE_INSUFFICIENT",
                    f"{candidate.confidence:.4f}",
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
    candidate = normalize_memory_epistemics(candidate)
    candidate = align_foundational_ontology_boundary(candidate)
    candidate = align_interaction_event_pattern_boundary(candidate)
    candidate = enforce_interaction_pattern_observability(candidate)
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


def align_foundational_ontology_boundary(
    candidate: MemoryCandidate,
) -> MemoryCandidate:
    """Map Fact/Preference semantics without trusting the model's final kind.

    The mapper is deliberately narrower than extraction: it recognizes only
    reviewed preference categories and never turns a single observed action
    into a durable preference.  Legacy epistemic propositions keep their
    storage-compatible kind, but are explicitly marked as beliefs and cannot
    acquire governed ``profile.*`` fact semantics.
    """

    payload = dict(candidate.payload)
    evidence = " ".join(
        part
        for part in [candidate.original_text, *candidate.evidence_spans]
        if isinstance(part, str) and part.strip()
    )

    if (
        candidate.kind == MemoryKind.STABLE_FACT
        and candidate.perspective == MemoryPerspective.USER_BELIEF
    ):
        payload["semantic_type"] = "belief"
        payload["objective_fact"] = False
        updates: dict[str, object] = {"payload": payload}
        if (
            isinstance(candidate.canonical_predicate, str)
            and candidate.canonical_predicate.startswith("profile.")
        ):
            custom = candidate.custom_predicate or _belief_custom_predicate(candidate)
            payload["predicate"] = custom
            updates.update(
                {
                    "payload": payload,
                    "raw_predicate": custom,
                    "predicate_type": PredicateType.CUSTOM,
                    "canonical_predicate": None,
                    "custom_predicate": custom,
                    "state_dimension": None,
                    "state_value": None,
                }
            )
        return candidate.model_copy(update=updates)

    if candidate.kind not in {MemoryKind.STABLE_FACT, MemoryKind.PREFERENCE}:
        return candidate
    if candidate.kind == MemoryKind.STABLE_FACT and _is_registered_profile_declaration(
        candidate
    ):
        return candidate

    explicit_preference = _has_explicit_preference_shape(payload)
    preference_language = bool(_PREFERENCE_CUE_PATTERN.search(evidence))
    romantic_interest = bool(_ROMANTIC_INTEREST_TARGET_PATTERN.search(evidence))
    if candidate.kind == MemoryKind.STABLE_FACT and (
        not (explicit_preference or preference_language) or romantic_interest
    ):
        return candidate

    domain, dimension, canonical_value = _preference_ontology_classification(
        payload,
        evidence,
    )
    if domain is None or dimension is None:
        # Existing Preference rows may remain open-world Custom.  A Fact is
        # reclassified only when the reviewed ontology can name its category.
        return candidate
    value = _preference_value(payload, evidence, canonical_value=canonical_value)
    if value is None:
        return candidate

    payload["semantic_type"] = "preference"
    payload["domain"] = domain
    payload["dimension"] = dimension
    payload["preference"] = value
    payload.setdefault("value", value)
    if canonical_value == "avoid_silent_treatment":
        payload["preference_type"] = "avoid"
    else:
        payload.setdefault(
            "preference_type",
            "dislike" if re.search(r"(?:不喜欢|讨厌|避免|不要)", evidence) else "like",
        )
    return candidate.model_copy(
        update={
            "kind": MemoryKind.PREFERENCE,
            "payload": payload,
            "time_kind": (
                TimeKind.TIMELESS
                if candidate.time_kind == TimeKind.UNKNOWN
                else candidate.time_kind
            ),
        }
    )


def _has_explicit_preference_shape(payload: dict[str, object]) -> bool:
    return any(
        payload.get(field) is not None
        for field in (
            "preference",
            "preference_type",
            "preference_type_hint",
            "preference_domain",
            "preference_dimension",
        )
    ) or str(payload.get("domain") or "").casefold() in {
        "food",
        "hobby",
        "interest",
        "lifestyle",
        "communication",
        "communication_frequency",
        "relationship",
        "conflict_resolution",
        "emotional",
    }


def _preference_ontology_classification(
    payload: dict[str, object],
    evidence: str,
) -> tuple[str | None, str | None, str | None]:
    raw_domain = str(
        payload.get("domain")
        or payload.get("preference_domain")
        or payload.get("category")
        or ""
    ).casefold().strip().replace("-", "_")
    domain_aliases = {
        "personal_interest": "interest",
        "communication_frequency": "communication",
        "conflict_resolution": "relationship",
    }
    domain = domain_aliases.get(raw_domain, raw_domain) or None
    raw_dimension = str(
        payload.get("dimension") or payload.get("preference_dimension") or ""
    ).casefold().strip().replace("-", "_")
    if domain is not None:
        dimension = raw_dimension or _PREFERENCE_DEFAULT_DIMENSION.get(raw_domain or domain)
        if dimension is not None:
            return domain, dimension, None
    for matched_domain, dimension, pattern, canonical_value in _PREFERENCE_ONTOLOGY_RULES:
        if pattern.search(evidence) is not None:
            return matched_domain, dimension, canonical_value
    return None, None, None


def _preference_value(
    payload: dict[str, object],
    evidence: str,
    *,
    canonical_value: str | None,
) -> str | None:
    if canonical_value is not None:
        return canonical_value
    for field in ("preference", "value", "object"):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    extractors = (
        re.compile(r"对(?P<value>.{1,40}?)很感兴趣"),
        re.compile(r"(?:喜欢|爱吃|偏好|爱好)(?P<value>.{1,40})"),
        re.compile(r"希望.{0,20}(?P<value>多(?:交流|沟通|联系))"),
    )
    for pattern in extractors:
        match = pattern.search(evidence)
        if match is not None:
            return match.group("value").strip(" 。.!！?？")
    return None


def _is_registered_profile_declaration(candidate: MemoryCandidate) -> bool:
    values = (
        candidate.canonical_predicate,
        candidate.raw_predicate,
        candidate.payload.get("predicate"),
    )
    return any(
        isinstance(value, str) and value.startswith("profile.")
        for value in values
    )


def _belief_custom_predicate(candidate: MemoryCandidate) -> str:
    source = candidate.raw_predicate or candidate.payload.get("predicate") or "proposition"
    normalized = re.sub(r"[^a-z0-9_]+", "_", str(source).casefold()).strip("_")
    return f"believed_{normalized or 'proposition'}"[:120]


def align_interaction_event_pattern_boundary(
    candidate: MemoryCandidate,
) -> MemoryCandidate:
    """Demote an explicitly bounded one-off behavior misclassified as Pattern.

    This is intentionally one-way.  A single turn can establish an Event, but
    it cannot promote itself to a Pattern; durable Pattern inference belongs to
    evidence-backed consolidation.
    """

    if candidate.kind != MemoryKind.INTERACTION_PATTERN:
        return candidate
    evidence = " ".join(
        part
        for part in [candidate.original_text, *candidate.evidence_spans]
        if isinstance(part, str) and part.strip()
    )
    recurrence = interaction_pattern_has_recurrence_signal(candidate)
    window = interaction_pattern_has_time_window(candidate)
    pattern_eligible = (
        recurrence and window and candidate.confidence >= _PATTERN_CONFIDENCE_FLOOR
    )
    if pattern_eligible:
        return candidate

    structured_action = any(
        isinstance(candidate.payload.get(field), str)
        and bool(str(candidate.payload[field]).strip())
        for field in ("action", "activity_type", "outcome")
    )
    bounded_action = structured_action or bool(
        _SINGLE_INTERACTION_ACTION_PATTERN.search(evidence)
    )
    point_in_time = (
        candidate.time_kind == TimeKind.POINT
        or candidate.occurred_at is not None
        or bool(_SINGLE_EVENT_TIME_PATTERN.search(evidence))
    )
    # A behaviour verb alone does not prove a single occurrence.  Historical
    # or interval-shaped descriptions may legitimately describe a Pattern
    # even when the wording is unusual (for example, "last year she almost
    # never initiated contact").  Keep a recurring-but-windowless claim as a
    # Pattern so Admission can reject/review it explicitly; demote only a
    # genuinely one-off action (a point cue, or a non-recurring action with no
    # interval shape at all).
    interval_shape = (
        candidate.time_kind == TimeKind.INTERVAL
        or candidate.period_start is not None
        or candidate.period_end is not None
    )
    trend_shape = _has_payload_value(candidate.payload.get("direction")) or bool(
        _INTERACTION_TREND_PATTERN.search(evidence)
    )
    single_behavior = point_in_time or (
        not recurrence and not interval_shape and not trend_shape
    )
    if not bounded_action or not single_behavior:
        # Admission owns the strict Pattern recurrence/window/confidence gate.
        # Normalization only repairs a clear single-behavior type error.
        return candidate
    payload = {
        key: value
        for key, value in candidate.payload.items()
        if key not in _PATTERN_ONLY_PAYLOAD_FIELDS
    }
    action = payload.get("action") or payload.get("activity_type")
    if not isinstance(action, str) or not action.strip():
        raw_action = candidate.raw_predicate or candidate.custom_predicate
        if isinstance(raw_action, str) and raw_action.strip():
            payload["action"] = raw_action.strip()
    payload["type_refinement"] = "single_behavior_pattern_to_event"
    updates: dict[str, object] = {
        "kind": MemoryKind.INTERACTION_EVENT,
        "payload": payload,
        "state_dimension": None,
        "state_value": None,
    }
    if candidate.time_kind == TimeKind.UNKNOWN or not point_in_time:
        updates["time_kind"] = TimeKind.POINT
    return candidate.model_copy(update=updates)


def enforce_interaction_pattern_observability(
    candidate: MemoryCandidate,
) -> MemoryCandidate:
    """Keep Patterns on observable interaction dimensions, not mental conclusions.

    A mixed sentence may include both an observable trend and the user's
    interpretation of it.  In that case the Pattern is projected onto the
    observable metric while the original evidence remains intact.  A pure
    psychological conclusion has no safe Pattern representation and fails
    closed at normalization.
    """

    if candidate.kind != MemoryKind.INTERACTION_PATTERN:
        return candidate
    evidence = " ".join(
        part
        for part in [candidate.original_text, *candidate.evidence_spans]
        if isinstance(part, str) and part.strip()
    )
    if not _PSYCHOLOGICAL_PATTERN_CONCLUSION.search(evidence):
        return candidate
    if not _OBSERVABLE_PATTERN_EVIDENCE.search(evidence):
        raise NormalizationContractError(
            "INTERACTION_PATTERN_NOT_OBSERVATIONAL",
            "a psychological conclusion cannot be represented as an interaction Pattern",
        )

    payload = dict(candidate.payload)
    metric = normalize_interaction_metric(payload.get("metric"))
    if metric not in INTERACTION_PATTERN_DIMENSIONS or metric == "emotional_disclosure":
        metric = _observable_metric_from_evidence(evidence)
    if metric is None:
        raise NormalizationContractError(
            "INTERACTION_PATTERN_NOT_OBSERVATIONAL",
            "the source contains no uniquely supported observable interaction metric",
        )

    canonical = f"interaction.{metric}"
    payload["predicate"] = canonical
    payload["metric"] = metric
    if metric == "initiation_balance" and interaction_pattern_state(metric, payload) is None:
        initiation = infer_initiation_balance(evidence)
        if initiation is not None:
            payload["current"] = initiation
    payload["observational_projection"] = "psychological_conclusion_to_observed_trend"
    state_value = interaction_pattern_state(metric, payload)
    return candidate.model_copy(
        update={
            "summary": _observational_pattern_summary(metric, state_value, payload),
            "payload": payload,
            "raw_predicate": canonical,
            "predicate_type": PredicateType.CANONICAL,
            "canonical_predicate": canonical,
            "custom_predicate": None,
            "state_dimension": canonical,
            "state_value": state_value,
        }
    )


def _observable_metric_from_evidence(evidence: str) -> str | None:
    if re.search(
        r"回复|回应|接话|敷衍|\b(?:repl|respond)\w*\b",
        evidence,
        re.IGNORECASE,
    ):
        return "response_engagement"
    if re.search(
        r"吵架|争吵|争执|矛盾|冲突|冷战|\b(?:argu|fight|conflict)\w*\b",
        evidence,
        re.IGNORECASE,
    ):
        return "conflict_frequency"
    if re.search(
        r"主动.{0,10}(?:联系|找|发消息|聊天|约|邀请|分享|告诉)"
        r"|\b(?:initiat|started).{0,18}(?:contact|messag|chat|invit|share)\w*\b",
        evidence,
        re.IGNORECASE,
    ):
        return "initiation_balance"
    if re.search(
        r"联系|发消息|聊天|见面|通话|电话|\b(?:contact|messag|chat|meet|call)\w*\b",
        evidence,
        re.IGNORECASE,
    ):
        return "contact_frequency"
    return None


def _observational_pattern_summary(
    metric: str,
    state_value: str | None,
    payload: dict[str, object],
) -> str:
    state = state_value or str(payload.get("direction") or payload.get("current") or "").strip()
    if metric == "initiation_balance":
        if state == "partner_to_user":
            return "对方近期更常主动发起互动。"
        if state == "user_to_partner":
            return "近期互动更多由用户主动发起。"
        return "双方近期的互动主动性出现了可观察变化。"
    if metric == "response_engagement":
        return "对方近期的回应参与度出现了可观察变化。"
    if metric == "conflict_frequency":
        return "双方近期的冲突频率出现了可观察变化。"
    return "双方近期的互动频率出现了可观察变化。"


def _has_payload_value(value: object) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def interaction_pattern_has_recurrence_signal(candidate: MemoryCandidate) -> bool:
    evidence = " ".join(
        part
        for part in [candidate.original_text, *candidate.evidence_spans]
        if isinstance(part, str) and part.strip()
    )
    if _PATTERN_RECURRENCE_PATTERN.search(evidence):
        return True
    if any(
        _has_payload_value(candidate.payload.get(field))
        for field in ("frequency", "baseline")
    ):
        return True
    evidence_ids = candidate.payload.get("evidence_ids")
    return isinstance(evidence_ids, list) and len(
        {value for value in evidence_ids if isinstance(value, str) and value}
    ) >= 2


def interaction_pattern_has_time_window(candidate: MemoryCandidate) -> bool:
    evidence = " ".join(
        part
        for part in [candidate.original_text, *candidate.evidence_spans]
        if isinstance(part, str) and part.strip()
    )
    return bool(_PATTERN_WINDOW_PATTERN.search(evidence)) or any(
        (
            candidate.period_start is not None and candidate.period_end is not None,
            candidate.time_kind == TimeKind.INTERVAL,
            _has_payload_value(candidate.payload.get("time_window")),
            _has_payload_value(candidate.payload.get("time_range")),
        )
    )


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

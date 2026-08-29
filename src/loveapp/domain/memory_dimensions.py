import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class RelationshipStatePolicy:
    dimension: str
    aliases: frozenset[str]
    allowed_values: frozenset[str]
    value_aliases: tuple[tuple[str, str], ...]
    default_ttl: timedelta


@dataclass(frozen=True)
class EvidenceDimensionPolicy:
    dimension: str
    patterns: tuple[re.Pattern[str], ...]


def _normalize_identifier(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"[\s-]+", "_", normalized)


RELATIONSHIP_STATE_POLICIES: tuple[RelationshipStatePolicy, ...] = (
    RelationshipStatePolicy(
        dimension="relationship_familiarity",
        aliases=frozenset({"familiarity", "relationship_closeness"}),
        allowed_values=frozenset({"unfamiliar", "low", "moderate", "high"}),
        value_aliases=(
            ("stranger", "unfamiliar"),
            ("not_familiar", "low"),
            ("slightly_familiar", "low"),
            ("medium", "moderate"),
            ("familiar", "high"),
            ("very_familiar", "high"),
        ),
        default_ttl=timedelta(days=90),
    ),
    RelationshipStatePolicy(
        dimension="contact_opportunity",
        aliases=frozenset({"meeting_opportunity", "interaction_opportunity"}),
        allowed_values=frozenset({"low", "moderate", "high"}),
        value_aliases=(
            ("rare", "low"),
            ("few", "low"),
            ("limited", "low"),
            ("medium", "moderate"),
            ("regular", "high"),
            ("frequent", "high"),
            ("many", "high"),
        ),
        default_ttl=timedelta(days=30),
    ),
    RelationshipStatePolicy(
        dimension="contact_availability",
        aliases=frozenset({"communication_availability", "reachability"}),
        allowed_values=frozenset({"unavailable", "limited", "available"}),
        value_aliases=(
            ("unreachable", "unavailable"),
            ("blocked", "unavailable"),
            ("partial", "limited"),
            ("reachable", "available"),
            ("restored", "available"),
        ),
        default_ttl=timedelta(days=3),
    ),
    RelationshipStatePolicy(
        dimension="conflict_status",
        aliases=frozenset({"relationship_conflict_status"}),
        allowed_values=frozenset({"active", "cooling", "repairing", "resolved"}),
        value_aliases=(
            ("unresolved", "active"),
            ("in_conflict", "active"),
            ("deescalating", "cooling"),
            ("reconciliation", "repairing"),
            ("repaired", "resolved"),
        ),
        default_ttl=timedelta(days=14),
    ),
    RelationshipStatePolicy(
        dimension="interaction_reciprocity",
        aliases=frozenset({"reciprocity", "interaction_balance"}),
        allowed_values=frozenset({"low", "mixed", "high"}),
        value_aliases=(
            ("one_sided", "low"),
            ("uneven", "mixed"),
            ("balanced", "high"),
            ("mutual", "high"),
        ),
        default_ttl=timedelta(days=30),
    ),
    RelationshipStatePolicy(
        dimension="partner_relationship_status",
        aliases=frozenset(
            {
                "relationship_status",
                "partner_status",
                "romantic_availability",
            }
        ),
        allowed_values=frozenset({"unknown", "single", "partnered", "married"}),
        value_aliases=(
            ("uncertain", "unknown"),
            ("not_sure", "unknown"),
            ("unconfirmed", "unknown"),
            ("available", "single"),
            ("in_relationship", "partnered"),
            ("has_partner", "partnered"),
            ("not_single", "partnered"),
            ("dating", "partnered"),
            ("has_boyfriend", "partnered"),
            ("has_girlfriend", "partnered"),
        ),
        default_ttl=timedelta(days=90),
    ),
)


INTERACTION_METRIC_ALIASES: dict[str, str] = {
    "communication_frequency": "contact_frequency",
    "interaction_frequency": "contact_frequency",
    "meeting_frequency": "contact_frequency",
    "conversation_topics": "topic_scope",
    "conversation_topic_scope": "topic_scope",
    "personal_topic_frequency": "topic_scope",
    "communication_channel": "interaction_channel",
    "conversation_channel": "interaction_channel",
    "initiative_balance": "initiation_balance",
    "contact_initiative": "initiation_balance",
    "contact_initiation": "initiation_balance",
    "conversation_initiative": "initiation_balance",
    "conversation_initiator": "initiation_balance",
    "interaction_initiative": "initiation_balance",
    "interaction_initiator": "initiation_balance",
    "initiation_frequency": "initiation_balance",
    "initiative_pattern": "initiation_balance",
    "who_initiates": "initiation_balance",
    "topic_initiation": "initiation_balance",
    "reply_engagement": "response_engagement",
    "conversation_engagement": "response_engagement",
}

INTERACTION_PATTERN_DIMENSIONS = frozenset(
    {
        "contact_frequency",
        "topic_scope",
        "interaction_channel",
        "initiation_balance",
        "response_engagement",
        "emotional_disclosure",
    }
)

_INITIATION_BALANCE_VALUES = frozenset(
    {"partner_to_user", "balanced", "user_to_partner", "mixed"}
)
_INITIATION_BALANCE_VALUE_ALIASES = {
    "partner_initiated": "partner_to_user",
    "partner_initiates": "partner_to_user",
    "partner_led": "partner_to_user",
    "user_initiated": "user_to_partner",
    "user_initiates": "user_to_partner",
    "user_led": "user_to_partner",
    "mutual": "balanced",
    "reciprocal": "balanced",
    "both": "balanced",
    "alternating": "mixed",
}
_CADENCE_VALUES = frozenset(
    {
        "daily",
        "weekly",
        "monthly",
        "frequent",
        "frequently",
        "often",
        "occasionally",
        "rare",
        "rarely",
    }
)
_RELATIONSHIP_INTERACTION_SUBJECTS = frozenset(
    {
        "relationship",
        "partner",
        "user",
        "both",
        "couple",
        "dyad",
        "partner_and_user",
        "user_and_partner",
        "双方",
        "关系",
        "对方",
        "用户",
        "我",
        "她",
        "他",
        "我们",
        "我和她",
        "我和他",
    }
)


# Model predicates are intentionally normalized through a registry rather than
# interpreted with one-off branches in the extraction pipeline.  Metrics and
# relationship-state dimensions remain the preferred, explicit declarations;
# these aliases cover stable facts and older model output.
DIMENSION_PREDICATE_ALIASES: dict[str, frozenset[str]] = {
    "romantic_interest": frozenset(
        {
            "has_romantic_interest_in",
            "has_crush_on",
            "is_attracted_to",
            "likes",
            "romantic_interest",
        }
    ),
    "social_relation": frozenset(
        {
            "classmate_of",
            "is_classmate_with",
            "is_colleague_with",
            "is_roommate_with",
            "social_relation",
        }
    ),
    "shared_context": frozenset(
        {
            "in_same_group",
            "shares_activity_with",
            "shares_course_group_with",
            "shares_group_with",
            "shared_context",
        }
    ),
    "relationship_familiarity": frozenset(
        {
            "familiarity_level",
            "has_familiarity",
            "relationship_familiarity",
        }
    ),
    "contact_opportunity": frozenset(
        {
            "contact_opportunity",
            "has_contact_opportunity",
            "meeting_opportunity",
        }
    ),
    "contact_frequency": frozenset(
        {
            "communication_frequency",
            "contact_frequency",
            "contact_frequency_changed",
            "interaction_frequency",
        }
    ),
    "topic_scope": frozenset(
        {
            "conversation_topic_scope",
            "discuss_topics",
            "topic_scope",
        }
    ),
    "interaction_channel": frozenset(
        {
            "communicates_via",
            "communication_channel",
            "interaction_channel",
            "primary_communication_channel",
        }
    ),
    "initiation_balance": frozenset(
        {
            "contact_initiation_pattern",
            "initiation_balance",
            "initiates_contact",
            "initiates_conversation",
            "initiates_interaction",
            "repeatedly_initiates_conversation",
        }
    ),
    "response_engagement": frozenset(
        {
            "conversation_engagement",
            "response_engagement",
            "responds_to_conversation",
        }
    ),
    "emotional_disclosure": frozenset(
        {
            "emotional_disclosure",
            "shares_emotions_with",
            "shares_personal_feelings_with",
        }
    ),
    "partner_relationship_status": frozenset(
        {
            "partner_relationship_status",
            "relationship_status_unknown",
            "partner_is_single",
            "partner_has_partner",
            "partner_is_married",
        }
    ),
}


# An atomic fact may still need context.  For example, "online conversation
# became more frequent" has one updatable metric (frequency) and one channel
# qualifier.  These pairs may share one claim without allowing unrelated
# states such as familiarity and contact opportunity to be merged.
ATOMIC_CONTEXT_COMPANIONS: dict[str, frozenset[str]] = {
    "romantic_interest": frozenset({"social_relation", "shared_context"}),
    "relationship_familiarity": frozenset({"social_relation", "shared_context"}),
    "contact_opportunity": frozenset({"social_relation", "shared_context"}),
    "contact_frequency": frozenset(
        {"social_relation", "shared_context", "interaction_channel"}
    ),
    "topic_scope": frozenset(
        {"social_relation", "shared_context", "interaction_channel"}
    ),
    "interaction_channel": frozenset(
        {"social_relation", "shared_context", "contact_frequency", "topic_scope"}
    ),
    "initiation_balance": frozenset(
        {
            "social_relation",
            "shared_context",
            "interaction_channel",
            "contact_frequency",
        }
    ),
    "response_engagement": frozenset(
        {"social_relation", "shared_context", "interaction_channel"}
    ),
    "emotional_disclosure": frozenset(
        {"social_relation", "shared_context", "interaction_channel"}
    ),
    "partner_relationship_status": frozenset({"social_relation"}),
}


EVIDENCE_DIMENSION_POLICIES: tuple[EvidenceDimensionPolicy, ...] = (
    EvidenceDimensionPolicy(
        dimension="romantic_interest",
        patterns=(
            re.compile(r"(?:喜欢|暗恋|心动|有好感).{0,10}(?:她|他|对方|女生|男生|女孩|男孩)"),
            re.compile(r"(?:她|他|对方|女生|男生|女孩|男孩).{0,10}(?:喜欢|暗恋|心动|有好感)"),
        ),
    ),
    EvidenceDimensionPolicy(
        dimension="social_relation",
        patterns=(re.compile(r"同班|同学|同事|校友|室友|邻居"),),
    ),
    EvidenceDimensionPolicy(
        dimension="shared_context",
        patterns=(
            re.compile(r"同(?:一|一个)?.{0,8}(?:小组|课程|社团|项目|班级)"),
            re.compile(r"同组|一起上课|共同(?:课程|项目|活动)"),
        ),
    ),
    EvidenceDimensionPolicy(
        dimension="relationship_familiarity",
        patterns=(
            re.compile(
                r"不.{0,2}熟|不熟悉|很熟|比较熟|熟悉|熟了(?:一|些|一点|一些)?|"
                r"更熟|逐渐熟|慢慢熟|熟络|刚认识|认识不久|陌生|了解不多|关系生疏"
            ),
        ),
    ),
    EvidenceDimensionPolicy(
        dimension="contact_opportunity",
        patterns=(
            re.compile(r"(?:接触|见面|碰面|独处|聊天).{0,5}机会"),
            re.compile(r"机会.{0,10}(?:接触|见面|碰面|独处|聊)"),
        ),
    ),
    EvidenceDimensionPolicy(
        dimension="contact_frequency",
        patterns=(
            re.compile(
                r"(?:每天|每周|经常|偶尔|很少|几乎不)(?!.{0,10}机会).{0,10}"
                r"(?:联系|聊天|交流|见面|碰面)"
            ),
            re.compile(
                r"(?:联系|聊天|交流|见面|碰面)(?!.{0,6}机会)(?:次数|频率)?.{0,4}"
                r"(?:频繁|变多|变少|很少|经常|偶尔)"
            ),
        ),
    ),
    EvidenceDimensionPolicy(
        dimension="topic_scope",
        patterns=(
            re.compile(
                r"课程相关|工作相关|话题.{0,6}(?:范围|内容|局限|集中|主要|大多)"
            ),
            re.compile(r"(?:聊|谈|围绕).{0,10}(?:工作|课程|学习|个人|生活|兴趣)"),
            re.compile(r"只.{0,4}(?:谈|聊).{0,8}(?:工作|课程|学习)"),
        ),
    ),
    EvidenceDimensionPolicy(
        dimension="interaction_channel",
        patterns=(re.compile(r"群聊|私聊|线上|线下|微信|电话|当面"),),
    ),
    EvidenceDimensionPolicy(
        dimension="initiation_balance",
        patterns=(
            re.compile(r"主动.{0,10}(?:联系|找|发起|开话题|聊天)"),
            re.compile(r"总是我.{0,10}(?:联系|找|发起|开口)"),
            re.compile(
                r"(?:我|她|他|对方).{0,10}(?:先|主动).{0,8}"
                r"(?:问候|开口|发消息|联系|聊天|找)"
            ),
        ),
    ),
    EvidenceDimensionPolicy(
        dimension="response_engagement",
        patterns=(re.compile(r"回复|回应|接话|反问|敷衍"),),
    ),
    EvidenceDimensionPolicy(
        dimension="emotional_disclosure",
        patterns=(re.compile(r"倾诉|心事|情绪|难过时.{0,8}(?:找|联系|告诉)"),),
    ),
    EvidenceDimensionPolicy(
        dimension="partner_relationship_status",
        patterns=(
            re.compile(
                r"(?:是否|是不是|不确定|不知道|没确认|没有确认).{0,8}"
                r"(?:单身|有对象|有伴侣|感情状态)"
            ),
            re.compile(
                r"(?:她|他|对方).{0,10}(?:单身|有对象|有男朋友|有女朋友|"
                r"有伴侣|已婚|结婚了)"
            ),
        ),
    ),
)


_STATE_POLICIES_BY_NAME: dict[str, RelationshipStatePolicy] = {}
for _policy in RELATIONSHIP_STATE_POLICIES:
    _STATE_POLICIES_BY_NAME[_policy.dimension] = _policy
    for _alias in _policy.aliases:
        _STATE_POLICIES_BY_NAME[_alias] = _policy

_DIMENSION_BY_PREDICATE = {
    _normalize_identifier(alias): dimension
    for dimension, aliases in DIMENSION_PREDICATE_ALIASES.items()
    for alias in aliases
}


def normalize_state_dimension(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    policy = _STATE_POLICIES_BY_NAME.get(_normalize_identifier(value))
    return policy.dimension if policy is not None else None


def normalize_state_value(dimension: object, value: object) -> str | None:
    canonical_dimension = normalize_state_dimension(dimension)
    if canonical_dimension is None or not isinstance(value, str):
        return None
    policy = _STATE_POLICIES_BY_NAME[canonical_dimension]
    normalized_value = _normalize_identifier(value)
    aliases = dict(policy.value_aliases)
    canonical_value = aliases.get(normalized_value, normalized_value)
    return canonical_value if canonical_value in policy.allowed_values else None


def relationship_state_ttl(dimension: object) -> timedelta | None:
    canonical = normalize_state_dimension(dimension)
    if canonical is None:
        return None
    return _STATE_POLICIES_BY_NAME[canonical].default_ttl


def normalize_interaction_metric(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = _normalize_identifier(value)
    return INTERACTION_METRIC_ALIASES.get(normalized, normalized)


def normalize_interaction_pattern_payload(
    payload: Mapping[str, object],
    evidence_text: str,
    predicate: object = None,
) -> dict[str, object]:
    """Normalize one interaction metric without mixing cadence and direction."""

    normalized = dict(payload)
    metric = normalize_interaction_metric(normalized.get("metric"))
    metric = reconcile_interaction_metric(metric, evidence_text, predicate)
    if metric is None:
        return normalized
    normalized["metric"] = metric
    if metric != "initiation_balance":
        return normalized

    raw_state = normalized.get("current")
    state = next(
        (
            candidate
            for field in ("current", "direction")
            if (
                candidate := normalize_interaction_state_value(
                    metric,
                    normalized.get(field),
                )
            )
            is not None
        ),
        None,
    )
    if state is None:
        state = infer_initiation_balance(evidence_text)
    if state is not None:
        normalized["current"] = state
    elif raw_state is not None:
        normalized.pop("current", None)

    raw_key = _normalize_identifier(str(raw_state or ""))
    if raw_key in _CADENCE_VALUES:
        normalized.setdefault("frequency", raw_key)
    return normalized


def normalize_interaction_state_value(
    metric: object,
    value: object,
) -> str | None:
    canonical_metric = normalize_interaction_metric(metric)
    if value is None:
        return None
    normalized = _normalize_identifier(str(value))
    if canonical_metric != "initiation_balance":
        return normalized or None
    normalized = _INITIATION_BALANCE_VALUE_ALIASES.get(normalized, normalized)
    return normalized if normalized in _INITIATION_BALANCE_VALUES else None


def is_relationship_interaction_subject(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    return _normalize_identifier(value) in _RELATIONSHIP_INTERACTION_SUBJECTS


def interaction_pattern_state(
    metric: object,
    payload: Mapping[str, object],
) -> str | None:
    canonical_metric = normalize_interaction_metric(metric)
    if canonical_metric == "initiation_balance":
        for key in ("current", "direction"):
            value = normalize_interaction_state_value(
                canonical_metric,
                payload.get(key),
            )
            if value is not None:
                return value
        return None
    for key in ("current", "direction", "frequency"):
        value = normalize_interaction_state_value(canonical_metric, payload.get(key))
        if value is not None:
            return value
    return None


def infer_initiation_balance(text: str) -> str | None:
    if re.search(r"双方|两人|彼此|互相", text) and re.search(
        r"轮流|都会|各自|互相|有来有回", text
    ):
        return "balanced"
    partner = _has_positive_initiation_clause(
        text,
        r"(?:她|他|对方)",
        r"(?:我|用户)",
    )
    user = _has_positive_initiation_clause(
        text,
        r"(?:我|用户)",
        r"(?:她|他|对方)",
    )
    if partner and user:
        return "mixed"
    if partner:
        return "partner_to_user"
    if user:
        return "user_to_partner"
    return None


def _has_positive_initiation_clause(
    text: str,
    actor: str,
    recipient: str,
) -> bool:
    for clause in re.split(r"[，,。；;！？!?]", text):
        if not re.search(
            rf"{actor}.{{0,14}}(?:主动|先).{{0,10}}"
            rf"(?:找|联系|发消息|聊|问候|开口).{{0,8}}{recipient}",
            clause,
        ):
            continue
        if re.search(
            rf"{actor}.{{0,8}}(?:很少|几乎不|不再|从来不|从来没有|"
            rf"从不|从未|未曾|没有|并不|不(?:太|怎么)?).{{0,5}}主动",
            clause,
        ):
            continue
        return True
    return False


def detect_evidence_dimensions(text: str) -> frozenset[str]:
    return frozenset(
        policy.dimension
        for policy in EVIDENCE_DIMENSION_POLICIES
        if any(pattern.search(text) for pattern in policy.patterns)
    )


def reconcile_interaction_metric(
    metric: str | None,
    evidence_text: str,
    predicate: object = None,
) -> str | None:
    """Correct a registered metric only when evidence has one clear metric."""

    detected = detect_evidence_dimensions(evidence_text) & INTERACTION_PATTERN_DIMENSIONS
    predicate_dimension = dimension_for_predicate(predicate)
    if (
        predicate_dimension in INTERACTION_PATTERN_DIMENSIONS
        and predicate_dimension in detected
        and predicate_dimension != metric
    ):
        return predicate_dimension
    if (
        metric in INTERACTION_PATTERN_DIMENSIONS
        and len(detected) == 1
        and metric not in detected
    ):
        return next(iter(detected))
    return metric


def declared_claim_dimension(
    *,
    kind: object,
    predicate: object,
    payload: Mapping[str, object],
) -> str | None:
    """Return the single dimension explicitly declared by a model claim."""

    kind_name = _normalize_identifier(kind) if isinstance(kind, str) else ""
    if kind_name == "relationship_state":
        return normalize_state_dimension(payload.get("state_dimension"))
    if kind_name == "interaction_pattern":
        metric = normalize_interaction_metric(payload.get("metric"))
        if metric in _REGISTERED_EVIDENCE_DIMENSIONS:
            return metric
    return dimension_for_predicate(predicate)


def dimension_for_predicate(predicate: object) -> str | None:
    if not isinstance(predicate, str):
        return None
    return _DIMENSION_BY_PREDICATE.get(_normalize_identifier(predicate))


def covered_claim_dimensions(
    *,
    kind: object,
    predicate: object,
    payload: Mapping[str, object],
    evidence_text: str,
) -> frozenset[str]:
    """Return independently represented dimensions, excluding qualifiers."""

    declared = declared_claim_dimension(
        kind=kind,
        predicate=predicate,
        payload=payload,
    )
    if declared is not None:
        return frozenset({declared})
    detected = detect_evidence_dimensions(evidence_text)
    return detected if len(detected) == 1 else frozenset()


def conflicting_atomic_dimensions(
    primary_dimension: str | None,
    detected_dimensions: frozenset[str],
) -> frozenset[str]:
    """Find dimensions that make one claim independently updatable in two ways."""

    if len(detected_dimensions) <= 1:
        return frozenset()
    if primary_dimension is None or primary_dimension not in detected_dimensions:
        return detected_dimensions
    allowed = {primary_dimension}
    allowed.update(ATOMIC_CONTEXT_COMPANIONS.get(primary_dimension, ()))
    incompatible = detected_dimensions - allowed
    if not incompatible:
        return frozenset()
    return frozenset({primary_dimension, *incompatible})


_REGISTERED_EVIDENCE_DIMENSIONS = frozenset(
    policy.dimension for policy in EVIDENCE_DIMENSION_POLICIES
)

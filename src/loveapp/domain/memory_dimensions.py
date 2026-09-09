import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta


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
    "conflict_trend": "conflict_frequency",
    "argument_frequency": "conflict_frequency",
    "conflict_recurrence": "conflict_frequency",
}

INTERACTION_PATTERN_DIMENSIONS = frozenset(
    {
        "contact_frequency",
        "topic_scope",
        "interaction_channel",
        "initiation_balance",
        "response_engagement",
        "emotional_disclosure",
        "conflict_frequency",
    }
)

# Interaction payloads intentionally remain dictionaries at the persistence
# boundary for backwards compatibility.  These bounded vocabularies and
# helpers provide a small, deterministic contract without introducing a
# second Memory model or changing the Store schema.
# ``model_inferred`` is the historical persisted spelling.  New Pattern
# producers may use the more precise ``derived_from_events`` provenance while
# old Event/Pattern rows continue to round-trip unchanged.
INTERACTION_MEMORY_SOURCES = frozenset(
    {"user_reported", "model_inferred", "derived_from_events"}
)
INTERACTION_PATTERN_SOURCES = frozenset(
    {"user_reported", "model_inferred", "derived_from_events"}
)
INTERACTION_EVENT_TYPES = frozenset(
    {
        "conversation",
        "shared_activity",
        "date",
        "conflict",
        "reconciliation",
        "affection_expression",
        "support",
        "milestone",
    }
)
INTERACTION_EVENT_PAYLOAD_FIELDS = frozenset(
    {
        "event_type",
        "participants",
        "action",
        "activity_type",
        "time",
        "location",
        "cause",
        "severity",
        "resolution",
        "emotion",
        "outcome",
        "source",
        "salience",
        "novelty",
        "relationship_impact",
    }
)
INTERACTION_PATTERN_PAYLOAD_FIELDS = frozenset(
    {
        "source",
        "evidence",
        "evidence_ids",
        "positive_evidence_ids",
        "negative_evidence_ids",
        "time_window",
        "state",
    }
)

_INTERACTION_SOURCE_ALIASES = {
    "reported": "user_reported",
    "user": "user_reported",
    "user_reported": "user_reported",
    "explicit": "user_reported",
    "inferred": "model_inferred",
    "model": "model_inferred",
    "model_inferred": "model_inferred",
    "system_inferred": "model_inferred",
    "derived": "derived_from_events",
    "event_derived": "derived_from_events",
    "derived_from_events": "derived_from_events",
    "event_inferred": "derived_from_events",
}
_EVENT_PARTICIPANT_ALIASES = {
    "i": "user",
    "me": "user",
    "myself": "user",
    "user": "user",
    "我": "user",
    "用户": "user",
    "she": "partner",
    "he": "partner",
    "her": "partner",
    "him": "partner",
    "partner": "partner",
    "other": "partner",
    "对方": "partner",
    "她": "partner",
    "他": "partner",
}
_EVENT_BOTH_PARTICIPANT_ALIASES = {
    "we",
    "both",
    "couple",
    "dyad",
    "relationship",
    "user_and_partner",
    "partner_and_user",
    "我们",
    "双方",
    "两人",
    "彼此",
}
_EVENT_ACTION_ALIASES = ("action", "activity_type", "activity", "event_action", "verb")
_EVENT_TYPE_ALIASES = ("event_type", "event_category", "domain_event_type")
_EVENT_TIME_ALIASES = ("time", "event_time", "time_expression", "temporal_expression")
_EVENT_LOCATION_ALIASES = ("location", "place", "venue", "where")
_EVENT_CAUSE_ALIASES = ("cause", "conflict_cause")
_EVENT_EMOTION_ALIASES = ("emotion", "feeling")
_EVENT_OUTCOME_ALIASES = ("outcome", "result", "effect")
_PATTERN_SOURCE_ALIASES = ("source", "provenance", "origin")
_PATTERN_EVIDENCE_ID_ALIASES = (
    "evidence_ids",
    "event_ids",
    "source_event_ids",
    "supporting_event_ids",
)
_PATTERN_TIME_WINDOW_ALIASES = ("time_window", "time_range", "window")

_EVENT_TYPE_VALUE_ALIASES = {
    "chat": "conversation",
    "communication": "conversation",
    "talk": "conversation",
    "conversation": "conversation",
    "activity": "shared_activity",
    "meal": "shared_activity",
    "shared_activity": "shared_activity",
    "dating": "date",
    "romantic_date": "date",
    "date": "date",
    "argument": "conflict",
    "fight": "conflict",
    "quarrel": "conflict",
    "conflict": "conflict",
    "repair": "reconciliation",
    "reconcile": "reconciliation",
    "reconciliation": "reconciliation",
    "gift": "affection_expression",
    "affection": "affection_expression",
    "affection_expression": "affection_expression",
    "comfort": "support",
    "emotional_support": "support",
    "support": "support",
    "first": "milestone",
    "first_date": "milestone",
    "relationship_milestone": "milestone",
    "milestone": "milestone",
}
_EVENT_TYPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "milestone",
        re.compile(r"(?:第一次|初次|头一次|首次)|\bfirst\s+(?:time|date|meeting)\b", re.I),
    ),
    (
        "reconciliation",
        re.compile(
            r"(?:和好|说开|和解|修复|恢复正常|道歉后.{0,8}原谅)"
            r"|\b(?:reconcil|made\s+up|repair)\w*\b",
            re.I,
        ),
    ),
    (
        "conflict",
        re.compile(
            r"(?:吵架|争吵|争执|矛盾|冲突|冷战)"
            r"|\b(?:argu|fight|quarrel|conflict)\w*\b",
            re.I,
        ),
    ),
    (
        "support",
        re.compile(
            r"(?:安慰|支持|鼓励|陪伴|帮我扛|倾听)"
            r"|\b(?:comfort|support|encourag)\w*\b",
            re.I,
        ),
    ),
    (
        "affection_expression",
        re.compile(
            r"(?:表白|告白|示爱|说喜欢|送.{0,8}(?:礼物|花))"
            r"|\b(?:confess|gift|affection)\w*\b",
            re.I,
        ),
    ),
    ("date", re.compile(r"(?:约会|正式约.{0,8}(?:吃饭|见面))|\bdate\b", re.I)),
    (
        "shared_activity",
        re.compile(
            r"(?:一起|共同).{0,12}(?:吃饭|看电影|散步|旅行|活动|逛|玩|见面)"
            r"|\b(?:together|shared)\b",
            re.I,
        ),
    ),
    (
        "conversation",
        re.compile(
            r"(?:聊天|谈话|通话|沟通|聊了|发消息)"
            r"|\b(?:chat|talk|conversation|call)\w*\b",
            re.I,
        ),
    ),
)
_CONFLICT_CAUSE_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "communication_frequency",
        re.compile(r"(?:联系|回复|回消息|沟通|聊天).{0,10}(?:少|慢|不够|频率)|(?:少|慢|不够).{0,8}(?:联系|回复|沟通|聊天)"),
    ),
    ("financial_values", re.compile(r"(?:钱|花钱|消费|支出|预算|经济|消费观)")),
    ("misunderstanding", re.compile(r"(?:误会|误解|没听懂|理解错)")),
    ("trust", re.compile(r"(?:信任|怀疑|隐瞒|欺骗|撒谎)")),
    ("boundary", re.compile(r"(?:边界|界限|底线|隐私)")),
    ("availability", re.compile(r"(?:工作忙|没时间|时间安排|行程|排期)")),
)
_CONFLICT_CAUSE_CATEGORY_ALIASES = {
    "contact_frequency": "communication_frequency",
    "reply_frequency": "communication_frequency",
    "communication": "communication_frequency",
    "insufficient_contact": "communication_frequency",
    "money": "financial_values",
    "spending": "financial_values",
    "consumption_values": "financial_values",
    "miscommunication": "misunderstanding",
    "boundaries": "boundary",
    "schedule": "availability",
}

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
    "conflict_frequency": frozenset(
        {
            "argument_frequency",
            "conflict_frequency",
            "conflict_recurrence",
            "conflict_trend",
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
    "conflict_frequency": frozenset(
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
        dimension="conflict_frequency",
        patterns=(
            re.compile(
                r"(?:经常|反复|频繁|多次|越来越多|每(?:天|周|月)).{0,12}"
                r"(?:吵架|争吵|争执|矛盾|冲突)"
            ),
            re.compile(
                r"(?:吵架|争吵|争执|矛盾|冲突).{0,10}"
                r"(?:变多|增加|减少|变少|频繁|次数)"
            ),
        ),
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


def normalize_interaction_event_payload(
    payload: Mapping[str, object],
    *,
    perspective: object = None,
    occurred_at: datetime | None = None,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    emotions: list[str] | None = None,
    evidence_text: str = "",
) -> dict[str, object]:
    """Normalize the bounded fields of an ``interaction_event`` payload.

    Event payloads historically were free-form dictionaries.  This helper
    deliberately adds only canonical aliases and light type-preserving
    normalization; it does not discard legacy keys or infer a new event.
    Shape enforcement is kept in :func:`validate_interaction_event_payload` so
    callers can choose the appropriate contract boundary.
    """

    normalized = dict(payload)
    _copy_payload_alias(normalized, "event_type", _EVENT_TYPE_ALIASES)
    _copy_payload_alias(normalized, "action", _EVENT_ACTION_ALIASES)
    _copy_payload_alias(normalized, "time", _EVENT_TIME_ALIASES)
    _copy_payload_alias(normalized, "location", _EVENT_LOCATION_ALIASES)
    _copy_payload_alias(normalized, "cause", _EVENT_CAUSE_ALIASES)
    _copy_payload_alias(normalized, "emotion", _EVENT_EMOTION_ALIASES)
    _copy_payload_alias(normalized, "outcome", _EVENT_OUTCOME_ALIASES)

    event_type = normalize_interaction_event_type(normalized.get("event_type"))
    if event_type is None:
        event_type = infer_interaction_event_type(normalized, evidence_text=evidence_text)
    if event_type is not None:
        normalized["event_type"] = event_type

    if "cause" in normalized:
        normalized["cause"] = normalize_interaction_event_cause(normalized["cause"])

    if "participants" not in normalized:
        for alias in ("actors", "involved_participants", "involved_people"):
            if alias in normalized:
                normalized["participants"] = normalized[alias]
                break
    if "participants" in normalized:
        normalized["participants"] = _normalize_event_participants(
            normalized["participants"]
        )

    if "action" in normalized:
        if isinstance(normalized["action"], str) and normalized["action"].strip():
            normalized["action"] = normalized["action"].strip()
            normalized.setdefault("activity_type", normalized["action"])
    else:
        action = _first_nonempty_text(normalized, "activity_type")
        if action is not None:
            normalized["action"] = action
            # ``activity_type`` is the pre-V2 key used by plans and relationship
            # evidence.  Keep it synchronized when it was omitted, but preserve a
            # deliberately more specific value supplied by older callers.
            normalized.setdefault("activity_type", action)

    if "time" not in normalized:
        temporal_expression = normalized.get("temporal_expression")
        if isinstance(temporal_expression, str) and temporal_expression.strip():
            normalized["time"] = temporal_expression.strip()
        elif occurred_at is not None:
            normalized["time"] = occurred_at.isoformat()
        elif period_start is not None or period_end is not None:
            normalized["time"] = _format_temporal_range(period_start, period_end)
    elif isinstance(normalized["time"], datetime):
        normalized["time"] = normalized["time"].isoformat()
    elif isinstance(normalized["time"], str):
        normalized["time"] = normalized["time"].strip()

    for field in ("location", "outcome", "resolution"):
        value = normalized.get(field)
        if isinstance(value, str):
            normalized[field] = value.strip()
    if isinstance(normalized.get("emotion"), str):
        normalized["emotion"] = normalized["emotion"].strip()
    elif (
        "emotion" not in normalized
        and emotions
        and len(emotions) == 1
        and isinstance(emotions[0], str)
        and emotions[0].strip()
    ):
        # Keep the complete top-level ``emotions`` list on the candidate while
        # exposing the singular schema field when there is exactly one item.
        normalized["emotion"] = emotions[0].strip()

    if "source" in normalized:
        source = normalized["source"]
        if isinstance(source, str) and source.strip():
            normalized["source"] = normalize_interaction_source(source) or source.strip()
    else:
        source_alias_found = False
        for alias in ("provenance", "origin"):
            if alias not in normalized:
                continue
            source_alias_found = True
            source = normalized[alias]
            if isinstance(source, str) and source.strip():
                normalized["source"] = normalize_interaction_source(source) or source.strip()
            else:
                normalized["source"] = source
            break
        if not source_alias_found and perspective is not None:
            normalized["source"] = interaction_source_for_perspective(perspective)

    return normalized


def validate_interaction_event_payload(
    payload: Mapping[str, object],
    *,
    perspective: object = None,
) -> None:
    """Validate present Event schema fields without rejecting legacy omissions."""

    if not isinstance(payload, Mapping):
        raise ValueError("interaction_event payload must be an object")

    event_type = payload.get("event_type")
    if event_type is not None and normalize_interaction_event_type(event_type) is None:
        raise ValueError("interaction_event event_type is not in the reviewed ontology")

    participants = payload.get("participants")
    if participants is not None:
        if not isinstance(participants, (list, tuple)):
            raise ValueError("interaction_event participants must be a list")
        if len(participants) > 20:
            raise ValueError("interaction_event participants cannot exceed 20 items")
        if any(not isinstance(item, str) or not item.strip() for item in participants):
            raise ValueError("interaction_event participants must contain non-empty strings")

    for field, max_length in (
        ("action", 120),
        ("activity_type", 120),
        ("time", 240),
        ("location", 240),
        ("outcome", 500),
        ("resolution", 240),
    ):
        value = payload.get(field)
        if value is not None and (
            not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length
        ):
            raise ValueError(f"interaction_event {field} must be a non-empty string")

    emotion = payload.get("emotion")
    if emotion is not None:
        if isinstance(emotion, str):
            if not emotion.strip() or len(emotion.strip()) > 120:
                raise ValueError("interaction_event emotion must be a non-empty string")
        elif isinstance(emotion, (list, tuple)):
            if len(emotion) > 8 or any(
                not isinstance(item, str) or not item.strip() for item in emotion
            ):
                raise ValueError("interaction_event emotion list is invalid")
        else:
            raise ValueError("interaction_event emotion must be a string or list")

    cause = payload.get("cause")
    if cause is not None:
        if not isinstance(cause, Mapping):
            raise ValueError("interaction_event cause must be an object")
        if not cause or set(cause) - {"category", "description"}:
            raise ValueError("interaction_event cause contains unknown or empty fields")
        category = cause.get("category")
        description = cause.get("description")
        if category is not None and (
            not isinstance(category, str)
            or not category.strip()
            or len(category.strip()) > 80
            or re.fullmatch(r"[a-z][a-z0-9_]*", category.strip()) is None
        ):
            raise ValueError("interaction_event cause category must be snake_case text")
        if description is not None and (
            not isinstance(description, str)
            or not description.strip()
            or len(description.strip()) > 500
        ):
            raise ValueError("interaction_event cause description must be non-empty text")

    severity = payload.get("severity")
    if severity is not None:
        valid_numeric = (
            isinstance(severity, int)
            and not isinstance(severity, bool)
            and 1 <= severity <= 5
        )
        valid_text = isinstance(severity, str) and severity.casefold().strip() in {
            "low",
            "moderate",
            "high",
            "severe",
        }
        if not valid_numeric and not valid_text:
            raise ValueError("interaction_event severity must be 1..5 or a bounded label")

    for field in ("salience", "novelty"):
        value = payload.get(field)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 <= float(value) <= 1
        ):
            raise ValueError(f"interaction_event {field} must be between 0 and 1")

    impact = payload.get("relationship_impact")
    if impact is not None and (
        not isinstance(impact, str)
        or impact.casefold().strip()
        not in {"improving", "damaging", "unchanged", "unclear"}
    ):
        raise ValueError("interaction_event relationship_impact is invalid")

    source = payload.get("source")
    normalized_source = (
        normalize_interaction_source(source) if source is not None else None
    )
    if source is not None and normalized_source is None:
        raise ValueError(
            "interaction_event source must be user_reported or model_inferred"
        )
    if normalized_source == "derived_from_events":
        raise ValueError(
            "interaction_event source cannot be derived_from_events"
        )
    _validate_interaction_source_alignment(source, perspective, kind="event")


def normalize_interaction_event_type(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = _normalize_identifier(value)
    normalized = _EVENT_TYPE_VALUE_ALIASES.get(normalized, normalized)
    return normalized if normalized in INTERACTION_EVENT_TYPES else None


def infer_interaction_event_type(
    payload: Mapping[str, object],
    *,
    evidence_text: str = "",
) -> str | None:
    values = [
        evidence_text,
        payload.get("action"),
        payload.get("activity_type"),
        payload.get("predicate"),
        payload.get("outcome"),
        payload.get("resolution"),
    ]
    text = " ".join(
        str(value) for value in values if isinstance(value, str) and value.strip()
    )
    return next(
        (
            event_type
            for event_type, pattern in _EVENT_TYPE_PATTERNS
            if pattern.search(text) is not None
        ),
        None,
    )


def is_conflict_interaction_event(
    payload: Mapping[str, object],
    *,
    evidence_text: str = "",
) -> bool:
    return (
        normalize_interaction_event_type(payload.get("event_type")) == "conflict"
        or infer_interaction_event_type(payload, evidence_text=evidence_text) == "conflict"
    )


def normalize_interaction_event_cause(value: object) -> object:
    if isinstance(value, str):
        description = value.strip()
        if not description:
            return value
        category = infer_conflict_cause_category(description)
        return {
            **({"category": category} if category is not None else {}),
            "description": description,
        }
    if not isinstance(value, Mapping):
        return value
    normalized = dict(value)
    category = normalized.get("category")
    description = normalized.get("description")
    if isinstance(category, str) and category.strip():
        category_key = _normalize_identifier(category)
        normalized["category"] = _CONFLICT_CAUSE_CATEGORY_ALIASES.get(
            category_key,
            category_key,
        )
    if isinstance(description, str):
        normalized["description"] = description.strip()
    if normalized.get("category") is None and isinstance(description, str):
        inferred = infer_conflict_cause_category(description)
        if inferred is not None:
            normalized["category"] = inferred
    return normalized


def infer_conflict_cause_category(text: str) -> str | None:
    return next(
        (
            category
            for category, pattern in _CONFLICT_CAUSE_CATEGORY_PATTERNS
            if pattern.search(text) is not None
        ),
        None,
    )


def normalize_interaction_pattern_provenance(
    payload: Mapping[str, object],
    *,
    perspective: object = None,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    temporal_expression: object = None,
) -> dict[str, object]:
    """Normalize Pattern provenance, Event evidence IDs, and time window."""

    normalized = dict(payload)
    if "source" in normalized:
        source = normalized["source"]
        if isinstance(source, str) and source.strip():
            normalized["source"] = normalize_interaction_source(source) or source.strip()
    else:
        source_alias_found = False
        for alias in ("provenance", "origin"):
            if alias not in normalized:
                continue
            source_alias_found = True
            source = normalized[alias]
            if isinstance(source, str) and source.strip():
                normalized["source"] = normalize_interaction_source(source) or source.strip()
            else:
                normalized["source"] = source
            break
        if not source_alias_found and perspective is not None:
            normalized["source"] = interaction_source_for_perspective(perspective)

    evidence_value: object = None
    evidence_is_explicit_id_field = False
    for alias in _PATTERN_EVIDENCE_ID_ALIASES:
        if alias in normalized:
            evidence_value = normalized[alias]
            evidence_is_explicit_id_field = True
            break
    if evidence_value is None and "evidence" in normalized:
        evidence_value = normalized["evidence"]
    if evidence_value is not None:
        evidence_ids = (
            _normalize_identifier_list(evidence_value)
            if evidence_is_explicit_id_field
            else _normalize_pattern_evidence_ids(evidence_value)
        )
        if evidence_ids is not None:
            normalized["evidence_ids"] = evidence_ids
            # ``evidence`` is the name used by the redesign document and older
            # callers.  Preserve it while exposing the explicit ID field.
            if "evidence" in normalized:
                normalized["evidence"] = evidence_ids

    if "time_window" not in normalized:
        for alias in _PATTERN_TIME_WINDOW_ALIASES[1:]:
            if alias in normalized:
                normalized["time_window"] = normalized[alias]
                break
    if "time_window" not in normalized:
        if period_start is not None or period_end is not None:
            normalized["time_window"] = _temporal_window_dict(period_start, period_end)
        elif isinstance(temporal_expression, str) and temporal_expression.strip():
            normalized["time_window"] = {"label": temporal_expression.strip()}
        elif isinstance(normalized.get("temporal_expression"), str) and normalized[
            "temporal_expression"
        ].strip():
            normalized["time_window"] = {
                "label": normalized["temporal_expression"].strip()
            }
    else:
        time_window = _normalize_time_window(normalized["time_window"])
        if time_window is None:
            # Model providers commonly serialize omitted optional fields as
            # JSON null. Treat that as absence without manufacturing temporal
            # evidence; malformed non-null values still fail validation.
            normalized.pop("time_window", None)
        else:
            normalized["time_window"] = time_window
    return normalized


def merge_interaction_pattern_provenance(
    existing: Mapping[str, object],
    incoming: Mapping[str, object],
) -> dict[str, object]:
    """Preserve linked Event evidence when an equivalent Pattern is merged."""

    merged = dict(existing)
    for field in ("evidence_ids", "positive_evidence_ids", "negative_evidence_ids"):
        existing_ids = existing.get(field)
        incoming_ids = incoming.get(field)
        if not isinstance(incoming_ids, (list, tuple)):
            continue
        retained_ids = list(existing_ids) if isinstance(existing_ids, (list, tuple)) else []
        evidence_ids = list(
            dict.fromkeys(
                str(value).strip()
                for value in [*retained_ids, *incoming_ids]
                if isinstance(value, str) and value.strip()
            )
        )[-20:]
        merged[field] = evidence_ids
        if field == "evidence_ids" and ("evidence" in existing or "evidence" in incoming):
            merged["evidence"] = evidence_ids
    if incoming.get("time_window") is not None:
        # A SAME merge is a fresh observation of the same current Pattern.
        # Keep its latest declared observation window while retaining all
        # linked evidence IDs above.
        merged["time_window"] = incoming["time_window"]
    state = incoming.get("state")
    if isinstance(state, str) and state.casefold().strip() in {
        "active",
        "weakening",
        "superseded",
    }:
        merged["state"] = state.casefold().strip()
    for field in (
        "pattern_evolution_update",
        "pattern_evolution_rule",
        "last_positive_evidence_id",
        "last_negative_evidence_id",
    ):
        if incoming.get(field) is not None:
            merged[field] = incoming[field]
    return merged


def validate_interaction_pattern_payload(
    payload: Mapping[str, object],
    *,
    perspective: object = None,
) -> None:
    """Validate optional Pattern provenance fields at the contract boundary."""

    if not isinstance(payload, Mapping):
        raise ValueError("interaction_pattern payload must be an object")
    source = payload.get("source")
    normalized_source = normalize_interaction_source(source) if source is not None else None
    if source is not None and normalized_source is None:
        raise ValueError(
            "interaction_pattern source must be user_reported, model_inferred, "
            "or derived_from_events"
        )
    _validate_interaction_source_alignment(source, perspective, kind="pattern")
    if (
        normalized_source == "derived_from_events"
        and perspective is not None
        and _normalize_identifier(str(perspective)) != "model_inferred"
    ):
        raise ValueError(
            "interaction_pattern derived_from_events source requires model_inferred perspective"
        )

    evidence_ids = payload.get("evidence_ids")
    evidence = payload.get("evidence")
    if evidence_ids is not None:
        _validate_identifier_list(evidence_ids, field="evidence_ids")
    if evidence is not None:
        _validate_identifier_list(evidence, field="evidence")
    if (
        evidence_ids is not None
        and evidence is not None
        and list(evidence_ids) != list(evidence)
    ):
        raise ValueError("interaction_pattern evidence and evidence_ids differ")
    # Inferred patterns must cite stable event identifiers.  Free-form
    # evidence text is useful for user-reported claims, but it cannot prove a
    # link to stored Event rows and therefore must not authorize inference.
    if normalized_source in {"model_inferred", "derived_from_events"} and not evidence_ids:
        raise ValueError(
            "inferred interaction_pattern requires evidence IDs"
        )

    for field in ("positive_evidence_ids", "negative_evidence_ids"):
        value = payload.get(field)
        if value is not None:
            _validate_identifier_list(value, field=field)
    positive_ids = set(payload.get("positive_evidence_ids") or [])
    negative_ids = set(payload.get("negative_evidence_ids") or [])
    if positive_ids & negative_ids:
        raise ValueError("interaction_pattern positive and negative evidence overlap")

    state = payload.get("state")
    if state is not None and (
        not isinstance(state, str)
        or state.casefold().strip() not in {"active", "weakening", "superseded"}
    ):
        raise ValueError("interaction_pattern state must be active, weakening, or superseded")

    if "time_window" in payload:
        _validate_time_window(payload["time_window"])


def normalize_interaction_source(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return _INTERACTION_SOURCE_ALIASES.get(_normalize_identifier(value))


def interaction_source_for_perspective(value: object) -> str:
    return (
        "model_inferred"
        if _normalize_identifier(str(value)) == "model_inferred"
        else "user_reported"
    )


def _copy_payload_alias(
    payload: dict[str, object],
    canonical: str,
    aliases: tuple[str, ...],
) -> None:
    if canonical in payload:
        return
    for alias in aliases:
        if alias in payload:
            payload[canonical] = payload[alias]
            return


def _first_nonempty_text(payload: Mapping[str, object], *fields: str) -> str | None:
    for field in fields:
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _normalize_event_participants(value: object) -> object:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return value
        direct = _normalize_event_participant(text)
        if direct is not None:
            return direct
        parts = [part.strip() for part in re.split(r"[,，、/和与及]+", text) if part.strip()]
        return _dedupe_participants(parts) if parts else value
    if isinstance(value, (list, tuple)):
        if not all(isinstance(item, str) for item in value):
            return value
        return _dedupe_participants(list(value))
    return value


def _normalize_event_participant(value: str) -> list[str] | None:
    key = _normalize_identifier(value)
    if key in _EVENT_BOTH_PARTICIPANT_ALIASES:
        return ["user", "partner"]
    alias = _EVENT_PARTICIPANT_ALIASES.get(key)
    if alias is not None:
        return [alias]
    return None


def _dedupe_participants(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized:
            continue
        mapped = _normalize_event_participant(normalized)
        for item in (mapped or [normalized]):
            if item not in result:
                result.append(item)
    return result


def _normalize_identifier_list(value: object) -> object:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        if not all(isinstance(item, str) for item in value):
            return value
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))
    return value


def _normalize_pattern_evidence_ids(value: object) -> list[str] | None:
    if isinstance(value, str):
        text = value.strip()
        return [text] if _looks_like_evidence_id(text) else None
    if isinstance(value, (list, tuple)):
        if not all(isinstance(item, str) for item in value):
            return None
        values = [item.strip() for item in value if item.strip()]
        if values and all(_looks_like_evidence_id(item) for item in values):
            return list(dict.fromkeys(values))
    return None


def _looks_like_evidence_id(value: str) -> bool:
    return bool(
        value
        and value.isascii()
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}", value) is not None
    )


def _validate_interaction_source_alignment(
    source: object,
    perspective: object,
    *,
    kind: str,
) -> None:
    """Reject contradictory provenance declarations at the contract boundary.

    ``perspective`` is the authoritative claim-level provenance.  Keeping a
    conflicting payload source would let an inferred Pattern masquerade as a
    user report and bypass its Event-evidence requirement.
    """

    if source is None or perspective is None:
        return
    normalized_source = normalize_interaction_source(source)
    expected_source = interaction_source_for_perspective(perspective)
    compatible_inferred_sources = {"model_inferred", "derived_from_events"}
    aligned = normalized_source == expected_source or (
        expected_source == "model_inferred"
        and normalized_source in compatible_inferred_sources
    )
    if normalized_source is not None and not aligned:
        raise ValueError(
            f"interaction_{kind} source conflicts with claim perspective"
        )


def _validate_identifier_list(value: object, *, field: str) -> None:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"interaction_pattern {field} must be a list")
    if len(value) > 20 or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"interaction_pattern {field} contains invalid IDs")


def _format_temporal_range(
    period_start: datetime | None,
    period_end: datetime | None,
) -> str:
    start = period_start.isoformat() if period_start is not None else ""
    end = period_end.isoformat() if period_end is not None else ""
    return f"{start}/{end}".strip("/")


def _temporal_window_dict(
    period_start: datetime | None,
    period_end: datetime | None,
) -> dict[str, str]:
    value: dict[str, str] = {}
    if period_start is not None:
        value["start"] = period_start.isoformat()
    if period_end is not None:
        value["end"] = period_end.isoformat()
    return value


def _normalize_time_window(value: object) -> object:
    if isinstance(value, str):
        text = value.strip()
        if "/" in text:
            start_text, end_text = (part.strip() for part in text.split("/", 1))
            if _parse_datetime_text(start_text) is not None and _parse_datetime_text(
                end_text
            ) is not None:
                return {"start": start_text, "end": end_text}
        return {"label": text}
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return {"start": _normalize_time_value(value[0]), "end": _normalize_time_value(value[1])}
    if isinstance(value, Mapping):
        normalized = dict(value)
        aliases = {"from": "start", "to": "end"}
        for alias, canonical in aliases.items():
            if canonical not in normalized and alias in normalized:
                normalized[canonical] = normalized[alias]
            normalized.pop(alias, None)
        for field in ("start", "end", "label", "precision"):
            if field in normalized:
                normalized[field] = _normalize_time_value(normalized[field])
        return normalized
    return value


def _normalize_time_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value.strip()
    return value


def _validate_time_window(value: object) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("interaction_pattern time_window must be an object")
    allowed = {"start", "end", "label", "precision"}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("interaction_pattern time_window contains unknown fields")
    if not any(value.get(field) for field in allowed):
        raise ValueError("interaction_pattern time_window cannot be empty")
    for field in ("start", "end", "label", "precision"):
        item = value.get(field)
        if item is not None and (not isinstance(item, str) or not item.strip()):
            raise ValueError(f"interaction_pattern time_window {field} must be text")
    start = _parse_datetime_text(value.get("start"))
    end = _parse_datetime_text(value.get("end"))
    if start is not None and end is not None:
        try:
            reversed_range = start > end
        except TypeError:
            # A naive and an aware timestamp cannot be ordered safely without
            # an explicit timezone policy; fail closed rather than guessing.
            raise ValueError(
                "interaction_pattern time_window timestamps must share timezone shape"
            ) from None
        if reversed_range:
            raise ValueError("interaction_pattern time_window start cannot be later than end")


def _parse_datetime_text(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


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
    # A canonical interaction declaration is allowed to preserve an omitted
    # state value.  Do not manufacture a current state from evidence on that
    # surface; aliases and model hints still use the bounded inference path.
    canonical_surface = _is_canonical_interaction_surface(predicate, metric)
    if state is None and not canonical_surface:
        state = infer_initiation_balance(evidence_text)
    if state is not None:
        normalized["current"] = state
    elif raw_state is not None:
        normalized.pop("current", None)

    raw_key = _normalize_identifier(str(raw_state or ""))
    if raw_key in _CADENCE_VALUES:
        normalized.setdefault("frequency", raw_key)
    return normalized


def _is_canonical_interaction_surface(predicate: object, metric: str) -> bool:
    if not isinstance(predicate, str):
        return False
    return _normalize_identifier(predicate) == f"interaction.{metric}"


def normalize_interaction_state_value(
    metric: object,
    value: object,
) -> str | None:
    canonical_metric = normalize_interaction_metric(metric)
    if value is None:
        return None
    normalized = _normalize_identifier(str(value))
    if canonical_metric == "response_engagement":
        normalized = {
            "resumed": "responsive",
            "recovered": "responsive",
            "back_to_normal": "responsive",
            "normal_chatting": "responsive",
        }.get(normalized, normalized)
        if normalized.startswith("none_for_"):
            normalized = f"no_response_{normalized.removeprefix('none_for_')}"
        duration_match = re.fullmatch(
            r"(no_(?:response|reply))_(one|two|three|four|five|six|seven)_days",
            normalized,
        )
        if duration_match is not None:
            count = {
                "one": "1",
                "two": "2",
                "three": "3",
                "four": "4",
                "five": "5",
                "six": "6",
                "seven": "7",
            }[duration_match.group(2)]
            normalized = f"{duration_match.group(1)}_{count}_days"
    elif canonical_metric == "contact_frequency":
        normalized = {
            "resumed": "restored",
            "recovered": "restored",
            "back_to_normal": "restored",
        }.get(normalized, normalized)
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
        for key in ("current", "direction", "state_value"):
            value = normalize_interaction_state_value(
                canonical_metric,
                payload.get(key),
            )
            if value is not None:
                return value
        return None
    for key in ("current", "direction", "frequency", "state_value"):
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

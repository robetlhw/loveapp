import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from loveapp.application.contextual_memory_updates import (
    may_contain_contextual_memory_update,
    resolve_contextual_memory_update,
)
from loveapp.application.relationship_events import resolve_contextual_relationship_event
from loveapp.domain.memory import MemoryGateDecision, MemoryGateReason, MemoryItem, StoredMessage
from loveapp.domain.relationship_plan import has_retrospective_event_semantics


@dataclass(frozen=True)
class _GateMatch:
    rule: str
    span: str


class MemoryGate:
    def evaluate(
        self,
        text: str,
        *,
        conversation_history: Iterable[StoredMessage] = (),
        existing_memories: Iterable[MemoryItem] = (),
    ) -> MemoryGateDecision:
        normalized = _normalize(text)
        compact = _compact(normalized)
        if compact in _CASUAL_MESSAGES or any(
            pattern.fullmatch(normalized) for pattern in _CASUAL_PATTERNS
        ):
            return _skip(
                MemoryGateReason.CASUAL,
                "exact_casual",
                matched_rule="casual_exact",
                matched_span=normalized,
            )
        hypothetical = _first_match(normalized, _HYPOTHETICAL_PATTERNS, "hypothetical")
        if hypothetical is not None:
            return _skip(
                MemoryGateReason.HYPOTHETICAL,
                "hypothetical framing",
                matched_rule=hypothetical.rule,
                matched_span=hypothetical.span,
            )
        operation = _first_match(normalized, _OPERATION_PATTERNS, "operation")
        if operation is not None:
            return _skip(
                MemoryGateReason.OPERATION,
                "agent operation",
                matched_rule=operation.rule,
                matched_span=operation.span,
            )
        knowledge_question = _first_match(
            normalized,
            _KNOWLEDGE_QUESTION_PATTERNS,
            "knowledge_question",
        )
        if knowledge_question is not None:
            return _skip(
                MemoryGateReason.KNOWLEDGE_QUESTION,
                "generic knowledge",
                matched_rule=knowledge_question.rule,
                matched_span=knowledge_question.span,
            )
        explicit_remember = _first_match(
            normalized,
            _EXPLICIT_REMEMBER_PATTERNS,
            "explicit_remember",
        )
        if explicit_remember is not None:
            return MemoryGateDecision(
                should_extract=True,
                reason=MemoryGateReason.EXPLICIT_REMEMBER,
                signals=["explicit remember request"],
                matched_rule=explicit_remember.rule,
                matched_span=explicit_remember.span,
            )

        contextual_update = resolve_contextual_memory_update(
            text,
            conversation_history,
            existing_memories,
        )
        if contextual_update.resolved:
            return MemoryGateDecision(
                should_extract=True,
                reason=MemoryGateReason.CONTEXTUAL_UPDATE,
                signals=["contextual_memory_update", contextual_update.update_type.value],
                matched_rule=f"contextual_{contextual_update.update_type.value}",
                matched_span=contextual_update.evidence_span,
                contextual_probe=True,
                antecedent_candidate_ids=list(contextual_update.candidate_ids),
                selected_target_memory_id=contextual_update.target.id,
                target_guard_result="compatible_active_target",
                contextual_update_type=contextual_update.update_type.value,
            )

        contextual_event = resolve_contextual_relationship_event(
            text,
            conversation_history,
            existing_memories,
        )
        if contextual_event is not None:
            return MemoryGateDecision(
                should_extract=True,
                reason=MemoryGateReason.DURABLE_SIGNAL,
                signals=["contextual_relationship_event", contextual_event.signal],
                matched_rule="contextual_relationship_event",
                matched_span=text,
            )

        if has_retrospective_event_semantics(text):
            return MemoryGateDecision(
                should_extract=True,
                reason=MemoryGateReason.DURABLE_SIGNAL,
                signals=["retrospective_event_semantics"],
                matched_rule="retrospective_event_semantics",
                matched_span=text,
                contextual_probe=may_contain_contextual_memory_update(text),
            )

        pure_partner_hypothesis = _first_match(
            normalized,
            _PURE_PARTNER_HYPOTHESIS_PATTERNS,
            "pure_partner_hypothesis",
        )
        if pure_partner_hypothesis is not None:
            return _skip(
                MemoryGateReason.CONSULTATION_ONLY,
                "partner hypothesis without observable claim",
                matched_rule=pure_partner_hypothesis.rule,
                matched_span=pure_partner_hypothesis.span,
            )

        signals = [
            name
            for name, patterns in _DURABLE_SIGNAL_PATTERNS.items()
            if any(pattern.search(normalized) for pattern in patterns)
        ]
        if "planned_event" in signals and _is_habitual_not_future(normalized):
            signals.remove("planned_event")
        interaction_decline = _find_interaction_decline(normalized)
        if interaction_decline is not None and "interaction_decline" not in signals:
            signals.append("interaction_decline")
        interaction_qualifier = _find_interaction_qualifier(normalized)
        if interaction_qualifier is not None and "interaction_qualifier" not in signals:
            signals.append("interaction_qualifier")
        if signals:
            generic_match = _first_signal_match(normalized, signals)
            matched = interaction_decline or interaction_qualifier or generic_match
            return MemoryGateDecision(
                should_extract=True,
                reason=MemoryGateReason.DURABLE_SIGNAL,
                signals=signals,
                matched_rule=matched.rule if matched is not None else None,
                matched_span=matched.span if matched is not None else None,
                contextual_probe=may_contain_contextual_memory_update(text),
            )
        consultation = _first_match(
            normalized,
            _CONSULTATION_PATTERNS,
            "consultation_question",
        )
        if consultation is not None:
            return _skip(
                MemoryGateReason.CONSULTATION_ONLY,
                "question without durable claim",
                matched_rule=consultation.rule,
                matched_span=consultation.span,
            )
        return _skip(
            MemoryGateReason.NO_DURABLE_SIGNAL,
            "no durable signal",
            matched_rule="no_durable_signal",
        )


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()


def _compact(value: str) -> str:
    return re.sub(r"[\s,，。.!！?？~～]", "", value)


def _skip(
    reason: MemoryGateReason,
    signal: str,
    *,
    matched_rule: str | None = None,
    matched_span: str | None = None,
) -> MemoryGateDecision:
    return MemoryGateDecision(
        should_extract=False,
        reason=reason,
        signals=[signal],
        matched_rule=matched_rule,
        matched_span=matched_span,
    )


def _first_match(
    text: str,
    patterns: tuple[re.Pattern[str], ...],
    rule_prefix: str,
) -> _GateMatch | None:
    for index, pattern in enumerate(patterns, start=1):
        match = pattern.search(text)
        if match is not None:
            return _GateMatch(f"{rule_prefix}_{index}", match.group(0))
    return None


def _first_signal_match(text: str, signals: list[str]) -> _GateMatch | None:
    for signal in signals:
        match = _first_match(text, _DURABLE_SIGNAL_PATTERNS.get(signal, ()), signal)
        if match is not None:
            return match
    return None


def _find_interaction_decline(text: str) -> _GateMatch | None:
    for rule, pattern in _INTERACTION_DECLINE_RULES:
        match = pattern.search(text)
        if match is not None:
            return _GateMatch(rule, match.group(0))
    return None


def _find_interaction_qualifier(text: str) -> _GateMatch | None:
    for rule, pattern in _INTERACTION_QUALIFIER_RULES:
        match = pattern.search(text)
        if match is not None:
            return _GateMatch(rule, match.group(0))
    return None


def _has_consultation_question(text: str) -> bool:
    return any(pattern.search(text) for pattern in _CONSULTATION_PATTERNS)


def _is_habitual_not_future(text: str) -> bool:
    habitual = re.search(r"平时|通常|每周|每月|每逢|偶尔|有时", text) is not None
    explicit_future = re.search(
        r"明天|后天|大后天|下周|下个月|本周末|这周末|过几天|几天后|月底|"
        r"准备|计划|打算|将要|约好|已经约",
        text,
    ) is not None
    return habitual and not explicit_future


_CASUAL_MESSAGES = {
    "你好",
    "您好",
    "在吗",
    "你好在吗",
    "谢谢",
    "谢谢你",
    "先这样",
    "谢谢先这样",
    "好的",
    "明白了",
    "再见",
    "拜拜",
    "晚安",
}

_CASUAL_PATTERNS = (
    re.compile(r"^(?:早上好|上午好|中午好|下午好|晚上好|早安|午安)(?:啊|呀|呢)?[!！。]*$"),
)

_HYPOTHETICAL_PATTERNS = (
    re.compile(r"^(?:假设|假如|如果|举个例子|如果一个人|一般来说)"),
    re.compile(r"纯属假设|只是举例"),
)

_OPERATION_PATTERNS = (
    re.compile(r"^(?:请)?(?:把|将).{0,20}(?:压缩|改写|翻译|总结|列成|改成)"),
    re.compile(r"^(?:这次|本轮).{0,12}(?:不要|别).{0,8}(?:引用|读取|使用).{0,8}记忆"),
    re.compile(r"你刚才为什么.{0,20}(?:检索|路由|调用|输出)"),
    re.compile(r"^(?:重新|继续|停止|暂停)(?:回答|生成|输出)"),
)

_KNOWLEDGE_QUESTION_PATTERNS = (
    re.compile(r"^(?:什么叫|什么是|请解释|解释一下|介绍一下|如何定义)"),
    re.compile(r"(?:是什么概念|是什么意思)\??$"),
)

_PURE_PARTNER_HYPOTHESIS_PATTERNS = (
    re.compile(r"^(?:你觉得)?(?:她|他|对方).{0,12}(?:是不是|会不会).{0,16}(?:不喜欢|没兴趣|兴趣下降).*[?？]?$"),
    re.compile(r"^你觉得.{0,20}(?:兴趣下降|不喜欢).*[?？]?$"),
)

_EXPLICIT_REMEMBER_PATTERNS = (
    re.compile(r"(?:请)?记住[：:]?"),
    re.compile(r"记一下[：:]?"),
)

# Keep the interaction-decline vocabulary separate from the broader durable
# signal patterns below.  This makes it possible to expand colloquial Chinese
# without weakening unrelated Gate rules.
_INTERACTION_TIME_MARKERS = (
    "最近",
    "近来",
    "这段时间",
    "这几天",
    "这周",
    "上周",
    "过去",
    "前阵子",
    "一直",
)
_INTERACTION_SUBJECTS = ("我和她", "我和他", "我俩", "我们", "她", "他", "对方", "对象", "伴侣")
_INTERACTION_TARGET = r"(?:我|你|他|她|人|对方|消息|信息)?"
_INTERACTION_VERB = (
    r"(?:理(?:睬|我|你|他|她|人|对方)?|"
    rf"搭理{_INTERACTION_TARGET}|"
    rf"回复{_INTERACTION_TARGET}|回(?:我|消息|信息)|"
    rf"联系{_INTERACTION_TARGET}|聊天{_INTERACTION_TARGET}|"
    rf"交流{_INTERACTION_TARGET}|沟通{_INTERACTION_TARGET}|互动{_INTERACTION_TARGET})"
)
_INTERACTION_VERB_WITH_BOUNDARY = rf"{_INTERACTION_VERB}(?:了|着|过)?(?![\u4e00-\u9fff])"
_INTERACTION_DECLINE_PREFIX = r"(?:不怎么|不太|很少|几乎不)"
_INTERACTION_DECLINE_TREND = (
    r"(?:回复|联系|聊天|交流|沟通|互动)(?:明显)?"
    r"(?:变少(?:了)?|越来越少|减少(?:了)?|下降(?:了)?|降低(?:了)?|少了)"
    r"(?![\u4e00-\u9fff])"
)
_INTERACTION_DECLINE_PHRASE = (
    rf"(?:{_INTERACTION_DECLINE_PREFIX}{_INTERACTION_VERB_WITH_BOUNDARY}|"
    rf"爱答不理|{_INTERACTION_DECLINE_TREND})"
)
_INTERACTION_DECLINE_RULES = (
    (
        "temporal_interaction_decline",
        re.compile(
            rf"(?:{'|'.join(map(re.escape, _INTERACTION_TIME_MARKERS))})"
            rf".{{0,24}}{_INTERACTION_DECLINE_PHRASE}"
        ),
    ),
    (
        "subject_interaction_decline",
        re.compile(
            rf"(?:{'|'.join(map(re.escape, _INTERACTION_SUBJECTS))})"
            rf".{{0,20}}{_INTERACTION_DECLINE_PHRASE}"
        ),
    ),
    ("interaction_decline", re.compile(_INTERACTION_DECLINE_PHRASE)),
)

# These are independent observable interaction dimensions.  They deliberately
# avoid inferring motivation or a global relationship state from one channel.
_INTERACTION_QUALIFIER_RULES = (
    (
        "quantified_contact_frequency",
        re.compile(
            r"(?:一天|每天|一日).{0,5}(?:只|就|才).{0,5}"
            r"(?:回(?:复)?|联系|聊天).{0,8}"
            r"(?:\d+|[一二三四五六七八九十两]+).{0,4}(?:条|次)"
        ),
    ),
    (
        "response_engagement_qualifier",
        re.compile(
            r"(?:内容|回复|回(?:我|消息)?).{0,10}"
            r"(?:不(?:是|算)?(?:特别)?敷衍|不敷衍|还算认真|挺认真|很认真)"
        ),
    ),
    (
        "offline_interaction_qualifier",
        re.compile(
            r"(?:线下|见面).{0,14}(?:挺正常|很正常|没什么问题|还不错|挺好)"
        ),
    ),
    (
        "initiation_balance_qualifier",
        re.compile(r"(?:基本|大多|通常).{0,8}(?:都是|由).{0,6}我主动(?:联系|聊天|找她)?"),
    ),
)

_DURABLE_SIGNAL_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "preference": (
        re.compile(
            r"(?:我|她|他|对象|伴侣|我们).{0,16}"
            r"(?:喜欢(?!的)|不喜欢|爱吃|不吃|能接受|不能接受|讨厌|偏好|过敏|不能吃|想去|"
            r"勤俭节约|节俭|经济实惠|实惠|精打细算|省钱|消费观(?:念)?|消费习惯)"
        ),
    ),
    "temporal_interaction": (
        re.compile(
            r"(?:最近|过去|这几天|这周|上周|昨晚|昨天|今天|刚刚|刚|一直|通常|每周|每月)"
            r".{0,24}(?:联系|聊天|交流|见面|通话|吵|争执|约会|回复|回我|道歉|主动|拒绝|"
            r"冷淡|和好|复盘|约了|改到|推荐|分享)"
        ),
    ),
    "relationship_event": (
        re.compile(
            r"(?:我和|我俩|我们|双方|她|他|对象|伴侣).{0,30}"
            r"(?:联系|聊天|交流|见面|通话|吵架|争执|分手|拒绝|约定|主动|表白|在一起|复盘|约了|改到|"
            r"回复|回我|道歉|不理|打不通|和好|和解|达成一致|推荐|分享)"
        ),
    ),
    "planned_relationship_action": (
        re.compile(r"(?:准备|打算|计划|要去|想要).{0,20}(?:表白|告白)"),
        re.compile(
            r"(?:决定|准备|打算|计划|安排).{0,36}"
            r"(?:邀请|约|见面|联系|道歉|吃饭|看电影|聊|谈|沟通|解释|"
            r"请.{0,8}(?:吃.{0,2}饭|喝咖啡|看电影|逛))"
        ),
    ),
    "stable_fact": (
        re.compile(
            r"(?:我|她|他|对象|伴侣|我们).{0,12}"
            r"(?:是同学|是同事|住在|工作在|确认关系|认识了|有空|没空)"
        ),
    ),
    "profile_fact": (
        re.compile(
            r"(?:我|她|他|对象|伴侣).{0,10}"
            r"(?:来自|老家(?:在|是)?|家乡(?:在|是)?|是(?!一个|个).{1,8}(?:人|的))"
        ),
        re.compile(
            r"(?:我|她|他|对象|伴侣).{0,12}"
            r"(?:有(?:一个|个)?(?:哥哥|姐姐|弟弟|妹妹)|是独生子女)"
        ),
        re.compile(
            r"(?:我|她|他|对象|伴侣).{0,16}"
            r"(?:内向|外向|慢热|不善言辞|不太会聊天|不.{0,4}外向|社恐)"
        ),
    ),
    "shared_context": (
        re.compile(
            r"(?:我和她|我和他|我俩|我们).{0,40}"
            r"(?:同组|小组|同学|同事|同班|一起上课|共同课程|课程作业|"
            r"分到.{0,12}(?:同一个|同一|一起)?(?:组|小组))"
        ),
        re.compile(
            r"(?:组里|课上|课堂|讨论时|做作业时).{0,30}"
            r"(?:聊|交流|互动|讨论|说上几句|说几句话)"
        ),
    ),
    "relationship_state": (
        re.compile(
            r"(?:刚认识|认识不久|不.{0,2}熟|不熟悉|越来越熟|已经很熟|彼此熟悉|"
            r"比较熟|熟了(?:一|些|一点|一些)?|更熟|逐渐熟|慢慢熟|熟络|"
            r"比较陌生|了解不多|关系生疏)"
        ),
        re.compile(
            r"(?:(?:接触|见面|碰面|独处|聊天).{0,5}机会.{0,5}"
            r"(?:很少|不多|有限|很多|较多|充足)|"
            r"(?:很少|没有|几乎没有|很多|经常有|缺少).{0,6}机会.{0,10}"
            r"(?:接触|见面|碰面|独处|聊))"
        ),
        re.compile(
            r"(?:是否|是不是|不确定|不知道|没确认|没有确认).{0,10}"
            r"(?:单身|有对象|有伴侣|感情状态)"
        ),
        re.compile(
            r"(?:她|他|对方).{0,12}(?:单身|有对象|有男朋友|有女朋友|"
            r"有伴侣|已婚|结婚了)"
        ),
    ),
    "planned_event": (
        re.compile(
            r"(?:明天|后天|大后天|下周|下个月|本周末|这周末|周末|过几天|几天后|月底|"
            r"周[一二三四五六日天]).{0,50}"
            r"(?:有|要|将|准备|计划|安排|打算|参加|去|见|开|做|进行|约)"
        ),
        re.compile(
            r"(?:有|要|将|准备|计划|安排|打算|参加|去|见|开|做|进行|约).{0,50}"
            r"(?:明天|后天|大后天|下周|下个月|本周末|这周末|周末|过几天|几天后|月底|"
            r"周[一二三四五六日天])"
        ),
    ),
    "self_belief": (
        re.compile(
            r"(?:我觉得|我认为|我就是|可是我|但我|我就).{0,24}"
            r"(?:普通|没.{0,4}长处|配不上|不自信|自卑|不够好|没优势)"
        ),
    ),
    "partner_attribute": (
        re.compile(
            r"(?:对方|她|他).{0,16}"
            r"(?:优秀|成绩好|漂亮|帅|能力强|很有才华|条件好)"
        ),
    ),
    "belief_or_competition": (
        re.compile(
            r"(?:听说|感觉|觉得|认为).{0,36}"
            r"(?:聊天|联系|追求|喜欢|竞争|比我|优秀|希望大)"
        ),
    ),
    "advice_outcome": (
        re.compile(
            r"(?:我照你说的|按你说的|按照建议|采纳建议|听了建议).{0,80}"
            r"(?:有效|有用|成功|改善|缓和|和好)"
        ),
        re.compile(
            r"(?:选择|调整|改成|主动).{0,50}(?:之后|后).{0,20}"
            r"(?:开心|满意|和好|改善|缓和|有效)"
        ),
        re.compile(
            r"(?:考虑到|顾及|尊重).{0,50}(?:选择|调整|改成).{0,50}"
            r"(?:开心|满意|和好|改善|缓和)"
        ),
    ),
}

_CONSULTATION_PATTERNS = (
    re.compile(r"怎么办|如何|怎样|为什么|是不是|该不该|要不要|能不能|可以吗"),
    re.compile(r"(?:^|[，,。；;])\s*怎么"),
    re.compile(r"(?:我|那|这|该|应该).{0,3}怎么(?:办|做|说|追|聊|回复|处理|解决|开始)"),
    re.compile(r"(?:有啥|有什么|给我|能给).{0,8}建议"),
)

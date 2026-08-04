import re
import unicodedata
from collections.abc import Iterable

from loveapp.application.relationship_events import resolve_contextual_relationship_event
from loveapp.domain.memory import MemoryGateDecision, MemoryGateReason, MemoryItem, StoredMessage
from loveapp.domain.relationship_plan import has_retrospective_event_semantics


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
            return _skip(MemoryGateReason.CASUAL, "exact_casual")
        if any(pattern.search(normalized) for pattern in _HYPOTHETICAL_PATTERNS):
            return _skip(MemoryGateReason.HYPOTHETICAL, "hypothetical framing")
        if any(pattern.search(normalized) for pattern in _OPERATION_PATTERNS):
            return _skip(MemoryGateReason.OPERATION, "agent operation")
        if any(pattern.search(normalized) for pattern in _KNOWLEDGE_QUESTION_PATTERNS):
            return _skip(MemoryGateReason.KNOWLEDGE_QUESTION, "generic knowledge")
        if any(pattern.search(normalized) for pattern in _EXPLICIT_REMEMBER_PATTERNS):
            return MemoryGateDecision(
                should_extract=True,
                reason=MemoryGateReason.EXPLICIT_REMEMBER,
                signals=["explicit remember request"],
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
            )

        if has_retrospective_event_semantics(text):
            return MemoryGateDecision(
                should_extract=True,
                reason=MemoryGateReason.DURABLE_SIGNAL,
                signals=["retrospective_event_semantics"],
            )

        signals = [
            name
            for name, patterns in _DURABLE_SIGNAL_PATTERNS.items()
            if any(pattern.search(normalized) for pattern in patterns)
        ]
        if "planned_event" in signals and _is_habitual_not_future(normalized):
            signals.remove("planned_event")
        if signals:
            return MemoryGateDecision(
                should_extract=True,
                reason=MemoryGateReason.DURABLE_SIGNAL,
                signals=signals,
            )
        if _has_consultation_question(normalized):
            return _skip(MemoryGateReason.CONSULTATION_ONLY, "question without durable claim")
        return _skip(MemoryGateReason.NO_DURABLE_SIGNAL, "no durable signal")


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()


def _compact(value: str) -> str:
    return re.sub(r"[\s,，。.!！?？~～]", "", value)


def _skip(reason: MemoryGateReason, signal: str) -> MemoryGateDecision:
    return MemoryGateDecision(should_extract=False, reason=reason, signals=[signal])


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

_EXPLICIT_REMEMBER_PATTERNS = (
    re.compile(r"(?:请)?记住[：:]?"),
    re.compile(r"记一下[：:]?"),
)

_DURABLE_SIGNAL_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "preference": (
        re.compile(
            r"(?:我|她|他|对象|伴侣|我们).{0,16}"
            r"(?:喜欢(?!的)|不喜欢|爱吃|不吃|讨厌|偏好|过敏|不能吃|想去|"
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

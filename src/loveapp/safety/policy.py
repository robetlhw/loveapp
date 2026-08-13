import re
import unicodedata
from collections.abc import Sequence
from typing import ClassVar

from pydantic import BaseModel, Field

from loveapp.domain.enums import RiskLevel
from loveapp.domain.memory import MessageRole, StoredMessage
from loveapp.domain.routing import RecentRiskState


class SafetyAssessment(BaseModel):
    risk_level: RiskLevel
    reasons: list[str] = Field(default_factory=list)
    inherited: bool = False
    deescalated: bool = False


class SafetyPolicy:
    _high_risk_patterns: ClassVar[dict[str, tuple[re.Pattern[str], ...]]] = {
        "可能存在人身暴力": tuple(
            re.compile(pattern)
            for pattern in (
                r"打(?:我|她|他|人)",
                r"杀(?:了|掉|死)?(?:我|你|她|他|人)",
                r"伤害(?:我|你|她|他|自己|人)",
                r"拿(?:刀|棍|武器).{0,8}(?:吓|威胁|捅|砍|打)",
                r"拿(?:着)?(?:刀|棍|武器).{0,10}(?:去|找|到|楼下)",
                r"(?:推搡|掐脖|扇耳光|拳打脚踢)",
                r"砸(?:坏|烂|东西|手机|门)",
                r"人身威胁|家暴",
            )
        ),
        "可能存在跟踪、限制自由或强迫": tuple(
            re.compile(pattern)
            for pattern in (
                r"跟踪|尾随|蹲守|监视|骚扰",
                r"堵(?:住|门|在)?(?:我|她|他)?",
                r"不让.{0,8}(?:离开|走|出门)",
                r"强迫",
                r"逼(?:我|她|他)?.{0,10}(?:交出|密码|定位|见面|交往|发生|就范)",
            )
        ),
        "可能存在自伤风险": tuple(
            re.compile(pattern)
            for pattern in (
                r"自杀|不想活|伤害自己|结束生命",
                r"(?:跳楼|从楼上跳|割腕|吞药)",
                r"活着.{0,6}(?:没有|没).{0,4}意义",
                r"(?:写好|留下|准备).{0,8}(?:遗书|告别的话|告别信)",
            )
        ),
        "可能存在未经同意的亲密行为": tuple(
            re.compile(pattern)
            for pattern in (
                r"未经同意",
                r"不顾.{0,10}(?:拒绝|说不要).{0,10}(?:强行|强迫)",
                r"(?:强行|强迫).{0,10}(?:亲密行为|发生关系|性行为)",
            )
        ),
        "可能存在报复意图": tuple(
            re.compile(pattern) for pattern in (r"报复", r"毁掉(?:我|她|他)")
        ),
    }

    _continuation_pattern: ClassVar[re.Pattern[str]] = re.compile(
        r"(?:现在|已经|正在|马上|这就|那我就|我就).{0,14}"
        r"(?:进去|进入|过去|到了?|楼下|上楼|敲门|门口|靠近|动手|开始|继续|找她|找他|找对方)"
        r"|(?:继续|接着).{0,8}(?:做|进去|动手|找她|找他)"
        r"|(?:还在|仍在|一直).{0,10}(?:等她|等他|等对方|楼下|附近)"
        r"|(?:继续|接着).{0,12}(?:等她|等他|等对方|楼下|附近)"
        r"|(?:站在|守在|等在).{0,8}(?:门口|楼下|附近)"
        r"|(?:我|他|她)?(?:已经|正在)?进(?:了)?(?:她|他|对方)?(?:家|房间|门内)"
        r"|(?:我|他|她)?(?:已经|正在)?进去了?"
    )
    _deescalation_pattern: ClassVar[re.Pattern[str]] = re.compile(
        r"(?:已经|现在)?(?:回家|离开|远离|停下|冷静下来)"
        r"|(?:刀|武器).{0,8}(?:交给|放下|收起来)"
        r"|(?:报警|联系警方|联系家人|找家人|到了安全的地方)"
        r"|不会.{0,8}(?:伤害|动手|跟踪|报复)"
    )
    _safety_seeking_pattern: ClassVar[re.Pattern[str]] = re.compile(
        r"(?:怎么|如何|怎样).{0,8}(?:避免|防止|阻止).{0,8}"
        r"(?:伤害自己|自残|自杀)"
        r"|(?:害怕|担心|不想).{0,8}(?:伤害自己|自残|自杀)"
    )

    def __init__(self, *, context_turns: int = 4) -> None:
        self._context_turns = max(2, min(context_turns, 4))

    def assess(
        self,
        text: str,
        recent_messages: Sequence[StoredMessage] = (),
        previous_risk_state: RecentRiskState | None = None,
    ) -> SafetyAssessment:
        normalized = unicodedata.normalize("NFKC", text).casefold()
        reasons = self._current_reasons(normalized)
        if reasons:
            return SafetyAssessment(risk_level=RiskLevel.HIGH, reasons=reasons)
        if self._safety_seeking_pattern.search(normalized):
            return SafetyAssessment(
                risk_level=RiskLevel.SENSITIVE,
                reasons=["用户表达了避免自伤的安全求助"],
            )

        inherited_reasons = self._recent_risk_reasons(
            recent_messages,
            previous_risk_state,
        )
        if not inherited_reasons:
            return SafetyAssessment(risk_level=RiskLevel.NORMAL)
        # A current continuation action wins over any apparent de-escalation
        # phrase. This keeps phrases such as "我不会停下，现在就进去" from
        # being treated as safe merely because they contain "停下".
        if self._continuation_pattern.search(normalized):
            return SafetyAssessment(
                risk_level=RiskLevel.HIGH,
                reasons=[*inherited_reasons, "当前表达延续了近期高风险行动"],
                inherited=True,
            )
        if _has_affirmative_deescalation_signal(normalized, self._deescalation_pattern):
            return SafetyAssessment(
                risk_level=RiskLevel.SENSITIVE,
                reasons=["近期高风险情境已出现明确降级信号"],
                inherited=True,
                deescalated=True,
            )
        return SafetyAssessment(
            risk_level=RiskLevel.SENSITIVE,
            reasons=["近期对话包含尚未完全解除的高风险情境"],
            inherited=True,
        )

    def _current_reasons(self, normalized: str) -> list[str]:
        return [
            reason
            for reason, patterns in self._high_risk_patterns.items()
            if any(_has_non_negated_match(normalized, pattern) for pattern in patterns)
        ]

    def _recent_risk_reasons(
        self,
        recent_messages: Sequence[StoredMessage],
        previous_risk_state: RecentRiskState | None,
    ) -> list[str]:
        if (
            previous_risk_state is not None
            and previous_risk_state.expires_after_turns > 0
            and previous_risk_state.level == RiskLevel.HIGH
        ):
            return list(previous_risk_state.reasons) or ["上一轮存在高风险状态"]
        user_messages = [
            message
            for message in recent_messages
            if message.role == MessageRole.USER
        ][-self._context_turns :]
        for message in reversed(user_messages):
            reasons = self._current_reasons(
                unicodedata.normalize("NFKC", message.content).casefold()
            )
            if reasons:
                return reasons
            if _has_affirmative_deescalation_signal(
                unicodedata.normalize("NFKC", message.content).casefold(),
                self._deescalation_pattern,
            ):
                return []
        return []


def _has_non_negated_match(text: str, pattern: re.Pattern[str]) -> bool:
    return any(not _is_negated(text, match.start()) for match in pattern.finditer(text))


def _is_negated(text: str, start: int) -> bool:
    prefix = text[max(0, start - 14) : start]
    clause = re.split(r"[，,。！？!?；;]", prefix)[-1]
    return (
        re.search(
            r"(?:不会|不想|没有|没想|还没|未|不要|别|绝不|停止|避免|不再|已不|已经不|从不).{0,9}$",
            clause,
        )
        is not None
    )


def _has_affirmative_deescalation_signal(text: str, pattern: re.Pattern[str]) -> bool:
    """Reject negated or ineffective de-escalation wording in a risk context."""

    invalid_suffix = re.compile(r"(?:也)?(?:没用|没有用|不行|不能|做不到|来不及)")
    return any(
        not _is_negated(text, match.start())
        and invalid_suffix.search(text[match.end() : match.end() + 8]) is None
        for match in pattern.finditer(text)
    )

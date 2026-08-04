import re
import unicodedata
from typing import ClassVar

from pydantic import BaseModel, Field

from loveapp.domain.enums import RiskLevel


class SafetyAssessment(BaseModel):
    risk_level: RiskLevel
    reasons: list[str] = Field(default_factory=list)


class SafetyPolicy:
    _high_risk_patterns: ClassVar[dict[str, tuple[re.Pattern[str], ...]]] = {
        "可能存在人身暴力": tuple(
            re.compile(pattern)
            for pattern in (
                r"打(?:我|她|他|人)",
                r"杀(?:了|掉|死)?(?:我|你|她|他|人)",
                r"拿(?:刀|棍|武器).{0,8}(?:吓|威胁|捅|砍|打)",
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

    def assess(self, text: str) -> SafetyAssessment:
        normalized = unicodedata.normalize("NFKC", text).casefold()
        reasons = [
            reason
            for reason, patterns in self._high_risk_patterns.items()
            if any(_has_non_negated_match(normalized, pattern) for pattern in patterns)
        ]
        return SafetyAssessment(
            risk_level=RiskLevel.HIGH if reasons else RiskLevel.NORMAL,
            reasons=reasons,
        )


def _has_non_negated_match(text: str, pattern: re.Pattern[str]) -> bool:
    return any(not _is_negated(text, match.start()) for match in pattern.finditer(text))


def _is_negated(text: str, start: int) -> bool:
    prefix = text[max(0, start - 14) : start]
    clause = re.split(r"[，。！？!?；;]", prefix)[-1]
    return re.search(r"(?:不会|不想|没有|没想|不要|别|绝不|停止|避免).{0,9}$", clause) is not None

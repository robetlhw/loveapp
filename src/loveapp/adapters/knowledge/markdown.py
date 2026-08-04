import re
from dataclasses import dataclass, field
from pathlib import Path

from loveapp.domain.enums import (
    AdviceGoal,
    AdviceScenario,
    RiskLevel,
    SourceType,
)
from loveapp.domain.knowledge import KnowledgeDocument

_SECTION_HEADING = re.compile(r"^#\s+(.+?)\s*$")
_QUESTION_HEADING = re.compile(r"^##\s+(?:(\d+)[.、]\s*)?(.+?)\s*$")
_FIELD = re.compile(r"^\*\*(标签|问|答)[：:]\*\*\s*(.*?)\s*$")
_TAG_SEPARATOR = re.compile(r"[、,，;；]")


@dataclass
class _QuestionBlock:
    section: str
    ordinal: int | None
    title: str
    lines: list[str] = field(default_factory=list)


def load_qa_markdown(path: Path) -> list[KnowledgeDocument]:
    return parse_qa_markdown(
        path.read_text(encoding="utf-8-sig"),
        source_ref=path.name,
    )


def parse_qa_markdown(text: str, source_ref: str) -> list[KnowledgeDocument]:
    blocks: list[_QuestionBlock] = []
    current_section = ""
    current_block: _QuestionBlock | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        question_heading = _QUESTION_HEADING.match(line)
        section_heading = _SECTION_HEADING.match(line)

        if question_heading:
            if current_block is not None:
                blocks.append(current_block)
            ordinal = int(question_heading.group(1)) if question_heading.group(1) else None
            current_block = _QuestionBlock(
                section=current_section,
                ordinal=ordinal,
                title=question_heading.group(2).strip(),
            )
            continue

        if section_heading:
            if current_block is not None:
                blocks.append(current_block)
                current_block = None
            heading = section_heading.group(1).strip()
            if not heading.startswith("LoveApp") and heading != "入库建议":
                current_section = _strip_section_number(heading)
            continue

        if current_block is not None:
            current_block.lines.append(line)

    if current_block is not None:
        blocks.append(current_block)

    documents = [_block_to_document(block, source_ref) for block in blocks]
    if not documents:
        raise ValueError(f"Markdown 中没有找到以二级标题表示的问答块：{source_ref}")
    return documents


def _block_to_document(block: _QuestionBlock, source_ref: str) -> KnowledgeDocument:
    fields: dict[str, list[str]] = {"标签": [], "问": [], "答": []}
    active_field: str | None = None

    for line in block.lines:
        field_match = _FIELD.match(line.strip())
        if field_match:
            active_field = field_match.group(1)
            value = field_match.group(2).strip()
            if value:
                fields[active_field].append(value)
            continue
        if active_field and line.strip() and line.strip() != "---":
            fields[active_field].append(line.strip())

    question = _join_paragraphs(fields["问"]) or block.title
    answer = _join_paragraphs(fields["答"])
    if not answer:
        raise ValueError(f"问答块缺少答案：{block.title}")

    tags = _parse_tags(fields["标签"])
    scenario, risk_level = _classify_section(block.section)
    goals = _classify_goals(" ".join([block.title, question, *tags]))
    ordinal = block.ordinal or 0
    query_variants = [] if question == block.title else [block.title]
    return KnowledgeDocument(
        id=f"formal_v1_{ordinal:03d}" if ordinal else _fallback_id(block.title),
        title=block.title,
        scenario=scenario,
        goals=goals,
        tags=tags,
        question=question,
        query_variants=query_variants,
        answer=answer,
        section=block.section or None,
        ordinal=block.ordinal,
        risk_level=risk_level,
        source_type=SourceType.SYNTHETIC_DRAFT,
        source_ref=source_ref,
        version="1.0",
    )


def _classify_section(
    section: str,
) -> tuple[AdviceScenario, RiskLevel]:
    if "追求" in section:
        return AdviceScenario.PURSUIT, RiskLevel.NORMAL
    if "冲突" in section:
        return AdviceScenario.CONFLICT, RiskLevel.NORMAL
    if "风险" in section or "边界" in section:
        return AdviceScenario.BOUNDARY, RiskLevel.HIGH
    return AdviceScenario.RELATIONSHIP_MAINTENANCE, RiskLevel.NORMAL


def _classify_goals(text: str) -> list[AdviceGoal]:
    return [
        goal
        for goal, patterns in _GOAL_PATTERNS.items()
        if any(pattern in text for pattern in patterns)
    ]


def _parse_tags(lines: list[str]) -> list[str]:
    return [tag.strip() for line in lines for tag in _TAG_SEPARATOR.split(line) if tag.strip()]


def _join_paragraphs(lines: list[str]) -> str:
    return "\n".join(dict.fromkeys(line for line in lines if line))


def _strip_section_number(value: str) -> str:
    return re.sub(r"^[一二三四五六七八九十]+[、.．]\s*", "", value)


def _fallback_id(title: str) -> str:
    import hashlib

    digest = hashlib.sha256(title.encode("utf-8")).hexdigest()[:12]
    return f"formal_v1_{digest}"


_GOAL_PATTERNS: dict[AdviceGoal, tuple[str, ...]] = {
    AdviceGoal.INITIATE: (
        "刚认识",
        "怎么认识",
        "开口",
        "开启",
        "自然交流",
        "聊天技巧",
        "搭话",
        "搭讪",
    ),
    AdviceGoal.UNDERSTAND: (
        "是不是",
        "是否",
        "判断",
        "怎么理解",
        "算不算",
        "识别",
        "区别",
        "什么意思",
    ),
    AdviceGoal.PROGRESS: (
        "表白",
        "约会",
        "邀约",
        "邀请",
        "发展",
        "推进",
        "追求",
        "表达感情",
        "关系确认",
    ),
    AdviceGoal.REPAIR: (
        "吵架",
        "冲突",
        "道歉",
        "恢复",
        "修复",
        "冷战",
        "重复问题",
        "真正解决",
    ),
    AdviceGoal.COMMUNICATE: (
        "沟通",
        "表达",
        "讨论",
        "协调",
        "倾听",
        "解释",
        "追问",
        "怎么谈",
        "应该谈",
        "说清楚",
    ),
    AdviceGoal.SET_BOUNDARY: (
        "边界",
        "拒绝",
        "停止联系",
        "强迫",
        "控制",
        "跟踪",
        "骚扰",
        "暴力",
        "同意",
        "隐私",
        "贬低",
        "羞辱",
        "威胁",
        "限制",
    ),
    AdviceGoal.END_RELATIONSHIP: ("分手", "结束关系", "离开关系"),
}

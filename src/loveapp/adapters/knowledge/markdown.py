import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from loveapp.domain.enums import (
    AdviceGoal,
    AdviceScenario,
    RelationshipStage,
    RiskLevel,
    SourceType,
)
from loveapp.domain.knowledge import KnowledgeDocument

_SECTION_HEADING = re.compile(r"^#\s+(.+?)\s*$")
_QUESTION_HEADING = re.compile(r"^##\s+(?:(\d+)[.、]\s*)?(.+?)\s*$")
_FIELD = re.compile(
    r"^\*\*(ID|Scenario|RelationshipStages|Goals|RiskLevel|SourceType|Version|"
    r"QueryVariants|Context|Principles|RecommendedActions|SamplePhrases|"
    r"AvoidActions|ClarifyingQuestions|标签|问|答)[：:]\*\*\s*(.*?)\s*$"
)
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
    _ensure_unique_ids(documents, source_ref)
    return documents


def _block_to_document(block: _QuestionBlock, source_ref: str) -> KnowledgeDocument:
    fields: dict[str, list[str]] = {}
    present_fields: set[str] = set()
    active_field: str | None = None

    for line in block.lines:
        stripped = line.strip()
        field_match = _FIELD.match(stripped)
        if field_match:
            active_field = field_match.group(1)
            present_fields.add(active_field)
            fields.setdefault(active_field, [])
            value = field_match.group(2).strip()
            if value:
                fields[active_field].append(value)
            continue
        if active_field and stripped and stripped != "---":
            fields.setdefault(active_field, [])
            fields[active_field].append(stripped)

    document_id = (
        _scalar_field(fields.get("ID", []), block=block, field_name="ID")
        if "ID" in present_fields
        else None
    )
    question = _join_paragraphs(fields.get("问", []))
    if not question:
        raise _block_error(block, "缺少问字段", document_id=document_id)
    answer = _join_paragraphs(fields.get("答", []))
    if not answer:
        raise _block_error(block, "缺少答字段", document_id=document_id)

    tags = _parse_list_field(fields.get("标签", []), split_values=True)
    fallback_scenario, fallback_risk = _classify_section(block.section)
    scenario = (
        _parse_enum_field(
            fields.get("Scenario", []),
            AdviceScenario,
            block=block,
            field_name="Scenario",
            document_id=document_id,
        )
        if "Scenario" in present_fields
        else fallback_scenario
    )
    risk_level = (
        _parse_enum_field(
            fields.get("RiskLevel", []),
            RiskLevel,
            block=block,
            field_name="RiskLevel",
            document_id=document_id,
        )
        if "RiskLevel" in present_fields
        else fallback_risk
    )
    goals = (
        _parse_enum_list(
            fields.get("Goals", []),
            AdviceGoal,
            block,
            "Goals",
            document_id=document_id,
        )
        if "Goals" in present_fields
        else _classify_goals(" ".join([block.title, question, *tags]))
    )
    relationship_stages = (
        _parse_enum_list(
            fields.get("RelationshipStages", []),
            RelationshipStage,
            block,
            "RelationshipStages",
            document_id=document_id,
        )
        if "RelationshipStages" in present_fields
        else []
    )
    query_variants = (
        _parse_list_field(fields.get("QueryVariants", []), split_values=True)
        if "QueryVariants" in present_fields
        else ([] if question == block.title else [block.title])
    )
    context = _join_paragraphs(fields.get("Context", []))
    principles = _parse_list_field(fields.get("Principles", []), split_values=True)
    recommended_actions = _parse_list_field(
        fields.get("RecommendedActions", []), split_values=True
    )
    sample_phrases = _parse_list_field(fields.get("SamplePhrases", []), split_values=True)
    avoid_actions = _parse_list_field(fields.get("AvoidActions", []), split_values=True)
    clarifying_questions = _parse_list_field(
        fields.get("ClarifyingQuestions", []), split_values=True
    )
    version = (
        _scalar_field(
            fields.get("Version", []),
            block=block,
            field_name="Version",
            document_id=document_id,
        )
        if "Version" in present_fields
        else "1.0"
    )
    source_type = (
        _parse_enum_field(
            fields.get("SourceType", []),
            SourceType,
            block=block,
            field_name="SourceType",
            document_id=document_id,
        )
        if "SourceType" in present_fields
        else SourceType.SYNTHETIC_DRAFT
    )
    ordinal = block.ordinal or 0
    return KnowledgeDocument(
        id=document_id or (f"formal_v1_{ordinal:03d}" if ordinal else _fallback_id(block.title)),
        title=block.title,
        scenario=scenario,
        relationship_stages=relationship_stages,
        goals=goals,
        tags=tags,
        question=question,
        query_variants=query_variants,
        answer=answer,
        context=context,
        section=block.section or None,
        ordinal=block.ordinal,
        principles=principles,
        recommended_actions=recommended_actions,
        sample_phrases=sample_phrases,
        avoid_actions=avoid_actions,
        clarifying_questions=clarifying_questions,
        risk_level=risk_level,
        source_type=source_type,
        source_ref=source_ref,
        version=version,
    )


def _block_error(
    block: _QuestionBlock,
    message: str,
    *,
    document_id: str | None = None,
) -> ValueError:
    identity = block.title
    if block.ordinal is not None:
        identity = f"{block.ordinal}. {identity}"
    if document_id:
        identity = f"{document_id} / {identity}"
    return ValueError(f"问答块 {identity} {message}")


def _scalar_field(
    values: list[str],
    *,
    block: _QuestionBlock,
    field_name: str,
    document_id: str | None = None,
) -> str:
    value = _join_paragraphs(values)
    if not value:
        raise _block_error(block, f"{field_name} 不能为空", document_id=document_id)
    return value


def _parse_enum_field[EnumT: StrEnum](
    values: list[str],
    enum_type: type[EnumT],
    *,
    block: _QuestionBlock,
    field_name: str,
    document_id: str | None,
) -> EnumT:
    raw = _scalar_field(
        values,
        block=block,
        field_name=field_name,
        document_id=document_id,
    )
    try:
        return enum_type(raw)
    except (TypeError, ValueError) as exc:
        raise _block_error(
            block,
            f"{field_name} 枚举值非法：{raw}",
            document_id=document_id,
        ) from exc


def _parse_enum_list[EnumT: StrEnum](
    values: list[str],
    enum_type: type[EnumT],
    block: _QuestionBlock,
    field_name: str,
    *,
    document_id: str | None,
) -> list[EnumT]:
    raw_values = _parse_list_field(values, split_values=True)
    if not raw_values:
        raise _block_error(
            block,
            f"{field_name} 不能为空",
            document_id=document_id,
        )
    parsed: list[EnumT] = []
    for value in raw_values:
        try:
            parsed.append(enum_type(value))
        except (TypeError, ValueError) as exc:
            raise _block_error(
                block,
                f"{field_name} 枚举值非法：{value}",
                document_id=document_id,
            ) from exc
    return list(dict.fromkeys(parsed))


def _parse_list_field(values: list[str], *, split_values: bool) -> list[str]:
    """Parse markdown list items while retaining punctuation in bullet items."""
    result: list[str] = []
    for raw_value in values:
        is_bullet = bool(re.match(r"^[-*+]\s+", raw_value))
        value = re.sub(r"^[-*+]\s+", "", raw_value).strip()
        if not value or value == "[]":
            continue
        parts = _TAG_SEPARATOR.split(value) if split_values and not is_bullet else [value]
        result.extend(part.strip() for part in parts if part.strip())
    return list(dict.fromkeys(result))


def _ensure_unique_ids(documents: list[KnowledgeDocument], source_ref: str) -> None:
    seen: dict[str, str] = {}
    for document in documents:
        previous = seen.get(document.id)
        if previous is not None:
            description = (
                f"知识文档 ID 重复：{document.id}"
                f"（标题：{previous} 与 {document.title}；来源：{source_ref}）"
            )
            raise ValueError(description)
        seen[document.id] = document.title


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

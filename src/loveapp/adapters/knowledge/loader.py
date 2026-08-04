import json
import re
import unicodedata
from pathlib import Path

from pydantic import TypeAdapter

from loveapp.adapters.knowledge.markdown import load_qa_markdown
from loveapp.domain.enums import RiskLevel, SourceType
from loveapp.domain.knowledge import KnowledgeDocument

_DOCUMENTS_ADAPTER = TypeAdapter(list[KnowledgeDocument])


def parse_documents(value: object) -> list[KnowledgeDocument]:
    if isinstance(value, dict):
        value = [value]
    return _DOCUMENTS_ADAPTER.validate_python(value)


def load_knowledge_file(path: Path) -> list[KnowledgeDocument]:
    if path.suffix.casefold() in {".md", ".markdown"}:
        return load_qa_markdown(path)
    if path.suffix.casefold() == ".jsonl":
        values = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
        return parse_documents(values)
    if path.suffix.casefold() == ".json":
        return parse_documents(json.loads(path.read_text(encoding="utf-8-sig")))
    raise ValueError(f"不支持的知识文件格式：{path.suffix}")


def load_knowledge_path(path: Path) -> list[KnowledgeDocument]:
    if not path.exists():
        return []

    candidates = (
        [path]
        if path.is_file()
        else sorted(
            candidate
            for candidate in path.rglob("*")
            if candidate.suffix.casefold() in {".json", ".jsonl", ".md", ".markdown"}
            and candidate.name.casefold() != "readme.md"
        )
    )
    documents = [
        document for candidate in candidates for document in load_knowledge_file(candidate)
    ]
    _ensure_unique_ids(documents)
    return documents


def merge_knowledge_documents(
    *document_groups: list[KnowledgeDocument],
) -> list[KnowledgeDocument]:
    merged: list[KnowledgeDocument] = []
    id_to_index: dict[str, int] = {}
    question_to_index: dict[str, int] = {}
    for document in (item for group in document_groups for item in group):
        question_key = _canonical_question(document.question)
        index = id_to_index.get(document.id)
        if index is not None:
            existing = merged[index]
            if _canonical_question(existing.question) != question_key:
                raise ValueError(f"知识文档 ID 冲突且问题内容不同：{document.id}")
        else:
            index = question_to_index.get(question_key)
        if index is None:
            index = len(merged)
            merged.append(document)
        else:
            merged[index] = _merge_document(merged[index], document)
        id_to_index[merged[index].id] = index
        id_to_index[document.id] = index
        question_to_index[question_key] = index
    return merged


def _ensure_unique_ids(documents: list[KnowledgeDocument]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for document in documents:
        if document.id in seen:
            duplicates.add(document.id)
        seen.add(document.id)
    if duplicates:
        duplicate_list = ", ".join(sorted(duplicates))
        raise ValueError(f"知识文档 ID 重复：{duplicate_list}")


def _merge_document(
    first: KnowledgeDocument,
    second: KnowledgeDocument,
) -> KnowledgeDocument:
    source_type = max(
        (first.source_type, second.source_type),
        key=lambda value: _SOURCE_PRIORITY[value],
    )
    risk_level = max(
        (first.risk_level, second.risk_level),
        key=lambda value: _RISK_PRIORITY[value],
    )
    return first.model_copy(
        update={
            "relationship_stages": _unique(
                [*first.relationship_stages, *second.relationship_stages]
            ),
            "goals": _unique([*first.goals, *second.goals]),
            "tags": _unique([*first.tags, *second.tags]),
            "query_variants": _unique(
                [*first.query_variants, *second.query_variants]
            ),
            "answer": first.answer or second.answer,
            "context": first.context or second.context,
            "section": first.section or second.section,
            "ordinal": first.ordinal or second.ordinal,
            "principles": _unique([*first.principles, *second.principles]),
            "recommended_actions": _unique(
                [*first.recommended_actions, *second.recommended_actions]
            ),
            "sample_phrases": _unique(
                [*first.sample_phrases, *second.sample_phrases]
            ),
            "avoid_actions": _unique([*first.avoid_actions, *second.avoid_actions]),
            "clarifying_questions": _unique(
                [*first.clarifying_questions, *second.clarifying_questions]
            ),
            "risk_level": risk_level,
            "source_type": source_type,
            "source_ref": first.source_ref or second.source_ref,
            "version": max(first.version, second.version),
        }
    )


def _canonical_question(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", normalized)


def _unique[ValueT](values: list[ValueT]) -> list[ValueT]:
    return list(dict.fromkeys(values))


_SOURCE_PRIORITY = {
    SourceType.SYNTHETIC_DRAFT: 0,
    SourceType.REVIEWED_SYNTHETIC: 1,
    SourceType.PUBLIC_REFERENCE: 2,
    SourceType.SYSTEM_POLICY: 3,
}

_RISK_PRIORITY = {
    RiskLevel.NORMAL: 0,
    RiskLevel.SENSITIVE: 1,
    RiskLevel.HIGH: 2,
}

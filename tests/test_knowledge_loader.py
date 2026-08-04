import json

import pytest

from loveapp.adapters.knowledge.loader import load_knowledge_path, merge_knowledge_documents
from loveapp.bootstrap import load_seed_documents


def test_load_external_knowledge_file(tmp_path) -> None:
    document = load_seed_documents()[0].model_dump(mode="json", exclude={"retrieval_text"})
    document["id"] = "external_001"
    path = tmp_path / "knowledge.json"
    path.write_text(json.dumps([document], ensure_ascii=False), encoding="utf-8")

    loaded = load_knowledge_path(tmp_path)

    assert [item.id for item in loaded] == ["external_001"]


def test_duplicate_external_ids_fail_fast(tmp_path) -> None:
    document = load_seed_documents()[0].model_dump(mode="json", exclude={"retrieval_text"})
    path = tmp_path / "knowledge.json"
    path.write_text(json.dumps([document, document], ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="ID 重复"):
        load_knowledge_path(tmp_path)


def test_directory_loader_ignores_readme(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# 说明\n\n这里没有问答。", encoding="utf-8")

    assert load_knowledge_path(tmp_path) == []


def test_seed_and_formal_documents_merge_by_normalized_question() -> None:
    seed = load_seed_documents()[0]
    formal = seed.model_copy(
        update={
            "id": "formal_duplicate",
            "question": f"{seed.question.rstrip('？')}?",
            "answer": "正式答案。",
            "tags": ["正式标签"],
        }
    )

    merged = merge_knowledge_documents([seed], [formal])

    assert len(merged) == 1
    assert merged[0].id == seed.id
    assert merged[0].answer == "正式答案。"
    assert "正式标签" in merged[0].tags


def test_merge_rejects_same_id_with_different_questions() -> None:
    seed = load_seed_documents()[0]
    conflicting = seed.model_copy(update={"question": "完全不同的问题"})

    with pytest.raises(ValueError, match="ID 冲突"):
        merge_knowledge_documents([seed], [conflicting])

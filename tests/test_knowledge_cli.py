from typer.testing import CliRunner

from loveapp import cli
from loveapp.bootstrap import load_seed_documents


def test_knowledge_ingest_no_seed_indexes_only_external_documents(
    monkeypatch,
    tmp_path,
) -> None:
    external = load_seed_documents()[0].model_copy(update={"id": "external_only"})
    knowledge_path = tmp_path / "knowledge.json"
    knowledge_path.write_text(
        external.model_dump_json(exclude={"retrieval_text"}),
        encoding="utf-8",
    )
    captured = {}

    async def fake_ingest(documents, recreate):
        captured["ids"] = [document.id for document in documents]
        captured["recreate"] = recreate
        return len(documents), len(documents)

    monkeypatch.setattr(cli, "_ingest_documents", fake_ingest)

    result = CliRunner().invoke(
        cli.app,
        ["knowledge", "ingest", str(knowledge_path), "--no-seed"],
    )

    assert result.exit_code == 0, result.output
    assert captured == {"ids": ["external_only"], "recreate": True}


def test_knowledge_ingest_includes_seed_by_default(monkeypatch, tmp_path) -> None:
    external = load_seed_documents()[0].model_copy(
        update={"id": "external_unique", "question": "一个完全独立的知识问题？"}
    )
    knowledge_path = tmp_path / "knowledge.json"
    knowledge_path.write_text(
        external.model_dump_json(exclude={"retrieval_text"}),
        encoding="utf-8",
    )
    captured = {}

    async def fake_ingest(documents, recreate):
        captured["ids"] = [document.id for document in documents]
        return len(documents), len(documents)

    monkeypatch.setattr(cli, "_ingest_documents", fake_ingest)

    result = CliRunner().invoke(
        cli.app,
        ["knowledge", "ingest", str(knowledge_path)],
    )

    assert result.exit_code == 0, result.output
    assert set(captured["ids"]) == {
        *[document.id for document in load_seed_documents()],
        "external_unique",
    }

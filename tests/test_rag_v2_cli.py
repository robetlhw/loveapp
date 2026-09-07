import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import loveapp.cli as cli
from loveapp.evaluation.phase45_query_planner import ContextualRewriteCase

ROOT = Path(__file__).parents[1]
DATASET = ROOT / "evals/rag/cases_v2_dev.md"
TEST_DATASET = ROOT / "evals/rag/cases_v2_test.md"
KNOWLEDGE = ROOT / "knowledge/loveapp_rag_knowledge_base_v2.md"


def _report(*, mode: str, case_count: int, targets_passed: bool) -> dict:
    return {
        "schema_version": 2,
        "mode": mode,
        "case_count": case_count,
        "hit_at_1": 1.0,
        "hit_at_3": 1.0,
        "hit_at_5": 1.0,
        "mrr": 1.0,
        "ndcg_at_5": 1.0,
        "targets": {"passed": targets_passed, "checks": {}},
        "cases": [],
    }


def test_rag_cli_filters_cases_and_writes_json_and_markdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    async def fake_run(cases, *, mode, settings):
        del settings
        captured["ids"] = [case.id for case in cases]
        captured["mode"] = mode
        return _report(mode=mode, case_count=len(cases), targets_passed=True)

    monkeypatch.setattr(cli, "_run_rag_v2_cli_eval", fake_run)
    output = tmp_path / "rag.json"

    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "rag",
            "--dataset",
            str(DATASET),
            "--knowledge",
            str(KNOWLEDGE),
            "--mode",
            "retriever",
            "--output",
            str(output),
            "--case",
            "rag_v2_dev_001,rag_v2_dev_005",
            "--query-type",
            "colloquial",
            "--scenario",
            "relationship_maintenance",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured == {"ids": ["rag_v2_dev_001"], "mode": "retriever"}
    assert output.exists()
    assert output.with_suffix(".md").exists()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["inputs"]["filters"] == {
        "case": ["rag_v2_dev_001", "rag_v2_dev_005"],
        "query_type": ["colloquial"],
        "scenario": ["relationship_maintenance"],
    }
    assert report["integrity"]["passed"] is True
    assert set(report["integrity"]["validated_splits"]) == {"dev", "test"}


def test_rag_cli_fail_on_targets_exits_two_after_writing_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run(cases, *, mode, settings):
        del settings
        return _report(mode=mode, case_count=len(cases), targets_passed=False)

    monkeypatch.setattr(cli, "_run_rag_v2_cli_eval", fake_run)
    output = tmp_path / "rag-e2e.json"

    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "rag",
            "--dataset",
            str(DATASET),
            "--knowledge",
            str(KNOWLEDGE),
            "--mode",
            "e2e",
            "--output",
            str(output),
            "--case",
            "rag_v2_dev_001",
            "--fail-on-targets",
        ],
    )

    assert result.exit_code == 2, result.output
    assert output.exists()
    assert output.with_suffix(".md").exists()


def test_phase35_contextual_lint_reads_kb_without_retrieval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 4 lint must validate RelevantIDs even when retrieval is off."""

    dataset = tmp_path / "contextual.md"
    dataset.write_text("placeholder", encoding="utf-8")
    knowledge = tmp_path / "knowledge.md"
    knowledge.write_text("placeholder", encoding="utf-8")
    case = ContextualRewriteCase(
        id="contextual_control",
        query_type="standalone_control",
        difficulty="easy",
        length_bucket="short",
        expected_branch="rag",
        current_query="how should I communicate?",
        rewrite_required=False,
        expected_standalone_query="how should I communicate?",
        relevant_ids=["kb_v2_001"],
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "load_contextual_rewrite_eval_markdown", lambda path: [case])

    def fake_load_knowledge_path(path: Path) -> Path:
        captured["resolved_knowledge"] = path
        return knowledge

    monkeypatch.setattr(cli, "_phase35_load_knowledge_path", fake_load_knowledge_path)

    def fake_lint(**kwargs):
        captured["lint_knowledge"] = kwargs["knowledge"]
        return {"knowledge": str(kwargs["knowledge"]), "phase4": {"passed": True}}

    monkeypatch.setattr(cli, "_phase35_phase45_lint", fake_lint)
    output = tmp_path / "contextual.json"
    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "contextual-rewrite",
            "--dataset",
            str(dataset),
            "--knowledge",
            str(knowledge),
            "--without-retrieval",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["resolved_knowledge"] == knowledge
    assert captured["lint_knowledge"] == knowledge
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["inputs"]["with_retrieval"] is False
    assert report["inputs"]["knowledge"] == str(knowledge)


async def test_rag_cli_e2e_uses_executor_and_closes_retriever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRetriever:
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    retriever = FakeRetriever()
    router = object()

    class FakeRoutingContainer:
        closed = False

        def __init__(self) -> None:
            self.router = router

        async def aclose(self) -> None:
            self.closed = True

    routing_container = FakeRoutingContainer()
    executor = object()
    captured = []

    monkeypatch.setattr(cli, "build_qdrant_store", lambda settings: retriever)
    monkeypatch.setattr(
        cli,
        "build_routing_container",
        lambda settings: routing_container,
    )
    monkeypatch.setattr(
        cli,
        "build_e2e_executor",
        lambda route, value: executor if route is router and value is retriever else None,
    )

    async def fake_evaluate(cases, **kwargs):
        captured.append((cases, kwargs))
        return {
            "mode": kwargs["mode"],
            "hit_at_3": 0.9 if kwargs["mode"] == "e2e" else 1.0,
            "targets": {"passed": True},
        }

    monkeypatch.setattr(cli, "evaluate_rag_v2", fake_evaluate)
    monkeypatch.setattr(
        cli,
        "compare_oracle_and_e2e",
        lambda oracle, e2e: {"routing_degradation_pp": 10.0},
    )
    monkeypatch.setattr(
        cli,
        "evaluate_rag_targets",
        lambda report: {"passed": False, "checks": {"routing_degradation_pp": False}},
    )
    cases = [object()]

    report = await cli._run_rag_v2_cli_eval(cases, mode="e2e", settings=object())

    assert captured == [
        (cases, {"mode": "e2e", "executor": executor}),
        (cases, {"mode": "retriever", "retriever": retriever}),
    ]
    assert report["oracle_gap"] == {"routing_degradation_pp": 10.0}
    assert report["targets"]["passed"] is False
    assert routing_container.closed is True
    assert retriever.closed is True


async def test_rag_cli_ephemeral_qdrant_indexes_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSettings:
        qdrant_url = "http://configured"

        def model_copy(self, *, update):
            assert update == {"qdrant_url": ":memory:"}
            return self

    class FakeRetriever:
        closed = False
        indexed = None

        async def index_documents(self, documents, *, recreate):
            self.indexed = (documents, recreate)
            return len(documents)

        async def aclose(self) -> None:
            self.closed = True

    retriever = FakeRetriever()
    monkeypatch.setattr(cli, "build_qdrant_store", lambda settings: retriever)

    async def fake_evaluate(cases, **kwargs):
        assert kwargs["mode"] == "retriever"
        return {"mode": "retriever", "hit_at_3": 1.0}

    monkeypatch.setattr(cli, "evaluate_rag_v2", fake_evaluate)
    documents = [object()]
    report = await cli._run_rag_v2_cli_eval(
        [object()],
        mode="retriever",
        settings=FakeSettings(),
        documents=documents,
        ephemeral_qdrant=True,
    )

    assert retriever.indexed == (documents, True)
    assert report["retriever_backend"] == "qdrant_memory"
    assert report["indexed_document_count"] == 1
    assert retriever.closed is True


def test_rag_sweep_cli_writes_reports_from_dev_split(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    async def fake_run(documents, cases, *, settings, progress):
        del settings
        captured["document_count"] = len(documents)
        captured["case_count"] = len(cases)
        captured["progress_configured"] = progress is not None
        return {
            "schema_version": 2,
            "dataset": "dev",
            "protocol": "sequential_dev_only",
            "raw_query_only": True,
            "frozen_config": {"top_k": 5, "min_score": 0.45},
            "phases": {},
        }

    monkeypatch.setattr(cli, "_run_rag_v2_cli_sweep", fake_run)
    output = tmp_path / "sweep.json"

    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "rag-sweep",
            "--dataset",
            str(DATASET),
            "--knowledge",
            str(KNOWLEDGE),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured == {
        "document_count": 500,
        "case_count": 300,
        "progress_configured": True,
    }
    assert output.exists()
    assert output.with_suffix(".md").exists()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["inputs"] == {
        "dataset": str(DATASET),
        "knowledge": str(KNOWLEDGE),
    }
    assert set(report["integrity"]["validated_splits"]) == {"dev", "test"}


def test_rag_sweep_cli_rejects_test_split(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def fake_run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(cli, "_run_rag_v2_cli_sweep", fake_run)

    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "rag-sweep",
            "--dataset",
            str(TEST_DATASET),
            "--knowledge",
            str(KNOWLEDGE),
            "--output",
            str(tmp_path / "must-not-exist.json"),
        ],
    )

    assert result.exit_code == 1, result.output
    assert "Dev split only" in result.output
    assert called is False


async def test_rag_sweep_cli_closes_embedding_provider_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeEmbeddingProvider:
        close_count = 0

        async def aclose(self) -> None:
            self.close_count += 1

    provider = FakeEmbeddingProvider()
    captured = {}

    async def fake_sweep(documents, cases, *, embedding_provider, progress):
        captured.update(
            documents=documents,
            cases=cases,
            embedding_provider=embedding_provider,
            progress=progress,
        )
        raise RuntimeError("sweep failed")

    monkeypatch.setattr(cli, "build_embedding_provider", lambda settings: provider)
    monkeypatch.setattr(cli, "run_rag_v2_dev_sweep", fake_sweep)
    documents = [object()]
    cases = [object()]

    with pytest.raises(RuntimeError, match="sweep failed"):
        await cli._run_rag_v2_cli_sweep(
            documents,
            cases,
            settings=object(),
            progress=None,
        )

    assert captured == {
        "documents": documents,
        "cases": cases,
        "embedding_provider": provider,
        "progress": None,
    }
    assert provider.close_count == 1

from pathlib import Path
from types import SimpleNamespace

from pydantic import SecretStr
from typer.testing import CliRunner

from loveapp.cli import app


def test_memory_extraction_v1_cli_exposes_local_and_langsmith_modes() -> None:
    result = CliRunner().invoke(app, ["eval", "memory-extraction-v1", "--help"])

    assert result.exit_code == 0
    assert "--dataset" in result.stdout
    assert "--case" in result.stdout
    assert "--langsmith" in result.stdout
    assert "--sync-langsmith" in result.stdout
    assert "--fail-on-error" in result.stdout

    sync_result = CliRunner().invoke(
        app,
        ["eval", "memory-extraction-v1-sync-langsmith", "--help"],
    )
    assert sync_result.exit_code == 0
    assert "--dataset-name" in sync_result.stdout


def test_memory_extraction_v1_cli_without_langsmith_key_still_runs_locally(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.setattr(
        "loveapp.evaluation.memory_extraction_langsmith.create_langsmith_client",
        lambda: None,
    )
    settings = SimpleNamespace(
        llm_api_key=SecretStr("test"),
        llm_base_url="https://example.invalid",
        llm_model="strong-test",
        memory_extraction_model="flash-test",
        memory_extraction_strong_model="strong-test",
        memory_extraction_strong_timeout_seconds=10,
        memory_extraction_strong_max_retries=0,
        memory_extraction_strong_max_tokens=512,
        memory_extraction_strong_thinking="disabled",
    )

    class _Cascade:
        _flash = object()

        async def aclose(self) -> None:
            return None

    class _Matcher:
        def __init__(self, **_):
            pass

        async def aclose(self) -> None:
            return None

    captured = {}

    async def fake_evaluate(*_, observer, **__):
        captured["observer"] = observer
        metrics = {
            "claim_recall": 0.0,
            "spurious_claim_rate": 0.0,
            "perspective_accuracy": 0.0,
            "atomization_accuracy": 0.0,
            "context_reply_recall": 0.0,
            "empty_positive_rate": 0.0,
            "negative_restraint_false_positive_rate": 0.0,
        }
        return {
            "evaluation": "memory_extraction_v1",
            "dataset": "dataset.jsonl",
            "models": {},
            "layers": {
                "flash_raw": {"metrics": metrics},
                "flash_post_repair": {"metrics": metrics},
                "production_cascade": {"metrics": metrics},
            },
            "contributions": {},
            "cases": [],
        }

    monkeypatch.setattr("loveapp.cli.get_settings", lambda: settings)
    monkeypatch.setattr("loveapp.cli._build_memory_extractor", lambda _: _Cascade())
    monkeypatch.setattr(
        "loveapp.cli.OpenAICompatibleExtractionAlignmentJudge",
        _Matcher,
    )
    monkeypatch.setattr("loveapp.cli.evaluate_memory_extraction_v1", fake_evaluate)
    monkeypatch.setattr(
        "loveapp.cli.render_memory_extraction_v1_report",
        lambda _: "local-only report\n",
    )

    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        [
            "eval",
            "memory-extraction-v1",
            "--langsmith",
            "--output",
            "local-result.json",
        ],
    )

    assert result.exit_code == 0, f"{result.stdout}\n{result.exception!r}"
    assert Path("local-result.json").is_file()
    assert "LangSmith upload disabled" in result.stdout
    assert captured["observer"].requested is True
    assert captured["observer"].enabled is False

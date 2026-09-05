import json
from pathlib import Path

from typer.testing import CliRunner

from loveapp.cli import app


def test_memory_normalization_boundary_cli_exposes_filters() -> None:
    result = CliRunner().invoke(app, ["eval", "memory-normalization-boundary", "--help"])

    assert result.exit_code == 0
    assert "--dataset" in result.stdout
    assert "--output" in result.stdout
    assert "--markdown" in result.stdout
    assert "--case" in result.stdout
    assert "--fail-on-error" in result.stdout


def test_memory_normalization_boundary_cli_writes_local_reports(
    monkeypatch,
    tmp_path: Path,
) -> None:
    report = {
        "case_count": 1,
        "passed_case_count": 1,
        "model_calls_permitted": False,
        "store_mutation_permitted": False,
        "metrics": {
            "generic_validation_acceptance_rate": 1.0,
            "false_pre_normalization_rejection_rate": 0.0,
            "normalizer_recovery_accuracy": 1.0,
            "validation_boundary_rejection_count": 0,
        },
    }
    monkeypatch.setattr(
        "loveapp.cli.evaluate_memory_normalization_boundary",
        lambda *args, **kwargs: report,
    )
    monkeypatch.setattr(
        "loveapp.cli.render_memory_normalization_boundary_report",
        lambda _: "boundary report\n",
    )
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "boundary.json"
    markdown = tmp_path / "boundary.md"

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "memory-normalization-boundary",
            "--dataset",
            "gold.jsonl",
            "--output",
            str(output),
            "--markdown",
            str(markdown),
        ],
    )

    assert result.exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["case_count"] == 1
    assert markdown.read_text(encoding="utf-8") == "boundary report\n"

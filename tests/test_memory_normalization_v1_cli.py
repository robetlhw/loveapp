import json
from pathlib import Path

from typer.testing import CliRunner

from loveapp.cli import app


def test_memory_normalization_v1_cli_exposes_offline_filters() -> None:
    result = CliRunner().invoke(app, ["eval", "memory-normalization-v1", "--help"])

    assert result.exit_code == 0
    assert "--dataset" in result.stdout
    assert "--output" in result.stdout
    assert "--case" in result.stdout
    assert "--slice" in result.stdout
    assert "--fail-on-error" in result.stdout


def test_memory_normalization_v1_cli_writes_local_report(
    monkeypatch,
    tmp_path: Path,
) -> None:
    metrics = {
        "canonical_mapping_accuracy": 1.0,
        "state_dimension_accuracy": 1.0,
        "state_value_accuracy": 1.0,
        "custom_preservation_accuracy": 1.0,
        "unsafe_canonicalization_rate": 0.0,
        "schema_validity": 1.0,
        "idempotency_accuracy": 1.0,
        "canonical_coverage": 0.5,
    }
    report = {"case_count": 1, "passed_case_count": 1, "metrics": metrics}
    monkeypatch.setattr(
        "loveapp.cli.evaluate_memory_normalization_v1",
        lambda *args, **kwargs: report,
    )
    monkeypatch.setattr(
        "loveapp.cli.render_memory_normalization_v1_report",
        lambda _: "normalization report\n",
    )
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "baseline.json"

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "memory-normalization-v1",
            "--dataset",
            "gold.jsonl",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["case_count"] == 1
    assert output.with_suffix(".md").read_text(encoding="utf-8") == (
        "normalization report\n"
    )
    assert (tmp_path / "MEMORY_NORMALIZATION_V1_EVAL_REPORT.md").exists()


def test_memory_normalization_v1_filtered_run_does_not_clobber_full_baseline(
    monkeypatch,
    tmp_path: Path,
) -> None:
    report = {
        "case_count": 1,
        "passed_case_count": 1,
        "metrics": {
            "canonical_mapping_accuracy": 1.0,
            "state_dimension_accuracy": None,
            "state_value_accuracy": None,
            "custom_preservation_accuracy": None,
            "unsafe_canonicalization_rate": None,
            "schema_validity": 1.0,
            "idempotency_accuracy": None,
            "canonical_coverage": 1.0,
        },
    }
    monkeypatch.setattr(
        "loveapp.cli.evaluate_memory_normalization_v1",
        lambda *args, **kwargs: report,
    )
    monkeypatch.setattr(
        "loveapp.cli.render_memory_normalization_v1_report",
        lambda _: "filtered report\n",
    )
    monkeypatch.chdir(tmp_path)
    official_json = tmp_path / ".data/evals/memory_normalization_v1_baseline.json"
    official_json.parent.mkdir(parents=True)
    official_json.write_text("full baseline\n", encoding="utf-8")
    official_markdown = tmp_path / "MEMORY_NORMALIZATION_V1_EVAL_REPORT.md"
    official_markdown.write_text("full report\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["eval", "memory-normalization-v1", "--case", "NORM-001"],
    )

    assert result.exit_code == 0
    assert official_json.read_text(encoding="utf-8") == "full baseline\n"
    assert official_markdown.read_text(encoding="utf-8") == "full report\n"
    filtered = list(
        official_json.parent.glob("memory_normalization_v1_NORM-001_*.json")
    )
    assert len(filtered) == 1
    assert json.loads(filtered[0].read_text(encoding="utf-8"))["case_count"] == 1
    assert filtered[0].with_suffix(".md").read_text(encoding="utf-8") == (
        "filtered report\n"
    )

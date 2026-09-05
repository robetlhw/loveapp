import json
from pathlib import Path

from typer.testing import CliRunner

import loveapp.cli as cli


def test_memory_admission_v1_cli_exposes_offline_filters() -> None:
    result = CliRunner().invoke(cli.app, ["eval", "memory-admission-v1", "--help"])

    assert result.exit_code == 0
    assert "--dataset" in result.stdout
    assert "--output" in result.stdout
    assert "--integration-output" in result.stdout
    assert "--case" in result.stdout
    assert "--slice" in result.stdout
    assert "--contract-status" in result.stdout
    assert "--fail-on-error" in result.stdout


def test_memory_admission_v1_cli_writes_full_report_set(
    monkeypatch,
    tmp_path: Path,
) -> None:
    baseline = {
        "status": "BASELINE_PASS_POLICY_REVIEW_PENDING",
        "metrics": {
            "strict_case_count": 64,
            "strict_passed_case_count": 64,
            "decision_accuracy": 1.0,
            "reason_accuracy": 1.0,
            "score_mae": 0.0,
        },
    }
    integration = {"case_count": 12, "passed_case_count": 12}

    monkeypatch.setattr(cli, "evaluate_memory_admission_v1", lambda *args, **kwargs: baseline)

    async def fake_integration(*args, **kwargs):
        return integration

    monkeypatch.setattr(cli, "evaluate_memory_admission_integration", fake_integration)
    monkeypatch.setattr(cli, "render_memory_admission_v1_report", lambda _: "baseline\n")
    monkeypatch.setattr(cli, "render_memory_admission_policy_review", lambda _: "policy\n")
    monkeypatch.setattr(cli, "render_memory_admission_strong_review_audit", lambda _: "strong\n")
    monkeypatch.setattr(
        cli,
        "render_memory_admission_integration_diagnostic",
        lambda _: "integration\n",
    )
    monkeypatch.chdir(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    integration_path = tmp_path / "integration.json"

    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "memory-admission-v1",
            "--dataset",
            "admission.jsonl",
            "--output",
            str(baseline_path),
            "--integration-output",
            str(integration_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(baseline_path.read_text(encoding="utf-8")) == baseline
    assert baseline_path.with_suffix(".md").read_text(encoding="utf-8") == "baseline\n"
    assert json.loads(integration_path.read_text(encoding="utf-8")) == integration
    assert integration_path.with_suffix(".md").read_text(encoding="utf-8") == "integration\n"
    assert (tmp_path / "MEMORY_ADMISSION_V1_BASELINE_REPORT.md").exists()
    assert (tmp_path / "MEMORY_ADMISSION_POLICY_REVIEW.md").exists()
    assert (tmp_path / "MEMORY_ADMISSION_STRONG_REVIEW_AUDIT.md").exists()
    assert (tmp_path / "MEMORY_ADMISSION_V1_INTEGRATION_DIAGNOSTIC.md").exists()


def test_memory_admission_v1_filtered_cli_does_not_write_official_reports(
    monkeypatch,
    tmp_path: Path,
) -> None:
    report = {
        "status": "NOT_READY",
        "metrics": {
            "strict_case_count": 1,
            "strict_passed_case_count": 1,
            "decision_accuracy": 1.0,
            "reason_accuracy": 1.0,
            "score_mae": 0.0,
        },
    }
    monkeypatch.setattr(cli, "evaluate_memory_admission_v1", lambda *args, **kwargs: report)
    monkeypatch.setattr(cli, "render_memory_admission_v1_report", lambda _: "filtered\n")
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "filtered.json"

    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "memory-admission-v1",
            "--case",
            "ADM-001",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert output.exists()
    assert output.with_suffix(".md").read_text(encoding="utf-8") == "filtered\n"
    assert not (tmp_path / "MEMORY_ADMISSION_V1_BASELINE_REPORT.md").exists()
    assert not (tmp_path / "MEMORY_ADMISSION_V1_INTEGRATION_DIAGNOSTIC.md").exists()

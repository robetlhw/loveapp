import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import loveapp.cli as cli
from loveapp.domain.memory import AtomicExtraction, MemorySemanticGateReason

DATASET = Path(__file__).parents[1] / "evals" / "memory" / "gate_v2_60.jsonl"


class _LiveFixtureExtractor:
    async def extract(self, text: str, **kwargs) -> AtomicExtraction:
        del text, kwargs
        return AtomicExtraction(
            should_extract=True,
            gate_reason=MemorySemanticGateReason.STABLE_FACT,
        )

    async def aclose(self) -> None:
        return None


def test_gate_v2_cli_supports_case_filter_and_writes_json_and_markdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "_build_memory_extractor",
        lambda settings: _LiveFixtureExtractor(),
    )
    output = tmp_path / "gate-v2.json"

    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "memory-gate-v2",
            "--dataset",
            str(DATASET),
            "--case",
            "GATE-001",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["case_filter"] == "GATE-001"
    assert [case["id"] for case in report["cases"]] == ["GATE-001"]
    assert output.with_suffix(".md").exists()
    assert (tmp_path / "MEMORY_GATE_V2_EVAL_REPORT.md").exists()

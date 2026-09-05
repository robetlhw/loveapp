from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import loveapp.cli as cli
from loveapp.core.config import Settings


def _report() -> dict[str, object]:
    return {
        "version": "memory-longtail-write-v2-draft1",
        "case_count": 1,
        "passed_case_count": 1,
        "failed_case_count": 0,
        "status": "V2_STAGE_GOALS_MET",
        "dataset": {
            "status": "PASS",
            "structural_validation": {},
        },
        "retrieval_metrics": {},
        "oracle_relation_metrics": {},
        "retrieved_relation_metrics": {},
        "write_metrics": {},
        "safety_metrics": {},
        "failure_attribution": {"primary": {}, "secondary": {}},
        "rows": [],
    }


def test_longtail_write_v2_cli_help_exposes_retrieval_parameters() -> None:
    result = CliRunner().invoke(cli.app, ["eval", "memory-longtail-write-v2", "--help"])

    assert result.exit_code == 0, result.output
    for option in (
        "--dataset",
        "--shared-bank",
        "--case",
        "--slice",
        "--vector-limit",
        "--rank-limit",
        "--mode",
        "--repeat",
        "--hard-cases",
        "--compare-fixture",
        "--final-live-val",
        "--output",
        "--fail-on-error",
    ):
        assert option in result.output


def test_longtail_write_v2_cli_writes_json_and_markdown_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, object] = {}

    async def fake_run(dataset: Path, shared_bank: Path, **kwargs: object) -> dict[str, object]:
        received.update(dataset=dataset, shared_bank=shared_bank, **kwargs)
        return _report()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    monkeypatch.setattr(cli, "_run_live_memory_longtail_write_v2_eval", fake_run)
    output = tmp_path / "v2.json"

    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "memory-longtail-write-v2",
            "--dataset",
            "cases.jsonl",
            "--shared-bank",
            "shared.jsonl",
            "--case",
            "LTW2-011",
            "--vector-limit",
            "18",
            "--rank-limit",
            "4",
            "--output",
            str(output),
            "--fail-on-error",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text(encoding="utf-8"))["case_count"] == 1
    assert output.with_suffix(".md").exists()
    assert not (tmp_path / "MEMORY_LONGTAIL_WRITE_V2_RETRIEVAL_AWARE_REPORT.md").exists()
    assert received["dataset"] == Path("cases.jsonl")
    assert received["shared_bank"] == Path("shared.jsonl")
    assert received["case_id"] == "LTW2-011"
    assert received["vector_limit"] == 18
    assert received["rank_limit"] == 4
    assert received["fail_on_error"] is True


def test_longtail_write_v2_cli_passes_repeat_and_hard_case_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, object] = {}

    async def fake_run(dataset: Path, shared_bank: Path, **kwargs: object) -> dict[str, object]:
        received.update(dataset=dataset, shared_bank=shared_bank, **kwargs)
        return _report()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    monkeypatch.setattr(cli, "_run_live_memory_longtail_write_v2_eval", fake_run)
    output = tmp_path / "hard.json"

    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "memory-longtail-write-v2",
            "--output",
            str(output),
            "--repeat",
            "3",
            "--hard-cases",
        ],
    )

    assert result.exit_code == 0, result.output
    assert received["repeat"] == 3
    assert received["hard_cases"] is True


def test_longtail_write_v2_cli_rejects_hard_case_with_explicit_case() -> None:
    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "memory-longtail-write-v2",
            "--hard-cases",
            "--case",
            "LTW2-001",
        ],
    )

    assert result.exit_code != 0
    assert "cannot be combined" in result.output


def test_longtail_write_v2_cli_rejects_rank_limit_above_vector_limit() -> None:
    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "memory-longtail-write-v2",
            "--vector-limit",
            "4",
            "--rank-limit",
            "5",
        ],
    )

    assert result.exit_code != 0
    assert "rank-limit must not exceed" in result.output


@pytest.mark.asyncio
async def test_live_v2_runner_uses_process_local_llm_override_and_closes_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        llm_api_key="test-secret",
        llm_base_url="https://example.invalid/v1",
        memory_semantic_relation_provider="disabled",
        memory_semantic_relation_model="test-judge",
        embedding_model="test-embedding",
    )
    observed: dict[str, object] = {}

    class FakeEmbedding:
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    class FakeJudge:
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    embedding = FakeEmbedding()
    judge = FakeJudge()

    def fake_embedding_factory(live_settings: Settings) -> FakeEmbedding:
        observed["embedding_settings"] = live_settings
        return embedding

    def fake_judge_factory(
        live_settings: Settings,
        *,
        max_target_count: int,
    ) -> FakeJudge:
        observed["judge_settings"] = live_settings
        observed["max_target_count"] = max_target_count
        return judge

    async def fake_evaluate(*args: object, **kwargs: object) -> dict[str, object]:
        observed["evaluate_args"] = args
        observed["evaluate_kwargs"] = kwargs
        return _report()

    monkeypatch.setattr(cli, "build_embedding_provider", fake_embedding_factory)
    monkeypatch.setattr(cli, "_build_live_memory_relation_judge", fake_judge_factory)
    monkeypatch.setattr(cli, "evaluate_memory_longtail_write_v2", fake_evaluate)

    report = await cli._run_live_memory_longtail_write_v2_eval(
        Path("cases.jsonl"),
        Path("shared.jsonl"),
        settings=settings,
        case_id=None,
        slice_name="sustained_update",
        vector_limit=20,
        rank_limit=5,
        fail_on_error=True,
    )

    assert settings.memory_semantic_relation_provider == "disabled"
    assert observed["embedding_settings"].memory_semantic_relation_provider == "llm"
    assert observed["judge_settings"].memory_semantic_relation_provider == "llm"
    assert observed["max_target_count"] == 5
    assert embedding.closed is True
    assert judge.closed is True
    assert report["live_configuration"] == {
        "embedding_provider": "sentence_transformers",
        "embedding_model": "test-embedding",
        "semantic_relation_judge_model": "test-judge",
        "semantic_relation_provider_overridden_in_process": True,
        "judge_max_target_count": 5,
    }
    assert "test-secret" not in json.dumps(report)


def test_longtail_write_v2_cli_fixture_mode_uses_no_live_adapters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    async def fake_fixture(dataset: Path, shared_bank: Path, **kwargs: object) -> dict[str, object]:
        observed.update(dataset=dataset, shared_bank=shared_bank, **kwargs)
        return _report() | {
            "evaluation_mode": "shadow_fixture_v2",
            "store_mutation_permitted": False,
        }

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "evaluate_memory_longtail_write_v2_fixture", fake_fixture)
    monkeypatch.setattr(
        cli,
        "_run_live_memory_longtail_write_v2_eval",
        lambda *args, **kwargs: pytest.fail("live adapter path must not be called"),
    )
    output = tmp_path / "fixture.json"

    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "memory-longtail-write-v2",
            "--mode",
            "fixture",
            "--no-compare-fixture",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert observed["dataset"] == Path("evals/memory/longtail_write_v2_cases_draft1.jsonl")
    assert observed["hard_cases"] is False
    assert json.loads(output.read_text(encoding="utf-8"))["evaluation_mode_requested"] == "fixture"


def test_longtail_write_v2_cli_fixture_root_report_is_mode_specific(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fixture(dataset: Path, shared_bank: Path, **kwargs: object) -> dict[str, object]:
        del dataset, shared_bank, kwargs
        return _report() | {
            "evaluation_mode": "shadow_fixture_v2",
            "store_mutation_permitted": False,
        }

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "evaluate_memory_longtail_write_v2_fixture", fake_fixture)
    output = tmp_path / "fixture.json"

    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "memory-longtail-write-v2",
            "--mode",
            "fixture",
            "--no-compare-fixture",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "MEMORY_LONGTAIL_WRITE_V2_FIXTURE_REPORT.md").exists()
    assert not (tmp_path / "MEMORY_LONGTAIL_WRITE_V2_RETRIEVAL_AWARE_REPORT.md").exists()


def test_longtail_write_v2_cli_live_root_report_is_mode_specific(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_live(dataset: Path, shared_bank: Path, **kwargs: object) -> dict[str, object]:
        del dataset, shared_bank, kwargs
        return _report() | {
            "evaluation_mode": "shadow_live",
            "store_mutation_permitted": False,
        }

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    monkeypatch.setattr(cli, "_run_live_memory_longtail_write_v2_eval", fake_live)
    output = tmp_path / "live.json"

    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "memory-longtail-write-v2",
            "--no-compare-fixture",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "MEMORY_LONGTAIL_WRITE_V2_RETRIEVAL_AWARE_REPORT.md").exists()
    assert not (tmp_path / "MEMORY_LONGTAIL_WRITE_V2_FIXTURE_REPORT.md").exists()


def test_longtail_write_v2_cli_final_live_writes_full_and_hard_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = _report() | {"status": "MEMORY_V2_FREEZE_READY", "final_artifact_role": "full"}
    hard = _report() | {"status": "MEMORY_V2_FREEZE_READY", "final_artifact_role": "hard"}
    observed: dict[str, object] = {}

    async def fake_final(*args: object, **kwargs: object) -> tuple[dict, dict]:
        observed["args"] = args
        observed["kwargs"] = kwargs
        return full, hard

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    monkeypatch.setattr(cli, "_run_final_live_memory_longtail_write_v2_eval", fake_final)

    result = CliRunner().invoke(
        cli.app,
        ["eval", "memory-longtail-write-v2", "--final-live-validation"],
    )

    assert result.exit_code == 0, result.output
    output = tmp_path / ".data/evals/memory_longtail_write_v2_final_live.json"
    hard_output = tmp_path / ".data/evals/memory_longtail_write_v2_final_live_hard.json"
    assert json.loads(output.read_text(encoding="utf-8"))["final_artifact_role"] == "full"
    assert json.loads(hard_output.read_text(encoding="utf-8"))["final_artifact_role"] == "hard"
    assert output.with_suffix(".md").exists()
    assert hard_output.with_suffix(".md").exists()
    assert observed["kwargs"]["compare_fixture"] is True


def test_longtail_write_v2_cli_final_live_rejects_fixture_mode() -> None:
    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "memory-longtail-write-v2",
            "--final-live-validation",
            "--mode",
            "fixture",
        ],
    )

    assert result.exit_code != 0
    assert "requires --mode live" in result.output

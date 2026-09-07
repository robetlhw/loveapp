import json
from pathlib import Path

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

import loveapp.cli as cli
import loveapp.evaluation.router_phase32 as phase32
from loveapp.application.routing import HybridRouter
from loveapp.core.config import Settings
from loveapp.evaluation.router_phase31 import FixtureSemanticCorrector
from loveapp.evaluation.router_phase32 import (
    PHASE321_OUTPUT_FILENAMES,
    evaluate_phase321_experiment,
    write_phase321_reports,
)
from loveapp.safety import SafetyPolicy

ROOT = Path(__file__).parents[1]
DEV_PATH = ROOT / "evals" / "rag" / "phase3_5" / "loveapp_router_safety_eval_dev_v1.md"
CHALLENGE_PATH = (
    ROOT / "evals" / "rag" / "phase3_1" / "loveapp_router_challenge_dev_v1.md"
)
OLD_TEST_PATH = (
    ROOT / "evals" / "rag" / "phase3_5" / "loveapp_router_safety_eval_test_v1.md"
)


class Phase321LiveCorrector(FixtureSemanticCorrector):
    provider = "test_live_provider"
    live_llm = True

    async def correct(self, route_input, rule_result):
        correction = await super().correct(route_input, rule_result)
        self.last_telemetry.update(
            {
                "provider": self.provider,
                "live_llm": True,
                "model": "test-live-model",
                "input_tokens": 11,
                "output_tokens": 7,
                "duration_ms": 1.0,
            }
        )
        return correction


def _router(
    arm: str,
    profile: str,
    threshold: float,
    max_goals: int,
) -> HybridRouter:
    return HybridRouter(
        SafetyPolicy(),
        Phase321LiveCorrector(),
        router_v2_enabled=True,
        semantic_mode=arm,
        router_llm_correction_enabled=True,
        router_goal_secondary_threshold=threshold,
        router_goal_max_count=max_goals,
        router_conditional_trigger_profile=profile,
    )


def _goal_policy_source_report() -> dict:
    return {
        "evaluation": "phase3.2_live_llm_semantic_router",
        "dataset": str(DEV_PATH),
        "provider": "test_live_provider",
        "model": "test-live-model",
        "live_llm": True,
        "prompt_version": "routing-v3.2-before",
        "cases": [
            {
                "id": "goal-policy-source",
                "expected": {"branch": "rag", "goals": ["repair"]},
                "actual": {
                    "goals": ["repair", "communicate"],
                    "primary_goal": "repair",
                    "llm_goal_scores": {"repair": 0.9, "communicate": 0.3},
                },
                "trace": {
                    "llm_called": True,
                    "llm_route_decision": {
                        "primary_goal": "repair",
                        "goals": ["repair", "communicate"],
                        "goal_scores": {"repair": 0.9, "communicate": 0.3},
                    },
                },
            }
        ],
    }


def test_phase321_dataset_guard_rejects_old_test_before_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader_called = False

    def unexpected_loader(path):
        nonlocal loader_called
        loader_called = True
        raise AssertionError(f"must not load {path}")

    monkeypatch.setattr(cli, "validate_router_safety_dataset", unexpected_loader)

    with pytest.raises(ValueError, match="must never read"):
        cli._phase321_validate_datasets(OLD_TEST_PATH, CHALLENGE_PATH)

    assert loader_called is False


@pytest.mark.asyncio
async def test_phase321_evaluator_rejects_old_test_before_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader_called = False

    def unexpected_loader(path):
        nonlocal loader_called
        loader_called = True
        raise AssertionError(f"must not load {path}")

    monkeypatch.setattr(phase32, "_cases_for_path", unexpected_loader)

    with pytest.raises(ValueError, match="must never read"):
        await phase32.evaluate_phase321_experiment(
            OLD_TEST_PATH,
            CHALLENGE_PATH,
            router_factory=lambda *args: None,
            live_metadata={},
            goal_policy_source_report=_goal_policy_source_report(),
        )

    assert loader_called is False


def test_phase321_dataset_lint_references_dev_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dev = tmp_path / "loveapp_router_safety_eval_dev_v1.md"
    challenge = tmp_path / "loveapp_router_challenge_dev_v1.md"
    dev.write_text("dev", encoding="utf-8")
    challenge.write_text("challenge", encoding="utf-8")
    references: tuple[Path, ...] | None = None

    monkeypatch.setattr(
        cli,
        "validate_router_safety_dataset",
        lambda path: {"passed": True, "case_count": 120},
    )

    def challenge_lint(path, *, reference_paths=()):
        nonlocal references
        references = tuple(reference_paths)
        return {"passed": True, "case_count": 120}

    monkeypatch.setattr(cli, "validate_router_challenge_dataset", challenge_lint)

    resolved_dev, resolved_challenge, audit = cli._phase321_validate_datasets(
        dev,
        challenge,
    )

    assert resolved_dev == dev.resolve()
    assert resolved_challenge == challenge.resolve()
    assert references == (dev.resolve(),)
    assert OLD_TEST_PATH.resolve() not in references
    assert audit["old_router_test_loaded"] is False


def test_phase321_before_reports_are_reference_summaries_only(tmp_path: Path) -> None:
    for name, filename in cli._PHASE321_BEFORE_FILENAMES.items():
        (tmp_path / filename).write_text(
            json.dumps(
                {
                    "provider": "test_live_provider",
                    "model": "before-model",
                    "live_llm": True,
                    "arm": "conditional" if name.startswith("conditional") else "always",
                    "dataset": (
                        "loveapp_router_challenge_dev_v1.md"
                        if name.endswith("challenge_dev")
                        else "loveapp_router_safety_eval_dev_v1.md"
                    ),
                    "case_count": 120,
                    "llm_success_count": 1,
                    "cases": [{"id": "not-copied"}],
                }
            ),
            encoding="utf-8",
        )

    references = cli._phase321_load_before_references(tmp_path)

    assert references["status"] == "reused_as_read_only_before_reference"
    assert set(references["reports"]) == set(cli._PHASE321_BEFORE_FILENAMES)
    assert all("cases" not in item for item in references["reports"].values())
    assert references["new_live_calls_for_before_references"] == 0
    assert references["historical_llm_case_calls_represented"] == 0


@pytest.mark.asyncio
async def test_phase321_protocol_is_dev_first_and_writes_exact_bundle(
    tmp_path: Path,
) -> None:
    factory_calls: list[tuple[str, str, float, int]] = []

    def factory(arm: str, profile: str, threshold: float, max_goals: int):
        factory_calls.append((arm, profile, threshold, max_goals))
        return _router(arm, profile, threshold, max_goals)

    reports = await evaluate_phase321_experiment(
        DEV_PATH,
        CHALLENGE_PATH,
        router_factory=factory,
        live_metadata={
            "provider": "test_live_provider",
            "model": "test-live-model",
            "temperature": 0,
            "prompt_version": "routing-v3.2.1-test",
        },
        goal_policy_source_report=_goal_policy_source_report(),
        before_references={"status": "synthetic-before-reference"},
    )

    assert set(reports) == set(PHASE321_OUTPUT_FILENAMES)
    assert [(arm, profile) for arm, profile, *_ in factory_calls[:6]] == [
        ("always", "c2"),
        ("conditional", "c2"),
        ("always", "c2"),
        ("conditional", "c0"),
        ("conditional", "c1"),
        ("conditional", "c2"),
    ]
    assert [(arm, profile) for arm, profile, *_ in factory_calls[6:8]] == [
        ("always", "c2"),
        ("conditional", "c2"),
    ]
    assert factory_calls[8:] == [
        (
            "always",
            "c2",
            factory_calls[8][2],
            factory_calls[8][3],
        )
    ] * 3
    protocol = reports["always_dev"]["phase321_protocol"]
    assert protocol["challenge_profiles_executed"] == ["C2"]
    assert protocol["old_router_test_loaded"] is False
    assert Path(protocol["smoke"]["always"]["dataset"]).resolve() == DEV_PATH.resolve()
    assert (
        Path(protocol["smoke"]["conditional_c2"]["dataset"]).resolve()
        == DEV_PATH.resolve()
    )
    assert protocol["call_budget"]["llm_call_upper_bound"] == 808
    assert reports["repeatability"]["runs"] == 3
    assert reports["repeatability"]["sample_size"] == 24

    written = write_phase321_reports(reports, tmp_path)

    assert set(written) == set(PHASE321_OUTPUT_FILENAMES)
    assert {path.name for path in written.values()} == set(
        PHASE321_OUTPUT_FILENAMES.values()
    )


def test_phase321_cli_provider_failure_writes_no_formal_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_model="test-model",
        router_model="test-model",
        router_llm_provider="deepseek",
        llm_api_key=SecretStr("test-key"),
        llm_base_url="https://example.invalid",
        router_live_eval_enabled=True,
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(
        cli,
        "_phase321_validate_datasets",
        lambda dev, challenge: (
            DEV_PATH,
            CHALLENGE_PATH,
            {
                "dev": {"passed": True, "case_count": 120},
                "challenge_dev": {"passed": True, "case_count": 120},
                "old_router_test_loaded": False,
            },
        ),
    )
    monkeypatch.setattr(
        cli,
        "_phase321_load_before_references",
        lambda path: {"status": "not_available", "reports": {}},
    )
    monkeypatch.setattr(
        cli,
        "_phase321_load_goal_policy_source",
        lambda path: _goal_policy_source_report(),
    )

    async def fail_before_reports(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(cli, "evaluate_phase321_experiment", fail_before_reports)

    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "router-phase3-2-1",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "provider" in result.output
    assert "unavailable" in result.output
    assert not list(tmp_path.glob("router_phase3_2_1_*.json"))

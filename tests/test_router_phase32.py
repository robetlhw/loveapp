from pathlib import Path

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from loveapp.adapters.routing.openai_compatible import _build_prompt
from loveapp.application.routing import HybridRouter, route_by_rules
from loveapp.cli import app
from loveapp.core.config import Settings
from loveapp.domain.routing import RouteInput
from loveapp.evaluation.router_phase31 import FixtureSemanticCorrector
from loveapp.evaluation.router_phase32 import (
    add_conditional_diagnostics,
    build_phase32_router,
    compare_phase32_reports,
    evaluate_phase32_dataset,
    evaluate_phase32_repeatability,
    evaluate_phase32_smoke,
    normalize_phase32_arm,
    phase32_dry_run,
    render_phase32_findings,
    representative_challenge_cases,
)
from loveapp.safety import SafetyPolicy

ROOT = Path(__file__).parents[1]
DEV_PATH = ROOT / "evals" / "rag" / "phase3_5" / "loveapp_router_safety_eval_dev_v1.md"
CHALLENGE_PATH = ROOT / "evals" / "rag" / "phase3_1" / "loveapp_router_challenge_dev_v1.md"


class FakeLiveCorrector(FixtureSemanticCorrector):
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
                "duration_ms": 1.5,
            }
        )
        return correction


class FailingLiveCorrector(FixtureSemanticCorrector):
    provider = "test_live_provider"
    live_llm = True

    async def correct(self, route_input, rule_result):
        raise RuntimeError("provider unavailable")


class MissingLiveMarkerCorrector:
    """Pseudo provider intentionally missing the positive Live marker."""

    provider = "test_live_provider"

    async def correct(self, route_input, rule_result):
        raise AssertionError("must be rejected before routing")


def _fake_live_router(mode: str) -> HybridRouter:
    return HybridRouter(
        SafetyPolicy(),
        FakeLiveCorrector(),
        router_v2_enabled=True,
        semantic_mode=mode,
        router_llm_correction_enabled=True,
    )


def test_phase32_arm_normalization_and_live_guard() -> None:
    assert normalize_phase32_arm("live-always") == "always"
    assert normalize_phase32_arm("rule-only") == "rule"
    with pytest.raises(ValueError, match="Live LLM evaluation was not executed"):
        build_phase32_router("always", corrector=FixtureSemanticCorrector())


def test_phase32_live_guard_rejects_provider_without_live_marker() -> None:
    with pytest.raises(ValueError, match="Live LLM evaluation was not executed"):
        build_phase32_router("always", corrector=MissingLiveMarkerCorrector())


def test_live_router_prompt_is_bounded_to_query_history_and_compact_rule_hints() -> None:
    route_input = RouteInput(
        latest_query="她回复越来越慢是什么意思？",
        active_task="relationship_advice",
    )
    payload = _build_prompt(route_input, route_by_rules(route_input))
    assert "latest_query" in payload
    assert "recent_messages" in payload
    assert "rule_hints" in payload
    assert "runtime_context" not in payload
    assert "date_plan" not in payload
    assert "pending_task_reason" not in payload


async def test_phase32_rule_report_has_live_schema_metrics() -> None:
    report = await evaluate_phase32_dataset(DEV_PATH, arm="rule")

    assert report["live_llm"] is False
    assert report["provider"] == "none"
    assert report["case_count"] == 120
    assert report["branch_macro_f1"] == pytest.approx(0.9491)
    assert report["rag_recall"] == pytest.approx(0.9405)
    assert "router_p99_latency_ms" in report
    assert "llm_p99_latency_ms" in report
    assert "top_20_failures" in report
    assert report["prompt_sha256"]


async def test_phase32_live_report_and_comparison_diagnostics() -> None:
    rule = await evaluate_phase32_dataset(DEV_PATH, arm="rule")
    always = await evaluate_phase32_dataset(
        DEV_PATH,
        arm="always",
        router=_fake_live_router("always"),
        provider="test_live_provider",
        live_llm=True,
        model="test-live-model",
    )
    conditional = await evaluate_phase32_dataset(
        DEV_PATH,
        arm="conditional",
        router=_fake_live_router("conditional"),
        provider="test_live_provider",
        live_llm=True,
        model="test-live-model",
    )
    compared = compare_phase32_reports(rule, always, conditional)
    add_conditional_diagnostics(compared)

    assert always["live_llm"] is True
    assert always["provider"] == "test_live_provider"
    assert always["model"] == "test-live-model"
    assert always["llm_called_count"] > 0
    assert always["input_tokens"] > 0
    assert always["output_tokens"] > 0
    assert compared["always"]["comparison"]["llm_rescue_count"] >= 0
    assert compared["conditional"]["retention"]["rag_recall"] is not None
    assert "conditional_trigger_miss_count" in conditional
    assert "conditional_unnecessary_call_count" in conditional


@pytest.mark.asyncio
async def test_phase32_smoke_uses_hybrid_router_provider_and_covers_ood_and_multilabel() -> None:
    report = await evaluate_phase32_smoke(
        CHALLENGE_PATH,
        router=_fake_live_router("always"),
        arm="always",
        sample_size=8,
    )

    cases = report["cases"]
    assert report["provider"] == "test_live_provider"
    assert report["live_llm"] is True
    assert report["llm_success_count"] > 0
    assert any(row["expected"]["branch"] != "rag" for row in cases)
    assert any(len(row["expected"]["goals"]) > 1 for row in cases)


@pytest.mark.asyncio
async def test_phase32_repeatability_uses_hybrid_router_provider() -> None:
    """A wrapped live corrector must not be rejected as provider=none."""

    report = await evaluate_phase32_repeatability(
        CHALLENGE_PATH,
        router_factory=lambda arm, run: _fake_live_router(arm),
        arm="always",
        runs=2,
        sample_size=20,
    )

    assert report["provider"] == "test_live_provider"
    assert report["live_llm"] is True
    assert report["runs"] == 2
    assert report["sample_size"] == 20
    assert report["branch_agreement_rate"] == pytest.approx(1.0)
    assert report["scenario_agreement_rate"] == pytest.approx(1.0)
    assert report["goal_jaccard_agreement"] == pytest.approx(1.0)


async def test_phase32_stops_when_every_live_call_falls_back_to_rules() -> None:
    router = HybridRouter(
        SafetyPolicy(),
        FailingLiveCorrector(),
        router_v2_enabled=True,
        semantic_mode="always",
        router_llm_correction_enabled=True,
    )
    with pytest.raises(ValueError, match="Live LLM evaluation was not executed"):
        await evaluate_phase32_dataset(
            DEV_PATH,
            arm="always",
            router=router,
            provider="test_live_provider",
            live_llm=True,
            model="test-live-model",
        )


def test_phase32_conditional_recommendation_requires_all_retention_metrics() -> None:
    def report(*, call_rate: float, retention: dict[str, float] | None = None) -> dict:
        return {
            "branch_macro_f1": 0.9,
            "rag_recall": 0.9,
            "scenario_macro_f1": 0.8,
            "scenario_top2_hit": 0.8,
            "goal_micro_f1": 0.7,
            "goal_macro_f1": 0.7,
            "llm_call_rate": call_rate,
            "retention": retention or {},
            "scenario": {"per_class": {}},
            "goal": {"per_goal": {}},
            "comparison": {},
        }

    reports = {
        f"{arm}_{dataset}": report(call_rate=1.0)
        for dataset in ("dev", "challenge_dev")
        for arm in ("rule", "always")
    }
    reports["conditional_dev"] = report(
        call_rate=0.5,
        retention={"rag_recall": 0.96, "scenario_macro_f1": 0.99, "goal_micro_f1": 0.99},
    )
    reports["conditional_challenge_dev"] = report(
        call_rate=0.5,
        retention={"rag_recall": 0.96, "scenario_macro_f1": 0.99, "goal_micro_f1": 0.99},
    )
    assert "9. 当前推荐：Always-on" in render_phase32_findings(reports)
    reports["conditional_challenge_dev"]["retention"] = {
        "rag_recall": 0.98,
        "scenario_macro_f1": 0.98,
        "goal_micro_f1": 0.98,
    }
    assert "9. 当前推荐：Conditional" in render_phase32_findings(reports)
    reports["conditional_challenge_dev"]["llm_call_rate"] = 0.99
    assert "9. 当前推荐：Always-on" in render_phase32_findings(reports)


def test_phase32_cli_respects_disabled_live_gate_without_building_router(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_model="test-model",
        router_model="test-model",
        llm_api_key=SecretStr("test-key"),
        llm_base_url="https://example.invalid",
        router_live_eval_enabled=False,
    )
    monkeypatch.setattr("loveapp.cli.get_settings", lambda: settings)

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "router-phase3-2",
            "--semantic-mode",
            "always",
            "--output-dir",
            str(tmp_path),
            "--no-smoke",
            "--no-repeatability",
        ],
    )

    assert result.exit_code == 2
    assert "LOVEAPP_ROUTER_LIVE_EVAL_ENABLED=true" in result.output
    assert not list(tmp_path.glob("router_phase3_2_*.json"))


def test_phase32_repeatability_sample_and_dry_run() -> None:
    cases = representative_challenge_cases(CHALLENGE_PATH, limit=24)
    assert len(cases) == 24
    assert any(case.expected_branch != "rag" for case in cases)
    assert any(len(case.expected_goals) > 1 for case in cases)

    class Settings:
        router_provider = "disabled"
        router_model = ""
        llm_provider = "demo"
        llm_model = ""
        llm_api_key = None

    dry = phase32_dry_run(
        settings=Settings(),
        dev_dataset=DEV_PATH,
        challenge_dataset=CHALLENGE_PATH,
    )
    assert dry["api_request_sent"] is False
    assert dry["dataset"]["dev"]["case_count"] == 120
    assert dry["dataset"]["challenge_dev"]["case_count"] == 120
    assert dry["structured_output"] == "json_schema"
    assert dry["structured_schema"] is True
    assert set(dry["structured_schema_preview"]["json_schema"]["schema"]["required"]) == {
        "branch",
        "primary_scenario",
        "secondary_scenarios",
        "scenario_scores",
        "goals",
        "goal_scores",
        "confidence",
        "reasoning_summary",
    }
    assert dry["ok"] is False

    Settings.router_llm_structured_output = "json_object"
    json_object_dry = phase32_dry_run(
        settings=Settings(),
        dev_dataset=DEV_PATH,
        challenge_dataset=CHALLENGE_PATH,
    )
    assert json_object_dry["structured_output"] == "json_object"
    assert json_object_dry["structured_schema_preview"] == {"type": "json_object"}
    assert json_object_dry["structured_schema"] is True

    class RouterSpecificSettings:
        router_provider = "auto"
        router_llm_provider = "router-provider"
        router_model = "shared-router-model"
        router_llm_model = "router-specific-model"
        llm_provider = "demo"
        llm_model = "shared-model"
        llm_api_key = SecretStr("test-key")
        llm_base_url = "https://example.invalid"
        router_live_eval_enabled = False

    precedence = phase32_dry_run(
        settings=RouterSpecificSettings(),
        dev_dataset=DEV_PATH,
        challenge_dataset=CHALLENGE_PATH,
    )
    assert precedence["provider"] == "router-provider"
    assert precedence["model"] == "router-specific-model"

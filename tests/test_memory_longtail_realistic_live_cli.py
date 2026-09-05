from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import loveapp.cli as cli
from loveapp.evaluation.memory_longtail_realistic import (
    HARD_CASE_IDS,
    evaluate_memory_longtail_realistic,
)

DATASET = Path(__file__).parents[1] / "evals" / "memory" / "longtail_realistic_v1.jsonl"


class _EmbeddingProvider:
    async def embed_query(self, text: str) -> list[float]:
        return [float(len(text)), 1.0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text)), 1.0] for text in texts]


class _FailingEmbeddingProvider(_EmbeddingProvider):
    async def embed_query(self, text: str) -> list[float]:
        del text
        raise RuntimeError("embedding unavailable")


@pytest.mark.asyncio
async def test_live_embedding_observer_proves_vector_calls() -> None:
    observer = cli._ObservedEmbeddingProvider(_EmbeddingProvider())

    await observer.embed_query("query")
    await observer.embed_documents(["one", "two"])

    summary = observer.summary(model="fake-embedding", dimension=2)
    assert summary["query_call_count"] == 1
    assert summary["document_call_count"] == 1
    assert summary["document_text_count"] == 2
    assert summary["failure_count"] == 0
    assert summary["embedding_backed_retrieval_confirmed"] is True


@pytest.mark.asyncio
async def test_live_embedding_observer_exposes_fallback_risk() -> None:
    observer = cli._ObservedEmbeddingProvider(_FailingEmbeddingProvider())

    with pytest.raises(RuntimeError, match="embedding unavailable"):
        await observer.embed_query("query")

    summary = observer.summary(model="fake-embedding", dimension=2)
    assert summary["failure_count"] == 1
    assert summary["failure_types"] == {"RuntimeError": 1}
    assert summary["embedding_backed_retrieval_confirmed"] is False


def test_live_cli_rejects_candidate_limit_above_five() -> None:
    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "memory-longtail-realistic",
            "--mode",
            "live",
            "--candidate-limit",
            "6",
        ],
    )

    assert result.exit_code != 0
    assert "candidate-limit between 1 and 5" in result.output


@pytest.mark.asyncio
async def test_public_hard_case_filter_is_reported_without_dataset_rewrite() -> None:
    report = await evaluate_memory_longtail_realistic(
        DATASET,
        mode="fixture",
        hard_cases=True,
    )

    assert report["hard_cases_only"] is True
    assert report["scenario_count"] == len(HARD_CASE_IDS)
    assert {case["id"] for case in report["cases"]} == set(HARD_CASE_IDS)
    assert Path(report["dataset"]).resolve() == DATASET.resolve()


def test_hard_case_cli_writes_separate_markdown_without_overwriting_full_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_live_eval(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        return {
            "dataset": str(DATASET),
            "dataset_sha256": "fixture-sha",
            "scenario_count": len(HARD_CASE_IDS),
            "turn_count": len(HARD_CASE_IDS) * 2,
            "evaluation_mode": "shadow_live",
            "store_mutation_permitted": False,
            "methodology": "test",
            "metrics": {
                "scenario_count": len(HARD_CASE_IDS),
                "turn_count": len(HARD_CASE_IDS) * 2,
                "gate_recall": 1.0,
                "retrieval_recall_at_5": 1.0,
                "relation_accuracy": 1.0,
                "target_memory_precision": 1.0,
                "false_destructive_update_count": 0,
                "confirmed_overwrite_violation_count": 0,
                "error_attribution": {},
                "first_failing_stage": {},
            },
            "hard_case_consistency": {
                "relation_consistency_rate": 1.0,
                "target_consistency_rate": 1.0,
                "validator_consistency_rate": 1.0,
                "by_case": {},
            },
            "cases": [],
        }

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_run_live_memory_longtail_realistic_eval", fake_live_eval)
    output = tmp_path / "hard.json"

    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "memory-longtail-realistic",
            "--dataset",
            str(DATASET),
            "--mode",
            "live",
            "--hard-cases",
            "--no-compare-fixture",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert not (tmp_path / "MEMORY_LONGTAIL_REALISTIC_LIVE_EVAL_REPORT.md").exists()
    assert (tmp_path / "MEMORY_LONGTAIL_REALISTIC_LIVE_HARD_CASE_REPORT.md").exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["hard_case_ids"] == list(HARD_CASE_IDS)


def test_v2_fixture_comparison_keeps_reviewed_live_before_baseline() -> None:
    fixture = {
        "metrics": {
            "gate_recall": 0.8,
            "overall_semantic_identity_match_rate": 1.0,
            "semantic_identity_match_rate": 1.0,
        }
    }
    live = {
        "metrics": {
            "gate_recall": 0.79,
            "overall_semantic_identity_match_rate": 0.91,
            "canonical_semantic_identity_match_rate": 0.92,
            "custom_semantic_identity_match_rate": 0.90,
            "semantic_identity_match_rate": 0.9,
        }
    }

    comparison = cli._longtail_fixture_comparison(fixture, live)

    assert comparison["gate_recall"] == {
        "fixture": 0.8,
        "live_before": 0.7872,
        "live_after": 0.79,
        "live": 0.79,
    }
    assert comparison["semantic_identity_match_rate"]["live_before"] is None
    assert comparison["semantic_identity_match_rate"]["live_after"] == 0.9
    assert comparison["overall_semantic_identity_match_rate"] == {
        "fixture": 1.0,
        "live_before": None,
        "live_after": 0.91,
        "live": 0.91,
    }
    assert comparison["canonical_semantic_identity_match_rate"]["live_after"] == 0.92
    assert comparison["custom_semantic_identity_match_rate"]["live_after"] == 0.90

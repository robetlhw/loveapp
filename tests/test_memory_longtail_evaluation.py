import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from loveapp.application.memory_retrieval import HybridMemoryRetriever
from loveapp.application.memory_semantic_relations import LongTailRelationShadowEvaluator
from loveapp.cli import _default_memory_longtail_output_path, app
from loveapp.domain.memory import (
    AdmissionDecision,
    ClaimRelation,
    EvidenceExplicitness,
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    PredicateType,
    TimeKind,
)
from loveapp.domain.memory_semantic_relation import SemanticRelationProposal
from loveapp.evaluation.memory_longtail_relations import (
    REPORT_VERSION,
    FixtureSemanticRelationJudge,
    evaluate_memory_longtail_relations,
    render_longtail_baseline_report,
)

DATASET = Path("evals/memory/longtail_relations_v1.jsonl")
NOW = datetime(2026, 8, 29, 12, tzinfo=UTC)


def _cases(path: Path = DATASET) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class FailingJudge:
    async def propose_relation(self, **kwargs: Any) -> SemanticRelationProposal:
        del kwargs
        raise RuntimeError("judge unavailable")


def test_longtail_fixture_is_versioned_balanced_and_complete() -> None:
    cases = _cases()

    assert len(cases) == 42
    assert [case["id"] for case in cases] == [
        f"LT-{number:03d}" for number in range(1, 43)
    ]
    assert {
        "social_integration",
        "family_integration",
        "emotional_openness",
        "future_commitment",
        "interaction_investment",
        "boundary_change",
    } <= {case["category"] for case in cases}
    assert {case["expected"]["relation"] for case in cases} == {
        relation.value for relation in ClaimRelation
    }
    tags = {tag for case in cases for tag in case.get("tags", [])}
    assert {"mem-013", "mem-014", "mem-015"} <= tags
    assert all(case["proposal"] for case in cases)
    assert all(case["existing_memories"] for case in cases)


async def test_longtail_evaluator_runs_all_fixtures_without_mutation() -> None:
    judge = FixtureSemanticRelationJudge.from_path(DATASET)

    report = await evaluate_memory_longtail_relations(DATASET, judge=judge)

    assert report["version"] == REPORT_VERSION
    assert report["case_count"] == 42
    assert report["passed_case_count"] == 25
    assert report["failed_case_count"] == 17
    assert report["store_mutation_permitted"] is False
    assert report["metrics"]["relation_accuracy"] == pytest.approx(25 / 42, abs=0.0001)
    assert report["metrics"]["target_memory_accuracy"] == pytest.approx(
        32 / 42,
        abs=0.0001,
    )
    assert report["metrics"]["candidate_retrieval_recall_at_5"] >= 0.95
    assert report["metrics"]["false_destructive_update_count"] == 0
    assert report["metrics"]["confirmed_overwrite_violation_count"] == 0
    assert report["metrics"]["input_mutation_count"] == 0
    call_count = len(judge.calls)
    assert report["metrics"]["semantic_judge_token_usage"] == {
        "prompt_tokens": 20 * call_count,
        "completion_tokens": 10 * call_count,
        "total_tokens": 30 * call_count,
    }
    assert all(not case["input_mutated"] for case in report["cases"])
    assert all(
        case["validation"]["would_update"] is False
        or case["checks"]["would_update"]
        for case in report["cases"]
    )
    assert all(
        {record["name"] for record in case["trace"]}
        == {
            "memory_long_tail_candidate_retrieval",
            "memory_semantic_relation_proposal",
            "memory_long_tail_validator",
        }
        for case in report["cases"]
    )
    assert 35 <= call_count <= 42


async def test_longtail_evaluator_reports_layered_gate_retrieval_and_safety_metrics() -> None:
    judge = FixtureSemanticRelationJudge.from_path(DATASET)

    report = await evaluate_memory_longtail_relations(DATASET, judge=judge)
    metrics = report["metrics"]

    assert metrics["long_tail_gate_expected_positive_count"] > 0
    assert metrics["long_tail_gate_true_positive_count"] <= metrics[
        "long_tail_gate_expected_positive_count"
    ]
    assert 0 <= metrics["long_tail_gate_recall"] <= 1
    assert metrics["candidate_retrieval_expected_count"] > 0
    assert 0 <= metrics["candidate_retrieval_hit_at_1"] <= 1
    assert 0 <= metrics["candidate_retrieval_hit_at_3"] <= 1
    assert 0 <= metrics["candidate_retrieval_hit_at_5"] <= 1
    assert 0 <= metrics["target_memory_precision"] <= 1
    assert metrics["event_over_pattern_violation_count"] == 0
    assert metrics["weak_belief_overwrite_violation_count"] == 0
    assert "error_attribution" in metrics
    assert report["cases"][0]["gate"]["expectation_source"] == "derived"
    assert report["cases"][0]["resolution_status"] in {
        "semantic_relation_proposed",
        "validator_allowed_shadow",
        "validator_denied",
        "semantic_uncertain",
        "retrieval_no_candidate",
        "deterministic_fallback",
    }


async def test_longtail_baseline_report_renders_layered_sections() -> None:
    judge = FixtureSemanticRelationJudge.from_path(DATASET)
    report = await evaluate_memory_longtail_relations(DATASET, judge=judge)

    rendered = render_longtail_baseline_report(report)

    assert "## Layered Metrics" in rendered
    assert "candidate_retrieval_hit_at_1" in rendered
    assert "target_memory_precision" in rendered
    assert "event_over_pattern_violation_count" in rendered
    assert "## Error Attribution" in rendered


async def test_evaluator_supports_one_isolated_case_and_rejects_unknown_id() -> None:
    judge = FixtureSemanticRelationJudge.from_path(DATASET)

    report = await evaluate_memory_longtail_relations(
        DATASET,
        judge=judge,
        case_id="LT-001",
    )

    assert report["case_count"] == 1
    assert report["cases"][0]["id"] == "LT-001"
    assert judge.calls[0][0] == "LT-001"

    with pytest.raises(ValueError, match="unknown long-tail relation case"):
        await evaluate_memory_longtail_relations(
            DATASET,
            judge=judge,
            case_id="LT-999",
        )


async def test_retrieval_recall_at_5_survives_more_than_five_real_distractors(
    tmp_path: Path,
) -> None:
    case = json.loads(json.dumps(_cases()[1]))
    distractors = [
        ("cooking", "她最近每周都在学习做日料。"),
        ("running", "她每天早晨都会去公园跑步。"),
        ("work", "她最近换了新的工作项目。"),
        ("music", "她最近经常在家练习吉他。"),
        ("travel", "她计划秋天去云南旅行。"),
        ("reading", "她最近读了很多历史小说。"),
        ("sleep", "她这段时间通常很早睡觉。"),
    ]
    for key, summary in distractors:
        case["existing_memories"].append(
            {
                "id": f"lt002-distractor-{key}",
                "kind": "interaction_pattern",
                "subject": "partner",
                "summary": summary,
                "custom_predicate": f"longtail_distractor_{key}",
                "status": "confirmed",
                "source_message_id": f"src-lt002-distractor-{key}",
                "payload": {"object": key},
            }
        )
    dataset = tmp_path / "retrieval-stress.jsonl"
    dataset.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")

    report = await evaluate_memory_longtail_relations(
        dataset,
        judge=FixtureSemanticRelationJudge.from_path(dataset),
        retriever=HybridMemoryRetriever(),
        candidate_limit=5,
    )

    row = report["cases"][0]
    assert len(case["existing_memories"]) == 8
    assert len(row["retrieved_memory_ids"]) == 5
    assert "lt002-social-old" in row["retrieved_memory_ids"]
    assert row["checks"]["retrieval_recall_at_k"] is True
    assert report["metrics"]["candidate_retrieval_recall_at_5"] == 1.0


async def test_shadow_evaluator_does_not_mutate_caller_owned_objects() -> None:
    incoming = _candidate(
        "最近她基本不再主动跟我聊自己的烦心事了",
        custom_predicate="rarely_shares_private_emotions",
        period_start=datetime(2026, 8, 1, tzinfo=UTC),
        period_end=NOW,
    )
    target = _item(
        "emotional-openness-old",
        _candidate(
            "前两个月她经常主动跟我聊自己的烦心事",
            custom_predicate="shares_private_emotions",
            period_start=datetime(2026, 6, 1, tzinfo=UTC),
            period_end=datetime(2026, 7, 31, tzinfo=UTC),
        ),
    )

    class UpdateJudge:
        async def propose_relation(self, **kwargs: Any) -> SemanticRelationProposal:
            del kwargs
            return SemanticRelationProposal(
                relation=ClaimRelation.UPDATE,
                target_memory_ids=[target.id],
                same_semantic_dimension=True,
                confidence=0.98,
                reason=(
                    "The newer sustained pattern reverses the same emotional "
                    "openness dimension."
                ),
            )

    incoming_before = incoming.model_dump(mode="json")
    target_before = target.model_dump(mode="json")
    evaluator = LongTailRelationShadowEvaluator(UpdateJudge())

    result = await evaluator.evaluate(
        incoming=incoming,
        existing_memories=[target],
        user_id="longtail-user",
        relationship_id="partner",
        incoming_status=MemoryStatus.CONFIRMED,
        incoming_source_message_id="incoming-source",
        reference_time=NOW,
    )

    assert result.validation.would_update is True
    assert result.validation.would_supersede_memory_ids == [target.id]
    assert result.store_mutation_permitted is False
    assert incoming.model_dump(mode="json") == incoming_before
    assert target.model_dump(mode="json") == target_before
    assert target.status == MemoryStatus.CONFIRMED
    assert target.supersedes_id is None


async def test_judge_failure_fails_closed_without_mutating_fixture(tmp_path: Path) -> None:
    case = _cases()[0]
    case["expected"] = {
        "relation": "uncertain",
        "target_memory_ids": [],
        "retrieval_relevant_memory_ids": [case["existing_memories"][0]["id"]],
        "validator_pass": True,
        "validated_relation": "uncertain",
        "would_update": False,
        "would_supersede_memory_ids": [],
        "destructive_update_allowed": False,
    }
    dataset = tmp_path / "failing-judge.jsonl"
    dataset.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")

    report = await evaluate_memory_longtail_relations(dataset, judge=FailingJudge())

    assert report["passed_case_count"] == 0
    assert report["metrics"]["semantic_judge_failure_count"] == 1
    assert report["metrics"]["uncertain_rate"] == 1.0
    assert report["metrics"]["input_mutation_count"] == 0
    assert report["cases"][0]["judge_status"] == "failed"
    assert report["cases"][0]["checks"]["judge_execution"] is False
    assert report["cases"][0]["checks"]["relation"] is False
    assert "failed closed" in report["cases"][0]["proposal"]["reason"]


async def test_validated_update_to_wrong_target_is_false_destructive(
    tmp_path: Path,
) -> None:
    case = json.loads(json.dumps(_cases()[1]))
    wrong_target = dict(case["existing_memories"][0])
    wrong_target["id"] = "lt002-wrong-target"
    wrong_target["source_message_id"] = "src-lt002-wrong-target"
    case["existing_memories"].append(wrong_target)
    case["proposal"]["target_memory_ids"] = [wrong_target["id"]]
    dataset = tmp_path / "wrong-target.jsonl"
    dataset.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")

    judge = FixtureSemanticRelationJudge.from_path(dataset)
    report = await evaluate_memory_longtail_relations(dataset, judge=judge)

    row = report["cases"][0]
    assert row["validation"]["would_update"] is True
    assert row["validation"]["would_supersede_memory_ids"] == [wrong_target["id"]]
    assert row["destructive_target_mismatch"] is True
    assert row["false_destructive_update"] is True
    assert report["metrics"]["false_destructive_update_count"] == 1


def test_default_cli_output_is_timestamped_under_local_eval_directory() -> None:
    path = _default_memory_longtail_output_path(
        mode="fixture",
        now=datetime(2026, 8, 30, 9, 8, 7, tzinfo=UTC),
    )

    assert path == Path(
        ".data/evals/memory_longtail_relations_fixture_20260830_090807_000000.json"
    )


def test_cli_fixture_mode_writes_shadow_report_to_default_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = DATASET.resolve()
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "memory-longtail-relations",
            "--dataset",
            str(dataset),
            "--case",
            "LT-001",
        ],
    )

    assert result.exit_code == 0, result.output
    outputs = list((tmp_path / ".data" / "evals").glob("*.json"))
    assert len(outputs) == 1
    report = json.loads(outputs[0].read_text(encoding="utf-8"))
    assert report["semantic_judge_mode"] == "fixture"
    assert report["case_filter"] == "LT-001"
    assert report["store_mutation_permitted"] is False


def test_cli_live_mode_uses_and_closes_semantic_judge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "live.json"
    close_order: list[str] = []

    class TrackingEmbeddingProvider:
        def __init__(self) -> None:
            self.query_calls = 0
            self.document_calls = 0

        async def embed_query(self, text: str) -> list[float]:
            assert text
            self.query_calls += 1
            return [1.0, 0.0]

        async def embed_documents(self, texts: list[str]) -> list[list[float]]:
            self.document_calls += 1
            return [[1.0, 0.0] for _ in texts]

        async def aclose(self) -> None:
            close_order.append("embedding")

    class TrackingLiveJudge:
        def __init__(self) -> None:
            self.closed = False

        async def propose_relation(
            self,
            *,
            incoming: MemoryCandidate,
            candidates: list[MemoryItem],
            trace=None,
        ) -> SemanticRelationProposal:
            del incoming, trace
            return SemanticRelationProposal(
                relation=ClaimRelation.SAME,
                target_memory_ids=[candidates[0].id],
                same_semantic_dimension=True,
                confidence=0.98,
                reason="Equivalent durable social-integration pattern.",
                judge_model="fake-live-semantic-judge",
            )

        async def aclose(self) -> None:
            self.closed = True
            close_order.append("judge")

    judge = TrackingLiveJudge()
    embedding_provider = TrackingEmbeddingProvider()
    monkeypatch.setattr("loveapp.cli.get_settings", lambda: object())
    monkeypatch.setattr(
        "loveapp.cli.build_embedding_provider",
        lambda settings: embedding_provider,
    )
    monkeypatch.setattr(
        "loveapp.cli._build_live_memory_relation_judge",
        lambda settings: judge,
    )

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "memory-longtail-relations",
            "--dataset",
            str(DATASET),
            "--case",
            "LT-001",
            "--live",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["semantic_judge_mode"] == "live"
    assert report["store_mutation_permitted"] is False
    assert report["cases"][0]["proposal"]["judge_model"] == (
        "fake-live-semantic-judge"
    )
    assert embedding_provider.query_calls == 1
    assert embedding_provider.document_calls == 1
    assert judge.closed is True
    assert close_order == ["judge", "embedding"]


def _candidate(
    summary: str,
    *,
    custom_predicate: str,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
) -> MemoryCandidate:
    return MemoryCandidate(
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="partner",
        summary=summary,
        original_text=summary,
        evidence_spans=[summary],
        time_kind=TimeKind.INTERVAL,
        period_start=period_start,
        period_end=period_end,
        perspective=MemoryPerspective.USER_REPORTED,
        confidence=0.96,
        payload={},
        raw_predicate=custom_predicate,
        predicate_type=PredicateType.CUSTOM,
        custom_predicate=custom_predicate,
        explicitness=EvidenceExplicitness.EXPLICIT,
        admission_score=0.92,
        admission_decision=AdmissionDecision.CONFIRM,
    )


def _item(memory_id: str, candidate: MemoryCandidate) -> MemoryItem:
    return MemoryItem(
        **candidate.model_dump(),
        id=memory_id,
        user_id="longtail-user",
        relationship_id="partner",
        status=MemoryStatus.CONFIRMED,
        source_message_id=f"{memory_id}-source",
        created_at=NOW,
        updated_at=NOW,
        dedupe_key=f"fixture:{memory_id}",
    )

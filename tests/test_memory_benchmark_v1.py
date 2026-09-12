import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.application import MemoryService
from loveapp.cli import app
from loveapp.domain.memory import AtomicExtraction
from loveapp.evaluation.memory_benchmark_live import ReplayCapture, replay_case
from loveapp.evaluation.memory_benchmark_scoring import (
    assign_unique,
    claim_checks,
    percentile,
    score_case,
    summarize,
)
from loveapp.evaluation.memory_benchmark_v1 import (
    BenchmarkSchemaError,
    MemoryBenchmarkCase,
    evaluate_memory_benchmark_v1,
    load_memory_benchmark_v1_cases,
)

DATASET = Path("evals/memory/benchmark_v1.jsonl")


@pytest.fixture
def cases():
    return load_memory_benchmark_v1_cases(DATASET)


def test_required_coverage_and_real_user_lengths(cases):
    assert len(cases) == 100
    assert sum(t.role == "user" for case in cases for t in case.conversation) == 649
    assert all(
        sum(t.role == "user" for t in c.conversation) >= 20
        for c in cases
        if c.length_class == "long"
    )
    mixed = cases[64]
    assert mixed.expected.operation == "MIXED"
    assert {step.operation for step in mixed.expected.sub_operations if step.turn_id == "t5"} == {
        "ENRICH",
        "CREATE",
    }
    assert len(mixed.expected.stage2.claims) == 4
    assert (
        next(op for op in mixed.expected.sub_operations if op.operation == "ENRICH").target.ref
        == "c1"
    )


def test_assistant_padding_cannot_satisfy_long_length(cases):
    row = cases[80].model_dump(mode="json")
    for turn in row["conversation"][1:]:
        turn["role"] = "assistant"
    with pytest.raises(ValueError, match="USER turns"):
        MemoryBenchmarkCase.model_validate(row)


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("evidence", "evidence span"),
        ("reference", "unknown claim"),
        ("canonical", "unregistered canonical"),
        ("source", "source must"),
    ],
)
def test_contract_rejects_invalid_lineage(cases, mutation, error):
    row = cases[0].model_dump(mode="json")
    claim = row["expected"]["stage2"]["claims"][0]
    if mutation == "evidence":
        claim["evidence_spans"] = ["not in source"]
    elif mutation == "reference":
        row["expected"]["sub_operations"][0]["claim_refs"] = ["c99"]
    elif mutation == "canonical":
        claim.update(predicate_policy="canonical", predicate="fictional.canonical")
    else:
        claim["source_turn_id"] = "t99"
    with pytest.raises(ValueError, match=error):
        MemoryBenchmarkCase.model_validate(row)


def test_offline_contract_smoke_does_not_claim_model_quality():
    report = evaluate_memory_benchmark_v1(DATASET)
    assert report["mode"] == "contract_smoke"
    assert report["metrics"]["model_quality_evaluated"] is False


def test_loader_rejects_duplicate_ids(tmp_path, cases):
    path = tmp_path / "bad.jsonl"
    path.write_text((cases[0].model_dump_json() + "\n") * 2, encoding="utf-8")
    with pytest.raises(BenchmarkSchemaError, match="duplicate"):
        load_memory_benchmark_v1_cases(path, require_complete=False)


def observed(gold, source="s1", memory_id="m1"):
    return dict(
        id=memory_id,
        source_message_id=source,
        kind=gold.kind.value,
        subject=gold.subject,
        summary=gold.semantic_target,
        evidence_spans=gold.evidence_spans,
        perspective=gold.perspective,
        payload={"event_type": gold.event_type} if gold.event_type else {},
        status="confirmed",
    )


def turn(turn_id, source, before, after, *, extraction=None, batches=None, audits=None):
    return dict(
        turn_id=turn_id,
        source_message_id=source,
        gate={"should_extract": True},
        db_before=before,
        db_after=after,
        extraction=extraction or {},
        write_batches=batches or [],
        audits=audits or [],
        diagnostic={},
    )


def test_add_of_wrong_semantics_or_source_is_not_create_success(cases):
    case = cases[0]
    memory = observed(case.expected.stage2.claims[0], source="other-source")
    q = score_case(case, [turn("t1", "s1", [], [memory])])
    assert not q["operation_checks"][0]["passed"]
    memory.update(source_message_id="s1", summary="无关内容")
    q = score_case(case, [turn("t1", "s1", [], [memory])])
    assert not q["operation_checks"][0]["passed"]


def test_original_text_echo_cannot_satisfy_value_and_missing_subject_fails(cases):
    gold = cases[0].expected.stage2.claims[0].model_dump(mode="json")
    row = dict(
        kind="stable_fact",
        subject="user",
        summary="未知",
        original_text="我叫小林。",
        evidence_spans=["我叫小林。"],
        payload={"source_text": "我叫小林。"},
    )
    assert not claim_checks(gold, row)["value"]
    row["summary"] = "小林"
    row["subject"] = "partner"
    assert not claim_checks(gold, row)["subject"]
    row["subject"] = "user"
    row["evidence_spans"] = []
    assert not claim_checks(gold, row)["evidence"]


def test_matching_is_one_to_one():
    assert len(assign_unique([1, 1], [1], lambda a, b: a == b)) == 1
    assert len(assign_unique([1, 2], [2, 1], lambda a, b: a == b)) == 2


@pytest.mark.parametrize(
    "target,patch,fragment,expected",
    [
        ("m1", "钱", False, True),
        ("m2", "钱", False, False),
        ("m1", "家务", False, False),
        ("m1", "钱", True, False),
    ],
)
def test_enrichment_checks_exact_target_value_and_no_fragment(
    cases, target, patch, fragment, expected
):
    case = cases[55]
    old = observed(case.expected.stage2.claims[0])
    other = dict(old, id="m2", source_message_id="different")
    after = copy.deepcopy([old, other])
    next(row for row in after if row["id"] == target)["payload"]["cause"] = {"description": patch}
    if fragment:
        after.append(observed(case.expected.stage2.claims[1], source="s5", memory_id="m3"))
    batches = [{"batch": {"event_enrichments": [{"target_memory_id": target}]}}]
    audits = [dict(target_memory_ids=[target], source_message_id="s5", evidence=["因为钱"])]
    q = score_case(
        case,
        [
            turn("t1", "s1", [], [old]),
            turn("t5", "s5", [old, other], after, batches=batches, audits=audits),
        ],
    )
    assert q["operation_checks"][-1]["passed"] is expected


def test_db_rows_do_not_count_as_extraction(cases):
    case = cases[0]
    q = score_case(case, [turn("t1", "s1", [], [observed(case.expected.stage2.claims[0])])])
    assert q["operation_checks"][0]["passed"]
    assert not q["claim_checks"][0]["matched"]


def test_update_requires_old_closed_and_new_linked(cases):
    case = cases[2]
    old = observed(case.expected.stage2.claims[0])
    new = observed(case.expected.stage2.claims[1], source="s2", memory_id="m2")
    audit = [dict(target_memory_ids=["m1"])]
    turns = [turn("t1", "s1", [], [old]), turn("t2", "s2", [old], [old, new], audits=audit)]
    assert not score_case(case, turns)["operation_checks"][-1]["passed"]
    turns[-1]["db_after"] = [dict(old, status="superseded"), dict(new, supersedes_id="m1")]
    assert score_case(case, turns)["operation_checks"][-1]["passed"]


def test_project_cannot_pass_without_three_bound_source_events(cases):
    case = cases[70]
    events = [
        observed(gold, source=f"s{i}", memory_id=f"m{i}")
        for i, gold in enumerate(case.expected.stage2.claims, 1)
    ]
    pattern = dict(
        id="p1",
        kind="interaction_pattern",
        status="confirmed",
        state_dimension="contact_frequency",
        state_value="high",
        payload={},
    )
    turns = [turn(f"t{i}", f"s{i}", events[: i - 1], events[:i]) for i in range(1, 4)]
    turns[-1]["db_after"].append(pattern)
    assert not score_case(case, turns)["operation_checks"][0]["passed"]
    pattern["source_event_ids"] = ["m1", "m2", "m3"]
    assert score_case(case, turns)["operation_checks"][0]["passed"]


def test_percentile_uses_nearest_rank_and_attempts_are_not_trace_double_counted():
    assert percentile([10, 20, 30], 0.95) == 30
    assert percentile([], 0.95) is None
    q = dict(
        gate_checks=[],
        stage1_checks=[],
        claim_checks=[],
        operation_checks=[],
        passed=True,
        primary_failure_stage=None,
    )
    report = summarize(
        [
            dict(
                category="stable_fact",
                quality=q,
                turns=[
                    {
                        "extraction_runs": [
                            {
                                "attempts": [
                                    {
                                        "status": "completed",
                                        "duration_ms": 10,
                                        "prompt_tokens": 8,
                                        "total_tokens": 10,
                                    }
                                ]
                            }
                        ],
                        "trace": [
                            {"name": "memory_model_attempt_1", "details": {"total_tokens": 10}}
                        ],
                    }
                ],
            )
        ]
    )
    assert report["model_attempt_count"] == 1
    assert report["total_tokens"] == 10


def test_summary_separates_stage2_health_and_fallback_reasons():
    quality = dict(
        gate_checks=[],
        stage1_checks=[],
        claim_checks=[],
        operation_checks=[],
        passed=True,
        primary_failure_stage=None,
    )
    turns = [
        {
            "turn_id": "t1",
            "extractor_called": True,
            "diagnostic": {
                "stage2": {"called": True, "parse_success": True, "validation_success": True},
                "final_extractor_used": "two_stage_native",
                "fallback": {"triggered": False},
            },
        },
        {
            "turn_id": "t2",
            "extractor_called": True,
            "diagnostic": {
                "stage2": {"called": True, "parse_success": True, "validation_success": False},
                "final_extractor_used": "single_stage_fallback",
                "fallback": {"triggered": True, "reason_code": "TRACE_SCHEMA_ERROR"},
            },
        },
    ]

    report = summarize([dict(category="stable_fact", quality=quality, turns=turns)])

    assert report["stage2_called_turn_count"] == 2
    assert report["stage2_parse_success_rate"] == 1.0
    assert report["stage2_validation_success_rate"] == 0.5
    assert report["fallback_by_reason"] == {"TRACE_SCHEMA_ERROR": 1}
    assert report["trace_schema_error_count"] == 1
    assert report["model_stage2_schema_error_count"] == 0


def test_summary_reports_explicit_checkpoint_false_enrichment() -> None:
    quality = dict(
        gate_checks=[],
        stage1_checks=[],
        claim_checks=[],
        operation_checks=[],
        claim_memory_bindings={"c1": "target"},
        passed=False,
        primary_failure_stage=None,
    )
    expected = {
        "sub_operations": [
            {
                "turn_id": "t1",
                "operation": "CREATE",
                "target": {"ref": None},
                "fields": [],
            },
            {
                "turn_id": "t2",
                "operation": "ENRICH",
                "target": {"ref": "c1"},
                "fields": [{"path": "payload.cause"}],
            },
        ]
    }
    rows = [
        {
            "category": "enrichment",
            "expected": expected,
            "quality": quality,
            "turns": [
                {
                    "turn_id": "t1",
                    "write_batches": [
                        {"batch": {"event_enrichments": [{"target_memory_id": "target"}]}}
                    ],
                },
                {
                    "turn_id": "t2",
                    "write_batches": [
                        {
                            "batch": {
                                "event_enrichments": [
                                    {"target_memory_id": "target", "field": "cause"}
                                ]
                            }
                        }
                    ],
                },
            ],
        }
    ]

    report = summarize(rows)

    assert report["actual_enrichment_count"] == 2
    assert report["false_enrichment_count"] == 1
    assert report["false_enrichment_rate"] == 0.5


@pytest.mark.asyncio
async def test_capture_is_transparent_and_resets_stale_diagnostic():
    class Extractor:
        def __init__(self):
            self.last_diagnostic = {"stage1": {"called": True}}

        async def extract(self, text, **kwargs):
            assert text == "input"
            return AtomicExtraction()

    class Store:
        async def commit_memory_batch(self, **kwargs):
            return SimpleNamespace(model_dump=lambda **_: {"saved": []})

    ext = Extractor()
    container = SimpleNamespace(
        memory_service=SimpleNamespace(_extractor=ext), memory_store=Store()
    )
    capture = ReplayCapture(container)
    result = await ext.extract("input")
    assert isinstance(result, AtomicExtraction)
    assert capture.called
    capture.reset()
    assert not capture.called and not capture.diagnostic


@pytest.mark.asyncio
async def test_parallel_cases_isolate_stores_and_keep_each_turn_serial(cases, tmp_path):
    containers = []
    active = 0
    maximum = 0

    class Extractor:
        async def extract(self, text, **kwargs):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
            return AtomicExtraction()

    def factory(*args, **kwargs):
        store = InMemoryMemoryStore()
        service = MemoryService(store, Extractor())
        container = SimpleNamespace(
            memory_store=store, memory_service=service, aclose=service.aclose
        )
        containers.append(container)
        return container

    results = await asyncio.gather(
        *(
            replay_case(
                case,
                settings=None,
                embedding=None,
                output_dir=tmp_path,
                fingerprint="test",
                container_factory=factory,
            )
            for case in [cases[2], cases[4]]
        )
    )
    assert maximum == 2
    assert containers[0].memory_store is not containers[1].memory_store
    assert results[0]["scope"] != results[1]["scope"]
    assert all([row["turn_id"] for row in result["turns"]] == ["t1", "t2"] for result in results)
    assert all(result["completed"] for result in results)


def test_cli_writes_contract_report(tmp_path):
    output = tmp_path / "benchmark.json"
    result = CliRunner().invoke(app, ["eval", "memory-benchmark-v1", "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text(encoding="utf-8"))["case_count"] == 100
    assert output.with_suffix(".md").exists()

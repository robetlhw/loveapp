from pathlib import Path

from loveapp.evaluation.memory_event_enrichment_mvp import (
    evaluate_event_enrichment_mvp,
)

DATASET = Path("evals/memory/event_enrichment_mvp_v0_1.jsonl")


def test_event_enrichment_mvp_fixture_is_shadow_only_and_passes() -> None:
    report = evaluate_event_enrichment_mvp(DATASET)

    assert report["case_count"] == 32
    assert report["passed_case_count"] == 32
    assert report["store_mutation_permitted"] is False
    assert report["metrics"]["enrichment_target_accuracy"] == 1.0
    assert report["metrics"]["ambiguous_target_rejection_rate"] == 1.0
    assert report["metrics"]["false_enrichment_count"] == 0
    assert report["metrics"]["false_enrichment_rate"] == 0.0

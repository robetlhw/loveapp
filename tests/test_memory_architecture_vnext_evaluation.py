from pathlib import Path

from loveapp.evaluation.memory_architecture_vnext import (
    VNEXT_BENCHMARK_VIEW_VERSION,
    evaluate_memory_architecture_vnext,
    render_memory_architecture_vnext_report,
)

DATASET = Path("evals/memory/benchmark_v1.jsonl")


def test_vnext_benchmark_view_preserves_frozen_dataset_and_splits_responsibilities() -> None:
    report = evaluate_memory_architecture_vnext(DATASET, case_id="BM-001")

    assert report["version"] == VNEXT_BENCHMARK_VIEW_VERSION
    assert report["case_count"] == 1
    assert report["store_mutation_permitted"] is False
    assert report["ground_truth_modified"] is False
    case = report["cases"][0]
    assert case["semantic"]["stage2_claim_count"] == 1
    assert case["target"]["explicit_target_step_count"] == 0
    assert case["write_policy"]["projected_operations"] == ["CREATE_CORE"]


def test_vnext_benchmark_view_renders_contract_report() -> None:
    report = evaluate_memory_architecture_vnext(DATASET, case_id="BM-001")
    markdown = render_memory_architecture_vnext_report(report)
    assert "Semantic Understanding" in markdown
    assert "Target Resolution" in markdown
    assert "Write Policy" in markdown
    assert "Ground truth modified: `False`" in markdown

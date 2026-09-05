from pathlib import Path

from loveapp.evaluation.memory_extraction_failure_review import (
    analyze_extraction_v1_failures,
    render_extraction_v1_failure_review,
)


def test_failure_review_reproduces_baseline_attribution() -> None:
    review = analyze_extraction_v1_failures(
        Path(".data/evals/memory_extraction_v1_baseline_70.json"),
        Path(".data/evals/memory_extraction_v1_flash_diagnostic.json"),
        Path(".data/evals/memory_extraction_v1_production_cascade.json"),
    )

    assert [row["case_id"] for row in review["repair"]["hurt_cases"]] == [
        "EXT-016",
        "EXT-024",
        "EXT-049",
    ]
    assert [row["case_id"] for row in review["repair"]["helped_cases"]] == [
        "EXT-047"
    ]
    assert review["subject"]["error_count"] == 15
    assert review["spurious"]["case_count"] == 7
    assert review["spurious"]["category_case_counts"] == {
        "SUPPORTED_EXTRA_NOT_IN_GOLD": 4,
        "UNSUPPORTED_SPURIOUS": 2,
        "EVALUATION_ALIGNMENT_ARTIFACT": 1,
    }

    markdown = render_extraction_v1_failure_review(review)
    assert "CANONICAL_NORMALIZATION_COUPLING" in markdown
    assert "EVIDENCE_REPAIR" in markdown
    assert "semantic alignment one-to-one parse failure" in markdown
    assert "EXT-056" not in markdown

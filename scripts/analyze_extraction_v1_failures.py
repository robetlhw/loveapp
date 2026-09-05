from __future__ import annotations

import argparse
import json
from pathlib import Path

from loveapp.evaluation.memory_extraction_failure_review import (
    analyze_extraction_v1_failures,
    render_extraction_v1_failure_review,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Review Extraction V1 baseline failures.")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path(".data/evals/memory_extraction_v1_baseline_70.json"),
    )
    parser.add_argument(
        "--flash",
        type=Path,
        default=Path(".data/evals/memory_extraction_v1_flash_diagnostic.json"),
    )
    parser.add_argument(
        "--cascade",
        type=Path,
        default=Path(".data/evals/memory_extraction_v1_production_cascade.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("MEMORY_EXTRACTION_V1_FAILURE_REVIEW.md"),
    )
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    review = analyze_extraction_v1_failures(args.baseline, args.flash, args.cascade)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_extraction_v1_failure_review(review), encoding="utf-8")
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(review, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()

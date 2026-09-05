from __future__ import annotations

import argparse
import json
from pathlib import Path

from loveapp.evaluation.memory_extraction_remediation import (
    build_memory_extraction_remediation_review,
    render_memory_extraction_remediation_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render Extraction V1 remediation review.")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path(".data/evals/memory_extraction_v1_baseline_70.json"),
    )
    parser.add_argument(
        "--remediation",
        type=Path,
        default=Path(".data/evals/memory_extraction_v1_remediation_70.json"),
    )
    parser.add_argument(
        "--failure-review",
        type=Path,
        default=Path(".data/evals/memory_extraction_v1_failure_review.json"),
    )
    parser.add_argument(
        "--focused",
        type=Path,
        default=Path(".data/evals/memory_extraction_v1_remediation_focused_19_v2.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("MEMORY_EXTRACTION_V1_REMEDIATION_REPORT.md"),
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=Path(".data/evals/memory_extraction_v1_remediation_review.json"),
    )
    args = parser.parse_args()

    review = build_memory_extraction_remediation_review(
        args.baseline,
        args.remediation,
        args.failure_review,
        focused_path=args.focused,
    )
    args.output.write_text(
        render_memory_extraction_remediation_report(review),
        encoding="utf-8",
    )
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(review, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

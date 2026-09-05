from __future__ import annotations

import argparse
import json
from pathlib import Path

from loveapp.evaluation.memory_normalization_boundary import (
    evaluate_memory_normalization_boundary,
    evaluate_memory_normalization_v1_2,
    render_memory_normalization_boundary_report,
    render_memory_normalization_v1_2_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate the Memory Raw/Normalized validation boundary."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evals/memory/normalization_boundary_v1.jsonl"),
    )
    parser.add_argument("--case")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--v1-2-output", type=Path)
    parser.add_argument("--v1-2-markdown", type=Path)
    parser.add_argument(
        "--v1-1-report",
        type=Path,
        default=Path(".data/evals/memory_normalization_v1_1_remediation.json"),
    )
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args()

    report = evaluate_memory_normalization_boundary(
        args.dataset,
        case_id=args.case,
        fail_on_error=args.fail_on_error,
        require_complete=args.case is None,
    )
    output = args.output or Path(".data/evals/memory_normalization_boundary_v1.json")
    markdown = args.markdown or Path("MEMORY_VALIDATION_BOUNDARY_MIGRATION.md")
    output.parent.mkdir(parents=True, exist_ok=True)
    markdown.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown.write_text(render_memory_normalization_boundary_report(report), encoding="utf-8")

    v1_2 = evaluate_memory_normalization_v1_2(
        args.dataset,
        normalization_v1_1_report=args.v1_1_report,
        case_id=args.case,
        fail_on_error=args.fail_on_error,
    )
    v1_2_output = args.v1_2_output or Path(".data/evals/memory_normalization_v1_2.json")
    v1_2_markdown = args.v1_2_markdown or Path(
        "MEMORY_NORMALIZATION_V1_2_BOUNDARY_REPORT.md"
    )
    v1_2_output.parent.mkdir(parents=True, exist_ok=True)
    v1_2_markdown.parent.mkdir(parents=True, exist_ok=True)
    v1_2_output.write_text(json.dumps(v1_2, ensure_ascii=False, indent=2), encoding="utf-8")
    v1_2_markdown.write_text(
        render_memory_normalization_v1_2_report(v1_2),
        encoding="utf-8",
    )
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

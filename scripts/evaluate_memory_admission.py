from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from loveapp.evaluation.memory_admission_v1 import (
    evaluate_memory_admission_integration,
    evaluate_memory_admission_v1,
    render_memory_admission_integration_diagnostic,
    render_memory_admission_policy_review,
    render_memory_admission_strong_review_audit,
    render_memory_admission_v1_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Memory Admission V1")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evals/memory/admission_v1.jsonl"),
    )
    parser.add_argument("--case")
    parser.add_argument("--slice", dest="slice_name")
    parser.add_argument(
        "--contract-status",
        choices=("EXACT", "POLICY_REVIEW"),
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--policy-review", type=Path)
    parser.add_argument("--strong-review-audit", type=Path)
    parser.add_argument("--integration-output", type=Path)
    parser.add_argument("--integration-markdown", type=Path)
    parser.add_argument("--skip-integration", action="store_true")
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args()

    report = evaluate_memory_admission_v1(
        args.dataset,
        case_id=args.case,
        slice_name=args.slice_name,
        contract_status=args.contract_status,
        fail_on_error=args.fail_on_error,
    )
    output = args.output or Path(".data/evals/memory_admission_v1_baseline.json")
    markdown = args.markdown or Path("MEMORY_ADMISSION_V1_BASELINE_REPORT.md")
    policy_review = args.policy_review or Path("MEMORY_ADMISSION_POLICY_REVIEW.md")
    strong_review = args.strong_review_audit or Path("MEMORY_ADMISSION_STRONG_REVIEW_AUDIT.md")
    for path in (output, markdown, policy_review, strong_review):
        path.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown.write_text(render_memory_admission_v1_report(report), encoding="utf-8")
    policy_review.write_text(render_memory_admission_policy_review(report), encoding="utf-8")
    strong_review.write_text(render_memory_admission_strong_review_audit(report), encoding="utf-8")

    if (
        not args.skip_integration
        and args.case is None
        and args.slice_name is None
        and args.contract_status is None
    ):
        integration = asyncio.run(evaluate_memory_admission_integration(args.dataset))
        integration_output = args.integration_output or Path(
            ".data/evals/memory_admission_v1_integration.json"
        )
        integration_markdown = args.integration_markdown or Path(
            "MEMORY_ADMISSION_V1_INTEGRATION_DIAGNOSTIC.md"
        )
        integration_output.parent.mkdir(parents=True, exist_ok=True)
        integration_markdown.parent.mkdir(parents=True, exist_ok=True)
        integration_output.write_text(
            json.dumps(integration, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        integration_markdown.write_text(
            render_memory_admission_integration_diagnostic(integration),
            encoding="utf-8",
        )

    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

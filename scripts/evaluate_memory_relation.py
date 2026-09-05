"""Run the deterministic Memory Relation V1 baseline and diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from loveapp.evaluation.memory_relation_v1 import (
    evaluate_memory_relation_integration,
    evaluate_memory_relation_v1,
    render_memory_relation_integration_diagnostic,
    render_memory_relation_policy_review,
    render_memory_relation_v1_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Memory Relation V1")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evals/memory/relation_v1.jsonl"),
    )
    parser.add_argument("--case", help="Evaluate one case, e.g. REL-011")
    parser.add_argument("--slice", dest="slice_name")
    parser.add_argument(
        "--contract-status",
        choices=("EXACT", "POLICY_REVIEW"),
    )
    parser.add_argument("--json-out", "--output", dest="json_out", type=Path)
    parser.add_argument("--md-out", "--markdown", dest="md_out", type=Path)
    parser.add_argument("--policy-review", type=Path)
    parser.add_argument("--integration-output", type=Path)
    parser.add_argument("--integration-markdown", type=Path)
    parser.add_argument("--skip-integration", action="store_true")
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args()

    report = evaluate_memory_relation_v1(
        args.dataset,
        case_id=args.case,
        slice_name=args.slice_name,
        contract_status=args.contract_status,
        fail_on_error=args.fail_on_error,
    )
    json_out = args.json_out or Path(".data/evals/memory_relation_v1_baseline.json")
    md_out = args.md_out or Path("MEMORY_RELATION_V1_BASELINE_REPORT.md")
    policy_out = args.policy_review or Path("MEMORY_RELATION_POLICY_REVIEW.md")
    for path in (json_out, md_out, policy_out):
        path.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_out.write_text(render_memory_relation_v1_report(report), encoding="utf-8")
    policy_out.write_text(render_memory_relation_policy_review(report), encoding="utf-8")

    filtered_run = any((args.case, args.slice_name, args.contract_status))
    if not args.skip_integration and not filtered_run:
        integration = asyncio.run(evaluate_memory_relation_integration(args.dataset))
        integration_out = args.integration_output or Path(
            ".data/evals/memory_relation_v1_integration.json"
        )
        integration_md = args.integration_markdown or Path(
            "MEMORY_RELATION_V1_INTEGRATION_DIAGNOSTIC.md"
        )
        integration_out.parent.mkdir(parents=True, exist_ok=True)
        integration_md.parent.mkdir(parents=True, exist_ok=True)
        integration_out.write_text(
            json.dumps(integration, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        integration_md.write_text(
            render_memory_relation_integration_diagnostic(integration),
            encoding="utf-8",
        )

    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

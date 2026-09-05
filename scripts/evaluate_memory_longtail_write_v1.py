"""CLI for the deterministic Memory Long-tail Write V1 evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from loveapp.evaluation.memory_longtail_write_v1 import (
    evaluate_memory_longtail_write_integration,
    evaluate_memory_longtail_write_v1,
    render_memory_longtail_write_integration_diagnostic,
    render_memory_longtail_write_policy_review,
    render_memory_longtail_write_v1_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evals/memory/longtail_write_v1.jsonl"),
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path(".data/evals/memory_longtail_write_v1_baseline.json"),
    )
    parser.add_argument(
        "--md-out",
        type=Path,
        default=Path("MEMORY_LONGTAIL_WRITE_V1_BASELINE_REPORT.md"),
    )
    parser.add_argument("--integration-json-out", type=Path)
    parser.add_argument("--integration-md-out", type=Path)
    parser.add_argument("--case")
    parser.add_argument("--slice", dest="slice_name")
    parser.add_argument("--relation")
    parser.add_argument("--length-band")
    parser.add_argument("--contract-status")
    parser.add_argument(
        "--live-subset",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument("--integration", action="store_true")
    parser.add_argument("--fail-on-error", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = evaluate_memory_longtail_write_v1(
        args.dataset,
        case_id=args.case,
        slice_name=args.slice_name,
        relation=args.relation,
        length_band=args.length_band,
        contract_status=args.contract_status,
        live_subset=args.live_subset,
        fail_on_error=args.fail_on_error,
    )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.md_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.md_out.write_text(render_memory_longtail_write_v1_report(report), encoding="utf-8")
    Path("MEMORY_LONGTAIL_WRITE_V1_POLICY_REVIEW.md").write_text(
        render_memory_longtail_write_policy_review(report), encoding="utf-8"
    )

    if args.integration:
        # The integration evaluator is awaitable, matching the other memory
        # evaluation integrations.  Run it once at this CLI boundary rather
        # than attempting to JSON-serialize the coroutine object.
        integration = asyncio.run(
            evaluate_memory_longtail_write_integration(
                args.dataset,
                fail_on_error=args.fail_on_error,
            )
        )
        integration_json = args.integration_json_out or Path(
            ".data/evals/memory_longtail_write_v1_integration.json"
        )
        integration_md = args.integration_md_out or Path(
            "MEMORY_LONGTAIL_WRITE_V1_INTEGRATION_DIAGNOSTIC.md"
        )
        integration_json.parent.mkdir(parents=True, exist_ok=True)
        integration_md.parent.mkdir(parents=True, exist_ok=True)
        integration_json.write_text(
            json.dumps(integration, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        integration_md.write_text(
            render_memory_longtail_write_integration_diagnostic(integration),
            encoding="utf-8",
        )

    print(
        f"Long-tail Write V1: {report['strict_passed_case_count']}/"
        f"{report['strict_case_count']} strict cases; status={report['status']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

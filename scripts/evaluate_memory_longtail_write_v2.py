"""Run the live retrieval-aware Memory Long-tail Write V2 benchmark."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from loveapp.cli import _run_live_memory_longtail_write_v2_eval
from loveapp.core.config import get_settings
from loveapp.evaluation.memory_longtail_write_v2 import (
    compare_memory_longtail_write_v2_reports,
    evaluate_memory_longtail_write_v2_fixture,
    render_memory_longtail_write_v2_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        "--case-dataset",
        dest="dataset",
        type=Path,
        default=Path("evals/memory/longtail_write_v2_cases_draft1.jsonl"),
        help="V2 case overlay JSONL dataset.",
    )
    parser.add_argument(
        "--shared-bank",
        type=Path,
        default=Path("evals/memory/longtail_write_v2_shared_bank_draft1.jsonl"),
        help="V2 shared memory bank JSONL dataset.",
    )
    parser.add_argument("--case", help="Run one case, for example LTW2-011.")
    parser.add_argument("--slice", dest="slice_name", help="Run one semantic slice.")
    parser.add_argument(
        "--mode",
        choices=("fixture", "live"),
        default="live",
        help="Run deterministic fixture or real model adapters.",
    )
    parser.add_argument("--vector-limit", type=int, default=20)
    parser.add_argument("--rank-limit", type=int, default=5)
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Repeat each case for relation/target/validator consistency analysis.",
    )
    parser.add_argument(
        "--hard-cases",
        action="store_true",
        help="Run the fixed V2 hard-case identifier subset.",
    )
    parser.add_argument(
        "--compare-fixture",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Attach a same-scope fixture comparison in live mode.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="JSON report path; defaults to a timestamped path under .data/evals.",
    )
    parser.add_argument("--fail-on-error", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.vector_limit < 1:
        raise SystemExit("--vector-limit must be positive")
    if args.rank_limit < 1 or args.rank_limit > args.vector_limit:
        raise SystemExit("--rank-limit must be between 1 and --vector-limit")
    if args.repeat < 1 or args.repeat > 100:
        raise SystemExit("--repeat must be between 1 and 100")
    if args.hard_cases and (args.case is not None or args.slice_name is not None):
        raise SystemExit("--hard-cases cannot be combined with --case or --slice")
    effective_repeat = 3 if args.hard_cases and args.repeat == 1 else args.repeat
    if args.output is not None and args.output.suffix.casefold() == ".md":
        raise SystemExit("--output must be a JSON path; Markdown is written beside it")

    output = args.output or _default_output_path()
    if args.mode == "live":
        report = asyncio.run(
            _run_live_memory_longtail_write_v2_eval(
                args.dataset,
                args.shared_bank,
                settings=get_settings(),
                case_id=args.case,
                slice_name=args.slice_name,
                vector_limit=args.vector_limit,
                rank_limit=args.rank_limit,
                fail_on_error=args.fail_on_error,
                repeat=effective_repeat,
                hard_cases=args.hard_cases,
            )
        )
    else:
        report = asyncio.run(
            evaluate_memory_longtail_write_v2_fixture(
                args.dataset,
                args.shared_bank,
                case_id=args.case,
                slice_name=args.slice_name,
                vector_limit=args.vector_limit,
                rank_limit=args.rank_limit,
                fail_on_error=args.fail_on_error,
                repeat=effective_repeat,
                hard_cases=args.hard_cases,
            )
        )
    if args.mode == "live" and args.compare_fixture:
        fixture_report = asyncio.run(
            evaluate_memory_longtail_write_v2_fixture(
                args.dataset,
                args.shared_bank,
                case_id=args.case,
                slice_name=args.slice_name,
                vector_limit=args.vector_limit,
                rank_limit=args.rank_limit,
                fail_on_error=args.fail_on_error,
                repeat=effective_repeat,
                hard_cases=args.hard_cases,
            )
        )
        report["fixture_comparison"] = compare_memory_longtail_write_v2_reports(
            fixture_report,
            report,
        )
        report["fixture_baseline"] = {
            "evaluation_mode": fixture_report.get("evaluation_mode"),
            "case_count": fixture_report.get("case_count"),
            "passed_case_count": fixture_report.get("passed_case_count"),
            "repeat": fixture_report.get("repeat"),
        }
    report["evaluation_mode_requested"] = args.mode
    report["store_mutation_permitted"] = False
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown = render_memory_longtail_write_v2_report(report)
    markdown_output = output.with_suffix(".md")
    markdown_output.write_text(markdown, encoding="utf-8")
    # Keep the root convenience reports mode-specific.  A fixture run must
    # never overwrite the Live report (and vice versa); timestamped artifacts
    # remain the source of truth for filtered/repeated runs.
    if args.case is None and args.slice_name is None and not args.hard_cases:
        root_report = (
            "MEMORY_LONGTAIL_WRITE_V2_FIXTURE_REPORT.md"
            if args.mode == "fixture"
            else "MEMORY_LONGTAIL_WRITE_V2_RETRIEVAL_AWARE_REPORT.md"
        )
        Path(root_report).write_text(markdown, encoding="utf-8")

    print(
        f"Long-tail Write V2: {report['passed_case_count']}/{report['case_count']} cases; "
        f"status={report['status']}"
    )
    print(f"JSON: {output}")
    print(f"Markdown: {markdown_output}")
    return 0


def _default_output_path() -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    return Path(".data/evals") / f"memory_longtail_write_v2_{timestamp}.json"


if __name__ == "__main__":
    raise SystemExit(main())

"""Run the live TwoStage Memory Behavioral Anchor suite."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from loveapp.evaluation.memory_behavioral_anchor import (
    DEFAULT_BASELINE_PATH,
    evaluate_memory_behavioral_anchor,
    write_behavioral_anchor_baseline,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the live, shadow-only Memory Behavioral Anchor suite."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evals/memory/memory_behavioral_anchor_v0_1.jsonl"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/memory_behavioral_anchor_v0_1"),
    )
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--fail-on-error", action="store_true")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE_PATH)
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args()

    if args.case_ids:
        rows = [
            json.loads(line)
            for line in args.dataset.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
        selected = [row for row in rows if row.get("case_id") in set(args.case_ids)]
        if not selected:
            raise SystemExit("No requested --case IDs found")
        temporary = args.output_dir / ".selected_cases.jsonl"
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected),
            encoding="utf-8",
        )
        dataset = temporary
    else:
        dataset = args.dataset

    report = asyncio.run(
        evaluate_memory_behavioral_anchor(
            dataset,
            output_dir=args.output_dir,
            require_full_suite=not bool(args.case_ids),
            baseline_path=args.baseline,
        )
    )
    if args.update_baseline:
        if args.case_ids:
            raise SystemExit("--update-baseline requires the full 28-case suite")
        write_behavioral_anchor_baseline(report, args.baseline)
    print(
        json.dumps(
            {
                "case_count": report["case_count"],
                "passed_case_count": report["passed_case_count"],
                "metrics": report["metrics"],
                "store_mutation_permitted": report["store_mutation_permitted"],
                "output_dir": str(args.output_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.fail_on_error and report["passed_case_count"] != report["case_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

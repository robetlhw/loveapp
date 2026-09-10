"""Run the shadow-only Event Enrichment MVP resolver evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from loveapp.evaluation.memory_event_enrichment_mvp import (
    evaluate_event_enrichment_mvp,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evals/memory/event_enrichment_mvp_v0_1.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/event_enrichment_mvp_v0_1/results.json"),
    )
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args()
    report = evaluate_event_enrichment_mvp(args.dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {key: report[key] for key in ("case_count", "passed_case_count", "metrics")},
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.fail_on_error and report["passed_case_count"] != report["case_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from loveapp.evaluation.memory_normalization_freeze import (
    evaluate_memory_normalization_production_smoke,
    render_production_smoke_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the offline production-path Normalization V1 freeze smoke."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evals/memory/normalization_production_smoke_v1.jsonl"),
    )
    parser.add_argument("--case")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".data/evals/memory_normalization_production_smoke.json"),
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=Path("MEMORY_NORMALIZATION_PRODUCTION_SMOKE.md"),
    )
    args = parser.parse_args()
    report = asyncio.run(
        evaluate_memory_normalization_production_smoke(
            args.dataset,
            case_id=args.case,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.markdown.write_text(render_production_smoke_report(report), encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

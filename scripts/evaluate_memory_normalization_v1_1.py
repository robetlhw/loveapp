from __future__ import annotations

import argparse
import json
from pathlib import Path

from loveapp.evaluation.memory_normalization_v1_1 import (
    evaluate_memory_normalization_v1_1,
    migrate_memory_normalization_v1_1,
    render_memory_normalization_v1_1_migration,
    render_memory_normalization_v1_1_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Memory Normalization V1.1")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evals/memory/normalization_v1_1.jsonl"),
    )
    parser.add_argument("--case")
    parser.add_argument("--layer", choices=("N1", "N2"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--fail-on-error", action="store_true")
    parser.add_argument("--migrate-from", type=Path)
    parser.add_argument("--migration-report", type=Path)
    args = parser.parse_args()

    if args.migrate_from is not None:
        migrate_memory_normalization_v1_1(args.migrate_from, args.dataset)
        if args.migration_report is not None:
            args.migration_report.parent.mkdir(parents=True, exist_ok=True)
            args.migration_report.write_text(
                render_memory_normalization_v1_1_migration(
                    args.migrate_from,
                    args.dataset,
                ),
                encoding="utf-8",
            )
    report = evaluate_memory_normalization_v1_1(
        args.dataset,
        case_id=args.case,
        layer=args.layer,
        fail_on_error=args.fail_on_error,
    )
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(
            render_memory_normalization_v1_1_report(report),
            encoding="utf-8",
        )
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

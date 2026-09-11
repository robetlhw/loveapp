"""Run the context-aware Memory behavioral anchor in shadow mode.

The command intentionally performs no MemoryService/Store write.  By default
it runs the identical dataset twice so model drift is visible in a separate
comparison artifact.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from loveapp.evaluation.memory_context_aware_behavioral_anchor import (
    DEFAULT_BASELINE_PATH,
    DEFAULT_DATASET_PATH,
    DEFAULT_OUTPUT_DIR,
    compare_context_aware_runs,
    evaluate_context_aware_behavioral_anchor,
    write_context_aware_artifacts,
    write_context_aware_baseline,
    write_run_comparison,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE_PATH)
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--category", action="append", dest="categories")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--write-context-baseline", action="store_true")
    parser.add_argument("--fail-on-error", action="store_true")
    return parser


def _filtered_dataset(path: Path, case_ids: list[str] | None, categories: list[str] | None) -> Path:
    if not case_ids and not categories:
        return path
    wanted_ids = set(case_ids or [])
    wanted_categories = set(categories or [])
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    selected = [
        row
        for row in rows
        if (not wanted_ids or row.get("case_id") in wanted_ids)
        and (not wanted_categories or row.get("category") in wanted_categories)
    ]
    if not selected:
        raise SystemExit("No requested context-aware cases found")
    temp_dir = Path(tempfile.mkdtemp(prefix="loveapp-context-aware-"))
    selected_path = temp_dir / "selected.jsonl"
    selected_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected),
        encoding="utf-8",
    )
    return selected_path


async def _run(args: argparse.Namespace, dataset: Path, run_label: str) -> dict:
    return await evaluate_context_aware_behavioral_anchor(
        dataset,
        output_dir=None,
        baseline_path=args.baseline,
        run_label=run_label,
        require_full_suite=not (args.case_ids or args.categories),
    )


def main() -> int:
    args = build_parser().parse_args()
    if args.repeat < 2 or args.repeat > 10:
        raise SystemExit("--repeat must be between 2 and 10 for identical-run drift analysis")
    dataset = _filtered_dataset(args.dataset, args.case_ids, args.categories)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    reports: list[dict] = []
    for index in range(1, args.repeat + 1):
        reports.append(asyncio.run(_run(args, dataset, f"run{index}")))

    first = reports[0]
    write_context_aware_artifacts(first, args.output_dir)
    for index, report in enumerate(reports, start=1):
        (args.output_dir / f"run{index}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    comparison = compare_context_aware_runs(first, reports[1])
    comparison["run_count"] = len(reports)
    comparison["run_labels"] = [report.get("run_label") for report in reports]
    write_run_comparison(comparison, args.output_dir)
    (args.output_dir / "run_comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if args.write_context_baseline:
        write_context_aware_baseline(
            first,
            args.output_dir.parent.parent
            / "evals"
            / "baselines"
            / "context_aware_behavioral_anchor_v0_1.json",
        )

    print(
        json.dumps(
            {
                "case_count": first["case_count"],
                "diagnostic_case_count": first["diagnostic_case_count"],
                "passed_case_count": first["passed_case_count"],
                "metrics": first["metrics"],
                "configuration_status": first["configuration_status"],
                "store_mutation_permitted": False,
                "repeat": len(reports),
                "drift_rate": comparison["drift_rate"],
                "output_dir": str(args.output_dir),
                "generated_at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.fail_on_error and first["passed_case_count"] != first["case_count"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

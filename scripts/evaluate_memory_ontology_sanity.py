"""Run the live Ontology Sanity draft-Gold diagnostic without Store writes."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from loveapp.application.memory import NoOpMemoryExtractor
from loveapp.bootstrap import _build_memory_extractor
from loveapp.core.config import get_settings
from loveapp.evaluation.memory_ontology_sanity import (
    evaluate_memory_ontology_sanity,
    write_ontology_sanity_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate live Memory extraction and deterministic Ontology v3 normalization."
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/ontology_sanity_v0_1"),
    )
    parser.add_argument("--case")
    parser.add_argument(
        "--reference-time",
        default="2026-09-09T12:00:00+08:00",
    )
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args()

    reference_time = datetime.fromisoformat(args.reference_time)
    if reference_time.tzinfo is None:
        raise ValueError("--reference-time must include a UTC offset")
    settings = get_settings()

    async def run() -> dict:
        extractor = _build_memory_extractor(settings)
        if isinstance(extractor, NoOpMemoryExtractor):
            raise ValueError("Ontology Sanity requires the configured production extractor")
        try:
            return await evaluate_memory_ontology_sanity(
                args.dataset,
                extractor=extractor,
                reference_time=reference_time,
                case_id=args.case,
                fail_on_error=args.fail_on_error,
            )
        finally:
            close = getattr(extractor, "aclose", None)
            if callable(close):
                await close()

    report = asyncio.run(run())
    results_path, summary_path = write_ontology_sanity_artifacts(
        report,
        args.output_dir,
    )
    print(
        json.dumps(
            {
                "case_count": report["case_count"],
                "model": report["model"],
                "metrics": report["metrics"],
                "error_taxonomy": report["error_taxonomy"],
                "results": str(results_path),
                "summary": str(summary_path),
                "store_mutation_permitted": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

"""Run the frozen Ontology Sanity Gold in SingleStage/TwoStage shadow mode."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from loveapp.application.memory import NoOpMemoryExtractor
from loveapp.bootstrap import _build_memory_extractor
from loveapp.core.config import get_settings
from loveapp.evaluation.memory_ontology_shadow import (
    evaluate_memory_ontology_shadow,
    write_ontology_shadow_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compare SingleStage and opt-in TwoStage Memory extraction "
            "without Store writes."
        )
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/ontology_sanity_shadow_v1"),
    )
    parser.add_argument("--case")
    parser.add_argument("--reference-time", default="2026-09-09T12:00:00+08:00")
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args()

    reference_time = datetime.fromisoformat(args.reference_time)
    if reference_time.tzinfo is None:
        raise ValueError("--reference-time must include a UTC offset")
    settings = get_settings()
    single_settings = settings.model_copy(update={"memory_extraction_mode": "single_stage"})
    two_settings = settings.model_copy(update={"memory_extraction_mode": "two_stage"})

    async def run() -> dict:
        single = _build_memory_extractor(single_settings)
        two_stage = _build_memory_extractor(two_settings)
        if isinstance(single, NoOpMemoryExtractor) or isinstance(two_stage, NoOpMemoryExtractor):
            raise ValueError("Ontology shadow evaluation requires a configured LLM extractor")
        try:
            return await evaluate_memory_ontology_shadow(
                args.dataset,
                single_stage_extractor=single,
                two_stage_extractor=two_stage,
                reference_time=reference_time,
                case_id=args.case,
                fail_on_error=args.fail_on_error,
            )
        finally:
            for extractor in (single, two_stage):
                close = getattr(extractor, "aclose", None)
                if callable(close):
                    await close()

    report = asyncio.run(run())
    results_path, summary_path = write_ontology_shadow_artifacts(report, args.output_dir)
    print(
        json.dumps(
            {
                "case_count": report["single_stage"]["case_count"],
                "metrics": report["comparison"]["metrics"],
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

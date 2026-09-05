from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from loveapp.adapters.observability.langsmith import (
    create_langsmith_client,
    load_langsmith_config,
)

DEFAULT_DATASET_NAME = "loveapp-memory-extraction-v1-70"
FLASH_EXPERIMENT_PREFIX = "loveapp-memory-extraction-v1-flash-diagnostic"
CASCADE_EXPERIMENT_PREFIX = "loveapp-memory-extraction-v1-production-cascade"


def langsmith_enabled() -> bool:
    config = load_langsmith_config()
    return config.configured and config.tracing


def langsmith_configured() -> bool:
    return load_langsmith_config().configured


class LangSmithExtractionObserver:
    """Create reference-linked case runs for the two Extraction V1 experiments."""

    def __init__(
        self,
        *,
        enabled: bool,
        dataset_name: str = DEFAULT_DATASET_NAME,
        client: Any | None = None,
        run_suffix: str | None = None,
    ) -> None:
        self.requested = enabled
        self.client = client or (create_langsmith_client() if enabled else None)
        self.enabled = bool(enabled and self.client is not None)
        self.disabled_reason = "missing_api_key" if enabled and not self.enabled else None
        self.dataset_name = dataset_name
        self.run_suffix = run_suffix or datetime.now().astimezone().strftime(
            "%Y%m%d-%H%M%S-%f"
        )
        self.experiments = {
            "flash_diagnostic": f"{FLASH_EXPERIMENT_PREFIX}-{self.run_suffix}",
            "production_cascade": f"{CASCADE_EXPERIMENT_PREFIX}-{self.run_suffix}",
        }

    @contextmanager
    def case(
        self,
        stage: str,
        *,
        inputs: Mapping[str, Any],
        metadata: Mapping[str, Any],
    ) -> Iterator[dict[str, Any]]:
        outputs: dict[str, Any] = {}
        if not self.enabled:
            yield outputs
            return
        from langsmith.run_helpers import trace, tracing_context

        case_id = str(inputs["case_id"])
        with tracing_context(
            enabled=True,
            project_name=self.experiments[stage],
            metadata=dict(metadata),
            client=self.client,
        ), trace(
            "memory_extraction_v1_case",
            inputs=dict(inputs),
            project_name=self.experiments[stage],
            metadata=dict(metadata),
            client=self.client,
            reference_example_id=uuid5(
                NAMESPACE_URL,
                f"{self.dataset_name}:{case_id}",
            ),
        ) as run:
            try:
                yield outputs
            finally:
                run.end(outputs=outputs)


def sync_memory_extraction_dataset(
    path: Path,
    *,
    dataset_name: str = DEFAULT_DATASET_NAME,
    client: Any | None = None,
) -> dict[str, Any]:
    """Idempotently upsert the local golden JSONL into a LangSmith dataset."""

    if client is None:
        client = create_langsmith_client()
        if client is None:
            raise ValueError("LANGSMITH_API_KEY is required for dataset sync")

    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    datasets = list(client.list_datasets(dataset_name=dataset_name, limit=1))
    dataset = datasets[0] if datasets else client.create_dataset(
        dataset_name,
        description="LoveApp synthetic Memory Extraction V1 70-case golden set",
        metadata={"source": path.name, "schema_version": "extraction-v1.0"},
    )
    examples = []
    for case in cases:
        case_id = case["case_id"]
        examples.append(
            {
                "id": str(uuid5(NAMESPACE_URL, f"{dataset_name}:{case_id}")),
                "inputs": {
                    "case_id": case_id,
                    "user_message": case["user_message"],
                    "reference_time": case["reference_time"],
                    "conversation_history": case["conversation_history"],
                    "pending_memory_context": case["pending_memory_context"],
                    "existing_memories": case["existing_memories"],
                },
                "outputs": {
                    "expected_claims": case["expected_claims"],
                    "expected_discarded_spans": case["expected_discarded_spans"],
                },
                "metadata": {
                    "slice": case["slice"],
                    "difficulty": case["difficulty"],
                    "length_class": case["length_class"],
                    "contains_distractor": case["contains_distractor"],
                    "distractor_types": case["distractor_types"],
                },
            }
        )
    client.create_examples(dataset_id=dataset.id, examples=examples)
    flush = getattr(client, "flush", None)
    if callable(flush):
        flush()
    return {
        "dataset_name": dataset_name,
        "dataset_id": str(dataset.id),
        "example_count": len(examples),
        "source_of_truth": str(path),
    }

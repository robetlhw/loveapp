from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from loveapp.adapters.observability.langsmith import (
    LangSmithTraceRecorder,
    load_langsmith_config,
)
from loveapp.evaluation.memory_extraction_langsmith import (
    DEFAULT_DATASET_NAME,
    LangSmithExtractionObserver,
    langsmith_enabled,
    sync_memory_extraction_dataset,
)
from loveapp.ports.observability import TraceRecorder

DATASET = Path("evals/memory/extraction_v1_70.jsonl")


class _FakeLangSmithClient:
    def __init__(self) -> None:
        self.dataset = SimpleNamespace(id="dataset-1")
        self.created_dataset_count = 0
        self.batches: list[list[dict[str, Any]]] = []
        self.flush_count = 0

    def list_datasets(self, **_: Any) -> list[Any]:
        return [self.dataset]

    def create_dataset(self, *_: Any, **__: Any) -> Any:
        self.created_dataset_count += 1
        return self.dataset

    def create_examples(self, *, examples: list[dict[str, Any]], **_: Any) -> None:
        self.batches.append(examples)

    def flush(self) -> None:
        self.flush_count += 1


def test_langsmith_disabled_does_not_import_or_send(monkeypatch: Any) -> None:
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    recorder = LangSmithTraceRecorder(enabled=True)

    trace_recorder: TraceRecorder = recorder
    assert recorder.enabled is False
    context: AbstractContextManager[dict[str, object]] = trace_recorder.measure("memory_model")
    with context as details:
        details["claim_count"] = 1


def test_langsmith_enabled_requires_flag_and_key(monkeypatch: Any) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-only")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    assert langsmith_enabled() is True

    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    assert langsmith_enabled() is False


def test_langsmith_config_reads_dotenv_without_export(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    for name in (
        "LANGSMITH_API_KEY",
        "LANGSMITH_TRACING",
        "LANGSMITH_PROJECT",
        "LANGSMITH_WORKSPACE_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "LANGSMITH_API_KEY=test-only",
                "LANGSMITH_TRACING=true",
                "LANGSMITH_PROJECT=extraction-evals",
                "LANGSMITH_WORKSPACE_ID=workspace-test",
            )
        ),
        encoding="utf-8",
    )

    config = load_langsmith_config(env_file)

    assert config.configured is True
    assert config.api_key is not None
    assert config.api_key.get_secret_value() == "test-only"
    assert config.tracing is True
    assert config.project_name == "extraction-evals"
    assert config.workspace_id == "workspace-test"


def test_langsmith_observer_fails_soft_without_key(monkeypatch: Any) -> None:
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.setattr(
        "loveapp.evaluation.memory_extraction_langsmith.create_langsmith_client",
        lambda: None,
    )

    observer = LangSmithExtractionObserver(enabled=True)

    assert observer.requested is True
    assert observer.enabled is False
    assert observer.disabled_reason == "missing_api_key"


def test_langsmith_recorder_maps_details_to_span(monkeypatch: Any) -> None:
    run = SimpleNamespace(metadata={}, outputs=None)
    run.end = lambda *, outputs: setattr(run, "outputs", outputs)
    trace_arguments: list[dict[str, Any]] = []

    @contextmanager
    def fake_context(**_: Any):
        yield

    @contextmanager
    def fake_trace(*_: Any, **kwargs: Any):
        trace_arguments.append(kwargs)
        yield run

    monkeypatch.setattr("langsmith.run_helpers.tracing_context", fake_context)
    monkeypatch.setattr("langsmith.run_helpers.trace", fake_trace)
    recorder = LangSmithTraceRecorder(enabled=True, client=object())

    with recorder.measure("memory_model_attempt_1") as details:
        details["model"] = "flash-test"
        details["claim_count"] = 2

    assert run.metadata["model"] == "flash-test"
    assert run.outputs == {
        "telemetry": {"model": "flash-test", "claim_count": 2}
    }
    assert trace_arguments[0]["run_type"] == "llm"


def test_dataset_sync_reuses_dataset_and_stable_example_ids() -> None:
    client = _FakeLangSmithClient()

    first = sync_memory_extraction_dataset(DATASET, client=client)
    second = sync_memory_extraction_dataset(DATASET, client=client)

    assert first["dataset_name"] == DEFAULT_DATASET_NAME
    assert first["example_count"] == 70
    assert second["example_count"] == 70
    assert client.created_dataset_count == 0
    assert client.flush_count == 2
    assert len(client.batches) == 2
    assert [row["id"] for row in client.batches[0]] == [
        row["id"] for row in client.batches[1]
    ]
    assert client.batches[0][0]["inputs"]["case_id"] == "EXT-001"
    assert "expected_claims" in client.batches[0][0]["outputs"]
    assert client.batches[0][0]["metadata"]["slice"] == "stable_preference"

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from pydantic import SecretStr

from loveapp.ports.observability import TraceDetails


@dataclass(frozen=True)
class LangSmithConfig:
    api_key: SecretStr | None
    tracing: bool
    project_name: str
    workspace_id: str | None

    @property
    def configured(self) -> bool:
        return self.api_key is not None


def load_langsmith_config(env_file: Path = Path(".env")) -> LangSmithConfig:
    values = dotenv_values(env_file) if env_file.is_file() else {}

    def read(name: str) -> str | None:
        process_value = os.getenv(name)
        if process_value is not None:
            return process_value.strip() or None
        file_value = values.get(name)
        return str(file_value).strip() or None if file_value is not None else None

    tracing = (read("LANGSMITH_TRACING") or "").casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
    api_key = read("LANGSMITH_API_KEY")
    return LangSmithConfig(
        api_key=SecretStr(api_key) if api_key else None,
        tracing=tracing,
        project_name=read("LANGSMITH_PROJECT") or "loveapp-memory-evals",
        workspace_id=read("LANGSMITH_WORKSPACE_ID"),
    )


def create_langsmith_client(config: LangSmithConfig | None = None) -> Any | None:
    active = config or load_langsmith_config()
    if active.api_key is None:
        return None
    from langsmith import Client

    return Client(
        api_key=active.api_key.get_secret_value(),
        workspace_id=active.workspace_id,
    )


class LangSmithTraceRecorder:
    """Map LoveApp's small TraceRecorder contract to optional LangSmith spans."""

    def __init__(
        self,
        *,
        enabled: bool,
        project_name: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        client: Any | None = None,
    ) -> None:
        config = load_langsmith_config()
        self._client = client or (create_langsmith_client(config) if enabled else None)
        self.enabled = bool(enabled and self._client is not None)
        self.project_name = project_name or config.project_name
        self.metadata = dict(metadata or {})

    @contextmanager
    def measure(self, name: str) -> Iterator[TraceDetails]:
        details: TraceDetails = {}
        if not self.enabled:
            yield details
            return

        from langsmith.run_helpers import trace, tracing_context

        with tracing_context(
            enabled=True,
            project_name=self.project_name,
            metadata=self.metadata,
            client=self._client,
        ), trace(
            name,
            run_type=_run_type(name),
            inputs={},
            project_name=self.project_name,
            metadata=self.metadata,
            client=self._client,
        ) as run:
            try:
                yield details
            except BaseException:
                if details:
                    run.metadata.update(_safe_metadata(details))
                raise
            else:
                safe = _safe_metadata(details)
                run.metadata.update(safe)
                run.end(outputs={"telemetry": safe})


def _run_type(name: str) -> str:
    return (
        "llm"
        if any(marker in name for marker in ("model", "flash_raw", "semantic_alignment"))
        else "chain"
    )


def _safe_metadata(values: Mapping[str, object]) -> dict[str, str | int | float | bool | None]:
    return {
        str(key): value
        for key, value in values.items()
        if value is None or isinstance(value, (str, int, float, bool))
    }

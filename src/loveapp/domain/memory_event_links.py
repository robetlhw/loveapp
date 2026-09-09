"""Typed Event-State link helpers for atomic memory batches."""

from loveapp.domain.memory import MemoryCandidate
from loveapp.domain.memory_write import MemoryWriteOperation


def resolve_operation_source_event_ids(
    operation: MemoryWriteOperation,
    saved_memory_ids: list[str],
    *,
    operation_index: int,
) -> list[str]:
    """Resolve typed Event links after operation IDs are allocated."""

    resolved: list[str] = []
    for source_index in operation.source_event_operation_indexes:
        if source_index < 0 or source_index >= len(saved_memory_ids):
            raise ValueError("memory batch source event operation index is out of range")
        if source_index == operation_index:
            raise ValueError("memory batch operation cannot link itself as an Event")
        resolved.append(saved_memory_ids[source_index])
    return list(dict.fromkeys(resolved))


def attach_source_event_ids(
    candidate: MemoryCandidate,
    event_ids: list[str],
) -> MemoryCandidate:
    """Return a candidate with typed Event links mirrored into its payload."""

    merged = list(dict.fromkeys([*candidate.source_event_ids, *event_ids]))
    if not merged:
        return candidate
    payload = dict(candidate.payload)
    payload["source_event_ids"] = merged
    return candidate.model_copy(
        update={
            "source_event_ids": merged,
            "payload": payload,
        }
    )


__all__ = ["attach_source_event_ids", "resolve_operation_source_event_ids"]

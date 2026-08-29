"""Observability helpers around the production Memory pipeline.

The inspector deliberately delegates all extraction and governance decisions to
``MemoryService``.  It only captures trace records and persisted state around a
normal ``remember_text`` call so debug tooling can explain the resulting write.
The production call may persist Memory; this module does not make write decisions.
"""

from __future__ import annotations

import json
from typing import Any

from loveapp.application.memory import MemoryService
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.memory import (
    MemoryExtractionRun,
    MemoryItem,
    MemoryStatus,
    RememberResult,
    StoredMessage,
)
from loveapp.domain.memory_write import MemoryTransitionAudit
from loveapp.ports.memory import MemoryStore

_ACTIVE_STATUSES = {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
_GOVERNANCE_TRACE = "memory_candidate_governance"
_MODEL_TRACE_PREFIX = "memory_model"


class MemoryInspector:
    """Aggregate Memory execution evidence for one relationship test scope."""

    def __init__(
        self,
        memory_service: MemoryService,
        memory_store: MemoryStore,
        *,
        user_id: str,
        relationship_id: str,
        conversation_id: str,
        requested_status: MemoryStatus = MemoryStatus.CONFIRMED,
        limit: int = 200,
    ) -> None:
        if not user_id.strip():
            raise ValueError("user_id must not be empty")
        if not relationship_id.strip():
            raise ValueError("relationship_id must not be empty")
        if not conversation_id.strip():
            raise ValueError("conversation_id must not be empty")
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")

        self.memory_service = memory_service
        self.memory_store = memory_store
        self.user_id = user_id
        self.relationship_id = relationship_id
        self.conversation_id = conversation_id
        self.requested_status = requested_status
        self.limit = limit
        self._turn = 0

    async def execute_turn(
        self,
        text: str,
        *,
        requested_status: MemoryStatus | None = None,
    ) -> dict[str, Any]:
        """Run one input through ``MemoryService`` and return an observation report."""

        if not text.strip():
            raise ValueError("text must not be empty")

        before_items = await self._snapshot_items()
        trace = ExecutionTrace()
        result = await self.memory_service.remember_text(
            user_id=self.user_id,
            relationship_id=self.relationship_id,
            conversation_id=self.conversation_id,
            text=text,
            status=requested_status or self.requested_status,
            trace=trace,
        )
        after_items = await self._snapshot_items()
        audits = await self.memory_store.list_transition_audits(
            user_id=self.user_id,
            relationship_id=self.relationship_id,
            source_message_id=result.message.id,
            limit=500,
        )
        extraction_run = await self._extraction_run(result.extraction_run_id)
        records = trace.snapshot()
        candidates = _governance_candidates(records)
        memory_index = {item.id: item for item in [*before_items, *after_items]}
        _attach_candidate_memories(candidates, memory_index)
        contextual_update = _trace_payload(records, "memory_contextual_update")
        explicit_correction = _trace_payload(records, "memory_explicit_correction")
        operations = _planned_operations(candidates)
        if (
            contextual_update is not None
            and contextual_update.get("selected_target_memory_id")
            and contextual_update.get("contextual_update_type")
        ):
            operations.append(
                {
                    "candidate_index": None,
                    "action": "contextual_update",
                    "update_type": contextual_update["contextual_update_type"],
                    "target_memory_ids": [
                        contextual_update["selected_target_memory_id"]
                    ],
                    "rule": "contextual_memory_update",
                    "reason": contextual_update.get("reason"),
                }
            )
        before = [
            _memory_record(item) for item in before_items if item.status in _ACTIVE_STATUSES
        ]
        after = [
            _memory_record(item) for item in after_items if item.status in _ACTIVE_STATUSES
        ]

        self._turn += 1
        return {
            "turn": self._turn,
            "input": text,
            "scope": {
                "user_id": self.user_id,
                "relationship_id": self.relationship_id,
                "conversation_id": self.conversation_id,
                "source_message_id": result.message.id,
                "extraction_run_id": result.extraction_run_id,
            },
            "gate": (
                result.gate_decision.model_dump(mode="json")
                if result.gate_decision is not None
                else {}
            ),
            "contextual_update": contextual_update,
            "explicit_correction": explicit_correction,
            "model_outputs": _model_outputs(records),
            "before": before,
            "candidates": candidates,
            "operations": operations,
            "after": after,
            "diff": _memory_diff(before_items, after_items, result, candidates),
            "extraction_run": (
                _extraction_run_record(extraction_run) if extraction_run is not None else {}
            ),
            "audits": [_audit_record(audit) for audit in audits],
            "extraction_error": result.extraction_error,
            "result": {
                "saved_count": len(result.saved),
                "rejected_by_policy": result.rejected_by_policy,
                "skipped_low_confidence": result.skipped_low_confidence,
                "discarded_spans": [
                    item.model_dump(mode="json") for item in result.discarded_spans
                ],
                "pending": result.pending,
            },
        }

    async def observe_turn(
        self,
        text: str,
        *,
        requested_status: MemoryStatus | None = None,
    ) -> dict[str, Any]:
        """Compatibility alias for callers that describe a turn as observation."""

        return await self.execute_turn(text, requested_status=requested_status)

    async def list_memories(
        self,
        *,
        include_all: bool = False,
        include_inactive: bool | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return active memories by default, or complete lifecycle history."""

        if include_inactive is not None:
            include_all = include_inactive
        items = await self._snapshot_items(limit=limit)
        if not include_all:
            items = [item for item in items if item.status in _ACTIVE_STATUSES]
        return [_memory_record(item) for item in items]

    async def get_memory(self, memory_id: str) -> dict[str, Any] | None:
        """Return a detailed memory only when it belongs to this relationship scope."""

        item = await self.memory_store.get_memory(memory_id, self.user_id)
        if item is None or item.relationship_id != self.relationship_id:
            return None
        return _memory_record(item)

    async def get_context(self, *, query: str | None = None) -> dict[str, Any]:
        """Return the actual context projection consumed by application code."""

        context = await self.memory_service.get_context(
            self.user_id,
            self.relationship_id,
            query=query,
        )
        return context.model_dump(mode="json")

    async def get_history(self) -> list[dict[str, Any]]:
        """Return conversation history through the production service boundary."""

        messages = await self.memory_service.get_conversation_history(
            self.user_id,
            self.relationship_id,
            self.conversation_id,
        )
        return [_message_record(message) for message in messages]

    async def list_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return extraction run and attempt telemetry for this conversation."""

        runs = await self.memory_store.list_extraction_runs(
            user_id=self.user_id,
            relationship_id=self.relationship_id,
            conversation_id=self.conversation_id,
            limit=limit,
        )
        return [_extraction_run_record(run) for run in runs]

    async def list_audits(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return transition audit history for this relationship scope."""

        audits = await self.memory_store.list_transition_audits(
            user_id=self.user_id,
            relationship_id=self.relationship_id,
            limit=limit,
        )
        return [_audit_record(audit) for audit in audits]

    async def reset(self) -> dict[str, Any]:
        """Clear only this user and relationship through the Store's scoped API."""

        await self.memory_store.reset_relationship_scope(
            user_id=self.user_id,
            relationship_id=self.relationship_id,
        )
        self._turn = 0
        return {
            "reset": True,
            "user_id": self.user_id,
            "relationship_id": self.relationship_id,
        }

    async def _snapshot_items(self, *, limit: int | None = None) -> list[MemoryItem]:
        return await self.memory_store.list_memories(
            user_id=self.user_id,
            relationship_id=self.relationship_id,
            limit=limit or self.limit,
            read_only=True,
        )

    async def _extraction_run(self, run_id: str | None) -> MemoryExtractionRun | None:
        if run_id is None:
            return None
        runs = await self.memory_store.list_extraction_runs(
            user_id=self.user_id,
            relationship_id=self.relationship_id,
            conversation_id=self.conversation_id,
            limit=50,
        )
        return next((run for run in runs if run.id == run_id), None)


def _memory_record(item: MemoryItem) -> dict[str, Any]:
    """Serialize without dropping IDs, provenance, lifecycle, or relation fields."""

    return item.model_dump(mode="json")


def _message_record(message: StoredMessage) -> dict[str, Any]:
    return message.model_dump(mode="json")


def _extraction_run_record(run: MemoryExtractionRun) -> dict[str, Any]:
    return run.model_dump(mode="json")


def _audit_record(audit: MemoryTransitionAudit) -> dict[str, Any]:
    return audit.model_dump(mode="json")


def _model_outputs(records: list[Any]) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for record in records:
        if not record.name.startswith(_MODEL_TRACE_PREFIX):
            continue
        details = record.details
        tier = str(details.get("tier") or "")
        if not tier:
            tier = "strong" if "strong" in record.name else "flash"
        outputs.append(
            {
                "trace_name": record.name,
                "tier": tier,
                "model": details.get("model"),
                "status": record.status.value,
                "duration_ms": round(record.duration_ms, 2),
                "repair_status": details.get("repair_status"),
                "failure_category": details.get("failure_category"),
                "raw_claims": _load_json(
                    details.get("claims_json"),
                    _load_json(details.get("claim_predicates_json"), []),
                ),
            }
        )
    return outputs


def _governance_candidates(records: list[Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    json_fields = {
        "score_breakdown_json": ("score_breakdown", {}),
        "compared_memory_ids_json": ("compared_memory_ids", []),
        "strong_compared_memory_ids_json": ("strong_compared_memory_ids", []),
        "relation_target_memory_ids_json": ("relation_target_memory_ids", []),
        "planned_target_memory_ids_json": ("planned_target_memory_ids", []),
        "target_operation_indexes_json": ("target_operation_indexes", []),
        "planned_actions_json": ("planned_actions", []),
        "evidence_spans_json": ("evidence_spans", []),
        "payload_json": ("payload", {}),
    }
    for record in records:
        if record.name != _GOVERNANCE_TRACE:
            continue
        candidate = dict(record.details)
        for source, (target, fallback) in json_fields.items():
            candidate[target] = _load_json(candidate.pop(source, None), fallback)
        candidate["trace_duration_ms"] = round(record.duration_ms, 4)
        candidates.append(candidate)
    return sorted(candidates, key=lambda item: int(item.get("candidate_index", 0)))


def _trace_payload(records: list[Any], trace_name: str) -> dict[str, Any] | None:
    record = next((item for item in records if item.name == trace_name), None)
    if record is None:
        return None
    details = dict(record.details)
    for field in (
        "antecedent_candidate_ids_json",
        "semantic_candidate_ids_json",
        "compatible_candidate_ids_json",
        "rejected_candidates_json",
    ):
        if field in details:
            details[field.removesuffix("_json")] = _load_json(details.pop(field), [])
    details["trace_status"] = record.status.value
    details["trace_duration_ms"] = round(record.duration_ms, 4)
    return details


def _attach_candidate_memories(
    candidates: list[dict[str, Any]],
    memory_index: dict[str, MemoryItem],
) -> None:
    fields = {
        "compared_memory_ids": "compared_memories",
        "relation_target_memory_ids": "relation_target_memories",
        "strong_compared_memory_ids": "strong_compared_memories",
        "planned_target_memory_ids": "planned_target_memories",
    }
    for candidate in candidates:
        for id_field, target_field in fields.items():
            candidate[target_field] = [
                _memory_record(memory_index[memory_id])
                if memory_id in memory_index
                else {"id": memory_id, "missing_from_snapshot": True}
                for memory_id in candidate.get(id_field, [])
            ]


def _planned_operations(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    for candidate in candidates:
        planned = candidate.get("planned_actions")
        if not isinstance(planned, list):
            continue
        for action in planned:
            if not isinstance(action, dict):
                continue
            operations.append(
                {
                    "candidate_index": candidate.get("candidate_index"),
                    "summary": candidate.get("summary"),
                    "relation": candidate.get("claim_relation"),
                    "rule_name": candidate.get("relation_rule"),
                    "reason": candidate.get("relation_reason"),
                    **action,
                }
            )
    return operations


def _memory_diff(
    before: list[MemoryItem],
    after: list[MemoryItem],
    result: RememberResult,
    candidates: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    before_by_id = {item.id: item for item in before}
    after_by_id = {item.id: item for item in after}
    created_ids = {saved.item.id for saved in result.saved if saved.created}
    merged_ids = {saved.item.id for saved in result.saved if not saved.created}
    contextual_ids = set(result.contextual_updated_memory_ids)

    added = [
        _memory_record(item)
        for item in after
        if item.id not in before_by_id or item.id in created_ids
    ]
    merged = [
        _changed_memory(before_by_id.get(memory_id), after_by_id[memory_id])
        for memory_id in merged_ids
        if memory_id in after_by_id
    ]
    updated = [
        _changed_memory(before_by_id.get(memory_id), after_by_id[memory_id])
        for memory_id in contextual_ids
        if memory_id in after_by_id
    ]
    status_changed: list[dict[str, Any]] = []
    superseded: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []
    for memory_id, previous in before_by_id.items():
        current = after_by_id.get(memory_id)
        if current is None or current.status == previous.status:
            continue
        change = _status_change(previous, current)
        status_changed.append(change)
        if current.status == MemoryStatus.SUPERSEDED:
            superseded.append(change)
        elif current.status == MemoryStatus.EXPIRED:
            expired.append(change)

    rejected = [
        {
            "candidate_index": candidate.get("candidate_index"),
            "summary": candidate.get("summary"),
            "admission_decision": candidate.get("admission_decision"),
            "admission_reason": candidate.get("admission_reason"),
            "planned_action": candidate.get("planned_action"),
        }
        for candidate in candidates
        if candidate.get("admission_decision") == "reject"
        or candidate.get("planned_action") in {"reject", "skip_low_confidence"}
    ]
    return {
        "added": _unique_by_memory_id(added),
        "merged": _unique_by_nested_memory_id(merged),
        "updated": _unique_by_nested_memory_id(updated),
        "superseded": superseded,
        "expired": expired,
        "status_changed": status_changed,
        "rejected": rejected,
    }


def _changed_memory(previous: MemoryItem | None, current: MemoryItem) -> dict[str, Any]:
    old_evidence = set(previous.evidence_spans) if previous is not None else set()
    return {
        "memory": _memory_record(current),
        "new_evidence": [value for value in current.evidence_spans if value not in old_evidence],
    }


def _status_change(previous: MemoryItem, current: MemoryItem) -> dict[str, Any]:
    return {
        "memory": _memory_record(current),
        "from_status": previous.status.value,
        "to_status": current.status.value,
    }


def _unique_by_memory_id(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list({str(item["id"]): item for item in items}.values())


def _unique_by_nested_memory_id(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list({str(item["memory"]["id"]): item for item in items}.values())


def _load_json(value: object, fallback: Any) -> Any:
    if not isinstance(value, str) or not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback

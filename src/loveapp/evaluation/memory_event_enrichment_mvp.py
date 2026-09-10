"""Shadow evaluation for the deterministic Event Enrichment MVP resolver."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loveapp.application.event_enrichment import resolve_event_enrichment
from loveapp.domain.memory import (
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    MessageRole,
    StoredMessage,
)
from loveapp.domain.memory_semantic_units import AttributeNamespace, EnrichmentDraft

REFERENCE_TIME = datetime(2026, 9, 10, 12, tzinfo=UTC)


def evaluate_event_enrichment_mvp(path: Path) -> dict[str, Any]:
    cases = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    rows = [_evaluate_case(case) for case in cases]
    expected_rejections = [row for row in rows if row["expected"] != "resolved"]
    false_enrichments = [row for row in expected_rejections if row["actual"] == "resolved"]
    ambiguous = [
        row
        for row in rows
        if row["expected"] == "ambiguous_semantic_event_antecedent"
    ]
    resolved = [row for row in rows if row["expected"] == "resolved"]
    return {
        "evaluation": "event_enrichment_mvp_v0_1",
        "mode": "semantic_draft_fixture_shadow",
        "store_mutation_permitted": False,
        "case_count": len(rows),
        "passed_case_count": sum(row["passed"] for row in rows),
        "metrics": {
            "enrichment_target_accuracy": _ratio(
                sum(row["passed"] for row in resolved), len(resolved)
            ),
            "ambiguous_target_rejection_rate": _ratio(
                sum(row["passed"] for row in ambiguous), len(ambiguous)
            ),
            "false_enrichment_count": len(false_enrichments),
            "false_enrichment_rate": _ratio(
                len(false_enrichments), len(expected_rejections)
            ),
            "canonical_field_pass_count": sum(
                row["passed"] and row["category"] == "canonical" for row in rows
            ),
            "custom_attribute_pass_count": sum(
                row["passed"] and row["category"] == "custom" for row in rows
            ),
        },
        "cases": rows,
    }


def _evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    source = StoredMessage(
        id="source",
        conversation_id="conversation",
        user_id="user",
        relationship_id="relationship",
        role=MessageRole.USER,
        content="昨天我们发生了一次互动",
        created_at=REFERENCE_TIME,
    )
    memories = [
        _memory(case, index=index)
        for index in range(int(case.get("memory_count", 1)))
    ]
    draft = EnrichmentDraft(
        unit_id=case["case_id"],
        target_kind=MemoryKind.INTERACTION_EVENT,
        target_semantic_hint={
            "event_type": case.get("draft_event_type", case["event_type"]),
            **(
                {"subject": case["draft_subject"]}
                if case.get("draft_subject")
                else {}
            ),
        },
        attribute_namespace=AttributeNamespace(case.get("namespace", "canonical")),
        attribute_name=case["field"],
        value=case["value"],
        evidence_span=case.get("evidence_span", case["text"]),
        confidence=float(case.get("confidence", 0.95)),
    )
    resolution = resolve_event_enrichment(
        draft,
        current_text=case["text"],
        conversation_history=[source],
        existing_memories=memories,
        user_id="user",
        relationship_id="relationship",
    )
    actual = "resolved" if resolution.resolved else resolution.reason
    return {
        "case_id": case["case_id"],
        "category": case["category"],
        "expected": case["expected"],
        "actual": actual,
        "passed": actual == case["expected"],
        "semantic_candidate_ids": list(resolution.semantic_candidate_ids),
        "compatible_candidate_ids": list(resolution.compatible_candidate_ids),
        "selected_target_memory_id": (
            resolution.target.id if resolution.target is not None else None
        ),
    }


def _memory(case: dict[str, Any], *, index: int) -> MemoryItem:
    event_type = case["event_type"]
    return MemoryItem(
        id=f"target-{index + 1}",
        user_id="user",
        relationship_id=case.get("memory_relationship_id", "relationship"),
        source_message_id="source",
        status=MemoryStatus(case.get("status", "confirmed")),
        kind=MemoryKind(case.get("memory_kind", "interaction_event")),
        subject=case.get("target_subject", "relationship"),
        summary="一次互动事件",
        original_text="昨天我们发生了一次互动",
        evidence_spans=["昨天我们发生了一次互动"],
        payload={
            "event_type": event_type,
            "action": "吵架" if event_type == "conflict" else "一起活动",
            **case.get("existing", {}),
        },
        created_at=REFERENCE_TIME,
        updated_at=REFERENCE_TIME,
        dedupe_key=f"target-{index + 1}",
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None

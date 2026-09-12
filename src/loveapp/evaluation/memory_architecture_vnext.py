"""Contract-level benchmark migration view for Memory Architecture VNext.

The original benchmark remains the source of truth. This module only projects
its existing Stage 1, Stage 2, target, and operation checkpoints into the
three architectural responsibilities described by VNext; it never rewrites
golden expectations or executes Store mutations.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from loveapp.evaluation.memory_benchmark_v1 import (
    MemoryBenchmarkCase,
    load_memory_benchmark_v1_cases,
)

VNEXT_BENCHMARK_VIEW_VERSION = "memory-architecture-vnext-view-v1"


@dataclass(frozen=True)
class SemanticUnderstandingView:
    stage1_should_extract: bool
    stage1_unit_count: int
    stage2_claim_count: int
    semantic_type_counts: dict[str, int]
    event_detail_candidate_count: int


@dataclass(frozen=True)
class TargetResolutionView:
    target_step_count: int
    explicit_target_step_count: int
    ambiguous_or_sequence_step_count: int
    target_refs: tuple[str, ...]


@dataclass(frozen=True)
class WritePolicyView:
    operation: str
    operation_step_count: int
    projected_operations: tuple[str, ...]
    requires_clarification_review: bool


@dataclass(frozen=True)
class MemoryArchitectureVNextCaseView:
    case_id: str
    category: str
    semantic: SemanticUnderstandingView
    target: TargetResolutionView
    write_policy: WritePolicyView
    source_operation: str


def project_memory_benchmark_case(case: MemoryBenchmarkCase) -> MemoryArchitectureVNextCaseView:
    """Project one frozen V1 case into the VNext evaluation responsibilities."""

    claims = list(case.expected.stage2.claims)
    semantic_type_counts = dict(Counter(claim.semantic_type for claim in claims))
    event_detail_candidates = sum(
        1
        for claim in claims
        if claim.semantic_type == "enrichment"
        or claim.attribute is not None
    )
    steps = list(case.expected.sub_operations)
    target_refs = tuple(
        ref
        for step in steps
        for ref in ([step.target.ref] if step.target.ref else []) + list(step.target.refs)
    )
    explicit_steps = sum(
        1
        for step in steps
        if step.target.ref is not None or bool(step.target.refs)
    )
    sequence_steps = sum(1 for step in steps if step.target.selector == "event_sequence")
    projected = tuple(_project_operation(step.operation) for step in steps)
    requires_clarification = any(
        step.target.selector == "event_sequence" or case.review_reason is not None
        for step in steps
    )
    return MemoryArchitectureVNextCaseView(
        case_id=case.id,
        category=case.category,
        semantic=SemanticUnderstandingView(
            stage1_should_extract=case.expected.stage1.should_extract,
            stage1_unit_count=len(case.expected.stage1.semantic_units),
            stage2_claim_count=len(claims),
            semantic_type_counts=semantic_type_counts,
            event_detail_candidate_count=event_detail_candidates,
        ),
        target=TargetResolutionView(
            target_step_count=len(steps),
            explicit_target_step_count=explicit_steps,
            ambiguous_or_sequence_step_count=sequence_steps,
            target_refs=target_refs,
        ),
        write_policy=WritePolicyView(
            operation=case.expected.operation,
            operation_step_count=len(steps),
            projected_operations=projected,
            requires_clarification_review=requires_clarification,
        ),
        source_operation=case.expected.operation,
    )


def evaluate_memory_architecture_vnext(
    path: Path,
    *,
    case_id: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Return a non-mutating three-layer view over the frozen benchmark."""

    cases = load_memory_benchmark_v1_cases(path, require_complete=False, profile=profile)
    if case_id is not None:
        cases = [case for case in cases if case.id == case_id]
        if not cases:
            raise ValueError(f"unknown benchmark case: {case_id}")
    views = [project_memory_benchmark_case(case) for case in cases]
    semantic = [view.semantic for view in views]
    target = [view.target for view in views]
    writes = [view.write_policy for view in views]
    stage1_positive = sum(item.stage1_should_extract for item in semantic)
    stage2_claims = sum(item.stage2_claim_count for item in semantic)
    target_steps = sum(item.target_step_count for item in target)
    explicit_targets = sum(item.explicit_target_step_count for item in target)
    detail_candidates = sum(item.event_detail_candidate_count for item in semantic)
    projected_operations = Counter(
        operation for item in writes for operation in item.projected_operations
    )
    return {
        "version": VNEXT_BENCHMARK_VIEW_VERSION,
        "source_dataset": str(path),
        "case_count": len(views),
        "cases": [_view_to_dict(view) for view in views],
        "architecture": _architecture_metadata(),
        "metrics": {
            "semantic_understanding": {
                "stage1_positive_case_count": stage1_positive,
                "stage1_positive_rate": _ratio(stage1_positive, len(semantic)),
                "stage1_unit_count": sum(item.stage1_unit_count for item in semantic),
                "stage2_claim_count": stage2_claims,
                "event_detail_candidate_count": detail_candidates,
            },
            "target_resolution": {
                "target_step_count": target_steps,
                "explicit_target_step_count": explicit_targets,
                "explicit_target_rate": _ratio(explicit_targets, target_steps),
                "sequence_or_ambiguity_step_count": sum(
                    item.ambiguous_or_sequence_step_count for item in target
                ),
            },
            "write_policy": {
                "projected_operation_counts": dict(projected_operations),
                "clarification_review_case_count": sum(
                    item.requires_clarification_review for item in writes
                ),
            },
        },
        "store_mutation_permitted": False,
        "ground_truth_modified": False,
    }


def render_memory_architecture_vnext_report(report: dict[str, Any]) -> str:
    """Render the contract view without claiming live model performance."""

    metrics = report["metrics"]
    semantic = metrics["semantic_understanding"]
    target = metrics["target_resolution"]
    writes = metrics["write_policy"]
    architecture = report.get("architecture", _architecture_metadata())
    lines = [
        "# Memory Architecture VNext Contract View",
        "",
        f"- Source dataset: `{report['source_dataset']}`",
        f"- Cases: {report['case_count']}",
        "- Store mutation permitted: `False`",
        "- Ground truth modified: `False`",
        "",
        "## Architecture Changes",
        "",
        *[f"- {item}" for item in architecture["architecture_changes"]],
        "",
        "## Files Changed",
        "",
        *[f"- `{item}`" for item in architecture["files_changed"]],
        "",
        "## Schema Changes",
        "",
        *[f"- {item}" for item in architecture["schema_changes"]],
        "",
        "## Stage 1",
        "",
        *[f"- {item}" for item in architecture["stage1_changes"]],
        "",
        "## Stage 2",
        "",
        *[f"- {item}" for item in architecture["stage2_changes"]],
        "",
        "## PendingQuestion / Conversation Binding",
        "",
        *[f"- {item}" for item in architecture["pending_binding"]],
        "",
        "## Semantic Understanding",
        "",
        f"- Stage1 positive cases: {semantic['stage1_positive_case_count']} "
        f"({semantic['stage1_positive_rate']:.3f})",
        f"- Stage1 semantic units: {semantic['stage1_unit_count']}",
        f"- Stage2 claims: {semantic['stage2_claim_count']}",
        f"- Event-detail candidates: {semantic['event_detail_candidate_count']}",
        "",
        "## Target Resolution",
        "",
        f"- Target steps: {target['target_step_count']}",
        f"- Explicit target steps: {target['explicit_target_step_count']} "
        f"({target['explicit_target_rate']:.3f})",
        f"- Sequence/ambiguity steps: {target['sequence_or_ambiguity_step_count']}",
        "",
        "## Write Policy",
        "",
        "- Projected operations: `"
        f"{json.dumps(writes['projected_operation_counts'], ensure_ascii=False)}`",
        f"- Cases needing clarification review: {writes['clarification_review_case_count']}",
        "",
        "## Candidate Generator",
        "",
        *[f"- {item}" for item in architecture["candidate_generator"]],
        "",
        "## Resolver",
        "",
        *[f"- {item}" for item in architecture["resolver"]],
        "",
        "## WritePolicy",
        "",
        *[f"- {item}" for item in architecture["write_policy"]],
        "",
        "## EventDetail",
        "",
        *[f"- {item}" for item in architecture["event_detail"]],
        "",
        "## Benchmark Migration",
        "",
        *[f"- {item}" for item in architecture["benchmark_migration"]],
        "",
        "## Before / After Metrics",
        "",
        "- This report is a frozen contract projection; no model execution or Store "
        "mutation was performed.",
        "- Before/after production accuracy is therefore `NOT_APPLICABLE` here; use "
        "the live benchmark evaluators for model metrics.",
        "",
        "## False Enrichment Cases",
        "",
        *[f"- {item}" for item in architecture["false_enrichment"]],
        "",
        "## Remaining Failures",
        "",
        "- No runtime failures are inferred by this contract-only projection.",
        "- Any fixture expectation drift remains owned by the existing benchmark report.",
        "",
        "## Known Limitations",
        "",
        *[f"- {item}" for item in architecture["known_limitations"]],
        "",
        "## Tests",
        "",
        *[f"- {item}" for item in architecture["tests"]],
        "",
        "## Git Diff Summary",
        "",
        "- This view does not inspect or mutate Git state; commit details belong to "
        "the change report.",
        "",
        "This is a frozen-dataset contract projection, not a model accuracy score.",
    ]
    return "\n".join(lines) + "\n"


def _architecture_metadata() -> dict[str, list[str]]:
    """Describe the bounded VNext contract in the generated audit report."""

    return {
        "architecture_changes": [
            "Stage 1/Stage 2 emit semantic drafts only.",
            "Candidate generation, target resolution, and write policy are separate "
            "application steps.",
            "Ambiguous targets produce CLARIFY; unresolved targets produce NOOP.",
            "Store mutation is limited to authorized batch operations.",
        ],
        "files_changed": [
            "src/loveapp/domain/memory_architecture_vnext.py",
            "src/loveapp/domain/memory_semantic_units.py",
            "src/loveapp/domain/runtime_context.py",
            "src/loveapp/application/memory_target_resolution.py",
            "src/loveapp/application/memory_write_policy.py",
            "src/loveapp/application/memory.py",
            "src/loveapp/adapters/memory/two_stage.py",
            "src/loveapp/adapters/memory/in_memory.py",
            "src/loveapp/adapters/memory/sqlite.py",
            "src/loveapp/domain/memory_write.py",
        ],
        "schema_changes": [
            "Added EventDetailDraft, Candidate, TargetResolution, WriteDecision, and "
            "ClarificationRequired contracts.",
            "Added an auxiliary EventDetail persistence path; no fifth Core Memory "
            "kind was introduced.",
            "Added bounded PendingQuestion conversation-binding metadata hidden from "
            "model context.",
        ],
        "stage1_changes": [
            "Preserves proposition-level semantic roles, context dependency, "
            "occurrence grouping, and answered question IDs.",
            "Does not emit target IDs or write operations.",
        ],
        "stage2_changes": [
            "Accepts EventDetail semantic units with raw evidence and event-type constraints.",
            "Does not emit target IDs, mutation actions, or database patches.",
        ],
        "pending_binding": [
            "Carries question, assistant message, optional application target "
            "binding, field, event type, and bounded expiry.",
            "Binding is validated by Python resolver code and is never exposed as "
            "write authority to extraction.",
        ],
        "candidate_generator": [
            "Uses pending binding, explicit temporal/reference cues, recent context, "
            "structured lookup, and vector fallback.",
            "Retains candidate provenance and does not treat one retrieval hit as "
            "proof of uniqueness.",
        ],
        "resolver": [
            "Returns RESOLVED, AMBIGUOUS, or UNRESOLVED only.",
            "Fails closed for incomplete candidate coverage, ambiguity, expired "
            "targets, and incompatibility.",
        ],
        "write_policy": [
            "Maps resolved core fields to typed ENRICH_CORE and soft/non-core fields "
            "to ATTACH_DETAIL.",
            "Maps ambiguity to CLARIFY and unresolved/unsupported cases to NOOP.",
        ],
        "event_detail": [
            "Stores raw evidence, source message/proposition provenance, parent event "
            "ID, and bounded status.",
            "EventDetail is not included in ordinary Core Memory retrieval.",
        ],
        "benchmark_migration": [
            "Projects the frozen benchmark into Semantic Understanding, Target "
            "Resolution, and Write Policy views.",
            "Original benchmark data and golden expectations are unchanged.",
        ],
        "false_enrichment": [
            "Multiple same-type events remain ambiguous before field compatibility is applied.",
            "Subject mismatch, incompatible fields, expired targets, and vector-only "
            "recall fail closed.",
        ],
        "known_limitations": [
            "Multi-target mutation is not implemented.",
            "Clarification is currently represented as an audit/trace result; UX "
            "follow-up is outside this change.",
            "EventDetail is not a general Memory Graph or time-series store.",
        ],
        "tests": [
            "Contract tests cover semantic boundaries and write authority rejection.",
            "Candidate, policy, Store, and MemoryService integration tests cover "
            "attach, enrich, ambiguity, and no-op paths.",
            "The frozen benchmark view is evaluated without Store mutation.",
        ],
    }


def write_memory_architecture_vnext_report(
    report: dict[str, Any],
    output_path: Path,
) -> Path:
    """Persist the non-mutating contract view for review artifacts."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_memory_architecture_vnext_report(report), encoding="utf-8")
    return output_path


def _project_operation(operation: str) -> str:
    return {
        "CREATE": "CREATE_CORE",
        "ENRICH": "ENRICH_CORE",
        "REFINE": "NOOP",
        "UPDATE": "ENRICH_CORE",
        "MERGE": "ENRICH_CORE",
        "PROJECT": "NOOP",
        "PRESERVE": "NOOP",
        "NOOP": "NOOP",
    }.get(operation, "NOOP")


def _view_to_dict(view: MemoryArchitectureVNextCaseView) -> dict[str, Any]:
    value = asdict(view)
    value["target"]["target_refs"] = list(value["target"]["target_refs"])
    value["write_policy"]["projected_operations"] = list(
        value["write_policy"]["projected_operations"]
    )
    return value


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


__all__ = [
    "VNEXT_BENCHMARK_VIEW_VERSION",
    "MemoryArchitectureVNextCaseView",
    "evaluate_memory_architecture_vnext",
    "project_memory_benchmark_case",
    "render_memory_architecture_vnext_report",
    "write_memory_architecture_vnext_report",
]

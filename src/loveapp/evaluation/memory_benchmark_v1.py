"""Golden checkpoints and contract-only diagnostics for Memory Benchmark V1."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from loveapp.application.memory_admission import assess_memory_admission
from loveapp.domain.memory import EvidenceExplicitness, MemoryCandidate, MemoryKind, SemanticRole
from loveapp.domain.memory_lifecycle import normalize_memory_candidate
from loveapp.domain.memory_predicates import CANONICAL_PREDICATES

BENCHMARK_VERSION = "memory-benchmark-v1"
REFERENCE_TIME = datetime(2026, 9, 12, 12, tzinfo=UTC)
EXPECTED_CATEGORY_COUNTS = dict(
    stable_fact=20, preference=15, event=20, enrichment=15, pattern=10, state=10, long_tail=10
)
EXPECTED_LENGTH_COUNTS = dict(short=40, medium=40, long=20)
LENGTH_RANGES = {"short": (1, 3), "medium": (5, 10), "long": (20, 30)}
EXPANSION_LENGTH_RANGES = {"short": (1, 4), "medium": (3, 10), "long": (6, 30)}
BenchmarkProfile = Literal["original", "expansion", "merged"]
Operation = Literal["CREATE", "ENRICH", "REFINE", "UPDATE", "MERGE", "PROJECT", "PRESERVE", "NOOP"]


class BenchmarkSchemaError(ValueError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationTurn(StrictModel):
    turn_id: str = Field(pattern=r"^t[1-9]\d*$")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class SemanticUnitExpectation(StrictModel):
    turn_id: str
    evidence_span: str = Field(min_length=1)
    semantic_role: SemanticRole


class Stage1Expectation(StrictModel):
    should_extract: bool
    semantic_units: list[SemanticUnitExpectation] = Field(default_factory=list)
    negative_turn_ids: list[str] = Field(default_factory=list)


class Stage2ClaimExpectation(StrictModel):
    claim_id: str = Field(pattern=r"^c[1-9]\d*$")
    source_turn_id: str
    semantic_type: Literal["new_memory", "enrichment", "refinement"] = "new_memory"
    kind: MemoryKind
    subject: str
    predicate: str
    predicate_policy: Literal["canonical", "semantic", "custom"] = "semantic"
    event_type: str | None = None
    perspective: str | None = None
    attribute: str | None = None
    semantic_target: str
    evidence_spans: list[str] = Field(min_length=1)
    # All groups must match, alternatives within a group are OR. Anchors are
    # authored before live execution and do not include the echoed raw input.
    value_groups: list[list[str]] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_semantics(self) -> Stage2ClaimExpectation:
        if self.predicate_policy == "canonical" and self.predicate not in CANONICAL_PREDICATES:
            raise ValueError(f"unregistered canonical predicate: {self.predicate}")
        if any(not group or any(not item.strip() for item in group) for group in self.value_groups):
            raise ValueError("value groups require nonempty alternatives")
        if self.semantic_type == "enrichment" and not self.attribute:
            raise ValueError("enrichment requires an attribute")
        return self


class Stage2Expectation(StrictModel):
    claims: list[Stage2ClaimExpectation] = Field(default_factory=list)


class TargetExpectation(StrictModel):
    selector: Literal["none", "unique_matching", "event_sequence"] = "none"
    ref: str | None = None
    refs: list[str] = Field(default_factory=list)


class FieldExpectation(StrictModel):
    path: str
    alternatives: list[str] = Field(min_length=1)


class OperationStep(StrictModel):
    turn_id: str
    operation: Operation
    target: TargetExpectation = Field(default_factory=TargetExpectation)
    claim_refs: list[str] = Field(default_factory=list)
    fields: list[FieldExpectation] = Field(default_factory=list)
    output_kind: MemoryKind | None = None
    output_dimension: str | None = None
    output_value: str | None = None
    minimum_evidence: int = Field(default=0, ge=0)
    forbidden_kind: MemoryKind | None = None

    @model_validator(mode="after")
    def validate_target(self) -> OperationStep:
        if self.operation in {"ENRICH", "REFINE", "UPDATE", "MERGE"} and not self.target.ref:
            raise ValueError(f"{self.operation} requires a claim reference target")
        if self.operation in {"CREATE", "PRESERVE"} and not self.claim_refs:
            raise ValueError("CREATE/PRESERVE requires output claim references")
        if self.operation == "ENRICH" and not self.fields:
            raise ValueError("ENRICH requires expected patch values")
        if self.operation == "PROJECT" and not self.output_kind:
            raise ValueError("PROJECT requires a derived output kind")
        return self


class BenchmarkExpected(StrictModel):
    stage1: Stage1Expectation
    stage2: Stage2Expectation
    operation: Literal[
        "CREATE", "ENRICH", "REFINE", "UPDATE", "MERGE", "PROJECT", "PRESERVE", "NOOP", "MIXED"
    ]
    sub_operations: list[OperationStep] = Field(min_length=1)


class MemoryBenchmarkCase(StrictModel):
    id: str = Field(pattern=r"^BM-\d{3}$")
    schema_version: Literal["memory-benchmark-v1"] = BENCHMARK_VERSION
    category: Literal[
        "stable_fact", "preference", "event", "enrichment", "pattern", "state", "long_tail"
    ]
    scenario: str = Field(min_length=1)
    failure_mode: str = Field(min_length=1)
    difficulty: Literal["easy", "medium", "hard"]
    length_class: Literal["short", "medium", "long"]
    conversation: list[ConversationTurn] = Field(min_length=1, max_length=60)
    expected: BenchmarkExpected
    review_reason: str | None = None
    notes: str = ""

    @model_validator(mode="after")
    def validate_case(self, info: ValidationInfo) -> MemoryBenchmarkCase:
        turns = {turn.turn_id: turn for turn in self.conversation}
        if len(turns) != len(self.conversation):
            raise ValueError("duplicate turn_id")
        users = {key: turn.content for key, turn in turns.items() if turn.role == "user"}
        profile = str((info.context or {}).get("length_profile", "original"))
        ranges = EXPANSION_LENGTH_RANGES if profile == "expansion" else LENGTH_RANGES
        lower, upper = ranges[self.length_class]
        if not lower <= len(users) <= upper:
            raise ValueError("length_class counts USER turns, not assistant padding")
        if any("\ufffd" in turn.content for turn in self.conversation):
            raise ValueError("Unicode replacement character in conversation")
        claims = {claim.claim_id: claim for claim in self.expected.stage2.claims}
        if len(claims) != len(self.expected.stage2.claims):
            raise ValueError("duplicate claim_id")
        order = {turn.turn_id: index for index, turn in enumerate(self.conversation)}
        for claim in claims.values():
            if claim.source_turn_id not in users:
                raise ValueError("claim source must reference a user turn")
            if any(span not in users[claim.source_turn_id] for span in claim.evidence_spans):
                raise ValueError("evidence span must occur in its own source turn")
        for unit in self.expected.stage1.semantic_units:
            if unit.evidence_span not in users.get(unit.turn_id, ""):
                raise ValueError("Stage1 evidence must occur in its own user turn")
        for turn_id in self.expected.stage1.negative_turn_ids:
            if turn_id not in users:
                raise ValueError("negative checkpoint must reference a user turn")
        for step in self.expected.sub_operations:
            if step.turn_id not in users:
                raise ValueError("operation must reference a user turn")
            references = [*step.claim_refs, *step.target.refs]
            if step.target.ref:
                references.append(step.target.ref)
            for ref in references:
                if ref not in claims:
                    raise ValueError(f"unknown claim reference: {ref}")
                if order[claims[ref].source_turn_id] > order[step.turn_id]:
                    raise ValueError("forward claim reference")
            if step.target.ref:
                source = claims[step.target.ref].source_turn_id
                if order[source] >= order[step.turn_id]:
                    raise ValueError("mutation target must precede the mutation")
        return self


def load_memory_benchmark_v1_cases(
    path: Path, *, require_complete: bool = True, profile: BenchmarkProfile | None = None
) -> list[MemoryBenchmarkCase]:
    try:
        content = path.read_text(encoding="utf-8-sig")
        if "\ufffd" in content:
            raise ValueError("Unicode replacement character")
        raw_cases = [
            json.loads(line)
            for line in content.splitlines()
            if line.strip()
        ]
        ids = [str(case.get("id", "")) for case in raw_cases]
        if profile is None:
            numbers = {
                int(case_id[3:])
                for case_id in ids
                if case_id.startswith("BM-") and case_id[3:].isdigit()
            }
            if numbers and min(numbers) >= 101:
                profile = "expansion"
            elif numbers and max(numbers) > 100:
                profile = "merged"
            else:
                profile = "original"
        cases = [
            MemoryBenchmarkCase.model_validate(
                raw_case,
                context={
                    "length_profile": (
                        "expansion"
                        if profile == "expansion"
                        or (profile == "merged" and str(raw_case.get("id", ""))[3:].isdigit()
                            and int(str(raw_case.get("id", ""))[3:]) >= 101)
                        else "original"
                    )
                },
            )
            for raw_case in raw_cases
        ]
    except ValueError as exc:
        raise BenchmarkSchemaError(str(exc)) from exc
    if not cases or len({case.id for case in cases}) != len(cases):
        raise BenchmarkSchemaError("dataset empty or duplicate case id")
    if require_complete:
        if profile == "original":
            expected_ids = [f"BM-{n:03d}" for n in range(1, 101)]
            expected_categories = EXPECTED_CATEGORY_COUNTS
            expected_lengths = EXPECTED_LENGTH_COUNTS
        elif profile == "expansion":
            expected_ids = [f"BM-{n:03d}" for n in range(101, 181)]
            expected_categories = {"enrichment": 80}
            expected_lengths = {"short": 28, "medium": 32, "long": 20}
        else:
            expected_ids = [f"BM-{n:03d}" for n in range(1, 181)]
            expected_categories = {
                "stable_fact": 20,
                "preference": 15,
                "event": 20,
                "enrichment": 95,
                "pattern": 10,
                "state": 10,
                "long_tail": 10,
            }
            expected_lengths = {"short": 68, "medium": 72, "long": 40}
        if [case.id for case in cases] != expected_ids:
            raise BenchmarkSchemaError(f"expected {expected_ids[0]}..{expected_ids[-1]}")
        if Counter(case.category for case in cases) != expected_categories:
            raise BenchmarkSchemaError("category quotas differ")
        if Counter(case.length_class for case in cases) != expected_lengths:
            raise BenchmarkSchemaError("length quotas differ")
    return cases


def evaluate_memory_benchmark_v1(
    path: Path,
    *,
    case_id: str | None = None,
    require_complete: bool = True,
    profile: BenchmarkProfile | None = None,
) -> dict[str, Any]:
    cases = load_memory_benchmark_v1_cases(
        path, require_complete=require_complete, profile=profile
    )
    cases = [case for case in cases if case_id is None or case.id == case_id]
    if not cases:
        raise ValueError(f"unknown Benchmark V1 case: {case_id}")
    rows = []
    for case in cases:
        sources = {turn.turn_id: turn.content for turn in case.conversation}
        smoke = []
        for claim in case.expected.stage2.claims:
            if claim.semantic_type != "new_memory":
                continue
            try:
                candidate = MemoryCandidate(
                    kind=claim.kind,
                    subject=claim.subject,
                    summary=claim.semantic_target,
                    original_text=sources[claim.source_turn_id],
                    evidence_spans=claim.evidence_spans,
                    raw_predicate=claim.predicate,
                    explicitness=EvidenceExplicitness.EXPLICIT,
                    payload={"event_type": claim.event_type} if claim.event_type else {},
                )
                normalized = normalize_memory_candidate(candidate, REFERENCE_TIME)
                admission = assess_memory_admission(normalized, sources[claim.source_turn_id])
                smoke.append(
                    dict(
                        claim_id=claim.claim_id,
                        decision=admission.decision.value,
                        predicate=normalized.canonical_predicate or normalized.custom_predicate,
                        error=None,
                    )
                )
            except Exception as exc:
                smoke.append(
                    dict(claim_id=claim.claim_id, error=f"{type(exc).__name__}: {exc}"[:500])
                )
        rows.append(dict(id=case.id, production_claim_smoke=smoke))
    checks = [entry for row in rows for entry in row["production_claim_smoke"]]
    return dict(
        evaluation=BENCHMARK_VERSION,
        mode="contract_smoke",
        dataset=str(path),
        dataset_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        case_count=len(cases),
        user_turn_count=sum(turn.role == "user" for case in cases for turn in case.conversation),
        category_counts=dict(Counter(case.category for case in cases)),
        length_counts=dict(Counter(case.length_class for case in cases)),
        contract_valid=True,
        metrics=dict(
            schema_validity=1.0,
            model_quality_evaluated=False,
            production_claim_smoke_pass_rate=(
                sum(not check["error"] for check in checks) / len(checks) if checks else None
            ),
        ),
        cases=rows,
    )


def render_memory_benchmark_v1_report(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Memory Benchmark V1 — contract validation",
            "",
            f"Dataset: `{report['dataset']}`",
            f"SHA256: `{report['dataset_sha256']}`",
            "",
            f"Cases: {report['case_count']}; user turns: {report['user_turn_count']}",
            f"Categories: {report['category_counts']}",
            f"Lengths: {report['length_counts']}",
            "",
            "Model quality evaluated: **False**. These are structure and domain smoke checks.",
            "The smoke rate measures execution without exceptions, "
            "not semantic correctness or admission.",
            "For measured extraction/resolver/lifecycle quality, use the separate live report.",
            "",
        ]
    )

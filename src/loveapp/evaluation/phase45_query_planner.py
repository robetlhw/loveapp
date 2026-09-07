"""Evaluators for the Phase 4 contextual-rewrite and Phase 5 multi-query sets.

The specialised Markdown sets intentionally live outside the RAG V2 corpus.
This module parses them, runs the shared :class:`RetrievalQueryPlanner`, and
optionally performs real retrieval comparisons against an injected retriever.
No Gold data is changed by the evaluator.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from loveapp.application.retrieval_query_planner import RetrievalQueryPlanner
from loveapp.domain.knowledge import KnowledgeDocument, KnowledgeFilters
from loveapp.ports.knowledge import KnowledgeRetriever

_CASE_HEADING = re.compile(r"^##\s+([A-Za-z0-9_-]+)\s*$")
_FIELD = re.compile(r"^\*\*([^*]+):\*\*\s*(.*?)\s*$")
_LIST_SPLIT = re.compile(r"\s*[,，、;；\s]+\s*")


class ContextualRewriteCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    query_type: str
    difficulty: str
    length_bucket: str
    expected_branch: str
    expected_scenario: str | None = None
    relationship_stage: str | None = None
    expected_goals: list[str] = Field(default_factory=list)
    history: list[str] = Field(default_factory=list)
    current_query: str = Field(min_length=1)
    rewrite_required: bool
    expected_standalone_query: str = Field(min_length=1)
    relevant_ids: list[str] = Field(default_factory=list)


class MultiQueryCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    query_type: str
    difficulty: str
    length_bucket: str
    expected_branch: str
    expected_scenario: str | None = None
    relationship_stage: str | None = None
    expected_goals: list[str] = Field(default_factory=list)
    query: str = Field(min_length=1)
    decompose_required: bool
    expected_subquery_count: int = Field(ge=1, le=3)
    expected_subqueries: list[str] = Field(default_factory=list)
    relevant_ids: list[str] = Field(default_factory=list)
    need_coverage_groups: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_needs(self) -> MultiQueryCase:
        if (
            self.decompose_required
            and len(self.expected_subqueries) != self.expected_subquery_count
        ):
            raise ValueError("ExpectedSubqueries count does not match ExpectedSubqueryCount")
        if not self.decompose_required and self.expected_subquery_count != 1:
            raise ValueError("single-intent cases must expect one subquery")
        relevant = set(self.relevant_ids)
        for ids in self.need_coverage_groups.values():
            if not set(ids) <= relevant:
                raise ValueError("NeedCoverageGroups must be subsets of RelevantIDs")
        return self


def _parse_bool(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"invalid boolean: {value}")


def _parse_list(value: str) -> list[str]:
    value = value.strip()
    if value in {"", "[]", "none", "null"}:
        return []
    value = value.strip("[]")
    return list(dict.fromkeys(item.strip() for item in _LIST_SPLIT.split(value) if item.strip()))


def _parse_history(lines: list[str]) -> list[str]:
    history: list[str] = []
    for line in lines:
        value = line.strip()
        if not value.startswith("-"):
            continue
        value = value[1:].strip()
        # Keep only the actual turn content, while accepting both Chinese and
        # English role labels used by hand-authored fixtures.
        value = re.sub(
            r"^(?:用户|助手|user|assistant|human)\s*(?:上一轮|本轮)?\s*[:：]\s*",
            "",
            value,
            flags=re.I,
        )
        if value:
            history.append(value)
    return history


def _blocks(text: str) -> list[tuple[str, list[str]]]:
    output: list[tuple[str, list[str]]] = []
    current: str | None = None
    lines: list[str] = []
    for raw in text.splitlines():
        heading = _CASE_HEADING.match(raw.strip())
        if heading:
            if current is not None:
                output.append((current, lines))
            current, lines = heading.group(1), []
        elif current is not None:
            lines.append(raw.rstrip())
    if current is not None:
        output.append((current, lines))
    if not output:
        raise ValueError("evaluation Markdown contains no cases")
    return output


def _field_map(lines: list[str]) -> tuple[dict[str, str], list[str]]:
    fields: dict[str, str] = {}
    history_lines: list[str] = []
    in_history = False
    for line in lines:
        match = _FIELD.match(line.strip())
        if match:
            key, value = match.group(1).strip(), match.group(2).strip()
            fields[key] = value
            in_history = key.casefold() == "history"
            continue
        if in_history:
            history_lines.append(line)
    return fields, history_lines


def load_contextual_rewrite_eval_markdown(path: Path) -> list[ContextualRewriteCase]:
    return parse_contextual_rewrite_eval_markdown(
        path.read_text(encoding="utf-8-sig"), source_ref=path.name
    )


def parse_contextual_rewrite_eval_markdown(
    text: str, *, source_ref: str = "<string>"
) -> list[ContextualRewriteCase]:
    cases: list[ContextualRewriteCase] = []
    for case_id, lines in _blocks(text):
        fields, history_lines = _field_map(lines)
        required = {
            "QueryType",
            "Difficulty",
            "LengthBucket",
            "ExpectedBranch",
            "CurrentQuery",
            "RewriteRequired",
            "ExpectedStandaloneQuery",
            "RelevantIDs",
        }
        missing = required - fields.keys()
        if missing:
            raise ValueError(f"{source_ref}:{case_id} missing fields: {sorted(missing)}")
        cases.append(
            ContextualRewriteCase(
                id=case_id,
                query_type=fields["QueryType"],
                difficulty=fields["Difficulty"],
                length_bucket=fields["LengthBucket"],
                expected_branch=fields["ExpectedBranch"],
                expected_scenario=fields.get("ExpectedPrimaryScenario"),
                relationship_stage=fields.get("RelationshipStage"),
                expected_goals=_parse_list(fields.get("ExpectedGoals", "")),
                history=_parse_history(history_lines),
                current_query=fields["CurrentQuery"],
                rewrite_required=_parse_bool(fields["RewriteRequired"]),
                expected_standalone_query=fields["ExpectedStandaloneQuery"],
                relevant_ids=_parse_list(fields["RelevantIDs"]),
            )
        )
    _ensure_unique_ids(cases, source_ref)
    return cases


def load_multiquery_eval_markdown(path: Path) -> list[MultiQueryCase]:
    return parse_multiquery_eval_markdown(
        path.read_text(encoding="utf-8-sig"), source_ref=path.name
    )


def parse_multiquery_eval_markdown(
    text: str, *, source_ref: str = "<string>"
) -> list[MultiQueryCase]:
    cases: list[MultiQueryCase] = []
    for case_id, lines in _blocks(text):
        fields, _ = _field_map(lines)
        # List-valued ExpectedSubqueries and NeedCoverageGroups are parsed from
        # their following bullet lines rather than the bold field value.
        subqueries: list[str] = []
        groups: dict[str, list[str]] = {}
        active: str | None = None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("**ExpectedSubqueries:"):
                active = "subqueries"
                continue
            if stripped.startswith("**NeedCoverageGroups:"):
                active = "groups"
                continue
            if stripped.startswith("**"):
                active = None
                continue
            if not stripped.startswith("-"):
                continue
            item = stripped[1:].strip()
            if active == "subqueries":
                item = re.sub(r"^Q\d+\s*[:：]\s*", "", item, flags=re.I)
                if item:
                    subqueries.append(item)
            elif active == "groups":
                key, separator, value = item.partition(":")
                if not separator:
                    key, separator, value = item.partition("：")
                if separator:
                    groups[key.strip()] = _parse_list(value)
        required = {
            "QueryType",
            "Difficulty",
            "LengthBucket",
            "ExpectedBranch",
            "Query",
            "DecomposeRequired",
            "ExpectedSubqueryCount",
            "RelevantIDs",
        }
        missing = required - fields.keys()
        if missing:
            raise ValueError(f"{source_ref}:{case_id} missing fields: {sorted(missing)}")
        cases.append(
            MultiQueryCase(
                id=case_id,
                query_type=fields["QueryType"],
                difficulty=fields["Difficulty"],
                length_bucket=fields["LengthBucket"],
                expected_branch=fields["ExpectedBranch"],
                expected_scenario=fields.get("ExpectedPrimaryScenario"),
                relationship_stage=fields.get("RelationshipStage"),
                expected_goals=_parse_list(fields.get("ExpectedGoals", "")),
                query=fields["Query"],
                decompose_required=_parse_bool(fields["DecomposeRequired"]),
                expected_subquery_count=int(fields["ExpectedSubqueryCount"]),
                expected_subqueries=subqueries,
                relevant_ids=_parse_list(fields["RelevantIDs"]),
                need_coverage_groups=groups,
            )
        )
    _ensure_unique_ids(cases, source_ref)
    return cases


def _ensure_unique_ids(cases: Sequence[Any], source_ref: str) -> None:
    ids = [case.id for case in cases]
    duplicates = sorted(item for item, count in Counter(ids).items() if count > 1)
    if duplicates:
        raise ValueError(f"{source_ref} duplicate case IDs: {duplicates}")


# The specialised Phase 4/5 fixtures intentionally use a small, explicit
# vocabulary.  Keep these sets local to the evaluator instead of widening the
# production domain enums (which would silently change routing semantics).
_PHASE45_BRANCHES = frozenset({"rag", "safety", "out_of_scope"})
_PHASE45_SCENARIOS = frozenset(
    {
        "pursuit",
        "chat_analysis",
        "conflict",
        "relationship_maintenance",
        "boundary",
        "breakup",
    }
)
_PHASE45_GOALS = frozenset(
    {
        "initiate",
        "understand",
        "progress",
        "repair",
        "communicate",
        "set_boundary",
        "end_relationship",
    }
)
_PHASE45_STAGES = frozenset(
    {
        "unknown",
        "stranger",
        "acquaintance",
        "ambiguous",
        "dating",
        "stable_relationship",
        "long_distance",
        "breakup",
    }
)
_PHASE45_DIFFICULTIES = frozenset({"easy", "medium", "hard"})
_PHASE45_LENGTH_BUCKETS = frozenset({"short", "medium", "long"})
_PHASE4_QUERY_TYPES = frozenset({"context_dependent", "standalone_control"})
_PHASE5_QUERY_TYPES = frozenset({"single_intent_control", "two_intents", "three_intents"})


def _canonical_case_text(value: str) -> str:
    """Canonicalise fixture text for duplicate/equivalence checks.

    This is deliberately a lint-only normalisation.  It does not alter the
    query sent to the planner or retriever and therefore cannot affect scores.
    """

    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return "".join(
        char
        for char in normalized
        if char.isalnum() or "\u4e00" <= char <= "\u9fff"
    )


def _lint_token_set(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    latin = set(re.findall(r"[a-z0-9]+", normalized))
    chinese = re.findall(r"[\u4e00-\u9fff]+", normalized)
    grams = {
        sequence[index : index + 2]
        for sequence in chinese
        for index in range(max(0, len(sequence) - 1))
    }
    return latin | grams


def _semantically_equivalent(left: str, right: str) -> bool:
    """Conservative equivalence check used for standalone controls.

    Exact canonical equality is preferred.  For hand-authored controls that
    differ only by punctuation/spacing or a very small paraphrase, a high
    token Jaccard threshold is accepted.  This check is intentionally much
    stricter than the retrieval drift diagnostic below.
    """

    if _canonical_case_text(left) == _canonical_case_text(right):
        return True
    left_tokens, right_tokens = _lint_token_set(left), _lint_token_set(right)
    if not left_tokens or not right_tokens:
        return False
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens) >= 0.80


def _known_document_ids(
    knowledge_ids: Sequence[str] | None,
    knowledge_documents: Sequence[KnowledgeDocument] | None,
) -> tuple[set[str] | None, dict[str, Any]]:
    """Normalise an optional KB input and return check metadata.

    ``None`` means that the caller did not provide a KB and the existence
    check is therefore unavailable; an empty sequence is a real (empty) KB
    and will correctly flag every referenced ID as unknown.
    """

    if knowledge_documents is not None:
        ids = {str(document.id) for document in knowledge_documents}
        return ids, {
            "provided": True,
            "document_count": len(knowledge_documents),
            "count_is_500": len(knowledge_documents) == 500,
            "source": "documents",
        }
    if knowledge_ids is not None:
        ids = {str(value) for value in knowledge_ids}
        return ids, {
            "provided": True,
            "document_count": len(ids),
            "count_is_500": len(ids) == 500,
            "source": "ids",
        }
    return None, {
        "provided": False,
        "document_count": None,
        "count_is_500": None,
        "source": None,
    }


def _phase45_case_common_lint(
    cases: Sequence[ContextualRewriteCase | MultiQueryCase],
    *,
    phase: int,
    source_ref: str,
    known_ids: set[str] | None,
) -> tuple[list[str], list[str], dict[str, Any]]:
    """Validate fields shared by both specialised datasets."""

    errors: list[str] = []
    warnings: list[str] = []
    ids = [case.id for case in cases]
    duplicate_ids = sorted(item for item, count in Counter(ids).items() if count > 1)
    if duplicate_ids:
        errors.append(f"{source_ref} duplicate case IDs: {duplicate_ids}")

    query_values = [
        case.current_query if isinstance(case, ContextualRewriteCase) else case.query
        for case in cases
    ]
    canonical_queries = [_canonical_case_text(value) for value in query_values]
    duplicate_queries = sorted(
        value
        for value, count in Counter(canonical_queries).items()
        if value and count > 1
    )
    if duplicate_queries:
        errors.append(f"{source_ref} duplicate queries: {len(duplicate_queries)}")

    invalid: dict[str, list[str]] = {
        "branch": [],
        "scenario": [],
        "goal": [],
        "relationship_stage": [],
        "difficulty": [],
        "length_bucket": [],
        "query_type": [],
    }
    for case in cases:
        if case.expected_branch not in _PHASE45_BRANCHES:
            invalid["branch"].append(case.id)
        if case.expected_scenario is not None and case.expected_scenario not in _PHASE45_SCENARIOS:
            invalid["scenario"].append(case.id)
        if case.relationship_stage is not None and case.relationship_stage not in _PHASE45_STAGES:
            invalid["relationship_stage"].append(case.id)
        if case.difficulty not in _PHASE45_DIFFICULTIES:
            invalid["difficulty"].append(case.id)
        if case.length_bucket not in _PHASE45_LENGTH_BUCKETS:
            invalid["length_bucket"].append(case.id)
        allowed_types = _PHASE4_QUERY_TYPES if phase == 4 else _PHASE5_QUERY_TYPES
        if case.query_type not in allowed_types:
            invalid["query_type"].append(case.id)
        for goal in case.expected_goals:
            if goal not in _PHASE45_GOALS:
                invalid["goal"].append(case.id)
                break
    for field, case_ids in invalid.items():
        if case_ids:
            errors.append(f"{source_ref} invalid {field} enum in cases: {sorted(set(case_ids))}")

    referenced_ids = sorted(
        {
            document_id
            for case in cases
            for document_id in case.relevant_ids
        }
    )
    unknown_ids: list[str] = []
    if known_ids is None:
        warnings.append("KB ID existence check unavailable (knowledge IDs were not supplied)")
    else:
        unknown_ids = sorted(set(referenced_ids) - known_ids)
        if unknown_ids:
            errors.append(f"{source_ref} RelevantIDs missing from KB: {unknown_ids}")
    return errors, warnings, {
        "case_count": len(cases),
        "ids_unique": not duplicate_ids,
        "duplicate_ids": duplicate_ids,
        "queries_unique": not duplicate_queries,
        "duplicate_queries": duplicate_queries,
        "referenced_ids": referenced_ids,
        "unknown_relevant_ids": unknown_ids,
        "invalid_enums": {key: sorted(set(value)) for key, value in invalid.items() if value},
    }


def validate_contextual_rewrite_dataset(
    cases: Sequence[ContextualRewriteCase],
    *,
    knowledge_ids: Sequence[str] | None = None,
    knowledge_documents: Sequence[KnowledgeDocument] | None = None,
    source_ref: str = "<contextual-rewrite>",
) -> dict[str, Any]:
    """Run the complete Phase 4 fixture lint without mutating Gold data."""

    known_ids, knowledge = _known_document_ids(knowledge_ids, knowledge_documents)
    errors, warnings, details = _phase45_case_common_lint(
        cases,
        phase=4,
        source_ref=source_ref,
        known_ids=known_ids,
    )
    standalone_drift: list[str] = []
    missing_history: list[str] = []
    for case in cases:
        if not case.rewrite_required and not _semantically_equivalent(
            case.current_query, case.expected_standalone_query
        ):
            standalone_drift.append(case.id)
        if case.rewrite_required and not case.history:
            missing_history.append(case.id)
    if standalone_drift:
        errors.append(
            f"{source_ref} standalone controls have non-equivalent ExpectedStandaloneQuery: "
            f"{standalone_drift}"
        )
    if missing_history:
        warnings.append(
            f"{source_ref} rewrite-required cases have empty History: {missing_history}"
        )
    details.update(
        {
            "standalone_control_equivalent": not standalone_drift,
            "standalone_control_drift_cases": standalone_drift,
            "rewrite_required_without_history": missing_history,
        }
    )
    return {
        "phase": 4,
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "knowledge": knowledge,
        "details": details,
    }


def validate_multiquery_dataset(
    cases: Sequence[MultiQueryCase],
    *,
    knowledge_ids: Sequence[str] | None = None,
    knowledge_documents: Sequence[KnowledgeDocument] | None = None,
    source_ref: str = "<multiquery>",
) -> dict[str, Any]:
    """Run the complete Phase 5 fixture lint without mutating Gold data."""

    known_ids, knowledge = _known_document_ids(knowledge_ids, knowledge_documents)
    errors, warnings, details = _phase45_case_common_lint(
        cases,
        phase=5,
        source_ref=source_ref,
        known_ids=known_ids,
    )
    count_errors: list[str] = []
    expected_subquery_errors: list[str] = []
    group_errors: list[str] = []
    duplicate_subqueries: list[str] = []
    for case in cases:
        if not 1 <= int(case.expected_subquery_count) <= 3:
            count_errors.append(case.id)
        if len(case.expected_subqueries) != int(case.expected_subquery_count):
            expected_subquery_errors.append(case.id)
        if not case.decompose_required and int(case.expected_subquery_count) != 1:
            count_errors.append(case.id)
        if case.decompose_required and int(case.expected_subquery_count) < 2:
            count_errors.append(case.id)
        if case.query_type == "single_intent_control" and (
            case.decompose_required or int(case.expected_subquery_count) != 1
        ):
            count_errors.append(case.id)
        if case.query_type == "two_intents" and (
            not case.decompose_required or int(case.expected_subquery_count) != 2
        ):
            count_errors.append(case.id)
        if case.query_type == "three_intents" and (
            not case.decompose_required or int(case.expected_subquery_count) != 3
        ):
            count_errors.append(case.id)
        relevant = set(case.relevant_ids)
        for group, ids in case.need_coverage_groups.items():
            if not group.strip() or not ids or not set(ids) <= relevant:
                group_errors.append(case.id)
        canonical_subqueries = [_canonical_case_text(value) for value in case.expected_subqueries]
        if len(canonical_subqueries) != len(set(canonical_subqueries)):
            duplicate_subqueries.append(case.id)
    count_errors = sorted(set(count_errors))
    expected_subquery_errors = sorted(set(expected_subquery_errors))
    group_errors = sorted(set(group_errors))
    duplicate_subqueries = sorted(set(duplicate_subqueries))
    if count_errors:
        errors.append(f"{source_ref} ExpectedSubqueryCount contract failed: {count_errors}")
    if expected_subquery_errors:
        errors.append(f"{source_ref} ExpectedSubqueries count mismatch: {expected_subquery_errors}")
    if group_errors:
        errors.append(f"{source_ref} NeedCoverageGroups invalid: {group_errors}")
    if duplicate_subqueries:
        errors.append(f"{source_ref} duplicate ExpectedSubqueries: {duplicate_subqueries}")
    details.update(
        {
            "expected_subquery_count_leq_3": not any(
                int(case.expected_subquery_count) > 3 for case in cases
            ),
            "expected_subquery_count_errors": count_errors,
            "expected_subqueries_count_mismatch": expected_subquery_errors,
            "need_coverage_groups_valid": not group_errors,
            "need_coverage_groups_errors": group_errors,
            "duplicate_expected_subqueries": duplicate_subqueries,
        }
    )
    return {
        "phase": 5,
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "knowledge": knowledge,
        "details": details,
    }


def validate_phase45_datasets(
    rewrite_dev: Sequence[ContextualRewriteCase],
    rewrite_test: Sequence[ContextualRewriteCase],
    multiquery_dev: Sequence[MultiQueryCase],
    multiquery_test: Sequence[MultiQueryCase],
    *,
    knowledge_ids: Sequence[str] | None = None,
    knowledge_documents: Sequence[KnowledgeDocument] | None = None,
    strict_cross_split: bool = True,
) -> dict[str, Any]:
    """Lint all Phase 4/5 splits and their Dev/Test separation.

    ``strict_cross_split`` controls whether duplicate Dev/Test query text is a
    failing error or a warning.  The default follows the written benchmark
    contract.  Existing contextual fixtures intentionally reuse generic
    ellipsis queries with different histories; callers evaluating those
    historical fixtures can set it to ``False`` while retaining diagnostics.
    """

    _known_ids, knowledge = _known_document_ids(knowledge_ids, knowledge_documents)
    phase4_dev = validate_contextual_rewrite_dataset(
        rewrite_dev,
        knowledge_ids=knowledge_ids,
        knowledge_documents=knowledge_documents,
        source_ref="phase4-dev",
    )
    phase4_test = validate_contextual_rewrite_dataset(
        rewrite_test,
        knowledge_ids=knowledge_ids,
        knowledge_documents=knowledge_documents,
        source_ref="phase4-test",
    )
    phase5_dev = validate_multiquery_dataset(
        multiquery_dev,
        knowledge_ids=knowledge_ids,
        knowledge_documents=knowledge_documents,
        source_ref="phase5-dev",
    )
    phase5_test = validate_multiquery_dataset(
        multiquery_test,
        knowledge_ids=knowledge_ids,
        knowledge_documents=knowledge_documents,
        source_ref="phase5-test",
    )
    errors = [
        *phase4_dev["errors"],
        *phase4_test["errors"],
        *phase5_dev["errors"],
        *phase5_test["errors"],
    ]
    warnings = [
        *phase4_dev["warnings"],
        *phase4_test["warnings"],
        *phase5_dev["warnings"],
        *phase5_test["warnings"],
    ]
    split_pairs = {
        "phase4": (rewrite_dev, rewrite_test, "current_query"),
        "phase5": (multiquery_dev, multiquery_test, "query"),
    }
    cross_split: dict[str, Any] = {}
    for name, (dev, test, field) in split_pairs.items():
        dev_ids = {case.id for case in dev}
        test_ids = {case.id for case in test}
        id_overlap = sorted(dev_ids & test_ids)
        dev_map = {
            _canonical_case_text(getattr(case, field)): case.id
            for case in dev
            if getattr(case, field)
        }
        test_map = {
            _canonical_case_text(getattr(case, field)): case.id
            for case in test
            if getattr(case, field)
        }
        query_overlap = sorted(set(dev_map) & set(test_map))
        cross_split[name] = {
            "dev_test_id_overlap": id_overlap,
            "dev_test_query_overlap": [
                {"canonical_query": query, "dev_id": dev_map[query], "test_id": test_map[query]}
                for query in query_overlap
            ],
            "queries_disjoint": not query_overlap,
        }
        if id_overlap:
            errors.append(f"{name} Dev/Test case IDs overlap: {id_overlap}")
        if query_overlap:
            message = f"{name} Dev/Test queries overlap: {len(query_overlap)}"
            if strict_cross_split:
                errors.append(message)
            else:
                warnings.append(message)

    # Cross-phase overlap is useful to expose, but it is not inherently a
    # violation: the same user query can be represented in rewrite and
    # decomposition fixtures for different measurements.
    all_phase_queries: dict[str, list[str]] = {}
    for label, values, field in (
        ("phase4-dev", rewrite_dev, "current_query"),
        ("phase4-test", rewrite_test, "current_query"),
        ("phase5-dev", multiquery_dev, "query"),
        ("phase5-test", multiquery_test, "query"),
    ):
        for case in values:
            key = _canonical_case_text(getattr(case, field))
            all_phase_queries.setdefault(key, []).append(f"{label}:{case.id}")
    cross_phase_overlap = {
        query: refs for query, refs in all_phase_queries.items() if len(refs) > 1
    }
    if cross_phase_overlap:
        warnings.append(f"cross-phase query overlap: {len(cross_phase_overlap)}")

    return {
        "schema_version": 1,
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "knowledge": knowledge,
        "phase4": {"dev": phase4_dev, "test": phase4_test},
        "phase5": {"dev": phase5_dev, "test": phase5_test},
        "cross_split": cross_split,
        "cross_phase_query_overlap": cross_phase_overlap,
        "strict_cross_split": strict_cross_split,
    }


def _binary_metrics(predicted: Sequence[bool], expected: Sequence[bool]) -> dict[str, float]:
    tp = sum(p and e for p, e in zip(predicted, expected, strict=True))
    fp = sum(p and not e for p, e in zip(predicted, expected, strict=True))
    fn = sum((not p) and e for p, e in zip(predicted, expected, strict=True))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


def _rank_metrics(
    ids: Sequence[str],
    relevant: Sequence[str],
    *,
    top_k: int = 5,
    candidate_ids: Sequence[str] | None = None,
) -> dict[str, float | bool]:
    relevant_set = set(relevant)
    values: dict[str, float | bool] = {}
    for k in (1, 3, 5):
        top = list(ids[:k])
        values[f"hit_at_{k}"] = bool(relevant_set.intersection(top))
    first = next((index + 1 for index, value in enumerate(ids) if value in relevant_set), None)
    values["mrr"] = round(1 / first, 4) if first else 0.0
    # Phase 5 only has a binary ``RelevantIDs`` annotation.  Use a gain of
    # one for those IDs and zero for all other candidates; the ideal list is
    # therefore the best possible placement of the annotated IDs.  Keeping
    # this helper local avoids importing the V2 evaluator (and its graded
    # relevance contract) into the specialised Phase 4/5 evaluator.
    values["ndcg_at_3"] = ndcg_at_k(ids, relevant, 3)
    values["ndcg_at_5"] = ndcg_at_k(ids, relevant, 5)
    # Candidate recall is defined over the pre-rerank candidate pool when the
    # retriever exposes one.  Falling back to the returned list keeps simple
    # retriever adapters compatible while making the metric's provenance
    # explicit for detailed stores.
    candidate_pool = list(candidate_ids) if candidate_ids is not None else list(ids)
    values["candidate_recall"] = (
        round(len(relevant_set.intersection(candidate_pool)) / len(relevant_set), 4)
        if relevant_set
        else 0.0
    )
    return values


def ndcg_at_k(returned_ids: Sequence[str], relevant_ids: Sequence[str], k: int) -> float:
    """Return binary-relevance nDCG@``k`` for a Phase 5 result list.

    ``RelevantIDs`` in the Phase 5 fixtures denotes the set of documents that
    satisfy a need; it does not carry the graded 0/1/2/3 labels used by the
    main RAG V2 evaluator.  This implementation consequently uses binary
    gains while still normalising against the ideal ordering for the full
    relevant set.  Empty/invalid cutoffs have a deterministic score of zero.
    """

    if k <= 0:
        return 0.0
    relevant = set(relevant_ids)
    if not relevant:
        return 0.0
    gains = [1 if document_id in relevant else 0 for document_id in returned_ids[:k]]
    dcg = sum(gain / math.log2(rank + 1) for rank, gain in enumerate(gains, start=1))
    ideal_length = min(k, len(relevant))
    idcg = sum(1 / math.log2(rank + 1) for rank in range(1, ideal_length + 1))
    return round(dcg / idcg, 4) if idcg else 0.0


async def _search_ids(
    retriever: KnowledgeRetriever,
    query: str,
    *,
    case: ContextualRewriteCase | MultiQueryCase,
    limit: int,
) -> tuple[list[str], float, list[str]]:
    started = perf_counter()
    filters = KnowledgeFilters()
    detailed = getattr(retriever, "search_detailed", None)
    if callable(detailed):
        result = await detailed(query, filters=filters, limit=limit, trace=None)
        returned_values = result.returned or result.reranked_candidates
        candidate_values = (
            result.candidates
            or result.nearest_candidates
            or result.reranked_candidates
            or returned_values
        )
    else:
        returned_values = await retriever.search(
            query,
            filters=filters,
            limit=limit,
            trace=None,
        )
        candidate_values = returned_values
    return (
        [item.document.id for item in returned_values],
        (perf_counter() - started) * 1000,
        [item.document.id for item in candidate_values],
    )


async def evaluate_contextual_rewrite(
    cases: Sequence[ContextualRewriteCase],
    *,
    planner: RetrievalQueryPlanner | None = None,
    retriever: KnowledgeRetriever | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    planner = planner or RetrievalQueryPlanner(contextual_query_rewrite_enabled=True)
    rows: list[dict[str, Any]] = []
    predicted: list[bool] = []
    expected: list[bool] = []
    for case in cases:
        plan = planner.plan(case.current_query, case.history)
        predicted.append(plan.rewritten)
        expected.append(case.rewrite_required)
        row: dict[str, Any] = {
            "id": case.id,
            "expected_rewrite": case.rewrite_required,
            "predicted_rewrite": plan.rewritten,
            "trigger_reason": plan.rewrite_trigger,
            "raw_query": case.current_query,
            "retrieval_query": plan.retrieval_query,
            "expected_standalone_query": case.expected_standalone_query,
            "history_window": plan.history_window,
            "query_drift": bool(not case.rewrite_required and plan.rewritten),
            "passthrough_accuracy": (
                _semantically_equivalent(plan.retrieval_query, case.current_query)
                if not case.rewrite_required
                else None
            ),
        }
        if case.rewrite_required:
            row["query_drift"] = bool(
                plan.rewritten
                and _semantic_overlap(plan.retrieval_query, case.expected_standalone_query) < 0.20
            )
        row["errors"] = []
        if plan.rewritten != case.rewrite_required:
            row["errors"].append("rewrite_trigger_error")
        if row["query_drift"]:
            row["errors"].append("query_drift")
        if retriever is not None:
            raw_ids, raw_latency, raw_candidate_ids = await _search_ids(
                retriever, case.current_query, case=case, limit=top_k
            )
            rewritten_ids, rewritten_latency, rewritten_candidate_ids = await _search_ids(
                retriever, plan.retrieval_query, case=case, limit=top_k
            )
            row["raw"] = {
                "ids": raw_ids,
                "candidate_ids": raw_candidate_ids,
                "latency_ms": round(raw_latency, 3),
                **_rank_metrics(
                    raw_ids,
                    case.relevant_ids,
                    top_k=top_k,
                    candidate_ids=raw_candidate_ids,
                ),
            }
            row["rewritten"] = {
                "ids": rewritten_ids,
                "candidate_ids": rewritten_candidate_ids,
                "latency_ms": round(rewritten_latency, 3),
                **_rank_metrics(
                    rewritten_ids,
                    case.relevant_ids,
                    top_k=top_k,
                    candidate_ids=rewritten_candidate_ids,
                ),
            }
            row["retrieval_gain"] = {
                key: round(float(row["rewritten"][key]) - float(row["raw"][key]), 4)
                for key in (
                    "hit_at_1",
                    "hit_at_3",
                    "hit_at_5",
                    "mrr",
                    "ndcg_at_3",
                    "ndcg_at_5",
                    "candidate_recall",
                )
            }
            row["retrieval_degradation"] = {
                key: round(float(row["raw"][key]) - float(row["rewritten"][key]), 4)
                for key in (
                    "hit_at_1",
                    "hit_at_3",
                    "hit_at_5",
                    "mrr",
                    "ndcg_at_3",
                    "ndcg_at_5",
                    "candidate_recall",
                )
            }
            row["latency_ms"] = round(raw_latency + rewritten_latency, 3)
            # Candidate miss means the gold document was absent from both
            # candidate pools; rerank error means it was a candidate but did
            # not survive the final top-k.
            gold = set(case.relevant_ids)
            rewritten_candidates = set(rewritten_candidate_ids)
            if gold and not gold.intersection(rewritten_candidates):
                row["errors"].append("candidate_miss")
            elif gold and not gold.intersection(rewritten_ids[:top_k]):
                row["errors"].append("rerank_error")
        rows.append(row)
    report: dict[str, Any] = {
        "schema_version": 1,
        "phase": 4,
        "generated_at": datetime.now(UTC).isoformat(),
        "case_count": len(rows),
        "lint": validate_contextual_rewrite_dataset(cases),
        "trigger": _binary_metrics(predicted, expected),
        "query_drift_rate": round(mean(bool(row["query_drift"]) for row in rows), 4)
        if rows
        else 0.0,
        "cases": rows,
        "slices": _slice_reports(cases, rows),
        "error_attribution": _error_counts(rows, PHASE45_ERROR_ATTRIBUTION_CATEGORIES),
        "top_failures": [row for row in rows if row.get("errors")][:20],
        "latency": _latency_summary(rows),
        "retrieval_executed": retriever is not None,
    }
    if retriever is not None:
        rewrite_rows = [
            row for row in rows if row.get("expected_rewrite") and "retrieval_gain" in row
        ]
        control_rows = [
            row for row in rows if not row.get("expected_rewrite") and "retrieval_gain" in row
        ]
        report["retrieval_gain"] = _mean_gain(rewrite_rows)
    else:
        control_rows = [row for row in rows if not row.get("expected_rewrite")]
    standalone = _standalone_control_summary(control_rows)
    report["standalone_control"] = standalone
    # Keep the key metrics available at the report root as well as in the
    # nested control object.  This makes the JSON directly consumable by the
    # Phase 4 acceptance checks without requiring callers to know the layout.
    report["passthrough_accuracy"] = standalone["passthrough_accuracy"]
    report["standalone_retrieval_degradation"] = standalone["retrieval_degradation"]
    drift_cases = [row["id"] for row in rows if row.get("query_drift")]
    report["query_drift_summary"] = {
        "count": len(drift_cases),
        "rate": report["query_drift_rate"],
        "case_ids": drift_cases,
    }
    return report


def _mean_gain(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    keys = (
        "hit_at_1",
        "hit_at_3",
        "hit_at_5",
        "mrr",
        "ndcg_at_3",
        "ndcg_at_5",
        "candidate_recall",
    )
    return {
        key: round(mean(float(row["retrieval_gain"][key]) for row in rows), 4) if rows else 0.0
        for key in keys
    }


def _standalone_control_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarise the no-rewrite control separately from rewrite-needed cases."""

    if not rows:
        return {
            "case_count": 0,
            "passthrough_accuracy": 0.0,
            "query_drift_rate": 0.0,
            "hit_at_3_degradation": 0.0,
            "hit_at_5_degradation": 0.0,
            "mrr_degradation": 0.0,
            "candidate_recall_degradation": 0.0,
            "latency_overhead_ms": 0.0,
            "retrieval_degradation": {},
        }

    passthrough = [
        not bool(row.get("predicted_rewrite"))
        and _canonical_case_text(str(row.get("raw_query", "")))
        == _canonical_case_text(str(row.get("retrieval_query", "")))
        for row in rows
    ]
    drift = [bool(row.get("query_drift")) for row in rows]
    metrics = (
        "hit_at_3",
        "hit_at_5",
        "mrr",
        "candidate_recall",
        "ndcg_at_3",
        "ndcg_at_5",
    )
    degradation: dict[str, float] = {}
    for key in metrics:
        values: list[float] = []
        for row in rows:
            raw = row.get("raw")
            rewritten = row.get("rewritten")
            if isinstance(raw, Mapping) and isinstance(rewritten, Mapping):
                values.append(float(raw.get(key, 0.0)) - float(rewritten.get(key, 0.0)))
        degradation[key] = round(mean(values), 4) if values else 0.0
    overhead = [
        float(row["rewritten"]["latency_ms"]) - float(row["raw"]["latency_ms"])
        for row in rows
        if isinstance(row.get("raw"), Mapping)
        and isinstance(row.get("rewritten"), Mapping)
        and isinstance(row["raw"].get("latency_ms"), (int, float))
        and isinstance(row["rewritten"].get("latency_ms"), (int, float))
    ]
    return {
        "case_count": len(rows),
        "passthrough_accuracy": round(mean(passthrough), 4),
        "query_drift_rate": round(mean(drift), 4),
        "hit_at_3_degradation": degradation["hit_at_3"],
        "hit_at_5_degradation": degradation["hit_at_5"],
        "mrr_degradation": degradation["mrr"],
        "candidate_recall_degradation": degradation["candidate_recall"],
        "latency_overhead_ms": round(mean(overhead), 3) if overhead else 0.0,
        "retrieval_degradation": degradation,
    }


def _decomposition_disabled_planner(planner: RetrievalQueryPlanner) -> RetrievalQueryPlanner:
    """Build the single-query control while preserving rewrite/rerank policy."""

    return RetrievalQueryPlanner(
        contextual_query_rewrite_enabled=planner.contextual_query_rewrite_enabled,
        query_decomposition_enabled=False,
        max_subqueries=planner.max_subqueries,
        history_window=planner.history_window,
        rewrite_fn=planner.rewrite_fn,
        rerank_config=planner.rerank_config,
        coverage_bonus=getattr(planner, "coverage_bonus", 0.0),
    )


def _retrieval_rank_payload(
    ids: Sequence[str],
    relevant_ids: Sequence[str],
    *,
    top_k: int,
    latency_ms: float,
    merged_candidate_count: int,
) -> dict[str, Any]:
    """Serialize ranking and execution diagnostics for a Phase 5 row."""

    return {
        "ids": list(ids),
        "latency_ms": round(float(latency_ms), 3),
        "merged_candidate_count": int(merged_candidate_count),
        **_rank_metrics(ids, relevant_ids, top_k=top_k),
    }


async def evaluate_multiquery(
    cases: Sequence[MultiQueryCase],
    *,
    planner: RetrievalQueryPlanner | None = None,
    retriever: KnowledgeRetriever | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    planner = planner or RetrievalQueryPlanner(query_decomposition_enabled=True)
    # The baseline intentionally disables only decomposition.  If contextual
    # rewrite is enabled for the evaluated arm, the baseline keeps that same
    # rewrite policy so the measured deltas isolate multi-query retrieval.
    baseline_planner = (
        _decomposition_disabled_planner(planner) if retriever is not None else None
    )
    rows: list[dict[str, Any]] = []
    predicted: list[bool] = []
    expected: list[bool] = []
    count_correct = 0
    for case in cases:
        plan = planner.plan(case.query)
        predicted.append(plan.decomposed)
        expected.append(case.decompose_required)
        count_correct += plan.subquery_count == case.expected_subquery_count
        row: dict[str, Any] = {
            "id": case.id,
            "expected_decompose": case.decompose_required,
            "predicted_decompose": plan.decomposed,
            "expected_subquery_count": case.expected_subquery_count,
            "predicted_subquery_count": plan.subquery_count,
            "decomposition_trigger": plan.decomposition_trigger,
            "subqueries": plan.subqueries,
            "errors": [],
        }
        if plan.decomposed != case.decompose_required:
            row["errors"].append("decomposition_trigger_error")
        if plan.subquery_count != case.expected_subquery_count:
            row["errors"].append("subquery_quality_error")
        if case.expected_subqueries and plan.decomposed:
            expected_tokens = [_token_set(item) for item in case.expected_subqueries]
            predicted_tokens = [_token_set(item) for item in plan.subqueries]
            quality = all(
                any(len(left & right) / len(left | right) >= 0.15 for right in predicted_tokens)
                for left in expected_tokens
            )
            row["subquery_quality_ok"] = quality
            if not quality:
                row["errors"].append("subquery_quality_error")
        else:
            row["subquery_quality_ok"] = not case.decompose_required
        if retriever is not None:
            merged = await planner.retrieve(
                retriever,
                case.query,
                limit=top_k,
            )
            per_ids = merged.per_subquery_candidate_ids
            merged_ids = merged.merged_candidate_ids
            row["per_subquery_candidate_ids"] = per_ids
            row["per_subquery_scores"] = merged.per_subquery_scores
            row["merged_candidate_ids"] = merged_ids
            row["merged_candidate_count"] = len(merged_ids)
            row.update(
                {
                    key: value
                    for key, value in _rank_metrics(
                        merged_ids, case.relevant_ids, top_k=top_k
                    ).items()
                }
            )
            groups = case.need_coverage_groups or {
                f"Need{index + 1}": [document_id]
                for index, document_id in enumerate(case.relevant_ids)
            }

            need3, all3 = _coverage_metrics(merged_ids, groups, 3)
            need5, all5 = _coverage_metrics(merged_ids, groups, 5)
            row["need_recall_at_3"] = need3
            row["need_recall_at_5"] = need5
            row["all_needs_covered_at_3"] = all3
            row["all_needs_covered_at_5"] = all5
            row["any_hit_at_3"] = bool(set(case.relevant_ids).intersection(merged_ids[:3]))
            row["any_hit_at_5"] = bool(set(case.relevant_ids).intersection(merged_ids[:5]))
            row["duplicate_candidate_ratio"] = round(
                1
                - len(set(item for ids in per_ids for item in ids))
                / max(sum(len(ids) for ids in per_ids), 1),
                4,
            )
            row["latency_ms"] = round(merged.duration_ms, 3)
            row["score_aggregation"] = "max_then_soft_rerank"
            row["final_top_k"] = merged.final_top_k
            if baseline_planner is not None:
                baseline = await baseline_planner.retrieve(
                    retriever,
                    case.query,
                    limit=top_k,
                )
                baseline_ids = baseline.merged_candidate_ids
                baseline_payload = _retrieval_rank_payload(
                    baseline_ids,
                    case.relevant_ids,
                    top_k=top_k,
                    latency_ms=baseline.duration_ms,
                    merged_candidate_count=len(baseline_ids),
                )
                baseline_payload["final_top_k"] = baseline.final_top_k
                row["single_intent_baseline"] = baseline_payload
                # ``baseline`` is retained as a short compatibility alias for
                # consumers that already use that conventional field name.
                row["baseline"] = baseline_payload
                row["baseline_latency_ms"] = round(baseline.duration_ms, 3)
                row["latency_overhead_ms"] = round(
                    float(merged.duration_ms) - float(baseline.duration_ms), 3
                )
                row["latency_overhead_ratio"] = round(
                    (float(merged.duration_ms) - float(baseline.duration_ms))
                    / float(baseline.duration_ms),
                    4,
                ) if baseline.duration_ms else 0.0
                row["baseline_hit_at_3"] = bool(baseline_payload["hit_at_3"])
                row["baseline_mrr"] = float(baseline_payload["mrr"])
                row["baseline_ndcg_at_5"] = float(baseline_payload["ndcg_at_5"])
                row["hit_at_3_degradation"] = (
                    float(baseline_payload["hit_at_3"]) - float(row["hit_at_3"])
                    if not case.decompose_required
                    else None
                )
            groups_hit = set(
                group for group, ids in groups.items() if set(ids).intersection(merged_ids[:top_k])
            )
            if case.decompose_required and len(groups_hit) < len(groups):
                row["errors"].append("candidate_miss")
        rows.append(row)
    report: dict[str, Any] = {
        "schema_version": 1,
        "phase": 5,
        "generated_at": datetime.now(UTC).isoformat(),
        "case_count": len(rows),
        "lint": validate_multiquery_dataset(cases),
        "trigger": _binary_metrics(predicted, expected),
        "subquery_count_exact_accuracy": round(count_correct / len(rows), 4) if rows else 0.0,
        "cases": rows,
        "slices": _slice_reports(cases, rows),
        "error_attribution": _error_counts(rows, PHASE45_ERROR_ATTRIBUTION_CATEGORIES),
        "top_failures": [row for row in rows if row.get("errors")][:20],
        "latency": _latency_summary(rows),
        "retrieval_executed": retriever is not None,
    }
    retrieval_rows = [row for row in rows if "need_recall_at_5" in row]
    single_rows = [row for row in retrieval_rows if not row["expected_decompose"]]
    report["retrieval"] = {
        "need_recall_at_3": _mean_field(retrieval_rows, "need_recall_at_3"),
        "need_recall_at_5": _mean_field(retrieval_rows, "need_recall_at_5"),
        "all_needs_covered_at_3": _mean_bool(retrieval_rows, "all_needs_covered_at_3"),
        "all_needs_covered_at_5": _mean_bool(retrieval_rows, "all_needs_covered_at_5"),
        "any_hit_at_3": _mean_bool(retrieval_rows, "any_hit_at_3"),
        "any_hit_at_5": _mean_bool(retrieval_rows, "any_hit_at_5"),
        "duplicate_candidate_ratio": _mean_field(retrieval_rows, "duplicate_candidate_ratio"),
        "merged_candidate_count": _mean_field(retrieval_rows, "merged_candidate_count"),
        "mrr": _mean_field(retrieval_rows, "mrr"),
        "ndcg_at_3": _mean_field(retrieval_rows, "ndcg_at_3"),
        "ndcg_at_5": _mean_field(retrieval_rows, "ndcg_at_5"),
        "latency_overhead_ms": _mean_field(retrieval_rows, "latency_overhead_ms"),
        "latency_overhead_ratio": _mean_field(retrieval_rows, "latency_overhead_ratio"),
    }
    report["single_intent_control"] = {
        "unnecessary_decomposition_rate": round(
            mean(bool(row["predicted_decompose"]) for row in single_rows), 4
        )
        if single_rows
        else 0.0,
        "hit_at_3_degradation": _mean_field(single_rows, "hit_at_3_degradation"),
        "latency_overhead_ms": _mean_field(single_rows, "latency_overhead_ms"),
        "latency_overhead_ratio": _mean_field(single_rows, "latency_overhead_ratio"),
        "baseline_hit_at_3": _mean_field(single_rows, "baseline_hit_at_3"),
        "frozen_hit_at_3": _mean_field(single_rows, "hit_at_3"),
        "baseline_latency_ms": _mean_field(single_rows, "baseline_latency_ms"),
        "frozen_latency_ms": _mean_field(single_rows, "latency_ms"),
    }
    overhead_rows = [row for row in retrieval_rows if "latency_overhead_ms" in row]
    overhead_values = [float(row["latency_overhead_ms"]) for row in overhead_rows]
    report["latency_overhead"] = _latency_delta_summary(overhead_values)
    # Keep the overhead summary next to the existing latency summary for
    # report consumers that do not inspect the retrieval section.
    report["latency"]["overhead_ms"] = report["latency_overhead"]["mean_ms"]
    return report


def _mean_field(rows: Sequence[Mapping[str, Any]], key: str) -> float:
    return round(mean(float(row[key]) for row in rows), 4) if rows else 0.0


def _mean_bool(rows: Sequence[Mapping[str, Any]], key: str) -> float:
    return round(mean(bool(row[key]) for row in rows), 4) if rows else 0.0


def _token_set(value: str) -> set[str]:
    """Small language-agnostic token set for semantic-drift diagnostics.

    Chinese character bigrams make the check useful without introducing a new
    embedding or reranker.  This is a diagnostic guard, not a quality score.
    """

    normalized = re.sub(r"\s+", "", value.casefold())
    latin = set(re.findall(r"[a-z0-9]+", normalized))
    chinese = re.findall(r"[\u4e00-\u9fff]+", normalized)
    grams = {
        sequence[index : index + 2]
        for sequence in chinese
        for index in range(max(0, len(sequence) - 1))
    }
    return latin | grams


def _semantic_overlap(left: str, right: str) -> float:
    left_tokens, right_tokens = _token_set(left), _token_set(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _slice_reports(
    cases: Sequence[ContextualRewriteCase | MultiQueryCase],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    dimensions: dict[str, dict[str, list[int]]] = {
        "length_bucket": {},
        "query_type": {},
        "scenario": {},
        "goal": {},
    }
    for index, case in enumerate(cases):
        values: dict[str, list[str | None]] = {
            "length_bucket": [case.length_bucket],
            "query_type": [case.query_type],
            "scenario": [case.expected_scenario] if case.expected_scenario else [],
            "goal": list(case.expected_goals),
        }
        for dimension, keys in values.items():
            for key in keys:
                if key is None:
                    continue
                dimensions[dimension].setdefault(str(key), []).append(index)
    return {
        dimension: {
            key: _slice_summary(
                [cases[index] for index in indexes],
                [rows[index] for index in indexes],
            )
            for key, indexes in values.items()
        }
        for dimension, values in dimensions.items()
    }


def _slice_summary(
    cases: Sequence[ContextualRewriteCase | MultiQueryCase],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    expected_trigger = [
        bool(case.rewrite_required)
        if isinstance(case, ContextualRewriteCase)
        else bool(case.decompose_required)
        for case in cases
    ]
    predicted_trigger = (
        [
            bool(row.get("predicted_rewrite"))
            if isinstance(cases[0], ContextualRewriteCase)
            else bool(row.get("predicted_decompose"))
            for row in rows
        ]
        if cases
        else []
    )
    result: dict[str, Any] = {
        "count": len(cases),
        "trigger": _binary_metrics(predicted_trigger, expected_trigger),
        "error_count": sum(bool(row.get("errors")) for row in rows),
        "latency_ms": _latency_summary(rows),
    }
    if isinstance(cases[0], ContextualRewriteCase) if cases else False:
        result["query_drift_rate"] = _mean_bool(rows, "query_drift")
        if any("retrieval_gain" in row for row in rows):
            result["retrieval_gain"] = _mean_gain([row for row in rows if "retrieval_gain" in row])
    else:
        result["subquery_quality_rate"] = (
            round(mean(bool(row.get("subquery_quality_ok", False)) for row in rows), 4)
            if rows
            else 0.0
        )
        if any("need_recall_at_5" in row for row in rows):
            result["need_recall_at_5"] = _mean_field(rows, "need_recall_at_5")
            result["all_needs_covered_at_5"] = _mean_bool(rows, "all_needs_covered_at_5")
    return result


def _latency_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, float | int]:
    values: list[float] = []
    for row in rows:
        value = row.get("latency_ms", row.get("duration_ms"))
        if isinstance(value, (int, float)):
            values.append(float(value))
            continue
        for key in ("raw", "rewritten"):
            nested = row.get(key)
            if isinstance(nested, Mapping) and isinstance(nested.get("latency_ms"), (int, float)):
                values.append(float(nested["latency_ms"]))
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0}
    p50 = ordered[min(len(ordered) - 1, int((len(ordered) - 1) * 0.50))]
    p95 = ordered[min(len(ordered) - 1, int((len(ordered) - 1) * 0.95))]
    return {
        "count": len(ordered),
        "mean": round(mean(ordered), 3),
        "p50": round(p50, 3),
        "p95": round(p95, 3),
    }


def _latency_delta_summary(values: Sequence[float]) -> dict[str, float | int]:
    """Summarise current-minus-baseline latency deltas in milliseconds."""

    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {
            "count": 0,
            "mean_ms": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
        }
    p50 = ordered[min(len(ordered) - 1, int((len(ordered) - 1) * 0.50))]
    p95 = ordered[min(len(ordered) - 1, int((len(ordered) - 1) * 0.95))]
    return {
        "count": len(ordered),
        "mean_ms": round(mean(ordered), 3),
        "p50_ms": round(p50, 3),
        "p95_ms": round(p95, 3),
    }


def _error_counts(rows: Sequence[Mapping[str, Any]], categories: Sequence[str]) -> dict[str, int]:
    counts = Counter(error for row in rows for error in row.get("errors", []))
    return {category: counts.get(category, 0) for category in categories}


PHASE45_ERROR_ATTRIBUTION_CATEGORIES: tuple[str, ...] = (
    "router_branch_error",
    "router_scenario_error",
    "router_goal_error",
    "safety_error",
    "rewrite_trigger_error",
    "query_drift",
    "decomposition_trigger_error",
    "subquery_quality_error",
    "candidate_miss",
    "rerank_error",
    "gold_or_dataset_issue",
)


def _coverage_metrics(
    merged_ids: Sequence[str], groups: Mapping[str, Sequence[str]], k: int
) -> tuple[float, bool]:
    top = set(merged_ids[:k])
    hits = sum(bool(top.intersection(ids)) for ids in groups.values())
    total = len(groups)
    return (
        round(hits / total, 4) if total else 0.0,
        bool(total and hits == total),
    )


def render_contextual_rewrite_report(report: Mapping[str, Any]) -> str:
    trigger = report.get("trigger", {})
    lines = [
        "# Phase 4 Contextual Rewrite Evaluation",
        "",
        f"- Cases: {report.get('case_count', 0)}",
        f"- Trigger Precision / Recall / F1: {trigger.get('precision', 0)} / "
        f"{trigger.get('recall', 0)} / {trigger.get('f1', 0)}",
        f"- Query drift rate: {report.get('query_drift_rate', 0)}",
    ]
    if report.get("retrieval_gain") is not None:
        lines.append(
            "- Rewrite retrieval gain: `"
            f"{json.dumps(report['retrieval_gain'], ensure_ascii=False)}`"
        )
    if report.get("standalone_control") is not None:
        lines.append(
            "- Standalone control: `"
            f"{json.dumps(report['standalone_control'], ensure_ascii=False)}`"
        )
        control = report["standalone_control"]
        if isinstance(control, Mapping):
            lines.append(
                f"- Passthrough accuracy: {control.get('passthrough_accuracy', 0)}; "
                f"Hit@3 degradation: {control.get('hit_at_3_degradation', 0)}"
            )
    _append_phase45_diagnostics(lines, report, phase=4)
    return "\n".join(lines) + "\n"


def render_multiquery_report(report: Mapping[str, Any]) -> str:
    trigger = report.get("trigger", {})
    retrieval = report.get("retrieval", {})
    control = report.get("single_intent_control", {})
    latency_overhead = report.get("latency_overhead", {})
    lines = [
        "# Phase 5 Multi-query Evaluation",
        "",
        f"- Cases: {report.get('case_count', 0)}",
        f"- Trigger Precision / Recall / F1: {trigger.get('precision', 0)} / "
        f"{trigger.get('recall', 0)} / {trigger.get('f1', 0)}",
        f"- Subquery count exact accuracy: {report.get('subquery_count_exact_accuracy', 0)}",
        f"- NeedRecall@5: {retrieval.get('need_recall_at_5', 0)}",
        f"- AllNeedsCovered@5: {retrieval.get('all_needs_covered_at_5', 0)}",
        f"- Duplicate candidate ratio: {retrieval.get('duplicate_candidate_ratio', 0)}",
        f"- MRR: {retrieval.get('mrr', 0)}",
        f"- nDCG@3 / nDCG@5: {retrieval.get('ndcg_at_3', 0)} / "
        f"{retrieval.get('ndcg_at_5', 0)}",
        f"- Merged candidate count (mean): {retrieval.get('merged_candidate_count', 0)}",
        f"- Latency overhead (mean ms): {latency_overhead.get('mean_ms', 0)}",
        f"- Single-intent Hit@3 degradation: {control.get('hit_at_3_degradation', 0)}",
        f"- Single-intent latency overhead (mean ms): "
        f"{control.get('latency_overhead_ms', 0)}",
    ]
    _append_phase45_diagnostics(lines, report, phase=5)
    return "\n".join(lines) + "\n"


def _append_phase45_diagnostics(
    lines: list[str], report: Mapping[str, Any], *, phase: int
) -> None:
    """Append the required human-readable slices and failure diagnostics."""

    lines.extend(["", "## Slices", ""])
    slices = report.get("slices")
    if isinstance(slices, Mapping) and slices:
        for dimension, values in slices.items():
            if not isinstance(values, Mapping):
                continue
            lines.append(f"### {dimension}")
            for name, summary in values.items():
                if not isinstance(summary, Mapping):
                    lines.append(f"- `{name}`: {summary}")
                    continue
                details = [f"count={summary.get('count', 0)}"]
                trigger = summary.get("trigger")
                if isinstance(trigger, Mapping):
                    details.append(f"trigger_f1={trigger.get('f1', 0)}")
                if phase == 4:
                    details.append(f"drift={summary.get('query_drift_rate', 0)}")
                    gain = summary.get("retrieval_gain")
                    if isinstance(gain, Mapping):
                        details.append(f"hit_at_3_gain={gain.get('hit_at_3', 0)}")
                else:
                    details.append(f"subquery_quality={summary.get('subquery_quality_rate', 0)}")
                    if "need_recall_at_5" in summary:
                        details.append(f"need_recall_at_5={summary.get('need_recall_at_5', 0)}")
                        details.append(
                            f"all_needs_covered_at_5={summary.get('all_needs_covered_at_5', 0)}"
                        )
                latency = summary.get("latency_ms")
                if isinstance(latency, Mapping):
                    details.append(f"latency_mean_ms={latency.get('mean', 0)}")
                lines.append(f"- `{name}`: " + "; ".join(details))
            lines.append("")
    else:
        lines.append("- none")

    lines.extend(["## Error attribution", "", "| Error | Count |", "| --- | ---: |"])
    errors = report.get("error_attribution")
    if isinstance(errors, Mapping) and errors:
        for name, count in errors.items():
            lines.append(f"| {name} | {count} |")
    else:
        lines.append("| none | 0 |")

    lines.extend(["", "## Top failures", ""])
    failures = report.get("top_failures")
    if isinstance(failures, Sequence) and not isinstance(failures, (str, bytes)) and failures:
        for failure in failures:
            if isinstance(failure, Mapping):
                errors_value = failure.get("errors", [])
                if isinstance(errors_value, Sequence) and not isinstance(
                    errors_value, (str, bytes)
                ):
                    error_text = ", ".join(str(item) for item in errors_value) or "unspecified"
                else:
                    error_text = str(errors_value)
                lines.append(f"- {failure.get('id', '')}: {error_text}")
            else:
                lines.append(f"- {failure}")
    else:
        lines.append("- none")

    lines.extend(["", "## Latency", ""])
    latency = report.get("latency")
    if isinstance(latency, Mapping):
        lines.append(
            f"- count={latency.get('count', 0)}; "
            f"mean={latency.get('mean', latency.get('mean_ms', 0))} ms; "
            f"p50={latency.get('p50', latency.get('p50_ms', 0))} ms; "
            f"p95={latency.get('p95', latency.get('p95_ms', 0))} ms"
        )
    else:
        lines.append("- unavailable")


def write_phase45_report(
    report: Mapping[str, Any], output_path: Path, *, phase: int
) -> tuple[Path, Path]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    json_path = (
        output_path
        if output_path.suffix.casefold() == ".json"
        else output_path.with_suffix(".json")
    )
    markdown_path = json_path.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    renderer = render_contextual_rewrite_report if phase == 4 else render_multiquery_report
    markdown_path.write_text(renderer(report), encoding="utf-8")
    return json_path, markdown_path


# Explicit aliases make the split-specific CLI and external scripts readable.
evaluate_contextual_rewrite_v1 = evaluate_contextual_rewrite
evaluate_multiquery_v1 = evaluate_multiquery


__all__ = [
    "PHASE45_ERROR_ATTRIBUTION_CATEGORIES",
    "ContextualRewriteCase",
    "MultiQueryCase",
    "evaluate_contextual_rewrite",
    "evaluate_contextual_rewrite_v1",
    "evaluate_multiquery",
    "evaluate_multiquery_v1",
    "load_contextual_rewrite_eval_markdown",
    "load_multiquery_eval_markdown",
    "ndcg_at_k",
    "parse_contextual_rewrite_eval_markdown",
    "parse_multiquery_eval_markdown",
    "render_contextual_rewrite_report",
    "render_multiquery_report",
    "validate_contextual_rewrite_dataset",
    "validate_multiquery_dataset",
    "validate_phase45_datasets",
    "write_phase45_report",
]

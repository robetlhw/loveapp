from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from loveapp.application.routing import HybridRouter
from loveapp.application.scenario_policy import (
    ScenarioPolicyRegistry,
    default_scenario_policy_registry,
)
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.enums import (
    AdviceGoal,
    AdviceScenario,
    RelationshipStage,
    RiskLevel,
    TaskType,
)
from loveapp.domain.knowledge import KnowledgeDocument, KnowledgeFilters, RetrievedDocument
from loveapp.domain.routing import RouteInput
from loveapp.ports.knowledge import KnowledgeRetriever
from loveapp.ports.routing import Router
from loveapp.safety import SafetyPolicy

_CASE_HEADING = re.compile(r"^##\s+(rag_v2_(?:dev|test)_\d+)\s*$")
_FIELD = re.compile(r"^\*\*([^*]+):\*\*\s*(.*?)\s*$")
_LIST_SEPARATOR = re.compile(r"\s*[,，、]\s*")
_QUERY_TYPES = frozenset(
    {
        "paraphrase",
        "colloquial",
        "hard_confusion",
        "multi_scenario",
        "multi_goal",
        "long_context",
        "noisy_typo",
        "no_answer",
    }
)
_DIFFICULTIES = frozenset({"easy", "medium", "hard"})
_TARGETS = {
    "hit_at_1": (">=", 0.80),
    "hit_at_3": (">=", 0.92),
    "hit_at_5": (">=", 0.96),
    "mrr": (">=", 0.86),
    "ndcg_at_5": (">=", 0.90),
    "hard_confusion_hit_at_3": (">=", 0.85),
    "hard_negative_leakage_at_3": ("<=", 0.10),
    "no_answer_f1": (">=", 0.85),
    "false_retrieval_rate": ("<=", 0.10),
    "coverage": (">=", 0.95),
}


class RagExpectedBranch(StrEnum):
    RAG = "rag"
    SAFETY = "safety"
    OUT_OF_SCOPE = "out_of_scope"


class RagNoAnswerScope(StrEnum):
    IN_DOMAIN_UNCOVERED = "in_domain_uncovered"
    OUT_OF_DOMAIN = "out_of_domain"


class RagEvalCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    query_type: str
    difficulty: str
    expected_branch: RagExpectedBranch
    expected_primary_scenario: AdviceScenario | None
    expected_secondary_scenarios: list[AdviceScenario] = Field(default_factory=list)
    relationship_stage: RelationshipStage
    expected_goals: list[AdviceGoal] = Field(default_factory=list)
    expected_risk_level: RiskLevel
    no_answer: bool
    no_answer_scope: RagNoAnswerScope | None = None
    query: str = Field(min_length=1)
    relevant_ids: list[str] = Field(default_factory=list)
    graded_relevance: dict[str, int] = Field(default_factory=dict)
    hard_negative_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_contract(self) -> RagEvalCase:
        if self.query_type not in _QUERY_TYPES:
            raise ValueError(f"QueryType 非法：{self.query_type}")
        if self.difficulty not in _DIFFICULTIES:
            raise ValueError(f"Difficulty 非法：{self.difficulty}")
        if any(grade not in {0, 1, 2, 3} for grade in self.graded_relevance.values()):
            raise ValueError("GradedRelevance 只允许 0/1/2/3")
        expected_relevant = {
            document_id
            for document_id, grade in self.graded_relevance.items()
            if grade >= 2
        }
        if set(self.relevant_ids) != expected_relevant:
            raise ValueError("RelevantIDs 必须等于 GradedRelevance 中 grade>=2 的集合")
        if self.no_answer and self.no_answer_scope is None:
            raise ValueError("NoAnswer=true 时必须填写 NoAnswerScope")
        if not self.no_answer and self.no_answer_scope is not None:
            raise ValueError("NoAnswer=false 时 NoAnswerScope 必须为空")
        if (
            self.no_answer_scope == RagNoAnswerScope.IN_DOMAIN_UNCOVERED
            and (self.expected_branch != RagExpectedBranch.RAG or self.relevant_ids)
        ):
            raise ValueError("in_domain_uncovered 必须 ExpectedBranch=rag 且 RelevantIDs=[]")
        if (
            self.no_answer_scope == RagNoAnswerScope.OUT_OF_DOMAIN
            and (self.expected_branch != RagExpectedBranch.OUT_OF_SCOPE or self.relevant_ids)
        ):
            raise ValueError("out_of_domain 必须 ExpectedBranch=out_of_scope 且 RelevantIDs=[]")
        return self


class RagEvalResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    returned: list[RetrievedDocument] = Field(default_factory=list)
    nearest_candidates: list[RetrievedDocument] = Field(default_factory=list)
    candidates: list[RetrievedDocument] = Field(default_factory=list)
    reranked_candidates: list[RetrievedDocument] = Field(default_factory=list)
    duration_ms: float = Field(default=0, ge=0)
    retrieval_duration_ms: float | None = Field(default=None, ge=0)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    predicted_branch: RagExpectedBranch | None = None
    predicted_scenario: AdviceScenario | None = None
    predicted_goals: list[AdviceGoal] = Field(default_factory=list)
    retrieval_called: bool = True
    diagnostics_available: bool = False


RagExecutor = Callable[[RagEvalCase], Awaitable[RagEvalResult]]


def load_rag_eval_markdown(path: Path) -> list[RagEvalCase]:
    return parse_rag_eval_markdown(path.read_text(encoding="utf-8-sig"), source_ref=path.name)


def parse_rag_eval_markdown(text: str, *, source_ref: str = "<string>") -> list[RagEvalCase]:
    blocks: list[tuple[str, list[str]]] = []
    current_id: str | None = None
    current_lines: list[str] = []
    for raw_line in text.splitlines():
        heading = _CASE_HEADING.match(raw_line.strip())
        if heading:
            if current_id is not None:
                blocks.append((current_id, current_lines))
            current_id = heading.group(1)
            current_lines = []
        elif current_id is not None:
            current_lines.append(raw_line.rstrip())
    if current_id is not None:
        blocks.append((current_id, current_lines))
    if not blocks:
        raise ValueError(f"RAG V2 评测文件没有 case：{source_ref}")

    cases: list[RagEvalCase] = []
    for case_id, lines in blocks:
        fields: dict[str, str] = {}
        for line in lines:
            match = _FIELD.match(line.strip())
            if match:
                fields[match.group(1)] = match.group(2).strip()
        try:
            cases.append(_parse_case(case_id, fields))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"评测 case {case_id} 非法：{exc}") from exc
    _ensure_unique_cases(cases, source_ref)
    return cases


def _parse_case(case_id: str, fields: dict[str, str]) -> RagEvalCase:
    required = {
        "QueryType",
        "Difficulty",
        "ExpectedBranch",
        "ExpectedPrimaryScenario",
        "ExpectedSecondaryScenarios",
        "RelationshipStage",
        "ExpectedGoals",
        "ExpectedRiskLevel",
        "NoAnswer",
        "Query",
        "RelevantIDs",
        "GradedRelevance",
        "HardNegativeIDs",
    }
    missing = sorted(required - fields.keys())
    if missing:
        raise ValueError(f"缺少字段：{', '.join(missing)}")
    no_answer = _parse_bool(fields["NoAnswer"])
    scope_value = fields.get("NoAnswerScope", "").strip()
    no_answer_scope = (
        None
        if scope_value.casefold() in {"", "null", "none"}
        else RagNoAnswerScope(scope_value)
    )
    return RagEvalCase(
        id=case_id,
        query_type=fields["QueryType"],
        difficulty=fields["Difficulty"],
        expected_branch=RagExpectedBranch(fields["ExpectedBranch"]),
        expected_primary_scenario=(
            None
            if fields["ExpectedPrimaryScenario"].casefold() == "null"
            else AdviceScenario(fields["ExpectedPrimaryScenario"])
        ),
        expected_secondary_scenarios=[
            AdviceScenario(value) for value in _parse_list(fields["ExpectedSecondaryScenarios"])
        ],
        relationship_stage=RelationshipStage(fields["RelationshipStage"]),
        expected_goals=[AdviceGoal(value) for value in _parse_list(fields["ExpectedGoals"])],
        expected_risk_level=RiskLevel(fields["ExpectedRiskLevel"]),
        no_answer=no_answer,
        no_answer_scope=no_answer_scope,
        query=fields["Query"],
        relevant_ids=_parse_list(fields["RelevantIDs"]),
        graded_relevance=_parse_grades(fields["GradedRelevance"]),
        hard_negative_ids=_parse_list(fields["HardNegativeIDs"]),
    )


def _parse_bool(value: str) -> bool:
    normalized = value.casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"布尔值非法：{value}")


def _parse_list(value: str) -> list[str]:
    value = value.strip()
    if value in {"", "[]"}:
        return []
    return list(dict.fromkeys(item for item in _LIST_SEPARATOR.split(value) if item))


def _parse_grades(value: str) -> dict[str, int]:
    value = value.strip()
    if value in {"", "{}"}:
        return {}
    result: dict[str, int] = {}
    for item in _LIST_SEPARATOR.split(value):
        document_id, separator, grade = item.partition("=")
        if not separator or not document_id.strip():
            raise ValueError(f"GradedRelevance 格式非法：{item}")
        normalized_id = document_id.strip()
        if normalized_id in result:
            raise ValueError(f"GradedRelevance 文档 ID 重复：{normalized_id}")
        result[normalized_id] = int(grade.strip())
    return result


def _ensure_unique_cases(cases: Sequence[RagEvalCase], source_ref: str) -> None:
    duplicate_ids = _duplicates(case.id for case in cases)
    duplicate_queries = _duplicates(_canonical(case.query) for case in cases)
    if duplicate_ids:
        raise ValueError(f"{source_ref} case ID 重复：{', '.join(sorted(duplicate_ids))}")
    if duplicate_queries:
        raise ValueError(f"{source_ref} Query 重复：{len(duplicate_queries)} 组")


def validate_rag_v2_dataset(
    documents: Sequence[KnowledgeDocument],
    dev_cases: Sequence[RagEvalCase],
    test_cases: Sequence[RagEvalCase],
    *,
    require_exact_counts: bool = True,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    document_ids = [document.id for document in documents]
    if require_exact_counts and len(documents) != 500:
        errors.append(f"V2 KB 文档数应为 500，实际 {len(documents)}")
    if require_exact_counts and (len(dev_cases), len(test_cases)) != (300, 200):
        errors.append(
            f"V2 Dev/Test 数量应为 300/200，实际 {len(dev_cases)}/{len(test_cases)}"
        )
    duplicate_document_ids = _duplicates(document_ids)
    if duplicate_document_ids:
        errors.append(f"KB ID 重复：{', '.join(sorted(duplicate_document_ids))}")
    duplicate_questions = _duplicates(_canonical(document.question) for document in documents)
    if duplicate_questions:
        errors.append(f"KB canonical question 重复：{len(duplicate_questions)} 组")
    variant_counts = [len(document.query_variants) for document in documents]
    invalid_variants = [
        document.id for document in documents if not 3 <= len(document.query_variants) <= 5
    ]
    if invalid_variants:
        errors.append(f"QueryVariants 数量不在 3~5：{', '.join(invalid_variants[:20])}")
    incomplete_documents = [
        document.id
        for document in documents
        if document.version.startswith("2")
        and not all(
            (
                document.id,
                document.title,
                document.relationship_stages,
                document.goals,
                document.tags,
                document.question,
                document.answer,
                document.context,
                document.principles,
                document.recommended_actions,
                document.avoid_actions,
                document.clarifying_questions,
            )
        )
    ]
    if incomplete_documents:
        errors.append(f"V2 必填字段不完整：{', '.join(incomplete_documents[:20])}")

    kb_ids = set(document_ids)
    all_cases = [*dev_cases, *test_cases]
    referenced_ids = {
        document_id
        for case in all_cases
        for document_id in [
            *case.relevant_ids,
            *case.graded_relevance.keys(),
            *case.hard_negative_ids,
        ]
    }
    unknown_ids = referenced_ids - kb_ids
    if unknown_ids:
        errors.append(f"评测引用不存在的 KB ID：{', '.join(sorted(unknown_ids))}")
    dev_ids = {case.id for case in dev_cases}
    test_ids = {case.id for case in test_cases}
    if dev_ids & test_ids:
        errors.append("Dev/Test case ID 有重叠")
    dev_queries = {_canonical(case.query) for case in dev_cases}
    test_queries = {_canonical(case.query) for case in test_cases}
    if dev_queries & test_queries:
        errors.append("Dev/Test Query 有重叠")
    kb_expressions = {
        _canonical(text)
        for document in documents
        for text in [document.question, *document.query_variants]
    }
    leaked_cases = [case.id for case in all_cases if _canonical(case.query) in kb_expressions]
    if leaked_cases:
        errors.append(f"Eval Query 与 KB question/query variant 精确泄漏：{leaked_cases[:20]}")

    expected_scope_counts = {
        "dev": {"in_domain_uncovered": 24, "out_of_domain": 6},
        "test": {"in_domain_uncovered": 16, "out_of_domain": 4},
    }
    actual_scope_counts: dict[str, dict[str, int]] = {}
    for split, cases in (("dev", dev_cases), ("test", test_cases)):
        counts = defaultdict(int)
        for case in cases:
            if case.no_answer_scope is not None:
                counts[case.no_answer_scope.value] += 1
        actual_scope_counts[split] = dict(counts)
        if require_exact_counts and dict(counts) != expected_scope_counts[split]:
            errors.append(
                f"{split} NoAnswerScope 分布错误：实际 {dict(counts)}，"
                f"期望 {expected_scope_counts[split]}"
            )

    answer_lengths = [len(document.answer) for document in documents]
    retrieval_lengths = [len(document.retrieval_text) for document in documents]
    if answer_lengths and (min(answer_lengths) < 100 or max(answer_lengths) > 600):
        warnings.append("存在异常短/长 Answer，请人工复核；未自动截断")
    near_duplicates = [
        *_near_duplicate_pairs(
            [(document.id, document.question) for document in documents],
            category="knowledge_question",
        ),
        *_near_duplicate_pairs(
            [(case.id, case.query) for case in all_cases],
            category="eval_query",
        ),
    ]
    near_duplicate_errors = [pair for pair in near_duplicates if pair["similarity"] >= 0.90]
    near_duplicate_warnings = [
        pair for pair in near_duplicates if 0.82 <= pair["similarity"] < 0.90
    ]
    if near_duplicate_errors:
        errors.append(f"存在 >=0.90 的近重复文本：{len(near_duplicate_errors)} 对")
    if near_duplicate_warnings:
        warnings.append(
            f"存在 0.82~0.90 的近重复文本：{len(near_duplicate_warnings)} 对，请人工复核"
        )
    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "knowledge": len(documents),
            "dev": len(dev_cases),
            "test": len(test_cases),
            "query_variants_min": min(variant_counts, default=0),
            "query_variants_max": max(variant_counts, default=0),
            "no_answer_scope": actual_scope_counts,
        },
        "lengths": {
            "answer": _distribution(answer_lengths),
            "retrieval_text": _distribution(retrieval_lengths),
        },
        "near_duplicate_review": near_duplicates,
    }


async def evaluate_rag_v2(
    cases: Sequence[RagEvalCase],
    *,
    mode: Literal["retriever", "e2e"] = "retriever",
    executor: RagExecutor | None = None,
    retriever: KnowledgeRetriever | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    if mode not in {"retriever", "e2e"}:
        raise ValueError(f"unsupported RAG evaluation mode: {mode}")
    if executor is None:
        if mode == "e2e":
            raise ValueError("E2E 模式必须显式提供 Router -> Policy -> Retriever executor")
        if retriever is None:
            raise ValueError("executor 和 retriever 至少提供一个")
        executor = _retriever_executor(retriever, top_k=top_k)
    if mode == "e2e" and getattr(executor, "_rag_v2_diagnostic_only", False):
        raise ValueError("rule-only executor is diagnostic-only and cannot produce an E2E score")

    rows: list[dict[str, Any]] = []
    for case in cases:
        if mode == "retriever" and case.expected_branch != RagExpectedBranch.RAG:
            continue
        result = await executor(case)
        rows.append(_case_row(case, result, mode=mode, top_k=top_k))

    report = _aggregate_report(rows, mode=mode, top_k=top_k)
    retrieval_rows = [row for row in rows if row["retrieval_called"]]
    cold_start_ms = (
        retrieval_rows[0]["retrieval_duration_ms"] if retrieval_rows else None
    )
    warm_retrieval_rows = retrieval_rows[1:]
    warm_latencies = [
        row["retrieval_duration_ms"]
        for row in warm_retrieval_rows
        if row["retrieval_duration_ms"] is not None
    ]
    report.update(
        {
            "schema_version": 2,
            "generated_at": datetime.now(UTC).isoformat(),
            "mode": mode,
            "case_count": len(rows),
            "cold_start_ms": (
                round(cold_start_ms, 3) if cold_start_ms is not None else None
            ),
            "warm_latency_ms": _latency(warm_latencies),
            "stage_latency_ms": _stage_latencies(warm_retrieval_rows),
            "retrieval_latency_denominators": {
                "cold": 1 if cold_start_ms is not None else 0,
                "warm": len(warm_latencies),
            },
            "cases": rows,
        }
    )
    report["targets"] = evaluate_rag_targets(report)
    return report


def _retriever_executor(retriever: KnowledgeRetriever, *, top_k: int) -> RagExecutor:
    async def execute(case: RagEvalCase) -> RagEvalResult:
        filters = KnowledgeFilters(
            scenario=case.expected_primary_scenario,
            scenarios=case.expected_secondary_scenarios,
            relationship_stage=case.relationship_stage,
            goals=case.expected_goals,
        )
        trace = ExecutionTrace()
        started = perf_counter()
        detailed_search = getattr(retriever, "search_detailed", None)
        if callable(detailed_search):
            detailed = await detailed_search(
                case.query,
                filters=filters,
                limit=top_k,
                trace=trace,
            )
            returned = detailed.returned
            nearest_candidates = detailed.nearest_candidates
            candidates = detailed.candidates
            reranked_candidates = detailed.reranked_candidates
            diagnostics_available = True
        else:
            returned = await retriever.search(
                case.query,
                filters=filters,
                limit=top_k,
                trace=trace,
            )
            candidates = []
            nearest_candidates = []
            reranked_candidates = []
            diagnostics_available = False
        duration_ms = (perf_counter() - started) * 1000
        return RagEvalResult(
            returned=returned,
            nearest_candidates=nearest_candidates,
            candidates=candidates,
            reranked_candidates=reranked_candidates,
            duration_ms=duration_ms,
            retrieval_duration_ms=duration_ms,
            trace=[record.model_dump(mode="json") for record in trace.snapshot()],
            predicted_branch=RagExpectedBranch.RAG,
            diagnostics_available=diagnostics_available,
        )

    return execute


def build_e2e_rule_executor(
    retriever: KnowledgeRetriever,
    *,
    top_k: int = 5,
) -> RagExecutor:
    """Build a deterministic routing diagnostic, not an official E2E evaluator."""

    router = HybridRouter(SafetyPolicy(), corrector=None)
    executor = build_e2e_executor(router, retriever, top_k=top_k)
    executor._rag_v2_diagnostic_only = True  # type: ignore[attr-defined]
    return executor


def build_e2e_executor(
    router: Router,
    retriever: KnowledgeRetriever,
    *,
    policy_registry: ScenarioPolicyRegistry | None = None,
    top_k: int = 5,
) -> RagExecutor:
    registry = policy_registry or default_scenario_policy_registry()

    async def execute(case: RagEvalCase) -> RagEvalResult:
        started = perf_counter()
        route = await router.route(RouteInput(latest_query=case.query))
        if route.risk_level in {RiskLevel.HIGH, RiskLevel.SENSITIVE}:
            return RagEvalResult(
                duration_ms=(perf_counter() - started) * 1000,
                predicted_branch=RagExpectedBranch.SAFETY,
                predicted_scenario=route.primary_scenario,
                predicted_goals=_route_goals(route.primary_goal, route.secondary_goals),
                retrieval_called=False,
            )
        if route.task_type == TaskType.OUT_OF_SCOPE:
            return RagEvalResult(
                duration_ms=(perf_counter() - started) * 1000,
                predicted_branch=RagExpectedBranch.OUT_OF_SCOPE,
                predicted_scenario=route.primary_scenario,
                predicted_goals=_route_goals(route.primary_goal, route.secondary_goals),
                retrieval_called=False,
            )
        if route.task_type != TaskType.RELATIONSHIP_ADVICE or route.primary_scenario is None:
            return RagEvalResult(
                duration_ms=(perf_counter() - started) * 1000,
                predicted_branch=RagExpectedBranch.OUT_OF_SCOPE,
                predicted_scenario=route.primary_scenario,
                predicted_goals=_route_goals(route.primary_goal, route.secondary_goals),
                retrieval_called=False,
            )
        policy = registry.resolve(
            route.primary_scenario,
            route.secondary_scenarios,
            route.primary_goal,
            route.secondary_goals,
        )
        scenario_weights = {
            scenario: limit / policy.total_document_limit
            for scenario, limit in policy.retrieval_limits.items()
        }
        retrieval_limit = min(policy.total_document_limit, top_k)
        trace = ExecutionTrace()
        filters = KnowledgeFilters(
            scenario=route.primary_scenario,
            scenarios=route.secondary_scenarios,
            relationship_stage=case.relationship_stage,
            goal=route.primary_goal,
            goals=route.secondary_goals,
            scenario_weights=scenario_weights,
        )
        retrieval_started = perf_counter()
        detailed_search = getattr(retriever, "search_detailed", None)
        if callable(detailed_search):
            detailed = await detailed_search(
                case.query,
                filters=filters,
                limit=retrieval_limit,
                trace=trace,
            )
            returned = detailed.returned
            nearest_candidates = detailed.nearest_candidates
            candidates = detailed.candidates
            reranked_candidates = detailed.reranked_candidates
            diagnostics_available = True
        else:
            returned = await retriever.search(
                case.query,
                filters=filters,
                limit=retrieval_limit,
                trace=trace,
            )
            candidates = []
            nearest_candidates = []
            reranked_candidates = []
            diagnostics_available = False
        retrieval_duration_ms = (perf_counter() - retrieval_started) * 1000
        return RagEvalResult(
            returned=returned,
            nearest_candidates=nearest_candidates,
            candidates=candidates,
            reranked_candidates=reranked_candidates,
            duration_ms=(perf_counter() - started) * 1000,
            retrieval_duration_ms=retrieval_duration_ms,
            trace=[record.model_dump(mode="json") for record in trace.snapshot()],
            predicted_branch=RagExpectedBranch.RAG,
            predicted_scenario=route.primary_scenario,
            predicted_goals=_route_goals(route.primary_goal, route.secondary_goals),
            diagnostics_available=diagnostics_available,
        )

    return execute


def _route_goals(primary: AdviceGoal | None, secondary: list[AdviceGoal]) -> list[AdviceGoal]:
    return list(dict.fromkeys([*([primary] if primary else []), *secondary]))


def _case_row(
    case: RagEvalCase,
    result: RagEvalResult,
    *,
    mode: Literal["retriever", "e2e"],
    top_k: int,
) -> dict[str, Any]:
    returned_ids = [item.document.id for item in result.returned]
    nearest_candidate_ids = [item.document.id for item in result.nearest_candidates]
    candidate_ids = [item.document.id for item in result.candidates]
    reranked_candidate_ids = [item.document.id for item in result.reranked_candidates]
    answered_case = case.expected_branch == RagExpectedBranch.RAG and not case.no_answer
    in_domain_no_answer = case.no_answer_scope == RagNoAnswerScope.IN_DOMAIN_UNCOVERED
    rag_retrieval = result.retrieval_called and (
        result.predicted_branch == RagExpectedBranch.RAG
        or (mode == "retriever" and result.predicted_branch is None)
    )
    abstained = rag_retrieval and not result.returned
    covered = rag_retrieval and bool(result.returned)
    metrics = retrieval_metrics(
        returned_ids,
        relevant_ids=case.relevant_ids,
        graded_relevance=case.graded_relevance,
        hard_negative_ids=case.hard_negative_ids,
        nearest_candidate_ids=nearest_candidate_ids,
        candidate_ids=candidate_ids,
        reranked_candidate_ids=reranked_candidate_ids,
        diagnostics_available=result.diagnostics_available,
        top_k=top_k,
    )
    branch_correct = (
        result.predicted_branch == case.expected_branch
        if result.predicted_branch is not None
        else None
    )
    ordinary_rag_bypass_violation = bool(
        case.expected_branch == RagExpectedBranch.SAFETY and result.retrieval_called
    )
    return {
        "id": case.id,
        "evaluation_mode": mode,
        "query": case.query,
        "query_type": case.query_type,
        "difficulty": case.difficulty,
        "scenario": (
            case.expected_primary_scenario.value
            if case.expected_primary_scenario is not None
            else "none"
        ),
        "stage": case.relationship_stage.value,
        "goals": [goal.value for goal in case.expected_goals],
        "risk_level": case.expected_risk_level.value,
        "relevant_ids": case.relevant_ids,
        "hard_negative_ids": case.hard_negative_ids,
        "expected_branch": case.expected_branch.value,
        "predicted_branch": (
            result.predicted_branch.value if result.predicted_branch is not None else None
        ),
        "branch_correct": branch_correct,
        "predicted_scenario": (
            result.predicted_scenario.value if result.predicted_scenario is not None else None
        ),
        "predicted_goals": [goal.value for goal in result.predicted_goals],
        "answered_case": answered_case,
        "in_domain_no_answer": in_domain_no_answer,
        "out_of_domain": case.no_answer_scope == RagNoAnswerScope.OUT_OF_DOMAIN,
        "abstained": abstained,
        "covered": covered,
        "ordinary_rag_bypass_violation": ordinary_rag_bypass_violation,
        "retrieval_called": result.retrieval_called,
        "diagnostics_available": result.diagnostics_available,
        "duration_ms": round(result.duration_ms, 3),
        "retrieval_duration_ms": (
            round(result.retrieval_duration_ms, 3)
            if result.retrieval_duration_ms is not None
            else None
        ),
        "returned_ids": returned_ids,
        "nearest_candidate_ids": nearest_candidate_ids,
        "candidate_ids": candidate_ids,
        "reranked_candidate_ids": reranked_candidate_ids,
        "returned": [
            {
                "id": item.document.id,
                "score": item.score,
                "base_score": item.base_score,
                "score_components": item.score_components,
            }
            for item in result.returned
        ],
        "nearest_candidates": [
            {
                "id": item.document.id,
                "score": item.score,
                "base_score": item.base_score,
                "score_components": item.score_components,
            }
            for item in result.nearest_candidates
        ],
        "trace": result.trace,
        **metrics,
    }


def retrieval_metrics(
    returned_ids: Sequence[str],
    *,
    relevant_ids: Sequence[str],
    graded_relevance: dict[str, int],
    hard_negative_ids: Sequence[str] = (),
    nearest_candidate_ids: Sequence[str] = (),
    candidate_ids: Sequence[str] = (),
    reranked_candidate_ids: Sequence[str] = (),
    diagnostics_available: bool = False,
    top_k: int = 5,
) -> dict[str, Any]:
    relevant = set(relevant_ids)
    ids = list(returned_ids)
    first_rank = next(
        (rank for rank, document_id in enumerate(ids, start=1) if document_id in relevant),
        None,
    )
    result: dict[str, Any] = {
        "first_relevant_rank": first_rank,
        "mrr": 1 / first_rank if first_rank else 0.0,
        "candidate_recall": (
            len(set(candidate_ids) & relevant) / len(relevant)
            if diagnostics_available and relevant
            else None
        ),
        "nearest_candidate_recall": (
            len(set(nearest_candidate_ids) & relevant) / len(relevant)
            if diagnostics_available and relevant
            else None
        ),
        "rerank_lift": (
            _rerank_lift(candidate_ids, reranked_candidate_ids, relevant)
            if diagnostics_available
            else None
        ),
        "hard_negative_leakage_at_3": bool(set(ids[:3]) & set(hard_negative_ids)),
    }
    for k in (1, 3, 5):
        top = ids[:k]
        hits = len(set(top) & relevant)
        result[f"hit_at_{k}"] = bool(hits)
        if k in {3, 5}:
            result[f"recall_at_{k}"] = hits / len(relevant) if relevant else None
            result[f"precision_at_{k}"] = hits / k
            result[f"ndcg_at_{k}"] = ndcg_at_k(ids, graded_relevance, k)
    return result


def ndcg_at_k(returned_ids: Sequence[str], graded_relevance: dict[str, int], k: int) -> float:
    gains = [graded_relevance.get(document_id, 0) for document_id in returned_ids[:k]]
    dcg = sum((2**gain - 1) / math.log2(rank + 1) for rank, gain in enumerate(gains, 1))
    ideal = sorted(graded_relevance.values(), reverse=True)[:k]
    idcg = sum((2**gain - 1) / math.log2(rank + 1) for rank, gain in enumerate(ideal, 1))
    return dcg / idcg if idcg else 0.0


def _aggregate_report(
    rows: Sequence[dict[str, Any]],
    *,
    mode: Literal["retriever", "e2e"],
    top_k: int,
) -> dict[str, Any]:
    answered = [row for row in rows if row["answered_case"]]
    in_domain_no_answer = [row for row in rows if row["in_domain_no_answer"]]
    ood = [row for row in rows if row["out_of_domain"]]
    safety = [row for row in rows if row["expected_branch"] == "safety"]
    high_safety = [row for row in safety if row["risk_level"] == "high"]
    sensitive_safety = [row for row in safety if row["risk_level"] == "sensitive"]
    hard_negative_cases = [row for row in answered if row["hard_negative_ids"]]
    hard_confusion_cases = [
        row for row in answered if row["query_type"] == "hard_confusion"
    ]
    hard_cases = [row for row in answered if row["difficulty"] == "hard"]
    rag_route_cases = [row for row in rows if row["expected_branch"] == "rag"]
    abstention_population = [
        row for row in rows if row["answered_case"] or row["in_domain_no_answer"]
    ]
    predicted_abstentions = [row for row in abstention_population if row["abstained"]]
    true_abstentions = [row for row in predicted_abstentions if row["in_domain_no_answer"]]
    true_positive = len(true_abstentions)
    false_negative = sum(not row["abstained"] for row in in_domain_no_answer)
    false_positive = sum(row["abstained"] for row in answered)
    true_negative = sum(not row["abstained"] for row in answered)
    no_answer_recall = _optional_ratio(true_positive, true_positive + false_negative)
    no_answer_precision = _optional_ratio(true_positive, true_positive + false_positive)
    no_answer_f1 = _confusion_f1(true_positive, false_positive, false_negative)
    no_answer_confusion = {
        "true_positive": true_positive,
        "false_negative": false_negative,
        "false_positive": false_positive,
        "true_negative": true_negative,
        "actual_positive": len(in_domain_no_answer),
        "actual_negative": len(answered),
        "predicted_positive": len(predicted_abstentions),
        "predicted_negative": len(abstention_population) - len(predicted_abstentions),
        "denominator": len(abstention_population),
    }
    attribution_cutoff = min(max(top_k, 1), 3)
    failures = [
        {
            **row,
            "error_attribution": _error_attribution(row, cutoff=attribution_cutoff),
        }
        for row in rows
        if _is_failure(row, cutoff=attribution_cutoff)
    ]
    metrics: dict[str, Any] = {
        "hit_at_1": _optional_metric_mean(answered, "hit_at_1"),
        "hit_at_3": _optional_metric_mean(answered, "hit_at_3"),
        "hit_at_5": _optional_metric_mean(answered, "hit_at_5"),
        "recall_at_3": _optional_metric_mean(answered, "recall_at_3"),
        "recall_at_5": _optional_metric_mean(answered, "recall_at_5"),
        "precision_at_3": _optional_metric_mean(answered, "precision_at_3"),
        "precision_at_5": _optional_metric_mean(answered, "precision_at_5"),
        "mrr": _optional_metric_mean(answered, "mrr"),
        "ndcg_at_3": _optional_metric_mean(answered, "ndcg_at_3"),
        "ndcg_at_5": _optional_metric_mean(answered, "ndcg_at_5"),
        "candidate_recall": _optional_metric_mean(answered, "candidate_recall"),
        "rerank_lift": _optional_metric_mean(answered, "rerank_lift"),
        "hard_negative_leakage_at_3": _optional_metric_mean(
            hard_negative_cases,
            "hard_negative_leakage_at_3",
        ),
        "abstention_precision": no_answer_precision,
        "abstention_recall": no_answer_recall,
        "no_answer_f1": no_answer_f1,
        "false_retrieval_rate": _optional_ratio(
            sum(row["covered"] for row in in_domain_no_answer),
            len(in_domain_no_answer),
        ),
        "coverage": _optional_ratio(sum(row["covered"] for row in answered), len(answered)),
        "ood_route_accuracy": _boolean_accuracy(ood, "branch_correct") if mode == "e2e" else None,
        "branch_accuracy": _boolean_accuracy(rows, "branch_correct") if mode == "e2e" else None,
        "safety_rag_bypass_violation_rate": (
            _optional_metric_mean(safety, "ordinary_rag_bypass_violation")
            if mode == "e2e"
            else None
        ),
        "hard_confusion_hit_at_3": _optional_metric_mean(
            hard_confusion_cases, "hit_at_3"
        ),
        "hard_case_hit_at_3": _optional_metric_mean(hard_cases, "hit_at_3"),
        "router_primary_scenario_accuracy": (
            _scenario_accuracy(rows) if mode == "e2e" else None
        ),
        "goal_micro_f1": _goal_micro_f1(rows) if mode == "e2e" else None,
        "high_safety_recall": (
            _boolean_accuracy(high_safety, "branch_correct") if mode == "e2e" else None
        ),
        "sensitive_branch_accuracy": (
            _boolean_accuracy(sensitive_safety, "branch_correct")
            if mode == "e2e"
            else None
        ),
    }
    metric_denominators = {
        **{key: _metric_denominator(answered, key) for key in (
            "hit_at_1",
            "hit_at_3",
            "hit_at_5",
            "recall_at_3",
            "recall_at_5",
            "precision_at_3",
            "precision_at_5",
            "mrr",
            "ndcg_at_3",
            "ndcg_at_5",
            "candidate_recall",
            "rerank_lift",
        )},
        "hard_negative_leakage_at_3": len(hard_negative_cases),
        "abstention_precision": len(predicted_abstentions),
        "abstention_recall": len(in_domain_no_answer),
        "no_answer_f1": len(abstention_population),
        "false_retrieval_rate": len(in_domain_no_answer),
        "coverage": len(answered),
        "ood_route_accuracy": len(ood) if mode == "e2e" else 0,
        "branch_accuracy": len(rows) if mode == "e2e" else 0,
        "safety_rag_bypass_violation_rate": len(safety) if mode == "e2e" else 0,
        "hard_confusion_hit_at_3": len(hard_confusion_cases),
        "hard_case_hit_at_3": len(hard_cases),
        "router_primary_scenario_accuracy": len(rag_route_cases) if mode == "e2e" else 0,
        "goal_micro_f1": len(rag_route_cases) if mode == "e2e" else 0,
        "high_safety_recall": len(high_safety) if mode == "e2e" else 0,
        "sensitive_branch_accuracy": len(sensitive_safety) if mode == "e2e" else 0,
    }
    return {
        **metrics,
        "normal_answered_count": len(answered),
        "in_domain_no_answer_count": len(in_domain_no_answer),
        "out_of_domain_count": len(ood),
        "no_answer_confusion_matrix": no_answer_confusion,
        "branch_confusion_matrix": _branch_confusion_matrix(rows) if mode == "e2e" else {},
        "metric_denominators": metric_denominators,
        "error_attribution_cutoff": attribution_cutoff,
        "error_attribution_counts": dict(
            Counter(row["error_attribution"] for row in failures)
        ),
        "slices": {
            "scenario": _slice_metrics(answered, "scenario"),
            "query_type": _slice_metrics(answered, "query_type"),
            "difficulty": _slice_metrics(answered, "difficulty"),
            "stage": _slice_metrics(answered, "stage"),
            "goal": _goal_slices(answered),
        },
        "top_failures": sorted(
            failures,
            key=lambda row: (row["hit_at_3"], row["mrr"], row["id"]),
        )[:20],
    }


def evaluate_rag_targets(report: dict[str, Any]) -> dict[str, Any]:
    checks: dict[str, bool | None] = {}
    for metric, (operator, target) in _TARGETS.items():
        value = report.get(metric)
        if value is None:
            checks[metric] = None
        else:
            checks[metric] = value >= target if operator == ">=" else value <= target
    scenario_checks = {
        scenario: (
            values["hit_at_3"] >= 0.88 if values.get("hit_at_3") is not None else None
        )
        for scenario, values in report.get("slices", {}).get("scenario", {}).items()
    }
    if report.get("mode") == "e2e":
        high_safety_recall = report.get("high_safety_recall")
        checks["high_safety_recall"] = (
            high_safety_recall >= 0.98 if high_safety_recall is not None else None
        )
        bypass_rate = report.get("safety_rag_bypass_violation_rate")
        checks["safety_rag_bypass_violation_rate"] = (
            bypass_rate == 0 if bypass_rate is not None else None
        )
        oracle_gap = report.get("oracle_gap")
        degradation = (
            oracle_gap.get("routing_degradation_pp")
            if isinstance(oracle_gap, dict)
            else None
        )
        checks["routing_degradation_pp"] = (
            degradation <= 8 if degradation is not None else None
        )
    unavailable = sorted(metric for metric, passed in checks.items() if passed is None)
    return {
        "passed": bool(checks) and not unavailable and all(checks.values()) and all(
            passed is True for passed in scenario_checks.values()
        ),
        "checks": checks,
        "unavailable": unavailable,
        "scenario_hit_at_3_checks": scenario_checks,
    }


def compare_oracle_and_e2e(
    oracle_report: dict[str, Any],
    e2e_report: dict[str, Any],
) -> dict[str, float | bool | None]:
    oracle_value = oracle_report.get("hit_at_3")
    e2e_value = e2e_report.get("hit_at_3")
    oracle_hit = float(oracle_value) if oracle_value is not None else None
    e2e_hit = float(e2e_value) if e2e_value is not None else None
    degradation = (
        round((oracle_hit - e2e_hit) * 100, 2)
        if oracle_hit is not None and e2e_hit is not None
        else None
    )
    return {
        "oracle_hit_at_3": oracle_hit,
        "e2e_hit_at_3": e2e_hit,
        "routing_degradation_pp": degradation,
        "target_max_pp": 8.0,
        "target_passed": degradation <= 8 if degradation is not None else None,
    }


def write_rag_report(report: dict[str, Any], output_path: Path) -> tuple[Path, Path]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    json_path = (
        output_path
        if output_path.suffix.casefold() == ".json"
        else output_path.with_suffix(".json")
    )
    markdown_path = json_path.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_rag_report(report), encoding="utf-8")
    return json_path, markdown_path


def render_rag_report(report: dict[str, Any]) -> str:
    keys = [
        "hit_at_1",
        "hit_at_3",
        "hit_at_5",
        "recall_at_3",
        "recall_at_5",
        "precision_at_3",
        "precision_at_5",
        "mrr",
        "ndcg_at_3",
        "ndcg_at_5",
        "candidate_recall",
        "rerank_lift",
        "hard_negative_leakage_at_3",
        "abstention_precision",
        "abstention_recall",
        "no_answer_f1",
        "false_retrieval_rate",
        "coverage",
        "branch_accuracy",
        "ood_route_accuracy",
        "router_primary_scenario_accuracy",
        "goal_micro_f1",
        "high_safety_recall",
        "sensitive_branch_accuracy",
        "safety_rag_bypass_violation_rate",
    ]
    lines = [
        "# LoveApp RAG V2 Evaluation Report",
        "",
        f"- Mode: `{report.get('mode', '-')}`",
        f"- Cases: {report.get('case_count', 0)}",
        f"- Targets passed: {report.get('targets', {}).get('passed', False)}",
        "",
        "## Overall",
        "",
        "| Metric | Value | Denominator |",
        "|---|---:|---:|",
    ]
    denominators = report.get("metric_denominators", {})
    for key in keys:
        if key in report:
            lines.append(
                f"| `{key}` | {_format_metric(report[key])} | "
                f"{denominators.get(key, 'N/A')} |"
            )
    baseline_comparison = report.get("baseline_comparison")
    if isinstance(baseline_comparison, dict):
        lines.extend(
            [
                "",
                "## Baseline vs frozen",
                "",
                "| Metric | Baseline | Frozen |",
                "|---|---:|---:|",
            ]
        )
        for key, values in baseline_comparison.items():
            if not isinstance(values, dict):
                continue
            lines.append(
                f"| `{key}` | {_format_metric(values.get('baseline'))} | "
                f"{_format_metric(values.get('frozen'))} |"
            )
    run = report.get("run")
    if isinstance(run, dict):
        lines.extend(["", "## Run metadata", "", "| Field | Value |", "|---|---|"])
        for key, value in run.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            lines.append(f"| `{key}` | {value} |")
    lines.extend(["", "## Latency", ""])
    lines.append(f"- Cold start: {_format_duration(report.get('cold_start_ms'))}")
    for key, value in report.get("warm_latency_ms", {}).items():
        if key == "sample_count":
            lines.append(f"- Warm sample count: {value}")
        else:
            lines.append(f"- `{key}`: {_format_duration(value)}")
    for stage, values in report.get("stage_latency_ms", {}).items():
        lines.append(
            f"- `{stage}`: P50={_format_duration(values.get('p50'))}, "
            f"P95={_format_duration(values.get('p95'))}, n={values.get('sample_count', 0)}"
        )
    confusion = report.get("no_answer_confusion_matrix", {})
    lines.extend(
        [
            "",
            "## In-domain no-answer",
            "",
            "| TP | FP | FN | TN | N | Precision | Recall | F1 | False retrieval | Coverage |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            "| "
            f"{confusion.get('true_positive', 0)} | {confusion.get('false_positive', 0)} | "
            f"{confusion.get('false_negative', 0)} | {confusion.get('true_negative', 0)} | "
            f"{confusion.get('denominator', 0)} | "
            f"{_format_metric(report.get('abstention_precision'))} | "
            f"{_format_metric(report.get('abstention_recall'))} | "
            f"{_format_metric(report.get('no_answer_f1'))} | "
            f"{_format_metric(report.get('false_retrieval_rate'))} | "
            f"{_format_metric(report.get('coverage'))} |",
        ]
    )
    branch_confusion = report.get("branch_confusion_matrix", {})
    if branch_confusion:
        lines.extend(["", "## Branch confusion matrix", ""])
        lines.extend(["| Expected | Predicted | Cases |", "|---|---|---:|"])
        for expected, predictions in branch_confusion.items():
            for predicted, count in predictions.items():
                lines.append(f"| `{expected}` | `{predicted}` | {count} |")
    oracle_gap = report.get("oracle_gap")
    if oracle_gap:
        lines.extend(
            [
                "",
                "## Oracle vs E2E",
                "",
                f"- Oracle Hit@3: {_format_metric(oracle_gap['oracle_hit_at_3'])}",
                f"- E2E Hit@3: {_format_metric(oracle_gap['e2e_hit_at_3'])}",
                "- Routing degradation: "
                f"{_format_metric(oracle_gap['routing_degradation_pp'])} pp",
                f"- <= 8 pp target: {_format_metric(oracle_gap.get('target_passed'))}",
            ]
        )
    for slice_name, values in report.get("slices", {}).items():
        lines.extend(
            [
                "",
                f"## Slice: {slice_name}",
                "",
                "| Value | Cases | Hit@3 | Hit@5 | MRR | nDCG@5 |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for value, metrics in values.items():
            lines.append(
                f"| `{value}` | {metrics['case_count']} | "
                f"{_format_metric(metrics['hit_at_3'])} | "
                f"{_format_metric(metrics['hit_at_5'])} | "
                f"{_format_metric(metrics['mrr'])} | "
                f"{_format_metric(metrics['ndcg_at_5'])} |"
            )
    lines.extend(["", "## Error attribution", ""])
    for name, count in sorted(report.get("error_attribution_counts", {}).items()):
        lines.append(f"- `{name}`: {count}")
    lines.extend(["", "## Top failures", ""])
    for row in report.get("top_failures", []):
        relevant = json.dumps(
            row.get("relevant_ids", []),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        returned = json.dumps(row["returned"], ensure_ascii=False, separators=(",", ":"))
        nearest = json.dumps(
            row.get("nearest_candidates", [])[:5],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        lines.append(
            f"- `{row['id']}` attribution={row['error_attribution']} "
            f"expected_branch={row['expected_branch']} relevant={relevant} "
            f"returned={returned} nearest={nearest} Hit@3={row['hit_at_3']}"
        )
    lines.extend(["", "## Uncovered nearest-candidate review", ""])
    for row in report.get("cases", []):
        if row.get("in_domain_no_answer"):
            nearest = json.dumps(
                row.get("nearest_candidates", [])[:5],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            lines.append(f"- `{row['id']}`: {nearest}")
    if report.get("protocol_notes"):
        lines.extend(["", "## Protocol notes", ""])
        for note in report["protocol_notes"]:
            lines.append(f"- {note}")
    if report.get("conclusion"):
        lines.extend(["", "## Conclusion", "", str(report["conclusion"])])
    return "\n".join(lines) + "\n"


def _slice_metrics(rows: Sequence[dict[str, Any]], field: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[field])].append(row)
    return {key: _brief_metrics(values) for key, values in sorted(grouped.items())}


def _goal_slices(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for goal in row["goals"]:
            grouped[goal].append(row)
    return {key: _brief_metrics(values) for key, values in sorted(grouped.items())}


def _brief_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "case_count": len(rows),
        "hit_at_3": _metric_mean(rows, "hit_at_3"),
        "hit_at_5": _metric_mean(rows, "hit_at_5"),
        "mrr": _metric_mean(rows, "mrr"),
        "ndcg_at_5": _metric_mean(rows, "ndcg_at_5"),
    }


def _is_failure(row: dict[str, Any], *, cutoff: int) -> bool:
    if row["answered_case"]:
        return not _has_relevant_at_cutoff(row, cutoff=cutoff)
    if row["in_domain_no_answer"]:
        return not row["abstained"]
    return row["branch_correct"] is False or row["ordinary_rag_bypass_violation"]


def _error_attribution(row: dict[str, Any], *, cutoff: int) -> str:
    if row["branch_correct"] is False or row["ordinary_rag_bypass_violation"]:
        return "router_error"
    if not row["answered_case"]:
        return "gold_or_dataset_issue"
    expected_goals = set(row["goals"])
    predicted_goals = set(row["predicted_goals"])
    if row["evaluation_mode"] == "e2e" and (
        row["predicted_scenario"] != row["scenario"]
        or (expected_goals and not expected_goals & predicted_goals)
    ):
        return "router_error"
    if not row["diagnostics_available"]:
        return "gold_or_dataset_issue"
    relevant = set(row["relevant_ids"])
    if not relevant:
        return "gold_or_dataset_issue"
    if not (set(row["candidate_ids"]) & relevant) and (
        set(row["nearest_candidate_ids"]) & relevant
    ):
        return "threshold_rejection"
    if not (set(row["candidate_ids"]) & relevant):
        return "candidate_miss"
    if not _has_relevant_at_cutoff(row, cutoff=cutoff):
        return "rerank_error"
    return "gold_or_dataset_issue"


def _has_relevant_at_cutoff(row: dict[str, Any], *, cutoff: int) -> bool:
    return bool(set(row["returned_ids"][:cutoff]) & set(row["relevant_ids"]))


def _rerank_lift(
    candidate_ids: Sequence[str],
    reranked_candidate_ids: Sequence[str],
    relevant_ids: set[str],
) -> float | None:
    before = {document_id: rank for rank, document_id in enumerate(candidate_ids, 1)}
    after = {document_id: rank for rank, document_id in enumerate(reranked_candidate_ids, 1)}
    comparable = relevant_ids & before.keys() & after.keys()
    if not comparable:
        return None
    return mean(before[document_id] - after[document_id] for document_id in comparable)


def _scenario_accuracy(rows: Sequence[dict[str, Any]]) -> float | None:
    eligible = [row for row in rows if row["expected_branch"] == "rag"]
    return _optional_ratio(
        sum(row["predicted_scenario"] == row["scenario"] for row in eligible),
        len(eligible),
    )


def _goal_micro_f1(rows: Sequence[dict[str, Any]]) -> float | None:
    true_positive = false_positive = false_negative = 0
    for row in rows:
        if row["expected_branch"] != "rag":
            continue
        expected = set(row["goals"])
        predicted = set(row["predicted_goals"])
        true_positive += len(expected & predicted)
        false_positive += len(predicted - expected)
        false_negative += len(expected - predicted)
    if true_positive + false_positive + false_negative == 0:
        return None
    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    return _f1(precision, recall)


def _boolean_accuracy(rows: Sequence[dict[str, Any]], key: str) -> float | None:
    return _optional_ratio(sum(row.get(key) is True for row in rows), len(rows))


def _branch_confusion_matrix(rows: Sequence[dict[str, Any]]) -> dict[str, dict[str, int]]:
    matrix: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        predicted = row["predicted_branch"] or "unavailable"
        matrix[row["expected_branch"]][predicted] += 1
    return {
        expected: dict(sorted(predictions.items()))
        for expected, predictions in sorted(matrix.items())
    }


def _metric_denominator(rows: Sequence[dict[str, Any]], key: str) -> int:
    return sum(row.get(key) is not None for row in rows)


def _metric_mean(rows: Sequence[dict[str, Any]], key: str) -> float:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return round(mean(values), 4) if values else 0.0


def _optional_metric_mean(
    rows: Sequence[dict[str, Any]],
    key: str,
) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return round(mean(values), 4) if values else None


def _latency(values: Sequence[float]) -> dict[str, float | int | None]:
    return {
        "sample_count": len(values),
        "mean": round(mean(values), 3) if values else None,
        "p50": round(_percentile(values, 0.50), 3) if values else None,
        "p90": round(_percentile(values, 0.90), 3) if values else None,
        "p95": round(_percentile(values, 0.95), 3) if values else None,
    }


def _stage_latencies(
    rows: Sequence[dict[str, Any]],
) -> dict[str, dict[str, float | int]]:
    stages = {
        "rag_query_embedding",
        "rag_vector_search",
        "rag_soft_rerank",
    }
    values: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        for record in row["trace"]:
            if record.get("name") in stages:
                values[record["name"]].append(float(record.get("duration_ms", 0)))
    return {
        stage: {
            "sample_count": len(durations),
            "p50": round(_percentile(durations, 0.50), 3),
            "p95": round(_percentile(durations, 0.95), 3),
        }
        for stage, durations in sorted(values.items())
    }


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1))
    return ordered[index]


def _distribution(values: Sequence[int]) -> dict[str, float | int]:
    return {
        "min": min(values, default=0),
        "median": _percentile(values, 0.50),
        "p90": _percentile(values, 0.90),
        "max": max(values, default=0),
    }


def _duplicates(values) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def _near_duplicate_pairs(
    items: Sequence[tuple[str, str]],
    *,
    category: str,
) -> list[dict[str, Any]]:
    grams = [_character_bigrams(value) for _, value in items]
    pairs: list[dict[str, Any]] = []
    for left_index, left in enumerate(grams):
        for right_index in range(left_index + 1, len(grams)):
            right = grams[right_index]
            union = left | right
            similarity = len(left & right) / len(union) if union else 0.0
            if similarity >= 0.82:
                pairs.append(
                    {
                        "category": category,
                        "left_id": items[left_index][0],
                        "right_id": items[right_index][0],
                        "similarity": round(similarity, 4),
                    }
                )
    return sorted(pairs, key=lambda pair: pair["similarity"], reverse=True)


def _character_bigrams(value: str) -> set[str]:
    normalized = "".join(character.casefold() for character in value if character.isalnum())
    return {normalized[index : index + 2] for index in range(len(normalized) - 1)}


def _canonical(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", normalized)


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _optional_ratio(
    numerator: int | float,
    denominator: int | float,
) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _confusion_f1(
    true_positive: int,
    false_positive: int,
    false_negative: int,
) -> float | None:
    denominator = 2 * true_positive + false_positive + false_negative
    return round(2 * true_positive / denominator, 4) if denominator else None


def _f1(precision: float, recall: float) -> float:
    return round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0


def _format_metric(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"{value:.4f}" if isinstance(value, float) else str(value)


def _format_duration(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):.3f} ms"

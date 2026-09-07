"""Conditional contextual rewriting and bounded multi-query planning.

The planner is deliberately independent from an LLM and from a particular
vector store.  A deterministic, conservative policy is used by default; an
optional callable can be supplied when a deployment wants a structured model
correction.  The important contract is that rewriting and decomposition are
explicitly feature-gated and never silently change the raw user query.
"""

from __future__ import annotations

import inspect
import json
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from loveapp.adapters.knowledge.scoring import RerankConfig, soft_rerank
from loveapp.domain.knowledge import (
    KnowledgeFilters,
    KnowledgeSearchResult,
    RetrievedDocument,
)
from loveapp.ports.knowledge import KnowledgeRetriever
from loveapp.ports.observability import TraceRecorder

HistoryValue = Any
RewriteCallable = Callable[[str, list[str]], str | Awaitable[str]]
DecomposeCallable = Callable[[str], Sequence[str] | Awaitable[Sequence[str]]]


class ContextualQueryResult(BaseModel):
    """Result of the conditional contextual-query stage."""

    model_config = ConfigDict(extra="forbid")

    raw_query: str = Field(min_length=1)
    retrieval_query: str = Field(min_length=1)
    rewritten: bool = False
    trigger_reason: str | None = None
    history_window: list[str] = Field(default_factory=list)

    @property
    def rewrite_trigger(self) -> str | None:
        """Compatibility alias used by trace/evaluation callers."""

        return self.trigger_reason


class QueryDecompositionResult(BaseModel):
    """Bounded, non-recursive decomposition output."""

    model_config = ConfigDict(extra="forbid")

    raw_query: str = Field(min_length=1)
    subqueries: list[str] = Field(min_length=1, max_length=3)
    decomposed: bool = False
    trigger_reason: str | None = None

    @property
    def decomposition_trigger(self) -> str | None:
        return self.trigger_reason

    @property
    def subquery_count(self) -> int:
        return len(self.subqueries)


class RetrievalQueryPlan(BaseModel):
    """Combined Phase 4/5 plan and its observable decisions."""

    model_config = ConfigDict(extra="forbid")

    raw_query: str = Field(min_length=1)
    retrieval_query: str = Field(min_length=1)
    rewritten: bool = False
    rewrite_trigger: str | None = None
    history_window: list[str] = Field(default_factory=list)
    subqueries: list[str] = Field(min_length=1, max_length=3)
    decomposed: bool = False
    decomposition_trigger: str | None = None

    @model_validator(mode="after")
    def validate_subqueries(self) -> RetrievalQueryPlan:
        if not self.subqueries:
            raise ValueError("at least one retrieval query is required")
        if any(not value.strip() for value in self.subqueries):
            raise ValueError("subqueries cannot be blank")
        return self

    @property
    def rewrite_required(self) -> bool:
        return self.rewritten

    @property
    def decomposition_required(self) -> bool:
        return self.decomposed

    @property
    def subquery_count(self) -> int:
        return len(self.subqueries)


# A shorter name is useful to callers that do not need to distinguish the
# planner from the resulting object.
QueryPlan = RetrievalQueryPlan


class MultiQueryRetrievalResult(BaseModel):
    """Merged retrieval output plus per-subquery evidence."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    plan: RetrievalQueryPlan
    returned: list[RetrievedDocument] = Field(default_factory=list)
    per_subquery_candidate_ids: list[list[str]] = Field(default_factory=list)
    per_subquery_scores: list[dict[str, float]] = Field(default_factory=list)
    merged_candidate_ids: list[str] = Field(default_factory=list)
    final_top_k: list[str] = Field(default_factory=list)
    duplicate_candidate_ratio: float = Field(default=0, ge=0, le=1)
    duration_ms: float = Field(default=0, ge=0)
    trace: list[dict[str, Any]] = Field(default_factory=list)


_CONTEXT_CUE_RE = re.compile(
    r"(?:^|[，。！？\s])(?:那(?:我|现在|接下来)?|所以|这样|这种(?:情况|时候)?|这个(?:情况|变化|问题|事情)?|"
    r"这件事|接下来|下一步|然后呢|还要继续|还能继续|怎么办|怎么做|怎么处理|说明什么|合适吗|该不该|"
    r"是否要继续|要不要继续)",
)
_CONTEXT_ONLY_RE = re.compile(
    r"^(?:那(?:我|现在|接下来)?|所以|这样|这种(?:情况|时候)?|这个(?:情况|变化|问题|事情)?|"
    r"这件事|接下来|下一步|然后呢|怎么办|怎么做|怎么处理|说明什么|合适吗|该不该|"
    r"还要继续|还能继续|是否要继续|要不要继续)[？?！!。；;，,\s]*$"
)

_SPLIT_MARKER_RE = re.compile(
    r"(?:第一|第二|第三|第[一二三1-3]个?|一是|二是|三是|一边|另一边|"
    r"另外|此外|还有(?:一件事|一个问题)|另一个(?:问题|事情)?|同时|再者)\s*[,，:：]?"
)
_INLINE_SPLIT_RE = re.compile(
    r"\s*(?:；|;|\n+|(?:。|！|！|？|\?|!)+\s*(?=(?:另外|此外|还有|同时|再者|第二|第三|另一个)))\s*"
)
_TRAILING_META_RE = re.compile(
    r"(?:[。！？!?；;]+\s*)?(?:这(?:两|三|几)(?:件事|个问题)(?:分别)?[^。！？!?]*|"
    r"我不想把(?:两|三)件事混在一起[^。！？!?]*|(?:我感觉全搅在一起了，)?想(?:分别|分开)知道[^。！？!?]*|"
    r"我?想把这(?:两|三|几)个问题拆开处理[^。！？!?]*|请分别[^。！？!?]*)[。！？!?]*$"
)


def _history_content(value: HistoryValue) -> tuple[str, str | None]:
    """Return ``(content, role)`` from strings, messages, or mappings."""

    if isinstance(value, str):
        return value.strip(), None
    if isinstance(value, Mapping):
        content = value.get("content", value.get("text", value.get("message", "")))
        role = value.get("role")
    else:
        content = getattr(value, "content", getattr(value, "text", ""))
        role = getattr(value, "role", None)
    role_value = getattr(role, "value", role)
    return str(content or "").strip(), str(role_value) if role_value is not None else None


def normalize_history(history: Sequence[HistoryValue] | None, *, window: int = 4) -> list[str]:
    """Extract a bounded history window without inventing content.

    User turns are preferred when roles are available.  If callers provide
    plain strings, every non-empty entry is retained.  Assistant turns are
    retained only when no user turn is available, preventing generic assistant
    filler from dominating a rewritten query.
    """

    if not history or window <= 0:
        return []
    values: list[tuple[str, str | None]] = []
    for item in history:
        content, role = _history_content(item)
        if content:
            values.append((content, role))
    values = values[-window:]
    user_values = [content for content, role in values if role in {"user", "human"}]
    if user_values:
        return user_values
    return [content for content, _ in values]


def contextual_trigger_reason(raw_query: str, history: Sequence[str] | None) -> str | None:
    """Conservative trigger detector for contextual rewriting."""

    if not history:
        return None
    query = " ".join(raw_query.split())
    if not query:
        return None
    if _CONTEXT_ONLY_RE.match(query):
        return "contextual_ellipsis"
    if _CONTEXT_CUE_RE.search(query):
        # A very long, fully specified query can contain words such as "所以"
        # as ordinary discourse.  Require a short follow-up or an explicit
        # deictic opening in that case.
        compact_length = len(re.sub(r"\s+", "", query))
        if compact_length <= 42 or re.match(r"^(?:那|所以|这样|这种|这个|接下来|下一步)", query):
            return "deictic_reference"
    compact_length = len(re.sub(r"\s+", "", query))
    if compact_length <= 14:
        return "short_follow_up"
    return None


def _safe_query(value: str) -> str:
    return " ".join(str(value).split()).strip()


def _normalize_subqueries(values: Iterable[str], *, max_subqueries: int) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _safe_query(value)
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(normalized)
        if len(output) >= max_subqueries:
            break
    return output


def _strip_segment_prefix(segment: str) -> str:
    segment = re.sub(
        r"^(?:第[一二三1-3]个?|第一|第二|第三|一是|二是|三是|一边|另一边|另外|此外|还有(?:一件事|一个问题)|另一个(?:问题|事情)?|同时|再者)\s*[,，:：]?\s*",
        "",
        segment,
    )
    segment = re.sub(r"^(?:又?是)\s*", "", segment)
    return _safe_query(_TRAILING_META_RE.sub("", segment))


def _strip_leading_frame(segment: str) -> str:
    segment = re.sub(
        r"^(?:事情有点多|我现在(?:其实)?(?:卡在|有)(?:两|三|几个)(?:件事|问题)(?:上)?|"
        r"我现在有(?:两|三|几个)个问题想一起理清)\s*[:：]?\s*",
        "",
        segment,
    )
    return _strip_segment_prefix(segment)


def split_information_needs(query: str, *, max_subqueries: int = 3) -> list[str]:
    """Split only explicit, semantically marked independent needs.

    The detector intentionally does not use question-mark count, sentence
    count, or query length alone.  Explicit enumerators and contrastive
    discourse markers are the deterministic evidence that the user asked for
    separate needs.
    """

    query = _safe_query(query)
    if not query:
        return []
    # Enumerated Chinese/Arabic needs: keep the text after each marker.
    marker_matches = list(_SPLIT_MARKER_RE.finditer(query))
    segments: list[str] = []
    first_marker = marker_matches[0].group().strip(" ，,:：") if marker_matches else ""
    continuation_first = first_marker.startswith(
        ("另一边", "另外", "此外", "同时", "再者", "还有", "另一个")
    )
    if len(marker_matches) >= 2 or (len(marker_matches) == 1 and continuation_first):
        if continuation_first:
            prefix = _strip_leading_frame(query[: marker_matches[0].start()])
            if len(re.sub(r"\W", "", prefix)) >= 6:
                segments.append(prefix)
        for index, match in enumerate(marker_matches):
            start = match.start()
            end = (
                marker_matches[index + 1].start() if index + 1 < len(marker_matches) else len(query)
            )
            segment = _strip_segment_prefix(query[start:end])
            if segment:
                segments.append(segment)
    if len(segments) < 2:
        # Strong inline markers ("另外…", "同时…") are accepted only when
        # each side contains a substantive clause.
        pieces = _INLINE_SPLIT_RE.split(query)
        pieces = [_strip_segment_prefix(piece) for piece in pieces]
        if len(pieces) >= 2 and all(len(re.sub(r"\W", "", piece)) >= 6 for piece in pieces):
            segments = pieces
    if len(segments) < 2:
        return [query]
    # Drop framing/trailing requests and preserve the user's substantive text.
    cleaned = _normalize_subqueries(segments, max_subqueries=max_subqueries)
    return cleaned if len(cleaned) >= 2 else [query]


class RetrievalQueryPlanner:
    """Shared Phase 4/5 planner.

    Flags default to ``False`` so existing production behavior is unchanged.
    ``plan`` is synchronous for deterministic rules; ``aplan`` additionally
    supports asynchronous structured rewrite/decomposition callbacks.
    """

    def __init__(
        self,
        *,
        contextual_query_rewrite_enabled: bool = False,
        query_decomposition_enabled: bool = False,
        max_subqueries: int = 3,
        history_window: int = 4,
        rewrite_fn: RewriteCallable | None = None,
        decompose_fn: DecomposeCallable | None = None,
        rerank_config: RerankConfig | None = None,
        coverage_bonus: float = 0.0,
    ) -> None:
        if not 1 <= int(max_subqueries) <= 3:
            raise ValueError("max_subqueries must be between 1 and 3")
        if history_window < 0:
            raise ValueError("history_window cannot be negative")
        if coverage_bonus < 0:
            raise ValueError("coverage_bonus cannot be negative")
        self.contextual_query_rewrite_enabled = bool(contextual_query_rewrite_enabled)
        self.query_decomposition_enabled = bool(query_decomposition_enabled)
        self.max_subqueries = int(max_subqueries)
        self.history_window = int(history_window)
        self.rewrite_fn = rewrite_fn
        self.decompose_fn = decompose_fn
        self.rerank_config = rerank_config or RerankConfig()
        self.coverage_bonus = float(coverage_bonus)

    def rewrite(
        self,
        raw_query: str,
        history: Sequence[HistoryValue] | None = None,
    ) -> ContextualQueryResult:
        raw = _safe_query(raw_query)
        history_window = normalize_history(history, window=self.history_window)
        reason = (
            contextual_trigger_reason(raw, history_window)
            if self.contextual_query_rewrite_enabled
            else None
        )
        if reason is None or not history_window:
            return ContextualQueryResult(
                raw_query=raw,
                retrieval_query=raw,
                rewritten=False,
                trigger_reason=None,
                history_window=history_window,
            )
        # A synchronous custom callback is supported by ``plan``.  Async
        # callbacks are intentionally ignored here and handled by ``aplan``.
        if self.rewrite_fn is not None and not inspect.iscoroutinefunction(self.rewrite_fn):
            try:
                candidate = self.rewrite_fn(raw, history_window)
            except Exception:
                # A model-backed callback is an optimisation.  Keep the
                # deterministic history+query fallback available when it is
                # unavailable, malformed, or raises during a request.
                candidate = None
            if isinstance(candidate, str) and _safe_query(candidate):
                rewritten = _safe_query(candidate)
            else:
                rewritten = " ".join([*history_window, raw])
        else:
            rewritten = " ".join([*history_window, raw])
        return ContextualQueryResult(
            raw_query=raw,
            retrieval_query=rewritten,
            rewritten=rewritten != raw,
            trigger_reason=reason,
            history_window=history_window,
        )

    def decompose(self, query: str) -> QueryDecompositionResult:
        raw = _safe_query(query)
        if not self.query_decomposition_enabled:
            return QueryDecompositionResult(raw_query=raw, subqueries=[raw])
        values: Sequence[str]
        if self.decompose_fn is not None and not inspect.iscoroutinefunction(self.decompose_fn):
            try:
                candidate = self.decompose_fn(raw)
            except Exception:
                # Preserve the bounded deterministic detector if an optional
                # structured decomposer fails.  It never recurses into itself.
                candidate = split_information_needs(raw, max_subqueries=self.max_subqueries)
            values = (
                candidate
                if isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes))
                else [raw]
            )
            trigger = "custom_decomposer" if len(values) > 1 else None
        else:
            values = split_information_needs(raw, max_subqueries=self.max_subqueries)
            trigger = "explicit_multi_intent" if len(values) > 1 else None
        normalized = _normalize_subqueries(values, max_subqueries=self.max_subqueries)
        if len(normalized) < 2:
            return QueryDecompositionResult(raw_query=raw, subqueries=[raw])
        return QueryDecompositionResult(
            raw_query=raw,
            subqueries=normalized,
            decomposed=True,
            trigger_reason=trigger,
        )

    def plan(
        self,
        raw_query: str,
        history: Sequence[HistoryValue] | None = None,
        *,
        trace: TraceRecorder | None = None,
    ) -> RetrievalQueryPlan:
        raw = _safe_query(raw_query)
        context = self.rewrite(raw, history)
        decomposition = self.decompose(context.retrieval_query)
        plan = RetrievalQueryPlan(
            raw_query=raw,
            retrieval_query=context.retrieval_query,
            rewritten=context.rewritten,
            rewrite_trigger=context.trigger_reason,
            history_window=context.history_window,
            subqueries=decomposition.subqueries,
            decomposed=decomposition.decomposed,
            decomposition_trigger=decomposition.trigger_reason,
        )
        _record_plan_trace(trace, plan)
        return plan

    async def arewrite(
        self,
        raw_query: str,
        history: Sequence[HistoryValue] | None = None,
    ) -> ContextualQueryResult:
        result = self.rewrite(raw_query, history)
        if (
            not result.rewritten
            or self.rewrite_fn is None
            or not inspect.iscoroutinefunction(self.rewrite_fn)
        ):
            return result
        try:
            candidate = await self.rewrite_fn(result.raw_query, result.history_window)
        except Exception:
            return result
        if not isinstance(candidate, str) or not _safe_query(candidate):
            return result
        normalized = _safe_query(candidate)
        # Treat a model returning the untouched raw query as a no-op and keep
        # the conservative deterministic rewrite.  This avoids reporting
        # ``rewritten=True`` for an unchanged query and preserves the fail-safe
        # behavior when the optional callback produces an unusable answer.
        if normalized == result.raw_query:
            return result
        return result.model_copy(update={"retrieval_query": normalized, "rewritten": True})

    async def adecompose(self, query: str) -> QueryDecompositionResult:
        result = self.decompose(query)
        if (
            not self.query_decomposition_enabled
            or self.decompose_fn is None
            or not inspect.iscoroutinefunction(self.decompose_fn)
        ):
            return result
        try:
            candidate = await self.decompose_fn(result.raw_query)
            if not isinstance(candidate, Sequence) or isinstance(candidate, (str, bytes)):
                raise TypeError("decompose callback must return a sequence of queries")
            normalized = _normalize_subqueries(candidate, max_subqueries=self.max_subqueries)
        except Exception:
            normalized = _normalize_subqueries(
                split_information_needs(
                    result.raw_query,
                    max_subqueries=self.max_subqueries,
                ),
                max_subqueries=self.max_subqueries,
            )
        if len(normalized) < 2:
            return QueryDecompositionResult(
                raw_query=result.raw_query,
                subqueries=[result.raw_query],
            )
        return QueryDecompositionResult(
            raw_query=result.raw_query,
            subqueries=normalized,
            decomposed=True,
            trigger_reason="custom_decomposer",
        )

    async def aplan(
        self,
        raw_query: str,
        history: Sequence[HistoryValue] | None = None,
        *,
        trace: TraceRecorder | None = None,
    ) -> RetrievalQueryPlan:
        raw = _safe_query(raw_query)
        context = await self.arewrite(raw, history)
        decomposition = await self.adecompose(context.retrieval_query)
        plan = RetrievalQueryPlan(
            raw_query=raw,
            retrieval_query=context.retrieval_query,
            rewritten=context.rewritten,
            rewrite_trigger=context.trigger_reason,
            history_window=context.history_window,
            subqueries=decomposition.subqueries,
            decomposed=decomposition.decomposed,
            decomposition_trigger=decomposition.trigger_reason,
        )
        _record_plan_trace(trace, plan)
        return plan

    async def retrieve(
        self,
        retriever: KnowledgeRetriever,
        raw_query: str,
        *,
        history: Sequence[HistoryValue] | None = None,
        filters: KnowledgeFilters | None = None,
        limit: int = 5,
        candidate_limit: int | None = None,
        trace: TraceRecorder | None = None,
    ) -> MultiQueryRetrievalResult:
        """Plan, retrieve each subquery, merge by document ID, and rerank."""

        started = perf_counter()
        plan = await self.aplan(raw_query, history, trace=trace)
        candidate_limit = max(int(candidate_limit or limit), int(limit), 1)
        per_ids: list[list[str]] = []
        per_scores: list[dict[str, float]] = []
        merged: dict[str, RetrievedDocument] = {}
        seen_total = 0
        search_detailed = getattr(retriever, "search_detailed", None)
        for subquery in plan.subqueries:
            if callable(search_detailed):
                detailed: KnowledgeSearchResult = await search_detailed(
                    subquery,
                    filters=filters,
                    limit=candidate_limit,
                    trace=trace,
                )
                pool = detailed.candidates or detailed.nearest_candidates or detailed.returned
            else:
                pool = await retriever.search(
                    subquery,
                    filters=filters,
                    limit=candidate_limit,
                    trace=trace,
                )
            ids: list[str] = []
            scores: dict[str, float] = {}
            seen_total += len(pool)
            for item in pool:
                document_id = item.document.id
                ids.append(document_id)
                base = float(item.base_score if item.base_score is not None else item.score)
                scores[document_id] = round(base, 6)
                current = merged.get(document_id)
                if current is None or base > float(current.base_score or current.score):
                    merged[document_id] = item.model_copy(
                        update={"score": base, "base_score": base}
                    )
            per_ids.append(list(dict.fromkeys(ids)))
            per_scores.append(scores)

        # Coverage bonus is intentionally tiny and optional.  Max score is
        # the frozen, easy-to-explain default for the first implementation.
        if self.coverage_bonus and len(plan.subqueries) > 1:
            coverage: defaultdict[str, int] = defaultdict(int)
            for ids in per_ids:
                for document_id in set(ids):
                    coverage[document_id] += 1
            for document_id, count in coverage.items():
                if document_id in merged and count > 1:
                    item = merged[document_id]
                    score = float(item.score) + self.coverage_bonus * (count - 1)
                    merged[document_id] = item.model_copy(update={"score": score})

        merged_values = list(merged.values())
        rerank_query = plan.retrieval_query
        # ``soft_rerank`` applies the existing lexical and soft metadata terms
        # after cross-subquery deduplication; no new embedding/reranker is
        # introduced here.
        reranked = soft_rerank(rerank_query, merged_values, filters, self.rerank_config)
        returned = reranked[:limit]
        merged_ids = [item.document.id for item in reranked]
        duplicate_ratio = (
            round(1 - len(set(item for ids in per_ids for item in ids)) / seen_total, 4)
            if seen_total
            else 0.0
        )
        duration_ms = (perf_counter() - started) * 1000
        result = MultiQueryRetrievalResult(
            plan=plan,
            returned=returned,
            per_subquery_candidate_ids=per_ids,
            per_subquery_scores=per_scores,
            merged_candidate_ids=merged_ids,
            final_top_k=[item.document.id for item in returned],
            duplicate_candidate_ratio=max(0.0, min(1.0, duplicate_ratio)),
            duration_ms=duration_ms,
            trace=_trace_snapshot(trace),
        )
        return result


def _record_plan_trace(trace: TraceRecorder | None, plan: RetrievalQueryPlan) -> None:
    if trace is None:
        return
    # ExecutionTrace details are JSON-compatible at runtime even though its
    # protocol uses a deliberately narrow scalar type for legacy records.
    measure = trace.measure("retrieval_query_planning")
    with measure as details:
        details["raw_query"] = plan.raw_query
        details["history_window"] = json.dumps(plan.history_window, ensure_ascii=False)
        details["rewrite_trigger"] = plan.rewrite_trigger
        details["rewritten_query"] = plan.retrieval_query if plan.rewritten else None
        details["decomposition_trigger"] = plan.decomposition_trigger
        details["subqueries"] = json.dumps(plan.subqueries, ensure_ascii=False)


def _trace_snapshot(trace: TraceRecorder | None) -> list[dict[str, Any]]:
    snapshot = getattr(trace, "snapshot", None)
    if not callable(snapshot):
        return []
    return [record.model_dump(mode="json") for record in snapshot()]


__all__ = [
    "ContextualQueryResult",
    "MultiQueryRetrievalResult",
    "QueryDecompositionResult",
    "QueryPlan",
    "RetrievalQueryPlan",
    "RetrievalQueryPlanner",
    "contextual_trigger_reason",
    "normalize_history",
    "split_information_needs",
]

"""Read-path retrieval for governed memories.

The write path remains the authority for admission and lifecycle state.  This
module only ranks already stored memories and deliberately treats inactive
rows as ineligible candidates.
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from math import sqrt

from loveapp.domain.advice import RelationshipContext
from loveapp.domain.memory import MemoryItem, MemoryKind, MemoryStatus, utc_now
from loveapp.domain.memory_context import attach_memories, memory_attention_reason
from loveapp.domain.memory_lifecycle import MemoryRole, memory_role, semantic_context_key
from loveapp.domain.relationship_evidence import RelationshipEvidenceProfile
from loveapp.domain.relationship_plan import RelationshipPlan
from loveapp.ports.embeddings import EmbeddingProvider


@dataclass(frozen=True)
class MemoryRetrievalScore:
    """Explainable score components for one retrieval candidate."""

    semantic_similarity: float
    predicate_match: float
    recency: float
    importance: float
    confidence: float
    lifecycle_priority: float
    total: float

    def as_dict(self) -> dict[str, float]:
        return {
            "semantic_similarity": round(self.semantic_similarity, 4),
            "predicate_match": round(self.predicate_match, 4),
            "recency": round(self.recency, 4),
            "importance": round(self.importance, 4),
            "confidence": round(self.confidence, 4),
            "lifecycle_priority": round(self.lifecycle_priority, 4),
            "total": round(self.total, 4),
        }


@dataclass(frozen=True)
class RetrievedMemory:
    item: MemoryItem
    score: MemoryRetrievalScore
    retrieval_text: str


class HybridMemoryRetriever:
    """Rank active memories with structured and semantic signals.

    Embeddings are optional.  Deployments can inject the existing embedding
    provider; tests and offline operation use the deterministic lexical score.
    No vectors are persisted, so the Store contract and memory schema remain
    unchanged.
    """

    def __init__(
        self,
        embedding_provider: EmbeddingProvider | None = None,
        *,
        token_budget: int = 4096,
    ) -> None:
        self._embedding_provider = embedding_provider
        self._token_budget = max(token_budget, 1)

    async def retrieve(
        self,
        memories: Iterable[MemoryItem],
        *,
        query: str | None = None,
        limit: int = 20,
        reference_time: datetime | None = None,
        token_budget: int | None = None,
    ) -> list[RetrievedMemory]:
        now = reference_time or utc_now()
        candidates = [
            item
            for item in memories
            if _is_retrievable(item, now)
        ]
        if not candidates or limit <= 0:
            return []

        query_text = (query or "").strip()
        query_vector: list[float] | None = None
        document_vectors: list[list[float]] | None = None
        if query_text and self._embedding_provider is not None:
            try:
                query_vector = await self._embedding_provider.embed_query(query_text)
                document_vectors = await self._embedding_provider.embed_documents(
                    [_retrieval_text(item) for item in candidates]
                )
            except Exception:
                # A retrieval provider is an optimization.  Structured and
                # lexical ranking must remain available when it is offline.
                query_vector = None
                document_vectors = None

        scored: list[RetrievedMemory] = []
        for index, item in enumerate(candidates):
            retrieval_text = _retrieval_text(item)
            lexical_similarity = _lexical_similarity(query_text, retrieval_text)
            semantic_similarity = lexical_similarity
            if query_vector is not None and document_vectors is not None and index < len(
                document_vectors
            ):
                semantic_similarity = _cosine(query_vector, document_vectors[index])
            predicate_match = _predicate_match(item, query_text)
            if query_text and not _candidate_is_relevant(
                item,
                query_text,
                lexical_similarity=lexical_similarity,
                semantic_similarity=semantic_similarity,
                predicate_match=predicate_match,
            ):
                continue
            score = _score(
                item,
                semantic_similarity=semantic_similarity,
                predicate_match=predicate_match,
                now=now,
            )
            scored.append(
                RetrievedMemory(
                    item=item,
                    score=score,
                    retrieval_text=retrieval_text,
                )
            )

        scored.sort(key=_retrieval_sort_key)
        deduplicated = _deduplicate(scored)
        budget = self._token_budget if token_budget is None else max(token_budget, 1)
        selected: list[RetrievedMemory] = []
        used_tokens = 0
        for result in deduplicated:
            if len(selected) >= limit:
                break
            estimated = _estimate_tokens(result.item)
            if selected and used_tokens + estimated > budget:
                continue
            selected.append(result)
            used_tokens += estimated
        return selected


class MemoryContextBuilder:
    """Turn ranked retrieval results into the existing typed context object."""

    def build(
        self,
        base: RelationshipContext,
        retrieved: Sequence[RetrievedMemory],
        *,
        active_plans: Sequence[RelationshipPlan] | None = None,
        relationship_evidence: RelationshipEvidenceProfile | None = None,
        reference_time: datetime | None = None,
    ) -> RelationshipContext:
        return attach_memories(
            base,
            [result.item for result in retrieved],
            active_plans=active_plans,
            relationship_evidence=relationship_evidence,
            reference_time=reference_time,
        )


_PREDICATE_TERMS: dict[str, frozenset[str]] = {
    "interaction.contact_frequency": frozenset(
        {"冷淡", "回复", "回我", "联系", "主动", "互动", "聊天", "见面", "频率", "少", "慢"}
    ),
    "interaction.response_engagement": frozenset(
        {"冷淡", "回复", "回我", "消息", "回应", "聊天", "慢", "少", "主动"}
    ),
    "relationship.conflict_status": frozenset(
        {"关系", "冲突", "冷战", "吵架", "矛盾", "争吵", "和好", "解决", "不开心"}
    ),
    "contact.status": frozenset(
        {"联系", "失联", "恢复", "正常", "回复", "主动", "冷淡"}
    ),
    "relationship.stage": frozenset(
        {"关系", "在一起", "分手", "复合", "对象", "恋爱"}
    ),
    "interaction.topic_scope": frozenset({"话题", "聊", "聊天", "内容"}),
    "interaction.channel": frozenset({"线上", "线下", "见面", "电话", "消息"}),
}
_PREFERENCE_TERMS = frozenset(
    {"喜欢", "偏好", "爱吃", "吃什么", "口味", "餐厅", "日料", "韩餐", "安静", "热闹"}
)
_EVENT_TERMS = frozenset({"发生", "事情", "事件", "经历"})
_PLAN_TERMS = frozenset(
    {"计划", "准备", "安排", "下周", "周末", "约会", "表白", "做什么", "合适", "适合", "下雨"}
)
_RELATIONSHIP_TERMS = frozenset(
    {"她", "对方", "我们", "关系", "伴侣", "对象", "冷淡", "联系", "回复", "冲突", "吵架"}
)


def _is_retrievable(item: MemoryItem, now: datetime) -> bool:
    if item.status not in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}:
        return False
    if item.expires_at is not None:
        expires_at = _align_timezone(item.expires_at, now)
        if expires_at <= now:
            return False
    return True


def _retrieval_text(item: MemoryItem) -> str:
    payload_values = " ".join(
        str(value)
        for key, value in item.payload.items()
        if key
        in {
            "predicate",
            "metric",
            "current",
            "direction",
            "state_dimension",
            "state_value",
            "preference",
            "preference_type",
            "object",
            "activity_type",
            "event_status",
        }
    )
    return " ".join(
        part
        for part in (
            item.summary,
            item.original_text,
            item.canonical_predicate,
            item.state_dimension,
            item.state_value,
            payload_values,
            " ".join(item.evidence_spans),
        )
        if part
    )


def _predicate_match(item: MemoryItem, query: str) -> float:
    if not query:
        return 0.0
    query_features = _features(query)
    predicate = item.canonical_predicate or item.state_dimension or ""
    terms = _PREDICATE_TERMS.get(predicate, frozenset())
    if item.kind == MemoryKind.PREFERENCE:
        terms = _PREFERENCE_TERMS
    elif item.kind in {MemoryKind.INTERACTION_EVENT, MemoryKind.ADVICE_OUTCOME}:
        terms = _EVENT_TERMS
    elif item.kind in {MemoryKind.PLANNED_EVENT, MemoryKind.ACTION_INTENT}:
        terms = _PLAN_TERMS
    if not terms:
        return 0.0
    matched = sum(1 for term in terms if term in query or term in query_features)
    return min(matched / max(min(len(terms), 4), 1), 1.0)


def _candidate_is_relevant(
    item: MemoryItem,
    query: str,
    *,
    lexical_similarity: float,
    semantic_similarity: float,
    predicate_match: float,
) -> bool:
    if lexical_similarity > 0 or semantic_similarity >= 0.35 or predicate_match > 0:
        return True
    if (
        memory_attention_reason(item) == "unresolved"
        and _query_has_personal_intent(query)
    ):
        return True
    if item.kind == MemoryKind.PREFERENCE and _query_has_personal_intent(query):
        return True
    query_features = _features(query)
    if _contains_query_term(query, _RELATIONSHIP_TERMS) or query_features & _RELATIONSHIP_TERMS:
        return memory_role(item) in {
            MemoryRole.CURRENT_STATE,
            MemoryRole.INTERACTION_PATTERN,
            MemoryRole.RECENT_EVENT,
        }
    return False


def _query_has_personal_intent(query: str) -> bool:
    features = _features(query)
    terms = _RELATIONSHIP_TERMS | _PREFERENCE_TERMS | _PLAN_TERMS
    return bool(features & terms or _contains_query_term(query, terms))


def _contains_query_term(query: str, terms: frozenset[str]) -> bool:
    return any(term in query for term in terms)


def _score(
    item: MemoryItem,
    *,
    semantic_similarity: float,
    predicate_match: float,
    now: datetime,
) -> MemoryRetrievalScore:
    timestamp = item.occurred_at or item.period_end or item.updated_at
    timestamp = _align_timezone(timestamp, now)
    age_days = max((now - timestamp).total_seconds() / 86400, 0.0)
    recency = 1.0 / (1.0 + age_days / 30.0)
    importance = min(max(item.importance / 5.0, 0.0), 1.0)
    confidence = min(max(item.confidence, 0.0), 1.0)
    lifecycle_priority = _lifecycle_priority(item)
    total = (
        semantic_similarity * 0.42
        + predicate_match * 0.25
        + recency * 0.10
        + importance * 0.08
        + confidence * 0.08
        + lifecycle_priority * 0.07
    )
    return MemoryRetrievalScore(
        semantic_similarity=semantic_similarity,
        predicate_match=predicate_match,
        recency=recency,
        importance=importance,
        confidence=confidence,
        lifecycle_priority=lifecycle_priority,
        total=total,
    )


def _lifecycle_priority(item: MemoryItem) -> float:
    status_priority = 1.0 if item.status == MemoryStatus.CONFIRMED else 0.35
    role_priority = {
        MemoryRole.CURRENT_STATE: 1.0,
        MemoryRole.PREFERENCE: 0.9,
        MemoryRole.INTERACTION_PATTERN: 0.8,
        MemoryRole.RECENT_EVENT: 0.7,
        MemoryRole.PLANNED_EVENT: 0.65,
        MemoryRole.ACTION_INTENT: 0.6,
        MemoryRole.STABLE_PROFILE: 0.55,
    }.get(memory_role(item), 0.5)
    return status_priority * role_priority


def _deduplicate(results: Sequence[RetrievedMemory]) -> list[RetrievedMemory]:
    grouped: dict[tuple[str, str, str], RetrievedMemory] = {}
    ungrouped: list[RetrievedMemory] = []
    for result in results:
        key = semantic_context_key(result.item)
        if key is None:
            ungrouped.append(result)
            continue
        previous = grouped.get(key)
        if previous is None or _retrieval_sort_key(result) < _retrieval_sort_key(previous):
            grouped[key] = result
    return sorted([*ungrouped, *grouped.values()], key=_retrieval_sort_key)


def _retrieval_sort_key(result: RetrievedMemory) -> tuple[float, int, float, str]:
    item = result.item
    return (
        -result.score.total,
        -int(item.status == MemoryStatus.CONFIRMED),
        -(item.occurred_at or item.period_end or item.updated_at).timestamp(),
        item.id,
    )


def _lexical_similarity(query: str, text: str) -> float:
    if not query:
        return 0.0
    query_features = _features(query)
    text_features = _features(text)
    if not query_features or not text_features:
        return 0.0
    return min(len(query_features & text_features) / len(query_features), 1.0)


def _features(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    features = set(re.findall(r"[a-z0-9_]{2,}", normalized))
    for block in re.findall(r"[\u4e00-\u9fff]+", normalized):
        features.update(block[index : index + 2] for index in range(len(block) - 1))
    return features


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    denominator = sqrt(sum(value * value for value in left)) * sqrt(
        sum(value * value for value in right)
    )
    if denominator == 0:
        return 0.0
    return max(min(sum(a * b for a, b in zip(left, right, strict=True)) / denominator, 1.0), 0.0)


def _estimate_tokens(item: MemoryItem) -> int:
    text = _retrieval_text(item)
    return max(1, (len(text) + 2) // 3)


def _align_timezone(value: datetime, reference: datetime) -> datetime:
    if value.tzinfo is None and reference.tzinfo is not None:
        return value.replace(tzinfo=reference.tzinfo)
    if value.tzinfo is not None and reference.tzinfo is None:
        return value.replace(tzinfo=None)
    return value


__all__ = [
    "HybridMemoryRetriever",
    "MemoryContextBuilder",
    "MemoryRetrievalScore",
    "RetrievedMemory",
]

import re

from loveapp.domain.enums import RelationshipStage
from loveapp.domain.knowledge import KnowledgeDocument, KnowledgeFilters, RetrievedDocument


def soft_rerank(
    query: str,
    matches: list[RetrievedDocument],
    preferences: KnowledgeFilters | None,
) -> list[RetrievedDocument]:
    reranked: list[RetrievedDocument] = []
    for match in matches:
        components = lexical_components(query, match.document)
        components.update(_metadata_components(match.document, preferences))
        score = match.score + sum(components.values())
        reranked.append(
            match.model_copy(
                update={
                    "score": round(score, 6),
                    "base_score": match.base_score or match.score,
                    "score_components": components,
                }
            )
        )
    return sorted(reranked, key=lambda item: item.score, reverse=True)


def lexical_components(query: str, document: KnowledgeDocument) -> dict[str, float]:
    query_terms = text_terms(query)
    if not query_terms:
        return {}

    title_terms = text_terms(f"{document.title}\n{document.question}")
    variant_terms = text_terms("\n".join(document.query_variants))
    title_overlap = len(query_terms & title_terms) / len(query_terms)
    variant_overlap = len(query_terms & variant_terms) / len(query_terms)
    tag_hits = sum(tag.casefold() in query.casefold() for tag in document.tags)
    components: dict[str, float] = {}
    if title_overlap:
        components["lexical_title"] = round(min(title_overlap * 0.12, 0.12), 6)
    if variant_overlap:
        components["lexical_variant"] = round(min(variant_overlap * 0.06, 0.06), 6)
    if tag_hits:
        components["lexical_tags"] = round(min(tag_hits * 0.035, 0.07), 6)
    return components


def text_terms(text: str) -> set[str]:
    normalized = text.casefold()
    words = set(re.findall(r"[a-z0-9_]+", normalized))
    chinese_sequences = re.findall(r"[\u4e00-\u9fff]+", normalized)
    characters = {character for sequence in chinese_sequences for character in sequence}
    bigrams = {
        sequence[index : index + 2]
        for sequence in chinese_sequences
        for index in range(len(sequence) - 1)
    }
    return words | characters | bigrams


def _metadata_components(
    document: KnowledgeDocument,
    preferences: KnowledgeFilters | None,
) -> dict[str, float]:
    if preferences is None:
        return {}
    components: dict[str, float] = {}
    if preferences.scenario_weights:
        weight = preferences.scenario_weights.get(document.scenario)
        if weight:
            components["scenario"] = round(0.12 * weight, 6)
    elif document.scenario == preferences.scenario:
        components["scenario"] = 0.07
    elif document.scenario in preferences.scenarios:
        components["scenario"] = 0.035

    if preferences.goal and preferences.goal in document.goals:
        components["goal"] = 0.05
    elif set(preferences.goals).intersection(document.goals):
        components["goal"] = 0.025
    if (
        preferences.relationship_stage not in (None, RelationshipStage.UNKNOWN)
        and preferences.relationship_stage in document.relationship_stages
    ):
        components["relationship_stage"] = 0.02
    return components

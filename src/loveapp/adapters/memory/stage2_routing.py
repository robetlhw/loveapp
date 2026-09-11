"""Role-aware routing helpers for the optional two-stage memory extractor.

Stage 1 is a semantic hand-off, not a write planner.  This module keeps the
hand-off explicit by mapping a coarse role to the *kind of reasoning* Stage 2
should use.  The router deliberately returns descriptive metadata only;
database targets and mutation commands remain outside this boundary.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from loveapp.domain.memory import (
    CoarseProposition,
    ExtractionEpistemicStatus,
    MemoryKind,
    SemanticRole,
    canonical_semantic_role,
)


class Stage2ExtractorRoute(StrEnum):
    """Bounded semantic extractor routes (not storage actions)."""

    NEW_MEMORY = "new_memory"
    EVENT_ENRICHMENT = "event_enrichment"
    STATE = "state"
    PATTERN = "pattern"
    REFINEMENT = "refinement"
    BELIEF = "belief"
    UNCERTAIN = "uncertain"


CANONICAL_SEMANTIC_ROLES: tuple[SemanticRole, ...] = (
    SemanticRole.NEW_PROPOSITION,
    SemanticRole.ATTRIBUTE_COMPLETION,
    SemanticRole.REFINEMENT,
    SemanticRole.CORRECTION,
    SemanticRole.UNCERTAIN,
)


# A short alias is useful to callers that use the terminology from the design
# document.  Keep the longer enum name as the canonical public symbol.
Stage2Route = Stage2ExtractorRoute


@dataclass(frozen=True)
class Stage2PromptRoute:
    """The semantic route selected for one coarse proposition.

    ``candidate_routes`` preserves ambiguity instead of silently selecting a
    top-one branch.  ``selected_route`` is ``UNCERTAIN`` whenever more than
    one concrete route remains.
    """

    proposition_id: str
    semantic_roles: tuple[SemanticRole, ...]
    candidate_routes: tuple[Stage2ExtractorRoute, ...]
    selected_route: Stage2ExtractorRoute
    reason: str
    candidate_kinds: tuple[MemoryKind, ...] = ()
    epistemic_status: ExtractionEpistemicStatus = ExtractionEpistemicStatus.UNCERTAIN
    rejected_combinations: tuple[tuple[str, str], ...] = ()

    @property
    def route(self) -> Stage2ExtractorRoute:
        """Compatibility spelling used by prompt/evaluation callers."""

        return self.selected_route

    @property
    def extractor_name(self) -> str:
        if self.selected_route == Stage2ExtractorRoute.NEW_MEMORY and self.candidate_kinds == (
            MemoryKind.INTERACTION_EVENT,
        ):
            return "NewEventExtractor"
        return {
            Stage2ExtractorRoute.NEW_MEMORY: "NewMemoryExtractor",
            Stage2ExtractorRoute.EVENT_ENRICHMENT: "EventEnrichmentExtractor",
            Stage2ExtractorRoute.STATE: "StateExtractor",
            Stage2ExtractorRoute.PATTERN: "PatternExtractor",
            Stage2ExtractorRoute.REFINEMENT: "RefinementExtractor",
            Stage2ExtractorRoute.BELIEF: "BeliefExtractor",
            Stage2ExtractorRoute.UNCERTAIN: "ConservativeSemanticExtractor",
        }[self.selected_route]

    def model_dump(self) -> dict[str, Any]:
        """Return a JSON-safe route description for diagnostics/prompts."""

        return {
            "proposition_id": self.proposition_id,
            "semantic_roles": [role.value for role in self.semantic_roles],
            "candidate_routes": [route.value for route in self.candidate_routes],
            "selected_route": self.selected_route.value,
            "extractor": self.extractor_name,
            "reason": self.reason,
            "candidate_kinds": [kind.value for kind in self.candidate_kinds],
            "epistemic_status": self.epistemic_status.value,
            "rejected_combinations": [
                {"semantic_role": role, "candidate_kind": kind, "reason": "incompatible_role_kind"}
                for role, kind in self.rejected_combinations
            ],
        }


_ROLE_TO_ROUTE: dict[SemanticRole, Stage2ExtractorRoute] = {
    SemanticRole.NEW_PROPOSITION: Stage2ExtractorRoute.NEW_MEMORY,
    # Values emitted by the first Stage-1 prompt are retained as aliases.
    SemanticRole.STANDALONE_PROPOSITION: Stage2ExtractorRoute.NEW_MEMORY,
    SemanticRole.ATTRIBUTE_COMPLETION: Stage2ExtractorRoute.EVENT_ENRICHMENT,
    SemanticRole.CONTEXTUAL_COMPLETION: Stage2ExtractorRoute.EVENT_ENRICHMENT,
    SemanticRole.ATTRIBUTE_UPDATE: Stage2ExtractorRoute.EVENT_ENRICHMENT,
    SemanticRole.REFINEMENT: Stage2ExtractorRoute.REFINEMENT,
    SemanticRole.CORRECTION: Stage2ExtractorRoute.NEW_MEMORY,
    SemanticRole.REFINEMENT_CANDIDATE: Stage2ExtractorRoute.REFINEMENT,
    SemanticRole.STATE_UPDATE: Stage2ExtractorRoute.STATE,
    SemanticRole.STATE_ASSERTION: Stage2ExtractorRoute.STATE,
    SemanticRole.PATTERN_EXTRACTION: Stage2ExtractorRoute.PATTERN,
    SemanticRole.BELIEF_EXTRACTION: Stage2ExtractorRoute.BELIEF,
    SemanticRole.UNCERTAIN: Stage2ExtractorRoute.UNCERTAIN,
}


def _route_for_role_and_kind(
    role: SemanticRole,
    kind: MemoryKind,
    epistemic: ExtractionEpistemicStatus,
) -> Stage2ExtractorRoute | None:
    """Evaluate one matrix cell; never discard a role's other legal routes."""

    if role in {SemanticRole.NEW_PROPOSITION, SemanticRole.CORRECTION}:
        # Correction supplies the corrected claim to existing governance. It
        # is neither a precision-only refinement nor a model-authorized write.
        if epistemic == ExtractionEpistemicStatus.BELIEVED:
            return Stage2ExtractorRoute.BELIEF
        if kind == MemoryKind.INTERACTION_PATTERN:
            return Stage2ExtractorRoute.PATTERN
        if kind == MemoryKind.RELATIONSHIP_STATE:
            return Stage2ExtractorRoute.STATE
        return Stage2ExtractorRoute.NEW_MEMORY
    if role == SemanticRole.ATTRIBUTE_COMPLETION:
        return (
            Stage2ExtractorRoute.EVENT_ENRICHMENT if kind == MemoryKind.INTERACTION_EVENT else None
        )
    if role == SemanticRole.REFINEMENT:
        # Event attribute detail belongs to completion. Refinement is the
        # existing trace-only path for making non-event propositions precise.
        return (
            Stage2ExtractorRoute.REFINEMENT
            if kind not in {MemoryKind.INTERACTION_EVENT, MemoryKind.ADVICE_OUTCOME}
            else None
        )
    if role == SemanticRole.UNCERTAIN:
        return Stage2ExtractorRoute.UNCERTAIN
    return None


def route_semantic_role(role: SemanticRole | str | None) -> Stage2ExtractorRoute:
    """Map one role spelling to a bounded Stage-2 route.

    Unknown values are intentionally conservative.  This function is used at
    the adapter boundary, so it never invents a database action.
    """

    if not isinstance(role, SemanticRole):
        normalized = str(role or "").strip().casefold().replace("-", "_").replace(" ", "_")
        try:
            role = SemanticRole(normalized)
        except ValueError:
            aliases = {
                "new": SemanticRole.NEW_PROPOSITION,
                "standalone": SemanticRole.STANDALONE_PROPOSITION,
                "attribute": SemanticRole.ATTRIBUTE_COMPLETION,
                "enrichment": SemanticRole.ATTRIBUTE_COMPLETION,
                "context": SemanticRole.CONTEXTUAL_COMPLETION,
                "state": SemanticRole.STATE_UPDATE,
                "state_assertion": SemanticRole.STATE_ASSERTION,
                "pattern": SemanticRole.PATTERN_EXTRACTION,
                "belief": SemanticRole.BELIEF_EXTRACTION,
                "update": SemanticRole.STATE_UPDATE,
            }
            role = aliases.get(normalized, SemanticRole.UNCERTAIN)
    return _ROLE_TO_ROUTE.get(role, Stage2ExtractorRoute.UNCERTAIN)


class SemanticRoleRouter:
    """Resolve coarse role candidates into a Stage-2 prompt route."""

    def route(self, proposition: CoarseProposition) -> Stage2PromptRoute:
        roles: list[SemanticRole] = []
        for value in [
            *list(getattr(proposition, "semantic_role_candidates", []) or []),
            getattr(proposition, "semantic_role", SemanticRole.UNCERTAIN),
        ]:
            if isinstance(value, str):
                try:
                    value = SemanticRole(value)
                except ValueError:
                    continue
            if isinstance(value, SemanticRole):
                operation = canonical_semantic_role(value)
                if operation not in roles:
                    roles.append(operation)
        if not roles:
            roles = [SemanticRole.UNCERTAIN]

        # ``UNCERTAIN`` is a fallback hint, not a competing concrete route.
        concrete_roles = [role for role in roles if role != SemanticRole.UNCERTAIN]
        considered_roles = concrete_roles or [SemanticRole.UNCERTAIN]
        routes: list[Stage2ExtractorRoute] = []
        rejected: list[tuple[str, str]] = []
        for role in considered_roles:
            for kind in proposition.candidate_kinds:
                route = _route_for_role_and_kind(role, kind, proposition.epistemic_status)
                if route is None:
                    rejected.append((role.value, kind.value))
                elif route not in routes:
                    routes.append(route)
        if len(routes) == 1:
            selected = routes[0]
            reason = "single_compatible_semantic_role" if rejected else "single_semantic_role"
        elif len(routes) == 0:
            selected = Stage2ExtractorRoute.UNCERTAIN
            reason = "no_compatible_role_kind_combination"
        else:
            selected = Stage2ExtractorRoute.UNCERTAIN
            reason = "multiple_semantic_routes_fail_closed"
        return Stage2PromptRoute(
            proposition_id=proposition.proposition_id,
            semantic_roles=tuple(roles),
            candidate_routes=tuple(routes),
            selected_route=selected,
            reason=reason,
            candidate_kinds=tuple(proposition.candidate_kinds),
            epistemic_status=proposition.epistemic_status,
            rejected_combinations=tuple(rejected),
        )

    def route_many(self, propositions: Iterable[CoarseProposition]) -> list[Stage2PromptRoute]:
        return [self.route(proposition) for proposition in propositions]

    __call__ = route


def route_stage2_role(
    role_or_proposition: SemanticRole | str | CoarseProposition | None,
) -> Stage2ExtractorRoute | Stage2PromptRoute:
    """Convenience API for tests and adapter integrations."""

    if isinstance(role_or_proposition, CoarseProposition):
        return SemanticRoleRouter().route(role_or_proposition)
    return route_semantic_role(role_or_proposition)


ROLE_INSTRUCTIONS: dict[Stage2ExtractorRoute, str] = {
    Stage2ExtractorRoute.NEW_MEMORY: (
        "NewMemoryExtractor：只识别当前文本中独立、可定位的新命题。若有新的时间标记、"
        "有边界 occurrence 和 event predicate，必须输出 new_memory；"
        "不得把新事件当作旧 Event enrichment。"
    ),
    Stage2ExtractorRoute.EVENT_ENRICHMENT: (
        "EventEnrichmentExtractor：只补充已有 interaction_event 的属性（cause、severity、emotion、"
        "resolution、outcome、location、activity_type）。‘说开/和解/解决’在已有 active conflict"
        "语境中应表达为 resolution enrichment；只输出语义草稿，不输出 target id 或 ENRICH 命令。"
    ),
    Stage2ExtractorRoute.STATE: (
        "StateExtractor：将当前关系状态表达为 relationship_state，并明确一个已注册的"
        "state_dimension/state_value；不要把当前状态降级成普通 Event attribute。"
    ),
    Stage2ExtractorRoute.PATTERN: (
        "PatternExtractor：输出 interaction_pattern 时必须保留一个已注册 metric（如"
        "initiation_balance、interaction_frequency/contact_frequency、response_engagement、"
        "conflict_frequency）及可观察 value/direction；不能只输出 pattern=true。"
    ),
    Stage2ExtractorRoute.REFINEMENT: (
        "RefinementExtractor：同一语义对象仅变得更具体时输出 refinement；如果 predicate/value"
        "发生事实冲突，只描述新的命题，不能输出 UPDATE/SUPERSEDE 或数据库操作。"
    ),
    Stage2ExtractorRoute.BELIEF: (
        "BeliefExtractor：用户的感觉、可能、猜测和担忧使用 user_belief/uncertain 语义；"
        "不得升级为 confirmed 的客观关系状态或替用户确认第三方事实。"
    ),
    Stage2ExtractorRoute.UNCERTAIN: (
        "ConservativeSemanticExtractor：候选角色冲突或证据不足时保留不确定性，宁可不产出"
        "语义单位；不得猜测目标、关系或 mutation。"
    ),
}


def route_instruction(route: Stage2ExtractorRoute | str) -> str:
    """Return the bounded instruction block for one route."""

    if not isinstance(route, Stage2ExtractorRoute):
        try:
            route = Stage2ExtractorRoute(str(route))
        except ValueError:
            route = Stage2ExtractorRoute.UNCERTAIN
    return ROLE_INSTRUCTIONS[route]


# These patterns are intentionally broad semantic guards, not a replacement
# for the downstream event resolver.  They are used only to stop a clearly new
# bounded occurrence from being routed as an old-event enrichment.
_NEW_TIME_MARKER_PATTERN = re.compile(
    r"(?:今天|今日|昨天|前天|刚才|刚刚|这次|又|再次|重新|前几天|最近又|"
    r"\btoday\b|\byesterday\b|\bagain\b|\bthis\s+time\b|\banother\b)",
    re.IGNORECASE,
)
_EVENT_PREDICATE_PATTERN = re.compile(
    r"(?:吵架|吵了一架|争吵|争执|矛盾|冲突|冷战|约会|见面|吃饭|聊天|发生|"
    r"\b(?:argu|fight|quarrel|conflict|date|met|went|occurrence)\w*\b)",
    re.IGNORECASE,
)


def is_new_event_occurrence(text: str, evidence_span: str | None = None) -> bool:
    """Return whether text contains a bounded new Event occurrence marker."""

    haystack = " ".join(
        value.strip()
        for value in (text, evidence_span or "")
        if isinstance(value, str) and value.strip()
    )
    return bool(
        haystack
        and _NEW_TIME_MARKER_PATTERN.search(haystack)
        and _EVENT_PREDICATE_PATTERN.search(haystack)
    )


__all__ = [
    "CANONICAL_SEMANTIC_ROLES",
    "ROLE_INSTRUCTIONS",
    "SemanticRoleRouter",
    "Stage2ExtractorRoute",
    "Stage2PromptRoute",
    "Stage2Route",
    "is_new_event_occurrence",
    "route_instruction",
    "route_semantic_role",
    "route_stage2_role",
]

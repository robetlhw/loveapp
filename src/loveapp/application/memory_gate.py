import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from loveapp.application.contextual_memory_updates import (
    detect_contextual_signal,
    may_contain_contextual_memory_update,
    resolve_contextual_memory_update,
)
from loveapp.application.date_planning.clause_parsing import split_date_clauses
from loveapp.application.relationship_events import resolve_contextual_relationship_event
from loveapp.domain.enums import TaskType
from loveapp.domain.memory import (
    MemoryGateDecision,
    MemoryGateReason,
    MemoryItem,
    MemoryL0Route,
    MemorySemanticGateReason,
    MessageRole,
    StoredMessage,
)
from loveapp.domain.relationship_plan import has_retrospective_event_semantics
from loveapp.domain.runtime_context import PendingMemoryContext


@dataclass(frozen=True)
class _GateMatch:
    rule: str
    span: str


@dataclass(frozen=True)
class _PendingMemoryQuestion:
    category: str
    rule: str
    text: str


@dataclass(frozen=True)
class _L0RouteMatch:
    semantic_reason: MemorySemanticGateReason
    signal: str
    rule: str
    span: str
    legacy_reason: MemoryGateReason = MemoryGateReason.DURABLE_SIGNAL


class MemoryGate:
    def evaluate(
        self,
        text: str,
        *,
        conversation_history: Iterable[StoredMessage] = (),
        existing_memories: Iterable[MemoryItem] = (),
        active_task: TaskType | None = None,
    ) -> MemoryGateDecision:
        conversation_history = list(conversation_history)
        existing_memories = list(existing_memories)
        normalized = _normalize(text)
        compact = _compact(normalized)
        durable_preference_clause = _durable_preference_clause(normalized)
        if compact in _CASUAL_MESSAGES or any(
            pattern.fullmatch(normalized) for pattern in _CASUAL_PATTERNS
        ):
            return _skip(
                MemoryGateReason.CASUAL,
                "exact_casual",
                matched_rule="casual_exact",
                matched_span=normalized,
            )
        hypothetical = _first_match(normalized, _HYPOTHETICAL_PATTERNS, "hypothetical")
        if hypothetical is not None:
            return _skip(
                MemoryGateReason.HYPOTHETICAL,
                "hypothetical framing",
                matched_rule=hypothetical.rule,
                matched_span=hypothetical.span,
            )
        operation = _first_match(normalized, _OPERATION_PATTERNS, "operation")
        if operation is not None and durable_preference_clause is None:
            return _skip(
                MemoryGateReason.OPERATION,
                "agent operation",
                matched_rule=operation.rule,
                matched_span=operation.span,
            )
        knowledge_question = _first_match(
            normalized,
            _KNOWLEDGE_QUESTION_PATTERNS,
            "knowledge_question",
        )
        if knowledge_question is not None:
            return _skip(
                MemoryGateReason.KNOWLEDGE_QUESTION,
                "generic knowledge",
                matched_rule=knowledge_question.rule,
                matched_span=knowledge_question.span,
            )
        explicit_remember = _first_match(
            normalized,
            _EXPLICIT_REMEMBER_PATTERNS,
            "explicit_remember",
        )
        if explicit_remember is not None:
            return MemoryGateDecision(
                should_extract=True,
                reason=MemoryGateReason.EXPLICIT_REMEMBER,
                signals=["explicit remember request"],
                matched_rule=explicit_remember.rule,
                matched_span=explicit_remember.span,
            )
        task_local_date_constraint = _task_local_date_constraint(normalized, active_task)
        if task_local_date_constraint is not None:
            return _skip(
                MemoryGateReason.OPERATION,
                "task-local date planning constraint",
                matched_rule="date_task_local_constraint",
                matched_span=task_local_date_constraint,
            )

        future_reversal = _first_match(
            normalized,
            _FUTURE_BEHAVIORAL_REVERSAL_PATTERNS,
            "future_behavioral_reversal",
        )
        if future_reversal is not None:
            return _skip(
                MemoryGateReason.HYPOTHETICAL,
                "future or speculative behavioral concern",
                matched_rule=future_reversal.rule,
                matched_span=future_reversal.span,
            )
        one_off_interaction = _first_match(
            normalized,
            _ONE_OFF_INTERACTION_EVENT_PATTERNS,
            "single_interaction_event",
        )
        if one_off_interaction is not None:
            return _skip(
                MemoryGateReason.NO_DURABLE_SIGNAL,
                "single short-lived interaction event",
                matched_rule=one_off_interaction.rule,
                matched_span=one_off_interaction.span,
            )

        contextual_update = resolve_contextual_memory_update(
            text,
            conversation_history,
            existing_memories,
        )
        if contextual_update.resolved:
            return MemoryGateDecision(
                should_extract=True,
                reason=MemoryGateReason.CONTEXTUAL_UPDATE,
                signals=["contextual_memory_update", contextual_update.update_type.value],
                matched_rule=f"contextual_{contextual_update.update_type.value}",
                matched_span=contextual_update.evidence_span,
                contextual_probe=True,
                antecedent_candidate_ids=list(contextual_update.candidate_ids),
                selected_target_memory_id=contextual_update.target.id,
                target_guard_result="compatible_active_target",
                contextual_update_type=contextual_update.update_type.value,
            )

        contextual_signal = detect_contextual_signal(
            text,
            conversation_history,
        )

        contextual_event = resolve_contextual_relationship_event(
            text,
            conversation_history,
            existing_memories,
        )
        if contextual_event is not None:
            return MemoryGateDecision(
                should_extract=True,
                reason=MemoryGateReason.DURABLE_SIGNAL,
                signals=["contextual_relationship_event", contextual_event.signal],
                matched_rule="contextual_relationship_event",
                matched_span=text,
            )

        if has_retrospective_event_semantics(text):
            return MemoryGateDecision(
                should_extract=True,
                reason=MemoryGateReason.DURABLE_SIGNAL,
                signals=["retrospective_event_semantics"],
                matched_rule="retrospective_event_semantics",
                matched_span=text,
                contextual_probe=may_contain_contextual_memory_update(text),
            )

        pure_partner_hypothesis = _first_match(
            normalized,
            _PURE_PARTNER_HYPOTHESIS_PATTERNS,
            "pure_partner_hypothesis",
        )
        if pure_partner_hypothesis is not None:
            return _skip(
                MemoryGateReason.CONSULTATION_ONLY,
                "partner hypothesis without observable claim",
                matched_rule=pure_partner_hypothesis.rule,
                matched_span=pure_partner_hypothesis.span,
            )

        relationship_action_consultation = _first_match(
            normalized,
            _RELATIONSHIP_ACTION_CONSULTATION_PATTERNS,
            "relationship_action_consultation",
        )
        if relationship_action_consultation is not None:
            return _skip(
                MemoryGateReason.CONSULTATION_ONLY,
                "relationship action consultation without observable claim",
                matched_rule=relationship_action_consultation.rule,
                matched_span=relationship_action_consultation.span,
            )

        signals = [
            name
            for name, patterns in _DURABLE_SIGNAL_PATTERNS.items()
            if any(pattern.search(normalized) for pattern in patterns)
        ]
        if "planned_event" in signals and _is_habitual_not_future(normalized):
            signals.remove("planned_event")
        interaction_decline = _find_interaction_decline(normalized)
        if interaction_decline is not None and "interaction_decline" not in signals:
            signals.append("interaction_decline")
        interaction_qualifier = _find_interaction_qualifier(normalized)
        if interaction_qualifier is not None and "interaction_qualifier" not in signals:
            signals.append("interaction_qualifier")
        durable_reversal = _first_match(
            normalized,
            _DURABLE_BEHAVIORAL_REVERSAL_PATTERNS,
            "durable_behavioral_reversal",
        )
        if durable_reversal is not None and "durable_behavioral_reversal" not in signals:
            signals.append("durable_behavioral_reversal")
        if signals:
            if contextual_signal is not None:
                signals.append(f"contextual_{contextual_signal.category}")
                if contextual_signal.history_derived:
                    signals.append("contextual_history_derived")
            generic_match = _first_signal_match(normalized, signals)
            contextual_match = (
                _GateMatch(
                    contextual_signal.matched_rule,
                    contextual_signal.matched_span,
                )
                if contextual_signal is not None
                else None
            )
            matched = (
                contextual_match
                or interaction_decline
                or interaction_qualifier
                or generic_match
                or durable_reversal
            )
            return MemoryGateDecision(
                should_extract=True,
                reason=MemoryGateReason.DURABLE_SIGNAL,
                signals=signals,
                matched_rule=matched.rule if matched is not None else None,
                matched_span=matched.span if matched is not None else None,
                contextual_probe=may_contain_contextual_memory_update(text),
            )
        if contextual_signal is not None:
            signal_names = [
                "contextual_signal",
                f"contextual_{contextual_signal.category}",
            ]
            if contextual_signal.history_derived:
                signal_names.append("contextual_history_derived")
            return MemoryGateDecision(
                should_extract=True,
                reason=MemoryGateReason.DURABLE_SIGNAL,
                signals=signal_names,
                matched_rule=contextual_signal.matched_rule,
                matched_span=contextual_signal.matched_span,
                contextual_probe=True,
            )
        if contextual_update.semantic_candidate_ids:
            return MemoryGateDecision(
                should_extract=False,
                reason=MemoryGateReason.NO_DURABLE_SIGNAL,
                signals=[
                    "contextual_memory_update_unresolved",
                    contextual_update.reason,
                ],
                matched_rule=(
                    f"contextual_{contextual_update.update_type.value}_unresolved"
                    if contextual_update.update_type is not None
                    else "contextual_update_unresolved"
                ),
                matched_span=contextual_update.evidence_span,
                contextual_probe=True,
                antecedent_candidate_ids=list(contextual_update.semantic_candidate_ids),
                target_guard_result=contextual_update.reason,
                contextual_update_type=(
                    contextual_update.update_type.value
                    if contextual_update.update_type is not None
                    else None
                ),
            )
        if durable_reversal is not None:
            return MemoryGateDecision(
                should_extract=True,
                reason=MemoryGateReason.DURABLE_SIGNAL,
                signals=["durable_behavioral_reversal"],
                matched_rule=durable_reversal.rule,
                matched_span=durable_reversal.span,
                contextual_probe=may_contain_contextual_memory_update(text),
            )
        consultation = _first_match(
            normalized,
            _CONSULTATION_PATTERNS,
            "consultation_question",
        )
        if consultation is not None:
            return _skip(
                MemoryGateReason.CONSULTATION_ONLY,
                "question without durable claim",
                matched_rule=consultation.rule,
                matched_span=consultation.span,
            )
        return _skip(
            MemoryGateReason.NO_DURABLE_SIGNAL,
            "no durable signal",
            matched_rule="no_durable_signal",
        )

    def route_v2(
        self,
        text: str,
        *,
        conversation_history: Iterable[StoredMessage] = (),
        existing_memories: Iterable[MemoryItem] = (),
        active_task: TaskType | None = None,
        pending_memory_context: PendingMemoryContext | None = None,
    ) -> MemoryGateDecision:
        """Route a turn to the existing extractor without deciding its semantics.

        The legacy ``evaluate`` method remains the A-side baseline.  V2 only
        hard-drops input when the absence of memory value is cheap and clear;
        ambiguous input is deliberately delegated to the extractor's semantic
        gate.  A pending Assistant memory question takes precedence because a
        short answer may be meaningless in isolation.
        """

        history = list(conversation_history)
        pending_source: str | None = None
        pending_match: _PendingMemoryQuestion | None = None
        pending = (
            pending_memory_context
            if pending_memory_context is not None
            and pending_memory_context.memory_relevant
            else None
        )
        if pending is not None:
            pending_source = "structured"
        elif pending_memory_context is None:
            pending_match = _pending_memory_question(history)
            if pending_match is not None:
                latest_assistant = next(
                    (
                        message
                        for message in reversed(history)
                        if message.role == MessageRole.ASSISTANT
                        and message.content.strip()
                    ),
                    None,
                )
                pending = _pending_context_from_match(
                    pending_match,
                    question=(
                        latest_assistant.content.strip()
                        if latest_assistant is not None
                        else pending_match.text
                    ),
                    created_turn="history:legacy",
                )
                pending_source = "history_fallback"
            else:
                pending_match = None
        if pending is not None:
            return _v2_decision(
                MemoryL0Route.CONTEXT_PASS,
                semantic_reason=MemorySemanticGateReason.CONTEXT_DEPENDENT_REPLY,
                signal=f"pending_memory_question:{pending.expected_slot or 'unspecified'}",
                matched_rule=(
                    "structured_pending_memory_context"
                    if pending_source == "structured"
                    else (
                        pending_match.rule
                        if pending_match is not None
                        else _pending_rule_for_slot(pending.expected_slot)
                    )
                ),
                matched_span=pending.previous_assistant_question,
                history_loaded=bool(history),
                pending_memory_context=pending,
                pending_memory_context_source=pending_source,
            )

        normalized = _normalize(text)
        compact = _compact(normalized)
        legacy = self.evaluate(
            text,
            conversation_history=history,
            existing_memories=existing_memories,
            active_task=active_task,
        )
        if legacy.contextual_probe and (
            legacy.reason == MemoryGateReason.CONTEXTUAL_UPDATE
            or legacy.target_guard_result is not None
        ):
            # Keep the existing antecedent resolver's authorization result.
            # V2 may send an unresolved contextual turn to Flash for semantic
            # review, but Flash must never turn a denied target guard into a
            # write authorization.
            return legacy.model_copy(
                update={
                    "should_extract": True,
                    "l0_route": MemoryL0Route.SEMANTIC_REVIEW,
                    "l0_semantic_hint": None,
                    "semantic_gate_reason": None,
                    "history_loaded_for_gate": bool(history),
                }
            )
        hard_drop = _l0_hard_drop(normalized, compact, legacy=legacy)
        if hard_drop is not None:
            return _v2_decision(
                MemoryL0Route.HARD_DROP,
                semantic_reason=hard_drop.semantic_reason,
                signal=hard_drop.signal,
                matched_rule=hard_drop.rule,
                matched_span=hard_drop.span,
                history_loaded=bool(history),
                legacy_reason=hard_drop.legacy_reason,
            )

        hard_pass = _l0_hard_pass(normalized)
        if hard_pass is not None:
            return _v2_decision(
                MemoryL0Route.HARD_PASS,
                semantic_reason=hard_pass.semantic_reason,
                signal=hard_pass.signal,
                matched_rule=hard_pass.rule,
                matched_span=hard_pass.span,
                history_loaded=bool(history),
            )

        return _v2_decision(
            MemoryL0Route.SEMANTIC_REVIEW,
            semantic_reason=None,
            signal=(legacy.signals[0] if legacy.signals else "semantic_review"),
            matched_rule=legacy.matched_rule or "semantic_review_default",
            matched_span=legacy.matched_span,
            history_loaded=bool(history),
            legacy_decision=legacy,
        )


def _v2_decision(
    route: MemoryL0Route,
    *,
    semantic_reason: MemorySemanticGateReason | None,
    signal: str,
    matched_rule: str,
    matched_span: str | None,
    history_loaded: bool,
    legacy_reason: MemoryGateReason = MemoryGateReason.DURABLE_SIGNAL,
    legacy_decision: MemoryGateDecision | None = None,
    pending_memory_context: PendingMemoryContext | None = None,
    pending_memory_context_source: str | None = None,
) -> MemoryGateDecision:
    signals = [f"l0_{route.value}", signal]
    if legacy_decision is not None:
        signals = list(dict.fromkeys([f"l0_{route.value}", *legacy_decision.signals]))
    return MemoryGateDecision(
        should_extract=route != MemoryL0Route.HARD_DROP,
        reason=(
            legacy_reason
            if route == MemoryL0Route.HARD_DROP
            else MemoryGateReason.DURABLE_SIGNAL
        ),
        signals=signals,
        matched_rule=matched_rule,
        matched_span=matched_span,
        history_loaded_for_gate=history_loaded,
        l0_route=route,
        l0_semantic_hint=semantic_reason,
        pending_memory_context=pending_memory_context,
        pending_memory_context_source=pending_memory_context_source,
    )


def build_pending_memory_context(
    questions: Iterable[str],
    *,
    created_turn: str,
    expires_after_turns: int = 2,
    previous_context: PendingMemoryContext | None = None,
) -> PendingMemoryContext | None:
    """Register one unambiguous structured Assistant memory follow-up."""

    normalized_questions = [question.strip() for question in questions if question.strip()]
    matches = [
        (question, matched)
        for question in normalized_questions
        for matched in [_match_memory_question(question)]
        if matched is not None
    ]
    if len(matches) == 1:
        question, matched = matches[0]
        return _pending_context_from_match(
            matched,
            question=question,
            created_turn=created_turn,
            expires_after_turns=expires_after_turns,
        )
    if (
        not matches
        and len(normalized_questions) == 1
        and previous_context is not None
        and _PENDING_CONFIRMATION_PATTERN.search(normalized_questions[0]) is not None
    ):
        return previous_context.model_copy(
            update={
                "previous_assistant_question": normalized_questions[0],
                "created_turn": created_turn,
                "expires_after_turns": expires_after_turns,
            }
        )
    return None


def pending_memory_context_from_history(
    conversation_history: Iterable[StoredMessage],
    *,
    created_turn: str | None = None,
) -> PendingMemoryContext | None:
    """Compatibility adapter for transcripts created before structured context."""

    history = list(conversation_history)
    pending = _pending_memory_question(history)
    if pending is None:
        return None
    latest_assistant = next(
        (
            message
            for message in reversed(history)
            if message.role == MessageRole.ASSISTANT and message.content.strip()
        ),
        None,
    )
    source_turn = created_turn or (
        f"message:{latest_assistant.id}" if latest_assistant is not None else "history:unknown"
    )
    return _pending_context_from_match(
        pending,
        question=(
            latest_assistant.content.strip()
            if latest_assistant is not None
            else pending.text
        ),
        created_turn=source_turn,
    )


def _pending_context_from_match(
    pending: _PendingMemoryQuestion,
    *,
    question: str,
    created_turn: str,
    expires_after_turns: int = 2,
) -> PendingMemoryContext:
    return PendingMemoryContext(
        previous_assistant_question=question,
        memory_relevant=True,
        expected_slot=_pending_slot(pending.category),
        topic=_pending_topic(question),
        created_turn=created_turn,
        expires_after_turns=expires_after_turns,
    )


def _pending_slot(category: str) -> str:
    return {
        "cause_scope": "cause",
        "relationship_interaction": "interaction_state",
    }.get(category, category)


def _pending_rule_for_slot(slot: str | None) -> str:
    return {
        "duration": "pending_memory_duration_question",
        "cause": "pending_memory_cause_question",
        "actor": "pending_memory_actor_question",
        "interaction_state": "pending_memory_interaction_question",
    }.get(slot or "", "pending_memory_context_history_fallback")


def _pending_topic(question: str) -> str:
    if re.search(r"分手|复合|在一起", question):
        return "relationship_transition"
    if re.search(r"冷战|吵|矛盾|冲突", question):
        return "conflict"
    if re.search(r"联系|回复|聊天|交流|沟通|见面|道歉|情绪", question):
        return "interaction"
    return "relationship"


def _pending_memory_question(
    conversation_history: list[StoredMessage],
) -> _PendingMemoryQuestion | None:
    latest_assistant_index = next(
        (
            index
            for index in range(len(conversation_history) - 1, -1, -1)
            if conversation_history[index].role == MessageRole.ASSISTANT
            and conversation_history[index].content.strip()
        ),
        None,
    )
    if latest_assistant_index is None:
        return None
    user_turns_since = sum(
        message.role == MessageRole.USER
        for message in conversation_history[latest_assistant_index + 1 :]
    )
    # The current turn can be the first or second user answer. Older questions
    # must not keep routing unrelated conversation into Memory indefinitely.
    if user_turns_since >= 2:
        return None

    latest = conversation_history[latest_assistant_index].content.strip()
    direct = _match_memory_question(latest)
    if direct is not None:
        return direct
    confirmation = _PENDING_CONFIRMATION_PATTERN.search(latest)
    if confirmation is None:
        return None

    # A confirmation question is itself sufficient pending Memory context.
    # Earlier turns can refine its category, but are not required (the eval
    # and production history window may begin at this Assistant message).
    fallback = _PendingMemoryQuestion(
        category="confirmation",
        rule="pending_memory_confirmation",
        text=confirmation.group(0),
    )

    prior_user_turns = user_turns_since
    for message in reversed(conversation_history[:latest_assistant_index]):
        if message.role == MessageRole.USER:
            prior_user_turns += 1
            if prior_user_turns >= 2:
                break
            continue
        if message.role != MessageRole.ASSISTANT or not message.content.strip():
            continue
        prior = _match_memory_question(message.content.strip())
        if prior is not None:
            return _PendingMemoryQuestion(
                category=prior.category,
                rule="pending_memory_confirmation",
                text=latest,
            )
    return fallback


def _match_memory_question(text: str) -> _PendingMemoryQuestion | None:
    if _QUESTION_MARK_PATTERN.search(text) is None:
        return None
    for category, rule, pattern in _PENDING_MEMORY_QUESTION_RULES:
        match = pattern.search(text)
        if match is not None:
            return _PendingMemoryQuestion(
                category=category,
                rule=rule,
                text=match.group(0),
            )
    return None


def _l0_hard_drop(
    text: str,
    compact: str,
    *,
    legacy: MemoryGateDecision,
) -> _L0RouteMatch | None:
    if compact in _L0_ACKNOWLEDGEMENT_MESSAGES:
        return _L0RouteMatch(
            semantic_reason=MemorySemanticGateReason.NO_MEMORY,
            signal="acknowledgement",
            rule="l0_acknowledgement",
            span=text,
            legacy_reason=MemoryGateReason.CASUAL,
        )
    if (
        legacy.reason == MemoryGateReason.CONSULTATION_ONLY
        and (legacy.matched_rule or "").startswith(
            "relationship_action_consultation_"
        )
    ):
        return _L0RouteMatch(
            semantic_reason=MemorySemanticGateReason.NO_MEMORY,
            signal="pure_relationship_action_consultation",
            rule=legacy.matched_rule or "l0_relationship_action_consultation",
            span=legacy.matched_span or text,
            legacy_reason=legacy.reason,
        )
    casual = _first_match(text, _L0_SMALL_TALK_PATTERNS, "l0_small_talk")
    if casual is not None:
        return _L0RouteMatch(
            semantic_reason=MemorySemanticGateReason.SMALL_TALK,
            signal="small_talk",
            rule=casual.rule,
            span=casual.span,
            legacy_reason=MemoryGateReason.CASUAL,
        )
    if legacy.reason in {
        MemoryGateReason.CASUAL,
        MemoryGateReason.KNOWLEDGE_QUESTION,
        MemoryGateReason.OPERATION,
        MemoryGateReason.HYPOTHETICAL,
    }:
        semantic_reason = (
            MemorySemanticGateReason.SMALL_TALK
            if legacy.reason == MemoryGateReason.CASUAL
            else MemorySemanticGateReason.NO_MEMORY
        )
        return _L0RouteMatch(
            semantic_reason=semantic_reason,
            signal=f"legacy_{legacy.reason.value}",
            rule=legacy.matched_rule or f"l0_{legacy.reason.value}",
            span=legacy.matched_span or text,
            legacy_reason=legacy.reason,
        )
    if not legacy.should_extract:
        consultation = _first_match(
            text,
            _L0_PURE_CONSULTATION_PATTERNS,
            "l0_pure_consultation",
        )
        if consultation is not None:
            return _L0RouteMatch(
                semantic_reason=MemorySemanticGateReason.NO_MEMORY,
                signal="pure_consultation",
                rule=consultation.rule,
                span=consultation.span,
                legacy_reason=MemoryGateReason.CONSULTATION_ONLY,
            )
    unrelated = _first_match(
        text,
        _L0_UNRELATED_TRANSIENT_PATTERNS,
        "l0_unrelated_transient",
    )
    if unrelated is not None and _RELATIONSHIP_MENTION_PATTERN.search(text) is None:
        return _L0RouteMatch(
            semantic_reason=MemorySemanticGateReason.NO_MEMORY,
            signal="unrelated_transient",
            rule=unrelated.rule,
            span=unrelated.span,
            legacy_reason=MemoryGateReason.NO_DURABLE_SIGNAL,
        )
    return None


def _l0_hard_pass(text: str) -> _L0RouteMatch | None:
    explicit_remember = _L0_EXPLICIT_REMEMBER_PATTERN.search(text)
    if explicit_remember is not None:
        return _L0RouteMatch(
            semantic_reason=MemorySemanticGateReason.STABLE_FACT,
            signal="explicit_remember",
            rule="l0_explicit_remember",
            span=explicit_remember.group(0),
        )
    if _L0_SEMANTIC_REVIEW_CUE_PATTERN.search(text) is not None:
        return None
    for semantic_reason, signal, rule, pattern in _L0_HARD_PASS_RULES:
        match = pattern.search(text)
        if match is not None:
            return _L0RouteMatch(
                semantic_reason=semantic_reason,
                signal=signal,
                rule=rule,
                span=match.group(0),
            )
    return None


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()


def _compact(value: str) -> str:
    return re.sub(r"[\s,，。.!！?？~～]", "", value)


def _skip(
    reason: MemoryGateReason,
    signal: str,
    *,
    matched_rule: str | None = None,
    matched_span: str | None = None,
) -> MemoryGateDecision:
    return MemoryGateDecision(
        should_extract=False,
        reason=reason,
        signals=[signal],
        matched_rule=matched_rule,
        matched_span=matched_span,
    )


def _first_match(
    text: str,
    patterns: tuple[re.Pattern[str], ...],
    rule_prefix: str,
) -> _GateMatch | None:
    for index, pattern in enumerate(patterns, start=1):
        match = pattern.search(text)
        if match is not None:
            return _GateMatch(f"{rule_prefix}_{index}", match.group(0))
    return None


def _first_signal_match(text: str, signals: list[str]) -> _GateMatch | None:
    for signal in signals:
        match = _first_match(text, _DURABLE_SIGNAL_PATTERNS.get(signal, ()), signal)
        if match is not None:
            return match
    return None


def _find_interaction_decline(text: str) -> _GateMatch | None:
    for rule, pattern in _INTERACTION_DECLINE_RULES:
        match = pattern.search(text)
        if match is not None:
            return _GateMatch(rule, match.group(0))
    return None


def _find_interaction_qualifier(text: str) -> _GateMatch | None:
    for rule, pattern in _INTERACTION_QUALIFIER_RULES:
        match = pattern.search(text)
        if match is not None:
            return _GateMatch(rule, match.group(0))
    return None


def _has_consultation_question(text: str) -> bool:
    return any(pattern.search(text) for pattern in _CONSULTATION_PATTERNS)


def _is_habitual_not_future(text: str) -> bool:
    habitual = re.search(r"平时|通常|每周|每月|每逢|偶尔|有时", text) is not None
    explicit_future = (
        re.search(
            r"明天|后天|大后天|下周|下个月|本周末|这周末|过几天|几天后|月底|"
            r"准备|计划|打算|将要|约好|已经约",
            text,
        )
        is not None
    )
    return habitual and not explicit_future


def _task_local_date_constraint(
    text: str,
    active_task: TaskType | None,
) -> str | None:
    if (
        active_task != TaskType.DATE_PLANNING
        or _has_durable_preference_scope(text)
        or _durable_preference_clause(text) is not None
    ):
        return None
    match = _DATE_TASK_LOCAL_PATTERN.search(text)
    return match.group(0) if match is not None else None


def _has_durable_preference_scope(text: str) -> bool:
    return _DATE_DURABLE_SCOPE_PATTERN.search(text) is not None


def _durable_preference_clause(text: str) -> str | None:
    preference_patterns = _DURABLE_SIGNAL_PATTERNS["preference"]
    for clause in split_date_clauses(text):
        if any(pattern.search(clause.text) for pattern in preference_patterns):
            return clause.text
    return None


_QUESTION_MARK_PATTERN = re.compile(r"[?？]|(?:吗|呢|么|没有)[。.!！]?$")
_PENDING_CONFIRMATION_PATTERN = re.compile(
    r"(?:对|是这样|没错|正确)吗[?？。.!！]*$"
)
_PENDING_MEMORY_QUESTION_RULES: tuple[
    tuple[str, str, re.Pattern[str]],
    ...,
] = (
    (
        "duration",
        "pending_memory_duration_question",
        re.compile(r"(?:多久|多长时间|持续.{0,5}(?:多久|多长))"),
    ),
    (
        "cause_scope",
        "pending_memory_cause_question",
        re.compile(r"(?:为什么|因为什么|什么原因|主要.{0,5}(?:原因|为什么))"),
    ),
    (
        "actor",
        "pending_memory_actor_question",
        re.compile(r"(?:(?:是|由)?谁.{0,8}(?:先|提|发起)|哪一方.{0,8}(?:先|提|发起))"),
    ),
    (
        "relationship_interaction",
        "pending_memory_interaction_question",
        re.compile(
            r"(?:她|他|对方|你们|你俩|双方).{0,28}"
            r"(?:主动|道歉|联系|回复|聊天|聊|交流|沟通|见面|情绪|烦心事|"
            r"分手|冷战).{0,10}(?:吗|呢|没有|多久)"
        ),
    ),
)

_L0_ACKNOWLEDGEMENT_MESSAGES = {
    "好",
    "好的",
    "好吧",
    "行",
    "可以",
    "知道了",
    "我知道了",
    "好的我知道了",
    "明白",
    "明白了",
    "收到",
    "那就这样",
    "thanks",
    "thankyou",
    "thankyou.",
    "thankyou!",
    "thanks.",
    "thanks!",
}
_L0_SMALL_TALK_PATTERNS = (
    re.compile(
        r"^(?!.*(?:她|他|对象|伴侣|我们|我俩|生日|喜欢|不喜欢|分手|冷战|"
        r"联系|回复|见面))(?:哈){2,}.{0,16}[。.!！~～]*$"
    ),
)
_L0_PURE_CONSULTATION_PATTERNS = (
    re.compile(r"^(?:那)?我(?:现在)?(?:应该|该|要)?怎么(?:回|回复|说|做|处理).*[?？]?$"),
    re.compile(r"^你觉得.{0,24}(?:正常|合适|合理)吗[?？]?$"),
    re.compile(r"^(?:你能不能|能不能|请)?帮我分析.{0,30}(?:想什么|怎么想).*[?？]?$"),
)
_L0_UNRELATED_TRANSIENT_PATTERNS = (
    re.compile(
        r"(?:今天|昨天|刚才|刚刚).{0,20}"
        r"(?:堵车|路上堵|迟到|快递|天气|网络|心率|系统|开会)"
    ),
    re.compile(
        r"(?:我|自己).{0,12}(?:一天|每天|每周|每月).{0,12}"
        r"(?:吃药|喝水|喝咖啡|跑步|运动|睡觉|工作)"
    ),
    re.compile(
        r"(?:我|自己).{0,8}(?:一天|每天|每周|每月).{0,12}"
        r"(?:次|杯|小时).{0,8}(?:吃药|喝水|喝咖啡|跑步|运动|睡觉|工作)?"
    ),
    re.compile(r"(?:最近|近来|这段时间).{0,12}(?:工作|学习).{0,8}(?:越来越|变得)"),
)
_RELATIONSHIP_MENTION_PATTERN = re.compile(
    r"(?:我和她|我和他|我俩|我们|她|他|对方|对象|伴侣|女朋友|男朋友)"
)
_L0_SEMANTIC_REVIEW_CUE_PATTERN = re.compile(
    r"(?:我发现|我感觉|我觉得|我怀疑|我担心|听说|可能|也许|好像|"
    r"不确定|突然|最近|近来|这段时间|随口|没认真|还没|以前|之前)"
)
_L0_EXPLICIT_REMEMBER_PATTERN = re.compile(r"(?:请)?记住[：:]?|记一下[：:]?")
_L0_HARD_PASS_RULES: tuple[
    tuple[MemorySemanticGateReason, str, str, re.Pattern[str]],
    ...,
] = (
    (
        MemorySemanticGateReason.STABLE_FACT,
        "explicit_remember",
        "l0_explicit_remember",
        _L0_EXPLICIT_REMEMBER_PATTERN,
    ),
    (
        MemorySemanticGateReason.STABLE_FACT,
        "stable_birthday",
        "l0_stable_birthday",
        re.compile(
            r"(?:她|他|对象|伴侣).{0,8}(?:生日|出生日期)"
            r".{0,10}(?:\d{1,2}月\d{1,2}[日号]?|[一二三四五六七八九十]+月)"
        ),
    ),
    (
        MemorySemanticGateReason.STABLE_FACT,
        "stable_work_or_residence",
        "l0_stable_work_or_residence",
        re.compile(
            r"(?:她|他|对象|伴侣).{0,16}"
            r"(?:(?:在|于).{1,12}工作|工作在.{1,12}|住在.{1,12}|居住在.{1,12})"
        ),
    ),
    (
        MemorySemanticGateReason.RELATIONSHIP_STATE,
        "explicit_relationship_state",
        "l0_explicit_relationship_state",
        re.compile(
            r"(?:我和她|我和他|我俩|我们).{0,24}"
            r"(?:(?:确定|确认|建立|开始)(?:了)?关系|正式在一起|已经在一起|"
            r"现在还在冷战|目前还在冷战)"
        ),
    ),
    (
        MemorySemanticGateReason.PREFERENCE,
        "explicit_preference",
        "l0_explicit_preference",
        re.compile(
            r"(?:她|他|对象|伴侣).{0,36}"
            r"(?:特别能吃辣|很能吃辣|能吃辣|特别喜欢|很喜欢|更喜欢|不喜欢|"
            r"偏好|爱吃|不吃|不怎么喝|更愿意)"
        ),
    ),
    (
        MemorySemanticGateReason.PLANNED_EVENT,
        "confirmed_planned_event",
        "l0_confirmed_planned_event",
        re.compile(
            r"(?:(?:已经|都)?约好.{0,40}(?:订好|订了|买好|买了)|"
            r"(?:酒店|车票|机票|门票).{0,8}(?:已经|都)?(?:订好|订了|买好|买了))"
        ),
    ),
    (
        MemorySemanticGateReason.ACTION_INTENT,
        "high_impact_action_intent",
        "l0_high_impact_action_intent",
        re.compile(r"(?:准备|打算|决定).{0,12}(?:跟|和|向)?(?:她|他|对方)?.{0,4}(?:提)?分手"),
    ),
)


_CASUAL_MESSAGES = {
    "你好",
    "您好",
    "在吗",
    "你好在吗",
    "谢谢",
    "谢谢你",
    "先这样",
    "谢谢先这样",
    "好的",
    "明白了",
    "再见",
    "拜拜",
    "晚安",
}

_CASUAL_PATTERNS = (
    re.compile(r"^(?:早上好|上午好|中午好|下午好|晚上好|早安|午安)(?:啊|呀|呢)?[!！。]*$"),
)

_HYPOTHETICAL_PATTERNS = (
    re.compile(r"^(?:假设|假如|如果|举个例子|如果一个人|一般来说)"),
    re.compile(r"纯属假设|只是举例"),
)

_OPERATION_PATTERNS = (
    re.compile(r"^(?:请)?(?:把|将).{0,20}(?:压缩|改写|翻译|总结|列成|改成)"),
    re.compile(r"^(?:这次|本轮).{0,12}(?:不要|别).{0,8}(?:引用|读取|使用).{0,8}记忆"),
    re.compile(r"你刚才为什么.{0,20}(?:检索|路由|调用|输出)"),
    re.compile(r"^(?:重新|继续|停止|暂停)(?:回答|生成|输出)"),
)

_KNOWLEDGE_QUESTION_PATTERNS = (
    re.compile(r"^(?:什么叫|什么是|请解释|解释一下|介绍一下|如何定义)"),
    re.compile(r"(?:是什么概念|是什么意思)\??$"),
)

_PURE_PARTNER_HYPOTHESIS_PATTERNS = (
    re.compile(
        r"^(?:你觉得)?(?:她|他|对方).{0,12}(?:是不是|会不会).{0,16}(?:不喜欢|没兴趣|兴趣下降).*[?？]?$"
    ),
    re.compile(r"^你觉得.{0,20}(?:兴趣下降|不喜欢).*[?？]?$"),
)

_RELATIONSHIP_ACTION = (
    r"(?:道歉|表白|告白|解释|沟通|联系|回复|回应|追求|邀请|约会|挽回)"
)
_RELATIONSHIP_ACTION_CONSULTATION_PATTERNS = (
    re.compile(
        rf"^(?:我)?(?:现在)?(?:想知道)?(?:应该|应当|该|要)?(?:怎么|如何|怎样)"
        rf".{{0,16}}{_RELATIONSHIP_ACTION}(?:呢|啊)?[?？。！!]*$"
    ),
    re.compile(
        rf"^(?:我)?(?:现在)?(?:该不该|应不应该|要不要|能不能|可不可以)"
        rf".{{0,16}}{_RELATIONSHIP_ACTION}(?:呢|啊)?[?？。！!]*$"
    ),
)

_EXPLICIT_REMEMBER_PATTERNS = (
    re.compile(r"(?:请)?记住[：:]?"),
    re.compile(r"记一下[：:]?"),
)

_DATE_DURABLE_SCOPE_PATTERN = re.compile(r"以后|长期|一贯|平时|通常|一般|每次|每回|一直|总是|从来")
_DATE_TASK_LOCAL_PATTERN = re.compile(
    r"(?:这次|本次|这回|当前|今天|明天|后天|周末|下周)?.{0,10}"
    r"(?:总?预算|\d{2,6}\s*(?:元|块)|城市|区域|商圈|日期|时间|"
    r"交通方式|步行|地铁|公交|开车|自驾|骑行|餐厅|活动|景点|"
    r"火锅|烧烤|西餐|日料|电影|博物馆|美术馆)"
)

# Keep the interaction-decline vocabulary separate from the broader durable
# signal patterns below.  This makes it possible to expand colloquial Chinese
# without weakening unrelated Gate rules.
_INTERACTION_TIME_MARKERS = (
    "最近",
    "近来",
    "这段时间",
    "这几天",
    "这周",
    "上周",
    "过去",
    "前阵子",
    "一直",
)
_INTERACTION_SUBJECTS = ("我和她", "我和他", "我俩", "我们", "她", "他", "对方", "对象", "伴侣")
_INTERACTION_TARGET = r"(?:我|你|他|她|人|对方|消息|信息)?"
_INTERACTION_VERB = (
    r"(?:理(?:睬|我|你|他|她|人|对方)?|"
    rf"搭理{_INTERACTION_TARGET}|"
    rf"回复{_INTERACTION_TARGET}|回(?:我|消息|信息)|"
    rf"联系{_INTERACTION_TARGET}|聊天{_INTERACTION_TARGET}|"
    rf"交流{_INTERACTION_TARGET}|沟通{_INTERACTION_TARGET}|互动{_INTERACTION_TARGET})"
)
_INTERACTION_VERB_WITH_BOUNDARY = rf"{_INTERACTION_VERB}(?:了|着|过)?(?![\u4e00-\u9fff])"
_INTERACTION_DECLINE_PREFIX = r"(?:不怎么|不太|很少|几乎不)"
_INTERACTION_DECLINE_TREND = (
    r"(?:回复|联系|聊天|交流|沟通|互动)(?:明显)?"
    r"(?:变少(?:了)?|越来越少|减少(?:了)?|下降(?:了)?|降低(?:了)?|少了)"
    r"(?![\u4e00-\u9fff])"
)
_INTERACTION_DECLINE_PHRASE = (
    rf"(?:{_INTERACTION_DECLINE_PREFIX}{_INTERACTION_VERB_WITH_BOUNDARY}|"
    rf"爱答不理|{_INTERACTION_DECLINE_TREND})"
)
_INTERACTION_DECLINE_RULES = (
    (
        "temporal_interaction_decline",
        re.compile(
            rf"(?:{'|'.join(map(re.escape, _INTERACTION_TIME_MARKERS))})"
            rf".{{0,24}}{_INTERACTION_DECLINE_PHRASE}"
        ),
    ),
    (
        "subject_interaction_decline",
        re.compile(
            rf"(?:{'|'.join(map(re.escape, _INTERACTION_SUBJECTS))})"
            rf".{{0,20}}{_INTERACTION_DECLINE_PHRASE}"
        ),
    ),
    ("interaction_decline", re.compile(_INTERACTION_DECLINE_PHRASE)),
)

# Open-world durable reversal coverage.  The three dimensions are kept
# separate so a time marker or a negative word alone cannot admit an ordinary
# message into Memory.
_REVERSAL_TIME_CUE = (
    r"(?:最近|近来|这段时间|这几天|这周|这两周|这几个月|"
    r"过去一段时间|前阵子|长期以来|一直以来|一个月来)"
)
_REVERSAL_CHANGE_CUE = (
    r"(?:不再|不怎么|不太|很少|几乎不|不像以前|"
    r"明显(?:减少|变少|变慢|变冷淡|变差)|越来越少|越来越慢|"
    r"变少|变慢|下降|降低|恶化|开始回避|不愿意)"
)
_REVERSAL_BEHAVIOR_CUE = (
    r"(?:联系|聊天|交流|沟通|互动|回复|回应|回消息|见面|碰面|"
    r"邀请|约我|讨论|谈未来|未来计划|烦心事|情绪|矛盾|冲突|吵架|主动)"
)
_DURABLE_BEHAVIORAL_REVERSAL_PATTERNS = (
    re.compile(
        rf"{_REVERSAL_TIME_CUE}.{{0,24}}{_REVERSAL_CHANGE_CUE}.{{0,16}}{_REVERSAL_BEHAVIOR_CUE}"
    ),
    re.compile(
        rf"{_REVERSAL_TIME_CUE}.{{0,24}}{_REVERSAL_BEHAVIOR_CUE}.{{0,16}}{_REVERSAL_CHANGE_CUE}"
    ),
)
_FUTURE_BEHAVIORAL_REVERSAL_PATTERNS = (
    re.compile(
        r"(?:担心|害怕|怕|不知道|会不会).{0,20}"
        r"(?:以后|将来|未来).{0,20}"
        rf"{_REVERSAL_CHANGE_CUE}.{{0,12}}{_REVERSAL_BEHAVIOR_CUE}"
    ),
    re.compile(
        r"(?:以后|将来|未来).{0,12}"
        rf"{_REVERSAL_CHANGE_CUE}.{{0,12}}{_REVERSAL_BEHAVIOR_CUE}"
        r".{0,8}(?:怎么办|怎么做|该怎么办)"
    ),
)
_ONE_OFF_INTERACTION_EVENT_PATTERNS = (
    re.compile(
        r"(?:昨天|今天|刚刚|刚才).{0,18}"
        r"(?:没|没有|未).{0,8}(?:主动)?(?:联系|回复|聊天|回应|回消息)"
        r".{0,6}(?:一次|一回)[。.!！?？]*$"
    ),
)

# These are independent observable interaction dimensions.  They deliberately
# avoid inferring motivation or a global relationship state from one channel.
_INTERACTION_QUALIFIER_RULES = (
    (
        "quantified_contact_frequency",
        re.compile(
            r"(?:一天|每天|一日).{0,5}(?:只|就|才).{0,5}"
            r"(?:回(?:复)?|联系|聊天).{0,8}"
            r"(?:\d+|[一二三四五六七八九十两]+).{0,4}(?:条|次)"
        ),
    ),
    (
        "response_engagement_qualifier",
        re.compile(
            r"(?:内容|回复|回(?:我|消息)?).{0,10}"
            r"(?:不(?:是|算)?(?:特别)?敷衍|不敷衍|还算认真|挺认真|很认真)"
        ),
    ),
    (
        "offline_interaction_qualifier",
        re.compile(r"(?:线下|见面).{0,14}(?:挺正常|很正常|没什么问题|还不错|挺好)"),
    ),
    (
        "initiation_balance_qualifier",
        re.compile(r"(?:基本|大多|通常).{0,8}(?:都是|由).{0,6}我主动(?:联系|聊天|找她)?"),
    ),
)

_RELATIONSHIP_PARTNER = r"(?:她|他|对方|对象|伴侣)"
_SOCIAL_RELATION_TARGET = r"(?:朋友|朋友圈|社交圈|聚会|活动|家人|父母|亲友)"
_SOCIAL_INTEGRATION_PATTERNS = (
    re.compile(
        rf"{_RELATIONSHIP_PARTNER}.{{0,20}}(?:带|邀请|叫|让).{{0,4}}我"
        rf".{{0,12}}(?:参加|加入|认识|见|融入).{{0,12}}{_SOCIAL_RELATION_TARGET}"
    ),
    re.compile(
        rf"{_RELATIONSHIP_PARTNER}.{{0,24}}(?:把)?我.{{0,8}}介绍给"
        rf".{{0,8}}(?:朋友|家人|父母|亲友|别人)"
    ),
    re.compile(
        rf"(?:聚会|活动).{{0,12}}{_RELATIONSHIP_PARTNER}.{{0,10}}"
        r"(?:没(?:有)?|不再|很少)?(?:叫|邀请|带).{0,4}我"
    ),
)

_DURABLE_SIGNAL_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "preference": (
        re.compile(
            r"(?:我|她|他|对象|伴侣|我们).{0,16}"
            r"(?:喜欢(?!的)|不喜欢|爱吃|不吃|能接受|不能接受|讨厌|偏好|过敏|不能吃|想去|"
            r"勤俭节约|节俭|经济实惠|实惠|精打细算|省钱|消费观(?:念)?|消费习惯)"
        ),
        re.compile(
            r"(?:以后|平时|通常|一般|每次|一直).{0,20}(?:约会|见面)"
            r".{0,20}(?:预算|消费|花费|喜欢|偏好|不喜欢)"
        ),
    ),
    "temporal_interaction": (
        re.compile(
            r"(?:最近|过去|这几天|这周|上周|昨晚|昨天|今天|刚刚|刚|一直|通常|每周|每月)"
            r".{0,24}(?:联系|聊天|交流|见面|通话|吵|争执|约会|回复|回我|道歉|主动|拒绝|"
            r"冷淡|和好|复盘|约了|改到|推荐|分享)"
        ),
    ),
    "relationship_event": (
        re.compile(
            r"(?:我和|我俩|我们|双方|她|他|对象|伴侣).{0,30}"
            r"(?:联系|聊天|交流|见面|通话|吵架|争执|分手|拒绝|约定|主动|表白|在一起|复盘|约了|改到|"
            r"回复|回我|道歉|不理|打不通|和好|和解|达成一致|推荐|分享)"
        ),
    ),
    "planned_relationship_action": (
        re.compile(r"(?:准备|打算|计划|要去|想要).{0,20}(?:表白|告白)"),
        re.compile(
            r"(?:决定|准备|打算|计划|安排).{0,36}"
            r"(?:邀请|约|见面|联系|道歉|吃饭|看电影|聊|谈|沟通|解释|"
            r"请.{0,8}(?:吃.{0,2}饭|喝咖啡|看电影|逛))"
        ),
    ),
    "stable_fact": (
        re.compile(
            r"(?:我|她|他|对象|伴侣|我们).{0,12}"
            r"(?:是同学|是同事|住在|工作在|确认关系|认识了|有空|没空)"
        ),
    ),
    "profile_fact": (
        re.compile(
            r"(?:我|她|他|对象|伴侣).{0,10}"
            r"(?:来自|老家(?:在|是)?|家乡(?:在|是)?|是(?!一个|个).{1,8}(?:人|的))"
        ),
        re.compile(
            r"(?:我|她|他|对象|伴侣).{0,12}"
            r"(?:有(?:一个|个)?(?:哥哥|姐姐|弟弟|妹妹)|是独生子女)"
        ),
        re.compile(
            r"(?:我|她|他|对象|伴侣).{0,16}"
            r"(?:内向|外向|慢热|不善言辞|不太会聊天|不.{0,4}外向|社恐)"
        ),
    ),
    "shared_context": (
        re.compile(
            r"(?:我和她|我和他|我俩|我们).{0,40}"
            r"(?:同组|小组|同学|同事|同班|一起上课|共同课程|课程作业|"
            r"分到.{0,12}(?:同一个|同一|一起)?(?:组|小组))"
        ),
        re.compile(
            r"(?:组里|课上|课堂|讨论时|做作业时).{0,30}"
            r"(?:聊|交流|互动|讨论|说上几句|说几句话)"
        ),
    ),
    "relationship_state": (
        re.compile(
            r"(?:我和她|我和他|我俩|我们).{0,12}"
            r"(?:现在|目前|还在|还是|处于)?"
            r"(?:冷战|暧昧(?:关系)?|闹矛盾|有冲突|冲突状态)"
        ),
        re.compile(
            r"(?:我和她|我和他|我俩|我们).{0,16}"
            r"(?:已经|正式|重新)?(?:分手|在一起|确认关系|复合)"
        ),
        re.compile(
            r"(?:刚认识|认识不久|不.{0,2}熟|不熟悉|越来越熟|已经很熟|彼此熟悉|"
            r"比较熟|熟了(?:一|些|一点|一些)?|更熟|逐渐熟|慢慢熟|熟络|"
            r"比较陌生|了解不多|关系生疏)"
        ),
        re.compile(
            r"(?:(?:接触|见面|碰面|独处|聊天).{0,5}机会.{0,5}"
            r"(?:很少|不多|有限|很多|较多|充足)|"
            r"(?:很少|没有|几乎没有|很多|经常有|缺少).{0,6}机会.{0,10}"
            r"(?:接触|见面|碰面|独处|聊))"
        ),
        re.compile(
            r"(?:是否|是不是|不确定|不知道|没确认|没有确认).{0,10}"
            r"(?:单身|有对象|有伴侣|感情状态)"
        ),
        re.compile(
            r"(?:她|他|对方).{0,12}(?:单身|有对象|有男朋友|有女朋友|"
            r"有伴侣|已婚|结婚了)"
        ),
    ),
    "interaction_trend": (
        re.compile(
            r"(?:最近|近来|这段时间|这几天|过去).{0,16}"
            r"(?:(?:我们(?:的)?)(?:矛盾|冲突|争执)|"
            r"(?:我和她|我和他|我俩).{0,6}(?:矛盾|冲突|争执)).{0,8}"
            r"(?:越来越多|变多|加剧|更明显|变严重|升级)"
        ),
        re.compile(
            r"(?:最近|近来|这段时间|这几天|过去).{0,16}"
            r"(?:回复|联系|聊天|交流|见面|互动).{0,8}"
            r"(?:越来越慢|越来越少|明显变差|明显变少|明显变慢|变冷淡)"
        ),
    ),
    "relationship_transition": (
        re.compile(
            r"(?:我和她|我和他|我俩|我们)(?:的关系)?"
            r"(?:(?:现在|目前|最近|已经|终于|也|都|又|重新)|[\s，,]){0,4}"
            r"(?:恢复正常|说开了?|和好|冷战结束|重新在一起|复合)"
        ),
        re.compile(
            r"(?:我和她|我和他|我俩|我们).{0,12}"
            r"(?:矛盾|冲突|争执|冷战).{0,10}"
            r"(?:说开|解决|缓和|恢复正常)"
        ),
        re.compile(
            r"(?:我和她|我和他|我俩|我们)(?:的关系)?"
            r"(?:(?:现在|目前|最近|这几天|这周|又|再次|重新)|[\s，,]){0,4}"
            r"(?:又出现|再次出现|重新出现)(?:矛盾|冲突|问题)"
        ),
    ),
    "social_integration": _SOCIAL_INTEGRATION_PATTERNS,
    "planned_event": (
        re.compile(
            r"(?:明天|后天|大后天|下周|下个月|本周末|这周末|周末|过几天|几天后|月底|"
            r"周[一二三四五六日天]).{0,50}"
            r"(?:有|要|将|准备|计划|安排|打算|参加|去|见|开|做|进行|约)"
        ),
        re.compile(
            r"(?:有|要|将|准备|计划|安排|打算|参加|去|见|开|做|进行|约).{0,50}"
            r"(?:明天|后天|大后天|下周|下个月|本周末|这周末|周末|过几天|几天后|月底|"
            r"周[一二三四五六日天])"
        ),
    ),
    "self_belief": (
        re.compile(
            r"(?:我觉得|我认为|我就是|可是我|但我|我就).{0,24}"
            r"(?:普通|没.{0,4}长处|配不上|不自信|自卑|不够好|没优势)"
        ),
    ),
    "partner_attribute": (
        re.compile(
            r"(?:对方|她|他).{0,16}"
            r"(?:优秀|成绩好|漂亮|帅|能力强|很有才华|条件好)"
        ),
    ),
    "belief_or_competition": (
        re.compile(
            r"(?:听说|感觉|觉得|认为).{0,36}"
            r"(?:聊天|联系|追求|喜欢|竞争|比我|优秀|希望大)"
        ),
    ),
    "advice_outcome": (
        re.compile(
            r"(?:我照你说的|按你说的|按照建议|采纳建议|听了建议).{0,80}"
            r"(?:有效|有用|成功|改善|缓和|和好)"
        ),
        re.compile(
            r"(?:选择|调整|改成|主动).{0,50}(?:之后|后).{0,20}"
            r"(?:开心|满意|和好|改善|缓和|有效)"
        ),
        re.compile(
            r"(?:考虑到|顾及|尊重).{0,50}(?:选择|调整|改成).{0,50}"
            r"(?:开心|满意|和好|改善|缓和)"
        ),
    ),
}

_CONSULTATION_PATTERNS = (
    re.compile(r"怎么办|如何|怎样|为什么|是不是|该不该|要不要|能不能|可以吗"),
    re.compile(r"(?:^|[，,。；;])\s*怎么"),
    re.compile(r"(?:我|那|这|该|应该).{0,3}怎么(?:办|做|说|追|聊|回复|处理|解决|开始)"),
    re.compile(r"(?:有啥|有什么|给我|能给).{0,8}建议"),
)

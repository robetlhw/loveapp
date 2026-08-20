from datetime import UTC, datetime
from pathlib import Path

from loveapp.adapters.advice import TemplateAdviceComposer
from loveapp.adapters.knowledge import InMemoryKnowledgeRetriever
from loveapp.adapters.maps import DemoMapProvider
from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.agents import AdviceAgent, DatePlanningAgent
from loveapp.application import MemoryService
from loveapp.application.memory import NoOpMemoryExtractor
from loveapp.bootstrap import build_memory_container, load_seed_documents
from loveapp.core.config import Settings
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.advice import AdviceRequest, AdviceResponse, RelationshipContext
from loveapp.domain.date_plan import DatePlanRequest
from loveapp.domain.enums import AdviceScenario
from loveapp.domain.knowledge import RetrievedDocument
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    DiscardedSpan,
    DiscardReason,
    MemoryAttemptStatus,
    MemoryCandidate,
    MemoryExtractionAttempt,
    MemoryExtractionStatus,
    MemoryItem,
    MemoryKind,
    MemoryPerspective,
    MemoryStatus,
    MemoryValence,
    MessageRole,
    RelationshipImpact,
    StoredMessage,
    TimeKind,
)
from loveapp.domain.policy import ResolvedScenarioPolicy
from loveapp.safety import SafetyPolicy


class StubExtractor:
    def __init__(self, candidates: list[MemoryCandidate]) -> None:
        self.extraction = AtomicExtraction(
            claims=[
                _claim_from_candidate(candidate, index)
                for index, candidate in enumerate(candidates, start=1)
            ]
        )
        self.existing_memories: list[MemoryItem] = []
        self.conversation_history: list[StoredMessage] = []

    async def extract(
        self,
        text: str,
        *,
        reference_time: datetime,
        existing_memories: list[MemoryItem],
        conversation_history: list[StoredMessage],
        trace=None,
    ) -> AtomicExtraction:
        del text, reference_time, trace
        self.existing_memories = existing_memories
        self.conversation_history = conversation_history
        return self.extraction


class TelemetryExtractor:
    def __init__(self, extraction: AtomicExtraction) -> None:
        self.extraction = extraction

    async def extract(
        self,
        text: str,
        *,
        reference_time: datetime,
        existing_memories: list[MemoryItem],
        conversation_history: list[StoredMessage],
        trace=None,
        attempt_callback=None,
    ) -> AtomicExtraction:
        del text, reference_time, existing_memories, conversation_history, trace
        if attempt_callback is not None:
            attempt_callback(
                MemoryExtractionAttempt(
                    attempt=1,
                    status=MemoryAttemptStatus.COMPLETED,
                    duration_ms=12.5,
                    model="test-memory-model",
                    prompt_tokens=10,
                    completion_tokens=8,
                    total_tokens=18,
                )
            )
        return self.extraction


class FailingExtractor:
    async def extract(
        self,
        text: str,
        *,
        reference_time: datetime,
        existing_memories: list[MemoryItem],
        conversation_history: list[StoredMessage],
        trace=None,
        attempt_callback=None,
    ) -> AtomicExtraction:
        del text, reference_time, existing_memories, conversation_history, trace
        del attempt_callback
        raise ValueError("invalid extraction response")


class CapturingComposer:
    def __init__(self) -> None:
        self.context: RelationshipContext | None = None
        self.histories: list[list[StoredMessage]] = []
        self._delegate = TemplateAdviceComposer()

    async def compose(
        self,
        request: AdviceRequest,
        scenario: AdviceScenario,
        context: RelationshipContext,
        documents: list[RetrievedDocument],
        conversation_history: list[StoredMessage],
        policy: ResolvedScenarioPolicy,
        stream_callback=None,
    ) -> AdviceResponse:
        del stream_callback
        self.context = context
        self.histories.append(conversation_history)
        return await self._delegate.compose(
            request,
            scenario,
            context,
            documents,
            conversation_history,
            policy,
        )


async def test_memory_service_filters_low_confidence_and_preserves_source_text() -> None:
    statement = "每个月最后一个周日，我们会一起复盘本月的相处。"
    high_confidence = _candidate(
        summary="双方每月最后一个周日复盘关系",
        original_text="模型改写的句子",
        confidence=0.92,
        supersedes_id="hallucinated-id",
    ).model_copy(update={"evidence_spans": [statement]})
    low_confidence = _candidate(
        summary="双方可能会写复盘笔记",
        original_text=statement,
        confidence=0.4,
    )
    store = InMemoryMemoryStore()
    service = MemoryService(
        store,
        StubExtractor([high_confidence, low_confidence]),
        min_confidence=0.65,
        clock=lambda: datetime(2026, 7, 17, 12, tzinfo=UTC),
    )

    result = await service.remember_text(
        user_id="service-user",
        relationship_id="primary",
        text=statement,
    )

    assert len(result.saved) == 1
    assert result.skipped_low_confidence == 1
    assert result.saved[0].item.original_text == statement
    assert result.saved[0].item.supersedes_id is None
    assert result.saved[0].item.status == MemoryStatus.PROPOSED


async def test_memory_service_adds_default_expiration_to_planned_events() -> None:
    text = "我俩最近被分到了同一个课程作业小组，下周有机会一起小组讨论。"
    planned = MemoryCandidate(
        kind=MemoryKind.PLANNED_EVENT,
        subject="relationship",
        summary="下周双方有机会参加课程小组讨论",
        original_text=text,
        evidence_spans=["下周有机会一起小组讨论"],
        time_kind=TimeKind.POINT,
        period_start=datetime(2099, 7, 27, 9, tzinfo=UTC),
        payload={
            "predicate": "attend_course_discussion",
            "object": "课程小组讨论",
            "event_status": "tentative",
        },
        confidence=0.9,
    )
    store = InMemoryMemoryStore()
    service = MemoryService(
        store,
        StubExtractor([planned]),
        clock=lambda: datetime(2099, 7, 18, 12, tzinfo=UTC),
    )

    result = await service.remember_text(
        user_id="planned-user",
        relationship_id="classmate",
        text=text,
    )

    assert len(result.saved) == 1
    saved = result.saved[0].item
    assert saved.kind == MemoryKind.PLANNED_EVENT
    assert saved.expires_at == datetime(2099, 7, 28, 9, tzinfo=UTC)
    context = await service.get_context("planned-user", "classmate")
    assert [item.id for item in context.planned_events] == [saved.id]


async def test_memory_service_keeps_tentative_belief_and_hearsay_candidates() -> None:
    text = "听说她最近经常和一个男生聊天，我觉得他可能在追求她。"
    belief = MemoryCandidate(
        kind=MemoryKind.STABLE_FACT,
        subject="user",
        summary="用户觉得另一名男生可能在追求对方",
        original_text=text,
        evidence_spans=["我觉得他可能在追求她"],
        perspective=MemoryPerspective.USER_BELIEF,
        confidence=0.4,
        payload={"predicate": "believes_other_boy_pursues_partner"},
    )
    hearsay = MemoryCandidate(
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="relationship",
        summary="用户听说对方最近经常和一名男生聊天",
        original_text=text,
        evidence_spans=["听说她最近经常和一个男生聊天"],
        time_kind=TimeKind.INTERVAL,
        perspective=MemoryPerspective.USER_REPORTED,
        confidence=0.5,
        payload={
            "predicate": "partner_chat_frequency",
            "metric": "partner_chat_frequency",
            "source_type": "hearsay",
        },
    )
    service = MemoryService(
        InMemoryMemoryStore(),
        StubExtractor([belief, hearsay]),
        min_confidence=0.65,
        tentative_min_confidence=0.5,
        belief_min_confidence=0.4,
    )

    result = await service.remember_text(
        user_id="tentative-user",
        relationship_id="primary",
        text=text,
    )

    assert len(result.saved) == 2
    assert result.skipped_low_confidence == 0
    assert all(item.item.status == MemoryStatus.PROPOSED for item in result.saved)


async def test_memory_service_persists_completed_extraction_run_and_attempts() -> None:
    text = "我喜欢看展览。"
    candidate = MemoryCandidate(
        kind=MemoryKind.PREFERENCE,
        subject="user",
        summary="用户喜欢看展览",
        original_text=text,
        time_kind=TimeKind.TIMELESS,
        valence=MemoryValence.POSITIVE,
        relationship_impact=RelationshipImpact.UNCLEAR,
        perspective=MemoryPerspective.USER_REPORTED,
        confidence=0.95,
        payload={"preference": "展览", "preference_type": "like"},
    )
    store = InMemoryMemoryStore()
    service = MemoryService(store, TelemetryExtractor(AtomicExtraction(
        claims=[_claim_from_candidate(candidate, 1)],
    )))

    result = await service.remember_text(
        user_id="run-user",
        relationship_id="primary",
        conversation_id="run-conversation",
        text=text,
    )

    runs = await store.list_extraction_runs(
        user_id="run-user",
        relationship_id="primary",
        conversation_id="run-conversation",
    )
    assert result.extraction_run_id == runs[0].id
    assert runs[0].status == MemoryExtractionStatus.COMPLETED
    assert runs[0].saved_memory_ids == [result.saved[0].item.id]
    assert runs[0].attempts[0].model == "test-memory-model"
    assert runs[0].attempts[0].total_tokens == 18


async def test_memory_service_persists_gate_skip_run() -> None:
    store = InMemoryMemoryStore()
    service = MemoryService(store, FailingExtractor())

    result = await service.remember_text(
        user_id="skip-run-user",
        relationship_id="primary",
        conversation_id="skip-conversation",
        text="谢谢",
    )

    runs = await store.list_extraction_runs(
        user_id="skip-run-user",
        relationship_id="primary",
        conversation_id="skip-conversation",
    )
    assert result.saved == []
    assert runs[0].status == MemoryExtractionStatus.SKIPPED
    assert runs[0].gate_decision.should_extract is False
    assert runs[0].attempts == []


async def test_memory_service_persists_failed_extraction_run() -> None:
    store = InMemoryMemoryStore()
    service = MemoryService(store, FailingExtractor())

    result = await service.remember_text(
        user_id="failed-run-user",
        relationship_id="primary",
        conversation_id="failed-conversation",
        text="我最近连续三天都主动联系她。",
    )

    runs = await store.list_extraction_runs(
        user_id="failed-run-user",
        relationship_id="primary",
        conversation_id="failed-conversation",
    )
    assert result.extraction_error == "invalid extraction response"
    assert runs[0].status == MemoryExtractionStatus.FAILED
    assert runs[0].error == "invalid extraction response"


async def test_advice_agent_receives_remembered_context() -> None:
    store = InMemoryMemoryStore()
    service = MemoryService(store, NoOpMemoryExtractor())
    await service.remember_date_preferences(
        user_id="advice-memory-user",
        relationship_id="primary",
        preferences=["陶艺"],
    )
    composer = CapturingComposer()
    agent = AdviceAgent(
        InMemoryKnowledgeRetriever(load_seed_documents()),
        service,
        SafetyPolicy(),
        composer,
    )

    first = await agent.advise_turn(
        AdviceRequest(
            user_id="advice-memory-user",
            relationship_id="primary",
            conversation_id="multi-turn-conversation",
            query="我们周末做什么更合适？",
        )
    )
    second = await agent.advise_turn(
        AdviceRequest(
            user_id="advice-memory-user",
            relationship_id="primary",
            conversation_id="multi-turn-conversation",
            query="那如果下雨呢？",
        )
    )

    assert first.response.recommended_actions
    assert second.response.recommended_actions
    assert first.conversation_id == second.conversation_id == "multi-turn-conversation"
    assert composer.context is not None
    assert composer.context.user_preferences == ["陶艺"]
    assert composer.context.remembered_items[0].status == MemoryStatus.CONFIRMED
    assert composer.histories[0] == []
    assert [message.role.value for message in composer.histories[1]] == ["user", "assistant"]
    assert composer.histories[1][0].content == "我们周末做什么更合适？"
    persisted = await store.list_messages(
        user_id="advice-memory-user",
        relationship_id="primary",
        conversation_id="multi-turn-conversation",
    )
    assert [message.role.value for message in persisted] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]


async def test_memory_service_atomizes_preference_arrays() -> None:
    text = "我不喜欢卡丁车，但很喜欢脱口秀。"
    combined = MemoryCandidate(
        kind=MemoryKind.PREFERENCE,
        subject="user",
        summary="用户不喜欢卡丁车但喜欢脱口秀",
        original_text=text,
        time_kind=TimeKind.TIMELESS,
        valence=MemoryValence.MIXED,
        relationship_impact=RelationshipImpact.UNCLEAR,
        perspective=MemoryPerspective.USER_REPORTED,
        confidence=0.96,
        payload={
            "preference": ["卡丁车", "脱口秀"],
            "preference_type": ["dislike", "like"],
        },
    )
    service = MemoryService(InMemoryMemoryStore(), StubExtractor([combined]))

    result = await service.remember_text(
        user_id="atomic-user",
        relationship_id="primary",
        text=text,
    )

    assert len(result.saved) == 2
    assert [saved.item.payload["preference"] for saved in result.saved] == [
        "卡丁车",
        "脱口秀",
    ]
    assert [saved.item.summary for saved in result.saved] == [
        "用户不喜欢卡丁车",
        "用户喜欢脱口秀",
    ]
    assert result.saved[0].item.id != result.saved[1].item.id


async def test_memory_extractor_receives_prior_conversation_messages() -> None:
    extractor = StubExtractor([])
    service = MemoryService(InMemoryMemoryStore(), extractor)
    scope = {
        "user_id": "history-extraction-user",
        "relationship_id": "primary",
        "conversation_id": "history-extraction-conversation",
    }
    await service.remember_text(text="我们原本约了周日下午去听讲座。", **scope)
    await service.record_message(
        role=MessageRole.ASSISTANT,
        content="可以先确认讲座时间和交通安排。",
        **scope,
    )

    await service.remember_text(text="她刚说还是改到上午吧。", **scope)

    assert [message.role for message in extractor.conversation_history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert extractor.conversation_history[0].content == "我们原本约了周日下午去听讲座。"


async def test_memory_service_exposes_discarded_consultation_question() -> None:
    text = "她最近主动找我聊天，这是不是说明她喜欢我？"
    extractor = StubExtractor([])
    extractor.extraction = AtomicExtraction(
        discarded_spans=[
            DiscardedSpan(
                text="这是不是说明她喜欢我？",
                reason=DiscardReason.CONSULTATION_QUESTION,
            )
        ]
    )
    service = MemoryService(InMemoryMemoryStore(), extractor)

    result = await service.remember_text(
        user_id="discard-user",
        relationship_id="primary",
        text=text,
    )

    assert result.saved == []
    assert result.discarded_spans[0].reason == DiscardReason.CONSULTATION_QUESTION


async def test_sidecar_duration_qualifier_updates_only_compatible_contact_pattern() -> None:
    now = datetime(2026, 8, 8, 12, tzinfo=UTC)
    store = InMemoryMemoryStore(clock=lambda: now)
    service = MemoryService(store, NoOpMemoryExtractor(), clock=lambda: now)
    scope = {
        "user_id": "contextual-update-user",
        "relationship_id": "primary",
        "conversation_id": "contextual-update-conversation",
    }
    first = await service.record_message(
        role=MessageRole.USER,
        content="她最近回复越来越慢。",
        **scope,
    )
    contact = await store.save_memory(
        user_id=scope["user_id"],
        relationship_id=scope["relationship_id"],
        source_message_id=first.id,
        status=MemoryStatus.CONFIRMED,
        candidate=MemoryCandidate(
            kind=MemoryKind.INTERACTION_PATTERN,
            subject="relationship",
            summary="用户报告最近线上联系频率降低",
            original_text=first.content,
            evidence_spans=["最近回复越来越慢"],
            time_kind=TimeKind.INTERVAL,
            confidence=0.98,
            canonical_predicate="interaction.contact_frequency",
            raw_predicate="reply_frequency_declined",
            payload={
                "predicate": "reply_frequency_declined",
                "metric": "contact_frequency",
                "direction": "decreasing",
                "channel": "messaging",
            },
        ),
    )
    preference = await store.save_memory(
        user_id=scope["user_id"],
        relationship_id=scope["relationship_id"],
        status=MemoryStatus.CONFIRMED,
        candidate=MemoryCandidate(
            kind=MemoryKind.PREFERENCE,
            subject="partner",
            summary="对方喜欢日料",
            original_text="她喜欢日料。",
            evidence_spans=["喜欢日料"],
            confidence=0.98,
            payload={"predicate": "likes_cuisine", "preference": "日料"},
        ),
    )
    current = await service.record_message(
        role=MessageRole.USER,
        content="持续了一个月了，你觉得这种情况是兴趣下降了吗？",
        **scope,
    )
    trace = ExecutionTrace()

    result = await service.remember_recorded_message(
        message=current,
        text=current.content,
        trace=trace,
    )

    updated = await store.get_memory(contact.item.id, scope["user_id"])
    unchanged_preference = await store.get_memory(preference.item.id, scope["user_id"])
    audits = await store.list_transition_audits(
        user_id=scope["user_id"],
        relationship_id=scope["relationship_id"],
        source_message_id=current.id,
    )
    gate = result.gate_decision
    assert gate is not None and gate.reason.value == "contextual_update"
    assert gate.history_loaded_for_gate is True
    assert gate.selected_target_memory_id == contact.item.id
    assert result.saved == []
    assert result.contextual_updated_memory_ids == [contact.item.id]
    assert updated is not None
    assert updated.payload["duration_value"] == 1
    assert updated.payload["duration_unit"] == "month"
    assert updated.payload["temporal_expression"] == "持续了一个月了"
    assert "持续了一个月了" in updated.evidence_spans
    assert unchanged_preference is not None
    assert unchanged_preference.payload.get("duration_value") is None
    assert any(audit.rule_name == "contextual_duration_update" for audit in audits)
    assert all(audit.canonical_predicate != "relationship.romantic_interest" for audit in audits)
    gate_trace = next(record for record in trace.snapshot() if record.name == "memory_gate")
    assert gate_trace.details["contextual_probe"] is True
    assert gate_trace.details["history_loaded_for_gate"] is True


async def test_contextual_duration_does_not_update_ambiguous_or_incompatible_targets() -> None:
    now = datetime(2026, 8, 8, 12, tzinfo=UTC)
    store = InMemoryMemoryStore(clock=lambda: now)
    service = MemoryService(store, NoOpMemoryExtractor(), clock=lambda: now)
    scope = {
        "user_id": "ambiguous-contextual-update-user",
        "relationship_id": "primary",
        "conversation_id": "ambiguous-contextual-update-conversation",
    }
    prior = await service.record_message(
        role=MessageRole.USER,
        content="她最近回复越来越慢，而且现在也很少回。",
        **scope,
    )
    for index, evidence in enumerate(("最近回复越来越慢", "现在也很少回")):
        await store.save_memory(
            user_id=scope["user_id"],
            relationship_id=scope["relationship_id"],
            source_message_id=prior.id,
            status=MemoryStatus.CONFIRMED,
            candidate=MemoryCandidate(
                kind=MemoryKind.INTERACTION_PATTERN,
                subject="relationship",
                summary=f"用户报告最近联系频率降低 {index}",
                original_text=prior.content,
                evidence_spans=[evidence],
                confidence=0.98,
                canonical_predicate="interaction.contact_frequency",
                raw_predicate="reply_frequency_declined",
                payload={
                    "predicate": "reply_frequency_declined",
                    "metric": "contact_frequency",
                    "direction": "decreasing" if index == 0 else "low",
                    "channel": "messaging",
                },
            ),
        )
    current = await service.record_message(
        role=MessageRole.USER,
        content="持续了一个月了。",
        **scope,
    )

    result = await service.remember_recorded_message(message=current, text=current.content)

    assert result.gate_decision is not None
    assert result.gate_decision.should_extract is False
    assert result.gate_decision.reason.value == "no_durable_signal"
    assert result.contextual_updated_memory_ids == []


async def test_date_planner_uses_preference_from_memory_when_request_has_none() -> None:
    store = InMemoryMemoryStore()
    service = MemoryService(store, NoOpMemoryExtractor())
    await service.remember_date_preferences(
        user_id="date-memory-user",
        relationship_id="primary",
        preferences=["展览"],
    )
    planner = DatePlanningAgent(DemoMapProvider(), service)

    plan = await planner.plan(
        DatePlanRequest(
            user_id="date-memory-user",
            relationship_id="primary",
            city="苏州",
            budget=500,
        )
    )

    assert plan.items[0].place.name.startswith("城市美术馆")
    assert "展览" in plan.items[0].reason


async def test_memory_admin_container_does_not_require_llm_credentials(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key=None,
        memory_backend="sqlite",
        memory_database_path=tmp_path / "offline-admin.db",
    )

    container = build_memory_container(settings, enable_extraction=False)
    try:
        context = await container.memory_service.get_context("offline-user", "primary")
    finally:
        await container.aclose()

    assert context.relationship_id == "primary"
    assert context.remembered_items == []


async def test_memory_compaction_marks_legacy_semantic_duplicates_superseded() -> None:
    store = InMemoryMemoryStore()
    service = MemoryService(store, NoOpMemoryExtractor())
    variants = [
        MemoryCandidate(
            kind=MemoryKind.STABLE_FACT,
            subject="user",
            summary="用户喜欢一个女孩子",
            original_text="我喜欢了一个女孩子",
            payload={"predicate": "likes", "object": "a_girl"},
        ),
        MemoryCandidate(
            kind=MemoryKind.STABLE_FACT,
            subject="user",
            summary="用户喜欢班上的一个女孩",
            original_text="我喜欢班上的一个女孩",
            payload={"predicate": "likes", "object": "classmate_girl"},
        ),
    ]
    for index, candidate in enumerate(variants):
        item = MemoryItem(
            id=f"legacy-{index}",
            user_id="compact-user",
            relationship_id="primary",
            status=MemoryStatus.PROPOSED,
            dedupe_key=f"legacy-key-{index}",
            **candidate.model_dump(),
        )
        store._memories[item.id] = item

    preview = await service.compact_memories(
        user_id="compact-user",
        relationship_id="primary",
    )
    applied = await service.compact_memories(
        user_id="compact-user",
        relationship_id="primary",
        apply=True,
    )

    assert len(preview.groups) == 1
    assert preview.applied_count == 0
    assert applied.applied_count == 1
    items = await store.list_memories(user_id="compact-user", relationship_id="primary")
    assert sum(item.status == MemoryStatus.SUPERSEDED for item in items) == 1


def _claim_from_candidate(candidate: MemoryCandidate, index: int) -> AtomicClaim:
    data = candidate.model_dump(exclude={"original_text"})
    data.update(
        {
            "claim_id": f"test-claim-{index}",
            "predicate": str(candidate.payload.get("predicate") or "test_predicate"),
            "object": None,
        }
    )
    return AtomicClaim.model_validate(data)


def _candidate(
    *,
    summary: str,
    original_text: str,
    confidence: float,
    supersedes_id: str | None = None,
) -> MemoryCandidate:
    return MemoryCandidate(
        kind=MemoryKind.INTERACTION_PATTERN,
        subject="relationship",
        summary=summary,
        original_text=original_text,
        time_kind=TimeKind.INTERVAL,
        valence=MemoryValence.NEUTRAL,
        relationship_impact=RelationshipImpact.UNCHANGED,
        perspective=MemoryPerspective.USER_REPORTED,
        confidence=confidence,
        supersedes_id=supersedes_id,
    )

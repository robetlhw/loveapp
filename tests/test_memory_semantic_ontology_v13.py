"""V1.3 contracts: orthogonal routing, provenance, and conservative governance."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import SecretStr, ValidationError

from loveapp.adapters.memory import InMemoryMemoryStore, SQLiteMemoryStore
from loveapp.adapters.memory.stage2_routing import SemanticRoleRouter, Stage2ExtractorRoute
from loveapp.adapters.memory.two_stage import TwoStageMemoryExtractor, _build_coarse_prompt
from loveapp.application.event_enrichment import resolve_event_enrichment
from loveapp.domain.advice import RelationshipContext
from loveapp.domain.memory import (
    AtomicExtraction,
    CoarseExtraction,
    CoarseProposition,
    EpistemicStatus,
    ExtractionEpistemicStatus,
    MemoryKind,
    MemoryStatus,
    MessageRole,
    SemanticRole,
)
from loveapp.domain.memory_normalization import normalize_memory_candidate_contract
from loveapp.domain.runtime_context import ConversationContext, PendingQuestion
from loveapp.evaluation.memory_context_aware_behavioral_anchor import (
    _attach_stage1_origins,
    _forbidden_hits,
    _unit_rows,
    evaluate_context_aware_behavioral_anchor,
)
from loveapp.evaluation.memory_extraction_diagnostics import extraction_failure_stages

NOW = datetime(2026, 9, 11, tzinfo=UTC)


def _prop(
    text="她陪我聊天", *, pid="p1", kind="interaction_event", role="new_proposition", **extra
):
    return {
        "proposition_id": pid,
        "evidence_span": text,
        "candidate_kind": kind,
        "semantic_role": role,
        **extra,
    }


def _claim(text="她陪我聊天", *, pid="p1", kind="interaction_event", **extra):
    return {
        "semantic_type": "new_memory",
        "claim_id": pid,
        "kind": kind,
        "subject": "relationship",
        "predicate": "chatted",
        "summary": text,
        "evidence_spans": [text],
        "semantic_payload": {"event_type": "shared_activity"},
        **extra,
    }


def _enrichment(text="因为钱的事", **extra):
    return {
        "semantic_type": "enrichment",
        "unit_id": "p1",
        "target_kind": "interaction_event",
        "target_semantic_hint": {"event_type": "conflict"},
        "attribute_namespace": "canonical",
        "attribute_name": "cause",
        "value": {"category": "financial_values"},
        "evidence_span": text,
        **extra,
    }


def _extractor(props, units, *, direct=False, fallback=None, answered=()):
    responses = [
        {
            "should_extract": True,
            "gate_reason": "COMPOUND_MEMORY",
            "semantic_units": props,
            "answered_pending_questions": list(answered),
        },
        {"claims" if direct else "semantic_units": units},
    ]
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content=json.dumps(responses[len(calls) - 1], ensure_ascii=False),
                    ),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

    async def close():
        pass

    return TwoStageMemoryExtractor(
        api_key=SecretStr("test"),
        base_url="https://example.invalid",
        model="fake-flash",
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=close
        ),
        fallback=fallback,
    ), calls


async def _run(extractor, text, **kwargs):
    return await extractor.extract(
        text,
        reference_time=NOW,
        existing_memories=[],
        conversation_history=[],
        **kwargs,
    )


def test_documented_stage1_schema_keeps_four_axes_independent():
    coarse = CoarseExtraction.model_validate(
        {
            "should_extract": True,
            "semantic_units": [
                {
                    "evidence_span": "我觉得她可能不喜欢我",
                    "semantic_role": "NEW_PROPOSITION",
                    "candidate_kind": "relationship_state",
                    "proposition_origin": "ANSWER_TO_QUESTION",
                    "perspective": "USER",
                    "epistemic_status": "BELIEVED",
                    "temporal_hint": {},
                    "target_field_hint": [],
                    "attributes_hint": [],
                    "confidence": 0.9,
                }
            ],
        }
    )
    p = coarse.propositions[0]
    assert p.semantic_role == SemanticRole.NEW_PROPOSITION
    assert p.candidate_kinds == [MemoryKind.RELATIONSHIP_STATE]
    assert p.epistemic_status == ExtractionEpistemicStatus.BELIEVED
    assert p.proposition_origin.value == "answer_to_question"
    assert SemanticRoleRouter().route(p).selected_route == Stage2ExtractorRoute.BELIEF
    assert not hasattr(p, "target_memory_id")


@pytest.mark.parametrize(
    ("kind", "role", "epistemic", "route"),
    [
        ("interaction_event", "new_proposition", "reported", "new_memory"),
        ("stable_fact", "new_proposition", "observed", "new_memory"),
        ("preference", "new_proposition", "reported", "new_memory"),
        ("interaction_pattern", "new_proposition", "reported", "pattern"),
        ("relationship_state", "new_proposition", "reported", "state"),
        ("relationship_state", "new_proposition", "believed", "belief"),
        ("interaction_pattern", "new_proposition", "inferred", "pattern"),
        ("interaction_event", "attribute_completion", "reported", "event_enrichment"),
        ("stable_fact", "attribute_completion", "reported", "uncertain"),
        ("interaction_event", "refinement", "reported", "uncertain"),
        ("stable_fact", "refinement", "reported", "refinement"),
        ("interaction_pattern", "correction", "reported", "pattern"),
        ("relationship_state", "correction", "believed", "belief"),
    ],
)
def test_role_kind_epistemic_matrix(kind, role, epistemic, route):
    proposition = CoarseProposition.model_validate(
        _prop(kind=kind, role=role, epistemic_status=epistemic)
    )
    routed = SemanticRoleRouter().route(proposition)
    assert routed.selected_route.value == route
    if kind == "interaction_event" and role == "new_proposition":
        assert routed.extractor_name == "NewEventExtractor"


@pytest.mark.parametrize(
    ("kind", "selected"),
    [
        ("stable_fact", "refinement"),
        ("interaction_event", "event_enrichment"),
    ],
)
def test_invalid_role_kind_pair_does_not_create_spurious_route_conflict(kind, selected):
    p = CoarseProposition.model_validate(
        _prop(
            kind=kind,
            role="refinement",
            semantic_role_candidates=["refinement", "attribute_completion"],
        )
    )
    route = SemanticRoleRouter().route(p)
    assert route.selected_route.value == selected
    assert len(route.rejected_combinations) == 1
    assert route.reason == "single_compatible_semantic_role"


def test_multi_kind_new_proposition_cannot_be_discarded_to_manufacture_unique_refinement():
    p = CoarseProposition.model_validate(
        _prop(
            kind=["interaction_event", "interaction_pattern"],
            semantic_role_candidates=["new_proposition", "refinement"],
        )
    )
    route = SemanticRoleRouter().route(p)
    assert set(route.candidate_routes) == {"new_memory", "pattern", "refinement"}
    assert route.selected_route == Stage2ExtractorRoute.UNCERTAIN


@pytest.mark.parametrize(
    "origin", ["answer_to_question", "spontaneous_disclosure", "new_occurrence"]
)
def test_proposition_origin_alone_cannot_change_route(origin):
    p = CoarseProposition.model_validate(_prop(proposition_origin=origin))
    assert SemanticRoleRouter().route(p).selected_route.value == "new_memory"


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize(
    ("epistemic", "perspective", "stored"),
    [
        ("observed", "user_reported", "confirmed"),
        ("reported", "user_reported", "confirmed"),
        ("believed", "user_belief", "uncertain"),
        ("inferred", "model_inferred", "hypothesis"),
        ("uncertain", "user_reported", "uncertain"),
    ],
)
async def test_stage1_epistemic_survives_overconfident_stage2_and_legacy_claim_envelope(
    direct,
    epistemic,
    perspective,
    stored,
):
    claim = _claim(epistemic_status="confirmed", perspective="user_reported")
    if direct:
        claim["payload"] = claim.pop("semantic_payload")
        claim.pop("semantic_type")
    extractor, calls = _extractor(
        [_prop(epistemic_status=epistemic, perspective="USER")],
        [claim],
        direct=direct,
    )
    result = await _run(extractor, "她陪我聊天")
    assert len(calls) == 2
    assert not extractor.last_diagnostic["fallback"]["triggered"]
    assert result.claims[0].epistemic_status.value == stored
    assert result.claims[0].perspective.value == perspective
    assert result.semantic_units[0].provenance.epistemic_status.value == epistemic


async def test_absent_legacy_epistemic_does_not_change_confirmed_report_default():
    extractor, _ = _extractor([_prop(role="standalone_proposition")], [_claim()])
    result = await _run(extractor, "她陪我聊天")
    assert result.claims[0].epistemic_status == EpistemicStatus.CONFIRMED
    assert result.semantic_units[0].provenance.epistemic_status is None


async def test_legacy_belief_role_and_new_operation_share_safe_routing():
    extractor, _ = _extractor([_prop(role="belief_extraction")], [_claim()])
    result = await _run(extractor, "她陪我聊天")
    assert extractor.last_diagnostic["stage2"]["routes"][0]["semantic_roles"] == ["new_proposition"]
    assert result.claims[0].perspective.value == "user_belief"
    assert result.claims[0].epistemic_status == EpistemicStatus.UNCERTAIN


async def test_believed_enrichment_cannot_authorize_a_write():
    extractor, _ = _extractor(
        [_prop("因为钱的事", role="attribute_completion", epistemic_status="believed")],
        [_enrichment(epistemic_status="confirmed", perspective="user_reported")],
    )
    result = await _run(extractor, "因为钱的事")
    draft = result.semantic_units[0]
    resolution = resolve_event_enrichment(
        draft,
        current_text="因为钱的事",
        conversation_history=[],
        existing_memories=[],
    )
    assert not resolution.resolved
    assert resolution.reason == "enrichment_evidence_not_confirmed"


async def test_unknown_or_ambiguous_routes_do_not_trigger_fallback_to_bypass_abstention():
    class Fallback:
        calls = 0

        async def extract(self, *_args, **_kwargs):
            self.calls += 1
            return AtomicExtraction()

    fallback = Fallback()
    extractor, calls = _extractor(
        [_prop(kind=["interaction_event", "interaction_pattern"])],
        [_claim()],
        fallback=fallback,
    )
    result = await _run(extractor, "她陪我聊天")
    assert result.should_extract is True
    assert result.claims == []
    assert len(calls) == 1
    assert fallback.calls == 0
    assert extractor.last_diagnostic["stage2"]["abstentions"][0]["reason"] == "ROUTING_AMBIGUITY"


async def test_mixed_batch_preserves_safe_claim_while_rejecting_model_output_for_illegal_route():
    extractor, _ = _extractor(
        [
            _prop("她陪我聊天"),
            _prop("因为钱的事", pid="p2", kind="stable_fact", role="attribute_completion"),
        ],
        [_claim(), _enrichment(unit_id="p2")],
    )
    result = await _run(extractor, "她陪我聊天，因为钱的事")
    assert [claim.claim_id for claim in result.claims] == ["p1"]
    assert len(result.semantic_units) == 1
    assert extractor.last_diagnostic["stage2"]["abstentions"] == [
        {"proposition_id": "p2", "reason": "ROUTING_ABSTENTION"},
    ]


async def test_pending_question_alignment_is_transported_without_target_authority():
    question = PendingQuestion(
        question_id="q-cause",
        target_kind="interaction_event",
        target_field="cause",
        expected_answer_type="cause",
    )
    context = ConversationContext(current_user_message="因为钱的事", pending_questions=[question])
    extractor, calls = _extractor(
        [
            _prop(
                "因为钱的事", role="attribute_completion", answered_pending_questions=[question.id]
            )
        ],
        [_enrichment()],
        answered=[question.id],
    )
    result = await _run(extractor, "因为钱的事", conversation_context=context)
    for call in calls:
        sent = json.loads(call["messages"][1]["content"])
        assert sent["conversation_context"]["pending_questions"][0]["question_id"] == "q-cause"
        assert (
            sent["conversation_context"]["pending_questions"][0]["expected_answer_type"] == "cause"
        )
    assert result.semantic_units[0].provenance.answered_pending_questions == ["q-cause"]
    resolution = resolve_event_enrichment(
        result.semantic_units[0],
        current_text="因为钱的事",
        conversation_history=[],
        existing_memories=[],
    )
    assert not resolution.resolved


async def test_fabricated_question_id_fails_schema_validation():
    extractor, calls = _extractor(
        [_prop(answered_pending_questions=["memory-123"])],
        [_claim()],
    )
    result = await _run(extractor, "她陪我聊天")
    assert not result.claims
    assert len(calls) == 1
    assert extractor.last_diagnostic["fallback"]["reason_code"] == "STAGE1_SCHEMA_ERROR"


@pytest.mark.parametrize("same_group", [True, False])
async def test_stage2_only_recombines_propositions_from_same_occurrence(same_group):
    text = "昨天我压力很大，她陪我聊了两个小时"
    props = [
        _prop("昨天我压力很大", same_occurrence_group="g1", epistemic_status="reported"),
        _prop(
            "她陪我聊了两个小时",
            pid="p2",
            same_occurrence_group="g1" if same_group else "g2",
            epistemic_status="reported",
        ),
    ]
    extractor, _ = _extractor(
        props,
        [_claim(text, pid="merged", proposition_ids=["p1", "p2"], same_occurrence_group="g1")],
    )
    result = await _run(extractor, text)
    if same_group:
        assert len(result.claims) == 1
        assert result.semantic_units[0].provenance.proposition_ids == ["p1", "p2"]
        assert result.semantic_units[0].provenance.same_occurrence_group == "g1"
    else:
        assert not result.claims
        assert not extractor.last_diagnostic["fallback"]["triggered"]
        assert (
            extractor.last_diagnostic["stage2"]["abstentions"][0]["reason"] == "ROUTING_AMBIGUITY"
        )


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_group_provenance_round_trips_existing_store_json_contract(backend, tmp_path):
    extractor, _ = _extractor([_prop(same_occurrence_group="g1")], [_claim()])
    claim = (await _run(extractor, "她陪我聊天")).claims[0]
    candidate = normalize_memory_candidate_contract(
        claim.to_candidate(), NOW, allow_legacy_open_world=True
    )
    store = InMemoryMemoryStore() if backend == "memory" else SQLiteMemoryStore(tmp_path / "v13.db")
    await store.save_relationship_context(
        RelationshipContext(user_id="v13-user", relationship_id="v13-rel")
    )
    source = await store.add_message(
        user_id="v13-user",
        relationship_id="v13-rel",
        conversation_id="v13-conversation",
        role=MessageRole.USER,
        content="她陪我聊天",
    )
    saved = await store.save_memory(
        user_id="v13-user",
        relationship_id="v13-rel",
        source_message_id=source.id,
        candidate=candidate,
        status=MemoryStatus.CONFIRMED,
    )
    if backend == "sqlite":
        await store.aclose()
        store = SQLiteMemoryStore(tmp_path / "v13.db")
    loaded = await store.get_memory(saved.item.id, "v13-user")
    assert loaded.payload["extraction_provenance"]["same_occurrence_group"] == "g1"
    assert loaded.source_message_id == source.id
    await store.aclose()


async def test_correction_outputs_corrected_claim_instead_of_trace_only_refinement():
    text = "刚才说错了，我住北京"
    extractor, _ = _extractor(
        [_prop(text, kind="stable_fact", role="correction", epistemic_status="reported")],
        [
            _claim(
                text,
                kind="stable_fact",
                subject="user",
                predicate="resides_in",
                semantic_payload={"value": "北京"},
            )
        ],
    )
    result = await _run(extractor, text)
    assert len(result.claims) == 1
    assert result.semantic_units[0].semantic_type == "new_memory"
    assert result.semantic_units[0].provenance.semantic_roles == [SemanticRole.CORRECTION]
    assert "target_memory_id" not in result.claims[0].payload
    assert _unit_rows(result)[0]["semantic_role"] == "correction"


@pytest.mark.parametrize("explicit_ids", [False, True])
async def test_proposition_id_cannot_attach_epistemics_to_unrelated_evidence(explicit_ids):
    extractor, _ = _extractor(
        [_prop(), _prop("她可能讨厌我", pid="p2", epistemic_status="believed")],
        [_claim("她可能讨厌我", **({"proposition_ids": ["p1"]} if explicit_ids else {}))],
    )
    result = await _run(extractor, "她陪我聊天，她可能讨厌我")
    assert not result.claims
    assert not extractor.last_diagnostic["fallback"]["triggered"]
    abstention = extractor.last_diagnostic["stage2"]["abstentions"][0]
    assert abstention["reason"] == "ROUTING_ABSTENTION"
    assert "evidence" in abstention["detail"]


@pytest.mark.parametrize("grouped", [True, False])
async def test_single_supplied_id_does_not_hide_second_proposition_in_group_evidence(grouped):
    text = "她陪我聊天，应该是为了安慰我"
    group = "g1" if grouped else None
    extractor, _ = _extractor(
        [
            _prop(same_occurrence_group=group, epistemic_status="reported"),
            _prop(
                "应该是为了安慰我",
                pid="p2",
                same_occurrence_group=group,
                epistemic_status="believed",
            ),
        ],
        [_claim(text, proposition_ids=["p1"], epistemic_status="confirmed")],
    )
    result = await _run(extractor, text)
    assert not extractor.last_diagnostic["fallback"]["triggered"]
    if grouped:
        assert result.claims[0].epistemic_status == EpistemicStatus.UNCERTAIN
        assert result.semantic_units[0].provenance.proposition_ids == ["p1", "p2"]
    else:
        assert not result.claims
        assert (
            extractor.last_diagnostic["stage2"]["abstentions"][0]["reason"] == "ROUTING_AMBIGUITY"
        )


@pytest.mark.parametrize(
    ("source_kind", "output_kind"),
    [("interaction_event", "relationship_state"), ("relationship_state", "interaction_event")],
)
async def test_occurrence_group_cannot_combine_state_propositions(source_kind, output_kind):
    text = "她陪我聊天，我们关系很好"
    extractor, _ = _extractor(
        [
            _prop(kind=source_kind, same_occurrence_group="g1"),
            _prop("我们关系很好", pid="p2", kind=source_kind, same_occurrence_group="g1"),
        ],
        [_claim(text, kind=output_kind, proposition_ids=["p1", "p2"])],
    )
    result = await _run(extractor, text)
    assert not result.claims
    assert not extractor.last_diagnostic["fallback"]["triggered"]
    assert extractor.last_diagnostic["stage2"]["abstentions"][0]["reason"] == "ROUTING_ABSTENTION"


@pytest.mark.parametrize("source", ["user_reported", "derived_from_events"])
async def test_pattern_source_survives_orthogonal_new_proposition_route(source):
    text = "最近总是我主动联系她"
    extractor, _ = _extractor(
        [_prop(text, kind="interaction_pattern", epistemic_status="reported")],
        [
            _claim(
                text,
                kind="interaction_pattern",
                predicate="initiation_balance",
                semantic_payload={
                    "metric": "initiation_balance",
                    "value": "user",
                    "source": source,
                },
            )
        ],
    )
    result = await _run(extractor, text)
    assert len(result.claims) == 1
    assert result.claims[0].payload["source"] == source
    assert result.semantic_units[0].provenance.semantic_roles == [SemanticRole.NEW_PROPOSITION]


@pytest.mark.parametrize(
    ("case", "expected_stage"),
    [
        ("ambiguous", "ROUTING_AMBIGUITY"),
        ("illegal_pair", "ROUTING_ABSTENTION"),
        ("empty", "EMPTY_STAGE2_OUTPUT"),
        ("schema", "SCHEMA_VALIDATION_FAILURE"),
        ("resolver", "RESOLVER_FAILURE"),
        ("allowed_abstention", None),
    ],
)
async def test_evaluator_primary_failure_reflects_actual_stage(case, expected_stage, tmp_path):
    text = "因为钱的事"
    prop = _prop(text)
    units = [_claim(text)]
    if case in {"ambiguous", "allowed_abstention"}:
        prop["candidate_kind"] = ["interaction_event", "interaction_pattern"]
    elif case == "illegal_pair":
        prop.update(candidate_kind="stable_fact", semantic_role="attribute_completion")
    elif case == "empty":
        units = []
    elif case == "schema":
        units[0]["confidence"] = 2.0
    elif case == "resolver":
        prop["semantic_role"] = "attribute_completion"
        units = [_enrichment()]
    extractor, _ = _extractor([prop], units)
    row = {
        "case_id": "V13-DIAGNOSTIC",
        "category": "diagnostics",
        "text": text,
        "expected_semantics": {
            "should_extract": True,
            "unit_count": 0 if case == "allowed_abstention" else 1,
        },
    }
    if case == "resolver":
        row["resolution_expectation"] = {"status": "resolved"}
    path = tmp_path / "case.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    report = await evaluate_context_aware_behavioral_anchor(
        path,
        extractor=extractor,
        baseline_path=None,
        require_full_suite=False,
    )
    result = report["cases"][0]
    assert result["primary_failure_stage"] == expected_stage
    assert result["passed"] is (case == "allowed_abstention")
    assert report["metrics"]["model_parse_failure_count"] == 0


@pytest.mark.parametrize("semantic_type", ["enrichment", "refinement"])
def test_evaluator_preserves_origin_for_non_new_memory_drafts(semantic_type):
    rows = _attach_stage1_origins(
        [{"semantic_type": semantic_type, "raw": {"unit_id": "p1"}}],
        [{"proposition_id": "p1", "proposition_origin": "answer_to_question"}],
    )
    assert rows[0]["proposition_origin"] == "answer_to_question"


def test_evaluator_group_origin_uses_all_provenance_not_first_row():
    rows = _attach_stage1_origins(
        [{"raw": {"provenance": {"proposition_ids": ["p1", "p2"]}}}],
        [
            {"proposition_id": "p1", "proposition_origin": "answer_to_question"},
            {"proposition_id": "p2", "proposition_origin": "new_occurrence"},
        ],
    )
    assert rows[0]["proposition_origin"] == "uncertain"
    assert rows[0]["proposition_origin_proposition_ids"] == ["p1", "p2"]


@pytest.mark.parametrize("epistemic", ["uncertain", "confirmed"])
def test_evaluator_only_flags_actually_confirmed_belief(epistemic):
    hits = _forbidden_hits(
        {"forbidden": ["confirmed_belief"]},
        [{"perspective": "user_belief", "epistemic_status": epistemic}],
    )
    assert hits == (["confirmed_belief"] if epistemic == "confirmed" else [])


@pytest.mark.parametrize("nested", [False, True])
async def test_evaluator_reads_event_attributes_without_losing_nested_cause(nested):
    text = "今天又因为钱的事吵架了"
    details = {"cause": "钱的事"}
    extractor, _ = _extractor(
        [_prop(text)],
        [
            _claim(
                text,
                semantic_payload={
                    "event_type": "conflict",
                    **({"attributes": details} if nested else details),
                },
            )
        ],
    )
    result = await _run(extractor, text)
    assert "钱的事" in _unit_rows(result)[0]["values"]


async def test_retired_operation_expectation_is_reviewed_without_rewriting_gold(tmp_path):
    text = "我感觉她不喜欢我"
    extractor, _ = _extractor(
        [_prop(text, kind="relationship_state", epistemic_status="believed")],
        [_claim(text, kind="relationship_state")],
    )
    expected = {
        "should_extract": True,
        "unit_count": 1,
        "required": [
            {
                "semantic_role": "belief_extraction",
                "memory_kind": "relationship_state",
                "perspective": "user_belief",
            }
        ],
        "forbidden": ["confirmed_belief"],
    }
    path = tmp_path / "legacy.jsonl"
    path.write_text(
        json.dumps(
            {
                "case_id": "legacy-belief",
                "category": "epistemics",
                "text": text,
                "expected_semantics": expected,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    report = await evaluate_context_aware_behavioral_anchor(
        path,
        extractor=extractor,
        baseline_path=None,
        require_full_suite=False,
    )
    row = report["cases"][0]
    assert row["expected_semantics"] == expected
    assert not row["passed"]
    assert row["needs_review"]
    assert row["primary_failure_stage"] == "EVALUATION_EXPECTATION"
    assert row["forbidden_hits"] == []


@pytest.mark.parametrize("field", ["target_semantic_hint", "temporal_hint"])
def test_stage1_cannot_smuggle_store_authority_in_hints(field):
    with pytest.raises(ValidationError):
        CoarseProposition.model_validate(_prop(**{field: {"target_memory_id": "old"}}))


def test_canonical_prompt_excludes_legacy_operations_and_does_not_expose_write_targets():
    prompt = json.loads(
        _build_coarse_prompt(
            "我俩正在吵架",
            reference_time=NOW,
            existing_memories=[],
            conversation_history=[],
            pending_memory_context=None,
        )
    )
    assert set(prompt["contract"]["semantic_roles"]) == {
        "new_proposition",
        "attribute_completion",
        "refinement",
        "correction",
        "uncertain",
    }
    assert "same_occurrence_group" in prompt["contract"]["semantic_units"]["fields"]
    assert set(prompt["contract"]["epistemic_statuses"]) == {
        item.value for item in ExtractionEpistemicStatus
    }


@pytest.mark.parametrize(
    ("category", "stage"),
    [
        ("json_syntax", "MODEL_PARSE_FAILURE"),
        ("empty_response", "MODEL_PARSE_FAILURE"),
        ("schema_validation", "SCHEMA_VALIDATION_FAILURE"),
        ("unsupported_enum", "SCHEMA_VALIDATION_FAILURE"),
        ("empty_claims", "EMPTY_STAGE2_OUTPUT"),
        ("routing_ambiguity", "ROUTING_AMBIGUITY"),
        ("routing_abstention", "ROUTING_ABSTENTION"),
        ("transport_or_runtime", "TRANSPORT_OR_RUNTIME_FAILURE"),
    ],
)
def test_evaluator_does_not_report_all_failures_as_model_parse(category, stage):
    assert extraction_failure_stages(
        [{"status": "failed", "stage": "detailed", "failure_category": category}]
    ) == [stage]


def test_evaluator_records_router_abstention_without_inventing_failed_model_call():
    assert extraction_failure_stages(
        [], {"stage2": {"abstentions": [{"reason": "ROUTING_ABSTENTION"}]}}
    ) == ["ROUTING_ABSTENTION"]

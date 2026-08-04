import json
from datetime import UTC, datetime

import pytest

from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.application.memory import MemoryService
from loveapp.application.memory_repair import (
    parse_memory_response,
    validate_memory_extraction,
)
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    DiscardedSpan,
    DiscardReason,
    MemoryKind,
    MemoryStatus,
)
from loveapp.domain.memory_dimensions import (
    detect_evidence_dimensions,
    normalize_interaction_metric,
)


class SequenceExtractor:
    def __init__(self, extractions: list[AtomicExtraction]) -> None:
        self._extractions = iter(extractions)

    async def extract(self, text: str, **kwargs) -> AtomicExtraction:
        del text, kwargs
        return next(self._extractions)


def test_parser_keeps_independent_relationship_state_dimensions() -> None:
    text = "我们刚认识不久，还不太熟，但每周都能在社团见面，聊天大多围绕社团工作。"
    payload = {
        "claims": [
            _state_claim(
                "familiarity",
                "relationship_familiarity",
                "low",
                "双方目前熟悉度较低",
                "还不太熟",
            ),
            _state_claim(
                "opportunity",
                "contact_opportunity",
                "high",
                "双方每周都有见面机会",
                "每周都能在社团见面",
            ),
            {
                "claim_id": "topic-scope",
                "kind": "interaction_pattern",
                "subject": "relationship",
                "predicate": "conversation_topic_scope",
                "summary": "双方聊天大多围绕社团工作",
                "evidence_spans": ["聊天大多围绕社团工作"],
                "payload": {
                    "metric": "topic_scope",
                    "current": "shared_work",
                },
            },
        ],
        "discarded_spans": [],
    }

    parsed = parse_memory_response(json.dumps(payload, ensure_ascii=False), source_text=text)

    assert [claim.kind for claim in parsed.extraction.claims] == [
        MemoryKind.RELATIONSHIP_STATE,
        MemoryKind.RELATIONSHIP_STATE,
        MemoryKind.INTERACTION_PATTERN,
    ]
    assert [
        claim.payload.get("state_dimension") for claim in parsed.extraction.claims[:2]
    ] == ["relationship_familiarity", "contact_opportunity"]


@pytest.mark.parametrize(
    "payload",
    [
        {"state_value": "low"},
        {"state_dimension": "relationship_familiarity"},
        {"state_dimension": "unsupported_dimension", "state_value": "low"},
        {"state_dimension": "relationship_familiarity", "state_value": "frequent"},
    ],
)
def test_relationship_state_requires_registered_dimension_and_value(payload: dict) -> None:
    claim = AtomicClaim(
        claim_id="invalid-state",
        kind=MemoryKind.RELATIONSHIP_STATE,
        subject="relationship",
        predicate="relationship_state",
        summary="双方当前关系状态发生变化",
        evidence_spans=["我们现在的状态有变化"],
        payload=payload,
    )

    with pytest.raises(ValueError, match="关系状态"):
        validate_memory_extraction(
            AtomicExtraction(claims=[claim]),
            "我们现在的状态有变化",
        )


def test_multi_dimension_claim_keeps_safely_repairable_dimension() -> None:
    text = "平时很少有机会见面，聊天也基本只谈工作。"
    payload = {
        "claims": [
            {
                "claim_id": "combined",
                "kind": "interaction_pattern",
                "subject": "relationship",
                "predicate": "interaction_is_limited",
                "summary": "双方见面机会少且聊天只谈工作",
                "evidence_spans": ["平时很少有机会见面，聊天也基本只谈工作"],
                "payload": {"metric": "contact_frequency", "frequency": "low"},
            }
        ],
        "discarded_spans": [],
    }

    parsed = parse_memory_response(
        json.dumps(payload, ensure_ascii=False),
        source_text=text,
    )

    assert len(parsed.extraction.claims) == 1
    repaired = parsed.extraction.claims[0]
    assert repaired.payload["metric"] == "topic_scope"
    assert repaired.evidence_spans == ["聊天也基本只谈工作"]
    assert parsed.repaired_claim_count == 1
    assert parsed.discarded_claim_count == 0
    assert "atomic_evidence_narrowing" in parsed.repair_steps


@pytest.mark.parametrize(
    ("text", "metric", "predicate"),
    [
        (
            "最近主要在线上聊天，而且联系比以前更频繁了。",
            "contact_frequency",
            "contact_frequency_changed",
        ),
        (
            "我们主要通过微信聊生活和兴趣。",
            "interaction_channel",
            "primary_communication_channel",
        ),
        (
            "我们在共同项目里通常只聊工作。",
            "topic_scope",
            "conversation_topic_scope",
        ),
    ],
)
def test_context_qualifier_does_not_turn_one_metric_into_non_atomic_claim(
    text: str,
    metric: str,
    predicate: str,
) -> None:
    payload = {
        "claims": [
            {
                "claim_id": "qualified-pattern",
                "kind": "interaction_pattern",
                "subject": "relationship",
                "predicate": predicate,
                "summary": "双方当前互动存在一个带场景限定的主要模式",
                "evidence_spans": [text.rstrip("。")],
                "payload": {"metric": metric, "current": "reported"},
            }
        ],
        "discarded_spans": [],
    }

    parsed = parse_memory_response(json.dumps(payload, ensure_ascii=False), source_text=text)

    assert [claim.payload["metric"] for claim in parsed.extraction.claims] == [metric]


def test_single_clear_evidence_dimension_repairs_mislabeled_interaction_metric() -> None:
    text = "双方主要通过微信联系。"
    payload = {
        "claims": [
            {
                "claim_id": "channel",
                "kind": "interaction_pattern",
                "subject": "relationship",
                "predicate": "contact_frequency",
                "summary": "双方主要通过微信联系",
                "evidence_spans": ["主要通过微信联系"],
                "payload": {
                    "metric": "contact_frequency",
                    "channel": "wechat",
                },
            }
        ],
        "discarded_spans": [],
    }

    parsed = parse_memory_response(json.dumps(payload, ensure_ascii=False), source_text=text)

    assert parsed.extraction.claims[0].payload["metric"] == "interaction_channel"
    assert parsed.extraction.claims[0].predicate == "interaction_channel"
    assert "interaction_metric_from_evidence" in parsed.repair_steps


def test_contact_initiative_alias_supports_channel_qualified_pattern() -> None:
    text = "微信上通常是我主动找她聊天。"
    payload = {
        "claims": [
            {
                "claim_id": "initiative",
                "kind": "interaction_pattern",
                "subject": "relationship",
                "predicate": "has_contact_pattern",
                "summary": "微信聊天通常由用户主动发起",
                "evidence_spans": ["微信上通常是我主动找她聊天"],
                "payload": {
                    "metric": "contact_initiative",
                    "channel": "wechat",
                    "current": "user_initiated",
                },
            }
        ],
        "discarded_spans": [],
    }

    parsed = parse_memory_response(json.dumps(payload, ensure_ascii=False), source_text=text)

    assert parsed.extraction.claims[0].payload["metric"] == "initiation_balance"
    assert "interaction_metric_aliases" in parsed.repair_steps


def test_initiation_predicate_wins_over_frequency_qualifier() -> None:
    text = "周末见面时对方偶尔会先问候我。"
    payload = {
        "claims": [
            {
                "claim_id": "partner-initiative",
                "kind": "interaction_pattern",
                "subject": "partner",
                "predicate": "initiates_contact",
                "summary": "周末见面时对方偶尔会先问候用户",
                "evidence_spans": ["周末见面时对方偶尔会先问候我"],
                "payload": {
                    "metric": "contact_frequency",
                    "frequency": "occasionally",
                    "channel": "in_person",
                },
            }
        ],
        "discarded_spans": [],
    }

    parsed = parse_memory_response(json.dumps(payload, ensure_ascii=False), source_text=text)

    assert parsed.extraction.claims[0].payload["metric"] == "initiation_balance"
    assert "interaction_metric_from_evidence" in parsed.repair_steps


@pytest.mark.parametrize(
    "alias",
    [
        "contact_initiative",
        "contact_initiation",
        "conversation_initiative",
        "conversation_initiator",
        "interaction_initiative",
        "interaction_initiator",
        "initiation_frequency",
        "initiative_pattern",
        "who_initiates",
    ],
)
def test_initiation_metric_aliases_share_one_canonical_dimension(alias: str) -> None:
    assert normalize_interaction_metric(alias) == "initiation_balance"


def test_comparative_familiarity_language_is_a_registered_dimension() -> None:
    dimensions = detect_evidence_dimensions("最近相处多了，我们慢慢熟络了一些。")

    assert "relationship_familiarity" in dimensions


def test_discarded_span_overlapping_claim_evidence_is_removed() -> None:
    text = "我们聊天大多围绕课程，偶尔才聊自己的生活。"
    payload = {
        "claims": [
            {
                "claim_id": "topic-scope",
                "kind": "interaction_pattern",
                "subject": "relationship",
                "predicate": "conversation_topic_scope",
                "summary": "双方聊天以课程话题为主",
                "evidence_spans": ["我们聊天大多围绕课程，偶尔才聊自己的生活"],
                "payload": {"metric": "topic_scope", "current": "mostly_course"},
            }
        ],
        "discarded_spans": [
            {"text": "偶尔才聊自己的生活", "reason": "ephemeral"}
        ],
    }

    parsed = parse_memory_response(json.dumps(payload, ensure_ascii=False), source_text=text)

    assert len(parsed.extraction.claims) == 1
    assert parsed.extraction.discarded_spans == []
    assert "discarded_overlap" in parsed.repair_steps


async def test_new_state_supersedes_only_the_same_dimension() -> None:
    first_text = "我们刚认识不久，目前还不太熟，也很少有机会碰面。"
    second_text = "相处一段时间后我们已经很熟了，但见面机会还是很少。"
    extractor = SequenceExtractor(
        [
            AtomicExtraction(
                claims=[
                    _state_atomic_claim(
                        "familiarity-low",
                        "relationship_familiarity",
                        "low",
                        "双方目前熟悉度较低",
                        "目前还不太熟",
                    ),
                    _state_atomic_claim(
                        "opportunity-low",
                        "contact_opportunity",
                        "low",
                        "双方目前见面机会较少",
                        "很少有机会碰面",
                    ),
                ]
            ),
            AtomicExtraction(
                claims=[
                    _state_atomic_claim(
                        "familiarity-high",
                        "relationship_familiarity",
                        "high",
                        "双方目前已经比较熟悉",
                        "我们已经很熟了",
                    ),
                    _state_atomic_claim(
                        "opportunity-still-low",
                        "contact_opportunity",
                        "low",
                        "双方见面机会仍然较少",
                        "见面机会还是很少",
                    ),
                ]
            ),
        ]
    )
    store = InMemoryMemoryStore()
    service = MemoryService(
        store,
        extractor,
        clock=lambda: datetime(2099, 7, 31, 12, tzinfo=UTC),
    )
    scope = {
        "user_id": "state-user",
        "relationship_id": "partner",
        "conversation_id": "state-conversation",
    }

    first = await service.remember_text(text=first_text, **scope)
    second = await service.remember_text(text=second_text, **scope)

    first_by_dimension = {
        item.item.payload["state_dimension"]: item.item for item in first.saved
    }
    second_by_dimension = {
        item.item.payload["state_dimension"]: item.item for item in second.saved
    }
    old_familiarity = await store.get_memory(
        first_by_dimension["relationship_familiarity"].id,
        "state-user",
    )
    old_opportunity = await store.get_memory(
        first_by_dimension["contact_opportunity"].id,
        "state-user",
    )
    assert old_familiarity is not None
    assert old_familiarity.status == MemoryStatus.SUPERSEDED
    assert old_opportunity is not None
    assert old_opportunity.status in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
    assert (
        second_by_dimension["contact_opportunity"].id
        == first_by_dimension["contact_opportunity"].id
    )

    context = await service.get_context("state-user", "partner")
    current = {
        item.payload["state_dimension"]: item.payload["state_value"]
        for item in context.current_state
    }
    assert current == {
        "relationship_familiarity": "high",
        "contact_opportunity": "low",
    }


async def test_discarded_spans_are_persisted_in_extraction_runs() -> None:
    extractor = SequenceExtractor(
        [
            AtomicExtraction(
                discarded_spans=[
                    DiscardedSpan(
                        text="这是不是说明她喜欢我",
                        reason=DiscardReason.CONSULTATION_QUESTION,
                    )
                ]
            )
        ]
    )
    store = InMemoryMemoryStore()
    service = MemoryService(store, extractor)

    await service.remember_text(
        user_id="discard-run-user",
        relationship_id="partner",
        conversation_id="discard-run-conversation",
        text="她最近主动找我聊天，这是不是说明她喜欢我",
    )

    runs = await store.list_extraction_runs(
        user_id="discard-run-user",
        relationship_id="partner",
    )
    assert runs[0].discarded_spans == [
        DiscardedSpan(
            text="这是不是说明她喜欢我",
            reason=DiscardReason.CONSULTATION_QUESTION,
        )
    ]


def _state_claim(
    claim_id: str,
    dimension: str,
    value: str,
    summary: str,
    evidence: str,
) -> dict:
    return {
        "claim_id": claim_id,
        "kind": "relationship_state",
        "subject": "relationship",
        "predicate": "relationship_state",
        "summary": summary,
        "evidence_spans": [evidence],
        "payload": {"state_dimension": dimension, "state_value": value},
    }


def _state_atomic_claim(
    claim_id: str,
    dimension: str,
    value: str,
    summary: str,
    evidence: str,
) -> AtomicClaim:
    return AtomicClaim.model_validate(
        _state_claim(claim_id, dimension, value, summary, evidence)
    )

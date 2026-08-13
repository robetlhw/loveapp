from datetime import date

from loveapp.adapters.routing.openai_compatible import _parse_response_with_slot_rejections
from loveapp.application.route_slot_validation import (
    merge_route_slot_sources,
    validate_route_slots,
)
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.enums import DatePlanningStatus
from loveapp.domain.memory import MessageRole, StoredMessage
from loveapp.domain.routing import DatePlanSlots, RouteInput


def test_slot_validator_drops_hallucinated_city_and_budget() -> None:
    result = validate_route_slots(
        RouteInput(latest_query="帮我安排一次约会"),
        DatePlanSlots(),
        DatePlanSlots(city="上海", budget=500),
    )

    assert result.validated_slots.city is None
    assert result.validated_slots.budget is None
    assert result.rejected_fields == {
        "city": "no_source_evidence",
        "budget": "no_source_evidence",
    }
    assert result.warnings == [
        "dropped city: no_source_evidence",
        "dropped budget: no_source_evidence",
    ]


def test_slot_validator_requires_an_exact_budget_number_in_the_same_clause() -> None:
    for unsupported in (30, 3000):
        result = validate_route_slots(
            RouteInput(latest_query="预算300元"),
            DatePlanSlots(),
            DatePlanSlots(budget=unsupported),
        )

        assert result.validated_slots.budget is None
        assert result.rejected_fields["budget"] == "no_source_evidence"

    separated = validate_route_slots(
        RouteInput(latest_query="预算还没决定，纪念日是500号"),
        DatePlanSlots(),
        DatePlanSlots(budget=500),
    )
    supported = validate_route_slots(
        RouteInput(latest_query="预算控制在500以内"),
        DatePlanSlots(),
        DatePlanSlots(budget=500),
    )

    assert separated.validated_slots.budget is None
    assert separated.rejected_fields["budget"] == "no_source_evidence"
    assert supported.validated_slots.budget == 500
    assert supported.field_sources["budget"] == "llm_verified"


def test_slot_validator_accepts_supported_field_without_accepting_peer_hallucination() -> None:
    result = validate_route_slots(
        RouteInput(latest_query="预算300元，地点你看着办"),
        DatePlanSlots(budget=300),
        DatePlanSlots(city="杭州", budget=300),
    )

    assert result.validated_slots.budget == 300
    assert result.field_sources["budget"] == "rule"
    assert result.rejected_fields["city"] == "no_source_evidence"


def test_slot_validator_requires_date_to_match_deterministic_parse() -> None:
    result = validate_route_slots(
        RouteInput(latest_query="周六下午"),
        DatePlanSlots(date=date(2026, 8, 8)),
        DatePlanSlots(date=date(2026, 8, 9)),
    )

    assert result.validated_slots.date is None
    assert result.rejected_fields["date"] == "no_source_evidence"


def test_slot_validator_rejects_partial_location_names_but_accepts_complete_names() -> None:
    partial = validate_route_slots(
        RouteInput(latest_query="城市是上海，区域是西湖区"),
        DatePlanSlots(),
        DatePlanSlots(city="海", area="湖区"),
    )
    complete = validate_route_slots(
        RouteInput(latest_query="城市是上海，区域是西湖区"),
        DatePlanSlots(),
        DatePlanSlots(city="上海", area="西湖区"),
    )

    assert partial.validated_slots.city is None
    assert partial.validated_slots.area is None
    assert partial.rejected_fields == {
        "city": "no_source_evidence",
        "area": "no_source_evidence",
    }
    assert complete.validated_slots.city == "上海"
    assert complete.validated_slots.area == "西湖区"


def test_replace_place_names_requires_current_text_without_alias_expansion() -> None:
    aliased = validate_route_slots(
        RouteInput(latest_query="把日本料理换掉"),
        DatePlanSlots(),
        DatePlanSlots(replace_place_names=["日料"]),
    )
    exact = validate_route_slots(
        RouteInput(latest_query="把日本料理换掉"),
        DatePlanSlots(),
        DatePlanSlots(replace_place_names=["日本料理"]),
    )

    assert aliased.validated_slots.replace_place_names == []
    assert aliased.rejected_fields == {
        "replace_place_names": "unsupported_values:日料"
    }
    assert exact.validated_slots.replace_place_names == ["日本料理"]

    task_only = validate_route_slots(
        RouteInput(latest_query="预算300元"),
        DatePlanSlots(),
        DatePlanSlots(replace_place_names=["旧餐厅"]),
        DatePlanSlots(replace_place_names=["旧餐厅"]),
    )
    assert task_only.validated_slots.replace_place_names == []
    assert task_only.rejected_fields["replace_place_names"] == "unsupported_values:旧餐厅"


def test_slot_validator_allows_resumable_task_history_but_not_unrelated_history() -> None:
    history = [
        StoredMessage(
            id="history-city",
            user_id="u1",
            relationship_id="r1",
            conversation_id="c1",
            role=MessageRole.USER,
            content="上次约会想在上海安排。",
        )
    ]
    task_state = DatePlanningTaskState(
        user_id="u1",
        relationship_id="r1",
        conversation_id="c1",
        status=DatePlanningStatus.COLLECTING,
        city="上海",
    )
    resumed = validate_route_slots(
        RouteInput(
            latest_query="预算300",
            recent_messages=history,
            date_task_state=task_state,
        ),
        DatePlanSlots(budget=300),
        DatePlanSlots(city="上海", budget=300),
        DatePlanSlots(city="上海"),
    )
    unrelated = validate_route_slots(
        RouteInput(latest_query="预算300", recent_messages=history),
        DatePlanSlots(budget=300),
        DatePlanSlots(city="上海", budget=300),
    )

    assert resumed.validated_slots.city == "上海"
    assert unrelated.validated_slots.city is None
    assert unrelated.rejected_fields["city"] == "no_source_evidence"


def test_slot_source_merge_keeps_rule_scalar_over_verified_llm() -> None:
    merged, accepted, sources = merge_route_slot_sources(
        DatePlanSlots(city="上海", budget=300),
        DatePlanSlots(city="杭州", budget=500, dining_keywords=["日料"]),
        DatePlanSlots(city="北京", budget=200),
    )

    assert merged.city == "上海"
    assert merged.budget == 300
    assert merged.dining_keywords == ["日料"]
    assert accepted["city"] == "上海"
    assert sources["city"] == "rule"


def test_slot_source_merge_normalizes_new_date_against_existing_duration() -> None:
    merged, accepted, sources = merge_route_slot_sources(
        DatePlanSlots(date=date(2026, 8, 20)),
        DatePlanSlots(),
        DatePlanSlots(
            date=date(2026, 8, 1),
            end_date=date(2026, 8, 3),
            day_count=3,
            nights=2,
        ),
    )

    assert merged.date == date(2026, 8, 20)
    assert merged.end_date == date(2026, 8, 22)
    assert merged.day_count == 3
    assert merged.end_date >= merged.date
    assert accepted["end_date"] == "2026-08-22"
    assert sources["end_date"].startswith("derived_from:")


def test_slot_source_merge_does_not_shorten_duration_with_stale_task_end_date() -> None:
    merged, _, _ = merge_route_slot_sources(
        DatePlanSlots(date=date(2026, 8, 3)),
        DatePlanSlots(),
        DatePlanSlots(
            date=date(2026, 8, 1),
            end_date=date(2026, 8, 5),
            day_count=5,
            nights=4,
        ),
    )

    assert merged.date == date(2026, 8, 3)
    assert merged.day_count == 5
    assert merged.end_date == date(2026, 8, 7)


def test_slot_source_merge_prefers_current_rule_range_over_stale_task_duration() -> None:
    merged, _, sources = merge_route_slot_sources(
        DatePlanSlots(
            date=date(2026, 8, 20),
            end_date=date(2026, 8, 21),
        ),
        DatePlanSlots(),
        DatePlanSlots(day_count=3, nights=2),
    )

    assert merged.date == date(2026, 8, 20)
    assert merged.end_date == date(2026, 8, 21)
    assert merged.day_count == 2
    assert merged.nights <= 1
    assert sources["day_count"].startswith("derived_from:")


def test_slot_source_merge_can_recover_duration_from_nights() -> None:
    merged, _, _ = merge_route_slot_sources(
        DatePlanSlots(date=date(2026, 8, 20)),
        DatePlanSlots(),
        DatePlanSlots(end_date=date(2026, 8, 18), nights=2),
    )

    assert merged.date == date(2026, 8, 20)
    assert merged.day_count == 3
    assert merged.end_date == date(2026, 8, 22)
    assert merged.nights == 2


def test_slot_source_merge_preserves_task_and_mixed_provenance() -> None:
    task_backed, _, task_sources = merge_route_slot_sources(
        DatePlanSlots(),
        DatePlanSlots(city="上海"),
        DatePlanSlots(city="上海"),
    )
    mixed, _, mixed_sources = merge_route_slot_sources(
        DatePlanSlots(dining_keywords=["日料"]),
        DatePlanSlots(dining_keywords=["咖啡"]),
        DatePlanSlots(dining_keywords=["火锅"]),
    )

    assert task_backed.city == "上海"
    assert task_sources["city"] == "task_state"
    assert mixed.dining_keywords == ["日料", "咖啡", "火锅"]
    assert mixed_sources["dining_keywords"] == "mixed:rule+llm_verified+task_state"


def test_route_corrector_parser_drops_only_malformed_slot_fields() -> None:
    correction, rejected = _parse_response_with_slot_rejections(
        '{"task_type":"date_planning","task_confidence":0.9,'
        '"date_plan":{"city":"上海","budget":"not-a-number"}}',
        "stop",
    )

    assert correction.task_type.value == "date_planning"
    assert correction.date_plan.city == "上海"
    assert correction.date_plan.budget is None
    assert rejected == {"budget": "invalid_schema"}

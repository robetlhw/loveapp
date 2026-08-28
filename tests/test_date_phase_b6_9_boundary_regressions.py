from datetime import datetime

from loveapp.application.date_planning.operation_resolution import DateOperationResolver
from loveapp.application.date_planning.requirements import DateRequirementMatcher
from loveapp.application.routing import extract_date_plan_slots
from loveapp.domain.date_operations import (
    DateOperationType,
    DatePlanOperation,
    DateRequirementUpdate,
    DateStopConstraints,
    DateStopRequirement,
    DesiredDateStop,
    RequirementReference,
    StopKind,
    StopReference,
)
from loveapp.domain.date_patch import DatePlanPatch
from loveapp.domain.date_plan import DatePlan, DatePlanItem, Place
from loveapp.domain.enums import PlaceCategory, RelationshipStage
from loveapp.domain.routing import RouteInput
from loveapp.domain.runtime_context import DatePlanRuntimeContext, RuntimeContext


def _requirement(requirement_id: str, keyword: str) -> DateStopRequirement:
    return DateStopRequirement(
        id=requirement_id,
        alternatives=[DesiredDateStop(kind=StopKind.ACTIVITY, keyword=keyword)],
    )


def _item(place_id: str, name: str, keyword: str, order: int) -> DatePlanItem:
    return DatePlanItem(
        order=order,
        place=Place(
            id=place_id,
            name=name,
            city="上海",
            address="测试地址",
            category=(
                PlaceCategory.ENTERTAINMENT
                if keyword == "电影院"
                else PlaceCategory.ATTRACTION
            ),
            tags=[keyword],
            search_keywords=[keyword],
            estimated_cost_per_person=100,
            source="test",
        ),
        duration_minutes=60,
        estimated_cost=100,
        reason="测试",
        slot_keyword=keyword,
    )


def _runtime(
    items: list[DatePlanItem],
    requirements: list[DateStopRequirement],
) -> RuntimeContext:
    plan = DatePlan(
        title="测试计划",
        summary="测试",
        items=items,
        total_estimated_cost=sum(item.estimated_cost for item in items),
        total_duration_minutes=sum(item.duration_minutes for item in items),
        data_source="test",
    )
    return RuntimeContext(
        user_id="user",
        relationship_id="relationship",
        conversation_id="conversation",
        relationship_stage=RelationshipStage.UNKNOWN,
        now=datetime(2026, 8, 28, 12, 0),
        active_date_plan=DatePlanRuntimeContext(
            current_plan=plan,
            requirements=requirements,
            requirement_satisfaction=DateRequirementMatcher().match(requirements, plan),
        ),
    )


def test_negated_one_of_cue_does_not_regroup_requirements() -> None:
    requirements = [
        _requirement("req-cinema", "电影院"),
        _requirement("req-pearl", "东方明珠"),
    ]
    runtime = _runtime(
        [
            _item("cinema", "测试电影院", "电影院", 1),
            _item("pearl", "东方明珠", "东方明珠", 2),
        ],
        requirements,
    )

    result = DateOperationResolver().resolve(
        "不要二选一，电影院和东方明珠都保留。",
        runtime,
        DatePlanPatch(),
    )

    assert not any(
        operation.type == DateOperationType.UPDATE_REQUIREMENT
        for operation in result.operations
    )


def test_regroup_targets_are_scoped_to_the_clause_with_the_one_of_cue() -> None:
    requirements = [
        _requirement("req-cinema", "电影院"),
        _requirement("req-pearl", "东方明珠"),
        _requirement("req-park", "公园"),
    ]
    runtime = _runtime(
        [
            _item("cinema", "测试电影院", "电影院", 1),
            _item("pearl", "东方明珠", "东方明珠", 2),
            _item("park", "测试公园", "公园", 3),
        ],
        requirements,
    )

    result = DateOperationResolver().resolve(
        "电影院和东方明珠二选一，公园继续保留。",
        runtime,
        DatePlanPatch(),
    )

    updates = [
        operation
        for operation in result.operations
        if operation.type == DateOperationType.UPDATE_REQUIREMENT
    ]
    assert len(updates) == 1
    assert updates[0].requirement_update is not None
    assert {
        target.requirement_id for target in updates[0].requirement_update.targets
    } == {"req-cinema", "req-pearl"}


def test_semantic_regroup_cannot_bind_targets_from_other_clauses() -> None:
    requirements = [
        _requirement("req-cinema", "电影院"),
        _requirement("req-pearl", "东方明珠"),
        _requirement("req-museum", "博物馆"),
        _requirement("req-aquarium", "海洋馆"),
    ]
    runtime = _runtime(
        [
            _item("cinema", "测试电影院", "电影院", 1),
            _item("pearl", "东方明珠", "东方明珠", 2),
            _item("museum", "测试博物馆", "博物馆", 3),
            _item("aquarium", "测试海洋馆", "海洋馆", 4),
        ],
        requirements,
    )
    text = "电影院保留，东方明珠也保留。博物馆和海洋馆二选一。"
    proposed = DatePlanOperation(
        type=DateOperationType.UPDATE_REQUIREMENT,
        requirement_update=DateRequirementUpdate(
            targets=[
                RequirementReference(
                    requirement_id=requirement.id,
                    stop_reference=StopReference(
                        keyword=requirement.alternatives[0].keyword,
                    ),
                )
                for requirement in requirements
            ],
        ),
        source_span=text,
    )

    result = DateOperationResolver().resolve(
        text,
        runtime,
        DatePlanPatch(),
        proposed_operations=[proposed],
    )

    updates = [
        operation
        for operation in result.operations
        if operation.type == DateOperationType.UPDATE_REQUIREMENT
    ]
    assert len(updates) == 1
    assert updates[0].requirement_update is not None
    assert {
        target.requirement_id for target in updates[0].requirement_update.targets
    } == {"req-museum", "req-aquarium"}
    assert any(
        rejected.reason == "requirement_targets_outside_regroup_clause"
        for rejected in result.rejected
    )


def test_regroup_prefers_specific_over_overlapping_requirement_name() -> None:
    requirements = [
        _requirement("req-generic-museum", "博物馆"),
        _requirement("req-shanghai-museum", "上海博物馆"),
        _requirement("req-aquarium", "海洋馆"),
    ]
    runtime = _runtime(
        [
            _item("generic-museum", "测试博物馆", "博物馆", 1),
            _item("shanghai-museum", "上海博物馆", "上海博物馆", 2),
            _item("aquarium", "测试海洋馆", "海洋馆", 3),
        ],
        requirements,
    )

    result = DateOperationResolver().resolve(
        "上海博物馆和海洋馆二选一。",
        runtime,
        DatePlanPatch(),
    )

    updates = [
        operation
        for operation in result.operations
        if operation.type == DateOperationType.UPDATE_REQUIREMENT
    ]
    assert len(updates) == 1
    assert updates[0].requirement_update is not None
    assert {
        target.requirement_id for target in updates[0].requirement_update.targets
    } == {"req-shanghai-museum", "req-aquarium"}


def test_unsatisfied_regroup_target_does_not_also_become_mandatory_add() -> None:
    requirements = [
        _requirement("req-cinema", "电影院"),
        _requirement("req-park", "公园"),
    ]
    runtime = _runtime(
        [_item("cinema", "测试电影院", "电影院", 1)],
        requirements,
    )

    result = DateOperationResolver().resolve(
        "电影院和公园二选一吧。",
        runtime,
        DatePlanPatch(),
    )

    assert [operation.type for operation in result.operations] == [
        DateOperationType.UPDATE_REQUIREMENT
    ]


def test_semantic_dedupe_keeps_a_specific_replacement_specific() -> None:
    runtime = _runtime(
        [_item("pearl", "东方明珠", "景点", 1)],
        [_requirement("req-attraction", "景点")],
    )
    proposed = DatePlanOperation(
        type=DateOperationType.REPLACE_STOP,
        target=StopReference(keyword="景点"),
        payload=DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword="博物馆",
            generic_replacement=True,
        ),
        source_span="把景点改成博物馆",
    )

    result = DateOperationResolver().resolve(
        "把景点改成博物馆。",
        runtime,
        DatePlanPatch(),
        proposed_operations=[proposed],
    )

    assert len(result.operations) == 1
    replacement = result.operations[0]
    assert replacement.type == DateOperationType.REPLACE_STOP
    assert replacement.payload is not None
    assert replacement.payload.keyword == "博物馆"
    assert replacement.payload.generic_replacement is False


def test_semantic_regroup_cannot_bypass_a_negated_one_of_cue() -> None:
    requirements = [
        _requirement("req-cinema", "电影院"),
        _requirement("req-pearl", "东方明珠"),
    ]
    runtime = _runtime(
        [
            _item("cinema", "测试电影院", "电影院", 1),
            _item("pearl", "东方明珠", "东方明珠", 2),
        ],
        requirements,
    )
    text = "不要二选一，电影院和东方明珠都保留。"
    proposed = DatePlanOperation(
        type=DateOperationType.UPDATE_REQUIREMENT,
        requirement_update=DateRequirementUpdate(
            targets=[
                RequirementReference(
                    requirement_id=requirement.id,
                    stop_reference=StopReference(
                        keyword=requirement.alternatives[0].keyword,
                    ),
                )
                for requirement in requirements
            ],
        ),
        source_span=text,
    )

    result = DateOperationResolver().resolve(
        text,
        runtime,
        DatePlanPatch(),
        proposed_operations=[proposed],
    )

    assert result.operations == ()
    assert any(item.reason == "requirement_update_negated" for item in result.rejected)


def test_semantic_one_of_cannot_authorize_two_of_two_cardinality() -> None:
    requirements = [
        _requirement("req-cinema", "电影院"),
        _requirement("req-pearl", "东方明珠"),
    ]
    runtime = _runtime(
        [
            _item("cinema", "测试电影院", "电影院", 1),
            _item("pearl", "东方明珠", "东方明珠", 2),
        ],
        requirements,
    )
    text = "电影院和东方明珠二选一。"
    proposed = DatePlanOperation(
        type=DateOperationType.UPDATE_REQUIREMENT,
        requirement_update=DateRequirementUpdate(
            targets=[
                RequirementReference(
                    requirement_id=requirement.id,
                    stop_reference=StopReference(
                        keyword=requirement.alternatives[0].keyword,
                    ),
                )
                for requirement in requirements
            ],
            min_satisfied=2,
            max_satisfied=2,
        ),
        source_span=text,
    )

    result = DateOperationResolver().resolve(
        text,
        runtime,
        DatePlanPatch(),
        proposed_operations=[proposed],
    )

    updates = [
        operation
        for operation in result.operations
        if operation.type == DateOperationType.UPDATE_REQUIREMENT
    ]
    assert len(updates) == 1
    assert updates[0].requirement_update is not None
    assert updates[0].requirement_update.min_satisfied == 1
    assert updates[0].requirement_update.max_satisfied == 1
    assert any(
        item.reason == "requirement_cardinality_without_source_evidence"
        for item in result.rejected
    )


def test_specific_rule_add_merges_with_semantic_stop_constraints() -> None:
    text = "晚餐改成一家人均500元以内、评分4.9以上、陆家嘴附近的法餐。"
    semantic = DatePlanOperation(
        type=DateOperationType.ADD_STOP,
        payload=DesiredDateStop(
            kind=StopKind.DINING,
            meal_type="dinner",
            constraints=DateStopConstraints(
                max_cost_per_person=500,
                min_rating=4.9,
                preferred_area="陆家嘴",
            ),
        ),
        source_span=text,
    )

    result = DateOperationResolver().resolve(
        text,
        None,
        DatePlanPatch(
            dining_keywords=["法餐"],
            meal_keywords={"dinner": ["法餐"]},
        ),
        proposed_operations=[semantic],
    )

    additions = [
        operation
        for operation in result.operations
        if operation.type == DateOperationType.ADD_STOP
    ]
    assert len(additions) == 1
    assert additions[0].payload is not None
    assert additions[0].payload.keyword == "法餐"
    assert additions[0].payload.constraints == semantic.payload.constraints


def test_folded_temporal_add_keeps_literal_source_evidence() -> None:
    text = "晚饭后再加一个电影院。"
    patch = DatePlanPatch.model_validate(
        extract_date_plan_slots(RouteInput(latest_query=text)).model_dump()
    )

    result = DateOperationResolver().resolve(text, None, patch)

    assert len(result.operations) == 1
    addition = result.operations[0]
    assert addition.type == DateOperationType.ADD_STOP
    assert addition.source_span == "晚饭后再加一个电影院"
    assert addition.payload is not None
    assert addition.payload.keyword == "电影院"
    assert addition.payload.after == "dinner"
    assert not any(
        rejected.reason == "source_span_not_in_current_turn"
        for rejected in result.rejected
    )

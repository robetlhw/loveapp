from datetime import datetime

from loveapp.application.date_planning.operation_resolution import (
    DateOperationResolver,
    deterministic_date_parse_is_complete,
)
from loveapp.application.date_planning.operation_validation import DateOperationVerifier
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


def test_negated_replace_cue_does_not_become_a_deterministic_remove() -> None:
    result = DateOperationResolver().resolve(
        "电影不要换，只移到晚饭后。",
        _runtime(
            [_item("cinema", "测试电影院", "电影院", 1)],
            [_requirement("req-cinema", "电影院")],
        ),
        DatePlanPatch(),
    )

    assert not any(
        operation.type == DateOperationType.REMOVE_STOP
        for operation in result.operations
    )


def test_explicit_do_not_want_phrase_remains_a_remove() -> None:
    result = DateOperationResolver().resolve(
        "电影不要了，其他安排不变。",
        _runtime(
            [_item("cinema", "测试电影院", "电影院", 1)],
            [_requirement("req-cinema", "电影院")],
        ),
        DatePlanPatch(),
    )

    assert [operation.type for operation in result.operations] == [
        DateOperationType.REMOVE_STOP
    ]


def test_explicit_do_not_retain_phrase_is_a_remove() -> None:
    result = DateOperationResolver().resolve(
        "电影不要保留，其他安排不变。",
        _runtime(
            [_item("cinema", "测试电影院", "电影院", 1)],
            [_requirement("req-cinema", "电影院")],
        ),
        DatePlanPatch(),
    )

    assert [operation.type for operation in result.operations] == [
        DateOperationType.REMOVE_STOP
    ]


def test_preservation_boundary_scopes_remove_to_its_target() -> None:
    runtime = _runtime(
        [
            _item("cinema", "测试电影院", "电影院", 1),
            _item("park", "测试公园", "公园", 2),
        ],
        [
            _requirement("req-cinema", "电影院"),
            _requirement("req-park", "公园"),
        ],
    )

    for text in ("删掉电影但保留公园。", "删掉电影并保留公园。"):
        deterministic = DateOperationResolver().resolve(
            text,
            runtime,
            DatePlanPatch(),
        )
        assert [
            operation.target.keyword
            for operation in deterministic.operations
            if operation.type == DateOperationType.REMOVE_STOP
            and operation.target is not None
        ] == ["电影院"]

        proposed_park_remove = DatePlanOperation(
            type=DateOperationType.REMOVE_STOP,
            target=StopReference(keyword="公园"),
            source_span=text.removesuffix("。"),
        )
        semantic = DateOperationResolver().resolve(
            text,
            runtime,
            DatePlanPatch(),
            proposed_operations=[proposed_park_remove],
        )

        assert not any(
            operation.type == DateOperationType.REMOVE_STOP
            and operation.target is not None
            and operation.target.keyword == "公园"
            for operation in semantic.operations
        )
        assert any(
            rejected.operation == proposed_park_remove
            and rejected.reason == "remove_without_explicit_cue"
            for rejected in semantic.rejected
        )


def test_postfix_remove_cue_does_not_cross_another_target() -> None:
    text = "电影保留但公园不要了。"
    runtime = _runtime(
        [
            _item("cinema", "测试电影院", "电影院", 1),
            _item("park", "测试公园", "公园", 2),
        ],
        [
            _requirement("req-cinema", "电影院"),
            _requirement("req-park", "公园"),
        ],
    )
    proposed_cinema_remove = DatePlanOperation(
        type=DateOperationType.REMOVE_STOP,
        target=StopReference(keyword="电影院"),
        source_span=text.removesuffix("。"),
    )

    result = DateOperationResolver().resolve(
        text,
        runtime,
        DatePlanPatch(),
        proposed_operations=[proposed_cinema_remove],
    )

    assert [
        operation.target.keyword
        for operation in result.operations
        if operation.type == DateOperationType.REMOVE_STOP
        and operation.target is not None
    ] == ["公园"]
    assert any(
        rejected.operation == proposed_cinema_remove
        and rejected.reason == "remove_without_explicit_cue"
        for rejected in result.rejected
    )


def test_remove_list_scope_rejects_non_list_semantic_residue() -> None:
    runtime = _runtime(
        [
            _item("cinema", "测试电影院", "电影院", 1),
            _item("park", "测试公园", "公园", 2),
        ],
        [
            _requirement("req-cinema", "电影院"),
            _requirement("req-park", "公园"),
        ],
    )
    for text in ("电影还有安排，公园删掉。", "电影还有安排，公园不要了。"):
        proposed_cinema_remove = DatePlanOperation(
            type=DateOperationType.REMOVE_STOP,
            target=StopReference(keyword="电影院"),
            source_span=text.removesuffix("。"),
        )

        result = DateOperationResolver().resolve(
            text,
            runtime,
            DatePlanPatch(),
            proposed_operations=[proposed_cinema_remove],
        )

        assert [
            operation.target.keyword
            for operation in result.operations
            if operation.type == DateOperationType.REMOVE_STOP
            and operation.target is not None
        ] == ["公园"]
        assert any(
            rejected.operation == proposed_cinema_remove
            and rejected.reason == "remove_without_explicit_cue"
            for rejected in result.rejected
        )


def test_plural_remove_cue_targets_all_listed_stops() -> None:
    runtime = _runtime(
        [
            _item("cinema", "测试电影院", "电影院", 1),
            _item("park", "测试公园", "公园", 2),
        ],
        [
            _requirement("req-cinema", "电影院"),
            _requirement("req-park", "公园"),
        ],
    )
    for text in (
        "电影和公园都删掉。",
        "电影和公园删掉。",
        "电影和公园不要了。",
        "删掉电影和公园。",
    ):
        proposed = [
            DatePlanOperation(
                type=DateOperationType.REMOVE_STOP,
                target=StopReference(keyword=keyword),
                source_span=text.removesuffix("。"),
            )
            for keyword in ("电影院", "公园")
        ]

        deterministic = DateOperationResolver().resolve(
            text,
            runtime,
            DatePlanPatch(),
        )
        assert {
            operation.target.keyword
            for operation in deterministic.operations
            if operation.type == DateOperationType.REMOVE_STOP
            and operation.target is not None
        } == {"电影院", "公园"}

        verification = DateOperationVerifier().verify(
            proposed,
            text,
            runtime,
            DatePlanPatch(),
        )
        assert verification.accepted == tuple(proposed)
        assert not verification.rejected


def test_explicit_do_not_want_remove_is_authorized_for_semantic_operation() -> None:
    runtime = _runtime(
        [_item("cinema", "测试电影院", "电影院", 1)],
        [_requirement("req-cinema", "电影院")],
    )

    for text in ("电影不要了。", "电影不要保留。", "不要保留电影。"):
        proposed = DatePlanOperation(
            type=DateOperationType.REMOVE_STOP,
            target=StopReference(keyword="电影院"),
            source_span=text.removesuffix("。"),
        )
        verification = DateOperationVerifier().verify(
            [proposed],
            text,
            runtime,
            DatePlanPatch(),
        )

        assert verification.accepted == (proposed,)
        assert not verification.rejected

        deterministic = DateOperationResolver().resolve(
            text,
            runtime,
            DatePlanPatch(),
        )
        assert any(
            operation.type == DateOperationType.REMOVE_STOP
            and operation.target is not None
            and operation.target.keyword == "电影院"
            for operation in deterministic.operations
        )


def test_do_not_schedule_too_late_does_not_remove_the_stop() -> None:
    runtime = _runtime(
        [_item("cinema", "测试电影院", "电影院", 1)],
        [_requirement("req-cinema", "电影院")],
    )

    for text in (
        "电影不要太晚。",
        "电影不要安排太晚。",
        "不要电影太晚。",
        "不要电影安排得太晚。",
    ):
        proposed = DatePlanOperation(
            type=DateOperationType.REMOVE_STOP,
            target=StopReference(keyword="电影院"),
            source_span=text.removesuffix("。"),
        )
        result = DateOperationResolver().resolve(
            text,
            runtime,
            DatePlanPatch(),
            proposed_operations=[proposed],
        )

        assert not any(
            operation.type == DateOperationType.REMOVE_STOP
            for operation in result.operations
        )


def test_semantic_remove_requires_clause_local_positive_evidence() -> None:
    text = "晚餐不要换，电影也别删，只把电影调到晚饭后。"
    proposed_remove = DatePlanOperation(
        type=DateOperationType.REMOVE_STOP,
        target=StopReference(keyword="电影院"),
        source_span="电影也别删",
    )

    result = DateOperationResolver().resolve(
        text,
        _runtime(
            [_item("cinema", "测试电影院", "电影院", 1)],
            [_requirement("req-cinema", "电影院")],
        ),
        DatePlanPatch(),
        proposed_operations=[proposed_remove],
    )

    assert not any(
        operation.type == DateOperationType.REMOVE_STOP
        for operation in result.operations
    )
    assert any(
        rejected.operation == proposed_remove
        and rejected.reason == "remove_negated_in_source"
        for rejected in result.rejected
    )


def test_semantic_move_rejects_negated_operation_local_evidence() -> None:
    text = "不要把电影放到晚饭后。"
    proposed_move = DatePlanOperation(
        type=DateOperationType.MOVE_STOP,
        target=StopReference(keyword="电影院"),
        payload=DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword="电影院",
            after="dinner",
        ),
        source_span="不要把电影放到晚饭后",
    )

    result = DateOperationResolver().resolve(
        text,
        _runtime(
            [_item("cinema", "测试电影院", "电影院", 1)],
            [_requirement("req-cinema", "电影院")],
        ),
        DatePlanPatch(),
        proposed_operations=[proposed_move],
    )

    assert not any(
        operation.type == DateOperationType.MOVE_STOP
        for operation in result.operations
    )
    assert any(
        rejected.operation.type == DateOperationType.MOVE_STOP
        and rejected.reason == "move_negated_in_source"
        for rejected in result.rejected
    )


def test_bare_negation_rejects_destructive_semantic_operations() -> None:
    cases = [
        (
            "电影不删除。",
            DatePlanOperation(
                type=DateOperationType.REMOVE_STOP,
                target=StopReference(keyword="电影院"),
                source_span="电影不删除",
            ),
            "remove_negated_in_source",
        ),
        (
            "电影不换成景点。",
            DatePlanOperation(
                type=DateOperationType.REPLACE_STOP,
                target=StopReference(keyword="电影院"),
                payload=DesiredDateStop(kind=StopKind.ACTIVITY, keyword="景点"),
                source_span="电影不换成景点",
            ),
            "replace_negated_in_source",
        ),
        (
            "电影不放到晚饭后。",
            DatePlanOperation(
                type=DateOperationType.MOVE_STOP,
                target=StopReference(keyword="电影院"),
                payload=DesiredDateStop(
                    kind=StopKind.ACTIVITY,
                    keyword="电影院",
                    after="dinner",
                ),
                source_span="电影不放到晚饭后",
            ),
            "move_negated_in_source",
        ),
    ]
    runtime = _runtime(
        [_item("cinema", "测试电影院", "电影院", 1)],
        [_requirement("req-cinema", "电影院")],
    )

    for text, proposed, reason in cases:
        result = DateOperationResolver().resolve(
            text,
            runtime,
            DatePlanPatch(),
            proposed_operations=[proposed],
        )

        assert proposed not in result.operations
        assert any(
            rejected.operation.type == proposed.type
            and rejected.operation.source_span == proposed.source_span
            and rejected.reason == reason
            for rejected in result.rejected
        )


def test_semantic_constraints_cannot_borrow_cross_clause_evidence() -> None:
    base_dinner = _item("dinner", "测试晚餐", "法餐", 1)
    dinner = base_dinner.model_copy(
        update={
            "place": base_dinner.place.model_copy(
                update={"category": PlaceCategory.RESTAURANT}
            ),
            "meal_type": "dinner",
            "slot_keyword": "法餐",
            "time_label": "晚餐",
        }
    )
    text = "晚餐改成法餐，咖啡人均100元以内。"
    proposed = DatePlanOperation(
        type=DateOperationType.REPLACE_STOP,
        target=StopReference(place_id="dinner"),
        payload=DesiredDateStop(
            kind=StopKind.DINING,
            keyword="法餐",
            meal_type="dinner",
            constraints=DateStopConstraints(max_cost_per_person=100),
        ),
        source_span="咖啡人均100元以内",
    )

    result = DateOperationResolver().resolve(
        text,
        _runtime([dinner], []),
        DatePlanPatch(
            dining_keywords=["法餐"],
            meal_keywords={"dinner": ["法餐"]},
        ),
        proposed_operations=[proposed],
    )

    replacements = [
        operation
        for operation in result.operations
        if operation.type == DateOperationType.REPLACE_STOP
    ]
    assert len(replacements) == 1
    assert replacements[0].payload is not None
    assert replacements[0].payload.constraints is None
    assert any(
        rejected.operation == proposed
        and rejected.reason == "replace_without_explicit_cue"
        for rejected in result.rejected
    )


def test_stop_local_constraint_completeness_is_clause_scoped() -> None:
    text = "晚餐人均500元以内的法餐，咖啡人均100元以内。"
    dinner = DatePlanOperation(
        type=DateOperationType.ADD_STOP,
        payload=DesiredDateStop(
            kind=StopKind.DINING,
            keyword="法餐",
            meal_type="dinner",
            constraints=DateStopConstraints(max_cost_per_person=500),
        ),
        source_span="晚餐人均500元以内的法餐",
    )

    result = DateOperationResolver().resolve(
        text,
        None,
        DatePlanPatch(),
        proposed_operations=[dinner],
    )

    assert any(
        operation.type == DateOperationType.ADD_STOP
        and operation.source_span == dinner.source_span
        and operation.payload is not None
        and operation.payload.constraints == dinner.payload.constraints
        for operation in result.operations
    )
    assert not deterministic_date_parse_is_complete(text, None, result)


def test_stop_local_constraint_completeness_counts_same_clause_occurrences() -> None:
    text = "晚餐人均500元以内的法餐和咖啡人均100元以内。"
    dinner = DatePlanOperation(
        type=DateOperationType.ADD_STOP,
        payload=DesiredDateStop(
            kind=StopKind.DINING,
            keyword="法餐",
            meal_type="dinner",
            constraints=DateStopConstraints(max_cost_per_person=500),
        ),
        source_span="晚餐人均500元以内的法餐",
    )

    result = DateOperationResolver().resolve(
        text,
        None,
        DatePlanPatch(),
        proposed_operations=[dinner],
    )

    assert not deterministic_date_parse_is_complete(text, None, result)


def test_stop_local_constraints_reject_whole_query_source_span() -> None:
    text = "晚餐改成法餐，咖啡人均100元以内。"
    proposed = DatePlanOperation(
        type=DateOperationType.REPLACE_STOP,
        target=StopReference(place_id="dinner"),
        payload=DesiredDateStop(
            kind=StopKind.DINING,
            keyword="法餐",
            meal_type="dinner",
            constraints=DateStopConstraints(max_cost_per_person=100),
        ),
        source_span=text,
    )
    base_dinner = _item("dinner", "测试晚餐", "法餐", 1)
    dinner = base_dinner.model_copy(
        update={
            "place": base_dinner.place.model_copy(
                update={"category": PlaceCategory.RESTAURANT}
            ),
            "meal_type": "dinner",
        }
    )

    result = DateOperationResolver().resolve(
        text,
        _runtime([dinner], []),
        DatePlanPatch(),
        proposed_operations=[proposed],
    )

    assert proposed not in result.operations
    assert any(
        rejected.operation.type == DateOperationType.REPLACE_STOP
        and rejected.operation.source_span == text
        and rejected.reason == "stop_local_constraint_source_not_clause_local"
        for rejected in result.rejected
    )


def test_stop_local_constraints_reject_multi_target_clause_source_span() -> None:
    text = "晚餐人均500元以内的法餐和咖啡人均100元以内。"
    proposed = DatePlanOperation(
        type=DateOperationType.ADD_STOP,
        payload=DesiredDateStop(
            kind=StopKind.DINING,
            keyword="法餐",
            meal_type="dinner",
            constraints=DateStopConstraints(max_cost_per_person=500),
        ),
        source_span=text,
    )

    result = DateOperationResolver().resolve(
        text,
        None,
        DatePlanPatch(),
        proposed_operations=[proposed],
    )

    assert proposed not in result.operations
    assert any(
        rejected.operation.type == DateOperationType.ADD_STOP
        and rejected.operation.source_span == text
        and rejected.reason == "stop_local_constraint_source_ambiguous"
        for rejected in result.rejected
    )


def test_stop_local_constraints_cannot_swap_values_across_targets() -> None:
    text = "晚餐人均500元以内的法餐和咖啡评分4.9以上。"
    operations = [
        DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=DesiredDateStop(
                kind=StopKind.DINING,
                keyword="法餐",
                meal_type="dinner",
                constraints=DateStopConstraints(
                    max_cost_per_person=500,
                    min_rating=4.9,
                ),
            ),
            source_span=text,
        ),
        DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=DesiredDateStop(
                kind=StopKind.CAFE,
                keyword="咖啡",
                constraints=DateStopConstraints(
                    max_cost_per_person=500,
                    min_rating=4.9,
                ),
            ),
            source_span=text,
        ),
    ]

    result = DateOperationResolver().resolve(
        text,
        None,
        DatePlanPatch(),
        proposed_operations=operations,
    )

    assert all(operation not in result.operations for operation in operations)
    assert sum(
        rejected.reason == "stop_local_constraint_source_ambiguous"
        for rejected in result.rejected
    ) >= 2
    assert not deterministic_date_parse_is_complete(text, None, result)


def test_multi_target_constraint_source_is_connector_independent() -> None:
    for connector in ("与", "跟", "加上", "、", "/"):
        text = f"晚餐人均500元以内的法餐{connector}咖啡评分4.9以上。"
        proposed = DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=DesiredDateStop(
                kind=StopKind.DINING,
                keyword="法餐",
                meal_type="dinner",
                constraints=DateStopConstraints(
                    max_cost_per_person=500,
                    min_rating=4.9,
                ),
            ),
            source_span=text.removesuffix("。"),
        )

        result = DateOperationResolver().resolve(
            text,
            None,
            DatePlanPatch(),
            proposed_operations=[proposed],
        )

        assert proposed not in result.operations
        assert any(
            rejected.operation == proposed
            and rejected.reason == "stop_local_constraint_source_ambiguous"
            for rejected in result.rejected
        )


def test_named_stops_cannot_share_generic_constraint_target_evidence() -> None:
    cases = (
        ("海棠餐厅", "晚餐海棠餐厅人均500元以内与明月餐厅评分4.9以上。"),
        ("海棠饭店", "晚餐海棠饭店人均500元以内与明月餐厅评分4.9以上。"),
        ("海棠轩", "晚餐海棠轩人均500元以内与明月楼评分4.9以上。"),
    )
    for place_name, text in cases:
        proposed = DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=DesiredDateStop(
                kind=StopKind.DINING,
                place_name=place_name,
                meal_type="dinner",
                constraints=DateStopConstraints(
                    max_cost_per_person=500,
                    min_rating=4.9,
                ),
            ),
            source_span=text.removesuffix("。"),
        )

        result = DateOperationResolver().resolve(
            text,
            None,
            DatePlanPatch(),
            proposed_operations=[proposed],
        )

        assert proposed not in result.operations
        assert any(
            rejected.operation == proposed
            and rejected.reason == "stop_local_constraint_source_ambiguous"
            for rejected in result.rejected
        )
        assert not deterministic_date_parse_is_complete(text, None, result)


def test_single_constraint_binds_to_its_local_named_stop() -> None:
    cases = (
        ("海棠餐厅", "明月餐厅", "晚餐海棠餐厅人均500元以内与明月餐厅。"),
        ("海棠轩", "明月楼", "晚餐海棠轩人均500元以内与明月楼。"),
    )
    for local_name, remote_name, text in cases:
        local = DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=DesiredDateStop(
                kind=StopKind.DINING,
                place_name=local_name,
                meal_type="dinner",
                constraints=DateStopConstraints(max_cost_per_person=500),
            ),
            source_span=text.removesuffix("。"),
        )
        remote = local.model_copy(
            update={
                "payload": local.payload.model_copy(
                    update={"place_name": remote_name}
                )
            }
        )

        result = DateOperationResolver().resolve(
            text,
            None,
            DatePlanPatch(),
            proposed_operations=[local, remote],
        )

        assert local in result.operations
        assert remote not in result.operations
        assert any(
            rejected.operation == remote
            and rejected.reason == "stop_local_constraint_source_ambiguous"
            for rejected in result.rejected
        )

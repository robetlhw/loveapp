from datetime import datetime

from loveapp.application.date_planning.operation_resolution import DateOperationResolver
from loveapp.application.routing import route_by_rules
from loveapp.domain.date_operations import (
    DateConstraintField,
    DateOperationType,
    DatePlanOperation,
    DesiredDateStop,
    StopKind,
    TemporalAnchor,
)
from loveapp.domain.date_patch import DatePlanPatch
from loveapp.domain.date_plan import DatePlan, DatePlanItem, Place
from loveapp.domain.enums import PlaceCategory, RelationshipStage, TaskType
from loveapp.domain.routing import RouteInput
from loveapp.domain.runtime_context import DatePlanRuntimeContext, RuntimeContext


def _resolve(
    text: str,
    patch: DatePlanPatch | None = None,
    *,
    runtime_context: RuntimeContext | None = None,
    proposed_operations: list[DatePlanOperation] | None = None,
):
    return DateOperationResolver().resolve(
        text,
        runtime_context,
        patch or DatePlanPatch(),
        proposed_operations=proposed_operations,
    )


def _place(place_id: str, name: str, category: PlaceCategory, *tags: str) -> Place:
    return Place(
        id=place_id,
        name=name,
        city="上海",
        address="测试地址",
        category=category,
        tags=list(tags),
        estimated_cost_per_person=100,
        source="test",
    )


def _runtime_with_movie_and_dinner(
    *,
    movie_order: int = 1,
    dinner_order: int = 2,
) -> RuntimeContext:
    movie = DatePlanItem(
        order=movie_order,
        place=_place("movie", "测试电影院", PlaceCategory.ENTERTAINMENT, "电影"),
        duration_minutes=90,
        estimated_cost=100,
        reason="看电影",
        slot_keyword="电影院",
    )
    dinner = DatePlanItem(
        order=dinner_order,
        place=_place("dinner", "测试餐厅", PlaceCategory.RESTAURANT, "晚餐"),
        duration_minutes=90,
        estimated_cost=200,
        reason="晚餐",
        meal_type="dinner",
        slot_keyword="西餐",
    )
    return _runtime_with_plan(movie, dinner)


def _runtime_with_plan(*items: DatePlanItem) -> RuntimeContext:
    return RuntimeContext(
        user_id="user",
        relationship_id="relationship",
        conversation_id="conversation",
        relationship_stage=RelationshipStage.UNKNOWN,
        now=datetime(2026, 8, 27, 12, 0),
        active_date_plan=DatePlanRuntimeContext(
            current_plan=DatePlan(
                title="现有计划",
                summary="电影在晚餐前",
                items=sorted(items, key=lambda item: (item.day_index, item.order)),
                total_estimated_cost=sum(item.estimated_cost for item in items),
                total_duration_minutes=sum(item.duration_minutes for item in items),
                data_source="test",
            )
        ),
    )


def test_constraint_fields_resolve_to_update_operations() -> None:
    budget = _resolve("预算改为1000", DatePlanPatch(budget=1000))
    area = _resolve("区域改为徐汇区", DatePlanPatch(area="徐汇区"))

    assert budget.operations[0].type == DateOperationType.UPDATE_CONSTRAINT
    assert budget.operations[0].constraint_field == DateConstraintField.BUDGET
    assert budget.operations[0].constraint_value == 1000
    assert area.operations[0].type == DateOperationType.UPDATE_CONSTRAINT
    assert area.operations[0].constraint_field == DateConstraintField.AREA
    assert area.operations[0].constraint_value == "徐汇区"


def test_replacement_resolves_target_and_payload() -> None:
    result = _resolve("把火锅改成西餐")

    assert len(result.operations) == 1
    operation = result.operations[0]
    assert operation.type == DateOperationType.REPLACE_STOP
    assert operation.target is not None
    assert operation.target.keyword == "火锅"
    assert operation.payload is not None
    assert operation.payload.kind == StopKind.DINING
    assert operation.payload.keyword == "西餐"


def test_meal_and_temporal_stop_requests_resolve_to_typed_adds() -> None:
    lunch = _resolve("中午吃烧烤")
    movie = _resolve("晚饭后看电影")

    lunch_stop = lunch.operations[0].payload
    assert lunch.operations[0].type == DateOperationType.ADD_STOP
    assert lunch_stop is not None
    assert lunch_stop.keyword == "烧烤"
    assert lunch_stop.meal_type is not None
    assert lunch_stop.meal_type.value == "lunch"
    movie_stop = movie.operations[0].payload
    assert movie.operations[0].type == DateOperationType.ADD_STOP
    assert movie_stop is not None
    assert movie_stop.keyword == "电影院"
    assert movie_stop.after == TemporalAnchor.DINNER


def test_existing_misplaced_stop_resolves_to_move() -> None:
    result = _resolve(
        "晚饭后看电影",
        runtime_context=_runtime_with_movie_and_dinner(),
    )

    assert len(result.operations) == 1
    operation = result.operations[0]
    assert operation.type == DateOperationType.MOVE_STOP
    assert operation.target is not None
    assert operation.target.place_id == "movie"
    assert operation.payload is not None
    assert operation.payload.after == TemporalAnchor.DINNER


def test_existing_correctly_placed_stop_resolves_to_noop() -> None:
    result = _resolve(
        "晚饭后看电影",
        runtime_context=_runtime_with_movie_and_dinner(movie_order=2, dinner_order=1),
    )

    assert result.operations == ()


def test_compound_request_preserves_independent_operations() -> None:
    result = _resolve(
        "预算改到600，加一顿烧烤午饭，晚饭后看电影",
        DatePlanPatch(budget=600),
    )

    assert [operation.type for operation in result.operations] == [
        DateOperationType.UPDATE_CONSTRAINT,
        DateOperationType.ADD_STOP,
        DateOperationType.ADD_STOP,
    ]
    assert result.operations[0].constraint_value == 600
    assert result.operations[1].payload is not None
    assert result.operations[1].payload.keyword == "烧烤"
    assert result.operations[2].payload is not None
    assert result.operations[2].payload.keyword == "电影院"


def test_verified_patch_keywords_outside_rule_vocabulary_become_operations() -> None:
    result = _resolve(
        "晚上吃海底捞，中午吃韩国料理",
        DatePlanPatch(
            dining_keywords=["韩国料理", "海底捞"],
            meal_keywords={"lunch": ["韩国料理"], "dinner": ["海底捞"]},
        ),
    )

    assert [operation.type for operation in result.operations] == [
        DateOperationType.ADD_STOP,
        DateOperationType.ADD_STOP,
    ]
    assert [operation.payload.keyword for operation in result.operations if operation.payload] == [
        "韩国料理",
        "海底捞",
    ]


def test_named_replacement_and_additional_meal_stay_separate() -> None:
    park = DatePlanItem(
        order=1,
        place=_place("park", "测试公园", PlaceCategory.ATTRACTION, "公园"),
        duration_minutes=60,
        estimated_cost=50,
        reason="公园",
        time_label="下午",
        slot_keyword="公园",
    )
    korean = DatePlanItem(
        order=2,
        place=_place(
            "korean",
            "测试韩国料理",
            PlaceCategory.RESTAURANT,
            "韩国料理",
        ),
        duration_minutes=60,
        estimated_cost=100,
        reason="午餐",
        meal_type="lunch",
        slot_keyword="韩国料理",
    )
    result = _resolve(
        "晚上吃海底捞，中午吃韩国料理，然后下午不去测试公园，换一个博物馆",
        DatePlanPatch(
            dining_keywords=["韩国料理", "海底捞"],
            meal_keywords={"lunch": ["韩国料理"], "dinner": ["海底捞"]},
            activity_keywords=["博物馆"],
            replace_place_names=["测试公园"],
        ),
        runtime_context=_runtime_with_plan(park, korean),
    )

    assert [operation.type for operation in result.operations] == [
        DateOperationType.REPLACE_STOP,
        DateOperationType.ADD_STOP,
    ]
    replacement, addition = result.operations
    assert replacement.target is not None
    assert replacement.target.place_id == "park"
    assert replacement.payload is not None
    assert replacement.payload.keyword == "博物馆"
    assert addition.payload is not None
    assert addition.payload.keyword == "海底捞"


def test_unique_contextual_activity_can_be_replaced_on_target_day() -> None:
    activity = DatePlanItem(
        order=1,
        day_index=2,
        place=_place("activity", "原活动", PlaceCategory.ATTRACTION, "景点"),
        duration_minutes=60,
        estimated_cost=50,
        reason="activity",
        slot_keyword="景点",
    )
    lunch = DatePlanItem(
        order=2,
        day_index=2,
        place=_place("lunch", "午餐", PlaceCategory.RESTAURANT, "午餐"),
        duration_minutes=60,
        estimated_cost=100,
        reason="lunch",
        meal_type="lunch",
        slot_keyword="午餐",
    )
    result = _resolve(
        "第二天下午不去原来的活动了，换成公园",
        DatePlanPatch(activity_keywords=["公园"], target_day=2),
        runtime_context=_runtime_with_plan(activity, lunch),
    )

    assert len(result.operations) == 1
    operation = result.operations[0]
    assert operation.type == DateOperationType.REPLACE_STOP
    assert operation.target is not None
    assert operation.target.place_id == "activity"
    assert operation.payload is not None
    assert operation.payload.keyword == "公园"


def test_rule_route_exposes_patch_and_typed_compound_operations() -> None:
    result = route_by_rules(
        RouteInput(
            latest_query="预算改到600，加一顿烧烤午饭，晚饭后看电影",
            forced_task=TaskType.DATE_PLANNING,
        )
    )

    assert result.date_patch is not None
    assert result.date_patch.budget == 600
    assert [operation.type for operation in result.date_operations] == [
        DateOperationType.UPDATE_CONSTRAINT,
        DateOperationType.UPDATE_CONSTRAINT,
        DateOperationType.ADD_STOP,
        DateOperationType.ADD_STOP,
    ]
    assert [
        operation.constraint_field for operation in result.date_operations[:2]
    ] == [DateConstraintField.BUDGET, DateConstraintField.BUDGET_SCOPE]
    assert result.date_operation_candidate_count == 4
    assert result.date_operation_rejections == []


def test_deterministic_constraint_rejects_conflicting_model_value() -> None:
    proposed = DatePlanOperation(
        type=DateOperationType.UPDATE_CONSTRAINT,
        constraint_field=DateConstraintField.BUDGET,
        constraint_value=2000,
        source_span="预算改为1000",
    )

    result = _resolve(
        "预算改为1000",
        DatePlanPatch(budget=1000),
        proposed_operations=[proposed],
    )

    assert [operation.constraint_value for operation in result.operations] == [1000]
    assert [(item.operation.constraint_value, item.reason) for item in result.rejected] == [
        (2000, "constraint_conflicts_with_deterministic_patch")
    ]


def test_model_stop_without_current_turn_evidence_is_rejected() -> None:
    proposed = DatePlanOperation(
        type=DateOperationType.ADD_STOP,
        payload=DesiredDateStop(kind=StopKind.DINING, keyword="日料"),
        source_span="预算改为1000",
    )

    result = _resolve(
        "预算改为1000",
        DatePlanPatch(budget=1000),
        proposed_operations=[proposed],
    )

    assert [operation.type for operation in result.operations] == [
        DateOperationType.UPDATE_CONSTRAINT
    ]
    assert [item.reason for item in result.rejected] == [
        "payload_without_current_turn_evidence"
    ]

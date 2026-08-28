from datetime import date, datetime

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from loveapp.adapters.maps.amap import AmapMapProvider
from loveapp.adapters.maps.demo import DemoMapProvider
from loveapp.adapters.memory.in_memory import InMemoryMemoryStore
from loveapp.agents.date_planner import DatePlanningAgent
from loveapp.application.conversation_flow import clarification_message
from loveapp.application.date_planning.operation_resolution import (
    DateOperationResolver,
    date_semantic_parse_reasons,
)
from loveapp.application.date_planning.operation_validation import DateOperationVerifier
from loveapp.application.date_planning.patching import DatePlanPatchApplier
from loveapp.application.date_planning.requirements import DateRequirementMatcher
from loveapp.application.date_planning.state_projection import (
    DateRequirementProjector,
    project_requirements_to_state,
)
from loveapp.application.memory import MemoryService, NoOpMemoryExtractor
from loveapp.application.routing import route_by_rules
from loveapp.bootstrap import build_container
from loveapp.core.config import Settings
from loveapp.domain.conversation import ConversationRequest, ConversationTurnResult
from loveapp.domain.date_operations import (
    DateOperationType,
    DatePlanOperation,
    DateStopConstraints,
    DateStopRequirement,
    DesiredDateStop,
    MealType,
    RequirementStatus,
    StopKind,
    StopReference,
    deterministic_requirement_id,
)
from loveapp.domain.date_patch import DatePlanPatch
from loveapp.domain.date_plan import (
    DatePlan,
    DatePlanItem,
    DatePlanRequest,
    Place,
    PlaceSearchRequest,
    Route,
)
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.enums import (
    DatePlanningStatus,
    PlaceCategory,
    RelationshipStage,
    TaskType,
    TransportMode,
)
from loveapp.domain.observability import TimingStatus
from loveapp.domain.routing import RouteInput, RouteResult
from loveapp.domain.runtime_context import DatePlanRuntimeContext, RuntimeContext


class _CapturingDemoMapProvider(DemoMapProvider):
    def __init__(self) -> None:
        self.requests: list[PlaceSearchRequest] = []

    async def search_places(self, request: PlaceSearchRequest) -> list[Place]:
        self.requests.append(request)
        return await super().search_places(request)


def _state(*, constraints: list[str] | None = None) -> DatePlanningTaskState:
    return DatePlanningTaskState(
        user_id="b69-user",
        relationship_id="b69-relationship",
        conversation_id="b69-conversation",
        status=DatePlanningStatus.PLANNED,
        city="上海",
        area="静安区",
        date=date(2026, 8, 29),
        budget=1500,
        constraints=constraints or [],
    )


def _constraint_plan(
    *,
    cost: int = 480,
    cost_is_estimate: bool = False,
    rating: float | None = 4.9,
    address: str = "上海市浦东新区陆家嘴测试地址",
    route_distance: int | None = 900,
) -> DatePlan:
    route = (
        Route(
            origin_id="origin",
            destination_id="restaurant",
            mode=TransportMode.TRANSIT,
            duration_minutes=10,
            distance_meters=route_distance,
            source="test",
        )
        if route_distance is not None
        else None
    )
    place = Place(
        id="restaurant",
        name="测试法餐厅",
        city="上海",
        address=address,
        category=PlaceCategory.RESTAURANT,
        tags=["法餐"],
        estimated_cost_per_person=cost,
        cost_is_estimate=cost_is_estimate,
        rating=rating,
        source="test",
    )
    item = DatePlanItem(
        order=1,
        place=place,
        duration_minutes=90,
        estimated_cost=cost * 2,
        reason="test",
        route_from_previous=route,
        meal_type="dinner",
        slot_keyword="法餐",
    )
    return DatePlan(
        title="测试计划",
        summary="测试计划",
        items=[item],
        total_estimated_cost=item.estimated_cost,
        total_duration_minutes=item.duration_minutes,
        data_source="test",
    )


def _requirement(constraints: DateStopConstraints) -> DateStopRequirement:
    return DateStopRequirement(
        id="dinner-requirement",
        alternatives=[
            DesiredDateStop(
                kind=StopKind.DINING,
                keyword="法餐",
                meal_type=MealType.DINNER,
                constraints=constraints,
            )
        ],
    )


def _assert_no_failed_timing(turn: ConversationTurnResult) -> None:
    assert all(timing.status != TimingStatus.FAILED for timing in turn.timings)
    assert all(timing.error is None for timing in turn.timings)


def test_stop_local_constraints_survive_typed_operation_validation() -> None:
    operation = DatePlanOperation.model_validate(
        {
            "type": "replace_stop",
            "target": {"meal_type": "dinner"},
            "payload": {
                "kind": "dining",
                "keyword": "法餐",
                "meal_type": "dinner",
                "constraints": {
                    "max_cost_per_person": 500,
                    "min_rating": 4.9,
                    "preferred_area": "陆家嘴",
                    "max_distance_meters": 1200,
                },
            },
        }
    )

    assert operation.payload is not None
    assert operation.payload.constraints == DateStopConstraints(
        max_cost_per_person=500,
        min_rating=4.9,
        preferred_area="陆家嘴",
        max_distance_meters=1200,
    )


def test_unconstrained_requirement_identity_remains_migration_stable() -> None:
    requirement_id = deterministic_requirement_id(
        [DesiredDateStop(kind=StopKind.DINING, keyword="火锅")]
    )

    assert requirement_id == "04594959e8165754adef6016e18478ef"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_cost_per_person", 0),
        ("min_rating", 5.1),
        ("preferred_area", ""),
        ("max_distance_meters", 0),
    ],
)
def test_stop_local_constraints_reject_invalid_typed_values(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        DateStopConstraints.model_validate({field: value})


def test_stop_local_area_does_not_overwrite_global_task_area() -> None:
    existing = DateStopRequirement(
        id="dinner",
        alternatives=[
            DesiredDateStop(
                kind=StopKind.DINING,
                keyword="西餐",
                meal_type=MealType.DINNER,
            )
        ],
    )
    current = project_requirements_to_state(_state(), [existing])
    operation = DatePlanOperation(
        type=DateOperationType.REPLACE_STOP,
        target=StopReference(meal_type=MealType.DINNER),
        payload=DesiredDateStop(
            kind=StopKind.DINING,
            keyword="法餐",
            meal_type=MealType.DINNER,
            constraints=DateStopConstraints(preferred_area="陆家嘴"),
        ),
    )

    requirements = DateRequirementProjector().apply_requirement_operations(
        current.requirements,
        [operation],
    )
    projected = project_requirements_to_state(current, requirements)

    assert projected.area == "静安区"
    assert projected.requirements[0].alternatives[0].constraints == DateStopConstraints(
        preferred_area="陆家嘴"
    )


def test_stop_local_constraints_require_semantic_parse() -> None:
    text = "晚餐改成一家人均500元以内、评分4.9以上、陆家嘴附近的法餐。"
    deterministic = DateOperationResolver().resolve(text, None, DatePlanPatch())

    reasons = date_semantic_parse_reasons(text, None, deterministic)

    assert "stop_local_constraints" in reasons


def test_named_landmark_is_a_deterministic_typed_stop() -> None:
    text = "下午去东方明珠。"
    deterministic = DateOperationResolver().resolve(
        text,
        None,
        DatePlanPatch(schedule_hints=["下午"]),
    )

    additions = [
        operation
        for operation in deterministic.operations
        if operation.type == DateOperationType.ADD_STOP
    ]
    assert len(additions) == 1
    assert additions[0].payload is not None
    assert additions[0].payload.place_name == "东方明珠"


def test_stop_local_constraints_require_current_turn_evidence() -> None:
    text = "晚餐改成一家人均500元以内、评分4.9以上、陆家嘴附近的法餐。"
    valid = DatePlanOperation(
        type=DateOperationType.REPLACE_STOP,
        target=StopReference(meal_type=MealType.DINNER),
        payload=DesiredDateStop(
            kind=StopKind.DINING,
            keyword="法餐",
            meal_type=MealType.DINNER,
            constraints=DateStopConstraints(
                max_cost_per_person=500,
                min_rating=4.9,
                preferred_area="陆家嘴",
            ),
        ),
        source_span=text,
    )
    hallucinated = valid.model_copy(
        update={
            "payload": valid.payload.model_copy(
                update={
                    "constraints": valid.payload.constraints.model_copy(
                        update={"min_rating": 5.0}
                    )
                }
            )
        }
    )

    verifier = DateOperationVerifier()
    runtime = RuntimeContext(
        user_id="b69-user",
        relationship_id="b69-relationship",
        conversation_id="b69-conversation",
        relationship_stage=RelationshipStage.UNKNOWN,
        now=datetime(2026, 8, 28, 10, 0),
        active_date_plan=DatePlanRuntimeContext(current_plan=_constraint_plan()),
    )
    accepted = verifier.verify([valid], text, runtime, DatePlanPatch())
    rejected = verifier.verify([hallucinated], text, runtime, DatePlanPatch())

    assert accepted.accepted == (valid,)
    assert rejected.accepted == ()
    assert rejected.rejected[0].reason == "payload_modifier_without_source_evidence"


def test_move_and_replace_preserve_unmentioned_stop_constraints() -> None:
    constraints = DateStopConstraints(
        max_cost_per_person=500,
        min_rating=4.9,
        preferred_area="陆家嘴",
    )
    existing = DesiredDateStop(
        kind=StopKind.DINING,
        keyword="法餐",
        meal_type=MealType.DINNER,
        constraints=constraints,
    )
    projector = DateRequirementProjector()
    moved = projector.apply_operations(
        [existing],
        [
            DatePlanOperation(
                type=DateOperationType.MOVE_STOP,
                target=StopReference(meal_type=MealType.DINNER),
                payload=DesiredDateStop(
                    kind=StopKind.DINING,
                    keyword="法餐",
                    target_day=2,
                ),
            )
        ],
    )
    replaced = projector.apply_operations(
        moved,
        [
            DatePlanOperation(
                type=DateOperationType.REPLACE_STOP,
                target=StopReference(meal_type=MealType.DINNER),
                payload=DesiredDateStop(kind=StopKind.DINING, keyword="西餐"),
            )
        ],
    )

    assert moved[0].constraints == constraints
    assert replaced[0].constraints == constraints


def test_replacement_overlays_only_explicit_stop_constraint_fields() -> None:
    existing = DesiredDateStop(
        kind=StopKind.DINING,
        keyword="法餐",
        meal_type=MealType.DINNER,
        constraints=DateStopConstraints(
            max_cost_per_person=500,
            min_rating=4.8,
            preferred_area="陆家嘴",
        ),
    )
    replacement = DatePlanOperation(
        type=DateOperationType.REPLACE_STOP,
        target=StopReference(meal_type=MealType.DINNER),
        payload=DesiredDateStop(
            kind=StopKind.DINING,
            keyword="法餐",
            constraints=DateStopConstraints(min_rating=4.9),
        ),
    )

    projected = DateRequirementProjector().apply_operations([existing], [replacement])

    assert projected[0].constraints == DateStopConstraints(
        max_cost_per_person=500,
        min_rating=4.9,
        preferred_area="陆家嘴",
    )


async def test_planner_scopes_search_to_stop_local_constraints() -> None:
    provider = _CapturingDemoMapProvider()
    memory_service = MemoryService(InMemoryMemoryStore(), NoOpMemoryExtractor())
    planner = DatePlanningAgent(provider, memory_service)
    requirement = _requirement(
        DateStopConstraints(
            max_cost_per_person=500,
            min_rating=4.9,
            preferred_area="陆家嘴",
        )
    )

    await planner.plan(
        DatePlanRequest(
            city="上海",
            area="静安区",
            budget=1500,
            requirements=[requirement],
        )
    )

    search = next(
        request
        for request in provider.requests
        if request.category == PlaceCategory.RESTAURANT
        and request.required_keywords == ["法餐"]
    )
    assert search.area == "陆家嘴"
    assert search.max_cost_per_person == 500
    assert search.require_verified_cost is True
    assert search.min_rating == 4.9


async def test_amap_fails_closed_when_required_price_or_rating_is_missing() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v5/place/text"
        return httpx.Response(
            200,
            json={
                "status": "1",
                "info": "OK",
                "pois": [
                    {
                        "id": "missing-price",
                        "name": "缺价格法餐",
                        "type": "餐饮服务;西餐厅",
                        "typecode": "050100",
                        "adname": "浦东新区",
                        "adcode": "310115",
                        "location": "121.50,31.23",
                        "business": {"rating": "5.0"},
                    },
                    {
                        "id": "missing-rating",
                        "name": "缺评分法餐",
                        "type": "餐饮服务;西餐厅",
                        "typecode": "050100",
                        "adname": "浦东新区",
                        "adcode": "310115",
                        "location": "121.51,31.23",
                        "business": {"cost": "400"},
                    },
                    {
                        "id": "verified",
                        "name": "可验证法餐",
                        "type": "餐饮服务;西餐厅",
                        "typecode": "050100",
                        "adname": "浦东新区",
                        "adcode": "310115",
                        "location": "121.52,31.23",
                        "business": {"cost": "480", "rating": "4.9"},
                    },
                ],
            },
        )

    client = httpx.AsyncClient(
        base_url="https://restapi.amap.com",
        transport=httpx.MockTransport(handler),
    )
    provider = AmapMapProvider(
        SecretStr("test-key"),
        min_interval_seconds=0,
        client=client,
    )
    try:
        places = await provider.search_places(
            PlaceSearchRequest(
                city="上海",
                category=PlaceCategory.RESTAURANT,
                max_cost_per_person=500,
                require_verified_cost=True,
                min_rating=4.9,
            )
        )
        estimated_places = await provider.search_places(
            PlaceSearchRequest(
                city="上海",
                category=PlaceCategory.RESTAURANT,
                max_cost_per_person=500,
            )
        )
    finally:
        await client.aclose()

    assert [place.id for place in places] == ["verified"]
    estimated = next(place for place in estimated_places if place.id == "missing-price")
    assert estimated.cost_is_estimate is True


@pytest.mark.parametrize(
    ("constraints", "plan_overrides", "expected_reason"),
    [
        (
            DateStopConstraints(max_cost_per_person=500),
            {"cost_is_estimate": True},
            "constraint_unverified",
        ),
        (
            DateStopConstraints(max_cost_per_person=500),
            {"cost": 501},
            "constraint_unsatisfied",
        ),
        (
            DateStopConstraints(min_rating=4.9),
            {"rating": None},
            "constraint_unverified",
        ),
        (
            DateStopConstraints(min_rating=4.9),
            {"rating": 4.8},
            "constraint_unsatisfied",
        ),
        (
            DateStopConstraints(preferred_area="陆家嘴"),
            {"address": "上海市静安区测试地址"},
            "constraint_unsatisfied",
        ),
        (
            DateStopConstraints(max_distance_meters=1000),
            {"route_distance": None},
            "constraint_unverified",
        ),
        (
            DateStopConstraints(max_distance_meters=1000),
            {"route_distance": 1001},
            "constraint_unsatisfied",
        ),
    ],
)
def test_requirement_matcher_fails_closed_for_unmet_or_unverified_stop_constraints(
    constraints: DateStopConstraints,
    plan_overrides: dict[str, object],
    expected_reason: str,
) -> None:
    plan = _constraint_plan(**plan_overrides)  # type: ignore[arg-type]

    match = DateRequirementMatcher().match([_requirement(constraints)], plan)[0]

    assert match.status == RequirementStatus.UNSATISFIED
    assert match.reason_code == expected_reason
    assert match.matched_place_ids == ["restaurant"]


def test_requirement_matcher_accepts_fully_verified_stop_constraints() -> None:
    constraints = DateStopConstraints(
        max_cost_per_person=500,
        min_rating=4.9,
        preferred_area="陆家嘴",
        max_distance_meters=1000,
    )

    match = DateRequirementMatcher().match(
        [_requirement(constraints)],
        _constraint_plan(),
    )[0]

    assert match.status == RequirementStatus.FULFILLED
    assert match.reason_code is None
    assert match.matched_place_ids == ["restaurant"]


@pytest.mark.parametrize(
    "query",
    [
        "预算提高到1500，其他安排都不要动。",
        "晚餐不要换，电影也别删，只把电影调到晚饭后。",
        "电影不要了，其他安排不变。",
    ],
)
def test_turn_control_language_is_policy_not_durable_constraint(query: str) -> None:
    current = _state(constraints=["不要室外活动"])

    route = route_by_rules(
        RouteInput(
            latest_query=query,
            active_task=TaskType.DATE_PLANNING,
            forced_task=TaskType.DATE_PLANNING,
            date_task_state=current,
        )
    )

    assert route.date_mutation_policy.preserve_unmentioned_items is True
    assert not any(
        marker in constraint
        for constraint in route.date_plan.constraints
        for marker in ("不要动", "不要换", "别删", "其他安排不变")
    )
    patch = route.date_patch or DatePlanPatch()
    candidate = DatePlanPatchApplier().apply(current, patch)
    assert candidate.constraints == ["不要室外活动"]


def test_durable_relationship_plan_constraint_remains_persistable() -> None:
    route = route_by_rules(
        RouteInput(
            latest_query="帮我安排约会，不要室外活动。",
            forced_task=TaskType.DATE_PLANNING,
        )
    )

    assert route.date_mutation_policy.preserve_unmentioned_items is False
    assert route.date_plan.constraints == ["不要室外活动"]


def test_french_cuisine_is_preserved_as_a_typed_meal_requirement() -> None:
    route = route_by_rules(
        RouteInput(
            latest_query=(
                "晚餐改成一家人均500元以内、评分4.9以上、陆家嘴附近的法餐。"
            ),
            active_task=TaskType.DATE_PLANNING,
            forced_task=TaskType.DATE_PLANNING,
            date_task_state=_state(),
        )
    )

    assert route.date_patch is not None
    assert route.date_patch.dining_keywords == ["法餐"]
    assert route.date_patch.meal_keywords == {"dinner": ["法餐"]}


def test_unresolved_date_reference_uses_domain_clarification_options() -> None:
    route = RouteResult(
        normalized_query="那个餐厅换一家吧。",
        task_type=TaskType.DATE_PLANNING,
        task_confidence=1,
        needs_clarification=True,
        clarification_triggered=True,
        clarification_reason="unresolved_date_plan_reference",
        clarification_options=["午餐的餐厅 A", "晚餐的餐厅 B"],
    )

    message = clarification_message(route, repeated=False)

    assert "午餐的餐厅 A" in message
    assert "晚餐的餐厅 B" in message
    assert "分析关系" not in message
    assert "安排约会" not in message


async def test_fresh_chinese_city_area_turn_does_not_raise(
    app_settings: Settings,
) -> None:
    container = build_container(app_settings)
    try:
        turn = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="b69-runtime-fresh-user",
                relationship_id="b69-runtime-fresh-relationship",
                conversation_id="b69-runtime-fresh-conversation",
                query="帮我安排上海静安区约会，预算1000。",
            )
        )
    finally:
        await container.aclose()

    assert turn.route.task_type == TaskType.DATE_PLANNING
    assert turn.date_task_state is not None
    assert turn.date_task_state.city == "上海"
    assert turn.date_task_state.area == "静安区"
    _assert_no_failed_timing(turn)


@pytest.mark.parametrize(
    ("case_id", "query"),
    [
        ("district", "上海静安区"),
        ("business-area", "上海陆家嘴，预算1000。"),
        ("landmark", "上海静安寺附近，预算1000。"),
    ],
)
async def test_resumed_chinese_city_area_turn_does_not_raise(
    app_settings: Settings,
    case_id: str,
    query: str,
) -> None:
    container = build_container(app_settings)
    scope = {
        "user_id": f"b69-runtime-{case_id}-user",
        "relationship_id": f"b69-runtime-{case_id}-relationship",
        "conversation_id": f"b69-runtime-{case_id}-conversation",
    }
    try:
        first = await container.conversation_agent.chat(
            ConversationRequest(
                **scope,
                query="帮我安排约会，预算1000。",
            )
        )
        resumed = await container.conversation_agent.chat(
            ConversationRequest(
                **scope,
                query=query,
                active_task=first.active_task,
            )
        )
    finally:
        await container.aclose()

    assert first.date_task_state is not None
    assert first.date_task_state.status == DatePlanningStatus.COLLECTING
    assert resumed.route.task_type == TaskType.DATE_PLANNING
    assert resumed.date_task_state is not None
    assert resumed.date_task_state.city == "上海"
    assert resumed.date_task_state.budget == 1000
    _assert_no_failed_timing(resumed)

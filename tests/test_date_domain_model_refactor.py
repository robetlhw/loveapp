import json
from datetime import UTC, date, datetime
from pathlib import Path

import aiosqlite

from loveapp.adapters.date_tasks import (
    InMemoryDatePlanningTaskStore,
    SQLiteDatePlanningTaskStore,
)
from loveapp.agents.date_workflow import DatePlanningWorkflow, _apply_requirement_match_reasons
from loveapp.application.date_planning.operations import (
    DateOperationExecutor,
    DatePlanOperationExecution,
    RejectedDatePlanOperation,
)
from loveapp.application.date_planning.plan_diff import (
    diff_date_plans,
    diff_date_tasks,
)
from loveapp.application.date_planning.requirements import DateRequirementMatcher
from loveapp.application.date_planning.state_projection import (
    DateRequirementProjector,
    desired_stops_from_plan,
    project_requirements_to_state,
    requirements_for_state,
)
from loveapp.application.routing import extract_date_plan_slots
from loveapp.domain.conversation import ConversationRequest
from loveapp.domain.date_operations import (
    DateConstraintField,
    DateOperationType,
    DatePlanOperation,
    DateStopRequirement,
    DesiredDateStop,
    MealType,
    RequirementStatus,
    StopKind,
    StopReference,
    TemporalAnchor,
)
from loveapp.domain.date_patch import DatePlanPatch, SlotSource
from loveapp.domain.date_plan import DatePlan, DatePlanItem, DatePlanRequest, Place
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.date_workflow import DatePlanningWorkflowInput
from loveapp.domain.enums import (
    DatePlanMutation,
    DatePlanningStatus,
    DateTaskIntent,
    PlaceCategory,
    TaskType,
)
from loveapp.domain.routing import DatePlanSlots, RouteInput, RouteResult

PLANNED_DATE = date(2026, 8, 29)


def _place(
    place_id: str,
    name: str,
    category: PlaceCategory,
    *tags: str,
    cost: int = 50,
) -> Place:
    return Place(
        id=place_id,
        name=name,
        city="上海",
        address="上海市静安区测试地址",
        category=category,
        tags=list(tags),
        search_keywords=list(tags),
        estimated_cost_per_person=cost,
        source="test",
    )


def _item(
    place_id: str,
    keyword: str,
    category: PlaceCategory,
    *,
    order: int,
    meal_type: MealType | None = None,
    cost: int = 50,
) -> DatePlanItem:
    return DatePlanItem(
        order=order,
        place=_place(place_id, f"测试{keyword}", category, keyword, cost=cost),
        duration_minutes=90,
        estimated_cost=cost,
        reason="测试",
        scheduled_date=PLANNED_DATE,
        meal_type=meal_type.value if meal_type else None,
        time_label=(
            {MealType.LUNCH: "午餐", MealType.DINNER: "晚餐"}.get(meal_type)
            if meal_type
            else None
        ),
        slot_keyword=keyword,
    )


def _plan(*items: DatePlanItem, total_cost: int | None = None) -> DatePlan:
    return DatePlan(
        title="测试约会计划",
        summary="测试",
        start_date=PLANNED_DATE,
        end_date=PLANNED_DATE,
        items=list(items),
        total_estimated_cost=(
            total_cost if total_cost is not None else sum(item.estimated_cost for item in items)
        ),
        total_duration_minutes=sum(item.duration_minutes for item in items),
        data_source="test",
    )


def _requirement(
    requirement_id: str,
    *alternatives: DesiredDateStop,
    source_span: str | None = None,
) -> DateStopRequirement:
    return DateStopRequirement(
        id=requirement_id,
        alternatives=list(alternatives),
        min_satisfied=1,
        max_satisfied=1,
        source_span=source_span,
    )


def _route(
    query: str,
    operations: list[DatePlanOperation],
    *,
    patch: DatePlanPatch | None = None,
) -> RouteResult:
    return RouteResult(
        normalized_query=query,
        task_type=TaskType.DATE_PLANNING,
        task_confidence=1,
        date_plan=DatePlanSlots(),
        date_patch=patch,
        date_intent=DateTaskIntent.SUPPLEMENT,
        date_mutation=DatePlanMutation.ADD,
        date_operations=operations,
    )


def _current_state(
    conversation_id: str,
    plan: DatePlan,
    *,
    budget: int = 500,
) -> DatePlanningTaskState:
    return DatePlanningTaskState(
        user_id="date-domain-user",
        relationship_id="date-domain-relationship",
        conversation_id=conversation_id,
        status=DatePlanningStatus.PLANNED,
        city="上海",
        date=PLANNED_DATE,
        budget=budget,
        current_plan=plan,
        plan_version=1,
        clarification_round=3,
    )


def test_requirements_are_canonical_and_legacy_slots_are_one_way_derived() -> None:
    hotpot = _requirement(
        "req-hotpot",
        DesiredDateStop(
            kind=StopKind.DINING,
            keyword="火锅",
            meal_type=MealType.DINNER,
        ),
    )
    barbecue = _requirement(
        "req-barbecue",
        DesiredDateStop(
            kind=StopKind.DINING,
            keyword="烧烤",
            meal_type=MealType.LUNCH,
        ),
    )

    state = project_requirements_to_state(
        DatePlanningTaskState(user_id="u", relationship_id="r", conversation_id="c"),
        [hotpot, barbecue],
    )

    assert state.requirements == [hotpot, barbecue]
    assert [stop.keyword for stop in state.desired_stops] == ["火锅", "烧烤"]
    assert state.dining_keywords == ["火锅", "烧烤"]
    assert state.meal_keywords == {"dinner": ["火锅"], "lunch": ["烧烤"]}

    stale_legacy = state.model_copy(
        update={"dining_keywords": ["日料"], "meal_keywords": {}}
    )
    assert requirements_for_state(stale_legacy) == [hotpot, barbecue]


def test_plan_projection_is_diagnostic_and_never_overwrites_requirements() -> None:
    lunch_barbecue = _requirement(
        "req-lunch-barbecue",
        DesiredDateStop(
            kind=StopKind.DINING,
            keyword="烧烤",
            meal_type=MealType.LUNCH,
        ),
    )
    state = project_requirements_to_state(
        DatePlanningTaskState(user_id="u", relationship_id="r", conversation_id="c"),
        [lunch_barbecue],
    )
    generic_cafe_plan = _plan(
        _item("generic-cafe", "咖啡", PlaceCategory.CAFE, order=1)
    )

    diagnostic = desired_stops_from_plan(generic_cafe_plan)

    assert diagnostic[0].keyword == "咖啡"
    assert requirements_for_state(state) == [lunch_barbecue]
    assert state.requirements[0].alternatives[0].keyword == "烧烤"


async def test_legacy_sqlite_state_migrates_once_without_overwriting_canonical(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy-date-task.db"
    store = SQLiteDatePlanningTaskStore(database_path)
    await store.get(user_id="u", relationship_id="r", conversation_id="c")
    now = datetime.now(UTC).isoformat()
    old_payload = {
        "user_id": "u",
        "relationship_id": "r",
        "conversation_id": "c",
        "dining_keywords": ["火锅"],
        "meal_keywords": {"dinner": ["火锅"]},
        "updated_at": now,
    }
    async with aiosqlite.connect(database_path) as connection:
        await connection.execute(
            """
            INSERT INTO date_planning_tasks (
                user_id, relationship_id, conversation_id, state_json, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("u", "r", "c", json.dumps(old_payload, ensure_ascii=False), now),
        )
        await connection.commit()

    migrated = await store.get(user_id="u", relationship_id="r", conversation_id="c")

    assert migrated is not None
    assert migrated.requirement_schema_version == 1
    assert [stop.keyword for stop in migrated.requirements[0].alternatives] == ["火锅"]
    first_id = migrated.requirements[0].id

    await store.save(migrated.model_copy(update={"dining_keywords": ["日料"]}))
    reloaded = await store.get(user_id="u", relationship_id="r", conversation_id="c")

    assert reloaded is not None
    assert reloaded.requirements[0].id == first_id
    assert reloaded.requirements[0].alternatives[0].keyword == "火锅"
    assert reloaded.dining_keywords == ["火锅"]


def test_requirement_matcher_reports_fulfilled_and_unsatisfied() -> None:
    requirement = _requirement(
        "req-hotpot",
        DesiredDateStop(kind=StopKind.DINING, keyword="火锅"),
    )
    matcher = DateRequirementMatcher()

    fulfilled = matcher.match(
        [requirement],
        _plan(_item("hotpot", "火锅", PlaceCategory.RESTAURANT, order=1)),
    )[0]
    unsatisfied = matcher.match(
        [requirement],
        _plan(_item("japanese", "日料", PlaceCategory.RESTAURANT, order=1)),
    )[0]

    assert fulfilled.status == RequirementStatus.FULFILLED
    assert fulfilled.matched_place_ids == ["hotpot"]
    assert unsatisfied.status == RequirementStatus.UNSATISFIED
    assert unsatisfied.reason_code == "required_stop_missing"


def test_one_of_requirement_needs_only_one_alternative() -> None:
    one_of = _requirement(
        "req-museum-or-aquarium",
        DesiredDateStop(kind=StopKind.ACTIVITY, keyword="博物馆"),
        DesiredDateStop(kind=StopKind.ACTIVITY, keyword="海洋馆"),
        source_span="博物馆，海洋馆也行",
    )

    match = DateRequirementMatcher().match(
        [one_of],
        _plan(_item("aquarium", "海洋馆", PlaceCategory.ATTRACTION, order=1)),
    )[0]

    assert match.status == RequirementStatus.FULFILLED
    assert match.matched_place_ids == ["aquarium"]


def test_requirement_matching_respects_plan_item_requirement_ownership() -> None:
    museum_requirement = _requirement(
        "req-museum",
        DesiredDateStop(kind=StopKind.ACTIVITY, keyword="博物馆"),
    )
    attraction_requirement = _requirement(
        "req-attraction",
        DesiredDateStop(kind=StopKind.ACTIVITY, keyword="景点"),
    )
    plan = _plan(
        _item(
            "museum",
            "博物馆",
            PlaceCategory.ATTRACTION,
            order=1,
        ),
        _item(
            "attraction",
            "景点",
            PlaceCategory.ATTRACTION,
            order=2,
        ),
    )

    matches = DateRequirementMatcher().match(
        [museum_requirement, attraction_requirement],
        plan,
    )

    assert [match.status for match in matches] == [
        RequirementStatus.FULFILLED,
        RequirementStatus.FULFILLED,
    ]
    assert matches[0].matched_place_ids == ["museum"]
    assert matches[1].matched_place_ids == ["attraction"]


def test_single_alternative_cardinality_counts_alternatives_not_matching_places() -> None:
    requirement = _requirement(
        "req-hotpot",
        DesiredDateStop(kind=StopKind.DINING, keyword="火锅"),
    )
    plan = _plan(
        _item(
            "hotpot-a",
            "火锅",
            PlaceCategory.RESTAURANT,
            order=1,
        ),
        _item(
            "hotpot-b",
            "火锅",
            PlaceCategory.RESTAURANT,
            order=2,
        ),
    )

    match = DateRequirementMatcher().match([requirement], plan)[0]

    assert match.status == RequirementStatus.FULFILLED
    assert match.matched_place_ids == ["hotpot-a", "hotpot-b"]


def test_one_of_cardinality_rejects_two_distinct_satisfied_alternatives() -> None:
    requirement = _requirement(
        "req-choice",
        DesiredDateStop(kind=StopKind.ACTIVITY, keyword="博物馆"),
        DesiredDateStop(kind=StopKind.ACTIVITY, keyword="海洋馆"),
    )
    plan = _plan(
        _item(
            "museum",
            "博物馆",
            PlaceCategory.ATTRACTION,
            order=1,
        ),
        _item(
            "aquarium",
            "海洋馆",
            PlaceCategory.ATTRACTION,
            order=2,
        ),
    )

    match = DateRequirementMatcher().match([requirement], plan)[0]

    assert match.status == RequirementStatus.UNSATISFIED
    assert match.reason_code == "alternative_cardinality_exceeded"


def test_explicit_alternative_group_projects_to_one_requirement() -> None:
    operations = [
        DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=DesiredDateStop(kind=StopKind.ACTIVITY, keyword=keyword),
            alternative_group="museum-choice",
            source_span="博物馆，海洋馆也行",
        )
        for keyword in ("博物馆", "海洋馆")
    ]

    projected = DateRequirementProjector().apply_requirement_operations([], operations)

    assert len(projected) == 1
    assert [alternative.keyword for alternative in projected[0].alternatives] == [
        "博物馆",
        "海洋馆",
    ]
    assert projected[0].min_satisfied == 1
    assert projected[0].max_satisfied == 1


def test_readding_replaced_content_creates_a_distinct_requirement() -> None:
    projector = DateRequirementProjector()
    original = DesiredDateStop(kind=StopKind.DINING, keyword="日料")
    replacement = DesiredDateStop(kind=StopKind.DINING, keyword="火锅")

    added = projector.apply_requirement_operations(
        [],
        [DatePlanOperation(type=DateOperationType.ADD_STOP, payload=original)],
    )
    original_requirement_id = added[0].id
    replaced = projector.apply_requirement_operations(
        added,
        [
            DatePlanOperation(
                type=DateOperationType.REPLACE_STOP,
                target=StopReference(keyword="日料"),
                payload=replacement,
            )
        ],
    )
    projected = projector.apply_requirement_operations(
        replaced,
        [DatePlanOperation(type=DateOperationType.ADD_STOP, payload=original)],
    )

    assert replaced[0].id == original_requirement_id
    assert len(projected) == 2
    assert [requirement.alternatives[0].keyword for requirement in projected] == [
        "火锅",
        "日料",
    ]
    assert all(len(requirement.alternatives) == 1 for requirement in projected)
    assert projected[0].id != projected[1].id


class _AlternativeAddPlanner:
    def __init__(self, available: set[str]) -> None:
        self.available = available
        self.attempted: list[str] = []

    async def plan(
        self,
        request,
        *,
        existing_plan=None,
        mutation=DatePlanMutation.NONE,
        focus_activity_keywords=None,
        **kwargs,
    ) -> DatePlan:
        del request, kwargs
        assert existing_plan is not None
        assert mutation == DatePlanMutation.ADD
        keyword = focus_activity_keywords[0]
        self.attempted.append(keyword)
        if keyword not in self.available:
            return existing_plan
        return _plan(
            *existing_plan.items,
            _item(
                f"new-{keyword}",
                keyword,
                PlaceCategory.ATTRACTION,
                order=len(existing_plan.items) + 1,
            ),
        )

    async def rebuild_plan(self, *args, **kwargs):  # pragma: no cover - branch guard
        raise AssertionError("rebuild should not run")


def _alternative_add_operations() -> list[DatePlanOperation]:
    return [
        DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=DesiredDateStop(kind=StopKind.ACTIVITY, keyword=keyword),
            alternative_group="activity-choice",
            source_span="museum or aquarium",
        )
        for keyword in ("museum", "aquarium")
    ]


async def test_executor_adds_at_most_one_explicit_alternative() -> None:
    planner = _AlternativeAddPlanner({"museum", "aquarium"})
    operations = _alternative_add_operations()
    current = _plan(
        _item("dinner", "dinner", PlaceCategory.RESTAURANT, order=1)
    )

    execution = await DateOperationExecutor(planner).apply(
        current,
        operations,
        DatePlanRequest(city="Shanghai", budget=1000),
    )

    assert planner.attempted == ["museum"]
    assert [item.slot_keyword for item in execution.plan.items] == ["dinner", "museum"]
    assert execution.applied == (operations[0],)
    assert [(item.operation, item.reason) for item in execution.rejected] == [
        (operations[1], "alternative_not_selected")
    ]


async def test_executor_tries_next_alternative_when_first_cannot_be_added() -> None:
    planner = _AlternativeAddPlanner({"aquarium"})
    operations = _alternative_add_operations()
    current = _plan(
        _item("dinner", "dinner", PlaceCategory.RESTAURANT, order=1)
    )

    execution = await DateOperationExecutor(planner).apply(
        current,
        operations,
        DatePlanRequest(city="Shanghai", budget=1000),
    )

    assert planner.attempted == ["museum", "aquarium"]
    assert [item.slot_keyword for item in execution.plan.items] == ["dinner", "aquarium"]
    assert execution.applied == (operations[1],)
    assert [(item.operation, item.reason) for item in execution.rejected] == [
        (operations[0], "stop_not_added")
    ]


def test_temporal_requirements_match_after_lunch_and_after_dinner() -> None:
    plan = _plan(
        _item(
            "lunch",
            "烧烤",
            PlaceCategory.RESTAURANT,
            order=1,
            meal_type=MealType.LUNCH,
        ),
        _item("museum", "博物馆", PlaceCategory.ATTRACTION, order=2),
        _item(
            "dinner",
            "火锅",
            PlaceCategory.RESTAURANT,
            order=3,
            meal_type=MealType.DINNER,
        ),
        _item("scenic", "景点", PlaceCategory.ATTRACTION, order=4),
    )
    requirements = [
        _requirement(
            "req-after-lunch",
            DesiredDateStop(
                kind=StopKind.ACTIVITY,
                keyword="博物馆",
                after=TemporalAnchor.LUNCH,
            ),
        ),
        _requirement(
            "req-after-dinner",
            DesiredDateStop(
                kind=StopKind.ACTIVITY,
                keyword="景点",
                after=TemporalAnchor.DINNER,
            ),
        ),
    ]

    matches = DateRequirementMatcher().match(requirements, plan)

    assert [match.status for match in matches] == [
        RequirementStatus.FULFILLED,
        RequirementStatus.FULFILLED,
    ]


class _UnusedPlanner:
    async def plan(self, *args, **kwargs):  # pragma: no cover - branch guard
        raise AssertionError("planner should not run")


class _OutcomeExecutor:
    def __init__(
        self,
        candidate: DatePlan,
        *,
        apply_operation: bool,
    ) -> None:
        self._candidate = candidate
        self._apply_operation = apply_operation

    async def apply(
        self,
        existing_plan,
        operations,
        request,
        *,
        trace=None,
        required_mutation=DatePlanMutation.NONE,
        requirements=None,
    ) -> DatePlanOperationExecution:
        del existing_plan, request, trace, required_mutation, requirements
        operation = operations[0]
        return DatePlanOperationExecution(
            plan=self._candidate,
            applied=(operation,) if self._apply_operation else (),
            rejected=(
                ()
                if self._apply_operation
                else (RejectedDatePlanOperation(operation, "stop_not_added"),)
            ),
            effective_mutation=(
                DatePlanMutation.ADD if self._apply_operation else DatePlanMutation.NONE
            ),
        )


async def _run_failed_add(
    *,
    conversation_id: str,
    candidate: DatePlan,
    apply_operation: bool,
) -> tuple[DatePlanningTaskState, DatePlanOperation]:
    old_plan = _plan(
        _item("old-japanese", "日料", PlaceCategory.RESTAURANT, order=1)
    )
    current = _current_state(conversation_id, old_plan, budget=300)
    operation = DatePlanOperation(
        type=DateOperationType.ADD_STOP,
        payload=DesiredDateStop(kind=StopKind.DINING, keyword="火锅"),
        source_span="晚餐吃火锅",
    )
    query = "晚餐吃火锅"
    request = ConversationRequest(
        user_id=current.user_id,
        relationship_id=current.relationship_id,
        conversation_id=current.conversation_id,
        query=query,
    )
    workflow = DatePlanningWorkflow(
        _UnusedPlanner(),  # type: ignore[arg-type]
        InMemoryDatePlanningTaskStore(),
        operation_executor=_OutcomeExecutor(  # type: ignore[arg-type]
            candidate,
            apply_operation=apply_operation,
        ),
    )

    result = await workflow.run(
        DatePlanningWorkflowInput(
            request=request,
            route=_route(query, [operation]),
            current_task_state=current,
        )
    )
    return result.task_state, operation


async def test_rejected_operation_preserves_requested_requirement() -> None:
    old_plan = _plan(
        _item("old-japanese", "日料", PlaceCategory.RESTAURANT, order=1)
    )

    state, operation = await _run_failed_add(
        conversation_id="rejected-operation",
        candidate=old_plan,
        apply_operation=False,
    )

    assert state.current_plan is not None
    assert [item.place.id for item in state.current_plan.items] == ["old-japanese"]
    assert [alternative.keyword for alternative in state.requirements[0].alternatives] == [
        "火锅"
    ]
    assert state.requirement_satisfaction[0].status == RequirementStatus.UNSATISFIED
    assert state.last_operations == [operation]


async def test_invalid_candidate_preserves_requirement_and_last_valid_plan() -> None:
    over_budget_hotpot = _plan(
        _item("expensive-hotpot", "火锅", PlaceCategory.RESTAURANT, order=1, cost=800),
        total_cost=800,
    )

    state, _ = await _run_failed_add(
        conversation_id="invalid-candidate",
        candidate=over_budget_hotpot,
        apply_operation=True,
    )

    assert state.current_plan is not None
    assert [item.place.id for item in state.current_plan.items] == ["old-japanese"]
    assert state.requirements[0].alternatives[0].keyword == "火锅"
    assert state.requirement_satisfaction[0].status == RequirementStatus.UNSATISFIED


async def test_invalid_candidate_without_fallback_matches_the_committed_empty_plan() -> None:
    current = DatePlanningTaskState(
        user_id="date-domain-user",
        relationship_id="date-domain-relationship",
        conversation_id="invalid-without-fallback",
        status=DatePlanningStatus.COLLECTING,
        city="上海",
        date=PLANNED_DATE,
        budget=300,
        clarification_round=3,
    )
    operation = DatePlanOperation(
        type=DateOperationType.ADD_STOP,
        payload=DesiredDateStop(kind=StopKind.DINING, keyword="火锅"),
        source_span="晚餐吃火锅",
    )
    candidate = _plan(
        _item("expensive-hotpot", "火锅", PlaceCategory.RESTAURANT, order=1, cost=800),
        total_cost=800,
    )
    workflow = DatePlanningWorkflow(
        _UnusedPlanner(),  # type: ignore[arg-type]
        InMemoryDatePlanningTaskStore(),
        operation_executor=_OutcomeExecutor(  # type: ignore[arg-type]
            candidate,
            apply_operation=True,
        ),
    )

    result = await workflow.run(
        DatePlanningWorkflowInput(
            request=ConversationRequest(
                user_id=current.user_id,
                relationship_id=current.relationship_id,
                conversation_id=current.conversation_id,
                query="晚餐吃火锅",
            ),
            route=_route("晚餐吃火锅", [operation]),
            current_task_state=current,
        )
    )

    assert result.plan is None
    assert result.plan_committed is False
    assert result.task_state.current_plan is None
    assert result.requirement_satisfaction[0].status == RequirementStatus.UNSATISFIED
    assert result.requirement_satisfaction[0].reason_code == "plan_unavailable"


class _IsolationPlanner:
    def __init__(self) -> None:
        self.requests: list[tuple[DatePlanMutation, DatePlanRequest]] = []

    async def plan(
        self,
        request: DatePlanRequest,
        *,
        existing_plan: DatePlan | None = None,
        mutation: DatePlanMutation = DatePlanMutation.NONE,
        **kwargs,
    ) -> DatePlan:
        del kwargs
        assert existing_plan is not None
        self.requests.append((mutation, request))
        if mutation == DatePlanMutation.REPLACE:
            retained = [item for item in existing_plan.items if item.place.id != "old-attraction"]
            replacement = _item(
                "new-attraction",
                "新景点",
                PlaceCategory.ATTRACTION,
                order=1,
            )
            return _plan(replacement, *retained)
        if mutation == DatePlanMutation.ADD:
            movie = _item(
                "new-movie",
                "电影院",
                PlaceCategory.ENTERTAINMENT,
                order=len(existing_plan.items) + 1,
            )
            return _plan(*existing_plan.items, movie)
        raise AssertionError(f"unexpected mutation: {mutation}")

    async def rebuild_plan(self, *args, **kwargs):  # pragma: no cover - branch guard
        raise AssertionError("rebuild should not run")


async def test_compound_add_and_generic_replace_use_operation_local_context() -> None:
    old_plan = _plan(
        _item("old-attraction", "原景点", PlaceCategory.ATTRACTION, order=1),
        _item(
            "dinner",
            "西餐",
            PlaceCategory.RESTAURANT,
            order=2,
            meal_type=MealType.DINNER,
        ),
    )
    replacement = DatePlanOperation(
        type=DateOperationType.REPLACE_STOP,
        target=StopReference(place_id="old-attraction"),
        payload=DesiredDateStop(
            kind=StopKind.ACTIVITY,
            generic_replacement=True,
        ),
        source_span="把第一个景点换成其他景点",
    )
    add_movie = DatePlanOperation(
        type=DateOperationType.ADD_STOP,
        payload=DesiredDateStop(kind=StopKind.ACTIVITY, keyword="电影院"),
        source_span="加一个电影",
    )
    requirements = [
        _requirement(
            "req-replacement",
            DesiredDateStop(kind=StopKind.ACTIVITY, generic_replacement=True),
        ),
        _requirement(
            "req-movie",
            DesiredDateStop(kind=StopKind.ACTIVITY, keyword="电影院"),
        ),
    ]
    request_state = project_requirements_to_state(
        _current_state("compound-isolation", old_plan, budget=1000),
        requirements,
    )
    request = DatePlanRequest(
        city="上海",
        date=PLANNED_DATE,
        budget=1000,
        requirements=request_state.requirements,
        dining_keywords=request_state.dining_keywords,
        activity_keywords=request_state.activity_keywords,
        meal_keywords=request_state.meal_keywords,
        schedule_hints=request_state.schedule_hints,
    )
    planner = _IsolationPlanner()

    execution = await DateOperationExecutor(planner).apply(
        old_plan,
        [add_movie, replacement],
        request,
        requirements=requirements,
    )

    assert {item.place.id for item in execution.plan.items} == {
        "new-attraction",
        "dinner",
        "new-movie",
    }
    replace_request = next(
        local_request
        for mutation, local_request in planner.requests
        if mutation == DatePlanMutation.REPLACE
    )
    assert all(
        alternative.keyword != "电影院"
        for requirement in replace_request.requirements
        for alternative in requirement.alternatives
    )


def test_task_diff_reports_budget_change_when_plan_is_unchanged() -> None:
    plan = _plan(_item("museum", "博物馆", PlaceCategory.ATTRACTION, order=1))
    before = _current_state("task-diff", plan, budget=600)
    after = before.model_copy(update={"budget": 800})

    task_diff = diff_date_tasks(before, after)
    plan_diff = diff_date_plans(plan, plan)

    assert task_diff.changed_fields == ["budget"]
    assert task_diff.changes["budget"].before == 600
    assert task_diff.changes["budget"].after == 800
    assert plan_diff.changed is False


def test_requirement_reason_targets_only_the_matching_plan_item_instance() -> None:
    lunch = _item(
        "shared-hotpot",
        "火锅",
        PlaceCategory.RESTAURANT,
        order=1,
        meal_type=MealType.LUNCH,
    )
    dinner = _item(
        "shared-hotpot",
        "火锅",
        PlaceCategory.RESTAURANT,
        order=2,
        meal_type=MealType.DINNER,
    )
    plan = _plan(lunch, dinner)
    requirement = _requirement(
        "dinner-hotpot",
        DesiredDateStop(
            kind=StopKind.DINING,
            keyword="火锅",
            meal_type=MealType.DINNER,
        ),
    )
    matches = DateRequirementMatcher().match([requirement], plan)

    enriched = _apply_requirement_match_reasons(plan, [requirement], matches)

    assert "符合火锅要求" not in enriched.items[0].reason
    assert "符合火锅要求" in enriched.items[1].reason


def test_requirement_reason_replaces_incorrect_global_preference_attribution() -> None:
    hotpot = _item(
        "hotpot",
        "火锅",
        PlaceCategory.RESTAURANT,
        order=1,
        meal_type=MealType.LUNCH,
    ).model_copy(update={"reason": "符合你们对火锅的偏好。"})
    barbecue = _item(
        "barbecue",
        "烧烤",
        PlaceCategory.RESTAURANT,
        order=2,
        meal_type=MealType.DINNER,
    ).model_copy(update={"reason": "符合你们对火锅的偏好。"})
    requirements = [
        _requirement(
            "lunch-hotpot",
            DesiredDateStop(
                kind=StopKind.DINING,
                keyword="火锅",
                meal_type=MealType.LUNCH,
            ),
        ),
        _requirement(
            "dinner-barbecue",
            DesiredDateStop(
                kind=StopKind.DINING,
                keyword="烧烤",
                meal_type=MealType.DINNER,
            ),
        ),
    ]
    plan = _plan(hotpot, barbecue)
    matches = DateRequirementMatcher().match(requirements, plan)

    enriched = _apply_requirement_match_reasons(plan, requirements, matches)

    assert enriched.items[0].reason == "符合火锅要求。"
    assert enriched.items[1].reason == "符合烧烤要求。"
    assert "火锅" not in enriched.items[1].reason


def test_plan_diff_keeps_same_place_on_other_day_when_one_instance_is_removed() -> None:
    day_one = _item(
        "shared-place",
        "景点",
        PlaceCategory.ATTRACTION,
        order=1,
    )
    day_two = day_one.model_copy(update={"day_index": 2})
    before = _plan(day_one, day_two).model_copy(update={"day_count": 2})
    after = _plan(day_one)

    diff = diff_date_plans(before, after)

    assert diff.removed_place_ids == ["shared-place"]
    assert diff.added_place_ids == []
    assert diff.moved_place_ids == []
    assert diff.unchanged_place_ids == ["shared-place"]


def test_operation_only_phrase_is_not_persisted_as_durable_constraint() -> None:
    slots = extract_date_plan_slots(RouteInput(latest_query="这个地方不想去了，换一个"))

    assert slots.constraints == []
    assert all("不想去" not in constraint for constraint in slots.constraints)
    assert all("换一个" not in constraint for constraint in slots.constraints)


def test_independent_constraint_survives_an_operation_in_another_clause() -> None:
    slots = extract_date_plan_slots(
        RouteInput(latest_query="把第一个景点换成博物馆，我不想吃辣")
    )

    assert slots.constraints == ["不想吃辣"]


class _CompoundOutcomeExecutor:
    def __init__(self, candidate: DatePlan) -> None:
        self._candidate = candidate

    async def apply(
        self,
        existing_plan,
        operations,
        request,
        *,
        trace=None,
        required_mutation=DatePlanMutation.NONE,
        requirements=None,
    ) -> DatePlanOperationExecution:
        del existing_plan, request, trace, required_mutation, requirements
        return DatePlanOperationExecution(
            plan=self._candidate,
            applied=tuple(operations),
            rejected=(),
            effective_mutation=DatePlanMutation.ADD,
        )


async def test_last_operations_records_the_complete_compound_turn() -> None:
    old_plan = _plan(
        _item("old-attraction", "原景点", PlaceCategory.ATTRACTION, order=1)
    )
    movie = _item("movie", "电影院", PlaceCategory.ENTERTAINMENT, order=2)
    candidate = _plan(*old_plan.items, movie)
    current = _current_state("compound-history", old_plan, budget=600)
    operations = [
        DatePlanOperation(
            type=DateOperationType.UPDATE_CONSTRAINT,
            constraint_field=DateConstraintField.BUDGET,
            constraint_value=800,
            source_span="预算提高到800",
        ),
        DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=DesiredDateStop(kind=StopKind.ACTIVITY, keyword="电影院"),
            source_span="加一个电影",
        ),
    ]
    query = "预算提高到800，再加一个电影"
    patch = DatePlanPatch(
        budget=800,
        source_by_field={"budget": SlotSource.RULE},
    )
    request = ConversationRequest(
        user_id=current.user_id,
        relationship_id=current.relationship_id,
        conversation_id=current.conversation_id,
        query=query,
    )
    workflow = DatePlanningWorkflow(
        _UnusedPlanner(),  # type: ignore[arg-type]
        InMemoryDatePlanningTaskStore(),
        operation_executor=_CompoundOutcomeExecutor(candidate),  # type: ignore[arg-type]
    )

    result = await workflow.run(
        DatePlanningWorkflowInput(
            request=request,
            route=_route(query, operations, patch=patch),
            current_task_state=current,
        )
    )

    assert result.task_state.budget == 800
    assert result.task_state.last_operations == operations
    assert [operation.type for operation in result.task_state.last_operations] == [
        DateOperationType.UPDATE_CONSTRAINT,
        DateOperationType.ADD_STOP,
    ]

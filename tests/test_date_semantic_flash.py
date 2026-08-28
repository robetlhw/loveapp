import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from rich.console import Console

import loveapp.adapters.date_semantics.openai_compatible as adapter_module
import loveapp.cli as cli_module
from loveapp.adapters.date_semantics import OpenAICompatibleDateSemanticParser
from loveapp.adapters.routing import OpenAICompatibleRouteCorrector
from loveapp.application.date_planning.operation_resolution import (
    DateOperationResolver,
    _prefer_grouped_additions,
    deterministic_date_parse_is_complete,
)
from loveapp.application.date_planning.operation_validation import DateOperationVerifier
from loveapp.application.date_planning.state_projection import DateRequirementProjector
from loveapp.application.routing import HybridRouter
from loveapp.bootstrap import (
    _build_date_semantic_parser,
    _build_route_corrector,
    build_container,
)
from loveapp.core.config import Settings
from loveapp.domain.conversation import ConversationRequest
from loveapp.domain.date_operations import (
    DateConstraintField,
    DateOperationType,
    DatePlanOperation,
    DateReplacementPreference,
    DateSemanticParseResult,
    DateStopConstraints,
    DesiredDateStop,
    MealType,
    StopKind,
    StopReference,
    TemporalAnchor,
    TimeWindow,
)
from loveapp.domain.date_patch import DatePlanPatch, SlotSource
from loveapp.domain.date_plan import DatePlan, DatePlanItem, Place
from loveapp.domain.enums import PlaceCategory, RelationshipStage, TaskType
from loveapp.domain.routing import RouteInput
from loveapp.domain.runtime_context import DatePlanRuntimeContext, RuntimeContext
from loveapp.safety import SafetyPolicy


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self.content = content
        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs: object):
        self.requests.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.content),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=61, completion_tokens=17),
        )


class _ConcurrentFakeCompletions:
    def __init__(self) -> None:
        self._both_started = asyncio.Event()
        self._started = 0

    async def create(self, **kwargs: object):
        self._started += 1
        if self._started == 2:
            self._both_started.set()
        await self._both_started.wait()
        messages = kwargs["messages"]
        assert isinstance(messages, list)
        payload = json.loads(messages[1]["content"])
        query = payload["latest_query"]
        if query == "first request":
            await asyncio.sleep(0.01)
            input_tokens, output_tokens = 101, 11
        else:
            input_tokens, output_tokens = 202, 22
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"operations": [], "unresolved_references": []}'
                    ),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
            ),
        )


class _FakeOpenAI:
    completions: _FakeCompletions

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.chat = SimpleNamespace(completions=self.completions)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _StaticSemanticParser:
    def __init__(self, result: DateSemanticParseResult) -> None:
        self.result = result
        self.calls = 0
        self.semantic_profile = {
            "model": "deepseek-v4-flash",
            "thinking": "disabled",
            "prompt_version": "date-semantic-v1.1",
        }
        self.last_telemetry: dict[str, object] = {}

    async def parse_date_operations(self, text, runtime_context, deterministic_operations):
        del text, runtime_context, deterministic_operations
        self.calls += 1
        self.last_telemetry = {
            **self.semantic_profile,
            "input_tokens": 642,
            "output_tokens": 187,
            "duration_ms": 532.0,
        }
        return self.result


class _FailingSemanticParser(_StaticSemanticParser):
    async def parse_date_operations(self, text, runtime_context, deterministic_operations):
        del text, runtime_context, deterministic_operations
        self.calls += 1
        self.last_telemetry = {
            **self.semantic_profile,
            "input_tokens": None,
            "output_tokens": None,
            "duration_ms": 20_000.0,
        }
        raise TimeoutError("date semantic timeout")


def _place(
    place_id: str,
    name: str,
    category: PlaceCategory,
    keyword: str,
) -> Place:
    return Place(
        id=place_id,
        name=name,
        city="上海",
        address="测试地址",
        category=category,
        tags=[keyword],
        search_keywords=[keyword],
        estimated_cost_per_person=100,
        source="test",
    )


def _item(
    place_id: str,
    name: str,
    category: PlaceCategory,
    keyword: str,
    *,
    order: int,
    meal_type: str | None = None,
    time_label: str | None = None,
) -> DatePlanItem:
    return DatePlanItem(
        order=order,
        place=_place(place_id, name, category, keyword),
        duration_minutes=90,
        estimated_cost=200,
        reason="test",
        meal_type=meal_type,
        time_label=time_label,
        slot_keyword=keyword,
    )


def _runtime(*items: DatePlanItem, budget: int = 600) -> RuntimeContext:
    plan = DatePlan(
        title="现有计划",
        summary="静安区单日约会",
        items=list(items),
        total_estimated_cost=sum(item.estimated_cost for item in items),
        total_duration_minutes=sum(item.duration_minutes for item in items),
        data_source="test",
    )
    return RuntimeContext(
        user_id="user",
        relationship_id="relationship",
        conversation_id="conversation",
        relationship_stage=RelationshipStage.STABLE_RELATIONSHIP,
        active_task=TaskType.DATE_PLANNING,
        active_date_plan=DatePlanRuntimeContext(
            city="上海",
            area="静安区",
            budget=budget,
            current_plan=plan,
            plan_version=1,
        ),
        now=datetime(2026, 8, 27, 12, 0),
    )


def _compound_runtime() -> RuntimeContext:
    return _runtime(
        _item(
            "temple",
            "静安寺",
            PlaceCategory.ATTRACTION,
            "景点",
            order=1,
        ),
        _item(
            "restaurant",
            "ShakeShack",
            PlaceCategory.RESTAURANT,
            "西餐",
            order=2,
            meal_type="dinner",
            time_label="晚餐",
        ),
        _item(
            "movie",
            "百美汇电影院",
            PlaceCategory.ENTERTAINMENT,
            "电影院",
            order=3,
            time_label="下午",
        ),
    )


def _semantic_budget(value: int, source_span: str) -> DatePlanOperation:
    return DatePlanOperation(
        type=DateOperationType.UPDATE_CONSTRAINT,
        constraint_field=DateConstraintField.BUDGET,
        constraint_value=value,
        source_span=source_span,
    )


def _compound_semantic_result() -> DateSemanticParseResult:
    return DateSemanticParseResult(
        operations=[
            _semantic_budget(800, "预算从600提高到800"),
            DatePlanOperation(
                type=DateOperationType.REPLACE_STOP,
                target=StopReference(ordinal=2),
                payload=DesiredDateStop(
                    kind=StopKind.DINING,
                    generic_replacement=True,
                    replacement_preferences=[DateReplacementPreference.NEARBY],
                ),
                source_span="第二个地方换个近一点的",
            ),
            DatePlanOperation(
                type=DateOperationType.MOVE_STOP,
                target=StopReference(keyword="电影院"),
                payload=DesiredDateStop(
                    kind=StopKind.ACTIVITY,
                    keyword="电影院",
                    after=TemporalAnchor.DINNER,
                ),
                source_span="电影放晚饭后",
            ),
        ]
    )


def _verify_operation(
    operation: DatePlanOperation,
    text: str,
    *,
    runtime: RuntimeContext | None = None,
) -> tuple[list[DatePlanOperation], list[str]]:
    result = DateOperationVerifier().verify(
        [operation],
        text,
        runtime,
        DatePlanPatch(),
        allow_semantic_constraint_corrections=True,
    )
    return list(result.accepted), [item.reason for item in result.rejected]


def _verify_operations(
    operations: list[DatePlanOperation],
    text: str,
) -> tuple[list[DatePlanOperation], list[str]]:
    result = DateOperationVerifier().verify(
        operations,
        text,
        None,
        DatePlanPatch(),
        allow_semantic_constraint_corrections=True,
    )
    return list(result.accepted), [item.reason for item in result.rejected]


def test_semantic_scalar_requires_value_in_its_own_source_span() -> None:
    operation = _semantic_budget(900, "预算从600提高到800")

    accepted, rejected = _verify_operation(
        operation,
        "预算从600提高到800，再加一个电影",
    )

    assert accepted == []
    assert rejected == ["constraint_not_in_deterministic_patch"]


@pytest.mark.parametrize(
    ("payload", "source_span"),
    [
        (
            DesiredDateStop(
                kind=StopKind.ACTIVITY,
                keyword="博物馆",
                meal_type=MealType.DINNER,
            ),
            "加一个博物馆",
        ),
        (
            DesiredDateStop(
                kind=StopKind.ACTIVITY,
                keyword="博物馆",
                after=TemporalAnchor.LUNCH,
            ),
            "加一个博物馆",
        ),
        (
            DesiredDateStop(
                kind=StopKind.ACTIVITY,
                keyword="博物馆",
                before=TemporalAnchor.DINNER,
            ),
            "加一个博物馆",
        ),
    ],
)
def test_semantic_payload_roles_require_source_span_evidence(
    payload: DesiredDateStop,
    source_span: str,
) -> None:
    operation = DatePlanOperation(
        type=DateOperationType.ADD_STOP,
        payload=payload,
        source_span=source_span,
    )

    accepted, rejected = _verify_operation(operation, source_span)

    assert accepted == []
    assert rejected == ["payload_modifier_without_source_evidence"]


@pytest.mark.parametrize(
    ("payload", "source_span"),
    [
        (
            DesiredDateStop(
                kind=StopKind.DINING,
                keyword="火锅",
                meal_type=MealType.DINNER,
            ),
            "晚餐吃火锅",
        ),
        (
            DesiredDateStop(
                kind=StopKind.ACTIVITY,
                keyword="电影院",
                after=TemporalAnchor.DINNER,
            ),
            "晚饭后看电影",
        ),
        (
            DesiredDateStop(
                kind=StopKind.ACTIVITY,
                keyword="公园",
                target_day=2,
            ),
            "第二天去公园",
        ),
        (
            DesiredDateStop(
                kind=StopKind.ACTIVITY,
                keyword="公园",
                time_window=TimeWindow(label="下午"),
            ),
            "下午去公园",
        ),
    ],
)
def test_semantic_payload_roles_accept_explicit_source_span_evidence(
    payload: DesiredDateStop,
    source_span: str,
) -> None:
    operation = DatePlanOperation(
        type=DateOperationType.ADD_STOP,
        payload=payload,
        source_span=source_span,
    )

    accepted, rejected = _verify_operation(operation, source_span)

    assert accepted == [operation]
    assert rejected == []


@pytest.mark.parametrize(
    ("source_span", "accepted_count", "reasons"),
    [
        ("火锅或者烧烤", 2, []),
        (
            "火锅和烧烤",
            0,
            [
                "alternative_group_without_source_evidence",
                "alternative_group_without_source_evidence",
            ],
        ),
    ],
)
def test_alternative_group_requires_explicit_choice_semantics(
    source_span: str,
    accepted_count: int,
    reasons: list[str],
) -> None:
    operations = [
        DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=DesiredDateStop(kind=StopKind.DINING, keyword=keyword),
            alternative_group="meal-choice",
            source_span=source_span,
        )
        for keyword in ("火锅", "烧烤")
    ]

    accepted, rejected = _verify_operations(operations, source_span)

    assert len(accepted) == accepted_count
    assert rejected == reasons


def test_alternative_group_uses_bounded_batch_evidence_for_shared_anchor() -> None:
    text = "午饭后我想去博物馆，海洋馆也行，晚饭后再帮我安排一个景点。"
    operations = [
        DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=DesiredDateStop(
                kind=StopKind.ACTIVITY,
                keyword=keyword,
                after=TemporalAnchor.LUNCH,
            ),
            alternative_group="museum-or-aquarium",
            source_span=source_span,
        )
        for keyword, source_span in (
            ("博物馆", "午饭后我想去博物馆"),
            ("海洋馆", "海洋馆也行"),
        )
    ]
    operations.append(
        DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=DesiredDateStop(
                kind=StopKind.ACTIVITY,
                keyword="景点",
                after=TemporalAnchor.DINNER,
                time_window=TimeWindow(label="晚饭后"),
            ),
            source_span="晚饭后再帮我安排一个景点",
        )
    )

    accepted, rejected = _verify_operations(operations, text)

    assert accepted == operations
    assert rejected == []


def test_verified_alternative_refines_deterministic_singleton() -> None:
    text = "午饭后我想去博物馆，海洋馆也行，晚饭后再帮我安排一个景点。"
    semantic = [
        DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=DesiredDateStop(
                kind=StopKind.ACTIVITY,
                keyword=keyword,
                after=TemporalAnchor.LUNCH,
            ),
            alternative_group="museum-or-aquarium",
            source_span=source_span,
        )
        for keyword, source_span in (
            ("博物馆", "午饭后我想去博物馆"),
            ("海洋馆", "海洋馆也行"),
        )
    ]
    semantic.append(
        DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=DesiredDateStop(
                kind=StopKind.ACTIVITY,
                keyword="景点",
                after=TemporalAnchor.DINNER,
                time_window=TimeWindow(label="晚饭后"),
            ),
            source_span="晚饭后再帮我安排一个景点",
        )
    )
    resolver = DateOperationResolver()
    deterministic = resolver.resolve(text, None, DatePlanPatch())

    assert not deterministic_date_parse_is_complete(text, None, deterministic)

    resolved = resolver.resolve(
        text,
        None,
        DatePlanPatch(),
        proposed_operations=semantic,
    )
    requirements = DateRequirementProjector().apply_requirement_operations(
        [],
        resolved.operations,
        source_text=text,
    )

    grouped = [
        operation
        for operation in resolved.operations
        if operation.alternative_group == "museum-or-aquarium"
    ]
    assert [operation.payload.keyword for operation in grouped if operation.payload] == [
        "博物馆",
        "海洋馆",
    ]
    assert sum(
        operation.payload is not None and operation.payload.keyword == "博物馆"
        for operation in resolved.operations
    ) == 1
    assert deterministic_date_parse_is_complete(text, None, resolved)
    assert len(requirements) == 2
    assert [item.keyword for item in requirements[0].alternatives] == [
        "博物馆",
        "海洋馆",
    ]
    assert requirements[0].alternatives[1].after == TemporalAnchor.LUNCH
    assert requirements[1].alternatives[0].after == TemporalAnchor.DINNER


def test_alternative_group_cannot_cross_a_sentence_boundary() -> None:
    text = "火锅也行。烧烤必须要"
    operations = [
        DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=DesiredDateStop(kind=StopKind.DINING, keyword=keyword),
            alternative_group="invalid-cross-sentence",
            source_span=source_span,
        )
        for keyword, source_span in (("火锅", "火锅也行"), ("烧烤", "烧烤必须要"))
    ]

    accepted, rejected = _verify_operations(operations, text)

    assert accepted == []
    assert rejected == [
        "alternative_group_without_source_evidence",
        "alternative_group_without_source_evidence",
    ]


def test_alternative_group_rejects_conflicting_local_temporal_modifier() -> None:
    text = "午饭后吃火锅或者晚饭后吃烧烤"
    operations = [
        DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=DesiredDateStop(
                kind=StopKind.DINING,
                keyword=keyword,
                after=TemporalAnchor.LUNCH,
            ),
            alternative_group="conflicting-meals",
            source_span=source_span,
        )
        for keyword, source_span in (
            ("火锅", "午饭后吃火锅"),
            ("烧烤", "晚饭后吃烧烤"),
        )
    ]

    accepted, rejected = _verify_operations(operations, text)

    assert accepted == []
    assert rejected == [
        "alternative_group_member_rejected",
        "payload_modifier_without_source_evidence",
    ]


@pytest.mark.parametrize(
    ("current", "incoming"),
    [
        (
            DesiredDateStop(kind=StopKind.ACTIVITY, keyword="博物馆", target_day=2),
            DesiredDateStop(kind=StopKind.ACTIVITY, keyword="博物馆"),
        ),
        (
            DesiredDateStop(
                kind=StopKind.DINING,
                keyword="火锅",
                meal_type=MealType.LUNCH,
            ),
            DesiredDateStop(kind=StopKind.DINING, keyword="火锅"),
        ),
        (
            DesiredDateStop(
                kind=StopKind.ACTIVITY,
                keyword="博物馆",
                after=TemporalAnchor.LUNCH,
            ),
            DesiredDateStop(kind=StopKind.ACTIVITY, keyword="博物馆"),
        ),
        (
            DesiredDateStop(
                kind=StopKind.ACTIVITY,
                keyword="博物馆",
                time_window=TimeWindow(label="下午"),
            ),
            DesiredDateStop(kind=StopKind.ACTIVITY, keyword="博物馆"),
        ),
        (
            DesiredDateStop(kind=StopKind.ACTIVITY, place_name="博物馆 A"),
            DesiredDateStop(kind=StopKind.ACTIVITY, keyword="博物馆 A"),
        ),
        (
            DesiredDateStop(
                kind=StopKind.ACTIVITY,
                keyword="博物馆",
                replacement_preferences=[DateReplacementPreference.NEARBY],
            ),
            DesiredDateStop(kind=StopKind.ACTIVITY, keyword="博物馆"),
        ),
    ],
    ids=(
        "target-day",
        "meal-type",
        "temporal-anchor",
        "time-window",
        "place-name",
        "replacement-preference",
    ),
)
def test_grouped_refinement_cannot_drop_deterministic_payload_fields(
    current: DesiredDateStop,
    incoming: DesiredDateStop,
) -> None:
    text = "博物馆 A"
    deterministic = DatePlanOperation(
        type=DateOperationType.ADD_STOP,
        payload=current,
        source_span=text,
    )
    grouped = DatePlanOperation(
        type=DateOperationType.ADD_STOP,
        payload=incoming,
        alternative_group="semantic-choice",
        source_span=text,
    )

    resolved = _prefer_grouped_additions(
        [deterministic, grouped],
        [deterministic],
        text,
    )

    assert resolved == [deterministic, grouped]


def test_one_group_id_cannot_join_two_independent_choices() -> None:
    text = "火锅或者烧烤，日料或者西餐"
    operations = [
        DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=DesiredDateStop(kind=StopKind.DINING, keyword=keyword),
            alternative_group="incorrect-shared-group",
            source_span=source_span,
        )
        for keyword, source_span in (
            ("火锅", "火锅或者烧烤"),
            ("烧烤", "火锅或者烧烤"),
            ("日料", "日料或者西餐"),
            ("西餐", "日料或者西餐"),
        )
    ]

    accepted, rejected = _verify_operations(operations, text)

    assert accepted == []
    assert rejected == ["alternative_group_without_source_evidence"] * 4


def test_alternative_group_is_rejected_atomically() -> None:
    text = "不想吃火锅，烧烤也行"
    operations = [
        DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=DesiredDateStop(kind=StopKind.DINING, keyword=keyword),
            alternative_group="negated-choice",
            source_span=source_span,
        )
        for keyword, source_span in (
            ("火锅", "不想吃火锅"),
            ("烧烤", "烧烤也行"),
        )
    ]

    accepted, rejected = _verify_operations(operations, text)

    assert accepted == []
    assert rejected == ["add_negated_in_source", "alternative_group_member_rejected"]


def test_each_explicit_choice_requires_its_own_complete_group() -> None:
    text = "火锅或者烧烤，博物馆或者海洋馆"
    semantic = [
        DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=DesiredDateStop(kind=StopKind.DINING, keyword=keyword),
            alternative_group="meal-choice",
            source_span="火锅或者烧烤",
        )
        for keyword in ("火锅", "烧烤")
    ]

    resolved = DateOperationResolver().resolve(
        text,
        None,
        DatePlanPatch(),
        proposed_operations=semantic,
    )

    assert not deterministic_date_parse_is_complete(text, None, resolved)


def test_grouped_refinement_does_not_delete_independent_mandatory_stop() -> None:
    text = "必须吃火锅，火锅或者烧烤"
    semantic = [
        DatePlanOperation(
            type=DateOperationType.ADD_STOP,
            payload=DesiredDateStop(kind=StopKind.DINING, keyword=keyword),
            alternative_group="later-choice",
            source_span="火锅或者烧烤",
        )
        for keyword in ("火锅", "烧烤")
    ]

    resolved = DateOperationResolver().resolve(
        text,
        None,
        DatePlanPatch(),
        proposed_operations=semantic,
    )

    hotpot_operations = [
        operation
        for operation in resolved.operations
        if operation.payload is not None and operation.payload.keyword == "火锅"
    ]
    assert len(hotpot_operations) == 2
    assert {operation.alternative_group for operation in hotpot_operations} == {
        None,
        "later-choice",
    }


def test_non_stop_flexibility_cue_does_not_require_alternative_group() -> None:
    text = "时间都可以，想吃火锅"
    resolved = DateOperationResolver().resolve(text, None, DatePlanPatch())

    assert deterministic_date_parse_is_complete(text, None, resolved)


def test_semantic_nearby_preference_requires_source_span_evidence() -> None:
    source_span = "第二个地方换一个"
    operation = DatePlanOperation(
        type=DateOperationType.REPLACE_STOP,
        target=StopReference(ordinal=2),
        payload=DesiredDateStop(
            kind=StopKind.DINING,
            generic_replacement=True,
            replacement_preferences=[DateReplacementPreference.NEARBY],
        ),
        source_span=source_span,
    )

    accepted, rejected = _verify_operation(
        operation,
        source_span,
        runtime=_compound_runtime(),
    )

    assert accepted == []
    assert rejected == ["payload_modifier_without_source_evidence"]


def test_negated_semantic_add_is_rejected() -> None:
    source_span = "不要去博物馆"
    operation = DatePlanOperation(
        type=DateOperationType.ADD_STOP,
        payload=DesiredDateStop(kind=StopKind.ACTIVITY, keyword="博物馆"),
        source_span=source_span,
    )

    accepted, rejected = _verify_operation(operation, source_span)

    assert accepted == []
    assert rejected == ["add_negated_in_source"]


def test_nearby_adjective_is_not_mistaken_for_a_negated_add() -> None:
    source_span = "想加个不要太远的咖啡馆"
    operation = DatePlanOperation(
        type=DateOperationType.ADD_STOP,
        payload=DesiredDateStop(kind=StopKind.CAFE, keyword="咖啡馆"),
        source_span=source_span,
    )

    accepted, rejected = _verify_operation(operation, source_span)

    assert accepted == [operation]
    assert rejected == []


def test_vague_reference_cannot_be_made_unique_by_model_place_id() -> None:
    source_span = "那个地方换成博物馆"
    operation = DatePlanOperation(
        type=DateOperationType.REPLACE_STOP,
        target=StopReference(place_id="movie"),
        payload=DesiredDateStop(kind=StopKind.ACTIVITY, keyword="博物馆"),
        source_span=source_span,
    )

    accepted, rejected = _verify_operation(
        operation,
        source_span,
        runtime=_compound_runtime(),
    )

    assert accepted == []
    assert rejected == ["target_without_source_or_unique_context_evidence"]


def test_meal_role_narrows_a_generic_restaurant_reference() -> None:
    runtime = _runtime(
        _item(
            "lunch",
            "午餐餐厅 A",
            PlaceCategory.RESTAURANT,
            "日料",
            order=1,
            meal_type="lunch",
        ),
        _item(
            "dinner",
            "晚餐餐厅 B",
            PlaceCategory.RESTAURANT,
            "西餐",
            order=2,
            meal_type="dinner",
        ),
    )
    source_span = "把午餐餐厅换成火锅"
    operation = DatePlanOperation(
        type=DateOperationType.REPLACE_STOP,
        target=StopReference(place_id="lunch", meal_type=MealType.LUNCH),
        payload=DesiredDateStop(kind=StopKind.DINING, keyword="火锅"),
        source_span=source_span,
    )

    accepted, rejected = _verify_operation(
        operation,
        source_span,
        runtime=runtime,
    )

    assert accepted == [operation]
    assert rejected == []


def test_generic_category_does_not_fall_back_to_an_incompatible_unique_item() -> None:
    runtime = _runtime(
        _item(
            "restaurant",
            "测试餐厅",
            PlaceCategory.RESTAURANT,
            "西餐",
            order=1,
        )
    )
    source_span = "把景点换成博物馆"
    operation = DatePlanOperation(
        type=DateOperationType.REPLACE_STOP,
        target=StopReference(place_id="restaurant", keyword="景点"),
        payload=DesiredDateStop(kind=StopKind.ACTIVITY, keyword="博物馆"),
        source_span=source_span,
    )

    accepted, rejected = _verify_operation(
        operation,
        source_span,
        runtime=runtime,
    )

    assert accepted == []
    assert rejected == ["target_without_source_or_unique_context_evidence"]


@pytest.mark.parametrize(
    "target",
    [
        StopReference(ordinal=2),
        StopReference(keyword="电影院"),
    ],
)
def test_explicit_ordinal_and_name_references_remain_authorized(
    target: StopReference,
) -> None:
    target_text = "第二个地方" if target.ordinal is not None else "电影院"
    source_span = f"把{target_text}换成博物馆"
    operation = DatePlanOperation(
        type=DateOperationType.REPLACE_STOP,
        target=target,
        payload=DesiredDateStop(kind=StopKind.ACTIVITY, keyword="博物馆"),
        source_span=source_span,
    )

    accepted, rejected = _verify_operation(
        operation,
        source_span,
        runtime=_compound_runtime(),
    )

    assert accepted == [operation]
    assert rejected == []


def test_date_semantic_settings_are_independent_from_main_and_router_models() -> None:
    settings = Settings(
        _env_file=None,
        llm_model="deepseek-v4-pro",
        router_model="router-flash",
        date_semantic_model="date-flash",
    )

    assert settings.llm_model == "deepseek-v4-pro"
    assert settings.router_model == "router-flash"
    assert settings.date_semantic_model == "date-flash"
    assert settings.date_semantic_thinking == "disabled"
    assert settings.date_semantic_prompt_version == "date-semantic-v1.1"


def test_date_semantic_settings_load_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOVEAPP_LLM_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("LOVEAPP_ROUTER_MODEL", "router-flash")
    monkeypatch.setenv("LOVEAPP_DATE_SEMANTIC_MODEL", "date-flash")

    settings = Settings(_env_file=None)

    assert settings.llm_model == "deepseek-v4-pro"
    assert settings.router_model == "router-flash"
    assert settings.date_semantic_model == "date-flash"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("date_model", "router_model", "main_model", "expected"),
    [
        ("date-flash", "router-flash", "main-pro", "date-flash"),
        ("", "router-flash", "main-pro", "router-flash"),
        ("", "", "main-pro", "main-pro"),
    ],
)
async def test_date_semantic_model_fallback_order(
    date_model: str,
    router_model: str,
    main_model: str,
    expected: str,
) -> None:
    parser = _build_date_semantic_parser(
        Settings(
            _env_file=None,
            llm_provider="deepseek",
            llm_model=main_model,
            llm_api_key=SecretStr("test-key"),
            llm_base_url="https://example.invalid",
            router_model=router_model,
            date_semantic_provider="llm",
            date_semantic_model=date_model,
        )
    )
    assert isinstance(parser, OpenAICompatibleDateSemanticParser)
    try:
        assert parser.semantic_profile["model"] == expected
    finally:
        await parser.aclose()


@pytest.mark.asyncio
async def test_router_and_date_semantic_builders_create_distinct_adapters() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_model="main-pro",
        llm_api_key=SecretStr("test-key"),
        llm_base_url="https://example.invalid",
        router_provider="llm",
        router_model="router-flash",
        date_semantic_provider="llm",
        date_semantic_model="date-flash",
    )
    corrector = _build_route_corrector(settings)
    parser = _build_date_semantic_parser(settings)
    assert isinstance(corrector, OpenAICompatibleRouteCorrector)
    assert isinstance(parser, OpenAICompatibleDateSemanticParser)
    assert corrector is not parser
    assert not hasattr(corrector, "parse_date_operations")
    try:
        assert corrector._model == "router-flash"
        assert parser.semantic_profile["model"] == "date-flash"
    finally:
        await parser.aclose()
        await corrector.aclose()


def test_explicit_date_semantic_llm_configuration_fails_fast_without_credentials() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_model="main-pro",
        date_semantic_provider="llm",
        date_semantic_model="date-flash",
    )

    with pytest.raises(ValueError, match="LOVEAPP_LLM_API_KEY"):
        _build_date_semantic_parser(settings)


@pytest.mark.asyncio
async def test_openai_date_semantic_adapter_uses_flash_structured_non_thinking_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = json.dumps(
        {
            "operations": [
                _semantic_budget(800, "预算从600提高到800").model_dump(mode="json")
            ],
            "unresolved_references": [],
        },
        ensure_ascii=False,
    )
    completions = _FakeCompletions(content)
    _FakeOpenAI.completions = completions
    monkeypatch.setattr(adapter_module, "AsyncOpenAI", _FakeOpenAI)
    parser = OpenAICompatibleDateSemanticParser(
        api_key=SecretStr("test-key"),
        base_url="https://example.invalid",
        model="deepseek-v4-flash",
        timeout_seconds=20,
        max_retries=0,
        max_tokens=2048,
        thinking="disabled",
        prompt_version="date-semantic-v1.1",
    )
    runtime = _compound_runtime()

    try:
        result = await parser.parse_date_operations(
            "预算从600提高到800",
            runtime,
            (),
        )
    finally:
        await parser.aclose()

    request = completions.requests[0]
    assert request["model"] == "deepseek-v4-flash"
    assert request["temperature"] == 0
    assert request["response_format"] == {"type": "json_object"}
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert result.operations[0].constraint_value == 800
    payload = json.loads(request["messages"][1]["content"])
    assert payload["date_context"]["budget"] == 600
    assert payload["date_context"]["current_plan"]["items"][1]["ordinal"] == 2
    assert "user_id" not in payload["date_context"]
    assert parser.last_telemetry == {
        "model": "deepseek-v4-flash",
        "thinking": "disabled",
        "prompt_version": "date-semantic-v1.1",
        "input_tokens": 61,
        "output_tokens": 17,
        "duration_ms": parser.last_telemetry["duration_ms"],
    }


@pytest.mark.asyncio
async def test_date_semantic_telemetry_is_isolated_per_concurrent_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completions = _ConcurrentFakeCompletions()
    _FakeOpenAI.completions = completions  # type: ignore[assignment]
    monkeypatch.setattr(adapter_module, "AsyncOpenAI", _FakeOpenAI)
    parser = OpenAICompatibleDateSemanticParser(
        api_key=SecretStr("test-key"),
        base_url="https://example.invalid",
        model="deepseek-v4-flash",
    )
    parsed = asyncio.Queue[str]()
    read_telemetry = asyncio.Event()

    async def parse_and_read(text: str) -> dict[str, object]:
        await parser.parse_date_operations(text, None, ())
        await parsed.put(text)
        await read_telemetry.wait()
        return dict(parser.last_telemetry)

    first = asyncio.create_task(parse_and_read("first request"))
    second = asyncio.create_task(parse_and_read("second request"))
    try:
        await parsed.get()
        await parsed.get()
        read_telemetry.set()
        first_telemetry, second_telemetry = await asyncio.gather(first, second)
    finally:
        read_telemetry.set()
        await parser.aclose()

    assert first_telemetry["input_tokens"] == 101
    assert first_telemetry["output_tokens"] == 11
    assert second_telemetry["input_tokens"] == 202
    assert second_telemetry["output_tokens"] == 22
    assert first_telemetry["duration_ms"] is not None
    assert second_telemetry["duration_ms"] is not None


@pytest.mark.asyncio
async def test_simple_budget_uses_deterministic_fast_path() -> None:
    parser = _StaticSemanticParser(DateSemanticParseResult())
    result = await HybridRouter(
        SafetyPolicy(),
        date_semantic_parser=parser,
    ).route(
        RouteInput(
            latest_query="预算800",
            forced_task=TaskType.DATE_PLANNING,
        )
    )

    assert parser.calls == 0
    assert result.date_patch is not None
    assert result.date_patch.budget == 800
    assert result.date_semantic_llm_used is False
    assert result.date_semantic_fallback_reason == "deterministic_complete"


@pytest.mark.asyncio
async def test_relative_budget_update_uses_new_value_and_verified_provenance() -> None:
    parser = _StaticSemanticParser(
        DateSemanticParseResult(
            operations=[_semantic_budget(800, "预算从600提高到800")]
        )
    )
    result = await HybridRouter(
        SafetyPolicy(),
        date_semantic_parser=parser,
    ).route(
        RouteInput(
            latest_query="预算从600提高到800",
            forced_task=TaskType.DATE_PLANNING,
        )
    )

    assert result.date_patch is not None
    assert result.date_patch.budget == 800
    assert result.date_patch.source_by_field["budget"] == SlotSource.LLM_VERIFIED
    assert 600 not in {
        operation.constraint_value
        for operation in result.date_operations
        if operation.constraint_field == DateConstraintField.BUDGET
    }
    assert result.date_semantic_llm_used is True
    assert result.date_semantic_trigger_reasons == [
        "multiple_numeric_candidates",
        "relative_scalar_update",
    ]


@pytest.mark.asyncio
async def test_compound_partial_parse_is_completed_by_date_semantic_flash() -> None:
    query = "预算从600提高到800，第二个地方换个近一点的，电影放晚饭后。"
    parser = _StaticSemanticParser(_compound_semantic_result())
    result = await HybridRouter(
        SafetyPolicy(),
        date_semantic_parser=parser,
    ).route(
        RouteInput(
            latest_query=query,
            active_task=TaskType.DATE_PLANNING,
            forced_task=TaskType.DATE_PLANNING,
            runtime_context=_compound_runtime(),
        )
    )

    assert parser.calls == 1
    assert result.date_patch is not None
    assert result.date_patch.budget == 800
    assert result.date_semantic_llm_used is True
    assert result.date_semantic_model == "deepseek-v4-flash"
    assert result.date_semantic_input_tokens == 642
    assert result.date_semantic_output_tokens == 187
    assert result.date_semantic_duration_ms == 532
    assert "partial_parse" in result.date_semantic_trigger_reasons
    assert {
        DateOperationType.UPDATE_CONSTRAINT,
        DateOperationType.REPLACE_STOP,
        DateOperationType.MOVE_STOP,
    } <= {operation.type for operation in result.date_operations}
    replacement = next(
        operation
        for operation in result.date_operations
        if operation.type == DateOperationType.REPLACE_STOP
    )
    move = next(
        operation
        for operation in result.date_operations
        if operation.type == DateOperationType.MOVE_STOP
    )
    assert replacement.target == StopReference(ordinal=2)
    assert replacement.payload is not None
    assert replacement.payload.replacement_preferences == [DateReplacementPreference.NEARBY]
    assert move.payload is not None
    assert move.payload.after == TemporalAnchor.DINNER


@pytest.mark.asyncio
async def test_stop_local_area_does_not_overwrite_global_area() -> None:
    query = "晚餐改成一家人均500元以内、评分4.9以上、陆家嘴附近的法餐。"
    operation = DatePlanOperation(
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
        source_span=query,
    )
    parser = _StaticSemanticParser(DateSemanticParseResult(operations=[operation]))

    result = await HybridRouter(
        SafetyPolicy(),
        date_semantic_parser=parser,
    ).route(
        RouteInput(
            latest_query=query,
            active_task=TaskType.DATE_PLANNING,
            forced_task=TaskType.DATE_PLANNING,
            runtime_context=_runtime(
                _item(
                    "restaurant",
                    "现有晚餐",
                    PlaceCategory.RESTAURANT,
                    "西餐",
                    order=1,
                    meal_type="dinner",
                    time_label="晚餐",
                )
            ),
        )
    )

    assert parser.calls == 1
    assert "stop_local_constraints" in result.date_semantic_trigger_reasons
    assert result.date_patch is not None
    assert result.date_patch.area is None
    replacement = next(
        candidate
        for candidate in result.date_operations
        if candidate.type == DateOperationType.REPLACE_STOP
    )
    assert replacement.payload is not None
    assert replacement.payload.constraints == operation.payload.constraints


@pytest.mark.asyncio
async def test_explicit_global_area_survives_stop_local_price_constraint() -> None:
    query = "整个约会都安排在陆家嘴附近，晚餐改成人均500以内的法餐。"
    operation = DatePlanOperation(
        type=DateOperationType.REPLACE_STOP,
        target=StopReference(meal_type=MealType.DINNER),
        payload=DesiredDateStop(
            kind=StopKind.DINING,
            keyword="法餐",
            meal_type=MealType.DINNER,
            constraints=DateStopConstraints(max_cost_per_person=500),
        ),
        source_span=query,
    )
    parser = _StaticSemanticParser(DateSemanticParseResult(operations=[operation]))

    result = await HybridRouter(
        SafetyPolicy(),
        date_semantic_parser=parser,
    ).route(
        RouteInput(
            latest_query=query,
            active_task=TaskType.DATE_PLANNING,
            forced_task=TaskType.DATE_PLANNING,
            runtime_context=_runtime(
                _item(
                    "restaurant",
                    "现有晚餐",
                    PlaceCategory.RESTAURANT,
                    "西餐",
                    order=1,
                    meal_type="dinner",
                    time_label="晚餐",
                )
            ),
        )
    )

    assert result.date_patch is not None
    assert result.date_patch.area == "陆家嘴"


@pytest.mark.asyncio
async def test_deterministic_turn_does_not_reuse_previous_semantic_usage() -> None:
    parser = _StaticSemanticParser(_compound_semantic_result())
    router = HybridRouter(SafetyPolicy(), date_semantic_parser=parser)
    first = await router.route(
        RouteInput(
            latest_query="预算从600提高到800，第二个地方换个近一点的，电影放晚饭后。",
            active_task=TaskType.DATE_PLANNING,
            forced_task=TaskType.DATE_PLANNING,
            runtime_context=_compound_runtime(),
        )
    )
    second = await router.route(
        RouteInput(
            latest_query="预算改为700",
            active_task=TaskType.DATE_PLANNING,
            forced_task=TaskType.DATE_PLANNING,
            runtime_context=_compound_runtime(),
        )
    )

    assert first.date_semantic_llm_used is True
    assert first.date_semantic_input_tokens == 642
    assert parser.calls == 1
    assert second.date_semantic_llm_used is False
    assert second.date_semantic_input_tokens is None
    assert second.date_semantic_output_tokens is None
    assert second.date_semantic_duration_ms is None


@pytest.mark.asyncio
async def test_ambiguous_restaurant_reference_remains_unresolved() -> None:
    runtime = _runtime(
        _item(
            "lunch",
            "午餐餐厅 A",
            PlaceCategory.RESTAURANT,
            "日料",
            order=1,
            meal_type="lunch",
        ),
        _item(
            "dinner",
            "晚餐餐厅 B",
            PlaceCategory.RESTAURANT,
            "西餐",
            order=2,
            meal_type="dinner",
        ),
    )
    parser = _StaticSemanticParser(
        DateSemanticParseResult(
            unresolved_references=["午餐餐厅 A", "晚餐餐厅 B"]
        )
    )
    result = await HybridRouter(SafetyPolicy(), date_semantic_parser=parser).route(
        RouteInput(
            latest_query="那个餐厅换掉",
            active_task=TaskType.DATE_PLANNING,
            forced_task=TaskType.DATE_PLANNING,
            runtime_context=runtime,
        )
    )

    assert result.date_operations == []
    assert result.date_unresolved_references == ["午餐餐厅 A", "晚餐餐厅 B"]
    assert result.needs_clarification is True


@pytest.mark.asyncio
async def test_negated_dinner_change_only_keeps_movie_move() -> None:
    runtime = _compound_runtime()
    move = DatePlanOperation(
        type=DateOperationType.MOVE_STOP,
        target=StopReference(keyword="电影院"),
        payload=DesiredDateStop(
            kind=StopKind.ACTIVITY,
            keyword="电影院",
            after=TemporalAnchor.DINNER,
        ),
        source_span="电影放到晚饭后",
    )
    parser = _StaticSemanticParser(DateSemanticParseResult(operations=[move]))
    result = await HybridRouter(SafetyPolicy(), date_semantic_parser=parser).route(
        RouteInput(
            latest_query="晚餐不要换，电影放到晚饭后",
            active_task=TaskType.DATE_PLANNING,
            forced_task=TaskType.DATE_PLANNING,
            runtime_context=runtime,
        )
    )

    assert [operation.type for operation in result.date_operations] == [
        DateOperationType.MOVE_STOP
    ]


@pytest.mark.asyncio
async def test_semantic_timeout_fails_closed_when_deterministic_parse_is_partial() -> None:
    query = "预算从600提高到800，第二个地方换个近一点的，电影放晚饭后。"
    parser = _FailingSemanticParser(DateSemanticParseResult())
    result = await HybridRouter(SafetyPolicy(), date_semantic_parser=parser).route(
        RouteInput(
            latest_query=query,
            active_task=TaskType.DATE_PLANNING,
            forced_task=TaskType.DATE_PLANNING,
            runtime_context=_compound_runtime(),
        )
    )

    assert result.date_semantic_llm_used is True
    assert result.date_semantic_fallback_reason == "semantic_parse_failed"
    assert result.date_semantic_error == "date semantic timeout"
    assert result.date_operations == []
    assert result.date_unresolved_references
    assert result.needs_clarification is True


@pytest.mark.asyncio
async def test_semantic_failure_keeps_complete_folded_temporal_add() -> None:
    query = "晚饭后再加一个电影院。"
    parser = _FailingSemanticParser(DateSemanticParseResult())

    result = await HybridRouter(
        SafetyPolicy(),
        date_semantic_parser=parser,
    ).route(
        RouteInput(
            latest_query=query,
            active_task=TaskType.DATE_PLANNING,
            forced_task=TaskType.DATE_PLANNING,
        )
    )

    assert parser.calls == 1
    assert result.date_semantic_fallback_reason == "semantic_parse_failed"
    assert len(result.date_operations) == 1
    addition = result.date_operations[0]
    assert addition.type == DateOperationType.ADD_STOP
    assert addition.source_span == "晚饭后再加一个电影院"
    assert addition.payload is not None
    assert addition.payload.keyword == "电影院"
    assert result.date_unresolved_references == []
    assert result.needs_clarification is False


@pytest.mark.asyncio
async def test_semantic_failure_drops_untyped_stop_local_fallback() -> None:
    query = "晚餐改成一家人均500元以内、评分4.9以上、陆家嘴附近的法餐。"
    parser = _FailingSemanticParser(DateSemanticParseResult())

    result = await HybridRouter(
        SafetyPolicy(),
        date_semantic_parser=parser,
    ).route(
        RouteInput(
            latest_query=query,
            active_task=TaskType.DATE_PLANNING,
            forced_task=TaskType.DATE_PLANNING,
        )
    )

    assert parser.calls == 1
    assert result.date_semantic_fallback_reason == "semantic_parse_failed"
    assert result.date_operations == []
    assert result.date_unresolved_references
    assert result.needs_clarification is True


@pytest.mark.asyncio
async def test_valid_but_incomplete_semantic_result_also_fails_closed() -> None:
    query = "预算从600提高到800，第二个地方换个近一点的，电影放晚饭后。"
    parser = _StaticSemanticParser(DateSemanticParseResult())
    result = await HybridRouter(SafetyPolicy(), date_semantic_parser=parser).route(
        RouteInput(
            latest_query=query,
            active_task=TaskType.DATE_PLANNING,
            forced_task=TaskType.DATE_PLANNING,
            runtime_context=_compound_runtime(),
        )
    )

    assert result.date_semantic_fallback_reason == "semantic_result_incomplete"
    assert result.date_operations == []
    assert result.date_unresolved_references
    assert result.needs_clarification is True


@pytest.mark.asyncio
async def test_semantic_timeout_keeps_complete_deterministic_constraint() -> None:
    parser = _FailingSemanticParser(DateSemanticParseResult())
    result = await HybridRouter(SafetyPolicy(), date_semantic_parser=parser).route(
        RouteInput(
            latest_query="预算从600提高到800",
            forced_task=TaskType.DATE_PLANNING,
        )
    )

    assert result.date_patch is not None
    assert result.date_patch.budget == 800
    assert result.date_operations
    assert result.date_semantic_fallback_reason == "semantic_parse_failed"
    assert result.date_unresolved_references == []
    assert result.needs_clarification is False


@pytest.mark.asyncio
async def test_verified_semantic_budget_reaches_date_task_state() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="demo",
        rag_backend="memory",
        map_provider="demo",
        memory_backend="memory",
        memory_extraction_provider="disabled",
        date_semantic_provider="disabled",
    )
    container = build_container(settings)
    query = "预算从600提高到800。"
    try:
        initial = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="date-semantic-user",
                relationship_id="date-semantic-relationship",
                conversation_id="date-semantic-conversation",
                query="帮我安排一个静安区约会，预算600。",
            )
        )
        container.router._date_semantic_parser = _StaticSemanticParser(
            DateSemanticParseResult(
                operations=[_semantic_budget(800, "预算从600提高到800")]
            )
        )
        updated = await container.conversation_agent.chat(
            ConversationRequest(
                user_id="date-semantic-user",
                relationship_id="date-semantic-relationship",
                conversation_id="date-semantic-conversation",
                query=query,
                active_task=initial.active_task,
            )
        )
    finally:
        await container.aclose()

    assert initial.date_task_state is not None
    assert initial.date_task_state.budget == 600
    assert updated.date_task_state is not None
    assert updated.date_task_state.budget == 800
    assert updated.route.date_semantic_llm_used is True
    assert updated.route.date_semantic_model == "deepseek-v4-flash"


def test_cli_date_semantic_debug_block_exposes_flash_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    console = Console(record=True, width=120)
    monkeypatch.setattr(cli_module, "console", console)
    route = SimpleNamespace(
        date_semantic_llm_used=True,
        date_semantic_model="deepseek-v4-flash",
        date_semantic_thinking="disabled",
        date_semantic_prompt_version="date-semantic-v1.1",
        date_semantic_input_tokens=642,
        date_semantic_output_tokens=187,
        date_semantic_duration_ms=532.0,
        date_semantic_trigger_reasons=[
            "multiple_operations",
            "relative_scalar_update",
        ],
        date_semantic_fallback_reason=None,
        date_semantic_error=None,
    )

    cli_module._render_date_semantic(route)
    output = console.export_text()

    assert "date_semantic_llm_used" in output
    assert "deepseek-v4-flash" in output
    assert "date_semantic_input_tokens" in output
    assert "date_semantic_output_tokens" in output
    assert "date_semantic_duration_ms" in output
    assert "relative_scalar_update" in output

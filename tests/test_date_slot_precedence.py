from loveapp.application.date_planning.patching import DatePlanPatchApplier
from loveapp.application.routing import HybridRouter, route_by_rules
from loveapp.domain.date_patch import DatePlanPatch
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.enums import DateTaskIntent, TaskType
from loveapp.domain.routing import DatePlanSlots, RouteCorrection, RouteInput
from loveapp.safety import SafetyPolicy


def test_current_turn_patch_has_priority_over_committed_budget() -> None:
    state = DatePlanningTaskState(
        user_id="u",
        relationship_id="r",
        conversation_id="c",
        city="上海",
        budget=300,
    )
    result = DatePlanPatchApplier().apply(state, DatePlanPatch(budget=600))
    assert result.city == "上海"
    assert result.budget == 600


def test_patch_does_not_create_phantom_historical_fields() -> None:
    patch = DatePlanPatch(date=None, budget=600)
    assert patch.city is None
    assert patch.area is None
    assert patch.budget == 600


def test_rule_router_emits_only_latest_turn_values_in_date_patch() -> None:
    state = DatePlanningTaskState(
        user_id="u",
        relationship_id="r",
        conversation_id="c",
        city="上海",
        area="静安区",
        budget=300,
    )

    route = route_by_rules(
        RouteInput(
            latest_query="预算改为600元",
            active_task=TaskType.DATE_PLANNING,
            date_task_state=state,
        )
    )

    assert route.date_plan.city == "上海"  # Legacy compatibility output.
    assert route.date_patch is not None
    assert route.date_patch.budget == 600
    assert route.date_patch.city is None
    assert route.date_patch.area is None


class _EchoingCorrector:
    async def correct(self, route_input, rule_result) -> RouteCorrection:
        del route_input, rule_result
        return RouteCorrection(
            task_type=TaskType.DATE_PLANNING,
            task_confidence=0.99,
            date_intent=DateTaskIntent.SUPPLEMENT,
            date_plan=DatePlanSlots(city="上海", budget=600),
        )

    async def aclose(self) -> None:
        return None


class _AlwaysCorrectingRouter(HybridRouter):
    def _needs_llm_correction(self, route_input, result) -> bool:
        del route_input, result
        return True


async def test_hybrid_router_does_not_promote_task_state_echo_into_patch() -> None:
    state = DatePlanningTaskState(
        user_id="u",
        relationship_id="r",
        conversation_id="c",
        city="上海",
        budget=300,
    )

    route = await _AlwaysCorrectingRouter(SafetyPolicy(), _EchoingCorrector()).route(
        RouteInput(
            latest_query="预算改为600元",
            active_task=TaskType.DATE_PLANNING,
            date_task_state=state,
        )
    )

    assert route.date_patch is not None
    assert route.date_patch.budget == 600
    assert route.date_patch.city is None

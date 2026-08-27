from loveapp.application.date_planning.patching import DatePlanPatchApplier
from loveapp.domain.date_patch import DatePlanPatch
from loveapp.domain.date_task import DatePlanningTaskState


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

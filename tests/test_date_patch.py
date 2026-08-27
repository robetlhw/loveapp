from loveapp.application.date_planning.patching import DatePlanPatchApplier
from loveapp.domain.date_patch import DatePlanPatch, SlotSource
from loveapp.domain.date_task import DatePlanningTaskState


def test_patch_overrides_scalar_without_repeating_committed_fields() -> None:
    current = DatePlanningTaskState(
        user_id="u",
        relationship_id="r",
        conversation_id="c",
        city="上海",
        area="静安区",
        budget=300,
    )
    patch = DatePlanPatch(
        budget=600,
        source_by_field={"budget": SlotSource.RULE},
    )

    candidate = DatePlanPatchApplier().apply(current, patch)

    assert candidate.city == "上海"
    assert candidate.area == "静安区"
    assert candidate.budget == 600
    assert patch.city is None
    assert patch.area is None


def test_patch_appends_and_deduplicates_lists_and_nested_meals() -> None:
    current = DatePlanningTaskState(
        user_id="u",
        relationship_id="r",
        conversation_id="c",
        preferences=["安静"],
        meal_keywords={"lunch": ["日料"]},
    )
    patch = DatePlanPatch(
        preferences=["安静", "展览"],
        meal_keywords={"lunch": ["日料"], "dinner": ["火锅"]},
    )

    candidate = DatePlanPatchApplier().apply(current, patch)

    assert candidate.preferences == ["安静", "展览"]
    assert candidate.meal_keywords == {"lunch": ["日料"], "dinner": ["火锅"]}

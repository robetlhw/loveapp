from loveapp.application.date_planning.location import resolve_date_location
from loveapp.application.routing import extract_date_plan_slots
from loveapp.domain.routing import RouteInput


def test_city_and_area_are_extracted_independently() -> None:
    slots = extract_date_plan_slots(
        RouteInput(latest_query="上海，静安区，预算300元")
    )

    assert slots.city == "上海"
    assert slots.area == "静安区"
    assert slots.budget == 300


def test_unique_area_resolves_city_without_losing_area() -> None:
    slots = extract_date_plan_slots(RouteInput(latest_query="她想去静安区玩玩"))

    assert slots.city == "上海"
    assert slots.area == "静安区"


def test_unknown_area_is_preserved_without_inventing_city() -> None:
    location = resolve_date_location("想去朝阳区玩玩")

    assert location.city is None
    assert location.area == "朝阳区"


def test_shanghai_business_areas_are_resolved_as_date_location_slots() -> None:
    slots = extract_date_plan_slots(RouteInput(latest_query="想在陆家嘴安排晚餐"))

    assert slots.city == "上海"
    assert slots.area == "陆家嘴"

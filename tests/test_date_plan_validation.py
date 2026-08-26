from datetime import date

from loveapp.application.date_planning import DatePlanValidator
from loveapp.domain.date_constraints import build_date_constraints
from loveapp.domain.date_plan import DatePlan, DatePlanItem, DatePlanRequest, Place, Route
from loveapp.domain.enums import PlaceCategory, TransportMode


def _place(place_id: str, name: str, *, tags: list[str] | None = None) -> Place:
    return Place(
        id=place_id,
        name=name,
        city="Shanghai",
        address="address",
        category=PlaceCategory.ATTRACTION,
        tags=tags or [],
        estimated_cost_per_person=100,
        source="test",
    )


def _plan(items: list[DatePlanItem], *, total_cost: int = 200) -> DatePlan:
    return DatePlan(
        title="plan",
        summary="summary",
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 1),
        items=items,
        total_estimated_cost=total_cost,
        total_duration_minutes=120,
        data_source="test",
    )


def _item(
    place: Place,
    *,
    order: int,
    cost: int = 100,
    slot_keyword: str | None = None,
    route: Route | None = None,
) -> DatePlanItem:
    return DatePlanItem(
        order=order,
        place=place,
        duration_minutes=60,
        estimated_cost=cost,
        reason="test",
        scheduled_date=date(2026, 8, 1),
        slot_keyword=slot_keyword,
        route_from_previous=route,
    )


def test_validator_accepts_valid_plan_and_route_warning_is_non_blocking() -> None:
    request = DatePlanRequest(city="Shanghai", date=date(2026, 8, 1), budget=300)
    plan = _plan([_item(_place("museum", "Museum", tags=["museum"]), order=1)])

    result = DatePlanValidator().validate(plan, request, build_date_constraints(request))

    assert result.valid is True


def test_validator_rejects_budget_missing_required_keyword_and_exclusion() -> None:
    request = DatePlanRequest(
        city="Shanghai",
        date=date(2026, 8, 1),
        budget=100,
        activity_keywords=["museum"],
        excluded_keywords=["spicy"],
    )
    plan = _plan([_item(_place("hotpot", "Hotpot", tags=["spicy"]), order=1)], total_cost=200)

    result = DatePlanValidator().validate(plan, request, build_date_constraints(request))

    assert result.valid is False
    assert {issue.code for issue in result.issues} >= {
        "budget_exceeded",
        "required_keyword_missing",
        "hard_exclusion_violated",
    }


def test_validator_rejects_duplicate_poi_bad_order_and_bad_route() -> None:
    first = _place("museum", "Museum")
    second = _place("dining", "Dining")
    bad_route = Route(
        origin_id="wrong",
        destination_id=second.id,
        mode=TransportMode.TRANSIT,
        duration_minutes=10,
        distance_meters=100,
        source="test",
    )
    plan = _plan(
        [
            _item(first, order=1),
            _item(first, order=3, route=bad_route),
            _item(second, order=4),
        ],
        total_cost=300,
    )
    request = DatePlanRequest(city="Shanghai", date=date(2026, 8, 1), budget=500)

    result = DatePlanValidator().validate(plan, request, build_date_constraints(request))

    assert result.valid is False
    assert {issue.code for issue in result.issues} >= {
        "duplicate_poi",
        "non_sequential_order",
        "route_mismatch",
    }

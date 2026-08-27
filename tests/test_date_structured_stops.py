from loveapp.application.date_planning.structured_stops import match_desired_stop
from loveapp.domain.date_operations import (
    DesiredDateStop,
    MealType,
    StopKind,
    StopReference,
    TemporalAnchor,
    TimeWindow,
)
from loveapp.domain.date_plan import DatePlan, DatePlanItem, Place
from loveapp.domain.enums import PlaceCategory

_ALIASES = {"电影院": ("电影院", "电影", "影院")}


def _item(
    item_id: str,
    keyword: str,
    category: PlaceCategory,
    *,
    order: int,
    day_index: int = 1,
    meal_type: str | None = None,
    time_label: str | None = None,
) -> DatePlanItem:
    return DatePlanItem(
        order=order,
        day_index=day_index,
        place=Place(
            id=item_id,
            name=f"测试{keyword}",
            city="上海",
            address="测试地址",
            category=category,
            tags=[keyword],
            estimated_cost_per_person=100,
            source="test",
        ),
        duration_minutes=60,
        estimated_cost=100,
        reason="test",
        meal_type=meal_type,
        time_label=time_label,
        slot_keyword=keyword,
    )


def _plan(*items: DatePlanItem) -> DatePlan:
    return DatePlan(
        title="测试计划",
        summary="测试计划",
        items=list(items),
        total_estimated_cost=sum(item.estimated_cost for item in items),
        total_duration_minutes=sum(item.duration_minutes for item in items),
        data_source="test",
    )


def test_identity_match_compares_keyword_and_stop_kind() -> None:
    wrong_kind = _item(
        "restaurant",
        "电影院",
        PlaceCategory.RESTAURANT,
        order=1,
    )
    desired = DesiredDateStop(
        kind=StopKind.ACTIVITY,
        keyword="电影院",
    )

    assert match_desired_stop(
        _plan(wrong_kind),
        desired,
        keyword_aliases=_ALIASES,
    ) == ()


def test_target_day_scopes_stop_identity() -> None:
    movie = _item(
        "movie",
        "电影院",
        PlaceCategory.ENTERTAINMENT,
        order=1,
        day_index=1,
    )
    desired = DesiredDateStop(
        kind=StopKind.ACTIVITY,
        keyword="电影院",
        target_day=2,
    )

    assert match_desired_stop(
        _plan(movie),
        desired,
        keyword_aliases=_ALIASES,
    ) == ()


def test_meal_role_mismatch_preserves_identity_but_fails_placement() -> None:
    barbecue = _item(
        "barbecue",
        "烧烤",
        PlaceCategory.RESTAURANT,
        order=1,
        meal_type="dinner",
    )
    desired = DesiredDateStop(
        kind=StopKind.DINING,
        keyword="烧烤",
        meal_type=MealType.LUNCH,
    )

    matches = match_desired_stop(_plan(barbecue), desired)

    assert [match.item.place.id for match in matches] == ["barbecue"]
    assert matches[0].placement_satisfied is False


def test_time_window_is_part_of_structured_placement() -> None:
    movie = _item(
        "movie",
        "电影院",
        PlaceCategory.ENTERTAINMENT,
        order=1,
        time_label="下午",
    )
    afternoon = DesiredDateStop(
        kind=StopKind.ACTIVITY,
        keyword="电影院",
        time_window=TimeWindow(label="下午"),
    )
    evening = afternoon.model_copy(update={"time_window": TimeWindow(label="晚上")})

    assert match_desired_stop(
        _plan(movie),
        afternoon,
        keyword_aliases=_ALIASES,
    )[0].placement_satisfied is True
    assert match_desired_stop(
        _plan(movie),
        evening,
        keyword_aliases=_ALIASES,
    )[0].placement_satisfied is False


def test_after_dinner_requires_relative_order_not_only_keyword_presence() -> None:
    movie_before = _item(
        "movie",
        "电影院",
        PlaceCategory.ENTERTAINMENT,
        order=1,
    )
    dinner = _item(
        "dinner",
        "西餐",
        PlaceCategory.RESTAURANT,
        order=2,
        meal_type="dinner",
    )
    movie_after = movie_before.model_copy(update={"order": 3})
    desired = DesiredDateStop(
        kind=StopKind.ACTIVITY,
        keyword="电影院",
        time_window=TimeWindow(label="晚饭后"),
        after=TemporalAnchor.DINNER,
    )

    before_match = match_desired_stop(
        _plan(movie_before, dinner),
        desired,
        keyword_aliases=_ALIASES,
    )[0]
    after_match = match_desired_stop(
        _plan(dinner, movie_after),
        desired,
        keyword_aliases=_ALIASES,
    )[0]

    assert before_match.placement_satisfied is False
    assert after_match.placement_satisfied is True


def test_stop_reference_before_and_after_are_verified_structurally() -> None:
    museum = _item(
        "museum",
        "博物馆",
        PlaceCategory.ATTRACTION,
        order=1,
    )
    cafe = _item(
        "cafe",
        "咖啡",
        PlaceCategory.CAFE,
        order=2,
    )
    after_museum = DesiredDateStop(
        kind=StopKind.CAFE,
        keyword="咖啡",
        after=StopReference(keyword="博物馆"),
    )
    before_museum = after_museum.model_copy(
        update={"after": None, "before": StopReference(keyword="博物馆")}
    )

    assert match_desired_stop(
        _plan(museum, cafe),
        after_museum,
    )[0].placement_satisfied is True
    assert match_desired_stop(
        _plan(museum, cafe),
        before_museum,
    )[0].placement_satisfied is False

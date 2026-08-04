from datetime import UTC, date, datetime

from loveapp.adapters.date_tasks import SQLiteDatePlanningTaskStore
from loveapp.domain.date_plan import DatePlan, DatePlanItem, Place
from loveapp.domain.date_task import DatePlanningTaskState
from loveapp.domain.enums import BudgetScope, DatePlanMode, PlaceCategory


async def test_sqlite_date_task_state_survives_store_reopen(tmp_path) -> None:
    database_path = tmp_path / "tasks.db"
    state = DatePlanningTaskState(
        user_id="u1",
        relationship_id="r1",
        conversation_id="c1",
        city="上海",
        budget=500,
        missing_fields=["date_time"],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    first_store = SQLiteDatePlanningTaskStore(database_path)
    await first_store.save(state)
    await first_store.aclose()

    second_store = SQLiteDatePlanningTaskStore(database_path)
    loaded = await second_store.get(
        user_id="u1",
        relationship_id="r1",
        conversation_id="c1",
    )

    assert loaded is not None
    assert loaded.city == "上海"
    assert loaded.budget == 500
    assert loaded.missing_fields == ["date_time"]
    await second_store.aclose()


async def test_sqlite_date_task_state_persists_current_plan_snapshot(tmp_path) -> None:
    database_path = tmp_path / "tasks.db"
    place = Place(
        id="p1",
        name="展览馆",
        city="上海",
        address="静安区",
        category=PlaceCategory.ATTRACTION,
        estimated_cost_per_person=50,
        source="test",
    )
    state = DatePlanningTaskState(
        user_id="u1",
        relationship_id="r1",
        conversation_id="c1",
        current_plan=DatePlan(
            title="上海约会计划",
            summary="保留原行程",
            items=[
                DatePlanItem(
                    order=1,
                    place=place,
                    duration_minutes=90,
                    estimated_cost=100,
                    reason="测试",
                )
            ],
            total_estimated_cost=100,
            total_duration_minutes=90,
            data_source="test",
        ),
        plan_version=2,
    )

    first_store = SQLiteDatePlanningTaskStore(database_path)
    await first_store.save(state)
    await first_store.aclose()

    second_store = SQLiteDatePlanningTaskStore(database_path)
    loaded = await second_store.get(
        user_id="u1",
        relationship_id="r1",
        conversation_id="c1",
    )

    assert loaded is not None
    assert loaded.plan_version == 2
    assert loaded.current_plan is not None
    assert loaded.current_plan.items[0].place.id == "p1"
    await second_store.aclose()


async def test_sqlite_date_task_state_persists_multi_day_fields(tmp_path) -> None:
    database_path = tmp_path / "multi-day-tasks.db"
    state = DatePlanningTaskState(
        user_id="multi-user",
        relationship_id="multi-relationship",
        conversation_id="multi-conversation",
        city="上海",
        plan_mode=DatePlanMode.MULTI_DAY,
        date=date(2026, 8, 7),
        end_date=date(2026, 8, 9),
        day_count=3,
        nights=2,
        target_day=2,
        budget=500,
        budget_scope=BudgetScope.PER_DAY,
        lodging_notes=["希望住在地铁站附近"],
    )

    first_store = SQLiteDatePlanningTaskStore(database_path)
    await first_store.save(state)
    await first_store.aclose()

    second_store = SQLiteDatePlanningTaskStore(database_path)
    loaded = await second_store.get(
        user_id="multi-user",
        relationship_id="multi-relationship",
        conversation_id="multi-conversation",
    )

    assert loaded is not None
    assert loaded.plan_mode == DatePlanMode.MULTI_DAY
    assert loaded.date == date(2026, 8, 7)
    assert loaded.end_date == date(2026, 8, 9)
    assert loaded.day_count == 3
    assert loaded.nights == 2
    assert loaded.target_day == 2
    assert loaded.budget_scope == BudgetScope.PER_DAY
    assert loaded.lodging_notes == ["希望住在地铁站附近"]
    await second_store.aclose()

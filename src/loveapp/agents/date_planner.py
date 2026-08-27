import asyncio
import math
from datetime import timedelta
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from loveapp.application import MemoryService
from loveapp.application.date_planning.mutations import DatePlanMutator
from loveapp.application.date_planning.ranking import DateCandidateOptimizer
from loveapp.core.timing import ExecutionTrace
from loveapp.domain.advice import RelationshipContext
from loveapp.domain.date_operations import (
    DateStopRequirement,
    DesiredDateStop,
    StopKind,
    StopReference,
    TemporalAnchor,
)
from loveapp.domain.date_plan import (
    DatePlan,
    DatePlanDay,
    DatePlanItem,
    DatePlanRequest,
    Place,
    PlaceSearchRequest,
)
from loveapp.domain.enums import DatePlanMode, DatePlanMutation, PlaceCategory
from loveapp.domain.memory import MemoryKind
from loveapp.domain.weather import WeatherForecast, WeatherRequest
from loveapp.ports.maps import MapProvider
from loveapp.ports.weather import WeatherProvider


class DatePlanningState(TypedDict, total=False):
    request: DatePlanRequest
    existing_plan: DatePlan | None
    mutation: DatePlanMutation
    focus_activity_keywords: list[str] | None
    focus_dining_keywords: list[str] | None
    context: RelationshipContext
    effective_preferences: list[str]
    effective_excluded_keywords: list[str]
    activities: list[Place]
    restaurants: list[Place]
    cafes: list[Place]
    response: DatePlan
    weather: WeatherForecast | None
    daily_weather: list[WeatherForecast]
    weather_error: str | None
    trace: ExecutionTrace


class DatePlanningAgent:
    def __init__(
        self,
        map_provider: MapProvider,
        memory_service: MemoryService,
        weather_provider: WeatherProvider | None = None,
    ) -> None:
        self._map_provider = map_provider
        self._memory_service = memory_service
        self._weather_provider = weather_provider
        self._mutator = DatePlanMutator(map_provider)
        self._candidate_optimizer = DateCandidateOptimizer(map_provider)
        self._graph = self._build_graph()

    async def plan(
        self,
        request: DatePlanRequest,
        *,
        trace: ExecutionTrace | None = None,
        existing_plan: DatePlan | None = None,
        mutation: DatePlanMutation = DatePlanMutation.NONE,
        focus_activity_keywords: list[str] | None = None,
        focus_dining_keywords: list[str] | None = None,
    ) -> DatePlan:
        state = await self._graph.ainvoke(
            {
                "request": request,
                "trace": trace or ExecutionTrace(),
                "existing_plan": existing_plan,
                "mutation": mutation,
                "focus_activity_keywords": focus_activity_keywords,
                "focus_dining_keywords": focus_dining_keywords,
            }
        )
        return _normalize_plan_metadata(
            state["response"],
            request,
            state.get("daily_weather", []),
        )

    async def rebuild_plan(
        self,
        existing_plan: DatePlan,
        request: DatePlanRequest,
        items: list[DatePlanItem],
        *,
        summary: str,
        trace: ExecutionTrace | None = None,
    ) -> DatePlan:
        active_trace = trace or ExecutionTrace()
        with active_trace.measure("date_operation_rebuild") as details:
            rebuilt, route_notes = await self._rebuild_plan_items(
                items,
                request.transport_mode,
                preserve_order=True,
            )
            details["item_count"] = len(rebuilt)
            details["route_note_count"] = len(route_notes)
        return _make_plan_from_items(
            existing_plan,
            request,
            rebuilt,
            alternatives=existing_plan.alternatives,
            weather=request.weather,
            notes=[
                _data_source_note(self._map_provider.name),
                *route_notes,
                *request.notes,
                *request.constraints,
            ],
            summary=summary,
        )

    def _build_graph(self):
        graph = StateGraph(DatePlanningState)
        graph.add_node("load_memory", self._load_memory)
        graph.add_node("load_weather", self._load_weather)
        graph.add_node("search_places", self._search_places)
        graph.add_node("build_plan", self._build_plan)
        graph.add_edge(START, "load_memory")
        graph.add_edge("load_memory", "load_weather")
        graph.add_edge("load_weather", "search_places")
        graph.add_edge("search_places", "build_plan")
        graph.add_edge("build_plan", END)
        return graph.compile()

    async def _load_memory(self, state: DatePlanningState) -> dict:
        with state["trace"].measure("date_memory_load"):
            request = state["request"]
            await self._memory_service.remember_date_preferences(
                user_id=request.user_id,
                relationship_id=request.relationship_id,
                preferences=request.preferences,
                relationship_stage=request.relationship_stage,
            )
            context = await self._memory_service.get_context(
                request.user_id,
                request.relationship_id,
                request.relationship_stage,
            )
            memory_preferences, memory_exclusions = _date_memory_preferences(context)
            effective_preferences = list(
                dict.fromkeys(
                    [
                        *request.preferences,
                        *memory_preferences,
                    ]
                )
            )
            effective_excluded_keywords = list(
                dict.fromkeys([*request.excluded_keywords, *memory_exclusions])
            )
            return {
                "context": context,
                "effective_preferences": effective_preferences,
                "effective_excluded_keywords": effective_excluded_keywords,
            }

    async def _load_weather(self, state: DatePlanningState) -> dict:
        request = state["request"]
        if request.weather_forecasts:
            forecasts = sorted(request.weather_forecasts, key=lambda item: item.date)
            return {"weather": forecasts[0], "daily_weather": forecasts}
        if request.weather is not None:
            return {"weather": request.weather, "daily_weather": [request.weather]}
        if self._weather_provider is None or request.date is None or request.city is None:
            return {}
        with state["trace"].measure("weather_lookup") as details:
            dates = [request.date + timedelta(days=index) for index in range(request.day_count)]
            results = await asyncio.gather(
                *(
                    self._weather_provider.forecast(
                        WeatherRequest(city=request.city or "", date=planned_date)
                    )
                    for planned_date in dates
                ),
                return_exceptions=True,
            )
            forecasts: list[WeatherForecast] = []
            errors: list[str] = []
            for planned_date, result in zip(dates, results, strict=True):
                if isinstance(result, Exception):
                    errors.append(f"{planned_date.isoformat()}: {str(result)[:100]}")
                elif result is None:
                    errors.append(f"{planned_date.isoformat()}: 暂无可用预报")
                else:
                    forecasts.append(result)
            details["requested_days"] = len(dates)
            details["available_days"] = len(forecasts)
            if forecasts:
                details["source"] = forecasts[0].source
            if errors:
                details["error_count"] = len(errors)
            return {
                "weather": forecasts[0] if forecasts else None,
                "daily_weather": forecasts,
                "weather_error": "；".join(errors) if errors else None,
            }

    async def _search_places(self, state: DatePlanningState) -> dict:
        with state["trace"].measure("map_search") as details:
            request = state["request"]
            if not request.city:
                details["skipped"] = "missing_city"
                return {"activities": [], "restaurants": [], "cafes": []}
            preferences = state.get("effective_preferences", request.preferences)
            excluded_keywords = state.get("effective_excluded_keywords", request.excluded_keywords)
            focus_dining = state.get("focus_dining_keywords")
            focus_activity = state.get("focus_activity_keywords")
            dining_keywords = (
                _requirement_search_keywords(request, dining=True)
                if focus_dining is None and request.requirements
                else request.dining_keywords
                if focus_dining is None
                else focus_dining
            )
            activity_keywords = (
                _requirement_search_keywords(request, dining=False)
                if focus_activity is None and request.requirements
                else request.activity_keywords
                if focus_activity is None
                else focus_activity
            )
            had_activity_keywords = bool(activity_keywords)
            had_dining_keywords = bool(dining_keywords)
            existing_plan = state.get("existing_plan")
            if existing_plan is not None and state.get("mutation") == DatePlanMutation.ADD:
                comparison_items = [
                    item
                    for item in existing_plan.items
                    if request.target_day is None or item.day_index == request.target_day
                ]
                dining_keywords = [
                    keyword
                    for keyword in dining_keywords
                    if not any(
                        _plan_item_matches_keyword(item, keyword) for item in comparison_items
                    )
                ]
                activity_keywords = [
                    keyword
                    for keyword in activity_keywords
                    if not any(
                        _plan_item_matches_keyword(item, keyword) for item in comparison_items
                    )
                ]
            details["dining_keywords"] = ",".join(dining_keywords) or None
            details["activity_keywords"] = ",".join(activity_keywords) or None
            details["excluded_keywords"] = ",".join(excluded_keywords) or None
            per_person_budget = max(request.effective_daily_budget // 2, 1)

            async def search(
                category: PlaceCategory,
                keywords: list[str | None],
            ) -> list[Place]:
                # Each explicit term describes one possible stop.  Sending
                # all terms as required_keywords would ask the map provider
                # for one place that is simultaneously a cinema, attraction,
                # Japanese restaurant, and hot-pot venue.
                query_keywords = list(dict.fromkeys(keywords)) or [None]
                batches = await asyncio.gather(
                    *(
                        self._map_provider.search_places(
                            PlaceSearchRequest(
                                city=request.city,
                                area=request.area,
                                category=category,
                                preferences=preferences,
                                keywords=[keyword] if keyword else [],
                                required_keywords=[keyword] if keyword else [],
                                excluded_keywords=excluded_keywords,
                                max_cost_per_person=per_person_budget,
                            )
                        )
                        for keyword in query_keywords
                    )
                )
                by_id: dict[str, Place] = {}
                for keyword, places in zip(query_keywords, batches, strict=False):
                    for place in places:
                        previous = by_id.get(place.id)
                        search_keywords = list(
                            dict.fromkeys(
                                [
                                    *(previous.search_keywords if previous else []),
                                    *([keyword] if keyword else []),
                                ]
                            )
                        )
                        by_id[place.id] = place.model_copy(
                            update={"search_keywords": search_keywords}
                        )
                return list(by_id.values())

            explicit_activity = bool(activity_keywords)
            explicit_dining = bool(dining_keywords)
            attraction_keywords = [
                keyword
                for keyword in activity_keywords
                if keyword not in _ENTERTAINMENT_ONLY_KEYWORDS
            ]
            entertainment_keywords = [
                keyword for keyword in activity_keywords if keyword not in _ATTRACTION_ONLY_KEYWORDS
            ]

            async def search_or_empty(
                category: PlaceCategory,
                keywords: list[str],
                *,
                enabled: bool,
            ) -> list[Place]:
                return await search(category, keywords) if enabled else []

            restaurant_search_keywords: list[str | None] = list(dining_keywords)
            if _requires_dinner_anchor(request) and None not in restaurant_search_keywords:
                restaurant_search_keywords.append(None)
            attractions, entertainment, restaurants, cafes = await asyncio.gather(
                search_or_empty(
                    PlaceCategory.ATTRACTION,
                    attraction_keywords,
                    enabled=(
                        bool(attraction_keywords)
                        if explicit_activity
                        else focus_activity is None and not had_activity_keywords
                    ),
                ),
                search_or_empty(
                    PlaceCategory.ENTERTAINMENT,
                    entertainment_keywords,
                    enabled=(
                        bool(entertainment_keywords)
                        if explicit_activity
                        else focus_activity is None and not had_activity_keywords
                    ),
                ),
                search_or_empty(
                    PlaceCategory.RESTAURANT,
                    restaurant_search_keywords,
                    enabled=(explicit_dining or (focus_dining is None and not had_dining_keywords)),
                ),
                search_or_empty(
                    PlaceCategory.CAFE,
                    dining_keywords,
                    enabled=(
                        not explicit_dining and focus_dining is None and not had_dining_keywords
                    ),
                ),
            )
            activities = sorted(
                [*attractions, *entertainment],
                key=lambda place: _activity_score(
                    place,
                    preferences,
                    state.get("weather"),
                    request.constraints,
                    activity_keywords,
                ),
                reverse=True,
            )
            details["activity_count"] = len(activities)
            details["restaurant_count"] = len(restaurants)
            details["cafe_count"] = len(cafes)
            return {"activities": activities, "restaurants": restaurants, "cafes": cafes}

    async def _build_plan(self, state: DatePlanningState) -> dict:
        with state["trace"].measure("date_plan_build"):
            return await self._build_plan_response(state)

    async def _build_plan_response(self, state: DatePlanningState) -> dict:
        request = state["request"]
        existing_plan = state.get("existing_plan")
        mutation = state.get("mutation", DatePlanMutation.NONE)
        activities = state.get("activities", [])
        restaurants = state.get("restaurants", [])
        cafes = state.get("cafes", [])
        preferences = state.get("effective_preferences", request.preferences)
        weather = state.get("weather")
        weather_error = state.get("weather_error")

        if (
            existing_plan is not None
            and existing_plan.items
            and mutation != DatePlanMutation.REPLAN
        ):
            if mutation == DatePlanMutation.ADD:
                return {
                    "response": await self._append_to_existing_plan(
                        state,
                        existing_plan,
                        activities,
                        restaurants,
                        cafes,
                    )
                }
            if mutation == DatePlanMutation.REPLACE:
                return {
                    "response": await self._replace_existing_plan(
                        state,
                        existing_plan,
                        activities,
                        restaurants,
                        cafes,
                    )
                }
            if mutation == DatePlanMutation.REMOVE:
                return {
                    "response": await self._remove_from_existing_plan(
                        state,
                        existing_plan,
                    )
                }
            if mutation == DatePlanMutation.REORDER:
                return {"response": await self._mutator.reorder(existing_plan, request)}
            if mutation == DatePlanMutation.UPDATE_CONSTRAINT:
                updated = self._mutator.update_constraint(existing_plan, request)
                if updated is not None:
                    return {"response": updated}
                mutation = DatePlanMutation.REPLAN
            elif mutation != DatePlanMutation.REPLAN:
                return {
                    "response": _preserve_existing_plan(
                        existing_plan,
                        request,
                        weather=weather,
                        note=(
                            "已保留上一版行程。若要全部重新安排，请明确说“重新规划”；"
                            "修改预算或日期后，我会在现有节点上重新校验。"
                        ),
                    )
                }

        if request.plan_mode == DatePlanMode.MULTI_DAY or request.day_count > 1:
            return {
                "response": await self._build_multi_day_plan(
                    state,
                    activities,
                    restaurants,
                    cafes,
                )
            }

        # A specific cuisine/venue term must not silently fall back to an
        # unrelated cafe. Generic requests may still use cafes as a fallback.
        dining_places = restaurants or (cafes if not request.dining_keywords else [])

        if not activities or not dining_places:
            if not request.city:
                notes = [
                    "尚未提供城市，已忽略真实地点、路线和营业信息。",
                    "可以先按通用流程：选择一个便于交流的室内或轻户外活动，再安排一顿方便聊天的餐食。",
                ]
                if request.date is None and request.start_time is None:
                    notes.append("尚未提供日期/时间，已忽略天气和营业时间校验。")
                if request.budget_is_assumed:
                    notes.append("尚未提供预算，暂按 500 元总预算估算。")
                notes.extend([*request.notes, *request.constraints])
                if weather_error:
                    notes.append(f"天气查询未完成：{weather_error}")
                return {
                    "response": DatePlan(
                        title="约会规划草案",
                        summary="目前先给出通用的约会结构，补充城市后可以继续生成真实地点和路线。",
                        total_estimated_cost=0,
                        total_duration_minutes=180,
                        notes=notes,
                        weather=weather,
                        data_source=self._map_provider.name,
                    )
                }
            return {
                "response": DatePlan(
                    title=f"{request.city}约会计划",
                    summary="没有找到同时满足预算和偏好的活动与餐厅。",
                    total_estimated_cost=0,
                    total_duration_minutes=0,
                    notes=[
                        "建议放宽区域、预算或活动偏好后重新规划。",
                        *(
                            [
                                f"未找到满足精确餐饮条件的地点：{'、'.join(request.dining_keywords)}。"
                            ]
                            if request.dining_keywords and not restaurants
                            else []
                        ),
                        *(
                            [
                                f"未找到满足精确活动条件的地点：{'、'.join(request.activity_keywords)}。"
                            ]
                            if request.activity_keywords and not activities
                            else []
                        ),
                        *([f"天气查询未完成：{weather_error}"] if weather_error else []),
                        *request.notes,
                        *request.constraints,
                    ],
                    weather=weather,
                    data_source=self._map_provider.name,
                )
            }

        if request.activity_keywords or request.dining_keywords:
            keyword_plan = await self._build_keyword_plan(
                state,
                activities,
                restaurants,
                cafes,
            )
            if keyword_plan is not None:
                return {"response": keyword_plan}
            return {
                "response": DatePlan(
                    title=f"{request.city}约会计划",
                    summary="暂时没有找到能同时覆盖全部明确节点且不超预算的完整行程。",
                    alternatives=_select_alternatives(
                        [*activities, *restaurants, *cafes],
                        selected_ids=set(),
                        limit=3,
                    ),
                    total_estimated_cost=0,
                    total_duration_minutes=0,
                    notes=[
                        "我没有用不相关地点替代你明确指定的餐饮或活动类型。",
                        "可以提高预算、放宽区域，或明确删去一个节点后继续规划。",
                        *request.notes,
                        *request.constraints,
                    ],
                    weather=weather,
                    data_source=self._map_provider.name,
                )
            }

        feasible_pairs = [
            (activity, restaurant)
            for activity in activities
            for restaurant in dining_places
            if (activity.estimated_cost_per_person + restaurant.estimated_cost_per_person) * 2
            <= request.effective_total_budget
        ]
        if not feasible_pairs:
            return {
                "response": DatePlan(
                    title=f"{request.city}约会计划",
                    summary="候选地点无法在总预算内组成完整行程。",
                    alternatives=[*activities[:2], *restaurants[:2]],
                    total_estimated_cost=0,
                    total_duration_minutes=0,
                    notes=[
                        "建议提高预算，或减少一个付费活动节点。",
                        *([f"天气查询未完成：{weather_error}"] if weather_error else []),
                        *request.notes,
                        *request.constraints,
                    ],
                    weather=weather,
                    data_source=self._map_provider.name,
                )
            }

        optimized = await self._candidate_optimizer.optimize_pair(
            activities,
            dining_places,
            request,
        )
        if optimized is None:  # guarded by feasible_pairs; keep the fallback deterministic.
            activity, restaurant = max(
                feasible_pairs,
                key=lambda pair: _pair_score(pair, weather, request.constraints),
            )
            route, route_note = None, "候选路线暂不可用。"
        else:
            activity, restaurant = optimized.activity, optimized.dining
            route = optimized.route
            route_note = (
                f"地点已找到，但路线服务暂未返回结果：{optimized.route_error}"
                if optimized.route_error
                else None
            )
        items = [
            DatePlanItem(
                order=1,
                place=activity,
                duration_minutes=90,
                estimated_cost=activity.estimated_cost_per_person * 2,
                reason=_preference_reason(
                    activity,
                    [*preferences, *request.activity_keywords],
                ),
            ),
            DatePlanItem(
                order=2,
                place=restaurant,
                duration_minutes=90,
                estimated_cost=restaurant.estimated_cost_per_person * 2,
                reason=_preference_reason(
                    restaurant,
                    [*preferences, *request.dining_keywords],
                ),
                route_from_previous=route,
            ),
        ]
        total_cost = sum(item.estimated_cost for item in items)
        total_duration = sum(item.duration_minutes for item in items) + (
            route.duration_minutes if route else 0
        )
        alternatives = _select_alternatives(
            [*activities, *restaurants, *cafes],
            selected_ids={activity.id, restaurant.id},
            limit=3,
        )

        return {
            "response": DatePlan(
                title=f"{request.city}{request.area or ''}半日约会计划",
                summary="先安排便于自然交流的活动，再前往餐厅用餐。",
                items=items,
                alternatives=alternatives,
                total_estimated_cost=total_cost,
                total_duration_minutes=total_duration,
                notes=[
                    _data_source_note(self._map_provider.name),
                    f"用户预算为 {request.budget} 元，当前估算为 {total_cost} 元。",
                    *(
                        ["未找到合适餐厅，已使用咖啡馆作为第二个交流节点。"]
                        if not restaurants
                        else []
                    ),
                    *([route_note] if route_note else []),
                    *([f"天气：{_weather_summary(weather)}"] if weather else []),
                    *([f"天气查询未完成：{weather_error}"] if weather_error else []),
                    *(
                        ["未启用天气 provider，未进行天气校验。"]
                        if request.date is not None and weather is None and not weather_error
                        else []
                    ),
                    *request.notes,
                    *request.constraints,
                    *(
                        ["日期/时间未提供，未进行天气和营业时间校验。"]
                        if request.date is None and request.start_time is None
                        else []
                    ),
                    *(["预算未提供，以上按默认 500 元估算。"] if request.budget_is_assumed else []),
                ],
                weather=weather,
                data_source=self._map_provider.name,
            )
        }

    async def _build_multi_day_plan(
        self,
        state: DatePlanningState,
        activities: list[Place],
        restaurants: list[Place],
        cafes: list[Place],
    ) -> DatePlan:
        request = state["request"]
        forecasts = state.get("daily_weather", [])
        weather_by_date = {forecast.date: forecast for forecast in forecasts}
        day_dates = [
            request.date + timedelta(days=index) if request.date is not None else None
            for index in range(request.day_count)
        ]

        if not request.city:
            days = [
                DatePlanDay(
                    day_index=index,
                    date=planned_date,
                    weather=(weather_by_date.get(planned_date) if planned_date else None),
                    lodging_notes=request.lodging_notes if index < request.day_count else [],
                )
                for index, planned_date in enumerate(day_dates, start=1)
            ]
            return DatePlan(
                title=f"{request.day_count} 日约会旅行草案",
                summary="目前先保留逐日结构；补充城市后可以搜索真实地点并计算每天的路线。",
                plan_mode=DatePlanMode.MULTI_DAY,
                start_date=request.date,
                end_date=request.end_date,
                day_count=request.day_count,
                nights=request.nights,
                days=days,
                total_estimated_cost=0,
                total_duration_minutes=0,
                notes=[
                    "尚未提供城市，已忽略真实地点、路线和营业信息。",
                    *request.lodging_notes,
                    *request.notes,
                    *request.constraints,
                ],
                weather=state.get("weather"),
                data_source=self._map_provider.name,
            )

        dining_places = restaurants or (cafes if not request.dining_keywords else [])
        activity_groups = _assign_requirement_groups_to_days(
            request,
            dining=False,
        )
        dining_groups = _assign_requirement_groups_to_days(
            request,
            dining=True,
        )
        activity_assignments = _requirement_group_keywords(activity_groups)
        dining_assignments = _requirement_group_keywords(dining_groups)
        preferences = state.get("effective_preferences", request.preferences)
        used_ids: set[str] = set()
        selected: list[DatePlanItem] = []
        missing_keywords: list[str] = []
        daily_budget = request.effective_daily_budget

        for day_index, planned_date in enumerate(day_dates, start=1):
            day_weather = weather_by_date.get(planned_date) if planned_date else None
            day_items: list[DatePlanItem] = []

            for alternatives in activity_groups.get(day_index, []):
                candidate, desired = _first_matching_alternative(
                    activities,
                    alternatives,
                    used_ids,
                )
                if candidate is None:
                    missing_keywords.append(_requirement_group_label(alternatives))
                    continue
                used_ids.add(candidate.id)
                item = _apply_requirement_role(
                    _make_date_item(
                        candidate,
                        preferences=preferences,
                        activity_keywords=request.activity_keywords,
                        dining_keywords=request.dining_keywords,
                        meal_keywords=request.meal_keywords,
                        schedule_hints=request.schedule_hints,
                        slot_keyword=(
                            desired.keyword or desired.place_name if desired is not None else None
                        ),
                    ),
                    desired,
                )
                day_items.append(
                    item.model_copy(
                        update={"day_index": day_index, "scheduled_date": planned_date}
                    )
                )

            for alternatives in dining_groups.get(day_index, []):
                candidate, desired = _first_matching_alternative(
                    dining_places,
                    alternatives,
                    used_ids,
                )
                if candidate is None:
                    missing_keywords.append(_requirement_group_label(alternatives))
                    continue
                used_ids.add(candidate.id)
                item = _apply_requirement_role(
                    _make_date_item(
                        candidate,
                        preferences=preferences,
                        activity_keywords=request.activity_keywords,
                        dining_keywords=request.dining_keywords,
                        meal_keywords=request.meal_keywords,
                        schedule_hints=request.schedule_hints,
                        slot_keyword=(
                            desired.keyword or desired.place_name if desired is not None else None
                        ),
                    ),
                    desired,
                )
                day_items.append(
                    item.model_copy(
                        update={"day_index": day_index, "scheduled_date": planned_date}
                    )
                )

            has_activity = any(not _is_dining_item(item) for item in day_items)
            has_dining = any(_is_dining_item(item) for item in day_items)
            current_cost = sum(item.estimated_cost for item in day_items)
            # Do not spend a later day's explicitly requested candidate while
            # filling this day's generic activity or dining slot.
            reserved_activity_ids = _reserved_candidate_ids(
                activities,
                activity_assignments,
                day_index,
                used_ids,
            )
            reserved_dining_ids = _reserved_candidate_ids(
                dining_places,
                dining_assignments,
                day_index,
                used_ids,
            )
            available_activities = [
                place
                for place in activities
                if place.id not in used_ids and place.id not in reserved_activity_ids
            ]
            available_dining = [
                place
                for place in dining_places
                if place.id not in used_ids and place.id not in reserved_dining_ids
            ]

            if not has_activity and not has_dining:
                feasible_pairs = [
                    (activity, dining)
                    for activity in available_activities
                    for dining in available_dining
                    if (activity.estimated_cost_per_person + dining.estimated_cost_per_person) * 2
                    <= daily_budget
                ]
                if feasible_pairs:
                    activity, dining = max(
                        feasible_pairs,
                        key=lambda pair: _pair_score(
                            pair,
                            day_weather,
                            request.constraints,
                        ),
                    )
                    used_ids.update({activity.id, dining.id})
                    day_items.extend(
                        [
                            _make_date_item(
                                activity,
                                preferences=preferences,
                                activity_keywords=request.activity_keywords,
                                dining_keywords=request.dining_keywords,
                                meal_keywords=request.meal_keywords,
                                schedule_hints=request.schedule_hints,
                                slot_keyword=None,
                            ).model_copy(
                                update={
                                    "day_index": day_index,
                                    "scheduled_date": planned_date,
                                    "time_label": "下午",
                                }
                            ),
                            _make_date_item(
                                dining,
                                preferences=preferences,
                                activity_keywords=request.activity_keywords,
                                dining_keywords=request.dining_keywords,
                                meal_keywords=request.meal_keywords,
                                schedule_hints=request.schedule_hints,
                                slot_keyword=None,
                            ).model_copy(
                                update={
                                    "day_index": day_index,
                                    "scheduled_date": planned_date,
                                    "meal_type": "dinner",
                                    "time_label": "晚餐",
                                }
                            ),
                        ]
                    )
            else:
                if not has_activity:
                    affordable = [
                        place
                        for place in available_activities
                        if current_cost + place.estimated_cost_per_person * 2 <= daily_budget
                    ]
                    if affordable:
                        activity = max(
                            affordable,
                            key=lambda place: _activity_score(
                                place,
                                preferences,
                                day_weather,
                                request.constraints,
                                request.activity_keywords,
                            ),
                        )
                        used_ids.add(activity.id)
                        day_items.append(
                            _make_date_item(
                                activity,
                                preferences=preferences,
                                activity_keywords=request.activity_keywords,
                                dining_keywords=request.dining_keywords,
                                meal_keywords=request.meal_keywords,
                                schedule_hints=request.schedule_hints,
                                slot_keyword=None,
                            ).model_copy(
                                update={
                                    "day_index": day_index,
                                    "scheduled_date": planned_date,
                                    "time_label": "下午",
                                }
                            )
                        )
                        current_cost += activity.estimated_cost_per_person * 2
                if not has_dining:
                    affordable = [
                        place
                        for place in available_dining
                        if current_cost + place.estimated_cost_per_person * 2 <= daily_budget
                    ]
                    if affordable:
                        dining = max(affordable, key=lambda place: place.rating or 0)
                        used_ids.add(dining.id)
                        day_items.append(
                            _make_date_item(
                                dining,
                                preferences=preferences,
                                activity_keywords=request.activity_keywords,
                                dining_keywords=request.dining_keywords,
                                meal_keywords=request.meal_keywords,
                                schedule_hints=request.schedule_hints,
                                slot_keyword=None,
                            ).model_copy(
                                update={
                                    "day_index": day_index,
                                    "scheduled_date": planned_date,
                                    "meal_type": "dinner",
                                    "time_label": "晚餐",
                                }
                            )
                        )

            selected.extend(day_items)

        rebuilt, route_notes = await self._rebuild_plan_items(
            selected,
            request.transport_mode,
        )
        days = _build_plan_days(rebuilt, request, forecasts)
        total_cost = sum(item.estimated_cost for item in rebuilt)
        weather_error = state.get("weather_error")
        budget_label = (
            f"每天预算 {request.budget} 元"
            if request.budget_scope.value == "per_day"
            else f"总预算 {request.budget} 元"
        )
        empty_days = [day.day_index for day in days if not day.items]
        notes = [
            _data_source_note(self._map_provider.name),
            f"{budget_label}，当前地点费用估算合计 {total_cost} 元。",
            "路线仅在每天内部连接；次日会重新开始计算，不跨夜串联。",
            *(
                [
                    "以下日期没有在当日预算和限制内找到完整节点："
                    + "、".join(f"第 {day_index} 天" for day_index in empty_days)
                    + "。可以提高预算或放宽地点条件。"
                ]
                if empty_days
                else []
            ),
            *(
                ["住宿要求已保留在逐日结构中，本版本暂不自动搜索酒店。"]
                if request.lodging_notes
                else []
            ),
            *(
                [f"以下明确条件暂未找到对应地点：{'、'.join(dict.fromkeys(missing_keywords))}。"]
                if missing_keywords
                else []
            ),
            *route_notes,
            *([f"部分日期天气查询未完成：{weather_error}"] if weather_error else []),
            *request.lodging_notes,
            *request.notes,
            *request.constraints,
        ]
        return DatePlan(
            title=f"{request.city}{request.area or ''}{request.day_count}日约会旅行计划",
            summary=(
                f"行程按 {request.day_count} 天拆分，每天安排一个主要活动和一个用餐节点，"
                "并分别核对预算、天气与当日路线。"
            ),
            plan_mode=DatePlanMode.MULTI_DAY,
            start_date=request.date,
            end_date=request.end_date,
            day_count=request.day_count,
            nights=request.nights,
            days=days,
            items=rebuilt,
            alternatives=_select_alternatives(
                [*activities, *restaurants, *cafes],
                selected_ids={item.place.id for item in rebuilt},
                limit=3,
            ),
            total_estimated_cost=total_cost,
            total_duration_minutes=_plan_duration(rebuilt),
            notes=list(dict.fromkeys(notes)),
            weather=state.get("weather"),
            data_source=self._map_provider.name,
        )

    async def _build_keyword_plan(
        self,
        state: DatePlanningState,
        activities: list[Place],
        restaurants: list[Place],
        cafes: list[Place],
    ) -> DatePlan | None:
        """Build one plan item for each explicit activity/meal constraint."""

        request = state["request"]
        preferences = state.get("effective_preferences", request.preferences)
        activity_requirements = _requirements_for_kind(request, dining=False)
        activity_groups = (
            [requirement.alternatives for requirement in activity_requirements]
            if activity_requirements
            else [[None]]
        )
        dining_places = restaurants or (cafes if not request.dining_keywords else [])
        dining_requirements = _requirements_for_kind(request, dining=True)
        dining_groups = (
            [requirement.alternatives for requirement in dining_requirements]
            if dining_requirements
            else [[None]]
        )
        selections = _select_requirement_group_places(
            [
                *((activities, list(alternatives), False) for alternatives in activity_groups),
                *((dining_places, list(alternatives), True) for alternatives in dining_groups),
            ],
            max_total_cost=(
                None if request.budget_is_assumed else request.effective_total_budget
            ),
        )
        if selections is None:
            return None

        selected: list[DatePlanItem] = []
        used_ids = {candidate.id for candidate, _, _ in selections}
        for candidate, desired, dining in selections:
            item = _make_date_item(
                candidate,
                preferences=preferences,
                activity_keywords=(
                    request.activity_keywords
                    if dining
                    else _requirement_search_keywords(request, dining=False)
                ),
                dining_keywords=(
                    _requirement_search_keywords(request, dining=True)
                    if dining
                    else request.dining_keywords
                ),
                meal_keywords=request.meal_keywords,
                schedule_hints=request.schedule_hints,
                slot_keyword=(desired.keyword or desired.place_name) if desired else None,
            )
            selected.append(_apply_requirement_role(item, desired))

        required_meal_anchors = _required_meal_anchors(request)
        assigned_meal_anchors = {
            item.meal_type for item in selected if _is_dining_item(item)
        }
        missing_meal_anchors = [
            anchor
            for anchor in ("lunch", "dinner")
            if anchor in required_meal_anchors and anchor not in assigned_meal_anchors
        ]
        untyped_dining_count = sum(
            _is_dining_item(item) and item.meal_type is None for item in selected
        )
        for anchor in missing_meal_anchors[untyped_dining_count:]:
            meal_place = _first_matching_place(dining_places, None, used_ids)
            if meal_place is None:
                return None
            used_ids.add(meal_place.id)
            selected.append(
                _make_date_item(
                    meal_place,
                    preferences=preferences,
                    activity_keywords=request.activity_keywords,
                    dining_keywords=request.dining_keywords,
                    meal_keywords=request.meal_keywords,
                    schedule_hints=request.schedule_hints,
                    slot_keyword=None,
                ).model_copy(
                    update={
                        "meal_type": anchor,
                        "time_label": "午餐" if anchor == "lunch" else "晚餐",
                    }
                )
            )

        if (
            not selected
            or (
                not request.budget_is_assumed
                and sum(item.estimated_cost for item in selected) > request.effective_total_budget
            )
        ):
            return None
        selected = _sort_plan_items(selected)
        rebuilt, route_notes = await self._rebuild_plan_items(
            selected,
            request.transport_mode,
        )
        weather = state.get("weather")
        weather_error = state.get("weather_error")
        total_cost = sum(item.estimated_cost for item in rebuilt)
        return DatePlan(
            title=f"{request.city}{request.area or ''}约会计划",
            summary=_keyword_plan_summary(rebuilt),
            items=rebuilt,
            alternatives=_select_alternatives(
                [*activities, *restaurants, *cafes],
                selected_ids=used_ids,
                limit=3,
            ),
            total_estimated_cost=total_cost,
            total_duration_minutes=_plan_duration(rebuilt),
            notes=[
                _data_source_note(self._map_provider.name),
                f"用户预算为 {request.budget} 元，当前估算为 {total_cost} 元。",
                *route_notes,
                *([f"天气：{_weather_summary(weather)}"] if weather else []),
                *([f"天气查询未完成：{weather_error}"] if weather_error else []),
                *request.notes,
                *request.constraints,
            ],
            weather=weather,
            data_source=self._map_provider.name,
        )

    async def _append_to_existing_plan(
        self,
        state: DatePlanningState,
        existing_plan: DatePlan,
        activities: list[Place],
        restaurants: list[Place],
        cafes: list[Place],
    ) -> DatePlan:
        request = state["request"]
        preferences = state.get("effective_preferences", request.preferences)
        focus_activity = state.get("focus_activity_keywords")
        focus_dining = state.get("focus_dining_keywords")
        selected_ids = {item.place.id for item in existing_plan.items}
        target_items = [
            item
            for item in existing_plan.items
            if request.target_day is None or item.day_index == request.target_day
        ]
        requested_activities = (
            focus_activity if focus_activity is not None else request.activity_keywords
        )
        requested_dining = focus_dining if focus_dining is not None else request.dining_keywords
        additions: list[tuple[Place, str | None]] = []
        missing_keywords: list[str] = []

        if requested_activities:
            for keyword in dict.fromkeys(requested_activities):
                candidate = _first_matching_place(activities, keyword, selected_ids)
                if candidate is None:
                    if any(_plan_item_matches_keyword(item, keyword) for item in target_items):
                        continue
                    missing_keywords.append(keyword)
                    continue
                additions.append((candidate, keyword))
                selected_ids.add(candidate.id)
        if requested_dining:
            dining_places = restaurants or (cafes if not request.dining_keywords else [])
            for keyword in dict.fromkeys(requested_dining):
                candidate = _first_matching_place(dining_places, keyword, selected_ids)
                if candidate is None:
                    if any(_plan_item_matches_keyword(item, keyword) for item in target_items):
                        continue
                    missing_keywords.append(keyword)
                    continue
                additions.append((candidate, keyword))
                selected_ids.add(candidate.id)

        if not additions and not requested_activities and not requested_dining:
            fallback_activity = _first_matching_place(activities, None, selected_ids)
            fallback_dining = _first_matching_place(
                [*restaurants, *cafes],
                None,
                selected_ids,
            )
            if fallback_activity is not None:
                additions.append((fallback_activity, None))
                selected_ids.add(fallback_activity.id)
            elif fallback_dining is not None:
                additions.append((fallback_dining, None))
                selected_ids.add(fallback_dining.id)

        if not additions:
            return _preserve_existing_plan(
                existing_plan,
                request,
                weather=state.get("weather"),
                note=(
                    "没有找到不重复的新地点，已保留上一版行程。"
                    + (f"未找到：{'、'.join(missing_keywords)}。" if missing_keywords else "")
                ),
            )

        target_day = _target_day_for_edit(existing_plan, request)
        scheduled_date = (
            request.date + timedelta(days=target_day - 1) if request.date is not None else None
        )
        next_order = (
            max(
                (item.order for item in existing_plan.items if item.day_index == target_day),
                default=0,
            )
            + 1
        )
        new_items = [
            _make_date_item(
                candidate,
                preferences=preferences,
                activity_keywords=request.activity_keywords,
                dining_keywords=request.dining_keywords,
                meal_keywords=request.meal_keywords,
                schedule_hints=request.schedule_hints,
                slot_keyword=keyword,
            ).model_copy(
                update={
                    "order": next_order + index,
                    "day_index": target_day,
                    "scheduled_date": scheduled_date,
                }
            )
            for index, (candidate, keyword) in enumerate(additions)
        ]
        current_cost = sum(item.estimated_cost for item in existing_plan.items)
        added_cost = sum(item.estimated_cost for item in new_items)
        if current_cost + added_cost > request.effective_total_budget:
            return _preserve_existing_plan(
                existing_plan,
                request,
                weather=state.get("weather"),
                note=(
                    "加入新的节点后预计超出预算，已保留原行程；可以提高预算或删除一个节点后再加入。"
                ),
            )

        items = [_annotate_existing_item(item, request) for item in existing_plan.items]
        items.extend(new_items)
        items = _sort_plan_items(items)
        rebuilt, route_notes = await self._rebuild_plan_items(
            items,
            request.transport_mode,
        )
        alternatives = _select_alternatives(
            [
                *existing_plan.alternatives,
                *activities,
                *restaurants,
                *cafes,
            ],
            selected_ids={item.place.id for item in rebuilt},
            limit=3,
        )
        return _make_plan_from_items(
            existing_plan,
            request,
            rebuilt,
            alternatives=alternatives,
            weather=state.get("weather"),
            notes=[
                _data_source_note(self._map_provider.name),
                "已在上一版行程基础上增加："
                + "、".join(item.place.name for item in new_items)
                + "。",
                *(
                    [f"以下条件暂未找到对应地点：{'、'.join(missing_keywords)}。"]
                    if missing_keywords
                    else []
                ),
                *route_notes,
                *request.notes,
                *request.constraints,
            ],
            summary=_incremental_plan_summary(existing_plan, rebuilt, new_items),
        )

    async def _replace_existing_plan(
        self,
        state: DatePlanningState,
        existing_plan: DatePlan,
        activities: list[Place],
        restaurants: list[Place],
        cafes: list[Place],
    ) -> DatePlan:
        request = state["request"]
        focus_activity = state.get("focus_activity_keywords")
        focus_dining = state.get("focus_dining_keywords")
        selected_ids = {item.place.id for item in existing_plan.items}
        target_index = _replacement_target_index(
            existing_plan.items,
            request.replace_place_names,
            target_day=request.target_day,
        )
        if request.replace_place_names and target_index is None:
            return _preserve_existing_plan(
                existing_plan,
                request,
                weather=state.get("weather"),
                note=(
                    f"没有在当前行程中找到要替换的地点：{'、'.join(request.replace_place_names)}。"
                ),
            )

        if target_index is not None:
            replace_dining = _is_dining_item(existing_plan.items[target_index])
        elif focus_activity is not None or focus_dining is not None:
            replace_dining = bool(focus_dining) and not bool(focus_activity)
        else:
            replace_dining = bool(request.dining_keywords)

        if target_index is None:
            target_index = next(
                (
                    index
                    for index, item in enumerate(existing_plan.items)
                    if _is_dining_item(item) == replace_dining
                    and (request.target_day is None or item.day_index == request.target_day)
                    and (
                        not request.schedule_hints
                        or item.time_label is None
                        or item.time_label in request.schedule_hints
                    )
                ),
                None,
            )

        candidates = [*restaurants, *cafes] if replace_dining else activities
        replacement_keywords = (
            (focus_dining if focus_dining is not None else request.dining_keywords)
            if replace_dining
            else (focus_activity if focus_activity is not None else request.activity_keywords)
        )
        replacement_keyword, candidate = _replacement_candidate(
            candidates,
            replacement_keywords,
            selected_ids,
        )
        if candidate is None or target_index is None:
            return _preserve_existing_plan(
                existing_plan,
                request,
                weather=state.get("weather"),
                note="没有找到可替换的地点，已保留上一版行程。",
            )

        replaced_item = existing_plan.items[target_index]
        items = [_annotate_existing_item(item, request) for item in existing_plan.items]
        replacement_item = _make_date_item(
            candidate,
            preferences=request.preferences,
            activity_keywords=request.activity_keywords,
            dining_keywords=request.dining_keywords,
            meal_keywords=request.meal_keywords,
            schedule_hints=request.schedule_hints,
            slot_keyword=replacement_keyword,
        )
        items[target_index] = replacement_item.model_copy(
            update={
                "order": replaced_item.order,
                "day_index": replaced_item.day_index,
                "scheduled_date": replaced_item.scheduled_date,
                "meal_type": replacement_item.meal_type or replaced_item.meal_type,
                "time_label": replacement_item.time_label or replaced_item.time_label,
                "after_item": replacement_item.after_item or replaced_item.after_item,
                "slot_keyword": replacement_item.slot_keyword or replaced_item.slot_keyword,
            }
        )
        if sum(item.estimated_cost for item in items) > request.effective_total_budget:
            return _preserve_existing_plan(
                existing_plan,
                request,
                weather=state.get("weather"),
                note="替换后的行程会超出预算，已保留上一版行程。",
            )
        rebuilt, route_notes = await self._rebuild_plan_items(
            items,
            request.transport_mode,
        )
        alternatives = _select_alternatives(
            [*existing_plan.alternatives, *activities, *restaurants, *cafes],
            selected_ids={item.place.id for item in rebuilt},
            limit=3,
        )
        replacement_note = f"已将{replaced_item.place.name}替换为{candidate.name}。"
        interim_plan = _make_plan_from_items(
            existing_plan,
            request,
            rebuilt,
            alternatives=alternatives,
            weather=state.get("weather"),
            notes=[
                _data_source_note(self._map_provider.name),
                replacement_note,
                *route_notes,
                *request.notes,
                *request.constraints,
            ],
            summary=(f"已保留其他行程节点，并将{replaced_item.place.name}替换为{candidate.name}。"),
        )
        if not _has_uncovered_plan_keywords(
            interim_plan,
            focus_activity or [],
            focus_dining or [],
        ):
            return interim_plan

        updated_plan = await self._append_to_existing_plan(
            state,
            interim_plan,
            activities,
            restaurants,
            cafes,
        )
        added_names = [
            item.place.name
            for item in updated_plan.items
            if item.place.id not in {entry.place.id for entry in interim_plan.items}
        ]
        return updated_plan.model_copy(
            update={
                "summary": (
                    interim_plan.summary
                    + (f" 同时补充了{'、'.join(added_names)}。" if added_names else "")
                ),
                "notes": list(dict.fromkeys([replacement_note, *updated_plan.notes])),
            }
        )

    async def _remove_from_existing_plan(
        self,
        state: DatePlanningState,
        existing_plan: DatePlan,
    ) -> DatePlan:
        request = state["request"]
        if len(existing_plan.items) <= 1:
            return _preserve_existing_plan(
                existing_plan,
                request,
                weather=state.get("weather"),
                note="当前计划只剩一个节点，未执行删除，避免生成空行程。",
            )
        target_index = next(
            (
                index
                for index, item in enumerate(existing_plan.items)
                if item.place.category not in {PlaceCategory.RESTAURANT, PlaceCategory.CAFE}
                and (request.target_day is None or item.day_index == request.target_day)
            ),
            next(
                (
                    index
                    for index, item in reversed(list(enumerate(existing_plan.items)))
                    if request.target_day is None or item.day_index == request.target_day
                ),
                len(existing_plan.items) - 1,
            ),
        )
        items = [item for index, item in enumerate(existing_plan.items) if index != target_index]
        rebuilt, route_notes = await self._rebuild_plan_items(
            items,
            request.transport_mode,
        )
        return _make_plan_from_items(
            existing_plan,
            request,
            rebuilt,
            alternatives=existing_plan.alternatives,
            weather=state.get("weather"),
            notes=[
                _data_source_note(self._map_provider.name),
                f"已删除：{existing_plan.items[target_index].place.name}。",
                *route_notes,
                *request.notes,
                *request.constraints,
            ],
            summary="已保留其余行程节点，并重新计算了路线。",
        )

    async def _rebuild_plan_items(
        self,
        items: list[DatePlanItem],
        mode,
        *,
        preserve_order: bool = False,
    ) -> tuple[list[DatePlanItem], list[str]]:
        rebuilt: list[DatePlanItem] = []
        route_notes: list[str] = []
        previous: Place | None = None
        current_day: int | None = None
        day_order = 0
        source_items = items if preserve_order else _sort_plan_items(items)
        for item in source_items:
            if item.day_index != current_day:
                current_day = item.day_index
                previous = None
                day_order = 0
            day_order += 1
            route = None
            if previous is not None:
                try:
                    route = await self._map_provider.route(previous, item.place, mode)
                except Exception as exc:
                    route_notes.append(f"地点之间的路线暂未返回：{str(exc)[:120]}")
            rebuilt.append(
                item.model_copy(
                    update={
                        "order": day_order,
                        "route_from_previous": route,
                    }
                )
            )
            previous = item.place
        return rebuilt, list(dict.fromkeys(route_notes))


def _assign_keywords_to_days(
    keywords: list[str],
    day_count: int,
    target_day: int | None,
) -> dict[int, list[str]]:
    assignments = {day_index: [] for day_index in range(1, day_count + 1)}
    if target_day is not None:
        assignments[min(target_day, day_count)].extend(dict.fromkeys(keywords))
        return assignments
    for index, keyword in enumerate(dict.fromkeys(keywords)):
        assignments[(index % day_count) + 1].append(keyword)
    return assignments


def _assign_requirement_groups_to_days(
    request: DatePlanRequest,
    *,
    dining: bool,
) -> dict[int, list[list[DesiredDateStop | None]]]:
    assignments: dict[int, list[list[DesiredDateStop | None]]] = {
        day_index: [] for day_index in range(1, request.day_count + 1)
    }
    requirements = _requirements_for_kind(request, dining=dining)
    if requirements:
        for index, requirement in enumerate(requirements):
            explicit_days = {
                alternative.target_day
                for alternative in requirement.alternatives
                if alternative.target_day is not None
            }
            requested_day = next(iter(explicit_days)) if len(explicit_days) == 1 else None
            day_index = min(
                requested_day or request.target_day or (index % request.day_count) + 1,
                request.day_count,
            )
            assignments[day_index].append(list(requirement.alternatives))
        return assignments

    keywords = request.dining_keywords if dining else request.activity_keywords
    kind = StopKind.DINING if dining else StopKind.ACTIVITY
    legacy = _assign_keywords_to_days(keywords, request.day_count, request.target_day)
    for day_index, values in legacy.items():
        assignments[day_index].extend(
            [DesiredDateStop(kind=kind, keyword=keyword, target_day=day_index)]
            for keyword in values
        )
    return assignments


def _requirement_group_keywords(
    assignments: dict[int, list[list[DesiredDateStop | None]]],
) -> dict[int, list[str]]:
    return {
        day_index: list(
            dict.fromkeys(
                value
                for alternatives in groups
                for alternative in alternatives
                if alternative is not None
                if (value := alternative.keyword or alternative.place_name) is not None
            )
        )
        for day_index, groups in assignments.items()
    }


def _requirement_group_label(alternatives: list[DesiredDateStop | None]) -> str:
    values = [
        value
        for alternative in alternatives
        if alternative is not None
        if (value := alternative.keyword or alternative.place_name) is not None
    ]
    return "/".join(values) or "未指定节点"


def _reserved_candidate_ids(
    places: list[Place],
    assignments: dict[int, list[str]],
    current_day: int,
    used_ids: set[str],
) -> set[str]:
    reserved: set[str] = set()
    unavailable = set(used_ids)
    for day_index in sorted(assignments):
        if day_index <= current_day:
            continue
        for keyword in assignments[day_index]:
            candidate = _first_matching_place(places, keyword, unavailable)
            if candidate is not None:
                reserved.add(candidate.id)
                unavailable.add(candidate.id)
    return reserved


def _target_day_for_edit(existing_plan: DatePlan, request: DatePlanRequest) -> int:
    if request.target_day is not None:
        return min(request.target_day, request.day_count)
    if existing_plan.plan_mode == DatePlanMode.MULTI_DAY:
        item_counts = {
            day_index: sum(item.day_index == day_index for item in existing_plan.items)
            for day_index in range(1, existing_plan.day_count + 1)
        }
        return min(item_counts, key=item_counts.get)
    return 1


def _build_plan_days(
    items: list[DatePlanItem],
    request: DatePlanRequest,
    forecasts: list[WeatherForecast],
) -> list[DatePlanDay]:
    weather_by_date = {forecast.date: forecast for forecast in forecasts}
    days: list[DatePlanDay] = []
    for day_index in range(1, request.day_count + 1):
        planned_date = (
            request.date + timedelta(days=day_index - 1) if request.date is not None else None
        )
        day_items = [item for item in items if item.day_index == day_index]
        days.append(
            DatePlanDay(
                day_index=day_index,
                date=planned_date,
                items=day_items,
                total_estimated_cost=sum(item.estimated_cost for item in day_items),
                total_duration_minutes=_plan_duration(day_items),
                weather=weather_by_date.get(planned_date) if planned_date else None,
                lodging_notes=(request.lodging_notes if day_index <= request.nights else []),
            )
        )
    return days


def _normalize_plan_metadata(
    plan: DatePlan,
    request: DatePlanRequest,
    forecasts: list[WeatherForecast],
) -> DatePlan:
    items = [
        item.model_copy(
            update={
                "scheduled_date": (
                    request.date + timedelta(days=item.day_index - 1)
                    if request.date is not None
                    else None
                )
            }
        )
        for item in plan.items
    ]
    days = (
        _build_plan_days(items, request, forecasts)
        if request.plan_mode == DatePlanMode.MULTI_DAY
        else []
    )
    return plan.model_copy(
        update={
            "plan_mode": request.plan_mode,
            "start_date": request.date,
            "end_date": request.end_date,
            "day_count": request.day_count,
            "nights": request.nights,
            "days": days,
            "items": items,
        }
    )


def _first_matching_place(
    places: list[Place],
    keyword: str | None,
    excluded_ids: set[str],
) -> Place | None:
    for place in places:
        if place.id in excluded_ids:
            continue
        if keyword is None or _place_matches_keyword(place, keyword):
            return place
    return None


def _replacement_target_index(
    items: list[DatePlanItem],
    place_names: list[str],
    *,
    target_day: int | None = None,
) -> int | None:
    normalized_targets = [_normalized_place_name(name) for name in place_names]
    for index, item in enumerate(items):
        if target_day is not None and item.day_index != target_day:
            continue
        place_name = _normalized_place_name(item.place.name)
        if any(
            target and (target in place_name or place_name in target)
            for target in normalized_targets
        ):
            return index
    return None


def _normalized_place_name(value: str) -> str:
    return "".join(value.casefold().split()).strip("，,。；;()（）")


def _is_dining_item(item: DatePlanItem) -> bool:
    return item.place.category in {PlaceCategory.RESTAURANT, PlaceCategory.CAFE}


def _replacement_candidate(
    places: list[Place],
    keywords: list[str],
    excluded_ids: set[str],
) -> tuple[str | None, Place | None]:
    for keyword in dict.fromkeys(keywords):
        exact = next(
            (
                place
                for place in places
                if place.id not in excluded_ids and _place_identity_matches_keyword(place, keyword)
            ),
            None,
        )
        if exact is not None:
            return keyword, exact
        candidate = _first_matching_place(places, keyword, excluded_ids)
        if candidate is not None:
            return keyword, candidate
    return None, _first_matching_place(places, None, excluded_ids)


def _has_uncovered_plan_keywords(
    plan: DatePlan,
    activity_keywords: list[str],
    dining_keywords: list[str],
) -> bool:
    return any(
        not any(_plan_item_matches_keyword(item, keyword) for item in plan.items)
        for keyword in dict.fromkeys([*activity_keywords, *dining_keywords])
    )


def _plan_item_matches_keyword(item: DatePlanItem, keyword: str) -> bool:
    if item.slot_keyword == keyword or keyword in item.place.search_keywords:
        return True
    # Use exact text for an already-selected item.  Broad aliases are useful
    # when selecting a candidate, but should not silently claim that a generic
    # museum already satisfies a new explicit "景点" request.
    haystack = " ".join(
        [
            item.place.name,
            item.place.type_name or "",
            *item.place.tags,
            *item.place.matched_preferences,
        ]
    )
    return keyword in haystack


def _place_matches_keyword(place: Place, keyword: str) -> bool:
    if keyword in place.search_keywords:
        return True
    aliases = _DATE_KEYWORD_ALIASES.get(keyword, (keyword,))
    haystack = " ".join([place.name, place.type_name or "", *place.tags])
    return any(alias in haystack for alias in aliases)


def _place_identity_matches_keyword(place: Place, keyword: str) -> bool:
    aliases = _DATE_KEYWORD_ALIASES.get(keyword, (keyword,))
    identity = " ".join([place.name, place.type_name or "", *place.tags])
    return any(alias in identity for alias in aliases)


def _make_date_item(
    place: Place,
    *,
    preferences: list[str],
    activity_keywords: list[str],
    dining_keywords: list[str],
    meal_keywords: dict[str, list[str]],
    schedule_hints: list[str],
    slot_keyword: str | None,
) -> DatePlanItem:
    is_dining = place.category in {PlaceCategory.RESTAURANT, PlaceCategory.CAFE}
    meal_type = _meal_type_for_keyword(slot_keyword, meal_keywords)
    if meal_type is None and is_dining:
        meal_type = next(
            (
                meal
                for meal, keywords in meal_keywords.items()
                if any(_place_matches_keyword(place, keyword) for keyword in keywords)
            ),
            None,
        )
    time_label = _time_label_for_item(
        slot_keyword,
        meal_type,
        schedule_hints,
        is_dining=is_dining,
    )
    after_item = _after_item_for_keyword(slot_keyword, schedule_hints)
    return DatePlanItem(
        order=1,
        place=place,
        duration_minutes=90,
        estimated_cost=place.estimated_cost_per_person * 2,
        reason=_preference_reason(
            place,
            [*preferences, *activity_keywords, *dining_keywords],
        ),
        meal_type=meal_type,
        time_label=time_label,
        after_item=after_item,
        slot_keyword=slot_keyword,
    )


def _annotate_existing_item(
    item: DatePlanItem,
    request: DatePlanRequest,
) -> DatePlanItem:
    keyword = item.slot_keyword
    if keyword is None:
        keyword = next(
            (
                candidate
                for candidate in [
                    *request.activity_keywords,
                    *request.dining_keywords,
                ]
                if (
                    candidate in item.place.search_keywords
                    or candidate
                    in " ".join(
                        [
                            item.place.name,
                            item.place.type_name or "",
                            *item.place.tags,
                            *item.place.matched_preferences,
                        ]
                    )
                )
            ),
            None,
        )
    mapped_meal_type = _meal_type_for_keyword(keyword, request.meal_keywords)
    meal_type = mapped_meal_type or item.meal_type
    is_dining = item.place.category in {PlaceCategory.RESTAURANT, PlaceCategory.CAFE}
    inferred_time_label = _time_label_for_item(
        keyword,
        meal_type,
        request.schedule_hints,
        is_dining=is_dining,
    )
    time_label = inferred_time_label if mapped_meal_type else item.time_label or inferred_time_label
    after_item = item.after_item or _after_item_for_keyword(
        keyword,
        request.schedule_hints,
    )
    return item.model_copy(
        update={
            "meal_type": meal_type,
            "time_label": time_label,
            "after_item": after_item,
            "slot_keyword": keyword,
        }
    )


def _meal_type_for_keyword(
    keyword: str | None,
    meal_keywords: dict[str, list[str]],
) -> str | None:
    if keyword is None:
        return None
    return next(
        (meal_type for meal_type, keywords in meal_keywords.items() if keyword in keywords),
        None,
    )


def _requirements_for_kind(
    request: DatePlanRequest,
    *,
    dining: bool,
) -> list[DateStopRequirement]:
    return [
        requirement
        for requirement in request.requirements
        if (
            requirement.alternatives[0].kind in {StopKind.DINING, StopKind.CAFE}
        )
        == dining
    ]


def _requirement_search_keywords(
    request: DatePlanRequest,
    *,
    dining: bool,
) -> list[str]:
    return list(
        dict.fromkeys(
            value
            for requirement in _requirements_for_kind(request, dining=dining)
            for alternative in requirement.alternatives
            if (value := alternative.keyword or alternative.place_name) is not None
        )
    )


def _first_matching_alternative(
    places: list[Place],
    alternatives: list[DesiredDateStop] | list[None],
    excluded_ids: set[str],
) -> tuple[Place | None, DesiredDateStop | None]:
    for desired in alternatives:
        keyword = desired.keyword or desired.place_name if desired is not None else None
        candidate = _first_matching_place(places, keyword, excluded_ids)
        if candidate is not None:
            return candidate, desired
    return None, None


def _select_requirement_group_places(
    groups: list[
        tuple[
            list[Place],
            list[DesiredDateStop | None],
            bool,
        ]
    ],
    *,
    max_total_cost: int | None,
) -> list[tuple[Place, DesiredDateStop | None, bool]] | None:
    """Choose one unique place per requirement group without greedy budget loss."""

    def select(
        index: int,
        used_ids: set[str],
        total_cost: int,
    ) -> list[tuple[Place, DesiredDateStop | None, bool]] | None:
        if index == len(groups):
            return []
        places, alternatives, dining = groups[index]
        for desired in alternatives:
            keyword = desired.keyword or desired.place_name if desired is not None else None
            for place in places:
                if place.id in used_ids or (
                    keyword is not None and not _place_matches_keyword(place, keyword)
                ):
                    continue
                estimated_cost = place.estimated_cost_per_person * 2
                if max_total_cost is not None and total_cost + estimated_cost > max_total_cost:
                    continue
                remaining = select(
                    index + 1,
                    {*used_ids, place.id},
                    total_cost + estimated_cost,
                )
                if remaining is not None:
                    return [(place, desired, dining), *remaining]
        return None

    return select(0, set(), 0)


def _apply_requirement_role(
    item: DatePlanItem,
    desired: DesiredDateStop | None,
) -> DatePlanItem:
    if desired is None:
        return item
    after_item = item.after_item
    if isinstance(desired.after, StopReference):
        after_item = desired.after.keyword or desired.after.place_name
    time_label = (
        desired.time_window.label
        if desired.time_window is not None and desired.time_window.label is not None
        else {
            TemporalAnchor.LUNCH: "午饭后",
            TemporalAnchor.DINNER: "晚饭后",
            TemporalAnchor.AFTER_DINNER: "晚饭后",
            TemporalAnchor.AFTERNOON: "下午",
            TemporalAnchor.EVENING: "晚上",
        }.get(desired.after)
        or item.time_label
    )
    return item.model_copy(
        update={
            "day_index": desired.target_day or item.day_index,
            "meal_type": desired.meal_type.value if desired.meal_type else item.meal_type,
            "time_label": time_label,
            "after_item": after_item,
            "slot_keyword": desired.keyword or desired.place_name or item.slot_keyword,
        }
    )


def _requires_dinner_anchor(request: DatePlanRequest) -> bool:
    return "dinner" in _required_meal_anchors(request)


def _required_meal_anchors(request: DatePlanRequest) -> set[str]:
    anchors: set[str] = set()
    for requirement in request.requirements:
        for stop in requirement.alternatives:
            label = stop.time_window.label if stop.time_window is not None else None
            if stop.after == TemporalAnchor.LUNCH or (
                label is not None and ("午饭后" in label or "午餐后" in label)
            ):
                anchors.add("lunch")
            if (
                isinstance(stop.after, TemporalAnchor)
                and stop.after in {TemporalAnchor.DINNER, TemporalAnchor.AFTER_DINNER}
            ) or (label is not None and ("晚饭后" in label or "晚餐后" in label)):
                anchors.add("dinner")
    for hint in request.schedule_hints:
        if "午饭后" in hint or "午餐后" in hint:
            anchors.add("lunch")
        if "晚饭后" in hint or "晚餐后" in hint:
            anchors.add("dinner")
    return anchors


def _time_label_for_item(
    keyword: str | None,
    meal_type: str | None,
    schedule_hints: list[str],
    *,
    is_dining: bool,
) -> str | None:
    if meal_type is not None:
        return {
            "breakfast": "早餐",
            "lunch": "午餐",
            "dinner": "晚餐",
        }.get(meal_type, meal_type)
    if is_dining:
        return None
    if keyword is None:
        return None
    if any("看完电影后" in hint or "电影后" in hint for hint in schedule_hints) and keyword in {
        "景点",
        "公园",
        "博物馆",
        "美术馆",
    }:
        return "电影后"
    if "下午" in schedule_hints:
        return "下午"
    if "晚上" in schedule_hints:
        return "晚上"
    if "上午" in schedule_hints:
        return "上午"
    return None


def _after_item_for_keyword(
    keyword: str | None,
    schedule_hints: list[str],
) -> str | None:
    if keyword in {"景点", "公园", "博物馆", "美术馆"} and any(
        "看完电影后" in hint or "电影后" in hint for hint in schedule_hints
    ):
        return "电影院"
    return None


def _sort_plan_items(items: list[DatePlanItem]) -> list[DatePlanItem]:
    def sort_key(item: DatePlanItem) -> tuple[int, int]:
        if item.meal_type == "breakfast":
            rank = 10
        elif item.meal_type == "lunch":
            rank = 20
        elif item.time_label in {"午饭后", "午餐后"} or item.time_label == "上午":
            rank = 25
        elif item.time_label == "下午":
            rank = 30
        elif item.after_item is not None or item.time_label == "电影后":
            rank = 35
        elif item.meal_type == "dinner" or item.time_label == "晚餐":
            rank = 50
        elif item.time_label in {"晚饭后", "晚餐后"}:
            rank = 60
        elif item.place.category in {PlaceCategory.RESTAURANT, PlaceCategory.CAFE}:
            rank = 45
        else:
            rank = 30
        return rank, item.order

    ordered: list[DatePlanItem] = []
    for day_index in sorted({item.day_index for item in items}):
        day_items = [item for item in items if item.day_index == day_index]
        independent = sorted(
            (item for item in day_items if item.after_item is None),
            key=sort_key,
        )
        dependent = sorted(
            (item for item in day_items if item.after_item is not None),
            key=sort_key,
        )
        for item in dependent:
            target_index = next(
                (
                    index
                    for index in range(len(independent) - 1, -1, -1)
                    if _plan_item_matches_keyword(independent[index], item.after_item or "")
                ),
                None,
            )
            if target_index is None:
                independent.append(item)
            else:
                independent.insert(target_index + 1, item)
        ordered.extend(independent)
    return ordered


def _plan_duration(items: list[DatePlanItem]) -> int:
    return sum(item.duration_minutes for item in items) + sum(
        item.route_from_previous.duration_minutes
        for item in items
        if item.route_from_previous is not None
    )


def _keyword_plan_summary(items: list[DatePlanItem]) -> str:
    labels = "、".join(_date_item_label(item) for item in items)
    return f"我按{labels}的顺序安排了这版行程，保留了明确的餐次和活动先后关系。"


def _incremental_plan_summary(
    existing_plan: DatePlan,
    items: list[DatePlanItem],
    additions: list[DatePlanItem],
) -> str:
    del existing_plan
    added = "、".join(_date_item_label(item) for item in additions)
    sequence = "、".join(_date_item_label(item) for item in items)
    return f"已保留原有行程，并加入{added}。当前顺序为：{sequence}。"


def _date_item_label(item: DatePlanItem) -> str:
    prefix: str | None = None
    if item.meal_type:
        prefix = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}.get(
            item.meal_type,
            item.meal_type,
        )
    elif item.time_label:
        prefix = item.time_label
    return f"[{prefix}] {item.place.name}" if prefix else item.place.name


_DATE_KEYWORD_ALIASES = {
    "电影院": ("电影院", "电影", "影院"),
    "景点": ("景点", "旅游景点", "美术馆", "博物馆"),
    "博物馆": ("博物馆", "美术馆"),
    "美术馆": ("美术馆", "博物馆"),
    "公园": ("公园",),
    "剧场": ("剧场", "演出"),
    "西餐": ("西餐", "西餐厅"),
    "日料": ("日料", "日本料理"),
    "韩国料理": ("韩国料理", "韩餐", "韩国烤肉"),
    "海底捞": ("海底捞",),
    "火锅": ("火锅",),
    "烧烤": ("烧烤",),
    "素食": ("素食", "素菜"),
}

_ATTRACTION_ONLY_KEYWORDS = {"景点", "博物馆", "美术馆", "公园"}
_ENTERTAINMENT_ONLY_KEYWORDS = {"电影院", "剧场"}


def _preserve_existing_plan(
    existing_plan: DatePlan,
    request: DatePlanRequest,
    *,
    weather: WeatherForecast | None,
    note: str,
) -> DatePlan:
    items = [
        item.model_copy(
            update={
                "scheduled_date": (
                    request.date + timedelta(days=item.day_index - 1)
                    if request.date is not None
                    else None
                )
            }
        )
        for item in existing_plan.items
    ]
    forecasts = [day.weather for day in existing_plan.days if day.weather is not None]
    if weather is not None:
        forecasts = [weather, *[item for item in forecasts if item.date != weather.date]]
    days = (
        _build_plan_days(items, request, forecasts)
        if request.plan_mode == DatePlanMode.MULTI_DAY
        else []
    )
    return existing_plan.model_copy(
        update={
            "plan_mode": request.plan_mode,
            "start_date": request.date,
            "end_date": request.end_date,
            "day_count": request.day_count,
            "nights": request.nights,
            "days": days,
            "items": items,
            "weather": weather or existing_plan.weather,
            "notes": list(dict.fromkeys([*existing_plan.notes, note, *request.notes])),
        }
    )


def _make_plan_from_items(
    existing_plan: DatePlan,
    request: DatePlanRequest,
    items: list[DatePlanItem],
    *,
    alternatives: list[Place],
    weather: WeatherForecast | None,
    notes: list[str],
    summary: str,
) -> DatePlan:
    items = [
        item.model_copy(
            update={
                "scheduled_date": (
                    request.date + timedelta(days=item.day_index - 1)
                    if request.date is not None
                    else None
                )
            }
        )
        for item in items
    ]
    total_cost = sum(item.estimated_cost for item in items)
    total_duration = sum(item.duration_minutes for item in items) + sum(
        item.route_from_previous.duration_minutes
        for item in items
        if item.route_from_previous is not None
    )
    forecasts = [day.weather for day in existing_plan.days if day.weather is not None]
    if weather is not None:
        forecasts = [weather, *[item for item in forecasts if item.date != weather.date]]
    days = (
        _build_plan_days(items, request, forecasts)
        if request.plan_mode == DatePlanMode.MULTI_DAY
        else []
    )
    return DatePlan(
        title=existing_plan.title or f"{request.city or ''}{request.area or ''}约会计划",
        summary=summary,
        plan_mode=request.plan_mode,
        start_date=request.date,
        end_date=request.end_date,
        day_count=request.day_count,
        nights=request.nights,
        days=days,
        items=items,
        alternatives=alternatives,
        total_estimated_cost=total_cost,
        total_duration_minutes=total_duration,
        notes=list(dict.fromkeys(notes)),
        weather=weather or existing_plan.weather,
        data_source=existing_plan.data_source,
    )


def _preference_reason(place: Place, preferences: list[str]) -> str:
    matched = place.matched_preferences or [
        preference for preference in preferences if preference in place.tags
    ]
    if matched:
        return f"符合你们对{'、'.join(matched)}的偏好。"
    return "适合作为轻松交流的约会节点。"


def _date_memory_preferences(context: RelationshipContext) -> tuple[list[str], list[str]]:
    positive: list[str] = []
    excluded: list[str] = []
    classified: set[str] = set()
    for item in context.remembered_items:
        value = item.payload.get("preference")
        constraint_values = _memory_constraint_values(item.payload)
        if item.kind != MemoryKind.PREFERENCE and value is None and not constraint_values:
            continue
        if isinstance(value, list):
            values = [str(entry).strip() for entry in value if str(entry).strip()]
        elif value is None:
            values = constraint_values
        else:
            values = [str(value).strip()]
        preference_type = str(item.payload.get("preference_type") or "").casefold()
        is_excluded = (
            bool(constraint_values)
            or preference_type
            in {
                "dislike",
                "avoid",
                "restriction",
                "allergy",
            }
            or any(marker in item.summary for marker in ("不喜欢", "避免", "不能", "不吃", "过敏"))
        )
        target = excluded if is_excluded else positive
        target.extend(value for value in values if value)
        classified.update(value for value in values if value)

    positive.extend(
        value
        for value in [*context.user_preferences, *context.partner_preferences]
        if value not in classified
    )
    return list(dict.fromkeys(positive)), list(dict.fromkeys(excluded))


def _memory_constraint_values(payload: dict) -> list[str]:
    values: list[str] = []
    for key in (
        "allergen",
        "allergy_type",
        "constraint",
        "dietary_restriction",
        "restriction",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            values.extend(str(entry).strip() for entry in value if str(entry).strip())
        elif value is not None and str(value).strip():
            values.append(str(value).strip())
    return list(dict.fromkeys(values))


def _data_source_note(provider_name: str) -> str:
    if provider_name == "demo-map":
        return "当前地点和路线为演示数据，不可直接用于真实出行。"
    return "地点与路线来自高德；营业状态、价格和预约情况请在出发前再次确认。"


def _activity_score(
    place: Place,
    preferences: list[str],
    weather: WeatherForecast | None,
    constraints: list[str],
    activity_keywords: list[str],
) -> tuple:
    explicit_match = sum(
        _place_identity_matches_keyword(place, keyword) for keyword in activity_keywords
    )
    matched = len(place.matched_preferences)
    indoor = int(
        place.category == PlaceCategory.ENTERTAINMENT
        or any(tag in {"室内", "博物馆", "美术馆", "展览", "手工"} for tag in place.tags)
    )
    indoor_requested = any(
        marker in " ".join(constraints) for marker in ("不要户外", "避免户外", "室内", "下雨")
    )
    adverse_weather = weather is not None and weather.favors_indoor
    weather_bonus = int((indoor_requested or adverse_weather) and indoor)
    weather_penalty = int((indoor_requested or adverse_weather) and not indoor)
    preference_fallback = sum(preference in place.tags for preference in preferences)
    return (
        explicit_match,
        weather_bonus,
        -weather_penalty,
        matched + preference_fallback,
        place.rating or 0,
    )


def _weather_summary(weather: WeatherForecast) -> str:
    values = [weather.condition]
    if weather.temperature_low is not None and weather.temperature_high is not None:
        values.append(f"{weather.temperature_low}-{weather.temperature_high}℃")
    if weather.rain_probability is not None:
        values.append(f"降雨概率约 {weather.rain_probability}%")
    if weather.wind:
        values.append(weather.wind)
    return "，".join(values)


def _pair_score(
    pair: tuple[Place, Place],
    weather: WeatherForecast | None = None,
    constraints: list[str] | None = None,
) -> tuple:
    activity, restaurant = pair
    matched_preferences = len(activity.matched_preferences) + len(restaurant.matched_preferences)
    distance = _straight_line_distance(activity, restaurant)
    rating = (activity.rating or 0) + (restaurant.rating or 0)
    known_costs = int(not activity.cost_is_estimate) + int(not restaurant.cost_is_estimate)
    constraints = constraints or []
    indoor_requested = any(
        marker in " ".join(constraints) for marker in ("不要户外", "避免户外", "室内", "下雨")
    )
    adverse_weather = weather is not None and weather.favors_indoor
    indoor = activity.category == PlaceCategory.ENTERTAINMENT or any(
        tag in {"室内", "博物馆", "美术馆", "展览", "手工"} for tag in activity.tags
    )
    indoor_score = int((indoor_requested or adverse_weather) and indoor)
    outdoor_penalty = int((indoor_requested or adverse_weather) and not indoor)
    return indoor_score, -outdoor_penalty, matched_preferences, -distance, known_costs, rating


def _straight_line_distance(origin: Place, destination: Place) -> float:
    if None in (
        origin.longitude,
        origin.latitude,
        destination.longitude,
        destination.latitude,
    ):
        return float("inf")
    latitude_1 = math.radians(origin.latitude)
    latitude_2 = math.radians(destination.latitude)
    latitude_delta = math.radians(destination.latitude - origin.latitude)
    longitude_delta = math.radians(destination.longitude - origin.longitude)
    value = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(latitude_1) * math.cos(latitude_2) * math.sin(longitude_delta / 2) ** 2
    )
    value = min(max(value, 0), 1)
    return 6_371_000 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def _select_alternatives(
    candidates: list[Place],
    selected_ids: set[str],
    limit: int,
) -> list[Place]:
    alternatives: list[Place] = []
    seen = set(selected_ids)
    for candidate in candidates:
        if candidate.id in seen:
            continue
        alternatives.append(candidate)
        seen.add(candidate.id)
        if len(alternatives) == limit:
            break
    return alternatives

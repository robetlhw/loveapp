from collections import defaultdict
from datetime import timedelta
from enum import StrEnum
from itertools import pairwise

from pydantic import BaseModel, Field

from loveapp.domain.date_constraints import (
    ConstraintStrength,
    DateConstraint,
    DateConstraintKind,
)
from loveapp.domain.date_plan import DatePlan, DatePlanItem, DatePlanRequest, Place


class ValidationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class PlanValidationIssue(BaseModel):
    code: str
    severity: ValidationSeverity
    message: str
    item_ids: list[str] = Field(default_factory=list)


class PlanValidationResult(BaseModel):
    issues: list[PlanValidationIssue] = Field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not any(issue.severity == ValidationSeverity.ERROR for issue in self.issues)


class DatePlanValidator:
    """Final hard-constraint validation before a plan snapshot becomes current."""

    def validate(
        self,
        plan: DatePlan,
        request: DatePlanRequest,
        constraints: list[DateConstraint],
    ) -> PlanValidationResult:
        issues: list[PlanValidationIssue] = []
        issues.extend(self._validate_budget(plan, request))
        issues.extend(self._validate_required_keywords(plan, constraints))
        issues.extend(self._validate_exclusions(plan, constraints))
        issues.extend(self._validate_duplicates(plan))
        issues.extend(self._validate_days(plan, request))
        issues.extend(self._validate_order_and_routes(plan))
        return PlanValidationResult(issues=issues)

    def _validate_budget(
        self, plan: DatePlan, request: DatePlanRequest
    ) -> list[PlanValidationIssue]:
        issues = []
        if plan.total_estimated_cost > request.effective_total_budget:
            issues.append(
                _error(
                    "budget_exceeded",
                    "计划总费用超过预算。",
                    [item.place.id for item in plan.items],
                )
            )
        by_day = _items_by_day(plan.items)
        if request.day_count > 1 and request.budget_scope.value == "per_day":
            for day, items in by_day.items():
                if sum(item.estimated_cost for item in items) > request.effective_daily_budget:
                    issues.append(
                        _error(
                            "daily_budget_exceeded",
                            f"第 {day} 天费用超过每日预算。",
                            [item.place.id for item in items],
                        )
                    )
        return issues

    def _validate_required_keywords(
        self, plan: DatePlan, constraints: list[DateConstraint]
    ) -> list[PlanValidationIssue]:
        issues = []
        for constraint in constraints:
            if constraint.strength != ConstraintStrength.REQUIRED:
                continue
            keyword = str(constraint.value).strip()
            if keyword and not any(_item_matches_keyword(item, keyword) for item in plan.items):
                issues.append(
                    _error(
                        "required_keyword_missing",
                        f"未满足明确要求：{keyword}。",
                    )
                )
        return issues

    def _validate_exclusions(
        self, plan: DatePlan, constraints: list[DateConstraint]
    ) -> list[PlanValidationIssue]:
        issues = []
        exclusions = [
            str(constraint.value).strip()
            for constraint in constraints
            if constraint.strength == ConstraintStrength.HARD
            and constraint.kind in {DateConstraintKind.EXCLUSION, DateConstraintKind.ALLERGY}
        ]
        for keyword in exclusions:
            matched = [
                item.place.id for item in plan.items if _place_matches_keyword(item.place, keyword)
            ]
            if matched:
                issues.append(
                    _error("hard_exclusion_violated", f"计划命中排除项：{keyword}。", matched)
                )
        return issues

    def _validate_duplicates(self, plan: DatePlan) -> list[PlanValidationIssue]:
        issues = []
        for _, items in _items_by_day(plan.items).items():
            ids = [item.place.id for item in items]
            duplicates = sorted({place_id for place_id in ids if ids.count(place_id) > 1})
            if duplicates:
                issues.append(_error("duplicate_poi", "同一天不能重复使用同一地点。", duplicates))
        return issues

    def _validate_days(self, plan: DatePlan, request: DatePlanRequest) -> list[PlanValidationIssue]:
        issues = []
        for item in plan.items:
            if item.day_index > request.day_count:
                issues.append(
                    _error("day_index_out_of_range", "计划节点超出行程天数。", [item.place.id])
                )
            if request.date is not None:
                expected = request.date + timedelta(days=item.day_index - 1)
                if item.scheduled_date != expected:
                    issues.append(
                        _error(
                            "scheduled_date_mismatch", "节点日期与行程日期不一致。", [item.place.id]
                        )
                    )
        return issues

    def _validate_order_and_routes(self, plan: DatePlan) -> list[PlanValidationIssue]:
        issues = []
        for _, items in _items_by_day(plan.items).items():
            ordered = sorted(items, key=lambda item: item.order)
            if [item.order for item in ordered] != list(range(1, len(ordered) + 1)):
                issues.append(
                    _error(
                        "non_sequential_order",
                        "每日节点顺序必须连续。",
                        [item.place.id for item in items],
                    )
                )
            for previous, current in pairwise(ordered):
                route = current.route_from_previous
                if route is None:
                    issues.append(
                        _warning("route_missing", "地点间路线暂不可用。", [current.place.id])
                    )
                elif (
                    route.origin_id != previous.place.id or route.destination_id != current.place.id
                ):
                    issues.append(
                        _error("route_mismatch", "路线与相邻地点不一致。", [current.place.id])
                    )
        return issues


def _items_by_day(items: list[DatePlanItem]) -> dict[int, list[DatePlanItem]]:
    grouped: dict[int, list[DatePlanItem]] = defaultdict(list)
    for item in items:
        grouped[item.day_index].append(item)
    return grouped


def _item_matches_keyword(item: DatePlanItem, keyword: str) -> bool:
    return (
        _place_matches_keyword(item.place, keyword)
        or keyword.casefold() in (item.slot_keyword or "").casefold()
    )


def _place_matches_keyword(place: Place, keyword: str) -> bool:
    target = keyword.casefold().replace(" ", "")
    values = [place.name, place.type_name or "", *place.tags, *place.matched_preferences]
    return any(target in value.casefold().replace(" ", "") for value in values)


def _error(code: str, message: str, item_ids: list[str] | None = None) -> PlanValidationIssue:
    return PlanValidationIssue(
        code=code, severity=ValidationSeverity.ERROR, message=message, item_ids=item_ids or []
    )


def _warning(code: str, message: str, item_ids: list[str] | None = None) -> PlanValidationIssue:
    return PlanValidationIssue(
        code=code, severity=ValidationSeverity.WARNING, message=message, item_ids=item_ids or []
    )

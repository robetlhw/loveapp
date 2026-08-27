from collections.abc import Iterable

from loveapp.application.date_planning.structured_stops import match_desired_stop
from loveapp.domain.date_operations import (
    DateRequirementMatch,
    DateStopRequirement,
    DesiredDateStop,
    RequirementStatus,
)
from loveapp.domain.date_plan import DatePlan


class DateRequirementMatcher:
    """Evaluate a concrete plan against durable user requirements."""

    def match(
        self,
        requirements: Iterable[DateStopRequirement],
        plan: DatePlan | None,
    ) -> list[DateRequirementMatch]:
        return [self._match_one(requirement, plan) for requirement in requirements]

    def _match_one(
        self,
        requirement: DateStopRequirement,
        plan: DatePlan | None,
    ) -> DateRequirementMatch:
        if plan is None:
            return _unsatisfied(requirement, "plan_unavailable")

        matched_place_ids: list[str] = []
        identity_matches = 0
        satisfied_alternatives = 0
        for alternative in requirement.alternatives:
            matches = list(match_desired_stop(plan, alternative))
            identity_matches += len(matches)
            placed = [match for match in matches if match.placement_satisfied]
            if placed:
                satisfied_alternatives += 1
                matched_place_ids.extend(match.item.place.id for match in placed)

        matched_place_ids = list(dict.fromkeys(matched_place_ids))
        if satisfied_alternatives < requirement.min_satisfied:
            reason = "required_stop_role_mismatch" if identity_matches else "required_stop_missing"
            return _unsatisfied(requirement, reason, matched_place_ids)
        if (
            requirement.max_satisfied is not None
            and satisfied_alternatives > requirement.max_satisfied
        ):
            return _unsatisfied(
                requirement,
                "alternative_cardinality_exceeded",
                matched_place_ids,
            )
        return DateRequirementMatch(
            requirement_id=requirement.id,
            status=RequirementStatus.FULFILLED,
            matched_place_ids=matched_place_ids,
        )


def primary_desired_stops(
    requirements: Iterable[DateStopRequirement],
) -> list[DesiredDateStop]:
    """Compatibility projection used by the legacy planner search path."""

    return [requirement.alternatives[0] for requirement in requirements]


def all_desired_stop_alternatives(
    requirements: Iterable[DateStopRequirement],
) -> list[DesiredDateStop]:
    return [
        alternative
        for requirement in requirements
        for alternative in requirement.alternatives
    ]


def _unsatisfied(
    requirement: DateStopRequirement,
    reason: str,
    matched_place_ids: list[str] | None = None,
) -> DateRequirementMatch:
    return DateRequirementMatch(
        requirement_id=requirement.id,
        status=RequirementStatus.UNSATISFIED,
        matched_place_ids=matched_place_ids or [],
        reason_code=reason,
    )

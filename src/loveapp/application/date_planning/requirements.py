from collections.abc import Iterable
from dataclasses import dataclass

from loveapp.application.date_planning.structured_stops import match_desired_stop
from loveapp.domain.date_operations import (
    DateRequirementMatch,
    DateStopRequirement,
    DesiredDateStop,
    RequirementStatus,
)
from loveapp.domain.date_plan import DatePlan


@dataclass(frozen=True)
class DateRequirementBinding:
    requirement_id: str
    alternative_index: int
    place_id: str
    source: str


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
        identity_place_ids: list[str] = []
        identity_matches = 0
        satisfied_alternatives = 0
        constraint_reasons: list[str] = []
        for alternative in requirement.alternatives:
            matches = list(match_desired_stop(plan, alternative))
            identity_matches += len(matches)
            identity_place_ids.extend(match.item.place.id for match in matches)
            constraint_reasons.extend(
                match.constraint_reason
                for match in matches
                if match.constraint_reason is not None
            )
            placed = [match for match in matches if match.placement_satisfied]
            if placed:
                satisfied_alternatives += 1
                matched_place_ids.extend(match.item.place.id for match in placed)

        matched_place_ids = list(dict.fromkeys(matched_place_ids))
        if satisfied_alternatives < requirement.min_satisfied:
            reason = (
                "constraint_unverified"
                if "constraint_unverified" in constraint_reasons
                else "constraint_unsatisfied"
                if constraint_reasons
                else "required_stop_role_mismatch"
                if identity_matches
                else "required_stop_missing"
            )
            return _unsatisfied(
                requirement,
                reason,
                list(dict.fromkeys(identity_place_ids)),
            )
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


def resolve_requirement_bindings_for_plan_item(
    *,
    place_id: str,
    requirements: Iterable[DateStopRequirement],
    plan: DatePlan | None,
    matches: Iterable[DateRequirementMatch] = (),
) -> list[DateRequirementBinding]:
    """Bind a concrete PlanItem to durable requirements before label fallback."""

    if plan is None or not any(item.place.id == place_id for item in plan.items):
        return []
    requirement_list = list(requirements)
    by_id = {requirement.id: requirement for requirement in requirement_list}
    matched_ids = list(
        dict.fromkeys(
            match.requirement_id
            for match in matches
            if place_id in match.matched_place_ids and match.requirement_id in by_id
        )
    )
    source = "plan_item_match" if matched_ids else "semantic_fallback"
    candidates = (
        [by_id[requirement_id] for requirement_id in matched_ids]
        if matched_ids
        else requirement_list
    )
    bindings: list[DateRequirementBinding] = []
    for requirement in candidates:
        for index, alternative in enumerate(requirement.alternatives):
            if place_id in requirement_identity_place_ids(plan, alternative):
                bindings.append(
                    DateRequirementBinding(
                        requirement_id=requirement.id,
                        alternative_index=index,
                        place_id=place_id,
                        source=source,
                    )
                )
    return bindings


def requirement_identity_place_ids(
    plan: DatePlan | None,
    desired: DesiredDateStop,
) -> tuple[str, ...]:
    """Return identity matches without treating the requested day as identity."""

    if plan is None:
        return ()
    identity_probe = desired.model_copy(update={"target_day": None})
    return tuple(
        dict.fromkeys(
            match.item.place.id for match in match_desired_stop(plan, identity_probe)
        )
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

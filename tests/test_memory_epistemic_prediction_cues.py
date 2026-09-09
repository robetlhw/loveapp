import pytest

from loveapp.domain.memory import (
    EpistemicStatus,
    MemoryCandidate,
    MemoryKind,
    MemoryPerspective,
)
from loveapp.domain.memory_epistemics import normalize_memory_epistemics


def _belief(text: str) -> MemoryCandidate:
    return MemoryCandidate(
        kind=MemoryKind.STABLE_FACT,
        subject="partner",
        summary=text,
        original_text=text,
        evidence_spans=[text],
        perspective=MemoryPerspective.USER_BELIEF,
        raw_predicate="partner_state",
        custom_predicate="partner_state",
    )


def test_may_already_resolved_is_not_prediction() -> None:
    candidate = normalize_memory_epistemics(
        _belief("I think she may already be resolved.")
    )

    assert candidate.epistemic_status in {
        EpistemicStatus.UNCERTAIN,
        EpistemicStatus.HYPOTHESIS,
    }
    assert candidate.epistemic_status != EpistemicStatus.PREDICTION


@pytest.mark.parametrize(
    "text",
    [
        "I think she may contact me tomorrow.",
        "I think she might contact me next week.",
        "I think she may contact me in the future.",
    ],
)
def test_may_or_might_with_future_cue_is_prediction(text: str) -> None:
    candidate = normalize_memory_epistemics(_belief(text))

    assert candidate.epistemic_status == EpistemicStatus.PREDICTION


def test_might_with_past_cue_is_not_prediction() -> None:
    candidate = normalize_memory_epistemics(
        _belief("I think she might have been upset last week.")
    )

    assert candidate.epistemic_status != EpistemicStatus.PREDICTION

from __future__ import annotations

import re

from loveapp.domain.memory import (
    EpistemicStatus,
    MemoryCandidate,
    MemoryKind,
    MemoryPerspective,
)

_BELIEF_CUE_PATTERN = re.compile(
    r"(?:"
    r"我(?:觉(?:得|着)|感觉|认为|猜(?:测)?|怀疑|担心).{0,18}"
    r"(?:她|他|对方|我们|关系|这段关系|可能|也许|大概|似乎|好像|应该)|"
    r"(?:她|他|对方|我们|这段关系).{0,12}(?:可能|也许|大概|似乎|好像|应该)|"
    r"(?:I\s+(?:think|feel|believe|suspect|guess)|"
    r"maybe|perhaps|probably|seems?|appears?)\b"
    r")",
    re.IGNORECASE,
)

_PREDICTION_CUE_PATTERN = re.compile(
    r"(?:"
    r"(?:以后|将来|未来|明天|后天|下周|下个月|之后|接下来).{0,16}"
    r"(?:会|可能|也许|应该)|"
    r"(?:会|将|可能会|也许会|应该会).{0,18}(?:以后|未来|明天|下周|之后)?|"
    r"\b(?:will|would|going\s+to|likely\s+to|predict)\b"
    r")",
    re.IGNORECASE,
)

# ``may`` and ``might`` are ambiguous epistemic modals.  They only indicate a
# prediction when an explicit future-oriented cue occurs in the same
# proposition; phrases such as ``may already be resolved`` remain uncertain
# beliefs.  Keep this separate from the broader prediction regex so adding a
# new future cue cannot accidentally make bare present-tense modals predictive.
_MAY_MIGHT_FUTURE_PATTERN = re.compile(
    r"(?:"
    r"\b(?:may|might)\b.{0,32}"
    r"(?:tomorrow|next\s+(?:week|month|year)|in\s+the\s+future|"
    r"later|eventually|soon|one\s+day|未来|将来|以后|明天|后天|下周|下个月|之后|接下来)|"
    r"(?:tomorrow|next\s+(?:week|month|year)|in\s+the\s+future|"
    r"later|eventually|soon|one\s+day|未来|将来|以后|明天|后天|下周|下个月|之后|接下来)"
    r".{0,32}\b(?:may|might)\b"
    r")",
    re.IGNORECASE,
)

_HYPOTHESIS_CUE_PATTERN = re.compile(
    r"(?:我(?:猜(?:测)?|推测|估计)|(?:猜测|推测|假设|speculat(?:e|ion)|hypothesis))",
    re.IGNORECASE,
)


def normalize_memory_epistemics(candidate: MemoryCandidate) -> MemoryCandidate:
    """Align explicit belief language with a bounded epistemic contract.

    The extractor remains responsible for semantic parsing, but a missed
    first-person belief cue must not silently become a reported fact.  This
    guard is intentionally narrow: preferences are subjective propositions
    about the holder and hearsay keeps its existing source-type governance.
    """

    evidence_parts = candidate.evidence_spans or [candidate.original_text]
    evidence = " ".join(
        part for part in evidence_parts if isinstance(part, str) and part.strip()
    )
    source_type = str(candidate.payload.get("source_type") or "").casefold()
    cue_detected = bool(_BELIEF_CUE_PATTERN.search(evidence))
    should_reclassify = (
        candidate.perspective == MemoryPerspective.USER_REPORTED
        and candidate.kind != MemoryKind.PREFERENCE
        and source_type not in {"hearsay", "third_party_report"}
        and cue_detected
    )
    perspective = (
        MemoryPerspective.USER_BELIEF if should_reclassify else candidate.perspective
    )
    prediction = bool(
        _PREDICTION_CUE_PATTERN.search(evidence)
        or _MAY_MIGHT_FUTURE_PATTERN.search(evidence)
    )
    hypothesis = bool(_HYPOTHESIS_CUE_PATTERN.search(evidence))
    epistemic_status = candidate.epistemic_status
    if perspective == MemoryPerspective.USER_BELIEF:
        if prediction:
            epistemic_status = EpistemicStatus.PREDICTION
        elif hypothesis:
            epistemic_status = EpistemicStatus.HYPOTHESIS
        elif epistemic_status == EpistemicStatus.CONFIRMED:
            epistemic_status = EpistemicStatus.UNCERTAIN
    elif perspective == MemoryPerspective.MODEL_INFERRED:
        if prediction:
            epistemic_status = EpistemicStatus.PREDICTION
        elif epistemic_status in {
            EpistemicStatus.CONFIRMED,
            EpistemicStatus.UNCERTAIN,
        }:
            epistemic_status = EpistemicStatus.HYPOTHESIS

    updates: dict[str, object] = {}
    if perspective != candidate.perspective:
        updates["perspective"] = perspective
    if epistemic_status != candidate.epistemic_status:
        updates["epistemic_status"] = epistemic_status
    return candidate.model_copy(update=updates) if updates else candidate


def is_epistemically_confirmed(candidate: MemoryCandidate) -> bool:
    return (
        candidate.perspective == MemoryPerspective.USER_REPORTED
        and candidate.epistemic_status == EpistemicStatus.CONFIRMED
    )


def is_belief_like(candidate: MemoryCandidate) -> bool:
    return not is_epistemically_confirmed(candidate)

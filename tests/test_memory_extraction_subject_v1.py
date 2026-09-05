from collections import Counter
from pathlib import Path

from loveapp.evaluation.memory_extraction_v1 import load_memory_extraction_v1_cases

DATASET = Path("evals/memory/extraction_subject_v1.jsonl")

EXPECTED_SLICE_COUNTS = {
    "subject_user_fact": 3,
    "subject_partner_fact": 3,
    "subject_belief_partner": 4,
    "subject_belief_relationship": 3,
    "subject_actor_event": 4,
    "subject_relationship_event": 4,
    "subject_relationship_pattern": 3,
    "subject_advice_outcome": 3,
    "context_reply": 3,
}


def test_subject_v1_dataset_contract_and_distribution() -> None:
    cases = load_memory_extraction_v1_cases(DATASET)

    assert [case.case_id for case in cases] == [
        f"SUBJ-{index:03d}" for index in range(1, 31)
    ]
    assert Counter(case.slice for case in cases) == EXPECTED_SLICE_COUNTS
    assert all(len(case.expected_claims) == 1 for case in cases)
    assert all(
        claim.subject in {"user", "partner", "relationship"}
        for case in cases
        for claim in case.expected_claims
    )
    assert all(not case.existing_memories for case in cases)
    assert all(
        span in case.user_message
        for case in cases
        for claim in case.expected_claims
        for span in claim.evidence_spans
    )


def test_subject_v1_hard_contrasts_preserve_subject_and_perspective() -> None:
    cases = {
        case.case_id: case for case in load_memory_extraction_v1_cases(DATASET)
    }
    expected = {
        "SUBJ-003": ("user", "user_reported"),
        "SUBJ-004": ("partner", "user_reported"),
        "SUBJ-007": ("partner", "user_belief"),
        "SUBJ-011": ("relationship", "user_belief"),
        "SUBJ-014": ("user", "user_reported"),
        "SUBJ-015": ("partner", "user_reported"),
        "SUBJ-019": ("relationship", "user_reported"),
        "SUBJ-022": ("relationship", "user_reported"),
    }

    assert {
        case_id: (
            cases[case_id].expected_claims[0].subject,
            cases[case_id].expected_claims[0].perspective,
        )
        for case_id in expected
    } == expected


def test_subject_v1_context_replies_have_structured_actor_context() -> None:
    cases = load_memory_extraction_v1_cases(DATASET)
    context_cases = [case for case in cases if case.slice == "context_reply"]

    assert [case.case_id for case in context_cases] == [
        "SUBJ-028",
        "SUBJ-029",
        "SUBJ-030",
    ]
    assert [case.expected_claims[0].subject for case in context_cases] == [
        "partner",
        "user",
        "partner",
    ]
    for case in context_cases:
        pending = case.pending_memory_context
        assert pending is not None
        assert pending["expected_slot"] == "actor"
        assert case.conversation_history == [
            {
                "role": "assistant",
                "content": pending["previous_assistant_question"],
            }
        ]

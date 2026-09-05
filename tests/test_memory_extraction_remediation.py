from loveapp.evaluation.memory_extraction_remediation import (
    _focused_summary,
    _review_spurious,
    _user_belief_subject_accuracy,
)


def _layer(
    *,
    claims: list[dict[str, object]],
    matches: list[dict[str, object]] | None = None,
    unmatched_actual: list[int] | None = None,
) -> dict[str, object]:
    return {
        "claims": claims,
        "alignment": {
            "matches": matches or [],
            "unmatched_actual": unmatched_actual or [],
            "over_merge_actual_indices": [],
            "over_split_expected_indices": [],
        },
        "counts": {
            "expected": len(matches or []),
            "matched_expected": len(matches or []),
            "unmatched_actual": len(unmatched_actual or []),
        },
        "errors": [],
    }


def test_remediation_taxonomy_keeps_supported_extra_out_of_unsupported_rate() -> None:
    report = {
        "cases": [
            {
                "case_id": "EXT-005",
                "layers": {
                    "production_cascade": _layer(
                        claims=[{"summary": "对方平时课很多"}],
                        unmatched_actual=[0],
                    )
                },
            }
        ]
    }

    result = _review_spurious(report)

    assert result["category_claim_counts"] == {"SUPPORTED_EXTRA_NOT_IN_GOLD": 1}


def test_user_belief_subject_accuracy_scores_belief_claims_across_slices() -> None:
    report = {
        "cases": [
            {
                "case_id": "belief",
                "slice": "user_belief",
                "expected_claims": [
                    {
                        "subject": "relationship",
                        "perspective": "user_belief",
                    },
                    {
                        "subject": "partner",
                        "perspective": "user_reported",
                    },
                ],
                "layers": {
                    "production_cascade": _layer(
                        claims=[
                            {"subject": "user"},
                            {"subject": "partner"},
                        ],
                        matches=[
                            {
                                "expected_index": 0,
                                "actual_index": 0,
                                "proposition_equivalent": True,
                            },
                            {
                                "expected_index": 1,
                                "actual_index": 1,
                                "proposition_equivalent": True,
                            },
                        ],
                    )
                },
            },
            {
                "case_id": "other",
                "slice": "stable_preference",
                "expected_claims": [
                    {
                        "subject": "partner",
                        "perspective": "user_belief",
                    }
                ],
                "layers": {
                    "production_cascade": _layer(
                        claims=[{"subject": "partner"}],
                        matches=[
                            {
                                "expected_index": 0,
                                "actual_index": 0,
                                "proposition_equivalent": True,
                            }
                        ],
                    )
                },
            },
        ]
    }

    result = _user_belief_subject_accuracy(report)

    assert result == {"correct": 1, "total": 2, "accuracy": 0.5}


def test_focused_summary_checks_unknown_refusal_and_topic_switch() -> None:
    empty = _layer(claims=[])
    topic = _layer(
        claims=[{"summary": "对方喜欢粤菜"}],
        matches=[
            {
                "expected_index": 0,
                "actual_index": 0,
                "proposition_equivalent": True,
            }
        ],
    )
    report = {
        "cases": [
            {
                "case_id": "EXT-R-CTX-009",
                "slice": "context_reply",
                "layers": {"production_cascade": empty},
            },
            {
                "case_id": "EXT-R-CTX-010",
                "slice": "context_reply",
                "layers": {"production_cascade": empty},
            },
            {
                "case_id": "EXT-R-CTX-011",
                "slice": "context_reply",
                "layers": {"production_cascade": topic},
            },
            {
                "case_id": "atom",
                "slice": "atomization",
                "layers": {"production_cascade": empty},
            },
        ],
        "layers": {
            "production_cascade": {
                "metrics": {
                    "context_reply_recall": 1.0,
                    "atomization_accuracy": 1.0,
                    "negative_restraint_false_positive_rate": 0.0,
                }
            }
        },
    }

    result = _focused_summary(report)

    assert result["unknown_refusal_fail_safe"] is True
    assert result["topic_switch_pass"] is True
    assert result["context_case_count"] == 3
    assert result["atomization_case_count"] == 1

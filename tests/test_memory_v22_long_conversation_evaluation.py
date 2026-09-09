import runpy
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "evaluate_memory_v2_long_conversation.py"
)
_NAMESPACE = runpy.run_path(str(_SCRIPT), run_name="memory_v22_eval_test")
DEFAULT_DATASET_PATH = _NAMESPACE["DEFAULT_DATASET_PATH"]
_aggregate_v22_metrics = _NAMESPACE["_aggregate_v22_metrics"]
_case_result = _NAMESPACE["_case_result"]
_load_dataset = _NAMESPACE["_load_dataset"]
_select_cases = _NAMESPACE["_select_cases"]


def test_v22_dataset_contains_ten_independent_long_conversations() -> None:
    cases = _load_dataset(DEFAULT_DATASET_PATH)

    assert len(cases) == 10
    assert len({case["case_id"] for case in cases}) == 10
    assert all(5 <= len(case["turns"]) <= 10 for case in cases)
    assert any(case.get("expect_pattern_consolidation") for case in cases)


def test_v22_metric_aggregation_exposes_required_rates() -> None:
    metrics = _aggregate_v22_metrics(
        [
            {
                "v22_metrics": {
                    "single_event_expected_count": 4,
                    "single_event_to_pattern_count": 1,
                    "belief_expected_count": 2,
                    "belief_as_fact_count": 0,
                    "pattern_without_evidence": 1,
                    "event_cluster_to_pattern_expected": 1,
                    "event_cluster_to_pattern_succeeded": 1,
                }
            },
            {
                "v22_metrics": {
                    "single_event_expected_count": 1,
                    "single_event_to_pattern_count": 0,
                    "belief_expected_count": 2,
                    "belief_as_fact_count": 1,
                    "pattern_without_evidence": 0,
                    "event_cluster_to_pattern_expected": 1,
                    "event_cluster_to_pattern_succeeded": 0,
                }
            },
        ]
    )

    assert metrics["single_event_to_pattern_rate"] == 0.2
    assert metrics["pattern_without_evidence"] == 1
    assert metrics["belief_as_fact_rate"] == 0.25
    assert metrics["event_cluster_to_pattern_success"] == 0.5


def _event(memory_id: str) -> dict[str, object]:
    return {
        "id": memory_id,
        "kind": "interaction_event",
        "status": "confirmed",
        "perspective": "user_reported",
        "payload": {},
    }


def _pattern(*evidence_ids: str) -> dict[str, object]:
    return {
        "id": "pattern-1",
        "kind": "interaction_pattern",
        "status": "proposed",
        "perspective": "model_inferred",
        "payload": {
            "source": "model_inferred",
            "evidence_ids": list(evidence_ids),
            "time_window": {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-08-03T00:00:00Z",
            },
        },
    }


def _report(*added: dict[str, object]) -> dict[str, object]:
    return {
        "gate": {"should_extract": True},
        "summary": {"extracted_claim_count": 1},
        "diff": {"added": list(added)},
    }


def test_evidence_backed_consolidation_is_not_single_event_promotion() -> None:
    events = [_event(f"event-{index}") for index in range(1, 4)]
    pattern = _pattern(*(str(item["id"]) for item in events))
    case = {
        "case_id": "V22-TEST",
        "difficulty": "medium",
        "title": "event consolidation",
        "expected_checks": [],
        "expect_pattern_consolidation": True,
        "turns": [
            {"text": "one", "labels": ["single_event", "cluster_event"]},
            {"text": "two", "labels": ["single_event", "cluster_event"]},
            {"text": "three", "labels": ["single_event", "cluster_event"]},
        ],
    }
    reports = [
        _report(events[0]),
        _report(events[1]),
        _report(events[2], pattern),
    ]

    result = _case_result(case, reports, [*events, pattern])

    assert result["v22_metrics"]["single_event_to_pattern_count"] == 0
    assert result["v22_metrics"]["event_cluster_to_pattern_succeeded"] == 1


def test_cluster_success_requires_real_events_and_complete_cluster_coverage() -> None:
    events = [_event(f"event-{index}") for index in range(1, 4)]
    incomplete_pattern = _pattern("event-1", "event-2", "missing-event")
    case = {
        "case_id": "V22-TEST",
        "difficulty": "medium",
        "title": "invalid consolidation evidence",
        "expected_checks": [],
        "expect_pattern_consolidation": True,
        "turns": [
            {"text": "one", "labels": ["cluster_event"]},
            {"text": "two", "labels": ["cluster_event"]},
            {"text": "three", "labels": ["cluster_event"]},
        ],
    }

    result = _case_result(
        case,
        [_report(events[0]), _report(events[1]), _report(events[2])],
        [*events, incomplete_pattern],
    )

    assert result["v22_metrics"]["event_cluster_to_pattern_succeeded"] == 0


def test_case_selection_filters_the_loaded_v22_dataset() -> None:
    cases = _load_dataset(DEFAULT_DATASET_PATH)

    selected = _select_cases(cases, ["v22-lc-010"])

    assert [item["case_id"] for item in selected] == ["V22-LC-010"]

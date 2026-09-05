import json
import runpy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from loveapp.core.config import Settings
from loveapp.domain.memory import (
    AtomicClaim,
    AtomicExtraction,
    EvidenceExplicitness,
    MemoryAttemptStatus,
    MemoryExtractionAttempt,
    MemoryKind,
    TimeKind,
)

_LIVE_SCRIPT = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts" / "evaluate_memory_foundation_live.py")
)
evaluate_memory_foundation_live = _LIVE_SCRIPT["evaluate_memory_foundation_live"]
default_output_path = _LIVE_SCRIPT["default_output_path"]
_record_matches = _LIVE_SCRIPT["_record_matches"]
LIVE_EXPECTATIONS = (
    Path(__file__).parents[1]
    / "evals"
    / "memory"
    / "cases_v1_live_expectations.json"
)
FOUNDATION_DATASET = Path(__file__).parents[1] / "evals" / "memory" / "cases_v1.jsonl"


class RecordingFlashExtractor:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    async def extract(
        self,
        text: str,
        *,
        attempt_callback=None,
        **kwargs: object,
    ) -> AtomicExtraction:
        del kwargs
        self.inputs.append(text)
        extraction = AtomicExtraction(claims=[_preference_claim(text)])
        if attempt_callback is not None:
            attempt_callback(
                MemoryExtractionAttempt(
                    attempt=1,
                    status=MemoryAttemptStatus.COMPLETED,
                    duration_ms=12.5,
                    model="fake-flash-model",
                    tier="flash",
                    prompt_tokens=11,
                    completion_tokens=7,
                    total_tokens=18,
                    claim_count=1,
                    original_claim_count=1,
                    repaired_claim_count=0,
                    discarded_claim_count=0,
                    invalid_claim_count=0,
                    repair_status="not_needed",
                )
            )
        return extraction


class FailingFlashExtractor:
    async def extract(
        self,
        text: str,
        *,
        attempt_callback=None,
        **kwargs: object,
    ) -> AtomicExtraction:
        del text, kwargs
        if attempt_callback is not None:
            attempt_callback(
                MemoryExtractionAttempt(
                    attempt=1,
                    status=MemoryAttemptStatus.FAILED,
                    duration_ms=8.25,
                    model="fake-flash-model",
                    tier="flash",
                    failure_category="schema_validation",
                    error="invalid extraction response",
                )
            )
        raise ValueError("invalid extraction response")


class SequenceFlashExtractor:
    def __init__(self, *extractions: AtomicExtraction) -> None:
        self._extractions = iter(extractions)

    async def extract(
        self,
        text: str,
        *,
        attempt_callback=None,
        **kwargs: object,
    ) -> AtomicExtraction:
        del text, kwargs
        extraction = next(self._extractions)
        if attempt_callback is not None:
            attempt_callback(
                MemoryExtractionAttempt(
                    attempt=1,
                    status=MemoryAttemptStatus.COMPLETED,
                    duration_ms=5,
                    model="fake-flash-model",
                    tier="flash",
                    claim_count=len(extraction.claims),
                )
            )
        return extraction


def test_default_output_path_is_timestamped_under_local_eval_directory() -> None:
    path = default_output_path(now=datetime(2026, 8, 29, 9, 8, 7, tzinfo=UTC))

    assert path == Path(".data/evals/memory_foundation_live_20260829_090807_000000.json")


def test_live_fixture_encodes_documented_relaxed_expectations() -> None:
    cases = json.loads(LIVE_EXPECTATIONS.read_text(encoding="utf-8"))["cases"]

    mem002 = cases["MEM-002"]["final"]
    assert "active_memory_count" not in mem002
    assert mem002["duplicate_active_max"] == [
        {"selector": {"state_dimension": "conflict_status"}, "max": 1}
    ]

    mem006_turn2 = cases["MEM-006"]["turns"][1]
    assert "claims" not in mem006_turn2
    assert "relations" not in mem006_turn2

    mem008_relation = cases["MEM-008"]["turns"][1]["relations"][0]
    assert set(mem008_relation["allowed"]) == {"complementary", "unrelated"}

    assert cases["MEM-004"]["final"]["context"]["fields"] == [
        {"path": "relationship_stage", "expected": "dating"}
    ]

    atomization = cases["MEM-014"]["final"]["active_counts"]
    assert atomization[0]["min"] == 2
    assert atomization[0].get("severity", "failure") == "failure"
    assert atomization[1]["min"] == 4
    assert atomization[1]["severity"] == "warning"


def test_record_selector_any_supports_alternatives_and_common_constraints() -> None:
    record = {
        "canonical_predicate": "interaction.response_engagement",
        "state_value": "normal",
        "perspective": "user_reported",
        "payload": {},
    }
    selector = {
        "perspective": "user_reported",
        "$any": [
            {"payload.canonical_concept": "contact_restored"},
            {
                "canonical_predicate": "interaction.response_engagement",
                "state_value": ["normal", "responsive"],
            },
        ],
    }

    assert _record_matches(record, selector)
    assert not _record_matches(record, {**selector, "perspective": "inferred"})


def test_record_selector_any_is_recursive_and_fails_when_no_branch_matches() -> None:
    record = {
        "canonical_predicate": "contact.status",
        "state_value": "restored",
    }

    assert _record_matches(
        record,
        {
            "$any": [
                {"canonical_predicate": "interaction.response_engagement"},
                {
                    "$any": [
                        {
                            "canonical_predicate": "contact.status",
                            "state_value": "restored",
                        },
                        {"payload.canonical_concept": "contact_restored"},
                    ]
                },
            ]
        },
    )
    assert not _record_matches(
        record,
        {
            "$any": [
                {"canonical_predicate": "interaction.response_engagement"},
                {"payload.canonical_concept": "contact_restored"},
            ]
        },
    )
    assert not _record_matches(record, {"$any": []})


async def test_case_filter_repeat_isolates_scopes_and_writes_redacted_report(
    tmp_path: Path,
) -> None:
    dataset = _write_dataset(
        tmp_path,
        [
            _case("LIVE-001", "\u8bb0\u4e00\u4e0b: she likes ramen."),
            _case(
                "LIVE-002",
                "\u8bb0\u4e00\u4e0b: she likes sushi.",
                scripted_marker="must-not-be-used-or-reported",
            ),
        ],
    )
    output = tmp_path / "reports" / "live.json"
    extractor = RecordingFlashExtractor()
    settings = _live_settings(api_key="top-secret-live-key")

    report = await evaluate_memory_foundation_live(
        dataset,
        output,
        settings=settings,
        case_ids=("LIVE-002",),
        repeat=2,
        extractor=extractor,
    )

    persisted_text = output.read_text(encoding="utf-8")
    persisted = json.loads(persisted_text)
    assert persisted == report
    assert report["status"] == "completed"
    assert report["execution_status"] == "passed"
    assert report["selected_case_ids"] == ["LIVE-002"]
    assert report["repeat"] == 2
    assert extractor.inputs == [
        "\u8bb0\u4e00\u4e0b: she likes sushi.",
        "\u8bb0\u4e00\u4e0b: she likes sushi.",
    ]

    assert len(report["cases"]) == 2
    scope_tuples = {
        (
            case["scope"]["user_id"],
            case["scope"]["relationship_id"],
            case["scope"]["conversation_id"],
        )
        for case in report["cases"]
    }
    assert len(scope_tuples) == 2
    assert {case["repeat"] for case in report["cases"]} == {1, 2}
    assert all(len(case["final"]["active_memories"]) == 1 for case in report["cases"])
    memory_ids = {
        case["final"]["active_memories"][0]["id"] for case in report["cases"]
    }
    assert len(memory_ids) == 2

    configuration = report["configuration"]
    assert configuration["memory_backend"] == "memory"
    assert configuration["flash_only"] is True
    assert configuration["strong_enabled"] is False
    assert configuration["verifier_enabled"] is False
    assert configuration["scripted_claims_used"] is False
    assert configuration["flash_model"] == "fake-flash-model"
    assert "top-secret-live-key" not in persisted_text
    assert "must-not-be-used-or-reported" not in persisted_text

    summary = report["summary"]
    assert summary["case_count"] == 2
    assert summary["turn_count"] == 2
    assert summary["flash_call_count"] == 2
    assert summary["strong_call_count"] == 0
    assert summary["completed_attempt_count"] == 2
    assert summary["failed_attempt_count"] == 0
    assert summary["total_tokens"] == 36
    assert summary["semantic_not_evaluated_count"] == 2


async def test_foundation_dataset_auto_loads_companion_semantic_fixture(
    tmp_path: Path,
) -> None:
    extractor = RecordingFlashExtractor()

    report = await evaluate_memory_foundation_live(
        FOUNDATION_DATASET,
        tmp_path / "mem016.json",
        settings=_live_settings(),
        case_ids=("MEM-016",),
        extractor=extractor,
    )

    assert extractor.inputs == []
    assert report["semantic_expectations"]["enabled"] is True
    assert report["semantic_expectations"]["version"] == (
        "memory-foundation-live-semantic-v1"
    )
    assert report["cases"][0]["execution_status"] == "passed"
    assert report["cases"][0]["semantic_status"] == "passed"
    assert report["summary"]["semantic_pass_count"] == 1
    assert report["summary"]["gate_match_rate"] == 1.0


async def test_failed_extraction_attempt_is_preserved_in_local_report(
    tmp_path: Path,
) -> None:
    dataset = _write_dataset(
        tmp_path,
        [_case("LIVE-FAIL", "\u8bb0\u4e00\u4e0b: she likes sushi.")],
    )
    output = tmp_path / "nested" / "partial.json"

    report = await evaluate_memory_foundation_live(
        dataset,
        output,
        settings=_live_settings(),
        extractor=FailingFlashExtractor(),
    )

    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted == report
    assert report["status"] == "partial"
    assert report["execution_status"] == "failed"
    assert report["summary"]["failed_case_count"] == 1
    assert report["summary"]["extraction_error_turn_count"] == 1
    assert report["summary"]["flash_call_count"] == 1
    assert report["summary"]["failed_attempt_count"] == 1
    assert report["summary"]["schema_validation_failure_count"] == 1

    case = report["cases"][0]
    assert case["status"] == "failed"
    assert case["execution_status"] == "failed"
    assert case["semantic_status"] == "not_evaluated"
    assert case["turns"][0]["status"] == "failed"
    assert case["turns"][0]["extraction_error"] == "invalid extraction response"
    attempt = case["turns"][0]["extraction_run"]["attempts"][0]
    assert attempt["status"] == "failed"
    assert attempt["tier"] == "flash"
    assert attempt["failure_category"] == "schema_validation"
    assert case["final"]["active_memories"] == []


async def test_invalid_repeat_fails_before_creating_a_report(tmp_path: Path) -> None:
    dataset = _write_dataset(
        tmp_path,
        [_case("LIVE-001", "\u8bb0\u4e00\u4e0b: she likes sushi.")],
    )
    output = tmp_path / "should-not-exist.json"

    with pytest.raises(ValueError, match="repeat must be at least 1"):
        await evaluate_memory_foundation_live(
            dataset,
            output,
            settings=_live_settings(),
            repeat=0,
            extractor=RecordingFlashExtractor(),
        )

    assert not output.exists()


async def test_declarative_expectations_cover_lifecycle_and_current_context(
    tmp_path: Path,
) -> None:
    active_text = "\u8bb0\u4e00\u4e0b: our current conflict is active."
    resolved_text = "\u8bb0\u4e00\u4e0b: our current conflict is resolved."
    dataset = _write_dataset(
        tmp_path,
        [_case_with_turns("LIVE-LIFECYCLE", active_text, resolved_text)],
    )
    expectations = _write_expectations(
        tmp_path,
        {
            "LIVE-LIFECYCLE": {
                "turns": [
                    {
                        "gate": {"should_extract": True},
                        "claims": [
                            {
                                "selector": {
                                    "canonical_predicate": "relationship.conflict_status",
                                    "state_dimension": "conflict_status",
                                    "state_value": "active",
                                    "perspective": "user_reported",
                                }
                            }
                        ],
                    },
                    {
                        "gate": {"should_extract": True},
                        "claims": [
                            {
                                "selector": {
                                    "canonical_predicate": "relationship.conflict_status",
                                    "state_dimension": "conflict_status",
                                    "state_value": "resolved",
                                    "perspective": "user_reported",
                                }
                            }
                        ],
                        "relations": [
                            {
                                "selector": {
                                    "canonical_predicate": "relationship.conflict_status",
                                    "state_value": "resolved",
                                },
                                "allowed": ["update"],
                            }
                        ],
                    },
                ],
                "final": {
                    "expected_active": [
                        {
                            "selector": {
                                "canonical_predicate": "relationship.conflict_status",
                                "state_value": "resolved",
                            }
                        }
                    ],
                    "superseded": [
                        {
                            "selector": {
                                "canonical_predicate": "relationship.conflict_status",
                                "state_value": "active",
                            }
                        }
                    ],
                    "forbidden_active": [
                        {
                            "selector": {
                                "canonical_predicate": "relationship.conflict_status",
                                "state_value": "active",
                            }
                        }
                    ],
                    "duplicate_active_max": [
                        {
                            "selector": {
                                "canonical_predicate": "relationship.conflict_status"
                            },
                            "max": 1,
                        }
                    ],
                    "protected_confirmed": [
                        {
                            "selector": {
                                "canonical_predicate": "relationship.conflict_status",
                                "state_value": "resolved",
                            }
                        }
                    ],
                    "context": {
                        "expected_current": [
                            {
                                "selector": {
                                    "canonical_predicate": "relationship.conflict_status",
                                    "state_value": "resolved",
                                }
                            }
                        ],
                        "forbidden_current": [
                            {
                                "selector": {
                                    "canonical_predicate": "relationship.conflict_status",
                                    "state_value": "active",
                                }
                            }
                        ],
                        "fields": [
                            {
                                "path": "relationship_evidence.conflict_status",
                                "expected": "resolved",
                            }
                        ],
                    },
                },
            }
        },
    )
    output = tmp_path / "semantic-pass.json"

    report = await evaluate_memory_foundation_live(
        dataset,
        output,
        settings=_live_settings(),
        extractor=SequenceFlashExtractor(
            AtomicExtraction(claims=[_conflict_claim("active", active_text)]),
            AtomicExtraction(claims=[_conflict_claim("resolved", resolved_text)]),
        ),
        expectation_path=expectations,
    )

    case = report["cases"][0]
    assert case["execution_status"] == "passed"
    assert case["semantic_status"] == "passed"
    assert case["semantic_failures"] == []
    assert case["semantic_warnings"] == []
    assert all(assertion["passed"] for assertion in case["semantic_assertions"])
    summary = report["summary"]
    assert summary["execution_pass_count"] == 1
    assert summary["semantic_pass_count"] == 1
    assert summary["gate_match_rate"] == 1.0
    assert summary["canonical_match_rate"] == 1.0
    assert summary["perspective_match_rate"] == 1.0
    assert summary["relation_match_rate"] == 1.0
    assert summary["lifecycle_match_rate"] == 1.0
    assert summary["context_match_rate"] == 1.0
    assert summary["stale_active_memory_count"] == 0
    assert summary["duplicate_active_memory_count"] == 0
    assert summary["confirmed_overwrite_violation_count"] == 0


async def test_execution_success_is_distinct_from_semantic_warning_and_failure(
    tmp_path: Path,
) -> None:
    warning_text = "\u8bb0\u4e00\u4e0b: she likes sushi."
    failure_text = "\u8bb0\u4e00\u4e0b: she likes ramen."
    dataset = _write_dataset(
        tmp_path,
        [
            _case("LIVE-WARNING", warning_text),
            _case("LIVE-SEMANTIC-FAIL", failure_text),
        ],
    )
    preference_selector = {"canonical_predicate": "preference.food.cuisine"}
    expectations = _write_expectations(
        tmp_path,
        {
            "LIVE-WARNING": {
                "turns": [
                    {"claims": [{"selector": preference_selector}]}
                ],
                "final": {
                    "active_counts": [
                        {
                            "selector": preference_selector,
                            "min": 2,
                            "severity": "warning",
                        }
                    ]
                },
            },
            "LIVE-SEMANTIC-FAIL": {
                "turns": [
                    {
                        "claims": [
                            {
                                "selector": {
                                    "canonical_predicate": "relationship.conflict_status"
                                }
                            }
                        ]
                    }
                ],
                "final": {
                    "forbidden_active": [
                        {"selector": preference_selector}
                    ],
                    "duplicate_active_max": [
                        {"selector": preference_selector, "max": 0}
                    ],
                    "protected_confirmed": [
                        {
                            "selector": {
                                "canonical_predicate": "relationship.conflict_status"
                            }
                        }
                    ],
                    "context": {
                        "expected_current": [
                            {
                                "selector": {
                                    "canonical_predicate": "relationship.conflict_status"
                                }
                            }
                        ]
                    },
                },
            },
        },
    )

    report = await evaluate_memory_foundation_live(
        dataset,
        tmp_path / "semantic-mixed.json",
        settings=_live_settings(),
        extractor=RecordingFlashExtractor(),
        expectation_path=expectations,
    )

    assert report["execution_status"] == "passed"
    assert [case["execution_status"] for case in report["cases"]] == [
        "passed",
        "passed",
    ]
    assert [case["semantic_status"] for case in report["cases"]] == [
        "warning",
        "failed",
    ]
    summary = report["summary"]
    assert summary["execution_pass_count"] == 2
    assert summary["execution_fail_count"] == 0
    assert summary["semantic_pass_count"] == 0
    assert summary["semantic_warning_count"] == 1
    assert summary["semantic_fail_count"] == 1
    assert summary["canonical_match_rate"] == 0.5
    assert summary["lifecycle_match_rate"] == 0.0
    assert summary["context_match_rate"] == 0.0
    assert summary["stale_active_memory_count"] == 1
    assert summary["duplicate_active_memory_count"] == 1
    assert summary["confirmed_overwrite_violation_count"] == 1


def _live_settings(*, api_key: str = "fake-api-key") -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="openai-compatible",
        llm_api_key=SecretStr(api_key),
        llm_base_url="https://models.example.test/v1",
        memory_backend="sqlite",
        memory_extraction_provider="disabled",
        memory_extraction_model="fake-flash-model",
    )


def _write_dataset(tmp_path: Path, cases: list[dict[str, object]]) -> Path:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases),
        encoding="utf-8",
    )
    return path


def _write_expectations(
    tmp_path: Path,
    cases: dict[str, dict[str, object]],
) -> Path:
    path = tmp_path / "live_expectations.json"
    path.write_text(
        json.dumps(
            {"version": "test-live-expectations-v1", "cases": cases},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _case(
    case_id: str,
    text: str,
    *,
    scripted_marker: str | None = None,
) -> dict[str, object]:
    turn: dict[str, object] = {"input": text, "expect": {"gate_should_extract": True}}
    if scripted_marker is not None:
        turn["scripted_claims"] = [{"summary": scripted_marker}]
    return {
        "id": case_id,
        "category": "live_test",
        "description": "Synthetic no-network live-runner fixture.",
        "reference_time": "2026-08-29T12:00:00+08:00",
        "turns": [turn],
        "expected_final": {},
    }


def _case_with_turns(case_id: str, *texts: str) -> dict[str, object]:
    return {
        "id": case_id,
        "category": "live_test",
        "description": "Synthetic lifecycle fixture.",
        "reference_time": "2026-08-29T12:00:00+08:00",
        "turns": [{"input": text} for text in texts],
        "expected_final": {},
    }


def _preference_claim(evidence: str) -> AtomicClaim:
    return AtomicClaim(
        claim_id="sushi-preference",
        kind=MemoryKind.PREFERENCE,
        subject="partner",
        predicate="likes_cuisine",
        summary="Partner likes sushi.",
        evidence_spans=[evidence],
        time_kind=TimeKind.TIMELESS,
        confidence=0.99,
        explicitness=EvidenceExplicitness.EXPLICIT,
        payload={"preference": "sushi", "preference_type": "cuisine"},
    )


def _conflict_claim(value: str, evidence: str) -> AtomicClaim:
    return AtomicClaim(
        claim_id=f"conflict-{value}",
        kind=MemoryKind.RELATIONSHIP_STATE,
        subject="relationship",
        predicate="conflict_status",
        summary=f"Current conflict state is {value}.",
        evidence_spans=[evidence],
        confidence=0.99,
        explicitness=EvidenceExplicitness.EXPLICIT,
        payload={"state_dimension": "conflict_status", "state_value": value},
    )

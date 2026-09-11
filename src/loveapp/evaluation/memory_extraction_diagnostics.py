"""Stage attribution shared by the semantic extraction evaluation harnesses."""

from typing import Any


def extraction_failure_stages(
    attempts: list[dict[str, Any]],
    diagnostic: dict[str, Any] | None = None,
    *,
    runtime_error: str | None = None,
) -> list[str]:
    stages: list[str] = []
    for attempt in attempts:
        if attempt.get("status") != "failed":
            continue
        category = str(attempt.get("failure_category") or "").casefold()
        if category in {"json_syntax", "empty_response", "root_shape", "format"}:
            stage = "MODEL_PARSE_FAILURE"
        elif category in {"schema_validation", "unsupported_enum", "semantic_gate_contract"}:
            stage = "SCHEMA_VALIDATION_FAILURE"
        elif category == "empty_claims" and attempt.get("stage") == "detailed":
            stage = "EMPTY_STAGE2_OUTPUT"
        elif category in {"routing_ambiguity", "routing_abstention"}:
            stage = category.upper()
        elif category in {"transport", "transport_or_runtime"}:
            stage = "TRANSPORT_OR_RUNTIME_FAILURE"
        else:
            stage = "SEMANTIC_VALIDATION_FAILURE"
        stages.append(stage)
    for abstention in (diagnostic or {}).get("stage2", {}).get("abstentions", []):
        reason = abstention.get("reason")
        if reason in {"ROUTING_AMBIGUITY", "ROUTING_ABSTENTION"}:
            stages.append(reason)
    if runtime_error and not stages:
        stages.append("TRANSPORT_OR_RUNTIME_FAILURE")
    return list(dict.fromkeys(stages))

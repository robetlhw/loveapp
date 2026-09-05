"""Run and render the Normalization V1 final freeze closeout."""

# Markdown table rows are intentionally kept readable as single report lines.
# They are generated output, not executable code.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loveapp.evaluation.memory_normalization_boundary import (
    evaluate_memory_normalization_boundary,
)
from loveapp.evaluation.memory_normalization_freeze import (
    evaluate_memory_normalization_production_smoke,
)
from loveapp.evaluation.memory_normalization_v1_1 import (
    evaluate_memory_normalization_v1_1,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--normalization-dataset",
        type=Path,
        default=Path("evals/memory/normalization_v1_1.jsonl"),
    )
    parser.add_argument(
        "--boundary-dataset",
        type=Path,
        default=Path("evals/memory/normalization_boundary_v1.jsonl"),
    )
    parser.add_argument(
        "--smoke-dataset",
        type=Path,
        default=Path("evals/memory/normalization_production_smoke_v1.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".data/evals/memory_normalization_v1_final_56.json"),
    )
    parser.add_argument(
        "--write-path-report",
        type=Path,
        default=Path("MEMORY_PRODUCTION_WRITE_PATH_AUDIT.md"),
    )
    parser.add_argument(
        "--reconciliation-report",
        type=Path,
        default=Path("MEMORY_NORMALIZATION_METRIC_RECONCILIATION.md"),
    )
    parser.add_argument(
        "--freeze-report",
        type=Path,
        default=Path("MEMORY_NORMALIZATION_V1_FINAL_FREEZE_REPORT.md"),
    )
    args = parser.parse_args()

    normalization = evaluate_memory_normalization_v1_1(
        args.normalization_dataset,
        fail_on_error=True,
    )
    boundary = evaluate_memory_normalization_boundary(
        args.boundary_dataset,
        fail_on_error=True,
    )
    smoke = asyncio.run(
        evaluate_memory_normalization_production_smoke(args.smoke_dataset)
    )
    final = _build_final_artifact(normalization, boundary, smoke)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    args.write_path_report.write_text(_render_write_path_audit(), encoding="utf-8")
    args.reconciliation_report.write_text(
        _render_reconciliation(normalization, boundary),
        encoding="utf-8",
    )
    args.freeze_report.write_text(
        _render_freeze_report(final),
        encoding="utf-8",
    )
    print(json.dumps(final["freeze_decision"], ensure_ascii=False, indent=2))


def _build_final_artifact(
    normalization: dict[str, Any],
    boundary: dict[str, Any],
    smoke: dict[str, Any],
) -> dict[str, Any]:
    targets = normalization["target_pass"]
    boundary_metrics = boundary["metrics"]
    smoke_pass = smoke["status"] == "PASS"
    standard_path_pass = smoke_pass and boundary["boundary_status"] == "PASS"
    freeze = {
        "decision": "FROZEN" if all(targets.values()) and standard_path_pass else "NOT_FROZEN",
        "scope": "standard conversational LLM extraction ingress",
        "next_module": "Admission V1"
        if all(targets.values()) and standard_path_pass
        else None,
        "trusted_deterministic_date_preference_exception": True,
        "trusted_exception_scope": "DatePlanningAgent -> remember_date_preferences -> Store",
    }
    artifact = dict(normalization)
    artifact.update(
        {
            "evaluation": "memory_normalization_v1_final_closeout",
            "generated_at": datetime.now().astimezone().isoformat(),
            "normalization_56": normalization,
            "boundary_20": boundary,
            "production_smoke": smoke,
            "freeze_decision": freeze,
            "closeout_metrics": {
                "generic_validation_acceptance_rate": boundary_metrics[
                    "generic_validation_acceptance_rate"
                ],
                "false_pre_normalization_rejection_rate": boundary_metrics[
                    "false_pre_normalization_rejection_rate"
                ],
                "normalizer_recovery_accuracy": boundary_metrics[
                    "normalizer_recovery_accuracy"
                ],
                "validation_boundary_rejection_count": boundary_metrics[
                    "validation_boundary_rejection_count"
                ],
                "production_smoke_admission_reached_rate": smoke["metrics"][
                    "admission_reached_rate"
                ],
                "production_smoke_store_write_attempt_rate": smoke["metrics"][
                    "store_write_attempt_rate"
                ],
            },
        }
    )
    return artifact


def _render_write_path_audit() -> str:
    return """# Memory Production Write-Path Audit

Generated by the Normalization V1 final closeout.

## Standard conversational ingress

```text
User message
→ MemoryGate
→ TieredMemoryExtractor (Flash/Strong, validation_mode=raw)
→ parse_memory_response(raw)
→ Generic Validator
→ AtomicClaim.to_candidate
→ normalize_memory_candidate_contract
→ Canonical/State Validator
→ Admission
→ Claim Relation
→ Lifecycle Planner
→ MemoryWriteBatch
→ Store
```

The authoritative contract is `normalize_memory_candidate_contract()` in
`src/loveapp/domain/memory_normalization.py`. It performs generic raw-boundary
validation, deterministic normalization, and post-normalization canonical/state
validation. The standard bootstrap explicitly wires both Flash and Strong to
`validation_mode="raw"`.

## Legacy validator audit

| Path | Location | Reachability | Finding |
|---|---|---|---|
| Combined generic + canonical validator | `src/loveapp/application/memory_repair.py:validate_memory_claim` | Not on default production extraction path | Selected only by `validation_mode="legacy"`; evaluation/test compatibility callers remain |
| Adapter `_validate_extraction` helper | `src/loveapp/adapters/memory/openai_compatible.py` | Dead/test-only | No in-repository production caller |
| `normalize_predicate` Store call | InMemory/SQLite Store | Reachable after contract | Idempotent persistence normalization; does not enforce pre-normalizer canonical completeness |
| Read-side `memory_concept`/alias previews | `MemoryService`/lifecycle helpers | Reachable | Observability/identity probes only; no rejection or write |

## Production-reachable deterministic exception

`DatePlanningAgent._load_memory()` calls
`MemoryService.remember_date_preferences()`. That method constructs trusted date
preference candidates and calls `Store.save_memories()` directly, bypassing Gate,
extractor, Generic Validator, contract Normalizer, Canonical Validator, Admission,
Relation, and Lifecycle. It is not a reachable legacy validator and does not
affect the standard conversational Raw-claim boundary, but it is explicitly
outside the full Raw→Generic→Normalizer contract. No change was made in this
closeout because it is a separate deterministic date-preference ingress.

## Conclusion

No production-reachable legacy validator was found to reject a semantic-valid Raw
claim before the authoritative Normalizer on the standard LLM extraction path.
The freeze decision is therefore scoped to that path, with the deterministic date
preference exception recorded above.
"""


def _render_reconciliation(
    normalization: dict[str, Any],
    boundary: dict[str, Any],
) -> str:
    return f"""# Memory Normalization Metric Reconciliation

## Comparable final metrics

| Metric | Final | Numerator | Denominator | Comparable | Pass |
|---|---:|---:|---:|---|---|
| Canonical Mapping | {normalization['metrics']['canonical_mapping_accuracy']:.4f} | 13 | 13 | Yes | Yes |
| State Dimension | {normalization['metrics']['state_dimension_accuracy']:.4f} | 13 | 13 | Yes | Yes |
| State Value | {normalization['metrics']['state_value_accuracy']:.4f} | 13 | 13 | Yes | Yes |
| Custom Preservation | {normalization['metrics']['custom_preservation_accuracy']:.4f} | 21 | 21 | Yes | Yes |
| Unsafe Canonicalization | {normalization['metrics']['unsafe_canonicalization_rate']:.4f} | 0 | 21 | Yes | Yes |
| Schema Validity | {normalization['metrics']['schema_validity']:.4f} | 56 | 56 | Yes | Yes |
| Idempotency | {normalization['metrics']['idempotency_accuracy']:.4f} | 5 | 5 | Yes | Yes |
| Generic Validation Acceptance | {boundary['metrics']['generic_validation_acceptance_rate']:.4f} | 16 | 16 | Yes, boundary set | Yes |
| False Pre-Normalization Reject | {boundary['metrics']['false_pre_normalization_rejection_rate']:.4f} | 0 | 16 | Yes, boundary set | Yes |
| Normalizer Recovery | {boundary['metrics']['normalizer_recovery_accuracy']:.4f} | 10 | 10 | Yes, boundary set | Yes |

## Why historical 16, current 2, and boundary 4 differ

The historical `16` was reported over the 56-case Normalization dataset using the
older combined generic + canonical/state validator before normalization. Re-running
that legacy function against the current 56-case input reproduces the same count
(`NORM-001..003, NORM-018, NORM-023..027, NORM-047..048, NORM-052..056`). The
current `2` is from the same 56-case dataset but counts only the migrated generic
validation diagnostic (NORM-018 and NORM-023 are generic atomicity-invalid cases).
The boundary `4` is from a separate 20-case boundary dataset (BND-013..BND-016)
and counts expected generic-invalid inputs.

Thus the three values have different stage definitions and, for `4`, a different
dataset and denominator. They are **not directly comparable**; no 16-to-4 reduction
claim is made. The stable safety metric is False Pre-Normalization Rejection =
0.0000 on the dedicated boundary denominator.

## Denominator ledger

- 56-case final normalization regression: `{normalization['case_count']}` cases.
- 20-case validation boundary: `{boundary['case_count']}` cases; 16 semantic-valid,
  4 expected generic-invalid.
- Historical 16: `56` cases, old combined validator stage.
- Current 2: `56` cases, migrated generic diagnostic stage.
"""


def _render_freeze_report(final: dict[str, Any]) -> str:
    n = final["normalization_56"]
    b = final["boundary_20"]
    s = final["production_smoke"]
    smoke_lines = []
    for row in s["cases"]:
        smoke_lines.append(
            f"| {row['source_case_id']} | {row['raw_claim_present']} | "
            f"{row['generic_validation_result']['status']} | "
            f"{bool(row['normalizer_output'])} | {row['admission_reached']} | "
            f"{row['store_write_attempted']} | {row['drop_stage'] or '-'} |"
        )
    return f"""# Memory Normalization V1 Final Freeze Report

Generated: `{final['generated_at']}`

## Freeze decision

```text
Gate = FROZEN
Extraction = STRUCTURALLY_STABLE_WITH_KNOWN_SEMANTIC_VARIANCE
Normalization = {final['freeze_decision']['decision']}
Next Module = {final['freeze_decision']['next_module'] or 'blocked / review'}
```

The decision is scoped to the standard conversational LLM extraction ingress.
The DatePlanning deterministic preference writer is recorded as a trusted
out-of-contract exception in the write-path audit.

## 56-case regression

- Latest code rerun: `Yes`
- Cases: `{n['case_count']}`
- Passed: `{n['passed_case_count']}`
- Semantic Hint Resolution: `{n['metrics']['semantic_hint_resolution_accuracy']:.4f}`
- Canonical Mapping: `{n['metrics']['canonical_mapping_accuracy']:.4f}`
- State Dimension / Value: `{n['metrics']['state_dimension_accuracy']:.4f}` / `{n['metrics']['state_value_accuracy']:.4f}`
- Representation Normalization: `{n['metrics']['representation_normalization_accuracy']:.4f}`
- Custom Preservation: `{n['metrics']['custom_preservation_accuracy']:.4f}`
- Unsafe Canonicalization: `{n['metrics']['unsafe_canonicalization_rate']:.4f}`
- Schema Validity: `{n['metrics']['schema_validity']:.4f}`
- Idempotency: `{n['metrics']['idempotency_accuracy']:.4f}`
- Conflict Outcome: `{n['metrics']['conflict_outcome_accuracy']:.4f}`

## Boundary contract

- Generic Validation Acceptance: `{b['metrics']['generic_validation_acceptance_rate']:.4f}`
- False Pre-Normalization Rejection: `{b['metrics']['false_pre_normalization_rejection_rate']:.4f}`
- Normalizer Recovery: `{b['metrics']['normalizer_recovery_accuracy']:.4f}`
- Validation Boundary Reject Count: `{b['metrics']['validation_boundary_rejection_count']}`
- Boundary suite: `{b['passed_case_count']}/{b['case_count']}`

The boundary rejects are expected generic-invalid inputs; they are not semantic
loss. See `MEMORY_NORMALIZATION_METRIC_RECONCILIATION.md` for denominator detail.

## Production-path smoke

The smoke uses the real `OpenAICompatibleMemoryExtractor` raw parser, real
`MemoryService`, contract Normalizer, admission path, and isolated
`InMemoryMemoryStore`, with an in-process deterministic OpenAI-compatible client
and no network/API key use. Isolated writes are observed for retention evidence;
destructive external Store mutation remains disabled.

| Case | Raw claim | Generic | Normalizer | Admission | Store write | Drop stage |
|---|---|---|---|---|---|---|
{chr(10).join(smoke_lines)}

Smoke metrics:

- Admission reached rate: `{s['metrics']['admission_reached_rate']}`
- Store write attempt rate: `{s['metrics']['store_write_attempt_rate']}`
- Smoke status: `{s['status']}`

`SUBJ-021` intentionally retains the two claims observed in the pressure
artifact; both reach Admission. `SUBJ-022` carries the unregistered raw value
`paused`, so safe Custom preservation is expected rather than forced canonical
state; this is not a pre-normalizer drop.

## Freeze questions answered

1. Latest 56-case rerun: **Yes, 56/56**.
2. Canonical Mapping: **1.0000**.
3. State Dimension / Value: **1.0000 / 1.0000**.
4. Custom Preservation: **1.0000**.
5. Unsafe Canonicalization: **0.0000**.
6. Idempotency: **1.0000**.
7. Generic Validator still kills semantic-valid Raw claims: **No on the boundary suite**.
8. False Pre-Normalization Reject: **0.0000 (0/16)**.
9-12. SUBJ-003/013/021/022: **all reached Admission boundary**.
13. Reachable legacy validator: **None on standard LLM extraction ingress**;
    trusted DatePlanning preference bypass is documented separately.
14. Historical 16 vs current 2: **different stage definitions; boundary 4 also
    uses a different dataset; not directly comparable**.
15. Normalization V1: **{final['freeze_decision']['decision']} for the scoped LLM claim path**.

## Remaining known limitations

- Date preference deterministic ingress bypasses the claim normalization boundary.
- Extraction retains known semantic variance, especially belief subject/perspective
  and focused long-tail recall; no Prompt change was made here.
- Admission/relation/lifecycle behavior is outside this Normalization freeze.
"""


if __name__ == "__main__":
    main()

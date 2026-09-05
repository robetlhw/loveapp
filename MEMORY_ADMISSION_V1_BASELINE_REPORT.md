# Memory Admission V1 Baseline Report

Generated: `2026-09-03T11:48:12.382007+08:00`  
Dataset: `evals\memory\admission_v1.jsonl`  
Admission production policy modified: `False`  
Gate / Extraction / Relation / Lifecycle scored: `False`

## Baseline Status

Admission V1: **BASELINE_PASS_POLICY_REVIEW_PENDING**
Policy snapshot drift: `False`

## Strict Metrics (64 EXACT cases)

| Metric | Result |
|---|---:|
| Strict cases | 64 |
| Passed | 64 |
| Decision accuracy | 1.0000 |
| Reason accuracy | 1.0000 |
| Score MAE | 0.0000000000 |
| Score max abs error | 0.0000000000 |

## Decision Precision / Recall

| Decision | Precision | Recall | Support |
|---|---:|---:|---:|
| CONFIRM | 1.0000 | 1.0000 | 12 |
| PROPOSE | 1.0000 | 1.0000 | 18 |
| STRONG_REVIEW | 1.0000 | 1.0000 | 21 |
| REJECT | 1.0000 | 1.0000 | 13 |

## Per MemoryKind

| Kind | Cases | Passed | Accuracy |
|---|---:|---:|---:|
| action_intent | 7 | 7 | 1.0000 |
| advice_outcome | 5 | 5 | 1.0000 |
| interaction_event | 4 | 4 | 1.0000 |
| interaction_pattern | 10 | 10 | 1.0000 |
| planned_event | 8 | 8 | 1.0000 |
| preference | 12 | 12 | 1.0000 |
| relationship_state | 8 | 8 | 1.0000 |
| stable_fact | 10 | 10 | 1.0000 |

## Per Slice

| Slice | Cases | Passed | Accuracy |
|---|---:|---:|---:|
| conflict | 3 | 3 | 1.0000 |
| custom | 3 | 3 | 1.0000 |
| evidence_grounding | 2 | 2 | 1.0000 |
| explicitness | 5 | 5 | 1.0000 |
| kind_decision_matrix | 32 | 32 | 1.0000 |
| pattern_evidence | 5 | 5 | 1.0000 |
| perspective | 7 | 7 | 1.0000 |
| relationship_state_safety | 1 | 1 | 1.0000 |
| requires_inference | 3 | 3 | 1.0000 |
| source_type | 1 | 1 | 1.0000 |
| temporal_shape | 2 | 2 | 1.0000 |

## Safety / Governance

| Metric | Result |
|---|---:|
| invalid_evidence_reject_recall | 1.0000 |
| speculative_relationship_state_reject_recall | 1.0000 |
| custom_direct_confirm_violation_rate | 0.0000 |
| user_belief_direct_confirm_violation_rate | 0.0000 |
| model_inferred_direct_confirm_violation_rate | 0.0000 |
| inference_direct_confirm_violation_rate | 0.0000 |
| conflict_direct_confirm_violation_rate | 0.0000 |
| relationship_state_confirm_precision | 1.0000 |
| reject_precision | 1.0000 |
| dangerous_direct_confirm_violation_count | 0 |

## Pattern / Temporal Diagnostics

```json
{
  "pattern": {
    "case_count": 10,
    "frequency_detection_accuracy": 1.0,
    "multi_evidence_detection_accuracy": 1.0,
    "corroboration_handling_accuracy": 1.0,
    "unsupported_pattern_direct_confirm_violation_rate": 0.0
  },
  "temporal": {
    "planned_event_temporal_shape_accuracy": 1.0,
    "invalid_temporal_reason_accuracy": 1.0,
    "invalid_temporal_case_count": 2
  }
}
```

## Failed Strict Cases

| Case | Decision | Reason | Score | Primary error |
|---|---|---|---:|---|
| none | - | - | - | - |

## Required Findings

1. Strict Accuracy is `1.0000` (`64/64`).
2. CONFIRM, PROPOSE, STRONG_REVIEW, and REJECT precision/recall are all `1.0000`.
3. No weakest MemoryKind was observed; all eight kinds scored `1.0000`.
4. No weakest slice was observed; all strict slices scored `1.0000`.
5. No strict USER_BELIEF claim was directly confirmed.
6. No strict MODEL_INFERRED claim was directly confirmed.
7. No strict Custom claim was directly confirmed.
8. No strict conflict or requires-inference claim was directly confirmed.
9. Invalid evidence reject recall is `1.0000`.
10. Speculative relationship-state reject recall is `1.0000`.
11. Frequency, multiple-evidence, corroboration, and unsupported-pattern diagnostics all match the Golden contract.
12. Invalid planned-event temporal shape currently becomes `PROPOSE(invalid_temporal_shape)`, including a score clamped to zero.
13. Score MAE and maximum error are zero at report precision; policy snapshot drift is `False`.
14. High-risk direct confirm should remain a product decision; do not change it before replaying existing lifecycle transitions.
15. USER_BELIEF floor `0.15` needs calibration. MemoryService currently also applies a raw-confidence floor, so Admission is not the only guard.
16. Hearsay floor `0.35` needs calibration. MemoryService's default tentative raw-confidence floor is stricter than this Admission floor.
17. Unknown subjects need defense-in-depth review; the upstream subject contract remains the current primary boundary.
18. ACTION_INTENT TTL `14` is consumed by MemoryService and written to `expires_at`; it is not a dead policy field.
19. STRONG_REVIEW invokes StrongClaimVerifier only when one is configured; the call does not require an existing relation target. Unverified or failed review normally remains PROPOSED.
20. Next step is policy review and targeted calibration/integration analysis, not production-policy remediation or freeze declaration.

Policy-review cases are observe-only and excluded from strict accuracy.
See `MEMORY_ADMISSION_POLICY_REVIEW.md` and `MEMORY_ADMISSION_STRONG_REVIEW_AUDIT.md` for governance diagnostics.

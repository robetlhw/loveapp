# Memory Behavioral Anchor Test v0.1

Dataset: `evals\memory\memory_behavioral_anchor_v0_1.jsonl`
Dataset SHA256: `c4023316dcc79433e60ec1284d518c9d561bd00828d16141cdc56bf136b206b0`
Mode: `two_stage`
Store mutation permitted: `False`

## Metrics

| Metric | Result |
|---|---:|
| overall_anchor_pass_rate | 0.7857 |
| semantic_role_accuracy | 0.8929 |
| memory_kind_accuracy | 1.0 |
| event_type_accuracy | 0.9333 |
| perspective_accuracy | 1.0 |
| semantic_coverage | 0.9 |
| new_event_safety_accuracy | 0.8333 |
| enrichment_detection_precision | 0.7778 |
| enrichment_detection_recall | 0.8571 |
| false_enrichment_count | 1 |
| false_enrichment_rate | 0.1667 |
| ambiguous_target_rejection_rate | 1.0 |
| refinement_vs_update_accuracy | 0.5 |
| multi_claim_coverage | 1.0 |
| model_failure_count | 0 |

Overall: `22/28` cases passed.

## Case diagnostics

| Case | Category | Result | First failure |
|---|---|---|---|
| A01 | foundation | PASS | - |
| A02 | foundation | PASS | - |
| A03 | new_event | PASS | - |
| A04 | new_event | PASS | - |
| A05 | pattern_state | FAIL | Stage2_semantic_decomposition |
| A06 | pattern_state | PASS | - |
| A07 | epistemics | PASS | - |
| A08 | foundation | PASS | - |
| A09 | completion | PASS | - |
| A10 | completion | PASS | - |
| A11 | completion | FAIL | Resolver |
| A12 | completion | FAIL | Stage2_semantic_decomposition |
| A13 | new_event_safety | PASS | - |
| A14 | new_event_safety | FAIL | Stage2_forbidden_semantics |
| A15 | new_event_safety | PASS | - |
| A16 | multi_claim | PASS | - |
| A17 | multi_claim | PASS | - |
| A18 | pattern_state | PASS | - |
| A19 | refinement_update | PASS | - |
| A20 | refinement_update | FAIL | Stage2_semantic_decomposition |
| A21 | multi_claim | PASS | - |
| A22 | multi_claim | PASS | - |
| A23 | target_safety | PASS | - |
| A24 | target_safety | FAIL | Resolver |
| A25 | custom_attribute | PASS | - |
| A26 | custom_attribute | PASS | - |
| A27 | target_safety | PASS | - |
| A28 | target_safety | PASS | - |

## Baseline comparison

Status: `compared`
Old pass → new fail: `-`
Old fail → new pass: `-`
Unchanged: `28` cases

## Interpretation

This is a live semantic regression diagnostic. Gold describes behavior, not production class names.
No Store mutation, lifecycle commit, ontology expansion, or prompt change is authorized by this run.

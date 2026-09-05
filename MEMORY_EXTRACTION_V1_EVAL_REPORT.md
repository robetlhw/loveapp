# Memory Extraction V1 Evaluation Report

Generated: `2026-09-02T17:33:10.432694+08:00`  
Dataset: `evals\memory\extraction_v1_regression_19.jsonl`  
Dataset SHA256: `08da513899bdf719135ddd865dfd9859dd92ba70283b18e5d9fccb243556a058`  
Gate participates in scoring: `False`  
Canonical/state fields participate in Extraction scoring: `False`  
PendingMemoryContext path: `ExtractionV1Case.pending_memory_context -> PendingMemoryContext.model_validate -> _build_prompt(runtime_context.pending_memory_context)`  
LangSmith requested: `False`  
LangSmith enabled: `False`  
LangSmith disabled reason: `not_requested`  
LangSmith dataset: `loveapp-memory-extraction-v1-70`

## Flash / Repair / Cascade

| Metric | Flash Raw | Flash Post-Repair | Production Cascade |
|---|---:|---:|---:|
| Claim Recall | 1.0000 | 0.9130 | 0.9130 |
| Spurious Claim Rate | 0.0000 | 0.0000 | 0.0000 |
| Kind Accuracy | 0.9565 | 0.9524 | 0.9524 |
| Subject Accuracy | 1.0000 | 1.0000 | 1.0000 |
| Perspective Accuracy | 0.0435 | 1.0000 | 1.0000 |
| Atomization Accuracy | 1.0000 | 0.8750 | 0.8750 |
| Context Reply Recall | 1.0000 | 1.0000 | 1.0000 |
| Negative FP Rate | 0.0000 | 0.0000 | 0.0000 |

## Production Cascade Slices

### Business

| Slice | Cases | Claim Recall | Spurious Rate |
|---|---:|---:|---:|
| atomization | 8 | 0.8571 | 0.0000 |
| context_reply | 11 | 1.0000 | 0.0000 |

### Length

| Slice | Cases | Claim Recall | Spurious Rate |
|---|---:|---:|---:|
| medium | 8 | 0.8571 | 0.0000 |
| short | 11 | 1.0000 | 0.0000 |

### Difficulty

| Slice | Cases | Claim Recall | Spurious Rate |
|---|---:|---:|---:|
| hard | 18 | 0.9091 | 0.0000 |
| medium | 1 | 1.0000 | 0.0000 |

### Noise

| Slice | Cases | Claim Recall | Spurious Rate |
|---|---:|---:|---:|
| clean | 16 | 0.9091 | 0.0000 |
| noisy | 3 | 1.0000 | 0.0000 |

### Context_Reply

| Slice | Cases | Claim Recall | Spurious Rate |
|---|---:|---:|---:|
| actor | 3 | 1.0000 | 0.0000 |
| cause | 3 | 1.0000 | 0.0000 |
| interaction_state | 2 | 1.0000 | 0.0000 |
| refusal | 1 | 0.0000 | 0.0000 |
| topic_switch | 1 | 1.0000 | 0.0000 |
| unknown | 1 | 0.0000 | 0.0000 |

## Error Taxonomy

| Error | Cases |
|---|---:|
| EMPTY_POSITIVE | 1 |
| KIND_ERROR | 1 |
| MISSED_CLAIM | 1 |

## Model Telemetry

| Component | Calls | Failures | p50 ms | p95 ms | Total tokens |
|---|---:|---:|---:|---:|---:|
| Flash | 19 | 0 | 2340.49 | 3744.69 | 117332 |
| Strong | 0 | 0 | 0.00 | 0.00 | 0 |
| Semantic matcher | 33 | 0 | 2584.22 | 3020.12 | 32921 |

## Manual Semantic Review

Fail-closed semantic matcher cases: none.

## Required Answers

1. Flash Raw Claim Recall: `1.0000`
2. Post-Repair Claim Recall: `0.9130`
3. Production Cascade Claim Recall: `0.9130`
4. Spurious Claim Rate (Raw / Repair / Cascade): `0.0000 / 0.0000 / 0.0000`
5. Perspective Accuracy: `1.0000`
6. USER_BELIEF Perspective Accuracy: `0.0000`; mixed USER_REPORTED + USER_BELIEF cases: `0.0000`
7. Atomization Accuracy: `0.8750`
8. Context Reply Recall: `1.0000`
9. Empty Positive Rate: `0.0588`
10. Negative Restraint FP Rate: `0.0000`
11. Noisy vs clean Claim Recall: `1.0000 vs 0.9091`; delta `+0.0909`. Spurious Rate: `0.0000 vs 0.0000`; delta `+0.0000`
12. Short / medium / long Claim Recall: `1.0000 / 0.8571 / 0.0000`; max-min gap `0.1429`.
13. Safe Repair helped / hurt / unchanged: `0 / 1 / 18`; net matched-claim delta `-2`, recall delta `-0.0870`.
14. Strong Upgrade helped / hurt / unchanged / triggered rate: `0 / 0 / 0 / 0.0000`; net matched-claim delta `+0`, recall delta `+0.0000`.
15. Top Extraction bottlenecks are listed below.

## NEXT_REMEDIATION_PRIORITY

Top 1: USER_BELIEF perspective preservation (gap=1.0000)
Top 2: Claim atomization and proposition boundaries (gap=0.1250)
Top 3: Empty positive extractions (gap=0.0588)

This report measures the current baseline only. It does not approve or implement Extractor remediation.

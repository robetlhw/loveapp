# Memory Validation Boundary Migration Report

Generated: `2026-09-02T17:25:11.440110+08:00`  
Dataset: `evals\memory\normalization_boundary_v1.jsonl`  
Dataset SHA256: `e3e043e02d9069ce1affbe28e4b913a2a5f631bc138fa32ba8f15a183aed78cc`  
Generic Validator and Canonical Validator are scored as separate stages.
Store mutation permitted: `False`

## Boundary Metrics

| Metric | Result | Numerator | Denominator | Target |
|---|---:|---:|---:|---:|
| generic_validation_acceptance_rate | 1.0000 | 16 | 16 | >=0.95 |
| false_pre_normalization_rejection_rate | 0.0000 | 0 | 16 | <=0.05 |
| normalizer_recovery_accuracy | 1.0000 | 10 | 10 | >=0.95 |
| validation_boundary_rejection_count | 4 | 4 | 20 | generic-invalid only |

## Layer Results

| Case | Generic | Normalization | Canonical | Retained | Drop stage | Errors |
|---|---|---|---|---|---|---|
| BND-001 | accept | accept | accept | True | - | - |
| BND-002 | accept | accept | accept | True | - | - |
| BND-003 | accept | accept | accept | True | - | - |
| BND-004 | accept | accept | accept | True | - | - |
| BND-005 | accept | accept | accept | True | - | - |
| BND-006 | accept | accept | accept | True | - | - |
| BND-007 | accept | accept | accept | True | - | - |
| BND-008 | accept | accept | accept | True | - | - |
| BND-009 | accept | accept | accept | True | - | - |
| BND-010 | accept | accept | accept | True | - | - |
| BND-011 | accept | accept | accept | True | - | - |
| BND-012 | accept | reject | not_run | False | normalization | - |
| BND-013 | reject | not_run | not_run | False | generic_validation | - |
| BND-014 | reject | not_run | not_run | False | generic_validation | - |
| BND-015 | reject | not_run | not_run | False | generic_validation | - |
| BND-016 | reject | not_run | not_run | False | generic_validation | - |
| BND-017 | accept | reject | not_run | False | normalization | - |
| BND-018 | accept | reject | not_run | False | normalization | - |
| BND-019 | accept | accept | accept | True | - | - |
| BND-020 | accept | reject | not_run | False | normalization | - |

## Error Taxonomy

| Error | Cases |
|---|---:|

## Diagnostic Taxonomy

| Diagnostic | Cases |
|---|---:|
| CANONICAL_CUSTOM_CONFLICT | 1 |
| GENERIC_ENUM_INVALID | 2 |
| GENERIC_SCHEMA_INVALID | 2 |
| INTERACTION_METRIC_INVALID | 1 |
| UNKNOWN_STATE_DIMENSION | 2 |

Boundary status: `PASS`

Raw semantic validity is evaluated independently from canonical validity.

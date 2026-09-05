# Memory Normalization V1.2 Boundary Report

Generated: `2026-09-02T17:25:11.464120+08:00`  
Normalization V1.2 status: `FREEZE_CANDIDATE`

## Before / After

| Metric | Before V1.1 | Boundary V1.2 |
|---|---:|---:|
| Validation Boundary Reject Count | 2 | 4 |
| Generic Validation Acceptance | n/a | 1.0000 |
| False Pre-Normalization Reject Rate | n/a | 0.0000 |
| Canonical Mapping Accuracy | 1.0000 | see V1.1 baseline |
| State Dimension Accuracy | 1.0000 | see V1.1 baseline |
| State Value Accuracy | 1.0000 | see V1.1 baseline |
| Custom Preservation | 1.0000 | see V1.1 baseline |
| Unsafe Canonicalization | 0.0000 | see V1.1 baseline |
| Idempotency | 1.0000 | see V1.1 baseline |

## Boundary Result

- Cases: `20`  
- Passed: `20`  
- Generic acceptance: `1.0`  
- False pre-normalization rejection: `0.0`  
- Normalizer recovery: `1.0`

Production extraction is wired to the Raw/Generic validation boundary before deterministic normalization. This migration does not alter the Extraction Prompt, ontology, Relation, Lifecycle, or Store contracts.

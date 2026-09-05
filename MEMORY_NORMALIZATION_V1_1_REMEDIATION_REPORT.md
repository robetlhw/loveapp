# Memory Normalization V1.1 Remediation Report

Generated: `2026-09-02T17:16:41.076949+08:00`  
Dataset: `evals\memory\normalization_v1_1.jsonl`  
Model calls permitted: `False`  
Store mutation permitted: `False`

## Metrics

| Metric | Result | Numerator | Denominator | Pass |
|---|---:|---:|---:|---|
| semantic_hint_resolution_accuracy | 1.0000 | 46 | 46 | True |
| canonical_mapping_accuracy | 1.0000 | 13 | 13 | True |
| state_dimension_accuracy | 1.0000 | 13 | 13 | True |
| state_value_accuracy | 1.0000 | 13 | 13 | True |
| custom_preservation_accuracy | 1.0000 | 21 | 21 | True |
| unsafe_canonicalization_rate | 0.0000 | 0 | 21 | True |
| schema_validity | 1.0000 | 56 | 56 | True |
| idempotency_accuracy | 1.0000 | 5 | 5 | True |
| conflict_outcome_accuracy | 1.0000 | 5 | 5 | True |
| representation_normalization_accuracy | 1.0000 | 10 | 10 | observe |

## Contract Decisions

- Architecture: Option C (raw claim + hints -> generic validation -> deterministic normalization -> canonical validation).
- Semantic mapping authority: deterministic Normalizer; extractor fields are non-authoritative hints.
- State namespace: lifecycle dimensions at top level and in payload; dotted canonical names remain predicate identifiers.
- NORM-052: unrelated canonical/custom declarations fail closed.
- NORM-053: equivalent duplicate declarations reconcile deterministically.

## Failed Cases

| Case | Layer | Errors |
|---|---|---|
| none | - | - |

Normalization V1.1 = `FREEZE_CANDIDATE`

Unknown semantics continue to fall back to Custom; no ontology was added.

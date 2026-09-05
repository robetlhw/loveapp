# Memory Normalization V1 Evaluation Report

Generated: `2026-09-02T11:52:45.180628+08:00`  
Dataset: `evals\memory\normalization_v1.jsonl`  
Dataset SHA256: `b426cc8192b69be3ccf7308afa3d703bac0ff01c5403610f9336d61a3534e4f4`  
Extraction participates in scoring: `False`  
Store mutation permitted: `False`

## Metrics

| Metric | Result | Numerator | Denominator | Target | Pass |
|---|---:|---:|---:|---:|---|
| canonical_mapping_accuracy | 0.0769 | 1 | 13 | >=0.90 | False |
| state_dimension_accuracy | 0.0000 | 0 | 13 | >=0.90 | False |
| state_value_accuracy | 0.0000 | 0 | 13 | >=0.90 | False |
| custom_preservation_accuracy | 1.0000 | 21 | 21 | >=0.95 | True |
| unsafe_canonicalization_rate | 0.0000 | 0 | 21 | <=0.05 | True |
| schema_validity | 0.7857 | 44 | 56 | 1.00 | False |
| idempotency_accuracy | 1.0000 | 5 | 5 | >=0.98 | True |
| canonical_coverage | 0.1346 | 7 | 52 | observe | observe |

## Slice Metrics

| Slice | Cases | Canonical | State Dimension | State Value | Custom | Unsafe Rate | Schema | Idempotency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| canonical_direct | 10 | 0.0000 | n/a | n/a | 1.0000 | 0.0000 | 1.0000 | n/a |
| state_mapping | 12 | n/a | 0.0000 | 0.0000 | n/a | n/a | 0.0833 | n/a |
| alias_variation | 8 | 0.0000 | 0.0000 | 0.0000 | n/a | n/a | 0.8750 | n/a |
| custom_preservation | 8 | n/a | n/a | n/a | 1.0000 | 0.0000 | 1.0000 | n/a |
| ambiguous | 8 | n/a | n/a | n/a | 1.0000 | 0.0000 | 1.0000 | n/a |
| idempotency | 5 | n/a | n/a | n/a | n/a | n/a | 1.0000 | 1.0000 |
| conflict_shape | 5 | 1.0000 | n/a | n/a | n/a | n/a | 1.0000 | n/a |

## Error Taxonomy

| Error | Cases |
|---|---:|
| AMBIGUOUS_GOLD_POLICY | 0 |
| CANONICAL_CUSTOM_CONFLICT | 0 |
| CUSTOM_NOT_PRESERVED | 0 |
| IMPLEMENTATION_SPEC_CONFLICT | 23 |
| MISSED_CANONICAL_MAPPING | 11 |
| NON_IDEMPOTENT | 0 |
| SCHEMA_INVALID | 12 |
| STATE_VALUE_INVALID | 0 |
| UNKNOWN_STATE_DIMENSION | 12 |
| UNSAFE_CANONICALIZATION | 0 |
| WRONG_CANONICAL_MAPPING | 1 |
| WRONG_NORMALIZATION_MODE | 23 |
| WRONG_STATE_DIMENSION | 13 |
| WRONG_STATE_VALUE | 13 |

## Contract Diagnostics

| Diagnostic | Cases |
|---|---:|
| CANONICAL_CUSTOM_CONFLICT | 2 |
| REPAIR_CHANGED_RAW_CLAIM | 34 |
| REPAIR_REJECTED_RAW_CLAIM | 26 |
| STATE_SHAPE_REJECTED | 14 |
| STATE_VALUE_INVALID | 2 |
| UNKNOWN_STATE_DIMENSION | 13 |
| VALIDATION_BOUNDARY_REJECTED | 27 |

## Failed Cases

| Case | Slice | Actual Mode | Errors |
|---|---|---|---|
| NORM-001 | canonical_direct | custom | WRONG_NORMALIZATION_MODE, MISSED_CANONICAL_MAPPING, IMPLEMENTATION_SPEC_CONFLICT |
| NORM-002 | canonical_direct | custom | WRONG_NORMALIZATION_MODE, MISSED_CANONICAL_MAPPING, IMPLEMENTATION_SPEC_CONFLICT |
| NORM-003 | canonical_direct | custom | WRONG_NORMALIZATION_MODE, MISSED_CANONICAL_MAPPING, IMPLEMENTATION_SPEC_CONFLICT |
| NORM-004 | canonical_direct | custom | WRONG_NORMALIZATION_MODE, MISSED_CANONICAL_MAPPING, IMPLEMENTATION_SPEC_CONFLICT |
| NORM-006 | canonical_direct | canonical | WRONG_CANONICAL_MAPPING |
| NORM-011 | state_mapping | custom | WRONG_NORMALIZATION_MODE, WRONG_STATE_DIMENSION, WRONG_STATE_VALUE, SCHEMA_INVALID, UNKNOWN_STATE_DIMENSION, IMPLEMENTATION_SPEC_CONFLICT |
| NORM-012 | state_mapping | custom | WRONG_NORMALIZATION_MODE, WRONG_STATE_DIMENSION, WRONG_STATE_VALUE, SCHEMA_INVALID, UNKNOWN_STATE_DIMENSION, IMPLEMENTATION_SPEC_CONFLICT |
| NORM-013 | state_mapping | custom | WRONG_NORMALIZATION_MODE, WRONG_STATE_DIMENSION, WRONG_STATE_VALUE, SCHEMA_INVALID, UNKNOWN_STATE_DIMENSION, IMPLEMENTATION_SPEC_CONFLICT |
| NORM-014 | state_mapping | state | WRONG_STATE_DIMENSION, WRONG_STATE_VALUE, IMPLEMENTATION_SPEC_CONFLICT |
| NORM-015 | state_mapping | custom | WRONG_NORMALIZATION_MODE, WRONG_STATE_DIMENSION, WRONG_STATE_VALUE, SCHEMA_INVALID, UNKNOWN_STATE_DIMENSION, IMPLEMENTATION_SPEC_CONFLICT |
| NORM-016 | state_mapping | custom | WRONG_NORMALIZATION_MODE, WRONG_STATE_DIMENSION, WRONG_STATE_VALUE, SCHEMA_INVALID, UNKNOWN_STATE_DIMENSION, IMPLEMENTATION_SPEC_CONFLICT |
| NORM-017 | state_mapping | custom | WRONG_NORMALIZATION_MODE, WRONG_STATE_DIMENSION, WRONG_STATE_VALUE, SCHEMA_INVALID, UNKNOWN_STATE_DIMENSION, IMPLEMENTATION_SPEC_CONFLICT |
| NORM-018 | state_mapping | custom | WRONG_NORMALIZATION_MODE, WRONG_STATE_DIMENSION, WRONG_STATE_VALUE, SCHEMA_INVALID, UNKNOWN_STATE_DIMENSION, IMPLEMENTATION_SPEC_CONFLICT |
| NORM-019 | state_mapping | custom | WRONG_NORMALIZATION_MODE, WRONG_STATE_DIMENSION, WRONG_STATE_VALUE, SCHEMA_INVALID, UNKNOWN_STATE_DIMENSION, IMPLEMENTATION_SPEC_CONFLICT |
| NORM-020 | state_mapping | custom | WRONG_NORMALIZATION_MODE, WRONG_STATE_DIMENSION, WRONG_STATE_VALUE, SCHEMA_INVALID, UNKNOWN_STATE_DIMENSION, IMPLEMENTATION_SPEC_CONFLICT |
| NORM-021 | state_mapping | custom | WRONG_NORMALIZATION_MODE, WRONG_STATE_DIMENSION, WRONG_STATE_VALUE, SCHEMA_INVALID, UNKNOWN_STATE_DIMENSION, IMPLEMENTATION_SPEC_CONFLICT |
| NORM-022 | state_mapping | custom | WRONG_NORMALIZATION_MODE, WRONG_STATE_DIMENSION, WRONG_STATE_VALUE, SCHEMA_INVALID, UNKNOWN_STATE_DIMENSION, IMPLEMENTATION_SPEC_CONFLICT |
| NORM-023 | alias_variation | custom | WRONG_NORMALIZATION_MODE, MISSED_CANONICAL_MAPPING, IMPLEMENTATION_SPEC_CONFLICT |
| NORM-024 | alias_variation | custom | WRONG_NORMALIZATION_MODE, MISSED_CANONICAL_MAPPING, IMPLEMENTATION_SPEC_CONFLICT |
| NORM-025 | alias_variation | custom | WRONG_NORMALIZATION_MODE, MISSED_CANONICAL_MAPPING, IMPLEMENTATION_SPEC_CONFLICT |
| NORM-026 | alias_variation | custom | WRONG_NORMALIZATION_MODE, MISSED_CANONICAL_MAPPING, IMPLEMENTATION_SPEC_CONFLICT |
| NORM-027 | alias_variation | custom | WRONG_NORMALIZATION_MODE, MISSED_CANONICAL_MAPPING, IMPLEMENTATION_SPEC_CONFLICT |
| NORM-028 | alias_variation | custom | WRONG_NORMALIZATION_MODE, MISSED_CANONICAL_MAPPING, IMPLEMENTATION_SPEC_CONFLICT |
| NORM-029 | alias_variation | custom | WRONG_NORMALIZATION_MODE, MISSED_CANONICAL_MAPPING |
| NORM-030 | alias_variation | custom | WRONG_NORMALIZATION_MODE, WRONG_STATE_DIMENSION, WRONG_STATE_VALUE, SCHEMA_INVALID, UNKNOWN_STATE_DIMENSION, IMPLEMENTATION_SPEC_CONFLICT |

## Top Bottlenecks

1. state_dimension_accuracy (target gap=0.9000)
2. state_value_accuracy (target gap=0.9000)
3. canonical_mapping_accuracy (target gap=0.8231)

## Required Answers

1. Current Canonical Registry (23): `contact.status`, `relationship.stage`, `relationship.repair_status`, `confession.status`, `plan.status`, `relationship.familiarity`, `relationship.contact_opportunity`, `relationship.conflict_status`, `relationship.interaction_reciprocity`, `partner.relationship_status`, `relationship.romantic_interest`, `interaction.contact_frequency`, `interaction.topic_scope`, `interaction.channel`, `interaction.initiation_balance`, `interaction.response_engagement`, `interaction.emotional_disclosure`, `preference.general`, `preference.food.cuisine`, `preference.food.spiciness`, `preference.environment.noise`, `preference.activity.type`, `preference.budget.range`.
2. Current lifecycle state dimensions and values: `relationship_familiarity`=[high, low, moderate, unfamiliar]; `contact_opportunity`=[high, low, moderate]; `contact_availability`=[available, limited, unavailable]; `conflict_status`=[active, cooling, repairing, resolved]; `interaction_reciprocity`=[high, low, mixed]; `partner_relationship_status`=[married, partnered, single, unknown].
3. `CONTRACT_VERIFY` resolutions: NORM-005=custom_fallback; NORM-006=repo_identifier; NORM-007=custom_fallback; NORM-008=custom_fallback; NORM-009=custom_fallback; NORM-010=custom_fallback; NORM-029=repo_identifier; NORM-052=reject; NORM-053=repo_identifier. Full reasons are fixed in `MEMORY_NORMALIZATION_CONTRACT_RESOLUTION.md`.
4. Canonical Mapping Accuracy: `0.0769`.
5. Most error-prone canonical targets: interaction.initiation_balance (5), interaction.response_engagement (3), preference.food.cuisine (3), preference.budget.range (1). Failed case IDs: `NORM-001, NORM-002, NORM-003, NORM-004, NORM-023, NORM-024, NORM-025, NORM-026, NORM-027, NORM-028, NORM-029, NORM-006`.
6. State Dimension Accuracy: `0.0000`.
7. State Value Accuracy: `0.0000`.
8. Custom Preservation Accuracy: `1.0000`.
9. Unsafe Canonicalization Rate: `0.0000`; ambiguous cases forced canonical: `[]`.
10. Ambiguous cases incorrectly forced canonical: `[]`.
11. Alias Variation misses: `['NORM-023', 'NORM-024', 'NORM-025', 'NORM-026', 'NORM-027', 'NORM-028', 'NORM-029']`.
12. Idempotency Accuracy: `1.0000`.
13. Normalized outputs retaining canonical and custom simultaneously: `[]`. Incorrectly accepted raw conflicts: `[]`; safely reconciled equivalent declarations: `['NORM-053']`.
14. Invalid state dimension/value inputs accepted: `[]`; NORM-054 through NORM-056 were rejected by ingress.
15. `IMPLEMENTATION_SPEC_CONFLICT` count: `23`.
16. Top three bottlenecks: state_dimension_accuracy (target gap=0.9000); state_value_accuracy (target gap=0.9000); canonical_mapping_accuracy (target gap=0.8231).
17. Next remediation should be limited to two points: bounded canonical/alias coverage, and one authoritative lifecycle-state representation plus ingress conflict handling. No ontology expansion is recommended from this baseline alone.

NEXT_PHASE = `Normalization V1 Failure Review + Minimal Remediation`

This is an observational baseline. It does not modify the production normalizer.

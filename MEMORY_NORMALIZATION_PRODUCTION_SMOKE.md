# Memory Normalization Production-Path Smoke

Generated: `2026-09-02T19:48:39.359113+08:00`  
Dataset: `evals\memory\normalization_production_smoke_v1.jsonl`  
Model calls permitted: `False` (deterministic in-process OpenAI-compatible client)  
Store mutation permitted: `False` (isolated InMemoryMemoryStore only)

## Metrics

| Metric | Result |
|---|---:|
| raw_claim_present_count | 6 |
| generic_validation_acceptance_rate | 1.0 |
| normalization_success_rate | 1.0 |
| canonical_validation_acceptance_rate | 1.0 |
| admission_reached_rate | 1.0 |
| store_write_attempt_rate | 1.0 |
| passed_case_count | 6 |

## Case Results

| Case | Raw | Generic | Normalizer | Canonical | Admission | Store | Drop | Result |
|---|---|---|---|---|---|---|---|---|
| SMOKE-SUBJ-003 | True | accept | custom | accept | True | True | - | True |
| SMOKE-SUBJ-013 | True | accept | custom | accept | True | True | - | True |
| SMOKE-SUBJ-021 | True | accept | custom, custom | accept, accept | True | True | - | True |
| SMOKE-SUBJ-022 | True | accept | custom | accept | True | True | - | True |
| SMOKE-INITIATION-BALANCE | True | accept | canonical | accept | True | True | - | True |
| SMOKE-CONFLICT-ACTIVE | True | accept | canonical | accept | True | True | - | True |

## Pressure Case Details

### SMOKE-SUBJ-003 (SUBJ-003)

- Text: `我最近越来越不想继续这段关系。`
- Admission reached: `True`
- Store write attempted: `True`
- Final retention: `[{"id": "74d67834-3e32-4c3f-bb3a-e05c9a1f1b13", "status": "proposed", "predicate_type": "custom", "canonical_predicate": null, "custom_predicate": "desire_to_continue", "state_dimension": null, "state_value": null, "admission_decision": "strong_review"}]`
- Drop: `none` / `none`

### SMOKE-SUBJ-013 (SUBJ-013)

- Text: `我总觉得这段关系没有以前稳定了。`
- Admission reached: `True`
- Store write attempted: `True`
- Final retention: `[{"id": "c5e0e7cb-562a-47bd-a92f-2134b0fefaa4", "status": "proposed", "predicate_type": "custom", "canonical_predicate": null, "custom_predicate": "relationship_stability", "state_dimension": null, "state_value": null, "admission_decision": "propose"}]`
- Drop: `none` / `none`

### SMOKE-SUBJ-021 (SUBJ-021)

- Text: `昨晚我们把边界和之后的联系频率谈妥了。`
- Admission reached: `True`
- Store write attempted: `True`
- Final retention: `[{"id": "c5b7ea2c-829b-4081-ac1b-65fe2e4428d5", "status": "proposed", "predicate_type": "custom", "canonical_predicate": null, "custom_predicate": "boundary_agreed", "state_dimension": null, "state_value": null, "admission_decision": "strong_review"}, {"id": "30ea9de4-d25c-4a6b-8809-cf428c5f6cad", "status": "proposed", "predicate_type": "custom", "canonical_predicate": null, "custom_predicate": "contact_frequency_agreed", "state_dimension": null, "state_value": null, "admission_decision": "strong_review"}]`
- Drop: `none` / `none`

### SMOKE-SUBJ-022 (SUBJ-022)

- Text: `我们已经暂停联系两周了。`
- Admission reached: `True`
- Store write attempted: `True`
- Final retention: `[{"id": "93853aef-0c29-4985-888d-734bda045bdb", "status": "proposed", "predicate_type": "custom", "canonical_predicate": null, "custom_predicate": "contact.status", "state_dimension": null, "state_value": null, "admission_decision": "strong_review"}]`
- Drop: `none` / `none`

### SMOKE-INITIATION-BALANCE (EXTRA-INITIATION)

- Text: `她最近基本都不主动找我了`
- Admission reached: `True`
- Store write attempted: `True`
- Final retention: `[{"id": "af4678f7-68f9-41de-8302-c23b100630d2", "status": "proposed", "predicate_type": "canonical", "canonical_predicate": "interaction.initiation_balance", "custom_predicate": null, "state_dimension": "interaction.initiation_balance", "state_value": null, "admission_decision": "propose"}]`
- Drop: `none` / `none`

### SMOKE-CONFLICT-ACTIVE (EXTRA-CONFLICT)

- Text: `我们现在还在冷战`
- Admission reached: `True`
- Store write attempted: `True`
- Final retention: `[{"id": "694a3caa-989f-4b0b-b34f-c014b2670df1", "status": "proposed", "predicate_type": "canonical", "canonical_predicate": "relationship.conflict_status", "custom_predicate": null, "state_dimension": "conflict_status", "state_value": "active", "admission_decision": "strong_review"}]`
- Drop: `none` / `none`

Smoke status: `PASS`

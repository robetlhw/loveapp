# LoveApp Memory Ontology Sanity Test v0.1

Generated: `2026-09-10T14:45:18.759453+08:00`
Dataset: `D:\Users\90994\chrome\LoveApp_Memory_Ontology_Sanity_Gold_v0_1.jsonl`
Dataset SHA256: `f294b4d52ef162bdd6864e053e4d78cccf5e4542348ef269d7e4273b90160555`
Policy: `memory_ontology_v3_policy_v1`
Gold review status: `draft`
Flash model: `deepseek-v4-flash`
Strong model: `deepseek-v4-pro`
Store mutation permitted: `False`

> This is a diagnostic against draft Gold, not a production acceptance benchmark.

## Overall metrics

| Metric | Result |
|---|---:|
| Extraction Pass Rate | 0.6750 |
| Normalized Pass Rate | 0.7000 |
| Kind Accuracy | 0.8636 (38/44) |
| Subject Accuracy | 0.8913 (41/46) |
| Perspective Accuracy | 0.9130 (42/46) |
| Canonical/Custom Accuracy | 0.8182 (27/33) |
| Event Type Accuracy | 0.6667 (6/9) |
| Pattern Metric Accuracy | 0.6667 (6/9) |
| Multi-claim Completeness | 0.0000 |
| Forbidden Claim Violation Rate | 0.1250 |

## Results by group

| Group | Cases | Extraction | Normalized | Kind | Subject | Perspective | Canonical/Custom | Event Type | Pattern Metric | Multi-claim | Forbidden |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| stable_fact | 8 | 1.0000 | 1.0000 | 1.0000 (8/8) | 1.0000 (8/8) | 1.0000 (8/8) | 1.0000 (8/8) | n/a (0/0) | n/a (0/0) | n/a | 0.0000 |
| preference | 8 | 0.7500 | 0.7500 | 1.0000 (8/8) | 1.0000 (8/8) | 1.0000 (8/8) | 0.7500 (6/8) | n/a (0/0) | n/a (0/0) | n/a | 0.2500 |
| interaction_event | 8 | 0.7500 | 0.7500 | 1.0000 (8/8) | 0.8750 (7/8) | 1.0000 (8/8) | 1.0000 (1/1) | 0.8571 (6/7) | n/a (0/0) | n/a | 0.0000 |
| interaction_pattern | 8 | 0.7500 | 0.7500 | 0.7500 (6/8) | 0.8750 (7/8) | 0.8750 (7/8) | 0.8750 (7/8) | n/a (0/0) | 0.7143 (5/7) | n/a | 0.0000 |
| boundary | 8 | 0.1250 | 0.2500 | 0.6667 (8/12) | 0.7857 (11/14) | 0.7857 (11/14) | 0.6250 (5/8) | 0.0000 (0/2) | 0.5000 (1/2) | 0.0000 | 0.3750 |

## Case diagnostics

| case_id | group | Extraction | Normalization | first failure | short reason |
|---|---|---|---|---|---|
| sf_001 | stable_fact | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| sf_002 | stable_fact | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| sf_003 | stable_fact | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| sf_004 | stable_fact | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| sf_005 | stable_fact | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| sf_006 | stable_fact | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| sf_007 | stable_fact | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| sf_008 | stable_fact | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| pref_001 | preference | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| pref_002 | preference | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| pref_003 | preference | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| pref_004 | preference | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| pref_005 | preference | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| pref_006 | preference | FAIL | FAIL | FORBIDDEN_CLAIM_EMITTED | extraction:unmatched_expected=[0] fields=canonical_predicate forbidden_hits=1; normalization:unmatched_expected=[0] fields=canonical_predicate,predicate_type |
| pref_007 | preference | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| pref_008 | preference | FAIL | FAIL | FORBIDDEN_CLAIM_EMITTED | extraction:unmatched_expected=[0] fields=canonical_predicate forbidden_hits=1; normalization:unmatched_expected=[0] fields=canonical_predicate forbidden_hits=1 |
| event_001 | interaction_event | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| event_002 | interaction_event | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| event_003 | interaction_event | FAIL | FAIL | EXTRACTION_SEMANTIC_LOSS | extraction:unmatched_expected=[0] fields=milestone_type; normalization:unmatched_expected=[0] fields=milestone_type |
| event_004 | interaction_event | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| event_005 | interaction_event | FAIL | FAIL | EXTRACTION_SUBJECT_ERROR | extraction:unmatched_expected=[0] fields=subject; normalization:unmatched_expected=[0] fields=subject |
| event_006 | interaction_event | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| event_007 | interaction_event | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| event_008 | interaction_event | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| pattern_001 | interaction_pattern | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| pattern_002 | interaction_pattern | FAIL | FAIL | EXTRACTION_ATOMICITY_ERROR | extraction:unmatched_expected=[0] fields=canonical_predicate,memory_kind,metric,perspective,predicate_type,source,subject; normalization:unmatched_expected=[0] fields=canonical_predicate,memory_kind,metric,perspective,predicate_type,source,subject |
| pattern_003 | interaction_pattern | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| pattern_004 | interaction_pattern | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| pattern_005 | interaction_pattern | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| pattern_006 | interaction_pattern | FAIL | FAIL | EXTRACTION_KIND_ERROR | extraction:unmatched_expected=[0] fields=memory_kind; normalization:unmatched_expected=[0] fields=memory_kind,metric,source |
| pattern_007 | interaction_pattern | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| pattern_008 | interaction_pattern | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| bd_001 | boundary | FAIL | FAIL | FORBIDDEN_CLAIM_EMITTED | extraction:unmatched_expected=[0] fields=predicate_type forbidden_hits=1; normalization:unmatched_expected=[0] fields=predicate_type forbidden_hits=1 |
| bd_002 | boundary | FAIL | FAIL | MULTI_CLAIM_MISSING | extraction:unmatched_expected=[0] fields=event_type,memory_kind,subject; normalization:unmatched_expected=[0] fields=event_type,memory_kind,subject |
| bd_003 | boundary | PASS | PASS | - | Extraction and normalization satisfy all partial constraints. |
| bd_004 | boundary | FAIL | FAIL | MULTI_CLAIM_MISSING | extraction:unmatched_expected=[1] fields=memory_kind,subject; normalization:unmatched_expected=[1] fields=memory_kind,subject |
| bd_005 | boundary | FAIL | PASS | EXTRACTION_SEMANTIC_LOSS | extraction:unmatched_expected=[0] fields=canonical_predicate |
| bd_006 | boundary | FAIL | FAIL | FORBIDDEN_CLAIM_EMITTED | extraction:unmatched_expected=[0, 1] fields=canonical_predicate,memory_kind,value forbidden_hits=1; normalization:unmatched_expected=[0, 1] fields=canonical_predicate,memory_kind,value forbidden_hits=1 |
| bd_007 | boundary | FAIL | FAIL | EXTRACTION_SEMANTIC_LOSS | extraction:unmatched_expected=[0] fields=milestone_type; normalization:unmatched_expected=[0] fields=milestone_type |
| bd_008 | boundary | FAIL | FAIL | EXTRACTION_ATOMICITY_ERROR | extraction:unmatched_expected=[1] fields=canonical_predicate,memory_kind,metric,perspective,subject forbidden_hits=1; normalization:unmatched_expected=[1] fields=canonical_predicate,memory_kind,metric,perspective,subject forbidden_hits=1 |

## Diagnostic conclusion

- Extraction-drift candidates: `8`
- Normalization-only drift candidates: `0`
- Technical extractor failures: `0`
- Draft-policy review candidates: `5`
- Result interpretation: failures are diagnostic findings against draft Gold; they are not automatic authorization to change production semantics.

## Error taxonomy

| First failing stage | Cases |
|---|---:|
| EXTRACTION_ATOMICITY_ERROR | 2 |
| EXTRACTION_KIND_ERROR | 1 |
| EXTRACTION_SEMANTIC_LOSS | 3 |
| EXTRACTION_SUBJECT_ERROR | 1 |
| FORBIDDEN_CLAIM_EMITTED | 4 |
| MULTI_CLAIM_MISSING | 2 |

## High-risk cases

| Case | Extraction | Normalized | First failure |
|---|---|---|---|
| bd_001 | FAIL | FAIL | FORBIDDEN_CLAIM_EMITTED |
| bd_002 | FAIL | FAIL | MULTI_CLAIM_MISSING |
| bd_006 | FAIL | FAIL | FORBIDDEN_CLAIM_EMITTED |
| bd_007 | FAIL | FAIL | EXTRACTION_SEMANTIC_LOSS |
| bd_008 | FAIL | FAIL | EXTRACTION_ATOMICITY_ERROR |
| event_003 | FAIL | FAIL | EXTRACTION_SEMANTIC_LOSS |
| pattern_002 | FAIL | FAIL | EXTRACTION_ATOMICITY_ERROR |
| pattern_004 | PASS | PASS | - |
| pattern_006 | FAIL | FAIL | EXTRACTION_KIND_ERROR |
| pref_006 | FAIL | FAIL | FORBIDDEN_CLAIM_EMITTED |
| sf_004 | PASS | PASS | - |

## Policy-suspect cases

| Case | Why Gold needs human review |
|---|---|
| bd_001 | The current schema has no belief MemoryKind, so a Custom proposition stored with perspective=user_belief and objective_fact=false can be defensible even though the draft Gold forbids stable_fact. Review the storage-kind boundary, not the model output alone. |
| bd_004 | The model emitted the bounded exception as a separate relationship Event, while the draft Gold assigns that non-action to partner. Subject ownership for an omitted contact is semantically debatable. |
| bd_007 | Both the first shared-trip Event and recurring travel Pattern were extracted. The first occurrence survives in predicate, summary, and novelty, but draft Gold additionally requires a milestone_type field that the current production contract does not define. |
| event_003 | The underlying date Event and first-occurrence meaning are preserved in event_type, predicate, summary, and novelty. Draft Gold additionally requires milestone_type, which is not currently a production extraction field. |
| event_005 | The draft Gold treats a partner-provided support event as relationship-subject, while the current subject hard contrast selects the single acting partner. Both readings are semantically defensible. |

## Model telemetry

- Calls: `41`
- Flash calls: `40`
- Strong calls: `1`
- Failures: `0`
- Prompt tokens: `274065`
- Completion tokens: `12851`
- P50 latency: `1697.75 ms`
- P95 latency: `2529.6 ms`

No production ontology, Prompt, Normalizer, Validator, Relation, Lifecycle, or Store behavior was changed by this diagnostic run.

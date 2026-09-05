# Memory Long-tail Write V1 Baseline Report

- Dataset: `evals\memory\longtail_write_v1.jsonl`
- Dataset SHA-256: `4e2394c36db8a7cf404ec20e53136ef72c759a97b4c74076ca6054d9e36d0a31`
- Cases evaluated: `112`
- Strict cases: `96`
- Strict passed: `8`
- Status: **BASELINE_DRIFT_REQUIRES_REVIEW**
- Production Store mutation permitted: `False`
- Model calls permitted: `False`

## Authority

- Relation: `loveapp.application.memory_relations.resolve_claim_relation`
- Write: `loveapp.domain.memory_write.MemoryWriteBatch -> loveapp.adapters.memory.in_memory.InMemoryMemoryStore.commit_memory_batch`

## Strict Metrics

| Metric | Value |
|---|---:|
| `case_count` | 96 |
| `passed_case_count` | 8 |
| `failed_case_count` | 88 |
| `relation_accuracy` | 0.1771 |
| `target_exact_match_accuracy` | 0.6562 |
| `target_set_accuracy` | 0.6562 |
| `target_micro_precision` | 0.7195 |
| `target_micro_recall` | 0.7468 |
| `target_micro_f1` | 0.7329 |
| `store_action_accuracy` | 0.8750 |
| `new_row_decision_accuracy` | 1.0000 |
| `final_status_accuracy` | 1.0000 |
| `supersede_exact_match_accuracy` | 0.8750 |
| `preserve_exact_match_accuracy` | 1.0000 |

## Relation Precision / Recall / F1

| Relation | Support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| `same` | 8 | 1.0000 | 1.0000 | 1.0000 |
| `complementary` | 34 | 0.0000 | 0.0000 | 0.0000 |
| `update` | 12 | 0.0000 | 0.0000 | 0.0000 |
| `contradiction` | 17 | 0.0000 | 0.0000 | 0.0000 |
| `unrelated` | 16 | 0.0000 | 0.0000 | 0.0000 |
| `uncertain` | 9 | 0.1023 | 1.0000 | 0.1856 |

## Safety / Governance

| Metric | Value |
|---|---:|
| `false_supersede_count` | 0 |
| `false_merge_count` | 0 |
| `false_link_count` | 14 |
| `cross_subject_false_link_count` | 0 |
| `event_false_dedupe_count` | 0 |
| `event_false_supersede_count` | 0 |
| `event_to_pattern_false_update_count` | 0 |
| `custom_to_canonical_false_supersede_count` | 0 |
| `proposed_overwrites_confirmed_count` | 0 |
| `uncertain_destructive_update_count` | 0 |
| `non_target_supersede_count` | 0 |
| `false_supersede_rate` | 0.0000 |
| `false_merge_rate` | 0.0000 |
| `false_link_rate` | 0.1458 |
| `cross_subject_false_link_rate` | 0.0000 |
| `event_false_dedupe_rate` | 0.0000 |
| `event_false_supersede_rate` | 0.0000 |
| `event_to_pattern_false_update_rate` | 0.0000 |
| `custom_to_canonical_false_supersede_rate` | 0.0000 |
| `proposed_overwrites_confirmed_violation_rate` | 0.0000 |
| `uncertain_destructive_update_rate` | 0.0000 |
| `non_target_supersede_rate` | 0.0000 |
| `historical_event_preservation_rate` | 1.0000 |

## Error Taxonomy

| Category | Count |
|---|---:|
| `CUSTOM_CANONICAL_COLLISION` | 8 |
| `EVENT_IDENTITY_ERROR` | 8 |
| `EVENT_PATTERN_CONFUSION` | 8 |
| `SEMANTIC_RELATION_ERROR` | 55 |
| `TARGET_SELECTION_ERROR` | 9 |

## Failed Strict Cases

| Case | Slice | Expected | Actual | Action | Category |
|---|---|---|---|---|---|
| LTW-009 | complementary_detail | complementary | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-010 | complementary_detail | complementary | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-011 | complementary_detail | complementary | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-012 | complementary_detail | complementary | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-013 | complementary_detail | complementary | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-014 | complementary_detail | complementary | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-015 | complementary_detail | complementary | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-016 | complementary_detail | complementary | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-017 | sustained_update | update | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-018 | sustained_update | update | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-019 | sustained_update | update | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-020 | sustained_update | update | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-021 | sustained_update | update | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-022 | sustained_update | update | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-023 | sustained_update | update | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-024 | sustained_update | update | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-025 | contradiction_authority_guard | contradiction | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-026 | contradiction_authority_guard | contradiction | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-027 | contradiction_authority_guard | contradiction | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-028 | contradiction_authority_guard | contradiction | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-029 | contradiction_authority_guard | contradiction | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-030 | contradiction_authority_guard | contradiction | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-031 | contradiction_authority_guard | contradiction | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-032 | contradiction_authority_guard | contradiction | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-033 | unrelated_same_subject_kind | unrelated | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-034 | unrelated_same_subject_kind | unrelated | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-035 | unrelated_same_subject_kind | unrelated | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-036 | unrelated_same_subject_kind | unrelated | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-037 | unrelated_same_subject_kind | unrelated | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-038 | unrelated_same_subject_kind | unrelated | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-039 | unrelated_same_subject_kind | unrelated | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-040 | unrelated_same_subject_kind | unrelated | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-041 | cross_subject | unrelated | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-042 | cross_subject | unrelated | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-043 | cross_subject | unrelated | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-044 | cross_subject | unrelated | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-045 | cross_subject | unrelated | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-046 | cross_subject | unrelated | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-047 | cross_subject | unrelated | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-048 | cross_subject | unrelated | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-049 | temporal_event_identity | complementary | uncertain | add_without_supersede | EVENT_IDENTITY_ERROR |
| LTW-050 | temporal_event_identity | complementary | uncertain | add_without_supersede | EVENT_IDENTITY_ERROR |
| LTW-051 | temporal_event_identity | complementary | uncertain | add_without_supersede | EVENT_IDENTITY_ERROR |
| LTW-052 | temporal_event_identity | complementary | uncertain | add_without_supersede | EVENT_IDENTITY_ERROR |
| LTW-053 | temporal_event_identity | complementary | uncertain | add_without_supersede | EVENT_IDENTITY_ERROR |
| LTW-054 | temporal_event_identity | complementary | uncertain | add_without_supersede | EVENT_IDENTITY_ERROR |
| LTW-055 | temporal_event_identity | complementary | uncertain | add_without_supersede | EVENT_IDENTITY_ERROR |
| LTW-056 | temporal_event_identity | complementary | uncertain | add_without_supersede | EVENT_IDENTITY_ERROR |
| LTW-057 | event_vs_pattern | complementary | uncertain | add_without_supersede | EVENT_PATTERN_CONFUSION |
| LTW-058 | event_vs_pattern | complementary | uncertain | add_without_supersede | EVENT_PATTERN_CONFUSION |
| LTW-059 | event_vs_pattern | complementary | uncertain | add_without_supersede | EVENT_PATTERN_CONFUSION |
| LTW-060 | event_vs_pattern | complementary | uncertain | add_without_supersede | EVENT_PATTERN_CONFUSION |
| LTW-061 | event_vs_pattern | complementary | uncertain | add_without_supersede | EVENT_PATTERN_CONFUSION |
| LTW-062 | event_vs_pattern | complementary | uncertain | add_without_supersede | EVENT_PATTERN_CONFUSION |
| LTW-063 | event_vs_pattern | complementary | uncertain | add_without_supersede | EVENT_PATTERN_CONFUSION |
| LTW-064 | event_vs_pattern | complementary | uncertain | add_without_supersede | EVENT_PATTERN_CONFUSION |
| LTW-065 | custom_canonical_coexistence | complementary | uncertain | add_without_supersede | CUSTOM_CANONICAL_COLLISION |
| LTW-066 | custom_canonical_coexistence | complementary | uncertain | add_without_supersede | CUSTOM_CANONICAL_COLLISION |
| LTW-067 | custom_canonical_coexistence | complementary | uncertain | add_without_supersede | CUSTOM_CANONICAL_COLLISION |
| LTW-068 | custom_canonical_coexistence | complementary | uncertain | add_without_supersede | CUSTOM_CANONICAL_COLLISION |
| LTW-069 | custom_canonical_coexistence | complementary | uncertain | add_without_supersede | CUSTOM_CANONICAL_COLLISION |
| LTW-070 | custom_canonical_coexistence | complementary | uncertain | add_without_supersede | CUSTOM_CANONICAL_COLLISION |
| LTW-071 | custom_canonical_coexistence | complementary | uncertain | add_without_supersede | CUSTOM_CANONICAL_COLLISION |
| LTW-072 | custom_canonical_coexistence | complementary | uncertain | add_without_supersede | CUSTOM_CANONICAL_COLLISION |
| LTW-073 | authority_status_safety | contradiction | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-074 | authority_status_safety | contradiction | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-075 | authority_status_safety | contradiction | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-076 | authority_status_safety | contradiction | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-077 | authority_status_safety | contradiction | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-078 | authority_status_safety | contradiction | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-079 | authority_status_safety | contradiction | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-080 | authority_status_safety | contradiction | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-081 | multi_memory_targeting | update | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-082 | multi_memory_targeting | update | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-083 | multi_memory_targeting | complementary | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-084 | multi_memory_targeting | uncertain | uncertain | add_without_supersede | TARGET_SELECTION_ERROR |
| LTW-085 | multi_memory_targeting | update | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-086 | multi_memory_targeting | contradiction | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-087 | multi_memory_targeting | complementary | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-088 | multi_memory_targeting | update | uncertain | add_without_supersede | SEMANTIC_RELATION_ERROR |
| LTW-089 | safe_uncertain_ambiguity | uncertain | uncertain | add_without_supersede | TARGET_SELECTION_ERROR |
| LTW-090 | safe_uncertain_ambiguity | uncertain | uncertain | add_without_supersede | TARGET_SELECTION_ERROR |
| LTW-091 | safe_uncertain_ambiguity | uncertain | uncertain | add_without_supersede | TARGET_SELECTION_ERROR |
| LTW-092 | safe_uncertain_ambiguity | uncertain | uncertain | add_without_supersede | TARGET_SELECTION_ERROR |
| LTW-093 | safe_uncertain_ambiguity | uncertain | uncertain | add_without_supersede | TARGET_SELECTION_ERROR |
| LTW-094 | safe_uncertain_ambiguity | uncertain | uncertain | add_without_supersede | TARGET_SELECTION_ERROR |
| LTW-095 | safe_uncertain_ambiguity | uncertain | uncertain | add_without_supersede | TARGET_SELECTION_ERROR |
| LTW-096 | safe_uncertain_ambiguity | uncertain | uncertain | add_without_supersede | TARGET_SELECTION_ERROR |

The deterministic baseline uses fixture candidates only. Policy Review cases are observe-only and excluded from strict scoring.

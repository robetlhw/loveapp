# Memory Relation V1 Baseline Report

- Dataset: `evals\memory\relation_v1.jsonl`
- Dataset SHA-256: `af19abf63c351d1da36fa58de92f4b4b69358509d8413690da0fa070455132ff`
- Cases evaluated: `1`
- Strict cases: `1`
- Production Store mutation permitted: `False`
- Status: **BASELINE_PASS_POLICY_REVIEW_PENDING**

## Strict Metrics

| Metric | Result |
|---|---:|
| `case_count` | 1 |
| `passed_case_count` | 1 |
| `failed_case_count` | 0 |
| `relation_accuracy` | 1.0000 |
| `rule_name_accuracy` | 1.0000 |
| `reason_accuracy` | 1.0000 |
| `target_exact_match_accuracy` | 1.0000 |
| `target_set_accuracy` | 1.0000 |
| `target_micro_precision` | 0.0000 |
| `target_micro_recall` | 0.0000 |
| `target_micro_f1` | 0.0000 |

## Relation Precision / Recall

| Relation | Support | Precision | Recall |
|---|---:|---:|---:|
| `same` | 0 | 0.0000 | 0.0000 |
| `complementary` | 0 | 0.0000 | 0.0000 |
| `update` | 0 | 0.0000 | 0.0000 |
| `contradiction` | 0 | 0.0000 | 0.0000 |
| `unrelated` | 1 | 1.0000 | 1.0000 |
| `uncertain` | 0 | 0.0000 | 0.0000 |

## Safety / Governance

| Metric | Result |
|---|---:|
| `same_keeper_accuracy` | 0.0000 |
| `confirmed_state_update_recall` | 0.0000 |
| `proposed_state_contradiction_recall` | 0.0000 |
| `proposed_overwrites_confirmed_violation_rate` | 0.0000 |
| `contact_transition_accuracy` | 0.0000 |
| `preference_polarity_accuracy` | 0.0000 |
| `single_value_preference_accuracy` | 0.0000 |
| `preference_hierarchy_accuracy` | 0.0000 |
| `custom_uncertain_recall` | 0.0000 |
| `custom_target_cap_accuracy` | 0.0000 |
| `cross_subject_false_link_rate` | 0.0000 |
| `unrelated_false_link_rate` | 0.0000 |

## By Slice

| Slice | Cases | Passed | Relation accuracy | Target set accuracy |
|---|---:|---:|---:|---:|
| `plan_distinct` | 1 | 1 | 1.0000 | 1.0000 |

## Error Taxonomy

| Category | Count |
|---|---:|

## Failed Strict Cases

| Case | Expected | Actual | Rule | Category |
|---|---|---|---|---|
| none | - | - | - | - |

## Contract Interpretation

The baseline calls the production relation resolver directly and does not reimplement its rules. Fixture identity is never trusted for `dedupe_key`; the production `memory_dedupe_key()` is used so identity drift remains visible.

POLICY_REVIEW cases are observe-only and excluded from strict scoring. No relation, lifecycle, admission, normalization, or Store production policy was changed by this evaluation.

# Memory Lifecycle V1 Baseline Report

- Dataset: `evals\memory\lifecycle_v1.jsonl`
- Dataset SHA-256: `dd631241f992a1273ec823dbf8673dd1980d4140c908a7a99e6b9c9074c6aefd`
- Cases evaluated: `72`
- Strict cases: `64`
- Production Store mutation permitted: `False`
- Status: **ENGINEERING_FROZEN_WITH_KNOWN_POLICY_DEBT**

## Strict Metrics

| Metric | Result |
|---|---:|
| `case_count` | 64 |
| `passed_case_count` | 64 |
| `failed_case_count` | 0 |
| `overall_strict_case_accuracy` | 1.0000 |
| `plan_exact_match_accuracy` | 1.0000 |
| `rule_name_accuracy` | 1.0000 |
| `target_exact_match_accuracy` | 1.0000 |
| `target_set_accuracy` | 1.0000 |
| `target_micro_precision` | 1.0000 |
| `target_micro_recall` | 1.0000 |
| `target_micro_f1` | 1.0000 |
| `all_operation_target_micro_precision` | 1.0000 |
| `all_operation_target_micro_recall` | 1.0000 |
| `all_operation_target_micro_f1` | 1.0000 |
| `duplicate_exact_match_accuracy` | 1.0000 |
| `duplicate_set_accuracy` | 1.0000 |
| `legacy_target_exact_match_accuracy` | 1.0000 |
| `legacy_target_set_accuracy` | 1.0000 |

## Safety / Governance

| Metric | Result |
|---|---:|
| `proposed_closes_confirmed_violation_rate` | 0.0000 |
| `rejected_trigger_transition_rate` | 0.0000 |
| `confirmed_state_replacement_recall` | 1.0000 |
| `proposed_closes_proposed_recall` | 1.0000 |
| `rule_precedence_accuracy` | 1.0000 |
| `claimed_target_double_close_rate` | 0.0000 |
| `different_dimension_false_transition_rate` | 0.0000 |
| `same_value_false_transition_rate` | 0.0000 |
| `keeper_accuracy` | 1.0000 |
| `confirmed_keeper_accuracy` | 1.0000 |
| `importance_tiebreak_accuracy` | 1.0000 |
| `confidence_tiebreak_accuracy` | 1.0000 |
| `updated_at_tiebreak_accuracy` | 1.0000 |
| `cross_subject_false_collapse_rate` | 0.0000 |
| `cross_role_false_collapse_rate` | 0.0000 |
| `ordinary_event_false_collapse_rate` | 0.0000 |
| `legacy_proposed_over_confirmed_violation_rate` | 0.0000 |
| `legacy_confirmed_over_proposed_recall` | 1.0000 |

## By Operation

| Operation | Cases | Passed | Accuracy | Target set accuracy |
|---|---:|---:|---:|---:|
| `legacy_transition_targets` | 8 | 8 | 1.0000 | 1.0000 |
| `plan_transitions` | 40 | 40 | 1.0000 | 1.0000 |
| `semantic_duplicates` | 16 | 16 | 1.0000 | 1.0000 |

## By Rule

| Expected rule | Cases | Passed | Accuracy | Target set accuracy |
|---|---:|---:|---:|---:|
| `complete_confession_intent` | 2 | 2 | 1.0000 | 1.0000 |
| `none` | 38 | 38 | 1.0000 | 1.0000 |
| `replace_state:contact_availability` | 1 | 1 | 1.0000 | 1.0000 |
| `replace_state:contact_opportunity` | 1 | 1 | 1.0000 | 1.0000 |
| `replace_state:interaction.initiation_balance` | 1 | 1 | 1.0000 | 1.0000 |
| `replace_state:interaction_reciprocity` | 1 | 1 | 1.0000 | 1.0000 |
| `replace_state:partner_relationship_status` | 1 | 1 | 1.0000 | 1.0000 |
| `replace_state:relationship_familiarity` | 5 | 5 | 1.0000 | 1.0000 |
| `replace_state:relationship_familiarity,replace_state:contact_opportunity` | 1 | 1 | 1.0000 | 1.0000 |
| `resolve_active_conflict` | 4 | 4 | 1.0000 | 1.0000 |
| `restore_contact` | 2 | 2 | 1.0000 | 1.0000 |
| `restore_contact,restore_contact_frequency` | 1 | 1 | 1.0000 | 1.0000 |
| `restore_contact_frequency` | 2 | 2 | 1.0000 | 1.0000 |
| `restore_response_engagement` | 4 | 4 | 1.0000 | 1.0000 |

## Failed Strict Cases

| Case | Operation | Slice | Expected | Actual | Category |
|---|---|---|---|---|---|
| none | - | - | - | - | - |

## Error Taxonomy

| Category | Count |
|---|---:|

## Contract Interpretation

This baseline calls the three production lifecycle functions directly. Fixture memories derive `dedupe_key` through production `memory_dedupe_key()`. POLICY_REVIEW rows are observe-only and excluded from strict scoring.

No production Lifecycle rule, ontology, Relation policy, Admission policy, or Store contract was changed by this baseline.

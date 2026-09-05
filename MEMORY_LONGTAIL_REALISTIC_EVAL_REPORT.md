# Memory Long-tail Realistic Evaluation

- Report version: `memory-longtail-realistic-v3`
- Dataset: `evals\memory\longtail_realistic_v1.jsonl`
- Dataset SHA-256: `4654dd7aa96bc9eed5f2c69fd8c19661042a2be3640a3ca222a181f350f63ead`
- Scenarios: 26
- Turns: 50
- Mode: `shadow_fixture`
- Store mutation permitted: `False`
- Methodology: `production_gate_retrieval_and_shadow_validator_with_reviewed_fixture_extraction_and_relation_proposals`

## Metrics

| Layer | Metric | Value |
|---|---|---:|
| Summary | `scenario_pass_count` | 18 |
| Summary | `scenario_pass_rate` | 0.6923 |
| Gate | `gate_expected_positive_count` | 47 |
| Gate | `gate_expected_negative_count` | 3 |
| Gate | `gate_true_positive_count` | 37 |
| Gate | `gate_true_negative_count` | 3 |
| Gate | `gate_false_negative_count` | 10 |
| Gate | `gate_false_positive_count` | 0 |
| Gate | `gate_recall` | 0.7872 |
| Gate | `gate_precision` | 1.0 |
| Gate | `gate_specificity` | 1.0 |
| Gate | `durable_reversal_recall` | 0.8571 |
| Gate | `gate_false_negative_case_ids` | ["LT-A-001", "LT-C-002", "LT-C-003", "LT-M-001", "LT-P-002", "LT-R-004", "LT-U-002"] |
| Gate | `gate_false_negative_by_category` | {"complementary": 3, "durable_reversal": 1, "multi_turn_context": 1, "partial_change": 2, "perspective_protection": 2, "unrelated": 1} |
| Gate | `gate_false_negative_by_reason` | {"no_durable_signal": 10} |
| Extraction | `extraction_call_count` | 0 |
| Extraction | `extractor_failure_count` | 0 |
| Extraction | `extractor_attempt_failure_count` | 0 |
| Extraction | `extracted_claim_count` | 0 |
| Extraction | `extraction_expected_count` | 0 |
| Extraction | `extraction_success_count` | 0 |
| Extraction | `extraction_failure_count` | 0 |
| Extraction | `extraction_semantic_success_rate` | 0.0 |
| Extraction | `schema_validation_failure_count` | 0 |
| Extraction | `empty_claim_turn_count` | 0 |
| Extraction | `expected_memory_kind_accuracy` | 0.0 |
| Extraction | `expected_predicate_accuracy` | 0.0 |
| Extraction | `overall_semantic_identity_expected_count` | 0 |
| Extraction | `overall_semantic_identity_pass_count` | 0 |
| Extraction | `overall_semantic_identity_match_rate` | 0.0 |
| Extraction | `canonical_semantic_identity_expected_count` | 0 |
| Extraction | `canonicalized_expected_claim_count` | 0 |
| Extraction | `canonicalized_case_ids` | [] |
| Extraction | `canonical_semantic_identity_pass_count` | 0 |
| Extraction | `canonical_semantic_identity_match_rate` | 0.0 |
| Extraction | `canonical_governance_pass_count` | 0 |
| Extraction | `canonical_semantic_failure_case_ids` | [] |
| Extraction | `canonical_governance_failure_case_ids` | [] |
| Extraction | `custom_semantic_identity_expected_count` | 0 |
| Extraction | `custom_semantic_identity_pass_count` | 0 |
| Extraction | `custom_semantic_identity_match_rate` | 0.0 |
| Extraction | `semantic_identity_match_rate` | 0.0 |
| Extraction | `raw_predicate_match_rate` | 0.0 |
| Extraction | `canonical_predicate_match_rate` | 0.0 |
| Extraction | `true_custom_long_tail_claim_count` | 0 |
| Retrieval | `retrieval_expected_count` | 16 |
| Retrieval | `retrieval_hit_at_1` | 0.9375 |
| Retrieval | `retrieval_hit_at_3` | 1.0 |
| Retrieval | `retrieval_hit_at_5` | 1.0 |
| Retrieval | `retrieval_recall_at_5` | 1.0 |
| Retrieval | `avg_candidate_count` | 0.4375 |
| Relation | `relation_accuracy` | 0.7826 |
| Relation | `same_accuracy` | 1.0 |
| Relation | `update_accuracy` | 0.5 |
| Relation | `contradiction_accuracy` | 0.5 |
| Relation | `complementary_accuracy` | 0.625 |
| Relation | `unrelated_accuracy` | 0.6667 |
| Relation | `uncertain_accuracy` | 1.0 |
| Relation | `update_precision` | 1.0 |
| Relation | `update_recall` | 0.5 |
| Target | `target_memory_accuracy` | 0.7778 |
| Target | `target_memory_precision` | 1.0 |
| Validator Safety | `validator_allow_count` | 47 |
| Validator Safety | `validator_deny_count` | 1 |
| Validator Safety | `false_destructive_update_count` | 0 |
| Validator Safety | `false_destructive_update_rate` | 0.0 |
| Validator Safety | `confirmed_overwrite_violation_count` | 0 |
| Validator Safety | `event_over_pattern_violation_count` | 0 |
| Validator Safety | `weak_belief_overwrite_violation_count` | 0 |
| Judge | `semantic_judge_call_count` | 20 |
| Judge | `semantic_judge_failure_count` | 0 |
| Judge | `judge_evaluated_count` | 20 |
| Judge | `judge_transport_failure_count` | 0 |
| Judge | `judge_parse_failure_count` | 0 |
| Judge | `judge_first_attempt_parse_failure_count` | 0 |
| Judge | `judge_retry_count` | 0 |
| Judge | `judge_retry_success_count` | 0 |
| Judge | `judge_final_parse_failure_count` | 0 |
| Judge | `judge_final_parse_failure_rate` | 0.0 |
| Judge | `judge_fail_closed_count` | 0 |
| Judge | `judge_model_attempt_count` | 20 |
| Judge | `judge_relation_expected_count` | 18 |
| Judge | `judge_relation_correct_count` | 18 |
| Judge | `judge_relation_accuracy` | 1.0 |
| Judge | `judge_relation_mismatch_count` | 0 |
| Judge | `judge_target_mismatch_count` | 0 |
| Judge | `incorrect_update_proposal_count` | 0 |
| Judge | `incorrect_update_proposal_denied_count` | 0 |
| Judge | `semantic_judge_mean_latency_ms` | 0.0 |
| Judge | `semantic_judge_p50_latency_ms` | 0.0 |
| Judge | `semantic_judge_p95_latency_ms` | 0.0 |
| Judge | `semantic_judge_token_usage` | {"completion_tokens": 0, "prompt_tokens": 0, "total_tokens": 0} |
| Model Telemetry | `extractor_latency_p50` | 0.0 |
| Model Telemetry | `extractor_latency_p95` | 0.0 |
| Model Telemetry | `strong_upgrade_count` | 0 |
| Model Telemetry | `strong_upgrade_reason_counts` | {} |
| Model Telemetry | `strong_success_count` | 0 |
| Model Telemetry | `strong_failure_count` | 0 |
| Model Telemetry | `strong_latency_p50` | 0.0 |
| Model Telemetry | `strong_latency_p95` | 0.0 |
| Model Telemetry | `strong_fallback_to_flash_count` | 0 |
| Model Telemetry | `strong_no_value_added_count` | 0 |
| Model Telemetry | `strong_governed_local_resolution_count` | 0 |
| Model Telemetry | `judge_latency_p50` | 0.0 |
| Model Telemetry | `judge_latency_p95` | 0.0 |
| Model Telemetry | `prompt_tokens` | 0 |
| Model Telemetry | `completion_tokens` | 0 |
| Model Telemetry | `total_tokens` | 0 |
| Model Telemetry | `extractor_models` | [] |
| Model Telemetry | `semantic_judge_models` | ["scenario-fixture-judge"] |

## Error Attribution

| Layer | Count |
|---|---:|
| Gate | 7 |
| Retrieval | 2 |
| Target Selection | 1 |
| Validator | 1 |

## First Failing Stage

| Layer | Count |
|---|---:|
| Gate | 7 |
| Validator | 1 |

## Representative Failures

| Case | Category | Primary | Secondary |
|---|---|---|---|
| LT-R-004 | durable_reversal | Gate | - |
| LT-P-002 | perspective_protection | Gate | - |
| LT-C-002 | complementary | Gate | Retrieval, Target Selection |
| LT-C-003 | complementary | Gate | - |
| LT-U-002 | unrelated | Gate | Retrieval |
| LT-A-001 | partial_change | Gate | - |
| LT-M-001 | multi_turn_context | Gate | - |
| LT-H-001 | history_aware | Validator | - |

## Scope

Fixture mode uses reviewed fixture extraction and relation proposals.
Virtual context may add normally admitted memories for later turns, but it never commits relation/lifecycle mutations to the Store.
Failed scenarios retain a primary failure stage so Gate, Extraction, Normalization, Admission, Retrieval, Semantic Judge, Target Selection, and Validator gaps remain distinguishable.

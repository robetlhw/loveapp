# Memory Long-tail Realistic Evaluation

- Report version: `memory-longtail-realistic-v3`
- Dataset: `evals\memory\longtail_realistic_v1.jsonl`
- Dataset SHA-256: `4654dd7aa96bc9eed5f2c69fd8c19661042a2be3640a3ca222a181f350f63ead`
- Scenarios: 26
- Turns: 50
- Mode: `shadow_live`
- Store mutation permitted: `False`
- Methodology: `production_gate_retrieval_and_shadow_validator_with_real_extraction_and_relation_judge`
- Live models: `{"embedding": "AI-ModelScope/bge-small-zh-v1.5", "extractor": "deepseek-v4-flash", "semantic_relation_judge": "deepseek-v4-flash"}`
- Embedding telemetry: `{"dimension": 512, "document_call_count": 9, "document_text_count": 11, "embedding_backed_retrieval_confirmed": true, "embedding_retrieval_attempted": true, "failure_count": 0, "failure_types": {}, "model": "AI-ModelScope/bge-small-zh-v1.5", "provider": "sentence_transformers", "query_call_count": 9}`

## Metrics

| Layer | Metric | Value |
|---|---|---:|
| Summary | `scenario_pass_count` | 4 |
| Summary | `scenario_pass_rate` | 0.1538 |
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
| Extraction | `extraction_call_count` | 37 |
| Extraction | `extractor_failure_count` | 0 |
| Extraction | `extractor_attempt_failure_count` | 2 |
| Extraction | `extracted_claim_count` | 40 |
| Extraction | `extraction_expected_count` | 38 |
| Extraction | `extraction_success_count` | 32 |
| Extraction | `extraction_failure_count` | 6 |
| Extraction | `extraction_semantic_success_rate` | 0.8421 |
| Extraction | `schema_validation_failure_count` | 1 |
| Extraction | `empty_claim_turn_count` | 2 |
| Extraction | `expected_memory_kind_accuracy` | 0.9688 |
| Extraction | `expected_predicate_accuracy` | 0.0 |
| Extraction | `overall_semantic_identity_expected_count` | 32 |
| Extraction | `overall_semantic_identity_pass_count` | 15 |
| Extraction | `overall_semantic_identity_match_rate` | 0.4688 |
| Extraction | `canonical_semantic_identity_expected_count` | 16 |
| Extraction | `canonicalized_expected_claim_count` | 16 |
| Extraction | `canonicalized_case_ids` | ["LT-E-001", "LT-H-001", "LT-H-002", "LT-M-001", "LT-R-001", "LT-R-002", "LT-S-001", "LT-T-001", "LT-T-002", "LT-U-001"] |
| Extraction | `canonical_semantic_identity_pass_count` | 11 |
| Extraction | `canonical_semantic_identity_match_rate` | 0.6875 |
| Extraction | `canonical_governance_pass_count` | 8 |
| Extraction | `canonical_semantic_failure_case_ids` | ["LT-H-001", "LT-M-001", "LT-R-002", "LT-T-001", "LT-U-001"] |
| Extraction | `canonical_governance_failure_case_ids` | ["LT-M-001", "LT-T-001", "LT-T-002"] |
| Extraction | `custom_semantic_identity_expected_count` | 16 |
| Extraction | `custom_semantic_identity_pass_count` | 4 |
| Extraction | `custom_semantic_identity_match_rate` | 0.25 |
| Extraction | `semantic_identity_match_rate` | 0.4688 |
| Extraction | `raw_predicate_match_rate` | 0.0312 |
| Extraction | `canonical_predicate_match_rate` | 1.0 |
| Extraction | `true_custom_long_tail_claim_count` | 16 |
| Retrieval | `retrieval_expected_count` | 6 |
| Retrieval | `retrieval_hit_at_1` | 0.6667 |
| Retrieval | `retrieval_hit_at_3` | 0.8333 |
| Retrieval | `retrieval_hit_at_5` | 0.8333 |
| Retrieval | `retrieval_recall_at_5` | 0.8333 |
| Retrieval | `avg_candidate_count` | 0.1964 |
| Relation | `relation_accuracy` | 0.3333 |
| Relation | `same_accuracy` | 1.0 |
| Relation | `update_accuracy` | 0.2 |
| Relation | `contradiction_accuracy` | 0.0 |
| Relation | `complementary_accuracy` | 0.25 |
| Relation | `unrelated_accuracy` | 0.3333 |
| Relation | `uncertain_accuracy` | 0.0 |
| Relation | `update_precision` | 0.5 |
| Relation | `update_recall` | 0.2 |
| Target | `target_memory_accuracy` | 0.5 |
| Target | `target_memory_precision` | 0.7143 |
| Validator Safety | `validator_allow_count` | 15 |
| Validator Safety | `validator_deny_count` | 6 |
| Validator Safety | `false_destructive_update_count` | 0 |
| Validator Safety | `false_destructive_update_rate` | 0.0 |
| Validator Safety | `confirmed_overwrite_violation_count` | 0 |
| Validator Safety | `event_over_pattern_violation_count` | 0 |
| Validator Safety | `weak_belief_overwrite_violation_count` | 0 |
| Judge | `semantic_judge_call_count` | 9 |
| Judge | `semantic_judge_failure_count` | 0 |
| Judge | `judge_evaluated_count` | 9 |
| Judge | `judge_transport_failure_count` | 0 |
| Judge | `judge_parse_failure_count` | 0 |
| Judge | `judge_first_attempt_parse_failure_count` | 0 |
| Judge | `judge_retry_count` | 0 |
| Judge | `judge_retry_success_count` | 0 |
| Judge | `judge_final_parse_failure_count` | 0 |
| Judge | `judge_final_parse_failure_rate` | 0.0 |
| Judge | `judge_fail_closed_count` | 0 |
| Judge | `judge_model_attempt_count` | 9 |
| Judge | `judge_relation_expected_count` | 5 |
| Judge | `judge_relation_correct_count` | 3 |
| Judge | `judge_relation_accuracy` | 0.6 |
| Judge | `judge_relation_mismatch_count` | 2 |
| Judge | `judge_target_mismatch_count` | 2 |
| Judge | `incorrect_update_proposal_count` | 2 |
| Judge | `incorrect_update_proposal_denied_count` | 2 |
| Judge | `semantic_judge_mean_latency_ms` | 1521.391 |
| Judge | `semantic_judge_p50_latency_ms` | 1517.81 |
| Judge | `semantic_judge_p95_latency_ms` | 2398.368 |
| Judge | `semantic_judge_token_usage` | {"completion_tokens": 807, "prompt_tokens": 5740, "total_tokens": 6547} |
| Model Telemetry | `extractor_latency_p50` | 2553.694 |
| Model Telemetry | `extractor_latency_p95` | 6490.504 |
| Model Telemetry | `strong_upgrade_count` | 3 |
| Model Telemetry | `strong_upgrade_reason_counts` | {"existing_memory_conflict": 3} |
| Model Telemetry | `strong_success_count` | 3 |
| Model Telemetry | `strong_failure_count` | 0 |
| Model Telemetry | `strong_latency_p50` | 4964.027 |
| Model Telemetry | `strong_latency_p95` | 9056.238 |
| Model Telemetry | `strong_fallback_to_flash_count` | 0 |
| Model Telemetry | `strong_no_value_added_count` | 0 |
| Model Telemetry | `strong_governed_local_resolution_count` | 2 |
| Model Telemetry | `judge_latency_p50` | 1517.81 |
| Model Telemetry | `judge_latency_p95` | 2398.368 |
| Model Telemetry | `prompt_tokens` | 158314 |
| Model Telemetry | `completion_tokens` | 14136 |
| Model Telemetry | `total_tokens` | 172450 |
| Model Telemetry | `extractor_models` | ["deepseek-v4-flash", "deepseek-v4-pro"] |
| Model Telemetry | `semantic_judge_models` | ["deepseek-v4-flash"] |

## Fixture vs Live Before vs Live After

| Metric | Fixture | Live Before | Live After |
|---|---:|---:|---:|
| `scenario_pass_rate` | 0.6923 | 0.23076923076923078 | 0.1538 |
| `gate_recall` | 0.7872 | 0.7872 | 0.7872 |
| `extraction_semantic_success_rate` | 0.0 | 0.7894736842105263 | 0.8421 |
| `overall_semantic_identity_match_rate` | 0.0 | - | 0.4688 |
| `canonical_semantic_identity_match_rate` | 0.0 | - | 0.6875 |
| `custom_semantic_identity_match_rate` | 0.0 | - | 0.25 |
| `semantic_identity_match_rate` | 0.0 | - | 0.4688 |
| `retrieval_hit_at_3` | 1.0 | 1.0 | 0.8333 |
| `retrieval_hit_at_5` | 1.0 | 1.0 | 0.8333 |
| `retrieval_recall_at_5` | 1.0 | 1.0 | 0.8333 |
| `relation_accuracy` | 0.7826 | 0.6667 | 0.3333 |
| `judge_relation_accuracy` | 1.0 | 1.0 | 0.6 |
| `judge_first_attempt_parse_failure_count` | 0 | 2 | 0 |
| `judge_final_parse_failure_count` | 0 | 2 | 0 |
| `update_precision` | 1.0 | 1.0 | 0.5 |
| `target_memory_accuracy` | 0.7778 | 1.0 | 0.5 |
| `target_memory_precision` | 1.0 | 1.0 | 0.7143 |
| `false_destructive_update_count` | 0 | 0 | 0 |
| `extractor_latency_p50` | 0.0 | 2522.61 | 2553.694 |
| `extractor_latency_p95` | 0.0 | 71633.999 | 6490.504 |
| `strong_upgrade_count` | 0 | 6 | 3 |
| `strong_latency_p95` | 0.0 | 69492.171 | 9056.238 |
| `strong_no_value_added_count` | 0 | 2 | 0 |

## Error Attribution

| Layer | Count |
|---|---:|
| Admission | 1 |
| Canonical Governance | 3 |
| Extraction | 5 |
| Gate | 7 |
| Normalization | 13 |
| Retrieval | 3 |
| Semantic Judge | 2 |
| Target Selection | 3 |
| Validator | 3 |

## First Failing Stage

| Layer | Count |
|---|---:|
| Canonical Governance | 1 |
| Extraction | 3 |
| Gate | 5 |
| Normalization | 12 |
| Retrieval | 1 |

## Representative Failures

| Case | Category | Primary | Secondary |
|---|---|---|---|
| LT-R-002 | durable_reversal | Normalization | - |
| LT-R-003 | durable_reversal | Normalization | Validator |
| LT-R-004 | durable_reversal | Normalization | Admission, Extraction, Gate |
| LT-S-001 | same | Extraction | - |
| LT-S-002 | same | Normalization | - |
| LT-E-001 | event_vs_pattern | Retrieval | Target Selection |
| LT-E-002 | event_vs_pattern | Normalization | - |
| LT-P-001 | perspective_protection | Extraction | - |
| LT-P-002 | perspective_protection | Gate | - |
| LT-C-001 | complementary | Extraction | - |
| LT-C-002 | complementary | Gate | Extraction |
| LT-C-003 | complementary | Gate | - |
| LT-U-001 | unrelated | Normalization | Retrieval |
| LT-U-002 | unrelated | Gate | Normalization, Retrieval |
| LT-T-001 | historical_current | Normalization | Canonical Governance |
| LT-T-002 | historical_current | Canonical Governance | - |
| LT-A-001 | partial_change | Gate | - |
| LT-A-002 | partial_change | Normalization | Semantic Judge, Target Selection, Validator |
| LT-M-001 | multi_turn_context | Normalization | Gate, Canonical Governance |
| LT-B-001 | ambiguous_target | Normalization | Semantic Judge, Target Selection, Validator |
| LT-H-001 | history_aware | Normalization | - |
| LT-H-002 | history_aware | Normalization | - |

## Live V3 Evaluation Answers

1. Canonicalized reviewed claims: 16 across case IDs ["LT-E-001", "LT-H-001", "LT-H-002", "LT-M-001", "LT-R-001", "LT-R-002", "LT-S-001", "LT-T-001", "LT-T-002", "LT-U-001"].
2. Canonical contract: semantic identity passed 11/16; deterministic governance passed 8/16. Semantic failures: ["LT-H-001", "LT-M-001", "LT-R-002", "LT-T-001", "LT-U-001"]; governance failures: ["LT-M-001", "LT-T-001", "LT-T-002"].
3. True Custom long-tail claims reaching the custom path: 16.
4. True long-tail retrieval: Hit@3=0.8333, Recall@5=0.8333 across 6 eligible target observations; embedding-backed retrieval confirmed=True.
5. Semantic Judge: first-attempt parse failures=0, retry successes=0, final parse failures=0, completed Judge relation accuracy=0.6, UPDATE precision=0.5.
6. Strong Upgrade: calls=3, p95=9056.238ms, no-value-added lower bound=0.
7. Safety: false destructive update=0, confirmed overwrite=0, event-over-pattern=0, weak-belief-overwrite=0.

## Operational Observations

Real Gate: 10 false negatives across 47 expected positive turns (recall 0.7872). Reasons: no_durable_signal=10; categories: complementary=3, partial_change=2, perspective_protection=2, durable_reversal=1, multi_turn_context=1, unrelated=1.
Relation pipeline accuracy is 0.3333; completed Judge accuracy is 0.6 across 5 comparable expectations, with 0 call failures. Most frequent completed-Judge mismatches: complementary->update (1), uncertain->update (1).
Unsafe UPDATE proposals: 2/2 incorrect UPDATE proposals were denied by the validator; 0 were validator-approved destructive mismatches.
Hard-case drift: not run.

## Current Status

- Memory Foundation: REQUIRES SEPARATE REGRESSION.
- Canonical Governance: NEEDS REVIEW.
- Long-tail Eval Contract: PASS.
- Gate: ACCEPTABLE.
- Extraction Semantic Quality: NEEDS IMPROVEMENT.
- Extraction Latency: PASS.
- Retrieval: NEEDS IMPROVEMENT.
- Semantic Judge: NEEDS IMPROVEMENT.
- Validator Safety: PASS.
- Phase 2C NOT APPROVED; lifecycle commit remains shadow-only.

## Scope

Live mode invokes the configured production Memory extractor, embedding retriever, semantic relation judge, and deterministic validator.
Virtual context may add normally admitted memories for later turns, but it never commits relation/lifecycle mutations to the Store.
Failed scenarios retain a primary failure stage so Gate, Extraction, Normalization, Admission, Retrieval, Semantic Judge, Target Selection, and Validator gaps remain distinguishable.

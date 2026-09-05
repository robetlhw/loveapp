# Memory Long-tail Realistic Evaluation

- Dataset: `evals\memory\longtail_realistic_v1.jsonl`
- Dataset SHA-256: `0527493664a32fcdd3ceab346af00e93d0e9f91f67832aa05f3cec1c105088aa`
- Scenarios: 26
- Turns: 50
- Mode: `shadow_live`
- Store mutation permitted: `False`
- Methodology: `production_gate_retrieval_and_shadow_validator_with_real_extraction_and_relation_judge`
- Live models: `{"embedding": "AI-ModelScope/bge-small-zh-v1.5", "extractor": "deepseek-v4-flash", "semantic_relation_judge": "deepseek-v4-flash"}`
- Embedding telemetry: `{"dimension": 512, "document_call_count": 11, "document_text_count": 15, "embedding_backed_retrieval_confirmed": true, "embedding_retrieval_attempted": true, "failure_count": 0, "failure_types": {}, "model": "AI-ModelScope/bge-small-zh-v1.5", "provider": "sentence_transformers", "query_call_count": 11}`

## Metrics

| Layer | Metric | Value |
|---|---|---:|
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
| Extraction | `extractor_attempt_failure_count` | 3 |
| Extraction | `extracted_claim_count` | 42 |
| Extraction | `extraction_expected_count` | 38 |
| Extraction | `extraction_success_count` | 30 |
| Extraction | `extraction_failure_count` | 8 |
| Extraction | `schema_validation_failure_count` | 1 |
| Extraction | `empty_claim_turn_count` | 1 |
| Extraction | `expected_memory_kind_accuracy` | 0.9667 |
| Extraction | `expected_predicate_accuracy` | 0.0 |
| Retrieval | `retrieval_expected_count` | 4 |
| Retrieval | `retrieval_hit_at_1` | 0.75 |
| Retrieval | `retrieval_hit_at_3` | 1.0 |
| Retrieval | `retrieval_hit_at_5` | 1.0 |
| Retrieval | `retrieval_recall_at_5` | 1.0 |
| Retrieval | `avg_candidate_count` | 0.25 |
| Relation | `relation_accuracy` | 0.6667 |
| Relation | `same_accuracy` | 1.0 |
| Relation | `update_accuracy` | 0.5 |
| Relation | `contradiction_accuracy` | 0.0 |
| Relation | `complementary_accuracy` | 1.0 |
| Relation | `unrelated_accuracy` | 0.0 |
| Relation | `uncertain_accuracy` | 1.0 |
| Relation | `update_precision` | 1.0 |
| Relation | `update_recall` | 0.5 |
| Target | `target_memory_accuracy` | 1.0 |
| Target | `target_memory_precision` | 1.0 |
| Validator Safety | `validator_allow_count` | 17 |
| Validator Safety | `validator_deny_count` | 6 |
| Validator Safety | `false_destructive_update_count` | 0 |
| Validator Safety | `false_destructive_update_rate` | 0.0 |
| Validator Safety | `confirmed_overwrite_violation_count` | 0 |
| Validator Safety | `event_over_pattern_violation_count` | 0 |
| Validator Safety | `weak_belief_overwrite_violation_count` | 0 |
| Judge | `semantic_judge_call_count` | 11 |
| Judge | `semantic_judge_failure_count` | 2 |
| Judge | `judge_evaluated_count` | 9 |
| Judge | `judge_transport_failure_count` | 0 |
| Judge | `judge_parse_failure_count` | 2 |
| Judge | `judge_relation_expected_count` | 3 |
| Judge | `judge_relation_correct_count` | 3 |
| Judge | `judge_relation_accuracy` | 1.0 |
| Judge | `judge_relation_mismatch_count` | 0 |
| Judge | `judge_target_mismatch_count` | 0 |
| Judge | `incorrect_update_proposal_count` | 0 |
| Judge | `incorrect_update_proposal_denied_count` | 0 |
| Judge | `semantic_judge_mean_latency_ms` | 1551.618 |
| Judge | `semantic_judge_p50_latency_ms` | 1685.703 |
| Judge | `semantic_judge_p95_latency_ms` | 1981.606 |
| Judge | `semantic_judge_token_usage` | {"completion_tokens": 895, "prompt_tokens": 5856, "total_tokens": 6751} |
| Model Telemetry | `extractor_latency_p50` | 2522.61 |
| Model Telemetry | `extractor_latency_p95` | 71633.999 |
| Model Telemetry | `judge_latency_p50` | 1685.703 |
| Model Telemetry | `judge_latency_p95` | 1981.606 |
| Model Telemetry | `prompt_tokens` | 171122 |
| Model Telemetry | `completion_tokens` | 31545 |
| Model Telemetry | `total_tokens` | 202667 |
| Model Telemetry | `extractor_models` | ["deepseek-v4-flash", "deepseek-v4-pro"] |
| Model Telemetry | `semantic_judge_models` | ["deepseek-v4-flash"] |

## Fixture vs Live

| Metric | Fixture | Live |
|---|---:|---:|
| `gate_recall` | 0.7872 | 0.7872 |
| `retrieval_hit_at_5` | 1.0 | 1.0 |
| `retrieval_recall_at_5` | 1.0 | 1.0 |
| `relation_accuracy` | 0.7826 | 0.6667 |
| `update_precision` | 1.0 | 1.0 |
| `target_memory_accuracy` | 0.7778 | 1.0 |
| `target_memory_precision` | 1.0 | 1.0 |
| `false_destructive_update_count` | 0 | 0 |

## Hard-case Repeat Consistency

- Judge reach: 0 completed / 0 attempted calls across 24 relation-expected claim observations.

| Scope | Relation | Target | Validator |
|---|---:|---:|---:|
| Aggregate | 1.0 | 1.0 | 1.0 |
| `LT-A-001` | 1.0 | 1.0 | 1.0 |
| `LT-C-002` | 1.0 | 1.0 | 1.0 |
| `LT-C-003` | 1.0 | 1.0 | 1.0 |
| `LT-H-001` | 1.0 | 1.0 | 1.0 |
| `LT-M-001` | 1.0 | 1.0 | 1.0 |
| `LT-P-002` | 1.0 | 1.0 | 1.0 |
| `LT-R-004` | 1.0 | 1.0 | 1.0 |
| `LT-U-002` | 1.0 | 1.0 | 1.0 |

## Error Attribution

| Layer | Count |
|---|---:|
| Evaluation Expectation | 7 |
| Extraction | 7 |
| Gate | 7 |
| Retrieval | 2 |
| Semantic Judge | 1 |
| Validator | 2 |

## First Failing Stage

| Layer | Count |
|---|---:|
| Evaluation Expectation | 6 |
| Extraction | 6 |
| Gate | 6 |
| Retrieval | 1 |
| Validator | 1 |

## Representative Failures

| Case | Category | Primary | Secondary |
|---|---|---|---|
| LT-R-001 | durable_reversal | Evaluation Expectation | - |
| LT-R-002 | durable_reversal | Evaluation Expectation | - |
| LT-R-003 | durable_reversal | Validator | - |
| LT-R-004 | durable_reversal | Extraction | Gate |
| LT-S-001 | same | Extraction | - |
| LT-E-001 | event_vs_pattern | Extraction | - |
| LT-P-001 | perspective_protection | Extraction | - |
| LT-P-002 | perspective_protection | Gate | - |
| LT-C-001 | complementary | Extraction | - |
| LT-C-002 | complementary | Gate | Evaluation Expectation |
| LT-C-003 | complementary | Gate | - |
| LT-U-001 | unrelated | Retrieval | - |
| LT-U-002 | unrelated | Gate | Retrieval |
| LT-T-001 | historical_current | Evaluation Expectation | - |
| LT-T-002 | historical_current | Evaluation Expectation | - |
| LT-A-001 | partial_change | Gate | - |
| LT-A-002 | partial_change | Extraction | Validator |
| LT-M-001 | multi_turn_context | Gate | Semantic Judge, Extraction |
| LT-H-001 | history_aware | Evaluation Expectation | - |
| LT-H-002 | history_aware | Evaluation Expectation | - |

## Live Evaluation Answers

1. Real Gate: 10 false negatives across 47 expected positive turns (recall 0.7872). Reasons: no_durable_signal=10; categories: complementary=3, partial_change=2, perspective_protection=2, durable_reversal=1, multi_turn_context=1, unrelated=1.
2. Real Retriever: Recall@5 is 1.0 across 4 eligible target observations; retrieval is measured only where the expected target reached virtual context. Embedding-backed retrieval confirmed=True.
3. Relation pipeline accuracy is 0.6667; completed Judge accuracy is 1.0 across 3 comparable expectations, with 2 call failures. Most frequent completed-Judge mismatches: none recorded.
4. Unsafe UPDATE proposals: 0/0 incorrect UPDATE proposals were denied by the validator; 0 were validator-approved destructive mismatches.
5. Protection violations: confirmed overwrite=0, event-over-pattern=0, weak-belief-overwrite=0.
6. Hard-case drift: relation/target/validator consistency = 1.0/1.0/1.0; Judge completed 0/0 attempted calls across 24 expectations.
7. Current Long-tail status:
   - Architecture: PASS (shadow-only).
   - Gate: ACCEPTABLE.
   - Retrieval: PASS.
   - Semantic Judge: NEEDS IMPROVEMENT.
   - Validator: PASS.
   - Phase 2C NOT APPROVED: lifecycle commit remains shadow-only.

## Scope

Live mode invokes the configured production Memory extractor, embedding retriever, semantic relation judge, and deterministic validator.
Virtual context may add normally admitted memories for later turns, but it never commits relation/lifecycle mutations to the Store.
Failed scenarios retain a primary failure stage so Gate, Extraction, Normalization, Admission, Retrieval, Semantic Judge, Target Selection, and Validator gaps remain distinguishable.

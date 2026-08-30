# Memory Long-tail Baseline

- Dataset: `evals\memory\longtail_relations_v1.jsonl`
- Dataset SHA-256: `716bae2d55d9c38af9a40de6be5f368af5e605d9402b0b5503b40f0f01c0e52f`
- Cases: 42
- Passed relation-governance cases: 25
- Store mutation permitted: `False`

## Dataset

| Category | Cases | Relation accuracy |
|---|---:|---:|
| boundary_change | 7 | 0.4286 |
| emotional_openness | 7 | 0.4286 |
| family_integration | 7 | 0.8571 |
| future_commitment | 7 | 0.5714 |
| interaction_investment | 7 | 0.5714 |
| social_integration | 7 | 0.7143 |

## Layered Metrics

| Layer | Metric | Value |
|---|---|---:|
| Gate | `long_tail_gate_expected_positive_count` | 34 |
| Gate | `long_tail_gate_true_positive_count` | 22 |
| Gate | `long_tail_gate_false_negative_count` | 12 |
| Gate | `long_tail_gate_recall` | 0.6471 |
| Gate | `durable_reversal_gate_expected_count` | 7 |
| Gate | `durable_reversal_gate_true_positive_count` | 6 |
| Gate | `durable_reversal_gate_recall` | 0.8571 |
| Retrieval | `candidate_retrieval_expected_count` | 36 |
| Retrieval | `candidate_retrieval_hit_at_1` | 0.9167 |
| Retrieval | `candidate_retrieval_hit_at_3` | 1.0 |
| Retrieval | `candidate_retrieval_hit_at_5` | 1.0 |
| Retrieval | `candidate_retrieval_recall_at_5` | 1.0 |
| Retrieval | `avg_candidate_count` | 1.0 |
| Relation | `relation_accuracy` | 0.5952 |
| Relation | `same_accuracy` | 0.8571 |
| Relation | `update_accuracy` | 1.0 |
| Relation | `contradiction_accuracy` | 0.6667 |
| Relation | `complementary_accuracy` | 0.2857 |
| Relation | `unrelated_accuracy` | 0.6 |
| Relation | `uncertain_accuracy` | 0.3333 |
| Target | `target_memory_accuracy` | 0.7619 |
| Target | `target_memory_precision` | 1.0 |
| Target | `target_memory_predicted_count` | 18 |
| Target | `target_memory_expected_count` | 18 |
| Validator safety | `false_destructive_update_count` | 0 |
| Validator safety | `false_destructive_update_rate` | 0.0 |
| Validator safety | `confirmed_overwrite_violation_count` | 0 |
| Validator safety | `event_over_pattern_violation_count` | 0 |
| Validator safety | `weak_belief_overwrite_violation_count` | 0 |
| Judge | `semantic_judge_call_count` | 39 |
| Judge | `semantic_judge_failure_count` | 0 |
| Judge | `semantic_judge_mean_latency_ms` | 2.5 |
| Judge | `semantic_judge_p50_latency_ms` | 2.5 |
| Judge | `semantic_judge_p95_latency_ms` | 2.5 |
| Judge | `semantic_judge_token_usage` | {"completion_tokens": 390, "prompt_tokens": 780, "total_tokens": 1170} |

## Error Attribution

| Layer | Count |
|---|---:|
| Gate | 13 |
| None | 20 |
| Semantic Judge | 17 |
| Target | 10 |

## Representative Failures

| Case | Expected | Actual | Resolution status | Attribution |
|---|---|---|---|---|
| LT-003 | contradiction | update | validator_denied | Gate, Semantic Judge |
| LT-007 | complementary | update | validator_denied | Semantic Judge |
| LT-012 | unrelated | update | validator_denied | Semantic Judge, Target |
| LT-018 | uncertain | update | validator_denied | Gate, Semantic Judge, Target |
| LT-019 | uncertain | update | validator_denied | Semantic Judge, Target |
| LT-020 | uncertain | update | validator_denied | Semantic Judge, Target |
| LT-021 | uncertain | update | validator_denied | Gate, Semantic Judge, Target |
| LT-024 | complementary | update | validator_denied | Gate, Semantic Judge |
| LT-025 | complementary | update | validator_denied | Semantic Judge |
| LT-028 | uncertain | update | validator_denied | Semantic Judge, Target |
| LT-032 | same | update | validator_denied | Semantic Judge |
| LT-033 | uncertain | update | validator_denied | Semantic Judge, Target |

## Interpretation

This is a read-only shadow baseline. Semantic proposals never commit to the Store.
The next recommended single-layer change is Gate coverage when durable-reversal recall is below target; otherwise attribute failures to retrieval, Judge, or Validator using the table above.

Known scope limits: no Judge prompt/threshold changes, Retriever reweighting, Validator threshold changes, lifecycle commit, ontology expansion, or multi-target mutation is included in this baseline.

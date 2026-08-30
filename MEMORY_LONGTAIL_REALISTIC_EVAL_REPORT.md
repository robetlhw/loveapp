# Memory Long-tail Realistic Evaluation

- Dataset: `evals\memory\longtail_realistic_v1.jsonl`
- Dataset SHA-256: `0527493664a32fcdd3ceab346af00e93d0e9f91f67832aa05f3cec1c105088aa`
- Scenarios: 26
- Turns: 50
- Mode: `shadow_fixture`
- Store mutation permitted: `False`

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
| Retrieval | `retrieval_expected_count` | 16 |
| Retrieval | `retrieval_hit_at_1` | 0.9375 |
| Retrieval | `retrieval_hit_at_3` | 1.0 |
| Retrieval | `retrieval_hit_at_5` | 1.0 |
| Retrieval | `retrieval_recall_at_5` | 1.0 |
| Relation | `relation_accuracy` | 0.7826 |
| Relation | `same_accuracy` | 1.0 |
| Relation | `update_accuracy` | 1.0 |
| Relation | `contradiction_accuracy` | 0.5 |
| Relation | `complementary_accuracy` | 0.625 |
| Relation | `unrelated_accuracy` | 0.6667 |
| Relation | `uncertain_accuracy` | 1.0 |
| Target | `target_memory_accuracy` | 0.7778 |
| Target | `target_memory_precision` | 1.0 |
| Safety | `false_destructive_update_count` | 0 |
| Safety | `false_destructive_update_rate` | 0.0 |
| Safety | `confirmed_overwrite_violation_count` | 0 |
| Safety | `event_over_pattern_violation_count` | 0 |
| Safety | `weak_belief_overwrite_violation_count` | 0 |
| Judge | `semantic_judge_call_count` | 20 |
| Judge | `semantic_judge_failure_count` | 0 |
| Judge | `semantic_judge_mean_latency_ms` | 0.0 |
| Judge | `semantic_judge_p50_latency_ms` | 0.0 |
| Judge | `semantic_judge_p95_latency_ms` | 0.0 |
| Judge | `semantic_judge_token_usage` | {"completion_tokens": 0, "prompt_tokens": 0, "total_tokens": 0} |

## Error Attribution

| Layer | Count |
|---|---:|
| Gate | 10 |
| Semantic Judge | 20 |
| Target Selection | 1 |
| Validator | 1 |

## First Failing Stage

| Layer | Count |
|---|---:|
| Gate | 10 |
| Semantic Judge | 20 |
| Validator | 1 |

## Representative Failures

| Case | Category | Attribution |
|---|---|---|
| LT-R-004 | durable_reversal | Gate, Semantic Judge |
| LT-P-002 | perspective_protection | Gate |
| LT-C-002 | complementary | Gate, Semantic Judge, Target Selection |
| LT-C-003 | complementary | Gate |
| LT-U-002 | unrelated | Gate, Semantic Judge |
| LT-A-001 | partial_change | Gate |
| LT-M-001 | multi_turn_context | Gate, Semantic Judge |
| LT-H-001 | history_aware | Semantic Judge, Validator |

## Scope

This evaluation uses reviewed scripted extraction/proposals and a virtual in-process context. It never commits destructive lifecycle changes to the Store.
Failed scenarios retain layer attribution so Gate, Extraction, Retrieval, Semantic Judge, Target Selection and Validator gaps remain distinguishable.

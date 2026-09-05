# Memory Gate V2 Evaluation Report

Generated: `2026-09-01T18:04:04.269315+00:00`
Dataset: `evals\memory\gate_v2_60.jsonl`
Store mutation permitted: `False`
Label leakage permitted: `False`
Memory Gate V2 freeze status: `FROZEN`

## Current Gate vs Hybrid Gate V2

| Metric | Current Python Gate | Hybrid Gate V2 | Delta |
|---|---:|---:|---:|
| Recall | 0.5455 | 1.0000 | +0.4545 |
| Precision | 0.9231 | 1.0000 | +0.0769 |
| Specificity | 0.8750 | 1.0000 | +0.1250 |

## Named Slices

| Slice | Metric | Current | Hybrid | Delta |
|---|---|---:|---:|---:|
| USER_BELIEF | recall | 0.3333 | 1.0000 | +0.6667 |
| PARTIAL_CHANGE | recall | 0.6667 | 1.0000 | +0.3333 |
| DURABLE_CHANGE | recall | 0.7500 | 1.0000 | +0.2500 |
| CONTEXT_DEPENDENT_REPLY | recall | 0.1000 | 1.0000 | +0.9000 |
| TRANSIENT | specificity | 0.6667 | 1.0000 | +0.3333 |
| SMALL_TALK | specificity | 1.0000 | 1.0000 | +0.0000 |

## Gate V2 Metrics

- `case_count`: `60`
- `routing_accuracy`: `1.0`
- `hard_drop_false_negative_count`: `0`
- `context_pass_recall`: `1.0`
- `semantic_gate_recall`: `1.0`
- `semantic_gate_precision`: `1.0`
- `semantic_gate_specificity`: `1.0`
- `semantic_gate_reason_accuracy`: `0.7833`
- `current_user_belief_recall`: `0.3333`
- `user_belief_recall`: `1.0`
- `user_belief_false_negative_reduction`: `4`
- `current_partial_change_recall`: `0.6667`
- `partial_change_recall`: `1.0`
- `partial_change_false_negative_reduction`: `1`
- `durable_change_recall`: `1.0`
- `context_dependent_reply_recall`: `1.0`
- `context_short_reply_semantic_recall`: `1.0`
- `transient_specificity`: `1.0`
- `transient_belief_negative_accuracy`: `0.0`
- `durable_belief_positive_recall`: `0.0`
- `small_talk_specificity`: `1.0`
- `extraction_call_count`: `53`
- `extraction_failure_count`: `0`
- `extraction_attempt_failure_count`: `0`
- `schema_validation_failure_count`: `0`
- `gate_contract_error_count`: `0`
- `claim_schema_error_count`: `2`
- `claim_schema_invalid_turn_count`: `2`
- `empty_claim_turn_count`: `0`
- `semantic_gate_contract_violation_count`: `0`
- `missing_gate_contract_count`: `0`
- `false_with_claims_count`: `0`
- `flash_latency_p50_ms`: `2973.31`
- `flash_latency_p95_ms`: `4517.13`
- `prompt_tokens`: `314215`
- `completion_tokens`: `18184`
- `total_tokens`: `332399`
- `model_call_counts`: `{'deepseek-v4-flash': 53, 'deepseek-v4-pro': 2}`

## Failed Cases

| Case | Expected Route | Actual Route | Expected L1 | Actual L1 | Expected Reason | Actual Reason |
|---|---|---|---:|---:|---|---|
| GATE-005 | HARD_PASS | HARD_PASS | true | true | PREFERENCE | STABLE_FACT |
| GATE-008 | HARD_PASS | HARD_PASS | true | true | PREFERENCE | STABLE_FACT |
| GATE-010 | SEMANTIC_REVIEW | SEMANTIC_REVIEW | true | true | PREFERENCE | STABLE_FACT |
| GATE-011 | SEMANTIC_REVIEW | SEMANTIC_REVIEW | true | true | RELATIONSHIP_CHANGE | INTERACTION_PATTERN |
| GATE-012 | SEMANTIC_REVIEW | SEMANTIC_REVIEW | true | true | PARTIAL_CHANGE | STABLE_FACT |
| GATE-013 | SEMANTIC_REVIEW | SEMANTIC_REVIEW | true | true | INTERACTION_PATTERN | STABLE_FACT |
| GATE-016 | SEMANTIC_REVIEW | SEMANTIC_REVIEW | true | true | RELATIONSHIP_CHANGE | INTERACTION_PATTERN |
| GATE-017 | SEMANTIC_REVIEW | SEMANTIC_REVIEW | true | true | RELATIONSHIP_CHANGE | INTERACTION_PATTERN |
| GATE-018 | SEMANTIC_REVIEW | SEMANTIC_REVIEW | true | true | PARTIAL_CHANGE | INTERACTION_PATTERN |
| GATE-033 | SEMANTIC_REVIEW | SEMANTIC_REVIEW | false | false | TRANSIENT | NO_MEMORY |
| GATE-058 | SEMANTIC_REVIEW | SEMANTIC_REVIEW | true | true | COMPOUND_MEMORY | STABLE_FACT |
| GATE-059 | SEMANTIC_REVIEW | SEMANTIC_REVIEW | true | true | PARTIAL_CHANGE | INTERACTION_PATTERN |
| GATE-060 | SEMANTIC_REVIEW | SEMANTIC_REVIEW | true | true | RELATIONSHIP_CHANGE | INTERACTION_PATTERN |

## Required Questions

1. Current Python Gate vs Hybrid Gate V2 的 Recall / Precision 分别是多少？  
   Current = `0.5455 / 0.9231`; Hybrid = `1.0000 / 1.0000`.
2. USER_BELIEF 漏检减少了多少？  
   Hybrid recall = `1.0000`; false negatives reduced by `4`.
3. PARTIAL_CHANGE 漏检减少了多少？  
   Hybrid recall = `1.0000`; false negatives reduced by `1`.
4. 所有 CONTEXT_PASS case 是否都正确路由？  
   Context-pass routing recall = `1.0000`.
5. 拒绝、不知道和 topic switch 是否 L0 PASS、L1 不乱存？  
   `True`.
6. 是否出现 should_extract=false 但 claims!=[]？  
   False-with-claims cases = `0`; all semantic contract violations = `0`.
7. HARD_DROP 是否误杀高价值 Memory？  
   High-value false negatives = `0`.
8. Flash Gate p50/p95 latency 与 token usage 是多少？  
   `2973.31 / 4517.13 ms`; total tokens = `332399`.
9. 本轮是否修改 Gate 之外的生产逻辑？  
   `Yes, narrowly`; only structured PendingMemoryContext handoff and Gate/extraction accounting changed. Store, retrieval, relation, validator, and lifecycle behavior were not changed.
10. 下一轮 Extraction Eval 最应优先看什么？  
   Prioritize empty positive extractions, spurious claims, subject/perspective accuracy, atomization, and evidence validity.

Finalization decision: `Memory Gate V2 = FROZEN`.

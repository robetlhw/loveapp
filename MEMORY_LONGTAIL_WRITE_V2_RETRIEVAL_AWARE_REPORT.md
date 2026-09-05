# Memory Long-tail Write V2 Retrieval-aware Report

- Version: `memory-longtail-write-v2-draft1`
- Cases: `40`
- Passed: `6`
- Evaluated rows (including repeats): `40`
- Evaluation status: **DATASET_REVIEW_REQUIRED**
- Safety status (applied destructive writes): **PASS**
- Evaluation mode: `shadow_live`
- Dataset status: **DATASET_REVIEW_REQUIRED**
- Repeat runs: `1`
- Hard-case mode: `False`
- Production Store mutation permitted: `False`
- Isolated InMemoryStore mutation permitted: `True`

## Repeat / Hard-case Diagnostics

- Filter status: `NOT_REQUESTED`
- Matched IDs: `[]`
- Missing IDs: `[]`
- Relation consistency: `-`
- Target consistency: `-`
- Validator consistency: `-`
- Fixture repeat scope: `1`
- Live repeat scope: `1`
- Consistency rates are per-case mode rates across repeated runs; `1.0` means no drift.

## Dataset Contract

- Shared bank: `120` memories
- Overlay: `200` memories
- Candidate pools: `shared_pools + overlay`, actual sizes `{'35': 14, '65': 22, '95': 4}`
- Document's approximate `125` pool claim: **AMBIGUOUS**; case-declared pools produce 35/65/95 candidates.
- Gold collision review cases: `20` ['LTW2-002', 'LTW2-003', 'LTW2-004', 'LTW2-005', 'LTW2-006', 'LTW2-007', 'LTW2-008', 'LTW2-009', 'LTW2-010', 'LTW2-018', 'LTW2-020', 'LTW2-029', 'LTW2-031', 'LTW2-032', 'LTW2-034', 'LTW2-035', 'LTW2-036', 'LTW2-037', 'LTW2-038', 'LTW2-039']
- Collision audit: exact shared/overlay cases `17`; Gold exact `11`; semantic-tag cases `37`; Gold tag `18`.
- Non-Gold collision cases remain diagnostic only: `['LTW2-001', 'LTW2-011', 'LTW2-012', 'LTW2-013', 'LTW2-016', 'LTW2-017', 'LTW2-019', 'LTW2-021', 'LTW2-022', 'LTW2-023', 'LTW2-024', 'LTW2-025', 'LTW2-026', 'LTW2-027', 'LTW2-028', 'LTW2-030', 'LTW2-033', 'LTW2-040']`

## Retrieval and Ranking

- Vector retrieval stage: **Top-20** candidates
- Cheap ranking stage: **Top-5** candidates (also supplied to the Semantic Judge)
- Retrieval engine: `['HybridMemoryRetriever']`
- Embedding input: `production_retrieval_text_composite`
- Embedding input detail: HybridMemoryRetriever._retrieval_text: summary + original text + canonical/state fields + selected payload + evidence spans
- Judge query input: `incoming summary + evidence spans`

| Metric | Value |
|---|---:|

### Per-run summary

| Run | Cases | Passed | Failed |
|---:|---:|---:|---:|
| 1 | 40 | 6 | 34 |
| `retrieval_expected_case_count` | 35 |
| `retrieval_expected_target_count` | 40 |
| `retrieval_hit_at_1` | 0.5714 |
| `retrieval_hit_at_3` | 0.8857 |
| `retrieval_hit_at_5` | 1.0000 |
| `retrieval_hit_at_10` | 1.0000 |
| `retrieval_hit_at_20` | 1.0000 |
| `retrieval_recall_at_5` | 0.9750 |
| `retrieval_recall_at_10` | 0.9750 |
| `retrieval_recall_at_20` | 1.0000 |
| `mrr` | 0.7462 |
| `gold_retention_at_5` | 0.9250 |
| `target_set_recall_at_5` | 0.9250 |
| `gold_target_set_exact_at_5` | 0.9143 |
| `hard_negative_promotion_count` | 3 |
| `hard_negative_promotion_rate` | 0.0857 |
| `unrelated_candidate_vector_count` | 675 |
| `unrelated_candidate_ranked_count` | 123 |
| `unrelated_candidate_retention_rate` | 0.1822 |
| `avg_candidate_count` | 57.5000 |
| `retrieval_latency_p50_ms` | 561.9020 |
| `retrieval_latency_p95_ms` | 830.7080 |

## Collision Details

| Case | Gold exact | Gold tag | Other exact/tag overlap |
|---|---|---|---|
| LTW2-001 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O002', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SP028']}]} |
| LTW2-002 | [] | [{'overlay_memory_id': 'O006', 'overlay_role': 'gold', 'shared_memory_ids': ['SP007']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O007', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP008']}, {'overlay_memory_id': 'O009', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP002']}]} |
| LTW2-003 | [] | [{'overlay_memory_id': 'O011', 'overlay_role': 'gold', 'shared_memory_ids': ['SR001']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O012', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR009']}, {'overlay_memory_id': 'O013', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR008']}, {'overlay_memory_id': 'O015', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR004']}]} |
| LTW2-004 | [{'overlay_memory_id': 'O016', 'overlay_role': 'gold', 'shared_memory_ids': ['SI001']}] | [{'overlay_memory_id': 'O016', 'overlay_role': 'gold', 'shared_memory_ids': ['SI001']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O017', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SI019']}, {'overlay_memory_id': 'O019', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SI017']}]} |
| LTW2-005 | [] | [{'overlay_memory_id': 'O021', 'overlay_role': 'gold', 'shared_memory_ids': ['SR005']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O022', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR013']}, {'overlay_memory_id': 'O025', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR029']}]} |
| LTW2-006 | [{'overlay_memory_id': 'O026', 'overlay_role': 'gold', 'shared_memory_ids': ['SR018']}] | [{'overlay_memory_id': 'O026', 'overlay_role': 'gold', 'shared_memory_ids': ['SR018']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O029', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR002']}]} |
| LTW2-007 | [] | [{'overlay_memory_id': 'O031', 'overlay_role': 'gold', 'shared_memory_ids': ['SP004']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O032', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SP017']}, {'overlay_memory_id': 'O034', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP010']}]} |
| LTW2-008 | [] | [{'overlay_memory_id': 'O036', 'overlay_role': 'gold', 'shared_memory_ids': ['SR002']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O037', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR005']}, {'overlay_memory_id': 'O038', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR013']}, {'overlay_memory_id': 'O040', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR014']}]} |
| LTW2-009 | [{'overlay_memory_id': 'O041', 'overlay_role': 'gold', 'shared_memory_ids': ['SP011']}] | [{'overlay_memory_id': 'O041', 'overlay_role': 'gold', 'shared_memory_ids': ['SP011']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O042', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SP003']}, {'overlay_memory_id': 'O043', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP005']}, {'overlay_memory_id': 'O044', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP012']}, {'overlay_memory_id': 'O045', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP018']}]} |
| LTW2-010 | [{'overlay_memory_id': 'O046', 'overlay_role': 'gold', 'shared_memory_ids': ['SR020']}] | [{'overlay_memory_id': 'O046', 'overlay_role': 'gold', 'shared_memory_ids': ['SR020']}] | {'exact': [{'overlay_memory_id': 'O047', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR019']}], 'tag': [{'overlay_memory_id': 'O047', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR019']}, {'overlay_memory_id': 'O049', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR018']}, {'overlay_memory_id': 'O050', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR009']}]} |
| LTW2-011 | [] | [] | {'exact': [{'overlay_memory_id': 'O054', 'overlay_role': 'event_distractor', 'shared_memory_ids': ['SE016']}], 'tag': [{'overlay_memory_id': 'O055', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR028']}]} |
| LTW2-012 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O057', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR019']}, {'overlay_memory_id': 'O059', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR008']}]} |
| LTW2-013 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O062', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP013']}, {'overlay_memory_id': 'O063', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SP018']}]} |
| LTW2-016 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O078', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP015']}]} |
| LTW2-017 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O082', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR004']}, {'overlay_memory_id': 'O085', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR016']}]} |
| LTW2-018 | [] | [{'overlay_memory_id': 'O086', 'overlay_role': 'gold', 'shared_memory_ids': ['SP001']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O088', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP004']}, {'overlay_memory_id': 'O089', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SP024']}]} |
| LTW2-019 | [] | [] | {'exact': [{'overlay_memory_id': 'O092', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SE016']}], 'tag': [{'overlay_memory_id': 'O093', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR008']}, {'overlay_memory_id': 'O094', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR009']}, {'overlay_memory_id': 'O095', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR016']}]} |
| LTW2-020 | [] | [{'overlay_memory_id': 'O096', 'overlay_role': 'gold', 'shared_memory_ids': ['SI001']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O097', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SI019']}, {'overlay_memory_id': 'O099', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SI017']}]} |
| LTW2-021 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O101', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR018']}, {'overlay_memory_id': 'O104', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR008']}]} |
| LTW2-022 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O106', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP002']}, {'overlay_memory_id': 'O107', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP008']}, {'overlay_memory_id': 'O109', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP007']}, {'overlay_memory_id': 'O110', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP014']}]} |
| LTW2-023 | [] | [] | {'exact': [{'overlay_memory_id': 'O112', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SE030']}, {'overlay_memory_id': 'O113', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SI023']}], 'tag': [{'overlay_memory_id': 'O111', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR009']}, {'overlay_memory_id': 'O114', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR004']}]} |
| LTW2-024 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O116', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP017']}, {'overlay_memory_id': 'O117', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP004']}, {'overlay_memory_id': 'O120', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP009']}]} |
| LTW2-025 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O122', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP020']}, {'overlay_memory_id': 'O123', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR001']}, {'overlay_memory_id': 'O124', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP021']}]} |
| LTW2-026 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O129', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR008']}]} |
| LTW2-027 | [] | [] | {'exact': [{'overlay_memory_id': 'O133', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SE008']}], 'tag': [{'overlay_memory_id': 'O134', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SP001']}]} |
| LTW2-028 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O137', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SP016']}]} |
| LTW2-029 | [{'overlay_memory_id': 'O141', 'overlay_role': 'gold', 'shared_memory_ids': ['SE005']}] | [] | {'exact': [{'overlay_memory_id': 'O143', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SI011']}], 'tag': [{'overlay_memory_id': 'O142', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR022']}, {'overlay_memory_id': 'O145', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR001']}]} |
| LTW2-030 | [] | [] | {'exact': [{'overlay_memory_id': 'O148', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SE007']}], 'tag': [{'overlay_memory_id': 'O147', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR011']}]} |
| LTW2-031 | [{'overlay_memory_id': 'O151', 'overlay_role': 'gold', 'shared_memory_ids': ['SR008']}] | [{'overlay_memory_id': 'O151', 'overlay_role': 'gold', 'shared_memory_ids': ['SR008']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O152', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR001']}]} |
| LTW2-032 | [{'overlay_memory_id': 'O156', 'overlay_role': 'gold', 'shared_memory_ids': ['SR003']}] | [{'overlay_memory_id': 'O156', 'overlay_role': 'gold', 'shared_memory_ids': ['SR003']}] | {'exact': [{'overlay_memory_id': 'O158', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SE016']}], 'tag': [{'overlay_memory_id': 'O157', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR028']}, {'overlay_memory_id': 'O159', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR008']}]} |
| LTW2-033 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O164', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR018']}, {'overlay_memory_id': 'O165', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR014']}]} |
| LTW2-034 | [] | [{'overlay_memory_id': 'O166', 'overlay_role': 'gold', 'shared_memory_ids': ['SR017']}] | {'exact': [{'overlay_memory_id': 'O168', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SI002']}], 'tag': [{'overlay_memory_id': 'O169', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR004']}, {'overlay_memory_id': 'O170', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR025']}]} |
| LTW2-035 | [{'overlay_memory_id': 'O171', 'overlay_role': 'gold', 'shared_memory_ids': ['SR007']}] | [{'overlay_memory_id': 'O171', 'overlay_role': 'gold', 'shared_memory_ids': ['SR007']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O173', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR027']}]} |
| LTW2-036 | [] | [{'overlay_memory_id': 'O177', 'overlay_role': 'gold', 'shared_memory_ids': ['SR022']}] | {'exact': [], 'tag': []} |
| LTW2-037 | [{'overlay_memory_id': 'O181', 'overlay_role': 'gold', 'shared_memory_ids': ['SP011']}, {'overlay_memory_id': 'O182', 'overlay_role': 'gold', 'shared_memory_ids': ['SP003']}] | [{'overlay_memory_id': 'O181', 'overlay_role': 'gold', 'shared_memory_ids': ['SP011']}, {'overlay_memory_id': 'O182', 'overlay_role': 'gold', 'shared_memory_ids': ['SP003']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O183', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SP005']}, {'overlay_memory_id': 'O184', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP012']}, {'overlay_memory_id': 'O185', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP004']}]} |
| LTW2-038 | [{'overlay_memory_id': 'O186', 'overlay_role': 'gold', 'shared_memory_ids': ['SR008']}] | [{'overlay_memory_id': 'O186', 'overlay_role': 'gold', 'shared_memory_ids': ['SR008']}, {'overlay_memory_id': 'O187', 'overlay_role': 'gold', 'shared_memory_ids': ['SR003']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O188', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR028']}, {'overlay_memory_id': 'O189', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR010']}]} |
| LTW2-039 | [{'overlay_memory_id': 'O191', 'overlay_role': 'gold', 'shared_memory_ids': ['SI004']}, {'overlay_memory_id': 'O192', 'overlay_role': 'gold', 'shared_memory_ids': ['SI022']}] | [] | {'exact': [], 'tag': []} |
| LTW2-040 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O198', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR008']}, {'overlay_memory_id': 'O200', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR016']}]} |

## Oracle vs Retrieved Relation

| Metric | Oracle candidates | Retrieved Top-5 |
|---|---:|---:|
| `relation_accuracy` | 0.7250 | 0.6750 |
| `macro_f1` | 0.6502 | 0.5760 |
| `update_precision` | 0.8571 | 1.0000 |
| `update_recall` | 0.8571 | 0.8571 |
| `target_exact_match` | 0.8000 | 0.2750 |
| `target_set_accuracy` | 0.8000 | 0.2750 |
| `target_memory_accuracy` | 0.8000 | 0.2750 |
| `target_micro_precision` | 0.8947 | 0.4500 |
| `target_memory_precision` | 0.8947 | 0.4500 |
| `target_micro_recall` | 0.8500 | 0.6750 |
| `target_micro_f1` | 0.8718 | 0.5400 |
| `judge_failure_count` | 0 | 0 |

## Retrieved Relation PRF

| Relation | Support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| `same` | 5 | 0.4545 | 1.0000 | 0.6250 |
| `complementary` | 17 | 0.8462 | 0.6471 | 0.7334 |
| `update` | 7 | 1.0000 | 0.8571 | 0.9231 |
| `contradiction` | 5 | 0.0000 | 0.0000 | 0.0000 |
| `unrelated` | 5 | 1.0000 | 0.8000 | 0.8889 |
| `uncertain` | 1 | 0.1667 | 1.0000 | 0.2858 |

## Review-excluded Metrics

- Cases included: `20`
- Evaluated rows included (including repeats): `20`
- Excluded Gold-collision cases: `['LTW2-002', 'LTW2-003', 'LTW2-004', 'LTW2-005', 'LTW2-006', 'LTW2-007', 'LTW2-008', 'LTW2-009', 'LTW2-010', 'LTW2-018', 'LTW2-020', 'LTW2-029', 'LTW2-031', 'LTW2-032', 'LTW2-034', 'LTW2-035', 'LTW2-036', 'LTW2-037', 'LTW2-038', 'LTW2-039']`

| Metric | Value |
|---|---:|
| `retrieval_recall_at_20` | 1.0000 |
| `gold_retention_at_5` | 0.9375 |
| `relation_accuracy` | 0.7500 |
| `target_set_accuracy` | 0.5500 |
| `destructive_safety_violation_count` | 0 |
| `proposal_plus_write_safety_diagnostic_count` | 5 |

## Write and Store

| Metric | Value |
|---|---:|
| `evaluated_count` | 40 |
| `store_action_accuracy` | 0.7750 |
| `new_row_decision_accuracy` | 0.9000 |
| `final_status_accuracy` | 0.9000 |
| `supersede_exact_match_accuracy` | 0.8750 |
| `preserve_exact_match_accuracy` | 1.0000 |
| `store_application_error_count` | 0 |
| `transition_audit_count` | 40 |
| `validator_allow_count` | 13 |
| `validator_deny_count` | 27 |

## Policy Boundaries

| Boundary | Value |
|---|---:|
| `multi_target_proposal_count` | 28 |
| `multi_target_validator_denied_count` | 28 |
| `destructive_multi_target_write_count` | 0 |
| `multi_target_status` | UNSUPPORTED_FAIL_CLOSED |
| `action_intent_update` | {'proposal_count': 1, 'destructive_role_denied_count': 1, 'status': 'VALIDATOR_POLICY_BOUNDARY'} |

## Safety

| Metric | Value |
|---|---:|
| `false_supersede_count` | 0 |
| `false_merge_count` | 0 |
| `false_link_count` | 14 |
| `cross_subject_false_link_count` | 1 |
| `event_false_dedupe_count` | 0 |
| `event_false_supersede_count` | 0 |
| `event_to_pattern_false_update_count` | 0 |
| `custom_to_canonical_false_supersede_count` | 0 |
| `proposed_overwrites_confirmed_count` | 0 |
| `uncertain_destructive_update_count` | 0 |
| `non_target_supersede_count` | 0 |
| `historical_event_not_preserved_count` | 0 |
| `proposal_safety_violation_count` | 14 |
| `validator_blocked_false_link_count` | 13 |
| `validator_allowed_false_link_count` | 1 |
| `actual_false_link_write_count` | 0 |
| `actual_destructive_write_count` | 0 |
| `actual_destructive_write_violation_count` | 0 |
| `destructive_safety_violation_count` | 0 |
| `proposal_plus_write_safety_diagnostic_count` | 14 |
| `false_destructive_update_count` | 0 |
| `false_destructive_update_rate` | 0.0000 |
| `proposal_safety_violation_rate` | 0.3500 |
| `actual_destructive_write_violation_rate` | 0.0000 |
| `historical_event_preservation_rate` | 1.0000 |

`destructive_safety_violation_count` counts only applied destructive Store violations. `proposal_safety_violation_count` and `proposal_plus_write_safety_diagnostic_count` describe blocked or proposed-link diagnostics separately.

## Safety Coverage

| Invariant | Status | Evidence / limitation |
|---|---|---|
| `custom_to_canonical_false_supersede` | `NOT_TESTED` | All fixture candidates use CUSTOM; canonical transition safety is vacuous. |
| `temporal_evidence` | `NOT_TESTED` | Draft fixture has no typed temporal fields; no synthetic evidence is added. |
| `store_seed_identity` | `AUXILIARY_ONLY` | Observed Store-side identity collapses are diagnostic only; loader collision audit is the dataset source of truth. |

## Model and Evaluation Telemetry

| Metric | Value |
|---|---:|
| `embedding_model` | AI-ModelScope/bge-small-zh-v1.5 |
| `embedding_model_version` | unknown |
| `embedding_dimension` | 512 |
| `embedding_document_call_count` | 40 |
| `embedding_query_call_count` | 40 |
| `embedding_failure_count` | 0 |
| `embedding_document_latency_p50_ms` | 523.3380 |
| `embedding_document_latency_p95_ms` | 782.1340 |
| `embedding_query_latency_p50_ms` | 17.9150 |
| `embedding_query_latency_p95_ms` | 24.2630 |
| `embedding_query_latency_total_ms` | 737.0560 |
| `judge_models` | ['deepseek-v4-flash'] |
| `judge_call_count` | 80 |
| `judge_evaluated_count` | 80 |
| `judge_failure_count` | 0 |
| `judge_transport_failure_count` | 0 |
| `judge_parse_failure_count` | 0 |
| `judge_relation_mismatch_count` | 24 |
| `judge_target_mismatch_count` | 37 |
| `judge_target_candidate_unavailable_count` | 3 |
| `judge_target_gold_available_mismatch_count` | 34 |
| `judge_unexpected_target_count` | 26 |
| `judge_latency_p50_ms` | 1227.4880 |
| `judge_latency_p95_ms` | 1745.5610 |
| `judge_prompt_tokens` | 76537 |
| `judge_completion_tokens` | 6949 |
| `judge_total_tokens` | 83486 |
| `judge_avg_prompt_tokens` | 956.7125 |
| `judge_avg_completion_tokens` | 86.8625 |
| `judge_avg_total_tokens` | 1043.5750 |
| `estimated_cost_per_100_writes` | - |

## Failure Attribution

Counts below are evaluated-row counts; in repeat mode one case may contribute more than one row.

| Primary stage | Count |
|---|---:|
| `DATASET_COLLISION` | 15 |
| `RANKING_DROP` | 3 |
| `SAFETY_DOWNGRADE` | 5 |
| `TARGET_SELECTION_ERROR` | 11 |

## Failed Cases

| Run | Case | Slice | Primary | Secondary | Expected | Actual | Targets | Action |
|---:|---|---|---|---|---|---|---|---|
| - | LTW2-002 | same_semantic_rephrase | DATASET_COLLISION | ['TARGET_SELECTION_ERROR', 'WRITE_POLICY_ERROR'] | same | same | ['SP007', 'O006'] | add_without_supersede |
| - | LTW2-003 | same_semantic_rephrase | DATASET_COLLISION | ['TARGET_SELECTION_ERROR', 'WRITE_POLICY_ERROR'] | same | same | ['SR001', 'O011'] | add_without_supersede |
| - | LTW2-004 | same_semantic_rephrase | DATASET_COLLISION | ['TARGET_SELECTION_ERROR', 'WRITE_POLICY_ERROR'] | same | same | ['O016', 'SI001'] | add_without_supersede |
| - | LTW2-005 | same_semantic_rephrase | DATASET_COLLISION | ['TARGET_SELECTION_ERROR', 'WRITE_POLICY_ERROR'] | same | same | ['SR005', 'O021'] | add_without_supersede |
| - | LTW2-006 | complementary_detail | TARGET_SELECTION_ERROR | - | complementary | complementary | ['O030'] | add_without_supersede |
| - | LTW2-007 | complementary_detail | DATASET_COLLISION | ['TARGET_SELECTION_ERROR'] | complementary | complementary | ['SP004', 'SP009'] | add_without_supersede |
| - | LTW2-008 | complementary_detail | DATASET_COLLISION | ['TARGET_SELECTION_ERROR'] | complementary | same | ['SR002', 'O036'] | add_without_supersede |
| - | LTW2-009 | complementary_detail | DATASET_COLLISION | ['TARGET_SELECTION_ERROR'] | complementary | same | ['O041', 'SP011'] | add_without_supersede |
| - | LTW2-010 | complementary_detail | RANKING_DROP | ['TARGET_SELECTION_ERROR'] | complementary | complementary | ['O048', 'SE019'] | add_without_supersede |
| - | LTW2-011 | sustained_update | SAFETY_DOWNGRADE | ['WRITE_POLICY_ERROR'] | update | update | ['O051'] | add_without_supersede |
| - | LTW2-012 | sustained_update | SAFETY_DOWNGRADE | ['WRITE_POLICY_ERROR'] | update | update | ['O056'] | add_without_supersede |
| - | LTW2-013 | sustained_update | SAFETY_DOWNGRADE | ['WRITE_POLICY_ERROR'] | update | update | ['O061'] | add_without_supersede |
| - | LTW2-014 | sustained_update | SAFETY_DOWNGRADE | ['WRITE_POLICY_ERROR'] | update | update | ['O066'] | add_without_supersede |
| - | LTW2-015 | sustained_update | SAFETY_DOWNGRADE | ['WRITE_POLICY_ERROR'] | update | update | ['O071'] | add_without_supersede |
| - | LTW2-016 | contradiction_authority | TARGET_SELECTION_ERROR | - | contradiction | uncertain | [] | add_without_supersede |
| - | LTW2-017 | contradiction_authority | TARGET_SELECTION_ERROR | - | contradiction | uncertain | [] | add_without_supersede |
| - | LTW2-018 | contradiction_authority | TARGET_SELECTION_ERROR | - | contradiction | uncertain | [] | add_without_supersede |
| - | LTW2-019 | contradiction_authority | TARGET_SELECTION_ERROR | - | contradiction | uncertain | [] | add_without_supersede |
| - | LTW2-020 | contradiction_authority | TARGET_SELECTION_ERROR | - | contradiction | uncertain | [] | add_without_supersede |
| - | LTW2-023 | unrelated_hard_negative | TARGET_SELECTION_ERROR | - | unrelated | complementary | ['O112', 'SE030', 'O113', 'SI023'] | add_without_supersede |
| - | LTW2-026 | temporal_event_identity | TARGET_SELECTION_ERROR | - | complementary | complementary | ['O126', 'O127', 'SE015'] | add_without_supersede |
| - | LTW2-028 | temporal_event_identity | TARGET_SELECTION_ERROR | - | complementary | complementary | ['SE009', 'O136'] | add_without_supersede |
| - | LTW2-029 | temporal_event_identity | DATASET_COLLISION | ['TARGET_SELECTION_ERROR'] | complementary | complementary | ['O141', 'SE005'] | add_without_supersede |
| - | LTW2-030 | temporal_event_identity | TARGET_SELECTION_ERROR | - | complementary | complementary | ['O146', 'SE002'] | add_without_supersede |
| - | LTW2-031 | event_vs_pattern | DATASET_COLLISION | ['TARGET_SELECTION_ERROR'] | complementary | complementary | ['O151', 'SR008'] | add_without_supersede |
| - | LTW2-032 | event_vs_pattern | DATASET_COLLISION | ['TARGET_SELECTION_ERROR'] | complementary | same | ['O156', 'SR003'] | add_without_supersede |
| - | LTW2-033 | event_vs_pattern | TARGET_SELECTION_ERROR | - | complementary | same | ['SE013', 'O162'] | add_without_supersede |
| - | LTW2-034 | event_vs_pattern | RANKING_DROP | ['TARGET_SELECTION_ERROR'] | complementary | complementary | ['O168', 'SI002'] | add_without_supersede |
| - | LTW2-035 | event_vs_pattern | DATASET_COLLISION | ['TARGET_SELECTION_ERROR'] | complementary | complementary | ['O171', 'SR007'] | add_without_supersede |
| - | LTW2-036 | multi_target_ambiguity | DATASET_COLLISION | ['TARGET_SELECTION_ERROR'] | update | update | ['O176', 'O177', 'SR022'] | add_without_supersede |
| - | LTW2-037 | multi_target_ambiguity | DATASET_COLLISION | ['TARGET_SELECTION_ERROR'] | complementary | same | ['O182', 'SP003', 'O181', 'SP011'] | add_without_supersede |
| - | LTW2-038 | multi_target_ambiguity | DATASET_COLLISION | ['TARGET_SELECTION_ERROR'] | update | complementary | ['SR008', 'O186'] | add_without_supersede |
| - | LTW2-039 | multi_target_ambiguity | DATASET_COLLISION | ['TARGET_SELECTION_ERROR'] | complementary | same | ['O192', 'SI022', 'O191', 'SI004'] | add_without_supersede |
| - | LTW2-040 | multi_target_ambiguity | RANKING_DROP | ['TARGET_SELECTION_ERROR'] | uncertain | uncertain | [] | add_without_supersede |

## Fixture vs Live

- Comparison status: `COMPARABLE`
- Methodology: fixture uses reviewed labels with deterministic benchmark retrieval; live uses the production HybridMemoryRetriever, embedding, and semantic Judge. The comparison is diagnostic, not a production quality claim.
- Fixture scope: Top-20 → Top-5
- Live scope: Top-20 → Top-5

| Metric | Fixture | Live |
|---|---:|---:|
| `retrieval_hit_at_1` | 0.3714 | 0.5714 |
| `retrieval_hit_at_3` | 0.4571 | 0.8857 |
| `retrieval_hit_at_5` | 0.4857 | 1.0000 |
| `retrieval_recall_at_5` | 0.5000 | 0.9750 |
| `retrieval_recall_at_20` | 0.7750 | 1.0000 |
| `gold_retention_at_5` | 0.8387 | 0.9250 |
| `oracle_relation_accuracy` | 1.0000 | 0.7250 |
| `retrieved_relation_accuracy` | 1.0000 | 0.6750 |
| `oracle_update_precision` | 1.0000 | 0.8571 |
| `retrieved_update_precision` | 1.0000 | 1.0000 |
| `oracle_target_set_accuracy` | 1.0000 | 0.8000 |
| `retrieved_target_set_accuracy` | 0.6750 | 0.2750 |
| `store_action_accuracy` | 0.8250 | 0.7750 |
| `destructive_safety_violation_count` | 0 | 0 |
| `proposal_safety_violation_count` | 0 | 14 |
| `actual_destructive_write_violation_count` | 0 | 0 |
| `actual_destructive_write_count` | 0 | 0 |
| `proposal_plus_write_safety_diagnostic_count` | 0 | 14 |

## Governance Notes

Vector similarity only recalls candidates; it never authorizes a write. 
The Semantic Judge proposes, the production validator authorizes, and only 
a case-local InMemoryStore receives a batch. Multi-target destructive writes 
remain unsupported and fail closed. The production validator may also deny 
action_intent UPDATE proposals because that role is outside its destructive 
role policy. Benchmark semantic tags are used only for collision and 
hard-negative diagnostics, never ranking or Judge input. Store seed identity 
collapse counts are auxiliary; the loader collision audit is authoritative.
 Raw false-link proposals are reported separately from actual destructive 
writes; a validator-denied proposal does not count as an applied Store safety 
violation. Proposal-level safety issues are reported separately from applied destructive violations.

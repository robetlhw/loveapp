# Memory Long-tail Write V2 Final Live Validation

- Version: `memory-longtail-write-v2-final-live-v1`
- Cases: `40`
- Passed: `19`
- Evaluated rows (including repeats): `40`
- Evaluation status: **V2_STAGE_GOALS_MET**
- Safety status (applied destructive writes): **PASS**
- Evaluation mode: `shadow_live`
- Dataset status: **PASS**
- Repeat runs: `1`
- Hard-case mode: `False`
- Production Store mutation permitted: `False`
- Isolated InMemoryStore mutation permitted: `True`
- Repository: `-`
- Branch / commit: `-` / `-`
- Working tree: `-` (- changes)

## Repeat / Hard-case Diagnostics

- Filter status: `NOT_REQUESTED`
- Matched IDs: `[]`
- Missing IDs: `[]`
- Relation consistency: `-`
- Target consistency: `-`
- Validator consistency: `-`
- Retrieval Top-5 order consistency: `-`
- Fixture repeat scope: `-`
- Live repeat scope: `-`
- Consistency rates are per-case mode rates across repeated runs; `1.0` means no drift.

### Per-case consistency

| Case | Relation | Target | Validator | Top-5 order | Target drift attribution |
|---|---:|---:|---:|---:|---|
| none | - | - | - | - | - |

## Dataset Contract

- Shared bank: `120` memories
- Overlay: `200` memories
- Candidate pools: `all shared memories + overlay`, actual sizes `{125: 40}`
- Candidate contract: **FIXED** at `120` shared + `5` overlay (`125` candidates per case); `shared_pools` is descriptive only.
- Gold collision review cases: `0` []
- Collision audit: exact shared/overlay cases `18`; equivalent exact cases `18`; unresolved exact cases `0`; Gold exact `11`; semantic-tag cases `37`; Gold tag `18`.
- Non-Gold collision cases remain diagnostic only: `['LTW2-001', 'LTW2-002', 'LTW2-003', 'LTW2-004', 'LTW2-005', 'LTW2-006', 'LTW2-007', 'LTW2-008', 'LTW2-009', 'LTW2-010', 'LTW2-011', 'LTW2-012', 'LTW2-013', 'LTW2-016', 'LTW2-017', 'LTW2-018', 'LTW2-019', 'LTW2-020', 'LTW2-021', 'LTW2-022', 'LTW2-023', 'LTW2-024', 'LTW2-025', 'LTW2-026', 'LTW2-027', 'LTW2-028', 'LTW2-029', 'LTW2-030', 'LTW2-031', 'LTW2-032', 'LTW2-033', 'LTW2-034', 'LTW2-035', 'LTW2-036', 'LTW2-037', 'LTW2-038', 'LTW2-039', 'LTW2-040']`
- Semantic target contract: retrieval-reference-only cases `['LTW2-040']`; their non-target-bearing relation proposals must use an empty target set.

## Retrieval and Ranking

- Vector retrieval stage: **Top-20** candidates
- Cheap ranking stage: vector candidates are reranked before documented equivalence groups are collapsed
- Semantic Judge stage: **Top-5** collapsed semantic candidates
- Retrieval engine: `['HybridMemoryRetriever']`
- Embedding input: `production_retrieval_text_composite`
- Embedding input detail: HybridMemoryRetriever._retrieval_text: summary + original text + canonical/state fields + selected payload + evidence spans
- Judge query input: `incoming summary + evidence spans`

| Metric | Value |
|---|---:|

### Per-run summary

| Run | Cases | Passed | Failed |
|---:|---:|---:|---:|
| 1 | 40 | 19 | 21 |
| `retrieval_expected_case_count` | 35 |
| `retrieval_expected_target_count` | 40 |
| `retrieval_hit_at_1` | 0.4571 |
| `retrieval_hit_at_3` | 0.8857 |
| `retrieval_hit_at_5` | 0.9714 |
| `retrieval_hit_at_10` | 1.0000 |
| `retrieval_hit_at_20` | 1.0000 |
| `retrieval_recall_at_5` | 0.9250 |
| `retrieval_recall_at_10` | 0.9750 |
| `retrieval_recall_at_20` | 1.0000 |
| `raw_retrieval_recall_at_5` | 0.9250 |
| `raw_retrieval_recall_at_10` | 0.9750 |
| `raw_retrieval_recall_at_20` | 1.0000 |
| `equivalence_aware_recall_at_5` | 0.9250 |
| `equivalence_aware_recall_at_10` | 0.9750 |
| `equivalence_aware_recall_at_20` | 1.0000 |
| `mrr` | 0.6865 |
| `conditional_gold_retention_at_5` | 0.9000 |
| `gold_retention_at_5` | 0.9000 |
| `end_to_end_gold_recall_at_5` | 0.9000 |
| `target_set_recall_at_5` | 0.9000 |
| `gold_target_set_exact_at_5` | 0.8857 |
| `hard_negative_promotion_count` | 7 |
| `hard_negative_promotion_rate` | 0.2000 |
| `unrelated_candidate_vector_count` | 736 |
| `unrelated_candidate_ranked_count` | 164 |
| `unrelated_candidate_retention_rate` | 0.2228 |
| `equivalence_group_duplicate_slot_count_at_20` | 24 |
| `equivalence_group_duplicate_slot_count_at_5` | 0 |
| `pre_collapse_candidate_count` | 800 |
| `post_collapse_candidate_count` | 776 |
| `equivalence_groups_collapsed` | 24 |
| `duplicate_slots_removed` | 24 |
| `top5_duplicate_semantic_memory_count` | 0 |
| `avg_candidate_count` | 125 |
| `retrieval_latency_p50_ms` | 1009.3980 |
| `retrieval_latency_p95_ms` | 1060.0910 |
| `cheap_ranking_latency_p50_ms` | 0.0120 |
| `cheap_ranking_latency_p95_ms` | 0.0140 |
| `vector_ranking_latency_p50_ms` | 0.0810 |
| `vector_ranking_latency_p95_ms` | 0.0960 |

Metric definitions: `raw_retrieval_recall_at_20` counts exact physical Gold IDs in vector Top-20. `equivalence_aware_recall_at_20` counts a case-local documented equivalence group as one semantic hit. `conditional_gold_retention_at_5` is ranked Top-5 Gold hits divided by Gold hits already present in vector Top-20. `end_to_end_gold_recall_at_5` is ranked Top-5 Gold hits divided by all expected retrieval references.

## V2 Acceptance Checks

| Check | Result |
|---|---|
| `retrieval_recall_at_20` | PASS |
| `relation_accuracy` | PASS |
| `relation_macro_f1` | PASS |
| `target_set_accuracy` | PASS |
| `target_micro_f1` | PASS |
| `destructive_safety` | PASS |

## Collision Details

| Case | Gold exact | Gold tag | Other exact/tag overlap |
|---|---|---|---|
| LTW2-001 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O002', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SP028']}]} |
| LTW2-002 | [] | [{'overlay_memory_id': 'O006', 'overlay_role': 'gold', 'shared_memory_ids': ['SP007']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O007', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP008']}, {'overlay_memory_id': 'O009', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP002']}]} |
| LTW2-003 | [] | [{'overlay_memory_id': 'O011', 'overlay_role': 'gold', 'shared_memory_ids': ['SR001']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O012', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR009']}, {'overlay_memory_id': 'O013', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR008']}, {'overlay_memory_id': 'O015', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR004']}]} |
| LTW2-004 | [{'overlay_memory_id': 'O016', 'overlay_role': 'gold', 'shared_memory_ids': ['SI001'], 'equivalent_memory_group_id': 'EQ-O016', 'equivalent_documented': True}] | [{'overlay_memory_id': 'O016', 'overlay_role': 'gold', 'shared_memory_ids': ['SI001']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O017', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SI019']}, {'overlay_memory_id': 'O019', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SI017']}]} |
| LTW2-005 | [] | [{'overlay_memory_id': 'O021', 'overlay_role': 'gold', 'shared_memory_ids': ['SR005']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O022', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR013']}, {'overlay_memory_id': 'O025', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR029']}]} |
| LTW2-006 | [{'overlay_memory_id': 'O026', 'overlay_role': 'gold', 'shared_memory_ids': ['SR018'], 'equivalent_memory_group_id': 'EQ-O026', 'equivalent_documented': True}] | [{'overlay_memory_id': 'O026', 'overlay_role': 'gold', 'shared_memory_ids': ['SR018']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O028', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SP029']}, {'overlay_memory_id': 'O029', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR002']}]} |
| LTW2-007 | [] | [{'overlay_memory_id': 'O031', 'overlay_role': 'gold', 'shared_memory_ids': ['SP004']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O032', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SP017']}, {'overlay_memory_id': 'O034', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP010']}, {'overlay_memory_id': 'O035', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SI007']}]} |
| LTW2-008 | [] | [{'overlay_memory_id': 'O036', 'overlay_role': 'gold', 'shared_memory_ids': ['SR002']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O037', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR005']}, {'overlay_memory_id': 'O038', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR013']}, {'overlay_memory_id': 'O040', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR014']}]} |
| LTW2-009 | [{'overlay_memory_id': 'O041', 'overlay_role': 'gold', 'shared_memory_ids': ['SP011'], 'equivalent_memory_group_id': 'EQ-O041', 'equivalent_documented': True}] | [{'overlay_memory_id': 'O041', 'overlay_role': 'gold', 'shared_memory_ids': ['SP011']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O042', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SP003']}, {'overlay_memory_id': 'O043', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP005']}, {'overlay_memory_id': 'O044', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP012']}, {'overlay_memory_id': 'O045', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP018']}]} |
| LTW2-010 | [{'overlay_memory_id': 'O046', 'overlay_role': 'gold', 'shared_memory_ids': ['SR020'], 'equivalent_memory_group_id': 'EQ-O046', 'equivalent_documented': True}] | [{'overlay_memory_id': 'O046', 'overlay_role': 'gold', 'shared_memory_ids': ['SR020']}] | {'exact': [{'overlay_memory_id': 'O047', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR019'], 'equivalent_memory_group_id': 'EQ-O047', 'equivalent_documented': True}], 'tag': [{'overlay_memory_id': 'O047', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR019']}, {'overlay_memory_id': 'O049', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR018']}, {'overlay_memory_id': 'O050', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR009']}]} |
| LTW2-011 | [] | [] | {'exact': [{'overlay_memory_id': 'O054', 'overlay_role': 'event_distractor', 'shared_memory_ids': ['SE016'], 'equivalent_memory_group_id': 'EQ-O054', 'equivalent_documented': True}], 'tag': [{'overlay_memory_id': 'O055', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR028']}]} |
| LTW2-012 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O057', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR019']}, {'overlay_memory_id': 'O059', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR008']}]} |
| LTW2-013 | [] | [] | {'exact': [{'overlay_memory_id': 'O064', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SE007'], 'equivalent_memory_group_id': 'EQ-O064', 'equivalent_documented': True}], 'tag': [{'overlay_memory_id': 'O062', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP013']}, {'overlay_memory_id': 'O063', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SP018']}]} |
| LTW2-016 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O078', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP015']}, {'overlay_memory_id': 'O079', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR023']}]} |
| LTW2-017 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O082', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR004']}, {'overlay_memory_id': 'O084', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP025']}, {'overlay_memory_id': 'O085', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR016']}]} |
| LTW2-018 | [] | [{'overlay_memory_id': 'O086', 'overlay_role': 'gold', 'shared_memory_ids': ['SP001']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O088', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP004']}, {'overlay_memory_id': 'O089', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SP024']}, {'overlay_memory_id': 'O090', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR023']}]} |
| LTW2-019 | [] | [] | {'exact': [{'overlay_memory_id': 'O092', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SE016'], 'equivalent_memory_group_id': 'EQ-O054', 'equivalent_documented': True}], 'tag': [{'overlay_memory_id': 'O093', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR008']}, {'overlay_memory_id': 'O094', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR009']}, {'overlay_memory_id': 'O095', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR016']}]} |
| LTW2-020 | [] | [{'overlay_memory_id': 'O096', 'overlay_role': 'gold', 'shared_memory_ids': ['SI001']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O097', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SI019']}, {'overlay_memory_id': 'O099', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SI017']}]} |
| LTW2-021 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O101', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR018']}, {'overlay_memory_id': 'O104', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR008']}, {'overlay_memory_id': 'O105', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP017']}]} |
| LTW2-022 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O106', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP002']}, {'overlay_memory_id': 'O107', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP008']}, {'overlay_memory_id': 'O109', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP007']}, {'overlay_memory_id': 'O110', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP014']}]} |
| LTW2-023 | [] | [] | {'exact': [{'overlay_memory_id': 'O112', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SE030'], 'equivalent_memory_group_id': 'EQ-O112', 'equivalent_documented': True}, {'overlay_memory_id': 'O113', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SI023'], 'equivalent_memory_group_id': 'EQ-O113', 'equivalent_documented': True}], 'tag': [{'overlay_memory_id': 'O111', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR009']}, {'overlay_memory_id': 'O114', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR004']}, {'overlay_memory_id': 'O115', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP024']}]} |
| LTW2-024 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O116', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP017']}, {'overlay_memory_id': 'O117', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP004']}, {'overlay_memory_id': 'O118', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SI007']}, {'overlay_memory_id': 'O120', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP009']}]} |
| LTW2-025 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O122', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP020']}, {'overlay_memory_id': 'O123', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR001']}, {'overlay_memory_id': 'O124', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP021']}]} |
| LTW2-026 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O129', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR008']}]} |
| LTW2-027 | [] | [] | {'exact': [{'overlay_memory_id': 'O133', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SE008'], 'equivalent_memory_group_id': 'EQ-O133', 'equivalent_documented': True}], 'tag': [{'overlay_memory_id': 'O134', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SP001']}]} |
| LTW2-028 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O137', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SP016']}, {'overlay_memory_id': 'O138', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR003']}]} |
| LTW2-029 | [{'overlay_memory_id': 'O141', 'overlay_role': 'gold', 'shared_memory_ids': ['SE005'], 'equivalent_memory_group_id': 'EQ-O141', 'equivalent_documented': True}] | [] | {'exact': [{'overlay_memory_id': 'O143', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SI011'], 'equivalent_memory_group_id': 'EQ-O143', 'equivalent_documented': True}], 'tag': [{'overlay_memory_id': 'O142', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR022']}, {'overlay_memory_id': 'O145', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR001']}]} |
| LTW2-030 | [] | [] | {'exact': [{'overlay_memory_id': 'O148', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SE007'], 'equivalent_memory_group_id': 'EQ-O064', 'equivalent_documented': True}], 'tag': [{'overlay_memory_id': 'O147', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR011']}, {'overlay_memory_id': 'O149', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP003']}]} |
| LTW2-031 | [{'overlay_memory_id': 'O151', 'overlay_role': 'gold', 'shared_memory_ids': ['SR008'], 'equivalent_memory_group_id': 'EQ-O151', 'equivalent_documented': True}] | [{'overlay_memory_id': 'O151', 'overlay_role': 'gold', 'shared_memory_ids': ['SR008']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O152', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR001']}]} |
| LTW2-032 | [{'overlay_memory_id': 'O156', 'overlay_role': 'gold', 'shared_memory_ids': ['SR003'], 'equivalent_memory_group_id': 'EQ-O156', 'equivalent_documented': True}] | [{'overlay_memory_id': 'O156', 'overlay_role': 'gold', 'shared_memory_ids': ['SR003']}] | {'exact': [{'overlay_memory_id': 'O158', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SE016'], 'equivalent_memory_group_id': 'EQ-O054', 'equivalent_documented': True}], 'tag': [{'overlay_memory_id': 'O157', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR028']}, {'overlay_memory_id': 'O159', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR008']}]} |
| LTW2-033 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O164', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR018']}, {'overlay_memory_id': 'O165', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR014']}]} |
| LTW2-034 | [] | [{'overlay_memory_id': 'O166', 'overlay_role': 'gold', 'shared_memory_ids': ['SR017']}] | {'exact': [{'overlay_memory_id': 'O168', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SI002'], 'equivalent_memory_group_id': 'EQ-O168', 'equivalent_documented': True}], 'tag': [{'overlay_memory_id': 'O169', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR004']}, {'overlay_memory_id': 'O170', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR025']}]} |
| LTW2-035 | [{'overlay_memory_id': 'O171', 'overlay_role': 'gold', 'shared_memory_ids': ['SR007'], 'equivalent_memory_group_id': 'EQ-O171', 'equivalent_documented': True}] | [{'overlay_memory_id': 'O171', 'overlay_role': 'gold', 'shared_memory_ids': ['SR007']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O173', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SR027']}]} |
| LTW2-036 | [] | [{'overlay_memory_id': 'O177', 'overlay_role': 'gold', 'shared_memory_ids': ['SR022']}] | {'exact': [], 'tag': []} |
| LTW2-037 | [{'overlay_memory_id': 'O181', 'overlay_role': 'gold', 'shared_memory_ids': ['SP011'], 'equivalent_memory_group_id': 'EQ-O041', 'equivalent_documented': True}, {'overlay_memory_id': 'O182', 'overlay_role': 'gold', 'shared_memory_ids': ['SP003'], 'equivalent_memory_group_id': 'EQ-O182', 'equivalent_documented': True}] | [{'overlay_memory_id': 'O181', 'overlay_role': 'gold', 'shared_memory_ids': ['SP011']}, {'overlay_memory_id': 'O182', 'overlay_role': 'gold', 'shared_memory_ids': ['SP003']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O183', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SP005']}, {'overlay_memory_id': 'O184', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP012']}, {'overlay_memory_id': 'O185', 'overlay_role': 'hard_negative', 'shared_memory_ids': ['SP004']}]} |
| LTW2-038 | [{'overlay_memory_id': 'O186', 'overlay_role': 'gold', 'shared_memory_ids': ['SR008'], 'equivalent_memory_group_id': 'EQ-O151', 'equivalent_documented': True}] | [{'overlay_memory_id': 'O186', 'overlay_role': 'gold', 'shared_memory_ids': ['SR008']}, {'overlay_memory_id': 'O187', 'overlay_role': 'gold', 'shared_memory_ids': ['SR003']}] | {'exact': [], 'tag': [{'overlay_memory_id': 'O188', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR028']}, {'overlay_memory_id': 'O189', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR010']}]} |
| LTW2-039 | [{'overlay_memory_id': 'O191', 'overlay_role': 'gold', 'shared_memory_ids': ['SI004'], 'equivalent_memory_group_id': 'EQ-O191', 'equivalent_documented': True}, {'overlay_memory_id': 'O192', 'overlay_role': 'gold', 'shared_memory_ids': ['SI022'], 'equivalent_memory_group_id': 'EQ-O192', 'equivalent_documented': True}] | [] | {'exact': [], 'tag': []} |
| LTW2-040 | [] | [] | {'exact': [], 'tag': [{'overlay_memory_id': 'O198', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR008']}, {'overlay_memory_id': 'O200', 'overlay_role': 'related_distractor', 'shared_memory_ids': ['SR016']}]} |

## Oracle vs Retrieved Relation

| Metric | Oracle candidates | Retrieved Top-5 |
|---|---:|---:|
| `relation_accuracy` | 0.9750 | 0.8750 |
| `macro_f1` | 0.9838 | 0.8249 |
| `update_precision` | 0.8750 | 0.8750 |
| `update_recall` | 1.0000 | 1.0000 |
| `target_exact_match` | 0.9750 | 0.5750 |
| `target_set_accuracy` | 0.9750 | 0.6000 |
| `target_memory_accuracy` | 0.9750 | 0.6000 |
| `target_micro_precision` | 1.0000 | 0.6667 |
| `target_memory_precision` | 1.0000 | 0.6667 |
| `target_micro_recall` | 0.9737 | 0.7895 |
| `target_micro_f1` | 0.9867 | 0.7229 |
| `judge_failure_count` | 0 | 0 |

## Retrieved Relation PRF

| Relation | Support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| `same` | 5 | 1.0000 | 0.4000 | 0.5714 |
| `complementary` | 17 | 0.8421 | 0.9412 | 0.8889 |
| `update` | 7 | 0.8750 | 1.0000 | 0.9333 |
| `contradiction` | 5 | 1.0000 | 0.8000 | 0.8889 |
| `unrelated` | 5 | 1.0000 | 1.0000 | 1.0000 |
| `uncertain` | 1 | 0.5000 | 1.0000 | 0.6667 |

## Contradiction Diagnostics

- Support: `5`
- Precision / Recall / F1: `1.0000` / `0.8000` / `0.8889`

| Case | Incoming | Candidates | Gold | Actual | Confidence | Validator |
|---|---|---|---|---|---:|---|
| LTW2-016 | 我总觉得她可能其实不太想让我融入她朋友圈，只是嘴上不好拒绝。 | ['她之前明确说愿意带我认识她的朋友。'] | contradiction | contradiction | 0.7000 | pass=True, would_update=False |
| LTW2-017 | 我最近有点怀疑她是不是其实还有别的对象，但我没有证据。 | ['她之前明确告诉我自己目前是单身。'] | contradiction | contradiction | 0.9000 | pass=True, would_update=False |
| LTW2-018 | 我感觉她可能其实更喜欢很热闹的地方，因为上次在酒吧她看起来挺开心。 | ['她一直更喜欢安静的小餐馆，不喜欢特别吵的店。'] | contradiction | contradiction | 0.9000 | pass=False, would_update=False |
| LTW2-019 | 我有点觉得她是不是已经不想主动找我了，不过最近样本也不多。 | ['最近两个月她仍然会主动开启不少聊天。'] | contradiction | uncertain | 0.8500 | pass=True, would_update=False |
| LTW2-020 | 我觉得她可能已经不想去国庆后的徒步了，但她没有明确说取消。 | ['我们已经确认国庆以后找一个周末去附近徒步。'] | contradiction | contradiction | 0.8500 | pass=False, would_update=False |

## Review-excluded Metrics

- Cases included: `40`
- Evaluated rows included (including repeats): `40`
- Excluded Gold-collision cases: `[]`

| Metric | Value |
|---|---:|
| `retrieval_recall_at_20` | 1.0000 |
| `gold_retention_at_5` | 0.9000 |
| `relation_accuracy` | 0.8750 |
| `target_set_accuracy` | 0.6000 |
| `destructive_safety_violation_count` | 0 |
| `proposal_plus_write_safety_diagnostic_count` | 14 |

## Write and Store

| Metric | Value |
|---|---:|
| `evaluated_count` | 40 |
| `store_action_accuracy` | 0.8000 |
| `new_row_decision_accuracy` | 0.9250 |
| `final_status_accuracy` | 0.9250 |
| `supersede_exact_match_accuracy` | 0.8750 |
| `preserve_exact_match_accuracy` | 1.0000 |
| `store_application_error_count` | 0 |
| `transition_audit_count` | 40 |
| `validator_allow_count` | 23 |
| `validator_deny_count` | 17 |

## Policy Boundaries

| Boundary | Value |
|---|---:|
| `multi_target_proposal_count` | 13 |
| `retrieved_multi_target_proposal_count` | 10 |
| `expected_multi_target_case_count` | 4 |
| `exact_expected_multi_target_proposal_count` | 1 |
| `overbroad_multi_target_proposal_count` | 9 |
| `policy_boundary_count` | 1 |
| `multi_target_validator_denied_count` | 13 |
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
| `validator_blocked_false_link_count` | 11 |
| `validator_allowed_false_link_count` | 3 |
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
| `embedding_document_failure_count` | 0 |
| `embedding_query_failure_count` | 0 |
| `embedding_document_latency_p50_ms` | 946.5290 |
| `embedding_document_latency_p95_ms` | 993.9480 |
| `embedding_query_latency_p50_ms` | 18.9520 |
| `embedding_query_latency_p95_ms` | 21.1390 |
| `embedding_query_latency_total_ms` | 753.2460 |
| `judge_models` | ['deepseek-v4-flash'] |
| `judge_call_count` | 80 |
| `judge_evaluated_count` | 80 |
| `judge_failure_count` | 0 |
| `judge_transport_failure_count` | 0 |
| `judge_parse_failure_count` | 0 |
| `judge_relation_mismatch_count` | 6 |
| `judge_target_mismatch_count` | 17 |
| `judge_retrieval_reference_unavailable_count` | 4 |
| `judge_target_candidate_unavailable_count` | 3 |
| `judge_target_gold_available_mismatch_count` | 14 |
| `judge_unexpected_target_count` | 14 |
| `target_policy_accepted_count` | 80 |
| `target_policy_fail_closed_count` | 0 |
| `judge_latency_p50_ms` | 1578.0560 |
| `judge_latency_p95_ms` | 2237.8330 |
| `judge_prompt_tokens` | 225147 |
| `judge_completion_tokens` | 20142 |
| `judge_total_tokens` | 245289 |
| `judge_avg_prompt_tokens` | 2814.3375 |
| `judge_avg_completion_tokens` | 251.7750 |
| `judge_avg_total_tokens` | 3066.1125 |
| `oracle_judge` | {'call_count': 40, 'completed_count': 40, 'failure_count': 0, 'relation_mismatch_count': 1, 'target_mismatch_count': 1, 'unexpected_target_count': 0, 'target_candidate_unavailable_count': 0, 'latency_p50_ms': 1382.901, 'latency_p95_ms': 1890.557, 'prompt_tokens': 108679, 'completion_tokens': 8776, 'total_tokens': 117455, 'avg_prompt_tokens': 2716.975, 'avg_completion_tokens': 219.4, 'avg_total_tokens': 2936.375} |
| `retrieved_judge` | {'call_count': 40, 'completed_count': 40, 'failure_count': 0, 'relation_mismatch_count': 5, 'target_mismatch_count': 16, 'unexpected_target_count': 14, 'target_candidate_unavailable_count': 3, 'latency_p50_ms': 1720.558, 'latency_p95_ms': 2300.945, 'prompt_tokens': 116468, 'completion_tokens': 11366, 'total_tokens': 127834, 'avg_prompt_tokens': 2911.7, 'avg_completion_tokens': 284.15, 'avg_total_tokens': 3195.85} |
| `estimated_cost_per_100_writes` | N/A |

## Failure Attribution

Counts below are evaluated-row counts; in repeat mode one case may contribute more than one row.

| Primary stage | Count |
|---|---:|
| `RANKING_DROP` | 4 |
| `SAFETY_DOWNGRADE` | 4 |
| `SEMANTIC_RELATION_ERROR` | 4 |
| `TARGET_SELECTION_ERROR` | 9 |

| Secondary diagnostic | Count |
|---|---:|
| `DIRECT_TARGET_ELIGIBILITY_ERROR` | 16 |
| `RELATION_CLASSIFICATION_ERROR` | 5 |
| `SEMANTIC_RELATION_ERROR` | 1 |
| `TARGET_SELECTION_ERROR` | 7 |
| `WRITE_POLICY_ERROR` | 8 |

## Failed Cases

| Run | Case | Slice | Primary | Secondary | Expected | Actual | Targets | Action |
|---:|---|---|---|---|---|---|---|---|
| - | LTW2-002 | same_semantic_rephrase | SEMANTIC_RELATION_ERROR | ['RELATION_CLASSIFICATION_ERROR', 'DIRECT_TARGET_ELIGIBILITY_ERROR', 'TARGET_SELECTION_ERROR', 'WRITE_POLICY_ERROR'] | same | complementary | ['SP007', 'O006'] | add_without_supersede |
| - | LTW2-003 | same_semantic_rephrase | SEMANTIC_RELATION_ERROR | ['RELATION_CLASSIFICATION_ERROR', 'DIRECT_TARGET_ELIGIBILITY_ERROR', 'TARGET_SELECTION_ERROR', 'WRITE_POLICY_ERROR'] | same | complementary | ['SR001', 'O011'] | add_without_supersede |
| - | LTW2-005 | same_semantic_rephrase | SEMANTIC_RELATION_ERROR | ['RELATION_CLASSIFICATION_ERROR', 'DIRECT_TARGET_ELIGIBILITY_ERROR', 'TARGET_SELECTION_ERROR', 'WRITE_POLICY_ERROR'] | same | complementary | ['SR005', 'O021'] | add_without_supersede |
| - | LTW2-006 | complementary_detail | RANKING_DROP | ['DIRECT_TARGET_ELIGIBILITY_ERROR', 'TARGET_SELECTION_ERROR'] | complementary | complementary | ['O027', 'SE004'] | add_without_supersede |
| - | LTW2-007 | complementary_detail | TARGET_SELECTION_ERROR | ['DIRECT_TARGET_ELIGIBILITY_ERROR'] | complementary | complementary | ['SP004'] | add_without_supersede |
| - | LTW2-008 | complementary_detail | TARGET_SELECTION_ERROR | ['DIRECT_TARGET_ELIGIBILITY_ERROR'] | complementary | complementary | ['SR002'] | add_without_supersede |
| - | LTW2-010 | complementary_detail | RANKING_DROP | ['DIRECT_TARGET_ELIGIBILITY_ERROR', 'TARGET_SELECTION_ERROR'] | complementary | complementary | ['O048'] | add_without_supersede |
| - | LTW2-011 | sustained_update | SAFETY_DOWNGRADE | ['WRITE_POLICY_ERROR'] | update | update | ['O051'] | add_without_supersede |
| - | LTW2-012 | sustained_update | SAFETY_DOWNGRADE | ['WRITE_POLICY_ERROR'] | update | update | ['O056'] | add_without_supersede |
| - | LTW2-013 | sustained_update | TARGET_SELECTION_ERROR | ['DIRECT_TARGET_ELIGIBILITY_ERROR', 'WRITE_POLICY_ERROR'] | update | update | ['SP018', 'O061'] | add_without_supersede |
| - | LTW2-014 | sustained_update | SAFETY_DOWNGRADE | ['WRITE_POLICY_ERROR'] | update | update | ['O066'] | add_without_supersede |
| - | LTW2-015 | sustained_update | SAFETY_DOWNGRADE | ['WRITE_POLICY_ERROR'] | update | update | ['O071'] | add_without_supersede |
| - | LTW2-018 | contradiction_authority | TARGET_SELECTION_ERROR | ['DIRECT_TARGET_ELIGIBILITY_ERROR'] | contradiction | contradiction | ['SP001', 'O086'] | add_without_supersede |
| - | LTW2-019 | contradiction_authority | SEMANTIC_RELATION_ERROR | ['RELATION_CLASSIFICATION_ERROR', 'DIRECT_TARGET_ELIGIBILITY_ERROR', 'TARGET_SELECTION_ERROR'] | contradiction | uncertain | [] | add_without_supersede |
| - | LTW2-020 | contradiction_authority | TARGET_SELECTION_ERROR | ['DIRECT_TARGET_ELIGIBILITY_ERROR'] | contradiction | contradiction | ['O096', 'SI001'] | add_without_supersede |
| - | LTW2-028 | temporal_event_identity | TARGET_SELECTION_ERROR | ['DIRECT_TARGET_ELIGIBILITY_ERROR'] | complementary | complementary | ['SE009'] | add_without_supersede |
| - | LTW2-034 | event_vs_pattern | RANKING_DROP | ['RELATION_CLASSIFICATION_ERROR', 'DIRECT_TARGET_ELIGIBILITY_ERROR', 'SEMANTIC_RELATION_ERROR', 'TARGET_SELECTION_ERROR'] | complementary | update | ['O168'] | add_without_supersede |
| - | LTW2-036 | multi_target_ambiguity | TARGET_SELECTION_ERROR | ['DIRECT_TARGET_ELIGIBILITY_ERROR'] | update | update | ['O177', 'SR022', 'O176'] | add_without_supersede |
| - | LTW2-038 | multi_target_ambiguity | TARGET_SELECTION_ERROR | ['DIRECT_TARGET_ELIGIBILITY_ERROR'] | update | update | ['O186'] | add_without_supersede |
| - | LTW2-039 | multi_target_ambiguity | TARGET_SELECTION_ERROR | ['DIRECT_TARGET_ELIGIBILITY_ERROR'] | complementary | complementary | ['O192', 'O191', 'SI026'] | add_without_supersede |
| - | LTW2-040 | multi_target_ambiguity | RANKING_DROP | - | uncertain | uncertain | [] | add_without_supersede |

### Candidate-wise Failure Trace

| Run | Case | Candidate | Relation | Direct target | Final target | Gold target | Overall | Validator pass |
|---:|---|---|---|---:|---:|---:|---|---:|
| - | LTW2-002 | SP007 | same | True | True | False | complementary | False |
| - | LTW2-002 | O006 | same | True | True | True | complementary | False |
| - | LTW2-002 | SP001 | unrelated | False | False | False | complementary | False |
| - | LTW2-002 | SP008 | unrelated | False | False | False | complementary | False |
| - | LTW2-002 | O007 | unrelated | False | False | False | complementary | False |
| - | LTW2-003 | SR001 | same | True | True | False | complementary | False |
| - | LTW2-003 | O011 | same | True | True | True | complementary | False |
| - | LTW2-003 | O012 | complementary | False | False | False | complementary | False |
| - | LTW2-003 | SE020 | unrelated | False | False | False | complementary | False |
| - | LTW2-003 | SR022 | unrelated | False | False | False | complementary | False |
| - | LTW2-005 | SR005 | same | True | True | False | complementary | False |
| - | LTW2-005 | O021 | same | True | True | True | complementary | False |
| - | LTW2-005 | SR021 | unrelated | False | False | False | complementary | False |
| - | LTW2-005 | SR002 | complementary | False | False | False | complementary | False |
| - | LTW2-005 | SR006 | unrelated | False | False | False | complementary | False |
| - | LTW2-006 | O030 | complementary | False | False | False | complementary | False |
| - | LTW2-006 | SP018 | unrelated | False | False | False | complementary | False |
| - | LTW2-006 | O027 | complementary | True | True | False | complementary | False |
| - | LTW2-006 | SE004 | complementary | True | True | False | complementary | False |
| - | LTW2-006 | SP009 | unrelated | False | False | False | complementary | False |
| - | LTW2-007 | SP004 | complementary | True | True | False | complementary | True |
| - | LTW2-007 | SP009 | complementary | False | False | False | complementary | True |
| - | LTW2-007 | O031 | complementary | False | False | True | complementary | True |
| - | LTW2-007 | SP003 | unrelated | False | False | False | complementary | True |
| - | LTW2-007 | SP027 | unrelated | False | False | False | complementary | True |
| - | LTW2-008 | SR002 | complementary | True | True | False | complementary | True |
| - | LTW2-008 | O036 | complementary | False | False | True | complementary | True |
| - | LTW2-008 | SE013 | unrelated | False | False | False | complementary | True |
| - | LTW2-008 | SR003 | unrelated | False | False | False | complementary | True |
| - | LTW2-008 | SR010 | unrelated | False | False | False | complementary | True |
| - | LTW2-010 | SE011 | unrelated | False | False | False | complementary | False |
| - | LTW2-010 | O048 | complementary | True | True | False | complementary | False |
| - | LTW2-010 | SE019 | complementary | False | False | False | complementary | False |
| - | LTW2-010 | SE028 | unrelated | False | False | False | complementary | False |
| - | LTW2-010 | SE029 | unrelated | False | False | False | complementary | False |
| - | LTW2-011 | SE011 | complementary | False | False | False | update | False |
| - | LTW2-011 | O051 | update | True | True | True | update | False |
| - | LTW2-011 | SR026 | unrelated | False | False | False | update | False |
| - | LTW2-011 | SR010 | unrelated | False | False | False | update | False |
| - | LTW2-011 | SE013 | unrelated | False | False | False | update | False |
| - | LTW2-012 | O056 | update | True | True | True | update | False |
| - | LTW2-012 | SR002 | complementary | False | False | False | update | False |
| - | LTW2-012 | SE028 | unrelated | False | False | False | update | False |
| - | LTW2-012 | SR026 | unrelated | False | False | False | update | False |
| - | LTW2-012 | SR006 | unrelated | False | False | False | update | False |
| - | LTW2-013 | SP018 | complementary | True | True | False | update | False |
| - | LTW2-013 | O063 | complementary | False | False | False | update | False |
| - | LTW2-013 | O065 | unrelated | False | False | False | update | False |
| - | LTW2-013 | O061 | update | True | True | True | update | False |
| - | LTW2-013 | SE029 | unrelated | False | False | False | update | False |
| - | LTW2-014 | SE005 | unrelated | False | False | False | update | False |
| - | LTW2-014 | O066 | update | True | True | True | update | False |
| - | LTW2-014 | O069 | complementary | False | False | False | update | False |
| - | LTW2-014 | SE020 | complementary | False | False | False | update | False |
| - | LTW2-014 | SR022 | unrelated | False | False | False | update | False |
| - | LTW2-015 | O071 | update | True | True | True | update | False |
| - | LTW2-015 | SI008 | unrelated | False | False | False | update | False |
| - | LTW2-015 | SI026 | unrelated | False | False | False | update | False |
| - | LTW2-015 | SI030 | unrelated | False | False | False | update | False |
| - | LTW2-015 | SI022 | unrelated | False | False | False | update | False |
| - | LTW2-018 | SP001 | contradiction | True | True | False | contradiction | False |
| - | LTW2-018 | O086 | contradiction | True | True | True | contradiction | False |
| - | LTW2-018 | O088 | unrelated | False | False | False | contradiction | False |
| - | LTW2-018 | SP004 | unrelated | False | False | False | contradiction | False |
| - | LTW2-018 | SP003 | unrelated | False | False | False | contradiction | False |
| - | LTW2-019 | SI020 | unrelated | False | False | False | uncertain | True |
| - | LTW2-019 | O091 | uncertain | False | False | True | uncertain | True |
| - | LTW2-019 | SR026 | unrelated | False | False | False | uncertain | True |
| - | LTW2-019 | O092 | uncertain | False | False | False | uncertain | True |
| - | LTW2-019 | SE013 | unrelated | False | False | False | uncertain | True |
| - | LTW2-020 | SP003 | unrelated | False | False | False | contradiction | False |
| - | LTW2-020 | O096 | contradiction | True | True | True | contradiction | False |
| - | LTW2-020 | SI001 | contradiction | True | True | False | contradiction | False |
| - | LTW2-020 | SP005 | unrelated | False | False | False | contradiction | False |
| - | LTW2-020 | SP009 | unrelated | False | False | False | contradiction | False |
| - | LTW2-028 | SE009 | complementary | True | True | False | complementary | True |
| - | LTW2-028 | O136 | complementary | False | False | True | complementary | True |
| - | LTW2-028 | O137 | unrelated | False | False | False | complementary | True |
| - | LTW2-028 | O138 | complementary | False | False | False | complementary | True |
| - | LTW2-028 | SR003 | complementary | False | False | False | complementary | True |
| - | LTW2-034 | SI024 | complementary | False | False | False | update | False |
| - | LTW2-034 | O168 | update | True | True | False | update | False |
| - | LTW2-034 | SI011 | unrelated | False | False | False | update | False |
| - | LTW2-034 | SI023 | unrelated | False | False | False | update | False |
| - | LTW2-034 | SI012 | unrelated | False | False | False | update | False |
| - | LTW2-036 | O177 | update | True | True | True | update | False |
| - | LTW2-036 | SR009 | unrelated | False | False | False | update | False |
| - | LTW2-036 | SE005 | complementary | False | False | False | update | False |
| - | LTW2-036 | SR022 | update | True | True | False | update | False |
| - | LTW2-036 | O176 | update | True | True | True | update | False |
| - | LTW2-038 | O186 | update | True | True | True | update | False |
| - | LTW2-038 | O187 | complementary | False | False | True | update | False |
| - | LTW2-038 | SR003 | complementary | False | False | False | update | False |
| - | LTW2-038 | SR010 | complementary | False | False | False | update | False |
| - | LTW2-038 | SP023 | unrelated | False | False | False | update | False |
| - | LTW2-039 | O192 | same | True | True | True | complementary | False |
| - | LTW2-039 | O191 | same | True | True | True | complementary | False |
| - | LTW2-039 | SI012 | unrelated | False | False | False | complementary | False |
| - | LTW2-039 | SI026 | same | True | True | False | complementary | False |
| - | LTW2-039 | SI008 | unrelated | False | False | False | complementary | False |
| - | LTW2-040 | O197 | uncertain | False | False | False | uncertain | True |
| - | LTW2-040 | SE013 | uncertain | False | False | False | uncertain | True |
| - | LTW2-040 | SE011 | uncertain | False | False | False | uncertain | True |
| - | LTW2-040 | O199 | uncertain | False | False | False | uncertain | True |
| - | LTW2-040 | SR026 | unrelated | False | False | False | uncertain | True |

### Bounded Failure Details

#### LTW2-002 / run 1

- Incoming: 她一直都不喜欢特别辣的菜。
- Final target set: ['SP007', 'O006']
- Gold target set: ['O006']
- Overall relation: complementary
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:target_count_within_bounds', 'fail_closed']
- Top-K candidates:

  - 1. SP007: 她吃饭时不太能接受特别辣的菜。 [relation=same, direct=True]
  - 2. O006: 她不太能吃辣。 [relation=same, direct=True]
  - 3. SP001: 她更喜欢安静的小餐馆，不太喜欢音乐很吵的店。 [relation=unrelated, direct=False]
  - 4. SP008: 她比较喜欢甜度低的甜品。 [relation=unrelated, direct=False]
  - 5. O007: 她喜欢低甜度甜品。 [relation=unrelated, direct=False]

#### LTW2-003 / run 1

- Incoming: 工作日我们基本还是等晚上忙完才会认真聊。
- Final target set: ['SR001', 'O011']
- Gold target set: ['O011']
- Overall relation: complementary
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:target_count_within_bounds', 'fail_closed']
- Top-K candidates:

  - 1. SR001: 我们工作日通常都是晚上才会认真聊天。 [relation=same, direct=True]
  - 2. O011: 我们平时工作日通常晚上才会认真聊天。 [relation=same, direct=True]
  - 3. O012: 我们周末聊天会比工作日多。 [relation=complementary, direct=False]
  - 4. SE020: 前天晚上我们只聊了十几分钟，因为两个人都很累。 [relation=unrelated, direct=False]
  - 5. SR022: 如果一周没见面，我们通常会找一个晚上视频聊一会儿。 [relation=unrelated, direct=False]

#### LTW2-005 / run 1

- Incoming: 她不开心的时候还是不会马上告诉我具体原因。
- Final target set: ['SR005', 'O021']
- Gold target set: ['O021']
- Overall relation: complementary
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:target_count_within_bounds', 'fail_closed']
- Top-K candidates:

  - 1. SR005: 她不高兴时通常不会马上说原因。 [relation=same, direct=True]
  - 2. O021: 她不高兴时通常不会立刻说出原因。 [relation=same, direct=True]
  - 3. SR021: 她不喜欢在情绪很高的时候立刻做关系决定。 [relation=unrelated, direct=False]
  - 4. SR002: 她遇到工作上的烦心事时，有时会先自己消化一阵。 [relation=complementary, direct=False]
  - 5. SR006: 我一焦虑就容易连续追问她是不是生气了。 [relation=unrelated, direct=False]

#### LTW2-006 / run 1

- Incoming: 她一般会去公司附近那条河边走四十分钟左右。
- Final target set: ['O027', 'SE004']
- Gold target set: ['O026']
- Overall relation: complementary
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:target_count_within_bounds', 'fail_closed']
- Top-K candidates:

  - 1. O030: 她公司离江边大概十分钟。 [relation=complementary, direct=False]
  - 2. SP018: 她现在住的地方离公司通勤大约四十分钟。 [relation=unrelated, direct=False]
  - 3. O027: 前天她下班后去江边走了很久。 [relation=complementary, direct=True]
  - 4. SE004: 前天她下班后一个人去江边走了很久。 [relation=complementary, direct=True]
  - 5. SP009: 她周末更愿意去公园、展馆这类地方，而不是大型商场。 [relation=unrelated, direct=False]

#### LTW2-007 / run 1

- Incoming: 她尤其喜欢摄影展，对大型商业艺术展反而一般。
- Final target set: ['SP004']
- Gold target set: ['O031']
- Overall relation: complementary
- Validator: pass=True, validated_relation=complementary, would_update=False, reasons=['non_destructive_relation']
- Top-K candidates:

  - 1. SP004: 她平时比较喜欢独立书店和小型展览空间。 [relation=complementary, direct=True]
  - 2. SP009: 她周末更愿意去公园、展馆这类地方，而不是大型商场。 [relation=complementary, direct=False]
  - 3. O031: 她平时喜欢独立书店和小型展览空间。 [relation=complementary, direct=False]
  - 4. SP003: 她旅行时更偏向慢节奏，不喜欢一天排太多景点。 [relation=unrelated, direct=False]
  - 5. SP027: 她买衣服时更看重舒适，不太追求品牌。 [relation=unrelated, direct=False]

#### LTW2-008 / run 1

- Incoming: 她通常会先自己消化一晚上，第二天才愿意主动说。
- Final target set: ['SR002']
- Gold target set: ['O036']
- Overall relation: complementary
- Validator: pass=True, validated_relation=complementary, would_update=False, reasons=['non_destructive_relation']
- Top-K candidates:

  - 1. SR002: 她遇到工作上的烦心事时，有时会先自己消化一阵。 [relation=complementary, direct=True]
  - 2. O036: 她遇到工作上的烦心事时有时会先自己消化一阵。 [relation=complementary, direct=False]
  - 3. SE013: 她前几天明确说那周想自己休息，不安排见面。 [relation=unrelated, direct=False]
  - 4. SR003: 她心情不错的时候会主动分享一些很小的日常。 [relation=unrelated, direct=False]
  - 5. SR010: 她准备重要汇报前会明显减少闲聊。 [relation=unrelated, direct=False]

#### LTW2-010 / run 1

- Incoming: 除了工作，我们最近也会聊她家里的事情，只是频率没有工作话题高。
- Final target set: ['O048']
- Gold target set: ['O046']
- Overall relation: complementary
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:subject_compatible', 'fail_closed']
- Top-K candidates:

  - 1. SE011: 她上周做完汇报以后主动跟我聊了很久。 [relation=unrelated, direct=False]
  - 2. O048: 她上周第一次主动提到家里一个烦心事。 [relation=complementary, direct=True]
  - 3. SE019: 她上周第一次主动提到家里的一个烦心事。 [relation=complementary, direct=False]
  - 4. SE028: 上个月有一次她迟到了二十分钟，我们没有因此争吵。 [relation=unrelated, direct=False]
  - 5. SE029: 她上周末在家休息了两天，没有安排外出。 [relation=unrelated, direct=False]

#### LTW2-011 / run 1

- Incoming: 最近三周基本都是我先发消息，她已经很少主动开话题了。
- Final target set: ['O051']
- Gold target set: ['O051']
- Overall relation: update
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:temporal_evidence_available', 'fail_closed']
- Top-K candidates:

  - 1. SE011: 她上周做完汇报以后主动跟我聊了很久。 [relation=complementary, direct=False]
  - 2. O051: 前两个月她经常会主动找我聊天。 [relation=update, direct=True]
  - 3. SR026: 她不太喜欢连续多条消息追问同一个问题。 [relation=unrelated, direct=False]
  - 4. SR010: 她准备重要汇报前会明显减少闲聊。 [relation=unrelated, direct=False]
  - 5. SE013: 她前几天明确说那周想自己休息，不安排见面。 [relation=unrelated, direct=False]

#### LTW2-012 / run 1

- Incoming: 这一个月她几乎不再和我讲工作上的情绪，问了也只是说没事。
- Final target set: ['O056']
- Gold target set: ['O056']
- Overall relation: update
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:temporal_evidence_available', 'fail_closed']
- Top-K candidates:

  - 1. O056: 她以前工作不顺时经常会主动来跟我说。 [relation=update, direct=True]
  - 2. SR002: 她遇到工作上的烦心事时，有时会先自己消化一阵。 [relation=complementary, direct=False]
  - 3. SE028: 上个月有一次她迟到了二十分钟，我们没有因此争吵。 [relation=unrelated, direct=False]
  - 4. SR026: 她不太喜欢连续多条消息追问同一个问题。 [relation=unrelated, direct=False]
  - 5. SR006: 我一焦虑就容易连续追问她是不是生气了。 [relation=unrelated, direct=False]

#### LTW2-013 / run 1

- Incoming: 她上个月已经搬到浦东了，现在平时都是从浦东那边通勤。
- Final target set: ['SP018', 'O061']
- Gold target set: ['O061']
- Overall relation: update
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:target_count_within_bounds', 'failed:temporal_evidence_available', 'fail_closed']
- Top-K candidates:

  - 1. SP018: 她现在住的地方离公司通勤大约四十分钟。 [relation=complementary, direct=True]
  - 2. O063: 她通勤大约四十分钟。 [relation=complementary, direct=False]
  - 3. O065: 她有一个大学室友住在浦东。 [relation=unrelated, direct=False]
  - 4. O061: 她之前一直住在徐汇。 [relation=update, direct=True]
  - 5. SE029: 她上周末在家休息了两天，没有安排外出。 [relation=unrelated, direct=False]

#### LTW2-014 / run 1

- Incoming: 从八月中旬开始我们差不多两三天才认真聊一次，这个频率已经持续半个月了。
- Final target set: ['O066']
- Gold target set: ['O066']
- Overall relation: update
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:temporal_evidence_available', 'fail_closed']
- Top-K candidates:

  - 1. SE005: 上周我们视频聊了一个多小时。 [relation=unrelated, direct=False]
  - 2. O066: 之前我们基本每天都会认真聊一会儿。 [relation=update, direct=True]
  - 3. O069: 前天晚上我们只聊了十几分钟。 [relation=complementary, direct=False]
  - 4. SE020: 前天晚上我们只聊了十几分钟，因为两个人都很累。 [relation=complementary, direct=False]
  - 5. SR022: 如果一周没见面，我们通常会找一个晚上视频聊一会儿。 [relation=unrelated, direct=False]

#### LTW2-015 / run 1

- Incoming: 我想了一下还是先不表白了，至少等最近这段尴尬缓下来再说。
- Final target set: ['O071']
- Gold target set: ['O071']
- Overall relation: update
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:destructive_role_eligible', 'failed:temporal_evidence_available', 'fail_closed']
- Top-K candidates:

  - 1. O071: 我原本打算下次见面时向她表白。 [relation=update, direct=True]
  - 2. SI008: 我准备先观察一周聊天状态，再决定要不要谈关系问题。 [relation=unrelated, direct=False]
  - 3. SI026: 我想以后她明确说忙的时候先不追着问。 [relation=unrelated, direct=False]
  - 4. SI030: 我想下次争执时先确认事实，再表达自己的感受。 [relation=unrelated, direct=False]
  - 5. SI022: 我准备先把自己的需求说清楚，而不是猜她怎么想。 [relation=unrelated, direct=False]

#### LTW2-018 / run 1

- Incoming: 我感觉她可能其实更喜欢很热闹的地方，因为上次在酒吧她看起来挺开心。
- Final target set: ['SP001', 'O086']
- Gold target set: ['O086']
- Overall relation: contradiction
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:target_count_within_bounds', 'fail_closed']
- Top-K candidates:

  - 1. SP001: 她更喜欢安静的小餐馆，不太喜欢音乐很吵的店。 [relation=contradiction, direct=True]
  - 2. O086: 她一直更喜欢安静的小餐馆，不喜欢特别吵的店。 [relation=contradiction, direct=True]
  - 3. O088: 她喜欢小型展览空间。 [relation=unrelated, direct=False]
  - 4. SP004: 她平时比较喜欢独立书店和小型展览空间。 [relation=unrelated, direct=False]
  - 5. SP003: 她旅行时更偏向慢节奏，不喜欢一天排太多景点。 [relation=unrelated, direct=False]

#### LTW2-019 / run 1

- Incoming: 我有点觉得她是不是已经不想主动找我了，不过最近样本也不多。
- Final target set: []
- Gold target set: ['O091']
- Overall relation: uncertain
- Validator: pass=True, validated_relation=uncertain, would_update=False, reasons=['non_destructive_relation']
- Top-K candidates:

  - 1. SI020: 我想找机会问清楚她最近为什么减少主动联系。 [relation=unrelated, direct=False]
  - 2. O091: 最近两个月她仍然会主动开启不少聊天。 [relation=uncertain, direct=False]
  - 3. SR026: 她不太喜欢连续多条消息追问同一个问题。 [relation=unrelated, direct=False]
  - 4. O092: 昨天她主动问我周末有没有空。 [relation=uncertain, direct=False]
  - 5. SE013: 她前几天明确说那周想自己休息，不安排见面。 [relation=unrelated, direct=False]

#### LTW2-020 / run 1

- Incoming: 我觉得她可能已经不想去国庆后的徒步了，但她没有明确说取消。
- Final target set: ['O096', 'SI001']
- Gold target set: ['O096']
- Overall relation: contradiction
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:target_count_within_bounds', 'fail_closed']
- Top-K candidates:

  - 1. SP003: 她旅行时更偏向慢节奏，不喜欢一天排太多景点。 [relation=unrelated, direct=False]
  - 2. O096: 我们已经确认国庆以后找一个周末去附近徒步。 [relation=contradiction, direct=True]
  - 3. SI001: 我们计划国庆以后找一个周末去附近徒步。 [relation=contradiction, direct=True]
  - 4. SP005: 她不太喜欢临时改行程，希望提前知道大概安排。 [relation=unrelated, direct=False]
  - 5. SP009: 她周末更愿意去公园、展馆这类地方，而不是大型商场。 [relation=unrelated, direct=False]

#### LTW2-028 / run 1

- Incoming: 她昨晚又主动给我发了一张路边猫的照片。
- Final target set: ['SE009']
- Gold target set: ['O136']
- Overall relation: complementary
- Validator: pass=True, validated_relation=complementary, would_update=False, reasons=['non_destructive_relation']
- Top-K candidates:

  - 1. SE009: 三天前她给我发了一张路边猫的照片。 [relation=complementary, direct=True]
  - 2. O136: 三天前她也给我发过一张路边猫的照片。 [relation=complementary, direct=False]
  - 3. O137: 她家里以前养过猫。 [relation=unrelated, direct=False]
  - 4. O138: 她心情不错时会主动分享小日常。 [relation=complementary, direct=False]
  - 5. SR003: 她心情不错的时候会主动分享一些很小的日常。 [relation=complementary, direct=False]

#### LTW2-034 / run 1

- Incoming: 这次约会临时取消后，我们当天就重新约好了下周六。
- Final target set: ['O168']
- Gold target set: ['O166']
- Overall relation: update
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:destructive_role_eligible', 'failed:event_pattern_state_protection', 'failed:kind_compatible', 'failed:temporal_evidence_available', 'fail_closed']
- Top-K candidates:

  - 1. SI024: 我想以后约会取消时尽快重新定一个新时间。 [relation=complementary, direct=False]
  - 2. O168: 我们暂定下周六晚上一起吃饭。 [relation=update, direct=True]
  - 3. SI011: 我们暂定周日晚上视频聊一下。 [relation=unrelated, direct=False]
  - 4. SI023: 我们提过以后找时间一起做顿饭。 [relation=unrelated, direct=False]
  - 5. SI012: 我准备下次约会前先问她想不想去人少一点的地方。 [relation=unrelated, direct=False]

#### LTW2-036 / run 1

- Incoming: 最近我们既减少了日常聊天，也很少视频了，整体联系频率比以前低很多。
- Final target set: ['O177', 'SR022', 'O176']
- Gold target set: ['O176', 'O177']
- Overall relation: update
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:target_count_within_bounds', 'failed:temporal_evidence_available', 'fail_closed']
- Top-K candidates:

  - 1. O177: 以前一周没见面时我们通常会找晚上视频聊。 [relation=update, direct=True]
  - 2. SR009: 我们周末的聊天频率通常比工作日高。 [relation=unrelated, direct=False]
  - 3. SE005: 上周我们视频聊了一个多小时。 [relation=complementary, direct=False]
  - 4. SR022: 如果一周没见面，我们通常会找一个晚上视频聊一会儿。 [relation=update, direct=True]
  - 5. O176: 之前我们基本每天都会认真聊一会儿。 [relation=update, direct=True]

#### LTW2-038 / run 1

- Incoming: 她最近忙的时候不仅回复更短，而且也更少主动分享日常了。
- Final target set: ['O186']
- Gold target set: ['O186', 'O187']
- Overall relation: update
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:temporal_evidence_available', 'fail_closed']
- Top-K candidates:

  - 1. O186: 她忙的时候回复会变短，但不一定完全不回。 [relation=update, direct=True]
  - 2. O187: 她心情不错时会主动分享一些很小的日常。 [relation=complementary, direct=False]
  - 3. SR003: 她心情不错的时候会主动分享一些很小的日常。 [relation=complementary, direct=False]
  - 4. SR010: 她准备重要汇报前会明显减少闲聊。 [relation=complementary, direct=False]
  - 5. SP023: 我比较在意对方是否主动分享日常。 [relation=unrelated, direct=False]

#### LTW2-039 / run 1

- Incoming: 我准备一方面少追问她，另一方面下次见面时把自己的需求直接说清楚。
- Final target set: ['O192', 'O191', 'SI026']
- Gold target set: ['O191', 'O192']
- Overall relation: complementary
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:target_count_within_bounds', 'fail_closed']
- Top-K candidates:

  - 1. O192: 我准备先把自己的需求说清楚，而不是猜她怎么想。 [relation=same, direct=True]
  - 2. O191: 我准备这周先少追问她，让她忙完手头的事。 [relation=same, direct=True]
  - 3. SI012: 我准备下次约会前先问她想不想去人少一点的地方。 [relation=unrelated, direct=False]
  - 4. SI026: 我想以后她明确说忙的时候先不追着问。 [relation=same, direct=True]
  - 5. SI008: 我准备先观察一周聊天状态，再决定要不要谈关系问题。 [relation=unrelated, direct=False]

#### LTW2-040 / run 1

- Incoming: 我感觉她最近可能既不太想见我，也不太想主动聊天，但我没有足够证据。
- Final target set: []
- Gold target set: []
- Overall relation: uncertain
- Validator: pass=True, validated_relation=uncertain, would_update=False, reasons=['non_destructive_relation']
- Top-K candidates:

  - 1. O197: 最近两个月她仍然会主动开启不少聊天。 [relation=uncertain, direct=False]
  - 2. SE013: 她前几天明确说那周想自己休息，不安排见面。 [relation=uncertain, direct=False]
  - 3. SE011: 她上周做完汇报以后主动跟我聊了很久。 [relation=uncertain, direct=False]
  - 4. O199: 她前几天说那周想自己休息，不安排见面。 [relation=uncertain, direct=False]
  - 5. SR026: 她不太喜欢连续多条消息追问同一个问题。 [relation=unrelated, direct=False]


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
 The production SemanticRelationProposal exposes a bounded `reason` rather than a separate `reason_code`; this evaluator records the existing contract without expanding the relation ontology.

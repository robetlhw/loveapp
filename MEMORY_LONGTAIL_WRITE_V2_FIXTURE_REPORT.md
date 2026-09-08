# Memory Long-tail Write V2 Final Live Validation

- Version: `memory-longtail-write-v2-final-live-v1`
- Cases: `40`
- Passed: `23`
- Evaluated rows (including repeats): `40`
- Evaluation status: **V2_BASELINE_REQUIRES_REVIEW**
- Safety status (applied destructive writes): **PASS**
- Evaluation mode: `shadow_fixture_v2`
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
- Retrieval engine: `['benchmark_staged']`
- Embedding input: `natural_language_text_only`
- Embedding input detail: incoming/seed natural-language text only
- Judge query input: `incoming natural-language text`

| Metric | Value |
|---|---:|

### Per-run summary

| Run | Cases | Passed | Failed |
|---:|---:|---:|---:|
| 1 | 40 | 23 | 17 |
| `retrieval_expected_case_count` | 35 |
| `retrieval_expected_target_count` | 40 |
| `retrieval_hit_at_1` | 0.3429 |
| `retrieval_hit_at_3` | 0.4571 |
| `retrieval_hit_at_5` | 0.4571 |
| `retrieval_hit_at_10` | 0.4857 |
| `retrieval_hit_at_20` | 0.5429 |
| `retrieval_recall_at_5` | 0.4500 |
| `retrieval_recall_at_10` | 0.5000 |
| `retrieval_recall_at_20` | 0.5500 |
| `raw_retrieval_recall_at_5` | 0.4500 |
| `raw_retrieval_recall_at_10` | 0.5000 |
| `raw_retrieval_recall_at_20` | 0.5500 |
| `equivalence_aware_recall_at_5` | 0.4500 |
| `equivalence_aware_recall_at_10` | 0.5000 |
| `equivalence_aware_recall_at_20` | 0.5500 |
| `mrr` | 0.4020 |
| `conditional_gold_retention_at_5` | 1.0000 |
| `gold_retention_at_5` | 1.0000 |
| `end_to_end_gold_recall_at_5` | 0.5500 |
| `target_set_recall_at_5` | 0.5500 |
| `gold_target_set_exact_at_5` | 0.5429 |
| `hard_negative_promotion_count` | 7 |
| `hard_negative_promotion_rate` | 0.2000 |
| `unrelated_candidate_vector_count` | 762 |
| `unrelated_candidate_ranked_count` | 178 |
| `unrelated_candidate_retention_rate` | 0.2336 |
| `equivalence_group_duplicate_slot_count_at_20` | 16 |
| `equivalence_group_duplicate_slot_count_at_5` | 0 |
| `pre_collapse_candidate_count` | 800 |
| `post_collapse_candidate_count` | 784 |
| `equivalence_groups_collapsed` | 16 |
| `duplicate_slots_removed` | 16 |
| `top5_duplicate_semantic_memory_count` | 0 |
| `avg_candidate_count` | 125 |
| `retrieval_latency_p50_ms` | 10.9070 |
| `retrieval_latency_p95_ms` | 17.4850 |
| `cheap_ranking_latency_p50_ms` | 0.5060 |
| `cheap_ranking_latency_p95_ms` | 0.8660 |
| `vector_ranking_latency_p50_ms` | 0.0000 |
| `vector_ranking_latency_p95_ms` | 0.0000 |

Metric definitions: `raw_retrieval_recall_at_20` counts exact physical Gold IDs in vector Top-20. `equivalence_aware_recall_at_20` counts a case-local documented equivalence group as one semantic hit. `conditional_gold_retention_at_5` is ranked Top-5 Gold hits divided by Gold hits already present in vector Top-20. `end_to_end_gold_recall_at_5` is ranked Top-5 Gold hits divided by all expected retrieval references.

## V2 Acceptance Checks

| Check | Result |
|---|---|
| `retrieval_recall_at_20` | MISS |
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
| `relation_accuracy` | 1.0000 | 1.0000 |
| `macro_f1` | 1.0000 | 1.0000 |
| `update_precision` | 1.0000 | 1.0000 |
| `update_recall` | 1.0000 | 1.0000 |
| `target_exact_match` | 1.0000 | 0.6250 |
| `target_set_accuracy` | 1.0000 | 0.6250 |
| `target_memory_accuracy` | 1.0000 | 0.6250 |
| `target_micro_precision` | 1.0000 | 1.0000 |
| `target_memory_precision` | 1.0000 | 1.0000 |
| `target_micro_recall` | 1.0000 | 0.5789 |
| `target_micro_f1` | 1.0000 | 0.7333 |
| `judge_failure_count` | 0 | 0 |

## Retrieved Relation PRF

| Relation | Support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| `same` | 5 | 1.0000 | 1.0000 | 1.0000 |
| `complementary` | 17 | 1.0000 | 1.0000 | 1.0000 |
| `update` | 7 | 1.0000 | 1.0000 | 1.0000 |
| `contradiction` | 5 | 1.0000 | 1.0000 | 1.0000 |
| `unrelated` | 5 | 1.0000 | 1.0000 | 1.0000 |
| `uncertain` | 1 | 1.0000 | 1.0000 | 1.0000 |

## Contradiction Diagnostics

- Support: `5`
- Precision / Recall / F1: `1.0000` / `1.0000` / `1.0000`

| Case | Incoming | Candidates | Gold | Actual | Confidence | Validator |
|---|---|---|---|---|---:|---|
| LTW2-016 | 我总觉得她可能其实不太想让我融入她朋友圈，只是嘴上不好拒绝。 | ['她之前明确说愿意带我认识她的朋友。'] | contradiction | contradiction | 0.9900 | pass=False, would_update=False |
| LTW2-017 | 我最近有点怀疑她是不是其实还有别的对象，但我没有证据。 | ['她之前明确告诉我自己目前是单身。'] | contradiction | contradiction | 0.9900 | pass=True, would_update=False |
| LTW2-018 | 我感觉她可能其实更喜欢很热闹的地方，因为上次在酒吧她看起来挺开心。 | ['她一直更喜欢安静的小餐馆，不喜欢特别吵的店。'] | contradiction | contradiction | 0.9900 | pass=False, would_update=False |
| LTW2-019 | 我有点觉得她是不是已经不想主动找我了，不过最近样本也不多。 | ['最近两个月她仍然会主动开启不少聊天。'] | contradiction | contradiction | 0.9900 | pass=False, would_update=False |
| LTW2-020 | 我觉得她可能已经不想去国庆后的徒步了，但她没有明确说取消。 | ['我们已经确认国庆以后找一个周末去附近徒步。'] | contradiction | contradiction | 0.9900 | pass=False, would_update=False |

## Review-excluded Metrics

- Cases included: `40`
- Evaluated rows included (including repeats): `40`
- Excluded Gold-collision cases: `[]`

| Metric | Value |
|---|---:|
| `retrieval_recall_at_20` | 0.5500 |
| `gold_retention_at_5` | 1.0000 |
| `relation_accuracy` | 1.0000 |
| `target_set_accuracy` | 0.6250 |
| `destructive_safety_violation_count` | 0 |
| `proposal_plus_write_safety_diagnostic_count` | 0 |

## Write and Store

| Metric | Value |
|---|---:|
| `evaluated_count` | 40 |
| `store_action_accuracy` | 0.8250 |
| `new_row_decision_accuracy` | 0.9500 |
| `final_status_accuracy` | 0.9500 |
| `supersede_exact_match_accuracy` | 0.8750 |
| `preserve_exact_match_accuracy` | 1.0000 |
| `store_application_error_count` | 0 |
| `transition_audit_count` | 40 |
| `validator_allow_count` | 21 |
| `validator_deny_count` | 19 |

## Policy Boundaries

| Boundary | Value |
|---|---:|
| `multi_target_proposal_count` | 7 |
| `retrieved_multi_target_proposal_count` | 3 |
| `expected_multi_target_case_count` | 4 |
| `exact_expected_multi_target_proposal_count` | 3 |
| `overbroad_multi_target_proposal_count` | 0 |
| `policy_boundary_count` | 3 |
| `multi_target_validator_denied_count` | 7 |
| `destructive_multi_target_write_count` | 0 |
| `multi_target_status` | UNSUPPORTED_FAIL_CLOSED |
| `action_intent_update` | {'proposal_count': 1, 'destructive_role_denied_count': 1, 'status': 'VALIDATOR_POLICY_BOUNDARY'} |

## Safety

| Metric | Value |
|---|---:|
| `false_supersede_count` | 0 |
| `false_merge_count` | 0 |
| `false_link_count` | 0 |
| `cross_subject_false_link_count` | 0 |
| `event_false_dedupe_count` | 0 |
| `event_false_supersede_count` | 0 |
| `event_to_pattern_false_update_count` | 0 |
| `custom_to_canonical_false_supersede_count` | 0 |
| `proposed_overwrites_confirmed_count` | 0 |
| `uncertain_destructive_update_count` | 0 |
| `non_target_supersede_count` | 0 |
| `historical_event_not_preserved_count` | 0 |
| `proposal_safety_violation_count` | 0 |
| `validator_blocked_false_link_count` | 0 |
| `validator_allowed_false_link_count` | 0 |
| `actual_false_link_write_count` | 0 |
| `actual_destructive_write_count` | 0 |
| `actual_destructive_write_violation_count` | 0 |
| `destructive_safety_violation_count` | 0 |
| `proposal_plus_write_safety_diagnostic_count` | 0 |
| `false_destructive_update_count` | 0 |
| `false_destructive_update_rate` | 0.0000 |
| `proposal_safety_violation_rate` | 0.0000 |
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
| `embedding_model` | fixture-char-ngram |
| `embedding_model_version` | v1 |
| `embedding_dimension` | 256 |
| `embedding_document_call_count` | 1 |
| `embedding_query_call_count` | 40 |
| `embedding_failure_count` | 0 |
| `embedding_document_failure_count` | 0 |
| `embedding_query_failure_count` | 0 |
| `embedding_document_latency_p50_ms` | 31.4550 |
| `embedding_document_latency_p95_ms` | 31.4550 |
| `embedding_query_latency_p50_ms` | 0.2270 |
| `embedding_query_latency_p95_ms` | 0.3850 |
| `embedding_query_latency_total_ms` | 9.5920 |
| `judge_models` | ['fixture-v2-reviewed'] |
| `judge_call_count` | 80 |
| `judge_evaluated_count` | 80 |
| `judge_failure_count` | 0 |
| `judge_transport_failure_count` | 0 |
| `judge_parse_failure_count` | 0 |
| `judge_relation_mismatch_count` | 0 |
| `judge_target_mismatch_count` | 15 |
| `judge_retrieval_reference_unavailable_count` | 16 |
| `judge_target_candidate_unavailable_count` | 15 |
| `judge_target_gold_available_mismatch_count` | 0 |
| `judge_unexpected_target_count` | 0 |
| `target_policy_accepted_count` | 80 |
| `target_policy_fail_closed_count` | 0 |
| `judge_latency_p50_ms` | 0.1220 |
| `judge_latency_p95_ms` | 0.1700 |
| `judge_prompt_tokens` | 0 |
| `judge_completion_tokens` | 0 |
| `judge_total_tokens` | 0 |
| `judge_avg_prompt_tokens` | 0.0000 |
| `judge_avg_completion_tokens` | 0.0000 |
| `judge_avg_total_tokens` | 0.0000 |
| `oracle_judge` | {'call_count': 40, 'completed_count': 40, 'failure_count': 0, 'relation_mismatch_count': 0, 'target_mismatch_count': 0, 'unexpected_target_count': 0, 'target_candidate_unavailable_count': 0, 'latency_p50_ms': 0.131, 'latency_p95_ms': 0.183, 'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'avg_prompt_tokens': 0.0, 'avg_completion_tokens': 0.0, 'avg_total_tokens': 0.0} |
| `retrieved_judge` | {'call_count': 40, 'completed_count': 40, 'failure_count': 0, 'relation_mismatch_count': 0, 'target_mismatch_count': 15, 'unexpected_target_count': 0, 'target_candidate_unavailable_count': 15, 'latency_p50_ms': 0.079, 'latency_p95_ms': 0.131, 'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'avg_prompt_tokens': 0.0, 'avg_completion_tokens': 0.0, 'avg_total_tokens': 0.0} |
| `estimated_cost_per_100_writes` | N/A |

## Failure Attribution

Counts below are evaluated-row counts; in repeat mode one case may contribute more than one row.

| Primary stage | Count |
|---|---:|
| `RETRIEVAL_MISS` | 16 |
| `SAFETY_DOWNGRADE` | 1 |

| Secondary diagnostic | Count |
|---|---:|
| `TARGET_SELECTION_ERROR` | 15 |
| `WRITE_POLICY_ERROR` | 7 |

## Failed Cases

| Run | Case | Slice | Primary | Secondary | Expected | Actual | Targets | Action |
|---:|---|---|---|---|---|---|---|---|
| - | LTW2-002 | same_semantic_rephrase | RETRIEVAL_MISS | ['TARGET_SELECTION_ERROR', 'WRITE_POLICY_ERROR'] | same | same | [] | add_without_supersede |
| - | LTW2-005 | same_semantic_rephrase | RETRIEVAL_MISS | ['TARGET_SELECTION_ERROR', 'WRITE_POLICY_ERROR'] | same | same | [] | add_without_supersede |
| - | LTW2-006 | complementary_detail | RETRIEVAL_MISS | ['TARGET_SELECTION_ERROR'] | complementary | complementary | [] | add_without_supersede |
| - | LTW2-010 | complementary_detail | RETRIEVAL_MISS | ['TARGET_SELECTION_ERROR'] | complementary | complementary | [] | add_without_supersede |
| - | LTW2-011 | sustained_update | RETRIEVAL_MISS | ['TARGET_SELECTION_ERROR', 'WRITE_POLICY_ERROR'] | update | update | [] | add_without_supersede |
| - | LTW2-012 | sustained_update | RETRIEVAL_MISS | ['TARGET_SELECTION_ERROR', 'WRITE_POLICY_ERROR'] | update | update | [] | add_without_supersede |
| - | LTW2-013 | sustained_update | RETRIEVAL_MISS | ['TARGET_SELECTION_ERROR', 'WRITE_POLICY_ERROR'] | update | update | [] | add_without_supersede |
| - | LTW2-014 | sustained_update | SAFETY_DOWNGRADE | ['WRITE_POLICY_ERROR'] | update | update | ['O066'] | add_without_supersede |
| - | LTW2-015 | sustained_update | RETRIEVAL_MISS | ['TARGET_SELECTION_ERROR', 'WRITE_POLICY_ERROR'] | update | update | [] | add_without_supersede |
| - | LTW2-016 | contradiction_authority | RETRIEVAL_MISS | ['TARGET_SELECTION_ERROR'] | contradiction | contradiction | [] | add_without_supersede |
| - | LTW2-018 | contradiction_authority | RETRIEVAL_MISS | ['TARGET_SELECTION_ERROR'] | contradiction | contradiction | [] | add_without_supersede |
| - | LTW2-019 | contradiction_authority | RETRIEVAL_MISS | ['TARGET_SELECTION_ERROR'] | contradiction | contradiction | [] | add_without_supersede |
| - | LTW2-020 | contradiction_authority | RETRIEVAL_MISS | ['TARGET_SELECTION_ERROR'] | contradiction | contradiction | [] | add_without_supersede |
| - | LTW2-031 | event_vs_pattern | RETRIEVAL_MISS | ['TARGET_SELECTION_ERROR'] | complementary | complementary | [] | add_without_supersede |
| - | LTW2-035 | event_vs_pattern | RETRIEVAL_MISS | ['TARGET_SELECTION_ERROR'] | complementary | complementary | [] | add_without_supersede |
| - | LTW2-036 | multi_target_ambiguity | RETRIEVAL_MISS | ['TARGET_SELECTION_ERROR'] | update | update | [] | add_without_supersede |
| - | LTW2-040 | multi_target_ambiguity | RETRIEVAL_MISS | - | uncertain | uncertain | [] | add_without_supersede |

### Candidate-wise Failure Trace

| Run | Case | Candidate | Relation | Direct target | Final target | Gold target | Overall | Validator pass |
|---:|---|---|---|---:|---:|---:|---|---:|
| - | LTW2-002 | SP007 | unrelated | False | False | False | same | False |
| - | LTW2-002 | SP003 | unrelated | False | False | False | same | False |
| - | LTW2-002 | O007 | unrelated | False | False | False | same | False |
| - | LTW2-002 | SP009 | unrelated | False | False | False | same | False |
| - | LTW2-002 | SP005 | unrelated | False | False | False | same | False |
| - | LTW2-005 | SR005 | unrelated | False | False | False | same | False |
| - | LTW2-005 | SR021 | unrelated | False | False | False | same | False |
| - | LTW2-005 | SR024 | unrelated | False | False | False | same | False |
| - | LTW2-005 | SR018 | unrelated | False | False | False | same | False |
| - | LTW2-005 | SR003 | unrelated | False | False | False | same | False |
| - | LTW2-006 | SP018 | unrelated | False | False | False | complementary | False |
| - | LTW2-006 | O030 | unrelated | False | False | False | complementary | False |
| - | LTW2-006 | SP015 | unrelated | False | False | False | complementary | False |
| - | LTW2-006 | SP009 | unrelated | False | False | False | complementary | False |
| - | LTW2-006 | SE029 | unrelated | False | False | False | complementary | False |
| - | LTW2-010 | SR009 | unrelated | False | False | False | complementary | False |
| - | LTW2-010 | O050 | unrelated | False | False | False | complementary | False |
| - | LTW2-010 | SR025 | unrelated | False | False | False | complementary | False |
| - | LTW2-010 | SE018 | unrelated | False | False | False | complementary | False |
| - | LTW2-010 | SE007 | unrelated | False | False | False | complementary | False |
| - | LTW2-011 | O052 | unrelated | False | False | False | update | False |
| - | LTW2-011 | SR019 | unrelated | False | False | False | update | False |
| - | LTW2-011 | SR024 | unrelated | False | False | False | update | False |
| - | LTW2-011 | SR013 | unrelated | False | False | False | update | False |
| - | LTW2-011 | SR018 | unrelated | False | False | False | update | False |
| - | LTW2-012 | SR002 | unrelated | False | False | False | update | False |
| - | LTW2-012 | SR021 | unrelated | False | False | False | update | False |
| - | LTW2-012 | SR018 | unrelated | False | False | False | update | False |
| - | LTW2-012 | SR026 | unrelated | False | False | False | update | False |
| - | LTW2-012 | SR030 | unrelated | False | False | False | update | False |
| - | LTW2-013 | SR012 | unrelated | False | False | False | update | False |
| - | LTW2-013 | SR008 | unrelated | False | False | False | update | False |
| - | LTW2-013 | SR021 | unrelated | False | False | False | update | False |
| - | LTW2-013 | SR002 | unrelated | False | False | False | update | False |
| - | LTW2-013 | SP009 | unrelated | False | False | False | update | False |
| - | LTW2-014 | O066 | update | True | True | True | update | False |
| - | LTW2-014 | SR001 | unrelated | False | False | False | update | False |
| - | LTW2-014 | SE002 | unrelated | False | False | False | update | False |
| - | LTW2-014 | SR025 | unrelated | False | False | False | update | False |
| - | LTW2-014 | SE018 | unrelated | False | False | False | update | False |
| - | LTW2-015 | SI026 | unrelated | False | False | False | update | False |
| - | LTW2-015 | SI010 | unrelated | False | False | False | update | False |
| - | LTW2-015 | SI024 | unrelated | False | False | False | update | False |
| - | LTW2-015 | SI002 | unrelated | False | False | False | update | False |
| - | LTW2-015 | SI007 | unrelated | False | False | False | update | False |
| - | LTW2-016 | SP017 | unrelated | False | False | False | contradiction | False |
| - | LTW2-016 | SP014 | unrelated | False | False | False | contradiction | False |
| - | LTW2-016 | SP011 | unrelated | False | False | False | contradiction | False |
| - | LTW2-016 | SR019 | unrelated | False | False | False | contradiction | False |
| - | LTW2-016 | SP027 | unrelated | False | False | False | contradiction | False |
| - | LTW2-018 | SP028 | unrelated | False | False | False | contradiction | False |
| - | LTW2-018 | SP009 | unrelated | False | False | False | contradiction | False |
| - | LTW2-018 | SP010 | unrelated | False | False | False | contradiction | False |
| - | LTW2-018 | SP013 | unrelated | False | False | False | contradiction | False |
| - | LTW2-018 | SP016 | unrelated | False | False | False | contradiction | False |
| - | LTW2-019 | SR023 | unrelated | False | False | False | contradiction | False |
| - | LTW2-019 | SR030 | unrelated | False | False | False | contradiction | False |
| - | LTW2-019 | SR015 | unrelated | False | False | False | contradiction | False |
| - | LTW2-019 | SR002 | unrelated | False | False | False | contradiction | False |
| - | LTW2-019 | SR018 | unrelated | False | False | False | contradiction | False |
| - | LTW2-020 | SI025 | unrelated | False | False | False | contradiction | False |
| - | LTW2-020 | SE023 | unrelated | False | False | False | contradiction | False |
| - | LTW2-020 | SE014 | unrelated | False | False | False | contradiction | False |
| - | LTW2-020 | SI022 | unrelated | False | False | False | contradiction | False |
| - | LTW2-020 | SI026 | unrelated | False | False | False | contradiction | False |
| - | LTW2-031 | O153 | unrelated | False | False | False | complementary | False |
| - | LTW2-031 | SR018 | unrelated | False | False | False | complementary | False |
| - | LTW2-031 | SE006 | unrelated | False | False | False | complementary | False |
| - | LTW2-031 | SR024 | unrelated | False | False | False | complementary | False |
| - | LTW2-031 | SR026 | unrelated | False | False | False | complementary | False |
| - | LTW2-035 | O172 | unrelated | False | False | False | complementary | False |
| - | LTW2-035 | SE018 | unrelated | False | False | False | complementary | False |
| - | LTW2-035 | SI023 | unrelated | False | False | False | complementary | False |
| - | LTW2-035 | SR009 | unrelated | False | False | False | complementary | False |
| - | LTW2-035 | SR022 | unrelated | False | False | False | complementary | False |
| - | LTW2-036 | SR001 | unrelated | False | False | False | update | False |
| - | LTW2-036 | SI015 | unrelated | False | False | False | update | False |
| - | LTW2-036 | SE001 | unrelated | False | False | False | update | False |
| - | LTW2-036 | SR009 | unrelated | False | False | False | update | False |
| - | LTW2-036 | SE002 | unrelated | False | False | False | update | False |
| - | LTW2-040 | SP030 | uncertain | False | False | False | uncertain | True |
| - | LTW2-040 | SE011 | uncertain | False | False | False | uncertain | True |
| - | LTW2-040 | SP003 | uncertain | False | False | False | uncertain | True |
| - | LTW2-040 | SE029 | uncertain | False | False | False | uncertain | True |
| - | LTW2-040 | SE004 | uncertain | False | False | False | uncertain | True |

### Bounded Failure Details

#### LTW2-002 / run 1

- Incoming: 她一直都不喜欢特别辣的菜。
- Final target set: []
- Gold target set: ['O006']
- Overall relation: same
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:event_pattern_state_protection', 'failed:kind_compatible', 'failed:same_scope', 'failed:source_message_is_distinct', 'failed:subject_compatible', 'failed:target_active', 'failed:target_count_within_bounds', 'failed:target_exists_in_retrieved_set', 'failed:target_not_expired', 'fail_closed']
- Top-K candidates:

  - 1. SP007: 她吃饭时不太能接受特别辣的菜。 [relation=unrelated, direct=False]
  - 2. SP003: 她旅行时更偏向慢节奏，不喜欢一天排太多景点。 [relation=unrelated, direct=False]
  - 3. O007: 她喜欢低甜度甜品。 [relation=unrelated, direct=False]
  - 4. SP009: 她周末更愿意去公园、展馆这类地方，而不是大型商场。 [relation=unrelated, direct=False]
  - 5. SP005: 她不太喜欢临时改行程，希望提前知道大概安排。 [relation=unrelated, direct=False]

#### LTW2-005 / run 1

- Incoming: 她不开心的时候还是不会马上告诉我具体原因。
- Final target set: []
- Gold target set: ['O021']
- Overall relation: same
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:event_pattern_state_protection', 'failed:kind_compatible', 'failed:same_scope', 'failed:source_message_is_distinct', 'failed:subject_compatible', 'failed:target_active', 'failed:target_count_within_bounds', 'failed:target_exists_in_retrieved_set', 'failed:target_not_expired', 'fail_closed']
- Top-K candidates:

  - 1. SR005: 她不高兴时通常不会马上说原因。 [relation=unrelated, direct=False]
  - 2. SR021: 她不喜欢在情绪很高的时候立刻做关系决定。 [relation=unrelated, direct=False]
  - 3. SR024: 她工作压力小的时候更愿意主动安排周末活动。 [relation=unrelated, direct=False]
  - 4. SR018: 她工作特别累的时候更愿意一个人散步。 [relation=unrelated, direct=False]
  - 5. SR003: 她心情不错的时候会主动分享一些很小的日常。 [relation=unrelated, direct=False]

#### LTW2-006 / run 1

- Incoming: 她一般会去公司附近那条河边走四十分钟左右。
- Final target set: []
- Gold target set: ['O026']
- Overall relation: complementary
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:same_scope', 'failed:source_message_is_distinct', 'failed:subject_compatible', 'failed:target_active', 'failed:target_count_within_bounds', 'failed:target_exists_in_retrieved_set', 'failed:target_not_expired', 'fail_closed']
- Top-K candidates:

  - 1. SP018: 她现在住的地方离公司通勤大约四十分钟。 [relation=unrelated, direct=False]
  - 2. O030: 她公司离江边大概十分钟。 [relation=unrelated, direct=False]
  - 3. SP015: 她有一个关系很好的大学室友，现在还经常联系。 [relation=unrelated, direct=False]
  - 4. SP009: 她周末更愿意去公园、展馆这类地方，而不是大型商场。 [relation=unrelated, direct=False]
  - 5. SE029: 她上周末在家休息了两天，没有安排外出。 [relation=unrelated, direct=False]

#### LTW2-010 / run 1

- Incoming: 除了工作，我们最近也会聊她家里的事情，只是频率没有工作话题高。
- Final target set: []
- Gold target set: ['O046']
- Overall relation: complementary
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:same_scope', 'failed:source_message_is_distinct', 'failed:subject_compatible', 'failed:target_active', 'failed:target_count_within_bounds', 'failed:target_exists_in_retrieved_set', 'failed:target_not_expired', 'fail_closed']
- Top-K candidates:

  - 1. SR009: 我们周末的聊天频率通常比工作日高。 [relation=unrelated, direct=False]
  - 2. O050: 我们周末聊天通常更多。 [relation=unrelated, direct=False]
  - 3. SR025: 我们一旦把周末计划定下来，一般很少在当天临时更改。 [relation=unrelated, direct=False]
  - 4. SE018: 我们最近一次见面时聊到了她换领导后的工作变化。 [relation=unrelated, direct=False]
  - 5. SE007: 上个月我们一起去了苏州两天。 [relation=unrelated, direct=False]

#### LTW2-011 / run 1

- Incoming: 最近三周基本都是我先发消息，她已经很少主动开话题了。
- Final target set: []
- Gold target set: ['O051']
- Overall relation: update
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:confirmed_protection', 'failed:destructive_role_eligible', 'failed:event_pattern_state_protection', 'failed:kind_compatible', 'failed:perspective_protection', 'failed:same_scope', 'failed:source_message_is_distinct', 'failed:subject_compatible', 'failed:target_active', 'failed:target_count_within_bounds', 'failed:target_exists_in_retrieved_set', 'failed:target_not_expired', 'failed:temporal_evidence_available', 'failed:temporal_order_plausible', 'fail_closed']
- Top-K candidates:

  - 1. O052: 她工作忙的时候回复会变短。 [relation=unrelated, direct=False]
  - 2. SR019: 她遇到家里的烦心事时更少主动讲细节。 [relation=unrelated, direct=False]
  - 3. SR024: 她工作压力小的时候更愿意主动安排周末活动。 [relation=unrelated, direct=False]
  - 4. SR013: 她觉得被误解时往往会先解释事实，再谈感受。 [relation=unrelated, direct=False]
  - 5. SR018: 她工作特别累的时候更愿意一个人散步。 [relation=unrelated, direct=False]

#### LTW2-012 / run 1

- Incoming: 这一个月她几乎不再和我讲工作上的情绪，问了也只是说没事。
- Final target set: []
- Gold target set: ['O056']
- Overall relation: update
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:confirmed_protection', 'failed:destructive_role_eligible', 'failed:event_pattern_state_protection', 'failed:kind_compatible', 'failed:perspective_protection', 'failed:same_scope', 'failed:source_message_is_distinct', 'failed:subject_compatible', 'failed:target_active', 'failed:target_count_within_bounds', 'failed:target_exists_in_retrieved_set', 'failed:target_not_expired', 'failed:temporal_evidence_available', 'failed:temporal_order_plausible', 'fail_closed']
- Top-K candidates:

  - 1. SR002: 她遇到工作上的烦心事时，有时会先自己消化一阵。 [relation=unrelated, direct=False]
  - 2. SR021: 她不喜欢在情绪很高的时候立刻做关系决定。 [relation=unrelated, direct=False]
  - 3. SR018: 她工作特别累的时候更愿意一个人散步。 [relation=unrelated, direct=False]
  - 4. SR026: 她不太喜欢连续多条消息追问同一个问题。 [relation=unrelated, direct=False]
  - 5. SR030: 她如果对一个活动真的感兴趣，会主动问具体时间。 [relation=unrelated, direct=False]

#### LTW2-013 / run 1

- Incoming: 她上个月已经搬到浦东了，现在平时都是从浦东那边通勤。
- Final target set: []
- Gold target set: ['O061']
- Overall relation: update
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:confirmed_protection', 'failed:destructive_role_eligible', 'failed:event_pattern_state_protection', 'failed:kind_compatible', 'failed:perspective_protection', 'failed:same_scope', 'failed:source_message_is_distinct', 'failed:subject_compatible', 'failed:target_active', 'failed:target_count_within_bounds', 'failed:target_exists_in_retrieved_set', 'failed:target_not_expired', 'failed:temporal_evidence_available', 'failed:temporal_order_plausible', 'fail_closed']
- Top-K candidates:

  - 1. SR012: 她遇到不确定的计划时倾向先说再看看。 [relation=unrelated, direct=False]
  - 2. SR008: 她忙的时候回复会变短，但不一定完全不回。 [relation=unrelated, direct=False]
  - 3. SR021: 她不喜欢在情绪很高的时候立刻做关系决定。 [relation=unrelated, direct=False]
  - 4. SR002: 她遇到工作上的烦心事时，有时会先自己消化一阵。 [relation=unrelated, direct=False]
  - 5. SP009: 她周末更愿意去公园、展馆这类地方，而不是大型商场。 [relation=unrelated, direct=False]

#### LTW2-014 / run 1

- Incoming: 从八月中旬开始我们差不多两三天才认真聊一次，这个频率已经持续半个月了。
- Final target set: ['O066']
- Gold target set: ['O066']
- Overall relation: update
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:temporal_evidence_available', 'fail_closed']
- Top-K candidates:

  - 1. O066: 之前我们基本每天都会认真聊一会儿。 [relation=update, direct=True]
  - 2. SR001: 我们工作日通常都是晚上才会认真聊天。 [relation=unrelated, direct=False]
  - 3. SE002: 八月中旬我们因为旅行预算问题争执过一次。 [relation=unrelated, direct=False]
  - 4. SR025: 我们一旦把周末计划定下来，一般很少在当天临时更改。 [relation=unrelated, direct=False]
  - 5. SE018: 我们最近一次见面时聊到了她换领导后的工作变化。 [relation=unrelated, direct=False]

#### LTW2-015 / run 1

- Incoming: 我想了一下还是先不表白了，至少等最近这段尴尬缓下来再说。
- Final target set: []
- Gold target set: ['O071']
- Overall relation: update
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:confirmed_protection', 'failed:destructive_role_eligible', 'failed:event_pattern_state_protection', 'failed:kind_compatible', 'failed:perspective_protection', 'failed:same_scope', 'failed:source_message_is_distinct', 'failed:subject_compatible', 'failed:target_active', 'failed:target_count_within_bounds', 'failed:target_exists_in_retrieved_set', 'failed:target_not_expired', 'failed:temporal_evidence_available', 'failed:temporal_order_plausible', 'fail_closed']
- Top-K candidates:

  - 1. SI026: 我想以后她明确说忙的时候先不追着问。 [relation=unrelated, direct=False]
  - 2. SI010: 我想以后发生分歧时尽量少在微信里争论。 [relation=unrelated, direct=False]
  - 3. SI024: 我想以后约会取消时尽快重新定一个新时间。 [relation=unrelated, direct=False]
  - 4. SI002: 我们暂定下周六晚上一起吃饭。 [relation=unrelated, direct=False]
  - 5. SI007: 我们计划下个月去看一个摄影展。 [relation=unrelated, direct=False]

#### LTW2-016 / run 1

- Incoming: 我总觉得她可能其实不太想让我融入她朋友圈，只是嘴上不好拒绝。
- Final target set: []
- Gold target set: ['O076']
- Overall relation: contradiction
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:same_scope', 'failed:source_message_is_distinct', 'failed:subject_compatible', 'failed:target_active', 'failed:target_count_within_bounds', 'failed:target_exists_in_retrieved_set', 'failed:target_not_expired', 'fail_closed']
- Top-K candidates:

  - 1. SP017: 她大学期间学过一点摄影。 [relation=unrelated, direct=False]
  - 2. SP014: 她通常工作日七点以后才下班。 [relation=unrelated, direct=False]
  - 3. SP011: 她住宿时很在意房间是否安静。 [relation=unrelated, direct=False]
  - 4. SR019: 她遇到家里的烦心事时更少主动讲细节。 [relation=unrelated, direct=False]
  - 5. SP027: 她买衣服时更看重舒适，不太追求品牌。 [relation=unrelated, direct=False]

#### LTW2-018 / run 1

- Incoming: 我感觉她可能其实更喜欢很热闹的地方，因为上次在酒吧她看起来挺开心。
- Final target set: []
- Gold target set: ['O086']
- Overall relation: contradiction
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:same_scope', 'failed:source_message_is_distinct', 'failed:subject_compatible', 'failed:target_active', 'failed:target_count_within_bounds', 'failed:target_exists_in_retrieved_set', 'failed:target_not_expired', 'fail_closed']
- Top-K candidates:

  - 1. SP028: 她听播客时更常听人物访谈和文化类节目。 [relation=unrelated, direct=False]
  - 2. SP009: 她周末更愿意去公园、展馆这类地方，而不是大型商场。 [relation=unrelated, direct=False]
  - 3. SP010: 她看电影更喜欢剧情片，不太看恐怖片。 [relation=unrelated, direct=False]
  - 4. SP013: 她目前在一家互联网公司做产品相关工作。 [relation=unrelated, direct=False]
  - 5. SP016: 她家里养过一只猫。 [relation=unrelated, direct=False]

#### LTW2-019 / run 1

- Incoming: 我有点觉得她是不是已经不想主动找我了，不过最近样本也不多。
- Final target set: []
- Gold target set: ['O091']
- Overall relation: contradiction
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:same_scope', 'failed:source_message_is_distinct', 'failed:subject_compatible', 'failed:target_active', 'failed:target_count_within_bounds', 'failed:target_exists_in_retrieved_set', 'failed:target_not_expired', 'fail_closed']
- Top-K candidates:

  - 1. SR023: 她在熟悉的人面前会比刚认识时健谈很多。 [relation=unrelated, direct=False]
  - 2. SR030: 她如果对一个活动真的感兴趣，会主动问具体时间。 [relation=unrelated, direct=False]
  - 3. SR015: 她表达感谢时更常通过行动而不是长篇文字。 [relation=unrelated, direct=False]
  - 4. SR002: 她遇到工作上的烦心事时，有时会先自己消化一阵。 [relation=unrelated, direct=False]
  - 5. SR018: 她工作特别累的时候更愿意一个人散步。 [relation=unrelated, direct=False]

#### LTW2-020 / run 1

- Incoming: 我觉得她可能已经不想去国庆后的徒步了，但她没有明确说取消。
- Final target set: []
- Gold target set: ['O096']
- Overall relation: contradiction
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:same_scope', 'failed:source_message_is_distinct', 'failed:subject_compatible', 'failed:target_active', 'failed:target_count_within_bounds', 'failed:target_exists_in_retrieved_set', 'failed:target_not_expired', 'fail_closed']
- Top-K candidates:

  - 1. SI025: 我们考虑年底找一个周末去周边温泉。 [relation=unrelated, direct=False]
  - 2. SE023: 两周前我们临时改过一次见面地点，但时间没变。 [relation=unrelated, direct=False]
  - 3. SE014: 上周我们一起讨论了国庆假期怎么安排。 [relation=unrelated, direct=False]
  - 4. SI022: 我准备先把自己的需求说清楚，而不是猜她怎么想。 [relation=unrelated, direct=False]
  - 5. SI026: 我想以后她明确说忙的时候先不追着问。 [relation=unrelated, direct=False]

#### LTW2-031 / run 1

- Incoming: 昨天她因为工作特别累，只回了我几条很短的消息。
- Final target set: []
- Gold target set: ['O151']
- Overall relation: complementary
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:same_scope', 'failed:source_message_is_distinct', 'failed:subject_compatible', 'failed:target_active', 'failed:target_count_within_bounds', 'failed:target_exists_in_retrieved_set', 'failed:target_not_expired', 'fail_closed']
- Top-K candidates:

  - 1. O153: 昨天她说新项目让她有点疲惫。 [relation=unrelated, direct=False]
  - 2. SR018: 她工作特别累的时候更愿意一个人散步。 [relation=unrelated, direct=False]
  - 3. SE006: 昨天她说最近新项目让她有点疲惫。 [relation=unrelated, direct=False]
  - 4. SR024: 她工作压力小的时候更愿意主动安排周末活动。 [relation=unrelated, direct=False]
  - 5. SR026: 她不太喜欢连续多条消息追问同一个问题。 [relation=unrelated, direct=False]

#### LTW2-035 / run 1

- Incoming: 今天我们在文字里又因为一个小分歧越聊越僵。
- Final target set: []
- Gold target set: ['O171']
- Overall relation: complementary
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:same_scope', 'failed:source_message_is_distinct', 'failed:subject_compatible', 'failed:target_active', 'failed:target_count_within_bounds', 'failed:target_exists_in_retrieved_set', 'failed:target_not_expired', 'fail_closed']
- Top-K candidates:

  - 1. O172: 上个月我们因为电影选择有过一个小分歧。 [relation=unrelated, direct=False]
  - 2. SE018: 我们最近一次见面时聊到了她换领导后的工作变化。 [relation=unrelated, direct=False]
  - 3. SI023: 我们提过以后找时间一起做顿饭。 [relation=unrelated, direct=False]
  - 4. SR009: 我们周末的聊天频率通常比工作日高。 [relation=unrelated, direct=False]
  - 5. SR022: 如果一周没见面，我们通常会找一个晚上视频聊一会儿。 [relation=unrelated, direct=False]

#### LTW2-036 / run 1

- Incoming: 最近我们既减少了日常聊天，也很少视频了，整体联系频率比以前低很多。
- Final target set: []
- Gold target set: ['O176', 'O177']
- Overall relation: update
- Validator: pass=False, validated_relation=uncertain, would_update=False, reasons=['failed:confirmed_protection', 'failed:destructive_role_eligible', 'failed:event_pattern_state_protection', 'failed:kind_compatible', 'failed:perspective_protection', 'failed:same_scope', 'failed:source_message_is_distinct', 'failed:subject_compatible', 'failed:target_active', 'failed:target_count_within_bounds', 'failed:target_exists_in_retrieved_set', 'failed:target_not_expired', 'failed:temporal_evidence_available', 'failed:temporal_order_plausible', 'fail_closed']
- Top-K candidates:

  - 1. SR001: 我们工作日通常都是晚上才会认真聊天。 [relation=unrelated, direct=False]
  - 2. SI015: 我们有一个还没定日期的短途旅行想法。 [relation=unrelated, direct=False]
  - 3. SE001: 上周六我们一起去看了一个小型摄影展。 [relation=unrelated, direct=False]
  - 4. SR009: 我们周末的聊天频率通常比工作日高。 [relation=unrelated, direct=False]
  - 5. SE002: 八月中旬我们因为旅行预算问题争执过一次。 [relation=unrelated, direct=False]

#### LTW2-040 / run 1

- Incoming: 我感觉她最近可能既不太想见我，也不太想主动聊天，但我没有足够证据。
- Final target set: []
- Gold target set: []
- Overall relation: uncertain
- Validator: pass=True, validated_relation=uncertain, would_update=False, reasons=['non_destructive_relation']
- Top-K candidates:

  - 1. SP030: 她的生日在十二月。 [relation=uncertain, direct=False]
  - 2. SE011: 她上周做完汇报以后主动跟我聊了很久。 [relation=uncertain, direct=False]
  - 3. SP003: 她旅行时更偏向慢节奏，不喜欢一天排太多景点。 [relation=uncertain, direct=False]
  - 4. SE029: 她上周末在家休息了两天，没有安排外出。 [relation=uncertain, direct=False]
  - 5. SE004: 前天她下班后一个人去江边走了很久。 [relation=uncertain, direct=False]


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

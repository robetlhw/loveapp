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
- Cheap ranking stage: **Top-5** candidates (also supplied to the Semantic Judge)
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
| `hard_negative_promotion_count` | 6 |
| `hard_negative_promotion_rate` | 0.1714 |
| `unrelated_candidate_vector_count` | 762 |
| `unrelated_candidate_ranked_count` | 168 |
| `unrelated_candidate_retention_rate` | 0.2205 |
| `equivalence_group_duplicate_slot_count_at_20` | 16 |
| `equivalence_group_duplicate_slot_count_at_5` | 10 |
| `avg_candidate_count` | 125 |
| `retrieval_latency_p50_ms` | 11.9140 |
| `retrieval_latency_p95_ms` | 17.2950 |
| `cheap_ranking_latency_p50_ms` | 0.2210 |
| `cheap_ranking_latency_p95_ms` | 0.3180 |
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
| `embedding_document_latency_p50_ms` | 48.7340 |
| `embedding_document_latency_p95_ms` | 48.7340 |
| `embedding_query_latency_p50_ms` | 0.2640 |
| `embedding_query_latency_p95_ms` | 0.4400 |
| `embedding_query_latency_total_ms` | 11.4350 |
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
| `target_policy_accepted_count` | 0 |
| `target_policy_fail_closed_count` | 0 |
| `judge_latency_p50_ms` | 0.0390 |
| `judge_latency_p95_ms` | 0.0720 |
| `judge_prompt_tokens` | 0 |
| `judge_completion_tokens` | 0 |
| `judge_total_tokens` | 0 |
| `judge_avg_prompt_tokens` | 0.0000 |
| `judge_avg_completion_tokens` | 0.0000 |
| `judge_avg_total_tokens` | 0.0000 |
| `oracle_judge` | {'call_count': 40, 'completed_count': 40, 'failure_count': 0, 'latency_p50_ms': 0.053, 'latency_p95_ms': 0.074, 'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'avg_prompt_tokens': 0.0, 'avg_completion_tokens': 0.0, 'avg_total_tokens': 0.0} |
| `retrieved_judge` | {'call_count': 40, 'completed_count': 40, 'failure_count': 0, 'latency_p50_ms': 0.022, 'latency_p95_ms': 0.045, 'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'avg_prompt_tokens': 0.0, 'avg_completion_tokens': 0.0, 'avg_total_tokens': 0.0} |
| `estimated_cost_per_100_writes` | N/A |

## Failure Attribution

Counts below are evaluated-row counts; in repeat mode one case may contribute more than one row.

| Primary stage | Count |
|---|---:|
| `RETRIEVAL_MISS` | 16 |
| `SAFETY_DOWNGRADE` | 1 |

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

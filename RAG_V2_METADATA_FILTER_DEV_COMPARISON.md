# LoveApp RAG V2 Metadata Filter Hard vs Soft Comparison

- Dataset: `dev`
- Mode: `e2e`
- Delta: `soft_minus_hard`
- Scope comparable: `True`

## Frozen configuration

| Setting | Hard | Soft |
|---|---|---|
| `min_score` | 0.6000 | 0.6000 |
| `candidate_limit` | 30 | 30 |
| `top_k` | 5 | 5 |
| `reranker_mode` | full | full |
| `lexical_weight` | 1.5000 | 1.5000 |
| `metadata_weight` | 1.0000 | 1.0000 |
| `retrieval_text_mode` | question_variants | question_variants |
| `hard_filter` | True | False |

## Overall

| Metric | Hard | Soft | Delta (Soft-Hard) |
|---|---:|---:|---:|
| `hit_at_1` | 0.1012 | 0.4413 | 0.3401 |
| `hit_at_3` | 0.1012 | 0.4737 | 0.3725 |
| `hit_at_5` | 0.1012 | 0.4777 | 0.3765 |
| `recall_at_3` | 0.0951 | 0.4433 | 0.3482 |
| `recall_at_5` | 0.0951 | 0.4595 | 0.3644 |
| `mrr` | 0.1012 | 0.4565 | 0.3553 |
| `ndcg_at_5` | 0.0986 | 0.4464 | 0.3478 |
| `candidate_recall` | 0.1926 | 0.9754 | 0.7828 |
| `coverage` | 0.2389 | 0.4939 | 0.2550 |
| `no_answer_precision` | 0.1711 | 1.0000 | 0.8289 |
| `no_answer_recall` | 0.5417 | 0.3333 | -0.2084 |
| `abstention_precision` | 0.1711 | 1.0000 | 0.8289 |
| `abstention_recall` | 0.5417 | 0.3333 | -0.2084 |
| `no_answer_f1` | 0.2600 | 0.5000 | 0.2400 |
| `false_retrieval_rate` | 0.0833 | 0.2917 | 0.2084 |
| `hard_negative_leakage_at_3` | 0.0000 | 0.0000 | 0.0000 |
| `branch_accuracy` | 0.4900 | 0.4900 | 0.0000 |
| `router_primary_scenario_accuracy` | 0.1661 | 0.1661 | 0.0000 |
| `goal_micro_f1` | 0.1919 | 0.1919 | 0.0000 |
| `high_safety_recall` | 0.2500 | 0.2500 | 0.0000 |
| `sensitive_branch_accuracy` | 0.0000 | 0.0000 | 0.0000 |
| `safety_rag_bypass_violation_rate` | 0.3913 | 0.3913 | 0.0000 |
| `oracle_e2e_degradation_pp` | N/A | N/A | N/A |
| `retrieval_called_count` | 146 | 146 | 0 |
| `retrieval_bypassed_count` | 154 | 154 | 0 |
| `empty_candidate_count` | 82 | 8 | -74 |

### Recovery counts on Router-metadata-incorrect answered RAG cases

- Denominator: `231`
- Gold recovered in candidate pool: `96` (0.4156)
- Gold recovered in Top3: `92` (0.3983)
- Gold recovered in Top5: `93` (0.4026)

## Router / retrieval coupling

- `hard_filter_amplification_count`: 96
- `hard_filter_amplification_rate`: 0.4156
- `hard_filter_candidate_recovery`: 96
- `hard_filter_top3_recovery`: 92
- `hard_filter_top5_recovery`: 93
- Amplification denominator (Router metadata incorrect answered RAG): 231
- Case-study target counts: recovered 5/5, residual 3/3, safety/router 2/2

## Router correctness slices

| Slice | Cases | Hard Hit@3 | Soft Hit@3 | Hard MRR | Soft MRR | Hard Coverage | Soft Coverage | Hard Candidate Recall | Soft Candidate Recall |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `metadata_correct` | 16 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9062 | 1.0000 |
| `metadata_incorrect` | 231 | 0.0390 | 0.4372 | 0.0390 | 0.4188 | 0.1861 | 0.4589 | 0.0849 | 0.9717 |

## Relationship-stage evaluation boundary

RouteResult currently emits no predicted relationship_stage; E2E keeps the case stage constant in both arms, so this experiment isolates scenario/goal Router errors and does not claim stage-misprediction evidence

## Error attribution

| Attribution | Hard | Soft | Paired |
|---|---:|---:|---:|
| `router_branch_error` | 134 | 134 | 134 |
| `router_metadata_error` | 1 | 5 | 10 |
| `hard_filter_amplification` | 96 | 0 | 96 |
| `retriever_candidate_miss` | 0 | 0 | 0 |
| `rerank_error` | 0 | 0 | 0 |
| `safety_error` | 19 | 19 | 19 |
| `no_answer_error` | 2 | 7 | 7 |
| `gold_or_dataset_issue` | 0 | 0 | 0 |

## Hard-filter trace evidence

- Required evidence cases: `5`
- Shape: soft dense nearest contains gold; hard dense nearest excludes gold; hard_filter=true is recorded on rag_vector_search before rerank
- `rag_v2_dev_002`: hard_nearest_contains_gold=False, soft_nearest_contains_gold=True, hard_filter_applied_before_dense_candidate=True
- `rag_v2_dev_008`: hard_nearest_contains_gold=False, soft_nearest_contains_gold=True, hard_filter_applied_before_dense_candidate=True
- `rag_v2_dev_009`: hard_nearest_contains_gold=False, soft_nearest_contains_gold=True, hard_filter_applied_before_dense_candidate=True
- `rag_v2_dev_010`: hard_nearest_contains_gold=False, soft_nearest_contains_gold=True, hard_filter_applied_before_dense_candidate=True
- `rag_v2_dev_013`: hard_nearest_contains_gold=False, soft_nearest_contains_gold=True, hard_filter_applied_before_dense_candidate=True

## Case studies

### `rag_v2_dev_002` — `hard_filter_amplification`

- Query: 我这边既有感情变淡但仍然舍不得共同经历，同时还有这段关系里大部分时间都在焦虑和猜测。我既想判断现状，也需要决定怎么行动，应该先处理哪些信息、再怎么做？
- Gold Scenario / Goals: `breakup` / `['understand', 'end_relationship']`
- Predicted Scenario / Goals: `relationship_maintenance` / `['understand']`
- Gold IDs: `['kb_v2_434', 'kb_v2_435']`
- Hard Filter Candidate IDs: `[]`
- Hard Filter Top5 IDs: `[]`
- Soft Metadata Candidate IDs: `['kb_v2_434', 'kb_v2_435', 'kb_v2_460', 'kb_v2_141', 'kb_v2_187', 'kb_v2_320', 'kb_v2_130', 'kb_v2_280', 'kb_v2_276', 'kb_v2_352', 'kb_v2_476', 'kb_v2_291', 'kb_v2_454', 'kb_v2_007', 'kb_v2_292', 'kb_v2_192', 'kb_v2_324', 'kb_v2_290', 'kb_v2_260', 'kb_v2_147', 'kb_v2_193', 'kb_v2_126', 'kb_v2_278', 'kb_v2_064', 'kb_v2_068', 'kb_v2_463', 'kb_v2_084', 'kb_v2_457', 'kb_v2_138', 'kb_v2_326']`
- Soft Metadata Top5 IDs: `['kb_v2_434', 'kb_v2_435', 'kb_v2_141', 'kb_v2_320', 'kb_v2_292']`

### `rag_v2_dev_008` — `hard_filter_amplification`

- Query: 我现在同时碰到两件事：一边是长期只有我在推动下一步，另一边又有不知道如何从共同兴趣开启话题。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
- Gold Scenario / Goals: `chat_analysis` / `['understand', 'initiate', 'communicate']`
- Predicted Scenario / Goals: `pursuit` / `['communicate', 'understand']`
- Gold IDs: `['kb_v2_165', 'kb_v2_020']`
- Hard Filter Candidate IDs: `['kb_v2_065', 'kb_v2_071', 'kb_v2_063', 'kb_v2_053', 'kb_v2_079', 'kb_v2_067', 'kb_v2_059', 'kb_v2_077', 'kb_v2_075', 'kb_v2_057', 'kb_v2_061']`
- Hard Filter Top5 IDs: `['kb_v2_065', 'kb_v2_071', 'kb_v2_053', 'kb_v2_079', 'kb_v2_075']`
- Soft Metadata Candidate IDs: `['kb_v2_020', 'kb_v2_165', 'kb_v2_027', 'kb_v2_277', 'kb_v2_104', 'kb_v2_170', 'kb_v2_330', 'kb_v2_095', 'kb_v2_182', 'kb_v2_031', 'kb_v2_065', 'kb_v2_347', 'kb_v2_275', 'kb_v2_212', 'kb_v2_293', 'kb_v2_008', 'kb_v2_455', 'kb_v2_071', 'kb_v2_200', 'kb_v2_137', 'kb_v2_233', 'kb_v2_260', 'kb_v2_326', 'kb_v2_386', 'kb_v2_245', 'kb_v2_023', 'kb_v2_131', 'kb_v2_146', 'kb_v2_292', 'kb_v2_438']`
- Soft Metadata Top5 IDs: `['kb_v2_020', 'kb_v2_027', 'kb_v2_031', 'kb_v2_165', 'kb_v2_277']`

### `rag_v2_dev_009` — `hard_filter_amplification`

- Query: 就是ta想立刻公开恋情我还没准备好这个事，感觉沟通有点卡，我下一步咋整比较不容易弄巧成拙？
- Gold Scenario / Goals: `boundary` / `['set_boundary', 'communicate']`
- Predicted Scenario / Goals: `relationship_maintenance` / `['communicate']`
- Gold IDs: `['kb_v2_401']`
- Hard Filter Candidate IDs: `['kb_v2_344', 'kb_v2_320', 'kb_v2_352', 'kb_v2_280', 'kb_v2_293', 'kb_v2_290', 'kb_v2_326', 'kb_v2_296', 'kb_v2_269', 'kb_v2_322', 'kb_v2_336', 'kb_v2_324', 'kb_v2_340', 'kb_v2_261', 'kb_v2_286', 'kb_v2_278', 'kb_v2_274', 'kb_v2_330', 'kb_v2_263', 'kb_v2_265', 'kb_v2_332', 'kb_v2_284']`
- Hard Filter Top5 IDs: `['kb_v2_344', 'kb_v2_352', 'kb_v2_280', 'kb_v2_320', 'kb_v2_326']`
- Soft Metadata Candidate IDs: `['kb_v2_401', 'kb_v2_344', 'kb_v2_394', 'kb_v2_442', 'kb_v2_190', 'kb_v2_320', 'kb_v2_352', 'kb_v2_079', 'kb_v2_365', 'kb_v2_280', 'kb_v2_293', 'kb_v2_083', 'kb_v2_397', 'kb_v2_379', 'kb_v2_290', 'kb_v2_441', 'kb_v2_281', 'kb_v2_070', 'kb_v2_391', 'kb_v2_051', 'kb_v2_013', 'kb_v2_361', 'kb_v2_326', 'kb_v2_187', 'kb_v2_206', 'kb_v2_201', 'kb_v2_296', 'kb_v2_247', 'kb_v2_269', 'kb_v2_199']`
- Soft Metadata Top5 IDs: `['kb_v2_401', 'kb_v2_344', 'kb_v2_352', 'kb_v2_280', 'kb_v2_320']`

### `rag_v2_dev_010` — `hard_filter_amplification`

- Query: 我遇到的是“交流总从“在吗”开始很难延续”，和看起来很像的其他情况不太一样。哪些细节能区分，具体该怎么做？
- Gold Scenario / Goals: `pursuit` / `['initiate', 'communicate']`
- Predicted Scenario / Goals: `relationship_maintenance` / `[]`
- Gold IDs: `['kb_v2_019']`
- Hard Filter Candidate IDs: `[]`
- Hard Filter Top5 IDs: `[]`
- Soft Metadata Candidate IDs: `['kb_v2_019', 'kb_v2_266', 'kb_v2_146', 'kb_v2_025', 'kb_v2_018', 'kb_v2_200', 'kb_v2_288', 'kb_v2_035', 'kb_v2_128', 'kb_v2_026', 'kb_v2_347', 'kb_v2_161', 'kb_v2_170', 'kb_v2_091', 'kb_v2_142', 'kb_v2_001', 'kb_v2_068', 'kb_v2_472', 'kb_v2_104', 'kb_v2_438', 'kb_v2_350', 'kb_v2_097', 'kb_v2_062', 'kb_v2_212', 'kb_v2_278', 'kb_v2_090', 'kb_v2_137', 'kb_v2_311', 'kb_v2_123', 'kb_v2_355']`
- Soft Metadata Top5 IDs: `['kb_v2_019', 'kb_v2_266', 'kb_v2_347', 'kb_v2_288', 'kb_v2_146']`

### `rag_v2_dev_013` — `hard_filter_amplification`

- Query: 我这边就是ta把结束这段关系理解成暂时冷静，越想越乱，这种情况正常该怎么处理？
- Gold Scenario / Goals: `breakup` / `['end_relationship', 'communicate']`
- Predicted Scenario / Goals: `relationship_maintenance` / `[]`
- Gold IDs: `['kb_v2_454']`
- Hard Filter Candidate IDs: `['kb_v2_297', 'kb_v2_355', 'kb_v2_291', 'kb_v2_314']`
- Hard Filter Top5 IDs: `['kb_v2_297', 'kb_v2_355', 'kb_v2_291', 'kb_v2_314']`
- Soft Metadata Candidate IDs: `['kb_v2_454', 'kb_v2_495', 'kb_v2_372', 'kb_v2_322', 'kb_v2_187', 'kb_v2_185', 'kb_v2_290', 'kb_v2_352', 'kb_v2_129', 'kb_v2_112', 'kb_v2_142', 'kb_v2_439', 'kb_v2_244', 'kb_v2_141', 'kb_v2_465', 'kb_v2_297', 'kb_v2_390', 'kb_v2_268', 'kb_v2_219', 'kb_v2_083', 'kb_v2_249', 'kb_v2_355', 'kb_v2_105', 'kb_v2_291', 'kb_v2_329', 'kb_v2_069', 'kb_v2_276', 'kb_v2_199', 'kb_v2_193', 'kb_v2_222']`
- Soft Metadata Top5 IDs: `['kb_v2_454', 'kb_v2_352', 'kb_v2_297', 'kb_v2_322', 'kb_v2_355']`

### `rag_v2_dev_001` — `router_branch_error`

- Query: 我这边就是我们两个人职业发展速度不同带来落差，越想越乱，这种情况正常该怎么处理？
- Gold Scenario / Goals: `relationship_maintenance` / `['communicate', 'progress']`
- Predicted Scenario / Goals: `None` / `[]`
- Gold IDs: `['kb_v2_325']`
- Hard Filter Candidate IDs: `[]`
- Hard Filter Top5 IDs: `[]`
- Soft Metadata Candidate IDs: `[]`
- Soft Metadata Top5 IDs: `[]`

### `rag_v2_dev_005` — `router_branch_error`

- Query: 我这边就是ta情绪上来后我也会立刻反击，越想越乱，这种情况正常该怎么处理？
- Gold Scenario / Goals: `conflict` / `['repair', 'communicate']`
- Predicted Scenario / Goals: `None` / `[]`
- Gold IDs: `['kb_v2_168']`
- Hard Filter Candidate IDs: `[]`
- Hard Filter Top5 IDs: `[]`
- Soft Metadata Candidate IDs: `[]`
- Soft Metadata Top5 IDs: `[]`

### `rag_v2_dev_006` — `router_branch_error`

- Query: 最近遇到一个情况：朋友都劝放下但我做不到。这种时候更合适的判断和处理顺序是什么？
- Gold Scenario / Goals: `breakup` / `['understand']`
- Predicted Scenario / Goals: `None` / `[]`
- Gold IDs: `['kb_v2_478']`
- Hard Filter Candidate IDs: `[]`
- Hard Filter Top5 IDs: `[]`
- Soft Metadata Candidate IDs: `[]`
- Soft Metadata Top5 IDs: `[]`

### `rag_v2_dev_003` — `safety_error`

- Query: 同类情况很多容易搞混：现在是ta反复否定我的记忆和感受。我应该依据什么判断，而不是直接套结论？
- Gold Scenario / Goals: `boundary` / `['set_boundary']`
- Predicted Scenario / Goals: `None` / `[]`
- Gold IDs: `['kb_v2_414']`
- Hard Filter Candidate IDs: `[]`
- Hard Filter Top5 IDs: `[]`
- Soft Metadata Candidate IDs: `[]`
- Soft Metadata Top5 IDs: `[]`

### `rag_v2_dev_007` — `safety_error`

- Query: 关于有人拿很私人的照片逼我继续这段关系，我最容易误判的地方是什么，下一步应该怎么做？
- Gold Scenario / Goals: `boundary` / `['set_boundary', 'end_relationship']`
- Predicted Scenario / Goals: `relationship_maintenance` / `[]`
- Gold IDs: `['kb_v2_427']`
- Hard Filter Candidate IDs: `[]`
- Hard Filter Top5 IDs: `[]`
- Soft Metadata Candidate IDs: `['kb_v2_427', 'kb_v2_428', 'kb_v2_121', 'kb_v2_426']`
- Soft Metadata Top5 IDs: `['kb_v2_427', 'kb_v2_428', 'kb_v2_121', 'kb_v2_426']`


## Conclusion

在 Router metadata 错误的 answered RAG case 中，Hard Filter 放大了 96 个 case 的候选/Top-K 丢失（amplification rate=0.4156）；切换 Soft Metadata 后 E2E Hit@3 delta=0.3725、Coverage delta=0.2550（delta 定义为 Soft-Hard）。

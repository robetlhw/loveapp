# LoveApp RAG V2 Metadata Filter Hard vs Soft Comparison

- Dataset: `test`
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
| `hit_at_1` | 0.1479 | 0.4615 | 0.3136 |
| `hit_at_3` | 0.1479 | 0.4675 | 0.3196 |
| `hit_at_5` | 0.1479 | 0.4675 | 0.3196 |
| `recall_at_3` | 0.1331 | 0.4349 | 0.3018 |
| `recall_at_5` | 0.1331 | 0.4467 | 0.3136 |
| `mrr` | 0.1479 | 0.4645 | 0.3166 |
| `ndcg_at_5` | 0.1363 | 0.4387 | 0.3024 |
| `candidate_recall` | 0.2778 | 1.0000 | 0.7222 |
| `coverage` | 0.2781 | 0.4793 | 0.2012 |
| `no_answer_precision` | 0.2273 | 1.0000 | 0.7727 |
| `no_answer_recall` | 0.6250 | 0.3125 | -0.3125 |
| `abstention_precision` | 0.2273 | 1.0000 | 0.7727 |
| `abstention_recall` | 0.6250 | 0.3125 | -0.3125 |
| `no_answer_f1` | 0.3333 | 0.4762 | 0.1429 |
| `false_retrieval_rate` | 0.0625 | 0.3750 | 0.3125 |
| `hard_negative_leakage_at_3` | 0.0000 | 0.0000 | 0.0000 |
| `branch_accuracy` | 0.4950 | 0.4950 | 0.0000 |
| `router_primary_scenario_accuracy` | 0.2054 | 0.2054 | 0.0000 |
| `goal_micro_f1` | 0.1943 | 0.1943 | 0.0000 |
| `high_safety_recall` | 0.4286 | 0.4286 | 0.0000 |
| `sensitive_branch_accuracy` | 0.0000 | 0.0000 | 0.0000 |
| `safety_rag_bypass_violation_rate` | 0.3636 | 0.3636 | 0.0000 |
| `oracle_e2e_degradation_pp` | N/A | N/A | N/A |
| `retrieval_called_count` | 96 | 96 | 0 |
| `retrieval_bypassed_count` | 104 | 104 | 0 |
| `empty_candidate_count` | 44 | 5 | -39 |

### Recovery counts on Router-metadata-incorrect answered RAG cases

- Denominator: `155`
- Gold recovered in candidate pool: `56` (0.3613)
- Gold recovered in Top3: `54` (0.3484)
- Gold recovered in Top5: `54` (0.3484)

## Router / retrieval coupling

- `hard_filter_amplification_count`: 56
- `hard_filter_amplification_rate`: 0.3613
- `hard_filter_candidate_recovery`: 56
- `hard_filter_top3_recovery`: 54
- `hard_filter_top5_recovery`: 54
- Amplification denominator (Router metadata incorrect answered RAG): 155
- Case-study target counts: recovered 5/5, residual 3/3, safety/router 2/2

## Router correctness slices

| Slice | Cases | Hard Hit@3 | Soft Hit@3 | Hard MRR | Soft MRR | Hard Coverage | Soft Coverage | Hard Candidate Recall | Soft Candidate Recall |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `metadata_correct` | 14 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9286 | 1.0000 |
| `metadata_incorrect` | 155 | 0.0710 | 0.4194 | 0.0710 | 0.4161 | 0.2129 | 0.4323 | 0.1418 | 1.0000 |

## Relationship-stage evaluation boundary

RouteResult currently emits no predicted relationship_stage; E2E keeps the case stage constant in both arms, so this experiment isolates scenario/goal Router errors and does not claim stage-misprediction evidence

## Error attribution

| Attribution | Hard | Soft | Paired |
|---|---:|---:|---:|
| `router_branch_error` | 93 | 93 | 93 |
| `router_metadata_error` | 0 | 2 | 11 |
| `hard_filter_amplification` | 56 | 0 | 56 |
| `retriever_candidate_miss` | 0 | 0 | 0 |
| `rerank_error` | 0 | 0 | 0 |
| `safety_error` | 8 | 8 | 8 |
| `no_answer_error` | 1 | 6 | 6 |
| `gold_or_dataset_issue` | 0 | 0 | 0 |

## Hard-filter trace evidence

- Required evidence cases: `5`
- Shape: soft dense nearest contains gold; hard dense nearest excludes gold; hard_filter=true is recorded on rag_vector_search before rerank
- `rag_v2_test_001`: hard_nearest_contains_gold=False, soft_nearest_contains_gold=True, hard_filter_applied_before_dense_candidate=True
- `rag_v2_test_008`: hard_nearest_contains_gold=False, soft_nearest_contains_gold=True, hard_filter_applied_before_dense_candidate=True
- `rag_v2_test_009`: hard_nearest_contains_gold=False, soft_nearest_contains_gold=True, hard_filter_applied_before_dense_candidate=True
- `rag_v2_test_010`: hard_nearest_contains_gold=False, soft_nearest_contains_gold=True, hard_filter_applied_before_dense_candidate=True
- `rag_v2_test_011`: hard_nearest_contains_gold=False, soft_nearest_contains_gold=True, hard_filter_applied_before_dense_candidate=True

## Case studies

### `rag_v2_test_001` — `hard_filter_amplification`

- Query: 背景是双方之前有一段时间相处正常，最近工作、学习和情绪都比较忙乱。现在出现共同租房押金和费用需要结算，我分不清这是短期波动、需求差异还是关系本身的信号。有哪些判断依据，沟通时应该抓住什么重点？
- Gold Scenario / Goals: `breakup` / `['set_boundary', 'end_relationship']`
- Predicted Scenario / Goals: `relationship_maintenance` / `['communicate', 'understand']`
- Gold IDs: `['kb_v2_482']`
- Hard Filter Candidate IDs: `[]`
- Hard Filter Top5 IDs: `[]`
- Soft Metadata Candidate IDs: `['kb_v2_482', 'kb_v2_277', 'kb_v2_232', 'kb_v2_233', 'kb_v2_335', 'kb_v2_490', 'kb_v2_433', 'kb_v2_394', 'kb_v2_371', 'kb_v2_337', 'kb_v2_239', 'kb_v2_392', 'kb_v2_256', 'kb_v2_398', 'kb_v2_015', 'kb_v2_316', 'kb_v2_148', 'kb_v2_230', 'kb_v2_196', 'kb_v2_184', 'kb_v2_258', 'kb_v2_340', 'kb_v2_268', 'kb_v2_244', 'kb_v2_402', 'kb_v2_422', 'kb_v2_227', 'kb_v2_481', 'kb_v2_229', 'kb_v2_292']`
- Soft Metadata Top5 IDs: `['kb_v2_482', 'kb_v2_277', 'kb_v2_335', 'kb_v2_337', 'kb_v2_316']`

### `rag_v2_test_008` — `hard_filter_amplification`

- Query: 最近我希望保留不与对象重叠的朋友圈，我有点搞不懂ta啥意思，也不想瞎脑补，咋处理比较稳？
- Gold Scenario / Goals: `boundary` / `['set_boundary', 'communicate']`
- Predicted Scenario / Goals: `relationship_maintenance` / `[]`
- Gold IDs: `['kb_v2_380']`
- Hard Filter Candidate IDs: `[]`
- Hard Filter Top5 IDs: `[]`
- Soft Metadata Candidate IDs: `['kb_v2_380', 'kb_v2_199', 'kb_v2_361', 'kb_v2_322', 'kb_v2_082', 'kb_v2_280', 'kb_v2_261', 'kb_v2_217', 'kb_v2_234', 'kb_v2_461', 'kb_v2_229', 'kb_v2_154', 'kb_v2_379', 'kb_v2_352', 'kb_v2_442', 'kb_v2_247', 'kb_v2_211', 'kb_v2_064', 'kb_v2_346', 'kb_v2_130', 'kb_v2_342', 'kb_v2_381', 'kb_v2_201', 'kb_v2_478', 'kb_v2_286', 'kb_v2_375', 'kb_v2_494', 'kb_v2_249', 'kb_v2_385', 'kb_v2_149']`
- Soft Metadata Top5 IDs: `['kb_v2_380', 'kb_v2_280', 'kb_v2_199', 'kb_v2_322', 'kb_v2_261']`

### `rag_v2_test_009` — `hard_filter_amplification`

- Query: 背景是双方之前有一段时间相处正常，最近工作、学习和情绪都比较忙乱。现在出现一生气就连续发送很多消息，我分不清这是短期波动、需求差异还是关系本身的信号。有哪些判断依据，沟通时应该抓住什么重点？
- Gold Scenario / Goals: `conflict` / `['repair', 'communicate']`
- Predicted Scenario / Goals: `relationship_maintenance` / `['communicate', 'understand']`
- Gold IDs: `['kb_v2_167']`
- Hard Filter Candidate IDs: `['kb_v2_352', 'kb_v2_322', 'kb_v2_280', 'kb_v2_269', 'kb_v2_293', 'kb_v2_288', 'kb_v2_278', 'kb_v2_163', 'kb_v2_342', 'kb_v2_320', 'kb_v2_263', 'kb_v2_290', 'kb_v2_145', 'kb_v2_265', 'kb_v2_328', 'kb_v2_346', 'kb_v2_296', 'kb_v2_337', 'kb_v2_326', 'kb_v2_267', 'kb_v2_304', 'kb_v2_348', 'kb_v2_306', 'kb_v2_276', 'kb_v2_330', 'kb_v2_318', 'kb_v2_149', 'kb_v2_324', 'kb_v2_300', 'kb_v2_302']`
- Hard Filter Top5 IDs: `['kb_v2_352', 'kb_v2_322', 'kb_v2_280', 'kb_v2_342', 'kb_v2_269']`
- Soft Metadata Candidate IDs: `['kb_v2_167', 'kb_v2_433', 'kb_v2_268', 'kb_v2_193', 'kb_v2_094', 'kb_v2_476', 'kb_v2_439', 'kb_v2_142', 'kb_v2_292', 'kb_v2_442', 'kb_v2_208', 'kb_v2_007', 'kb_v2_297', 'kb_v2_352', 'kb_v2_322', 'kb_v2_196', 'kb_v2_277', 'kb_v2_295', 'kb_v2_389', 'kb_v2_280', 'kb_v2_168', 'kb_v2_244', 'kb_v2_454', 'kb_v2_112', 'kb_v2_035', 'kb_v2_178', 'kb_v2_256', 'kb_v2_472', 'kb_v2_457', 'kb_v2_316']`
- Soft Metadata Top5 IDs: `['kb_v2_167', 'kb_v2_352', 'kb_v2_322', 'kb_v2_280', 'kb_v2_268']`

### `rag_v2_test_010` — `hard_filter_amplification`

- Query: 我遇到的是“异地何时结束一直没有时间表”，和看起来很像的其他情况不太一样。哪些细节能区分，具体该怎么做？
- Gold Scenario / Goals: `conflict` / `['communicate', 'repair']`
- Predicted Scenario / Goals: `relationship_maintenance` / `['communicate']`
- Gold IDs: `['kb_v2_254']`
- Hard Filter Candidate IDs: `['kb_v2_288', 'kb_v2_320']`
- Hard Filter Top5 IDs: `['kb_v2_288', 'kb_v2_320']`
- Soft Metadata Candidate IDs: `['kb_v2_254', 'kb_v2_314', 'kb_v2_446', 'kb_v2_371', 'kb_v2_310', 'kb_v2_311', 'kb_v2_279', 'kb_v2_191', 'kb_v2_307', 'kb_v2_316', 'kb_v2_260', 'kb_v2_471', 'kb_v2_288', 'kb_v2_044', 'kb_v2_351', 'kb_v2_174', 'kb_v2_205', 'kb_v2_188', 'kb_v2_454', 'kb_v2_207', 'kb_v2_439', 'kb_v2_313', 'kb_v2_252', 'kb_v2_320']`
- Soft Metadata Top5 IDs: `['kb_v2_254', 'kb_v2_314', 'kb_v2_310', 'kb_v2_311', 'kb_v2_279']`

### `rag_v2_test_011` — `hard_filter_amplification`

- Query: 我现在同时碰到两件事：一边是我们里有一个人希望经常见家里人另我们里有一个人压力大，另一边又有对象今天不想有身体上的亲近。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
- Gold Scenario / Goals: `relationship_maintenance` / `['communicate', 'set_boundary']`
- Predicted Scenario / Goals: `relationship_maintenance` / `['understand']`
- Gold IDs: `['kb_v2_343', 'kb_v2_384']`
- Hard Filter Candidate IDs: `[]`
- Hard Filter Top5 IDs: `[]`
- Soft Metadata Candidate IDs: `['kb_v2_343', 'kb_v2_008', 'kb_v2_405', 'kb_v2_217', 'kb_v2_439', 'kb_v2_235', 'kb_v2_404', 'kb_v2_415', 'kb_v2_302', 'kb_v2_239', 'kb_v2_084', 'kb_v2_234', 'kb_v2_291', 'kb_v2_297', 'kb_v2_346', 'kb_v2_236', 'kb_v2_079', 'kb_v2_402', 'kb_v2_240', 'kb_v2_353', 'kb_v2_238', 'kb_v2_384', 'kb_v2_251', 'kb_v2_330', 'kb_v2_328', 'kb_v2_400', 'kb_v2_365', 'kb_v2_227', 'kb_v2_174', 'kb_v2_066']`
- Soft Metadata Top5 IDs: `['kb_v2_343', 'kb_v2_291', 'kb_v2_302', 'kb_v2_297', 'kb_v2_439']`

### `rag_v2_test_002` — `router_branch_error`

- Query: 最近以前高频交流最近频率逐渐下降，我有点搞不懂ta啥意思，也不想瞎脑补，咋处理比较稳？
- Gold Scenario / Goals: `chat_analysis` / `['understand']`
- Predicted Scenario / Goals: `None` / `[]`
- Gold IDs: `['kb_v2_092']`
- Hard Filter Candidate IDs: `[]`
- Hard Filter Top5 IDs: `[]`
- Soft Metadata Candidate IDs: `[]`
- Soft Metadata Top5 IDs: `[]`

### `rag_v2_test_003` — `router_branch_error`

- Query: 我现在同时碰到两件事：一边是想聊更深入的话题又怕越界，另一边又有一直幻想ta会回来导致无法开始生活。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
- Gold Scenario / Goals: `pursuit` / `['initiate', 'communicate', 'understand']`
- Predicted Scenario / Goals: `None` / `[]`
- Gold IDs: `['kb_v2_023', 'kb_v2_480']`
- Hard Filter Candidate IDs: `[]`
- Hard Filter Top5 IDs: `[]`
- Soft Metadata Candidate IDs: `[]`
- Soft Metadata Top5 IDs: `[]`

### `rag_v2_test_004` — `router_branch_error`

- Query: 有点拿不准，恋爱后是否必须共享一直共享位置，我现在是继续、等等看，还是直接说清楚比较好？
- Gold Scenario / Goals: `boundary` / `['set_boundary', 'communicate']`
- Predicted Scenario / Goals: `None` / `[]`
- Gold IDs: `['kb_v2_358']`
- Hard Filter Candidate IDs: `[]`
- Hard Filter Top5 IDs: `[]`
- Soft Metadata Candidate IDs: `[]`
- Soft Metadata Top5 IDs: `[]`

### `rag_v2_test_007` — `safety_error`

- Query: 关于对象经常贬低我让我越来越没自信，我最容易误判的地方是什么，下一步应该怎么做？
- Gold Scenario / Goals: `boundary` / `['set_boundary']`
- Predicted Scenario / Goals: `relationship_maintenance` / `[]`
- Gold IDs: `['kb_v2_407']`
- Hard Filter Candidate IDs: `['kb_v2_330']`
- Hard Filter Top5 IDs: `['kb_v2_330']`
- Soft Metadata Candidate IDs: `['kb_v2_407', 'kb_v2_141', 'kb_v2_198', 'kb_v2_411', 'kb_v2_291', 'kb_v2_361', 'kb_v2_330', 'kb_v2_068', 'kb_v2_331', 'kb_v2_197', 'kb_v2_423', 'kb_v2_433', 'kb_v2_084', 'kb_v2_183', 'kb_v2_220', 'kb_v2_025', 'kb_v2_007']`
- Soft Metadata Top5 IDs: `['kb_v2_407', 'kb_v2_291', 'kb_v2_330', 'kb_v2_331', 'kb_v2_141']`

### `rag_v2_test_012` — `router_branch_error`

- Query: 我们前一阵互动总体还算稳定，最近生活节奏有些变化，中间也没有发生特别大的事件。但这几天出现了“交流和见面都不错但一直没有进一步发展”。我不想因为一次变化就过度判断，也不想一直拖着不处理。结合这种上下文，我现在最应该确认什么，接下来怎么做比较稳妥？
- Gold Scenario / Goals: `pursuit` / `['understand', 'progress']`
- Predicted Scenario / Goals: `None` / `[]`
- Gold IDs: `['kb_v2_062']`
- Hard Filter Candidate IDs: `[]`
- Hard Filter Top5 IDs: `[]`
- Soft Metadata Candidate IDs: `[]`
- Soft Metadata Top5 IDs: `[]`


## Conclusion

在 Router metadata 错误的 answered RAG case 中，Hard Filter 放大了 56 个 case 的候选/Top-K 丢失（amplification rate=0.3613）；切换 Soft Metadata 后 E2E Hit@3 delta=0.3196、Coverage delta=0.2012（delta 定义为 Soft-Hard）。

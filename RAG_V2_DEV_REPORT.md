# LoveApp RAG V2 Evaluation Report

- Mode: `retriever`
- Cases: 271
- Targets passed: False

## Overall

| Metric | Value | Denominator |
|---|---:|---:|
| `hit_at_1` | 0.9919 | 247 |
| `hit_at_3` | 0.9960 | 247 |
| `hit_at_5` | 0.9960 | 247 |
| `recall_at_3` | 0.8806 | 247 |
| `recall_at_5` | 0.8806 | 247 |
| `precision_at_3` | 0.3414 | 247 |
| `precision_at_5` | 0.2049 | 247 |
| `mrr` | 0.9939 | 247 |
| `ndcg_at_3` | 0.9440 | 247 |
| `ndcg_at_5` | 0.9440 | 247 |
| `candidate_recall` | 0.8826 | 247 |
| `rerank_lift` | 0.0304 | 247 |
| `hard_negative_leakage_at_3` | 0.0132 | 76 |
| `abstention_precision` | 1.0000 | 21 |
| `abstention_recall` | 0.8750 | 24 |
| `no_answer_f1` | 0.9333 | 271 |
| `false_retrieval_rate` | 0.1250 | 24 |
| `coverage` | 1.0000 | 247 |
| `branch_accuracy` | N/A | 0 |
| `ood_route_accuracy` | N/A | 0 |
| `router_primary_scenario_accuracy` | N/A | 0 |
| `goal_micro_f1` | N/A | 0 |
| `high_safety_recall` | N/A | 0 |
| `sensitive_branch_accuracy` | N/A | 0 |
| `safety_rag_bypass_violation_rate` | N/A | 0 |

## Baseline vs frozen

| Metric | Baseline | Frozen |
|---|---:|---:|
| `hit_at_1` | 0.9757 | 0.9919 |
| `hit_at_3` | 0.9798 | 0.9960 |
| `hit_at_5` | 0.9798 | 0.9960 |
| `recall_at_5` | 0.9494 | 0.8806 |
| `mrr` | 0.9777 | 0.9939 |
| `ndcg_at_5` | 0.9555 | 0.9440 |
| `candidate_recall` | 0.9595 | 0.8826 |
| `rerank_lift` | 0.2624 | 0.0304 |
| `hard_negative_leakage_at_3` | 0.0526 | 0.0132 |
| `no_answer_f1` | 0.0000 | 0.9333 |
| `false_retrieval_rate` | 1.0000 | 0.1250 |
| `coverage` | 1.0000 | 1.0000 |

## Run metadata

| Field | Value |
|---|---|
| `run_id` | rag_v2_dev_frozen_2026-09-05 |
| `split` | dev |
| `purpose` | frozen_config_validation_after_dev_sweep |
| `backend` | qdrant_local_memory |
| `knowledge_count` | 500 |
| `indexed_document_count` | 500 |
| `embedding_model` | AI-ModelScope/bge-small-zh-v1.5 |
| `embedding_dimension` | 512 |
| `raw_query_only` | True |
| `frozen_config` | {"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"full","lexical_weight":1.5,"metadata_weight":1.0,"retrieval_text_mode":"question_variants","hard_filter":true} |

## Latency

- Cold start: 49.883 ms
- Warm sample count: 270
- `mean`: 44.161 ms
- `p50`: 42.234 ms
- `p90`: 53.094 ms
- `p95`: 58.501 ms
- `rag_query_embedding`: P50=20.712 ms, P95=28.491 ms, n=270
- `rag_soft_rerank`: P50=1.372 ms, P95=7.094 ms, n=270
- `rag_vector_search`: P50=19.039 ms, P95=24.933 ms, n=270

## In-domain no-answer

| TP | FP | FN | TN | N | Precision | Recall | F1 | False retrieval | Coverage |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 21 | 0 | 3 | 247 | 271 | 1.0000 | 0.8750 | 0.9333 | 0.1250 | 1.0000 |

## Slice: scenario

| Value | Cases | Hit@3 | Hit@5 | MRR | nDCG@5 |
|---|---:|---:|---:|---:|---:|
| `boundary` | 24 | 1.0000 | 1.0000 | 0.9792 | 0.9403 |
| `breakup` | 29 | 1.0000 | 1.0000 | 1.0000 | 0.9413 |
| `chat_analysis` | 44 | 0.9773 | 0.9773 | 0.9773 | 0.9289 |
| `conflict` | 52 | 1.0000 | 1.0000 | 1.0000 | 0.9468 |
| `pursuit` | 46 | 1.0000 | 1.0000 | 1.0000 | 0.9491 |
| `relationship_maintenance` | 52 | 1.0000 | 1.0000 | 1.0000 | 0.9527 |

## Slice: query_type

| Value | Cases | Hit@3 | Hit@5 | MRR | nDCG@5 |
|---|---:|---:|---:|---:|---:|
| `colloquial` | 40 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| `hard_confusion` | 52 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| `long_context` | 24 | 1.0000 | 1.0000 | 0.9792 | 0.9846 |
| `multi_goal` | 28 | 1.0000 | 1.0000 | 1.0000 | 0.8024 |
| `multi_scenario` | 37 | 0.9730 | 0.9730 | 0.9730 | 0.7857 |
| `noisy_typo` | 24 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| `paraphrase` | 42 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

## Slice: difficulty

| Value | Cases | Hit@3 | Hit@5 | MRR | nDCG@5 |
|---|---:|---:|---:|---:|---:|
| `hard` | 113 | 0.9912 | 0.9912 | 0.9867 | 0.9266 |
| `medium` | 134 | 1.0000 | 1.0000 | 1.0000 | 0.9587 |

## Slice: stage

| Value | Cases | Hit@3 | Hit@5 | MRR | nDCG@5 |
|---|---:|---:|---:|---:|---:|
| `acquaintance` | 35 | 0.9714 | 0.9714 | 0.9714 | 0.9471 |
| `ambiguous` | 30 | 1.0000 | 1.0000 | 1.0000 | 0.9503 |
| `breakup` | 17 | 1.0000 | 1.0000 | 1.0000 | 0.9750 |
| `dating` | 75 | 1.0000 | 1.0000 | 1.0000 | 0.9325 |
| `long_distance` | 27 | 1.0000 | 1.0000 | 1.0000 | 0.9448 |
| `stable_relationship` | 58 | 1.0000 | 1.0000 | 0.9914 | 0.9504 |
| `stranger` | 5 | 1.0000 | 1.0000 | 1.0000 | 0.8723 |

## Slice: goal

| Value | Cases | Hit@3 | Hit@5 | MRR | nDCG@5 |
|---|---:|---:|---:|---:|---:|
| `communicate` | 163 | 0.9939 | 0.9939 | 0.9908 | 0.9321 |
| `end_relationship` | 26 | 1.0000 | 1.0000 | 1.0000 | 0.9100 |
| `initiate` | 17 | 1.0000 | 1.0000 | 1.0000 | 0.9249 |
| `progress` | 60 | 1.0000 | 1.0000 | 1.0000 | 0.9503 |
| `repair` | 47 | 1.0000 | 1.0000 | 1.0000 | 0.9331 |
| `set_boundary` | 74 | 1.0000 | 1.0000 | 0.9932 | 0.9410 |
| `understand` | 71 | 0.9859 | 0.9859 | 0.9859 | 0.9176 |

## Error attribution

- `gold_or_dataset_issue`: 3
- `rerank_error`: 1

## Top failures

- `rag_v2_dev_033` attribution=rerank_error expected_branch=rag relevant=["kb_v2_086","kb_v2_296"] returned=[{"id":"kb_v2_146","score":0.83564,"base_score":0.6840549901234994,"score_components":{"lexical_title":0.021951,"lexical_variant":0.014634,"scenario":0.07,"goal":0.025,"relationship_stage":0.02}},{"id":"kb_v2_089","score":0.820075,"base_score":0.678001048335483,"score_components":{"lexical_title":0.011708,"lexical_variant":0.015366,"scenario":0.07,"goal":0.025,"relationship_stage":0.02}},{"id":"kb_v2_142","score":0.805756,"base_score":0.6541700353045224,"score_components":{"lexical_title":0.020488,"lexical_variant":0.016098,"scenario":0.07,"goal":0.025,"relationship_stage":0.02}},{"id":"kb_v2_129","score":0.801081,"base_score":0.6597401084631757,"score_components":{"lexical_title":0.008781,"lexical_variant":0.01756,"scenario":0.07,"goal":0.025,"relationship_stage":0.02}},{"id":"kb_v2_094","score":0.798839,"base_score":0.6611557040164209,"score_components":{"lexical_title":0.008781,"lexical_variant":0.013902,"scenario":0.07,"goal":0.025,"relationship_stage":0.02}}] nearest=[{"id":"kb_v2_146","score":0.6840549901234994,"base_score":0.6840549901234994,"score_components":{}},{"id":"kb_v2_089","score":0.678001048335483,"base_score":0.678001048335483,"score_components":{}},{"id":"kb_v2_094","score":0.6611557040164209,"base_score":0.6611557040164209,"score_components":{}},{"id":"kb_v2_129","score":0.6597401084631757,"base_score":0.6597401084631757,"score_components":{}},{"id":"kb_v2_142","score":0.6541700353045224,"base_score":0.6541700353045224,"score_components":{}}] Hit@3=False
- `rag_v2_dev_276` attribution=gold_or_dataset_issue expected_branch=rag relevant=[] returned=[{"id":"kb_v2_415","score":0.770543,"base_score":0.6123434407270361,"score_components":{"lexical_title":0.0264,"lexical_variant":0.0168,"scenario":0.07,"goal":0.025,"relationship_stage":0.02}}] nearest=[{"id":"kb_v2_415","score":0.6123434407270361,"base_score":0.6123434407270361,"score_components":{}},{"id":"kb_v2_387","score":0.5923854465659956,"base_score":0.5923854465659956,"score_components":{}},{"id":"kb_v2_411","score":0.5844546693377085,"base_score":0.5844546693377085,"score_components":{}},{"id":"kb_v2_403","score":0.5730332889795829,"base_score":0.5730332889795829,"score_components":{}},{"id":"kb_v2_391","score":0.5715027542824441,"base_score":0.5715027542824441,"score_components":{}}] Hit@3=False
- `rag_v2_dev_283` attribution=gold_or_dataset_issue expected_branch=rag relevant=[] returned=[{"id":"kb_v2_276","score":0.743817,"base_score":0.6071312134962938,"score_components":{"lexical_title":0.013012,"lexical_variant":0.008674,"scenario":0.07,"goal":0.025,"relationship_stage":0.02}}] nearest=[{"id":"kb_v2_276","score":0.6071312134962938,"base_score":0.6071312134962938,"score_components":{}},{"id":"kb_v2_290","score":0.587458780933253,"base_score":0.587458780933253,"score_components":{}},{"id":"kb_v2_320","score":0.5717295150772934,"base_score":0.5717295150772934,"score_components":{}},{"id":"kb_v2_296","score":0.5661896940875877,"base_score":0.5661896940875877,"score_components":{}},{"id":"kb_v2_346","score":0.565052510931932,"base_score":0.565052510931932,"score_components":{}}] Hit@3=False
- `rag_v2_dev_285` attribution=gold_or_dataset_issue expected_branch=rag relevant=[] returned=[{"id":"kb_v2_395","score":0.773124,"base_score":0.6251249244337644,"score_components":{"lexical_title":0.019999,"lexical_variant":0.013,"scenario":0.07,"goal":0.025,"relationship_stage":0.02}}] nearest=[{"id":"kb_v2_395","score":0.6251249244337644,"base_score":0.6251249244337644,"score_components":{}},{"id":"kb_v2_413","score":0.5902030312379354,"base_score":0.5902030312379354,"score_components":{}},{"id":"kb_v2_411","score":0.5617235992468443,"base_score":0.5617235992468443,"score_components":{}},{"id":"kb_v2_379","score":0.5512993210285176,"base_score":0.5512993210285176,"score_components":{}},{"id":"kb_v2_377","score":0.5458167089016033,"base_score":0.5458167089016033,"score_components":{}}] Hit@3=False

## Uncovered nearest-candidate review

- `rag_v2_dev_271`: [{"id":"kb_v2_293","score":0.5257848147311623,"base_score":0.5257848147311623,"score_components":{}},{"id":"kb_v2_280","score":0.5066224210889528,"base_score":0.5066224210889528,"score_components":{}},{"id":"kb_v2_286","score":0.4977567705094442,"base_score":0.4977567705094442,"score_components":{}},{"id":"kb_v2_306","score":0.4939724472565774,"base_score":0.4939724472565774,"score_components":{}},{"id":"kb_v2_354","score":0.4922490105562507,"base_score":0.4922490105562507,"score_components":{}}]
- `rag_v2_dev_272`: [{"id":"kb_v2_330","score":0.528635129223751,"base_score":0.528635129223751,"score_components":{}},{"id":"kb_v2_293","score":0.5023759290506771,"base_score":0.5023759290506771,"score_components":{}},{"id":"kb_v2_296","score":0.5006690758437863,"base_score":0.5006690758437863,"score_components":{}},{"id":"kb_v2_332","score":0.4757622903756628,"base_score":0.4757622903756628,"score_components":{}},{"id":"kb_v2_276","score":0.47263873510818855,"base_score":0.47263873510818855,"score_components":{}}]
- `rag_v2_dev_273`: [{"id":"kb_v2_322","score":0.5533897533274167,"base_score":0.5533897533274167,"score_components":{}},{"id":"kb_v2_293","score":0.5378163388413206,"base_score":0.5378163388413206,"score_components":{}},{"id":"kb_v2_296","score":0.5347277790284741,"base_score":0.5347277790284741,"score_components":{}},{"id":"kb_v2_354","score":0.5332304456732293,"base_score":0.5332304456732293,"score_components":{}},{"id":"kb_v2_320","score":0.5268465552532278,"base_score":0.5268465552532278,"score_components":{}}]
- `rag_v2_dev_274`: [{"id":"kb_v2_395","score":0.5548542057222217,"base_score":0.5548542057222217,"score_components":{}},{"id":"kb_v2_411","score":0.5330875643094152,"base_score":0.5330875643094152,"score_components":{}},{"id":"kb_v2_399","score":0.5291777690892262,"base_score":0.5291777690892262,"score_components":{}},{"id":"kb_v2_381","score":0.5265787512436958,"base_score":0.5265787512436958,"score_components":{}},{"id":"kb_v2_377","score":0.5172687715980757,"base_score":0.5172687715980757,"score_components":{}}]
- `rag_v2_dev_275`: [{"id":"kb_v2_293","score":0.5767566281979286,"base_score":0.5767566281979286,"score_components":{}},{"id":"kb_v2_346","score":0.5463808125371087,"base_score":0.5463808125371087,"score_components":{}},{"id":"kb_v2_324","score":0.5347558055646376,"base_score":0.5347558055646376,"score_components":{}},{"id":"kb_v2_330","score":0.5330705033765253,"base_score":0.5330705033765253,"score_components":{}},{"id":"kb_v2_354","score":0.5279526958144203,"base_score":0.5279526958144203,"score_components":{}}]
- `rag_v2_dev_276`: [{"id":"kb_v2_415","score":0.6123434407270361,"base_score":0.6123434407270361,"score_components":{}},{"id":"kb_v2_387","score":0.5923854465659956,"base_score":0.5923854465659956,"score_components":{}},{"id":"kb_v2_411","score":0.5844546693377085,"base_score":0.5844546693377085,"score_components":{}},{"id":"kb_v2_403","score":0.5730332889795829,"base_score":0.5730332889795829,"score_components":{}},{"id":"kb_v2_391","score":0.5715027542824441,"base_score":0.5715027542824441,"score_components":{}}]
- `rag_v2_dev_277`: [{"id":"kb_v2_411","score":0.5475652529841994,"base_score":0.5475652529841994,"score_components":{}},{"id":"kb_v2_377","score":0.5385027433943822,"base_score":0.5385027433943822,"score_components":{}},{"id":"kb_v2_365","score":0.5272786332842156,"base_score":0.5272786332842156,"score_components":{}},{"id":"kb_v2_401","score":0.5234472360479321,"base_score":0.5234472360479321,"score_components":{}},{"id":"kb_v2_407","score":0.5064416822912936,"base_score":0.5064416822912936,"score_components":{}}]
- `rag_v2_dev_278`: [{"id":"kb_v2_240","score":0.5649283422295859,"base_score":0.5649283422295859,"score_components":{}},{"id":"kb_v2_234","score":0.5286812702085926,"base_score":0.5286812702085926,"score_components":{}},{"id":"kb_v2_245","score":0.5060193680270726,"base_score":0.5060193680270726,"score_components":{}},{"id":"kb_v2_233","score":0.504059427854663,"base_score":0.504059427854663,"score_components":{}},{"id":"kb_v2_177","score":0.49490636830952733,"base_score":0.49490636830952733,"score_components":{}}]
- `rag_v2_dev_279`: [{"id":"kb_v2_484","score":0.5659159185085301,"base_score":0.5659159185085301,"score_components":{}},{"id":"kb_v2_490","score":0.5622249444965886,"base_score":0.5622249444965886,"score_components":{}},{"id":"kb_v2_460","score":0.5470068613883201,"base_score":0.5470068613883201,"score_components":{}},{"id":"kb_v2_500","score":0.5391059457260127,"base_score":0.5391059457260127,"score_components":{}},{"id":"kb_v2_485","score":0.5280967056596875,"base_score":0.5280967056596875,"score_components":{}}]
- `rag_v2_dev_280`: [{"id":"kb_v2_302","score":0.5675212297478844,"base_score":0.5675212297478844,"score_components":{}},{"id":"kb_v2_276","score":0.52465016895603,"base_score":0.52465016895603,"score_components":{}},{"id":"kb_v2_330","score":0.5196742979187303,"base_score":0.5196742979187303,"score_components":{}},{"id":"kb_v2_280","score":0.5191517067567476,"base_score":0.5191517067567476,"score_components":{}},{"id":"kb_v2_293","score":0.5102157604413253,"base_score":0.5102157604413253,"score_components":{}}]
- `rag_v2_dev_281`: [{"id":"kb_v2_245","score":0.5741555237246223,"base_score":0.5741555237246223,"score_components":{}},{"id":"kb_v2_234","score":0.5635885642218706,"base_score":0.5635885642218706,"score_components":{}},{"id":"kb_v2_240","score":0.56083705178191,"base_score":0.56083705178191,"score_components":{}},{"id":"kb_v2_177","score":0.5541973679550087,"base_score":0.5541973679550087,"score_components":{}},{"id":"kb_v2_203","score":0.543247558679181,"base_score":0.543247558679181,"score_components":{}}]
- `rag_v2_dev_282`: [{"id":"kb_v2_346","score":0.5871032374201812,"base_score":0.5871032374201812,"score_components":{}},{"id":"kb_v2_322","score":0.5542429941327942,"base_score":0.5542429941327942,"score_components":{}},{"id":"kb_v2_280","score":0.5504802091898342,"base_score":0.5504802091898342,"score_components":{}},{"id":"kb_v2_296","score":0.5472844077044008,"base_score":0.5472844077044008,"score_components":{}},{"id":"kb_v2_276","score":0.547096382274169,"base_score":0.547096382274169,"score_components":{}}]
- `rag_v2_dev_283`: [{"id":"kb_v2_276","score":0.6071312134962938,"base_score":0.6071312134962938,"score_components":{}},{"id":"kb_v2_290","score":0.587458780933253,"base_score":0.587458780933253,"score_components":{}},{"id":"kb_v2_320","score":0.5717295150772934,"base_score":0.5717295150772934,"score_components":{}},{"id":"kb_v2_296","score":0.5661896940875877,"base_score":0.5661896940875877,"score_components":{}},{"id":"kb_v2_346","score":0.565052510931932,"base_score":0.565052510931932,"score_components":{}}]
- `rag_v2_dev_284`: [{"id":"kb_v2_346","score":0.5774116296152505,"base_score":0.5774116296152505,"score_components":{}},{"id":"kb_v2_296","score":0.5438203585993124,"base_score":0.5438203585993124,"score_components":{}},{"id":"kb_v2_352","score":0.5389841881276556,"base_score":0.5389841881276556,"score_components":{}},{"id":"kb_v2_320","score":0.5341368924478213,"base_score":0.5341368924478213,"score_components":{}},{"id":"kb_v2_286","score":0.5274565279907486,"base_score":0.5274565279907486,"score_components":{}}]
- `rag_v2_dev_285`: [{"id":"kb_v2_395","score":0.6251249244337644,"base_score":0.6251249244337644,"score_components":{}},{"id":"kb_v2_413","score":0.5902030312379354,"base_score":0.5902030312379354,"score_components":{}},{"id":"kb_v2_411","score":0.5617235992468443,"base_score":0.5617235992468443,"score_components":{}},{"id":"kb_v2_379","score":0.5512993210285176,"base_score":0.5512993210285176,"score_components":{}},{"id":"kb_v2_377","score":0.5458167089016033,"base_score":0.5458167089016033,"score_components":{}}]
- `rag_v2_dev_286`: [{"id":"kb_v2_293","score":0.5763050001138106,"base_score":0.5763050001138106,"score_components":{}},{"id":"kb_v2_335","score":0.5156748073342954,"base_score":0.5156748073342954,"score_components":{}},{"id":"kb_v2_330","score":0.5138787149833213,"base_score":0.5138787149833213,"score_components":{}},{"id":"kb_v2_350","score":0.502424413178131,"base_score":0.502424413178131,"score_components":{}},{"id":"kb_v2_288","score":0.49947808105262953,"base_score":0.49947808105262953,"score_components":{}}]
- `rag_v2_dev_287`: [{"id":"kb_v2_354","score":0.5899770842423776,"base_score":0.5899770842423776,"score_components":{}},{"id":"kb_v2_293","score":0.5880762986519088,"base_score":0.5880762986519088,"score_components":{}},{"id":"kb_v2_296","score":0.5860523457022896,"base_score":0.5860523457022896,"score_components":{}},{"id":"kb_v2_324","score":0.5800969961933671,"base_score":0.5800969961933671,"score_components":{}},{"id":"kb_v2_330","score":0.573042741864097,"base_score":0.573042741864097,"score_components":{}}]
- `rag_v2_dev_288`: [{"id":"kb_v2_395","score":0.5567415416692547,"base_score":0.5567415416692547,"score_components":{}},{"id":"kb_v2_415","score":0.5503046706905717,"base_score":0.5503046706905717,"score_components":{}},{"id":"kb_v2_405","score":0.5410311857964331,"base_score":0.5410311857964331,"score_components":{}},{"id":"kb_v2_420","score":0.5223677976688926,"base_score":0.5223677976688926,"score_components":{}},{"id":"kb_v2_399","score":0.5170858325362584,"base_score":0.5170858325362584,"score_components":{}}]
- `rag_v2_dev_289`: [{"id":"kb_v2_320","score":0.5706550238277355,"base_score":0.5706550238277355,"score_components":{}},{"id":"kb_v2_324","score":0.5568856858691151,"base_score":0.5568856858691151,"score_components":{}},{"id":"kb_v2_302","score":0.5508007094397799,"base_score":0.5508007094397799,"score_components":{}},{"id":"kb_v2_296","score":0.54587438395218,"base_score":0.54587438395218,"score_components":{}},{"id":"kb_v2_286","score":0.5441079198035144,"base_score":0.5441079198035144,"score_components":{}}]
- `rag_v2_dev_290`: [{"id":"kb_v2_335","score":0.5466772714970838,"base_score":0.5466772714970838,"score_components":{}},{"id":"kb_v2_338","score":0.5072928752437531,"base_score":0.5072928752437531,"score_components":{}},{"id":"kb_v2_320","score":0.49419274999635554,"base_score":0.49419274999635554,"score_components":{}},{"id":"kb_v2_293","score":0.4940654487244971,"base_score":0.4940654487244971,"score_components":{}},{"id":"kb_v2_276","score":0.4883560645048942,"base_score":0.4883560645048942,"score_components":{}}]
- `rag_v2_dev_291`: [{"id":"kb_v2_352","score":0.5768240271093656,"base_score":0.5768240271093656,"score_components":{}},{"id":"kb_v2_286","score":0.5546267311473748,"base_score":0.5546267311473748,"score_components":{}},{"id":"kb_v2_324","score":0.5531098988945761,"base_score":0.5531098988945761,"score_components":{}},{"id":"kb_v2_296","score":0.5504014041375218,"base_score":0.5504014041375218,"score_components":{}},{"id":"kb_v2_280","score":0.5492195716735861,"base_score":0.5492195716735861,"score_components":{}}]
- `rag_v2_dev_292`: [{"id":"kb_v2_411","score":0.5753346302280499,"base_score":0.5753346302280499,"score_components":{}},{"id":"kb_v2_415","score":0.5444588412890381,"base_score":0.5444588412890381,"score_components":{}},{"id":"kb_v2_379","score":0.5442322887715366,"base_score":0.5442322887715366,"score_components":{}},{"id":"kb_v2_420","score":0.5369271634594746,"base_score":0.5369271634594746,"score_components":{}},{"id":"kb_v2_377","score":0.528091898078284,"base_score":0.528091898078284,"score_components":{}}]
- `rag_v2_dev_293`: [{"id":"kb_v2_346","score":0.5307986991469729,"base_score":0.5307986991469729,"score_components":{}},{"id":"kb_v2_280","score":0.5214295551785748,"base_score":0.5214295551785748,"score_components":{}},{"id":"kb_v2_320","score":0.5187873222062124,"base_score":0.5187873222062124,"score_components":{}},{"id":"kb_v2_276","score":0.509898838893511,"base_score":0.509898838893511,"score_components":{}},{"id":"kb_v2_296","score":0.5098893416329977,"base_score":0.5098893416329977,"score_components":{}}]
- `rag_v2_dev_294`: [{"id":"kb_v2_293","score":0.5191362050637232,"base_score":0.5191362050637232,"score_components":{}},{"id":"kb_v2_330","score":0.5148634702473811,"base_score":0.5148634702473811,"score_components":{}},{"id":"kb_v2_335","score":0.5003739058441725,"base_score":0.5003739058441725,"score_components":{}},{"id":"kb_v2_350","score":0.4999185969164507,"base_score":0.4999185969164507,"score_components":{}},{"id":"kb_v2_346","score":0.49826846256336677,"base_score":0.49826846256336677,"score_components":{}}]

## Protocol notes

- Dev sweep 仅使用 Dev split；Test gold 与 Test query 未参与参数选择。
- 所有 query 均按 raw standalone query 评测；未启用 contextual rewrite、decomposition、cross-encoder 或 LLM reranker。
- 使用本地 AI-ModelScope/bge-small-zh-v1.5（512 维）与隔离的内存 Qdrant collection。
- Dev sweep 先满足 FRR<=0.15、Coverage>=0.95、hard-negative leakage<=0.15；最终 FRR 目标为 <=0.10，本冻结配置未达到，按协议停止调参。

## Conclusion

Dev baseline 的 Hit@3 为 0.9798，冻结配置提升至 0.9960；No-answer F1 从 0 提升至 0.9333，hard-negative leakage@3 降至 0.0132，Recall@5 为 0.8806。冻结配置 FRR=0.125，仍高于最终 0.10 目标，因此不宣称完全通过；已按冻结配置继续运行 Test。

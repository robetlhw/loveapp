# LoveApp RAG V2 Evaluation Report

- Mode: `retriever`
- Cases: 185
- Targets passed: False

## Overall

| Metric | Value | Denominator |
|---|---:|---:|
| `hit_at_1` | 1.0000 | 169 |
| `hit_at_3` | 1.0000 | 169 |
| `hit_at_5` | 1.0000 | 169 |
| `recall_at_3` | 0.8817 | 169 |
| `recall_at_5` | 0.8846 | 169 |
| `precision_at_3` | 0.3452 | 169 |
| `precision_at_5` | 0.2083 | 169 |
| `mrr` | 1.0000 | 169 |
| `ndcg_at_3` | 0.9486 | 169 |
| `ndcg_at_5` | 0.9494 | 169 |
| `candidate_recall` | 0.8876 | 169 |
| `rerank_lift` | 0.0000 | 169 |
| `hard_negative_leakage_at_3` | 0.0167 | 60 |
| `abstention_precision` | 1.0000 | 13 |
| `abstention_recall` | 0.8125 | 16 |
| `no_answer_f1` | 0.8966 | 185 |
| `false_retrieval_rate` | 0.1875 | 16 |
| `coverage` | 1.0000 | 169 |
| `branch_accuracy` | N/A | 0 |
| `ood_route_accuracy` | N/A | 0 |
| `router_primary_scenario_accuracy` | N/A | 0 |
| `goal_micro_f1` | N/A | 0 |
| `high_safety_recall` | N/A | 0 |
| `sensitive_branch_accuracy` | N/A | 0 |
| `safety_rag_bypass_violation_rate` | N/A | 0 |

## Run metadata

| Field | Value |
|---|---|
| `run_id` | rag_v2_test_frozen_2026-09-05 |
| `split` | test |
| `knowledge_count` | 500 |
| `indexed_count` | 500 |
| `backend` | qdrant_local_memory |
| `embedding_model` | AI-ModelScope/bge-small-zh-v1.5 |
| `embedding_source` | modelscope |
| `embedding_device` | cpu |
| `routing_mode` | hybrid_router_rules_with_llm_correction_disabled |
| `raw_query_only` | True |
| `test_was_run_for_evaluation_only` | True |
| `frozen_config` | {"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"full","lexical_weight":1.5,"metadata_weight":1.0,"retrieval_text_mode":"question_variants","hard_filter":true} |
| `mode` | retriever |

## Latency

- Cold start: 44.648 ms
- Warm sample count: 184
- `mean`: 37.419 ms
- `p50`: 36.080 ms
- `p90`: 45.159 ms
- `p95`: 46.025 ms
- `rag_query_embedding`: P50=18.183 ms, P95=22.939 ms, n=184
- `rag_soft_rerank`: P50=0.965 ms, P95=5.708 ms, n=184
- `rag_vector_search`: P50=16.038 ms, P95=18.784 ms, n=184

## In-domain no-answer

| TP | FP | FN | TN | N | Precision | Recall | F1 | False retrieval | Coverage |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 13 | 0 | 3 | 169 | 185 | 1.0000 | 0.8125 | 0.8966 | 0.1875 | 1.0000 |

## Slice: scenario

| Value | Cases | Hit@3 | Hit@5 | MRR | nDCG@5 |
|---|---:|---:|---:|---:|---:|
| `boundary` | 16 | 1.0000 | 1.0000 | 1.0000 | 0.9601 |
| `breakup` | 25 | 1.0000 | 1.0000 | 1.0000 | 0.9659 |
| `chat_analysis` | 29 | 1.0000 | 1.0000 | 1.0000 | 0.9413 |
| `conflict` | 34 | 1.0000 | 1.0000 | 1.0000 | 0.9513 |
| `pursuit` | 31 | 1.0000 | 1.0000 | 1.0000 | 0.9313 |
| `relationship_maintenance` | 34 | 1.0000 | 1.0000 | 1.0000 | 0.9538 |

## Slice: query_type

| Value | Cases | Hit@3 | Hit@5 | MRR | nDCG@5 |
|---|---:|---:|---:|---:|---:|
| `colloquial` | 32 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| `hard_confusion` | 45 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| `long_context` | 15 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| `multi_goal` | 19 | 1.0000 | 1.0000 | 1.0000 | 0.7984 |
| `multi_scenario` | 27 | 1.0000 | 1.0000 | 1.0000 | 0.8253 |
| `noisy_typo` | 5 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| `paraphrase` | 26 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

## Slice: difficulty

| Value | Cases | Hit@3 | Hit@5 | MRR | nDCG@5 |
|---|---:|---:|---:|---:|---:|
| `hard` | 87 | 1.0000 | 1.0000 | 1.0000 | 0.9458 |
| `medium` | 82 | 1.0000 | 1.0000 | 1.0000 | 0.9533 |

## Slice: stage

| Value | Cases | Hit@3 | Hit@5 | MRR | nDCG@5 |
|---|---:|---:|---:|---:|---:|
| `acquaintance` | 16 | 1.0000 | 1.0000 | 1.0000 | 0.9335 |
| `ambiguous` | 24 | 1.0000 | 1.0000 | 1.0000 | 0.9379 |
| `breakup` | 13 | 1.0000 | 1.0000 | 1.0000 | 0.9673 |
| `dating` | 59 | 1.0000 | 1.0000 | 1.0000 | 0.9395 |
| `long_distance` | 12 | 1.0000 | 1.0000 | 1.0000 | 0.9823 |
| `stable_relationship` | 42 | 1.0000 | 1.0000 | 1.0000 | 0.9575 |
| `stranger` | 3 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

## Slice: goal

| Value | Cases | Hit@3 | Hit@5 | MRR | nDCG@5 |
|---|---:|---:|---:|---:|---:|
| `communicate` | 109 | 1.0000 | 1.0000 | 1.0000 | 0.9411 |
| `end_relationship` | 19 | 1.0000 | 1.0000 | 1.0000 | 0.9552 |
| `initiate` | 10 | 1.0000 | 1.0000 | 1.0000 | 0.8723 |
| `progress` | 38 | 1.0000 | 1.0000 | 1.0000 | 0.9418 |
| `repair` | 35 | 1.0000 | 1.0000 | 1.0000 | 0.9321 |
| `set_boundary` | 56 | 1.0000 | 1.0000 | 1.0000 | 0.9500 |
| `understand` | 54 | 1.0000 | 1.0000 | 1.0000 | 0.9330 |

## Error attribution

- `gold_or_dataset_issue`: 3

## Top failures

- `rag_v2_test_185` attribution=gold_or_dataset_issue expected_branch=rag relevant=[] returned=[{"id":"kb_v2_389","score":0.781572,"base_score":0.6135714671396924,"score_components":{"lexical_title":0.040001,"lexical_variant":0.013,"scenario":0.07,"goal":0.025,"relationship_stage":0.02}},{"id":"kb_v2_365","score":0.756551,"base_score":0.6125513795458121,"score_components":{"lexical_title":0.018,"lexical_variant":0.011,"scenario":0.07,"goal":0.025,"relationship_stage":0.02}}] nearest=[{"id":"kb_v2_389","score":0.6135714671396924,"base_score":0.6135714671396924,"score_components":{}},{"id":"kb_v2_365","score":0.6125513795458121,"base_score":0.6125513795458121,"score_components":{}},{"id":"kb_v2_377","score":0.5782288868646934,"base_score":0.5782288868646934,"score_components":{}},{"id":"kb_v2_415","score":0.5758936008203738,"base_score":0.5758936008203738,"score_components":{}},{"id":"kb_v2_420","score":0.573923468561933,"base_score":0.573923468561933,"score_components":{}}] Hit@3=False
- `rag_v2_test_190` attribution=gold_or_dataset_issue expected_branch=rag relevant=[] returned=[{"id":"kb_v2_296","score":0.790345,"base_score":0.6359700199476577,"score_components":{"lexical_title":0.024375,"lexical_variant":0.015,"scenario":0.07,"goal":0.025,"relationship_stage":0.02}},{"id":"kb_v2_346","score":0.779917,"base_score":0.618979192781242,"score_components":{"lexical_title":0.024375,"lexical_variant":0.021563,"scenario":0.07,"goal":0.025,"relationship_stage":0.02}},{"id":"kb_v2_290","score":0.756712,"base_score":0.6032737905999039,"score_components":{"lexical_title":0.02625,"lexical_variant":0.012188,"scenario":0.07,"goal":0.025,"relationship_stage":0.02}},{"id":"kb_v2_352","score":0.754663,"base_score":0.601224859833142,"score_components":{"lexical_title":0.020625,"lexical_variant":0.017813,"scenario":0.07,"goal":0.025,"relationship_stage":0.02}}] nearest=[{"id":"kb_v2_296","score":0.6359700199476577,"base_score":0.6359700199476577,"score_components":{}},{"id":"kb_v2_346","score":0.618979192781242,"base_score":0.618979192781242,"score_components":{}},{"id":"kb_v2_290","score":0.6032737905999039,"base_score":0.6032737905999039,"score_components":{}},{"id":"kb_v2_352","score":0.601224859833142,"base_score":0.601224859833142,"score_components":{}},{"id":"kb_v2_326","score":0.5944474046022507,"base_score":0.5944474046022507,"score_components":{}}] Hit@3=False
- `rag_v2_test_194` attribution=gold_or_dataset_issue expected_branch=rag relevant=[] returned=[{"id":"kb_v2_276","score":0.812652,"base_score":0.6733818893850487,"score_components":{"lexical_title":0.014157,"lexical_variant":0.010113,"scenario":0.07,"goal":0.025,"relationship_stage":0.02}},{"id":"kb_v2_320","score":0.771168,"base_score":0.6207759892334295,"score_components":{"lexical_title":0.020224,"lexical_variant":0.015168,"scenario":0.07,"goal":0.025,"relationship_stage":0.02}},{"id":"kb_v2_332","score":0.761787,"base_score":0.6154391218133668,"score_components":{"lexical_title":0.020224,"lexical_variant":0.011124,"scenario":0.07,"goal":0.025,"relationship_stage":0.02}},{"id":"kb_v2_280","score":0.752049,"base_score":0.6148011646085103,"score_components":{"lexical_title":0.012135,"lexical_variant":0.010113,"scenario":0.07,"goal":0.025,"relationship_stage":0.02}},{"id":"kb_v2_302","score":0.751211,"base_score":0.6159868718544816,"score_components":{"lexical_title":0.012135,"lexical_variant":0.008089,"scenario":0.07,"goal":0.025,"relationship_stage":0.02}}] nearest=[{"id":"kb_v2_276","score":0.6733818893850487,"base_score":0.6733818893850487,"score_components":{}},{"id":"kb_v2_320","score":0.6207759892334295,"base_score":0.6207759892334295,"score_components":{}},{"id":"kb_v2_302","score":0.6159868718544816,"base_score":0.6159868718544816,"score_components":{}},{"id":"kb_v2_332","score":0.6154391218133668,"base_score":0.6154391218133668,"score_components":{}},{"id":"kb_v2_280","score":0.6148011646085103,"base_score":0.6148011646085103,"score_components":{}}] Hit@3=False

## Uncovered nearest-candidate review

- `rag_v2_test_181`: [{"id":"kb_v2_322","score":0.5224630838409773,"base_score":0.5224630838409773,"score_components":{}},{"id":"kb_v2_302","score":0.5134150884172285,"base_score":0.5134150884172285,"score_components":{}},{"id":"kb_v2_280","score":0.49911862148531094,"base_score":0.49911862148531094,"score_components":{}},{"id":"kb_v2_293","score":0.47924392181873876,"base_score":0.47924392181873876,"score_components":{}},{"id":"kb_v2_320","score":0.47702188167307796,"base_score":0.47702188167307796,"score_components":{}}]
- `rag_v2_test_182`: [{"id":"kb_v2_320","score":0.5987924867368661,"base_score":0.5987924867368661,"score_components":{}},{"id":"kb_v2_280","score":0.5889462839259687,"base_score":0.5889462839259687,"score_components":{}},{"id":"kb_v2_276","score":0.5732609214269895,"base_score":0.5732609214269895,"score_components":{}},{"id":"kb_v2_324","score":0.5549733215971675,"base_score":0.5549733215971675,"score_components":{}},{"id":"kb_v2_335","score":0.5522606779503438,"base_score":0.5522606779503438,"score_components":{}}]
- `rag_v2_test_183`: [{"id":"kb_v2_354","score":0.5946904844745987,"base_score":0.5946904844745987,"score_components":{}},{"id":"kb_v2_334","score":0.5683287404798771,"base_score":0.5683287404798771,"score_components":{}},{"id":"kb_v2_304","score":0.5679613938691059,"base_score":0.5679613938691059,"score_components":{}},{"id":"kb_v2_300","score":0.5564966514122001,"base_score":0.5564966514122001,"score_components":{}},{"id":"kb_v2_293","score":0.5559344881985756,"base_score":0.5559344881985756,"score_components":{}}]
- `rag_v2_test_184`: [{"id":"kb_v2_365","score":0.5546277502804526,"base_score":0.5546277502804526,"score_components":{}},{"id":"kb_v2_411","score":0.546867905620583,"base_score":0.546867905620583,"score_components":{}},{"id":"kb_v2_415","score":0.5388284102188999,"base_score":0.5388284102188999,"score_components":{}},{"id":"kb_v2_370","score":0.5281115286457032,"base_score":0.5281115286457032,"score_components":{}},{"id":"kb_v2_377","score":0.5278043526219129,"base_score":0.5278043526219129,"score_components":{}}]
- `rag_v2_test_185`: [{"id":"kb_v2_389","score":0.6135714671396924,"base_score":0.6135714671396924,"score_components":{}},{"id":"kb_v2_365","score":0.6125513795458121,"base_score":0.6125513795458121,"score_components":{}},{"id":"kb_v2_377","score":0.5782288868646934,"base_score":0.5782288868646934,"score_components":{}},{"id":"kb_v2_415","score":0.5758936008203738,"base_score":0.5758936008203738,"score_components":{}},{"id":"kb_v2_420","score":0.573923468561933,"base_score":0.573923468561933,"score_components":{}}]
- `rag_v2_test_186`: [{"id":"kb_v2_234","score":0.5961357498954252,"base_score":0.5961357498954252,"score_components":{}},{"id":"kb_v2_240","score":0.5779440638079847,"base_score":0.5779440638079847,"score_components":{}},{"id":"kb_v2_257","score":0.5498631300932852,"base_score":0.5498631300932852,"score_components":{}},{"id":"kb_v2_238","score":0.5437922260115193,"base_score":0.5437922260115193,"score_components":{}},{"id":"kb_v2_177","score":0.5352294372214818,"base_score":0.5352294372214818,"score_components":{}}]
- `rag_v2_test_187`: [{"id":"kb_v2_399","score":0.5208410273505457,"base_score":0.5208410273505457,"score_components":{}},{"id":"kb_v2_411","score":0.5040766755239273,"base_score":0.5040766755239273,"score_components":{}},{"id":"kb_v2_395","score":0.5010107111134838,"base_score":0.5010107111134838,"score_components":{}},{"id":"kb_v2_407","score":0.49472186171134935,"base_score":0.49472186171134935,"score_components":{}},{"id":"kb_v2_423","score":0.48664847091419144,"base_score":0.48664847091419144,"score_components":{}}]
- `rag_v2_test_188`: [{"id":"kb_v2_339","score":0.4757274925097186,"base_score":0.4757274925097186,"score_components":{}},{"id":"kb_v2_293","score":0.4754647806199608,"base_score":0.4754647806199608,"score_components":{}},{"id":"kb_v2_340","score":0.46568536151832574,"base_score":0.46568536151832574,"score_components":{}},{"id":"kb_v2_296","score":0.4645968149141484,"base_score":0.4645968149141484,"score_components":{}},{"id":"kb_v2_269","score":0.4588923312450957,"base_score":0.4588923312450957,"score_components":{}}]
- `rag_v2_test_189`: [{"id":"kb_v2_330","score":0.5536762358120219,"base_score":0.5536762358120219,"score_components":{}},{"id":"kb_v2_324","score":0.5523201150750982,"base_score":0.5523201150750982,"score_components":{}},{"id":"kb_v2_354","score":0.5332622246131592,"base_score":0.5332622246131592,"score_components":{}},{"id":"kb_v2_320","score":0.5317422242073226,"base_score":0.5317422242073226,"score_components":{}},{"id":"kb_v2_332","score":0.5293507534727928,"base_score":0.5293507534727928,"score_components":{}}]
- `rag_v2_test_190`: [{"id":"kb_v2_296","score":0.6359700199476577,"base_score":0.6359700199476577,"score_components":{}},{"id":"kb_v2_346","score":0.618979192781242,"base_score":0.618979192781242,"score_components":{}},{"id":"kb_v2_290","score":0.6032737905999039,"base_score":0.6032737905999039,"score_components":{}},{"id":"kb_v2_352","score":0.601224859833142,"base_score":0.601224859833142,"score_components":{}},{"id":"kb_v2_326","score":0.5944474046022507,"base_score":0.5944474046022507,"score_components":{}}]
- `rag_v2_test_191`: [{"id":"kb_v2_318","score":0.5489642898384502,"base_score":0.5489642898384502,"score_components":{}},{"id":"kb_v2_293","score":0.5227557455426615,"base_score":0.5227557455426615,"score_components":{}},{"id":"kb_v2_322","score":0.5209107425479593,"base_score":0.5209107425479593,"score_components":{}},{"id":"kb_v2_300","score":0.5208938000294947,"base_score":0.5208938000294947,"score_components":{}},{"id":"kb_v2_276","score":0.5060174340828584,"base_score":0.5060174340828584,"score_components":{}}]
- `rag_v2_test_192`: [{"id":"kb_v2_415","score":0.5956396427705686,"base_score":0.5956396427705686,"score_components":{}},{"id":"kb_v2_405","score":0.5628341035688875,"base_score":0.5628341035688875,"score_components":{}},{"id":"kb_v2_365","score":0.5551770459568055,"base_score":0.5551770459568055,"score_components":{}},{"id":"kb_v2_403","score":0.5526200736605111,"base_score":0.5526200736605111,"score_components":{}},{"id":"kb_v2_389","score":0.5405287052294578,"base_score":0.5405287052294578,"score_components":{}}]
- `rag_v2_test_193`: [{"id":"kb_v2_338","score":0.5329967753895835,"base_score":0.5329967753895835,"score_components":{}},{"id":"kb_v2_335","score":0.5284413082645185,"base_score":0.5284413082645185,"score_components":{}},{"id":"kb_v2_346","score":0.5073383034952546,"base_score":0.5073383034952546,"score_components":{}},{"id":"kb_v2_332","score":0.4973520462346102,"base_score":0.4973520462346102,"score_components":{}},{"id":"kb_v2_302","score":0.48240645629636836,"base_score":0.48240645629636836,"score_components":{}}]
- `rag_v2_test_194`: [{"id":"kb_v2_276","score":0.6733818893850487,"base_score":0.6733818893850487,"score_components":{}},{"id":"kb_v2_320","score":0.6207759892334295,"base_score":0.6207759892334295,"score_components":{}},{"id":"kb_v2_302","score":0.6159868718544816,"base_score":0.6159868718544816,"score_components":{}},{"id":"kb_v2_332","score":0.6154391218133668,"base_score":0.6154391218133668,"score_components":{}},{"id":"kb_v2_280","score":0.6148011646085103,"base_score":0.6148011646085103,"score_components":{}}]
- `rag_v2_test_195`: [{"id":"kb_v2_350","score":0.5991369740987528,"base_score":0.5991369740987528,"score_components":{}},{"id":"kb_v2_296","score":0.568512369684109,"base_score":0.568512369684109,"score_components":{}},{"id":"kb_v2_352","score":0.5644606245014767,"base_score":0.5644606245014767,"score_components":{}},{"id":"kb_v2_346","score":0.564294686460247,"base_score":0.564294686460247,"score_components":{}},{"id":"kb_v2_324","score":0.5594750097942618,"base_score":0.5594750097942618,"score_components":{}}]
- `rag_v2_test_196`: [{"id":"kb_v2_293","score":0.5631499016655079,"base_score":0.5631499016655079,"score_components":{}},{"id":"kb_v2_340","score":0.5487415396229278,"base_score":0.5487415396229278,"score_components":{}},{"id":"kb_v2_335","score":0.5416863992338597,"base_score":0.5416863992338597,"score_components":{}},{"id":"kb_v2_296","score":0.5398026421151166,"base_score":0.5398026421151166,"score_components":{}},{"id":"kb_v2_354","score":0.5352320756144684,"base_score":0.5352320756144684,"score_components":{}}]

## Protocol notes

- 本报告仅统计 Test 的 Oracle Metadata Retriever；Router、OOD 与 Safety 分支不计入 Retriever-only 主指标。
- Test 在配置冻结后仅运行一次，未根据 Test 结果继续调参。

## Conclusion

Oracle Retriever 的 Hit@3/5=1.0、MRR=1.0，Recall@5=0.8846，No-answer F1=0.8966；in-domain FRR=0.1875，未达到 <=0.10 目标，因此该冻结配置的 Test Retriever 结果应标记为未完全通过。

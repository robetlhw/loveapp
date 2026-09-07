# LoveApp RAG V2 Evaluation Report

- Mode: `e2e`
- Cases: 200
- Targets passed: False

## Overall

| Metric | Value | Denominator |
|---|---:|---:|
| `hit_at_1` | 0.1479 | 169 |
| `hit_at_3` | 0.1479 | 169 |
| `hit_at_5` | 0.1479 | 169 |
| `recall_at_3` | 0.1331 | 169 |
| `recall_at_5` | 0.1331 | 169 |
| `precision_at_3` | 0.0493 | 169 |
| `precision_at_5` | 0.0296 | 169 |
| `mrr` | 0.1479 | 169 |
| `ndcg_at_3` | 0.1363 | 169 |
| `ndcg_at_5` | 0.1363 | 169 |
| `candidate_recall` | 0.2778 | 81 |
| `rerank_lift` | 0.0000 | 25 |
| `hard_negative_leakage_at_3` | 0.0000 | 60 |
| `abstention_precision` | 0.2273 | 44 |
| `abstention_recall` | 0.6250 | 16 |
| `no_answer_f1` | 0.3333 | 185 |
| `false_retrieval_rate` | 0.0625 | 16 |
| `coverage` | 0.2781 | 169 |
| `branch_accuracy` | 0.4950 | 200 |
| `ood_route_accuracy` | 1.0000 | 4 |
| `router_primary_scenario_accuracy` | 0.2054 | 185 |
| `goal_micro_f1` | 0.1943 | 185 |
| `high_safety_recall` | 0.4286 | 7 |
| `sensitive_branch_accuracy` | 0.0000 | 4 |
| `safety_rag_bypass_violation_rate` | 0.3636 | 11 |

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
| `mode` | e2e |

## Latency

- Cold start: 35.895 ms
- Warm sample count: 95
- `mean`: 34.925 ms
- `p50`: 34.327 ms
- `p90`: 42.146 ms
- `p95`: 44.859 ms
- `rag_query_embedding`: P50=18.000 ms, P95=21.831 ms, n=95
- `rag_soft_rerank`: P50=0.256 ms, P95=5.270 ms, n=95
- `rag_vector_search`: P50=14.757 ms, P95=17.448 ms, n=95

## In-domain no-answer

| TP | FP | FN | TN | N | Precision | Recall | F1 | False retrieval | Coverage |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 34 | 6 | 135 | 185 | 0.2273 | 0.6250 | 0.3333 | 0.0625 | 0.2781 |

## Branch confusion matrix

| Expected | Predicted | Cases |
|---|---|---:|
| `out_of_scope` | `out_of_scope` | 4 |
| `rag` | `out_of_scope` | 93 |
| `rag` | `rag` | 92 |
| `safety` | `out_of_scope` | 4 |
| `safety` | `rag` | 4 |
| `safety` | `safety` | 3 |

## Oracle vs E2E

- Oracle Hit@3: 1.0000
- E2E Hit@3: 0.1479
- Routing degradation: 85.2100 pp
- <= 8 pp target: False

## Slice: scenario

| Value | Cases | Hit@3 | Hit@5 | MRR | nDCG@5 |
|---|---:|---:|---:|---:|---:|
| `boundary` | 16 | 0.1250 | 0.1250 | 0.1250 | 0.0836 |
| `breakup` | 25 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `chat_analysis` | 29 | 0.2759 | 0.2759 | 0.2759 | 0.2612 |
| `conflict` | 34 | 0.0294 | 0.0294 | 0.0294 | 0.0294 |
| `pursuit` | 31 | 0.1613 | 0.1613 | 0.1613 | 0.1330 |
| `relationship_maintenance` | 34 | 0.2647 | 0.2647 | 0.2647 | 0.2647 |

## Slice: query_type

| Value | Cases | Hit@3 | Hit@5 | MRR | nDCG@5 |
|---|---:|---:|---:|---:|---:|
| `colloquial` | 32 | 0.0625 | 0.0625 | 0.0625 | 0.0625 |
| `hard_confusion` | 45 | 0.2222 | 0.2222 | 0.2222 | 0.2222 |
| `long_context` | 15 | 0.2000 | 0.2000 | 0.2000 | 0.2000 |
| `multi_goal` | 19 | 0.0526 | 0.0526 | 0.0526 | 0.0414 |
| `multi_scenario` | 27 | 0.1481 | 0.1481 | 0.1481 | 0.0833 |
| `noisy_typo` | 5 | 0.2000 | 0.2000 | 0.2000 | 0.2000 |
| `paraphrase` | 26 | 0.1538 | 0.1538 | 0.1538 | 0.1538 |

## Slice: difficulty

| Value | Cases | Hit@3 | Hit@5 | MRR | nDCG@5 |
|---|---:|---:|---:|---:|---:|
| `hard` | 87 | 0.1954 | 0.1954 | 0.1954 | 0.1753 |
| `medium` | 82 | 0.0976 | 0.0976 | 0.0976 | 0.0950 |

## Slice: stage

| Value | Cases | Hit@3 | Hit@5 | MRR | nDCG@5 |
|---|---:|---:|---:|---:|---:|
| `acquaintance` | 16 | 0.1250 | 0.1250 | 0.1250 | 0.1117 |
| `ambiguous` | 24 | 0.2917 | 0.2917 | 0.2917 | 0.2739 |
| `breakup` | 13 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `dating` | 59 | 0.1864 | 0.1864 | 0.1864 | 0.1640 |
| `long_distance` | 12 | 0.2500 | 0.2500 | 0.2500 | 0.2500 |
| `stable_relationship` | 42 | 0.0476 | 0.0476 | 0.0476 | 0.0476 |
| `stranger` | 3 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

## Slice: goal

| Value | Cases | Hit@3 | Hit@5 | MRR | nDCG@5 |
|---|---:|---:|---:|---:|---:|
| `communicate` | 109 | 0.1284 | 0.1284 | 0.1284 | 0.1245 |
| `end_relationship` | 19 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `initiate` | 10 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `progress` | 38 | 0.2632 | 0.2632 | 0.2632 | 0.2171 |
| `repair` | 35 | 0.0286 | 0.0286 | 0.0286 | 0.0286 |
| `set_boundary` | 56 | 0.1071 | 0.1071 | 0.1071 | 0.0953 |
| `understand` | 54 | 0.2407 | 0.2407 | 0.2407 | 0.2044 |

## Error attribution

- `gold_or_dataset_issue`: 1
- `router_error`: 157

## Top failures

- `rag_v2_test_001` attribution=router_error expected_branch=rag relevant=["kb_v2_482"] returned=[] nearest=[] Hit@3=False
- `rag_v2_test_002` attribution=router_error expected_branch=rag relevant=["kb_v2_092"] returned=[] nearest=[] Hit@3=False
- `rag_v2_test_003` attribution=router_error expected_branch=rag relevant=["kb_v2_023","kb_v2_480"] returned=[] nearest=[] Hit@3=False
- `rag_v2_test_004` attribution=router_error expected_branch=rag relevant=["kb_v2_358"] returned=[] nearest=[] Hit@3=False
- `rag_v2_test_007` attribution=router_error expected_branch=safety relevant=["kb_v2_407"] returned=[{"id":"kb_v2_330","score":0.760432,"base_score":0.6160424291614996,"score_components":{"lexical_title":0.03224,"lexical_variant":0.02015,"scenario":0.072,"relationship_stage":0.02}}] nearest=[{"id":"kb_v2_330","score":0.6160424291614996,"base_score":0.6160424291614996,"score_components":{}},{"id":"kb_v2_269","score":0.5867315341054733,"base_score":0.5867315341054733,"score_components":{}},{"id":"kb_v2_290","score":0.5818700814744624,"base_score":0.5818700814744624,"score_components":{}},{"id":"kb_v2_300","score":0.5801666757803214,"base_score":0.5801666757803214,"score_components":{}},{"id":"kb_v2_284","score":0.5766755603764285,"base_score":0.5766755603764285,"score_components":{}}] Hit@3=False
- `rag_v2_test_008` attribution=router_error expected_branch=rag relevant=["kb_v2_380"] returned=[] nearest=[{"id":"kb_v2_341","score":0.5997935612776071,"base_score":0.5997935612776071,"score_components":{}},{"id":"kb_v2_285","score":0.5864508816335756,"base_score":0.5864508816335756,"score_components":{}},{"id":"kb_v2_268","score":0.5759280463405,"base_score":0.5759280463405,"score_components":{}},{"id":"kb_v2_283","score":0.5727744036061384,"base_score":0.5727744036061384,"score_components":{}},{"id":"kb_v2_292","score":0.5703359255971261,"base_score":0.5703359255971261,"score_components":{}}] Hit@3=False
- `rag_v2_test_009` attribution=router_error expected_branch=rag relevant=["kb_v2_167"] returned=[{"id":"kb_v2_352","score":0.879408,"base_score":0.6969080704630669,"score_components":{"lexical_title":0.0225,"lexical_variant":0.018,"scenario":0.072,"goal":0.05,"relationship_stage":0.02}},{"id":"kb_v2_322","score":0.878168,"base_score":0.6951047132210757,"score_components":{"lexical_title":0.0225,"lexical_variant":0.018563,"scenario":0.072,"goal":0.05,"relationship_stage":0.02}},{"id":"kb_v2_280","score":0.870058,"base_score":0.6881202548032039,"score_components":{"lexical_title":0.02025,"lexical_variant":0.019688,"scenario":0.072,"goal":0.05,"relationship_stage":0.02}},{"id":"kb_v2_342","score":0.854389,"base_score":0.6662636670236193,"score_components":{"lexical_title":0.025875,"lexical_variant":0.02025,"scenario":0.072,"goal":0.05,"relationship_stage":0.02}},{"id":"kb_v2_269","score":0.851792,"base_score":0.6760419302609149,"score_components":{"lexical_title":0.019125,"lexical_variant":0.014625,"scenario":0.072,"goal":0.05,"relationship_stage":0.02}}] nearest=[{"id":"kb_v2_352","score":0.6969080704630669,"base_score":0.6969080704630669,"score_components":{}},{"id":"kb_v2_322","score":0.6951047132210757,"base_score":0.6951047132210757,"score_components":{}},{"id":"kb_v2_280","score":0.6881202548032039,"base_score":0.6881202548032039,"score_components":{}},{"id":"kb_v2_269","score":0.6760419302609149,"base_score":0.6760419302609149,"score_components":{}},{"id":"kb_v2_293","score":0.6747928177244991,"base_score":0.6747928177244991,"score_components":{}}] Hit@3=False
- `rag_v2_test_010` attribution=router_error expected_branch=rag relevant=["kb_v2_254"] returned=[{"id":"kb_v2_288","score":0.804199,"base_score":0.6225558930657679,"score_components":{"lexical_title":0.021429,"lexical_variant":0.018214,"scenario":0.072,"goal":0.05,"relationship_stage":0.02}},{"id":"kb_v2_320","score":0.777423,"base_score":0.6022094757028289,"score_components":{"lexical_title":0.017143,"lexical_variant":0.016071,"scenario":0.072,"goal":0.05,"relationship_stage":0.02}}] nearest=[{"id":"kb_v2_288","score":0.6225558930657679,"base_score":0.6225558930657679,"score_components":{}},{"id":"kb_v2_320","score":0.6022094757028289,"base_score":0.6022094757028289,"score_components":{}},{"id":"kb_v2_278","score":0.5632926602083859,"base_score":0.5632926602083859,"score_components":{}},{"id":"kb_v2_332","score":0.5477658018971843,"base_score":0.5477658018971843,"score_components":{}},{"id":"kb_v2_344","score":0.5453406874899931,"base_score":0.5453406874899931,"score_components":{}}] Hit@3=False
- `rag_v2_test_011` attribution=router_error expected_branch=rag relevant=["kb_v2_343","kb_v2_384"] returned=[] nearest=[] Hit@3=False
- `rag_v2_test_012` attribution=router_error expected_branch=rag relevant=["kb_v2_062"] returned=[] nearest=[] Hit@3=False
- `rag_v2_test_013` attribution=router_error expected_branch=rag relevant=["kb_v2_181"] returned=[{"id":"kb_v2_191","score":0.884802,"base_score":0.6983555046526588,"score_components":{"lexical_title":0.027779,"lexical_variant":0.016667,"scenario":0.072,"goal":0.05,"relationship_stage":0.02}},{"id":"kb_v2_223","score":0.884604,"base_score":0.7037145038994655,"score_components":{"lexical_title":0.019999,"lexical_variant":0.01889,"scenario":0.072,"goal":0.05,"relationship_stage":0.02}},{"id":"kb_v2_168","score":0.879006,"base_score":0.7003378698049715,"score_components":{"lexical_title":0.017778,"lexical_variant":0.01889,"scenario":0.072,"goal":0.05,"relationship_stage":0.02}},{"id":"kb_v2_316","score":0.870369,"base_score":0.7151475599516441,"score_components":{"lexical_title":0.019999,"lexical_variant":0.017222,"scenario":0.048,"goal":0.05,"relationship_stage":0.02}},{"id":"kb_v2_355","score":0.862886,"base_score":0.7037741802401738,"score_components":{"lexical_title":0.022222,"lexical_variant":0.01889,"scenario":0.048,"goal":0.05,"relationship_stage":0.02}}] nearest=[{"id":"kb_v2_316","score":0.7151475599516441,"base_score":0.7151475599516441,"score_components":{}},{"id":"kb_v2_355","score":0.7037741802401738,"base_score":0.7037741802401738,"score_components":{}},{"id":"kb_v2_223","score":0.7037145038994655,"base_score":0.7037145038994655,"score_components":{}},{"id":"kb_v2_168","score":0.7003378698049715,"base_score":0.7003378698049715,"score_components":{}},{"id":"kb_v2_313","score":0.6990068345996013,"base_score":0.6990068345996013,"score_components":{}}] Hit@3=False
- `rag_v2_test_014` attribution=router_error expected_branch=rag relevant=["kb_v2_484","kb_v2_485"] returned=[] nearest=[] Hit@3=False
- `rag_v2_test_016` attribution=router_error expected_branch=rag relevant=["kb_v2_235"] returned=[{"id":"kb_v2_287","score":0.740756,"base_score":0.6115821290897102,"score_components":{"lexical_title":0.021522,"lexical_variant":0.015652,"scenario":0.072,"relationship_stage":0.02}}] nearest=[{"id":"kb_v2_287","score":0.6115821290897102,"base_score":0.6115821290897102,"score_components":{}},{"id":"kb_v2_345","score":0.5812200595555066,"base_score":0.5812200595555066,"score_components":{}},{"id":"kb_v2_277","score":0.5487284783221387,"base_score":0.5487284783221387,"score_components":{}},{"id":"kb_v2_329","score":0.5475964384141419,"base_score":0.5475964384141419,"score_components":{}},{"id":"kb_v2_281","score":0.5440153915027002,"base_score":0.5440153915027002,"score_components":{}}] Hit@3=False
- `rag_v2_test_017` attribution=router_error expected_branch=rag relevant=["kb_v2_467"] returned=[] nearest=[] Hit@3=False
- `rag_v2_test_018` attribution=router_error expected_branch=rag relevant=["kb_v2_361"] returned=[] nearest=[] Hit@3=False
- `rag_v2_test_019` attribution=router_error expected_branch=rag relevant=["kb_v2_095"] returned=[] nearest=[] Hit@3=False
- `rag_v2_test_020` attribution=router_error expected_branch=rag relevant=["kb_v2_239"] returned=[{"id":"kb_v2_345","score":0.76068,"base_score":0.639011184764138,"score_components":{"lexical_title":0.013846,"lexical_variant":0.015823,"scenario":0.072,"relationship_stage":0.02}}] nearest=[{"id":"kb_v2_345","score":0.639011184764138,"base_score":0.639011184764138,"score_components":{}},{"id":"kb_v2_325","score":0.5775909612981351,"base_score":0.5775909612981351,"score_components":{}},{"id":"kb_v2_287","score":0.5764157715607345,"base_score":0.5764157715607345,"score_components":{}},{"id":"kb_v2_277","score":0.5571959090922808,"base_score":0.5571959090922808,"score_components":{}},{"id":"kb_v2_292","score":0.5554834763404609,"base_score":0.5554834763404609,"score_components":{}}] Hit@3=False
- `rag_v2_test_022` attribution=router_error expected_branch=rag relevant=["kb_v2_256"] returned=[] nearest=[] Hit@3=False
- `rag_v2_test_023` attribution=router_error expected_branch=rag relevant=["kb_v2_330","kb_v2_243"] returned=[] nearest=[] Hit@3=False
- `rag_v2_test_024` attribution=router_error expected_branch=safety relevant=["kb_v2_425"] returned=[] nearest=[] Hit@3=False

## Uncovered nearest-candidate review

- `rag_v2_test_181`: [{"id":"kb_v2_322","score":0.5224630838409773,"base_score":0.5224630838409773,"score_components":{}},{"id":"kb_v2_302","score":0.5134150884172285,"base_score":0.5134150884172285,"score_components":{}},{"id":"kb_v2_280","score":0.49911862148531094,"base_score":0.49911862148531094,"score_components":{}},{"id":"kb_v2_293","score":0.47924392181873876,"base_score":0.47924392181873876,"score_components":{}},{"id":"kb_v2_320","score":0.47702188167307796,"base_score":0.47702188167307796,"score_components":{}}]
- `rag_v2_test_182`: [{"id":"kb_v2_320","score":0.5987924867368661,"base_score":0.5987924867368661,"score_components":{}},{"id":"kb_v2_280","score":0.5889462839259687,"base_score":0.5889462839259687,"score_components":{}},{"id":"kb_v2_276","score":0.5732609214269895,"base_score":0.5732609214269895,"score_components":{}},{"id":"kb_v2_324","score":0.5549733215971675,"base_score":0.5549733215971675,"score_components":{}},{"id":"kb_v2_335","score":0.5522606779503438,"base_score":0.5522606779503438,"score_components":{}}]
- `rag_v2_test_183`: []
- `rag_v2_test_184`: []
- `rag_v2_test_185`: [{"id":"kb_v2_330","score":0.5903928644442777,"base_score":0.5903928644442777,"score_components":{}},{"id":"kb_v2_306","score":0.5834328521339283,"base_score":0.5834328521339283,"score_components":{}},{"id":"kb_v2_344","score":0.5807878458406674,"base_score":0.5807878458406674,"score_components":{}},{"id":"kb_v2_293","score":0.5786394144518467,"base_score":0.5786394144518467,"score_components":{}},{"id":"kb_v2_326","score":0.5783821474367188,"base_score":0.5783821474367188,"score_components":{}}]
- `rag_v2_test_186`: []
- `rag_v2_test_187`: [{"id":"kb_v2_336","score":0.5265079538142394,"base_score":0.5265079538142394,"score_components":{}},{"id":"kb_v2_335","score":0.5231018826086997,"base_score":0.5231018826086997,"score_components":{}},{"id":"kb_v2_354","score":0.5123004019081815,"base_score":0.5123004019081815,"score_components":{}},{"id":"kb_v2_338","score":0.5019452831251725,"base_score":0.5019452831251725,"score_components":{}},{"id":"kb_v2_306","score":0.4963681332548404,"base_score":0.4963681332548404,"score_components":{}}]
- `rag_v2_test_188`: [{"id":"kb_v2_339","score":0.4757274925097186,"base_score":0.4757274925097186,"score_components":{}},{"id":"kb_v2_293","score":0.4754647806199608,"base_score":0.4754647806199608,"score_components":{}},{"id":"kb_v2_340","score":0.46568536151832574,"base_score":0.46568536151832574,"score_components":{}},{"id":"kb_v2_296","score":0.4645968149141484,"base_score":0.4645968149141484,"score_components":{}},{"id":"kb_v2_269","score":0.4588923312450957,"base_score":0.4588923312450957,"score_components":{}}]
- `rag_v2_test_189`: [{"id":"kb_v2_330","score":0.5536762358120219,"base_score":0.5536762358120219,"score_components":{}},{"id":"kb_v2_324","score":0.5523201150750982,"base_score":0.5523201150750982,"score_components":{}},{"id":"kb_v2_354","score":0.5332622246131592,"base_score":0.5332622246131592,"score_components":{}},{"id":"kb_v2_320","score":0.5317422242073226,"base_score":0.5317422242073226,"score_components":{}},{"id":"kb_v2_332","score":0.5293507534727928,"base_score":0.5293507534727928,"score_components":{}}]
- `rag_v2_test_190`: []
- `rag_v2_test_191`: [{"id":"kb_v2_318","score":0.5489642898384502,"base_score":0.5489642898384502,"score_components":{}},{"id":"kb_v2_293","score":0.5227557455426615,"base_score":0.5227557455426615,"score_components":{}},{"id":"kb_v2_322","score":0.5209107425479593,"base_score":0.5209107425479593,"score_components":{}},{"id":"kb_v2_300","score":0.5208938000294947,"base_score":0.5208938000294947,"score_components":{}},{"id":"kb_v2_276","score":0.5060174340828584,"base_score":0.5060174340828584,"score_components":{}}]
- `rag_v2_test_192`: []
- `rag_v2_test_193`: []
- `rag_v2_test_194`: [{"id":"kb_v2_276","score":0.6733818893850487,"base_score":0.6733818893850487,"score_components":{}},{"id":"kb_v2_320","score":0.6207759892334295,"base_score":0.6207759892334295,"score_components":{}},{"id":"kb_v2_302","score":0.6159868718544816,"base_score":0.6159868718544816,"score_components":{}},{"id":"kb_v2_332","score":0.6154391218133668,"base_score":0.6154391218133668,"score_components":{}},{"id":"kb_v2_280","score":0.6148011646085103,"base_score":0.6148011646085103,"score_components":{}}]
- `rag_v2_test_195`: [{"id":"kb_v2_350","score":0.5991369740987528,"base_score":0.5991369740987528,"score_components":{}},{"id":"kb_v2_296","score":0.568512369684109,"base_score":0.568512369684109,"score_components":{}},{"id":"kb_v2_352","score":0.5644606245014767,"base_score":0.5644606245014767,"score_components":{}},{"id":"kb_v2_346","score":0.564294686460247,"base_score":0.564294686460247,"score_components":{}},{"id":"kb_v2_324","score":0.5594750097942618,"base_score":0.5594750097942618,"score_components":{}}]
- `rag_v2_test_196`: [{"id":"kb_v2_293","score":0.5631499016655079,"base_score":0.5631499016655079,"score_components":{}},{"id":"kb_v2_340","score":0.5487415396229278,"base_score":0.5487415396229278,"score_components":{}},{"id":"kb_v2_335","score":0.5416863992338597,"base_score":0.5416863992338597,"score_components":{}},{"id":"kb_v2_296","score":0.5398026421151166,"base_score":0.5398026421151166,"score_components":{}},{"id":"kb_v2_354","score":0.5352320756144684,"base_score":0.5352320756144684,"score_components":{}}]

## Protocol notes

- Test 仅在 Dev 冻结配置完成后运行；未使用 Test gold 或 Test query 进行调参。
- Oracle 使用 gold scenario/goal/stage；E2E 使用真实 HybridRouter.route -> ScenarioPolicyRegistry.resolve -> Retriever 链路。
- 由于 router_live_eval_enabled=false，本次 E2E 使用规则 Router，LLM correction disabled；结果不代表 live LLM Router。
- Safety 与 out-of-domain case 按分支行为单独评估，不混入 Retriever-only 的主 RAG 指标。

## Conclusion

Oracle Retriever 的 Hit@3/5=1.0、MRR=1.0；Test in-domain Recall@5=0.8846、No-answer F1=0.8966、FRR=0.1875，其中 FRR 未达到 <=0.10 目标。E2E Hit@3=0.1479，较 Oracle 下降 85.21pp；OOD route accuracy=1.0，但 high-safety recall=0.4286、safety bypass violation=0.3636。该结果如实表明当前 Router/Safety 链路是主要瓶颈，未修改 Test gold 或用 RAG 指标掩盖。

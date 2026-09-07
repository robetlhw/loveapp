# Phase 5 Multi-query Findings

## 结论

Bounded Multi-query Decomposition 的触发器与数量控制达到建议线，且真实 500-KB retrieval 对多意图覆盖有明显收益。Dev/Test Trigger Precision、Recall、F1 均为 `1.0000`，ExpectedSubqueryCount exact accuracy 为 `1.0000`，single-intent unnecessary decomposition rate 为 `0.0000`，single-intent Hit@3 degradation 为 `0.0000`。多 query 结果按 document ID 去重、聚合分数，再做现有 lexical + Soft Metadata rerank；没有加入 BM25、Cross-Encoder、新 embedding 或 LLM reranker。

## 实验协议与数据

- Dev：36 cases，1/2/3 intents 分布为 `9/18/9`。
- Test：18 cases，1/2/3 intents 分布为 `4/10/4`；在实现冻结后只运行一次确认。
- `max_subqueries=3`，不递归分解；rewrite（若启用）先于 decomposition。本次专项 retrieval 命令使用 `rewrite=false`，单独测量 decomposition。
- 使用现有 500-document KB 的临时内存 Qdrant，只读载入正式 KB 后建立临时索引；专项 fixture 从未进入 KB 或 embedding corpus。
- KB SHA256：`e4373a1e4426ac422a42e7dae7d7bb61c6291832da67625b311b99bf57714d25`。
- Dev/Test fixture SHA256：`8096e318fe34ae6bbcefe642772421029e805fc9df4eeafffe1e48061c530c2a` / `b325ec947c017d9942005ec4c842467576ab65b6e85ee1f01702c1c953bdc515`。

## Detector 与 single-intent control

| 指标 | 建议线 | Dev | Test | 结论 |
|---|---:|---:|---:|---|
| Decomposition trigger F1 | ≥0.90 | 1.0000 | 1.0000 | 通过 |
| Subquery count exact accuracy | — | 1.0000 | 1.0000 | 通过 |
| Unnecessary decomposition rate | 低 | 0.0000 | 0.0000 | 通过 |
| Single-intent Hit@3 degradation | ≤0.02 | 0.0000 | 0.0000 | 通过 |

single-intent baseline 和 frozen arm 的 Hit@3 都是 `1.0000`。最新 paired run 的单意图额外延迟为 Dev `+1.860 ms`（ratio `0.0402`）、Test `+7.241 ms`（ratio `0.1222`）；这是本地 paired run 的开销测量，不是线上 SLA。

## Retrieval 结果

| 指标 | Dev | Test |
|---|---:|---:|
| NeedRecall@3 | 0.9676 | 1.0000 |
| NeedRecall@5 | 1.0000 | 1.0000 |
| AllNeedsCovered@3 | 0.9167 | 1.0000 |
| AllNeedsCovered@5 | 1.0000 | 1.0000 |
| AnyHit@3 / AnyHit@5 | 1.0000 / 1.0000 | 1.0000 / 1.0000 |
| MRR | 1.0000 | 1.0000 |
| nDCG@3 / nDCG@5 | 0.9762 / 0.9948 | 0.9955 / 0.9955 |
| Duplicate candidate ratio | 0.0176 | 0.0074 |
| Mean merged candidate count | 28.9722 | 28.7778 |
| Multi-query latency overhead | +27.924 ms | +29.949 ms |

按 intent slice，Dev 的 2-intent / 3-intent current AllNeedsCovered@5 均为 `1.0000`，对应 single-query baseline 分别为 `0.8889` / `0.1111`；Test 的 2-intent / 3-intent current 均为 `1.0000`，baseline 分别为 `1.0000` / `0.5000`。这支持“多 query 对独立 Need 覆盖有帮助”的结论，但 Test 样本只有 18 条，不应外推到所有查询。

## Merge、候选与错误归因

每个 subquery 保存 candidate IDs 与 scores，最终报告保存 `per_subquery_candidate_ids`、`per_subquery_scores`、`merged_candidate_ids`、`final_top_k` 和 `merged_candidate_count`。Candidate Recall 按 detailed retriever 提供的 pre-rerank candidate pool 计算；Hit/MRR/nDCG 按最终排序计算。Dev/Test 的 `candidate_miss`、`rerank_error`、`subquery_quality_error` 和 `decomposition_trigger_error` 均为 0。

Phase 5 lint 在两个 split 都通过：case IDs、query 唯一，枚举合法，ExpectedSubqueries 数量与 1–3 上限一致，NeedCoverageGroups 是 RelevantIDs 子集，且 500-KB 中所有 RelevantIDs 均存在。完整 case 诊断和 slices 见：

- `.data/evals/multiquery_dev.json`
- `.data/evals/multiquery_test.json`

## 决策与限制

建议保留 decomposition feature flag，默认仍关闭，在 Router 已进入 RAG 且检测到明确独立 information needs 时启用。NeedRecall/AllNeedsCovered 应作为主指标，AnyHit 只是辅助，不能用单个 Hit@K 代替多 Need 覆盖。

当前实现和评测只对显式枚举、对比和 discourse marker 做确定性拆分；句子数量、问号数量或长度本身不会触发拆分。延迟与召回结果依赖 frozen embedding、Qdrant 临时索引和本地负载；Test 只作确认，不参与阈值或规则选择。

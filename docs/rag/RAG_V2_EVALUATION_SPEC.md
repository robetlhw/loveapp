# LoveApp RAG V2 Evaluation Specification

## 1. 评测目标与当前边界

V2 不再用“12 个简单 Query 全部命中”作为充分证据，而是分别评估：数据质量、纯 Retriever、Reranker、No-answer、Router→RAG 端到端损失、安全分支和延迟。Dev 用于调参，Test 只用于最终冻结配置评测。

**当前 V2 Core Benchmark 使用单轮、语义完整的 standalone query。** 即使一个 query 同时包含 2～3 个相关信息需求，Phase-1 也先把用户原始完整 query 作为一次 retrieval request，作为当前系统 baseline。

本阶段明确不把以下能力混进 baseline：
- Conditional Contextual Query Rewrite：解决“那我怎么办？”这类依赖历史的省略/指代问题。
- Multi-query Decomposition：解决一个完整输入中存在 2～3 个相对独立 information needs 的问题拆分。
- Cross-Encoder Reranker：作为后续神经精排实验。

原因是先测清当前 `raw query -> dense retrieval -> lexical/metadata rerank` 的能力，保证后续每个改动都可归因。

## 2. 必须先修正的旧指标语义

当前 `evaluate_rag()` 用“第一个相关文档是否出现在 Top-K”命名为 `recall_at_k`。当一个 case 存在多个 `relevant_ids` 时，这实际上是 **Hit@K**，不是 Recall@K。V2 必须同时报告：

- `Hit@K = 1[TopK ∩ Relevant ≠ ∅]`
- `Recall@K = |TopK ∩ Relevant| / |Relevant|`
- `Precision@K = |TopK ∩ Relevant| / K`
- `MRR = mean(1 / rank(first relevant))`
- `nDCG@K`：使用 `GradedRelevance` 的 0/1/2/3 相关等级

如果实际返回不足 K，Precision@K 的分母仍按 K；若另报 returned-count variant，必须使用不同名称。

## 3. Retriever-only（Oracle Metadata）

输入：`Query + gold Scenario/Goals/RelationshipStage`，直接调用 `QdrantKnowledgeStore.search()`，不经过 Router。

纳入：
- `ExpectedBranch=rag && NoAnswer=false` 的正常检索 case。
- `ExpectedBranch=rag && NoAnswer=true && NoAnswerScope=in_domain_uncovered` 的域内缺知识 case。

排除：
- `ExpectedBranch=safety`：属于安全分支评测。
- `ExpectedBranch=out_of_scope` / `NoAnswerScope=out_of_domain`：顶层 Router 本应拦截，不应拿来作为主 RAG abstention 成绩。

正常检索 case 必须报告：

1. Hit@1 / Hit@3 / Hit@5
2. Recall@3 / Recall@5
3. Precision@3 / Precision@5
4. MRR
5. nDCG@3 / nDCG@5
6. `candidate_recall`：rerank 前 candidate pool 是否已经包含 gold
7. `rerank_lift`：相关文档 rerank 前后平均 rank 改善
8. `hard_negative_leakage@3`：HardNegativeIDs 进入 Top-3 的 case 比例
9. 分 slice：Scenario / QueryType / Difficulty / RelationshipStage / Goal

### 目标线（Test）

- Hit@1 >= 0.80
- Hit@3 >= 0.92
- Hit@5 >= 0.96
- MRR >= 0.86
- nDCG@5 >= 0.90
- Hard-confusion Hit@3 >= 0.85
- 每个主要 Scenario Hit@3 >= 0.88（仅统计正常 RAG-eligible cases）
- hard_negative_leakage@3 <= 0.10

目标线用于工程验收，不是先验保证；第一次达不到时只能使用 Dev 做误差分析与调参，不能修改 Test gold 来追指标。

## 4. No-answer 必须拆成两类

V2 的 `NoAnswer=true` 不再等同于“随便放一个领域外问题”。

### A. `in_domain_uncovered` —— 主 RAG abstention 集

定义：
- 问题本身明确属于恋爱/关系咨询。
- Router 的合理结果仍应是 `relationship_advice` / `ExpectedBranch=rag`。
- 但当前 500 条 KB 没有足够具体、可作为正确依据的知识。
- 目标是测试 `rag_min_score`、candidate pool 和 rerank 是否会把“看起来相近但并不真正回答问题”的文档硬塞进上下文。

示例类型：
- 重病/长期照护对伴侣关系的特殊影响
- 不孕治疗、流产后的伴侣协作
- 移民担保造成的关系权力依赖
- 成瘾康复、开放式关系、重组家庭等当前 KB 未专门覆盖主题

主指标：
- Abstention Recall：`in_domain_uncovered` 中正确不注入高置信文档的比例
- Abstention Precision：所有 RAG abstention 中真正为 `in_domain_uncovered` 的比例
- Abstention F1
- False Retrieval Rate：`in_domain_uncovered` 中仍返回足以注入上下文的文档比例
- Coverage：正常有答案 RAG case 中未被错误 abstain 的比例

目标：No-answer F1 >= 0.85，False Retrieval Rate <= 0.10，同时正常 case Coverage >= 0.95。

### B. `out_of_domain` —— Router 边界集

定义：
- 天气、编程、汇率、硬件等明显非恋爱咨询问题。
- `ExpectedBranch=out_of_scope`。
- E2E 应由顶层 Router 拦截，不应调用普通 Advice RAG。

这些 case：
- 可以计入 E2E route/out-of-scope accuracy。
- **不计入主 Retriever-only No-answer F1 / False Retrieval Rate。**
- 如额外直接送 Retriever 做压力测试，必须单独报告为 `OOD direct-retrieval diagnostic`，不能和域内缺知识混成一个指标。

当前 50 个 No-answer：40 个 `in_domain_uncovered` + 10 个 `out_of_domain`。

## 5. 当前 Query Planning 评测边界

### 5.1 当前 V2：Raw standalone query baseline

Dev/Test 中的 Query 都应在不依赖对话历史的前提下表达完整语义。当前系统按原始 Query 一次检索。

`multi_scenario` / `multi_goal` 代表一个完整输入可能涉及多个知识方向；本阶段依然先做 single-query retrieval baseline，利用多个 RelevantIDs 检验 Recall@K。

### 5.2 后续 Phase：Conditional Contextual Query Rewrite

另建多轮数据集，例如：

`history + current_query("那我怎么办？") -> standalone_query -> retrieval`

至少比较：
- Raw current query
- Conditional rewrite query

核心看：
- rewrite-needed detection accuracy
- rewrite 后 retrieval Hit@3 / Recall@5
- rewrite faithfulness / topic drift
- latency / LLM-call rate

不要把该数据与当前 standalone Retriever benchmark 混算。

### 5.3 后续 Phase：Multi-query Decomposition

另建明确包含 2～3 个独立 information needs 的数据集，并提供 subquery-level gold。比较：

- 原始完整 query 单次检索
- 1～3 个 subquery 分别检索 → merge/dedup → rerank

重点报告：
- all-needs recall
- per-subquery recall
- final context diversity
- latency 增量

## 6. End-to-End Router → Policy → Retriever

不能把 oracle metadata 的成绩直接当作 Agent 的真实成绩。还要运行真实链路：

`Query -> routing -> ScenarioPolicy -> KnowledgeFilters -> Qdrant -> rerank`

报告：

- E2E Hit@3 / Hit@5 / MRR / nDCG@5（正常有答案 RAG case）
- Router primary scenario accuracy
- Goal micro-F1
- Out-of-scope branch accuracy
- `routing_degradation_pp = Oracle Hit@3 - E2E Hit@3`
- 按错误类型归因：`router_error / candidate_miss / rerank_error / threshold_rejection / gold_or_dataset_issue`

对 `in_domain_uncovered`，E2E 预期仍进入 normal relationship-advice 路径，但 Retriever 应能 abstain；该类不因为没有 Gold 文档就算 router error。

目标：routing degradation <= 8 percentage points。

## 7. Safety-aware Evaluation

`ExpectedBranch=safety` 的 case 不应因为知识库命中就走普通建议生成。端到端评测检查：

- HIGH safety recall >= 0.98，推荐目标 1.00
- Sensitive branch accuracy
- Safety case ordinary-RAG bypass violation rate = 0
- Safety case 不加载长期记忆/普通 advice composer 的现有行为不得回归

Retriever-only 可以单独检查高风险文档可检索性，但其结果不能覆盖端到端安全分支要求。

## 8. Latency

旧报告把第一次 embedding warmup 与后续热查询混成一个 mean，容易失真。V2 分开：

- `cold_start_ms`：首次模型 warmup + query
- warm retrieval latency：P50 / P90 / P95 / mean
- query embedding / vector search / rerank 分阶段 P50/P95

建议 warm P95 <= 150 ms 作为本地开发参考线；cold start 只记录，不与 warm latency 混算。

## 9. Data Quality / Lint

Knowledge V2 必须自动检测：

- 文档数 = 500
- ID 唯一率 = 100%
- canonical question 唯一率 = 100%
- 显式 enum 全部可解析
- QueryVariants 数量 3~5；本数据固定 3
- Required field completeness = 100%
- RelevantIDs / HardNegativeIDs 全部存在于 KB
- Dev/Test case ID 无重叠
- Dev/Test Query 与 KB question/query_variants normalized exact overlap = 0
- Dev/Test Query exact overlap = 0
- `NoAnswer=true` 必须有合法 `NoAnswerScope`
- `in_domain_uncovered` 必须 `ExpectedBranch=rag` 且 `RelevantIDs=[]`
- `out_of_domain` 必须 `ExpectedBranch=out_of_scope` 且 `RelevantIDs=[]`
- 近重复检测：中文字符 bigram Jaccard 或 embedding cosine；>=0.90 作为 error，0.82~0.90 作为 warning，人工复核
- Answer / retrieval_text 长度只做分布与异常值 warning，不做机械截断

对于 `in_domain_uncovered`，lint/report 还应输出 nearest retrieved docs 供人工复核，避免 benchmark 错把已有知识标成“缺失”。

## 10. 调参协议

### Phase A — Freeze baseline

记录当前：
- `bge-small-zh-v1.5`
- `rag_min_score=0.45`
- candidate pool 至少 15
- Top-K=5
- 当前 lexical / metadata reranker 权重
- **raw standalone query，不做 rewrite/decomposition**
- **不使用 Cross-Encoder**

### Phase B — Dev sweep

按顺序，不要一次改所有变量：

1. `rag_min_score`: 0.30 / 0.35 / 0.40 / 0.45 / 0.50 / 0.55 / 0.60
2. candidate limit: 15 / 30 / 50
3. final Top-K: 3 / 5 / 7
4. reranker ablation：Vector only；+Lexical；+Metadata；Full
5. retrieval_text ablation：Question；Question+Variants；Question+Variants+Answer；Full structured
6. lexical / metadata 权重调优
7. soft metadata vs hard filter

### Phase C — 选择配置

先满足硬约束：
- in-domain No-answer False Retrieval Rate <= 0.15
- Coverage >= 0.95
- hard_negative_leakage@3 <= 0.15

再在满足约束的配置中最大化 Dev composite：

`Score = 0.25*Hit@3 + 0.20*MRR + 0.20*nDCG@5 + 0.15*Recall@5 + 0.10*NoAnswerF1 + 0.10*HardCaseHit@3`

### Phase D — Freeze & Test

参数冻结后运行 200-case Test。Test 结果不用于继续调参；若必须改变系统设计，应记录为新实验版本并重新定义冻结点。

### Phase E — 后续 Agent/RAG 增量实验（不属于当前冻结配置）

完成 Core RAG 后再依次做：
1. Conditional Contextual Query Rewrite
2. Multi-query Decomposition（最多 3 个 subquery）
3. Cross-Encoder Reranker

每一步都使用独立 benchmark 或新增 slice，并保留上一阶段结果作为对照。

## 11. 报告应输出的表

1. Overall retrieval metrics
2. Scenario slice
3. QueryType slice
4. Dev parameter sweep
5. Reranker ablation
6. Retrieval-text ablation
7. In-domain No-answer confusion matrix
8. OOD routing accuracy
9. Oracle vs E2E gap
10. Latency percentile
11. Top failure cases（至少 20 个，含 expected / returned / vector score / score components / error attribution）

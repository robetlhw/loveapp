# LoveApp RAG V2 Metadata Filter Findings

> 状态：`DEV_TEST_COMPLETED`（已完成固定配置的 Dev Hard/Soft 比较及 Test 一次确认；未修改 KB 或 Gold）。

## 1. 目的与边界

本轮只回答一个问题：当 Router 的 `scenario / goal / relationship_stage` 判断错误时，
把这些字段作为 Qdrant dense 检索前的 Hard Filter，是否会放大 Router 错误；将它们
改为 Soft Metadata Rerank feature 后，是否能恢复原本可检索的 Gold 文档。

本轮不修改以下输入：

- `knowledge/loveapp_rag_knowledge_base_v2.md`；
- `evals/rag/cases_v2_dev.md` 与 `evals/rag/cases_v2_test.md`；
- Test Gold、RelevantIDs、HardNegativeIDs、NoAnswer 标注；
- frozen Retriever 参数（除 `hard_filter` 外）；
- embedding、query rewrite、query decomposition、BM25、cross-encoder、LLM reranker。

`scenario / goal / relationship_stage` 在本轮被视为 relevance metadata，而不是访问资格。
真正的 eligibility metadata（例如 tenant、ACL、deleted/expired、language hard constraint）
仍可在未来独立建模为不可绕过的 Hard Filter；本轮不把两类 metadata 混为一谈。

需要特别说明 `relationship_stage` 的实验边界：当前 `RouteResult` 没有输出 predicted
`relationship_stage`。因此 E2E 配对的 Hard/Soft 两臂都使用同一个 case stage；本轮的
metadata correctness 切片只在两侧已有已知 stage 时按定义比较，不能被解读为已经验证了
stage misprediction。当前 amplification 的 trace 证据主要覆盖 `scenario / goals` 判断错误
被 Hard Filter 放大的情形，不覆盖独立的 relationship-stage 误判；若后续 Router 增加该字段，
应另行运行包含真实 predicted stage 的实验。

## 2. 固定实验协议

两条臂必须使用同一批 case、同一 Router 输出、同一 embedding、同一 `min_score`、
`candidate_limit`、`top_k`、reranker、权重和 retrieval text。唯一自变量如下：

| Arm | `hard_filter` | Metadata 行为 |
|---|---:|---|
| Hard | `true` | dense candidate 生成前按 Router metadata 过滤 |
| Soft | `false` | dense candidate 先生成，scenario/goal/stage 仅作为 rerank bonus |

冻结配置（两臂除 `hard_filter` 外完全相同）：

```json
{
  "min_score": 0.6,
  "candidate_limit": 30,
  "top_k": 5,
  "reranker_mode": "full",
  "lexical_weight": 1.5,
  "metadata_weight": 1.0,
  "retrieval_text_mode": "question_variants"
}
```

正式产物：

- `RAG_V2_METADATA_FILTER_DEV_COMPARISON.json/.md`
- `RAG_V2_METADATA_FILTER_TEST_COMPARISON.json/.md`
- 可选：`RAG_V2_LIVE_ROUTER_SOFT_FILTER_REPORT.json/.md`（只有 live Router 真正启用且可复现时生成）

## 3. 必须先确认的实现语义

Hard arm 的 trace 必须显示如下顺序：

```text
Router predicted scenario / goals / stage
    -> KnowledgeFilters
    -> rag_vector_search (hard_filter=true)
    -> dense nearest candidates
    -> min_score candidate pool
    -> lexical + metadata rerank
    -> returned Top-K
```

Soft arm 必须显示：

```text
Router predicted scenario / goals / stage
    -> dense nearest candidates (无 metadata hard exclusion)
    -> min_score candidate pool
    -> lexical + metadata rerank
    -> returned Top-K
```

若 trace 没有 `rag_vector_search.details.hard_filter=true`，不能把该 case 作为
“Hard Filter 在 dense 前排除了 Gold”的证据；应记录为 `diagnostics_unavailable`，而不是
推断实现语义。

## 4. Q1–Q5 结果表

所有 Delta 定义为 `Soft - Hard`。`N/A` 表示分母为零或诊断信息不可用；不得用 0 替代。

| 问题 | 指标/字段 | Dev | Test（仅确认） | 结论 |
|---|---|---:|---:|---|
| Q1. Router metadata 错误时，Hard 是否降低 Candidate Recall？ | Router metadata incorrect slice: Hard/Soft Candidate Recall、`candidate_recovery_rate` | Dev: `0.0849 → 0.9717`（Δ `+0.8868`） | Test: `0.1418 → 1.0000`（Δ `+0.8582`） | 是；Hard 显著降低候选召回 |
| Q1 | `hard_filter_amplification_count` / rate | `96 / 231 = 0.4156` | `56 / 155 = 0.3613` | 存在稳定 amplification |
| Q2. 取消 Hard Filter 是否恢复 E2E？ | E2E Hit@3、Coverage、MRR、nDCG@5 的 Soft-Hard Delta | Hit@3 `+0.3725`；Coverage `+0.2550`；MRR `+0.3553` | Hit@3 `+0.3196`；Coverage `+0.2012`；MRR `+0.3166` | 检索侧明显恢复；Router 分支指标不变 |
| Q3. Soft 是否保留 metadata rerank 收益？ | Router-correct slice 的 Hit@3/MRR/nDCG；rerank lift | Dev correct slice Hit@3/MRR `1.0000/1.0000`（两臂相同） | Test correct slice Hit@3/MRR `1.0000/1.0000`（两臂相同） | 在正确 slice 未见收益损失；metadata 仍参与 Soft rerank |
| Q4. No-answer / Hard Negative 是否恶化？ | No-answer Precision/Recall/F1、FRR、Hard Negative Leakage@3 | F1 `0.2600 → 0.5000`；FRR `0.0833 → 0.2917`；HN `0 → 0` | F1 `0.3333 → 0.4762`；FRR `0.0625 → 0.3750`；HN `0 → 0` | F1 提升，但 FRR 上升；需保留 no-answer 风险监控 |
| Q5. 瓶颈来自 Router 还是 Router × Hard Filter？ | Error attribution、branch/metadata slices、trace evidence | Paired: branch `134`、amplification `96`、safety `19`；trace evidence `5` | Paired: branch `93`、amplification `56`、safety `8`；trace evidence `5` | 两层均有瓶颈；Hard Filter 是 Router metadata 错误的额外放大器 |

### 4.1 运行记录

| 字段 | Dev | Test |
|---|---|---|
| dataset SHA256 | 见报告 `inputs` / integrity | 见报告 `inputs` / integrity |
| KB SHA256 | 500 文档，完整性通过 | 500 文档，完整性通过 |
| case count / paired count | `300 / 300` | `200 / 200` |
| Router mode | rules（LLM correction disabled） | rules（LLM correction disabled） |
| Hard report path | `RAG_V2_METADATA_FILTER_DEV_COMPARISON.json/.md` | `RAG_V2_METADATA_FILTER_TEST_COMPARISON.json/.md` |
| Soft report path | 同一 comparison report 的 `soft_filter` arm | 同一 comparison report 的 `soft_filter` arm |
| trace evidence cases | `5` | `5` |

完整性检查两次均通过；报告保留现有数据集 lint 的近重复文本 warning（2 对），未删除或修改相关 case。

## 5. Hard Filter amplification 定义与证据

一个 answered RAG case 只有在同时满足以下条件时，才能计为
`hard_filter_amplification`：

1. Router metadata 与 Gold 不一致；
2. Hard arm 中 Gold 不在 dense candidate pool（或 Top-3/Top-5）；
3. Soft arm 使用完全相同 query、Router 输出与 frozen Retriever 参数后，Gold 进入 candidate pool 或 Top-K；
4. Hard trace 明确记录 `rag_vector_search.details.hard_filter=true`，从而证明是 dense 前过滤，而不是 rerank 排序失败。

每个 amplification case 至少保存：

```json
{
  "case_id": "rag_v2_dev_xxx",
  "gold_ids": [],
  "predicted_scenario": "<router_prediction>",
  "gold_scenario": "<gold_scenario>",
  "hard_candidate_contains_gold": false,
  "soft_candidate_contains_gold": true,
  "hard_top5_contains_gold": false,
  "soft_top5_contains_gold": true,
  "hard_filter_applied_before_dense_candidate": true,
  "attribution": "hard_filter_amplification"
}
```

若 Gold 在 Hard nearest pool 中但只在 rerank 后掉出 Top-K，应归为 `rerank_error`，不得归为
Hard Filter amplification。若 Router 分支本身错误（例如 safety 被路由为 RAG），应归为
`safety_error` 或 `router_branch_error`，不能让 Retriever 背锅。

## 6. Router correctness slices

### Slice A — Router metadata correct

纳入 answered RAG cases，其中 primary scenario 正确、goals 满足现有评测的交集标准，且
stage（两侧均提供已知值时）一致。输出：

- Hit@3、MRR、Coverage、Candidate Recall；
- Hard 与 Soft 的差值；
- 是否保留 metadata rerank 的收益。

### Slice B — Router metadata incorrect

纳入 answered RAG cases，其中 scenario/goal/stage 至少一项不一致。输出：

- Hard/Soft Hit@3；
- Hard/Soft Coverage；
- Hard/Soft Candidate Recall；
- Candidate、Top-3、Top-5 recovery；
- `hard_filter_amplification_count/rate`。

两个 slice 必须分开报告，不能用全量 E2E 平均值代替。

## 7. Error attribution 规则

按以下优先级统计，每个失败 case 只保留一个主归因：

1. `gold_or_dataset_issue`
2. `safety_error`
3. `router_branch_error`
4. `hard_filter_amplification`
5. `router_metadata_error`
6. `retriever_candidate_miss`
7. `rerank_error`
8. `no_answer_error`

报告同时保留 Hard、Soft 和 paired attribution。`router_metadata_error` 表示 metadata 错误
但没有满足 amplification 证据；`hard_filter_amplification` 表示该错误被 dense 前 Hard
Filter 进一步放大。这样可以拆开“Router 本身造成的损失”和“Router × Hard Filter 的额外损失”。

## 8. Case study 要求

优先展示：

- 最多 5 个 Hard → Soft 恢复 Gold 的 amplification case；
- 最多 3 个 Soft 后仍失败的 retrieval/rerank case；
- 最多 2 个 safety 或 Router branch failure。

每个 case 必须包含 Query、Gold Scenario/Goals、Predicted Scenario/Goals、Gold IDs、Hard
Candidate/Top5、Soft Candidate/Top5、最终 attribution。样本不足时展示全部可用样本，并明确实际数量。

## 9. Production decision

### Option A — 保留 Hard Filter

只有当 Dev 的 Router metadata accuracy 足够高、amplification rate 接近零，且 Soft 没有
带来可接受范围外的 No-answer/Hard-negative 恶化时，才可考虑。Test 只能做一次确认，不能用来
重新调参或选择方案。

### Option B — Soft Metadata Bonus Only

若 Hard arm 在 metadata-incorrect slice 显著降低 Candidate Recall，且 Soft arm 恢复 Hit@3/
Coverage，同时 No-answer 与 Hard Negative 没有不可接受恶化，则 production 默认建议
`hard_filter=false`，继续保留 scenario/goal/stage 作为 relevance bonus。

### 当前状态

基于 Dev 的 amplification（`96/231 = 41.56%`）以及 metadata-incorrect slice 的
Candidate Recall（`0.0849 → 0.9717`）、Hit@3（`0.0390 → 0.4372`）和 Coverage
（`0.1861 → 0.4589`）恢复，当前生产建议倾向 **Option B：`hard_filter=false`**，继续保留
`scenario / goal / relationship_stage` 作为 Soft Metadata relevance bonus。Test 一次确认方向一致：
amplification `56/155 = 36.13%`，Candidate Recall `0.1418 → 1.0000`，Hit@3 `0.0710 → 0.4194`。

这个结论只针对当前 LoveApp 的 relevance metadata 和 frozen 配置；不能外推到未来的 tenant、ACL、
deleted/expired 或 language eligibility constraints。No-answer 的 FRR 在 Soft arm 上升（Dev
`0.0833 → 0.2917`，Test `0.0625 → 0.3750`），因此切换不等于放宽 abstention：必须继续监控
`min_score`、No-answer F1/FRR 和 Hard Negative Leakage。Router branch/safety 错误仍需单独修复，
不能归因给 Retriever；本轮没有根据 Test 调参。

## 10. Test 与安全边界

- Test 只在 Dev 结论冻结后运行一次 Hard vs Soft 确认。
- 不修改 KB、Dev/Test Gold 或 query_variants。
- OOD 不混入 Retriever-only no-answer 主指标。
- safety 分支单独统计；`expected_branch=safety` 但实际调用 RAG 必须计为 `safety_error` / bypass violation。
- Live Hybrid Router 若未配置、成本不可控或无法复现，跳过并记录原因，不伪造结果。

## 11. 评测后续路线

本轮完成后暂停自动实现。若确有需要，下一阶段再单独设计 Conditional Contextual Query Rewrite、
Multi-query Decomposition、可选 Cross-Encoder，以及 Dense + BM25 hybrid retrieval 的实验，
不得把这些能力混入本轮 Hard/Soft 因果比较。

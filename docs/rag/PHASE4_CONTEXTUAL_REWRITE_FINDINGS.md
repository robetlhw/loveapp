# Phase 4 Contextual Rewrite Findings

## 结论

Conditional Contextual Query Rewrite 的功能指标达到建议线：Dev/Test Trigger Precision、Recall、F1 均为 `1.0000`，standalone control passthrough accuracy 为 `1.0000`，观测到的 query drift rate 为 `0.0000`，且真实检索对照没有伤害 standalone case。对 rewrite-required case，rewritten query 相对 raw `CurrentQuery` 带来显著检索提升。

但 Phase 4 数据 lint 不是 clean pass：Dev 和 Test 各有 6 个重复的通用 `CurrentQuery`，不同 case 依赖不同 History。这是 supplied Gold 的既有设计冲突，本轮没有修改或删除 Gold。因此 Phase 4 可认定为“实现与功能指标通过、数据完整性带已知例外”，不能描述为无保留的 benchmark freeze。

## 实验协议

- Dev：36 cases，`RewriteRequired=true/false = 24/12`。
- Test：18 cases，`RewriteRequired=true/false = 12/6`；在实现冻结后运行，没有用 Test 反向调参。
- Planner 顺序：context-dependent 检测 → 必要时 rewrite；完整 standalone query 原样透传。
- Retrieval：使用现有 500-document KB 和临时内存 Qdrant；只索引正式 KB，不索引 Phase 3–5 fixture。
- KB SHA256：`e4373a1e4426ac422a42e7dae7d7bb61c6291832da67625b311b99bf57714d25`。
- Dev/Test fixture SHA256：`358ecddff43495a5c7b4fbb487a9703d06381c602efe50ab8b325eb10358e502` / `834c7d1c8d2cb96b39de4feccddb36976cc1f0d38043905696649610da45a997`。
- 500-KB、旧 RAG V2 Gold 和专项 Test Gold 均未修改。

## Trigger、drift 与 control

| 指标 | 建议线 | Dev | Test | 结论 |
|---|---:|---:|---:|---|
| Trigger precision | — | 1.0000 | 1.0000 | 无 false positive |
| Trigger recall | — | 1.0000 | 1.0000 | 无 false negative |
| Trigger F1 | ≥0.90 | 1.0000 | 1.0000 | 通过 |
| Query drift rate | ≤0.05 | 0.0000 | 0.0000 | 通过 |
| Standalone passthrough accuracy | — | 1.0000 | 1.0000 | 通过 |
| Standalone Hit@3 degradation | ≤0.02 | 0.0000 | 0.0000 | 通过 |

Standalone 的 Hit@5、MRR、nDCG@3/@5 和 candidate recall degradation 在两个 split 也均为 `0.0000`。最新隔离 Qdrant paired run 的检索延迟差为 Dev `+2.169 ms`、Test `-0.082 ms`，属于本次本地运行的测量值，不应外推为线上 SLA。

## Rewrite-required retrieval gain

以下均为 `metric(rewritten standalone query) - metric(raw CurrentQuery)`，只统计需要 rewrite 的 cases：

| Metric | Dev gain | Test gain |
|---|---:|---:|
| Hit@1 | +0.9167 | +1.0000 |
| Hit@3 | +1.0000 | +1.0000 |
| Hit@5 | +1.0000 | +1.0000 |
| MRR | +0.9583 | +1.0000 |
| nDCG@3 | +0.9692 | +1.0000 |
| nDCG@5 | +0.9692 | +1.0000 |
| Candidate Recall | +0.9583 | +1.0000 |

Candidate Recall 使用 retriever 暴露的 pre-rerank candidate pool；若适配器没有 detailed candidate pool，才回退到 returned IDs。Hit/MRR/nDCG 仍基于最终排序结果。Dev/Test 都没有 `candidate_miss` 或 `rerank_error`。

报告的整行检索 mean latency 为 Dev `81.534 ms`、Test `85.699 ms`；其运行同时执行 raw 与 rewritten paired retrieval，主要用于同机相对比较。

## 数据 lint 与错误归因

500-KB 校验通过：两个 split 的 RelevantIDs 均存在，KB document count 为 500，unknown RelevantIDs 为空；ID、枚举、standalone equivalence、rewrite-required history 均无其他错误。

唯一 lint 错误是每个 split 内以下 6 个 CurrentQuery 文本被重复使用：

- `所以这个变化一般说明什么`
- `这种情况我还要继续吗`
- `这种时候下一步怎么做比较稳`
- `那我是不是先别主动了`
- `那我现在到底该怎么办`
- `那我要怎么跟ta说比较好`

它们由不同 History 消歧，功能上正是 contextual rewrite case，但违反书面规范中的“CurrentQuery 不重复”。本轮将其记录为已知 `gold_or_dataset_issue`，没有为了让 lint 变绿而修改 Gold。运行时错误归因中 `rewrite_trigger_error=0`、`query_drift=0`、`candidate_miss=0`、`rerank_error=0`；top failures 为空。

## 诊断边界

当前 drift 检测覆盖 standalone 是否被不必要改写，以及 rewrite result 与 ExpectedStandaloneQuery 的保守语义重合度。`0.0000` 表示本 fixture 与当前诊断器下未发现 drift，不等于已经用独立语义裁判穷尽验证“改变主体、引入新事实、把不确定写成确定、改变信息需求、普通问题与安全问题互换”等所有风险。生产接入结构化模型 rewrite 时仍应保留 fail-safe passthrough 和抽样审计。

## 产物与决策

- `.data/evals/contextual_rewrite_dev.json`
- `.data/evals/contextual_rewrite_test.json`

建议保留 feature flag，默认历史行为继续关闭；在 Router 已正确进入 RAG 后才启用 rewrite。Phase 4 不改变 scenario/goal/stage 的 Soft Metadata 语义，也不引入 Cross-Encoder、BM25、新 embedding 或 LLM reranker。

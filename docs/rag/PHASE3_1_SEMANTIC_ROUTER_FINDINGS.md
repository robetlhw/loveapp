# Phase 3.1 Semantic Router Findings

本轮只在旧 Phase 3 Dev 与新增 Challenge Dev 上比较 Rule-only、LLM always-on 和 Conditional。
报告中的默认 provider 是 `fixture_semantic`，`live_llm=false`；离线启发式结果不能冒充线上 LLM 泛化。
旧 Phase 3 Test 已暴露，本轮没有把它当作新的 held-out Test，也没有创建或运行 Router Test V1.1。

## 1. 完整对比

| Dataset | Arm | Branch macro-F1 | RAG recall | Scenario macro-F1 | Goal micro-F1 | LLM call rate |
|---|---|---:|---:|---:|---:|---:|
| dev | rule | 0.9491 | 0.9405 | 0.8834 | 0.5780 | 0.0000 |
| dev | llm always-on | 1.0000 | 1.0000 | 0.7978 | 0.6182 | 0.7083 |
| dev | conditional | 1.0000 | 1.0000 | 0.9188 | 0.6000 | 0.1333 |
| challenge_dev | rule | 0.6079 | 0.7895 | 0.3882 | 0.3028 | 0.0000 |
| challenge_dev | llm always-on | 1.0000 | 1.0000 | 0.7145 | 0.7832 | 0.9583 |
| challenge_dev | conditional | 1.0000 | 1.0000 | 0.6915 | 0.6888 | 0.7333 |

说明：`branch_macro_f1` 对只包含 normal/OOD 的 Challenge Dev 按实际出现的类别计算；
未出现的 safety 类别保留在 `branch.per_class`，但不把不存在的类别强行计入宏平均。

## 2. Q1–Q9 明确结论

### Q1. LLM correction 是否显著提高 RAG Branch Recall？**有限支持（离线 fixture）**。
Dev：always-on 1.0000（Δ +0.0595），conditional 1.0000（Δ +0.0595）；Challenge Dev：always-on 1.0000（Δ +0.2105），conditional 1.0000（Δ +0.2105）。两份 Dev fixture 都有提升，但 provider 不是线上 LLM，不能据此宣称线上收益。

### Q2. LLM 是否提高 short / colloquial query 泛化？**Branch 有提升，Scenario 仍不足**。
Challenge short/colloquial scenario accuracy：rule 0.3519 → always-on 0.8148 → conditional 0.8148；conditional 的 branch 召回改善不能等同于 scenario 已达验收。

### Q3. boundary / conflict / maintenance confusion 是否下降？**部分下降，未稳定解决**。
Challenge canonical `scenario_hard_confusion` scenario accuracy：rule 0.3250 → always-on 0.6500 → conditional 0.5750；该 slice 是相邻场景混淆的总体代理，仍需逐类 confusion matrix 和新的 Dev 调优。

### Q4. understand / progress / repair / set_boundary recall 是否恢复？**部分恢复，仍未达目标**。

| Goal recall | Dev rule | Dev always-on | Dev conditional | Challenge rule | Challenge always-on | Challenge conditional |
|---|---:|---:|---:|---:|---:|---:|
| understand | 0.0769 | 0.6923 | 0.1154 | 0.1667 | 0.8333 | 0.6905 |
| progress | 0.3333 | 0.4444 | 0.3889 | 0.1923 | 0.6538 | 0.5769 |
| repair | 0.0000 | 0.4286 | 0.1429 | 0.3333 | 0.9333 | 0.7333 |
| set_boundary | 0.3333 | 0.8333 | 0.3750 | 0.1905 | 0.7857 | 0.6190 |

Challenge canonical `goal_multilabel` slice goal micro-F1：rule 0.3130 → always-on 0.8256 → conditional 0.6575。

### Q5. communicate 是否仍有 default overprediction？**是**。
`goal_wrong_default_communicate`：Dev rule/always/conditional = 25 / 0 / 24；Challenge = 9 / 13 / 13。

### Q6. Always-on 与 Conditional 的准确率差距是多少？**Conditional 更省调用，但准确率取舍依数据集而异**。
Dev scenario macro-F1：0.7978 vs 0.9188 （conditional Δ +0.1210）；goal micro-F1：0.6182 vs 0.6000。Challenge scenario macro-F1：0.7145 vs 0.6915；goal micro-F1：0.7832 vs 0.6888。

### Q7. Conditional 节省了多少调用、token 和 latency？**调用与 token 明显下降；latency 需按本机 fixture 解读**。
Dev call rate 0.7083 → 0.1333，total tokens 23199 → 4340，router mean latency 3.412 → 2.037 ms；Challenge call rate 0.9583 → 0.7333，total tokens 32193 → 24293。

### Q8. Safety 是否保持不回退？**是；Challenge Dev 无 safety 样本，相关 recall 只能记为未评估**。

| Dataset | Arm | High support | Sensitive support | Safety → RAG bypass |
|---|---|---:|---:|---:|
| dev | rule | 12 | 6 | 0.0000 |
| dev | always-on | 12 | 6 | 0.0000 |
| dev | conditional | 12 | 6 | 0.0000 |
| challenge_dev | rule | 0 | 0 | 0.0000 |
| challenge_dev | always-on | 0 | 0 | 0.0000 |
| challenge_dev | conditional | 0 | 0 | 0.0000 |

Dev 的 high/sensitive recall 均为 1.0、bypass 为 0；Challenge 的 high/sensitive support 都是 0，不能把 0 当作失败召回。

### Q9. 当前是否值得自动构建 Router Test V1.1？**否，不自动构建；保留人工决策**。
Challenge Dev 仍显示 scenario/goal 泛化缺口，且当前语义 provider 是 offline fixture；应先冻结配置、补齐 Dev remediation，
再由人工决定新的 held-out Test。

## 3. 范围与下一步边界

- 本轮不修改旧 Test/Gold/KB，不恢复 scenario/goal hard filter。
- 本轮不修改 Retriever、Phase 4 Contextual Rewrite、Phase 5 Multi-query Decomposition。
- 不进入 Cross-Encoder、BM25、新 embedding、LLM reranker 或 production rollout。

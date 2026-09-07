# LoveApp Phase 3.2.1 Semantic Router Stabilization Findings

## 1. 结论

本轮完成了 Always-on invariant、Rule/LLM score space 分离、Goal Policy sweep、Conditional C0/C1/C2、sanitization trace、20 条 long-context 人工裁决和 24 条 x 3 次 repeatability。代码与评测链路已可复现，真实 DeepSeek 调用也稳定完成，但 **Router 目前仍不应冻结，也不应创建 Router Test V1.1**。

原因不是 Scenario 或 Conditional 总体 gap：这两项已经达到参考线。未通过的是 Goal 的绝对质量与 recall 保护：

- Always-fixed Challenge Scenario Macro-F1 为 `0.8490`，Top-2 为 `0.9825`，均通过参考线。
- Conditional-remediated Challenge 相对 Always-fixed 的 Scenario / Goal gap 分别为 `0.36pp / 0.76pp`，均小于 `3pp`。
- Always-fixed Challenge Goal Micro-F1 / Macro-F1 为 `0.7330 / 0.7080`，低于 `0.76 / 0.72` 参考线。
- Goal sweep 的 10 个候选没有一个通过 monitored-goal recall guard；最终回退选择的 `threshold=0.3, max_goals=3` 明确记录为 `selected_without_recall_guard_no_candidate_passed`。
- Challenge Goal overprediction case 从 `75` 降至 `43`，但 Micro Recall 同时从 `0.8791` 降至 `0.7023`。这不是可以忽略的 recall 损失。
- Conditional 在 Challenge 只比 Always 少 `10pp` 调用率，低于“最好节省至少 15pp”的参考目标；本次 P95 仅下降 `3.94%`，仍不能宣称 tail latency 显著改善。

因此，本轮工程整改有效，但 accuracy-cost 选择尚未达到可冻结状态。

## 2. 实验协议与真实性

- 数据集只使用 Original Dev 与 Challenge Dev；未读取或运行 `loveapp_router_safety_eval_test_v1.md`，也未创建 Test V1.1。
- Goal Policy 只离线回放 Phase 3.2 Always Dev 的真实 Live trace，不增加 provider 调用。
- Conditional 的 `C0/C1/C2` 只在 Dev 运行并选择；Dev 选择 `C2` 后，Challenge 只运行冻结的 `C2`。
- provider/model 为 `deepseek / deepseek-v4-flash`，`temperature=0`。
- semantic prompt 内容未改，SHA-256 仍为 `b627098824f25263381471a964e4ee32a4285169ded2ad6a1f6af45073fbb4c8`。`routing-v3.2.1-v1` 标识本轮 Router policy/eval 版本，不代表 prompt 文本发生变化。
- 使用 `max_retries=1`；调用预算上限为 808 个 routed cases / 1616 次 provider attempts，实际为 `475 / 475`。
- 四份正式 Live 主报告、三份消融/稳定性报告均通过 bundle validator。正式主报告和 repeatability 共 `0` provider error、`0` timeout、`0` parse error、`0` fallback。
- 旧 Phase 3.2 四份 Live 报告只作为 before reference，没有重跑、改写或冒充本轮结果。

### 2.1 Dev-first 协议复核

最终正式报告由 smoke 修复后的编排重新生成。`phase321_protocol.smoke.always.dataset` 与 `conditional_c2.dataset` 都是 Original Dev；smoke case ID 均为 `router_v1_dev_*`。Goal Policy 和 C0/C1/C2 选择完成后才进入 Challenge，且 `challenge_profiles_executed=["C2"]`。7 份报告的 protocol 完全一致，严格 Dev-first 复核通过。

本轮 7 份正式 JSON：

1. `.data/evals/router_phase3_2_1_always_dev.json`
2. `.data/evals/router_phase3_2_1_conditional_dev.json`
3. `.data/evals/router_phase3_2_1_always_challenge_dev.json`
4. `.data/evals/router_phase3_2_1_conditional_challenge_dev.json`
5. `.data/evals/router_phase3_2_1_goal_policy_ablation.json`
6. `.data/evals/router_phase3_2_1_conditional_ablation.json`
7. `.data/evals/router_phase3_2_1_repeatability.json`

## 3. 主结果

### 3.1 Branch / Scenario

| Dataset | Arm | Branch Macro-F1 | RAG Recall | Scenario Acc | Scenario Macro-F1 | Scenario Top-2 |
|---|---|---:|---:|---:|---:|---:|
| Dev | Rule | 0.9491 | 0.9405 | 0.8571 | 0.8834 | 0.8690 |
| Dev | Always-before | 1.0000 | 1.0000 | 0.7143 | 0.7209 | 0.9286 |
| Dev | Always-fixed | 1.0000 | 1.0000 | 0.7024 | 0.7060 | 0.9167 |
| Dev | Conditional-before | 1.0000 | 1.0000 | 0.8929 | 0.8962 | 0.9405 |
| Dev | Conditional-remediated | 1.0000 | 1.0000 | 0.7619 | 0.7696 | 0.9286 |
| Challenge | Rule | 0.6079 | 0.7895 | 0.3333 | 0.3882 | 0.3596 |
| Challenge | Always-before | 1.0000 | 1.0000 | 0.8509 | 0.8383 | 0.9737 |
| Challenge | Always-fixed | 1.0000 | 1.0000 | 0.8596 | 0.8490 | 0.9825 |
| Challenge | Conditional-before | 1.0000 | 1.0000 | 0.8070 | 0.7921 | 0.8947 |
| Challenge | Conditional-remediated | 1.0000 | 1.0000 | 0.8596 | 0.8454 | 0.9561 |

Original Dev 的 Always Scenario 下降不能全部解释为模型能力回退。人工裁决发现固定模板为全部 42 条 long-context 注入了额外的 maintenance/communication 语义，详见 `PHASE3_2_LONG_CONTEXT_ADJUDICATION.md`。

### 3.2 Goal

| Dataset | Arm | Micro P | Micro R | Micro-F1 | Macro-F1 | Exact-set | Avg Predicted | Avg Gold |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Dev | Rule | 0.6726 | 0.5067 | 0.5780 | 0.4879 | 0.2143 | 1.3452 | 1.7857 |
| Dev | Always-before | 0.5356 | 0.8533 | 0.6581 | 0.6638 | 0.0714 | 2.8452 | 1.7857 |
| Dev | Always-fixed | 0.5906 | 0.6733 | 0.6292 | 0.6316 | 0.1190 | 2.0357 | 1.7857 |
| Dev | Conditional-before | 0.6015 | 0.5333 | 0.5654 | 0.4985 | 0.1786 | 1.5833 | 1.7857 |
| Dev | Conditional-remediated | 0.5808 | 0.6467 | 0.6120 | 0.6249 | 0.1429 | 1.9881 | 1.7857 |
| Challenge | Rule | 0.6232 | 0.2000 | 0.3028 | 0.2966 | 0.0351 | 0.6053 | 1.8860 |
| Challenge | Always-before | 0.6949 | 0.8791 | 0.7762 | 0.7431 | 0.2982 | 2.3860 | 1.8860 |
| Challenge | Always-fixed | 0.7665 | 0.7023 | 0.7330 | 0.7080 | 0.2368 | 1.7281 | 1.8860 |
| Challenge | Conditional-before | 0.7222 | 0.7256 | 0.7239 | 0.7083 | 0.2982 | 1.8947 | 1.8860 |
| Challenge | Conditional-remediated | 0.8187 | 0.6512 | 0.7254 | 0.7150 | 0.2719 | 1.5000 | 1.8860 |

Goal Policy 的方向有效但力度失衡：

- Dev overprediction case/label 从 `78/111` 降至 `60/70`，但 missing case/label 从 `20/22` 增至 `43/49`。
- Challenge overprediction case/label 从 `75/83` 降至 `43/46`，但 missing case/label 从 `26/26` 增至 `63/64`。
- Dev 的 overprediction/missing labels per case 从 `1.3214/0.2619` 变为 `0.8333/0.5833`；Challenge 从 `0.7281/0.2281` 变为 `0.4035/0.5614`。
- Challenge precision 提升 `7.16pp`，recall 下降 `17.68pp`，Micro-F1 下降 `4.32pp`。
- `threshold=0.3, max_goals=3` 是本轮实验中的 fallback candidate，不是可直接冻结的生产结论。

### 3.3 调用、token 与 latency

| Dataset | Arm | Calls | Call Rate | Tokens | Router Mean ms | Router P95 ms | Trigger Miss Cases | Unnecessary Call Cases | Sanitization Rate |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Dev | Rule | 0 | 0.0000 | 0 | 1.083 | 1.887 | N/A | N/A | N/A |
| Dev | Always-before | 101 | 0.8417 | 63,578 | 1076.487 | 1620.744 | N/A | N/A | N/A |
| Dev | Always-fixed | 85 | 0.7083 | 54,975 | 1169.782 | 2067.200 | 0 | 0 | 0.2941 |
| Dev | Conditional-before | 16 | 0.1333 | 9,836 | 175.336 | 1301.816 | N/A | N/A | N/A |
| Dev | Conditional-remediated | 55 | 0.4583 | 36,168 | 654.808 | 1621.117 | 6 | 11 | 0.4000 |
| Challenge | Rule | 0 | 0.0000 | 0 | 0.708 | 1.077 | N/A | N/A | N/A |
| Challenge | Always-before | 119 | 0.9917 | 71,146 | 1200.872 | 1497.849 | N/A | N/A | N/A |
| Challenge | Always-fixed | 116 | 0.9667 | 69,521 | 1165.875 | 1550.676 | 0 | 0 | 0.0776 |
| Challenge | Conditional-before | 88 | 0.7333 | 52,313 | 886.502 | 1502.627 | N/A | N/A | N/A |
| Challenge | Conditional-remediated | 104 | 0.8667 | 62,127 | 1065.832 | 1489.577 | 1 | 2 | 0.0769 |

相对 Always-fixed，Challenge Conditional-remediated：

- 少 `12` 次 Live 调用，调用率下降 `10pp`。
- 少 `7,394` tokens，下降 `10.64%`。
- mean latency 下降 `100.043ms`，下降 `8.58%`。
- 本次 P95 下降 `61.099ms`（`3.94%`）。该幅度较小且 provider latency 有运行间波动；C2 仍调用 86.67% 的 cases，因此收益仍应表述为平均延迟、调用数和 token，不能保证 tail latency 显著下降。

`conditional_unnecessary_call_case_count` 是唯一 case 数；`conditional_unnecessary_call_dimension_count` 是 branch/scenario/goal 三维累计数，二者不可混用。Challenge 分别为 `2` 与 `115`，因此维度数大于调用 case 数并非计数错误。

## 4. Always-on invariant 与 score space

Always-fixed 的控制流现在只允许 Safety、date workflow、显式系统命令和 deterministic OOD 在 semantic call 前 bypass。结果如下：

| Dataset | Cases | LLM Calls | Allowed Bypass | Unexpected Bypass | normal + rag 未调用 |
|---|---:|---:|---:|---:|---:|
| Dev | 120 | 85 | 35 (`18 safety + 16 OOD + 1 date`) | 0 | 0 |
| Challenge | 120 | 116 | 4 (`4 OOD`) | 0 | 0 |

Rule 与 LLM 分数已拆分为：

- `rule_scenario_scores` / `rule_goal_scores`：deterministic heuristic raw score。
- `llm_scenario_scores` / `llm_goal_scores`：`0..1` semantic relevance score，不称为 probability。
- `final_scenario_weights` / `final_goal_weights`：只从当前 active source 做 max-normalization，不再做 `rule 5.0 + llm 0.8` 式混合。

对应 invariant、normalization、primary-retention、threshold 与 sanitization 单测均通过。

## 5. Conditional C0/C1/C2

Dev ablation 只观察 Dev，并据此选择 C2：

| Variant | Scenario Macro-F1 | Goal Micro-F1 | Call Rate | Tokens | Mean ms | Trigger Miss Cases | Acceptance |
|---|---:|---:|---:|---:|---:|---:|---|
| C0 | 0.8965 | 0.5672 | 0.1250 | 9,297 | 188.096 | 11 | Fail |
| C1 | 0.8984 | 0.5683 | 0.1583 | 11,973 | 233.636 | 10 | Fail |
| C2 | 0.7696 | 0.6120 | 0.4583 | 36,168 | 654.808 | 6 | Pass |

Challenge 的总体 gap 已达标，但 slice 结论需要更精确：

| Slice | Always Scenario Acc | Conditional Scenario Acc | Always Goal F1 | Conditional Goal F1 | Conditional Call Rate |
|---|---:|---:|---:|---:|---:|
| short_colloquial | 0.9630 | 0.9630 | 0.7470 | 0.7654 | 0.9167 |
| hard_confusion | 0.8000 | 0.8000 | 0.7317 | 0.7114 | 0.7750 |
| multi_label_goals | 0.6750 | 0.6750 | 0.7117 | 0.7020 | 0.8000 |

- hard_confusion Scenario gap 已消除；Goal gap 从 before 的 `9.42pp` 缩小到 `2.03pp`。
- multi-label Scenario gap 已消除；Goal gap 从 before 的 `12.72pp` 缩小到 `0.97pp`。
- short_colloquial Scenario 无回退，Goal F1 反而提高 `1.84pp`。

## 6. Sanitization 与 provider reliability

真实发生的 sanitization reason 只有：

- Dev：`unknown_enum_removed=20/16`、`max_length=6/8`（Always/Conditional）。
- Challenge：两臂均为 `unknown_enum_removed=8`、`max_length=1`。

Trace 同时保留 bounded `llm_raw_decision`、`llm_sanitized_decision`、reason list 和简短 `reasoning_summary`，不记录隐藏 Chain-of-Thought。四份主报告和 72 次 repeatability 调用均没有 provider/parse/timeout/fallback error。

用户此前遇到的 `8/8 provider_errors` 未在本轮复现；完整实验 `475/475` attempts 成功，说明此前更符合瞬时 provider 故障，而不是 fixture fallback 或当前配置持续失效。adapter 现在会记录经过脱敏和长度限制的 provider error sample，后续若复现可直接看到 HTTP/provider 原因。

## 7. Repeatability

Challenge representative sample 为 24 条，覆盖 short colloquial、hard confusion 和 multi-label goals，共运行 3 次：

| Metric | Result |
|---|---:|
| Branch agreement | 1.0000 |
| Primary Scenario agreement | 1.0000 |
| Scenario Top-2 set agreement | 0.9722 |
| Goal Jaccard agreement | 0.9629 |
| Exact Goal-set agreement | 0.9167 |

Branch 与 primary scenario 稳定；Goal set 仍有 `8.33%` 的 pairwise exact-set 不一致，因此应称为“核心路由稳定、Goal 集合仍有少量波动”，不应写成完全确定性。

## 8. Original Dev long-context 裁决

42 条 long-context 中，严格满足“Rule primary 正确、Always-before primary 错误”的只有 12 条。为避免伪造样本数，人工审计使用这 12 条，加 5 条相邻错误样本和 3 条强锚点对照，共 20 条。

20 条裁决为：Gold clearly reasonable `11`、LLM clearly reasonable `3`、Both reasonable `2`、Query wording conflicts with Gold `4`。严格 12 条 regression 中只有 7 条可明确判为真实 LLM regression，另 5 条属于 LLM 更合理、两者均合理或 Query/Gold 冲突。

结论是存在真实 LLM 错误，也存在明显的模板语义注入和 label ambiguity。本轮不修改旧 Dev Gold；任何 V1.1 都应先重写 Query 并双人裁决，而不是继续对这些歧义样本调 prompt。

## 9. Router -> Retriever 实际契约

实际链路为：

```text
HybridRouter
  -> RouteResult primary/secondary Scenario + Goal labels
  -> ConversationAgent
  -> AdviceRequest labels
  -> ScenarioPolicy retrieval_limits
  -> KnowledgeFilters
  -> Retriever soft_rerank
```

当前 Retriever 实际使用情况：

| Router signal | 是否到达 Retriever | 实际用途 |
|---|---|---|
| primary/secondary scenario labels | 是 | 先生成 Advice policy/quota，再进入 `KnowledgeFilters` |
| primary/secondary goal labels | 是 | 与文档 Goal 做 soft metadata match |
| `KnowledgeFilters.scenario_weights` | 是 | 由 `policy.retrieval_limits / total_document_limit` 重新派生，soft scenario boost |
| `goal_weights` | 否 | `KnowledgeFilters` 当前没有该字段 |
| `final_scenario_weights` | 否 | 只存在于 `RouteResult`/eval trace，未直接传给 `AdviceRequest` |
| `final_goal_weights` | 否 | 同上 |
| Rule raw score | 否 | 不传入 Retriever |
| LLM semantic score | 否 | 不传入 Retriever |

因此，当前 `final_*_weights` 的统一量纲修复是 Router contract/observability 正确性修复，但尚未改变 Retriever 数值 rerank。Retriever 的 scenario numeric weight 来自 Advice policy quota，而不是 Router final score；Goal 仍按 labels 加分。scenario/goal/relationship_stage 继续保持 soft metadata，没有恢复 hard filter，也没有改 Retriever 参数。

## 10. 验收线

| Check | Target | Result | Status |
|---|---:|---:|---|
| Always Challenge Branch Macro-F1 | >= 0.98 | 1.0000 | Pass |
| Always Challenge RAG Recall | >= 0.98 | 1.0000 | Pass |
| Always Challenge Scenario Macro-F1 | >= 0.82 | 0.8490 | Pass |
| Always Challenge Scenario Top-2 | >= 0.95 | 0.9825 | Pass |
| Always Challenge Goal Micro-F1 | >= 0.76 | 0.7330 | **Fail** |
| Always Challenge Goal Macro-F1 | >= 0.72 | 0.7080 | **Fail** |
| Conditional Scenario gap | <= 3pp | 0.36pp | Pass |
| Conditional Goal gap | <= 3pp | 0.76pp | Pass |
| Conditional RAG Recall | >= 0.98 | 1.0000 | Pass |
| Conditional Call Rate saving | >= 15pp preferred | 10pp | **Below reference** |

## 11. 必答问题

1. **Q1 Always-on 漏调用是否修复？** 是。两个数据集 `always_unexpected_bypass_count=0`，normal relationship RAG 未调用数为 0。
2. **Q2 Rule/LLM score 是否彻底分离？** Router 内已分离，final weights 只归一化单一 active source；没有 raw score 混加。
3. **Q3 Retriever 实际使用哪个 metadata weight？** 使用由 Advice policy quota 派生的 `KnowledgeFilters.scenario_weights`；不直接使用 Router `final_*_weights`，也没有 `goal_weights`。
4. **Q4 Goal overprediction 是否明显下降？** Challenge case `75 -> 43`，label `83 -> 46`，是明显下降。
5. **Q5 Goal recall 是否明显受损？** 是，Challenge `0.8791 -> 0.7023`，下降 `17.68pp`，不可接受为无损优化。
6. **Q6 hard_confusion trigger 是否改善？** 是，Scenario gap 消除，Goal gap 从 `9.42pp` 缩至 `2.03pp`。
7. **Q7 multi-label trigger 是否改善？** 是，Scenario gap 消除，Goal gap 从 `12.72pp` 缩至 `0.97pp`。
8. **Q8 Conditional 与 Always 总体 gap 是否 <= 3pp？** 是，Scenario `0.36pp`、Goal `0.76pp`。
9. **Q9 Conditional 节省多少？** Challenge 少 12 次调用、10pp 调用率、10.64% tokens、8.58% mean latency。
10. **Q10 P95 为什么不能保证明显下降？** C2 仍调用 86.67% 的 Challenge cases，tail 样本大多仍经过相同 Live provider；本次虽下降 `61.099ms`（3.94%），不足以证明稳定且显著的 tail 改善。
11. **Q11 Original Dev long-context 是否有明显 label ambiguity？** 是。严格 12 条自动 regression 中有 5 条不能人工认定为明确模型错误。
12. **Q12 Live LLM repeatability 是否稳定？** Branch/primary scenario 稳定，Goal set 尚未完全稳定；结论为部分稳定而非完全稳定。
13. **Q13 现在是否可以冻结 Router？** 否。严格 Dev-first 已通过，但 Goal absolute gate 与 recall guard 未通过，Conditional 15pp cost 参考线也未达到。
14. **Q14 是否值得现在创建 held-out Router Test V1.1？** 否。先在不看旧 Test 的前提下解决 Goal precision-recall trade-off，并完成 long-context 数据重写/双人裁决，再创建新的 held-out Test。

## 12. 验证

- Router 相关回归：`221 passed`
- RAG / Knowledge 相关回归：`67 passed`
- `uv run ruff check src tests`：passed
- `uv run python -m compileall -q src tests`：passed
- 7 份 JSON 均可解析、文件集合完整，且 `old_router_test_loaded=false`。
- 严格 Dev-first protocol audit：passed；两个 smoke 均使用 Original Dev，Challenge 仅运行 Dev 冻结的 C2。
- Live calls：`475 attempts / 475 successful decisions`，无 fixture fallback
- provider 单价未传入，estimated cost 为 `N/A`；token 数是本轮可审计的成本代理。

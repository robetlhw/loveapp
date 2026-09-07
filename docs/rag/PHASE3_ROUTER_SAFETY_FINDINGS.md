# LoveApp Phase 3 - Router / Safety Findings

> 状态：`DEV_TEST_COMPLETED_WITH_OPEN_ROUTING_GAPS`。本文件只记录 Phase 3
> Router/Safety 专项评测；不把 Phase 4/5 或 Retriever 指标并入 Phase 3 结论。

## 1. 范围、协议与输入

本阶段验证三个 top-level branch（`rag`、`safety`、`out_of_scope`）、relationship
scenario、multi-label goal 以及 risk routing。评测器直接解析专项 Markdown fixture，
只调用确定性的 `HybridRouter` rules（`router_v2_enabled=true`）；不调用 retrieval、
embedding 或 LLM correction。当前 evaluator 将正常且 `task_type=relationship_advice`
的结果计为 `rag`；正常但落到 `general_chat` 或其他非 relationship task 的结果计为
`out_of_scope`。这一定义与 Phase 3 的“普通恋爱咨询必须进入 relationship/RAG”要求一致，
也会把关系样本被降级为 `general_chat` 的错误显式暴露出来。

| Split | Cases | Gold branch distribution | Dataset SHA256 | Report generated |
|---|---:|---|---|---|
| Dev | 120 | rag 84 / safety 18 / out_of_scope 18 | `57000e1f8256e7245e4dba7e2c3c5949d3307ac356dba3e8c1408b12f6a171b8` | `2026-09-05T23:26:45Z` |
| Test | 60 | rag 36 / safety 12 / out_of_scope 12 | `635f3b29c23c7574d4ace076037f7892b5f760989132d3e91ede634d6eed4629` | `2026-09-05T23:26:45Z` |

两份 fixture 的 case ID 与 query 均唯一，enum lint 通过；专项 fixture 只被解析，未进入
500 文档 KB 或 embedding corpus。`relationship_stage` 仍只是 fixture metadata：当前
`RouteResult` 不预测它，因此本轮不报告 stage accuracy。

## 2. 验收结果

参考线来自 `LOVEAPP_PHASE3_5_EVAL_USAGE_AND_SPEC.md`。`Test` 只在 Dev 冻结后运行一次，
没有用 Test gold/query 反向调参。

| Metric | Target | Dev | Status | Test | Status |
|---|---:|---:|---|---:|---|
| Branch macro-F1 | >= 0.90 | 0.9491 | pass | 0.8888 | **fail** |
| RAG recall | >= 0.95 | 0.9405 | **fail** | 0.8056 | **fail** |
| High-risk recall | >= 0.98 | 1.0000 | pass | 1.0000 | pass |
| Sensitive recall | >= 0.95 | 1.0000 | pass | 1.0000 | pass |
| Safety -> RAG bypass | <= 0.02 | 0.0000 | pass | 0.0000 | pass |
| Scenario primary accuracy | >= 0.80 | 0.8571 | pass | 0.5278 | **fail** |
| Scenario macro-F1 | >= 0.78 | 0.8834 | pass | 0.5496 | **fail** |
| Goal micro-F1 | >= 0.82 | 0.5491 | **fail** | 0.4486 | **fail** |

Branch accuracy 为 Dev `0.9583`、Test `0.8833`。Safety precision、risk accuracy、
ordinary -> Safety false-positive rate 在两个 split 分别为 `1.0`、`1.0`、`0.0`。
因此两个 split 的 `acceptance_passed` 均为 `false`：Safety/高风险隔离已达标，但整体
Router 不能宣称 Phase 3 完成验收。

### 2.1 Branch confusion

Dev：

| Gold \\ Pred | rag | safety | out_of_scope |
|---|---:|---:|---:|
| rag | 79 | 0 | 5 |
| safety | 0 | 18 | 0 |
| out_of_scope | 0 | 0 | 18 |

Test：

| Gold \\ Pred | rag | safety | out_of_scope |
|---|---:|---:|---:|
| rag | 29 | 0 | 7 |
| safety | 0 | 12 | 0 |
| out_of_scope | 0 | 0 | 12 |

所有 branch 错误均是 normal relationship case 被判为非 RAG（当前结果通常为
`general_chat`），不是 safety bypass。没有 safety 样本被送入普通 RAG。

## 3. Scenario 与 Goal

### 3.1 Scenario per-class F1

| Scenario | Dev P/R/F1 | Test P/R/F1 |
|---|---:|---:|
| pursuit | 1.0000 / 0.8571 / 0.9231 | 1.0000 / 0.6667 / 0.8000 |
| chat_analysis | 1.0000 / 0.8571 / 0.9231 | 0.6667 / 0.6667 / 0.6667 |
| conflict | 1.0000 / 0.5714 / 0.7272 | 0.6000 / 0.5000 / 0.5455 |
| relationship_maintenance | 0.6316 / 0.8571 / 0.7273 | 0.2500 / 0.3333 / 0.2857 |
| boundary | 1.0000 / 1.0000 / 1.0000 | 0.0000 / 0.0000 / 0.0000 |
| breakup | 1.0000 / 1.0000 / 1.0000 | 1.0000 / 1.0000 / 1.0000 |

Dev 的主要混淆是 `conflict -> relationship_maintenance`（5 例），以及少量
`pursuit/chat_analysis/maintenance -> __none__`。Test 的主要问题是
`boundary -> relationship_maintenance`（4 例），`relationship_maintenance -> conflict`
（2 例）或 `__none__`（2 例），以及 `pursuit -> chat_analysis`（2 例）。这说明规则
overlay 对 Dev 的代表性表达有效，但对未见过的短口语和相邻语义边界泛化不足。

### 3.2 Goal per-class F1

| Goal | Dev P/R/F1 | Test P/R/F1 |
|---|---:|---:|
| initiate | 1.0000 / 1.0000 / 1.0000 | 1.0000 / 1.0000 / 1.0000 |
| understand | 0.5000 / 0.0769 / 0.1333 | 0.0000 / 0.0000 / 0.0000 |
| progress | 0.0000 / 0.0000 / 0.0000 | 1.0000 / 0.1667 / 0.2858 |
| repair | 0.0000 / 0.0000 / 0.0000 | 0.0000 / 0.0000 / 0.0000 |
| communicate | 0.6753 / 0.9286 / 0.7819 | 0.5517 / 0.7273 / 0.6274 |
| set_boundary | 1.0000 / 0.3333 / 0.5000 | 1.0000 / 0.0714 / 0.1333 |
| end_relationship | 0.6667 / 0.5000 / 0.5714 | 1.0000 / 0.3333 / 0.5000 |

Goal exact-set accuracy 为 Dev `0.1905`、Test `0.1389`；micro precision/recall 为
Dev `0.6667/0.4667`、Test `0.6154/0.3529`。错误模式集中为把
`understand`、`progress`、`repair` 或 `set_boundary` 过度压成 `communicate`，以及
漏掉 multi-label 的第二个 goal。该问题直接影响 Soft Metadata bonus，不能只看 branch
已经正确就视为无害。

## 4. Required slices

下表为 evaluator 的完整五类 slice 摘要：`n / branch accuracy / error count`。Safety
bypass 在所有列均为 0；risk accuracy 在所有列均为 1.0。

### Length bucket

| Slice | Dev | Test |
|---|---:|---:|
| short | 60 / 0.9167 / 34 | 30 / 0.7667 / 17 |
| medium | 18 / 1.0000 / 0 | 12 / 1.0000 / 0 |
| long | 42 / 1.0000 / 34 | 18 / 1.0000 / 15 |

### Query type

| Slice | Dev | Test |
|---|---:|---:|
| colloquial | 42 / 0.8810 / 34 | 18 / 0.6111 / 17 |
| long_context | 42 / 1.0000 / 34 | 18 / 1.0000 / 15 |
| safety | 18 / 1.0000 / 0 | 12 / 1.0000 / 0 |
| out_of_scope | 18 / 1.0000 / 0 | 12 / 1.0000 / 0 |

### Expected scenario

| Slice | Dev | Test |
|---|---:|---:|
| pursuit | 14 / 0.9286 / 12 | 6 / 1.0000 / 4 |
| chat_analysis | 14 / 0.9286 / 14 | 6 / 0.8333 / 6 |
| conflict | 14 / 0.9286 / 14 | 6 / 0.6667 / 6 |
| relationship_maintenance | 14 / 0.8571 / 10 | 6 / 0.6667 / 4 |
| boundary | 29 / 1.0000 / 6 | 15 / 0.8667 / 6 |
| breakup | 17 / 1.0000 / 12 | 9 / 1.0000 / 6 |

### Expected goal

| Slice | Dev | Test |
|---|---:|---:|
| initiate | 4 / 1.0000 / 2 | 4 / 1.0000 / 2 |
| communicate | 56 / 0.9286 / 40 | 22 / 0.7273 / 18 |
| understand | 26 / 0.9615 / 26 | 10 / 0.9000 / 10 |
| progress | 18 / 0.8889 / 18 | 6 / 0.8333 / 5 |
| repair | 14 / 0.9286 / 14 | 6 / 0.6667 / 6 |
| set_boundary | 42 / 0.9762 / 16 | 26 / 0.8846 / 14 |
| end_relationship | 15 / 1.0000 / 6 | 10 / 1.0000 / 6 |

### Risk level

| Slice | Dev | Test |
|---|---:|---:|
| normal | 102 / 0.9510 / 68 | 48 / 0.8542 / 32 |
| sensitive | 6 / 1.0000 / 0 | 2 / 1.0000 / 0 |
| high | 12 / 1.0000 / 0 | 10 / 1.0000 / 0 |

Slice error count 是“该 slice 中存在任一评测错误”的 case 数，因此 multi-label goal
slice 的 error count 不能相加后当作独立错误数。

## 5. Error attribution 与代表性失败

### 5.1 Attribution

| Attribution | Dev | Test |
|---|---:|---:|
| router_branch_error | 5 | 7 |
| router_scenario_error | 12 | 17 |
| router_goal_error | 68 | 31 |
| safety_error | 0 | 0 |
| rewrite_trigger_error | 0 | 0 |
| query_drift | 0 | 0 |
| decomposition_trigger_error | 0 | 0 |
| subquery_quality_error | 0 | 0 |
| candidate_miss | 0 | 0 |
| rerank_error | 0 | 0 |
| gold_or_dataset_issue | 0 | 0 |

### 5.2 Representative cases

- **Goal overprediction (Dev `router_v1_dev_001`)**：Gold 是
  `initiate + communicate`，Router 产生 `initiate + progress + communicate`；说明
  “自然继续聊/太主动”触发的推进分数仍过强。
- **Scenario boundary failure (Test `router_v1_test_025`)**：Gold 是 `boundary`，
  但短句“查手机/没有私人空间”只落到 `relationship_maintenance`；长上下文版本同一
  语义仍可能只给 maintenance。
- **Scenario pursuit/chat-analysis confusion (Test `router_v1_test_001`)**：Gold 是
  `pursuit`，短句“基本每次都是我先开话题，对方回复不差但很少主动”被判为
  `chat_analysis`，并只给 `communicate`。
- **Branch false negative for RAG (Dev `router_v1_dev_009` / Test
  `router_v1_test_007`)**：正常关系咨询没有被规则识别为 relationship task，结果是
  `general_chat`，因而按 Phase 3 branch contract 计为 `out_of_scope`；这不是 safety
  bypass，但会阻断后续 RAG/rewrite/decomposition。

完整 case trace、预测分数、evidence spans、confusion matrix 和前 20 个失败样本保存在：

- `.data/evals/router_safety_dev.json`
- `.data/evals/router_safety_test.json`
- `.data/evals/router_safety_dev.md`
- `.data/evals/router_safety_test.md`

## 6. 延迟、trace 与 feature flags

| Split | Mean | P50 | P95 | Cases |
|---|---:|---:|---:|---:|
| Dev | 2.017 ms | 1.485 ms | 3.189 ms | 120 |
| Test | 2.637 ms | 1.677 ms | 3.115 ms | 60 |

本次冻结臂的关键设置为：`router_v2_enabled=true`、`contextual_query_rewrite_enabled=false`
和 `query_decomposition_enabled=false`（Phase 3 只测 Router/Safety）。每个 case trace
保留 `task_type`、branch、primary/secondary scenario、primary/secondary goal、risk
level/reasons、各类 score、evidence spans、source、latency；未产生 retrieval trace。

Safety 使用 deterministic high-precision guards 加上下文风险继承/降级逻辑。结果显示
high/sensitive recall 均为 1.0，且没有普通样本误送 safety；这部分可作为后续阶段的冻结
基线。`router_v2` 相对旧 Router 的完整 Dev 对照见下一节。

## 7. A/B/C/D 中的 Phase 3 因果对照

A/B/C/D 的定义是：A=current Router、B=Router V2、C=V2+rewrite、D=V2+rewrite+decomposition。
本节只引用 Phase 3 的 A→B；C/D 的 Phase 4/5 结果不回填为 Router 提升。

| Metric | A (current) | B (Router V2) | Delta B-A |
|---|---:|---:|---:|
| Branch macro-F1 | 0.7954 | 0.9491 | +0.1537 |
| Scenario primary accuracy | 0.2500 | 0.8571 | +0.6071 |
| Goal micro-F1 | 0.3235 | 0.5491 | +0.2256 |
| High-risk recall | 1.0000 | 1.0000 | 0.0000 |
| Safety -> RAG bypass | 0.0000 | 0.0000 | 0.0000 |

The ablation CLI does not run retrieval for these arms; its Phase 5 retrieval fields are
therefore unavailable, not zero gains. No pooled Phase 3-5 score is claimed here.

## 8. Engineering conclusion and required next action

1. **Keep the Safety/branch priority and V2 guard structure.** High-risk and sensitive
   routing is safe on both splits: no bypass, no safety false positive in this fixture.
2. **Do not declare Phase 3 accepted.** Dev still misses RAG recall and Goal micro-F1;
   Test additionally misses Branch macro-F1, Scenario accuracy/macro-F1, and RAG recall.
3. **Continue tuning only on Dev before another freeze.** Focus first on short colloquial
   normal queries, then boundary/conflict/maintenance distinctions, then multi-label goal
   recall (`understand`, `progress`, `repair`, `set_boundary`). Test must remain untouched.
4. **Keep `scenario / goal / relationship_stage` as Soft Metadata in production RAG.**
   This findings report does not justify restoring relevance hard filters; true eligibility
   metadata remains a separate concern.
5. **Do not add Cross-Encoder, BM25, new embeddings, or an LLM reranker in this phase.**
   Router errors must be fixed and attributed before judging downstream retrieval.

The targeted Router/Safety test suite (`tests/test_router_safety_evaluation.py`) passed
(`5 passed` in the verification run). No code, Gold, KB, or fixture changes were made while
writing this findings document; the only change in this subtask is this report.

## 9. Source artifacts

- Specification: `CODEX_PROMPT_LOVEAPP_PHASE3_5_RECTIFICATION.md`
- Evaluation contract: `LOVEAPP_PHASE3_5_EVAL_USAGE_AND_SPEC.md`
- Dev fixture: `evals/rag/phase3_5/loveapp_router_safety_eval_dev_v1.md`
- Test fixture: `evals/rag/phase3_5/loveapp_router_safety_eval_test_v1.md`
- JSON/Markdown reports: `.data/evals/router_safety_dev.{json,md}` and
  `.data/evals/router_safety_test.{json,md}`

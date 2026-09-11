# Memory V1.3 Semantic Ontology 整改与测试报告

日期：2026-09-11。起点：`552501f`。

依据：`LoveApp_Memory_V1.3_Semantic_Ontology_Refactor.md`。

本轮完成语义操作、Memory kind、提取证据性质、命题来源的解耦。正常提取仍为一次 Stage1 加一次批量 Stage2；所有 route 不确定时直接 abstain，不使用 fallback 绕过路由拒绝。既有异常 fallback 机制保留。

这是一份代码与回归交付报告，不表示真实模型的所有行为已经稳定。真实 Flash 抽样仍有事件组合漂移；既有全仓基线也有 3 项失败，详见下文。

## 1. 根因与设计

旧 semantic_role 同时包含操作（new/completion/refinement）、kind（pattern/state）和证据性质（belief）。Router 仅看 role，导致本可用 kind 排除的非法分支也被计入歧义。Stage1 的猜测属性、问题回答关联及同事件依赖不能可靠传到 Stage2 草稿。

现在新 Prompt 只输出以下操作：

`new_proposition / attribute_completion / refinement / correction / uncertain`

历史 `state_update / pattern_extraction / belief_extraction` 等字符串仍可解析；路由时转成规范操作，并分别读取 kind、epistemic。未删除历史 Memory kind 或存量数据。

路由顺序为：枚举每个 role × kind 组合 → 排除非法组合 → 保留所有合法 route → 只有一个合法 route 时采用；多个合法 route 或全部非法时保守 abstain。

| Operation | Kind / epistemic | Stage2 route |
|---|---|---|
| new_proposition / correction | interaction_event | NewEventExtractor（既有批量 new_memory route） |
| new_proposition / correction | interaction_pattern | PatternExtractor |
| new_proposition / correction | relationship_state | StateExtractor |
| new_proposition / correction | believed | BeliefExtractor |
| attribute_completion | interaction_event | EventEnrichmentExtractor |
| refinement | 非 interaction_event / advice_outcome | 既有 trace-only RefinementExtractor |
| 多个合法 route / 无合法组合 | 任意 | abstain，无目标授权 |

这是语义路由的合法组合检查；没有改变 Issue #3.1 中必须先判断语义候选歧义、再校验 mutation compatibility 的 target safety 规则。

新增 `ExtractionEpistemicStatus`，与 Store 原有 `EpistemicStatus` 分开：

| 提取 epistemic | 既有持久化语义 |
|---|---|
| observed / reported | user_reported + confirmed epistemic；仍须经过 Admission，非自动 CONFIRMED row |
| believed | user_belief + uncertain |
| inferred | model_inferred + hypothesis |
| uncertain | uncertain |

Stage2 的高置信输出不能提升 Stage1 的 believed/inferred。组合事件保留最弱证据性质。缺少新字段的旧格式继续使用原有默认值。

## 2. 来源、上下文与治理边界

- `PendingQuestion` 接受旧 `id` 和新 `question_id`，增加 `expected_answer_type`；Prompt 序列化为 `question_id`。
- `answered_pending_questions` 只允许引用当前 open question；question ID 不等于 Memory target。
- 同事件使用 `same_occurrence_group`；只有属于同组、来自当前输入、种类兼容的新事件命题可组合。跨组、跨 state、错误引用都拒绝组合。
- Stage2 提供的 proposition ID 必须与证据一致；一个 ID 不能隐藏相同 evidence 中另一个较弱命题。
- 草稿保留有界 provenance。新 Memory 通过既有 `payload.extraction_provenance` JSON 保存，已验证 InMemory 和 SQLite 重开读取。
- 修复 `NewMemoryDraft.provenance` 被旧 AtomicClaim 的同名 payload alias 吞掉的兼容问题。
- correction 输出更正后的新 claim，由现有治理判断关系与 mutation；refinement 仍只提供语义草稿。
- Event/state projection、Resolver、ClaimRelation、Lifecycle、Store API 均沿用现有实现。没有新增数据库列、迁移、MemoryStatus、multi-target 或写入命令。

## 3. 修改文件

| 文件 | 用途 |
|---|---|
| `src/loveapp/domain/memory.py` | 提取 epistemic、规范操作映射、Stage1 正交字段与旧格式兼容 |
| `src/loveapp/domain/runtime_context.py` | PendingQuestion 字段对齐 |
| `src/loveapp/domain/memory_semantic_units.py` | 草稿 provenance 与既有 payload 的无损衔接 |
| `src/loveapp/adapters/memory/stage2_routing.py` | role × kind × epistemic matrix，合法 route 歧义判断 |
| `src/loveapp/adapters/memory/two_stage.py` | V1.3 Prompt、上下文校验、事件组合、证据继承与 abstention |
| `src/loveapp/evaluation/memory_extraction_diagnostics.py` | parse/schema/routing/empty-output 错误分类 |
| `src/loveapp/evaluation/memory_context_aware_behavioral_anchor.py` | 真实失败归因、来源关联、epistemic 与嵌套属性读取、旧 expectation 待复核标记 |
| `tests/test_memory_semantic_ontology_v13.py` | 76 项独立回归 |
| `tests/test_memory_stage2_prompt_routing.py` | 新规范 Prompt 与 role/kind contract 回归 |

新分类包括 `MODEL_PARSE_FAILURE`、`SCHEMA_VALIDATION_FAILURE`、`ROUTING_AMBIGUITY`、`ROUTING_ABSTENTION`、`EMPTY_STAGE2_OUTPUT`、`RESOLVER_FAILURE`。按预期执行的保守 no-op 不再被记为模型解析失败。

评测另外修正三处实际读取错误：enrichment/refinement 原先没有关联 Stage1 origin；未确认的 user_belief 被误报为 confirmed belief；`attributes.cause` 等嵌套值未参与字段评分。未改 dataset 的任何 expected answer。

## 4. 自动测试

相关定向套件：**165 passed**，其中 V1.3 新增回归 **76 项**。

覆盖五类 epistemic、legacy claims envelope、合法组合过滤、多合法 route、混合批次、来源 ID/证据错配、跨事件拒绝、同事件组合、两种 Store、问题 ID 校验、belief 不授权 enrichment、correction 草稿，以及真实 evaluator 的错误分类。

最终全仓：**2115 passed, 3 failed**。失败均为干净 `552501f` 已复现的 long-tail 基线，见上表；本轮没有新增失败。JUnit 保存于 `.data/evals/memory_semantic_ontology_v13_pytest.xml`。

已在干净 `552501f` worktree 复现的 3 项既有失败：

| Test | 实际 | 原 expectation |
|---|---:|---:|
| `test_longtail_evaluator_runs_all_fixtures_without_mutation` | passed cases = 24 | 25 |
| `test_realistic_longtail_dataset_is_multiturn_and_shadow_only` | retrieval recall = 0.875 | >= 0.9 |
| `test_longtail_write_v1_baseline_uses_production_resolver_read_only` | strict passed = 16 | 8 |

没有通过修改这些 expectation 来消除失败。

修改文件 Ruff、`compileall`、`git diff --check` 均通过。

## 5. 真实 Flash 抽样

使用 production bootstrap 的 `TwoStageMemoryExtractor`、真实 `deepseek-v4-flash`、现有 parsing/local repair，以及 shadow resolver。没有 Store mutation。本轮不是 80-case 全量 live 验收，也不是 lifecycle 写入 replay。

最终生产提取代码运行 8 个样例 × 2 轮，实际 32 次模型调用、0 次失败、0 次 fallback，共 66,554 tokens。随后只修正 evaluator 对嵌套属性的读取，并对相同保存输出重新评分；重评分新增模型调用为 0。

| Case | 内容 | Run 1 | Run 2 | 诊断 |
|---|---|---|---|---|
| A01 | 居住地事实 | PASS | PASS | new_proposition + stable_fact |
| A06 | 压力背景 + 陪伴聊天 | FAIL | PASS | 一轮背景被标为 stable_fact，未组成同一事件；另一轮组成为一个 support event |
| B01 | 回答冲突原因 | PASS | PASS | answer_to_question + cause enrichment，关联 open question |
| C02 | 新一轮争吵及原因 | PASS | PASS | 新 conflict event，原因保存在 attributes.cause |
| D02 | 回复越来越慢 | needs_review | needs_review | kind/metric 正确；旧 gold 仍要求 pattern_extraction |
| E01 | 住址细化 | PASS | PASS | refinement 草稿，无新增写入能力 |
| F01 | 感觉她兴趣下降 | needs_review | needs_review | believed → user_belief/uncertain；旧 gold 仍要求 belief_extraction |
| I01 | 模糊冲突 antecedent | PASS | PASS | resolver no-op，未选择任何 target |

严格保留旧 gold 分数：**5/8、6/8**；两轮各 2 项 needs_review 仍计入未通过，未擅自变成 PASS。

来源、问题回答、perspective 的已计分字段均为 1.0；model parse/schema failures = 0；forbidden semantic violations = 0；new event 被当作 enrichment = 0。样本很小，不应视为总体准确率。

语义签名 drift = **2/8（25%）**：A06 与 I01；通过/失败结果 drift = **1/8**，仅 A06。I01 两轮均保守 no-op。

注意：B01、I01 的 shadow resolver 返回 `no_event_antecedent_message`，不是成功匹配后拒绝多目标。本轮证明没有误写，不能据此宣称完整 target resolution 已通过。

A06 的自动 primary 是 Stage2 semantic decomposition；人工 trace 显示失败轮的 Stage1 已把短暂压力背景标为 stable_fact，随后 Stage2 没有组合。不是 parser/schema 故障，也不能仅归责于 Stage2。

## 6. 报告位置与复现

- [最终重评分摘要](artifacts/memory_semantic_ontology_v13_live_rescored/summary.md)
- [Run 1 完整评分](artifacts/memory_semantic_ontology_v13_live_rescored/run1.json)
- [Run 2 完整评分](artifacts/memory_semantic_ontology_v13_live_rescored/run2.json)
- [两轮漂移](artifacts/memory_semantic_ontology_v13_live_rescored/run_comparison.json)
- [最终真实调用 Run 1 原始记录](artifacts/memory_semantic_ontology_v13_live_final/run1.json)
- [最终真实调用 Run 2 原始记录](artifacts/memory_semantic_ontology_v13_live_final/run2.json)
- 初次诊断记录保存在 `artifacts/memory_semantic_ontology_v13_live/`，不覆盖历史证据。

完整原 dataset SHA256：`395c69ea0468869e785ae02798952eda66ec51a275d7eb8e9a86f1c5dddd8329`。

本轮选中 8 例 SHA256：`c1fa51beb922c376032b280d178cd2ceeaef6b8eeacd796c6c52d458a8dd4adb`。

真实调用命令（重复运行会再次调用 API）：

```powershell
uv run python scripts/evaluate_memory_context_aware_behavioral_anchor.py --case A01 --case A06 --case B01 --case C02 --case D02 --case E01 --case F01 --case I01 --repeat 2 --output-dir artifacts/memory_semantic_ontology_v13_live_replay
```

本机当前配置读取到的 extraction mode 仍为 `single_stage`。`--memory-version v2` 选择 Semantic Judge 路径，不自动选择两阶段提取。要在交互测试中使用本轮 V1.3：

```powershell
$env:LOVEAPP_MEMORY_EXTRACTION_MODE = "two_stage"
uv run loveapp memory-test --memory-version v2 --isolated
```

该环境变量只作用于当前 PowerShell 及其子进程；本轮没有修改 `.env`。上面的 live evaluator 自身会强制选择 `two_stage`。

## 7. 剩余限制

1. 同事件的背景/动作 kind 及合并仍依赖模型；A06 有真实漂移，不以硬编码或强制跨 kind 合并掩盖。
2. 旧 gold 的 pattern/state/belief 操作标签需要按新版四维 contract 人工审核；目前保留原始 expectation 并明确 needs_review。
3. Refinement 继续 trace-only；本轮没有新增多目标、通用 mutation、episode graph 或新的生命周期能力。
4. 本轮没有把错误 fallback、完整关系投影、所有 Stage2 补充属性的召回率宣称为已通过 live 验收。
5. 3 项全仓既有 long-tail 基线失败仍在。提交不包含其他任务的旧 behavioral-anchor 产物或 `loveapp_lhw/`。

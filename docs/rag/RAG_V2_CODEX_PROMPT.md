# Codex Task Prompt — Integrate LoveApp RAG Knowledge Base V2 + Evaluation V2

你现在要在仓库 `robetlhw/loveapp` 中完成一次 **RAG 数据扩容 + 规范评测体系升级**。不要重写 Agent 架构，不要把 RAG 替换成另一个框架；目标是在现有 `KnowledgeDocument -> QdrantKnowledgeStore -> soft_rerank -> AdviceAgent` 链路上完成向后兼容升级。

### 本次任务的实验边界

本轮必须先建立并优化 **当前 RAG Core baseline**：

`raw standalone query -> BGE dense retrieval -> rag_min_score -> candidate pool -> lexical/metadata rerank -> Top-K`

本轮 **不要同时实现**：
- Conditional Contextual Query Rewrite
- Multi-query Decomposition
- Cross-Encoder Reranker
- LLM Reranker

这些是 Core RAG 冻结后的后续独立实验。原因是避免多个变量一起变化，导致无法判断指标提升来自哪一层。当前 Dev/Test Query 都应被视为单轮语义完整的 standalone query；`multi_scenario/multi_goal` 本轮仍作为一次 raw query 检索，用多个 Gold 文档测试 Recall@K。

## 一、输入文件

把我提供的这些 Markdown 放入仓库合适位置：

1. `loveapp_rag_knowledge_base_v2.md` — 500 条正式知识文档
2. `loveapp_rag_eval_dev_v2.md` — 300 条 Dev case
3. `loveapp_rag_eval_test_v2.md` — 200 条 Test case
4. `loveapp_rag_evaluation_spec_v2.md` — 指标与验收规范
5. `loveapp_rag_v2_dataset_card.md` — 数据卡和使用说明

建议路径：

- knowledge: `knowledge/loveapp_rag_knowledge_base_v2.md`
- eval dev: `evals/rag/cases_v2_dev.md`
- eval test: `evals/rag/cases_v2_test.md`
- docs: `docs/rag/`

如果仓库现有目录约定不同，以当前项目结构为准，但不要改变内容语义。

## 二、先阅读并保持兼容的现有实现

重点阅读：

- `src/loveapp/domain/knowledge.py`
- `src/loveapp/domain/enums.py`
- `src/loveapp/adapters/knowledge/markdown.py`
- `src/loveapp/adapters/knowledge/loader.py`
- `src/loveapp/adapters/knowledge/qdrant.py`
- `src/loveapp/adapters/knowledge/scoring.py`
- `src/loveapp/agents/advice.py`
- `src/loveapp/application/scenario_policy.py`
- `src/loveapp/evaluation/baseline.py`
- `src/loveapp/cli.py`
- 现有 `tests/` 与 `evals/rag/cases_v1.jsonl`

现有 `KnowledgeDocument` 已经有 title/scenario/relationship_stages/goals/tags/question/query_variants/answer/context/principles/recommended_actions/sample_phrases/avoid_actions/clarifying_questions/risk_level/source_type/version，不要再造第二套 Document schema。

## 三、扩展 Markdown Knowledge Parser，必须向后兼容

当前 parser 只稳定识别 `标签/问/答` 并通过 section/关键词猜 Scenario/Goals/Risk。V2 文件提供显式 metadata，因此实现以下规则：

### 1. 支持 V2 字段

标量：
- ID
- Scenario
- RiskLevel
- SourceType
- Version
- 问
- 答
- Context

列表：
- RelationshipStages
- Goals
- 标签
- QueryVariants
- Principles
- RecommendedActions
- SamplePhrases（V2 可能为空，但 parser 应支持）
- AvoidActions
- ClarifyingQuestions

列表既允许 Markdown `- item`，也允许单行用逗号/中文逗号/顿号分隔。

### 2. 显式 metadata 优先

- V2 有 `ID` -> 直接作为 `KnowledgeDocument.id`。
- V2 有 `Scenario` -> 使用 `AdviceScenario(value)` 严格校验。
- V2 有 `RelationshipStages` -> 严格解析 `RelationshipStage`。
- V2 有 `Goals` -> 严格解析 `AdviceGoal`。
- V2 有 `RiskLevel` / `SourceType` -> 严格解析枚举。
- 若字段缺失，才回退现有 V1 heuristic。

### 3. V1 必须完全不回归

旧 `loveapp_rag_knowledge_base_formal_v1.md` 仍然必须 validate/ingest 成功，已有 parser tests 必须继续通过。

### 4. 错误必须 fail-closed

未知 enum、重复 ID、缺少问/答、空 ID、非法列表值应明确报错并指出文档 ID/标题，不允许静默降级成错误 scenario。

## 四、解决 500 + 6 Seed 的数量问题

当前 `knowledge ingest` 会执行：

`merge_knowledge_documents(load_seed_documents(), external_documents)`

因此导入 500 条 V2 会额外加入内置 seed。请给 CLI 增加向后兼容开关：

- `--include-seed/--no-seed`
- 默认 `--include-seed`，保持旧行为不变。
- V2 正式命令使用 `--no-seed`，入库后 Qdrant count 必须等于 500。

`knowledge validate` 只校验输入文件，不应偷偷加入 seed。

## 五、实现 V2 Eval Markdown Loader

不要要求我手工把 500 个 case 改成 JSONL。为 `evals/rag/cases_v2_dev.md` 和 `cases_v2_test.md` 增加确定性 parser，输出一个明确的 `RagEvalCase` Pydantic model（可以新建 eval-only schema，不要污染 domain KnowledgeDocument）。

字段：
- id
- query_type
- difficulty
- expected_branch
- expected_primary_scenario
- expected_secondary_scenarios
- relationship_stage
- expected_goals
- expected_risk_level
- no_answer
- no_answer_scope  # optional；NoAnswer=true 时必填：in_domain_uncovered / out_of_domain
- query
- relevant_ids
- graded_relevance
- hard_negative_ids

要求：
- 所有 Relevant/HardNegative ID 必须存在于当前 V2 KB，否则 fail。
- `graded_relevance` 只允许 0/1/2/3。
- `relevant_ids` 必须等价于 grade>=2 的正相关集合（允许 grade=1 只用于 nDCG）。
- Dev/Test case ID 和 Query 不得重复。
- `NoAnswer=false` 时 `no_answer_scope` 必须为空/None。
- `NoAnswer=true && no_answer_scope=in_domain_uncovered` 时：`expected_branch=rag` 且 `relevant_ids=[]`。
- `NoAnswer=true && no_answer_scope=out_of_domain` 时：`expected_branch=out_of_scope` 且 `relevant_ids=[]`。
- 当前固定 No-answer 分布：Dev 24 个 in-domain + 6 个 OOD；Test 16 个 in-domain + 4 个 OOD。

可以保留 v1 JSONL loader；V2 增加 Markdown loader，不要破坏旧 baseline。

## 六、重构 RAG Evaluation：修正指标名称和语义

当前 baseline 的 `recall_at_3/5` 实际上只是“first relevant 是否进入 Top-K”，这应该叫 Hit@K。V2 新 evaluator 必须实现：

- Hit@1 / Hit@3 / Hit@5
- Recall@3 / Recall@5
- Precision@3 / Precision@5
- MRR
- nDCG@3 / nDCG@5
- candidate recall（rerank 前候选是否包含 relevant）
- rerank lift
- hard_negative_leakage@3
- **in-domain no-answer** abstention precision / recall / F1 / false retrieval rate
- normal answered-case coverage
- OOD route accuracy（单独报告，不与 in-domain RAG abstention 混算）
- latency: cold start + warm mean/P50/P90/P95
- slice metrics: scenario/query_type/difficulty/stage/goal

注意：多个 RelevantIDs 时，Recall@K 必须是真正的集合召回率，不要再复用 first_rank。

## 七、增加两个评测模式

### Mode A: Retriever-only / Oracle Metadata

直接使用 case 中 gold scenario/goals/stage 构造 `KnowledgeFilters`，用于隔离检索能力。

纳入：
- 正常 `ExpectedBranch=rag && NoAnswer=false`
- `ExpectedBranch=rag && NoAnswerScope=in_domain_uncovered`

排除：
- `ExpectedBranch=safety`
- `ExpectedBranch=out_of_scope / NoAnswerScope=out_of_domain`

OOD 不应成为主 Retriever-only abstention 指标的一部分；若为了诊断直接把 OOD Query 发给 Retriever，必须单独标成 `OOD direct-retrieval diagnostic`。

### Mode B: End-to-End

真实走 Router -> ScenarioPolicy -> Retriever；报告与 Oracle 的 gap，并按下面类别做错误归因：

- router_error
- candidate_miss
- rerank_error
- threshold_rejection
- gold_or_dataset_issue

`expected_branch=safety` 的 case 在 E2E 模式必须验证进入安全分支，而不是要求普通 RAG 返回答案。`out_of_scope` 同理。

对 `NoAnswerScope=in_domain_uncovered`：
- Router 的正确行为仍是进入 normal relationship advice。
- Retriever 的正确行为是没有足够高置信的知识可注入，而不是强行找到一个“差不多”的文档。
- 报告 nearest candidates、vector/base score、rerank score components，便于人工确认究竟是 threshold 问题还是 benchmark 标注问题。

## 八、增加专用 CLI，不要把所有实验塞进旧 baseline

建议新增：

```powershell
uv run loveapp eval rag --dataset evals/rag/cases_v2_dev.md --knowledge knowledge/loveapp_rag_knowledge_base_v2.md --mode retriever --output .data/evals/rag_v2_dev.json
uv run loveapp eval rag --dataset evals/rag/cases_v2_test.md --knowledge knowledge/loveapp_rag_knowledge_base_v2.md --mode retriever --output .data/evals/rag_v2_test.json
uv run loveapp eval rag --dataset evals/rag/cases_v2_test.md --knowledge knowledge/loveapp_rag_knowledge_base_v2.md --mode e2e --output .data/evals/rag_v2_test_e2e.json
```

参数名可按现有 Typer 风格微调，但必须：
- 可以指定 dataset
- 可以指定 mode
- 可以指定 output
- 可以按 query_type/scenario/case 过滤，方便 debug
- `--fail-on-targets` 时未达到 spec 的目标线返回非零 exit code

旧 `loveapp eval baseline` 必须继续工作。

## 九、把 candidate_limit 变成可配置参数

当前 `QdrantKnowledgeStore.search()` 非 hard filter 使用 `max(limit, 15)`。为了做规范实验：

- 新增 Settings，例如 `rag_candidate_limit`，默认 15，保持旧行为。
- search 时使用 `max(limit, configured_candidate_limit)`。
- 把实际 candidate_limit 继续写入 trace。

不要为了调参直接在代码里改 magic number。

## 十、实验能力：只在 Dev 调参

当前实验统一使用 **raw standalone query**。不要在 sweep 中偷偷加入 query rewrite、query decomposition 或 Cross-Encoder。

实现一个可重复的 Dev sweep runner 或脚本，至少支持：

1. `rag_min_score`: 0.30,0.35,0.40,0.45,0.50,0.55,0.60
2. candidate limit: 15,30,50
3. Top-K: 3,5,7
4. reranker ablation: vector_only / vector+lexical / vector+metadata / full
5. retrieval text ablation: question / question+variants / question+variants+answer / full
6. lexical / metadata rerank 权重调优
7. soft vs hard metadata filter

实验结果保存为 JSON，同时渲染 Markdown 汇总表。不要自动用 Test 选参数。

## 十一、Data Lint

实现一个可从测试或 CLI 调用的 validator，检查：

- KB exactly 500 when validating V2 corpus
- unique IDs
- normalized question duplicates
- enum validity
- required fields
- QueryVariants 3~5
- relevant/hard-negative ID integrity
- dev/test overlap
- query vs KB exact leakage
- near-duplicate warning（字符 bigram Jaccard 或已有 embedding；不必因 warning 阻断）
- `NoAnswerScope` 语义和 ExpectedBranch 一致性
- 对 `in_domain_uncovered` 输出 nearest candidates 供人工复核
- 文本长度分布与极端异常值

不要机械截断长文本。

## 十二、测试要求

至少补：

1. V2 parser full-field round-trip test
2. V1 backward compatibility test
3. explicit metadata overrides heuristic test
4. invalid enum fail-closed test
5. exactly 500 docs / unique ID test
6. no-seed ingest merge count test
7. true Recall@K multi-relevant unit test
8. nDCG unit test
9. in-domain no-answer metric unit test
10. OOD 不混入 Retriever abstention 的 unit test
11. NoAnswerScope / ExpectedBranch consistency test
12. hard-negative leakage unit test
13. Dev/Test leakage/integrity test
14. Candidate limit config test
15. E2E safety case must bypass ordinary RAG test

运行现有 test suite，不能只跑新增测试。

## 十三、建议执行顺序

1. 先跑现有 tests，保存 baseline。
2. 接入 V2 parser，保证 V1/V2 validate。
3. 完成 `--no-seed` 并 ingest V2，确认 Qdrant count=500。
4. 实现 eval markdown loader + lint。
5. 修正 Hit/Recall/nDCG/No-answer/latency 指标。
6. 实现 Retriever-only，再实现 E2E。
7. 以 **raw standalone query** 跑当前参数作为 V2 Dev baseline。
8. 只在 Dev 做 threshold / candidate / Top-K / rerank / retrieval_text / weight sweep。
9. 选定并冻结 Core RAG 参数。
10. 最后跑 Test，一次性生成最终 Core RAG 报告。
11. 本任务到此为止；不要顺手加入 Contextual Rewrite、Query Decomposition 或 Cross-Encoder。

## 十四、最终必须交付

- 修改后的代码
- 新增/更新 tests
- `RAG_V2_DEV_REPORT.md`
- `RAG_V2_TEST_REPORT.md`
- `RAG_V2_ABLATION_REPORT.md`
- 一份简洁 `docs/rag/RAG_V2_IMPLEMENTATION.md`，记录最终配置、命令、指标、失败类型和主要结论

## 十五、禁止事项

- 不要把 Test Query 加入 query_variants。
- 不要根据 Test 失败去改 gold 或继续调参而不记录版本。
- 不要删除旧 V1 parser/JSONL eval 兼容性。
- 不要把 Safety case 强行塞进普通 RAG 生成链路。
- 不要为了达到指标直接降低 benchmark 难度。
- 不要换 embedding 模型作为第一步；先把当前 `bge-small-zh-v1.5` 的系统调透，再把 embedding model comparison 作为独立实验。
- 不要把天气/编程类 OOD 当成主要 RAG No-answer；主 abstention 只看 `in_domain_uncovered`。
- 不要在本轮实现 Conditional Contextual Query Rewrite。
- 不要在本轮把 multi-intent query 自动拆成多个 subquery。
- 不要在本轮加入 Cross-Encoder/LLM reranker。

## 十六、后续阶段说明（只记录，不在本任务实现）

等 Core RAG 的 Dev/Test 报告完成后，再单独规划：

### Phase E1 — Conditional Contextual Query Rewrite
针对：
- “那我怎么办？”
- “她这样是什么意思？”
- “那还要继续吗？”

构造 `history + current_query -> standalone_query` 多轮 benchmark，对比 raw current query 与 contextualized query。

### Phase E2 — Multi-query Decomposition
针对单轮输入里存在 2～3 个独立 information needs 的情况，最多拆 3 个 subquery，分别检索后 merge/dedup/rerank；建立 subquery-level gold。

### Phase E3 — Cross-Encoder Reranker
保留当前 Dense+Lexical+Metadata 作为 baseline，再对 Dense candidate Top-N 使用可本地部署的小型 reranker 精排。不要直接用 DeepSeek 充当第一版 reranker。

完成后先向我汇报：文件变更清单、V2 parse/ingest 数量、所有测试结果、Dev baseline、调参结果、冻结配置、Test 结果，以及 Oracle vs E2E 的差距。

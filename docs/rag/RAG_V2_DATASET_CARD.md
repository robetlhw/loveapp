# LoveApp RAG V2 Dataset Card & Usage Guide

## 1. 目标

本数据包把 LoveApp 的 RAG 知识源从原先约 50 个 QA 扩展为 **500 个原子化 KnowledgeDocument**，并建立 **300-case Dev + 200-case Test** 的独立固定评测集。知识库用于 Qdrant 入库；Dev/Test 只用于评测，绝对不能进入 embedding corpus。

当前 V2 的首要目标是先测清并优化现有：

`raw standalone query -> BGE dense retrieval -> lexical/metadata rerank -> Top-K`

因此本版本不同时引入 Contextual Query Rewrite、Multi-query Decomposition 或 Cross-Encoder，避免多个变量一起变化后无法归因。

## 2. 文件

- `loveapp_rag_knowledge_base_v2.md`：500 条知识文档，Source of Truth。
- `loveapp_rag_eval_dev_v2.md`：300 条开发评测，允许调参。
- `loveapp_rag_eval_test_v2.md`：200 条最终评测，参数冻结后才运行。
- `loveapp_rag_evaluation_spec_v2.md`：指标、验收线、调参协议。
- `CODEX_PROMPT_LoveApp_RAG_V2.md`：交给 Codex 的集成任务说明。

## 3. Knowledge Corpus 分布

| Scenario | 文档数 |
|---|---:|
| `pursuit` | 85 |
| `chat_analysis` | 80 |
| `conflict` | 95 |
| `relationship_maintenance` | 95 |
| `boundary` | 75 |
| `breakup` | 70 |
| **Total** | **500** |

RiskLevel 分布：`normal=466`，`sensitive=11`，`high=23`。

## 4. 长度与结构检查

- Answer 字符数：min=154，P10=224.9，median=244.0，P90=267.1，max=299。
- 近似 retrieval_text 字符数：min=557，P10=619.9，median=658.0，P90=699.0，max=750。
- 每条文档 QueryVariants：固定 3 条。
- 每条文档显式提供 ID / Scenario / RelationshipStages / Goals / RiskLevel / SourceType / Version。

这里不要求每条文本机械等长；目标是保证知识粒度相近、一个文档主要解决一个问题。超长或超短项应作为 lint warning，而不是自动截断。

## 5. Dev/Test 设计原则

Dev 类型分布保持：
`colloquial=45, multi_goal=30, hard_confusion=55, paraphrase=50, multi_scenario=40, noisy_typo=25, long_context=25, no_answer=30`。

Test 类型分布保持：
`long_context=15, noisy_typo=5, multi_scenario=30, colloquial=35, paraphrase=30, hard_confusion=45, multi_goal=20, no_answer=20`。

修改后的全体 ExpectedBranch：
- `rag = 456`
- `safety = 34`
- `out_of_scope = 10`

### 5.1 No-answer 不是 OOD 的同义词

50 个 `NoAnswer=true` 拆成：

| 类型 | Dev | Test | Total | 用途 |
|---|---:|---:|---:|---|
| `in_domain_uncovered` | 24 | 16 | 40 | 主 RAG abstention / threshold 测试 |
| `out_of_domain` | 6 | 4 | 10 | Router / out-of-scope 边界测试 |

`in_domain_uncovered`：
- 问题仍然是完整的恋爱咨询。
- 应进入 normal relationship advice。
- 但当前 500 条 KB 没有足够具体的正确知识。
- 用来检测 RAG 是否会因为“表面相关”而强行塞入错误上下文。

`out_of_domain`：
- 天气、编程、汇率等。
- 顶层 Router 本应拦截。
- 不应作为主 Retriever-only No-answer F1 的组成部分。

因此“上海明天天气怎么样”可以保留少量，但它主要测 Router，不再代表 RAG abstention 的主体。

### 5.2 当前 Query 都是 standalone

当前 Dev/Test 不要求读取历史对话后再补全 Query。Query 应在单轮内具备足够语义。

这意味着本版本主要测 Retriever Core，而不是：
- “那我怎么办？”这类上下文省略的 Query Rewrite。
- 一个完整输入中 2～3 个独立问题的 Query Decomposition。

`multi_scenario / multi_goal` 当前仍以原始完整 Query 做一次检索，通过多个 Gold 文档测 Recall@K，作为后续 decomposition 的 baseline。

## 6. 接入 LoveApp 的关键兼容要求

现有 `KnowledgeDocument` 已具备 V2 所有结构化字段，因此不应新建第二套领域模型。需要做的是向后兼容扩展 `src/loveapp/adapters/knowledge/markdown.py`：

1. V1 只有 `标签/问/答` 时，保持现有 heuristic 分类行为。
2. V2 有显式 metadata 时，显式值优先，并严格验证枚举。
3. V2 `ID` 必须直接成为 `KnowledgeDocument.id`。
4. List 字段解析 Markdown `-` 列表；标量字段允许多行。
5. Qdrant、Retriever、AdviceAgent 的接口保持不变。
6. 当前 CLI ingest 会自动 merge 6 条 seed。V2 为保证总数恰好 500，应增加向后兼容 `--include-seed/--no-seed`，默认保持旧行为，V2 使用 `--no-seed`。
7. Eval schema 增加可选 `NoAnswerScope`，仅 `NoAnswer=true` 时必填。

## 7. 推荐验证顺序

```powershell
uv run loveapp knowledge validate loveapp_rag_knowledge_base_v2.md
uv run loveapp knowledge ingest loveapp_rag_knowledge_base_v2.md --recreate --no-seed
uv run loveapp knowledge search "对方第一次没答应见面，但还是会主动聊天，我还能再约吗？"
```

随后：

1. 固定当前 raw-query 配置，跑 V2 Dev baseline。
2. 调 `rag_min_score / candidate_limit / Top-K / lexical & metadata weights / retrieval_text`。
3. 完成 ablation。
4. 冻结 Core RAG 配置。
5. 最后跑 Test。
6. Core RAG 完成后，另开实验加入 Conditional Contextual Query Rewrite。
7. 再之后测试最多 3 个 subquery 的 Multi-query Decomposition。
8. 如仍有 rerank 空间，再比较本地 Cross-Encoder Reranker。

不要在第一轮同时改 Rewrite、Decomposition 和 Cross-Encoder。

## 8. 后续 Query Planner 的定位

后续可以新增：

`RetrievalQueryPlanner`

逻辑上支持：
- `passthrough`：完整单问题，原样检索。
- `contextual_rewrite`：依赖历史、含省略/指代的问题。
- `decompose`：完整但包含 2～3 个独立 information needs 的输入。

这属于 Agent/RAG 编排层优化，不应该污染当前 Retriever-only baseline。

## 9. 生成后静态质量检查结果

本次 V2 核心静态检查目标：

- Knowledge ID：500/500 唯一。
- Canonical question：500/500 唯一。
- Dev case：300/300 ID 与 Query 唯一。
- Test case：200/200 ID 与 Query 唯一。
- Dev/Test 合计 500 个 Query，无重复。
- `RelevantIDs / GradedRelevance / HardNegativeIDs` 引用必须全部存在。
- 多相关文档 case 中 `RelevantIDs == {grade >= 2}`。
- Eval Query 与 Knowledge question/query_variants 无 normalized exact match。
- `NoAnswerScope` 分布固定为 40 个 `in_domain_uncovered` + 10 个 `out_of_domain`。
- 对 40 个 `in_domain_uncovered`，应额外输出 nearest candidates 供人工复核，防止错误标注“KB 无知识”。

这些静态检查不能替代真实 embedding/Qdrant 评测；它们只保证数据结构、Gold 标注和明显泄漏问题在入库前是可审计的。

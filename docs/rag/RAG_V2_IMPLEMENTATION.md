# LoveApp RAG V2 实现与评测记录

## 范围

本轮在现有 `KnowledgeDocument -> QdrantKnowledgeStore -> soft_rerank -> AdviceAgent` 链路上完成 RAG V2 的数据、检索配置和评测升级。评测输入使用 raw standalone query；本轮没有加入 contextual query rewrite、multi-query decomposition、cross-encoder 或 LLM reranker。

## 实现要点

- `adapters/knowledge/markdown.py` 支持 V2 显式 metadata（ID、Scenario、RelationshipStages、Goals、RiskLevel、SourceType、Version 及完整列表字段），显式值优先；缺失字段继续使用 V1 heuristic。
- V2 enum、必填字段、重复 ID、非法列表值均 fail-closed；V1 知识库和旧 JSONL 评测保持兼容。
- `knowledge ingest` 新增 `--include-seed/--no-seed`，默认保留旧行为；V2 正式导入使用 `--no-seed`，确保 Qdrant 文档数为 500。
- `RagEvalCase` 与 Markdown loader 校验 case 字段、Relevant/HardNegative 引用、GradedRelevance、NoAnswerScope、Dev/Test 唯一性和泄漏规则。
- `QdrantKnowledgeStore.search_detailed()` 暴露 nearest dense pool、threshold 后 candidate pool、reranked pool 和最终 returned；候选池不可用时不伪造 candidate recall 或 rerank lift。
- RAG 参数已配置化：`rag_min_score`、`rag_candidate_limit`、`rag_reranker_mode`、`rag_lexical_weight`、`rag_metadata_weight`、`rag_retrieval_text_mode`、`rag_hard_filter`。
- 本地评测支持隔离的 in-memory Qdrant（`--ephemeral-qdrant`）和批量 query embedding，避免污染生产 collection。

## CLI

```powershell
uv run loveapp knowledge validate knowledge/loveapp_rag_knowledge_base_v2.md
uv run loveapp knowledge ingest knowledge/loveapp_rag_knowledge_base_v2.md --recreate --no-seed

uv run loveapp eval rag `
  --dataset evals/rag/cases_v2_dev.md `
  --knowledge knowledge/loveapp_rag_knowledge_base_v2.md `
  --mode retriever --ephemeral-qdrant `
  --output .data/evals/rag_v2_dev.json

uv run loveapp eval rag-sweep `
  --dataset evals/rag/cases_v2_dev.md `
  --knowledge knowledge/loveapp_rag_knowledge_base_v2.md `
  --output .data/evals/rag_v2_dev_sweep.json

uv run loveapp eval rag `
  --dataset evals/rag/cases_v2_test.md `
  --knowledge knowledge/loveapp_rag_knowledge_base_v2.md `
  --mode retriever --ephemeral-qdrant `
  --output .data/evals/rag_v2_test_oracle.json

uv run loveapp eval rag `
  --dataset evals/rag/cases_v2_test.md `
  --knowledge knowledge/loveapp_rag_knowledge_base_v2.md `
  --mode e2e --ephemeral-qdrant `
  --output .data/evals/rag_v2_test_e2e.json
```

`rag` 支持 `--case`、`--query-type`、`--scenario` 过滤；`--fail-on-targets` 会在未达到验收线时返回退出码 2。`rag-sweep` 只接受 Dev split，拒绝 Test split。

本次归档的 E2E 报告使用规则 Router（LLM correction disabled）。如需在当前环境复现该口径，可在 PowerShell 进程内临时设置 `$env:LOVEAPP_ROUTER_PROVIDER="disabled"` 与 `$env:LOVEAPP_DATE_SEMANTIC_PROVIDER="disabled"`；不设置时，CLI 会按 `.env`/Settings 的 Router provider 配置运行。

## Dev 调参与冻结配置

Sweep 按规范顺序比较 threshold、candidate limit、Top-K、reranker、retrieval text、权重和 soft/hard metadata filter；只使用 Dev，不读取 Test 做选参。

最终冻结配置：

```json
{
  "min_score": 0.6,
  "candidate_limit": 30,
  "top_k": 5,
  "reranker_mode": "full",
  "lexical_weight": 1.5,
  "metadata_weight": 1.0,
  "retrieval_text_mode": "question_variants",
  "hard_filter": true
}
```

Dev 冻结运行（247 个正常 answered case、24 个 in-domain uncovered case）：

| 指标 | Baseline | Frozen |
|---|---:|---:|
| Hit@1 | 0.9757 | 0.9919 |
| Hit@3 | 0.9798 | 0.9960 |
| Recall@5 | 0.9494 | 0.8806 |
| MRR | 0.9777 | 0.9939 |
| nDCG@5 | 0.9555 | 0.9440 |
| No-answer F1 | 0.0000 | 0.9333 |
| False Retrieval Rate | 1.0000 | 0.1250 |
| Coverage | 1.0000 | 1.0000 |
| Hard-negative leakage@3 | 0.0526 | 0.0132 |

冻结配置满足 sweep 阶段硬约束（FRR<=0.15、Coverage>=0.95、hard-negative leakage<=0.15），但仍未达到最终 FRR<=0.10，因此报告标记为未完全通过，并按协议停止继续调参。

## Test 结果（参数未变更）

### Oracle Retriever（185 个 RAG-eligible case）

- Hit@1/3/5 = `1.0000/1.0000/1.0000`
- Recall@5 = `0.8846`，MRR = `1.0000`，nDCG@5 = `0.9494`
- Candidate recall = `0.8876`
- Hard-negative leakage@3 = `0.0167`
- No-answer F1 = `0.8966`，FRR = `0.1875`，Coverage = `1.0000`

主要检索命中目标通过；FRR 高于 `0.10`，所以 Oracle 结果仍标记为未完全通过。

### End-to-End（完整 200 case）

E2E 使用真实 `HybridRouter.route -> ScenarioPolicyRegistry.resolve -> Retriever`。当前 `.env` 的 `router_live_eval_enabled=false`，因此本次是规则 Router、LLM correction disabled 的真实链路评测，不代表 live LLM Router。

- Hit@3 = `0.1479`，MRR = `0.1479`，Coverage = `0.2781`
- No-answer F1 = `0.3333`，FRR = `0.0625`
- Branch accuracy = `0.4950`，primary scenario accuracy = `0.2054`，goal micro-F1 = `0.1943`
- OOD route accuracy = `1.0000`
- High-safety recall = `0.4286`，sensitive branch accuracy = `0`，safety RAG bypass violation rate = `0.3636`
- Oracle/E2E routing degradation = `85.21 pp`（目标 <= `8 pp`）

该结果是当前 Router/Safety 路径的真实失败信号；没有修改 Test gold，也没有把 OOD 或 Safety 混入 Retriever-only 主指标。

## 指标与错误归因

评测同时输出 Hit@K、集合 Recall@K、Precision@K、MRR、nDCG、candidate recall、rerank lift、hard-negative leakage、in-domain no-answer confusion matrix、OOD route accuracy、分支混淆矩阵、冷启动/热查询延迟和 scenario/query_type/difficulty/stage/goal slice。

失败归因按实际 Top-3 cutoff 分为 `router_error`、`candidate_miss`、`rerank_error`、`threshold_rejection` 和 `gold_or_dataset_issue`；in-domain uncovered case 会保留 nearest candidates、vector/base score 与 rerank components 供人工复核。

## 产物

- [RAG_V2_DEV_REPORT.md](../../RAG_V2_DEV_REPORT.md) / `.json`
- [RAG_V2_ABLATION_REPORT.md](../../RAG_V2_ABLATION_REPORT.md) / `.json`
- [RAG_V2_TEST_ORACLE_REPORT.md](../../RAG_V2_TEST_ORACLE_REPORT.md) / `.json`
- [RAG_V2_TEST_REPORT.md](../../RAG_V2_TEST_REPORT.md) / `.json`

## 验证状态

- RAG/Qdrant/parser/CLI/bootstrap 定向测试：`56 passed`（最终组合运行，含评分、知识 CLI、parser 契约及 hard-filter/score 边界回归）。
- `uv run ruff check src tests`：通过。
- 完整测试：`1604 passed, 2 failed`。一项是日期测试硬编码旧日期（期望 `2026-08-29`，当前日期逻辑得到 `2026-09-12`）；另一项是既有 Memory read-path 测试中 legacy transition 记忆仍出现在 `remembered_items`。两项均不属于本轮 RAG 修改，未修改或回滚用户已有改动。

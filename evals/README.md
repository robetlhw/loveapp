# Baseline 评测

这里的评测数据与 `tests/` 分离。单元测试验证代码契约，baseline 用固定语料比较检索、路由、安全和记忆策略在修改前后的行为。

- `routing/cases_v1.jsonl`：Task、Scenario、Goal 和上下文路由。
- `rag/cases_v1.jsonl`：每个查询允许多个相关文档 ID，统计 Recall@K 与 MRR。
- `safety/cases_v1.jsonl`：高风险阳性和容易误报的阴性，统计召回率、精确率和特异度。
- `memory/gate_v1.jsonl`：应抽取与不应抽取的输入。记忆污染率是不应持久化的输入中实际产生记忆的比例。
- `memory/conversations_v1.jsonl`：更完整的多轮原子化、时间、隔离和替代评测。

运行当前配置的 baseline：

```powershell
uv run loveapp eval baseline --output evals/baselines/current.json
```

真实 RAG 评测要求 Qdrant 已启动且 collection 已入库。真实记忆污染评测会调用配置的抽取模型；使用 `--no-live-memory` 可跳过它。报告只记录非敏感配置，不写入 API Key。

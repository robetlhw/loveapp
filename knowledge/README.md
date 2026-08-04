# 外部知识文档

LoveApp 支持 Markdown、JSON 和 JSONL 知识源。当前正式知识源位于项目根目录的 `loveapp_rag_knowledge_base_formal_v1.md`。

约定：

- Markdown 中每个 `##` 问答块会作为一个完整 chunk，不进行固定长度切分。
- `.json` 可以是一条文档对象，也可以是文档数组。
- `.jsonl` 每一行必须是一条完整文档对象。
- `id` 在所有内置及外部文档中必须唯一。
- 初次生成使用 `source_type: synthetic_draft`；人工审核后改为 `reviewed_synthetic`。
- 字段枚举和完整示例参考 `example.json.example`。

验证命令：

```powershell
uv run loveapp knowledge validate loveapp_rag_knowledge_base_formal_v1.md
```

写入 Qdrant：

```powershell
uv run loveapp knowledge ingest loveapp_rag_knowledge_base_formal_v1.md --recreate
```

入库命令会先把内置 Seed 和指定知识源统一解析为 `KnowledgeDocument`，再按 ID 与规范化问题去重合并。正式 Markdown 的 Goal 按每个问答自身的标题、问题和标签生成，不使用章节级统一 Goal。

# Memory Evaluation Set

`conversations_v1.jsonl` 是 LoveApp 记忆系统的独立多轮评测集。它与 `tests/` 中的单元测试使用不同语料，也不会被 pytest 导入。单元测试负责代码正确性；这里的数据用于比较不同抽取模型、Prompt、去重策略和上下文选择策略的效果。

`conversations_v2.jsonl` 补充了听闻信息、竞争者判断和用户自我比较场景，用于验证 `kind` 与 `perspective` 不混淆、原子拆分以及咨询问题丢弃。v1 保持不变，便于比较版本结果。

`conversations_v3.jsonl` 覆盖关系状态维度、同维度状态迁移、渠道限定与互动指标拆分，以及事实和咨询问题同句出现的场景。v1/v2 保持不变，避免修改历史基线。

`conversations_v4.jsonl` 覆盖多轮 interaction pattern 的时长限定更新。它要求频率、回复质量和线下互动保持为独立维度，并验证“持续一个月”只更新兼容的联系模式，不能把咨询性推测写成对方兴趣事实。

交互式测试多组 Memory 更新时运行 `python scripts/test_memory_update_groups.py`。每组使用全新的 InMemory Store，以及独立的 user、relationship 和 conversation ID。输入 `s` 并回车结束当前组、创建下一组；输入 `state` 查看当前组，输入 `q` 结束全部测试。可使用 `--output <path>.json` 保存每轮 trace 和各组最终 Memory。

`gate_v1.jsonl` 另行评估抽取前 Gate。`should_store=false` 的样例若产生任何持久化候选，就计入记忆污染；同时统计 Gate 召回率和特异度，防止只靠“全部跳过”获得低污染率。

`lifecycle_v1.jsonl` 使用固定的 scripted claims，不调用真实模型，专门回归 Predicate 规范化、typed admission、六类 ClaimRelation、状态/计划迁移、错误合并、冲突泄漏和 transition audit。运行方式：

```powershell
uv run loveapp eval memory-lifecycle --output evals/baselines/memory_lifecycle_v1.json
```

该报告按 tag 和 `MemoryKind` 汇总，并包含失败 case 明细和数据集 SHA-256。Strong 升级率是确定性 Policy 指标；真实模型延迟、费用和未标注的 Strong Review Precision 不在离线数据中估算。

`extraction_v1_70.jsonl` 是绕过 Gate 的 Extraction V1 source of truth。它分别观测同一次 Flash 输出的 Raw/Post-Repair 结果，以及重放该 Flash 响应后由真实 `TieredMemoryExtractor` 决定 Strong upgrade 的 Production Cascade：

```powershell
uv run loveapp eval memory-extraction-v1
uv run loveapp eval memory-extraction-v1 --case EXT-055
uv run loveapp eval memory-extraction-v1-sync-langsmith
uv run loveapp eval memory-extraction-v1 --langsmith
```

本地评测不依赖 LangSmith。上传合成数据和 experiment trace 时才需要在进程环境或 `.env` 中配置 `LANGSMITH_API_KEY`；仓库 JSONL 始终是 Golden Set，LangSmith Dataset 只是按稳定 case ID 同步的副本。显式传入 `--langsmith` 但未配置 key 时，命令会记录提示并继续完成 local-only evaluation，不会丢失本地报告。

`normalization_v1.jsonl` 是独立的 56-case Normalization V1 Golden Set。它输入固定、人工确认的 Raw claim，绕过 Gate 和模型 Extraction，离线调用 production candidate normalizer；invalid-shape cases 同时记录 ingress repair/validator contract，但不进入 Admission、Relation、Lifecycle planner 或 Store：

```powershell
uv run loveapp eval memory-normalization-v1
uv run loveapp eval memory-normalization-v1 --case NORM-011
uv run loveapp eval memory-normalization-v1 --slice state_mapping
```

完整运行默认写入 `.data/evals/memory_normalization_v1_baseline.json` 和 `MEMORY_NORMALIZATION_V1_EVAL_REPORT.md`。低于指标门槛属于 baseline 结果，不会让命令失败，也不会自动修改 Normalizer 或 ontology。每条 Golden case 都显式保存 `contract_resolution` 与 contract-outcome scoring；评测器不得在 dataset SHA 之外注入另一份可变 Gold。带 `--case` 或 `--slice` 的调试运行默认写入带筛选标签和时间戳的独立文件，也不会覆盖完整 baseline 或根目录正式报告。

## 使用约定

- 每一行是一个完全独立的评测案例，不共享数据库。
- 按 `sessions[].turns[]` 顺序处理对话，只对包含 `expected_extraction` 的 `user` 消息执行记忆抽取。
- `assistant` 消息用于构成自然对话，不应被当成用户记忆写入。
- 每个候选的 `ref` 是评测集内部引用，不是数据库 ID。评测器需维护 `ref -> memory_id` 映射。
- `atomic_count` 要求该轮信息按可独立更新或删除的最小语义单元拆分，禁止复合记忆。
- `interaction_event` 是一次有边界的互动；`interaction_pattern` 是重复行为或区间汇总。
- `planned_event` 是有明确时间或事件的未来安排；它使用 `period_start/period_end` 和 `expires_at` 控制有效窗口，不能伪装成已经发生的 `interaction_event`。
- `summary_contains` 是语义关键词，不要求生成完全相同的摘要。
- 时间断言以 `reference_time` 为基准；`tolerance_days` 允许相对时间解析存在合理边界差异。
- `expected_control` 表示评测驱动器应调用存储接口执行确认、删除等动作。该字段不要求抽取模型把控制命令识别成普通记忆。
- `expected_context_refs` 用于检查某一轮读取上下文时应出现的既有记忆。
- `must_not_infer` 中的内容不得作为客观事实写入。
- `expected_final_state[].state` 取值为 `active`、`superseded` 或 `deleted`。

修改既有案例会破坏版本间可比性。需要扩充或调整语义时，应新增版本化 JSONL 文件。

## Long-tail Write V2 Retrieval-aware Benchmark

`longtail_write_v2_shared_bank_draft1.jsonl` 与
`longtail_write_v2_cases_draft1.jsonl` 将 V1 的“已给定候选”评测扩展为：

```text
natural-language memory
-> text-only embedding
-> vector Top-20
-> cheap rank Top-5
-> semantic relation Judge
-> production safety validator
-> isolated InMemoryStore write
```

运行真实 production adapters：

```powershell
uv run loveapp eval memory-longtail-write-v2
uv run loveapp eval memory-longtail-write-v2 --case LTW2-011
uv run loveapp eval memory-longtail-write-v2 --slice sustained_update
uv run python scripts/evaluate_memory_longtail_write_v2.py
```

默认 JSON 写入 `.data/evals/memory_longtail_write_v2_<timestamp>.json`，Markdown
写入同名 sidecar；完整运行还会刷新
`MEMORY_LONGTAIL_WRITE_V2_RETRIEVAL_AWARE_REPORT.md`。评测只写每 case 独立的
`InMemoryMemoryStore`，不会连接或修改 production Store。Semantic Judge 的 adapter
target 上限可配置（production live CLI 默认固定为 1）；即使实验配置允许多个 target，
现有 validator 仍会拒绝 multi-target destructive write，并以 fail-closed 方式处理。

Long-tail Write V2 使用冻结的统一候选契约：每个 case 都包含全部 120 条 shared
memories 加 5 条 case-local overlay（共 125 条）。`shared_pools` 仅保留为数据集
语义分组元数据，不再裁剪候选池。跨 shared/overlay 的 exact duplicate 只有在双方
声明同一个 `equivalent_memory_group_id` 时才视为已治理的等价别名；semantic-tag
重叠仅作为诊断，不会触发 review。不得根据模型输出反向修改 Gold 或隐藏未治理的
identity collision。

## 覆盖范围

当前 10 个案例覆盖：

- 单次事件与互动模式共存
- 约会偏好、饮食限制和预算
- 单条输入中多个事实或偏好的原子化拆分
- mixed 与 neutral 情绪属性
- 用户主观判断与客观陈述分离
- 信息纠正与 supersession
- 不应写入的寒暄、知识问题和格式指令
- 同一用户的多段关系隔离
- 绝对时间、区间时间和相对时间
- 建议采纳结果
- 上下文读取及删除/遗忘

建议至少统计：候选级 precision/recall、kind 准确率、原文证据命中率、过度合并率、过度拆分率、时间解析准确率、关系串线率、替代准确率、删除成功率和上下文召回率。

## 快速校验

### Validation boundary evaluation

`normalization_boundary_v1.jsonl` is a deterministic 20-case audit of the
Raw Claim → Generic Validator → Deterministic Normalizer →
Canonical/Normalized Validator boundary. It does not call an LLM or mutate a
Memory Store. Each row records the first failing stage and diagnostic reason.

```powershell
uv run loveapp eval memory-normalization-boundary
uv run loveapp eval memory-normalization-boundary --case BND-001
uv run python scripts/evaluate_memory_normalization_boundary.py
```

The default artifacts are `.data/evals/memory_normalization_boundary_v1.json`,
`.data/evals/memory_normalization_v1_2.json`,
`MEMORY_VALIDATION_BOUNDARY_MIGRATION.md`, and
`MEMORY_NORMALIZATION_V1_2_BOUNDARY_REPORT.md`. The audit does not alter the
Extraction Prompt, ontology, Relation, Lifecycle, or Store.

PowerShell 可逐行验证 JSON 语法：

```powershell
Get-Content evals/memory/conversations_v1.jsonl | ForEach-Object { $_ | ConvertFrom-Json | Out-Null }
Get-Content evals/memory/conversations_v2.jsonl | ForEach-Object { $_ | ConvertFrom-Json | Out-Null }
Get-Content evals/memory/conversations_v3.jsonl | ForEach-Object { $_ | ConvertFrom-Json | Out-Null }
```

# 路由测试集

`cases_v1.jsonl` 用于独立检查顶层 TaskType、关系建议主次场景、主次目标、上下文续接和安全覆盖。它与单元测试中的固定样例分开维护，后续可以用于规则阈值调优和 LLM Router 评测。

`cases_v2.jsonl` 是保留的历史版本化回归集，按完整会话组织，每个案例包含多个 `turns`，用于检查 active task 继承、历史消息、上下文省略、任务切换和跨业务复合请求。修改语义时新增版本，不覆盖旧集。

`cases_v3.jsonl` 补充“第三方向用户推荐内容”和“用户直接要求 Agent 推荐地点”的话语行为区分，并覆盖已有约会 active task 时切回关系沟通的场景。v2/v3 保持不变，用于历史结果对比；当前整改主集是 v4。

字段说明：

- `latest_query`：本轮用户输入。
- `recent_messages`：只用于解析指代或省略的近期消息。
- `active_task`：当前会话正在进行的任务，可为空。
- `expected`：必须满足的主任务、主场景、次场景、目标或风险结果。

v2 字段约定：

- `seed_messages`：会话开始前已经存在的历史消息。
- `turns[].query`：本轮用户输入；`assistant` 用于构造下一轮上下文。
- `turns[].expected.llm_policy`：`never` 表示不应调用 Router LLM，`required` 表示该轮需要语义校正，`optional` 表示两者均可。
- v2 评测使用记录型假 Router Corrector，测量触发策略和 Python 合并保护，不把真实模型波动混入规则回归结果；真实模型评测应另行运行并记录模型、延迟和费用。

评测时应分别统计 TaskType 准确率、主场景准确率、次场景召回率、Goal 多标签 F1 和高风险召回率，不应把 LLM 自报的 confidence 当成真实概率。v2 CLI 还会报告有历史上下文轮次的路由准确率。

v2 另外统计会话/轮次数量、上下文轮次准确率、LLM 调用率、`never/required` 策略违例数、平均规则路由耗时和每个会话的失败轮次。

## v4 Policy Eval

`cases_v4.jsonl` 是整改后的主路由固定集，共 47 个会话、118 turns。数据按 `category`
覆盖 casual、out-of-scope、relationship advice、date planning、日期行动评估、上下文
续接、任务切换、复合任务、pending 未消费前的明确取消、澄清、否定、高风险上下文、Slot 提取/幻觉和 LLM 失败。
旧版本数据集继续保留用于历史行为对比，不用 v4 覆盖它们。

默认运行确定性的 Policy Eval，不访问外部模型：

```powershell
uv run loveapp eval routing
```

Policy 报告默认写入 `evals/baselines/routing_v4_current.json`，并明确记录
`evaluation_mode=policy`、`corrector_kind=recording` 和数据集 SHA-256。`--fail-on-targets`
可用于 CI 门禁；门禁只代表该固定集，不代表线上准确率。

Policy 报告包括 Task Macro Precision/Recall/F1 和各 Task 指标、Scenario/Goal、澄清
Precision/Recall、`clarification_exhausted`、pending continuation/取消、Out-of-Scope Accuracy、Slot Exact Match、字段 Precision/Recall、Slot
Hallucination Rate、LLM 调用策略、fallback/guard/error rate 以及 P50/P95 policy latency。
只有包含 `expected.slots` 的 turn 才进入 Slot 指标，防止不完整标注污染幻觉率。
`slot_hallucination_rate` 衡量经过 Validator 后仍进入 RouteResult 的无依据字段；
`slot_hallucination_attempt_rate` 另外统计模型/fixture 尝试写入但被拒绝的字段，
`slot_validator_block_rate` 则衡量这些尝试是否被成功阻断，三者不能混为一个指标。
带 `llm_correction`/`llm_failure` 的确定性 fixture 在 Live Eval 中仍检查语义结果，但不会
要求真实模型复现注入的 fallback 或 rejected-field 结果。
因此 Policy 报告中的 invalid JSON、evidence failure 和 hallucination attempt rate 是故障注入
覆盖率，不是对真实模型质量的估计；真实模型质量只能引用带 `evaluation_mode=live` 的报告。

## Live Router Eval

Live Eval 必须显式设置保护开关和模型配置，普通测试不会访问外部模型：

```powershell
$env:LOVEAPP_ROUTER_LIVE_EVAL_ENABLED="true"
$env:LOVEAPP_LLM_API_KEY="..."
$env:LOVEAPP_LLM_BASE_URL="..."
$env:LOVEAPP_ROUTER_MODEL="..."
uv run loveapp eval routing --live
```

未设置 `LOVEAPP_ROUTER_LIVE_EVAL_ENABLED=true` 时，`--live` 会在创建模型请求前失败。
Live 默认输出为 `evals/baselines/routing_v4_live_current.json`，不会覆盖 Policy baseline。
可用 `--input-cost-per-million` 和 `--output-cost-per-million` 提供当前模型单价；否则成本
保持为 0，不猜测供应商价格。若一次校正包含结构修复重试，token 和成本按每个真实模型请求累计；
`average_input_tokens` / `average_output_tokens` 按实际请求数平均，报告同时提供 per-turn 指标。
Live 报告额外提供真实 P50/P95 Router latency 和每 turn 估算成本，并以 `evaluation_mode=live` 与 Policy 结果严格区分。

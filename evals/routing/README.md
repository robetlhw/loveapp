# 路由测试集

`cases_v1.jsonl` 用于独立检查顶层 TaskType、关系建议主次场景、主次目标、上下文续接和安全覆盖。它与单元测试中的固定样例分开维护，后续可以用于规则阈值调优和 LLM Router 评测。

`cases_v2.jsonl` 是当前路由整改使用的版本化评测集，按完整会话组织，每个案例包含多个 `turns`，用于检查 active task 继承、历史消息、上下文省略、任务切换和跨业务复合请求。修改语义时新增版本，不覆盖旧集。

`cases_v3.jsonl` 补充“第三方向用户推荐内容”和“用户直接要求 Agent 推荐地点”的话语行为区分，并覆盖已有约会 active task 时切回关系沟通的场景。v2 保持不变，用于历史结果对比。

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

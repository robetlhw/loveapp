# LoveApp 路由系统整改说明

本文记录 2026-08-06 路由整改后的实现边界。它描述的是仓库当前可执行链路，而不是目标架构草图；原始整改要求见仓库根目录的 `loveapp_routing_system_remediation.md`。

## 1. 现状审计

### 1.1 主要代码位置

| 职责 | 实现 |
| --- | --- |
| 混合路由、规则打分、LLM 调用策略与 Guard | `src/loveapp/application/routing.py` |
| LLM RouteCorrector 与结构化响应解析 | `src/loveapp/adapters/routing/openai_compatible.py` |
| Slot 字段级证据校验 | `src/loveapp/application/route_slot_validation.py` |
| LangGraph 顶层分支 | `src/loveapp/agents/conversation.py` |
| 跨轮 pending、澄清与风险状态推进 | `src/loveapp/application/conversation_flow.py` |
| SQLite 会话流状态 | `src/loveapp/adapters/conversation_states.py` |
| 上下文安全规则 | `src/loveapp/safety/policy.py` |
| 路由领域模型 | `src/loveapp/domain/routing.py` |
| Policy/Live 评测 | `src/loveapp/evaluation/routing.py` |

顶层 `TaskType` 为 `general_chat`、`relationship_advice`、`date_planning` 和 `out_of_scope`。`ConversationAgent` 实际执行七个互斥分支：

```text
route
├── high_risk_response
├── sensitive_risk_response
├── clarify_intent
├── relationship_advice
├── date_planning
├── out_of_scope
└── casual_chat
```

分支优先级是 HIGH/SENSITIVE 安全响应、pending 取消、领域外、澄清和普通业务任务。所有分支结束后都进入 `finalize_flow`，持久化短期会话流状态。

### 1.2 整改前基线

`evals/baselines/routing_remediation_pre.json` 保存了整改前的确定性 Policy Eval：13 个多轮会话、36 轮、Task Accuracy 1.0、Context Route Accuracy 1.0、High-risk Recall 1.0、Goal Micro-F1 0.9744、LLM Call Rate 0.1111，`never` 策略违例和 `required` 漏调均为 0。

这些数字来自 `RecordingRouteCorrector`，只证明固定样例上的规则、调用策略和合并保护可重复，不代表真实 Router LLM 的准确率或线上效果。该旧报告也没有新评测 Schema 的 Clarification、Out-of-Scope 和 Slot Hallucination 指标。

## 2. 整改后的设计

```text
用户输入 + 近期消息 + 会话流状态 + 约会任务状态
  ↓
NFKC 标准化
  ↓
SafetyPolicy（当前轮 + 近期用户消息 + 短期风险状态）
  ↓
确定性 Task / Goal / Scenario / Date Mode / Slot 解析
  ↓
明确快路径直接返回，灰区按策略调用 RouteCorrector
  ↓
Pydantic 结构校验 + evidence_spans 原文校验
  ↓
逐字段 Slot Provenance Validation
  ↓
Python Task Guard + 来源优先级合并
  ↓
Clarification / Out-of-Scope / Pending Task 元数据收敛
  ↓
LangGraph 互斥分支
  ↓
SQLite 会话流状态推进
```

主要设计变更如下：

1. `needs_clarification` 已接入真正的 `clarify_intent` 节点，不再只是诊断字段。
2. 新增 `OUT_OF_SCOPE`，将寒暄与产品能力外请求分开处理。
3. LLM 的约会 Slot 在进入任务状态、地图或天气链路前逐字段验证；单个非法字段只丢弃自身。
4. `secondary_tasks` 的首个可执行业务任务保存为 `pending_task`，下一轮可继续、取消或被新任务替换。
5. Safety 会结合近期消息和短期 `RecentRiskState` 识别省略式承接动作，并识别明确降级信号。
6. 路由评测拆为确定性 Policy Eval 和显式开启的真实模型 Live Eval。

## 3. 触发、校验与回退

### 3.1 规则快路径与 LLM 触发

安全扫描永远先执行。以下情况不会调用 Router LLM：HIGH/SENSITIVE 安全输入、未配置 Corrector、明确寒暄、明确领域外请求，以及规则能够可靠处理的明确请求。`forced_task` 会锁定最终主任务，但在需要理解已有约会状态等语义时仍可能调用 Corrector 补充分类信息。

RouteCorrector 主要用于这些灰区：

- Task 得分低于置信阈值，或前两名分差小于歧义阈值；
- 依赖近期消息才能解析的省略或指代；
- 约会候选表达需要区分“评价行动”与“真正规划”；候选只打开语义校正，不单独授权执行日期任务；
- 已存在可恢复约会任务，但本轮不是可确定解析的纯 Slot 补充；
- 跨业务复合请求需要确定主次顺序；规则已识别“先...再...”顺序时，LLM 不能反转该顺序；
- 关系场景或目标存在语义歧义。

`LOVEAPP_ROUTER_CONFIDENCE_THRESHOLD`、`LOVEAPP_ROUTER_AMBIGUITY_MARGIN` 和 `LOVEAPP_ROUTER_CLARIFICATION_THRESHOLD` 集中控制阈值。`LOVEAPP_ROUTER_PROVIDER=disabled` 可完全关闭 RouteCorrector；`auto` 在主模型不是 demo 时启用；`llm` 强制使用已配置的兼容模型。

### 3.2 Task Guard

LLM 是校正器，不是最终授权者。合并时应用以下保护：

- HIGH 风险在调用模型前已截断，LLM 无法覆盖安全分支；
- 明确 `out_of_scope` 只接受同类校正；
- LLM 将业务请求改为 `out_of_scope` 时，规则结果必须本来就是寒暄或领域外，且文本没有明确业务请求；
- LLM 将主任务或 secondary 改为 `date_planning` 时，除了形成可执行的 `place_search`、`itinerary` 或 `modify` 模式，还必须有规则结果、本地 Date Mode、显式 Agent 指令或已有活动日期任务提供语义授权；日期候选本身只打开 LLM 校正路径，不提供执行授权，LLM 自报 mode 不能单独越过 Guard；`evaluate` 与 `category_recommendation` 不能启动行程参数收集；
- 明确关系建议或约会规划不能被轻易降级为 `general_chat`；
- 规则已识别的“先...再...”复合请求会锁定主次顺序，LLM 只能补充标签，不能反转执行顺序；
- 规则确认的 replace/remove/reorder/replan 等约会修改语义优先保留。

Guard 生效时 `task_guard_applied=true`，并同时保留 `rule_task_type`、`llm_task_type` 与最终 `task_type`。

### 3.3 Slot 字段级校验

合并优先级是：

```text
当前轮规则确认值 > 经验证的 LLM 值 > 可恢复任务状态值 > 无值
```

默认预算和交通方式由 `DatePlanningAgent` 在执行阶段使用，不伪装成 Router 抽取结果。校验规则包括：

- `city` / `area` 必须能在当前用户文本中找到；仅在可恢复约会任务中才允许使用近期用户文本或任务状态；
- `budget` 必须有对应数字和预算、总共、每天、元或块等关联表达；
- `budget_scope`、`plan_mode`、`transport_mode` 必须有明确标记；
- `date`、`end_date`、`day_count`、`nights`、`target_day`、`start_time` 只接受确定性解析器或现有任务状态得到的同值；
- 餐饮、活动、偏好、限制和排除词必须是原文子串或已注册规范化别名；
- `replace_place_names` 只检查当前轮用户原文，不能从助手历史计划近似推断；
- 无法解析的嵌套 Slot、未知字段和无证据字段分别记录拒绝原因，不使整个 `RouteCorrection` 失败。

历史用户消息只在当前存在可恢复约会任务时作为 Slot 证据来源；普通历史对话不会单独给当前轮的城市、预算或地点替换提供授权。这样可以避免把旧关系聊天中的地名或偏好误送入地图/天气链路。日期、时间、范围和天数优先采用当前轮确定性解析器或任务状态的同值，不能仅凭 LLM 自报值覆盖已有时间窗口。

### 3.4 Clarification

澄清只在非 HIGH、非 forced、非寒暄、非领域外、非 pending 取消的情况下考虑。可恢复的约会补充、主次明确的复合请求以及有可靠 active task 的上下文不会被多余澄清。

典型触发是“你觉得这样行吗？”一类缺少指代上下文的输入，或路由已标记需要澄清且置信度低于阈值。首次返回带候选项的具体问题；相同原因再次出现时设置 `clarification_exhausted=true`，改为一次性能力边界提示，不重复原问题。状态按 `user_id + relationship_id + conversation_id` 保存，并由 `ConversationFlowState` 推进 `last_clarification_reason` 与 `clarification_attempt_count`。

### 3.5 Pending Secondary Task

复合请求仍只执行一个主任务。首个 `relationship_advice` 或 `date_planning` 次任务会以 `secondary_task` 来源挂起，默认保留 2 个后续轮次。ConversationAgent 与 Policy Evaluator 都使用 `is_pending_continuation()` 识别“继续”“接着来”等短语，并将当前 pending task 映射为 `forced_task`；任务执行完成后清除。明确取消、新业务任务、领域外请求、高风险或 SENSITIVE 安全分支、TTL 耗尽都会清除。安全中断会暂停现有日期任务，泛化的“继续”不会自动重新激活它，之后仍可通过明确约会请求或 Slot 补充恢复。评测报告逐轮记录 `flow_before`、`flow_after`、`pending_continuation` 和 `forced_task`，避免只检查最终 Task 而漏掉跨轮状态错误。

该设计避免在单轮中并行调用多个 Agent，同时让“先分析关系，再安排约会”能够跨轮完成。

### 3.6 上下文 Safety

Safety 检查当前输入，也检查最近 2 至 4 条用户消息和短期 HIGH 状态：

- 近期 HIGH + “现在到楼下了”“继续进去”等承接动作，继续判定 HIGH；
- 近期 HIGH + 明确“已回家、放下武器、联系家人/警方”等有效信号，降为 SENSITIVE 并清除短期 HIGH 状态；
- 否定、避免伤害的求助和无效降级表达分别处理，防止简单关键词误报或错误降级。

HIGH 与 SENSITIVE 都进入专用安全响应，不加载长期关系上下文、不执行 RAG、场景策略或普通建议生成器；进入前会清除 `pending_task`，并暂停当前独立的约会任务状态。顶层路由会通过 `forced_risk_level` 和原因列表把多轮风险结论传给 `AdviceAgent`，避免后者只扫描当前省略句后把风险降回普通建议。两类分支仍会记录本轮用户消息和安全响应。

它仍是确定性安全前置策略，不能替代人工干预、专业危机识别或线上安全运营。

### 3.7 失败回退

| 失败点 | 行为 | 可观测字段 |
| --- | --- | --- |
| 未配置/禁用 RouteCorrector | 使用规则结果 | `llm_used=false` |
| Router 超时、网络、JSON 或 evidence 校验失败 | 整体回退规则结果 | `llm_error`、`fallback_reason=llm_correction_failed` |
| 单个 Slot Schema 非法 | 丢弃该字段，继续解析其余 Correction | `slot_rejected_fields[field]=invalid_schema` |
| Slot 无原文证据 | 丢弃该字段，保留路由与其他合法 Slot | `slot_rejected_fields[field]=no_source_evidence` |
| LLM Task 越过 Guard | 保留规则允许的最终任务 | `task_guard_applied=true` |
| 地图/天气缺少已验证参数 | 由下游追问或使用显式默认策略 | Router 不生成伪 Slot |

## 4. 可观测性

交互模式默认显示路由详情和模块耗时；使用 `--debug-route` 明确开启，或用 `--no-debug-route` 关闭。关键 Trace 包括：

- `routing`：`final_task`、`rule_task`、`llm_task`、Guard、澄清、领域外原因、pending、风险继承、模型、Prompt 版本、token、耗时与回退原因；
- `route_slot_validation`：`accepted_fields_json`、`rejected_fields_json` 和 `field_sources_json`；
- `clarify_intent`、`out_of_scope`、`conversation_flow_state_persistence`：实际分支与状态持久化；
- 路由详情表：Task/Goal/Scenario 得分、Date Mode/Intent/Mutation、Slot 来源和拒绝原因。

生产日志不应记录完整敏感对话或完整 evidence；结构化 Trace 应优先保存分类、原因码、计数、模型元数据和脱敏摘要。

## 5. 评测口径

### 5.1 Policy Eval

当前 `cases_v4.jsonl` 包含 47 个会话、118 轮，覆盖核心任务、边界表达、跨轮状态、安全继承、Task Guard、pending 未消费前的明确取消和 Slot 幻觉样例，并显式标注 pending continuation、sensitive safety 与 clarification exhausted。历史 `cases_v2.jsonl` 与 `cases_v3.jsonl` 保持原样，用于版本化回归对比。

```powershell
uv run loveapp eval routing --policy `
  --dataset evals/routing/cases_v4.jsonl `
  --output evals/baselines/routing_v4_current.json `
  --fail-on-targets
```

Policy Eval 是默认模式，使用确定性的 `RecordingRouteCorrector`。它验证 LLM 应调/不应调策略、Guard、合并、澄清、pending、Slot 拒绝和规则回退，适合 CI 回归。它不测量真实模型质量、真实 token 或真实网络延迟。

### 5.2 Live Router Eval

先在 `.env` 显式设置 `LOVEAPP_ROUTER_LIVE_EVAL_ENABLED=true` 并配置 Router 模型，再运行：

```powershell
uv run loveapp eval routing --live `
  --dataset evals/routing/cases_v4.jsonl `
  --output evals/baselines/routing_v4_live_current.json `
  --input-cost-per-million 0 `
  --output-cost-per-million 0
```

Live Eval 调用真实 RouteCorrector，报告模型延迟、token 与按传入单价估算的成本。结构修复重试会按每次底层模型请求累计 token；`average_input_tokens` 和 `average_output_tokens` 按真实请求数计算，另提供每 turn 平均值。环境保护未显式开启时命令会失败，普通单元测试和 Policy Eval 不访问外部 Router 模型。

两类报告都标记 `evaluation_mode`、`corrector_kind`、数据集路径和 SHA-256，并输出 Task Macro Precision/Recall/F1、逐类指标、Scenario/Goal、Clarification、`clarification_exhausted`、Out-of-Scope、Context Route、Slot Exact Match、Slot Field Precision/Recall、Slot Hallucination Rate、实际 LLM 请求与 Corrector 调用数、LLM Call/Guard/Fallback、P50/P95 和验收目标。Policy 报告还报告 `pending_continuation_count`、`pending_continuation_success_rate` 与逐轮 flow snapshot。任何固定集结果都不能表述为线上准确率。

全组件 baseline 与路由专项评测用途不同：

```powershell
uv run loveapp eval baseline --output evals/baselines/current.json
uv run loveapp eval routing --policy --output evals/baselines/routing_v4_current.json
uv run loveapp eval memory-lifecycle --output evals/baselines/memory_lifecycle_v1.json
```

## 6. 已知限制

- 中文 Task、Goal、Scenario、领域外和安全判断仍依赖有限词表与正则；口语、方言、反讽和隐喻需要持续扩充人工边界集。
- Slot 证据校验是保守的字符串/本地解析校验，不是完整行政区划、时间语义或实体链接系统；别名表目前有限。
- `pending_task` 只保留一个次任务，使用轮次 TTL，不支持并行任务 DAG、墙上时间 TTL 或多设备并发冲突解决。
- 重复歧义会在相同 `clarification_reason` 再次出现时设置 `clarification_exhausted=true`，转入一次性能力边界提示，不会无限重复追问；它不是永久禁用澄清的全局熔断，用户切换到明确任务或新会话后状态会重置。系统不会替用户自动选择业务任务。
- 上下文 Safety 是启发式规则，短期窗口之外的风险、复杂否定和隐晦威胁仍可能漏检或误报。
- Live Eval 受模型版本、服务波动和采样实现影响；成本只有传入真实单价时才有意义。
- 当前 CLI Trace 面向本地调试，不等于具备采样、脱敏、告警和长期聚合能力的生产监控平台。

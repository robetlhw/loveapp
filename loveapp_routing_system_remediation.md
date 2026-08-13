# LoveApp Routing System 整改说明

> 目标读者：负责修改 LoveApp 路由系统的 Codex
> 改造目标：补齐当前 Hybrid Router 的企业级工程边界，同时保持实习项目所需的合理复杂度
> 总体原则：**增量修改、保留现有架构、优先增强可靠性和可评测性，不引入过度设计**

---

# 0. 当前架构与整改定位

当前 LoveApp 路由主链路已经具备：

```text
用户输入
  ↓
文本标准化
  ↓
SafetyPolicy
  ↓
规则 Task / Goal / Scenario 打分
  ↓
日期意图与 Slot 提取
  ↓
是否需要 LLM RouteCorrector
  ├─ 否：直接使用规则结果
  └─ 是：LLM 结构化校正
             ↓
      Pydantic + evidence 校验
             ↓
      Python Route Guard 合并
  ↓
LangGraph 条件分支
  ├─ high_risk_response
  ├─ relationship_advice
  ├─ date_planning
  └─ casual_chat
```

本次不重写路由器，不增加多模型投票、Router Agent Debate、动态图生成或专用训练模型。

本次主要补齐以下问题：

1. `needs_clarification` 目前只记录，不真正进入澄清分支；
2. LLM RouteCorrector 输出的日期 Slot 缺少字段级证据校验；
3. `general_chat` 混合承担寒暄和领域外请求；
4. `secondary_tasks` 能识别但执行层没有消费；
5. SafetyPolicy 对多轮上下文风险感知有限；
6. 当前评测主要使用确定性 Corrector，缺少真实模型评测；
7. 中文规则和正则较多，需要扩展边界样例，而不是无限增加规则。

---

# 1. 执行要求

## 1.1 先审计现状

修改前请确认并记录：

- `HybridRouter` 的真实文件路径；
- `route_by_rules()`、`merge_route_correction()` 和 `_allow_task_override()`；
- `RouteInput`、`RouteResult`、`RouteCorrection`；
- `ConversationAgent` 的 LangGraph 分支；
- `SafetyPolicy`；
- RouteCorrector Prompt 和 evidence 校验；
- `DatePlanningTaskState`；
- 当前 routing evaluator 和 JSONL 数据集；
- 当前完整测试基线；
- 当前最新 routing eval 指标。

先输出一份简短审计报告，列出：

```text
当前路由节点
当前 TaskType
当前 LLM 调用条件
当前 Guard 规则
当前 Slot 提取来源
当前评测集规模
已有失败测试
```

本文中的类名与实际代码不一致时，以仓库实现为准，但行为目标不得改变。

## 1.2 改造约束

- 不重写整个 `routing.py`；
- 不增加十几个顶层 TaskType；
- 不实现任意多任务并行执行；
- 不让 LLM 直接决定工具调用参数；
- 不让 LLM 覆盖高风险路由；
- 不因一个 Slot 非法而拒绝整个路由结果；
- 不把所有请求都送入 Router LLM；
- 不仅依赖 Prompt 保证字段真实性；
- 所有新增行为必须提供测试和 Trace；
- 所有修改应保持现有命令、API 和测试尽量向后兼容。

---

# 2. Milestone 0：基线保护

在功能修改前完成：

- [ ] 运行完整测试并记录结果；
- [ ] 运行当前 routing eval；
- [ ] 保存当前输出为 baseline；
- [ ] 添加 Characterization Tests，锁定现有正确行为；
- [ ] 确认明确寒暄不会调用 LLM；
- [ ] 确认高风险请求优先进入安全分支；
- [ ] 确认明确 relationship advice 不会被 LLM 降级为 general chat；
- [ ] 确认明确 date planning 可以提取现有 Slots；
- [ ] 确认 Router LLM 超时后回退规则结果。

验收：

```text
本次整改不能破坏当前已有明确请求的路由准确性。
```

---

# 3. Milestone 1：增加真正的 Clarification 分支

## 3.1 当前问题

`RouteResult.needs_clarification` 已存在，但 Graph 仍然强制选择：

```text
relationship_advice
date_planning
general_chat
```

因此即使路由器明确表示无法可靠判断，也会强行执行一个业务分支。

## 3.2 目标架构

修改 LangGraph：

```text
route
  ├─ high_risk_response
  ├─ clarify_intent
  ├─ relationship_advice
  ├─ date_planning
  ├─ out_of_scope
  └─ casual_chat
```

高风险仍必须拥有最高优先级。

## 3.3 Clarification 触发条件

不要只检查一个布尔字段。建议增加确定性函数：

```python
def should_clarify_route(
    route_input: RouteInput,
    route: RouteResult,
) -> bool:
    ...
```

满足以下条件才进入澄清：

```text
risk_level != HIGH
且
forced_task is None
且
route.needs_clarification is True
且
没有足够强的当前 active_task 继承
且
当前 Query 不是明确寒暄
且
规则与 LLM 都没有形成可靠主任务
```

可结合：

- `task_confidence`；
- top-2 margin；
- `rule_task_type` 与 `llm_task_type` 是否冲突；
- 是否缺少必要上下文；
- 是否存在两个跨业务候选；
- 是否有 resumable date task。

## 3.4 Clarification 输出

不要生成泛化回复。根据候选任务生成具体问题：

### Relationship advice vs date planning

```text
你是想让我分析这段关系，还是帮你具体安排一次约会？
```

### Date task supplement vs new task

```text
你是在补充上一版约会计划，还是想重新开始一份新计划？
```

### 缺少指代上下文

```text
你说的“这样”是指刚才的沟通方式，还是约会安排？
```

建议 RouteResult 新增或扩展：

```python
clarification_options: list[str]
clarification_reason: str | None
```

如果不希望修改 Schema，可由 `clarify_intent` 节点根据 RouteResult 生成。

## 3.5 防止重复澄清

增加短期状态：

```text
last_clarification_reason
clarification_attempt_count
```

规则：

- 相同澄清问题最多询问一次；
- 用户仍未明确时，使用安全默认或明确说明能力边界；
- 不进入无限追问循环。

## 3.6 测试

应增加：

```text
“你觉得这样行吗？”
无上下文
→ clarify_intent

“你觉得这样行吗？”
上一轮在讨论表白话术
→ relationship_advice，不澄清

“上海”
存在 resumable date task 且缺 city
→ date_planning supplement，不澄清

“帮我分析她的态度，再帮我安排约会”
→ 不直接澄清；主次任务明确

“你好”
→ casual_chat，不澄清

高风险模糊输入
→ high_risk_response，不澄清
```

验收：

- `needs_clarification` 不再只是诊断字段；
- 低置信度输入不会被随意强制路由；
- 明确输入不因阈值波动被多余追问。

---

# 4. Milestone 2：日期 Slot 字段级证据校验

## 4.1 当前问题

LLM RouteCorrector 的 `evidence_spans` 只保证某段文本来自对话，但不能证明每个日期 Slot 都有对应证据。

风险示例：

```json
{
  "city": "上海",
  "budget": 500,
  "evidence_spans": ["帮我安排一次约会"]
}
```

`evidence_spans` 合法，但 city 和 budget 均为模型编造。

## 4.2 新增 Slot Provenance Validator

建议新增：

```text
src/loveapp/application/route_slot_validation.py
```

或者放入现有 routing 模块的独立区段。

核心接口：

```python
@dataclass(frozen=True)
class SlotValidationResult:
    validated_slots: DatePlanSlots
    accepted_fields: dict[str, str]
    rejected_fields: dict[str, str]
    warnings: list[str]

def validate_route_slots(
    route_input: RouteInput,
    rule_slots: DatePlanSlots,
    llm_slots: DatePlanSlots,
) -> SlotValidationResult:
    ...
```

## 4.3 字段验证规则

### city / area

必须满足至少一项：

- 当前或历史用户消息中出现；
- 本地行政区解析器能从原文规范化得到；
- rule extractor 已经识别相同值。

不能仅因 LLM 输出就接受。

### budget

必须满足：

- 原文存在对应数字；
- 数字与“预算、元、块、每天、总共”等语义关联；
- `budget_scope` 能从原文或规则结果确认。

### date / end_date / day_count / nights

优先使用确定性时间解析器。

要求：

- LLM 值必须与本地解析结果一致；
- 或原文存在可验证的相对日期表达；
- 日期范围必须内部一致；
- `day_count` 与 `date/end_date` 冲突时以确定性解析为准；
- 无法确认的 LLM 日期字段丢弃。

### start_time / target_day

必须由明确表达支持：

```text
下午三点
第二天
周六上午
```

不能由模型自行补默认时间。

### dining_keywords / activity_keywords

必须：

- 是原文子串；
- 或属于已注册规范化映射，例如“日本料理”→“日料”；
- 不得生成未提及的具体 POI 名称。

### replace_place_names

必须逐字来自用户消息，不能从助手历史计划外推一个近似名称。

### preferences / constraints / excluded_keywords

必须能追溯到用户原文或已确认的结构化任务状态。

## 4.4 合并策略

当前规则结果和 LLM 结果合并时，改为：

```text
规则确认字段
  >
经过字段级验证的 LLM 字段
  >
已有任务状态字段
  >
默认值
```

注意：

- 默认值应由 DatePlanningAgent 使用；
- 不建议把默认值伪装成 Router 提取结果；
- 丢弃一个非法字段时，不拒绝整个 RouteCorrection；
- 记录 rejected field 原因。

## 4.5 Trace

新增 Trace：

```json
{
  "name": "route_slot_validation",
  "accepted_fields": {
    "city": "上海",
    "budget": 300
  },
  "rejected_fields": {
    "date": "no_source_evidence"
  },
  "source": {
    "city": "rule",
    "budget": "llm_verified"
  }
}
```

## 4.6 测试

### 正例

```text
“上海，预算300，周六下午”
→ city=上海
→ budget=300
→ date/start_time 正确解析
```

### 幻觉字段

用户只说：

```text
帮我安排一次约会
```

LLM 输出：

```text
city=上海
budget=500
```

预期：

```text
city=None
budget=None
rejected_fields 包含 city、budget
```

### 部分接受

用户说：

```text
预算300，地点你看着办
```

LLM 输出：

```text
budget=300
city=杭州
```

预期：

```text
budget 接受
city 丢弃
整个路由仍然有效
```

### 历史补充

上一轮用户说上海，本轮说预算300：

```text
city 可来自 task state
budget 来自当前输入
```

验收：

- 未经证据支持的 Slot 不会进入地图或天气工具；
- RouteCorrector 仍可辅助复杂 Slot 提取；
- 规则结果不被 LLM 幻觉覆盖。

---

# 5. Milestone 3：区分 Casual 与 Out-of-Scope

## 5.1 当前问题

`general_chat` 同时包含：

```text
你好
谢谢
帮我分析 Python 代码
写一篇新闻稿
```

但 `_casual_reply()` 只适合寒暄，不适合领域外请求。

## 5.2 推荐方案

为保持改动适中，有两个可选方案。

### 推荐方案 A：新增 TaskType.OUT_OF_SCOPE

```python
class TaskType(StrEnum):
    GENERAL_CHAT = "general_chat"
    RELATIONSHIP_ADVICE = "relationship_advice"
    DATE_PLANNING = "date_planning"
    OUT_OF_SCOPE = "out_of_scope"
```

LangGraph 新增：

```text
out_of_scope
```

### 备选方案 B：保留 TaskType，增加 GeneralChatMode

```python
class GeneralChatMode(StrEnum):
    CASUAL = "casual"
    UNSUPPORTED = "unsupported"
```

优先选择方案 A，语义和评测更清楚。

## 5.3 分类定义

### general_chat

仅用于：

- 问候；
- 感谢；
- 告别；
- 简短情绪性承接；
- 不需要业务处理的自然对话。

### out_of_scope

用于：

- 编程；
- 学术题；
- 医疗诊断；
- 法律分析；
- 新闻写作；
- 与 LoveApp 能力无关的工具请求。

## 5.4 输出

```text
当前版本主要支持关系咨询和约会规划。你可以直接描述关系问题，或者告诉我约会城市、预算和偏好。
```

不要假装能处理完整领域外任务。

## 5.5 测试

```text
你好
→ general_chat

谢谢
→ general_chat

帮我写一个 Python 爬虫
→ out_of_scope

帮我诊断胸痛
→ out_of_scope 或安全医疗提示，但不能进入 relationship advice

帮我分析她发来的 Python 代码是什么意思
→ out_of_scope，不因“她”进入 relationship advice

我和她因为我总写代码吵架了怎么办
→ relationship_advice
```

验收：

- 非恋爱任务不再统一回复“我在听”；
- 领域关键词不会简单压过真实关系语义；
- 产品能力边界清晰。

---

# 6. Milestone 4：消费 Secondary Tasks

## 6.1 当前问题

Router 能输出：

```text
primary task
secondary_tasks
```

但 Graph 实际只执行 primary task。

## 6.2 简化目标

本次不要实现多任务并行 DAG。

采用：

```text
执行 primary task
  ↓
secondary task 保存为 pending
  ↓
主任务结果末尾询问是否继续
```

## 6.3 数据结构

建议在会话短期状态或 `ConversationTurnResult` 中增加：

```python
pending_task: TaskType | None
pending_task_reason: str | None
```

若已有多个 secondary tasks，只保留第一个最重要任务。

## 6.4 执行逻辑

例如：

```text
用户：
先帮我判断她是不是对我有好感，再帮我安排周末约会。

Router：
primary=relationship_advice
secondary=date_planning
```

执行结果：

```text
先返回关系分析。
结尾：
“如果你准备继续推进，我可以接着根据城市、预算和时间安排约会。”
```

会话状态：

```text
pending_task=date_planning
```

下一轮用户：

```text
好，继续
```

路由器结合 pending task：

```text
date_planning
```

## 6.5 清理规则

以下情况清除 pending task：

- 用户明确取消；
- 用户明确切换到新任务；
- 已执行完成；
- 会话重置；
- 超过合理 TTL；
- 高风险分支触发。

## 6.6 测试

```text
关系分析 + 约会安排复合请求
→ primary advice
→ pending date planning

下一轮“继续”
→ date planning

下一轮“算了，不安排了”
→ pending task 清除

下一轮明确提出新的关系问题
→ 不错误恢复 pending date task
```

验收：

- `secondary_tasks` 不再只是调试字段；
- 不引入单轮双 Agent 高延迟；
- 复合意图可以跨轮完成。

---

# 7. Milestone 5：上下文感知 Safety

## 7.1 当前问题

SafetyPolicy 主要扫描当前输入，省略式多轮表达可能漏检。

示例：

```text
上一轮：我已经拿刀去找他了。
本轮：那我现在就进去。
```

第二句单独看风险词较少。

## 7.2 目标

SafetyPolicy 支持：

```python
def assess(
    current_text: str,
    recent_messages: Sequence[StoredMessage] = (),
    previous_risk_state: RiskState | None = None,
) -> SafetyAssessment:
    ...
```

## 7.3 简化实现

不引入专用安全模型，仅增加：

- 最近 2～4 条消息；
- 上一轮是否为 HIGH；
- 高风险状态短期 TTL；
- 当前输入是否表示继续、执行、靠近、进入、动手等承接动作。

示例状态：

```python
class RecentRiskState(BaseModel):
    level: RiskLevel
    reasons: list[str]
    expires_after_turns: int = 2
```

## 7.4 规则

```text
上一轮 HIGH
+
当前是明显承接/继续动作
→ 继续 HIGH

上一轮 HIGH
+
用户明确说明已远离危险、已联系警方/家人
→ 可降低，但保留敏感提示

当前明确否定危险行为
→ 防止误触发
```

## 7.5 测试

```text
“我拿刀去找她”
→ HIGH

下一轮“我现在到她楼下了”
→ HIGH

下一轮“我已经回家，把刀交给家人了”
→ 不应继续按立即实施动作处理，但可保留安全关注

“我不会伤害她”
→ 不因“伤害”误判 HIGH

“怎样避免伤害自己”
→ 根据现有安全策略谨慎处理，不能只靠否定窗口误判
```

验收：

- 高风险上下文不会因省略主语而丢失；
- 明确否定不产生明显误报；
- Safety 分支仍优先于 Clarification 和普通业务路由。

---

# 8. Milestone 6：路由评测升级

## 8.1 保留两类评测

### Policy Eval

继续使用确定性 `RecordingRouteCorrector`，验证：

- 是否应该调用 LLM；
- Guard 是否正确；
- merge 是否正确；
- clarify 是否触发；
- Slot Validator 是否拒绝幻觉字段；
- secondary task 是否挂起。

### Live Router Eval

新增真实 RouteCorrector 评测，可通过环境变量显式开启。

不得在普通单元测试中强制访问外部模型。

## 8.2 数据规模

将固定路由集扩展到约：

```text
100～150 turns
```

不用追求大型 benchmark，但必须覆盖主要边界。

建议分类：

```text
casual
out_of_scope
relationship_advice
date_planning
date_evaluation_vs_planning
context_follow_up
task_switch
task_cancel
compound_task
clarification
negation
high_risk_context
slot_extraction
slot_hallucination
llm_failure
```

## 8.3 数据划分

建议：

```text
cases_core.jsonl
  高频核心路径

cases_boundary.jsonl
  否定、口语、歧义、跨任务边界

cases_stateful.jsonl
  多轮任务状态

cases_safety.jsonl
  高风险上下文

cases_slot_validation.jsonl
  Slot 提取与幻觉
```

## 8.4 新增指标

```text
Task Macro Precision / Recall / F1
每个 TaskType 的 Precision / Recall
Primary Scenario Accuracy
Goal Micro F1
Clarification Precision / Recall
Out-of-Scope Accuracy
Context Route Accuracy
Slot Exact Match
Slot Field Precision / Recall
Slot Hallucination Rate
LLM Call Rate
Rule Fallback Rate
Guard Activation Rate
Invalid JSON Rate
Evidence Validation Failure Rate
P50 / P95 Policy Latency
P50 / P95 Live Router Latency
Average Input / Output Tokens
Estimated Cost per Turn
```

## 8.5 注意评测真实性

文档必须区分：

```text
确定性 Policy Eval 指标
真实模型 Live Eval 指标
```

不能把 RecordingRouteCorrector 的 100% 结果描述成真实 LLM 准确率。

## 8.6 验收目标

初始可设：

```text
核心 Task Macro-F1 >= 0.90
高风险 Recall = 1.00（固定测试集）
Out-of-Scope Accuracy >= 0.90
Clarification Precision >= 0.85
Slot Hallucination Rate <= 0.02
Never-call-policy violations = 0
Required-call-policy misses = 0
```

以上只针对固定评测集，不得表述为真实线上指标。

---

# 9. 路由可观测性

当前已有：

```text
rule_task
llm_task
task_guard
scores
llm_used
llm_error
```

本次增加：

```text
clarification_triggered
clarification_reason
out_of_scope_reason
pending_task
pending_task_source
slot_accepted_fields
slot_rejected_fields
slot_field_sources
recent_risk_inherited
router_prompt_version
router_model
router_input_tokens
router_output_tokens
router_duration_ms
fallback_reason
```

建议结构化 Trace：

```json
{
  "name": "route_decision",
  "rule_task": "relationship_advice",
  "llm_task": "date_planning",
  "final_task": "relationship_advice",
  "task_guard_applied": true,
  "needs_clarification": false,
  "pending_task": "date_planning",
  "slot_rejections": {
    "city": "no_source_evidence"
  },
  "llm_used": true,
  "fallback_reason": null
}
```

敏感对话内容默认不完整写入生产日志，只记录必要摘要或脱敏 evidence。

---

# 10. 配置与版本管理

新增或确认配置：

```text
LOVEAPP_ROUTER_CONFIDENCE_THRESHOLD
LOVEAPP_ROUTER_AMBIGUITY_MARGIN
LOVEAPP_ROUTER_CLARIFICATION_THRESHOLD
LOVEAPP_ROUTER_CONTEXT_RISK_TURNS
LOVEAPP_ROUTER_LIVE_EVAL_ENABLED
LOVEAPP_ROUTER_PROMPT_VERSION
```

要求：

- 阈值集中配置；
- 不要散落魔法数字；
- Trace 中记录 Prompt 版本；
- baseline 报告记录代码 commit 和数据集版本；
- 修改规则后必须重新运行 eval。

---

# 11. 推荐实施顺序

## Phase A：核心安全整改

- [ ] Clarification Graph 分支；
- [ ] Slot 字段级证据校验；
- [ ] 对应单元测试和 Trace。

## Phase B：产品边界

- [ ] Casual / Out-of-Scope 区分；
- [ ] Context-aware Safety；
- [ ] 对应多轮测试。

## Phase C：复合任务

- [ ] pending secondary task；
- [ ] 下一轮继续、取消和切换逻辑；
- [ ] 状态清理。

## Phase D：评测

- [ ] 扩展到 100～150 turns；
- [ ] Policy Eval；
- [ ] 可选 Live Eval；
- [ ] 输出 Accuracy/F1/Latency/Cost/Slot Hallucination。

---

# 12. 最终验收标准

## 路由正确性

- [ ] 高风险始终优先；
- [ ] 明确 relationship advice 不会被降级成 general chat；
- [ ] 明确 date planning 可以进入 DatePlanningAgent；
- [ ] 约会评估类请求不会错误启动行程参数收集；
- [ ] 含糊请求可以进入 clarification；
- [ ] 寒暄和领域外请求被区分；
- [ ] active task 与 task switch 行为稳定；
- [ ] secondary task 可以跨轮继续。

## Slot 安全

- [ ] Router LLM 无法凭空生成城市；
- [ ] Router LLM 无法凭空生成预算；
- [ ] Router LLM 无法凭空生成日期；
- [ ] 非法单字段被丢弃，不破坏整个路由；
- [ ] 每个接受字段有可追溯来源；
- [ ] 地图和天气工具只收到验证后的字段。

## 工程质量

- [ ] 现有测试全部通过；
- [ ] 新增代码不复制已有业务逻辑；
- [ ] 规则、LLM、Guard 和 Validator 职责清晰；
- [ ] 阈值集中配置；
- [ ] 新增分支有 Trace；
- [ ] 路由失败有规则回退；
- [ ] 不引入额外不必要模型调用。

## 评测

- [ ] Policy Eval 可重复运行；
- [ ] Live Eval 可选运行；
- [ ] 数据集不少于 100 turns；
- [ ] 输出 Task Macro-F1；
- [ ] 输出 Clarification 指标；
- [ ] 输出 Out-of-Scope 指标；
- [ ] 输出 Slot Hallucination Rate；
- [ ] 输出 LLM Call Rate；
- [ ] 输出 P50/P95 和估算成本；
- [ ] 报告明确区分模拟 Corrector 与真实模型。

---

# 13. 非目标

本次不实现：

- 专门训练 Router 分类模型；
- 多个 LLM Router 投票；
- Router Agent 互相辩论；
- 任意任务动态 Graph；
- 多任务并行执行；
- 分布式路由微服务；
- 在线强化学习 Router；
- 自动发现无限 TaskType；
- 全量线上监控平台；
- 与当前三类业务无关的复杂权限系统。

当前项目合理目标是：

```text
规则快速路径
+ LLM 灰区校正
+ Python Guard
+ Slot Provenance Validation
+ Clarification
+ Task State
+ 离线评测
```

---

# 14. Codex 最终交付物

请最终输出：

1. 当前路由系统审计报告；
2. 设计变更说明；
3. 代码修改；
4. 新增/修改文件列表；
5. 单元测试与集成测试结果；
6. 新增路由评测集；
7. Policy Eval 报告；
8. 可选 Live Eval 报告；
9. 改造前后指标对比；
10. README 与架构文档更新；
11. 已知限制；
12. 后续建议。

最终总结必须说明：

```text
Clarification 如何触发
Out-of-Scope 如何识别
LLM Slot 如何验证
secondary task 如何保存和恢复
Safety 如何继承上下文
LLM 何时调用
LLM 失败如何回退
评测结果是什么
仍有哪些未解决问题
```

---

# 15. 推荐简历表述

完成整改并获得真实评测数据后，可以写：

> 设计分层混合路由系统，采用确定性规则处理安全和高置信度快速路径，仅在上下文歧义与跨任务请求中调用 LLM 校正，并通过 Python Guard 限制任务覆盖。基于 LangGraph 管理关系咨询、约会规划、澄清和领域外分支，结合多轮任务状态识别补充、切换与取消；对 LLM 提取的城市、预算和日期执行字段级证据校验，避免幻觉参数进入地图工具。

有真实指标后再补：

> 在自建多轮路由集上实现 Task Macro-F1 为 X，LLM 调用率为 Y%，Slot Hallucination Rate 为 Z%，P95 路由延迟为 N ms。

不得使用确定性 Corrector 的结果冒充真实 Router 模型准确率。

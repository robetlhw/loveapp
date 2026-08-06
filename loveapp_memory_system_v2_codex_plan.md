# LoveApp Memory System V2 改造说明

> 目标读者：在 LoveApp 仓库中执行代码修改的 Codex  
> 改造范围：长期记忆抽取、Predicate 规范化、记忆准入、去重、冲突判断、状态迁移、审计与评测  
> 总体原则：**增量改造、保持现有功能、先测试后重构、模型负责理解、程序负责治理**

---

## 0. 执行要求

在修改代码前，先完成一次仓库审计，不要直接根据本文猜测文件结构。

### 0.1 必须先确认的现状

请在仓库中定位并记录：

1. `MemoryKind`、`MemoryStatus`、`AtomicClaim`、`MemoryItem` 等领域模型。
2. Flash 模型的记忆抽取 Prompt、结构化输出 Schema 和解析逻辑。
3. `predicate` 当前是否为任意字符串，是否已有 alias、normalizer 或白名单。
4. `dedupe_key` 的具体构造方式。
5. SQLite/InMemory Memory Store 的唯一约束、更新、过期和 supersede 逻辑。
6. `PredicateFamily`、`StateTransitionRule`、`plan_memory_transitions` 等生命周期代码。
7. `state_dimension`、`state_value` 当前覆盖哪些状态。
8. `RelationshipPlan` 与 `planned_event`、`action_intent` 的关联方式。
9. Strong 模型当前在什么条件下调用、输出什么字段。
10. 当前测试基线：完整运行单元测试、集成测试和已有 memory eval。

先输出一份简短审计记录，列出：

- 实际文件路径；
- 当前执行链路；
- 当前已注册 PredicateFamily；
- 当前失败测试；
- 本文建议与现有实现冲突之处。

若本文中的类名、路径或字段名与仓库不同，以仓库现状为准，但保持本文定义的行为目标。

### 0.2 改造约束

- 不要一次性重写整个 Memory 模块。
- 不要删除现有 Memory 数据或破坏旧数据库。
- 不要让 LLM 直接执行数据库删除或自由指定任意待删除 ID。
- 不要把所有新旧 Claim 两两交给 Strong 模型，避免成本失控。
- 不要仅靠向量相似度决定合并或 supersede。
- 不要将未知 Predicate 自动映射到高风险状态。
- 所有状态修改必须可解释、可重放、可测试。
- 所有新功能必须提供测试。
- 优先修复现有失败测试，再新增能力。

---

# 1. 改造目标

当前 Memory 系统已经具备原子 Claim、记忆类型、状态、去重、TTL、supersede、PredicateFamily 和计划生命周期等能力，但仍有四个主要问题：

1. **Predicate 漂移**  
   Flash 模型可能针对相同语义生成不同字符串，导致去重和状态迁移失效。

2. **记忆准入策略过于统一**  
   `preference` 与 `relationship_state` 的错误代价不同，不应使用相同写入标准。

3. **跨 Predicate 冲突规则覆盖有限**  
   未注册 Predicate 或同义表达可能导致新旧矛盾状态同时 active。

4. **缺少系统化评测与审计**  
   无法量化去重、错误合并、旧状态残留、Strong 升级率和处理成本。

Memory System V2 的目标是：

```text
自然语言
  ↓
候选原子 Claim
  ↓
Predicate 规范化
  ↓
按 MemoryKind 执行准入策略
  ↓
候选 Claim 与已有 Memory 的关系判断
  ↓
必要时 Strong Verifier
  ↓
确定性生命周期规划
  ↓
事务化写入、合并、替代、过期
  ↓
审计与评测
```

---

# 2. 目标架构

建议将 Memory 写入流程拆成以下组件。可复用现有模块，不要求机械地新增所有文件。

```text
MemoryGate
  └─ 判断本轮是否值得进入长期记忆流程

FlashMemoryExtractor
  └─ 高召回提取候选原子 Claim、证据和结构化信号

PredicateNormalizer
  ├─ Canonical Predicate Registry
  ├─ Alias Mapping
  └─ Custom Predicate Fallback

MemoryAdmissionPolicy
  └─ 根据 MemoryKind、证据明确度和风险决定：
     confirm / proposed / strong_review / reject

ClaimRelationResolver
  └─ 判断候选与已有记忆的关系：
     SAME / COMPLEMENTARY / UPDATE / CONTRADICTION / UNRELATED

StrongClaimVerifier
  └─ 只处理高风险灰区，不直接修改数据库

MemoryLifecyclePlanner
  ├─ exact/normalized dedupe
  ├─ same-dimension supersession
  ├─ PredicateFamily transition
  ├─ plan lifecycle
  ├─ TTL
  └─ unknown-safe fallback

MemoryUnitOfWork
  └─ 在同一事务中提交新 Memory、旧状态更新和审计记录
```

---

# 3. Phase 1：Predicate 规范化

这是本次改造的最高优先级。

## 3.1 建立 Canonical Predicate Registry

为**会影响生命周期的核心业务状态**建立受控词表。不要试图枚举所有自然语言语义。

建议至少覆盖：

### 联系状态

```text
contact.status
state_value:
- normal
- reduced
- unavailable
- restored
```

### 关系阶段或状态

```text
relationship.stage
state_value:
- unknown
- acquaintance
- dating
- committed
- cooling_off
- separated
- reconciled
```

实际枚举必须结合当前业务和已有数据，不要盲目采用全部示例值。

### 关系修复

```text
relationship.repair_status
state_value:
- not_started
- intended
- in_progress
- completed
- failed
```

### 表白流程

```text
confession.status
state_value:
- intended
- executed
- accepted
- rejected
- withdrawn
```

### 计划状态

计划状态优先继续由 `RelationshipPlan` 管理：

```text
plan.status
- proposed
- confirmed
- completed
- cancelled
- expired
```

### 偏好维度

偏好不必将每个具体值注册成 Predicate，建议使用固定维度和开放 value：

```text
preference.food.cuisine
preference.food.spiciness
preference.environment.noise
preference.activity.type
preference.budget.range
```

## 3.2 优先使用 `dimension + value`

对状态型记忆，不再依赖多个自由 Predicate 表示状态：

```json
{
  "canonical_predicate": "contact.status",
  "state_dimension": "relationship.contact_status",
  "state_value": "restored"
}
```

而不是：

```text
contact_unavailable
contact_restored
started_talking_again
communication_recovered
```

核心规则：

- 相同 `state_dimension` 表示处于同一个状态空间。
- 新的 confirmed `state_value` 可以 supersede 旧的 active value。
- 不同 `state_dimension` 可以共存。
- `interaction_event` 仍保留历史，不因状态更新而删除。

## 3.3 支持 Custom Predicate

开放描述性记忆不应被强制塞入核心状态词表。

建议 Schema：

```python
class PredicateType(str, Enum):
    CANONICAL = "canonical"
    CUSTOM = "custom"
```

候选 Claim 至少包含：

```python
canonical_predicate: str | None
custom_predicate: str | None
predicate_type: PredicateType
```

约束：

- `predicate_type == canonical` 时，`canonical_predicate` 必须在注册表中。
- `predicate_type == custom` 时，必须提供 `custom_predicate`。
- 未知 Custom Predicate 默认只能作为描述性记忆或 `proposed` 保存。
- Custom Predicate 不允许直接触发自动 supersede。
- Custom Predicate 不允许绕过高风险记忆准入策略。

## 3.4 Alias Normalization

添加集中式 alias registry，用于兼容：

- 旧数据库中的历史 Predicate；
- Flash 模型偶尔产生的历史写法；
- 当前 PredicateFamily 已注册名称。

示例：

```python
PREDICATE_ALIASES = {
    "resumed_contact": ("contact.status", "restored"),
    "contact_restored": ("contact.status", "restored"),
    "started_talking_again": ("contact.status", "restored"),
    "communication_recovered": ("contact.status", "restored"),

    "ignoring_user": ("contact.status", "unavailable"),
    "contact_unavailable": ("contact.status", "unavailable"),
}
```

要求：

1. Alias Mapping 必须集中维护。
2. 计算 `dedupe_key` 前必须先 normalization。
3. 生命周期规则必须使用 canonical representation。
4. 原始模型输出应保存在审计字段中，方便分析 Predicate 漂移。
5. 不得使用 alias 将模糊表达强制映射成确定状态。

## 3.5 Flash 输出约束

修改 Flash 抽取的 JSON Schema 和 Prompt：

- 核心状态优先从 canonical registry 中选择。
- 无法映射时输出 `custom`，不得自由伪造新的 canonical predicate。
- 必须返回 `evidence_span`。
- 必须返回 `explicitness`。
- 必须返回 `requires_inference`。
- 状态型记忆必须尽可能返回 `state_dimension` 和 `state_value`。

建议字段：

```python
class EvidenceExplicitness(str, Enum):
    EXPLICIT = "explicit"
    STRONGLY_IMPLIED = "strongly_implied"
    WEAKLY_INFERRED = "weakly_inferred"
    SPECULATIVE = "speculative"
```

示例：

```json
{
  "kind": "relationship_state",
  "predicate_type": "canonical",
  "canonical_predicate": "contact.status",
  "custom_predicate": null,
  "state_dimension": "relationship.contact_status",
  "state_value": "restored",
  "subject": "partner",
  "object": "user",
  "summary": "对方已经恢复与用户联系",
  "evidence_spans": ["她今天主动找我聊了很久"],
  "explicitness": "explicit",
  "requires_inference": false,
  "model_confidence": 0.91
}
```

`model_confidence` 可以保留，但不得作为唯一写入依据。

---

# 4. Phase 2：类型化记忆准入

## 4.1 新增 Memory Admission Policy

为每种 `MemoryKind` 设置独立策略。具体阈值不得凭感觉固定，应先提供合理初值，再通过评测校准。

建议配置：

```python
@dataclass(frozen=True)
class MemoryAdmissionPolicy:
    direct_confirm_threshold: float
    strong_review_threshold: float
    allow_proposed: bool
    require_explicit_evidence: bool
    require_multi_evidence: bool
    default_ttl_days: int | None
    high_risk: bool
```

示例初值：

| MemoryKind | 直接确认 | Strong 灰区起点 | proposed | 其他要求 |
|---|---:|---:|---|---|
| preference | 0.75 | 0.55 | 允许 | 主体尽量明确 |
| stable_fact | 0.85 | 0.65 | 允许 | 需要直接证据 |
| interaction_event | 0.80 | 0.60 | 允许 | 时间和事件身份 |
| interaction_pattern | 0.92 | 0.70 | 允许 | 明确频率词或多证据 |
| planned_event | 0.85 | 0.65 | 允许 | 区分意向与已确定计划 |
| action_intent | 0.75 | 0.55 | 允许 | 使用较短 TTL |
| advice_outcome | 0.85 | 0.65 | 允许 | 尽量关联历史建议 |
| relationship_state | 0.95 | 0.70 | 谨慎或不允许 | 必须有强直接证据 |

注意：

- 以上数字只是初始化参数。
- 不允许仅使用 LLM 自报 `confidence`。
- `relationship_state` 的推测性表达不得直接 confirmed。
- Strong 模型不能将原文没有支持的内容“验证”为事实。

## 4.2 计算可解释的 Admission Score

基于以下信号：

- Flash 模型自报 confidence；
- `explicitness`；
- evidence 是否为原文真实子串；
- subject 是否解析明确；
- 时间是否合理；
- 是否需要跨句推断；
- 是否与现有记忆冲突；
- 是否有多次证据；
- MemoryKind 风险等级。

可先采用规则评分，不要求训练新模型。

要求：

- 每个分数项必须可解释。
- 返回 score breakdown，写入审计。
- 阈值必须配置化。
- 不得把 score 宣称为经过校准的真实概率。

## 4.3 决策类型

统一输出：

```python
class AdmissionDecision(str, Enum):
    CONFIRM = "confirm"
    PROPOSE = "propose"
    STRONG_REVIEW = "strong_review"
    REJECT = "reject"
```

典型规则：

- 低风险、证据明确、分数高：`CONFIRM`
- 有价值但证据不充分：`PROPOSE`
- 高风险或与现有状态冲突：`STRONG_REVIEW`
- 推测性高风险状态、无证据、Schema 不合法：`REJECT`

---

# 5. Phase 3：Claim Relation Resolver

## 5.1 统一关系类型

新增：

```python
class ClaimRelation(str, Enum):
    SAME = "same"
    COMPLEMENTARY = "complementary"
    UPDATE = "update"
    CONTRADICTION = "contradiction"
    UNRELATED = "unrelated"
    UNCERTAIN = "uncertain"
```

定义：

### SAME

同一事实的重复表达。

```text
她喜欢日料
她爱吃日本料理
```

行为：

- 合并证据；
- 更新 `last_confirmed_at`、importance 或 confidence；
- 不新增第二条 active Memory。

### COMPLEMENTARY

相关但可同时成立。

```text
她喜欢日料
她尤其喜欢寿司
```

行为：

- 独立保留或合并为更丰富结构；
- 不能因语义接近直接去重。

### UPDATE

同一状态维度的新版本。

```text
她住在上海
她上个月搬到了杭州
```

行为：

- 旧状态 `superseded`；
- 新状态 active；
- 保留版本链。

### CONTRADICTION

当前不能同时成立，但时间、证据或上下文不足以直接判断谁替代谁。

```text
她不吃辣
她很喜欢吃辣
```

行为：

- 高风险或歧义时升级 Strong；
- 未能确认时不得同时 confirmed；
- 可以将新项设为 proposed 并标记冲突。

### UNRELATED

独立信息，正常新增。

### UNCERTAIN

本地规则无法判断，进入 Strong 或保守存储。

## 5.2 本地快速判断优先

在调用 Strong 前，按以下顺序处理：

1. 相同 active `dedupe_key` → `SAME`
2. 相同 `state_dimension` 且 value 不同 → `UPDATE` 或 `CONTRADICTION`
3. 已知 PredicateFamily 迁移 → `UPDATE`
4. 相同事件内容但不同时间/事件 ID → `UNRELATED`，不得错误去重
5. 已注册互斥偏好维度 → `UPDATE`/`CONTRADICTION`
6. 无法判断 → `UNCERTAIN`

## 5.3 Strong Verifier

Strong 模型只处理：

- 高风险 MemoryKind；
- `UNCERTAIN`；
- 与已确认状态发生冲突；
- Custom Predicate 可能映射到核心状态；
- evidence 与 claim 的蕴含关系不明确；
- 需要区分 `COMPLEMENTARY` 与 `CONTRADICTION`。

Strong 输入：

- 原始用户消息；
- 候选 Claim；
- 最相关的少量现有 Memory；
- canonical registry 中可选项；
- 明确决策定义。

Strong 输出：

```json
{
  "claim_supported": true,
  "relation": "update",
  "canonical_predicate": "contact.status",
  "state_dimension": "relationship.contact_status",
  "state_value": "restored",
  "target_memory_ids": ["..."],
  "reason": "用户明确说明对方重新主动联系",
  "evidence_sufficient": true
}
```

约束：

- Strong 只提供判断信号。
- Python Policy 最终决定数据库修改。
- `target_memory_ids` 必须属于本次传入的候选集合。
- Strong 不得返回任意数据库 ID。
- evidence 不充分时不能输出 confirmed update。
- 所有 Strong 调用记录模型、Prompt 版本、输入摘要、输出和成本。

---

# 6. Phase 4：生命周期与 PredicateFamily 重构

## 6.1 PredicateFamily 的新定位

保留 PredicateFamily，但缩小职责：

### `state_dimension`

负责大多数同维度单值状态更新：

```text
relationship.contact_status:
unavailable → restored
```

默认规则：

- 同一维度只有一个 confirmed active value。
- 新 confirmed value supersede 旧 confirmed value。
- 新 proposed value不得自动关闭旧 confirmed value。
- `unknown` 可以被明确值替代。
- 不同维度不得互相替代。

### `PredicateFamily`

只负责跨 `MemoryKind`、跨维度或特殊业务过程：

```text
action_intent(confession intended)
  → interaction_event(confession executed)

planned_event
  → interaction_event(completed)

repair intent
  → repair in progress
  → repair outcome
```

不要为普通同维度状态继续堆叠大量 PredicateFamily 规则。

## 6.2 未知规则的安全回退

当新的 Claim：

- 无法映射到 canonical predicate；
- 不属于已知 PredicateFamily；
- 没有明确 same-dimension 规则；
- Strong 也不能确认；

则：

1. 保存为 `custom` 或 `proposed`；
2. 不自动 supersede 旧状态；
3. 标记 `lifecycle_review_required = true`；
4. 写入审计原因 `unknown_transition`；
5. Context Assembler 中降低其作为“当前事实”的权重；
6. 不得物理删除任何旧 Memory。

## 6.3 Interaction Event 与 Current State 分离

必须保证：

```text
历史事件：之前有一段时间不联系
当前状态：已经恢复联系
```

可以同时存在。

状态迁移只关闭旧 `relationship_state` 或旧工作状态，不删除历史 `interaction_event`。

## 6.4 Interaction Pattern

当前阶段只做安全增强，不要求实现复杂自动聚类。

最低要求：

- 单次 event 不得轻易生成 confirmed `interaction_pattern`。
- 用户明确说“经常、总是、每次、最近一直”等，可以作为直接证据。
- 多条相似 event 可作为 corroboration 信号，但不要在本次改造中引入复杂聚类系统。
- 没有多证据或明确频率表达时，pattern 最多 proposed。

## 6.5 Planned Event 与 Action Intent

继续使用现有 `RelationshipPlan`，增强匹配：

匹配优先级：

1. 显式 `plan_id` / `completes_plan_id`
2. activity type
3. participants
4. time window
5. location
6. normalized lexical similarity

计划完成、取消或过期时：

- 更新 `RelationshipPlan.status`
- 关闭对应 active `planned_event`
- 关闭或 supersede 关联 `action_intent`
- 保留完成事件作为历史
- 写入 lifecycle audit

若匹配不确定：

- 不自动关闭多个计划；
- 升级 Strong 或标记人工/后续确认；
- 不得按纯向量相似度直接完成计划。

---

# 7. Dedupe 改造

## 7.1 `dedupe_key` 必须基于规范化结果

建议包含：

```text
user_id
relationship_id
MemoryKind
normalized subject/entity
canonical predicate 或 custom predicate namespace
normalized object/value
state_dimension
必要的时间/事件标识
```

注意：

- 对状态型 Memory，时间不应导致同一状态无法识别为更新。
- 对 `interaction_event`，时间和 event identity 必须参与，避免把多次相似事件错误合并。
- 对偏好，应区分“喜欢日料”和“喜欢寿司”。
- 对计划，应区分不同日期的相同活动。

## 7.2 合并行为

SAME 时不要只丢弃新证据。应至少考虑：

- 合并 evidence；
- 更新 `last_seen_at`；
- 提升 proposed → confirmed；
- 更新 confidence/importance，但避免无上限累加；
- 保存 extraction provenance；
- 幂等处理重复消息。

## 7.3 防止错误合并

以下场景必须有测试：

```text
她喜欢日料 ≠ 她喜欢寿司
上周因回复慢吵架 ≠ 昨天再次因回复慢吵架
计划周五吃饭 ≠ 计划下周五吃饭
用户喜欢安静环境 ≠ 对方喜欢安静环境
```

---

# 8. 数据模型与数据库迁移

根据现有 Schema 做最小增量迁移。建议考虑新增：

```text
canonical_predicate
raw_predicate
predicate_type
custom_predicate
state_dimension
state_value
explicitness
requires_inference
admission_score
admission_decision
lifecycle_review_required
last_seen_at
prompt_version
extractor_model
verifier_model
```

新增审计表，例如：

```text
memory_transition_audit
```

字段建议：

```text
id
user_id
relationship_id
source_message_id
incoming_memory_id
target_memory_ids
relation
decision
rule_name
admission_score
score_breakdown_json
raw_predicate
canonical_predicate
extractor_model
verifier_model
prompt_version
evidence_json
reason
created_at
```

要求：

- 提供数据库 migration。
- 旧记录可读。
- 旧 Predicate 通过 alias 在读取或后台迁移时规范化。
- 不要求一次性重写全部历史数据。
- 可采用 lazy migration：旧记录首次参与判断时规范化。
- 迁移必须可回滚。
- 不物理删除旧数据。

---

# 9. 事务、幂等与并发

一次生命周期更新可能包含：

1. 插入或合并新 Memory；
2. 更新旧 Memory 为 superseded；
3. 更新 `supersedes_id`；
4. 更新 RelationshipPlan；
5. 写 transition audit；
6. 更新检索索引。

要求：

- 核心数据库修改放在同一事务或 Unit of Work 中。
- 引入幂等键，例如：
  `source_message_id + normalized claim identity`
- 重试不得重复创建 active Memory。
- 使用 optimistic locking/version，或在 SQLite 场景采用合适事务锁。
- 向量索引更新失败时记录待重试任务，不得让主数据库进入未知状态。
- 修复现有 Clock 注入不一致：Service、Store、TTL 测试统一使用注入时钟。

---

# 10. Context Assembler 改造

最终 Prompt 不得把 proposed、冲突项和 confirmed 当前状态等权展示。

建议分区：

```text
[已确认当前状态]
[已确认长期事实与偏好]
[近期历史事件]
[可能但尚未确认的信息]
[存在冲突、需要谨慎使用的信息]
```

规则：

- `superseded`、`expired`、`rejected` 默认不进入当前上下文。
- proposed 信息必须显式标记“不确定”。
- 同一 `state_dimension` 只注入最新 confirmed active value。
- 若存在未解决冲突，不要同时作为两个确定事实呈现。
- Custom Predicate 默认按描述性记忆处理。
- 保留 query relevance、时间、importance 和 role quota。
- 不依赖 Context Assembler 替代存储层生命周期治理。

---

# 11. 测试计划

## 11.1 单元测试

### Predicate Normalizer

- canonical 输入保持不变；
- alias 正确映射；
- 未知值进入 custom；
- 模糊表达不错误映射为高风险状态；
- 旧 Predicate 兼容。

### Admission Policy

- preference 的直接写入；
- speculative relationship_state 被拒绝或 Strong review；
- interaction_pattern 单证据不能 confirmed；
- explicit evidence 提高准入；
- evidence 不在原文中时降级；
- 不同 policy 可配置。

### Claim Relation

覆盖：

- SAME
- COMPLEMENTARY
- UPDATE
- CONTRADICTION
- UNRELATED
- UNCERTAIN

### Lifecycle

- same-dimension confirmed update；
- proposed 不关闭 confirmed；
- unknown → known；
- PredicateFamily 跨 kind 迁移；
- unknown transition 安全回退；
- event 历史保留；
- planned event complete/cancel/expire；
- TTL；
- clock 一致性；
- 幂等重试。

### Dedupe

- 同义偏好合并；
- 子类别偏好不错误合并；
- 不同时间事件不错误合并；
- 不同 relationship 不串线；
- 重复消息幂等。

## 11.2 集成测试

构造完整多轮场景：

### 场景 A：联系恢复

```text
1. 她最近不回复我
2. 她今天主动找我聊天了
```

预期：

- 历史不回复事件保留；
- 当前 contact state = restored；
- 旧 unavailable state superseded；
- Prompt 不再把 unavailable 当作当前状态。

### 场景 B：推测分手

```text
她最近不理我，我感觉可能快分手了，但我们现在还是男女朋友
```

预期：

- 不写入 confirmed separated；
- 当前关系仍保持 committed/dating；
- “担忧分手”可作为 concern 或 proposed 描述；
- evidence 与决策可审计。

### 场景 C：偏好变化

```text
1. 她以前不吃辣
2. 她现在能接受微辣了
```

预期：

- 不应简单作为 SAME；
- 新偏好更新旧偏好；
- 历史可追溯；
- Prompt 使用当前偏好。

### 场景 D：重复偏好

```text
1. 她喜欢日料
2. 她很爱吃日本料理
```

预期：

- 一个 active preference；
- evidence 合并；
- 不产生重复权重。

### 场景 E：相似但不同偏好

```text
1. 她喜欢日料
2. 她尤其喜欢寿司
```

预期：

- 两条可以共存或结构化为层级关系；
- 不错误去重。

### 场景 F：表白流程

```text
1. 我准备下周表白
2. 我昨天已经表白了
3. 她接受了
```

预期：

- intent → executed → accepted 正确迁移；
- 历史事件保留；
- 当前状态不残留“准备表白”。

### 场景 G：计划完成

```text
1. 周六准备一起爬山
2. 上次爬山回来后她还照顾我
```

预期：

- 尽可能识别对应计划已完成；
- 匹配不确定时不误关其他计划；
- 不再把已完成爬山显示为未来计划。

### 场景 H：未知 Predicate

Flash 产生未注册表达：

```text
started_talking_again
```

预期：

- alias 命中则规范化；
- alias 不命中则 custom/proposed；
- 不无依据关闭旧状态；
- 记录 unknown transition 指标。

---

# 12. Evaluation 数据集与指标

在现有测试之外，新增一个小型、可重复运行的 Memory Lifecycle Eval。

建议初版 200～500 条样本，按以下类别组织：

```text
tests/eval_data/
  predicate_normalization.jsonl
  duplicate_pairs.jsonl
  complementary_pairs.jsonl
  update_pairs.jsonl
  contradiction_pairs.jsonl
  event_identity.jsonl
  plan_lifecycle.jsonl
  multi_turn_memory.jsonl
```

每条样本应包含：

- 输入消息或多轮消息；
- 现有 Memory；
- 预期 canonical predicate；
- 预期 admission decision；
- 预期 relation；
- 预期 active memories；
- 预期 superseded IDs；
- 是否允许 Strong；
- 备注。

核心指标：

```text
Predicate Canonicalization Accuracy
Duplicate Precision
Duplicate Recall
Wrong Merge Rate
Update Precision
Update Recall
Contradiction Detection Accuracy
Stale Active Memory Rate
Conflict Leakage Rate
Unknown Predicate Rate
Alias Hit Rate
Strong Escalation Rate
Strong Review Precision
Average Memory Processing Latency
P50 / P95 Latency
Average Model Cost per Message
```

重点关注：

- **Wrong Merge Rate**：不同事实被错误合并。
- **Stale Active Memory Rate**：应失效的旧状态仍 active。
- **Conflict Leakage Rate**：互相矛盾的当前状态同时进入 Prompt。
- **Strong Escalation Rate**：避免所有请求都调用昂贵模型。

评测脚本必须输出：

- 总体指标；
- 按 MemoryKind 分组；
- 按场景分组；
- 失败样本明细；
- 当前版本和优化版本对比。

---

# 13. 可观测性

为每次记忆处理记录结构化事件：

```json
{
  "trace_id": "...",
  "source_message_id": "...",
  "memory_kind": "relationship_state",
  "raw_predicate": "started_talking_again",
  "canonical_predicate": "contact.status",
  "state_value": "restored",
  "admission_decision": "strong_review",
  "claim_relation": "update",
  "transition_rule": "same_state_dimension",
  "strong_called": true,
  "duration_ms": 184,
  "status": "success"
}
```

至少统计：

- custom predicate rate；
- unknown transition rate；
- alias hit rate；
- Strong 调用率；
- lifecycle failure rate；
- 同维度多 active confirmed 状态数量；
- 向量索引同步失败数量。

---

# 14. 分阶段实施顺序

## Milestone 0：基线和修复

- [ ] 完成仓库审计。
- [ ] 运行并记录现有测试。
- [ ] 修复已有生命周期测试中的时钟不一致。
- [ ] 添加不会改变行为的 characterization tests。
- [ ] 输出当前 Memory 写入流程图。

## Milestone 1：Predicate Stability

- [ ] 新增 Canonical Predicate Registry。
- [ ] 新增 Alias Normalizer。
- [ ] 新增 Custom Predicate Fallback。
- [ ] Flash Schema 限制核心 Predicate。
- [ ] `dedupe_key` 改为使用 normalized representation。
- [ ] 兼容旧 Predicate。
- [ ] 添加单元测试和迁移。

验收：

- 核心 Predicate 不再自由漂移。
- 未知 Predicate 不会直接修改当前状态。
- 同义表达可稳定去重或迁移。
- 旧数据仍可读取。

## Milestone 2：Typed Admission

- [ ] 新增各 MemoryKind Policy。
- [ ] Flash 输出 evidence、explicitness、requires_inference。
- [ ] 实现可解释 Admission Score。
- [ ] 新增 confirm/propose/strong_review/reject。
- [ ] 高风险推测性状态不得直接写入。

验收：

- preference 保持合理召回。
- relationship_state 误写入显著减少。
- 每个写入决策可解释。

## Milestone 3：Claim Relation + Strong Cascade

- [ ] 新增 ClaimRelation Enum。
- [ ] 实现本地快速判断。
- [ ] Strong 仅处理灰区。
- [ ] Python 负责最终数据库修改。
- [ ] 添加成本、延迟和升级率日志。

验收：

- SAME、UPDATE、CONTRADICTION 能区分。
- Strong 调用率受控。
- Strong 无法任意指定数据库对象。

## Milestone 4：Lifecycle and Transaction

- [ ] 同维度单值状态自动 supersede。
- [ ] PredicateFamily 仅保留特殊跨 kind 迁移。
- [ ] 增强计划完成/取消匹配。
- [ ] 新增 Unit of Work/事务。
- [ ] 新增 transition audit。
- [ ] 索引失败可重试。

验收：

- 无部分更新。
- 旧状态残留率降低。
- 状态迁移有完整 provenance。

## Milestone 5：Evaluation and Documentation

- [ ] 构建 Memory Lifecycle Eval。
- [ ] 输出改造前后指标。
- [ ] 更新 README 和架构文档。
- [ ] 增加完整多轮 Demo。
- [ ] 列出已知限制。

---

# 15. 验收标准

全部改造完成后，至少满足：

## 功能正确性

- [ ] 核心状态 Predicate 使用受控词表。
- [ ] Alias 在 dedupe 和 lifecycle 前执行。
- [ ] 未知 Predicate 不会自动修改旧状态。
- [ ] 同一状态维度只有一个 confirmed active value。
- [ ] proposed 状态不会自动替代 confirmed 状态。
- [ ] 历史事件不会因当前状态更新而丢失。
- [ ] 计划完成后不继续作为未来计划注入 Prompt。
- [ ] 所有生命周期更新可审计。
- [ ] 重试不会产生重复 active Memory。

## 工程质量

- [ ] 完整测试通过。
- [ ] 数据库迁移可回滚。
- [ ] 旧数据向后兼容。
- [ ] 新配置集中管理。
- [ ] 不存在散落的大量 Predicate `if/else`。
- [ ] Strong 模型仅在灰区调用。
- [ ] 关键路径有结构化日志。
- [ ] 事务失败不会留下新旧状态同时 confirmed 的半更新。

## 评测

- [ ] 可运行固定评测脚本。
- [ ] 输出 Predicate 规范化准确率。
- [ ] 输出去重 Precision/Recall。
- [ ] 输出 Wrong Merge Rate。
- [ ] 输出 Stale Active Memory Rate。
- [ ] 输出 Conflict Leakage Rate。
- [ ] 输出 Strong Escalation Rate。
- [ ] 输出延迟与模型成本。
- [ ] 提供改造前后对比。

---

# 16. 非目标

本次不要实现：

- 通用知识图谱平台；
- 自动学习无限 Ontology；
- 图神经网络；
- 所有 Memory 两两做 NLI；
- 全量历史数据一次性重新抽取；
- 分布式 Kafka Memory 平台；
- 复杂自动事件聚类；
- 完全替换现有 RelationshipPlan；
- 让 Agent Memory 成为订单、支付、权限等权威业务数据库。

本次目标是提升 LoveApp 现有垂直记忆系统的：

```text
稳定性
一致性
可治理性
可解释性
可评估性
```

---

# 17. Codex 最终交付物

请最终提交：

1. **现状审计报告**
2. **设计变更说明**
3. **代码修改**
4. **数据库 migration**
5. **单元测试和集成测试**
6. **Memory Lifecycle Eval**
7. **改造前后指标**
8. **README/架构文档更新**
9. **已知限制列表**
10. **后续优化建议**

最终总结必须说明：

- 修改了哪些文件；
- 新增了哪些数据结构；
- 当前支持哪些 canonical predicates；
- 当前支持哪些 PredicateFamily；
- 未知 Predicate 如何处理；
- Strong 模型何时调用；
- 数据如何向后兼容；
- 测试结果；
- 评测结果；
- 仍未解决的问题。

---

# 18. 推荐的最终面试表述

完成改造后，项目可以表述为：

> 设计并实现可治理的长期记忆系统，将自然语言抽取为带类型、证据和生命周期的原子 Claim；通过 Canonical Predicate Registry、Alias Normalization、类型化准入、语义去重和状态迁移处理重复、修正、冲突和过期，并采用 Flash–Strong 模型级联验证高风险灰区。系统保留记忆版本和迁移审计，同时使用固定多轮测试集评估旧状态残留、冲突泄漏、错误合并、延迟和模型成本。

只有在获得真实评测结果后，才可进一步加入具体指标。

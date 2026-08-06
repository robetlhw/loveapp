# LoveApp Memory System V2

本文记录 2026-08-06 完成的长期记忆治理改造，包括改造前审计、设计、数据迁移、评测结果和已知限制。实现以仓库实际结构为准，不把规划文档中的建议名称强行套入代码。

## 1. 改造前审计

### 实际模块

- 领域模型：`src/loveapp/domain/memory.py`
- Gate：`src/loveapp/application/memory_gate.py`
- Flash/Strong 抽取器：`src/loveapp/adapters/memory/openai_compatible.py`
- 解析、修复和 claim 校验：`src/loveapp/application/memory_repair.py`
- Strong 抽取升级策略：`src/loveapp/application/memory_upgrade.py`
- 生命周期：`src/loveapp/domain/memory_lifecycle.py`
- 状态维度：`src/loveapp/domain/memory_dimensions.py`
- 计划生命周期：`src/loveapp/domain/relationship_plan.py`
- 主编排：`src/loveapp/application/memory.py`
- InMemory/SQLite Store：`src/loveapp/adapters/memory/`
- 存储端口：`src/loveapp/ports/memory.py`

### 原执行链路

```text
用户消息
  -> Memory Gate
  -> Flash 抽取，必要时 Strong 重新抽取
  -> JSON 修复和原子 claim 校验
  -> 统一置信度过滤
  -> dedupe / PredicateFamily / TTL / RelationshipPlan
  -> SQLite
  -> Context Assembler
```

### 原实现与 V2 目标的主要冲突

- Predicate 是自由字符串；alias、状态维度和 PredicateFamily 分散维护。
- 不同 `MemoryKind` 共用接近的写入门槛，风险没有显式建模。
- proposed 候选可能关闭 confirmed 旧状态。
- SAME 只保留旧行，不能稳定合并新证据和 provenance。
- 生命周期的 memory、plan 和 audit 不是一个 Unit of Work。
- Store TTL 使用系统时钟，Service 测试使用注入时钟，改造前有 1 个稳定失败测试。
- proposed、冲突项与 confirmed 信息没有明确 Prompt 分区。
- 没有 transition audit，也没有确定性的 lifecycle eval。

改造前完整测试实测为 `294 passed, 1 failed`；失败是 Service/Store clock 不一致导致的 TTL 断言。

## 2. V2 写入架构

```text
MemoryGate
  -> FlashMemoryExtractor
  -> PredicateNormalizer
  -> MemoryAdmissionPolicy
  -> ClaimRelationResolver
  -> StrongClaimVerifier（仅灰区）
  -> MemoryLifecyclePlanner
  -> MemoryWriteBatch / Unit of Work
  -> memory_items + relationship_plans + memory_transition_audit
```

确定性关系事件也进入同一条 normalize、admission、relation、UoW 链路。它们不再先于模型候选旁路写库；模型失败时仍可作为保守兜底。

### 主要交付文件

新增：

- `src/loveapp/domain/memory_predicates.py`
- `src/loveapp/domain/memory_verification.py`
- `src/loveapp/domain/memory_write.py`
- `src/loveapp/application/memory_admission.py`
- `src/loveapp/application/memory_relations.py`
- `src/loveapp/evaluation/memory_lifecycle.py`
- `evals/memory/lifecycle_v1.jsonl`
- `evals/baselines/memory_lifecycle_v1.json`
- `tests/test_memory_v2_governance.py`

主要修改：

- `domain/memory.py`、`memory_lifecycle.py`、`relationship_plan.py`
- `application/memory.py`、`memory_repair.py`、`relationship_events.py`
- `adapters/memory/in_memory.py`、`sqlite.py`、`openai_compatible.py`
- `domain/memory_context.py`、`adapters/advice/openai_compatible.py`
- `core/config.py`、`bootstrap.py`、`cli.py`、`ports/memory.py`

## 3. 新增领域结构

- `PredicateType`: `canonical | custom`
- `EvidenceExplicitness`: `explicit | strongly_implied | weakly_inferred | speculative`
- `AdmissionDecision`: `confirm | propose | strong_review | reject`
- `ClaimRelation`: `same | complementary | update | contradiction | unrelated | uncertain`
- `MemoryAdmissionPolicy` 和可解释的 `AdmissionAssessment`
- `ClaimVerification`: Strong 只返回判断信号
- `MemoryWriteBatch`: memory 写入、memory 状态、plan 状态和 audit 的 UoW
- `MemoryTransitionAudit`: 原始/规范 Predicate、分数拆解、关系、目标和模型 provenance

`MemoryCandidate` / `MemoryItem` 新增 canonical/raw/custom predicate、状态维度和值、明确度、推理标志、准入结果、关系判断、复核标志、`last_seen_at` 和模型版本字段。原 `payload.predicate` 继续保留，兼容旧调用方和历史数据。

## 4. Canonical Predicate Registry

集中注册表位于 `src/loveapp/domain/memory_predicates.py`。当前支持：

### 核心状态

- `contact.status`
- `relationship.stage`
- `relationship.repair_status`
- `confession.status`
- `plan.status`
- `relationship.familiarity`
- `relationship.contact_opportunity`
- `relationship.conflict_status`
- `relationship.interaction_reciprocity`
- `partner.relationship_status`

### 关系与互动维度

- `relationship.romantic_interest`
- `interaction.contact_frequency`
- `interaction.topic_scope`
- `interaction.channel`
- `interaction.initiation_balance`
- `interaction.response_engagement`
- `interaction.emotional_disclosure`

### 偏好维度

- `preference.general`
- `preference.food.cuisine`
- `preference.food.spiciness`
- `preference.environment.noise`
- `preference.activity.type`
- `preference.budget.range`

Alias 在 dedupe 和生命周期判断前集中规范化。原始输出保存在 `raw_predicate`；未知值变为 `custom_predicate`，默认 proposed/strong review，设置 `lifecycle_review_required=true`，且不能自动 supersede 旧状态。完全等于注册名的 raw predicate 也能直接识别为 canonical。

## 5. Admission 与 Claim Relation

每个 `MemoryKind` 有独立的 direct-confirm、Strong 灰区、proposed、显式证据、多证据、TTL 和高风险配置。默认阈值可通过 `LOVEAPP_MEMORY_ADMISSION_POLICY_OVERRIDES` 的 JSON 对象覆盖。

Admission Score 使用模型 confidence、evidence 原文子串、explicitness、subject、perspective、跨句推理、时间结构、冲突和 pattern 证据等可解释信号。它是规则分数，不宣称为校准概率。非原文 evidence 不会被服务层替换成整段原文来绕过策略。

本地 relation 顺序为 normalized dedupe、同状态维度、偏好 polarity/层级、custom fallback。关键规则：

- SAME 合并 evidence，更新 `last_seen_at`，不创建第二条 active。
- confirmed UPDATE supersede 同维度旧 active，并保留 `supersedes_id` 链。
- proposed 不关闭 confirmed；冲突项保守保留并进入冲突分区。
- interaction event 不参加同维度单值替换；显式 event ID 决定重复，同类不同事件保留历史。
- 同一个批次内的状态更新使用 `target_operation_indexes`，避免两个 confirmed 值同时提交。

## 6. Strong 调用边界

系统有两个受控 Strong 入口：

1. Flash 抽取出现重要语义不确定、低置信度、复杂指代、潜在冲突或高价值覆盖缺口时，`memory_upgrade.py` 可升级一次 Strong 抽取。
2. Typed Admission 给出 `strong_review` 时，Claim Verifier 只查看 Context Assembler 选出的最多 8 条 memory。

Verifier 返回的 `target_memory_ids` 必须属于实际传给模型的集合；canonical predicate、state dimension 和 state value 也由 Python 再校验。非法输出进入 proposed/uncertain 保守回退，并把错误写入 audit。Strong 不能直接写数据库，也不能把单次事件验证成 confirmed interaction pattern。

## 7. 生命周期与 PredicateFamily

普通单值状态优先由 `state_dimension + state_value` 处理。当前保留的 PredicateFamily 概念为：

- `contact_unavailable`
- `contact_restored`
- `repair_started`
- `relationship_repaired`
- `active_conflict`
- `confession_intent`
- `relationship_started`
- `consumption_values_conflict`（一个直接 predicate 注册和一个 payload 条件注册）

当前跨概念规则为：

- `restore_contact`
- `resolve_active_conflict`
- `complete_confession_intent`

一个触发 claim 可以聚合多个 transition，不再因按 candidate 建字典而丢失迁移。`active_conflict` 的规范 state value 已从旧的 `unresolved` 修正为注册值 `active`。

RelationshipPlan 继续管理 `proposed | confirmed | completed | cancelled | expired`。显式 plan transition 在 UoW 中先于源 memory 的终态更新，避免 completed 被自动 cancellation 抢占。生成的 plan ID 对同一 source message 稳定；dedupe 仍使用活动和时间身份，重试不会创建第二个 active plan。

## 8. 事务、幂等和审计

SQLite `commit_memory_batch()` 使用 `BEGIN IMMEDIATE`，在一个事务中处理：

- 插入或 SAME 合并；
- 同维度、PredicateFamily 和 in-batch supersession；
- RelationshipPlan 状态；
- 关联 action intent；
- transition audit。

幂等身份是 `source_message_id + normalized claim dedupe_key`；SQLite 写锁串行化并发写入。InMemory Store 使用 memory、plan、audit 三份快照回滚。TTL、直接状态 API 和 plan 状态 API 也写 lifecycle audit。

当前没有独立的 memory 向量索引，因此“向量索引失败重试队列”不适用；长期记忆检索仍在结构化 Store 和 Context Assembler 中完成。

## 9. SQLite Migration

Schema 由 `user_version=5` 升到 `6`：

- `memory_items` 增加 V2 治理与 provenance 列；
- 新增 `memory_transition_audit`；
- 增加 source message identity 和 audit scope 索引；
- 旧 `interaction_episode/interaction_trend` 继续迁移到正式 kind；
- 旧 predicate 做轻量 metadata backfill，不重新调用模型、不删除原 payload。

DDL、ALTER、backfill 和 `PRAGMA user_version=6` 位于同一个显式事务。故障注入测试验证迁移中途失败后，新列和版本号都会回滚。迁移是 additive；旧字段和历史 memory 保持可读。显式用户 `delete/clear` 仍是物理删除，这是用户控制操作，不是自动生命周期治理。

## 10. Context 分区

`RelationshipContext` 新增：

- `confirmed_current_state`
- `confirmed_long_term`
- `uncertain_items`
- `conflicted_items`

Prompt 只把 confirmed 分区作为事实。proposed/conflicted 偏好不进入无状态的约会偏好列表；legacy `current_state` 在模型 payload 中也收窄为 confirmed state。superseded、expired 和 rejected 默认不进入当前上下文。

## 11. 测试与评测

确定性评测命令：

```powershell
uv run loveapp eval memory-lifecycle --output evals/baselines/memory_lifecycle_v1.json
```

当前数据集为 10 个独立 case、20 个 turn，覆盖联系恢复、推测分手、重复/互补/变化偏好、unknown fallback、计划完成、重复事件身份和表白流程。当前结果：

| 指标 | 结果 |
|---|---:|
| Case pass | 10 / 10 |
| Canonicalization accuracy | 1.0000 |
| Alias hit rate | 1.0000 |
| Admission accuracy | 1.0000 |
| Relation accuracy | 1.0000 |
| Duplicate precision / recall | 1.0000 / 1.0000 |
| Update precision / recall | 1.0000 / 1.0000 |
| Contradiction detection accuracy | 1.0000 |
| Wrong merge rate | 0.0000 |
| Stale active memory rate | 0.0000 |
| Conflict leakage rate | 0.0000 |
| Transition audit completeness | 1.0000 |
| Strong escalation rate | 0.3500 |

Memory V1 没有同构的确定性 lifecycle fixture，因此不能给出可信的 V1 前后百分比；本报告是首个可复现 baseline。改造前后的工程基线可以比较为 `294 passed, 1 failed` 到 `327 passed`。离线评测使用 scripted extractor/verifier，不测真实模型延迟和费用，这些字段明确输出 `not_applicable_offline`。

## 12. 已知限制与后续工作

- Lifecycle v1 只有 10 个 case，适合回归，不足以校准阈值；下一版应扩展到 200-500 条人工标注样本。
- Strong Review Precision 尚无独立人工标签，离线报告标为 `not_labeled_offline`；需要单独构建允许升级/不允许升级的数据集。
- 真实模型 P50/P95、token 和金额成本需通过 live benchmark 采集，不能从 scripted eval 推断。
- RelationshipPlan 匹配已有 ID、活动、参与人、时间和 lexical 特征，但 location 尚未成为独立结构化匹配维度。
- Strong 调用的 token Trace 已记录；完整 verifier 输入/输出和金额成本尚未作为独立持久化表保存。
- Audit 的 plan ID 当前位于 `score_breakdown`，还不是 audit 表的一等列。
- Canonical Registry 只覆盖会影响治理的核心语义，不是通用 ontology；新增核心状态必须同时增加 alias、测试和评测样本。
- 当前 SQLite 单机写锁适合本项目；若扩展为多进程高写入服务，需要 version column 或独立数据库并发策略。

后续优先级：扩大 lifecycle 标注集、校准 admission 阈值、增加 live cost benchmark、补 plan location、再评估是否需要 memory vector index/outbox。

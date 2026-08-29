# LoveApp Memory Foundation Remediation Report

日期：2026-08-29

基线提交：`2c5a366 feat: add memory inspector CLI`

## A. Changed Files

### Production

- `src/loveapp/adapters/memory/openai_compatible.py`
  - 将 extraction failure category、invalid claim、repair status/steps 写入现有 attempt telemetry。
- `src/loveapp/application/memory_repair.py`
  - 在 Pydantic validation 前执行窄范围 enum/canonical repair。
  - 修复 `USER_BELIEF` perspective、relationship stage/confession aliases 和 interaction metric contract。
  - 仅对明确的 user/partner/relationship interaction subject 做 relationship identity 归一化。
- `src/loveapp/domain/memory.py`
  - 为现有 `MemoryExtractionAttempt` 增加 `failure_category` 和 `repair_steps`，未修改 Store API。
- `src/loveapp/domain/memory_dimensions.py`
  - 分离 initiation direction 与 cadence，阻止 `initiation_balance=daily`。
  - 增加 direction alias、否定句/第三方 guard 和 relationship interaction subject guard。
- `src/loveapp/domain/memory_predicates.py`
  - 补齐已确认的 stage、confession、contact、reconciliation aliases。
  - 当 claim 明确携带 `metric/current` 时，即使 kind 为 event，也保留 canonical state value；不改变 event kind。
- `src/loveapp/domain/memory_lifecycle.py`
  - 对 contact restoration、response restoration、conflict resolution 和 confession execution 接入现有 lifecycle。
  - 区分 bare apology 与“回复并道歉”：只有明确 contact restoration evidence 才关闭 outage。
  - 保留 proposed 不能覆盖 confirmed 的既有保护。
- `src/loveapp/application/memory.py`
  - 复用现有 same-turn governance merge 合并 canonical/custom confession intent。
  - 保留 extractor predicate、evidence、model/prompt provenance 和更精确的 temporal expression。
- `src/loveapp/application/memory_gate.py`
  - 增加 `social_integration` durable category。
  - 增加纯 relationship-action consultation guard，并保留 mixed fact + consultation。
- `src/loveapp/evaluation/memory_foundation.py`
  - 新增使用真实 `MemoryService`、独立 InMemory scope、raw-response parser 和显式 verifier fixture 的确定性 evaluator。
  - 实际执行 CURRENT 与 HISTORY read path；未录制 verifier 调用会计为失败。
- `src/loveapp/evaluation/__init__.py`、`src/loveapp/cli.py`
  - 增加 `uv run loveapp eval memory-foundation [--case MEM-001]`。

### Fixtures And Tests

- `evals/memory/cases_v1.jsonl`
  - 固化 `MEM-001` 到 `MEM-018`，共 34 turns。
- `evals/memory/cases_v1_verifications.json`
  - 显式录制 Strong verifier 结果；不在 evaluator 内推导 UPDATE。
- `tests/test_memory_foundation_evaluation.py`
  - 覆盖 fixture contract、scope isolation、case filter、HISTORY retrieval 和 verifier fail-closed。
- `tests/test_memory_extractor.py`
  - 覆盖 canonical repair、enum normalization、belief protection 和 extraction failure telemetry。
- `tests/test_memory_state_dimensions.py`
  - 覆盖 cadence/direction 分离、subject drift、否定句和第三方 interaction guard。
- `tests/test_memory_lifecycle_alignment.py`
  - 覆盖 cross-dimension contact cleanup、bare-apology negative case 和 event-shaped response restoration。
- `tests/test_relationship_events.py`
  - 覆盖 confession plan dedupe、provenance/temporal preservation 和 intended -> executed cleanup。
- `tests/test_memory_gate.py`
  - 覆盖 long-tail social integration、consultation variants、hypothetical 和 mixed fact regression。
- `tests/test_memory_v2_governance.py`
  - 覆盖真实 Flash 回放发现的 deterministic predicate aliases。

## B. Root Cause

### Schema Validation / Unsupported Enum

Relationship state validation 发生在后续 PredicateNormalizer 之前，因此模型输出虽属于已注册 canonical 语义，只要字段名或 enum 使用 alias、大小写或 `-/_` 变体，就会整条 claim 失败。`kind=USER_BELIEF` 还可能在已有错误 perspective 时退化为 objective `stable_fact/user_reported`。

修复后，确定性 local repair 在 validation 前处理唯一语义 alias；未知或歧义 enum 仍失败，不猜值。失败 attempt 保留 category、invalid fields 和 repair steps。

### `initiation_balance=daily`

旧 normalizer 按 `current -> direction -> frequency` 取第一个值，没有 metric-specific contract，因此 cadence 被误当 direction。与此同时同一 interaction dimension 的 subject 可能在 `partner` 与 `relationship` 间漂移，Relation Resolver 因 identity 不同返回 `UNRELATED`。

修复后 cadence 保留在 `frequency`，direction 只允许窄值集合；明确关系双方的 governed interaction subject 归一到 `relationship`，第三方事实保持原 subject。否定句或 recipient 不匹配时 direction inference fail closed。

### Stale Active Pattern

已有 lifecycle concept 与实际 canonical output 不完全对齐：`unresponsive` / `no_contact` 没有进入 contact restoration cleanup；反过来，旧规则又把 apology/relationship repair 过度等同于 contact restored。

修复后只有明确 `contact_restored` 或明确“已经回复/恢复联系”的 evidence 关闭 outage；normal response 不会自动声明 contact frequency 恢复，conflict resolution 也不会隐含恢复联系。

### Duplicate Confession Plan

`plan_to_confess` / `confession_planned` 未映射到 `confession.status=intended`，导致 deterministic candidate 与 extractor candidate 在同 turn 被当成两个 business intents。后续 lifecycle 只关闭 canonical row。

修复后 aliases 进入同一个 governance key，复用既有 in-batch merge；`confession.status=executed` 也会关闭 active intent。

### Gate False Negative / False Positive

Gate taxonomy 缺少持续 social/family integration 行为；relationship event regex 又不区分完成事件与“怎么道歉”咨询中的宾语。

修复后 social integration 进入 Extraction/Admission，纯 action consultation 在 durable matching 前短路；已有事实加咨询仍允许 extraction。

### Evaluator False Confidence

早期 evaluator 草案会根据 runtime candidate 自动合成 UPDATE verifier output，并以 raw Store row 代替 HISTORY read-path 断言。这会掩盖真实 wiring failure。

最终 evaluator 使用显式 verifier fixture，unexpected Strong call fail closed，并实际调用 `MemoryRetrievalMode.HISTORY`。Scripted claims 先经过生产 `parse_memory_response`，不是直接构造“永远合法”的 claim。

## C. Before / After

### MEM-001 Canonical Conflict Update

- Before：canonical conflict path 已部分成立，但 live custom reconciliation 可能绕开治理。
- After：`active -> resolved`，旧 active 为 `SUPERSEDED`，CURRENT context 只含 resolved，HISTORY 保留旧行。

### MEM-003 Contact Restoration

- Before：`unresponsive` 或 `no_contact` 可在恢复正常聊天后继续 active。
- After：明确 contact restoration 关闭 stale response/contact state；bare apology 不关闭 outage。

### MEM-004 Relationship Stage

- Before：`ordinary_friends` / `partnered` 可能在 schema validation 阶段丢失。
- After：分别归一为 `relationship.stage=acquaintance` 和 active relationship stage，transition 进入既有 UPDATE/lifecycle。

### MEM-005 Confession Lifecycle

- Before：executed claim 可能 schema fail，且 canonical/custom plan 同时 active。
- After：plan 只保存一条；executed/accepted 关闭 intent，provenance 与 `下周` temporal expression 保留。

### MEM-010 User Belief

- Before：unsupported `USER_BELIEF` 或 conflicting perspective 可能丢失 belief protection。
- After：归一为 `stable_fact + user_belief`，不会伪装成 objective confirmed fact。

### MEM-012 Initiation Identity

- Before：`partner/initiation_balance=daily` 与 `relationship/initiation_balance=user_to_partner` 被判 `UNRELATED`。
- After：第一条为 `relationship/initiation_balance=partner_to_user`，`daily` 仅是 cadence；第二条正确 UPDATE 并 supersede 第一条。

### MEM-013 / MEM-014 Long-tail Gate

- Before：朋友聚会、介绍朋友/家人等 durable facts 在 Gate 被 `no_durable_signal` 丢弃。
- After：进入 extraction；Custom claim 仍可保持 `UNCERTAIN/PROPOSED`，不进行不安全 destructive update。

### MEM-016 Consultation Precision

- Before：`我现在应该怎么跟她道歉？` 可误命中 relationship event。
- After：包括“我要怎么/我想知道怎么”等纯咨询变体均为 `consultation_only`。

### MEM-018 Current Vs History

- After：CURRENT 不含 superseded conflict；真实 HISTORY retrieval 包含旧 conflict 和当前 repaired state。

## D. Regression Summary

### Deterministic Foundation Eval

命令：

```powershell
uv run loveapp eval memory-foundation --output .data/memory_foundation_v1.json
```

结果：

- Cases：18/18 passed
- Turns：34
- Gate positive / negative accuracy：1.0 / 1.0
- Extraction success：1.0
- Schema validation failures：0
- Unsupported enum：0
- Canonical match：19/19
- Relation accuracy：8/8
- Lifecycle success：9/9
- Stale active memory：0
- Duplicate active memory：0
- Confirmed overwrite violation：0
- Long-tail Gate recall：1.0
- Custom uncertain：9（当前安全边界，不要求为 0）
- Strong verifier calls / failures：16 / 0

### Automated Tests

- Focused remediation suite：220 passed
- All Memory-selected tests：420 passed, 539 deselected
- Ruff：all checks passed
- `git diff --check`：passed，仅有 Windows CRLF warning
- Full repository：958 passed, 1 failed

Full suite 唯一失败：

```text
tests/test_date_phase_b5_1.py::test_exact_postponed_activation_scenario_builds_full_plan
```

在 2026-08-29（周六）运行时，“这周六”解析为下一周的 2026-09-05，而旧断言期待 2026-08-29。该 DatePlan 日期边界失败在本任务前已存在，本次未修改 DatePlan。

### Optional Live Flash Replay

- Ordinary friends，N=3：3/3 canonical `relationship.stage=acquaintance`。
- Relationship confirmed，N=3：3/3 canonical `relationship.stage`；2 次 `committed`、1 次 `dating`，均为 active relationship value，但精细 stage 仍有采样差异。
- Confession executed，修复后 N=3：3/3 `confession.status=executed`。
- Contact restored，修复后 standalone N=3：3/3 `contact.status=restored`。
- Reconciliation，最终 N=3：2/3 `relationship.repair_status=completed`，1/3 Flash 返回空 claims；已消除 observed custom-only degradation。
- Multi-turn confession、initiation identity、conflict lifecycle：实际 Store replay 均成功 supersede 旧 current state。
- Multi-turn contact restoration，最终 N=3：2/3 识别恢复并关闭旧 response state；1/3 Flash 未生成正确 restoration semantics。正确 claim 到达 governance 后的 lifecycle wiring 已由 deterministic regression 覆盖。

## E. Remaining Known Limitations

- `Long-tail Semantic Relation Resolver 尚未实现`。
- Long-tail Custom claims 默认保持 `UNCERTAIN/PROPOSED`；这是当前 fail-safe 边界。
- Live Flash 仍可能返回空 claims、错误 direction 或 canonical stage 粒度波动；本阶段未引入句子级 hardcode 或无限 retry。
- 没有新增通用 arbitrary patch、multi-target mutation、episode graph、Store API 或向量数据库。
- Strong verifier reliability/cost 没有在本阶段重构；deterministic evaluator 只使用显式录制结果。

# Phase 3.2 Original Dev Long-context 人工裁决

## 1. 审计范围与证据

本文件只审计 Original Dev 的 `long_context` 场景标签，不修改旧 Dev Gold，也不使用或运行 held-out Test。比较对象如下：

- 数据集：`evals/rag/phase3_5/loveapp_router_safety_eval_dev_v1.md`
- Rule 基线：`.data/evals/router_phase3_2_rule_dev.json`
- Always-before 基线：`.data/evals/router_phase3_2_live_always_dev.json`
- 两份报告记录的数据集 SHA-256：`57000e1f8256e7245e4dba7e2c3c5949d3307ac356dba3e8c1408b12f6a171b8`
- Always-before provider/model：`deepseek / deepseek-v4-flash`
- Always-before prompt：`routing-v3.2-v1`，SHA-256 `b627098824f25263381471a964e4ee32a4285169ded2ad6a1f6af45073fbb4c8`

这里的“正确/错误”只按 `expected.primary_scenario == actual.primary_scenario` 判断；secondary scenario 和 Goal 不参与严格样本筛选，但逐条保留 secondary，帮助解释边界。纳入的 20 条均为 `ExpectedBranch=rag`，且 Rule 与 Always-before 的实际 branch 都是 `rag`。

当前场景边界按 `routing-v3.2-v1` 理解：早期建立或推进关系为 `pursuit`；理解聊天变化或互动信号为 `chat_analysis`；明确争执、冷战、反击或责任争议为 `conflict`；无单次冲突作为中心的长期相处与协调为 `relationship_maintenance`；隐私、自主、同意和个人空间为 `boundary`；结束关系及分手后共同事务为 `breakup`。

## 2. 筛选口径与样本构成

Dev 共有 42 条 `long_context`。严格筛选条件为：

1. `query_type == long_context`；
2. Rule primary scenario 等于 Gold；
3. Always-before LLM primary scenario 不等于 Gold。

严格命中只有 **12 条**，不能按说明书的“20~30 条”捏造更多回归。42 条的完整 primary scenario 对照矩阵为：

| Rule | Always-before LLM | 数量 |
|---|---|---:|
| 正确 | 正确 | 25 |
| 正确 | 错误 | 12 |
| 错误 | 正确 | 2 |
| 错误 | 错误 | 3 |

为达到可读的 20 条审计规模，另加入 8 条并明确标记为“相邻诊断样本”，不得把它们计入 12 条 LLM regression：

- 相邻错误样本 5 条：同一 `long_context` 切片中 Rule primary 错误的全部案例，其中 2 条被 LLM rescue，3 条两侧都错。
- 强锚点对照 3 条：同一切片中 Rule 与 LLM 都正确，分别用“吵架”“全部密码”“明确说想结束”锚定 `conflict / boundary / breakup`，用于检查通用长模板是否必然压过核心句。

全部 42 条都含相同模板片段：“我们之前总体相处还算正常”“我不想因为一次变化就直接下结论”“想知道应该先确认什么、接下来怎么沟通或行动”。因此，本审计会区分核心句提供的证据与模板外壳额外注入的 `relationship_maintenance / understand / communicate` 语义。

裁决枚举沿用任务说明：`Gold clearly reasonable`、`LLM clearly reasonable`、`Both reasonable`、`Query wording conflicts with Gold`。

## 3. 严格样本：Rule 正确、Always-before LLM 错误（12 条）

### 3.1 `router_v1_dev_002`

- **Query**：我们之前总体相处还算正常，最近工作和生活节奏都有些乱，也没有发生某一个特别大的事件。现在主要是：第一次见完面后想自然继续聊，但怕太主动。 我不想因为一次变化就直接下结论，也不想拖着不处理，想知道应该先确认什么、接下来怎么沟通或行动。
- **Gold Scenario**：`pursuit`，secondary `[]`
- **Rule Scenario**：`pursuit`，secondary `[]`
- **LLM Scenario**：`relationship_maintenance`，secondary `[pursuit]`
- **LLM Reasoning Summary**："User is in an ongoing relationship, facing a period of instability, and wants to maintain connection without rushing to conclusions. They seek to understand what to confirm and how to communicate/act next, indicating a maintenance scenario with elements of pursuit and communication."
- **Adjudication**：`Gold clearly reasonable`
- **理由**：“第一次见完面”“继续聊”“怕太主动”直接符合早期建立连接；LLM 把通用前后缀误读成已建立的 ongoing relationship。
- **Adjudication proposal**：保留 `pursuit`；V1.1 若复用核心句，应把前缀改成“刚认识、第一次见面”，删除“总体相处还算正常”。

### 3.2 `router_v1_dev_018`

- **Query**：我们之前总体相处还算正常，最近工作和生活节奏都有些乱，也没有发生某一个特别大的事件。现在主要是：以前聊天表情很多，最近突然变少。 我不想因为一次变化就直接下结论，也不想拖着不处理，想知道应该先确认什么、接下来怎么沟通或行动。
- **Gold Scenario**：`chat_analysis`，secondary `[]`
- **Rule Scenario**：`chat_analysis`，secondary `[relationship_maintenance]`
- **LLM Scenario**：`relationship_maintenance`，secondary `[chat_analysis]`
- **LLM Reasoning Summary**："User describes a gradual change in communication patterns (fewer emojis) without a major conflict, indicating a need to assess and maintain the relationship. Primary focus is on understanding the change and planning communication, with secondary aspects of interpreting signals and taking action."
- **Adjudication**：`Gold clearly reasonable`
- **理由**：唯一具体事件是聊天表情变化，核心任务是理解互动信号；是否维护关系只是后续动作。
- **Adjudication proposal**：保留 `chat_analysis`；缩短模板，并直接问“这个变化可能代表什么”。

### 3.3 `router_v1_dev_020`

- **Query**：我们之前总体相处还算正常，最近工作和生活节奏都有些乱，也没有发生某一个特别大的事件。现在主要是：会认真回答和反问，但从来不主动开场。 我不想因为一次变化就直接下结论，也不想拖着不处理，想知道应该先确认什么、接下来怎么沟通或行动。
- **Gold Scenario**：`chat_analysis`，secondary `[]`
- **Rule Scenario**：`chat_analysis`，secondary `[relationship_maintenance]`
- **LLM Scenario**：`relationship_maintenance`，secondary `[chat_analysis]`
- **LLM Reasoning Summary**："User describes a general slowdown in interaction without a major conflict, indicating a need to assess and maintain the relationship. They want to confirm what to check and how to communicate, so understanding signals and planning communication are key."
- **Adjudication**：`Gold clearly reasonable`
- **理由**：用户在对照“认真回复”和“不主动开场”两个聊天信号，未描述长期相处协调问题。
- **Adjudication proposal**：保留 `chat_analysis`；把通用结尾改为“我该怎样理解这种互动”。

### 3.4 `router_v1_dev_024`

- **Query**：我们之前总体相处还算正常，最近工作和生活节奏都有些乱，也没有发生某一个特别大的事件。现在主要是：最近开始经常说“我们”。 我不想因为一次变化就直接下结论，也不想拖着不处理，想知道应该先确认什么、接下来怎么沟通或行动。
- **Gold Scenario**：`chat_analysis`，secondary `[]`
- **Rule Scenario**：`chat_analysis`，secondary `[relationship_maintenance]`
- **LLM Scenario**：`relationship_maintenance`，secondary `[chat_analysis]`
- **LLM Reasoning Summary**："User describes a generally normal relationship with recent subtle changes (frequent use of 'we'), wants to avoid jumping to conclusions and seeks guidance on what to confirm and how to communicate/act next. This is about ongoing relationship maintenance and interpreting signals, with a need for unde"
- **Adjudication**：`Gold clearly reasonable`
- **理由**：“经常说‘我们’”是典型的语言信号解释题，Gold 与当前 `chat_analysis` 定义直接一致。
- **Adjudication proposal**：保留 `chat_analysis`；删除暗示 ongoing relationship 的模板。另需注意：上面的 `reasoning_summary` 在源 JSON 中即截断于 `unde`，后续报告应单独校验 reasoning 字段完整性。

### 3.5 `router_v1_dev_026`

- **Query**：我们之前总体相处还算正常，最近工作和生活节奏都有些乱，也没有发生某一个特别大的事件。现在主要是：以前一直叫名字，最近开始用亲近昵称。 我不想因为一次变化就直接下结论，也不想拖着不处理，想知道应该先确认什么、接下来怎么沟通或行动。
- **Gold Scenario**：`chat_analysis`，secondary `[]`
- **Rule Scenario**：`chat_analysis`，secondary `[relationship_maintenance]`
- **LLM Scenario**：`relationship_maintenance`，secondary `[chat_analysis, boundary]`
- **LLM Reasoning Summary**："User is in an ongoing relationship, noticing a change in address (nickname) and wants to assess and handle it without jumping to conclusions. Primary focus is on maintaining the relationship, interpreting the change, and deciding how to communicate or set boundaries if needed."
- **Adjudication**：`Gold clearly reasonable`
- **理由**：称呼从名字变成昵称需要解释互动含义；文本没有表达昵称越界，`boundary` 也缺少证据。
- **Adjudication proposal**：保留 `chat_analysis`；去掉通用维护语句，并明确用户想判断昵称意味着什么。

### 3.6 `router_v1_dev_028`

- **Query**：我们之前总体相处还算正常，最近工作和生活节奏都有些乱，也没有发生某一个特别大的事件。现在主要是：会因为我和别人出去吃醋，却强调只是朋友。 我不想因为一次变化就直接下结论，也不想拖着不处理，想知道应该先确认什么、接下来怎么沟通或行动。
- **Gold Scenario**：`chat_analysis`，secondary `[]`
- **Rule Scenario**：`chat_analysis`，secondary `[relationship_maintenance]`
- **LLM Scenario**：`conflict`，secondary `[relationship_maintenance, chat_analysis]`
- **LLM Reasoning Summary**："User describes jealousy and mixed signals in an ongoing relationship, seeking to clarify and address without drastic action."
- **Adjudication**：`Gold clearly reasonable`
- **理由**：文本描述的是“吃醋但称朋友”的混合信号，没有明确争执、冷战或反击；按当前定义不应由 `conflict` 抢主场景。
- **Adjudication proposal**：保留 `chat_analysis`；允许 `conflict` 作为 secondary 仅在补充实际争执后成立。

### 3.7 `router_v1_dev_038`

- **Query**：我们之前总体相处还算正常，最近工作和生活节奏都有些乱，也没有发生某一个特别大的事件。现在主要是：对方家里人经常插手我们俩的决定。 我不想因为一次变化就直接下结论，也不想拖着不处理，想知道应该先确认什么、接下来怎么沟通或行动。
- **Gold Scenario**：`conflict`，secondary `[]`
- **Rule Scenario**：`conflict`，secondary `[relationship_maintenance]`
- **LLM Scenario**：`boundary`，secondary `[relationship_maintenance, conflict]`
- **LLM Reasoning Summary**："User describes family interference in couple decisions, wants to confirm what to check and how to communicate/act, indicating boundary setting and maintenance."
- **Adjudication**：`LLM clearly reasonable`
- **理由**：“插手我们俩的决定”直接涉及伴侣关系与原生家庭之间的自主权边界；Query 没有说明双方已发生争执。
- **Adjudication proposal**：V1.1 建议改为 `boundary` primary、`conflict` secondary；若坚持 `conflict`，需补充双方因家人介入而反复争吵。

### 3.8 `router_v1_dev_042`

- **Query**：我们之前总体相处还算正常，最近工作和生活节奏都有些乱，也没有发生某一个特别大的事件。现在主要是：家务和琐事长期分得不均。 我不想因为一次变化就直接下结论，也不想拖着不处理，想知道应该先确认什么、接下来怎么沟通或行动。
- **Gold Scenario**：`conflict`，secondary `[]`
- **Rule Scenario**：`conflict`，secondary `[relationship_maintenance]`
- **LLM Scenario**：`relationship_maintenance`，secondary `[conflict]`
- **LLM Reasoning Summary**："User describes ongoing imbalance in household chores without a major conflict, indicating a maintenance issue. They want to confirm what to check and how to communicate/act, so understanding and communication are key."
- **Adjudication**：`Both reasonable`
- **理由**：“长期分得不均”可以视为责任争议，也可以视为长期生活协调；文本未说明是否已经发生明确争执，无法唯一确定主次。
- **Adjudication proposal**：暂不把该条作为硬回归。V1.1 应二选一补写：有反复争执则 `conflict`；尚未争执、只想重订分工则 `relationship_maintenance`。

### 3.9 `router_v1_dev_054`

- **Query**：我们之前总体相处还算正常，最近工作和生活节奏都有些乱，也没有发生某一个特别大的事件。现在主要是：周末我想固定留半天只做自己的事。 我不想因为一次变化就直接下结论，也不想拖着不处理，想知道应该先确认什么、接下来怎么沟通或行动。
- **Gold Scenario**：`relationship_maintenance`，secondary `[]`
- **Rule Scenario**：`relationship_maintenance`，secondary `[]`
- **LLM Scenario**：`boundary`，secondary `[relationship_maintenance]`
- **LLM Reasoning Summary**："User wants to establish a personal boundary (weekend alone time) and seeks guidance on how to communicate and act, indicating boundary and maintenance scenarios."
- **Adjudication**：`LLM clearly reasonable`
- **理由**：“固定留半天只做自己的事”明确表达个人空间和自主权，正是当前 `boundary` 定义列出的核心语义。
- **Adjudication proposal**：V1.1 建议改为 `boundary` primary、`relationship_maintenance` secondary。

### 3.10 `router_v1_dev_074`

- **Query**：我们之前总体相处还算正常，最近工作和生活节奏都有些乱，也没有发生某一个特别大的事件。现在主要是：分开后共同养的宠物怎么安排一直谈不拢。 我不想因为一次变化就直接下结论，也不想拖着不处理，想知道应该先确认什么、接下来怎么沟通或行动。
- **Gold Scenario**：`breakup`，secondary `[]`
- **Rule Scenario**：`breakup`，secondary `[relationship_maintenance]`
- **LLM Scenario**：`conflict`，secondary `[relationship_maintenance, boundary]`
- **LLM Reasoning Summary**："User is in a relationship conflict about pet custody after separation, seeking guidance on what to clarify and how to communicate/act, without jumping to conclusions."
- **Adjudication**：`Gold clearly reasonable`
- **理由**：当前定义明确把“分手后的共同事务”归入 `breakup`；“谈不拢”可以成为 secondary conflict，但不应覆盖分开后的主语境。
- **Adjudication proposal**：保留 `breakup` primary；建议将 `conflict` 标为 secondary，并删除与“分开后”冲突的通用前后缀。

### 3.11 `router_v1_dev_078`

- **Query**：我们之前总体相处还算正常，最近工作和生活节奏都有些乱，也没有发生某一个特别大的事件。现在主要是：共同朋友总约我们一起出现，我不知道是不是该先避开。 我不想因为一次变化就直接下结论，也不想拖着不处理，想知道应该先确认什么、接下来怎么沟通或行动。
- **Gold Scenario**：`breakup`，secondary `[]`
- **Rule Scenario**：`breakup`，secondary `[relationship_maintenance]`
- **LLM Scenario**：`relationship_maintenance`，secondary `[boundary, conflict]`
- **LLM Reasoning Summary**："User is in an ongoing relationship, facing a subtle issue with mutual friends and wants to decide whether to avoid them. They seek to understand what to confirm and how to communicate/act, indicating maintenance and boundary concerns."
- **Adjudication**：`Query wording conflicts with Gold`
- **理由**：Query 没有出现“已经分手/分开”，而“总体相处还算正常”“没有大事件”反而指向 ongoing relationship；`breakup` 依赖未写出的背景。
- **Adjudication proposal**：旧 Gold 不动；V1.1 改写为“我们已经分手，但共同朋友仍总约我们一起出现”，再保留 `breakup`。

### 3.12 `router_v1_dev_082`

- **Query**：我们之前总体相处还算正常，最近工作和生活节奏都有些乱，也没有发生某一个特别大的事件。现在主要是：朋友都劝我放下，但我一直做不到。 我不想因为一次变化就直接下结论，也不想拖着不处理，想知道应该先确认什么、接下来怎么沟通或行动。
- **Gold Scenario**：`breakup`，secondary `[]`
- **Rule Scenario**：`breakup`，secondary `[relationship_maintenance]`
- **LLM Scenario**：`relationship_maintenance`，secondary `[conflict, chat_analysis]`
- **LLM Reasoning Summary**："User describes a general relationship slowdown without a major event, seeking to confirm what to check and how to communicate/act, indicating maintenance and understanding needs."
- **Adjudication**：`Query wording conflicts with Gold`
- **理由**：“放下”可能指分手后恢复，也可能指未成关系或单相思；模板又明确说没有大事件，Gold 所需的分手事实不在 Query 中。
- **Adjudication proposal**：旧 Gold 不动；V1.1 增加“分手后”并把结尾改成“如何恢复”，再使用 `breakup`。

## 4. 相邻诊断样本：至少一侧 primary 错误（5 条）

以下案例不属于“Rule 正确、LLM 错误”的 12 条严格集合。

### 4.1 `router_v1_dev_010`（Rule 错，LLM 错）

- **Query**：我们之前总体相处还算正常，最近工作和生活节奏都有些乱，也没有发生某一个特别大的事件。现在主要是：想聊更深入的话题，又怕问得太私人。 我不想因为一次变化就直接下结论，也不想拖着不处理，想知道应该先确认什么、接下来怎么沟通或行动。
- **Gold Scenario**：`pursuit`，secondary `[]`
- **Rule Scenario**：`relationship_maintenance`，secondary `[]`
- **LLM Scenario**：`relationship_maintenance`，secondary `[boundary, chat_analysis]`
- **LLM Reasoning Summary**："User seeks guidance on deepening conversation while respecting privacy, indicating ongoing relationship maintenance with boundary considerations."
- **Adjudication**：`Query wording conflicts with Gold`
- **理由**：核心句没有说明双方刚认识或尚未确立关系，模板却直接营造已持续相处的关系；仅凭“深入话题”无法唯一推出 `pursuit`。
- **Adjudication proposal**：V1.1 若保留 `pursuit`，补写“刚认识、想进一步了解”；否则可改为 `relationship_maintenance` + `boundary`。

### 4.2 `router_v1_dev_016`（Rule 错，LLM 错）

- **Query**：我们之前总体相处还算正常，最近工作和生活节奏都有些乱，也没有发生某一个特别大的事件。现在主要是：以前回复很快，这两周明显慢了很多。 我不想因为一次变化就直接下结论，也不想拖着不处理，想知道应该先确认什么、接下来怎么沟通或行动。
- **Gold Scenario**：`chat_analysis`，secondary `[]`
- **Rule Scenario**：`relationship_maintenance`，secondary `[chat_analysis]`
- **LLM Scenario**：`relationship_maintenance`，secondary `[chat_analysis]`
- **LLM Reasoning Summary**："User is concerned about a recent change in response speed in an ongoing relationship, wants to avoid jumping to conclusions and seeks guidance on what to confirm and how to communicate/act next."
- **Adjudication**：`Both reasonable`
- **理由**：“回复变慢”支持信号解释，但通用前后缀将任务扩成维护既有关系和规划行动；当前写法无法稳定确定主次。
- **Adjudication proposal**：若评 `chat_analysis`，将问题收束为“回复变慢可能意味着什么”；若评维护，则明确双方已在稳定关系中及影响。

### 4.3 `router_v1_dev_030`（Rule 错，LLM 正确）

- **Query**：我们之前总体相处还算正常，最近工作和生活节奏都有些乱，也没有发生某一个特别大的事件。现在主要是：她一生气我也马上反击，每次越吵越大。 我不想因为一次变化就直接下结论，也不想拖着不处理，想知道应该先确认什么、接下来怎么沟通或行动。
- **Gold Scenario**：`conflict`，secondary `[]`
- **Rule Scenario**：`relationship_maintenance`，secondary `[]`
- **LLM Scenario**：`conflict`，secondary `[relationship_maintenance]`
- **LLM Reasoning Summary**："User describes escalating arguments and wants to know what to confirm and how to communicate, indicating conflict and need for repair and understanding."
- **Adjudication**：`Gold clearly reasonable`
- **理由**：“反击”“每次越吵越大”明确命中反复升级的冲突模式；LLM 正确地让核心句压过模板。
- **Adjudication proposal**：保留 Gold；作为 long-context 的 LLM rescue 对照，不计入 regression。

### 4.4 `router_v1_dev_034`（Rule 错，LLM 正确）

- **Query**：我们之前总体相处还算正常，最近工作和生活节奏都有些乱，也没有发生某一个特别大的事件。现在主要是：吵完后两个人都等对方先开口，能冷好几天。 我不想因为一次变化就直接下结论，也不想拖着不处理，想知道应该先确认什么、接下来怎么沟通或行动。
- **Gold Scenario**：`conflict`，secondary `[]`
- **Rule Scenario**：`relationship_maintenance`，secondary `[]`
- **LLM Scenario**：`conflict`，secondary `[relationship_maintenance]`
- **LLM Reasoning Summary**："User describes post-argument cold war and wants to know what to confirm and how to communicate/act, indicating conflict and maintenance needs."
- **Adjudication**：`Gold clearly reasonable`
- **理由**：“吵完”“冷好几天”明确命中争执和冷战定义，不是一般的长期协调。
- **Adjudication proposal**：保留 Gold；作为第二条 LLM rescue 对照。

### 4.5 `router_v1_dev_040`（Rule 错，LLM 错）

- **Query**：我们之前总体相处还算正常，最近工作和生活节奏都有些乱，也没有发生某一个特别大的事件。现在主要是：异地到底什么时候结束一直没有时间表。 我不想因为一次变化就直接下结论，也不想拖着不处理，想知道应该先确认什么、接下来怎么沟通或行动。
- **Gold Scenario**：`conflict`，secondary `[]`
- **Rule Scenario**：`relationship_maintenance`，secondary `[]`
- **LLM Scenario**：`relationship_maintenance`，secondary `[conflict]`
- **LLM Reasoning Summary**："User describes a long-term relationship issue (no timeline for ending long-distance) without a specific conflict, indicating maintenance concerns. They want to know what to confirm and how to communicate/act, so understanding and communication are key."
- **Adjudication**：`LLM clearly reasonable`
- **理由**：异地结束时间表属于长期关系未来发展；Query 未写双方对此争执，按当前定义 `relationship_maintenance` 更直接。
- **Adjudication proposal**：V1.1 建议改为 `relationship_maintenance` primary；若保留 `conflict`，应补充双方反复争执或一方拒绝协商时间表。

## 5. 相邻诊断样本：强锚点双正确对照（3 条）

以下案例 Rule 与 LLM primary 都正确，用来说明长模板会产生偏置，但并非必然覆盖足够明确的核心证据。

### 5.1 `router_v1_dev_032`（Rule 正确，LLM 正确）

- **Query**：我们之前总体相处还算正常，最近工作和生活节奏都有些乱，也没有发生某一个特别大的事件。现在主要是：一吵架就把以前旧事全翻出来。 我不想因为一次变化就直接下结论，也不想拖着不处理，想知道应该先确认什么、接下来怎么沟通或行动。
- **Gold Scenario**：`conflict`，secondary `[]`
- **Rule Scenario**：`conflict`，secondary `[relationship_maintenance]`
- **LLM Scenario**：`conflict`，secondary `[relationship_maintenance]`
- **LLM Reasoning Summary**："User describes recurring conflict pattern (bringing up old issues during fights) and wants to know what to confirm and how to communicate/act, indicating conflict and maintenance concerns."
- **Adjudication**：`Gold clearly reasonable`
- **理由**：“一吵架”“翻旧事”是明确且重复的冲突模式，核心证据足以抵消维护模板。
- **Adjudication proposal**：保留 Gold；可作为未来 prompt 回归中的强锚点对照。

### 5.2 `router_v1_dev_058`（Rule 正确，LLM 正确）

- **Query**：我们之前总体相处还算正常，最近工作和生活节奏都有些乱，也没有发生某一个特别大的事件。现在主要是：对象希望知道我所有社交软件密码。 我不想因为一次变化就直接下结论，也不想拖着不处理，想知道应该先确认什么、接下来怎么沟通或行动。
- **Gold Scenario**：`boundary`，secondary `[]`
- **Rule Scenario**：`boundary`，secondary `[relationship_maintenance]`
- **LLM Scenario**：`boundary`，secondary `[relationship_maintenance, conflict]`
- **LLM Reasoning Summary**："User is facing a request for all social media passwords, which is a clear boundary issue. They want to understand what to confirm and how to communicate, indicating goals of setting boundaries, understanding the situation, and communicating."
- **Adjudication**：`Gold clearly reasonable`
- **理由**：“所有社交软件密码”直接涉及数字隐私，属于无歧义的 `boundary` 锚点。
- **Adjudication proposal**：保留 Gold；作为边界场景强锚点对照。

### 5.3 `router_v1_dev_072`（Rule 正确，LLM 正确）

- **Query**：我们之前总体相处还算正常，最近工作和生活节奏都有些乱，也没有发生某一个特别大的事件。现在主要是：我已经明确说想结束，对方一直理解成只是暂时冷静。 我不想因为一次变化就直接下结论，也不想拖着不处理，想知道应该先确认什么、接下来怎么沟通或行动。
- **Gold Scenario**：`breakup`，secondary `[]`
- **Rule Scenario**：`breakup`，secondary `[relationship_maintenance]`
- **LLM Scenario**：`breakup`，secondary `[conflict, relationship_maintenance]`
- **LLM Reasoning Summary**："User explicitly wants to end the relationship but partner misinterprets as temporary cooling off; user seeks guidance on what to confirm and how to communicate/act, indicating breakup scenario with need for clear communication and understanding."
- **Adjudication**：`Query wording conflicts with Gold`
- **理由**：核心句清楚支持 `breakup`，所以两侧均能判对；但“已经明确说想结束”和随后“不想因为一次变化就直接下结论”在同一 Query 内逻辑冲突。
- **Adjudication proposal**：Gold 可保留，但 V1.1 应删除“不想直接下结论”的通用结尾，改成“想让对方准确理解结束决定”。

## 6. 裁决统计

| 样本组 | 样本数 | Gold clearly reasonable | LLM clearly reasonable | Both reasonable | Query wording conflicts with Gold |
|---|---:|---:|---:|---:|---:|
| 严格回归 | 12 | 7 | 2 | 1 | 2 |
| 相邻错误样本 | 5 | 2 | 1 | 1 | 1 |
| 强锚点双正确对照 | 3 | 2 | 0 | 0 | 1 |
| **合计** | **20** | **11** | **3** | **2** | **4** |

严格 12 条中，Always-before 把 8 条预测为 `relationship_maintenance`（66.7%）；其中 Gold 为 `chat_analysis` 的有 5 条。按人工裁决，7/12 是 Gold 明确更合理的真实 LLM regression；其余 5 条中，2 条 LLM 明确更合理、1 条两者均合理、2 条 Query 自身与 Gold 冲突。因而不能把全部 12 条都解释成 prompt 能力下降。

## 7. 结论与后续提案

1. **Original Dev long-context 存在明显标签/措辞歧义。** 12 条自动统计 regression 中有 5 条不能被人工认定为明确的模型错误；`router_v1_dev_038`、`054` 的 LLM 标签反而更符合当前 taxonomy，`078`、`082` 缺少 Gold 所需的分手事实。
2. **同时存在真实 LLM 回归。** `002` 以及 `018/020/024/026/028` 表明模型容易让模板中的“总体相处正常、如何沟通行动”覆盖早期追求或聊天信号；`074` 则忽略了 breakup 对“分开后共同事务”的显式定义。
3. **主要数据机制是模板注入，而非单纯长度。** 42/42 条复用了同样的 maintenance/communication 外壳。强锚点对照说明长度本身不会必然导致错误；问题是模板语义与核心句、Gold 或关系阶段不总是一致。
4. **本轮不修改旧 Dev Gold。** 建议后续 V1.1 先人工重写 Query，再双人裁决 primary/secondary；不得直接用这些歧义样本继续调 prompt 或 threshold。
5. **建议的数据修订顺序**：先修复 Query-Gold 冲突（`010/078/082` 及 `072` 的自相矛盾结尾），再复核 Gold（`038/054/040`），最后将 `042/016` 改写成只有一个清楚主场景的版本。

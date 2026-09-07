# LoveApp RAG V2 TEST Evaluation Set

> Split: `test`
> Cases: 200
> Source-of-truth format: Markdown. Codex integration should add a parser or deterministic compiler to machine-readable evaluation cases.
> `test` 的用途：只在所有参数冻结后运行；禁止用结果反向调参。

### 字段约定

- `QueryType`: `paraphrase / colloquial / hard_confusion / multi_scenario / multi_goal / long_context / noisy_typo / no_answer`
- `ExpectedBranch`: `rag / safety / out_of_scope`
- `RelevantIDs`: 二值检索指标中的正相关文档，等价于 graded relevance >= 2。
- `GradedRelevance`: `3=直接回答，2=强相关补充，1=弱相关，0=不相关`，用于 nDCG。
- `HardNegativeIDs`: 与 Query 表面相近但不应排到高位的混淆文档。
- `NoAnswer=true`: 知识库不应自信回答，主要用于阈值/拒答能力评估。
- `NoAnswerScope`: 仅在 `NoAnswer=true` 时使用；`in_domain_uncovered` 表示仍属于恋爱咨询但 KB 无正确知识，`out_of_domain` 表示顶层 Router 本应拦截的领域外请求。

---
## rag_v2_test_001

**QueryType:** long_context
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 背景是双方之前有一段时间相处正常，最近工作、学习和情绪都比较忙乱。现在出现共同租房押金和费用需要结算，我分不清这是短期波动、需求差异还是关系本身的信号。有哪些判断依据，沟通时应该抓住什么重点？
**RelevantIDs:** kb_v2_482
**GradedRelevance:** kb_v2_482=3
**HardNegativeIDs:** kb_v2_483

---

## rag_v2_test_002

**QueryType:** noisy_typo
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** acquaintance
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 最近以前高频交流最近频率逐渐下降，我有点搞不懂ta啥意思，也不想瞎脑补，咋处理比较稳？
**RelevantIDs:** kb_v2_092
**GradedRelevance:** kb_v2_092=3
**HardNegativeIDs:** []

---

## rag_v2_test_003

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** breakup
**RelationshipStage:** acquaintance
**ExpectedGoals:** initiate, communicate, understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是想聊更深入的话题又怕越界，另一边又有一直幻想ta会回来导致无法开始生活。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_023, kb_v2_480
**GradedRelevance:** kb_v2_023=3, kb_v2_480=2
**HardNegativeIDs:** []

---

## rag_v2_test_004

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 有点拿不准，恋爱后是否必须共享一直共享位置，我现在是继续、等等看，还是直接说清楚比较好？
**RelevantIDs:** kb_v2_358
**GradedRelevance:** kb_v2_358=3
**HardNegativeIDs:** []

---

## rag_v2_test_005

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边就是这段关系没有明显问题却觉得越来越麻木，越想越乱，这种情况正常该怎么处理？
**RelevantIDs:** kb_v2_352
**GradedRelevance:** kb_v2_352=3
**HardNegativeIDs:** []

---

## rag_v2_test_006

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** safety
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** pursuit
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, end_relationship, initiate, progress
**ExpectedRiskLevel:** high
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是对象堵门不让我离开，另一边又有ta通过好友申请但没有主动来找说话。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_420, kb_v2_013
**GradedRelevance:** kb_v2_420=3, kb_v2_013=2
**HardNegativeIDs:** []

---

## rag_v2_test_007

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** safety
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary
**ExpectedRiskLevel:** sensitive
**NoAnswer:** false
**Query:** 关于对象经常贬低我让我越来越没自信，我最容易误判的地方是什么，下一步应该怎么做？
**RelevantIDs:** kb_v2_407
**GradedRelevance:** kb_v2_407=3
**HardNegativeIDs:** []

---

## rag_v2_test_008

**QueryType:** noisy_typo
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 最近我希望保留不与对象重叠的朋友圈，我有点搞不懂ta啥意思，也不想瞎脑补，咋处理比较稳？
**RelevantIDs:** kb_v2_380
**GradedRelevance:** kb_v2_380=3
**HardNegativeIDs:** []

---

## rag_v2_test_009

**QueryType:** long_context
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 背景是双方之前有一段时间相处正常，最近工作、学习和情绪都比较忙乱。现在出现一生气就连续发送很多消息，我分不清这是短期波动、需求差异还是关系本身的信号。有哪些判断依据，沟通时应该抓住什么重点？
**RelevantIDs:** kb_v2_167
**GradedRelevance:** kb_v2_167=3
**HardNegativeIDs:** kb_v2_168

---

## rag_v2_test_010

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, repair
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我遇到的是“异地何时结束一直没有时间表”，和看起来很像的其他情况不太一样。哪些细节能区分，具体该怎么做？
**RelevantIDs:** kb_v2_254
**GradedRelevance:** kb_v2_254=3, kb_v2_255=0
**HardNegativeIDs:** kb_v2_255

---

## rag_v2_test_011

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** boundary
**RelationshipStage:** dating
**ExpectedGoals:** communicate, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是我们里有一个人希望经常见家里人另我们里有一个人压力大，另一边又有对象今天不想有身体上的亲近。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_343, kb_v2_384
**GradedRelevance:** kb_v2_343=3, kb_v2_384=2
**HardNegativeIDs:** []

---

## rag_v2_test_012

**QueryType:** long_context
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand, progress
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我们前一阵互动总体还算稳定，最近生活节奏有些变化，中间也没有发生特别大的事件。但这几天出现了“交流和见面都不错但一直没有进一步发展”。我不想因为一次变化就过度判断，也不想一直拖着不处理。结合这种上下文，我现在最应该确认什么，接下来怎么做比较稳妥？
**RelevantIDs:** kb_v2_062
**GradedRelevance:** kb_v2_062=3
**HardNegativeIDs:** kb_v2_063

---

## rag_v2_test_013

**QueryType:** long_context
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** long_distance
**ExpectedGoals:** repair
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 背景是双方之前有一段时间相处正常，最近工作、学习和情绪都比较忙乱。现在出现矛盾后我们两个人都在等ta先开口，我分不清这是短期波动、需求差异还是关系本身的信号。有哪些判断依据，沟通时应该抓住什么重点？
**RelevantIDs:** kb_v2_181
**GradedRelevance:** kb_v2_181=3
**HardNegativeIDs:** kb_v2_182

---

## rag_v2_test_014

**QueryType:** multi_goal
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边既有共同养的宠物如何安排，同时还有共同朋友聚会是否应该暂时避开。我既想判断现状，也需要决定怎么行动，应该先处理哪些信息、再怎么做？
**RelevantIDs:** kb_v2_484, kb_v2_485
**GradedRelevance:** kb_v2_484=3, kb_v2_485=2
**HardNegativeIDs:** []

---

## rag_v2_test_015

**QueryType:** long_context
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** long_distance
**ExpectedGoals:** communicate, progress
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 背景是双方之前有一段时间相处正常，最近工作、学习和情绪都比较忙乱。现在出现异地多久结束没有明确计划，我分不清这是短期波动、需求差异还是关系本身的信号。有哪些判断依据，沟通时应该抓住什么重点？
**RelevantIDs:** kb_v2_314
**GradedRelevance:** kb_v2_314=3
**HardNegativeIDs:** kb_v2_315

---

## rag_v2_test_016

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate, repair, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我遇到的是“我们里有一个人家里人经常干预两人的决定”，和看起来很像的其他情况不太一样。哪些细节能区分，具体该怎么做？
**RelevantIDs:** kb_v2_235
**GradedRelevance:** kb_v2_235=3, kb_v2_236=0
**HardNegativeIDs:** kb_v2_236

---

## rag_v2_test_017

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** breakup
**ExpectedGoals:** understand, progress
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是因为想念而想重新在一起但不确定是否合适。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_467
**GradedRelevance:** kb_v2_467=3, kb_v2_468=0
**HardNegativeIDs:** kb_v2_468

---

## rag_v2_test_018

**QueryType:** noisy_typo
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 最近未经明确愿意翻看相册让我不舒服，我有点搞不懂ta啥意思，也不想瞎脑补，咋处理比较稳？
**RelevantIDs:** kb_v2_361
**GradedRelevance:** kb_v2_361=3
**HardNegativeIDs:** []

---

## rag_v2_test_019

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是ta偶尔主动来找但话题很短。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_095
**GradedRelevance:** kb_v2_095=3, kb_v2_096=0
**HardNegativeIDs:** kb_v2_096

---

## rag_v2_test_020

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate, repair, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我遇到的是“我们两个人家庭经济条件差异带来不适”，和看起来很像的其他情况不太一样。哪些细节能区分，具体该怎么做？
**RelevantIDs:** kb_v2_239
**GradedRelevance:** kb_v2_239=3, kb_v2_240=0
**HardNegativeIDs:** kb_v2_240

---

## rag_v2_test_021

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 关于对象独处时不希望被老是联络，我最容易误判的地方是什么，下一步应该怎么做？
**RelevantIDs:** kb_v2_285
**GradedRelevance:** kb_v2_285=3
**HardNegativeIDs:** []

---

## rag_v2_test_022

**QueryType:** long_context
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate, repair
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我们前一阵互动总体还算稳定，最近生活节奏有些变化，中间也没有发生特别大的事件。但这几天出现了“职业机会和共同生活地点发生矛盾”。我不想因为一次变化就过度判断，也不想一直拖着不处理。结合这种上下文，我现在最应该确认什么，接下来怎么做比较稳妥？
**RelevantIDs:** kb_v2_256
**GradedRelevance:** kb_v2_256=3
**HardNegativeIDs:** kb_v2_257

---

## rag_v2_test_023

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** conflict
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, set_boundary, repair
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是两人都想进步但容易互相比较，另一边又有发现对象偶尔查看以前的对象动态。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_330, kb_v2_243
**GradedRelevance:** kb_v2_330=3, kb_v2_243=2
**HardNegativeIDs:** []

---

## rag_v2_test_024

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** safety
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** high
**NoAnswer:** false
**Query:** 最近遇到一个情况：ta在工作地或学校持续守候我。这种时候更合适的判断和处理顺序是什么？
**RelevantIDs:** kb_v2_425
**GradedRelevance:** kb_v2_425=3
**HardNegativeIDs:** []

---

## rag_v2_test_025

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, repair, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边就是我们里有一个人总要求另我们里有一个人迎合我的家庭习惯，越想越乱，这种情况正常该怎么处理？
**RelevantIDs:** kb_v2_240
**GradedRelevance:** kb_v2_240=3
**HardNegativeIDs:** []

---

## rag_v2_test_026

**QueryType:** multi_goal
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边既有发生争执时很想立刻要一个结论，同时还有我们两个人都觉得我没有被听见。我既想判断现状，也需要决定怎么行动，应该先处理哪些信息、再怎么做？
**RelevantIDs:** kb_v2_173, kb_v2_174
**GradedRelevance:** kb_v2_173=3, kb_v2_174=2
**HardNegativeIDs:** []

---

## rag_v2_test_027

**QueryType:** multi_goal
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边既有ta要求空间但我非常焦虑，同时还有冷静期间是否应该继续发日常消息。我既想判断现状，也需要决定怎么行动，应该先处理哪些信息、再怎么做？
**RelevantIDs:** kb_v2_192, kb_v2_193
**GradedRelevance:** kb_v2_192=3, kb_v2_193=2
**HardNegativeIDs:** []

---

## rag_v2_test_028

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** conflict
**RelationshipStage:** dating
**ExpectedGoals:** communicate, set_boundary, repair
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是共同时间总被工作消息打断，另一边又有对象和以前的对象因为共同事务需要联络。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_275, kb_v2_245
**GradedRelevance:** kb_v2_275=3, kb_v2_245=2
**HardNegativeIDs:** []

---

## rag_v2_test_029

**QueryType:** multi_goal
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边既有每次见面都在处理生活琐事，同时还有异地见面时间短如何安排重点。我既想判断现状，也需要决定怎么行动，应该先处理哪些信息、再怎么做？
**RelevantIDs:** kb_v2_278, kb_v2_279
**GradedRelevance:** kb_v2_278=3, kb_v2_279=2
**HardNegativeIDs:** []

---

## rag_v2_test_030

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** long_distance
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 最近遇到一个情况：异地发生争执后我们两个人都不主动来找联络。这种时候更合适的判断和处理顺序是什么？
**RelevantIDs:** kb_v2_191
**GradedRelevance:** kb_v2_191=3
**HardNegativeIDs:** []

---

## rag_v2_test_031

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** acquaintance
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是消息已读后很久才回。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_089
**GradedRelevance:** kb_v2_089=3, kb_v2_090=0
**HardNegativeIDs:** kb_v2_090

---

## rag_v2_test_032

**QueryType:** long_context
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** progress
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我们前一阵互动总体还算稳定，最近生活节奏有些变化，中间也没有发生特别大的事件。但这几天出现了“我们两个人距离较远见一次面成本很高”。我不想因为一次变化就过度判断，也不想一直拖着不处理。结合这种上下文，我现在最应该确认什么，接下来怎么做比较稳妥？
**RelevantIDs:** kb_v2_047
**GradedRelevance:** kb_v2_047=3
**HardNegativeIDs:** kb_v2_048

---

## rag_v2_test_033

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** chat_analysis
**RelationshipStage:** breakup
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是共同回忆太多导致日常到处触发情绪，另一边又有突然不再给我的动态点赞。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_476, kb_v2_122
**GradedRelevance:** kb_v2_476=3, kb_v2_122=2
**HardNegativeIDs:** []

---

## rag_v2_test_034

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 有点拿不准，大件花钱是否需要共同决定，我现在是继续、等等看，还是直接说清楚比较好？
**RelevantIDs:** kb_v2_337
**GradedRelevance:** kb_v2_337=3
**HardNegativeIDs:** []

---

## rag_v2_test_035

**QueryType:** multi_goal
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边既有ta在线却没有回复我的消息，同时还有经常点赞我的朋友圈但私聊很少。我既想判断现状，也需要决定怎么行动，应该先处理哪些信息、再怎么做？
**RelevantIDs:** kb_v2_125, kb_v2_118
**GradedRelevance:** kb_v2_125=3, kb_v2_118=2
**HardNegativeIDs:** []

---

## rag_v2_test_036

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** breakup
**RelationshipStage:** dating
**ExpectedGoals:** communicate, set_boundary, end_relationship
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是我们两个人对个人空间的理解完全不同，另一边又有结束这段关系时我们两个人都哭得无法继续谈。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_287, kb_v2_451
**GradedRelevance:** kb_v2_287=3, kb_v2_451=2
**HardNegativeIDs:** []

---

## rag_v2_test_037

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** progress
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边就是ta明确说不想两个人单独碰面但仍正常交流，越想越乱，这种情况正常该怎么处理？
**RelevantIDs:** kb_v2_051
**GradedRelevance:** kb_v2_051=3
**HardNegativeIDs:** []

---

## rag_v2_test_038

**QueryType:** multi_goal
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** understand, end_relationship
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边既有长期不开心但没有明显大矛盾是否该结束这段关系，同时还有同一个核心问题反复谈过仍没有变化。我既想判断现状，也需要决定怎么行动，应该先处理哪些信息、再怎么做？
**RelevantIDs:** kb_v2_431, kb_v2_432
**GradedRelevance:** kb_v2_431=3, kb_v2_432=2
**HardNegativeIDs:** []

---

## rag_v2_test_039

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** progress, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是第一次约会结束后ta没有明确评价。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_058
**GradedRelevance:** kb_v2_058=3, kb_v2_059=0
**HardNegativeIDs:** kb_v2_059

---

## rag_v2_test_040

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate, repair
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 有点拿不准，异地我们两个人对视频频率需求不同，我现在是继续、等等看，还是直接说清楚比较好？
**RelevantIDs:** kb_v2_221
**GradedRelevance:** kb_v2_221=3
**HardNegativeIDs:** []

---

## rag_v2_test_041

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** progress, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边就是第一次约会后我很喜欢但不确定ta感受，越想越乱，这种情况正常该怎么处理？
**RelevantIDs:** kb_v2_061
**GradedRelevance:** kb_v2_061=3
**HardNegativeIDs:** []

---

## rag_v2_test_042

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** understand, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 最近遇到一个情况：会吃醋但又强调只是朋友。这种时候更合适的判断和处理顺序是什么？
**RelevantIDs:** kb_v2_139
**GradedRelevance:** kb_v2_139=3
**HardNegativeIDs:** []

---

## rag_v2_test_043

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** progress, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边就是ta约会时老是看手机应该如何理解，越想越乱，这种情况正常该怎么处理？
**RelevantIDs:** kb_v2_056
**GradedRelevance:** kb_v2_056=3
**HardNegativeIDs:** []

---

## rag_v2_test_044

**QueryType:** multi_goal
**Difficulty:** medium
**ExpectedBranch:** safety
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** high
**NoAnswer:** false
**Query:** 我这边既有争执中出现掐脖子或其他严重肢体攻击，同时还有ta在工作地或学校持续守候我。我既想判断现状，也需要决定怎么行动，应该先处理哪些信息、再怎么做？
**RelevantIDs:** kb_v2_424, kb_v2_425
**GradedRelevance:** kb_v2_424=3, kb_v2_425=2
**HardNegativeIDs:** []

---

## rag_v2_test_045

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** understand, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是愿意线下碰面却几乎不在线上联络。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_135
**GradedRelevance:** kb_v2_135=3, kb_v2_136=0
**HardNegativeIDs:** kb_v2_136

---

## rag_v2_test_046

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** acquaintance
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 最近遇到一个情况：突然开始老是使用爱心表情。这种时候更合适的判断和处理顺序是什么？
**RelevantIDs:** kb_v2_110
**GradedRelevance:** kb_v2_110=3
**HardNegativeIDs:** []

---

## rag_v2_test_047

**QueryType:** long_context
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 背景是双方之前有一段时间相处正常，最近工作、学习和情绪都比较忙乱。现在出现暂停沟通后不知道如何重新开口，我分不清这是短期波动、需求差异还是关系本身的信号。有哪些判断依据，沟通时应该抓住什么重点？
**RelevantIDs:** kb_v2_190
**GradedRelevance:** kb_v2_190=3
**HardNegativeIDs:** kb_v2_191

---

## rag_v2_test_048

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我遇到的是“仪式感变成任务后我们两个人都累”，和看起来很像的其他情况不太一样。哪些细节能区分，具体该怎么做？
**RelevantIDs:** kb_v2_305
**GradedRelevance:** kb_v2_305=3, kb_v2_306=0
**HardNegativeIDs:** kb_v2_306

---

## rag_v2_test_049

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** progress, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 关于担心表白后连朋友都做不成，我最容易误判的地方是什么，下一步应该怎么做？
**RelevantIDs:** kb_v2_073
**GradedRelevance:** kb_v2_073=3
**HardNegativeIDs:** []

---

## rag_v2_test_050

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** pursuit
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate, progress
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是周末想有独处时间却总被要求见面，另一边又有第一次约会地点怎么选才不会压力太大。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_370, kb_v2_052
**GradedRelevance:** kb_v2_370=3, kb_v2_052=2
**HardNegativeIDs:** []

---

## rag_v2_test_051

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, repair, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是节假日去谁家总会争吵。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_236
**GradedRelevance:** kb_v2_236=3, kb_v2_237=0
**HardNegativeIDs:** kb_v2_237

---

## rag_v2_test_052

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** conflict
**RelationshipStage:** dating
**ExpectedGoals:** progress, communicate, repair
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是第一次约会聊得不错但结束很突然，另一边又有ta认错和修复了我仍然很难释怀。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_053, kb_v2_178
**GradedRelevance:** kb_v2_053=3, kb_v2_178=2
**HardNegativeIDs:** []

---

## rag_v2_test_053

**QueryType:** long_context
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** breakup
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 背景是双方之前有一段时间相处正常，最近工作、学习和情绪都比较忙乱。现在出现看到以前的对象开始新这段关系很难受，我分不清这是短期波动、需求差异还是关系本身的信号。有哪些判断依据，沟通时应该抓住什么重点？
**RelevantIDs:** kb_v2_475
**GradedRelevance:** kb_v2_475=3
**HardNegativeIDs:** kb_v2_476

---

## rag_v2_test_054

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 最近遇到一个情况：节假日如何分配给我们两个人家庭。这种时候更合适的判断和处理顺序是什么？
**RelevantIDs:** kb_v2_345
**GradedRelevance:** kb_v2_345=3
**HardNegativeIDs:** []

---

## rag_v2_test_055

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate, progress
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是如何支持对象换工作或转行。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_327
**GradedRelevance:** kb_v2_327=3, kb_v2_328=0
**HardNegativeIDs:** kb_v2_328

---

## rag_v2_test_056

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** long_distance
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我遇到的是“对象压力大时容易把情绪带到这段关系里”，和看起来很像的其他情况不太一样。哪些细节能区分，具体该怎么做？
**RelevantIDs:** kb_v2_297
**GradedRelevance:** kb_v2_297=3, kb_v2_298=0
**HardNegativeIDs:** kb_v2_298

---

## rag_v2_test_057

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边就是ta所有朋友都使用相似的表情风格，越想越乱，这种情况正常该怎么处理？
**RelevantIDs:** kb_v2_117
**GradedRelevance:** kb_v2_117=3
**HardNegativeIDs:** []

---

## rag_v2_test_058

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** pursuit
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, understand, progress
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是我们里有一个人不想牵手时另我们里有一个人应该怎么回应，另一边又有ta说顺其自然但我需要更清晰的方向。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_382, kb_v2_071
**GradedRelevance:** kb_v2_382=3, kb_v2_071=2
**HardNegativeIDs:** []

---

## rag_v2_test_059

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是消息未及时回复容易被误解为冷淡。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_268
**GradedRelevance:** kb_v2_268=3, kb_v2_269=0
**HardNegativeIDs:** kb_v2_269

---

## rag_v2_test_060

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 最近遇到一个情况：忙碌期经常临时说来不了计划。这种时候更合适的判断和处理顺序是什么？
**RelevantIDs:** kb_v2_321
**GradedRelevance:** kb_v2_321=3
**HardNegativeIDs:** []

---

## rag_v2_test_061

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** acquaintance
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边就是经常点赞我的朋友圈但私聊很少，越想越乱，这种情况正常该怎么处理？
**RelevantIDs:** kb_v2_118
**GradedRelevance:** kb_v2_118=3
**HardNegativeIDs:** []

---

## rag_v2_test_062

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** safety
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** conflict
**RelationshipStage:** ambiguous
**ExpectedGoals:** set_boundary, end_relationship, communicate, repair
**ExpectedRiskLevel:** high
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是ta偷拍视频或录音来逼我，另一边又有家里人发生矛盾后对象不知道该站哪边。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_429, kb_v2_241
**GradedRelevance:** kb_v2_429=3, kb_v2_241=2
**HardNegativeIDs:** []

---

## rag_v2_test_063

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是这段关系进入稳定期后感觉缺少新鲜感。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_348
**GradedRelevance:** kb_v2_348=3, kb_v2_349=0
**HardNegativeIDs:** kb_v2_349

---

## rag_v2_test_064

**QueryType:** long_context
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我们前一阵互动总体还算稳定，最近生活节奏有些变化，中间也没有发生特别大的事件。但这几天出现了“我们里有一个人做饭另我们里有一个人不认可这种贡献”。我不想因为一次变化就过度判断，也不想一直拖着不处理。结合这种上下文，我现在最应该确认什么，接下来怎么做比较稳妥？
**RelevantIDs:** kb_v2_334
**GradedRelevance:** kb_v2_334=3
**HardNegativeIDs:** kb_v2_335

---

## rag_v2_test_065

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** understand, end_relationship
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是同一个核心问题反复谈过仍没有变化。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_432
**GradedRelevance:** kb_v2_432=3, kb_v2_433=0
**HardNegativeIDs:** kb_v2_433

---

## rag_v2_test_066

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stranger
**ExpectedGoals:** initiate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 有点拿不准，ta偶尔主动来找打招呼但互动很短，我现在是继续、等等看，还是直接说清楚比较好？
**RelevantIDs:** kb_v2_009
**GradedRelevance:** kb_v2_009=3
**HardNegativeIDs:** []

---

## rag_v2_test_067

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** dating
**ExpectedGoals:** understand, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是短期波动很多但整体投入越来越稳定，另一边又有稳定这段关系里怎样保持小而持续的亲密。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_159, kb_v2_306
**GradedRelevance:** kb_v2_159=3, kb_v2_306=2
**HardNegativeIDs:** []

---

## rag_v2_test_068

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** acquaintance
**ExpectedGoals:** communicate, progress
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 有点拿不准，总是我先开话题是否应该停一停，我现在是继续、等等看，还是直接说清楚比较好？
**RelevantIDs:** kb_v2_031
**GradedRelevance:** kb_v2_031=3
**HardNegativeIDs:** []

---

## rag_v2_test_069

**QueryType:** long_context
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand, progress, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 背景是双方之前有一段时间相处正常，最近工作、学习和情绪都比较忙乱。现在出现喜欢的是同班同学担心失败后尴尬，我分不清这是短期波动、需求差异还是关系本身的信号。有哪些判断依据，沟通时应该抓住什么重点？
**RelevantIDs:** kb_v2_081
**GradedRelevance:** kb_v2_081=3
**HardNegativeIDs:** kb_v2_082

---

## rag_v2_test_070

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** end_relationship, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我遇到的是“ta不断追问“到底为什么”应该解释多少”，和看起来很像的其他情况不太一样。哪些细节能区分，具体该怎么做？
**RelevantIDs:** kb_v2_449
**GradedRelevance:** kb_v2_449=3, kb_v2_450=0
**HardNegativeIDs:** kb_v2_450

---

## rag_v2_test_071

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 关于对象反复抱怨同一件事让我疲惫，我最容易误判的地方是什么，下一步应该怎么做？
**RelevantIDs:** kb_v2_292
**GradedRelevance:** kb_v2_292=3
**HardNegativeIDs:** []

---

## rag_v2_test_072

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我遇到的是“公开恋情的时间点意见不同”，和看起来很像的其他情况不太一样。哪些细节能区分，具体该怎么做？
**RelevantIDs:** kb_v2_344
**GradedRelevance:** kb_v2_344=3, kb_v2_345=0
**HardNegativeIDs:** kb_v2_345

---

## rag_v2_test_073

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边就是对象不希望我单独和这段关系比较近的异性朋友见面，越想越乱，这种情况正常该怎么处理？
**RelevantIDs:** kb_v2_374
**GradedRelevance:** kb_v2_374=3
**HardNegativeIDs:** []

---

## rag_v2_test_074

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** pursuit
**RelationshipStage:** acquaintance
**ExpectedGoals:** understand, progress, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是回复慢但每次都会认真接上之前的话题，另一边又有第一次约会地点怎么选才不会压力太大。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_146, kb_v2_052
**GradedRelevance:** kb_v2_146=3, kb_v2_052=2
**HardNegativeIDs:** []

---

## rag_v2_test_075

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** boundary
**RelationshipStage:** acquaintance
**ExpectedGoals:** initiate, progress, set_boundary, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是ta迟迟没有通过好友申请，另一边又有ta希望所有聚会都带上我。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_014, kb_v2_379
**GradedRelevance:** kb_v2_014=3, kb_v2_379=2
**HardNegativeIDs:** []

---

## rag_v2_test_076

**QueryType:** multi_goal
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边既有我们里有一个人希望见家里人另我们里有一个人暂时不愿意，同时还有家人直接介入两人的重要决定。我既想判断现状，也需要决定怎么行动，应该先处理哪些信息、再怎么做？
**RelevantIDs:** kb_v2_404, kb_v2_405
**GradedRelevance:** kb_v2_404=3, kb_v2_405=2
**HardNegativeIDs:** []

---

## rag_v2_test_077

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是总用忙作为理由但从不主动来找恢复联络。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_145
**GradedRelevance:** kb_v2_145=3, kb_v2_146=0
**HardNegativeIDs:** kb_v2_146

---

## rag_v2_test_078

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** chat_analysis
**RelationshipStage:** ambiguous
**ExpectedGoals:** progress, communicate, understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是什么时候表达好感比突然表白更合适，另一边又有白天几乎不回但晚上会认真回复。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_072, kb_v2_088
**GradedRelevance:** kb_v2_072=3, kb_v2_088=2
**HardNegativeIDs:** []

---

## rag_v2_test_079

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 关于ta未经明确愿意使用我的信用卡，我最容易误判的地方是什么，下一步应该怎么做？
**RelevantIDs:** kb_v2_397
**GradedRelevance:** kb_v2_397=3
**HardNegativeIDs:** []

---

## rag_v2_test_080

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** conflict
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, set_boundary, repair
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是两人同时工作很忙很难安排时间，另一边又有我想看对象手机来获得安全感。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_320, kb_v2_246
**GradedRelevance:** kb_v2_320=3, kb_v2_246=2
**HardNegativeIDs:** []

---

## rag_v2_test_081

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** long_distance
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是ta承诺改变但我仍会反复确认。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_204
**GradedRelevance:** kb_v2_204=3, kb_v2_205=0
**HardNegativeIDs:** kb_v2_205

---

## rag_v2_test_082

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** conflict
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate, repair
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是家人直接介入两人的重要决定，另一边又有我不知道怎样表达失望而不攻击。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_405, kb_v2_198
**GradedRelevance:** kb_v2_405=3, kb_v2_198=2
**HardNegativeIDs:** []

---

## rag_v2_test_083

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边就是对象用送礼后要求回报让我不舒服，越想越乱，这种情况正常该怎么处理？
**RelevantIDs:** kb_v2_396
**GradedRelevance:** kb_v2_396=3
**HardNegativeIDs:** []

---

## rag_v2_test_084

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我遇到的是“备考那阵子联络下降但结束后会恢复”，和看起来很像的其他情况不太一样。哪些细节能区分，具体该怎么做？
**RelevantIDs:** kb_v2_143
**GradedRelevance:** kb_v2_143=3, kb_v2_144=0
**HardNegativeIDs:** kb_v2_144

---

## rag_v2_test_085

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 关于ta会记得我之前提过的小事，我最容易误判的地方是什么，下一步应该怎么做？
**RelevantIDs:** kb_v2_109
**GradedRelevance:** kb_v2_109=3
**HardNegativeIDs:** []

---

## rag_v2_test_086

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我遇到的是“对象和这段关系比较近的异性朋友单独吃饭让我不舒服”，和看起来很像的其他情况不太一样。哪些细节能区分，具体该怎么做？
**RelevantIDs:** kb_v2_210
**GradedRelevance:** kb_v2_210=3, kb_v2_211=0
**HardNegativeIDs:** kb_v2_211

---

## rag_v2_test_087

**QueryType:** multi_goal
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand, progress, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边既有ta刚结束上一段这段关系是否适合推进，同时还有我们两个人年龄或生活阶段差异较大。我既想判断现状，也需要决定怎么行动，应该先处理哪些信息、再怎么做？
**RelevantIDs:** kb_v2_083, kb_v2_084
**GradedRelevance:** kb_v2_083=3, kb_v2_084=2
**HardNegativeIDs:** []

---

## rag_v2_test_088

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** pursuit
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand, progress, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是经常使用“我们”是否意味着这段关系更近，另一边又有互动还不稳定时是否适合直接表白。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_103, kb_v2_075
**GradedRelevance:** kb_v2_103=3, kb_v2_075=2
**HardNegativeIDs:** []

---

## rag_v2_test_089

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** long_distance
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边就是ta总要求证明我没有隐瞒，越想越乱，这种情况正常该怎么处理？
**RelevantIDs:** kb_v2_207
**GradedRelevance:** kb_v2_207=3
**HardNegativeIDs:** []

---

## rag_v2_test_090

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我遇到的是“每次矛盾都会消失两三天”，和看起来很像的其他情况不太一样。哪些细节能区分，具体该怎么做？
**RelevantIDs:** kb_v2_189
**GradedRelevance:** kb_v2_189=3, kb_v2_190=0
**HardNegativeIDs:** kb_v2_190

---

## rag_v2_test_091

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** acquaintance
**ExpectedGoals:** communicate, progress
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 关于刚认识就每天高频交流是否太快，我最容易误判的地方是什么，下一步应该怎么做？
**RelevantIDs:** kb_v2_029
**GradedRelevance:** kb_v2_029=3
**HardNegativeIDs:** []

---

## rag_v2_test_092

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** dating
**ExpectedGoals:** communicate, set_boundary, repair
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是ta不愿透露上一段这段关系细节，另一边又有忙碌期经常临时说来不了计划。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_244, kb_v2_321
**GradedRelevance:** kb_v2_244=3, kb_v2_321=2
**HardNegativeIDs:** []

---

## rag_v2_test_093

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 关于恋爱期间是否应该替ta承担大额债务，我最容易误判的地方是什么，下一步应该怎么做？
**RelevantIDs:** kb_v2_395
**GradedRelevance:** kb_v2_395=3
**HardNegativeIDs:** []

---

## rag_v2_test_094

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 有点拿不准，已经开始新生活但以前的对象老是回忆过去，我现在是继续、等等看，还是直接说清楚比较好？
**RelevantIDs:** kb_v2_464
**GradedRelevance:** kb_v2_464=3
**HardNegativeIDs:** []

---

## rag_v2_test_095

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, repair
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 关于价值观相近但人生时间表不一致，我最容易误判的地方是什么，下一步应该怎么做？
**RelevantIDs:** kb_v2_260
**GradedRelevance:** kb_v2_260=3
**HardNegativeIDs:** []

---

## rag_v2_test_096

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** end_relationship, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我遇到的是“担心结束这段关系谈话变成无休止争论”，和看起来很像的其他情况不太一样。哪些细节能区分，具体该怎么做？
**RelevantIDs:** kb_v2_448
**GradedRelevance:** kb_v2_448=3, kb_v2_441=0
**HardNegativeIDs:** kb_v2_441

---

## rag_v2_test_097

**QueryType:** long_context
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 背景是双方之前有一段时间相处正常，最近工作、学习和情绪都比较忙乱。现在出现对象工作受挫时不知道怎么安慰，我分不清这是短期波动、需求差异还是关系本身的信号。有哪些判断依据，沟通时应该抓住什么重点？
**RelevantIDs:** kb_v2_289
**GradedRelevance:** kb_v2_289=3
**HardNegativeIDs:** kb_v2_290

---

## rag_v2_test_098

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我遇到的是“开始新这段关系后以前的对象仍老是联络”，和看起来很像的其他情况不太一样。哪些细节能区分，具体该怎么做？
**RelevantIDs:** kb_v2_498
**GradedRelevance:** kb_v2_498=3, kb_v2_499=0
**HardNegativeIDs:** kb_v2_499

---

## rag_v2_test_099

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** acquaintance
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 关于发合照但没有说明这段关系，我最容易误判的地方是什么，下一步应该怎么做？
**RelevantIDs:** kb_v2_121
**GradedRelevance:** kb_v2_121=3
**HardNegativeIDs:** []

---

## rag_v2_test_100

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate, set_boundary, repair
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我遇到的是“我想看对象手机来获得安全感”，和看起来很像的其他情况不太一样。哪些细节能区分，具体该怎么做？
**RelevantIDs:** kb_v2_246
**GradedRelevance:** kb_v2_246=3, kb_v2_247=0
**HardNegativeIDs:** kb_v2_247

---

## rag_v2_test_101

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** safety
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** high
**NoAnswer:** false
**Query:** 我这边就是明确要求停止联络后仍被大量反复纠缠，越想越乱，这种情况正常该怎么处理？
**RelevantIDs:** kb_v2_422
**GradedRelevance:** kb_v2_422=3
**HardNegativeIDs:** []

---

## rag_v2_test_102

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是需要冷静但ta觉得我在逃避，另一边又有项目高峰期几乎没有时间约会。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_187, kb_v2_317
**GradedRelevance:** kb_v2_187=3, kb_v2_317=2
**HardNegativeIDs:** []

---

## rag_v2_test_103

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是我们两个人收入差异很大如何分担生活费。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_335
**GradedRelevance:** kb_v2_335=3, kb_v2_336=0
**HardNegativeIDs:** kb_v2_336

---

## rag_v2_test_104

**QueryType:** noisy_typo
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 最近上班日回复慢但周末明显更积极，我有点搞不懂ta啥意思，也不想瞎脑补，咋处理比较稳？
**RelevantIDs:** kb_v2_087
**GradedRelevance:** kb_v2_087=3
**HardNegativeIDs:** []

---

## rag_v2_test_105

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** acquaintance
**ExpectedGoals:** understand, progress, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 关于喜欢的是同事需要考虑职场边界，我最容易误判的地方是什么，下一步应该怎么做？
**RelevantIDs:** kb_v2_080
**GradedRelevance:** kb_v2_080=3
**HardNegativeIDs:** []

---

## rag_v2_test_106

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** breakup
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我遇到的是“结束这段关系后睡眠和工作状态明显受影响”，和看起来很像的其他情况不太一样。哪些细节能区分，具体该怎么做？
**RelevantIDs:** kb_v2_479
**GradedRelevance:** kb_v2_479=3, kb_v2_480=0
**HardNegativeIDs:** kb_v2_480

---

## rag_v2_test_107

**QueryType:** multi_goal
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, repair
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边既有最近上班事情很多导致约会经常被取消，同时还有周末总优先朋友让对象觉得被忽视。我既想判断现状，也需要决定怎么行动，应该先处理哪些信息、再怎么做？
**RelevantIDs:** kb_v2_219, kb_v2_220
**GradedRelevance:** kb_v2_219=3, kb_v2_220=2
**HardNegativeIDs:** []

---

## rag_v2_test_108

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** chat_analysis
**RelationshipStage:** ambiguous
**ExpectedGoals:** initiate, communicate, understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是ta回复简短时如何换话题，另一边又有只有需要帮忙时才主动来找联络我。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_022, kb_v2_148
**GradedRelevance:** kb_v2_022=3, kb_v2_148=2
**HardNegativeIDs:** []

---

## rag_v2_test_109

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是经常隔天回复但内容很详细。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_090
**GradedRelevance:** kb_v2_090=3, kb_v2_091=0
**HardNegativeIDs:** kb_v2_091

---

## rag_v2_test_110

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** repair
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 有点拿不准，认错和修复很多次但同样的问题反复发生，我现在是继续、等等看，还是直接说清楚比较好？
**RelevantIDs:** kb_v2_177
**GradedRelevance:** kb_v2_177=3
**HardNegativeIDs:** []

---

## rag_v2_test_111

**QueryType:** long_context
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stranger
**ExpectedGoals:** initiate, progress
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 背景是双方之前有一段时间相处正常，最近工作、学习和情绪都比较忙乱。现在出现想从线下认识自然过渡到加联络方式，我分不清这是短期波动、需求差异还是关系本身的信号。有哪些判断依据，沟通时应该抓住什么重点？
**RelevantIDs:** kb_v2_011
**GradedRelevance:** kb_v2_011=3
**HardNegativeIDs:** kb_v2_012

---

## rag_v2_test_112

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** conflict
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, set_boundary, repair
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是一个人更擅长计划导致承担了所有隐形劳动，另一边又有过去交流记录被翻出后引发争吵。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_338, kb_v2_249
**GradedRelevance:** kb_v2_338=3, kb_v2_249=2
**HardNegativeIDs:** []

---

## rag_v2_test_113

**QueryType:** multi_goal
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate, repair
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边既有我们里有一个人想每天见面另我们里有一个人需要更多个人时间，同时还有最近上班事情很多导致约会经常被取消。我既想判断现状，也需要决定怎么行动，应该先处理哪些信息、再怎么做？
**RelevantIDs:** kb_v2_218, kb_v2_219
**GradedRelevance:** kb_v2_218=3, kb_v2_219=2
**HardNegativeIDs:** []

---

## rag_v2_test_114

**QueryType:** multi_goal
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边既有对象在社交平台和异性互动很多，同时还有我容易因为ta同事吃醋。我既想判断现状，也需要决定怎么行动，应该先处理哪些信息、再怎么做？
**RelevantIDs:** kb_v2_212, kb_v2_213
**GradedRelevance:** kb_v2_212=3, kb_v2_213=2
**HardNegativeIDs:** []

---

## rag_v2_test_115

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** end_relationship, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 关于ta最近有重要考试是否应该推迟，我最容易误判的地方是什么，下一步应该怎么做？
**RelevantIDs:** kb_v2_445
**GradedRelevance:** kb_v2_445=3
**HardNegativeIDs:** []

---

## rag_v2_test_116

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** safety
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** conflict
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** sensitive
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是ta通过冷动手迫使我让步，另一边又有我们里有一个人花钱更高另我们里有一个人感到压力。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_410, kb_v2_227
**GradedRelevance:** kb_v2_410=3, kb_v2_227=2
**HardNegativeIDs:** []

---

## rag_v2_test_117

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是回复常用“嗯嗯”“好呀”该怎么理解。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_105
**GradedRelevance:** kb_v2_105=3, kb_v2_106=0
**HardNegativeIDs:** kb_v2_106

---

## rag_v2_test_118

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** chat_analysis
**RelationshipStage:** ambiguous
**ExpectedGoals:** initiate, communicate, understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是ta很少分享生活该如何找话题，另一边又有见面时很热情线上却很冷淡。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_026, kb_v2_126
**GradedRelevance:** kb_v2_026=3, kb_v2_126=2
**HardNegativeIDs:** []

---

## rag_v2_test_119

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** long_distance
**ExpectedGoals:** understand, end_relationship, communicate, progress
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是我们两个人价值观存在难以调和的差异，另一边又有我们里有一个人开始新兴趣另我们里有一个人觉得被冷落。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_433, kb_v2_326
**GradedRelevance:** kb_v2_433=3, kb_v2_326=2
**HardNegativeIDs:** []

---

## rag_v2_test_120

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** safety
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary
**ExpectedRiskLevel:** sensitive
**NoAnswer:** false
**Query:** 有点拿不准，每次拒绝后都会遭到长时间羞辱，我现在是继续、等等看，还是直接说清楚比较好？
**RelevantIDs:** kb_v2_409
**GradedRelevance:** kb_v2_409=3
**HardNegativeIDs:** []

---

## rag_v2_test_121

**QueryType:** multi_goal
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边既有开始把我加入仅部分人可见的动态，同时还有发合照但没有说明这段关系。我既想判断现状，也需要决定怎么行动，应该先处理哪些信息、再怎么做？
**RelevantIDs:** kb_v2_120, kb_v2_121
**GradedRelevance:** kb_v2_120=3, kb_v2_121=2
**HardNegativeIDs:** []

---

## rag_v2_test_122

**QueryType:** multi_goal
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** repair
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边既有我说错话后不知道怎样真诚认错和修复，同时还有认错和修复很多次但同样的问题反复发生。我既想判断现状，也需要决定怎么行动，应该先处理哪些信息、再怎么做？
**RelevantIDs:** kb_v2_176, kb_v2_177
**GradedRelevance:** kb_v2_176=3, kb_v2_177=2
**HardNegativeIDs:** []

---

## rag_v2_test_123

**QueryType:** multi_goal
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边既有对象不喜欢我的朋友圈怎么办，同时还有我们两个人朋友聚会风格差异很大。我既想判断现状，也需要决定怎么行动，应该先处理哪些信息、再怎么做？
**RelevantIDs:** kb_v2_341, kb_v2_342
**GradedRelevance:** kb_v2_341=3, kb_v2_342=2
**HardNegativeIDs:** []

---

## rag_v2_test_124

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 有点拿不准，交流一般但每次邀约都愿意线下碰面，我现在是继续、等等看，还是直接说清楚比较好？
**RelevantIDs:** kb_v2_127
**GradedRelevance:** kb_v2_127=3
**HardNegativeIDs:** []

---

## rag_v2_test_125

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是结束这段关系后仍互相看朋友圈和社媒越来越焦虑。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_463
**GradedRelevance:** kb_v2_463=3, kb_v2_464=0
**HardNegativeIDs:** kb_v2_464

---

## rag_v2_test_126

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 有点拿不准，异地时ta要求长时间保持视频通话，我现在是继续、等等看，还是直接说清楚比较好？
**RelevantIDs:** kb_v2_373
**GradedRelevance:** kb_v2_373=3
**HardNegativeIDs:** []

---

## rag_v2_test_127

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** progress
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是想邀请同学在课外一起活动。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_050
**GradedRelevance:** kb_v2_050=3, kb_v2_051=0
**HardNegativeIDs:** kb_v2_051

---

## rag_v2_test_128

**QueryType:** multi_goal
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** acquaintance
**ExpectedGoals:** initiate, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边既有我很会讲但很少给ta表达空间，同时还有ta很少分享生活该如何找话题。我既想判断现状，也需要决定怎么行动，应该先处理哪些信息、再怎么做？
**RelevantIDs:** kb_v2_025, kb_v2_026
**GradedRelevance:** kb_v2_025=3, kb_v2_026=2
**HardNegativeIDs:** []

---

## rag_v2_test_129

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是已经放下以前的对象但共同朋友圈仍会见面。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_500
**GradedRelevance:** kb_v2_500=3, kb_v2_498=0
**HardNegativeIDs:** kb_v2_498

---

## rag_v2_test_130

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 有点拿不准，发现前后说法不一致不知道怎么问，我现在是继续、等等看，还是直接说清楚比较好？
**RelevantIDs:** kb_v2_206
**GradedRelevance:** kb_v2_206=3
**HardNegativeIDs:** []

---

## rag_v2_test_131

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** end_relationship, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边就是住在一起结束这段关系后短期还必须住在一起，越想越乱，这种情况正常该怎么处理？
**RelevantIDs:** kb_v2_488
**GradedRelevance:** kb_v2_488=3
**HardNegativeIDs:** []

---

## rag_v2_test_132

**QueryType:** multi_goal
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** progress, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边既有约会过程中我们两个人花钱习惯差异明显，同时还有第一次约会时间拖得太长我们两个人都很疲惫。我既想判断现状，也需要决定怎么行动，应该先处理哪些信息、再怎么做？
**RelevantIDs:** kb_v2_059, kb_v2_060
**GradedRelevance:** kb_v2_059=3, kb_v2_060=2
**HardNegativeIDs:** []

---

## rag_v2_test_133

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate, progress
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是对象取得成绩时我反而产生自卑。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_331
**GradedRelevance:** kb_v2_331=3, kb_v2_332=0
**HardNegativeIDs:** kb_v2_332

---

## rag_v2_test_134

**QueryType:** long_context
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我们前一阵互动总体还算稳定，最近生活节奏有些变化，中间也没有发生特别大的事件。但这几天出现了“对象不喜欢某个朋友就要求断交”。我不想因为一次变化就过度判断，也不想一直拖着不处理。结合这种上下文，我现在最应该确认什么，接下来怎么做比较稳妥？
**RelevantIDs:** kb_v2_377
**GradedRelevance:** kb_v2_377=3
**HardNegativeIDs:** kb_v2_378

---

## rag_v2_test_135

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是白天几乎不回但晚上会认真回复。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_088
**GradedRelevance:** kb_v2_088=3, kb_v2_089=0
**HardNegativeIDs:** kb_v2_089

---

## rag_v2_test_136

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 最近遇到一个情况：交流很亲密但一直不愿线下碰面。这种时候更合适的判断和处理顺序是什么？
**RelevantIDs:** kb_v2_134
**GradedRelevance:** kb_v2_134=3
**HardNegativeIDs:** []

---

## rag_v2_test_137

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** acquaintance
**ExpectedGoals:** progress
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是第一次约ta出去没有约成但ta说最近很忙。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_037
**GradedRelevance:** kb_v2_037=3, kb_v2_038=0
**HardNegativeIDs:** kb_v2_038

---

## rag_v2_test_138

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** boundary
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是以前的对象深夜情绪化联络我，另一边又有亲吻前如何确认ta意愿。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_462, kb_v2_383
**GradedRelevance:** kb_v2_462=3, kb_v2_383=2
**HardNegativeIDs:** []

---

## rag_v2_test_139

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** dating
**ExpectedGoals:** understand, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是只有需要帮忙时才主动来找联络我，另一边又有稳定恋爱后交流明显没有暧昧期多。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_148, kb_v2_261
**GradedRelevance:** kb_v2_148=3, kb_v2_261=2
**HardNegativeIDs:** []

---

## rag_v2_test_140

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stranger
**ExpectedGoals:** initiate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 最近遇到一个情况：在工作场合认识又担心太冒进。这种时候更合适的判断和处理顺序是什么？
**RelevantIDs:** kb_v2_005
**GradedRelevance:** kb_v2_005=3
**HardNegativeIDs:** []

---

## rag_v2_test_141

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** acquaintance
**ExpectedGoals:** initiate, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是每天都问吃了吗感觉越来越机械。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_021
**GradedRelevance:** kb_v2_021=3, kb_v2_022=0
**HardNegativeIDs:** kb_v2_022

---

## rag_v2_test_142

**QueryType:** noisy_typo
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 最近我们两个人作息不同导致经常错过交流，我有点搞不懂ta啥意思，也不想瞎脑补，咋处理比较稳？
**RelevantIDs:** kb_v2_269
**GradedRelevance:** kb_v2_269=3
**HardNegativeIDs:** []

---

## rag_v2_test_143

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边就是约会花费谁承担总是意见不同，越想越乱，这种情况正常该怎么处理？
**RelevantIDs:** kb_v2_226
**GradedRelevance:** kb_v2_226=3
**HardNegativeIDs:** []

---

## rag_v2_test_144

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** long_distance
**ExpectedGoals:** communicate, progress
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我遇到的是“我们两个人目标一致但很少真正推进”，和看起来很像的其他情况不太一样。哪些细节能区分，具体该怎么做？
**RelevantIDs:** kb_v2_351
**GradedRelevance:** kb_v2_351=3, kb_v2_352=0
**HardNegativeIDs:** kb_v2_352

---

## rag_v2_test_145

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** safety
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary
**ExpectedRiskLevel:** sensitive
**NoAnswer:** false
**Query:** 我这边就是对象反复劝说直到我勉强答应，越想越乱，这种情况正常该怎么处理？
**RelevantIDs:** kb_v2_390
**GradedRelevance:** kb_v2_390=3
**HardNegativeIDs:** []

---

## rag_v2_test_146

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** acquaintance
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 最近遇到一个情况：以前很多表情最近突然变少。这种时候更合适的判断和处理顺序是什么？
**RelevantIDs:** kb_v2_114
**GradedRelevance:** kb_v2_114=3
**HardNegativeIDs:** []

---

## rag_v2_test_147

**QueryType:** multi_goal
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** acquaintance
**ExpectedGoals:** initiate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边既有刚认识时我过度紧张容易连续输出，同时还有第一次见面后想自然开启后续交流。我既想判断现状，也需要决定怎么行动，应该先处理哪些信息、再怎么做？
**RelevantIDs:** kb_v2_010, kb_v2_001
**GradedRelevance:** kb_v2_010=3, kb_v2_001=2
**HardNegativeIDs:** []

---

## rag_v2_test_148

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate, repair
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 最近遇到一个情况：每次讨论都被打断无法完整表达。这种时候更合适的判断和处理顺序是什么？
**RelevantIDs:** kb_v2_200
**GradedRelevance:** kb_v2_200=3
**HardNegativeIDs:** []

---

## rag_v2_test_149

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是之前借给以前的对象的钱还没有还。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_483
**GradedRelevance:** kb_v2_483=3, kb_v2_484=0
**HardNegativeIDs:** kb_v2_484

---

## rag_v2_test_150

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 最近遇到一个情况：我们里有一个人需要拥抱另我们里有一个人习惯讲道理。这种时候更合适的判断和处理顺序是什么？
**RelevantIDs:** kb_v2_293
**GradedRelevance:** kb_v2_293=3
**HardNegativeIDs:** []

---

## rag_v2_test_151

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** safety
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** high
**NoAnswer:** false
**Query:** 关于ta没经过我明确愿意就保存或传播很私人的照片/视频，我最容易误判的地方是什么，下一步应该怎么做？
**RelevantIDs:** kb_v2_426
**GradedRelevance:** kb_v2_426=3
**HardNegativeIDs:** []

---

## rag_v2_test_152

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** long_distance
**ExpectedGoals:** end_relationship, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我遇到的是“已经多次暗示但ta没有理解”，和看起来很像的其他情况不太一样。哪些细节能区分，具体该怎么做？
**RelevantIDs:** kb_v2_446
**GradedRelevance:** kb_v2_446=3, kb_v2_447=0
**HardNegativeIDs:** kb_v2_447

---

## rag_v2_test_153

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** long_distance
**ExpectedGoals:** communicate, repair
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是周末总优先朋友让对象觉得被忽视。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_220
**GradedRelevance:** kb_v2_220=3, kb_v2_221=0
**HardNegativeIDs:** kb_v2_221

---

## rag_v2_test_154

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 最近遇到一个情况：我们里有一个人想尝试新的身体上的亲密行为另我们里有一个人犹豫。这种时候更合适的判断和处理顺序是什么？
**RelevantIDs:** kb_v2_386
**GradedRelevance:** kb_v2_386=3
**HardNegativeIDs:** []

---

## rag_v2_test_155

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** understand, end_relationship
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边就是这段关系里大部分时间都在焦虑和猜测，越想越乱，这种情况正常该怎么处理？
**RelevantIDs:** kb_v2_435
**GradedRelevance:** kb_v2_435=3
**HardNegativeIDs:** []

---

## rag_v2_test_156

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** breakup
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 最近遇到一个情况：结束这段关系后自责觉得都是我的问题。这种时候更合适的判断和处理顺序是什么？
**RelevantIDs:** kb_v2_477
**GradedRelevance:** kb_v2_477=3
**HardNegativeIDs:** []

---

## rag_v2_test_157

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** safety
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** high
**NoAnswer:** false
**Query:** 关于ta在我明确拒绝后仍持续上门，我最容易误判的地方是什么，下一步应该怎么做？
**RelevantIDs:** kb_v2_416
**GradedRelevance:** kb_v2_416=3
**HardNegativeIDs:** []

---

## rag_v2_test_158

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 最近遇到一个情况：住在一起后家务总落在一个人身上。这种时候更合适的判断和处理顺序是什么？
**RelevantIDs:** kb_v2_333
**GradedRelevance:** kb_v2_333=3
**HardNegativeIDs:** []

---

## rag_v2_test_159

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边就是两人兴趣差异大很难找共同活动，越想越乱，这种情况正常该怎么处理？
**RelevantIDs:** kb_v2_277
**GradedRelevance:** kb_v2_277=3
**HardNegativeIDs:** []

---

## rag_v2_test_160

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** end_relationship, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 最近遇到一个情况：ta承诺立刻改变让我动摇。这种时候更合适的判断和处理顺序是什么？
**RelevantIDs:** kb_v2_450
**GradedRelevance:** kb_v2_450=3
**HardNegativeIDs:** []

---

## rag_v2_test_161

**QueryType:** long_context
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** long_distance
**ExpectedGoals:** end_relationship, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 背景是双方之前有一段时间相处正常，最近工作、学习和情绪都比较忙乱。现在出现跨城市结束这段关系后物品归还成本很高，我分不清这是短期波动、需求差异还是关系本身的信号。有哪些判断依据，沟通时应该抓住什么重点？
**RelevantIDs:** kb_v2_492
**GradedRelevance:** kb_v2_492=3
**HardNegativeIDs:** kb_v2_488

---

## rag_v2_test_162

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** long_distance
**ExpectedGoals:** communicate, progress
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 有点拿不准，见面成本高导致计划总被推迟，我现在是继续、等等看，还是直接说清楚比较好？
**RelevantIDs:** kb_v2_309
**GradedRelevance:** kb_v2_309=3
**HardNegativeIDs:** []

---

## rag_v2_test_163

**QueryType:** paraphrase
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 关于共同旅行预算差异很大，我最容易误判的地方是什么，下一步应该怎么做？
**RelevantIDs:** kb_v2_230
**GradedRelevance:** kb_v2_230=3
**HardNegativeIDs:** []

---

## rag_v2_test_164

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate, repair
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我遇到的是“毕业后去不同城市发展”，和看起来很像的其他情况不太一样。哪些细节能区分，具体该怎么做？
**RelevantIDs:** kb_v2_253
**GradedRelevance:** kb_v2_253=3, kb_v2_254=0
**HardNegativeIDs:** kb_v2_254

---

## rag_v2_test_165

**QueryType:** multi_goal
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边既有经常主动来找靠近但从未谈过这段关系，同时还有见面时眼神接触很多是否代表喜欢。我既想判断现状，也需要决定怎么行动，应该先处理哪些信息、再怎么做？
**RelevantIDs:** kb_v2_131, kb_v2_132
**GradedRelevance:** kb_v2_131=3, kb_v2_132=2
**HardNegativeIDs:** []

---

## rag_v2_test_166

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我遇到的是“经常在朋友圈和社媒回复我的内容”，和看起来很像的其他情况不太一样。哪些细节能区分，具体该怎么做？
**RelevantIDs:** kb_v2_123
**GradedRelevance:** kb_v2_123=3, kb_v2_124=0
**HardNegativeIDs:** kb_v2_124

---

## rag_v2_test_167

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** chat_analysis
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是讨论未来总停留在很模糊的愿望，另一边又有ta愿意多人活动但回避两个人单独碰面。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_350, kb_v2_133
**GradedRelevance:** kb_v2_350=3, kb_v2_133=2
**HardNegativeIDs:** []

---

## rag_v2_test_168

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我遇到的是“ta情绪很差但拒绝谈原因”，和看起来很像的其他情况不太一样。哪些细节能区分，具体该怎么做？
**RelevantIDs:** kb_v2_295
**GradedRelevance:** kb_v2_295=3, kb_v2_296=0
**HardNegativeIDs:** kb_v2_296

---

## rag_v2_test_169

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** understand, end_relationship
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边就是未来城市和人生规划完全矛盾，越想越乱，这种情况正常该怎么处理？
**RelevantIDs:** kb_v2_437
**GradedRelevance:** kb_v2_437=3
**HardNegativeIDs:** []

---

## rag_v2_test_170

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 有点拿不准，我们两个人对夜间聚会接受程度不同，我现在是继续、等等看，还是直接说清楚比较好？
**RelevantIDs:** kb_v2_378
**GradedRelevance:** kb_v2_378=3
**HardNegativeIDs:** []

---

## rag_v2_test_171

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, set_boundary
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边就是我们里有一个人花钱更高另我们里有一个人感到压力，越想越乱，这种情况正常该怎么处理？
**RelevantIDs:** kb_v2_227
**GradedRelevance:** kb_v2_227=3
**HardNegativeIDs:** []

---

## rag_v2_test_172

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** chat_analysis
**RelationshipStage:** dating
**ExpectedGoals:** progress, understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是ta拒绝后我又给了另一个可行时间，另一边又有回消息的速度很快但内容非常短。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_039, kb_v2_091
**GradedRelevance:** kb_v2_039=3, kb_v2_091=2
**HardNegativeIDs:** []

---

## rag_v2_test_173

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, set_boundary, repair
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 同类情况很多容易搞混：现在是ta未经允许看了我的交流记录。我应该依据什么判断，而不是直接套结论？
**RelevantIDs:** kb_v2_247
**GradedRelevance:** kb_v2_247=3, kb_v2_248=0
**HardNegativeIDs:** kb_v2_248

---

## rag_v2_test_174

**QueryType:** multi_scenario
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** chat_analysis
**RelationshipStage:** dating
**ExpectedGoals:** understand, progress, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我现在同时碰到两件事：一边是互动像恋爱但ta不愿定义这段关系，另一边又有愿意线下碰面却几乎不在线上联络。这两个问题互相影响，我应该先处理哪个、分别依据什么来判断？
**RelevantIDs:** kb_v2_065, kb_v2_135
**GradedRelevance:** kb_v2_065=3, kb_v2_135=2
**HardNegativeIDs:** []

---

## rag_v2_test_175

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边就是工作时间是否需要随时回复，越想越乱，这种情况正常该怎么处理？
**RelevantIDs:** kb_v2_264
**GradedRelevance:** kb_v2_264=3
**HardNegativeIDs:** []

---

## rag_v2_test_176

**QueryType:** multi_goal
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边既有某次聊完后突然两三天没有消息，同时还有以前很快回，现在常常隔很久。我既想判断现状，也需要决定怎么行动，应该先处理哪些信息、再怎么做？
**RelevantIDs:** kb_v2_093, kb_v2_086
**GradedRelevance:** kb_v2_093=3, kb_v2_086=2
**HardNegativeIDs:** []

---

## rag_v2_test_177

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边就是ta希望所有聚会都带上我，越想越乱，这种情况正常该怎么处理？
**RelevantIDs:** kb_v2_379
**GradedRelevance:** kb_v2_379=3
**HardNegativeIDs:** []

---

## rag_v2_test_178

**QueryType:** hard_confusion
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我遇到的是“回复里经常使用波浪号或叠词”，和看起来很像的其他情况不太一样。哪些细节能区分，具体该怎么做？
**RelevantIDs:** kb_v2_113
**GradedRelevance:** kb_v2_113=3, kb_v2_114=0
**HardNegativeIDs:** kb_v2_114

---

## rag_v2_test_179

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** progress, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 我这边就是第一次约会时间拖得太长我们两个人都很疲惫，越想越乱，这种情况正常该怎么处理？
**RelevantIDs:** kb_v2_060
**GradedRelevance:** kb_v2_060=3
**HardNegativeIDs:** []

---

## rag_v2_test_180

**QueryType:** colloquial
**Difficulty:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** understand, progress
**ExpectedRiskLevel:** normal
**NoAnswer:** false
**Query:** 有点拿不准，暧昧很久后开始感到不确定和消耗，我现在是继续、等等看，还是直接说清楚比较好？
**RelevantIDs:** kb_v2_069
**GradedRelevance:** kb_v2_069=3
**HardNegativeIDs:** []

---

## rag_v2_test_181

**QueryType:** no_answer
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** true
**NoAnswerScope:** in_domain_uncovered
**Query:** 伴侣做完器官移植后需要长期恢复，我们的日常角色突然变成照护者和被照护者，怎样避免关系只剩下护理任务？
**RelevantIDs:** []
**GradedRelevance:** {}
**HardNegativeIDs:** []


---

## rag_v2_test_182

**QueryType:** no_answer
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** true
**NoAnswerScope:** in_domain_uncovered
**Query:** 伴侣长期透析，很多共同安排都要围绕治疗时间，我们都很疲惫，如何重新分配家务和关系中的期待？
**RelevantIDs:** []
**GradedRelevance:** {}
**HardNegativeIDs:** []


---

## rag_v2_test_183

**QueryType:** no_answer
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** true
**NoAnswerScope:** in_domain_uncovered
**Query:** 我们信仰不同，最近因为固定礼拜、斋戒和节日安排开始影响共同生活，应该怎样协商而不是要求一方放弃？
**RelevantIDs:** []
**GradedRelevance:** {}
**HardNegativeIDs:** []


---

## rag_v2_test_184

**QueryType:** no_answer
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** true
**NoAnswerScope:** in_domain_uncovered
**Query:** 伴侣戒毒康复后又出现复发迹象，我想保护共同生活和财务安全，但也不想变成全天候监控者，边界该怎么设？
**RelevantIDs:** []
**GradedRelevance:** {}
**HardNegativeIDs:** []


---

## rag_v2_test_185

**QueryType:** no_answer
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** true
**NoAnswerScope:** in_domain_uncovered
**Query:** 我们在双方同意下开始开放式关系，但伴侣和新对象变得很亲密后我出现强烈嫉妒，应该怎样重新谈规则和退出选择？
**RelevantIDs:** []
**GradedRelevance:** {}
**HardNegativeIDs:** []


---

## rag_v2_test_186

**QueryType:** no_answer
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, repair
**ExpectedRiskLevel:** normal
**NoAnswer:** true
**NoAnswerScope:** in_domain_uncovered
**Query:** 我和伴侣成为寄养家庭后，对孩子的依恋、管教方式和未来可能离开的现实看法不同，怎样处理我们之间的冲突？
**RelevantIDs:** []
**GradedRelevance:** {}
**HardNegativeIDs:** []


---

## rag_v2_test_187

**QueryType:** no_answer
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** true
**NoAnswerScope:** in_domain_uncovered
**Query:** 伴侣的移民身份由我的经济担保支持，他担心如果我们关系出问题就失去居留资格，我们该如何减少这种依赖造成的权力不平衡？
**RelevantIDs:** []
**GradedRelevance:** {}
**HardNegativeIDs:** []


---

## rag_v2_test_188

**QueryType:** no_answer
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** true
**NoAnswerScope:** in_domain_uncovered
**Query:** 多次不孕治疗失败后，我们开始讨论是否使用供精或供卵，但两个人对遗传关系和家庭告知的接受程度不同，该怎么谈？
**RelevantIDs:** []
**GradedRelevance:** {}
**HardNegativeIDs:** []


---

## rag_v2_test_189

**QueryType:** no_answer
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** true
**NoAnswerScope:** in_domain_uncovered
**Query:** 伴侣需要无障碍环境才能出行，我有时为了方便直接替她决定活动，她觉得被剥夺选择，我们应该如何调整相处方式？
**RelevantIDs:** []
**GradedRelevance:** {}
**HardNegativeIDs:** []


---

## rag_v2_test_190

**QueryType:** no_answer
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** true
**NoAnswerScope:** in_domain_uncovered
**Query:** 伴侣成年后刚确诊自闭谱系，我们以前常把他需要独处和不擅长即时表达理解成冷淡，现在应该怎么重新建立沟通规则？
**RelevantIDs:** []
**GradedRelevance:** {}
**HardNegativeIDs:** []


---

## rag_v2_test_191

**QueryType:** no_answer
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** true
**NoAnswerScope:** in_domain_uncovered
**Query:** 失去孩子以后，我和伴侣处理悲伤的方式完全不同，甚至开始互相回避，这种重大丧失之后关系应该如何相互支持？
**RelevantIDs:** []
**GradedRelevance:** {}
**HardNegativeIDs:** []


---

## rag_v2_test_192

**QueryType:** no_answer
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** true
**NoAnswerScope:** in_domain_uncovered
**Query:** 伴侣想把需要长期照护的父母接来和我们一起住，而我担心隐私、家务和经济压力，我们应该在同住前谈清哪些边界？
**RelevantIDs:** []
**GradedRelevance:** {}
**HardNegativeIDs:** []


---

## rag_v2_test_193

**QueryType:** no_answer
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** true
**NoAnswerScope:** in_domain_uncovered
**Query:** 伴侣需要长期担任成年残障家人的法定监护人，这会影响我们未来的居住和财务计划，我们怎样一起评估长期责任？
**RelevantIDs:** []
**GradedRelevance:** {}
**HardNegativeIDs:** []


---

## rag_v2_test_194

**QueryType:** no_answer
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** true
**NoAnswerScope:** in_domain_uncovered
**Query:** 伴侣患有慢性疲劳类疾病，精力每天波动很大，我很难安排共同活动，也怕把身体限制理解成不重视关系，该怎么协商？
**RelevantIDs:** []
**GradedRelevance:** {}
**HardNegativeIDs:** []


---

## rag_v2_test_195

**QueryType:** no_answer
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** true
**NoAnswerScope:** in_domain_uncovered
**Query:** 伴侣正在申请庇护，未来能否继续留在当前国家很不确定，我们想规划长期关系但很多现实条件都无法确定，该怎么讨论？
**RelevantIDs:** []
**GradedRelevance:** {}
**HardNegativeIDs:** []


---

## rag_v2_test_196

**QueryType:** no_answer
**Difficulty:** hard
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate
**ExpectedRiskLevel:** normal
**NoAnswer:** true
**NoAnswerScope:** in_domain_uncovered
**Query:** 我们在不孕治疗后考虑领养，但双方父母强烈反对，一边是我们自己的决定，一边是长期家庭关系压力，该怎么建立共同立场？
**RelevantIDs:** []
**GradedRelevance:** {}
**HardNegativeIDs:** []


---

## rag_v2_test_197

**QueryType:** no_answer
**Difficulty:** medium
**ExpectedBranch:** out_of_scope
**ExpectedPrimaryScenario:** null
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** unknown
**ExpectedGoals:** []
**ExpectedRiskLevel:** normal
**NoAnswer:** true
**NoAnswerScope:** out_of_domain
**Query:** 北京到上海高铁一般要多久？
**RelevantIDs:** []
**GradedRelevance:** {}
**HardNegativeIDs:** []


---

## rag_v2_test_198

**QueryType:** no_answer
**Difficulty:** medium
**ExpectedBranch:** out_of_scope
**ExpectedPrimaryScenario:** null
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** unknown
**ExpectedGoals:** []
**ExpectedRiskLevel:** normal
**NoAnswer:** true
**NoAnswerScope:** out_of_domain
**Query:** Java 的 HashMap 原理是什么？
**RelevantIDs:** []
**GradedRelevance:** {}
**HardNegativeIDs:** []


---

## rag_v2_test_199

**QueryType:** no_answer
**Difficulty:** medium
**ExpectedBranch:** out_of_scope
**ExpectedPrimaryScenario:** null
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** unknown
**ExpectedGoals:** []
**ExpectedRiskLevel:** normal
**NoAnswer:** true
**NoAnswerScope:** out_of_domain
**Query:** 如何把 CSV 文件读进 pandas？
**RelevantIDs:** []
**GradedRelevance:** {}
**HardNegativeIDs:** []


---

## rag_v2_test_200

**QueryType:** no_answer
**Difficulty:** medium
**ExpectedBranch:** out_of_scope
**ExpectedPrimaryScenario:** null
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** unknown
**ExpectedGoals:** []
**ExpectedRiskLevel:** normal
**NoAnswer:** true
**NoAnswerScope:** out_of_domain
**Query:** 我想做一个个人博客应该用什么框架？
**RelevantIDs:** []
**GradedRelevance:** {}
**HardNegativeIDs:** []

# LoveApp Multi-query Decomposition TEST

> Split: `test`
> Cases: **18**
> `NeedCoverageGroups` 是主 Gold：每个 Need 至少命中一个对应文档才算该信息需求被覆盖。最大 subquery 数为 3。

---

## multiquery_v1_test_001

**QueryType:** single_intent_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** dating
**ExpectedGoals:** understand, communicate
**Query:** 她会吃醋，但又一直强调只是朋友。 我现在主要想知道这一件事该怎么判断和处理。
**DecomposeRequired:** false
**ExpectedSubqueryCount:** 1
**ExpectedSubqueries:**
- Q1: 她会吃醋，但又一直强调只是朋友。
**RelevantIDs:** kb_v2_139
**NeedCoverageGroups:**
- Need1: kb_v2_139
**Notes:** 单一 information need，应保持一个 retrieval query。

---

## multiquery_v1_test_002

**QueryType:** single_intent_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**RelationshipStage:** long_distance
**ExpectedGoals:** communicate, repair
**Query:** 异地时我们对视频频率需求差很多。 我现在主要想知道这一件事该怎么判断和处理。
**DecomposeRequired:** false
**ExpectedSubqueryCount:** 1
**ExpectedSubqueries:**
- Q1: 异地时我们对视频频率需求差很多。
**RelevantIDs:** kb_v2_221
**NeedCoverageGroups:**
- Need1: kb_v2_221
**Notes:** 单一 information need，应保持一个 retrieval query。

---

## multiquery_v1_test_003

**QueryType:** single_intent_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** acquaintance
**ExpectedGoals:** understand
**Query:** 以前聊天表情很多，最近突然变少。 我现在主要想知道这一件事该怎么判断和处理。
**DecomposeRequired:** false
**ExpectedSubqueryCount:** 1
**ExpectedSubqueries:**
- Q1: 以前聊天表情很多，最近突然变少。
**RelevantIDs:** kb_v2_114
**NeedCoverageGroups:**
- Need1: kb_v2_114
**Notes:** 单一 information need，应保持一个 retrieval query。

---

## multiquery_v1_test_004

**QueryType:** single_intent_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**RelationshipStage:** dating
**ExpectedGoals:** communicate, set_boundary
**Query:** 约会花费谁承担，我们一直意见不同。 我现在主要想知道这一件事该怎么判断和处理。
**DecomposeRequired:** false
**ExpectedSubqueryCount:** 1
**ExpectedSubqueries:**
- Q1: 约会花费谁承担，我们一直意见不同。
**RelevantIDs:** kb_v2_226
**NeedCoverageGroups:**
- Need1: kb_v2_226
**Notes:** 单一 information need，应保持一个 retrieval query。

---

## multiquery_v1_test_005

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** dating
**ExpectedGoals:** understand, communicate, repair
**Query:** 我现在有两个问题想一起理清：第一，她会吃醋，但又一直强调只是朋友。 第二，异地时我们对视频频率需求差很多。 这两件事分别应该怎么判断和处理？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 她会吃醋，但又一直强调只是朋友。
- Q2: 异地时我们对视频频率需求差很多。
**RelevantIDs:** kb_v2_139, kb_v2_221
**NeedCoverageGroups:**
- Need1: kb_v2_139
- Need2: kb_v2_221
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_test_006

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** dating
**ExpectedGoals:** understand, communicate
**Query:** 她会吃醋，但又一直强调只是朋友。 另外还有一件事，以前聊天表情很多，最近突然变少。 我不想把两件事混在一起，能不能分别告诉我该关注什么？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 她会吃醋，但又一直强调只是朋友。
- Q2: 以前聊天表情很多，最近突然变少。
**RelevantIDs:** kb_v2_139, kb_v2_114
**NeedCoverageGroups:**
- Need1: kb_v2_139
- Need2: kb_v2_114
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_test_007

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** dating
**ExpectedGoals:** understand, communicate, set_boundary
**Query:** 最近一边是她会吃醋，但又一直强调只是朋友。 另一边又是约会花费谁承担，我们一直意见不同。 我感觉全搅在一起了，想分别知道下一步怎么做。
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 她会吃醋，但又一直强调只是朋友。
- Q2: 约会花费谁承担，我们一直意见不同。
**RelevantIDs:** kb_v2_139, kb_v2_226
**NeedCoverageGroups:**
- Need1: kb_v2_139
- Need2: kb_v2_226
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_test_008

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** dating
**ExpectedGoals:** understand, communicate, progress
**Query:** 我现在有两个问题想一起理清：第一，她会吃醋，但又一直强调只是朋友。 第二，我们都说目标一致，但一直很少真正推进。 这两件事分别应该怎么判断和处理？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 她会吃醋，但又一直强调只是朋友。
- Q2: 我们都说目标一致，但一直很少真正推进。
**RelevantIDs:** kb_v2_139, kb_v2_351
**NeedCoverageGroups:**
- Need1: kb_v2_139
- Need2: kb_v2_351
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_test_009

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** dating
**ExpectedGoals:** understand, communicate, progress
**Query:** 她会吃醋，但又一直强调只是朋友。 另外还有一件事，交流和见面都不错，但关系一直没有进一步发展。 我不想把两件事混在一起，能不能分别告诉我该关注什么？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 她会吃醋，但又一直强调只是朋友。
- Q2: 交流和见面都不错，但关系一直没有进一步发展。
**RelevantIDs:** kb_v2_139, kb_v2_062
**NeedCoverageGroups:**
- Need1: kb_v2_139
- Need2: kb_v2_062
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_test_010

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** dating
**ExpectedGoals:** understand, communicate, repair, set_boundary
**Query:** 最近一边是她会吃醋，但又一直强调只是朋友。 另一边又是对方家里人经常干预我们两个人的决定。 我感觉全搅在一起了，想分别知道下一步怎么做。
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 她会吃醋，但又一直强调只是朋友。
- Q2: 对方家里人经常干预我们两个人的决定。
**RelevantIDs:** kb_v2_139, kb_v2_235
**NeedCoverageGroups:**
- Need1: kb_v2_139
- Need2: kb_v2_235
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_test_011

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** dating
**ExpectedGoals:** understand, communicate, set_boundary
**Query:** 我现在有两个问题想一起理清：第一，她会吃醋，但又一直强调只是朋友。 第二，对象未经我明确同意翻看我的相册。 这两件事分别应该怎么判断和处理？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 她会吃醋，但又一直强调只是朋友。
- Q2: 对象未经我明确同意翻看我的相册。
**RelevantIDs:** kb_v2_139, kb_v2_361
**NeedCoverageGroups:**
- Need1: kb_v2_139
- Need2: kb_v2_361
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_test_012

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** dating
**ExpectedGoals:** understand, communicate, progress
**Query:** 她会吃醋，但又一直强调只是朋友。 另外还有一件事，异地多久结束没有明确计划。 我不想把两件事混在一起，能不能分别告诉我该关注什么？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 她会吃醋，但又一直强调只是朋友。
- Q2: 异地多久结束没有明确计划。
**RelevantIDs:** kb_v2_139, kb_v2_314
**NeedCoverageGroups:**
- Need1: kb_v2_139
- Need2: kb_v2_314
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_test_013

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** dating
**ExpectedGoals:** understand, communicate, set_boundary
**Query:** 最近一边是她会吃醋，但又一直强调只是朋友。 另一边又是周末我想留半天只做自己的事情。 我感觉全搅在一起了，想分别知道下一步怎么做。
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 她会吃醋，但又一直强调只是朋友。
- Q2: 周末我想留半天只做自己的事情。
**RelevantIDs:** kb_v2_139, kb_v2_282
**NeedCoverageGroups:**
- Need1: kb_v2_139
- Need2: kb_v2_282
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_test_014

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** dating
**ExpectedGoals:** understand, communicate, set_boundary
**Query:** 我现在有两个问题想一起理清：第一，她会吃醋，但又一直强调只是朋友。 第二，对象希望知道我所有社交软件密码。 这两件事分别应该怎么判断和处理？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 她会吃醋，但又一直强调只是朋友。
- Q2: 对象希望知道我所有社交软件密码。
**RelevantIDs:** kb_v2_139, kb_v2_364
**NeedCoverageGroups:**
- Need1: kb_v2_139
- Need2: kb_v2_364
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_test_015

**QueryType:** three_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** dating
**ExpectedGoals:** understand, communicate, repair
**Query:** 我现在其实卡在三件事上：一是她会吃醋，但又一直强调只是朋友。 二是异地时我们对视频频率需求差很多。 三是以前聊天表情很多，最近突然变少。 这三个问题分别应该怎么判断，处理顺序怎么排？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 3
**ExpectedSubqueries:**
- Q1: 她会吃醋，但又一直强调只是朋友。
- Q2: 异地时我们对视频频率需求差很多。
- Q3: 以前聊天表情很多，最近突然变少。
**RelevantIDs:** kb_v2_139, kb_v2_221, kb_v2_114
**NeedCoverageGroups:**
- Need1: kb_v2_139
- Need2: kb_v2_221
- Need3: kb_v2_114
**Notes:** 最多拆 3 个 subquery，不递归拆分。

---

## multiquery_v1_test_016

**QueryType:** three_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** dating
**ExpectedGoals:** understand, communicate, repair, set_boundary
**Query:** 事情有点多：她会吃醋，但又一直强调只是朋友。；同时异地时我们对视频频率需求差很多。；另外约会花费谁承担，我们一直意见不同。。我想把这三个问题拆开处理，不知道每个问题该看什么。
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 3
**ExpectedSubqueries:**
- Q1: 她会吃醋，但又一直强调只是朋友。
- Q2: 异地时我们对视频频率需求差很多。
- Q3: 约会花费谁承担，我们一直意见不同。
**RelevantIDs:** kb_v2_139, kb_v2_221, kb_v2_226
**NeedCoverageGroups:**
- Need1: kb_v2_139
- Need2: kb_v2_221
- Need3: kb_v2_226
**Notes:** 最多拆 3 个 subquery，不递归拆分。

---

## multiquery_v1_test_017

**QueryType:** three_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** dating
**ExpectedGoals:** understand, communicate, repair, progress
**Query:** 我现在其实卡在三件事上：一是她会吃醋，但又一直强调只是朋友。 二是异地时我们对视频频率需求差很多。 三是我们都说目标一致，但一直很少真正推进。 这三个问题分别应该怎么判断，处理顺序怎么排？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 3
**ExpectedSubqueries:**
- Q1: 她会吃醋，但又一直强调只是朋友。
- Q2: 异地时我们对视频频率需求差很多。
- Q3: 我们都说目标一致，但一直很少真正推进。
**RelevantIDs:** kb_v2_139, kb_v2_221, kb_v2_351
**NeedCoverageGroups:**
- Need1: kb_v2_139
- Need2: kb_v2_221
- Need3: kb_v2_351
**Notes:** 最多拆 3 个 subquery，不递归拆分。

---

## multiquery_v1_test_018

**QueryType:** three_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** dating
**ExpectedGoals:** understand, communicate, repair, progress
**Query:** 事情有点多：她会吃醋，但又一直强调只是朋友。；同时异地时我们对视频频率需求差很多。；另外交流和见面都不错，但关系一直没有进一步发展。。我想把这三个问题拆开处理，不知道每个问题该看什么。
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 3
**ExpectedSubqueries:**
- Q1: 她会吃醋，但又一直强调只是朋友。
- Q2: 异地时我们对视频频率需求差很多。
- Q3: 交流和见面都不错，但关系一直没有进一步发展。
**RelevantIDs:** kb_v2_139, kb_v2_221, kb_v2_062
**NeedCoverageGroups:**
- Need1: kb_v2_139
- Need2: kb_v2_221
- Need3: kb_v2_062
**Notes:** 最多拆 3 个 subquery，不递归拆分。

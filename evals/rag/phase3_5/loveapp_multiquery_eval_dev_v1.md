# LoveApp Multi-query Decomposition DEV

> Split: `dev`
> Cases: **36**
> `NeedCoverageGroups` 是主 Gold：每个 Need 至少命中一个对应文档才算该信息需求被覆盖。最大 subquery 数为 3。

---

## multiquery_v1_dev_001

**QueryType:** single_intent_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress
**Query:** 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 我现在主要想知道这一件事该怎么判断和处理。
**DecomposeRequired:** false
**ExpectedSubqueryCount:** 1
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
**RelevantIDs:** kb_v2_325
**NeedCoverageGroups:**
- Need1: kb_v2_325
**Notes:** 单一 information need，应保持一个 retrieval query。

---

## multiquery_v1_dev_002

**QueryType:** single_intent_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**RelationshipStage:** stable_relationship
**ExpectedGoals:** repair, communicate
**Query:** 她情绪上来我也会立刻反击，每次都越吵越大。 我现在主要想知道这一件事该怎么判断和处理。
**DecomposeRequired:** false
**ExpectedSubqueryCount:** 1
**ExpectedSubqueries:**
- Q1: 她情绪上来我也会立刻反击，每次都越吵越大。
**RelevantIDs:** kb_v2_168
**NeedCoverageGroups:**
- Need1: kb_v2_168
**Notes:** 单一 information need，应保持一个 retrieval query。

---

## multiquery_v1_dev_003

**QueryType:** single_intent_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**RelationshipStage:** breakup
**ExpectedGoals:** understand
**Query:** 分手后朋友都劝我放下，但我就是一直做不到。 我现在主要想知道这一件事该怎么判断和处理。
**DecomposeRequired:** false
**ExpectedSubqueryCount:** 1
**ExpectedSubqueries:**
- Q1: 分手后朋友都劝我放下，但我就是一直做不到。
**RelevantIDs:** kb_v2_478
**NeedCoverageGroups:**
- Need1: kb_v2_478
**Notes:** 单一 information need，应保持一个 retrieval query。

---

## multiquery_v1_dev_004

**QueryType:** single_intent_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**Query:** 对方想马上公开恋情，我还没准备好。 我现在主要想知道这一件事该怎么判断和处理。
**DecomposeRequired:** false
**ExpectedSubqueryCount:** 1
**ExpectedSubqueries:**
- Q1: 对方想马上公开恋情，我还没准备好。
**RelevantIDs:** kb_v2_401
**NeedCoverageGroups:**
- Need1: kb_v2_401
**Notes:** 单一 information need，应保持一个 retrieval query。

---

## multiquery_v1_dev_005

**QueryType:** single_intent_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**RelationshipStage:** acquaintance
**ExpectedGoals:** initiate, communicate
**Query:** 我们聊天总从“在吗”开始，很难把话题继续下去。 我现在主要想知道这一件事该怎么判断和处理。
**DecomposeRequired:** false
**ExpectedSubqueryCount:** 1
**ExpectedSubqueries:**
- Q1: 我们聊天总从“在吗”开始，很难把话题继续下去。
**RelevantIDs:** kb_v2_019
**NeedCoverageGroups:**
- Need1: kb_v2_019
**Notes:** 单一 information need，应保持一个 retrieval query。

---

## multiquery_v1_dev_006

**QueryType:** single_intent_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**RelationshipStage:** breakup
**ExpectedGoals:** end_relationship, communicate
**Query:** 我已经说想结束，但对方一直当成只是暂时冷静。 我现在主要想知道这一件事该怎么判断和处理。
**DecomposeRequired:** false
**ExpectedSubqueryCount:** 1
**ExpectedSubqueries:**
- Q1: 我已经说想结束，但对方一直当成只是暂时冷静。
**RelevantIDs:** kb_v2_454
**NeedCoverageGroups:**
- Need1: kb_v2_454
**Notes:** 单一 information need，应保持一个 retrieval query。

---

## multiquery_v1_dev_007

**QueryType:** single_intent_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate
**Query:** 我喜欢文字，他更喜欢语音，我们经常因为回复方式觉得对方敷衍。 我现在主要想知道这一件事该怎么判断和处理。
**DecomposeRequired:** false
**ExpectedSubqueryCount:** 1
**ExpectedSubqueries:**
- Q1: 我喜欢文字，他更喜欢语音，我们经常因为回复方式觉得对方敷衍。
**RelevantIDs:** kb_v2_267
**NeedCoverageGroups:**
- Need1: kb_v2_267
**Notes:** 单一 information need，应保持一个 retrieval query。

---

## multiquery_v1_dev_008

**QueryType:** single_intent_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**Query:** 我们有共同开销，但我还是想保留个人财务自主。 我现在主要想知道这一件事该怎么判断和处理。
**DecomposeRequired:** false
**ExpectedSubqueryCount:** 1
**ExpectedSubqueries:**
- Q1: 我们有共同开销，但我还是想保留个人财务自主。
**RelevantIDs:** kb_v2_399
**NeedCoverageGroups:**
- Need1: kb_v2_399
**Notes:** 单一 information need，应保持一个 retrieval query。

---

## multiquery_v1_dev_009

**QueryType:** single_intent_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** acquaintance
**ExpectedGoals:** understand
**Query:** 她会认真反问很多问题，但从来不主动开场。 我现在主要想知道这一件事该怎么判断和处理。
**DecomposeRequired:** false
**ExpectedSubqueryCount:** 1
**ExpectedSubqueries:**
- Q1: 她会认真反问很多问题，但从来不主动开场。
**RelevantIDs:** kb_v2_097
**NeedCoverageGroups:**
- Need1: kb_v2_097
**Notes:** 单一 information need，应保持一个 retrieval query。

---

## multiquery_v1_dev_010

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, repair
**Query:** 我现在有两个问题想一起理清：第一，我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 第二，她情绪上来我也会立刻反击，每次都越吵越大。 这两件事分别应该怎么判断和处理？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 她情绪上来我也会立刻反击，每次都越吵越大。
**RelevantIDs:** kb_v2_325, kb_v2_168
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_168
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_dev_011

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, understand
**Query:** 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 另外还有一件事，分手后朋友都劝我放下，但我就是一直做不到。 我不想把两件事混在一起，能不能分别告诉我该关注什么？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 分手后朋友都劝我放下，但我就是一直做不到。
**RelevantIDs:** kb_v2_325, kb_v2_478
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_478
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_dev_012

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, set_boundary
**Query:** 最近一边是我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 另一边又是对方想马上公开恋情，我还没准备好。 我感觉全搅在一起了，想分别知道下一步怎么做。
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 对方想马上公开恋情，我还没准备好。
**RelevantIDs:** kb_v2_325, kb_v2_401
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_401
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_dev_013

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, initiate
**Query:** 我现在有两个问题想一起理清：第一，我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 第二，我们聊天总从“在吗”开始，很难把话题继续下去。 这两件事分别应该怎么判断和处理？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 我们聊天总从“在吗”开始，很难把话题继续下去。
**RelevantIDs:** kb_v2_325, kb_v2_019
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_019
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_dev_014

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, end_relationship
**Query:** 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 另外还有一件事，我已经说想结束，但对方一直当成只是暂时冷静。 我不想把两件事混在一起，能不能分别告诉我该关注什么？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 我已经说想结束，但对方一直当成只是暂时冷静。
**RelevantIDs:** kb_v2_325, kb_v2_454
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_454
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_dev_015

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress
**Query:** 最近一边是我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 另一边又是我喜欢文字，他更喜欢语音，我们经常因为回复方式觉得对方敷衍。 我感觉全搅在一起了，想分别知道下一步怎么做。
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 我喜欢文字，他更喜欢语音，我们经常因为回复方式觉得对方敷衍。
**RelevantIDs:** kb_v2_325, kb_v2_267
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_267
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_dev_016

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, set_boundary
**Query:** 我现在有两个问题想一起理清：第一，我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 第二，我们有共同开销，但我还是想保留个人财务自主。 这两件事分别应该怎么判断和处理？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 我们有共同开销，但我还是想保留个人财务自主。
**RelevantIDs:** kb_v2_325, kb_v2_399
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_399
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_dev_017

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, understand
**Query:** 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 另外还有一件事，她会认真反问很多问题，但从来不主动开场。 我不想把两件事混在一起，能不能分别告诉我该关注什么？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 她会认真反问很多问题，但从来不主动开场。
**RelevantIDs:** kb_v2_325, kb_v2_097
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_097
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_dev_018

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, set_boundary, end_relationship
**Query:** 最近一边是我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 另一边又是分手后还有很多共同账户和服务绑定在一起。 我感觉全搅在一起了，想分别知道下一步怎么做。
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 分手后还有很多共同账户和服务绑定在一起。
**RelevantIDs:** kb_v2_325, kb_v2_491
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_491
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_dev_019

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, set_boundary
**Query:** 我现在有两个问题想一起理清：第一，我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 第二，对象要求睡前必须通话，我最近越来越有压力。 这两件事分别应该怎么判断和处理？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 对象要求睡前必须通话，我最近越来越有压力。
**RelevantIDs:** kb_v2_325, kb_v2_368
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_368
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_dev_020

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress
**Query:** 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 另外还有一件事，他开始了一个很投入的新兴趣，我支持但也觉得被冷落。 我不想把两件事混在一起，能不能分别告诉我该关注什么？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 他开始了一个很投入的新兴趣，我支持但也觉得被冷落。
**RelevantIDs:** kb_v2_325, kb_v2_326
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_326
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_dev_021

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, set_boundary
**Query:** 最近一边是我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 另一边又是我想确认亲吻之前怎样问对方意愿才自然。 我感觉全搅在一起了，想分别知道下一步怎么做。
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 我想确认亲吻之前怎样问对方意愿才自然。
**RelevantIDs:** kb_v2_325, kb_v2_383
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_383
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_dev_022

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, initiate
**Query:** 我现在有两个问题想一起理清：第一，我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 第二，我们在兴趣社群认识，现在想从群聊转私聊。 这两件事分别应该怎么判断和处理？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 我们在兴趣社群认识，现在想从群聊转私聊。
**RelevantIDs:** kb_v2_325, kb_v2_004
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_004
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_dev_023

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress
**Query:** 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 另外还有一件事，我们都很忙，但还是希望在关系里有被重视的感觉。 我不想把两件事混在一起，能不能分别告诉我该关注什么？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 我们都很忙，但还是希望在关系里有被重视的感觉。
**RelevantIDs:** kb_v2_325, kb_v2_280
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_280
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_dev_024

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, understand
**Query:** 最近一边是我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 另一边又是她以前回复很快，最近频率明显下降。 我感觉全搅在一起了，想分别知道下一步怎么做。
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 她以前回复很快，最近频率明显下降。
**RelevantIDs:** kb_v2_325, kb_v2_092
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_092
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_dev_025

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, set_boundary
**Query:** 我现在有两个问题想一起理清：第一，我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 第二，对象觉得恋爱后应该一直共享位置。 这两件事分别应该怎么判断和处理？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 对象觉得恋爱后应该一直共享位置。
**RelevantIDs:** kb_v2_325, kb_v2_358
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_358
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_dev_026

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, understand
**Query:** 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 另外还有一件事，这段关系没明显大问题，但我越来越麻木。 我不想把两件事混在一起，能不能分别告诉我该关注什么？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 这段关系没明显大问题，但我越来越麻木。
**RelevantIDs:** kb_v2_325, kb_v2_352
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_352
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_dev_027

**QueryType:** two_intents
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, set_boundary
**Query:** 最近一边是我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 另一边又是我想保留一部分和对象不重叠的朋友圈。 我感觉全搅在一起了，想分别知道下一步怎么做。
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 2
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 我想保留一部分和对象不重叠的朋友圈。
**RelevantIDs:** kb_v2_325, kb_v2_380
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_380
**Notes:** 两个独立 information needs；分别检索后 merge/dedup，再统一 rerank。

---

## multiquery_v1_dev_028

**QueryType:** three_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, repair, understand
**Query:** 我现在其实卡在三件事上：一是我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 二是她情绪上来我也会立刻反击，每次都越吵越大。 三是分手后朋友都劝我放下，但我就是一直做不到。 这三个问题分别应该怎么判断，处理顺序怎么排？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 3
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 她情绪上来我也会立刻反击，每次都越吵越大。
- Q3: 分手后朋友都劝我放下，但我就是一直做不到。
**RelevantIDs:** kb_v2_325, kb_v2_168, kb_v2_478
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_168
- Need3: kb_v2_478
**Notes:** 最多拆 3 个 subquery，不递归拆分。

---

## multiquery_v1_dev_029

**QueryType:** three_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, repair, set_boundary
**Query:** 事情有点多：我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。；同时她情绪上来我也会立刻反击，每次都越吵越大。；另外对方想马上公开恋情，我还没准备好。。我想把这三个问题拆开处理，不知道每个问题该看什么。
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 3
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 她情绪上来我也会立刻反击，每次都越吵越大。
- Q3: 对方想马上公开恋情，我还没准备好。
**RelevantIDs:** kb_v2_325, kb_v2_168, kb_v2_401
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_168
- Need3: kb_v2_401
**Notes:** 最多拆 3 个 subquery，不递归拆分。

---

## multiquery_v1_dev_030

**QueryType:** three_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, repair, initiate
**Query:** 我现在其实卡在三件事上：一是我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 二是她情绪上来我也会立刻反击，每次都越吵越大。 三是我们聊天总从“在吗”开始，很难把话题继续下去。 这三个问题分别应该怎么判断，处理顺序怎么排？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 3
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 她情绪上来我也会立刻反击，每次都越吵越大。
- Q3: 我们聊天总从“在吗”开始，很难把话题继续下去。
**RelevantIDs:** kb_v2_325, kb_v2_168, kb_v2_019
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_168
- Need3: kb_v2_019
**Notes:** 最多拆 3 个 subquery，不递归拆分。

---

## multiquery_v1_dev_031

**QueryType:** three_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, repair, end_relationship
**Query:** 事情有点多：我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。；同时她情绪上来我也会立刻反击，每次都越吵越大。；另外我已经说想结束，但对方一直当成只是暂时冷静。。我想把这三个问题拆开处理，不知道每个问题该看什么。
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 3
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 她情绪上来我也会立刻反击，每次都越吵越大。
- Q3: 我已经说想结束，但对方一直当成只是暂时冷静。
**RelevantIDs:** kb_v2_325, kb_v2_168, kb_v2_454
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_168
- Need3: kb_v2_454
**Notes:** 最多拆 3 个 subquery，不递归拆分。

---

## multiquery_v1_dev_032

**QueryType:** three_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, repair
**Query:** 我现在其实卡在三件事上：一是我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 二是她情绪上来我也会立刻反击，每次都越吵越大。 三是我喜欢文字，他更喜欢语音，我们经常因为回复方式觉得对方敷衍。 这三个问题分别应该怎么判断，处理顺序怎么排？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 3
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 她情绪上来我也会立刻反击，每次都越吵越大。
- Q3: 我喜欢文字，他更喜欢语音，我们经常因为回复方式觉得对方敷衍。
**RelevantIDs:** kb_v2_325, kb_v2_168, kb_v2_267
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_168
- Need3: kb_v2_267
**Notes:** 最多拆 3 个 subquery，不递归拆分。

---

## multiquery_v1_dev_033

**QueryType:** three_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, repair, set_boundary
**Query:** 事情有点多：我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。；同时她情绪上来我也会立刻反击，每次都越吵越大。；另外我们有共同开销，但我还是想保留个人财务自主。。我想把这三个问题拆开处理，不知道每个问题该看什么。
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 3
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 她情绪上来我也会立刻反击，每次都越吵越大。
- Q3: 我们有共同开销，但我还是想保留个人财务自主。
**RelevantIDs:** kb_v2_325, kb_v2_168, kb_v2_399
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_168
- Need3: kb_v2_399
**Notes:** 最多拆 3 个 subquery，不递归拆分。

---

## multiquery_v1_dev_034

**QueryType:** three_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, repair, understand
**Query:** 我现在其实卡在三件事上：一是我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 二是她情绪上来我也会立刻反击，每次都越吵越大。 三是她会认真反问很多问题，但从来不主动开场。 这三个问题分别应该怎么判断，处理顺序怎么排？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 3
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 她情绪上来我也会立刻反击，每次都越吵越大。
- Q3: 她会认真反问很多问题，但从来不主动开场。
**RelevantIDs:** kb_v2_325, kb_v2_168, kb_v2_097
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_168
- Need3: kb_v2_097
**Notes:** 最多拆 3 个 subquery，不递归拆分。

---

## multiquery_v1_dev_035

**QueryType:** three_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, repair, set_boundary, end_relationship
**Query:** 事情有点多：我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。；同时她情绪上来我也会立刻反击，每次都越吵越大。；另外分手后还有很多共同账户和服务绑定在一起。。我想把这三个问题拆开处理，不知道每个问题该看什么。
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 3
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 她情绪上来我也会立刻反击，每次都越吵越大。
- Q3: 分手后还有很多共同账户和服务绑定在一起。
**RelevantIDs:** kb_v2_325, kb_v2_168, kb_v2_491
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_168
- Need3: kb_v2_491
**Notes:** 最多拆 3 个 subquery，不递归拆分。

---

## multiquery_v1_dev_036

**QueryType:** three_intents
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress, repair, set_boundary
**Query:** 我现在其实卡在三件事上：一是我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 二是她情绪上来我也会立刻反击，每次都越吵越大。 三是对象要求睡前必须通话，我最近越来越有压力。 这三个问题分别应该怎么判断，处理顺序怎么排？
**DecomposeRequired:** true
**ExpectedSubqueryCount:** 3
**ExpectedSubqueries:**
- Q1: 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- Q2: 她情绪上来我也会立刻反击，每次都越吵越大。
- Q3: 对象要求睡前必须通话，我最近越来越有压力。
**RelevantIDs:** kb_v2_325, kb_v2_168, kb_v2_368
**NeedCoverageGroups:**
- Need1: kb_v2_325
- Need2: kb_v2_168
- Need3: kb_v2_368
**Notes:** 最多拆 3 个 subquery，不递归拆分。

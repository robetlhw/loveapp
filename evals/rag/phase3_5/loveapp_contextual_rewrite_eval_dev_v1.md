# LoveApp Conditional Contextual Rewrite DEV

> Split: `dev`
> Cases: **36**
> 用于测试 Conditional Contextual Query Rewrite。`RelevantIDs` 引用现有 500-KB，用于比较 Raw Query 与 Rewritten Query 的真实检索收益。

---

## context_v1_dev_001

**QueryType:** context_dependent
**Difficulty:** hard
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress
**History:**
- 用户上一轮：我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 那我现在到底该怎么办？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 我现在到底该怎么办？
**RelevantIDs:** kb_v2_325
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_002

**QueryType:** standalone_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress
**History:**
- 用户上一轮：最近工作挺忙的。
- 助手上一轮：如果你愿意，可以说说具体发生了什么。
**CurrentQuery:** 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 我现在想知道应该怎么判断和处理，最好先做什么？
**RewriteRequired:** false
**ExpectedStandaloneQuery:** 我们职业发展速度差很多，我最近开始有落差感，也怕把比较带进关系。 我现在想知道应该怎么判断和处理，最好先做什么？
**RelevantIDs:** kb_v2_325
**Notes:** 完整 standalone query，应 passthrough；不得因历史存在就强制改写。

---

## context_v1_dev_003

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**RelationshipStage:** stable_relationship
**ExpectedGoals:** repair, communicate
**History:**
- 用户上一轮：她情绪上来我也会立刻反击，每次都越吵越大。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 这种情况我还要继续吗？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 她情绪上来我也会立刻反击，每次都越吵越大。 在这种情况下我还要继续吗？
**RelevantIDs:** kb_v2_168
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_004

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**RelationshipStage:** breakup
**ExpectedGoals:** understand
**History:**
- 用户上一轮：分手后朋友都劝我放下，但我就是一直做不到。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 那我要怎么跟ta说比较好？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 分手后朋友都劝我放下，但我就是一直做不到。 我要怎么跟ta说比较好？
**RelevantIDs:** kb_v2_478
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_005

**QueryType:** standalone_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**RelationshipStage:** breakup
**ExpectedGoals:** understand
**History:**
- 用户上一轮：最近工作挺忙的。
- 助手上一轮：如果你愿意，可以说说具体发生了什么。
**CurrentQuery:** 分手后朋友都劝我放下，但我就是一直做不到。 我现在想知道应该怎么判断和处理，最好先做什么？
**RewriteRequired:** false
**ExpectedStandaloneQuery:** 分手后朋友都劝我放下，但我就是一直做不到。 我现在想知道应该怎么判断和处理，最好先做什么？
**RelevantIDs:** kb_v2_478
**Notes:** 完整 standalone query，应 passthrough；不得因历史存在就强制改写。

---

## context_v1_dev_006

**QueryType:** context_dependent
**Difficulty:** hard
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**History:**
- 用户上一轮：对方想马上公开恋情，我还没准备好。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 所以这个变化一般说明什么？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 对方想马上公开恋情，我还没准备好。 所以这个变化一般说明什么？
**RelevantIDs:** kb_v2_401
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_007

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**RelationshipStage:** acquaintance
**ExpectedGoals:** initiate, communicate
**History:**
- 用户上一轮：我们聊天总从“在吗”开始，很难把话题继续下去。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 那我是不是先别主动了？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 我们聊天总从“在吗”开始，很难把话题继续下去。 我是不是先别主动了？
**RelevantIDs:** kb_v2_019
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_008

**QueryType:** standalone_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**RelationshipStage:** acquaintance
**ExpectedGoals:** initiate, communicate
**History:**
- 用户上一轮：最近工作挺忙的。
- 助手上一轮：如果你愿意，可以说说具体发生了什么。
**CurrentQuery:** 我们聊天总从“在吗”开始，很难把话题继续下去。 我现在想知道应该怎么判断和处理，最好先做什么？
**RewriteRequired:** false
**ExpectedStandaloneQuery:** 我们聊天总从“在吗”开始，很难把话题继续下去。 我现在想知道应该怎么判断和处理，最好先做什么？
**RelevantIDs:** kb_v2_019
**Notes:** 完整 standalone query，应 passthrough；不得因历史存在就强制改写。

---

## context_v1_dev_009

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**RelationshipStage:** breakup
**ExpectedGoals:** end_relationship, communicate
**History:**
- 用户上一轮：我已经说想结束，但对方一直当成只是暂时冷静。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 这种时候下一步怎么做比较稳？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 我已经说想结束，但对方一直当成只是暂时冷静。 这种时候下一步怎么做比较稳？
**RelevantIDs:** kb_v2_454
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_010

**QueryType:** context_dependent
**Difficulty:** hard
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate
**History:**
- 用户上一轮：我喜欢文字，他更喜欢语音，我们经常因为回复方式觉得对方敷衍。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 那我现在到底该怎么办？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 我喜欢文字，他更喜欢语音，我们经常因为回复方式觉得对方敷衍。 我现在到底该怎么办？
**RelevantIDs:** kb_v2_267
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_011

**QueryType:** standalone_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate
**History:**
- 用户上一轮：最近工作挺忙的。
- 助手上一轮：如果你愿意，可以说说具体发生了什么。
**CurrentQuery:** 我喜欢文字，他更喜欢语音，我们经常因为回复方式觉得对方敷衍。 我现在想知道应该怎么判断和处理，最好先做什么？
**RewriteRequired:** false
**ExpectedStandaloneQuery:** 我喜欢文字，他更喜欢语音，我们经常因为回复方式觉得对方敷衍。 我现在想知道应该怎么判断和处理，最好先做什么？
**RelevantIDs:** kb_v2_267
**Notes:** 完整 standalone query，应 passthrough；不得因历史存在就强制改写。

---

## context_v1_dev_012

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**History:**
- 用户上一轮：我们有共同开销，但我还是想保留个人财务自主。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 这种情况我还要继续吗？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 我们有共同开销，但我还是想保留个人财务自主。 在这种情况下我还要继续吗？
**RelevantIDs:** kb_v2_399
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_013

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** acquaintance
**ExpectedGoals:** understand
**History:**
- 用户上一轮：她会认真反问很多问题，但从来不主动开场。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 那我要怎么跟ta说比较好？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 她会认真反问很多问题，但从来不主动开场。 我要怎么跟ta说比较好？
**RelevantIDs:** kb_v2_097
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_014

**QueryType:** standalone_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** acquaintance
**ExpectedGoals:** understand
**History:**
- 用户上一轮：最近工作挺忙的。
- 助手上一轮：如果你愿意，可以说说具体发生了什么。
**CurrentQuery:** 她会认真反问很多问题，但从来不主动开场。 我现在想知道应该怎么判断和处理，最好先做什么？
**RewriteRequired:** false
**ExpectedStandaloneQuery:** 她会认真反问很多问题，但从来不主动开场。 我现在想知道应该怎么判断和处理，最好先做什么？
**RelevantIDs:** kb_v2_097
**Notes:** 完整 standalone query，应 passthrough；不得因历史存在就强制改写。

---

## context_v1_dev_015

**QueryType:** context_dependent
**Difficulty:** hard
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, end_relationship
**History:**
- 用户上一轮：分手后还有很多共同账户和服务绑定在一起。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 所以这个变化一般说明什么？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 分手后还有很多共同账户和服务绑定在一起。 所以这个变化一般说明什么？
**RelevantIDs:** kb_v2_491
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_016

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**History:**
- 用户上一轮：对象要求睡前必须通话，我最近越来越有压力。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 那我是不是先别主动了？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 对象要求睡前必须通话，我最近越来越有压力。 我是不是先别主动了？
**RelevantIDs:** kb_v2_368
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_017

**QueryType:** standalone_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**History:**
- 用户上一轮：最近工作挺忙的。
- 助手上一轮：如果你愿意，可以说说具体发生了什么。
**CurrentQuery:** 对象要求睡前必须通话，我最近越来越有压力。 我现在想知道应该怎么判断和处理，最好先做什么？
**RewriteRequired:** false
**ExpectedStandaloneQuery:** 对象要求睡前必须通话，我最近越来越有压力。 我现在想知道应该怎么判断和处理，最好先做什么？
**RelevantIDs:** kb_v2_368
**Notes:** 完整 standalone query，应 passthrough；不得因历史存在就强制改写。

---

## context_v1_dev_018

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress
**History:**
- 用户上一轮：他开始了一个很投入的新兴趣，我支持但也觉得被冷落。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 这种时候下一步怎么做比较稳？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 他开始了一个很投入的新兴趣，我支持但也觉得被冷落。 这种时候下一步怎么做比较稳？
**RelevantIDs:** kb_v2_326
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_019

**QueryType:** context_dependent
**Difficulty:** hard
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**History:**
- 用户上一轮：我想确认亲吻之前怎样问对方意愿才自然。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 那我现在到底该怎么办？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 我想确认亲吻之前怎样问对方意愿才自然。 我现在到底该怎么办？
**RelevantIDs:** kb_v2_383
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_020

**QueryType:** standalone_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**History:**
- 用户上一轮：最近工作挺忙的。
- 助手上一轮：如果你愿意，可以说说具体发生了什么。
**CurrentQuery:** 我想确认亲吻之前怎样问对方意愿才自然。 我现在想知道应该怎么判断和处理，最好先做什么？
**RewriteRequired:** false
**ExpectedStandaloneQuery:** 我想确认亲吻之前怎样问对方意愿才自然。 我现在想知道应该怎么判断和处理，最好先做什么？
**RelevantIDs:** kb_v2_383
**Notes:** 完整 standalone query，应 passthrough；不得因历史存在就强制改写。

---

## context_v1_dev_021

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**RelationshipStage:** acquaintance
**ExpectedGoals:** initiate, communicate
**History:**
- 用户上一轮：我们在兴趣社群认识，现在想从群聊转私聊。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 这种情况我还要继续吗？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 我们在兴趣社群认识，现在想从群聊转私聊。 在这种情况下我还要继续吗？
**RelevantIDs:** kb_v2_004
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_022

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** dating
**ExpectedGoals:** communicate
**History:**
- 用户上一轮：我们都很忙，但还是希望在关系里有被重视的感觉。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 那我要怎么跟ta说比较好？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 我们都很忙，但还是希望在关系里有被重视的感觉。 我要怎么跟ta说比较好？
**RelevantIDs:** kb_v2_280
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_023

**QueryType:** standalone_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** dating
**ExpectedGoals:** communicate
**History:**
- 用户上一轮：最近工作挺忙的。
- 助手上一轮：如果你愿意，可以说说具体发生了什么。
**CurrentQuery:** 我们都很忙，但还是希望在关系里有被重视的感觉。 我现在想知道应该怎么判断和处理，最好先做什么？
**RewriteRequired:** false
**ExpectedStandaloneQuery:** 我们都很忙，但还是希望在关系里有被重视的感觉。 我现在想知道应该怎么判断和处理，最好先做什么？
**RelevantIDs:** kb_v2_280
**Notes:** 完整 standalone query，应 passthrough；不得因历史存在就强制改写。

---

## context_v1_dev_024

**QueryType:** context_dependent
**Difficulty:** hard
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** acquaintance
**ExpectedGoals:** understand
**History:**
- 用户上一轮：她以前回复很快，最近频率明显下降。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 所以这个变化一般说明什么？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 她以前回复很快，最近频率明显下降。 所以这个变化一般说明什么？
**RelevantIDs:** kb_v2_092
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_025

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**History:**
- 用户上一轮：对象觉得恋爱后应该一直共享位置。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 那我是不是先别主动了？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 对象觉得恋爱后应该一直共享位置。 我是不是先别主动了？
**RelevantIDs:** kb_v2_358
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_026

**QueryType:** standalone_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**History:**
- 用户上一轮：最近工作挺忙的。
- 助手上一轮：如果你愿意，可以说说具体发生了什么。
**CurrentQuery:** 对象觉得恋爱后应该一直共享位置。 我现在想知道应该怎么判断和处理，最好先做什么？
**RewriteRequired:** false
**ExpectedStandaloneQuery:** 对象觉得恋爱后应该一直共享位置。 我现在想知道应该怎么判断和处理，最好先做什么？
**RelevantIDs:** kb_v2_358
**Notes:** 完整 standalone query，应 passthrough；不得因历史存在就强制改写。

---

## context_v1_dev_027

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** understand, communicate
**History:**
- 用户上一轮：这段关系没明显大问题，但我越来越麻木。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 这种时候下一步怎么做比较稳？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 这段关系没明显大问题，但我越来越麻木。 这种时候下一步怎么做比较稳？
**RelevantIDs:** kb_v2_352
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_028

**QueryType:** context_dependent
**Difficulty:** hard
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**History:**
- 用户上一轮：我想保留一部分和对象不重叠的朋友圈。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 那我现在到底该怎么办？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 我想保留一部分和对象不重叠的朋友圈。 我现在到底该怎么办？
**RelevantIDs:** kb_v2_380
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_029

**QueryType:** standalone_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**History:**
- 用户上一轮：最近工作挺忙的。
- 助手上一轮：如果你愿意，可以说说具体发生了什么。
**CurrentQuery:** 我想保留一部分和对象不重叠的朋友圈。 我现在想知道应该怎么判断和处理，最好先做什么？
**RewriteRequired:** false
**ExpectedStandaloneQuery:** 我想保留一部分和对象不重叠的朋友圈。 我现在想知道应该怎么判断和处理，最好先做什么？
**RelevantIDs:** kb_v2_380
**Notes:** 完整 standalone query，应 passthrough；不得因历史存在就强制改写。

---

## context_v1_dev_030

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**RelationshipStage:** stable_relationship
**ExpectedGoals:** repair, communicate
**History:**
- 用户上一轮：一生气我就连续发很多消息，事后又后悔。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 这种情况我还要继续吗？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 一生气我就连续发很多消息，事后又后悔。 在这种情况下我还要继续吗？
**RelevantIDs:** kb_v2_167
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_031

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**RelationshipStage:** long_distance
**ExpectedGoals:** communicate, repair
**History:**
- 用户上一轮：异地什么时候结束一直没有时间表。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 那我要怎么跟ta说比较好？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 异地什么时候结束一直没有时间表。 我要怎么跟ta说比较好？
**RelevantIDs:** kb_v2_254
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_032

**QueryType:** standalone_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**RelationshipStage:** long_distance
**ExpectedGoals:** communicate, repair
**History:**
- 用户上一轮：最近工作挺忙的。
- 助手上一轮：如果你愿意，可以说说具体发生了什么。
**CurrentQuery:** 异地什么时候结束一直没有时间表。 我现在想知道应该怎么判断和处理，最好先做什么？
**RewriteRequired:** false
**ExpectedStandaloneQuery:** 异地什么时候结束一直没有时间表。 我现在想知道应该怎么判断和处理，最好先做什么？
**RelevantIDs:** kb_v2_254
**Notes:** 完整 standalone query，应 passthrough；不得因历史存在就强制改写。

---

## context_v1_dev_033

**QueryType:** context_dependent
**Difficulty:** hard
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**RelationshipStage:** long_distance
**ExpectedGoals:** repair
**History:**
- 用户上一轮：矛盾后我们两个人都在等对方先开口。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 所以这个变化一般说明什么？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 矛盾后我们两个人都在等对方先开口。 所以这个变化一般说明什么？
**RelevantIDs:** kb_v2_181
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_034

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, end_relationship
**History:**
- 用户上一轮：分手后共同租房押金和费用还没有结清。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 那我是不是先别主动了？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 分手后共同租房押金和费用还没有结清。 我是不是先别主动了？
**RelevantIDs:** kb_v2_482
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_dev_035

**QueryType:** standalone_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, end_relationship
**History:**
- 用户上一轮：最近工作挺忙的。
- 助手上一轮：如果你愿意，可以说说具体发生了什么。
**CurrentQuery:** 分手后共同租房押金和费用还没有结清。 我现在想知道应该怎么判断和处理，最好先做什么？
**RewriteRequired:** false
**ExpectedStandaloneQuery:** 分手后共同租房押金和费用还没有结清。 我现在想知道应该怎么判断和处理，最好先做什么？
**RelevantIDs:** kb_v2_482
**Notes:** 完整 standalone query，应 passthrough；不得因历史存在就强制改写。

---

## context_v1_dev_036

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**RelationshipStage:** dating
**ExpectedGoals:** understand, progress
**History:**
- 用户上一轮：第一次约会后我很喜欢对方，但不确定她感受。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 这种时候下一步怎么做比较稳？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 第一次约会后我很喜欢对方，但不确定她感受。 这种时候下一步怎么做比较稳？
**RelevantIDs:** kb_v2_061
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

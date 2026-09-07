# LoveApp Conditional Contextual Rewrite TEST

> Split: `test`
> Cases: **18**
> 用于测试 Conditional Contextual Query Rewrite。`RelevantIDs` 引用现有 500-KB，用于比较 Raw Query 与 Rewritten Query 的真实检索收益。

---

## context_v1_test_001

**QueryType:** context_dependent
**Difficulty:** hard
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** dating
**ExpectedGoals:** understand, communicate
**History:**
- 用户上一轮：她会吃醋，但又一直强调只是朋友。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 那我现在到底该怎么办？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 她会吃醋，但又一直强调只是朋友。 我现在到底该怎么办？
**RelevantIDs:** kb_v2_139
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_test_002

**QueryType:** standalone_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** dating
**ExpectedGoals:** understand, communicate
**History:**
- 用户上一轮：最近工作挺忙的。
- 助手上一轮：如果你愿意，可以说说具体发生了什么。
**CurrentQuery:** 她会吃醋，但又一直强调只是朋友。 我现在想知道应该怎么判断和处理，最好先做什么？
**RewriteRequired:** false
**ExpectedStandaloneQuery:** 她会吃醋，但又一直强调只是朋友。 我现在想知道应该怎么判断和处理，最好先做什么？
**RelevantIDs:** kb_v2_139
**Notes:** 完整 standalone query，应 passthrough；不得因历史存在就强制改写。

---

## context_v1_test_003

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**RelationshipStage:** long_distance
**ExpectedGoals:** communicate, repair
**History:**
- 用户上一轮：异地时我们对视频频率需求差很多。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 这种情况我还要继续吗？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 异地时我们对视频频率需求差很多。 在这种情况下我还要继续吗？
**RelevantIDs:** kb_v2_221
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_test_004

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**RelationshipStage:** acquaintance
**ExpectedGoals:** understand
**History:**
- 用户上一轮：以前聊天表情很多，最近突然变少。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 那我要怎么跟ta说比较好？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 以前聊天表情很多，最近突然变少。 我要怎么跟ta说比较好？
**RelevantIDs:** kb_v2_114
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_test_005

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
**CurrentQuery:** 以前聊天表情很多，最近突然变少。 我现在想知道应该怎么判断和处理，最好先做什么？
**RewriteRequired:** false
**ExpectedStandaloneQuery:** 以前聊天表情很多，最近突然变少。 我现在想知道应该怎么判断和处理，最好先做什么？
**RelevantIDs:** kb_v2_114
**Notes:** 完整 standalone query，应 passthrough；不得因历史存在就强制改写。

---

## context_v1_test_006

**QueryType:** context_dependent
**Difficulty:** hard
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**RelationshipStage:** dating
**ExpectedGoals:** communicate, set_boundary
**History:**
- 用户上一轮：约会花费谁承担，我们一直意见不同。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 所以这个变化一般说明什么？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 约会花费谁承担，我们一直意见不同。 所以这个变化一般说明什么？
**RelevantIDs:** kb_v2_226
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_test_007

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** long_distance
**ExpectedGoals:** communicate, progress
**History:**
- 用户上一轮：我们都说目标一致，但一直很少真正推进。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 那我是不是先别主动了？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 我们都说目标一致，但一直很少真正推进。 我是不是先别主动了？
**RelevantIDs:** kb_v2_351
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_test_008

**QueryType:** standalone_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** long_distance
**ExpectedGoals:** communicate, progress
**History:**
- 用户上一轮：最近工作挺忙的。
- 助手上一轮：如果你愿意，可以说说具体发生了什么。
**CurrentQuery:** 我们都说目标一致，但一直很少真正推进。 我现在想知道应该怎么判断和处理，最好先做什么？
**RewriteRequired:** false
**ExpectedStandaloneQuery:** 我们都说目标一致，但一直很少真正推进。 我现在想知道应该怎么判断和处理，最好先做什么？
**RelevantIDs:** kb_v2_351
**Notes:** 完整 standalone query，应 passthrough；不得因历史存在就强制改写。

---

## context_v1_test_009

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand, progress
**History:**
- 用户上一轮：交流和见面都不错，但关系一直没有进一步发展。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 这种时候下一步怎么做比较稳？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 交流和见面都不错，但关系一直没有进一步发展。 这种时候下一步怎么做比较稳？
**RelevantIDs:** kb_v2_062
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_test_010

**QueryType:** context_dependent
**Difficulty:** hard
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**RelationshipStage:** dating
**ExpectedGoals:** communicate, repair, set_boundary
**History:**
- 用户上一轮：对方家里人经常干预我们两个人的决定。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 那我现在到底该怎么办？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 对方家里人经常干预我们两个人的决定。 我现在到底该怎么办？
**RelevantIDs:** kb_v2_235
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_test_011

**QueryType:** standalone_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**RelationshipStage:** dating
**ExpectedGoals:** communicate, repair, set_boundary
**History:**
- 用户上一轮：最近工作挺忙的。
- 助手上一轮：如果你愿意，可以说说具体发生了什么。
**CurrentQuery:** 对方家里人经常干预我们两个人的决定。 我现在想知道应该怎么判断和处理，最好先做什么？
**RewriteRequired:** false
**ExpectedStandaloneQuery:** 对方家里人经常干预我们两个人的决定。 我现在想知道应该怎么判断和处理，最好先做什么？
**RelevantIDs:** kb_v2_235
**Notes:** 完整 standalone query，应 passthrough；不得因历史存在就强制改写。

---

## context_v1_test_012

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**History:**
- 用户上一轮：对象未经我明确同意翻看我的相册。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 这种情况我还要继续吗？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 对象未经我明确同意翻看我的相册。 在这种情况下我还要继续吗？
**RelevantIDs:** kb_v2_361
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_test_013

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** long_distance
**ExpectedGoals:** communicate, progress
**History:**
- 用户上一轮：异地多久结束没有明确计划。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 那我要怎么跟ta说比较好？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 异地多久结束没有明确计划。 我要怎么跟ta说比较好？
**RelevantIDs:** kb_v2_314
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_test_014

**QueryType:** standalone_control
**Difficulty:** medium
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** long_distance
**ExpectedGoals:** communicate, progress
**History:**
- 用户上一轮：最近工作挺忙的。
- 助手上一轮：如果你愿意，可以说说具体发生了什么。
**CurrentQuery:** 异地多久结束没有明确计划。 我现在想知道应该怎么判断和处理，最好先做什么？
**RewriteRequired:** false
**ExpectedStandaloneQuery:** 异地多久结束没有明确计划。 我现在想知道应该怎么判断和处理，最好先做什么？
**RelevantIDs:** kb_v2_314
**Notes:** 完整 standalone query，应 passthrough；不得因历史存在就强制改写。

---

## context_v1_test_015

**QueryType:** context_dependent
**Difficulty:** hard
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, set_boundary
**History:**
- 用户上一轮：周末我想留半天只做自己的事情。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 所以这个变化一般说明什么？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 周末我想留半天只做自己的事情。 所以这个变化一般说明什么？
**RelevantIDs:** kb_v2_282
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_test_016

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**History:**
- 用户上一轮：对象希望知道我所有社交软件密码。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 那我是不是先别主动了？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 对象希望知道我所有社交软件密码。 我是不是先别主动了？
**RelevantIDs:** kb_v2_364
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

---

## context_v1_test_017

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
**CurrentQuery:** 对象希望知道我所有社交软件密码。 我现在想知道应该怎么判断和处理，最好先做什么？
**RewriteRequired:** false
**ExpectedStandaloneQuery:** 对象希望知道我所有社交软件密码。 我现在想知道应该怎么判断和处理，最好先做什么？
**RelevantIDs:** kb_v2_364
**Notes:** 完整 standalone query，应 passthrough；不得因历史存在就强制改写。

---

## context_v1_test_018

**QueryType:** context_dependent
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**RelationshipStage:** long_distance
**ExpectedGoals:** understand, communicate
**History:**
- 用户上一轮：长期矛盾减少了，但亲密感也一起下降。
- 助手上一轮：可以先把已经发生的事实、你的感受和你希望的结果分开看。
**CurrentQuery:** 这种时候下一步怎么做比较稳？
**RewriteRequired:** true
**ExpectedStandaloneQuery:** 长期矛盾减少了，但亲密感也一起下降。 这种时候下一步怎么做比较稳？
**RelevantIDs:** kb_v2_353
**Notes:** 必须利用历史补全指代/省略；以语义和检索结果评测，不做逐字匹配。

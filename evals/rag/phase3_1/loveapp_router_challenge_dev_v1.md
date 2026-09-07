# LoveApp Phase 3.1 Semantic Router Challenge Dev

> Split: `challenge_dev` (Dev-only)
> Cases: **120**
> This fixture is for semantic-router remediation only. It is not a held-out
> Test set, is never added to the knowledge base or embedding corpus, and does
> not alter the frozen Phase 3 Dev/Test Gold.

Challenge slices:

- `short_colloquial`: cases 001-060 (60 cases; all colloquial and short)
- `scenario_hard_confusion`: cases 061-100 (40 cases)
- `goal_multilabel`: cases 081-120 (40 cases; overlaps the final hard cases)

The labels use the frozen Router/Safety enum vocabulary. `ExpectedGoals` is
intentionally multi-label on the goal slice.

---

## phase31_challenge_001
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** acquaintance
**ExpectedGoals:** initiate, progress
**ExpectedRiskLevel:** normal
**Query:** 刚认识的人，怎么自然把话题聊下去？

## phase31_challenge_002
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** acquaintance
**ExpectedGoals:** understand, progress
**ExpectedRiskLevel:** normal
**Query:** 他回得挺快，我要不要再约一次？

## phase31_challenge_003
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** acquaintance
**ExpectedGoals:** initiate, communicate
**ExpectedRiskLevel:** normal
**Query:** 想加对方联系方式，开口会不会太冒昧？

## phase31_challenge_004
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** acquaintance
**ExpectedGoals:** initiate, progress
**ExpectedRiskLevel:** normal
**Query:** 第一次见面后咋接着聊，不显得刻意？

## phase31_challenge_005
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** progress, communicate
**ExpectedRiskLevel:** normal
**Query:** 对方答应下次见，我该怎么推进？

## phase31_challenge_006
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** progress
**ExpectedRiskLevel:** normal
**Query:** 聊得还行，怎么把关系往前带一点？

## phase31_challenge_007
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** acquaintance
**ExpectedGoals:** initiate, communicate
**ExpectedRiskLevel:** normal
**Query:** 刚加上好友，第一句说啥比较自然？

## phase31_challenge_008
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** acquaintance
**ExpectedGoals:** initiate, progress
**ExpectedRiskLevel:** normal
**Query:** 想约她喝咖啡，怎么说不尴尬？

## phase31_challenge_009
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand, progress
**ExpectedRiskLevel:** normal
**Query:** 他总是点赞但不主动，我还要继续吗？

## phase31_challenge_010
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**Query:** 昨天聊得热络，今天突然冷了，啥意思？

## phase31_challenge_011
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**Query:** 她只回表情，是忙还是没兴趣？

## phase31_challenge_012
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**Query:** 他看了消息没回，我该咋想？

## phase31_challenge_013
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**Query:** 忽冷忽热的，到底怎么判断？

## phase31_challenge_014
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand, progress
**ExpectedRiskLevel:** normal
**Query:** 她说改天再约，是真有事还是婉拒？

## phase31_challenge_015
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**Query:** 对方聊天总绕开感情话题，说明啥？

## phase31_challenge_016
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**Query:** 他突然不叫我昵称了，是我多想吗？

## phase31_challenge_017
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**Query:** 刚吵完架，怎么把话说开？

## phase31_challenge_018
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**Query:** 他又翻旧账了，我该怎么接？

## phase31_challenge_019
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** repair
**ExpectedRiskLevel:** normal
**Query:** 我们因为小事顶起来，咋收场？

## phase31_challenge_020
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**Query:** 冷战几天了，第一句话说什么？

## phase31_challenge_021
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**Query:** 她觉得我不在乎，我怎么解释？

## phase31_challenge_022
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**Query:** 每次一聊钱就吵，怎么破？

## phase31_challenge_023
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** boundary
**RelationshipStage:** dating
**ExpectedGoals:** communicate, set_boundary
**ExpectedRiskLevel:** normal
**Query:** 他说我管太多，这次该怎么谈？

## phase31_challenge_024
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** repair, progress
**ExpectedRiskLevel:** normal
**Query:** 吵完他不理我，我要不要先低头？

## phase31_challenge_025
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** progress, communicate
**ExpectedRiskLevel:** normal
**Query:** 平时相处没大事，怎么让关系更稳定？

## phase31_challenge_026
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** progress
**ExpectedRiskLevel:** normal
**Query:** 想和对象把日常过得舒服点，有啥建议？

## phase31_challenge_027
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** understand, progress
**ExpectedRiskLevel:** normal
**Query:** 最近联系少了，但也没吵架，咋调整？

## phase31_challenge_028
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress
**ExpectedRiskLevel:** normal
**Query:** 怎么让两个人的相处别总靠一方维持？

## phase31_challenge_029
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** understand, communicate
**ExpectedRiskLevel:** normal
**Query:** 在一起久了没话聊，正常吗？

## phase31_challenge_030
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** progress
**ExpectedRiskLevel:** normal
**Query:** 想安排一次小约会，让感情升温，怎么做？

## phase31_challenge_031
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** communicate, progress
**ExpectedRiskLevel:** normal
**Query:** 双方工作都忙，怎么保持联系不累？

## phase31_challenge_032
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** relationship_maintenance
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** understand, communicate
**ExpectedRiskLevel:** normal
**Query:** 他对未来没规划，我该怎么聊？

## phase31_challenge_033
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 他总拿我手机看，我不舒服，咋说？

## phase31_challenge_034
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 不想马上公开关系，怎么讲清楚？

## phase31_challenge_035
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 朋友总替我做决定，我该拒绝吗？

## phase31_challenge_036
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 对方老在我忙时打电话，怎么设个界限？

## phase31_challenge_037
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 我不想分享密码，说出来会伤感情吗？

## phase31_challenge_038
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 他未经允许发我们的照片，我该怎么办？

## phase31_challenge_039
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 每次见面都要我买单，怎么谈钱的边界？

## phase31_challenge_040
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 我需要一点独处时间，怎么让他理解？

## phase31_challenge_041
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** understand, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 想分开但又舍不得，先想清楚什么？

## phase31_challenge_042
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** stable_relationship
**ExpectedGoals:** understand, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 这段关系让我很累，要不要结束？

## phase31_challenge_043
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** breakup
**ExpectedGoals:** end_relationship, set_boundary
**ExpectedRiskLevel:** normal
**Query:** 分手后还要不要继续联系？

## phase31_challenge_044
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** breakup
**ExpectedGoals:** end_relationship, communicate
**ExpectedRiskLevel:** normal
**Query:** 已经说分开了，东西怎么处理不尴尬？

## phase31_challenge_045
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** breakup
**ExpectedGoals:** understand, progress
**ExpectedRiskLevel:** normal
**Query:** 他想复合，我不知道该不该答应。

## phase31_challenge_046
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** breakup
**ExpectedGoals:** end_relationship, communicate
**ExpectedRiskLevel:** normal
**Query:** 准备提分手，怎么说比较体面？

## phase31_challenge_047
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** breakup
**ExpectedGoals:** understand, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 分开后总想回头，怎么稳住自己？

## phase31_challenge_048
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** breakup
**ExpectedGoals:** end_relationship, communicate
**ExpectedRiskLevel:** normal
**Query:** 关系走到尽头了，怎样好好告别？

## phase31_challenge_049
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand, communicate
**ExpectedRiskLevel:** normal
**Query:** 他发来一句“在吗”，我该怎么回？

## phase31_challenge_050
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**Query:** 对方突然送礼物，我该怎么判断用意？

## phase31_challenge_051
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**Query:** 他说“随便你”，这话是在生气吗？

## phase31_challenge_052
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** chat_analysis
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand
**ExpectedRiskLevel:** normal
**Query:** 我想知道他是不是在试探我，咋看？

## phase31_challenge_053
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand, progress
**ExpectedRiskLevel:** normal
**Query:** 第一次见面后没下文，还值得再问吗？

## phase31_challenge_054
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand, progress
**ExpectedRiskLevel:** normal
**Query:** 暧昧期卡住了，下一步怎么走？

## phase31_challenge_055
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** out_of_scope
**ExpectedPrimaryScenario:** null
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** null
**ExpectedGoals:** []
**ExpectedRiskLevel:** normal
**Query:** 电脑蓝屏咋整？

## phase31_challenge_056
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** out_of_scope
**ExpectedPrimaryScenario:** null
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** null
**ExpectedGoals:** []
**ExpectedRiskLevel:** normal
**Query:** 明天上海下雨吗？

## phase31_challenge_057
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** out_of_scope
**ExpectedPrimaryScenario:** null
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** null
**ExpectedGoals:** []
**ExpectedRiskLevel:** normal
**Query:** Java 报错怎么排查？

## phase31_challenge_058
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** out_of_scope
**ExpectedPrimaryScenario:** null
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** null
**ExpectedGoals:** []
**ExpectedRiskLevel:** normal
**Query:** 番茄炒蛋怎么做？

## phase31_challenge_059
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** out_of_scope
**ExpectedPrimaryScenario:** null
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** null
**ExpectedGoals:** []
**ExpectedRiskLevel:** normal
**Query:** 美元兑人民币现在多少？

## phase31_challenge_060
**ChallengeSlices:** short_colloquial
**QueryType:** colloquial
**Difficulty:** medium
**LengthBucket:** short
**ExpectedBranch:** out_of_scope
**ExpectedPrimaryScenario:** null
**ExpectedSecondaryScenarios:** []
**RelationshipStage:** null
**ExpectedGoals:** []
**ExpectedRiskLevel:** normal
**Query:** 帮我写一份周报开头。

## phase31_challenge_061
**ChallengeSlices:** scenario_hard_confusion
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** conflict
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 我不想让他看手机，可他觉得我心里有鬼，怎么谈？

## phase31_challenge_062
**ChallengeSlices:** scenario_hard_confusion
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** conflict
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 她总替我答应别人，我说不行就吵起来了，怎么办？

## phase31_challenge_063
**ChallengeSlices:** scenario_hard_confusion
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** conflict
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 他未经同意把聊天截图发群里，我们为此争执，我该先说什么？

## phase31_challenge_064
**ChallengeSlices:** scenario_hard_confusion
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** conflict
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 我拒绝亲密接触后他很生气，怎样把界限讲明白？

## phase31_challenge_065
**ChallengeSlices:** scenario_hard_confusion
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** conflict
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 对方借钱不还，我想拒绝再次借，怎么说才不升级矛盾？

## phase31_challenge_066
**ChallengeSlices:** scenario_hard_confusion
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** conflict
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 他要求随时汇报行踪，我不答应就冷脸，怎么处理？

## phase31_challenge_067
**ChallengeSlices:** scenario_hard_confusion
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** conflict
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 我不愿公开关系，她说我在躲她，边界和争吵该怎么分开看？

## phase31_challenge_068
**ChallengeSlices:** scenario_hard_confusion
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** conflict
**RelationshipStage:** dating
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 家人介入我们的决定，我想退出这类讨论，怎么表达？

## phase31_challenge_069
**ChallengeSlices:** scenario_hard_confusion
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 我们相处没什么大矛盾，只是我需要独处，他总觉得我疏远，怎么说？

## phase31_challenge_070
**ChallengeSlices:** scenario_hard_confusion
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 平时关系不错，但他常翻我聊天记录，我该怎么设界限？

## phase31_challenge_071
**ChallengeSlices:** scenario_hard_confusion
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 交往稳定后各自的钱要不要分开，我想谈清楚又怕破坏气氛。

## phase31_challenge_072
**ChallengeSlices:** scenario_hard_confusion
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 她喜欢把日常发到网上，我不想露脸，怎样协商？

## phase31_challenge_073
**ChallengeSlices:** scenario_hard_confusion
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 工作忙时我不想即时回复，怎么让对方别把它当冷淡？

## phase31_challenge_074
**ChallengeSlices:** scenario_hard_confusion
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 两个人都挺好，只是见朋友和家人要不要一起，我有自己的节奏。

## phase31_challenge_075
**ChallengeSlices:** scenario_hard_confusion
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 他总替我安排周末，我想保留自己的计划，怎么讲不伤关系？

## phase31_challenge_076
**ChallengeSlices:** scenario_hard_confusion
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 关系没有出问题，但我不接受共享定位，该怎么说明？

## phase31_challenge_077
**ChallengeSlices:** scenario_hard_confusion
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**Query:** 我们没有要分手，只是每次谈未来都会吵，怎么把这个循环停下来？

## phase31_challenge_078
**ChallengeSlices:** scenario_hard_confusion
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**Query:** 日常相处还行，一提家务分配就互相指责，先解决争执还是谈规则？

## phase31_challenge_079
**ChallengeSlices:** scenario_hard_confusion
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**Query:** 他觉得我不够关心，我觉得他要求太多，怎么从争吵回到相处？

## phase31_challenge_080
**ChallengeSlices:** scenario_hard_confusion
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**Query:** 连续几次因为回消息速度吵架，关系还想继续，怎么修复？

## phase31_challenge_081
**ChallengeSlices:** scenario_hard_confusion, goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** chat_analysis
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand, progress
**ExpectedRiskLevel:** normal
**Query:** 他约过我一次，后来只偶尔发消息，我该看行动还是继续试探？

## phase31_challenge_082
**ChallengeSlices:** scenario_hard_confusion, goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** chat_analysis
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand, progress
**ExpectedRiskLevel:** normal
**Query:** 刚认识不久，她聊天很热但迟迟不肯见面，下一步怎么判断？

## phase31_challenge_083
**ChallengeSlices:** scenario_hard_confusion, goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** chat_analysis
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand, progress
**ExpectedRiskLevel:** normal
**Query:** 对方说有空再约，最近又点赞我的动态，我要不要再发消息？

## phase31_challenge_084
**ChallengeSlices:** scenario_hard_confusion, goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** chat_analysis
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand, progress
**ExpectedRiskLevel:** normal
**Query:** 我主动多一点他也回应，只是不确定这是礼貌还是有兴趣，怎么推进？

## phase31_challenge_085
**ChallengeSlices:** scenario_hard_confusion, goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** chat_analysis
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand, progress
**ExpectedRiskLevel:** normal
**Query:** 聊天里她常问我的事，却从不主动约，我该怎么理解并行动？

## phase31_challenge_086
**ChallengeSlices:** scenario_hard_confusion, goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** chat_analysis
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand, progress
**ExpectedRiskLevel:** normal
**Query:** 第一次见面后他说下次再聊，几天没动静，我该不该给个轻松的开场？

## phase31_challenge_087
**ChallengeSlices:** scenario_hard_confusion, goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** chat_analysis
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand, progress
**ExpectedRiskLevel:** normal
**Query:** 他一会儿热一会儿冷，我既想弄明白，也想知道还能不能继续靠近。

## phase31_challenge_088
**ChallengeSlices:** scenario_hard_confusion, goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** pursuit
**ExpectedSecondaryScenarios:** chat_analysis
**RelationshipStage:** ambiguous
**ExpectedGoals:** understand, progress
**ExpectedRiskLevel:** normal
**Query:** 暧昧对象总在深夜聊天，白天却很少联系，我该怎么判断下一步？

## phase31_challenge_089
**ChallengeSlices:** scenario_hard_confusion, goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**Query:** 我们还想好好相处，但每次提到消费分担就吵，怎么把争执谈成方案？

## phase31_challenge_090
**ChallengeSlices:** scenario_hard_confusion, goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**Query:** 关系本身没想结束，可他把承诺忘了几次，我该怎么修复信任并说清需要？

## phase31_challenge_091
**ChallengeSlices:** scenario_hard_confusion, goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**Query:** 她觉得我在敷衍，我觉得她一直追问，怎么结束这轮争吵再继续相处？

## phase31_challenge_092
**ChallengeSlices:** scenario_hard_confusion, goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** conflict
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** repair, communicate
**ExpectedRiskLevel:** normal
**Query:** 双方都愿意继续，只是旧账反复出现，怎样道歉、倾听并重建相处方式？

## phase31_challenge_093
**ChallengeSlices:** scenario_hard_confusion, goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** understand, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 我们没有立刻分手，但长期消耗让我想结束，怎么先谈清彼此还能否继续？

## phase31_challenge_094
**ChallengeSlices:** scenario_hard_confusion, goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** understand, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 相处还有温情，却反复踩到同一个问题，我该修复还是准备离开？

## phase31_challenge_095
**ChallengeSlices:** scenario_hard_confusion, goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** understand, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 他说想冷静一阵，我想知道这是调整相处还是关系要结束？

## phase31_challenge_096
**ChallengeSlices:** scenario_hard_confusion, goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** understand, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 关系快走到尽头了，我还想好好沟通一次，应该谈哪些事？

## phase31_challenge_097
**ChallengeSlices:** scenario_hard_confusion, goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 已经决定分开，怎样把联系方式和见面边界说清楚？

## phase31_challenge_098
**ChallengeSlices:** scenario_hard_confusion, goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 分手后还要共同照顾宠物，怎么定规则避免反复拉扯？

## phase31_challenge_099
**ChallengeSlices:** scenario_hard_confusion, goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 我想结束这段关系，也不想继续被临时求助，怎么表达？

## phase31_challenge_100
**ChallengeSlices:** scenario_hard_confusion, goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** long
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 双方都知道不合适了，告别时哪些界限要提前讲好？

## phase31_challenge_101
**ChallengeSlices:** goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 我想保留自己的社交圈，也想让伴侣安心，怎么一起定出可执行的边界？

## phase31_challenge_102
**ChallengeSlices:** goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 不愿共享账号但愿意增加透明度，怎么沟通这个取舍？

## phase31_challenge_103
**ChallengeSlices:** goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 对方总在我工作时讨论关系，我想约定合适时间，应该怎么开口？

## phase31_challenge_104
**ChallengeSlices:** goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** boundary
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** set_boundary, communicate
**ExpectedRiskLevel:** normal
**Query:** 我需要拒绝一笔共同支出，同时不让对方觉得被否定，怎么谈？

## phase31_challenge_105
**ChallengeSlices:** goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** understand, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 我越来越想离开，但分不清是暂时疲惫还是关系真的不合适，怎么判断？

## phase31_challenge_106
**ChallengeSlices:** goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** understand, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 他对我很好，可我没有继续的意愿，怎样确认自己的想法并决定是否结束？

## phase31_challenge_107
**ChallengeSlices:** goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** breakup
**ExpectedGoals:** understand, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 分开后我总怀疑是不是冲动，怎么理解这种反复再做决定？

## phase31_challenge_108
**ChallengeSlices:** goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** understand, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 我们价值观差很多，我想知道还能否调整，还是该结束？

## phase31_challenge_109
**ChallengeSlices:** goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** understand, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 每次想到未来都想逃，我该先看哪些信号再决定去留？

## phase31_challenge_110
**ChallengeSlices:** goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** understand, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 她说愿意改变，我却没有信心了，怎么判断该修复还是离开？

## phase31_challenge_111
**ChallengeSlices:** goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** breakup
**ExpectedGoals:** understand, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 关系结束后对方还来找我，我想理解自己的犹豫并说明决定。

## phase31_challenge_112
**ChallengeSlices:** goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** stable_relationship
**ExpectedGoals:** understand, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 我不确定舍不得的是人还是习惯，怎么想清楚要不要分手？

## phase31_challenge_113
**ChallengeSlices:** goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 决定分手后，怎样把不再见面和不再暧昧说清楚？

## phase31_challenge_114
**ChallengeSlices:** goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 分开了但有共同朋友圈，怎么设定联系范围？

## phase31_challenge_115
**ChallengeSlices:** goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 我愿意把物品还回去，但不想再单独见面，怎么提条件？

## phase31_challenge_116
**ChallengeSlices:** goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 前任总在夜里找我倾诉，我想结束这种联系，怎么说？

## phase31_challenge_117
**ChallengeSlices:** goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 共同账单还没结清，如何在分手后把责任和联络边界写明？

## phase31_challenge_118
**ChallengeSlices:** goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 不想公开解释分手原因，但需要告诉重要朋友，怎么处理边界？

## phase31_challenge_119
**ChallengeSlices:** goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 已经说好分开，他又提出做朋友，我不想接受，怎样坚定表达？

## phase31_challenge_120
**ChallengeSlices:** goal_multilabel
**QueryType:** long_context
**Difficulty:** hard
**LengthBucket:** medium
**ExpectedBranch:** rag
**ExpectedPrimaryScenario:** breakup
**ExpectedSecondaryScenarios:** relationship_maintenance
**RelationshipStage:** breakup
**ExpectedGoals:** set_boundary, end_relationship
**ExpectedRiskLevel:** normal
**Query:** 结束关系后还要在同一单位见面，怎样保持礼貌又不恢复亲密？

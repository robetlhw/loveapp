# Extraction Failure Review

## Scope

This review is based only on the final Live V2 artifact:

- Artifact: `.data/evals/memory_longtail_realistic_live_v2_20260830_223348.json`
- Report version: `memory-longtail-realistic-v2`
- Generated at: `2026-08-30T14:36:24.233607+00:00`
- Dataset hash: `6f2db529d7d6601b93e478aaf2ebfb26d8855ee4869775a5c2cd18af6a96b14a`
- Evaluation mode: `shadow_live`
- Store mutation permitted: `false`

The Live V2 metric counted `38` Gate-admitted expected claims, `31` matched claims, and `7` unmatched expected claims. This document reviews those seven only. Gate false negatives are excluded because extraction was never called for those turns; they are covered separately by `GATE_FALSE_NEGATIVE_REVIEW.md`.

Observed boundary telemetry:

- Extractor calls: `37`
- Extractor boundary failures: `0`
- Failed internal attempts: `2`
- Empty-claim turns after validation: `2`
- Schema/semantic validation failures counted by the evaluator: `1`

`empty claim` is an observed outcome for two rows, not their root-cause category. One was rejected for atomicity and one for invalid evidence provenance.

## Summary

| Case / Turn | Primary review type | Expected claim | Actual result | Review disposition |
|---|---|---|---|---|
| `LT-R-004/t1` | wrong semantic identity | `interaction_pattern / discusses_future_plans` | Valid custom claim `discuss_future_plans`, but not a reviewed representation | Custom contract review; do not auto-accept |
| `LT-S-001/t2` | atomization issue | ongoing emotional disclosure pattern | No usable claim; candidate contained two governed dimensions | Extraction failure; fail-closed behavior was correct |
| `LT-P-001/t2` | wrong semantic identity | `stable_fact / possibly_avoids_social_integration` | Valid user-belief claim `believes_partner_reluctant_to_integrate`, but unmatched | Custom contract review; do not auto-accept |
| `LT-C-001/t1` | wrong kind | stable fact about willingness to introduce friends | `relationship_state / relationship.familiarity=moderate` | Reject as equivalent representation |
| `LT-C-001/t2` | wrong kind | stable fact about unwillingness to meet parents | `relationship_state / partner_family_meeting_status=not_ready` | Kind/role contract needs review |
| `LT-C-002/t2` | schema failure | interaction pattern avoiding family topics | No usable claim; evidence span came from history, not current input | Extraction failure; fail-closed behavior was correct |
| `LT-A-002/t1` | atomization issue | one compound invitation-and-introduction claim | Two valid atomic custom claims | Evaluation expectation needs review |

Primary review-type counts sum to seven:

- Wrong semantic identity: `2`
- Wrong kind: `2`
- Schema/semantic validation failure: `1`
- Atomization issue: `2`

## LT-R-004/t1

Input:

```text
最近我们经常一起聊以后想去哪里生活、怎么安排未来。
```

Expected claim:

- Kind: `interaction_pattern`
- Custom predicate: `discusses_future_plans`
- Reviewed semantic concept: `interaction_future_topic_scope`
- Reviewed alternatives in the artifact: canonical `interaction.topic_scope=future_planning` or custom `discusses_future_plans`

Actual claims:

```text
interaction_pattern
custom_predicate = discuss_future_plans
summary = 最近经常一起聊未来生活安排
confidence = 0.90
```

Failure type: `wrong semantic identity` under the reviewed V2 contract.

The claim is structurally valid and its surface meaning is close to the expected claim, but `discuss_future_plans` was not present in the artifact's reviewed acceptable representations. The evaluator therefore left `future-old` unmatched. This is not an empty extraction or transport failure. The representation may be reviewed manually, but the Live output alone is not authority to add it.

## LT-S-001/t2

Input:

```text
最近她还是常常会主动找我倾诉自己的烦恼。
```

Expected claim:

- Kind: `interaction_pattern`
- Semantic concept: `interaction_emotional_disclosure`
- Expected state: emotional disclosure remains high/frequent

Actual claims: none after validation.

Failure type: `atomization issue`, with `empty claim` as the final outcome.

The Flash attempt produced one candidate that simultaneously contained `emotional_disclosure` and `initiation_balance`. Production atomicity validation rejected it:

```text
claims.0 - 原子声明 c1 包含多个记忆维度：emotional_disclosure, initiation_balance
```

Attempt category was `atomicity_validation`; repair was not available and the result was discarded. Fail closed was correct. The evidence supports separating the disclosure fact from the initiation cue rather than admitting a multi-dimensional claim.

## LT-P-001/t2

Input:

```text
虽然她这么说，但我感觉她可能其实不太想让我融入她的朋友圈。
```

Expected claim:

- Kind: `stable_fact`
- Custom predicate: `possibly_avoids_social_integration`
- Perspective: `user_belief`
- Explicitness: `weakly_inferred`

Actual claims:

```text
stable_fact
subject = user
custom_predicate = believes_partner_reluctant_to_integrate
perspective = user_belief
explicitness = strongly_implied
requires_inference = true
confidence = 0.60
```

Failure type: `wrong semantic identity` under the reviewed V2 contract.

The extractor retained the critical belief perspective and inference requirement, but the custom predicate was not a reviewed representation of the expected claim. The confidence and explicitness also differ from the embedded expectation. This is a candidate for manual Custom Semantic Contract review, not automatic predicate relaxation.

## LT-C-001/t1

Input:

```text
她现在愿意带我认识她的朋友。
```

Expected claim:

```text
kind = stable_fact
custom_predicate = willing_to_introduce_user_to_friends
```

Actual claims:

```text
kind = relationship_state
canonical_predicate = relationship.familiarity
state_dimension = relationship_familiarity
state_value = moderate
explicitness = strongly_implied
requires_inference = true
confidence = 0.70
```

Failure type: `wrong kind` and semantic over-generalization.

The source explicitly states willingness to introduce the user to friends. The actual claim inferred a broader relationship-familiarity state. Because kind compatibility is part of the semantic identity contract, this must not be accepted merely because the topics are related.

## LT-C-001/t2

Input:

```text
但她暂时还不愿意让我去见她父母。
```

Expected claim:

```text
kind = stable_fact
custom_predicate = unwilling_to_introduce_user_to_parents
```

Actual claims:

```text
kind = relationship_state
custom_predicate = partner_family_meeting_status
state_dimension = partner_family_meeting_status
state_value = not_ready
explicitness = explicit
confidence = 0.90
```

Failure type: `wrong kind` / memory-role mismatch.

The actual claim captures much of the source meaning, but it represents the statement as a mutable relationship state rather than the expected stable fact. That distinction affects lifecycle behavior, so semantic similarity alone cannot make the representations interchangeable. No automatic mapping is recommended from this trace.

## LT-C-002/t2

Input:

```text
但她还是不太愿意谈自己的家庭问题。
```

Expected claim:

```text
kind = interaction_pattern
custom_predicate = avoids_family_topics
```

Actual claims: none after validation.

Failure type: `schema failure` at semantic evidence validation, with `empty claim` as the final outcome.

The model included this history sentence as an evidence span even though it was not part of the current input:

```text
她最近很愿意跟我聊工作压力
```

Production validation rejected the output with failure category `semantic_validation` and reason `证据片段不在用户原文中`. This is the one failure counted by the V2 `schema_validation_failure_count` metric. The fail-closed outcome is correct; admitting the claim would violate source provenance.

## LT-A-002/t1

Input:

```text
她最近经常邀请我参加朋友聚会，也会主动把我介绍给朋友。
```

Expected claim:

```text
kind = interaction_pattern
custom_predicate = invites_and_introduces_user_to_friends
```

Actual claims:

```text
1. invite_to_social_gatherings
   summary = 她最近经常邀请我参加朋友聚会

2. introduce_to_friends
   summary = 她会主动把我介绍给朋友
```

Both actual claims were explicit, user-reported, confidence `0.90`, and structurally valid.

Failure type: `atomization issue` in the evaluation contract.

The fixture expects one compound claim, while production correctly produced two independently evolvable facts: invitation behavior and introduction behavior. They must not be collapsed solely to match the expected predicate. This row should be reviewed as an evaluation-expectation mismatch; no extractor change is justified by this trace.

## Conclusions

1. The seven metric failures do not represent seven model-empty turns. Five turns produced structurally usable claims that failed matching or kind/atomization expectations.
2. Two turns ended empty because production validation rejected unsafe output. Both correctly failed closed.
3. `LT-R-004/t1` and `LT-P-001/t2` are candidates for human review of acceptable custom representations, but the Live model output itself is not sufficient approval.
4. `LT-C-001/t1` must remain a mismatch because the actual extraction inferred a broader canonical relationship state.
5. `LT-C-001/t2` needs a deliberate memory-role decision before any representation can be accepted.
6. `LT-A-002/t1` is evidence that the compound expected claim conflicts with production atomization, not evidence that production should emit a compound memory.
7. No Flash prompt, extractor architecture, dataset expectation, or production admission rule is changed by this review.

# Memory Extraction V1 Failure Review

Dataset: `evals\memory\extraction_v1_70.jsonl`  
Dataset SHA256: `a9ed2f8b26e829171e1de98d59b26de2e823b44c7845cdf3fddf0d88e6a58b00`  
Pre-remediation baseline generated: `2026-09-01T20:27:30.626644+08:00`  
Cases: `70`

This is a read-only review of the pre-remediation baseline. It does not call a model or mutate Memory Store state.

## Input Consistency

| Baseline | Flash diagnostic | Production cascade | IDs equal |
|---:|---:|---:|---|
| 70 | 70 | 70 | True |

## Repair Delta

Programmatic result: `3` hurt, `1` helped.

| Case | Raw matched | Post matched | Attribution | Rule / failure |
|---|---:|---:|---|---|
| EXT-016 | 1 | 0 | CANONICAL_NORMALIZATION_COUPLING | registered canonical plus duplicate custom predicate was rejected |
| EXT-024 | 1 | 0 | EVIDENCE_REPAIR | structured evidence object was discarded instead of narrowed to its text |
| EXT-049 | 2 | 0 | OTHER | semantic alignment one-to-one parse failure; no claim was discarded |
| EXT-047 (helped) | 0 | 1 | OTHER | no destructive repair rule; inspect alignment or sampling |

### EXT-016

User message: 最近一个月她基本都不主动找我了。

- Attribution: `CANONICAL_NORMALIZATION_COUPLING`
- Causal rule: registered canonical plus duplicate custom predicate was rejected
- Repair: `local_repair` / `interaction_value_contract,all_claims_invalid,partial_claims`
- Invalid reasons: `claims.0 - 声明 claim_001 不能同时提供 canonical 和 custom predicate`
- Lost expected indices: `[0]`
- Gained expected indices: `[]`

<details><summary>Expected / Raw / Post-Repair diff</summary>

```json
{
  "expected_claims": [
    {
      "kind": "interaction_pattern",
      "subject": "relationship",
      "perspective": "user_reported",
      "semantic_target": "partner_contact_initiation_decreased",
      "evidence_spans": [
        "最近一个月她基本都不主动找我了"
      ]
    }
  ],
  "flash_raw": {
    "claims": [
      {
        "claim_id": "claim_001",
        "kind": "interaction_pattern",
        "subject": "partner",
        "predicate": "initiates_contact",
        "predicate_type": "custom",
        "custom_predicate": "initiation_balance",
        "canonical_predicate": "interaction.initiation_balance",
        "raw_predicate": "initiates_contact",
        "summary": "最近一个月，对方基本不主动联系用户。",
        "evidence_spans": [
          "最近一个月她基本都不主动找我了。"
        ],
        "explicitness": "explicit",
        "requires_inference": false,
        "time_kind": "interval",
        "period_start": "2026-08-01T00:00:00+08:00",
        "period_end": "2026-09-01T18:00:00+08:00",
        "temporal_precision": "month",
        "valence": "negative",
        "relationship_impact": "damaging",
        "confidence": 0.95,
        "importance": 4,
        "intensity": 3,
        "payload": {
          "metric": "initiation_balance",
          "direction": "decreasing",
          "baseline": "normal",
          "current": "low",
          "frequency": "monthly",
          "participants": [
            "partner"
          ],
          "relationship_evidence": [
            {
              "dimension": "investment",
              "direction": "oppose",
              "strength": 0.6,
              "confidence": 0.8,
              "rationale": "partner_initiative_decreased"
            }
          ]
        }
      }
    ],
    "alignment": {
      "matches": [
        {
          "expected_index": 0,
          "actual_index": 0,
          "proposition_equivalent": true,
          "semantic_match": true,
          "evidence_support": "PASS",
          "reason": "Both claims state that the partner has stopped initiating contact in the last month."
        }
      ],
      "unmatched_expected": [],
      "unmatched_actual": [],
      "over_merge_actual_indices": [],
      "over_split_expected_indices": [],
      "uncertain": false,
      "reason": "The expected and actual claims align on the same proposition with matching evidence."
    }
  },
  "post_repair": {
    "claims": [],
    "alignment": {
      "matches": [],
      "unmatched_expected": [
        0
      ],
      "unmatched_actual": [],
      "over_merge_actual_indices": [],
      "over_split_expected_indices": [],
      "uncertain": false,
      "reason": ""
    }
  }
}
```

</details>

### EXT-024

User message: 我越来越担心她其实不太想继续这段关系，但她没有明确这么说过。

- Attribution: `EVIDENCE_REPAIR`
- Causal rule: structured evidence object was discarded instead of narrowed to its text
- Repair: `local_repair` / `all_claims_invalid,partial_claims,partial_discarded_spans`
- Invalid reasons: `claims.0 - evidence_spans.0 - Input should be a valid string`
- Lost expected indices: `[0]`
- Gained expected indices: `[]`

<details><summary>Expected / Raw / Post-Repair diff</summary>

```json
{
  "expected_claims": [
    {
      "kind": "stable_fact",
      "subject": "relationship",
      "perspective": "user_belief",
      "semantic_target": "user_believes_partner_may_not_want_relationship_to_continue",
      "evidence_spans": [
        "我越来越担心她其实不太想继续这段关系"
      ]
    }
  ],
  "flash_raw": {
    "claims": [
      {
        "claim_id": "c1",
        "kind": "stable_fact",
        "subject": "user",
        "predicate": "worried_partner_may_not_want_relationship",
        "predicate_type": "custom",
        "custom_predicate": "worried_partner_may_not_want_relationship",
        "canonical_predicate": null,
        "raw_predicate": "worried_partner_may_not_want_relationship",
        "summary": "用户越来越担心对方可能不太想继续这段关系，但对方没有明确表达过。",
        "evidence_spans": [
          {
            "text": "我越来越担心她其实不太想继续这段关系",
            "start": 0,
            "end": 20
          }
        ],
        "explicitness": "explicit",
        "requires_inference": false,
        "perspective": "user_belief",
        "confidence": 0.8,
        "importance": 4,
        "intensity": 4,
        "time_kind": "timeless",
        "temporal_precision": "unknown",
        "payload": {
          "source_type": "user_belief",
          "belief_duration": "increasing",
          "belief_evidence": "increasing_worry_without_explicit_confirmation"
        }
      }
    ],
    "alignment": {
      "matches": [
        {
          "expected_index": 0,
          "actual_index": 0,
          "proposition_equivalent": true,
          "semantic_match": true,
          "evidence_support": "PASS",
          "reason": "Both express the user's belief that the partner may not want to continue the relationship, with the actual claim adding the nuance of increasing worry and lack of explicit confirmation."
        }
      ],
      "unmatched_expected": [],
      "unmatched_actual": [],
      "over_merge_actual_indices": [],
      "over_split_expected_indices": [],
      "uncertain": false,
      "reason": "The expected and actual claims align on the same proposition with compatible subject and perspective."
    }
  },
  "post_repair": {
    "claims": [],
    "alignment": {
      "matches": [],
      "unmatched_expected": [
        0
      ],
      "unmatched_actual": [],
      "over_merge_actual_indices": [],
      "over_split_expected_indices": [],
      "uncertain": false,
      "reason": ""
    }
  }
}
```

</details>

### EXT-049

User message: 她回复还是很快，但消息比以前短很多，而且最近很少主动开启话题。

- Attribution: `OTHER`
- Causal rule: semantic alignment one-to-one parse failure; no claim was discarded
- Repair: `local_repair` / `interaction_value_contract`
- Invalid reasons: `none`
- Lost expected indices: `[0, 2]`
- Gained expected indices: `[]`

<details><summary>Expected / Raw / Post-Repair diff</summary>

```json
{
  "expected_claims": [
    {
      "kind": "interaction_pattern",
      "subject": "relationship",
      "perspective": "user_reported",
      "semantic_target": "reply_speed_remains_fast",
      "evidence_spans": [
        "她回复还是很快"
      ]
    },
    {
      "kind": "interaction_pattern",
      "subject": "relationship",
      "perspective": "user_reported",
      "semantic_target": "message_length_decreased",
      "evidence_spans": [
        "消息比以前短很多"
      ]
    },
    {
      "kind": "interaction_pattern",
      "subject": "relationship",
      "perspective": "user_reported",
      "semantic_target": "partner_topic_initiation_decreased",
      "evidence_spans": [
        "最近很少主动开启话题"
      ]
    }
  ],
  "flash_raw": {
    "claims": [
      {
        "claim_id": "claim_001",
        "kind": "interaction_pattern",
        "subject": "partner",
        "predicate": "response_engagement",
        "predicate_type": "canonical",
        "canonical_predicate": "interaction.response_engagement",
        "raw_predicate": "response_engagement",
        "summary": "她回复速度很快，但消息比以前短很多，且最近很少主动开启话题。",
        "evidence_spans": [
          "她回复还是很快",
          "但消息比以前短很多",
          "而且最近很少主动开启话题"
        ],
        "explicitness": "explicit",
        "requires_inference": false,
        "time_kind": "interval",
        "temporal_precision": "approximate",
        "confidence": 0.9,
        "importance": 4,
        "intensity": 3,
        "valence": "negative",
        "relationship_impact": "damaging",
        "payload": {
          "metric": "response_engagement",
          "direction": "decreasing",
          "baseline": "responsive",
          "current": "brief_responses",
          "frequency": "recently",
          "channel": "text",
          "relationship_evidence": [
            {
              "dimension": "investment",
              "direction": "oppose",
              "strength": 0.6,
              "confidence": 0.8,
              "rationale": "reduced_message_length_and_initiative"
            }
          ]
        }
      },
      {
        "claim_id": "claim_002",
        "kind": "interaction_pattern",
        "subject": "partner",
        "predicate": "initiation_balance",
        "predicate_type": "canonical",
        "canonical_predicate": "interaction.initiation_balance",
        "raw_predicate": "initiation_balance",
        "summary": "她最近很少主动开启话题。",
        "evidence_spans": [
          "而且最近很少主动开启话题"
        ],
        "explicitness": "explicit",
        "requires_inference": false,
        "time_kind": "interval",
        "temporal_precision": "approximate",
        "confidence": 0.9,
        "importance": 4,
        "intensity": 3,
        "valence": "negative",
        "relationship_impact": "damaging",
        "payload": {
          "metric": "initiation_balance",
          "direction": "decreasing",
          "baseline": "balanced",
          "current": "partner_low_initiative",
          "frequency": "recently",
          "channel": "text",
          "relationship_evidence": [
            {
              "dimension": "investment",
              "direction": "oppose",
              "strength": 0.5,
              "confidence": 0.8,
              "rationale": "reduced_initiative_from_partner"
            }
          ]
        }
      }
    ],
    "alignment": {
      "matches": [
        {
          "expected_index": 0,
          "actual_index": 0,
          "proposition_equivalent": true,
          "semantic_match": true,
          "evidence_support": "PASS",
          "reason": "Both state that her reply speed remains fast."
        },
        {
          "expected_index": 2,
          "actual_index": 1,
          "proposition_equivalent": true,
          "semantic_match": true,
          "evidence_support": "PASS",
          "reason": "Both state that she rarely initiates topics recently."
        }
      ],
      "unmatched_expected": [
        1
      ],
      "unmatched_actual": [],
      "over_merge_actual_indices": [
        0
      ],
      "over_split_expected_indices": [],
      "uncertain": false,
      "reason": "Actual claim 0 merges three distinct expected propositions, while actual claim 1 correctly captures the third proposition."
    }
  },
  "post_repair": {
    "claims": [
      {
        "claim_id": "claim_001",
        "kind": "interaction_pattern",
        "subject": "relationship",
        "predicate": "response_engagement",
        "object": null,
        "summary": "她回复速度很快，但消息比以前短很多，且最近很少主动开启话题。",
        "evidence_spans": [
          "她回复还是很快",
          "但消息比以前短很多",
          "而且最近很少主动开启话题"
        ],
        "time_kind": "interval",
        "occurred_at": null,
        "period_start": null,
        "period_end": null,
        "expires_at": null,
        "temporal_precision": "approximate",
        "valence": "negative",
        "relationship_impact": "damaging",
        "intensity": 3,
        "emotions": [],
        "importance": 4,
        "perspective": "user_reported",
        "confidence": 0.9,
        "payload": {
          "metric": "response_engagement",
          "direction": "decreasing",
          "baseline": "responsive",
          "current": "brief_responses",
          "frequency": "recently",
          "channel": "text",
          "relationship_evidence": [
            {
              "dimension": "investment",
              "direction": "oppose",
              "strength": 0.6,
              "confidence": 0.8,
              "rationale": "reduced_message_length_and_initiative"
            }
          ]
        },
        "supersedes_id": null,
        "raw_predicate": "response_engagement",
        "predicate_type": "canonical",
        "canonical_predicate": "interaction.response_engagement",
        "custom_predicate": null,
        "state_dimension": null,
        "state_value": null,
        "explicitness": "explicit",
        "requires_inference": false,
        "prompt_version": "memory-v2.3",
        "extractor_model": "deepseek-v4-flash",
        "verifier_model": null
      },
      {
        "claim_id": "claim_002",
        "kind": "interaction_pattern",
        "subject": "relationship",
        "predicate": "initiation_balance",
        "object": null,
        "summary": "她最近很少主动开启话题。",
        "evidence_spans": [
          "而且最近很少主动开启话题"
        ],
        "time_kind": "interval",
        "occurred_at": null,
        "period_start": null,
        "period_end": null,
        "expires_at": null,
        "temporal_precision": "approximate",
        "valence": "negative",
        "relationship_impact": "damaging",
        "intensity": 3,
        "emotions": [],
        "importance": 4,
        "perspective": "user_reported",
        "confidence": 0.9,
        "payload": {
          "metric": "initiation_balance",
          "direction": "decreasing",
          "baseline": "balanced",
          "frequency": "recently",
          "channel": "text",
          "relationship_evidence": [
            {
              "dimension": "investment",
              "direction": "oppose",
              "strength": 0.5,
              "confidence": 0.8,
              "rationale": "reduced_initiative_from_partner"
            }
          ]
        },
        "supersedes_id": null,
        "raw_predicate": "initiation_balance",
        "predicate_type": "canonical",
        "canonical_predicate": "interaction.initiation_balance",
        "custom_predicate": null,
        "state_dimension": null,
        "state_value": null,
        "explicitness": "explicit",
        "requires_inference": false,
        "prompt_version": "memory-v2.3",
        "extractor_model": "deepseek-v4-flash",
        "verifier_model": null
      }
    ],
    "alignment": {
      "matches": [],
      "unmatched_expected": [
        0,
        1,
        2
      ],
      "unmatched_actual": [
        0,
        1
      ],
      "over_merge_actual_indices": [],
      "over_split_expected_indices": [],
      "uncertain": true,
      "reason": "semantic_alignment_failed:ValueError"
    }
  }
}
```

</details>

### EXT-047

User message: 她会邀请我参加朋友聚会，也会主动把我介绍给她的朋友。

- Attribution: `OTHER`
- Causal rule: no destructive repair rule; inspect alignment or sampling
- Repair: `direct` / ``
- Invalid reasons: `none`
- Lost expected indices: `[]`
- Gained expected indices: `[0]`

<details><summary>Expected / Raw / Post-Repair diff</summary>

```json
{
  "expected_claims": [
    {
      "kind": "interaction_pattern",
      "subject": "partner",
      "perspective": "user_reported",
      "semantic_target": "partner_invites_user_to_social_gatherings",
      "evidence_spans": [
        "她会邀请我参加朋友聚会"
      ]
    },
    {
      "kind": "interaction_pattern",
      "subject": "partner",
      "perspective": "user_reported",
      "semantic_target": "partner_introduces_user_to_friends",
      "evidence_spans": [
        "也会主动把我介绍给她的朋友"
      ]
    }
  ],
  "flash_raw": {
    "claims": [
      {
        "claim_id": "claim_001",
        "kind": "interaction_pattern",
        "subject": "partner",
        "predicate": "invites_user_to_social_events",
        "predicate_type": "custom",
        "custom_predicate": "invites_user_to_social_events",
        "canonical_predicate": null,
        "raw_predicate": "invites_user_to_social_events",
        "summary": "她会邀请我参加朋友聚会，也会主动把我介绍给她的朋友。",
        "evidence_spans": [
          "她会邀请我参加朋友聚会",
          "也会主动把我介绍给她的朋友"
        ],
        "explicitness": "explicit",
        "requires_inference": false,
        "time_kind": "timeless",
        "temporal_precision": "unknown",
        "confidence": 0.95,
        "importance": 4,
        "intensity": null,
        "valence": "positive",
        "relationship_impact": "improving",
        "payload": {
          "metric": "social_inclusion",
          "frequency": "recurring",
          "participants": [
            "partner",
            "user"
          ],
          "relationship_evidence": [
            {
              "dimension": "familiarity",
              "direction": "support",
              "strength": 0.7,
              "confidence": 0.9,
              "rationale": "partner_includes_user_in_social_circle"
            },
            {
              "dimension": "trust",
              "direction": "support",
              "strength": 0.6,
              "confidence": 0.85,
              "rationale": "partner_introduces_user_to_friends"
            }
          ]
        }
      }
    ],
    "alignment": {
      "matches": [],
      "unmatched_expected": [
        0,
        1
      ],
      "unmatched_actual": [
        0
      ],
      "over_merge_actual_indices": [],
      "over_split_expected_indices": [],
      "uncertain": true,
      "reason": "semantic_alignment_failed:ValueError"
    }
  },
  "post_repair": {
    "claims": [
      {
        "claim_id": "claim_001",
        "kind": "interaction_pattern",
        "subject": "partner",
        "predicate": "invites_user_to_social_events",
        "object": null,
        "summary": "她会邀请我参加朋友聚会，也会主动把我介绍给她的朋友。",
        "evidence_spans": [
          "她会邀请我参加朋友聚会",
          "也会主动把我介绍给她的朋友"
        ],
        "time_kind": "timeless",
        "occurred_at": null,
        "period_start": null,
        "period_end": null,
        "expires_at": null,
        "temporal_precision": "unknown",
        "valence": "positive",
        "relationship_impact": "improving",
        "intensity": null,
        "emotions": [],
        "importance": 4,
        "perspective": "user_reported",
        "confidence": 0.95,
        "payload": {
          "metric": "social_inclusion",
          "frequency": "recurring",
          "participants": [
            "partner",
            "user"
          ],
          "relationship_evidence": [
            {
              "dimension": "familiarity",
              "direction": "support",
              "strength": 0.7,
              "confidence": 0.9,
              "rationale": "partner_includes_user_in_social_circle"
            },
            {
              "dimension": "trust",
              "direction": "support",
              "strength": 0.6,
              "confidence": 0.85,
              "rationale": "partner_introduces_user_to_friends"
            }
          ]
        },
        "supersedes_id": null,
        "raw_predicate": "invites_user_to_social_events",
        "predicate_type": "custom",
        "canonical_predicate": null,
        "custom_predicate": "invites_user_to_social_events",
        "state_dimension": null,
        "state_value": null,
        "explicitness": "explicit",
        "requires_inference": false,
        "prompt_version": "memory-v2.3",
        "extractor_model": "deepseek-v4-flash",
        "verifier_model": null
      }
    ],
    "alignment": {
      "matches": [
        {
          "expected_index": 0,
          "actual_index": 0,
          "proposition_equivalent": true,
          "semantic_match": true,
          "evidence_support": "PASS",
          "reason": "Both express that the partner invites the user to social gatherings; actual claim also includes introduction, but the first proposition is covered."
        }
      ],
      "unmatched_expected": [
        1
      ],
      "unmatched_actual": [],
      "over_merge_actual_indices": [
        0
      ],
      "over_split_expected_indices": [],
      "uncertain": false,
      "reason": "The single actual claim merges two independently updateable expected propositions: inviting to gatherings and introducing to friends. Both are semantically matched and supported by evidence."
    }
  }
}
```

</details>

## Subject Attribution

Automatically listed subject mismatches: `15`.

| Case | Slice | Gold | Actual | Category | Proposition |
|---|---|---|---|---|---|
| EXT-011 | interaction_event | relationship | user | EVENT_SHOULD_BE_RELATIONSHIP | partner_declined_hand_holding |
| EXT-015 | interaction_event | partner | relationship | ACTOR_AS_SUBJECT | partner_voluntarily_stayed_for_dinner |
| EXT-021 | interaction_pattern | relationship | partner | STATE_SHOULD_BE_RELATIONSHIP | message_length_decreased_while_reply_speed_stable |
| EXT-023 | user_belief | partner | user | BELIEVER_AS_SUBJECT | user_believes_partner_is_avoiding_user |
| EXT-025 | user_belief | partner | user | BELIEVER_AS_SUBJECT | user_believes_partner_may_mind_users_income |
| EXT-026 | user_belief | relationship | user | BELIEVER_AS_SUBJECT | user_believes_partner_family_may_not_accept_user |
| EXT-028 | user_belief | relationship | user | BELIEVER_AS_SUBJECT | user_believes_partner_may_have_lost_interest |
| EXT-029 | user_belief | relationship | user | BELIEVER_AS_SUBJECT | persistent_belief_partner_may_end_relationship |
| EXT-033 | relationship_state | relationship | partner | GOLD_POLICY_AMBIGUOUS | partner_relationship_status_single |
| EXT-034 | relationship_state | relationship | partner | GOLD_POLICY_AMBIGUOUS | partner_relationship_status_partnered |
| EXT-041 | plan_intent | relationship | user | GOLD_POLICY_AMBIGUOUS | planned_birthday_weekend_seaside_trip |
| EXT-042 | advice_outcome | relationship | user | OUTCOME_SHOULD_BE_RELATIONSHIP | apology_advice_led_to_partner_relaxing |
| EXT-044 | advice_outcome | relationship | partner | OUTCOME_SHOULD_BE_RELATIONSHIP | budget_transparency_advice_had_positive_partner_response |
| EXT-052 | atomization | partner | relationship | ACTOR_AS_SUBJECT | hearsay_partner_often_chats_with_another_man |
| EXT-053 | atomization | relationship | user | GOLD_POLICY_AMBIGUOUS | considering_partner_spending_values_led_to_positive_response |

Category counts: `ACTOR_AS_SUBJECT=2, BELIEVER_AS_SUBJECT=5, EVENT_SHOULD_BE_RELATIONSHIP=1, GOLD_POLICY_AMBIGUOUS=4, OUTCOME_SHOULD_BE_RELATIONSHIP=2, STATE_SHOULD_BE_RELATIONSHIP=1`.

`GOLD_POLICY_AMBIGUOUS` cases must not drive prompt optimization without a separate subject-policy decision.

## Spurious Taxonomy Review

Original spurious result: `7` cases and `8` unmatched actual claims.

| Case | Category | Summary | Evidence valid | Review reason |
|---|---|---|---|---|
| EXT-001 | SUPPORTED_EXTRA_NOT_IN_GOLD | 她尤其喜欢三文鱼寿司 | True | The salmon-sushi preference is explicitly stated by the source text. |
| EXT-005 | SUPPORTED_EXTRA_NOT_IN_GOLD | 她平时课很多。 | True | The partner's heavy course load is explicitly stated. |
| EXT-006 | SUPPORTED_EXTRA_NOT_IN_GOLD | 下次约会时想照顾她喜欢安静的习惯。 | True | The next-date accommodation intent is explicitly stated. |
| EXT-009 | UNSUPPORTED_SPURIOUS | 当前关系处于冲突状态 | True | One argument does not establish a durable current conflict state. |
| EXT-011 | SUPPORTED_EXTRA_NOT_IN_GOLD | 对方表示对牵手感到不习惯。 | True | The partner explicitly said hand-holding felt unfamiliar. |
| EXT-049 | EVALUATION_ALIGNMENT_ARTIFACT | 她回复速度很快，但消息比以前短很多，且最近很少主动开启话题。 | True | The matcher failed its one-to-one contract on an over-merged claim. |
| EXT-049 | EVALUATION_ALIGNMENT_ARTIFACT | 她最近很少主动开启话题。 | True | The matcher failed its one-to-one contract on an over-merged claim. |
| EXT-053 | UNSUPPORTED_SPURIOUS | 用户和对方一起去了平价餐厅用餐。 | True | Choosing a restaurant does not explicitly establish a completed visit. |

Case counts: `EVALUATION_ALIGNMENT_ARTIFACT=1, SUPPORTED_EXTRA_NOT_IN_GOLD=4, UNSUPPORTED_SPURIOUS=2`.

## Atomization Manual Review

- `EXT-047`: Two claims: social-gathering invitations and friend introductions are independently updateable. The observed single claim is OVER_MERGE.
- `EXT-049`: Three claims: reply speed, message length, and topic initiation are independently updateable. The observed response-engagement claim is OVER_MERGE and the message-length proposition is not atomicized.

## Minimal Remediation Boundary

- Normalize a registered canonical predicate plus duplicate custom predicate into one canonical declaration before validation.
- Convert an evidence object containing an exact text field into that text before generic schema validation.
- Treat explicit many-to-one judge pairs as an OVER_MERGE diagnostic instead of a whole-case alignment parse failure.
- Add compact structured-context, subject, and independent-updateability rules to the existing extraction prompt.

Gate, perspective policy, Strong upgrade policy, normalization ontology, admission, retrieval, relation, lifecycle, and Store are outside this review.

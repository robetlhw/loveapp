# Memory Long-tail Write V1 Policy Review

These cases are observe-only and excluded from strict V1 scoring.

| Case | Slice | Expected | Actual | Classification | Recommendation |
|---|---|---|---|---|---|
| LTW-097 | policy_review_semantic_paraphrase_identity | uncertain | uncertain | GOLD_POLICY_AMBIGUITY | NEEDS_PRODUCT_DECISION |
| LTW-098 | policy_review_custom_fact_object_change | uncertain | uncertain | ONTOLOGY_MIGRATION_ISSUE | CHANGE_RECOMMENDED |
| LTW-099 | policy_review_custom_preference_polarity | uncertain | uncertain | GOLD_POLICY_AMBIGUITY | NEEDS_PRODUCT_DECISION |
| LTW-100 | policy_review_custom_action_intent_completion | uncertain | uncertain | DOWNSTREAM_INTEGRATION_ISSUE | CHANGE_RECOMMENDED |
| LTW-101 | policy_review_custom_plan_completion | uncertain | uncertain | DOWNSTREAM_INTEGRATION_ISSUE | CHANGE_RECOMMENDED |
| LTW-102 | policy_review_event_recurrence | complementary | uncertain | EVENT_LIFECYCLE_GAP | NEEDS_PRODUCT_DECISION |
| LTW-103 | policy_review_custom_to_canonical_promotion | uncertain | uncertain | ONTOLOGY_MIGRATION_ISSUE | NEEDS_PRODUCT_DECISION |
| LTW-104 | policy_review_belief_high_confidence | contradiction | uncertain | NEEDS_PRODUCT_DECISION | KEEP_CURRENT |
| LTW-105 | policy_review_long_multi_claim_user_text | uncertain | uncertain | UPSTREAM_CONTRACT_ISSUE | CHANGE_RECOMMENDED |
| LTW-106 | policy_review_subject_alias | unrelated | uncertain | UPSTREAM_CONTRACT_ISSUE | CHANGE_RECOMMENDED |
| LTW-107 | policy_review_vague_temporal_update | uncertain | uncertain | GOLD_POLICY_AMBIGUITY | NEEDS_PRODUCT_DECISION |
| LTW-108 | policy_review_merge_vs_complement | complementary | uncertain | GOLD_POLICY_AMBIGUITY | NEEDS_PRODUCT_DECISION |
| LTW-109 | policy_review_many_related_targets | uncertain | uncertain | DOWNSTREAM_INTEGRATION_ISSUE | CHANGE_RECOMMENDED |
| LTW-110 | policy_review_event_level_lifecycle_gap | complementary | uncertain | EVENT_LIFECYCLE_GAP | NEEDS_PRODUCT_DECISION |
| LTW-111 | policy_review_custom_status_fields | uncertain | uncertain | ONTOLOGY_MIGRATION_ISSUE | NEEDS_PRODUCT_DECISION |
| LTW-112 | policy_review_canonical_custom_overlap | uncertain | uncertain | ONTOLOGY_MIGRATION_ISSUE | NEEDS_PRODUCT_DECISION |

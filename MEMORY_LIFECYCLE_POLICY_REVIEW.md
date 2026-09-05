# Memory Lifecycle V1 Policy Review

These cases are observe-only and excluded from strict baseline accuracy.
Production lifecycle policy was not changed.

| Case | Operation | Classification | Recommendation |
|---|---|---|---|
| LIFE-065 | plan_transitions | UPSTREAM_CONTRACT_ISSUE | CHANGE_RECOMMENDED |
| LIFE-066 | plan_transitions | UPSTREAM_CONTRACT_ISSUE | NEEDS_PRODUCT_DECISION |
| LIFE-067 | plan_transitions | UPSTREAM_CONTRACT_ISSUE | NEEDS_PRODUCT_DECISION |
| LIFE-068 | plan_transitions | TRANSITION_PRECEDENCE_BUG | NEEDS_PRODUCT_DECISION |
| LIFE-069 | semantic_duplicates | POLICY_SNAPSHOT_DRIFT | NEEDS_PRODUCT_DECISION |
| LIFE-070 | semantic_duplicates | SEMANTIC_DUPLICATE_BUG | CHANGE_RECOMMENDED |
| LIFE-071 | legacy_transition_targets | LEGACY_ORDERING_BUG | NEEDS_PRODUCT_DECISION |
| LIFE-072 | plan_transitions | CALLER_ACTIVE_SET_VIOLATION | UPSTREAM_CONTRACT_ISSUE |

## Per-Case Diagnostics

### LIFE-065

- Expected: `[{"rule_name": "replace_state:relationship_familiarity", "trigger_index": 0, "target_ids": ["PR65"], "target_status": "superseded"}]`
- Actual: `[{"rule_name": "replace_state:relationship_familiarity", "trigger_index": 0, "target_ids": ["PR65"], "target_status": "superseded"}]`
- Classification: `UPSTREAM_CONTRACT_ISSUE`
- Recommendation: `CHANGE_RECOMMENDED`
- Note: Without trigger_statuses, plain MemoryCandidate has status=None and can close CONFIRMED. Audit caller contract.

### LIFE-066

- Expected: `[{"rule_name": "replace_state:relationship_familiarity", "trigger_index": 0, "target_ids": ["PR66"], "target_status": "superseded"}]`
- Actual: `[{"rule_name": "replace_state:relationship_familiarity", "trigger_index": 0, "target_ids": ["PR66"], "target_status": "superseded"}]`
- Classification: `UPSTREAM_CONTRACT_ISSUE`
- Recommendation: `NEEDS_PRODUCT_DECISION`
- Note: Generic governed state transition does not check subject; normalization is expected to canonicalize relationship state subject.

### LIFE-067

- Expected: `[{"rule_name": "resolve_active_conflict", "trigger_index": 0, "target_ids": ["PR67"], "target_status": "superseded"}]`
- Actual: `[{"rule_name": "resolve_active_conflict", "trigger_index": 0, "target_ids": ["PR67"], "target_status": "superseded"}]`
- Classification: `UPSTREAM_CONTRACT_ISSUE`
- Recommendation: `NEEDS_PRODUCT_DECISION`
- Note: Semantic transition rules also do not compare subject; relationship scope/candidate normalization is the upstream boundary.

### LIFE-068

- Expected: `[{"rule_name": "resolve_active_conflict", "trigger_index": 0, "target_ids": ["PR68"], "target_status": "superseded"}]`
- Actual: `[{"rule_name": "resolve_active_conflict", "trigger_index": 0, "target_ids": ["PR68"], "target_status": "superseded"}]`
- Classification: `TRANSITION_PRECEDENCE_BUG`
- Recommendation: `NEEDS_PRODUCT_DECISION`
- Note: claimed_targets makes trigger order observable. Review whether stronger later trigger should own the target.

### LIFE-069

- Expected: `["PR69a"]`
- Actual: `["PR69a"]`
- Classification: `POLICY_SNAPSHOT_DRIFT`
- Recommendation: `NEEDS_PRODUCT_DECISION`
- Note: consumption_values_conflict is the only collapsible recent-event concept; known domain-specific debt.

### LIFE-070

- Expected: `["PR70a"]`
- Actual: `["PR70a"]`
- Classification: `SEMANTIC_DUPLICATE_BUG`
- Recommendation: `CHANGE_RECOMMENDED`
- Note: semantic_duplicate_ids groups Stable Profile by role+subject+concept, not object. Review false-collapse risk.

### LIFE-071

- Expected: `["PR71a"]`
- Actual: `["PR71a"]`
- Classification: `LEGACY_ORDERING_BUG`
- Recommendation: `NEEDS_PRODUCT_DECISION`
- Note: When occurred_at/period_end are absent, legacy ordering uses updated_at. Review whether write time is an acceptable temporal proxy.

### LIFE-072

- Expected: `[{"rule_name": "replace_state:relationship_familiarity", "trigger_index": 0, "target_ids": ["PR72"], "target_status": "superseded"}]`
- Actual: `[{"rule_name": "replace_state:relationship_familiarity", "trigger_index": 0, "target_ids": ["PR72"], "target_status": "superseded"}]`
- Classification: `CALLER_ACTIVE_SET_VIOLATION`
- Recommendation: `UPSTREAM_CONTRACT_ISSUE`
- Note: Planner does not filter target status; caller is expected to pass active PROPOSED/CONFIRMED rows only.

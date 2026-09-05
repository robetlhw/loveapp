# Memory Admission Policy Review

These eight cases are observe-only and are excluded from strict baseline accuracy.
Production policy was not changed by this evaluation.

| Case | Kind | Decision | Score | Reason | Classification | Category |
|---|---|---|---:|---|---|---|
| ADM-065 | interaction_event | confirm | 0.9000 | direct_threshold_met | NEEDS_PRODUCT_DECISION | PRODUCT_DECISION |
| ADM-066 | preference | propose | 0.1500 | valuable_but_unconfirmed | NEEDS_PRODUCT_DECISION | POLICY_CALIBRATION |
| ADM-067 | stable_fact | propose | 0.1800 | valuable_but_unconfirmed | CHANGE_RECOMMENDED | POLICY_CALIBRATION |
| ADM-068 | preference | propose | 0.3600 | valuable_but_unconfirmed | NEEDS_PRODUCT_DECISION | POLICY_CALIBRATION |
| ADM-069 | preference | confirm | 0.7500 | direct_threshold_met | UPSTREAM_CONTRACT_ISSUE | DEFENSE_IN_DEPTH |
| ADM-070 | preference | reject | 0.3500 | below_admission_threshold | NEEDS_PRODUCT_DECISION | POLICY_CALIBRATION |
| ADM-071 | action_intent | confirm | 0.8000 | direct_threshold_met | KEEP_CURRENT | DOWNSTREAM_INTEGRATION |
| ADM-072 | relationship_state | strong_review | 0.8000 | high_risk_or_ambiguous | DOWNSTREAM_INTEGRATION_ISSUE | DOWNSTREAM_INTEGRATION |

## Per-Case Diagnostics

### ADM-065

- Current decision: `confirm`
- Current score: `0.9000`
- Current reason: `direct_threshold_met`
- Current code path: `direct_requirements_met -> direct_confirm_threshold before high-risk review`
- Policy classification: `NEEDS_PRODUCT_DECISION`
- Review category: `PRODUCT_DECISION`
- Recommended policy: Review whether high-risk relationship repair events may direct-confirm.
- Risk if unchanged: A high-risk event could enter authoritative state without review.
- Risk if changed: Forcing review may delay legitimate state repair and existing lifecycle transitions.

Current score breakdown:

```json
{
  "model_confidence": 0.9,
  "explicitness": "explicit",
  "explicitness_adjustment": 0.0,
  "requires_inference": false,
  "inference_adjustment": 0.0,
  "evidence_is_source_substring": true,
  "evidence_adjustment": 0.0,
  "subject_resolved": true,
  "subject_adjustment": 0.0,
  "perspective_adjustment": 0.0,
  "conflict": false,
  "conflict_adjustment": 0.0,
  "temporal_shape_valid": true,
  "temporal_adjustment": 0.0,
  "pattern_has_frequency": false,
  "pattern_has_multiple_evidence": false,
  "pattern_adjustment": 0.0,
  "governed_transition_candidate": false,
  "governed_transition_reason": "not_evaluated",
  "score": 0.9
}
```

### ADM-066

- Current decision: `propose`
- Current score: `0.1500`
- Current reason: `valuable_but_unconfirmed`
- Current code path: `USER_BELIEF perspective penalty -> proposed_floor lowered to 0.15`
- Policy classification: `NEEDS_PRODUCT_DECISION`
- Review category: `POLICY_CALIBRATION`
- Recommended policy: Re-evaluate the very low USER_BELIEF proposed floor with downstream authority.
- Risk if unchanged: Weak beliefs may accumulate as durable context.
- Risk if changed: Raising the floor may lose useful early hypotheses.

Current score breakdown:

```json
{
  "model_confidence": 0.3,
  "explicitness": "explicit",
  "explicitness_adjustment": 0.0,
  "requires_inference": false,
  "inference_adjustment": 0.0,
  "evidence_is_source_substring": true,
  "evidence_adjustment": 0.0,
  "subject_resolved": true,
  "subject_adjustment": 0.0,
  "perspective_adjustment": -0.15,
  "conflict": false,
  "conflict_adjustment": 0.0,
  "temporal_shape_valid": true,
  "temporal_adjustment": 0.0,
  "pattern_has_frequency": false,
  "pattern_has_multiple_evidence": false,
  "pattern_adjustment": 0.0,
  "governed_transition_candidate": false,
  "governed_transition_reason": "not_evaluated",
  "score": 0.15
}
```

### ADM-067

- Current decision: `propose`
- Current score: `0.1800`
- Current reason: `valuable_but_unconfirmed`
- Current code path: `weakly_inferred adjustment + USER_BELIEF penalty -> 0.15 proposed floor`
- Policy classification: `CHANGE_RECOMMENDED`
- Review category: `POLICY_CALIBRATION`
- Recommended policy: Consider separate durable-belief and transient-speculation policy.
- Risk if unchanged: Weak inferred beliefs can enter Memory as proposals.
- Risk if changed: A stricter floor could reduce recall for evolving preferences.

Current score breakdown:

```json
{
  "model_confidence": 0.55,
  "explicitness": "weakly_inferred",
  "explicitness_adjustment": -0.22,
  "requires_inference": false,
  "inference_adjustment": 0.0,
  "evidence_is_source_substring": true,
  "evidence_adjustment": 0.0,
  "subject_resolved": true,
  "subject_adjustment": 0.0,
  "perspective_adjustment": -0.15,
  "conflict": false,
  "conflict_adjustment": 0.0,
  "temporal_shape_valid": true,
  "temporal_adjustment": 0.0,
  "pattern_has_frequency": false,
  "pattern_has_multiple_evidence": false,
  "pattern_adjustment": 0.0,
  "governed_transition_candidate": false,
  "governed_transition_reason": "not_evaluated",
  "score": 0.18
}
```

### ADM-068

- Current decision: `propose`
- Current score: `0.3600`
- Current reason: `valuable_but_unconfirmed`
- Current code path: `source_type=hearsay -> proposed_floor lowered to 0.35`
- Policy classification: `NEEDS_PRODUCT_DECISION`
- Review category: `POLICY_CALIBRATION`
- Recommended policy: Keep hearsay floor provisional pending product policy.
- Risk if unchanged: Third-party claims may be retained too readily.
- Risk if changed: Rejecting them outright loses potentially useful planning evidence.

Current score breakdown:

```json
{
  "model_confidence": 0.36,
  "explicitness": "explicit",
  "explicitness_adjustment": 0.0,
  "requires_inference": false,
  "inference_adjustment": 0.0,
  "evidence_is_source_substring": true,
  "evidence_adjustment": 0.0,
  "subject_resolved": true,
  "subject_adjustment": 0.0,
  "perspective_adjustment": 0.0,
  "conflict": false,
  "conflict_adjustment": 0.0,
  "temporal_shape_valid": true,
  "temporal_adjustment": 0.0,
  "pattern_has_frequency": false,
  "pattern_has_multiple_evidence": false,
  "pattern_adjustment": 0.0,
  "governed_transition_candidate": false,
  "governed_transition_reason": "not_evaluated",
  "score": 0.36
}
```

### ADM-069

- Current decision: `confirm`
- Current score: `0.7500`
- Current reason: `direct_threshold_met`
- Current code path: `unknown subject receives -0.05; subject resolution is not a direct-confirm guard`
- Policy classification: `UPSTREAM_CONTRACT_ISSUE`
- Review category: `DEFENSE_IN_DEPTH`
- Recommended policy: Keep upstream subject scoping and consider Admission-side defense-in-depth.
- Risk if unchanged: Unknown subjects can be confirmed if upstream filtering fails.
- Risk if changed: A second guard could reject otherwise valid out-of-scope facts.

Current score breakdown:

```json
{
  "model_confidence": 0.8,
  "explicitness": "explicit",
  "explicitness_adjustment": 0.0,
  "requires_inference": false,
  "inference_adjustment": 0.0,
  "evidence_is_source_substring": true,
  "evidence_adjustment": 0.0,
  "subject_resolved": false,
  "subject_adjustment": -0.05,
  "perspective_adjustment": 0.0,
  "conflict": false,
  "conflict_adjustment": 0.0,
  "temporal_shape_valid": true,
  "temporal_adjustment": 0.0,
  "pattern_has_frequency": false,
  "pattern_has_multiple_evidence": false,
  "pattern_adjustment": 0.0,
  "governed_transition_candidate": false,
  "governed_transition_reason": "not_evaluated",
  "score": 0.75
}
```

### ADM-070

- Current decision: `reject`
- Current score: `0.3500`
- Current reason: `below_admission_threshold`
- Current code path: `MODEL_INFERRED penalty -> normal proposed floor; boundary float resolves to reject`
- Policy classification: `NEEDS_PRODUCT_DECISION`
- Review category: `POLICY_CALIBRATION`
- Recommended policy: Review MODEL_INFERRED floor and persistence policy.
- Risk if unchanged: Inferred claims may be discarded even when useful.
- Risk if changed: Lowering the floor risks model-authored facts gaining authority.

Current score breakdown:

```json
{
  "model_confidence": 0.5,
  "explicitness": "explicit",
  "explicitness_adjustment": 0.0,
  "requires_inference": false,
  "inference_adjustment": 0.0,
  "evidence_is_source_substring": true,
  "evidence_adjustment": 0.0,
  "subject_resolved": true,
  "subject_adjustment": 0.0,
  "perspective_adjustment": -0.15,
  "conflict": false,
  "conflict_adjustment": 0.0,
  "temporal_shape_valid": true,
  "temporal_adjustment": 0.0,
  "pattern_has_frequency": false,
  "pattern_has_multiple_evidence": false,
  "pattern_adjustment": 0.0,
  "governed_transition_candidate": false,
  "governed_transition_reason": "not_evaluated",
  "score": 0.35
}
```

### ADM-071

- Current decision: `confirm`
- Current score: `0.8000`
- Current reason: `direct_threshold_met`
- Current code path: `MemoryService applies policy.default_ttl_days to expires_at before Admission`
- Policy classification: `KEEP_CURRENT`
- Review category: `DOWNSTREAM_INTEGRATION`
- Recommended policy: Keep the current MemoryService TTL consumer and add regression coverage.
- Risk if unchanged: A future wiring regression could leave intents indefinitely active.
- Risk if changed: Applying TTL universally could expire still-relevant plans.

Current score breakdown:

```json
{
  "model_confidence": 0.8,
  "explicitness": "explicit",
  "explicitness_adjustment": 0.0,
  "requires_inference": false,
  "inference_adjustment": 0.0,
  "evidence_is_source_substring": true,
  "evidence_adjustment": 0.0,
  "subject_resolved": true,
  "subject_adjustment": 0.0,
  "perspective_adjustment": 0.0,
  "conflict": false,
  "conflict_adjustment": 0.0,
  "temporal_shape_valid": true,
  "temporal_adjustment": 0.0,
  "pattern_has_frequency": false,
  "pattern_has_multiple_evidence": false,
  "pattern_adjustment": 0.0,
  "governed_transition_candidate": false,
  "governed_transition_reason": "not_evaluated",
  "score": 0.8
}
```

### ADM-072

- Current decision: `strong_review`
- Current score: `0.8000`
- Current reason: `high_risk_or_ambiguous`
- Current code path: `STRONG_REVIEW -> verifier only when MemoryService has a configured verifier`
- Policy classification: `DOWNSTREAM_INTEGRATION_ISSUE`
- Review category: `DOWNSTREAM_INTEGRATION`
- Recommended policy: Audit StrongClaimVerifier invocation and final status before policy change.
- Risk if unchanged: STRONG_REVIEW may be stored proposed without verification in some integrations.
- Risk if changed: Forcing verification can increase latency and failure surface.

Current score breakdown:

```json
{
  "model_confidence": 0.95,
  "explicitness": "explicit",
  "explicitness_adjustment": 0.0,
  "requires_inference": false,
  "inference_adjustment": 0.0,
  "evidence_is_source_substring": true,
  "evidence_adjustment": 0.0,
  "subject_resolved": true,
  "subject_adjustment": 0.0,
  "perspective_adjustment": -0.15,
  "conflict": false,
  "conflict_adjustment": 0.0,
  "temporal_shape_valid": true,
  "temporal_adjustment": 0.0,
  "pattern_has_frequency": false,
  "pattern_has_multiple_evidence": false,
  "pattern_adjustment": 0.0,
  "governed_transition_candidate": false,
  "governed_transition_reason": "not_evaluated",
  "score": 0.8
}
```

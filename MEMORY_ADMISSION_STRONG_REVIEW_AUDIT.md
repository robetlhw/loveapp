# Memory Admission STRONG_REVIEW Audit

The Admission layer returns `strong_review`; invocation of StrongClaimVerifier is a downstream MemoryService concern.

## Production Path

`MemoryService` calls StrongClaimVerifier only when `decision == STRONG_REVIEW` and a verifier instance is configured. The verifier receives selected context memories and an allowed target-id set. Validation failures fall back conservatively; a supported, sufficient verification can promote a candidate to CONFIRM subject to `_verification_can_confirm`.

Strict/review rows whose Admission decision is STRONG_REVIEW: `22`

| Case | Kind | Score | Reason |
|---|---|---:|---|
| ADM-003 | preference | 0.8000 | high_risk_or_ambiguous |
| ADM-007 | stable_fact | 0.8000 | high_risk_or_ambiguous |
| ADM-011 | interaction_event | 0.8500 | high_risk_or_ambiguous |
| ADM-015 | interaction_pattern | 0.8000 | high_risk_or_ambiguous |
| ADM-019 | planned_event | 0.9000 | high_risk_or_ambiguous |
| ADM-023 | action_intent | 0.8000 | high_risk_or_ambiguous |
| ADM-027 | advice_outcome | 0.9000 | high_risk_or_ambiguous |
| ADM-031 | relationship_state | 0.8200 | high_risk_or_ambiguous |
| ADM-038 | relationship_state | 0.7500 | high_risk_or_ambiguous |
| ADM-039 | stable_fact | 0.7500 | high_risk_or_ambiguous |
| ADM-041 | stable_fact | 0.7500 | high_risk_or_ambiguous |
| ADM-043 | preference | 0.7200 | high_risk_or_ambiguous |
| ADM-045 | preference | 0.8500 | high_risk_or_ambiguous |
| ADM-047 | stable_fact | 0.8700 | high_risk_or_ambiguous |
| ADM-048 | stable_fact | 0.7300 | high_risk_or_ambiguous |
| ADM-054 | interaction_pattern | 0.7100 | high_risk_or_ambiguous |
| ADM-056 | interaction_pattern | 0.9400 | high_risk_or_ambiguous |
| ADM-057 | relationship_state | 0.9500 | high_risk_or_ambiguous |
| ADM-058 | relationship_state | 0.9500 | high_risk_or_ambiguous |
| ADM-060 | action_intent | 0.6000 | high_risk_or_ambiguous |
| ADM-061 | action_intent | 0.5700 | high_risk_or_ambiguous |
| ADM-072 | relationship_state | 0.8000 | high_risk_or_ambiguous |

## Integration Interpretation

The baseline evaluator intentionally does not invoke StrongClaimVerifier. See the integration diagnostic for dynamic call counts. A STRONG_REVIEW assessment alone does not imply that a verifier was called or that a candidate was confirmed.

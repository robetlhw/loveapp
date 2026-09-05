# Memory Relation V1 Remediation Report

## Scope

This closeout covers the two minimal Relation V1 remediations requested by the
Relation/Lifecycle contract. Admission policy and Lifecycle production rules
were not changed in this pass.

## Root Causes and Fixes

### Contact state identity

Legacy `stable_fact` candidates carrying the canonical `contact.status`
predicate did not always include their normalized state dimension/value in the
dedupe identity. Opposite states such as `reduced` and `restored` could
therefore be classified as `SAME` before the governed contact transition path
ran.

`memory_dedupe_key()` now includes the registered state dimension and value for
this legacy contact shape. The change is scoped to `stable_fact + contact.status`
and leaves ordinary event, plan, preference, and temporal identity unchanged.

### Custom preference routing

The deterministic preference resolver was reached for open-world `CUSTOM`
preferences with no canonical predicate. Two unrelated custom values could
then be treated as a canonical preference dimension.

Only canonical preferences with a non-null canonical predicate now enter the
preference resolver. Custom preferences continue through the existing
open-world `UNCERTAIN` relation policy.

## Verification

Relation V1 uses the production `resolve_claim_relation()` directly and keeps
the baseline non-mutating. The 72-case source snapshot contains 64 strict and 8
`POLICY_REVIEW` cases.

| Check | Result |
|---|---:|
| Strict cases | 64/64 |
| Relation / rule / reason accuracy | 1.0000 |
| Target exact / set / micro F1 | 1.0000 |
| SAME keeper accuracy | 1.0000 |
| Proposed-overwrites-confirmed violations | 0 |
| Cross-subject false links | 0 |
| Relation integration cases | 14/14 |
| Relation V1 evaluator tests (`tests/test_memory_relation_v1_evaluation.py`) | 8 passed |

The remediated cases are `REL-016`, `REL-017`, `REL-028`, `REL-029`, and
`REL-051`. No strict error taxonomy entries remain. The eight policy-review
rows remain observe-only in `MEMORY_RELATION_POLICY_REVIEW.md`.

## Admission Regression

Admission was intentionally unchanged. Its existing V1 baseline and
integration reports remain the source of truth; no thresholds, verifier policy,
or Gold expectations were modified here.

## Lifecycle Boundary

Lifecycle V1 was evaluated separately through the production deterministic
functions and an isolated `InMemoryMemoryStore`. The current baseline is
`57/64` strict, with seven documented drifts in
`MEMORY_LIFECYCLE_V1_BASELINE_REPORT.md`. No Lifecycle rule or Golden Set was
changed to hide those differences.

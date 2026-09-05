# Memory Normalization V1 Final Freeze Report

Generated: `2026-09-02T19:51:55.195800+08:00`

## Freeze decision

```text
Gate = FROZEN
Extraction = STRUCTURALLY_STABLE_WITH_KNOWN_SEMANTIC_VARIANCE
Normalization = FROZEN
Next Module = Admission V1
```

The decision is scoped to the standard conversational LLM extraction ingress.
The DatePlanning deterministic preference writer is recorded as a trusted
out-of-contract exception in the write-path audit.

## 56-case regression

- Latest code rerun: `Yes`
- Cases: `56`
- Passed: `56`
- Semantic Hint Resolution: `1.0000`
- Canonical Mapping: `1.0000`
- State Dimension / Value: `1.0000` / `1.0000`
- Representation Normalization: `1.0000`
- Custom Preservation: `1.0000`
- Unsafe Canonicalization: `0.0000`
- Schema Validity: `1.0000`
- Idempotency: `1.0000`
- Conflict Outcome: `1.0000`

## Boundary contract

- Generic Validation Acceptance: `1.0000`
- False Pre-Normalization Rejection: `0.0000`
- Normalizer Recovery: `1.0000`
- Validation Boundary Reject Count: `4`
- Boundary suite: `20/20`

The boundary rejects are expected generic-invalid inputs; they are not semantic
loss. See `MEMORY_NORMALIZATION_METRIC_RECONCILIATION.md` for denominator detail.

## Production-path smoke

The smoke uses the real `OpenAICompatibleMemoryExtractor` raw parser, real
`MemoryService`, contract Normalizer, admission path, and isolated
`InMemoryMemoryStore`, with an in-process deterministic OpenAI-compatible client
and no network/API key use. Isolated writes are observed for retention evidence;
destructive external Store mutation remains disabled.

| Case | Raw claim | Generic | Normalizer | Admission | Store write | Drop stage |
|---|---|---|---|---|---|---|
| SUBJ-003 | True | accept | True | True | True | - |
| SUBJ-013 | True | accept | True | True | True | - |
| SUBJ-021 | True | accept | True | True | True | - |
| SUBJ-022 | True | accept | True | True | True | - |
| EXTRA-INITIATION | True | accept | True | True | True | - |
| EXTRA-CONFLICT | True | accept | True | True | True | - |

Smoke metrics:

- Admission reached rate: `1.0`
- Store write attempt rate: `1.0`
- Smoke status: `PASS`

`SUBJ-021` intentionally retains the two claims observed in the pressure
artifact; both reach Admission. `SUBJ-022` carries the unregistered raw value
`paused`, so safe Custom preservation is expected rather than forced canonical
state; this is not a pre-normalizer drop.

## Freeze questions answered

1. Latest 56-case rerun: **Yes, 56/56**.
2. Canonical Mapping: **1.0000**.
3. State Dimension / Value: **1.0000 / 1.0000**.
4. Custom Preservation: **1.0000**.
5. Unsafe Canonicalization: **0.0000**.
6. Idempotency: **1.0000**.
7. Generic Validator still kills semantic-valid Raw claims: **No on the boundary suite**.
8. False Pre-Normalization Reject: **0.0000 (0/16)**.
9-12. SUBJ-003/013/021/022: **all reached Admission boundary**.
13. Reachable legacy validator: **None on standard LLM extraction ingress**;
    trusted DatePlanning preference bypass is documented separately.
14. Historical 16 vs current 2: **different stage definitions; boundary 4 also
    uses a different dataset; not directly comparable**.
15. Normalization V1: **FROZEN for the scoped LLM claim path**.

## Remaining known limitations

- Date preference deterministic ingress bypasses the claim normalization boundary.
- Extraction retains known semantic variance, especially belief subject/perspective
  and focused long-tail recall; no Prompt change was made here.
- Admission/relation/lifecycle behavior is outside this Normalization freeze.

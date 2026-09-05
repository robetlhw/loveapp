# Memory Normalization Metric Reconciliation

## Comparable final metrics

| Metric | Final | Numerator | Denominator | Comparable | Pass |
|---|---:|---:|---:|---|---|
| Canonical Mapping | 1.0000 | 13 | 13 | Yes | Yes |
| State Dimension | 1.0000 | 13 | 13 | Yes | Yes |
| State Value | 1.0000 | 13 | 13 | Yes | Yes |
| Custom Preservation | 1.0000 | 21 | 21 | Yes | Yes |
| Unsafe Canonicalization | 0.0000 | 0 | 21 | Yes | Yes |
| Schema Validity | 1.0000 | 56 | 56 | Yes | Yes |
| Idempotency | 1.0000 | 5 | 5 | Yes | Yes |
| Generic Validation Acceptance | 1.0000 | 16 | 16 | Yes, boundary set | Yes |
| False Pre-Normalization Reject | 0.0000 | 0 | 16 | Yes, boundary set | Yes |
| Normalizer Recovery | 1.0000 | 10 | 10 | Yes, boundary set | Yes |

## Why historical 16, current 2, and boundary 4 differ

The historical `16` was reported over the 56-case Normalization dataset using the
older combined generic + canonical/state validator before normalization. Re-running
that legacy function against the current 56-case input reproduces the same count
(`NORM-001..003, NORM-018, NORM-023..027, NORM-047..048, NORM-052..056`). The
current `2` is from the same 56-case dataset but counts only the migrated generic
validation diagnostic (NORM-018 and NORM-023 are generic atomicity-invalid cases).
The boundary `4` is from a separate 20-case boundary dataset (BND-013..BND-016)
and counts expected generic-invalid inputs.

Thus the three values have different stage definitions and, for `4`, a different
dataset and denominator. They are **not directly comparable**; no 16-to-4 reduction
claim is made. The stable safety metric is False Pre-Normalization Rejection =
0.0000 on the dedicated boundary denominator.

## Denominator ledger

- 56-case final normalization regression: `56` cases.
- 20-case validation boundary: `20` cases; 16 semantic-valid,
  4 expected generic-invalid.
- Historical 16: `56` cases, old combined validator stage.
- Current 2: `56` cases, migrated generic diagnostic stage.

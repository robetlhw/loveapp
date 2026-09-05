# Memory Normalization Contract Reconciliation

Date: `2026-09-02`

## Root cause

The previous path validated semantic completeness before deterministic normalization:

```text
Raw model claim -> validate_memory_claim (canonical/metric/state completeness) -> Normalizer
```

This caused valid raw claims with missing or non-authoritative hints to be discarded before the
Normalizer could choose a registered representation or safe Custom fallback. Authority was also
split among the extractor, repair layer, predicate adapter, and lifecycle normalizer.

## Reconciled contract (Option C)

```text
Extractor
  -> Raw claim + non-authoritative semantic hints
  -> Generic ingress validation
  -> Deterministic Normalizer
  -> Canonical/normalized validator
  -> Admission / Relation / Lifecycle
```

Option C is selected because it preserves the current deterministic, no-second-LLM architecture
while making semantic hints useful without treating them as canonical truth. Unsafe or ambiguous
hints fall back to Custom or are rejected by the post-normalization validator.

## Field ownership

| Field | Authoritative owner | Earlier layers may provide |
|---|---|---|
| `raw_predicate` | Normalizer output, retaining model wording | Raw extractor |
| `payload.metric` | Deterministic Normalizer | `metric_hint` / model metric |
| `preference_type` | Deterministic Normalizer and preference-domain validator | `preference_type_hint` |
| `state_dimension`, `state_value` | Deterministic Normalizer using the single state registry | `state_dimension_hint`, `state_value_hint` |
| `canonical_predicate` | Post-normalization canonical validator | Model proposal / alias hint |

Generic validation checks only JSON shape, required generic fields, enum/range validity, evidence
shape, subject/perspective types, and atomicity structure. It does not require canonical mapping,
metric completeness, or registered state shape before normalization.

The post-normalization validator checks registered canonical predicates, canonical/custom mutual
exclusion, registered state dimensions and values, metric validity, preference domain validity,
and the final output schema.

## Conflict and namespace decisions

Canonical + unrelated Custom declarations are rejected on every ingress path. Equivalent duplicate
declarations may be reconciled. The authoritative state namespace is the lifecycle policy
namespace (`conflict_status`, `relationship_familiarity`, `contact_availability`, etc.); dotted
predicate names remain predicate identifiers only and are never copied as a second state namespace.

Safety invariants remain mandatory: Custom Preservation >= 0.95 (target 1.0), Unsafe
Canonicalization <= 0.05 (target 0), and Idempotency >= 0.98 (target 1.0).


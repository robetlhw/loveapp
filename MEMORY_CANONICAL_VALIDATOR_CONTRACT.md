# Memory Canonical Validator Contract

The Canonical/Normalized validator runs after deterministic normalization.  It
owns the final representation contract and must fail closed when the output
cannot be governed safely.

## Final Representation

A normalized claim must contain exactly one of:

- a registered `canonical_predicate`;
- a non-empty `custom_predicate`.

Canonical and Custom representations cannot coexist unless the deterministic
normalizer reconciles equivalent duplicate declarations first.

Interaction patterns require a registered `payload.metric`.  Relationship
states require a lifecycle-canonical `state_dimension` and compatible
`state_value`, mirrored consistently in the payload.  Unknown state semantics
may be retained as Custom when no safe canonical mapping exists; they must not
be silently dropped or fabricated into a registered state.

## Failure Taxonomy

Post-normalization failures remain distinct from generic ingress failures:

- `CANONICAL_UNREGISTERED`
- `STATE_VALUE_INVALID`
- `UNKNOWN_STATE_DIMENSION`
- `CANONICAL_CUSTOM_CONFLICT`
- `NORMALIZATION_UNRESOLVED`
- `SCHEMA_INVALID`

The validator does not perform fuzzy nearest-neighbor mapping, infer missing
state values from unrelated evidence, or mutate a Memory Store.

## Governing Sequence

```text
Raw Claim
  -> Generic Validator
  -> Deterministic Normalizer
  -> Canonical/Normalized Validator
  -> Admission / Relation / Lifecycle / Store
```

This separation makes a failure attributable to the layer that first loses
semantic information and preserves the existing Custom fallback policy.

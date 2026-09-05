# Memory Generic Validator Contract

This document defines the ingress boundary for a Raw Memory claim.  The
generic validator answers only whether the claim is structurally usable and
semantically grounded enough to reach deterministic normalization.

## Accepted Raw Shape

Raw claims may contain a free-form `raw_predicate` and non-authoritative
semantic hints:

- `metric_hint`
- `preference_type_hint`
- `state_dimension_hint`
- `state_value_hint`

They do not need a registered canonical predicate, registered metric, or
complete lifecycle state at this stage.

The validator checks required field shape, `kind`, `subject`, `perspective`,
confidence bounds, evidence presence/grounding, summary type, temporal shape,
atomicity, and generic payload shape.  Hint values are checked only for basic
type and non-empty shape.

## Explicitly Deferred

These checks belong to deterministic normalization or the post-normalization
validator, not Raw ingress:

- canonical predicate registration;
- metric registration;
- state dimension/value registration and compatibility;
- preference-domain canonicalization;
- canonical/custom final representation conflicts.

An unknown but structurally valid semantic claim must reach the normalizer so
it can be mapped safely to a registered canonical form, preserved as Custom,
or rejected with a precise post-normalization diagnostic.

## Failure Taxonomy

Generic failures are reported as `GENERIC_SCHEMA_INVALID`,
`GENERIC_EVIDENCE_INVALID`, or `GENERIC_ENUM_INVALID`.  A semantic-valid Raw
claim rejected here is a `FALSE_PRE_NORMALIZATION_REJECT` and must be counted
separately from a post-normalization contract failure.

## Boundary Trace

Every boundary evaluation row records the Raw claim, generic validation result,
normalizer input, normalizer output, canonical validation result, final claim,
drop stage, and drop reason.  This trace is observational and does not write to
the Memory Store.

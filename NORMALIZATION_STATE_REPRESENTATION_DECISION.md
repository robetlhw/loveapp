# Normalization State Representation Decision

Date: `2026-09-02`

## Decision

Use one authoritative top-level state namespace: the registered lifecycle policy dimensions and
values (`conflict_status`, `contact_availability`, `relationship_familiarity`,
`partner_relationship_status`, and the other existing policy names). `state_dimension` and
`state_value` on the normalized candidate and its payload use the same normalized pair.

Canonical dotted predicate names such as `relationship.conflict_status` and `contact.status` remain
predicate identifiers. They are mapped to the policy namespace exactly once; they do not create a
parallel dotted state representation.

## Rationale

Lifecycle identity and TTL policies already consume the policy namespace. Aligning Normalization to
that registry avoids evaluator-dependent representation selection and prevents a normalized claim
from carrying incompatible top-level and payload dimensions. Existing canonical predicate names,
aliases, and Store fields remain unchanged.

## Hint examples

```text
state_dimension_hint=relationship_conflict_status
state_value_hint=unresolved
-> state_dimension=conflict_status, state_value=active
```

Unknown or incompatible hints do not get mapped to a nearest state. They remain Custom or are
rejected by the canonical validator according to the final claim shape.


# Memory Normalization V1 Contract Resolution

The external Golden specification remains the semantic source of truth. `CONTRACT_VERIFY` only
resolves exact repository identifiers or deterministic invalid-shape behavior.
Each resolution below is embedded directly in the corresponding JSONL row as
`contract_resolution`; the evaluator validates it against this reviewed ledger and does not inject
an alternate executable Gold at runtime.

| Case | Spec semantic | Repo contract | Executable Gold | Reason |
|---|---|---|---|---|
| NORM-005 | environment crowding preference | no crowding canonical | Custom `dislikes_crowded_places` | Noise is narrower and not equivalent to crowding. |
| NORM-006 | low date budget preference | `preference.budget.range` exists | Canonical `preference.budget.range`; preserve `budget_max_cny=300` | The registry has an exact budget domain. |
| NORM-007 | beverage sugar preference | no beverage/sugar canonical | Custom `coffee_without_sugar` | Cuisine and spiciness are incompatible substitutes. |
| NORM-008 | allergy / food restriction | no allergy/restriction canonical | Custom `has_peanut_allergy` | A stable allergy fact must not be forced into preference ontology. |
| NORM-009 | education background | no education canonical | Custom `architecture_graduate_student` | No exact registered concept exists. |
| NORM-010 | planned shared event | no shared-event canonical predicate | Custom `birthday_weekend_seaside_trip`; preserve `event_status=planned` | `plan.status` is a state dimension, not an event concept. |
| NORM-029 | sushi and Japanese cuisine preference | `preference.food.cuisine` exists | Canonical `preference.food.cuisine` | The claim explicitly includes the cuisine and a compatible item; it must not map to another food dimension. |
| NORM-052 | unrelated canonical/custom dual declaration | ingress declarations are mutually exclusive | Reject with `CANONICAL_CUSTOM_CONFLICT` | Unrelated Custom meaning cannot be silently discarded. |
| NORM-053 | duplicate canonical/custom surface | bounded equivalent reconciliation exists | Canonical `interaction.initiation_balance`, Custom cleared | The executable fixture supplies the shared interaction kind and metric needed to prove equivalence. |

Missing ontology for NORM-005, NORM-007, NORM-008, NORM-009, and NORM-010 is recorded as a
contract limitation, not repaired in this baseline task. NORM-006 and NORM-029 remain canonical Gold
even if the current implementation misses or misroutes them.

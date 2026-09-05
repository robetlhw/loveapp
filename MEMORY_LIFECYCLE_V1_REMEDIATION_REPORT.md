# Memory Lifecycle V1 Remediation Report

Date: 2026-09-03

Scope: the seven Lifecycle V1 strict drifts identified by the remediation
prompt. No Golden fixture, Admission policy, Relation policy, Gate,
Extraction, Normalization, Retrieval, Store contract, Router, or DatePlan
production code was changed for this remediation.

## Outcome

Lifecycle V1 now satisfies the complete strict contract:

| Measure | Result |
|---|---:|
| Strict cases | 64 / 64 |
| Plan transitions | 40 / 40 |
| Semantic duplicates | 16 / 16 |
| Legacy transition targets | 8 / 8 |
| Plan target precision / recall / F1 | 1.0000 / 1.0000 / 1.0000 |
| Rule precedence accuracy | 1.0000 |
| Safety violations | 0 |
| Isolated integration cases | 19 / 19 |

The resulting baseline status is `ENGINEERING_FROZEN_WITH_KNOWN_POLICY_DEBT`.
The evaluator still sets `production_store_mutation_permitted=false` and uses
only an isolated in-memory Store for its integration exercise.

## Root Cause And Fix

### LIFE-005 / LIFE-006 / LIFE-007: interaction concept mapping

The interaction metric branch of `normalize_candidate_predicate()` derives a
state from `current`, `direction`, or `frequency`. These fixtures (and some
legacy callers) supplied the equivalent value in the typed top-level or
payload `state_value`. The shared normalizer therefore returned a canonical
interaction predicate with no state value. `memory_concept()` fell back to a
generic `state:...` concept, so no response-restoration rule matched.

`memory_concept()` now recovers an interaction state value at the Lifecycle
boundary, in this order: top-level `state_value`, payload `state_value`,
`current`, `direction`, then `frequency`. Values are normalized through the
existing `normalize_interaction_state_value()` helper. This yields
`response_restored` for response engagement and `contact_restored` for contact
frequency, without changing the shared Normalization contract.

### LIFE-014: specialized rule precedence

Both the incoming and target claims were explicit `contact.status` states in
the `contact_availability` dimension. The broad `restore_contact_frequency`
rule claimed the target before generic same-dimension replacement, producing
the wrong audit rule even though the target was correct.

The planner now applies a representation guard to that specialized rule. It
remains eligible when either side is an interaction contact-frequency metric,
or when a legacy dimensionless contact-status surface is involved. It is
skipped when both sides are explicit `contact.status` states, allowing
`replace_state:contact_availability` to own the transition. Existing
cross-representation contact-frequency behavior remains unchanged.

### LIFE-018 / LIFE-019: interaction rule ownership

The same state-value loss caused these cases to fall through to generic
replacement. After the Lifecycle boundary recovers the typed interaction
state, the specific rules run before the generic loop:

* response engagement -> `restore_response_engagement`
* contact frequency -> `restore_contact_frequency`

### LIFE-062: legacy temporal ordering

The old comparison independently selected the first available timestamp on
each row. It could compare a target `updated_at` with a trigger
`occurred_at`, reversing the persisted business order. The new helper chooses
one field for both rows: common `occurred_at`, then common `period_end`, then
`updated_at` for both. The comparison remains strict `<`; equal timestamps do
not transition.

## Algorithm

Before:

1. Normalize each candidate through the generic predicate normalizer.
2. Map the normalized shape to a Lifecycle concept.
3. Iterate semantic rules and claim matching targets.
4. Run generic governed-state replacement for unclaimed targets.
5. For legacy ordering, independently choose each row's first available time.

After:

1. Normalize the candidate and recover typed interaction state at the
   Lifecycle boundary when the shared normalizer omitted it.
2. Map the resulting canonical concept.
3. Evaluate specialized semantic rules with their representation guard.
4. Run generic same-dimension replacement only for unclaimed targets.
5. For legacy ordering, compare a single common timestamp field and require
   strict older-than ordering.

## Focused Replay

| Case | Trigger concept / rule | Target result |
|---|---|---|
| LIFE-005 | `response_restored` / `restore_response_engagement` | `M5` superseded |
| LIFE-006 | `response_restored` / `restore_response_engagement` | `M6` superseded |
| LIFE-007 | `response_restored` / `restore_response_engagement` | `M7a`, `M7b` superseded |
| LIFE-014 | `contact_restored` / `replace_state:contact_availability` | `M14` superseded |
| LIFE-018 | `response_restored` / `restore_response_engagement` | `M18` superseded |
| LIFE-019 | `contact_restored` / `restore_contact_frequency` | `M19` superseded |
| LIFE-062 | repair trigger with equal persisted `updated_at` basis | no target |

The input fixture snapshots remain read-only in every evaluated case.

## Integration

The isolated integration sample was expanded from 16 to 19 cases to include
LIFE-018, LIFE-019, and LIFE-062. Results:

* full plan contract: `19 / 19`
* expected Store outcome: `19 / 19`
* isolated write batches applied: `19 / 19`
* status-transition rows: `16`
* transition audits: `18` (no-op cases intentionally have no audit)
* production Store mutation: `false`

LIFE-062 is treated as an exercised successful no-op; an empty batch is not
classified as an integration failure.

## Verification

* Lifecycle files (`test_memory_lifecycle*.py`): `37 passed`
* Lifecycle, alignment, semantic remediation, and canonical transition focus:
  `61 passed`
* Relation/Admission selected regression set: `60 passed`
* Memory test set (`test_memory*.py`): `835 passed`
* Full repository: `1448 passed, 1 failed`
* Full-suite failure is unrelated `tests/test_date_phase_b5_1.py::test_exact_postponed_activation_scenario_builds_full_plan`: current date parsing resolves "this Saturday" to `2026-09-05`, while the test has the stale fixed expectation `2026-08-29`.
* Ruff for the Lifecycle implementation, evaluator, and Lifecycle evaluator
  tests: passed.
* `git diff --check`: passed.

Generated artifacts:

* `MEMORY_LIFECYCLE_V1_BASELINE_REPORT.md`
* `MEMORY_LIFECYCLE_POLICY_REVIEW.md`
* `MEMORY_LIFECYCLE_V1_INTEGRATION_DIAGNOSTIC.md`
* `.data/evals/memory_lifecycle_v1_baseline.json`
* `.data/evals/memory_lifecycle_v1_integration.json`

## Freeze And Remaining Limitations

Lifecycle V1 meets the strict engineering contract and is frozen with known
policy debt. The eight `POLICY_REVIEW` cases remain observe-only caller or
product-policy questions; they were not silently changed to reach 64/64.

This remediation does not add a new ontology or enum, does not implement a
recurrence/episode model, and does not change subject canonicalization. The
legacy ordering fallback necessarily uses `updated_at` when no common event
time exists. The unrelated pre-existing `interaction.emotional_disclosure`
working-tree line is not part of this remediation.

No commit or push was performed. The repository remains a pre-existing dirty
working tree; unrelated user changes were preserved.

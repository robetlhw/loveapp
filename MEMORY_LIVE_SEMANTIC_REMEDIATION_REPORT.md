# Memory Live Semantic Remediation Report

## Scope

This remediation addresses four live end-to-end gaps:

1. Canonical current-state precedence in `RelationshipContext`.
2. Contact outage/restoration lifecycle alignment across model surface variants.
3. Social/family integration over-canonicalization and unsafe dedupe.
4. Live evaluator semantic assertions distinct from execution success.

It does not add a long-tail relation resolver, a new persistent ontology, a Store
API, or Strong verifier behavior.

## Changed Files

- `src/loveapp/domain/relationship_evidence.py`
  - Selects the authoritative active canonical state per evidence dimension.
  - Gives confirmed state precedence over newer proposed state.
  - Preserves the exact canonical state in the Context projection instead of
    re-inferring it from generic evidence strength.
- `src/loveapp/domain/memory_lifecycle.py`
  - Groups contact outage surface values into stable lifecycle concepts.
  - Aligns contact restoration across canonical representations.
  - Uses claim-local evidence for outage/restoration guards.
  - Demotes social/family integration claims from over-broad canonical states to
    guarded CUSTOM predicates with deterministic aspect and stance objects.
  - Promotes only direct, non-speculative integration assertions to explicit
    evidence without changing model confidence.
- `src/loveapp/application/memory_repair.py`
  - Allows narrowly recognized open-world social/family relationship claims to
    pass extraction validation as CUSTOM facts.
- `scripts/evaluate_memory_foundation_live.py`
  - Separates `execution_status` from `semantic_status`.
  - Loads a dataset-bound declarative semantic fixture.
  - Evaluates Gate, normalized claims, perspective, relation, lifecycle,
    current Context, stale states, duplicates, and confirmed protection.
  - Supports recursive `$any` selectors for valid model representation variants.
- `evals/memory/cases_v1_live_expectations.json`
  - Defines live semantic expectations for MEM-001 through MEM-018 and is bound
    to the deterministic dataset SHA256.
- `tests/test_memory_foundation_live_script.py`
- `tests/test_memory_live_semantic_remediation.py`
- `tests/test_relationship_evidence.py`
- `tests/test_memory_historical_retrieval.py`
- `tests/test_memory_lifecycle_alignment.py`

No persistent Memory schema, ontology enum, Store contract, prompt, or Strong
verifier implementation was changed.

## Root Causes

### Canonical State vs Generic Evidence

The Store correctly held `relationship.conflict_status=resolved`, but generic
`relationship_evidence` from the same claim could be scored as evidence that a
conflict remained active. Context projection re-derived the state from evidence
instead of treating the governed canonical current state as authoritative.

The fixed order is:

```text
confirmed active canonical state
  > proposed active canonical state
  > generic evidence projection fallback
```

The authoritative value is carried into the projection directly, including
`active`, `cooling`, `repairing`, and `resolved`.

### Contact Restoration Family

Lifecycle matching depended on a small set of exact state strings. Real Flash
outputs included values such as `no_response_3_days`, `no_reply_for_3_days`,
`none`, and equivalent response/contact predicates. Valid restoration could
therefore leave an outage active.

The fix recognizes bounded outage surface families and aligns restoration
across `contact.status`, `interaction.contact_frequency`, and
`interaction.response_engagement`. Ordinary low offline contact opportunity is
kept independent. Guards read the claim's evidence spans rather than unrelated
words elsewhere in a multi-claim turn. Proposed state still cannot close a
confirmed state.

### Social/Family Over-Canonicalization

Friend introduction, social activity inclusion, and family introduction were
being compressed into canonical familiarity or interaction-frequency states.
Once demoted to CUSTOM, positive and negative claims could still share the same
dedupe identity because both had an unknown object.

The fix uses:

```text
social_circle_integration
family_integration
```

with deterministic open-world objects that preserve the relevant aspect and
stance, for example `introduction_included`, `participation_restricted`, or the
broader `included`/`restricted` when a claim combines aspects. Distinct facts
coexist under UNCERTAIN/non-destructive governance.

### Live Evaluator False Confidence

The previous report treated successful execution as case success. It could
therefore report no failed cases while Store or Context semantics were wrong.
The evaluator now reports execution and semantics independently and persists
each assertion. Recursive `$any` alternatives express valid Flash output forms
without allowing predicate/value cross-products.

## Key Case Results

### MEM-001

Before: the Store held resolved conflict, while Context projected active.

After:

- old `relationship.conflict_status=active`: `SUPERSEDED`
- new `relationship.conflict_status=resolved`: `CONFIRMED`
- Context `conflict_status`: `resolved`
- semantic status: `PASS`

### MEM-003

Before: restoration could coexist with a stale outage or low contact state.

After:

- old `interaction.response_engagement=no_response`: `SUPERSEDED`
- restoration/normal response: `CONFIRMED`
- no active outage or stale low opportunity in current Context
- semantic status: `PASS`

### MEM-015

Before: friend and family integration could be merged into one familiarity
state, and a direct low-confidence friend-introduction fact could be rejected.

After:

- `social_circle_integration`: active `CONFIRMED`
- `family_integration`: active `CONFIRMED`
- neither is canonicalized as `relationship.familiarity`
- semantic status: `PASS`

### MEM-018

After:

- historical active conflict remains available as `SUPERSEDED`
- current resolved conflict is `CONFIRMED`
- CURRENT Context contains resolved and excludes active
- semantic status: `PASS`

## Deterministic Regression

Command:

```text
uv run loveapp eval memory-foundation
```

Result:

```text
18 / 18 cases passed
34 turns
canonical_match_rate = 1.0
relation_accuracy = 1.0
lifecycle_success_rate = 1.0
stale_active_memory_count = 0
duplicate_active_memory_count = 0
confirmed_overwrite_violation_count = 0
```

Report:

`D:\project\loveapp\.data\evals\memory_foundation_live_semantic_remediation_deterministic_final_v2.json`

## Live Flash Regression

Final full run:

```text
execution: 18 passed / 0 failed
semantics: 17 passed / 1 warning / 0 failed / 0 not evaluated
turns: 34
Flash calls: 32
semantic assertions: 184
canonical_match_rate = 1.0
perspective_match_rate = 1.0
relation_match_rate = 1.0
context_match_rate = 1.0
lifecycle_match_rate = 0.9825
stale_active_memory_count = 0
duplicate_active_memory_count = 0
confirmed_overwrite_violation_count = 0
p50 Flash latency = 2243.63 ms
p95 Flash latency = 3968.72 ms
```

The one warning is MEM-014 atomic extraction granularity. It is intentionally a
quality warning rather than a semantic failure.

Report:

`D:\project\loveapp\.data\evals\memory_foundation_live_semantic_remediation_full_final_v2.json`

## Automated Verification

```text
focused remediation tests: 67 passed
all Memory-related tests: 460 passed, 527 deselected
full repository: 986 passed, 1 failed
Ruff on changed Python files: passed
git diff --check on task files: passed
```

The sole repository failure is unrelated to Memory. The date-planning test
`test_exact_postponed_activation_scenario_builds_full_plan` hard-codes
`2026-08-29` for "this Saturday", while the current reference time resolves it
to `2026-09-05`. Date production code and its test were intentionally left out
of scope.

## Remaining Known Limitations

- Long-tail semantic UPDATE/CONTRADICTION resolution remains intentionally
  unimplemented for open-world CUSTOM facts.
- MEM-014 can still combine ideal atomic facts; this remains a non-blocking
  extraction-quality warning.
- Live Flash output is nondeterministic. The evaluator now exposes transport,
  schema, execution, and semantic failures separately rather than masking them.
- Strong verifier latency and reliability were not changed or measured in this
  Flash-only run.

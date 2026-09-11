# Memory V1.4 Semantic Decomposer Refactor

## Scope

This change upgrades the existing two-stage extractor from a semantic splitter
to a bounded semantic decomposer. It does not change Gate, relation/lifecycle
governance, Store APIs, or mutation authority.

## Changes

- Added bounded Stage 1 `relation_hint` values: `none`, `same_event`,
  `cause`, `context`, `support`, `contrast`, and `correction`.
- Added `occurrence_group`, `operation_hint` (`new_like`, `enrich_like`,
  `refine_like`, `unknown`), and `answered_questions`.
- Preserved `same_occurrence_group` and `answered_pending_questions` as
  compatible input/output aliases for V1.3 payloads.
- Carried the new semantic metadata through `SemanticUnitProvenance` without
  adding target IDs or mutation commands.
- Added an explicit read-only Stage 1 -> `HybridMemoryRetriever` -> Stage 2
  boundary. Retrieval is bounded to five candidates and only supplies context;
  it cannot select a resolver target.
- Updated the two-stage prompt contract/version to V1.4. State, pattern, and
  belief remain represented by kind/epistemic dimensions rather than operation
  labels. Legacy role values remain parseable for stored diagnostics.
- Extended the context-aware evaluator with decomposer hint telemetry and
  `event_composition_accuracy`, `state_projection_consistency`, and
  `resolver_accuracy` metrics.

## Verification

- V1.4 semantic decomposer regressions: **5 passed**.
- V1.3 semantic ontology, context-aware extraction, Stage 2 routing and
  two-stage extractor tests: **102 passed**.
- Memory test set: **1353 passed, 3 existing long-tail baseline failures**.
- Full repository: **2120 passed, 3 existing long-tail baseline failures**.
- Ruff on changed files: **passed**.
- `compileall`: **passed**.
- `git diff --check`: **passed**.

The three baseline failures are unchanged expectation drift in the existing
long-tail relation, realistic retrieval, and write-v1 evaluation suites. No
expectation or fixture was changed for V1.4.

## Compatibility and limits

No domain/schema migration, Store API, resolver, lifecycle rule, or additional
LLM call was added. Stage 1 hints remain non-authoritative. Retrieval is only a
candidate provider, and multi-target mutation, complex event grouping, and
real-model semantic quality remain downstream/operational limitations.

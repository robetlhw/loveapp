# Memory Event Enrichment MVP Remediation Report

Generated: 2026-09-10

## Outcome

The bounded Event Enrichment MVP is implemented and demonstrable. Extraction now
produces untrusted semantic drafts, target resolution remains deterministic and
fail-closed, and validated Event enrichments use the existing atomic write batch and
Store audit path. The supported target domain is deliberately limited to
`interaction_event` memories whose EventType is `conflict`, `date`, or
`shared_activity`.

`single_stage` remains the production default. The new TwoStage contract is opt-in and
is not ready to replace SingleStage on broad ontology quality.

## Architecture

The implemented flow is:

```text
TwoStage extraction
  -> SemanticAtomicExtraction
     -> NewMemoryDraft -> lossless AtomicClaim compatibility path
     -> EnrichmentDraft -> deterministic EventEnrichmentResolver
     -> RefinementDraft -> audit-only trace
  -> MemoryWriteBatch.event_enrichments
  -> existing InMemory/SQLite atomic Store transaction
  -> typed non-destructive payload update + transition audit
```

The extraction layer cannot authorize a database mutation. `target_semantic_hint` is
untrusted, and both trusted memory IDs and mutation directives are rejected by schema.
The resolver discovers semantic candidates before applying compatibility checks, so an
incompatible candidate cannot be filtered away to manufacture false uniqueness.

## Contracts Added

- `NewMemoryDraft`: a new proposition with `semantic_payload` and a lossless adapter to
  the frozen `AtomicClaim` contract.
- `EnrichmentDraft`: an untrusted proposal for one Event attribute. It cannot carry a
  trusted target ID or write action.
- `RefinementDraft`: recognized and audited, but deliberately not written in this MVP.
- `SemanticAtomicExtraction`: discriminated semantic-unit output while retaining legacy
  `claims` compatibility and negative semantic-Gate invariants.
- `EventEnrichmentResolution`: observable semantic candidates, compatible candidates,
  rejected candidates, selected target, and deterministic reason.
- `CustomEventAttribute`: typed value plus evidence, source message, confidence, and
  creation time.

The existing `MemoryWriteBatch.event_enrichments` and Store API were reused. No new
Store API or arbitrary payload patch mechanism was introduced.

## Supported Enrichment Matrix

| EventType | Canonical fields |
|---|---|
| `conflict` | `cause`, `severity`, `emotion`, `resolution`, `outcome` |
| `date` | `location`, `activity_type`, `emotion`, `outcome` |
| `shared_activity` | `location`, `activity_type`, `emotion`, `outcome` |

The only open-world attribute enabled in V1 is `weather`, and only for `date` and
`shared_activity`. Custom attributes solve bounded schema coverage; they never relax
event identity or target resolution.

## Target Safety

The resolver permits only source-linked, context-linked, or pending-slot-linked
candidates in the current user and relationship scope:

```text
0 semantic targets  -> reject
1 semantic target   -> compatibility validation
2+ semantic targets -> ambiguous_semantic_event_antecedent -> reject
```

It then validates active status, Event kind/type, subject, field compatibility,
evidence presence, confirmed user-reported perspective, confidence, and existing field
value. A new bounded occurrence is rejected from enrichment and must continue through
the normal new-memory path. There is no recency or embedding top-1 fallback.

Both resolver and Store enforce EventType-by-field compatibility. A different existing
canonical or custom value cannot be silently overwritten. Replays with the same source
are idempotent, and `enrichment_history` retains provenance.

## Evaluation Results

### Deterministic Event Enrichment Shadow Set

Dataset: `evals/memory/event_enrichment_mvp_v0_1.jsonl`

| Metric | Result |
|---|---:|
| Cases | 32/32 passed |
| Enrichment target accuracy | 1.0000 |
| Ambiguous target rejection rate | 1.0000 |
| False enrichment count | 0 |
| False enrichment rate | 0.0000 |
| Canonical field passes | 13 |
| Custom attribute passes | 3 |
| Store mutation permitted | False |

This is a semantic-draft fixture and deterministic resolver evaluation. Its zero false
enrichment rate is not presented as a live-model production metric.

### Real TwoStage Smoke

Model: `deepseek-v4-flash`

| Metric | Result |
|---|---:|
| Stage 1 | 18/18 |
| Stage 2 | 17/17 (one negative Gate skips Stage 2) |
| Native TwoStage completion | 18/18 |
| Fallback | 0/18 |
| Legacy multi-claim cases | 3/3 |
| Semantic operation cases S10-S15 | 6/6 |

Observed S10-S15 operations were:

| Case | Semantic output |
|---|---|
| S10 | `EnrichmentDraft(cause)` |
| S11 | `EnrichmentDraft(emotion)` |
| S12 | two enrichments (`cause`, `emotion`) plus one new relationship state |
| S13 | `NewMemoryDraft`, not enrichment |
| S14 | `RefinementDraft` (trace-only) |
| S15 | `NewMemoryDraft`, not enrichment/refinement |

### Frozen Ontology Gold Diagnostic

The same unmodified 40-case draft Gold was run against both extraction modes. Store
mutation was disabled in both runs.

| Mode | Extraction | Normalized | Technical model failures |
|---|---:|---:|---:|
| SingleStage default | 27/40 (0.6750) | 28/40 (0.7000) | 0 |
| TwoStage opt-in | 13/40 (0.3250) | 15/40 (0.3750) | 3 |

These live-model results are diagnostic and can vary between calls. The default
SingleStage implementation and frozen Gold were not modified. The TwoStage result is a
material known quality gap, especially for canonical/custom predicates and interaction
patterns, so TwoStage remains opt-in.

## Automated Verification

- Event Enrichment/TwoStage targeted suite: 58 passed.
- New deterministic evaluator: 32/32 passed.
- Memory selection: 1282 passed, 3 known frozen-baseline failures.
- Full repository: 2009 passed, the same 3 known frozen-baseline failures.
- `ruff check .`: passed.
- `python -m compileall -q src scripts`: passed.
- `git diff --check`: passed (line-ending notices only).

The three pre-existing baseline drifts were left unchanged:

1. Long-tail fixture expects 25 passes; current result is 24.
2. Realistic retrieval expects Recall@5 >= 0.90; current result is 0.875.
3. Long-tail-write V1 fixture expects 8 strict passes; current result is 16.

No expectation was changed to conceal these results.

## Required Closeout Answers

**Q1. Can Stage 1 distinguish a new Event from attribute completion?**

Yes for the bounded MVP smoke: S10/S11 were completions and S13 was a new occurrence.
The broad frozen-Gold result shows that TwoStage semantic quality is not yet stable
enough to become the default.

**Q2. Does Stage 2 retain field, value, and evidence?**

Yes in all six semantic-operation smoke cases and in the typed resolver regressions.
Evidence must be a literal span of the current user message.

**Q3. Is NewMemoryDraft losslessly compatible with AtomicClaim?**

Yes. Round-trip equality, including payload and temporal fields, is covered by a
regression test. Legacy `claims` are also exposed as `NewMemoryDraft` units.

**Q4. Is EnrichmentDraft free of trusted target_memory_id?**

Yes. Trusted target IDs and nested mutation/write authority are schema-invalid.

**Q5. Does the generic resolver continue only for one target?**

Yes. Semantic cardinality is checked before mutation compatibility.

**Q6. Do zero and multiple targets fail closed?**

Yes. Both are deterministic no-op paths with observable reasons and audit-only records.

**Q7. Are new Events still misclassified as enrichment?**

No such case occurred in the 32-case deterministic set or S13 live smoke. The bounded
occurrence guard covers representative conflict, date, shared-activity, and gift-like
new occurrences. This is not a claim of exhaustive natural-language coverage.

**Q8. What is the False Enrichment Rate?**

0.0000 in the 32-case deterministic shadow fixture. A broader live rate has not been
established.

**Q9. Does canonical field enrichment work?**

Yes. All 13 canonical matrix cases passed, with end-to-end MemoryService and both Store
backends covered.

**Q10. Do custom_attributes only solve schema coverage?**

Yes. V1 allows only `weather` for date/shared activity after unique target resolution.
It provides no event-identity authority.

**Q11. Did any silent overwrite occur?**

No. Conflicting existing canonical and custom values are rejected at resolver and Store
boundaries. Same-source replay is idempotent.

**Q12. Were Stable Fact, Preference, Pattern, or State added to generic enrichment?**

No. `EnrichmentDraft.target_kind` must be `interaction_event`; non-Event rows cannot be
semantic targets. Refinement is trace-only.

**Q13. Did the frozen 40-case suite regress?**

The default SingleStage code path was not changed and remains available. The latest
SingleStage live run was 0.6750/0.7000 extraction/normalized. The opt-in TwoStage run was
substantially lower, which is explicitly retained as a known limitation rather than
hidden by changing Gold or promoting TwoStage.

**Q14. Is SingleStage fallback still usable?**

Yes. SingleStage is still the configured default, legacy Stage 2 `claims` remain
accepted, and fallback regressions pass. The focused live smoke completed natively with
zero fallback.

**Q15. Is V1 MVP demonstrable?**

Yes, as a bounded opt-in semantic extraction plus precision-first Event enrichment
capability. It is not approval for broad generic enrichment or for making TwoStage the
production default.

## Known Limitations

- TwoStage broad ontology quality is below SingleStage on the frozen 40-case diagnostic.
- Target identity is limited to explicit source/context/pending-slot linkage; no broad
  historical semantic search is used.
- Only one target is supported. Ambiguous and multi-target enrichment fail closed.
- Generic refinement mutation is not implemented; refinement drafts are trace-only.
- Custom attributes are limited to `weather` for date/shared activity.
- No cross-conversation enrichment, complex pending-slot lifecycle, Event-to-Pattern
  consolidation, or generic non-Event enrichment is implemented.
- The dedicated legacy conflict-cause resolver remains in place for legacy AtomicClaim
  extraction compatibility.

## Artifacts

- `artifacts/event_enrichment_mvp_v0_1/results.json`
- `artifacts/event_enrichment_mvp_two_stage/raw_results.jsonl`
- `artifacts/event_enrichment_mvp_two_stage/summary.md`
- `artifacts/event_enrichment_ontology_single/results.jsonl`
- `artifacts/event_enrichment_ontology_single/summary.md`
- `artifacts/event_enrichment_ontology_two/results.jsonl`
- `artifacts/event_enrichment_ontology_two/summary.md`

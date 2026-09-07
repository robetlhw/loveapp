# Memory V2 Freeze Readiness

## Decision

`MEMORY_V2_FREEZE_READY_MINIMUM`

The final 40-case Live run and hard 8x3 repeat meet every Minimum Freeze Line
check. The evaluation remained shadow-only: production Store mutation was not
permitted. This decision freezes the evaluated Memory V2 architecture; it does
not enable destructive multi-target writes.

## Top-K Ablation

The ablation used the unchanged pre-remediation Judge protocol. Embedding,
Vector Top-20, cheap-ranking features, equivalence collapse, Validator, dataset,
and Store policy were held constant.

| Metric | Top-3 | Top-4 | Top-5 |
|---|---:|---:|---:|
| Semantic Recall@K | 0.7750 | 0.8500 | 0.9000 |
| Gold Retention@K | 0.7750 | 0.8500 | 0.9000 |
| Relation Accuracy | 0.6500 | 0.6750 | 0.7750 |
| Macro-F1 | 0.5455 | 0.5784 | 0.6595 |
| Target Set Accuracy | 0.4000 | 0.4000 | 0.4750 |
| Target Micro-F1 | 0.6000 | 0.6436 | 0.6882 |
| Over-broad Multi-target | 15 | 14 | 14 |
| Passed Cases | 9 | 10 | 12 |
| Avg Judge Tokens | 1710.925 | 1864.100 | 2003.775 |
| Judge Latency p50 (ms) | 1371.229 | 1562.341 | 1396.997 |
| Judge Latency p95 (ms) | 2018.802 | 2008.033 | 1988.937 |
| Destructive Safety Violations | 0 | 1 | 0 |

Recommended semantic Top-K: **5**.

Top-4 is disqualified by one isolated-Store safety violation. Top-3 remains
safe but loses 0.125 semantic recall versus Top-5 and also has lower relation
and target quality. Top-5 is selected using Safety, Recall, Target, Relation,
then Cost.

## Final Live Result

| Metric | Pre-remediation Top-5 | Final | Delta |
|---|---:|---:|---:|
| Recall@20 | 1.0000 | 1.0000 | 0.0000 |
| Semantic Recall@5 | 0.9000 | 0.9000 | 0.0000 |
| Relation Accuracy | 0.7750 | 0.8750 | +0.1000 |
| Macro-F1 | 0.6595 | 0.8249 | +0.1654 |
| Target Set Accuracy | 0.4750 | 0.6000 | +0.1250 |
| Target Micro-F1 | 0.6882 | 0.7229 | +0.0347 |
| Exact Expected Multi-target | 0 | 1 | +1 |
| Over-broad Multi-target | 14 | 9 | -5 |
| Passed Cases | 12 | 19 | +7 |
| Store Application Errors | 0 | 0 | 0 |
| Destructive Safety Violations | 0 | 0 | 0 |

The original published baseline reported 16 over-broad multi-target proposals;
the final result is 9, a reduction of 7. The directly comparable Top-5
ablation run reported 14, a reduction of 5.

Final model telemetry:

| Metric | Pre-remediation Top-5 | Final |
|---|---:|---:|
| Avg prompt tokens | 1773.000 | 2911.700 |
| Avg completion tokens | 230.775 | 284.150 |
| Avg total tokens | 2003.775 | 3195.850 |
| Latency p50 (ms) | 1396.997 | 1720.558 |
| Latency p95 (ms) | 1988.937 | 2300.945 |

The quality gain therefore has a measurable cost: average total Judge tokens
increased by about 59.5%, p50 latency by about 23.2%, and p95 latency by about
15.7%. This is acceptable for the Minimum line but remains a non-blocking
optimization item.

## Hard 8x3

| Metric | Result |
|---|---:|
| Relation consistency | 1.0000 |
| Target consistency | 1.0000 |
| Validator consistency | 1.0000 |
| Top-5 order consistency | 1.0000 |
| Relation Accuracy | 1.0000 |
| Target Set Accuracy | 0.8750 |
| Target Micro-F1 | 0.9333 |
| Store Application Errors | 0 |
| Destructive Safety Violations | 0 |

The hard run had no stochastic relation, target, Validator, or retrieval-order
drift across its 24 evaluated rows.

## Remediation Contract

Each supplied candidate now has one strict candidate-wise result:

```json
{
  "memory_id": "M1",
  "relation": "update",
  "is_direct_target": true,
  "confidence": 0.94
}
```

The adapter requires exact candidate-ID coverage. Unknown, missing, or
duplicate IDs fail closed. `is_direct_target` must be a JSON boolean.
`UNRELATED` and `UNCERTAIN` cannot be direct targets. The final target set must
exactly match the IDs marked direct; inconsistent output fails closed as
`UNCERTAIN` with no targets.

The prompt boundaries now distinguish:

- same fact/state/event identity from a merely similar event type;
- a distinct event instance from a repeated event;
- event instances from sustained patterns;
- claim-level complementarity from topical or explanatory relevance;
- direct targets from useful background context;
- direct contradictions from weak, multi-dimensional intent inference;
- historical-to-current state change from same-time contradiction.

No second Judge call, benchmark metadata, Gold ID, arbitrary patch object,
ontology extension, Validator relaxation, Store API, or lifecycle enum was
introduced.

## Failure Attribution

Failed rows expose candidate-wise relation, direct-target flag, final target
set, Gold target set, overall relation, and Validator decision. Secondary
diagnostics distinguish:

- `RELATION_CLASSIFICATION_ERROR`
- `DIRECT_TARGET_ELIGIBILITY_ERROR`
- `TARGET_AGGREGATION_ERROR`
- `RANKING_DROP`
- `SAFETY_DOWNGRADE`

Gold is used only for offline attribution. It is never passed to the Judge,
target aggregation, Validator, or Store.

## Final Questions

1. **Which K is best?** Top-5. It is safe, retains 0.9000 of semantic Gold
   targets, and has the strongest relation and target metrics. Top-4 has one
   safety violation; Top-3 loses too much recall.
2. **How much recall is lost by reducing K?** Top-4 loses 0.0500 and Top-3
   loses 0.1250 semantic recall versus Top-5.
3. **How much did Target Set Accuracy improve?** 0.4750 to 0.6000, +0.1250.
4. **How much did Target Micro-F1 improve?** 0.6882 to 0.7229, +0.0347.
5. **Did SAME/COMPLEMENTARY improve?** Yes for the original failure direction:
   `COMPLEMENTARY -> SAME` fell from 4 to 0, and correct COMPLEMENTARY rose from
   11/17 to 16/17. Three duplicate/multi-proposition SAME cases now aggregate
   as COMPLEMENTARY and remain a known exact-target limitation.
6. **Did UNRELATED -> COMPLEMENTARY decrease?** Yes, from 2 to 0 in the final
   full run. The dedicated unrelated slice passed 5/5.
7. **How far did over-broad multi-target fall?** From the published 16 to 9;
   from the directly comparable ablation run, 14 to 9.
8. **Did exact expected multi-target improve?** Yes, from 0 to 1.
9. **Is LTW2-040 still misclassified as contradiction?** No. It was
   `UNCERTAIN` with no target in final Live and all three hard repeats.
10. **Did Validator, Store, or Safety regress?** No. Store application errors
    and destructive safety violations are both zero. Multi-target destructive
    writes remain unsupported and fail closed.
11. **Are token and latency costs acceptable?** They pass the Minimum line,
    but the 59.5% average token increase is a known non-blocking cost.
12. **Was the Minimum Freeze Line reached?** Yes. Every finalizer check passed,
    and the measured quality metrics also exceeded the documented Stretch
    thresholds in this run.

## Frozen Pipeline

```text
User Message
  -> Hybrid Gate
  -> Extraction
  -> Generic Validation
  -> Deterministic Normalization
  -> Canonical Validation
  -> Admission
  -> Hybrid Retrieval Top-20
  -> Cheap Rank
  -> Semantic Top-5
  -> Candidate-wise Semantic Judge
  -> Direct Target Eligibility
  -> Minimal Target Set
  -> Deterministic Validator
  -> Store
```

Frozen and unchanged in this remediation: `HybridMemoryRetriever`, embedding
model/input, Vector Top-20, cheap-ranking weights, equivalence collapse, Gate,
Extraction, Normalization, Admission, production Validator, Store/lifecycle,
destructive multi-target policy, Golden labels, dataset text, Shared Bank, and
canonical ontology.

## Known Limitations

- Semantically near-duplicate candidates without a documented equivalence
  group remain hard to distinguish by exact memory ID without benchmark
  leakage.
- Nine over-broad multi-target proposals remain; the Validator keeps
  destructive multi-target operations fail closed.
- Semantic Top-5 retains 0.9000 rather than all Gold targets, while Top-20
  retrieval remains 1.0000.
- Final contradiction recall is 0.8000 because one weak case is conservatively
  uncertain; destructive safety remains zero.
- The longer Judge prompt increases token and latency cost.
- This was a shadow evaluation. Production Store mutation was not enabled by
  the evaluator.

## Verification

- Focused Judge/evaluator/CLI regressions: `114 passed`.
- Memory test selection: `954 passed, 1 failed`; the existing lifecycle test
  `test_get_context_projects_legacy_transition_without_mutating_store` fails
  when preceded by another lifecycle test and passes alone (`1 passed`).
- Full repository: `1714 passed, 3 failed`. The failures are the same lifecycle
  order dependency plus two unrelated Date/Router assertions; both Date tests
  also fail alone in the current dirty workspace.
- `ruff check src tests`: passed.
- Scoped Ruff for all remediation files: passed.
- `ruff check .`: blocked only by 7 pre-existing findings in untracked
  `.tmp/probe.py` and `.tmp/probe2.py`.
- `git diff --check`: passed.

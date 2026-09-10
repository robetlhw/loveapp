# TwoStage Diagnostic Result

Generated from the frozen Ontology Sanity v0.1 Gold. Store mutation was disabled.

## 1. Root Cause

- Primary: the Stage1 prompt/output contract and `CoarseExtraction` schema did not agree.
  The live Flash model returned descriptive gate reasons, transport aliases such as `text`
  and `source_span`, and bounded-domain concepts such as `event` or `residence` where the
  adapter required exact `MemoryKind` enum values.
- Secondary: after Stage1 was made observable, Stage2 showed the same transport-contract
  drift. It returned wrapper keys such as `type`, `extractions`, and
  `atomic_extractions`, nested claims, and structural aliases such as `claim_type` and
  `evidence_span`.
- The failures were before normalization, relation, lifecycle, reconciliation, or Store
  serialization.

## 2. Why 40/40 Previously Fell Back

Before the fix, the representative 18-case live Smoke failed at Stage1 in every case:

| Stage | Success | Fallback reason |
|---|---:|---|
| Stage1 | 0/18 | `STAGE1_SCHEMA_ERROR` (18) |
| Stage2 | 0/18 | not reached |
| Native TwoStage | 0/18 | n/a |

The old 40/40 fallback run did not retain structured stage reasons. The reproduced Smoke
showed one systematic Stage1 contract mismatch rather than 18 separate ontology failures.
Once Stage1 aliases were bounded, 17 positive cases reached Stage2 and all 17 initially
exposed the second transport mismatch. This establishes that the apparent TwoStage result
was SingleStage fallback output, not native TwoStage execution.

## 3. Minimal Fix

- Added per-stage raw output, parse, validation, propositions, claims, token telemetry,
  structured fallback reason, exception, and final-extractor diagnostics.
- Tightened only the opt-in TwoStage Stage1/Stage2 output instructions.
- Added bounded Stage1 aliases for known transport fields, semantic roles, and memory-kind
  hints. Unknown or empty positive outputs still fail closed.
- Added bounded Stage2 wrapper/field normalization before the existing raw parser and
  atomicity validator. Mutation targets and write instructions are rejected before any
  flattening.
- Kept SingleStage, production default mode, Gold, Normalizer, Gate, Relation, Lifecycle,
  Retrieval, and Store contracts unchanged.

## 4. Smoke After Fix

| Measure | Result |
|---|---:|
| Cases | 18 |
| Stage1 success | 18/18 |
| Stage2 success | 17/17 |
| Stage2 correctly skipped by negative gate | 1/1 |
| Native TwoStage completion | 18/18 (100%) |
| Fallback | 0/18 (0%) |
| Stage1 multi-proposition minimum met | 3/4 |
| Stage2 multi-claim count preserved | 4/4 |

The native completion target of at least 90% is met. Native completion is reported
separately from semantic correctness.

## 5. Multi-claim Cases

| Case | Stage1 | Stage2 | Assessment |
|---|---|---|---|
| S07 Event + State | 2 propositions | 2 claims | Event and State preserved |
| S08 Pattern + State | 2 propositions | 2 claims | Pattern and State preserved |
| S12 Enrichment-ready + State | 2 propositions, expected >=3 | 2 claims | cause/emotion were merged into one Event; incomplete decomposition |
| S17 Observable + Belief | 2 propositions | 2 claims | observable and interpretation split, but belief remained `user_reported` |

Stage1 is now genuinely decomposing propositions, but semantic-role and perspective
quality are not yet consistently correct. Stage2 receives the serialized Stage1
`coarse_extraction` and produces one batched response for all propositions.

## 6. Frozen v0.1 Single vs TwoStage

This is the latest same-run A/B, so it may differ from earlier live SingleStage baselines
because the model is nondeterministic.

| Metric | Single | TwoStage with fallback | Delta |
|---|---:|---:|---:|
| Native execution rate | 1.0000 | 0.9750 | -0.0250 |
| Fallback rate | n/a | 0.0250 | n/a |
| Extraction Pass | 0.7000 | 0.2000 | -0.5000 |
| Normalized Pass | 0.7000 | 0.3000 | -0.4000 |
| Kind Accuracy | 0.8636 | 0.9318 | +0.0682 |
| Subject Accuracy | 0.8913 | 0.7174 | -0.1739 |
| Perspective Accuracy | 0.9130 | 0.9565 | +0.0435 |
| Canonical/Custom Accuracy | 0.8485 | 0.5455 | -0.3030 |
| Event Type Accuracy | 0.6667 | 0.3333 | -0.3334 |
| Pattern Metric Accuracy | 0.6667 | 0.1111 | -0.5556 |
| Multi-claim Completeness | 0.1667 | 0.0000 | -0.1667 |
| Forbidden Claim Rate | 0.1250 | 0.0250 | -0.1000 |

TwoStage native-only (39 cases): Extraction `0.1795`, Normalized `0.2821`,
Multi-claim `0.0000`, Forbidden Claim Rate `0.0256`. Fallback therefore does not hide a
semantically strong native result.

The only fallback was `pattern_001`:

```text
ATOMIC_EXTRACTION_VALIDATION_ERROR
claim c1 contained both contact_frequency and initiation_balance
```

This is a valid safety rejection by the existing atomicity validator. It must not be fixed
by weakening validation.

## 7. Case-level Regression Against Stored Baseline

Stored baseline: Extraction `25/40`, Normalized `24/40`.
Latest TwoStage: Extraction `8/40`, Normalized `12/40`.

Old pass -> new fail (13):

| Case | First failure |
|---|---|
| sf_002 | `EXTRACTION_SEMANTIC_LOSS` |
| sf_003 | `EXTRACTION_SEMANTIC_LOSS` |
| sf_004 | `EXTRACTION_SEMANTIC_LOSS` |
| sf_005 | `EXTRACTION_SUBJECT_ERROR` |
| sf_006 | `EXTRACTION_SUBJECT_ERROR` |
| pref_001 | `EXTRACTION_SEMANTIC_LOSS` |
| pref_003 | `EXTRACTION_SEMANTIC_LOSS` |
| pref_006 | `EXTRACTION_SEMANTIC_LOSS` |
| event_002 | `EXTRACTION_SEMANTIC_LOSS` |
| event_004 | `EXTRACTION_SEMANTIC_LOSS` |
| pattern_003 | `EXTRACTION_SEMANTIC_LOSS` |
| pattern_005 | `EXTRACTION_KIND_ERROR` |
| pattern_008 | `EXTRACTION_SUBJECT_ERROR` |

Old fail -> new pass (1): `event_008`.

Unchanged pass (11): `sf_007`, `sf_008`, `pref_002`, `pref_004`, `pref_005`,
`event_001`, `event_006`, `event_007`, `pattern_001`, `bd_003`, `bd_005`.

Unchanged fail (15): `sf_001`, `pref_007`, `pref_008`, `event_003`, `event_005`,
`pattern_002`, `pattern_004`, `pattern_006`, `pattern_007`, `bd_001`, `bd_002`,
`bd_004`, `bd_006`, `bd_007`, `bd_008`.

## 8. Cost

| Mode | Cases | Calls | Avg calls/case | Tokens | Avg tokens/case |
|---|---:|---:|---:|---:|---:|
| Smoke TwoStage | 18 | 35 | 1.9444 | 21,386 | 1,188.1111 |
| Frozen SingleStage | 40 | 41 | 1.0250 | 286,764 | 7,169.1000 |
| Frozen TwoStage with fallback | 40 | 81 | 2.0250 | 56,000 | 1,400.0000 |
| Frozen TwoStage native-only | 39 | 78 | 2.0000 | 47,771 | 1,224.8974 |

## 9. Diagnostic Answers

- Q1: fallback occurred at Stage1 schema validation; once exposed, Stage2 had a second
  prompt/transport contract mismatch. The remaining one-case fallback is atomicity
  validation.
- Q2: the reproduced all-case Stage1 failure had one systematic root cause. Stage2 was a
  separate secondary mismatch hidden behind it.
- Q3: Stage1 now performs real decomposition; it met the minimum in 3/4 focused multi-claim
  cases, missing the three-way S12 split.
- Q4: candidate kinds are structurally valid and cover expected kinds in the Smoke, but
  several propositions include broad alternative kinds and are not final classifications.
- Q5: semantic roles are emitted, but S08 and S17 use `state_assertion` too broadly and S12
  does not isolate the two expected attribute updates.
- Q6: Stage2 receives Stage1 propositions in its prompt and returns a single batched result;
  focused cases preserve multiple claims. Semantic quality still does not prove every
  Stage1 hint is used correctly.
- Q7: 39/40 frozen cases satisfy the native `AtomicExtraction` contract. One unsafe
  multi-dimension claim is correctly rejected.
- Q8: primary categories were schema/enum/field mismatch and prompt-output contract drift;
  not confidence, reconciliation, Store serialization, or Strong escalation.
- Q9: post-fix native execution is 18/18 Smoke and 39/40 frozen Gold.
- Q10: multi-claim transport works in all four Smoke cases, but S12 decomposition and frozen
  multi-claim correctness remain inadequate.

## 10. Remaining Problems

- Values/objects are often omitted, causing stable facts and preferences to lose required
  semantics even when kind classification is correct.
- Subject accuracy regresses materially in the frozen set.
- Canonical/custom, Event type, and Pattern metric cues are not reliably carried from
  evidence through Stage2.
- S12 enrichment-ready decomposition is incomplete.
- Belief perspective in S17 is not preserved.
- Multi-claim completeness on the frozen Gold remains zero.
- Live model nondeterminism remains; this report separates the stored baseline from the
  latest same-run A/B rather than treating them as interchangeable.

## 11. Recommendation

**No: TwoStage is not ready for Ontology Sanity v0.2 construction.**

The `Stage1 -> Stage2 -> AtomicExtraction` chain is now genuinely executable and observable,
and its fallback rate is within the requested bound. However, semantic accuracy regressed
substantially against both the same-run SingleStage result and the stored baseline. The next
work should analyze Stage2 value/payload preservation, subject assignment, semantic roles,
and multi-claim correctness before expanding the benchmark. TwoStage remains opt-in.

## 12. Verification

- Targeted TwoStage/Ontology tests: `23 passed`.
- Memory-selected repository tests: `1234 passed`, `3 failed`, `727 deselected`.
- Full repository: `1961 passed`, `3 failed`.
- The same three pre-existing long-tail baseline assertions fail in both runs:
  `passed_case_count` expected 25/actual 24, retrieval Recall@5 expected >=0.90/actual
  0.875, and Long-tail Write strict pass expected 8/actual 16. This task did not touch
  those evaluators, datasets, relation/retrieval paths, or expectations.
- Ruff on all changed Python files: passed.
- `python -m compileall -q src scripts/evaluate_two_stage_extraction_diagnostic.py`:
  passed.
- `git diff --check`: passed.

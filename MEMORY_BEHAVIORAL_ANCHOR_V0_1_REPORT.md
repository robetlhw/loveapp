# Memory Behavioral Anchor v0.1 — Live Evaluation Report

Date: 2026-09-10
Mode: real TwoStage Flash extraction, shadow-only
Store mutation permitted: `False`
Dataset: `evals/memory/memory_behavioral_anchor_v0_1.jsonl`
Dataset SHA256: `c4023316dcc79433e60ec1284d518c9d561bd00828d16141cdc56bf136b206b0`

## Result

The canonical live run passed **22/28** cases (`0.7857`). A prior identical run
passed 23/28, so the observed 22–23/28 range is model nondeterminism, not a
fixture change. No model transport or schema-parse failures occurred in either
run. The established baseline records the latest 22/28 run and is stored at
`evals/baselines/memory_behavioral_anchor_v0_1.json`.

Artifacts for the canonical run are in
`artifacts/memory_behavioral_anchor_v0_1/`:

- `summary.md`
- `raw_results.jsonl`
- `normalized_results.jsonl`
- `failures.jsonl`

## Metrics

| Metric | Live result |
|---|---:|
| Overall anchor pass rate | 0.7857 |
| Semantic role accuracy | 0.8929 |
| MemoryKind accuracy | 1.0 |
| Event type accuracy | 0.9333 |
| Perspective accuracy | 1.0 |
| Semantic coverage | 0.9 |
| New-event safety accuracy | 0.8333 |
| Enrichment detection precision | 0.7778 |
| Enrichment detection recall | 0.8571 |
| False enrichment count | 1 |
| False enrichment rate | 0.1667 |
| Ambiguous-target rejection rate | 1.0 |
| Refinement-vs-update accuracy | 0.5 |
| Multi-claim coverage | 1.0 |
| Model failure count | 0 |

## Case attribution

The canonical run's six failures are:

| Case | Primary attribution | Diagnosis |
|---|---|---|
| A05 | Stage2 semantic decomposition | Interaction pattern was emitted, but the adapter/model output did not expose the expected `initiation_balance` metric. |
| A11 | Resolver / validator boundary | Severity text (`特别严重`) was extracted, but the existing typed enrichment validator accepts only bounded labels or numeric 1–5. |
| A12 | Stage2 semantic decomposition | “说开/和解” became a standalone reconciliation Event rather than an enrichment completion of the prior conflict; resolution meaning was not represented in the expected completion role. |
| A14 | Stage2 forbidden semantics | A new bounded conflict correctly appeared, but the same turn also emitted an enrichment draft; the deterministic resolver rejected the enrichment, yet the semantic false-positive remains. |
| A20 | Stage2 semantic decomposition | “搬到深圳” was classified as refinement instead of a new/update-like stable fact. |
| A24 | Resolver | Explicit “昨天” evidence was present, but the current resolver still saw two historical Events linked to the same source message and failed closed as ambiguous. |

Among the two stabilized post-adapter runs, the repeat passed 23/28 (failures
A11, A12, A14, A20, A24) and the canonical run passed 22/28 (the same five
plus A05). An earlier pre-adapter diagnostic run was 18/28 and is not used as
the baseline. A05 is the observed drift case in the baseline comparison.

## Answers to the required questions

1. **28-case pass count:** 22/28 in the canonical run; 23/28 in a repeat.
2. **Largest Stage1 failure category:** no Stage1 transport/gate category was observed. All model calls completed. Case failures were downstream: Stage2 semantic decomposition (3), Resolver (2), and Stage2 forbidden semantics (1).
3. **Largest Stage2 semantic loss:** Event/Pattern/State boundary details, especially missing pattern metric exposure and completion-vs-reconciliation role selection.
4. **New Event → Enrichment false positive:** 1 case (A14).
5. **False Enrichment Rate:** 1/6 new-event-safety cases = 0.1667.
6. **Event vs State/Pattern:** multi-claim coverage was 1.0 and event safety was 0.8333; the remaining weakness is canonical pattern metric exposure and occasional same-turn overproduction.
7. **Refinement vs Update:** not stable yet; 0.5 on the two refinement/update anchors (A19 passed, A20 failed).
8. **`user_belief` promotion:** no confirmed-belief violation was observed in the canonical run. The evaluator adapter correctly recognized uncertain perception evidence in A07/A22.
9. **Multi-claim coverage:** 1.0 in the canonical run.
10. **Ambiguous target fail-closed:** yes for the ambiguous-target anchors (1.0 rejection rate). A24 is intentionally reported as a current resolver limitation, not converted into an unsafe nearest-target write.
11. **Highest-priority follow-up cases:** A14 (false enrichment), A24 (date-aware antecedent resolution), A12 (completion/reconciliation boundary), A20 (refinement/update), A11 (bounded severity vocabulary), and A05 (pattern metric normalization).
12. **Architecture vs local repair:** no broad architecture rewrite is indicated. The failures are primarily local prompt/model semantic-role selection, normalization/adapter mapping, bounded validator vocabulary, and an existing resolver limitation.
13. **Frozen Gold changed:** no. The 28-case dataset and expectations were not changed based on model output.
14. **Long-term anchor:** yes, with the current result treated as a diagnostic baseline (not a release gate). Re-run it after every extraction/enrichment change and inspect case-level attribution.

## Safety and regression verification

- Store mutation remained disabled for every Behavioral Anchor run.
- 6 evaluator regression tests passed.
- Existing targeted Memory/Event-Enrichment tests plus this evaluator: **63 passed**.
- Existing 18-case TwoStage smoke: **18/18 native cases passed** (one Stage2 N/A by design; no fallback).
- Existing 32-case deterministic Event Enrichment: **32/32 passed**.
- Existing 40-case frozen ontology diagnostic: **28/40**, unchanged baseline behavior; expectations were not modified.
- Full repository: **2015 passed, 3 known baseline failures** unrelated to this task:
  - long-tail fixture expected 25, actual 24;
  - realistic retrieval Recall@5 expected >=0.90, actual 0.875;
  - long-tail-write V1 expected 8, actual 16.
- `ruff check .`: passed.
- `python -m compileall -q src scripts`: passed.
- `git diff --check`: passed.

## Scope decision

This task added only the Behavioral Anchor evaluator, its semantic Gold,
regression tests, baseline, and reports. It did not modify Memory ontology,
MemoryKind, extraction prompts, Resolver production behavior, or Store
contracts. The appropriate next work is a separately reviewed remediation for
the six diagnosed clusters; this report does not authorize such a change.

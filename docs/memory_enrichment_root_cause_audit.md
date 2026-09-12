# LoveApp Memory Enrichment Root-Cause Audit

## Scope and frozen baseline

This audit covers every record with `category == "enrichment"` in
`evals/memory/benchmark_v1.jsonl` (15 cases, dynamically selected by category;
currently BM-056 through BM-070). The baseline is the completed real-model
replay in `.data/evals/memory_benchmark_v1_live_20260912T100000Z.json`, whose
recorded code head is `61cc77f31516b7d8763608e3f3fd104aaebade91`, the current
HEAD before this audit. No production code, prompt, normalizer, benchmark, or
assertion was changed for this baseline.

The replay used isolated stores, serial turns within each case, native
two-stage extraction where available, production embedding-backed retrieval,
and the production Event enrichment resolver and Store write path.

## Baseline metrics (Enrichment subset)

| Metric | Result |
|---|---:|
| Cases / user turns | 15 / 75 |
| Passing cases | 3 (20%) |
| Gate recall / precision | 1.0 / 1.0 |
| Stage-1 proposition recall | 1.0 |
| Stage-1 semantic-operation accuracy | 1.0 |
| Stage-2 parse success | 63/63 (1.0) |
| Stage-2 validation success | 57/63 (0.9048) |
| Extraction contract recall | 0.7097 |
| ENRICH checkpoints | 11 |
| ENRICH target accuracy | 0.0 |
| ENRICH patch accuracy | 0.1818 (2/11) |
| NOOP checkpoints | 2/2 |
| Native two-stage turn rate | 0.92 |
| Primary failures | Extraction: 8; Resolver: 4 |

## Actual pipeline

```text
Gate
  -> Stage 1 proposition, role and hints
  -> Stage 2 typed EnrichmentDraft
  -> production retrieval (advisory Stage-2 context)
  -> resolve_event_enrichment(draft, history, existing_memories)
  -> typed GenericEventEnrichment
  -> MemoryWriteBatch / Store
  -> enrichment provenance audit
  -> benchmark claim/target/field scoring
```

Stage 1 is not the dominant failure in this subset: all 31 Stage-1 semantic
checkpoints were recalled with the expected operation role, and all 29 positive
Gate checkpoints were admitted. The resolver already emits enough diagnostics
to distinguish empty candidate propagation from compatibility rejection:
`semantic_candidate_ids`, `compatible_candidate_ids`, rejected candidates,
candidate scores, selected target and resolution reason.

## Case-level trace

`Primary root cause` is the earliest stage that made the expected checkpoint
impossible. Mutation and evaluator failures after a missing target are shown as
downstream symptoms, not independent root causes.

| Case | Stage 1 / hints | Stage 2 | Retrieval | Resolver | Mutation / evaluator | Primary root cause |
|---|---|---|---|---|---|---|
| BM-056 | PASS; `attribute_completion` | validation failed: `STAGE2_ROLE_MISMATCH` | expected Event retrieved | typed enrichment not reached | fallback created a new Event fragment | `STAGE2_ROLE_ERROR`: bounded-occurrence guard sees “昨天” + “吵架” in an explicit old-event completion |
| BM-057 | PASS; cause completion | typed `enrichment(cause)` | expected target hit (1/1) | `semantic_candidate_ids=[]`; `no_source_or_context_linked_event` | no patch/provenance | `RESOLVER_CANDIDATE_PROPAGATION_ERROR` |
| BM-058 | PASS; severity completion | typed `enrichment(severity)` | expected target hit (1/1) | empty semantic candidates; same reason | no patch | `RESOLVER_CANDIDATE_PROPAGATION_ERROR` (Golden anchor also omits normalized `high`) |
| BM-059 | PASS; resolution completion | typed resolution/outcome units | expected conflict row was retrieved, but a reconciliation row was ranked first | only incompatible `reconciliation` candidate reached resolver; `unsupported_event_type` | no patch | `STAGE2_SEMANTIC_TYPE_ERROR`: model produced a reconciliation Event for the original conflict; resolver rejection is downstream |
| BM-060 | PASS | CREATE-only control | n/a | n/a | both CREATE operations pass | no change |
| BM-061 | PASS; outcome completion | typed `enrichment(outcome)` | support row retrieved, but c1 binding failed because model emitted subject `partner` and event type `comfort` instead of the Golden contract | no target binding | CREATE and ENRICH score fail | `STAGE2_SEMANTIC_TYPE_ERROR` / Golden contract mismatch, not resolver compatibility |
| BM-062 | PASS; emotion completion | typed `enrichment(emotion)` | expected target hit (1/1) | empty semantic candidates; same reason | no patch | `RESOLVER_CANDIDATE_PROPAGATION_ERROR` |
| BM-063 | PASS | CREATE-only control | n/a | n/a | CREATE checkpoints pass; claim contract flags raw `travel` vs `shared_activity` | `GOLDEN_MISMATCH` for an alias-normalization expectation (not an ENRICH resolver failure) |
| BM-064 | PASS at Gate/Stage-1 | t1 model emits raw `travel`; t5 Stage-2 abstains (`ROUTING_ABSTENTION`) | no target row was bound | not reached | no CREATE target, therefore no ENRICH target | `STAGE2_ROUTE_ERROR` / `STAGE2_EMPTY_CLAIM` at t5; t1 also has raw `travel` contract drift |
| BM-065 | PASS; mixed completion + new propositions | valid cause enrichment plus two new-memory units | conflict target hit | enrichment resolver candidate set empty | new Events were added; cause patch missed | `RESOLVER_CANDIDATE_PROPAGATION_ERROR`; the independent c3 kind/subject drift is model output |
| BM-066 | PASS | two CREATE controls and explicit ambiguous NOOP | n/a | NOOP is correct | NOOP passes | no change; ambiguity safety preserved |
| BM-067 | PASS; outcome completion | typed `enrichment(outcome)` | expected target hit (1/1) | empty semantic candidates; same reason | no patch | `RESOLVER_CANDIDATE_PROPAGATION_ERROR` |
| BM-068 | PASS | subject-mismatch NOOP control | n/a | NOOP is correct | NOOP passes | no change; subject safety preserved |
| BM-069 | PASS; location completion | typed `enrichment(location)` | expected target was not retrieved | empty semantic candidates | no patch | `RETRIEVAL_MISSED_TARGET` (resolver has no retrieval-candidate input) |
| BM-070 | PASS; repeated cause completion | typed `enrichment(cause)` | expected target hit (1/1) | empty semantic candidates; same reason | no patch/provenance | `RESOLVER_CANDIDATE_PROPAGATION_ERROR` |

### Retrieval-to-resolver evidence

For nine ENRICH checkpoints with a bound expected target, retrieval contained the
expected target in seven cases (BM-056, BM-057, BM-058, BM-059, BM-062, BM-067,
and BM-070). In BM-057, BM-058, BM-062, BM-067, and BM-070 the subsequent
`memory_event_enrichment` trace still had both semantic and compatible candidate
lists empty. This proves that retrieval output is advisory Stage-2 context and
is not propagated to `resolve_event_enrichment()`.

BM-069 is a genuine retrieval miss. BM-061 and BM-064 fail earlier because the
expected historical claim is not correctly admitted/bound. Therefore resolver
propagation is the largest cluster, but it cannot alone fix every case.

### Stage-2 occurrence-boundary evidence

BM-056's Stage-1 proposition is explicitly an old-event `attribute_completion`,
yet `_validate_stage2_role_contract()` calls `is_new_event_occurrence()` on its
span. The span contains a date marker and an event word, so Stage 2 raises
`new bounded Event occurrence cannot be an enrichment`. The configured fallback
then stores a second Event carrying the cause. This is a Stage-2 boundary bug,
not a target-selection result.

### Compatibility evidence

The current typed matrix is bounded and should not be broadened for this audit:

* `conflict`: `cause`, `severity`, `emotion`, `resolution`, `outcome`;
* `date` / `shared_activity`: `location`, `activity_type`, `emotion`, `outcome`;
* custom `weather`: `date` / `shared_activity` only.

BM-059's `unsupported_event_type` follows the model's `reconciliation` EventType
and is not evidence that `outcome` should be allowed on arbitrary EventTypes.

## Root-cause counts

These are primary case attributions, not a count of downstream failed checks.

| Category | Cases | Count |
|---|---|---:|
| `RESOLVER_CANDIDATE_PROPAGATION_ERROR` | BM-057, BM-058, BM-062, BM-065, BM-067, BM-070 | 6 |
| `STAGE2_ROLE_ERROR` | BM-056 | 1 |
| `STAGE2_SEMANTIC_TYPE_ERROR` | BM-059, BM-061 | 2 |
| `RETRIEVAL_MISSED_TARGET` | BM-069 | 1 |
| `STAGE2_ROUTE_ERROR` / `STAGE2_EMPTY_CLAIM` | BM-064 | 1 |
| `GOLDEN_MISMATCH` (raw alias expectation) | BM-063 | 1 |
| no-change controls | BM-060, BM-066, BM-068 | 3 |

## Evaluator assessment

The scorer in `src/loveapp/evaluation/memory_benchmark_scoring.py` requires an
ENRICH target to be bound to the expected source claim and to appear in write
batch/audit target IDs; it also checks that the target remains active, the field
contains the expected value, no fragment row is added, and provenance exists.
For the propagation cases there is no typed Event enrichment operation and no
selected-target audit, so zero target accuracy reflects runtime behavior rather
than an evaluator-only false negative. CREATE controls can pass independently,
which confirms that source-bound DB mapping itself is functional for new rows.

BM-058, BM-061 and BM-063 also expose strict Golden-vs-normalized vocabulary
differences (`high`/`comfort`/`travel` versus the expected anchors). Those are
separate contract observations and must not be “fixed” by relaxing the scorer or
expanding Event compatibility without an ontology decision.

## Phase-A conclusion

1. Gate and Stage 1 coverage are healthy for this subset.
2. The largest actionable architecture gap is candidate propagation: retrieval
   finds the target in most affected cases, but the resolver receives only
   history/source-linked candidates and never the retrieved set.
3. BM-056 has a separate Stage-2 old-event/new-occurrence boundary bug.
4. BM-059/BM-061 are model semantic-type/contract drift; BM-069 is a genuine
   retrieval miss; BM-063 is an alias expectation mismatch. They should remain
   separate clusters.
5. Existing scope, status, EventType×field compatibility, subject checks,
   ambiguity fail-closed behavior and evaluator safety checks are working and
   should be preserved.

## Minimal Phase-B boundary

The evidence supports only a narrow follow-up:

* pass a bounded production-retrieved candidate pool (plus validated
  source/context evidence) into the existing resolver;
* preserve semantic-cardinality-before-compatibility and fail closed on zero or
  multiple candidates;
* distinguish explicit old-event completion wording from a genuinely new
  bounded occurrence at the Stage-2 boundary;
* do not grant Stage 2 DB target authority, broaden the compatibility matrix,
  alter benchmark expectations, or change unrelated extraction behavior.

The next phase should first implement and test one of these changes at a time,
then rerun the dynamically selected Enrichment subset before any full-100-case
regression.

## Post-fix validation

The narrow Phase-B changes were applied only to the production candidate
propagation path and the Stage-2 old-event occurrence guard. The final real
model replay is recorded in
`.data/evals/memory_benchmark_v1_live_enrichment_final.json` and covers all 15
`category == "enrichment"` cases (75 user turns):

| Metric | Result |
|---|---:|
| Passing cases | 5 / 15 |
| Gate recall / precision | 1.0 / 1.0 |
| Stage-1 proposition recall | 1.0 |
| Extraction contract recall | 0.7419 |
| Enrichment target accuracy | 0.4545 |
| Enrichment patch accuracy | 0.3636 |
| False enrichment count / rate | 1 / 0.25 |
| Native two-stage turn rate | 0.88 |
| Trace schema errors | 0 |

The single false-enrichment observation is scored only against an explicit
benchmark checkpoint. Model extraction failures remain separately attributed
to Extraction/Stage2 and are not counted as resolver failures.

The control replay is in
`.data/evals/memory_benchmark_v1_live_enrichment_control_20260912.json`:
9 cases, 23 user turns, 7 cases passed, Gate recall/precision 1.0/1.0, and
zero enrichment writes. A retry of the two model-sensitive controls is in
`.data/evals/memory_benchmark_v1_live_enrichment_control_retry_20260912.json`;
both failures again occurred before enrichment at Stage1/Extraction, with no
false patch.

Regression checks for the changed modules passed (`70 passed`), and Ruff plus
`git diff --check` passed. The complete repository and all-memory runs each
reported 3 existing long-tail baseline failures (`2150 passed, 3 failed` and
`1383 passed, 3 failed` respectively): the fixture relation count, realistic
retrieval threshold, and long-tail write baseline count. These failures are
outside the Enrichment changes and were not changed or masked.

## Phase-B files and boundary

Changed production files:

* `src/loveapp/adapters/memory/stage2_routing.py`
* `src/loveapp/application/event_enrichment.py`
* `src/loveapp/application/memory.py`
* `src/loveapp/evaluation/memory_benchmark_scoring.py`

Changed regression tests:

* `tests/test_memory_event_enrichment_mvp.py`
* `tests/test_memory_benchmark_v1.py`

The resolver still fails closed for zero or multiple semantic candidates, and
retrieval remains advisory rather than target authority. No Prompt, ontology,
schema, Store contract, Golden expectation, or compatibility matrix was
changed. Remaining limitations are model Stage2 semantic drift, occasional
retrieval misses, and the strict Golden vocabulary mismatches documented
above.

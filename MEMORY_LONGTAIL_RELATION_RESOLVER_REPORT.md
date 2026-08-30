# Memory Long-tail Relation Resolver Report

## Decision

Phase 2A relation-only evaluation and Phase 2B validator shadow mode are
implemented. Phase 2C lifecycle integration is **not approved**.

The live semantic judge did not meet the proposed quality gate. The local
validator nevertheless blocked every unsafe destructive proposal, and no
shadow result entered ClaimRelation governance, MemoryLifecycle, a write batch,
an audit-only batch, or the Store.

## Architecture

```text
incoming custom Memory
  -> HybridMemoryRetriever (active custom candidates, top 5)
  -> SemanticRelationJudge (typed proposal only)
  -> deterministic LongTailSemanticRelationValidator
  -> trace / Memory Inspector / evaluation report only
```

The canonical deterministic relation and lifecycle path is unchanged. A
semantic proposal cannot create a `MemoryWriteOperation`, status update,
supersession, or Store mutation.

## Candidate Retrieval

- Scope is restricted to the same user and relationship.
- Only active, non-expired custom memories are eligible.
- Candidate identity is preserved; semantic-context deduplication is disabled
  for this retrieval mode.
- Zero lexical relevance does not discard a semantic candidate in this narrow
  path.
- Subject, kind, and memory-role compatibility improve ranking but do not
  remove candidates and manufacture false uniqueness.
- The judge receives no more than five candidates.
- Live evaluation constructs the same `SentenceTransformerEmbeddingProvider`
  used by production bootstrap and injects it into `HybridMemoryRetriever`;
  fixture evaluation remains deterministic and provider-free.

A dedicated regression also exercises eight realistic same-scope distractors;
the relevant custom-memory target remains in the top-five candidate set.

## Semantic Judge Contract

The OpenAI-compatible judge returns a strict `SemanticRelationProposal`:

- relation: SAME, UPDATE, CONTRADICTION, COMPLEMENTARY, UNRELATED, or UNCERTAIN
- target memory IDs
- same-semantic-dimension flag
- confidence
- bounded reason
- model, latency, and token metadata

Malformed JSON, unknown relations, out-of-range confidence, empty responses,
and transport/model exceptions fail closed to UNCERTAIN. Invalid raw model
output is not copied into traces or fail-closed reasons.

## Validator Rules

All destructive UPDATE previews require:

- exactly one retrieved target in the same scope;
- active and non-expired target status;
- compatible subject, kind, and durable memory role;
- same semantic dimension and proposal confidence at or above the threshold;
- explicit, non-inferred, user-reported incoming evidence;
- sufficient incoming confidence and confirmed admission;
- temporal evidence with plausible ordering;
- incoming perspective at least as strong as the target perspective;
- confirmed-overwrite protection;
- a source message distinct from the target source.

Events, plans, and action intents are not eligible for destructive long-tail
updates. Multi-target proposals fail closed. The validator produces only
`would_update` and `would_supersede_memory_ids`; it has no commit API.

## Evaluation Set

`evals/memory/longtail_relations_v1.jsonl` contains 42 cases across:

- social integration
- family integration
- emotional openness
- future commitment
- interaction investment
- boundary change

All six relation labels are represented. MEM-013, MEM-014, and MEM-015
behaviors are included.

## Fixture Shadow Baseline

The fixture proposals deliberately contain adversarial wrong UPDATE decisions
to exercise deterministic guards. Their proposal accuracy is not a model
acceptance result.

| Metric | Result |
|---|---:|
| Cases | 42 |
| Proposal-level passes | 25 |
| Relation accuracy | 0.5952 |
| Target accuracy | 0.7619 |
| UPDATE precision | 0.32 |
| Candidate retrieval recall@5 | 1.0 |
| False destructive updates after validation | 0 |
| Confirmed overwrite violations | 0 |
| Input mutations | 0 |

Local report:
`.data/evals/memory_longtail_relations_fixture_20260830_092812_468336.json`

## Live Judge Results

The latest real OpenAI-compatible run used the configured `deepseek-v4-flash`
model with the production embedding-backed retriever. The run was started with
temporary process environment overrides for the disabled-by-default semantic
relation provider; the repository `.env` was not changed.

| Metric | Result | Proposed gate |
|---|---:|---:|
| Cases | 42 | - |
| Proposal-level passes | 19 | - |
| Relation accuracy | 0.5952 | >= 0.90 |
| Target accuracy | 0.6429 | - |
| UPDATE precision | 0.4444 | >= 0.95 |
| Candidate retrieval recall@5 | 1.0 | >= 0.95 |
| False destructive updates after validation | 0 | <= 0.02 rate |
| Confirmed overwrite violations | 0 | 0 |
| Input mutations | 0 | 0 |
| Judge calls | 39 (10 failed) | - |
| Judge not called | 3 | - |
| Total judge tokens | 19,484 | - |
| Mean judge latency | 1,606.735 ms | - |

Per-relation accuracy:

| Relation | Accuracy |
|---|---:|
| SAME | 1.0 |
| UPDATE | 0.5 |
| CONTRADICTION | 0.3333 |
| COMPLEMENTARY | 0.7143 |
| UNRELATED | 0.6 |
| UNCERTAIN | 0.4167 |

Local report:
`.data/evals/memory_longtail_relations_live_20260830_092927_855097.json`

## Destructive Mistake Review

The live judge proposed five false UPDATE relations. All were blocked:

| Case | Expected | Validator blockers |
|---|---|---|
| LT-006 | UNCERTAIN | weak evidence, low proposal confidence, no temporal evidence |
| LT-018 | UNCERTAIN | no temporal evidence |
| LT-021 | UNCERTAIN | weak evidence, low proposal confidence, no temporal evidence |
| LT-024 | COMPLEMENTARY | implausible temporal order |
| LT-033 | UNCERTAIN | insufficient admission, no temporal evidence |

Wrong-target validated UPDATEs are also counted as false destructive updates,
even when UPDATE is the expected relation.

## MEM Case Results

- MEM-013 event versus pattern: COMPLEMENTARY, validator non-destructive, pass.
- MEM-014 sustained pattern reversal: judge returned UNCERTAIN instead of
  UPDATE, so this remains a semantic false negative and no-op.
- MEM-015 social versus family integration: COMPLEMENTARY, validator
  non-destructive, pass.

## Observability

Memory Inspector now renders:

- long-tail candidate identities and retrieval scores;
- semantic proposal, targets, confidence, model, latency, and tokens;
- every validator check and fail-closed reason;
- would-update / would-supersede preview;
- the invariant `store_mutation_permitted = false`.

The CLI supports:

```text
loveapp eval memory-longtail-relations --fixture
loveapp eval memory-longtail-relations --live
```

Both support case filtering, candidate limits, custom output paths, and default
timestamped reports under `.data/evals/`.

## Known Limitations

- Live relation accuracy and UPDATE precision are below the acceptance target.
- CONTRADICTION, UNCERTAIN, and open-world UPDATE distinctions need further
  judge calibration and evaluation before any lifecycle integration.
- Candidate retrieval is evaluated against a controlled set; broader real
  memory distributions still need retrieval calibration.
- Multi-target semantic mutation is unsupported and intentionally fails closed.
- Shadow validation proves destructive safety for these fixtures, not that the
  judge is semantically ready to mutate production Memory.

## Next Integration Decision

Keep the feature disabled by default and remain in shadow mode. Do not connect
semantic proposals to ClaimRelationResolver, MemoryLifecyclePlanner,
MemoryWriteBatch, or Store until live relation accuracy and UPDATE precision
meet the gate and every destructive mistake has been reviewed.

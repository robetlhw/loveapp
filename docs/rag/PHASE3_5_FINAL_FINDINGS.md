# LoveApp Phase 3--5 Final Findings

## Final status

Phase 3--5 implementation, Dev evaluation, frozen Test confirmation, and the controlled A/B/C/D ablation are complete. The result is mixed and is recorded as such:

- Phase 3 Safety is ready against the written thresholds, but corrected branch accounting leaves RAG recall below target on both splits. Branch macro-F1 passes on Dev and fails on Test. Router Scenario passes on Dev but fails on Test; Goal micro-F1 fails on both splits.
- Phase 4 trigger and standalone-safety controls pass. Real retrieval gain is positive on both Dev and Test. The supplied contextual fixture has a documented duplicate-CurrentQuery lint exception.
- Phase 5 trigger, bounded count, multi-Need coverage, and single-intent control pass on both splits, with measurable multi-query latency overhead.
- No relationship-stage accuracy is claimed because `RouteResult` does not predict `relationship_stage`.

This is an implementation/evaluation closeout, not a claim that every Phase 3 acceptance target is met.

## Frozen boundaries

- Formal KB: `knowledge/loveapp_rag_knowledge_base_v2.md`, 500 documents / 500 unique IDs, SHA256 `e4373a1e4426ac422a42e7dae7d7bb61c6291832da67625b311b99bf57714d25`.
- Existing RAG V2 Dev/Test Gold and all six supplied Phase 3--5 fixtures were preserved. Specialized fixture IDs were checked against the KB and never indexed as documents.
- No BM25, Cross-Encoder, new embedding model, or LLM reranker was introduced.
- `scenario / goal / relationship_stage` remain relevance Soft Metadata; eligibility metadata may be a separate future hard-filter concern.
- Feature flags remain explicit: `router_v2_enabled`, `contextual_query_rewrite_enabled`, `query_decomposition_enabled`, `max_subqueries=3`. Historical production defaults remain off.

## Phase summary

| Phase | Dev | Test | Decision |
|---|---|---|---|
| 3 Router/Safety | Branch macro-F1 `0.9491`; RAG recall `0.9405`; Scenario accuracy `0.8571`; Goal micro-F1 `0.5491`; high-risk/sensitive recall `1.0/1.0` | Branch macro-F1 `0.8888`; RAG recall `0.8056`; Scenario accuracy `0.5278`; Goal micro-F1 `0.4486`; high-risk/sensitive recall `1.0/1.0` | Keep Safety V2; continue branch/scenario/goal work on Dev only |
| 4 Contextual Rewrite | Trigger F1 `1.0`; rewrite-needed Hit@3 `1.0`; MRR `0.9583`; drift `0`; retrieval executed | Trigger F1 `1.0`; rewrite-needed Hit@3 `1.0`; MRR `1.0`; drift `0`; retrieval executed | Pass with known Gold duplicate-query lint exception |
| 5 Multi-query | Trigger F1 `1.0`; NeedRecall@5 `1.0`; AllNeedsCovered@5 `1.0`; nDCG@5 `0.9948`; retrieval executed | Trigger F1 `1.0`; NeedRecall@5 `1.0`; AllNeedsCovered@5 `1.0`; nDCG@5 `0.9955`; retrieval executed | Pass functional controls; monitor latency |

## A/B/C/D interpretation

The canonical arms are fixed as follows:

| Arm | Router | Rewrite | Decomposition |
|---|---|---|---|
| A | Current | off | off |
| B | V2 | off | off |
| C | V2 | on | off |
| D | V2 | on | on |

The controlled no-retrieval ablation reports these Dev deltas:

- A→B Router/Safety: Branch macro-F1 `+0.1537` (`0.7954→0.9491`), Scenario accuracy `+0.6071` (`0.25→0.8571`), Goal micro-F1 `+0.2256` (`0.3235→0.5491`), high-risk recall unchanged at `1.0`, bypass unchanged at `0`.
- B→C Contextual Rewrite: trigger F1 `0→1`; drift remains `0`.
- C→D Multi-query: trigger F1 `0→1`; subquery-count exact accuracy `0.25→1.0`. Retrieval values are unavailable in this no-retrieval ablation and must not be read as zero retrieval quality.
- A→D is a cross-phase comparison only; no pooled score is reported.

The real retrieval runs are recorded separately in the Phase 4/5 standard JSON artifacts. They use the same 500-KB and temporary Qdrant, and do not alter the canonical ablation protocol.

## Data and diagnostics

Phase 4 Dev/Test lint confirms 500-KB membership, valid enums, standalone equivalence, and required history. The only error is six repeated generic CurrentQuery strings in each supplied split; different History makes those cases functionally distinct, but the textual duplication remains a `gold_or_dataset_issue` exception. Phase 5 Dev/Test lint is clean.

The query planner records rewrite/decomposition decisions, history window, subqueries, per-subquery candidates/scores, merged IDs, final top-k, and latency. Candidate Recall uses the detailed retriever's pre-rerank candidate pool when available; ranking metrics use final returned order. Drift detection is conservative and fixture-based, so zero drift is not a substitute for an independent semantic judge covering subject changes, new facts, uncertainty, information-need changes, or ordinary↔safety changes.

## Artifacts

- Phase 3: `.data/evals/router_safety_dev.json`, `.data/evals/router_safety_test.json`
- Phase 4: `.data/evals/contextual_rewrite_dev.json`, `.data/evals/contextual_rewrite_test.json`
- Phase 5: `.data/evals/multiquery_dev.json`, `.data/evals/multiquery_test.json`
- Combined protocol: `.data/evals/phase3_5_ablation.json`
- Detailed findings: `PHASE3_ROUTER_SAFETY_FINDINGS.md`, `PHASE4_CONTEXTUAL_REWRITE_FINDINGS.md`, `PHASE5_MULTIQUERY_FINDINGS.md`

## Production recommendation

Keep Safety/Branch V2 and the fail-safe planner implementation behind flags. Do not promote Phase 3 Scenario/Goal as fully accepted until the Dev-only remediation raises the frozen acceptance targets and a newly frozen Test run confirms them. Keep metadata as Soft Metadata in retrieval, use NeedRecall/AllNeedsCovered rather than AnyHit as the Phase 5 decision metrics, and monitor multi-query latency and no-answer behavior in later production validation.

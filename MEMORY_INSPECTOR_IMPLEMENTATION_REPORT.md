# LoveApp Memory Inspector Implementation Report

## A. Files and responsibilities

- `src/loveapp/application/memory_inspector.py`
  - Runs the real `MemoryService.remember_text` path.
  - Captures active before/after snapshots, `ExecutionTrace`, persisted transition audits,
    extraction runs, and actual persisted diffs.
  - Delegates context, history, memory detail, run listing, and reset to production service/store
    boundaries.
- `src/loveapp/cli_memory_inspector.py`
  - Implements the interactive commands and compact Rich rendering.
  - Provides machine-readable non-interactive JSON and interactive JSON Lines.
- `src/loveapp/cli.py`
  - Registers `uv run loveapp memory-test` with fixed debug-scope defaults.
- `src/loveapp/ports/memory.py`
  - Adds the narrowly scoped `reset_relationship_scope` store contract.
- `src/loveapp/adapters/memory/in_memory.py`
  - Clears only the requested relationship's context, messages, memories, extraction runs,
    transition audits, and relationship plans.
- `src/loveapp/adapters/memory/sqlite.py`
  - Deletes transition audits for the exact scope, then deletes the exact relationship row so
    existing foreign-key cascades remove its context-owned records atomically.
- `src/loveapp/application/memory.py`
  - Adds existing candidate fields to the ephemeral governance trace. It does not change any
    admission, relation, lifecycle, or write decision.
- `src/loveapp/adapters/memory/openai_compatible.py`
  - Adds full parsed claims to the ephemeral model-attempt trace while retaining the previous
    compact predicate trace field.
- `tests/test_memory_inspector.py`
  - Covers real service execution, before/after and diff, lifecycle supersession, skip/failure,
    context/history/runs, and reset delegation.
- `tests/test_cli_memory_inspector.py`
  - Covers CLI defaults, JSON stability, interactive commands, and skip/error rendering.
- `tests/test_memory_store_reset.py`
  - Covers complete, isolated, idempotent reset for in-memory and SQLite stores.
- `tests/test_memory_extractor.py` and `tests/test_memory_v2_governance.py`
  - Lock the additional trace fields without changing semantic expectations.
- `README.md`
  - Documents the integrated Inspector entry point and commands.

## B. Data provenance

| Inspector data | Authoritative source |
|---|---|
| Input message, Gate, extraction error, saved IDs | `MemoryService.remember_text` / `RememberResult` |
| Raw model claims, model/tier/duration, repair/failure | `ExecutionTrace` model-attempt records |
| Normalization, admission, relation, targets, planned action | `memory_candidate_governance` trace |
| Contextual/correction resolution | Existing contextual/correction trace records |
| Before/after and memory detail | `MemoryStore.list_memories` / `get_memory` |
| Added/merged/updated/status changes | Actual before/after state plus `RememberResult` |
| Transition rule and persisted targets | `MemoryTransitionAudit` |
| Run status, attempts, token counts, saved IDs | `MemoryExtractionRun` |
| Advice-facing context | The real `MemoryService.get_context` result |
| Conversation history | `MemoryService.get_conversation_history` |
| Reset | `MemoryStore.reset_relationship_scope` |

The displayed relation is the final governed relation after deterministic lifecycle planning. The
Inspector does not relabel that value as the resolver's pre-lifecycle result.

## C. Production semantic behavior

No semantic behavior change.

The new Store operation is a debug/test scoped cleanup boundary. The new trace fields are
observability-only. The Inspector does not implement Gate, extraction, normalization, admission,
relation, lifecycle, dedupe, or persistence decisions.

## D. Eight-case verification

The cases below were replayed on 2026-08-29 through the configured live LLM extractor and the real
Memory pipeline, with an in-memory Store and a distinct scope per case. Model output is inherently
non-deterministic; deterministic tests separately lock the Inspector contracts.

### Case 1: stable preference and repeat

- First turn: Gate `durable_signal`; `PREFERENCE`; `CONFIRM`; `ADD` one confirmed memory.
- Second turn: relation `SAME`, rule `normalized_dedupe`, planned action `MERGE`.
- Final active state: one memory row. No duplicate active preference was created.

Result: PASS.

### Case 2: cold war to reconciled

- Before turn 2: one confirmed `relationship.conflict_status=active` memory.
- Turn 2 relation: `UPDATE`.
- Lifecycle/write rule: `resolve_active_conflict`, planned action `replace`.
- Persisted diff: old active-conflict memory `CONFIRMED -> SUPERSEDED`; one new confirmed
  `relationship.conflict_status=resolved` memory added with `supersedes_id` pointing to the old row.
- Context: `confirmed_current_state` contained only the resolved/reconciled state. The old cold-war
  state was absent.

Result: PASS.

### Case 3: state changes again (A -> B -> A')

- Input generated a new confirmed interaction event and a new confirmed
  `relationship.conflict_status=active` state.
- Relation for the state candidate: `UPDATE`; rule
  `replace_state:relationship.conflict_status`.
- The reconciled state became `SUPERSEDED`; the original conflict history remained historical;
  the new active state used a new memory ID.

Result: PASS. The additional interaction event is expected event history, not a duplicate current
state.

### Case 4: explicit correction

- First turn added confirmed "partner does not eat spicy food" preference.
- Correction turn produced one preference candidate, relation `UPDATE`, planned `replace`, final
  governed rule `single_value_preference_dimension`.
- Persisted diff superseded the old row and added one new confirmed qualified preference with
  `supersedes_id` pointing to the old row.
- Final active state contained only the corrected preference.

Result: PASS.

### Case 5: weak claim cannot replace confirmed

- First turn added one confirmed positive spicy-food preference.
- The speculative follow-up was extracted as `user_belief` with confidence `0.35` and was skipped
  as low confidence.
- No replacement or status change occurred; the confirmed old memory remained the only active row.

Result: PASS for the confirmed-protection invariant. This replay rejected the weak candidate rather
than persisting it as `PROPOSED`.

### Case 6: user belief is not partner fact

- Gate entered extraction, but no governed candidate or persisted memory was produced.
- No definite partner fact appeared in active memory.

Result: PASS for safety. Observed limitation: the Gate matched the broad preference rule before the
extractor/governance path safely produced no write.

### Case 7: consultation question

- Gate returned `should_extract=false`, reason `no_durable_signal`.
- Extraction run status was `skipped`; diff was empty.

Result: PASS.

### Case 8: current versus history

- Active view returned only the confirmed reconciled state.
- All-history view returned both the superseded cold-war state and the confirmed reconciled state,
  with their original IDs and `supersedes_id` link intact.

Result: PASS.

## E. Known limitations

- `operations` is decoded from the existing final governance/contextual trace. The production
  `MemoryWriteBatch` itself is not exposed as a debug DTO; persisted audit plus before/after diff is
  the authoritative confirmation of the write.
- The final candidate trace can include lifecycle-overridden relation/rule data. The pre-lifecycle
  resolver result is not separately represented in the current implementation.
- Strong-verifier calls are visible in the current turn trace (`strong_called`, model fields), but
  verifier attempts are not persisted as `MemoryExtractionRun.attempts`; `/runs` can only show the
  persisted extraction attempts.
- Conversation history follows the production service's configured history limit.
- `--isolated` state is process-local and disappears when the Inspector exits. The default mode uses
  the configured backend under the fixed debug identity and can be cleared with `/reset`.
- The Inspector reports existing semantic behavior. It does not repair a Gate, extractor, relation,
  lifecycle, or ontology defect discovered during inspection.

## Verification

- Inspector/reset/observer/extractor/governance focused tests: 75 passed.
- All tests selected by `-k memory`: 375 passed.
- Full repository suite: 911 passed, 1 pre-existing date-boundary test failed. On the current date
  (Saturday, 2026-08-29),
  `test_exact_postponed_activation_scenario_builds_full_plan` expected "this Saturday" to resolve
  to 2026-08-29, while existing DatePlan parsing resolved it to 2026-09-05. The test failed again
  in isolation. No DatePlan code was changed as part of this Memory Inspector task.
- `uv run ruff check .`: passed.
- `git diff --check`: passed (line-ending conversion warnings only).

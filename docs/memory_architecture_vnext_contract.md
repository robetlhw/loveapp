# LoveApp Memory Architecture VNext Contract

This document records the first, additive contract boundary for the VNext
memory pipeline. It is intentionally not a Store migration and does not
replace the existing V1/V2 extraction or lifecycle contracts.

## Authority boundaries

```text
Stage 1 / Stage 2 -> semantic drafts only
Candidate Generator -> candidate set + provenance
Target Resolver -> RESOLVED / AMBIGUOUS / UNRESOLVED
Write Policy -> CREATE_CORE / ENRICH_CORE / ATTACH_DETAIL / CLARIFY / NOOP
Store -> executes an already-authorized decision
```

`EventDetailDraft` has no target ID and no write operation. `Candidate` records
the memory row and the channels that surfaced it. `TargetResolution` never
selects a write action. `WriteDecision` is the only contract that can carry a
bounded write operation.

## Event details

`EventDetail` is an auxiliary object attached to an `interaction_event`; it is
not a fifth `MemoryKind` and is not part of the ordinary Core Memory retrieval
pool. Raw `evidence_span` and source provenance are retained.

## Conversation binding

`PendingQuestion` now has bounded fields for the Assistant message, optional
application-owned target binding, event type, expected field, creation turn,
and expiry. These fields describe a conversation binding. They do not grant
Stage 1 or Stage 2 permission to mutate a Store row.

## Safety invariants

- A semantic draft cannot contain a target ID or a database operation.
- `RESOLVED` requires exactly one explicit target ID.
- `AMBIGUOUS` requires clarification metadata and cannot carry a target.
- `ATTACH_DETAIL` requires a resolved target and an `EventDetail`.
- Unknown or weak target identity must result in `CLARIFY` or `NOOP`, never an
  attach fallback.

The next implementation phases will adapt the existing resolver and write
batch incrementally. Existing V1/V2 behavior remains the compatibility path
until those phases are separately tested and enabled.

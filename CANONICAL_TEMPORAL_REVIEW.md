# Canonical Temporal Review

## Scope

This review is based only on the final Live V2 artifact:

- Artifact: `.data/evals/memory_longtail_realistic_live_v2_20260830_223348.json`
- Report version: `memory-longtail-realistic-v2`
- Generated at: `2026-08-30T14:36:24.233607+00:00`
- Dataset hash: `6f2db529d7d6601b93e478aaf2ebfb26d8855ee4869775a5c2cd18af6a96b14a`
- Evaluation mode: `shadow_live`
- Store mutation permitted: `false`

No production lifecycle behavior or dataset expectation is changed by this review. The purpose is to separate structural canonical identity from temporal replacement authority for `LT-T-001` and `LT-T-002`.

## Decision Principle

Two claims can use the same canonical predicate and state dimension while describing disjoint temporal slices. Same canonical dimension is necessary for comparing state, but it is not sufficient evidence for destructive supersession.

For these cases:

- A historical interval remains historical evidence.
- A current interval may define the current view without deleting a non-overlapping historical row.
- A later source message does not make the fact it describes temporally later.
- `UPDATE` of a current-state projection, if such a projection exists, is distinct from superseding the historical evidence row.

## LT-T-001: Historical Low Initiation Followed by Current High Initiation

### Inputs

Turn 1:

```text
去年她基本很少主动联系我。
```

Turn 2:

```text
但最近一个月她开始经常主动找我聊天了。
```

### Live V2 Trace

| Field | Turn 1 | Turn 2 |
|---|---|---|
| Memory ID | `LT-T-001-t1-1` | `LT-T-001-t2-1` |
| Kind | `interaction_pattern` | `interaction_pattern` |
| Canonical predicate | `interaction.initiation_balance` | `interaction.initiation_balance` |
| Normalized state dimension | `interaction.initiation_balance` | `interaction.initiation_balance` |
| Normalized state value | missing | `partner_to_user` |
| Actual period | `2025-01-01T00:00:00Z` to `2025-12-31T23:59:59Z` | `2026-07-29T00:00:00Z` to `2026-08-29T12:00:00Z` |
| Admission | `confirm`, score `0.94` | `confirm`, score `0.94` |
| Actual relation | `UNRELATED` | `UNRELATED` |
| Local rule | `local_unrelated` | `local_unrelated` |
| Lifecycle plans | none | none |

Turn 1 preserved the raw frequency and direction cues in its payload, but normalization did not produce a canonical `state_value`. Its semantic-identity check therefore failed before the Turn 2 governance result was evaluated. Turn 2 had a valid current value, but the local resolver had no comparable governed value on the historical target and returned `UNRELATED`.

### Manual Temporal Review

- Old temporal scope: calendar year 2025.
- New temporal scope: the recent month ending at the Live V2 reference time on 2026-08-29.
- Overlap: no.
- Old claim role: historical evidence.
- New claim role: current state evidence at evaluation time.
- Should the current view change to the new value: yes.
- Should the old historical row be retained: yes.
- May `LT-T-001-t2-1` supersede `LT-T-001-t1-1`: no; the intervals are disjoint and the old row is not a competing current fact.
- Appropriate row-level relation: non-destructive temporal coexistence, such as `COMPLEMENTARY`, unless a separate current-state projection is the update target.

### Review Verdict

The embedded expectation that Turn 2 destructively updates and would supersede `LT-T-001-t1-1` requires business review. It conflates updating the current view with deleting historical evidence. This case must not be used to broaden canonical lifecycle supersession until the historical/current representation contract is explicit.

There is also an independent normalization defect in this exact trace: Turn 1 has no canonical state value. Fixing that defect alone must not authorize superseding the historical row.

## LT-T-002: Current High Initiation Followed by Historical Low Initiation

### Inputs

Turn 1:

```text
最近一个月她经常主动找我聊天。
```

Turn 2:

```text
其实去年她几乎从来不会主动联系我。
```

### Live V2 Trace

| Field | Turn 1 | Turn 2 |
|---|---|---|
| Memory ID | `LT-T-002-t1-1` | `LT-T-002-t2-1` |
| Kind | `interaction_pattern` | `interaction_pattern` |
| Canonical predicate | `interaction.initiation_balance` | `interaction.initiation_balance` |
| Normalized state dimension | `interaction.initiation_balance` | `interaction.initiation_balance` |
| Normalized state value | `partner_to_user` | `user_to_partner` |
| Actual period | `2026-07-29T00:00:00Z` to `2026-08-29T12:00:00Z` | `2025-01-01T00:00:00Z` to `2025-12-31T23:59:59Z` |
| Admission | `confirm`, score `0.94` | `propose`, score `0.65` |
| Actual relation | `UNRELATED` | `CONTRADICTION` targeting `LT-T-002-t1-1` |
| Local rule | `local_unrelated` | `proposed_state_conflict` |
| Lifecycle plans | none | none |

Turn 2 was downgraded because the admission policy observed a conflicting active value. Canonical governance then treated the historical value as an unconfirmed competing current state and returned `CONTRADICTION` with reason: `An unconfirmed value cannot close an existing state value.` The trace contains no destructive lifecycle plan, so the Store remained safe.

### Manual Temporal Review

- Current claim scope: the recent month ending at the Live V2 reference time on 2026-08-29.
- Historical claim scope: calendar year 2025.
- Overlap: no.
- Turn 1 role: current state evidence.
- Turn 2 role: historical evidence, despite arriving in the later source message.
- Should Turn 2 update the current state: no.
- Should both rows be retained: yes.
- May Turn 2 supersede `LT-T-002-t1-1`: no.
- Appropriate relation: `COMPLEMENTARY` across non-overlapping temporal scopes.

The two rows share a structural state dimension, but they are not the same destructive-governance slot once temporal scope is considered.

### Review Verdict

The embedded non-destructive `COMPLEMENTARY` expectation is semantically consistent with the trace. The actual `proposed_state_conflict` result demonstrates that the local canonical relation path does not currently distinguish historical evidence from a competing current value. This is a documented limitation, not authorization to change lifecycle behavior in this remediation.

## Consolidated Decision

| Case | Old scope | New scope | Overlap | Current fact after both turns | Preserve history | Destructive supersede allowed |
|---|---|---|---|---|---|---|
| `LT-T-001` | 2025 historical | Recent month in 2026 | No | Turn 2 | Yes, Turn 1 | No |
| `LT-T-002` | Recent month in 2026 | 2025 historical | No | Turn 1 | Yes, Turn 2 | No |

Neither case supports a rule that automatically turns `same state_dimension + different state_value` into destructive `UPDATE`. Temporal role and overlap must be resolved first.

# Memory Extraction Gold Policy Patch

Date: `2026-09-02`  
Policy: `Subject Policy v1`

## Decision

`EXT-033`, `EXT-034`, and `SUBJ-006` all describe the partner's personal relationship status,
not the current shared state between the user and the partner. They therefore use the same
subject policy: `subject=partner`.

| Case | Old Gold | New Gold | Policy basis |
|---|---|---|---|
| EXT-034 | `subject=relationship` | `subject=partner` | “她说自己已经有男朋友了” describes the partner's personal partnered status. It is semantically parallel to EXT-033 and SUBJ-006. |

No other Gold field changed. `kind`, `perspective`, `semantic_target`, evidence, source text, and
scoring scope remain unchanged. This patch resolves a policy inconsistency; it was not selected to
match a sampled model output or increase a score.


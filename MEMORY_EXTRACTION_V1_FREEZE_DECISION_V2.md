# Memory Extraction V1 Freeze Decision V2

Date: `2026-09-02`  
Prompt version: `memory-v2.6`  
Decision: **NOT_FROZEN**

## Phase A Evidence

The only production prompt change in this closeout was the bounded Subject contrast added to
`memory-v2.6`. No Gate, Repair, ontology, Admission, Relation, Lifecycle, or Store behavior was
changed. `EXT-034` was aligned to the documented Subject Policy (`partner`) as a Gold-policy patch,
not as score optimization.

Two independent live runs of the 30-case Subject set were completed:

| Metric | Run 1 | Run 2 | Freeze threshold |
|---|---:|---:|---:|
| Claim Recall | 0.8667 | 0.8667 | >= 0.95 |
| Subject Accuracy | 0.8077 | 0.9231 | >= 0.90 |
| USER_BELIEF Subject Accuracy | 0.6000 | 0.8000 | >= 0.85 |
| Perspective Accuracy | 1.0000 | 1.0000 | >= 0.98 |
| Context Reply Recall | 1.0000 | 1.0000 | >= 0.90 |
| Safe Repair Hurt | 5 | 5 | <= 1 |

The refreshed 70-case run (`.data/evals/memory_extraction_v1_v26_70.json`) reached Claim Recall
`0.9747`, Subject Accuracy `0.9091`, Perspective `1.0000`, Atomization `0.8889`, Context Reply
Recall `0.9000`, and Negative FP `0.0000`; however USER_BELIEF Subject Accuracy was `0.6667` and
Safe Repair Hurt was `3`. The refreshed 19-case regression reached Claim Recall `0.9130` and
Context Reply Recall `0.8889`.

## Decision Rationale

The required thresholds are evaluated as a conjunction. The two Subject runs fail Claim Recall,
USER_BELIEF Subject Accuracy, and Safe Repair Hurt limits; the 70-case run independently fails the
USER_BELIEF and Repair Hurt limits. Live sampling also varies materially between runs. Therefore
`Extraction V1 = NOT_FROZEN` and no further prompt tuning is authorized in this closeout.

The four Subject Repair Hurt cases remain structural validation/Normalization boundary findings;
they are documented in `MEMORY_EXTRACTION_SUBJECT_REPAIR_REVIEW.md` and are not safely repairable by
a semantic Subject rule.


# Memory Extraction Post-Boundary Regression

Generated: 2026-09-02

This report re-runs the configured Flash extraction path after the Raw Claim /
Generic Validator boundary migration. It does not change the extraction prompt,
Gate policy, ontology, Relation, Lifecycle, Store, or test expectations.

## Results

| Dataset | Cases | Production claim recall | Subject accuracy | USER_BELIEF subject accuracy | Safe Repair hurt |
|---|---:|---:|---:|---:|---:|
| `extraction_subject_v1.jsonl` | 30 | 1.0000 | 0.9000 | 0.8571 | 0 |
| `extraction_v1_70.jsonl` | 70 | 1.0000 | 0.9494 | 0.7778 | 0 |
| `extraction_v1_regression_19.jsonl` | 19 | 0.9130 | 1.0000 | n/a | 1 |

## Boundary pressure cases

The four required cases were observed at the raw/normalization boundary:

| Case | Raw claim | Generic validation | Normalizer | Canonical validation | Store retention |
|---|---|---|---|---|---|
| `SUBJ-003` | present | accepted | Custom `desire_to_continue` | accepted as Custom | not observed by this evaluator |
| `SUBJ-013` | present | accepted | Custom `relationship_stability` | accepted as Custom | not observed by this evaluator |
| `SUBJ-021` | present | accepted | Custom `boundary_agreed` (live run may emit an additional related claim) | accepted as Custom | not observed by this evaluator |
| `SUBJ-022` | present | accepted | canonical `contact.status` when the state namespace is valid | accepted | not observed by this evaluator |

The Extraction V1 evaluator exercises Flash, repair, and Tiered extraction replay;
it does not execute `MemoryService.remember_text` or write a Memory Store. Therefore
“Store retention” is explicitly `not observed`, not inferred from a cascade claim.

## Attribution

The boundary migration removed the previous pre-normalization claim loss. Remaining
failures are model semantic differences (kind, subject, perspective, or atomization)
and one focused-regression repair hurt; they are not generic-validator rejections.

Artifacts:

- `.data/evals/memory_extraction_subject_v1_post_boundary.json`
- `.data/evals/memory_extraction_v1_70_post_boundary.json`
- `.data/evals/memory_extraction_v1_regression_19_post_boundary.json`


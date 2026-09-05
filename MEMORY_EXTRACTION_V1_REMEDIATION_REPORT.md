# Memory Extraction V1 Remediation Report

Baseline: `.data\evals\memory_extraction_v1_baseline_70.json`  
Remediation: `.data\evals\memory_extraction_v1_remediation_70.json`  
Dataset SHA256: `a9ed2f8b26e829171e1de98d59b26de2e823b44c7845cdf3fddf0d88e6a58b00`  
Models: `{"flash": "deepseek-v4-flash", "strong": "deepseek-v4-pro", "semantic_matcher": "deepseek-v4-pro"}`

## Baseline vs Remediation

| Metric | Baseline | Remediation | Delta |
|---|---:|---:|---:|
| Flash Raw Recall | 0.9241 | 1.0000 | +0.0759 |
| Post-Repair Recall | 0.8861 | 1.0000 | +0.1139 |
| Production Recall | 0.8987 | 1.0000 | +0.1013 |
| Unsupported Spurious Rate | 0.0253 | 0.0000 | -0.0253 |
| Supported Extra Count | 4.0000 | 3.0000 | -1.0000 |
| Subject Accuracy | 0.7887 | 0.8481 | +0.0594 |
| USER_BELIEF Subject Accuracy | 0.2857 | 0.6250 | +0.3393 |
| Perspective Accuracy | 1.0000 | 1.0000 | +0.0000 |
| Atomization Accuracy | 0.6667 | 1.0000 | +0.3333 |
| Context Reply Recall | 0.7000 | 1.0000 | +0.3000 |
| Negative FP | 0.0000 | 0.0000 | +0.0000 |

## Repair Contract

Baseline hurt/helped: `['EXT-016', 'EXT-024', 'EXT-049']` / `['EXT-047']`.  
Remediation hurt/helped: `[]` / `[]`.

| Original case | Attribution | Causal rule |
|---|---|---|
| EXT-016 | CANONICAL_NORMALIZATION_COUPLING | registered canonical plus duplicate custom predicate was rejected |
| EXT-024 | EVIDENCE_REPAIR | structured evidence object was discarded instead of narrowed to its text |
| EXT-049 | OTHER | semantic alignment one-to-one parse failure; no claim was discarded |

## Focused Regression

Cases: `19`; context: `11`; atomization: `8`.  
Context recall: `1.0000`; atomization: `0.8750`; negative FP: `0.0000`.  
Unknown/refusal fail-safe: `True`; topic-switch pass: `True`.

## Spurious Taxonomy

- Baseline: `EVALUATION_ALIGNMENT_ARTIFACT=2, SUPPORTED_EXTRA_NOT_IN_GOLD=4, UNSUPPORTED_SPURIOUS=2`.
- Remediation: `SUPPORTED_EXTRA_NOT_IN_GOLD=3`.

## Required Answers

1. Baseline Repair Hurt cases: `EXT-016`, `EXT-024`, `EXT-049`.
2. `EXT-016` was canonical/custom normalization coupling; `EXT-024` was structured evidence rejection; `EXT-049` was an alignment artifact, not claim loss.
3. Remediation semantic Match-to-Miss cases: `[]`.
4. EXT-056 cause recovered: `True`.
5. EXT-057 actor recovered: `True`.
6. EXT-059 negative answer recovered: `True`.
7. Unknown/refusal remains fail-safe: `True`.
8. Subject error cases changed from `15` to `11`.
9. USER_BELIEF Subject Accuracy changed from `0.2857` to `0.6250`.
10. Perspective regression: `False`.
11. EXT-047 should be two claims: social invitation and friend introduction. Production pass: `True`.
12. EXT-049 should be three independently updateable response dimensions. Production pass: `True`.
13. OVER_MERGE / OVER_SPLIT: `0 / 1`.
14. Original spurious taxonomy is shown above; supported extras are not counted as hallucinations.
15. Strong Upgrade remains low-frequency and non-destructive; baseline/remediation trigger rates: `0.0429` / `0.0143`.
16. Top remaining bottlenecks:
   - Subject attribution remains below threshold, with several actor-vs-relationship and USER_BELIEF cases requiring an explicit Gold subject-policy decision.
   - Supported extras and one OVER_SPLIT diagnostic still require Gold completeness or subject-policy review rather than extractor suppression.
   - Open-semantic atomization remains sampling-sensitive in the supplemental set even though the 70-case production layer passed this run.

## Freeze Decision

- `repair_not_systematically_lowering_recall`: `True`
- `repair_hurt_case_count_le_1`: `True`
- `context_reply_recall_ge_0_90`: `True`
- `subject_accuracy_ge_0_90`: `False`
- `perspective_accuracy_ge_0_98`: `True`
- `negative_fp_eq_0`: `True`
- `atomization_accuracy_ge_0_80`: `True`

Extraction V1 `freeze_candidate = false`.

Gate, Perspective policy, Strong upgrade policy, Admission, Store, Retrieval, Relation, and Lifecycle were not changed by this remediation.

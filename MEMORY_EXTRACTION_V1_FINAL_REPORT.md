# Memory Extraction V1 Final Report

Evaluation date: `2026-09-02`  
Prompt version: `memory-v2.5`  
Models: Flash `deepseek-v4-flash`, Strong `deepseek-v4-pro`, semantic matcher `deepseek-v4-pro`

## Evaluated Artifacts

| Scope | Dataset | Result artifact |
|---|---|---|
| Subject policy | `evals/memory/extraction_subject_v1.jsonl` (30) | `.data/evals/memory_extraction_subject_v1_final.json` |
| Full Extraction | `evals/memory/extraction_v1_70.jsonl` (70) | `.data/evals/memory_extraction_v1_final_70.json` |
| Focused regression | `evals/memory/extraction_v1_regression_19.jsonl` (19) | `.data/evals/memory_extraction_v1_final_focused_19.json` |

All three are live model runs. Gate does not participate in Extraction scoring, and
canonical/state identifiers do not participate in Extraction scoring.

## Directional History

The baseline, remediation, and final runs are not a controlled prompt-only A/B: the final run uses
the six reviewed subject labels and every run is a fresh live sample. The table is retained only as
a directional diagnostic.

| Metric | Baseline | Remediation | Final |
|---|---:|---:|---:|
| Flash Raw Claim Recall | 0.9241 | 1.0000 | 1.0000 |
| Post-Repair Claim Recall | 0.8861 | 1.0000 | 1.0000 |
| Production Claim Recall | 0.8987 | 1.0000 | 1.0000 |
| Fixed-Gold Spurious Rate | 0.1013 | 0.0366 | 0.0247 |
| Unsupported Spurious Rate | 0.0253 | 0.0000 | 0.0000 |
| Supported Extra Count | 4 | 3 | 2 |
| Kind Accuracy | 0.9859 | 0.9747 | 0.9620 |
| Subject Accuracy | 0.7887 | 0.8481 | 0.8987 |
| USER_BELIEF Subject Accuracy | 0.3750 | 0.5556 | 0.7778 |
| Perspective Accuracy | 1.0000 | 1.0000 | 1.0000 |
| Atomization Accuracy | 0.6667 | 1.0000 | 1.0000 |
| Context Reply Recall | 0.7000 | 1.0000 | 1.0000 |
| Negative FP Rate | 0.0000 | 0.0000 | 0.0000 |
| Empty Positive Rate | 0.0625 | 0.0000 | 0.0000 |

## Final Threshold Decision

| Requirement | Result | Threshold | Pass |
|---|---:|---:|---|
| Overall Subject Accuracy (70-case) | 0.8987 | >=0.90 | No |
| Subject-specialized Accuracy | 0.8519 | >=0.90 | No |
| USER_BELIEF Subject Accuracy (70-case) | 0.7778 | >=0.85 | No |
| USER_BELIEF Subject Accuracy (specialized) | 0.8333 | >=0.85 | No |
| Production Claim Recall | 1.0000 | >=0.95 | Yes |
| Post-Repair Claim Recall | 1.0000 | >=0.95 | Yes |
| Context Reply Recall | 1.0000 | >=0.90 | Yes |
| Perspective Accuracy | 1.0000 | >=0.98 | Yes |
| Atomization Accuracy | 1.0000 | >=0.80 | Yes |
| Negative FP Rate | 0.0000 | =0 | Yes |
| Unsupported Spurious Rate | 0.0000 | <=0.05 | Yes |
| Repair Hurt, 70-case | 0 cases | <=1 | Yes |

Because four required subject metrics miss their thresholds:

`Extraction V1 = NOT_FROZEN`

This is a strict threshold decision. High recall and restraint do not compensate for inaccurate
subject attribution.

## Full 70-case Result

Dataset SHA256: `7fe9801c1f2b7367eb30f1b02d6587896244c43c2c99c6c901c507b2cf2ba550`

| Metric | Flash Raw | Post-Repair | Production Cascade |
|---|---:|---:|---:|
| Claim Recall | 1.0000 | 1.0000 | 1.0000 |
| Spurious Claim Rate | 0.0247 | 0.0247 | 0.0247 |
| Kind Accuracy | 0.9367 | 0.9620 | 0.9620 |
| Subject Accuracy | 0.9241 | 0.8987 | 0.8987 |
| Perspective Accuracy | 0.1266 | 1.0000 | 1.0000 |
| Atomization Accuracy | 1.0000 | 1.0000 | 1.0000 |
| Context Reply Recall | 1.0000 | 1.0000 | 1.0000 |
| Negative FP Rate | 0.0000 | 0.0000 | 0.0000 |

Production errors: 8 subject cases, 3 kind cases, and 2 supported extras counted in the generic
fixed-Gold spurious rate. The two extras, in EXT-005 and EXT-006, are
`SUPPORTED_EXTRA_NOT_IN_GOLD`; unsupported spurious count and rate are both zero. They are not
classified as hallucinations. Safe Repair helped/hurt `0/0` cases. Strong Upgrade triggered once,
added no matched claim, and caused no harm.

Residual subject cases are EXT-014, EXT-015, EXT-020, EXT-024, EXT-029, EXT-033, EXT-034, and
EXT-052. EXT-014/015/020/052 collapse actor-focused propositions to `relationship`;
EXT-024/029 collapse belief targets to `relationship`; EXT-033 broadens partner personal status.
EXT-034 is a `GOLD_POLICY_INCONSISTENCY`: its current Gold disagrees with the new policy used for
EXT-033 and SUBJ-006, so it is not counted as a proven extractor defect in the qualitative review.
No Gold is changed here. This non-uniform residual shape is why another broad prompt tweak is not
justified in this closeout.

Final live telemetry:

| Component | Calls | Failures | p50 ms | p95 ms | Total tokens |
|---|---:|---:|---:|---:|---:|
| Flash | 70 | 0 | 2383.03 | 4103.85 | 423733 |
| Strong | 1 | 0 | 10065.67 | 10065.67 | 6473 |
| Semantic matcher | 129 | 0 | 3183.81 | 4865.24 | 123844 |

## Subject-specialized Result

Dataset SHA256: `0f6cac43aa871391094108a3264964f25e3b62391f0008d6658b9db43108fc12`

| Metric | Result |
|---|---:|
| Claim Recall | 0.9000 |
| Subject Accuracy | 0.8519 |
| USER_BELIEF Subject Accuracy | 0.8333 |
| Perspective Accuracy | 1.0000 |
| Context Reply Recall | 1.0000 |
| Negative FP Rate | 0.0000 |

SUBJ-002, SUBJ-006, SUBJ-010, and SUBJ-017 retain subject errors. SUBJ-003, SUBJ-013, and
SUBJ-022 were missed after repair. On this diagnostic set Safe Repair hurt four cases and Strong
Upgrade recovered one; this does not alter the 70-case repair threshold, but it remains a known
boundary risk.

The reported Subject Accuracy is `23/27`, because only semantically matched claims enter the field
denominator. Including the three omissions as failed subject coverage yields `23/30 = 0.7667`.
USER_BELIEF subject accuracy is `5/6`. Atomization is not applicable to this one-claim-per-case
stress set.

## Focused 19-case Regression

Dataset SHA256: `08da513899bdf719135ddd865dfd9859dd92ba70283b18e5d9fccb243556a058`

Production Claim Recall, Subject Accuracy, Perspective Accuracy, Atomization Accuracy, and Context
Reply Recall were all `1.0000`; Negative FP Rate was `0.0000`. One kind mismatch remained. This
confirms that the previous context-reply and atomization repairs did not regress in the focused set.

## Changes In This Closeout

- Clarified Subject Policy v1 in the production extraction prompt and advanced its version to
  `memory-v2.5`.
- Added the independent 30-case subject policy set and regression harness.
- Corrected six Gold subject labels after policy review: EXT-011, EXT-020, EXT-024, EXT-028 belief,
  EXT-029, and EXT-033. The policy reasons are recorded in
  `MEMORY_EXTRACTION_SUBJECT_POLICY_REVIEW.md`.
- Corrected the USER_BELIEF subject metric denominator so all belief claims across slices are
  measured.

The final 70-case artifact uses reviewed Gold and a fresh live sample, so comparisons with earlier
baseline/remediation artifacts are directional diagnostics, not a controlled prompt-only A/B.

No Gate, context-reply policy, perspective policy, Safe Repair, Strong Upgrade, ontology,
Normalizer, Admission, Relation, Lifecycle, or Store behavior was modified as part of the subject
closeout.

## Known Limitations

1. Actor-focused durable behaviors and events can still be broadened to `relationship`.
2. Partner personal status and partner intent can still be broadened to `relationship`.
3. Subject-specialized short durable statements exposed Safe Repair recall loss not present in the
   full 70-case run.
4. EXT-034 retains a Gold-policy inconsistency requiring a later dataset review.
5. Live model sampling remains non-deterministic; these artifacts are one final run, not a repeated
   stability estimate.

## Repository Verification

| Check | Result |
|---|---|
| Subject/Prompt/Extraction closeout regressions | 18 passed |
| Normalization evaluator/CLI regressions | 13 passed |
| All Memory tests | 775 passed |
| Full repository | 1388 passed, 1 unrelated Date failure |
| Ruff | passed |
| `git diff --check` | passed; existing LF-to-CRLF notices only |

The single repository failure is
`test_exact_postponed_activation_scenario_builds_full_plan`: its fixed expectation is
`2026-08-29`, while the current reference date resolves "this Saturday" to `2026-09-05`. This task
does not modify Date behavior or that unrelated expectation.

The required next phase is the independent Normalization V1 observational baseline. Extraction
must remain marked `NOT_FROZEN`; this task does not authorize a second prompt adjustment.

# TwoStage Extraction Diagnostic v0.1

## Before fix

- Stage1 success: `0/18`
- Stage2 success: `0`
- Native TwoStage completion: `0/18`
- Fallback: `18/18`
- Fallback reasons: `{'STAGE1_SCHEMA_ERROR': 18}`

Primary cause: Stage1 prompt-output contract mismatch produced invalid CoarseExtraction fields and enums.

## After fix

Cases: `18`
Stage1 success: `18/18`
Stage2 success: `17/17` (not called for `1` negative-gate cases)
Native TwoStage completion: `18/18`
Fallback count: `0`
Multi-claim cases meeting expected count: `4/4`
Stage1 multi-proposition decomposition: `3/4`
Stage2 multi-claim preservation: `4/4`

## Fallback reasons

- none

## Multi-claim cases

| Case | Stage1 props | Expected min | Stage2 claims | Expected | Kind coverage |
|---|---:|---:|---:|---:|---|
| S07 | 2 | 2 | 2 | 2 | PASS |
| S08 | 2 | 2 | 2 | 2 | PASS |
| S12 | 2 | 3 | 2 | 2 | PASS |
| S17 | 2 | 2 | 2 | 2 | PASS |

## Cost

- Calls: `35` (`1.9444` per case)
- Tokens: `21386` (`1188.1111` per case)
- Prompt/completion tokens: `18244/3142`

## Cases

| Case | Stage1 | Stage2 | Native | Reason |
|---|---|---|---|---|
| S01 | PASS | PASS | PASS | - |
| S02 | PASS | PASS | PASS | - |
| S03 | PASS | PASS | PASS | - |
| S04 | PASS | PASS | PASS | - |
| S05 | PASS | PASS | PASS | - |
| S06 | PASS | PASS | PASS | - |
| S07 | PASS | PASS | PASS | - |
| S08 | PASS | PASS | PASS | - |
| S09 | PASS | PASS | PASS | - |
| S10 | PASS | PASS | PASS | - |
| S11 | PASS | PASS | PASS | - |
| S12 | PASS | PASS | PASS | - |
| S13 | PASS | PASS | PASS | - |
| S14 | PASS | PASS | PASS | - |
| S15 | PASS | PASS | PASS | - |
| S16 | PASS | PASS | PASS | - |
| S17 | PASS | PASS | PASS | - |
| S18 | PASS | N/A | PASS | - |

Store mutation permitted: `False`

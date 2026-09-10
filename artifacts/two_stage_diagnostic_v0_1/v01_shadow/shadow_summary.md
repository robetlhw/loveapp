# LoveApp Memory Ontology Shadow Evaluation v1

Dataset: `D:\Users\90994\chrome\LoveApp_Memory_Ontology_Sanity_Gold_v0_1.jsonl`
Dataset SHA256: `f294b4d52ef162bdd6864e053e4d78cccf5e4542348ef269d7e4273b90160555`
Store mutation permitted: `False`

> SingleStage and opt-in TwoStage use the same Gold and deterministic normalization. This is a shadow diagnostic, not a production switch.

## Native execution

- Native completion: `39/40` (`0.975`)
- Fallback: `1` (`0.025`)
- Fallback reasons: `{'ATOMIC_EXTRACTION_VALIDATION_ERROR': 1}`

## Metrics

| Metric | SingleStage | TwoStage | Delta |
|---|---:|---:|---:|
| extraction_pass_rate | 0.7 | 0.2 | -0.5 |
| normalized_pass_rate | 0.7 | 0.3 | -0.4 |
| multi_claim_completeness | 0.1667 | 0.0 | -0.1667 |
| forbidden_claim_violation_rate | 0.125 | 0.025 | -0.1 |
| kind_accuracy | 0.8636 | 0.9318 | 0.0682 |
| subject_accuracy | 0.8913 | 0.7174 | -0.1739 |
| perspective_accuracy | 0.913 | 0.9565 | 0.0435 |
| canonical_custom_accuracy | 0.8485 | 0.5455 | -0.303 |
| event_type_accuracy | 0.6667 | 0.3333 | -0.3334 |
| pattern_metric_accuracy | 0.6667 | 0.1111 | -0.5556 |

## TwoStage native-only metrics

- Cases: `39`
- Extraction pass: `0.1795`
- Normalized pass: `0.2821`
- Multi-claim completeness: `0.0`
- Forbidden claim rate: `0.0256`

## Cost per case

| Mode | Cases | Calls | Avg calls | Tokens | Avg tokens |
|---|---:|---:|---:|---:|---:|
| single_stage | 40 | 41 | 1.025 | 286764 | 7169.1 |
| two_stage_with_fallback | 40 | 81 | 2.025 | 56000 | 1400.0 |
| two_stage_native_only | 39 | 78 | 2.0 | 47771 | 1224.8974 |

## Telemetry

- SingleStage calls: `41`
- TwoStage calls: `81`
- Call delta: `40.0`
- SingleStage tokens: `286764`
- TwoStage tokens: `56000`

## Case comparison

| Case | Single normalized | Two normalized | Stage used | Fallback | Reason | Changed |
|---|---|---|---|---|---|---|
| bd_001 | FAIL | FAIL | two_stage | False | - | False |
| bd_002 | FAIL | FAIL | two_stage | False | - | False |
| bd_003 | PASS | PASS | two_stage | False | - | False |
| bd_004 | FAIL | FAIL | two_stage | False | - | False |
| bd_005 | PASS | PASS | two_stage | False | - | True |
| bd_006 | FAIL | FAIL | two_stage | False | - | False |
| bd_007 | FAIL | FAIL | two_stage | False | - | False |
| bd_008 | FAIL | FAIL | two_stage | False | - | False |
| event_001 | PASS | PASS | two_stage | False | - | False |
| event_002 | PASS | FAIL | two_stage | False | - | True |
| event_003 | FAIL | FAIL | two_stage | False | - | False |
| event_004 | PASS | FAIL | two_stage | False | - | True |
| event_005 | FAIL | FAIL | two_stage | False | - | False |
| event_006 | PASS | PASS | two_stage | False | - | False |
| event_007 | PASS | PASS | two_stage | False | - | False |
| event_008 | PASS | PASS | two_stage | False | - | False |
| pattern_001 | PASS | PASS | single_stage_fallback | True | ATOMIC_EXTRACTION_VALIDATION_ERROR | False |
| pattern_002 | FAIL | FAIL | two_stage | False | - | False |
| pattern_003 | PASS | FAIL | two_stage | False | - | True |
| pattern_004 | PASS | FAIL | two_stage | False | - | True |
| pattern_005 | PASS | FAIL | two_stage | False | - | True |
| pattern_006 | FAIL | FAIL | two_stage | False | - | False |
| pattern_007 | PASS | FAIL | two_stage | False | - | True |
| pattern_008 | PASS | FAIL | two_stage | False | - | True |
| pref_001 | PASS | FAIL | two_stage | False | - | True |
| pref_002 | PASS | PASS | two_stage | False | - | True |
| pref_003 | PASS | FAIL | two_stage | False | - | True |
| pref_004 | PASS | PASS | two_stage | False | - | True |
| pref_005 | PASS | PASS | two_stage | False | - | True |
| pref_006 | FAIL | FAIL | two_stage | False | - | False |
| pref_007 | PASS | FAIL | two_stage | False | - | True |
| pref_008 | FAIL | FAIL | two_stage | False | - | False |
| sf_001 | PASS | FAIL | two_stage | False | - | True |
| sf_002 | PASS | FAIL | two_stage | False | - | True |
| sf_003 | PASS | FAIL | two_stage | False | - | True |
| sf_004 | PASS | FAIL | two_stage | False | - | True |
| sf_005 | PASS | FAIL | two_stage | False | - | True |
| sf_006 | PASS | FAIL | two_stage | False | - | True |
| sf_007 | PASS | PASS | two_stage | False | - | False |
| sf_008 | PASS | PASS | two_stage | False | - | False |

No Store, lifecycle, relation, or production-default behavior was changed by this evaluation.

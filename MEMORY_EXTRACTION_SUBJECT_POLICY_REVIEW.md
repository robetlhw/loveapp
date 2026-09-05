# Memory Extraction Subject Policy Review

Review date: `2026-09-02`  
Policy: `Subject Policy v1`  
Prompt version evaluated: `memory-v2.5`  
Primary pre-review artifact: `.data/evals/memory_extraction_v1_remediation_70.json`
Pre-review dataset SHA256: `a9ed2f8b26e829171e1de98d59b26de2e823b44c7845cdf3fddf0d88e6a58b00`

## Scope

This review closes the single permitted subject-attribution adjustment. It separates:

- `subject`: the entity or relationship described by the proposition;
- `perspective`: how the proposition is known;
- `payload.actor`: the actor inside an event or behavior.

The bounded subject vocabulary is `user`, `partner`, and `relationship`. A belief holder is not
automatically the subject, and an event actor is not automatically the subject of a shared state or
interaction metric.

## Policy Decisions

| Proposition focus | Subject | Notes |
|---|---|---|
| User-only fact, preference, behavior, plan, or actor-focused event | `user` | Includes the user repeatedly initiating apologies. |
| Partner-only fact, preference, behavior, status, or actor-focused event | `partner` | Includes partner intent expressed as a user belief. |
| Shared relationship state, bilateral event, interaction metric, or relationship advice outcome | `relationship` | Grammatical actor alone does not make a dyadic metric partner-scoped. |

Perspective remains independent: both `partner` and `relationship` claims may use
`user_belief`; explicit partner statements remain `user_reported`. Third parties belong in the
payload or proposition, not in the bounded `subject` field.

## Pre-review Subject Errors

The latest 70-case remediation run contained 11 cases with `SUBJECT_ERROR`. The table records the
primary policy attribution. A Gold change was made only where Subject Policy v1 made the previous
label inconsistent.

| Case | Previous Gold | Observed | Primary category | Resolution |
|---|---|---|---|---|
| EXT-011 | relationship | partner | GOLD_POLICY_AMBIGUOUS | Partner boundary action; Gold changed to partner. |
| EXT-015 | partner | relationship | RELATIONSHIP_AS_SUBJECT_TOO_BROAD | Keep partner Gold; actor-focused voluntary action. |
| EXT-020 | relationship | user | GOLD_POLICY_AMBIGUOUS | Repeated user-only apology behavior; Gold changed to user. |
| EXT-021 | relationship | partner | PARTNER_AS_SUBJECT_TOO_NARROW | Keep relationship Gold; message length is a dyadic metric. |
| EXT-026 | relationship | partner | PARTNER_AS_SUBJECT_TOO_NARROW | Keep relationship Gold; family acceptance is modeled as relationship integration. |
| EXT-028 | relationship | partner | GOLD_POLICY_AMBIGUOUS | The belief proposition concerns partner interest; second Gold claim changed to partner. |
| EXT-029 | relationship | user | BELIEVER_AS_SUBJECT | Believer is not subject; Gold resolved to partner and model output remained wrong. |
| EXT-033 | relationship | partner | GOLD_POLICY_AMBIGUOUS | Partner's personal relationship status; Gold changed to partner. |
| EXT-044 | relationship | partner | OUTCOME_POLICY_ERROR | Keep relationship Gold; advice outcome is the effect on the relationship. |
| EXT-049 | relationship | partner | PARTNER_AS_SUBJECT_TOO_NARROW | Keep relationship Gold for reply-speed and message-length metrics. |
| EXT-052 | partner | other_male | ACTOR_AS_SUBJECT | Keep partner Gold; third party cannot become the bounded subject. |

No case was relabeled merely to match a model output. In particular, EXT-021, EXT-026, EXT-044,
EXT-049, and EXT-052 retain Gold that disagreed with the observed response.

The six Gold changes also do not inflate the final score: the final sampled outputs would match
`73/79 = 0.9241` subjects under the previous labels, but match `71/79 = 0.8987` under the reviewed
labels. Policy consistency, not score direction, determined the changes.

## Gold Resolutions

| Case | Old subject | New subject | Policy reason |
|---|---|---|---|
| EXT-011 | relationship | partner | The saved proposition is the partner withdrawing her hand and setting a boundary. |
| EXT-020 | relationship | user | The repeated durable behavior is performed by the user alone. |
| EXT-024 | relationship | partner | The belief describes the partner's possible intent to end the relationship. |
| EXT-028 belief claim | relationship | partner | Perspective is user belief; subject is the partner whose interest is questioned. |
| EXT-029 | relationship | partner | The belief describes the partner's possible intent, not the believer. |
| EXT-033 | relationship | partner | Being single is the partner's personal status, not the shared relationship state. |

Only `subject` and the accompanying policy note changed in these six cases. Kind, perspective,
semantic target, evidence, and the original user text remained fixed. The reviewed 70-case dataset
SHA256 is `7fe9801c1f2b7367eb30f1b02d6587896244c43c2c99c6c901c507b2cf2ba550`.

## Prompt Adjustment

The one permitted adjustment changed the Memory extraction prompt from `memory-v2.4` to
`memory-v2.5`. It added the three-way distinction above and compact contrasts for actor-focused
events, shared states, dyadic interaction metrics, belief propositions, and advice outcomes. Gate,
context-reply policy, perspective policy, Safe Repair, Strong Upgrade, Admission, Relation,
Lifecycle, and Store behavior were not changed for this adjustment.

## Subject-specialized Live Result

Artifact: `.data/evals/memory_extraction_subject_v1_final.json`  
Dataset: `evals/memory/extraction_subject_v1.jsonl` (30 cases)  
Dataset SHA256: `0f6cac43aa871391094108a3264964f25e3b62391f0008d6658b9db43108fc12`

| Metric | Production Cascade |
|---|---:|
| Claim Recall | 0.9000 |
| Subject Accuracy | 0.8519 |
| USER_BELIEF Subject Accuracy | 0.8333 |
| Perspective Accuracy | 1.0000 |
| Context Reply Recall | 1.0000 |
| Negative FP Rate | 0.0000 |

Subject Accuracy is `23/27`; the denominator contains matched claims only. If the three missing
claims are conservatively treated as failed subject coverage, coverage would be `23/30 = 0.7667`.
USER_BELIEF Subject Accuracy is `5/6`. Atomization is not applicable to this single-claim subject
suite even though the generic evaluator renders a zero-valued atomization field.

Residual subject errors were:

| Case | Expected | Actual | Diagnosis |
|---|---|---|---|
| SUBJ-002 | user | relationship | Actor-focused repeated apology was broadened to the relationship. |
| SUBJ-006 | partner | relationship | Partner personal single status was broadened to the relationship. |
| SUBJ-010 | partner | relationship | Belief about partner intent was broadened to the relationship. |
| SUBJ-017 | partner | relationship | Partner voluntary action was broadened to a joint event. |

The production cascade also missed SUBJ-003, SUBJ-013, and SUBJ-022. Safe Repair reduced recall by
four claims (`0.1333`) on this diagnostic set; one Strong Upgrade recovered one claim. These are
reported as observed boundary failures, not addressed by a second prompt change.

One separate Gold-policy inconsistency remains visible in the full set: EXT-033 and SUBJ-006 define
a partner's personal relationship status as `partner`, while EXT-034 still expects `relationship`
for a closely related personal-status proposition. EXT-034 is therefore classified as
`GOLD_POLICY_INCONSISTENCY`, not as a proven extractor defect. This review records the issue and
does not silently change that Gold.

## Final Finding

Subject Policy v1 is explicit and the dataset policy ambiguity has been resolved, but live subject
attribution is not yet stable enough to freeze. The specialized threshold is `>=0.90`; the observed
result is `0.8519`, and USER_BELIEF subject accuracy is `0.8333` against `>=0.85`.

`Extraction V1 = NOT_FROZEN`

Per the task stop condition, no second subject-prompt adjustment is performed in this phase.

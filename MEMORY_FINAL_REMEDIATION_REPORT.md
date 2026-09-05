# LoveApp Memory Final Remediation Report

## Scope

This remediation completes the reviewed Custom Semantic Eval Contract and the
Admission-to-Lifecycle alignment for governed canonical transitions.

The following boundaries remain unchanged:

- Memory Gate rules
- Retriever model, weights, thresholds, and Top-K
- Semantic Judge prompt
- Deterministic Validator thresholds
- Store/UoW API
- Long-tail destructive lifecycle commit

`Store mutation permitted` remains `false` for all Long-tail Live evaluation
runs. Phase 2C remains OFF.

## Root Causes

### Custom Semantic Evaluation

The evaluator treated raw predicate string equality as semantic identity. A
production claim such as `social_circle_integration` could therefore fail an
expectation such as `invites_user_to_friend_activities`, even when the kind,
subject, evidence, and reviewed qualifiers represented the same independently
evolvable memory concept.

The fix is a closed, reviewed `acceptable_representations` contract. It does
not use embedding similarity, LLM approval, or automatic alias expansion.
Custom aliases may be constrained only by the reviewed payload fields
`activity_type`, `direction`, `frequency`, `object`, and `topic`, plus
`evidence_contains_any`. Unknown representation or qualifier fields fail
validation.

### Governed Canonical Transition

An explicit current canonical value that differed from an active value was
first treated as a generic conflict. Admission downgraded it to PROPOSED, after
which the existing confirmed-protection rule correctly prevented UPDATE. The
Lifecycle layer therefore never received an eligible confirmed transition.

The fix introduces a narrow governed transition eligibility assessment before
Admission. It requires:

- a registered canonical governed state and valid value;
- one active, same-subject, same-dimension, different-value target;
- explicit USER_REPORTED evidence with no inference;
- confidence at least `0.90`;
- evidence spans present in the source text;
- a valid and forward temporal direction.

Only an eligible candidate receives `CONFIRM` with reason
`confirmed_governed_transition`. Existing ClaimRelation and Lifecycle logic
then produce `UPDATE` and `replace_state:<dimension>` without a new Store API.

For a unique target whose model output omitted structured time, the fallback is
still fail-closed unless the incoming claim has a structured time anchor and
its evidence is explicitly current/recent. Historical evidence such as
`去年` remains ineligible for destructive replacement.

## Reviewed Custom Mappings

| Reviewed semantic concept | Expected representation | Qualified production representation |
|---|---|---|
| Friend activity participation pattern | invitation/participation predicates | `social_circle_integration` with reviewed object, activity, frequency, and evidence qualifiers |
| Friend introduction willingness/pattern | introduction predicates | `social_circle_integration` with introduction-specific object/activity qualifiers |
| Broad social-circle integration | reduced integration | `social_circle_integration` with broad-integration qualifiers |
| Single-day contact absence event | `single_day_no_contact` | `contact_absence` with contact-absence evidence |
| Single gathering exclusion event | `single_gathering_not_invited` | `excluded_from_gathering` with gathering qualifiers |
| Money-conflict frequency pattern | `money_conflict_pattern` | `conflict_frequency` with `topic=money` and reviewed frequency |

The dataset contains 13 claim-level reviewed aliases. Invitation,
introduction, broad integration, and single-event memories remain distinct by
qualifier, kind, and evidence rather than being merged into one broad alias.

Mappings were deliberately not added for:

- `LT-P-001/t2`: belief/subject semantics differ;
- `LT-C-001/t1,t2`: kind and memory-role mismatch;
- `LT-A-002/t1`: compound expectation conflicts with atomic production;
- `LT-U-001/t2`: event/kind mismatch;
- `LT-U-002/t2`: unreviewed preference canonicalization mismatch.

## Remaining Legacy Mismatches

Replaying the original 15 Live V2 legacy mismatches through the reviewed
contract accepts 13 and leaves 2 expected-claim mismatches:

- `LT-U-001/t2/unrelated-social`
- `LT-U-002/t2/unrelated-restaurant`

The final stochastic Live V3 run recorded 3 expected-claim rows with the
literal `legacy_semantic_identity_mismatch` reason: the two rows above plus
`LT-A-002/t1/partial-invite-intro`, because that run emitted a compound claim.
`LT-A-002/t1` remains an atomization/expectation issue and is intentionally not
added to the alias contract.

Two unmatched extra model claims (`LT-R-004/t1` and `LT-M-001/t3`) also carry
the legacy reason in trace data, but they are not expected-claim observations
and are excluded from the count above.

## Canonical Transition Results

| Case | Admission | Relation | Lifecycle | Result |
|---|---|---|---|---|
| CG-001 initiation balance | `CONFIRM / confirmed_governed_transition` | `UPDATE`, unique T1 target | `replace_state:interaction.initiation_balance` | Full V3 PASS; repeat 5/5 PASS |
| CG-002 emotional disclosure, valid `high -> low` outputs | `CONFIRM / confirmed_governed_transition` | `UPDATE`, unique T1 target | `replace_state:interaction.emotional_disclosure` | 2/2 valid normalized repeat observations PASS |
| CG-003 weak belief | PROPOSED | CONTRADICTION, no destructive target action | none | PASS; confirmed old state retained |
| CG-004 historical incoming state | PROPOSED/non-UPDATE | no current-state replacement | none | PASS; current state retained |

The CG-002 repeat produced three additional `rare`/`rarely` values. Those are
not governed `interaction.emotional_disclosure` values, so they correctly
failed at Normalization and did not mutate lifecycle state. The complete
repeat result is 2/5 scenario passes, with every valid `high -> low`
observation passing the Admission, Relation, and Lifecycle chain.

No weak belief was promoted to a confirmed governed transition. Count: `0`.

## Historical Review

`CANONICAL_TEMPORAL_REVIEW.md` records the manual review of `LT-T-001` and
`LT-T-002`. Their historical and current intervals do not overlap. Historical
rows must remain available as evidence and must not be destructively
superseded merely because the canonical state dimension matches.

The deterministic CG-004 regression confirms that historical incoming evidence
cannot replace a current confirmed state. The final Live V3 run produced no
historical-over-current destructive mutation. The remaining LT-T relation
mismatches are documented limitations, not authorization to broaden lifecycle.

## Extraction Review

`EXTRACTION_FAILURE_REVIEW.md` classifies the seven Live V2 Gate-admitted
unmatched expected claims as:

- wrong semantic identity: 2;
- wrong kind: 2;
- schema/evidence failure: 1;
- atomization issue: 2.

No Flash prompt or extraction architecture was changed in this remediation.

## Final Live V3

Primary artifact:

`.data/evals/memory_longtail_realistic_live_20260831_005918_924538.json`

Generated Markdown:

`MEMORY_LONGTAIL_REALISTIC_LIVE_EVAL_REPORT_V3.md`

| Metric | Result |
|---|---:|
| Scenario pass | 4/26 |
| Gate recall / precision | 0.7872 / 1.0 |
| Extraction semantic success | 32/38 = 0.8421 |
| Canonical semantic identity | 11/16 = 0.6875 |
| Custom semantic identity | 4/16 = 0.25 |
| Overall semantic identity | 15/32 = 0.4688 |
| Canonical governance pass | 8/16 |
| Retrieval Recall@5 | 0.8333 over 6 eligible observations |
| Relation accuracy | 0.3333 |
| Completed Judge accuracy | 0.6 over 5 comparable expectations |
| UPDATE precision | 0.5 |
| Target precision | 0.7143 |
| Judge transport / parse failures | 0 / 0 |

The strict Custom rate in this stochastic run reflects payload/value variants
outside the reviewed qualifier contract. It is not addressed by automatically
adding aliases.

### Safety

| Safety invariant | Violations |
|---|---:|
| False destructive update | 0 |
| Confirmed overwrite | 0 |
| Event over pattern | 0 |
| Weak belief overwrite | 0 |

Both incorrect UPDATE proposals in the final Live V3 run were denied by the
Validator. Phase 2C is NOT APPROVED because relation accuracy and UPDATE
precision remain below the required confidence for destructive commit.

## Repeat Artifacts

- CG-001, repeat 5:
  `.data/evals/memory_longtail_realistic_live_20260831_010212_043526.json`
- CG-002, repeat 5:
  `.data/evals/memory_longtail_realistic_live_20260831_010319_329030.json`
- MEM-008, repeat 3:
  `.data/evals/memory_foundation_live_20260830_171345_399050.json`
- MEM-003 diagnostic repeat 3:
  `.data/evals/memory_foundation_live_20260830_171614_307377.json`

CG-001 relation, target, and lifecycle were identical in all five runs. CG-002
showed normalization drift, but both valid governed observations were
identical and correct. MEM-008 passed 3/3. MEM-003 passed 3/3 in the diagnostic
repeat after one failure in the single full Live Foundation run.

## Regression Results

- Canonical transition plus Live evaluator targeted tests: `44 passed`.
- All Memory tests: `619 passed`.
- Memory Foundation deterministic: `18/18 passed`.
- Long-tail fixture: 26 scenarios completed; baseline metrics preserved.
- Full Live Foundation: 18/18 executed; 16 semantic pass, 1 warning, 1 fail.
  The single failure was MEM-003 model normalization/lifecycle output; its
  immediate three-run diagnostic repeat passed 3/3.
- Full repository: `1188 passed, 1 failed`.
- Ruff on all modified Python files: passed.
- `git diff --check`: passed.

The sole repository failure is
`tests/test_date_phase_b5_1.py::test_exact_postponed_activation_scenario_builds_full_plan`.
It hard-codes `2026-08-29` for `这周六`; at the current test date the application
correctly resolves `2026-09-05`. DatePlan is outside this remediation and was
not changed.

## Final Status

- Memory Foundation: FROZEN; deterministic PASS, Live model output still has
  observed stochastic variance.
- Custom Semantic Eval Contract: REVIEWED.
- Governed Canonical Transition: WORKING for valid normalized governed values.
- Gate: ACCEPTABLE and unchanged.
- Extraction: ACCEPTABLE for this phase; known drift documented.
- Retriever: SHADOW / NEEDS MORE DATA.
- Semantic Judge: SHADOW / NEEDS IMPROVEMENT.
- Validator: SAFE in the evaluated set.
- Phase 2C lifecycle commit: OFF / NOT APPROVED.

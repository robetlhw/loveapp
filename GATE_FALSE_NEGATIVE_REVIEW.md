# Gate False-Negative Review

Source: `memory_longtail_realistic_live_20260830_200605_final.json`

This is a review artifact, not a Gate change request. The V2 remediation leaves Gate
rules unchanged. `Should really be durable memory` distinguishes durable facts/patterns
from short-lived events and protected user beliefs; it does not inherit the fixture label
without review.

## LT-A-001

### t1

- Input: `她最近经常跟我聊工作和家庭里的事情。`
- Current reason: `no_durable_signal`
- Expected reason: repeated interaction topic scope
- Should really be durable memory: **yes**, as an interaction pattern with separable work
  and family topic dimensions
- Recommended future action: review semantic topic-scope coverage and atomization; do not
  add a sentence-specific Gate rule

### t2

- Input: `现在她还是会跟我聊工作，但已经很少再谈家里的事情了。`
- Current reason: `no_durable_signal`
- Expected reason: sustained partial topic-scope change
- Should really be durable memory: **yes**, but only if work and family dimensions remain
  independently represented
- Recommended future action: future Gate/Extraction review; preserve partial-change safety

## LT-C-002

### t1

- Input: `她最近很愿意跟我聊工作压力。`
- Current reason: `no_durable_signal`
- Expected reason: recurring work-related emotional disclosure
- Should really be durable memory: **uncertain**; the wording expresses willingness but no
  explicit cadence or duration
- Recommended future action: review the dataset expectation before widening Gate coverage

## LT-C-003

### t1

- Input: `她最近很愿意和我一起出去见朋友。`
- Current reason: `no_durable_signal`
- Expected reason: current social-integration tendency
- Should really be durable memory: **uncertain**; useful if treated as a current tendency,
  not a stable fact
- Recommended future action: review TTL and interaction-pattern criteria first

### t2

- Input: `不过我们现在还很少讨论长期未来规划。`
- Current reason: `no_durable_signal`
- Expected reason: quantified current future-planning topic pattern
- Should really be durable memory: **yes**
- Recommended future action: future topic-scope Gate taxonomy review

## LT-M-001

### t2

- Input: `前几天我们还聊了很久，她也跟我说了不少工作上的烦心事。`
- Current reason: `no_durable_signal`
- Expected reason: reviewed fixture expects an interaction pattern
- Should really be durable memory: **uncertain**; this is one retrospective interaction
  event and does not by itself establish a sustained pattern
- Recommended future action: mark the pattern expectation for human review; do not broaden
  Gate solely for this case

## LT-P-002

### t1

- Input: `她明确说以后愿意跟我一起规划未来。`
- Current reason: `no_durable_signal`
- Expected reason: explicit future-planning willingness
- Should really be durable memory: **yes**, as an explicit reported statement with source
  provenance
- Recommended future action: future relationship-intent signal review

### t2

- Input: `我最近总担心她可能已经不想谈未来了。`
- Current reason: `no_durable_signal`
- Expected reason: protected user belief/perspective signal
- Should really be durable memory: **uncertain**; it may be useful as `user_belief`, but must
  never be stored as a partner fact or overwrite the explicit statement
- Recommended future action: review belief-memory product value before changing Gate

## LT-R-004

### t2

- Input: `但这段时间她开始明显回避谈这些未来计划。`
- Current reason: `no_durable_signal`
- Expected reason: sustained reversal in future-planning discussion
- Should really be durable memory: **yes**
- Recommended future action: future durable reversal coverage review; keep target resolution
  and lifecycle authorization downstream

## LT-U-002

### t1

- Input: `她最近工作压力很大。`
- Current reason: `no_durable_signal`
- Expected reason: current partner context
- Should really be durable memory: **uncertain**; useful only as a time-bounded current state,
  not the fixture's broad stable fact
- Recommended future action: review expectation kind and TTL semantics before Gate changes

## Review Summary

- Clear durable candidates: 5 turns
- Context-dependent or expectation-needs-review: 5 turns
- Gate behavior changed in V2: **no**
- Recommended next step: jointly review dataset kind/TTL assumptions before any independent
  Gate remediation

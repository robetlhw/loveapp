# Memory Normalization V1 Contract Snapshot

Snapshot date: 2026-09-02

## Production Call Path

The evaluated path is fixed `RawMemoryClaim` data converted to `AtomicClaim`, then
`AtomicClaim.to_candidate()` and `normalize_memory_candidate()`. It bypasses Gate, model
Extraction, Admission, Relation, Lifecycle planning, and Store writes.

Production calls the same path after `parse_memory_response()` performs bounded local repair and
`validate_memory_claim()` accepts the claim. The invalid-shape slice therefore records both the
Normalizer result and the ingress validator/repair outcome. A validator rejection is not silently
relabelled as a mapping miss.

Code ownership:

- Ingress repair and validation: `src/loveapp/application/memory_repair.py`
- Candidate normalizer: `src/loveapp/domain/memory_lifecycle.py::normalize_memory_candidate`
- Predicate adapter: `src/loveapp/domain/memory.py::normalize_candidate_predicate`
- Canonical registry and aliases: `src/loveapp/domain/memory_predicates.py`
- Lifecycle state registry: `src/loveapp/domain/memory_dimensions.py`

## Canonical Predicate Registry

The registry has 23 predicates. It does not encode `allowed_kinds`; therefore none of the rows
below has a registry-enforced kind ACL. The effective branch/requirements column records the
actual ingress and normalizer contract: `interaction_pattern` requires one normalized
`payload.metric`, `relationship_state` requires a registered dimension/value pair (except the
bounded open-world social-integration path), and preference domain selection runs only for
`kind=preference`. A registered canonical name by itself does not prove that its kind or payload is
semantically valid.

| Predicate | Effective branch / payload requirement (not a registry kind ACL) | State dimension | Allowed values |
|---|---|---|---|
| `contact.status` | current contact state | `relationship.contact_status` | normal, reduced, unavailable, restored |
| `relationship.stage` | relationship stage | `relationship.stage` | unknown, acquaintance, dating, committed, cooling_off, separated, reconciled |
| `relationship.repair_status` | repair process state | `relationship.repair_status` | not_started, intended, in_progress, completed, failed |
| `confession.status` | confession state | `relationship.confession_status` | intended, executed, accepted, rejected, withdrawn |
| `plan.status` | plan state | `relationship.plan_status` | proposed, confirmed, completed, cancelled, expired |
| `relationship.familiarity` | relationship state | `relationship.familiarity` | unfamiliar, low, moderate, high |
| `relationship.contact_opportunity` | relationship state | `relationship.contact_opportunity` | low, moderate, high |
| `relationship.conflict_status` | relationship state | `relationship.conflict_status` | active, cooling, repairing, resolved |
| `relationship.interaction_reciprocity` | relationship state | `relationship.interaction_reciprocity` | low, mixed, high |
| `partner.relationship_status` | partner relationship state | `partner.relationship_status` | unknown, single, partnered, married |
| `relationship.romantic_interest` | open canonical fact | none | open |
| `interaction.contact_frequency` | `payload.metric=contact_frequency` | `interaction.contact_frequency` | open |
| `interaction.topic_scope` | `payload.metric=topic_scope` | `interaction.topic_scope` | open |
| `interaction.channel` | `payload.metric=interaction_channel` | `interaction.channel` | open |
| `interaction.initiation_balance` | `payload.metric=initiation_balance` | `interaction.initiation_balance` | open |
| `interaction.response_engagement` | `payload.metric=response_engagement` | `interaction.response_engagement` | open |
| `interaction.emotional_disclosure` | `payload.metric=emotional_disclosure` | `interaction.emotional_disclosure` | open |
| `preference.general` | compatibility hint; untyped values fail closed to Custom | none | open |
| `preference.food.cuisine` | typed preference, food domain | none | open |
| `preference.food.spiciness` | typed preference, food domain | none | open |
| `preference.environment.noise` | typed preference; no registry semantic-domain metadata | none | open |
| `preference.activity.type` | typed preference, activity domain | none | open |
| `preference.budget.range` | typed preference, budget domain | none | open |

## Relationship State Registry

The lifecycle registry uses undotted dimensions, which are not identical to the dotted canonical
predicate dimensions above.

| Dimension | Allowed values | Aliases |
|---|---|---|
| `relationship_familiarity` | unfamiliar, low, moderate, high | familiarity, relationship_closeness |
| `contact_opportunity` | low, moderate, high | meeting_opportunity, interaction_opportunity |
| `contact_availability` | unavailable, limited, available | communication_availability, reachability |
| `conflict_status` | active, cooling, repairing, resolved | relationship_conflict_status |
| `interaction_reciprocity` | low, mixed, high | reciprocity, interaction_balance |
| `partner_relationship_status` | unknown, single, partnered, married | relationship_status, partner_status, romantic_availability |

The evaluator records both top-level `candidate.state_dimension/state_value` and
`candidate.payload.state_dimension/state_value`. It never chooses whichever representation happens
to match Gold. This exposes namespace or value drift such as `contact_availability/available` versus
`relationship.contact_status/normal`.

## Alias Layers

Normalization uses five bounded alias layers:

1. Exact raw predicate aliases for contact restoration/outage, repair, conflict, confession, and
   relationship-stage transitions.
2. State-dimension aliases mapped to a canonical predicate.
3. Interaction metric aliases such as `communication_frequency`, `meeting_frequency`,
   `initiative_balance`, and `reply_engagement`.
4. Preference type aliases for cuisine/food/dish, spiciness, noise/environment, activity/date, and
   budget/price.
5. State-value aliases scoped to a canonical predicate.

Aliases are normalized with NFKC, case folding, whitespace/hyphen to underscore conversion, and
identifier cleanup. They remain finite registries; unknown semantics are not fuzzy-matched.

### Predicate Aliases

| Input aliases | Canonical predicate | Fixed state value |
|---|---|---|
| `confession_accepted`, `confession_succeeded` | `confession.status` | `accepted` |
| `confessed`, `confessed_to_partner`, `confession_executed` | `confession.status` | `executed` |
| `confession_planned`, `intend_to_confess`, `plan_to_confess`, `plans_to_confess`, `will_confess` | `confession.status` | `intended` |
| `confession_rejected` | `confession.status` | `rejected` |
| `contact_frequency_declined`, `contact_reduced`, `reply_frequency_declined` | `contact.status` | `reduced` |
| `communication_recovered`, `contact_restored`, `partner_replied`, `partner_responded`, `partner_resumed_contact`, `received_reply`, `resumed_communication`, `resumed_contact`, `started_talking_again` | `contact.status` | `restored` |
| `calls_unanswered`, `contact_unavailable`, `ignoring_user`, `no_response`, `not_responding`, `partner_ignoring_user`, `partner_not_responding`, `partner_unreachable`, `stopped_responding`, `unable_to_contact_partner` | `contact.status` | `unavailable` |
| `response_restored`, `resumed_chatting` | `interaction.response_engagement` | `responsive` |
| `cold_war_active`, `conflict_active`, `in_conflict`, `unresolved_conflict` | `relationship.conflict_status` | `active` |
| `conflict_resolved`, `interaction.reconciliation`, `made_up`, `reconciled`, `reconciliation_occurred`, `relationship_reconciled`, `relationship_repaired`, `resolved_conflict` | `relationship.repair_status` | `completed` |
| `apologized_to_user`, `mutual_apology`, `partner_apologized`, `partner_said_sorry` | `relationship.repair_status` | `in_progress` |
| `has_crush_on`, `is_attracted_to`, `likes`, `likes_person` | `relationship.romantic_interest` | none |
| `relationship_confirmed`, `relationship_started` | `relationship.stage` | `dating` |
| `broke_up`, `relationship_ended`, `separated` | `relationship.stage` | `separated` |

### Lifecycle State Aliases

| Canonical lifecycle dimension | Dimension aliases | Value aliases |
|---|---|---|
| `relationship_familiarity` | `familiarity`, `relationship_closeness` | stranger->unfamiliar; not_familiar/slightly_familiar->low; medium->moderate; familiar/very_familiar->high |
| `contact_opportunity` | `interaction_opportunity`, `meeting_opportunity` | rare/few/limited->low; medium->moderate; regular/frequent/many->high |
| `contact_availability` | `communication_availability`, `reachability` | unreachable/blocked->unavailable; partial->limited; reachable/restored->available |
| `conflict_status` | `relationship_conflict_status` | unresolved/in_conflict->active; deescalating->cooling; reconciliation->repairing; repaired->resolved |
| `interaction_reciprocity` | `interaction_balance`, `reciprocity` | one_sided->low; uneven->mixed; balanced/mutual->high |
| `partner_relationship_status` | `partner_status`, `relationship_status`, `romantic_availability` | uncertain/not_sure/unconfirmed->unknown; available->single; in_relationship/has_partner/not_single/dating/has_boyfriend/has_girlfriend->partnered |

### Interaction Metric Aliases

| Canonical metric | Input aliases |
|---|---|
| `contact_frequency` | `communication_frequency`, `interaction_frequency`, `meeting_frequency` |
| `topic_scope` | `conversation_topic_scope`, `conversation_topics`, `personal_topic_frequency` |
| `interaction_channel` | `communication_channel`, `conversation_channel` |
| `initiation_balance` | `contact_initiation`, `contact_initiative`, `conversation_initiative`, `conversation_initiator`, `initiation_frequency`, `initiative_balance`, `initiative_pattern`, `interaction_initiative`, `interaction_initiator`, `topic_initiation`, `who_initiates` |
| `response_engagement` | `conversation_engagement`, `reply_engagement` |

`emotional_disclosure` has no alternate metric name in the current
`INTERACTION_METRIC_ALIASES` map.

### Preference Type And Value Aliases

| Input `preference_type` | Canonical predicate |
|---|---|
| `cuisine`, `food`, `dish` | `preference.food.cuisine` |
| `spiciness`, `spicy` | `preference.food.spiciness` |
| `noise`, `environment` | `preference.environment.noise` |
| `activity`, `date` | `preference.activity.type` |
| `budget`, `price` | `preference.budget.range` |

Cuisine values `日本料理`, `日本菜`, `japanese_food`, and `japanese_cuisine` normalize to `日料`.

### Dotted Canonical State Value Aliases

| Canonical predicate | Value aliases |
|---|---|
| `contact.status` | available/reachable->normal; limited/rare->reduced; blocked/unreachable->unavailable; recovered/available_again->restored |
| `relationship.conflict_status` | unresolved/in_conflict->active; deescalating->cooling; reconciliation->repairing; repaired->resolved |
| `relationship.stage` | friend/friends/friendship/ordinary_friends->acquaintance; partnered->dating; stable_relationship/long_distance->committed; breakup->separated |
| `confession.status` | confessed/confessed_pending_response/pending_response->executed |

## Representation Contract

- Canonical output: registered `canonical_predicate`, no `custom_predicate`.
- Custom output: normalized `custom_predicate`, no canonical or top-level state representation.
- Ingress rejects an unregistered canonical and rejects simultaneous canonical/custom declarations.
- Bounded repair may clear a duplicate custom declaration only when both declarations normalize to
  the same canonical meaning.
- Unknown raw predicates preserve a normalized Custom identifier. Missing identifiers fall back to
  `unknown` and require lifecycle review.
- Invalid state dimension/value pairs must not reach a persisted normalized candidate.
- `MemoryCandidate` itself does not enforce these invariants; production safety depends on ingress
  validation. This boundary is reported explicitly in the baseline.

## Normalizer / Validator Boundary

`normalize_memory_candidate()` may normalize preference kinds, interaction payloads, state aliases,
roles, bounded TTLs, and canonical/custom representation. It is not a second Extractor and does not
invent a semantic dimension absent from the fixed Raw claim.

`validate_memory_claim()` separately enforces source evidence, registered canonical names,
canonical/custom exclusivity, single preference values, interaction metrics, registered
relationship states, atomicity, and planned-event anchors. Admission and downstream governance are
outside Normalization V1 scoring.

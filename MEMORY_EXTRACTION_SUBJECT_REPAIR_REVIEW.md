# Memory Extraction Subject Safe Repair Review

Date: `2026-09-02`  
Source artifact: `.data/evals/memory_extraction_subject_v1_final.json`

## Finding

The four observed Safe Repair hurts are caused by the pre-Normalizer canonical/state validation
boundary. Safe Repair did not rewrite the claim subject. A semantically useful raw claim was
discarded because the model supplied an open or incompatible relationship-state representation
before deterministic Normalization could safely select canonical versus Custom output.

| Case | Raw claim | Post-Repair claim | Repair step / rejection | Why Match became Miss |
|---|---|---|---|---|
| SUBJ-003 | `relationship_state`, custom `desire_to_continue`, state `desire_to_continue=decreasing` | none | `default_fields`, `structured_evidence_text_narrowing`, `all_claims_invalid`, `partial_claims`; registered-state validation | The open state dimension is rejected before Normalization; no semantic subject rewrite occurred. |
| SUBJ-013 | `relationship_state`, custom `relationship_stability`, state `relationship_stability=decreased` | none | `structured_evidence_text_narrowing`, `all_claims_invalid`, `partial_claims`; registered-state validation | The open state dimension is rejected before Normalization. |
| SUBJ-021 | `relationship_state`, custom `boundary_agreed`, state `boundary_agreed=agreed` | none at Flash; Strong later recovered an `interaction_event` | `default_fields`, `structured_evidence_text_narrowing`, `all_claims_invalid`, `partial_claims` | Flash claim is rejected by the registered-state boundary; Strong recovery does not remove the repair-hurt observation. |
| SUBJ-022 | canonical `contact.status`, state `contact_status=paused` | none | `structured_evidence_text_narrowing`, `all_claims_invalid`, `partial_claims`; incompatible registered state value | `paused` is not a registered `contact.status` value, so the raw semantic claim is discarded before Normalization. |

## Boundary decision

Safe Repair remains structural: JSON/schema cleanup, exact evidence narrowing, and bounded known
normalization. It must not become a second semantic classifier and must not invent a canonical
state, kind, or subject. The validation-order issue belongs to the Normalization contract phase.


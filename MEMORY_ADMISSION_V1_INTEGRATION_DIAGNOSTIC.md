# Memory Admission V1 Integration Diagnostic

Deterministic candidates were routed through an isolated InMemoryMemoryStore and MemoryService. This diagnostic is not part of Layer Accuracy and permits no production-store mutation.

Cases: `12`  
Passed: `12`  
Strong verifier calls: `3`  
Store write/audit attempts: `12`  
Memory writes: `9`  
Transition audits: `12`  
TTL diagnostic: `True`

| Case | Expected | Admission | Strong called | Relation | Action | Planned status | Final status | TTL | Passed |
|---|---|---|---|---|---|---|---|---|---|
| ADM-001 | confirm | confirm | False | unrelated | add | confirmed | confirmed | None | True |
| ADM-013 | confirm | confirm | False | unrelated | add | confirmed | confirmed | None | True |
| ADM-051 | confirm | confirm | False | unrelated | add | confirmed | confirmed | None | True |
| ADM-014 | propose | propose | False | unrelated | add | proposed | proposed | None | True |
| ADM-062 | propose | propose | False | uncertain | add | proposed | proposed | True | True |
| ADM-063 | propose | propose | False | uncertain | add | proposed | proposed | None | True |
| ADM-003 | strong_review | strong_review | True | unrelated | add | proposed | proposed | None | True |
| ADM-007 | strong_review | strong_review | True | unrelated | add | proposed | proposed | None | True |
| ADM-011 | strong_review | strong_review | True | unrelated | add | proposed | proposed | None | True |
| ADM-033 | reject | reject | False | uncertain | reject | None | None | None | True |
| ADM-034 | reject | reject | False | uncertain | reject | None | None | None | True |
| ADM-037 | reject | reject | False | uncertain | reject | None | None | None | True |

## Action Intent TTL Evidence

- Consumer: `MemoryService.remember_recorded_message`
- Policy TTL: `14 days`
- Expected expires_at: `2026-09-16T10:00:00+00:00`
- Actual expires_at: `2026-09-16T10:00:00+00:00`
- Saved memory ids: `9f25bda0-ea39-46fe-be53-eda67d66f535`
- Check passed: `True`

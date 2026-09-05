# Memory Long-tail Write V1 Integration Diagnostic

- Cases: `36`
- Passed: `4`
- Store write attempts: `36`
- Transition audits: `36`
- Production Store mutation permitted: `False`
- Isolated InMemoryMemoryStore mutation: `True`

| Case | Expected | Actual | Action | New row | Superseded | Status changes | Passed |
|---|---|---|---|---|---|---:|---|
| LTW-001 | same | same | merge_or_refresh | False | - | 0 | True |
| LTW-002 | same | same | merge_or_refresh | False | - | 0 | True |
| LTW-003 | same | same | merge_or_refresh | False | - | 0 | True |
| LTW-004 | same | same | merge_or_refresh | False | - | 0 | True |
| LTW-009 | complementary | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-010 | complementary | uncertain | add_without_supersede | False | - | 0 | False |
| LTW-011 | complementary | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-012 | complementary | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-017 | update | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-018 | update | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-019 | update | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-020 | update | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-021 | update | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-022 | update | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-025 | contradiction | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-026 | contradiction | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-027 | contradiction | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-028 | contradiction | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-033 | unrelated | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-034 | unrelated | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-035 | unrelated | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-036 | unrelated | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-084 | uncertain | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-089 | uncertain | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-090 | uncertain | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-091 | uncertain | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-092 | uncertain | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-093 | uncertain | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-049 | complementary | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-050 | complementary | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-051 | complementary | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-052 | complementary | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-065 | complementary | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-066 | complementary | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-067 | complementary | uncertain | add_without_supersede | True | - | 0 | False |
| LTW-068 | complementary | uncertain | add_without_supersede | True | - | 0 | False |

The JSON artifact contains before_rows, after_rows, inserted_rows, status changes, and transition audits for every selected case.

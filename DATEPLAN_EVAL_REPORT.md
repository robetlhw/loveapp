# DatePlan Evaluation Report

- Scenarios: 10
- Turns: 16
- Scenario pass rate: 0.9
- Patch accuracy: 1.0
- State preservation accuracy: 1.0
- Validation accuracy: 1.0
- Final plan completion rate: 0.9

## Scenarios

| Case | Category | Result | First failure attribution |
|---|---|---|---|
| DP-001 | complete_request | PASS | - |
| DP-002 | incremental_fields | PASS | - |
| DP-003 | budget_update | PASS | - |
| DP-004 | date_update | PASS | - |
| DP-005 | interruption_resume | PASS | - |
| DP-006 | ambiguous_time | PASS | - |
| DP-007 | explicit_cancel | PASS | - |
| DP-008 | expired_date | FAIL | Workflow state transition |
| DP-009 | multi_day | PASS | - |
| DP-010 | constraints | PASS | - |

## Failure Cases

- `DP-008`: task_status (expected `collecting`, actual `planned`, attribution `Workflow state transition`)

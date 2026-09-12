# Memory Benchmark V1 Report

- Dataset: `evals\memory\benchmark_v1.jsonl`
- Cases: `100`
- Contract valid: `True`
- Schema validity: `1.0`
- Production claim smoke pass rate: `1.0`
- Model quality evaluated: `False` (contract/smoke mode)

## Coverage

- Categories: `{"enrichment": 15, "event": 20, "long_tail": 10, "pattern": 10, "preference": 15, "stable_fact": 20, "state": 10}`
- Length classes: `{"long": 20, "medium": 40, "short": 40}`

## Boundary

This run validates the frozen golden contract and exercises normalization and admission on expected claims. It does not call an LLM or claim extraction, resolver, lifecycle, or pattern accuracy.

## Data Structure Notes

The source document's short example is illustrative but underspecified. The frozen contract adds `schema_version`, `scenario`, `difficulty`, `length_class`, typed `conversation`, and typed expected stage/operation and mutation sections.
`operation` is bounded to CREATE, ENRICH, REFINE, UPDATE, MERGE, PROJECT, MIXED, NOOP, or REJECT. Mixed cases use typed `sub_operations`; target selectors and mutation actions are separately typed so a semantic operation cannot silently imply an arbitrary patch.

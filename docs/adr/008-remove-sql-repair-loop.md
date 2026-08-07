# ADR 008: Remove the LLM SQL Repair Loop

**Date:** 2026-08-07
**Status:** ACCEPTED (IMPLEMENTED)
**Scope:** SQL workflow (`agent_host/nodes/sql_nodes.py`, `sql/generation.py`)

## Context

The SQL workflow once generated SQL with a model, so a repair loop existed:
`validate_sql` routed "repairable" failures back to `fix_sql`, which asked the
model to rewrite the candidate under a "repair syntax only" instruction, up to
`max_sql_repairs` times.

Two later decisions made that loop unreachable in effect:

1. `write_sql` became a deterministic compiler (`sql/compiler.py`). Its output
   is sqlglot-generated SQL, which cannot fail the syntactic and structural
   gates the repairable codes describe.
2. The validator gained the `plan_sql_mismatch` gate, which blocks any
   candidate whose canonical form differs from the freshly compiled approved
   plan. A "repaired" candidate therefore has exactly one way to pass
   validation: being canonically identical to the SQL the compiler already
   produced. `fix_sql`'s own loop detection then stops it for repeating an
   earlier candidate.

The loop could consume up to three model calls to arrive, provably, at either
the same SQL or a rejection. A validation failure on deterministic compiler
output is a compiler/validator version skew — a bug to surface, not a state a
model rewrite is permitted to paper over.

## Decision

Delete the repair loop and the model SQL generator it called:

- `fix_sql` node, its routing, and the `validate_sql -> fix_sql` edge
- `sql/generation.py` (`generate_sql`, the `emit_sql` tool, its system prompt)
- `SqlGenerationResult`, `RepairAttempt`, the `is_repairable`/`repair_hint`
  fields on `SqlValidationResult`, and `REPAIRABLE_CODES`
- `repair_count`/`repair_hint`/`repair_history` state keys and
  `budget.max_sql_repairs`
- `CompiledQuery.source` narrows to `"deterministic"`

`validate_sql` failure is now terminal: the user receives the violation and
the trace records `validate_sql.failed_final`.

## Consequences

- The only SQL author in the system is the deterministic compiler; the model's
  authority ends at the typed `QueryPlanAST`. This sharpens the ADR 001
  boundary rather than changing it.
- A future model-generated SQL path (for example, if the compiler's dialect
  coverage is ever outgrown) must arrive with its own ADR and its own
  validation contract; resurrecting the old loop is not a shortcut, because
  `plan_sql_mismatch` still binds any candidate to the approved plan.
- Validation failures surface immediately instead of after silent model
  retries, making compiler/validator skew visible in one trace event.

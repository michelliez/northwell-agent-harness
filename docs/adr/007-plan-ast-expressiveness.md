# ADR 007: Plan AST Expressiveness — Time Buckets, Ordering, Limits; IN Removed

**Date:** 2026-08-07
**Status:** ACCEPTED (IMPLEMENTED)
**Scope:** `sql/models.py`, `sql/planning.py`, `sql/compiler.py`

## Context

The deterministic compiler could express a single aggregate SELECT with raw
column groupings and scalar comparison filters. That excluded the most common
analyst question shapes:

- "admissions **per month**" — calendar bucketing needs an expression in
  GROUP BY, and groupings were raw columns only
- "**top 10** departments by encounter count" — no ORDER BY or LIMIT
- `IN` was accepted by the plan schema and plan validation, then rejected at
  compile time (`array_parameter_in_not_supported`), so a model could be led
  into a plan that died one node after being approved

## Decision

Extend the typed plan AST with three deterministic constructs and remove one
trap:

1. **`time_buckets`** (max 2): `{column, granularity ∈ DAY|WEEK|MONTH|QUARTER|
   YEAR, alias}`. Plan authorization requires the column to be classified
   `safe_aggregate` **and** evidenced as `DATE`/`DATETIME`/`TIMESTAMP`
   (`time_bucket_unsafe_column`, `time_bucket_not_temporal`). The compiler
   selects `DATE_TRUNC`/`DATETIME_TRUNC`/`TIMESTAMP_TRUNC` from the evidenced
   type and emits the identical expression in the projection and GROUP BY.
2. **`order_by`**: one output alias plus a direction — never an arbitrary
   expression (`order_by_unknown_alias` otherwise). **`limit`**: 1–1000.
3. **`IN` removed** from `PlannedFilter`. Array parameters require `UNNEST`,
   which the validator blocks as an unapproved table source; until that
   changes with its own ADR, offering `IN` in the schema is a promise the
   compiler cannot keep. Ranges use `BETWEEN`; discrete alternatives use
   separate queries.

Bucket aliases join aggregation aliases and grouping names in the
`expected_output` contract, and alias uniqueness is enforced
(`duplicate_output_alias`). `COMPILER_VERSION` moves to
`approved-plan-bigquery-v2`.

## Safety analysis

- Every new construct is compiled deterministically from validated
  identifiers and enum granularities; no model-supplied SQL fragment exists.
- The SQLGlot validator accepts the output unchanged (verified: qualify
  resolves alias GROUP BY; `plan_sql_mismatch` still binds candidates to the
  recompiled plan, canonical roundtrip is stable).
- Time buckets only widen what an already-`safe_aggregate` temporal column can
  express; no safety class gains new reach. ORDER BY/LIMIT only reorder and
  truncate an already-approved aggregate result.

## Consequences

- "Per month" and "top N" questions compile without loosening any gate.
- Plans can no longer be approved and then rejected by the compiler for `IN`.
- A future array-parameter `IN` needs a validator story for `UNNEST` first.

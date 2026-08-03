# ADR 004: Result Safety Gate (Block & Redact Sensitive Columns)

**Date:** 2026-07-30  
**Status:** ACCEPTED & IMPLEMENTED  
**Scope:** SQL execution module (Track 2)

## Decision

After query execution, verify and/or redact result rows to ensure **no sensitive or identifier columns** are returned to the user.

Two functions:
- `verify_result_safety(rows, approved)` - Raises error if forbidden columns found
- `redact_result(rows, approved)` - Removes forbidden columns

## Rationale

### Problem
Even after SQL validation blocks sensitive columns in the SELECT clause, results can leak sensitive data if:
1. **Validation gap:** SQL validator missed a column reference
2. **Scope mismatch:** Query compiled with one schema snapshot, but result contains columns from a different state
3. **Unexpected columns:** BigQuery returns metadata columns we didn't anticipate

### Why Post-Execution Check

- **Defense in depth:** Don't trust just SQL validation; verify actual result
- **Column safety source of truth:** Schema snapshot defines what's safe; enforce that at result time
- **Catch mistakes:** If a mistake sneaks through SQL validation, result gate catches it
- **Graceful degradation:** Can redact (remove) or reject (raise error) depending on context

## Implementation

**File:** `src/sql/result_safety.py`

```python
def verify_result_safety(result_rows: list[dict], approved: ApprovedQueryPlan) -> list[dict]:
    """Verify results don't contain sensitive or identifier columns."""
    
    for row in result_rows:
        for column_name in row.keys():
            # Look up column in schema
            column = find_column(column_name, approved.permission_scope.schema_snapshot)
            
            if column is None:
                # Unknown column; check if it's an allowed aggregation/grouping
                if not is_allowed_computed_column(column_name, approved):
                    raise ResultSafetyViolation(f"Unmapped column: {column_name}")
            
            # Verify safety classification
            if column.safety in {"sensitive", "identifier"}:
                raise ResultSafetyViolation(f"Forbidden {column.safety} column: {column_name}")
    
    return result_rows


def redact_result(result_rows: list[dict], approved: ApprovedQueryPlan) -> list[dict]:
    """Remove forbidden columns from results."""
    
    allowed_columns = set()
    for agg in approved.plan.aggregations:
        allowed_columns.add(agg.alias.lower())
    for grouping in approved.plan.groupings:
        allowed_columns.add(grouping.column.lower())
    
    redacted_rows = []
    for row in result_rows:
        redacted_row = {k: v for k, v in row.items() if k.lower() in allowed_columns}
        redacted_rows.append(redacted_row)
    
    return redacted_rows
```

### Key Properties

- **Schema-driven:** Uses `SchemaSnapshot` as source of truth for column safety
- **Case-insensitive:** Handles DEPT_ID, dept_id, Dept_Id equivalently
- **Allows aggregations:** Recognizes aggregation aliases and grouping columns
- **Catches unmapped columns:** Rejects columns not in schema (unless they're computed)
- **Two modes:** Verify (raise error) or redact (remove columns)

## Testing

**Test file:** `tests/sql/test_result_safety.py`  
**Test count:** 19 tests, all passing

Coverage:
- Verification passes for approved columns
- Verification rejects sensitive columns
- Verification rejects identifier columns
- Verification allows empty results
- Case-insensitive matching
- Unmapped column rejection
- Null value handling
- Multiple data types (numeric, string, etc.)
- Redaction removes sensitive columns
- Redaction removes identifier columns
- Redaction keeps only approved columns
- Integration with compiled queries
- Full workflow audit trail
- Multiple runs with separate logs

## Trade-offs

| Aspect | Chosen | Alternative | Why |
|--------|--------|-------------|-----|
| Timing | After execution | Before execution | Post-execution is final safety gate |
| Failure mode | Raise + redact | Raise only | Redact available for graceful degradation |
| Source of truth | SchemaSnapshot | SQL query text | Schema is what was approved |
| Column matching | Case-insensitive | Case-sensitive | BigQuery is case-insensitive |
| Aggregations | Allow | Block | Aggregations are computed, not from schema |

## Assumptions

- `SchemaSnapshot` accurately reflects the schema used in query planning
- `ApprovedQueryPlan` accurately reflects what the user approved
- Results are returned as list of dicts (not DataFrame or custom objects)
- Column names in results are unqualified (not "table.column")

## Constraints

- **Single snapshot:** Assumes schema doesn't change between planning and execution (acceptable; snapshots are immutable)
- **No column renaming:** Assumes result column names match schema or approved plan (acceptable; compiler is deterministic)
- **No derived columns:** Can't verify safety of computed columns like `DEPT_ID || ' - ' || DEPT_NAME` (acceptable; validator should block these)

## Future Considerations

- **Cell-level masking:** Currently blocks entire columns; could mask values instead (e.g., SSN → "***-**-****")
- **Min-cell-count:** Could enforce k-anonymity by hiding groups with <k rows (future work)
- **Differential privacy:** Could add noise to aggregate results (future work)
- **Column provenance:** Could track which evidence chunk approved each column (ADR 008 suggestion)

## Related ADRs

- [ADR 003](003-read-only-executor.md) - Execute queries safely
- [ADR 005](005-audit-logging.md) - Record result safety decisions
- [SQL Validation ADR](001-graph-trunk-and-mcp-boundary.md#sql-validation) - Pre-result validation

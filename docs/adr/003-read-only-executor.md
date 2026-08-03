# ADR 003: BigQuery Read-Only Executor with Strict Safety Constraints

**Date:** 2026-07-30  
**Status:** ACCEPTED & IMPLEMENTED  
**Scope:** SQL execution module (Track 2)

## Decision

Execute approved, validated queries on BigQuery with **strict read-only constraints** enforced at the executor layer:
- `maximum_bytes_billed` quota (hard limit on data scanned)
- `timeout_ms` timeout (hard limit on query duration)
- `use_legacy_sql=False` (BigQuery standard SQL only)
- `allow_large_results=False` (no writes to destination table)
- Audit labels (source, sql_source, run_id, user_id)
- Parameter binding (no string interpolation)

## Rationale

### Problem
After cost-gating approves a query, we need to execute it safely on BigQuery while preventing:
1. **Quota overruns:** Query scans more data than approved (data changed, plan estimate was wrong)
2. **Runaway queries:** Query hangs or runs forever (missing WHERE clause, infinite loop)
3. **Accidental writes:** Query somehow writes data (malicious or buggy SQL)
4. **Lack of audit trail:** No record of who ran what, when, and with what result

### Why Executor-Layer Constraints

- **Defense in depth:** Cost gate approves based on estimates; executor enforces actual limits
- **Non-negotiable:** These constraints cannot be bypassed by the model or caller
- **Per-query:** Each execution has its own timeout, quota, labels
- **Clear failure modes:** Timeout → `TimeoutError`, quota exceeded → `RuntimeError`

## Implementation

**File:** `src/sql/bigquery_adapter.py`

```python
class BigQueryReadOnlyExecutor:
    def execute(
        self,
        compiled: CompiledQuery,
        maximum_bytes_billed: int,
        timeout_ms: int = 30_000,
        run_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict]:
        """Execute with constraints."""
        
        # Build job config with safety constraints
        job_config = bigquery.QueryJobConfig(
            use_legacy_sql=False,              # Standard SQL only
            query_parameters=params,           # Bind @param_name values
            maximum_bytes_billed=max_bytes,    # Hard quota limit
            labels={...},                      # Audit labels
            allow_large_results=False,         # No destination table
        )
        
        # Execute query
        job = client.query(sql, job_config=job_config)
        
        # Enforce timeout at result retrieval
        result = job.result(timeout=timeout_ms / 1000.0)
        
        return [dict(row) for row in result]
```

### Key Properties

- **Parameter binding:** Converts `PlannedParameter` objects to BigQuery `ScalarQueryParameter`
- **Timeout enforcement:** Via `job.result(timeout=...)` (wall-clock timeout)
- **Quota enforcement:** Via `maximum_bytes_billed` in job config (hard limit)
- **Read-only:** `use_legacy_sql=False` + `allow_large_results=False`
- **Audit labels:** Tags each query with source, sql_source, run_id, user_id

## Testing

**Test file:** `tests/sql/test_bigquery_read_only_executor.py`  
**Test count:** 29 tests, all passing

Coverage:
- Basic execution (simple queries, with parameters)
- Constraint enforcement (bytes, timeout)
- Audit label setting
- Safety settings (legacy SQL, destination table)
- Error handling (timeout, quota, BigQuery errors, permission errors)
- Initialization (service account, default credentials)
- Parameter binding (7 BigQuery types: STRING, INT64, FLOAT64, BOOL, DATE, DATETIME, TIMESTAMP)
- Result conversion (list of dicts)

## Trade-offs

| Aspect | Chosen | Alternative | Why |
|--------|--------|-------------|-----|
| Constraint enforcement | At executor layer | At client/SDK layer | Executor is last line of defense |
| Timeout mechanism | `job.result(timeout)` | Query config | Result timeout is actual wall-clock |
| Parameter binding | Via SDK ScalarQueryParameter | String interpolation | SQL injection prevention |
| Result format | List of dicts | DataFrame/Iterator | Simple, serializable |

## Assumptions

- Queries are already validated (`validate_sql()`) before reaching executor
- Parameters are already typed (PlannedParameter with type field)
- Service account credentials are available (env var or default)
- BigQuery client library handles connection/auth correctly

## Constraints

- **Quota limit enforcement:** Works only for currently-running query; if query completes just before quota would be exceeded, we don't refund the bytes (acceptable per cost-gating logic)
- **Timeout enforcement:** Wall-clock timeout; if network is slow, query may timeout even if it would complete quickly (acceptable; user can retry)
- **No result streaming:** Results are materialized into memory (limit to ~100K rows; large results should be written to table instead)
- **No caching:** Each execution hits BigQuery; no result caching between duplicate queries (acceptable; queries are user-specific)

## Future Considerations

- **Result size limit:** Could enforce max result size in memory (currently no limit)
- **Partial results:** Could return partial results if query is interrupted by timeout (currently: no results on timeout)
- **Credential rotation:** Currently uses service account key from file or default credentials; could add periodic rotation
- **Regional execution:** Currently uses global location; could optimize for data locality

## Related ADRs

- [ADR 002](002-bigquery-dry-run-pattern.md) - Cost estimation before execution
- [ADR 004](004-result-safety-gate.md) - Verify results don't leak sensitive data
- [ADR 005](005-audit-logging.md) - Recording execution decisions

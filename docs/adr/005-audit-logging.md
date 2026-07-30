# ADR 005: File-Based JSONL Audit Logging

**Date:** 2026-07-30  
**Status:** ACCEPTED & IMPLEMENTED  
**Scope:** SQL execution module (Track 2)

## Decision

Log all workflow decisions to **JSONL files** (one file per `run_id`), with the following structure:

```python
@dataclass
class AuditEvent:
    timestamp: str                # ISO 8601 timestamp
    run_id: str                   # Unique run identifier
    user_id: str | None           # User who triggered the query
    event_type: str               # policy_check, intent_classified, retrieval, ..., execution
    decision: str                 # allowed, rejected, error
    reason: str | None            # Why (if rejected/error)
    sql: str | None               # Generated SQL
    bytes_processed: int | None    # Bytes scanned
    referenced_tables: list[str]   # Tables used
```

Each event is appended as a newline-delimited JSON to `.local/audit_logs/{run_id}.jsonl`.

## Rationale

### Problem
Need a complete audit trail of all decisions in the SQL workflow for:
1. **Compliance:** Show regulators what data was accessed and by whom
2. **Debugging:** Understand why a query succeeded or failed
3. **Security:** Detect unusual query patterns (repeated rejections, high costs, etc.)
4. **Cost allocation:** Charge teams/users for BigQuery costs
5. **Improvement:** Identify where queries fail most often

### Why JSONL Files

| Choice | Alternative | Why |
|--------|-------------|-----|
| JSONL (line-delimited JSON) | Database (PostgreSQL, MongoDB) | Local files are portable, require no setup, immutable (append-only) |
| One file per run_id | One file for all runs | Faster reads (grep one file), per-user log separation, concurrent writes don't conflict |
| Local filesystem | Cloud storage (GCS) | Fast access, no network latency, suitable for MVP; can migrate to GCS later |
| Append-only | Update-in-place | Immutable log = audit trail is permanent, can't be modified |
| Text format (JSONL) | Binary | Human-readable, easy to grep/filter, standard tool support |

## Implementation

**File:** `src/sql/audit_log.py`

```python
class AuditLog:
    def __init__(self, log_dir: str | Path):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
    
    def record(self, event: AuditEvent) -> None:
        """Append event to run's log file."""
        event.validate()  # Reject invalid events
        log_file = self.log_dir / f"{event.run_id}.jsonl"
        with open(log_file, "a") as f:
            f.write(json.dumps(asdict(event), default=str) + "\n")
    
    def get_events(self, run_id: str) -> list[AuditEvent]:
        """Read all events for a run in order."""
        log_file = self.log_dir / f"{run_id}.jsonl"
        if not log_file.exists():
            return []
        
        events = []
        with open(log_file) as f:
            for line in f:
                data = json.loads(line)
                events.append(AuditEvent(**data))
        return events
    
    def summary_for_run(self, run_id: str) -> dict:
        """Count decisions by event_type."""
        events = self.get_events(run_id)
        summary = {}
        for event in events:
            if event.event_type not in summary:
                summary[event.event_type] = {"allowed": 0, "rejected": 0, "error": 0}
            summary[event.event_type][event.decision] += 1
        return summary
```

### Key Properties

- **Validation:** Events must have timestamp, run_id, event_type, decision; rejected/error decisions need a reason
- **Append-only:** Log files never shrink or get overwritten
- **Per-run files:** Each query run gets its own log (parallelizable, independent)
- **Event types:** 9 types covering all workflow stages (policy_check, intent_classified, retrieval, plan_safety, sql_compiled, dry_run, cost_gate, result_safety, execution)
- **Frozen dataclass:** AuditEvent is immutable once created

## Testing

**Test file:** `tests/test_audit_log.py`  
**Test count:** 25 tests, all passing

Coverage:
- Event validation (required fields, decision-specific requirements)
- Recording (file creation, JSONL format, appending)
- Retrieval (get all events, preserve order, empty logs)
- Summaries (count decisions by type, handle all 9 types)
- Integration (full 8-step workflow trace)
- Multiple runs (separate files)
- Optional fields (handle all combinations)

## Trade-offs

| Aspect | Chosen | Alternative | Why |
|--------|--------|-------------|-----|
| Storage | Files | Database | No infrastructure needed, fast, portable |
| Format | JSONL | CSV/TSV | Supports nested fields (references_tables), extensible |
| Granularity | Per-run files | One global log | Independent files avoid lock contention, per-user queries natural |
| Immutability | Append-only | Mutable records | Tamper-resistant, compliance-friendly |
| Retention | Local disk | Ephemeral | Logs persist across restarts, audit-trail accountability |

## Assumptions

- Filesystem is reliable (writes don't get lost or corrupted)
- Disk has enough space for audit logs (estimate: ~1KB per event, ~5-10 events per query, ~100 queries/day = 500KB-1MB/day)
- File locking is sufficient (no concurrent writes to same run_id from multiple processes)
- Logs are read rarely (not a query bottleneck)

## Constraints

- **Immutable after write:** Events can't be modified or deleted (by design)
- **No transactions:** If process crashes mid-write, line may be corrupted (acceptable; JSON parser will skip malformed lines)
- **Single host:** Logs are local to the machine they're written on (acceptable for MVP; can centralize to GCS later)
- **No indexing:** Finding events requires scanning files (acceptable for <1000 events per run)

## Future Considerations

- **Centralized logging:** Migrate logs to Cloud Logging or ELK stack for long-term retention
- **Real-time monitoring:** Stream events to monitoring system for alerts (e.g., repeated rejections)
- **Compliance redaction:** Remove sensitive data from audit logs before archival (PII, SQL query contents)
- **Retention policies:** Automatically delete logs after N days/years per compliance
- **Log signing:** HMAC-sign each line to detect tampering during archival
- **Event streaming:** Publish events to Pub/Sub for downstream consumption (cost allocation, dashboards)

## Security Considerations

- **Log file permissions:** Should be readable only by authorized users (Northwell IT responsible)
- **SQL redaction:** Currently logs full SQL (may contain parameter values); consider redacting parameters
- **Bytes processed:** Public information (auditable); not sensitive
- **User ID:** Maps to identity; keep logs access-controlled
- **Content filtering:** No PHI, no query results, only metadata (run_id, bytes, tables) stored

## Related ADRs

- [ADR 002](002-bigquery-dry-run-pattern.md) - Dry-run decisions recorded
- [ADR 003](003-read-only-executor.md) - Execution decisions recorded
- [ADR 004](004-result-safety-gate.md) - Result safety decisions recorded

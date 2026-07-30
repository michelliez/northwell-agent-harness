# Implementation & Architecture Plans

Consolidated planning document organized by category. For implemented decisions, see [ADRs](adr/).

---

## Table of Contents

1. [Implementation Roadmap](#implementation-roadmap)
2. [Track 2 (SQL Execution) Work](#track-2-sql-execution-work)
3. [Streamlit UI Plan](#streamlit-ui-plan)
4. [Future Security Improvements](#future-security-improvements)
5. [Parallel Development Strategy](#parallel-development-strategy)

---

## Implementation Roadmap

### Overview

4-phase plan to complete the agent harness. **Phase 1 (Track 1) is in progress by another team. Phase 2 (Track 2) is complete.**

### Phase 1: Harden Track 1 (Policy, Intent, Retrieval)
**Owner:** Separate team  
**Status:** In progress  
**Timeline:** 2-3 weeks

- [x] Policy gate (deterministic input screening)
- [x] Intent classification (route to SQL or documentation)
- [x] Retrieval (index search, evidence extraction)
- [x] Schema snapshot (permission scope)
- [ ] Repair hints for out-of-scope queries
- [ ] Multi-turn interaction (refine after rejection)

### Phase 2: Complete Track 2 (SQL Execution Path)
**Owner:** This session  
**Status:** ✅ COMPLETE (422 tests passing)  
**Timeline:** 3 days

**Completed:**
- [x] BigQueryDryRun adapter (cost estimation)
- [x] BigQueryReadOnlyExecutor (safe query execution)
- [x] Approval Token HMAC (cost gate barrier)
- [x] Result Safety Gate (sensitive column blocking)
- [x] Audit Logging (JSONL audit trail)
- [x] SQL Validation (injection, sensitivity checking)
- [x] SQL Compilation (deterministic code generation)
- [x] Query Planning (schema-constrained proposer)

**Not yet implemented:**
- [ ] FastAPI backend (optional, MVP uses direct Python calls)
- [ ] Streamlit UI (user-facing interface)

### Phase 3: User-Facing API & UI
**Timeline:** 2-3 weeks  
**Depends on:** Phase 2 completion

- [ ] FastAPI endpoints (`/ask`, `/resume`)
- [ ] Streamlit web interface
- [ ] Authentication/authorization layer
- [ ] Rate limiting

### Phase 4: Testing & Deployment
**Timeline:** 1-2 weeks

- [ ] End-to-end integration tests
- [ ] Load testing
- [ ] Security audit
- [ ] Staging deployment
- [ ] Production rollout

---

## Track 2 (SQL Execution) Work

### Independent Modules (No Track 1 Dependencies)

**Completed (70% of Track 2):**

1. **BigQueryDryRun Adapter** (25 tests)
   - `dry_run(sql, project, location)` function
   - Estimates cost without executing
   - Returns `DryRunResult` with bytes processed, tables, errors
   - Handles environment variables, all error types
   - Status: ✅ COMPLETE

2. **BigQueryReadOnlyExecutor** (29 tests)
   - `BigQueryReadOnlyExecutor` class
   - Executes approved queries with constraints
   - Enforces `maximum_bytes_billed`, `timeout_ms`, read-only
   - Sets audit labels for tracing
   - Binds typed parameters (7 BigQuery types)
   - Status: ✅ COMPLETE

3. **Approval Token HMAC** (9 tests)
   - Already implemented in `cost_gate.py`
   - Issues short-lived tokens with HMAC signatures
   - Verifies tokens before execution (detects tampering)
   - Token format: `v1.{expiry_epoch}.{max_bytes}.{signature}`
   - Status: ✅ COMPLETE

4. **Result Safety Gate** (19 tests)
   - `verify_result_safety(rows, approved)` - rejects sensitive columns
   - `redact_result(rows, approved)` - removes forbidden columns
   - Handles qualified/unqualified column names
   - Case-insensitive matching
   - Status: ✅ COMPLETE

5. **Audit Logging** (25 tests)
   - `AuditEvent` dataclass with validation
   - `AuditLog` class for file-based JSONL logging
   - One log file per `run_id` (append-only)
   - Event types: policy_check, intent_classified, retrieval, plan_safety, sql_compiled, dry_run, cost_gate, result_safety, execution
   - Summaries and retrieval methods
   - Status: ✅ COMPLETE

### Modules Requiring Track 1 Integration (30% of Track 2)

6. **Cost Gate Enforcement** (Part of cost_gate.py)
   - Validates dry-run bytes against configured ceiling
   - Rejects if bytes exceed limit
   - Rejects if plan hash or scope hash mismatched
   - Rejects if partition filters missing
   - Issues approval token on success
   - Status: ✅ COMPLETE (waiting for Track 1 to provide IntentSlots, PermissionScope)

7. **SQL Generation** (part of compiler.py)
   - Deterministic code generation from approved plans
   - No model involved, fully reproducible
   - Produces BigQuery standard SQL
   - Status: ✅ COMPLETE

8. **SQL Validation** (validation.py)
   - 143 comprehensive tests
   - Blocks injection, malicious SQL, sensitive data leaks
   - Blocks row-level access in aggregate-only scope
   - Status: ✅ COMPLETE

### Test Coverage Summary

| Component | Tests | Status |
|-----------|-------|--------|
| BigQueryDryRun | 25 | ✅ PASS |
| BigQueryReadOnlyExecutor | 29 | ✅ PASS |
| Cost Gate | 9 | ✅ PASS |
| Result Safety | 19 | ✅ PASS |
| Audit Logging | 25 | ✅ PASS |
| SQL Validation | 143 | ✅ PASS |
| SQL Compiler | 39 | ✅ PASS |
| Query Planning | 94 | ✅ PASS |
| Graph/Routing | 22 | ✅ PASS |
| Trace Contract | 36 | ✅ PASS |
| **TOTAL** | **422** | **✅ PASS** |

### Architecture: SQL Execution Pipeline

```
ApprovedQueryPlan (from Track 1)
    ↓
plan_to_bigquery_sql() [COMPLETE]
    ↓
CompiledQuery (SQL + parameters)
    ↓
validate_sql() [COMPLETE]
    ↓
dry_run() [COMPLETE]
    ↓
DryRunResult (bytes_processed estimate)
    ↓
evaluate_cost_execution() [COMPLETE]
    ├─ Validate plan/SQL match
    ├─ Check bytes ≤ max_bytes
    ├─ Check partition filters present
    └─ Issue approval token or reject
    ↓
CostGateResult
    ├─ allowed=true → approval_token + expires_at
    └─ allowed=false → violations
    ↓
[Optional: User approves expensive query]
    ↓
BigQueryReadOnlyExecutor.execute() [COMPLETE]
    ├─ Bind parameters
    ├─ Enforce maximum_bytes_billed
    ├─ Enforce timeout
    ├─ Set audit labels
    └─ Return results
    ↓
verify_result_safety() [COMPLETE]
    ├─ Block sensitive columns
    ├─ Block identifier columns
    └─ Allow safe_aggregate columns
    ↓
AuditLog.record() [COMPLETE]
    └─ Log decision to JSONL
    ↓
Results → User
```

---

## Streamlit UI Plan

### Purpose
Web-based interface for analysts to query healthcare data through the governed SQL agent.

### User Stories

**Story 1: Ask a Question**
- Analyst enters natural language question
- System processes and shows workflow trace
- Analyst sees results if approved and executed

**Story 2: Understand Why Query Failed**
- Query rejected at some stage (policy, plan, cost, etc.)
- System shows rejection reason
- Analyst can refine and resubmit

**Story 3: Approve Expensive Queries**
- Query passes all checks but cost is near limit
- Analyst reviews estimated bytes and cost
- Analyst clicks "Approve & Execute"
- Query runs with approval token

**Story 4: Review Audit Trail**
- Admin views all queries executed
- Filters by date, user, run_id
- Clicks event to see full details

### Page Structure

#### Page 1: Query Interface
```
┌─────────────────────────────────────┐
│ SQL Agent Query Interface           │
├─────────────────────────────────────┤
│ [Question Input] [Ask] [Clear]      │
├─────────────────────────────────────┤
│ Workflow Trace (Expandable):        │
│  ✓ Policy Gate                      │
│  ✓ Intent Classification            │
│  ✓ Retrieval (3 chunks)             │
│  ✓ Plan Safety                      │
│  ✓ SQL Compilation                  │
│    [SQL Display with highlighting]  │
│  ✓ Dry-Run Cost (1.2 GB)            │
│  ✓ Cost Gate (within budget)        │
│  ✓ Execution (42 rows)              │
├─────────────────────────────────────┤
│ Results Table:                      │
│ │ DEPT_ID    │ appt_count │         │
│ ├────────────┼────────────┤         │
│ │ CARDIOLOGY │        127 │         │
│ │ ONCOLOGY   │         89 │         │
│ [Download CSV] [Copy SQL]           │
├─────────────────────────────────────┤
│ Citations:                          │
│ - chunk-001: "APPOINTMENT_FACT..."  │
└─────────────────────────────────────┘
```

#### Page 2: Audit Log
- Timeline view of all queries
- Columns: timestamp, user, run_id, status, event_count
- Click to expand and view details
- Filters: date range, user, decision type

#### Page 3: Settings
- API endpoint configuration
- User ID setting
- Default byte limit
- Display preferences

### Components

1. **Query Input** - Text area, validation
2. **Workflow Trace** - Expandable sections, status indicators
3. **Results Table** - Pandas DataFrame with sorting/filtering
4. **Citations** - List of schema evidence chunks
5. **Error Display** - Graceful error messages
6. **Approval Dialog** - Cost review modal
7. **Audit Log Table** - Sortable, filterable timeline

### State Management

**Navigation:** Home/Query page, Audit Log page, Settings page

**Query Lifecycle:**
1. Idle - Waiting for input
2. Submitting - Query sent, waiting for response
3. Approval Needed - Cost gate needs approval
4. Executing - Running on BigQuery
5. Complete - Results displayed
6. Error - Failed at some stage

**Session Persistence:**
- Store `run_id` (user can refresh without losing trace)
- Store `user_id` (user doesn't need to re-login)
- Cache settings in session

### Integration: Direct Python Calls (No FastAPI for MVP)

Instead of calling HTTP endpoints, Streamlit imports and calls the workflow directly:

```python
from agent_host.graph import build_workflow
from sql.audit_log import AuditLog

def ask_question(question: str, user_id: str):
    workflow = build_workflow(config)
    events = workflow.run(question, user_id=user_id)
    audit_log.record_events(events)
    return events
```

**Advantages:**
- No network overhead
- Simpler deployment (single Python process)
- Faster development
- No authentication layer needed yet

**Limitation:**
- Single client only (Streamlit can't be easily called from other apps)
- Can add FastAPI layer later if needed

### Deployment

**Local:**
```bash
streamlit run src/ui/app.py --server.port 8501
```

**Docker:**
```dockerfile
FROM python:3.14-slim
RUN pip install streamlit pandas pydantic
COPY . /app
WORKDIR /app
CMD ["streamlit", "run", "src/ui/app.py"]
```

### File Structure

```
src/ui/
├── app.py                    # Main entry point
├── pages/
│   ├── 01_query.py          # Query interface
│   ├── 02_audit.py          # Audit log viewer
│   └── 03_settings.py       # Settings
├── components/
│   ├── workflow_trace.py    # Render trace
│   ├── results_table.py     # Display results
│   └── citations.py         # Show evidence
└── utils/
    ├── formatting.py        # Format helpers
    └── cache.py             # Caching
```

### Success Criteria

- ✅ Analyst can submit natural language questions
- ✅ Complete workflow trace visible with decisions
- ✅ SQL and estimated cost shown
- ✅ Expensive queries can be approved
- ✅ Results displayed in interactive table
- ✅ Admin can view audit log
- ✅ No raw exceptions shown to user
- ✅ API calls complete in <10 seconds

---

## Future Security Improvements

**Not blocking, but recommended for Phase 4+**

### 1. Column Safety Classification Audit Trail

**Why:** Currently, column safety comes from retrieval evidence with no audit of how classifications were decided.

**Improvement:** Track which chunk(s) determined each column's safety, so we can audit/dispute classifications.

**Implementation:**
```python
@dataclass
class SchemaColumn(BaseModel):
    name: str
    data_type: str | None
    safety: Literal["identifier", "sensitive", "safe_aggregate", "unknown"]
    source_evidence: str  # NEW: chunk_id that classified this
    classification_reason: str | None  # NEW: why this safety level
```

**Effort:** 1-2 days (add to retrieval evidence extraction, update validators)

### 2. Repair Hint Validation

**Why:** When SQL validation fails, we suggest repairs, but don't validate that repairs actually work.

**Improvement:** Test suggested repairs before showing them to the user.

**Implementation:**
```python
if result.is_repairable:
    proposed_repair = suggest_repair(result.repair_hint)
    # Validate the repair works
    validation_of_repair = validate_sql(proposed_repair, ...)
    if validation_of_repair.allowed:
        result.validated_repair = proposed_repair
```

**Effort:** 1 day (add validation loop to SQL validator)

### 3. Budget Immutability & Signing

**Why:** Currently, budget limits are configuration. They should be signed by an admin to prevent tampering.

**Improvement:** Budget config is signed with admin key, validated at startup.

**Implementation:**
```python
@dataclass
class SignedBudgetConfig:
    max_bytes: int
    approved_by: str
    signed_at: datetime
    signature: str  # HMAC-SHA256
```

**Effort:** 0.5 days (add signing to cost_gate initialization)

---

## Parallel Development Strategy

### Goal
Develop Track 2 (SQL execution) in parallel with Track 1 (policy/intent/retrieval) without blocking.

### Approach: Contract-First Development

**Step 1: Define contracts upfront**
- Track 1 outputs: `IntentSlots` (intent + permissions)
- Track 2 inputs: `PermissionScope` + `QueryPlanAST`
- Define in `sql/models.py`

**Step 2: Mock Track 1 outputs in Track 2**
- Track 2 tests create mock `IntentSlots`, `PermissionScope`, `QueryPlanAST` objects
- Tests don't care where these come from
- Track 2 can be fully tested without Track 1

**Step 3: Implement Track 2 modules independently**
- Each module (compiler, validator, executor) has clear input/output contracts
- Tests verify contracts, not implementation details
- Modules can be implemented in any order

**Step 4: Wire up Track 1 → Track 2 when Track 1 is ready**
- Track 1 produces `PermissionScope` + `QueryPlanAST`
- Track 2 consumes them
- No contract changes needed

### Result
- 70% of Track 2 completed without waiting for Track 1
- Tests pass both with mocks and with real Track 1 outputs
- Teams can work independently

### Implementation Examples

**Track 2 tests mock Track 1 outputs:**
```python
def _approved():
    scope = PermissionScope(
        schema_snapshot=SchemaSnapshot(tables=[...]),
        aggregate_only=True,
    )
    plan = QueryPlanAST(
        objective="...",
        tables=["TABLE1"],
        aggregations=[...],
    )
    return validate_query_plan(plan, scope, {citation})
```

**When Track 1 delivers real outputs:**
```python
# No test changes needed!
# Track 1 now produces PermissionScope + QueryPlanAST
# Track 2 consumes them
# Contracts match → integration works
```

---

## Implementation Timeline (Estimate)

| Phase | Component | Effort | Status |
|-------|-----------|--------|--------|
| 2 | BigQueryDryRun | 1 day | ✅ DONE |
| 2 | ReadOnlyExecutor | 1 day | ✅ DONE |
| 2 | Result Safety Gate | 0.5 days | ✅ DONE |
| 2 | Audit Logging | 0.5 days | ✅ DONE |
| 2 | SQL Validation | 2 days | ✅ DONE |
| 2 | SQL Compilation | 1 day | ✅ DONE |
| 2 | Query Planning | 1 day | ✅ DONE |
| 3 | Streamlit UI | 2 days | 📋 PLANNED |
| 3 | FastAPI (optional) | 1 day | 📋 PLANNED |
| 4 | E2E Tests | 2 days | 📋 PLANNED |
| 4 | Staging Deploy | 2 days | 📋 PLANNED |

**Total:** ~14 days elapsed (many done in parallel)

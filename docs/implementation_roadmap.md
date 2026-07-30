# Implementation Roadmap: Full Architecture

**Status:** Based on your detailed specification and current codebase audit  
**Last Updated:** 2026-07-30

---

## Overview

You have specified a **two-track architecture**:

- **Track 1 (Frontend):** User request → policy → intent → retrieval → plan → bounded context
- **Track 2 (Backend):** SQL generation → validation → dry-run → cost gate → execution → result safety → interpretation

Current state: **Track 2 SQL core is 70% done** (deterministic SQL validation, plan safety, compilation are solid); **Track 1 policy/intent/retrieval is 60% done** (framework in place, some gates stub or missing enforcement); **User-facing API/UX is 0%**.

**Critical blockers for MVP:**
1. RetrievalPermissionGate must enforce role-based column masking (currently always allows)
2. IntentClassifier must extract slots (metric, entity, time window, grouping, filters, grain)
3. FastAPI endpoint to accept `{session_id, user_id, prompt}`
4. Approval-token barrier before BigQuery execution
5. Result safety gate must block identifiers and enforce min_cell_count
6. Streamlit demo to show trace, SQL, bytes, citations, refusals

---

## Phase 1: Audit Track 1 (2–3 days)

### Goal
Verify that policy → intent → retrieval → plan forms a coherent, enforceable chain with no bypasses.

### Deliverables

#### 1.1 InputPolicyGate Audit
**Current:** `policy/screen.py` + `policy_nodes.py`

- [ ] Verify all SQL verbs (DDL, DML, DCL) are rejected
- [ ] Verify all direct-identifier patterns (name, DOB, MRN, contact) are rejected
- [ ] Verify patient-identifying intent ("which patient", "show me rows") is rejected
- [ ] Verify admin/operational requests are rejected
- [ ] **Add test fixtures** for all prohibited patterns from brainstorm doc

**Audit checklist:**
```
✓ Destructive verbs: DROP, DELETE, UPDATE, INSERT, ALTER, MERGE, TRUNCATE, TRUNCATE TABLE
✓ PHI/PII literals: SSN, DOB patterns, name patterns, MRN, CSN
✓ Patient-identifying intent: "which patient", "show me rows for", "individual", "specific record"
✓ Permission-changing: "grant", "revoke", "authorize"
✓ Admin DB: "schema", "table structure", "column list" (should redirect to catalog)
```

**Output:** `tests/policy_gate/test_complete_screening.py` with 20+ malicious test cases

#### 1.2 IntentClassifier Audit
**Current:** `nodes/intent_nodes.py`

- [ ] Verify output includes `QuestionIntent.safety_labels` (currently returns `intent: str`)
- [ ] **Extract slots:** metric, entity, time_window, grouping, filters, requested_grain
- [ ] Verify intent categories: `schema_discovery`, `join_help`, `aggregate_query`, `cohort_definition`, `clarification_needed`, `disallowed`
- [ ] Verify schema_discovery and join_help skip retrieval and return catalog only
- [ ] Add slot extraction to Pydantic model

**Model signature (to implement):**
```python
class QuestionIntent(BaseModel):
    intent: Literal["schema_discovery", "join_help", "aggregate_query", 
                    "cohort_definition", "clarification_needed", "disallowed"]
    safety_labels: list[str]  # e.g., ["requires_aggregate", "has_time_window", "has_filters"]
    
    # Slots extracted from prompt
    metric: str | None  # e.g., "appointment count"
    entity: str | None  # e.g., "encounters", "patients"
    time_window: str | None  # e.g., "last 30 days"
    grouping: list[str]  # e.g., ["department", "provider"]
    filters: list[str]  # e.g., ["visit type = office", "age > 18"]
    requested_grain: str | None  # e.g., "daily", "by_department"
```

**Output:** Updated `nodes/intent_nodes.py` + test cases for slot extraction

#### 1.3 RetrievalPermissionGate Implementation
**Current:** `nodes/retrieval_nodes.py:18–30` (stub; always permits)

- [ ] Compute PermissionScope from user_id + role lookup
- [ ] Load dataset/table/column allowlists from config
- [ ] Apply sensitivity tags *before* retrieval (so forbidden columns never enter LLM)
- [ ] Log approved tables and masked columns

**Pseudocode:**
```python
def retrieval_permission_node(state: AgentState) -> dict:
    user_id = state.get("user_id")
    role = lookup_user_role(user_id)  # e.g., "analytics", "research", "clinical"
    
    # Load allowlists
    allowed_tables = ROLE_TABLE_ALLOWLIST.get(role, [])
    allowed_columns = ROLE_COLUMN_ALLOWLIST.get(role, {})  # table -> columns
    masked_columns = ROLE_COLUMN_MASKS.get(role, {})  # table -> {column -> mask_type}
    
    # Compute PermissionScope
    scope = PermissionScope(
        allowed_tables=allowed_tables,
        allowed_columns=allowed_columns,
        masked_columns=masked_columns,
        user_id=user_id,
        role=role,
    )
    
    return {"permissions": scope.model_dump()}
```

**Dependencies:**
- User/role lookup service (stub or mock for MVP)
- Config file with role-based allowlists
- Update RetrievalPermissionGate model

**Output:** `nodes/retrieval_nodes.py` updated + role config file + tests

#### 1.4 RetrievalLayer Audit
**Current:** `retrieval/search.py` (lexical FTS only; no vector/graph)

- [ ] Verify lexical search works correctly
- [ ] **Add vector search** (semantic) — stub for MVP; can be Postgres vector or embedding mock
- [ ] **Add graph search** (join paths) — lookup join_edge metadata
- [ ] **Implement RRF fusion:** `score = lexical_rrf + vector_rrf + exact_id_boost + join_path_boost - sensitivity_penalty`
- [ ] **Enforce column masking** from PermissionScope (e.g., replace sensitive column values with `[REDACTED]`)
- [ ] Bound output: top 5 tables, top 30 columns, top 5 join paths

**Output:** Updated `retrieval/search.py` + retrieval tests + brainstorm-compliance test fixtures

#### 1.5 RetrievedContextGate Audit
**Current:** `nodes/retrieval_nodes.py:69–112` (context_gate_node)

- [ ] Verify at least one table above threshold (currently only checks for no chunks)
- [ ] Verify requested metric column exists in retrieved tables
- [ ] Verify time column exists if intent has time_window
- [ ] Verify group-by columns exist if intent has groupings
- [ ] Verify join path exists if >1 table
- [ ] Verify all candidates are allowed by PermissionScope
- [ ] Explicit conflict/gap detection (e.g., "time window requested but no date column")
- [ ] If weak context, allow one retrieval expansion; then clarify/refuse

**Output:** Enhanced `context_gate_node()` with all checks + test cases

### Phase 1 Success Criteria

```
✅ Policy gate rejects all prohibited patterns (20+ test cases)
✅ Intent classifier extracts all slots correctly
✅ RetrievalPermissionGate blocks forbidden tables/columns
✅ RetrievalLayer ranks by lexical + semantic + join paths (mock OK)
✅ RetrievedContextGate validates all required context before proceeding
✅ Malicious prompts are rejected before retrieval
```

---

## Phase 2: Complete Track 2 Execution Path (3–4 days)

### Goal
Wire dry-run → cost gate → execution → result safety → interpretation with approval-token barrier.

### Deliverables

#### 2.1 BigQueryDryRun Adapter
**Current:** `sql/bigquery_adapter.py:16–17` (stub raising exception)

- [ ] Implement dry-run: run query with `dry_run=True`
- [ ] Capture: `total_bytes_processed`, `valid`, `referenced_tables`, `error`
- [ ] Return `DryRunResult` model (already defined in `sql/models.py`)
- [ ] Log bytes and tables in trace
- [ ] **Important:** Dry-run estimates bytes; actual runtime enforced later with timeout, not here

**Pseudocode:**
```python
def dry_run(sql: str, project: str, location: str) -> DryRunResult:
    """Run query with dry_run=True to estimate cost without execution."""
    job = client.query(sql, project=project, location=location, dry_run=True)
    return DryRunResult(
        valid=True,
        total_bytes_processed=job.total_bytes_processed,
        project=project,
        location=location,
        referenced_tables=_extract_tables(job),
    )
```

**Wire into graph:**
- Add node: `bigquery_dry_run_node(state)` → calls `dry_run()` → stores `dry_run_result` in state
- Add edge: `validate_sql` → `bigquery_dry_run` (before cost gate)

**Output:** `sql/bigquery_adapter.py` updated + `sql_nodes.py` new node

#### 2.2 CostExecutionGate Implementation
**Current:** `sql_nodes.py:356–409` (defined but not connected)

- [ ] Gate is already implemented; just verify logic:
  - Check `dry_run_bytes <= max_bytes`
  - Check partition filters required for large fact tables
  - Check aggregate-only for restricted roles
  - Reject `LIMIT` as cost-control substitute (bytes-based, not row-based)
- [ ] Implement approval token:
  ```python
  approval_token = HMAC(
      payload = f"{sql_hash}:{plan_hash}:{scope_hash}:{max_bytes}:{expiry}",
      key = HMAC_SECRET
  )
  ```
- [ ] Token is opaque: only execution gate can validate it
- [ ] Set expiry: 5–10 minutes

**Wire into graph:**
- Add edge: `bigquery_dry_run` → `cost_execution_gate` → `read_only_execution` or `result_safety`
- Update routing: `_route_from_cost_gate()` returns `execute` or `result_safety`

**Output:** Updated `sql_nodes.py` + cost gate tests

#### 2.3 ReadOnlyQueryExecution Wrapper
**Current:** `sql/execution.py:20–39` (token-gated boundary, no actual executor)

- [ ] Implement `BigQueryReadOnlyExecutor`:
  ```python
  class BigQueryReadOnlyExecutor:
      def execute(self, compiled: CompiledQuery, maximum_bytes_billed: int) -> QueryResult:
          # Revalidate approval token
          max_bytes = require_valid_approval_token(
              approval_token, compiled, approved_plan, HMAC_SECRET
          )
          
          # Execute with tight constraints
          job = client.query(
              compiled.sql,
              job_config=QueryJobConfig(
                  use_legacy_sql=False,
                  query_parameters=compiled.parameters,
                  maximum_bytes_billed=maximum_bytes_billed,
                  labels={"run_id": run_id, "user_id": user_id},
                  timeout_ms=30_000,  # 30 second timeout
                  allow_large_results=False,  # no destination table
                  dry_run=False,
              ),
          )
          job.result()  # block until complete
          return job.to_arrow()  # return raw result only
  ```
- [ ] Never expose secrets to LLM (no credentials, no connection strings)
- [ ] Use read-only service account
- [ ] Enforce `maximum_bytes_billed` from cost gate
- [ ] Set `job_timeout_ms` (not query timeout; is duration limit)
- [ ] Use query parameters, not string interpolation
- [ ] Set labels for audit logging
- [ ] Return raw result only; no interpretation yet

**Wire into graph:**
- Add node: `read_only_execution_node(state)` → calls `execute_approved_query()`
- Add edge: `cost_execution_gate` → `read_only_execution` (on approval)

**Output:** `sql/bigquery_adapter.py` updated with executor + `sql_nodes.py` new node

#### 2.4 ResultSafetyGate Implementation
**Current:** `nodes/output_safety_nodes.py` (exists; verify completeness)

- [ ] Block/redact if columns include identifiers:
  - Patient, encounter, account, MRN, name, DOB, address, phone, email
- [ ] Block/redact if columns include denied sensitivity tags (from PermissionScope)
- [ ] Enforce row cap: `len(result) <= max_rows` (default 1000)
- [ ] Enforce min_cell_count: aggregate cells `< min_cell_count` are suppressed (e.g., if count < 5, show "small count")
- [ ] Return safe_result only to next node, never raw result

**Output:** Verify + enhance `nodes/output_safety_nodes.py`

#### 2.5 InterpretationCitations
**Current:** `nodes/lifecycle_nodes.py:interpretation_and_citations_node()` (exists; verify)

- [ ] Claude may summarize only the safe result payload
- [ ] Always show:
  - SQL (what was executed)
  - Result shape (rows, columns, types)
  - Assumptions (grouping, filters, time range, grain)
  - Caveats (redactions, cell suppression, min_cell_count)
  - Citations (table/column source chunks)
  - Limitations (e.g., "results are aggregated; individual-level data is not available")
- [ ] Prevent misleading interpretation (e.g., "this is all patients" when it's "patients seen in cardiology")

**Output:** Verify + enhance `nodes/lifecycle_nodes.py`

#### 2.6 BoundedFollowUp
**Current:** `nodes/lifecycle_nodes.py:bounded_followup_node()` (exists; verify limits)

- [ ] Classify follow-up: refinement (reuse context), new request (start over), or unsafe
- [ ] Reuse prior context only when scope does not broaden
- [ ] No new permissions, no new tools, no agent spawning
- [ ] Enforce caps:
  - Retrieval expansions ≤ 2
  - SQL repairs ≤ 2
  - Cost replans ≤ 1
  - Full graph recursion limit ≤ 25
- [ ] Re-enter at the latest correct node, not from the top

**Output:** Verify + enhance `nodes/lifecycle_nodes.py`

### Phase 2 Success Criteria

```
✅ Dry-run captures bytes and referenced tables
✅ Cost gate checks bytes <= limit, enforces partitioning
✅ Approval token is generated and validated before execution
✅ ReadOnlyExecutor runs with tight constraints (timeout, max_bytes, labels)
✅ Result safety blocks identifiers and enforces min_cell_count
✅ Interpretation shows SQL, result shape, assumptions, caveats, citations
✅ Follow-up is bounded with explicit retry limits
✅ No unapproved execution path exists
```

---

## Phase 3: User-Facing API & UX (2–3 days)

### Goal
Build FastAPI endpoint and Streamlit demo to make the system usable.

### Deliverables

#### 3.1 FastAPI Endpoint
**New file:** `app/api.py` (or equivalent)

- [ ] Endpoint: `POST /ask` accepting:
  ```python
  {
      "session_id": str,
      "user_id": str,
      "prompt": str,
  }
  ```
- [ ] Store raw prompt only if policy allows; otherwise store `prompt_hash` + redacted prompt
- [ ] Create `AgentState(run_id, prompt, user_context, budget)`
- [ ] Call graph and return:
  ```python
  {
      "answer": str,
      "sql": str | None,
      "result": dict | None,
      "bytes_processed": int | None,
      "citations": list[str],
      "clarification_prompt": str | None,
      "refusal_reason": str | None,
      "trace_file": str,
  }
  ```
- [ ] Endpoint: `POST /resume` for clarification interrupts
- [ ] Log all interactions to audit trail

**Output:** `app/api.py` with endpoints

#### 3.2 Streamlit Demo
**New file:** `app/streamlit_app.py` (or equivalent)

- [ ] UI: session, user_id, prompt input
- [ ] Show trace: all nodes executed, timing, decisions
- [ ] Show SQL: if executed, show the exact SQL
- [ ] Show bytes: dry-run and actual bytes processed
- [ ] Show result: safe result table (with redactions)
- [ ] Show citations: table/column source chunks
- [ ] Show refusal: if denied, show reason
- [ ] Show errors: validation failures, cost rejections, etc.
- [ ] Allow follow-up: re-enter from latest node

**Output:** `app/streamlit_app.py` with all views

#### 3.3 Prompt Storage & Redaction
**New:** Logging & audit trail

- [ ] Store raw prompt only if policy allows
- [ ] If policy blocks, store:
  - `prompt_hash`: SHA256 of raw prompt (for dedup)
  - `redacted_prompt`: with PHI/PII patterns replaced (for audit)
- [ ] Log all decision points: policy block, intent, retrieval allowed, cost, execution
- [ ] Trace includes run_id, user_id, session_id, timestamp, all decisions, SQL, result (if safe)

**Output:** Audit logging integration + trace storage

### Phase 3 Success Criteria

```
✅ FastAPI endpoint accepts {session_id, user_id, prompt}
✅ Prompt is stored safely (raw or redacted)
✅ Streamlit demo shows trace, SQL, bytes, result, citations, refusals
✅ Clarification flow works end-to-end
✅ Audit trail logs all interactions
```

---

## Phase 4: Testing & Hardening (2–3 days)

### Goal
Verify no bypasses, test all prohibited patterns, ensure result safety.

### Deliverables

#### 4.1 Malicious Input Test Suite
**New file:** `tests/e2e/test_full_pipeline_security.py`

Test cases (50+):
- [ ] SQL injection: `'; DROP TABLE--`, `UNION SELECT`, parameter injection
- [ ] Patient identification: "show me rows for patient 123", "which patient has", "individual records"
- [ ] PHI/PII literals: SSN, DOB, name, MRN, contact info in prompt
- [ ] Destructive SQL: DROP, DELETE, UPDATE, INSERT, ALTER, TRUNCATE, MERGE
- [ ] Permission bypass: "grant me access to", "authorize me to"
- [ ] Admin/operational: "change schema", "show table structure", "list all columns"
- [ ] Cost attacks: "count all rows in every table", "scan entire dataset", "JOIN all tables"
- [ ] Follow-up attacks: escalation via refinement, new permissions via follow-up

**Output:** `tests/e2e/test_full_pipeline_security.py` with 50+ cases

#### 4.2 Happy-Path E2E Tests
**New file:** `tests/e2e/test_full_pipeline_happy.py`

Test cases (20+):
- [ ] Simple aggregate: "count appointments by department"
- [ ] With filters: "count appointments by department in last 30 days"
- [ ] With joins: "count unique patients per department"
- [ ] Clarification flow: missing time window → ask → resume
- [ ] Follow-up refinement: aggregate → same time window, different grouping
- [ ] Result redaction: sensitive columns suppressed
- [ ] Cost rejection: high-cost query → rejected

**Output:** `tests/e2e/test_full_pipeline_happy.py` with happy-path cases

#### 4.3 Result Safety Tests
**New file:** `tests/test_result_safety_gate.py`

Test cases:
- [ ] Identifier columns (patient_id, MRN, name) are blocked
- [ ] Sensitive columns (DOB, SSN, address) are redacted
- [ ] Min_cell_count enforced: counts < 5 show "small count"
- [ ] Row cap enforced: results > 1000 rows are capped
- [ ] Safe aggregates pass through: COUNT, SUM, AVG

**Output:** `tests/test_result_safety_gate.py`

#### 4.4 Trace & Audit Tests
**New file:** `tests/test_tracing_and_audit.py`

- [ ] All decisions are traced (policy, intent, retrieval, cost, execution)
- [ ] Trace includes run_id, user_id, session_id, timestamps
- [ ] SQL is logged
- [ ] Bytes processed logged
- [ ] Refusal reasons logged
- [ ] Prompt stored safely (raw or redacted)

**Output:** `tests/test_tracing_and_audit.py`

### Phase 4 Success Criteria

```
✅ 50+ malicious inputs rejected with appropriate reason
✅ 20+ happy-path flows execute correctly
✅ Result safety blocks identifiers and enforces min_cell_count
✅ Trace is complete and audit trail is tamper-evident
✅ No uncontrolled execution paths exist
```

---

## Critical Path & Dependencies

### Dependency Graph

```
Phase 1 (Track 1 Audit)
├── 1.1 InputPolicyGate audit
├── 1.2 IntentClassifier audit + slots
├── 1.3 RetrievalPermissionGate impl
│   └── requires: user/role lookup, role config
├── 1.4 RetrievalLayer audit + vector/graph
│   └── requires: 1.3 (PermissionScope)
├── 1.5 RetrievedContextGate audit
│   └── requires: 1.2 (intent slots), 1.4 (retrieval)
└── Phase 1 complete ✅

Phase 2 (Track 2 Execution)
├── 2.1 BigQueryDryRun adapter
│   └── requires: BigQuery credentials, test project
├── 2.2 CostExecutionGate (already impl, just wire)
│   └── requires: 2.1 (dry_run_result)
├── 2.3 ReadOnlyExecutor wrapper
│   └── requires: 2.2 (approval_token), read-only service account
├── 2.4 ResultSafetyGate (already impl, verify)
│   └── requires: 2.3 (execution result)
├── 2.5 InterpretationCitations (already impl, verify)
│   └── requires: 2.4 (safe result)
├── 2.6 BoundedFollowUp (already impl, verify)
└── Phase 2 complete ✅

Phase 3 (User-Facing)
├── 3.1 FastAPI endpoint
│   └── requires: Phase 1 + 2 complete
├── 3.2 Streamlit demo
│   └── requires: 3.1
└── Phase 3 complete ✅

Phase 4 (Testing)
├── All depends on Phase 1 + 2 complete
└── Phase 4 complete ✅
```

### Critical Blockers

**Must complete before Phase 2 execution:**
1. RetrievalPermissionGate role enforcement (1.3)
2. IntentClassifier slot extraction (1.2)
3. RetrievedContextGate validation (1.5)

**Must complete before Phase 3:**
1. All of Phase 1 and 2

**Must complete before production:**
1. All 50+ malicious input tests pass
2. Result safety gate blocks all identifiers
3. Approval token barrier enforced
4. Audit trail complete and tamper-evident

---

## Timeline Estimate

```
Phase 1: 2–3 days (policy, intent, retrieval)
Phase 2: 3–4 days (execution, cost, results)
Phase 3: 2–3 days (API, Streamlit)
Phase 4: 2–3 days (testing)

Total: 9–13 days for MVP with all phases

Fast track (core only, no Streamlit):
Phase 1: 2 days
Phase 2: 3 days
Phase 4: 2 days
Total: 7 days
```

---

## Recommended Starting Point

**For MVP with maximum security value, in order:**

1. **Week 1, Days 1–2:** Phase 1.1–1.5 (harden Track 1)
   - Reject all malicious inputs
   - Extract intent slots
   - Enforce retrieval permissions
   - Validate context gates

2. **Week 1, Days 3–4:** Phase 2.1–2.3 (build execution path)
   - Dry-run adapter
   - Cost gate (already impl, just verify)
   - Approval token barrier

3. **Week 2, Days 1–2:** Phase 2.4–2.6 (complete backend)
   - Result safety gate
   - Interpretation + citations
   - Bounded follow-up

4. **Week 2, Days 3–4:** Phase 3 (user-facing)
   - FastAPI endpoint
   - Streamlit demo

5. **Week 3:** Phase 4 (testing)
   - Malicious input suite
   - Happy-path suite
   - Result safety suite
   - Trace & audit suite

**This sequence ensures:**
- ✅ Policy + intent rejection happens first (fail-fast)
- ✅ Retrieval is permission-gated before context reaches LLM
- ✅ Execution is blocked until approval token exists
- ✅ Results are validated before interpretation
- ✅ Testing hardens the entire pipeline

---

## Definition of Done: MVP

**Product**
- [ ] FastAPI endpoint with `/ask` and `/resume`
- [ ] Streamlit demo showing trace, SQL, bytes, result, citations, refusals
- [ ] End-to-end flow: prompt → policy → intent → retrieval → plan → SQL → dry-run → cost → execution → result safety → interpretation

**Security**
- [ ] 50+ malicious inputs rejected with appropriate reason
- [ ] Result safety blocks all identifier columns
- [ ] Approval token required before any execution
- [ ] Audit trail logs all decisions

**Quality**
- [ ] All tests passing (unit, integration, e2e)
- [ ] Trace shows every node and decision
- [ ] Zero uncontrolled execution paths
- [ ] Clarification flow works end-to-end

---

## Notes for Implementation

### File Structure
```
src/
├── agent_host/
│   ├── graph.py (already exists; verify wiring)
│   ├── nodes/
│   │   ├── intent_nodes.py (update to extract slots)
│   │   ├── retrieval_nodes.py (update permission gate)
│   │   └── ... (rest already exist)
│   ├── budget.py (already exists)
│   └── state.py (already exists; may add fields)
├── sql/
│   ├── bigquery_adapter.py (update with dry-run + executor)
│   ├── execution.py (already exists; verify)
│   └── ... (rest already exist)
├── retrieval/
│   ├── search.py (update with vector + graph)
│   └── client.py (already exists)
├── policy/
│   └── screen.py (already exists)
└── app/ (NEW)
    ├── api.py (NEW FastAPI)
    └── streamlit_app.py (NEW UI)

tests/
├── test_*.py (already exist; audit and enhance)
├── policy_gate/
│   └── test_complete_screening.py (NEW 50+ cases)
├── e2e/
│   ├── test_full_pipeline_security.py (NEW)
│   └── test_full_pipeline_happy.py (NEW)
└── test_result_safety_gate.py (NEW)
```

### Config File Structure
```
config/
├── role_allowlists.yaml (NEW)
└── sensitivity_tags.yaml (NEW)

Example role_allowlists.yaml:
```yaml
roles:
  analytics:
    allowed_tables: [APPOINTMENT_FACT, PROVIDER, DEPT]
    allowed_columns:
      APPOINTMENT_FACT: [APPT_DATE, PROVIDER_ID, DEPT_ID, STATUS]
      PROVIDER: [PROVIDER_ID, PROVIDER_NAME, DEPT_ID]
    masked_columns:
      PROVIDER: { PROVIDER_NAME: "name" }
  
  research:
    allowed_tables: [PATIENT_FACT, ENCOUNTER_FACT]
    allowed_columns:
      PATIENT_FACT: [PATIENT_ID, AGE, GENDER]
      ENCOUNTER_FACT: [ENCOUNTER_ID, PATIENT_ID, DATE]
    masked_columns: {}
```

### Environment Variables to Add
```
BIGQUERY_PROJECT=your-project
BIGQUERY_LOCATION=us-west1
BIGQUERY_READ_ONLY_SERVICE_ACCOUNT_EMAIL=...
BIGQUERY_READ_ONLY_SERVICE_ACCOUNT_KEY_FILE=...
HMAC_SECRET=<32+ bytes, base64-encoded>
MAX_RESULT_ROWS=1000
MIN_CELL_COUNT=5
RETRIEVAL_EXPANSION_LIMIT=2
SQL_REPAIR_LIMIT=2
COST_REPLAN_LIMIT=1
```

---

## Handoff: What to Do Next

**Immediate (next 2 hours):**
1. Read this doc
2. Create a branch: `git checkout -b impl/track1-audit`
3. Create a checklist issue with Phase 1 items
4. Schedule Phase 1 work

**Next (this week):**
1. Complete Phase 1 audit (2–3 days)
2. Create PRs for each sub-phase (1.1, 1.2, 1.3, 1.4, 1.5)
3. Each PR includes tests + audit

**See also:**
- [[docs/sql_future_security_improvements.md]] — minor hardening for future
- SQL workflow architecture is sound; this roadmap builds *around* it

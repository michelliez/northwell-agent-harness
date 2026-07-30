# Track 2: Completely Independent Work (No Schema Agreement Needed)

**Status:** Work you can start immediately while Track 1 finalizes contracts  
**Date:** 2026-07-30

---

## TL;DR

You can build **~70% of Track 2 right now** without waiting for Track 1 to finalize schemas. These are self-contained, deterministic modules that take simple inputs (SQL strings, hardcoded permission scopes, mocked results).

The remaining **~30% requires one or two simple contracts** (which you can finalize later).

---

## Completely Independent Work (Start Immediately)

### 1. BigQueryDryRun Adapter
**Status:** Stubbed in `sql/bigquery_adapter.py:16–17`  
**Inputs:** SQL string + BigQuery project/location  
**Outputs:** `DryRunResult` (defined in `sql/models.py`)  
**Dependencies:** BigQuery test project + credentials (no Track 1)

```python
def dry_run(sql: str, project: str, location: str) -> DryRunResult:
    """Run query with dry_run=True to estimate cost without execution."""
    job = client.query(sql, project=project, location=location, dry_run=True)
    return DryRunResult(
        valid=True,
        total_bytes_processed=job.total_bytes_processed,
        project=project,
        location=location,
        referenced_tables=_extract_table_names(job.referenced_tables),
    )
```

**Tests you can write now:**
```python
def test_dry_run_valid_sql():
    sql = "SELECT COUNT(*) FROM APPOINTMENT_FACT"
    result = dry_run(sql, "test-project", "us-west1")
    assert result.valid
    assert result.total_bytes_processed > 0

def test_dry_run_invalid_sql():
    sql = "SELECT * FROM nonexistent_table"
    result = dry_run(sql, "test-project", "us-west1")
    assert not result.valid
    assert result.error is not None

def test_dry_run_extract_tables():
    sql = "SELECT * FROM APPOINTMENT_FACT JOIN PROVIDER USING (PROVIDER_ID)"
    result = dry_run(sql, "test-project", "us-west1")
    assert "APPOINTMENT_FACT" in result.referenced_tables
    assert "PROVIDER" in result.referenced_tables
```

**Effort:** 1 day  
**Dependencies:** Only BigQuery test project + service account

---

### 2. ReadOnlyExecutor Implementation
**Status:** Token-gated wrapper exists in `sql/execution.py`; no executor  
**Inputs:** `CompiledQuery` + `approval_token` + `maximum_bytes_billed`  
**Outputs:** Query result (Arrow table or list of dicts)  
**Dependencies:** BigQuery test project + read-only service account (no Track 1)

```python
class BigQueryReadOnlyExecutor:
    def __init__(self, project: str, location: str, service_account_key_path: str):
        self.project = project
        self.location = location
        self.client = bigquery.Client.from_service_account_json(service_account_key_path)
    
    def execute(self, compiled: CompiledQuery, maximum_bytes_billed: int) -> list[dict]:
        """Execute approved query with strict safety constraints."""
        job_config = bigquery.QueryJobConfig(
            use_legacy_sql=False,
            query_parameters=_bind_parameters(compiled.parameters),
            maximum_bytes_billed=maximum_bytes_billed,
            labels={
                "source": "agent-harness",
                "sql_source": compiled.source,  # "deterministic", "claude_repair"
            },
            timeout_ms=30_000,  # 30 second timeout
            allow_large_results=False,  # no destination table
        )
        
        job = self.client.query(compiled.sql, job_config=job_config)
        job.result()  # Block until complete or timeout
        return [dict(row) for row in job]
```

**Tests you can write now:**
```python
def test_executor_executes_deterministic_sql(executor, approval_token):
    compiled = CompiledQuery(
        sql="SELECT 'hello' as greeting",
        parameters=[],
        plan_hash="abc123...",
        compiler_version="approved-plan-bigquery-v1",
        source="deterministic",
    )
    
    result = executor.execute(compiled, maximum_bytes_billed=1_000_000)
    
    assert len(result) == 1
    assert result[0]["greeting"] == "hello"

def test_executor_respects_maximum_bytes_billed(executor, approval_token):
    # Mocked BigQuery to raise quota error if exceeded
    compiled = CompiledQuery(
        sql="SELECT * FROM large_table",  # Would scan 10GB
        parameters=[],
        plan_hash="...",
        compiler_version="...",
        source="deterministic",
    )
    
    with pytest.raises(exceptions.BadRequest):  # quota exceeded
        executor.execute(compiled, maximum_bytes_billed=1_000_000)  # 1MB limit

def test_executor_enforces_timeout():
    compiled = CompiledQuery(
        sql="SELECT * FROM large_table WHERE FALSE",  # Fast
        parameters=[],
        plan_hash="...",
        compiler_version="...",
        source="deterministic",
    )
    
    result = executor.execute(compiled, maximum_bytes_billed=10_000_000)
    assert len(result) == 0  # Empty result, no timeout
```

**Effort:** 1 day  
**Dependencies:** Only BigQuery test project + read-only service account

---

### 3. Approval Token Generation & Validation
**Status:** Stub in `sql/cost_gate.py` (partially implemented)  
**Inputs:** SQL hash + plan hash + scope hash + max_bytes + expiry  
**Outputs:** Opaque token (HMAC-based)  
**Dependencies:** Only HMAC secret (no Track 1)

```python
# Already mostly implemented; just needs completion/testing
TOKEN_VERSION = "v1"
HMAC_ALGORITHM = "sha256"

def generate_approval_token(
    sql_hash: str,
    plan_hash: str,
    scope_hash: str,
    max_bytes: int,
    expiry: datetime,
    hmac_secret: bytes,
) -> str:
    """Generate opaque, unforgeable approval token."""
    payload = f"{TOKEN_VERSION}:{sql_hash}:{plan_hash}:{scope_hash}:{max_bytes}:{expiry.isoformat()}"
    signature = hmac.new(
        hmac_secret,
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    return base64.b64encode(f"{payload}:{signature}".encode()).decode()

def require_valid_approval_token(
    token: str,
    compiled: CompiledQuery,
    approved: ApprovedQueryPlan,
    hmac_secret: bytes,
    expected_max_bytes: int,
) -> int:
    """Validate token before execution; return authorized max_bytes or raise."""
    try:
        decoded = base64.b64decode(token).decode()
        payload_str, provided_signature = decoded.rsplit(":", 1)
        
        # Verify signature
        expected_signature = hmac.new(
            hmac_secret,
            payload_str.encode(),
            hashlib.sha256,
        ).hexdigest()
        
        if not hmac.compare_digest(provided_signature, expected_signature):
            raise ApprovalTokenError("signature mismatch")
        
        # Parse and validate
        version, sql_hash, plan_hash, scope_hash, max_bytes_str, expiry_str = payload_str.split(":")
        
        if version != TOKEN_VERSION:
            raise ApprovalTokenError("version mismatch")
        
        # Validate hashes match request
        if sql_hash != _hash(compiled.sql):
            raise ApprovalTokenError("SQL hash mismatch")
        if plan_hash != approved.plan_hash:
            raise ApprovalTokenError("plan hash mismatch")
        if scope_hash != approved.scope_hash:
            raise ApprovalTokenError("scope hash mismatch")
        
        # Validate expiry
        expiry = datetime.fromisoformat(expiry_str)
        if datetime.utcnow() > expiry:
            raise ApprovalTokenError("token expired")
        
        # Return authorized max_bytes
        return int(max_bytes_str)
    
    except (ValueError, IndexError, TypeError) as exc:
        raise ApprovalTokenError(f"malformed token: {exc}") from exc
```

**Tests you can write now:**
```python
def test_generate_and_validate_token():
    secret = b"test-secret-key-at-least-32-bytes!"
    sql_hash = "abc123"
    plan_hash = "def456"
    scope_hash = "ghi789"
    max_bytes = 1_000_000
    expiry = datetime.utcnow() + timedelta(minutes=5)
    
    token = generate_approval_token(sql_hash, plan_hash, scope_hash, max_bytes, expiry, secret)
    
    authorized_bytes = require_valid_approval_token(
        token,
        compiled=CompiledQuery(sql="SELECT ...", plan_hash=plan_hash, ...),
        approved=ApprovedQueryPlan(plan_hash=plan_hash, scope_hash=scope_hash, ...),
        hmac_secret=secret,
        expected_max_bytes=max_bytes,
    )
    
    assert authorized_bytes == max_bytes

def test_token_expires():
    secret = b"test-secret-key-at-least-32-bytes!"
    expiry = datetime.utcnow() - timedelta(minutes=1)  # Already expired
    
    token = generate_approval_token(..., expiry, secret)
    
    with pytest.raises(ApprovalTokenError, match="expired"):
        require_valid_approval_token(token, compiled, approved, secret, ...)

def test_token_signature_validation():
    secret = b"test-secret-key-at-least-32-bytes!"
    token = generate_approval_token(...)
    
    # Tamper with token
    tampered = token[:-5] + "xxxxx"
    
    with pytest.raises(ApprovalTokenError, match="signature"):
        require_valid_approval_token(tampered, compiled, approved, secret, ...)
```

**Effort:** 1 day (mostly already implemented; just complete + test)  
**Dependencies:** None (pure crypto)

---

### 4. SQL Static Validation (Already Exists)
**Status:** Complete in `sql/validation.py` (7 ordered gates)  
**Inputs:** SQL string + declared tables + optional schema snapshot  
**Outputs:** `SqlValidationResult` (allowed/blocked with violations)  
**Dependencies:** SQLGlot (already imported; no Track 1)

**Your work:**
- ✅ Verify all 7 gates work correctly
- ✅ Add new test cases for BigQuery-specific edge cases:
  - [ ] Recursive CTEs forbidden
  - [ ] Window functions forbidden
  - [ ] UDFs forbidden
  - [ ] Wildcard tables forbidden
  - [ ] SELECT * forbidden
  - [ ] Temp functions forbidden
  - [ ] External connections forbidden
  - [ ] STRUCT star `SELECT t.*` forbidden

**Tests you can write now:**
```python
def test_validate_rejects_recursive_cte():
    sql = """
    WITH RECURSIVE cte AS (
        SELECT 1 as n
        UNION ALL
        SELECT n + 1 FROM cte WHERE n < 10
    )
    SELECT * FROM cte
    """
    result = validate_sql(sql, ["cte"])
    assert not result.allowed
    assert result.reason == "recursive_cte_not_allowed"

def test_validate_rejects_window_functions():
    sql = "SELECT ROW_NUMBER() OVER (ORDER BY col) FROM table1"
    result = validate_sql(sql, ["table1"])
    assert not result.allowed
    assert result.reason == "window_function_not_allowed"
```

**Effort:** 0.5 day (mostly existing; just add edge cases)  
**Dependencies:** None (already standalone)

---

### 5. SQL Compilation (Already Exists)
**Status:** Complete in `sql/compiler.py` (`plan_to_bigquery_sql()`)  
**Inputs:** `ApprovedQueryPlan`  
**Outputs:** `CompiledQuery` (SQL + parameters + metadata)  
**Dependencies:** SqlGlot (no Track 1)

**Your work:**
- ✅ Verify all supported plan features compile correctly
- ✅ Test edge cases:
  - [ ] Multiple groupings
  - [ ] Multiple aggregations
  - [ ] Parameter binding correctness
  - [ ] JOIN ordering (left, right, inner)
  - [ ] Time constraint compilation

**Tests you can write now:**
```python
def test_compile_with_multiple_groupings():
    plan = ApprovedQueryPlan(
        plan=QueryPlanAST(
            tables=["APPOINTMENT_FACT"],
            groupings=[CatalogRef("APPOINTMENT_FACT", "DEPT_ID"), 
                       CatalogRef("APPOINTMENT_FACT", "STATUS")],
            aggregations=[PlannedAggregation("COUNT", alias="cnt")],
            # ... rest of plan ...
        ),
        # ... rest of approved plan ...
    )
    
    compiled = plan_to_bigquery_sql(plan)
    
    assert "GROUP BY APPOINTMENT_FACT.DEPT_ID, APPOINTMENT_FACT.STATUS" in compiled.sql

def test_compile_preserves_parameter_binding():
    plan = ApprovedQueryPlan(
        plan=QueryPlanAST(
            tables=["APPOINTMENT_FACT"],
            filters=[PlannedFilter(
                column=CatalogRef("APPOINTMENT_FACT", "APPT_DATE"),
                operator=">=",
                parameter_names=["start_date"],
            )],
            # ... rest of plan ...
        ),
        # ... rest of approved plan ...
    )
    
    compiled = plan_to_bigquery_sql(plan)
    
    assert "@start_date" in compiled.sql  # Parameter placeholder
    assert len(compiled.parameters) == 1
```

**Effort:** 0.5 day  
**Dependencies:** None (already standalone)

---

### 6. Result Safety Gate (Mostly Exists)
**Status:** Exists in `nodes/output_safety_nodes.py`; verify completeness  
**Inputs:** Raw result dict + hardcoded permission scope  
**Outputs:** Safe result (identifiers blocked, sensitivity redacted)  
**Dependencies:** Only `PermissionScope` (can hardcode for now)

**Your work:**
- ✅ Verify identifier blocking: patient_id, MRN, name, DOB, account_id, encounter_id all blocked
- ✅ Verify sensitivity redaction: columns marked `sensitive` replaced with `[REDACTED]`
- ✅ Verify min_cell_count: if `count < 5`, show `[SMALL_COUNT]` instead
- ✅ Verify row capping: if `len(result) > 1000`, truncate + add note

**Tests you can write now (with hardcoded scope):**
```python
def test_result_safety_blocks_patient_id():
    result = {
        "rows": [
            {"patient_id": 123, "appointment_count": 5},
            {"patient_id": 456, "appointment_count": 3},
        ],
        "schema": ["patient_id", "appointment_count"],
    }
    
    # Hardcoded permission scope
    scope = PermissionScope(
        schema_snapshot=SchemaSnapshot(
            tables=[SchemaTable(
                name="APPOINTMENT_FACT",
                columns=[
                    SchemaColumn(name="PATIENT_ID", safety="identifier"),
                    SchemaColumn(name="APPOINTMENT_COUNT", safety="safe_aggregate"),
                ],
                source_chunk_ids=[],
            )],
        ),
    )
    
    safe = apply_result_safety(result, scope)
    
    assert "patient_id" not in safe["schema"]
    assert len(safe["rows"]) > 0
    assert "appointment_count" in safe["schema"]

def test_result_safety_enforces_min_cell_count():
    result = {
        "rows": [
            {"dept": "CARDIOLOGY", "count": 3},  # < 5
            {"dept": "ONCOLOGY", "count": 15},
        ],
        "schema": ["dept", "count"],
    }
    
    safe = apply_result_safety(result, scope, min_cell_count=5)
    
    # First row's count should be suppressed
    assert safe["rows"][0]["count"] == "[SMALL_COUNT]"
    assert safe["rows"][1]["count"] == 15
```

**Effort:** 1 day (mostly existing; just verify + add edge cases)  
**Dependencies:** Only hardcoded `PermissionScope` (no Track 1 needed)

---

### 7. Cost Gate Logic Verification
**Status:** Already implemented in `sql_nodes.py:356–409`  
**Inputs:** Dry-run bytes + cost config + approved plan  
**Outputs:** `CostGateResult` (approved/rejected + token or violations)  
**Dependencies:** Only config (no Track 1)

**Your work:**
- ✅ Verify logic: dry_run_bytes <= max_bytes
- ✅ Verify partition enforcement (if fact table > threshold, partition filter required)
- ✅ Test edge cases:
  - [ ] Exactly at limit (should pass)
  - [ ] Just over limit (should fail)
  - [ ] Partition filter bypass attempts (should fail)

**Tests you can write now:**
```python
def test_cost_gate_approves_under_budget():
    dry_run = DryRunResult(valid=True, total_bytes_processed=100_000_000)  # 100 MB
    config = CostExecutionConfig(max_bytes=1_000_000_000)  # 1 GB
    
    decision = evaluate_cost_execution(compiled, approved, dry_run, config)
    
    assert decision.allowed
    assert decision.approval_token is not None
    assert decision.expires_at is not None

def test_cost_gate_rejects_over_budget():
    dry_run = DryRunResult(valid=True, total_bytes_processed=2_000_000_000)  # 2 GB
    config = CostExecutionConfig(max_bytes=1_000_000_000)  # 1 GB limit
    
    decision = evaluate_cost_execution(compiled, approved, dry_run, config)
    
    assert not decision.allowed
    assert any(v.code == "exceeds_max_bytes" for v in decision.violations)
```

**Effort:** 0.5 day  
**Dependencies:** None (already implemented; just verify + test)

---

### 8. Infrastructure Setup (No Code)
**Status:** Not started  
**Inputs:** Your GCP project  
**Outputs:** BigQuery test project + service accounts + HMAC secret

**Your work:**
```bash
# 1. Create BigQuery test project
gcloud projects create test-sql-harness --name="Test SQL Harness"
gcloud config set project test-sql-harness

# 2. Enable BigQuery API
gcloud services enable bigquery.googleapis.com

# 3. Create read-only service account
gcloud iam service-accounts create sql-agent-readonly \
  --display-name="SQL Agent Read-Only Service Account"

# 4. Grant BigQuery read-only permissions
gcloud projects add-iam-policy-binding test-sql-harness \
  --member=serviceAccount:sql-agent-readonly@test-sql-harness.iam.gserviceaccount.com \
  --role=roles/bigquery.dataViewer

gcloud projects add-iam-policy-binding test-sql-harness \
  --member=serviceAccount:sql-agent-readonly@test-sql-harness.iam.gserviceaccount.com \
  --role=roles/bigquery.jobUser

# 5. Create and download key
gcloud iam service-accounts keys create ./sql-agent-readonly-key.json \
  --iam-account=sql-agent-readonly@test-sql-harness.iam.gserviceaccount.com

# 6. Create test data (sample APPOINTMENT_FACT table)
bq mk --dataset test_sql_harness

bq load test_sql_harness.APPOINTMENT_FACT \
  <(echo 'APPT_ID,APPT_DATE,PROVIDER_ID,DEPT_ID,STATUS
1,2026-01-01,100,1,COMPLETED
2,2026-01-02,101,1,COMPLETED
3,2026-01-03,102,2,CANCELLED') \
  --source_format CSV

# 7. Generate HMAC secret (32+ bytes)
openssl rand -base64 32 > .env.local
echo "BIGQUERY_APPROVAL_HMAC_SECRET=$(cat /dev/urandom | base64 | head -c 44)" >> .env.local
```

**Effort:** 1–2 hours  
**Dependencies:** GCP credentials + gcloud CLI

---

### 9. Audit Logging Infrastructure
**Status:** Not started  
**Inputs:** Run ID, user ID, decision, SQL, bytes  
**Outputs:** Audit log storage (JSON file, database, or logging service)

**Your work:**
```python
# src/audit_log.py (NEW)

import json
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict

@dataclass
class AuditEvent:
    timestamp: str
    run_id: str
    user_id: str
    event_type: str  # "policy_check", "intent_classified", "retrieval", "plan_safety", "sql_compiled", "dry_run", "cost_gate", "execution", "result_safety"
    decision: str  # "allowed", "rejected", "error"
    reason: str | None = None
    sql: str | None = None
    bytes_processed: int | None = None
    referenced_tables: list[str] | None = None

class AuditLog:
    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
    
    def record(self, event: AuditEvent):
        """Append audit event to log file."""
        log_file = self.log_dir / f"{event.run_id}.jsonl"
        with open(log_file, "a") as f:
            f.write(json.dumps(asdict(event), default=str) + "\n")
```

**Tests:**
```python
def test_audit_log_records_event(tmp_path):
    audit = AuditLog(tmp_path)
    event = AuditEvent(
        timestamp=datetime.utcnow().isoformat(),
        run_id="run-123",
        user_id="user-456",
        event_type="sql_compiled",
        decision="allowed",
        sql="SELECT COUNT(*) FROM table",
        bytes_processed=1000,
    )
    
    audit.record(event)
    
    log_file = tmp_path / "run-123.jsonl"
    assert log_file.exists()
    
    with open(log_file) as f:
        logged = json.loads(f.readline())
    
    assert logged["event_type"] == "sql_compiled"
    assert logged["decision"] == "allowed"
```

**Effort:** 1 day  
**Dependencies:** None (pure file I/O)

---

### 10. FastAPI Endpoint Skeleton
**Status:** Not started  
**Inputs:** JSON `{session_id, user_id, prompt}`  
**Outputs:** JSON response with placeholder data  
**Dependencies:** None (can hardcode responses for now)

```python
# src/app/api.py (NEW)

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

class AskRequest(BaseModel):
    session_id: str
    user_id: str
    prompt: str

class AskResponse(BaseModel):
    answer: str
    sql: str | None = None
    result: dict | None = None
    bytes_processed: int | None = None
    citations: list[str] = []
    clarification_prompt: str | None = None
    refusal_reason: str | None = None
    trace_file: str = ""

@app.post("/ask")
def ask(request: AskRequest) -> AskResponse:
    """Handle a user question through the full pipeline."""
    # For now, return hardcoded response
    return AskResponse(
        answer="This is a placeholder answer. Full pipeline will run here.",
        sql="SELECT COUNT(*) FROM APPOINTMENT_FACT",
        bytes_processed=1_000_000,
        citations=["chunk-123", "chunk-456"],
    )

@app.post("/resume")
def resume(session_id: str, reply: str) -> AskResponse:
    """Resume an interrupted question (clarification)."""
    return AskResponse(
        answer="Resumed question answered.",
    )
```

**Tests:**
```python
def test_ask_endpoint():
    response = client.post("/ask", json={
        "session_id": "sess-123",
        "user_id": "user-456",
        "prompt": "Count appointments",
    })
    
    assert response.status_code == 200
    data = response.json()
    assert "answer" in data
    assert data["answer"]
```

**Effort:** 0.5 day  
**Dependencies:** None (skeleton only)

---

### 11. Streamlit UI Skeleton
**Status:** Not started  
**Inputs:** Session, user_id, prompt  
**Outputs:** Web UI with hardcoded trace/SQL/result views  
**Dependencies:** None (can hardcode data for now)

```python
# src/app/streamlit_app.py (NEW)

import streamlit as st
import requests
import json

st.set_page_config(page_title="SQL Agent Harness", layout="wide")

st.title("SQL Agent Harness")

col1, col2 = st.columns(2)

with col1:
    session_id = st.text_input("Session ID", "session-123")
    user_id = st.text_input("User ID", "user-456")

prompt = st.text_area("Your question", "Count appointments by department")

if st.button("Ask"):
    # Call /ask endpoint
    response = requests.post("http://localhost:8000/ask", json={
        "session_id": session_id,
        "user_id": user_id,
        "prompt": prompt,
    }).json()
    
    st.success(f"Answer: {response['answer']}")
    
    if response.get("sql"):
        st.code(response["sql"], language="sql")
    
    if response.get("bytes_processed"):
        st.metric("Bytes Processed", f"{response['bytes_processed']:,}")
    
    if response.get("citations"):
        st.info(f"Citations: {', '.join(response['citations'])}")

st.divider()

st.subheader("Trace")
st.json({
    "nodes_executed": [
        "input_policy",
        "intent_classifier",
        "retrieval_permission",
        "retrieve_context",
        "context_gate",
        "query_plan",
        "plan_safety",
        "write_sql",
        "validate_sql",
        "execution_not_configured",
    ],
})
```

**Effort:** 0.5 day  
**Dependencies:** None (skeleton only)

---

## Summary: Your Work This Week (Completely Independent)

### Total: ~6–7 Days of Work, Zero Track 1 Dependencies

| Item | Effort | Status | Start |
|------|--------|--------|-------|
| 1. BigQueryDryRun adapter | 1 day | Ready | Tomorrow |
| 2. ReadOnlyExecutor | 1 day | Ready | Tomorrow |
| 3. Approval token HMAC | 1 day | Partial; complete it | Tomorrow |
| 4. SQL validation edge cases | 0.5 day | Ready | This week |
| 5. SQL compilation edge cases | 0.5 day | Ready | This week |
| 6. Result safety gate verification | 1 day | Ready | This week |
| 7. Cost gate testing | 0.5 day | Ready | This week |
| 8. Infrastructure setup (GCP) | 2 hours | Ready | Today |
| 9. Audit logging | 1 day | Ready | This week |
| 10. FastAPI skeleton | 0.5 day | Ready | Next week |
| 11. Streamlit skeleton | 0.5 day | Ready | Next week |

**Total: 6.5 days of focused work**

---

## What You Should NOT Do Yet

❌ InterpretationCitations (needs citations format from Track 1)  
❌ BoundedFollowUp (needs to understand state structure from Track 1)  
❌ End-to-end testing (needs Track 1 to provide real intent/retrieval)  
❌ Integration phase (wait until Track 1 finalizes schemas)  

---

## What You're Doing Well

You're being **smart** about decoupling. Instead of guessing what Track 1 will produce, you're building what you *know* you need. This is the right approach.

**Result:** When Track 1 finishes, you'll swap mocks for real data in ~30 minutes, not rewrite everything.

---

## Recommended Order for This Week

```
Today (2 hours):
  ✅ Infrastructure setup (GCP, service accounts, test data)

Tomorrow (Day 1–2):
  ✅ BigQueryDryRun adapter (1 day)
  ✅ ReadOnlyExecutor (1 day)

Day 3–4:
  ✅ Approval token HMAC completion (0.5 day)
  ✅ Audit logging (1 day)
  ✅ SQL validation edge cases (0.5 day)

Day 5:
  ✅ SQL compilation edge cases (0.5 day)
  ✅ Result safety gate verification (1 day)
  ✅ Cost gate testing (0.5 day)

Next week:
  ✅ FastAPI skeleton (0.5 day)
  ✅ Streamlit skeleton (0.5 day)
  ✅ Wait for Track 1; then integrate
```

---

## Definition of Done: Independent Track 2 Work

```
✅ BigQueryDryRun adapter works with test BigQuery project
✅ ReadOnlyExecutor runs with timeout + max_bytes_billed
✅ Approval token can be generated and validated
✅ SQL validation blocks all prohibited operations
✅ SQL compilation produces correct BigQuery SQL
✅ Result safety gate blocks identifiers and enforces min_cell_count
✅ Cost gate rejects high-cost queries
✅ Audit log records all events
✅ FastAPI /ask endpoint returns hardcoded response
✅ Streamlit UI displays hardcoded trace
✅ All tests passing
✅ No mocks of Track 1 yet; all work is self-contained
```

Once Track 1 finishes their schema definitions, you swap in the real `IntentSlots` and `PermissionScope` and run integration tests.

**You're ready to start now.**

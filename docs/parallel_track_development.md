# Parallel Track Development: Working on Track 2 While Track 1 Finishes

**Status:** Guidance for splitting work between two teams  
**Date:** 2026-07-30

---

## Overview

**Track 1** (policy → intent → retrieval → context validation) and **Track 2** (SQL execution path) have **loose coupling**. You can work on Track 2 in parallel with Track 1 if you:

1. **Mock Track 1 outputs** for Track 2 testing (no need to wait)
2. **Define clear contracts** between tracks (interface-first)
3. **Test Track 2 in isolation** until Track 1 is ready to integrate
4. **Defer end-to-end testing** until both are complete

---

## Dependency Analysis

### What Track 2 Needs from Track 1 (Runtime)

Track 2 nodes take these inputs from Track 1:

| Input | Source (Track 1) | For Track 2 | Can Mock? |
|-------|---|---|---|
| `intent` | IntentClassifier | Routes plan → SQL | ✅ Yes, enum |
| `metric` | IntentClassifier (slot) | Plan validation, result interpretation | ✅ Yes, string |
| `entity` | IntentClassifier (slot) | Plan validation | ✅ Yes, string |
| `time_window` | IntentClassifier (slot) | Plan validation (time filters required) | ✅ Yes, string |
| `grouping` | IntentClassifier (slot) | Plan validation (grouping columns) | ✅ Yes, list |
| `filters` | IntentClassifier (slot) | Plan validation (filter safety) | ✅ Yes, list |
| `requested_grain` | IntentClassifier (slot) | Plan validation (granularity) | ✅ Yes, enum |
| `permission_scope` | RetrievalPermissionGate | Plan/SQL validation, cost gate | ✅ Yes, Pydantic model |
| `retrieved_chunks` | RetrievalLayer + RetrievedContextGate | Schema snapshot extraction | ✅ Yes, list of chunks |
| `schema_snapshot` | RetrievalLayer (resolved from chunks) | SQL validation, plan safety | ✅ Yes, Pydantic model |

**All are mockable.** None require live Track 1 execution.

### What Track 2 Doesn't Need from Track 1

✅ **Track 2 is completely independent for:**
- Plan-to-SQL compilation (deterministic `plan_to_bigquery_sql()`)
- SQL static validation (SQLGlot with BigQuery dialect)
- BigQuery dry-run (just needs SQL + credentials)
- Cost gate logic (already implemented)
- Approval token generation
- Read-only execution (needs BigQuery, not Track 1)
- Result safety gate (just needs result + permission scope)
- Interpretation + citations (just needs safe result)

### End-to-End Dependencies

**You cannot test these without Track 1:**

❌ Full pipeline: policy → intent → retrieval → plan → SQL → dry-run → execution  
❌ Permission enforcement: does retrieval respect role limits?  
❌ Intent-driven behavior: schema_discovery returns docs-only vs. aggregate_query executes SQL  
❌ Follow-up with permission boundaries: can user refine without new permissions?  
❌ Real-world happy paths: e.g., "count appointments by department in last 30 days"

**These are deferred until integration.**

---

## Recommended Working Approach

### Step 1: Define Track 1 → Track 2 Contracts (Now, ~2 hours)

Create interfaces/test fixtures that formalize what Track 1 produces:

```python
# src/contracts/track1_outputs.py (NEW - shared between teams)

from dataclasses import dataclass
from typing import Literal

@dataclass
class IntentSlots:
    """Output from IntentClassifier. Track 2 tests mock this."""
    intent: Literal["schema_discovery", "join_help", "aggregate_query", 
                    "cohort_definition", "clarification_needed", "disallowed"]
    metric: str | None  # e.g., "appointment count"
    entity: str | None  # e.g., "encounters"
    time_window: str | None  # e.g., "last 30 days"
    grouping: list[str]  # e.g., ["department", "provider"]
    filters: list[str]  # e.g., ["visit type = office"]
    requested_grain: str | None  # e.g., "daily"
    safety_labels: list[str]  # e.g., ["requires_aggregate", "has_time_window"]

@dataclass
class PermissionScopeFixture:
    """Output from RetrievalPermissionGate. Track 2 mocks this."""
    allowed_tables: list[str]
    allowed_columns: dict[str, list[str]]  # table -> columns
    masked_columns: dict[str, dict]  # table -> {column -> mask_type}
    user_role: str  # e.g., "analytics"

@dataclass
class RetrievedContextFixture:
    """Output from RetrievalLayer + RetrievedContextGate. Track 2 mocks this."""
    chunks: list[dict]  # Retrieved chunk dicts
    schema_snapshot: dict  # Serialized SchemaSnapshot
```

This lets Track 1 and Track 2 work to the same spec without live integration.

### Step 2: Create Track 2 Test Fixtures (Now, ~1 day)

```python
# tests/fixtures/track1_mocks.py (NEW - Track 2 uses these)

@pytest.fixture
def intent_aggregate_monthly():
    """IntentClassifier output: 'count appointments by department, monthly'"""
    return IntentSlots(
        intent="aggregate_query",
        metric="appointment count",
        entity="appointments",
        time_window="last 12 months",
        grouping=["department"],
        filters=[],
        requested_grain="monthly",
        safety_labels=["requires_aggregate", "has_time_window"],
    )

@pytest.fixture
def permission_scope_analytics():
    """RetrievalPermissionGate output for analytics role"""
    return PermissionScopeFixture(
        allowed_tables=["APPOINTMENT_FACT", "PROVIDER", "DEPT"],
        allowed_columns={
            "APPOINTMENT_FACT": ["APPT_DATE", "PROVIDER_ID", "DEPT_ID", "STATUS"],
            "PROVIDER": ["PROVIDER_ID", "PROVIDER_NAME"],
            "DEPT": ["DEPT_ID", "DEPT_NAME"],
        },
        masked_columns={"PROVIDER": {"PROVIDER_NAME": "name"}},
        user_role="analytics",
    )

@pytest.fixture
def retrieved_context_appointments():
    """RetrievalLayer + RetrievedContextGate output"""
    return RetrievedContextFixture(
        chunks=[...],  # List of chunk dicts
        schema_snapshot={
            "tables": [
                {
                    "name": "APPOINTMENT_FACT",
                    "columns": [
                        {"name": "APPT_DATE", "data_type": "DATE", "safety": "safe_aggregate"},
                        {"name": "PROVIDER_ID", "data_type": "INT64", "safety": "identifier"},
                    ],
                    "source_chunk_ids": ["appt-date-chunk", "provider-id-chunk"],
                }
            ],
        },
    )
```

Now Track 2 tests can use these without waiting for Track 1 to be perfect.

### Step 3: Test Track 2 Nodes in Isolation (This week, ~3 days)

```python
# tests/test_track2_execution_isolation.py (NEW)

def test_plan_safety_with_mocked_intent(intent_aggregate_monthly, permission_scope_analytics):
    """Test plan_safety_node with mocked intent and scope."""
    plan = QueryPlanAST(
        objective="Count appointments by department, monthly",
        tables=["APPOINTMENT_FACT", "DEPT"],
        # ... rest of plan ...
    )
    
    state = {
        "question": "Count appointments by department, monthly",
        "intent": intent_aggregate_monthly.intent,
        "metric": intent_aggregate_monthly.metric,
        "grouping": intent_aggregate_monthly.grouping,
        "time_window": intent_aggregate_monthly.time_window,
        "permission_scope": permission_scope_analytics.model_dump(),
        "query_plan": plan.model_dump(),
        "retrieved_chunks": [],
    }
    
    result = plan_safety_node(state)
    assert result["approved_plan"] is not None


def test_dry_run_with_valid_sql(mock_bigquery_client):
    """Test BigQueryDryRun adapter (mocked BigQuery)."""
    sql = "SELECT COUNT(*) as cnt, DEPT_ID FROM APPOINTMENT_FACT GROUP BY DEPT_ID"
    
    result = dry_run(sql, project="test-project", location="us-west1")
    
    assert result.valid
    assert result.total_bytes_processed > 0
    assert "APPOINTMENT_FACT" in result.referenced_tables


def test_cost_gate_rejects_high_cost(mock_dry_run_result):
    """Test cost gate rejects query exceeding max_bytes."""
    dry_run_result = mock_dry_run_result(total_bytes=50_000_000_000)  # 50 GB
    config = CostExecutionConfig(max_bytes=1_000_000_000)  # 1 GB limit
    
    decision = evaluate_cost_execution(compiled, approved, dry_run_result, config)
    
    assert not decision.allowed
    assert any(v.code == "exceeds_max_bytes" for v in decision.violations)


def test_result_safety_blocks_identifiers():
    """Test result safety gate blocks identifier columns."""
    result_with_patient_id = {
        "rows": [
            {"patient_id": 123, "count": 5},  # <-- identifier!
            {"patient_id": 456, "count": 3},
        ],
        "schema": ["patient_id", "count"],
    }
    
    safe_result = apply_result_safety_gate(result_with_patient_id, permission_scope)
    
    assert "patient_id" not in safe_result["schema"]
    assert safe_result.get("safety_notes") is not None
```

All of these **pass without Track 1** because they use mocks.

### Step 4: Develop Track 2 Execution Path (This week, ~3 days)

1. ✅ BigQueryDryRun adapter (needs BigQuery test project)
2. ✅ Wire CostExecutionGate (already impl)
3. ✅ ReadOnlyExecutor with approval token
4. ✅ Verify ResultSafetyGate
5. ✅ Verify InterpretationCitations

All testable with mocks of Track 1 inputs.

### Step 5: Integration Testing (When Track 1 is Ready, ~2 days)

Once Track 1 finishes:

```python
# tests/e2e/test_full_pipeline_integration.py (NEW)

def test_full_pipeline_policy_to_result(graph, mock_bigquery):
    """End-to-end: policy → intent → retrieval → plan → SQL → dry-run → cost → execution → result"""
    response = graph.invoke({
        "question": "Count appointments by department in last 30 days",
        "user_id": "user123",
        "session_id": "session456",
    })
    
    # Now we can test the full chain
    assert response["answer"]
    assert response["generated_sql"]
    assert response["dry_run_bytes"] > 0
    assert "APPOINTMENT_FACT" in response["referenced_tables"]
    # ... more assertions ...
```

This test **doesn't work until Track 1 is done**, but Track 2 doesn't need to wait.

---

## What Each Team Works On

### Track 1 Team (Retrieval)

**Phase 1.1–1.5 (2–3 days):**
1. Audit InputPolicyGate
2. Extract IntentClassifier slots → returns `IntentSlots`
3. Implement RetrievalPermissionGate → returns `PermissionScopeFixture`
4. Enhance RetrievalLayer → returns chunks
5. Enhance RetrievedContextGate → returns `RetrievedContextFixture`

**Definition of Done:**
- [ ] `IntentSlots` are extracted and logged in trace
- [ ] `PermissionScope` is computed from user role
- [ ] RetrievedContextGate validates required fields (table, metric column, time column, etc.)
- [ ] Track 1 integration tests pass

### Track 2 Team (You)

**Phase 2.1–2.6 (3–4 days, in parallel):**
1. Implement BigQueryDryRun adapter
2. Wire CostExecutionGate
3. Implement ReadOnlyExecutor with approval token
4. Verify ResultSafetyGate
5. Verify InterpretationCitations
6. Verify BoundedFollowUp

**Definition of Done:**
- [ ] All Track 2 nodes tested in isolation with mocks
- [ ] Track 2 integration tests pass (dry-run → cost → execution → results → interpretation)
- [ ] Approval token barrier enforced
- [ ] Result safety blocks identifiers and enforces min_cell_count

### Integration Phase (Both Teams, ~2 days)

Once both tracks finish:
1. Wire Track 1 outputs → Track 2 inputs
2. Run full end-to-end tests
3. Test with real TrackI intent extraction and retrieval
4. Build FastAPI + Streamlit

---

## Risk Mitigation

### Risk 1: Contract Changes

**What if Track 1 changes IntentSlots structure mid-development?**

- ✅ Agree on contracts upfront (30 min conversation)
- ✅ Define `contracts/track1_outputs.py` in shared code
- ✅ Both teams commit to that interface
- ✅ Change it only if both agree

### Risk 2: Incompatible Schemas

**What if Track 1 produces different schema than Track 2 expects?**

- ✅ Use Pydantic models for validation
- ✅ Test fixtures use same models
- ✅ Integration test fails fast if incompatible
- ✅ Catch during integration phase (day 7–8), not day 14

### Risk 3: Missing Track 1 Features

**What if Track 1 doesn't finish on time?**

- ✅ Track 2 is 100% done with mocks
- ✅ Integration is straightforward: swap mocks for real Track 1
- ✅ No rework needed
- ✅ Can deploy Track 2 features independently (BigQuery dry-run, cost gate)

### Risk 4: End-to-End Testing Delay

**What if integration reveals issues?**

- ✅ Both teams have working unit tests
- ✅ Issues are in the *contract*, not the implementation
- ✅ Quick to fix once identified

---

## Concrete Timeline: Parallel Execution

```
Week 1, Day 1–2 (Mon–Tue):
├─ Track 1 Team: Phase 1.1–1.2 (policy audit, intent slots)
├─ Track 2 Team: Define contracts, create test fixtures
└─ Both: Review contracts, align on models

Week 1, Day 3–4 (Wed–Thu):
├─ Track 1 Team: Phase 1.3–1.5 (retrieval permissions, context validation)
├─ Track 2 Team: Phase 2.1–2.3 (dry-run adapter, cost gate, approval token)
└─ Both: Parallel development, daily sync

Week 1, Day 5 (Fri):
├─ Track 1 Team: Integration testing (1.1–1.5 together)
├─ Track 2 Team: Integration testing (2.1–2.6 together)
└─ Both: Code review on each other's work

Week 2, Day 1–2 (Mon–Tue):
├─ Track 1 Team: Bugfixes from integration testing
├─ Track 2 Team: Phase 2.4–2.6 (result safety, interpretation, follow-up)
└─ Both: Address review comments

Week 2, Day 3–4 (Wed–Thu):
├─ INTEGRATION PHASE: Wire Track 1 → Track 2
├─ Run full end-to-end tests
├─ Fix contract mismatches (if any)
└─ Both: Full pipeline testing

Week 2, Day 5 (Fri):
├─ FastAPI + Streamlit (if time)
├─ Malicious input testing
└─ Prep for production hardening

Total: 10 days for full pipeline + API, vs. 13 days sequential
```

---

## Definition of Done: Each Track

### Track 1 Done (Before Integration)
```
✅ IntentClassifier extracts all slots
✅ RetrievalPermissionGate enforces role-based access
✅ RetrievalLayer returns top 5 tables, top 30 columns, top 5 join paths
✅ RetrievedContextGate validates required context
✅ All Track 1 tests pass (unit + integration)
```

### Track 2 Done (Before Integration)
```
✅ BigQueryDryRun adapter captures bytes and tables
✅ CostExecutionGate rejects high-cost queries
✅ Approval token barrier enforced before execution
✅ ReadOnlyExecutor runs with timeout + max_bytes_billed
✅ ResultSafetyGate blocks identifiers and enforces min_cell_count
✅ InterpretationCitations shows SQL, result shape, assumptions, caveats, citations
✅ BoundedFollowUp enforces retry limits
✅ All Track 2 tests pass (unit + integration)
```

### Integration Done (After Both Tracks)
```
✅ Full end-to-end pipeline (policy → intent → retrieval → plan → SQL → execution → results)
✅ Real intent slots flow through to plan/SQL validation
✅ Real permission scope blocks forbidden columns in retrieval AND SQL
✅ Real BigQuery execution with real dry-run and cost gating
✅ All e2e tests pass
✅ 50+ malicious input tests pass
```

---

## Communication Plan

**Daily (15 min):**
- Sync on blockers and contract changes
- Confirm no incompatibilities

**Per-Phase (before finishing each phase):**
- Share test fixtures and mock data
- Review contract usage
- Confirm interfaces match

**At Integration (careful alignment):**
- Both teams present their Track's test results
- Identify contract mismatches early
- Plan quick fixes

---

## Bottom Line

**Yes, you can work on Track 2 now.** You'll be 90% done when Track 1 finishes, and integration will take 1–2 days instead of 4.

The key is:
1. **Mock Track 1 outputs** (contracts + fixtures)
2. **Test Track 2 in isolation** (no live Track 1 needed)
3. **Defer end-to-end testing** until both complete
4. **Swap mocks for real Track 1** at integration time

You're trading "wait for Track 1" for "align on contracts upfront," which saves time overall.

---

## Next Steps (For You, Track 2)

1. **Today (1–2 hours):** Sync with Track 1 team on contracts
2. **Tomorrow morning:** Define `contracts/track1_outputs.py` together
3. **Tomorrow afternoon:** Start Phase 2.1 (BigQueryDryRun adapter)
4. **This week:** Complete Phase 2.1–2.6 with mocked Track 1 inputs
5. **Next week:** Integrate with real Track 1 outputs

See [[docs/implementation_roadmap.md]] for Phase 2 detailed checklist.

# ADR 006: Streamlit Direct Python Calls (No FastAPI for MVP)

**Date:** 2026-07-30  
**Status:** ACCEPTED (NOT YET IMPLEMENTED)  
**Scope:** User-facing interface (Phase 3)

## Decision

For the MVP, Streamlit will **call the LangGraph workflow directly** as Python functions, without an intermediate FastAPI backend.

```python
# app.py
from agent_host.graph import build_workflow

def ask_question(question: str, user_id: str):
    workflow = build_workflow(config)
    events = workflow.run(question, user_id=user_id)
    audit_log.record_events(events)
    return events
```

**Deferred:** FastAPI backend can be added later when needed (mobile app, third-party API access).

## Rationale

### Problem
Need a user-facing interface for analysts to query data. Options:
1. **Direct Python calls** (Streamlit calls workflow as library) - Simple, fast, works for single client
2. **FastAPI backend** (Streamlit calls HTTP API, separate backend service) - Flexible, scalable, more infrastructure

### Why Direct Calls for MVP

| Aspect | Direct | FastAPI | Winner |
|--------|--------|---------|--------|
| Dev time | 1 day | 2 days | Direct |
| Deployment | 1 process | 2 processes | Direct |
| Latency | <100ms | 100-500ms | Direct |
| Multiple clients | Not supported | Supported | FastAPI |
| Separate scaling | Not possible | Possible | FastAPI |
| Infrastructure | Minimal | Moderate | Direct |
| MVP readiness | Now | 2 weeks | Direct |

**For MVP:** Single analyst using Streamlit → direct calls work fine.

**For production scaling:** Multiple analysts, mobile app, third-party integrations → add FastAPI later.

## Implementation

**Deployment:**
```bash
streamlit run src/ui/app.py --server.port 8501
```

**Single process** runs both Streamlit and the LangGraph workflow.

**Imports:**
```python
# app.py
from agent_host.graph import build_workflow
from agent_host.config import AppConfig
from sql.audit_log import AuditLog

config = AppConfig.from_env()
audit = AuditLog(Path(".local/audit_logs"))
workflow = build_workflow(config)
```

**No HTTP layer:**
- No FastAPI server to run
- No request/response serialization
- No authentication layer needed yet
- No rate limiting infrastructure

## Trade-offs

| Aspect | Direct | FastAPI | Notes |
|--------|--------|---------|-------|
| Simplicity | ✅ High | ❌ Lower | Direct is 50% less code for MVP |
| Scalability | ❌ Single user | ✅ Many users | Can upgrade later |
| Security | ⚠️ Basic | ✅ Advanced | Auth added when needed |
| Testing | ✅ Easy | ❌ Harder | Direct calls easier to unit test |
| Latency | ✅ Low | ⚠️ Higher | No network overhead |
| Reusability | ❌ Streamlit only | ✅ Any client | FastAPI lets mobile apps call same API |

## Constraints

- **Single client:** Only one Streamlit instance can run at a time (sharing the workflow process)
- **No API versioning:** Can't have multiple API versions simultaneously
- **State sharing:** Workflow state is shared across browser sessions (acceptable; each query has independent run_id)
- **No load balancing:** Can't scale horizontally without adding backend

## Assumptions

- MVP is single-user or small team (1-5 users)
- Streamlit can be co-located with workflow (same Python process)
- Network latency is not a constraint (same process = <100ms)
- Authentication can be added later (MVP uses simple user_id setting)

## Migration Path (When Needed)

If we later need multiple clients:

1. **Extract workflow calls into FastAPI:**
   ```python
   # api/main.py
   @app.post("/ask")
   def ask(question: str, user_id: str):
       events = workflow.run(question, user_id)
       return {"events": events}
   ```

2. **Update Streamlit to use HTTP:**
   ```python
   # app.py (updated)
   import requests
   
   response = requests.post("http://localhost:8000/ask", 
                           json={"question": q, "user_id": u})
   events = response.json()["events"]
   ```

3. **No workflow code changes needed** - just changes to how Streamlit calls it.

## Future Decisions

**When to add FastAPI:**
- Need for mobile app (calls same backend)
- Multiple teams need API access
- Need separate scaling (backend runs on multiple machines)
- Need API versioning (multiple clients on different versions)
- Need authentication/authorization gateway

**Estimated effort to add FastAPI:** 1-2 days (after MVP is working).

## Success Criteria

- ✅ Streamlit successfully calls workflow directly
- ✅ Single analyst can submit queries and see results
- ✅ No HTTP calls or network overhead
- ✅ Deployment is single Python process: `streamlit run app.py`
- ✅ Audit logs are recorded correctly
- ✅ All 422 tests still pass (no workflow changes)

## Related ADRs

- [ADR 002-005](002-bigquery-dry-run-pattern.md) - Workflow components that Streamlit calls

# SQL Future Security Improvements

**Date:** 2026-07-30  
**Status:** Documented findings from architecture review (not blocking; safe for local development)  
**Audience:** Security review, architecture review, future production work

---

## Overview

This document records three minor security improvements identified during the SQL workflow architecture review. None are blockers for current local development, but all improve auditability, consistency, and clarity before production.

**Key Finding:** The current architecture is **safe and well-designed**. These improvements strengthen it further by adding traceability, validation, and immutability at key decision points.

---

## Finding 1: Column Safety Classification Audit Trail

### Current Situation

The `_infer_safety()` function in `retrieval/client.py:178–207` classifies each column's safety based on:

1. **Name-based rules** (always checked first):
   - Identifier patterns: `_ID`, `_MRN`, `_CSN`, `_EPI`, `_ACCT`, etc.
   - Sensitive patterns: `_SSN`, `_DOB`, `_NAME`, `_ADDR`, `_PHONE`, `_EMAIL`, `_ZIP`, etc.

2. **Text markers** (prose from retrieved HTML, restriction-only):
   - Keywords: "sensitive", "phi", "protected health", "do not expose", "identifiable", "restricted"
   - These can only narrow permission, never widen it

3. **Safe aggregate suffix whitelist** (name-only, never prose):
   - Suffixes: `_DATE`, `_DATETIME`, `_STATUS`, `_TYPE`, `_FLAG`, `_CODE`, `_COUNT`, `_QTY`, `_AMOUNT`, `_YEAR`, `_MONTH`, `_DAY`, `_DEPT`

4. **Default: unknown** (deny-by-default)
   - Unmatched columns stay `unknown`, which blocks SQL generation

### The Risk

If an attacker controls or poisons the retrieval index, they could:

- Add misleading prose to documentation (e.g., "This sensitive_column is safe for analysis") to try to bypass restrictions
- Manipulate heading paths or metadata chunks to influence column extraction
- Rely on substring matching to craft column names or descriptions that confuse the classifier

### Current Mitigations

✅ **Deny-by-default enforcement:**

```python
# retrieval/client.py:200–207
if any(marker in text_lower for marker in _SENSITIVE_TEXT_MARKERS):
    return "sensitive"  # Prose can only restrict

if _SAFE_AGGREGATE_SUFFIXES.search(name_upper):
    return "safe_aggregate"  # Only from name, never prose

return "unknown"  # Default case
```

✅ **Enforced at plan safety gate:**

```python
# sql/planning.py:150–158
for reference in projected_refs:
    column = _resolve_column(reference, tables)
    if column is not None and column.safety in {"identifier", "sensitive", "unknown"}:
        violations.append(
            _violation(
                "unsafe_projection",
                "Restricted columns cannot be projected or grouped.",
                ...
            )
        )
```

✅ **Re-enforced at SQL validation:**

```python
# sql/validation.py (lines 250+)
# Column references are re-validated against schema snapshot
```

✅ **Early check in query planning:**

```python
# sql_nodes.py:49–50
if not raw_snapshot or snapshot.has_unknown_safety():
    return {"answer": "I couldn't find enough schema information..."}
```

### Gap: No Audit Trail

The column's `source_evidence` field is set but not validated against the text/logic that produced the classification. This makes security review difficult:

- Cannot trace *why* a column was classified as `safe_aggregate` vs. `unknown`
- Cannot audit which text markers or name patterns triggered a classification
- Cannot distinguish between "unknown because no evidence" vs. "unknown because evidence was ambiguous"

### Recommended Improvements

#### Option A: Add Classification Audit Trail (Recommended)

Create a `ColumnSafetyAudit` dataclass to record the reasoning for each classification:

```python
@dataclass
class ColumnSafetyAudit:
    """Audit trail for column safety classification decisions."""
    column_name: str
    inferred_safety: str  # identifier | sensitive | safe_aggregate | unknown
    source_chunk_id: str
    classification_reason: str  # enum: name_match_identifier | text_markers_sensitive | name_suffix_safe_aggregate | no_classification_matched
    name_match: str | None = None  # regex pattern or description that matched
    text_markers_found: list[str] = field(default_factory=list)  # which markers were present
```

Modify `_infer_safety()` to return `(safety: str, audit: ColumnSafetyAudit)` instead of just the safety string.

**Benefit:**
- Full audit trail: every classification decision is logged with reason
- Security review: can audit decisions post-hoc
- Traceability: can debug schema evidence issues
- No behavioral change: deny-by-default logic unchanged

**Implementation location:** `retrieval/client.py:178–207`

#### Option B: Validate Repair Hints Before Passing to Model

Combine with Finding 2 improvements (below).

---

## Finding 2: Repair Hint Validation

### Current Situation

When SQL validation fails and repair is needed, the hint is passed to Claude:

```python
# sql_nodes.py:195–199
result = generate_sql(
    plan,
    cfg,
    budget,
    repair_hint=validation.repair_hint or validation.reason,
    candidate_sql=candidate,
)
```

The repair hint comes from `SqlValidationResult.repair_hint`, which is set by the validator during validation. Possible values:

- A string like `"Fix ungrouped_projection: add GROUP BY to all columns"`
- None (no specific repair guidance)
- Empty string (edge case)

### The Risk

If a validator gate has a bug and produces a misleading or malformed `repair_hint`, Claude could:

- Misinterpret the hint and generate SQL that passes validation by accident (correct result, wrong reason)
- Spend tokens trying to understand a nonsensical hint
- Generate SQL that violates the approved plan's intent

**Examples of bad hints:**
- `"Fix ungrouped_projection: add GROUP BY to all columns"` (vague, too generic)
- `"expected 'id', got 'user_name'"` (confusing, not actionable)
- Empty string or whitespace-only string (unhelpful)
- Very long hint (>500 chars) suggesting malformed output from validator

### Current Safeguards

✅ **Hash-based loop detection:**

```python
# sql_nodes.py:208–216
input_hash = _sql_hash(candidate)
output_hash = _sql_hash(result.sql)
previous_hashes = {str(item.get("output_sql_hash")) for item in state.get("repair_history", [])}
if output_hash == input_hash or output_hash in previous_hashes:
    trace.record("fix_sql.loop_detected")
    return {"answer": "SQL repair stopped because it repeated an earlier candidate."}
```

✅ **Budget enforcement:**

```python
# sql_nodes.py:296–298
next_repair_count = repair_count + 1
if result.is_repairable and next_repair_count < budget.max_sql_repairs:
    # route to fix_sql
```

✅ **Re-validation:**

```python
# sql_nodes.py:279
result = validate_sql(sql, declared_tables, snapshot, approved)
```

These prevent infinite loops and silent acceptance of broken SQL, but a malformed hint could still waste tokens or confuse the model.

### Gap: No Validation of Hint Quality

Hints are trusted without validation. If the validator produces a malformed hint, it's passed directly to Claude.

### Recommended Improvements

#### Option A: Centralized, Enum-Driven Repair Guidance (Recommended)

Replace free-form `repair_hint` strings with a well-known set of codes, each with documented guidance:

```python
# sql/validation.py

REPAIR_GUIDANCE = {
    "ungrouped_projection": (
        "The SELECT list contains non-aggregated columns that are not in GROUP BY. "
        "Add all non-aggregated columns to the GROUP BY clause."
    ),
    "non_aggregate_sql": (
        "The query must return aggregate results (SUM, COUNT, etc.), not row-level data. "
        "Use aggregation functions for all selected columns."
    ),
    "unknown_or_ambiguous_column": (
        "A column name is ambiguous or not found in the schema. "
        "Verify the column name and table prefix are correct."
    ),
    "declared_table_mismatch": (
        "The declared tables do not match the tables referenced in the SQL. "
        "Update the table list or fix the SQL to match declared tables."
    ),
    "unknown_safety_column": (
        "A column's safety classification is unknown. "
        "Only columns marked safe_aggregate, identifier, or safe for your query are allowed."
    ),
    # ... more codes as needed ...
}
```

When returning a `SqlValidationResult`, use the code as the key:

```python
# sql/validation.py (in validate_sql function)
if not result.allowed and result.reason in REPAIR_GUIDANCE:
    result = SqlValidationResult(
        allowed=False,
        reason=result.reason,
        repair_hint=REPAIR_GUIDANCE[result.reason],  # Use guidance, not ad-hoc hint
        # ... rest of fields ...
    )
```

**Benefits:**
- ✅ Centralized, auditable repair guidance
- ✅ Consistent hints across all validation runs
- ✅ Easy to update guidance without changing validator logic
- ✅ Prevents ad-hoc, malformed hints
- ✅ Testable: each code maps to known guidance

**Implementation location:** `sql/validation.py`

#### Option B: Sanitize and Validate Repair Hints Before Passing to Model

Add a `_sanitize_repair_hint()` function that:

1. Rejects hints longer than 200 characters (likely malformed)
2. Rejects hints that don't start with a likely action or noun (e.g., "add", "remove", "fix", "missing", etc.)
3. Falls back to `reason` if hint is suspicious
4. Logs when sanitization was applied for debugging

```python
# sql_nodes.py

def _sanitize_repair_hint(reason: str | None, hint: str | None) -> str | None:
    """Ensure repair hint is safe and actionable."""
    # Prefer hint if available and valid, else reason
    candidates = [h for h in [hint, reason] if h and len(h.strip()) <= 200]
    
    if not candidates:
        return None
    
    selected = candidates[0] if hint and len(hint.strip()) <= 200 else candidates[-1]
    
    # Sanity check: first word should be a likely action or noun
    first_word = selected.split()[0].lower().rstrip(',:.')
    likely_actions = {
        'add', 'remove', 'change', 'fix', 'use', 'include', 'exclude',
        'group', 'partition', 'select', 'filter', 'join', 'order',
        'missing', 'invalid', 'unsafe', 'unknown', 'ungrouped', 'ambiguous',
    }
    
    if not any(first_word.startswith(action) for action in likely_actions):
        # Fallback to reason
        return reason.strip() if reason else None
    
    return selected.strip()
```

**Benefits:**
- ✅ Simple, defensive check without changing validator
- ✅ Prevents obvious malformed hints from reaching model
- ✅ Can be applied immediately

**Implementation location:** `sql_nodes.py:177–203` (fix_sql_node)

#### Option C: Validate Hint Format at Model

Add validation to `SqlValidationResult` to ensure `repair_hint` is well-formed:

```python
# sql/models.py

class SqlValidationResult(BaseModel):
    # ... existing fields ...
    repair_hint: str | None = None
    
    @model_validator(mode="after")
    def validate_repair_hint_state(self) -> Self:
        """Ensure repair_hint is well-formed if repair is possible."""
        if not self.allowed:
            # Invalid SQL must have either reason or repair_hint
            if self.reason is None and self.repair_hint is None:
                raise ValueError(
                    "invalid SQL must have reason or repair_hint (or both)"
                )
            # If repair_hint exists, it must be non-empty and concise
            if self.repair_hint is not None:
                hint = self.repair_hint.strip()
                if not hint or len(hint) > 500:
                    raise ValueError(
                        "repair_hint must be non-empty and under 500 characters"
                    )
        return self
```

**Benefits:**
- ✅ Type-safe validation at model boundary
- ✅ Catches invalid hints at construction time
- ✅ Prevents bad hints from ever entering state

**Implementation location:** `sql/models.py:288+`

---

## Finding 3: Budget Loading in Routing Function

### Current Situation

The `_route_from_validate_sql()` function loads the budget from environment when making the repair decision:

```python
# graph.py:129–150
def _route_from_validate_sql(state: AgentState) -> str:
    # ...
    budget = budget_from_env()  # <-- Loaded here, in routing function
    
    if result.allowed:
        return "execution_not_configured"
    
    repair_count = state.get("repair_count", 0)
    if result.is_repairable and repair_count < budget.max_sql_repairs:
        return "fix_sql"
    
    return "result_safety"
```

Meanwhile, earlier nodes load budget from runtime context:

```python
# sql_nodes.py:38–44
def query_plan_node(
    state: AgentState,
    runtime: Runtime[AgentContext] | None = None,
) -> dict:
    budget = runtime.context.budget if runtime is not None else budget_from_env()
```

### The Risk

If the environment changes between invocations in the same request (unlikely but possible):

- An earlier invocation might have had `MAX_SQL_REPAIRS=3`
- A later routing decision might have `MAX_SQL_REPAIRS=1`
- The repair budget could be treated inconsistently across the request lifecycle

This is a **low-risk but high-visibility code smell** because:

1. Budget should be immutable per request
2. It's loaded fresh every time, not cached
3. It's loaded in a routing function (late in the request lifecycle), not at request initialization

### Current Mitigations

✅ **Budget is typically set at startup:**

```python
# agent_host/budget.py:105–145
def budget_from_env() -> ExecutionBudget:
    """Build a budget from environment variable overrides, falling back to defaults."""
```

✅ **Budget is thread-scoped in most nodes:**

```python
# agent_host/graph.py:318
context = AgentContext(budget=budget_from_env())
_thread_contexts[tid] = context
```

✅ **Enforcement is consistent:** All budget checks use the same `ExecutionBudget` class.

### Gap: Budget is Not Immutable Per Request

Budget is a mutable dataclass loaded fresh from environment at each decision point, not as a snapshot at request start.

### Recommended Improvements

#### Option A: Persist Budget in State (Recommended)

Store the budget as a snapshot in the state at request initialization:

```python
# agent_host/state.py

class AgentState(TypedDict, total=False):
    """Stateful agent pipeline input/output."""
    # ... existing fields ...
    
    execution_budget: dict | None  # serialized ExecutionBudget snapshot
```

Capture at request start:

```python
# agent_host/graph.py:ask() function

initial_state = make_initial_state(question, run_id=run_id, started_at=started_at)
config: RunnableConfig = {"configurable": {"thread_id": tid}}
context = AgentContext(budget=budget_from_env())
_thread_contexts[tid] = context

# Add budget snapshot to state
initial_state["execution_budget"] = context.budget.model_dump()

result = graph.invoke(initial_state, config=config, context=context)
```

Update routing to use state:

```python
# graph.py:129–150

def _route_from_validate_sql(state: AgentState) -> str:
    # ...
    
    # Load budget from state (immutable snapshot)
    budget_data = state.get("execution_budget")
    if budget_data:
        budget = ExecutionBudget(**budget_data)
    else:
        budget = budget_from_env()  # Fallback
    
    # ... rest of routing logic ...
```

**Benefits:**
- ✅ Budget is immutable per request
- ✅ No reload from environment during request lifecycle
- ✅ Tracing shows exactly what budget was used
- ✅ All nodes see the same budget

**Implementation location:** `agent_host/state.py`, `agent_host/graph.py:ask()`, `graph.py:_route_from_validate_sql()`

#### Option B: Make ExecutionBudget Frozen (Immutable)

Mark the dataclass as frozen:

```python
# agent_host/budget.py

from dataclasses import dataclass

@dataclass(frozen=True)  # <-- Immutable
class ExecutionBudget:
    """Per-request execution budget. All limits live here, not in AppConfig.
    
    Immutable: frozen=True prevents accidental mutation.
    """
    max_rounds: int = 3
    max_model_calls: int = 4
    # ... rest of fields ...
```

Add a fingerprint for consistency checking:

```python
def fingerprint(self) -> str:
    """One-way hash of budget limits for consistency checking in tests."""
    import hashlib
    import json
    
    payload = json.dumps(
        {k: getattr(self, k) for k in ['max_rounds', 'max_model_calls', 'max_sql_repairs', 'max_wall_seconds']},
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:16]
```

**Benefits:**
- ✅ Budget cannot be mutated after creation
- ✅ Clear intent: budget is set at request start
- ✅ Fingerprint enables consistency tests

**Implementation location:** `agent_host/budget.py:29+`

#### Option C: Add Budget Validation Helper

Create a `_get_request_budget()` helper that centralizes loading logic and adds validation:

```python
# graph.py

def _get_request_budget(state: AgentState) -> ExecutionBudget:
    """Get the budget for this request, with validation.
    
    The budget should be set at request start and never change during routing.
    If it's missing, reload from environment (fallback) and warn.
    """
    budget_data = state.get("execution_budget")
    if budget_data:
        try:
            return ExecutionBudget(**budget_data)
        except (TypeError, ValueError):
            # Corrupted state; reload from environment
            import logging
            logging.warning("execution_budget in state is invalid; reloading from environment")
            return budget_from_env()
    
    # Missing budget; reload from environment (shouldn't happen in normal flow)
    return budget_from_env()


# Use everywhere:
def _route_from_validate_sql(state: AgentState) -> str:
    # ...
    budget = _get_request_budget(state)
    # ...
```

**Benefits:**
- ✅ Centralized budget retrieval logic
- ✅ Validation and fallback handling
- ✅ Easy to audit all budget decisions

**Implementation location:** `graph.py`

---

## Summary: Implementation Priority

| Finding | Severity | Recommended Fix | Effort | Benefit | Blocks Local Dev? |
|---------|----------|---|---|---|---|
| **1. Column safety audit trail** | Low | Option A: Add `ColumnSafetyAudit` dataclass + trace logging | 1–2h | Security audit, transparency | No |
| **2. Repair hint validation** | Low | Option A: Centralized `REPAIR_GUIDANCE` enum | 1h | Consistency, debuggability | No |
| **3. Budget immutability** | Low | Option A: Persist in state at request start | 1.5h | Clarity, immutability | No |

### Order for Implementation (if doing all three)

1. **Finding 2** (easiest, highest immediate value): Add `REPAIR_GUIDANCE` enum. This is a simple refactor of existing logic.
2. **Finding 1** (medium, highest auditability): Add audit trail for schema evidence. Enables security review.
3. **Finding 3** (medium, highest clarity): Persist budget in state. Makes request lifecycle clearer.

### Notes

- **None of these are blockers** for local development. The current architecture is safe and well-designed.
- All three improve **auditability and traceability** more than they fix bugs.
- Recommended for implementation **before production**, especially Finding 1 (audit trail for schema evidence).
- Each can be implemented independently; they do not depend on each other.

---

## References

- ADR 001: Graph architecture and constraints
- `retrieval/client.py`: Column safety inference
- `sql/models.py`: Validation result contracts
- `sql_nodes.py`: Repair logic
- `agent_host/graph.py`: Routing and budget management
- `agent_host/budget.py`: Budget model and environment loading

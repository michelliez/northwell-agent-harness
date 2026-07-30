"""SQL workflow nodes: query planning, generation, validation, and execution stub.

BigQuery execution is not configured. Validated SQL is returned as a draft
with execution_status='not_configured'. No SQL is ever run.
"""

from __future__ import annotations

import hashlib
import json

from langgraph.runtime import Runtime

from agent_host.budget import budget_from_env
from agent_host.config import get_config
from agent_host.state import AgentContext, AgentState
from agent_host.trace_logger import TraceLogger
from sql.compiler import UnsupportedPlanError, plan_to_bigquery_sql
from sql.cost_gate import cost_execution_config_from_env, evaluate_cost_execution
from sql.generation import generate_sql
from sql.models import (
    ApprovedQueryPlan,
    CompiledQuery,
    DryRunResult,
    PermissionScope,
    QueryPlanAST,
    RepairAttempt,
    SchemaSnapshot,
    SqlValidationResult,
)
from sql.planning import (
    permission_scope_from_snapshot,
    propose_query_plan,
    validate_query_plan,
)
from sql.validation import validate_sql


def query_plan_node(
    state: AgentState,
    runtime: Runtime[AgentContext] | None = None,
) -> dict:
    """Ask Claude for a typed plan constrained by host-owned schema scope."""
    cfg = get_config()
    budget = runtime.context.budget if runtime is not None else budget_from_env()
    trace = _open_trace(state, cfg)
    question = state.get("question", "")
    raw_snapshot = state.get("schema_snapshot")

    if not raw_snapshot:
        trace.record("query_plan.no_schema_snapshot")
        return {
            "query_plan": None,
            "answer": (
                "I couldn't find enough schema information to plan a SQL query for this question. "
                "Please ask about a specific documented table."
            ),
        }

    snapshot = SchemaSnapshot.model_validate(raw_snapshot)
    scope = permission_scope_from_snapshot(snapshot)
    citations = [
        str(chunk["chunk_id"])
        for chunk in state.get("retrieved_chunks", [])
        if chunk.get("chunk_id")
    ]
    try:
        proposed = propose_query_plan(
            question,
            scope,
            citations,
            cfg,
            budget,
        )
    except Exception as exc:
        trace.record("query_plan.error", error=type(exc).__name__)
        return {
            "permission_scope": scope.model_dump(),
            "query_plan": None,
            "answer": "I couldn't create a structured query plan for this request.",
        }

    trace.record("query_plan.built", table_count=len(proposed.tables))
    return {
        "permission_scope": scope.model_dump(),
        "query_plan": proposed.model_dump(),
        "plan_validation": None,
        "approved_plan": None,
    }


def plan_safety_node(state: AgentState) -> dict:
    """Deterministically authorize a proposed plan before SQL generation."""
    cfg = get_config()
    trace = _open_trace(state, cfg)
    raw_plan = state.get("query_plan")
    raw_scope = state.get("permission_scope")

    if not raw_plan or not raw_scope:
        trace.record("plan_safety.no_plan")
        return {"answer": "I stopped because no query plan was available."}

    proposed = QueryPlanAST.model_validate(raw_plan)
    scope = PermissionScope.model_validate(raw_scope)
    citations = {
        str(chunk["chunk_id"])
        for chunk in state.get("retrieved_chunks", [])
        if chunk.get("chunk_id")
    }
    validation = validate_query_plan(
        state.get("question", ""),
        proposed,
        scope,
        citations,
    )
    if not validation.allowed:
        codes = ", ".join(violation.code for violation in validation.violations)
        trace.record("plan_safety.rejected", violations=codes)
        return {
            "plan_validation": validation.model_dump(),
            "approved_plan": None,
            "answer": f"I couldn't approve the proposed query plan: {codes}.",
        }

    trace.record(
        "plan_safety.approved",
        tables=proposed.tables,
        plan_hash=validation.approved_plan.plan_hash if validation.approved_plan else None,
    )
    return {
        "plan_validation": validation.model_dump(),
        "approved_plan": (
            validation.approved_plan.model_dump() if validation.approved_plan else None
        ),
    }


def write_sql_node(
    state: AgentState,
    _runtime: Runtime[AgentContext] | None = None,
) -> dict:
    """Compile an approved plan deterministically."""
    cfg = get_config()
    trace = _open_trace(state, cfg)
    raw_plan = state.get("approved_plan")
    if not raw_plan:
        trace.record("generate_sql.no_plan")
        return {"answer": "I stopped because no query plan was available for SQL generation."}

    plan = ApprovedQueryPlan.model_validate(raw_plan)

    try:
        compiled = plan_to_bigquery_sql(plan)
    except UnsupportedPlanError as compiler_error:
        trace.record("write_sql.compiler_unsupported", reason=compiler_error.code)
        return {
            "answer": (
                "The approved query plan uses a feature the deterministic SQL "
                f"compiler does not support yet: {compiler_error.code}."
            )
        }

    trace.record(
        "generate_sql.completed",
        refused=False,
        has_sql=True,
        source=compiled.source,
    )
    return {
        "compiled_query": compiled.model_dump(),
        "candidate_sql": compiled.sql,
        "query_parameters": [item.model_dump() for item in compiled.parameters],
        "validation_result": None,
    }


def fix_sql_node(
    state: AgentState,
    runtime: Runtime[AgentContext] | None = None,
) -> dict:
    """Repair SQL without permitting changes to the approved plan."""
    cfg = get_config()
    budget = runtime.context.budget if runtime is not None else budget_from_env()
    trace = _open_trace(state, cfg)
    raw_plan = state.get("approved_plan")
    candidate = state.get("candidate_sql")
    raw_validation = state.get("validation_result")
    if not raw_plan or not candidate or not raw_validation:
        trace.record("fix_sql.missing_state")
        return {"answer": "I stopped because SQL repair state was incomplete."}

    plan = ApprovedQueryPlan.model_validate(raw_plan)
    validation = SqlValidationResult.model_validate(raw_validation)
    try:
        result = generate_sql(
            plan,
            cfg,
            budget,
            repair_hint=validation.repair_hint or validation.reason,
            candidate_sql=candidate,
        )
    except Exception as exc:
        trace.record("fix_sql.error", error=type(exc).__name__)
        return {"answer": "I encountered an internal error while repairing SQL."}
    if result.sql is None:
        return {"answer": "The SQL repair model could not produce a candidate."}

    input_hash = _sql_hash(candidate)
    output_hash = _sql_hash(result.sql)
    previous_hashes = {str(item.get("output_sql_hash")) for item in state.get("repair_history", [])}
    if output_hash == input_hash or output_hash in previous_hashes:
        trace.record("fix_sql.loop_detected")
        return {"answer": "SQL repair stopped because it repeated an earlier candidate."}

    attempt = max(1, state.get("repair_count", 1))
    compiled = CompiledQuery(
        sql=result.sql,
        parameters=plan.plan.parameters,
        plan_hash=plan.plan_hash,
        compiler_version="claude-repair-v1",
        source="claude_repair",
    )
    trace.record("fix_sql.completed", attempt=attempt)
    return {
        "compiled_query": compiled.model_dump(),
        "candidate_sql": compiled.sql,
        "query_parameters": [item.model_dump() for item in compiled.parameters],
        "validation_result": None,
        # Through RepairAttempt rather than a bare dict: the model already
        # declares the hash format and attempt floor, and it was being bypassed.
        "repair_history": [
            RepairAttempt(
                attempt=attempt,
                input_sql_hash=input_hash,
                output_sql_hash=output_hash,
                error_code=validation.reason or "validation_error",
            ).model_dump()
        ],
    }


def validate_sql_node(
    state: AgentState,
    runtime: Runtime[AgentContext] | None = None,
) -> dict:
    """Run deterministic SQLGlot validation against the schema snapshot.

    Sets repair_count and repair_hint on invalid-but-repairable results so
    the conditional edge can route back to generate_sql.
    """
    cfg = get_config()
    trace = _open_trace(state, cfg)
    sql = state.get("candidate_sql")
    raw_plan = state.get("approved_plan")
    repair_count = state.get("repair_count", 0)
    budget = runtime.context.budget if runtime is not None else budget_from_env()

    if not sql:
        trace.record("validate_sql.no_sql")
        return {
            "validation_result": None,
            "answer": "I stopped because no SQL was generated.",
        }

    snapshot = SchemaSnapshot()
    declared_tables: list[str] = []
    if raw_plan:
        plan = ApprovedQueryPlan.model_validate(raw_plan)
        snapshot = plan.permission_scope.schema_snapshot
        declared_tables = plan.plan.tables

    approved = ApprovedQueryPlan.model_validate(raw_plan) if raw_plan else None
    result = validate_sql(sql, declared_tables, snapshot, approved)
    trace.record(
        "validate_sql.completed",
        allowed=result.allowed,
        reason=result.reason,
        is_repairable=result.is_repairable,
    )

    validation_dict = result.model_dump()

    if result.allowed:
        return {
            "validation_result": validation_dict,
            "repair_hint": None,
        }

    # Invalid SQL
    next_repair_count = repair_count + 1
    if result.is_repairable and next_repair_count < budget.max_sql_repairs:
        trace.record("validate_sql.routing_to_repair", repair_count=next_repair_count)
        return {
            "validation_result": validation_dict,
            "repair_count": next_repair_count,
            "repair_hint": result.repair_hint,
        }

    # Not repairable or budget exhausted
    trace.record("validate_sql.failed_final", reason=result.reason)
    violations = [v.code for v in result.violations]
    return {
        "validation_result": validation_dict,
        "answer": (
            f"The generated SQL failed safety validation and could not be repaired. "
            f"Violation: {result.reason or ', '.join(violations)}. "
            "Please rephrase your question."
        ),
    }


def execution_not_configured_node(state: AgentState) -> dict:
    """Return the validated SQL draft; note that execution is not configured.

    This is the terminal SQL node. No SQL is executed. BigQuery integration
    is future work (see sql/bigquery_adapter.py).
    """
    cfg = get_config()
    trace = _open_trace(state, cfg)
    sql = state.get("candidate_sql") or ""
    raw_validation = state.get("validation_result")

    notes: list[str] = []
    if raw_validation:
        result = SqlValidationResult.model_validate(raw_validation)
        notes = result.notes

    answer = (
        f"Here is the validated SQL draft:\n\n```sql\n{sql}\n```\n\n"
        "Note: SQL execution is not configured in this environment. "
        "This draft requires authorized review before execution.\n"
    )
    if notes:
        answer += f"\nValidation notes: {'; '.join(notes)}"
    parameters = state.get("query_parameters") or []
    if parameters:
        answer += (
            "\n\nNamed query parameters:\n\n```json\n"
            f"{json.dumps(parameters, indent=2, ensure_ascii=False)}\n```"
        )

    trace.record("execution_not_configured", sql_length=len(sql))

    return {
        "execution_status": "not_configured",
        "answer": answer,
    }


def cost_execution_gate_node(state: AgentState) -> dict:
    """Authorize execution from trusted dry-run evidence.

    The graph does not route here until a real BigQuery dry-run adapter exists.
    Tests and a future adapter can invoke it with serialized ``dry_run_result``.
    """
    cfg = get_config()
    trace = _open_trace(state, cfg)
    raw_query = state.get("compiled_query")
    raw_plan = state.get("approved_plan")
    raw_dry_run = state.get("dry_run_result")
    if not raw_query or not raw_plan or not raw_dry_run:
        trace.record("cost_execution_gate.missing_evidence")
        return {
            "cost_gate_result": None,
            "approval_token": None,
            "answer": "Execution was not approved because trusted dry-run evidence is missing.",
        }

    try:
        gate_config = cost_execution_config_from_env()
        decision = evaluate_cost_execution(
            CompiledQuery.model_validate(raw_query),
            ApprovedQueryPlan.model_validate(raw_plan),
            DryRunResult.model_validate(raw_dry_run),
            gate_config,
        )
    except (RuntimeError, ValueError) as exc:
        trace.record("cost_execution_gate.config_error", error=type(exc).__name__)
        return {
            "cost_gate_result": None,
            "approval_token": None,
            "answer": f"Execution was not approved because cost controls are unavailable: {exc}",
        }

    if not decision.allowed:
        codes = ", ".join(item.code for item in decision.violations)
        trace.record("cost_execution_gate.rejected", violations=codes)
        return {
            "cost_gate_result": decision.model_dump(mode="json"),
            "approval_token": None,
            "answer": f"Execution was not approved by the cost gate: {codes}.",
        }

    trace.record(
        "cost_execution_gate.approved",
        total_bytes_processed=decision.total_bytes_processed,
        max_bytes=decision.max_bytes,
        expires_at=decision.expires_at.isoformat() if decision.expires_at else None,
    )
    return {
        "cost_gate_result": decision.model_dump(mode="json"),
        "approval_token": decision.approval_token,
    }


def _sql_hash(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()


def _open_trace(state: AgentState, cfg) -> TraceLogger:
    run_id = state.get("run_id") or "unknown"
    trace_file = state.get("trace_file")
    if trace_file:
        trace_dir = str(trace_file).rsplit("/", 1)[0].rsplit("\\", 1)[0]
    else:
        trace_dir = str(cfg.trace_dir)
    return TraceLogger(
        trace_dir=trace_dir,
        run_id=run_id,
        content_mode=cfg.trace_content_mode,
    )

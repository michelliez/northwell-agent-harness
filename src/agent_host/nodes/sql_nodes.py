"""SQL workflow nodes: query planning, generation, validation, and execution stub.

BigQuery execution is not configured. Validated SQL is returned as a draft
with execution_status='not_configured'. No SQL is ever run.
"""

from __future__ import annotations

from langgraph.runtime import Runtime

from agent_host import failure_messages
from agent_host.budget import budget_from_env
from agent_host.config import get_config
from agent_host.conversation import turns_to_messages
from agent_host.state import AgentContext, AgentState
from agent_host.trace_logger import TraceLogger
from sql.compiler import UnsupportedPlanError, plan_to_bigquery_sql
from sql.cost_gate import cost_execution_config_from_env, evaluate_cost_execution
from sql.guidance import describe_violations
from sql.models import (
    ApprovedQueryPlan,
    CompiledQuery,
    DryRunResult,
    PermissionScope,
    PlanValidationResult,
    QueryPlanAST,
    SchemaSnapshot,
    SqlValidationResult,
)
from sql.planning import (
    permission_scope_from_snapshot,
    propose_query_plan,
    repair_feedback,
    validate_query_plan,
)
from sql.validation import validate_sql

# One violation-fed retry, then the rejection goes to the user. The repaired
# plan re-enters the same deterministic authorizer; repair never approves.
MAX_PLAN_REPAIRS = 1


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

    if not raw_snapshot or not raw_snapshot.get("tables"):
        # A snapshot with zero tables is as unusable as no snapshot: every
        # plan the model could propose would fail table_out_of_scope.
        trace.record("query_plan.no_schema_snapshot")
        return {
            "query_plan": None,
            "answer": failure_messages.SQL_NO_SCHEMA_EVIDENCE,
        }

    snapshot = SchemaSnapshot.model_validate(raw_snapshot)
    scope = permission_scope_from_snapshot(snapshot)
    citations = [
        str(chunk["chunk_id"])
        for chunk in state.get("retrieved_chunks", [])
        if chunk.get("chunk_id")
    ]

    feedback = _repair_feedback_from_state(state)
    if feedback is not None:
        trace.record("query_plan.repair_attempted")

    history = turns_to_messages(state.get("conversation_turns") or [])

    try:
        proposed = propose_query_plan(
            question,
            scope,
            citations,
            cfg,
            budget,
            feedback=feedback,
            history=history or None,
            trace=trace,
        )
    except Exception as exc:
        trace.record("query_plan.error", error=type(exc).__name__)
        return {
            "permission_scope": scope.model_dump(),
            "query_plan": None,
            "answer": failure_messages.SQL_PLANNER_UNAVAILABLE,
        }

    trace.record("query_plan.built", table_count=len(proposed.tables))
    return {
        "permission_scope": scope.model_dump(),
        "query_plan": proposed.model_dump(),
        "plan_validation": None,
        "approved_plan": None,
    }


def _repair_feedback_from_state(state: AgentState) -> str | None:
    """Host-composed feedback when the previous plan was rejected, else None."""
    raw_validation = state.get("plan_validation")
    raw_plan = state.get("query_plan")
    if not raw_validation or not raw_plan or raw_validation.get("allowed"):
        return None
    return repair_feedback(
        QueryPlanAST.model_validate(raw_plan),
        PlanValidationResult.model_validate(raw_validation),
    )


def plan_safety_node(state: AgentState) -> dict:
    """Deterministically authorize a proposed plan before SQL generation."""
    cfg = get_config()
    trace = _open_trace(state, cfg)
    raw_plan = state.get("query_plan")
    raw_scope = state.get("permission_scope")

    if not raw_plan or not raw_scope:
        trace.record("plan_safety.no_plan")
        return {"answer": failure_messages.SQL_PIPELINE_STATE_MISSING}

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
        repair_count = state.get("plan_repair_count", 0)
        trace.record(
            "plan_safety.rejected",
            violations=", ".join(v.code for v in validation.violations),
            details=[{"code": v.code, "evidence": v.evidence} for v in validation.violations],
            repair_attempts_used=repair_count,
        )
        if repair_count < MAX_PLAN_REPAIRS:
            # No answer: the graph routes back to query_plan for one
            # violation-fed repair attempt.
            return {
                "plan_validation": validation.model_dump(),
                "approved_plan": None,
                "plan_repair_count": repair_count + 1,
            }
        return {
            "plan_validation": validation.model_dump(),
            "approved_plan": None,
            "answer": (
                f"I couldn't approve the proposed query plan. "
                f"{describe_violations(validation.violations)}"
            ),
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
        return {"answer": failure_messages.SQL_PIPELINE_STATE_MISSING}

    plan = ApprovedQueryPlan.model_validate(raw_plan)

    try:
        compiled = plan_to_bigquery_sql(plan)
    except UnsupportedPlanError as compiler_error:
        trace.record("write_sql.compiler_unsupported", reason=compiler_error.code)
        return {
            "answer": failure_messages.SQL_COMPILER_UNSUPPORTED.format(code=compiler_error.code)
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


def validate_sql_node(
    state: AgentState,
    _runtime: Runtime[AgentContext] | None = None,
) -> dict:
    """Run deterministic SQLGlot validation against the schema snapshot.

    Failure is terminal: the candidate is deterministic compiler output, so a
    validation failure is a compiler/validator version skew to surface, not
    something a model rewrite could fix (the plan_sql_mismatch gate would
    reject any rewrite that differs from the compiled plan anyway).
    """
    cfg = get_config()
    trace = _open_trace(state, cfg)
    sql = state.get("candidate_sql")
    raw_plan = state.get("approved_plan")

    if not sql:
        trace.record("validate_sql.no_sql")
        return {
            "validation_result": None,
            "answer": failure_messages.SQL_PIPELINE_STATE_MISSING,
        }

    snapshot = SchemaSnapshot()
    declared_tables: list[str] = []
    if raw_plan:
        plan = ApprovedQueryPlan.model_validate(raw_plan)
        snapshot = plan.permission_scope.schema_snapshot
        declared_tables = plan.plan.tables

    approved = ApprovedQueryPlan.model_validate(raw_plan) if raw_plan else None
    try:
        result = validate_sql(sql, declared_tables, snapshot, approved)
    except Exception as exc:
        trace.record("validate_sql.error", error=type(exc).__name__)
        return {
            "validation_result": None,
            "answer": failure_messages.SQL_PIPELINE_STATE_MISSING,
        }
    trace.record(
        "validate_sql.completed",
        allowed=result.allowed,
        reason=result.reason,
    )

    validation_dict = result.model_dump()

    if result.allowed:
        return {"validation_result": validation_dict}

    trace.record("validate_sql.failed_final", reason=result.reason)
    return {
        "validation_result": validation_dict,
        "answer": (
            f"The generated SQL failed safety validation. {describe_violations(result.violations)}"
        ),
    }


def execution_not_configured_node(state: AgentState) -> dict:
    """Record that execution is not configured; the SQL is in candidate_sql.

    The UI renders the SQL code block, the 'not configured' notice, and any
    named parameters from the structured generated_sql, execution_status, and
    query_parameters response fields. Writing the SQL block into answer would
    duplicate it. Only validation notes — prose the UI has no other source for
    — belong in answer.
    """
    cfg = get_config()
    trace = _open_trace(state, cfg)
    sql = state.get("candidate_sql") or ""
    raw_validation = state.get("validation_result")

    notes: list[str] = []
    if raw_validation:
        result = SqlValidationResult.model_validate(raw_validation)
        notes = result.notes

    answer = f"Validation notes: {'; '.join(notes)}" if notes else ""

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

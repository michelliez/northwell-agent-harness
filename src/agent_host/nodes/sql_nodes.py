"""SQL workflow nodes: query planning, generation, validation, and execution stub.

BigQuery execution is not configured. Validated SQL is returned as a draft
with execution_status='not_configured'. No SQL is ever run.
"""

from __future__ import annotations

from langgraph.runtime import Runtime

from agent_host.budget import budget_from_env
from agent_host.config import get_config
from agent_host.state import AgentContext, AgentState
from agent_host.trace_logger import TraceLogger
from sql.generation import generate_sql
from sql.models import QueryPlan, SchemaSnapshot, SqlValidationResult
from sql.validation import validate_sql


def query_plan_node(state: AgentState) -> dict:
    """Build a minimal QueryPlan from the schema snapshot in state.

    Unknown-safety columns are recorded for generation guidance. They do not
    block the table-level plan because the deterministic SQL validator checks
    only columns actually referenced by the generated statement.
    """
    cfg = get_config()
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
    safety_notes: list[str] = []
    if snapshot.has_unknown_safety():
        safety_notes.append(
            "Some columns have unknown safety classifications; clarification may be needed."
        )

    plan = QueryPlan(
        tables=[t.name for t in snapshot.tables],
        purpose=question,
        schema_snapshot=snapshot,
        safety_notes=safety_notes,
    )
    trace.record("query_plan.built", table_count=len(plan.tables))

    return {"query_plan": plan.model_dump()}


def plan_safety_node(state: AgentState) -> dict:
    """Check that an evidence-backed query plan exists before SQL generation.

    Column-level safety cannot be decided at this stage because no SQL exists
    yet. In particular, a safe ``COUNT(*)`` references no columns. The
    deterministic validator applies unknown, sensitive, and identifier rules
    to the columns the generated SQL actually uses.
    """
    cfg = get_config()
    trace = _open_trace(state, cfg)
    raw_plan = state.get("query_plan")

    if not raw_plan:
        trace.record("plan_safety.no_plan")
        return {"answer": "I stopped because no query plan was available."}

    plan = QueryPlan.model_validate(raw_plan)
    unknown_column_count = sum(
        column.safety == "unknown"
        for table in plan.schema_snapshot.tables
        for column in table.columns
    )
    trace.record(
        "plan_safety.approved",
        tables=plan.tables,
        unknown_column_count=unknown_column_count,
        column_checks_deferred=True,
    )
    return {}


def generate_sql_node(
    state: AgentState,
    runtime: Runtime[AgentContext] | None = None,
) -> dict:
    """Call the model with a forced emit_sql tool to draft aggregate SQL."""
    cfg = get_config()
    budget = runtime.context.budget if runtime is not None else budget_from_env()
    trace = _open_trace(state, cfg)
    question = state.get("question", "")
    raw_plan = state.get("query_plan")
    repair_hint = state.get("repair_hint")

    if not raw_plan:
        trace.record("generate_sql.no_plan")
        return {"answer": "I stopped because no query plan was available for SQL generation."}

    plan = QueryPlan.model_validate(raw_plan)

    try:
        result = generate_sql(
            question,
            plan.schema_snapshot,
            cfg,
            budget,
            repair_hint=repair_hint,
        )
    except Exception as exc:
        trace.record("generate_sql.error", error=str(exc))
        return {"answer": "I encountered an internal error during SQL generation."}

    trace.record(
        "generate_sql.completed",
        refused=result.refused,
        has_sql=result.sql is not None,
    )

    if result.refused:
        return {
            "answer": (
                f"SQL generation was refused: "
                f"{result.reason or 'the request cannot be answered safely'}."
            )
        }

    if result.sql is None:
        return {"answer": "The request is ambiguous or not supported by the available schema."}

    return {"generated_sql": result.sql}


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
    sql = state.get("generated_sql")
    raw_plan = state.get("query_plan")
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
        plan = QueryPlan.model_validate(raw_plan)
        snapshot = plan.schema_snapshot
        declared_tables = plan.tables

    result = validate_sql(sql, declared_tables, snapshot)
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
    sql = state.get("generated_sql") or ""
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

    trace.record("execution_not_configured", sql_length=len(sql))

    return {
        "execution_status": "not_configured",
        "answer": answer,
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

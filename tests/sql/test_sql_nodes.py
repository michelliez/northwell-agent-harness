from __future__ import annotations

from agent_host.budget import ExecutionBudget
from agent_host.nodes import sql_nodes
from sql.models import (
    ApprovedQueryPlan,
    PermissionScope,
    PlannedAggregation,
    QueryPlanAST,
    SchemaColumn,
    SchemaSnapshot,
    SchemaTable,
    SqlGenerationResult,
    SqlValidationResult,
    SqlViolation,
)
from sql.planning import validate_query_plan


def _approved_count_plan() -> ApprovedQueryPlan:
    snapshot = SchemaSnapshot(
        tables=[
            SchemaTable(
                name="A0H_MAP",
                columns=[SchemaColumn(name="LINE", safety="unknown")],
                source_chunk_ids=["a0h-map-line"],
            )
        ],
        derived_from_chunks=["a0h-map-line"],
    )
    scope = PermissionScope(schema_snapshot=snapshot)
    plan = QueryPlanAST(
        objective="Count all rows",
        target_metric="row_count",
        tables=["A0H_MAP"],
        aggregations=[PlannedAggregation(function="COUNT", alias="row_count")],
        expected_output=["row_count"],
        citations=["a0h-map-line"],
    )
    result = validate_query_plan(
        plan.objective,
        plan,
        scope,
        {"a0h-map-line"},
    )
    assert result.approved_plan is not None
    return result.approved_plan


def test_plan_safety_approves_count_star_without_referencing_unknown_column() -> None:
    snapshot = SchemaSnapshot(
        tables=[
            SchemaTable(
                name="a0h_map",
                columns=[SchemaColumn(name="LINE", safety="unknown")],
                source_chunk_ids=["a0h-map-line"],
            )
        ],
        derived_from_chunks=["a0h-map-line"],
    )
    scope = PermissionScope(schema_snapshot=snapshot)
    plan = QueryPlanAST(
        objective="Count all rows",
        target_metric="row_count",
        tables=["a0h_map"],
        aggregations=[PlannedAggregation(function="COUNT", alias="row_count")],
        expected_output=["row_count"],
        citations=["a0h-map-line"],
    )

    result = sql_nodes.plan_safety_node(
        {
            "question": "Count all rows",
            "query_plan": plan.model_dump(),
            "permission_scope": scope.model_dump(),
            "retrieved_chunks": [{"chunk_id": "a0h-map-line"}],
        }  # type: ignore[arg-type]
    )

    assert result["approved_plan"] is not None
    assert result["plan_validation"]["allowed"] is True


def test_write_sql_node_uses_deterministic_compiler() -> None:
    approved = _approved_count_plan()

    result = sql_nodes.write_sql_node(
        {"approved_plan": approved.model_dump()}  # type: ignore[arg-type]
    )

    assert result["compiled_query"]["source"] == "deterministic"
    assert "COUNT(*) AS row_count" in result["candidate_sql"]
    assert result["query_parameters"] == []


def test_deterministic_write_then_static_validation_succeeds() -> None:
    approved = _approved_count_plan()
    written = sql_nodes.write_sql_node(
        {"approved_plan": approved.model_dump()}  # type: ignore[arg-type]
    )

    validated = sql_nodes.validate_sql_node(
        {
            "approved_plan": approved.model_dump(),
            "candidate_sql": written["candidate_sql"],
            "repair_count": 0,
        }  # type: ignore[arg-type]
    )

    assert validated["validation_result"]["allowed"] is True


def test_fix_sql_node_records_repair_attempt(monkeypatch) -> None:
    approved = _approved_count_plan()
    validation = SqlValidationResult(
        allowed=False,
        reason="plan_sql_mismatch",
        violations=[SqlViolation(code="plan_sql_mismatch", message="Mismatch")],
        is_repairable=True,
        repair_hint="Restore the approved aggregation.",
    )
    monkeypatch.setattr(
        sql_nodes,
        "generate_sql",
        lambda *_args, **_kwargs: SqlGenerationResult(
            sql="SELECT COUNT(*) AS row_count FROM A0H_MAP",
            tables=["A0H_MAP"],
        ),
    )

    result = sql_nodes.fix_sql_node(
        {
            "approved_plan": approved.model_dump(),
            "candidate_sql": "SELECT COUNT(*) FROM A0H_MAP",
            "validation_result": validation.model_dump(),
            "repair_count": 1,
            "repair_history": [],
        }  # type: ignore[arg-type]
    )

    assert result["compiled_query"]["source"] == "claude_repair"
    assert result["repair_history"][0]["attempt"] == 1
    assert result["candidate_sql"].endswith("FROM A0H_MAP")


def test_fix_sql_node_stops_repeated_candidate(monkeypatch) -> None:
    approved = _approved_count_plan()
    candidate = "SELECT COUNT(*) FROM A0H_MAP"
    validation = SqlValidationResult(
        allowed=False,
        reason="plan_sql_mismatch",
        violations=[SqlViolation(code="plan_sql_mismatch", message="Mismatch")],
        is_repairable=True,
        repair_hint="Repair it.",
    )
    monkeypatch.setattr(
        sql_nodes,
        "generate_sql",
        lambda *_args, **_kwargs: SqlGenerationResult(
            sql=candidate,
            tables=["A0H_MAP"],
        ),
    )

    result = sql_nodes.fix_sql_node(
        {
            "approved_plan": approved.model_dump(),
            "candidate_sql": candidate,
            "validation_result": validation.model_dump(),
            "repair_count": 1,
            "repair_history": [],
        }  # type: ignore[arg-type]
    )

    assert "repeated" in result["answer"]


def test_last_failed_repair_returns_an_answer(
    monkeypatch,
) -> None:
    validation = SqlValidationResult(
        allowed=False,
        reason="declared_table_mismatch",
        violations=[
            SqlViolation(
                code="declared_table_mismatch",
                message="Declared tables do not match referenced tables.",
            )
        ],
        is_repairable=True,
        repair_hint="Declare the referenced table exactly.",
    )
    monkeypatch.setattr(sql_nodes, "validate_sql", lambda *_args: validation)
    monkeypatch.setattr(
        sql_nodes,
        "budget_from_env",
        lambda: ExecutionBudget(max_sql_repairs=3),
    )

    result = sql_nodes.validate_sql_node(
        {
            "generated_sql": "SELECT COUNT(*) FROM A0H_MAP",
            "repair_count": 2,
        }  # type: ignore[arg-type]
    )

    assert result["answer"]
    assert result["validation_result"]["reason"] == "declared_table_mismatch"

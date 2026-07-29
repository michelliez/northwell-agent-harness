from __future__ import annotations

from agent_host.budget import ExecutionBudget
from agent_host.nodes import sql_nodes
from sql.models import (
    QueryPlan,
    SchemaColumn,
    SchemaSnapshot,
    SchemaTable,
    SqlValidationResult,
    SqlViolation,
)


def test_plan_safety_defers_unknown_column_check_until_sql_validation() -> None:
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
    plan = QueryPlan(
        tables=["a0h_map"],
        purpose="Count all rows",
        schema_snapshot=snapshot,
    )

    result = sql_nodes.plan_safety_node({"query_plan": plan.model_dump()})  # type: ignore[arg-type]

    assert result == {}


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

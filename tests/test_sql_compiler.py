from __future__ import annotations

from sql.compiler import plan_to_bigquery_sql
from sql.models import (
    PermissionScope,
    QueryPlanAST,
    SchemaColumn,
    SchemaSnapshot,
    SchemaTable,
)
from sql.planning import validate_query_plan
from sql.validation import validate_sql

QUESTION = "Count A0H_MAP rows"
CITATION = "chunk-a0h"


def _scope() -> PermissionScope:
    return PermissionScope(
        schema_snapshot=SchemaSnapshot(
            tables=[
                SchemaTable(
                    name="A0H_MAP",
                    columns=[
                        SchemaColumn(
                            name="INTERNAL_ID",
                            data_type="NUMERIC",
                            safety="identifier",
                        ),
                        SchemaColumn(
                            name="STATUS_CODE",
                            data_type="STRING",
                            safety="safe_aggregate",
                        ),
                    ],
                    source_chunk_ids=[CITATION],
                )
            ],
            derived_from_chunks=[CITATION],
        )
    )


def _approved(**updates):
    payload = {
        "objective": QUESTION,
        "target_metric": "row_count",
        "tables": ["A0H_MAP"],
        "aggregations": [{"function": "COUNT", "alias": "row_count"}],
        "expected_output": ["row_count"],
        "citations": [CITATION],
    }
    payload.update(updates)
    plan = QueryPlanAST.model_validate(payload)
    result = validate_query_plan(QUESTION, plan, _scope(), {CITATION})
    assert result.approved_plan is not None, result.violations
    return result.approved_plan


def test_compiles_count_star_without_model_or_literals() -> None:
    approved = _approved()

    compiled = plan_to_bigquery_sql(approved)

    assert compiled.source == "deterministic"
    assert compiled.parameters == []
    assert "COUNT(*) AS row_count" in compiled.sql
    assert "FROM A0H_MAP" in compiled.sql
    assert validate_sql(
        compiled.sql,
        approved.plan.tables,
        approved.permission_scope.schema_snapshot,
        approved,
    ).allowed


def test_compiles_distinct_identifier_count() -> None:
    approved = _approved(
        aggregations=[
            {
                "function": "COUNT_DISTINCT",
                "column": {"table": "A0H_MAP", "column": "INTERNAL_ID"},
                "alias": "record_count",
            }
        ],
        expected_output=["record_count"],
    )

    compiled = plan_to_bigquery_sql(approved)

    assert "COUNT(DISTINCT A0H_MAP.INTERNAL_ID) AS record_count" in compiled.sql
    assert validate_sql(
        compiled.sql,
        approved.plan.tables,
        approved.permission_scope.schema_snapshot,
        approved,
    ).allowed


def test_compiles_grouping_and_named_parameter_without_literal_value() -> None:
    approved = _approved(
        groupings=[{"table": "A0H_MAP", "column": "STATUS_CODE"}],
        filters=[
            {
                "column": {"table": "A0H_MAP", "column": "STATUS_CODE"},
                "operator": "=",
                "parameter_names": ["status"],
            }
        ],
        parameters=[{"name": "status", "type": "STRING", "value": "ACTIVE"}],
        expected_output=["STATUS_CODE", "row_count"],
    )

    compiled = plan_to_bigquery_sql(approved)

    assert "A0H_MAP.STATUS_CODE = @status" in compiled.sql
    assert "ACTIVE" not in compiled.sql
    assert "GROUP BY\n  A0H_MAP.STATUS_CODE" in compiled.sql
    assert compiled.parameters[0].value == "ACTIVE"
    assert validate_sql(
        compiled.sql,
        approved.plan.tables,
        approved.permission_scope.schema_snapshot,
        approved,
    ).allowed


def test_plan_aware_validation_rejects_sql_that_broadens_plan() -> None:
    approved = _approved()

    result = validate_sql(
        "SELECT COUNT(*) AS row_count FROM A0H_MAP "
        "WHERE A0H_MAP.STATUS_CODE = @status",
        approved.plan.tables,
        approved.permission_scope.schema_snapshot,
        approved,
    )

    assert result.allowed is False
    assert result.reason == "plan_sql_mismatch"

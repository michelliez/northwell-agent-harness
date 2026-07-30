from __future__ import annotations

from sql.models import (
    CatalogRef,
    PermissionScope,
    PlannedAggregation,
    QueryPlanAST,
    SchemaColumn,
    SchemaSnapshot,
    SchemaTable,
)
from sql.planning import validate_query_plan


def _scope() -> PermissionScope:
    return PermissionScope(
        schema_snapshot=SchemaSnapshot(
            tables=[
                SchemaTable(
                    name="A0H_MAP",
                    columns=[
                        SchemaColumn(name="INTERNAL_ID", safety="identifier"),
                        SchemaColumn(name="LINE", safety="unknown"),
                        SchemaColumn(name="STATUS_CODE", safety="safe_aggregate"),
                    ],
                    source_chunk_ids=["chunk-a0h"],
                )
            ],
            derived_from_chunks=["chunk-a0h"],
        )
    )


def _count_plan(**updates) -> QueryPlanAST:
    payload = {
        "objective": "Count all A0H_MAP rows",
        "target_metric": "row_count",
        "tables": ["A0H_MAP"],
        "columns": [],
        "filters": [],
        "joins": [],
        "aggregations": [{"function": "COUNT", "column": None, "alias": "row_count"}],
        "time_constraints": [],
        "groupings": [],
        "expected_output": ["row_count"],
        "citations": ["chunk-a0h"],
        "requires_row_level_access": False,
    }
    payload.update(updates)
    return QueryPlanAST.model_validate(payload)


def test_count_star_plan_is_approved_and_bound_to_scope() -> None:
    result = validate_query_plan(
        "Count all A0H_MAP rows",
        _count_plan(),
        _scope(),
        {"chunk-a0h"},
    )

    assert result.allowed is True
    assert result.approved_plan is not None
    assert len(result.approved_plan.plan_hash) == 64
    assert len(result.approved_plan.scope_hash) == 64


def test_plan_cannot_change_objective_or_invent_catalog_references() -> None:
    result = validate_query_plan(
        "Count all A0H_MAP rows",
        _count_plan(
            objective="Return all patient records",
            columns=[CatalogRef(table="A0H_MAP", column="DOES_NOT_EXIST")],
        ),
        _scope(),
        {"chunk-a0h"},
    )

    codes = {violation.code for violation in result.violations}
    assert result.allowed is False
    assert "objective_mismatch" in codes
    assert "column_out_of_scope" in codes


def test_identifier_may_be_counted_but_not_projected() -> None:
    identifier = CatalogRef(table="A0H_MAP", column="INTERNAL_ID")
    counted = _count_plan(
        aggregations=[
            PlannedAggregation(
                function="COUNT_DISTINCT",
                column=identifier,
                alias="record_count",
            )
        ],
        expected_output=["record_count"],
    )
    projected = _count_plan(columns=[identifier])

    counted_result = validate_query_plan(
        counted.objective,
        counted,
        _scope(),
        {"chunk-a0h"},
    )
    projected_result = validate_query_plan(
        projected.objective,
        projected,
        _scope(),
        {"chunk-a0h"},
    )

    assert counted_result.allowed is True
    assert projected_result.allowed is False
    assert {item.code for item in projected_result.violations} >= {"unsafe_projection"}


def test_unretrieved_citation_is_rejected() -> None:
    result = validate_query_plan(
        "Count all A0H_MAP rows",
        _count_plan(citations=["invented"]),
        _scope(),
        {"chunk-a0h"},
    )

    assert result.allowed is False
    assert {item.code for item in result.violations} == {"citation_out_of_scope"}


def test_filter_parameters_must_be_declared_once_and_used() -> None:
    plan = _count_plan(
        filters=[
            {
                "column": {"table": "A0H_MAP", "column": "STATUS_CODE"},
                "operator": "=",
                "parameter_names": ["status"],
            }
        ],
        parameters=[
            {"name": "unused", "type": "STRING", "value": "x"},
        ],
    )

    result = validate_query_plan(plan.objective, plan, _scope(), {"chunk-a0h"})

    assert result.allowed is False
    assert {item.code for item in result.violations} >= {
        "undeclared_parameter",
        "unused_parameter",
    }

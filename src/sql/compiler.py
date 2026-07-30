"""Deterministic ApprovedQueryPlan to BigQuery SQL compilation."""

from __future__ import annotations

from typing import cast

from sqlglot import exp

from sql.models import (
    ApprovedQueryPlan,
    CatalogRef,
    CompiledQuery,
    PlannedAggregation,
    PlannedFilter,
)

COMPILER_VERSION = "approved-plan-bigquery-v1"


class UnsupportedPlanError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def plan_to_bigquery_sql(approved: ApprovedQueryPlan) -> CompiledQuery:
    """Compile only validated identifiers and named parameter placeholders."""
    plan = approved.plan
    if not plan.tables:
        raise UnsupportedPlanError("missing_table")

    projections: list[exp.Expression] = [_column(reference) for reference in plan.groupings]
    projections.extend(_aggregation(item) for item in plan.aggregations)
    query = exp.select(*projections).from_(exp.to_table(plan.tables[0]))

    joined_tables = {plan.tables[0].casefold()}
    for join in plan.joins:
        right_table = join.right.table
        if right_table.casefold() in joined_tables:
            right_table = join.left.table
        if right_table.casefold() in joined_tables:
            raise UnsupportedPlanError("ambiguous_join_order")
        query = query.join(
            exp.to_table(right_table),
            on=exp.EQ(this=_column(join.left), expression=_column(join.right)),
            join_type=join.join_type,
        )
        joined_tables.add(right_table.casefold())

    if joined_tables != {table.casefold() for table in plan.tables}:
        raise UnsupportedPlanError("disconnected_table")

    predicates = [_predicate(item) for item in [*plan.filters, *plan.time_constraints]]
    if predicates:
        combined = predicates[0]
        for predicate in predicates[1:]:
            combined = exp.and_(combined, predicate)
        query = query.where(combined)

    if plan.groupings:
        query = query.group_by(*(_column(reference) for reference in plan.groupings))

    return CompiledQuery(
        sql=query.sql(dialect="bigquery", pretty=True),
        parameters=plan.parameters,
        plan_hash=approved.plan_hash,
        compiler_version=COMPILER_VERSION,
        source="deterministic",
    )


def _column(reference: CatalogRef) -> exp.Column:
    return exp.column(reference.column, table=reference.table)


def _aggregation(item: PlannedAggregation) -> exp.Expression:
    column = _column(item.column) if item.column is not None else exp.Star()
    if item.function == "COUNT":
        result: exp.Expression = exp.Count(this=column)
    elif item.function == "COUNT_DISTINCT":
        result = exp.Count(this=exp.Distinct(expressions=[column]))
    else:
        function_type = {
            "SUM": exp.Sum,
            "AVG": exp.Avg,
            "MIN": exp.Min,
            "MAX": exp.Max,
        }[item.function]
        result = function_type(this=column)
    return cast(exp.Expression, result.as_(item.alias))


def _predicate(item: PlannedFilter) -> exp.Expression:
    column = _column(item.column)
    parameters = [exp.Parameter(this=exp.Var(this=name)) for name in item.parameter_names]
    if item.operator == "BETWEEN":
        return exp.Between(
            this=column,
            low=parameters[0],
            high=parameters[1],
        )
    if item.operator == "IN":
        raise UnsupportedPlanError("array_parameter_in_not_supported")
    expression_type = {
        "=": exp.EQ,
        "!=": exp.NEQ,
        "<": exp.LT,
        "<=": exp.LTE,
        ">": exp.GT,
        ">=": exp.GTE,
    }[item.operator]
    return cast(exp.Expression, expression_type(this=column, expression=parameters[0]))

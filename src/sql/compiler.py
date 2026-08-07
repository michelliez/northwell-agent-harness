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
    PlannedTimeBucket,
    SchemaSnapshot,
)

COMPILER_VERSION = "approved-plan-bigquery-v2"

# BigQuery pairs each temporal type with its own truncation function.
_TRUNC_BY_TYPE: dict[str, type[exp.Func]] = {
    "DATE": exp.DateTrunc,
    "DATETIME": exp.DatetimeTrunc,
    "TIMESTAMP": exp.TimestampTrunc,
}


class UnsupportedPlanError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def plan_to_bigquery_sql(approved: ApprovedQueryPlan) -> CompiledQuery:
    """Compile only validated identifiers and named parameter placeholders."""
    plan = approved.plan
    if not plan.tables:
        raise UnsupportedPlanError("missing_table")

    snapshot = approved.permission_scope.schema_snapshot
    bucket_expressions = [_time_bucket(item, snapshot) for item in plan.time_buckets]
    projections: list[exp.Expression] = [
        cast(exp.Expression, expression.copy().as_(item.alias))
        for item, expression in zip(plan.time_buckets, bucket_expressions, strict=True)
    ]
    projections.extend(_column(reference) for reference in plan.groupings)
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

    if bucket_expressions or plan.groupings:
        query = query.group_by(
            *(expression.copy() for expression in bucket_expressions),
            *(_column(reference) for reference in plan.groupings),
        )

    if plan.order_by is not None:
        query = query.order_by(
            exp.Ordered(
                this=exp.column(plan.order_by.alias),
                desc=plan.order_by.direction == "DESC",
            )
        )
    if plan.limit is not None:
        query = query.limit(plan.limit)

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


def _time_bucket(item: PlannedTimeBucket, snapshot: SchemaSnapshot) -> exp.Expression:
    table = snapshot.tables_by_name.get(item.column.table.lower())
    column = None
    if table is not None:
        column = next(
            (col for col in table.columns if col.name.casefold() == item.column.column.casefold()),
            None,
        )
    data_type = (column.data_type or "").upper().split("(", 1)[0].strip() if column else ""
    trunc_type = _TRUNC_BY_TYPE.get(data_type)
    if trunc_type is None:
        # Plan authorization already rejects this; the compiler still refuses
        # rather than guessing a truncation function for an unknown type.
        raise UnsupportedPlanError("time_bucket_untruncatable_type")
    return cast(
        exp.Expression, trunc_type(this=_column(item.column), unit=exp.var(item.granularity))
    )


def _predicate(item: PlannedFilter) -> exp.Expression:
    column = _column(item.column)
    parameters = [exp.Parameter(this=exp.Var(this=name)) for name in item.parameter_names]
    if item.operator == "BETWEEN":
        return exp.Between(
            this=column,
            low=parameters[0],
            high=parameters[1],
        )
    expression_type = {
        "=": exp.EQ,
        "!=": exp.NEQ,
        "<": exp.LT,
        "<=": exp.LTE,
        ">": exp.GT,
        ">=": exp.GTE,
    }[item.operator]
    return cast(exp.Expression, expression_type(this=column, expression=parameters[0]))

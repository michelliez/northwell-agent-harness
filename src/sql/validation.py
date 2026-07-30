"""Deterministic SQL safety validation against a SchemaSnapshot.

Seven ordered gates; the first failure returns a blocked result. No SQL is
returned for a blocked result. Does not execute SQL, authorize access, or
estimate cost. disclosure_status remains not_evaluated.
"""

from __future__ import annotations

from typing import Annotated, Any

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify

from sql.compiler import plan_to_bigquery_sql
from sql.models import (
    ApprovedQueryPlan,
    SchemaSnapshot,
    SqlValidationResult,
    SqlViolation,
)

SQL_DIALECT = "bigquery"
MAX_DECLARED_TABLES = 50


class _ValidateSqlArgs(BaseModel):
    """Input validation for validate_sql. Raises ValidationError on invalid input."""

    sql: str = Field(min_length=1, max_length=10_000)
    tables: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(
        default_factory=list,
        max_length=MAX_DECLARED_TABLES,
    )


PROHIBITED_EXPRESSION_TYPES = tuple(
    expression_type
    for name in (
        "Alter",
        "Command",
        "Commit",
        "Copy",
        "Create",
        "Declare",
        "Delete",
        "Drop",
        "Execute",
        "Export",
        "Grant",
        "Insert",
        "Merge",
        "Rollback",
        "Transaction",
        "TruncateTable",
        "Update",
        "Use",
    )
    if (expression_type := getattr(exp, name, None)) is not None
)

PROHIBITED_FUNCTIONS = {
    "CURRENT_ROLE",
    "CURRENT_USER",
    "ERROR",
    "EXTERNAL_QUERY",
    "SESSION_USER",
}

# Violation codes where repair (regeneration) is worth attempting.
REPAIRABLE_CODES = {
    "ungrouped_projection",
    "non_aggregate_sql",
    "unknown_or_ambiguous_column",
    "declared_table_mismatch",
    "unknown_safety_column",
}


def validate_sql(
    sql: str,
    tables: list[str],
    snapshot: SchemaSnapshot | None = None,
    approved_plan: ApprovedQueryPlan | None = None,
) -> SqlValidationResult:
    """Apply deterministic safety gates to a candidate BigQuery query.

    Args:
        sql: Candidate BigQuery SQL (single statement, non-empty).
        tables: Physical table names declared by the generator (advisory until
            they exactly match the AST-derived tables).
        snapshot: Evidence-backed schema used for table/column resolution.
            Defaults to an empty SchemaSnapshot (all tables will be unknown).

    Returns:
        SqlValidationResult. allowed=True includes normalized SQL; allowed=False
        includes at least one violation and no SQL.

    Raises:
        pydantic.ValidationError: If sql or tables fail input validation.
    """
    # Input validation — raises ValidationError on invalid input
    _ValidateSqlArgs(sql=sql.strip(), tables=tables)

    if snapshot is None:
        snapshot = SchemaSnapshot()

    # BigQuery identifiers are case-insensitive. Normalize model-declared table
    # names before comparing them with SQLGlot's normalized AST table names.
    declared_tables = sorted({table.casefold() for table in tables})

    if len(declared_tables) != len(tables):
        return _blocked(
            "invalid_table_declaration",
            declared_tables,
            evidence={"reason": "duplicate table declarations"},
        )

    try:
        statements = [
            stmt for stmt in sqlglot.parse(sql.strip(), read=SQL_DIALECT) if stmt is not None
        ]
    except ParseError as exc:
        return _blocked(
            "invalid_sql_syntax",
            declared_tables,
            evidence={"errors": exc.errors},
        )

    if len(statements) != 1:
        return _blocked(
            "multiple_statement_sql",
            declared_tables,
            evidence={"statement_count": len(statements)},
        )

    expression = statements[0]
    statement_type = type(expression).__name__
    if not isinstance(expression, exp.Query):
        return _blocked(
            "non_read_only_sql",
            declared_tables,
            statement_type=statement_type,
            evidence={"statement_type": statement_type},
        )

    empty_select = next(
        (s for s in expression.find_all(exp.Select) if not s.expressions),
        None,
    )
    if empty_select is not None:
        return _blocked(
            "invalid_sql_syntax",
            declared_tables,
            statement_type=statement_type,
            evidence={"error": "SELECT must contain at least one projection"},
        )

    prohibited = next(expression.find_all(*PROHIBITED_EXPRESSION_TYPES), None)
    if prohibited is not None:
        return _blocked(
            "unsafe_sql_operation",
            declared_tables,
            statement_type=statement_type,
            evidence={"expression_type": type(prohibited).__name__},
        )

    prohibited_function = _find_prohibited_function(expression)
    if prohibited_function is not None:
        return _blocked(
            "unsafe_sql_function",
            declared_tables,
            statement_type=statement_type,
            evidence={"function": prohibited_function},
        )

    if _uses_system_variable(expression):
        return _blocked(
            "unsafe_system_variable",
            declared_tables,
            statement_type=statement_type,
        )

    recursive_with = next(
        (w for w in expression.find_all(exp.With) if w.args.get("recursive")),
        None,
    )
    if recursive_with is not None:
        return _blocked("recursive_cte_not_allowed", declared_tables, statement_type=statement_type)

    window = next(expression.find_all(exp.Window), None)
    if window is not None:
        return _blocked(
            "window_function_not_allowed",
            declared_tables,
            statement_type=statement_type,
            evidence={"expression": window.sql(dialect=SQL_DIALECT)},
        )

    unnest = next(expression.find_all(exp.Unnest), None)
    if unnest is not None:
        return _blocked(
            "unapproved_table_source",
            declared_tables,
            statement_type=statement_type,
            evidence={"source_type": "Unnest"},
        )

    unsafe_join = _find_unsafe_join(expression)
    if unsafe_join is not None:
        return _blocked(
            "unsafe_join",
            declared_tables,
            statement_type=statement_type,
            evidence={"expression": unsafe_join.sql(dialect=SQL_DIALECT)},
        )

    unsafe_star = _find_unsafe_star(expression)
    if unsafe_star is not None:
        return _blocked(
            "select_star_sql",
            declared_tables,
            statement_type=statement_type,
            evidence={"expression": unsafe_star.sql(dialect=SQL_DIALECT)},
        )

    # Gate 4: table resolution against SchemaSnapshot
    cte_names = {cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE)}
    physical_tables = [
        table for table in expression.find_all(exp.Table) if table.name.lower() not in cte_names
    ]
    referenced_tables = sorted({table.name.lower() for table in physical_tables})

    if not referenced_tables:
        return _blocked("missing_approved_table", declared_tables, statement_type=statement_type)

    wildcard_table = next(
        (table for table in physical_tables if "*" in table.name),
        None,
    )
    if wildcard_table is not None:
        return _blocked(
            "wildcard_table_scan",
            declared_tables,
            referenced_tables=referenced_tables,
            statement_type=statement_type,
            evidence={"table": wildcard_table.sql(dialect=SQL_DIALECT)},
        )

    qualified_table = next(
        (table for table in physical_tables if table.db or table.catalog),
        None,
    )
    if qualified_table is not None:
        return _blocked(
            "qualified_table_not_allowed",
            declared_tables,
            referenced_tables=referenced_tables,
            statement_type=statement_type,
            evidence={"table": qualified_table.sql(dialect=SQL_DIALECT)},
        )

    unknown_tables = sorted(set(referenced_tables) - snapshot.known_table_names)
    if unknown_tables:
        return _blocked(
            "unknown_table",
            declared_tables,
            referenced_tables=referenced_tables,
            statement_type=statement_type,
            evidence={"tables": unknown_tables},
        )

    if set(declared_tables) != set(referenced_tables):
        return _blocked(
            "declared_table_mismatch",
            declared_tables,
            referenced_tables=referenced_tables,
            statement_type=statement_type,
            evidence={
                "declared_tables": declared_tables,
                "referenced_tables": referenced_tables,
            },
            repairable=True,
        )

    # Gate 5: column qualification against SchemaSnapshot
    try:
        qualified = qualify(
            expression.copy(),
            dialect=SQL_DIALECT,
            schema=_sqlglot_schema(snapshot),
            expand_stars=False,
            quote_identifiers=False,
            validate_qualify_columns=True,
        )
    except OptimizeError as exc:
        return _blocked(
            "unknown_or_ambiguous_column",
            declared_tables,
            referenced_tables=referenced_tables,
            statement_type=statement_type,
            evidence={"error": str(exc)},
            repairable=True,
        )

    referenced_columns = sorted(
        {_column_reference(col) for col in qualified.find_all(exp.Column) if col.name != "*"}
    )

    table_aliases = _table_aliases(qualified, snapshot)

    # Gate 6: aggregate safety
    output_selects = _output_selects(qualified)
    if not output_selects or any(not _select_has_direct_aggregate(s) for s in output_selects):
        return _blocked(
            "non_aggregate_sql",
            declared_tables,
            referenced_tables=referenced_tables,
            referenced_columns=referenced_columns,
            statement_type=statement_type,
            repairable=True,
        )

    ungrouped_column = next(
        (col for s in output_selects if (col := _find_ungrouped_projection_column(s)) is not None),
        None,
    )
    if ungrouped_column is not None:
        return _blocked(
            "ungrouped_projection",
            declared_tables,
            referenced_tables=referenced_tables,
            referenced_columns=referenced_columns,
            statement_type=statement_type,
            evidence={"column": _column_reference(ungrouped_column)},
            repairable=True,
        )

    # Reject unknown-safety columns (cannot be evaluated without evidence)
    unknown_safety_col = next(
        (
            col
            for col in qualified.find_all(exp.Column)
            if _column_safety_label(col, table_aliases, snapshot) == "unknown"
        ),
        None,
    )
    if unknown_safety_col is not None:
        return _blocked(
            "unknown_safety_column",
            declared_tables,
            referenced_tables=referenced_tables,
            referenced_columns=referenced_columns,
            statement_type=statement_type,
            evidence={"column": _column_reference(unknown_safety_col)},
            repairable=False,
        )

    unsafe_sensitive = _find_sensitive_column(qualified, table_aliases, snapshot)
    if unsafe_sensitive is not None:
        col, safety_label = unsafe_sensitive
        return _blocked(
            "sensitive_column_reference",
            declared_tables,
            referenced_tables=referenced_tables,
            referenced_columns=referenced_columns,
            statement_type=statement_type,
            evidence={"column": _column_reference(col), "safety_label": safety_label},
        )

    unsafe_identifier = next(
        (
            col
            for col in qualified.find_all(exp.Column)
            if _column_safety_label(col, table_aliases, snapshot) == "identifier"
            and not _identifier_use_is_allowed(col, table_aliases, snapshot)
        ),
        None,
    )
    if unsafe_identifier is not None:
        return _blocked(
            "identifier_column_disallowed_context",
            declared_tables,
            referenced_tables=referenced_tables,
            referenced_columns=referenced_columns,
            statement_type=statement_type,
            evidence={"column": _column_reference(unsafe_identifier)},
        )

    if approved_plan is not None:
        expected_sql = plan_to_bigquery_sql(approved_plan).sql
        expected_expression = sqlglot.parse_one(expected_sql, read=SQL_DIALECT)
        candidate_canonical = expression.sql(dialect=SQL_DIALECT, normalize=True)
        expected_canonical = expected_expression.sql(
            dialect=SQL_DIALECT,
            normalize=True,
        )
        if candidate_canonical != expected_canonical:
            return _blocked(
                "plan_sql_mismatch",
                declared_tables,
                referenced_tables=referenced_tables,
                referenced_columns=referenced_columns,
                statement_type=statement_type,
                evidence={
                    "candidate": candidate_canonical,
                    "expected": expected_canonical,
                },
                repairable=True,
            )

    # Gate 7: normalize
    normalized_sql = qualified.sql(dialect=SQL_DIALECT, pretty=True)
    return SqlValidationResult(
        allowed=True,
        disclosure_status="not_evaluated",
        requires_authorized_execution=True,
        normalized_sql=normalized_sql,
        tables=referenced_tables,
        referenced_tables=referenced_tables,
        referenced_columns=referenced_columns,
        declared_tables=declared_tables,
        statement_type=statement_type,
        notes=["SQL passed deterministic SQLGlot validation against schema evidence."],
    )


# ── helpers ───────────────────────────────────────────────────────────────────


def _sqlglot_schema(snapshot: SchemaSnapshot) -> dict[str, object]:
    return {
        table.name: {col.name: (col.data_type or "STRING").upper() for col in table.columns}
        for table in snapshot.tables
    }


def _table_aliases(
    expression: exp.Expression | exp.Query,
    snapshot: SchemaSnapshot,
) -> dict[str, str]:
    known = snapshot.known_table_names
    return {
        (table.alias_or_name or table.name).lower(): table.name.lower()
        for table in expression.find_all(exp.Table)
        if table.name.lower() in known
    }


def _column_safety_label(
    column: exp.Column,
    table_aliases: dict[str, str],
    snapshot: SchemaSnapshot,
) -> str | None:
    table_name = table_aliases.get(column.table.lower(), column.table.lower())
    table = snapshot.tables_by_name.get(table_name)
    if table is None:
        return None
    for col in table.columns:
        if col.name.casefold() == column.name.casefold():
            return col.safety
    return None


def _find_sensitive_column(
    expression: exp.Expression | exp.Query,
    table_aliases: dict[str, str],
    snapshot: SchemaSnapshot,
) -> tuple[exp.Column, str] | None:
    for col in expression.find_all(exp.Column):
        safety = _column_safety_label(col, table_aliases, snapshot)
        if safety == "sensitive":
            return col, safety
    return None


def _identifier_use_is_allowed(
    column: exp.Column,
    table_aliases: dict[str, str],
    snapshot: SchemaSnapshot,
) -> bool:
    count = column.find_ancestor(exp.Count)
    if count is not None:
        count_input = count.this
        if count_input is column:
            return True
        if isinstance(count_input, exp.Distinct):
            distinct_exprs = count_input.expressions
            if len(distinct_exprs) == 1 and distinct_exprs[0] is column:
                return True

    equality = column.find_ancestor(exp.EQ)
    join = equality.find_ancestor(exp.Join) if equality is not None else None
    if equality is None or join is None:
        return False

    join_condition = join.args.get("on")
    if join_condition is None or equality not in set(join_condition.walk()):
        return False

    compared_columns = list(equality.find_all(exp.Column))
    return len(compared_columns) == 2 and all(
        _column_safety_label(item, table_aliases, snapshot) == "identifier"
        for item in compared_columns
    )


def _find_prohibited_function(expression: exp.Expression | exp.Query) -> str | None:
    for function in expression.find_all(exp.Func):
        function_name = (
            function.name if isinstance(function, exp.Anonymous) else function.sql_name()
        ).upper()
        if isinstance(function, exp.Anonymous) or function_name in PROHIBITED_FUNCTIONS:
            return function_name
    return None


def _uses_system_variable(expression: exp.Expression | exp.Query) -> bool:
    return any(
        isinstance(parameter.this, exp.Parameter)
        for parameter in expression.find_all(exp.Parameter)
    )


def _find_unsafe_join(expression: exp.Expression | exp.Query) -> exp.Join | None:
    for join in expression.find_all(exp.Join):
        if join.args.get("method") == "NATURAL" or join.args.get("kind") == "CROSS":
            return join
        using = join.args.get("using")
        if using:
            continue
        condition = join.args.get("on")
        if condition is None or not _join_condition_is_safe(condition):
            return join
    return None


def _join_condition_is_safe(condition: exp.Expression) -> bool:
    if isinstance(condition, exp.And):
        return _join_condition_is_safe(condition.this) and _join_condition_is_safe(
            condition.expression
        )
    if not isinstance(condition, exp.EQ):
        return False
    return isinstance(condition.this, exp.Column) and isinstance(condition.expression, exp.Column)


def _find_unsafe_star(expression: exp.Expression | exp.Query) -> exp.Star | None:
    for star in expression.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count):
            return star
    return None


def _output_selects(expression: exp.Expression | exp.Query) -> list[exp.Select]:
    if isinstance(expression, exp.Select):
        return [expression]
    if isinstance(expression, exp.SetOperation):
        return [
            *_output_selects(expression.this),
            *_output_selects(expression.expression),
        ]
    return []


def _select_has_direct_aggregate(select: exp.Select) -> bool:
    return any(
        aggregate.find_ancestor(exp.Select) is select for aggregate in select.find_all(exp.AggFunc)
    )


def _find_ungrouped_projection_column(select: exp.Select) -> exp.Column | None:
    group = select.args.get("group")
    group_expressions = list(group.expressions) if isinstance(group, exp.Group) else []
    grouped_columns = {
        col.sql(dialect=SQL_DIALECT)
        for expr in group_expressions
        for col in expr.find_all(exp.Column)
    }
    grouped_ordinals = {
        int(expr.this)
        for expr in group_expressions
        if isinstance(expr, exp.Literal) and not expr.is_string and str(expr.this).isdigit()
    }

    for position, projection in enumerate(select.expressions, 1):
        if position in grouped_ordinals:
            continue
        for col in projection.find_all(exp.Column):
            if col.find_ancestor(exp.Select) is not select:
                continue
            aggregate = col.find_ancestor(exp.AggFunc)
            if aggregate is not None and aggregate.find_ancestor(exp.Select) is select:
                continue
            if col.sql(dialect=SQL_DIALECT) not in grouped_columns:
                return col
    return None


def _column_reference(column: exp.Column) -> str:
    return f"{column.table}.{column.name}" if column.table else column.name


def _blocked(
    reason: str,
    declared_tables: list[str],
    *,
    referenced_tables: list[str] | None = None,
    referenced_columns: list[str] | None = None,
    statement_type: str | None = None,
    evidence: dict[str, Any] | None = None,
    repairable: bool = False,
) -> SqlValidationResult:
    repair_hint = f"Revise the SQL to address: {reason.replace('_', ' ')}" if repairable else None
    violation = SqlViolation(
        code=reason,
        message=reason.replace("_", " "),
        evidence=evidence or {},
    )
    return SqlValidationResult(
        allowed=False,
        disclosure_status="not_evaluated",
        requires_authorized_execution=True,
        reason=reason,
        normalized_sql=None,
        tables=referenced_tables or [],
        referenced_tables=referenced_tables or [],
        referenced_columns=referenced_columns or [],
        declared_tables=declared_tables,
        statement_type=statement_type,
        violations=[violation],
        notes=["Generated SQL failed deterministic SQLGlot validation."],
        is_repairable=repairable or reason in REPAIRABLE_CODES,
        repair_hint=repair_hint,
    )

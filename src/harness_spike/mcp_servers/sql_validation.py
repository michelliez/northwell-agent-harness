from __future__ import annotations

from typing import Annotated, Any, Literal

import sqlglot
from fastmcp import FastMCP
from pydantic import BaseModel, Field, model_validator
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify

from harness_spike.mcp_servers.auth import build_service_auth
from harness_spike.mcp_servers.data_catalog import TABLES

mcp = FastMCP("sql_validation", auth=build_service_auth("sql_validation"))

SQL_DIALECT = "bigquery"
VALIDATOR_VERSION = "sqlglot_ast_v2"


class ValidateSqlArgs(BaseModel):
    sql: str = Field(min_length=1, max_length=10_000)
    tables: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(
        default_factory=list,
        max_length=len(TABLES),
    )


class SqlViolation(BaseModel):
    code: str
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class SqlValidationResult(BaseModel):
    allowed: bool
    disclosure_status: Literal["not_evaluated", "approved", "suppressed", "denied"] = (
        "not_evaluated"
    )
    requires_authorized_execution: bool = True
    reason: str | None = None
    normalized_sql: str | None = None
    tables: list[str] = Field(default_factory=list)
    referenced_tables: list[str] = Field(default_factory=list)
    referenced_columns: list[str] = Field(default_factory=list)
    declared_tables: list[str] = Field(default_factory=list)
    statement_type: str | None = None
    violations: list[SqlViolation] = Field(default_factory=list)
    validator_version: str = VALIDATOR_VERSION
    sqlglot_version: str = sqlglot.__version__
    notes: list[str] = Field(default_factory=list)
    source: str = "deterministic_sql_validation"
    is_dummy: bool = True

    @model_validator(mode="after")
    def enforce_allowed_result_contract(self) -> SqlValidationResult:
        if self.allowed and (not self.normalized_sql or self.violations):
            raise ValueError("allowed validation results require normalized SQL and no violations")
        if not self.allowed and (self.normalized_sql is not None or not self.violations):
            raise ValueError("blocked validation results require violations and no SQL")
        return self


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


@mcp.tool
def validate_sql(sql: str, tables: list[str] | None = None) -> dict[str, object]:
    """Apply the deterministic safety boundary to one generated BigQuery query.

    This tool validates read-only aggregate SQL against the validator's trusted
    catalog and column-safety metadata. The current server configuration obtains
    that metadata from the in-process ``TABLES`` fixture; a production deployment
    can provide the same validation inputs from an Epic/BigQuery catalog adapter.
    The tool does not execute SQL, estimate BigQuery cost, authorize access, or
    repair a rejected query. ``tables`` is only the generator's declaration;
    physical tables and columns are independently derived from the SQLGlot AST.

    Validation is ordered and fail closed. The first failing gate returns a blocked
    result with a stable violation code and AST-derived evidence when available:

    1. Validate the tool arguments and reject duplicate declared tables.
    2. Parse using the BigQuery dialect and require exactly one query statement.
    3. Reject unsafe AST structure: empty projections, non-query roots, prohibited
       operations or functions, system variables, recursive CTEs, windows, UNNEST,
       unsafe joins, and every form of star projection.
    4. Derive physical tables while excluding CTE aliases, then require at least one
       approved, non-wildcard catalog table and require the declaration to exactly
       match the tables observed in the AST. The current fixture policy additionally
       rejects project- or dataset-qualified names.
    5. Qualify the AST against the configured SQLGlot schema so unknown and ambiguous
       columns are rejected and referenced columns can be reported structurally.
    6. Enforce result-safety policy: every output branch must aggregate, projected
       columns must be grouped, sensitive columns are forbidden, and identifier
       columns may appear only in explicitly allowed aggregate or join contexts.
    7. Render the fully qualified AST back to normalized BigQuery SQL and return it
       with structural validation metadata. Disclosure status remains
       ``not_evaluated`` because this node does not authorize, execute, cost, or
       suppress query results. No SQL is returned for a blocked result.

    Args:
        sql: Candidate BigQuery SQL. Input is limited to one non-empty statement.
        tables: Physical table names declared by the generator. These are advisory
            until they exactly match the physical tables derived from the AST.

    Returns:
        A serialized ``SqlValidationResult``. Allowed results contain normalized SQL
        and no violations, but are not a data-disclosure approval. Blocked results
        contain at least one violation and never contain normalized SQL.
    """
    args = ValidateSqlArgs(sql=sql.strip(), tables=tables or [])
    declared_tables = sorted(set(args.tables))

    if len(declared_tables) != len(args.tables):
        return _blocked(
            "invalid_table_declaration",
            declared_tables,
            evidence={"reason": "duplicate table declarations"},
        )

    try:
        statements = [
            statement
            for statement in sqlglot.parse(args.sql, read=SQL_DIALECT)
            if statement is not None
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
        (select for select in expression.find_all(exp.Select) if not select.expressions),
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
        (
            with_expression
            for with_expression in expression.find_all(exp.With)
            if with_expression.args.get("recursive")
        ),
        None,
    )
    if recursive_with is not None:
        return _blocked(
            "recursive_cte_not_allowed",
            declared_tables,
            statement_type=statement_type,
        )

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

    cte_names = {cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE)}
    physical_tables = [
        table for table in expression.find_all(exp.Table) if table.name.lower() not in cte_names
    ]
    referenced_tables = sorted({table.name.lower() for table in physical_tables})

    if not referenced_tables:
        return _blocked(
            "missing_approved_table",
            declared_tables,
            statement_type=statement_type,
        )

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

    unknown_tables = sorted(set(referenced_tables) - set(TABLES))
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
        )

    try:
        qualified = qualify(
            expression.copy(),
            dialect=SQL_DIALECT,
            schema=_sqlglot_schema(),  # type: ignore[arg-type]
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
        )

    referenced_columns = sorted(
        {
            _column_reference(column)
            for column in qualified.find_all(exp.Column)
            if column.name != "*"
        }
    )

    table_aliases = _table_aliases(qualified)

    output_selects = _output_selects(qualified)
    if not output_selects or any(
        not _select_has_direct_aggregate(select) for select in output_selects
    ):
        return _blocked(
            "non_aggregate_sql",
            declared_tables,
            referenced_tables=referenced_tables,
            referenced_columns=referenced_columns,
            statement_type=statement_type,
        )

    ungrouped_column = next(
        (
            column
            for select in output_selects
            if (column := _find_ungrouped_projection_column(select)) is not None
        ),
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
        )

    unsafe_sensitive = _find_sensitive_column(qualified, table_aliases)
    if unsafe_sensitive is not None:
        column, safety_label = unsafe_sensitive
        return _blocked(
            "sensitive_column_reference",
            declared_tables,
            referenced_tables=referenced_tables,
            referenced_columns=referenced_columns,
            statement_type=statement_type,
            evidence={
                "column": _column_reference(column),
                "safety_label": safety_label,
            },
        )

    unsafe_identifier = next(
        (
            column
            for column in qualified.find_all(exp.Column)
            if _column_safety_label(column, table_aliases) == "identifier"
            and not _identifier_use_is_allowed(column, table_aliases)
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
        notes=["SQL passed deterministic SQLGlot mock-catalog validation."],
    ).model_dump()


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
        column.sql(dialect=SQL_DIALECT)
        for expression in group_expressions
        for column in expression.find_all(exp.Column)
    }
    grouped_ordinals = {
        int(expression.this)
        for expression in group_expressions
        if isinstance(expression, exp.Literal)
        and not expression.is_string
        and str(expression.this).isdigit()
    }

    for position, projection in enumerate(select.expressions, 1):
        if position in grouped_ordinals:
            continue
        for column in projection.find_all(exp.Column):
            if column.find_ancestor(exp.Select) is not select:
                continue
            aggregate = column.find_ancestor(exp.AggFunc)
            if aggregate is not None and aggregate.find_ancestor(exp.Select) is select:
                continue
            if column.sql(dialect=SQL_DIALECT) not in grouped_columns:
                return column
    return None


def _sqlglot_schema() -> dict[str, dict[str, str]]:
    return {
        table_name: {
            str(column["name"]): str(column["type"]).upper()
            for column in table["columns"]  # type: ignore[index]
        }
        for table_name, table in TABLES.items()
    }


def _column_reference(column: exp.Column) -> str:
    return f"{column.table}.{column.name}" if column.table else column.name


def _table_aliases(expression: exp.Expression | exp.Query) -> dict[str, str]:
    return {
        (table.alias_or_name or table.name).lower(): table.name.lower()
        for table in expression.find_all(exp.Table)
        if table.name.lower() in TABLES
    }


def _column_safety_label(column: exp.Column, table_aliases: dict[str, str]) -> str | None:
    table_name = table_aliases.get(column.table.lower(), column.table.lower())
    table = TABLES.get(table_name)
    if table is None:
        return None
    for catalog_column in table["columns"]:  # type: ignore[index]
        if catalog_column["name"] == column.name:
            return str(catalog_column["safety_label"])
    return None


def _find_sensitive_column(
    expression: exp.Expression | exp.Query,
    table_aliases: dict[str, str],
) -> tuple[exp.Column, str] | None:
    for column in expression.find_all(exp.Column):
        safety_label = _column_safety_label(column, table_aliases)
        if safety_label == "sensitive":
            return column, safety_label
    return None


def _identifier_use_is_allowed(column: exp.Column, table_aliases: dict[str, str]) -> bool:
    count = column.find_ancestor(exp.Count)
    if count is not None:
        count_input = count.this
        if count_input is column:
            return True
        if isinstance(count_input, exp.Distinct):
            distinct_expressions = count_input.expressions
            if len(distinct_expressions) == 1 and distinct_expressions[0] is column:
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
        _column_safety_label(item, table_aliases) == "identifier" for item in compared_columns
    )


def _blocked(
    reason: str,
    declared_tables: list[str],
    *,
    referenced_tables: list[str] | None = None,
    referenced_columns: list[str] | None = None,
    statement_type: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, object]:
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
    ).model_dump()


def main() -> None:
    mcp.run(transport="http", host="localhost", port=8004, path="/mcp")


if __name__ == "__main__":
    main()
